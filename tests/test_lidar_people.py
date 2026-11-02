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
