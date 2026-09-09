"""Command lifecycle, production-tool isolation and local simulator API."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app.robot_backend import active, use_backend
from app.robot_simulation import POINTS, SimulatedRobot
from app.robot_simulator import create_app


def test_game_routes_use_corridor_and_can_resume_from_mid_trip():
    from app.robot_simulation import corridor_route, route_position

    for start in POINTS.values():
        for target in POINTS.values():
            route = corridor_route(start, target)
            assert route[0] == list(start) and route[-1] == list(target)
            for a, b in zip(route, route[1:]):
                assert a[0] == b[0] or a[1] == b[1]
                if a[0] != b[0]:
                    assert a[1] == b[1] == 3.1
            halfway = route_position(route, 0.5)
            resumed = corridor_route(halfway, POINTS["base"])
            assert resumed[0] == halfway
            assert route_position(resumed, 1) == list(POINTS["base"])


@pytest.fixture
def rig():
    now = [0.0]
    robot = SimulatedRobot(clock=lambda: now[0])

    def advance(seconds):
        now[0] += seconds
        robot.tick()

    return robot, advance


def move(robot, ident="trip-1"):
    return robot.submit("move_to_point", {"place": "ห้องตัวอย่าง"}, ident)


def test_ack_is_not_arrival_and_position_progresses(rig):
    robot, advance = rig
    assert move(robot)["accepted"]
    assert not any(e["type"] == "robot_arrived" for e in robot.events)
    advance(3)
    assert robot.state["moving"] is True
    assert robot.position != list(POINTS["base"])
    assert robot.position != list(POINTS["ห้องตัวอย่าง"])
    advance(3)
    assert robot.phase == "arrived" and robot.state["moving"] is False
    assert robot.events[-1]["command_id"] == "trip-1"
    assert robot.events[-1]["ok"] is True


def test_duplicate_and_conflicting_id_do_not_restart_motion(rig):
    robot, advance = rig
    move(robot)
    advance(2)
    began = robot.pending["started"]
    assert move(robot)["duplicate"]
    assert robot.pending["started"] == began
    assert robot.submit("go_home", {}, "trip-1")["reason"] == "id_conflict"
    advance(4)
    assert move(robot)["duplicate"]
    assert robot.pending is None


def test_unknown_place_and_busy_are_rejected(rig):
    robot, _ = rig
    assert robot.submit("move_to_point", {"place": "missing"})["reason"] == "unknown_place"
    assert robot.pending is None
    move(robot)
    assert robot.submit("go_home", {})["reason"] == "busy"
    assert robot.pending["id"] == "trip-1"


def test_stop_preempts_motion_and_old_callback_cannot_finish_next_trip(rig):
    robot, advance = rig
    move(robot)
    advance(1)
    position = robot.position[:]
    robot.submit("cancel_navigation", {})
    advance(20)
    assert robot.position == position
    assert robot.commands["trip-1"]["status"] == "cancelled"
    move(robot, "trip-2")
    assert not robot.complete("trip-1", ok=True)
    assert robot.pending["id"] == "trip-2"
    advance(6)
    assert robot.phase == "arrived"


def test_go_home_reports_charging_only_after_arrival(rig):
    robot, advance = rig
    move(robot)
    advance(6)
    robot.submit("go_home", {})
    assert robot.state["charging"] is False
    advance(6)
    assert robot.state["charging"] is True
    assert robot.position == list(POINTS["base"])


@pytest.mark.parametrize("fault,phase", [
    ("obstacle", "blocked"), ("navigation_failed", "navigation_failed"),
    ("no_arrival", "moving"), ("ack_lost", "unknown"),
])
def test_faults_never_report_success(rig, fault, phase):
    robot, advance = rig
    robot.configure(fault=fault, duration=6, timeout=12)
    reply = move(robot)
    assert reply["accepted"] is (fault != "ack_lost")
    advance(6)
    assert robot.phase == phase
    advance(6)
    assert robot.phase == ("navigation_failed" if fault == "navigation_failed" else "timeout")
    assert not any(e.get("ok") is True for e in robot.events)


def test_disconnect_is_unknown_stop_fails_reconnect_does_not_replay(rig):
    robot, advance = rig
    move(robot)
    advance(2)
    robot.set_connected(False)
    assert robot.state["moving"] is None
    assert not robot.submit("cancel_navigation", {})["accepted"]
    assert robot.state["moving"] is None
    position = robot.position[:]
    advance(20)
    robot.set_connected(True)
    assert robot.pending is None
    assert robot.position == position
    assert move(robot)["duplicate"]
    assert robot.pending is None


def test_config_only_affects_next_command_and_snapshots_are_copies(rig):
    robot, advance = rig
    move(robot)
    robot.configure(fault="obstacle", duration=20, timeout=30)
    snap = robot.snapshot()
    snap["commands"][0]["args"]["place"] = "changed"
    snap["position"][0] = 1000
    advance(6)
    assert robot.phase == "arrived"
    assert robot.commands["trip-1"]["args"]["place"] == "ห้องตัวอย่าง"


def test_memory_is_bounded_and_active_command_is_preserved(rig):
    robot, _ = rig
    move(robot)
    for i in range(600):
        robot.submit("go_home", {}, f"rejected-{i}")
    assert len(robot.events) == 500
    assert len(robot.commands) == 256
    assert "trip-1" in robot.commands
    assert robot.complete("trip-1", ok=True)


def test_real_tools_use_simulator_without_mutating_real_link_or_opening_session(rig, monkeypatch):
    from app.tools import robot as handlers, robot_link
    from app import session
    from app.config import settings

    robot, advance = rig
    before = robot_link.snapshot()
    monkeypatch.setattr(settings, "robot_enabled", False)

    class ForbiddenSession:
        @property
        def ws(self):
            raise AssertionError("simulator touched a real transport")

    monkeypatch.setattr(session, "_active", ForbiddenSession())

    async def run():
        with use_backend(robot):
            out = await handlers.go_to_place("พาไปห้องตัวอย่าง")
            assert out["hardware"] == "simulated" and out["moving"]
            assert "ตัวจำลอง" in out["instruction"]
            assert handlers.get_robot_status()["hardware"] == "simulated"
            advance(1)
            assert (await handlers.stop_moving())["was_moving"] is True
            assert (await handlers.return_to_base())["hardware"] == "simulated"
        assert active.get() is None

    asyncio.run(run())
    assert robot_link.snapshot() == before


def test_backend_context_is_task_local_and_resets_after_exception(rig):
    robot, _ = rig

    async def run():
        async def simulated():
            with use_backend(robot):
                await asyncio.sleep(0)
                assert active.get() is robot

        async def real():
            await asyncio.sleep(0)
            assert active.get() is None

        await asyncio.gather(simulated(), real())
        with pytest.raises(RuntimeError):
            with use_backend(robot):
                raise RuntimeError("test")
        assert active.get() is None

    asyncio.run(run())


def test_api_uses_tools_and_validates_commands_without_provider(rig):
    robot, advance = rig
    with TestClient(create_app(robot)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/simulator.js").status_code == 200
        assert client.get("/simulator.css").status_code == 200
        out = client.post("/api/tool", json={"tool": "go_to_place", "place": "ห้องตัวอย่าง"})
        assert out.status_code == 200 and out.json()["result"]["hardware"] == "simulated"
        command = out.json()["state"]["commands"][0]
        retry = {k: command[k] for k in ("action", "args", "command_id")}
        assert client.post("/api/command", json=retry).json()["duplicate"]
        advance(6)
        assert client.get("/api/state").json()["phase"] == "arrived"
        for payload in [
            {"tool": "run_command"}, {"tool": "stop_moving", "extra": True},
            {"tool": "go_to_place", "place": "x" * 201},
        ]:
            assert client.post("/api/tool", json=payload).status_code == 422
        assert client.post("/api/command", json={**retry, "args": {"speed": "unsafe"}}).status_code == 422
        assert client.post("/api/config", json={"fault": "none", "duration": 0, "timeout": 12}).status_code == 422
        assert client.post("/api/connection", json={"connected": False}).json()["moving"] is None
        assert client.post("/api/reset", json={}).json()["phase"] == "idle"


def test_local_3d_modules_integrity_and_allowlist(rig):
    import hashlib
    from pathlib import Path

    robot, _ = rig
    with TestClient(create_app(robot)) as client:
        manifest = client.get("/vendor/three/manifest.json").json()
        assert manifest["version"] == "0.180.0"
        for name, entry in manifest["files"].items():
            response = client.get("/vendor/three/" + name)
            assert response.status_code == 200
            assert hashlib.sha256(response.content).hexdigest() == entry["sha256"]
            if name.endswith(".js"):
                assert "javascript" in response.headers["content-type"]
        assert client.get("/robot-scene-3d.js").status_code == 200
        for path in ["/vendor/three/missing.js", "/vendor/three/package.json", "/vendor/three/%2e%2e/%2e%2e/.env"]:
            assert client.get(path).status_code == 404
        csp = client.get("/").headers["content-security-policy"]
        assert "script-src 'self';" in csp
        assert "unsafe-eval" not in csp and "unsafe-inline" not in csp
        source = (Path(__file__).parents[1] / "client/vendor/three/OrbitControls.js").read_text(encoding="utf-8")
        assert "from './three.module.min.js'" in source


def test_api_rejects_cross_origin_unknown_host_and_non_json(rig):
    robot, _ = rig
    with TestClient(create_app(robot)) as client:
        assert client.post("/api/reset", json={}, headers={"Origin": "https://untrusted.example"}).status_code == 403
        assert client.post("/api/reset", content="{}").status_code == 415
        assert client.get("/api/state", headers={"Host": "untrusted.example"}).status_code == 400
        assert client.post("/api/reset", json={}, headers={"Origin": "http://testserver"}).status_code == 200
        assert client.get("/simulator.txt").status_code == 404
        assert "frame-ancestors 'none'" in client.get("/").headers["content-security-policy"]


def test_failed_physical_stop_preserves_pending_motion_and_does_not_claim_success(monkeypatch):
    from app.tools import robot, robot_link

    async def failed(*args, **kwargs):
        return "failed"

    monkeypatch.setattr(robot_link, "send", failed)
    monkeypatch.setitem(robot_link.STATE, "moving", True)
    monkeypatch.setitem(robot_link.STATE, "destination", "ห้องตัวอย่าง")
    out = asyncio.run(robot.stop_moving())
    assert out["ok"] is False
    assert robot_link.STATE["moving"] is True
    assert "ยังยืนยันการหยุดไม่ได้" in out["instruction"]


def test_physical_empty_map_never_uses_mock_names(monkeypatch):
    from app.config import settings
    from app.tools import robot_link

    monkeypatch.setattr(settings, "robot_mock_places", ["ห้องตัวอย่าง"])
    monkeypatch.setattr(robot_link, "KNOWN_PLACES", [])
    monkeypatch.setitem(robot_link.STATE, "connected", True)
    assert robot_link.places() == []


def test_old_different_destination_does_not_finish_physical_trip(monkeypatch):
    from app.tools import robot_link

    monkeypatch.setitem(robot_link.STATE, "moving", True)
    monkeypatch.setitem(robot_link.STATE, "destination", "โต๊ะเซลส์")
    assert asyncio.run(robot_link.arrived("ห้องตัวอย่าง")) is False
    assert robot_link.STATE["moving"] is True
