"""HEAD_CALIBRATE: the head measures itself before it follows anybody (31 Oct, round 2).

Owner, 31 Oct, round 2: one try on the robot ("จะได้ให้หุ่นลองรอบเดียว"). Two
things nobody has checked on the robot decide whether the loop turns TOWARD
a guest or away: the direction ch7 turns for "+", and whether the head
camera's picture is mirrored. These tests run the real sweep and the real
loop against a simulated head: a camera whose picture is a window on a fixed
scene at the head's pose -- with "+" turning it left, or right, at different
degrees per percent -- so the sweep has to find the sign and the scale by
itself, and the loop then has to bring a guest to the middle either way.
Nothing reaches a robot.
"""
from __future__ import annotations

import asyncio
import json
import math

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2", reason="no OpenCV: the sweep's phase correlation is NOT checked")

from app import facewatch, focus, head_follow, robot_camera, robot_face_link as link  # noqa: E402
from app.config import settings  # noqa: E402

W, H = 320, 240
FOCAL = (W / 2) / math.tan(math.radians(facewatch.ROBOT_CAMERA_HFOV_DEG / 2))


def _scene(seed=3, size=(900, 1600)):
    rng = np.random.default_rng(seed)
    noise = rng.random(size).astype(np.float32)
    return (cv2.GaussianBlur(noise, (0, 0), 4) * 4000 % 255).astype(np.uint8)


class Robot:
    """A head with a camera on it. `pan_deg`/`tilt_deg` are what "+100 %"
    REALLY does on this simulated robot (a negative pan = "+" turns it to the
    robot's right). Moves are instant; the clock is the test's."""

    def __init__(self, pan_deg=25.0, tilt_deg=20.0, *, texture=True, camera_on_head=True, hfov=None):
        self.pan_deg, self.tilt_deg = pan_deg, tilt_deg
        self.camera_on_head = camera_on_head
        # the lens it really has (facewatch assumes ROBOT_CAMERA_HFOV_DEG)
        self.focal = FOCAL if hfov is None else (W / 2) / math.tan(math.radians(hfov / 2))
        self.world = _scene() if texture else np.full((900, 1600), 128, np.uint8)
        self.pan = self.tilt = 0
        self.t = 1000.0
        self.seq = 0
        self.answer = {"accepted": True, "reason": None, "ms": 800}
        self.follow = None

    # the head
    def angle(self) -> float:                       # + = the robot's left
        return self.pan / 100.0 * self.pan_deg if self.camera_on_head else 0.0

    def elevation(self) -> float:                   # + = looking down
        return self.tilt / 100.0 * self.tilt_deg if self.camera_on_head else 0.0

    def frame(self):
        cx = self.world.shape[1] / 2 - self.focal * math.tan(math.radians(self.angle()))
        cy = self.world.shape[0] / 2 + self.focal * math.tan(math.radians(self.elevation()))
        x0, y0 = int(round(cx - W / 2)), int(round(cy - H / 2))
        return cv2.cvtColor(self.world[y0:y0 + H, x0:x0 + W], cv2.COLOR_GRAY2BGR)

    # robot_camera.feed
    def latest(self):
        self.seq += 1
        return self.seq, b"jpeg", self.t

    def decoded_for(self, seq, jpeg):
        return self.frame()

    # the face page relaying the app
    async def send_text(self, text):
        frame = json.loads(text)
        self.sent.append(frame)
        if frame["type"] != "head_look":
            return
        if self.answer["accepted"]:
            self.pan, self.tilt = frame["pan"], frame["tilt"]
        if self.follow is not None and "seq" in frame:
            self.follow.ack({"type": "head_look_ack", "seq": frame["seq"], **self.answer})


class Call:
    def __init__(self, robot):
        robot.sent = []
        self.ws = robot
        robot.headers = {"user-agent": "Mozilla/5.0 EmmaRobot/1.0"}
        link.claim(self)
        link.receive(self, {"type": "body_caps", "enabled": True, "allow_direction_known": False,
                            "gestures": {}, "speech": True, "look": True})


