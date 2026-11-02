"""Direction of arrival from the robot's mic array (docs/listening-focus-2026-10-09.md,
input 4): the app's GCC-PHAT (DoaMath.java), its WebSocket client (DoaLink.java),
/ws/robot-doa, and the learned lag -> bearing map (app/doa.py).

The APK cannot run here, so the Java that does arithmetic or speaks the wire
protocol is COMPILED with the JDK and RUN: on synthetic 8-channel audio with
known fractional delays, and against the real endpoint served by uvicorn.
Without a JDK those tests skip and say what went unchecked. The Android-only
parts (AudioRecord, the recording callback) are source contracts. Nothing
here talks to a robot or opens a microphone.
"""
from __future__ import annotations

import json
import math
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from app import doa
from app.config import settings
from tests.test_emma_robot_body_nav_app import SRC, _javac, code_only, method, read

MATH = SRC / "DoaMath.java"
LINK = SRC / "DoaLink.java"
LISTENER = SRC / "ArrayListener.java"
FACE = SRC / "EmmaFace.java"
MAIN = SRC / "MainActivity.java"
ROBOT_UA = "Mozilla/5.0 (Linux; Android 15) EmmaRobot/0.1 EmmaArray/1"
DESK_UA = "Mozilla/5.0 (Windows NT 10.0) Chrome/130"
TOKEN = "doa-test-token"
RATE = 16000
FRAME = 1024


@pytest.fixture(autouse=True)
def _fresh_doa(monkeypatch, tmp_path):
    doa.reset_state()
    monkeypatch.setattr(doa, "_calibration_path", lambda: tmp_path / "doa_calibration.json")
    yield
    doa.reset_state()


class FakeFocus:
    """Stands in for app/focus.py (built in parallel): records what DOA tells it."""

    def __init__(self) -> None:
        self.calls: list[tuple[float, float | None, float]] = []
        self.snap: dict = {}

    def observe_doa(self, t, bearing, conf):
        self.calls.append((t, bearing, conf))

    def snapshot(self):
        return self.snap


@pytest.fixture
def focus(monkeypatch) -> FakeFocus:
    fake = FakeFocus()
    monkeypatch.setattr(doa, "_focus", lambda: fake)
    return fake


# ==================== synthetic arrays ====================


def circle(rot: float = 0.4, r: float = 0.032):
    return np.array([[r * math.cos(rot + k * math.pi / 2), r * math.sin(rot + k * math.pi / 2), 0.0]
                     for k in range(4)])


def line(ang_deg: float = 12.0, d: float = 0.035):
    a = math.radians(ang_deg)
    return np.array([[(k - 1.5) * d * math.sin(a), (k - 1.5) * d * math.cos(a), 0.0] for k in range(4)])


def vertical(rot: float = 0.9, r: float = 0.032):
    return np.array([[0.0, r * math.cos(rot + k * math.pi / 2), r * math.sin(rot + k * math.pi / 2)]
                     for k in range(4)])


GEOMETRIES = {"circle": circle(), "line": line(), "vertical": vertical()}


def arrivals(positions, azimuth_deg, elevation_deg=10.0):
    """Arrival time of a far-field source at each mic, samples (relative)."""
    a, e = math.radians(azimuth_deg), math.radians(elevation_deg)
    toward = np.array([math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)])
    return -(np.asarray(positions) @ toward) / 343.0 * RATE


def bearing_of(cal: doa.Calibration, fitted) -> float:
    s, _ = cal.predict(fitted)
    return math.degrees(math.asin(max(-1.0, min(1.0, s))))


def taught(positions, rng, n=150, outliers=0.1, noise=0.15) -> doa.Calibration:
    """A calibration taught the way the robot teaches it: faces at -30..30,
    mouths at 5..25 degrees of elevation, and every tenth pair voiced by
    somebody else while the face's lips moved."""
    cal = doa.Calibration({})
    for _ in range(n):
        face = rng.uniform(-30, 30)
        voice = rng.uniform(-90, 90) if rng.random() < outliers else face
        lags = doa.farfield_lags(positions, voice, rng.uniform(5, 25)) + rng.normal(0, noise, 6)
        fitted, _ = doa.consistent(lags, [0.7] * 6)
        cal.add(fitted, face, 0.49)
    return cal


# ==================== the estimator (no geometry known) ====================


def test_one_far_source_closes_and_two_do_not():
    rng = np.random.default_rng(1)
    clean = doa.farfield_lags(circle(), 20, 10) + rng.normal(0, 0.1, 6)
    _, closure = doa.consistent(clean, [0.8] * 6)
    assert closure < 0.15
    # two talkers: each pair's peak picked one or the other
    a, b = doa.farfield_lags(circle(), 20, 10), doa.farfield_lags(circle(), -60, 10)
    mixed = np.where(np.arange(6) % 2 == 0, a, b)
    _, closure = doa.consistent(mixed, [0.8] * 6)
    assert closure > 0.6
    bearing, conf, info = doa.estimate(None, 0.0, mixed, [0.8] * 6, learn=False)
    assert (bearing, conf, info["why"]) == (None, 0.0, "no_single_source")


@pytest.mark.parametrize("kind", sorted(GEOMETRIES))
def test_the_map_is_learned_from_faces_for_any_geometry(kind):
    """The point of the module: nothing about the mic positions is given. A
    4-mic circle, a line and a circle standing upright (a chest array) are
    all learned from (lags, face bearing) pairs with 10 % of them voiced by
    somebody else -- and the faces' sector reads to a few degrees, while a
    voice from the side reads as the side, far outside any face's 25 degrees."""
    rng = np.random.default_rng(7)
    positions = GEOMETRIES[kind]
    cal = taught(positions, rng)
    assert cal.ready, (cal.spread_deg, cal.ident, cal.sigma)
    errors = []
    for _ in range(200):
        az = rng.uniform(-30, 30)
        lags = doa.farfield_lags(positions, az, rng.uniform(5, 25)) + rng.normal(0, 0.15, 6)
        errors.append(bearing_of(cal, doa.consistent(lags, [0.7] * 6)[0]) - az)
    errors = np.abs(errors)
    assert np.median(errors) < 2.5 and np.percentile(errors, 90) < 6.0, (np.median(errors), np.percentile(errors, 90))
    for az in (70, 90, -70, -90):
        lags = doa.farfield_lags(positions, az, 15) + rng.normal(0, 0.15, 6)
        seen = bearing_of(cal, doa.consistent(lags, [0.7] * 6)[0])
        assert abs(seen) > 40 and seen * az > 0, (az, seen)


