"""Pictures of every walk, for judging it afterwards (WALK_FRAMES).

Owner, 31 Oct 2026, after the floor-1 walks: "ลองให้เดินแล้วดูภาพประกอบด้วย
จะได้ประเมินถูก". Until now a walk left only the board's numbers -- poses, a
quality, why a leg stopped -- and "ชนเก้าอี้นิดหน่อย ฝั่งขวามีที่มากกว่า" (28 Oct)
came from the owner standing there. This keeps, while the robot walks:

* the head camera's newest frame (the face page's own stream, `robot_camera.
  feed.latest()` -- a peek: the greeter's `read()` still gets every frame);
* the board's pose and localization quality;
* the lidar: the nearest return per arc (`robot_chassis.clearance`) and a
  thinned scan, so a picture of a chair can be read next to how far the
  lidar put it -- and a chair the lidar never saw (below the scan plane,
  28 Oct) shows up as a picture with nothing near in the scan;
* what the walk itself said at that moment (`walk`: the event host's spot,
  state and board_walk's last step line, or the app's run state and place)
  and where the head was told to look (`head`, degrees) -- 31 Oct, round 2, so "where
  did it stop, and why" is on the picture, and a turned head is not read as
  what was ahead.

once every WALK_FRAMES_EVERY_S, into data/robot/walk-frames/<run>/ on the
robot: NNNN.jpg + frames.jsonl + run.json, and once per walk the board's map
as the walk began (map.bin, its explore grid) -- scripts/map_check.py lays
the walks' lidar on it to find what the map has wrong (31 Oct, round 4). scripts/walk_frames.py pulls a
run to the PC and draws a contact sheet (each picture with its time, pose,
quality and clearances, and where on the path it was taken).

"A walk" is whatever the robot says is a walk: the event host's own
(`event_walk.walking()`) or a run the app reports (`robot_face_link.
nav_activity()` active/stale -- the panel, a PC `robot_python run` the app's
poller sees) -- or, failing both, the base itself moving (`Motion`, the
board's pose once a second). 2 Nov: the evening's walk to map the whole floor
is a board_walk started by hand over adb, or the robot driven by hand; the
app's poller reads only drive_route's run.pid and the event host did not start
it, so without this the recorder kept nothing of exactly the walk it was made
for. Recording goes on TAIL_S past the end, so the picture of where it stopped
is there.

People walk through these pictures. They are the turn logs' kind of thing:
kept WALK_FRAMES_KEEP_DAYS (age from the folder's name, not mtime -- the
logs' rule), capped at WALK_FRAMES_MAX_MB, never committed (data/robot/ is
gitignored), never deployed over (robot_server only deletes what its own
MANIFEST listed). Off by default: nothing is written anywhere.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import math
import re
import shutil
import time
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger("condo_voice.walk_frames")

ROOT = Path(__file__).resolve().parent.parent
FRAMES_DIR = ROOT / "data" / "robot" / "walk-frames"
POLL_S = 0.5
#: Keep recording this long after the walk ended (where it stopped).
TAIL_S = 3.0
#: A frame older than this is the camera having stopped, not a picture of now.
FRESH_S = 2.0
#: One run's ceiling (an hour at 1 fps): a walk that never reports its end
#: must not fill the robot's storage on its own.
MAX_PER_RUN = 3600
#: ~1 degree apart: enough for the sheet's lidar strip under each picture
#: to put a chair leg at its column (180 was 2 degrees: a leg at 1 m is ~6).
SCAN_POINTS = 360
BOARD_TIMEOUT_S = 1.0
#: The map is ~0.4 MB from the board; read once per walk (board_walk's MapFit
#: reads the same grid once per run).
MAP_TIMEOUT_S = 5.0
NAME = re.compile(r"^(\d{8}-\d{6})-")
#: The base moving with no walk reported (`Motion`): the board's pose read
#: this often while nothing else says a walk is on.
MOTION_POLL_S = 1.0
#: One poll counts as moving past this. On the dock the pose jitters by
#: millimetres; board_walk's docking reverse is ~5 cm/s.
MOTION_M = 0.03
MOTION_DEG = 3.0
#: Moving polls in a row before a run opens: a single jump is the board
#: relocalizing, not the robot leaving.
MOTION_START_POLLS = 2
#: Still this long = that walk ended. board_walk waits 8 s at the multimedia
#: door and pauses at points; one walk must stay one run.
MOTION_STILL_S = 15.0

_task: dict[str, Any] = {"task": None}


class Motion:
    """Is the base itself moving, from consecutive board poses. No I/O."""

    def __init__(self) -> None:
        self.pose: tuple[float, float, float] | None = None
        self.streak = 0
        self.until = -1.0

    def forget(self) -> None:
        """Another source is reporting the walk: the next pose starts afresh
        (a pose from before a long gap is not one poll's movement)."""
        self.pose = None
        self.streak = 0

    def update(self, pose: Any, now: float) -> None:
        try:
            here = (float(pose["x"]), float(pose["y"]), float(pose.get("yaw") or 0.0))
        except (KeyError, TypeError, ValueError, AttributeError):
            self.forget()                     # no answer from the board: no claim either way
            return
        before, self.pose = self.pose, here
        if before is None:
            return
        turned = abs((math.degrees(here[2] - before[2]) + 180.0) % 360.0 - 180.0)
        moved = math.hypot(here[0] - before[0], here[1] - before[1]) >= MOTION_M or turned >= MOTION_DEG
        self.streak = self.streak + 1 if moved else 0
        if self.streak >= MOTION_START_POLLS or (moved and self.active(now)):
            self.until = now + MOTION_STILL_S

    def active(self, now: float) -> bool:
        return now < self.until

    def state(self, now: float) -> dict | None:
        """The record's `walk` for a run only the motion opened: where it
        paused shows on the sheet as "-> still"."""
        if not self.active(now):
            return None
        return {"by": "motion", "state": "moving" if self.streak else "still"}


_motion = Motion()


async def _poll_motion(now: float) -> None:
    from app import robot_chassis

    if not robot_chassis.configured():
        return
    try:
        pose = await asyncio.wait_for(robot_chassis.pose(), BOARD_TIMEOUT_S)
    except Exception:  # noqa: BLE001 -- a board that does not answer is not a walk
        logger.debug("walk frames: no pose for the motion check", exc_info=True)
        pose = None
    _motion.update(pose, now)


class Run:
    def __init__(self, label: str, folder: Path) -> None:
        self.label = label
        self.folder = folder
        self.started = time.monotonic()
        self.started_at = _dt.datetime.now().isoformat(timespec="seconds")
        self.last_walk = self.started
        self.next_at = self.started
        self.n = 0
        self.pictures = 0
        self.no_camera = 0
        self.last_seq = -1
        self.last_state: dict | None = None
        self.map: str | None = None
        self.map_task: asyncio.Task | None = None


def _label() -> str | None:
    """What is walking right now, or None."""
    try:
        from app import event_walk

        if event_walk.walking():
            run = event_walk.current()
            return "event-" + (getattr(run, "spot", "") or "walk")
    except Exception:  # noqa: BLE001 -- a probe, never the reason a walk is missed
        logger.debug("walk frames: event_walk probe failed", exc_info=True)
    try:
        from app import robot_face_link

        if robot_face_link.nav_activity() in ("active", "stale"):
            return "app"
    except Exception:  # noqa: BLE001
        logger.debug("walk frames: nav probe failed", exc_info=True)
    return None


_TEXT_KEYS = ("state", "place", "reason", "step", "phase")


def _short(value, limit: int = 120):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return value[:limit]
    return None


def _walk_state() -> dict | None:
    """What the walk itself says right now -- which step, which place, why it
    stopped -- so a picture can be read against it: the event host's run
    (its spot, state and board_walk's last line: positions and step labels,
    never anything a guest said), else the app's run as its face reported it."""
    try:
        from app import event_walk

        run = event_walk.current()
        if run is not None and (run.ended is None or time.monotonic() - run.ended < TAIL_S + 2):
            view = run.view()
            tail = view.get("tail") or []
            return {"by": "event", "spot": _short(view.get("spot"), 60), "state": _short(view.get("state"), 30),
                    "reason": _short(view.get("reason"), 60), "step": _short(tail[-1]) if tail else None}
    except Exception:  # noqa: BLE001 -- context for a picture, never the reason one is missing
        logger.debug("walk frames: event state unreadable", exc_info=True)
    try:
        from app import robot_face_link

        face = robot_face_link.walking_face()
        status = robot_face_link.nav_status(face) if face is not None else None
        if isinstance(status, dict):
            out = {"by": "app"}
            for key in _TEXT_KEYS:
                if key in status:
                    out[key] = _short(status.get(key), 60)
            return out
    except Exception:  # noqa: BLE001
        logger.debug("walk frames: app state unreadable", exc_info=True)
    return None


def _head() -> list[float] | None:
    """Where the head was told to look (degrees, + = left / down): a picture
    taken with the head turned is not a picture of what is ahead."""
    try:
        from app import head_follow

        pan, tilt = head_follow.offset_deg()
        return [round(pan, 1), round(tilt, 1)]
    except Exception:  # noqa: BLE001
        return None


def _camera() -> dict | None:
    """How a lidar bearing lands in these pictures (the sheet's lidar strip):
    the camera's assumed field of view and, once the head's lidar check
    holds (app/lidar_people.py), the measured line between the two."""
    try:
        from app import head_follow

        return head_follow.camera_mapping()
    except Exception:  # noqa: BLE001
        return None


def _safe(label: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "_", label.lower())[:40] or "walk"


def _stamp_of(name: str) -> _dt.datetime | None:
    m = NAME.match(name)
    if not m:
        return None
    try:
        return _dt.datetime.strptime(m.group(1), "%Y%m%d-%H%M%S")
    except ValueError:
        return None


def _size(folder: Path) -> int:
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())


