"""Emma's real WebSocket/audio/tool plumbing, with an upstream fake only."""
import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import settings
from app.providers.base import ProviderEvent
from app.robot_backend import SIMULATION_TOOLS, use_backend
from app.robot_simulation import SimulatedRobot
from app.robot_simulator import create_app


class ScriptedProvider:
    input_sample_rate = 16000
    output_sample_rate = 24000
    session_limit_minutes = 15
    auto_resumes = False
    model = "offline-test-provider"

    def __init__(self):
        self.queue = asyncio.Queue()
        self.audio = []
        self.said = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def send_audio(self, pcm):
        from app.tools import registry

        self.audio.append(pcm)
        if len(self.audio) == 1:
            await self.queue.put(ProviderEvent("tool_call", text="go_to_place"))
            result = await registry.dispatch("go_to_place", {"place": "ห้องตัวอย่าง"})
            await self.queue.put(ProviderEvent("tool_result", text="go_to_place", data=result))
            await self.queue.put(ProviderEvent("audio", audio=b"\x00\x00" * 240))
            await self.queue.put(ProviderEvent("turn_complete"))

    async def send_text(self, text):
        self.said.append(text)
        await self.queue.put(ProviderEvent("assistant_transcript", text=text))
        await self.queue.put(ProviderEvent("turn_complete"))

    async def events(self):
        while True:
            yield await self.queue.get()


@pytest.fixture
def voice_rig(monkeypatch):
    from app import session

    robot = SimulatedRobot()
    created = []

    def fake_provider(*args, **kwargs):
        from app.tools import load_tools, registry

        load_tools()
        assert {t.name for t in registry.all_tools()} == SIMULATION_TOOLS
        assert "ตัวจำลอง" in args[2] or "หุ่นจำลอง" in args[2]
        provider = ScriptedProvider()
        created.append(provider)
        return provider

    monkeypatch.setattr(session, "get_provider", fake_provider)
    monkeypatch.setattr(settings, "api_key_for", lambda provider: "offline-test-key")
    monkeypatch.setattr(settings, "tools_enabled", True)
    monkeypatch.setattr(settings, "assistant_profile", "condo")
    monkeypatch.setattr(settings, "tool_groups", "computer,memory,smarthome")
    monkeypatch.setattr(settings, "wake_enabled", False)
    # Deliberately configured: simulator must not poll host UI.
    monkeypatch.setattr(settings, "canva_url", "https://unused.invalid")
    monkeypatch.setattr(settings, "canva_poll_s", 0.01)

    async def forbidden_canva(self):
        raise AssertionError("simulator polled a real Canva window")

    monkeypatch.setattr(session.VoiceSession, "_follow_canva", forbidden_canva)
    return robot, created


def receive_until(ws, kind):
    while True:
        message = ws.receive()
        if message.get("bytes") is not None:
            continue
        import json

        event = json.loads(message["text"])
        assert event["type"] != "error", event
        if event["type"] == kind:
            return event


@pytest.mark.timeout(15)
@pytest.mark.parametrize("multi_session,ok", [(False, True), (True, True), (False, False)])
def test_audio_drives_actual_robot_tool_and_arrival_returns_to_emma(voice_rig, monkeypatch, multi_session, ok):
    from app import session
    from app.tools import robot_link

    robot, created = voice_rig
    monkeypatch.setattr(settings, "multi_session", multi_session)
    monkeypatch.setattr(settings, "robot_token", "offline-robot-token")
    before = robot_link.snapshot()
    with TestClient(create_app(robot)) as client:
        client.get("/voice")
        with client.websocket_connect("/ws?profile=translator", headers={"Origin": "http://testserver"}) as ws:
            ready = ws.receive_json()
            assert ready["type"] == "ready" and ready["profile"] == "emma"
            assert ready["robot_simulator"] is True
            ws.send_json({"type": "robot_ready", "token": "offline-robot-token", "places": ["not real"]})
            ws.send_bytes(b"\x01\x00" * 320)
            result = receive_until(ws, "tool_result")
            assert result["result"]["hardware"] == "simulated"
            assert robot.pending is not None
            receive_until(ws, "turn_complete")
            command_id = robot.pending["id"]
            # Simulate the controller callback on the server's own loop.
            async def reached():
                robot.complete(command_id, ok=ok)

            client.portal.call(reached)
            arrival = receive_until(ws, "assistant_transcript")
            assert ("หุ่นจำลองถึงห้องตัวอย่างแล้ว" if ok else "หุ่นจำลองไปห้องตัวอย่างไม่สำเร็จ") in arrival["text"]
            assert created[0].audio[0] == b"\x01\x00" * 320
            assert any(e["type"] == "tool_result" for e in robot.events)
        assert robot_link.snapshot() == before
    assert session._active is None