@pytest.fixture
def bench(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "head_follow", True)
    monkeypatch.setattr(settings, "head_calibrate", True)
    monkeypatch.setattr(settings, "head_pan_full_deg", 20.0)       # the unmeasured guess
    monkeypatch.setattr(settings, "head_tilt_full_deg", 20.0)
    monkeypatch.setattr(settings, "head_pan_max", 60)
    monkeypatch.setattr(settings, "head_tilt_down_max", 40)
    monkeypatch.setattr(settings, "head_tilt_up_max", 20)
    monkeypatch.setattr(settings, "head_search_tilt", 0)
    monkeypatch.setattr(head_follow, "CAL_PATH", tmp_path / "head_calibration.json")
    rows = []
    from app import turnlog

    monkeypatch.setattr(turnlog, "record", lambda kind, **f: rows.append((kind, f)))
    head_follow.reset()
    yield rows
    head_follow.reset()


def _wire(monkeypatch, robot):
    monkeypatch.setattr(head_follow, "_clock", lambda: robot.t)

    async def sleep(seconds):
        robot.t += seconds

    monkeypatch.setattr(head_follow, "_sleep", sleep)
    monkeypatch.setattr(robot_camera, "feed", robot)
    call = Call(robot)
    follow = head_follow.HeadFollow(call)
    robot.follow = follow
    return call, follow


def _guest_at(monkeypatch, robot, where_deg, height_deg=0.0):
    """A guest standing still at `where_deg` (+ = the robot's left): what the
    head camera's face tracker reports at the head's current pose."""
    def tracks():
        bearing = where_deg - robot.angle()
        elev = height_deg + robot.elevation()
        cx = W / 2 - robot.focal * math.tan(math.radians(bearing))
        cy = H / 2 - robot.focal * math.tan(math.radians(elev))
        return [(1, (cx - 20, cy - 25, cx + 20, cy + 25), None, robot.t)]

    def attention(t=None):
        (_tid, (x1, _y1, x2, _y2), _k, _t), = tracks()
        return {"bearing": facewatch.bearing_deg((x1 + x2) / 2, W), "source": "face", "track": 1}

    monkeypatch.setattr(facewatch, "latest_tracks", tracks)
    monkeypatch.setattr(facewatch, "latest_frame_size", lambda: (W, H))
    monkeypatch.setattr(focus, "attention", attention)
    monkeypatch.setattr(focus, "last_voice", lambda: (None, None))


def _run(follow, robot, seconds, step=0.25):
    end = robot.t + seconds
    while robot.t < end:
        asyncio.run(follow.tick(robot.t))
        robot.t += step


# ---- the arithmetic ------------------------------------------------------------

def _frames(robot, pan_pct=30, tilt_pct=25):
    out = {}
    for name, pan, tilt in (("centre", 0, 0), ("left", pan_pct, 0), ("right", -pan_pct, 0),
                            ("down", 0, tilt_pct), ("back", 0, 0)):
        robot.pan, robot.tilt = pan, tilt
        out[name] = robot.frame()
    robot.pan = robot.tilt = 0
    return out


@pytest.mark.parametrize("pan_deg,tilt_deg", [(25.0, 20.0), (-18.0, 20.0), (12.0, -15.0), (40.0, 30.0)])
def test_the_sweep_reads_degrees_and_their_sign_from_the_picture(pan_deg, tilt_deg):
    robot = Robot(pan_deg, tilt_deg)
    got = head_follow.measure(_frames(robot))
    assert got["ok"] is True, got
    assert got["pan_deg_per_100"] == pytest.approx(pan_deg, rel=0.08)
    assert got["tilt_deg_per_100"] == pytest.approx(tilt_deg, rel=0.08)


def test_a_blank_wall_says_so_instead_of_no_motion():
    got = head_follow.measure(_frames(Robot(texture=False)))
    assert got["ok"] is False and got["reason"] == "no_texture"


def test_a_camera_that_does_not_turn_with_the_head_is_no_loop():
    got = head_follow.measure(_frames(Robot(camera_on_head=False)))
    assert got["ok"] is False and got["reason"] == "no_motion"


def test_a_camera_mounted_sideways_is_caught():
    robot = Robot()
    frames = {k: np.ascontiguousarray(np.rot90(v)) for k, v in _frames(robot).items()}
    got = head_follow.measure(frames)
    assert got["ok"] is False and got["reason"] in ("axes_crossed", "no_motion")


def test_a_head_that_did_not_come_back_is_caught():
    robot = Robot()
    frames = _frames(robot)
    robot.pan = 20
    frames["back"] = robot.frame()
    got = head_follow.measure(frames)
    assert got["ok"] is False and got["reason"] == "no_return"


# ---- the sweep on a call, then the loop ------------------------------------------