def prune(root: Path = FRAMES_DIR, *, keep_days: int | None = None, max_mb: int | None = None,
          now: _dt.datetime | None = None) -> list[str]:
    """Delete runs older than keep_days (by the folder's NAME), then the oldest
    until the rest fit in max_mb. Folders not named like a run are left alone."""
    keep_days = settings.walk_frames_keep_days if keep_days is None else keep_days
    max_mb = settings.walk_frames_max_mb if max_mb is None else max_mb
    now = now or _dt.datetime.now()
    if not root.is_dir():
        return []
    runs = sorted((p for p in root.iterdir() if p.is_dir() and _stamp_of(p.name)), key=lambda p: p.name)
    gone: list[str] = []
    for p in list(runs):
        if keep_days > 0 and now - _stamp_of(p.name) > _dt.timedelta(days=keep_days):
            shutil.rmtree(p, ignore_errors=True)
            gone.append(p.name)
            runs.remove(p)
    if max_mb > 0:
        sizes = {p: _size(p) for p in runs}
        total = sum(sizes.values())
        while runs and total > max_mb * 1_000_000:
            p = runs.pop(0)
            total -= sizes[p]
            shutil.rmtree(p, ignore_errors=True)
            gone.append(p.name)
    return gone


def _open(label: str) -> Run:
    gone = prune()
    if gone:
        logger.info("walk frames: removed %d old run(s)", len(gone))
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = FRAMES_DIR / ("%s-%s" % (stamp, _safe(label)))
    folder.mkdir(parents=True, exist_ok=True)
    logger.info("walk frames: recording %s", folder.name)
    return Run(label, folder)


