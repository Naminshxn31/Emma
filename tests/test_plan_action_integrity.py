"""M1: provider success needs a same-session, same-byte render acknowledgement."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from fastapi.testclient import TestClient
import pytest

from app import plan_display, turnlog
from app.config import settings
from app.main import app
from app.session import VoiceSession
from app.tools import registry, units


IMAGE = b"verified image bytes"
DIGEST = hashlib.sha256(IMAGE).hexdigest()
PREPARED = {"ok": False, "pending_display": True, "screen": "plan",
            "project_id": "embassy_world",
            "image": "https://sales.test/fpg1.webp", "floor": 1, "building": None,
            "units": [{"no": "A-101"}], "counts": {"available": 1},
            "instruction": "not yet rendered"}


def test_verified_asset_route_serves_exact_cached_bytes_only(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "project_id", "embassy_world")
    monkeypatch.setattr(settings, "ws_token", "test-token")
    monkeypatch.setattr(settings, "inventory_plan_base", "https://sales.test")
    monkeypatch.setattr(units._time, "monotonic", lambda: 100.0)
    manifest = tmp_path / "plan.json"
    manifest.write_text(json.dumps({
        "source_id": "floor_plan_assets", "project_id": "embassy_world",
        "floors": [{"floor": 1, "status": "review-ready", "display_path": "/fpg1.webp",
                    "display_derivative_sha256": DIGEST}],
    }), encoding="utf-8")
    monkeypatch.setattr("app.data_sources.source_path", lambda source_id, project_id: manifest)
    units._PLAN_VERIFIED.clear()
    units._PLAN_VERIFIED[("embassy_world", PREPARED["image"], DIGEST)] = (
        99.0, DIGEST, "image/webp", IMAGE)
    client = TestClient(app)
    assert units.verified_plan_bytes(PREPARED["image"]) == (DIGEST, IMAGE)
    good = client.get(f"/verified-plan/{DIGEST}?token=test-token")
    assert good.status_code == 200 and good.content == IMAGE
    assert good.headers["cache-control"] == "no-store"
    assert client.get(f"/verified-plan/{DIGEST}").status_code == 403
    assert client.get(f"/verified-plan/{'0' * 64}?token=test-token").status_code == 404
    monkeypatch.setattr(settings, "project_id", "embassy_life")
    assert client.get(f"/verified-plan/{DIGEST}?token=test-token").status_code == 404
    monkeypatch.setattr(settings, "project_id", "embassy_world")
    manifest.write_text(json.dumps({
        "source_id": "floor_plan_assets", "project_id": "embassy_world",
        "floors": [{"floor": 1, "status": "draft", "display_path": "/fpg1.webp",
                    "display_derivative_sha256": DIGEST}],
    }), encoding="utf-8")
    assert client.get(f"/verified-plan/{DIGEST}?token=test-token").status_code == 404
    manifest.write_text(json.dumps({
        "source_id": "floor_plan_assets", "project_id": "embassy_world",
        "floors": [{"floor": 1, "status": "review-ready", "display_path": "/fpg1.webp",
                    "display_derivative_sha256": DIGEST}],
    }), encoding="utf-8")
    monkeypatch.setattr(units._time, "monotonic", lambda: 500.0)
    assert client.get(f"/verified-plan/{DIGEST}?token=test-token").status_code == 404
    units._PLAN_VERIFIED.clear()


def test_dispatch_waits_for_matching_render_ack_before_provider_success(monkeypatch):
    monkeypatch.setattr(settings, "project_id", "embassy_world")
    monkeypatch.setattr(units, "verified_plan_bytes", lambda url: (DIGEST, IMAGE))
    monkeypatch.setattr(registry._REGISTRY["show_plan"], "handler", lambda **kwargs: dict(PREPARED))
    token = turnlog.session_id.set("plan-session")
    commands = []

    async def send(command):
        commands.append(command)
        plan_display.receive_ack("plan-session", {
            "command_id": command["command_id"], "status": "rendered",
            "project_id": command["project_id"], "asset_id": command["asset_id"],
            "sha256": command["expected_sha256"],
        })

    plan_display.register("plan-session", send)
    try:
        result = asyncio.run(registry.dispatch("show_plan", {}))
    finally:
        plan_display.unregister("plan-session")
        turnlog.session_id.reset(token)
    assert result["ok"] and result["action_state"] == "RENDERED"
    assert result["render_ack"]["sha256"] == DIGEST
    assert "image" not in result and "units" not in result
    assert commands[0]["url"] == f"/verified-plan/{DIGEST}"
    assert "https://sales.test" not in str(commands)


def test_missing_display_and_wrong_session_ack_fail_closed(monkeypatch):
    monkeypatch.setattr(settings, "project_id", "embassy_world")
    monkeypatch.setattr(units, "verified_plan_bytes", lambda url: (DIGEST, IMAGE))
    monkeypatch.setattr(plan_display, "ACK_TIMEOUT_S", .01)
    token = turnlog.session_id.set("owner-session")
    commands = []
    try:
        assert not asyncio.run(plan_display.confirm_plan(PREPARED))["ok"]

        async def send(command):
            commands.append(command)
            if command["type"] == "display_cancel":
                return
            plan_display.receive_ack("other-session", {
                "command_id": command["command_id"], "status": "rendered",
                "project_id": "embassy_world", "asset_id": command["asset_id"],
                "sha256": DIGEST})

        plan_display.register("owner-session", send)
        result = asyncio.run(plan_display.confirm_plan(PREPARED))
        assert result["error"] == "display timeout"
        assert "image" not in result and "units" not in result
        assert commands[-1]["type"] == "display_cancel"
        assert commands[-1]["command_id"] == commands[0]["command_id"]
    finally:
        plan_display.unregister("owner-session")
        turnlog.session_id.reset(token)


def test_hash_mismatch_and_render_failure_never_confirm(monkeypatch):
    monkeypatch.setattr(settings, "project_id", "embassy_world")
    monkeypatch.setattr(units, "verified_plan_bytes", lambda url: (DIGEST, IMAGE))
    token = turnlog.session_id.set("plan-session")
    reply = {"sha256": "0" * 64, "status": "rendered"}

    async def send(command):
        plan_display.receive_ack("plan-session", {
            "command_id": command["command_id"], "project_id": command["project_id"],
            "asset_id": command["asset_id"], **reply})

    plan_display.register("plan-session", send)
    try:
        assert asyncio.run(plan_display.confirm_plan(PREPARED))["error"] == "display hash mismatch"
        reply.update(sha256=DIGEST, status="load_failed")
        assert asyncio.run(plan_display.confirm_plan(PREPARED))["error"] == "display render failed"
        wrong = {**PREPARED, "project_id": "embassy_life"}
        assert asyncio.run(plan_display.confirm_plan(wrong))["error"] == "project scope mismatch"
    finally:
        plan_display.unregister("plan-session")
        turnlog.session_id.reset(token)


def test_stalled_display_send_cannot_hold_provider_open(monkeypatch):
    monkeypatch.setattr(settings, "project_id", "embassy_world")
    monkeypatch.setattr(units, "verified_plan_bytes", lambda url: (DIGEST, IMAGE))
    monkeypatch.setattr(plan_display, "SEND_TIMEOUT_S", .01)
    token = turnlog.session_id.set("stalled-session")
    commands = []

    async def send(command):
        commands.append(command)
        if command["type"] == "display_command":
            await asyncio.Event().wait()

    plan_display.register("stalled-session", send)
    try:
        result = asyncio.run(plan_display.confirm_plan(PREPARED))
        assert not result["ok"] and result["action_state"] == "TIMEOUT"
        assert [command["type"] for command in commands] == ["display_command", "display_cancel"]
    finally:
        plan_display.unregister("stalled-session")
        turnlog.session_id.reset(token)


def test_voice_socket_pump_routes_ack_to_pending_action(monkeypatch):
    monkeypatch.setattr(settings, "project_id", "embassy_world")
    monkeypatch.setattr(units, "verified_plan_bytes", lambda url: (DIGEST, IMAGE))

    async def scenario():
        class Socket:
            def __init__(self):
                self.incoming = asyncio.Queue()

            async def receive(self):
                return {"text": await self.incoming.get()}

        socket = Socket()
        session = VoiceSession.__new__(VoiceSession)
        session.ws = socket
        token = turnlog.session_id.set("socket-session")

        async def send(command):
            await socket.incoming.put(json.dumps({
                "type": "display.render_ack", "command_id": command["command_id"],
                "project_id": command["project_id"], "asset_id": command["asset_id"],
                "sha256": command["expected_sha256"], "status": "rendered"}))

        plan_display.register("socket-session", send)
        pump = asyncio.create_task(session._browser_to_provider())
        try:
            result = await plan_display.confirm_plan(PREPARED)
            assert result["ok"] and result["action_state"] == "RENDERED"
        finally:
            await socket.incoming.put(json.dumps({"type": "stop"}))
            await pump
            plan_display.unregister("socket-session")
            turnlog.session_id.reset(token)

    asyncio.run(scenario())


def test_browser_requires_hash_and_does_not_reload_plan_on_tool_result():
    source = (Path(__file__).parents[1] / "client" / "index.html").read_text(encoding="utf-8")
    assert "await planSha256Hex(await blob.arrayBuffer())" in source
    assert "await decoded.decode()" in source and "await shown.decode()" in source
    assert "case 'display_command':" in source
    assert "case 'display_cancel':" in source and "pending.controller.abort()" in source
    assert "if (r.screen === 'plan') showPlan(r)" not in source


def test_lan_http_sha256_fallback_matches_known_vectors():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable; browser SHA-256 fallback not executable here")
    source = (Path(__file__).parents[1] / "client" / "index.html").read_text(encoding="utf-8")
    code = source.split("async function planSha256Hex(buffer) {", 1)[1].split(
        "async function handlePlanDisplayCommand", 1)[0]
    vectors = ["", "abc", "x" * 65000]
    harness = ("Object.defineProperty(globalThis, 'crypto', {value: undefined});\n"
               + "async function planSha256Hex(buffer) {" + code
               + "\n(async () => { for (const value of " + json.dumps(vectors)
               + ") console.log(await planSha256Hex(new TextEncoder().encode(value).buffer)); })();")
    result = subprocess.run([node, "-"], input=harness, capture_output=True, text=True,
                            timeout=10, check=True)
    assert result.stdout.splitlines() == [hashlib.sha256(v.encode()).hexdigest()
                                           for v in vectors]
