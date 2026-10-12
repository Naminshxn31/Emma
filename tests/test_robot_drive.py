"""Smooth lane driving (robot_lanes.lane_command / turn_command) on a simulated robot.

The simulation is deliberately unkind: commands arrive one cycle late (the
relay), the robot only follows them with its own acceleration limit, and a
turn-rate bias can be added (a base without an IMU that pulls to one side).
What the owner asked for is the test: "สมูท รวดเร็ว แม่นยำ" — no wobble, no
overshoot, stopping where it was sent.
"""
import concurrent.futures
import math

import httpx

import numpy as np
import pytest

from app import robot_lanes as rl


def simulate(pose, start, target, *, dt=0.1, latency=1, robot_accel=0.5, bias=0.0, wall_at=None,
             limits=rl.DriveLimits(), steps=600, w_deadband=0.0, v_deadband=0.0, w_breakaway=None):
    """w_deadband: turn commands smaller than this do nothing and larger ones
    lose that much — the casters have to swivel before the base turns (6 ต.ค.
    logs: commanded 0.028 rad/s gave 0.002-0.005, 0.051 gave 0.023).
    v_deadband: forward commands below this do not move the base at all.
    w_breakaway=(threshold, gain): the base as measured driving at 0.2 m/s
    (6 ต.ค. 16:04, constant turns for 1-1.4 s): 0.04, 0.05, 0.06 rad/s turned
    it 0.0-0.2°, 0.08 turned it 4.8° in a second (155%) — nothing until the
    casters break free, then more than asked."""
    x, y, yaw = pose
    v = w = 0.0
    queue = [(0.0, 0.0)] * latency
    prev = (0.0, 0.0)
    log = []
    for _ in range(steps):
        front = None
        if wall_at is not None:
            front = wall_at - x                       # an obstacle across the lane at x = wall_at
        cmd = rl.lane_command((x, y, yaw), start, target, prev, dt, front, limits)
        log.append((x, y, yaw, v, w, cmd))
        if cmd.done:
            break
        prev = (cmd.vx, cmd.omega)
        queue.append(prev)
        vc, wc = queue.pop(0)
        wc = math.copysign(max(0.0, abs(wc) - w_deadband), wc)
        if w_breakaway is not None:
            wc = 0.0 if abs(wc) < w_breakaway[0] else w_breakaway[1] * wc
        if abs(vc) < v_deadband and abs(v) < 1e-3:
            vc = 0.0
        v += max(-robot_accel * dt, min(robot_accel * dt, vc - v))
        w += max(-3.0 * dt, min(3.0 * dt, wc - w))
        x += v * math.cos(yaw) * dt
        y += v * math.sin(yaw) * dt
        yaw += (w + bias) * dt
    # the robot coasts to a stop on its own acceleration limit after the last command
    while abs(v) > 1e-3:
        v -= math.copysign(min(abs(v), robot_accel * dt), v)
        x += v * math.cos(yaw) * dt
        y += v * math.sin(yaw) * dt
    return (x, y, yaw), log


def omega_sign_changes(log, threshold=0.02):
    signs = [math.copysign(1, c.omega) for *_, c in log if abs(c.omega) > threshold]
    return sum(1 for a, b in zip(signs, signs[1:]) if a != b)


def test_a_straight_lane_is_driven_straight_and_stops_on_the_target():
    end, log = simulate((0.0, 0.0, 0.0), (0.0, 0.0), (2.0, 0.0))
    assert end[0] == pytest.approx(2.0, abs=0.03)
    assert max(abs(r[1]) for r in log) < 0.005
    assert max(r[3] for r in log) == pytest.approx(0.35, abs=0.02)     # it actually cruises


def test_a_fast_lane_ends_on_its_target_even_when_the_link_lags_half_a_second():
    # 7 ต.ค. 17:31: 0.40 m/s, a 0.48 s stall of the loop as braking was due, 13 cm past
    dr = _drive_route()
    end, _ = simulate((0.0, 0.0, 0.0), (0.0, 0.0), (2.5, 0.0), latency=5, limits=dr.lane_limits(0.4, 0.5))
    assert end[0] - 2.5 < 0.035
    end, _ = simulate((0.0, 0.0, 0.0), (0.0, 0.0), (2.5, 0.0), latency=2, limits=dr.lane_limits(0.4, 0.5))
    assert abs(end[0] - 2.5) < 0.03                               # and still reaches it on a good link


def test_starting_beside_the_lane_it_curves_in_without_swinging_across():
    end, log = simulate((0.0, 0.10, 0.0), (0.0, 0.0), (3.0, 0.0))
    assert abs(end[1]) < 0.02
    assert min(r[1] for r in log) > -0.02                 # no overshoot past the lane
    assert omega_sign_changes(log) <= 2


def test_no_wobble_even_with_a_slow_link_and_a_pulling_base():
    # 300 ms of command lag (the Wi-Fi relay on a bad minute, 6 ต.ค.) plus a
    # pulling base: steering that is too eager starts to weave here first.
    # The base has the casters' dead zone as measured: the controller adds it to
    # every correction, so a model without it would be a different robot.
    end, log = simulate((0.0, 0.15, 0.0), (0.0, 0.0), (3.0, 0.0), latency=3, bias=0.02, w_breakaway=(0.07, 1.4))
    # in, straighten, one trim — 3 changes on casters that only turn past the
    # breakaway; a weave would cross the lane, which the next line forbids
    assert omega_sign_changes(log) <= 3
    assert min(r[1] for r in log) > -0.01
    assert abs(end[1]) < 0.02


def test_a_big_heading_error_is_turned_out_on_the_spot_first():
    end, log = simulate((0.0, 0.0, math.radians(60)), (0.0, 0.0), (2.0, 0.0))
    phases = [c.phase for *_, c in log]
    assert phases[0] == "align"
    first_drive = phases.index("drive")
    assert all(abs(r[3]) < 0.06 for r in log[:first_drive])   # no driving while badly turned
    assert end[0] == pytest.approx(2.0, abs=0.03)
    assert abs(end[1]) < 0.03


def test_a_base_that_pulls_to_one_side_is_held_on_the_lane():
    # 0.03 rad/s of bias: 1.7°/s, about what an IMU-less base drifts by.
    end, log = simulate((0.0, 0.0, 0.0), (0.0, 0.0), (4.0, 0.0), bias=0.03)
    assert max(abs(r[1]) for r in log) < 0.03
    assert end[0] == pytest.approx(4.0, abs=0.04)


def test_something_in_the_way_stops_it_before_the_stop_distance():
    end, log = simulate((0.0, 0.0, 0.0), (0.0, 0.0), (4.0, 0.0), wall_at=2.0, steps=300)
    # front = 2.0 - x; the robot must come to rest with front >= front_stop minus its stopping slack
    assert 2.0 - end[0] >= rl.DriveLimits().front_stop - 0.05
    assert end[0] > 1.2                                   # but it did drive up to it


class SpotTurnBase:
    """Turning on the spot as measured on the robot (6 ต.ค. 16:0x, ±0.06…±0.35
    rad/s, alternating): under 0.13 rad/s it does not turn at all (0.06 and
    0.10 gave 0°); above, ~0.8 of the command (0.15 -> 8°/s, 0.35 -> 16°/s);
    from standstill it starts 0.4-0.7 s late while the casters swing round;
    and after a zero it coasts 1.6-2.6° from 8°/s, 5-5.7° from 16°/s."""

    def __init__(self, yaw=0.0, dt=0.1, lag=2, gain=0.8, down=1.0):
        self.yaw, self.w, self.dt = yaw, 0.0, dt
        self.queue = [0.0] * lag                    # 0.2 s of link + board lag
        self.pushing = 0.0                          # how long it has been asked to start
        self.gain, self.down = gain, down           # (varied to test what was not measured)

    def step(self, cmd):
        self.queue.append(cmd)
        c = self.queue.pop(0)
        target = 0.0 if abs(c) < 0.13 else self.gain * c
        if self.w == 0.0 and target != 0.0:
            self.pushing += self.dt
            if self.pushing < 0.4:                  # casters swinging round
                target = 0.0
        elif target == 0.0:
            self.pushing = 0.0
        rate = 0.5 if abs(target) > abs(self.w) else self.down
        self.w += max(-rate * self.dt, min(rate * self.dt, target - self.w))
        if abs(self.w) < 1e-4:
            self.w = 0.0
        self.yaw += self.w * self.dt


def spin(target_deg, start_deg=0.0, tol_deg=2.0, limits=rl.DriveLimits(), tries=3, base=None):
    """What Runner.turn_velocity does: run turn_command to done, let the base
    settle, and correct again if it is still out (at most `tries`)."""
    base = SpotTurnBase(math.radians(start_deg), **(base or {}))
    t, beyond = 0.0, 0.0
    target = math.radians(target_deg)
    sign = math.copysign(1, rl.wrap_pi(target - math.radians(start_deg)))

    def off():
        return math.degrees(rl.wrap_pi(base.yaw - target))

    for _ in range(tries):
        prev = 0.0
        for _ in range(300):
            c = rl.turn_command(base.yaw, target, prev, 0.1, limits, tol_deg=tol_deg)
            if c.done:
                break
            prev = c.omega
            base.step(c.omega)
            t += 0.1
            beyond = max(beyond, sign * off())
        for _ in range(15):                         # stop() waits for the board to go idle
            base.step(0.0)
            t += 0.1
            beyond = max(beyond, sign * off())
        if abs(off()) <= tol_deg:
            break
    return off(), beyond, t


def test_a_quarter_turn_on_the_spot_lands_on_the_angle_and_does_not_take_half_a_minute():
    # 6 ต.ค. 15:23: 90° took 27 s and still ended 3-4° off — near the angle the
    # rate dropped to 0.08 rad/s, which does not turn this base at all, and it
    # sat there until the 25 s limit.
    err, beyond, t = spin(90)
    assert abs(err) <= 2.0
    assert beyond < 2.5                             # no swing past and back: each reversal scrubs the casters
    assert t < 12.0


def test_spot_turns_cruise_fast_enough_and_still_land_on_the_angle():
    # 7 ต.ค. (owner: "ทำให้ไวกว่านี้"): ~14 spot turns a round at ~8 s per 90° and
    # 13 s per 180°, most of it at the 0.35 cruise rate
    err90, beyond90, t90 = spin(90)
    err180, beyond180, t180 = spin(180, -90)
    assert t90 < 7.0 and t180 < 11.0
    assert abs(err90) <= 2.0 and abs(err180) <= 2.0
    assert beyond90 < 2.5 and beyond180 < 2.5


@pytest.mark.parametrize("target,start", [(90, 0), (-90, 0), (-179, 0)])
def test_a_turn_allowed_5_degrees_still_lands_on_the_angle(target, start):
    # owner 8 ต.ค.: "ไม่จำเป็นต้องหันเพื่อไปปรับให้ตรงมุมไหม ไม่งั้นจะเสียเวลา" -- every
    # 90° turn before a stop (allowed 5°) stopped the moment it was within 5° and
    # coasted to 2.7-3.7° short (60 turns, 7-8 ต.ค.); each door then turned again
    # for its 2°, ~2 s four times a round. 5° says whether to turn, not where.
    err, beyond, t = spin(target, start, tol_deg=5.0)
    assert abs(err) <= 2.0, err
    assert beyond < 2.5


def test_within_the_allowance_it_does_not_turn_at_all():
    err, beyond, t = spin(4, tol_deg=5.0)
    assert t == pytest.approx(1.5)                  # only the settle: no turn sent
    assert err == pytest.approx(-4.0)


def test_a_small_correction_on_the_spot_still_turns():
    # 4° is under every rate that turns it if the rate is scaled by the error
    err, beyond, t = spin(4)
    assert abs(err) <= 2.0
    assert t < 8.0


def test_spot_turns_both_ways_and_from_odd_angles():
    for target, start in ((-90, 0), (180, -90), (35, 0), (-12, 0)):
        err, beyond, t = spin(target, start)
        assert abs(err) <= 2.0, (target, start, err)
        assert beyond < 2.5, (target, start, beyond)


def test_a_short_lane_ends_pointing_along_the_lane_not_at_the_target():
    # 6 ต.ค. 15:31, west lane 0.65 m starting 3 cm beside it: the pursuit point
    # had shrunk onto the target, the last centimetre was steered at a steep
    # angle, and the robot stopped 0.3 cm from the line but 8.5° off it — the
    # next leg then starts with a turn the owner sees as a wobble.
    end, log = simulate((0.0, 0.03, 0.0), (0.0, 0.0), (0.65, 0.0), **CASTERS)
    assert abs(math.degrees(end[2])) < 3.0
    # 3 cm over 0.65 m is under what the casters let it correct without
    # breaking free and swinging back: it is held, not chased
    assert abs(end[1]) < 0.035
    assert end[0] == pytest.approx(0.65, abs=0.03)


CASTERS = dict(w_breakaway=(0.07, 1.4), latency=3, limits=rl.DriveLimits(v_max=0.2, w_max=0.4))


def test_casters_that_swallow_small_turns_do_not_let_the_lane_drift():
    # 6 ต.ค. 15:33: 1 cm off and 1.8° off at the start of the dock lane, the
    # controller asked for +0.022..+0.039 rad/s for 7 s and the base hardly
    # turned (-1.8° -> -1.4°): 4.7 cm off the lane by the end of 1.6 m. The
    # owner then pointed at the cause — two drive wheels and swivel casters.
    end, log = simulate((0.0, -0.01, math.radians(-1.8)), (0.0, 0.0), (1.6, 0.0), **CASTERS)
    assert max(abs(r[1]) for r in log) < 0.025
    assert abs(end[1]) < 0.015


def test_through_the_dead_zone_without_flipping_left_and_right():
    # Every change of turn direction swivels the casters round again — the
    # scraping the owner hears. Holding a lane must not chatter.
    end, log = simulate((0.0, 0.04, 0.0), (0.0, 0.0), (3.0, 0.0), **CASTERS)
    assert omega_sign_changes(log, threshold=0.0) <= 2
    assert abs(end[1]) < 0.015


def test_it_finishes_the_lane_instead_of_hovering_short_of_it():
    # 6 ต.ค. 15:33: 3.4 cm short, 0.62 m from the wall, the commands cycled
    # 0 -> 0.035 -> 0.059 -> 0 for 8 s. Each pulse was too short for the base
    # to move with 0.3 s of lag, so the pose never changed and the cycle never
    # ended until the stall guard gave up. Held at that exact spot, the
    # controller must settle on a speed the base actually moves at.
    limits = rl.DriveLimits(v_max=0.2, w_max=0.4)
    pose, prev, sent = (-2.484, 1.43, 0.0), (0.0, 0.0), []
    for _ in range(20):
        cmd = rl.lane_command(pose, (-4.03, 1.43), (-2.45, 1.43), prev, 0.1, 0.62, limits)
        assert not cmd.done
        sent.append(cmd.vx)
        prev = (cmd.vx, cmd.omega)
    assert min(sent[3:]) >= limits.v_creep * 0.99


def test_the_stop_goes_out_before_the_angle_by_the_coast():
    # Sent AT the angle, the zero lands ~2° past it (it coasts a third of a
    # second) and the way back is a reversal — the casters swung round again
    # for a correction too small to make (anything under ~2° does not start
    # it moving before the stop arrives).
    err, beyond, t = spin(90, tol_deg=1.0)
    assert beyond < 0.8
    assert abs(err) <= 1.0


def test_holding_the_lane_on_casters_that_break_free_does_not_chatter():
    # Compensating up to the breakaway (0.07) held the lane to 0.8 cm in
    # simulation — by flipping left/right 18-20 times a lane, each flip the
    # casters swinging round: the noise the owner asked to get rid of.
    # Compensating to just under it corrects only offsets that grow, a few
    # centimetres, quietly.
    for lat0, h0 in ((0.03, 0.0), (-0.01, -1.8), (0.0, 2.3), (0.10, 0.0)):
        end, log = simulate((0.0, lat0, math.radians(h0)), (0.0, 0.0), (3.0, 0.0), **CASTERS)
        assert omega_sign_changes(log, threshold=0.0) <= 2, (lat0, h0)
        assert max(abs(r[1]) for r in log if r[0] > 1.0) < 0.035, (lat0, h0)


def test_the_recorded_gohome_is_summarised_as_heard():
    # 6 ต.ค. 16:04:31, the board's own GoHome from 0.5 m in front of the dock,
    # facing away: a ~200° spin at 50-58°/s, then backing in with the turn
    # flipping left/right about a dozen times — the scraping the owner heard.
    import json
    from pathlib import Path
    trace = json.loads((Path(__file__).parent / "data" / "robot" / "gohome_trace_20261006.json").read_text())
    s = rl.turn_summary(trace)
    assert 45 <= s["peak_rate_deg"] <= 65
    assert 9 <= s["reversals"] <= 16
    assert s["turned_deg"] > 200


