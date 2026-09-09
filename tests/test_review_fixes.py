"""Regression coverage for the eight issues reproduced in the September review."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
from threading import Event, get_ident
from types import SimpleNamespace

import pytest

from app.config import settings


@pytest.mark.parametrize("groups,multi,enabled", [
    ("knowledge", True, True), ("knowledge", False, True), ("", False, False),
])
def test_cold_import_cannot_grant_disabled_tools(groups, multi, enabled):
    # Process isolation exercises decorator registration, not a preloaded test
    # registry. No server startup, API calls, models, or hardware are opened.
    script = f'''
import asyncio
from app.config import settings
settings.tool_groups={groups!r}
settings.multi_session={multi!r}
settings.tools_enabled={enabled!r}
settings.assistant_profile='condo'
from app import tools
from app.tools import slides, units, computer
tools.load_tools()
names={{t['name'] for t in tools.as_openai_tools()}}
assert names == {{'search_condo_info'}} if {enabled!r} else names == set(), names
spec=tools.as_gemini_tool()
assert ({{f.name for f in spec.function_declarations}} if spec else set()) == names
for name in ['start_presentation', 'show_unit', 'open_program']:
    result=asyncio.run(tools.dispatch(name, {{}}))
    assert result['ok'] is False and 'disabled' in result['error'], result
'''
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", script],
                            capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("shared,groups", [(True, "memory"), (False, "knowledge")])
def test_disallowed_private_memory_is_never_read(monkeypatch, shared, groups):
    from app import memory_store
    from app.prompts import build_instructions

    monkeypatch.setattr(settings, "multi_session", shared)
    monkeypatch.setattr(settings, "tool_groups", groups)
    def refuse():
        pytest.fail("the private store was read")
    monkeypatch.setattr(memory_store, "prompt_block", refuse)
    text = build_instructions("review", profile="emma")
    assert "PRIVATE_SENTINEL" not in text


def test_owner_memory_still_works_when_explicitly_enabled(monkeypatch):
    from app import memory_store
    from app.prompts import build_instructions

    monkeypatch.setattr(settings, "tool_groups", "memory")
    monkeypatch.setattr(memory_store, "prompt_block", lambda: "PRIVATE_SENTINEL")
    assert "PRIVATE_SENTINEL" in build_instructions("review", profile="emma")


def test_url_cannot_unlock_emma_on_a_condo_machine(monkeypatch):
    from app.session import VoiceSession

    monkeypatch.setattr(settings, "assistant_profile", "condo")
    assert VoiceSession(None, profile="emma").profile == "condo"
    assert VoiceSession(None, profile="translator").profile == "translator"
    monkeypatch.setattr(settings, "assistant_profile", "emma")
    assert VoiceSession(None).profile == "emma"


@pytest.mark.parametrize("approved", [True, False, None])
@pytest.mark.parametrize("language", ["th", "en"])
def test_retrieval_preserves_script_approval(approved, language):
    from app.tools.knowledge import _entry

    slide = {f"script_{language}": "REVIEW_SCRIPT"}
    if approved is not None:
        slide["script_approved"] = approved
    out = _entry(slide)
    if approved:
        assert out["approved_script"] == "REVIEW_SCRIPT"
        assert "draft_script" not in out
    else:
        assert "approved_script" not in out
        assert out["draft_script"] == "REVIEW_SCRIPT" and out["script_is_draft"]


def test_shared_knowledge_does_not_mutate_presentation(monkeypatch):
    from app.tools import knowledge, slides, webstage

    monkeypatch.setattr(settings, "multi_session", True)
    monkeypatch.setattr(settings, "web_stage", True)
    slide = {"id": "review", "title_en": "pool", "file": "review.jpg"}
    monkeypatch.setattr(slides, "search_slides", lambda _: [SimpleNamespace(slide=slide, found=True)])
    monkeypatch.setattr(knowledge, "confident_enough_to_show", lambda *_: True)
    def refuse(*_):
        pytest.fail("shared caller tried to change the machine screen")
    monkeypatch.setattr(slides, "show_current", refuse)
    assert "now_showing" not in knowledge.search_condo_info("pool")
    assert not webstage.enabled()
    webstage.request("https://example.invalid")
    assert webstage._wanted is None


def test_inventory_worker_keeps_loop_responsive_and_refuses_duplicates(monkeypatch):
    from app.tools import registry, units

    monkeypatch.setattr(settings, "tool_groups", "units")
    monkeypatch.setattr(registry, "TOOL_TIMEOUT_S", .05)
    entered, release = Event(), Event()
    effects = []
    def stalled(**_):
        from app.tool_io import on_loop
        entered.set()
        assert release.wait(2)
        on_loop(effects.append, "late screen")
        return {"ok": True}
    monkeypatch.setitem(registry._REGISTRY, "show_unit",
                        replace(registry.get("show_unit"), handler=stalled))
    async def scenario():
        pending = asyncio.create_task(registry.dispatch("show_unit", {"room": "A801"}))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            # This runs while the worker is stalled; a blocking loop can't.
            busy = await registry.dispatch("show_unit", {"room": "A802"})
            assert busy["error"] == "busy"
            out = await pending
            assert out["error"] == "timeout"
            assert (await registry.dispatch("show_unit", {}))["error"] == "busy"
        finally:
            release.set()
            job = registry._inflight.get("show_unit")
            if job is not None:
                await asyncio.gather(job, return_exceptions=True)
        assert effects == [], "timed-out worker changed the screen later"
    asyncio.run(scenario())


def test_worker_ui_handoff_runs_on_the_event_loop():
    from app.tool_io import on_loop, run_blocking

    async def scenario():
        owner = get_ident()
        def worker():
            assert get_ident() != owner
            return on_loop(get_ident)
        assert await run_blocking(worker) == owner
    asyncio.run(scenario())


def test_printer_timeout_does_not_submit_a_duplicate(monkeypatch):
    from app.tools import documents, registry

    entered, release = Event(), Event()
    sent = []
    item = {"name": "fixture", "_path": Path("unused.pdf")}
    monkeypatch.setattr(documents, "load_catalogue", lambda: [item])
    monkeypatch.setattr(documents, "find", lambda _: item)
    monkeypatch.setattr(registry, "SLOW_TOOL_TIMEOUT_S", .05)
    def printer(*_):
        sent.append("print")
        entered.set()
        assert release.wait(2)
        return True, "simulated"
    monkeypatch.setattr(documents, "send_to_printer", printer)
    async def scenario():
        try:
            result = await registry.dispatch("print_document", {"document": "fixture"})
            assert entered.is_set() and result["error"] == "timeout"
            assert (await registry.dispatch("print_document", {"document": "fixture"}))["error"] == "busy"
        finally:
            release.set()
            job = registry._inflight.get("print_document")
            if job is not None:
                await asyncio.gather(job, return_exceptions=True)
        assert sent == ["print"]
    asyncio.run(scenario())


class FakeWS:
    async def send_text(self, *_):
        pass
    async def send(self, *_):
        pass
    async def close(self):
        pass


def test_consecutive_visitors_do_not_share_calc_state(monkeypatch):
    from app import session
    from app.tools import calc, slides

    starts = []
    async def fake_run(self):
        starts.append((dict(calc.STATE), slides.current_slide()))
        calc.STATE["price_thb"] = 8765432
        slides.STATE["current"] = {"id": "previous visitor"}
    monkeypatch.setattr(session.VoiceSession, "run", fake_run)
    async def scenario():
        await session.handle_connection(FakeWS())
        await session.handle_connection(FakeWS())
        assert calc.STATE == {} and slides.current_slide() is None
    asyncio.run(scenario())
    assert starts == [({}, None), ({}, None)]


@pytest.mark.parametrize("action", ["stop", "move_to_point"])
def test_real_session_transport_failure_is_not_robot_success(monkeypatch, action):
    from app import session
    from app.tools import robot_link

    class BrokenWS(FakeWS):
        async def send_text(self, *_):
            raise ConnectionError("simulated disconnect")
    monkeypatch.setattr(settings, "robot_enabled", True)
    monkeypatch.setattr(robot_link, "available", lambda: True)
    monkeypatch.setattr(session, "_active", session.VoiceSession(BrokenWS()))
    assert asyncio.run(robot_link.send(action)) == "failed"


@pytest.mark.parametrize("chunks", [["hello", " world"], ["a long block\nwith multiple lines\nthat is more than forty characters"]])
def test_normal_and_silent_transcripts_are_logged_once(monkeypatch, chunks):
    from app import session, turnlog
    from app.providers.base import ProviderEvent

    monkeypatch.setattr(settings, "multi_session", True)
    records = []
    monkeypatch.setattr(turnlog, "record", lambda event, **kw: records.append((event, kw)))
    class Provider:
        async def events(self):
            for text in chunks:
                yield ProviderEvent(kind="assistant_transcript", text=text)
    live = session.VoiceSession(FakeWS())
    live.provider = Provider()
    asyncio.run(live._provider_to_browser())
    assert [row["text"] for event, row in records if event == "said"] == chunks


def test_slide_metadata_is_never_served_and_images_require_token(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.slide_assets import SlideAssets

    for filename in ("index.json", "embeddings.npz", "backup.json", "sample.png"):
        (tmp_path / filename).write_bytes(b"fixture")
    app = FastAPI()
    app.mount("/slides", SlideAssets(directory=str(tmp_path)))
    monkeypatch.setattr(settings, "ws_token", "review-secret")
    client = TestClient(app)
    for filename in ("index.json", "embeddings.npz", "backup.json"):
        assert client.get(f"/slides/{filename}").status_code == 404
        assert client.get(f"/slides/{filename}?token=review-secret").status_code == 404
    assert client.get("/slides/sample.png").status_code == 403
    assert client.get("/slides/sample.png?token=wrong").status_code == 403
    response = client.get("/slides/sample.png?token=review-secret")
    assert response.status_code == 200 and response.content == b"fixture"
    assert response.headers["cache-control"] == "private, no-store"
    assert client.get("/slides/%2e%2e/secret.png?token=review-secret").status_code == 404
    monkeypatch.setattr(settings, "ws_token", "")
    assert client.get("/slides/sample.png").status_code == 200


@pytest.mark.parametrize("provider_name", ["gemini", "openai"])
def test_toolless_provider_refuses_unexpected_tool_calls(monkeypatch, provider_name):
    from app import tools
    from app.providers.gemini import GeminiProvider
    from app.providers.openai_realtime import OpenAIProvider

    async def refuse(*_):
        pytest.fail("translator executed a tool")
    monkeypatch.setattr(tools, "dispatch_all", refuse)
    if provider_name == "gemini":
        provider = GeminiProvider("Kore", "review", use_tools=False)
        calls = [SimpleNamespace(id="c1", name="open_program", args={"name": "fixture"})]
    else:
        provider = OpenAIProvider("marin", "review", use_tools=False)
        provider._ws = FakeWS()
        calls = [{"call_id": "c1", "name": "open_program", "arguments": "{}"}]
    async def scenario():
        events = [event async for event in provider._run_tool_calls(calls)]
        results = [event.data for event in events if event.kind == "tool_result"]
        assert len(results) == 1 and results[0]["ok"] is False
    asyncio.run(scenario())


def test_two_visitors_can_query_the_same_worker_tool(monkeypatch):
    from app import turnlog
    from app.tools import registry, units

    monkeypatch.setattr(settings, "tool_groups", "units")
    monkeypatch.setattr(settings, "multi_session", True)
    entered, release = Event(), Event()
    arrivals = []
    def query(room):
        arrivals.append(room)
        if len(arrivals) == 2:
            entered.set()
        assert release.wait(2)
        return {"room": room, "scope": turnlog.session_id.get()}
    monkeypatch.setitem(registry._REGISTRY, "show_unit",
                        replace(registry.get("show_unit"), handler=query))
    async def visit(room):
        token = turnlog.session_id.set(room)
        try:
            return await registry.dispatch("show_unit", {"room": room})
        finally:
            turnlog.session_id.reset(token)
    async def scenario():
        tasks = [asyncio.create_task(visit(room)) for room in ("A801", "A802")]
        try:
            assert await asyncio.to_thread(entered.wait, 1), "one visitor blocked the other"
        finally:
            release.set()
            results = await asyncio.gather(*tasks)
        assert [(r["room"], r["scope"]) for r in results] == [("A801", "A801"), ("A802", "A802")]
    asyncio.run(scenario())


def test_finished_visitor_cannot_reveal_a_queued_picture(monkeypatch):
    from app import display, session

    shown = []
    async def reveal(slide):
        shown.append(slide)
    monkeypatch.setattr(display, "_reveal", reveal)
    async def scenario():
        display.set_audio_lead(220)
        await display.show({"id": "old visitor"})
        session._reset_conversation_state()
        await asyncio.sleep(.25)
        assert shown == []
    asyncio.run(scenario())


def test_old_session_cleanup_leaves_new_visitors_numbers_intact(monkeypatch):
    from app import session
    from app.tools import calc

    async def scenario():
        old_ready, new_ready = asyncio.Event(), asyncio.Event()
        end_old, end_new = asyncio.Event(), asyncio.Event()
        old_ws, new_ws = FakeWS(), FakeWS()
        async def run(self):
            if self.ws is old_ws:
                old_ready.set()
                await end_old.wait()
            else:
                calc.STATE["price_thb"] = 1234567
                new_ready.set()
                await end_new.wait()
        monkeypatch.setattr(session.VoiceSession, "run", run)
        first = asyncio.create_task(session.handle_connection(old_ws))
        await old_ready.wait()
        second = asyncio.create_task(session.handle_connection(new_ws))
        await new_ready.wait()
        end_old.set()
        await first
        assert calc.STATE["price_thb"] == 1234567
        assert session._active.ws is new_ws
        end_new.set()
        await second
    asyncio.run(scenario())


def test_worker_log_rows_keep_their_session_identity(tmp_path, monkeypatch):
    from app import turnlog
    from app.tool_io import run_blocking

    monkeypatch.setattr(settings, "turn_log", True)
    monkeypatch.setattr(settings, "turn_log_dir", str(tmp_path))
    turnlog.close()
    async def visit(sid):
        token = turnlog.session_id.set(sid)
        try:
            await run_blocking(turnlog.record, "said", text="synthetic", visitor=sid)
        finally:
            turnlog.session_id.reset(token)
    async def scenario():
        await asyncio.gather(visit("one"), visit("two"))
    try:
        asyncio.run(scenario())
    finally:
        turnlog.close()
    rows = [json.loads(line) for file in tmp_path.glob("*.jsonl")
            for line in file.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert all(row["session_id"] == row["visitor"] for row in rows)
