"""LIDAR_PEOPLE: people the lidar sees round a standing robot, and the check
that ties the lidar to the head camera (owner 31 Oct, round 3: "ตรวจสอบโดย
เช็คเรดาห์ เลเซอร์ และภาพประกอบกัน ทำให้แม่นยำขึ้นไหม").

Synthetic scans of a room (walls, a desk's legs, the dock) with people's legs
put in and taken out; the camera check against a simulated head whose lens is
NOT the 70 degrees facewatch assumes and whose lidar counts angles the other
way round -- the two things nobody has checked on the robot. Nothing reaches
a robot.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import math

import numpy as np
import pytest

from app import facewatch, focus, head_follow, lidar_people as lp
from app.config import settings

STEP = 0.25            # degrees between returns (the board gives ~0.23)


def _room(extra=()):
    """A 6 x 5 m room around the robot, a desk's two legs, plus `extra`
    circles (x, y, radius) in front of the walls. Angles + = left."""
    circles = [(0.9, -1.2, 0.03), (0.9, -0.6, 0.03)] + list(extra)
    points = []
    a = -180.0
    while a < 180.0:
        rad = math.radians(a)
        dx, dy = math.cos(rad), math.sin(rad)
        best = min(t for t in (
            (3.0 / dx) if dx > 1e-9 else math.inf, (-3.0 / dx) if dx < -1e-9 else math.inf,
            (2.5 / dy) if dy > 1e-9 else math.inf, (-2.5 / dy) if dy < -1e-9 else math.inf))
        for cx, cy, r in circles:
            proj = cx * dx + cy * dy
            if proj <= 0:
                continue
            miss = math.hypot(cx - proj * dx, cy - proj * dy)
            if miss <= r:
                best = min(best, proj - math.sqrt(r * r - miss * miss))
        points.append({"angle": rad, "distance": best, "valid": best < 12})
        a += STEP
    return points


def _legs(bearing_deg, dist):
    rad = math.radians(bearing_deg)
    x, y = dist * math.cos(rad), dist * math.sin(rad)
    nx, ny = -math.sin(rad), math.cos(rad)                # sideways
    return [(x + 0.1 * nx, y + 0.1 * ny, 0.06), (x - 0.1 * nx, y - 0.1 * ny, 0.06)]


POSE = {"x": 1.0, "y": 2.0, "yaw": 0.3}


def _settle(tracker, t0=0.0, n=5):
    for i in range(n):
        tracker.update(_room(), POSE, t0 + i)
    return t0 + n


def test_the_room_is_background_and_a_person_is_not():
    tr = lp.Tracker()
    t = _settle(tr)
    assert tr.update(_room(), POSE, t) == [], "walls, the desk's legs: nobody"
    people = tr.update(_room(_legs(40.0, 1.0)), POSE, t + 1)
    assert len(people) == 1, people
    assert people[0].bearing == pytest.approx(40.0, abs=3.0)
    assert people[0].distance == pytest.approx(1.0, abs=0.12)
    # two people: nearest first
    two = tr.update(_room(_legs(40.0, 1.0) + _legs(-70.0, 0.6)), POSE, t + 2)
    assert [round(p.bearing / 10) for p in two] == [-7, 4]
    # gone again
    assert tr.update(_room(), POSE, t + 3) == []


def test_somebody_who_never_moves_joins_the_background():
    tr = lp.Tracker()
    t = _settle(tr)
    assert len(tr.update(_room(_legs(10.0, 1.2)), POSE, t)) == 1
    for i in range(int(lp.BG_WINDOW_S) + 2):
        found = tr.update(_room(_legs(10.0, 1.2)), POSE, t + 1 + i)
    assert found == [], "the window forgot the wall behind them (the camera has them by then)"


def test_the_robot_moving_throws_the_background_away():
    tr = lp.Tracker()
    t = _settle(tr)
    moved = dict(POSE, x=1.2)
    assert tr.update(_room(_legs(40.0, 1.0)), moved, t) == [], "a new place: nothing is new yet"
    assert len(tr.history) == 1


def test_one_scan_that_saw_through_a_cupboard_does_not_make_it_a_person():
    """4 Nov, the robot's dock room: 4-7 "people" round an empty room. Its
    real scans: a bin with a cupboard at ~1 m read far (or nothing) on 1 scan
    in 50 -- and the farthest return made the wall behind it the background
    for the whole window."""
    pole = [(0.5, 0.8, 0.15)]                     # person-sized, and it stays
    tr = lp.Tracker()
    found = []
    for i in range(60):
        seen_through = i == 20
        found.append(tr.update(_room([] if seen_through else pole), POSE, i))
    assert found[25:] == [[]] * 35, "the pole is furniture, not somebody"
    # ...and somebody who walks up afterwards is still found
    people = tr.update(_room(pole + _legs(120.0, 1.0)), POSE, 61)
    assert len(people) == 1 and people[0].bearing == pytest.approx(120.0, abs=3.0)


def test_a_cupboard_edge_wandering_into_the_next_bin_is_no_person():
    """The rest of the dock room's false people: a cupboard's edge at 2 m in
    the next bin on half the scans -- that bin's usual return is the wall,
    its own neighbour's is the cupboard."""
    def cupboard(edge_deg):
        top = 2.0 * math.tan(math.radians(edge_deg))
        return [(2.0, y / 100.0, 0.012) for y in range(30, int(top * 100) + 1)]
    tr = lp.Tracker()
    found = []
    for i in range(60):
        found.append(tr.update(_room(cupboard(23.5 if i % 2 else 25.5)), POSE, i))
    assert found[5:] == [[]] * 55


