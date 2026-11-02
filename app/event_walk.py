"""The event host's walks (owner 30 Oct 2026: "พาไปยังจุดต่างๆ และคอยไปทักทาย
คนต่างๆที่โต๊ะ"), started by STAFF from the phone page -- never by a voice.

Floor 1 walks with scripts/board_walk.py (28 ต.ค.: the board's own map holds a
fix there and its MoveTo gets through the multimedia room's door). Until now
it was run by hand from the PC over adb; here the robot's own server runs the
same script, with the same Python, from its code directory (where the script
expects to be: it imports app.slamware_sdk and reads ./.env for the room
screen). The steps for one spot are written to a small route file of their
own, with floor1.json's dock -- board_walk is unchanged, and a walk here is
exactly a walk the owner has watched.

Who can stop it, every way at once (CLAUDE.md: "หยุดต้องไม่มีวันถูกตอบ busy"):
* the staff page's STOP (`stop`), and any other walk or scene it starts;
* a voice "หยุด" -- stop_moving / move_body(stop) / the desk STOP call `stop`;
* the staff page going quiet: it polls every second, and a walk with no poll
  for EVENT_WALK_HEARTBEAT_S stops (a locked phone, Wi-Fi lost in the
  multimedia room -- a walk nobody is watching is the one to stop);
* the robot face going away (the app closed, crashed or put in the
  background): the app's own launcher tethers its walks the same way;
* the E-stop on the robot, which needs none of this.
SIGTERM is board_walk's Ctrl-C (`stop_on_signals`): it cancels its MoveTo,
zeroes a creep and puts the speed back. A process still alive after
KILL_AFTER_S is killed, and the board's current action is cancelled.

`walking()` feeds the listening focus (a guest's "หยุด" from behind a walking
robot must be heard) and the wheel fence (no second driver on the wheels).
"""
from __future__ import annotations

import asyncio
import collections
import contextlib
import json
import logging
import signal
import sys
import time
import uuid
from pathlib import Path
from typing import Awaitable, Callable

from app import turnlog
from app.config import settings

logger = logging.getLogger("condo_voice.event_walk")

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "board_walk.py"
#: board_walk's path name inside the route file this module writes.
PATH_NAME = "event"
#: A stopped board_walk cancels its MoveTo and exits in well under a second;
#: one that has not after this is killed.
KILL_AFTER_S = 3.0
#: The robot face may blink (a page reload is ~1-2 s): this long without one
#: before a walk is stopped for it.
FACE_GRACE_S = 3.0
#: Lines of board_walk's own output the staff page shows (its "step 2 ...
#: done", "STOP: ..."): positions and step labels, never anything a guest said.
TAIL_LINES = 6

#: Exit codes of board_walk.main.
_EXIT = {0: "arrived", 2: "stopped", 130: "stopped"}

#: Thai reasons for the staff page.
REASONS_TH = {
    "disabled": "การเดินปิดอยู่ (EVENT_WALK_ENABLED)",
    "busy": "หุ่นกำลังเดินอยู่แล้ว",
    "other_walker": "หุ่นกำลังเดินเส้นทางอื่นอยู่",
    "no_face": "หน้าหุ่น (แอป Emma Event) ไม่ได้เชื่อมต่อ",
    "no_steps": "จุดนี้ยังไม่ได้บันทึกตำแหน่ง",
    "no_route": "ไม่พบไฟล์เส้นทางชั้น 1",
    "no_script": "ไม่พบสคริปต์เดิน (scripts/board_walk.py)",
    "launch_failed": "เริ่มสคริปต์เดินไม่ได้",
    "staff": "ทีมงานกดหยุด",
    "voice": "สั่งหยุดด้วยเสียง",
    "desk": "กดหยุดจากหน้าคุมหุ่น",
    "control_lost": "หน้าคุมบนมือถือขาดการติดต่อ",
    "face_gone": "หน้าหุ่นหลุดการเชื่อมต่อ",
    "replaced": "เริ่มการเดินใหม่",
    "shutdown": "เซิร์ฟเวอร์ปิด",
}


