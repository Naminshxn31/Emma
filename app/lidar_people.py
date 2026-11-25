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
  The fit takes either sign of `a`.
* **9 Nov: on the robot the lens is measured, and the measured facts aim.**
  ROBOT_CAMERA_HFOV_MEASURED (19 deg), the picture mirrored (a box label in
  the sweep's pictures reads right only flipped) and the laser counting to the
  LEFT (map_check lays its rays on the map with that): with those set no fit
  is trusted (`solve` -> why "lens_measured", or "sign" for one that points
  the other way) -- none of the robot's 6 and 9 Nov solves agreed (head_sign,
  scale, disagree), and a review replay of 6 Nov found a wrong-signed one that
  a band widened to the measured lens would have trusted and saved.

How a person is found (`Tracker`): the robot stands still during a
conversation (docked or parked), so the room around it is a background --
per 2-degree bin, the return it has at least BG_QUANTILE of the last
BG_WINDOW_S (the nearer of it and its two neighbours'). Returns well
in front of it (CLOSER_M) within RANGE_M are something new; neighbouring
returns cluster, clusters the size of a leg or a body survive, two legs close
together are one person. A table, the dock, a chair that stays: background.
A person who stood perfectly still for BG_QUANTILE of the window (~1.5 min)
joins it (the camera has them by then). The robot moving (pose change, a walk) throws the
background away.

Reads only (the board's laser scan), and only while HEAD_FOLLOW and
LIDAR_PEOPLE are on and the robot is not walking. No picture, no words:
positions in degrees and metres.

**Who walked in and stood (`Follower`, owner 5 Nov: "เวลาเห็นคนก็ให้หุ่นเดินไปทัก").**
The head camera is 19 degrees wide; this sees 218 (raw frames: -107 to +110, not
behind her: "all round" above is the base's front half and sides). A blip is nobody
and somebody walking past is not for walking up to, so a person worth her walk is a
track that moved on its own feet and then stood. A mannequin, a table, a poster,
a chair that stays are background and never become one. Replayed on the walk
records (26 minutes standing, both floors): 2 such tracks, both real people in
the pictures; furniture that flickered did not move, and walkers did not stand.
What it misses is the other side of the same coin: somebody already standing when she
stopped IS background, until they move. 53 frames with a face in view while she stood:
the lidar called 1 of them new. So `things` too: every person-sized cluster, new or not.
The lidar cannot say what those are; face_approach asks the map, then the head camera
(PERSON_VISION). While that road is on (`want_far`) it reads every return out to
FAR_RANGE_M: at the 0.68 deg of SCAN_POINTS a leg at 4 m is 2 returns, under MIN_POINTS.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from app.config import settings

logger = logging.getLogger("condo_voice.lidar_people")

BIN_DEG = 2.0
BINS = int(360 / BIN_DEG)
BG_WINDOW_S = 120.0
#: The background is the return a bin has had at least this share of the
#: window -- not the farthest one (one see-through scan; see Tracker.update).
BG_QUANTILE = 0.8
#: A return this much nearer than the bin's background is something new
#: (lidar noise is ~2 cm; a person's legs in front of a wall are metres).
CLOSER_M = 0.25
RANGE_M = (0.25, 2.5)
#: Out to here while walking up to people is armed: her longest walk (4 m, code
#: ceiling) + where she stops + half her length, and no further.
FAR_RANGE_M = 5.8
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
#: |a| outside this is not a camera that a 70 deg guess could be off by -- times `expected_scale()`
#: once the lens is measured (9 Nov: the robot's is 19, |a| ~0.24).
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
        #: Every person-sized cluster in the scan, background or not (only with `want_far`).
        self.things: list[Person] = []
        self.at = -math.inf
        #: How many times the background was thrown away (Follower's tracks are
        #: in the frame of the base as it stood).
        self.resets = 0

    def reset(self) -> None:
        self.history.clear()
        self.people = []
        self.things = []
        self.resets += 1

    def stood_s(self, t: float) -> float:
        """How long the background has been gathered, standing where it is."""
        return t - self.history[0][0] if self.history else 0.0

    def _moved(self, pose) -> bool:
        """Past MOVE_RESET_* of where the background began. From that pose, not the
        last one: a base creeping at 5 cm/s (board_walk's docking, a slow spot turn)
        moves under the bar every poll and never reset it, and every wall then read
        as somebody new: 4.2 people a scan on the replays of 4 Nov's walks."""
        if not isinstance(pose, dict):
            return False
        try:
            here = (float(pose["x"]), float(pose["y"]), float(pose.get("yaw") or 0.0))
        except (KeyError, TypeError, ValueError):
            return False
        before = self.pose
        if before is None:
            self.pose = here
            return False
        turned = abs(_wrap_deg(math.degrees(here[2] - before[2])))
        if math.hypot(here[0] - before[0], here[1] - before[1]) > MOVE_RESET_M or turned > MOVE_RESET_DEG:
            self.pose = here
            return True
        return False

    def update(self, points: list[dict], pose, t: float, range_m: tuple[float, float] = RANGE_M) -> list[Person]:
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
        # Not the farthest return: ONE scan that saw through a gap, or saw
        # nothing in a bin, made the wall behind a cupboard that bin's
        # background for the whole window, and the cupboard "a person" (4 Nov,
        # the robot's dock room: 4-7 people round an empty room). 150 s of its
        # real scans replayed: farthest 2.85 false people a scan, farthest of
        # the bin and its neighbours 1.92, the 0.8 return 0.05 (0.9: 0.15),
        # the 0.8 one with the neighbours 0.01 (none in the second half).
        typical = np.quantile(np.array([scan[1] for scan in self.history]), BG_QUANTILE,
                              axis=0, method="higher").tolist()
        background = [min(typical[b - 1], typical[b], typical[(b + 1) % BINS]) for b in range(BINS)]
        new = sorted(((a, d) for a, d, b in valid
                      if range_m[0] <= d <= range_m[1] and d < background[b] - CLOSER_M),
                     key=lambda ad: math.atan2(math.sin(ad[0]), math.cos(ad[0])))
        self.people = _people(_clusters(new), t)
        self.things = []
        if range_m[1] > RANGE_M[1]:
            seen = sorted(((a, d) for a, d, _b in valid if range_m[0] <= d <= range_m[1]),
                          key=lambda ad: math.atan2(math.sin(ad[0]), math.cos(ad[0])))
            self.things = _people(_clusters(seen), t)
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


# ---- who walked in and stood ------------------------------------------------------

#: One poll to the next, a person's own feet: a brisk 1.5 m/s at 2 polls a second.
FOLLOW_JUMP_M = 0.8
#: A leg hidden by the other, a poll missed: still that track.
FOLLOW_GAP_S = 1.5
#: The background needs this long before anything new means anything: a robot that
#: has just stopped has no room behind anybody yet.
MIN_STAND_S = 6.0
#: On their own feet at least this far. A flicker on glass, or a chair leg at the
#: edge of a bin, reappears where it was: the replays' two false standers moved 0.00
#: and 0.02 m. Somebody who only shifted their weight has not arrived anywhere.
WALKED_M = 0.5
#: Standing: the track stayed this close to its own middle (one leg hidden moves the
#: middle of two legs half a stride's width).
STILL_M = 0.3
#: Of the polls that should have seen them while standing, at least this share did.
STILL_SEEN = 0.6
PATH_KEEP_S = 8.0
MAX_TRACKS = 24


class Follower:
    """The Tracker's people from poll to poll, in the frame of the base as it stands."""

    def __init__(self) -> None:
        self.tracks: list[dict] = []
        self.next_id = 1

    def reset(self) -> None:
        self.tracks = []

    def update(self, people: list[Person], t: float) -> None:
        self.tracks = [k for k in self.tracks if t - k["at"] <= FOLLOW_GAP_S]
        free = list(self.tracks)
        for person in people:                     # nearest first (_people sorts)
            rad = math.radians(person.bearing)
            x, y = person.distance * math.cos(rad), person.distance * math.sin(rad)
            best, gap = None, FOLLOW_JUMP_M
            for track in free:
                d = math.hypot(x - track["x"], y - track["y"])
                if d <= gap:
                    best, gap = track, d
            if best is None:
                best = {"id": self.next_id, "x0": x, "y0": y, "walked": 0.0, "path": deque(), "tried_at": -math.inf}
                self.next_id += 1
                self.tracks.append(best)
            else:
                free.remove(best)
            best.update(x=x, y=y, at=t, walked=max(best["walked"], math.hypot(x - best["x0"], y - best["y0"])))
            best["path"].append((t, x, y))
            while best["path"] and t - best["path"][0][0] > PATH_KEEP_S:
                best["path"].popleft()
        del self.tracks[:-MAX_TRACKS]

    def standing(self, t: float, still_s: float) -> list[dict]:
        """Who arrived on their own feet and has stood for `still_s`, nearest first."""
        out = []
        for track in self.tracks:
            if t - track["at"] > FOLLOW_GAP_S or track["walked"] < WALKED_M:
                continue
            recent = [p for p in track["path"] if t - p[0] <= still_s]
            older = [p for p in track["path"] if t - p[0] > still_s]
            if not older or len(recent) < 2:
                continue                          # not watched for that long yet
            xs, ys = sorted(p[1] for p in recent), sorted(p[2] for p in recent)
            mx, my = xs[len(xs) // 2], ys[len(ys) // 2]
            if any(math.hypot(p[1] - mx, p[2] - my) > STILL_M for p in recent):
                continue
            every = max(0.3, float(settings.lidar_people_every_s))
            if len(recent) < STILL_SEEN * still_s / every:
                continue
            out.append({"id": track["id"], "x": mx, "y": my, "at": track["at"], "walked": track["walked"],
                        "tried_at": track["tried_at"], "distance": math.hypot(mx, my),
                        "bearing": math.degrees(math.atan2(my, mx))})
        return sorted(out, key=lambda k: k["distance"])

    def track(self, track_id) -> dict | None:
        for track in self.tracks:
            if track["id"] == track_id:
                return track
        return None


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
    elif not SCALE_RANGE[0] * expected_scale() <= abs(a) <= SCALE_RANGE[1] * expected_scale():
        out["why"] = "scale"
    elif mad > MAX_MAD_DEG:
        out["why"] = "disagree"
    elif _lens_measured():
        # A record, never the aim (review 9 Nov): with the lens measured on the robot the measured
        # facts aim; whether this fit agrees with them is what the row is for.
        out["why"] = "sign" if (float(a) < 0) != bool(settings.robot_camera_mirrored) else "lens_measured"
    else:
        out["trusted"] = True
    return out


def _lens_measured() -> bool:
    return float(settings.robot_camera_hfov_measured or 0.0) > 0


def expected_scale() -> float:
    """The |a| a right check reads: 1 with only the 70 deg guess to go on; with the lens measured on
    the robot (ROBOT_CAMERA_HFOV_MEASURED) the small-angle ratio of the two -- the robot's 19 deg
    lens is ~0.24, which the 0.6-1.6 band could only ever refuse. With the lens measured no fit
    steers anything (`solve` keeps it a record), so this only says, in the rows, whether a fit is
    the right width."""
    from app import facewatch

    measured = float(settings.robot_camera_hfov_measured or 0.0)
    if not 0 < measured < 180:
        return 1.0
    return math.tan(math.radians(measured / 2.0)) / math.tan(math.radians(facewatch.ROBOT_CAMERA_HFOV_DEG / 2.0))


def camera_from_lidar(fit: dict, lidar_deg: float) -> float | None:
    """A lidar bearing in the head camera's units, body frame (what the head
    turns by); None without a trusted fit."""
    if not fit or not fit.get("trusted") or not fit.get("a"):
        return None
    return (float(lidar_deg) - float(fit["b"])) / float(fit["a"])


# ---- the poller -------------------------------------------------------------------

STATE: dict = {"tracker": Tracker(), "check": Check(), "follow": Follower(), "task": None, "logged_at": -math.inf}


def reset() -> None:
    STATE.update(tracker=Tracker(), check=Check(), follow=Follower(), logged_at=-math.inf)


def want_far() -> bool:
    """Walking up to whoever she sees is armed: the whole scan, further out."""
    try:
        from app import face_approach

        return bool(settings.face_approach_anyone and face_approach.armed())
    except Exception:  # noqa: BLE001 -- a probe: the head's own reading goes on
        return False


def enabled() -> bool:
    return bool(settings.lidar_people and (settings.head_follow or want_far()))


def fresh(now: float | None = None, max_age: float = FRESH_S) -> bool:
    """A scan has been read within `max_age`: an empty `people()` then means nobody, not no lidar."""
    now = time.monotonic() if now is None else now
    return now - STATE["tracker"].at <= max_age


def background_ready(now: float | None = None) -> bool:
    """The background has been gathered MIN_STAND_S where the base stands: before that an empty
    `people()` is a tracker warming up after a reset (a base move, a walk, the server's start)."""
    tracker = STATE["tracker"]
    now = time.monotonic() if now is None else now
    return fresh(now) and tracker.stood_s(tracker.at) >= MIN_STAND_S


def people(now: float | None = None, max_age: float = FRESH_S) -> list[Person]:
    tracker = STATE["tracker"]
    now = time.monotonic() if now is None else now
    if now - tracker.at > max_age:
        return []
    return list(tracker.people)


def things(now: float | None = None, max_age: float = FRESH_S) -> list[Person]:
    """Every person-sized cluster of the last scan, nearest first, new or not. Fresh reads only."""
    tracker = STATE["tracker"]
    now = time.monotonic() if now is None else now
    if now - tracker.at > max_age:
        return []
    return list(tracker.things)


def check() -> Check:
    return STATE["check"]


def standing(now: float | None = None, still_s: float | None = None) -> list[dict]:
    """Who walked in and stood (Follower), once the background means anything.
    Fresh reads only: a stopped poller's last answer is nobody."""
    tracker, follow = STATE["tracker"], STATE["follow"]
    now = time.monotonic() if now is None else now
    if now - tracker.at > FRESH_S or tracker.stood_s(tracker.at) < MIN_STAND_S:
        return []
    still = float(settings.face_approach_person_still_s if still_s is None else still_s)
    return follow.standing(now, still)


def world_of(person: dict) -> tuple[float, float] | None:
    """Where on the map, from where the base stood when the background began."""
    pose = STATE["tracker"].pose
    if pose is None:
        return None
    x, y, yaw = pose
    c, s = math.cos(yaw), math.sin(yaw)
    return (x + person["x"] * c - person["y"] * s, y + person["x"] * s + person["y"] * c)


def track(track_id) -> dict | None:
    return STATE["follow"].track(track_id)


def _walking() -> bool:
    try:
        from app import event_walk, robot_face_link

        # the wheels, not a walk still resting its arm (event_walk.moving, review 7 Nov)
        return bool(event_walk.moving()) or robot_face_link.nav_activity() in ("active", "stale")
    except Exception:  # noqa: BLE001
        return False


async def poll_once(now: float | None = None) -> list[Person]:
    from app import robot_chassis

    tracker, follow = STATE["tracker"], STATE["follow"]
    if _walking():
        tracker.reset()
        tracker.pose = None
        follow.reset()
        return []
    far = want_far()
    scan = await asyncio.wait_for(robot_chassis.laserscan(max_points=0 if far else SCAN_POINTS), 1.5)
    now = time.monotonic() if now is None else now
    resets = tracker.resets
    found = tracker.update(scan.get("points") or [], scan.get("pose"), now,
                           range_m=(RANGE_M[0], FAR_RANGE_M) if far else RANGE_M)
    if tracker.resets != resets:
        follow.reset()                         # the frame they were tracked in is gone
    if far:
        follow.update(found, now)
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
        except Exception as exc:  # noqa: BLE001 -- a missed scan is a stale answer, not a crash
            if time.monotonic() - failed_at > 60:
                failed_at = time.monotonic()
                if isinstance(exc, (TimeoutError, OSError)):
                    # the board slow to answer (the server's own start keeps the
                    # CPU busy for a while): one line, not a 40-line traceback
                    # that reads like a crash in server.log (4 Nov)
                    logger.info("lidar people: no scan (%s)", type(exc).__name__)
                else:
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
