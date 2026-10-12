"""Our own position fix for the Slamware chassis, and the route files that use it.

Why this exists (2026-10-06, the first walk from the ฝ่ายอาคาร dock to the big
meeting room and back): the board's own localization cannot be trusted in a
furnished room and cannot be tuned (Aobo locks the config). Switched on, it
pulled the docked robot 0.33 m sideways twice in one afternoon; switched off,
the board drives on wheel odometry alone, which drifted 0.2-0.3 m on a 90°
turn and 9° of heading when the robot brushed a door jamb (there is no IMU).
What got the robot there and back was done by hand: stop, take a lidar frame,
measure the distance to a wall that is *known* to be in the plan, correct the
pose, drive the next straight leg. This module is that procedure, written down.

**Every stop names the walls it may use.** That is the whole safety rule. A
plan-only fit picks whatever line happens to sit near a plan wall, and in
ฝ่ายอาคาร that was a row of cabinets flush with the east wall and a desk edge
0.4 m in front of the south wall — it moved the docked robot's y by 0.6 m in the
wrong direction (the owner's screenshot caught it). An anchor list per stop
makes that impossible: a line that is not near a *named* wall is ignored.

**An axis nobody measured is not corrected.** In a corridor only the two long
walls are visible, so the position along the corridor is unknown; the board's
own AMCL "solves" that by guessing, which is the jump we kept seeing. Here the
along-corridor coordinate keeps its odometry value and the result says so.

**Doors are measured, not assumed.** D11 is 0.856 m between jamb and open leaf
for a 0.55 m robot. A door anchor finds the actual gap in the lidar frame and
corrects the coordinate along the wall, so a push through it is aimed at the
middle of the opening that exists, not the one in the drawing.

Coordinates are the map frame of the head-office plan: metres, x east, y north,
yaw in radians counter-clockwise from east. Robot-frame scan points are x
forward, y left — the board's own `laserscan` convention.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np

#: The robot's real footprint, tape-measured by the owner on 6 ต.ค. 2026 after
#: the D11 contact: base 0.70 m wide x 0.60 m front-back, 0.76-0.78 m across
#: the arms and hands. The navigation board is configured as a 0.55 m disc
#: (`robot_size`, vendor-locked) — 15-23 cm narrower than the robot, which is
#: why its own plans scrape a desk or a door frame. Use these, not the board's.
ROBOT_WIDTH = 0.78
ROBOT_HALF_WIDTH = ROBOT_WIDTH / 2
ROBOT_BASE_WIDTH = 0.70
ROBOT_BASE_LENGTH = 0.60
#: Radius the base corners sweep when turning in place about its centre.
TURN_SWEEP_RADIUS = math.hypot(ROBOT_BASE_WIDTH / 2, ROBOT_BASE_LENGTH / 2)


def turn_sweep_gap(points: np.ndarray, turn: float, *, length: float = ROBOT_BASE_LENGTH,
                   width: float = ROBOT_WIDTH, step_deg: float = 1.0) -> float:
    """Smallest gap (m, negative = overlap) between the lidar returns and the
    robot's own rectangle — `length` x `width`, arms counted along its whole
    length to be safe — while it turns in place by `turn` radians (+ = left),
    the actual direction and angle, not a full circle.

    The circle (0.46 m sweep + 8 cm) was right for a full turn and wrong for
    the rest: on 6 ต.ค. at the dock's landing spot it refused a 13° turn to
    the right because something stood 0.47 m ahead-LEFT — a turn that moved
    every corner away from it. The owner: "ในโปรแกรมมีการกำหนดขนาดเป็น
    สี่เหลี่ยมโดยรอบ ก็เอามาใช้ได้ไหม" — yes. A full turn sweeps the same
    circle as before (corners at 0.49 m with the arms)."""
    p = np.asarray(points, dtype=float).reshape(-1, 2)
    reach = math.hypot(length / 2, width / 2) + 0.30
    p = p[np.hypot(p[:, 0], p[:, 1]) <= reach]
    if not len(p):
        return 9.0
    n = max(2, int(abs(math.degrees(turn)) / step_deg) + 2)
    hl, hw = length / 2, width / 2
    best = 9.0
    for phi in np.linspace(0.0, turn, n):
        # the point as seen from the rectangle after it has turned by phi
        c, s_ = math.cos(phi), math.sin(phi)
        lx, ly = p[:, 0] * c + p[:, 1] * s_, -p[:, 0] * s_ + p[:, 1] * c
        qx, qy = np.abs(lx) - hl, np.abs(ly) - hw
        d = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0)
        best = min(best, float(d.min()))
    return best

#: The directions a lane may run in. The owner asked for straight legs at right
#: angles ("พยายามเดินเป็นแนวตั้งฉาก มันจะได้ไม่ชน") after the board's planner
#: cut a diagonal across ฝ่ายอาคาร and hit a desk. Degrees in the route file
#: because people write them; radians everywhere else.
LANE_HEADINGS_DEG = (0, 90, 180, -90)


def wrap_pi(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def wrap_half_pi(a: float) -> float:
    """A line has no direction: 10° and 190° are the same wall."""
    return (a + math.pi / 2) % math.pi - math.pi / 2


# --------------------------------------------------------------------------
# What a stop may measure against


@dataclass(frozen=True)
class Anchor:
    """A straight wall face from the plan (or measured on site).

    axis "x": the face is the line x = `at`, running north-south, valid for
    y in [lo, hi]. axis "y": the line y = `at`, valid for x in [lo, hi].
    `gate` is how far the odometry guess may be from the truth for a line to
    count as this wall — wider lets in furniture, narrower loses the wall
    after a bad turn. 0.40 m covers every odometry error measured on 6 ต.ค.
    """
    axis: str
    at: float
    lo: float
    hi: float
    gate: float = 0.40

    def __post_init__(self) -> None:
        if self.axis not in ("x", "y"):
            raise ValueError("anchor axis must be 'x' or 'y', got %r" % (self.axis,))
        if not self.lo < self.hi:
            raise ValueError("anchor extent must have lo < hi")


@dataclass(frozen=True)
class Door:
    """An opening in a wall: the wall line (`axis`, `at` — its centre line is
    fine, both faces are searched) and the clear gap [lo, hi] along it.
    `leaf`: the side ("lo"/"hi") where a leaf standing open narrows the way
    through past the frame -- on the other side the jamb IS the passage's
    edge, so a frame seen whole is aimed at from that jamb (7 ต.ค. 14:37,
    D11: aimed at the frame's middle, went in 7 cm toward the leaf and was
    held by it inside the frame)."""
    axis: str
    at: float
    lo: float
    hi: float
    leaf: str | None = None

    def __post_init__(self) -> None:
        if self.axis not in ("x", "y"):
            raise ValueError("door axis must be 'x' or 'y', got %r" % (self.axis,))
        if self.leaf not in (None, "lo", "hi"):
            raise ValueError("door leaf must be 'lo', 'hi' or absent, got %r" % (self.leaf,))
        if not self.lo < self.hi:
            raise ValueError("door gap must have lo < hi")

    @property
    def centre(self) -> float:
        return (self.lo + self.hi) / 2

    @property
    def width(self) -> float:
        return self.hi - self.lo


# --------------------------------------------------------------------------
# Lidar frame -> straight segments


def scan_points(laser: dict[str, Any], max_range: float = 12.0) -> np.ndarray:
    """Robot-frame points (x forward, y left) from a `/system/v1/laserscan`
    document. Invalid returns and anything closer than 5 cm (the robot's own
    shell) are dropped."""
    out = []
    for p in laser.get("laser_points") or ():
        if not isinstance(p, dict) or not p.get("valid"):
            continue
        a, d = p.get("angle"), p.get("distance")
        if isinstance(a, (int, float)) and isinstance(d, (int, float)) and 0.05 < d < max_range:
            out.append((d * math.cos(a), d * math.sin(a)))
    return np.array(out, dtype=float).reshape(-1, 2)


@dataclass
class Segment:
    """A straight run of lidar returns, in the robot frame."""
    cx: float
    cy: float
    angle: float      # direction of the run, radians, robot frame (undirected)
    length: float
    n: int
    ends: tuple[tuple[float, float], tuple[float, float]] = ((0.0, 0.0), (0.0, 0.0))


def extract_segments(points: np.ndarray, *, tol: float = 0.02, min_len: float = 0.5,
                     min_pts: int = 25, max_gap: float = 0.25, max_lines: int = 12,
                     tries: int = 400, seed: int = 0) -> list[Segment]:
    """Sequential RANSAC, then each line's inliers cut where returns are more
    than `max_gap` apart: two cabinets in a row are two segments, and a wall
    with a door in it is two segments either side of the door."""
    rng = np.random.default_rng(seed)
    pts = np.asarray(points, dtype=float).reshape(-1, 2)
    segments: list[Segment] = []
    for _ in range(max_lines):
        if len(pts) < min_pts:
            break
        best = None
        for _ in range(tries):
            i, k = rng.choice(len(pts), 2, replace=False)
            d = pts[k] - pts[i]
            length = math.hypot(d[0], d[1])
            if length < 0.2:
                continue
            normal = np.array([-d[1], d[0]]) / length
            inliers = np.abs((pts - pts[i]) @ normal) < tol
            if best is None or inliers.sum() > best.sum():
                best = inliers
        if best is None or best.sum() < min_pts:
            break
        line = pts[best]
        centre = line.mean(axis=0)
        _, _, vt = np.linalg.svd(line - centre)
        direction = vt[0]
        along = (line - centre) @ direction
        order = np.argsort(along)
        along, line = along[order], line[order]
        cuts = np.nonzero(np.diff(along) > max_gap)[0] + 1
        for piece in np.split(np.arange(len(line)), cuts):
            if len(piece) < min_pts:
                continue
            run = line[piece]
            span = along[piece[-1]] - along[piece[0]]
            if span < min_len:
                continue
            c = run.mean(axis=0)
            _, _, v = np.linalg.svd(run - c)
            dvec = v[0]
            t = (run - c) @ dvec
            segments.append(Segment(
                cx=float(c[0]), cy=float(c[1]),
                angle=float(math.atan2(dvec[1], dvec[0])),
                length=float(t.max() - t.min()), n=int(len(run)),
                ends=((float(c[0] + t.min() * dvec[0]), float(c[1] + t.min() * dvec[1])),
                      (float(c[0] + t.max() * dvec[0]), float(c[1] + t.max() * dvec[1]))),
            ))
        pts = pts[~best]
    return segments


# --------------------------------------------------------------------------
# The fix


@dataclass
class Fix:
    """What one stop measured. `pose` is the corrected pose when `accepted`;
    otherwise the guess, unchanged, and `reason` says why."""
    accepted: bool
    reason: str
    guess: tuple[float, float, float]
    pose: tuple[float, float, float]
    dx: float | None = None          # None = this axis was not measured
    dy: float | None = None
    dyaw: float | None = None
    matches: list[dict[str, Any]] = field(default_factory=list)
    doors: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[dict[str, Any]] = field(default_factory=list)   # runs that disagreed on heading

    def summary(self) -> str:
        def f(v: float | None, unit: str, scale: float = 1.0) -> str:
            return "—" if v is None else ("%+.3f%s" % (v * scale, unit))
        return ("%s dx %s dy %s dyaw %s (%d walls, %d doors)%s" % (
            "OK" if self.accepted else "REJECTED", f(self.dx, " m"), f(self.dy, " m"),
            f(self.dyaw, "°", 180 / math.pi), len(self.matches), len(self.doors),
            "" if self.accepted else " — " + self.reason))


def _rot(yaw: float, x: float, y: float) -> tuple[float, float]:
    c, s = math.cos(yaw), math.sin(yaw)
    return c * x - s * y, s * x + c * y


def _weighted(values: list[tuple[float, float]]) -> tuple[float, float]:
    """Weighted mean and weighted spread (std) of (value, weight) pairs."""
    w = sum(wt for _, wt in values)
    mean = sum(v * wt for v, wt in values) / w
    spread = math.sqrt(sum(wt * (v - mean) ** 2 for v, wt in values) / w)
    return mean, spread


def _match(seg: Segment, pose: tuple[float, float, float], anchors: dict[str, Anchor],
           max_angle: float) -> tuple[str, Anchor] | None:
    x, y, yaw = pose
    wdir = yaw + seg.angle
    ox, oy = _rot(yaw, seg.cx, seg.cy)
    wx, wy = x + ox, y + oy
    e0 = _rot(yaw, *seg.ends[0]); e1 = _rot(yaw, *seg.ends[1])
    ends = ((x + e0[0], y + e0[1]), (x + e1[0], y + e1[1]))
    best = None
    for name, a in anchors.items():
        wall_dir = math.pi / 2 if a.axis == "x" else 0.0
        if abs(wrap_half_pi(wall_dir - wdir)) > max_angle:
            continue
        offset = (wx - a.at) if a.axis == "x" else (wy - a.at)
        if abs(offset) > a.gate:
            continue
        # How much of the segment lies along the anchor's stretch of wall.
        # Measured on the coordinate *along* the wall, with 0.2 m of slack:
        # a cabinet run that continues past the wall's end still overlaps
        # where it overlaps, and that part is honest.
        k = 1 if a.axis == "x" else 0
        s0, s1 = sorted((ends[0][k], ends[1][k]))
        inside = max(0.0, min(s1, a.hi + 0.2) - max(s0, a.lo - 0.2))
        if inside < 0.6 * max(s1 - s0, 1e-9):
            continue
        if best is None or abs(offset) < best[0]:
            best = (abs(offset), name, a)
    return None if best is None else (best[1], best[2])


def _door_gap(points_world: np.ndarray, door: Door, *, band: float = 0.15,
              min_gap: float = 0.5, min_edge_pts: int = 5) -> dict[str, Any] | None:
    """The opening in `door`'s wall as the lidar sees it, in world coordinates
    along the wall. Both jambs must be seen — half a door is not a door."""
    if door.axis == "x":
        near = points_world[np.abs(points_world[:, 0] - door.at) <= band][:, 1]
    else:
        near = points_world[np.abs(points_world[:, 1] - door.at) <= band][:, 0]
    window = near[(near >= door.lo - 0.6) & (near <= door.hi + 0.6)]
    if len(window) < 2 * min_edge_pts:
        return None
    s = np.sort(window)
    gaps = [(s[i], s[i + 1]) for i in range(len(s) - 1) if s[i + 1] - s[i] >= min_gap]
    if not gaps:
        return None
    lo, hi = min(gaps, key=lambda g: abs((g[0] + g[1]) / 2 - door.centre) + abs((g[1] - g[0]) - door.width))
    if (s <= lo).sum() < min_edge_pts or (s >= hi).sum() < min_edge_pts:
        return None
    return {"lo": float(lo), "hi": float(hi), "width": float(hi - lo), "centre": float((lo + hi) / 2)}


def correct_pose(guess: tuple[float, float, float], points: np.ndarray,
                 anchors: dict[str, Anchor], doors: dict[str, Door] | None = None, *,
                 max_angle_deg: float = 12.0, max_shift: float = 0.5, max_turn_deg: float = 15.0,
                 max_spread: float = 0.08, max_turn_spread_deg: float = 3.0,
                 segments: list[Segment] | None = None) -> Fix:
    """Correct `guess` from one stop's lidar points, using only `anchors` and
    `doors`. Heading comes from the angle of every matched wall; position
    comes only along the axes some anchor or door actually constrained."""
    doors = doors or {}
    x, y, yaw = guess
    segs = extract_segments(points) if segments is None else segments
    matched = []
    for seg in segs:
        m = _match(seg, guess, anchors, math.radians(max_angle_deg))
        if m is not None:
            matched.append((seg, m[0], m[1]))
    if not matched and not doors:
        return Fix(False, "no named wall in view", guess, guess)

    dyaw = 0.0
    turns = []
    for seg, _, a in matched:
        wall_dir = math.pi / 2 if a.axis == "x" else 0.0
        turns.append((wrap_half_pi(wall_dir - (yaw + seg.angle)), seg.length))
    # A short run that is not the wall can still sit inside a wall's gate: at
    # the "above D8" stop (6 ต.ค. 13:38) the charger plus column B4 made a
    # 0.53 m line 8.7° off the west wall while the 2.43 m east wall said 0.8°,
    # and the stop was rejected outright. Real walls agree on heading; take
    # the heading the most wall length supports and drop the runs that do not.
    dropped = []
    if len(turns) > 1:
        ranked = sorted(zip(turns, matched), key=lambda tm: tm[0][0])
        total = sum(w for (_, w), _ in ranked)
        acc, median = 0.0, ranked[0][0][0]
        for (t, w), _ in ranked:
            acc += w
            if acc >= total / 2:
                median = t
                break
        keep = [(tw, m) for tw, m in zip(turns, matched) if abs(tw[0] - median) <= math.radians(2.5)]
        dropped = [{"anchor": m[1], "length": round(m[0].length, 2), "turn_deg": round(math.degrees(tw[0]), 1)}
                   for tw, m in zip(turns, matched) if abs(tw[0] - median) > math.radians(2.5)]
        if keep:
            turns = [tw for tw, _ in keep]
            matched = [m for _, m in keep]
    if turns:
        dyaw, turn_spread = _weighted(turns)
        if abs(dyaw) > math.radians(max_turn_deg):
            return Fix(False, "heading off by %.1f° — too far to trust" % math.degrees(dyaw), guess, guess)
        if turn_spread > math.radians(max_turn_spread_deg):
            return Fix(False, "walls disagree on heading (%.1f°)" % math.degrees(turn_spread), guess, guess,
                       dropped=dropped)

    shifts: dict[str, list[tuple[float, float]]] = {"x": [], "y": []}
    report = []
    for seg, name, a in matched:
        ox, oy = _rot(yaw + dyaw, seg.cx, seg.cy)
        seen = (x + ox) if a.axis == "x" else (y + oy)
        shifts[a.axis].append((a.at - seen, seg.length))
        report.append({"anchor": name, "axis": a.axis, "at": a.at, "seen": round(seen, 3),
                       "length": round(seg.length, 2), "n": seg.n})

    pts = np.asarray(points, dtype=float).reshape(-1, 2)
    c, s = math.cos(yaw + dyaw), math.sin(yaw + dyaw)
    world = np.c_[x + c * pts[:, 0] - s * pts[:, 1], y + s * pts[:, 0] + c * pts[:, 1]]
    door_report = []
    # Doors measure the coordinate *along* their wall; whatever the walls said
    # about the other axis is applied first so the band around the wall line
    # is in the right place.
    pre = {k: (_weighted(v)[0] if v else 0.0) for k, v in shifts.items()}
    for name, door in doors.items():
        shifted = world + np.array([pre["x"], pre["y"]])
        gap = _door_gap(shifted, door)
        if gap is None:
            continue
        along = "y" if door.axis == "x" else "x"
        # Weight 2 m: a door's two jambs pin the coordinate harder than any
        # one wall face, and it is the coordinate the push depends on.
        shifts[along].append((door.centre - gap["centre"], 2.0))
        door_report.append({"door": name, "measured": gap, "expected": [door.lo, door.hi]})

    if not report and not door_report:
        return Fix(False, "no named wall or door in view", guess, guess)

    result: dict[str, float | None] = {}
    for axis in ("x", "y"):
        if not shifts[axis]:
            result[axis] = None
            continue
        mean, spread = _weighted(shifts[axis])
        if spread > max_spread:
            return Fix(False, "walls disagree on %s by %.2f m" % (axis, spread), guess, guess,
                       matches=report, doors=door_report)
        if abs(mean) > max_shift:
            return Fix(False, "%s off by %.2f m — too far to trust" % (axis, mean), guess, guess,
                       matches=report, doors=door_report)
        result[axis] = mean
    dyaw_out = dyaw if turns else None
    pose = (x + (result["x"] or 0.0), y + (result["y"] or 0.0), wrap_pi(yaw + (dyaw_out or 0.0)))
    return Fix(True, "", guess, pose, result["x"], result["y"], dyaw_out, report, door_report, dropped)


# --------------------------------------------------------------------------
# Route files


@dataclass
class Step:
    do: str                                   # checkpoint | move | door | dock
    heading_deg: float | None = None
    to: tuple[float, float] | None = None
    use: tuple[str, ...] = ()
    doors: tuple[str, ...] = ()
    door: str | None = None
    until: float | None = None
    label: str = ""
    # a door step: aim at the middle its own passes remembered, this door and
    # this way through (drive_route's door memory), not the drawing's
    remember: bool = False


def door_key(door: str, heading_deg: float) -> str:
    """One door, one way through: "D8@90", "D8@-90". D8's jambs put its middle
    5.6 cm apart from the corridor and from the room (9 ต.ค., calibrate_from_runs:
    -2.391 against -2.443, both MAD under 1.3 cm) -- a memory per door alone
    would average two different things."""
    return "%s@%d" % (door, int(round(heading_deg)))


@dataclass
class DockGap:
    """What beside the charger tells where the robot is across the dock's line,
    seen facing the dock from the pre-dock approach. 8 ต.ค.: nothing in the
    dock's room measures y; every other fix there is x and heading, and every
    half turn on the way puts ~3° the wheels do not see into the heading -- the
    robot came to the charger 4 cm aside and its tail met the column. Facing
    the dock, the lidar sees the bare wall behind it (`wall_x`, over
    `wall_spans` of y) and two faces standing `face_from`..`face_to` m out from
    it with a `width` m gap between them; the gap's two edges read within a
    millimetre frame to frame. The dock's line is put `offset` m north of the
    gap's centre (0: through it -- the owner: "มันไม่ตรงกลางแท่น มันเยื้องมา
    ทางซ้าย", with the board's home line 1.1-1.7 cm south of it)."""
    wall_x: float
    wall_spans: tuple[tuple[float, float], ...]
    face_from: float = 0.03
    face_to: float = 0.16
    width: float = 0.10
    offset: float = 0.0
    # how far out from the wall the face south of the gap (lo) and north of it
    # (hi) stand: each side of a gap must be a run of its own face (A 0.070, B 0.109)
    depth_lo: float | None = None
    depth_hi: float | None = None
    seen_from: float = 0.85          # m, faces to the robot when `width` was read
    # the gap's centre less the centre between the faces' far ends, as measured
    # (0.0225 both looks on 8 ต.ค.): a leg hiding one inner edge moves the gap's
    # centre and not the far ends -- and is invisible itself when it is thin
    outer_offset: float | None = None
    # where the gap's centre is (y, where the faces stand) seen from a dock the
    # robot backed onto itself: how far odometry may be out (max_dy) is counted
    # from here. Counted from the board's home line it was lopsided -- that line
    # runs 1.55 cm south of the gap, so 4.5 cm south already read "no gap" and the
    # robot backed in on odometry, 6 cm off (reviewer)
    centre_y: float | None = None