def test_a_wall_or_a_lone_return_is_no_person():
    tr = lp.Tracker()
    t = _settle(tr)
    # a big box pushed in front of the robot: wider than a body
    box = [(1.2, y / 10, 0.06) for y in range(-6, 7)]
    assert tr.update(_room(box), POSE, t) == []


# ---- the line between the lidar and the camera ------------------------------------

def _pairs(a, b, n=30, noise=0.8, head=None, seed=1):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        cam = rng.uniform(-25, 25)
        h = 0.0 if head is None else head[i % len(head)]
        out.append((cam, a * cam + b + rng.normal(0, noise), h))
    return out


def test_the_check_reads_the_cameras_real_width_and_where_it_points():
    fit = lp.solve(_pairs(0.8245, 3.0))
    assert fit["trusted"] is True, fit
    assert fit["a"] == pytest.approx(0.8245, abs=0.03) and fit["b"] == pytest.approx(3.0, abs=0.6)
    assert fit["hfov_deg"] == pytest.approx(60.0, abs=2.0)


def test_a_lidar_that_counts_the_other_way_is_still_a_line():
    fit = lp.solve(_pairs(-1.0, 0.0))
    assert fit["trusted"] is True and fit["a"] == pytest.approx(-1.0, abs=0.03)
    # a person the lidar puts at -30 is +30 in the camera's (and the head's) terms
    assert lp.camera_from_lidar(fit, -30.0) == pytest.approx(30.0, abs=1.0)


@pytest.mark.parametrize("pairs,why", [
    (_pairs(1.0, 0.0, n=10), "few_pairs"),
    ([(0.5 * (i % 3), 0.5 * (i % 3), 0.0) for i in range(30)], "no_spread"),
    (_pairs(1.0, 0.0, noise=12.0), "disagree"),
    (_pairs(3.0, 0.0), "scale"),
])
def test_a_check_that_does_not_hold_steers_nothing(pairs, why):
    fit = lp.solve(pairs)
    assert fit["trusted"] is False and fit["why"] == why
    assert lp.camera_from_lidar(fit, 10.0) is None


def test_a_head_sign_the_lidar_disagrees_with_is_named():
    """camera = face + head as the sweep believed; the lidar sees face - head
    (the sweep's sign was wrong): head_ratio comes out near -1."""
    rng = np.random.default_rng(4)
    pairs = []
    for i in range(40):
        face, head = rng.uniform(-10, 10), rng.uniform(-15, 15)
        pairs.append((face + head, face - head + rng.normal(0, 0.5), head))
    fit = lp.solve(pairs)
    assert fit["head_ratio"] == pytest.approx(-1.0, abs=0.2)
    assert fit["trusted"] is False and fit["why"] == "head_sign"