def test_outliers_are_rejected_not_averaged_in():
    """Robust by rejection: with every tenth pair from another voice, sigma
    stays near the clean fit's (down-weighting alone doubled it)."""
    clean = taught(circle(), np.random.default_rng(3), outliers=0.0)
    dirty = taught(circle(), np.random.default_rng(3), outliers=0.15)
    assert dirty.ready
    assert dirty.sigma < 1.3 * clean.sigma, (dirty.sigma, clean.sigma)
    assert dirty.inliers < len(dirty.pairs)


def test_one_spot_teaches_no_slope():
    """A customer standing dead centre all day is a point, not a map: the
    calibration stays unready and DOA says unknown, never a guess."""
    rng = np.random.default_rng(5)
    cal = doa.Calibration({})
    for _ in range(120):
        lags = doa.farfield_lags(circle(), 0.0, 10) + rng.normal(0, 0.15, 6)
        cal.add(doa.consistent(lags, [0.7] * 6)[0], rng.uniform(-1, 1), 0.49)
    assert not cal.ready
    bearing, conf, info = doa.estimate(cal, 0.0, doa.farfield_lags(circle(), 20, 10), [0.8] * 6, learn=False)
    assert (bearing, conf, info["why"]) == (None, 0.0, "uncalibrated")


def test_unknown_while_uncalibrated_or_the_robot_speaks():
    cal = taught(circle(), np.random.default_rng(2))
    lags = doa.farfield_lags(circle(), 15, 10)
    bearing, conf, info = doa.estimate(None, 0.0, lags, [0.8] * 6, learn=False)
    assert (bearing, conf, info["why"]) == (None, 0.0, "uncalibrated")
    bearing, conf, info = doa.estimate(cal, 0.0, lags, [0.8] * 6, speaking=True, learn=False)
    assert (bearing, conf, info["why"]) == (None, 0.0, "robot_speaking")
    bearing, conf, info = doa.estimate(cal, 0.0, lags, [0.8] * 6, learn=False)
    assert info["why"] == "ok" and abs(bearing - 15) < 4 and 0.3 < conf <= 1.0


def test_a_moved_array_starts_the_calibration_over():
    """Faces that keep disagreeing with the map mean the setup changed (the
    array moved, the head turned): the map is dropped and relearned rather
    than defended forever."""
    rng = np.random.default_rng(11)
    cal = taught(circle(rot=0.4), rng)
    assert cal.ready
    moved = circle(rot=0.4 + math.radians(50))
    verdicts = []
    for _ in range(200):
        face = rng.uniform(-30, 30)
        lags = doa.farfield_lags(moved, face, 15) + rng.normal(0, 0.15, 6)
        verdicts.append(cal.add(doa.consistent(lags, [0.7] * 6)[0], face, 0.49))
        if verdicts[-1] == "reset":
            break
    assert "reset" in verdicts
    for _ in range(150):
        face = rng.uniform(-30, 30)
        lags = doa.farfield_lags(moved, face, 15) + rng.normal(0, 0.15, 6)
        cal.add(doa.consistent(lags, [0.7] * 6)[0], face, 0.49)
    assert cal.ready
    assert abs(bearing_of(cal, doa.consistent(doa.farfield_lags(moved, 20, 15), [0.8] * 6)[0]) - 20) < 4


def test_the_calibration_file_round_trips_and_refuses_another_space(tmp_path):
    cal = taught(line(), np.random.default_rng(4))
    cal.fit()                         # online it refits every 8 pairs; the file refits on load
    cal.fingerprint = {"rate": 16000, "channels": [0, 1, 2, 3], "device": "Bothlent UAC Dongle"}
    doa._write(cal.to_json())
    back = doa._load({"rate": 16000, "channels": [0, 1, 2, 3], "device": "Bothlent UAC Dongle"})
    probe = doa.consistent(doa.farfield_lags(line(), 12, 10), [0.8] * 6)[0]
    assert back.ready and abs(bearing_of(back, probe) - bearing_of(cal, probe)) < 1e-3
    # another channel map or another device is another lag space: start over
    assert len(doa._load({"rate": 16000, "channels": [0, 1, 2, 4], "device": "Bothlent UAC Dongle"}).pairs) == 0
    assert len(doa._load({"rate": 16000, "channels": [0, 1, 2, 3], "device": "Other Mic"}).pairs) == 0
    # a hand-edited model is never trusted: it is re-derived from the pairs
    data = cal.to_json()
    data["model"]["g"] = [0.0] * 6
    doa._write(data)
    assert doa._load(cal.fingerprint).ready


def test_app_time_becomes_server_time_at_arrival():
    clock = doa.ClockMap()
    rng = np.random.default_rng(0)
    offset = 12345.678                 # the app's clock and ours share nothing
    for k in range(200):
        t_app = 1000.0 + k * 64.0
        true_server = t_app / 1000.0 + offset
        arrival = true_server + 0.004 + abs(rng.normal(0, 0.02))      # 4 ms floor + jitter
        got = clock.convert(t_app, arrival)
        assert got <= arrival
    assert abs(got - true_server) < 0.01


