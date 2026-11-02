"""Walk a remembered path on the navigation board, on the robot (floor 1, 28 ต.ค. 2026).

Owner: "ให้ทำการเดินจำการเข้าห้องนี้และส่วนต้อนรับทั้งหมด แล้วกลับแท่นให้ได้ด้วย".
Floor 2 runs on scripts/drive_route.py (our own lanes against measured walls,
because the board could not localize in the furnished rooms there). Floor 1 is
different: the board's own map is a lidar scan laid over the plan, it holds a
fix (Q 70-80) everywhere we went, and its MoveTo planner got through the
multimedia room's door and its ~0.9 m gap. So this is a thin runner:

* a path is a list of steps from `data/robot/waypoints/<floor>.json`:
  ``{"to": [x, y], "face": deg?, "speed": m/s?}`` = one MoveTo (``with_yaw``
  when ``face`` is given -- without the flag the board ignores the yaw),
  ``{"do": "undock"}``, ``{"do": "dock"}``;
* points are close together where the line matters (the gap between the
  table's chairs and the round chairs): the board plans its own way between
  two points, and close points are how the line is held;
* a leg with no progress for 8 s is cancelled and the run stops where it is
  -- no skipping ahead past a point it could not reach;
* docking is our own: to the pre-dock spot on the dock's line facing out,
  then reverse straight in under velocity control (the floor-2 way,
  drive_route.back_into_dock, owner 7 ต.ค. "เดินหน้าเข้ามาใกล้ๆ แล้วค่อยหมุนตัว
  ถอยง่ายกว่า"), stopping on on_dock, a bumper event, a stall, the heading
  leaving the line, or overshooting the dock pose. The lidar does not see
  behind, and on floor 1 it does not see the dock either (lower than the
  scan plane) -- the dock pose is the only guide, so the speeds are low.
  ``--register-dock`` registers the board's home dock once on it, when the
  map has none (GoHome needs one);
* ``{"creep": [[x, y, speed?], ...]}`` is a polyline we drive ourselves under
  velocity control where the board will not go (the multimedia room):
  `CreepLine` is its tick logic with no I/O, so scripts/walk_sim.py and the
  tests drive the same code against a simulated base and lidar. The board's
  pose jumps are eased in (`PoseFilter`), arbitrated by the lidar against
  the board's own map (`MapFit`); a base holding itself for something ahead
  is told apart from one caught on chair wheels below the lidar.

Run on the robot with the server's Python, from its code directory:
    cd /data/local/emma-server/code && ../runtime/py -s scripts/board_walk.py \\
        data/robot/waypoints/floor1.json multimedia_to_dock [--map] [--register-dock]
``--map`` keeps SLAM mapping on while walking (the lidar rewrites what it
sees -- "เดินจำ"), off again before a creep line (the multimedia room's door:
never mapped, CLAUDE.md), before docking and at the end. SIGTERM / SIGHUP
stop it the Ctrl-C way (`stop_on_signals`).
"""
from __future__ import annotations

import argparse
import json
import math
import signal
import struct
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable

# app.slamware_sdk: the repo (or the robot's code directory) above scripts/,
# and the working directory when the script was pushed somewhere else.
sys.path[:0] = [str(Path(__file__).resolve().parent.parent), "."]

BOARD ="http://192.168.11.1:1448"
SDK_HOST, SDK_PORT = "192.168.11.1", 1445
MOVE_TO = "slamtec.agent.actions.MoveToAction"
STOP_EVENTS = {"BUMPER_TRIGGERED", "PATH_OCCUPIED", "ROBOT_BLOCKED", "DEVICE_ERROR"}

#: A leg that moves less than this for STUCK_S is given up (the board keeps
#: "replanning" in front of a closed door or a person for as long as it likes).
STUCK_S = 8.0
#: Where a point the robot rolls through hands over to the next one.
HANDOVER_M = 0.35
#: A leg the board ends > 0.6 m short of its own accord is asked again once.
LEG_TRIES = 2
LEG_RETRY_WAIT_S = 2.0
#: A leg with a heading that the board ends further off it than this is
#: turned the rest of the way on the spot.
FACE_FIX_DEG = 8.0
LEG_MAX_S = 120.0
MIN_QUALITY = 50
#: drive_route's numbers for the floor-2 dock, the same charger model.
PREDOCK_M = 0.45
BACK_SPEED = 0.06
BACK_SLOW_M = 0.10
#: "ลื่นสมูทขึ้น" (owner 28 ต.ค.): the first 20 cm of the 45 cm back at 0.08;
#: the contacts are still met at drive_route's 0.06 and 0.036.
BACK_FAST_SPEED = 0.08
BACK_FAST_FROM_M = 0.25
#: How far past the dock pose a reverse may go without contact. 0.04 on
#: floor 2, where the dock pose was the board's own registration; here it is
#: an estimate until --register-dock has run, so a little more (the stall
#: check is what stops a push into something).
OVERSHOOT_M = 0.04
OVERSHOOT_ESTIMATED_M = 0.10
HEADING_GUARD_DEG = 5.0
STALL_FROM_M = 0.05
STALL_S = 1.2
STALL_MPS = 0.012
UNDOCK_SPEED = 0.15
#: Past the first contact, onto the springs (see Walker._seat), then how long
#: the robot has to stay on the dock before it counts.
SEAT_M = 0.015
SEAT_SPEED = 0.05
SETTLE_S = 2.0
DOCK_TRIES = 2
#: Closer than this to the pre-dock spot, the dock's approach point is skipped.
APPROACH_SKIP_M = 1.5
#: Driving out of the dock: stop for anything this close ahead of the front.
OUT_FRONT_STOP_M = 0.20
#: Our own line through the multimedia room (28 ต.ค.). The board plans the
#: middle of the free space and will not take a target within ~0.55 m of
#: anything (CANNOT_REACH_TARGET at 0.51 m) -- and between two targets its
#: path cut the table's corner at (-12.2, -11.1), ~0.25 m from the table-side
#: chairs, where the owner saw the robot touch a chair ("พื้นที่ขวาจะเยอะกว่า").
#: A "creep" step drives a polyline itself under velocity control, the
#: floor-2 way (drive_route lanes), with drive_route's lidar checks.
CREEP_SPEED = 0.15
#: The most a segment's own speed may ask (open stretches of a creep line).
CREEP_SPEED_MAX = 0.25
CREEP_START_TOL_M = 0.25
#: The next segment takes over this far before a vertex: robot_lanes'
#: lane_command brakes into a lane's end, which at every vertex is a stop.
CREEP_HANDOVER_M = 0.10
CREEP_MAX_OFF_M = 0.15
CREEP_HOLD_S = 20.0
#: Turn on the spot first when the line is more than this off the heading
#: (robot_lanes' 25° is for open lanes). 28 ต.ค. trace in the gap: heading
#: north while the line ran at 58°, the round chairs' legs came into the strip
#: ahead (0.28 m from the front), and the base -- which slows itself for what
#: is ahead -- made 0.009 m/s of the 0.06 asked and none of a 0.16 rad/s turn
#: for 9 s. Facing along the line first keeps the legs out of the strip.
CREEP_ALIGN_DEG = 12.0
#: A heading error up to this that has no room to be turned out on the spot
#: is steered out while rolling instead (the segment then stays steer-only).
STEER_INSTEAD_DEG = 30.0
CREEP_STALL_S = 3.0
CREEP_STALL_M = 0.03
#: The base holds itself for what is this close ahead of its front face in
#: the strip (its sonar hold): every held stretch of the 28 ต.ค. traces had
#: something 0.10-0.39 m ahead (14 stretches in 7 traces), moving freely from
#: ~0.5 m. Held like that is not "caught on chair wheels" -- tv1c and tv1d
#: stopped as caught with the round chair 0.34-0.38 m ahead -- and steering
#: does not get it out (go1: 0.13 rad/s asked, 6° in 3 s, 7 s standing).
HOLD_ZONE_M = 0.45
HELD_ASK_MPS = 0.05
#: Held with the heading this far off the line: turn on the spot onto it (the
#: line is where things are out of the strip). go1 / cyc1 at the gap's exit:
#: heading -110..-114° on a -99° line, the round chair's backrest tip in the
#: strip's edge; aligned, it is 0.41 m to the side.
HELD_TURN_MIN_DEG = 4.0
HELD_SPINS_MAX = 2
CREEP_END_TOL_M = 0.12
#: Held up by the base's own slow-down for something ahead this close to a
#: bend = take the next segment now (its heading turns away from it).
STALL_HANDOVER_M = 0.45
#: side_nudge: a side the lidar sees nearer than this to the base moves the
#: line away by the difference, at most NUDGE_MAX_M. 28 ต.ค.: the north end of
#: the gap leaves ~5 cm a side at the scan plane.
NUDGE_M = 0.08
NUDGE_MAX_M = 0.08
#: A pose change bigger than this (plus twice the distance the speed allows)
#: between two ticks of a creep line is the board's localization jumping.
POSE_JUMP_M = 0.06
#: A jump is eased into, not ignored (PoseFilter): the offset that keeps the
#: line continuous is worked off by this much per metre driven (0.2 m over
#: ~1.3 m, an approach of ~8.5°). 28-29 ต.ค. back1: the offset was kept for
#: good -- four corrections (0.10/0.21/0.17/0.11 m) on the way out of the
#: screen's alcove left the robot following a frame ~0.2 m east of the real
#: one, into the door leaf's tip, for 120 s; the lidar frame at the stop
#: matched the map at the board's pose, not at ours.
POSE_BLEND_PER_M = 0.15
#: One jump this big is not a correction: the board is lost or the wheels
#: slip (28 ต.ค.: 0.6 m north in one tick, the robot stuck on chair wheels).
POSE_LOST_M = 0.40
#: Lidar arbiter (MapFit): an offset is held, not eased off, only while the
#: scan fits the board's own map clearly better where we think the robot is
#: than where the board says it is -- the broken-door-map case of 28 ต.ค.
#: Held further than this from the board = stop, the two cannot both be right.
FIT_TOL_M = 0.06
FIT_HOLD_MARGIN = 0.08
FIT_EVERY_S = 0.5
POSE_HOLD_MAX_M = 0.30
#: The progress watchdog: less than this in CREEP_HOLD_S = held up, whatever
#: speed is being asked. back1: 83 s at 2-4 mm/s with 0.06-0.08 asked (the
#: base holds for what is within ~0.4 m ahead), caught by neither the stall
#: check (>= 0.08 asked) nor the hold timer (0 asked) -- only by CREEP_MAX_S.
#: 0.15 m: back1 made 0.05-0.07 m in its 20 s windows; the slowest stretch
#: that was not held made 0.31 m in 7 s (tv1g, segment 4).
PROGRESS_M = 0.15
#: Base half-width (0.35, robot_lanes) + 1 cm: at the scan plane the lidar
#: sees chair legs and the door frame, not the arms' height.
CREEP_HALF_WIDTH = 0.36
CREEP_SIDE_STOP_M = 0.02
TURN_SWEEP_MIN_M = 0.03            # room left round the arms' rectangle while turning in place
CREEP_MAX_S = 120.0
#: A lidar frame with fewer returns than this is not a look at the room: a
#: frame here has 1,200-1,600 (tests/data/robot), and drive_route met an empty
#: one on 6 ต.ค. 13:58 (its number, 200). Read as it was, an empty frame is
#: "nothing there" to every check -- no front, no sides, and 9 m of room for a
#: spot turn in the door frame (review 29 ต.ค.).
SCAN_MIN_PTS = 200


