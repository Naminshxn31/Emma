"""Faster lanes and turns (9 ต.ค., owner: "ทำให้ไวกว่านี้ได้ไหม เพิ่มสปีดเลย") without
stopping later for anything, and the free space the lidar shows every way
round (owner: "ดูผลจากเรดาห์หรือเลเซอร์กับทำให้รู้ไหมว่ามุมไหนเดินได้หรือไม่ได้").

The base in the stopping simulations is the one the 7-9 ต.ค. lane logs show:
odometry answers a speed command ~0.3 s late, it brakes at ~0.5 m/s^2, and the
frame a decision is made on is ~0.2 s old.
"""
from __future__ import annotations

import importlib.util
import math
import os
import types
from pathlib import Path

import numpy as np
import pytest

from app import robot_lanes as rl
from tests.test_robot_drive import _ClearWatcher, _drive_route, _lane_runner, _VelocitySdk, simulate
from tests.test_robot_lanes import raycast

ROOT = Path(__file__).resolve().parents[1]

#: lane_limits(0.35, 0.6, 0.5) as it was before 9 ต.ค. -- what every lane of the
#: first two panel walks ran on
OLD = rl.DriveLimits(v_max=0.35, w_max=0.6, latency=0.45)


def _free(scene, width, **kw):
    return dict(rl.free_space(scene, width, **kw))


def _circle(cx, cy, r, n=24):
    a = np.linspace(0.0, 2 * math.pi, n + 1)
    return [(cx + r * math.cos(a[i]), cy + r * math.sin(a[i]), cx + r * math.cos(a[i + 1]), cy + r * math.sin(a[i + 1]))
            for i in range(n)]


CORRIDOR = [(-6.0, 0.89, 6.0, 0.89), (-6.0, -0.89, 6.0, -0.89)]          # 1.78 m, as the floor-2 corridor
FAR_WALL = [(2.6, -3.0, 2.6, 3.0)]


def _doorway(half=0.38):
    """A wall across the way 1.0 m ahead, 0.10 m deep, with a 2 x `half` opening
    (0.76: D11 as the drawing has it), and a wall 2.6 m ahead beyond it."""
    return [(1.0, half, 1.0, 3.0), (1.0, -half, 1.0, -3.0), (1.0, half, 1.1, half), (1.0, -half, 1.1, -half),
            (1.1, half, 1.1, 3.0), (1.1, -half, 1.1, -3.0)] + FAR_WALL


# -- where it can go: free_space ------------------------------------------------

@pytest.mark.parametrize("width", [rl.ROBOT_BASE_WIDTH, rl.ROBOT_WIDTH])
def test_in_a_corridor_ahead_is_free_and_the_walls_are_where_the_body_meets_them(width):
    pts = raycast(CORRIDOR, (0.0, 0.0, 0.0), step=0.002)
    f = _free(pts, width)
    assert f[0.0] == 3.0                                    # nothing in the strip for 3 m
    # facing a wall 0.89 m away the front face is 0.30 m out: 0.59 m to go, whatever the width
    assert f[90.0] == pytest.approx(0.59, abs=0.012) and f[-90.0] == pytest.approx(0.59, abs=0.012)
    # at 45° the near corner of the body's strip meets the wall first: wider, sooner
    h = width / 2
    expect = (0.89 - h * math.cos(math.radians(45))) / math.sin(math.radians(45)) - 0.30
    assert f[45.0] == pytest.approx(expect, abs=0.012) and f[-45.0] == pytest.approx(expect, abs=0.012)
    # behind the lidar's arc nothing is seen, which is not free
    assert f[-180.0] is None and f[175.0] is None and f[110.0] is None and f[-105.0] is None


@pytest.mark.parametrize("offset,width,expect", [
    (0.0, rl.ROBOT_BASE_WIDTH, 2.30),     # the base, centred: through to the wall beyond
    (0.0, rl.ROBOT_WIDTH, 0.70),          # the arms out: 0.78 in 0.76 -- the jambs are in the way
    (0.04, rl.ROBOT_BASE_WIDTH, 0.70),    # the base 4 cm off the middle of 3 cm a side: a jamb is in the way
])
def test_a_doorway_0_76_wide_is_open_to_the_base_centred_and_shut_to_the_arms(offset, width, expect):
    pts = raycast(_doorway(), (0.0, offset, 0.0), step=0.002)
    assert _free(pts, width)[0.0] == pytest.approx(expect, abs=0.012)


