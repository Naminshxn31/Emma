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


def main(argv=None) -> int:
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
