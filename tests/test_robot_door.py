"""Doorways under velocity control (robot_lanes.DoorPass) through a simulated D8.

D8 as measured on 6 ต.ค. 2026 (every checkpoint scan of the day, gridded):
the room's south wall face at y -1.62, the corridor's north wall face at
-1.70, the opening x -2.80..-1.94, D8's own leaf standing open along x -1.96
from the frame to y -0.72 (the owner: "บานประตู D8 เปิดพิง"), ฝ่ายอาคาร's east
wall at x -1.825, the corridor's south wall at -3.42. The lidar is a raycast
over the real unit's arc (-103°..+93°); the base is the one measured the same
day — turn commands under 0.07 rad/s do nothing, above it 1.4× — with 300 ms
of command lag, and its odometry drifts. The robot's footprint is checked
against every wall at every tick: 0.60 m long, 0.78 m across the arms.
"""
import dataclasses
import json
import math
import time

import numpy as np
import pytest

from app import robot_lanes as rl

HALF_LEN, HALF_W = 0.30, rl.ROBOT_WIDTH / 2
D8_MIDDLE = (-2.38, -1.66)


#: D8's leaf is dark wood: the lidar gets a return from it only when its rays
#: meet it within this angle of square-on (beside it); at grazing angles from
#: the approach the beam is lost on it — no return, and nothing seen past it
#: either (6 ต.ค. 17:32 live scan: neither the leaf nor the wall behind it).
GLASS_MAX_INCIDENCE = math.radians(60)
GLASS = {6}                                 # index of the leaf in d8_world()


def d8_world(west=-2.80, east=-1.94, leaf_x=-1.96):
    return np.array([
        (-4.7, -1.62, west, -1.62), (east, -1.62, -1.0, -1.62),
        (-4.7, -1.70, west, -1.70), (east, -1.70, -1.0, -1.70),
        (west, -1.62, west, -1.70), (east, -1.62, east, -1.70),
        (leaf_x, -1.60, leaf_x, -0.72),
        (-1.825, -0.72, -1.825, 1.6),
        (-4.7, -3.42, -1.0, -3.42),
    ])


def raycast(pose, segs, rng, noise=0.003):
    x, y, yaw = pose
    a = np.radians(np.arange(-103.0, 93.0, 0.25))           # the unit gives ~0.22°
    dx, dy = np.cos(yaw + a)[:, None], np.sin(yaw + a)[:, None]
    x1, y1, x2, y2 = (segs[:, i][None, :] for i in range(4))
    ex, ey = x2 - x1, y2 - y1
    den = dx * ey - dy * ex
    with np.errstate(divide="ignore", invalid="ignore"):
        t = ((x1 - x) * ey - (y1 - y) * ex) / den
        u = ((x1 - x) * dy - (y1 - y) * dx) / den
    t = np.where((np.abs(den) > 1e-12) & (t > 0.05) & (u >= 0) & (u <= 1), t, np.inf)
    if GLASS:
        # |cos| of the angle between the ray and the segment's normal
        seg_len = np.hypot(ex, ey)
        cos_inc = np.abs(dx * ey - dy * ex) / np.where(seg_len > 0, seg_len, 1)
        glass = np.zeros(segs.shape[0], bool)
        glass[list(i for i in GLASS if i < segs.shape[0])] = True
        lost = glass[None, :] & (cos_inc < math.cos(GLASS_MAX_INCIDENCE)) & np.isfinite(t)
        first = np.argmin(t, axis=1)
        absorbed = lost[np.arange(t.shape[0]), first]       # the nearest thing on the ray swallows it
        t[absorbed, :] = np.inf
    d = t.min(axis=1)
    keep = np.isfinite(d) & (d < 12)
    d = d[keep] + rng.normal(0, noise, keep.sum())
    return np.c_[d * np.cos(a[keep]), d * np.sin(a[keep])]


def clearance(pose, segs):
    """Smallest gap between the robot's footprint and any wall (negative = overlap)."""
    x, y, yaw = pose
    t = np.linspace(0, 1, 400)[:, None]
    px = segs[:, 0] + t * (segs[:, 2] - segs[:, 0])
    py = segs[:, 1] + t * (segs[:, 3] - segs[:, 1])
    c, s = math.cos(yaw), math.sin(yaw)
    lx, ly = (px - x) * c + (py - y) * s, -(px - x) * s + (py - y) * c
    qx, qy = np.abs(lx) - HALF_LEN, np.abs(ly) - HALF_W
    out = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0)
    return float(out.min())


def run_door(start, heading, until, odom_err=(0.0, 0.0, 0.0), bias=0.0, world=None, seed=0,
             side_stop=0.008, breakaway=(0.07, 1.4), steps=1100, curve=0.0):
    """`curve`: a turn the base makes by itself (rad/s), seen by odometry too —
    the real one at a crawl on glossy tile (7 ต.ค. 11:47)."""
    world = d8_world() if world is None else world
    rng = np.random.default_rng(seed)
    tx, ty, tyaw = start
    ox, oy, oyaw = tx + odom_err[0], ty + odom_err[1], tyaw + odom_err[2]
    # the door as the route describes it: the way through as it really is,
    # jamb to leaf (0.84 m), not the drawing's 0.86 — with one edge in view
    # that width is what places the middle
    dp = rl.DoorPass(middle=D8_MIDDLE, heading=heading, until=until, width_hint=0.84)
    v = w = 0.0
    queue, prev = [(0.0, 0.0)] * 3, (0.0, 0.0)
    worst, stopped, done, laterals, yaw_dev = 9.0, None, False, [], 0.0
    for _ in range(steps):
        pts = raycast((tx, ty, tyaw), world, rng)
        cmd, info = dp.update((ox, oy, oyaw), pts, prev)
        sides = [g for g in info["sides"] if g is not None]
        if sides and min(sides) < side_stop:
            stopped = "side %.3f" % min(sides)
            break
        if dp.entered_yaw is not None and abs(rl.wrap_pi(info["yaw_est"] - dp.entered_yaw)) > math.radians(6):
            stopped = "heading"
            break
        if cmd.done:
            done = True
            break
        prev = (cmd.vx, cmd.omega)
        queue.append(prev)
        vc, wc = queue.pop(0)
        wa = 0.0 if abs(wc) < breakaway[0] else breakaway[1] * wc
        v += max(-0.05, min(0.05, vc - v))
        w += max(-0.3, min(0.3, wa - w))
        tyaw += (w + curve) * 0.1
        tx += v * math.cos(tyaw) * 0.1
        ty += v * math.sin(tyaw) * 0.1
        oyaw += (w + bias + curve) * 0.1
        ox += v * math.cos(oyaw) * 0.1
        oy += v * math.sin(oyaw) * 0.1
        worst = min(worst, clearance((tx, ty, tyaw), world))
        laterals.append(tx - D8_MIDDLE[0])
        yaw_dev = max(yaw_dev, abs(rl.wrap_pi(tyaw - heading)))
    return {"end": (tx, ty, tyaw), "clearance": worst, "stopped": stopped, "done": done, "laterals": laterals,
            "yaw_dev": yaw_dev}


SOUTH, NORTH = -math.pi / 2, math.pi / 2
START_ROOM = 0.25          # the door step starts here: ~1 m before the leaf, the opening 1.87 m ahead
#: The leaf-to-jamb opening is 0.84 m and the robot 0.78 m across the arms
#: (the owner's figure, padded 3-5 cm): 3 cm a side is the whole margin, so
#: "clear" here means at least 5 mm left of it with the padded width.
CLEAR = 0.005


def test_out_through_d8_with_odometry_two_centimetres_wrong():
    # Odometry thinks it is on the middle line; it is really 2 cm toward the leaf.
    r = run_door((-2.36, START_ROOM, SOUTH), SOUTH, until=-2.56, odom_err=(-0.02, 0.0, 0.0))
    assert r["done"] and r["stopped"] is None
    assert r["clearance"] > CLEAR
    assert r["end"][1] == pytest.approx(-2.56, abs=0.04)


def test_three_centimetres_toward_the_glass_leaf_and_angled_at_it_stops_short_of_it():
    # The leaf shows up only once the robot is beside it, too late to undo
    # 3 cm and 1° toward it (8 seeds: stopped every time, 8 mm from it): the
    # answer is the side stop, not contact. Lined up by the approach lane the
    # real robot stood 0.0 cm off the middle (6 ต.ค. 17:32).
    r = run_door((-2.35, START_ROOM, SOUTH + math.radians(1.0)), SOUTH, until=-2.56, odom_err=(-0.03, 0.0, 0.0))
    assert r["clearance"] > 0.0                                 # never touches
    # through, stopped by the side check, or held short by the ahead-strip
    # (as wide as the body + 1 cm since 7 ต.ค.: the leaf comes into it first)
    assert r["done"] or (r["stopped"] or "").startswith("side") or r["clearance"] > 0.02


def test_three_centimetres_away_from_the_leaf_goes_through():
    r = run_door((-2.41, START_ROOM, SOUTH - math.radians(1.0)), SOUTH, until=-2.56, odom_err=(0.03, 0.0, 0.0))
    assert r["done"] and r["clearance"] > CLEAR


@pytest.mark.parametrize("bias", [0.003, -0.003])
def test_odometry_drifting_while_it_drives_through(bias):
    # 0.003 rad/s at 0.06 m/s is ~3° of heading drift a metre, if trusted —
    # three times what the lanes of 6 ต.ค. showed (< 1° a metre).
    r = run_door((-2.38, START_ROOM, SOUTH), SOUTH, until=-2.56, bias=bias)
    assert r["clearance"] > CLEAR
    assert r["done"], r["stopped"]


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_back_in_through_d8_from_the_corridor(seed):
    r = run_door((-2.38, -2.56, NORTH), NORTH, until=START_ROOM, odom_err=(0.02, 0.0, 0.0), bias=0.003, seed=seed)
    assert r["done"] and r["stopped"] is None
    assert r["clearance"] > CLEAR


@pytest.mark.parametrize("curve", [0.007, -0.007])
def test_a_base_that_curves_by_itself_and_ignores_light_pulses_is_still_held_straight(curve):
    # 7 ต.ค. 11:47: crawling into D8 from the corridor the base turned left
    # ~0.4°/s (0.007 rad/s) through 37 right pulses of 0.08 — at a crawl the
    # casters did not break away at 0.08. Pulses the base ignores get stronger.
    r = run_door((-2.38, -2.56, NORTH), NORTH, until=START_ROOM, breakaway=(0.09, 1.4), curve=curve)
    assert r["done"] and r["stopped"] is None
    assert math.degrees(r["yaw_dev"]) < 6.0
    assert r["clearance"] > CLEAR


def test_the_lidar_not_odometry_decides_how_close_it_runs_to_the_leaf(monkeypatch):
    # The same start, but the door logic blind to the lidar's lateral: on
    # odometry 3 cm out it runs into the leaf/jamb or stops on the side check.
    # At the speeds this was written for (0.08 / 0.15). At 0.15 / 0.30 the blind
    # pass through this 3-cm-a-side opening (the padded 0.78 body) once scraped
    # by at 1.2 cm -- luck, not information; with the lidar the pass behaves the
    # same at 0.08, 0.12 and 0.15 here (all stop on the 8 mm side check).
    import dataclasses
    import unittest.mock as mock
    slow = dataclasses.replace(rl.DOOR_LIMITS, v_max=0.08, front_v=0.08)
    fast = dataclasses.replace(rl.DOOR_FAST_LIMITS, v_max=0.15, front_v=0.15)
    real = rl.DoorPass
    monkeypatch.setattr(rl, "DoorPass", lambda *a, **k: real(*a, **dict(k, limits=slow, fast_limits=fast)))
    with mock.patch.object(rl, "door_lateral", lambda *a, **k: None):
        r = run_door((-2.35, START_ROOM, SOUTH), SOUTH, until=-2.56, odom_err=(-0.03, 0.0, 0.0))
    # it does not get through cleanly: it touches, is stopped, or is held short
    assert not (r["done"] and r["clearance"] > CLEAR)


def test_door_lateral_reads_one_side_by_the_measured_margin():
    # 0.84 m opening, 0.78 m robot: 3 cm a side. Leaf on the left 1 cm away
    # = 2 cm too far left.
    assert rl.door_lateral(None, (0.01, None), width=0.84, robot_lat=0.02) == pytest.approx(0.02)
    assert rl.door_lateral(None, (None, 0.05), width=0.84, robot_lat=0.02) == pytest.approx(0.02)
    assert rl.door_lateral(None, (0.02, 0.04), width=0.84, robot_lat=0.0) == pytest.approx(0.01)
    assert rl.door_lateral({"centre": 0.03, "face": 1.0, "width": 0.84}, (None, None)) == pytest.approx(-0.03)
    assert rl.door_lateral(None, (None, None), width=0.84, robot_lat=0.0) is None


def test_a_wall_beside_the_robot_is_not_taken_for_the_doors_edge():
    # Centred on D8's line, ฝ่ายอาคาร's east wall is 14 cm from the arm; the
    # opening's edge would be 3 cm. Read as the edge it says "11 cm right".
    gap = {"centre": 0.0, "face": 1.5, "width": 0.84}
    assert rl.door_lateral(gap, (0.145, None), width=0.84, robot_lat=0.0) == pytest.approx(0.0)


@pytest.mark.parametrize("turning,expect", [(+0.12, 0.0), (-0.12, -1)])
def test_a_pulse_the_other_way_waits_for_the_turn_under_way_to_stop(turning, expect):
    # reviewer: the approach hands over to the pulses mid-turn, `since_pulse`
    # preset -- the first pulse could reverse the turn with no zero between
    # (each reversal swivels the casters round, the scraping the owner heard)
    dp = rl.DoorPass(middle=D8_MIDDLE, heading=SOUTH, until=-2.56, width=0.84, corr=0.0)
    pose = (-2.38 + 0.03, -0.30, SOUTH)                 # 3 cm left of the middle: wants to turn right (-)
    pts = raycast(pose, d8_world(), np.random.default_rng(2), noise=0.0)
    cmd, info = dp.update(pose, pts, prev=(0.10, turning))
    assert not info.get("approach") and cmd.vx > 0
    if expect == 0.0:
        assert cmd.omega == 0.0
    else:
        assert cmd.omega < 0.0