class Run:
    def __init__(self, spot: str, name: str):
        self.id = uuid.uuid4().hex[:10]
        self.spot = spot
        self.name = name
        self.started = time.monotonic()
        self.ended: float | None = None
        self.state = "starting"          # starting | walking | arrived | stopped | failed
        self.reason: str | None = None   # why it stopped, a REASONS_TH key or board_walk's word
        self.exit_code: int | None = None
        self.proc = None
        self.tail: collections.deque[str] = collections.deque(maxlen=TAIL_LINES)
        self.stop_requested: str | None = None
        self.tasks: list[asyncio.Task] = []

    def view(self) -> dict:
        now = time.monotonic()
        return {
            "id": self.id, "spot": self.spot, "name": self.name, "state": self.state,
            "reason": self.reason, "reason_th": REASONS_TH.get(self.reason or "", self.reason),
            "elapsed_s": round((self.ended or now) - self.started, 1),
            "tail": list(self.tail),
        }


STATE: dict = {"run": None, "heartbeat": None, "face_lost_at": None}


def reset() -> None:
    STATE.update(run=None, heartbeat=None, face_lost_at=None)


def walking() -> bool:
    run = STATE["run"]
    return run is not None and run.state in ("starting", "walking")


def current() -> Run | None:
    return STATE["run"]


def heartbeat() -> None:
    """The staff page is still watching (every poll of /event/state)."""
    STATE["heartbeat"] = time.monotonic()


def _faces() -> bool:
    try:
        from app import robot_face_link

        return bool(robot_face_link.live_faces())
    except Exception:                                    # noqa: BLE001 -- unknown = none
        return False


def _other_walker() -> bool:
    """A leg run the app started (floor 2's routes) or a PC run its poller sees."""
    try:
        from app import robot_face_link

        return robot_face_link.nav_activity() in ("active", "stale")
    except Exception:                                    # noqa: BLE001
        return False


#: Where each walk's route file goes (gitignored; the server may write under data/).
RUN_DIR = ROOT / "data" / "events" / ".run"


def _route_file(steps: list[dict], dock: dict | None) -> Path:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    path = RUN_DIR / "walk.json"
    doc = {"about": "written by app/event_walk.py for one walk -- not a route to keep",
           "paths": {PATH_NAME: {"steps": steps}}}
    if dock:
        doc["dock"] = dock
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _python() -> str:
    """The server's own Python. On the robot that is NOT sys.executable: the
    runtime is glibc Debian's, started through its own loader by the `py`
    launcher beside the code (scripts/robot_python.py launcher(): exec
    ld-linux-aarch64.so.1 --library-path ... python3 "$@") -- sys.executable
    is the bare binary, which Android's loader cannot start. emma_server.sh
    starts the server the same way (`$R/py -s scripts/emma_server.py`)."""
    launcher = ROOT.parent / "runtime" / "py"
    return str(launcher) if launcher.is_file() else sys.executable


async def _spawn(route: Path):
    """board_walk with the server's own Python, from the code directory."""
    return await asyncio.create_subprocess_exec(
        _python(), "-s", str(SCRIPT), str(route), PATH_NAME,
        cwd=str(ROOT), stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)


OnEnd = Callable[[Run], Awaitable[None]]


async def start(spot: str, name: str, steps: list[dict] | None, *, dock: dict | None,
                on_end: OnEnd | None = None) -> dict:
    """Start one walk. {"ok": True, "run": view} or {"ok": False, "error": key,
    "error_th": ...}. Refuses rather than queues: staff see why at once."""
    def refuse(key: str) -> dict:
        turnlog.record("event_walk", phase="refused", spot=spot, reason=key)
        return {"ok": False, "error": key, "error_th": REASONS_TH.get(key, key)}

    if not settings.event_walk_enabled:
        return refuse("disabled")
    if not steps:
        return refuse("no_steps")
    if walking():
        return refuse("busy")
    if _other_walker():
        return refuse("other_walker")
    if not _faces():
        return refuse("no_face")
    if not SCRIPT.is_file():
        return refuse("no_script")
    if any(s.get("do") == "dock" for s in steps) and not dock:
        return refuse("no_route")
    run = Run(spot, name)
    STATE["run"] = run
    STATE["face_lost_at"] = None
    heartbeat()                                          # the press that started it
    try:
        route = _route_file(steps, dock)
        run.proc = await _spawn(route)
    except Exception as exc:                             # noqa: BLE001
        logger.exception("event walk: board_walk did not start")
        run.state, run.reason, run.ended = "failed", "launch_failed", time.monotonic()
        run.tail.append(type(exc).__name__)
        turnlog.record("event_walk", phase="end", spot=spot, state="failed", reason="launch_failed")
        return {"ok": False, "error": "launch_failed", "error_th": REASONS_TH["launch_failed"],
                "run": run.view()}
    run.state = "walking"
    turnlog.record("event_walk", phase="start", spot=spot, run_id=run.id, steps=len(steps))
    logger.info("event walk %s to %s: %d steps", run.id, spot, len(steps))
    run.tasks.append(asyncio.create_task(_pump(run, on_end)))
    run.tasks.append(asyncio.create_task(_watch(run)))
    return {"ok": True, "run": run.view()}


