"""M0.3c: policy must survive the last provider, UI and adapter exits."""
from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace

import pytest

from app.config import settings
from app.tools import project_knowledge, registry, slides, units


BLOCKED = "BLOCKED_EGRESS_SENTINEL"


def run(coro):
    return asyncio.run(coro)


def test_direct_display_adapter_sanitizes_and_rechecks_project(monkeypatch):
    from app import display
    from app.tools import canva_display

    original = slides.load_slides()[0]
    draft = {**original, "title_th": BLOCKED, "summary_th": BLOCKED,
             "hidden": BLOCKED}
    monkeypatch.setattr(slides, "load_slides", lambda: [draft])
    monkeypatch.setattr(display, "_machine_owned", lambda: True)
    emitted, commands = [], []

    async def broadcast(payload):
        emitted.append(payload)

    async def goto(slide_id):
        commands.append(slide_id)

    monkeypatch.setattr(display, "broadcast", broadcast)
    monkeypatch.setattr(canva_display, "goto", goto)
    run(display._reveal({**draft, "project_id": "embassy_life"}))
    assert emitted == commands == []
    raw = {**draft, "url": "/slides/other.jpg"}
    run(display._reveal(raw))
    assert commands == [draft["id"]]
    assert BLOCKED not in json.dumps(emitted, ensure_ascii=False)
    assert emitted[0]["slide"]["url"] != raw["url"]

    safe = slides._public(draft)
    run(display._reveal(safe))
    assert commands == [draft["id"], draft["id"]]
    assert emitted[0]["slide"]["capabilities"] == {"display": True, "model_text": False}
    assert BLOCKED not in json.dumps(emitted, ensure_ascii=False)

    monkeypatch.setattr(settings, "project_id", "embassy_life")
    run(display._reveal(safe))
    assert len(emitted) == len(commands) == 2


def test_delayed_reveal_rechecks_revoked_approval(monkeypatch):
    from app import display

    source = {**slides.load_slides()[0], "approval_status": "approved",
              "approved_by": "test_reviewer", "approved_at": "2026-01-01T00:00:00+07:00",
              "effective_at": "2026-01-01T00:00:00+07:00",
              "disclosure_scope": "customer", "content_state": "existing",
              "title_th": "test approved title"}
    monkeypatch.setattr(slides, "load_slides", lambda: [source])
    old = slides._public(source)
    assert old["capabilities"]["model_text"]
    source["approval_status"] = "draft"
    source["title_th"] = BLOCKED
    monkeypatch.setattr(display, "_machine_owned", lambda: True)
    emitted = []

    async def broadcast(payload):
        emitted.append(payload)

    async def noop(slide_id):
        pass

    monkeypatch.setattr(display, "broadcast", broadcast)
    from app.tools import canva_display
    monkeypatch.setattr(canva_display, "goto", noop)
    run(display._reveal(old))
    assert emitted[0]["slide"]["capabilities"] == {"display": True, "model_text": False}
    assert "test approved title" not in json.dumps(emitted)
    assert BLOCKED not in json.dumps(emitted)


def test_direct_broadcast_cannot_bypass_display_policy(monkeypatch):
    from app import display

    draft = {**slides.load_slides()[0], "title_th": BLOCKED, "hidden": BLOCKED}
    monkeypatch.setattr(slides, "load_slides", lambda: [draft])
    messages = []

    class Client:
        async def send_text(self, payload):
            messages.append(payload)

    monkeypatch.setattr(display, "_clients", {Client()})
    run(display.broadcast({"type": "slide", "slide": {**draft,
                          "project_id": "embassy_life"}}))
    assert messages == []
    run(display.broadcast({"type": "slide", "slide": {**draft,
                          "url": "/slides/unchecked.jpg"}}))
    assert len(messages) == 1 and BLOCKED not in messages[0]
    assert "unchecked.jpg" not in messages[0]


def test_direct_canva_adapter_requires_registered_display_asset(monkeypatch):
    from pathlib import Path
    from app.tools import canva_display

    map_path = Path(settings.slides_dir) / "canva_pages.json"
    configured = json.loads(map_path.read_text(encoding="utf-8"))["deck_url"]
    monkeypatch.setattr(settings, "canva_url", configured)
    mapping = canva_display.load_page_map(force=True)
    slide_id = next(iter(mapping))
    original = next(s for s in slides.load_slides() if s["id"] == slide_id)
    monkeypatch.setattr(slides, "load_slides", lambda: [
        {**original, "file": "unregistered.jpg", "title_th": BLOCKED}])
    assert canva_display._page_number(slide_id) is None
    before = canva_display._wanted
    run(canva_display.goto(slide_id))
    assert canva_display._wanted == before