def test_the_approach_turns_for_where_the_heading_will_be_not_where_it_is(monkeypatch):
    # reviewer, then the simulation: with odometry 4° out, the approach's heading
    # hold swung odometry from -2° to +7.3° off the way through -- the runner
    # stops a pass at 8. Turning for the error left once the turn under way has
    # landed (~0.3 s), it peaks at 6.
    class Peak(_SimBase):
        peak = 0.0

        def call(self, command, args=None, timeout=None):
            if command == "setvelocity":
                Peak.peak = max(Peak.peak, abs(rl.wrap_pi(self.odom[2] - SOUTH)))
            return super().call(command, args, timeout)
    worst = 0.0
    for off, yerr in ((-0.04, 4.0), (0.04, -4.0)):
        Peak.peak = 0.0
        sim = Peak((-2.38 + off, START_ROOM, SOUTH), odom_err=(0.0, 0.0, math.radians(yerr)), seed=0)
        dr, r = _door_runner(sim, monkeypatch)
        monkeypatch.setattr(dr, "DOOR_STEER_IN_RATE", 100.0)        # curve in, whatever the gate says
        r.door_width = r.turn_width = 0.70
        r.measured_at = {"x": (sim.odom[0], sim.odom[1])}
        r.door_velocity(rl.Step(do="door", door="D8", heading_deg=-90, until=-2.56, label="D8 out"))
        assert [e["kind"] for e in r.logged if e.get("kind", "").startswith("door_st")] == ["door_steer_in"]
        assert r.logged[-1]["ok"]
        worst = max(worst, Peak.peak)
    assert math.degrees(worst) < 6.5


def test_held_in_a_doorway_it_does_not_turn_on_the_spot():
    dp = rl.DoorPass(middle=D8_MIDDLE, heading=SOUTH, until=-2.56, width=0.84, corr=0.0)
    rng = np.random.default_rng(1)
    pose = (-2.36, -0.60, SOUTH + math.radians(3))
    pts = raycast(pose, d8_world(), rng)
    pts = np.vstack([pts, [[0.33, 0.0], [0.33, 0.02], [0.34, -0.02]]])     # someone 0.33 m ahead
    cmd, info = dp.update(pose, pts, prev=(0.0, 0.0))
    assert cmd.vx == 0.0 and cmd.omega == 0.0


# -- the runner's own loop (scripts/drive_route.py Runner.door_velocity) ------

class _SimBase:
    """The simulated D8 behind a fake SDK: each setvelocity is 0.1 s of motion."""
    def __init__(self, true_pose, odom_err=(0.0, 0.0, 0.0), seed=0):
        self.true = list(true_pose)
        self.odom = [true_pose[i] + odom_err[i] for i in range(3)]
        self.v = self.w = 0.0
        self.queue = [(0.0, 0.0)] * 3
        self.world, self.rng, self.worst = d8_world(), np.random.default_rng(seed), 9.0

    def call(self, command, args=None, timeout=None):
        if command == "velocitycontrol":
            return {"code": 1, "id": 9}
        if command == "getpose":
            return {"x": self.odom[0], "y": self.odom[1], "yaw": self.odom[2]}
        if command == "getcurrentaction":
            return {"id": 0}
        if command == "setvelocity":
            self.queue.append((args["vx"], args["omega"]))
            vc, wc = self.queue.pop(0)
            wa = 0.0 if abs(wc) < 0.07 else 1.4 * wc
            self.v += max(-0.05, min(0.05, vc - self.v))
            self.w += max(-0.3, min(0.3, wa - self.w))
            for st in (self.true, self.odom):
                st[2] += self.w * 0.1
                st[0] += self.v * math.cos(st[2]) * 0.1
                st[1] += self.v * math.sin(st[2]) * 0.1
            self.worst = min(self.worst, clearance(tuple(self.true), self.world))
        return {"code": 0, "codestr": "done"}

    def points(self):
        return raycast(tuple(self.true), self.world, self.rng)


def _door_runner(sim, monkeypatch):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "scripts" / "drive_route.py"
    spec = importlib.util.spec_from_file_location("drive_route_door_test", path)
    dr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dr)
    monkeypatch.setattr(dr.time, "sleep", lambda s: None)

    class Watcher:
        def start(self): pass
        def stop(self): pass
        def age(self): return 0.05
        def points(self): return sim.points()
        def front(self): return rl.front_distance(sim.points())

    class Board:
        def scan(self): return sim.points()
        def events(self): return []
        def pose(self): return tuple(sim.odom)

    r = dr.Runner.__new__(dr.Runner)
    r.b, r.sdk, r.fences = Board(), sim, []
    r.route = rl.Route(name="d8", anchors={}, doors={"D8": rl.Door("y", -1.633, -2.80, -1.96)}, steps=[])
    r.limits, r.door_min_margin, r.door_width = rl.DriveLimits(v_max=0.2, w_max=0.4), 0.025, rl.ROBOT_WIDTH
    r.turn_width = rl.ROBOT_WIDTH
    r.say = lambda m: None
    r.logged = []
    r.log = lambda **e: r.logged.append(e)
    r.watcher = Watcher
    return dr, r


def test_the_runner_takes_the_robot_through_d8_on_the_simulated_base(monkeypatch):
    sim = _SimBase((-2.36, START_ROOM, SOUTH), odom_err=(-0.02, 0.0, 0.0))
    dr, r = _door_runner(sim, monkeypatch)
    r.door_velocity(rl.Step(do="door", door="D8", heading_deg=-90, until=-2.56, label="D8 out"))
    door = r.logged[-1]
    assert door["ok"] and door["reason"] is None
    assert sim.worst > CLEAR
    assert sim.true[1] == pytest.approx(-2.56, abs=0.05)


class _StickySim(_SimBase):
    """A floor where the base will not creep -- 7 ต.ค. 15:50 past D8: 0.061 sent,
    6 mm/s moved. Here a strip of the room before D8 where anything under
    0.085 moves it 3% of that (no coasting through it)."""
    def call(self, command, args=None, timeout=None):
        if command == "setvelocity" and 0.0 < self.true[1] < 0.22 and 0 < args["vx"] < 0.085:
            args = dict(args, vx=args["vx"] * 0.03)
            self.v = min(self.v, args["vx"])
        return super().call(command, args, timeout)


@pytest.mark.parametrize("open_v,kicked", [(0.07, True), (0.25, False)])
def test_a_base_that_will_not_creep_is_given_a_little_more_and_only_at_the_creep(monkeypatch, open_v, kicked):
    # the open floor at near the creep speed (0.07) stalls on the sticky strip:
    # kicked through. At the fast 0.25 the strip does not hold it, and the
    # kick is never for the fast speed anyway (reviewer: held at 0.25,
    # something the lidar may not see is holding it)
    sim = _StickySim((-2.36, START_ROOM, SOUTH))
    dr, r = _door_runner(sim, monkeypatch)
    real = dr.rl.DoorPass
    fast = dataclasses.replace(rl.DOOR_FAST_LIMITS, v_max=open_v, front_v=open_v)
    monkeypatch.setattr(dr.rl, "DoorPass", lambda *a, **k: real(*a, fast_limits=fast, **k))
    r.door_velocity(rl.Step(do="door", door="D8", heading_deg=-90, until=-2.56, label="D8 out"))
    door = r.logged[-1]
    assert door["ok"] and sim.worst > CLEAR
    assert (max(t[14] for t in door["trace"]) > 0) == kicked     # the kick column


@pytest.mark.parametrize("start,heading,until", [((-2.38, START_ROOM), SOUTH, -2.56), ((-2.38, -2.45), NORTH, START_ROOM)])
def test_a_door_pass_takes_under_20_s_and_keeps_its_clearance(monkeypatch, start, heading, until):
    # owner 7 ต.ค. 16:45: "เหลือแค่ที่ประตูถ้าไวกว่านี้ก็โอเค" -- 32-38 s a pass on the
    # robot, the simulated D8 32 s at 0.08 / 0.15; 18-19 s now, clearance unchanged
    sim = _SimBase((start[0], start[1], heading))
    dr, r = _door_runner(sim, monkeypatch)
    r.door_width = r.turn_width = 0.70
    r.door_velocity(rl.Step(do="door", door="D8", heading_deg=round(math.degrees(heading)), until=until,
                            label="D8"))
    door = r.logged[-1]
    assert door["ok"] and sim.worst + 0.04 > 0.045                  # the 0.70 body, as the 0.78 box measures
    assert len([t for t in door["trace"] if isinstance(t, (list, tuple))]) * 0.1 < 20.0


def test_the_kick_grows_only_while_stalled_and_never_in_the_frame(monkeypatch):
    # 15:50: 0.061 sent tick after tick, 6 mm/s moved
    dr, _ = _door_runner(_SimBase((-2.36, START_ROOM, SOUTH)), monkeypatch)
    clear = {"slow": 0, "front": None}

    def run(ticks, step, info=clear, vx=0.061, hist=None, kick=0.0, wait=0):
        hist = [] if hist is None else hist
        y, kicks = 0.0, []
        for _ in range(ticks):
            y -= step
            kick, wait = dr.Runner._door_kick(hist, kick, wait, (0.0, y), (0.0, 0.0), SOUTH, vx + kick, vx, info)
            kicks.append(kick)
        return kicks

    stalled = run(35, 0.0006)
    assert stalled[9] == 0.0 and stalled[10] == pytest.approx(0.01) and stalled[22] == pytest.approx(0.02)
    assert max(stalled) == pytest.approx(dr.DOOR_KICK_MAX)
    assert max(run(35, 0.006)) == 0.0                                  # 6 cm/s: moving, no kick
    assert max(run(35, 0.0006, info={"slow": 1, "front": None})) == 0.0   # in the frame
    assert max(run(35, 0.0006, info={"slow": 0, "front": 0.6})) == 0.0    # something near ahead
    assert max(run(35, 0.0006, vx=0.03)) == 0.0                         # braking to a stop
    assert max(run(35, 0.0006, vx=0.25)) == 0.0                         # held at the fast speed: not ours to push


def _person_in_the_doorway(sim, looks):
    """15:32 and 16:03: no opening from the aim, found a minute later."""
    person = np.c_[np.full(20, 1.85), np.linspace(-0.2, 0.2, 20)]     # robot frame, in D8's opening

    def scan():
        looks.append(1)
        p = sim.points()
        return np.vstack([p, person]) if len(looks) <= 3 else p
    return scan


def test_a_doorway_with_someone_in_it_is_looked_at_again_until_it_clears(monkeypatch):
    sim = _SimBase((-2.36, START_ROOM, SOUTH))
    dr, r = _door_runner(sim, monkeypatch)
    looks = []
    r.b.scan = _person_in_the_doorway(sim, looks)
    r.door_velocity(rl.Step(do="door", door="D8", heading_deg=-90, until=-2.56, label="D8 out"))
    assert len(looks) > 3 and r.logged[-1]["kind"] == "door" and r.logged[-1]["ok"]


def test_a_doorway_that_stays_shut_stops_and_leaves_what_it_saw(monkeypatch):
    sim = _SimBase((-2.36, START_ROOM, SOUTH))
    dr, r = _door_runner(sim, monkeypatch)
    monkeypatch.setattr(dr, "DOOR_FIND_WAIT_S", 0.2)
    looks = []
    scan = _person_in_the_doorway(sim, looks)
    r.b.scan = lambda: (looks.clear(), scan())[1]                    # someone there every look
    with pytest.raises(dr.Stop, match="no opening found"):
        r.door_velocity(rl.Step(do="door", door="D8", heading_deg=-90, until=-2.56, label="D8 out"))
    seen = [e for e in r.logged if e.get("kind") == "door_not_found"]
    assert len(seen) == 1 and len(seen[0]["points"]) > 20
    assert sim.true[1] == START_ROOM                                   # never moved


@pytest.mark.parametrize("remember", [True, False])
def test_the_way_back_through_a_door_uses_where_the_way_in_found_its_middle(monkeypatch, remember):
    # 7 ต.ค., D11 out, three runs: centred on the jambs on the way in 4-4.5 cm off
    # the route's middle, turned round, and the lane said "4.5 cm off, step
    # sideways" every time. Here D8 in from the corridor with odometry 5 cm out
    # across the door, turned round in the room, then D8 out.
    sim = _SimBase((-2.38, -2.45, NORTH), odom_err=(0.05, 0.0, 0.0))
    dr, r = _door_runner(sim, monkeypatch)
    r.door_width = r.turn_width = 0.70
    r.measured_at = {"x": (-9.0, -9.0)}                          # long ago: no checkpoint in between
    r.door_velocity(rl.Step(do="door", door="D8", heading_deg=90, until=START_ROOM, label="D8 in"))
    assert r.logged[-1]["kind"] == "door" and r.logged[-1]["ok"]
    if not remember:
        r.door_seen = {}
    for st in (sim.true, sim.odom):
        st[2] = SOUTH                                            # turned round where it stands
    r.logged.clear()
    r.door_velocity(rl.Step(do="door", door="D8", heading_deg=-90, until=-2.56, label="D8 out"))
    steps = [e for e in r.logged if e.get("kind") in ("door_step_across", "door_steer_in")]
    assert (len(steps) == 0) == remember                         # without it: a step, or a curve onto the line
    assert r.logged[-1]["kind"] == "door" and r.logged[-1]["ok"] and sim.worst > CLEAR


def test_the_runner_will_not_go_in_off_the_line(monkeypatch):
    # 9 cm off the opening's middle with ฝ่ายอาคาร's wall 7.5 cm from the arm:
    # no room to turn and step sideways, so it stops before anything moves
    sim = _SimBase((-2.29, START_ROOM, SOUTH))
    dr, r = _door_runner(sim, monkeypatch)
    with pytest.raises(dr.Stop, match="off the opening's middle, and no room"):
        r.door_velocity(rl.Step(do="door", door="D8", heading_deg=-90, until=-2.56, label="D8 out"))
    assert sim.true[1] == START_ROOM and sim.true[0] == -2.29     # never moved