def test_a_smooth_turn_has_no_reversals():
    trace = [(i * 0.1, 0.0, 0.0, min(90.0, i * 1.6)) for i in range(80)]    # 16°/s, one way
    s = rl.turn_summary(trace)
    assert s["reversals"] == 0
    assert s["peak_rate_deg"] == pytest.approx(16, abs=1)


def _drive_route():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "scripts" / "drive_route.py"
    spec = importlib.util.spec_from_file_location("drive_route_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _DockBoard:
    """Just enough of drive_route.Board for Runner.dock."""
    def __init__(self, start_fails=False):
        self.params = {"base.max_angular_speed": 1.0}
        self.writes, self.start_fails, self.polls = [], start_fails, 0

    def param(self, name):
        return self.params[name]

    def set_param(self, name, value):
        self.writes.append((name, value))
        self.params[name] = value

    def start(self, name, options):
        if self.start_fails:
            raise RuntimeError("relay dropped")
        return 7

    def power(self):
        self.polls += 1
        on = self.polls > 1
        return {"dockingStatus": "on_dock" if on else "not_on_dock", "isCharging": on}

    def current(self):
        return 7

    def state(self, aid):
        return {}

    def pose(self):
        return (-4.29, 1.43, -0.09)

    def home_pose(self):
        return (-4.544, 1.4522, -0.089)


def _dock_runner(dr, board, monkeypatch):
    monkeypatch.setattr(dr.time, "sleep", lambda s: None)
    r = dr.Runner.__new__(dr.Runner)
    r.b, r.sdk = board, None
    r.route = rl.Route(name="t", anchors={}, doors={}, steps=[], dock_pose=(-4.295, 1.43, -0.089))
    r.say = lambda m: None
    r.logged = []
    r.log = lambda **e: r.logged.append(e)
    r.fast_pose = board.pose
    r.turn_width = rl.ROBOT_WIDTH
    return r


def test_gohome_runs_with_the_turn_rate_capped_and_puts_it_back(monkeypatch):
    dr = _drive_route()
    board = _DockBoard()
    r = _dock_runner(dr, board, monkeypatch)
    r.dock(rl.Step(do="dock", label="dock"))
    assert board.writes == [("base.max_angular_speed", dr.DOCK_ANGULAR_SPEED), ("base.max_angular_speed", 1.0)]
    assert r.logged[-1]["ok"] is True and "turn" in r.logged[-1]


def test_the_turn_rate_goes_back_even_when_gohome_blows_up(monkeypatch):
    # Left capped, every later move on the board — the vendor app's included —
    # would turn at a third of its speed with nobody knowing why.
    dr = _drive_route()
    board = _DockBoard(start_fails=True)
    r = _dock_runner(dr, board, monkeypatch)
    with pytest.raises(RuntimeError):
        r.dock(rl.Step(do="dock", label="dock"))
    assert board.params["base.max_angular_speed"] == 1.0
    assert board.writes[-1] == ("base.max_angular_speed", 1.0)


def test_the_put_back_is_checked_after_the_board_has_applied_it(monkeypatch):
    # 6 ต.ค. 16:13: read straight after the PUT the board still said 0.35 and
    # a false "not put back" warning went out; it said 1.0 a moment later.
    dr = _drive_route()
    board = _DockBoard()
    lag = {"reads": 0}
    real_set = board.set_param

    def slow_set(name, value):
        real_set(name, value)
        if value == 1.0:
            board.params[name] = dr.DOCK_ANGULAR_SPEED       # not applied yet
            lag["reads"] = 2

    def slow_param(name):
        if lag["reads"]:
            lag["reads"] -= 1
            if not lag["reads"]:
                board.params[name] = 1.0
        return board.params[name]

    board.set_param, board.param = slow_set, slow_param
    r = _dock_runner(dr, board, monkeypatch)
    said = []
    r.say = said.append
    r.dock(rl.Step(do="dock", label="dock"))
    assert not [m for m in said if "WARNING" in m]
    assert board.params["base.max_angular_speed"] == 1.0


class _LandingBoard(_DockBoard):
    def __init__(self, pose, home=(-4.544, 1.4522, -0.089), home_fails=False):
        super().__init__()
        self._pose, self.home, self.home_fails = pose, home, home_fails

    def pose(self):
        return self._pose

    def home_pose(self):
        if self.home_fails:
            raise ValueError("no marker")
        return self.home


def _landing_runner(dr, board, monkeypatch):
    r = _dock_runner(dr, board, monkeypatch)
    r.sdk = object()
    r.calls = []
    r.drive_velocity = lambda step, direct=False: r.calls.append(("drive", tuple(step.to), direct))
    r.turn_velocity = lambda heading, **kw: r.calls.append(("turn", round(heading, 3)))
    return r


def test_the_last_lane_hands_gohome_the_robot_on_its_landing_spot(monkeypatch):
    # GoHome backs in from 1.04 m out along the marker's heading; anywhere else
    # it first drives there itself — 0.28-0.30 m with turns (6 ต.ค. 16:04/16:12).
    dr = _drive_route()
    r = _landing_runner(dr, _LandingBoard((-3.0, 1.43, math.pi)), monkeypatch)
    r.approach_landing()
    (kind, to, direct), turn = r.calls
    assert kind == "drive" and direct
    assert to == pytest.approx((-3.508, 1.359), abs=0.005)
    assert turn == ("turn", -0.089)                         # back to the charger


def test_past_the_landing_spot_it_does_not_try_to_drive_backwards(monkeypatch):
    dr = _drive_route()
    r = _landing_runner(dr, _LandingBoard((-3.80, 1.43, math.pi)), monkeypatch)
    r.approach_landing()
    assert r.calls == [("turn", -0.089)]


def test_without_a_dock_marker_it_still_turns_round_for_gohome(monkeypatch):
    dr = _drive_route()
    r = _landing_runner(dr, _LandingBoard((-3.0, 1.43, math.pi), home_fails=True), monkeypatch)
    r.approach_landing()
    assert r.calls == [("turn", -0.089)]


class _LaneSdk:
    """Each pose read moves the robot 5 cm along its heading."""
    def __init__(self, pose):
        self.pose = pose

    def call(self, command, args=None, timeout=None):
        if command == "velocitycontrol":
            return {"code": 1, "id": 5}
        if command == "getpose":
            x, y, yaw = self.pose
            self.pose = (x + 0.05 * math.cos(yaw), y + 0.05 * math.sin(yaw), yaw)
            return {"x": x, "y": y, "yaw": yaw}
        if command == "getcurrentaction":
            return {"id": 0}
        return {"code": 0, "codestr": "done"}


class _Watcher:
    def __init__(self, sides):
        self.sides = sides

    def start(self):
        pass

    def stop(self):
        pass

    def age(self):
        return 0.1

    def front(self):
        return None

    def clearance(self):
        return self.sides

    def points(self):
        # walls beside the robot giving those gaps from the 0.78 m arms
        xs = np.linspace(-0.25, 0.25, 26)
        left, right = self.sides
        return np.vstack([np.c_[xs, np.full(26, rl.ROBOT_HALF_WIDTH + left)],
                          np.c_[xs, np.full(26, -(rl.ROBOT_HALF_WIDTH + right))]])


def test_a_lane_stops_for_something_close_beside_it(monkeypatch):
    # D8's leaf stands open alongside the lane in front of the door, 12 cm from
    # the arm — outside the ahead-strip, so only a side check sees it coming.
    # Something that stays there ends the route once the hold is out (it was
    # at once; people beside the robot made that end routes, 7 ต.ค. 15:28).
    dr = _drive_route()
    monkeypatch.setattr(dr, "LANE_HOLD_S", 0.3)
    for sides, stops in (((0.30, 0.02), True), ((0.30, 0.12), False)):
        board = _LandingBoard((-2.45, 1.0, -math.pi / 2))
        r = _dock_runner(dr, board, monkeypatch)
        r.sdk = (_StillSdk if stops else _LaneSdk)(board.pose())
        board.pose = lambda: tuple(r.sdk.pose)
        r.fast_pose = dr.Runner.fast_pose.__get__(r)
        r.fences, r.limits = [], rl.DriveLimits(v_max=0.2, w_max=0.4)
        board.events = lambda: []
        r.watcher = lambda: _Watcher(sides)
        if stops:
            with pytest.raises(dr.Stop, match="beside"):
                r.drive_velocity(rl.Step(do="drive", to=(-2.45, -0.95), label="south to the door"))
        else:                                               # 12 cm, as at D8: drives on to the end
            r.drive_velocity(rl.Step(do="drive", to=(-2.45, -0.95), label="south to the door"))
            assert r.logged[-1]["stopped"] is None


class _PassingBy(_Watcher):
    """Someone beside the robot for the first `n` looks, then gone."""
    def __init__(self, n):
        super().__init__((0.30, 0.0))
        self.n = n

    def points(self):
        self.n -= 1
        if self.n >= 0:
            return super().points()
        return np.zeros((0, 2))


def test_someone_beside_a_lane_is_waited_for_and_the_lane_goes_on(monkeypatch):
    # 15:28, between the meeting rooms: 0.00 m beside, a moment later 0.4 m clear
    dr = _drive_route()
    board = _LandingBoard((2.52, -0.55, -math.pi / 2))
    r = _dock_runner(dr, board, monkeypatch)
    r.sdk = _LaneSdk(board.pose())
    board.pose = lambda: tuple(r.sdk.pose)
    r.fast_pose = dr.Runner.fast_pose.__get__(r)
    r.fences, r.limits = [], rl.DriveLimits(v_max=0.2, w_max=0.4)
    board.events = lambda: []
    w = _PassingBy(6)
    r.watcher = lambda: w
    r.drive_velocity(rl.Step(do="drive", to=(2.50, -2.45), label="south to the junction"))
    end = r.logged[-1]
    assert end["stopped"] is None and "held beside" in end["trace"]


class _PostsWatcher(_Watcher):
    """7 ต.ค. 11:39, corridor east of D8: the glass wall's posts stand 0.42-0.44 m
    left of the lane, 0.48 m on."""
    def __init__(self):
        super().__init__((None, None))

    def points(self):
        return np.c_[np.full(7, 0.48), np.linspace(0.42, 0.44, 7)]


@pytest.mark.parametrize("width,held", [(0.70, False), (rl.ROBOT_WIDTH, True)])
def test_the_strip_ahead_is_as_wide_as_the_body_counted(monkeypatch, width, held):
    # With the arms where they hang (--arms-in, the owner: "ไม่ได้พับนะ แล้วก็ผ่าน
    # ไปได้เอง") the posts are beside the way, not in it; across 0.78 they hold it.
    dr = _drive_route()
    board = _LandingBoard((-0.64, -2.31, 0.0))
    r = _dock_runner(dr, board, monkeypatch)
    r.sdk = _LaneSdk(board.pose())
    board.pose = lambda: tuple(r.sdk.pose)
    r.fast_pose = dr.Runner.fast_pose.__get__(r)
    r.fences, r.limits = [], rl.DriveLimits(v_max=0.2, w_max=0.4)
    r.turn_width = width
    board.events = lambda: []
    r.watcher = lambda: _PostsWatcher()
    r.drive_velocity(rl.Step(do="drive", to=(1.0, -2.31), label="east along the corridor"))
    speeds = [t[4] for t in r.logged[-1]["trace"] if isinstance(t, tuple)]
    assert (max(speeds) == 0.0) == held


class _ClearWatcher(_Watcher):
    def __init__(self):
        super().__init__((None, None))

    def points(self):
        return np.zeros((0, 2))


def _lane_runner(dr, start, monkeypatch):
    board = _LandingBoard(start)
    r = _dock_runner(dr, board, monkeypatch)
    r.sdk = _LaneSdk(board.pose())
    board.pose = lambda: tuple(r.sdk.pose)
    r.fast_pose = dr.Runner.fast_pose.__get__(r)
    r.fences, r.limits = [], rl.DriveLimits(v_max=0.2, w_max=0.4)
    board.events = lambda: []
    r.watcher = lambda: _ClearWatcher()
    return r


def test_a_lane_that_starts_where_a_checkpoint_put_it_is_not_off_the_lane(monkeypatch):
    # 7 ต.ค. 11:43: the stop before the junction moved y by 0.164 m and the lane
    # after it peaked at 0.179 of the 0.20 limit. 0.25 used to end the route
    # on the first tick; it is the start, not drift.
    dr = _drive_route()
    r = _lane_runner(dr, (1.0, -2.35, 0.0), monkeypatch)
    r.drive_velocity(rl.Step(do="drive", to=(2.46, -2.60), label="east to the junction"))
    assert r.logged[-1]["stopped"] is None


def test_a_lane_will_not_start_further_off_than_it_can_bring_back(monkeypatch):
    dr = _drive_route()
    r = _lane_runner(dr, (1.0, -2.20, 0.0), monkeypatch)
    with pytest.raises(dr.Stop, match="starts 0.40 m off its lane"):
        r.drive_velocity(rl.Step(do="drive", to=(2.46, -2.60), label="east to the junction"))


class _StillSdk(_LaneSdk):
    """A robot held where it stands: pose reads do not move it."""
    def call(self, command, args=None, timeout=None):
        if command == "getpose":
            x, y, yaw = self.pose
            return {"x": x, "y": y, "yaw": yaw}
        return super().call(command, args, timeout)


class _EndWallWatcher(_ClearWatcher):
    """7 ต.ค. 14:33: the step in the corridor's end wall, 0.49 m ahead, 0.20-0.40 m right."""
    def points(self):
        return np.c_[np.full(9, 0.49), np.linspace(-0.40, -0.20, 9)]


def test_a_lane_held_at_the_stop_distance_a_few_cm_from_its_end_has_ended(monkeypatch):
    dr = _drive_route()
    monkeypatch.setattr(dr, "LANE_HOLD_S", 0.3)               # the old way: wait, then end the route
    r = _lane_runner(dr, (2.40, -2.43, 0.0), monkeypatch)
    r.sdk = _StillSdk((2.40, -2.43, 0.0))
    r.b.pose = lambda: tuple(r.sdk.pose)
    r.watcher = lambda: _EndWallWatcher()
    r.drive_velocity(rl.Step(do="drive", to=(2.46, -2.45), label="east to the junction"))
    end = r.logged[-1]
    assert end["stopped"] is None and end["held_short"] == pytest.approx(0.06, abs=0.01)


def test_a_lane_held_far_from_its_end_still_waits_and_stops(monkeypatch):
    dr = _drive_route()
    monkeypatch.setattr(dr, "LANE_HOLD_S", 0.3)
    r = _lane_runner(dr, (1.80, -2.43, 0.0), monkeypatch)
    r.sdk = _StillSdk((1.80, -2.43, 0.0))
    r.b.pose = lambda: tuple(r.sdk.pose)
    r.watcher = lambda: _EndWallWatcher()
    with pytest.raises(dr.Stop, match="held"):
        r.drive_velocity(rl.Step(do="drive", to=(2.46, -2.45), label="east to the junction"))


class _VelocitySdk(_LaneSdk):
    """Moves only as commanded: each setvelocity is 0.1 s at that speed."""
    def call(self, command, args=None, timeout=None):
        if command == "getpose":
            x, y, yaw = self.pose
            return {"x": x, "y": y, "yaw": yaw}
        if command == "setvelocity":
            x, y, yaw = self.pose
            yaw += args["omega"] * 0.1
            self.pose = (x + args["vx"] * 0.1 * math.cos(yaw), y + args["vx"] * 0.1 * math.sin(yaw), yaw)
        return super().call(command, args, timeout)


class _LeafAheadWatcher(_ClearWatcher):
    """7 ต.ค. 15:14: D11's open leaf along y -0.19, the robot turned to face it."""
    def __init__(self, sdk):
        super().__init__()
        self.sdk = sdk

    def points(self):
        return np.c_[np.full(17, -0.19 - self.sdk.pose[1]), np.linspace(-0.40, 0.40, 17)]


def test_a_short_step_toward_a_leaf_half_a_metre_off_is_taken_not_skipped(monkeypatch):
    # 7 ต.ค. 15:14 inside the big room: 3.6 cm south of D11's line, the step
    # north had the leaf 0.47 m ahead -- under a lane's 0.50, so it stood still,
    # and "4 cm short of the end" counted as there. It went on to the door
    # unmoved. The base would end 13 cm from the leaf: a doorway's distance.
    dr = _drive_route()
    start = (3.90, -0.66, math.pi)
    r = _lane_runner(dr, start, monkeypatch)
    r.sdk = sdk = _VelocitySdk(start)
    r.b.pose = lambda: tuple(sdk.pose)
    ring = np.radians(np.arange(0.0, 360.0, 1.0))
    r.b.scan = lambda: np.c_[1.2 * np.cos(ring), 1.2 * np.sin(ring)]      # room to turn

    def turn(heading, **kw):
        sdk.pose = (sdk.pose[0], sdk.pose[1], heading)

    r.turn_velocity = turn
    r.rotate_to = lambda heading, tol_deg=None: turn(heading)
    r.watcher = lambda: _LeafAheadWatcher(sdk)
    r.step_across(rl.Step(do="door", door="D11", heading_deg=180, label="D11 out"), math.pi, +0.036)
    drive = [e for e in r.logged if e.get("kind") == "drive"][-1]
    assert drive["held_short"] is None and drive["stopped"] is None
    assert sdk.pose[1] == pytest.approx(-0.66 + 0.036, abs=0.012)


class _StallsNearTheEnd(_VelocitySdk):
    """Moves as commanded until it is 1.5 cm short of y_end, then not at all."""
    y_end = -0.62

    def call(self, command, args=None, timeout=None):
        if command == "setvelocity" and self.pose[1] >= self.y_end - 0.015:
            args = dict(args, vx=0.0)
        return super().call(command, args, timeout)


def test_a_step_that_stalls_a_centimetre_short_counts_as_stepped(monkeypatch):
    # 8 ต.ค. 09:36: a 10 cm step at D11 stood 2 cm short for 20 s and ended the route
    dr = _drive_route()
    monkeypatch.setattr(dr, "LANE_HOLD_S", 5.0)
    start = (3.90, -0.72, math.pi)
    r = _lane_runner(dr, start, monkeypatch)
    r.sdk = sdk = _StallsNearTheEnd(start)
    r.b.pose = lambda: tuple(sdk.pose)
    ring = np.radians(np.arange(0.0, 360.0, 1.0))
    r.b.scan = lambda: np.c_[1.2 * np.cos(ring), 1.2 * np.sin(ring)]

    def turn(heading, **kw):
        sdk.pose = (sdk.pose[0], sdk.pose[1], heading)

    r.turn_velocity = turn
    r.rotate_to = lambda heading, tol_deg=None: turn(heading)
    r.watcher = lambda: _ClearWatcher()
    r.step_across(rl.Step(do="door", door="D11", heading_deg=180, label="D11 out"), math.pi, +0.10)
    drive = [e for e in r.logged if e.get("kind") == "drive"][-1]
    assert drive["stopped"] is None and drive["held_short"] == pytest.approx(0.015, abs=0.006)


class _Creeps(_VelocitySdk):
    """A base that answers the creep at 4 mm/s (a glossy floor, a late start)."""
    def call(self, command, args=None, timeout=None):
        if command == "setvelocity":
            args = dict(args, vx=min(args["vx"], 0.004))
        return super().call(command, args, timeout)


def test_a_short_step_still_creeping_is_not_counted_as_stepped(monkeypatch):
    # reviewer: "not moving" was "no 1 cm of progress for 1.5 s" -- a 3 cm step
    # creeping at a few mm/s was counted done 1.4 cm short, still moving
    dr = _drive_route()
    start = (3.90, -0.72, math.pi)
    r = _lane_runner(dr, start, monkeypatch)
    # after the runner: it puts its own no-op sleep in, which froze this clock
    # and let the test pass whichever rule was in
    clock = {"t": 1000.0}
    monkeypatch.setattr(dr.time, "time", lambda: clock["t"])
    monkeypatch.setattr(dr.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + max(s, 0.01)))
    r.sdk = sdk = _Creeps(start)
    r.b.pose = lambda: tuple(sdk.pose)
    ring = np.radians(np.arange(0.0, 360.0, 1.0))
    r.b.scan = lambda: np.c_[1.2 * np.cos(ring), 1.2 * np.sin(ring)]

    def turn(heading, **kw):
        sdk.pose = (sdk.pose[0], sdk.pose[1], heading)

    r.turn_velocity = turn
    r.rotate_to = lambda heading, tol_deg=None: turn(heading)
    r.watcher = lambda: _ClearWatcher()
    r.step_across(rl.Step(do="door", door="D11", heading_deg=180, label="D11 out"), math.pi, +0.03)
    drive = [e for e in r.logged if e.get("kind") == "drive"][-1]
    assert drive["seconds"] > 4.0                              # it did creep: 2 cm at 4 mm/s
    assert drive["stopped"] is None and drive["held_short"] is None
    assert sdk.pose[1] == pytest.approx(-0.72 + 0.03, abs=0.011)


