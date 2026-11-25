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

What goes wrong at the event (review 5 Nov, owner: "ทำยังไงก็ได้ให้ตอนใช้งานจริง
หุ่นขยับได้ดี ยิ่งช่วงอีเว้นเปิดตัว"), and what each one used to look like:
* Android power-cycling itself mid-walk (three times on 4 Nov) takes this server
  and board_walk with it; the board finishes its MoveTo and stands. The page came
  back saying "พร้อม -- หุ่นยืนอยู่กับที่" with the scene forgotten. Now a walk leaves
  `active.json` until it ends: found at the next start = "interrupted", with
  `resume` to carry on from the step it reached.
* A guest in the way: 8 s, then a silent stop and an English line on the page.
  Now `patience_s` in the route file (board_walk) and `@@ {"held": ...}` lines, so
  Emma asks for room while the walk still waits.
* The gate at the start of a walk: Q under 50 is one try in three at the entrance
  (board_walk's numbers), and she had already said she was going. Now `start` waits
  for the walker's own word before Emma is told anything.
* Battery was shown nowhere and asked by nothing: `health`, read off the board
  while the staff page is open, and walks refused below EVENT_WALK_MIN_BATTERY --
  except the one that goes to the dock.

`walking()` feeds the listening focus (a guest's "หยุด" from behind a walking
robot must be heard) and the wheel fence (no second driver on the wheels).
"""
from __future__ import annotations

import asyncio
import collections
import contextlib
import json
import logging
import math
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
#: How long a leg stands waiting for a guest to step aside before it gives up
#: (board_walk `patience_s`; everyone else's walks keep its 8 s).
PATIENCE_S = 45.0
#: board_walk's own bar mid-walk: see its MIN_QUALITY note (measured 4 Nov).
MIN_QUALITY = 40
#: ...and Q does not know the wrong map (5 Nov on floor 2: Q41-45 on floor 1's map,
#: lidar 0.15). Right poses standing still on floor 1: 0.26 at the lowest, median of
#: three frames. board_walk's `min_fit` has the numbers. Rehearsal R5.
MIN_FIT = 0.20
#: How long `start` waits for board_walk to say it passed its gate, before Emma is
#: told she is walking. The robot's Python takes a few seconds to import; past this
#: it counts as walking, as it did before the gate was asked.
GATE_WAIT_S = 6.0
#: No line from board_walk for this long = hung (a leg is bounded at 120 s + patience).
NO_OUTPUT_S = 240.0
#: The board read for the staff page, while the page is open.
HEALTH_EVERY_S = 3.0
HEALTH_FRESH_S = 12.0
HEALTH_IDLE_S = 30.0
DOCK_POSE_TIMEOUT_S = 25.0

#: Thai reasons for the staff page.
REASONS_TH = {
    "disabled": "การเดินปิดอยู่ (EVENT_WALK_ENABLED)",
    "busy": "หุ่นกำลังเดินอยู่แล้ว",
    "other_walker": "หุ่นกำลังเดินเส้นทางอื่นอยู่",
    "no_face": "หน้าหุ่น (แอป Emma Event หรือ Emma Ceremony) ไม่ได้เชื่อมต่อ",
    "no_steps": "จุดนี้ยังไม่ได้บันทึกตำแหน่ง",
    "no_route": "ไม่พบไฟล์เส้นทางชั้น 1",
    "no_script": "ไม่พบสคริปต์เดิน (scripts/board_walk.py)",
    "launch_failed": "เริ่มสคริปต์เดินไม่ได้",
    "staff": "ทีมงานกดหยุด",
    "cancelled": "ยกเลิกการเดินนี้",
    "voice": "สั่งหยุดด้วยเสียง",
    "desk": "กดหยุดจากหน้าคุมหุ่น",
    "control_lost": "หน้าคุมบนมือถือขาดการติดต่อ",
    "face_gone": "หน้าหุ่นหลุดการเชื่อมต่อ",
    "replaced": "เริ่มการเดินใหม่",
    "shutdown": "เซิร์ฟเวอร์ปิด",
    "low_battery": "แบตเตอรี่ต่ำ ต้องกลับแท่นก่อน",
    "restarted": "เครื่องหุ่นรีบูตหรือเซิร์ฟเวอร์เริ่มใหม่ระหว่างเดิน",
    "hung": "สคริปต์เดินไม่ตอบ",
    "nothing": "ไม่มีการเดินที่ค้างอยู่",
    "gate": "หุ่นไม่ได้เริ่มเดิน",
    "walking": "ทำระหว่างหุ่นเดินไม่ได้",
    "no_dock": "ไม่พบตำแหน่งแท่นชาร์จในไฟล์เส้นทาง",
    "fit": "เลเซอร์ไม่ตรงแผนที่ที่จุดแท่น หุ่นอาจไม่ได้อยู่บนแท่น หรือมีคนยืนล้อมอยู่",
    "busy_board": "บอร์ดยังทำงานค้างอยู่",
    "pose_busy": "หุ่นกำลังหาตำแหน่งด้วยเลเซอร์ (หุ่นไม่ขยับ) — รอสักครู่แล้วกดใหม่",
    "app_stop": "กดหยุดที่ตัวหุ่น (ปุ่ม B หรือหน้าคุมบนหุ่น)",
    "arm_not_rest": "แขนยังไม่อยู่ท่าพัก (กำลังจับมือ/ทำท่า หรือสวิตช์ท่าทางบนหุ่นปิด) ลองใหม่อีกครั้ง",
    "no_call": "หน้าหุ่นเปิดสายไม่ขึ้นใน 8 วินาที (การพักแขน โบกมือ และเสียงต้องใช้สาย) — ดูว่าแอป Emma Event เปิดอยู่บนหุ่น แล้วกดใหม่",
    # the phone's red stop latches the app too (robot_body.staff_stop): "ที่ตัวหุ่น" alone read as
    # somebody else at the robot (harness 19 Nov: "เดินต่อ" right after the phone's own stop)
    "stop_latched": "เพิ่งกดหยุด (ปุ่มแดงบนมือถือ หรือที่ตัวหุ่น) — หุ่นล็อกการเดินไว้ 60 วินาที รอสักครู่แล้วกดใหม่",
    # app/event_box.py's latch (third review, 23 Nov): only the walk out of the box moves her while it holds
    "in_box": ("หุ่นยังอยู่ในกล่อง — เดินได้แค่การเดินออกจากกล่อง (การ์ดเปิดตัว / 'เดินออกให้ครบ') · ถ้าหุ่นพ้นกล่องแล้ว "
               "กด 'หุ่นออกจากกล่องแล้ว — ปลดล็อกแขน' ก่อนเดินไปที่อื่น"),
}

#: What kind of stop this is and the one thing staff can do about it. board_walk's
#: reasons are English lines for the owner's log; the phone needs the next move.
_NEXT_TH = {
    "blocked": ("มีคนหรือของขวางทาง", "ขอทาง/ย้ายของ แล้วกด 'เดินต่อ'"),
    "lost": ("หุ่นไม่แน่ใจตำแหน่งตัวเอง",
             "เข็นหุ่นเข้าแท่น แล้วกด 'ตั้งตำแหน่งที่แท่น' ก่อนเดินใหม่"),
    "dock": ("เข้าแท่นไม่สำเร็จ", "ดูว่ามีอะไรอยู่หลังหุ่น แล้วกด 'เดินต่อ' หรือเข็นเข้าแท่นเอง"),
    "control": ("หน้าคุมขาดการติดต่อ (Wi-Fi หรือจอล็อก)", "เปิดหน้านี้ค้างไว้ แล้วกด 'เดินต่อ'"),
    "face": ("หน้าหุ่นหลุดการเชื่อมต่อ", "เปิดแอป Emma Event บนหุ่น แล้วกด 'เดินต่อ'"),
    "restart": ("เครื่องหุ่นรีบูตหรือเซิร์ฟเวอร์เริ่มใหม่ระหว่างเดิน",
                "ดูว่าหุ่นยืนอยู่ตรงไหนและไม่มีอะไรขวาง แล้วกด 'เดินต่อ' หรือ 'กลับแท่น'"),
    "board": ("บอร์ดเดินไม่ตอบ", "กด 'เดินต่อ' ถ้ายังไม่ได้ ปิด-เปิดหุ่นตามคู่มือซ้อม"),
    "person": ("มีคนสั่งหยุด", "กด 'เดินต่อ' เมื่อพร้อม"),
}
_KIND_OF_KEY = {"control_lost": "control", "face_gone": "face", "restarted": "restart", "hung": "board",
                "staff": "person", "voice": "person", "desk": "person", "cancelled": "person",
                "app_stop": "person"}


#: A stop of board_walk's that no kind above names: Thai for the phone, the English in the details.
_UNKNOWN_STOP_TH = ("เดินไม่สำเร็จ (รายละเอียดใน 'รายละเอียดการเดิน')",
                    "ดูรอบหุ่นว่าไม่มีอะไรขวาง แล้วกด 'เดินต่อ' หรือ 'กลับแท่น'")
#: Held or stopped closer than this to where it set out = the wheels never turned (Run.still).
WHEELS_STILL_M = 0.03
#: ... and what the phone says then (owner 19 Nov, the night's first walk: the E-stop was still in).
STILL_TH = ("ล้อหุ่นไม่ขยับเลยตั้งแต่กด — ถ้าข้างหน้าหุ่นว่าง ปุ่มหยุดฉุกเฉิน (E-stop) บนตัวหุ่นอาจยังกดค้างอยู่ "
            "(บอร์ดไม่บอก): หมุนคลายปุ่ม ถ้าหุ่นยังไม่เดินต่อเอง กดใหม่")


def kind_of(reason: str | None) -> str | None:
    """The kind of a stop, from a REASONS_TH key or board_walk's own line."""
    if not reason:
        return None
    if reason in _KIND_OF_KEY:
        return _KIND_OF_KEY[reason]
    text = reason.lower()
    if "taken away" in text:
        return "person"                                  # board_walk forward: B or the robot's screen
    # the dock first: board_walk's back-in says "0.04 m past the dock POSE and no contact" -- read as
    # "pose" it told the phone "หุ่นไม่แน่ใจตำแหน่งตัวเอง" (16 Nov, floor 2: the robot backed to where it
    # had set out from, in front of the dock, and only missed the contacts)
    if "dock" in text and ("not on" in text or "not docked" in text or "behind" in text):
        return "dock"
    if "localization quality" in text or "pose" in text:
        return "lost"
    if "stopped answering" in text or text.startswith("exit"):
        return "board"
    if any(w in text for w in ("stuck", "held up", "something", "not moving", "not out", "ended", "too long", "no room",
                               "off the line", "board event", "refused", "another action", "not there")):
        return "blocked"
    return None


#: A walk up to a guest (app/face_approach.py). Its one step is where somebody stood
#: when it set off: carrying it on later, or after a restart, is walking at nobody.
NO_RESUME_SPOT = "approach"


class Run:
    def __init__(self, spot: str, name: str):
        self.id = uuid.uuid4().hex[:10]
        self.spot = spot
        self.name = name
        self.started = time.monotonic()
        self.ended: float | None = None
        # starting | walking | arrived | stopped | failed | interrupted | refused (never set out:
        # stopped or refused while still checking -- the arm's rest -- before any walker ran)
        self.state = "starting"
        self.launching = False           # past every check: the walker is being spawned
        self.prev: Run | None = None     # current() before this one, given back if it never sets out
        self.reason: str | None = None   # why it stopped, a REASONS_TH key or board_walk's word
        self.exit_code: int | None = None
        self.proc = None
        self.tail: collections.deque[str] = collections.deque(maxlen=TAIL_LINES)
        self.stop_requested: str | None = None
        self.tasks: list[asyncio.Task] = []
        self.steps: list[dict] = []
        self.dock: dict | None = None
        self.done = 0                    # steps finished (board_walk's `@@ {"step": n}`)
        self.held_at: list | None = None  # standing still for something in the way
        self.saw_started = False         # the walker passed its own gate
        self.gate = asyncio.Event()      # ... or ended
        self.on_event: OnEvent | None = None
        self.last_line = time.monotonic()
        # where board_walk set out (`started` -> `at`) and where it was last held or stopped:
        # the same spot = the wheels never turned (`still`, WHEELS_STILL_M)
        self.start_at: list | None = None
        self.last_at: list | None = None
        # a `forward` step's line (board_walk's `@@ forward_line`: where it set out, where it is): how far it really
        # went, from the board's pose -- `walked_m` (a walk out of the box, owner 23 Nov)
        self.forward_line: dict | None = None
        # board_walk lost the line (a pose jump: its two ends in two frames), or the walker had to be killed: how far
        # a forward went is not known (`walked_m` None -- second review 23 Nov: its last line said 0.0)
        self.line_lost = False
        self.killed = False
        # a `forward` with `meet_m` ended in front of someone (board_walk's `@@ {"met": step, ...}`): card 1's walk up
        # to an unmarked guest gives the hand only then (app/event_tour.py, owner 23 Nov)
        self.met: dict | None = None

    def can_resume(self) -> bool:
        if self.spot == NO_RESUME_SPOT:
            return False
        if any(_line_step(s) for s in self.steps[self.done:]):
            # a metre ahead of where she stood (board_walk `forward`, the debut's walk while its
            # spot is not recorded): started again it is a metre ahead of where she stands NOW
            return False
        return (self.state in ("stopped", "failed", "interrupted") and self.done < len(self.steps)
                and self.reason not in ("replaced", "shutdown"))

    def still(self) -> bool:
        """It set out and has been held, or has stopped, where it set out: the wheels never turned.
        19 Nov: an E-stop left pressed after the night before -- the board does not report it
        (hasSystemEmergencyStop false while pressed), the wheels simply do not move."""
        if self.start_at is None or self.last_at is None or None in self.start_at[:2] or None in self.last_at[:2]:
            return False
        try:
            return math.hypot(float(self.last_at[0]) - float(self.start_at[0]),
                              float(self.last_at[1]) - float(self.start_at[1])) < WHEELS_STILL_M
        except (TypeError, ValueError, IndexError):
            return False

    def view(self) -> dict:
        now = time.monotonic()
        kind = kind_of(self.reason) if self.state in ("stopped", "failed", "interrupted") else None
        what, next_th = _NEXT_TH.get(kind or "", (None, None))
        # a person's stop is a person's: her wheels not turning since is no news
        still = self.still() and kind != "person" and self.state in ("walking", "stopped", "failed")
        if self.state in ("stopped", "failed") and kind is None and self.reason and self.reason not in REASONS_TH:
            # board_walk's own English line with no kind of its own (review 19 Nov: "turn: 20 s and
            # still ..." reached the phone as it was, with no next move): the words stay in the
            # walk's details, the phone gets Thai and the move every stop has
            what, next_th = _UNKNOWN_STOP_TH
        if (next_th and kind != "lost" and not self.can_resume() and any("back_to" in s for s in self.steps)
                and _back_line_path().exists()):
            next_th = "ดูข้างหลังหุ่นว่าไม่มีอะไรขวาง แล้วกด 'กลับแท่น' อีกครั้ง (ถอยต่อจากตรงที่หุ่นยืน)"
        elif (next_th and kind != "lost" and not self.can_resume() and any("back_to" in s for s in self.steps)):
            # the way back is spent (16 Nov, floor 2: backed the whole way to where it set out, in front
            # of the contacts): no 'เดินต่อ' to press, and the map's 'กลับแท่น' is another floor's there
            next_th = "ถอยครบทางที่เดินออกมาแล้ว แต่ไม่แตะขั้วแท่น — ดูข้างหลังหุ่น แล้วเข็นหุ่นเข้าแท่นเอง"
        elif any(s.get("to") or s.get("glide") for s in self.steps) and kind in ("lost", "control", "face"):
            # a map walk with a walk ahead in it (card 3, 22 Nov) that stopped for one of these: their own move
            # (second review: "localization quality 30" said "press the card again" -- a MoveTo from a pose
            # that had just jumped -- and lost the push-onto-the-dock / set-the-pose advice)
            pass
        elif next_th and not self.can_resume() and any("forward" in s for s in self.steps):
            # a walk ahead is never carried on (can_resume): no 'เดินต่อ' button to point at (review 10 Nov)
            next_th = ("ดูรอบหุ่นว่าไม่มีอะไรขวาง แล้วกดฉากนี้อีกครั้ง (หุ่นกลับไปเริ่มจากจุดแรกของฉาก)"
                       if any(s.get("to") for s in self.steps) else    # card 3 (22 Nov): from the counter again
                       "ดูรอบหุ่นว่าไม่มีอะไรขวาง แล้วกดฉากนี้อีกครั้ง (เดินใหม่จากตรงที่หุ่นยืน)")
        return {
            "id": self.id, "spot": self.spot, "name": self.name, "state": self.state,
            "reason": self.reason, "reason_th": what or REASONS_TH.get(self.reason or "", self.reason),
            "elapsed_s": round((self.ended or now) - self.started, 1),
            "tail": list(self.tail),
            "kind": kind, "next_th": next_th, "can_resume": self.can_resume(),
            "held": self.held_at is not None, "done_steps": self.done, "total_steps": len(self.steps),
            "still": still, "still_th": STILL_TH if still else None,
        }


#: `stops`: every stop a PERSON asked for (`PERSON_STOPS`), a walk under way or not, and
#: `last_stop` its reason. A press that awaits things before it reaches `start` (event_host.go_spot:
#: the roam's stop, the replaced walk's, a pose read) compares the count from its own start
#: (`stops_seen`, `unless_stopped`): review 7 Nov, the red stop in that window found no walk to
#: stop, and the walk launched after it.
STATE: dict = {"run": None, "heartbeat": None, "face_lost_at": None, "health": None, "health_task": None,
               "stops": 0, "last_stop": None, "pose_busy": None}


def reset() -> None:
    STATE.update(run=None, heartbeat=None, face_lost_at=None, health=None, health_task=None,
                 stops=0, last_stop=None, pose_busy=None)


# `pose_busy`: who holds the board's pose now -- a lidar search or a pose write (find_dock_pose,
# set_dock_pose) or a dock re-learn's look on the contacts. Review 20 Nov, proved: the search takes
# seconds, `walking()` was asked once before it, a walk set out meanwhile and the board's pose was
# PUT under the moving wheels. Now one flag, both ways: no walk starts while it is held (`start`),
# no search runs while a walk is on (`walking()`), and the phones say it (`snapshot`).

def pose_busy() -> bool:
    return STATE["pose_busy"] is not None


@contextlib.contextmanager
def pose_hold(owner: str):
    """Hold the board's pose for `owner` (taken synchronously: nothing awaits between a caller's
    checks and this). Nested holds of the same owner give back what was there."""
    prev = STATE["pose_busy"]
    STATE["pose_busy"] = owner
    try:
        yield
    finally:
        STATE["pose_busy"] = prev


#: A stop waits this long for a walker that is still being started.
SPAWN_WAIT_S = 3.0
#: The stops that count for `stops()`: a person's. Not "replaced" (every start_scene/go_spot stops
#: the walk before its own), not a walk's own dead man's switches (they stop that run only).
PERSON_STOPS = frozenset({"staff", "voice", "desk", "shutdown"})


def walking() -> bool:
    """True from the moment `start` took the walk (state "starting": the arm's rest is being asked
    for) to its end. Review 7 Nov: the run used to be set only after the rest's ~2.5 + 12 s, and in
    that window a second start, a gesture (server_walking) and the red stop all saw no walk."""
    run = STATE["run"]
    return run is not None and run.state in ("starting", "walking")


def moving() -> bool:
    """The walker is out (state "walking"), not just taken: for what follows the WHEELS -- the
    lidar's background, the walk pictures, the head's walk scan. `walking()` is true through the
    arm's rest as well (up to ~14.5 s, and for a start refused or stopped in it): read there, the
    lidar forgot everyone standing still and the walk pictures began for walks that never set
    out (review 7 Nov, round 3). Gates and stops keep `walking()`."""
    run = STATE["run"]
    return run is not None and run.state == "walking"


def current() -> Run | None:
    return STATE["run"]


def stops() -> int:
    """How many people's stops so far (see STATE). Compare two readings, never the number."""
    return int(STATE.get("stops") or 0)


def heartbeat() -> None:
    """The staff page is still watching (every poll of /event/state)."""
    STATE["heartbeat"] = time.monotonic()
    _ensure_health()


def _ensure_health() -> None:
    task = STATE["health_task"]
    if task is not None and not task.done():
        return
    try:
        from app import robot_chassis

        if not robot_chassis.configured():
            return
        STATE["health_task"] = asyncio.get_running_loop().create_task(_health_loop())
    except Exception:                                    # noqa: BLE001 -- no loop, no chassis: nothing to read
        pass


async def _read_health() -> dict:
    from app import robot_chassis

    try:
        power = await asyncio.wait_for(robot_chassis.power(), 2.0)
        quality = await asyncio.wait_for(robot_chassis.localization_quality(), 2.0)
    except Exception:                                    # noqa: BLE001
        return {"at": time.monotonic(), "board": False}
    return {"at": time.monotonic(), "board": True, "battery": power.get("batteryPercentage"),
            "charging": power.get("isCharging"), "docking": power.get("dockingStatus"), "q": quality}


async def _health_loop() -> None:
    while time.monotonic() - (STATE["heartbeat"] or 0.0) < HEALTH_IDLE_S:
        STATE["health"] = await _read_health()
        await asyncio.sleep(HEALTH_EVERY_S)


def health_view() -> dict:
    """What the staff page shows of the board. known=False: no reading to go on
    (no chassis here, or not read for a while) -- which is not "all is well"."""
    h = STATE["health"]
    if not h or time.monotonic() - h["at"] > HEALTH_FRESH_S:
        return {"known": False}
    if not h.get("board"):
        return {"known": True, "board": False}
    battery, q = h.get("battery"), h.get("q")
    return {"known": True, "board": True, "battery": battery, "charging": h.get("charging"),
            "docking": h.get("docking"), "q": q, "lost": isinstance(q, (int, float)) and q < MIN_QUALITY,
            "low_battery": isinstance(battery, (int, float)) and battery < settings.event_walk_min_battery}


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
           "events": True, "patience_s": PATIENCE_S, "min_quality": MIN_QUALITY, "min_fit": MIN_FIT,
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
#: Called with the run while it is still walking ("held": something is in the way).
OnEvent = Callable[[Run, str], Awaitable[None]]


def _active_path() -> Path:
    return RUN_DIR / "active.json"


def _note_active(run: Run) -> None:
    """A walk in progress leaves a file: Android going down takes this server with
    it (4 Nov, three times), and nothing else would remember there was a walk."""
    try:
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        _active_path().write_text(json.dumps(
            {"spot": run.spot, "name": run.name, "steps": run.steps, "dock": run.dock, "done": run.done,
             "at": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        logger.exception("event walk: could not note the walk in progress")


def _clear_active() -> None:
    with contextlib.suppress(OSError):
        _active_path().unlink(missing_ok=True)


# ---- the way back along a walk ahead (owner 11 Nov: "ขอปุ่มกลับแท่น") ---------------------------
# The debut walked a metre straight off the floor-2 dock while the board held floor 1's map: a
# dock walk (MoveTo to floor 1's dock) was out of the question. board_walk's `forward` says the
# line it came out on (`@@ forward_line`); kept here, the staff page's "กลับแท่น" backs along it
# (board_walk `back_to`: velocity only, from ON that line). A file: the server dies with Android.
#: Older than this, a line is not trusted: the board keeps a stale pose when the robot is carried
#: (4 Nov, Q32), and a line whose pose checks still pass would back her a metre blind somewhere else
#: (review 11 Nov). A server start forgets it too (`recover_after_restart`).
BACK_LINE_MAX_S = 30 * 60.0
#: A walk ahead that sets out ON the line (anywhere along it, facing out along it) makes it longer.
LINE_JOIN_M, LINE_JOIN_DEG = 0.05, 3.0
#: board_walk's BACK_TO_MAX_M: a longer line is no way back (the back-in's own 40 s).
BACK_LINE_MAX_M = 2.5


def _back_line_path() -> Path:
    return RUN_DIR / "back_line.json"


def _pose3(value) -> list[float] | None:
    if (isinstance(value, list) and len(value) == 3
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
        return [float(v) for v in value]
    return None


def back_line() -> dict | None:
    """{"dock": [x, y, yaw°] where the walk ahead set out ON the dock, "end": where it stopped,
    "at": time} -- or None (none, unreadable, older than BACK_LINE_MAX_S)."""
    try:
        doc = json.loads(_back_line_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.exception("event walk: unreadable way back")
        forget_back_line("unreadable")
        return None
    dock, end = _pose3(doc.get("dock")), _pose3(doc.get("end"))
    if dock is None or end is None or not isinstance(doc.get("at"), (int, float)):
        forget_back_line("unreadable")
        return None
    if time.time() - float(doc["at"]) > BACK_LINE_MAX_S:
        forget_back_line("old")
        return None
    return {"dock": dock, "end": end, "at": float(doc["at"])}


def back_view() -> dict | None:
    """For the staff page: how far out along the way back the robot stopped."""
    line = back_line()
    if line is None:
        return None
    (dx, dy, _), (ex, ey, _) = line["dock"], line["end"]
    return {"out_m": round(((ex - dx) ** 2 + (ey - dy) ** 2) ** 0.5, 2)}


def forget_back_line(why: str) -> None:
    path = _back_line_path()
    if path.exists():
        with contextlib.suppress(OSError):
            path.unlink()
        turnlog.record("event_walk", phase="back_line", kept=False, why=why)


def _note_forward_line(line: dict) -> None:
    """board_walk's `@@ forward_line`: off the dock = a new way back; from where the way back
    ended, along it = that way, longer; anywhere else = no way back (the robot is off it)."""
    import math

    frm, to = _pose3(line.get("from")), _pose3(line.get("to"))
    if frm is None or to is None:
        return
    if line.get("on_dock") is True:
        doc, joined = {"dock": frm, "end": to}, False
    else:
        old = back_line()
        if old is None or not _stands_on(old, frm):
            forget_back_line("off_the_line")
            return
        doc, joined = {"dock": old["dock"], "end": to}, True
    if not _stands_on(doc, to, BACK_LINE_MAX_M):
        # out past what the way back may back (pressed three times), or off it at its end
        forget_back_line("too_far" if _along(doc, to) > BACK_LINE_MAX_M else "off_the_line")
        return
    doc["at"] = time.time()
    try:
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        _back_line_path().write_text(json.dumps(doc), encoding="utf-8")
    except OSError:
        logger.exception("event walk: could not keep the way back")
        return
    turnlog.record("event_walk", phase="back_line", kept=True, joined=joined,
                   out_m=round(math.hypot(doc["end"][0] - doc["dock"][0], doc["end"][1] - doc["dock"][1]), 2))


def walked_m(run) -> float | None:
    """How far a walk's `forward` step went along the heading it set out on, from the board's own poses (its last
    `@@ forward_line`) -- never board_walk's "done": a forward that met something within FORWARD_SHORT_OK_M of its
    end used to end done (review 23 Nov: a walk out of the box 0.2 m short counted as out). None = no line seen."""
    import math

    line = getattr(run, "forward_line", None)
    if not isinstance(line, dict) or line.get("end") is not True:
        return None                                      # only the step's closing line (board_walk `line_note(end)`)
    if getattr(run, "line_lost", False) or getattr(run, "killed", False):
        return None                                      # a pose jump, a walker killed: not known
    frm, to = _pose3(line.get("from")), _pose3(line.get("to"))
    if frm is None or to is None:
        return None
    h = math.radians(frm[2])
    return round((to[0] - frm[0]) * math.cos(h) + (to[1] - frm[1]) * math.sin(h), 3)


def _along(line: dict, pose: list[float]) -> float:
    import math

    dx, dy, dyaw = line["dock"][0], line["dock"][1], math.radians(line["dock"][2])
    return (pose[0] - dx) * math.cos(dyaw) + (pose[1] - dy) * math.sin(dyaw)


def _stands_on(line: dict, pose: list[float], beyond: float | None = None) -> bool:
    """`pose` stands on the line (aside, heading), between the dock and its end -- or `beyond` m
    out at most when given (a walk ahead's end)."""
    import math

    dx, dy, dyaw = line["dock"][0], line["dock"][1], math.radians(line["dock"][2])
    aside = (pose[0] - dx) * -math.sin(dyaw) + (pose[1] - dy) * math.cos(dyaw)
    turn = abs((pose[2] - line["dock"][2] + 180.0) % 360.0 - 180.0)
    along = _along(line, pose)
    far = beyond if beyond is not None else _along(line, line["end"]) + LINE_JOIN_M
    return abs(aside) <= LINE_JOIN_M and turn <= LINE_JOIN_DEG and -LINE_JOIN_M <= along <= far


def _line_step(step: dict) -> bool:
    """A walk ahead or the way back: both start from where the robot stands (no resuming them)."""
    return "forward" in step or "back_to" in step


def recover() -> Run | None:
    """At server start: a walk still noted = it never ended, the server died under
    it. The page says so instead of "ready", and `resume` carries on from there."""
    try:
        doc = json.loads(_active_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.exception("event walk: unreadable note of a walk in progress")
        _clear_active()
        return None
    run = Run(str(doc.get("spot")), str(doc.get("name")))
    run.state, run.reason, run.ended = "interrupted", "restarted", time.monotonic()
    run.steps, run.dock, run.done = list(doc.get("steps") or []), doc.get("dock"), int(doc.get("done") or 0)
    run.tail.append("walk noted at %s never ended" % doc.get("at"))
    STATE["run"] = run
    _clear_active()
    if any(isinstance(s, dict) and s.get("short_ok") is False for s in run.steps):
        # a walk out of the box (only its forward has short_ok False) cut by the restart: how far she went is not
        # known -- the latch says so (app/event_box.py: a press found under way at load is `unknown`, never re-armed)
        from app import event_box

        if event_box.held():
            event_box.outcome("unknown")
    turnlog.record("event_walk", phase="interrupted", spot=run.spot, done=run.done, steps=len(run.steps))
    logger.warning("event walk to %s was interrupted by a restart (%d of %d steps done)",
                   run.spot, run.done, len(run.steps))
    return run


async def recover_after_restart() -> None:
    """recover() + whatever the board is still doing on the walker's last word: a
    MoveTo outlives Android by as long as its leg. And no way back from before: the
    restart may be Android's, and a robot carried meanwhile keeps a stale pose."""
    forget_back_line("restart")
    if recover() is not None:
        await _cancel_board()


async def resume_last(*, on_end: OnEnd | None = None, on_event: OnEvent | None = None) -> dict:
    """The stopped walk, from the step it reached. One press at the event: the
    alternative was the scene again, opening line and all."""
    run = STATE["run"]
    if run is None or not run.can_resume():
        return {"ok": False, "error": "nothing", "error_th": REASONS_TH["nothing"]}
    left = [dict(s) for s in run.steps[run.done:]]
    # pushed onto the dock in between: leave it the dock's way. A no-op anywhere else.
    if left and left[0].get("do") not in ("undock", "dock"):
        left.insert(0, {"do": "undock"})
    if (run.dock or {}).get("leave_by_approach"):
        # ... and out through its corridor (review 17 Nov: the steps left were routed from
        # where she stood when the walk was first pressed, not from the dock she is on now).
        # Only for a dock that asks for that way out: without it the steps are as they were.
        from app import event_host

        left = await event_host.steps_from_pose(left, run.dock)
    # ... and from where she stands NOW, its big turns watched (review 23 Nov, event_tour.watched_turns): stopped and
    # turned by hand, the next point may be behind her. No pose read = the steps as they were.
    from app import event_tour

    here = await event_tour._where()
    if here is not None:
        left = event_tour.watched_turns(left, here)
    return await start(run.spot, run.name, left, dock=run.dock, on_end=on_end, on_event=on_event)


async def _board_once(dock: dict, *flags: str, timeout: float = DOCK_POSE_TIMEOUT_S) -> dict | None:
    """board_walk in one of its no-walk modes (--set-dock-pose, --find-dock-pose): its last `@@`
    line, {} when it printed none, None when it could not be run (or ran out of time)."""
    proc = None
    try:
        route = _route_file([{"do": "dock"}], dock)
        proc = await asyncio.create_subprocess_exec(
            _python(), "-s", str(SCRIPT), str(route), PATH_NAME, *flags,
            cwd=str(ROOT), stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except Exception as exc:                             # noqa: BLE001
        with contextlib.suppress(ProcessLookupError, OSError, AttributeError):
            proc.kill()
        logger.warning("board_walk %s: %s", " ".join(flags), type(exc).__name__)
        return None
    word: dict = {}
    for line in out.decode("utf-8", "replace").splitlines():
        if line.startswith("@@ "):
            with contextlib.suppress(ValueError):
                word = json.loads(line[3:])
    return word


#: --find-dock-pose searches ~16,000 poses: a few seconds on the robot, the frames and map read first.
DOCK_FIND_TIMEOUT_S = 45.0


async def find_dock_pose(dock: dict | None, *, set_it: bool, hold: str | None = None) -> dict:
    """Where the dock really is by the lidar, near the route file's pose (board_walk
    --find-dock-pose; `set_it` = --set: the board's pose is written there when the search is
    good). {"ok": True, "found": {...board_walk's word}} or a refusal. Moves nothing. Holds the
    pose (`pose_hold`) for its whole run; `hold` = a caller's own hold it runs inside."""
    def refuse(key: str, **more) -> dict:
        return {"ok": False, "error": key, "error_th": REASONS_TH.get(key, key), **more}

    if walking():
        return refuse("walking")
    if STATE["pose_busy"] is not None and STATE["pose_busy"] != hold:
        return refuse("pose_busy")
    if not dock:
        return refuse("no_dock")
    with pose_hold(hold or "find"):
        word = await _board_once(dock, "--find-dock-pose", *(("--set",) if set_it else ()), timeout=DOCK_FIND_TIMEOUT_S)
    if word is None:
        turnlog.record("event_dock_find", ok=False, why="launch_failed")
        return refuse("launch_failed")
    turnlog.record("event_dock_find", ok=bool(word.get("ok")), why=word.get("why"), fit=word.get("fit"),
                   file_fit=word.get("file_fit"), shift_m=word.get("shift_m"), set=bool(word.get("set")))
    if not word.get("dock_find"):
        return refuse({"busy": "busy_board"}.get(word.get("why"), "launch_failed"))
    return {"ok": True, "found": word}


async def set_dock_pose(dock: dict | None) -> dict:
    """The robot is on the dock and its pose is stale (a boot after being moved):
    board_walk writes the dock's pose if the lidar agrees (--set-dock-pose)."""
    def refuse(key: str, **more) -> dict:
        return {"ok": False, "error": key, "error_th": REASONS_TH.get(key, key), **more}

    if walking():
        return refuse("walking")
    if pose_busy():
        return refuse("pose_busy")
    if not dock:
        return refuse("no_dock")
    with pose_hold("set"):
        word = await _board_once(dock, "--set-dock-pose")
    if word is None:
        turnlog.record("event_dock_pose", ok=False, why="launch_failed")
        return refuse("launch_failed")
    turnlog.record("event_dock_pose", ok=bool(word.get("dock_pose")), why=word.get("why"),
                   fit=word.get("fit"), q=word.get("q"))
    if word.get("dock_pose"):
        return {"ok": True, "fit": word.get("fit"), "q": word.get("q")}
    why = word.get("why")
    return refuse({"fit": "fit", "busy": "busy_board"}.get(why, "launch_failed"), fit=word.get("fit"))


def _boxed(steps: list[dict]) -> bool:
    """The box's latch holds and this is not a walk out of it. A walk out = straight-ahead steps only, each all of it
    or not out (`short_ok` False: event_host.ahead_steps of a `walk_out_of_box` scene -- card 1, the ceremony's
    opening, "เดินออกให้ครบ"); a resumed one has an undock before it and is not."""
    from app import event_box

    if not event_box.held():
        return False

    def out_step(s) -> bool:
        return (isinstance(s, dict) and "forward" in s and s.get("short_ok") is False and s.get("do") is None
                and "back_to" not in s)

    lead = 0
    while lead < len(steps) and out_step(steps[lead]):
        lead += 1
    # ... and after its whole forward, steps marked `after_box` (card 1's walk out and on to the corner in one walker,
    # owner 25 Nov ~04:25): the walker runs them only once that forward is done -- a forward that stops ends the walker
    return lead == 0 or not all(isinstance(s, dict) and s.get("after_box") is True for s in steps[lead:])


def _abandon(run: Run, key: str, *, stopped: bool = False) -> None:
    """A walk that never set out: over at once, no walker spawned. Refused by a check (the arm
    not at rest, the app's latch) = current() is what it was before, as for any refusal.
    `stopped` (a stop while the arm was being rested) = it stays current(), "stopped" with
    the stop's reason: the stop paths read a person's stop off the run (face_approach's tick
    turns itself off on it), and the page says the walk was stopped."""
    run.state = "stopped" if stopped else "refused"
    run.reason, run.ended = key, time.monotonic()
    run.gate.set()
    if stopped:
        turnlog.record("event_walk", phase="end", spot=run.spot, run_id=run.id, state="stopped",
                       reason=key, launched=False)
    elif STATE["run"] is run:
        STATE["run"] = run.prev


async def start(spot: str, name: str, steps: list[dict] | None, *, dock: dict | None,
                on_end: OnEnd | None = None, on_event: OnEvent | None = None,
                autonomous: bool = False, stops_seen: int | None = None, beat: bool = True,
                rested_at: float | None = None, rested_by: str = "rest") -> dict:
    """Start one walk. {"ok": True, "run": view} or {"ok": False, "error": key,
    "error_th": ...}. Refuses rather than queues: staff see why at once.

    `rested_at`: the caller rested the arm itself, asked at that time (time.time()) -- the ceremony's walk
    out of the box rests it during the screen's countdown, so the wheels go on the hit (app/event_tour.py,
    owner 23 Nov). Taken only while it still holds (`_rest_holds`: the app's last word on the arm is that
    rest, done, nothing since); else the rest is asked here as for every walk.
    `rested_by` "handshake": the tour's walk right after its own handshake (owner 24 Nov: "ตอนจับมือเสร็จ เหมือนมันจะ
    ยกมือแล้วเอาหัวลง"): the app lowers that arm itself (its slow lowering, group 99, then #AGF = done) -- a second group
    99 ~4 s after it is what she was seen to do there. Only the tour says so, and only for a done that came by the
    arm's own answer (event_tour.HANDSHAKE_SURE_S).

    `autonomous`: nobody pressed anything (a walk up to somebody, a roaming leg). It does not
    count as the staff page being alive: a dead page got ~5 s more of walking from it.
    `beat=False`: a press made it, but not now (card 1's tour legs, app/event_tour.py -- minutes after
    the press): it rings the call as a press does, and is not the page's poll either (autonomous's reason).
    `stops_seen`: `stops()` when the caller's press began -- a person's stop since = no walk."""
    def refuse(key: str) -> dict:
        turnlog.record("event_walk", phase="refused", spot=spot, reason=key)
        return {"ok": False, "error": key, "error_th": REASONS_TH.get(key, key)}

    def stopped_since() -> bool:
        return stops_seen is not None and stops() != stops_seen

    if not settings.event_walk_enabled:
        return refuse("disabled")
    if not steps:
        return refuse("no_steps")
    if _boxed(steps):
        # she may be in the box (app/event_box.py; third review, 23 Nov): a MoveTo, a turn, a dock, a back_to from
        # inside it drives into its walls -- every walk but the walk out of it (cards 2/3, a spot, home, a roam, a
        # resume, the order's walks, a walk up to a guest) waits for staff's "out of the box"
        return refuse("in_box")
    if stopped_since():
        return refuse(STATE.get("last_stop") or "staff")
    if walking():
        return refuse("busy")
    if pose_busy():
        # a lidar search may PUT the board's pose when it ends: not under moving wheels (review 20 Nov)
        return refuse("pose_busy")
    if _other_walker():
        return refuse("other_walker")
    if not _faces():
        return refuse("no_face")
    if _app_latched():
        # The app's own walks are held for its 60 s latch; this one was not (review 6 Nov).
        return refuse("stop_latched")
    if not SCRIPT.is_file():
        return refuse("no_script")
    going_home = any(s.get("do") == "dock" or "back_to" in s for s in steps)
    if any(s.get("do") == "dock" for s in steps) and not dock:
        return refuse("no_route")
    if health_view().get("low_battery") and not going_home:
        return refuse("low_battery")
    # The walk is taken HERE, before the first await (review 7 Nov, proved with two starts at once:
    # both passed `walking()` while the first awaited the arm's rest, both spawned walkers, and the
    # red stop reached only the second -- the first stopped by E-stop alone). From here walking()
    # and server_walking() are true (no gesture is accepted while she is about to walk), and a stop
    # finds this run: it never sets out (`_abandon`).
    run = Run(spot, name)
    run.steps, run.dock, run.on_event = [dict(s) for s in steps], dock, on_event
    prev = STATE["run"]
    run.prev = prev.prev if prev is not None and prev.state == "refused" else prev
    STATE["run"] = run
    STATE["face_lost_at"] = None
    if not autonomous and beat:
        heartbeat()                                      # the press that started it
    try:
        # The arm's rest goes over the robot face's call (below): with none, rung first -- muted
        # under a held mic (owner 10 Nov, "เปิดสายแต่ mute ไมค์ได้ไหม"). EVENT_WALK_ARM_REST off
        # (the tests' default) asks the app nothing, so it needs no call either.
        called = await _call_up(run, autonomous) if settings.event_walk_arm_rest else True
        rested = (True if _rest_holds(rested_at, rested_by) else await _arm_rested()) if called else False
    except BaseException:
        # whoever asked has gone (a roam cancelled by a stop): nothing was spawned, nothing walks
        if run.state == "starting":
            run.stop_requested = run.stop_requested or "cancelled"
            _abandon(run, run.stop_requested, stopped=True)
        raise
    if stopped_since() and not run.stop_requested:
        run.stop_requested = STATE.get("last_stop") or "staff"
    if run.stop_requested or run.state != "starting":
        # stopped while the arm was being rested (review 7 Nov: the red stop answered
        # stopped:False and the walk launched afterwards). `_stop_run` has ended it already.
        key = run.stop_requested or run.reason or "cancelled"
        if run.state == "starting":
            _abandon(run, key, stopped=True)
        return {"ok": False, "error": "stopped", "error_th": REASONS_TH.get(key, key), "run": run.view()}
    if not called:
        # no call came up: the rest could not be asked for, and the phone says that -- not
        # "the arm is not at rest" (10 Nov: the owner read it as a handshake still going)
        _abandon(run, "no_call")
        return refuse("no_call")
    if not rested:
        # The app rests the arm before its own walks (restBeforeRun); a walk the server runs is
        # invisible to it, and a handshake's arm was out for ~20 s (review 6 Nov).
        _abandon(run, "arm_not_rest")
        return refuse("arm_not_rest")
    if _app_latched():
        # B on the robot during the rest: the latch is newer than the check above
        _abandon(run, "stop_latched")
        return refuse("stop_latched")
    run.launching = True
    launching = asyncio.ensure_future(_launch(run, steps, dock, on_end))
    try:
        launched = await asyncio.shield(launching)
    except asyncio.CancelledError:
        # Whoever asked has gone (a roam cancelled by a stop). Cancelling the spawn could leave
        # a walker running that nobody reads and nobody can stop: it starts, and is told to stop.
        if not run.stop_requested:
            run.stop_requested = "cancelled"
            turnlog.record("event_walk", phase="stop", spot=spot, run_id=run.id, reason="cancelled")
        raise
    if launched is not None:
        return launched
    if GATE_WAIT_S > 0:
        # She must not say she is going until the walker says it is (Q too low, the
        # board busy: refused in its first second, the scene already announced).
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(run.gate.wait(), GATE_WAIT_S)
        if run.state in ("stopped", "failed") and not run.saw_started:
            return {"ok": False, "error": "gate", "error_th": run.view()["reason_th"] or REASONS_TH["gate"],
                    "run": run.view()}
    return {"ok": True, "run": run.view()}


async def _launch(run: Run, steps: list[dict], dock: dict | None, on_end: OnEnd | None) -> dict | None:
    """The walker and what watches it, or the refusal. A stop that came while this was
    under way is carried out as soon as there is a process to stop.

    A stop is checked before this point, not here (review 7 Nov): `start` asks again after
    the arm's rest, and a stop during the rest never gets this far. One landing in the loop
    turn between that check and the spawn (`launching`) is signalled the moment the walker
    exists -- SIGTERM before board_walk has even imported -- because its callers learn of a
    stop from the run's END (face_approach turns itself off on a person's stop there;
    test_every_existing_stop_path_stops_it_and_turns_it_off lands exactly in this turn)."""
    try:
        run.proc = await _spawn(_route_file(steps, dock))
    except Exception as exc:                             # noqa: BLE001
        logger.exception("event walk: board_walk did not start")
        run.state, run.reason, run.ended = "failed", "launch_failed", time.monotonic()
        run.tail.append(type(exc).__name__)
        turnlog.record("event_walk", phase="end", spot=run.spot, state="failed", reason="launch_failed")
        return {"ok": False, "error": "launch_failed", "error_th": REASONS_TH["launch_failed"],
                "run": run.view()}
    run.state = "walking"
    _note_active(run)
    _catwalk(run, "start")
    turnlog.record("event_walk", phase="start", spot=run.spot, run_id=run.id, steps=len(steps))
    logger.info("event walk %s to %s: %d steps", run.id, run.spot, len(steps))
    run.tasks.append(asyncio.create_task(_pump(run, on_end)))
    run.tasks.append(asyncio.create_task(_watch(run)))
    if run.stop_requested:
        _signal(run.proc)
    return None


def _on_line(run: Run, doc: dict) -> None:
    """One `@@` line of board_walk."""
    if doc.get("started"):
        run.saw_started = True
        run.gate.set()
        if isinstance(doc.get("at"), list):
            run.start_at = doc["at"]
    if isinstance(doc.get("stopped_at"), list):
        run.last_at = doc["stopped_at"]
    if isinstance(doc.get("step"), int):
        run.done = max(run.done, doc["step"])
        _note_active(run)
    if "moving" in doc:
        run.held_at = None
    if isinstance(doc.get("forward_line"), dict):
        run.forward_line = doc["forward_line"]
        _note_forward_line(doc["forward_line"])
    if doc.get("docked") is not None and any("back_to" in s for s in run.steps):
        forget_back_line("docked")
    if doc.get("met") is not None:
        run.met = {k: doc.get(k) for k in ("met", "gap_m", "after_m")}
    if doc.get("line_lost") is not None:
        run.line_lost = True
        forget_back_line("lost")                          # a pose jump, or a way back from off it
    if "held" in doc:
        first = run.held_at is None
        run.held_at = doc.get("at") or [None, None]
        if doc.get("at"):
            run.last_at = doc["at"]
        if first:
            turnlog.record("event_walk", phase="held", spot=run.spot, run_id=run.id, at=run.held_at)
            if run.on_event is not None:
                run.tasks.append(asyncio.create_task(_tell(run, "held")))


async def _tell(run: Run, what: str) -> None:
    try:
        await run.on_event(run, what)
    except Exception:                                    # noqa: BLE001
        logger.exception("event walk %s: the %s callback failed", run.id, what)


async def _pump(run: Run, on_end: OnEnd | None) -> None:
    proc = run.proc
    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            run.last_line = time.monotonic()
            if text.startswith("@@ "):
                with contextlib.suppress(ValueError):
                    _on_line(run, json.loads(text[3:]))
            elif text:
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
    run.held_at = None
    _clear_active()
    if run.stop_requested:
        run.state, run.reason = "stopped", run.stop_requested
    else:
        run.state = _EXIT.get(code, "failed")
        if run.state == "failed" and not run.reason:
            run.reason = "exit %s" % code
    run.gate.set()
    _catwalk(run, "end")
    if run.saw_started and not all(_line_step(s) for s in run.steps):
        # it went by the map from where the way back ended: the robot is off that line now (a
        # walk refused at its own gate never moved, and keeps it -- on floor 2 the map's way home
        # is the wrong floor's). ANY map step does that (review 22 Nov: card 3 is a MoveTo, a turn and
        # a walk ahead -- stopped before its walk ahead, the old line was kept and "กลับแท่น" offered it)
        forget_back_line("walked")
    turnlog.record("event_walk", phase="end", spot=run.spot, run_id=run.id, state=run.state,
                   reason=run.reason, exit_code=code, seconds=round(run.ended - run.started, 1))
    logger.info("event walk %s to %s: %s (%s)", run.id, run.spot, run.state, run.reason)
    if on_end is not None:
        try:
            await on_end(run)
        except Exception:                                # noqa: BLE001
            logger.exception("event walk %s: the end callback failed", run.id)


def _catwalk(run: Run, phase: str) -> None:
    """A spot marked `"catwalk": true` in the event file (owner 25 Nov 01:57, card 3: "หุ่นก็ไปลูกโลกระหว่างก็เปิดซาวน์ไป"):
    the hall's catwalk music while its walk goes, its end hit when it arrives, off when it ends short. Card 1's own walks
    play theirs from app/event_tour.py and their spots carry no flag. Never a reason for a walk to fail."""
    try:
        from app import event_host, face_mirror

        spot = ((event_host.load() or {}).get("spots") or {}).get(run.spot) or {}
        if spot.get("catwalk") is not True:
            return
        if phase == "start":
            if isinstance(spot.get("show"), dict) and spot["show"].get("first"):
                return                                   # her line first, the music after it (event_host._walk_line)
            face_mirror.catwalk("walk", track="walk")
        else:
            face_mirror.catwalk("hit" if run.state == "arrived" else "off")
    except Exception:                                    # noqa: BLE001
        logger.exception("event walk %s: the catwalk music", run.id)


async def _watch(run: Run) -> None:
    """The dead man's switches: the staff page's polls and the robot face. Each stops THIS run
    (`_stop_run`), never whatever STATE["run"] is by then (review 7 Nov: a watcher of a replaced
    walk could stop the walk that replaced it)."""
    while run.state in ("starting", "walking"):
        await asyncio.sleep(0.5)
        if run.state not in ("starting", "walking"):
            return
        beat = STATE["heartbeat"]
        if beat is None or time.monotonic() - beat > settings.event_walk_heartbeat_s:
            await _stop_run(run, "control_lost")
            return
        if time.monotonic() - run.last_line > NO_OUTPUT_S:
            await _stop_run(run, "hung")
            return
        if _app_latched():
            # B or the operator screen on the robot (review 6 Nov, critical): the app DELETEs the board's
            # action, and board_walk read that as a leg ended short and sent MoveTo again ~2 s later --
            # she paused, then walked on. The app's latch is the one sign the server gets.
            await _stop_run(run, "app_stop")
            return
        if _faces():
            STATE["face_lost_at"] = None
        else:
            lost = STATE["face_lost_at"] = STATE["face_lost_at"] or time.monotonic()
            if time.monotonic() - lost > FACE_GRACE_S:
                await _stop_run(run, "face_gone")
                return
        _keep_call()


#: How long the arm's rest (group 99) may take before the walk is refused. The board answers
#: group 99's #AGF in ~1.5 s from rest (10 Oct); from a handshake it is the vendor's move.
ARM_REST_WAIT_S = 12.0
#: The app refuses ANY gesture, the rest too, for 3 s after the last one ENDED (BodyGestures.COOLDOWN_MS,
#: counted from finish()). 23 Nov 20:18:39 on the robot: card 1's pageant said done and the walk to the
#: counter asked for the rest in the same second -> "cooldown" -> arm_not_rest, the card ended 1 m out of
#: the dock (and every leg after a handshake would have done the same). A `cooldown` answer is asked again
#: every REST_COOLDOWN_EVERY_S until REST_COOLDOWN_S -- the app's 3 s and a margin. Only `cooldown`: `busy`
#: (a handshake or its slow lowering still going) is still no walk.
REST_COOLDOWN_S = 4.0
REST_COOLDOWN_EVERY_S = 0.5
#: ... and `busy` for this long WHEN the arm's last move was her talking arm (rehearsal 5, 25 Nov 03:22:19: the app holds
#: the last speech gesture 1.5 s, then its own group 99 -- that answered the walk's rest `busy` in that very second, done
#: a moment later, and card 3 had been refused). A handshake (or anything else) busy is still asked once and no walk:
#: someone's hand may be in hers (test_busy_is_not_asked_out_like_the_cooldown).
REST_BUSY_S = 5.0


def _talking_arm_busy(ack: dict) -> bool:
    """`busy` from her talking arm's own way down -- the one busy worth asking again (REST_BUSY_S)."""
    if ack.get("reason") != "busy":
        return False
    try:
        from app import robot_body
        from app import robot_contracts as rc

        return robot_body.STATE.get("last_action") in rc.SPEECH_BODY_ACTIONS
    except Exception:                                    # noqa: BLE001
        return False

#: No call on the robot face: rung, and waited for this long (face_approach's LINE_WAIT_S). A call
#: came up in 2-3 s on the robot on 10 Nov (a tap at 19:43:46 -> event_call at 19:43:48).
CALL_WAIT_S = 8.0


def _call_face():
    """The robot face's CALL socket, newest first -- never its status link, which carries a stop
    and nothing else (`robot_face_link._send`: anything else is refused `not_robot_face`). Both are
    claimed, so `current_face()` can be the status link while a call is up (the link reconnecting
    after the call opened, a server restart) and the arm's rest was refused with a call standing."""
    try:
        from app import robot_face_link

        for face in robot_face_link.live_faces():
            if not robot_face_link.is_status_link(face):
                return face
    except Exception:                                    # noqa: BLE001 -- unknown = none
        pass
    return None


def _call_ready() -> bool:
    """A call the arm's rest, the wave and her line can go over: its socket, and its voice up."""
    face = _call_face()
    return face is not None and getattr(face, "provider", None) is not None


async def _call_up(run: Run, autonomous: bool = False) -> bool:
    """A call on the robot face before the arm's rest is asked for. Owner 10 Nov: the debut pressed
    after the mic was tapped "Muted" and that call had hung up idle (19:54:17) -- no standing line
    rings under a held mic -- was refused "arm_not_rest", nothing moved; then "เดินแบบไม่ต้องเปิดสาย
    ไม่ได้หรอ" -> "เปิดสายแต่ mute ไมค์ได้ไหม". The ring is the camera's own bell (`wake.summon`): a
    held mic lets it through (only the standing line is skipped, `wake.HOLD_SKIPS`) and the call
    opens MUTED -- the hold is the page's, this never touches it. The recorded line and the wave
    need no mic. A stop while it dials: no walk (`start` reads the run). False = no call.

    `autonomous` (a roaming leg, a walk up to somebody) under a held mic rings nothing (review
    10 Nov): nobody pressed for it and nobody can stop it by voice -- face_approach's rule
    (`_line_up` refuses "mic_held"). Before this, such a leg with no call was refused at the rest
    and a roam gave up after two; it still is. The call is held from here (`_keep_call`): one
    already up may be a summoned line near its 30 s, and the rest below can take ~15 s."""
    from app import wake

    if _call_ready():
        _keep_call()
        return True
    if autonomous and wake.mic_held():
        turnlog.record("event_walk", phase="call", up=False, rang=0, why="mic_held")
        return False
    return await call_for(lambda: run.state == "starting" and not run.stop_requested)


async def call_for(still, *, reason: str = "event_walk") -> bool:
    """Ring the camera's bell for a staff press (`event_*`: the page answers it under a held mic,
    MUTED -- STAFF_RING) and wait CALL_WAIT_S for the call while `still()`; held once up
    (`_keep_call`). False = no call. `_call_up`'s road, and the debut's hand before her line where
    she stands (event_host._say_line, review 11 Nov: the start needs the call, and her line used to
    ring it before the start went)."""
    from app import wake

    if _call_ready():
        _keep_call()
        return True
    t0 = time.monotonic()
    rang = await wake.summon(reason=reason)
    if not rang:
        # no page in standby heard it (closed, reloading): nobody will dial -- events.announce's
        # "empty house" answer, not CALL_WAIT_S of waiting for nothing
        turnlog.record("event_walk", phase="call", up=False, rang=0, **({} if reason == "event_walk" else {"why": reason}))
        return False
    while time.monotonic() - t0 < CALL_WAIT_S:
        await asyncio.sleep(0.2)
        if not still():
            return False
        if _call_ready():
            _keep_call()
            turnlog.record("event_walk", phase="call", up=True, rang=rang,
                           waited_s=round(time.monotonic() - t0, 1), **({} if reason == "event_walk" else {"why": reason}))
            return True
    turnlog.record("event_walk", phase="call", up=False, rang=rang, waited_s=round(CALL_WAIT_S, 1),
                   **({} if reason == "event_walk" else {"why": reason}))
    return False


def _keep_call() -> None:
    """A call rung for a walk is a summoned line nobody greeted: SUMMONED_ABANDON_S (30 s) ends it,
    and a walk is longer. Held while she walks (face_approach's `_keep_line`); after, the ordinary
    rules end it."""
    keep = getattr(_call_face(), "keep_line", None)
    if callable(keep):
        try:
            keep()
        except Exception:                                # noqa: BLE001 -- a hang-up later, not a stop
            logger.debug("event walk: keep_line failed", exc_info=True)


async def _arm_rested() -> bool:
    """Ask the app for the arm's rest and wait for it to say it is done -- what the app does before its
    own walks (BodyController.restBeforeRun). From rest, group 99 moves nothing. Refused (a handshake or
    its slow lowering still going: busy; the gesture switch off; latched) or not done in time: no walk.
    EVENT_WALK_ARM_REST=false skips it (the tests' default; the robot keeps it on).

    The walk this rest is for = the run "starting" when it is asked (start() takes the run first); gone (stopped,
    replaced) = no more asking, before every `cooldown` re-ask and in the wait for done -- review 23 Nov: a walk
    stopped or replaced during the re-asks went on sending rests for ~4.5 s, an arm move after the stop, and the
    walk that replaced it got `request_pending` / `busy` for its own rest (one pending body request per call).
    No run starting (rest_arm ahead of a walk): nothing to watch."""
    if not settings.event_walk_arm_rest:
        return True
    mine = STATE.get("run")
    mine = mine if mine is not None and mine.state == "starting" else None

    def still() -> bool:
        return mine is None or (STATE.get("run") is mine and mine.state == "starting" and not mine.stop_requested)

    try:
        from app import event_pageant, robot_body, robot_face_link

        # the debut's pageant still up (its hand, or its way down): the rest would be refused busy.
        # It comes down first -- a walk is the reason that exception ends (owner 9 Nov). Not a gate
        # of its own: the app answers the rest below (busy = no walk), never a belief of the server's
        if not await event_pageant.end("walk", wait=True):
            turnlog.record("event_walk", phase="arm", pageant="not_confirmed_down")
        if not still():
            # stopped/replaced while the pageant came down (second review 23 Nov): no rest for a walk that is gone --
            # its rest and the replacing walk's would race for the call's one pending body request
            turnlog.record("event_walk", phase="arm", rested=False, reason="walk_gone")
            return False
        face = _call_face()                              # the call, not the stop-only status link
        if face is None:
            turnlog.record("event_walk", phase="arm", rested=False, reason="no_call")
            return False
        asked = time.time()
        ack = await robot_face_link.request(face, "body", {"action": "rest"}, settings.robot_body_ack_timeout_s)
        cool_until = time.monotonic() + REST_COOLDOWN_S
        busy_until = time.monotonic() + REST_BUSY_S
        if _talking_arm_busy(ack):
            cool_until = max(cool_until, time.monotonic() + REST_COOLDOWN_S)
        while not ack.get("accepted") and (
                (ack.get("reason") == "cooldown" and time.monotonic() < cool_until)
                or (_talking_arm_busy(ack) and time.monotonic() < busy_until)):
            # the app's timer after the gesture that just ended (REST_COOLDOWN_S): free again in moments
            await asyncio.sleep(REST_COOLDOWN_EVERY_S)
            if not still():
                turnlog.record("event_walk", phase="arm", rested=False, reason="walk_gone")
                return False
            face = _call_face()                          # a call re-dialled meanwhile: ask over the new one
            if face is None:
                turnlog.record("event_walk", phase="arm", rested=False, reason="no_call")
                return False
            asked = time.time()
            ack = await robot_face_link.request(face, "body", {"action": "rest"}, settings.robot_body_ack_timeout_s)
            if _talking_arm_busy(ack):
                # the app's 3 s cooldown runs from its group 99's END, not from our first ask (condo-voice-75's check,
                # 25 Nov: her talking arm's rest still busy 1.2+ s after the ask -> the cooldown outlasted a fixed
                # window and the walk was refused "cooldown"): the window starts again from each busy answer
                cool_until = max(cool_until, time.monotonic() + REST_COOLDOWN_S)
        if not ack.get("accepted"):
            turnlog.record("event_walk", phase="arm", rested=False, reason=ack.get("reason"))
            return False
        deadline = time.monotonic() + ARM_REST_WAIT_S
        while time.monotonic() < deadline:
            if not still():
                turnlog.record("event_walk", phase="arm", rested=False, reason="walk_gone")
                return False
            st = robot_body.STATE
            if st.get("last_action") == "rest" and (st.get("at") or 0.0) >= asked:
                if st.get("state") == "done":
                    return True
                if st.get("state") in ("failed", "stopped"):
                    break
            await asyncio.sleep(0.1)
        turnlog.record("event_walk", phase="arm", rested=False, reason="not_done")
        return False
    except Exception:                                    # noqa: BLE001 -- not knowing is not rested
        logger.exception("event walk: the arm's rest could not be asked for")
        return False


#: A rest asked before the walk (`rested_at`) holds this long at most.
REST_HOLDS_S = 60.0


def _rest_holds(rested_at: float | None, by: str = "rest") -> bool:
    """The rest asked at `rested_at` is still the arm's state: the app's latest row for the arm is that rest,
    done (robot_body.STATE keeps only the latest row of any gesture: a speech beat, a pageant, a handshake
    since would be the latest), no pageant up, and not older than REST_HOLDS_S. EVENT_WALK_ARM_REST off: no
    rest is asked anyway."""
    if rested_at is None or not settings.event_walk_arm_rest:
        return False
    try:
        from app import event_pageant, robot_body

        st = robot_body.STATE
        return (st.get("last_action") == ("handshake" if by == "handshake" else "rest") and st.get("state") == "done"
                and float(st.get("at") or 0.0) >= float(rested_at) and time.time() - float(rested_at) <= REST_HOLDS_S
                and not event_pageant.active())
    except Exception:                                    # noqa: BLE001 -- not knowing: rest it again
        return False


async def rest_arm() -> bool:
    """The arm's rest, asked now -- the walk's own (`_arm_rested`), for a caller that rests it ahead of a walk
    (`start(rested_at=...)`). True = the app said done (or EVENT_WALK_ARM_REST is off)."""
    return await _arm_rested()


def _app_latched() -> bool:
    """The robot face says its 60 s stop latch is on (greeter._app_latched): B, the operator screen,
    a stop on the robot. Unreadable = not latched, as for an older APK that never sends it."""
    try:
        from app import greeter, robot_face_link

        return greeter._app_latched(robot_face_link)
    except Exception:                                    # noqa: BLE001
        return False


def _signal(proc) -> None:
    with contextlib.suppress(ProcessLookupError, OSError):
        if sys.platform == "win32":
            proc.terminate()
        else:
            proc.send_signal(signal.SIGTERM)


async def stop(reason: str) -> bool:
    """Stop the walk under way, if any. Never raises, never waits on anything
    but the walker itself (bounded): a stop must not be held up. A person's stop
    is counted whether or not anything walks (`stops()`)."""
    if reason in PERSON_STOPS:
        STATE["stops"] = stops() + 1
        STATE["last_stop"] = reason
    run = STATE["run"]
    if run is None or run.state not in ("starting", "walking"):
        return False
    return await _stop_run(run, reason)


async def unless_stopped(seen: int, result: dict) -> dict:
    """For a press that awaited things before `start` (event_host.go_spot / start_scene /
    go_stop): a person's stop since `seen` and the press says it walks = that walk is stopped
    now, and the press answers "stopped". Those now pass `stops_seen` on to `start`, which then
    refuses before any walker exists; this stays as the backstop for a stop that lands after
    `start`'s last check (the walker is a second old at most)."""
    if stops() == seen or not isinstance(result, dict):
        return result
    # go_spot / start_scene answer {"walk": start's answer or None, ...}; `start` its own
    walk = result.get("walk") if "walk" in result else result
    if not isinstance(walk, dict) or not walk.get("ok"):
        return result                                    # nothing set out
    reason = STATE.get("last_stop") or "staff"
    run_id = (walk.get("run") or {}).get("id")
    run = STATE["run"]
    if run is not None and (run_id is None or run.id == run_id):
        await _stop_run(run, reason)
    turnlog.record("event_walk", phase="stopped_after", run_id=run_id, reason=reason)
    out = {"ok": False, "error": "stopped", "error_th": REASONS_TH.get(reason, reason)}
    if walk is result:
        return {**result, **out}
    # a scene switched all the same (its mode, its line): only its walk is refused
    return {**result, "walk": {**walk, **out}, **(out if "scene" not in result else {})}


async def _stop_run(run: Run, reason: str) -> bool:
    if run.state not in ("starting", "walking"):
        return False
    if not run.stop_requested:
        run.stop_requested = reason
        turnlog.record("event_walk", phase="stop", spot=run.spot, run_id=run.id, reason=reason)
        logger.warning("event walk %s: stop (%s)", run.id, reason)
    if not run.launching:
        # still checking (the arm's rest): it never sets out, and the stop is over now -- not
        # after SPAWN_WAIT_S of waiting for a walker that `start` will never spawn
        _abandon(run, run.stop_requested, stopped=True)
        return True
    # Spawning: no process yet, `_launch` reads the note above the moment it does. "หยุด"
    # in that moment used to be dropped here, and the walker set out (seen in a flaky test).
    for _ in range(int(SPAWN_WAIT_S / 0.02)):
        if run.proc is not None or run.state not in ("starting", "walking"):
            break
        await asyncio.sleep(0.02)
    proc = run.proc
    if proc is None:
        return True                                      # spawn failed, or `_launch` signals it when it lands
    _signal(proc)
    try:
        await asyncio.wait_for(proc.wait(), KILL_AFTER_S)
    except (asyncio.TimeoutError, Exception):            # noqa: BLE001
        run.killed = True                                # no closing line: how far it went is not known
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
    """For the other stop paths (voice, desk): a no-op without a walk, but "หยุด" with her still
    checking whether to set out is a stop all the same. Walking up to people turns off first."""
    try:
        from app import face_approach

        face_approach._turn_off(reason)
    except Exception:                                    # noqa: BLE001 -- the stop below still goes
        logger.exception("event walk stop (%s): walking up to people not turned off", reason)
    try:
        from app import event_roam

        # Both, always (CLAUDE.md): standing at a stop there is no walk to stop, and the roam
        # set out for its next stop once its wait ran out (review 6 Nov).
        await event_roam.stop(reason)
    except Exception:                                    # noqa: BLE001
        logger.exception("event walk stop (%s): roaming not turned off", reason)
    try:
        await stop(reason)
    except Exception:                                    # noqa: BLE001
        logger.exception("event walk stop (%s) failed", reason)


def snapshot() -> dict:
    run = STATE["run"]
    return {"enabled": settings.event_walk_enabled, "walking": walking(),
            "run": run.view() if run is not None else None, "health": health_view(), "back": back_view(),
            "pose_busy": pose_busy()}