def test_off_the_line_in_front_of_a_door_it_steps_sideways_and_goes_through(monkeypatch):
    # 7 ต.ค. 10:49: the corridor ended 20.5 cm off D11's line with odometry
    # sure it was on it; the runner stopped and I stepped it across by hand.
    # Here: D8 from the corridor, 14 cm east of the frame's middle, odometry
    # saying "on the line".
    sim = _SimBase((-2.23, -2.56, NORTH), odom_err=(-0.15, 0.0, 0.0))
    dr, r = _door_runner(sim, monkeypatch)
    r.door_velocity(rl.Step(do="door", door="D8", heading_deg=90, until=START_ROOM, label="D8 in"))
    across = [e for e in r.logged if e.get("kind") == "door_step_across"]
    assert len(across) == 1 and across[0]["off"] == pytest.approx(-0.14, abs=0.03)
    door = r.logged[-1]
    assert door["kind"] == "door" and door["ok"], door.get("reason")
    assert sim.worst > CLEAR                                # turns, step and pass: nothing touched
    assert sim.true[1] == pytest.approx(START_ROOM, abs=0.05)


def test_after_a_step_sideways_one_edge_that_agrees_with_the_door_is_believed(monkeypatch):
    # After the step odometry is 14 cm off the route's door by the step itself.
    # A one-edge reading that puts the robot on the door's middle must be read
    # against where the two edges put the door, not against the route — or
    # it is "trusting the lane" and stepping straight back.
    sim = _SimBase((-2.23, -2.56, NORTH), odom_err=(-0.15, 0.0, 0.0))
    dr, r = _door_runner(sim, monkeypatch)
    aims = iter([{"face": 0.86, "lo": -0.29, "hi": 0.57, "width": 0.86, "centre": 0.14, "edges": 2},
                 {"face": 0.86, "lo": -0.43, "hi": 0.43, "width": 0.86, "centre": 0.005, "edges": 1}])
    real = rl.opening_ahead
    monkeypatch.setattr(rl, "opening_ahead", lambda *a, **k: next(aims, None) or real(*a, **k))
    steps = []

    def step_across(step, heading, off, face=None):
        steps.append(off)
        for st in (sim.true, sim.odom):
            st[0] -= off * -math.sin(heading)

    r.step_across = step_across
    r.door_velocity(rl.Step(do="door", door="D8", heading_deg=90, until=START_ROOM, label="D8 in"))
    assert steps == [pytest.approx(-0.14)]                  # one step, not a second one back
    assert r.logged[-1]["ok"]


class _Decided(Exception):
    pass


def _decide(monkeypatch, measured_at, aims):
    """Run door_velocity up to the DoorPass it builds, D8 from the corridor with
    odometry on the line, `aims` the opening readings in turn."""
    sim = _SimBase((-2.38, -2.56, NORTH))
    dr, r = _door_runner(sim, monkeypatch)
    r.measured_at = measured_at
    it = iter(aims)
    monkeypatch.setattr(rl, "opening_ahead", lambda *a, **k: next(it))
    steps = []

    def step_across(step, heading, off, face=None):
        steps.append(off)
        for st in (sim.true, sim.odom):
            st[0] -= off * -math.sin(heading)

    r.step_across = step_across

    def built(**kw):
        raise _Decided(kw)

    monkeypatch.setattr(dr.rl, "DoorPass", built)
    with pytest.raises(_Decided) as got:
        r.door_velocity(rl.Step(do="door", door="D8", heading_deg=90, until=START_ROOM, label="D8 in"))
    return steps, got.value.args[0], r


ONE_EDGE_22 = {"face": 0.88, "lo": -0.22, "hi": 0.62, "width": 0.84, "centre": 0.20, "edges": 1}
ONE_EDGE_0 = {"face": 0.88, "lo": -0.415, "hi": 0.425, "width": 0.84, "centre": 0.005, "edges": 1}


def test_one_edge_far_from_a_lane_measured_long_ago_overrules_it(monkeypatch):
    # 7 ต.ค. 13:07, D11: the south jamb said 22 cm off, odometry's y was last
    # measured 2.5 m and two turns back; the lane won and the robot crawled at
    # the north jamb. Now the edge wins: step across, then go in from there.
    steps, kw, r = _decide(monkeypatch, {"x": (2.0, -2.6)}, [ONE_EDGE_22, ONE_EDGE_0])
    assert steps == [pytest.approx(-0.20)]
    assert kw["corr"] == pytest.approx(0.20, abs=0.01)       # the pass starts from where the edge put the door
    assert any(e.get("kind") == "door_edge_overrules_lane" for e in r.logged)


def test_one_edge_against_a_lane_just_measured_keeps_the_lane(monkeypatch):
    # D8 from the room: odometry 1.2 m from two long walls, the one edge past
    # the leaf wobbling with the standpoint (6.6 cm, 7 ต.ค. 10:44) — the lane.
    steps, kw, r = _decide(monkeypatch, {"x": (-2.38, -1.80)}, [ONE_EDGE_22])
    assert steps == [] and kw["corr"] is None
    assert not any(e.get("kind") == "door_edge_overrules_lane" for e in r.logged)


def test_a_small_disagreement_keeps_the_lane_even_long_after_it_was_measured(monkeypatch):
    # within what a one-edge reading has been seen to wobble: no overruling
    small = dict(ONE_EDGE_22, centre=0.06, lo=-0.36, hi=0.48)
    steps, kw, r = _decide(monkeypatch, {}, [small])
    assert steps == [] and kw["corr"] is None


def test_one_edge_close_to_a_fresh_lane_does_not_step_the_robot_for_its_wobble(monkeypatch):
    # 8 ต.ค. 09:23, D8 from the room at 1.94 m: one edge -0.038 with the lane
    # (walls measured on the move 0.4 m back) at -0.01 -- the code went by the
    # edge whenever they agreed within 3 cm, stepped 3.8 cm, read +0.044 and
    # stepped back: ~25 s for nothing
    wobble = dict(ONE_EDGE_0, centre=0.038, lo=-0.382, hi=0.458)
    steps, kw, r = _decide(monkeypatch, {"x": (-2.38, -2.20)}, [wobble])
    assert steps == []


def test_one_far_edge_within_its_wobble_does_not_overrule_a_stale_lane(monkeypatch):
    # 8 ต.ค. 09:30, D11 from 1.5 m: +0.087 against the frame's +0.027 -- 11 cm of
    # wobble; past the old 8 cm overrule it stepped twice and stopped
    wobble = dict(ONE_EDGE_0, centre=-0.11, lo=-0.53, hi=0.31)
    steps, kw, r = _decide(monkeypatch, {}, [wobble])
    assert steps == []
    assert not any(e.get("kind") == "door_edge_overrules_lane" for e in r.logged)


def test_in_a_doorway_the_slow_zone_never_drops_under_the_speed_the_base_goes_straight_at():
    # 7 ต.ค.: commanded 0.060 the base went 0.043 m/s, straight; ~0.05 (the
    # slow zone's creep floor, something 0.4-0.8 m ahead) it went 0.015-0.017
    # and curved left 0.34-0.65°/s. Moving in a door = 0.06; stopping = 0.
    L = rl.DOOR_LIMITS
    prev = (L.v_max, 0.0)
    for front in (0.75, 0.6, 0.45, 0.41):
        cmd = rl.lane_command((0.0, 0.0, 0.0), (-3.0, 0.0), (3.0, 0.0), prev, 0.1, front, L)
        assert 0.06 - 1e-9 <= cmd.vx <= L.v_max + 1e-9, front
        prev = (cmd.vx, cmd.omega)
    held = rl.lane_command((0.0, 0.0, 0.0), (-3.0, 0.0), (3.0, 0.0), (0.0, 0.0), 0.1, 0.39, L)
    assert held.vx == 0.0


def _settle(dp, pose, pts, ticks=30):
    prev = (0.0, 0.0)
    for _ in range(ticks):
        cmd, info = dp.update(pose, pts, prev)
        prev = (cmd.vx, 0.0)
    return cmd, info


def test_a_door_pass_is_quick_in_the_open_and_slow_only_at_the_frame_or_beside_something():
    # owner 7 ต.ค.: "อยากให้ตอนผ่านประตูไวกว่าเดิม" — 0.06 all the way took 53-70 s
    empty = np.zeros((0, 2))
    dp = rl.DoorPass(middle=(0.0, 0.0), heading=NORTH, until=2.0, width_hint=0.84)
    cmd, info = _settle(dp, (0.0, -1.5, NORTH), empty)                       # 1.5 m before the wall line
    assert not info["slow"] and cmd.vx == pytest.approx(rl.DOOR_FAST_LIMITS.v_max)
    cmd, info = _settle(rl.DoorPass(middle=(0.0, 0.0), heading=NORTH, until=2.0, width_hint=0.84),
                        (0.0, -0.5, NORTH), empty)                           # front 0.2 m from it
    assert info["slow"] and cmd.vx <= rl.DOOR_LIMITS.v_max + 1e-9
    cmd, info = _settle(rl.DoorPass(middle=(0.0, 0.0), heading=NORTH, until=2.0, width_hint=0.84),
                        (0.0, 0.3, NORTH), empty)                            # back still in the frame
    assert info["slow"]
    cmd, info = _settle(rl.DoorPass(middle=(0.0, 0.0), heading=NORTH, until=2.0, width_hint=0.84),
                        (0.0, 0.8, NORTH), empty)                            # through and clear
    assert not info["slow"]
    leaf = np.c_[np.linspace(-0.3, 0.3, 40), np.full(40, -(0.35 + 0.10))]   # 10 cm beside, far from the wall
    cmd, info = _settle(rl.DoorPass(middle=(0.0, 0.0), heading=NORTH, until=2.0, width_hint=0.84),
                        (0.0, -1.5, NORTH), leaf)
    assert info["slow"] and cmd.vx <= rl.DOOR_LIMITS.v_max + 1e-9


def _d11_front_frames():
    import json
    from pathlib import Path
    doc = json.loads((Path(__file__).parent / "data" / "robot" / "scan_d11_front_step_20261007.json").read_text())
    out = []
    for fr in doc["frames"]:
        x, y, yaw = fr["pose"]
        pts = rl.scan_points(fr)
        c, s = math.cos(yaw), math.sin(yaw)                  # into the way through (heading 0)
        out.append(((x, y, yaw), np.c_[pts[:, 0] * c - pts[:, 1] * s, pts[:, 0] * s + pts[:, 1] * c]))
    return out


def test_d11s_frame_middle_moves_with_the_robot_and_the_jamb_guess_is_not_trusted_for_it():
    # 7 ต.ค. 13:19/13:20: both jambs seen 0.87 m apart against a 0.76 drawing.
    # The frame's middle is a measurement: it moved by the 6.3 cm the robot
    # stepped. The one-edge guess assumes the robot came lined up; it put the
    # robot 6.7 cm off when it was ~19 off (the live scan at the frame 13:21).
    (p0, a), (p1, b) = _d11_front_frames()
    hint = 0.76
    g0 = rl.opening_ahead(a, 3.375 - p0[0], hint)
    g1 = rl.opening_ahead(b, 3.375 - p1[0], hint)
    assert g0["edges"] == 1 and g1["edges"] == 1
    assert g0["frame_width"] == pytest.approx(0.87, abs=0.03)
    stepped = p0[1] - p1[1]                                   # 0.063 m south = to the robot's right
    assert (g1["frame_centre"] - g0["frame_centre"]) == pytest.approx(stepped, abs=0.015)
    assert abs(g0["frame_centre"]) > rl.ONE_EDGE_TRUST_M + abs(g0["centre"] - g0["frame_centre"]) - 0.1
    assert abs(g0["centre"] - g0["frame_centre"]) > 0.04    # the guess is not the frame's middle


def _passage_scene(leaf_in=0.12, leaf_lat=0.30, robot_lat=0.0):
    """A 0.86 m frame 0.85 m ahead, a leaf standing open inside its left jamb."""
    ys = np.arange(-1.2, 1.2, 0.004)
    wall = np.c_[np.full(ys.size, 0.85), ys][(ys < -0.43 - robot_lat) | (ys > 0.43 - robot_lat)]
    xs = np.arange(0.85 + leaf_in, 1.8, 0.004)
    leaf = np.c_[xs, np.full(xs.size, leaf_lat - robot_lat)]
    rx = np.arange(0.85, 1.01, 0.004)                         # the jamb's own depth, 3 mm inside its edge
    reveal = np.c_[rx, np.full(rx.size, -0.43 - robot_lat + 0.003)]
    return np.vstack([wall, leaf, reveal])


def test_a_leaf_inside_the_frame_makes_the_passage_two_measured_edges():
    g = rl.opening_ahead(_passage_scene(robot_lat=0.15), 0.85, 0.76)
    assert g["edges"] == 2 and g.get("passage")
    assert g["lo"] == pytest.approx(-0.43 - 0.15, abs=0.01) and g["hi"] == pytest.approx(0.30 - 0.15, abs=0.01)
    assert g["centre"] == pytest.approx((-0.43 + 0.30) / 2 - 0.15, abs=0.01)


def test_a_jambs_own_reveal_is_not_a_narrowing():
    # 0.86 frame against a 0.80 drawing: just outside the two-edge tolerance,
    # inside the passage window -- 3 mm of reveal must not make it a passage
    pts = _passage_scene()
    pts = pts[np.abs(pts[:, 1] - 0.30) > 1e-6]                  # no leaf
    g = rl.opening_ahead(pts, 0.85, 0.80)
    assert g["edges"] == 1 and not g.get("passage") and "frame_centre" in g


def test_a_frame_narrower_than_the_passage_is_someone_in_it_not_a_frame():
    # 15:12, D11: "both jambs seen, 0.483 m apart" -- the next look said 0.867
    face = 0.85
    ys = np.arange(-1.2, 1.2, 0.004)
    wall = np.c_[np.full(ys.size, face), ys][(ys < -0.40) | (ys > 0.08)]      # 0.48 m, right edge near the line
    g = rl.opening_ahead(wall, face, 0.76)
    assert g is not None and g["edges"] == 1                       # the guess is still offered
    assert "frame_centre" not in g                                 # but not as a frame to aim by