def test_a_short_step_held_for_real_is_not_counted_as_stepped(monkeypatch):
    # Inside the doorway's stop distance a step does wait -- and if nothing
    # moves, the door must not go on as though the robot had stepped.
    dr = _drive_route()
    monkeypatch.setattr(dr, "LANE_HOLD_S", 0.3)
    start = (3.90, -0.57, math.pi)                          # the leaf 0.38 m off once turned
    r = _lane_runner(dr, start, monkeypatch)
    r.sdk = sdk = _VelocitySdk(start)
    r.b.pose = lambda: tuple(sdk.pose)
    ring = np.radians(np.arange(0.0, 360.0, 1.0))
    r.b.scan = lambda: np.c_[1.2 * np.cos(ring), 1.2 * np.sin(ring)]

    def turn(heading, **kw):
        sdk.pose = (sdk.pose[0], sdk.pose[1], heading)

    r.turn_velocity = turn
    r.rotate_to = lambda heading, tol_deg=None: turn(heading)
    r.watcher = lambda: _LeafAheadWatcher(sdk)
    with pytest.raises(dr.Stop, match="held"):
        r.step_across(rl.Step(do="door", door="D11", heading_deg=180, label="D11 out"), math.pi, +0.036)
    assert not [e for e in r.logged if e.get("kind") == "door_step_across"]


def _points_at(bearing_deg, r, spread_deg=6.0):
    a = np.radians(np.linspace(bearing_deg - spread_deg / 2, bearing_deg + spread_deg / 2, 13))
    return np.c_[r * np.cos(a), r * np.sin(a)]


def test_a_small_turn_away_from_something_beside_is_allowed():
    # 6 ต.ค. 18:01, the dock's landing spot: something 0.47 m ahead-left, a 13°
    # turn to the RIGHT still to make. The circle refused it; the rectangle
    # swept that way only moves its corners away.
    pts = _points_at(69.5, 0.47)
    assert rl.turn_sweep_gap(pts, math.radians(-13)) >= 0.04
    assert rl.turn_sweep_gap(pts, math.radians(+13)) < 0.04         # the other way swings a corner at it


def test_turning_round_beside_d8s_leaf_is_still_refused():
    # 6 ต.ค. 17:45: the open leaf 0.488 m to the left; a half turn sweeps the
    # corners (0.49 m with the arms) through it.
    assert rl.turn_sweep_gap(_points_at(90, 0.488, 40), math.radians(180)) < 0.0


def test_turning_round_with_a_wall_0_533_m_beside_is_allowed():
    # 6 ต.ค. 17:55, (-2.41, 1.17): ฝ่ายอาคาร's east wall 0.533 m to the left; the
    # circle wanted 0.54, the robot then turned there without touching.
    wall = np.c_[np.linspace(-0.6, 0.6, 61), np.full(61, 0.533)]
    assert rl.turn_sweep_gap(wall, math.radians(180)) >= 0.04 - 1e-9


def test_someone_close_in_front_still_stops_a_quarter_turn():
    # 6 ต.ค. morning: a person 0.44 m in front during a test
    assert rl.turn_sweep_gap(_points_at(0, 0.44, 20), math.radians(90)) < 0.04


def test_the_runner_turns_at_the_landing_spot_the_way_that_is_clear(monkeypatch):
    # The real case end to end: 13° right allowed at once, 13° left waited out
    # and refused — with the same thing 0.47 m ahead-left.
    dr = _drive_route()
    board = _DockBoard()
    far = np.c_[np.full(240, 2.0), np.linspace(-1.5, 1.5, 240)]              # a wall 2 m ahead
    board.scan = lambda: np.vstack([far, _points_at(69.5, 0.47)])
    r = _dock_runner(dr, board, monkeypatch)
    monkeypatch.setattr(dr.time, "time", iter(range(0, 1000)).__next__)
    r.turn_space_clear(math.radians(-13))
    with pytest.raises(dr.Stop, match="corners"):
        r.turn_space_clear(math.radians(13))



def test_with_the_arms_in_a_turn_counts_the_base_not_the_arms(monkeypatch):
    # 7 ต.ค. 10:33, top of the lane above D8, arms moved in by hand: something
    # 0.52 m to the right; a 94° left turn swept with the arms out came 2.8 cm
    # from it (refused), with the base's 0.70 m 5.9 cm (fine).
    dr = _drive_route()
    board = _DockBoard()
    far = np.c_[np.full(240, 2.0), np.linspace(-1.5, 1.5, 240)]
    board.scan = lambda: np.vstack([far, _points_at(-85, 0.52, 30)])
    r = _dock_runner(dr, board, monkeypatch)
    monkeypatch.setattr(dr.time, "time", iter(range(0, 1000)).__next__)
    with pytest.raises(dr.Stop):
        r.turn_space_clear(math.radians(94))
    r.turn_width = rl.ROBOT_BASE_WIDTH
    r.turn_space_clear(math.radians(94))


class _BackSim:
    """The dock approach as a fake board + SDK: each setvelocity is 0.1 s of motion
    on the base as measured (turns under 0.07 rad/s do nothing, 1.4x above)."""
    HOME = (-4.544, 1.4522, -0.089)

    def __init__(self, lat=0.0, yaw_off_deg=0.0, contact=True):
        hx, hy, hyaw = self.HOME
        ux, uy = math.cos(hyaw), math.sin(hyaw)
        d = 0.25 + 0.45                                         # the pre-dock spot
        self.p = [hx + d * ux - lat * uy, hy + d * uy + lat * ux, hyaw + math.radians(yaw_off_deg)]
        self.contact, self.started = contact, []
        self.params = {"base.max_angular_speed": 1.0}

    def out(self):
        hx, hy, hyaw = self.HOME
        return (self.p[0] - hx) * math.cos(hyaw) + (self.p[1] - hy) * math.sin(hyaw) - 0.25

    # board
    def home_pose(self): return self.HOME
    def power(self):
        return {"dockingStatus": "on_dock" if self.contact and self.out() <= 0.0 else "not_on_dock",
                "isCharging": self.contact and self.out() <= 0.0}
    def events(self): return []
    def pose(self): return tuple(self.p)
    def start(self, name, options):
        self.started.append(name)
        self.contact = True
        return 1
    def current(self): return None
    def state(self, aid): return {}
    def param(self, name): return self.params[name]
    def set_param(self, name, value): self.params[name] = value

    # SDK
    def call(self, command, args=None, timeout=None):
        if command == "velocitycontrol":
            return {"code": 1, "id": 3}
        if command == "getpose":
            return {"x": self.p[0], "y": self.p[1], "yaw": self.p[2]}
        if command == "getcurrentaction":
            return {"id": 0}
        if command == "setvelocity":
            w = args["omega"]
            w = 0.0 if abs(w) < 0.07 else 1.4 * w
            self.p[2] += w * 0.1
            self.p[0] += args["vx"] * math.cos(self.p[2]) * 0.1
            self.p[1] += args["vx"] * math.sin(self.p[2]) * 0.1
        return {"code": 0, "codestr": "done"}


def _back_runner(dr, sim, monkeypatch):
    monkeypatch.setattr(dr.time, "sleep", lambda s: None)
    r = dr.Runner.__new__(dr.Runner)
    r.b, r.sdk = sim, sim
    r.route = rl.Route(name="t", anchors={}, doors={}, steps=[], dock_pose=(-4.295, 1.43, -0.089))
    r.say = lambda m: None
    r.logged = []
    r.log = lambda **e: r.logged.append(e)
    r.turn_width = rl.ROBOT_WIDTH
    r.drive_velocity = lambda step, direct=False: None          # already at the pre-dock spot
    r.turn_velocity = lambda heading, **kw: 0.0                 # already turned round
    return r


def test_it_backs_straight_onto_the_charger_without_gohome(monkeypatch):
    # owner, 7 ต.ค.: "เดินหน้าเข้ามาใกล้ๆ แล้วค่อยหมุนตัวถอยง่ายกว่า"
    dr = _drive_route()
    sim = _BackSim(lat=0.015, yaw_off_deg=1.0)
    r = _back_runner(dr, sim, monkeypatch)
    r.dock(rl.Step(do="dock", label="dock"))
    assert sim.started == []                                     # GoHome never called
    back = [e for e in r.logged if e.get("kind") == "back_into_dock"][-1]
    assert back["docked"] and back["reason"] is None
    hx, hy, hyaw = sim.HOME
    lat = -(sim.p[0] - hx) * math.sin(hyaw) + (sim.p[1] - hy) * math.cos(hyaw)
    assert abs(lat) < 0.015                                     # leaned back toward the line, not away
    assert -0.02 <= sim.out() <= 0.0                              # stopped on contact, not pushing on


def test_no_contact_backing_in_tries_once_more_then_stops_without_gohome(monkeypatch):
    # 7 ต.ค. 14:47: the fallback GoHome spun 254° and left the robot on the
    # contacts at ~63° -- the owner docked it by hand. Two goes, then stand still.
    dr = _drive_route()
    sim = _BackSim(contact=False)
    r = _back_runner(dr, sim, monkeypatch)
    with pytest.raises(dr.Stop, match="not on the dock after 2 tries"):
        r.dock(rl.Step(do="dock", label="dock"))
    backs = [e for e in r.logged if e.get("kind") == "back_into_dock"]
    assert len(backs) == 2 and not any(b["docked"] for b in backs)
    assert all("past where the dock" in b["reason"] for b in backs)
    assert sim.out() >= -dr.BACK_OVERSHOOT_M - 0.01              # did not keep reversing
    assert sim.started == []                                      # no GoHome


class _SlipSim(_BackSim):
    """_BackSim with odometry apart from the robot: the tail meets something
    `block_out` m out from where it sits docked and goes no further back. The
    wheels then turn on (polished floor: odometry -- what the board reports --
    runs on as commanded, `scale` x the true motion) or, `stall`, stop. `drop_at`:
    the link drops (OSError) on the setvelocity that many calls in."""

    def __init__(self, block_out=-9.0, scale=1.0, stall=False, drop_at=None, yaw_drift=0.0, **kw):
        super().__init__(**kw)
        self.block_out, self.scale, self.stall, self.drop_at = block_out, scale, stall, drop_at
        self.yaw_drift = yaw_drift                          # rad odometry's heading wanders per setvelocity
        self.odo = list(self.p)
        self.sets = 0

    def pose(self): return tuple(self.odo)

    def call(self, command, args=None, timeout=None):
        if command == "getpose":
            return {"x": self.odo[0], "y": self.odo[1], "yaw": self.odo[2]}
        if command != "setvelocity":
            return super().call(command, args, timeout)
        self.sets += 1
        if self.drop_at is not None and self.sets >= self.drop_at:
            raise OSError("link dropped")
        before, odo_before = list(self.p), list(self.odo)
        r = super().call(command, args, timeout)
        self.odo[2] += self.p[2] - before[2] + self.yaw_drift
        self.odo[0] += self.scale * (self.p[0] - before[0])
        self.odo[1] += self.scale * (self.p[1] - before[1])
        if self.out() < self.block_out:
            self.p[0], self.p[1] = before[0], before[1]
            if self.stall:
                self.odo[0], self.odo[1] = odo_before[0], odo_before[1]
        return r

    def odo_out(self):
        hx, hy, hyaw = self.HOME
        return (self.odo[0] - hx) * math.cos(hyaw) + (self.odo[1] - hy) * math.sin(hyaw) - 0.25


class _WallWatcher:
    """The lidar while backing in: ฝ่ายอาคาร's east wall seen from where the robot
    really is, stamped with the pose the board reports when the frame was taken,
    a new frame every `every` s of the run's clock (0.19 s median on the robot,
    8 ต.ค.) -- none between `pause` (t0, t1). `ahead`: what front() says."""

    def __init__(self, sim, clock, ahead=None, every=0.2, pause=None):
        self.sim, self.clock, self.ahead, self.every, self.pause = sim, clock, ahead, every, pause
        self.last, self.cached = -9.0, (np.zeros((0, 2)), None)

    def start(self): pass
    def stop(self): pass
    def front(self): return self.ahead

    def frame(self):
        now = self.clock[0]
        paused = self.pause is not None and self.pause[0] <= now < self.pause[1]
        if now - self.last >= self.every and not paused:
            ys = np.linspace(-0.6, 1.68, 600)
            x, y, yaw = self.sim.p
            rel = np.c_[np.full(ys.size, -1.825) - x, ys - y]
            c, s = math.cos(yaw), math.sin(yaw)
            self.cached = (np.c_[rel[:, 0] * c + rel[:, 1] * s, -rel[:, 0] * s + rel[:, 1] * c], tuple(self.sim.odo))
            self.last = now
        return self.cached