@pytest.mark.parametrize("pan_deg", [25.0, -18.0], ids=["plus-turns-left", "plus-turns-right"])
def test_the_first_call_measures_then_brings_a_guest_to_the_middle(bench, monkeypatch, pan_deg):
    robot = Robot(pan_deg, 20.0)
    call, follow = _wire(monkeypatch, robot)
    _guest_at(monkeypatch, robot, 12.0, height_deg=4.0)
    _run(follow, robot, 1.0)                                  # the sweep runs in the first tick
    saved = json.loads(head_follow.CAL_PATH.read_text(encoding="utf-8"))
    assert saved["pan_deg_per_100"] == pytest.approx(pan_deg, rel=0.08)
    assert saved["tilt_deg_per_100"] == pytest.approx(20.0, rel=0.08)
    cal = [f for k, f in bench if k == "head_calibration"]
    assert len(cal) == 1 and cal[0]["ok"] is True
    sweep = [(f["pan"], f["tilt"]) for f in robot.sent if f["type"] == "head_look"]
    assert sweep[:5] == [(0, 0), (30, 0), (-30, 0), (0, 25), (0, 0)]
    _run(follow, robot, 12.0)
    assert abs(12.0 - robot.angle()) < head_follow.PAN_DEADBAND_DEG, "the guest is in the middle"
    assert follow.off is None
    assert abs(robot.pan) <= 60


def test_with_the_guess_a_reversed_head_would_turn_away(bench, monkeypatch):
    """Why the sweep exists: the same reversed head on the unmeasured +20
    stops itself (the divergence guard) instead of ever centring the guest."""
    monkeypatch.setattr(settings, "head_calibrate", False)
    robot = Robot(-18.0, 20.0)
    call, follow = _wire(monkeypatch, robot)
    _guest_at(monkeypatch, robot, 12.0)
    _run(follow, robot, 12.0)
    assert follow.off == "diverging"
    assert abs(12.0 - robot.angle()) > 12.0, "it had turned away"


def test_a_saved_sweep_is_used_and_not_repeated(bench, monkeypatch):
    head_follow.CAL_PATH.write_text(json.dumps({"pan_deg_per_100": -18.0, "tilt_deg_per_100": 20.0}),
                                    encoding="utf-8")
    robot = Robot(-18.0, 20.0)
    call, follow = _wire(monkeypatch, robot)
    _guest_at(monkeypatch, robot, -10.0)
    _run(follow, robot, 8.0)
    assert [k for k, _f in bench if k == "head_calibration"] == []
    assert abs(-10.0 - robot.angle()) < head_follow.PAN_DEADBAND_DEG
    assert head_follow.full_deg() == (-18.0, 20.0)


@pytest.mark.parametrize("bad", [{"pan_deg_per_100": 0.5, "tilt_deg_per_100": 20},
                                 {"pan_deg_per_100": "20", "tilt_deg_per_100": 20},
                                 {"pan_deg_per_100": 20}, [], "x"])
def test_a_file_without_two_usable_numbers_is_measured_again(bench, bad):
    head_follow.CAL_PATH.write_text(json.dumps(bad), encoding="utf-8")
    assert head_follow.load_calibration() is None


def test_the_switch_off_on_the_robot_is_tried_again_next_call(bench, monkeypatch):
    robot = Robot()
    robot.answer = {"accepted": False, "reason": "disabled", "ms": None}
    call, follow = _wire(monkeypatch, robot)
    _guest_at(monkeypatch, robot, 12.0)
    _run(follow, robot, 2.0)
    assert follow.off == "calibration:disabled"
    assert len([f for f in robot.sent if f["type"] == "head_look"]) == 1, "nothing moved, nothing more asked"
    assert not head_follow.CAL_PATH.exists()
    # the operator turns "สั่งท่าทางด้วยเสียง" on; the next call measures
    robot.answer = {"accepted": True, "reason": None, "ms": 800}
    call2, follow2 = _wire(monkeypatch, robot)
    _run(follow2, robot, 1.0)
    assert head_follow.CAL_PATH.exists()


def test_a_sweep_that_moved_and_failed_is_not_repeated_in_front_of_guests(bench, monkeypatch):
    robot = Robot(camera_on_head=False)
    call, follow = _wire(monkeypatch, robot)
    _guest_at(monkeypatch, robot, 12.0)
    _run(follow, robot, 1.0)
    assert follow.off == "calibration:no_motion"
    row = [f for k, f in bench if k == "head_calibration"][-1]
    assert row["ok"] is False and row["reason"] == "no_motion"
    assert len([f for f in robot.sent if f["type"] == "head_look"]) == 5
    call2, follow2 = _wire(monkeypatch, robot)            # the next call (its own page log)
    _run(follow2, robot, 3.0)
    assert follow2.off == "uncalibrated:no_motion"
    assert [f for f in robot.sent if f["type"] == "head_look"] == []