def test_the_wall_behind_d8s_glass_leaf_is_not_taken_for_a_jamb():
    # From the room the second "edge" is the wall past the leaf's end, ~1.2 m
    # from the west jamb: its middle is 8-23 cm toward the leaf. Not a frame.
    rng = np.random.default_rng(0)
    for x0 in (-2.30, -2.36, -2.40):
        g = rl.opening_ahead(raycast((x0, START_ROOM, SOUTH), d8_world(), rng), START_ROOM + 1.633, 0.84)
        assert g is not None and "frame_centre" not in g


def test_with_a_stale_lane_the_frames_middle_decides_not_the_jamb_guess(monkeypatch):
    # The 13:19 numbers: the guess said +0.067 off, the frame's middle +0.14.
    guess = {"face": 0.84, "lo": -0.313, "hi": 0.447, "width": 0.76, "centre": 0.067, "edges": 1,
             "frame_centre": 0.14, "frame_width": 0.87}
    after = dict(guess, centre=0.0, lo=-0.38, hi=0.38, frame_centre=0.004)
    steps, kw, r = _decide(monkeypatch, {}, [dict(guess, width=0.84, lo=-0.353, hi=0.487),
                                             dict(after, width=0.84, lo=-0.42, hi=0.42)])
    assert steps == [pytest.approx(-0.14)]
    assert any(e.get("kind") == "door_frame_overrules_lane" for e in r.logged)


def test_going_in_the_near_side_keeps_room_to_spare_not_just_under_six_centimetres(monkeypatch):
    # 7 ต.ค. 13:55, D8 in: both edges 0.852 m, the 0.70 base 5.7 cm off -- under
    # the old 6 cm, so straight in with 1.9 cm on the near side; the side stop
    # held it 5 mm from the west jamb inside the frame. Now: step across first.
    sim = _SimBase((-2.38, -2.56, NORTH))
    dr, r = _door_runner(sim, monkeypatch)
    r.door_width, r.measured_at = 0.70, {"x": (-2.38, -2.56)}
    reading = {"face": 0.76, "lo": -0.369, "hi": 0.483, "width": 0.852, "centre": 0.057, "edges": 2}
    after = dict(reading, lo=-0.426, hi=0.426, centre=0.0)
    it = iter([reading, after])
    monkeypatch.setattr(rl, "opening_ahead", lambda *a, **k: next(it))
    steps = []
    r.step_across = lambda step, heading, off, face=None: steps.append(off)

    def built(**kw):
        raise _Decided(kw)

    monkeypatch.setattr(dr.rl, "DoorPass", built)
    with pytest.raises(_Decided):
        r.door_velocity(rl.Step(do="door", door="D8", heading_deg=90, until=START_ROOM, label="D8 in"))
    assert steps == [pytest.approx(-0.057)]


def test_a_short_step_sideways_is_driven_not_refused(monkeypatch):
    # 7 ต.ค. 14:08: 3.6 cm off with the near side under 4 cm -> a 3.6 cm step,
    # which drive_velocity refused as "not a straight lane" (< 5 cm). Simulated
    # D8 from the corridor, a 0.74 m robot (3 cm a side through the frame).
    sim = _SimBase((-2.38 + 0.036, -2.56, NORTH))
    dr, r = _door_runner(sim, monkeypatch)
    r.door_width = r.turn_width = 0.74
    r.measured_at = {"x": (sim.odom[0], sim.odom[1])}
    r.door_velocity(rl.Step(do="door", door="D8", heading_deg=90, until=START_ROOM, label="D8 in"))
    across = [e for e in r.logged if e.get("kind") == "door_step_across"]
    assert len(across) >= 1 and abs(across[0]["off"]) < 0.05
    assert r.logged[-1]["kind"] == "door" and r.logged[-1]["ok"]
    assert sim.worst > CLEAR


def _turnspot_scan():
    import json
    from pathlib import Path
    doc = json.loads((Path(__file__).parent / "data" / "robot" / "scan_d11_turnspot_20261007.json").read_text())
    return np.array(doc["points"]), tuple(doc["pose"])


def test_d11s_leaf_tip_off_the_corner_does_not_block_a_short_step(monkeypatch):
    # 7 ต.ค. 15:53, D11's turn spot, 3.7 cm south of the line: the step north
    # turned, then stood 20 s before "something 0.36 m ahead" and ended the
    # route. The leaf tip was 0.34-0.36 m to the side of that way: in a lane's
    # strip (+5 cm a side), not in the doorway's (+1 cm).
    pts, pose = _turnspot_scan()
    dr, r, _ = _bare_runner(monkeypatch, pose)
    r.b.scan = lambda: pts
    r.turn_width, r.limits = 0.70, rl.DriveLimits()
    r.fast_pose = lambda: pose
    drives = []
    r.drive_velocity = lambda step, **kw: drives.append((step.to, kw.get("front_margin")))
    r.rotate_to = lambda heading, tol_deg=None: None
    step = rl.Step(do="door", door="D11", heading_deg=180, label="D11 out")
    r.step_across(step, math.pi, +0.037)
    (to, margin), = drives
    assert to[1] == pytest.approx(pose[1] + 0.037, abs=1e-6) and margin == dr.STEP_STRIP_MARGIN_M
    monkeypatch.setattr(dr, "STEP_STRIP_MARGIN_M", 0.05)              # a lane's strip
    with pytest.raises(dr.StepBlocked, match="something 0.36 m the way"):
        r.step_across(step, math.pi, +0.037)


def _open_ring(radius=1.2):
    a = np.radians(np.arange(0.0, 360.0, 1.0))
    return np.c_[radius * np.cos(a), radius * np.sin(a)]


@pytest.mark.parametrize("face,slanted", [(1.9, True), (0.65, False)])
def test_a_step_with_the_door_far_enough_ahead_goes_on_a_slant(monkeypatch, face, slanted):
    # owner 8 ต.ค. ("ไม่จำเป็นต้องหันเพื่อไปปรับให้ตรงมุมไหม ไม่งั้นจะเสียเวลา"): 11:29 at D8
    # out a 5.3 cm step was two 90° spot turns of ~6 s round a 5 cm drive, 16 s.
    # 35° there and back, the offset made in 9 cm of drive 7.6 cm toward the
    # door -- square across where that would leave under 0.60 m to the door.
    pose = (-2.40, 0.25, -math.pi / 2)
    dr, r, _ = _bare_runner(monkeypatch, pose)
    r.b.scan = lambda: _open_ring()
    r.turn_width, r.limits = 0.70, rl.DriveLimits()
    r.fast_pose = lambda: pose
    drives = []
    r.drive_velocity = lambda step, **kw: drives.append(step.to)
    r.rotate_to = lambda heading, tol_deg=None: None
    r.step_across(rl.Step(do="door", door="D8", heading_deg=-90, label="D8 out"), -math.pi / 2, +0.053, face=face)
    (to,) = drives
    # heading south, left is +x: 5.3 cm left of the middle steps -x
    assert to[0] == pytest.approx(pose[0] - 0.053, abs=1e-6)
    ahead = pose[1] - to[1]
    if slanted:
        assert ahead == pytest.approx(0.053 / math.tan(math.radians(35)), abs=1e-6)
        assert r.logged[-1]["slant_deg"] == 35.0
    else:
        assert ahead == pytest.approx(0.0, abs=1e-9) and r.logged[-1]["slant_deg"] == 90.0


def test_a_step_that_cannot_be_made_is_steered_on_the_way_in(monkeypatch):
    # Something really in the step's way (0.42 m that way, off the corner, so
    # the turn has room): no turning and standing, no route ended -- the pass
    # starts 5 cm off and steers it, as it does any offset. (With the curve-in
    # decision off: that one, where it applies, comes before any step.)
    # 8 ต.ค.: the 35° slant clears the way and the turn out, and ended 1 cm from
    # it on the turn back -- the spot turn's own check stopped the route there
    # until the turn back was forecast before anything moved.
    sim = _SimBase((-2.37 - 0.05, START_ROOM, SOUTH))
    sim.world = np.vstack([sim.world, [(-2.42 + 0.42, START_ROOM - 0.30, -2.42 + 0.42, START_ROOM - 0.36)]])
    dr, r = _door_runner(sim, monkeypatch)
    monkeypatch.setattr(dr, "DOOR_STEER_IN_MAX_M", 0.0)
    r.door_width = r.turn_width = 0.70
    r.measured_at = {"x": (sim.odom[0], sim.odom[1])}
    r.door_velocity(rl.Step(do="door", door="D8", heading_deg=-90, until=-2.56, label="D8 out"))
    assert not [e for e in r.logged if e.get("kind") == "door_step_across"]
    blocked = [e for e in r.logged if e.get("kind") == "door_step_blocked"]
    assert len(blocked) == 1 and blocked[0]["off"] == pytest.approx(-0.05, abs=0.02)
    assert "the way a step would go" in blocked[0]["why"]
    door = r.logged[-1]
    assert door["kind"] == "door" and door["ok"], door.get("reason")
    assert sim.worst > CLEAR
    assert sim.true[1] == pytest.approx(-2.56, abs=0.05)


def test_a_step_that_cannot_be_made_with_little_room_a_side_still_stops(monkeypatch):
    # reviewer: the steer-in must keep the near side DOOR_STEER_KEEP_M uncorrected
    # -- 13:55 at D8 1.9 cm went to 5 mm inside the frame. A 0.76 m robot at
    # 5 cm off leaves under 2 cm on the near side.
    sim = _SimBase((-2.37 - 0.05, START_ROOM, SOUTH))
    sim.world = np.vstack([sim.world, [(-2.42 + 0.42, START_ROOM - 0.30, -2.42 + 0.42, START_ROOM - 0.36)]])
    dr, r = _door_runner(sim, monkeypatch)
    r.door_width, r.turn_width = 0.76, 0.70
    r.measured_at = {"x": (sim.odom[0], sim.odom[1])}
    with pytest.raises(dr.StepBlocked):
        r.door_velocity(rl.Step(do="door", door="D8", heading_deg=-90, until=-2.56, label="D8 out"))
    assert sim.true[1] == START_ROOM                                  # never moved


def test_a_short_lidar_frame_is_read_again_not_taken_as_no_room(monkeypatch):
    pts, pose = _turnspot_scan()
    dr, r, _ = _bare_runner(monkeypatch, pose)
    frames = [pts[:60], pts]
    r.b.scan = lambda: frames.pop(0) if frames else pts
    r.turn_width, r.limits = 0.70, rl.DriveLimits()
    r.fast_pose = lambda: pose
    drives = []
    r.drive_velocity = lambda step, **kw: drives.append(step.to)
    r.rotate_to = lambda heading, tol_deg=None: None
    r.step_across(rl.Step(do="door", door="D11", heading_deg=180, label="D11 out"), math.pi, +0.037)
    assert len(drives) == 1


@pytest.mark.parametrize("off", [-0.04, 0.04])
def test_a_few_cm_off_with_room_ahead_curves_onto_the_line_instead_of_stepping(monkeypatch, off):
    # owner 8 ต.ค. (a sketch: the path curving onto the door's line on the way):
    # D8 from the room, 1.9 m out, 4 cm off -- no stop and step sideways (~20 s)
    sim = _SimBase((-2.38 + off, START_ROOM, SOUTH))
    dr, r = _door_runner(sim, monkeypatch)
    r.door_width = r.turn_width = 0.70
    r.measured_at = {"x": (sim.odom[0], sim.odom[1])}
    r.door_velocity(rl.Step(do="door", door="D8", heading_deg=-90, until=-2.56, label="D8 out"))
    assert [e["kind"] for e in r.logged if e.get("kind", "").startswith("door_st")] == ["door_steer_in"]
    door = r.logged[-1]
    assert door["ok"] and sim.worst + 0.04 > 0.025                 # the 0.70 body
    assert abs(sim.true[0] - (-2.38)) < 0.03


def test_curving_in_only_what_the_approach_brings_within_the_line_up(monkeypatch):
    # reviewer: the first gate let 6 cm in with ~2 cm a side at the frame -- the
    # approach takes off 1.4-2.5 cm before the slow zone, not half per 1.15 m.
    # 5 cm with 1.28 m of run is predicted 3.5 cm at the frame, past the 3 cm a
    # step would leave here: it steps (west, away from the leaf -- toward it there
    # is no room to turn, and that stops as it did before the curve-in).
    sim = _SimBase((-2.38 - 0.05, START_ROOM, SOUTH))
    dr, r = _door_runner(sim, monkeypatch)
    r.door_width = r.turn_width = 0.70
    r.measured_at = {"x": (sim.odom[0], sim.odom[1])}
    r.door_velocity(rl.Step(do="door", door="D8", heading_deg=-90, until=-2.56, label="D8 out"))
    assert [e["kind"] for e in r.logged if e.get("kind", "").startswith("door_st")] == ["door_step_across"]
    assert r.logged[-1]["ok"]


def test_a_narrow_opening_still_steps_rather_than_curving_in(monkeypatch):
    # the curve-in did worse than the pulses where the opening leaves 3 cm a side
    sim = _SimBase((-2.38 + 0.05, START_ROOM, SOUTH))
    dr, r = _door_runner(sim, monkeypatch)
    r.door_width, r.turn_width = 0.78, 0.70
    r.measured_at = {"x": (sim.odom[0], sim.odom[1])}
    try:
        r.door_velocity(rl.Step(do="door", door="D8", heading_deg=-90, until=-2.56, label="D8 out"))
    except dr.Stop:
        pass
    assert not [e for e in r.logged if e.get("kind") == "door_steer_in"]


def test_a_big_offset_that_cannot_be_stepped_still_stops(monkeypatch):
    sim = _SimBase((-2.37 + 0.09, START_ROOM, SOUTH))
    sim.world = np.vstack([sim.world, [(-2.28 - 0.42, START_ROOM - 0.30, -2.28 - 0.42, START_ROOM - 0.36)]])
    dr, r = _door_runner(sim, monkeypatch)
    r.door_width = r.turn_width = 0.70
    r.measured_at = {"x": (sim.odom[0], sim.odom[1])}
    with pytest.raises(dr.StepBlocked):
        r.door_velocity(rl.Step(do="door", door="D8", heading_deg=-90, until=-2.56, label="D8 out"))
    assert sim.true[1] == START_ROOM                                  # never moved