# ---- the head, with a lidar ------------------------------------------------------

cv2 = pytest.importorskip("cv2", reason="no OpenCV: the sweep in the end-to-end lidar check is NOT run")
from tests.test_head_calibration import Robot, W, _run, _wire  # noqa: E402


@pytest.fixture
def bench(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "head_follow", True)
    monkeypatch.setattr(settings, "head_calibrate", True)
    monkeypatch.setattr(settings, "lidar_people", True)
    monkeypatch.setattr(settings, "head_pan_full_deg", 20.0)
    monkeypatch.setattr(settings, "head_tilt_full_deg", 20.0)
    monkeypatch.setattr(settings, "head_pan_max", 60)
    monkeypatch.setattr(settings, "head_tilt_down_max", 40)
    monkeypatch.setattr(settings, "head_tilt_up_max", 20)
    monkeypatch.setattr(settings, "head_search_tilt", 30)
    monkeypatch.setattr(settings, "head_search_rms", 0.06)
    monkeypatch.setattr(head_follow, "CAL_PATH", tmp_path / "head_calibration.json")
    rows = []
    from app import turnlog

    monkeypatch.setattr(turnlog, "record", lambda kind, **f: rows.append((kind, f)))
    head_follow.reset()
    lp.reset()
    yield rows
    head_follow.reset()
    lp.reset()


class Guest:
    """One person: where they really stand (+ = left, true degrees), seen by
    the simulated head camera (its real lens) and by a lidar that counts
    angles the other way round."""

    def __init__(self, robot, where=10.0, dist=1.2, lidar_sign=-1.0):
        self.robot, self.where, self.dist, self.sign = robot, where, dist, lidar_sign
        self.in_camera = True
        self.voice = (None, None)

    def install(self, monkeypatch):
        r = self.robot

        def tracks():
            if not self.in_camera:
                return []
            bearing = self.where - r.angle()
            cx = W / 2 - r.focal * math.tan(math.radians(bearing))
            if getattr(r, "mirrored", False):
                cx = W - cx                    # the robot's head camera: a left-right flipped picture
            return [(1, (cx - 20, 95.0, cx + 20, 145.0), None, r.t)]

        def attention(t=None):
            found = tracks()
            if not found:
                return {"bearing": None, "source": None, "track": None}
            (_tid, (x1, _y1, x2, _y2), _k, _t), = found
            return {"bearing": facewatch.bearing_deg((x1 + x2) / 2, W), "source": "face", "track": 1}

        def people(now=None, max_age=lp.FRESH_S):
            return [lp.Person(bearing=self.sign * self.where, distance=self.dist, width=0.3, points=12, t=r.t)]

        monkeypatch.setattr(facewatch, "latest_tracks", tracks)
        monkeypatch.setattr(facewatch, "latest_frame_size", lambda: (W, 240))
        monkeypatch.setattr(focus, "attention", attention)
        monkeypatch.setattr(focus, "last_voice", lambda: self.voice)
        monkeypatch.setattr(lp, "people", people)


def test_a_guest_walking_about_teaches_the_check_then_the_lidar_finds_them_under_the_camera(bench, monkeypatch):
    """The real lens is 60 deg (facewatch assumes 70) and the lidar counts the
    other way: the sweep, then the pairs, then a voice with no face."""
    robot = Robot(25.0, 20.0, hfov=60.0)
    call, follow = _wire(monkeypatch, robot)
    guest = Guest(robot, where=0.0)
    guest.install(monkeypatch)
    _run(follow, robot, 1.0)                             # the sweep
    assert head_follow.CAL_PATH.exists()
    for where in [-18, -10, -2, 6, 14, 20, 12, 4, -6, -14] * 4:
        guest.where = float(where)
        _run(follow, robot, 2.0)
    fit = head_follow.lidar_fit()
    assert fit is not None and fit["trusted"] is True, [f for k, f in bench if k == "head_lidar_check"]
    assert fit["a"] == pytest.approx(-math.tan(math.radians(30)) / math.tan(math.radians(35)), abs=0.06)
    assert fit["hfov_deg"] == pytest.approx(60.0, abs=3.0)
    saved = json.loads(head_follow.CAL_PATH.read_text(encoding="utf-8"))
    assert saved["lidar"]["trusted"] is True, "the next process steers by it from its first call"
    # now they step under the camera, to the robot's left, and speak up
    guest.in_camera = False
    guest.where, guest.dist = 18.0, 0.7
    _run(follow, robot, head_follow.IDLE_S + 1.0)        # the head goes home first
    guest.voice = (robot.t, 0.09)
    _run(follow, robot, 0.5)
    look = [f for f in robot.sent if f["type"] == "head_look"][-1]
    true_turn = look["pan"] / 100.0 * 25.0
    assert true_turn == pytest.approx(18.0, abs=4.0), "turned toward where the lidar saw them"
    assert look["tilt"] == 30, "close: looks down too"
    assert [f for k, f in bench if k == "head_look"][-1]["why"] == "lidar"