def test_a_chair_leg_off_to_one_side_is_in_the_way_of_the_arms_only():
    leg = [(0.78, 0.36, 0.82, 0.36), (0.82, 0.36, 0.82, 0.40), (0.82, 0.40, 0.78, 0.40), (0.78, 0.40, 0.78, 0.36)]
    pts = raycast(leg + FAR_WALL, (0.0, 0.0, 0.0), step=0.002)
    assert _free(pts, rl.ROBOT_BASE_WIDTH)[0.0] == pytest.approx(2.30, abs=0.012)
    assert _free(pts, rl.ROBOT_WIDTH)[0.0] == pytest.approx(0.48, abs=0.012)
    # turned away from it the arms are clear too
    assert _free(pts, rl.ROBOT_WIDTH)[-30.0] > 2.0


@pytest.mark.parametrize("width", [rl.ROBOT_BASE_WIDTH, rl.ROBOT_WIDTH])
def test_someone_standing_ahead_blocks_the_ways_the_body_would_meet_them(width):
    pts = raycast(_circle(1.5, 0.0, 0.20) + FAR_WALL, (0.0, 0.0, 0.0), step=0.002)
    f = _free(pts, width)
    assert f[0.0] == pytest.approx(1.0, abs=0.012)          # 1.3 m to them, less the front 0.30
    for a in (-15.0, -10.0, 10.0, 15.0):
        assert f[a] < 1.2, (a, f[a])                         # the body still meets them
    for a in (-35.0, -30.0, 30.0, 35.0):
        assert f[a] > 2.0, (a, f[a])                         # past them: the way is open


def test_straight_ahead_free_space_is_the_lanes_own_front_distance():
    # one function under both (robot_lanes._strip_ahead): what slows a lane and
    # what a screen shows as walkable cannot come to disagree
    scenes = [raycast(_doorway(), (0.0, 0.0, 0.0), step=0.002), raycast(_doorway(), (0.0, 0.05, 0.0), step=0.002),
              raycast(_circle(1.5, 0.1, 0.2) + FAR_WALL, (0.0, 0.0, 0.0), step=0.002),
              raycast(CORRIDOR, (0.0, 0.0, 0.0), step=0.002), np.zeros((0, 2))]
    for pts in scenes:
        for width in (rl.ROBOT_BASE_WIDTH, rl.ROBOT_WIDTH):
            for margin in (0.0, 0.01, 0.05):
                free = dict(rl.free_space(pts, width, side_margin=margin, max_m=2.7))[0.0]
                front = rl.front_distance(pts, half_width=width / 2, margin=margin)
                if front is None:
                    assert free == 2.7
                else:
                    assert free == pytest.approx(front - rl.ROBOT_BASE_LENGTH / 2, abs=0.0006)


def test_free_space_is_the_whole_circle_in_steps_and_numpy_only():
    out = rl.free_space(np.zeros((0, 2)), rl.ROBOT_BASE_WIDTH, step_deg=5.0)
    assert [a for a, _ in out] == [-180.0 + 5.0 * k for k in range(72)]
    assert all((f is None) == (abs(a) > rl.LIDAR_HALF_ARC_DEG) for a, f in out)
    assert dict(out)[0.0] == 3.0


# -- how fast it may go for what is ahead ---------------------------------------

def test_the_stop_speed_is_the_speed_that_stops_in_that_distance():
    for free in (0.05, 0.2, 0.5, 1.0):
        v = rl.stop_speed(free, 0.6, 0.5)
        assert v * 0.6 + v * v / (2 * 0.5) == pytest.approx(free, abs=1e-9)
    assert rl.stop_speed(0.0, 0.6, 0.5) == 0.0 and rl.stop_speed(-0.1, 0.6, 0.5) == 0.0


