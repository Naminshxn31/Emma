"""The cockpit's camera and mic endpoints — /cam.jpg and /mic/level.

Both are read-only feeds for the control page. The one that could lie is the
camera: a stale JPEG served while the link is down reads as a still person, so
these prove /cam.jpg refuses a frame older than the freshness window instead of
repeating it, and that both endpoints demand the token.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import robot_camera, wake
from app.config import settings
from app.main import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "ws_token", "secret123")
    return TestClient(app)


def _jpeg() -> bytes:
    import cv2
    import numpy as np

    ok, buf = cv2.imencode(".jpg", np.full((24, 32, 3), 120, dtype=np.uint8))
    assert ok
    return buf.tobytes()


def test_cam_needs_the_token(client):
    assert client.get("/cam.jpg").status_code == 401
    assert client.get("/cam.jpg?token=wrong").status_code == 401


def test_cam_serves_a_fresh_frame_but_not_a_stale_one(client, monkeypatch):
    fresh = robot_camera.RobotCameraFeed()
    monkeypatch.setattr(robot_camera, "feed", fresh)

    # No frame yet -> 503, never a blank 200.
    assert client.get("/cam.jpg?token=secret123").status_code == 503

    data = _jpeg()
    fresh.push(data)
    r = client.get("/cam.jpg?token=secret123")
    assert r.status_code == 200 and r.content == data
    assert r.headers["content-type"] == "image/jpeg"

    # Age it past the freshness window: the same frame must not be served as
    # if it were live (a frozen feed is a stopped camera, not a still person).
    fresh._at = time.monotonic() - 5.0
    assert client.get("/cam.jpg?token=secret123").status_code == 503


def test_mic_level_needs_the_token_and_reports_streaming(client):
    assert client.get("/mic/level").json()["error"] == "unauthorized"
    out = client.get("/mic/level?token=secret123").json()
    assert out["ok"] is True
    assert "level" in out and "streaming" in out
    assert out["speech_floor"] == wake.WakeStream.SPEECH
    assert "near_field_floor" in out and "raw_floor_estimate" in out


def test_robot_kiosk_mic_level_wins_while_fresh(client, monkeypatch):
    fresh = robot_camera.RobotCameraFeed()
    fresh.push_mic_level(0.125, "Default")
    monkeypatch.setattr(robot_camera, "feed", fresh)

    out = client.get("/mic/level?token=secret123").json()

    assert out["ok"] is True and out["streaming"] is True
    assert out["source"] == "robot_kiosk"
    assert out["device_label"] == "Default"
    assert out["level"] == 0.125


def test_robot_mic_probe_requires_a_connected_kiosk(client, monkeypatch):
    fresh = robot_camera.RobotCameraFeed()
    monkeypatch.setattr(robot_camera, "feed", fresh)

    assert client.post("/mic/control", json={"action": "probe"}).json()["error"] == "unauthorized"
    out = client.post("/mic/control", json={"token": "secret123", "action": "probe"}).json()
    assert out["ok"] is False and "Emma" in out["error"]

    fresh.attach()
    out = client.post("/mic/control", json={"token": "secret123", "action": "probe"}).json()
    assert out["ok"] is True and out["seconds"] == 5
    assert fresh.control()[1]["mic_probe_s"] == 5