def test_without_a_check_a_voice_with_no_face_only_looks_down(bench, monkeypatch):
    head_follow.CAL_PATH.write_text(json.dumps({"pan_deg_per_100": 25.0, "tilt_deg_per_100": 20.0}),
                                    encoding="utf-8")
    robot = Robot(25.0, 20.0)
    call, follow = _wire(monkeypatch, robot)
    guest = Guest(robot, where=25.0, dist=0.7)
    guest.in_camera = False
    guest.install(monkeypatch)
    guest.voice = (robot.t, 0.09)
    _run(follow, robot, 0.5)
    looks = [(f["pan"], f["tilt"]) for f in robot.sent if f["type"] == "head_look"]
    assert looks == [(0, 30)], "a lidar never compared with the camera could have left and right swapped"


def test_with_the_lens_measured_no_fit_aims_it_is_a_record(monkeypatch):
    """With the robot's lens measured (19 deg, mirrored) the measured facts aim; a fit only says
    whether it agrees. Review 9 Nov: a band widened to the 19 deg lens alone would have trusted a
    WRONG-signed fit from 6 Nov (09:14:59: a +0.31, b -16), saved it, and turned every look away."""
    monkeypatch.setattr(settings, "robot_camera_hfov_measured", 19.0)
    monkeypatch.setattr(settings, "robot_camera_mirrored", True)
    k = lp.expected_scale()
    assert k == pytest.approx(math.tan(math.radians(9.5)) / math.tan(math.radians(35.0)), abs=1e-6)
    agrees = lp.solve(_pairs(-k, 1.0))
    assert agrees["trusted"] is False and agrees["why"] == "lens_measured", agrees
    assert agrees["hfov_deg"] == pytest.approx(19.0, abs=1.5)
    other_way = lp.solve(_pairs(k, 1.0))
    assert other_way["trusted"] is False and other_way["why"] == "sign"
    guess_width = lp.solve(_pairs(0.8245, 3.0))
    assert guess_width["trusted"] is False and guess_width["why"] == "scale"
    assert lp.camera_from_lidar(agrees, 10.0) is None


def test_without_a_measured_lens_the_check_expects_the_guess(monkeypatch):
    monkeypatch.setattr(settings, "robot_camera_hfov_measured", 0.0)
    assert lp.expected_scale() == 1.0
    assert lp.solve(_pairs(-0.24, 1.0))["why"] == "scale"