def _close(run: Run) -> None:
    doc = {"label": run.label, "started": run.started_at,
           "ended": _dt.datetime.now().isoformat(timespec="seconds"),
           "seconds": round(time.monotonic() - run.started, 1),
           "records": run.n, "pictures": run.pictures, "no_camera": run.no_camera,
           "end": run.last_state, "camera": _camera(), "map": run.map}
    try:
        (run.folder / "run.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        logger.warning("walk frames: could not write run.json for %s", run.folder.name, exc_info=True)
    logger.info("walk frames: %s done (%d pictures, %d without a camera frame)",
                run.folder.name, run.pictures, run.no_camera)


async def _board() -> dict:
    """Pose, quality and lidar from the board; {} for whatever did not answer."""
    from app import robot_chassis

    if not robot_chassis.configured():
        return {}
    out: dict[str, Any] = {}
    try:
        scan = await asyncio.wait_for(robot_chassis.laserscan(max_points=SCAN_POINTS), BOARD_TIMEOUT_S)
        out["pose"] = scan.get("pose")
        out["clear"] = {k: (None if v is None else round(v, 3)) for k, v in scan["clearance"].items()}
        out["scan"] = [[round(p["angle"], 3), round(p["distance"], 3)] for p in scan["points"] if p["valid"]]
    except Exception:  # noqa: BLE001 -- a missing scan is a hole in the record, not a stop
        logger.debug("walk frames: no lidar", exc_info=True)
    try:
        out["q"] = await asyncio.wait_for(robot_chassis.localization_quality(), BOARD_TIMEOUT_S)
    except Exception:  # noqa: BLE001
        logger.debug("walk frames: no quality", exc_info=True)
    return out


async def keep_map(run: Run) -> None:
    """The board's map as this walk starts, into map.bin: what the walk's
    lidar is checked against afterwards (scripts/map_check.py). Read-only,
    once; no map is a note in the log, never a reason to stop recording."""
    from app import robot_chassis

    if not robot_chassis.configured():
        return
    try:
        blob = await asyncio.wait_for(robot_chassis.request_raw("GET", robot_chassis.MAP_EXPLORE),
                                      MAP_TIMEOUT_S)
        if not blob:
            return
        robot_chassis.parse_explore_map(blob)          # a cut-off read is no map
        (run.folder / "map.bin").write_bytes(blob)
        run.map = "map.bin"
    except Exception:  # noqa: BLE001
        logger.info("walk frames: no map kept for %s", run.folder.name, exc_info=True)


async def snap(run: Run) -> dict:
    """One record: the newest camera frame (if fresh and new) + the board."""
    from app import robot_camera

    seq, jpeg, at = robot_camera.feed.latest()
    age = time.monotonic() - at if at else None
    record: dict[str, Any] = {"n": run.n, "t": _dt.datetime.now().isoformat(timespec="milliseconds"),
                              "s": round(time.monotonic() - run.started, 2), "file": None,
                              "cam_age_s": None if age is None else round(age, 2)}
    if jpeg and seq != run.last_seq and age is not None and age < FRESH_S and run.pictures < MAX_PER_RUN:
        name = "%04d.jpg" % run.n
        (run.folder / name).write_bytes(jpeg)
        record["file"] = name
        run.last_seq = seq
        run.pictures += 1
    else:
        run.no_camera += 1
    record["walk"] = _walk_state() or (_motion.state(time.monotonic()) if run.label == "moving" else None)
    if record["walk"] is not None:
        run.last_state = record["walk"]
    record["head"] = _head()
    record.update(await _board())
    with (run.folder / "frames.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    run.n += 1
    return record


async def _watch() -> None:
    run: Run | None = None
    _motion.__init__()
    next_motion = 0.0
    try:
        while True:
            now = time.monotonic()
            label = _label()
            if label:
                _motion.forget()
            else:
                if now >= next_motion:
                    next_motion = now + MOTION_POLL_S
                    await _poll_motion(now)
                if _motion.active(now):
                    label = "moving"
            if label and run is None:
                run = _open(label)
                # Beside the first frames, not before them: a slow board
                # link must not cost the pictures of the undocking.
                run.map_task = asyncio.create_task(keep_map(run))
            if run is not None:
                if label:
                    run.last_walk = now
                if not label and now - run.last_walk > TAIL_S:
                    _close(run)
                    run = None
                elif now >= run.next_at:
                    run.next_at = now + max(0.2, settings.walk_frames_every_s)
                    try:
                        await snap(run)
                    except OSError:
                        logger.warning("walk frames: could not write to %s", run.folder, exc_info=True)
            await asyncio.sleep(POLL_S)
    except asyncio.CancelledError:
        if run is not None:
            _close(run)
        raise


def start() -> None:
    """From main's startup hook. Off unless WALK_FRAMES."""
    if not settings.walk_frames:
        return
    task = _task["task"]
    if task is not None and not task.done():
        return
    logger.info("walk frames: on (every %.1f s, kept %d days, max %d MB) -> %s",
                settings.walk_frames_every_s, settings.walk_frames_keep_days,
                settings.walk_frames_max_mb, FRAMES_DIR)
    _task["task"] = asyncio.create_task(_watch())


async def stop() -> None:
    task, _task["task"] = _task["task"], None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