@pytest.mark.timeout(15)
def test_only_one_voice_client_and_disconnect_cancels_simulated_trip(voice_rig):
    robot, _ = voice_rig
    with TestClient(create_app(robot)) as client:
        client.get("/voice")
        with client.websocket_connect("/ws", headers={"Origin": "http://testserver"}) as ws:
            assert ws.receive_json()["type"] == "ready"
            with client.websocket_connect("/ws", headers={"Origin": "http://testserver"}) as second:
                assert second.receive_json()["code"] == "simulator_busy"
            ws.send_bytes(b"\x00\x00" * 160)
            receive_until(ws, "tool_result")
            assert robot.pending is not None
        # Wait for endpoint cleanup by scheduling onto its event loop.
        async def drained():
            for _ in range(50):
                if robot.pending is None:
                    return
                await asyncio.sleep(0.01)
            raise AssertionError("voice disconnect left navigation running")

        client.portal.call(drained)
        assert robot.phase == "stopped"


@pytest.mark.timeout(15)
def test_voice_tool_changes_virtual_lights_without_real_ir(voice_rig, monkeypatch):
    from app import session
    from app.tools import broadlink_ir, registry
    robot, _ = voice_rig

    def forbidden(*args, **kwargs):
        raise AssertionError("simulated voice sent real IR")

    monkeypatch.setattr(broadlink_ir, "send", forbidden)

    class HomeProvider(ScriptedProvider):
        async def send_audio(self, pcm):
            if self.audio:
                return
            self.audio.append(pcm)
            await self.queue.put(ProviderEvent("tool_call", text="set_simulated_device"))
            result = await registry.dispatch("set_simulated_device", {"device": "lights", "on": False})
            await self.queue.put(ProviderEvent("tool_result", text="set_simulated_device", data=result))
            await self.queue.put(ProviderEvent("turn_complete"))

    def factory(*args, **kwargs):
        from app.tools import load_tools
        load_tools()
        return HomeProvider()

    monkeypatch.setattr(session, "get_provider", factory)
    with TestClient(create_app(robot)) as client:
        client.get("/voice")
        with client.websocket_connect("/ws", headers={"Origin": "http://testserver"}) as ws:
            assert ws.receive_json()["type"] == "ready"
            ws.send_bytes(b"\x01\x00" * 320)
            result = receive_until(ws, "tool_result")["result"]
            assert result["hardware"] == "simulated"
            assert robot.smart_home["lights"]["on"] is False
            assert robot.pending is None


def test_voice_requires_local_page_cookie_and_matching_origin(voice_rig):
    robot, created = voice_rig
    with TestClient(create_app(robot)) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws", headers={"Origin": "http://testserver"}):
                pass
        page = client.get("/voice")
        assert "HttpOnly" in page.headers["set-cookie"]
        assert "SameSite=strict" in page.headers["set-cookie"]
        for origin in ["https://untrusted.example", "http://testserver:9999", "null"]:
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect("/ws", headers={"Origin": origin}):
                    pass
        health = client.get("/health").json()
        assert health["robot_simulator"] and health["auto_connect"] is False
        assert not health["wake"]["ready"]
        assert client.get("/voices").json()["voices"]
        assert not created


@pytest.mark.timeout(15)
def test_wake_opens_no_provider_until_voice_handover(voice_rig, monkeypatch):
    from app import wake

    robot, created = voice_rig
    monkeypatch.setattr(settings, "wake_enabled", True)
    monkeypatch.setattr(wake, "available", lambda: True)
    frames = []

    class Detector:
        def __init__(self, *, diagnostics):
            assert diagnostics is False

        def feed(self, data):
            frames.append(data)
            return "EMMA" if len(frames) == 2 else None

    monkeypatch.setattr(wake, "WakeStream", Detector)
    with TestClient(create_app(robot)) as client:
        client.get("/voice")
        assert client.get("/health").json()["wake"]["ready"]
        with client.websocket_connect("/ws/wake", headers={"Origin": "http://testserver"}) as ws:
            assert ws.receive_json()["type"] == "wake_listening"
            ws.send_bytes(b"\x00\x00" * 320)
            ws.send_bytes(b"\x01\x00" * 320)
            assert ws.receive_json() == {"type": "wake", "word": "EMMA"}
        assert not created
        assert len(frames) == 2
        assert any(e["type"] == "wake" for e in robot.events)
        with client.websocket_connect("/ws", headers={"Origin": "http://testserver"}) as ws:
            assert ws.receive_json()["robot_simulator"]
            ws.send_bytes(b"\x01\x00" * 320)
            assert receive_until(ws, "tool_result")["result"]["hardware"] == "simulated"
        assert len(created) == 1


