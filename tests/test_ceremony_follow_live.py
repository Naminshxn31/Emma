"""Follow mode answers the host live without her name (owner 24 Nov 11:00, app/ceremony_follow.py).

"เพราะเขาจะพูดสด ดังนั้นก็เหมือนตอนที่เราทำตอนแรก ให้หุ่นสามารถตอบได้แม้จะไม่ตรงกับบทมาก": what these hold down:

* off (CEREMONY_FOLLOW_ANSWER_ALL absent) = the named-only door as it was;
* on: an unnamed host question is answered in follow mode's open window, not outside it;
* a matched scripted line plays ONLY the recording: her live reply to the same words is held until follow decides
  and then dropped (NoiseReplyGate follow_hold); an unmatched line gets ONLY her live reply;
* a re-dialled ceremony call keeps follow on (pointer unchanged); staff stop / a reset / the app gone > 30 s end it;
* the script in her live context (CEREMONY_FOLLOW_SCRIPT_PROMPT): off = unchanged prompt; on = the signed cues, not the
  ones the server plays itself.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from app import ceremony, ceremony_follow as follow, event_cues, event_host, event_order
from app import session as session_module
from app.config import settings
from app.providers import gemini
from app.providers.base import ProviderEvent
from app.providers.transcript_filter import NoiseReplyGate
from tests.test_ceremony_follow import F1, begin, fol, hear, played, run   # noqa: F401 -- fixtures
from tests.test_event_order import REAL_SCRIPT, _signed, call, calls, event, rows  # noqa: F401 -- fixtures

UNNAMED = "ที่นี่มีสระว่ายน้ำไหมครับ"
OFF_SCRIPT = "วันนี้อากาศร้อนไหมครับ"


def _gate() -> NoiseReplyGate:
    return NoiseReplyGate(0.06, named_only=gemini._ceremony_named_only, cue_only=gemini._cue_only_closed,
                          follow_hold=gemini._follow_hold)


def _reply(gate: NoiseReplyGate, text: str, rms: float = 0.1) -> None:
    gate.segment(rms)
    gate.heard(text)


def _t(text="ค่ะ มีสระว่ายน้ำค่ะ"):
    return ProviderEvent(kind="assistant_transcript", text=text)


@pytest.fixture
def allow(fol, monkeypatch):                                     # noqa: F811
    monkeypatch.setattr(settings, "ceremony_follow_answer_all", True)
    return fol


def test_off_the_named_only_door_is_as_it_was(fol, monkeypatch):                     # noqa: F811
    assert settings.ceremony_follow_answer_all is False
    run(begin())
    assert follow.answers_all() is False and gemini._ceremony_named_only() is True
    gate = _gate()
    _reply(gate, UNNAMED)
    assert gate.push(_t()) == []                                   # not named: held, then dropped at the end
    owed, dropped = gate.close()
    assert owed == [] and dropped["why"] == "not_named"


def test_on_an_unnamed_host_question_is_answered_in_the_window_only(allow):
    run(begin())
    assert follow.answers_all() is True and gemini._ceremony_named_only() is False
    gate = _gate()
    _reply(gate, UNNAMED)
    first = _t()
    assert gate.push(first) == [first]                             # nothing pending: hers to say
    assert gate.close() == ([], None)
    # outside follow mode (the mode stopped by staff) the door is the named-only one again
    assert follow.stop("staff") is True
    assert follow.answers_all() is False and gemini._ceremony_named_only() is True
    gate = _gate()
    _reply(gate, UNNAMED)
    assert gate.push(_t()) == [] and gate.close()[1]["why"] in ("not_named", "cue_only")


def test_the_noise_floor_still_drops_a_faint_unnamed_sound(allow):
    # (24 Nov: the host's level 0.015+ now passes in the window -- a short real question at his mic, review A --
    # so "faint" here is under CEREMONY_HOST_MIN_RMS)
    run(begin())
    gate = _gate()
    _reply(gate, "อืม", rms=0.01)
    assert gate.push(_t("ขอโทษค่ะ ได้ยินไม่ชัด")) == []
    assert gate.close()[1] is not None


def test_a_sentence_the_recogniser_missed_at_the_hosts_level_is_still_answered(allow):
    # review 24 Nov (confirmed by a probe): the host's collar mic reads ~0.02-0.03, under NOISE_REPLY_NEAR_RMS, so an
    # EMPTY transcript had her answer dropped as "noise" -- silence on stage. Empty + his level = her answer passes;
    # the faint fragment above still drops, and so does an empty one under the host floor.
    run(begin())
    gate = _gate()
    _reply(gate, "", rms=0.025)
    assert gate.push(_t("Thank you, Khun Miki. I'm happy to help everyone today.")) != []
    quiet = _gate()
    _reply(quiet, "", rms=0.005)
    assert quiet.push(_t("Sorry?")) == []
    # review A 24 Nov: a SHORT real question at his level ("Why?") is answered too, not dropped as noise
    short = _gate()
    _reply(short, "Why?", rms=0.025)
    assert short.push(_t("Because the lagoon is the heart of the project.")) != []


def test_a_matched_line_plays_only_the_recording_her_reply_is_held_then_dropped(allow):
    calls = allow

    async def scenario():
        await begin()
        hear(F1)                                                   # the host's scripted line, decided by the server
        assert follow.reply_hold() == "wait"
        gate = _gate()
        _reply(gate, F1)
        assert gate.push(_t("ที่ตั้งของโครงการคือ...")) == []      # held: follow has not decided yet
        assert gate.follow_waiting() is True
        await follow._decide()
        assert played(calls) == ["f1"] and follow.STATE["pending"] is False and follow.reply_hold() == "drop"
        assert gate.push(_t("ต่อ")) == []                          # dropped, not released
        return gate.close()

    owed, dropped = asyncio.run(scenario())
    assert owed == [] and dropped is not None and dropped["why"] in ("cue_matched", "cue_only")


def test_an_unmatched_line_gets_only_her_live_reply(allow):
    calls = allow

    async def scenario():
        await begin()
        hear(OFF_SCRIPT)
        gate = _gate()
        _reply(gate, OFF_SCRIPT)
        first = _t("ร้อนนิดหน่อยค่ะ")
        assert gate.push(first) == []                              # held while follow decides
        await follow._decide()
        assert played(calls) == [] and follow.reply_hold() is None
        second = _t(" ค่ะ")
        assert gate.push(second) == [first, second]                # released whole, nothing lost
        assert gate.close() == ([], None)

    asyncio.run(scenario())


def test_turn_complete_before_the_decision_waits_for_it_bounded(allow, monkeypatch):
    async def scenario():
        await begin()
        hear(OFF_SCRIPT)
        gate = _gate()
        _reply(gate, OFF_SCRIPT)
        gate.push(_t("ร้อนค่ะ"))
        prov = SimpleNamespace(_noise_gate=gate, _closing=False)
        monkeypatch.setattr(gemini, "FOLLOW_SETTLE_MAX_S", 0.3)
        began = time.monotonic()
        await gemini.GeminiProvider._follow_settle(prov)           # the decision never lands: bounded
        assert 0.25 <= time.monotonic() - began < 1.0
        return gate.close()

    owed, dropped = asyncio.run(scenario())
    assert dropped is None and len(owed) == 1                      # then released: the final verdict is hers


def test_a_press_cuts_a_live_reply_that_is_still_out_instead_of_waiting_for_it(allow):
    calls = allow

    async def scenario():
        await begin()
        hear(F1)
        await follow._decide()

    asyncio.run(scenario())
    assert played(calls) == ["f1"]
    assert event_cues.STATE.get("preempt_live_until", 0.0) == 0.0  # cleared once the press is over


def test_a_redialled_call_keeps_follow_on_with_the_pointer_unchanged(allow):
    run(begin())
    before = follow.pointer()["at"]
    old = ceremony.STATE["session"]
    new = SimpleNamespace(**vars(old)) if hasattr(old, "__dict__") else SimpleNamespace(profile=ceremony.PROFILE)
    session_module._active = new
    ceremony.STATE["session"] = new
    assert follow.active() is True and follow.STATE["session"] is new and follow.STATE["redials"] == 1
    assert follow.pointer()["at"] == before
    assert follow.snapshot()["redial_th"]
    # the old window went with the old call: the keeper opens a new one
    ceremony.STATE["live"] = None
    run(follow._tick())
    assert ceremony._adhoc_live(time.time()) is not None


def test_follow_ends_for_staff_a_reset_or_the_app_gone_over_30_s_not_for_a_redial_gap(allow, monkeypatch):
    run(begin())
    # the app is gone for a moment (a re-dial gap): still on
    monkeypatch.setattr(follow, "KEEP_S", 0.01)
    ceremony.STATE["session"] = None
    follow.STATE["lost_at"] = time.monotonic() - 5
    task = None
    import asyncio

    async def gap():
        t = asyncio.ensure_future(follow._keep())
        await asyncio.sleep(0.1)
        still = follow.STATE["on"]
        follow.STATE["lost_at"] = time.monotonic() - 31
        await asyncio.wait_for(t, 2)
        return still

    assert asyncio.run(gap()) is True and follow.STATE["on"] is False   # ended only after > 30 s
    assert follow.stop("staff") is False                                # (already off: staff stop is harmless)


def test_staff_stop_still_ends_it(allow):
    run(begin())
    assert follow.stop("staff") is True and follow.STATE["on"] is False and follow.answers_all() is False


def test_the_script_in_her_context_is_off_by_default_and_skips_the_cues_the_server_plays(fol, monkeypatch):   # noqa: F811
    monkeypatch.setattr(settings, "ceremony_follow_host", True)
    assert settings.ceremony_follow_script_prompt is False
    assert event_host.script_block() is None                       # as it was: no script in the prompt
    monkeypatch.setattr(settings, "ceremony_follow_script_prompt", True)
    block = event_host.script_block()
    assert block and event_host.SCRIPT_HEAD in block
    assert "อัลฟา" in block and "แกมมา" in block                    # f1, f3: host-line cues
    assert "เบตา" not in block                                     # f2 `continues`: the server plays it itself
    monkeypatch.setattr(settings, "ceremony_follow_host", False)
    assert event_host.script_block() is None


def test_the_live_brief_and_prompt_say_no_name_needed_only_with_the_switch(monkeypatch):
    assert ceremony.follow_brief_th() == ceremony.FOLLOW_BRIEF_TH
    assert ceremony.follow_prompt_th() == ceremony.FOLLOW_PROMPT_TH
    monkeypatch.setattr(settings, "ceremony_follow_answer_all", True)
    assert ceremony.follow_brief_th() == ceremony.FOLLOW_BRIEF_ALL_TH
    assert len(ceremony.ADHOC_BRIEF_TH + ceremony.FOLLOW_BRIEF_ALL_TH) < 1200