@pytest.mark.parametrize("pan_deg,mirrored", [(37.0, True), (37.0, False), (-37.0, True), (-37.0, False)])
def test_with_the_lens_measured_a_voice_with_no_face_turns_to_the_lidar_person(bench, monkeypatch, pan_deg,
                                                                               mirrored):
    """9 Nov, the robot's log: the check never held, so a voice at the robot with no face in view
    only ever looked down -- while the person stood beside the robot. The robot's lens is measured
    (19 deg, mirrored, 0.37 deg a percent): turn to the lidar's person on that. Every way "+" can
    turn and the picture can be flipped, the sweep's sign and the mirroring say which way to turn."""
    monkeypatch.setattr(settings, "robot_camera_hfov_measured", 19.0)
    monkeypatch.setattr(settings, "robot_camera_mirrored", mirrored)
    robot = Robot(pan_deg, 20.0, hfov=19.0, mirrored=mirrored)
    call, follow = _wire(monkeypatch, robot)
    guest = Guest(robot, where=15.0, dist=0.7, lidar_sign=1.0)      # the robot's lidar counts to the left
    guest.in_camera = False
    guest.install(monkeypatch)
    _run(follow, robot, 1.0)                                          # the sweep
    assert head_follow.CAL_PATH.exists() and head_follow.lidar_fit() is None
    guest.voice = (robot.t, 0.09)
    _run(follow, robot, 0.5)
    look = [f for f in robot.sent if f["type"] == "head_look"][-1]
    assert look["pan"] / 100.0 * pan_deg == pytest.approx(15.0, abs=1.0), "turned toward the lidar's person"
    assert look["tilt"] == 30, "close: looks down too"
    assert [f for k, f in bench if k == "head_look"][-1]["why"] == "lidar"


def test_a_person_past_what_the_head_and_lens_reach_is_not_turned_to(bench, monkeypatch):
    """+-60 % is ~22 deg, plus 6 inside the lens's half width: someone at 50 deg is not in the
    picture from the limit either (9 Nov: 0.4-0.7 m beside the robot at 35-60 deg, much of the morning)."""
    monkeypatch.setattr(settings, "robot_camera_hfov_measured", 19.0)
    monkeypatch.setattr(settings, "robot_camera_mirrored", True)
    robot = Robot(37.0, 20.0, hfov=19.0, mirrored=True)
    call, follow = _wire(monkeypatch, robot)
    guest = Guest(robot, where=50.0, dist=0.6, lidar_sign=1.0)
    guest.in_camera = False
    guest.install(monkeypatch)
    _run(follow, robot, 1.0)
    before = len(robot.sent)
    guest.voice = (robot.t, 0.09)
    _run(follow, robot, 0.5)
    looks = [(f["pan"], f["tilt"]) for f in robot.sent[before:] if f["type"] == "head_look"]
    assert looks == [(0, 30)]


def test_the_head_and_the_walk_up_aim_with_the_same_degrees_a_percent():
    from app import face_approach

    assert head_follow.PAN_DEG_PER_PCT == face_approach.PAN_DEG_PER_PCT
    assert head_follow.LOOK_PAST_DEG == face_approach.LOOK_PAST_DEG


# ---- the measured lens on the robot's own numbers (9 Nov) ---------------------------

ROBOT_SWEEP = {"pan_deg_per_100": -94.44, "tilt_deg_per_100": 181.81}   # data/robot/head_calibration.json


def _robot_numbers(monkeypatch, sweep=ROBOT_SWEEP):
    monkeypatch.setattr(settings, "robot_camera_hfov_measured", 19.0)
    monkeypatch.setattr(settings, "robot_camera_mirrored", True)
    head_follow.CAL_PATH.write_text(json.dumps(sweep), encoding="utf-8")


def _voice_with_no_face(monkeypatch, people):
    """A loud voice at the robot, no face in the picture, `people` on the lidar: the looks sent."""
    robot = Robot(37.0, 20.0, hfov=19.0, mirrored=True)
    call, follow = _wire(monkeypatch, robot)
    guest = Guest(robot, where=0.0, dist=1.0, lidar_sign=1.0)
    guest.in_camera = False
    guest.install(monkeypatch)
    monkeypatch.setattr(lp, "people", lambda now=None, max_age=lp.FRESH_S: [
        lp.Person(bearing=b, distance=d, width=0.3, points=12, t=robot.t) for b, d in people])
    guest.voice = (robot.t, 0.09)
    _run(follow, robot, 0.5)
    return [(f["pan"], f["tilt"]) for f in robot.sent if f["type"] == "head_look"]


def test_a_saved_check_never_aims_once_the_lens_is_measured(bench, monkeypatch):
    """A fit saved before the lens was measured (or by an older build) must not win over it --
    lidar_fit feeds the head, map_check.lens and the walk-up alike."""
    _robot_numbers(monkeypatch, {**ROBOT_SWEEP, "lidar": {"trusted": True, "a": 0.309, "b": -16.03}})
    assert head_follow.lidar_fit() is None
    assert head_follow.camera_mapping()["lidar_a"] is None
    assert _voice_with_no_face(monkeypatch, [(15.0, 0.7)]) == [(41, 30)]


