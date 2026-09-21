"""The robot's camera over a WebSocket - app/robot_camera.py + /ws/camera.

The greeter reads frames through `cap.read()`; these tests prove the
socket-fed handle behaves like the USB one where it matters (fresh frames,
empty reads on a stall, no exception on bad bytes) and that the sender is
the kiosk page, video only, opt-in.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app import robot_camera
from app.config import settings


def _jpeg(w=64, h=48, shade=90) -> bytes:
    import cv2

    img = np.full((h, w, 3), shade, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def test_frames_pushed_into_the_feed_come_out_of_read():
    feed = robot_camera.RobotCameraFeed()
    cap = robot_camera.RobotCapture(feed, wait_s=0.2)
    feed.push(_jpeg(64, 48))
    ok, frame = cap.read()
    assert ok and frame.shape == (48, 64, 3)
    assert cap.get(3) == 64 and cap.get(4) == 48
    assert cap.isOpened()


def test_a_stalled_feed_reads_as_empty_not_as_the_same_frame_again():
    """A frozen link must look like a camera that stopped, not like a very
    still person: the greeter confirms a face across several frames, and a
    single frame served repeatedly would confirm itself."""
    feed = robot_camera.RobotCameraFeed()
    cap = robot_camera.RobotCapture(feed, wait_s=0.1)
    feed.push(_jpeg())
    assert cap.read()[0] is True
    ok, frame = cap.read()
    assert ok is False and frame is None
    feed.push(_jpeg(shade=20))
    assert cap.read()[0] is True, "a new frame after the stall is read normally"


def test_bad_bytes_are_an_empty_read_not_an_exception():
    feed = robot_camera.RobotCameraFeed()
    cap = robot_camera.RobotCapture(feed, wait_s=0.1)
    feed.push(b"not a jpeg at all")
    ok, frame = cap.read()
    assert ok is False and frame is None


def test_open_camera_hands_over_the_robot_feed_without_touching_a_lens(monkeypatch):
    """FACE_CAMERA_SOURCE=robot: no OpenCV device is probed at all. A server
    that opened the desk webcam *and* took robot frames would greet from
    two doors at once."""
    from app import camera

    monkeypatch.setattr(settings, "face_camera_source", "robot")
    monkeypatch.setattr(camera, "_try_open",
                        lambda *a, **k: pytest.fail("a local lens was probed"))
    cap = camera.open_camera()
    assert isinstance(cap, robot_camera.RobotCapture)


def test_anything_but_robot_means_local():
    """A typo in the switch must not turn the camera off quietly."""
    import os
    import subprocess
    import sys

    env = {**os.environ, "FACE_CAMERA_SOURCE": "robto"}
    out = subprocess.run(
        [sys.executable, "-c",
         "from app.config import settings; print(settings.face_camera_source)"],
        env=env, capture_output=True, text=True, encoding="utf-8", timeout=60,
        cwd=str(Path(__file__).resolve().parent.parent))
    assert out.returncode == 0, out.stderr[-800:]
    assert out.stdout.strip() == "local"


def test_the_camera_socket_feeds_the_greeter(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(settings, "ws_token", "")
    fresh = robot_camera.RobotCameraFeed()
    monkeypatch.setattr(robot_camera, "feed", fresh)
    data = _jpeg(32, 24)
    client = TestClient(app)
    with client.websocket_connect("/ws/camera") as ws:
        ws.send_bytes(data)
        ws.send_text('{"type":"mic_level","level":0.024,"label":"Default"}')
        ws.send_text("ignored: malformed telemetry")
    seq, jpeg, _ = fresh.latest()
    assert seq == 1 and jpeg == data
    mic = fresh.mic_level()
    assert mic["streaming"] is True and mic["level"] == 0.024
    assert mic["device_label"] == "Default"
    assert fresh.senders == 0, "the sender is counted out when its socket closes"


def _client_src() -> str:
    return (Path(__file__).parent.parent / "client" / "index.html").read_text(encoding="utf-8")


def test_the_kiosk_sends_video_only_and_only_when_asked():
    """Opt-in by URL and kiosk-only: a desk browser must never start
    uploading its webcam because a flag was left on. Video only: the mic
    already has its wake and call captures, and a third one on the same
    device is the exact-zeros bug the sleep path fixed."""
    src = _client_src()
    assert "get('cam') === '1'" in src
    assert "kioskMode && new URLSearchParams(location.search).get('cam')" in src
    body = src[src.index("async function startRobotCamera()"):]
    body = body[:body.index("\n}\n")]
    assert "audio: false" in body
    assert "video:" in body
    assert "/ws/camera" in body
    assert "image/jpeg" in body
    # The wrong-token rule every other socket learned the hard way.
    assert "evt.code === 'unauthorized'" in body and "robotCamDown = true" in body
    # And it does not queue stale frames on a slow link.
    assert "bufferedAmount" in body
    # Android's logical entries say "facing back": the physical module cannot
    # be inferred from that flag and the entry is chosen by index (?camdev=N).
    assert "facingMode" not in body and "camdev" in src
    # A first-request denial is retried a bounded number of times, then final.
    assert "robotCamTries >= 6" in body
    # Started after load, never during parsing, and a request that hangs is
    # a failure (both measured on the robot, 2026-09-11).
    assert "window.addEventListener('load', () => setTimeout(startRobotCamera" in src
    assert "camera request hung" in body


def test_the_kiosk_prefers_bothlent_but_accepts_androids_verified_usb_default():
    src = _client_src()
    helper = src[src.index("const ROBOT_MIC_LABEL"):src.index("async function startRobotCamera")]

    assert "kioskMode ? 'bothlent'" in helper
    assert "d.kind === 'audioinput'" in helper
    assert "deviceId: { exact: pick.deviceId }" in helper
    assert "ABSTRACT_AUDIO_LABELS" in helper
    assert "if (labelsAreAbstract) return opened" in helper
    assert "Bothlent microphone not found" in helper
    assert src.count("await openPreferredMic(") == 3, (
        "call, wake, and the bounded robot probe must use the same selector")


def test_robot_mic_probe_reports_levels_only_and_stops_after_five_seconds():
    src = _client_src()
    probe = src[src.index("async function runRobotMicProbe"):src.index("async function startRobotCamera")]

    assert "reportRobotMicLevel" in probe and "mic_level" in src
    assert "Math.min(5" in probe
    assert "getTracks().forEach((t) => t.stop())" in probe
    assert "MediaRecorder" not in probe and "ArrayBuffer" not in probe