def test_the_base_is_rounded_as_the_d8_jamb_showed():
    # 7 ต.ค. 14:05, D8: the jamb 0.101-0.110 m ahead of the centre at 0.349-0.350
    # from the centre line; the owner looked: "ไม่แตะ มีช่อง". The box says
    # touching; the rounded outline says a few mm -- what the owner saw.
    xs = np.linspace(0.101, 0.110, 6)
    jamb = np.c_[xs, np.linspace(0.3490, 0.3501, 6)]
    box_left, _ = rl.side_clearance(jamb, half_width=0.35)
    shaped_left, _ = rl.side_clearance(jamb, half_width=0.35, shaped=True)
    assert box_left < 0.0 < shaped_left < 0.01
    # the middle is the tape's full 0.70: a jamb there at 0.349 is touching either way
    mid = np.c_[np.linspace(-0.01, 0.01, 6), np.full(6, 0.349)]
    assert rl.side_clearance(mid, half_width=0.35, shaped=True)[0] < 0.0
    assert rl.base_shape(0.0) == pytest.approx(1.0) and rl.base_shape(0.30) == pytest.approx(0.0)
    assert 0.35 * rl.base_shape(0.10) == pytest.approx(0.3456, abs=0.0005)


@pytest.mark.parametrize("heading_deg,expect", [(0, +0.057), (180, -0.057)])
def test_with_the_leaf_side_known_the_aim_is_the_other_jamb_not_the_frames_middle(monkeypatch, heading_deg, expect):
    # 7 ต.ค. 14:37, D11 in: both jambs 0.874 m apart, the frame's middle on the
    # robot's line -- went in 7 cm toward the leaf (north, inside the big
    # room) and was held by it in the frame. The south jamb is the passage's
    # edge: from it the robot was 5.7 cm left. Facing west (D11 out) the same
    # leaf is on the right.
    sim = _SimBase((-2.38, -2.56, NORTH))
    dr, r = _door_runner(sim, monkeypatch)
    h = math.radians(heading_deg)
    sim.true[2] = sim.odom[2] = h
    r.route = rl.Route(name="t", anchors={}, doors={"D11": rl.Door("x", 3.375, -0.96, -0.20, leaf="hi")}, steps=[])
    r.measured_at, r.door_width, r.door_min_margin = {}, 0.70, 0.02      # the run's --arms-in --door-margin 0.02
    side = 1 if heading_deg == 0 else -1               # the north jamb is on the robot's left facing east
    lo, hi = (-0.437, 0.437)
    reading = {"face": 0.82, "lo": lo, "hi": lo + 0.76, "width": 0.76, "centre": lo + 0.38, "edges": 1,
               "frame_centre": 0.0, "frame_lo": lo, "frame_hi": hi, "frame_width": 0.874}
    # stepped `expect` to the right: the jambs now read that much further left
    shifted = dict(reading, frame_lo=lo + expect, frame_hi=hi + expect, frame_centre=expect)
    it = iter([reading, shifted])
    monkeypatch.setattr(rl, "opening_ahead", lambda *a, **k: next(it))
    steps = []
    r.step_across = lambda step, heading, off, face=None: steps.append(off)

    def built(**kw):
        raise _Decided(kw)

    monkeypatch.setattr(dr.rl, "DoorPass", built)
    with pytest.raises(_Decided):
        r.door_velocity(rl.Step(do="door", door="D11", heading_deg=heading_deg, until=4.7, label="D11"))
    assert steps == [pytest.approx(expect, abs=0.002)] and side in (1, -1)


def test_side_balance_steers_away_from_the_closer_side():
    # 14:37, in D11's frame: 1.4 cm left, 15.6 cm right -> 7.1 cm too far left
    bal, both = rl.side_balance((0.014, 0.156))
    assert both and bal == pytest.approx(0.071)
    bal, both = rl.side_balance((0.02, None), keep=0.06)             # one side, too close: off it
    assert not both and bal == pytest.approx(0.04)
    bal, both = rl.side_balance((None, 0.03), keep=0.05)
    assert bal == pytest.approx(-0.02)
    assert rl.side_balance((0.08, None), keep=0.06)[0] is None       # one side, far enough: nothing
    assert rl.side_balance((0.30, 0.40))[0] is None                  # nothing close


def test_two_sides_that_do_not_make_one_passage_are_not_balanced():
    # 15:14, D11 out, in the big room: furniture 0.19 m left, the leaf 0.12 m
    # right -- 1.01 m with the body, for a 0.76 m passage. Balanced, it steered
    # left into the south jamb. Only the nearer side counts, and 12 cm is not
    # too near.
    assert rl.side_balance((0.19, 0.12), keep=0.03, robot_width=0.70, passage=0.76)[0] is None
    bal, both = rl.side_balance((0.19, 0.02), keep=0.03, robot_width=0.70, passage=0.76)
    assert not both and bal == pytest.approx(-0.01)                   # off the leaf, not toward the furniture
    # in the frame (14:37): 1.4 / 15.6 cm + 0.70 = 0.87 m for D11's 0.874 frame
    bal, both = rl.side_balance((0.014, 0.156), robot_width=0.70, passage=0.76)
    assert both and bal == pytest.approx(0.071)


def test_in_a_frame_the_gaps_beside_steer_it_not_odometry(monkeypatch):
    # 14:37, D11: odometry (and the lane) said centred; the jambs said 7 cm
    # left. The pulses must go right (negative) while it moves.
    monkeypatch.setattr(rl, "opening_ahead", lambda *a, **k: None)
    xs = np.linspace(-0.25, 0.25, 30)
    pts = np.vstack([np.c_[xs, np.full(30, 0.35 + 0.014)], np.c_[xs, np.full(30, -(0.35 + 0.156))]])
    dp = rl.DoorPass(middle=(3.375, -0.58), heading=0.0, until=4.7, width_hint=0.76, robot_width=0.70)
    prev, omegas = (0.0, 0.0), []
    for _ in range(30):
        cmd, info = dp.update((3.40, -0.58, 0.0), pts, prev)
        prev = (cmd.vx, 0.0)
        omegas.append(cmd.omega)
    assert info["balance"] == pytest.approx(0.071)
    assert any(w < 0 for w in omegas) and not any(w > 0 for w in omegas)


def _leaf_end_ahead_left(dist=0.38):
    """15:03, D8 from the room: the end of D8's leaf 0.38 m ahead, 0.30-0.35 m left."""
    return np.c_[np.full(12, dist), np.linspace(0.30, 0.35, 12)]


def test_something_ahead_on_one_side_is_steered_round():
    need = rl.strip_edge(_leaf_end_ahead_left(), half_width=0.35)
    assert need == pytest.approx(0.35 + 0.01 + 0.02 - 0.3045, abs=0.005)       # move right ~7.5 cm
    right = _leaf_end_ahead_left() * [1, -1]
    assert rl.strip_edge(right, half_width=0.35) == pytest.approx(-need)
    dead_ahead = np.c_[np.full(12, 0.5), np.linspace(-0.05, 0.05, 12)]
    assert rl.strip_edge(dead_ahead, half_width=0.35) is None
    both = np.vstack([_leaf_end_ahead_left(), right])
    assert rl.strip_edge(both, half_width=0.35) is None
    deep = np.c_[np.full(12, 0.5), np.linspace(0.15, 0.20, 12)]                  # would need > 12 cm
    assert rl.strip_edge(deep, half_width=0.35) is None


def test_one_edge_readings_that_creep_cannot_walk_the_estimate_off(monkeypatch):
    # 7 ต.ค. 17:31, D8 from the room at 0.25 m/s: one edge 1.3-1.9 m ahead read
    # -0.012 -> +0.087 a centimetre at a time; each passed the 3 cm gate against
    # the estimate the last one had moved, corr reached -0.059, and the robot
    # steered into the frame's east side (2.3 cm). Here: 1 cm a tick for 15 ticks.
    reading = {"lat": 0.0}

    def creeping(points, face, width):
        reading["lat"] += 0.01
        return {"face": 1.5, "lo": -0.4, "hi": 0.4, "width": 0.80, "centre": -reading["lat"], "edges": 1}
    monkeypatch.setattr(rl, "opening_ahead", creeping)
    dp = rl.DoorPass(middle=(-2.45, -1.633), heading=SOUTH, until=-2.45, width_hint=0.80, robot_width=0.70)
    prev = (0.0, 0.0)
    for _ in range(15):
        cmd, info = dp.update((-2.45, 0.0, SOUTH), np.zeros((0, 2)), prev)
        prev = (cmd.vx, cmd.omega)
    assert abs(dp.corr - dp.corr_ref) <= rl.ONE_EDGE_TRUST_M + 1e-9
    assert reading["lat"] > 0.12                                   # it did keep creeping


def test_one_edge_is_held_to_what_two_edges_measured_not_to_where_the_pass_began(monkeypatch):
    # reviewer: corr_ref fixed at the first tick pulled corr back from what two
    # edges had measured, ~1.2 cm a tick, once only one edge was in view
    ticks = {"n": 0}

    def readings(points, face, width):
        ticks["n"] += 1
        edges = 2 if ticks["n"] <= 12 else 1
        return {"face": 1.2, "lo": -0.34, "hi": 0.46, "width": 0.80, "centre": -0.06, "edges": edges}
    monkeypatch.setattr(rl, "opening_ahead", readings)
    dp = rl.DoorPass(middle=(-2.45, -1.633), heading=SOUTH, until=-2.45, width_hint=0.80, robot_width=0.70)
    prev = (0.0, 0.0)
    for _ in range(30):
        cmd, info = dp.update((-2.45, 0.0, SOUTH), np.zeros((0, 2)), prev)
        prev = (cmd.vx, cmd.omega)
    assert dp.corr == pytest.approx(-0.06, abs=0.01)


def test_a_door_pass_steers_round_the_leaf_end_instead_of_driving_up_to_it(monkeypatch):
    # odometry said on the line, so the lane alone gave no reason to turn
    monkeypatch.setattr(rl, "opening_ahead", lambda *a, **k: None)
    dp = rl.DoorPass(middle=(-2.45, -1.633), heading=SOUTH, until=-2.45, width_hint=0.80, robot_width=0.70)
    prev, omegas = (0.0, 0.0), []
    for _ in range(30):
        cmd, info = dp.update((-2.45, 0.0, SOUTH), _leaf_end_ahead_left(0.9), prev)
        prev = (cmd.vx, 0.0)
        omegas.append(cmd.omega)
    assert info["avoid"] is not None and info["avoid"] > 0
    assert any(w < 0 for w in omegas) and not any(w > 0 for w in omegas)       # right, never left


# -- the link dropping (7 ต.ค.: three drops on one trip home, each ended the run) --

def _bare_runner(monkeypatch, pose=(0.0, 0.0, 0.0)):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "scripts" / "drive_route.py"
    spec = importlib.util.spec_from_file_location("drive_route_link_test", path)
    dr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dr)
    monkeypatch.setattr(dr.time, "sleep", lambda s: None)

    class Board:
        def __init__(self): self.p = pose
        def pose(self): return self.p
        def power(self): return {"dockingStatus": "not_on_dock"}

    r = dr.Runner.__new__(dr.Runner)
    r.b, r.sdk, r.sdk_addr = Board(), None, None
    r.say = lambda m: None
    r.logged = []
    r.log = lambda **e: r.logged.append(e)
    r.route = rl.Route(name="t", anchors={}, doors={"D8": rl.Door("y", -1.633, -2.80, -1.96)}, steps=[])
    recovered = []
    r.recover_link = lambda why: recovered.append(why)
    return dr, r, recovered


def test_a_dropped_link_waits_and_does_the_step_again(monkeypatch):
    dr, r, recovered = _bare_runner(monkeypatch)
    calls = []

    def drive(step):
        calls.append(step.label)
        if len(calls) == 1:
            raise dr.LinkLost("drive: link lost mid-leg")

    step = rl.Step(do="drive", to=(3.0, 0.0), label="east")
    r.run_step(7, step, {"drive": drive})
    assert calls == ["east", "east"] and len(recovered) == 1


def test_a_safety_stop_is_not_retried(monkeypatch):
    dr, r, recovered = _bare_runner(monkeypatch)

    def drive(step):
        raise dr.Stop("something 0.04 m beside the robot")

    with pytest.raises(dr.Stop, match="beside"):
        r.run_step(7, rl.Step(do="drive", to=(3.0, 0.0), label="east"), {"drive": drive})
    assert recovered == []


def test_a_lane_that_was_nearly_done_when_the_link_dropped_counts_as_done(monkeypatch):
    # a lane needs 5 cm to have a direction: driving the last 3 cm again would Stop
    dr, r, recovered = _bare_runner(monkeypatch, pose=(2.97, 0.0, 0.0))
    calls = []

    def drive(step):
        calls.append(1)
        raise dr.LinkLost("drive: link lost mid-leg")

    r.run_step(7, rl.Step(do="drive", to=(3.0, 0.0), label="east"), {"drive": drive})
    assert calls == [1] and len(recovered) == 1


def test_a_link_that_keeps_dropping_ends_the_run(monkeypatch):
    dr, r, recovered = _bare_runner(monkeypatch)

    def drive(step):
        raise dr.LinkLost("again")

    with pytest.raises(dr.Stop, match="dropped"):
        r.run_step(7, rl.Step(do="drive", to=(3.0, 0.0), label="east"), {"drive": drive})
    assert len(recovered) == dr.LINK_STEP_RETRIES


@pytest.mark.parametrize("y,resumes", [(-1.80, True), (-2.45, False)])
def test_a_door_goes_on_from_the_frame_or_starts_over_from_the_aim(monkeypatch, y, resumes):
    # 14:11, D8 in: the link dropped with the robot through the frame
    dr, r, recovered = _bare_runner(monkeypatch, pose=(-2.38, y, NORTH))
    r.sdk = object()
    got = []

    def door_velocity(step, resume=False, skip_lineup=False):
        got.append(resume)
        if len(got) == 1:
            raise dr.LinkLost("door D8: link lost in the doorway")

    r.door_velocity = door_velocity
    r.run_step(25, rl.Step(do="door", door="D8", heading_deg=90, until=0.25, label="D8 in"), {})
    assert got == [False, resumes]