def _watched_back_runner(dr, sim, monkeypatch, dock_gap=None, **watch):
    r = _back_runner(dr, sim, monkeypatch)
    clock = [1000.0]

    def sleep(sec):
        clock[0] += max(sec, 0.0)
    monkeypatch.setattr(dr.time, "sleep", sleep)
    monkeypatch.setattr(dr.time, "time", lambda: clock[0])
    r.route = rl.Route(name="t", anchors={"ea_east": rl.Anchor("x", -1.825, -0.6, 1.68)}, doors={}, steps=[],
                       dock_pose=(-4.295, 1.43, -0.089), dock_check=("ea_east",), dock_gap=dock_gap)
    r._dock_check = lambda hyaw: None                            # heading measured, as at the pre-dock spot
    r.watcher = lambda: _WallWatcher(sim, clock, **watch)
    r.clock = clock
    return r


def _back_record(r):
    backs = [e for e in r.logged if e.get("kind") == "back_into_dock"]
    assert len(backs) == 1
    return backs[0]


def test_backing_into_something_beside_the_charger_stops_at_once_eases_off_and_does_not_go_again(monkeypatch):
    # 8 ต.ค. 16:10 room test: 4 cm off the dock's line the tail met the column
    # beside the charger ~3 cm before the contacts; the wheels slipped on, odometry
    # counted on to the overshoot limit (6.4 cm past the column), and the robot
    # leant on it -- twice (owner: "ดันเมื่อกี้ 2 รอบหุ่นโดนกำแพงเกือบล้ม").
    dr = _drive_route()
    sim = _SlipSim(block_out=0.03, contact=False)
    r = _watched_back_runner(dr, sim, monkeypatch)
    with pytest.raises(dr.Stop, match="blocked backing in"):
        r.dock(rl.Step(do="dock", label="dock"))
    back = _back_record(r)
    assert back["blocked"] and "less by the wall ahead" in back["reason"]
    # pushed on by no more than it takes to be sure, frames 0.2 s apart (was 6.4 cm)
    assert sim.out() - sim.odo_out() <= 0.03
    # then straight forward off it, the way it came
    assert back["eased"] == pytest.approx(dr.BACK_EASE_M, abs=0.01) and back["ease_why"] is None
    assert sim.out() >= 0.03 + dr.BACK_EASE_M - 0.01
    assert sim.started == []                                      # no GoHome


def test_backing_into_something_the_wheels_cannot_turn_against_stops_too(monkeypatch):
    # four back-ins on 7-8 ต.ค. ended at the overshoot limit with odometry under
    # 0.005 m/s for 0.4 s first: the wheels stopped, so the wall ahead saw no slip
    dr = _drive_route()
    sim = _SlipSim(block_out=0.03, stall=True, contact=False)
    r = _watched_back_runner(dr, sim, monkeypatch)
    with pytest.raises(dr.Stop, match="blocked backing in"):
        r.dock(rl.Step(do="dock", label="dock"))
    back = _back_record(r)
    assert back["blocked"] and "wheels stopped" in back["reason"]
    end = back["trace"][-1]
    still = [q for q in back["trace"] if abs(q[1] - end[1]) < 1e-4 and abs(q[2] - end[2]) < 1e-4]
    assert len(still) <= 7                                        # stopped within ~0.6 s of the wheels stopping
    assert sim.out() >= 0.03 + dr.BACK_EASE_M - 0.01              # eased off


def test_blocked_with_someone_close_in_front_it_stops_without_easing(monkeypatch):
    dr = _drive_route()
    sim = _SlipSim(block_out=0.03, contact=False)
    r = _watched_back_runner(dr, sim, monkeypatch, ahead=0.40)
    with pytest.raises(dr.Stop, match="blocked backing in"):
        r.dock(rl.Step(do="dock", label="dock"))
    back = _back_record(r)
    assert back["eased"] == pytest.approx(0.0, abs=1e-6) and "0.40 m ahead" in back["ease_why"]


def test_after_a_block_the_dock_step_again_does_not_back_in_again(monkeypatch):
    # reviewer: a link drop after the block made run_step do the dock step again,
    # which backed in again from where the robot stood -- into the same column
    dr = _drive_route()
    sim = _SlipSim(block_out=0.03, contact=False)
    r = _watched_back_runner(dr, sim, monkeypatch)
    with pytest.raises(dr.Stop, match="blocked backing in"):
        r.dock(rl.Step(do="dock", label="dock"))
    with pytest.raises(dr.Stop, match="earlier this run"):
        r.dock(rl.Step(do="dock", label="dock"))
    assert len([e for e in r.logged if e.get("kind") == "back_into_dock"]) == 1


def test_a_link_drop_while_backing_in_is_a_stop_not_a_step_to_do_again(monkeypatch):
    # run_step does a step again on OSError / SdkError / LinkLost: started docking
    # must not come back to it from wherever the robot stands
    dr = _drive_route()
    sim = _SlipSim(drop_at=20)
    r = _watched_back_runner(dr, sim, monkeypatch)
    with pytest.raises(dr.Stop, match="own docking failed part way"):
        r.dock(rl.Step(do="dock", label="dock"))
    assert sim.started == []


def test_watching_the_wall_does_not_stop_a_backing_in_that_reaches_the_contacts(monkeypatch):
    # odometry 1.5% long over the 0.45 m, 1 cm aside, 1° off: on the dock, no block
    dr = _drive_route()
    sim = _SlipSim(scale=1.015, lat=0.01, yaw_off_deg=1.0)
    r = _watched_back_runner(dr, sim, monkeypatch)
    r.dock(rl.Step(do="dock", label="dock"))
    back = _back_record(r)
    assert back["docked"] and not back["blocked"] and back["slip_max"] < dr.BACK_SLIP_M
    assert back["slips"]                                           # the watch was on, frame by frame


def test_no_fresh_frame_over_the_last_12_cm_holds_the_robot_still(monkeypatch):
    # frames came 1.6 s apart at worst that day: backing on without them is
    # backing on with the watch off
    dr = _drive_route()
    sim = _SlipSim()
    r = _watched_back_runner(dr, sim, monkeypatch, pause=(1000.0 + 7.0, 1000.0 + 9.0))
    r.dock(rl.Step(do="dock", label="dock"))
    back = _back_record(r)
    assert back["docked"] and not back["blocked"]
    held = [q for q in back["trace"] if q[4] < 0.12 and q[6] == 0.0 and 7.7 <= q[0] <= 8.9]
    assert held                                                    # stood still waiting for the wall


@pytest.mark.parametrize("drift_deg", [0.012, -0.012])
def test_odometry_s_heading_wandering_while_backing_does_not_read_as_a_block(monkeypatch, drift_deg):
    # 8 ต.ค. 17:32, second back-in: the slip rose slowly 0.4 -> 1.2 cm from 9 cm
    # out and stopped it -- the median of the wall's returns sat ~0.9 m along the
    # wall from the robot, so a heading drifting under odometry moved it. 1° over
    # the back-in here: the line read where it crosses the robot does not.
    dr = _drive_route()
    sim = _SlipSim(yaw_drift=math.radians(drift_deg))
    r = _watched_back_runner(dr, sim, monkeypatch)
    r.dock(rl.Step(do="dock", label="dock"))
    back = _back_record(r)
    assert back["docked"] and not back["blocked"] and back["slip_max"] < 0.004
    assert abs(math.degrees(sim.odo[2] - sim.p[2])) > 0.8           # it did wander


# What the lidar sees facing the dock (8 ต.ค. 17:31, from a dock the robot backed onto
# itself): the bare wall, face A, the gap (the wall again, 0.5 cm further back), face B.
DOCK_SURROUNDS = [(-4.767, 0.65, -4.767, 1.125), (-4.697, 1.153, -4.697, 1.429), (-4.772, 1.429, -4.772, 1.529),
                  (-4.658, 1.529, -4.658, 1.758), (-4.767, 1.825, -4.767, 2.30)]


DOCK_GAP_CFG = rl.DockGap(wall_x=-4.767, wall_spans=((0.70, 1.10), (1.85, 2.30)), depth_lo=0.070, depth_hi=0.109,
                          outer_offset=0.0225, centre_y=1.477)


class _GapSim(_SlipSim):
    """_SlipSim that starts at the end of the west lane facing the dock, `aside`
    m south of where odometry has it, and shows DOCK_SURROUNDS to raw_scan from
    where it really is. Lanes and turns move it as odometry asks (no slip)."""

    def __init__(self, aside, start_x=-3.0, extra=(), takes_pose=True, **kw):
        super().__init__(**kw)
        self.odo = [start_x, 1.43, math.pi]
        self.p = [start_x, 1.43 - aside, math.pi]
        self.extra, self.takes_pose = list(extra), takes_pose

    def raw_scan(self):
        pts = []
        for x1, y1, x2, y2 in DOCK_SURROUNDS + self.extra:
            n = int(max(abs(x2 - x1), abs(y2 - y1)) / 0.004)
            pts.append(np.c_[np.linspace(x1, x2, n), np.linspace(y1, y2, n)])
        rel = np.vstack(pts) - self.p[:2]
        c, s = math.cos(self.p[2]), math.sin(self.p[2])
        loc = np.c_[rel[:, 0] * c + rel[:, 1] * s, -rel[:, 0] * s + rel[:, 1] * c]
        ang = np.arctan2(loc[:, 1], loc[:, 0])
        loc = loc[np.abs(ang) <= math.radians(103)]
        return {"laser_points": [{"angle": float(math.atan2(q[1], q[0])), "distance": float(math.hypot(*q)),
                                  "valid": True} for q in loc]}

    def set_pose(self, pose):
        if self.takes_pose:
            self.odo = list(pose)

    def lat_to_gap_line(self):
        """+ = north of the line through the gap's centre (1.479, where the faces stand) at the dock's heading."""
        hyaw = self.HOME[2]
        return -(self.p[0] + 4.6775) * math.sin(hyaw) + (self.p[1] - 1.479) * math.cos(hyaw)


def _gap_runner(dr, sim, monkeypatch):
    """A runner on `sim` whose lanes, turns and backing off move the robot as
    odometry asks (both poses by the same), with the routes' dock_gap."""
    r = _watched_back_runner(dr, sim, monkeypatch, dock_gap=DOCK_GAP_CFG)
    r.moves = []

    def drive(step, direct=False):
        dx, dy = step.to[0] - sim.odo[0], step.to[1] - sim.odo[1]
        h = math.atan2(dy, dx)
        sim.odo = [step.to[0], step.to[1], h]
        sim.p = [sim.p[0] + dx, sim.p[1] + dy, h]
        r.moves.append(("drive", step.label))

    def turn(heading, **kw):
        sim.p[2], sim.odo[2] = heading, heading
        return 0.0

    def back_off(dist, most=None, turn=None):
        for q in (sim.p, sim.odo):
            q[0] -= dist * math.cos(q[2])
            q[1] -= dist * math.sin(q[2])
        r.moves.append(("back", round(dist, 3)))
    r.drive_velocity, r.turn_velocity, r.back_off = drive, turn, back_off
    return r


def test_facing_the_dock_the_gap_puts_the_robot_on_the_charger_s_centre_line(monkeypatch):
    # owner, 8 ต.ค.: "มันไม่ตรงกลางแท่น มันเยื้องมาทางซ้าย" -- and at 16:10 4 cm aside the
    # tail met the column. 3 cm south of where odometry has it at the end of the west
    # lane: the look at the gap puts that right before the approach.
    dr = _drive_route()
    sim = _GapSim(aside=0.03)
    r = _gap_runner(dr, sim, monkeypatch)
    r.dock(rl.Step(do="dock", label="dock"))
    looks = [e for e in r.logged if e.get("kind") == "dock_gap"]
    assert [e["where"] for e in looks] == ["approach", "pre-dock"] and all(e["fix"] for e in looks)
    assert looks[0]["fix"]["dy"] == pytest.approx(-0.03 - 0.0155, abs=0.003)   # 3 cm + the home line's 1.5 cm
    assert abs(looks[1]["fix"]["dy"]) < 0.003                                 # nothing much left at the pre-dock spot
    assert _back_record(r)["docked"]
    assert abs(sim.lat_to_gap_line()) < 0.005
    assert ("back", dr.DOCK_GAP_BACK_M) not in r.moves                  # no second approach needed


def test_too_far_off_at_the_pre_dock_spot_it_backs_off_and_comes_again(monkeypatch):
    # reviewer: backing in leans at most 2° -- ~1 cm over 0.45 m. The approach look
    # missed (2.6 m off the wall), 3 cm aside: at the pre-dock spot, straight back
    # 0.40 m the way it came, the approach again, and on the line this time.
    dr = _drive_route()
    sim = _GapSim(aside=0.03, start_x=-2.2)
    r = _gap_runner(dr, sim, monkeypatch)
    r.dock(rl.Step(do="dock", label="dock"))
    looks = [e for e in r.logged if e.get("kind") == "dock_gap"]
    assert [e["where"] for e in looks] == ["approach", "pre-dock", "pre-dock, again"]
    assert "skipped" in looks[0] and abs(looks[1]["fix"]["dy"]) > dr.DOCK_GAP_REDO_M
    assert ("back", dr.DOCK_GAP_BACK_M) in r.moves
    assert _back_record(r)["docked"] and abs(sim.lat_to_gap_line()) < 0.005


def test_still_off_after_a_second_approach_it_does_not_back_in(monkeypatch):
    dr = _drive_route()
    sim = _GapSim(aside=0.03, start_x=-2.2, takes_pose=False)        # the fix never reaches the pose
    r = _gap_runner(dr, sim, monkeypatch)
    with pytest.raises(dr.Stop, match="after a second approach"):
        r.dock(rl.Step(do="dock", label="dock"))
    assert not [e for e in r.logged if e.get("kind") == "back_into_dock"]


def test_too_far_off_with_no_ground_it_just_crossed_it_does_not_back_in(monkeypatch):
    # backing off is blind: only over ground the robot has just driven
    dr = _drive_route()
    hx, hy, hyaw = _BackSim.HOME
    sim = _GapSim(aside=0.03, start_x=hx + 0.70 * math.cos(hyaw), takes_pose=False)
    sim.odo[1] = sim.p[1] = hy + 0.70 * math.sin(hyaw)
    sim.p[1] -= 0.03
    r = _gap_runner(dr, sim, monkeypatch)
    with pytest.raises(dr.Stop, match="no ground it just crossed"):
        r.dock(rl.Step(do="dock", label="dock"))
    assert not [m for m in r.moves if m[0] == "back"]


def test_the_gap_plainly_seen_too_far_off_is_a_stop_not_odometry(monkeypatch):
    # reviewer: "no gap" and "the gap, but further off than odometry should ever be"
    # are not the same; on the second, backing in on odometry is backing in that far off
    dr = _drive_route()
    sim = _GapSim(aside=0.09)
    r = _gap_runner(dr, sim, monkeypatch)
    with pytest.raises(dr.Stop, match="not something to back in on"):
        r.dock(rl.Step(do="dock", label="dock"))
    looks = [e for e in r.logged if e.get("kind") == "dock_gap"]
    assert looks[-1]["where"] == "pre-dock" and looks[-1]["why"].startswith("far:")
    assert not [e for e in r.logged if e.get("kind") == "back_into_dock"]


def test_a_second_approach_that_cannot_see_the_gap_does_not_back_in(monkeypatch):
    # reviewer: measured off by more than backing in can lean out, then no look to
    # say the second approach put it right
    dr = _drive_route()
    sim = _GapSim(aside=0.03, start_x=-2.2)
    r = _gap_runner(dr, sim, monkeypatch)
    real = r._dock_gap
    r._dock_gap = lambda *a: None if a[-1] == "pre-dock, again" else real(*a)
    with pytest.raises(dr.Stop, match="could not see the gap"):
        r.dock(rl.Step(do="dock", label="dock"))
    assert not [e for e in r.logged if e.get("kind") == "back_into_dock"]


def test_a_robot_that_never_stands_still_gets_no_look_and_does_not_hang(monkeypatch):
    # reviewer: the "still moving" branch went round again before the time check
    dr = _drive_route()
    sim = _GapSim(aside=0.0)
    r = _gap_runner(dr, sim, monkeypatch)
    calls = [0]

    def pose():
        calls[0] += 1
        assert calls[0] < 2000, "looping"
        return (sim.odo[0] + 0.005 * (calls[0] % 2), sim.odo[1], sim.odo[2])
    sim.pose = pose
    assert r._dock_gap(*sim.HOME, "approach") is None
    look = [e for e in r.logged if e.get("kind") == "dock_gap"][-1]
    assert look["why"] == "still moving"


def test_something_between_the_robot_and_the_charger_stops_it_backing_in(monkeypatch):
    # reviewer: whatever hides the faces from the pre-dock spot stands where the
    # robot is about to back into
    dr = _drive_route()
    sim = _GapSim(aside=0.0, extra=[(-4.45, 1.40, -4.45, 1.52)])       # a box 15 cm wide on the line
    r = _gap_runner(dr, sim, monkeypatch)
    with pytest.raises(dr.Stop, match="between the robot and the charger"):
        r.dock(rl.Step(do="dock", label="dock"))
    assert not [e for e in r.logged if e.get("kind") == "back_into_dock"]
    assert r.clock[0] - 1000.0 >= dr.DOCK_PATH_WAIT_S                    # waited for it to go first