@dataclass
class Route:
    name: str
    anchors: dict[str, Anchor]
    doors: dict[str, Door]
    steps: list[Step]
    start_from_dock: bool = False
    dock_pose: tuple[float, float, float] | None = None
    # Walls to take the heading (and what else they give) from at the pre-dock
    # spot, after the turn round and before backing in (7 ต.ค. 14:47).
    dock_check: tuple[str, ...] = ()
    dock_gap: DockGap | None = None
    # where a door's middle was measured before the runtime kept its own memory
    # (`door_key` -> the jambs-beside middles of past passes, oldest first, the
    # coordinate across the door): what a `remember` step starts from
    door_seed: dict[str, tuple[float, ...]] = field(default_factory=dict)

    def anchors_for(self, step: Step) -> dict[str, Anchor]:
        return {n: self.anchors[n] for n in step.use}

    def doors_for(self, step: Step) -> dict[str, Door]:
        names = step.doors + ((step.door,) if step.door else ())
        return {n: self.doors[n] for n in names}


#: "move" hands the leg to the board's planner (MoveTo); "drive" keeps it ours:
#: one turn, then straight ahead with our own heading corrections. The
#: owner watched MoveTo weave and swing at the end (6 ต.ค.), so open lanes
#: are moving to "drive".
STEP_KINDS = ("checkpoint", "move", "drive", "door", "dock")


def _heading(raw: Any, where: str) -> float:
    if raw not in LANE_HEADINGS_DEG:
        raise ValueError("%s: heading must be one of %s (straight lanes only), got %r"
                         % (where, LANE_HEADINGS_DEG, raw))
    return float(raw)


