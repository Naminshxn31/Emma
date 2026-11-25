"""A motion for what the ceremony's script does not answer (CEREMONY_OFFSCRIPT_STORIES, owner 24 Nov 2026 ~18:10)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import ceremony_stories as cs, stories
from app.config import settings
from app.providers import gemini, transcript_filter as tf
from app.providers.base import ProviderEvent
from app.tools import registry, stories as tool
from app.tools.registry import session_profile


@pytest.fixture
def stage(monkeypatch):
    """The ceremony app's call with the switches on; the approved catalog as it is in the tree."""
    cs.reset()
    for key in ("ceremony_offscript_stories", "ceremony_script_show_only", "ceremony_name_first", "stories_enabled"):
        monkeypatch.setattr(settings, key, True)
    token = session_profile.set("ceremony")
    yield
    session_profile.reset(token)
    cs.reset()


def _doc():
    return json.loads(cs.FILE.read_text(encoding="utf-8"))


def test_the_list_is_approved_stories_only_and_none_the_script_answers(stage):
    doc = _doc()
    catalog = set(stories.available())
    if not catalog:
        pytest.skip("the story catalog is a draft in this tree -- the list cannot be checked against it")
    listed, dropped = set(doc["topics"]), set(doc["dropped"])
    assert not listed & dropped and listed | dropped == catalog, (catalog - listed - dropped, (listed | dropped) - catalog)
    assert all(str(why).strip() for why in doc["dropped"].values())
    # the script's own questions (and its sales / price talk) are never told a second way
    assert {"location_beach", "location_travel", "nearby_places", "unit_count", "room_types", "facilities_overview",
            "building_height", "masterplan_lagoon", "project_concept", "project_tech", "smart_home", "views_collections",
            "furniture", "parking"} <= dropped
    script = json.loads((cs.FILE.parent / "floor1_ceremony.json").read_text(encoding="utf-8"))
    shown = {st.get("show") for sc in script["order"] for st in sc["steps"] if st.get("show")}
    assert shown and not shown & listed, shown & listed
    for sid in listed:
        words = sum(len(line["en"].split()) for line in stories.available()[sid]["lines"])
        assert words <= 95, (sid, words)


def test_a_question_of_the_script_leads_to_none_of_the_stories(stage):
    """By the stories' own matcher: Khun Miki's questions are none of the offered stories' questions."""
    host = json.loads((cs.FILE.parent / "floor1_ceremony.host.json").read_text(encoding="utf-8"))["lines"]
    have = cs.topics()
    if not have:
        pytest.skip("the story catalog is a draft in this tree")
    found = {}
    for cue, row in host.items():
        for ask in row.get("ask_en") or []:
            got = stories.story_for_query(ask, have)
            if got is not None:
                found[ask] = got
    assert not found, found


def test_off_or_anywhere_else_the_tool_is_as_it_was(monkeypatch):
    cs.reset()
    entry = registry._REGISTRY["present_story"]
    monkeypatch.setattr(settings, "stories_enabled", True)
    monkeypatch.setattr(settings, "ceremony_script_show_only", True)
    monkeypatch.setattr(settings, "tool_groups", "knowledge,stories")
    token = session_profile.set("ceremony")
    face = registry.robot_face_session.set("x")
    try:
        assert not registry.allowed(entry)                                   # off: no story in the ceremony
        assert cs.on() is False and cs.offered() is False
        monkeypatch.setattr(settings, "ceremony_offscript_stories", True)
        assert cs.on() is False                                              # (her name first is what makes it safe)
        monkeypatch.setattr(settings, "ceremony_name_first", True)
        assert cs.on() is True
        monkeypatch.setattr(cs, "topics", lambda: {"pools_all": {}})
        assert registry.allowed(entry)                                       # every switch on: offered
        monkeypatch.setattr(cs, "topics", lambda: {})
        assert not registry.allowed(entry)                                   # nothing on the list is approved: not
        monkeypatch.setattr(cs, "topics", lambda: 1 / 0)
        assert not registry.allowed(entry)                                   # a broken catalog offers nothing
    finally:
        session_profile.reset(token)
        registry.robot_face_session.reset(face)
    # every other call: the catalog's own result and declaration
    monkeypatch.setattr(stories, "tool_result", lambda topic: {"ok": True, "topic": topic, "plain": True})
    token = session_profile.set("condo")
    try:
        assert tool.present_story("pools_all") == {"ok": True, "topic": "pools_all", "plain": True}
    finally:
        session_profile.reset(token)


def test_the_call_gets_the_lines_in_english_and_only_listed_topics(stage, monkeypatch):
    story = {"id": "pools_all", "lines": [{"th": "สระของเรา", "en": "Our pools."}, {"th": "สองสระ", "en": "There are two."}],
             "questions_en": ["Which pools are there?"]}
    monkeypatch.setattr(stories, "available", lambda catalog=None: {"pools_all": story, "room_types": dict(story, id="room_types")})
    assert set(cs.topics()) == {"pools_all"}
    description, parameters = tool._declare()
    assert parameters["properties"]["topic"]["enum"] == ["pools_all"] and "Which pools are there?" in description
    got = tool.present_story("pools_all")
    assert got["ok"] and got["say"] == ["Our pools.", "There are two."]
    assert "story" not in got and "screen" not in got                         # the picture is not in it
    for topic in ("room_types", "nope", None):
        assert tool.present_story(topic)["ok"] is False, topic
    data = cs.picture_event("pools_all")
    assert data["topic"] == "pools_all" and data["by"] == "offscript" and data["sub"]["text"] == "สระของเรา สองสระ"
    assert cs.picture_event("room_types") is None
    monkeypatch.setattr(settings, "ceremony_offscript_stories", False)
    assert cs.tool_result("pools_all")["ok"] is False