def test_somebody_beside_the_robot_past_the_reach_means_only_a_look_down(bench, monkeypatch):
    """The likeliest voice is the one right beside the robot (9 Nov: 0.4-0.8 m at 35-55 deg for much
    of the morning): no turn to a farther person the head could reach instead."""
    _robot_numbers(monkeypatch)
    assert _voice_with_no_face(monkeypatch, [(48.0, 0.5), (10.0, 1.3)]) == [(0, 30)]


def test_a_far_thing_beside_the_robot_does_not_block_the_turn(bench, monkeypatch):
    """9 Nov: the lidar called something at -85..-91 deg, 1.5-1.6 m a person all morning."""
    _robot_numbers(monkeypatch)
    looks = _voice_with_no_face(monkeypatch, [(-88.0, 1.45), (10.0, 1.5)])
    assert looks == [(27, 0)], "10 deg left, 27 % at 0.37 deg a percent; 1.5 m away: no look down"


# ---- HEAD_SEARCH_NEEDS_LIDAR (owner 12 Nov "ทำเลย": the look down only with somebody in reach) ----

def _needs_lidar(monkeypatch, fresh=True):
    """The switch on; the head measured (no sweep in the way) but the lens not, so the lidar never
    turns the head here -- the look down is the only look left."""
    head_follow.CAL_PATH.write_text(json.dumps(ROBOT_SWEEP), encoding="utf-8")
    monkeypatch.setattr(settings, "head_search_needs_lidar", True)
    monkeypatch.setattr(lp, "background_ready", lambda now=None: fresh)


def _skipped(rows):
    return [f for kind, f in rows if kind == "head_look" and f.get("why") == "search_skipped"]


@pytest.mark.parametrize("side", [1.0, -1.0])
def test_nobody_in_the_heads_reach_is_no_look_down_and_a_row_says_where_they_were(bench, monkeypatch, side):
    """12 Nov, the GCK mic as her ears: 39 looks down and back in 37 min, the lidar's nearest person
    mostly 60-100 deg off ahead. The lens not measured here, so no lidar turn either."""
    _needs_lidar(monkeypatch)
    assert _voice_with_no_face(monkeypatch, [(48.0 * side, 0.5)]) == []
    rows = _skipped(bench)
    assert len(rows) == 1, "once per SEARCH_EVERY_S, not every tick"
    assert rows[0]["reason"] == "beside" and rows[0]["beside_deg"] == 48.0 * side and rows[0]["beside_m"] == 0.5
    for key, value in rows[0].items():
        assert isinstance(value, (int, float)) or key in ("why", "reason"), (key, value)


@pytest.mark.parametrize("bearing", [5.0, -5.0])
def test_somebody_within_reach_still_gets_the_look_down(bench, monkeypatch, bearing):
    _needs_lidar(monkeypatch)
    assert _voice_with_no_face(monkeypatch, [(48.0, 0.5), (bearing, 0.6)]) == [(0, 30)]
    assert _skipped(bench) == []


def test_somebody_in_line_but_farther_than_the_turn_is_no_look_down(bench, monkeypatch):
    _needs_lidar(monkeypatch)
    assert _voice_with_no_face(monkeypatch, [(5.0, head_follow.LIDAR_TURN_M + 0.4)]) == []
    assert _skipped(bench)[0]["reason"] == "nobody"


def test_nobody_at_all_is_no_look_down(bench, monkeypatch):
    _needs_lidar(monkeypatch)
    assert _voice_with_no_face(monkeypatch, []) == []
    assert _skipped(bench)[0]["reason"] == "nobody"


def test_a_lidar_still_gathering_its_background_is_the_search_as_before(bench, monkeypatch):
    """After a reset (a base move, a walk, the server's start) an empty scan is a warm-up, not nobody."""
    _needs_lidar(monkeypatch, fresh=False)
    assert _voice_with_no_face(monkeypatch, [(48.0, 0.5)]) == [(0, 30)]