def parse_route(doc: dict[str, Any]) -> Route:
    """Validate a route document. Every mistake is a ValueError naming the
    step, because a route file is read once, before the robot moves — a typo
    found at step 7 is a robot standing in the corridor."""
    anchors = {n: Anchor(**a) for n, a in (doc.get("anchors") or {}).items()}
    doors = {n: Door(**d) for n, d in (doc.get("doors") or {}).items()}
    steps = []
    for i, raw in enumerate(doc.get("steps") or []):
        where = "step %d (%s)" % (i + 1, raw.get("label") or raw.get("do"))
        kind = raw.get("do")
        if kind not in STEP_KINDS:
            raise ValueError("%s: 'do' must be one of %s" % (where, STEP_KINDS))
        st = Step(do=kind, label=raw.get("label", ""))
        if "heading" in raw:
            st.heading_deg = _heading(raw["heading"], where)
        if kind in ("move", "drive"):
            to = raw.get("to")
            if not (isinstance(to, list) and len(to) == 2):
                raise ValueError("%s: %s needs 'to': [x, y]" % (where, kind))
            st.to = (float(to[0]), float(to[1]))
            if kind == "drive":
                # walls to measure on the way (drive_route's rolling fixes)
                st.use = tuple(raw.get("use") or ())
        if kind == "checkpoint":
            st.use = tuple(raw.get("use") or ())
            st.doors = tuple(raw.get("doors") or ())
            if not st.use and not st.doors:
                raise ValueError("%s: a checkpoint must name the walls or doors it measures" % where)
        if kind == "door":
            st.door = raw.get("door")
            if st.heading_deg is None or "until" not in raw:
                raise ValueError("%s: door needs 'heading' and 'until'" % where)
            st.until = float(raw["until"])
            st.use = tuple(raw.get("use") or ())
            if raw.get("remember") not in (None, True, False):
                raise ValueError("%s: 'remember' is true or false" % where)
            st.remember = bool(raw.get("remember"))
        elif "remember" in raw:
            raise ValueError("%s: only a door step remembers a middle" % where)
        for n in st.use:
            if n not in anchors:
                raise ValueError("%s: unknown anchor %r" % (where, n))
        for n in st.doors + ((st.door,) if st.door else ()):
            if n not in doors:
                raise ValueError("%s: unknown door %r" % (where, n))
        steps.append(st)
    if not steps:
        raise ValueError("route has no steps")
    dock = doc.get("dock_pose")
    dock_check = tuple(doc.get("dock_check") or ())
    for n in dock_check:
        if n not in anchors:
            raise ValueError("dock_check: unknown anchor %r" % (n,))
    gap = doc.get("dock_gap")
    if gap is not None:
        gap = DockGap(wall_x=float(gap["wall_x"]),
                      wall_spans=tuple((float(a), float(b)) for a, b in gap["wall_spans"]),
                      **{k: float(gap[k]) for k in ("face_from", "face_to", "width", "offset", "depth_lo", "depth_hi",
                                                    "seen_from", "outer_offset", "centre_y")
                         if k in gap})
    seed = {}
    for key, vals in (doc.get("door_memory_seed") or {}).items():
        if key.startswith("_"):
            continue                                # a note
        door, _, h = key.partition("@")
        if door not in doors or not h.lstrip("-").isdigit() or int(h) not in LANE_HEADINGS_DEG:
            raise ValueError("door_memory_seed: %r is not <door>@<heading> of this route" % key)
        if (not isinstance(vals, list) or not vals
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                           for v in vals)):
            raise ValueError("door_memory_seed %s: a list of middles (m)" % key)
        seed[door_key(door, int(h))] = tuple(float(v) for v in vals)
    return Route(name=str(doc.get("name") or "route"), anchors=anchors, doors=doors, steps=steps,
                 start_from_dock=bool(doc.get("start_from_dock")),
                 dock_pose=None if dock is None else (float(dock[0]), float(dock[1]), float(dock[2])),
                 dock_check=dock_check, dock_gap=gap, door_seed=seed)


def dock_wall_fix(points: np.ndarray, pose: tuple[float, float, float], gap: DockGap, *,
                  max_dyaw_deg: float = 10.0, max_dx: float = 0.15, min_span: int = 20,
                  max_spread: float = 0.01, iterations: int = 8, why: list | None = None) -> dict[str, Any] | None:
    """The bare wall behind the charger (route `dock_gap`: `wall_x` over
    `wall_spans`), facing it: {"dx", "dyaw", "spread", "wall_n"} to add to
    `pose` -- the heading and x it gives -- or None (`why` gets the reason).
    The first half of `dock_gap_fix`, on its own for what reads the heading
    facing the dock without the gap (drive_route --dock-turn-test): its
    stretches are 0.40-0.45 m, under what `correct_pose`'s segments need."""
    def no(reason: str) -> None:
        if why is not None:
            why.append(reason)
        return None

    p = np.asarray(points, dtype=float).reshape(-1, 2)
    if len(p) < min_span * max(1, len(gap.wall_spans)):
        return no("too few returns")
    x, y, yaw = pose

    def world(yw: float, dx: float = 0.0) -> np.ndarray:
        c, s = math.cos(yw), math.sin(yw)
        return np.c_[x + dx + c * p[:, 0] - s * p[:, 1], y + s * p[:, 0] + c * p[:, 1]]

    def bare(w: np.ndarray, band: float = 0.04) -> np.ndarray | None:
        on = np.abs(w[:, 0] - gap.wall_x) <= band
        keep = np.zeros(len(w), dtype=bool)
        for lo, hi in gap.wall_spans:
            span = on & (w[:, 1] >= lo) & (w[:, 1] <= hi)
            if span.sum() < min_span:                  # every stretch, or the slope is a guess
                return None
            keep |= span
        return w[keep]

    # Heading and x from the bare wall, read again until they settle: read once
    # from the believed pose, the ±5 cm band cut a wall tilted by a wrong heading
    # unevenly and the answer kept up to 3° of it -- 8.7 cm across at 1.77 m
    # (reviewer, heading +6° and x +2 cm out). x = a + b (y - y_robot); a heading
    # too far anticlockwise leans the wall b = -tan(e) -- add atan(b).
    # Wide first, then narrower: a heading 5° out puts the far ends of the wall's
    # stretches 6-9 cm off it (refused 2 times in 3 with one ±5 cm band); the
    # last reads are ±4 cm, and a wall that does not settle there is not taken.
    dyaw = dx = 0.0
    for it in range(iterations):
        q = bare(world(yaw + dyaw, dx), (0.12, 0.08, 0.05)[it] if it < 3 else 0.04)
        if q is None:
            return no("the wall behind: a stretch out of sight")
        b, a = np.polyfit(q[:, 1] - y, q[:, 0], 1)
        dyaw += math.atan(b)
        dx += gap.wall_x - a
        if abs(dyaw) > math.radians(max_dyaw_deg) or abs(dx) > max_dx:
            return no("the wall behind: %.1f° / %.1f cm out" % (math.degrees(dyaw), dx * 100))
        if abs(b) < math.tan(math.radians(0.02)) and abs(gap.wall_x - a) < 0.0005:
            break
    else:
        return no("the wall behind: did not settle")
    q = bare(world(yaw + dyaw, dx))
    if q is None:
        return no("the wall behind: a stretch out of sight")
    b, a = np.polyfit(q[:, 1] - y, q[:, 0], 1)
    spread = float(np.std(q[:, 0] - (a + b * (q[:, 1] - y))))
    if spread > max_spread:                            # not one straight wall
        return no("the wall behind: not straight (%.1f cm)" % (spread * 100))
    return {"dx": float(dx), "dyaw": float(dyaw), "spread": spread, "wall_n": int(len(q))}


def dock_gap_fix(points: np.ndarray, pose: tuple[float, float, float], gap: DockGap,
                 line_y: float, *, max_dyaw_deg: float = 10.0, max_dx: float = 0.15,
                 max_dy: float = 0.07, min_span: int = 20, max_spread: float = 0.01,
                 min_edge: int = 15, iterations: int = 8, why: list | None = None) -> dict[str, Any] | None:
    """Where one look at the charger's surroundings (`gap`), facing the dock,
    puts the robot: the bare wall behind gives the heading and x, the gap
    between the two faces the y -- shifted so that the gap's centre (plus
    `gap.offset`) lands on `line_y`, the dock's line where it crosses the
    faces (wall_x + (face_from + face_to) / 2: the line runs 5° off the wall's
    normal, 0.8 cm across in the faces' 9 cm).
    {"dx", "dy", "dyaw", "centre", "width", "outer", "spread", "wall_n"} to add
    to `pose` ("outer": the centre between the faces' far ends, logged -- which
    centre the contacts sit on is the owner's to say), or None when the wall or
    the gap is not plainly there -- no guess; `why` (a list) gets the reason,
    "far: ..." when the gap is plainly there but further off than `max_dy`
    (odometry that far out is not something to back in on). Only for a wall on
    the room's west side (axis x, the room to its east), which is where this dock is."""
    def no(reason: str) -> None:
        if why is not None:
            why.append(reason)
        return None

    p = np.asarray(points, dtype=float).reshape(-1, 2)
    x, y, yaw = pose
    wall = dock_wall_fix(p, pose, gap, max_dyaw_deg=max_dyaw_deg, max_dx=max_dx, min_span=min_span,
                         max_spread=max_spread, iterations=iterations, why=why)
    if wall is None:
        return None
    dx, dyaw, spread = wall["dx"], wall["dyaw"], wall["spread"]

    def world(yw: float, dx: float = 0.0) -> np.ndarray:
        c, s = math.cos(yw), math.sin(yw)
        return np.c_[x + dx + c * p[:, 0] - s * p[:, 1], y + s * p[:, 0] + c * p[:, 1]]

    w = world(yaw + dyaw, dx)
    band = w[(w[:, 0] >= gap.wall_x + gap.face_from) & (w[:, 0] <= gap.wall_x + gap.face_to)
             & (np.abs(w[:, 1] - line_y) <= 0.40)]
    if len(band) < 2 * min_edge:
        return no("no faces")
    order = np.argsort(band[:, 1])
    ys, xs = band[order, 1], band[order, 0]
    # Each edge reads where the lidar's dropout beside it ends -- 0.7-1.4° with no
    # returns at every edge in the real frames -- so measured from further than
    # `gap.seen_from` (where `width` was read) the gap reads wider, ~1 cm a side
    # per metre for an angle that size, and nearer, narrower.
    r = (x + dx) - (gap.wall_x + (gap.face_from + gap.face_to) / 2.0)
    give = 2.0 * (r - gap.seen_from) * math.tan(math.radians(1.5))
    narrow, wide = gap.width - 0.010 + min(0.0, give), gap.width + 0.015 + max(0.0, give)
    expect = gap.centre_y if gap.centre_y is not None else line_y - gap.offset
    found, far_off = [], []
    # what the holes of the gap's width were refused for, said with "no gap" (9 ต.ค.:
    # both looks of the day's last run said only that, no frame logged -- the scene
    # could not be told from a broken look)
    tally = {"width": 0, "run": 0, "lo": [], "hi": []}
    for i in range(min_edge - 1, len(ys) - min_edge):
        lo, hi = ys[i], ys[i + 1]
        if not narrow <= hi - lo <= wide:
            continue
        tally["width"] += 1
        centre = (lo + hi) / 2.0
        # A hole that size in a face is not the gap: a chair leg's shadow made
        # one, taken 11-19 cm off (reviewer, 107 of ~20k legs). Each side must be
        # an unbroken run of face at that face's own depth.
        Ly, Lx = ys[i - min_edge + 1:i + 1], xs[i - min_edge + 1:i + 1]
        Hy, Hx = ys[i + 1:i + 1 + min_edge], xs[i + 1:i + 1 + min_edge]
        if np.diff(Ly).max() > 0.01 or np.diff(Hy).max() > 0.01:
            tally["run"] += 1
            continue
        if gap.depth_lo is not None and np.abs(Lx - (gap.wall_x + gap.depth_lo)).max() > 0.015:
            tally["lo"].append(float(np.median(Lx) - (gap.wall_x + gap.depth_lo)))
            continue
        if gap.depth_hi is not None and np.abs(Hx - (gap.wall_x + gap.depth_hi)).max() > 0.015:
            tally["hi"].append(float(np.median(Hx) - (gap.wall_x + gap.depth_hi)))
            continue
        (found if abs(centre - expect) <= max_dy else far_off).append((i, lo, hi, centre))
    if not found and len(far_off) == 1:
        return no("far: the gap is %.1f cm from where it should be" % ((far_off[0][3] - expect) * 100))
    if len(found) != 1:                                # two candidates: which one is a guess
        if found:
            return no("%d gaps" % len(found))
        said = ["%d hole(s) %.1f-%.1f cm wide" % (tally["width"], narrow * 100, wide * 100)]
        if tally["run"]:
            said.append("%d beside a broken run" % tally["run"])
        for side, name in (("lo", "face south of it"), ("hi", "face north of it")):
            if tally[side]:
                said.append("%d with the %s %+.1f cm off its depth" % (len(tally[side]), name,
                                                                        float(np.median(tally[side])) * 100))
        if far_off:
            said.append("%d further than %.0f cm from where it should be" % (len(far_off), max_dy * 100))
        return no("no gap (%s)" % "; ".join(said))
    i, lo, hi, centre = found[0]
    # Anything nearer than the faces between the robot and the gap's edges hides
    # where a face ends: a leg's shadow moved an edge 1-2 cm and the run checks
    # cannot see it (the leg stands in front of the band). No fix.
    X, Y, H = x + dx, y, yaw + dyaw                    # lo, hi and w are all in the believed y
    def bearing(px: np.ndarray | float, py: np.ndarray | float) -> np.ndarray:
        return (np.arctan2(np.asarray(py) - Y, np.asarray(px) - X) - H + math.pi) % (2 * math.pi) - math.pi
    near_x = gap.wall_x + gap.face_to + 0.03
    a1, a2 = sorted((float(bearing(near_x, lo - 0.05)), float(bearing(near_x, hi + 0.05))))
    b_ = bearing(w[:, 0], w[:, 1])
    if np.sum((w[:, 0] > near_x) & (b_ >= a1) & (b_ <= a2)) >= 3:
        return no("something in front of the gap's edges")
    # the faces' far ends, walking each run out from the gap (logged only)
    lx, hx = xs[i - min_edge + 1:i + 1].mean(), xs[i + 1:i + 1 + min_edge].mean()
    j = i
    while j > 0 and ys[j] - ys[j - 1] <= 0.01 and abs(xs[j - 1] - lx) <= 0.015:
        j -= 1
    k = i + 1
    while k + 1 < len(ys) and ys[k + 1] - ys[k] <= 0.01 and abs(xs[k + 1] - hx) <= 0.015:
        k += 1
    outer = float((ys[j] + ys[k]) / 2.0)
    if gap.outer_offset is not None and abs((centre - outer) - gap.outer_offset) > 0.01:
        return no("the faces' far ends do not agree (%.1f cm)" % ((centre - outer - gap.outer_offset) * 100))
    return {"dx": float(dx), "dy": float(line_y - (centre + gap.offset)), "dyaw": float(dyaw),
            "centre": float(centre), "width": float(hi - lo), "outer": outer,
            "spread": spread, "wall_n": wall["wall_n"]}