def _target(lim, front, rem=5.0):
    """The speed lane_command aims for (not the ramp toward it): from standing,
    with a tick long enough for any ramp."""
    return rl.lane_command((0.0, 0.0, 0.0), (-1.0, 0.0), (rem, 0.0), (0.0, 0.0), 10.0, front, lim)


def test_at_the_old_speed_the_strip_ahead_slows_and_stops_it_exactly_as_before():
    # the coordinator's rule: a frame where the lane is blocked slows and stops the
    # robot exactly as before at the old speed -- the stop cap (STOP_*) never binds there
    dr = _drive_route()
    now = dr.lane_limits(0.35, 0.6, 0.5)
    assert now.front_slow == pytest.approx(OLD.front_slow) and now.front_v == OLD.front_v
    for front in [None] + list(np.arange(0.30, 3.2, 0.02)):
        for pv in (0.0, 0.1, 0.2, 0.35):
            for rem in (0.15, 0.6, 3.0):
                a = rl.lane_command((0.0, 0.0, 0.0), (-1.0, 0.0), (rem, 0.0), (pv, 0.0), 0.1, front, OLD)
                b = rl.lane_command((0.0, 0.0, 0.0), (-1.0, 0.0), (rem, 0.0), (pv, 0.0), 0.1, front, now)
                assert (a.vx, a.omega, a.done) == (b.vx, b.omega, b.done), (front, pv, rem)


def test_a_faster_lane_starts_slowing_earlier_and_never_allows_more_near_something():
    dr = _drive_route()
    new = dr.lane_limits(dr.LANE_VMAX, 0.6, dr.SPIN_FAST)
    assert new.front_slow > OLD.front_slow                    # 1.40 m at 0.45: it slows earlier
    for front in np.arange(0.30, OLD.front_slow + 1e-9, 0.01):
        assert _target(new, front).vx <= _target(OLD, front).vx + 1e-9, front
    assert _target(new, 1.30).why == "front"                  # held for what is ahead, and it says so


def test_whatever_the_taper_says_it_never_goes_faster_than_it_can_stop():
    # the cap on top: a taper set too loose (a fast --vmax, a mistake) is still held
    # to the stop the base can make before front_stop
    dr = _drive_route()
    loose = dataclasses_replace(dr.lane_limits(0.9, 0.6), front_v=2.0, front_slow=0.6)
    for front in (0.7, 0.9, 1.2, 1.6):
        cmd = _target(loose, front)
        assert cmd.why == "stop"
        assert cmd.vx <= rl.stop_speed(front - loose.front_stop, dr.STOP_REACT_S, dr.STOP_DECEL) + 1e-9


def dataclasses_replace(lim, **kw):
    import dataclasses
    return dataclasses.replace(lim, **kw)


def _stop_sim(lim, wall, appear_x=None, lane=6.0, lag=3, decel=0.5, accel=0.5, age=2, dt=0.1):
    """1-D lane 0 -> `lane`, a wall across it at x = `wall` (in the strip from the
    moment the robot passes `appear_x`), the measured base (module docstring).
    Returns where it came to rest."""
    x = v = 0.0
    queue, seen, prev = [0.0] * lag, [0.0] * (age + 1), (0.0, 0.0)
    for _ in range(3000):
        seen = seen[1:] + [x]
        front = None
        if appear_x is None or x >= appear_x:
            front = wall - seen[0]
            front = None if front > 3.0 else front
        cmd = rl.lane_command((x, 0.0, 0.0), (0.0, 0.0), (lane, 0.0), prev, dt, front, lim)
        if cmd.done:
            break
        prev = (cmd.vx, cmd.omega)
        queue.append(cmd.vx)
        vc = queue.pop(0)
        v += max(-decel * dt, min(accel * dt, vc - v))
        x += v * dt
        if v < 1e-4 and cmd.vx < 1e-4 and queue == [0.0] * lag:
            break
    while v > 1e-4:                                   # the commands still in flight are zeros or less
        v = max(0.0, v - decel * dt)
        x += v * dt
    return x


