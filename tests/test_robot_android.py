"""The allow-listed Android controls behind the Robot Lab page."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import robot_android
from app.config import settings
from app.main import app


def test_state_reads_backlights_rotation_camera_and_audio(monkeypatch):
    async def fake_run(args, timeout=None):
        command = args[-1]
        if command == "get-state":
            return 0, "device"
        if "BL:" in command:
            return 0, "BL:a:204:255\nBL:b:128:255"
        if "ROT:" in command:
            return 0, "ROT:1:0"
        if command == "dumpsys media.camera":
            return 0, "Number of camera devices: 1"
        if command == "cat /proc/asound/cards":
            return 0, " 0 [Bothlent       ]: USB-Audio - Bothlent UAC Dongle"
        raise AssertionError(args)

    monkeypatch.setattr(robot_android, "_run", fake_run)
    out = asyncio.run(robot_android.state())

    assert out["connected"] is True
    assert out["backlights"]["a"]["percent"] == 80
    assert out["backlights"]["b"]["current"] == 128
    assert out["rotation"] == 1 and out["auto_rotate"] is False
    assert out["camera_count"] == 1
    assert out["bothlent_present"] is True


def test_brightness_uses_only_known_display_and_presets(monkeypatch):
    commands = []

    async def fake_shell(command):
        commands.append(command)
        if command.endswith("/max_brightness"):
            return 0, "255"
        return 0, "204"

    monkeypatch.setattr(robot_android, "_shell", fake_shell)
    result = asyncio.run(robot_android.set_brightness("b", 80))

    assert result["percent"] == 80
    assert commands[-1] == (
        "su 0 sh -c 'echo 204 > /sys/class/backlight/backlight1/brightness' "
        "&& cat /sys/class/backlight/backlight1/brightness")
    with pytest.raises(ValueError):
        asyncio.run(robot_android.set_brightness("../../anything", 80))
    with pytest.raises(ValueError):
        asyncio.run(robot_android.set_brightness("a", 81))


def test_android_endpoints_require_token_and_dispatch_allowlisted_action(monkeypatch):
    monkeypatch.setattr(settings, "ws_token", "robot-lab-token")

    async def fake_state():
        return {"connected": True, "backlights": {}}

    async def fake_brightness(display, percent):
        return {"display": display, "percent": percent}

    monkeypatch.setattr(robot_android, "state", fake_state)
    monkeypatch.setattr(robot_android, "set_brightness", fake_brightness)
    client = TestClient(app)

    assert client.get("/android/state").json()["error"] == "unauthorized"
    assert client.get("/android/state?token=robot-lab-token").json()["state"]["connected"]
    denied = client.post("/android/command", json={"action": "brightness",
                                                   "display": "a", "percent": 80})
    assert denied.json()["error"] == "unauthorized"
    allowed = client.post("/android/command", json={"token": "robot-lab-token",
        "action": "brightness", "display": "a", "percent": 80}).json()
    assert allowed == {"ok": True, "did": "brightness",
                       "result": {"display": "a", "percent": 80}}
    raw = client.post("/android/command", json={"token": "robot-lab-token",
                                                "action": "shell", "command": "id"}).json()
    assert raw == {"ok": False, "error": "ไม่รู้จักคำสั่ง Android"}


def test_rotation_endpoint_refuses_a_control_that_did_not_move_the_real_screen(monkeypatch):
    monkeypatch.setattr(settings, "ws_token", "robot-lab-token")
    assert not hasattr(robot_android, "set_rotation")
    out = TestClient(app).post("/android/command", json={
        "token": "robot-lab-token", "action": "rotation", "rotation": 1,
    }).json()

    assert out["error"] == "rotation_not_effective"