def load_route(path: str | Path) -> Route:
    return parse_route(json.loads(Path(path).read_text(encoding="utf-8")))


def lane_heading(frm: tuple[float, float], to: tuple[float, float], tol_deg: float = 20.0) -> float | None:
    """The axis heading (radians) from `frm` to `to`, or None when the two
    points are not on one lane — a diagonal leg is what the owner asked us
    to stop doing, so the runner refuses rather than rounds it."""
    dx, dy = to[0] - frm[0], to[1] - frm[1]
    if math.hypot(dx, dy) < 0.05:
        return None
    ang = math.degrees(math.atan2(dy, dx))
    for h in LANE_HEADINGS_DEG:
        if abs((ang - h + 180) % 360 - 180) <= tol_deg:
            return math.radians(h)
    return None


#: The base seen from above is rounded, not the 0.70 x 0.60 box the owner's
#: tape gives. 7 ต.ค. 14:05, D8: the west jamb 0.10 m ahead of the centre read
#: 0.349 m from the centre line -- 1 mm inside the box -- and the owner, on
#: the spot: "ไม่แตะ มีช่อง". A box touches there. The side as a superellipse
#: |x/0.30|^n + |y/0.35|^n = 1 leaves a gap only for n <= 3 (n = 3: 0.3456 at
#: x = 0.10); n = 3 is the squarest outline the walk allows. Only for STOPS
#: by what is beside the robot -- where along the body a jamb is decides the
#: real gap. Lateral position (door_lateral) still reads the box: it is the
#: distance of the jamb from the centre line, whatever the outline.
BASE_SHAPE_N = 3.0


def base_shape(x: np.ndarray | float) -> np.ndarray | float:
    """Fraction of the base's widest half-width at `x` m ahead (+) or behind
    (-) the centre: 1 at the middle, 0 at the ends (ROBOT_BASE_LENGTH)."""
    t = np.clip(np.abs(x) / (ROBOT_BASE_LENGTH / 2.0), 0.0, 1.0)
    return (1.0 - t ** BASE_SHAPE_N) ** (1.0 / BASE_SHAPE_N)


def side_clearance(points: np.ndarray, *, half_width: float = ROBOT_HALF_WIDTH, beside: float = 0.30,
                   min_pts: int = 3, shaped: bool = False) -> tuple[float | None, float | None]:
    """Gap between the robot's sides and the nearest return beside it, for
    a push through a doorway: (left, right) in metres, None = nothing beside.

    This is the check the first D11 push did not have (6 ต.ค. 13:12): its
    drift guard read odometry, which cannot see the robot sliding toward the
    leaf, and it hit the door. The lidar sees the jambs directly. `beside`
    is how far ahead/behind the robot's centre counts as "beside" — the
    jamb is a 0.10 m wall, so ±0.30 m catches it from just before the
    robot's widest point until just after. `half_width` is the measured
    half-width across the arms; the contact frame read 0.33 m to the frame,
    i.e. -0.06 m with this — touching, which is what happened. `shaped`: the
    side as the base's rounded outline (`base_shape`), for stops."""
    p = np.asarray(points, dtype=float).reshape(-1, 2)
    near = p[np.abs(p[:, 0]) <= beside]

    def side(sel: np.ndarray) -> float | None:
        if len(sel) < min_pts:
            return None
        # The few closest returns, not the single closest: one stray point is
        # dust on the window, three in a row is a door frame.
        hw = half_width * base_shape(sel[:, 0]) if shaped else half_width
        d = np.sort(np.abs(sel[:, 1]) - hw)[min_pts - 1]
        return float(d)

    return side(near[near[:, 1] > 0]), side(near[near[:, 1] < 0])


def front_distance(points: np.ndarray, *, half_width: float = ROBOT_HALF_WIDTH, margin: float = 0.05,
                   max_range: float = 3.0, min_pts: int = 3, from_x: float = ROBOT_BASE_LENGTH / 2) -> float | None:
    """Distance ahead (from the lidar) to the nearest return inside the
    robot's own width plus `margin` a side — the strip it will drive
    through. None = nothing within `max_range`. A cone test (the board's
    own "front arc") misses a chair leg 0.35 m to the side 0.5 m ahead,
    which a 0.78 m robot hits. Only returns past the robot's front face
    (`from_x`) count: one beside it is the side check's business — on 7 ต.ค.
    D11's open leaf, 0.38 m to the side and 5 cm ahead of the centre, read
    as "something 0.05 m ahead" and would have held the robot where it could
    neither turn nor move on."""
    ahead = _strip_ahead(np.asarray(points, dtype=float).reshape(-1, 2), half_width + margin, from_x, max_range)
    if len(ahead) < min_pts:
        return None
    return float(ahead[min_pts - 1])


def _strip_ahead(p: np.ndarray, half_width: float, from_x: float, to_x: float) -> np.ndarray:
    """How far ahead (x, sorted) the returns are that lie in the strip a body
    `half_width` a side sweeps going straight forward, past `from_x` and short
    of `to_x`. The one place this is computed: `front_distance` (the lane's and
    the doorway's stop) and `free_space` (every other heading) both read it, so
    the speed a lane is allowed and the walkable directions a screen shows
    cannot come to disagree about what is in the way."""
    sel = (p[:, 0] > from_x) & (p[:, 0] < to_x) & (np.abs(p[:, 1]) <= half_width)
    return np.sort(p[sel, 0])


#: The lidar's arc as the unit reports it (±103°, 6 ต.ค.): a heading outside it
#: has no returns at all, and "nothing seen" there is not "free".
LIDAR_HALF_ARC_DEG = 103.0


def free_space(points: np.ndarray, width: float = ROBOT_WIDTH, *, length: float = ROBOT_BASE_LENGTH,
               step_deg: float = 5.0, max_m: float = 3.0, side_margin: float = 0.0, keep: float = 0.0,
               min_pts: int = 3, half_arc_deg: float = LIDAR_HALF_ARC_DEG) -> list[tuple[float, float | None]]:
    """Which ways the robot can go from where it stands, and how far, from one
    lidar frame (robot frame: x forward, y left) -- the owner, 9 ต.ค.: "ดูผลจาก
    เรดาห์หรือเลเซอร์กับทำให้รู้ไหมว่ามุมไหนเดินได้หรือไม่ได้".

    [(angle_deg, free_m)] every `step_deg` round the full circle (-180 up to
    180, + = left, 0 = straight ahead): how far the robot's rectangle --
    `length` front to back about the turning centre, `width` across (0.70 the
    base, 0.78 with the arms out), plus `side_margin` a side -- goes straight
    that way, once facing it, before the `min_pts`-th return enters it (one
    stray return is dust on the window, three in a row a chair leg), less
    `keep`; capped at `max_m`, never below 0. None where the heading is outside
    the lidar's arc (behind the robot nothing is seen, which is not free).
    Near the arc's ends the strip's outer near corner is beyond what the lidar
    covers, so there the figure is what it sees, not more.

    Only what is ahead of the front face counts, as for `front_distance` (at 0°
    this IS front_distance less half the length): what is beside the body is
    `side_clearance`'s, and whether the robot can turn to face a heading is
    `turn_sweep_gap(points, radians(angle), width=width)` -- a heading is
    walkable when that turn has room and `free_m` is enough. numpy only, so the
    server can call it on a /robot/laserscan frame."""
    p = np.asarray(points, dtype=float).reshape(-1, 2)
    n = max(1, int(round(360.0 / step_deg)))
    half, face = width / 2.0 + side_margin, length / 2.0
    out: list[tuple[float, float | None]] = []
    for k in range(n):
        ang = -180.0 + k * step_deg
        if abs(ang) > half_arc_deg + 1e-9:
            out.append((ang, None))
            continue
        a = math.radians(ang)
        c, s = math.cos(a), math.sin(a)
        q = np.c_[p[:, 0] * c + p[:, 1] * s, -p[:, 0] * s + p[:, 1] * c]
        ahead = _strip_ahead(q, half, face, face + max_m + keep)
        free = max_m if len(ahead) < min_pts else min(max_m, max(0.0, float(ahead[min_pts - 1]) - face - keep))
        out.append((ang, round(free, 3)))
    return out


def stop_speed(free: float, latency: float, decel: float) -> float:
    """The fastest the base may go with `free` m to where it must have stopped:
    it goes on `latency` s at that speed before braking shows (the link, the
    board, the frame's age, the loop's tick), then brakes at `decel` --
    v * latency + v^2 / (2 decel) = free."""
    if free <= 0.0 or decel <= 0.0:
        return 0.0
    at = decel * latency
    return -at + math.sqrt(at * at + 2.0 * decel * free)


def gap_ahead(points: np.ndarray, *, max_face: float = 2.0, band: float = 0.15, min_gap: float = 0.5,
              side_from: float = 0.30, side_to: float = 1.4, min_edge_pts: int = 5) -> dict[str, float] | None:
    """The opening in the wall in front of the robot, in the ROBOT frame:
    face = distance ahead to the wall, lo/hi = lateral edges (+ = left),
    centre, width. None when no opening of at least `min_gap` is seen.

    Robot frame on purpose. The first door check looked for the jambs in a
    band around the drawing's wall line in world coordinates, and lost them
    when the robot's y was ~15 cm off (6 ต.ค. 13:48, in front of D8) — the
    thing that matters for the push is where the gap is relative to the
    robot, and the lidar measures exactly that. The wall face is the near
    edge (15th percentile) of returns 0.3-1.4 m to either side: wall on both
    sides of the opening, so a person in the middle of the doorway does not
    move it. Not the median: 0.26 m from D11 the lidar did not see the wall
    at steep angles at all (the robot's own shell is in the way), and the
    median of what was left was a cabinet 1.2 m away."""
    p = np.asarray(points, dtype=float).reshape(-1, 2)
    ahead = p[(p[:, 0] > 0.05) & (p[:, 0] < max_face) & (np.abs(p[:, 1]) < side_to)]
    side = ahead[(np.abs(ahead[:, 1]) >= side_from)]
    if len(side) < 2 * min_edge_pts:
        return None
    # The wall is the distance at which there are returns on BOTH sides of
    # the opening. An open leaf standing at right angles beside the door (D8
    # from inside ฝ่ายอาคาร) is a run of returns on one side only, stretching
    # toward the robot, and a percentile of one pile of points picked it as
    # the "wall" (6 ต.ค. 14:41). Vote in 5 cm bins; score = the smaller side.
    left, right = side[side[:, 1] > 0][:, 0], side[side[:, 1] < 0][:, 0]
    edges = np.arange(0.05, max_face + 0.05, 0.05)
    lc, _ = np.histogram(left, edges)
    rc, _ = np.histogram(right, edges)
    both = np.minimum(lc, rc)
    if both.max() < min_edge_pts:
        return None
    k = int(np.argmax(both))                  # first (nearest) bin with the best two-sided support
    near = side[(side[:, 0] >= edges[k] - 0.05) & (side[:, 0] < edges[k + 1] + 0.05)]
    face = float(np.median(near[:, 0]))
    wall = ahead[np.abs(ahead[:, 0] - face) <= band]
    s = np.sort(wall[:, 1])
    gaps = [(s[i], s[i + 1]) for i in range(len(s) - 1) if s[i + 1] - s[i] >= min_gap]
    if not gaps:
        return None
    lo, hi = min(gaps, key=lambda g: abs((g[0] + g[1]) / 2))
    if (s <= lo).sum() < min_edge_pts or (s >= hi).sum() < min_edge_pts:
        return None
    return {"face": face, "lo": float(lo), "hi": float(hi), "width": float(hi - lo), "centre": float((lo + hi) / 2)}


# --------------------------------------------------------------------------
# Smooth driving with the SDK's velocity control (app/slamware_sdk.py)