@pytest.mark.parametrize("appear", [None, 1.6, 1.4, 1.2, 1.0, 0.9])
def test_something_that_appears_at_the_old_safe_distance_still_stops_where_it_did(appear):
    # The old lane began to slow at front_slow, 1.20 m: something stepping into
    # the strip there (or anywhere it is seen from 0.9 m or more) stops the 0.45
    # lane where it stopped the 0.35 one, the base braking as measured.
    dr = _drive_route()
    new = dr.lane_limits(dr.LANE_VMAX, 0.6, dr.SPIN_FAST)
    wall = 3.0 + (appear or 0.0) if appear else 3.0
    at = 3.0 if appear else None
    old_gap = wall - _stop_sim(OLD, wall, at)
    new_gap = wall - _stop_sim(new, wall, at)
    assert new_gap >= old_gap - 0.005, (appear, old_gap, new_gap)
    assert new_gap >= OLD.front_stop - 0.04                 # at the stop distance, the base's lag past it


@pytest.mark.parametrize("lag", [2, 3, 5])
def test_a_lane_arrives_at_its_end_no_faster_than_before(lag):
    # checkpoints, doors' aims and corners are reached at the end of a lane: over
    # its last 15 cm the faster lane is going no faster than the old one was, on
    # a good link and with the loop stalled half a second (7 ต.ค. 17:31). With the
    # 0.45 s anticipation kept at 0.45 m/s it came in up to 6 cm/s faster.
    dr = _drive_route()
    new = dr.lane_limits(dr.LANE_VMAX, 0.6, dr.SPIN_FAST)

    def profile(lim):
        x = v = 0.0
        queue, prev, out = [0.0] * lag, (0.0, 0.0), []
        for _ in range(600):
            cmd = rl.lane_command((x, 0.0, 0.0), (0.0, 0.0), (3.0, 0.0), prev, 0.1, None, lim)
            out.append((3.0 - x, v))
            if cmd.done:
                break
            prev = (cmd.vx, cmd.omega)
            queue.append(cmd.vx)
            v += max(-0.05, min(0.05, queue.pop(0) - v))
            x += v * 0.1
        return out
    old_p, new_p = profile(OLD), profile(new)

    def speed_at(p, rem):
        return min(p, key=lambda r: abs(r[0] - rem))[1]
    assert max(v for _, v in new_p) > 0.44                   # it does cruise faster
    for rem in (0.15, 0.10, 0.05):
        assert speed_at(new_p, rem) <= speed_at(old_p, rem) + 0.005, rem
    # and it ends no further past the target than before
    end_old, _ = simulate((0.0, 0.0, 0.0), (0.0, 0.0), (2.5, 0.0), latency=lag, limits=OLD)
    end_new, _ = simulate((0.0, 0.0, 0.0), (0.0, 0.0), (2.5, 0.0), latency=lag, limits=new)
    assert end_new[0] <= end_old[0] + 0.005


class _BlobAhead(_ClearWatcher):
    """Nothing until the robot passes x = 0.5, then someone 1.0 m ahead of it,
    standing (the strip's points from where the robot is now)."""
    def __init__(self, sdk, at=0.5, ahead=1.0):
        super().__init__()
        self.sdk, self.at, self.blob = sdk, at, None
        self.ahead = ahead

    def points(self):
        x = self.sdk.pose[0]
        if self.blob is None and x >= self.at:
            self.blob = x + self.ahead
        if self.blob is None:
            return np.zeros((0, 2))
        d = self.blob - x
        return np.c_[np.full(9, d), np.linspace(-0.2, 0.2, 9)]