def test_the_gap_is_looked_for_only_facing_the_dock_and_near_it(monkeypatch):
    dr = _drive_route()
    gap = DOCK_GAP_CFG
    for odo in ([-3.85, 1.39, 0.0], [-1.6, 1.43, math.pi]):          # facing out; 3.2 m from the wall
        sim = _GapSim(aside=0.0)
        sim.odo, sim.p = list(odo), list(odo)
        r = _watched_back_runner(dr, sim, monkeypatch, dock_gap=gap)
        assert r._dock_gap(*sim.HOME, "approach") is None
        look = [e for e in r.logged if e.get("kind") == "dock_gap"][-1]
        assert look["fix"] is None and "skipped" in look
        assert sim.odo == list(odo)                                      # pose untouched


@pytest.mark.parametrize("extra,found", [((), True), ([(-4.71, 1.47, -4.71, 1.49)], False)])
def test_a_look_at_the_dock_keeps_what_it_saw_so_it_can_be_replayed(monkeypatch, extra, found):
    # 9 ต.ค.: both looks said "no gap" and kept nothing -- whether the charger had
    # moved, something stood at it, or the code was wrong could not be told. The
    # look's points, read again from its pose, give the same answer it gave.
    dr = _drive_route()
    sim = _GapSim(aside=0.0, extra=list(extra))               # a cable across the gap, or not
    r = _gap_runner(dr, sim, monkeypatch)
    assert (r._dock_gap(*sim.HOME, "approach") is not None) == found
    look = [e for e in r.logged if e.get("kind") == "dock_gap"][-1]
    pts = np.array(look["points"])
    assert len(pts) > 200 and (pts[:, 0] > 0).all()
    hx, hy, hyaw = sim.HOME
    why = []
    again = rl.dock_gap_fix(pts, tuple(look["pose"]), DOCK_GAP_CFG, look["line_y"], why=why)
    if found:
        assert again["dy"] == pytest.approx(look["fix"]["dy"], abs=0.001)
    else:
        assert again is None and why[-1] == look["why"] and look["why"].startswith("no gap (")


EA_EAST_SEG = (-1.825, -0.6, -1.825, 1.68)


class _HalfTurnSim(_GapSim):
    """The pre-dock spot: the dock's surroundings one way, ea_east the other. A half
    turn ends where odometry asked, and really `err[sense]` rad further round."""

    def __init__(self, err_cw_deg, err_ccw_deg):
        super().__init__(aside=0.0, extra=[EA_EAST_SEG])
        self.err = {-1: math.radians(err_cw_deg), 1: math.radians(err_ccw_deg)}
        self.turns = []

    def turn(self, heading, **kw):
        asked = rl.wrap_pi(heading - self.odo[2])
        sense = 1 if asked > 0 else -1
        self.turns.append((round(math.degrees(asked), 1), kw.get("board_idle")))
        self.p[2] += asked + (self.err[sense] if abs(asked) > math.radians(90) else 0.0)
        self.odo[2] = heading
        return 0.0


def test_the_dock_turn_test_turns_each_way_and_reads_each_half_turn_s_error(monkeypatch):
    # calibrate_from_runs, 9 ต.ค.: one CW-then-CCW pair at the dock came back +6.05°,
    # one CCW pair +0.64° -- n = 1 each, nothing to change on. The supervised test:
    # half turns one way and the other at the pre-dock spot, a look after each.
    dr = _drive_route()
    sim = _HalfTurnSim(err_cw_deg=-2.0, err_ccw_deg=1.0)
    hx, hy, hyaw = sim.HOME
    pre = (hx + 0.70 * math.cos(hyaw), hy + 0.70 * math.sin(hyaw))
    sim.odo, sim.p = [pre[0], pre[1], hyaw], [pre[0], pre[1], hyaw]
    r = _watched_back_runner(dr, sim, monkeypatch, dock_gap=DOCK_GAP_CFG)
    r.route.steps = [rl.Step(do="checkpoint", use=("ea_east",), label="on the dock")]
    done = []
    r.checkpoint = lambda step: done.append("checkpoint")
    r.undock = lambda: done.append("undock")
    r.drive_velocity = lambda step, direct=False, **kw: done.append(("drive", direct))
    r.turn_velocity = sim.turn
    r.fast_pose = lambda: tuple(sim.odo)
    r.dock = lambda step: done.append("dock")
    rows = r.dock_turn_test(2)
    assert done == ["checkpoint", "undock", ("drive", True), "dock"]
    asked = [t for t, _ in sim.turns if abs(t) > 90]
    assert asked == [-dr.DOCK_TURN_TEST_DEG, dr.DOCK_TURN_TEST_DEG] * 2      # CW, then back CCW, twice
    assert all(idle for _, idle in sim.turns)                                  # the dock's own way of turning
    assert [q["facing"] for q in rows] == ["out", "dock", "out", "dock", "out"]
    for q in rows[1:]:
        want = -2.0 if q["turn"] == "cw" else 1.0
        assert q["dyaw_deg"] == pytest.approx(want, abs=0.15), q
    summary = [e for e in r.logged if e.get("kind") == "dock_turn_summary"][0]
    assert summary["cw"]["n"] == summary["ccw"]["n"] == 2
    assert summary["cw"]["mean_deg"] == pytest.approx(-2.0, abs=0.15)
    assert summary["ccw"]["mean_deg"] == pytest.approx(1.0, abs=0.15)
    assert abs(math.degrees(rl.wrap_pi(sim.odo[2] - sim.p[2]))) < 0.15        # each look put the board right


def test_the_dock_turn_test_with_no_wall_to_read_stops_where_it_stands(monkeypatch):
    dr = _drive_route()
    sim = _HalfTurnSim(0.0, 0.0)
    sim.extra = []                                                   # ea_east not there to read facing out
    hx, hy, hyaw = sim.HOME
    sim.odo = [hx + 0.70 * math.cos(hyaw), hy + 0.70 * math.sin(hyaw), hyaw]
    sim.p = list(sim.odo)
    r = _watched_back_runner(dr, sim, monkeypatch, dock_gap=DOCK_GAP_CFG)
    r.checkpoint = r.undock = lambda *a: None
    r.drive_velocity = lambda step, direct=False, **kw: None
    r.turn_velocity, r.fast_pose = sim.turn, lambda: tuple(sim.odo)
    r.dock = lambda step: pytest.fail("docked after a look that read nothing")
    with pytest.raises(dr.Stop, match="not read facing out"):
        r.dock_turn_test(1)


def test_wall_offset_does_not_read_a_line_far_beyond_its_returns():
    # reviewer: 50 returns over 0.3 m, 1.5 m along from the robot -- a line read
    # out there is ~1 cm of noise a frame; the median of them is not
    dr = _drive_route()
    walls = {"ea_east": rl.Anchor("x", -1.825, -0.6, 1.68)}
    rng = np.random.default_rng(1)
    ys = np.linspace(-0.4, -0.1, 60)
    xs = -1.825 + rng.normal(0.0, 0.004, ys.size) + 0.03 * (ys + 0.25)          # a little tilt, noise
    robot = (-3.8, 1.39, 0.0)
    pts = np.c_[xs - robot[0], ys - robot[1]]
    dx, _ = dr.wall_offset(pts, robot, walls)
    assert dx == pytest.approx(-1.825 - float(np.median(xs)), abs=1e-9)


def test_wall_offset_reads_where_the_wall_puts_the_robot():
    dr = _drive_route()
    walls = {"ea_east": rl.Anchor("x", -1.825, -0.6, 1.68)}
    true, believed = (-3.80, 1.39, -0.09), (-3.83, 1.39, -0.09)
    ys = np.linspace(-0.6, 1.68, 300)
    rel = np.c_[np.full(ys.size, -1.825) - true[0], ys - true[1]]
    c, s = math.cos(true[2]), math.sin(true[2])
    pts = np.c_[rel[:, 0] * c + rel[:, 1] * s, -rel[:, 0] * s + rel[:, 1] * c]
    clutter = np.c_[np.full(80, 1.0), np.linspace(-0.3, 0.3, 80)]  # someone 1 m ahead: not the wall
    dx, dy = dr.wall_offset(np.vstack([pts, clutter]), believed, walls)
    assert dx == pytest.approx(0.03, abs=1e-3) and dy is None
    assert dr.wall_offset(pts[:40], believed, walls) == (None, None)
    crowd = np.c_[np.full(400, 1.0), np.linspace(-0.5, 0.5, 400)]  # a crowd and no wall: no reading
    assert dr.wall_offset(crowd, believed, walls) == (None, None)


def test_at_the_pre_dock_spot_the_heading_is_taken_from_the_east_wall(monkeypatch):
    # The half turn at the pre-dock spot came out 4° off what odometry says;
    # ฝ่ายอาคาร's east wall (x -1.825, y -0.6..1.68) is 2 m ahead to measure it.
    dr = _drive_route()
    true = (-3.85, 1.39, math.radians(-5.1 + 4.0))
    believed = (-3.85, 1.39, math.radians(-5.1))
    ys = np.linspace(-0.6, 1.68, 400)
    wall = np.c_[np.full(ys.size, -1.825), ys]
    c, s = math.cos(true[2]), math.sin(true[2])
    rel = wall - true[:2]
    local = np.c_[rel[:, 0] * c + rel[:, 1] * s, -rel[:, 0] * s + rel[:, 1] * c]
    frame = {"laser_points": [{"angle": float(math.atan2(q[1], q[0])), "distance": float(math.hypot(*q)),
                               "valid": True} for q in local]}

    class B:
        poses = []
        def pose(self): return believed
        def raw_scan(self): return frame
        def set_pose(self, p): B.poses.append(p)

    r = dr.Runner.__new__(dr.Runner)
    r.b = B()
    r.route = rl.Route(name="t", anchors={"ea_east": rl.Anchor("x", -1.825, -0.6, 1.68)}, doors={}, steps=[],
                       dock_check=("ea_east",))
    r.say, r.logged = (lambda m: None), []
    r.log = lambda **e: r.logged.append(e)
    turns = []
    r.turn_velocity = lambda h, **kw: turns.append(h)
    r._dock_check(math.radians(-5.1))
    assert B.poses and math.degrees(B.poses[-1][2]) == pytest.approx(-1.1, abs=0.3)
    assert turns == [pytest.approx(math.radians(-5.1))]           # turned back onto the dock's line


def _frame_of(walls, pose):
    """A raw lidar frame of wall segments [(x1, y1, x2, y2)] seen from `pose`."""
    pts = []
    for x1, y1, x2, y2 in walls:
        n = int(max(abs(x2 - x1), abs(y2 - y1)) / 0.01)
        pts.append(np.c_[np.linspace(x1, x2, n), np.linspace(y1, y2, n)])
    p = np.vstack(pts) - pose[:2]
    c, s = math.cos(pose[2]), math.sin(pose[2])
    local = np.c_[p[:, 0] * c + p[:, 1] * s, -p[:, 0] * s + p[:, 1] * c]
    local = local[(np.abs(np.arctan2(local[:, 1], local[:, 0])) < math.radians(100))]
    return {"laser_points": [{"angle": float(math.atan2(q[1], q[0])), "distance": float(math.hypot(*q)),
                              "valid": True} for q in local]}


@pytest.mark.parametrize("second_sees_north,reads", [(True, 2), (False, 2)])
def test_a_stop_reads_one_frame_unless_a_wall_it_should_measure_is_missing(monkeypatch, second_sees_north, reads):
    # owner 7 ต.ค.: "ทำให้เหลือ 1 นาที" -- two frames 0.3 s apart at every stop. One
    # frame matched today's 89 logged stops to 1-2 mm / 0.1°, except the junction
    # twice, where it missed mr_north and left y unmeasured (two frames: 18-19 cm).
    dr = _drive_route()
    monkeypatch.setattr(dr.time, "sleep", lambda s: None)
    pose = (2.46, -2.45, math.pi / 2)
    west, east, north = (1.715, -2.3, 1.715, -0.6), (3.325, -2.3, 3.325, -0.6), (1.0, -1.40, 2.2, -1.40)
    frames = [_frame_of([west, east], pose), _frame_of([west, east] + ([north] if second_sees_north else []), pose)]
    calls = []

    class B:
        def pose(self): return pose
        def raw_scan(self):
            calls.append(1)
            return frames[min(len(calls) - 1, 1)]
        def set_pose(self, p): pass
        def power(self): return {"dockingStatus": "not_on_dock"}

    r = dr.Runner.__new__(dr.Runner)
    r.b = B()
    r.route = rl.Route(name="t", anchors={"mr_west": rl.Anchor("x", 1.715, -2.5, 1.5),
                                          "mr_east": rl.Anchor("x", 3.325, -2.5, 1.5),
                                          "mr_north": rl.Anchor("y", -1.40, 0.5, 2.5)}, doors={}, steps=[])
    r.say, r.logged = (lambda m: None), []
    r.log = lambda **e: r.logged.append(e)
    r.rotate_to = lambda h, **k: 0.0
    fix = r.checkpoint(rl.Step(do="checkpoint", use=("mr_west", "mr_east", "mr_north"), label="junction"))
    assert len(calls) == reads
    assert (fix.dy is not None) == second_sees_north
    # and a stop whose walls one frame covers reads only one
    calls.clear()
    r.checkpoint(rl.Step(do="checkpoint", use=("mr_west", "mr_east"), label="corridor between meeting rooms"))
    assert len(calls) == 1


def _dock_check_runner(dr, frame, believed):
    class B:
        poses = []
        def pose(self): return believed
        def raw_scan(self): return frame
        def set_pose(self, p): B.poses.append(p)

    r = dr.Runner.__new__(dr.Runner)
    r.b = B()
    r.route = rl.Route(name="t", anchors={"ea_east": rl.Anchor("x", -1.825, -0.6, 1.68)}, doors={}, steps=[],
                       dock_check=("ea_east",))
    r.say, r.logged = (lambda m: None), []
    r.log = lambda **e: r.logged.append(e)
    r.turns = []
    r.turn_velocity = lambda h, **kw: r.turns.append(h)
    return r, B.poses


def test_the_dock_check_finds_the_wall_with_odometry_15_degrees_out(monkeypatch):
    # 7 ต.ค. 16:08: "no named wall in view" at the pre-dock spot, backed in on
    # odometry, met the charger crooked. The scan from the charger afterwards
    # (the robot on it at -11.9°): with the heading guessed 15° off the
    # matcher finds nothing -- searched over guesses it finds the wall.
    import json
    from pathlib import Path
    dr = _drive_route()
    doc = json.loads((Path(__file__).parent / "data" / "robot" / "scan_on_dock_crooked_20261007.json").read_text())
    believed = (-4.295, 1.43, math.radians(-11.9 + 15.0))
    assert not rl.correct_pose(believed, rl.scan_points(doc["frame"]),
                               {"ea_east": rl.Anchor("x", -1.825, -0.6, 1.68)}, {}).accepted
    r, poses = _dock_check_runner(dr, doc["frame"], believed)
    r._dock_check(math.radians(-5.1))
    assert poses and math.degrees(poses[-1][2]) == pytest.approx(-11.9, abs=0.5)
    assert r.turns == [pytest.approx(math.radians(-5.1))]
    check = [e for e in r.logged if e.get("kind") == "dock_check"][-1]
    assert check["accepted"] and check["yaw_guess_deg"] != 0.0


def test_no_wall_at_all_and_it_does_not_back_in_blind(monkeypatch):
    dr = _drive_route()
    monkeypatch.setattr(dr, "DOCK_CHECK_WAIT_S", 0.0)
    monkeypatch.setattr(dr.time, "sleep", lambda s: None)
    frame = {"laser_points": [{"angle": a, "distance": 0.0, "valid": False} for a in np.linspace(-1.5, 1.5, 300)]}
    r, poses = _dock_check_runner(dr, frame, (-3.85, 1.39, math.radians(-5.1)))
    with pytest.raises(dr.Stop, match="not backing in blind"):
        r._dock_check(math.radians(-5.1))
    assert poses == [] and r.turns == []
    assert [e for e in r.logged if e.get("kind") == "dock_check"][-1]["accepted"] is False


def test_the_second_go_drives_straight_out_before_any_turn(monkeypatch):
    # 16:08: the go again began with a 38° spot turn with the tail at the charger
    dr = _drive_route()
    sim = _BackSim(contact=False)
    r = _back_runner(dr, sim, monkeypatch)
    calls = []
    r.drive_velocity = lambda step, direct=False: calls.append(("drive", step.label, step.to, tuple(sim.p)))
    r.turn_velocity = lambda heading, **kw: calls.append(("turn",))
    with pytest.raises(dr.Stop, match="not on the dock after 2 tries"):
        r.dock(rl.Step(do="dock", label="dock"))
    second = calls[[c[0] for c in calls].index("turn") + 1:]       # after the first go's turn round
    kind, label, to, at = second[0]
    assert kind == "drive" and label == "straight out from the charger"
    heading = math.atan2(to[1] - at[1], to[0] - at[0])
    assert abs(rl.wrap_pi(heading - at[2])) < math.radians(1)       # straight ahead, no turn first
    assert math.hypot(to[0] - at[0], to[1] - at[1]) >= 0.15