@dataclass(frozen=True)
class DriveLimits:
    """Speeds for velocity-controlled lanes. Cruise 0.35 m/s is 40% faster
    than the 0.25 the burst driving used; the board's own max is 0.5. The
    stop distances are from the lidar, which sits near the robot's centre
    while the base reaches 0.30 m ahead of it."""
    v_max: float = 0.35
    w_max: float = 0.6
    accel: float = 0.35          # m/s² speeding up
    decel: float = 0.40          # m/s² planned braking into the target
    brake: float = 1.0           # m/s² allowed when something appears ahead
    w_accel: float = 1.5         # rad/s²
    lookahead: float = 0.8       # m — pursuit point ahead on the lane
    latency: float = 0.25        # s — command lag to anticipate when braking (relay + board)
    align_deg: float = 25.0      # heading error above which we turn on the spot first
    stop_tol: float = 0.02
    front_stop: float = 0.50     # obstacle this close in the robot's strip: speed 0
    front_slow: float = 1.20     # ... and from here, slow down linearly
    # Speed allowed at front_slow, tapering to 0 at front_stop. Its own number,
    # not a fraction of v_max: the first real run at --vmax 0.2 (6 ต.ค. 15:14)
    # crawled the whole leg at 0.06-0.18 m/s because a lane that ends 0.6 m
    # from a wall spends all of it inside the slow zone, and the zone was
    # scaled by the already-lowered top speed — slowed twice for one wall.
    front_v: float = 0.35
    # Slowest forward speed while there is lane left and nothing at the stop
    # distance. Without it the braking curve and the lag anticipation fed each
    # other 3 cm from the target: 0 -> 0.035 -> 0.059 -> 0, pulses too short
    # for the base to move, for 8 s (6 ต.ค. 15:33). 0.05 m/s for 1 s moved it
    # 3.5 cm off the dock, so the base does go at this speed.
    v_creep: float = 0.05
    # Turn commands smaller than this do nothing while driving: the casters
    # have to swivel before two drive wheels can turn the base. Measured
    # driving at 0.2 m/s (6 ต.ค. 16:04, constant turns 1-1.4 s): 0.04, 0.05,
    # 0.06 rad/s turned it 0.0-0.2°; 0.08 turned it 4.8° in a second, 155% of
    # what was asked — nothing until the casters break free, then more. A
    # correction is sent as what is wanted PLUS this. Deliberately UNDER the
    # breakaway: compensating up to it held the lane to 0.8 cm in simulation
    # by flipping left/right 18-20 times a lane (casters swung round on every
    # flip — the scraping); at 0.05 only offsets that grow get corrected,
    # within ~3 cm, with 0-1 flips. (The first value, 0.03, came from mixed
    # windows of the lane logs and left a 4.7 cm drift uncorrected.)
    w_deadband: float = 0.05
    w_on: float = 0.006          # below this a correction is not worth swivelling for
    # Turning ON THE SPOT, measured on the robot (6 ต.ค., ±0.06…±0.35 rad/s):
    # 0.06 and 0.10 turned it 0° — the casters have to be dragged sideways,
    # far more than while rolling; 0.15 gave ~8°/s, 0.35 ~16°/s; from rest it
    # starts 0.4-0.7 s late; after a zero it coasts ~a third of a second's
    # worth (1.6-2.6° from 8°/s, 5-5.7° from 16°/s). So two rates, and the
    # zero goes out early by the coast.
    # Cruise 0.5, not 0.35 (7 ต.ค., owner: "ทำให้ไวกว่านี้"): a round had ~14
    # spot turns at ~8 s per 90° / 13 s per 180°, 4.8 s of each 90° at the
    # cruise rate. The last spin_slow_deg is still turned at w_spin_min, so the
    # coast into the angle -- and where the corners stop -- does not change;
    # simulated on SpotTurnBase: 90° 8.4 -> 6.7 s, 180° 14.0 -> 10.7 s, error
    # within 0.2° either way.
    w_spin: float = 0.5
    w_spin_min: float = 0.15
    spin_slow_deg: float = 15.0  # switch to w_spin_min this close to the angle
    spin_coast_s: float = 0.26   # coast angle = this × commanded rate (0.35 -> 5.2°, 0.15 -> 2.2°)
    w_spin_fast: float = 0.0     # rad/s, while more than spin_fast_until_deg is left (0 = off)
    spin_fast_until_deg: float = 45.0
    # The stop the base can really make for something in the strip ahead
    # (`stop_speed`), on top of the front_slow taper: the speed is held to what
    # stops it by front_stop from where the strip says it is free, braking at
    # `stop_decel` after `stop_latency`. 0 = off (the doorway limits, and every
    # caller that does not ask for it, are as they were). drive_route's
    # lane_limits sets it from the logs: 0.3 s from a command to the wheels
    # answering, up to 0.2 s of frame age and a 0.1 s tick; braking 0.4-0.8
    # m/s^2 seen when 0.5-0.6 was asked (median 0.56, 7-9 ต.ค. lanes) -- 0.5.
    stop_latency: float = 0.0
    stop_decel: float = 0.0
    # No integral term on purpose: one was tried in simulation (6 ต.ค.) against
    # pull, dead-zone error and lag, and the worst lateral offset stayed ~6 cm
    # with ki 0.6-2.0 while flips went up; the real lanes show no pull to undo.


@dataclass
class LaneCommand:
    vx: float
    omega: float
    done: bool
    phase: str
    rem: float = 0.0             # metres left along the lane
    lat: float = 0.0             # metres off the lane, + = left
    err: float = 0.0             # heading error to the pursuit point, radians
    # what held the speed under the lane's own: "front" the taper for something
    # in the strip ahead, "stop" the stop it can really make (stop_speed), ""
    # nothing ahead did -- for the run log ("where it slowed, and for what")
    why: str = ""


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def lane_command(pose: tuple[float, float, float], start: tuple[float, float], target: tuple[float, float],
                 prev: tuple[float, float] = (0.0, 0.0), dt: float = 0.1, front: float | None = None,
                 limits: DriveLimits = DriveLimits()) -> LaneCommand:
    """Speed and turn rate to follow the straight lane start→target and stop on
    the target: pure pursuit on a point `lookahead` ahead on the lane, speed
    from the distance left (so it brakes smoothly instead of overshooting and
    swinging back, which is what the board's MoveTo did at every leg end), slowed
    by whatever the lidar sees in the robot's own strip, and rate-limited so
    every change is a ramp. Large heading errors are turned out on the spot
    first — a 0.70 x 0.60 m base swinging wide in a 1.6 m corridor is worse."""
    x, y, yaw = pose
    hx, hy = target[0] - start[0], target[1] - start[1]
    n = math.hypot(hx, hy)
    if n < 1e-6:
        return LaneCommand(0.0, 0.0, True, "arrived")
    ux, uy = hx / n, hy / n
    rem = (target[0] - x) * ux + (target[1] - y) * uy
    lat = -(x - target[0]) * uy + (y - target[1]) * ux
    if rem <= limits.stop_tol:
        return LaneCommand(0.0, 0.0, True, "arrived", rem, lat, 0.0)
    # The pursuit point stays a full lookahead ahead, past the target on the
    # same line if need be. Clamped to the target, it closed in as the lane
    # ran out and the last centimetre of offset was steered at a steep angle:
    # 0.3 cm from the line but 8.5° off it at the stop (6 ต.ค. 15:31).
    ahead = limits.lookahead
    px, py = target[0] - (rem - ahead) * ux, target[1] - (rem - ahead) * uy
    err = wrap_pi(math.atan2(py - y, px - x) - yaw)
    pv, pw = prev
    why = ""
    if abs(err) > math.radians(limits.align_deg):
        phase, v_t = "align", 0.0
        w_t = _clamp(1.5 * err, -limits.w_max, limits.w_max)
    else:
        phase = "drive"
        # Brake for where the robot will be when this command lands, not where it
        # is: in simulation a 0.25 s lag alone overshot the target by 4 cm, 7.5 cm
        # at 0.3 s.
        rem_eff = rem - pv * limits.latency - limits.stop_tol
        v_t = min(limits.v_max, math.sqrt(2 * limits.decel * max(rem_eff, 0.0)))
        if front is not None:
            span = max(limits.front_slow - limits.front_stop, 1e-6)
            v_lane = v_t
            v_t = min(v_t, limits.front_v * _clamp((front - limits.front_stop) / span, 0.0, 1.0))
            why = "front" if v_t < v_lane - 1e-9 else ""
            if limits.stop_decel > 0.0:
                v_stop = stop_speed(front - limits.front_stop, limits.stop_latency, limits.stop_decel)
                if v_stop < v_t - 1e-9:
                    v_t, why = v_stop, "stop"
        v_t *= max(0.0, math.cos(err))
        if front is None or front > limits.front_stop:
            v_t = max(v_t, limits.v_creep)
        ld = max(math.hypot(px - x, py - y), 0.2)
        curvature = 2.0 * math.sin(err) / ld
        w_t = v_t * curvature if v_t >= limits.v_creep * 0.99 else 1.5 * err
        w_t = _clamp(w_t, -limits.w_max, limits.w_max)
        w_t = _through_casters(w_t, pw, limits)
    down = (limits.brake if (front is not None and front < limits.front_slow) or why == "stop"
            else limits.decel * 2)
    vx = _clamp(v_t, pv - down * dt, pv + limits.accel * dt)
    vx = max(vx, 0.0)
    omega = _clamp(w_t, pw - limits.w_accel * dt, pw + limits.w_accel * dt)
    return LaneCommand(vx, omega, False, phase, rem, lat, err, why)


def _through_casters(w: float, prev_w: float, limits: DriveLimits) -> float:
    """The turn rate to SEND so the base turns at `w`: add the casters' dead
    zone, and drop corrections too small to be worth it — with a little
    hysteresis while already turning that way, so a correction that is
    fading does not switch on and off. A change of direction always goes
    through zero first: each one swivels the casters round again, which is the
    scraping the owner heard on every turn (6 ต.ค.)."""
    if limits.w_deadband <= 0:
        return w
    same_way = prev_w != 0.0 and (w > 0) == (prev_w > 0)
    if abs(w) < (limits.w_on / 2 if same_way else limits.w_on):
        return 0.0
    if prev_w != 0.0 and not same_way:
        return 0.0
    return math.copysign(min(limits.w_max, abs(w) + limits.w_deadband), w)


def turn_command(yaw: float, target_yaw: float, prev_w: float = 0.0, dt: float = 0.1,
                 limits: DriveLimits = DriveLimits(), tol_deg: float = 2.0) -> LaneCommand:
    """Turn on the spot to `target_yaw`: `w_spin` until `spin_slow_deg` away,
    then `w_spin_min` — the slowest rate that turns this base at all — and the
    zero is sent early by the angle it will coast. The first version scaled
    the rate down with the error and asked for 0.08 rad/s near the angle,
    which turns it 0°: 90° took 27 s and ended 3-4° off (6 ต.ค. 15:23). Done
    with a zero command; the caller lets the base settle and checks."""
    err = wrap_pi(target_yaw - yaw)
    a = abs(err)
    toward = prev_w != 0.0 and (prev_w > 0) == (err > 0)
    if toward and a <= abs(prev_w) * limits.spin_coast_s:
        return LaneCommand(0.0, 0.0, True, "coasting in", err=err)
    # `tol_deg` decides whether to turn at all, not where a turn under way
    # stops: stopping the moment it was within 5° left every 90° turn before a
    # stop 2.7-3.7° short (it coasts ~1.7° at w_spin_min; 7-8 ต.ค., 60 turns),
    # and each door then turned again for its 2° -- ~2 s, four times a round.
    if a <= math.radians(tol_deg) and not toward:
        return LaneCommand(0.0, 0.0, True, "aligned", err=err)
    if prev_w != 0.0 and not toward:
        # past it: stop first. A reversal swings the casters round again.
        return LaneCommand(0.0, 0.0, False, "turn", err=err)
    # Faster (`w_spin_fast`, `--spin`) only while more than spin_fast_until_deg
    # is left, then the cruise this base was measured at (w_spin) before the
    # slow end: the 0.5 rad/s approach to the angle is the part that was
    # validated. One fast cruise slowing at 20° was fine on the measured base
    # model and went 4-12° past with a 0.3 s lag or a base that brakes at
    # 0.5-0.7 rad/s² (reviewer); this two-stage way stayed within 0.5° on all
    # six models, 90° in 4.7 s (5.5) and 180° in 7.3 (9.3). Short turns: as before.
    if limits.w_spin_fast > limits.w_spin and a > math.radians(limits.spin_fast_until_deg):
        level = limits.w_spin_fast
        cap = max(limits.w_max, limits.w_spin_fast)     # a turn standing still, not steering on the move
    else:
        level = limits.w_spin if a > math.radians(limits.spin_slow_deg) else limits.w_spin_min
        cap = limits.w_max
    level = max(min(level, cap), limits.w_spin_min)
    omega = _clamp(math.copysign(level, err), prev_w - limits.w_accel * dt, prev_w + limits.w_accel * dt)
    return LaneCommand(0.0, omega, False, "turn", err=err)


# --------------------------------------------------------------------------
# Doorways under velocity control: across-the-door position from the lidar


#: Driving through a doorway: slow, a short pursuit point so a centimetre
#: shows up in the steering, and the casters' dead zone compensated right up
#: to their breakaway (0.07 measured) — on a lane that costs left/right flips
#: and the scraping, but in a 0.84 m opening with a 0.78 m robot a 3 cm
#: offset left uncorrected (what the lane setting allows) is the whole margin.
#: Never slower than 0.06 while moving (v_creep): at 0.05 this base barely
#: goes and curves by itself. 7 ต.ค., every velocity door pass: commanded 0.060
#: -> 0.043 m/s actual, heading steady (±0.07°/s); the three that failed had
#: slowed to ~0.05 for something in the strip ahead -> 0.015-0.017 m/s actual
#: and a left turn of 0.34-0.65°/s that no pulse undid (D8 in twice, D11 in).
#: Between the stop and 0.06 there is nothing worth having: stop, or 0.06.
DOOR_LIMITS = DriveLimits(v_max=0.15, w_max=0.3, lookahead=0.5, front_stop=0.40, front_slow=0.80,
                          front_v=0.15, w_deadband=0.075, w_on=0.004, v_creep=0.06)