def test_the_switch_off_is_the_search_as_before(bench, monkeypatch):
    """The switch as conftest leaves it, a lidar that is ready and shows only somebody beside:
    with the switch off that is the look down, as before."""
    head_follow.CAL_PATH.write_text(json.dumps(ROBOT_SWEEP), encoding="utf-8")
    monkeypatch.setattr(lp, "background_ready", lambda now=None: True)
    assert settings.head_search_needs_lidar is False
    assert _voice_with_no_face(monkeypatch, [(48.0, 0.5)]) == [(0, 30)]


def test_lidar_people_off_is_the_search_as_before(bench, monkeypatch):
    _needs_lidar(monkeypatch)
    monkeypatch.setattr(settings, "lidar_people", False)
    assert _voice_with_no_face(monkeypatch, [(48.0, 0.5)]) == [(0, 30)]


def test_with_a_lock_held_the_look_down_stays(monkeypatch):
    """Review 12 Nov: the one the head follows stepped under the camera and stood still -- the lidar
    made them its background in ~80-96 s. The lock's look down must not wait for the lidar."""
    monkeypatch.setattr(settings, "head_search_needs_lidar", True)
    monkeypatch.setattr(settings, "lidar_people", True)
    monkeypatch.setattr(lp, "background_ready", lambda now=None: True)
    monkeypatch.setattr(lp, "people", lambda now=None, max_age=lp.FRESH_S: [])
    follow = head_follow.HeadFollow(object())
    assert follow._worth_looking_down(100.0) is False
    follow.lock = {"track": 1}
    assert follow._worth_looking_down(100.0) is True


def test_background_ready_needs_a_fresh_scan_and_min_stand(monkeypatch):
    tracker = lp.STATE["tracker"]
    monkeypatch.setattr(tracker, "at", 100.0)
    monkeypatch.setattr(tracker, "history", [(100.0 - lp.MIN_STAND_S + 1.0, None)])
    assert lp.fresh(100.5) and not lp.background_ready(100.5)          # warming up
    monkeypatch.setattr(tracker, "history", [(100.0 - lp.MIN_STAND_S - 1.0, None)])
    assert lp.background_ready(100.5)
    assert not lp.fresh(100.0 + lp.FRESH_S + 0.1) and not lp.background_ready(100.0 + lp.FRESH_S + 0.1)


def test_the_switch_is_off_in_code_and_on_in_robot_env():
    root = Path(__file__).resolve().parents[1]
    assert 'head_search_needs_lidar: bool = _get_bool("HEAD_SEARCH_NEEDS_LIDAR", False)' in \
        (root / "app" / "config.py").read_text(encoding="utf-8")
    assert "HEAD_SEARCH_NEEDS_LIDAR=true" in (root / "data" / "robot-server" / "robot.env").read_text(
        encoding="utf-8").splitlines()


def test_no_lidar_pan_while_an_event_walk_gets_ready(bench, monkeypatch):
    from app import event_walk

    _robot_numbers(monkeypatch)
    monkeypatch.setattr(event_walk, "walking", lambda: True)
    monkeypatch.setattr(event_walk, "moving", lambda: False)        # the arm's rest, wheels still
    assert _voice_with_no_face(monkeypatch, [(15.0, 0.7)]) == [(0, 30)]


def test_a_lidar_pan_and_the_walk_ups_looks_share_one_gap(bench, monkeypatch):
    from app import face_approach

    _robot_numbers(monkeypatch)
    face_approach.STATE["head_at"] = -math.inf
    assert _voice_with_no_face(monkeypatch, [(15.0, 0.7)]) == [(41, 30)]
    assert face_approach._looked_recently(__import__("time").monotonic()), "the pan counts as a look"


def test_a_walk_up_look_just_now_holds_the_lidar_pan(bench, monkeypatch):
    from app import face_approach

    _robot_numbers(monkeypatch)
    monkeypatch.setitem(face_approach.STATE, "head_at", __import__("time").monotonic())
    assert _voice_with_no_face(monkeypatch, [(15.0, 0.7)]) == [(0, 30)]


def test_without_the_sweep_the_measured_lens_turns_nothing(bench, monkeypatch):
    _robot_numbers(monkeypatch)
    monkeypatch.setattr(settings, "head_calibrate", False)
    assert head_follow._measured_pan(15.0) is None


