"""People the lidar sees around a standing robot (LIDAR_PEOPLE), and the
check that ties the lidar to the head camera.

Owner, 31 Oct 2026 (after the head learnt to measure itself from its own
camera): "ให้ระบบหันเช็คเองเลยได้ไหม ตรวจสอบโดยเช็คเรดาห์ เลเซอร์ และภาพประกอบกัน
ทำให้แม่นยำขึ้นไหม". Two things the camera cannot do alone:

* **See a person it cannot see.** 12-13 Oct, 983 of 983 "no face in front"
  refusals had no face in the picture while people stood at the chest screen,
  under the head camera; the lidar, at knee height all round the base, sees
  their legs. A voice at the robot with no face in view used to tilt the head
  down blindly (head_follow's search); with a person on the lidar it turns
  toward THAT person -- the owner's "ได้ยินเสียงคนมาจากทางซ้ายใกล้ๆ ก็ให้หุ่นหันทัก",
  with the lidar saying where "the left" is, since the mic array cannot.
* **True degrees.** The head's sweep (head_follow.measure) is in the camera's
  own units: right for the loop, but scaled by a guessed 70 deg field of view.
  The lidar's angles are the robot's real ones. A face (camera bearing + where
  the head was turned) paired with the one person the lidar sees at the same
  moment, many times, gives `lidar = a * camera + b`: `a` is the true/assumed
  scale (so the camera's real FOV), `b` how far off straight-ahead the camera
  sits, and a fit that does NOT hold (big residuals) says the camera, the
  head's measured sign or the lidar's angle convention disagree -- which is
  why the lidar only steers the head once its pairs agree (`Check.trusted`).
  The fit takes either sign of `a`: nobody has confirmed on the robot that
  the board's laser angle grows to the robot's LEFT (robot_chassis assumes it).

How a person is found (`Tracker`): the robot stands still during a
conversation (docked or parked), so the room around it is a background --
per 2-degree bin, the farthest return over the last BG_WINDOW_S. Returns well
in front of it (CLOSER_M) within RANGE_M are something new; neighbouring
returns cluster, clusters the size of a leg or a body survive, two legs close
together are one person. A table, the dock, a chair that stays: background.
A person who stood perfectly still for longer than the window joins it (the
camera has them by then). The robot moving (pose change, a walk) throws the
background away.

Reads only (the board's laser scan), and only while HEAD_FOLLOW and
LIDAR_PEOPLE are on and the robot is not walking. No picture, no words:
positions in degrees and metres.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from dataclasses import dataclass

from app.config import settings

logger = logging.getLogger("condo_voice.lidar_people")

BIN_DEG = 2.0
BINS = int(360 / BIN_DEG)
BG_WINDOW_S = 120.0
#: A return this much nearer than the bin's background is something new
#: (lidar noise is ~2 cm; a person's legs in front of a wall are metres).
CLOSER_M = 0.25
RANGE_M = (0.25, 2.5)
#: Neighbouring returns within this range step are one object.
CLUSTER_GAP_M = 0.15
#: A leg ~0.08-0.15 m, two legs together or a body up to ~0.6 m.
WIDTH_M = (0.04, 0.8)
MIN_POINTS = 3
#: Two clusters this close (centre to centre) are one person's two legs.
PAIR_M = 0.45
#: The robot moved: its old background is somewhere else now.
MOVE_RESET_M = 0.05
MOVE_RESET_DEG = 2.0
SCAN_POINTS = 720
FRESH_S = 2.5
LOG_EVERY_S = 10.0

# ---- the camera check ----
MAX_PAIRS = 80
MIN_PAIRS = 15
#: Bearings spread at least this much (deg) -- a scale from people who all
#: stood straight ahead is no scale.
MIN_SPREAD_DEG = 5.0
#: |a| outside this is not a camera that a 70 deg guess could be off by.
SCALE_RANGE = (0.6, 1.6)
#: Median absolute residual (deg) at or under this = the two agree.
MAX_MAD_DEG = 4.0
PAIR_EVERY_S = 1.0


@dataclass(frozen=True)
class Person:
    bearing: float      # degrees, the lidar's own convention (+ = left, assumed)
    distance: float     # metres from the lidar
    width: float        # metres across
    points: int
    t: float


def _wrap_deg(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def _bin(angle_rad: float) -> int:
    return int(((math.degrees(angle_rad) % 360.0) // BIN_DEG)) % BINS


class Tracker:
    """Background-subtracted clusters from one standing robot's lidar."""

    def __init__(self) -> None:
        self.history: deque = deque()          # (t, [nearest per bin or inf])
        self.pose: tuple[float, float, float] | None = None
        self.people: list[Person] = []
        self.at = -math.inf

    def reset(self) -> None:
        self.history.clear()
        self.people = []

    def _moved(self, pose) -> bool:
        if not isinstance(pose, dict):
            return False
        try:
            here = (float(pose["x"]), float(pose["y"]), float(pose.get("yaw") or 0.0))
        except (KeyError, TypeError, ValueError):
            return False
        before, self.pose = self.pose, here
        if before is None:
            return False
        turned = abs(_wrap_deg(math.degrees(here[2] - before[2])))
        return math.hypot(here[0] - before[0], here[1] - before[1]) > MOVE_RESET_M or turned > MOVE_RESET_DEG

    def update(self, points: list[dict], pose, t: float) -> list[Person]:
        if self._moved(pose):
            self.reset()
        nearest = [math.inf] * BINS
        valid = []
        for p in points:
            if not p.get("valid"):
                continue
            d = float(p.get("distance") or 0.0)
            if d <= 0:
                continue
            a = float(p["angle"])
            b = _bin(a)
            nearest[b] = min(nearest[b], d)
            valid.append((a, d, b))
        self.history.append((t, nearest))
        while self.history and t - self.history[0][0] > BG_WINDOW_S:
            self.history.popleft()
        background = [max(scan[1][b] for scan in self.history) for b in range(BINS)]
        new = sorted(((a, d) for a, d, b in valid
                      if RANGE_M[0] <= d <= RANGE_M[1] and d < background[b] - CLOSER_M),
                     key=lambda ad: math.atan2(math.sin(ad[0]), math.cos(ad[0])))
        self.people = _people(_clusters(new), t)
        self.at = t
        return self.people