#: Two speeds through a doorway (owner, 7 ต.ค.: "อยากให้ตอนผ่านประตูไวกว่าเดิม
#: ... มันช้าเพราะค่อยๆเดิน"): every pass crawled at 0.06 from the aim to
#: `until`, 53-70 s, most of it in open floor before and after the door.
#: DOOR_LIMITS (0.08 then, up from 0.06; 0.12 now, below) only near the frame -- the front within
#: DOOR_SLOW_BEFORE_M of the wall line until the back is DOOR_SLOW_AFTER_M
#: past it -- or with anything closer beside than DOOR_FAST_SIDE_M (D8's
#: leaf runs 0.9 m alongside the way in from the room); DOOR_FAST_LIMITS
#: elsewhere. The side stop, the strip ahead and the heading guards are the
#: same at either speed.
#: Faster again (owner, 7 ต.ค. 16:45: "เหลือแค่ที่ประตูถ้าไวกว่านี้ก็โอเค"): each
#: pass was 32-38 s, ~27 s of it in the slow zone -- 1.7 m at 0.065 m/s real
#: (0.08 sent), 21 s of that held slow by something within 0.20 m beside.
#: Now 0.12 near the frame, 0.25 in the open, the zone from 0.30 before the
#: wall line to 0.08 after, and 0.12 m beside. Simulated D8 both ways, 36
#: starts (±3 cm off, ±2 cm odometry, two noise seeds): 36/36 through, the
#: closest side unchanged (5.2 cm), 32.5 -> 18.6 s a pass. Steering is per
#: distance, not per second (lean 2°, pulses), so a faster pass corrects as
#: much sideways per metre.
#: Then 0.15 / 0.30 (owner, 7 ต.ค.: "ทำให้เหลือ 1 นาที"), after 17:31 on the robot
#: at 0.12 / 0.25 -- 19.7-24.7 s a door, no touch -- and 192 simulated passes a
#: setting (three noise seeds): 0.12/0.25 18.5 s, closest 3.2 cm; 0.15/0.30
#: 15.1 s, 2.4 cm; 0.18/0.35 13.0 s, 3.2 cm; the same 146 through, the rest
#: stopped before the frame alike. One step at a time on the robot.
DOOR_FAST_LIMITS = DriveLimits(v_max=0.30, w_max=0.3, lookahead=0.5, front_stop=0.40, front_slow=0.80,
                               front_v=0.30, w_deadband=0.075, w_on=0.004, v_creep=0.06)
DOOR_SLOW_BEFORE_M = 0.30
DOOR_SLOW_AFTER_M = 0.08
DOOR_FAST_SIDE_M = 0.12
#: Steering in a doorway is pulses, not a continuous rate: one tick just past
#: the casters' breakaway, then wait out the lag before judging the result.
#: Continuous pursuit on this base (nothing under 0.07 rad/s, 1.4x above it,
#: 0.3 s late) swung the heading ±2-4° all the way through the simulated D8 —
#: at 3° the front corner moves 1.6 cm sideways, in an opening with 3 cm a
#: side — and ended 0.5 cm from the frame.
DOOR_PULSE_W = 0.08
DOOR_PULSE_WAIT_S = 0.5
DOOR_HEADING_DEADBAND = math.radians(0.5)
#: A pulse the base did not answer is followed by a stronger one, up to
#: DOOR_PULSE_MAX; back to DOOR_PULSE_W once the heading is in or the error
#: changes side. 7 ต.ค. 11:47, D8 from the corridor on glossy tile: crawling
#: at ~2 cm/s with stalls, the base curved left at ~0.4°/s through 37 right
#: pulses of 0.08 and none of them turned it -- the breakaway measured at
#: 0.2 m/s is not the breakaway at a crawl. Stopped by the 8° guard 0.45 m
#: short of the frame.
DOOR_PULSE_STEP = 0.03
DOOR_PULSE_MAX = 0.20
DOOR_PULSE_PROGRESS = math.radians(0.3)    # less gained than this since the last pulse = not answered
DOOR_LAT_GAIN = 3.0                     # rad of heading per metre off the middle (1 cm -> 1.7°)
DOOR_MAX_LEAN = math.radians(2.0)       # most it leans to come back to the middle
#: Out on the approach (the owner's sketch, 8 ต.ค.): a continuous heading hold,
#: gentle. 42 simulated D8-from-the-room starts (-8..+8 cm off, odometry ±2 cm,
#: glass leaf or not): pulses and steps 32 through, closest 3.6 cm, 14 steps;
#: this for every offset up to 6 cm, 34 through, closest 2.6 cm, 6 steps --
#: the reviewer's ~2 cm a side at the frame. So the runner curves in only what
#: it predicts the approach brings within the line-up (drive_route
#: DOOR_STEER_IN_*), which on those 42 is none: back to 32, 3.6, 14. Leaner
#: settings (5°, gain 1.0) went 2.1 cm; 10° tripped the 8° heading guard.
DOOR_APPROACH_LEAN = math.radians(3.0)
DOOR_APPROACH_GAIN = 0.6                 # rad of heading per metre off (5 cm -> 1.7°)
DOOR_APPROACH_W_GAIN = 0.8               # rad/s of turn per rad of heading error
DOOR_APPROACH_ODO_LEAN = math.radians(5.0)   # odometry's heading off the way through, at most (guard: 8°)
DOOR_APPROACH_LAG_S = 0.3                # how late a turn sent shows in odometry
DOOR_MAX_YAW_CORR = math.radians(3.0)  # most the walls may move the heading away from odometry in a doorway
#: The band at each side of the passage where the door itself stands (see
#: DoorPass._off_the_edges): from 5 cm inside the opening's side to 10 cm
#: outside it. D8's leaf stands 0.39-0.46 m from the route's middle (0.40 a
#: side); the room's ea_east wall 0.62 is kept. 15 cm took the simulated
#: room's ea_east (0.555 m) too, the only wall left to put a 4° odometry error
#: right on the way out of the room -- held at the leaf.
DOOR_EDGE_BAND_IN_M = 0.05
DOOR_EDGE_BAND_OUT_M = 0.10
ONE_EDGE_TRUST_M = 0.03                 # one-edge opening readings further than this from the lane are ignored
FRAME_MAX_EXTRA_M = 0.15                # two jambs this much wider than the passage can still be its frame


def opening_ahead(points: np.ndarray, face_near: float, width_hint: float, *, face_tol: float = 0.25,
                  band: float = 0.10, min_pts: int = 5, width_tol: float = 0.05,
                  depth: float = 0.40) -> dict | None:
    """The doorway ahead when the robot knows roughly where it is: the wall
    face looked for within `face_tol` of `face_near` (its distance by
    odometry), the opening's edges as the wall points nearest the robot's
    line on either side. Both edges seen: measured. One edge only: that edge
    and `width_hint` (the drawing's width) — D8's leaf is glass, and from
    the approach the lidar sees neither it nor the wall behind it on the
    east side (6 ต.ค. 17:32, live scan): `gap_ahead`, which needs both sides,
    paired ฝ่ายอาคาร's east wall with furniture 0.67 m ahead and reported a
    1.8 m opening 38 cm to the right. None if the face is not there, or two
    edges disagree with the drawing by more than `width_tol` (5 cm: one of
    them is then something else — in simulation, the wall behind a leaf seen
    past its edge read as an 0.88 m opening and steered the robot into it)."""
    p = np.asarray(points, dtype=float).reshape(-1, 2)
    near = p[(np.abs(p[:, 0] - face_near) <= face_tol) & (np.abs(p[:, 1]) <= 1.4)]
    if len(near) < 2 * min_pts:
        return None
    edges = np.arange(face_near - face_tol, face_near + face_tol + 0.05, 0.05)
    h, _ = np.histogram(near[:, 0], edges)
    k = int(np.argmax(h))
    face = float(np.median(near[(near[:, 0] >= edges[k] - 0.05) & (near[:, 0] < edges[k + 1] + 0.05), 0]))
    wall = near[np.abs(near[:, 0] - face) <= band]

    def edge(lats: np.ndarray) -> float | None:
        # The point nearest the robot's line that has wall continuing beyond
        # it: not the n-th nearest, which at 1.9 m and the lidar's spacing sat
        # several cm into the wall and made a 0.86 m door read 0.98 in
        # simulation — wide enough to let a wall beside pass as its edge.
        d = np.sort(np.abs(lats))
        for v in d:
            if np.count_nonzero((d > v) & (d <= v + 0.15)) >= min_pts:
                return float(v)
        return None

    r_edge = edge(wall[wall[:, 1] < -0.05][:, 1])
    l_edge = edge(wall[wall[:, 1] > 0.05][:, 1])
    lo = None if r_edge is None else -r_edge
    hi = l_edge
    frame = None
    if lo is not None and hi is not None:
        if abs((hi - lo) - width_hint) <= width_tol:
            return {"face": face, "lo": lo, "hi": hi, "width": hi - lo, "centre": (lo + hi) / 2.0, "edges": 2}
        frame = (lo, hi)
        # Wider than the drawing: look just past the wall for what narrows the
        # way through -- D11's leaf stands open inside, a few cm in from the
        # north jamb (7 ต.ค. 13:21, live scan at the frame: jamb, then the leaf
        # 0.12 m further in, 0.80 m of passage inside a 0.85 m frame). The
        # passage between the jamb on one side and that on the other is what
        # the robot drives through; it is structure on both sides, so it
        # counts as two edges. Accepted only narrower than the frame and not
        # narrower than the drawing allows.
        mid = (lo + hi) / 2.0
        inner = p[(p[:, 0] > face + band) & (p[:, 0] <= face + depth) & (p[:, 1] > lo) & (p[:, 1] < hi)]

        def intrusion(d: np.ndarray) -> float | None:
            # how far from the middle the narrowing starts: the min_pts-th
            # nearest return, so one stray point does not narrow anything (a
            # leaf seen along its face is all one lateral, nothing "beyond")
            d = np.sort(d)
            return float(d[min_pts - 1]) if len(d) >= min_pts else None

        e_r = intrusion(mid - inner[inner[:, 1] < mid][:, 1])
        e_l = intrusion(inner[inner[:, 1] > mid][:, 1] - mid)
        lo2 = lo if e_r is None else max(lo, mid - e_r)
        hi2 = hi if e_l is None else min(hi, mid + e_l)
        # a real narrowing (not the jamb's own reveal a few mm in), to a width
        # the drawing allows
        if ((hi - lo) - (hi2 - lo2) >= 0.02
                and width_hint - width_tol <= hi2 - lo2 <= width_hint + width_tol + 0.02):
            return {"face": face, "lo": lo2, "hi": hi2, "width": hi2 - lo2, "centre": (lo2 + hi2) / 2.0,
                    "edges": 2, "passage": True}
        # Otherwise one of the two is something else (the wall past a leaf's
        # end, seen round it). The robot came here lined up to a few cm, so the
        # right edge is the one that puts the middle near its line -- which is
        # only true when it did come here lined up: the frame's own middle is
        # kept (`frame_centre`) for a door step that knows its lane is stale.
        c_lo, c_hi = lo + width_hint / 2.0, hi - width_hint / 2.0
        if abs(c_lo) <= abs(c_hi):
            hi = None
        else:
            lo = None
        if min(abs(c_lo), abs(c_hi)) > 0.10:
            return None
    # Only a pair that can be a door frame: D11's is 0.87 against a 0.76
    # passage; from inside the room at D8 the "other jamb" is the wall behind
    # the glass leaf, 1.16-1.25 m from the jamb in simulation -- its middle is
    # 8-23 cm toward the leaf.
    # ...nor one narrower than the passage: something is standing in it (15:12,
    # D11 in: "both jambs 0.483 m apart" -- someone passing; the next look said 0.867).
    extra = ({} if frame is None or not width_hint - width_tol <= frame[1] - frame[0] <= width_hint + FRAME_MAX_EXTRA_M
             else {"frame_centre": (frame[0] + frame[1]) / 2.0, "frame_width": frame[1] - frame[0],
                   "frame_lo": frame[0], "frame_hi": frame[1]})
    if lo is not None:
        return {"face": face, "lo": lo, "hi": lo + width_hint, "width": width_hint,
                "centre": lo + width_hint / 2.0, "edges": 1, **extra}
    if hi is not None:
        return {"face": face, "lo": hi - width_hint, "hi": hi, "width": width_hint,
                "centre": hi - width_hint / 2.0, "edges": 1, **extra}
    return None


