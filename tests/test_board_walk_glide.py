"""board_walk's `glide` (24 Nov 2026): our own straight lines and turns over open floor, and the reverse's stall.

Owner, after the first whole card 1 (22:19-22:24): "การเดินเข้าไปแต่ละจุดช้า เหมือนต้องวัดหรืออะไร ทำให้เนียบเลย". The frames of that
run: the board's own arrival hunted at every stop, and at the third guest its pose jumped 0.4 m and it turned 280 deg on
the spot. Walked here in the simulated lobby (scripts/floor_sim.py -- not a measurement of the robot), with the jumps
those frames had put in.
"""
from __future__ import annotations

import math

import pytest

from scripts import walk_sim as ws
from tests.test_floor_sim import fs, today  # noqa: F401 -- `today` is a fixture, used by name

DOC = {"dock": {"pose": [-7.1, -6.84, 168.0]}, "events": True, "patience_s": 6, "min_quality": 40}


def _world(today, start, **kw):  # noqa: F811
    x, y, deg = start
    w = fs.FloorWorld(today, (x, y, math.radians(deg)), seed=3, **kw)
    w.trace = []
    w.sleep(1.0)
    return w


def _walk(w, steps, max_s=240.0, **doc):
    end, reason, said, events = fs._drive(w, dict(DOC, **doc), steps, max_s)
    return end, reason, said, events


def _true(w):
    return w.base.x, w.base.y, math.degrees(w.base.yaw)


def _wrap(a):
    return (a + 180.0) % 360.0 - 180.0


def _turned(w) -> float:
    """Every degree she turned, either way, over the walk (a run round on the spot shows here)."""
    yaws = [t[3] for t in w.trace]
    return sum(abs(_wrap(math.degrees(b - a))) for a, b in zip(yaws, yaws[1:]))


def test_a_glide_goes_straight_to_the_point_and_turns_to_its_heading(today):  # noqa: F811
    w = _world(today, (-8.92, -4.24, 0.0))
    end, reason, said, _ = _walk(w, [{"glide": [[-9.53, -2.09]], "speed": 0.42, "face": 116.0, "label": "แขก 1"}])
    assert end == "done", (reason, said)
    x, y, deg = _true(w)
    assert math.hypot(x + 9.53, y + 2.09) < 0.12 and abs(_wrap(deg - 116.0)) < 4.0, (x, y, deg)
    # turned to face the point (106), then to the heading (10): no hunting, no second way round
    assert _turned(w) < 106 + 10 + 25, _turned(w)
    assert any("glided" in s for s in said), said


def test_a_turn_of_nearly_a_half_goes_one_way_once(today):  # noqa: F811
    for face in (-91.0, -89.0, 89.5):
        w = _world(today, (-9.2, -4.6, 90.0))
        end, reason, said, _ = _walk(w, [{"glide": [[-9.2, -4.6]], "face": face, "label": "t"}])
        assert end == "done", (face, reason, said)
        assert abs(_wrap(_true(w)[2] - face)) < 4.0
        assert _turned(w) < abs(_wrap(face - 90.0)) + 25, (face, _turned(w))


def _jumping(w, at_s, dx, dy, deg=0.0):
    """The board's pose jumps by (dx, dy, deg) `at_s` into the walk and stays there (its localization, not the base)."""
    real, t0 = w.pose, w.base.t

    def pose():
        x, y, h = real()
        if w.base.t - t0 >= at_s:
            return x + dx, y + dy, h + math.radians(deg)
        return x, y, h

    w.pose = pose


@pytest.mark.parametrize("dx,dy", [(0.2, 0.0), (-0.2, 0.1), (0.0, 0.3)])
def test_a_pose_jump_on_the_way_is_not_chased(today, dx, dy):  # noqa: F811
    w = _world(today, (-9.49, -2.05, 110.0))
    _jumping(w, 8.0, dx, dy)
    end, reason, said, _ = _walk(w, [{"glide": [[-8.23, -1.82]], "speed": 0.42, "face": 90.0, "label": "แขก 3"}])
    assert end == "done", (reason, said)
    # 22:23: 280 deg round on the spot. Here: to face the point, to the heading, and what the jump costs -- never a run round
    assert _turned(w) < 100 + 80 + 120, (_turned(w), said)
    x, y, _ = _true(w)
    assert math.hypot(x + 8.23, y + 1.82) < 0.75, (x, y)