def _xy(a: float, d: float) -> tuple[float, float]:
    return d * math.cos(a), d * math.sin(a)


def _clusters(points: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    out: list[list[tuple[float, float]]] = []
    for a, d in points:
        if out:
            pa, pd = out[-1][-1]
            gap = math.dist(_xy(a, d), _xy(pa, pd))
            if gap <= CLUSTER_GAP_M + 0.02 and abs(d - pd) <= CLUSTER_GAP_M:
                out[-1].append((a, d))
                continue
        out.append([(a, d)])
    # the scan wraps at +-180 deg: the last and first may be one object
    if len(out) > 1:
        (fa, fd), (la, ld) = out[0][0], out[-1][-1]
        if math.dist(_xy(fa, fd), _xy(la, ld)) <= CLUSTER_GAP_M + 0.02:
            out[0] = out.pop() + out[0]
    return out


def _people(clusters, t: float) -> list[Person]:
    blobs = []
    for c in clusters:
        if len(c) < MIN_POINTS:
            continue
        width = math.dist(_xy(*c[0]), _xy(*c[-1]))
        if not WIDTH_M[0] <= width <= WIDTH_M[1]:
            continue
        xs, ys = zip(*(_xy(a, d) for a, d in c))
        blobs.append([sum(xs) / len(xs), sum(ys) / len(ys), width, len(c)])
    merged: list[list[float]] = []
    for blob in sorted(blobs, key=lambda b: math.hypot(b[0], b[1])):
        for m in merged:
            if math.hypot(blob[0] - m[0], blob[1] - m[1]) <= PAIR_M:
                n = m[3] + blob[3]
                m[0] = (m[0] * m[3] + blob[0] * blob[3]) / n
                m[1] = (m[1] * m[3] + blob[1] * blob[3]) / n
                m[2] = max(m[2], math.hypot(blob[0] - m[0], blob[1] - m[1]) * 2, blob[2])
                m[3] = n
                break
        else:
            merged.append(list(blob))
    people = [Person(bearing=round(math.degrees(math.atan2(y, x)), 1), distance=round(math.hypot(x, y), 2),
                     width=round(w, 2), points=int(n), t=t) for x, y, w, n in merged]
    return sorted(people, key=lambda p: p.distance)


# ---- the camera check -----------------------------------------------------------

class Check:
    """Pairs of (camera bearing in the body frame, lidar bearing) of the same
    person, and the line through them: lidar = a * camera + b."""

    def __init__(self) -> None:
        self.pairs: deque = deque(maxlen=MAX_PAIRS)   # (camera_deg, lidar_deg, head_deg)
        self.fit: dict | None = None
        self.last_at = -math.inf

    def add(self, camera_deg: float, lidar_deg: float, head_deg: float, t: float) -> bool:
        if t - self.last_at < PAIR_EVERY_S:
            return False
        self.last_at = t
        self.pairs.append((float(camera_deg), float(lidar_deg), float(head_deg)))
        return True

    def solve(self) -> dict:
        """The fit, robust to a few wrong pairs (drops residuals over 3x the
        median, once); `trusted` when it is good enough to steer by."""
        return solve(list(self.pairs))


def solve(pairs: list[tuple[float, float, float]]) -> dict:
    import numpy as np

    out = {"n": len(pairs), "a": None, "b": None, "mad_deg": None, "spread_deg": None,
           "hfov_deg": None, "head_ratio": None, "trusted": False, "why": None}
    if len(pairs) < MIN_PAIRS:
        out["why"] = "few_pairs"
        return out
    data = np.array(pairs, dtype=float)
    cam, lid, head = data[:, 0], data[:, 1], data[:, 2]
    spread = float(np.std(cam))
    out["spread_deg"] = round(spread, 1)
    if spread < MIN_SPREAD_DEG:
        out["why"] = "no_spread"
        return out
    keep = np.ones(len(cam), dtype=bool)
    for _ in range(2):
        a, b = np.polyfit(cam[keep], lid[keep], 1)
        resid = np.abs(lid - (a * cam + b))
        mad = float(np.median(resid[keep]))
        keep = resid <= max(3.0 * mad, 1.5)
    a, b = np.polyfit(cam[keep], lid[keep], 1)
    mad = float(np.median(np.abs(lid[keep] - (a * cam[keep] + b))))
    out.update(a=round(float(a), 3), b=round(float(b), 2), mad_deg=round(mad, 2), kept=int(keep.sum()))
    from app import facewatch

    if abs(a) > 0:
        # small angles: true/assumed = f_assumed/f_true
        half = math.radians(facewatch.ROBOT_CAMERA_HFOV_DEG / 2.0)
        out["hfov_deg"] = round(math.degrees(2.0 * math.atan(math.tan(half) * abs(float(a)))), 1)
    if np.std(head[keep]) >= 4.0:
        # where the head was turned, fitted on its own: the sweep's sign and
        # scale against the lidar's (1.0 = they agree; -1 = the sweep's sign is wrong)
        face = cam[keep] - head[keep]
        coef, *_ = np.linalg.lstsq(np.column_stack([face, head[keep], np.ones(int(keep.sum()))]),
                                   lid[keep], rcond=None)
        if abs(coef[0]) > 1e-6:
            out["head_ratio"] = round(float(coef[1] / coef[0]), 2)
    if out["head_ratio"] is not None and not 0.6 <= out["head_ratio"] <= 1.5:
        # the most specific diagnosis first: a wrong head sign also bends the
        # combined line (scale/residual), but this names the cause
        out["why"] = "head_sign"
    elif not SCALE_RANGE[0] <= abs(a) <= SCALE_RANGE[1]:
        out["why"] = "scale"
    elif mad > MAX_MAD_DEG:
        out["why"] = "disagree"
    else:
        out["trusted"] = True
    return out


def camera_from_lidar(fit: dict, lidar_deg: float) -> float | None:
    """A lidar bearing in the head camera's units, body frame (what the head
    turns by); None without a trusted fit."""
    if not fit or not fit.get("trusted") or not fit.get("a"):
        return None
    return (float(lidar_deg) - float(fit["b"])) / float(fit["a"])


# ---- the poller -------------------------------------------------------------------

STATE: dict = {"tracker": Tracker(), "check": Check(), "task": None, "logged_at": -math.inf}


def reset() -> None:
    STATE.update(tracker=Tracker(), check=Check(), logged_at=-math.inf)


def enabled() -> bool:
    return bool(settings.head_follow and settings.lidar_people)


def people(now: float | None = None, max_age: float = FRESH_S) -> list[Person]:
    tracker = STATE["tracker"]
    now = time.monotonic() if now is None else now
    if now - tracker.at > max_age:
        return []
    return list(tracker.people)


def check() -> Check:
    return STATE["check"]


def _walking() -> bool:
    try:
        from app import event_walk, robot_face_link

        return bool(event_walk.walking()) or robot_face_link.nav_activity() in ("active", "stale")
    except Exception:  # noqa: BLE001
        return False


async def poll_once(now: float | None = None) -> list[Person]:
    from app import robot_chassis

    tracker = STATE["tracker"]
    if _walking():
        tracker.reset()
        tracker.pose = None
        return []
    scan = await asyncio.wait_for(robot_chassis.laserscan(max_points=SCAN_POINTS), 1.5)
    now = time.monotonic() if now is None else now
    found = tracker.update(scan.get("points") or [], scan.get("pose"), now)
    if now - STATE["logged_at"] >= LOG_EVERY_S and found:
        STATE["logged_at"] = now
        _record(n=len(found), nearest_deg=found[0].bearing, nearest_m=found[0].distance)
    return found


async def _watch() -> None:
    from app import robot_chassis

    failed_at = -math.inf
    while True:
        try:
            if enabled() and robot_chassis.configured():
                await poll_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- a missed scan is a stale answer, not a crash
            if time.monotonic() - failed_at > 60:
                failed_at = time.monotonic()
                logger.info("lidar people: no scan", exc_info=True)
        await asyncio.sleep(max(0.3, float(settings.lidar_people_every_s)))


def start() -> None:
    """From main's startup hook: idle (one setting read a second) unless
    HEAD_FOLLOW and LIDAR_PEOPLE are on, so turning them on needs no restart."""
    if not settings.lidar_people:
        return
    task = STATE["task"]
    if task is not None and not task.done():
        return
    STATE["task"] = asyncio.create_task(_watch())


async def stop() -> None:
    task, STATE["task"] = STATE["task"], None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


def _record(**fields) -> None:
    try:
        from app import turnlog

        turnlog.record("lidar_people", **fields)
    except Exception:  # noqa: BLE001
        logger.debug("lidar_people row not written", exc_info=True)