def test_a_lane_slows_at_once_for_someone_who_steps_in_and_logs_the_free_run(monkeypatch):
    dr = _drive_route()
    r = _lane_runner(dr, (0.0, 0.0, 0.0), monkeypatch)
    r.sdk = _VelocitySdk((0.0, 0.0, 0.0))
    r.b.pose = lambda: tuple(r.sdk.pose)
    r.b.set_speed = lambda v: None
    r.limits = dr.lane_limits(dr.LANE_VMAX, 0.6, dr.SPIN_FAST)
    monkeypatch.setattr(dr, "LANE_HOLD_S", 0.5)
    w = _BlobAhead(r.sdk)
    r.watcher = lambda: w
    with pytest.raises(dr.Stop, match="held"):
        r.drive_velocity(rl.Step(do="drive", to=(4.0, 0.0), label="a long lane"))
    rows = [t for t in r.logged[-1]["trace"] if isinstance(t, tuple)]
    first = next(i for i, t in enumerate(rows) if t[9] is not None)
    assert rows[first][9] == pytest.approx(0.70, abs=0.06)    # 1.0 m ahead of the lidar, less the front 0.30
    assert rows[first][10] == "front"                        # held under the lane's speed, and why
    assert rows[first][4] < rows[first - 1][4]               # slowing on the very tick it is seen
    assert max(t[4] for t in rows) > 0.40                    # it was going at the new cruise
    assert w.blob - r.sdk.pose[0] >= OLD.front_stop - 0.01   # and stood at the stop distance, not past it


# -- the knob, and what keeps its old speed ---------------------------------------

def _args(*extra):
    dr = _drive_route()
    seen = {}
    real = dr._main

    def capture(args, watch):
        seen["args"] = args
        return 0
    dr._main = capture
    try:
        assert dr.main(["route.json", *extra]) == 0
    finally:
        dr._main = real
    return dr, seen["args"]


def test_the_launchers_runs_go_at_the_new_speeds_and_one_knob_dials_them_back():
    dr, args = _args()
    assert dr.run_speeds(args) == pytest.approx((0.45, 0.8))
    dr, args = _args("--speed-scale", "0.78")
    v, spin = dr.run_speeds(args)
    assert v == pytest.approx(0.351) and spin == pytest.approx(0.624)
    assert dr.lane_limits(v, 0.6, spin).w_spin_fast == pytest.approx(0.624)


@pytest.mark.parametrize("scale", ["0", "-0.5", "1.5"])
def test_the_knob_only_dials_back_and_says_no_before_touching_the_board(scale, tmp_path):
    dr = _drive_route()
    assert dr.main([str(tmp_path / "no_such_route.json"), "--speed-scale", scale]) == dr.EXIT_REFUSED


def test_the_launchers_fixed_argv_still_parses_and_gets_the_new_speeds():
    from tests.test_emma_nav_launcher import nav
    leg = types.SimpleNamespace(from_step=1, to_step=10, min_battery=30)
    cmd = nav.build_command(types.SimpleNamespace(py="python"), "data/robot/routes/floor2_bigroom_roundtrip.json",
                            leg, True, os.getppid(), arms_in=True)
    assert cmd[2] == "scripts/drive_route.py"
    dr, args = _args(*cmd[4:])
    assert dr.run_speeds(args) == pytest.approx((dr.LANE_VMAX, dr.SPIN_FAST))
    assert args.to_step == 10 and args.arms_in
    assert args.door_memory == "../nav/door_memory.json"            # 9 ต.ค.: each door's middle, kept


def test_steps_sideways_and_the_dock_keep_the_speed_they_were_validated_at(monkeypatch):
    dr = _drive_route()
    r = _lane_runner(dr, (0.0, 0.0, 0.0), monkeypatch)
    r.limits = dr.lane_limits(0.45, 0.6, 0.8)
    r.direct_limits = dr.lane_limits(min(0.45, dr.DIRECT_V_MAX), 0.6, 0.8)
    assert r.limits_for(direct=False).v_max == 0.45
    d = r.limits_for(direct=True)
    assert (d.v_max, d.front_v, d.front_slow) == (OLD.v_max, OLD.front_v, pytest.approx(OLD.front_slow))
    got = []
    r.drive_velocity = lambda step, direct=False, **kw: got.append((direct, kw.get("limits")))
    r.rotate_to = lambda h, **kw: 0.0
    r.b.scan = lambda: np.zeros((0, 2))
    r._step_blocked = lambda q, off, slant: None
    r.step_across(rl.Step(do="door", door="D8", heading_deg=0, until=2.0, label="D8"), 0.0, 0.04, face=1.5)
    (direct, lim), = got
    assert direct and lim.v_max == OLD.v_max and lim.front_v == OLD.front_v