class _SpeedBoard(_LandingBoard):
    def __init__(self, pose):
        super().__init__(pose)
        self.speeds = []

    def set_speed(self, v):
        self.speeds.append(v)


@pytest.mark.parametrize("v_max,expect", [(0.4, [0.4, 0.25]), (0.2, [])])
def test_a_velocity_lane_lifts_the_boards_speed_cap_for_itself_only(monkeypatch, v_max, expect):
    # 7 ต.ค. 16:37: 0.40 sent, 0.24 moved -- base.max_moving_speed caps velocity
    # control too. Raised for the lane, put back for MoveTo and GoHome.
    dr = _drive_route()
    r = _lane_runner(dr, (1.0, -2.45, 0.0), monkeypatch)
    board = _SpeedBoard((1.0, -2.45, 0.0))
    board.pose = lambda: tuple(r.sdk.pose)
    board.events = lambda: []
    r.b = board
    r.limits = rl.DriveLimits(v_max=v_max, w_max=0.4)
    r.drive_velocity(rl.Step(do="drive", to=(2.0, -2.45), label="east"))
    assert board.speeds == expect


def test_the_second_go_drives_out_only_as_far_as_it_backed(monkeypatch):
    # reviewer: out by a fixed 0.15 after a 3 cm back-in would overshoot the
    # pre-dock spot and need a half turn to come back to it
    dr = _drive_route()
    sim = _BackSim(contact=False)
    r = _back_runner(dr, sim, monkeypatch)
    calls = []
    r.drive_velocity = lambda step, direct=False: calls.append(step.label)
    r.last_backed = 0.03
    r._back_in_once(out_first=True)
    assert "straight out from the charger" not in calls


def test_a_board_error_part_way_through_docking_does_not_hand_over_to_gohome(monkeypatch):
    # reviewer: the GoHome fallback is for own docking that cannot start, not
    # for one that met the charger crooked (14:47: GoHome spun 254°)
    dr = _drive_route()
    sim = _BackSim(contact=False)
    r = _back_runner(dr, sim, monkeypatch)
    real = r._back_in_once

    def second_go_fails(out_first):
        if out_first:
            raise httpx.HTTPStatusError("500", request=None, response=None)
        return real(out_first)
    r._back_in_once = second_go_fails
    with pytest.raises(dr.Stop, match="not handing over to GoHome"):
        r.dock(rl.Step(do="dock", label="dock"))
    assert sim.started == []                                      # no GoHome


class _CorridorSim:
    """The corridor as a fake SDK and lidar: the true pose and odometry's,
    odometry's heading creeping `creep` rad a metre more than the base turns
    -- what the stopped checkpoints corrected on 7 ต.ค.: 10-19 cm across the
    corridor per 2-2.5 m."""
    WALLS = np.array([(-6.0, -1.683, 6.0, -1.683), (-6.0, -3.46, 6.0, -3.46)])

    def __init__(self, pose, creep=0.05):
        self.true, self.odom, self.creep = list(pose), list(pose), creep
        self.worst = 0.0

    def call(self, command, args=None, timeout=None):
        if command == "velocitycontrol":
            return {"code": 1, "id": 7}
        if command == "getpose":
            return {"x": self.odom[0], "y": self.odom[1], "yaw": self.odom[2]}
        if command == "getcurrentaction":
            return {"id": 0}
        if command == "setvelocity":
            vx, w = args["vx"], args["omega"]
            for st, extra in ((self.true, 0.0), (self.odom, self.creep * abs(vx) * 0.1)):
                st[2] += w * 0.1 + extra
                st[0] += vx * math.cos(st[2]) * 0.1
                st[1] += vx * math.sin(st[2]) * 0.1
            self.worst = max(self.worst, abs(self.true[1] + 2.45))
        return {"code": 0, "codestr": "done"}

    def points(self):
        x, y, yaw = self.true
        a = np.radians(np.arange(-100.0, 100.0, 0.5))
        dx, dy = np.cos(yaw + a)[:, None], np.sin(yaw + a)[:, None]
        x1, y1, x2, y2 = (self.WALLS[:, i][None, :] for i in range(4))
        ex, ey = x2 - x1, y2 - y1
        den = dx * ey - dy * ex
        with np.errstate(divide="ignore", invalid="ignore"):
            t = ((x1 - x) * ey - (y1 - y) * ex) / den
            u = ((x1 - x) * dy - (y1 - y) * dx) / den
        t = np.where((np.abs(den) > 1e-12) & (t > 0.05) & (u >= 0) & (u <= 1) & (t < 8.0), t, np.inf).min(axis=1)
        ok = np.isfinite(t)
        return np.c_[t[ok] * np.cos(a[ok]), t[ok] * np.sin(a[ok])]


class _SyncPool:
    """concurrent.futures stand-in: the match runs at submit, done next tick."""
    def __init__(self, max_workers=1):
        pass

    def submit(self, fn, *a):
        f = concurrent.futures.Future()
        f.set_result(fn(*a))
        return f

    def shutdown(self, wait=True):
        pass


@pytest.mark.parametrize("walls,worst_at_most,worst_at_least", [(("cor_north", "cor_south"), 0.05, 0.0),
                                                                 ((), 1.0, 0.10)])
def test_walls_measured_on_the_move_keep_a_long_lane_on_its_line(monkeypatch, walls, worst_at_most, worst_at_least):
    # 7 ต.ค. (owner: "จังหวะทางตรงบางทีจะมีหยุดก่อนแล้วค่อยเดินต่อ"): the corridor was
    # three lanes with a stopped checkpoint between each, because odometry let it
    # drift 10-19 cm across in 2-2.5 m. One 4.9 m lane, walls measured on the way.
    dr = _drive_route()
    monkeypatch.setattr(dr.concurrent.futures, "ThreadPoolExecutor", _SyncPool)
    start = (-2.43, -2.45, 0.0)
    sim = _CorridorSim(start)
    r = _lane_runner(dr, start, monkeypatch)
    r.sdk = sim
    r.b.pose = lambda: tuple(sim.odom)
    r.b.set_pose = lambda p: setattr(sim, "odom", list(p))
    r.b.set_speed = lambda v: None
    r.limits = rl.DriveLimits(v_max=0.4, w_max=0.5)

    class W(_ClearWatcher):
        def age(self):
            return 0.05

        def points(self):
            return sim.points()
    r.watcher = lambda: W()
    r.route = rl.Route(name="t", anchors={"cor_north": rl.Anchor("y", -1.683, -6.0, 6.0),
                                          "cor_south": rl.Anchor("y", -3.46, -6.0, 6.0)}, doors={}, steps=[])
    monkeypatch.setattr(dr, "LANE_HOLD_S", 0.3)                    # without walls it ends up held, or off its lane
    try:
        r.drive_velocity(rl.Step(do="drive", to=(2.46, -2.45), use=walls, label="east along the corridor"))
    except dr.Stop:
        assert not walls
    assert worst_at_least <= sim.worst <= worst_at_most
    drive = r.logged[-1]
    if walls:
        assert drive["rolling"] and sum(1 for f in drive["rolling"] if f["taken"]) >= 4
        assert abs(sim.odom[1] - sim.true[1]) < 0.03              # the board told where the walls put it


def test_a_door_reached_round_a_corner_takes_its_heading_again_from_its_own_walls():
    # the lane into D8 now rounds a corner instead of stopping at "above D8":
    # after a step sideways the heading must still be taken again, facing the door
    dr = _drive_route()
    r = dr.Runner.__new__(dr.Runner)
    r.route = rl.Route(name="t", anchors={"ea_east": rl.Anchor("x", -1.825, -0.6, 1.68)},
                       doors={"D8": rl.Door("y", -1.633, -2.85, -2.05)}, steps=[
        rl.Step(do="checkpoint", use=("ea_east",), label="on the dock"),
        rl.Step(do="drive", to=(-2.45, 1.43), label="east"),
        rl.Step(do="drive", to=(-2.51, 0.25), use=("ea_east",), label="south"),
        rl.Step(do="door", door="D8", heading_deg=-90, until=-2.45, use=("ea_east",), label="D8 out"),
        rl.Step(do="door", door="D8", heading_deg=90, until=0.25, label="D8 in")])
    cp = r.checkpoint_for(4)
    assert cp is not None and cp.do == "checkpoint" and cp.heading_deg == -90 and cp.use == ("ea_east",)
    assert r.checkpoint_for(5) is None                              # no walls of its own, none before


def test_a_thai_word_in_a_message_does_not_end_a_run_when_stdout_is_a_file():
    # 8 ต.ค. 09:14: stdout redirected to a file is cp1252 on Windows; preflight's
    # "localization was ON" line names ฝ่ายอาคาร and crashed the run
    import io
    dr = _drive_route()
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="cp1252")
    dr.utf8_output(out)
    print("board localization was ON -- it pulls the pose in ฝ่ายอาคาร", file=out, flush=True)
    assert "ฝ่ายอาคาร".encode("utf-8") in raw.getvalue()


def test_the_junction_corner_is_a_quarter_circle_between_the_two_lanes():
    dr = _drive_route()
    g = dr.corner_geometry((2.46, -2.45), 0.0, (2.55, -0.58), r=0.45)
    assert g["C"] == pytest.approx((2.55, -2.45)) and g["A"] == pytest.approx((2.10, -2.45))
    assert g["B"] == pytest.approx((2.55, -2.00)) and g["O"] == pytest.approx((2.10, -2.00))
    poses = dr.corner_poses(g)
    assert poses[0][:2] == pytest.approx(g["A"]) and poses[18][:2] == pytest.approx(g["B"], abs=1e-9)
    assert math.degrees(poses[18][2]) == pytest.approx(90.0)
    assert dr.corner_geometry((2.46, -2.45), 0.0, (2.55, -2.10)) is None         # no room after the arc


class _JunctionSim(_CorridorSim):
    """The corridor's east end and the corridor north between the meeting rooms."""
    WALLS = np.array([(-1.0, -3.46, 3.325, -3.46), (-1.0, -1.683, 1.715, -1.683),
                      (3.325, -3.46, 3.325, 0.6), (1.715, -1.683, 1.715, 0.6)])

    def __init__(self, pose, extra=()):
        super().__init__(pose, creep=0.0)
        self.WALLS = np.vstack([self.WALLS] + [np.array(extra)] if extra else [self.WALLS])
        self.path, self.sent = [], []

    def call(self, command, args=None, timeout=None):
        if command == "setvelocity":
            self.sent.append((args["vx"], args["omega"]))
            self.path.append(tuple(self.true))
        return super().call(command, args, timeout)

    def gap(self):
        """Least gap between the 0.60 x 0.70 base and the walls over the run."""
        t = np.linspace(0, 1, 300)[:, None]
        px = self.WALLS[:, 0] + t * (self.WALLS[:, 2] - self.WALLS[:, 0])
        py = self.WALLS[:, 1] + t * (self.WALLS[:, 3] - self.WALLS[:, 1])
        best = 9.0
        for x, y, yaw in self.path:
            c, s = math.cos(yaw), math.sin(yaw)
            lx, ly = (px - x) * c + (py - y) * s, -(px - x) * s + (py - y) * c
            qx, qy = np.abs(lx) - 0.30, np.abs(ly) - 0.35
            best = min(best, float((np.hypot(np.maximum(qx, 0), np.maximum(qy, 0))
                                    + np.minimum(np.maximum(qx, qy), 0)).min()))
        return best


def _junction_runner(dr, sim, monkeypatch):
    start = tuple(sim.true)
    r = _lane_runner(dr, start, monkeypatch)
    r.sdk = sim
    r.b.pose = lambda: tuple(sim.odom)
    r.b.set_pose = lambda p: setattr(sim, "odom", list(p))
    r.b.set_speed = lambda v: None
    r.b.power = lambda: {"dockingStatus": "not_on_dock"}
    r.limits = rl.DriveLimits(v_max=0.4, w_max=0.5)
    r.turn_width = 0.70
    r.turn_velocity = lambda h, **k: (_ for _ in ()).throw(AssertionError("spot turn"))

    class W(_ClearWatcher):
        def age(self):
            return 0.05

        def points(self):
            return sim.points()
    r.watcher = lambda: W()
    r.route = rl.Route(name="t", anchors={}, doors={}, steps=[
        rl.Step(do="drive", to=(2.46, -2.45), label="east to the junction"),
        rl.Step(do="drive", to=(2.55, -0.58), label="north to D11")])
    return r


def test_two_lanes_at_a_right_angle_are_driven_round_the_corner_without_stopping(monkeypatch):
    # owner 7 ต.ค.: "ทำให้เหลือ 1 นาที ... ทำครบรวมเลี้ยวโค้ง" -- at the junction a
    # stop, a 5.5 s spot turn, a checkpoint and a fresh start were ~9 s
    dr = _drive_route()
    sim = _JunctionSim((0.0, -2.45, 0.0))
    r = _junction_runner(dr, sim, monkeypatch)
    calls = []
    real = sim.call
    sim.call = lambda c, a=None, t=None: (calls.append(c), real(c, a, t))[1]
    assert r.run_chain([1, 2]) == 2
    assert calls.count("velocitycontrol") == 1                    # one action, never stopped
    first = [e for e in r.logged if e.get("kind") == "drive"][0]
    assert first["handed_over"] == "north to D11"
    corner = [t for t in first["trace"] if isinstance(t, tuple) and t[6] == "corner"]
    assert corner and min(t[4] for t in corner) >= 0.1             # still moving all round it
    x, y, yaw = sim.true
    assert (x, y) == pytest.approx((2.55, -0.58), abs=0.03)
    assert abs(math.degrees(rl.wrap_pi(yaw - math.pi / 2))) < 4.0
    assert sim.gap() > 0.10


def test_a_corner_with_something_on_the_arc_stops_and_leaves_the_turn_to_the_next_lane(monkeypatch):
    dr = _drive_route()
    box = [(2.80, -2.00, 2.92, -1.90)]          # where the body swings out on the arc, clear of the lane
    sim = _JunctionSim((0.0, -2.45, 0.0), extra=box)
    r = _junction_runner(dr, sim, monkeypatch)
    assert r.run_chain([1, 2]) == 1
    first = [e for e in r.logged if e.get("kind") == "drive"][0]
    assert first["corner"] and "clear" in first["corner"] and not first.get("handed_over")
    assert sim.true[0] == pytest.approx(2.46, abs=0.04)            # stopped at the lane's own end
    assert sim.gap() > 0.05


def test_a_chained_lane_that_must_stop_sends_its_zero_before_anything_else(monkeypatch):
    # reviewer: in a chain the session is run_chain's, so a lane breaking for the
    # stair fence sent its zero only after a pose read, logging and a sleep --
    # ~0.5 s and 15-18 cm more at 0.35 m/s
    dr = _drive_route()
    sim = _JunctionSim((0.0, -2.45, 0.0))
    r = _junction_runner(dr, sim, monkeypatch)
    r.fences = [(1.0, 1.3, -2.6, -2.3)]                            # across lane 1
    events = []
    real_call, real_pose = sim.call, r.b.pose
    sim.call = lambda c, a=None, t=None: (events.append(("set", a["vx"]) if c == "setvelocity" else (c,)),
                                          real_call(c, a, t))[1]
    r.b.pose = lambda: (events.append(("pose",)), real_pose())[1]
    with pytest.raises(dr.Stop, match="forbidden"):
        r.run_chain([1, 2])
    last_moving = max(k for k, e in enumerate(events) if e[0] == "set" and e[1] > 0)
    after = events[last_moving + 1:]
    first_set = next(k for k, e in enumerate(after) if e[0] == "set")
    assert after[first_set] == ("set", 0.0)
    assert ("pose",) not in after[:first_set]                      # the zero before the REST pose read


def test_walls_on_both_sides_may_move_the_lane_further_than_one_wall(monkeypatch):
    # reviewer: the junction's stop moved y 18-19 cm twice with both corridor walls
    # agreeing; capped at 0.15 the corridor lane after it would take no fix at all
    dr = _drive_route()
    monkeypatch.setattr(dr.concurrent.futures, "ThreadPoolExecutor", _SyncPool)
    start = (-2.43, -2.45, 0.0)
    sim = _CorridorSim(start, creep=0.0)
    sim.odom[1] += 0.20                                             # odometry 20 cm off across
    r = _lane_runner(dr, start, monkeypatch)
    r.sdk = sim
    r.b.pose = lambda: tuple(sim.odom)
    r.b.set_pose = lambda p: setattr(sim, "odom", list(p))
    r.b.set_speed = lambda v: None
    r.limits = rl.DriveLimits(v_max=0.4, w_max=0.5)

    class W(_ClearWatcher):
        def age(self):
            return 0.05

        def points(self):
            return sim.points()
    r.watcher = lambda: W()
    r.route = rl.Route(name="t", anchors={"cor_north": rl.Anchor("y", -1.683, -6.0, 6.0),
                                          "cor_south": rl.Anchor("y", -3.46, -6.0, 6.0)}, doors={}, steps=[])
    r.drive_velocity(rl.Step(do="drive", to=(2.46, -2.25), use=("cor_north", "cor_south"), label="east"))
    taken = [f for f in r.logged[-1]["rolling"] if f["taken"]]
    assert taken and taken[0]["both_sides"] and abs(taken[0]["lat"]) > 0.15
    assert sim.true[1] == pytest.approx(-2.25, abs=0.04)            # where the lane is, by the walls