def door_lateral(gap: dict | None, sides: tuple[float | None, float | None], width: float | None = None,
                 robot_lat: float | None = None, robot_width: float = ROBOT_WIDTH,
                 edge_tol: float = 0.03) -> float | None:
    """Where the robot is across a passage, from the lidar alone: metres, +
    = left of the passage's middle; None when the lidar cannot tell.

    A side reading counts only if it is where the opening's edge should be —
    `robot_lat` (the current estimate) plus the arm plus the gap lands within
    `edge_tol` of ±width/2. Without that check the simulation took ฝ่ายอาคาร's
    east wall, 14 cm beside the robot on the way to D8, for the passage's
    edge, decided it was 11 cm right of the middle and steered into the leaf.
    Both edges beside: half the difference. One edge — D8 from the room,
    where the leaf stands open along the east side for ~0.9 m before the
    frame and the west is open floor: keep it at the margin the measured
    opening leaves, (width - robot) / 2. Neither, but the opening ahead: its
    middle (`gap_ahead`'s centre is where the middle is, the robot is the
    opposite)."""
    half = robot_width / 2.0
    left, right = sides
    if width is None or robot_lat is None:
        left = right = None
    else:
        if left is not None and abs(robot_lat + half + left - width / 2.0) > edge_tol:
            left = None
        if right is not None and abs(robot_lat - half - right + width / 2.0) > edge_tol:
            right = None
    if left is not None and right is not None:
        return (right - left) / 2.0
    margin = None if width is None else max((width - robot_width) / 2.0, 0.0)
    if left is not None and margin is not None:
        return margin - left
    if right is not None and margin is not None:
        return right - margin
    if gap is not None:
        return -gap["centre"]
    return None


def heading_error_from_walls(points: np.ndarray, max_dev_deg: float = 10.0, near: float = 0.8) -> float | None:
    """The robot's heading relative to the walls around it, radians (+ =
    turned left of them): straight runs of lidar returns beside the robot
    (parallel to its way) or ahead of it (across it), None if there are none.
    Lateral position from the lidar is not enough on its own: in simulation
    the odometry heading drifted 4° one way while the robot really pointed
    3.6° the other, and the pursuit steered it into D8's west jamb with the
    position known to the centimetre. Walls across the way matter as much as
    walls along it: coming to D8 from the corridor there is nothing alongside,
    only the corridor wall the door is in."""
    p = np.asarray(points, dtype=float).reshape(-1, 2)
    # Beside, not far ahead, and a tight line: D8's jamb face sits 2 cm out
    # from the leaf's line just past its end, and a 1.5 cm tolerance took both
    # as one wall — 1.5-2.5° of false heading right where it matters most.
    beside = p[(p[:, 0] >= -0.30) & (p[:, 0] <= 0.60) & (np.abs(p[:, 1]) >= 0.30) & (np.abs(p[:, 1]) <= near)]
    ahead = p[(p[:, 0] >= 0.30) & (p[:, 0] <= 1.60) & (np.abs(p[:, 1]) <= 1.0)]
    num = den = 0.0
    for group, across in ((beside, False), (ahead, True)):
        if len(group) < 15:
            continue
        for sgm in extract_segments(group, tol=0.008, min_len=0.25, min_pts=15, max_lines=4, tries=150):
            a = (sgm.angle + math.pi / 2) % math.pi - math.pi / 2          # undirected, in [-90°, 90°)
            if across:
                a = a - math.copysign(math.pi / 2, a) if abs(a) > math.pi / 4 else None
            if a is not None and abs(a) <= math.radians(max_dev_deg):
                num += -a * sgm.length
                den += sgm.length
    return num / den if den > 0 else None


@dataclass
class DoorPass:
    """One doorway, tick by tick: the lane is the line through the door's
    middle along `heading`, shifted by what the lidar says the real middle is
    (`corr`, filtered), so odometry drift — 1-2 cm a metre on this IMU-less
    base, 10 cm on the 1.7 m leg south to D8 on 6 ต.ค. — never decides how
    close the robot runs to a jamb. `until` is the coordinate along the travel
    axis where the robot's centre stops, past the wall."""
    middle: tuple[float, float]
    heading: float
    until: float
    limits: DriveLimits = DOOR_LIMITS
    fast_limits: DriveLimits = DOOR_FAST_LIMITS
    width: float | None = None
    width_hint: float = 0.86
    # The width used inside the doorway, for both the side readings and the
    # margin to keep. ROBOT_WIDTH is the owner's 0.76-0.78 "padded 3-5 cm";
    # D8 as it is propped open leaves 0.78 at the leaf's tip, so the runner
    # can pass the unpadded figure for a door the owner has said it fits.
    robot_width: float = ROBOT_WIDTH
    corr: float | None = None
    yaw_corr: float = 0.0
    entered_yaw: float | None = None
    since_pulse: float = 99.0
    pulse_w: float = DOOR_PULSE_W
    pulse_want: float | None = None      # the heading error the last pulse was sent for
    # What the pass believed at its start (the aim's decision, or the lane):
    # one-edge readings may move `corr` at most ONE_EDGE_TRUST_M from it.
    corr_ref: float | None = None
    # The runner chose to curve onto the line on the way instead of a step
    # sideways: steer continuously out on the approach (off by default -- in a
    # 3-cm-a-side opening it did worse than the pulses in simulation).
    steer_in: bool = False
    odo_prev: float | None = None        # odometry's heading the tick before: how fast it is turning

    def lane(self) -> tuple[tuple[float, float], tuple[float, float]]:
        ux, uy = math.cos(self.heading), math.sin(self.heading)
        nx, ny = -uy, ux
        c = self.corr or 0.0
        mx, my = self.middle[0] + nx * c, self.middle[1] + ny * c
        d = (self.until - self.middle[0]) / ux if abs(ux) > abs(uy) else (self.until - self.middle[1]) / uy
        return (mx - 3.0 * ux, my - 3.0 * uy), (mx + d * ux, my + d * uy)

    def _off_the_edges(self, points: np.ndarray, robot_lat: float) -> np.ndarray:
        """The lidar points less those beside the robot in the band at the
        passage's edges (DOOR_EDGE_BAND_IN_M inside to DOOR_EDGE_BAND_OUT_M
        outside of where the opening's sides are): what is there is the door
        itself -- leaf, jambs, stop -- not a wall to take the heading from."""
        p = np.asarray(points, dtype=float).reshape(-1, 2)
        half = (self.width or self.width_hint) / 2.0
        across = np.abs(robot_lat + p[:, 1])            # from the passage's middle, roughly (heading within a few °)
        beside = (p[:, 0] >= -0.30) & (p[:, 0] <= 0.60)
        edge = (across >= half - DOOR_EDGE_BAND_IN_M) & (across <= half + DOOR_EDGE_BAND_OUT_M)
        return p[~(beside & edge)]

    def update(self, pose: tuple[float, float, float], points: np.ndarray, prev: tuple[float, float],
               dt: float = 0.1) -> tuple[LaneCommand, dict]:
        ux, uy = math.cos(self.heading), math.sin(self.heading)
        face_near = (self.middle[0] - pose[0]) * ux + (self.middle[1] - pose[1]) * uy
        # Read in the robot's frame (not squared to the door as the runner's aim
        # is): which heading to square it by is not settled here -- squared by
        # odometry, a base 4° out by odometry was held in D8 in simulation; and at
        # D8 in (8 ต.ค.) the walls' heading (yaw_corr) was the one 1-2.4° out.
        gap = (opening_ahead(points, face_near, self.width or self.width_hint)
               if face_near > 0.35 else None)
        sides = side_clearance(points, half_width=self.robot_width / 2.0)
        if gap is not None and gap["edges"] == 2 and abs(gap["centre"]) < 0.15:
            self.width = gap["width"]          # measured, both edges, while the opening is clearly ahead
        x, y, yaw = pose
        turning = 0.0 if self.odo_prev is None else wrap_pi(yaw - self.odo_prev) / dt
        self.odo_prev = yaw
        nx, ny = -math.sin(self.heading), math.cos(self.heading)
        lat_map = (x - self.middle[0]) * nx + (y - self.middle[1]) * ny
        robot_lat = lat_map - (self.corr or 0.0)
        if gap is not None and gap["edges"] == 1 and abs(-gap["centre"] - robot_lat) > ONE_EDGE_TRUST_M:
            # One edge from afar, disagreeing with where the lane put us: at D8
            # the edge it picks moved 6 cm with a 3 cm change of standpoint
            # (-2.87 at x -2.44, -2.81 at -2.47, 7 ต.ค.) while the jamb the
            # robot actually passes is at -2.93. The side readings beside the
            # leaf and frame correct it once there, and they are measured close.
            gap = None
        lat_meas = door_lateral(gap, sides, self.width, robot_lat, robot_width=self.robot_width)
        from_one_edge = (lat_meas is not None and gap is not None and gap["edges"] == 1
                         and door_lateral(None, sides, self.width, robot_lat, robot_width=self.robot_width) is None)
        # 4°, not 10: D8's leaf is propped 6.7° off the way through (6 ต.ค.
        # 17:45, live scan) and must not be taken for a wall along it.
        # ...and nothing at the passage's own edges counts as a wall beside: the
        # leaf, the jambs, a door stop stand there, never the walls that give a
        # heading. 8 ต.ค., D8 in, three runs: the leaf propped ~2° off the way
        # (x -1.987 -> -2.003 over 0.44 m, logged frames) passed the 4° gate,
        # yaw_corr went -1..-2.4° and the robot crossed the frame 1.4-3.7 cm from
        # the west jamb with no pulse sent (it "was already leaning"); the next
        # stop found the heading 4.7° out. Simulated, a leaf 2-3° off: yaw_corr
        # -2.5..-3.0°, 3.6-5.2 cm west at the end of D8 in; D8 out 3.9 / 2.1 cm
        # from the leaf instead of 6.6.
        theta = heading_error_from_walls(self._off_the_edges(points, robot_lat), max_dev_deg=4.0)
        if theta is not None:
            self.yaw_corr += 0.3 * (wrap_pi(self.heading + theta - yaw) - self.yaw_corr)
        # Odometry drifts under 2° over a doorway's few metres; the wall reading
        # can be wrong by far more. 7 ต.ค. 11:02, D8 from the corridor: it drifted
        # to -13° and the pulses, trusting it, turned the robot 22.8° left until
        # the ahead-strip held it 0.48 m from the wall beside the door.
        self.yaw_corr = _clamp(self.yaw_corr, -DOOR_MAX_YAW_CORR, DOOR_MAX_YAW_CORR)
        yaw_est = yaw + self.yaw_corr
        if self.corr_ref is None:
            self.corr_ref = self.corr or 0.0
        if lat_meas is not None:
            c = lat_map - lat_meas
            if from_one_edge:
                # The gate above is relative to the current estimate, which the
                # last reading moved: a reading that drifts a centimetre at a time
                # walks it anywhere. 7 ต.ค. 17:31, D8 from the room at 0.25 m/s:
                # one edge 1.3-1.9 m ahead went -0.012 -> +0.087 over 0.5 m, corr
                # followed to -0.059 and the robot steered 3.5 cm into the frame's
                # east side (2.3 cm left; the jambs beside put it back). Far, one
                # edge moves the estimate no more than the trust it has.
                c = _clamp(c, self.corr_ref - ONE_EDGE_TRUST_M, self.corr_ref + ONE_EDGE_TRUST_M)
            self.corr = c if self.corr is None else self.corr + 0.4 * (c - self.corr)
            if not from_one_edge:
                # two edges, or the jambs beside: what one far edge is now held to
                # (reviewer: fixed at the first tick it pulled corr back from them)
                self.corr_ref = self.corr
        left, right = sides
        inside = left is not None and right is not None and left < 0.25 and right < 0.25
        if inside and self.entered_yaw is None:
            self.entered_yaw = yaw_est
        elif not inside:
            # The "no turning inside the frame" guard is for the frame; out in
            # the corridor the heading may settle wherever the walls say.
            self.entered_yaw = None
        # Ahead-strip as wide as the body counted in this doorway, + 1 cm: D11's
        # leaf corner 1.5 cm clear of the base read as "0.35 m ahead" with 2 cm
        # and held the robot where it could not steer clear (7 ต.ค. 10:56). The
        # side stop (8 mm) is what guards the pass; this is for what is IN the way.
        front = front_distance(points, half_width=self.robot_width / 2.0, margin=0.01)
        start, target = self.lane()
        half_len = ROBOT_BASE_LENGTH / 2.0
        at_frame = -(half_len + DOOR_SLOW_AFTER_M) < face_near < half_len + DOOR_SLOW_BEFORE_M
        close_beside = any(g is not None and g < DOOR_FAST_SIDE_M for g in sides)
        slow = at_frame or close_beside
        cmd = lane_command((x, y, yaw_est), start, target, prev, dt, front, self.limits if slow else self.fast_limits)
        # Speed from the lane (braking to `until`, held by the strip); the turn
        # from a heading hold that leans back toward the middle, in pulses --
        # the middle of what is beside it when something is close (side_balance).
        margin = max(0.0, ((self.width or self.width_hint) - self.robot_width) / 2.0)
        bal, both = side_balance(sides, keep=min(DOOR_KEEP_M, margin), robot_width=self.robot_width,
                                 passage=max(self.width or 0.0, self.width_hint))
        if bal is not None and both:
            c = lat_map - bal
            self.corr = c if self.corr is None else self.corr + 0.4 * (c - self.corr)
        avoid = None if bal is not None else strip_edge(points, half_width=self.robot_width / 2.0)
        approach = self.steer_in and not slow and bal is None and avoid is None
        if approach and not (cmd.done or cmd.vx < self.limits.v_creep * 0.99):
            # Out on the approach, clear of the frame and of anything beside: steer
            # as a lane does, pursuit onto the (corrected) line through the middle,
            # an S-curve rather than pulses (owner, 8 ต.ค., a sketch: the path
            # curving onto the door's line on the way, "เลี้ยวไปอีกทางนิดหน่อยก็ปรับ
            # มุมได้"). Pulses with a 2° lean belong in the frame, where a swing
            # moves a corner 1.6 cm; out here they took ~1 m to fix 3 cm, so a few
            # cm off meant a stop and a step sideways (~20 s each). Never further
            # than DOOR_APPROACH_LEAN off the way through.
            # A heading hold, continuous: lean toward the line by the offset (at most
            # DOOR_APPROACH_LEAN), turn toward that heading at a rate that settles
            # it without swinging past -- a lane's pure pursuit, with the link's
            # lag, overshot to 8° and tripped the heading guard in simulation.
            lat_now = lat_map - (self.corr or 0.0)
            # ...and odometry's own heading (yaw_est - yaw_corr) held within
            # DOOR_APPROACH_ODO_LEAN: the runner stops a pass at 8° off by
            # odometry, and a 3° lean against a -3° yaw_corr plus the coast was
            # 7.4 of them (reviewer)
            lo = max(-DOOR_APPROACH_LEAN, self.yaw_corr - DOOR_APPROACH_ODO_LEAN)
            hi = min(DOOR_APPROACH_LEAN, self.yaw_corr + DOOR_APPROACH_ODO_LEAN)
            want = wrap_pi(self.heading + _clamp(-DOOR_APPROACH_GAIN * lat_now, lo, hi) - yaw_est)
            # Turn for the error there will be once the turn under way has landed:
            # the link and the base answer ~0.3 s late, and the casters' floor
            # (w_deadband) makes the least turn sent a real one -- turning on to
            # the heading itself swung odometry from -2° to +7.3° in simulation,
            # 0.7 from the 8° guard (reviewer).
            ahead = want - turning * DOOR_APPROACH_LAG_S
            w = (0.0 if abs(ahead) <= DOOR_HEADING_DEADBAND or ahead * want < 0
                 else _clamp(DOOR_APPROACH_W_GAIN * ahead, -0.3, 0.3))
            cmd.omega = _through_casters(w, prev[1], self.fast_limits)
            self.pulse_w, self.pulse_want, self.since_pulse = DOOR_PULSE_W, None, DOOR_PULSE_WAIT_S
            return cmd, {"gap": gap, "sides": sides, "lat_meas": lat_meas, "corr": self.corr, "inside": inside,
                         "front": front, "theta": theta, "yaw_est": yaw_est, "slow": slow, "balance": bal,
                         "avoid": avoid, "approach": True}
        lat = bal if bal is not None else (avoid if avoid is not None else lat_map - (self.corr or 0.0))
        lean = DOOR_AVOID_LEAN if (avoid is not None and not at_frame) else DOOR_MAX_LEAN
        want = wrap_pi(self.heading + _clamp(-DOOR_LAT_GAIN * lat, -lean, lean) - yaw_est)
        self.since_pulse += dt
        cmd.omega = 0.0
        if cmd.done or cmd.vx < self.limits.v_creep * 0.99:
            pass        # held or starting: never turn on the spot beside a leaf or a jamb
        elif abs(want) <= DOOR_HEADING_DEADBAND:
            self.pulse_w, self.pulse_want = DOOR_PULSE_W, None
        elif prev[1] != 0.0 and (want > 0) != (prev[1] > 0):
            # still turning the other way (the approach's continuous turn, the
            # tick it hands over): through zero first, as `_through_casters`
            # does -- each reversal swivels the casters round (reviewer)
            pass
        elif self.since_pulse >= DOOR_PULSE_WAIT_S:
            last = self.pulse_want
            if last is not None and want * last > 0 and abs(want) > abs(last) - DOOR_PULSE_PROGRESS:
                self.pulse_w = min(self.pulse_w + DOOR_PULSE_STEP, DOOR_PULSE_MAX)
            else:
                self.pulse_w = DOOR_PULSE_W
            cmd.omega = math.copysign(self.pulse_w, want)
            self.pulse_want = want
            self.since_pulse = 0.0
        return cmd, {"gap": gap, "sides": sides, "lat_meas": lat_meas, "corr": self.corr, "inside": inside,
                     "balance": bal, "avoid": avoid,
                     "front": front, "theta": theta, "yaw_est": yaw_est, "slow": slow}