def test_a_gesture_holding_the_head_is_waited_out(bench, monkeypatch):
    robot = Robot()
    call, follow = _wire(monkeypatch, robot)
    _guest_at(monkeypatch, robot, 0.0)
    busy = {"n": 3}
    real = robot.send_text

    async def send_text(text):
        frame = json.loads(text)
        if frame["type"] == "head_look" and busy["n"] > 0:
            busy["n"] -= 1
            robot.sent.append(frame)
            follow.ack({"seq": frame["seq"], "accepted": False, "reason": "busy", "ms": None})
            return
        await real(text)

    robot.send_text = send_text
    _run(follow, robot, 1.0)
    assert head_follow.CAL_PATH.exists() and follow.off is None


def test_the_sweep_waits_for_her_to_finish_speaking(bench, monkeypatch):
    robot = Robot()
    call, follow = _wire(monkeypatch, robot)
    _guest_at(monkeypatch, robot, 0.0)
    call._play_until = robot.t + 5.0                    # her greeting is still playing
    _run(follow, robot, 3.0)
    assert [f for f in robot.sent if f["type"] == "head_look"] == []
    _run(follow, robot, 3.0)
    assert head_follow.CAL_PATH.exists()


def test_the_sweep_rows_carry_numbers_only(bench, monkeypatch):
    robot = Robot()
    call, follow = _wire(monkeypatch, robot)
    _guest_at(monkeypatch, robot, 0.0)
    _run(follow, robot, 1.0)
    (row,) = [f for k, f in bench if k == "head_calibration"]
    assert set(row) == {"ok", "reason", "pan_deg_per_100", "tilt_deg_per_100", "texture", "shifts", "frame"}
    flat = json.dumps(row)
    assert all(ch in "0123456789.-+e,[]{}\": _abcdefghijklmnopqrstuvwxyz" for ch in flat)


def test_the_sweep_waits_for_the_pages_caps_and_an_old_apk_is_said_once(bench, monkeypatch):
    robot = Robot()
    call, follow = _wire(monkeypatch, robot)
    _guest_at(monkeypatch, robot, 0.0)
    link.reset_state()                                  # the page has not sent body_caps yet
    link.claim(call)
    _run(follow, robot, 1.0)
    assert follow.off is None and robot.sent == [], "waits; the first ticks of a call are not a failure"
    link.receive(call, {"type": "body_caps", "enabled": True, "allow_direction_known": False,
                        "gestures": {}, "speech": True, "look": True})
    _run(follow, robot, 1.0)
    assert head_follow.CAL_PATH.exists()
    # an APK from before the look: stops for the call, nothing sent, no failed sweep
    head_follow.reset()
    head_follow.CAL_PATH.unlink()
    robot2 = Robot()
    call2, follow2 = _wire(monkeypatch, robot2)
    link.receive(call2, {"type": "body_caps", "enabled": True, "allow_direction_known": False,
                         "gestures": {}, "speech": True})
    _run(follow2, robot2, 1.0)
    assert follow2.off == "app_cannot_look"
    assert [f for f in robot2.sent if f["type"] == "head_look"] == []
    assert head_follow._CAL["failed"] is None


def test_a_walk_that_begins_mid_sweep_is_not_a_failed_measurement(bench, monkeypatch):
    robot = Robot()
    call, follow = _wire(monkeypatch, robot)
    _guest_at(monkeypatch, robot, 0.0)
    real = robot.send_text

    async def send_text(text):
        frame = json.loads(text)
        if frame["type"] == "head_look" and frame["pan"] == -30:     # the third look
            robot.sent.append(frame)
            follow.ack({"seq": frame["seq"], "accepted": False, "reason": "navigating", "ms": None})
            return
        await real(text)

    robot.send_text = send_text
    _run(follow, robot, 1.0)
    assert follow.off == "calibration:navigating"
    assert head_follow._CAL["failed"] is None and not head_follow.CAL_PATH.exists()
    robot.send_text = real
    call2, follow2 = _wire(monkeypatch, robot)
    _run(follow2, robot, 1.0)
    assert head_follow.CAL_PATH.exists(), "the next call measures"