def test_odometry_8_degrees_out_after_a_corner_is_corrected_on_the_move_not_stopped_for(monkeypatch):
    # 8 ต.ค. 09:16: 0.1 m after the first corner both walls put the heading 6.6°
    # off odometry's; the 6° cap ended the run. The stop it was driven home from
    # measured 8.0° -- real, as spot turns had been 6-12° out all of 7 ต.ค.
    dr = _drive_route()
    monkeypatch.setattr(dr.concurrent.futures, "ThreadPoolExecutor", _SyncPool)
    start = (-2.43, -2.45, 0.0)
    sim = _CorridorSim(start, creep=0.0)
    sim.odom[2] += math.radians(8.0)                               # odometry's heading 8° out
    r = _lane_runner(dr, start, monkeypatch)
    r.sdk = sim
    r.b.pose = lambda: tuple(sim.odom)
    r.b.set_pose = lambda p: setattr(sim, "odom", list(p))
    r.b.set_speed = lambda v: None
    r.limits = rl.DriveLimits(v_max=0.4, w_max=0.5)
    r.turn_velocity = lambda h, **k: None

    class W(_ClearWatcher):
        def age(self):
            return 0.05

        def points(self):
            return sim.points()
    r.watcher = lambda: W()
    r.route = rl.Route(name="t", anchors={"cor_north": rl.Anchor("y", -1.683, -6.0, 6.0),
                                          "cor_south": rl.Anchor("y", -3.46, -6.0, 6.0)}, doors={}, steps=[])
    r.drive_velocity(rl.Step(do="drive", to=(1.5, -2.45), use=("cor_north", "cor_south"), label="east"))
    taken = [f for f in r.logged[-1]["rolling"] if f["taken"]]
    assert taken and abs(taken[0]["dyaw_deg"]) > 6.0
    assert sim.true[1] == pytest.approx(-2.45, abs=0.05)


def test_one_frame_turning_the_heading_9_degrees_is_not_followed_alone(monkeypatch):
    # reviewer: the 12° cap let a single mismatched both-walls frame steer the
    # robot up to 12° to follow it, the stair fence judged from that pose. Over
    # 6° it waits for the next frame to say the same heading.
    import dataclasses
    dr = _drive_route()
    monkeypatch.setattr(dr.concurrent.futures, "ThreadPoolExecutor", _SyncPool)
    start = (-2.43, -2.45, 0.0)
    sim = _CorridorSim(start, creep=0.0)                            # odometry right all along
    r = _lane_runner(dr, start, monkeypatch)
    r.sdk = sim
    r.b.pose = lambda: tuple(sim.odom)
    r.b.set_pose = lambda p: setattr(sim, "odom", list(p))
    r.b.set_speed = lambda v: None
    r.limits = rl.DriveLimits(v_max=0.4, w_max=0.5)
    real, n = rl.correct_pose, {"k": 0}

    def one_bad(guess, pts, anchors, doors):
        f = real(guess, pts, anchors, doors)
        n["k"] += 1
        if n["k"] == 1 and f.accepted:                              # the first frame alone is 9° out
            p = f.pose
            return dataclasses.replace(f, pose=(p[0], p[1], p[2] + math.radians(9.0)),
                                       dyaw=(f.dyaw or 0.0) + math.radians(9.0))
        return f
    monkeypatch.setattr(rl, "correct_pose", one_bad)

    class W(_ClearWatcher):
        def age(self):
            return 0.05

        def points(self):
            return sim.points()
    r.watcher = lambda: W()
    r.route = rl.Route(name="t", anchors={"cor_north": rl.Anchor("y", -1.683, -6.0, 6.0),
                                          "cor_south": rl.Anchor("y", -3.46, -6.0, 6.0)}, doors={}, steps=[])
    r.drive_velocity(rl.Step(do="drive", to=(1.5, -2.45), use=("cor_north", "cor_south"), label="east"))
    rows = r.logged[-1]["rolling"]
    assert abs(rows[0]["dyaw_deg"]) > 8.0 and not rows[0]["taken"] and "agree" in rows[0]["why"]
    assert all(abs(f["dyaw_deg"]) < 2.0 for f in rows if f["taken"])
    assert abs(sim.true[2]) < math.radians(2.0) and r.logged[-1]["stopped"] is None


def test_a_dropped_link_on_the_corner_stops_instead_of_driving_a_lane_again(monkeypatch):
    # reviewer: re-driving lane k from part way round meant a 150-180° turn and a
    # drive back, or "not a straight lane"
    dr = _drive_route()
    sim = _JunctionSim((0.0, -2.45, 0.0))
    r = _junction_runner(dr, sim, monkeypatch)
    real = sim.call
    state = {"arc": False}

    def call(c, a=None, t=None):
        if c == "setvelocity" and state["arc"]:
            raise OSError("relay gone")
        out = real(c, a, t)
        if c == "setvelocity" and abs(a["omega"]) > 0.3:
            state["arc"] = True                                     # turning at the arc's rate
        return out
    sim.call = call
    with pytest.raises(dr.Stop, match="on the corner") as e:
        r.run_chain([1, 2])
    assert not isinstance(e.value, dr.LinkLost)


def test_without_a_fresh_lidar_frame_the_corner_is_not_taken(monkeypatch):
    # reviewer: a frame up to 0.6 s old, laid on the current pose, puts what is
    # ahead up to 0.2 m further on than it is -- the dock corner has 0.14 m
    dr = _drive_route()
    sim = _JunctionSim((0.0, -2.45, 0.0))
    r = _junction_runner(dr, sim, monkeypatch)

    class Stale(_ClearWatcher):
        def age(self):
            return 0.4

        def points(self):
            return sim.points()
    r.watcher = lambda: Stale()
    assert r.run_chain([1, 2]) == 1
    assert "no fresh lidar frame" in [e for e in r.logged if e.get("kind") == "drive"][0]["corner"]


def _slip_on_the_arc(sim, when, by):
    """Odometry jumps `by` (x, y) the first time `when(true pose)` holds on a
    setvelocity: the robot off its arc by odometry's account."""
    real, state = sim.call, {"done": False}

    def call(c, a=None, t=None):
        out = real(c, a, t)
        if c == "setvelocity" and not state["done"] and when(sim.true):
            sim.odom[0] += by[0]
            sim.odom[1] += by[1]
            state["done"] = True
        return out
    sim.call = call
    return state


def test_an_arc_given_up_at_its_start_goes_on_along_the_lane_and_turns_at_the_corner(monkeypatch):
    # reviewer: stopped in the arc's first ~12° the robot was 0.42 m off the next
    # lane -- past the 0.35 it may start from -- and the route ended there
    dr = _drive_route()
    sim = _JunctionSim((0.0, -2.45, 0.0))
    r = _junction_runner(dr, sim, monkeypatch)
    r.turn_velocity = lambda h, **k: None
    g = dr.corner_geometry((2.46, -2.45), 0.0, (2.55, -0.58))
    slip = _slip_on_the_arc(sim, lambda p: p[2] > math.radians(3), (0.0, -0.12))    # outward
    assert r.run_chain([1, 2]) == 1
    assert slip["done"]
    first = [e for e in r.logged if e.get("kind") == "drive"][0]
    assert "on along the lane to the corner" in first["corner"] and not first.get("handed_over")
    assert first["stopped"] is None
    x, y, _ = sim.odom
    assert x == pytest.approx(2.46, abs=0.05) and x > g["A"][0] + 0.2       # at the lane's end, not on the arc
    lat_next = abs(x - 2.55)
    assert lat_next <= dr.LANE_START_MAX_OFF_M
    r.drive_velocity(r.route.steps[1])                                        # the next lane starts from there
    assert r.logged[-1]["stopped"] is None


def test_an_arc_given_up_past_its_middle_stops_for_the_next_lane(monkeypatch):
    dr = _drive_route()
    sim = _JunctionSim((0.0, -2.45, 0.0))
    r = _junction_runner(dr, sim, monkeypatch)
    r.turn_velocity = lambda h, **k: None
    out = (math.cos(math.radians(-45)) * 0.12, math.sin(math.radians(-45)) * 0.12)
    slip = _slip_on_the_arc(sim, lambda p: p[2] > math.radians(45), out)
    assert r.run_chain([1, 2]) == 1
    assert slip["done"]
    first = [e for e in r.logged if e.get("kind") == "drive"][0]
    assert "stopping, the next lane turns on the spot" in first["corner"]
    assert abs(sim.odom[0] - 2.55) <= dr.LANE_START_MAX_OFF_M - 0.05
    r.drive_velocity(r.route.steps[1])
    assert r.logged[-1]["stopped"] is None


def test_a_lane_that_reaches_the_arc_off_its_line_does_not_start_it(monkeypatch):
    # reviewer: a lane may come 0.20 off (a rolling fix just before the arc can move
    # it 0.30) -- off the arc from its first tick
    dr = _drive_route()
    sim = _JunctionSim((0.0, -2.45, 0.0))
    r = _junction_runner(dr, sim, monkeypatch)
    g = dr.corner_geometry((2.46, -2.45), 0.0, (2.55, -0.58))
    _slip_on_the_arc(sim, lambda p: p[0] > g["A"][0] - 0.03, (0.0, 0.09))
    assert r.run_chain([1, 2]) == 1
    first = [e for e in r.logged if e.get("kind") == "drive"][0]
    assert "off the lane before the arc" in first["corner"]
    assert not [t for t in first["trace"] if isinstance(t, tuple) and t[6] == "corner"]


def test_a_lidar_stall_on_the_arc_is_waited_out_not_given_up(monkeypatch):
    # reviewer: a stale frame reads as something ahead; held 1 s on the arc gave
    # it up, and from the arc's first degrees the next lane would not start
    dr = _drive_route()
    sim = _JunctionSim((0.0, -2.45, 0.0))
    r = _junction_runner(dr, sim, monkeypatch)
    clock = {"t": 1000.0}
    monkeypatch.setattr(dr.time, "time", lambda: clock["t"])
    monkeypatch.setattr(dr.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + max(s, 0.01)))
    g = dr.corner_geometry((2.46, -2.45), 0.0, (2.55, -0.58))
    stall = {"from": None}

    class Stalls(_ClearWatcher):
        def age(self):
            if sim.true[0] >= g["A"][0] + 0.01 and stall["from"] is None:
                stall["from"] = clock["t"]
            return 1.0 if stall["from"] is not None and clock["t"] - stall["from"] < 3.0 else 0.05

        def points(self):
            return sim.points()
    r.watcher = lambda: Stalls()
    assert r.run_chain([1, 2]) == 2
    first = [e for e in r.logged if e.get("kind") == "drive"][0]
    assert first["handed_over"] == "north to D11"
    assert sim.gap() > 0.10


def test_held_on_the_arc_it_waits_and_sets_off_again_ramped_from_standstill(monkeypatch):
    # reviewer: the side hold skipped `last_tick`; the first arc tick after a hold
    # ramped over the whole hold (dt 0.5): 0.175 m/s and 0.39 rad/s at once, beside
    # whoever had held it. And a hold on the arc is waited out, not given up.
    dr = _drive_route()
    sim = _JunctionSim((0.0, -2.45, 0.0))
    r = _junction_runner(dr, sim, monkeypatch)
    clock = {"t": 1000.0}
    monkeypatch.setattr(dr.time, "time", lambda: clock["t"])
    monkeypatch.setattr(dr.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + max(s, 0.01)))
    held = {"n": 0}

    class Beside(_ClearWatcher):
        def age(self):
            return 0.05

        def points(self):
            pts = sim.points()
            if sim.true[2] > math.radians(20) and held["n"] < 15:     # someone at its side on the arc, 1.5 s
                held["n"] += 1
                return np.vstack([pts, [[-0.05, 0.37], [0.0, 0.37], [0.05, 0.37]]])
            return pts
    r.watcher = lambda: Beside()
    assert r.run_chain([1, 2]) == 2
    assert held["n"] == 15
    first = [e for e in r.logged if e.get("kind") == "drive"][0]
    assert first["handed_over"] == "north to D11"
    k = max(i for i, t in enumerate(first["trace"]) if t == "held beside")
    after = [t for t in first["trace"][k:] if isinstance(t, tuple)]
    assert after and after[0][6] == "corner"
    assert 0.0 < after[0][4] <= 0.04                         # 0.35 m/s² x 0.1 s, not x 0.5


def test_a_dropped_link_keeps_the_walls_word(monkeypatch):
    dr = _drive_route()
    r = dr.Runner.__new__(dr.Runner)
    poses = []

    class B:
        def pose(self): return (1.0, -2.45, 0.0)
        def set_pose(self, p): poses.append(p)
    r.b = B()
    e = dr.LinkLost("gone")
    e.corr = (0.0, -0.12, 0.0)
    r.keep_corrections(e)
    assert poses == [pytest.approx((1.0, -2.57, 0.0))]


class _TouchAndGoBoard(_DockBoard):
    """GoHome that touches the contacts for a moment, crooked, and slides off."""
    def power(self):
        self.polls += 1
        return {"dockingStatus": "on_dock" if self.polls == 2 else "not_on_dock", "isCharging": False}

    def pose(self):
        return (-4.17, 1.28, 1.107)                               # ~63° off the dock's line, as at 14:47


def test_gohome_on_the_contacts_for_a_moment_is_not_docked(monkeypatch):
    dr = _drive_route()
    board = _TouchAndGoBoard()
    r = _dock_runner(dr, board, monkeypatch)
    with pytest.raises(dr.Stop, match="did not dock"):
        r.dock(rl.Step(do="dock", label="dock"))
    assert r.logged[-1]["ok"] is False


def test_a_leaf_beside_the_front_corner_is_not_something_ahead():
    # 7 ต.ค. 10:51, inside the big room: D11's leaf 0.38 m to the left, 5 cm
    # ahead of the centre, read as "0.05 m ahead" — beside, not in the way.
    beside = np.c_[np.linspace(-0.2, 0.1, 30), np.full(30, 0.38)]
    assert rl.front_distance(beside) is None
    ahead = np.vstack([beside, [[0.62, 0.0], [0.63, 0.05], [0.64, -0.05]]])
    assert rl.front_distance(ahead) == pytest.approx(0.64)


def test_a_dropped_link_a_few_cm_from_a_lanes_end_counts_it_done(monkeypatch):
    # reviewer: after a chain's drop the lane was gone at again as a fresh step --
    # 3 cm from its end that was "not a straight lane" and the route ended
    dr = _drive_route()
    r = _lane_runner(dr, (2.43, -2.45, 0.0), monkeypatch)
    r.b.pose = lambda: (2.43, -2.45, 0.0)
    r.b.power = lambda: {"dockingStatus": "not_on_dock"}
    lane = rl.Step(do="drive", to=(2.46, -2.45), label="east to the junction")

    def drive(step):
        raise AssertionError("driven again")
    r.run_step(6, lane, {"drive": drive}, dropped=True)
    with pytest.raises(AssertionError, match="driven again"):
        r.run_step(6, lane, {"drive": drive})


class _StallsNearIt:
    """Turns as commanded (0.8 of it, nothing under 0.13 rad/s) until 3.7° short
    of `target`, then not at all."""
    def __init__(self, yaw, target):
        self.pose, self.target = (0.0, 0.0, yaw), target

    def call(self, command, args=None, timeout=None):
        if command == "velocitycontrol":
            return {"code": 1, "id": 5}
        if command == "getpose":
            x, y, yaw = self.pose
            return {"x": x, "y": y, "yaw": yaw}
        if command == "getcurrentaction":
            return {"id": 0}
        if command == "setvelocity":
            x, y, yaw = self.pose
            w = args["omega"]
            if abs(w) >= 0.13 and abs(rl.wrap_pi(self.target - yaw)) > math.radians(3.7):
                yaw += 0.8 * w * 0.1
            self.pose = (x, y, yaw)
        return {"code": 0, "codestr": "done"}