def test_a_door_pass_resumed_inside_the_frame_gets_through_without_an_aim(monkeypatch):
    sim = _SimBase((-2.38, -1.80, NORTH))
    dr, r = _door_runner(sim, monkeypatch)
    monkeypatch.setattr(rl, "opening_ahead", lambda *a, **k: None)     # from in the frame there is no face to aim at
    r.door_velocity(rl.Step(do="door", door="D8", heading_deg=90, until=START_ROOM, label="D8 in"), resume=True)
    door = r.logged[-1]
    assert door["ok"] and door["resumed"]
    assert sim.worst > CLEAR


def test_recovering_the_link_puts_the_forward_back_and_waits_for_the_board(monkeypatch):
    dr, r, _ = _bare_runner(monkeypatch)
    del r.recover_link                                   # the real one
    answers, forwards = iter([dr.LinkLost("x"), dr.LinkLost("x"), {"dockingStatus": "not_on_dock"}]), []

    def power():
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return a

    r.b.power, r.b.reforward = power, lambda: forwards.append(1)
    r.recover_link("door D8: link lost")
    assert len(forwards) == 3 and any(e.get("kind") == "link_back" for e in r.logged)
    monkeypatch.setattr(dr, "LINK_WAIT_S", 0.05)

    def gone():
        raise dr.LinkLost("x")

    r.b.power = gone
    with pytest.raises(dr.Stop, match="did not come back"):
        r.recover_link("again")


def test_the_board_lost_for_good_is_a_link_loss_not_a_crash(monkeypatch):
    import httpx
    dr, r, _ = _bare_runner(monkeypatch)
    b = dr.Board("http://127.0.0.1:1")

    def refuse(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(b.http, "request", refuse)
    with pytest.raises(dr.LinkLost):
        b.get("/api/core/system/v1/power/status")


def test_a_door_held_by_something_on_one_side_backs_off_steps_across_and_goes_in_again(monkeypatch):
    # 15:15, D11 out: held 0.38 m from the south jamb at the left of the strip;
    # it waited 8 s and the run ended. Now: back, across by what clears it, in.
    dr, r, recovered = _bare_runner(monkeypatch, pose=(3.82, -0.67, math.pi))
    r.sdk = object()
    calls, backs, steps = [], [], []

    def door_velocity(step, resume=False, skip_lineup=False):
        calls.append(skip_lineup)
        if len(calls) == 1:
            raise dr.DoorBlocked("door D11: held by something ahead on one side", 0.042, 0.90)

    r.door_velocity = door_velocity
    r.back_off = lambda d, most=None, turn=None: backs.append((d, most, turn))
    r.step_across = lambda step, heading, off, face=None: steps.append(off)
    r.run_step(18, rl.Step(do="door", door="D11", heading_deg=180, until=2.55, label="D11 out"), {})
    assert calls == [False, True]                                  # in again without stepping back
    assert backs == [(pytest.approx(dr.DOOR_BACK_OFF_M), pytest.approx(0.88), pytest.approx(-math.pi / 2))]
    assert steps == [pytest.approx(0.042)]


def test_backing_off_goes_on_until_the_quarter_turn_has_room(monkeypatch):
    # 15:24, big room: 0.30 m back still had D11's open leaf beside the robot,
    # 12 cm from the body -- no room for the step's quarter turn. Back on.
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "scripts" / "drive_route.py"
    spec = importlib.util.spec_from_file_location("drive_route_backoff_test", path)
    dr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dr)
    monkeypatch.setattr(dr.time, "sleep", lambda s: None)

    class Sim:
        p = [4.13, -0.67, math.pi]
        def call(self, command, args=None, timeout=None):
            if command == "velocitycontrol":
                return {"code": 1, "id": 4}
            if command == "getpose":
                return {"x": self.p[0], "y": self.p[1], "yaw": self.p[2]}
            if command == "getcurrentaction":
                return {"id": 0}
            if command == "setvelocity":
                self.p[0] += args["vx"] * math.cos(self.p[2]) * 0.1
            return {"code": 0, "codestr": "done"}

    sim = Sim()

    class Watcher:
        def start(self): pass
        def stop(self): pass
        def points(self):
            # the leaf lies along x 3.50-4.40 at y -0.20; facing west from y -0.67
            # it is on the robot's right, 0.47 m from the centre line
            xs = np.linspace(3.50, 4.40, 400)
            return np.c_[sim.p[0] - xs, np.full(400, -0.47)]

    class Board:
        def events(self): return []

    r = dr.Runner.__new__(dr.Runner)
    r.b, r.sdk, r.turn_width = Board(), sim, 0.70
    r.say, r.logged = (lambda m: None), []
    r.log = lambda **e: r.logged.append(e)
    r.watcher = Watcher
    r.fast_pose = dr.Runner.fast_pose.__get__(r)
    r.back_off(0.30, most=0.90, turn=-math.pi / 2)            # need +0.038: a right turn, toward the leaf
    moved = r.logged[-1]["moved"]
    assert 0.40 < moved < 0.90                   # past the 0.30, stopped once the turn has room


def test_held_by_a_post_at_the_edge_of_the_strip_says_which_way_clears_it(monkeypatch):
    # a post 0.30 m left of the line, 0.15 m in front of the robot's face: no
    # room to steer round it before the strip holds the robot
    sim = _SimBase((-2.38, -2.56, NORTH))
    sim.world = np.vstack([sim.world, [(-2.38 - 0.30, -2.11, -2.38 - 0.34, -2.11)]])
    dr, r = _door_runner(sim, monkeypatch)
    r.door_width = 0.70
    with pytest.raises(dr.DoorBlocked) as got:
        r.door_velocity(rl.Step(do="door", door="D8", heading_deg=90, until=START_ROOM, label="D8 in"),
                        skip_lineup=True)
    assert got.value.need > 0                                      # too far left: the way round is right
    assert sim.worst > CLEAR


def test_no_opening_for_a_moment_is_looked_at_again(monkeypatch):
    # 15:32, D8 from the corridor: three looks in a second found nothing (someone
    # in the doorway); a minute later the same spot saw the 0.857 m passage.
    centred = {"face": 0.85, "lo": -0.42, "hi": 0.42, "width": 0.84, "centre": 0.0, "edges": 2}
    steps, kw, r = _decide(monkeypatch, {"x": (-2.38, -2.56)}, [None] * 6 + [centred])
    assert steps == [] and kw is not None


def test_a_door_takes_its_heading_again_at_its_own_checkpoint():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "scripts" / "drive_route.py"
    spec = importlib.util.spec_from_file_location("drive_route_cp_test", path)
    dr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dr)
    S = rl.Step
    r = dr.Runner.__new__(dr.Runner)
    r.route = rl.Route(name="t", anchors={}, doors={}, steps=[
        S(do="checkpoint", heading_deg=-90, label="above D8"), S(do="drive", to=(-2.47, 0.25)),
        S(do="door", door="D8", heading_deg=-90, until=-2.3),
        S(do="checkpoint", heading_deg=0, label="corridor"), S(do="drive", to=(2.46, -2.3)),
        S(do="door", door="D11", heading_deg=90, until=0.25),
        S(do="door", door="D12", heading_deg=90, until=1.0)])
    assert r.checkpoint_for(3).label == "above D8"
    assert r.checkpoint_for(6) is None                      # its checkpoint faces another way
    assert r.checkpoint_for(7) is None                      # a door, not a checkpoint, before it



def test_the_real_approach_scan_sees_one_edge_and_the_middle_by_the_drawing():
    # 6 ต.ค. 17:32, robot at (-2.384, 0.250) facing D8 1.87 m away: the glass
    # leaf and the wall behind it are invisible from here; the west jamb is
    # plain. gap_ahead (both sides) read a 1.8 m opening 0.38 m to the right.
    import json
    from pathlib import Path
    doc = json.loads((Path(__file__).parent / "data" / "robot" / "scan_d8_approach_glass_20261006.json").read_text())
    pts = rl.scan_points(doc)
    g = rl.opening_ahead(pts, 0.250 - (-1.62), 0.86)
    assert g is not None and g["edges"] == 1
    assert g["face"] == pytest.approx(1.87, abs=0.06)
    assert abs(g["centre"]) < 0.04                         # the robot was lined up by odometry to ~1-2 cm
    bad = rl.gap_ahead(pts)
    assert bad is not None and bad["width"] > 1.5          # what the both-sides measurement made of it



def test_the_heading_guard_in_a_frame_follows_the_room_either_side():
    # 7 ต.ค. 10:30: stopped at 6.1° in D8's frame with 9.8 / 11.9 cm free
    assert math.degrees(rl.frame_turn_limit((0.119, 0.098))) > 10
    assert math.degrees(rl.frame_turn_limit((0.02, 0.05))) == pytest.approx(6.0)     # tight: the old 6°
    assert math.degrees(rl.frame_turn_limit((0.5, 0.6))) == pytest.approx(12.0)      # never past 12°
    assert math.degrees(rl.frame_turn_limit((None, None))) == pytest.approx(6.0)



def test_one_edge_from_afar_that_disagrees_with_the_lane_is_not_steered_by(monkeypatch):
    # 7 ต.ค. 10:44: on D8's line by the lane (0.5 cm off), one edge seen from
    # 1.9 m said 6.6 cm off — the edge it picked moves with the standpoint.
    dp = rl.DoorPass(middle=(-2.465, -1.66), heading=SOUTH, until=-2.30, width_hint=0.81, robot_width=0.70)
    monkeypatch.setattr(rl, "opening_ahead", lambda *a, **k: {"face": 1.89, "lo": -0.339, "hi": 0.471,
                                                              "width": 0.81, "centre": 0.066, "edges": 1})
    cmd, info = dp.update((-2.47, 0.22, SOUTH), np.zeros((0, 2)), (0.0, 0.0))
    assert info["lat_meas"] is None and info["corr"] is None
    # the same reading 2 cm from the lane is used
    monkeypatch.setattr(rl, "opening_ahead", lambda *a, **k: {"face": 1.89, "lo": -0.385, "hi": 0.425,
                                                              "width": 0.81, "centre": 0.02, "edges": 1})
    cmd, info = dp.update((-2.47, 0.22, SOUTH), np.zeros((0, 2)), (0.0, 0.0))
    assert info["lat_meas"] == pytest.approx(-0.02)



def test_a_wrong_wall_heading_cannot_steer_the_robot_far_off_odometry(monkeypatch):
    # 7 ต.ค. 11:02: the wall heading drifted to -13° from odometry and the
    # pulses turned the robot 22.8°. The correction is now held within 3°.
    dp = rl.DoorPass(middle=(-2.465, -1.66), heading=NORTH, until=0.25, width_hint=0.81, robot_width=0.70)
    monkeypatch.setattr(rl, "heading_error_from_walls", lambda *a, **k: math.radians(-14))
    monkeypatch.setattr(rl, "opening_ahead", lambda *a, **k: None)
    for _ in range(50):
        cmd, info = dp.update((-2.47, -2.4, NORTH), np.zeros((0, 2)), (0.05, 0.0))
    assert abs(math.degrees(info["yaw_est"] - NORTH)) <= 3.0 + 1e-9


# -- out of a door round the corner into the next lane (DOOR_EXIT_*) ------------

class _PathSim(_SimBase):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.path = []

    def call(self, command, args=None, timeout=None):
        if command == "setvelocity":
            self.path.append(tuple(self.true))
        return super().call(command, args, timeout)


CORRIDOR_WALLS = {"cor_north": rl.Anchor("y", -1.70, -6.0, 6.0), "cor_south": rl.Anchor("y", -3.42, -6.0, 6.0),
                  "cor_west": rl.Anchor("x", -4.80, -6.0, 6.0)}


def _d8_out_then_corridor(monkeypatch, sim, corners=True, fences=(), cp_use=("cor_north", "cor_south"),
                          lane_use=("cor_north", "cor_south"), after_pass=None):
    dr, r = _door_runner(sim, monkeypatch)
    r.door_width = r.turn_width = 0.70
    r.measured_at = {"x": (sim.odom[0], sim.odom[1])}
    r.b.set_speed = lambda v: None
    r.b.power = lambda: {"dockingStatus": "not_on_dock"}
    r.b.set_pose = lambda p: None
    r.fences, r.no_corners = list(fences), not corners
    r.limits = rl.DriveLimits(v_max=0.4, w_max=0.5)
    r.route = rl.Route(name="d8", anchors=dict(CORRIDOR_WALLS), doors={"D8": rl.Door("y", -1.633, -2.80, -1.96)},
                       steps=[rl.Step(do="door", door="D8", heading_deg=-90, until=-2.45, label="D8 out"),
                              rl.Step(do="checkpoint", heading_deg=0, use=cp_use, label="corridor below D8"),
                              rl.Step(do="drive", to=(-1.30, -2.45), use=lane_use, label="east along the corridor")])
    r.checkpoint = lambda st: r.logged.append({"kind": "checkpoint_run", "label": st.label})
    if after_pass is not None:
        real = r.run_step

        def run_step(i, step, handlers, **kw):
            out = real(i, step, handlers, **kw)
            if step.do == "door":
                after_pass(sim)
            return out
        r.run_step = run_step
    r.run(1)
    return dr, r


