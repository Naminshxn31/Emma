"""Live first: CEREMONY_FOLLOW_CUES=false (owner 24 Nov ~11:40, "อยากให้มันตอบสดเลย ไม่ต้องมากดตามบท").

What these hold down:
* with the switch off a recognised scripted line presses NOTHING and holds/drops NOTHING of her live reply; the cue's
  slide goes on the hall screen (face_mirror.motion, settled) and the pointer moves on (the step marked done);
* an off-script line: her reply passes, and the topic slide still works;
* a staff press still plays the cue (the fallback); a continuation is not played by the server;
* the prompts say she answers herself; with the switch on (default) today's wording and behaviour are unchanged.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app import ceremony, ceremony_follow as follow, ceremony_motion, ceremony_slides, event_host, event_order
from app import face_mirror
from app.config import settings
from tests.test_ceremony_follow import F1, F3, begin, fol, hear, played, run   # noqa: F401 -- fixtures
from tests.test_event_order import REAL_SCRIPT, _signed, call, calls, event, rows  # noqa: F401 -- fixtures


@pytest.fixture
def live(fol, monkeypatch):                                      # noqa: F811
    """Follow on, cues OFF; a slide for every cue; the hall screen's messages captured."""
    monkeypatch.setattr(settings, "ceremony_follow_cues", False)
    monkeypatch.setattr(settings, "ceremony_follow_answer_all", True)
    monkeypatch.setattr(ceremony_slides, "for_cue",
                        lambda cue, lang="en", drafts=False: {"template": "act", "cue": cue})
    sent = []
    monkeypatch.setattr(face_mirror, "motion", lambda slide, cue: sent.append((slide, cue)))
    ceremony_motion.reset()
    yield SimpleNamespace(calls=fol, sent=sent)
    ceremony_motion.reset()


async def _settle(live=None):
    import asyncio
    await asyncio.sleep(0.1)                     # the window's own logo (on_open's executor) lands first
    if live is not None:
        live.sent.clear()


def test_the_switch_defaults_on_so_today_is_unchanged():
    assert settings.ceremony_follow_cues is True and follow.live_first() is False
    assert ceremony.follow_brief_th() == ceremony.FOLLOW_BRIEF_TH


def test_a_scripted_line_shows_its_slide_moves_the_pointer_and_plays_nothing(live):
    async def go():
        await begin()
        await _settle(live)
        hear(F1)
        await follow._decide()
        return ceremony.live_state()

    window = run(go())
    assert played(live.calls) == []                                          # no press, no recording
    assert live.sent == [({"template": "act", "cue": "f1"}, "f1")]           # the settled slide of the cue
    assert event_order.STATE["marks"]["2.0"]["state"] == "done" and event_order.STATE["last"] == (2, 0)
    assert window is not None                                                # her live window is still open
    assert follow.reply_hold() is None and follow.STATE["dropped"] is False  # never held, never dropped
    assert follow.STATE["last_fire"]["by"] == "live" and follow.STATE["last_fire"]["ok"] is True
    assert follow.snapshot()["live_first_th"] == "ตอบสดเป็นหลัก — บทกดเองเป็นทางสำรอง"


def test_the_next_line_matches_the_next_cue_and_a_continuation_is_not_played(live):
    async def go():
        await begin()
        await _settle(live)
        hear(F1)
        await follow._decide()
        ptr = follow.pointer()
        hear(F3)
        await follow._decide()
        await follow._tick()                                                 # the keeper: nothing plays a continuation
        return ptr

    ptr = run(go())
    assert ptr["kind"] == "host" and [c["cue"] for c in ptr["cands"]][0] == "f3"   # f2 (`continues`) read past
    assert [s[1] for s in live.sent] == ["f1", "f3"] and played(live.calls) == []
    assert event_order.STATE["last"] == (2, 2)


def test_an_off_script_line_changes_nothing_and_her_reply_passes(live):
    async def go():
        await begin()
        await _settle(live)
        hear("Emma, what time does the gym open?")
        await follow._decide()

    run(go())
    assert live.sent == [] and played(live.calls) == []
    assert event_order.STATE["last"] is None or event_order.STATE["last"] == (1, 0)
    assert follow.reply_hold() is None


def test_the_topic_slide_still_works_off_script(live):
    async def go():
        await begin()
        await _settle(live)
        ceremony_motion.STATE.update(slides={"facilities": {"template": "act", "cue": "s06_l03"}}, ready=True)
        cap = SimpleNamespace(text="Emma, do you have a gym or a sauna?", at=time.monotonic())
        ceremony_motion._decide_now(ceremony.active_session(), cap)

    run(go())
    assert live.sent == [({"template": "act", "cue": "s06_l03"}, "s06_l03")]