async def _pump(run: Run, on_end: OnEnd | None) -> None:
    proc = run.proc
    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            if text:
                run.tail.append(text[:200])
                if "STOP:" in text and not run.reason:
                    run.reason = text.split("STOP:", 1)[1].strip()[:120]
        code = await proc.wait()
    except asyncio.CancelledError:
        raise
    except Exception:                                    # noqa: BLE001
        logger.exception("event walk %s: lost board_walk's output", run.id)
        code = None
    run.exit_code = code
    run.ended = time.monotonic()
    if run.stop_requested:
        run.state, run.reason = "stopped", run.stop_requested
    else:
        run.state = _EXIT.get(code, "failed")
        if run.state == "failed" and not run.reason:
            run.reason = "exit %s" % code
    turnlog.record("event_walk", phase="end", spot=run.spot, run_id=run.id, state=run.state,
                   reason=run.reason, exit_code=code, seconds=round(run.ended - run.started, 1))
    logger.info("event walk %s to %s: %s (%s)", run.id, run.spot, run.state, run.reason)
    if on_end is not None:
        try:
            await on_end(run)
        except Exception:                                # noqa: BLE001
            logger.exception("event walk %s: the end callback failed", run.id)


async def _watch(run: Run) -> None:
    """The dead man's switches: the staff page's polls and the robot face."""
    while run.state in ("starting", "walking"):
        await asyncio.sleep(0.5)
        if run.state not in ("starting", "walking"):
            return
        beat = STATE["heartbeat"]
        if beat is None or time.monotonic() - beat > settings.event_walk_heartbeat_s:
            await stop("control_lost")
            return
        if _faces():
            STATE["face_lost_at"] = None
        else:
            lost = STATE["face_lost_at"] = STATE["face_lost_at"] or time.monotonic()
            if time.monotonic() - lost > FACE_GRACE_S:
                await stop("face_gone")
                return


def _signal(proc) -> None:
    with contextlib.suppress(ProcessLookupError, OSError):
        if sys.platform == "win32":
            proc.terminate()
        else:
            proc.send_signal(signal.SIGTERM)


async def stop(reason: str) -> bool:
    """Stop the walk under way, if any. Never raises, never waits on anything
    but the walker itself (bounded): a stop must not be held up."""
    run = STATE["run"]
    if run is None or run.state not in ("starting", "walking") or run.proc is None:
        return False
    if not run.stop_requested:
        run.stop_requested = reason
        turnlog.record("event_walk", phase="stop", spot=run.spot, run_id=run.id, reason=reason)
        logger.warning("event walk %s: stop (%s)", run.id, reason)
    proc = run.proc
    _signal(proc)
    try:
        await asyncio.wait_for(proc.wait(), KILL_AFTER_S)
    except (asyncio.TimeoutError, Exception):            # noqa: BLE001
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.kill()
        logger.error("event walk %s: board_walk did not exit on SIGTERM -- killed", run.id)
    await _cancel_board()
    # Until _pump has read the exit, the run still says "walking": the walk
    # that replaces this one (a scene switch, the next table) was refused
    # "busy" by its own predecessor (test_a_new_walk_replaces_the_one_under_way).
    for _ in range(100):
        if run.state not in ("starting", "walking"):
            break
        await asyncio.sleep(0.02)
    return True


async def _cancel_board() -> None:
    """Belt and braces after the walker is gone: no MoveTo left on the board."""
    try:
        from app import robot_chassis

        if robot_chassis.configured():
            await asyncio.wait_for(robot_chassis.cancel(), 3.0)
    except Exception:                                    # noqa: BLE001 -- the walker already did
        pass


async def stop_from(reason: str) -> None:
    """For the other stop paths (voice, desk): a no-op without a walk."""
    try:
        await stop(reason)
    except Exception:                                    # noqa: BLE001
        logger.exception("event walk stop (%s) failed", reason)


def snapshot() -> dict:
    run = STATE["run"]
    return {"enabled": settings.event_walk_enabled, "walking": walking(),
            "run": run.view() if run is not None else None}
