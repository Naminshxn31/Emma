"""Who can see the body/nav tools, and proof that nobody else noticed.

P0 of docs/emma-body-nav-design-2026-10-08.md. The sales room's rule is that a
`git pull` changes nothing (CLAUDE.md: "default ต้องเป็น condo เสมอ"), so the
first thing here is a snapshot of the surface the model sees - tool names,
descriptions, parameters and the prompt - taken from the tree *before* P0
touched it, and compared byte for byte with the tree after.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import pytest

SNAPSHOT = Path(__file__).parent / "data" / "body_nav_p0_snapshot.json"

PROFILES = ("condo", "joke", "emma", "translator")
_CLOCK = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?: \(วัน[^)]*\))?")


#: The machine-dependent inputs of the prompts, pinned to the values the
#: snapshot was taken with (review #39). Read from `settings`, the test was
#: green only on the owner's .env: FACE_SCAN_ENABLED=false (the code default,
#: the sales-room machine) or any other ROBOT_NAME/PROJECT_NAME/REPLY_LANGUAGES
#: turned it red — "ชุดเทสต์ต้องไม่เปลี่ยนสีตาม .env ของเครื่อง" (CLAUDE.md).
#: These are the snapshot's values, not the code defaults, so the stored
#: hashes stay valid without regenerating them.
SNAPSHOT_SETTINGS = {"project_name": "Embassy World", "robot_name": "Emma",
                     "reply_languages": "auto", "face_scan_enabled": True}


def _default_surface(monkeypatch) -> dict:
    """What every profile's model is handed, with the suite's pinned defaults.

    Tool declarations are kept whole (they are code). Prompts are kept as
    sha256: they carry the approved facts, and the point is "unchanged", not
    "readable in a diff".
    """
    import app.tools.registry as reg
    from app import memory_store, prompts, tools
    from app.config import settings
    from app.prompts import build_instructions
    from app.providers.gemini import adaptation_phrases

    for key, value in SNAPSHOT_SETTINGS.items():
        monkeypatch.setattr(settings, key, value)
    # The owner's gitignored documents and private memory (emma/joke inputs);
    # constants here so no machine's files reach the hash.
    monkeypatch.setattr(prompts, "_personal_docs_line", lambda: "[DOCS]")
    monkeypatch.setattr(memory_store, "prompt_block", lambda: "[MEMORY]")
    # The approved project data too (2026-10-12): an edit in the data console
    # withdraws approval, the prompt falls back, and this snapshot went red
    # over data, not code — then would have gone red again on re-approval.
    monkeypatch.setattr(prompts, "load_facts", lambda *a, **k: "## ข้อมูลโครงการ" + chr(10) + "- [FACTS]" + chr(10))
    monkeypatch.setattr(prompts, "load_sales_context",
                        lambda: {"story_rule": "- [STORY]", "scope_rule": "- [SCOPE]"})
    from app.providers import gemini as _gemini
    monkeypatch.setattr(_gemini, "facility_names", lambda: ["[FACILITY]"])

    for module in list(tools._TOOL_MODULES):
        __import__(module)
    out: dict = {"tools": {}, "prompts": {}, "phrases": None}
    for profile in PROFILES:
        for face in (False, True):
            p_token = reg.session_profile.set(profile)
            f_token = reg.robot_face_session.set(object() if face else None)
            try:
                out["tools"]["%s/face=%s" % (profile, face)] = [
                    {"name": t.name, "description": t.description,
                     "parameters": t.parameters, "long_running": t.long_running}
                    for t in reg.all_tools()]
            finally:
                reg.robot_face_session.reset(f_token)
                reg.session_profile.reset(p_token)
    # The emma prompt lists the owner's gitignored personal-docs folder and
    # the translator prompt is a constant, so the machine-independent pair is
    # the sales room and the public showroom face.
    for profile in ("condo", "joke"):
        for face in (False, True):
            text = build_instructions(SNAPSHOT_SETTINGS["project_name"],
                                      languages=SNAPSHOT_SETTINGS["reply_languages"],
                                      robot_name=SNAPSHOT_SETTINGS["robot_name"],
                                      profile=profile, robot_face=face)
            # The joke prompt carries the minute and weekday it was built in;
            # that is not the surface under test (measured: with the stamp set
            # back to the snapshot's own, 2026-10-08 23:35 วันพฤหัส, the
            # post-P0 hash equals the pre-P0 one — and the test went red the
            # first time the clock crossed midnight, so the weekday is out too).
            text = _CLOCK.sub("<clock>", text)
            out["prompts"]["%s/face=%s" % (profile, face)] = hashlib.sha256(
                text.encode("utf-8")).hexdigest()
    out["phrases"] = hashlib.sha256(
        json.dumps(adaptation_phrases(), ensure_ascii=False).encode("utf-8")).hexdigest()
    return out


@pytest.fixture
def _registry_restored():
    """Leave the registry *and* the import cache as they were.

    Restoring only the registry is not enough: a tool module first imported
    inside the test stays in sys.modules with its tools gone, so every later
    `import` is a no-op and those tools are simply missing for the rest of
    the run (measured here: request_handshake vanished for the next test).
    Forgetting the newly imported tool modules makes the next import
    register them again, the way a fresh process would.
    """
    import sys

    import app.tools as tools_pkg
    import app.tools.registry as reg

    before = dict(reg._REGISTRY)
    modules_before = set(sys.modules)
    yield
    reg._REGISTRY.clear()
    reg._REGISTRY.update(before)
    for name in list(tools_pkg._TOOL_MODULES):
        if name in sys.modules and name not in modules_before:
            del sys.modules[name]
            attr = name.rsplit(".", 1)[1]
            if getattr(tools_pkg, attr, None) is not None:
                delattr(tools_pkg, attr)


def _by_name(tools: list[dict]) -> list[dict]:
    return sorted(tools, key=lambda t: t["name"])


def test_defaults_are_byte_identical_to_the_pre_p0_snapshot(_registry_restored, monkeypatch):
    surface = _default_surface(monkeypatch)
    if os.environ.get("P0_WRITE_SNAPSHOT") == "1":
        SNAPSHOT.write_text(json.dumps(surface, ensure_ascii=False, indent=1,
                                       sort_keys=True), encoding="utf-8")
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    # Compared without order (review #38): `reg.all_tools()` lists tools in
    # registry insertion order, which is the order *earlier tests* happened
    # to import the tool modules in — `pytest tests/test_calc.py
    # tests/test_body_nav_gating.py` put calc_roi first and went red on
    # nothing but order. Production order comes from app/tools/__init__.py,
    # and the model is handed a set of declarations, so the set is the
    # surface under test; every field of every tool is still compared.
    assert ({k: _by_name(v) for k, v in surface["tools"].items()}
            == {k: _by_name(v) for k, v in expected["tools"].items()})
    assert surface["prompts"] == expected["prompts"]
    assert surface["phrases"] == expected["phrases"]


# ---- visibility of the body group (registry.allowed, design §2d / §5 P0) ----


@pytest.fixture
def fake_move_body(_registry_restored):
    """A stand-in for P1's move_body, registered under the `body` group the
    way app/tools/body.py will be (the group comes from the module name)."""
    import app.tools.robot  # noqa: F401  (registers request_handshake)
    from app.tools import registry as reg

    async def move_body(action: str) -> dict:
        return {"ok": True, "action": action}

    move_body.__module__ = "app.tools.body"
    reg._REGISTRY.pop("move_body", None)
    reg.tool(name="move_body", description="test", exclusive=True,
             preempt=lambda args: args.get("action") == "stop",
             parameters={"type": "object", "properties": {"action": {"type": "string"}}})(
        move_body)
    assert reg.get("move_body").group == "body"
    return reg.get("move_body")


def _visible(name, *, face, switch, groups, profile, multi):
    from app.config import settings
    from app.tools import registry as reg

    settings.robot_body_enabled = switch
    settings.tool_groups = groups
    settings.multi_session = multi
    p_token = reg.session_profile.set(profile)
    f_token = reg.robot_face_session.set(object() if face else None)
    try:
        entry = reg.get(name)
        return entry is not None and reg.allowed(entry)
    finally:
        reg.robot_face_session.reset(f_token)
        reg.session_profile.reset(p_token)


@pytest.mark.parametrize("multi", [False, True])
@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("groups", ["", "robot", "body", "robot,body"])
@pytest.mark.parametrize("switch", [False, True])
@pytest.mark.parametrize("face", [False, True])
def test_visibility_matrix(fake_move_body, face, switch, groups, profile, multi):
    """move_body is offered only on the robot face's own session, with the
    server key on, the group named in TOOL_GROUPS (opt-in: blank never
    includes it), and never under MULTI_SESSION (not shareable: it moves
    the one robot). The profile does not open or close it: every profile
    given the group gets it (design section 7, decision 2)."""
    expected = face and switch and "body" in groups.split(",") and not multi
    assert _visible("move_body", face=face, switch=switch, groups=groups,
                    profile=profile, multi=multi) is expected


def test_body_is_opt_in_and_not_shareable():
    from app import tools
    from app.config import MULTI_SESSION_SAFE

    assert "body" in tools._OPT_IN
    assert "body" not in MULTI_SESSION_SAFE
    assert "app.tools.body" not in tools._modules_to_load(None)


def test_handshake_is_hidden_only_while_move_body_is_offered(fake_move_body):
    kw = dict(profile="joke", multi=False)
    # The face, the switch and the group: move_body is the one handshake path.
    assert _visible("move_body", face=True, switch=True, groups="robot,body", **kw)
    assert not _visible("request_handshake", face=True, switch=True, groups="robot,body", **kw)
    # Any key missing: the old fixed handshake tool is back, exactly as before.
    assert _visible("request_handshake", face=True, switch=False, groups="robot,body", **kw)
    assert _visible("request_handshake", face=True, switch=True, groups="robot", **kw)
    assert _visible("request_handshake", face=True, switch=True, groups="", **kw)
    # Never on a desk tab, whatever else is on.
    assert not _visible("request_handshake", face=False, switch=True, groups="robot,body", **kw)


# ---- registry preempt (safety review #1) ----


@pytest.fixture
def slow_exclusive(_registry_restored):
    """An exclusive tool whose calls hang until released (stop only when
    `slow_stop` is set)."""
    from app.tools import registry as reg

    gate = {"event": None, "calls": [], "slow_stop": False}

    async def gesture(action: str) -> dict:
        gate["calls"].append(action)
        if action != "stop" or gate["slow_stop"]:
            await gate["event"].wait()
        return {"ok": True, "action": action}

    gesture.__module__ = "tests.fake_gesture"
    reg._REGISTRY.pop("gesture", None)
    reg.tool(name="gesture", description="test", exclusive=True,
             preempt=lambda args: args.get("action") == "stop")(gesture)
    return gate


def test_preempt_lets_a_stop_through_an_inflight_exclusive_call(slow_exclusive):
    import asyncio

    from app.tools import registry as reg

    async def scenario():
        slow_exclusive["event"] = asyncio.Event()
        nod = asyncio.create_task(reg.dispatch("gesture", {"action": "nod"}))
        await asyncio.sleep(0.02)
        second = await reg.dispatch("gesture", {"action": "wave"})
        loop = asyncio.get_running_loop()
        started = loop.time()
        stop = await reg.dispatch("gesture", {"action": "stop"})
        took = loop.time() - started
        slow_exclusive["event"].set()
        return second, stop, took, await nod

    second, stop, took, nod = asyncio.run(scenario())
    assert second["error"] == "busy"                 # the exclusivity still holds
    assert stop == {"ok": True, "action": "stop"} and took < 0.1
    assert nod["ok"] is True


def test_a_preempting_call_blocks_nothing(slow_exclusive):
    import asyncio

    from app.tools import registry as reg

    async def scenario():
        slow_exclusive["event"] = asyncio.Event()
        slow_exclusive["slow_stop"] = True
        stop = asyncio.create_task(reg.dispatch("gesture", {"action": "stop"}))
        await asyncio.sleep(0.02)
        recorded_for_stop = "gesture" in reg._inflight
        later = asyncio.create_task(reg.dispatch("gesture", {"action": "nod"}))
        await asyncio.sleep(0.02)
        recorded_for_nod = "gesture" in reg._inflight
        slow_exclusive["event"].set()
        return recorded_for_stop, recorded_for_nod, await stop, await later

    recorded_for_stop, recorded_for_nod, stop, later = asyncio.run(scenario())
    assert recorded_for_stop is False and recorded_for_nod is True
    assert stop["ok"] and later["ok"]
    assert slow_exclusive["calls"] == ["stop", "nod"]


def test_preempt_is_not_part_of_any_declaration(fake_move_body):
    from app.tools import registry as reg

    settings_token = reg.robot_face_session.set(object())
    try:
        from app.config import settings

        settings.robot_body_enabled = True
        settings.tool_groups = "body"
        declared = reg.as_openai_tools()
    finally:
        reg.robot_face_session.reset(settings_token)
    assert [d["name"] for d in declared] == ["move_body"]
    assert set(declared[0]) == {"type", "name", "description", "parameters"}


# ---- config: the boot rule for the nav panel (design §2h) ----


def test_panel_without_routes_is_forced_off_and_said_out_loud(caplog):
    import logging

    from app.config import Settings

    with caplog.at_level(logging.WARNING, logger="condo_voice.config"):
        cfg = Settings(robot_nav_panel="pin", robot_nav_routes_enabled=False)
    assert cfg.robot_nav_panel == "off"
    assert cfg.nav_panel_mode() == "off"
    assert any("FORCED OFF" in line for line in cfg.nav_boot_warnings)
    assert any("FORCED OFF" in r.getMessage() for r in caplog.records)


def test_panel_with_routes_stays():
    from app.config import Settings

    cfg = Settings(robot_nav_panel="open", robot_nav_routes_enabled=True)
    assert cfg.robot_nav_panel == "open" and cfg.nav_panel_mode() == "open"
    assert cfg.nav_boot_warnings == []


def test_typos_read_as_off():
    from app.config import Settings

    cfg = Settings(robot_nav_panel="pni", robot_nav_voice="yes", robot_nav_routes_enabled=True)
    assert cfg.robot_nav_panel == "off" and cfg.robot_nav_voice == "off"
    assert len(cfg.nav_boot_warnings) == 2


def test_the_rule_holds_at_runtime_too(monkeypatch):
    """A value set after boot (a test, a console) skips __post_init__; every
    reader goes through nav_panel_mode()."""
    from app.config import settings

    monkeypatch.setattr(settings, "robot_nav_panel", "pin")
    monkeypatch.setattr(settings, "robot_nav_routes_enabled", False)
    assert settings.nav_panel_mode() == "off"
    monkeypatch.setattr(settings, "robot_nav_routes_enabled", True)
    assert settings.nav_panel_mode() == "pin"
    monkeypatch.setattr(settings, "robot_nav_voice", "always")
    assert settings.nav_voice_mode() == "off"


def test_code_defaults_are_off():
    """The code defaults themselves, not the conftest pins: a fresh process
    with every ROBOT_*/TURN_IDLE/ANNOUNCE_ variable removed and .env not
    loaded."""
    import subprocess
    import sys

    code = (
        "import os, json\n"
        "for k in list(os.environ):\n"
        "    if k.startswith(('ROBOT_', 'TURN_IDLE', 'ANNOUNCE_')): del os.environ[k]\n"
        "import dotenv\n"
        "dotenv.load_dotenv = lambda *a, **k: False\n"
        "from app.config import settings as s\n"
        "print(json.dumps([s.robot_body_enabled, s.robot_body_ack_timeout_s,\n"
        "  s.robot_body_require_heard, s.robot_nav_routes_enabled, s.robot_nav_ack_timeout_s,\n"
        "  s.robot_nav_places_file, s.robot_nav_voice_unverified, s.robot_nav_require_heard,\n"
        "  s.robot_nav_voice, s.robot_heard_max_age_s, s.robot_nav_panel, s.robot_nav_pin,\n"
        "  s.robot_nav_status_stale_s, s.robot_face_token, s.turn_idle_tracks_tools,\n"
        "  s.announce_settle_s]))\n"
    )
    root = Path(__file__).resolve().parent.parent
    out = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True,
                         text=True, encoding="utf-8", timeout=120, check=True)
    assert json.loads(out.stdout.strip().splitlines()[-1]) == [
        False, 2.5, True, False, 3.0, "data/robot-places/floor2.json", False, True,
        "off", 10.0, "off", "", 20.0, "", False, 0.0]