def test_calibration_truth_is_one_engaged_face_whose_mouth_moves(monkeypatch):
    monkeypatch.setattr(settings, "focus_face_min_px", 90)
    monkeypatch.setattr(settings, "focus_yaw_max_deg", 35.0)
    t = 100.0
    face = {"track": 1, "t": t, "bearing_deg": 12.0, "width_px": 140, "yaw_deg": 5.0, "mouth": 0.8}
    assert doa._truth_from_snapshot({"faces": [face]}, t) == 12.0
    # the mouth table form, by track (int or str key)
    plain = {k: v for k, v in face.items() if k != "mouth"}
    assert doa._truth_from_snapshot({"faces": [plain], "mouth": {"1": {"t": t, "activity": 0.9}}}, t) == 12.0
    assert doa._truth_from_snapshot({"faces": [plain], "mouth": {1: [t, 0.9]}}, t) == 12.0
    # a dataclass face (focus.FaceObs) with the mouth in the table
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class FaceObs:
        track: int
        t: float
        bearing_deg: float
        width_px: int
        yaw_deg: float | None
        name: str | None

    assert doa._truth_from_snapshot({"faces": [FaceObs(1, t, 12.0, 140, 5.0, None)],
                                     "mouth": {1: {"t": t, "activity": 0.9}}}, t) == 12.0
    # not the situation the camera can vouch for -> no truth
    assert doa._truth_from_snapshot({"faces": [dict(face, mouth=0.2)]}, t) is None          # lips still
    assert doa._truth_from_snapshot({"faces": [face, dict(face, track=2, bearing_deg=-20)]}, t) is None
    far_talker = {"track": 3, "t": t, "bearing_deg": -25.0, "width_px": 40, "yaw_deg": 0.0, "mouth": 0.7}
    assert doa._truth_from_snapshot({"faces": [face, far_talker]}, t) is None             # someone else talking
    assert doa._truth_from_snapshot({"faces": [dict(face, width_px=50)]}, t) is None        # not engaged
    assert doa._truth_from_snapshot({"faces": [dict(face, engaged=False)]}, t) is None      # focus said so
    assert doa._truth_from_snapshot({"faces": [dict(face, mouth_t=t - 2)]}, t) is None      # stale mouth
    assert doa._truth_from_snapshot({"faces": [face]}, t + 5) is None                       # stale face
    assert doa._truth_from_snapshot({}, t) is None and doa._truth_from_snapshot(None, t) is None


def test_truth_from_the_real_focus_module(monkeypatch):
    """Against app/focus.py itself (built in parallel): its snapshot's
    `engaged` + `mouth` view is what the calibration reads. If that shape
    changes, this goes red instead of the calibration silently never
    learning."""
    from app import focus as real

    monkeypatch.setattr(settings, "focus_face_min_px", 90)
    monkeypatch.setattr(settings, "focus_yaw_max_deg", 35.0)
    monkeypatch.setattr(settings, "focus_hold_s", 1.5)
    real._forget_everything()
    try:
        now = time.monotonic()
        real.observe_faces([real.FaceObs(1, now, 14.0, 160, 3.0, None)], now)
        real.observe_mouth(1, now, 0.9)
        assert doa._truth_from_snapshot(real.snapshot(), now) == 14.0
        assert doa._truth_at(now) == 14.0               # the pull path, through the real import
        real.observe_mouth(1, now + 0.01, 0.1)          # lips still
        assert doa._truth_at(now) is None
        real.observe_mouth(1, now + 0.02, 0.9)
        real.observe_faces([real.FaceObs(1, now + 0.03, 14.0, 160, 3.0, None),
                            real.FaceObs(2, now + 0.03, -20.0, 40, 0.0, None)], now + 0.03)
        assert doa._truth_at(now) == 14.0               # the far one is not engaged
        # Far first, then near: a face engaged within FOCUS_HOLD_S stays
        # engaged (focus review 9 Oct), so the other order would still say
        # "two faces" — the stricter answer for calibration truth.
        real.observe_faces([real.FaceObs(1, now + 0.04, 14.0, 160, 3.0, None),
                            real.FaceObs(2, now + 0.04, -20.0, 150, 0.0, None)], now + 0.04)
        assert doa._truth_at(now) is None               # two engaged faces: whose voice?
    finally:
        real._forget_everything()


def test_frames_teach_the_map_while_a_face_speaks_and_then_answer(focus):
    """The online loop end to end, server side: unknown first, pairs taken
    from the speaking face (rate-limited), then bearings with confidence."""
    rng = np.random.default_rng(9)
    pos = vertical()
    link = doa._Link(ROBOT_UA)
    link.device = "Bothlent UAC Dongle"
    base = time.monotonic()
    first = None
    for k in range(1200):                         # 77 s of one person talking, at 64 ms a frame
        now = base + 0.064 * k
        face = 25 * math.sin(k / 60.0)
        lags = doa.farfield_lags(pos, face, 12) + rng.normal(0, 0.12, 6)
        focus.snap = {"faces": [{"track": 1, "t": now, "bearing_deg": face,
                                 "width_px": 150, "yaw_deg": 0.0, "mouth": 0.9}]}
        link._frame({"type": "doa", "t_app_ms": 64.0 * k, "lags": [round(float(x), 3) for x in lags],
                     "strength": [0.8] * 6, "rms": 0.02}, now)
        if first is None:
            first = focus.calls[-1]
    assert first[1:] == (None, 0.0)
    cal = doa._STATE.calibration
    assert cal is not None and cal.ready
    # pairs at most every PAIR_EVERY_S, never one per frame
    assert len(cal.pairs) <= 0.064 * 1200 / doa.PAIR_EVERY_S + 1
    t, bearing, conf = focus.calls[-1]
    assert bearing is not None and abs(bearing - 25 * math.sin(1199 / 60.0)) < 5 and conf > 0.3
    assert abs(t - (base + 0.064 * 1199)) < 0.01


# ==================== /ws/robot-doa ====================


def _client(monkeypatch, enabled=True):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(settings, "ws_token", TOKEN)
    monkeypatch.setattr(settings, "doa_enabled", enabled)
    return TestClient(app)


