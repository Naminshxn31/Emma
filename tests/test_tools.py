"""
Tests for the tool system: registry, smarthome handlers, and the two
provider adapters.

IR is stubbed throughout — the point is the wiring and the failure
behaviour, not whether a Broadlink hub is on the desk.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.config import settings
from app.tools import registry


@pytest.fixture
def clean_registry():
    """Isolate a test's registrations from the real tool set."""
    saved = dict(registry._REGISTRY)
    registry._REGISTRY.clear()
    yield registry
    registry._REGISTRY.clear()
    registry._REGISTRY.update(saved)


@pytest.fixture
def smarthome(monkeypatch):
    """Load the real smarthome tools with the IR sender stubbed."""
    import app.tools as tools_pkg
    from app.tools import broadlink_ir, smarthome as sh

    tools_pkg.load_tools()
    sent: list[tuple[str, int]] = []

    def fake_send(code, repeat=1, delay=0.6):
        sent.append((code, repeat))
        return fake_send.result

    fake_send.result = "ok"
    monkeypatch.setattr(broadlink_ir, "send", fake_send)
    monkeypatch.setattr(broadlink_ir, "available", lambda: True)

    sh.STATE["lights"] = True
    sh.STATE["ac"] = {"on": True, "temp": 25, "fan": "auto", "mode": "cool"}
    return type("S", (), {"sent": sent, "send": fake_send, "state": sh.STATE, "mod": sh})


def run(coro):
    return asyncio.run(coro)


# ==================== registry ====================


def test_registering_and_dispatching(clean_registry):
    @clean_registry.tool("greet", "say hi", {"type": "object", "properties": {}})
    def greet():
        return {"hello": True}

    assert run(clean_registry.dispatch("greet", {})) == {"hello": True, "ok": True}


def test_project_tool_result_cannot_return_a_foreign_project(clean_registry):
    @clean_registry.tool(name="foreign_slide", description="test", tags=["slides"])
    def foreign_slide():
        return {"ok": True, "slide": {"project_id": "embassy_life"}}

    out = run(clean_registry.dispatch("foreign_slide", {}))
    assert out == {"ok": False, "error": "project scope mismatch"}


def test_dispatch_supports_async_handlers(clean_registry):
    @clean_registry.tool("slow", "async tool")
    async def slow():
        await asyncio.sleep(0)
        return {"done": True}

    assert run(clean_registry.dispatch("slow", {}))["done"] is True


def test_unknown_tool_is_reported_not_raised(clean_registry):
    """The model can hallucinate a tool name; that must not kill the call."""
    out = run(clean_registry.dispatch("does_not_exist", {}))
    assert out["ok"] is False and "unknown tool" in out["error"]


def test_handler_exception_becomes_a_result(clean_registry):
    """A crashing tool must leave the guest with an answer, not silence."""

    @clean_registry.tool("boom", "explodes")
    def boom():
        raise RuntimeError("hub on fire")

    out = run(clean_registry.dispatch("boom", {}))
    assert out == {"ok": False, "error": "tool unavailable"}


def test_bad_arguments_are_reported(clean_registry):
    @clean_registry.tool("needs_arg", "x")
    def needs_arg(value: int):
        return {"value": value}

    out = run(clean_registry.dispatch("needs_arg", {"wrong": 1}))
    assert out["ok"] is False and "invalid arguments" in out["error"]


def test_duplicate_registration_is_rejected(clean_registry):
    @clean_registry.tool("dup", "first")
    def a():
        return {}

    with pytest.raises(ValueError):
        @clean_registry.tool("dup", "second")
        def b():
            return {}


def test_dispatch_all_runs_every_call(clean_registry):
    @clean_registry.tool("echo", "echo")
    def echo(n: int):
        return {"n": n}

    results = run(clean_registry.dispatch_all([
        ("c1", "echo", {"n": 1}),
        ("c2", "echo", {"n": 2}),
    ]))
    assert [r[0] for r in results] == ["c1", "c2"]
    assert [r[2]["n"] for r in results] == [1, 2]