@pytest.mark.parametrize("enabled,reason", [(False, "disabled"), (True, "no model")])
def test_wake_unavailable_keeps_manual_start(voice_rig, monkeypatch, enabled, reason):
    from app import wake

    robot, created = voice_rig
    monkeypatch.setattr(settings, "wake_enabled", enabled)
    monkeypatch.setattr(wake, "available", lambda: False)
    with TestClient(create_app(robot)) as client:
        client.get("/voice")
        assert not client.get("/health").json()["wake"]["ready"]
        with client.websocket_connect("/ws/wake", headers={"Origin": "http://testserver"}) as ws:
            assert ws.receive_json() == {"type": "wake_unavailable", "reason": reason}
        assert not created
        with client.websocket_connect("/ws", headers={"Origin": "http://testserver"}) as ws:
            assert ws.receive_json()["type"] == "ready"


def test_wake_requires_cookie_and_same_origin(voice_rig):
    robot, created = voice_rig
    with TestClient(create_app(robot)) as client:
        with client.websocket_connect("/ws/wake", headers={"Origin": "http://testserver"}) as ws:
            assert ws.receive_json()["code"] == "unauthorized"
        client.get("/voice")
        with client.websocket_connect("/ws/wake", headers={"Origin": "https://untrusted.example"}) as ws:
            assert ws.receive_json()["code"] == "unauthorized"
        assert not created


def test_simulation_cannot_expose_other_tools_or_private_prompt_and_logs(monkeypatch):
    from app import prompts, memory_store, turnlog
    from app.tools import registry, load_tools

    monkeypatch.setattr(settings, "tools_enabled", True)
    monkeypatch.setattr(settings, "tool_groups", "memory,computer,smarthome")

    def forbidden(*args, **kwargs):
        raise AssertionError("private state or visitor logs accessed")

    monkeypatch.setattr(memory_store, "prompt_block", forbidden)
    monkeypatch.setattr(prompts, "_personal_docs_line", forbidden)
    monkeypatch.setattr(turnlog, "_record", forbidden)
    monkeypatch.setitem(registry._REGISTRY, "unsafe_host_tool", registry.Tool(
        name="unsafe_host_tool", description="must be blocked", parameters={},
        handler=forbidden, group=None))
    with use_backend(SimulatedRobot()):
        load_tools()
        text = prompts.build_instructions("private-project", profile="emma")
        assert "private-project" not in text
        assert "go_to_place" in text
        assert {t.name for t in registry.all_tools()} == SIMULATION_TOOLS
        assert asyncio.run(registry.dispatch("unsafe_host_tool", {}))["error"].startswith("tool disabled:")
        turnlog.record("said", text="simulation only")
        from app.providers.openai_realtime import build_session_config

        assert {t["name"] for t in build_session_config("marin", text)["session"]["tools"]} == SIMULATION_TOOLS
        from app.providers.gemini import GeminiProvider

        monkeypatch.setattr(settings, "web_search", True)
        config = GeminiProvider("Kore", text)._build_config()
        assert {d.name for tool in config["tools"] for d in (tool.function_declarations or [])} == SIMULATION_TOOLS
        assert all(tool.google_search is None for tool in config["tools"])
        monkeypatch.setattr(settings, "tools_enabled", False)
        assert registry.all_tools() == []
    assert settings.tool_groups == "memory,computer,smarthome"


@pytest.mark.timeout(10)
@pytest.mark.parametrize("change,expected", [("reset", False), ("stop", False), ("rejected", True)])
def test_pending_announcement_relevance_tracks_effective_commands(monkeypatch, change, expected):
    from app import events, session
    from app.robot_voice import watch_robot

    async def run():
        robot = SimulatedRobot()
        live = object()
        monkeypatch.setattr(session, "_active", live)
        captured = asyncio.Event()
        checked = asyncio.Event()

        async def announce(text, **kwargs):
            captured.set()
            await checked.wait()
            assert kwargs["still_relevant"]() is expected

        monkeypatch.setattr(events, "announce", announce)
        task = asyncio.create_task(watch_robot(live, robot))
        await asyncio.sleep(0)
        robot.submit("go_home", {}, "trip")
        robot.complete("trip", ok=True)
        await asyncio.wait_for(captured.wait(), 2)
        if change == "reset":
            robot.reset()
        elif change == "stop":
            robot.submit("cancel_navigation", {})
        else:
            robot.submit("move_to_point", {"place": "unknown"})
        checked.set()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
