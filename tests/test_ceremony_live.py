"""The live session and the ceremony's script (owner 6 Nov 2026: "ส่งให้ Gemini ตอบ หรือ
ให้ตอบจากสคริปต์ อันไหนดีกว่า วัดดู"): EVENT_SCRIPT_PROMPT (the script in the ceremony block,
app/event_host.py script_block), EVENT_SCRIPT_TOOL (`say_script_line`, app/tools/ceremony.py)
and the measurement (scripts/eval_live_qa.py --suite ceremony). Since 9 Nov the ceremony is
the Emma Ceremony app (profile `ceremony`, app/ceremony.py: "ไม่งั้นจะปนกัน").

What these hold down:
* both switches off = the ceremony prompt without a script, and the event prompt never has one;
* only an approved cue ever reaches the model, in either mode -- a draft never does;
* the tool exists only for the ceremony profile, with its switch and its group, and the
  prompt names it only when the model can see it;
* a recorded cue tells her not to say it again, a cue with no recording quotes it;
* a recording is not her reply, so the tool asks the ceremony's door itself;
* `--check` refuses a cases file that would measure nothing.
"""
from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import event_host, event_script
from app.config import settings
from tests.ceremony_app import ceremony_off, ceremony_on

ROOT = Path(__file__).resolve().parents[1]
EVENT = ROOT / "tests" / "data" / "floor1_event_rehearsed.json"
REAL_SCRIPT = ROOT / "data" / "ceremony" / "floor1_ceremony.json"
CASES = ROOT / "data" / "eval" / "qa_live_ceremony.json"


def _signed(raw: dict) -> dict:
    """A cue signed for the test (tmp file only: the tracked script stays all drafts)."""
    c = event_script._normalise(raw)
    return dict(raw, approved_by="test", approved_at="2026-11-06 22:00",
                approved_sha256=event_script.digest(c))


SCRIPT = {"format": event_script.FORMAT, "host_th": "คุณมิกิ", "cues": [
    _signed({"id": "s1_greet", "scene": 1, "host_th": "(Emma เดินมาหยุดกลางเวที หันหน้าหาคุณมิกิ)",
             "th": "สวัสดีค่ะคุณมิกิ ฉันชื่อเอ็มม่าค่ะ", "numbers": []}),
    _signed({"id": "s1_can_do", "scene": 1, "host_th": "สวัสดีเอ็มม่า เอ็มม่าทำอะไรได้บ้าง",
             "th": "ฉันทำอะไรได้หลายอย่างเลยค่ะ คุณมิกิลองถามมาได้เลยค่ะ", "numbers": []}),
    # a brief of this test's own: the real one is the lead's to word (it names its tool)
    _signed({"id": "s4_recommend", "scene": 4, "host_th": "ฉันอยากได้ห้องที่เป็น Sea View ครับ",
             "th": None, "live_th": "บรีฟของเทสต์สำหรับคิวที่ไม่มีประโยคตายตัว", "numbers": []}),
    # signed by nobody: must never reach the model
    {"id": "s9_thanks", "scene": 9, "host_th": "ขอเปิด Preview ครับ", "th": "ขอขอบคุณทุกคนค่ะ", "numbers": [],
     "approved_by": None, "approved_at": None, "approved_sha256": None},
]}


@pytest.fixture
def ceremony(tmp_path, monkeypatch):
    """The tracked event file in a tmp folder (its facts and spots are shared), a small signed
    ceremony file of its own, and the ceremony app's call up (the named-only ceremony: these
    measure her live answer)."""
    target = tmp_path / "floor1_event.json"
    target.write_text(EVENT.read_text(encoding="utf-8"), encoding="utf-8")
    script = tmp_path / "floor1_ceremony.json"
    script.write_text(json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "event_file", str(target))
    monkeypatch.setattr(settings, "ceremony_file", str(script))
    event_host.reset()
    event_script.reset()
    ceremony_on(replies="named")
    yield target
    event_host.reset()
    event_script.reset()


@pytest.fixture
def ceremony_tool(ceremony, monkeypatch):
    """The cue tool registered and switched on for the event profile."""
    import app.tools.ceremony as module
    from app.tools import registry

    if registry.get("say_script_line") is None:          # another test cleared the registry
        import importlib

        importlib.reload(module)
    monkeypatch.setattr(settings, "tools_enabled", True)
    monkeypatch.setattr(settings, "event_script_tool", True)
    monkeypatch.setattr(settings, "tool_groups", "knowledge,ceremony")
    return module


def _visible(profile: str, name: str = "say_script_line") -> bool:
    from app.tools import registry

    token = registry.session_profile.set(profile)
    try:
        entry = registry.get(name)
        return entry is not None and registry.allowed(entry)
    finally:
        registry.session_profile.reset(token)


def _prompt(profile: str) -> str:
    from app.prompts import build_instructions
    from app.tools import registry

    token = registry.session_profile.set(profile)
    try:
        return build_instructions("Embassy World", profile=profile, robot_face=True)
    finally:
        registry.session_profile.reset(token)


def _event_prompt() -> str:
    return _prompt("event")


def _ceremony_prompt() -> str:
    return _prompt("ceremony")


# ---- the free mode: the script in the prompt ---------------------------------------------

def _scene_texts() -> dict[str, str | None]:
    return {sc["id"]: event_host.scene_text(sc, walking=False, spot_id=event_host._here(sc))
            for sc in event_host.load()["scenes"]}


def test_both_switches_off_leave_the_ceremony_block_without_a_script(ceremony, monkeypatch):
    """Ceremony running, signed cues on disk: still not one byte more with the switches off --
    in the ceremony's prompt; the event's prompt and its scenes never carry any of it."""
    assert settings.event_script_prompt is False and settings.event_script_tool is False
    assert event_host.script_cues()                       # there IS something it could add
    block = event_host.ceremony_block()
    texts = _scene_texts()
    assert event_host.script_block() is None
    assert event_host.SCRIPT_HEAD not in block and "«" not in block and "say_script_line" not in block
    assert not any("บทพิธี" in (t or "") for t in texts.values())
    # ...and the same texts as with no script and no ceremony at all
    ceremony_off()
    monkeypatch.setattr(event_host, "script_cues", lambda: {})
    assert event_host.ceremony_block() == block and _scene_texts() == texts


def test_the_free_block_is_the_ceremony_apps_and_never_the_events(ceremony, monkeypatch):
    """The ceremony app's call IS the ceremony (9 Nov): the block no longer names the scenes it is
    for, and the Event app's prompt has none of it, switch on or off (owner "ไม่งั้นจะปนกัน")."""
    off, event_off = _ceremony_prompt(), _event_prompt()
    monkeypatch.setattr(settings, "event_script_prompt", True)
    block = event_host.script_block()
    assert block is not None and block in event_host.ceremony_block()
    assert block not in event_host.event_block() and event_host.SCRIPT_HEAD not in event_host.event_block()
    assert block.startswith(event_host.SCRIPT_HEAD)
    assert "ฉากล่าสุด" not in block and "ฉากพิธีเปิด" not in block    # no scene to be scoped to
    assert "«ฉันทำอะไรได้หลายอย่างเลยค่ะ คุณมิกิลองถามมาได้เลยค่ะ»" in block
    assert "คุณมิกิ: 'สวัสดีเอ็มม่า เอ็มม่าทำอะไรได้บ้าง'" in block
    assert "จังหวะ (Emma เดินมาหยุดกลางเวที" in block     # the stage direction is a moment, not words
    brief = event_script.cue("s4_recommend", SCRIPT)["live_th"]
    assert "ไม่มีประโยคตายตัว ตอบเองตามแนวนี้: " + brief in block   # the live cue's own brief, whatever it says
    assert "ขอขอบคุณทุกคนค่ะ" not in block                 # the draft
    assert "ตัวเลขพูดได้เฉพาะ" in block and "ใช้แทนกฎ" in block
    assert "say_script_line" not in block                  # the tool is not visible
    on = _ceremony_prompt()
    assert len(on) - len(off) == len(block) + 1          # the block and its line break, nothing else
    assert _event_prompt() == event_off                   # the Event app's call: not a byte of it
    # the same block whatever the mode (the call's prompt is built once, at its claim)
    ceremony_off()
    assert event_host.script_block() == block