def test_cached_routing_and_plan_paths_do_not_cross_project(monkeypatch, tmp_path):
    path = tmp_path / "vocabulary.json"
    source = {"source_id": "project_vocabulary", "project_id": settings.project_id,
              "entities": [{"aliases": ["query"], "search_hint": BLOCKED}]}
    path.write_text(json.dumps(source), encoding="utf-8")
    monkeypatch.setattr(settings, "project_knowledge_file", str(path))
    project_knowledge.reset()
    assert BLOCKED in project_knowledge.expand("query")

    monkeypatch.setattr(settings, "project_id", "embassy_life")
    assert BLOCKED not in project_knowledge.expand("query")
    assert units._plan_paths() == {}
    source["project_id"] = "embassy_life"
    source["entities"][0]["search_hint"] = "NEW_PROJECT_ONLY"
    path.write_text(json.dumps(source), encoding="utf-8")
    assert project_knowledge.expand("query").endswith("NEW_PROJECT_ONLY")
    project_knowledge.reset()


def test_unit_caches_are_scoped_even_with_identical_paths_and_room(monkeypatch, tmp_path):
    source = {"source_id": "local_unit_inventory", "project_id": settings.project_id,
              "units": [{"room": "A801", "price_thb": BLOCKED}]}
    path = tmp_path / "units.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    monkeypatch.setattr(settings, "units_file", str(path))
    monkeypatch.setattr(settings, "units_sample", False)
    units.reset()
    assert units._load()["units"][0]["price_thb"] == BLOCKED

    units._live_cache[(settings.project_id, "A801")] = (units._time.monotonic(),
                                                        {"project_id": settings.project_id,
                                                         "price_thb": BLOCKED})
    monkeypatch.setattr(settings, "project_id", "embassy_life")
    assert units._load() == {}
    calls = []
    monkeypatch.setattr(units, "_scoped", lambda query: query)
    monkeypatch.setattr(units, "_live_get", lambda query: calls.append(query) or [{}])
    monkeypatch.setattr(units, "_card_from_live", lambda row: {"project_id": settings.project_id})
    assert units._live_find("A801") == {"project_id": "embassy_life"}
    assert len(calls) == 1
    units.reset()


def test_same_size_same_mtime_revocation_cannot_keep_static_inventory(monkeypatch, tmp_path):
    source = {"source_id": "local_unit_inventory", "project_id": settings.project_id,
              "approval_status": "approved", "approved_by": "test_reviewer",
              "approved_at": "2026-01-01T00:00:00+07:00",
              "effective_at": "2026-01-01T00:00:00+07:00",
              "disclosure_scope": "customer", "content_state": "existing",
              "units": [{"room": "A801", "status": "available"}]}
    path = tmp_path / "units.json"
    before = json.dumps(source)
    path.write_text(before, encoding="utf-8")
    monkeypatch.setattr(settings, "units_file", str(path))
    monkeypatch.setattr(settings, "units_sample", False)
    units.reset()
    assert units.show_unit("A801")["ok"]
    stamp = path.stat()

    source["approval_status"] = "draft"
    after = json.dumps(source)
    path.write_text(after + " " * (len(before) - len(after)), encoding="utf-8")
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert path.stat().st_size == stamp.st_size
    assert path.stat().st_mtime_ns == stamp.st_mtime_ns
    blocked = units.show_unit("A801")
    assert blocked["ok"] is False and "unit" not in blocked
    units.reset()


def test_approved_wrong_source_cannot_enter_prompt_via_explicit_path(tmp_path):
    from app.prompts import FALLBACK_FACTS, load_facts

    path = tmp_path / "facts.json"
    source = {"source_id": "other_source", "project_id": settings.project_id,
              "approval_status": "approved", "approved_by": "test_reviewer",
              "approved_at": "2026-01-01T00:00:00+07:00",
              "effective_at": "2026-01-01T00:00:00+07:00",
              "disclosure_scope": "customer", "content_state": "existing",
              "facts": [{"label": "test", "value": BLOCKED}]}
    path.write_text(json.dumps(source), encoding="utf-8")
    assert load_facts(str(path)) == FALLBACK_FACTS