# -- spot turns ---------------------------------------------------------------

class _TurnSdk(_VelocitySdk):
    def __init__(self, pose):
        super().__init__(pose)
        self.sent = []

    def call(self, command, args=None, timeout=None):
        if command == "setvelocity":
            self.sent.append(args["omega"])
        return super().call(command, args, timeout)


def _turn_runner(dr, monkeypatch, yaw_deg):
    r = _lane_runner(dr, (0.0, 0.0, math.radians(yaw_deg)), monkeypatch)
    r.sdk = sdk = _TurnSdk((0.0, 0.0, math.radians(yaw_deg)))
    r.b.pose = lambda: tuple(sdk.pose)
    r.turn_space_clear = lambda turn=None, wait_s=6.0: None
    r.limits = dr.lane_limits(0.45, 0.6, 0.8)
    return r, sdk


@pytest.mark.parametrize("off_deg,turns", [(2.1, False), (2.2, False), (2.4, True), (5.0, True)])
def test_a_turn_too_small_for_the_base_to_start_is_not_sent(monkeypatch, off_deg, turns):
    # 9 ต.ค., D8 out: 2.08° off, three goes of one 0.15 rad/s tick each, 0.7 s, the
    # base never moved (it needs 0.4-0.7 s of push from rest). Under the slowest
    # turning rate's coast (2.2°) nothing is sent; over it the turn is made as before.
    dr = _drive_route()
    r, sdk = _turn_runner(dr, monkeypatch, -off_deg)
    r.turn_velocity(0.0, tol_deg=2.0)
    assert bool([w for w in sdk.sent if w]) == turns
    assert any(e.get("kind") == "turn_skipped" for e in r.logged) != turns


@pytest.mark.parametrize("board_idle", [False, True])
def test_docking_turns_keep_the_validated_rate_and_the_others_turn_fast(monkeypatch, board_idle):
    # the half turn before backing in decides how crooked it meets the charger
    dr = _drive_route()
    r, sdk = _turn_runner(dr, monkeypatch, 0.0)
    r.turn_velocity(math.pi - 0.05, board_idle=board_idle)
    top = max(abs(w) for w in sdk.sent)
    assert top == pytest.approx(0.5 if board_idle else 0.8, abs=1e-6)


# -- what the run log says afterwards -------------------------------------------

def _analyze_run():
    spec = importlib.util.spec_from_file_location("analyze_run_speed_test", ROOT / "scripts" / "analyze_run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_summary_says_where_a_lane_slowed_for_something_and_why_the_dock_gap_was_not_used():
    ar = _analyze_run()
    row = lambda t, v, free, why: (0.0, 0.0, 0.0, 0.0, v, 0.0, "drive", t, 0.05, free, why)   # noqa: E731
    events = [{"kind": "run_start", "t": 0.0},
              {"kind": "step", "i": 1, "do": "drive", "label": "east", "t": 0.0},
              {"kind": "drive", "label": "east", "seconds": 3.0, "t": 3.0,
               "trace": [row(0.0, 0.45, None, None), row(0.1, 0.40, 0.95, "front"), row(0.2, 0.35, 0.80, "front"),
                         row(0.3, 0.35, 0.80, None), "held beside", row(0.4, 0.35, None, None)]},
              {"kind": "dock_gap", "where": "approach", "fix": None, "why": "the wall behind: a stretch out of sight",
               "t": 3.5},
              {"kind": "turn_skipped", "t": 3.6}, {"kind": "leg_done", "t": 4.0}]
    s = ar.summarize(events)
    assert s["slowed_ahead"] == [{"label": "east", "seconds": 0.2, "least_free_m": 0.8}]
    assert s["turns_skipped"] == 1
    text = ar.report(s)
    assert "slowed for something ahead: 0.2 s" in text and "0.80 m free" in text
    assert "dock gap not used (approach): the wall behind: a stretch out of sight" in text