def test_no_scene_announcement_carries_the_script(ceremony, monkeypatch):
    """Until 9 Nov a scene's announcement was her clock for the ceremony (on / over); the app is
    the clock now, and the Event app's scenes say nothing of the script with any switch on."""
    before = _scene_texts()
    monkeypatch.setattr(settings, "event_script_prompt", True)
    assert _scene_texts() == before
    monkeypatch.setattr(settings, "event_script_prompt", False)
    monkeypatch.setattr(settings, "event_script_tool", True)
    monkeypatch.setattr(settings, "tool_groups", "knowledge,ceremony")
    assert _scene_texts() == before


def test_a_draft_never_reaches_the_free_block(monkeypatch, tmp_path):
    """The tracked script as drafts: with the switch on and a ceremony running, nothing."""
    drafts = tmp_path / "floor1_ceremony.json"
    drafts.write_text(json.dumps(_as_drafts(json.loads(REAL_SCRIPT.read_text(encoding="utf-8"))),
                                 ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "event_file", str(EVENT))
    monkeypatch.setattr(settings, "ceremony_file", str(drafts))
    event_script.reset()
    ceremony_on()
    monkeypatch.setattr(settings, "event_script_prompt", True)
    try:
        assert event_script.servable() == {}
        assert event_host.script_block() is None
    finally:
        event_host.reset()
        event_script.reset()


# ---- the tool mode -------------------------------------------------------------------------

def test_the_tool_is_not_declared_with_its_switch_off(ceremony_tool, monkeypatch):
    monkeypatch.setattr(settings, "event_script_tool", False)
    assert _visible("ceremony") is False
    from app.tools import registry

    token = registry.session_profile.set("ceremony")
    try:
        assert "say_script_line" not in [t.name for t in registry.all_tools()]
    finally:
        registry.session_profile.reset(token)


def test_the_tool_is_the_ceremony_apps_only(ceremony_tool):
    """The event profile had it until 9 Nov; the Event app's call never sees it now."""
    assert _visible("ceremony") is True
    for profile in ("condo", "joke", "emma", "translator", "event"):
        assert _visible(profile) is False, profile


def test_the_tool_needs_its_group_and_a_signed_cue(ceremony_tool, monkeypatch):
    monkeypatch.setattr(settings, "tool_groups", "knowledge")
    assert _visible("ceremony") is False                 # opt-in: TOOL_GROUPS must name it
    monkeypatch.setattr(settings, "tool_groups", "")
    assert _visible("ceremony") is False                 # ...and blank TOOL_GROUPS is not naming it
    monkeypatch.setattr(settings, "tool_groups", "knowledge,ceremony")
    monkeypatch.setattr(event_host, "script_cues", lambda: {})
    assert _visible("ceremony") is False                 # all drafts = no tool
    from app import tools

    assert "ceremony" in tools._OPT_IN and tools._TOOL_MODULES["app.tools.ceremony"] == "ceremony"


def test_the_declaration_lists_the_signed_cues(ceremony_tool):
    from app.tools import registry

    description, parameters = registry.declaration(registry.get("say_script_line"))
    assert parameters["properties"]["cue"]["enum"] == ["s1_greet", "s1_can_do", "s4_recommend"]
    assert parameters["required"] == ["cue"]
    assert "s1_can_do = ฉาก 1 · คุณมิกิ: 'สวัสดีเอ็มม่า เอ็มม่าทำอะไรได้บ้าง'" in description
    assert "s4_recommend" in description and "(ไม่มีประโยคตายตัว)" in description
    assert "s9_thanks" not in description and "ฉันทำอะไรได้หลายอย่าง" not in description  # no lines
    # only the ceremony app's call has it: no scene to scope it to, and it says when not to call
    assert description.startswith("บทพิธีเปิดที่อนุมัติแล้ว") and "ฉากล่าสุด" not in description
    assert "ไม่ใช่สำหรับคำพูดที่ไม่ตรงบทไหน" in description


def test_out_of_the_ceremony_she_answers_herself(ceremony_tool, monkeypatch):
    monkeypatch.setattr(ceremony_tool, "_door_open", lambda: True)
    calls = _player(monkeypatch, {"ok": True, "mode": "recorded"})
    ceremony_off()                                       # the ceremony app's call is not the robot's
    out = _call(ceremony_tool, "s1_can_do")
    assert out["reason"] == "not_ceremony" and calls == []
    assert "ตอบคำถามเองทันทีตามปกติ" in out["instruction"]
    assert "ห้ามพูดประโยคของบทพิธี" in out["instruction"]


def _with_reply_language(view: dict, lang: str) -> dict:
    """The provider's own `_with_talk_language` on this tool's result, for a talk pinned to
    `lang` (TALK_LANGUAGE_NOTE) -- what the model is really handed."""
    from app import talk_lang
    from app.providers import gemini

    talk = talk_lang.TalkLanguage()
    assert talk.pin(lang)
    fake = SimpleNamespace(_talk=talk)
    return gemini.GeminiProvider._with_talk_language(fake, view)


def test_a_talk_held_in_english_does_not_unsay_the_thai_line(ceremony_tool, monkeypatch):
    """TALK_LANGUAGE_NOTE adds "answer in English ... even if this result is in another
    language" beside every tool result, after model_view -- the tool cannot take it off, so
    every result that has her say something states that the ceremony's line stays Thai."""
    monkeypatch.setattr(settings, "talk_language_note", True)
    monkeypatch.setattr(ceremony_tool, "_door_open", lambda: True)
    for answer in ({"ok": True, "mode": "say_yourself", "line": "ฉันทำอะไรได้หลายอย่างเลยค่ะ"},
                   {"ok": True, "mode": "recorded"}, {"ok": False, "error": "already"}):
        _player(monkeypatch, answer)
        view = _with_reply_language(_call(ceremony_tool, "s1_can_do"), "en")
        assert "reply_language" in view and "อังกฤษ" in view["reply_language"]   # the note is there...
        assert ceremony_tool.THAI_WINS in view["instruction"], answer          # ...and is answered


def _ceremony_block() -> str:
    """ceremony_block as a call of the ceremony profile builds it (session.py sets the profile first)."""
    from app.tools import registry

    token = registry.session_profile.set("ceremony")
    try:
        return event_host.ceremony_block()
    finally:
        registry.session_profile.reset(token)


def test_the_prompt_points_at_the_tool_only_when_it_is_visible(ceremony_tool, monkeypatch):
    monkeypatch.setattr(settings, "event_script_prompt", True)  # both on: the tool wins
    block = _ceremony_block()
    line = event_host.SCRIPT_TOOL_LINE % {"host": "คุณมิกิ"}
    assert line in block and "say_script_line" in line
    assert "«" not in block                               # the lines are the tool's, not hers to say
    assert "say_script_line" not in event_host.ceremony_block()   # a condo call cannot see the tool
    assert "say_script_line" not in event_host.event_block()      # nor does the event's block ever
    monkeypatch.setattr(settings, "tool_groups", "knowledge")   # switch on, group missing
    block = _ceremony_block()
    assert "say_script_line" not in block
    assert "«ฉันทำอะไรได้หลายอย่างเลยค่ะ" in block       # ...so the free block is what she has
    monkeypatch.setattr(settings, "event_script_prompt", False)
    assert "say_script_line" not in _ceremony_block() and "«" not in _ceremony_block()


def _call(module, cue):
    return asyncio.run(module.say_script_line(cue))


def _player(monkeypatch, result):
    """app.event_cues.play answering `result` (the module may not be in the tree yet)."""
    import sys
    import types

    import app

    calls = []

    async def play(cue_id, *, by="staff"):
        calls.append((cue_id, by))
        if isinstance(result, Exception):
            raise result
        return result

    fake = types.ModuleType("app.event_cues")
    fake.play = play
    monkeypatch.setitem(sys.modules, "app.event_cues", fake)
    monkeypatch.setattr(app, "event_cues", fake, raising=False)
    return calls


def test_a_recorded_cue_tells_her_not_to_say_it_again(ceremony_tool, monkeypatch):
    monkeypatch.setattr(ceremony_tool, "_door_open", lambda: True)
    calls = _player(monkeypatch, {"ok": True, "mode": "recorded"})
    out = _call(ceremony_tool, "s1_can_do")
    assert calls == [("s1_can_do", "model")]
    assert out["ok"] and out["mode"] == "recorded"
    assert "ห้ามพูดเนื้อหาของบทซ้ำ" in out["instruction"] and "'ค่ะ' คำเดียว" in out["instruction"]
    assert "ฉันทำอะไรได้หลายอย่าง" not in json.dumps(out, ensure_ascii=False)   # nothing to read out


def test_a_cue_with_no_recording_is_quoted_word_for_word(ceremony_tool, monkeypatch):
    monkeypatch.setattr(ceremony_tool, "_door_open", lambda: True)
    _player(monkeypatch, {"ok": True, "mode": "say_yourself",
                          "line": "ฉันทำอะไรได้หลายอย่างเลยค่ะ คุณมิกิลองถามมาได้เลยค่ะ"})
    out = _call(ceremony_tool, "s1_can_do")
    assert out["mode"] == "say_yourself"
    assert out["instruction"].endswith("«ฉันทำอะไรได้หลายอย่างเลยค่ะ คุณมิกิลองถามมาได้เลยค่ะ»")
    assert "ตามตัวอักษร" in out["instruction"] and "รวมคำถามที่อยู่ในบท" in out["instruction"]


def test_a_live_cue_hands_over_its_brief_and_plays_nothing(ceremony_tool, monkeypatch):
    monkeypatch.setattr(ceremony_tool, "_door_open", lambda: True)
    calls = _player(monkeypatch, {"ok": True, "mode": "recorded"})
    out = _call(ceremony_tool, "s4_recommend")
    assert calls == [] and out["mode"] == "live"
    assert event_script.cue("s4_recommend", SCRIPT)["live_th"] in out["instruction"]   # its own brief, as written


def test_the_tool_refuses_what_it_must(ceremony_tool, monkeypatch):
    calls = _player(monkeypatch, {"ok": True, "mode": "recorded"})
    monkeypatch.setattr(ceremony_tool, "_door_open", lambda: True)
    out = _call(ceremony_tool, "s9_thanks")                # a draft
    assert out["error"] == "unknown_cue" and "s9_thanks" not in out["known"]
    assert _call(ceremony_tool, "made_up")["error"] == "unknown_cue"
    monkeypatch.setattr(ceremony_tool, "_door_open", lambda: False)
    assert _call(ceremony_tool, "s1_can_do")["reason"] == "not_named"
    monkeypatch.setattr(ceremony_tool, "_door_open", lambda: True)
    ceremony_off()
    assert _call(ceremony_tool, "s1_can_do")["reason"] == "not_ceremony"
    assert calls == []                                     # none of these played anything


def test_a_failed_or_missing_player(ceremony_tool, monkeypatch):
    monkeypatch.setattr(ceremony_tool, "_door_open", lambda: True)
    _player(monkeypatch, {"ok": False, "error": "no_recording_device"})
    out = _call(ceremony_tool, "s1_can_do")
    assert out["ok"] is False and "ห้ามบอกว่าระบบมีปัญหา" in out["instruction"]
    # a line already being said (this one or another): answering herself is a second voice
    for error in ("already", "busy"):
        _player(monkeypatch, {"ok": False, "error": error})
        out = _call(ceremony_tool, "s1_can_do")
        assert out["error"] == error and "'ค่ะ' คำเดียว" in out["instruction"]
        assert "ตอบคำถามของพิธีกรเอง" not in out["instruction"]
    _player(monkeypatch, RuntimeError("boom"))
    assert _call(ceremony_tool, "s1_can_do")["error"] == "play_failed"
    # app/event_cues.py not in the tree yet: what a press with no recording does
    import sys

    import app

    monkeypatch.setitem(sys.modules, "app.event_cues", None)   # import -> ImportError
    monkeypatch.delattr(app, "event_cues", raising=False)
    out = _call(ceremony_tool, "s1_can_do")
    assert out["mode"] == "say_yourself" and "«ฉันทำอะไรได้หลายอย่างเลยค่ะ" in out["instruction"]


def test_the_door_wants_her_name_or_a_named_reply(ceremony_tool, monkeypatch):
    from app import heard
    from app import session as session_module

    room = object()
    heard.bind_session(room)
    try:
        monkeypatch.setattr(session_module, "_active", None)
        heard.mark_activity(room)
        heard.record("มีห้องทั้งหมดกี่ยูนิตครับท่าน", room)
        assert ceremony_tool._door_open() is False        # to the seats, no session to ask: closed
        gate = SimpleNamespace(named_recently=lambda: True, _heard=None)
        monkeypatch.setattr(session_module, "_active", SimpleNamespace(provider=SimpleNamespace(_noise_gate=gate)))
        assert ceremony_tool._door_open() is True         # she was named a moment ago
        # the real gate: named in what the host said since her last reply, though applause
        # opened a segment after the question (the caption is stale, the gate still knows)
        from app.providers.transcript_filter import NoiseReplyGate

        real = NoiseReplyGate(0.06, named_only=lambda: True)
        real.segment(0.2)
        real.heard("เอ็มม่า มีห้องทั้งหมดกี่ยูนิต")
        real.segment(0.05)                                  # applause, joins the same "heard"
        monkeypatch.setattr(session_module, "_active", SimpleNamespace(provider=SimpleNamespace(_noise_gate=real)))
        heard.mark_activity(room)                           # the caption went stale
        assert ceremony_tool._door_open() is True
        unnamed = NoiseReplyGate(0.06, named_only=lambda: True)
        unnamed.segment(0.2)
        unnamed.heard("มีห้องทั้งหมดกี่ยูนิตครับท่าน")
        monkeypatch.setattr(session_module, "_active", SimpleNamespace(provider=SimpleNamespace(_noise_gate=unnamed)))
        assert ceremony_tool._door_open() is False
        monkeypatch.setattr(session_module, "_active", None)
        heard.mark_activity(room)
        heard.record("เอ็มม่า มีห้องทั้งหมดกี่ยูนิต", room)
        assert ceremony_tool._door_open() is True         # named in these words
    finally:
        heard.forget(room)
        heard.bind_session(None)


def _as_drafts(doc: dict) -> dict:
    """The script with every approval taken off: what a test of "a draft is never served" needs,
    whatever the real file's state (the owner approved 29 cues on 12 Nov)."""
    import copy

    doc = copy.deepcopy(doc)
    for cue in doc.get("cues") or []:
        cue.update(approved_by=None, approved_at=None, approved_sha256=None, approved_voice_sha256=None)
    return doc


# ---- the measurement -----------------------------------------------------------------------

def _harness():
    spec = importlib.util.spec_from_file_location("eval_live_qa_ceremony", ROOT / "scripts" / "eval_live_qa.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_real_cases_file_validates_against_the_real_script():
    mod = _harness()
    data = json.loads(CASES.read_text(encoding="utf-8"))
    script = event_script.load(REAL_SCRIPT)
    assert mod.validate_ceremony_cases(data, script) == []
    lined = {c["id"] for c in event_script.cues(script) if c["th"]}
    assert {c["cue"] for c in data["cases"] if c["cue"]} == lined   # every line is measured
    assert sum(1 for c in data["cases"] if c["variant"] == "off_script") >= mod.CEREMONY_MIN_OFF_SCRIPT


def _case(data, case_id):
    return next(c for c in data["cases"] if c["id"] == case_id)


@pytest.mark.parametrize("breakage, problem", [
    (lambda d: _case(d, "K-s04_l03-v").update(cue="s05_l07"), "has no line"),     # waiting for its room
    (lambda d: _case(d, "K-s04_l03-v").update(cue="nope"), "not in the script"),
    (lambda d: _case(d, "K-s04_l03-v").update(host="อะไรก็ได้"), "verbatim host line differs"),
    (lambda d: _case(d, "K-s02_l05-p1").update(variant="verbatim"), "stage direction"),
    (lambda d: d["cases"].remove(_case(d, "K-s04_l03-p2")), "needs at least 2 paraphrases"),
    (lambda d: d["cases"][-1].update(cue="s04_l03"), "expects no cue"),
    (lambda d: _case(d, "K-s04_l09-v").update(before="s02_l05"), "before"),     # a stage-direction cue
    (lambda d: _case(d, "K-s04_l09-v").update(before=["s04_l06", "s04_l09"]), "before 's04_l09'"),
    (lambda d: _case(d, "K-s04_l09-v").update(before=7), "a cue id or a list"),
    (lambda d: d["cases"][0].update(extra=1), "unknown key"),
    (lambda d: d.update(format="x"), "format"),
])
def test_the_check_refuses_a_file_that_would_measure_nothing(breakage, problem):
    mod = _harness()
    data = copy.deepcopy(json.loads(CASES.read_text(encoding="utf-8")))
    breakage(data)
    problems = mod.validate_ceremony_cases(data, event_script.load(REAL_SCRIPT))
    assert any(problem in p for p in problems), problems


def test_check_passes_on_the_real_file_and_fails_on_a_broken_one(tmp_path, monkeypatch, capsys):
    mod = _harness()
    # conftest hides this machine's report, so the report-sourced numbers cannot be checked
    # here (tests/test_event_script.py checks them where the report is): rules only
    monkeypatch.setattr(event_script, "number_problems", lambda c, lookup=None, speak="th": [])
    monkeypatch.setattr(settings, "event_file", "")
    monkeypatch.setattr(settings, "ceremony_file", "")       # --check finds the tracked one itself
    args = SimpleNamespace(suite="ceremony", faq=str(tmp_path / "none.md"), profile=None,
                           ceremony_mode="tool", cue_answer="say_yourself")
    try:
        assert mod.check_offline(args) == 0
        assert "84 case(s) in suite ceremony load and validate; profile=ceremony" in capsys.readouterr().out
        args.ceremony_mode = "read"
        assert mod.check_offline(args) == 0
        assert "77 case(s)" in capsys.readouterr().out                # the 7 off-script lines are no press
        broken = json.loads(CASES.read_text(encoding="utf-8"))
        broken["cases"][3]["cue"] = "nope"
        path = tmp_path / "broken.json"
        path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(mod, "CEREMONY_SCENARIOS", path)
        assert mod.check_offline(args) == 1
        # a line the content rules refuse is a cue the session never sees: refused too
        monkeypatch.setattr(mod, "CEREMONY_SCENARIOS", CASES)
        monkeypatch.setattr(event_script, "problems", lambda c, lookup=None, speak="th": ["no_info_th"])
        assert mod.check_offline(args) == 1
        assert "not servable even as a draft" in capsys.readouterr().err
    finally:
        event_host.reset()
        event_script.reset()


def test_the_modes_build_their_turns():
    mod = _harness()
    data = json.loads(CASES.read_text(encoding="utf-8"))
    script = event_script.load(REAL_SCRIPT)
    host = {c["id"]: c["host_th"] for c in event_script.cues(script)}
    read = {c["id"]: c for c in mod.ceremony_cases(data, script, "read")}
    free = {c["id"]: c for c in mod.ceremony_cases(data, script, "free")}
    assert "K-x-staff" not in read and "K-x-staff" in free   # a press needs a cue
    assert read["K-s03_l03-v"]["turns"] == ["cue:s03_l03"]
    assert free["K-s03_l03-p1"]["turns"] == ["เอ็มม่า โครงการ Embassy World ตั้งอยู่ตรงไหนครับ"]
    # the unit question follows the location answer that names her on stage (its window)
    assert read["K-s04_l03-v"]["turns"] == ["cue:s03_l03", "cue:s04_l03"]
    # the punchline comes after the lines before it, in the script's order; only it is scored
    joke = free["K-s04_l11-p1"]
    assert joke["turns"][:2] == [host["s04_l06"], host["s04_l09"]] and joke["score_turn"] == 2
    assert read["K-s04_l11-p1"]["turns"] == ["cue:s04_l06", "cue:s04_l09", "cue:s04_l11"]
    # from the script, in the language it is SAID in (17 Nov: the real file's "speak" is "en")
    assert free["K-s04_l03-v"]["line"] == event_script.cue("s04_l03", script)[event_script.speak(script)]
    # no scene press ahead of a case since 9 Nov: the ceremony app's call is the ceremony
    assert not [c for c in free.values() if any(t.startswith("server:scene:") for t in c["turns"])]
    assert "opening" not in free["K-s04_l03-p1"]
    assert mod.resolve_profile("ceremony", None) == "ceremony"
    with pytest.raises(ValueError):
        mod.ceremony_cases(data, script, "nope")


def test_which_answers_the_room_would_hear():
    mod = _harness()
    data = json.loads(CASES.read_text(encoding="utf-8"))
    script = event_script.load(REAL_SCRIPT)
    assert mod.robot_hears("read", ["ไม่เรียกชื่อ"]) is True             # a staff press is never judged
    assert mod.robot_hears("free", ["มีกี่ยูนิตครับ"]) is False           # the door drops it
    assert mod.robot_hears("free", ["เอ็มม่า มีกี่ยูนิตครับ"]) is True
    assert mod.robot_hears("tool", ["เอ็มม่า มีกี่ยูนิต", "แล้วส่วนกลางล่ะ", "ว้าว ฉลาดมาก"]) is True   # the window
    assert mod.robot_hears("free", ["แล้วส่วนกลางล่ะ", "ว้าว ฉลาดมาก"]) is False
    free = {c["id"]: c for c in mod.ceremony_cases(data, script, "free")}
    assert free["K-s05_l06-v"]["robot_hears"] is True           # unnamed, right after a named s05_l03
    assert free["K-s04_l09-v"]["robot_hears"] is True
    # unnamed, and what names her just before on stage ("เอ็มม่า เปิดหน้าถัดไปครับ") is no cue a
    # `before` chain can replay: the suite scores it unheard
    assert free["K-s07_l07-v"]["robot_hears"] is False
    assert free["K-x-staff"]["robot_hears"] is False and free["K-x-applause"]["robot_hears"] is True
    assert all(c["robot_hears"] for c in mod.ceremony_cases(data, script, "read"))


def test_the_film_cue_is_measured_without_its_film(monkeypatch, tmp_path):
    """A cue that starts a film not on this disk (event_script `clip_missing`): the suite takes
    the event's films as present in its own process only -- it measures her words. The 12 Nov
    script plays its films from the show's LED (no cue carries a `clip`), so the real script with
    its Story line given the event's film stands in for the 6 Nov one that had it."""
    mod = _harness()
    doc = json.loads(REAL_SCRIPT.read_text(encoding="utf-8"))
    next(c for c in doc["cues"] if c["id"] == "s09_l02")["clip"] = "embassy_world_story"
    script_file = tmp_path / "floor1_ceremony.json"
    script_file.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "event_file", str(EVENT))
    monkeypatch.setattr(settings, "ceremony_file", str(script_file))
    monkeypatch.setattr(event_script, "number_problems", lambda c, lookup=None, speak="th": [])
    event_script.reset()
    try:
        from app import event_clips

        cue = event_script.cue("s09_l02", event_script.load(script_file))
        if event_clips.table().get(cue["clip"], {}).get("has_file"):
            pytest.skip("NOT CHECKED: the film is on this disk, so the stand-in is not exercised")
        real = event_clips.table
        with mod.script_drafts_in_memory():
            assert "s09_l02" not in event_script.servable(event_script.load(script_file))
            with mod.clips_present_in_memory():
                assert "s09_l02" in event_script.servable(event_script.load(script_file))
        assert event_clips.table is real
    finally:
        event_script.reset()


def test_drafts_are_served_only_inside_the_harness_patch():
    mod = _harness()
    real = event_script.servable
    with mod.script_drafts_in_memory():
        assert event_script.servable is not real
        assert set(event_script.servable(_as_drafts(event_script.load(REAL_SCRIPT)))) >= {"s04_l03"}
    assert event_script.servable is real
    assert event_script.servable(_as_drafts(event_script.load(REAL_SCRIPT))) == {}
    for path in (ROOT / "app").rglob("*.py"):              # and never under app/
        text = path.read_text(encoding="utf-8")
        if path.name == "event_script.py":
            text = text.split("def servable", 1)[0]
        assert not re.search(r"drafts\s*=\s*True", text), path


def _turn(said="", tools=(), timeline=((0.4, "tool_call"), (1.6, "audio")), complete=True, audio_s=None):
    if audio_s is None:
        audio_s = 2.0 if said else 0.0
    return {"said": said, "tools": list(tools), "timeline": [list(t) for t in timeline],
            "complete": complete, "audio_s": audio_s}


def test_the_score_is_what_the_room_hears(monkeypatch):
    mod = _harness()
    line = "โครงการของเรามีทั้งหมด 1,066 ยูนิตค่ะ"
    monkeypatch.setattr(event_script, "servable", lambda doc=None, *, drafts=False: {
        "s3_units": {"id": "s3_units", "th": line}, "s9_thanks": {"id": "s9_thanks", "th": "ขอขอบคุณทุกคนค่ะ"}})
    case = {"ceremony_mode": "tool", "cue_answer": "recorded", "cue": "s3_units", "line": line,
            "host": "มีกี่ยูนิตครับ", "variant": "paraphrase"}
    played = [{"cue_play": "s3_units", "mode": "recorded", "line": line},
              {"name": "say_script_line", "args": {"cue": "s3_units"}, "result": {"ok": True}}]
    quiet = mod.ceremony_score(case, [_turn("", played, timeline=((0.9, "tool_call"),))])
    assert quiet["fidelity"] == 1.0 and quiet["right_cue"] and quiet["extra_after_recorded_chars"] == 0
    assert quiet["first_sound_s"] == 0.9 and quiet["named"] is False and not quiet["silent"]
    assert quiet["scorer"] == "recording" and quiet["extra_after_recorded_s"] == 0
    # her voice over her recording with no transcript at all: still a second voice
    blind = mod.ceremony_score(case, [_turn("", played, audio_s=3.1)])
    assert blind["extra_after_recorded_s"] == 3.1 and blind["no_transcript"] and blind["scorer"] is None
    # she said it again over her own recording: a second voice, and a number nobody approved
    twice = mod.ceremony_score(case, [_turn("มี 1,066 ยูนิต และ 500 ห้องค่ะ", played)])
    assert twice["extra_after_recorded_chars"] > 0 and twice["fidelity"] < 1.0
    assert twice["numbers"]["extra"] == [1066.0, 500.0]   # the line's own number, said again, and 500
    assert twice["unapproved_numbers"] == [500.0] and twice["extra_numbers"] == 1
    wrong = mod.ceremony_score(case, [_turn("", [dict(played[1], args={"cue": "s9_thanks"})])])
    assert wrong["right_cue"] is False and wrong["silent"]
    # free mode: her own words, scored against the line
    free = mod.ceremony_score(dict(case, ceremony_mode="free", cue_answer=None),
                              [_turn("โครงการของเรามีทั้งหมด 1066 ยูนิตค่ะ")])
    assert free["fidelity"] == 1.0 and "right_cue" not in free and free["best_cue"] == "s3_units"
    # an off-script line answered with a script line is a misfire
    off = mod.ceremony_score({"ceremony_mode": "free", "cue": None, "line": None, "host": "ขอบคุณทีมงานครับ"},
                             [_turn("ขอขอบคุณทุกคนค่ะ")])
    assert off["misfire"] is True and off["best_cue"] == "s9_thanks"


def test_a_missing_transcript_is_read_by_the_recogniser_never_scored_zero(monkeypatch):
    mod = _harness()
    line = "สระว่ายน้ำยาว 155 เมตรค่ะ"
    monkeypatch.setattr(event_script, "servable", lambda doc=None, *, drafts=False: {
        "s3_facility": {"id": "s3_facility", "th": line}})
    case = {"ceremony_mode": "read", "cue": "s3_facility", "line": line, "host": "", "variant": "verbatim"}
    # she spoke, Live sent no transcript, nothing read it: unscored, not a 0
    blind = mod.ceremony_score(case, [_turn("", audio_s=4.0)])
    assert blind["no_transcript"] and blind["scorer"] is None and blind["fidelity"] is None
    assert not blind["silent"]
    # the recogniser read it: scored against ITS reading of the approved clip
    asr = {"text": "สระว่ายน้ำยาว๑๕๕เมตรค่ะ", "reference": "สระว่ายน้ำยาว๑๕๕เมตรค่ะ", "reference_from": "clip"}
    read = mod.ceremony_score(case, [_turn("", audio_s=4.0)], asr=asr)
    assert read["scorer"] == "asr" and read["fidelity"] == 1.0 and read["missing_numbers"] == []
    # no clip here: the written line, both sides normalised (Thai digits, ำ written ํ+า)
    asr = {"text": "สระว่ายน้ํายาว๑๕๕เมตรค่ะ", "reference": None, "reference_from": "line"}
    assert mod.ceremony_score(case, [_turn("", audio_s=4.0)], asr=asr)["fidelity"] == 1.0
    # numbers in ASR space count against the LINE only (review 6 Nov): the recogniser writes 1.3
    # as ๑๓ on the approved clip too -- that one is listed as noise, still counted, so a real
    # "13 เมตร" can never pass as the clip's misreading
    deep = "สระหลักลึกประมาณ 1.3 เมตรค่ะ"
    deep_case = dict(case, cue="s5_deep", line=deep)
    asr = {"text": "สระหลักลึกประมาณ๑๓เมตรค่ะ", "reference": "สระหลักลึกประมาณ๑๓เมตรค่ะ"}
    read = mod.ceremony_score(deep_case, [_turn("", audio_s=3.0)], asr=asr)
    assert read["unapproved_numbers"] == [13.0] and read["asr_number_noise"] == [13.0]
    assert read["missing_numbers"] == [] and read["numbers"]["from"] == "clip_reading"
    asr = {"text": "สระหลักลึกประมาณ๑๓เมตรและสูง๕๐เมตรค่ะ", "reference": "สระหลักลึกประมาณ๑๓เมตรค่ะ"}
    more = mod.ceremony_score(deep_case, [_turn("", audio_s=3.0)], asr=asr)
    assert more["unapproved_numbers"] == [13.0, 50.0] and more["asr_number_noise"] == [13.0]
    # a transcript-scored turn never has the field
    assert "asr_number_noise" not in mod.ceremony_score(deep_case, [_turn(deep)])
    # a transcript that came wins over the recogniser, which is kept beside it
    both = mod.ceremony_score(case, [_turn(line)], asr={"text": "สระยาว๑๕๕เมตร", "reference": line})
    assert both["scorer"] == "transcript" and both["fidelity"] == 1.0 and both["asr_fidelity"] < 1.0
    # nothing at all is silence, and a silence is a 0
    quiet = mod.ceremony_score(case, [_turn("")])
    assert quiet["scorer"] == "silence" and quiet["silent"] and quiet["fidelity"] == 0.0


def test_run_case_reads_a_turn_live_sent_no_transcript_for(monkeypatch):
    """The real run_case, a fake Live session that answers the press with audio and no
    transcript (6 Nov: ~1 turn in 3): the audio reaches the recogniser, the score says
    "asr", and no audio is written to the record."""
    mod = _harness()
    from app.providers import gemini
    from app.providers.base import ProviderEvent

    sent = []

    class FakeLive:
        output_sample_rate = 24000
        model = "fake-live"

        def __init__(self, *a, **k):
            self.queue = asyncio.Queue()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def send_text(self, text):
            sent.append(text)
            await self.queue.put(ProviderEvent(kind="audio", audio=b"\x01\x00" * 48000))   # 2 s
            await self.queue.put(ProviderEvent(kind="turn_complete"))

        async def events(self):
            while True:
                yield await self.queue.get()

    monkeypatch.setattr(gemini, "GeminiProvider", FakeLive)
    monkeypatch.setattr(settings, "event_file", str(EVENT))
    monkeypatch.setattr(settings, "ceremony_file", str(REAL_SCRIPT))
    monkeypatch.setattr(event_script, "problems", lambda c, lookup=None, speak="th": [])
    heard_audio = []
    monkeypatch.setattr(mod, "_transcribe", lambda pcm, rate: heard_audio.append((len(pcm), rate)) or "อ่านได้")
    monkeypatch.setattr(mod, "_clip_reading", lambda cue: "อ่านได้")
    monkeypatch.setitem(mod.CEREMONY_ASR, "mode", "all")
    data = json.loads(CASES.read_text(encoding="utf-8"))
    script = event_script.load(REAL_SCRIPT)
    case = next(c for c in mod.ceremony_cases(data, script, "read") if c["id"] == "K-s03_l03-v")
    event_script.reset()
    try:
        with mod.script_drafts_in_memory():
            record = asyncio.run(mod.run_case(case, greeting=False, robot_face=True, turn_timeout=20,
                                              screen=False, profile="ceremony"))
    finally:
        event_host.reset()
        event_script.reset()
    assert record.get("error") is None, record.get("error")
    # the read turn in the language the real file is SAID in (17 Nov: "speak": "en" -- the
    # English rule round the English line; the Thai line is the screen's subtitle)
    assert sent and sent[0].startswith("[Ceremony cue:") and "«Of course. Embassy World is in the Jomtien Second Road area" in sent[0]
    assert not any("฀" <= ch <= "๿" for ch in sent[0])
    assert heard_audio == [(96000, 24000)]
    score = record["ceremony"]
    assert score["no_transcript"] and score["scorer"] == "asr" and score["fidelity"] == 1.0
    assert "_pcm" not in record["turns"][0]
    json.dumps(record, ensure_ascii=False)                  # nothing binary in the record


def _fake_live(on_send):
    """A GeminiProvider stand-in: `on_send(self, text)` (async) puts the session's answer."""
    from app.providers.base import ProviderEvent

    class FakeLive:
        output_sample_rate = 24000
        model = "fake-live"
        instructions_seen: list = []

        def __init__(self, voice, instructions, **kwargs):
            self.queue = asyncio.Queue()
            self.answered = 0
            self._replied_since_end = False
            FakeLive.instructions_seen.append(instructions)

        def _cue_answered(self):
            self.answered += 1

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def send_text(self, text):
            await on_send(self, text, ProviderEvent)

        async def events(self):
            while True:
                yield await self.queue.get()

    return FakeLive


def test_the_robots_order_the_ceremony_apps_call_then_the_case(monkeypatch):
    """Free mode as the robot reaches it since 9 Nov: the ceremony app's call claims the mode
    before its prompt is built (the "named" ceremony: the suite measures her live answer), the
    ceremony's block with the script is in it, and the case's first turn is the host's line --
    no scene press ahead of it any more."""
    mod = _harness()
    from app import prompts
    from app.providers import gemini

    sent, states = [], []

    async def on_send(self, text, Event):
        sent.append(text)
        await self.queue.put(Event(kind="assistant_transcript", text="ยินดีต้อนรับค่ะ"))
        await self.queue.put(Event(kind="audio", audio=b"\x01\x00" * 24000))
        await self.queue.put(Event(kind="turn_complete"))

    real_build = prompts.build_instructions

    def build(*a, **k):
        states.append(event_host.ceremony_now())
        return real_build(*a, **k)

    FakeLive = _fake_live(on_send)
    monkeypatch.setattr(gemini, "GeminiProvider", FakeLive)
    monkeypatch.setattr(prompts, "build_instructions", build)
    monkeypatch.setattr(settings, "event_file", str(EVENT))
    monkeypatch.setattr(settings, "ceremony_file", str(REAL_SCRIPT))
    monkeypatch.setattr(settings, "event_script_prompt", True)
    monkeypatch.setattr(event_script, "problems", lambda c, lookup=None, speak="th": [])
    monkeypatch.setattr(mod, "CEREMONY_ASR", {"mode": "off"})
    event_host.reset()
    event_script.reset()
    data = json.loads(CASES.read_text(encoding="utf-8"))
    script = event_script.load(REAL_SCRIPT)
    case = next(c for c in mod.ceremony_cases(data, script, "free") if c["id"] == "K-s03_l03-v")   # names her
    try:
        with mod.script_drafts_in_memory(), mod.clips_present_in_memory():
            record = asyncio.run(mod.run_case(case, greeting=False, robot_face=True, turn_timeout=20,
                                              screen=False, profile=mod.resolve_profile("ceremony", None)))
            running = event_host.ceremony_now()
            cue_only = event_host.cue_only()
    finally:
        event_host.reset()
        event_script.reset()
    assert record.get("error") is None, record.get("error")
    # built with the ceremony on (ceremony builds condo inside itself: one call per profile)
    assert states and all(states)
    assert event_host.SCRIPT_HEAD in FakeLive.instructions_seen[-1]
    assert "[พิธีเปิดงาน — แอป Emma Ceremony]" in FakeLive.instructions_seen[-1]
    assert "[งานอีเวนต์" not in FakeLive.instructions_seen[-1]
    assert sent[0] == case["turns"][0] and running is True and cue_only is False   # "named", as measured
    assert record["ceremony"]["robot_hears"] is True and record["ceremony"]["named"] is True


def test_a_recorded_cue_holds_her_words_and_answers_the_watchdog(monkeypatch):
    """The stub's "recorded" mirrors the provider: what she says while her line plays is held
    (`held_after_recorded`, not the room's), the reply watchdog is answered, and the host's
    next line waits for the line to end."""
    mod = _harness()
    import sys
    import types

    import app
    from app.providers import gemini

    fake_cues = types.ModuleType("app.event_cues")
    monkeypatch.setitem(sys.modules, "app.event_cues", fake_cues)
    monkeypatch.setattr(app, "event_cues", fake_cues, raising=False)
    mod.stub_cue_player("recorded")
    # longer than the harness's own 2.5 s quiet wait, or a host who never waits passes too
    monkeypatch.setattr(mod, "_recorded_seconds", lambda cue: 3.5)
    sends, played, answered = [], [], []

    async def on_send(self, text, Event):
        sends.append(time.monotonic())
        cue = "s04_l06" if "สิ่งอำนวยความสะดวก" in text else "s04_l09"
        await self.queue.put(Event(kind="tool_call"))
        result = await fake_cues.play(cue, by="model")       # what say_script_line does
        played.append(time.monotonic())
        answered.append((self.answered, self._replied_since_end))
        log = mod._case_log.get()
        log.append({"name": "say_script_line", "args": {"cue": cue}, "result": result})
        await self.queue.put(Event(kind="tool_result"))
        # she says it again under her own recording: the provider holds it
        await self.queue.put(Event(kind="assistant_transcript", text="โครงการของเรามีทั้งหมด"))
        await self.queue.put(Event(kind="audio", audio=b"\x01\x00" * 24000))
        await self.queue.put(Event(kind="turn_complete"))

    monkeypatch.setattr(gemini, "GeminiProvider", _fake_live(on_send))
    monkeypatch.setattr(settings, "event_file", str(EVENT))
    monkeypatch.setattr(settings, "ceremony_file", str(REAL_SCRIPT))
    monkeypatch.setattr(event_script, "problems", lambda c, lookup=None, speak="th": [])
    monkeypatch.setattr(mod, "CEREMONY_ASR", {"mode": "off"})
    event_host.reset()
    event_script.reset()
    data = json.loads(CASES.read_text(encoding="utf-8"))
    script = event_script.load(REAL_SCRIPT)
    case = next(c for c in mod.ceremony_cases(data, script, "tool", "recorded") if c["id"] == "K-s04_l09-v")
    try:
        with mod.script_drafts_in_memory():
            record = asyncio.run(mod.run_case(case, greeting=False, robot_face=True, turn_timeout=20,
                                              screen=False, profile="ceremony"))
    finally:
        event_host.reset()
        event_script.reset()
    assert record.get("error") is None, record.get("error")
    first, last = record["turns"]
    assert first["said"] == "" and first["audio_s"] == 0.0           # the room heard only the recording
    assert first["held_said"] == "โครงการของเรามีทั้งหมด" and first["held_audio_s"] == 1.0
    assert sends[1] >= played[0] + 3.4                               # the host waited for the line
    assert answered == [(1, True), (2, True)]                         # the watchdog answered each time
    score = record["ceremony"]
    assert score["scorer"] == "recording" and score["fidelity"] == 1.0 and score["right_cue"]
    assert score["extra_after_recorded_chars"] == 0 and score["extra_after_recorded_s"] == 0
    assert score["held_after_recorded"] == {"said": "โครงการของเรามีทั้งหมด", "audio_s": 1.0}
    assert "t1:silent" not in record["flags"] and "t0:silent" not in record["flags"]


def test_quote_marks_and_a_number_in_words_are_the_line_said_right(monkeypatch):
    mod = _harness()
    line = "สระว่ายน้ำยาว 155 เมตรค่ะ"
    monkeypatch.setattr(event_script, "servable", lambda doc=None, *, drafts=False: {
        "s3_facility": {"id": "s3_facility", "th": line}})
    case = {"ceremony_mode": "read", "cue": "s3_facility", "line": line, "host": "", "variant": "verbatim"}
    quoted = mod.ceremony_score(case, [_turn("«สระว่ายน้ำยาว 155 เมตรค่ะ»")])
    assert quoted["fidelity"] == 1.0 and quoted["said"] == line
    words = mod.ceremony_score(case, [_turn("สระว่ายน้ำยาวหนึ่งร้อยห้าสิบห้าเมตรค่ะ")])
    assert words["fidelity"] == 1.0 and words["missing_numbers"] == []
    dropped = mod.ceremony_score(case, [_turn("สระว่ายน้ำยาวเมตรค่ะ")])
    assert dropped["missing_numbers"] == [155.0]


def test_the_recogniser_runs_as_asked(monkeypatch):
    mod = _harness()
    seen = []
    monkeypatch.setattr(mod, "_transcribe", lambda pcm, rate: seen.append(len(pcm)) or "ข้อความ")
    monkeypatch.setattr(mod, "_clip_reading", lambda cue: "อ้างอิง")
    case = {"cue": "s3_units", "line": "x"}
    voice = b"\x01\x00" * 24000                           # 1 s at 24 kHz
    monkeypatch.setitem(mod.CEREMONY_ASR, "mode", "all")
    assert mod.ceremony_asr(case, {"said": "มีคำ"}, voice, 24000)["reference"] == "อ้างอิง"
    assert mod.ceremony_asr(case, {"said": ""}, b"\x01\x00" * 100, 24000) is None   # a click
    monkeypatch.setitem(mod.CEREMONY_ASR, "mode", "missing")
    assert mod.ceremony_asr(case, {"said": "มีคำ"}, voice, 24000) is None
    assert mod.ceremony_asr(case, {"said": ""}, voice, 24000)["text"] == "ข้อความ"
    monkeypatch.setitem(mod.CEREMONY_ASR, "mode", "off")
    assert mod.ceremony_asr(case, {"said": ""}, voice, 24000) is None
    assert len(seen) == 2
    monkeypatch.setitem(mod.CEREMONY_ASR, "mode", "all")
    monkeypatch.setattr(mod, "_transcribe", lambda pcm, rate: None)
    assert mod.ceremony_asr(case, {"said": ""}, voice, 24000) == {"error": "no_model"}
    assert mod.asr_norm("จํานวน ๑,๐๖๖ «ห้อง»") == mod.asr_norm("จำนวน 1,066 ห้อง")


def test_the_clips_answer_is_recorded_only_with_a_clip(monkeypatch):
    mod = _harness()
    from app import event_voice

    line = "ขอขอบคุณทุกคนค่ะ"
    monkeypatch.setattr(event_script, "servable", lambda doc=None, *, drafts=False: {
        "s9_thanks": {"id": "s9_thanks", "th": line}, "s1_can_do": {"id": "s1_can_do", "th": "ได้ค่ะ"}})
    monkeypatch.setattr(event_voice, "status", lambda c, lang="th", base=None: {
        "recorded": c["id"] == "s9_thanks"})
    import sys
    import types

    import app

    # a stand-in module: the stub must never leave the real event_cues.play replaced
    fake = types.ModuleType("app.event_cues")
    monkeypatch.setitem(sys.modules, "app.event_cues", fake)
    monkeypatch.setattr(app, "event_cues", fake, raising=False)
    mod.stub_cue_player("clips")
    log = []
    token = mod._case_log.set(log)
    try:
        assert asyncio.run(fake.play("s9_thanks", by="model")) == {"ok": True, "mode": "recorded"}
        assert asyncio.run(fake.play("s1_can_do", by="model"))["mode"] == "say_yourself"
        assert [e["mode"] for e in log] == ["recorded", "say_yourself"]
    finally:
        mod._case_log.reset(token)


def test_her_silence_over_her_recording_is_not_flagged():
    mod = _harness()
    played = [{"cue_play": "s3_units", "mode": "recorded", "line": "x"}]
    turns = [_turn("", played), _turn(""), _turn("", [{"cue_play": "s3_units", "mode": None, "line": None}])]
    flags = ["t0:silent", "t1:silent", "t2:silent", "t0:incomplete_turn"]
    assert mod.recorded_silence_is_right(turns, flags) == ["t1:silent", "t2:silent", "t0:incomplete_turn"]


def test_the_summary_table_per_mode(monkeypatch):
    mod = _harness()
    rows = [
        {"id": "a", "ceremony": {"mode": "read", "expected": "s3_units", "fidelity": 1.0, "first_sound_s": 1.2,
                                 "extra_numbers": 0, "silent": False, "failed": False, "scorer": "transcript",
                                 "asr_fidelity": 0.9}},
        {"id": "b", "ceremony": {"mode": "read", "expected": "s3_units", "fidelity": 0.8, "first_sound_s": 1.6,
                                 "extra_numbers": 1, "silent": False, "failed": False, "scorer": "asr",
                                 "no_transcript": True, "asr_fidelity": 0.8}},
        {"id": "b2", "ceremony": {"mode": "read", "expected": "s3_units", "fidelity": None, "first_sound_s": 1.0,
                                  "silent": False, "failed": False, "scorer": None, "no_transcript": True}},
        {"id": "c", "error": "upstream: quota", "ceremony": {"mode": "read", "expected": "s2_location",
                                                            "failed": True, "silent": True}},
        {"id": "d", "ceremony": {"mode": "tool", "cue_answer": "recorded", "expected": "s3_units",
                                 "fidelity": 1.0, "right_cue": True, "played_recorded": 1,
                                 "extra_after_recorded_chars": 12, "first_sound_s": 0.9, "named": True,
                                 "robot_hears": True, "held_after_recorded": {"said": "ค่ะ", "audio_s": 0.6},
                                 "silent": False, "failed": False}},
        {"id": "d2", "ceremony": {"mode": "tool", "cue_answer": "recorded", "expected": "s3_units",
                                  "fidelity": 1.0, "right_cue": False, "played_recorded": 1, "first_sound_s": 0.7,
                                  "named": False, "robot_hears": False, "silent": False, "failed": False}},
        {"id": "e", "ceremony": {"mode": "tool", "cue_answer": "recorded", "expected": None,
                                 "chosen": ["s9_thanks"], "misfire": True, "silent": False, "named": True,
                                 "robot_hears": True, "failed": False}},
        {"id": "e2", "ceremony": {"mode": "tool", "cue_answer": "recorded", "expected": None,
                                  "chosen": ["s9_thanks"], "misfire": True, "silent": False, "named": False,
                                  "robot_hears": False, "failed": False}},
        # free: a named line heard, an unnamed one heard after a named exchange, one unheard
        {"id": "f1", "ceremony": {"mode": "free", "expected": "s3_units", "fidelity": 1.0, "named": True,
                                  "robot_hears": True, "first_sound_s": 0.8, "silent": False, "failed": False,
                                  "scorer": "transcript"}},
        {"id": "f2", "ceremony": {"mode": "free", "expected": "s3_facility", "fidelity": 0.9, "named": False,
                                  "robot_hears": True, "first_sound_s": 0.8, "silent": False, "failed": False,
                                  "scorer": "transcript"}},
        {"id": "f3", "ceremony": {"mode": "free", "expected": "s9_thanks", "fidelity": 0.2, "named": False,
                                  "robot_hears": False, "first_sound_s": 0.8, "silent": False, "failed": False,
                                  "scorer": "transcript"}},
    ]
    summary = mod.ceremony_summary(rows)
    assert set(summary) == {"read", "tool/recorded", "free"}
    read = summary["read"]
    assert read["cases"] == 4 and read["failed"] == 1 and read["fidelity_median"] == 0.9
    assert read["robot_hears"] == 4 and "by_named" not in read       # a staff press is always heard
    assert read["fidelity_ge_0_9_pct"] == 50.0 and read["extra_number_cases"] == 1
    # the unscored turn is counted, not averaged in as a 0
    assert read["no_transcript"] == 2 and read["unscored"] == 1
    assert read["scored_by"] == {"transcript": 1, "asr": 1, "None": 1}
    assert read["fidelity_median_transcript"] == 1.0 and read["asr_fidelity_median"] == 0.85
    # free: only what the room hears is scored; unheard apart; named/unnamed split
    free = summary["free"]
    assert free["cases"] == 3 and free["robot_hears"] == 2 and free["fidelity_median"] == 0.95
    assert free["unheard"]["cases"] == 1 and free["unheard"]["fidelity_median"] == 0.2
    assert free["by_named"]["named"]["cases"] == 1 and free["by_named"]["named"]["fidelity_median"] == 1.0
    assert free["by_named"]["unnamed"] == {"cases": 2, "robot_hears": 1, "fidelity_median": 0.9,
                                           "fidelity_cases": 1, "silent_pct": 0.0, "first_sound_median_s": 0.8}
    # tool/recorded: 1.0 by construction is no fidelity; the pick and the room's speech are
    tool = summary["tool/recorded"]
    assert tool["fidelity_median"] is None and tool["fidelity_cases"] == 0
    assert tool["cue_accuracy_pct"] == 100.0 and tool["recorded_with_extra_speech"] == 1
    assert tool["held_after_recorded"] == 1 and tool["unheard"]["cue_accuracy_pct"] == 0.0
    assert tool["off_script"] == {"cases": 2, "robot_hears": 1, "spoke": 1, "misfire": 1,
                                  "unsourced_number_cases": 0, "cue_called": 1,
                                  "unheard_spoke": 1, "unheard_cue_called": 1}
    table = mod.ceremony_table(summary)
    lines = table.splitlines()
    assert lines[0].startswith("mode") and " hear " in lines[0] and "sound_s" in lines[0]
    assert any(line.startswith("tool/recorded") and " 1:1/1 " in line for line in lines)
    assert any(line.strip().startswith("· unnamed") for line in lines)
    assert "end-of-speech wait" in table and "staff's reaction" in table   # what sound_s leaves out