def test_dispatch_all_preserves_model_call_order(clean_registry):
    """State-changing calls must not race and overwrite the screen."""
    events = []

    @clean_registry.tool("first", "first")
    async def first():
        events.append("first-start")
        await asyncio.sleep(0.01)
        events.append("first-end")
        return {}

    @clean_registry.tool("second", "second")
    async def second():
        events.append("second")
        return {}

    run(clean_registry.dispatch_all([
        ("c1", "first", {}),
        ("c2", "second", {}),
    ]))
    assert events == ["first-start", "first-end", "second"]


def test_non_dict_return_is_wrapped(clean_registry):
    @clean_registry.tool("plain", "returns a string")
    def plain():
        return "hello"

    assert run(clean_registry.dispatch("plain", {})) == {"ok": True, "result": "hello"}


# ==================== provider adapters ====================


def test_openai_tool_schema_shape(clean_registry):
    @clean_registry.tool(
        "set_thing", "does a thing",
        {"type": "object", "properties": {"on": {"type": "boolean"}}, "required": ["on"]},
    )
    def set_thing(on: bool):
        return {}

    spec = clean_registry.as_openai_tools()[0]
    assert spec["type"] == "function"
    assert spec["name"] == "set_thing"
    assert spec["parameters"]["required"] == ["on"]


def test_gemini_tool_declarations(clean_registry):
    @clean_registry.tool("set_thing", "does a thing",
                         {"type": "object", "properties": {}})
    def set_thing():
        return {}

    gtool = clean_registry.as_gemini_tool()
    assert gtool is not None
    assert [d.name for d in gtool.function_declarations] == ["set_thing"]


def test_gemini_declaration_carries_typed_parameters(clean_registry):
    """Regression: using `parameters_json_schema` left the schema as a raw
    dict the Live API didn't pass on, so the model never saw the argument
    names and invented its own — set_lights(lights_on=...) instead of
    set_lights(on=...). `parameters` produces a real typed Schema."""
    @clean_registry.tool(
        "set_lights", "toggle",
        {"type": "object",
         "properties": {"on": {"type": "boolean", "description": "true=on"}},
         "required": ["on"]},
    )
    def set_lights(on: bool):
        return {}

    decl = clean_registry.as_gemini_tool().function_declarations[0]
    assert decl.parameters is not None, "parameters must be a typed Schema"
    assert decl.parameters_json_schema is None
    assert "on" in decl.parameters.properties
    assert decl.parameters.required == ["on"]


def test_hallucinated_argument_names_are_ignored_not_fatal(clean_registry):
    """Defence in depth for the same bug: an invented kwarg must not raise."""
    @clean_registry.tool("set_lights", "toggle")
    def set_lights(on: bool = True):
        return {"lights": on}

    out = run(clean_registry.dispatch("set_lights", {"lights_on": False}))
    assert out["ok"] is True  # unknown kwarg dropped, default used


def test_handlers_taking_kwargs_still_receive_everything(clean_registry):
    @clean_registry.tool("anything", "accepts all")
    def anything(**kw):
        return {"got": sorted(kw)}

    out = run(clean_registry.dispatch("anything", {"a": 1, "b": 2}))
    assert out["got"] == ["a", "b"]


def test_the_slide_budgets_fit_inside_the_tool_timeout():
    """`next_slide` spends its time in three places and they are configured
    separately, so nothing stops their sum from exceeding the ceiling that
    kills the call — every advance would come back as {"error": "timeout"}
    instead of a slide, midway through a tour, for no visible reason.

    Measured against the *paced* ceiling: next_slide is not a slow tool, it
    is a waiting one, holding the call open on purpose until the guest has
    heard the slide being left.
    """
    from app.config import Settings
    from app.tools.registry import SLOW_TOOL_TIMEOUT_S

    s = Settings()
    worst = s.slide_tool_budget_s + s.slide_pause_s + s.canva_arrival_timeout_s
    assert worst < SLOW_TOOL_TIMEOUT_S, (
        "next_slide can take %.1fs but paced tools are cut off at %.1fs — the "
        "tour would break with a timeout error partway through"
        % (worst, SLOW_TOOL_TIMEOUT_S)
    )
    assert SLOW_TOOL_TIMEOUT_S - worst >= 1.0, (
        "only %.1fs of headroom; one .env tweak away from breaking"
        % (SLOW_TOOL_TIMEOUT_S - worst)
    )