def test_the_prompt_lines_of_the_other_calls_stay_out_of_the_ceremony(stage, monkeypatch):
    from app import prompts
    from app.tools import knowledge

    monkeypatch.setattr(prompts, "_tool_visible", lambda name: True)
    assert prompts._stories_told() is False and prompts._stories_suffix() == "" and not knowledge._stories_here()
    token = session_profile.set("condo")
    try:
        assert prompts._stories_told() is True and prompts._stories_suffix()
    finally:
        session_profile.reset(token)


def test_a_search_for_a_storys_topic_is_handed_to_the_story(stage, monkeypatch):
    from app.tools import knowledge

    if not cs.topics():
        pytest.skip("the story catalog is a draft in this tree")
    got = knowledge._story_redirect("nearby restaurants")
    assert got is not None and got["story_topic"] in cs.topics() and "present_story(topic=" in got["instruction"]
    assert knowledge._story_redirect("where is our project located") is None            # the script's: searched as usual
    description, _parameters = knowledge._declare_search()
    assert description.startswith(knowledge.SEARCH_CEREMONY_FIRST)
    monkeypatch.setattr(settings, "ceremony_offscript_stories", False)
    assert knowledge._story_redirect("nearby restaurants") is None and knowledge._declare_search()[0] == knowledge.SEARCH_DESCRIPTION


def _gate(decision):
    gate = tf.NoiseReplyGate(0.06)
    gate.follow_hold = lambda: decision["hold"]
    gate._heard = {"rms": 0.03, "text": "Emma, where is the sauna?", "tool": True, "server": False}
    return gate


AUDIO = ProviderEvent(kind="audio", audio=b"\0" * 480)
PICTURE = ProviderEvent(kind="story_picture", data={"topic": "saunas_where"})


def test_the_picture_goes_out_with_her_reply_or_not_at_all(monkeypatch):
    may = [True]
    monkeypatch.setattr(tf, "_ceremony_answers_all", lambda: True)
    monkeypatch.setattr(tf, "_ceremony_answer_named", lambda: True)
    monkeypatch.setattr(tf, "_ceremony_may_answer", lambda text: may[0])
    # held while the server decides, then first, once
    decision = {"hold": "wait"}
    gate = _gate(decision)
    gate.hold_picture(PICTURE)
    assert gate.push(AUDIO) == []
    decision["hold"] = None
    assert gate.push(AUDIO) == [PICTURE, AUDIO, AUDIO] and gate.push(AUDIO) == [AUDIO]
    # his words were a line of the script: its picture is gone with her reply
    gate = _gate({"hold": "drop"})
    gate.hold_picture(PICTURE)
    assert gate.push(AUDIO) == [] and gate._picture is None
    # he did not call her / told, not asked
    may[0] = False
    gate = _gate({"hold": None})
    gate.hold_picture(PICTURE)
    gate.push(AUDIO)
    assert gate.close()[0] == [] and gate._picture is None
    # 3.8 ends the tool's own turn first: the picture waits for the turn that tells it
    may[0] = True
    gate = _gate({"hold": None})
    gate.hold_picture(PICTURE)
    assert gate.close() == ([], None) and gate._picture is PICTURE
    assert gate.push(AUDIO) == [PICTURE, AUDIO]
    # new words of his: the old answer's picture is nobody's
    gate = _gate({"hold": None})
    gate.hold_picture(PICTURE)
    gate._output = True
    gate.segment(0.03)
    assert gate._picture is None
    gate = _gate({"hold": None})
    gate.hold_picture(PICTURE)
    gate.text_turn()
    assert gate._picture is None


def test_only_the_ceremonys_result_is_held(stage, monkeypatch):
    monkeypatch.setattr(cs, "picture_event", lambda topic: {"topic": topic, "by": "offscript"})
    held = []
    provider = SimpleNamespace(_noise_gate=SimpleNamespace(hold_picture=held.append))
    gemini.GeminiProvider._hold_story(provider, {"ok": True, "topic": "pools_all", "say": []})
    assert [e.data["topic"] for e in held] == ["pools_all"] and held[0].kind == "story_picture"
    gemini.GeminiProvider._hold_story(provider, {"ok": True, "topic": "pools_all", "screen": "story", "story": {}})
    token = session_profile.set("condo")
    try:
        gemini.GeminiProvider._hold_story(provider, {"ok": True, "topic": "pools_all"})
    finally:
        session_profile.reset(token)
    assert len(held) == 1


def test_the_halls_thai_line_comes_down_with_her_voice(monkeypatch):
    from app import face_mirror, session as session_mod

    sent = []
    monkeypatch.setattr(face_mirror, "subtitle_end", lambda sub_id=None, cut=False: sent.append(sub_id))
    real_sleep, clock = asyncio.sleep, [0.0]

    async def sleep(s):
        clock[0] += s
        await real_sleep(0)

    me = SimpleNamespace(_she_is_audible=lambda now: now < 12.0)

    async def go():
        with monkeypatch.context() as m:
            m.setattr(session_mod.asyncio, "sleep", sleep)
            m.setattr(session_mod.time, "monotonic", lambda: clock[0])
            await session_mod.VoiceSession._story_subtitle_end(me, "story-pools_all")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(go())
    finally:
        loop.close()
    assert sent == ["story-pools_all"] and 14.0 <= clock[0] < 17.0


def test_robot_env_turns_it_on():
    env = (Path(__file__).resolve().parents[1] / "data" / "robot-server" / "robot.env").read_text(encoding="utf-8")
    assert "\nCEREMONY_OFFSCRIPT_STORIES=true" in env.replace("\r\n", "\n")
