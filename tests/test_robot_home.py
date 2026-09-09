import asyncio
import copy

import pytest
from fastapi.testclient import TestClient

from app.robot_backend import use_backend
from app.robot_simulation import SimulatedRobot
from app.robot_simulator import create_app


def test_virtual_devices_share_state_reset_and_never_call_hardware(monkeypatch):
    from app.tools import smarthome, broadlink_ir, load_tools, registry
    from app.config import settings
    before = copy.deepcopy(smarthome.STATE)

    def forbidden(*args, **kwargs):
        raise AssertionError("virtual home accessed real hardware")

    monkeypatch.setattr(broadlink_ir, "send", forbidden)
    monkeypatch.setattr(broadlink_ir, "available", forbidden)
    monkeypatch.setattr(settings, "tools_enabled", True)
    robot = SimulatedRobot()
    with TestClient(create_app(robot)) as client:
        for command, key, expected in [
            ({"device": "lights", "on": False}, "lights", {"on": False, "brightness": 85}),
            ({"device": "lights", "value": 30}, "lights", {"on": True, "brightness": 30}),
            ({"device": "lights", "value": 0}, "lights", {"on": False, "brightness": 0}),
            ({"device": "lights", "on": True}, "lights", {"on": True, "brightness": 85}),
            ({"device": "ac", "value": 23}, "ac", {"on": True, "temperature": 23}),
            ({"device": "curtains", "value": 45}, "curtains", {"position": 45}),
            ({"device": "tv", "on": True}, "tv", {"on": True}),
        ]:
            response = client.post("/api/home", json=command)
            assert response.status_code == 200
            assert response.json()["result"]["hardware"] == "simulated"
            assert response.json()["state"]["smart_home"][key] == expected
        snapshot = client.get("/api/state").json()["smart_home"]
        with use_backend(robot):
            load_tools()
            assert asyncio.run(registry.dispatch("get_simulated_home", {}))["smart_home"] == snapshot
            assert not asyncio.run(registry.dispatch("set_lights", {"on": False}))["ok"]
            assert asyncio.run(registry.dispatch("set_simulated_device", {"device": "curtains", "on": False}))["ok"]
        assert robot.smart_home["curtains"]["position"] == 0
        assert client.get("/api/state").json()["events"][-1]["type"] == "smart_home"
        assert client.post("/api/reset", json={}).json()["smart_home"]["tv"]["on"] is False
    assert smarthome.STATE == before
    assert not registry.allowed(registry.get("set_simulated_device"))
    assert not asyncio.run(registry.dispatch("set_simulated_device", {"device": "tv", "on": True}))["ok"]


@pytest.mark.parametrize("command", [
    {"device": "door_lock", "on": True}, {"device": "tv", "on": "false"},
    {"device": "lights", "value": True}, {"device": "lights", "value": 101},
    {"device": "lights"}, {"device": "ac", "value": 15},
    {"device": "ac", "value": 31}, {"device": "tv", "value": 20},
    {"device": "curtains", "on": True, "value": 0},
    {"device": "tv", "on": True, "host": "real-device"},
])
def test_invalid_home_command_is_atomic(command):
    robot = SimulatedRobot()
    before = robot.snapshot()
    with TestClient(create_app(robot)) as client:
        assert client.post("/api/home", json=command).status_code == 422
        assert robot.snapshot() == before


def test_home_rejects_cross_origin_and_voice_handler_validates():
    from app.tools.simulation_home import set_simulated_device
    robot = SimulatedRobot()
    with TestClient(create_app(robot)) as client:
        assert client.post("/api/home", json={"device": "lights", "on": False},
                           headers={"Origin": "https://untrusted.invalid"}).status_code == 403
        assert client.post("/api/home", content='{}').status_code == 415
        assert client.get("/robot-explorer.js").status_code == 200
        assert client.get("/robot-interior.js").status_code == 200
        assert client.get("/robot-secret.js").status_code == 404
    with use_backend(robot):
        assert not set_simulated_device("lights", on="false")["ok"]
        assert robot.smart_home["lights"]["on"] is True
    assert not set_simulated_device("lights", on=False)["ok"]
