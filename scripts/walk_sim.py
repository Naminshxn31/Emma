"""Offline simulator for board_walk's creep lines (28-29 ต.ค. 2026).

Drives `board_walk.CreepLine` -- the same tick code the robot runs -- against
a simulated base, a simulated lidar over a cloud of obstacle points, and a
simulated board localization that can be off and correct itself in jumps.
Nothing here talks to the robot.

The base is the one the floor-1 traces measured (28 ต.ค., 8 runs, ~4,900
ticks), not an ideal one:

* commands land ~0.3 s late; forward speed comes out ~0.9 of what was asked
  in the open (median 0.90, p25 0.73-0.79 at 0.15-0.2 m/s);
* something in the strip ahead of the front face holds it, whatever is asked:
  0.35-0.50 m ahead -> 14-24 % of the asked speed, 0.25-0.35 m -> 3-9 %
  (back1, 83 s held: 2-4 mm/s of 0.06-0.08 asked, ~5 %) -- the base's own
  sonar hold, `ultra_obstacle_hold_in_front_dist 0.5` -- and held like that
  its steering hardly turns it either: go1 28 ต.ค. 0.13 rad/s asked, 6° in
  3 s (`held_steer` 1.0); the first gap trace, 0.16 rad/s and nothing for
  9 s (`held_steer` 0);
* turning while rolling does nothing under ~0.07 rad/s (the casters), on the
  spot nothing under 0.13 rad/s and ~0.8 of the rest (6 ต.ค. measurements).

Usage (a route file, a path, the index of its creep step, an obstacle file of
world points {"points": [[x, y], ...]} in the board's frame):
    python scripts/walk_sim.py data/robot/waypoints/floor1.json room_tv_go 4 obstacles.json \\
        [--board-off 0.09,0.08] [--corrections 0.3:0.5,1.2:0.5] [--start x,y,deg]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT)]

from app import robot_lanes as rl  # noqa: E402


def load_board_walk():
    spec = importlib.util.spec_from_file_location("board_walk", ROOT / "scripts" / "board_walk.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


#: the base, as measured (module docstring)
LAG_S = 0.3
ACCEL = 0.5
OPEN_GAIN = 0.9
DRIVE_W_DEADBAND = 0.07
SPOT_W_DEADBAND = 0.13
SPOT_W_GAIN = 0.8
HOLD_FULL_M = 0.60          # nothing in the strip nearer than this ahead of the face: not held
HOLD_STOP_M = 0.35          # nearer than this: ~nothing moves
LIDAR_HALF_ARC = math.radians(103.0)
LIDAR_RANGE = 6.0


def hold_factor(points_robot: np.ndarray) -> float:
    """What share of the asked speed the base gives with these lidar returns
    (robot frame) in the strip ahead."""
    p = points_robot
    if not len(p):
        return OPEN_GAIN
    sel = (p[:, 0] > rl.ROBOT_BASE_LENGTH / 2) & (np.abs(p[:, 1]) <= 0.36)
    if int(sel.sum()) < 3:                  # as robot_lanes.front_distance: one stray return is dust
        return OPEN_GAIN
    d = float(np.sort(p[sel, 0])[2]) - rl.ROBOT_BASE_LENGTH / 2
    if d >= HOLD_FULL_M:
        return OPEN_GAIN
    if d <= HOLD_STOP_M:
        return 0.05                 # back1: 2-4 mm/s made of 0.06-0.08 asked
    return 0.05 + (OPEN_GAIN - 0.05) * (d - HOLD_STOP_M) / (HOLD_FULL_M - HOLD_STOP_M)


def explore_blob(points, res: float = 0.05, margin: float = 1.0) -> bytes:
    """An explore-map blob (the board's REST format, board_walk.MapFit) with
    the cells under `points` occupied and the rest free -- the board's map of
    a simulated room."""
    import struct

    p = np.asarray(points, dtype=float).reshape(-1, 2)
    ox, oy = float(p[:, 0].min() - margin), float(p[:, 1].min() - margin)
    w = int(math.ceil((p[:, 0].max() + margin - ox) / res))
    h = int(math.ceil((p[:, 1].max() + margin - oy) / res))
    cells = np.full((h, w), 100, dtype=np.int8)
    cells[np.floor((p[:, 1] - oy) / res).astype(int), np.floor((p[:, 0] - ox) / res).astype(int)] = -100
    head = struct.pack("<ffIIf", ox, oy, w, h, res)
    return head + b"\0" * (32 - len(head)) + struct.pack("<I", w * h) + cells.tobytes()


def rect_gap(points_world: np.ndarray, pose, length: float, width: float) -> float:
    """Smallest gap between the robot's rectangle at `pose` and the points
    (negative = overlap)."""
    if not len(points_world):
        return 9.0
    x, y, yaw = pose
    c, s = math.cos(yaw), math.sin(yaw)
    dx, dy = points_world[:, 0] - x, points_world[:, 1] - y
    lx, ly = dx * c + dy * s, -dx * s + dy * c
    qx, qy = np.abs(lx) - length / 2, np.abs(ly) - width / 2
    d = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0)
    return float(d.min())


class SimBase:
    """True pose, a lagged base, a lidar over `env` (world points, nearest
    return per 0.4°), and the board's pose = true + `board_off`, which changes
    in jumps: `corrections` [(after m driven, share of the remaining error it
    corrects)] and `wrong_jumps` [(after m driven, dx, dy)] -- a jump AWAY from
    the truth (the broken door map of 28 ต.ค.). Between jumps the board's pose is
    smooth: standing, it read the same to the mm for seconds (go1, back1)."""

    def __init__(self, pose, env, *, dt: float = 0.1, board_off=(0.0, 0.0), corrections=(), wrong_jumps=(),
                 seed: int = 0, noise_m: float = 0.0005, held_steer: float = 1.0):
        self.x, self.y, self.yaw = pose
        self.env = np.asarray(env, dtype=float).reshape(-1, 2)
        self.dt = dt
        self.v = self.w = 0.0
        self.queue = [(0.0, 0.0)] * max(1, int(round(LAG_S / dt)))
        self.off = list(board_off)
        self.events = sorted([(float(m), "fix", float(share), 0.0) for m, share in corrections] +
                             [(float(m), "jump", float(dx), float(dy)) for m, dx, dy in wrong_jumps])
        self.driven = 0.0
        self.t = 0.0
        self.rng = np.random.default_rng(seed)
        self.noise_m = noise_m
        self.held_steer = held_steer
        self.spot_push = 0.0
        self.min_base_gap = self.min_arm_gap = 9.0
        self.path = []

    @property
    def pose(self):
        return (self.x, self.y, self.yaw)

    def board_pose(self):
        n = self.rng.normal(0.0, self.noise_m, 2) if self.noise_m else (0.0, 0.0)
        return (self.x + self.off[0] + n[0], self.y + self.off[1] + n[1], self.yaw)

    def lidar(self) -> np.ndarray:
        d = self.env - [self.x, self.y]
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        lx, ly = d[:, 0] * c + d[:, 1] * s, -d[:, 0] * s + d[:, 1] * c
        r, a = np.hypot(lx, ly), np.arctan2(ly, lx)
        m = (r < LIDAR_RANGE) & (r > 0.05) & (np.abs(a) < LIDAR_HALF_ARC)
        lx, ly, r, a = lx[m], ly[m], r[m], a[m]
        if not len(r):
            return np.zeros((0, 2))
        b = np.round(a / math.radians(0.4)).astype(int)
        order = np.lexsort((r, b))
        keep = np.ones(len(order), bool)
        keep[1:] = b[order][1:] != b[order][:-1]
        idx = order[keep]
        return np.c_[lx[idx], ly[idx]]

    def step(self, vx: float, w: float) -> None:
        self.queue.append((vx, w))
        cv, cw = self.queue.pop(0)
        f = hold_factor(self.lidar())
        if abs(cv) < 0.01:                                    # turning on the spot
            tv = 0.0
            if abs(cw) < SPOT_W_DEADBAND:
                tw, self.spot_push = 0.0, 0.0
            else:
                self.spot_push += self.dt
                tw = SPOT_W_GAIN * cw if self.spot_push >= 0.4 else 0.0
        else:
            self.spot_push = 0.0
            tv = cv * (f if cv > 0 else OPEN_GAIN)
            tw = 0.0 if abs(cw) < DRIVE_W_DEADBAND else cw
            if cv > 0 and f < 0.3:
                tw *= f / 0.3 * self.held_steer
        self.v += max(-ACCEL * self.dt, min(ACCEL * self.dt, tv - self.v))
        self.w += max(-1.5 * self.dt, min(1.5 * self.dt, tw - self.w))
        self.yaw = rl.wrap_pi(self.yaw + self.w * self.dt)
        dx = self.v * math.cos(self.yaw) * self.dt
        dy = self.v * math.sin(self.yaw) * self.dt
        self.x += dx
        self.y += dy
        self.driven += math.hypot(dx, dy)
        self.t += self.dt
        while self.events and self.driven >= self.events[0][0]:
            _, kind, a, b = self.events.pop(0)
            if kind == "fix":
                self.off = [o * (1.0 - a) for o in self.off]
            else:
                self.off = [self.off[0] + a, self.off[1] + b]
        self.min_base_gap = min(self.min_base_gap, rect_gap(self.env, self.pose, rl.ROBOT_BASE_LENGTH,
                                                            rl.ROBOT_BASE_WIDTH))
        self.min_arm_gap = min(self.min_arm_gap, rect_gap(self.env, self.pose, rl.ROBOT_BASE_LENGTH,
                                                          rl.ROBOT_WIDTH))
        self.path.append(self.pose)


def spot_turn(base: SimBase, heading: float, tol_deg: float, bw) -> str | None:
    """Walker._spot_turn on the simulated base: the arms' room first, then
    robot_lanes.turn_command until done (settling as the real one does)."""
    turn = rl.wrap_pi(heading - base.yaw)
    gap = rl.turn_sweep_gap(base.lidar(), turn)
    if gap < bw.TURN_SWEEP_MIN_M:
        return "no room to turn %.0f° here: the arms would pass %.2f m from something" % (math.degrees(turn), gap)
    prev_w, settled = 0.0, 0
    for _ in range(400):
        cmd = rl.turn_command(base.yaw, heading, prev_w, base.dt, tol_deg=tol_deg)
        if cmd.done:
            base.step(0.0, 0.0)
            if prev_w == 0.0:
                return None
            prev_w = 0.0
            for _ in range(5):
                base.step(0.0, 0.0)
            settled += 1
            if settled > 4:
                return None
            continue
        base.step(0.0, cmd.omega)
        prev_w = cmd.omega
    return "turn: 40 s and not there"


def run_line(bw, step: dict, base: SimBase, *, fit=None, max_s: float = 200.0, step_no: int = 1) -> dict:
    """One creep step on the simulated base, the way Walker.creep runs it.
    Returns how it ended and what it measured."""
    line = bw.CreepLine(step, step_no, base.board_pose(), base.t, fit=fit)
    said, spins, spin_s = list(line.pop_messages()), 0, 0.0
    end, reason = "running", ""
    t_start = base.t
    while base.t - t_start < max_s:
        act = line.tick(base.t, base.board_pose(), base.lidar())
        said += line.pop_messages()
        if act.kind == "again":
            continue
        if act.kind == "done":
            end = "done"
            break
        if act.kind == "stop":
            end, reason = "stop", act.reason
            break
        if act.kind == "spin":
            t0 = base.t
            why = spot_turn(base, act.heading, 3.0, bw)
            spins += 1
            spin_s += base.t - t0
            if why:
                end, reason = "stop", why
                break
            line.after_spin(base.t)
            continue
        line.sent(act, False)
        base.step(act.vx, act.w)
    for _ in range(int(LAG_S / base.dt) + 5):              # the zero, and the coast
        base.step(0.0, 0.0)
    return {"end": end, "reason": reason, "s": round(base.t - t_start, 1), "said": said, "spins": spins,
            "spin_s": round(spin_s, 1), "jumps": line.pose.jumps, "min_base_gap": round(base.min_base_gap, 3),
            "min_arm_gap": round(base.min_arm_gap, 3), "true_end": tuple(round(v, 3) for v in base.pose),
            "corr_end": tuple(round(v, 3) for v in line.pose.corr), "worst_lat": round(line.worst_lat, 3)}


def off_line(path, pts) -> float:
    """Largest distance of a pose path from a polyline."""
    worst = 0.0
    for x, y, _ in path:
        best = 9.0
        for a, b in zip(pts, pts[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            t = max(0.0, min(1.0, ((x - a[0]) * dx + (y - a[1]) * dy) / max(dx * dx + dy * dy, 1e-9)))
            best = min(best, math.hypot(x - a[0] - t * dx, y - a[1] - t * dy))
        worst = max(worst, best)
    return worst


# ---- the way home onto a dock in a nook the map does not have (16 Nov 2026, floor 1) ----
#: What the walk frames of ten runs said (`.tmp/floor1-20261116/slip_report.md`): facing the
#: dock's nook from nearer than ~1.5 m out the board's pose reads 0.3-0.45 m FURTHER OUT along
#: the dock's line than she is (it jumped 0.46 and 0.38 m back as she turned to face out);
#: facing out or sideways it is right. A MoveTo to the pre-dock spot through that zone ended
#: 0.8-0.9 m short twice of five (CANNOT_REACH_TARGET, both tries of the leg) and 9-18 cm
#: from the spot twice; a MoveTo to an open point with a heading ends 1-8 cm from it.
SLIP_M = 0.40
SLIP_WITHIN_M = 1.6
SLIP_ARC = math.radians(60.0)
SLIP_REFUSES = 0.4
#: The dock: the contacts take her this far aside (she charged 9.5 cm aside, 16 Nov) and the
#: body stops her this far past the dock pose.
DOCK_CONTACT_HALF_M = 0.12
DOCK_BODY_M = 0.02
#: The base rolls this far anticlockwise of the heading the board reads (floor 1, 16 Nov: a
#: 1.95 m reverse with the heading held -1.9° of the dock's went 1.5 cm the other way; straight
#: legs, median +2.2°). The dock pose in a route file is what the board READ with her square on
#: the dock, so the dock's true axis is that heading plus this.
ROLL_OFF_DEG = 2.3
#: 20 Nov, floor 1, the new map (Q 86-93) -- what the evening's ways home showed, as two models
#: (`near_leg_aside_m`, `pose_slide_m_per_rad` below; off unless asked, the rows before them keep
#: their numbers):
#: (a) a dock leg that STARTS nearer than NEAR_LEG_M to its point ended 5-6 cm aside of the dock's
#:     line: ONE case, n20k (5.5 cm from 0.14 m) -- the board takes such a target inside its own
#:     tolerance. (n20j is NOT one: its trace, scratchpad n20j/n20j_guard.jsonl, starts that leg at the
#:     reception, 1.17 m out, and it ended 5.1 cm aside after the board's own last turn -- a far leg.)
#:     NEAR_LEG_M is a guess between 0.14 and 1.17 m; `near_leg_m` moves it (1.1 = the come-again's
#:     own 1.0 m leg counts as near: the pessimistic case for coming again);
#: (b) our turns on the spot moved the BOARD'S POSE 0.025-0.106 m per radian, each turn its own
#:     way (n20j `_square_up`: -89 deg 16.5 cm, 180 deg 13 cm, -90 deg 4 cm; n20k the same) -- not
#:     known to be the base: with Q 88-93 and the scan fitting 0.75-0.93 nobody can say which moved.
#:     That a leg of the board's own takes the error away again is an ASSUMPTION (nothing showed it
#:     either way) -- and it decides nothing here: in every way home our turns come AFTER the board's
#:     last leg (a first `pose_slide_held` switch changed no number, re-review 21 Nov: removed).
#: `aside_repeats`: a leg to a spot ends where the last leg to that spot ended (the same aside both
#: times: what the board does at a place may be the place's, not chance) -- the worst case for coming
#: again, whose second leg is otherwise a fresh draw.
NEAR_LEG_M = 0.5
#: The far legs of 20 Nov to the 1.9 m spot (`far_leg_20nov`): of five, four ended <= 3 cm aside of the
#: dock's line and one (n20j) 5.1 cm. Aside: 4 in 5 uniform 0-3 cm, 1 in 5 uniform 3-6 cm, either side;
#: along it +-3 cm. (A first version, uniform +-3.75 cm a side, could never end past 4.3 cm -- under the
#: 5 cm bar, so it could not show coming again failing: review 21 Nov.)
FAR_20NOV_INNER_M, FAR_20NOV_OUTER_M, FAR_20NOV_OUTER_SHARE = 0.03, 0.06, 0.2


class DockWorld:
    """Walker.dock() on the simulated base: a board whose pose slips facing the nook, whose
    MoveTo is the board's own (it ends near the point, or gives up in the slip zone), a
    velocity session and a clock that roll the base, and a dock that says on_dock at its pose.
    `slip=False` is a dock in the open (floor 2's)."""

    def __init__(self, dock, start, *, seed: int = 0, slip: bool = True, noise_m: float = 0.004,
                 noise_deg: float = 0.15, arrive_m: float = 0.08, arrive_deg: float = 6.0, roll_off_deg: float = 0.0,
                 lag_s: float = LAG_S, yaw_gain: float = 1.0, blip_m: float = 0.0, contacts_miss: int = 0,
                 thing_ahead_from: float | None = None, taken_at_out: float | None = None,
                 gap_at_out: float | None = None, bumper_at_out: float | None = None,
                 turn_slide_m_per_rad: float = 0.0, near_leg_aside_m: tuple[float, float] | None = None,
                 pose_slide_m_per_rad: tuple[float, float] | None = None, near_leg_m: float = NEAR_LEG_M,
                 far_leg_20nov: bool = False, aside_repeats: bool = False, charger_deeper_m: float = 0.0):
        # The adverse cases (review 16 Nov): `lag_s` a base slower to answer; `yaw_gain` the share
        # of a turn-while-rolling it makes (the pulses of 16 Nov turned her 0.7-0.95°/s of the
        # 0.92 a full one gives); `blip_m` the board's pose stepping aside for 2-3 s near the dock
        # (2-4 cm, three reverses of that night); `contacts_miss` arrivals the contacts do not
        # close on (a second try); `thing_ahead_from` something in the strip ahead from this far
        # out (driving out again); `taken_at_out` / `gap_at_out` the velocity action taken by a
        # person / dropped by the watchdog as she reverses past this; `bumper_at_out` a board event.
        self.yaw_gain, self.blip_m, self.contacts_miss = yaw_gain, blip_m, contacts_miss
        self.thing_ahead_from, self.taken_at_out, self.gap_at_out = thing_ahead_from, taken_at_out, gap_at_out
        self.bumper_at_out = bumper_at_out
        # `turn_slide_m_per_rad`: a turn on the spot also moves her (20 Nov, floor 1, n20j: in front of
        # the dock, -89 deg moved the board's pose 0.165 m, 180 deg 0.13 m, -90 deg 0.04 m = 0.025-0.106
        # m per radian, each its own way). The true base moves that much per radian turned, in one
        # direction drawn at the start of each turn.
        self.turn_slide, self._slide_dir = turn_slide_m_per_rad, None
        # (a) `near_leg_aside_m` (lo, hi): a MoveTo with a heading that starts within NEAR_LEG_M of its
        # point ends that far aside of the dock's line (either side), +-1 cm along it.
        # (b) `pose_slide_m_per_rad` (lo, hi): our turns on the spot move the BOARD'S POSE (`perr`), not
        # the base -- per turn a rate in (lo, hi) and a direction of its own. Held until the board's next
        # MoveTo (assumed: its leg re-finds itself as it rolls, and where a leg ends is what was measured);
        # so a reverse steered on the board's pose ends that far off in truth.
        self.near_leg, self.pose_slide = near_leg_aside_m, pose_slide_m_per_rad
        self.near_leg_m, self.far_20nov, self.repeats = near_leg_m, far_leg_20nov, aside_repeats
        self._ended_at: dict = {}                                  # `aside_repeats`: spot -> where its last leg ended
        self.perr = np.zeros(2)
        self._perr_turn = None                                     # (direction, rate) of the turn under way
        self.arrivals, self.was_out = 0, True
        # `dock` is the route file's: where she sits, and the heading the board reads there.
        # `start` is a board pose too. True yaw = read yaw + roll_off.
        self.off = math.radians(roll_off_deg)
        self.dx, self.dy, self.dyaw = dock[0], dock[1], math.radians(dock[2]) + self.off     # the dock's TRUE axis
        self.ux, self.uy = math.cos(self.dyaw), math.sin(self.dyaw)
        # `charger_deeper_m`: the real charger (contacts and body) this much DEEPER than the file's dock pose (23 Nov,
        # floor 1: lidar fits on the contacts 9-25 cm deeper than the file's) -- the route file keeps its pose
        self.dx, self.dy = self.dx - charger_deeper_m * self.ux, self.dy - charger_deeper_m * self.uy
        self.base = SimBase((start[0], start[1], rl.wrap_pi(start[2] + self.off)), np.zeros((0, 2)), seed=seed, noise_m=0.0)
        self.base.queue = [(0.0, 0.0)] * max(1, int(round(lag_s / self.base.dt)))
        self.rng = np.random.default_rng(seed + 7919)
        self.slip, self.noise_m, self.noise_rad = slip, noise_m, math.radians(noise_deg)
        self.arrive_m, self.arrive_rad = arrive_m, math.radians(arrive_deg)
        self.refuses = bool(self.rng.random() < SLIP_REFUSES)      # one answer for both tries of a leg
        self.cmd = (0.0, 0.0)
        self.moves: list[tuple] = []
        self.reason = "ok"

    # -- the dock's frame, of the TRUE pose --
    def out_lat(self):
        x, y = self.base.x - self.dx, self.base.y - self.dy
        return x * self.ux + y * self.uy, x * -self.uy + y * self.ux

    def _slipped(self) -> bool:
        out, _ = self.out_lat()
        facing_nook = abs(rl.wrap_pi(self.base.yaw - self.dyaw - math.pi)) < SLIP_ARC
        return self.slip and facing_nook and 0.2 < out < SLIP_WITHIN_M

    # -- the clock (board_walk's `time`) --
    def monotonic(self) -> float:
        return self.base.t

    def sleep(self, s: float) -> None:
        for _ in range(max(1, int(round(s / self.base.dt)))):
            yaw0, rolling = self.base.yaw, abs(self.base.v) > 0.01
            x0, y0 = self.base.x, self.base.y
            self.base.step(*self.cmd)
            if rolling and self.yaw_gain != 1.0:                 # what a pulse turns her, of what it asks
                self.base.yaw = rl.wrap_pi(yaw0 + self.yaw_gain * rl.wrap_pi(self.base.yaw - yaw0))
            turned = abs(rl.wrap_pi(self.base.yaw - yaw0))
            on_spot = not rolling and turned > 1e-4 and math.hypot(self.base.x - x0, self.base.y - y0) < 1e-3
            if self.pose_slide and on_spot:                      # (b): the board's pose, not the base
                if self._perr_turn is None:
                    self._perr_turn = (float(self.rng.uniform(-math.pi, math.pi)), float(self.rng.uniform(*self.pose_slide)))
                d, rate = self._perr_turn
                self.perr += rate * turned * np.array([math.cos(d), math.sin(d)])
            elif turned <= 1e-4:
                self._perr_turn = None
            if self.turn_slide and on_spot:
                if self._slide_dir is None:
                    self._slide_dir = float(self.rng.uniform(-math.pi, math.pi))
                self.base.x += self.turn_slide * turned * math.cos(self._slide_dir)
                self.base.y += self.turn_slide * turned * math.sin(self._slide_dir)
            elif turned <= 1e-4:
                self._slide_dir = None
            out, _ = self.out_lat()
            if out < -DOCK_BODY_M:                               # the dock's body
                self.base.x += (-DOCK_BODY_M - out) * self.ux
                self.base.y += (-DOCK_BODY_M - out) * self.uy
                self.base.v = 0.0
            if out > 0.3:
                self.was_out = True
            elif out <= 0.0 and self.was_out:                    # one more arrival at the contacts
                self.was_out = False
                self.arrivals += 1

    def scan(self) -> np.ndarray:
        """Robot-frame lidar: nothing, or a thing 0.10 m ahead of her face while she drives OUT
        past `thing_ahead_from` (somebody stepping in front of a second try)."""
        out, _ = self.out_lat()
        if self.thing_ahead_from is not None and out >= self.thing_ahead_from and self.base.v > 0.02 \
                and abs(rl.wrap_pi(self.base.yaw - self.dyaw)) < math.radians(20):
            x = rl.ROBOT_BASE_LENGTH / 2 + 0.10
            return np.array([[x, -0.1], [x, 0.0], [x, 0.1], [x + 0.01, 0.05]])
        return np.zeros((0, 2))

    # -- the board --
    def pose(self):
        off = SLIP_M if self._slipped() else 0.0
        n = self.rng.normal(0.0, self.noise_m, 2)
        out, _ = self.out_lat()
        if self.blip_m and 0.12 < out < 0.26 and self.base.v < -0.01:   # the pose steps aside for a while
            n = n + np.array([-self.uy, self.ux]) * self.blip_m
        return (self.base.x + off * self.ux + n[0] + self.perr[0], self.base.y + off * self.uy + n[1] + self.perr[1],
                rl.wrap_pi(self.base.yaw - self.off + self.rng.normal(0.0, self.noise_rad)))

    def power(self) -> dict:
        out, lat = self.out_lat()
        on = out <= 0.0 and abs(lat) <= DOCK_CONTACT_HALF_M and self.arrivals > self.contacts_miss
        return {"dockingStatus": "on_dock" if on else "not_on_dock", "isCharging": on}

    def events(self) -> list:
        out, _ = self.out_lat()
        if self.bumper_at_out is not None and self.base.v < -0.01 and out <= self.bumper_at_out:
            self.bumper_at_out = None
            return [{"type": "BUMPER_TRIGGERED"}]
        return []

    def quality(self) -> int:
        return 54 if self._slipped() else 68

    def set_speed(self, v) -> None:
        pass

    def current(self):
        return None                                               # a MoveTo here is over when it returns

    def cancel(self) -> None:
        pass

    def action_reason(self, aid: int) -> str:
        return self.reason

    def move_to(self, x: float, y: float, yaw) -> int:
        """The board's MoveTo, as one move: turn to the point, roll, turn to the heading."""
        b = self.base
        self.moves.append((round(x, 2), round(y, 2), None if yaw is None else round(math.degrees(yaw), 1)))
        dist = math.hypot(x - b.x, y - b.y)
        t_out = (x - self.dx) * self.ux + (y - self.dy) * self.uy
        out, _ = self.out_lat()
        through_the_nook = self.slip and t_out < SLIP_WITHIN_M - 0.5 and out > t_out + 0.2
        if dist > 0.05:
            b.yaw = math.atan2(y - b.y, x - b.x)
        if through_the_nook and self.refuses:
            self.reason = "CANNOT_REACH_TARGET"                   # where it stood, facing the nook
            b.t += 5.0
            return len(self.moves)
        self.reason = "ok"
        miss = self.arrive_m * (2.2 if through_the_nook else 1.0)
        near = self.near_leg is not None and yaw is not None and \
            math.hypot(x - b.x - self.perr[0], y - b.y - self.perr[1]) < self.near_leg_m
        self.perr = np.zeros(2)                                   # (b) is held until a leg of the board's own (assumed)
        if near:                                                  # (a): a target inside its own tolerance
            aside = float(self.rng.uniform(*self.near_leg)) * (1.0 if self.rng.random() < 0.5 else -1.0)
            along = float(self.rng.uniform(-0.01, 0.01))
            b.x = x + along * self.ux - aside * self.uy
            b.y = y + along * self.uy + aside * self.ux
        elif self.far_20nov and yaw is not None:                  # a far leg to a spot with a heading: 20 Nov's five
            outer = self.rng.random() < FAR_20NOV_OUTER_SHARE
            aside = float(self.rng.uniform(FAR_20NOV_INNER_M, FAR_20NOV_OUTER_M) if outer
                          else self.rng.uniform(0.0, FAR_20NOV_INNER_M)) * (1.0 if self.rng.random() < 0.5 else -1.0)
            along = float(self.rng.uniform(-FAR_20NOV_INNER_M, FAR_20NOV_INNER_M))
            b.x = x + along * self.ux - aside * self.uy
            b.y = y + along * self.uy + aside * self.ux
        else:
            b.x = x + self.rng.uniform(-miss, miss)
            b.y = y + self.rng.uniform(-miss, miss)
        if self.repeats and yaw is not None:                      # the same aside at the same spot, every time
            key = (round(x, 2), round(y, 2))
            if key in self._ended_at:
                b.x, b.y = x + self._ended_at[key][0], y + self._ended_at[key][1]
            else:
                self._ended_at[key] = (b.x - x, b.y - y)
        if yaw is not None:                                       # the heading it READS there
            b.yaw = rl.wrap_pi(yaw + self.off + self.rng.uniform(-self.arrive_rad, self.arrive_rad))
        b.t += 4.0 + dist / 0.25
        b.v = b.w = 0.0
        return len(self.moves)


def run_dock(dock, start, *, back_from: float = 0.0, approach=None, seed: int = 0, slip: bool = True,
             straight_m: float | None = None, axis_offset_deg: float = 0.0, **world) -> dict:
    """Walker.dock() -- the robot's own code -- from `start` (x, y, yaw) onto `dock`
    (x, y, deg). Returns how it ended and where she truly sits: `aside_m`, `crooked_deg`."""
    import app.slamware_sdk as sw

    bw = load_board_walk()
    w_ = DockWorld(dock, start, seed=seed, slip=slip, **world)
    bw.time = w_                                                  # monotonic / sleep roll the base
    if straight_m is not None:
        bw.BACK_STRAIGHT_M = straight_m

    class Session:
        def __init__(self, sdk, *a, **k):
            self.last_renew = None

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            w_.cmd = (0.0, 0.0)

        def set(self, vx, w):
            out, _ = w_.out_lat()
            if vx < 0 and w_.taken_at_out is not None and out <= w_.taken_at_out:
                w_.taken_at_out, w_.cmd, self.last_renew = None, (0.0, 0.0), "taken"   # B: the base is the person's
                return True
            if vx < 0 and w_.gap_at_out is not None and out <= w_.gap_at_out:
                w_.gap_at_out, self.last_renew = None, "gap"                     # the watchdog dropped it; taken up again
                w_.base.v, w_.cmd = 0.0, (vx, w)
                w_.base.queue = [(0.0, 0.0)] * len(w_.base.queue)
                return True
            w_.cmd = (vx, w)
            return False

    doc = {"dock": {"pose": list(dock), "estimated": False}}
    if back_from:
        doc["dock"]["back_from_m"] = back_from
    if approach:
        doc["dock"]["approach"] = list(approach)
    if axis_offset_deg:
        doc["dock"]["axis_offset_deg"] = axis_offset_deg
    walker = bw.Walker(w_, doc, False, False)
    walker.sdk = object()
    said: list[str] = []
    walker.say = said.append
    walker.scan = w_.scan
    real = sw.VelocitySession
    sw.VelocitySession = Session
    end, reason = "docked", ""
    try:
        walker.dock()
    except bw.Stop as e:
        end, reason = "stop", str(e)
    finally:
        sw.VelocitySession = real
    w_.cmd = (0.0, 0.0)
    w_.sleep(1.0)
    out, lat = w_.out_lat()
    on = w_.power()["dockingStatus"] == "on_dock"
    return {"end": end if on or end == "stop" else "off", "reason": reason, "out_m": round(out, 3),
            "arrivals": w_.arrivals,
            "aside_m": round(lat, 3), "crooked_deg": round(math.degrees(rl.wrap_pi(w_.base.yaw - w_.dyaw)), 2),
            "s": round(w_.base.t, 1), "moves": w_.moves, "said": said}


#: floor 1, 16 Nov: the moved dock and the corridor point every way home comes along
FLOOR1_DOCK = (-7.11, -6.78, 169.6)
FLOOR1_APPROACH = (-9.0, -6.2)


def dock_trials(n: int = 40, *, back_from: float = 0.0, slip: bool = True, straight_m: float | None = None,
                first_seed: int = 0, axis_offset_deg: float = 0.0, roll_off_deg: float = 0.0, starts: str = "reception",
                **world) -> dict:
    """`n` seeded ways home from about the reception (3-4 m out, any side, any heading), or with
    `starts="near"` from in front of the dock: 0.05-0.45 m from the spot she reverses from, any
    heading (20 Nov n20k: `to_dock` pressed 0.14 m from it). `world`: DockWorld's adverse cases.
    `second_tries` = ways home that reached the contacts twice; `come_agains` = ways home that went
    out and came to the spot again (board_walk `_come_again`)."""
    docked, aside, crooked, stops, again, came = 0, [], [], [], 0, 0
    for seed in range(first_seed, first_seed + n):
        r = np.random.default_rng(seed)
        ux, uy = math.cos(math.radians(FLOOR1_DOCK[2])), math.sin(math.radians(FLOOR1_DOCK[2]))
        if starts == "near":
            far = back_from or 0.45
            d, a = r.uniform(0.05, 0.45), r.uniform(-math.pi, math.pi)
            out, lat = far + d * math.cos(a), d * math.sin(a)
        else:
            out, lat = r.uniform(3.0, 4.0), r.uniform(-1.0, 1.0)
        start = (FLOOR1_DOCK[0] + out * ux - lat * uy, FLOOR1_DOCK[1] + out * uy + lat * ux, r.uniform(-math.pi, math.pi))
        res = run_dock(FLOOR1_DOCK, start, back_from=back_from, approach=FLOOR1_APPROACH, seed=seed, slip=slip,
                       straight_m=straight_m, axis_offset_deg=axis_offset_deg, roll_off_deg=roll_off_deg, **world)
        again += any("out and in once more" in line for line in res["said"])
        came += any("coming to it again" in line for line in res["said"])
        if res["end"] == "docked":
            docked += 1
            aside.append(abs(res["aside_m"]))
            crooked.append(abs(res["crooked_deg"]))
        else:
            stops.append((seed, res["reason"] or res["end"]))
    return {"n": n, "docked": docked, "worst_aside_m": round(max(aside), 3) if aside else None,
            "worst_crooked_deg": round(max(crooked), 2) if crooked else None,
            "mean_aside_m": round(float(np.mean(aside)), 3) if aside else None,
            "mean_crooked_deg": round(float(np.mean(crooked)), 2) if crooked else None, "second_tries": again,
            "come_agains": came, "stops": stops}


def main(argv=None) -> int:
    if argv is None and len(sys.argv) > 1 and sys.argv[1] == "dock":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 200
        real = {"roll_off_deg": ROLL_OFF_DEG}                 # the base as floor 1 measured it
        f1 = dict(real, back_from=1.9, axis_offset_deg=ROLL_OFF_DEG)   # floor1.json
        near = {"near_leg_aside_m": (0.05, 0.06)}                      # 20 Nov (a), n20k
        pose = {"pose_slide_m_per_rad": (0.025, 0.106)}                # 20 Nov (b), n20j/n20k
        for name, kw in (("pre-dock spot (as it was)", dict(real)),
                         ("back_from 1.9, line = heading", dict(real, back_from=1.9, straight_m=0.0)),
                         ("  + square over the last 0.30 m", dict(real, back_from=1.9)),
                         ("floor1.json (1.9 m, line +2.3)", f1),
                         ("  base rolls true (offset wrong)", dict(f1, roll_off_deg=0.0)),
                         ("  base rolls 3.5", dict(f1, roll_off_deg=3.5)),
                         ("  base rolls -2.3 (wrong sign)", dict(f1, roll_off_deg=-ROLL_OFF_DEG)),
                         ("  file says -5, base +2.3", dict(real, back_from=1.9, axis_offset_deg=-5.0)),
                         ("  base answers after 0.8 s", dict(f1, lag_s=0.8)),
                         ("  heading reads 1 deg noisy", dict(f1, noise_deg=1.0)),
                         ("  pulses turn 0.75 of asked", dict(f1, yaw_gain=0.75)),
                         ("  pulses turn 0.3 of asked", dict(f1, yaw_gain=0.3)),
                         ("  pose blips 4 cm near the dock", dict(f1, blip_m=0.04)),
                         ("  legs end up to 0.4 m off", dict(f1, arrive_m=0.4)),
                         ("  contacts miss once", dict(f1, contacts_miss=1)),
                         ("  ... and a thing ahead going out", dict(f1, contacts_miss=1, thing_ahead_from=1.0)),
                         ("  taken at 1.0 m out", dict(f1, taken_at_out=1.0)),
                         ("  watchdog gap at 1.0 m out", dict(f1, gap_at_out=1.0)),
                         ("  bumper at 0.8 m out", dict(f1, bumper_at_out=0.8)),
                         ("  turns slide 0.05 m/rad (20 Nov)", dict(f1, turn_slide_m_per_rad=0.05)),
                         ("  turns slide 0.10 m/rad (20 Nov)", dict(f1, turn_slide_m_per_rad=0.10)),
                         ("  (a) near start ends 5-6 cm aside", dict(f1, starts="near", **near)),
                         ("  (b) turns move the board's pose", dict(f1, **pose)),
                         ("  (a) + (b)", dict(f1, starts="near", **near, **pose)),
                         ("  (a) + (b), far legs as 20 Nov", dict(f1, starts="near", far_leg_20nov=True, **near, **pose)),
                         ("  far legs as 20 Nov", dict(f1, far_leg_20nov=True)),
                         ("  far legs as 20 Nov + (b)", dict(f1, far_leg_20nov=True, **pose)),
                         ("  far legs 20 Nov + (b), aside repeats", dict(f1, far_leg_20nov=True, aside_repeats=True, **pose)),
                         ("  (a)+(b), 20 Nov, again-leg near", dict(f1, starts="near", far_leg_20nov=True, near_leg_m=1.1,
                                                                    **near, **pose)),
                         ("pre-dock spot, open floor, true", {"slip": False})):
            out = dock_trials(n, **kw)
            stops = out.pop("stops")
            why = {}
            for _, r in stops:
                key = r.split(":")[-1].strip()[:44] if "gave up" not in r else "the board gave up on the leg"
                why[key] = why.get(key, 0) + 1
            print("%-34s docked %3d/%d  aside worst %s mean %s  crooked worst %s mean %s  2nd tries %d  came again %d  "
                  "stops %s" % (name, out["docked"], out["n"], out["worst_aside_m"], out["mean_aside_m"],
                                out["worst_crooked_deg"], out["mean_crooked_deg"], out["second_tries"], out["come_agains"],
                                why or ""))
        return 0
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("route_file")
    ap.add_argument("path")
    ap.add_argument("step", type=int, help="index of the creep step in the path's steps")
    ap.add_argument("obstacles", help='{"points": [[x, y], ...]} in the board frame')
    ap.add_argument("--board-off", default="0,0", help="the board's pose minus the true one at the start, x,y")
    ap.add_argument("--corrections", default="", help="m_driven:share,... -- the board correcting itself")
    ap.add_argument("--start", help="x,y,deg (default: the line's first point, facing along it)")
    args = ap.parse_args(argv)
    bw = load_board_walk()
    doc = json.loads(Path(args.route_file).read_text(encoding="utf-8"))
    step = doc["paths"][args.path]["steps"][args.step]
    pts = [(p[0], p[1]) for p in step["creep"]]
    if args.start:
        sx, sy, sd = (float(v) for v in args.start.split(","))
        start = (sx, sy, math.radians(sd))
    else:
        start = (pts[0][0], pts[0][1], math.atan2(pts[1][1] - pts[0][1], pts[1][0] - pts[0][0]))
    env = json.loads(Path(args.obstacles).read_text(encoding="utf-8"))["points"]
    corr = [tuple(float(v) for v in c.split(":")) for c in args.corrections.split(",") if c]
    base = SimBase(start, env, board_off=tuple(float(v) for v in args.board_off.split(",")), corrections=corr)
    res = run_line(bw, step, base)
    res["true_off_line"] = round(off_line(base.path, pts), 3)
    for m in res.pop("said"):
        print("  " + m)
    print(json.dumps(res))
    return 0 if res["end"] == "done" else 2


if __name__ == "__main__":
    sys.exit(main())