@pytest.mark.parametrize("bearing", [-35.0, -28.0, -15.0, -4.0, 0.0, 6.0, 15.0, 22.0, 27.0, 29.0])
def test_the_head_and_the_walk_up_turn_the_same_way_on_the_robots_numbers(bench, monkeypatch, bearing):
    """face_approach takes "+" = left outright (`_pan_for`); the head works it out from the sweep's
    sign and the mirroring. On the robot's numbers they must give the same turn."""
    from app import face_approach

    _robot_numbers(monkeypatch)
    monkeypatch.setattr(settings, "head_follow", True)
    assert head_follow._measured_pan(bearing) == face_approach._pan_for(bearing)


def test_a_mirrored_camera_teaches_no_wrong_check_and_still_aims_right(bench, monkeypatch):
    """End to end on a mirrored 19 deg camera with the lens measured: a guest walking about makes
    pairs (the face loop follows them on the mirrored picture), no fit ever aims, and a voice with
    no face then turns to where the lidar sees them."""
    monkeypatch.setattr(settings, "robot_camera_hfov_measured", 19.0)
    monkeypatch.setattr(settings, "robot_camera_mirrored", True)
    robot = Robot(37.0, 20.0, hfov=19.0, mirrored=True)
    call, follow = _wire(monkeypatch, robot)
    guest = Guest(robot, where=0.0, lidar_sign=1.0)
    guest.install(monkeypatch)
    _run(follow, robot, 1.0)                                          # the sweep
    for where in [-8, -4, 0, 4, 8, 4, 0, -4] * 5:
        guest.where = float(where)
        _run(follow, robot, 2.0)
        assert abs(guest.where - robot.angle()) <= 9.5, "the face loop keeps them in the mirrored picture"
    checks = [f for k, f in bench if k == "head_lidar_check"]
    assert checks and not any(c["trusted"] for c in checks)
    assert head_follow.lidar_fit() is None
    guest.in_camera = False
    guest.where, guest.dist = 18.0, 0.7
    _run(follow, robot, head_follow.IDLE_S + 1.0)
    guest.voice = (robot.t, 0.09)
    _run(follow, robot, 0.5)
    look = [f for f in robot.sent if f["type"] == "head_look"][-1]
    assert look["pan"] / 100.0 * 37.0 == pytest.approx(18.0, abs=1.0)


def test_two_faces_or_two_people_make_no_pair(bench, monkeypatch):
    head_follow.CAL_PATH.write_text(json.dumps({"pan_deg_per_100": 25.0, "tilt_deg_per_100": 20.0}),
                                    encoding="utf-8")
    robot = Robot(25.0, 20.0)
    call, follow = _wire(monkeypatch, robot)
    guest = Guest(robot, where=5.0)
    guest.install(monkeypatch)
    two = [lp.Person(5.0, 1.2, 0.3, 10, robot.t), lp.Person(-40.0, 1.0, 0.3, 10, robot.t)]
    monkeypatch.setattr(lp, "people", lambda now=None, max_age=lp.FRESH_S: two)
    _run(follow, robot, 5.0)
    assert len(lp.check().pairs) == 0


def test_a_slow_board_is_one_log_line_not_a_traceback(monkeypatch, caplog):
    """4 Nov: the server's own start keeps the CPU busy, a scan times out, and
    server.log carried a 40-line traceback that read like a crash."""
    from app import robot_chassis

    async def slow(now=None):
        raise TimeoutError()

    async def stop(_seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(lp, "poll_once", slow)
    monkeypatch.setattr(lp, "enabled", lambda: True)
    monkeypatch.setattr(robot_chassis, "configured", lambda: True)
    monkeypatch.setattr(lp.asyncio, "sleep", stop)
    with caplog.at_level("INFO", logger="condo_voice.lidar_people"), pytest.raises(asyncio.CancelledError):
        asyncio.run(lp._watch())
    (rec,) = [r for r in caplog.records if "no scan" in r.getMessage()]
    assert rec.getMessage() == "lidar people: no scan (TimeoutError)" and rec.exc_info is None