def test_next_slide_is_registered_as_a_paced_tool():
    """Drop this flag and next_slide silently falls back to the 15s ceiling
    meant for light switches; the budget no longer fits inside it and long
    slides start timing out. The failure would look like a broken tour, not
    like a missing keyword argument."""
    from app.tools import registry, slides  # noqa: F401

    entry = registry.get("next_slide")
    assert entry is not None and entry.paced, (
        "next_slide waits on the guest by design and needs the paced ceiling"
    )


def test_the_wait_budget_covers_a_typical_slide_in_the_real_deck():
    """The budget decides how far the picture may run ahead of the words, and
    it was set to 6s — not against anything, just to fit under a ceiling. The
    median script in the shipped deck takes about ten seconds to say, so more
    than half of every tour advanced before the guest had heard it.

    Measured against the deck actually on disk, so rewriting the scripts
    longer fails here rather than in front of a guest. Thai narration runs
    roughly 6-8 characters a second; the slower figure is used, since being
    wrong that way only means waiting a moment too long.
    """
    import json
    from pathlib import Path

    from app.config import Settings, settings as live

    raw = json.loads(
        (Path(live.slides_dir).expanduser() / "index.json").read_text(encoding="utf-8")
    )
    images = raw["images"] if isinstance(raw, dict) else raw
    spoken = sorted(
        len(s.get("script_th") or "") / 6.0
        for s in images
        if s.get("type") == "deck" and s.get("script_th")
    )
    assert spoken, "the deck has no scripts — nothing to pace against"

    median = spoken[len(spoken) // 2]
    budget = Settings().slide_tool_budget_s
    assert budget >= median, (
        "SLIDE_TOOL_BUDGET_S is %.1fs but half the deck's slides take longer "
        "than %.1fs to narrate — those advance before the guest has heard them"
        % (budget, median)
    )

def test_a_hanging_tool_cannot_silence_the_robot(clean_registry, monkeypatch):
    """`start_presentation` was seen never returning — it had started waiting
    on a browser window. Function calling is synchronous, so from that
    moment the model produced nothing at all: a robot standing mute in front
    of a guest, unrecoverable without restarting the server.

    A tool that overruns is a bug worth fixing. It must surface as a bad
    answer, never as a dead conversation.
    """
    import asyncio

    from app.tools import registry as reg

    monkeypatch.setattr(reg, "TOOL_TIMEOUT_S", 0.3)

    @clean_registry.tool("stuck", "never returns")
    async def stuck():
        await asyncio.sleep(60)

    async def body():
        started = asyncio.get_event_loop().time()
        out = await clean_registry.dispatch("stuck", {})
        elapsed = asyncio.get_event_loop().time() - started
        assert elapsed < 2.0, "held the conversation for %.1fs" % elapsed
        assert out["ok"] is False
        assert out["error"] == "timeout"
        # And the model is told to keep talking rather than stall.
        assert "ห้ามเงียบ" in out["instruction"]

    asyncio.run(body())


def test_a_blocking_ir_send_does_not_block_the_event_loop(smarthome, monkeypatch):
    """Broadlink auth is synchronous; Gemini must remain responsive while it runs."""
    import time

    from app.tools import broadlink_ir

    def blocking_send(*args, **kwargs):
        time.sleep(0.15)
        return "ok"

    monkeypatch.setattr(broadlink_ir, "send", blocking_send)

    async def body():
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            for _ in range(5):
                await asyncio.sleep(0.02)
                ticks += 1

        result, _ = await asyncio.gather(
            registry.dispatch("set_lights", {"on": False}), heartbeat()
        )
        assert result["hardware"] == "ok"
        assert ticks == 5

    asyncio.run(body())


def test_a_wedged_canva_window_does_not_stop_a_tour(monkeypatch):
    """The specific failure. The Canva window never came up, and the whole
    presentation died at slide one.

    The tour now *waits* for the cover before narrating (so it doesn't talk
    over a still-loading window), but that wait is bounded by
    `canva_arrival_timeout_s` — a wedged window burns that budget, then the
    tour starts anyway with ok=True. Here the budget is set small so the
    assertion is about the guarantee, not the clock: a stuck window gives up
    and the presentation still begins."""
    import asyncio

    from app.config import settings
    from app.tools import canva_display, load_tools, registry, slides
    from tests.approval_fixture import approved_slides

    load_tools()
    slides.reload_slides()
    monkeypatch.setattr(slides, "_slides", approved_slides(slides.load_slides()))
    from pathlib import Path

    registered = json.loads((Path(settings.slides_dir) / "canva_pages.json").read_text(
        encoding="utf-8"))["deck_url"]
    monkeypatch.setattr(settings, "canva_url", "https://canva.test/design/%s/view"
                        % canva_display._design_id(registered))
    monkeypatch.setattr(settings, "canva_arrival_timeout_s", 0.5)

    async def never_ready(**_kw):
        await asyncio.sleep(600)

    monkeypatch.setattr(canva_display, "_ensure_page", never_ready)

    async def body():
        started = asyncio.get_event_loop().time()
        out = await registry.dispatch("start_presentation", {"tour": "deck"})
        elapsed = asyncio.get_event_loop().time() - started
        assert elapsed < 2.0, "wedged window held the tour %.1fs" % elapsed
        assert elapsed >= 0.5, "should have waited out its arrival budget first"
        assert out["ok"] is True
        assert out["slide"]["id"], "the tour still starts"

    asyncio.run(body())


def test_long_running_tools_are_marked_non_blocking(clean_registry, monkeypatch):
    """A robot walking to a POI takes ~30s; the conversation must not freeze.

    Pinned to a 2.5 model on purpose. This test used to assert the flag was
    always set, which quietly stopped being true when the deployed model
    moved to 3.x — the flag isn't supported there and the model blocks. The
    test passing told you nothing about whether the robot would actually
    keep talking.
    """
    from google.genai import types

    from app.config import settings

    monkeypatch.setattr(settings, "gemini_model",
                        "gemini-2.5-flash-native-audio-preview-12-2025")

    @clean_registry.tool("go_to", "walk somewhere", long_running=True)
    def go_to():
        return {}

    decl = clean_registry.as_gemini_tool().function_declarations[0]
    assert decl.behavior == types.Behavior.NON_BLOCKING


def test_no_tools_yields_no_gemini_tool(clean_registry):
    assert clean_registry.as_gemini_tool() is None
    assert clean_registry.as_openai_tools() == []


# ==================== smarthome ====================


def test_lights_on_sends_ir_and_updates_state(smarthome):
    out = run(registry.dispatch("set_lights", {"on": False}))
    assert out["ok"] is True and out["lights"] is False
    assert out["hardware"] == "ok"
    assert ("light_off", 2) in smarthome.sent


def test_handlers_return_facts_not_thai_sentences(smarthome):
    """The model speaks the reply, so a hardcoded Thai string would break
    the moment a guest asks in Chinese. "instruction" is exempt alongside
    "note": it is a directive TO the model (the one channel commands
    actually reach it through), never a sentence spoken verbatim."""
    out = run(registry.dispatch("set_lights", {"on": True}))
    assert "reply" not in out
    assert all(not isinstance(v, str) or v in ("ok", "mock", "failed")
               for k, v in out.items() if k not in ("note", "instruction"))


def test_ac_temperature_is_clamped_and_flagged(smarthome):
    out = run(registry.dispatch("set_air_conditioner", {"temp": 5}))
    assert out["ac"]["temp"] == 16
    assert "clamped" in out["note"]


def test_ac_only_changes_what_was_asked_for(smarthome):
    smarthome.state["ac"].update({"temp": 25, "fan": "auto"})
    run(registry.dispatch("set_air_conditioner", {"fan": "2"}))
    assert smarthome.state["ac"]["fan"] == "2"
    assert smarthome.state["ac"]["temp"] == 25  # untouched


def test_ac_with_no_arguments_is_a_no_op(smarthome):
    out = run(registry.dispatch("set_air_conditioner", {}))
    assert out["ok"] is False


def test_hardware_failure_is_surfaced_not_hidden(smarthome):
    """Claiming success while the room stays dark is the worst outcome."""
    smarthome.send.result = "failed"
    out = run(registry.dispatch("set_lights", {"on": True}))
    assert out["hardware"] == "failed"


def test_partial_hardware_failure_reports_failed(smarthome, monkeypatch):
    """One command through and one dropped must not be reported as success."""
    from app.tools import broadlink_ir

    calls = {"n": 0}

    def flaky(code, repeat=1, delay=0.6):
        calls["n"] += 1
        return "ok" if calls["n"] == 1 else "failed"

    monkeypatch.setattr(broadlink_ir, "send", flaky)

    # Start from off so turning on *and* setting a temperature both send.
    smarthome.state["ac"]["on"] = False
    out = run(registry.dispatch("set_air_conditioner", {"on": True, "temp": 22}))

    assert calls["n"] == 2, "both the power and temperature codes should be sent"
    assert out["hardware"] == "failed"


def test_temperature_is_not_sent_to_an_ac_being_turned_off(smarthome):
    """No point commanding a unit that's being switched off in the same call."""
    smarthome.sent.clear()
    run(registry.dispatch("set_air_conditioner", {"on": False, "temp": 22}))
    assert not any(code.startswith("ac_temp") for code, _ in smarthome.sent)
    assert smarthome.state["ac"]["temp"] == 22  # remembered for next power-on


def test_room_status_says_it_is_not_a_sensor_reading(smarthome):
    """IR is one-way; the value is what we sent, which can be stale."""
    out = run(registry.dispatch("get_room_status", {}))
    assert out["source"] == "last_command"
    assert "lights" in out and "ac" in out


def test_ir_runs_in_mock_mode_without_a_hub(monkeypatch):
    from app.tools import broadlink_ir

    monkeypatch.setattr(settings, "ir_enabled", True)
    monkeypatch.setattr(broadlink_ir, "available", lambda: False)
    assert broadlink_ir.send("light_on") == "mock"


def test_available_requires_the_broadlink_package(monkeypatch):
    """Config files alone aren't enough — without the package nothing sends."""
    from app.tools import broadlink_ir

    monkeypatch.setattr(broadlink_ir, "package_installed", lambda: False)
    assert broadlink_ir.available() is False


def test_mock_is_never_reported_as_success(smarthome, monkeypatch):
    """The bug this guards: with `broadlink` missing, send() returned "mock"
    but the result was upgraded to "ok" because the config files existed, so
    the assistant announced it had switched the AC off while nothing was
    sent at all."""
    from app.tools import broadlink_ir

    monkeypatch.setattr(broadlink_ir, "send", lambda *a, **k: "mock")
    monkeypatch.setattr(broadlink_ir, "available", lambda: True)  # files present

    out = run(registry.dispatch("set_air_conditioner", {"on": False}))
    assert out["hardware"] == "mock", "mock must never be upgraded to ok"

    out = run(registry.dispatch("set_lights", {"on": False}))
    assert out["hardware"] == "mock"


def test_ir_status_explains_why_it_is_unusable(monkeypatch):
    from app.tools import broadlink_ir

    monkeypatch.setattr(broadlink_ir, "package_installed", lambda: False)
    st = broadlink_ir.status()
    assert st["package_installed"] is False and st["usable"] is False


def test_ir_disabled_is_mock(monkeypatch):
    from app.tools import broadlink_ir

    monkeypatch.setattr(settings, "ir_enabled", False)
    assert broadlink_ir.send("light_on") == "mock"


# ==================== wiring into the providers ====================


def test_gemini_config_declares_the_tools(smarthome):
    from app.providers.gemini import GeminiProvider

    cfg = GeminiProvider("Kore", "x")._build_config()
    names = [d.name for d in cfg["tools"][0].function_declarations]
    assert "set_lights" in names and "set_air_conditioner" in names


def test_openai_session_declares_the_tools(smarthome):
    from app.providers.openai_realtime import build_session_config

    session = build_session_config("marin", "x")["session"]
    names = [t["name"] for t in session["tools"]]
    assert "set_lights" in names
    assert session["tool_choice"] == "auto"


def test_tools_can_be_disabled(monkeypatch):
    monkeypatch.setattr(settings, "tools_enabled", False)
    assert settings.enabled_tool_groups() == set()


def test_tool_groups_can_be_restricted(monkeypatch):
    monkeypatch.setattr(settings, "tools_enabled", True)
    monkeypatch.setattr(settings, "tool_groups", "smarthome")
    assert settings.enabled_tool_groups() == {"smarthome"}


def test_prompt_tells_the_model_not_to_claim_success_on_failure():
    from app.prompts import build_instructions

    text = build_instructions("X")
    assert "failed" in text and "ห้ามบอกว่าสำเร็จ" in text


def test_a_blocking_ac_send_does_not_block_the_event_loop(smarthome, monkeypatch):
    """The set_lights fix, finally applied to the worse case: one AC request
    is up to FOUR sends, and with the hub unreachable each walks the full
    retry-then-discovery path. Seen live 2026-08-22: "สั่งปิดแอร์" with no
    hub on the network froze the whole voice session."""
    import time

    from app.tools import broadlink_ir

    def slow_send(*args, **kwargs):
        time.sleep(0.1)
        return "ok"

    monkeypatch.setattr(broadlink_ir, "send", slow_send)

    async def body():
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            for _ in range(5):
                await asyncio.sleep(0.02)
                ticks += 1

        result, _ = await asyncio.gather(
            registry.dispatch("set_air_conditioner", {"on": False}), heartbeat()
        )
        assert result["hardware"] == "ok"
        assert ticks == 5, "the loop starved while the AC command ran"

    asyncio.run(body())


def test_an_unreachable_hub_makes_the_ac_result_honest(smarthome, monkeypatch):
    from app.tools import broadlink_ir

    monkeypatch.setattr(broadlink_ir, "send", lambda *a, **k: "failed")

    async def body():
        out = await registry.dispatch("set_air_conditioner", {"on": False})
        assert out["ok"] is False
        assert "ห้ามยืนยันว่าทำสำเร็จ" in out["instruction"]

    asyncio.run(body())


def test_a_successful_send_still_admits_it_cannot_see_the_room(smarthome):
    """Live, 2026-08-26 15:55: the hub took every frame ("ok" was truthful),
    the lamp never reacted, the owner said "แปลว่าเปิดไฟไม่ได้" — and the
    model argued back "เปิดได้ปกติเลยค่ะ". IR is one-way; ok means the hub
    accepted the payload, not that the room changed. The note rides on every
    ok result because a rule the model must remember across turns is a rule
    it loses (the UNITS_SAMPLE lesson), and it must not displace the failure
    instruction, which says something different."""
    from app.tools.smarthome import IR_ONE_WAY_NOTE, set_air_conditioner, set_lights

    out = run(set_lights(True))
    assert out["hardware"] == "ok"
    assert out["instruction"] == IR_ONE_WAY_NOTE
    assert "ทางเดียว" in IR_ONE_WAY_NOTE and "ห้ามเถียง" in IR_ONE_WAY_NOTE

    out = run(set_air_conditioner(temp=22))
    assert out["hardware"] == "ok"
    assert out["instruction"] == IR_ONE_WAY_NOTE

    smarthome.send.result = "failed"
    out = run(set_lights(False))
    assert "ห้ามยืนยันว่าทำสำเร็จ" in out["instruction"], \
        "the failure instruction must survive the ok-note addition"

    smarthome.send.result = "mock"
    out = run(set_lights(True))
    assert out.get("instruction") != IR_ONE_WAY_NOTE, \
        "mock is not ok — nothing was sent at all"
