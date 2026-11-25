"""A straight line is measured by the wheels, not the board's pose (D2, 24 Nov 2026).

23 Nov, floor 1, 22:26: the walk out of the box (`forward` 1.0 m) stopped at 0.24 m on "the board's pose
jumped 0.19 m / 1° in 0.1 s", and the back onto the dock on "jumped 0.16 m while backing" -- the
localization flipping between two fits along the dock's line, not the base. What these hold down:
* `forward` with the wheels' odometry goes the whole metre through such a flip;
* without odometry the flip is taken out (on by the speed asked) and a jump past POSE_LOST_M still stops;
* odometry that is not the wheels' radians frame -- a yaw in degrees, a constant -- is dropped for the
  pose, never read as "off its own line" (`getodopose` has not been seen on this board yet);
* `_OdoGuard` (back_to) the same, and `odo_pose` reads the reply shapes it claims.
"""
from __future__ import annotations

import math
import re

import pytest

from tests.test_board_walk import _walk
from tests.test_debut_forward import _Base, _forward_walker

ODO0 = (5.0, -3.0, 1.0)                                  # the odometry's own frame: another origin and heading


class _FlipBase(_Base):
    """`_Base` whose board pose flips `flip` metres along its heading once it has driven `at` metres
    (the localization, not the base), and whose wheels report odometry in their own frame."""

    def __init__(self, flip=0.19, at=0.24, odo_deg=False, odo_stuck=False, **kw):
        super().__init__(**kw)
        self.flip, self.at, self.flipped = flip, at, False
        self.wx = self.wy = 0.0                          # the true travel (metres along / aside)
        self.odo_deg, self.odo_stuck = odo_deg, odo_stuck
        self.h0 = self.h
        self.t = 0                                       # odometry reads, for a heading wobble

    def move(self, dt):
        super().move(dt)
        self.wx += self.vx * dt
        if not self.flipped and self.wx >= self.at:
            self.flipped = True
            self.x += self.flip * math.cos(self.h)
            self.y += self.flip * math.sin(self.h)

    def odo(self):
        self.t += 1
        if self.odo_stuck:
            return ODO0
        c, s = math.cos(ODO0[2]), math.sin(ODO0[2])
        yaw = ODO0[2] + (0.004 if self.t % 2 else -0.004)        # ~0.2° of wobble
        if self.odo_deg:
            yaw = math.degrees(yaw)
        return ODO0[0] + self.wx * c, ODO0[1] + self.wx * s, yaw


def _walk_out(monkeypatch, base, odometry=True):
    bw, w, clock = _forward_walker(monkeypatch, base, {"dock": {"pose": [0, 0, 0]}, "events": True})
    monkeypatch.setattr(w, "odo", base.odo if odometry else (lambda: None))
    return bw, w


def test_the_flip_of_22_26_no_longer_stops_the_walk_out_of_the_box(monkeypatch, capsys):
    base = _FlipBase()
    bw, w = _walk_out(monkeypatch, base)
    w.step({"forward": 1.0, "speed": 0.15, "label": "out of the box"}, 1)
    out = capsys.readouterr().out
    assert base.flipped and 0.99 <= base.wx <= 1.03, base.wx
    assert re.search(r"pose jumped 0\.(19|2\d) m", out) and "on by the wheels" in out
    assert "done" in out and "by the odometry" in out
    # the line's end is where the WHEELS put her (event_walk.walked_m reads it), not the flipped pose
    end = [ln for ln in out.splitlines() if '"end": true' in ln][-1]
    assert '"by": "odometry"' in end and '"jumps": 1' in end
    assert "pose_disagree" in out                        # the board ends 0.19 m off: said, not stopped for


def test_without_odometry_the_flip_is_taken_out_and_a_lost_pose_still_stops(monkeypatch, capsys):
    base = _FlipBase()
    bw, w = _walk_out(monkeypatch, base, odometry=False)
    w.step({"forward": 1.0, "speed": 0.15}, 1)
    out = capsys.readouterr().out
    assert 0.95 <= base.wx <= 1.06, base.wx             # the jump's tick counted as the speed asked
    assert "on by the speed asked" in out and "by the pose" in out
    base = _FlipBase(flip=0.6)                            # past POSE_LOST_M: another map, not a flip
    bw, w = _walk_out(monkeypatch, base, odometry=False)
    with pytest.raises(bw.Stop, match="pose jumped 0.6"):
        w.step({"forward": 1.0, "speed": 0.15}, 1)
    assert base.vx == 0.0 and base.wx < 0.4


@pytest.mark.parametrize("bad", ["degrees", "stuck"])
def test_odometry_that_is_not_the_wheels_is_dropped_for_the_pose(monkeypatch, capsys, bad):
    base = _FlipBase(flip=0.0, odo_deg=bad == "degrees", odo_stuck=bad == "stuck")
    bw, w = _walk_out(monkeypatch, base)
    w.step({"forward": 1.0, "speed": 0.15}, 1)
    out = capsys.readouterr().out
    assert 0.99 <= base.wx <= 1.03, base.wx
    assert "off its own line" not in out
    assert "measuring by the pose" in out and "by the pose" in out


