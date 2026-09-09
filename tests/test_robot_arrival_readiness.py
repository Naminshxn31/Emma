"""Reject malformed bridge events and distinguish requests from measured motion."""
import asyncio
import json

import pytest

from app import events, session
from app.config import settings
from app.tools import robot, robot_link


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    robot_link.reset_state()
    monkeypatch.setattr(settings, "robot_enabled", True)
    monkeypatch.setattr(settings, "robot_token", "test-bridge")
    monkeypatch.setattr(settings, "turn_log", False)
    yield
    robot_link.reset_state()


class WS:
    def __init__(self, messages):
        self.messages = iter(messages)

    async def receive(self):
        try:
            return {"text": json.dumps(next(self.messages))}
        except StopIteration:
            return {"type": "websocket.disconnect"}


def pump(messages):
    voice = session.VoiceSession(None, provider_name="gemini")
    voice.ws = WS(messages)
    asyncio.run(voice._browser_to_provider())
    return voice


@pytest.mark.parametrize("payload", [None, [], True, 3, "text", {"type": []}])
def test_malformed_json_does_not_end_the_message_pump(payload):
    voice = pump([payload, {"type": "robot_ready", "token": "test-bridge", "places": ["Lobby"]}])
    assert voice._is_robot
    assert robot_link.places() == ["Lobby"]


@pytest.mark.parametrize("names", [None, "Lobby", {}, [None], [1], [""], ["  "], ["x" * 201]])
def test_bad_map_cannot_authenticate_or_replace_valid_map(names):
    voice = pump([{"type": "robot_ready", "token": "test-bridge", "places": names}])
    assert not getattr(voice, "_is_robot", False)
    assert not robot_link.available()
    robot_link.app_connected(["Lobby"])
    assert robot_link.app_connected(names) is False
    assert robot_link.places() == ["Lobby"]


@pytest.mark.parametrize("ok", ["false", "true", 0, 1, None, [], {}])
def test_invalid_arrival_boolean_keeps_walk_pending(monkeypatch, ok):
    announced = []

    async def announce(*args, **kwargs):
        announced.append(args)

    monkeypatch.setattr(events, "announce", announce)
    robot_link.STATE.update(moving=True, destination="Lobby")
    pump([{"type": "robot_ready", "token": "test-bridge", "places": ["Lobby"]},
          {"type": "robot_arrived", "place": "Lobby", "ok": ok}])
    assert robot_link.STATE["moving"] is True
    assert announced == []


@pytest.mark.parametrize("place", [None, "", " ", [], "Other"])
def test_invalid_arrival_place_cannot_complete_walk(place):
    robot_link.STATE.update(moving=True, destination="Lobby")
    assert asyncio.run(robot_link.arrived(place, True)) is False
    assert robot_link.STATE["moving"] is True


def test_unrelated_destinations_do_not_pick_longer_name(monkeypatch):
    robot_link.app_connected(["Lobby", "Meeting Room"])
    sent = []

    async def send(*args, **kwargs):
        sent.append(args)
        return "ok"

    monkeypatch.setattr(robot_link, "send", send)
    result = asyncio.run(robot.go_to_place("Lobby or Meeting Room"))
    assert result["ok"] is False
    assert sent == []


def test_duplicate_names_are_one_destination():
    assert robot_link.app_connected(["Lobby", "Lobby"])
    assert robot_link.find_place("Lobby") == "Lobby"


@pytest.mark.parametrize("transport", ["ok", "failed", "mock"])
def test_timeout_never_confirms_physical_stop(monkeypatch, transport):
    spoken = []

    async def send(*args, **kwargs):
        return transport

    async def announce(text, **kwargs):
        spoken.append(text)

    monkeypatch.setattr(robot_link, "send", send)
    monkeypatch.setattr(events, "announce", announce)

    async def go():
        robot_link.app_connected(["Lobby"])
        robot_link.STATE.update(moving=True, destination="Lobby", status_source="command_sent")
        robot_link.start_arrival_watch("Lobby", 0.001)
        await robot_link._arrival_watch

    asyncio.run(go())
    assert robot_link.snapshot()["moving"] is None
    assert "ยังยืนยันว่าหุ่นหยุดจริงไม่ได้" in spoken[0]
    assert ("ส่งคำสั่งหยุดไม่สำเร็จ" in spoken[0]) == (transport != "ok")


def test_disconnect_exposes_unknown_motion():
    robot_link.app_connected(["Lobby"])
    robot_link.STATE.update(moving=True, status_source="command_sent")
    robot_link.app_gone()
    assert robot.get_robot_status()["moving"] is None
    assert robot.get_robot_status()["status_source"] == "disconnected"


def test_stop_request_exposes_unknown_until_hardware_report(monkeypatch):
    async def send(*args, **kwargs):
        return "ok"

    monkeypatch.setattr(robot_link, "send", send)
    robot_link.app_connected(["Lobby"])
    asyncio.run(robot.stop_moving())
    assert robot.get_robot_status()["moving"] is None
    assert robot.get_robot_status()["status_source"] == "stop_requested"