def _wait_for(predicate, seconds: float = 3.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


STATUS = {"type": "doa_status", "state": "capturing", "source": "VOICE_RECOGNITION",
          "device": "Bothlent UAC Dongle", "detail": None, "rms8": [0.01] * 8, "others": 1,
          "others_silenced": False, "self_silenced": False, "zero_ms": 0, "sent": 0, "gated": 0,
          "rate": 16000, "channels": 8}


def frame(t_app_ms: float, lags=(0.5, 1.0, -0.5, 0.5, -1.0, -1.5), strength=0.8) -> dict:
    return {"type": "doa", "t_app_ms": t_app_ms, "lags": list(lags), "strength": [strength] * 6, "rms": 0.02}


def test_the_socket_demands_the_token(monkeypatch, focus):
    client = _client(monkeypatch)
    for suffix in ("", "?token=wrong"):
        with client.websocket_connect("/ws/robot-doa" + suffix, headers={"user-agent": ROBOT_UA}) as ws:
            assert ws.receive_json()["code"] == "unauthorized"
    assert focus.calls == []


def test_the_socket_is_only_for_the_robots_app(monkeypatch, focus):
    client = _client(monkeypatch)
    with client.websocket_connect("/ws/robot-doa?token=" + TOKEN, headers={"user-agent": DESK_UA}) as ws:
        assert ws.receive_json() == {"type": "error", "code": "not_robot"}
    assert doa._STATE.link is None


def test_doa_off_tells_the_app_not_to_open_the_array(monkeypatch, focus):
    client = _client(monkeypatch, enabled=False)
    with client.websocket_connect("/ws/robot-doa?token=" + TOKEN, headers={"user-agent": ROBOT_UA}) as ws:
        assert ws.receive_json() == {"type": "doa_off"}
    assert doa._STATE.link is None


def test_ready_then_frames_reach_focus_as_unknown_until_calibrated(monkeypatch, focus):
    client = _client(monkeypatch)
    with client.websocket_connect("/ws/robot-doa?token=" + TOKEN, headers={"user-agent": ROBOT_UA}) as ws:
        ready = ws.receive_json()
        assert ready["type"] == "doa_ready" and ready["channels"] == [0, 1, 2, 3] and ready["rate"] == 16000
        ws.send_text(json.dumps({"type": "doa_hello", "token": ""}))
        ws.send_text(json.dumps(frame(500.0)))               # before the app said its device: not used
        ws.send_text(json.dumps(STATUS))
        before = time.monotonic()
        for k in range(5):
            ws.send_text(json.dumps(frame(1000.0 + 64 * k)))
        assert _wait_for(lambda: len(focus.calls) == 5)
    assert all(bearing is None and conf == 0.0 for _t, bearing, conf in focus.calls)
    assert all(before - 1 < t <= time.monotonic() for t, _b, _c in focus.calls)
    assert doa._STATE.link is None                            # the slot is free again


def test_a_calibrated_server_reports_bearings(monkeypatch, focus):
    rng = np.random.default_rng(21)
    pos = circle()
    cal = taught(pos, rng)
    cal.fingerprint = {"rate": 16000, "channels": [0, 1, 2, 3], "device": "Bothlent UAC Dongle"}
    doa._STATE.calibration = cal
    lags = [round(float(x), 3) for x in doa.farfield_lags(pos, -18, 12)]
    client = _client(monkeypatch)
    with client.websocket_connect("/ws/robot-doa?token=" + TOKEN, headers={"user-agent": ROBOT_UA}) as ws:
        ws.receive_json()
        ws.send_text(json.dumps(STATUS))
        ws.send_text(json.dumps(frame(1000.0, lags)))
        assert _wait_for(lambda: len(focus.calls) == 1)
    _t, bearing, conf = focus.calls[0]
    assert abs(bearing + 18) < 4 and conf > 0.3


def test_oversize_binary_and_malformed_messages_are_dropped(monkeypatch, focus):
    client = _client(monkeypatch)
    with client.websocket_connect("/ws/robot-doa?token=" + TOKEN, headers={"user-agent": ROBOT_UA}) as ws:
        ws.receive_json()
        ws.send_text(json.dumps(STATUS))
        big = frame(1000.0)
        big["pad"] = "x" * 2000
        ws.send_text(json.dumps(big))
        ws.send_bytes(b"\x00" * 64)
        ws.send_text("not json")
        ws.send_text(json.dumps(dict(frame(1000.0), lags=[1, 2, 3])))
        ws.send_text(json.dumps(dict(frame(1000.0), lags=[1e9] * 6)))
        ws.send_text(json.dumps(dict(frame(1000.0), strength=[True] * 6)))
        ws.send_text('{"type":"doa","t_app_ms":1,"lags":[0,0,0,0,0,0],"strength":[0.5,0.5,0.5,0.5,0.5,0.5],'
                     '"rms":NaN}')
        ws.send_text(json.dumps(frame(1064.0)))               # the one good frame
        assert _wait_for(lambda: len(focus.calls) == 1)
        link = doa._STATE.link
        assert link.counts["oversize"] == 1 and link.counts["malformed"] >= 4
    assert len(focus.calls) == 1


def test_frames_beyond_the_rate_are_dropped(monkeypatch, focus):
    client = _client(monkeypatch)
    with client.websocket_connect("/ws/robot-doa?token=" + TOKEN, headers={"user-agent": ROBOT_UA}) as ws:
        ws.receive_json()
        ws.send_text(json.dumps(STATUS))
        start = time.monotonic()
        for k in range(200):
            ws.send_text(json.dumps(frame(1000.0 + k)))
        assert _wait_for(lambda: doa._STATE.link is not None
                         and doa._STATE.link.counts["frames"] + doa._STATE.link.counts["dropped_rate"] == 200)
        elapsed = time.monotonic() - start
    assert len(focus.calls) <= doa.FRAME_BURST + doa.FRAME_REFILL_PER_S * elapsed + 2
    assert len(focus.calls) >= doa.FRAME_BURST


def test_one_array_at_a_time(monkeypatch, focus):
    """There are two ZC-3588A with this APK: two lag streams interleaved
    would be noise. The second gets `busy` (it waits; it does not stop)."""
    client = _client(monkeypatch)
    with client.websocket_connect("/ws/robot-doa?token=" + TOKEN, headers={"user-agent": ROBOT_UA}) as first:
        assert first.receive_json()["type"] == "doa_ready"
        with client.websocket_connect("/ws/robot-doa?token=" + TOKEN, headers={"user-agent": ROBOT_UA}) as second:
            assert second.receive_json() == {"type": "error", "code": "busy"}
    with client.websocket_connect("/ws/robot-doa?token=" + TOKEN, headers={"user-agent": ROBOT_UA}) as again:
        assert again.receive_json()["type"] == "doa_ready"


def test_with_a_face_token_the_hello_comes_first(monkeypatch, focus):
    monkeypatch.setattr(settings, "robot_face_token", "s3cret")
    client = _client(monkeypatch)
    with client.websocket_connect("/ws/robot-doa?token=" + TOKEN, headers={"user-agent": ROBOT_UA}) as ws:
        ws.send_text(json.dumps({"type": "doa_hello", "token": "wrong"}))
        assert ws.receive_json() == {"type": "error", "code": "bad_face_token"}
    with client.websocket_connect("/ws/robot-doa?token=" + TOKEN, headers={"user-agent": ROBOT_UA}) as ws:
        ws.send_text(json.dumps({"type": "doa_hello", "token": "s3cret"}))
        assert ws.receive_json()["type"] == "doa_ready"


def test_a_silent_link_is_closed_and_frees_the_slot(monkeypatch, focus):
    monkeypatch.setattr(doa, "BEAT_S", 0.05)
    monkeypatch.setattr(doa, "SILENT_S", 0.2)
    client = _client(monkeypatch)
    with client.websocket_connect("/ws/robot-doa?token=" + TOKEN, headers={"user-agent": ROBOT_UA}) as ws:
        assert ws.receive_json()["type"] == "doa_ready"
        assert ws.receive_json() == {"type": "doa_beat"}
        assert _wait_for(lambda: doa._STATE.link is None, 3.0)


def test_the_endpoint_is_gated_in_main():
    from app import main

    body = Path(main.__file__).read_text(encoding="utf-8").split('@app.websocket("/ws/robot-doa")', 1)[1]
    body = body.split("@app.", 1)[0]
    assert body.index("await _reject_unauthorized(websocket, token)") < body.index("doa.serve_link(websocket)")


# ==================== the Java, compiled and run ====================

GCC_HARNESS = """package com.emma.robot.control;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.ShortBuffer;
import java.nio.file.Files;
import java.nio.file.Paths;

public final class DoaHarness {
    public static void main(String[] a) throws Exception {
        byte[] bytes = Files.readAllBytes(Paths.get(a[0]));
        int frame = Integer.parseInt(a[1]);
        int[] ch = {Integer.parseInt(a[2]), Integer.parseInt(a[3]), Integer.parseInt(a[4]), Integer.parseInt(a[5])};
        ShortBuffer sb = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer();
        short[] all = new short[sb.remaining()];
        sb.get(all);
        int frames = all.length / (8 * frame);
        DoaMath math = new DoaMath(frame, 16000, 16, 0.25, 200, 4000);
        DoaMath.Result r = new DoaMath.Result();
        float[][] x = new float[4][frame];
        short[] buf = new short[frame * 8];
        double[] sums = new double[8];
        for (int f = 0; f < frames; f++) {
            System.arraycopy(all, f * frame * 8, buf, 0, frame * 8);
            int peak = DoaMath.accumulate(buf, frame, sums);
            double level = DoaMath.pick(buf, frame, ch, x);
            math.analyze(x, r);
            System.out.println(DoaMath.frameJson(f, r) + "\\t" + peak + "\\t" + level);
        }
    }
}
"""

RULES_HARNESS = """package com.emma.robot.control;

public final class DoaRulesHarness {
    public static void main(String[] a) {
        System.out.println(DoaMath.statusJson("capturing", "VOICE_RECOGNITION", "Both\\"lent\\\\ UAC\\nDongle\\u0001",
                null, new double[]{0.1, 0.2, 0.3, 0.4, 0, 0, 0, 0}, 1, Boolean.FALSE, null, 0, 12, 3, 16000, 8));
        System.out.println(DoaMath.statusJson("no_array", null, null, "no USB audio input", null, 0, null, null,
                0, 0, 0, 16000, 8));
        DoaMath.Params p = new DoaMath.Params();
        p.frame = 100000; p.channels = new int[]{0, 0, 1, 2}; p.maxLag = 1e9; p.gateRms = -1; p.maxFps = 1000;
        p.fLo = 0; p.fHi = 1e9; p.statsMs = 1;
        p.clamp(16000);
        System.out.println("clamp=" + p.frame + "," + java.util.Arrays.toString(p.channels) + "," + p.maxLag + ","
                + p.gateRms + "," + p.maxFps + "," + p.fLo + "," + p.fHi + "," + p.statsMs);
        DoaMath.Params q = new DoaMath.Params();
        q.frame = 700; q.channels = new int[]{4, 5, 6, 7};
        q.clamp(16000);
        System.out.println("keep=" + q.frame + "," + java.util.Arrays.toString(q.channels));
        String[][] cases = {{"doa_ready", ""}, {"doa_off", ""}, {"doa_beat", ""}, {"error", "busy"},
                {"error", "unauthorized"}, {"error", "not_robot"}, {"error", "bad_face_token"},
                {"error", "something_new"}, {"other", ""}};
        StringBuilder out = new StringBuilder("actions=");
        for (String[] c : cases) out.append(c[0]).append('/').append(c[1]).append(':')
                .append(DoaMath.linkAction(c[0], c[1])).append(' ');
        System.out.println(out.toString().trim());
        StringBuilder back = new StringBuilder("backoff=");
        for (int f = 0; f <= 8; f++) back.append(DoaMath.backoffMs(f)).append(' ');
        System.out.println(back.toString().trim());
    }
}
"""

LINK_HARNESS = """package com.emma.robot.control;

public final class DoaLinkHarness {
    public static void main(String[] a) throws Exception {
        String url = a[0];
        int frames = Integer.parseInt(a[1]);
        DoaLink link = DoaLink.open(url, ArrayListener_UA.UA, 5000, null);
        link.sendText("{\\"type\\":\\"doa_hello\\",\\"token\\":" + DoaMath.quote("") + "}");
        String first = link.readText();
        System.out.println("first=" + first);
        org_json_free: {
            int action = DoaMath.linkAction(first.contains("doa_ready") ? "doa_ready"
                    : first.contains("\\"error\\"") ? "error" : "other",
                    first.contains("busy") ? "busy" : "x");
            System.out.println("action=" + action);
            if (action != DoaMath.LINK_READY) break org_json_free;
            link.sendText(DoaMath.statusJson("capturing", "VOICE_RECOGNITION", "Bothlent UAC Dongle", null,
                    new double[8], 1, Boolean.FALSE, Boolean.FALSE, 0, 0, 0, 16000, 8));
            DoaMath.Result r = new DoaMath.Result();
            for (int k = 0; k < 6; k++) { r.lags[k] = 0.25 * k; r.strength[k] = 0.9; }
            r.rms = 0.02;
            for (int f = 0; f < frames; f++) link.sendText(DoaMath.frameJson(1000L + 64L * f, r));
            // a long one: the 16-bit length form of the frame header
            StringBuilder pad = new StringBuilder();
            for (int k = 0; k < 300; k++) pad.append('x');
            link.sendText("{\\"type\\":\\"doa_note\\",\\"pad\\":\\"" + pad + "\\"}");
        }
        link.close();
        System.out.println("closed");
    }
}

final class ArrayListener_UA {
    static final String UA = "EmmaRobot/0.1 EmmaArray/1";
}
"""


@pytest.fixture(scope="module")
def jvm(tmp_path_factory):
    tools = _javac()
    if tools is None:
        pytest.skip("no JDK found: DoaMath (GCC-PHAT, wire JSON, param clamps) and DoaLink (the WebSocket "
                    "client) were NOT compiled or run -- their sign, normalisation and framing are unchecked")
    javac, java = tools
    work = tmp_path_factory.mktemp("doa_java")
    src = work / "src"
    src.mkdir()
    for name, text in (("DoaHarness.java", GCC_HARNESS), ("DoaRulesHarness.java", RULES_HARNESS),
                       ("DoaLinkHarness.java", LINK_HARNESS)):
        (src / name).write_text(text, encoding="utf-8")
    out = work / "out"
    subprocess.run([javac, "-encoding", "UTF-8", "-d", str(out), str(MATH), str(LINK)]
                   + [str(p) for p in src.glob("*.java")],
                   check=True, capture_output=True, encoding="utf-8", timeout=180)
    return {"java": java, "cp": str(out), "work": work}


def _run(jvm, main: str, *args: str, timeout: float = 120) -> list[str]:
    done = subprocess.run([jvm["java"], "-cp", jvm["cp"], "com.emma.robot.control." + main, *args],
                          check=True, capture_output=True, encoding="utf-8", timeout=timeout)
    return done.stdout.splitlines()


def delayed(delays, n, rng, band=(300.0, 3800.0)):
    """Band-limited noise, one copy per delay (samples, fractional), exact."""
    total = n + 8192
    spec = np.fft.rfft(rng.normal(size=total))
    f = np.fft.rfftfreq(total, 1.0 / RATE)
    spec[(f < band[0]) | (f > band[1])] = 0
    out = np.array([np.fft.irfft(spec * np.exp(-2j * np.pi * f * d / RATE), total)[4096:4096 + n]
                    for d in delays])
    return out / np.max(np.abs(out))


def write_capture(path: Path, segments, picked=(0, 1, 2, 3), seed=0) -> None:
    """8-channel S16_LE interleaved, as the Bothlent array delivers it:
    each segment's four signals on the `picked` channels, -40 dB of
    independent noise everywhere, the other channels noise only."""
    rng = np.random.default_rng(seed)
    blocks = []
    for signals in segments:
        n = signals.shape[1]
        block = rng.normal(0, 0.003, (8, n))
        for m, c in enumerate(picked):
            block[c] += 0.3 * signals[m]
        blocks.append(block)
    data = np.concatenate(blocks, axis=1)
    pcm = np.clip(np.round(data.T * 32767), -32768, 32767).astype("<i2")
    path.write_bytes(pcm.tobytes())


def _frames(lines: list[str]) -> list[dict]:
    return [json.loads(line.split("\t", 1)[0]) for line in lines]


def test_gcc_phat_recovers_known_fractional_delays(jvm):
    """lag[p] = arrival at j minus arrival at i, in samples, to a twentieth
    of a sample; one coherent source has strength near 1, uncorrelated noise
    near 0. Signals on channels 2,3,5,6 with channels=[2,3,5,6] -> the same."""
    rng = np.random.default_rng(42)
    delays = np.array([0.0, 1.3, -2.7, 0.6])
    expected = [delays[j] - delays[i] for i, j in doa.PAIRS]
    frames_each = 6
    clean = delayed(delays, FRAME * frames_each, rng)
    noise = rng.normal(size=(4, FRAME * frames_each))
    noise /= np.max(np.abs(noise))
    path = jvm["work"] / "known.raw"
    write_capture(path, [clean, noise])
    got = _frames(_run(jvm, "DoaHarness", str(path), str(FRAME), "0", "1", "2", "3"))
    assert len(got) == 2 * frames_each
    for f in got[:frames_each]:
        assert max(abs(a - b) for a, b in zip(f["lags"], expected)) < 0.1, f["lags"]
        assert min(f["strength"]) > 0.8, f["strength"]
        assert doa._valid_frame(f) is not None
    for f in got[frames_each:]:
        assert max(f["strength"]) < 0.35, f["strength"]
    picked = jvm["work"] / "picked.raw"
    write_capture(picked, [clean], picked=(2, 3, 5, 6))
    for f in _frames(_run(jvm, "DoaHarness", str(picked), str(FRAME), "2", "3", "5", "6")):
        assert max(abs(a - b) for a, b in zip(f["lags"], expected)) < 0.1


def test_digital_silence_reads_as_peak_zero(jvm):
    path = jvm["work"] / "zero.raw"
    path.write_bytes(b"\x00" * (FRAME * 8 * 2 * 2))
    lines = _run(jvm, "DoaHarness", str(path), str(FRAME), "0", "1", "2", "3")
    assert [line.split("\t")[1] for line in lines] == ["0", "0"]
    assert all(max(f["strength"]) == 0.0 for f in _frames(lines))


def test_the_whole_chain_learns_a_circle_from_synthetic_audio(jvm):
    """Audio with the delays a 4-mic circle would see -> the app's GCC-PHAT
    -> the server's calibration taught by 'faces' at -30..30 -> bearings of
    other voices in the sector and at the side."""
    rng = np.random.default_rng(8)
    pos = circle(rot=1.1)
    teach = list(range(-30, 31, 5))
    probe = [-27, -13, 4, 18, 26]
    side = [75, -80]
    segments, labels = [], []
    for az in teach + probe + side:
        segments.append(delayed(arrivals(pos, az, 10.0), FRAME * 4, rng))
        labels += [az] * 4
    path = jvm["work"] / "chain.raw"
    write_capture(path, segments)
    frames = _frames(_run(jvm, "DoaHarness", str(path), str(FRAME), "0", "1", "2", "3"))
    assert len(frames) == len(labels)
    cal = doa.Calibration({})
    for f, az in zip(frames, labels):
        if az in teach:
            fitted, closure = doa.consistent(f["lags"], f["strength"])
            assert closure < 0.2
            cal.add(fitted, az, float(np.mean(f["strength"])) ** 2)
    cal.MIN_PAIRS = 40
    assert cal.fit(), (len(cal.pairs), cal.spread_deg, cal.ident, cal.sigma)
    for f, az in zip(frames, labels):
        if az in probe:
            bearing, conf, info = doa.estimate(cal, 0.0, f["lags"], f["strength"], learn=False)
            # 52 pairs: the `pairs` part keeps confidence modest until FULL_PAIRS
            assert abs(bearing - az) < 3.0 and conf > 0.15, (az, bearing, conf, info)
        if az in side:
            bearing, _conf, _info = doa.estimate(cal, 0.0, f["lags"], f["strength"], learn=False)
            assert abs(bearing) > 55 and bearing * az > 0, (az, bearing)


def test_the_wire_json_and_the_rules(jvm):
    lines = _run(jvm, "DoaRulesHarness")
    status = json.loads(lines[0])
    assert status["device"] == 'Both"lent\\ UAC\nDongle\x01'
    clean = doa._clean_status(status)
    assert clean["state"] == "capturing" and clean["source"] == "VOICE_RECOGNITION"
    assert clean["device"] == 'Both"lent\\ UACDongle'           # control characters never reach the log
    assert clean["rms8"][:4] == [0.1, 0.2, 0.3, 0.4] and clean["others"] == 1
    assert clean["others_silenced"] is False and clean["self_silenced"] is None
    assert len(lines[0].encode("utf-8")) < doa.MAX_TEXT_BYTES
    off = doa._clean_status(json.loads(lines[1]))
    assert off["state"] == "no_array" and off["rms8"] is None and off["device"] is None
    assert lines[2] == "clamp=2048,[0, 1, 2, 3],32.0,5.0E-4,20,50.0,7600.0,1000"
    assert lines[3] == "keep=1024,[4, 5, 6, 7]"
    actions = dict(item.rsplit(":", 1) for item in lines[4].split("=", 1)[1].split())
    assert actions == {"doa_ready/": "1", "doa_off/": "2", "doa_beat/": "5", "error/busy": "4",
                       "error/unauthorized": "3", "error/not_robot": "3", "error/bad_face_token": "3",
                       "error/something_new": "3", "other/": "0"}
    assert lines[5] == "backoff=1000 2000 4000 8000 16000 32000 60000 60000 60000"
    # what the server sends, the app understands: every type in ready_frame's world
    assert doa.ready_frame()["type"] == "doa_ready"


@pytest.fixture
def live_server(monkeypatch):
    """The real handler (main.ws_robot_doa) behind uvicorn on a free port:
    the only way to check a hand-written WebSocket client is a real server."""
    import uvicorn
    from fastapi import FastAPI

    from app import main

    monkeypatch.setattr(settings, "ws_token", TOKEN)
    monkeypatch.setattr(settings, "doa_enabled", True)
    mini = FastAPI()
    mini.add_api_websocket_route("/ws/robot-doa", main.ws_robot_doa)
    server = uvicorn.Server(uvicorn.Config(mini, host="127.0.0.1", port=0, log_level="warning", lifespan="off",
                                           timeout_graceful_shutdown=1))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    assert _wait_for(lambda: server.started, 10.0)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield "ws://127.0.0.1:%d/ws/robot-doa" % port
    server.should_exit = True
    thread.join(10)


def test_the_java_client_speaks_to_the_real_endpoint(jvm, live_server, focus):
    lines = _run(jvm, "DoaLinkHarness", live_server + "?token=" + TOKEN, "7", timeout=60)
    assert lines[0].startswith("first=") and json.loads(lines[0][6:])["type"] == "doa_ready"
    assert lines[1] == "action=1" and lines[-1] == "closed"
    assert _wait_for(lambda: len(focus.calls) == 7, 5.0)
    assert all(bearing is None for _t, bearing, _c in focus.calls)
    assert _wait_for(lambda: doa._STATE.link is None, 5.0)       # a clean close frees the slot


def test_the_java_client_stops_for_good_on_a_refusal(jvm, live_server, focus):
    lines = _run(jvm, "DoaLinkHarness", live_server + "?token=wrong", "3", timeout=60)
    first = json.loads(lines[0][6:])
    assert (first["type"], first["code"]) == ("error", "unauthorized")
    assert lines[1] == "action=3"                    # LINK_STOP_FOR_GOOD: never redialled
    assert focus.calls == []


# ==================== Android source contracts ====================


def test_the_capture_is_the_measured_array_format():
    src = code_only(read(LISTENER))
    assert "private static final int RATE = 16000;" in src
    assert "private static final int INDEX_MASK_8 = 0xff;" in src
    assert ".setChannelIndexMask(INDEX_MASK_8)" in src
    assert ".setEncoding(AudioFormat.ENCODING_PCM_16BIT)" in src
    opener = method(src, "private AudioRecord open(AudioDeviceInfo array, StringBuilder failures)")
    # VOICE_RECOGNITION first, UNPROCESSED only when the device says it has it, MIC last
    assert (opener.index("AudioSource.VOICE_RECOGNITION") < opener.index("AudioSource.UNPROCESSED")
            < opener.rindex("AudioSource.MIC"))
    assert "PROPERTY_SUPPORT_AUDIO_SOURCE_UNPROCESSED" in opener
    assert "r.setPreferredDevice(array);" in opener
    assert "r.getChannelCount() != DoaMath.CAPTURE_CHANNELS" in opener


def test_the_capture_never_fights_the_conversation_mic():
    src = code_only(read(LISTENER))
    check = method(src, "private void checkConcurrency(List<AudioRecordingConfiguration> configs)")
    assert "c.isClientSilenced()" in check and "Build.VERSION.SDK_INT >= 29" in check
    assert check.index("if (otherMuted)") < check.index("latch(\"conflict\"")
    assert "!now.containsAll(othersAtStart)" in check and "START_GRACE_MS" in check
    loop = method(src, "private void captureLoop(Run owner)")
    assert "registerAudioRecordingCallback(recordingCallback, main)" in loop
    assert "DoaMath.ZERO_LIMIT_MS" in loop and 'latch("conflict", "digital silence' in loop
    assert "r.getRoutedDevice()" in loop
    # never a permission dialog from here: the face asks when a person taps to talk
    assert "requestPermissions" not in src
    # a latch holds until the face comes back to the front (or the explicit
    # adb restart of the on-robot test) -- never a timer
    assert "latched = null;" in method(src, "void resume()")
    assert "latched = null;" in method(src, "private void restartCapture()")
    assert src.count("latched = null") == 2


def test_the_capture_runs_only_with_the_face_in_front_and_the_server_asking():
    main = code_only(read(MAIN))
    assert "if (array != null) array.pause();" in method(main, "protected void onPause()")
    assert "if (array != null && !operatorVisible) array.resume();" in method(main, "protected void onResume()")
    assert "if (array != null) array.pause();" in method(main, "private void showOperator()")
    assert "if (array != null) array.resume();" in method(main, "private void showEmma()")
    assert "if (array != null) array.shutdown();" in method(main, "protected void onDestroy()")
    src = code_only(read(LISTENER))
    serve = method(src, "private long serve(Run r, DoaLink opened)")
    # the array opens on doa_ready only; off/busy/refusals never start it
    assert serve.count("startCapture(r)") == 1
    assert serve.index("DoaMath.LINK_READY") < serve.index("startCapture(r)") < serve.index("DoaMath.LINK_OFF")
    # onResume AND showEmma both resume: the second must not start a second
    # link (the first would hold the server's one slot -> busy)
    resume = method(src, "void resume()")
    assert resume.index("if (running) {") < resume.index("return;") < resume.index("start();")
    # EmmaArrayMic/1 (ARRAY_MIC, 2 Nov): this build can hold the array alone
    assert 'static final String USER_AGENT = "EmmaRobot/0.1 EmmaArray/1 EmmaArrayMic/1";' in src
    loop = method(src, "private void linkLoop(Run r)")
    assert "HttpsURLConnection.getDefaultHostnameVerifier()" in loop      # a raw SSLSocket checks no host
    assert "if (retry == 0) failures = 0;" in loop                         # backoff resets on ready only


def test_the_token_never_follows_another_server():
    face = code_only(read(FACE))
    url = method(face, "String doaSocketUrl()")
    assert 'prefs.getString(PREF_TOKEN_ORIGIN, "")' in url and "validServer(server)" in url
    assert '"https".equals(base.getScheme()) ? "wss" : "ws"' in url
    link = code_only(read(LINK))
    assert "verifier.verify(host, ssl.getSession())" in link
    assert "(byte) (0x80 | n)" in link                   # every client frame masked (RFC 6455 5.3)


def test_the_adb_test_hook_is_debug_only_and_cannot_open_more_than_the_rules():
    """`am broadcast -a com.emma.robot.control.DOA --es do restart|off|on`:
    the only way to test both capture ORDERS and an A/B inside one call
    (the operator screen pauses the face and ends the call)."""
    src = code_only(read(LISTENER))
    ctor = method(src, "ArrayListener(Activity activity, Host host)")
    assert "ApplicationInfo.FLAG_DEBUGGABLE" in ctor
    assert ctor.index("FLAG_DEBUGGABLE") < ctor.index("registerReceiver(debugReceiver")
    restart = method(src, "private void restartCapture()")
    # a restart needs the server's ready link, and goes through startCapture's rules
    assert '!"ready".equals(linkState)' in restart and "startCapture(r);" in restart
    assert "debugHeld" in method(src, "private void startCapture(Run r)")
    assert "unregisterReceiver(debugReceiver)" in method(src, "void shutdown()")
    assert "if (array != null) array.shutdown();" in method(code_only(read(MAIN)), "protected void onDestroy()")