#: In a doorway, what is beside the robot steers it (the owner, 7 ต.ค. with a
#: photo of the robot in a frame, close on the left and room on the right:
#: "ฝั่งไหนเรดาห์เห็นว่ามันใกล้ก็ปรับให้หมุนไปอีกด้านที่พื้นที่ว่างกว่า"). Both sides
#: closer than DOOR_BALANCE_NEAR_M: aim for equal gaps. One side only, closer
#: than DOOR_KEEP_M -- or than the passage leaves a side, if less -- move off
#: it (more than the passage leaves puts the far jamb on the other side: in
#: simulation from ฝ่ายอาคาร, 3 cm a side, 6 cm off D8's leaf hit the west jamb). 14:37 at D11 the side readings (1.4 / 15.6 cm)
#: did not fit the drawn passage and were thrown away; the robot was steered
#: by odometry it believed centred and was held by the leaf 7 cm off.
DOOR_BALANCE_NEAR_M = 0.20
DOOR_KEEP_M = 0.06
#: Both sides are the way through only if together they make one: gaps +
#: body no wider than the passage + this. 15:14, D11 out: furniture 0.19 m
#: south of the robot in the big room and the leaf 0.12 m north read as the
#: two edges (1.01 m "passage" for a 0.76-0.87 door) and balanced it south,
#: into the south jamb.
DOOR_BALANCE_SPAN_TOL_M = 0.12


def side_balance(sides: tuple[float | None, float | None], *, near: float = DOOR_BALANCE_NEAR_M,
                 keep: float = DOOR_KEEP_M, robot_width: float | None = None,
                 passage: float | None = None) -> tuple[float | None, bool]:
    """How far the robot should move across, from the gaps beside it: metres,
    + = it is that much too far LEFT (so steer right). (None, _) when nothing
    beside is close. The flag: both sides were seen -- a measured position
    in the passage, not just a push away from one side. Given `robot_width`
    and `passage`, two sides that span more than the passage are not its
    edges: only the nearer one is pushed off."""
    left, right = sides
    l_near = left is not None and left < near
    r_near = right is not None and right < near
    if l_near and r_near and (robot_width is None or passage is None
                              or left + right + robot_width <= passage + DOOR_BALANCE_SPAN_TOL_M):
        return (right - left) / 2.0, True
    if l_near and r_near:                                   # not one passage: the nearer side only
        if left <= right:
            r_near = False
        else:
            l_near = False
    if l_near and left < keep:
        return keep - left, False
    if r_near and right < keep:
        return -(keep - right), False
    return None, False


#: Something in the strip ahead on ONE side only -- 7 ต.ค. 15:03, D8 from the
#: room: the robot 10-15 cm east of the way through (odometry had drifted),
#: the end of D8's leaf in the left of the strip 0.38 m ahead, nothing in the
#: middle or on the right. It drove up to it and stopped. Now it moves across
#: by what clears the strip (+ DOOR_AVOID_EXTRA_M), leaning up to
#: DOOR_AVOID_LEAN out in the open (DOOR_MAX_LEAN at the frame), if that is
#: no more than DOOR_AVOID_MAX_M; dead ahead, or both sides, is not steered round.
DOOR_AVOID_REACH_M = 1.2
DOOR_AVOID_CENTRE_M = 0.08
DOOR_AVOID_EXTRA_M = 0.02
DOOR_AVOID_MAX_M = 0.12
DOOR_AVOID_LEAN = math.radians(4.0)


def strip_edge(points: np.ndarray, *, half_width: float, margin: float = 0.01, min_pts: int = 3,
               reach: float = DOOR_AVOID_REACH_M) -> float | None:
    """How far to move across to clear what is in the strip ahead on one side
    only: metres, + = the robot is that much too far LEFT (move right). None
    when the strip is clear, blocked in the middle, blocked on both sides, or
    the move would be more than DOOR_AVOID_MAX_M."""
    p = np.asarray(points, dtype=float).reshape(-1, 2)
    front = ROBOT_BASE_LENGTH / 2.0
    strip = p[(p[:, 0] > front) & (p[:, 0] < front + reach) & (np.abs(p[:, 1]) <= half_width + margin)]
    if len(strip) < min_pts:
        return None
    left = strip[strip[:, 1] > DOOR_AVOID_CENTRE_M][:, 1]
    right = -strip[strip[:, 1] < -DOOR_AVOID_CENTRE_M][:, 1]
    if np.count_nonzero(np.abs(strip[:, 1]) <= DOOR_AVOID_CENTRE_M) >= min_pts:
        return None
    clear = half_width + margin + DOOR_AVOID_EXTRA_M
    if len(left) >= min_pts and len(right) < min_pts:
        need = clear - float(np.sort(left)[min_pts - 1])
        return need if need <= DOOR_AVOID_MAX_M else None
    if len(right) >= min_pts and len(left) < min_pts:
        need = clear - float(np.sort(right)[min_pts - 1])
        return -need if need <= DOOR_AVOID_MAX_M else None
    return None


def frame_turn_limit(sides: tuple[float | None, float | None], *, floor_deg: float = 6.0, cap_deg: float = 12.0,
                     lever: float = 0.30, keep: float = 0.008) -> float:
    """How far (radians) the heading may wander inside a door frame before the
    pass is stopped: as much as keeps the front corner — `lever` ahead of the
    centre — out of the side gap the lidar measures now, at least the old
    fixed 6°, at most 12°. The fixed 6° stopped the robot in D8's frame on
    7 ต.ค. 10:30 with 9.8 / 11.9 cm still free either side, where 6° moves the
    corner 3 cm."""
    gaps = [g for g in sides if g is not None]
    if not gaps:
        return math.radians(floor_deg)
    room = max(min(gaps) - keep, 0.0)
    allowed = math.asin(min(room / lever, 1.0))
    return min(max(allowed, math.radians(floor_deg)), math.radians(cap_deg))


def turn_summary(samples: list, window: float = 0.3, min_rate_deg: float = 3.0) -> dict:
    """How a recorded manoeuvre turned: `samples` are (t, x, y, yaw_deg). The
    yaw rate is taken over `window`-second steps; a reversal is the turn
    changing direction between two steps both turning faster than
    `min_rate_deg` °/s (slower is noise and pose-update jitter). Built for the
    board's GoHome, which the owner heard scraping (6 ต.ค.): the recording
    showed a ~200° spin at 50-58°/s, then ~12 left-right reversals backing in
    — each reversal swings the casters round again."""
    pts = [(float(s[0]), float(s[3])) for s in samples]
    if len(pts) < 2:
        return {"peak_rate_deg": 0.0, "reversals": 0, "turned_deg": 0.0}
    # unwrap the yaw
    un, prev = [pts[0][1]], pts[0][1]
    for _, a in pts[1:]:
        d = (a - prev + 180.0) % 360.0 - 180.0
        un.append(un[-1] + d)
        prev = a
    rates, i = [], 0
    while i < len(pts) - 1:
        j = i + 1
        while j < len(pts) - 1 and pts[j][0] - pts[i][0] < window:
            j += 1
        dt = pts[j][0] - pts[i][0]
        if dt > 0:
            rates.append((un[j] - un[i]) / dt)
        i = j
    signs = [1 if r > 0 else -1 for r in rates if abs(r) >= min_rate_deg]
    return {"peak_rate_deg": round(max((abs(r) for r in rates), default=0.0), 1),
            "reversals": sum(1 for a, b in zip(signs, signs[1:]) if a != b),
            "turned_deg": round(sum(abs(b - a) for a, b in zip(un, un[1:])), 1)}


def door_margin(gap_width: float, robot_width: float = ROBOT_WIDTH) -> float:
    """Free space on each side if the robot is perfectly centred in a gap --
    to 0.1 mm, finer than anything here measures. 8 ต.ค. 11:30, D11 in: the
    drawing's -1.13..-0.37 is 0.7599999999999999 wide in floats, 2.99999 cm a
    side against the 3.0 needed, and the route ended in front of the door."""
    return round((gap_width - robot_width) / 2, 4)


def points_to_world(pose: tuple[float, float, float], points: Iterable[Iterable[float]]) -> np.ndarray:
    x, y, yaw = pose
    p = np.asarray(list(points), dtype=float).reshape(-1, 2)
    c, s = math.cos(yaw), math.sin(yaw)
    return np.c_[x + c * p[:, 0] - s * p[:, 1], y + s * p[:, 0] + c * p[:, 1]]