def test_explicit_tool_group_cannot_enable_ungated_condo_sources(monkeypatch):
    from app.tools import websearch

    monkeypatch.setattr(settings, "assistant_profile", "condo")
    monkeypatch.setattr(settings, "tool_groups", "websearch,computer,memory,mydocs")
    monkeypatch.setattr(settings, "tools_enabled", True)
    for group in ("websearch", "computer", "memory", "mydocs"):
        entry = registry.Tool("test", "", {}, lambda: {}, group=group)
        assert not registry.allowed(entry)
    assert websearch.search_web(BLOCKED)["ok"] is False


def test_nested_tuple_from_direct_adapter_cannot_evade_scope_gate(monkeypatch):
    monkeypatch.setattr(settings, "tools_enabled", True)
    monkeypatch.setattr(registry, "_REGISTRY", registry._REGISTRY.copy())
    registry._REGISTRY["nested_probe"] = registry.Tool(
        "nested_probe", "", {}, lambda: {"items": ({"project_id": "embassy_life",
                                             "source_id": "foreign.source", "text": BLOCKED},)})
    result = run(registry.dispatch("nested_probe", {}))
    assert result == {"ok": False, "error": "project scope mismatch"}
    assert BLOCKED not in json.dumps(result)


def test_upstream_error_body_is_not_echoed_to_browser():
    from app.providers.base import ProviderEvent
    from app.session import VoiceSession

    class FakeSocket:
        async def send_text(self, payload):
            pass

    class Provider:
        async def events(self):
            yield ProviderEvent(kind="error", text=BLOCKED)

    session = VoiceSession(FakeSocket())
    session.provider = Provider()
    emitted = []

    async def capture(payload):
        emitted.append(payload)

    session._send_json = capture
    run(session._provider_to_browser())
    assert emitted[0]["type"] == "error"
    assert BLOCKED not in json.dumps(emitted, ensure_ascii=False)


@pytest.mark.parametrize("provider_kind", ["gemini", "openai"])
def test_provider_serialization_and_tool_side_effects_fail_closed(
        monkeypatch, provider_kind):
    from app.providers.gemini import GeminiProvider
    from app.providers.openai_realtime import OpenAIProvider

    monkeypatch.setattr(settings, "tools_enabled", True)
    existing = registry._REGISTRY.copy()
    monkeypatch.setattr(registry, "_REGISTRY", existing)
    emitted = []

    async def push():
        emitted.append(BLOCKED)

    monkeypatch.setattr(registry, "_push_to_displays", push)
    registry._REGISTRY["foreign_probe"] = registry.Tool(
        "foreign_probe", "", {}, lambda: {"ok": True, "project_id": "embassy_life",
                                                "source_id": "foreign.source", "hidden": BLOCKED},
        tags=["slides"])

    def broken():
        raise RuntimeError(BLOCKED)

    registry._REGISTRY["broken_probe"] = registry.Tool("broken_probe", "", {}, broken)
    draft = {**slides.load_slides()[0], "title_th": BLOCKED,
             "summary_th": BLOCKED, "hidden": BLOCKED}
    registry._REGISTRY["draft_probe"] = registry.Tool(
        "draft_probe", "", {}, lambda: {"ok": True, "slide": slides._public(draft)})
    calls = [("c1", "foreign_probe", {}), ("c2", "broken_probe", {}),
             ("c3", "draft_probe", {})]

    async def capture():
        if provider_kind == "gemini":
            provider = GeminiProvider("voice", "instructions")

            class FakeSession:
                async def send_tool_response(self, **kwargs):
                    emitted.append(kwargs)

            provider._session = FakeSession()
            invocations = [SimpleNamespace(id=cid, name=name, args=args)
                           for cid, name, args in calls]
            return [event async for event in provider._run_tool_calls(invocations)]
        provider = OpenAIProvider("voice", "instructions")

        class FakeSocket:
            async def send(self, payload):
                emitted.append(payload)

        provider._ws = FakeSocket()
        invocations = [{"call_id": cid, "name": name, "arguments": "{}"}
                       for cid, name, _args in calls]
        return [event async for event in provider._run_tool_calls(invocations)]

    events = run(capture())
    provider_payload = json.dumps(emitted, ensure_ascii=False, default=str)
    assert BLOCKED not in provider_payload
    assert "foreign.source" not in provider_payload
    assert BLOCKED not in json.dumps([e.data for e in events], default=str)
    assert [e.data["error"] for e in events if e.kind == "tool_result"
            and "error" in e.data] == [
        "project scope mismatch", "tool unavailable"]