def test_out_of_d8_round_the_corner_into_the_corridor_without_a_stop_or_a_spot_turn(monkeypatch):
    # owner 8 ต.ค.: "ไม่จำเป็นต้องหันเพื่อไปปรับให้ตรงมุมไหม ไม่งั้นจะเสียเวลา" -- out of D8 the
    # pass stopped on the corridor's lane, the checkpoint turned 90° on the spot
    # (5.5 s at 11:30) and the lane set off from standing
    sim = _PathSim((-2.38, START_ROOM, SOUTH))
    dr, r = _d8_out_then_corridor(monkeypatch, sim)
    kinds = [e.get("kind") for e in r.logged]
    assert "checkpoint_run" not in kinds and "turn" not in kinds
    assert [e["i"] for e in r.logged if e.get("kind") == "step_skipped"] == [2]
    lead = [e for e in r.logged if e.get("kind") == "drive"][0]
    assert lead["handed_over"] == "east along the corridor" and "clear" in lead["corner"]
    x, y, yaw = sim.true
    assert (x, y) == pytest.approx((-1.30, -2.45), abs=0.03) and abs(yaw) < math.radians(4)
    # the corner never takes the body back toward the frame it has just left: past
    # the wall line every point of the path goes on south, and stays clear of it
    out = [p for p in sim.path if p[1] < -1.66 - 0.38]
    assert out and max(p[1] for p in out[1:]) <= out[0][1] + 1e-6
    assert sim.worst + 0.04 > 0.04                                  # the 0.70 base, as in the frame


def test_out_of_d8_with_the_corner_not_clear_goes_on_to_the_lane_and_turns_as_before(monkeypatch):
    # a forbidden area across the arc (not the straight way on, nor the lane):
    # the lead lane stops on the corridor's line, the checkpoint turns, the lane goes
    sim = _PathSim((-2.38, START_ROOM, SOUTH))
    dr, r = _d8_out_then_corridor(monkeypatch, sim, fences=[(-2.31, -2.21, -2.38, -2.28)])
    kinds = [e.get("kind") for e in r.logged]
    assert "checkpoint_run" in kinds and "step_skipped" not in kinds
    lead = [e for e in r.logged if e.get("kind") == "drive"][0]
    assert not lead.get("handed_over") and "forbidden" in lead["corner"]
    assert lead["end"][1] == pytest.approx(-2.45, abs=0.03)
    assert sim.true[0] == pytest.approx(-1.30, abs=0.03)


@pytest.mark.parametrize("lane_y,expect_r", [(-2.45, 0.377), (-2.25, None)])
def test_a_corner_out_of_a_door_needs_half_the_body_of_radius(monkeypatch, lane_y, expect_r):
    # under half its width the inner rear corner moves back as it turns -- into
    # the jamb it has just passed: such a lane is turned into on the spot
    sim = _SimBase((-2.38, START_ROOM, SOUTH))
    dr, r = _door_runner(sim, monkeypatch)
    r.turn_width = 0.70
    r.route = rl.Route(name="d8", anchors=dict(CORRIDOR_WALLS), doors={"D8": rl.Door("y", -1.633, -2.80, -1.96)},
                       steps=[rl.Step(do="door", door="D8", heading_deg=-90, until=lane_y, label="D8 out"),
                              rl.Step(do="checkpoint", heading_deg=0, use=("cor_north",), label="corridor below D8"),
                              rl.Step(do="drive", to=(-1.30, lane_y), use=("cor_south",),
                                      label="east along the corridor")])
    plan = r.door_exit_plan(1)
    if expect_r is None:
        assert plan is None
    else:
        r_ = min(dr.CORNER_R, plan["c_from_wall"] - plan["end_from_wall"] - dr.DOOR_EXIT_RUNUP_M)
        assert r_ == pytest.approx(expect_r, abs=0.002) and plan["skipped"] == 2 and plan["lane_k"] == 3
        assert plan["until"] == pytest.approx(-1.633 - 0.38)


@pytest.mark.parametrize("cp_use,lane_use", [(("cor_north", "cor_west"), ("cor_north", "cor_south")), (("cor_north",), ())])
def test_a_checkpoint_is_rounded_only_where_the_lane_measures_what_it_did(monkeypatch, cp_use, lane_use):
    # reviewer: a checkpoint measuring along the lane (cor_west, x) -- or a lane
    # with no walls of its own -- loses its only fix when it is not stopped at
    sim = _SimBase((-2.38, START_ROOM, SOUTH))
    dr, r = _door_runner(sim, monkeypatch)
    r.turn_width = 0.70
    r.route = rl.Route(name="d8", anchors=dict(CORRIDOR_WALLS), doors={"D8": rl.Door("y", -1.633, -2.80, -1.96)},
                       steps=[rl.Step(do="door", door="D8", heading_deg=-90, until=-2.45, label="D8 out"),
                              rl.Step(do="checkpoint", heading_deg=0, use=cp_use, label="corridor below D8"),
                              rl.Step(do="drive", to=(-1.30, -2.45), use=lane_use, label="east along the corridor")])
    assert r.door_exit_plan(1) is None


def test_the_corner_out_survives_the_pass_stopping_a_little_late(monkeypatch):
    # reviewer: the run-up was exactly the lane's 5 cm "room before the arc" --
    # the pass ending a millimetre past its line lost the corner, every time
    def late(sim):                       # the simulated pass stops 5.5 mm short: 4.5 mm past
        sim.true[1] -= 0.010
        sim.odom[1] -= 0.010
    sim = _PathSim((-2.38, START_ROOM, SOUTH))
    dr, r = _d8_out_then_corridor(monkeypatch, sim, after_pass=late)
    lead = [e for e in r.logged if e.get("kind") == "drive"][0]
    assert lead["handed_over"] == "east along the corridor", lead["corner"]


def _late_by(m):
    def late(sim):
        sim.true[1] -= m
        sim.odom[1] -= m
    return late


def test_the_corner_out_survives_a_pass_that_stops_two_centimetres_late(monkeypatch):
    # 9 ต.ค., D8 out from the panel: the pass stopped at y -2.031 for an end at
    # -2.013 -- 1.8 cm on -- the radius came to 0.3589 against the 0.36 floor and
    # the corner was dropped for a stop on the corridor's line and a 90° spot turn
    # (~6 s). The radius stays at its floor; the run-up gives the difference.
    sim = _PathSim((-2.38, START_ROOM, SOUTH))
    dr, r = _d8_out_then_corridor(monkeypatch, sim, after_pass=_late_by(0.0235))   # 5.5 mm short -> 1.8 cm on
    lead = [e for e in r.logged if e.get("kind") == "drive"][0]
    assert lead["handed_over"] == "east along the corridor", lead.get("corner")
    assert "checkpoint_run" not in [e.get("kind") for e in r.logged]
    # never toward the frame it has just left, and nowhere near the jambs
    out = [p for p in sim.path if p[1] < -1.66 - 0.38]
    assert out and max(p[1] for p in out[1:]) <= out[0][1] + 1e-6
    assert sim.worst + 0.04 > 0.04


def test_a_pass_that_stops_further_on_than_the_run_up_allows_still_turns_on_the_spot(monkeypatch):
    # 3.5 cm late leaves the floor radius under DOOR_EXIT_MIN_RUNUP_M of run-up:
    # no tighter arc, no shorter run-up -- on to the lane's line and turn there
    sim = _PathSim((-2.38, START_ROOM, SOUTH))
    dr, r = _d8_out_then_corridor(monkeypatch, sim, after_pass=_late_by(0.040))
    lead = [e for e in r.logged if e.get("kind") == "drive"][0]
    assert not lead.get("handed_over")
    assert "checkpoint_run" in [e.get("kind") for e in r.logged]
    assert sim.true[0] == pytest.approx(-1.30, abs=0.03)


def test_a_leaf_along_the_way_out_keeps_the_pass_going_to_its_end(monkeypatch):
    # reviewer: D8 in from the corridor, the leaf along the robot's right in the
    # room. Ended at its slow zone for a corner, a lane's 4 cm side stop held it
    # beside the leaf 20 s and ended the route there, in the doorway.
    NORTH_ = math.pi / 2
    sim = _PathSim((-2.38, -2.45, NORTH_))
    dr, r = _door_runner(sim, monkeypatch)
    r.door_width = r.turn_width = 0.70
    r.measured_at = {"x": (sim.odom[0], sim.odom[1])}
    r.b.set_speed = lambda v: None
    r.b.power = lambda: {"dockingStatus": "not_on_dock"}
    r.b.set_pose = lambda p: None
    r.fences, r.limits = [], rl.DriveLimits(v_max=0.4, w_max=0.5)
    monkeypatch.setattr(dr, "LANE_HOLD_S", 2.0)
    room = {"room_south": rl.Anchor("y", -1.62, -6.0, 6.0)}
    r.route = rl.Route(name="d8", anchors=room, doors={"D8": rl.Door("y", -1.633, -2.80, -1.96)}, steps=[
        rl.Step(do="door", door="D8", heading_deg=90, until=0.25, label="D8 in"),
        rl.Step(do="drive", to=(-3.20, 0.25), use=("room_south",), label="west in the room")])
    assert r.door_exit_plan(1) is not None
    r.run(1)
    corner = [e for e in r.logged if e.get("kind") == "door_exit_corner"]
    assert corner and corner[0]["taken"] is False and corner[0]["beside"] < rl.DOOR_FAST_SIDE_M
    assert not [e for e in r.logged if e.get("kind") == "drive" and "out of D8" in e.get("label", "")]
    assert sim.true[0] == pytest.approx(-3.20, abs=0.04)


def test_a_slant_is_taken_only_where_the_turn_back_will_be_allowed(monkeypatch):
    # reviewer: the forecast let a slant go at 3.7 cm, the real turn back needs 4;
    # the robot stood 35° to the door. Every post the slant accepts, the real
    # turn back (`turn_space_clear`, from where the step ends) must accept too.
    dr, r, _ = _bare_runner(monkeypatch, (0.0, 0.0, 0.0))
    r.turn_width = 0.70
    a = np.radians(np.arange(-100, 100, 0.5))
    ring = np.c_[3.0 * np.cos(a), 3.0 * np.sin(a)]
    off, slant = +0.05, math.radians(dr.DOOR_STEP_ANGLE_DEG)
    side = -math.copysign(slant, off)
    run = abs(off) / math.sin(slant)
    ex, ey = run * math.cos(side), run * math.sin(side)
    c, s = math.cos(side), math.sin(side)
    accepted = 0
    for d in np.arange(0.30, 0.75, 0.01):
        for ang in np.radians(np.arange(-180, 180, 6)):
            px, py = d * math.cos(ang), d * math.sin(ang)            # a post, in the frame where it ends
            sx, sy = ex + px * c - py * s, ey + px * s + py * c
            if math.degrees(math.atan2(sy, sx)) < -100:
                continue                                              # behind the lidar at the start
            q = np.vstack([ring, np.c_[sx + 0.003 * np.arange(5), np.full(5, sy)]])
            if r._step_blocked(q, off, slant) is not None:
                continue
            accepted += 1
            end = np.c_[(q[:, 0] - ex) * math.cos(-side) - (q[:, 1] - ey) * math.sin(-side),
                        (q[:, 0] - ex) * math.sin(-side) + (q[:, 1] - ey) * math.cos(-side)]
            back = rl.turn_sweep_gap(end, -side + math.copysign(math.radians(dr.TURN_COAST_DEG), -side), width=0.70)
            assert back >= dr.TURN_MARGIN_M, (round(d, 3), round(math.degrees(ang)), back)
    assert accepted > 100



def test_after_a_step_a_few_millimetres_over_the_line_up_is_not_stepped_back(monkeypatch):
    # 8 ต.ค. 13:12 at D11: a 3.4 cm step, then the jamb read 2.3 cm the other way --
    # 3 mm over the 2 cm line-up -- and a second step, 12 s. The aim after a step
    # is a new reading of a new place; a few millimetres is its noise.
    sim = _SimBase((-2.38 - 0.06, START_ROOM, SOUTH))
    dr, r = _door_runner(sim, monkeypatch)
    monkeypatch.setattr(dr, "DOOR_STEER_IN_MAX_M", 0.0)
    r.door_width = r.turn_width = 0.70
    r.measured_at = {"x": (sim.odom[0], sim.odom[1])}
    steps = []

    def step_across(step, heading, off, face=None):
        # leaves the robot lineup + 3 mm the other side (the sim's line-up is 3 cm)
        steps.append(off)
        for st in (sim.true, sim.odom):
            st[0] += -math.copysign(0.033, off) - off           # heading south: the robot's left is +x
        r.measured_at = {"x": (sim.odom[0], sim.odom[1])}
    r.step_across = step_across
    r.door_velocity(rl.Step(do="door", door="D8", heading_deg=-90, until=-2.56, label="D8 out"))
    assert len(steps) == 1



def test_where_the_opening_is_tight_a_few_millimetres_over_is_still_stepped(monkeypatch):
    # reviewer: at D11 (3 cm a side) the slack would let 2.6 cm through after a
    # step -- 4 mm on the near side, under 13:55's 5. There it steps again.
    sim = _SimBase((-2.38 - 0.06, START_ROOM, SOUTH))
    dr, r = _door_runner(sim, monkeypatch)
    monkeypatch.setattr(dr, "DOOR_STEER_IN_MAX_M", 0.0)
    r.door_width, r.turn_width = 0.78, 0.70                    # the sim's 0.84 opening: 3 cm a side
    r.measured_at = {"x": (sim.odom[0], sim.odom[1])}
    steps = []

    def step_across(step, heading, off, face=None):
        steps.append(off)
        left = -math.copysign(0.023, off) if len(steps) == 1 else 0.0   # line-up (2 cm) + 3 mm, then the middle
        for st in (sim.true, sim.odom):
            st[0] += left - off
        r.measured_at = {"x": (sim.odom[0], sim.odom[1])}
    r.step_across = step_across
    try:
        r.door_velocity(rl.Step(do="door", door="D8", heading_deg=-90, until=-2.56, label="D8 out"))
    except (dr.Stop, dr.DoorBlocked):
        pass                                                    # the pass itself is not what is tested
    assert len(steps) == 2