def test_a_real_turn_by_the_wheels_is_still_off_its_line(monkeypatch):
    base = _FlipBase(flip=0.0)
    real_move, real_odo = base.move, base.odo

    def move(dt):                                        # past 0.3 m the base swings, 1° a tick: both see it
        real_move(dt)
        if base.wx > 0.3:
            base.h += math.radians(1.0)

    base.move = move
    bw, w = _walk_out(monkeypatch, base)
    monkeypatch.setattr(w, "odo", lambda: (lambda o: (o[0], o[1], o[2] + base.h - base.h0))(real_odo()))
    with pytest.raises(bw.Stop, match="off its own line by the odometry"):
        w.step({"forward": 1.0, "speed": 0.15}, 1)


# ---- _OdoGuard (back_to) ----------------------------------------------------------------

class _Seq:
    def __init__(self, poses):
        self.poses, self.i = poses, -1

    def pose(self):
        self.i = min(self.i + 1, len(self.poses) - 1)
        return self.poses[self.i]


def _guard(monkeypatch, poses, odos):
    import types

    bw = _walk()
    clock = iter(range(1000, 2000))
    monkeypatch.setattr(bw, "time", types.SimpleNamespace(monotonic=lambda: float(next(clock)) * 0.1))
    o = iter(odos)
    return bw, bw._OdoGuard(_Seq(poses), 0.08, lambda: next(o))


def test_back_to_rides_a_flip_on_the_wheels(monkeypatch):
    # backing along -x at 0.04 m a read; the board flips +0.20 m at the third read
    poses = [(-7.0, -6.8, 0.0), (-7.04, -6.8, 0.0), (-6.88, -6.8, 0.0), (-6.92, -6.8, 0.0)]
    odos = [(0.0, 0.0, 0.0), (-0.04, 0.0, 0.0), (-0.08, 0.0, 0.0), (-0.12, 0.0, 0.0)]
    bw, g = _guard(monkeypatch, poses, odos)
    got = [g.pose() for _ in poses]
    assert [round(p[0], 3) for p in got] == [-7.0, -7.04, -7.08, -7.12]
    assert g.jumps == 1 and g.by == "odometry"


def test_back_to_drops_degree_odometry_and_keeps_its_place(monkeypatch):
    poses = [(-7.0, -6.8, 0.0), (-7.04, -6.8, 0.002), (-7.08, -6.8, -0.002), (-7.12, -6.8, 0.0)]
    # the same wheels, yaw in degrees: a 0.1° wobble reads as 0.1 rad = 6° -- past ODO_TURN_DISAGREE_DEG
    odos = [(0.0, 0.0, 0.0), (-0.04, 0.0, 0.1), (-0.08, 0.0, -0.2), (-0.12, 0.0, 0.1)]
    bw, g = _guard(monkeypatch, poses, odos)
    got = [g.pose() for _ in poses]
    assert g.by == "pose"
    assert any("not the wheels' frame" in n for n in g.notes)
    # on from the last pose given, no step where it changed over
    assert all(abs(got[k + 1][0] - got[k][0]) < 0.06 for k in range(len(got) - 1)), got
    assert all(abs(p[2]) < math.radians(1) for p in got), got


def test_back_to_without_odometry_stops_on_a_lost_pose(monkeypatch):
    poses = [(-7.0, -6.8, 0.0), (-7.04, -6.8, 0.0), (-6.5, -6.8, 0.0)]
    bw, g = _guard(monkeypatch, poses, [None, None, None])
    g.pose()
    g.pose()
    with pytest.raises(bw._Jumped, match="jumped 0.54"):
        g.pose()


# ---- odo_pose ---------------------------------------------------------------------------

class _Sdk:
    def __init__(self, reply):
        self.reply = reply

    def call(self, command, args=None, timeout=None):
        assert command == "getodopose"
        return self.reply


@pytest.mark.parametrize("reply", [
    {"code": 1, "x": 1.5, "y": -2.0, "yaw": 0.3},
    {"pose": {"x": 1.5, "y": -2.0, "yaw": 0.3}},
    {"value": {"x": 1.5, "y": -2.0, "yaw": 0.3}},
])
def test_odo_pose_reads_the_shapes_it_names(reply):
    from app.slamware_sdk import odo_pose

    assert odo_pose(_Sdk(reply)) == (1.5, -2.0, 0.3)


@pytest.mark.parametrize("reply", [
    {"code": -1, "codestr": "unknown command"},
    {"code": 1},
    {"x": "nan", "y": 0, "yaw": 0},
    {"value": None},
])
def test_odo_pose_raises_on_anything_else(reply):
    from app.slamware_sdk import SdkError, odo_pose

    with pytest.raises(SdkError):
        odo_pose(_Sdk(reply))


def test_the_odometry_is_not_asked_until_it_has_been_seen_on_this_board(monkeypatch):
    """USE_ODOMETRY off (24 Nov): no SDK call at all -- the pose with its jumps out measures the lines."""
    bw = _walk()
    assert bw.USE_ODOMETRY is False
    w = bw.Walker(_Base(), {"dock": {"pose": [0, 0, 0]}}, False, False)

    def no_sdk():
        raise AssertionError("asked the board for odometry")

    monkeypatch.setattr(w, "_sdk", no_sdk)
    assert w.odo() is None