#: The robot's own Emma server (scripts/robot_server.py: 127.0.0.1 only) and
#: its .env beside the code, relative to the code directory this runs from.
SERVER = "https://127.0.0.1:8001"
ENV_PATH = ".env"


def ws_token(env_path: str | Path) -> str:
    """WS_TOKEN from a .env file -- read, never printed."""
    for line in Path(env_path).read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "WS_TOKEN":
            return value.strip().strip('"').strip("'")
    raise KeyError("WS_TOKEN")


def wrap_pi(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


class Stop(Exception):
    pass


def stop_on_signals() -> None:
    """SIGTERM (`pkill`'s default) and SIGHUP (the adb shell this was started
    from going away -- the multimedia room is a Wi-Fi dead zone, CLAUDE.md)
    end the run the Ctrl-C way, as KeyboardInterrupt: every open velocity
    session sends its zero, main's finally cancels the board's MoveTo. Python's
    own default for both is to die on the spot -- no finally, no __exit__ --
    and the MoveTo went on to its target with nobody watching (review 29 ต.ค.).
    SIGINT too: started in the background (`nohup ... &`) it is inherited
    ignored, and CPython only puts its handler over a default (drive_route's
    review finding 1: "stop" reported sent, the robot kept driving). A SIGHUP
    that is already ignored (nohup) stays ignored -- the run was meant to
    outlive the shell. The first signal ignores the rest, so a second cannot
    cut the stop short (drive_route's except KeyboardInterrupt); a stop that
    hangs is kill -9's."""
    sigs = [signal.SIGINT, signal.SIGTERM]
    hup = getattr(signal, "SIGHUP", None)                # not on Windows
    if hup is not None and signal.getsignal(hup) is not signal.SIG_IGN:
        sigs.append(hup)

    def _stop(signum, frame):
        for s in sigs:
            signal.signal(s, signal.SIG_IGN)
        raise KeyboardInterrupt(signal.Signals(signum).name)

    for s in sigs:
        signal.signal(s, _stop)


def load_path(route_file: str | Path, name: str) -> tuple[dict, list[dict]]:
    doc = json.loads(Path(route_file).read_text(encoding="utf-8"))
    paths = doc.get("paths") or {}
    if name not in paths:
        raise SystemExit("no path %r in %s (have: %s)" % (name, route_file, ", ".join(sorted(paths))))
    return doc, list(paths[name]["steps"])


def dock_pose(doc: dict) -> tuple[float, float, float]:
    x, y, deg = doc["dock"]["pose"]
    return float(x), float(y), math.radians(float(deg))


def pre_dock(doc: dict) -> tuple[float, float, float]:
    """On the dock's line, PREDOCK_M out, facing out (the way it backs in)."""
    x, y, yaw = dock_pose(doc)
    return x + PREDOCK_M * math.cos(yaw), y + PREDOCK_M * math.sin(yaw), yaw


def back_in_command(out: float, lat: float, herr: float) -> tuple[float, float]:
    """(vx, heading wanted minus heading now) for one tick of reversing onto
    the dock. ``out`` = still to go along the dock's line, ``lat`` = + left of
    the line facing out, ``herr`` = heading minus the dock's. Backing, the
    tail leads: to come back onto the line from the left the nose turns LEFT
    (drive_route, 7 ต.ค.). The turn itself goes out as short pulses
    (`BACK_PULSE_W`): a steady small rate does nothing on this base."""
    lean = max(-math.radians(2), min(math.radians(2), 3.0 * lat))
    want = wrap_pi(lean - herr)
    if out > BACK_FAST_FROM_M:
        vx = -BACK_FAST_SPEED
    elif out > BACK_SLOW_M:
        vx = -BACK_SPEED
    else:
        vx = -BACK_SPEED * 0.6
    return vx, want


def rolls_through(step: dict, nxt: dict | None) -> bool:
    """A plain point followed by another point is handed over before it is
    reached (Walker.leg). Not when it sets a heading (that is a place to face
    something), nor before a creep line (which starts from where the robot
    stands) or any other kind of step."""
    plain = "to" in step and step.get("face") is None and not step.get("fallback")
    return plain and nxt is not None and "to" in nxt


def side_nudge(left: float | None, right: float | None) -> float:
    """How far (m, + = left) to move a creep line this tick, away from a side
    the lidar sees within NUDGE_M of the base -- and only then. Both sides
    close = the difference (pushed both ways, the nearer wins)."""
    shift = 0.0
    if left is not None and left < NUDGE_M:
        shift -= NUDGE_M - left
    if right is not None and right < NUDGE_M:
        shift += NUDGE_M - right
    return max(-NUDGE_MAX_M, min(NUDGE_MAX_M, shift))


def join_line(pts: list[tuple[float, float]], at: tuple[float, float]) -> tuple[int, float]:
    """The segment of a creep line to start on from `at`, and how far `at` is
    from it: the nearest one, the later one when two are as near (at a vertex,
    the segment ahead). A run stopped part way picks up where it is."""
    best, best_d = 0, float("inf")
    for k, (a, b) in enumerate(zip(pts, pts[1:])):
        dx, dy = b[0] - a[0], b[1] - a[1]
        t = max(0.0, min(1.0, ((at[0] - a[0]) * dx + (at[1] - a[1]) * dy) / max(dx * dx + dy * dy, 1e-9)))
        d = math.hypot(at[0] - (a[0] + t * dx), at[1] - (a[1] + t * dy))
        if d <= best_d + 0.02:
            best, best_d = k, min(d, best_d)
    return best, best_d


#: drive_route's doorway pulse (robot_lanes.DOOR_PULSE_W / _WAIT_S): one tick
#: at 0.08 rad/s, at most every 0.5 s, when the heading is off by > 0.7°.
BACK_PULSE_W = 0.08
BACK_PULSE_WAIT_S = 0.5
BACK_PULSE_MIN_DEG = 0.7


class MapFit:
    """How well a lidar frame lies on the board's own map at a given pose:
    the fraction of returns within ~FIT_TOL_M of an occupied cell. The map is
    the board's explore grid (`GET /api/core/slam/v1/maps/explore`, header
    <ffIIf origin x/y, width, height, resolution; u32 size at 32; int8 cells
    from 36, < -10 = occupied). numpy only -- the robot has no scipy.

    Used to arbitrate a pose jump (PoseFilter): measured on the 28 ต.ค. stops,
    10-13 cm off moved the fit 0.83 -> 0.56 at the screen, so two poses 0.1 m
    apart are told apart; a constant bias of the map moves both alike."""

    def __init__(self, blob: bytes, tol_m: float = FIT_TOL_M):
        import numpy as np

        ox, oy, w, h, res = struct.unpack_from("<ffIIf", blob, 0)
        (size,) = struct.unpack_from("<I", blob, 32)
        if size != w * h or len(blob) < 36 + size or not res > 0:
            raise ValueError("explore map: size %d for %d x %d, %d bytes" % (size, w, h, len(blob)))
        occ = np.frombuffer(blob[36:36 + size], dtype=np.int8).reshape(h, w) < -10
        r = max(1, int(round(tol_m / res)))
        near = occ.copy()
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if dx * dx + dy * dy > r * r or (dx == 0 and dy == 0):
                    continue
                # near[y, x] |= occ[y + dy, x + dx], without wrapping round the edges
                ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
                xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
                near[ys, xs] |= occ[yd, xd]
        self.ox, self.oy, self.w, self.h, self.res = float(ox), float(oy), int(w), int(h), float(res)
        self.near = near

    def score(self, pose: tuple[float, float, float], points, max_pts: int = 400) -> float:
        import numpy as np

        p = np.asarray(points, dtype=float).reshape(-1, 2)
        if not len(p):
            return 0.0
        p = p[:: max(1, len(p) // max_pts)]
        x, y, yaw = pose
        c, s = math.cos(yaw), math.sin(yaw)
        col = np.floor((x + p[:, 0] * c - p[:, 1] * s - self.ox) / self.res).astype(int)
        row = np.floor((y + p[:, 0] * s + p[:, 1] * c - self.oy) / self.res).astype(int)
        inside = (col >= 0) & (col < self.w) & (row >= 0) & (row < self.h)
        hits = self.near[row[inside], col[inside]].sum()
        return float(hits) / len(p)


class PoseFilter:
    """The pose a creep line steers by: the board's, with its jumps eased in.

    History. The doorway, 28 ต.ค.: the board's pose jumped 0.2 m sideways in
    half a second (its saved map of the door was broken by a --map run), the
    line seemed 21° away and the turn back was refused at the jamb -- so a
    jump the base could not have made was not followed, an offset kept the
    line going on from where the robot was. With the map restored the jumps
    are the board CORRECTING itself: at the screen it reads 9-10 cm and 3-4°
    off (lidar fits of three stops), and corrects as the robot leaves the
    alcove. Kept for good, the offset carried that error -- back1, four jumps:
    ~0.2 m east of the line, into the door leaf, 120 s.

    Now a jump is eased in (POSE_BLEND_PER_M of the offset worked off per
    metre driven -- the robot comes back to the line at a few degrees, no
    spin in the gap), unless the lidar says the board jumped the wrong way
    (MapFit: the scan fits clearly better where we think the robot is) --
    then it is held, and stopped for beyond POSE_HOLD_MAX_M. One jump beyond
    POSE_LOST_M is a stop: no correction is that big."""

    def __init__(self, fit: MapFit | None = None):
        self.fit = fit
        self.corr = (0.0, 0.0)
        #: the jumps alone, never eased off: raw + this is continuous -- how far the
        #: robot has really moved (the stall and progress checks), not where to steer
        self.jumped = (0.0, 0.0)
        self.raw_prev: tuple[float, float] | None = None
        self.jumps = 0
        self.holding = False
        self.checked_at: float | None = None

    def update(self, raw: tuple[float, float, float], now: float, vx: float, dt: float,
               points=None) -> tuple[tuple[float, float, float], list[str], str | None]:
        """(pose to steer by, things to say, why to stop or None). `vx` is the
        speed asked on the last tick, `dt` the time since."""
        x, y, yaw = raw
        msgs: list[str] = []
        allowed = abs(vx) * dt
        travelled = 0.0
        if self.raw_prev is not None:
            moved = math.hypot(x - self.raw_prev[0], y - self.raw_prev[1])
            if moved > POSE_LOST_M:
                return (x + self.corr[0], y + self.corr[1], yaw), msgs, (
                    "the board's pose jumped %.2f m in one tick -- not a correction (lost, or the wheels slipping?)"
                    % moved)
            if moved > POSE_JUMP_M + 2 * allowed:
                d = (self.raw_prev[0] - x, self.raw_prev[1] - y)
                self.corr = (self.corr[0] + d[0], self.corr[1] + d[1])
                self.jumped = (self.jumped[0] + d[0], self.jumped[1] + d[1])
                self.jumps += 1
                self.checked_at = None                    # look at the lidar now
                msgs.append("the board's pose jumped %.2f m -- easing onto it as the robot drives" % moved)
            else:
                # eased per metre really driven -- not while held (asked, not moving)
                travelled = min(moved, 1.5 * allowed + 0.002)
        self.raw_prev = (x, y)
        c = math.hypot(*self.corr)
        if c > 1e-4:
            if self.fit is not None and points is not None and (
                    self.checked_at is None or now - self.checked_at >= FIT_EVERY_S):
                self.checked_at = now
                at_board = self.fit.score((x, y, yaw), points)
                at_ours = self.fit.score((x + self.corr[0], y + self.corr[1], yaw), points)
                hold = at_ours > at_board + FIT_HOLD_MARGIN
                if hold != self.holding:
                    msgs.append(("the lidar fits the map where the robot was (%.2f), not where the board jumped "
                                 "(%.2f) -- keeping to it" if hold else
                                 "the lidar no longer contradicts the board (%.2f vs %.2f) -- easing onto it")
                                % ((at_ours, at_board) if hold else (at_board, at_ours)))
                self.holding = hold
            if self.holding:
                if c > POSE_HOLD_MAX_M:
                    return (x + self.corr[0], y + self.corr[1], yaw), msgs, (
                        "the board's pose is %.2f m from where the lidar puts the robot" % c)
            else:
                k = max(0.0, c - POSE_BLEND_PER_M * travelled) / c
                self.corr = (self.corr[0] * k, self.corr[1] * k)
        return (x + self.corr[0], y + self.corr[1], yaw), msgs, None

    def odometry(self, raw: tuple[float, float, float]) -> tuple[float, float]:
        """The board's pose with the jumps taken out (continuous): what the
        stall and progress checks measure motion by. The steered pose moves
        while an offset is eased off, also with the robot standing still."""
        return raw[0] + self.jumped[0], raw[1] + self.jumped[1]


class CreepAction:
    """What one tick of a creep line asks for: "drive" (vx, w), "spin" (turn
    on the spot to `heading`, after checking the arms have room, then tick
    again), "again" (tick again now: a new segment), "done", "stop" (reason).
    (A plain class: the tests load this script by path, where a dataclass
    cannot find its own module.)"""

    def __init__(self, kind: str, vx: float = 0.0, w: float = 0.0, heading: float = 0.0, reason: str = ""):
        self.kind, self.vx, self.w, self.heading, self.reason = kind, vx, w, heading, reason

    def __repr__(self) -> str:
        return "CreepAction(%s, vx=%.3f, w=%.3f, heading=%.3f, %r)" % (self.kind, self.vx, self.w, self.heading,
                                                                      self.reason)


class CreepLine:
    """One creep step, tick by tick, with no I/O of its own -- Walker.creep
    reads the board and the lidar and sends what this returns; the tests and
    the replay of the recorded runs drive it with a simulated base. Owner,
    28 ต.ค.: "ทำเช็คทดสอบรีวิวให้ละเอียด" -- what is not driven offline is not
    tested before it meets the chairs."""

    def __init__(self, step: dict, step_no: int, raw_pose: tuple[float, float, float], now: float,
                 fit: MapFit | None = None):
        from app import robot_lanes as rl

        self.rl = rl
        self.step_no, self.label = step_no, step.get("label") or ""
        # the step's own speed is capped like a point's: the route file check lets a
        # step ask up to 0.5 (the board's legs), a doorway line must not take it
        vmax = min(float(step.get("speed") or CREEP_SPEED), CREEP_SPEED_MAX)
        pts = [(float(p[0]), float(p[1])) for p in step["creep"]]
        # [x, y, speed]: the segment starting there goes at that speed (28 ต.ค.: the open
        # middle of the room and the reception side of the door ran at the doorway's
        # 0.15 throughout -- owner: "เร็ว แม่นยำมากขึ้น")
        speeds = [min(float(p[2]), CREEP_SPEED_MAX) if len(p) > 2 else vmax for p in step["creep"]]
        first, off = join_line(pts, (raw_pose[0], raw_pose[1]))
        if off > CREEP_START_TOL_M:
            raise Stop("step %d: %.2f m from its line" % (step_no, off))
        self.messages: list[str] = []
        if first:
            self.messages.append("step %d: joining the line at segment %d (%.2f m from it)" % (step_no, first + 1, off))
        self.n_points = len(pts)
        self.segs = list(zip(pts, pts[1:]))[first:]
        self.seg_speeds = speeds[first:]
        self.pose = PoseFilter(fit)
        self.t0 = now
        self.prev = (0.0, 0.0)
        self.moving: list[tuple[float, float, float, float]] = []
        self.track: list[tuple[float, float, float]] = []
        self.worst_side, self.worst_lat, self.slowest_front = 9.0, 0.0, None
        self.last_row: dict | None = None
        self.k = -1
        self._next_segment(now)

    def _next_segment(self, now: float) -> None:
        rl = self.rl
        self.k += 1
        a, b = self.segs[self.k]
        self.hdg = math.atan2(b[1] - a[1], b[0] - a[0])
        # front_v with v_max (as DOOR_FAST_LIMITS has it): DOOR_LIMITS' 0.15 capped every
        # faster segment whenever anything was in the strip within 1.5 m -- 2.7 of go1's
        # 12.4 s and 9.8 of res1's 37.7 s on its 0.2-0.25 segments went at 0.15 for
        # something 0.5-1.2 m ahead. The taper below 0.5 m and the stop at 0.1 m stay.
        v = self.seg_speeds[self.k]
        self.limits = replace(rl.DOOR_LIMITS, v_max=v, front_v=max(v, rl.DOOR_LIMITS.front_v),
                              align_deg=CREEP_ALIGN_DEG)
        self.wide = replace(self.limits, align_deg=STEER_INSTEAD_DEG + 5)     # steer, don't spin (see tick)
        self.turn_checked, self.steer_only, self.gap = False, False, 9.0
        self.held_spins = 0
        self.tick_prev = now

    def pop_messages(self) -> list[str]:
        out, self.messages = self.messages, []
        return out

    def after_spin(self, now: float) -> None:
        """A spot turn (CreepAction "spin") took its seconds outside the ticks:
        the next tick ramps from standstill, its dt is not the turn's."""
        self.tick_prev, self.moving, self.prev = now, [], (0.0, 0.0)

    def sent(self, act: CreepAction, renewed: bool) -> None:
        """The command went out (`renewed`: the velocity action had to be taken
        up again and a zero went instead -- ramp from standstill)."""
        self.prev = (0.0, 0.0) if renewed else (act.vx, act.w)
        if self.last_row is not None:
            self.last_row["renewed"] = renewed

    def tick(self, now: float, raw: tuple[float, float, float], pts,
             board_event: Callable[[], bool] = lambda: False) -> CreepAction:
        rl = self.rl
        i, k = self.step_no, self.k
        last = k == len(self.segs) - 1
        a, b = self.segs[k]
        hdg = self.hdg
        dt = max(0.05, min(0.3, now - self.tick_prev))
        self.tick_prev = now
        (x, y, yaw), msgs, lost = self.pose.update(raw, now, self.prev[0], dt, pts)
        self.messages += ["step %d: %s" % (i, m) for m in msgs]
        if lost:
            return CreepAction("stop", reason="step %d %s, segment %d: %s at (%.2f, %.2f)" % (
                i, self.label, k + 1, lost, raw[0], raw[1]))
        front = rl.front_distance(pts, half_width=CREEP_HALF_WIDTH, margin=0.0, max_range=1.5)
        left, right = rl.side_clearance(pts, half_width=CREEP_HALF_WIDTH, shaped=True)
        # step away from a side the lidar sees closing in -- the line stays where it
        # was chosen otherwise (the owner's "ชิดซ้าย" is about wheels the lidar
        # cannot see; centring between legs would undo it)
        shift = side_nudge(left, right)
        sa = (a[0] - math.sin(hdg) * shift, a[1] + math.cos(hdg) * shift)
        sb = (b[0] - math.sin(hdg) * shift, b[1] + math.cos(hdg) * shift)
        cmd = rl.lane_command((x, y, yaw), sa, sb, self.prev, dt, front, self.limits)
        # the next segment takes over before this one brakes into its end
        if cmd.done or (not last and cmd.rem < CREEP_HANDOVER_M):
            if last:
                return CreepAction("done")
            self._next_segment(now)
            return CreepAction("again")
        sides = [s_ for s_ in (left, right) if s_ is not None]
        if sides:
            self.worst_side = min(self.worst_side, min(sides))
        self.worst_lat = max(self.worst_lat, abs(cmd.lat))
        if front is not None:
            front_gap = front - rl.ROBOT_BASE_LENGTH / 2
            self.slowest_front = front_gap if self.slowest_front is None else min(self.slowest_front, front_gap)
        reason = None
        if sides and min(sides) < CREEP_SIDE_STOP_M:
            reason = "something %.2f m from the %s side" % (
                min(sides), "left" if left is not None and left == min(sides) else "right")
        elif abs(cmd.lat) > CREEP_MAX_OFF_M:
            reason = "%.2f m off the line" % cmd.lat
        elif board_event():
            reason = "board event (bumper?)"
        elif now - self.t0 > CREEP_MAX_S:
            reason = "%.0f s on the line" % CREEP_MAX_S
        if cmd.phase == "align" and (self.steer_only or not self.turn_checked):
            if not self.steer_only:
                self.gap = rl.turn_sweep_gap(pts, cmd.err)
                self.turn_checked = True
            if self.steer_only or self.gap < TURN_SWEEP_MIN_M:
                if abs(cmd.err) <= math.radians(STEER_INSTEAD_DEG) and not reason:
                    # 28 ต.ค. in the doorway: 8° off and no room to turn on the
                    # spot -- steer it out while rolling, as an open lane would
                    self.steer_only = True
                    cmd = rl.lane_command((x, y, yaw), sa, sb, self.prev, dt, front, self.wide)
                    if cmd.phase == "align":       # beyond what steering takes, and no room to spin
                        reason = ("%.0f° off segment %d in a place too tight to turn on the spot"
                                  % (math.degrees(cmd.err), k + 1))
                else:
                    reason = ("no room to turn %.0f° onto segment %d: the arms would pass %.2f m "
                              "from something" % (math.degrees(cmd.err), k + 1, self.gap))
        # The progress watchdog (back1, 28-29 ต.ค.): whatever is asked, a robot that has
        # not got PROGRESS_M anywhere in CREEP_HOLD_S is held up -- by a person, a chair,
        # the door leaf, the base's own hold for what is ahead. It used to take 0.00 m/s
        # asked to start the clock; 0.06 asked and 0.005 m/s made went on for 120 s.
        ox_, oy_ = self.pose.odometry(raw)
        self.track = [p for p in self.track if now - p[0] <= CREEP_HOLD_S] + [(now, ox_, oy_)]
        if not reason and now - self.track[0][0] >= CREEP_HOLD_S * 0.95:
            got = max(math.hypot(ox_ - p[1], oy_ - p[2]) for p in self.track)
            if got < PROGRESS_M:
                reason = "held up: %.2f m in %.0f s%s" % (
                    got, CREEP_HOLD_S,
                    "" if front is None else ", something %.2f m ahead" % (front - rl.ROBOT_BASE_LENGTH / 2))
        # Driving but not going anywhere, two kinds. Held: something in the strip
        # within HOLD_ZONE_M of the front face, the base holding itself for it.
        # Stalled: nothing there, 0.08+ asked -- 28 ต.ค. the robot sat 23 s at
        # (-12.21, -10.70) with 0.15 m/s asked and nothing ahead, the owner:
        # caught on a chair's wheels (below the lidar). Never push on that.
        self.moving.append((now, ox_, oy_, cmd.vx))
        self.moving = [m for m in self.moving if now - m[0] <= CREEP_STALL_S]
        moving = self.moving
        still = (now - moving[0][0] >= CREEP_STALL_S * 0.9
                 and math.hypot(ox_ - moving[0][1], oy_ - moving[0][2]) < CREEP_STALL_M)
        asked = min(m[3] for m in moving)
        ahead = None if front is None else front - rl.ROBOT_BASE_LENGTH / 2
        held = still and asked >= HELD_ASK_MPS and ahead is not None and ahead < HOLD_ZONE_M
        stalled = still and asked >= 0.08 and not held
        if (held or stalled) and last and cmd.rem < CREEP_END_TOL_M and not reason:
            # 28 ต.ค. at the multimedia door: a wall 0.7 m ahead and the
            # base crept at ~1 cm/s for the last 9 cm -- that is there
            self.messages.append("step %d: %.2f m short of the end, the base will not creep closer -- stopping "
                                 "here" % (i, cmd.rem))
            return CreepAction("done")
        if held and not reason:
            # Near a bend (28 ต.ค. at the gap: the round chair in the strip 0.2-0.36 m
            # before the line bends away from it): turn onto the next segment and take
            # the bend now. Elsewhere, crooked on the line (go1/cyc1: -110..-114° on a
            # -99° line): turn onto the line. Only with room for the arms' turn
            # (robot_lanes.turn_sweep_gap); else wait -- the watchdog decides.
            bend = not last and cmd.rem < STALL_HANDOVER_M
            if bend:
                a2, b2 = self.segs[k + 1]
                target = math.atan2(b2[1] - a2[1], b2[0] - a2[0])
            else:
                target = hdg
            turn = wrap_pi(target - yaw)
            if abs(turn) > math.radians(HELD_TURN_MIN_DEG if not bend else 3.0) and \
                    self.held_spins < HELD_SPINS_MAX and rl.turn_sweep_gap(pts, turn) >= TURN_SWEEP_MIN_M:
                self.messages.append("step %d: held up by something %.2f m ahead%s -- turning %.0f° onto the %s" % (
                    i, ahead, ", %.2f m before the bend" % cmd.rem if bend else "", math.degrees(turn),
                    "next segment" if bend else "line"))
                self.moving, self.prev = [], (0.0, 0.0)
                if bend:
                    self._next_segment(now)
                self.held_spins += 1
                return CreepAction("spin", heading=target)
            if bend and abs(turn) <= math.radians(3.0):
                self.moving = []
                self._next_segment(now)
                return CreepAction("again")
        if stalled and not reason:
            reason = ("not moving: %.2f m in %.1f s with %.2f m/s asked -- caught on something "
                      "below the lidar (a chair's wheels?)" % (
                          math.hypot(ox_ - moving[0][1], oy_ - moving[0][2]), now - moving[0][0], asked))
        if reason:
            off = math.hypot(*self.pose.corr)
            return CreepAction("stop", reason="step %d %s, segment %d: %s at (%.2f, %.2f)%s" % (
                i, self.label, k + 1, reason, x, y,
                "" if off < 0.02 else " (the board says (%.2f, %.2f))" % (raw[0], raw[1])))
        self.last_row = {
            "t": round(now - self.t0, 2), "seg": k + 1, "pose": [round(x, 3), round(y, 3), round(yaw, 3)],
            "raw": [round(raw[0], 3), round(raw[1], 3)], "corr": [round(c_, 3) for c_ in self.pose.corr],
            "phase": cmd.phase, "vx": round(cmd.vx, 3), "w": round(cmd.omega, 3),
            "rem": round(cmd.rem, 3), "lat": round(cmd.lat, 3), "err": round(math.degrees(cmd.err), 1),
            "front": None if front is None else round(front, 2), "why": cmd.why,
            "sides": [None if s_ is None else round(s_, 3) for s_ in (left, right)],
            "shift": round(shift, 3), "steer_only": self.steer_only}
        return CreepAction("drive", vx=cmd.vx, w=cmd.omega)


class Board:
    def __init__(self, base: str = BOARD):
        import httpx

        self.c = httpx.Client(base_url=base, timeout=15)

    def get(self, path: str):
        r = self.c.get(path)
        r.raise_for_status()
        return r.json() if r.content else None

    def pose(self) -> tuple[float, float, float]:
        p = self.get("/api/core/slam/v1/localization/pose")
        return float(p["x"]), float(p["y"]), float(p["yaw"])

    def quality(self) -> int:
        return int(self.get("/api/core/slam/v1/localization/quality"))

    def power(self) -> dict:
        return self.get("/api/core/system/v1/power/status") or {}

    def current(self):
        r = self.c.get("/api/core/motion/v1/actions/:current")
        return r.json() if r.status_code == 200 and r.content else None

    def cancel(self) -> None:
        self.c.delete("/api/core/motion/v1/actions/:current")

    def events(self) -> list:
        try:
            return self.get("/api/platform/v1/events") or []
        except Exception:                                # noqa: BLE001
            return []

    def mapping(self, on: bool) -> None:
        self.c.put("/api/core/slam/v1/mapping/:enable", json={"enable": bool(on)}).raise_for_status()

    def speed(self) -> str | None:
        r = self.c.get("/api/core/system/v1/parameter", params={"param": "base.max_moving_speed"})
        return str(r.json()).strip('"') if r.status_code == 200 else None

    def set_speed(self, v) -> None:
        self.c.put("/api/core/system/v1/parameter", json={"param": "base.max_moving_speed", "value": str(v)})

    def move_to(self, x: float, y: float, yaw: float | None) -> int:
        r = self.c.post("/api/core/motion/v1/actions", json={
            "action_name": MOVE_TO,
            "options": {"target": {"x": x, "y": y, "z": 0},
                        "move_options": {"mode": 0, "flags": ["with_yaw"] if yaw is not None else [],
                                         "yaw": 0.0 if yaw is None else yaw,
                                         "acceptable_precision": 0, "fail_retry_count": 0}}})
        if r.status_code != 200:
            raise Stop("MoveTo refused: %s %s" % (r.status_code, r.text[:200]))
        return int(r.json().get("action_id") or 0)

    def action_reason(self, aid: int) -> str:
        """The board's own word on how an action ended (`:current` loses it)."""
        try:
            st = (self.get("/api/core/motion/v1/actions/%d" % aid) or {}).get("state") or {}
            return st.get("reason") or "result %s" % st.get("result")
        except Exception:                                # noqa: BLE001
            return "no reason given"

    def explore_map(self) -> bytes:
        """The board's occupancy grid, raw (MapFit parses it)."""
        r = self.c.get("/api/core/slam/v1/maps/explore")
        r.raise_for_status()
        return r.content

    def homedocks(self) -> list:
        return self.get("/api/core/slam/v1/homedocks") or []

    def register_dock(self) -> None:
        self.c.post("/api/core/slam/v1/homedocks/:register",
                    json={"metadata": {"display_name": "home_dock"}}).raise_for_status()


class Walker:
    def __init__(self, board: Board, doc: dict, mapping: bool, register: bool):
        self.b, self.doc, self.mapping_on, self.register = board, doc, mapping, register
        self.sdk = None
        self.trace = None                                # --trace: one JSON line per creep tick
        self.env_path = ENV_PATH
        self.record_dir = None                           # --record DIR
        self._map_fit: MapFit | None | bool = False      # False = not read yet (map_fit)
        self.t0 = time.monotonic()

    def say(self, text: str) -> None:
        print("[%5.1f] %s" % (time.monotonic() - self.t0, text), flush=True)

    def _sdk(self):
        if self.sdk is None:
            from app import slamware_sdk as sw

            self.sdk = sw.SlamwareSdk(SDK_HOST, SDK_PORT)
            self.sdk.call("getsdpversion")
        return self.sdk

    # -- legs ---------------------------------------------------------------
    def leg(self, step: dict, i: int, through: bool = False) -> None:
        """One MoveTo. `through`: the next step is another plain point, so this
        one hands over HANDOVER_M before it -- the next MoveTo replaces this one
        while the robot is still rolling (28 ต.ค., owner: "ปรับให้ลื่นสมูทขึ้น";
        every point of the first tours was a full stop and a start)."""
        tx, ty = (float(v) for v in step["to"])
        yaw = math.radians(float(step["face"])) if step.get("face") is not None else None
        if step.get("speed"):
            self.b.set_speed(step["speed"])
        start = time.monotonic()
        for attempt in range(1, LEG_TRIES + 1):
            aid = self.b.move_to(tx, ty, yaw)
            last, still = self.b.pose(), time.monotonic()
            result = None
            while result is None:
                time.sleep(0.2)
                p = self.b.pose()
                if math.hypot(p[0] - last[0], p[1] - last[1]) > 0.04 or abs(wrap_pi(p[2] - last[2])) > 0.08:
                    last, still = p, time.monotonic()
                if through and math.hypot(p[0] - tx, p[1] - ty) < HANDOVER_M:
                    result = "through"                   # not cancelled: the next MoveTo takes over
                elif self.b.current() is None:
                    result = "done"
                elif time.monotonic() - still > STUCK_S:
                    self.b.cancel()
                    result = "stuck"
                elif time.monotonic() - start > LEG_MAX_S:
                    self.b.cancel()
                    result = "too long"
            x, y, h = self.b.pose()
            if result != "done" or math.hypot(x - tx, y - ty) <= 0.6 or attempt == LEG_TRIES:
                break
            # 28 ต.ค. smooth run 4: the board ended a leg 0.92 m short on its own
            # (someone in the way?) -- say why, give it a moment, ask again
            self.say("step %d: the board ended %.2f m short (%s) -- once more in %.0f s" % (
                i, math.hypot(x - tx, y - ty), self.b.action_reason(aid), LEG_RETRY_WAIT_S))
            time.sleep(LEG_RETRY_WAIT_S)
        q = self.b.quality()
        miss = math.hypot(x - tx, y - ty)
        self.say("step %d %s -> (%.2f, %.2f)%s: %s in %.0f s, at (%.2f, %.2f) %.0f°, %.2f m off, Q%d%s" % (
            i, step.get("label") or "", tx, ty, "" if yaw is None else " facing %.0f°" % math.degrees(yaw),
            result, time.monotonic() - start, x, y, math.degrees(h), miss, q,
            "" if attempt == 1 else ", try %d" % attempt))
        if result not in ("done", "through"):
            raise Stop("step %d: %s" % (i, result))
        if miss > 0.6:
            raise Stop("step %d: ended %.2f m from the point (the board gave up on it)" % (i, miss))
        if yaw is not None and abs(wrap_pi(h - yaw)) > math.radians(FACE_FIX_DEG):
            # 28 ต.ค. at the door front: MoveTo with_yaw ended facing -176° for -98°
            self.say("step %d: the board left it facing %.0f° -- turning to %.0f°" % (
                i, math.degrees(h), math.degrees(yaw)))
            self.turn_to(yaw, tol_deg=3.0)
        if q < 40:
            raise Stop("step %d: localization quality %d" % (i, q))

    # -- our own line -------------------------------------------------------
    def creep(self, step: dict, i: int) -> None:
        """Drive a creep line (CreepLine): read the board's pose and a lidar
        frame, send what the line asks, until it is done or stops."""
        from app.slamware_sdk import VelocitySession

        if self.mapping_on:
            # CLAUDE.md, 28 ต.ค.: a --map run in the multimedia room wrote people and the
            # door leaf into the board's map of the door, and the pose jumped 0.2 m in the
            # doorway on every run after it. A creep line is that door and that room, and
            # --map left mapping on until docking (room_tv_go has no dock: mapped end to end).
            self.b.mapping(False)
            self.mapping_on = False
            self.say("step %d: mapping off for the rest -- never --map through the room's door" % i)
        line = CreepLine(step, i, self.b.pose(), time.monotonic(), fit=self.map_fit())
        for m in line.pop_messages():
            self.say(m)
        self._drop_events("step %d" % i)
        t0 = time.monotonic()
        with VelocitySession(self._sdk()) as v:
            while True:
                pts_now = self.scan()
                act = line.tick(time.monotonic(), self.b.pose(), pts_now, self._board_event)
                for m in line.pop_messages():
                    self.say(m)
                if act.kind == "again":
                    continue
                if act.kind == "done":
                    break
                if act.kind == "stop":
                    v.set(0.0, 0.0)
                    raise Stop(act.reason)
                if act.kind == "spin":
                    self._spot_turn(v, act.heading, tol_deg=3.0)
                    line.after_spin(time.monotonic())
                    continue
                renewed = v.set(act.vx, act.w)
                line.sent(act, renewed)
                if self.trace is not None and line.last_row is not None:
                    self.trace.write(json.dumps(line.last_row) + "\n")
                time.sleep(0.05)
            v.set(0.0, 0.0)
        x, y, h = self.b.pose()
        self.say("step %d %s: line of %d points done in %.0f s, at (%.2f, %.2f) %.0f°, off the line at most %.2f m, "
                 "closest side %.2f m, closest ahead %s, %d pose jump(s), Q%d" % (
                     i, step.get("label") or "", line.n_points, time.monotonic() - t0, x, y, math.degrees(h),
                     line.worst_lat, line.worst_side,
                     "-" if line.slowest_front is None else "%.2f m" % line.slowest_front, line.pose.jumps,
                     self.b.quality()))

    def _board_event(self) -> bool:
        return any(isinstance(e, dict) and e.get("type") in STOP_EVENTS for e in self.b.events())

    def _drop_events(self, why: str) -> None:
        """Read off what the board said before a drive of our own starts. Its
        events queue up until read (_back_in has always dropped them first --
        "drop what came before" -- and drive_route before every push), and
        nothing reads them during a MoveTo leg: a PATH_OCCUPIED from someone
        crossing the reception on the way to the door was the creep's first
        tick's "board event (bumper?)" (review 29 ต.ค.). Said, not lost."""
        old = sorted({e.get("type") for e in self.b.events() if isinstance(e, dict) and e.get("type") in STOP_EVENTS})
        if old:
            self.say("%s: the board had said %s before this drive -- not about it" % (why, ", ".join(old)))

    def map_fit(self) -> "MapFit | None":
        """The board's own map for the lidar arbiter of pose jumps, read once a
        run. None when it cannot be read: jumps are then eased in on the
        board's word (with the map fixed, every jump since was a correction)."""
        if self._map_fit is False:
            try:
                self._map_fit = MapFit(self.b.explore_map())
            except Exception as e:                       # noqa: BLE001
                self.say("(no map for the lidar check of pose jumps: %s -- trusting the board)" % type(e).__name__)
                self._map_fit = None
        return self._map_fit

    # -- dock ---------------------------------------------------------------
    def undock(self) -> None:
        if self.b.power().get("dockingStatus") != "on_dock":
            self.say("undock: not on the dock -- nothing to do")
            return
        self.drive_out("undock")

    def drive_out(self, why: str) -> None:
        """Straight out along the dock's line to the pre-dock spot, our own way
        (velocity). Off the dock, and between two tries at docking: 28 ต.ค. the
        board's MoveTo from beside the dock ended after 11 cm. The lidar watches
        the strip ahead (the owner moved the dock once to where a table stood
        in front of it)."""
        from app import robot_lanes as rl
        from app.slamware_sdk import VelocitySession

        dx, dy, dyaw = dock_pose(self.doc)
        ux, uy = math.cos(dyaw), math.sin(dyaw)
        self._drop_events(why)
        with VelocitySession(self._sdk()) as v:
            vx, t0 = 0.0, time.monotonic()
            while True:
                x, y, _ = self.b.pose()
                out = (x - dx) * ux + (y - dy) * uy
                if out >= PREDOCK_M:
                    break
                front = rl.front_distance(self.scan(), half_width=CREEP_HALF_WIDTH, margin=0.0, max_range=1.5)
                if front is not None and front - rl.ROBOT_BASE_LENGTH / 2 < OUT_FRONT_STOP_M:
                    v.set(0.0, 0.0)
                    raise Stop("%s: something %.2f m ahead" % (why, front - rl.ROBOT_BASE_LENGTH / 2))
                if any(isinstance(e, dict) and e.get("type") in STOP_EVENTS for e in self.b.events()):
                    v.set(0.0, 0.0)
                    raise Stop("%s: board event" % why)
                if time.monotonic() - t0 > 20:
                    v.set(0.0, 0.0)
                    raise Stop("%s: 20 s and not out" % why)
                # ease off over the last 10 cm: the pre-dock spot is where it turns
                target = UNDOCK_SPEED if PREDOCK_M - out > 0.10 else 0.06
                vx = min(target, vx + 0.03) if target > vx else target
                v.set(vx, 0.0)
                time.sleep(0.1)
            v.set(0.0, 0.0)
        x, y, h = self.b.pose()
        self.say("%s: out at (%.2f, %.2f) %.0f°, %s" % (why, x, y, math.degrees(h), self.b.power().get("dockingStatus")))

    def scan(self):
        """Robot-frame lidar points (x forward, y left) over the SDK port. A
        frame under SCAN_MIN_PTS is read again (drive_route: "a short frame is
        not 'no room': read again"), and a second one is a Stop -- not driven,
        and not turned, on "nothing there"."""
        from app import robot_lanes as rl
        from app.slamware_sdk import laser_scan

        pts = rl.scan_points(laser_scan(self._sdk()))
        if len(pts) < SCAN_MIN_PTS:
            time.sleep(0.1)
            again = rl.scan_points(laser_scan(self._sdk()))
            if len(again) < SCAN_MIN_PTS:
                raise Stop("the lidar sent frames of %d and %d points (1,200-1,600 in one) -- not moving on that"
                           % (len(pts), len(again)))
            pts = again
        return pts

    def turn_to(self, yaw: float, tol_deg: float = 1.0) -> None:
        from app.slamware_sdk import VelocitySession

        with VelocitySession(self._sdk()) as v:
            self._spot_turn(v, yaw, tol_deg)

    def _spot_turn(self, v, yaw: float, tol_deg: float) -> None:
        """Turn in place inside an open velocity session -- after checking the
        arms' rectangle has room for this turn (robot_lanes.turn_sweep_gap)."""
        from app import robot_lanes as rl

        turn = wrap_pi(yaw - self.b.pose()[2])
        gap = rl.turn_sweep_gap(self.scan(), turn)
        if gap < TURN_SWEEP_MIN_M:
            v.set(0.0, 0.0)
            raise Stop("no room to turn %.0f° here: the arms would pass %.2f m from something"
                       % (math.degrees(turn), gap))
        t0, prev_w, settled = time.monotonic(), 0.0, 0
        while True:
            cmd = rl.turn_command(self.b.pose()[2], yaw, prev_w, 0.1, tol_deg=tol_deg)
            if cmd.done:
                v.set(0.0, 0.0)
                if prev_w == 0.0:
                    break                                  # within tol from a standstill
                prev_w = 0.0
                time.sleep(0.5)                            # let it coast, then look again
                settled += 1
                if settled > 4:
                    break
                continue
            if time.monotonic() - t0 > 20:
                v.set(0.0, 0.0)
                raise Stop("turn: 20 s and still %.1f° off" % math.degrees(cmd.err))
            v.set(0.0, cmd.omega)
            prev_w = cmd.omega
            time.sleep(0.1)

    def dock(self) -> None:
        dx, dy, dyaw = dock_pose(self.doc)
        px, py, _ = pre_dock(self.doc)
        estimated = bool(self.doc["dock"].get("estimated"))
        overshoot = OVERSHOOT_ESTIMATED_M if estimated else OVERSHOOT_M
        if self.mapping_on:
            self.b.mapping(False)
            self.say("mapping off before docking")
        approach = self.doc["dock"].get("approach")
        x, y, _ = self.b.pose()
        if approach and math.hypot(x - px, y - py) > APPROACH_SKIP_M:
            # 28 ต.ค.: sent to the dock from the middle of the reception, the board
            # planned its own way past the table at (-7.5, -4.2) and an arm hit it
            # (owner: "ชิดโต๊ะมากเกินไปแขนชน ... อย่าให้เกิดแบบนี้อีก") -- every way
            # in comes along the corridor every good dock came along
            self.b.set_speed(0.35)
            self.leg({"to": approach, "label": "the way to the dock"}, 0, through=True)
        self.b.set_speed(0.25)
        self.leg({"to": [px, py], "face": math.degrees(dyaw), "label": "pre-dock spot, facing out"}, 0)
        ux, uy = math.cos(dyaw), math.sin(dyaw)
        nx, ny = -uy, ux
        docked, reason = False, None
        for attempt in range(1, DOCK_TRIES + 1):
            if attempt > 1:
                # 28 ต.ค. run 4: met the dock 1.9° crooked, wheels stopped 1.3 cm short,
                # no contact -- out along the line, square up, back in again
                self.say("dock: not on it (%s) -- out and in once more" % reason)
                self.drive_out("dock again")
            x, y, yaw = self.b.pose()
            if abs(wrap_pi(yaw - dyaw)) > math.radians(1.0):
                self.turn_to(dyaw)
            docked, reason, blocked = self._back_in(dx, dy, dyaw, overshoot)
            if docked:
                docked, reason = self._stays_docked(dx, dy, ux, uy, overshoot)
            if docked:
                break
            if blocked:
                # something behind it (or the bumper), not the dock: the next try
                # drove out and reversed into the same thing again (review 29 ต.ค.)
                reason += " -- not tried again"
                break
        x, y, yaw = self.b.pose()
        power = self.b.power()
        out = (x - dx) * ux + (y - dy) * uy
        lat = (x - dx) * nx + (y - dy) * ny
        self.say("dock: %s at (%.3f, %.3f) %.1f°, %.3f m along / %.3f m aside of the dock pose; %s, charging=%s%s" % (
            "ON THE DOCK" if docked else "NOT docked (%s)" % reason, x, y, math.degrees(yaw), out, lat,
            power.get("dockingStatus"), power.get("isCharging"), "" if attempt == 1 else ", try %d" % attempt))
        if not docked:
            raise Stop("not on the dock: %s" % reason)
        if self.register:
            docks = self.b.homedocks()
            if docks:
                self.say("home dock already registered: %s" % docks)
            elif self.b.quality() < MIN_QUALITY:
                self.say("not registering the dock: localization quality %d" % self.b.quality())
            else:
                self.b.register_dock()
                time.sleep(1.5)
                self.say("home dock registered: %s" % self.b.homedocks())

    def _back_in(self, dx: float, dy: float, dyaw: float, overshoot: float) -> tuple[bool, str | None, bool]:
        """Reverse straight onto the dock from the pre-dock spot (see the module
        docstring for the stops). Returns (on the contacts, why not, blocked):
        blocked = the bumper, or the wheels stopped further out than the
        dock's last BACK_SLOW_M -- something the lidar cannot see behind, not
        the dock, and no reason for another try (dock())."""
        from app.slamware_sdk import VelocitySession

        ux, uy = math.cos(dyaw), math.sin(dyaw)
        nx, ny = -uy, ux
        self.b.events()                                    # drop what came before
        x0, y0, _ = self.b.pose()
        recent: list[tuple[float, float, float]] = []
        docked, reason, blocked, vx, since_pulse = False, None, False, 0.0, 0.0
        t0 = last_power = time.monotonic()
        with VelocitySession(self._sdk()) as v:
            while True:
                tick = time.monotonic()
                x, y, yaw = self.b.pose()
                out = (x - dx) * ux + (y - dy) * uy
                lat = (x - dx) * nx + (y - dy) * ny
                herr = wrap_pi(yaw - dyaw)
                if out < 0.15 or tick - last_power >= 0.3:
                    last_power = tick
                    if self.b.power().get("dockingStatus") == "on_dock":
                        docked = True
                        self._seat(v, x, y)
                        break
                backed = math.hypot(x - x0, y - y0)
                recent = [q for q in recent if tick - q[0] <= STALL_S + 0.3] + [(tick, x, y)]
                ref = [q for q in recent if tick - q[0] >= STALL_S]
                if backed >= STALL_FROM_M and ref and \
                        math.hypot(x - ref[-1][1], y - ref[-1][2]) / (tick - ref[-1][0]) < STALL_MPS:
                    reason = "the wheels stopped after %.2f m (something behind, not the contacts)" % backed
                    # 28 ต.ค. run 4 (crooked, 1.3 cm short) is what DOCK_TRIES is for;
                    # stopped further out, the next try backs into the same thing
                    blocked = out > BACK_SLOW_M
                elif out < -overshoot:
                    reason = "%.2f m past the dock pose and no contact" % -out
                elif abs(herr) > math.radians(HEADING_GUARD_DEG):
                    reason = "heading %.1f° off the dock's line" % math.degrees(herr)
                elif any(isinstance(e, dict) and e.get("type") in STOP_EVENTS for e in self.b.events()):
                    reason, blocked = "board event (bumper?)", True
                elif tick - t0 > 40:
                    reason = "40 s and not on the dock"
                if reason:
                    break
                target, want = back_in_command(out, lat, herr)
                vx = max(target, vx - 0.02)
                omega = 0.0
                since_pulse += 0.1
                if abs(want) > math.radians(BACK_PULSE_MIN_DEG) and since_pulse >= BACK_PULSE_WAIT_S:
                    omega, since_pulse = math.copysign(BACK_PULSE_W, want), 0.0
                v.set(vx, omega)
                time.sleep(0.1)
        return docked, reason, blocked

    def _seat(self, v, xs: float, ys: float, extra: float = SEAT_M) -> None:
        """Keep reversing a little past the first contact. 28 ต.ค.: stopped the
        moment the board said on_dock, the robot read not_on_dock 1.5 s later
        and never charged -- the contacts touch before their springs are
        pressed, and the springs push a robot that stops there back off."""
        t0 = time.monotonic()
        while time.monotonic() - t0 < 1.5:
            x, y, _ = self.b.pose()
            if math.hypot(x - xs, y - ys) >= extra:
                break
            v.set(-SEAT_SPEED, 0.0)
            time.sleep(0.1)
        v.set(0.0, 0.0)

    def _stays_docked(self, dx: float, dy: float, ux: float, uy: float, overshoot: float) -> tuple[bool, str | None]:
        """On the dock SETTLE_S after stopping, and charging -- or one more
        short reverse onto it (not past `overshoot` beyond the dock pose)."""
        from app.slamware_sdk import VelocitySession

        for attempt in (1, 2):
            time.sleep(SETTLE_S)
            power = self.b.power()
            if power.get("dockingStatus") == "on_dock":
                return True, None
            if attempt == 2:
                return False, "touched the contacts but did not stay on them (%s, charging %s)" % (
                    power.get("dockingStatus"), power.get("isCharging"))
            self.say("dock: off the contacts again after stopping -- reversing onto them once more")
            with VelocitySession(self._sdk()) as v:
                t0 = time.monotonic()
                while time.monotonic() - t0 < 3.0:
                    x, y, _ = self.b.pose()
                    if (x - dx) * ux + (y - dy) * uy < -overshoot:
                        break
                    if self.b.power().get("dockingStatus") == "on_dock":
                        self._seat(v, x, y)
                        break
                    v.set(-SEAT_SPEED, 0.0)
                    time.sleep(0.1)
                v.set(0.0, 0.0)
        return False, "not on the dock"

    # -- the room's big screen ----------------------------------------------
    def screen(self, action: str) -> None:
        """Open/close the room screen through the robot's own server (POST
        /room-screen, the hold menu's way; app/room_screens.py) -- owner 28 ต.ค.:
        "พอไปถึงหน้าห้องก็เปิดจอ แล้วก็เดินกลับแท่นแล้วก็ปิดจอ". A screen that
        does not answer is said and walked past: it is no reason to leave a
        robot standing in the reception."""
        import httpx

        try:
            token = ws_token(self.env_path)
            # the robot's own server on loopback, its LAN certificate (mkcert) is
            # not in this Python's store
            r = httpx.post(SERVER + "/room-screen", json={"action": action, "token": token},
                           verify=False, timeout=30)
            doc = r.json()
            self.say("screen %s: %s%s" % (action, "ok" if doc.get("ok") else "NOT ok",
                                          "" if doc.get("ok") else " (%s)" % (doc.get("error") or doc.get("reason"))))
        except Exception as e:                           # noqa: BLE001
            self.say("screen %s: could not ask the server (%s)" % (action, type(e).__name__))

    # -- run ----------------------------------------------------------------
    def run(self, steps: list[dict], from_step: int) -> None:
        q = self.b.quality()
        if q < MIN_QUALITY:
            raise Stop("localization quality %d < %d -- set the pose first" % (q, MIN_QUALITY))
        if self.b.current() is not None:
            raise Stop("the board is running another action")
        if self.mapping_on:
            self.b.mapping(True)
            self.say("mapping on (the lidar rewrites what it sees on the way)")
        for i, step in enumerate(steps, 1):
            if i < from_step:
                continue
            nxt = steps[i] if i < len(steps) else None
            through = rolls_through(step, nxt)
            try:
                try:
                    self.step(step, i, through)
                except Stop as e:
                    if not step.get("fallback"):
                        raise
                    # 28 ต.ค.: the door front as one MoveTo, if the board takes the target;
                    # the three-part way that worked five times if it does not
                    self.say("step %d: %s -- the other way (%d steps)" % (i, e, len(step["fallback"])))
                    if self.b.current() is not None:
                        self.b.cancel()
                    for k, sub in enumerate(step["fallback"]):
                        self.step(sub, i, rolls_through(
                            sub, step["fallback"][k + 1] if k + 1 < len(step["fallback"]) else None))
            except Stop:
                # back1 (28-29 ต.ค.) stopped with no frame on record: where the robot
                # really was had to be read off a frame grabbed by hand afterwards
                if self.record_dir is not None:
                    try:
                        self.record(i, step, "stop")
                    except Exception as e2:              # noqa: BLE001 -- never hide the stop
                        self.say("(could not record the stop: %s)" % type(e2).__name__)
                raise
            if self.record_dir is not None and step.get("do") not in ("screen", "wait") and not through:
                self.record(i, step)

    def step(self, step: dict, i: int, through: bool = False) -> None:
        if step.get("do") == "undock":
            self.undock()
        elif step.get("do") == "dock":
            self.dock()
        elif step.get("do") == "screen":
            self.screen(step["action"])
        elif step.get("do") == "turn":
            # on the spot, after the lidar says the arms have room (_spot_turn)
            self.turn_to(math.radians(float(step["face"])), tol_deg=2.0)
            x, y, h = self.b.pose()
            self.say("step %d %s: facing %.0f° at (%.2f, %.2f)" % (i, step.get("label") or "turn", math.degrees(h), x, y))
        elif step.get("do") == "wait":
            self.say("wait %.0f s" % float(step["s"]))
            time.sleep(float(step["s"]))
        elif "creep" in step:
            self.creep(step, i)
        else:
            self.leg(step, i, through)

    def record(self, i: int, step: dict, tag: str = "") -> None:
        """--record: where the robot stopped, for measuring after the run -- the
        board's pose and quality, a lidar frame (REST document) and the power
        state. The board's own 'off by' is from its own pose; checking the frame
        against the map later says where the robot really was (28 ต.ค., owner:
        "ทำซ้ำสักห้ารอบดูเพื่อวัดความถูกต้องแม่นยำ")."""
        time.sleep(0.6)                                  # let the base settle
        doc = {"step": i, "spec": step, "t": round(time.monotonic() - self.t0, 1), "pose": list(self.b.pose()),
               "quality": self.b.quality(), "power": self.b.power(),
               "scan": self.b.get("/api/core/system/v1/laserscan")}
        name = "step_%02d%s.json" % (i, "_" + tag if tag else "")
        (Path(self.record_dir) / name).write_text(json.dumps(doc), encoding="utf-8")

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("route_file")
    ap.add_argument("path")
    ap.add_argument("--from-step", type=int, default=1)
    ap.add_argument("--map", action="store_true", help="SLAM mapping on while walking")
    ap.add_argument("--register-dock", action="store_true")
    ap.add_argument("--trace", help="write every creep tick to this JSON-lines file")
    ap.add_argument("--max-s", type=float, help="stop a creep line after this many seconds")
    ap.add_argument("--record", help="write the pose, a lidar frame and the power state at every stop to this folder")
    args = ap.parse_args(argv)
    stop_on_signals()                                    # before anything can move
    doc, steps = load_path(args.route_file, args.path)
    board = Board()
    w = Walker(board, doc, args.map, args.register_dock)
    if args.trace:
        w.trace = open(args.trace, "w", encoding="utf-8")
    if args.max_s:
        global CREEP_MAX_S
        CREEP_MAX_S = args.max_s
    if args.record:
        Path(args.record).mkdir(parents=True, exist_ok=True)
        w.record_dir = args.record
    speed_before = board.speed()
    w.say("path %s: %d steps, speed was %s, Q%d, %s" % (
        args.path, len(steps), speed_before, board.quality(), board.power().get("dockingStatus")))
    code = 0
    try:
        w.run(steps, args.from_step)
        w.say("path %s done" % args.path)
    except Stop as e:
        w.say("STOP: %s" % e)
        code = 2
    except KeyboardInterrupt as e:
        w.say("interrupted%s" % (" (%s)" % e if str(e) else ""))
        code = 130
    finally:
        try:
            if board.current() is not None:
                board.cancel()
        except Exception:                                # noqa: BLE001
            pass
        try:
            if args.map:
                board.mapping(False)
            if speed_before:
                board.set_speed(speed_before)
        except Exception as e:                           # noqa: BLE001
            w.say("(could not put the speed/mapping back: %s)" % e)
        if w.sdk is not None:
            w.sdk.close()
        if w.trace is not None:
            w.trace.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
