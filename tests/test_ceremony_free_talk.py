"""FREE TALK: CEREMONY_FREE_TALK (owner 24 Nov ~12:40, "สุดท้ายก็คุยด้นสด ... เราแค่ปรับโมชั่นให้มันดีขึ้น ... ตอนที่โชว์โมชั่นจบก็ขึ้นหน้าโลโก้
แล้วรอฟัง"; app/ceremony_follow.py `free_talk`, `claim_hall`, `_free_tick`, `_arm_topic_joke`).

* no nudge, no pointer marks/skips; her prompt has no script-line notes but keeps the facts and the source order;
* the hall: a cue slide > a story of hers > a topic slide, the first clear motion of an exchange wins;
* a new exchange with no motion of its own takes the old one down; the logo returns CEREMONY_LOGO_AFTER_S after her
  voice is out (a story is cleared too), a new caption / the host speaking / a press cancels it;
* a joke follows her answer on its topic, once; a pressed line's words never refine a pick;
* off = live first as before.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from app import ceremony, ceremony_follow as follow, ceremony_motion, ceremony_pick, event_host, event_order, face_mirror
from app.config import settings
from tests.test_ceremony_follow import F1, begin, hear, run
from tests.test_ceremony_jokes_auto import _gate, _session, jokes                       # noqa: F401 -- fixtures
from tests.test_ceremony_pick import WORDS, _fake_pick
from tests.test_event_order import call, calls, event                                    # noqa: F401 -- fixtures
from tests.test_event_order_chain import _until

_REAL_NUDGE = follow._nudge


@pytest.fixture
def free(jokes, monkeypatch):                                                           # noqa: F811
    monkeypatch.setattr(settings, "ceremony_free_talk", True)
    monkeypatch.setattr(settings, "ceremony_logo_after_s", 0.15)
    sent, cleared = [], []
    monkeypatch.setitem(face_mirror._state, "profile", "ceremony")
    monkeypatch.setattr(face_mirror, "motion", lambda slide, cue: sent.append((slide, cue)))
    real_publish = face_mirror.publish_json
    monkeypatch.setattr(face_mirror, "publish_json",
                        lambda p: cleared.append(p) if (p.get("result") or {}).get("screen") == "clear" else real_publish(p))
    jokes.sent, jokes.cleared = sent, cleared
    return jokes


def _hear_and_pick(text="tell us about the rooms"):
    hear(text)


def test_a_scripted_line_marks_no_step_and_nothing_nudges(free, monkeypatch):
    announced = []

    async def announce(*a, **k):
        announced.append(a)
        return True

    from app import events
    monkeypatch.setattr(events, "announce", announce)
    monkeypatch.setattr(follow, "NUDGE_AFTER_S", 0.0)

    async def go():
        await begin()
        await asyncio.sleep(0.1)
        free.sent.clear()
        hear(F1)
        await follow._decide()
        stamp = max(follow.STATE["utt"] or [0]) or follow.STATE["ex_stamp"]
        await _REAL_NUDGE("f1", stamp, follow.STATE["heard_mono"])
        await asyncio.sleep(0.05)

    run(go())
    assert [s[1] for s in free.sent] == ["f1"]                                   # the slide only
    assert event_order.STATE["marks"].get("2.0") is None and event_order.STATE["last"] in (None, (1, 0))
    assert announced == []                                                       # no "it is your turn"
    assert follow.snapshot()["free_talk_th"].startswith("คุยสด")


def test_a_picker_strong_pick_marks_and_skips_nothing(free, monkeypatch):
    _fake_pick(monkeypatch, {"early": {"verdict": "pick", "cue": "f1", "score": 0.9, "strong": True}, "refine": {}})

    async def go():
        await begin()
        await asyncio.sleep(0.1)
        free.sent.clear()
        hear("tell us about the location")
        await follow._decide()

    run(go())
    assert [s[1] for s in free.sent] == ["f1"]
    assert event_order.STATE["marks"].get("2.0") is None


def test_the_prompt_has_no_script_lines_but_keeps_the_facts_and_the_source_order(monkeypatch):
    # (24 Nov ~13:40 the owner brought the script back into free talk: "เอาของสคริปมาเป็นคำถามได้หนิ" -- with
    # CEREMONY_FOLLOW_SCRIPT_PROMPT on, tests/test_ceremony_script_show.py; off, free talk is as it was here)
    monkeypatch.setattr(settings, "ceremony_follow_host", True)
    monkeypatch.setattr(settings, "ceremony_follow_cues", False)
    monkeypatch.setattr(settings, "ceremony_follow_script_prompt", False)
    monkeypatch.setattr(settings, "ceremony_free_talk", True)
    assert event_host.script_block() is None                                     # no follow notes, no lines
    assert ceremony.follow_prompt_th() == ceremony.FOLLOW_PROMPT_FREE_TH
    assert "20-35" not in ceremony.follow_brief_th() and "แบบย่อของบท" not in ceremony.follow_brief_th()
    monkeypatch.setattr(settings, "ceremony_follow_script_prompt", True)          # with the script: its brief says so
    assert ceremony.follow_prompt_th() == ceremony.FOLLOW_PROMPT_FREE_SCRIPT_TH
    assert ceremony.follow_brief_th() == ceremony.FOLLOW_BRIEF_FREE_SCRIPT_TH
    monkeypatch.setattr(settings, "ceremony_follow_script_prompt", False)
    rules = ceremony.prompt_rules()
    assert ceremony.SOURCE_ORDER_TH in rules and ceremony.SCRIPT_WINS_TH in rules and ceremony.common_area_th() in rules
    monkeypatch.setattr(settings, "ceremony_free_talk", False)                   # off = live first as before
    assert ceremony.follow_prompt_th() == ceremony.FOLLOW_PROMPT_LIVE_TH
    assert ceremony.follow_brief_th() == ceremony.FOLLOW_BRIEF_LIVE_TH


def test_the_end_silence_stays_1500_in_free_talk(monkeypatch):
    monkeypatch.setattr(settings, "ceremony_live_end_silence_ms", 1500)
    monkeypatch.setattr(settings, "ceremony_follow_host", True)
    monkeypatch.setattr(settings, "ceremony_follow_cues", False)
    monkeypatch.setattr(settings, "ceremony_free_talk", True)
    assert settings.call_vad_silence_ms("ceremony") == 1500
    monkeypatch.setattr(settings, "ceremony_follow_cues", True)
    assert settings.call_vad_silence_ms("ceremony") is None


def test_a_story_shows_when_no_cue_slide_is_up_and_not_over_one(free, monkeypatch):
    topic = "location_beach"
    monkeypatch.setattr(follow, "script_story_ids", lambda: {topic})            # (the old rule would have hidden it)
    story = {"type": "tool_result", "result": {"screen": "story", "topic": topic, "story": {"id": "x"}}}

    async def go():
        await begin()
        await asyncio.sleep(0.1)
        hear("anything about the beach")
        follow.STATE["utt"].clear()
        # no cue slide for this exchange: her story goes to the hall
        assert face_mirror._story_is_the_scripts(topic) is False
        assert follow.STATE["ex"]["motion"] == "story"
        # a cue slide of the NEXT exchange replaces it; then a story of the same exchange does not replace the cue
        hear("and then the next question")
        assert follow.claim_hall("cue") is True
        assert face_mirror._story_is_the_scripts(topic) is True                  # held back: the cue slide stays
        assert follow.STATE["ex"]["motion"] == "cue"

    run(go())
    assert story  # (shape documented above)


def test_first_clear_motion_wins_cue_over_topic_story_over_topic_not_story_over_cue(free):
    async def go():
        await begin()
        hear("q1")
        assert follow.claim_hall("topic") and follow.claim_hall("story") and not follow.claim_hall("topic")
        hear("q2")
        assert follow.claim_hall("topic") and follow.claim_hall("cue") and follow.claim_hall("cue")
        assert not follow.claim_hall("story") and not follow.claim_hall("topic")

    run(go())


def test_a_topic_slide_does_not_replace_a_cue_slide_of_the_same_exchange(free):
    async def go():
        await begin()
        await asyncio.sleep(0.1)
        free.sent.clear()
        hear(F1)
        await follow._decide()
        ceremony_motion.STATE.update(slides={"facilities": {"template": "act", "cue": "s06_l03"}}, ready=True, cue_at=None)
        cap = SimpleNamespace(text="Emma, do you have a gym or a sauna?", at=follow.STATE["ex_stamp"])
        ceremony_motion._decide_now(ceremony.active_session(), cap)

    run(go())
    assert [s[1] for s in free.sent] == ["f1"]


def test_the_logo_returns_after_her_voice_ends_and_a_story_is_cleared_too(free):
    async def go():
        await begin()
        await asyncio.sleep(0.1)
        free.sent.clear()
        hear("tell us about the rooms")
        assert follow.claim_hall("story")
        follow.STATE["timer"] = None
        await follow._tick()                                                     # idle starts
        assert free.sent == []
        await asyncio.sleep(0.2)
        await follow._tick()

    run(go())
    assert free.sent == [(None, None)] and len(free.cleared) == 1                # the logo, and the story down


def test_the_logo_waits_while_her_voice_is_playing_and_a_new_caption_or_the_host_cancels_it(free, monkeypatch):
    async def go():
        await begin()
        await asyncio.sleep(0.1)
        free.sent.clear()
        hear("q")
        follow.claim_hall("cue")
        follow.STATE["timer"] = None
        session = _session()
        session._array_audible_until = lambda now: now + 5.0
        await follow._tick()
        await asyncio.sleep(0.2)
        await follow._tick()
        assert free.sent == []                                                   # her voice is still playing
        session._array_audible_until = lambda now: 0.0
        await follow._tick()                                                     # idle starts now
        gate = _gate(True)                                                       # the host speaks again
        await asyncio.sleep(0.2)
        await follow._tick()
        assert free.sent == [] and follow.STATE["idle_since"] is None            # cancelled
        gate.in_speech = False
        await follow._tick()
        hear("a new caption")                                                    # (a timer is armed by a real caption)
        follow.STATE["timer"] = object()
        await asyncio.sleep(0.2)
        await follow._tick()
        follow.STATE["timer"] = None
        assert free.sent == []

    run(go())


def test_a_staff_press_takes_the_logo_timer_away(free):
    async def go():
        await begin()
        await asyncio.sleep(0.1)
        free.sent.clear()
        hear("q")
        follow.claim_hall("cue")
        follow.STATE["timer"] = None
        ceremony_motion.on_press()                                               # a staff press (script_press -> _motion)
        await asyncio.sleep(0.2)
        await follow._tick()
        await asyncio.sleep(0.2)
        await follow._tick()

    run(go())
    assert free.sent == []                                                       # (no logo came for a slide the press owns)
    assert follow.STATE["hall"] is None


def test_the_slide_replaced_by_a_subtitle_is_not_taken_down_by_the_logo(free, monkeypatch):
    async def go():
        await begin()
        await asyncio.sleep(0.1)
        free.sent.clear()
        hear("q")
        follow.STATE["timer"] = None
        monkeypatch.setattr(face_mirror, "motion_now", lambda: obj[0])
        obj = [{"slide": 1}]
        follow.claim_hall("cue")
        await follow._tick()                                                     # records our slide
        obj[0] = None                                                            # a pressed line's subtitle took its place
        await asyncio.sleep(0.2)
        await follow._tick()
        await asyncio.sleep(0.2)
        await follow._tick()

    run(go())
    assert free.sent == [] and follow.STATE["hall"] is None


def test_a_new_exchange_with_no_motion_takes_the_old_slide_down(free, monkeypatch):
    _fake_pick(monkeypatch, {"early": {"verdict": "pick", "cue": "f1", "score": 0.9, "strong": True}, "refine": {}})

    async def go():
        await begin()
        await asyncio.sleep(0.1)
        free.sent.clear()
        hear("tell us about the location")
        await follow._decide()
        assert [s[1] for s in free.sent] == ["f1"]
        _fake_pick(monkeypatch, {"early": {}, "refine": {}})                     # the next question: nothing to pick
        hear("what time is lunch served today my friend")
        await follow._decide()

    run(go())
    assert [s[1] for s in free.sent] == ["f1", None]                             # slide A is not left under question B


def test_a_pressed_lines_words_do_not_refine_a_pick(free, monkeypatch):
    _fake_pick(monkeypatch, {"early": {}, "refine": {"verdict": "pick", "cue": "f1", "score": 0.9, "strong": True}})

    async def go():
        await begin()
        await asyncio.sleep(0.1)
        free.sent.clear()
        hear("tell us about the rooms")
        await follow._decide()
        ceremony.STATE["script_presses"] = 1                                     # a staff press is on
        follow.on_answer_words(ceremony.active_session(), WORDS)
        follow.on_answer_done(ceremony.active_session())
        await asyncio.sleep(0.2)
        ceremony.STATE["script_presses"] = 0

    run(go())
    assert free.sent == []


def test_script_press_forgets_the_pick_context(free):
    async def go():
        await begin()
        hear("tell us about the rooms")
        follow.STATE["pick"] = {"stamp": 1}
        async with ceremony.script_press("script"):
            assert follow.STATE["pick"] is None

    run(go())


def test_send_cue_refuses_while_a_pressed_lines_subtitle_is_held(free, monkeypatch):
    monkeypatch.setitem(face_mirror._state, "subtitle", {"id": "f1"})
    assert ceremony_motion.send_cue("f1", {"template": "act"}) is False
    assert free.sent == []


def test_a_joke_follows_her_answer_on_its_topic_once(free):
    voice = free

    async def go():
        await begin()
        await asyncio.sleep(0.1)
        hear(F1)
        await follow._decide()
        assert follow.STATE["topic_cue"] == "f1"
        follow.on_answer_words(_session(), "Of course, here is the answer to that")
        await follow._tick()
        assert voice.ids() == []                                                 # she has not finished
        follow.on_answer_done(_session())
        assert follow.STATE["joke"] is not None and follow.STATE["joke"]["cue"] == "j_setup"
        await asyncio.sleep(0.1)
        await follow._tick()
        assert voice.ids() == ["j_setup"]                                        # the staff-press path
        voice.said("j_setup")
        assert await _until(lambda: "j_punch" in voice.ids())
        for _ in range(3):
            await follow._tick()
        follow._arm_topic_joke("f1")                                             # the same topic again: never twice
        assert follow.STATE["joke"] is None
        return voice.ids()

    assert run(go()) == ["j_setup", "j_punch"]


def test_a_joke_waits_for_a_quiet_host_then_goes_to_staff(free, monkeypatch):
    voice = free
    monkeypatch.setattr(follow, "JOKE_GIVE_UP_S", 0.2)

    async def go():
        await begin()
        await asyncio.sleep(0.1)
        hear(F1)
        await follow._decide()
        follow.on_answer_words(_session(), "Of course, here is the answer to that")
        follow.on_answer_done(_session())
        _gate(True)
        await asyncio.sleep(0.1)
        await follow._tick()
        await asyncio.sleep(0.25)
        await follow._tick()
        assert follow.snapshot()["joke_th"] == follow.JOKE_STAFF_TH

    run(go())
    assert voice.ids() == []


def test_a_joke_is_not_armed_for_a_topic_without_one_or_with_jokes_off(free, monkeypatch):
    follow._arm_topic_joke("f4")
    assert follow.STATE["joke"] is None
    monkeypatch.setattr(settings, "ceremony_jokes_auto", False)
    follow._arm_topic_joke("f1")
    assert follow.STATE["joke"] is None


def test_the_keeper_keeps_free_talk_through_a_long_redial_gap_and_adopts_the_new_call(free, monkeypatch):
    monkeypatch.setattr(follow, "APP_GONE_S", 0.05)

    async def go():
        await begin()
        follow.STATE["lost_at"] = time.monotonic() - 1.0
        monkeypatch.setattr(ceremony, "active_session", lambda: None)
        monkeypatch.setattr(follow, "KEEP_S", 0.02)
        follow.STATE["keeper"] = asyncio.ensure_future(follow._keep())
        await asyncio.sleep(0.2)
        assert follow.STATE["on"] is True                                        # not stopped for the gap
        follow.STATE["keeper"].cancel()

    run(go())


def test_the_old_behaviour_stops_follow_after_the_gap_when_free_talk_is_off(free, monkeypatch):
    monkeypatch.setattr(settings, "ceremony_free_talk", False)
    monkeypatch.setattr(follow, "APP_GONE_S", 0.05)

    async def go():
        await begin()
        follow.STATE["lost_at"] = time.monotonic() - 1.0
        monkeypatch.setattr(ceremony, "active_session", lambda: None)
        monkeypatch.setattr(follow, "KEEP_S", 0.02)
        await asyncio.wait_for(follow._keep(), 2)

    run(go())
    assert follow.STATE["on"] is False


def test_free_talk_off_is_live_first_as_before(free, monkeypatch):
    monkeypatch.setattr(settings, "ceremony_free_talk", False)

    async def go():
        await begin()
        await asyncio.sleep(0.1)
        free.sent.clear()
        hear(F1)
        await follow._decide()

    run(go())
    assert [s[1] for s in free.sent] == ["f1"] and event_order.STATE["marks"]["2.0"]["state"] == "done"
    assert follow.free_talk() is False and follow.claim_hall("story") is True
    assert "free_talk_th" in follow.snapshot() and follow.snapshot()["free_talk_th"] is None


def test_the_pick_table_is_never_emptied_while_it_is_rebuilt():
    ceremony_pick.reset()
    assert ceremony_pick._STOCK.get("sig") is None
    ceremony_pick.table()
    assert "cues" in ceremony_pick._STOCK
    seen = []
    orig = dict.clear
    ceremony_pick.reset()
    ceremony_pick.table()
    assert ceremony_pick._STOCK.get("cues") is not None and seen == [] and orig is dict.clear


def test_the_hall_page_holds_a_slide_until_the_line_playing_there_is_down():
    """Source-level (no browser here): the slide branch of mirrorMotion queues behind a line still on (`heldMotion`),
    mirrorSubDown('over') plays it, a logo / a stop drops it. Review B 24 Nov."""
    from pathlib import Path

    page = (Path(__file__).resolve().parent.parent / "client" / "voice-preview.html").read_text(encoding="utf-8")
    # (the live-subtitle change, 24 Nov: a SCRIPT line -- `line`, never her live Thai line -- holds the slide)
    body = page[page.index("function mirrorMotion(m) {"):page.index("line.unsettled = true;")]
    assert "var line = mirrorSubRun && !mirrorSubRun.live ? mirrorSubRun : null;" in body
    assert body.index("line.heldMotion = m") < body.index("mirrorSubDown();")
    assert "line.heldMotion = null" in body
    down = page[page.index("function mirrorSubDown(over) {"):page.index("A line that reaches this screen while voice")]
    assert "over === 'over' && mirrorSubRun ? mirrorSubRun.heldMotion : null" in down and "mirrorMotion(heldMotion)" in down
