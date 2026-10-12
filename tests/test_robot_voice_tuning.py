"""P6 of docs/emma-body-nav-design-2026-10-08.md: what the voice model is told
about the robot's body and navigation, what the recogniser is biased toward,
and the robot suite of the live eval harness.

The rule over all of it is the sales room's: a `git pull` changes nothing
(CLAUDE.md "default ต้องเป็น condo เสมอ"). So the first test is a snapshot of
every profile's prompt and phrase list taken from the tree *before* P6 touched
prompts.py or adaptation_phrases, compared with the switches off.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = Path(__file__).parent / "data" / "body_nav_p6_snapshot.json"
PROFILES = ("condo", "joke", "emma", "translator")
_CLOCK = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?: \(วัน[^)]*\))?")


def _build(profile: str, face: bool) -> str:
    from app.prompts import build_instructions

    # Explicit arguments, not the machine's .env: the snapshot is about the
    # code, and robot_name/project_name are whatever this machine says.
    return build_instructions("Embassy World", languages="auto", robot_name="Emma",
                              profile=profile, robot_face=face)


def _surface(monkeypatch) -> dict:
    """Every profile's prompt (face on and off) and phrase list, as hashes.

    The emma prompt lists the owner's gitignored documents and his private
    memory, so both are replaced by constants: the surface under test is
    the code around them. The joke/emma prompts carry the minute they were
    built in; that is replaced too (P0 measured the midnight flake).
    """
    import app.tools.registry as reg
    from app import memory_store, prompts
    from app.config import settings
    from app.providers import gemini

    monkeypatch.setattr(prompts, "_personal_docs_line", lambda: "[DOCS]")
    monkeypatch.setattr(memory_store, "prompt_block", lambda: "[MEMORY]")
    # Approved project data is data, not code (see test_body_nav_gating).
    monkeypatch.setattr(prompts, "load_facts", lambda *a, **k: "## ข้อมูลโครงการ" + chr(10) + "- [FACTS]" + chr(10))
    monkeypatch.setattr(prompts, "load_sales_context",
                        lambda: {"story_rule": "- [STORY]", "scope_rule": "- [SCOPE]"})
    from app.providers import gemini as _gemini
    monkeypatch.setattr(_gemini, "facility_names", lambda: ["[FACILITY]"])
    monkeypatch.setattr(settings, "face_scan_enabled", False)
    monkeypatch.setattr(settings, "project_name", "Embassy World")
    monkeypatch.setattr(settings, "robot_name", "Emma")
    out: dict = {"prompts": {}, "phrases": {}}
    for profile in PROFILES:
        token = reg.session_profile.set(profile)
        try:
            for face in (False, True):
                f_token = reg.robot_face_session.set(object() if face else None)
                try:
                    text = _CLOCK.sub("<clock>", _build(profile, face))
                finally:
                    reg.robot_face_session.reset(f_token)
                out["prompts"]["%s/face=%s" % (profile, face)] = hashlib.sha256(
                    text.encode("utf-8")).hexdigest()
            out["phrases"][profile] = hashlib.sha256(json.dumps(
                gemini.adaptation_phrases(), ensure_ascii=False).encode("utf-8")).hexdigest()
        finally:
            reg.session_profile.reset(token)
    return out


@pytest.fixture
def registry_restored():
    """Leave the registry and the import cache as they were (P0's lesson: a
    tool module first imported inside a test stays in sys.modules with its
    tools gone, and the next test silently lacks them)."""
    import app.tools as tools_pkg
    import app.tools.registry as reg

    before = dict(reg._REGISTRY)
    modules_before = set(sys.modules)
    yield reg
    reg._REGISTRY.clear()
    reg._REGISTRY.update(before)
    for name in list(tools_pkg._TOOL_MODULES):
        if name in sys.modules and name not in modules_before:
            del sys.modules[name]
            attr = name.rsplit(".", 1)[1]
            if getattr(tools_pkg, attr, None) is not None:
                delattr(tools_pkg, attr)


def test_switches_off_every_prompt_and_phrase_list_is_byte_identical(
        monkeypatch, registry_restored):
    import app.tools.robot  # noqa: F401  (request_handshake, go_to_place exist as in the room)

    surface = _surface(monkeypatch)
    if os.environ.get("P6_WRITE_SNAPSHOT") == "1":
        SNAPSHOT.write_text(json.dumps(surface, ensure_ascii=False, indent=1,
                                       sort_keys=True), encoding="utf-8")
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert surface["prompts"] == expected["prompts"]
    assert surface["phrases"] == expected["phrases"]


# ---- 1/2. the robot-face block, rule 8 and the scope line (prompts.py) ----

ROBOT_TOOLS = ("move_body", "request_handshake", "go_to_place", "return_to_base",
               "stop_moving", "get_robot_status")


@pytest.fixture
def fake_move_body(registry_restored):
    """A stand-in for P1's move_body, registered under the `body` group the
    way app/tools/body.py will be (P0's fixture, same reason: P1 lands in
    parallel). The robot tools are imported so visibility is the real rule."""
    import app.tools.robot  # noqa: F401
    reg = registry_restored

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


def _prompt(monkeypatch, *, profile="joke", face=True, body=False, routes=False,
            groups="", voice="off") -> str:
    import app.tools.registry as reg
    from app import memory_store, prompts
    from app.config import settings

    monkeypatch.setattr(prompts, "_personal_docs_line", lambda: "[DOCS]")
    monkeypatch.setattr(memory_store, "prompt_block", lambda: "[MEMORY]")
    # Approved project data is data, not code (see test_body_nav_gating).
    monkeypatch.setattr(prompts, "load_facts", lambda *a, **k: "## ข้อมูลโครงการ" + chr(10) + "- [FACTS]" + chr(10))
    monkeypatch.setattr(prompts, "load_sales_context",
                        lambda: {"story_rule": "- [STORY]", "scope_rule": "- [SCOPE]"})
    from app.providers import gemini as _gemini
    monkeypatch.setattr(_gemini, "facility_names", lambda: ["[FACILITY]"])
    monkeypatch.setattr(settings, "robot_body_enabled", body)
    monkeypatch.setattr(settings, "robot_nav_routes_enabled", routes)
    monkeypatch.setattr(settings, "robot_nav_voice", voice)
    monkeypatch.setattr(settings, "tool_groups", groups)
    monkeypatch.setattr(settings, "assistant_profile", profile)
    p_token = reg.session_profile.set(profile)
    f_token = reg.robot_face_session.set(object() if face else None)
    try:
        return _build(profile, face)
    finally:
        reg.robot_face_session.reset(f_token)
        reg.session_profile.reset(p_token)


def _visible_names(monkeypatch, *, profile, face, body, routes, groups) -> set[str]:
    import app.tools.registry as reg
    from app.config import settings

    monkeypatch.setattr(settings, "robot_body_enabled", body)
    monkeypatch.setattr(settings, "robot_nav_routes_enabled", routes)
    monkeypatch.setattr(settings, "tool_groups", groups)
    monkeypatch.setattr(settings, "assistant_profile", profile)
    p_token = reg.session_profile.set(profile)
    f_token = reg.robot_face_session.set(object() if face else None)
    try:
        return {name for name in ROBOT_TOOLS
                if reg.get(name) is not None and reg.allowed(reg.get(name))}
    finally:
        reg.robot_face_session.reset(f_token)
        reg.session_profile.reset(p_token)


def test_body_line_replaces_the_handshake_line_only_while_move_body_is_offered(
        monkeypatch, fake_move_body):
    from app.prompts import BODY_FACE_LINE

    on = _prompt(monkeypatch, body=True, groups="robot,body")
    assert BODY_FACE_LINE in on
    # Review #20: the gesture list is not read from move_body's result (the
    # only way to get it is to move); a "what can you do" gets the broad
    # answer and no list.
    assert "ห้ามไล่รายชื่อท่า" in on
    # One handshake path: request_handshake is hidden, so it is not named.
    assert "request_handshake" not in on
    # The verified fact about the arm stays — it does not contradict.
    assert "ท่าจับมือกลุ่ม 6" in on

    for kwargs in ({"body": False, "groups": "robot,body"},     # switch off
                   {"body": True, "groups": "robot"},          # group not loaded
                   {"body": True, "groups": "robot,body", "face": False}):
        text = _prompt(monkeypatch, **kwargs)
        assert BODY_FACE_LINE not in text and "move_body" not in text, kwargs


def test_nav_line_needs_routes_and_the_robot_tools(monkeypatch, fake_move_body):
    from app.prompts import NAV_FACE_LINE, NAV_VOICE_OFF_CLAUSE

    on = _prompt(monkeypatch, routes=True, groups="robot")
    assert NAV_FACE_LINE in on
    # Default voice key: say who starts walks, never promise to go.
    assert NAV_VOICE_OFF_CLAUSE in on
    assert NAV_VOICE_OFF_CLAUSE not in _prompt(monkeypatch, routes=True, groups="robot",
                                               voice="on")
    for kwargs in ({"routes": False, "groups": "robot"},
                   {"routes": True, "groups": "body"},       # robot tools not loaded
                   {"routes": True, "groups": "robot", "face": False}):
        assert NAV_FACE_LINE not in _prompt(monkeypatch, **kwargs), kwargs


@pytest.mark.parametrize("profile", ["condo", "joke", "emma"])
@pytest.mark.parametrize("groups", ["", "robot", "body", "robot,body", "smarthome,robot,body"])
@pytest.mark.parametrize("body", [False, True])
@pytest.mark.parametrize("routes", [False, True])
@pytest.mark.parametrize("face", [False, True])
def test_no_prompt_names_an_invisible_robot_tool(monkeypatch, fake_move_body,
                                                 profile, groups, body, routes, face):
    text = _prompt(monkeypatch, profile=profile, face=face, body=body, routes=routes,
                   groups=groups)
    named = {name for name in ROBOT_TOOLS if re.search(r"\b%s\b" % name, text)}
    visible = _visible_names(monkeypatch, profile=profile, face=face, body=body,
                             routes=routes, groups=groups)
    assert named <= visible, (named - visible)


def test_emma_rule_8_says_walking_only_on_the_face_with_routes(monkeypatch, fake_move_body):
    walking = "เดินได้เฉพาะไปจุดหมายที่ get_robot_status บอก ("
    on = _prompt(monkeypatch, profile="emma", routes=True, groups="smarthome,robot")
    assert walking in on and " เดินไม่ได้ (" not in on
    for kwargs in ({"routes": False}, {"face": False}):
        off = _prompt(monkeypatch, profile="emma", groups="smarthome,robot",
                      **{"routes": True, **kwargs})
        assert " เดินไม่ได้ (" in off and walking not in off, kwargs


def test_condo_scope_line_names_walking_only_with_routes(monkeypatch, fake_move_body):
    on = _prompt(monkeypatch, profile="condo", routes=True)
    assert "ตัวหุ่น Emma กล้อง การจับมือ และการพาเดินไปจุดหมายของหุ่นได้ ห้ามตอบเรื่องอื่น" in on
    off = _prompt(monkeypatch, profile="condo", routes=False)
    assert "ตัวหุ่น Emma กล้อง และการจับมือได้ ห้ามตอบเรื่องอื่น" in off
    assert "การพาเดิน" not in off


def test_the_body_line_never_sends_the_model_to_move_for_the_list(monkeypatch, fake_move_body):
    """Review #20: "ท่าที่ทำได้ดูจากผลเครื่องมือ move_body (ช่อง available)"
    next to "a question is not a command" left "ทำท่าอะไรได้บ้าง" two wrong
    answers. No line may point at a field only a motion returns, and none
    may point at get_robot_status for it while that tool does not carry it."""
    from app.prompts import BODY_FACE_LINE

    for profile in ("condo", "joke", "emma"):
        text = _prompt(monkeypatch, profile=profile, body=True, groups="smarthome,robot,body")
        assert BODY_FACE_LINE in text, profile
        assert "move_body (ช่อง available)" not in text, profile
        assert "body.available" not in text, profile
    assert "ห้ามขยับเพื่อโชว์" in BODY_FACE_LINE


def test_condo_scope_and_closing_line_name_gestures_only_with_move_body(
        monkeypatch, fake_move_body):
    """Review #37: on a condo face with move_body offered, the iron scope
    rule ("ห้ามตอบเรื่องอื่นทุกกรณี") named only camera and handshake, so
    "ก้มหน้าหน่อย" could be filed as off-topic."""
    on = _prompt(monkeypatch, profile="condo", body=True, groups="robot,body")
    assert "ตัวหุ่น Emma กล้อง การจับมือ และการสั่งท่าหัวและแขนได้ ห้ามตอบเรื่องอื่น" in on
    assert "ตอบคำถามเรื่องกล้อง จับมือ และท่าทางได้ตรงๆ" in on
    both = _prompt(monkeypatch, profile="condo", body=True, routes=True, groups="robot,body")
    assert ("ตัวหุ่น Emma กล้อง การจับมือ การสั่งท่าหัวและแขน "
            "และการพาเดินไปจุดหมายของหุ่นได้ ห้ามตอบเรื่องอื่น") in both
    # Switch off (or group not loaded): the old words, byte for byte.
    for kwargs in ({"body": False, "groups": "robot,body"}, {"body": True, "groups": "robot"}):
        off = _prompt(monkeypatch, profile="condo", **kwargs)
        assert "ตัวหุ่น Emma กล้อง และการจับมือได้ ห้ามตอบเรื่องอื่น" in off, kwargs
        assert "ตอบคำถามเรื่องกล้องและจับมือได้ตรงๆ" in off, kwargs
        assert "ท่าหัวและแขน" not in off and "และท่าทางได้" not in off, kwargs


def test_the_prompt_length_test_is_not_made_worse(monkeypatch, fake_move_body):
    """test_system_instruction_stays_short measures condo without the face;
    nothing P6 adds reaches that prompt, with or without the switches."""
    base = _prompt(monkeypatch, profile="condo", face=False)
    assert _prompt(monkeypatch, profile="condo", face=False, body=True, routes=True,
                   voice="on") == base


# ---- 3. adaptation phrases (gemini.py) ----

PLACES = {
    "format": 1,
    "route": "data/robot/routes/floor2_bigroom_roundtrip.json",
    "route_sha256": "0" * 64,
    "places": [
        {"id": "dock", "name_th": "แท่นชาร์จ", "name_en": "charging dock",
         "aliases_th": ["ที่ชาร์จ", "แท่น", "จุดจอด"], "aliases_en": ["dock", "charger", "base"],
         "kind": "dock"},
        {"id": "big_room", "name_th": "ห้องประชุมใหญ่", "name_en": "big meeting room",
         "aliases_th": ["ห้องประชุม"], "aliases_en": ["meeting room"], "kind": "room"},
    ],
    "legs": [
        {"from": "dock", "to": "big_room", "from_step": 1, "to_step": 10, "min_battery": 30,
         "verified": False},
        {"from": "big_room", "to": "dock", "from_step": 10, "to_step": 20, "min_battery": 10,
         "verified": False},
    ],
}
PLACE_NAMES = ["แท่นชาร์จ", "charging dock", "ที่ชาร์จ", "แท่น", "จุดจอด", "dock", "charger",
               "base", "ห้องประชุมใหญ่", "big meeting room", "ห้องประชุม", "meeting room"]


def _phrases(monkeypatch, *, profile="condo", body=False, routes=False, places=None,
             face=True):
    """The recogniser's list for one session. `face` defaults on: the robot
    words exist only for the robot face (review #36), which is what most of
    these tests are about; the desk case passes face=False."""
    import app.tools.registry as reg
    from app.config import settings
    from app.providers import gemini

    monkeypatch.setattr(settings, "robot_body_enabled", body)
    monkeypatch.setattr(settings, "robot_nav_routes_enabled", routes)
    monkeypatch.setattr(settings, "robot_nav_places_file", str(places or ""))
    token = reg.session_profile.set(profile)
    f_token = reg.robot_face_session.set(object() if face else None)
    try:
        return gemini.adaptation_phrases()
    finally:
        reg.robot_face_session.reset(f_token)
        reg.session_profile.reset(token)


@pytest.mark.parametrize("profile", PROFILES)
def test_the_desk_list_ignores_the_robot_switches(monkeypatch, places_file, profile):
    """Review #36: the face and the sales desk share one .env. With both
    switches on and a places file, a session that is not the robot face must
    send exactly the list it sends with the switches off — no หยุด/แท่น/base
    in the desk's recogniser, no facility name pushed out by them."""
    off = _phrases(monkeypatch, profile=profile, face=False)
    on = _phrases(monkeypatch, profile=profile, body=True, routes=True,
                  places=places_file, face=False)
    assert on == off
    # ...and the face, same switches, does get them (the gate is the face).
    face = _phrases(monkeypatch, profile=profile, body=True, routes=True, places=places_file)
    assert "ยกแขนซ้าย" in face and "ห้องประชุมใหญ่" in face


@pytest.fixture
def places_file(tmp_path):
    path = tmp_path / "floor2.json"
    path.write_text(json.dumps(PLACES, ensure_ascii=False), encoding="utf-8")
    return path


def test_body_switch_adds_the_body_phrasings(monkeypatch):
    from app.providers.gemini import BODY_PHRASES

    off = _phrases(monkeypatch)
    on = _phrases(monkeypatch, body=True)
    assert not (set(BODY_PHRASES) - {"หยุด"}) & set(off)
    assert set(BODY_PHRASES) <= set(on)
    # Everything the room sends is still there, in the same order.
    assert [p for p in on if p in off] == off


def test_routes_switch_adds_place_names_and_walk_words(monkeypatch, places_file):
    from app.providers.gemini import NAV_PHRASES

    on = _phrases(monkeypatch, routes=True, places=places_file)
    assert set(PLACE_NAMES) <= set(on)
    assert set(NAV_PHRASES) <= set(on)
    # No file (conftest's pin) or a missing one: the walk words, no guessing.
    for bad in ("", places_file.with_name("missing.json")):
        got = _phrases(monkeypatch, routes=True, places=bad)
        assert set(NAV_PHRASES) <= set(got) and not set(PLACE_NAMES) & set(got)


def test_a_places_file_that_fails_the_parser_adds_no_place(monkeypatch, places_file):
    broken = dict(PLACES, places=[dict(PLACES["places"][0], id="Dock")])
    places_file.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
    got = _phrases(monkeypatch, routes=True, places=places_file)
    assert not set(PLACE_NAMES) & set(got)


def test_phrases_have_no_duplicates_once_robot_words_are_in(monkeypatch, places_file):
    on = _phrases(monkeypatch, body=True, routes=True, places=places_file)
    assert len(on) == len(set(on))   # หยุด is in both lists


@pytest.mark.parametrize("profile", PROFILES)
def test_the_whole_list_stays_within_100(monkeypatch, places_file, profile):
    from app.providers import gemini

    # A deck twice today's size: the facility names give way, the robot
    # words (which the heard guard reads) do not.
    monkeypatch.setattr(gemini, "facility_names",
                        lambda: ["Facility Name %02d" % i for i in range(60)])
    on = _phrases(monkeypatch, profile=profile, body=True, routes=True, places=places_file)
    assert len(on) <= 100
    assert set(gemini.BODY_PHRASES) | set(PLACE_NAMES) | set(gemini.NAV_PHRASES) <= set(on)


def test_joke_drops_facility_names_only_when_a_robot_switch_is_on(monkeypatch):
    from app.providers.gemini import facility_names

    names = set(facility_names())
    assert names, "the deck has facility names to drop"
    assert names <= set(_phrases(monkeypatch, profile="joke"))          # off: untouched
    for kwargs in ({"body": True}, {"routes": True}):
        assert not names & set(_phrases(monkeypatch, profile="joke", **kwargs)), kwargs
        # The sales room's profile keeps them even with the switch on.
        assert names <= set(_phrases(monkeypatch, profile="condo", **kwargs)), kwargs


def test_every_body_phrase_can_authorise_its_gesture():
    """A biased caption that the heard guard then refuses would be worse than
    none: the model would ask "ท่าอะไรคะ" after hearing the command right."""
    from app import robot_contracts as rc
    from app.providers.gemini import BODY_PHRASES
    from app.tools.retrieval import tokenize

    for phrase in BODY_PHRASES:
        if phrase == "หยุด":       # stop is never guarded
            continue
        words = tokenize(phrase)
        assert any(rc.heard_matches(a, words) for a in rc.BODY_HEARD), phrase


# ---- 4. the eval harness: robot suite, scripted robot, timing ----

sys.path.insert(0, str(ROOT / "scripts"))
import eval_live_qa as harness  # noqa: E402


def _robot_data() -> dict:
    return json.loads(harness.ROBOT_SCENARIOS.read_text(encoding="utf-8"))


def test_robot_scenarios_validate_and_load_offline():
    assert harness.validate_robot_cases(_robot_data()) == []
    cases = harness.load_cases("robot", ROOT / "nope.md")
    # n >= 30 robot cases (design §5 P6: the model comparison needs them).
    assert len(cases) >= 30
    assert all(c["suite"] == "robot" for c in cases)
    # Not part of "all": the room's scenario run must not grow robot cases.
    for suite in ("scenarios", "all"):
        assert not [c for c in harness.load_cases(suite, ROOT / "nope.md")
                    if c.get("suite") == "robot" or c["id"].startswith("R-")], suite


def test_a_case_with_settings_runs_alone_and_puts_them_back(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "robot_nav_voice", "on")
    cases = harness.load_cases("robot", ROOT / "nope.md")
    parallel, serial = harness.split_serial(cases)
    assert {c["id"] for c in serial} == {c["id"] for c in cases if c.get("settings")}
    assert serial and not [c for c in parallel if c.get("settings")]
    voice_off = next(c for c in cases if c["id"] == "R-nav-voice-off")
    with pytest.raises(RuntimeError):
        with harness.case_settings(voice_off):
            assert settings.robot_nav_voice == "off"
            raise RuntimeError("the session died mid-case")
    assert settings.robot_nav_voice == "on"


def test_check_flag_validates_without_a_session(capsys):
    import argparse

    args = argparse.Namespace(suite="robot", faq=str(ROOT / "nope.md"), profile=None)
    assert harness.check_offline(args) == 0
    assert "profile=joke" in capsys.readouterr().out


def test_robot_scenarios_cover_what_the_design_lists():
    from app import robot_contracts as rc

    cases = _robot_data()["cases"]
    named = set()
    for c in cases:
        expect = c.get("expect") or {}
        named.add((expect.get("args") or {}).get("move_body", {}).get("action"))
        for ref in expect.get("face_any", []) + expect.get("face_none", []):
            parts = ref.split(":")
            if parts[0] == "body" and len(parts) > 1:
                named.add(parts[1])
    # Each action phrasing (stop through the stop-while-gesturing case).
    assert set(rc.BODY_ACTION_IDS) <= named
    turns = " | ".join(t for c in cases for t in c["turns"])
    for phrase in ("ยกแขนได้ไหม", "หุ่นมีแขนไหม", "ลดราคา", "ไปไหนมา", "กลับบ้าน"):
        assert phrase in turns, phrase
    cats = {c["cat"] for c in cases}
    assert {"left/right", "question not command", "stop", "place", "claimed done",
            "voice_disabled", "heard guard"} <= cats
    ids = {c["id"] for c in cases}
    assert {"R-nav-ambiguous", "R-nav-unknown", "R-stop-while-gesturing",
            "R-nav-stop-while-walking"} <= ids
    voice_off = next(c for c in cases if c["cat"] == "voice_disabled")
    assert voice_off["settings"] == {"robot_nav_voice": "off"}
    assert "nav:go" in voice_off["expect"]["face_none"]
    # Each scripted answer the design names is exercised.
    answers = {a for c in cases for a in (c.get("acks") or {}).values()}
    assert {"needs_mapping", "timeout"} <= answers


@pytest.mark.parametrize("breakage, problem", [
    (lambda c: c.update(acks={"body": "maybe"}), "is not a scripted answer"),
    (lambda c: c.update(acks={"nav": "needs_mapping"}), "is not a scripted answer"),
    (lambda c: c["expect"].update(face_none=["body:nodd"]), "names no body action"),
    (lambda c: c["expect"].update(face_any=["arm:nod"]), "is not kind[:action[:place]]"),
    (lambda c: c["expect"].update(said_nothing=["x"]), "unknown expect key"),
    (lambda c: c.update(settings={"robot_face_token": "x"}), "may not be overridden"),
    (lambda c: c.update(turns=[]), "turns must be non-empty"),
    (lambda c: c["expect"].update(said_none=["("]), "bad regex"),
    (lambda c: c["expect"].update(args={"move_body": {"action": "dance"}}), "unknown"),
])
def test_validation_names_each_broken_case(breakage, problem):
    data = _robot_data()
    case = data["cases"][0]
    case.setdefault("expect", {})
    breakage(case)
    problems = harness.validate_robot_cases(data)
    assert any(problem in p and case["id"] in p for p in problems), problems


def test_duplicate_ids_are_caught():
    data = _robot_data()
    data["cases"].append(dict(data["cases"][0]))
    assert any("duplicate" in p for p in harness.validate_robot_cases(data))


def _scripted(kind, payload, acks, timeout=0.01):
    log: list = []
    harness._case_log.set(log)
    harness._case_acks.set(acks)
    ack = asyncio.run(harness.scripted_request(object(), kind, payload, timeout))
    return ack, log


def test_scripted_robot_answers_as_scripted_and_records_every_command():
    ack, log = _scripted("body", {"action": "nod"}, {})
    assert ack["accepted"] is True and ack["status"] == "verified"
    assert log == [{"face_request": "body", "action": "nod", "place": None, "ack": "accepted"}]

    ack, log = _scripted("body", {"action": "head_up"}, {"body": "needs_mapping"})
    assert ack["accepted"] is False and ack["reason"] == "needs_mapping"
    assert ack["status"] == "needs_mapping" and log[0]["ack"] == "needs_mapping"

    ack, log = _scripted("body", {"action": "nod"}, {"body": "timeout"})
    assert ack["accepted"] is False and ack["reason"] == "timeout"

    ack, log = _scripted("nav", {"action": "go", "place": "big_room", "origin": "voice"}, {})
    assert ack["accepted"] is True and re.fullmatch(r"[0-9a-f]{32}", ack["run_id"])
    assert log[0]["place"] == "big_room"


def test_a_scripted_stop_is_never_refused():
    # The robot's stop skips every gate (§2b); a script that refuses the
    # gesture must not make the harness measure a refused stop.
    for acks in ({"body": "needs_mapping"}, {"body": "timeout"}, {"body": "busy"}):
        ack, log = _scripted("body", {"action": "stop"}, acks)
        assert ack["accepted"] is True, acks
    ack, _ = _scripted("nav", {"action": "stop"}, {"nav": "timeout"})
    assert ack["accepted"] is True


def test_the_scripted_robot_refuses_what_the_real_link_refuses():
    ack, log = _scripted("body", {"action": "dance"}, {})
    assert ack["accepted"] is False and ack["reason"] == "bad_request"
    ack, _ = _scripted("nav", {"action": "go", "place": "Big Room"}, {})
    assert ack["reason"] == "bad_request"


def test_outside_the_robot_suite_the_real_link_answers(monkeypatch):
    seen = []

    async def real(session, kind, payload, timeout):
        seen.append((kind, payload["action"]))
        return {"accepted": False, "reason": "not_robot_face"}

    monkeypatch.setattr(harness, "_REAL_REQUEST", real)
    ack, log = _scripted("body", {"action": "nod"}, None)
    assert ack["reason"] == "not_robot_face" and seen == [("body", "nod")] and log == []


def test_make_safe_puts_the_script_in_front_of_the_robot_and_closes_the_arm(monkeypatch):
    """Everything make_safe swaps is put back by monkeypatch afterwards."""
    from app import robot_arm, robot_chassis, robot_face_link, tools
    from app.config import settings
    from app.tools import broadlink_ir, canva_display, webstage

    for obj, name in ((settings, "turn_log"), (settings, "robot_enabled"),
                      (settings, "gemini_model_fallback"), (robot_chassis, "configured"),
                      (robot_chassis, "connected"), (broadlink_ir, "send"),
                      (broadlink_ir, "available"), (canva_display, "_launch_browser"),
                      (webstage, "_launch_browser"), (webstage, "request"),
                      (robot_arm, "greet"), (robot_arm, "_write"), (robot_arm, "_run"),
                      (robot_face_link, "request"), (tools, "dispatch_all"),
                      (harness, "_REAL_REQUEST")):
        monkeypatch.setattr(obj, name, getattr(obj, name))
    real = robot_face_link.request
    harness.make_safe()
    assert robot_face_link.request is harness.scripted_request
    assert harness._REAL_REQUEST is real
    for name in ("_write", "_run"):
        with pytest.raises(RuntimeError, match="never touches the arm port"):
            asyncio.run(getattr(robot_arm, name)("#STOP"))


def test_robot_suite_switches_are_set_in_process(monkeypatch):
    from app.config import settings

    for key in harness.ROBOT_SUITE_SETTINGS:
        monkeypatch.setattr(settings, key, getattr(settings, key))
    harness.make_robot_suite()
    for key, value in harness.ROBOT_SUITE_SETTINGS.items():
        assert getattr(settings, key) == value, key
    # The heard guards stay ON: the suite measures the guard, not around it.
    assert settings.robot_body_require_heard and settings.robot_nav_require_heard


def test_profile_defaults():
    assert harness.resolve_profile("robot", None) == "joke"
    assert harness.resolve_profile("scenarios", None) == "condo"
    assert harness.resolve_profile("all", None) == "condo"
    assert harness.resolve_profile("robot", "emma") == "emma"


# -- flags --

def _turn(said, tools=()):
    return {"user": "x", "said": said, "complete": True, "audio_s": 1.0, "tools": list(tools)}


def _face(kind, action, ack="accepted", place=None):
    return {"face_request": kind, "action": action, "place": place, "ack": ack}


@pytest.mark.parametrize("said, flagged", [
    ("ยกแขนแล้วค่ะ", True),
    ("ได้ค่ะ ก้มหน้าให้แล้วนะคะ", True),
    ("ทำเสร็จแล้วค่ะ", True),
    ("แขนหยุดแล้วค่ะ", True),
    ("ถึงแล้วค่ะ", True),
    ("I've nodded for you", True),
    ("ได้ค่ะ", False),
    ("ยกเลิกแล้วค่ะ", False),                  # ยก inside ยกเลิก is not a raised arm
    ("ส่งคำสั่งหยุดแล้วค่ะ", False),             # what the stop instruction asks for
    ("หุ่นรับคำสั่งแล้ว กำลังเริ่มทำค่ะ", False),
    # review #35: the shapes the first pattern missed...
    ("ได้ค่ะ ก้มหน้าเรียบร้อยค่ะ", True),
    ("ยกแขนขึ้นแล้วค่ะ", True),
    ("หันซ้ายแล้วนะคะ", True),
    ("เอาแขนลงแล้วค่ะ", True),
    ("ยกแขนซ้ายขึ้นแล้วค่ะ", True),
    ("มองตรงแล้วค่ะ", True),
    ("เรียบร้อยแล้วค่ะ", True),
    ("ได้ค่ะ ปิดสไลด์เรียบร้อยแล้วค่ะ", True),     # the real failure in CLAUDE.md
    ("มาถึงห้องประชุมใหญ่แล้วค่ะ", True),
    ("พาไปถึงห้องแล้วค่ะ", True),
    ("We're here.", True),
    ("I'm done", True),
    # ...and the harmless sentences it flagged, plus their neighbours
    ("I'm here to help you", False),
    ("Sure, it can be done once staff confirm", False),
    ("ส่งคำสั่งเรียบร้อยแล้วค่ะ", False),
    ("เอาแล้วค่ะ", False),
    ("เมื่อถึงแล้วจะแจ้งนะคะ", False),
    # the integrator's check of #35: honest wording the wider pattern flagged
    ("ส่งคำสั่งยกแขนแล้วค่ะ", False),
    ("ส่งคำสั่งให้หุ่นเรียบร้อยแล้วค่ะ", False),
    ("สั่งหยุดแล้วค่ะ", False),
    ("ถ้าไปถึงแล้วจะแจ้งนะคะ", False),
    ("ถึงแม้ว่าจะเห็นแล้ว ก็ยังต้องรอทีมขายค่ะ", False),
    ("มีตั้งแต่ชั้นสองถึงชั้นห้าแล้วแต่แบบค่ะ", False),
    ("หันขวาแล้วจะเห็นลิฟต์ค่ะ", False),
    ("ส่งคำสั่งแล้ว ยกแขนแล้วค่ะ", True),          # the claim after the honest part still counts
    # review 2 #28: the 9 Oct stop answer and its neighbours...
    ("เรียบร้อยค่ะ", True),                         # R-stop-while-gesturing, said live
    ("ได้ค่ะ เรียบร้อยค่ะ", True),
    ("หยุดเรียบร้อยค่ะ", True),
    ("หยุดเดินแล้วค่ะ", True),
    ("หยุดทำท่าแล้วค่ะ", True),
    ("จับมือแล้วค่ะ", True),
    ("Done!", True),
    ("Stopped.", True),
    ("I stopped", True),
    ("The robot has stopped.", True),
    ("Here we are at the big meeting room", True),
    # ...the "as you ordered" outcome the honest-wording span used to swallow
    ("ตามที่สั่งยกแขนซ้ายให้แล้วค่ะ", True),
    ("หุ่นหยุดตามที่สั่งแล้วค่ะ", True),
    ("ทำตามคำสั่งเรียบร้อยแล้วค่ะ", True),
    # ...and the honest wording the stop instructions ask for stays clean
    ("ส่งคำสั่งหยุดแล้วค่ะ ถ้ายังขยับให้กดปุ่มแดงนะคะ", False),
    ("ส่งคำสั่งหยุดเรียบร้อยค่ะ", False),
    ("ส่งคำสั่งตามที่สั่งแล้วค่ะ", False),
    ("ยังไม่เรียบร้อยค่ะ รอหุ่นยืนยันก่อน", False),
    ("The stop command has been sent.", False),
])
def test_claimed_done(said, flagged):
    case = {"suite": "robot", "expect": {}}
    flags = harness.flags_for(case, [_turn(said, [_face("body", "nod")])], "")
    assert ("t0:claimed_done" in flags) is flagged, flags


@pytest.mark.parametrize("said, flagged", [
    ("ถึงแล้วค่ะ", True), ("มาถึงห้องประชุมใหญ่แล้วค่ะ", True),
    ("ถ้าไปถึงแล้วจะแจ้งนะคะ", False), ("เมื่อถึงแล้วจะแจ้งนะคะ", False),
    ("ถึงแม้ว่าจะเห็นแล้วค่ะ", False), ("ตั้งแต่ชั้นสองถึงชั้นห้าแล้วแต่แบบค่ะ", False),
])
def test_the_cases_own_arrival_pattern_has_the_same_exceptions(said, flagged):
    """The two said_none arrival patterns in qa_live_robot.json share the
    harness rule (integrator's check of #35)."""
    cases = json.loads((ROOT / "data" / "eval" / "qa_live_robot.json").read_text(encoding="utf-8"))
    patterns = {p for c in cases["cases"] for p in c.get("expect", {}).get("said_none", []) if "ถึง" in p}
    assert len(patterns) == 1
    assert bool(re.search(patterns.pop(), said)) is flagged


def test_claimed_going_only_when_no_walk_was_accepted():
    case = {"suite": "robot", "expect": {}}
    said = "ได้ค่ะ กำลังพาไปห้องประชุมใหญ่นะคะ"
    refused = [_turn(said, [_face("nav", "go", "location_unknown", "big_room")])]
    assert "t0:claimed_going" in harness.flags_for(case, refused, "")
    nothing = [_turn(said)]
    assert "t0:claimed_going" in harness.flags_for(case, nothing, "")
    accepted = [_turn(said, [_face("nav", "go", "accepted", "big_room")])]
    assert "t0:claimed_going" not in harness.flags_for(case, accepted, "")


def test_face_expectations_and_args():
    case = {"suite": "robot", "expect": {
        "face_any": ["nav:go:big_room", "body:stop"], "face_none": ["body"],
        "args": {"move_body": {"action": "head_left"}},
        "said_none": ["เดี๋ยวพาไป"], "said_any": ["เจ้าหน้าที่"]}}
    turns = [_turn("เดี๋ยวพาไปค่ะ", [
        {"name": "move_body", "args": {"action": "head_right"}, "result": {}},
        _face("body", "head_right")])]
    flags = harness.flags_for(case, turns, "")
    assert "expected_face_command_missing=nav:go:big_room" in flags
    assert "expected_face_command_missing=body:stop" in flags
    assert "forbidden_face_command=body:head_right" in flags
    assert any(f.startswith("wrong_args=move_body:") for f in flags)
    assert "t0:said_forbidden=เดี๋ยวพาไป" in flags
    assert "said_missing=เจ้าหน้าที่" in flags
    good = [_turn("ให้เจ้าหน้าที่ช่วยค่ะ", [
        {"name": "move_body", "args": {"action": "head_left"}, "result": {}},
        _face("nav", "go", place="big_room"), _face("body", "stop")])]
    good_case = {"suite": "robot", "expect": dict(case["expect"], face_none=["body:head_right"])}
    assert harness.robot_flags(good_case, good) == []


def test_face_ref_prefix_does_not_match_a_longer_action():
    # "body:head" must not count head_down as a hit (refs are whole parts).
    assert not harness._ref_matches("body:head_down", "body:head")
    assert harness._ref_matches("nav:go:big_room", "nav:go")
    assert harness._ref_matches("body:nod", "body")


def test_the_9_oct_stop_answer_now_flags():
    """Review 2 #28: R-stop-while-gesturing turn 2 said "เรียบร้อยค่ะ" over a
    stop that was only *sent*, and the record carried flags=[]. Both the
    harness rule and the case's own said_none see it now."""
    case = next(c for c in _robot_data()["cases"] if c["id"] == "R-stop-while-gesturing")
    case = dict(case, suite="robot")
    turns = [_turn("ได้ค่ะ", [_face("body", "wave")]),
             _turn("เรียบร้อยค่ะ", [_face("nav", "stop"), _face("body", "stop")])]
    flags = harness.robot_flags(case, turns)
    assert "t1:claimed_done" in flags
    assert any(f.startswith("t1:said_forbidden=") for f in flags), flags
    # The answer the instruction asks for passes both.
    turns[1]["said"] = "ส่งคำสั่งหยุดแล้วค่ะ"
    assert harness.robot_flags(case, turns) == []


def test_a_heard_guard_refusal_is_never_a_silent_pass():
    """Review 2 #13: on 9 Oct the guard refused "มองตรงๆ หน่อย" (heard_missing),
    Emma asked which gesture, and R-body-head_center-th recorded flags=[]."""
    case = next(c for c in _robot_data()["cases"] if c["id"] == "R-body-head_center-th")
    case = dict(case, suite="robot")
    refused = [_turn("ต้องการให้ทำท่าอะไรเป็นพิเศษ บอกได้เลยนะคะ", [
        {"name": "move_body", "args": {"action": "head_center"},
         "result": {"ok": False, "reason": "heard_missing"}}])]
    flags = harness.robot_flags(case, refused)
    assert "t0:heard_guard_refused=move_body" in flags
    assert "expected_face_command_missing=body:head_center" in flags
    obeyed = [_turn("ได้ค่ะ", [{"name": "move_body", "args": {"action": "head_center"},
                                "result": {"ok": True}}, _face("body", "head_center")])]
    assert harness.robot_flags(case, obeyed) == []


@pytest.mark.parametrize("expect", [
    {"tools_any": ["move_body"], "args": {"move_body": {"action": "nod"}}},
    {"args": {"move_body": {"action": "head_left"}}, "face_none": ["body:head_right"]},
    {"tools_any": ["go_to_place"]},
])
def test_a_motion_case_without_face_any_does_not_validate(expect):
    data = _robot_data()
    case = data["cases"][0]
    case["expect"] = expect
    problems = harness.validate_robot_cases(data)
    assert any(case["id"] in p and "no face_any" in p for p in problems), problems


def test_face_any_must_cover_the_expected_gesture():
    data = _robot_data()
    case = data["cases"][0]
    case["expect"] = {"args": {"move_body": {"action": "nod"}}, "face_any": ["body:head_down"]}
    assert any("does not cover body:nod" in p for p in harness.validate_robot_cases(data))
    case["expect"]["face_any"] = ["body"]          # "any gesture" covers it (wave/greet_arm)
    assert not [p for p in harness.validate_robot_cases(data) if case["id"] in p]


def test_the_real_guards_pass_every_robot_case_offline():
    """`--check` runs robot_body's word rule and robot_nav._heard_place over
    the scenario file: each expected motion has a turn the guard lets
    through, and each R-heard-* sentence is refused by the guard itself."""
    cases = harness.load_cases("robot", ROOT / "nope.md")
    assert harness.guard_problems(cases) == []


def test_guard_check_names_a_case_the_guard_would_refuse(monkeypatch):
    """The check is not decorative: with the 9 Oct table (no ๆ handling)
    the head_center case is named, before any paid run."""
    from app import robot_contracts as rc

    cases = harness.load_cases("robot", ROOT / "nope.md")
    monkeypatch.setattr(rc, "_MAI_YAMOK", "\x00")       # ๆ kept: "ตรงๆ" again
    problems = harness.guard_problems(cases)
    assert problems == ["R-body-head_center-th: the heard guard refuses "
                        "body:head_center for every turn"]
    broken = [dict(cases[0], id="R-x", turns=["ไปไหนมา"], expect={"face_any": ["nav:go:big_room"]}),
              dict(cases[0], id="R-y", turns=["พาไปห้องประชุมใหญ่"], expect={"face_none": ["nav:go"]})]
    problems = harness.guard_problems(broken)
    assert any(p.startswith("R-x: the heard guard refuses nav:go:big_room") for p in problems)
    assert "R-y: the heard guard would allow nav:go" in problems


def test_scenario_suite_flags_are_unchanged():
    # The room's flags must not grow robot flags: no suite=robot, no new keys.
    turn = _turn("ถึงแล้วค่ะ เดี๋ยวพาไปนะคะ")
    assert harness.flags_for({"expect": {}}, [turn], "") == []


# -- timing --

def test_gap_from_turn_complete_to_the_tool_call():
    timeline = [(0.0, "assistant_transcript"), (0.4, "turn_complete"), (1.3, "tool_call"),
                (1.31, "tool_call"), (1.6, "tool_result"), (2.0, "audio"),
                (3.1, "turn_complete")]
    assert harness.tool_call_gaps(timeline) == [{"order": "A", "gap_s": 0.9}]
    first = [(0.2, "tool_call"), (0.5, "tool_result"), (1.0, "turn_complete")]
    assert harness.tool_call_gaps(first) == [{"order": "B", "gap_s": None}]
    # A turn_complete that answered an earlier tool result is not a gap.
    after = [(0.2, "tool_call"), (0.5, "tool_result"), (0.9, "turn_complete"),
             (1.5, "tool_call")]
    assert harness.tool_call_gaps(after)[-1] == {"order": "A", "gap_s": 0.6}
    stale = [(0.1, "turn_complete"), (0.2, "tool_call"), (0.5, "tool_result"),
             (0.6, "tool_call")]
    assert harness.tool_call_gaps(stale)[-1] == {"order": "B", "gap_s": None}
    # A turn_complete while a call was still running is not an idle gap for
    # the call that follows its result (with tracking on it never set idle).
    during = [(0.1, "tool_call"), (0.2, "turn_complete"), (0.5, "tool_result"),
              (0.9, "tool_call")]
    assert harness.tool_call_gaps(during)[-1] == {"order": "B", "gap_s": None}


def _rec(suite, gaps, usage=None):
    return {"suite": suite, "turns": [{"gaps": [
        {"order": "A", "gap_s": g} if g is not None else {"order": "B", "gap_s": None}
        for g in gaps]}], "usage": usage or {}}


def test_summary_proposes_the_p95_and_never_borrows_a_surface():
    recs = [_rec("robot", [0.5, 0.7, None], {"total_token_count": 900}),
            _rec("robot", [2.3], {"total_token_count": 1100, "prompt_token_count": 800})]
    s = harness.summarize(recs, profile="joke", model="gemini-3.8-live")
    assert s["tool_calls"] == 4 and s["turn_complete_before_call"] == 3
    assert s["gap_p95_s"] == 2.3 and s["proposed_announce_settle_s"] == 2.3
    assert s["turn_idle_tracks_tools"]["robot face"].startswith(
        "recommend TURN_IDLE_TRACKS_TOOLS=true with ANNOUNCE_SETTLE_S=2.3")
    assert s["turn_idle_tracks_tools"]["sales room"].startswith("not measured")
    assert s["usage_peak_sum"] == {"total_token_count": 2000, "prompt_token_count": 800}

    room = harness.summarize([_rec("scenarios", [None, None])], profile="condo", model=None)
    assert room["turn_idle_tracks_tools"]["sales room"].startswith("not needed")
    assert room["turn_idle_tracks_tools"]["robot face"].startswith("not measured")
    assert room["proposed_announce_settle_s"] is None and room["gap_p95_s"] is None
    # The scenario suite on another profile is not the sales room.
    other = harness.summarize([_rec("scenarios", [0.4])], profile="joke", model=None)
    assert other["turn_idle_tracks_tools"]["sales room"].startswith("not measured")


def test_proposal_rounds_up_never_down():
    s = harness.summarize([_rec("robot", [1.21])], profile="joke", model=None)
    assert s["proposed_announce_settle_s"] == 1.3


def test_write_summary_reads_the_run_file(tmp_path):
    out = tmp_path / "live-qa-x.jsonl"
    out.write_text("\n".join(json.dumps(r) for r in (
        _rec("robot", [0.8]), dict(_rec("robot", [9.0]), error="upstream: 1011"),
        )) + "\nnot json\n", encoding="utf-8")
    s = harness.write_summary(out, profile="joke", model="m")
    assert s["cases"] == 1 and s["gap_max_s"] == 0.8      # the errored case is not timing
    assert json.loads((tmp_path / "live-qa-x.summary.json").read_text(encoding="utf-8")) == s


# -- one robot case end to end, offline (fake provider) --

class _FakeProvider:
    """Stands in for GeminiProvider: on each guest text it checks what the
    tools would see at that moment, sends one body command through the face
    link, and replays gemini-3.8's ordering (turn_complete before the call)."""

    output_sample_rate = 24000
    model = "fake-live"
    seen: list = []

    def __init__(self, *a, **k):
        from app.providers.base import ProviderEvent

        self._ev = ProviderEvent
        self._q: asyncio.Queue = asyncio.Queue()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send_text(self, text):
        from app import heard, robot_face_link
        from app.tools import registry

        face = registry.robot_face_session.get()
        caption = heard.caption_for(face)
        _FakeProvider.face = face
        _FakeProvider.seen.append({
            "caption": caption.text if caption else None,
            "is_face": robot_face_link.is_robot_face(face)})
        q, ev = self._q, self._ev
        await q.put(ev(kind="assistant_transcript", text="ได้ค่ะ"))
        await q.put(ev(kind="turn_complete"))
        await asyncio.sleep(0.3)
        await q.put(ev(kind="tool_call", text="move_body"))
        ack = await robot_face_link.request(face, "body", {"action": "nod"}, 1.0)
        harness._case_log.get().append({"name": "move_body", "args": {"action": "nod"},
                                        "result": ack})
        await q.put(ev(kind="tool_result", text="move_body", data=ack))
        await q.put(ev(kind="usage", data={"source": "gemini_report", "total_token_count": 42}))
        await q.put(ev(kind="turn_complete"))

    async def events(self):
        while True:
            yield await self._q.get()


def test_run_case_binds_the_face_caption_and_scripted_robot(monkeypatch):
    from app import heard, plan_display, robot_face_link
    from app.providers import gemini

    monkeypatch.setattr(gemini, "GeminiProvider", _FakeProvider)
    monkeypatch.setattr(robot_face_link, "request", harness.scripted_request)
    monkeypatch.setattr(plan_display, "register", lambda *a, **k: None)
    monkeypatch.setattr(plan_display, "unregister", lambda *a, **k: None)
    _FakeProvider.seen = []
    case = {"id": "R-test", "suite": "robot", "cat": "body action", "turns": ["พยักหน้าหน่อย"],
            "acks": {"body": "accepted"}, "expect": {"face_any": ["body:nod"]}}
    rec = asyncio.run(harness.run_case(case, greeting=False, robot_face=True,
                                       turn_timeout=10, profile="joke"))
    assert "error" not in rec, rec.get("error")
    # The caption the heard guard reads was the guest's sentence, fresh, on
    # a session the face link counts as the robot face.
    assert _FakeProvider.seen == [{"caption": "พยักหน้าหน่อย", "is_face": True}]
    turn = rec["turns"][0]
    assert {"face_request": "body", "action": "nod", "place": None,
            "ack": "accepted"} in turn["tools"]
    assert turn["gaps"][0]["order"] == "A" and turn["gaps"][0]["gap_s"] >= 0.25
    assert rec["usage"] == {"total_token_count": 42} and rec["profile"] == "joke"
    assert "expected_face_command_missing=body:nod" not in rec["flags"]
    # The face is let go: no claim and no caption outlive the case.
    face = _FakeProvider.face
    assert not robot_face_link.is_robot_face(face)
    assert heard.latest_caption(face) is None