def test_a_turn_that_stalls_inside_its_allowance_is_done_not_pushed_for_25_s(monkeypatch):
    # reviewer: a turn under way no longer stops at the allowance (5°); a base that
    # stalled 3.7° short pushed 0.15 rad/s for the whole 25 s go
    dr = _drive_route()
    r = _lane_runner(dr, (0.0, 0.0, 0.0), monkeypatch)
    clock = {"t": 1000.0}
    monkeypatch.setattr(dr.time, "time", lambda: clock["t"])
    monkeypatch.setattr(dr.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + max(s, 0.01)))
    r.sdk = sdk = _StallsNearIt(0.0, math.radians(90))
    r.b.pose = lambda: sdk.pose
    r.turn_space_clear = lambda turn=None, wait_s=6.0: None
    t0 = clock["t"]
    err = r.turn_velocity(math.radians(90), tol_deg=5.0, tries=1)
    assert abs(math.degrees(err)) <= 5.0
    assert clock["t"] - t0 < 15.0
    assert "still inside the allowance" in r.logged[-1]["trace"]


def test_a_lane_setting_off_just_before_its_corner_waits_for_a_fresh_frame_to_decide(monkeypatch):
    # reviewer: out of a door the lead lane starts 6 cm before the arc -- under the
    # 10 cm where a stale frame decided ("no fresh lidar frame": no corner)
    dr = _drive_route()
    g = dr.corner_geometry((2.46, -2.45), 0.0, (2.55, -0.58))
    sim = _JunctionSim((g["A"][0] - 0.06, -2.45, 0.0))
    r = _junction_runner(dr, sim, monkeypatch)
    calls = {"n": 0}

    class Slow(_ClearWatcher):
        def age(self):
            calls["n"] += 1
            return 0.4 if calls["n"] <= 3 else 0.05                  # the first frames over REST are late

        def points(self):
            return sim.points()
    r.watcher = lambda: Slow()
    assert r.run_chain([1, 2]) == 2
    assert [e for e in r.logged if e.get("kind") == "drive"][0]["handed_over"] == "north to D11"



#: What was not measured at the faster rate: a longer lag, a base that brakes
#: slower, more of the command turned (reviewer: one fast cruise slowing at 20°
#: went 4-12° past on these)
BASES = [{}, {"lag": 3}, {"down": 0.7}, {"gain": 0.9}, {"lag": 3, "down": 0.7}, {"down": 0.5}]


@pytest.mark.parametrize("base", BASES)
def test_a_faster_spin_still_lands_on_the_angle(monkeypatch, base):
    # owner 8 ต.ค. "ลองเพิ่มความเร็ว": --spin 0.8 while more than 45° is left, then the
    # measured 0.5 into the slow end -- on every base, not only the measured one
    dr = _drive_route()
    lim = dr.lane_limits(0.45, 0.5, 0.8)
    assert lim.w_spin_fast == 0.8 and lim.w_spin == 0.5 and lim.w_max == 0.5
    for target, start in ((90, 0), (-90, 0), (-179, 0), (35, 0), (-12, 0), (60, 0)):
        err, beyond, t = spin(target, start, limits=lim, base=base)
        slow = spin(target, start, limits=dr.lane_limits(0.45, 0.5), base=base)[2]
        assert abs(err) <= 2.0 and beyond < 1.0, (target, err, beyond)
        assert t <= slow + 1e-9, (target, t, slow)
        if abs(target) >= 90:
            assert t < slow - 0.5, (target, t, slow)                  # and it is faster where it can be
    assert dr.lane_limits(0.45, 0.5).w_spin_fast == 0.0               # the default is unchanged


class _CallsSdk(_VelocitySdk):
    def __init__(self, pose):
        super().__init__(pose)
        self.calls = []

    def call(self, command, args=None, timeout=None):
        self.calls.append(command)
        return super().call(command, args, timeout)


@pytest.mark.parametrize("board_idle", [False, True])
def test_a_spot_turn_waits_for_the_base_not_the_boards_watchdog(monkeypatch, board_idle):
    # 1.0-1.3 s after every turn's zero was the board dropping the action (its
    # ~1 s watchdog), x 13 turns a round; the coast is over in ~0.4 s. Docking
    # keeps the board's wait: GoHome may follow it.
    dr = _drive_route()
    r = _lane_runner(dr, (0.0, 0.0, 0.0), monkeypatch)
    r.sdk = sdk = _CallsSdk((0.0, 0.0, 0.0))
    r.b.pose = lambda: tuple(sdk.pose)
    r.turn_space_clear = lambda turn=None, wait_s=6.0: None
    r.turn_velocity(math.radians(90), board_idle=board_idle)
    assert ("cancelaction" in sdk.calls) == board_idle
    assert any(isinstance(t, tuple) and "settled" in t for t in r.logged[-1]["trace"]) != board_idle


@pytest.mark.parametrize("late,posed", [(0.0, False), (0.1, False), (0.1, True)])
def test_a_wall_across_the_way_puts_the_lane_right_along_it_before_the_corner(monkeypatch, late, posed):
    # 8 ต.ค., five runs: 0.1 m round the corner off the dock lane the walls put the
    # robot 3.4-5.2 cm east of odometry; the 0.76 m lane after it brought ~1 cm
    # back and D8 out stepped sideways every run. The dock lane names the wall
    # ahead (ea_east): its length is measured on the way.
    dr = _drive_route()
    monkeypatch.setattr(dr.concurrent.futures, "ThreadPoolExecutor", _SyncPool)
    ends = {}
    for use in ((), ("j_east",)):
        sim = _JunctionSim((0.0, -2.45, 0.0))
        sim.odom[0] -= 0.05                                         # odometry 5 cm short along the lane
        r = _junction_runner(dr, sim, monkeypatch)

        seen, seen_odom = [], []                                    # the poses, tick by tick
        real_call = sim.call

        def call(c, a=None, t=None, real_call=real_call, sim=sim, seen=seen, seen_odom=seen_odom):
            if c == "setvelocity":
                seen.append(tuple(sim.true))
                seen_odom.append(tuple(sim.odom))
            return real_call(c, a, t)
        sim.call = call

        class Late(_ClearWatcher):
            # a frame `late` s old: seen a tick back, `v x late` behind where the pose
            # is read -- along the lane that is 4.5 cm at 0.45 m/s. `posed`: the
            # frame carries the board's pose it was taken at (getlaserscan x/y/yaw)
            # and its age says nothing (the board's own latency is not in it)
            def age(self):
                return 0.0 if posed else late

            if posed:
                def frame(self, sim=sim, seen_odom=seen_odom):
                    return self.points(), (seen_odom[-1] if seen_odom else tuple(sim.odom))

            def points(self, sim=sim, seen=seen):
                if late and seen:
                    keep = list(sim.true)
                    sim.true[:] = list(seen[-1])                    # before the last 0.1 s command
                    pts = sim.points()
                    sim.true[:] = keep
                    return pts
                return sim.points()
        r.watcher = lambda Late=Late: Late()
        r.route = rl.Route(name="t", anchors={"j_east": rl.Anchor("x", 3.325, -3.46, 0.6)}, doors={}, steps=[
            rl.Step(do="drive", to=(2.46, -2.45), use=use, label="east to the junction"),
            rl.Step(do="drive", to=(2.55, -0.58), label="north to D11")])
        assert r.run_chain([1, 2]) == 2
        ends[use] = sim.true[0]
        if use:
            rows = [e for e in r.logged if e.get("kind") == "drive"][0]["rolling"]
            assert any(row.get("along_taken") for row in rows)
    assert abs(ends[()] - 2.55) > 0.035                            # odometry's 5 cm, carried round the corner
    assert ends[("j_east",)] == pytest.approx(2.55, abs=0.02)



def _lingering_runner(dr):
    calls = []

    class Sdk:
        def call(self, command, args=None, timeout=None):
            calls.append(command)
            return {"action_type": 0} if command == "getcurrentaction" else {"code": 0}

    class B(dr.Board):
        def __init__(self):
            pass

        def post(self, path, body=None):
            calls.append("REST start")
            return {"action_id": 7}

        def put(self, path, body=None):
            calls.append("set_speed")

    r = dr.Runner.__new__(dr.Runner)
    r.sdk, r.b = Sdk(), B()
    r._lingering = None
    r.b.before_action = r._drop_lingering
    return r, calls


@pytest.mark.parametrize("ago,dropped", [(0.3, True), (5.0, False)])
def test_a_turns_action_is_ended_before_a_rest_action_or_a_speed_change(ago, dropped):
    # reviewer: move() turned (not waiting for the board) and sent MoveTo with the
    # velocity action still current; and a lane may take a turn's action back,
    # set up under the speed cap from before the lane raised it
    dr = _drive_route()
    r, calls = _lingering_runner(dr)
    r._lingering = dr.time.time() - ago
    r.b.start(dr.MOVE_TO, {})
    assert calls[-1] == "REST start"
    assert ("cancelaction" in calls) == dropped
    if dropped:
        assert calls.index("cancelaction") < calls.index("REST start")
    r2, calls2 = _lingering_runner(dr)
    r2._lingering = dr.time.time() - ago
    with r2._board_speed(0.45):
        pass
    assert ("cancelaction" in calls2) == dropped
    assert calls2[-2:] == ["set_speed", "set_speed"]                 # raised, then put back


def test_an_along_fix_after_the_corner_was_judged_has_it_judged_again(monkeypatch):
    # reviewer: the arc's sweep was cleared from where the robot was thought to be;
    # a fix of up to 8 cm along after that moves the arc it will drive
    dr = _drive_route()
    monkeypatch.setattr(dr.concurrent.futures, "ThreadPoolExecutor", _SyncPool)
    monkeypatch.setattr(dr, "ROLL_FIX_EVERY_M", 0.5)               # fixes at 0.3, 0.8, 1.3, 1.8 m
    judged = []
    real = dr.corner_clear
    monkeypatch.setattr(dr, "corner_clear", lambda *a, **k: (judged.append(1), real(*a, **k))[1])
    sim = _JunctionSim((0.0, -2.45, 0.0))
    r = _junction_runner(dr, sim, monkeypatch)
    slip = {"done": False}
    real_call = sim.call

    def call(c, a=None, t=None):
        out = real_call(c, a, t)
        if c == "setvelocity" and not slip["done"] and sim.true[0] > 1.74:
            sim.odom[0] -= 0.04                                     # 4 cm of wheel slip, after the judging
            slip["done"] = True
        return out
    sim.call = call

    class Now(_ClearWatcher):
        def age(self):
            return 0.0

        def points(self):
            return sim.points()
    r.watcher = lambda: Now()
    r.route = rl.Route(name="t", anchors={"j_east": rl.Anchor("x", 3.325, -3.46, 0.6)}, doors={}, steps=[
        rl.Step(do="drive", to=(2.46, -2.45), use=("j_east",), label="east to the junction"),
        rl.Step(do="drive", to=(2.55, -0.58), label="north to D11")])
    assert r.run_chain([1, 2]) == 2
    assert slip["done"] and len(judged) == 2
    assert sim.true[0] == pytest.approx(2.55, abs=0.02)


@pytest.mark.parametrize("off", [0.05, -0.065])
def test_a_short_lane_gets_onto_its_line_within_its_length(monkeypatch, off):
    # 8 ต.ค., every run: the 0.76 m lane into D8 started 4-5 cm off (left by the
    # corner) and ended 4 cm off -- pursuit 0.8 m ahead turns too gently for that
    # length -- and D8 out stepped sideways (~10 s). Half its length ahead, not
    # under 0.4 m.
    dr = _drive_route()
    start = (0.0, -2.45 + off, 0.0)
    sim = _CorridorSim(start, creep=0.0)
    r = _lane_runner(dr, start, monkeypatch)
    r.sdk = sim
    r.b.pose = lambda: tuple(sim.odom)
    r.b.set_pose = lambda p: setattr(sim, "odom", list(p))
    r.b.set_speed = lambda v: None
    r.limits = dr.lane_limits(0.45, 0.5)
    r.turn_velocity = lambda h, **k: None

    class W(_ClearWatcher):
        def age(self):
            return 0.05

        def points(self):
            return sim.points()
    r.watcher = lambda: W()
    r.drive_velocity(rl.Step(do="drive", to=(0.76, -2.45), label="south onto D8's line"))
    assert abs(sim.true[1] + 2.45) < 0.02, sim.true                 # within D8's 2 cm line-up
    assert abs(math.degrees(sim.true[2])) < 6.0


def test_an_along_error_over_the_cap_is_taken_when_two_frames_agree(monkeypatch):
    # reviewer: the corridor's x was 10-20 cm off already at its start (7-8 ต.ค.
    # stops), the same all along it; over the 8 cm cap every along fix was
    # refused and the corner north still came 10-15 cm late. Two frames agreeing:
    # taken.
    dr = _drive_route()
    monkeypatch.setattr(dr.concurrent.futures, "ThreadPoolExecutor", _SyncPool)
    sim = _JunctionSim((0.0, -2.45, 0.0))
    sim.odom[0] -= 0.12                                             # odometry 12 cm short along the lane
    r = _junction_runner(dr, sim, monkeypatch)

    class Now(_ClearWatcher):
        def age(self):
            return 0.0

        def points(self):
            return sim.points()
    r.watcher = lambda: Now()
    r.route = rl.Route(name="t", anchors={"j_east": rl.Anchor("x", 3.325, -3.46, 0.6)}, doors={}, steps=[
        rl.Step(do="drive", to=(2.46, -2.45), use=("j_east",), label="east to the junction"),
        rl.Step(do="drive", to=(2.55, -0.58), label="north to D11")])
    r.run_chain([1, 2])
    rows = [e for e in r.logged if e.get("kind") == "drive"][0]["rolling"]
    assert not rows[0].get("along_taken") and rows[0]["along_fix"] > 0.08     # one frame: waits
    assert any(row.get("along_taken") for row in rows[1:])                   # the next agrees: taken
    assert sim.true[0] == pytest.approx(2.55, abs=0.02)


def test_a_short_lane_starting_far_off_keeps_the_long_lookahead(monkeypatch):
    # reviewer: with a short lookahead a lane starting 0.19-0.30 m off (a stop can
    # move y 16-19 cm) turned on the spot -- lane_command's align, with no room
    # check. Only lanes starting near their line get the short one.
    dr = _drive_route()
    start = (0.0, -2.45 + 0.30, 0.0)
    sim = _CorridorSim(start, creep=0.0)
    r = _lane_runner(dr, start, monkeypatch)
    r.sdk = sim
    r.b.pose = lambda: tuple(sim.odom)
    r.b.set_pose = lambda p: setattr(sim, "odom", list(p))
    r.b.set_speed = lambda v: None
    r.limits = dr.lane_limits(0.45, 0.5)
    r.turn_velocity = lambda h, **k: None

    class W(_ClearWatcher):
        def age(self):
            return 0.05

        def points(self):
            return sim.points()
    r.watcher = lambda: W()
    r.drive_velocity(rl.Step(do="drive", to=(0.90, -2.45), label="a short lane"))
    phases = [t[6] for t in r.logged[-1]["trace"] if isinstance(t, tuple)]
    assert "align" not in phases


def test_an_agreeing_along_fix_in_a_frame_refused_for_its_heading_waits_for_the_next(monkeypatch):
    # reviewer: the agreeing along value was dropped when that frame was refused
    # for something else (its heading here); two more readings, ~1.2 m, to try again
    dr = _drive_route()
    monkeypatch.setattr(dr.concurrent.futures, "ThreadPoolExecutor", _SyncPool)
    start = (0.0, -2.45, 0.0)
    sim = _CorridorSim(start, creep=0.0)
    r = _lane_runner(dr, start, monkeypatch)
    r.sdk = sim
    r.b.pose = lambda: tuple(sim.odom)
    r.b.set_pose = lambda p: setattr(sim, "odom", list(p))
    r.b.set_speed = lambda v: None
    r.limits = dr.lane_limits(0.45, 0.5)

    class W(_ClearWatcher):
        def age(self):
            return 0.0

        def points(self):
            return sim.points()
    r.watcher = lambda: W()
    yaws = iter([0.0, math.radians(5.0), 0.0, 0.0, 0.0, 0.0])

    def fix(guess, pts, anchors, doors):
        dyaw = next(yaws, 0.0)
        pose = (guess[0] + 0.12, guess[1], guess[2] + dyaw)       # 12 cm along, by the wall ahead
        return rl.Fix(True, "", guess, pose, dx=0.12, dy=None, dyaw=dyaw,
                      matches=[{"anchor": "j_east", "axis": "x", "at": 3.325, "seen": 3.2, "length": 1.0}])
    monkeypatch.setattr(rl, "correct_pose", fix)
    r.route = rl.Route(name="t", anchors={"j_east": rl.Anchor("x", 3.325, -3.46, 0.6)}, doors={}, steps=[])
    r.drive_velocity(rl.Step(do="drive", to=(2.0, -2.45), use=("j_east",), label="east"))
    rows = r.logged[-1]["rolling"]
    assert not rows[0].get("along_taken")                           # one frame: waits
    assert not rows[1]["taken"]                                     # agrees, refused for its 5° heading
    assert rows[2].get("along_taken")                               # the next agrees: taken