def test_a_lost_pose_is_stood_out_and_a_second_and_third_end_the_walk(today, monkeypatch):  # noqa: F811
    monkeypatch.setattr(fs, "wheel_odo", lambda w: None)      # no wheels: by the board's pose
    w = _world(today, (-8.5, -2.2, -113.0))
    _jumping(w, 6.0, 0.5, 0.0)
    end, reason, said, _ = _walk(w, [{"glide": [[-9.6, -4.6]], "speed": 0.42, "label": "x"}])
    # the lidar says the board is the one that is wrong: on by where she was, and she ends where she was sent
    assert end == "done" and any("on by it" in s or "stood till it held still" in s for s in said), (reason, said)
    x, y, _ = _true(w)
    assert math.hypot(x + 9.6, y + 4.6) < 0.35, (x, y, said)

    w = _world(today, (-8.5, -2.2, -113.0))
    real, t0 = w.pose, w.base.t

    def pose():
        x, y, h = real()
        return x + 0.6 * (int((w.base.t - t0) // 4) % 2), y, h

    w.pose = pose
    end, reason, said, _ = _walk(w, [{"glide": [[-10.0, -5.6]], "speed": 0.42, "label": "x"}])
    assert end == "stop", (end, said)
    assert w.cmd == (0.0, 0.0)


def test_someone_in_the_way_is_waited_for_and_the_phone_told(today):  # noqa: F811
    person = {"at": (-9.28, -3.3), "from_s": 0.0, "to_s": 1e9, "leave_after_seen_s": 18.0}
    w = _world(today, (-9.1, -4.8, 100.0), people=[person])
    end, reason, said, events = _walk(w, [{"glide": [[-9.5, -2.0]], "speed": 0.42, "label": "x"}], patience_s=30)
    assert end == "done", (reason, said)
    assert any("held" in e for e in events) and any("moving" in e for e in events), events
    assert w.min_person_gap > 0.15, w.min_person_gap


def test_someone_who_stays_ends_the_walk_where_she_stands(today):  # noqa: F811
    w = _world(today, (-9.1, -4.8, 100.0), people=[{"at": (-9.28, -3.3), "from_s": 0.0, "to_s": 1e9}])
    end, reason, said, events = _walk(w, [{"glide": [[-9.5, -2.0]], "speed": 0.42, "label": "x"}])
    assert end == "stop" and "ahead" in reason, (end, reason)
    assert w.min_person_gap > 0.15 and w.cmd == (0.0, 0.0)


def test_a_guest_at_the_end_of_the_way_is_arrived_at(today):  # noqa: F811
    """The guest the walk goes to stands 0.85 m past the point: near enough is there, not a hold."""
    w = _world(today, (-8.92, -4.24, 0.0), people=[{"at": (-9.75, -1.27), "from_s": 0.0, "to_s": 1e9}])
    end, reason, said, events = _walk(w, [{"glide": [[-9.53, -2.09]], "speed": 0.42, "face": 105.0, "label": "แขก 1"}])
    assert end == "done", (reason, said)
    assert w.min_person_gap > 0.15, w.min_person_gap


def test_someone_in_the_sweep_of_a_turn_is_waited_for(today):  # noqa: F811
    person = {"at": (-9.2, -3.88), "from_s": 0.0, "to_s": 1e9, "leave_after_seen_s": 6.0}
    w = _world(today, (-9.2, -4.6, 20.0), people=[person])
    end, reason, said, events = _walk(w, [{"glide": [[-9.2, -4.6]], "face": 110.0, "label": "t"}])
    assert w.min_person_gap > 0.0, w.min_person_gap
    assert end in ("done", "stop"), end


def test_the_wheels_are_zeroed_whatever_ends_it(today, monkeypatch):  # noqa: F811
    w = _world(today, (-9.1, -4.6, 100.0))
    calls = {"n": 0}
    real = w.scan

    def scan(*a, **k):
        calls["n"] += 1
        if calls["n"] > 25:
            raise RuntimeError("no scan")
        return real(*a, **k)

    w.scan = scan
    with pytest.raises(RuntimeError):
        _walk(w, [{"glide": [[-9.5, -2.3]], "speed": 0.42, "label": "x"}])
    assert w.cmd == (0.0, 0.0)


# ---- the reverse's stall ------------------------------------------------------------------------------------------

def _stops(samples, at=None):
    bw = ws.load_board_walk()
    out = []
    for k in range(len(samples)):
        now = samples[k][0]
        recent = [s for s in samples[:k + 1] if now - s[0] <= bw.STALL_NET_S + 0.3]
        if bw.wheels_stopped(recent, now):
            out.append(round(now, 1))
    return out


def _line(v, secs, dt=0.1, t0=0.0, x0=0.0):
    return [(t0 + k * dt, x0 + v * k * dt, 0.0) for k in range(int(secs / dt) + 1)]


def test_a_reverse_that_stops_is_seen_in_about_a_second():
    go = _line(0.054, 5.0)
    stop = [(go[-1][0] + 0.1 * k, go[-1][1], 0.0) for k in range(1, 40)]
    stops = _stops(go + stop)
    assert stops and 1.0 < stops[0] - go[-1][0] < 1.6, stops[:3]


def test_a_jump_against_her_way_is_not_a_stop():
    """24 Nov 21:26 (walk frames): -7.872, -7.882, -7.843, -7.801, then -7.846 -- 4.5 cm outward in one read -- reversing
    at 0.054 m/s, the lidar in front going 3.29 -> 3.36 m. It was read as the wheels stopping."""
    go = _line(0.054, 5.0)
    t, x = go[-1][0], go[-1][1]
    after = [(t + 0.1 * k, x - 0.08 + 0.0054 * k, 0.0) for k in range(1, 60)]
    assert _stops(go + after) == []


def test_wheels_that_go_round_and_get_nowhere_are_stopped_too():
    go = _line(0.054, 3.0)
    t, x = go[-1][0], go[-1][1]
    slip = [(t + 0.1 * k, x + (0.012 if k % 2 else -0.012), 0.0) for k in range(1, 80)]
    stops = _stops(go + slip)
    assert stops and stops[0] - t < 5.0, stops[:3]


# ---- by the wheels (24 Nov 23:26: getodopose answers on this board) -----------------------------------------------------

def _sliding(w, gain, x_from):
    """The board's pose runs ahead along x from `x_from`, as it did walking east past the guests (0.59 m for 0.31)."""
    real = w.pose

    def pose():
        x, y, h = real()
        return (x + gain * max(0.0, x - x_from) if abs(math.degrees(h)) < 45 else x), y, h

    w.pose = pose


def test_by_the_wheels_she_gets_there_when_the_boards_pose_runs_ahead(today):  # noqa: F811
    w = _world(today, (-8.94, -1.82, 100.0))
    _sliding(w, 0.9, -8.94)
    end, reason, said, _ = _walk(w, [{"glide": [[-8.23, -1.82]], "speed": 0.42, "face": 90.0, "label": "g3"}])
    assert end == "done", (reason, said)
    assert abs(_true(w)[0] + 8.23) < 0.10, (_true(w), said)
    assert any("board_off" in s for s in said), said


@pytest.mark.parametrize("settle", [True, False])
def test_a_glide_marked_settle_false_does_not_move_on_after_it_is_there(today, monkeypatch, settle):  # noqa: F811
    """25 Nov ~05:10, card 3 at the globe (Q66-73): standing, the board put her 0.16-0.40 m from the wheels and the settle
    drove her on again, 13 s more. `settle: false` = there by the wheels is there: the settle is never asked."""
    w = _world(today, (-8.94, -1.82, 100.0))
    asked = []
    load = fs.ws.load_board_walk

    def loaded():                                        # each run loads board_walk afresh: spy on that one
        mod = load()
        orig = mod.Walker._glide_settle

        def spy(self, *a, **k):
            asked.append(a[1] if len(a) > 1 else None)
            return orig(self, *a, **k)
        mod.Walker._glide_settle = spy
        return mod

    monkeypatch.setattr(fs.ws, "load_board_walk", loaded)
    step = {"glide": [[-8.23, -1.82]], "speed": 0.42, "face": 90.0, "label": "g3"}
    if not settle:
        step["settle"] = False
    end, reason, said, _ = _walk(w, [step])
    assert end == "done", (reason, said)
    assert bool(asked) is settle, (asked, said)


def test_without_wheels_it_goes_by_the_board_as_before(today, monkeypatch):  # noqa: F811
    w = _world(today, (-8.94, -1.82, 100.0))
    monkeypatch.setattr(fs, "wheel_odo", lambda w: None)
    end, reason, said, _ = _walk(w, [{"glide": [[-8.23, -1.82]], "speed": 0.42, "face": 90.0, "label": "g3"}])
    assert end == "done" and not any("board_off" in s for s in said), (reason, said)


def test_wheels_that_stop_answering_mid_line_stop_her(today, monkeypatch):  # noqa: F811
    w = _world(today, (-8.92, -4.24, 106.0))
    real, t0 = fs.wheel_odo, w.base.t
    monkeypatch.setattr(fs, "wheel_odo", lambda w: real(w) if w.base.t - t0 < 4.0 else None)
    end, reason, said, _ = _walk(w, [{"glide": [[-9.53, -2.09]], "speed": 0.42, "label": "x"}])
    assert end == "stop" and "odometry" in reason, (end, reason)
    assert w.cmd == (0.0, 0.0)


def test_wheels_that_go_round_while_the_board_stands_still_are_stopped(today, monkeypatch):  # noqa: F811
    w = _world(today, (-8.92, -4.24, 106.0))
    real, t0 = fs.wheel_odo, w.base.t
    at = w.pose()
    w.pose = lambda: at                                     # she is against something: the board sees no movement
    end, reason, said, _ = _walk(w, [{"glide": [[-9.53, -2.09]], "speed": 0.42, "label": "x"}])
    assert end == "stop" and "not moving" in reason, (end, reason)
    assert real(w) is not None and t0 >= 0.0


# ---- someone where she is going (owner 24 Nov night) ----------------------------------------------------------------

def test_someone_standing_on_the_spot_is_whom_she_stops_for(today):  # noqa: F811
    """A guest stands 0.45 m past the point, on her line: she stops in front of them and that is there."""
    w = _world(today, (-8.92, -4.24, 106.0), people=[{"at": (-9.6, -1.66), "from_s": 0.0, "to_s": 1e9}])
    end, reason, said, events = _walk(w, [{"glide": [[-9.53, -2.09]], "speed": 0.5, "face": 116.0, "label": "g1"}],
                                      patience_s=45)
    assert end == "done", (reason, said)
    assert math.hypot(w.base.x + 9.53, w.base.y + 2.09) < GLIDE_NEAR, (_true(w), said)
    assert w.min_person_gap > 0.15 and not any("round" in e for e in events), (w.min_person_gap, events)


GLIDE_NEAR = 0.85


def test_at_half_a_metre_a_second_she_still_stops_short_of_someone_who_steps_in(today):  # noqa: F811
    w = _world(today, (-8.23, -1.82, -116.0), people=[{"at": (-9.0, -3.5), "from_s": 0.0, "to_s": 1e9,
                                                       "to": (-9.75, -3.85), "walk_s": 1e9, "cross_when_near_m": 1.4}])
    end, reason, said, events = _walk(w, [{"glide": [[-10.9, -7.18]], "speed": 0.5, "face": 45.0, "label": "end",
                                           "short_ok": 0.6}], patience_s=45)
    assert w.min_person_gap > 0.05, (w.min_person_gap, end, reason)



def test_a_board_jump_across_the_line_is_not_followed(today):  # noqa: F811
    """Review 25 Nov: a flip of the board's pose across the line (0.25-0.34 m) was followed in full."""
    for dx in (0.25, -0.3):
        w = _world(today, (-8.92, -4.24, 106.0))
        _jumping(w, 2.0, dx, 0.0)
        end, reason, said, _ = _walk(w, [{"glide": [[-9.53, -2.09]], "speed": 0.5, "face": 116.0, "label": "g1"}])
        assert end == "done", (reason, said)
        x, y, _ = _true(w)
        assert math.hypot(x + 9.53, y + 2.09) < 0.15, (dx, x, y, said)


def test_a_wheel_read_that_fails_is_a_standstill_not_the_last_pose_driven_on(today, monkeypatch):  # noqa: F811
    w = _world(today, (-8.92, -4.24, 106.0))
    real, t0, n = fs.wheel_odo, w.base.t, {"k": 0}

    def flaky(w):
        n["k"] += 1
        return None if 3.0 < w.base.t - t0 < 4.5 else real(w)

    monkeypatch.setattr(fs, "wheel_odo", flaky)
    end, reason, said, _ = _walk(w, [{"glide": [[-9.53, -2.09]], "speed": 0.5, "face": 116.0, "label": "g1"}])
    assert end in ("done", "stop"), end
    x, y, _ = _true(w)
    assert math.hypot(x + 9.53, y + 2.09) < 0.20 or "odometry" in reason, (x, y, reason)