def test_the_matched_utterance_is_not_replaced_by_a_topic_slide(live):
    async def go():
        await begin()
        await _settle(live)
        ceremony_motion.STATE.update(slides={"facilities": {"template": "act", "cue": "s06_l03"}}, ready=True)
        at = time.monotonic()
        assert ceremony_motion.show_cue("f1", at) is True
        cap = SimpleNamespace(text="the gym and the sauna", at=at)
        ceremony_motion._decide_now(ceremony.active_session(), cap)

    run(go())
    assert [s[1] for s in live.sent] == ["f1"]


def test_a_staff_press_still_plays_the_cue_as_the_fallback(live):
    async def go():
        await begin()
        await _settle(live)
        return await event_order.press(2, 0)

    assert run(go())["ok"] is True
    assert played(live.calls) == ["f1"]


def test_no_servable_slide_still_moves_the_pointer(live, monkeypatch):
    monkeypatch.setattr(ceremony_slides, "for_cue", lambda cue, lang="en", drafts=False: None)

    async def go():
        await begin()
        await _settle(live)
        hear(F1)
        await follow._decide()

    run(go())
    assert live.sent == [] and played(live.calls) == []
    assert event_order.STATE["marks"]["2.0"]["state"] == "done"


def test_the_prompts_say_she_answers_herself(live, monkeypatch):
    monkeypatch.setattr(settings, "ceremony_follow_script_prompt", True)
    assert ceremony.follow_brief_th() == ceremony.FOLLOW_BRIEF_LIVE_TH
    assert ceremony.follow_prompt_th() == ceremony.FOLLOW_PROMPT_LIVE_TH
    block = event_host.script_block()
    assert block and "ไม่มีระบบเล่นบทให้" in block and "ระบบเล่นบทที่ตรงกับ" not in block
    monkeypatch.setattr(settings, "ceremony_follow_cues", True)
    block = event_host.script_block()
    assert "ระบบเล่นบทที่ตรงกับ" in block and "ไม่มีระบบเล่นบทให้" not in block


def test_a_refused_press_drops_nothing_and_reopens_the_window(fol, monkeypatch):     # noqa: F811
    """Robot log 24 Nov 11:36: `dropped` was set before the press, the press was refused -> silence."""
    monkeypatch.setattr(settings, "ceremony_follow_answer_all", True)

    async def refused(n, i, **kw):
        return {"ok": False, "error": "still_speaking", "error_th": "x"}

    async def go():
        await begin()
        await _settle()
        monkeypatch.setattr(event_order, "press", refused)
        hear(F1)
        await follow._decide()
        return ceremony.live_state()

    window = run(go())
    assert follow.STATE["dropped"] is False and follow.reply_hold() is None
    assert window is not None                                                  # the press closed it; follow reopened it


def test_no_press_while_her_own_reply_is_streaming(fol, monkeypatch):           # noqa: F811
    monkeypatch.setattr(settings, "ceremony_follow_answer_all", True)

    async def go():
        await begin()
        await _settle()
        follow.STATE["session"] = SimpleNamespace(provider=SimpleNamespace(model_streaming=lambda: True))
        return follow._gate_why()

    assert run(go()) == "answering"


def test_the_ceremony_call_transcribes_english_only_with_the_switch(monkeypatch):
    from app.providers import gemini

    monkeypatch.setattr(settings, "transcribe_languages", "th-TH,en-US,zh-CN")
    monkeypatch.setattr(gemini, "_session_profile", lambda: "ceremony")
    assert gemini.language_codes() == ["th-TH", "en-US", "zh-CN"]
    monkeypatch.setattr(settings, "ceremony_english_only", True)
    assert gemini.language_codes() == ["en-US"]
    monkeypatch.setattr(gemini, "_session_profile", lambda: "event")
    assert gemini.language_codes() == ["th-TH", "en-US", "zh-CN"]


def test_with_the_switch_on_a_match_still_presses_and_drops(fol, monkeypatch):     # noqa: F811
    monkeypatch.setattr(settings, "ceremony_follow_answer_all", True)

    async def go():
        await begin()
        await _settle()
        hear(F1)
        await follow._decide()

    run(go())
    assert played(fol) == ["f1"] and follow.STATE["last_fire"]["by"] == "host"