@pytest.mark.parametrize("yaw_deg", [1.5, -1.5, 1.9])
def test_the_aim_reads_the_door_square_to_it_not_to_the_robot(monkeypatch, yaw_deg):
    # 8 ต.ค., D11 in, both runs: the jamb stood at the same place in the world for
    # all three aims, but read 3 cm one way and then 2-3.5 cm the other after a 3 cm
    # step -- the robot's heading had changed by 1.2-2.1° (the turn before an aim
    # stops within 2°), and at the frame's distance a degree is 1.4-3.3 cm across.
    # Two steps back and forth, ~20 s. On the middle line, yawed: read on the middle.
    sim = _SimBase((-2.38, START_ROOM, SOUTH + math.radians(yaw_deg)))
    dr, r = _door_runner(sim, monkeypatch)
    monkeypatch.setattr(dr, "DOOR_STEER_IN_MAX_M", 0.0)
    r.door_width = r.turn_width = 0.70
    r.measured_at = {}
    r.step_across = lambda step, heading, off, face=None: None
    try:
        r.door_velocity(rl.Step(do="door", door="D8", heading_deg=-90, until=-2.56, label="D8 out"))
    except (dr.Stop, dr.DoorBlocked):
        pass
    aim = [e for e in r.logged if e.get("kind") == "door_aim"][0]
    assert aim["squared_deg"] == pytest.approx(yaw_deg, abs=0.05)
    assert abs(aim["gap"]["centre"]) < 0.005                    # 1.87 m x sin 1.5° = 4.9 cm unsquared


def test_a_leaf_propped_a_little_off_the_way_is_not_taken_for_a_wall(monkeypatch):
    # 8 ต.ค., D8 in, three runs: the leaf (x -1.987 at the wall, -2.003 0.44 m on:
    # ~2° into the passage) passed the 4° "wall along the way" gate; yaw_corr went
    # -1..-2.4°, the pass steered the robot that much left of where it believed,
    # and it crossed the frame 1.4-3.7 cm from the west jamb; the next stop found
    # the heading 4.7° out. The passage's own edges (leaf, jambs, stop) are never
    # a wall to take the heading from. Measured geometry, leaf 2.5° off.
    sim = _SimBase((-2.434, -2.45, math.pi / 2), seed=1)
    w = d8_world(west=-2.849, east=-1.984, leaf_x=-1.987)
    w[6] = (-1.987, -1.60, -1.987 - 0.88 * math.tan(math.radians(2.5)), -0.72)
    sim.world = w
    dr, r = _door_runner(sim, monkeypatch)
    monkeypatch.setattr(dr, "DOOR_STEER_IN_MAX_M", 0.0)
    r.route = rl.Route(name="d8", anchors={}, doors={"D8": rl.Door("y", -1.633, -2.85, -2.05, leaf="hi")}, steps=[])
    r.door_width = r.turn_width = 0.70
    r.door_min_margin = 0.03
    r.measured_at = {"x": (sim.odom[0], sim.odom[1])}
    r.door_velocity(rl.Step(do="door", door="D8", heading_deg=90, until=0.25, label="D8 in"))
    door = [e for e in r.logged if e.get("kind") == "door"][-1]
    yaw_corr = [t[4] - t[3] for t in door["trace"] if isinstance(t, (list, tuple))]
    assert door["ok"]
    assert max(abs(c) for c in yaw_corr) < 1.0, max(yaw_corr, key=abs)          # 2.5° unfiltered


# -- each door's middle, remembered across runs (drive_route DOOR_MEM_*, 9 ต.ค.) --------------
# Every first aim at D8 and D11 since 8 ต.ค. saw one edge + the drawing: the aim trusted
# the lane, and the lane is the DRAWING's middle. D8 from the corridor is 5.9 cm east of
# it by the jambs, and all five passes went in 6.8 cm west of the jambs' middle.

def _decide_remember(monkeypatch, robot_x, remember, aims, seed=(-2.33,) * 5, memory=None):
    """door_velocity up to the DoorPass it builds, D8 from the corridor (drawn middle
    -2.38), the robot at `robot_x` with x just measured; the route seeds D8@90 with
    `seed`, `memory` the --door-memory file."""
    sim = _SimBase((robot_x, -2.56, NORTH))
    dr, r = _door_runner(sim, monkeypatch)
    r.route.door_seed = {"D8@90": tuple(seed)}
    r.door_memory_path = memory
    r.measured_at = {"x": (robot_x, -2.56)}
    it = iter(aims)
    monkeypatch.setattr(rl, "opening_ahead", lambda *a, **k: next(it))
    steps = []

    def step_across(step, heading, off, face=None):
        steps.append(off)
        for st in (sim.true, sim.odom):
            st[0] -= off * -math.sin(heading)
    r.step_across = step_across

    def built(**kw):
        raise _Decided(kw)
    monkeypatch.setattr(dr.rl, "DoorPass", built)
    with pytest.raises(_Decided) as got:
        r.door_velocity(rl.Step(do="door", door="D8", heading_deg=90, until=START_ROOM, label="D8 in",
                                remember=remember))
    return steps, got.value.args[0], r


@pytest.mark.parametrize("robot_x,want_steps", [(-2.38, [0.05]), (-2.33, [])])
def test_a_one_edge_aim_goes_by_the_middle_the_doors_own_passes_found(monkeypatch, robot_x, want_steps):
    # the one edge agrees with the drawing (ONE_EDGE_0): before, the lane on the
    # drawing's middle went in as it stood. Remembered 5 cm east: on the drawing's
    # middle it steps the 5 cm; on the remembered one it goes in -- and the pass
    # starts from the remembered middle, not the drawing's (corr -0.05: the robot's
    # left is west going north)
    steps, kw, r = _decide_remember(monkeypatch, robot_x, True, [ONE_EDGE_0, ONE_EDGE_0])
    assert steps == [pytest.approx(s, abs=0.002) for s in want_steps]
    assert kw["corr"] == pytest.approx(-0.05, abs=1e-6)
    prior = [e for e in r.logged if e.get("kind") == "door_prior"]
    assert prior and prior[0]["middle"] == pytest.approx(-2.33) and prior[0]["seed"] == 5
    assert [e["prior"] for e in r.logged if e.get("kind") == "door_aim"][0] == pytest.approx(-0.05)


def test_a_door_not_marked_remember_keeps_the_drawing(monkeypatch, tmp_path):
    # D11: the jambs' middle is not its passage's (the leaf, calibrate_from_runs) --
    # seeded or remembered, a door step without `remember` goes by the drawing as before
    mem = tmp_path / "door_memory.json"
    mem.write_text(json.dumps({"format": 1, "doors": {"D8@90": [
        {"at": -2.33, "t": time.time(), "frame": "x"}] * 5}}), encoding="utf-8")
    steps, kw, r = _decide_remember(monkeypatch, -2.38, False, [ONE_EDGE_0], memory=mem)
    assert steps == [] and kw["corr"] is None
    assert not any(e.get("kind") == "door_prior" for e in r.logged)


def test_the_run_s_own_fresher_word_beats_the_remembered_middle(monkeypatch):
    # both edges measured now, on the drawing's middle: that, not the memory 5 cm east
    both = {"face": 0.88, "lo": -0.43, "hi": 0.43, "width": 0.86, "centre": 0.0, "edges": 2}
    steps, kw, r = _decide_remember(monkeypatch, -2.38, True, [both])
    assert steps == [] and kw["corr"] is None


def _dr_module():
    from pathlib import Path
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "drive_route_mem_test", Path(__file__).resolve().parents[1] / "scripts" / "drive_route.py")
    dr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dr)
    return dr


def test_the_remembered_middle_is_trusted_only_within_its_bounds():
    dr = _dr_module()
    drawn = -2.45
    assert dr.door_middle_prior([-2.39, -2.39], drawn)[0] is None                 # under DOOR_MEM_MIN_N
    assert dr.door_middle_prior([-2.39] * 3, drawn)[0] == pytest.approx(-2.39)
    assert dr.door_middle_prior([-2.36, -2.39, -2.42, -2.36, -2.42], drawn)[0] is None    # 3 cm apart: spread
    assert dr.door_middle_prior([-2.33] * 5, drawn)[0] is None                    # 12 cm off: not this door
    # one bad pass moves it by one rank, never by its own size (the four alone: -2.3905)
    assert dr.door_middle_prior([-2.39, -2.391, -2.392, -2.389, -2.25], drawn)[0] == pytest.approx(-2.39)
    # the newest five: the passes since replace the seed
    assert dr.door_middle_prior([-2.39] * 5 + [-2.41] * 5, drawn)[0] == pytest.approx(-2.41)


def test_learned_middles_count_only_from_this_frame_and_this_month(monkeypatch, tmp_path):
    dr = _dr_module()
    route = rl.Route(name="d8", anchors={}, doors={"D8": rl.Door("y", -1.633, -2.85, -2.05)}, steps=[],
                     door_seed={"D8@90": (-2.39,) * 5})
    fid = dr.route_frame_id(route)
    now = time.time()
    rows = ([{"at": -2.40, "t": now - 3600, "frame": fid}] * 3                    # this frame, today
            + [{"at": -2.30, "t": now - 3600, "frame": "another"}] * 5          # the route's walls moved since
            + [{"at": -2.30, "t": now - 40 * 86400, "frame": fid}] * 5)       # too old
    mem = tmp_path / "m.json"
    mem.write_text(json.dumps({"format": 1, "doors": {"D8@90": rows}}), encoding="utf-8")
    r = dr.Runner.__new__(dr.Runner)
    r.route, r.door_memory_path, r.logged = route, mem, []
    r.log = lambda **e: r.logged.append(e)
    step = rl.Step(do="door", door="D8", heading_deg=90, until=0.25, remember=True)
    prior, info = r._door_prior(step, route.doors["D8"], math.pi / 2, "x")
    assert (info["seed"], info["learned"]) == (5, 3)
    assert info["middle"] == pytest.approx(-2.40)              # last five: 2 seeds, 3 learned
    assert prior == pytest.approx(-(-2.40 + 2.45))
    # a different door frame: the seed alone (the seed is the route's own)
    route2 = rl.Route(name="d8", anchors={}, doors={"D8": rl.Door("y", -1.633, -2.86, -2.05)}, steps=[],
                      door_seed={"D8@90": (-2.39,) * 5})
    r.route = route2
    assert r._door_prior(step, route2.doors["D8"], math.pi / 2, "x")[1]["learned"] == 0


def _remembering_pass(monkeypatch, tmp_path, measured_x, memory_text=None):
    """A real pass through the simulated D8 (passage -2.80..-1.94: middle -2.37) from
    the corridor with --door-memory."""
    sim = _SimBase((-2.37, -2.45, NORTH))
    dr, r = _door_runner(sim, monkeypatch)
    r.route.door_seed = {}
    mem = tmp_path / "nav" / "door_memory.json"
    if memory_text is not None:
        mem.parent.mkdir(parents=True)
        mem.write_text(memory_text, encoding="utf-8")
    r.door_memory_path, r.log_name = mem, "20261010-090000_floor2.jsonl"
    r.measured_at = {} if measured_x is None else {"x": (measured_x, -2.45)}
    r.door_velocity(rl.Step(do="door", door="D8", heading_deg=90, until=START_ROOM, label="D8 in", remember=True))
    return dr, r, mem


def test_a_pass_is_remembered_from_the_jambs_beside(monkeypatch, tmp_path):
    dr, r, mem = _remembering_pass(monkeypatch, tmp_path, measured_x=-2.37)
    doc = json.loads(mem.read_text(encoding="utf-8"))
    (row,) = doc["doors"]["D8@90"]
    assert row["at"] == pytest.approx(-2.37, abs=0.01) and row["ticks"] >= dr.DOOR_MEM_MIN_TICKS
    assert row["frame"] == dr.route_frame_id(r.route) and row["run"] == "20261010-090000_floor2.jsonl"
    assert [e for e in r.logged if e.get("kind") == "door_middle"][0]["stored"]


def test_a_pass_with_the_coordinate_across_not_measured_is_not_remembered(monkeypatch, tmp_path):
    # odometry's drift of that run, not the door
    dr, r, mem = _remembering_pass(monkeypatch, tmp_path, measured_x=None)
    assert not mem.exists()
    log = [e for e in r.logged if e.get("kind") == "door_middle"][0]
    assert not log["stored"] and "never" in log["why"]


def test_a_memory_that_does_not_parse_is_not_written_over(monkeypatch, tmp_path):
    dr, r, mem = _remembering_pass(monkeypatch, tmp_path, measured_x=-2.37, memory_text="{not json")
    assert mem.read_text(encoding="utf-8") == "{not json"
    log = [e for e in r.logged if e.get("kind") == "door_middle"][0]
    assert not log["stored"] and "does not parse" in log["why"]


@pytest.mark.parametrize("seed", [0, 1])
def test_d8_in_on_its_remembered_middle_goes_through_centred(monkeypatch, seed):
    # D8 from the corridor as its jambs put it (middle -2.391, 0.86 m), the drawn door
    # -2.85..-2.05. Today: the lane on -2.42 left the robot at -2.435 after the stop
    # below D8 and the pass went in by the drawing -- 1.4-4.3 cm from the west jamb on
    # the robot. With the remembered middle and the lane moved (-2.376: it stands at
    # -2.391), no step and the same room both sides.
    def through(x, remember):
        sim = _SimBase((x, -2.45, NORTH), seed=seed)
        sim.world = d8_world(west=-2.821, east=-1.961, leaf_x=-1.963)
        dr, r = _door_runner(sim, monkeypatch)
        r.route = rl.Route(name="d8", anchors={}, doors={"D8": rl.Door("y", -1.633, -2.85, -2.05, leaf="hi")},
                           steps=[], door_seed={"D8@90": (-2.3805, -2.391, -2.3982, -2.3825, -2.4005)})
        r.door_width = r.turn_width = 0.70
        r.door_min_margin = 0.03
        r.measured_at = {"x": (x, -2.45)}
        r.door_velocity(rl.Step(do="door", door="D8", heading_deg=90, until=START_ROOM, label="D8 in",
                                remember=remember))
        door = [e for e in r.logged if e.get("kind") == "door"][-1]
        rows = [t for t in door["trace"] if isinstance(t, (list, tuple))]
        left = min(t[12] for t in rows if t[12] is not None)
        right = min(t[13] for t in rows if t[13] is not None)
        return door["ok"], left, right, [e for e in r.logged if e.get("kind") == "door_step_across"]
    ok, left, right, steps = through(-2.435, False)
    assert ok and right - left > 0.03                                   # today's: 2-4.6 cm from the west jamb
    ok, left, right, steps = through(-2.391, True)
    assert ok and steps == []
    assert min(left, right) > 0.055 and abs(left - right) < 0.025
