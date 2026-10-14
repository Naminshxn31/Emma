"""The follow-up after silence (FOLLOW_UP_AFTER_S, owner 12 Oct 2026).

"ถ้า emma ตอบคำถามจบแล้วต้องรอฟังเสียงกี่วิ ถ้าไม่ได้ยินเสียงอะไรเลยก็ถามต่อ
ถ้าได้ยินเสียงก็รอฟังแล้วก็ตอบ" -- answer completely, then: silence for the
set time after her voice has played out -> one short follow-up question; any
voice in that window -> no follow-up for that answer; once per answer.
"""
import asyncio
import pathlib

import pytest

from app import events, prompts, turnlog
from app import session as session_module
from app.config import settings


class _Provider:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(text)


@pytest.fixture
def world(monkeypatch):
    """A condo session with the follow-up on at 0.3 s, a quiet room, her
    voice already played out, and announce/turnlog captured."""
    monkeypatch.setattr(settings, "follow_up_after_s", 0.3)
    sess = session_module.VoiceSession(None, provider_name="gemini", profile="condo")
    sess.provider = _Provider()
    sess.provider._vad_gate = object()         # VAD_MODE=local: the watcher can hear
    session_module._active = sess              # the live call (conftest clears it)
    state = {"speaking": False, "audible": False}
    monkeypatch.setattr(sess, "_guest_speaking", lambda: state["speaking"])
    monkeypatch.setattr(sess, "_she_is_audible", lambda now: state["audible"])
    monkeypatch.setattr(sess, "_reply_owed", lambda now: False)
    said = []

    async def announce(text, source, still_relevant=None):
        if still_relevant is not None and not still_relevant():
            return False
        said.append((source, text))
        return True

    monkeypatch.setattr(events, "announce", announce)
    rows = []
    monkeypatch.setattr(turnlog, "record", lambda event, **f: rows.append((event, f)))
    return sess, state, said, rows


def _answer(sess, said_text="โครงการของเราอยู่ย่านจอมเทียนค่ะ", guest_text="อยู่ที่ไหน"):
    """A guest question and her complete answer, ending at turn_complete."""
    sess._guest_started_turn()
    sess._guest_text = guest_text
    sess._said_since_guest = said_text
    sess._spoke_this_turn = True
    sess._maybe_schedule_follow_up()
    sess._spoke_this_turn = False


async def _settle(sess, seconds=1.0):
    await asyncio.sleep(seconds)
    task = sess._follow_up_task
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_silence_after_the_answer_gets_exactly_one_follow_up(world):
    sess, state, said, rows = world

    async def go():
        _answer(sess)
        await _settle(sess)
        # her follow-up's own turn_complete must not arm another one
        sess._spoke_this_turn = True
        sess._maybe_schedule_follow_up()
        await _settle(sess)

    asyncio.run(go())
    assert said == [("follow_up", session_module.FOLLOW_UP_TEXT)]
    assert ("follow_up", {"sent": True, "after_s": 0.3}) in rows


def test_any_voice_in_the_window_cancels_it(world):
    """Even a voice focus refused (a friend beside her): listen, no follow-up."""
    sess, state, said, rows = world

    async def go():
        _answer(sess)
        await asyncio.sleep(0.1)
        state["speaking"] = True
        await _settle(sess)

    asyncio.run(go())
    assert said == []
    assert ("follow_up", {"sent": False, "why": "voice_heard"}) in rows


def test_the_guest_speaking_again_voids_the_pending_one(world):
    sess, state, said, rows = world

    async def go():
        _answer(sess)
        await asyncio.sleep(0.1)
        sess._guest_started_turn()           # speech_started of their next question
        await _settle(sess)

    asyncio.run(go())
    assert said == []
    assert ("follow_up", {"sent": False, "why": "guest_spoke"}) in rows


def test_the_clock_starts_when_her_voice_has_played_out(world):
    """The model generates 4-6x faster than she speaks: silence is counted
    from the end of her audio, not from turn_complete."""
    sess, state, said, rows = world

    async def go():
        state["audible"] = True
        _answer(sess)
        await asyncio.sleep(0.8)              # well past 0.3 s, but she is still talking
        assert said == []
        state["audible"] = False
        await _settle(sess, 0.8)

    asyncio.run(go())
    assert [s for s, _ in said] == ["follow_up"]


@pytest.mark.parametrize("said_text,guest_text,why", [
    ("มีห้องตัวอย่างให้ชมด้วยนะคะ สนใจดูไหมคะ", "มีห้องตัวอย่างไหม", "she_asked"),
    ("สนใจห้องแบบไหนดีคะ", "มีห้องแบบไหนบ้าง", "she_asked"),
    ("หนึ่งห้องนอนหรือสองห้องนอนคะ", "อยากได้ห้องใหญ่", "she_asked"),
    ("Would you like to see the floor plan?", "where is it", "she_asked"),
    ("ยินดีค่ะ", "ขอบคุณมากครับ", "guest_closed"),
    ("No problem.", "that's all, thanks", "guest_closed"),
])
def test_no_follow_up_when_she_already_asked_or_the_guest_closed(world, said_text, guest_text, why):
    sess, state, said, rows = world

    async def go():
        _answer(sess, said_text=said_text, guest_text=guest_text)
        await _settle(sess)

    asyncio.run(go())
    assert said == []
    assert ("follow_up", {"sent": False, "why": why}) in rows


def test_off_by_default_and_never_outside_the_sales_profile(world, monkeypatch):
    sess, state, said, rows = world

    async def go(s):
        _answer(s)
        await _settle(s, 0.6)

    monkeypatch.setattr(settings, "follow_up_after_s", 0.0)
    asyncio.run(go(sess))
    assert sess._follow_up_task is None and said == []
    monkeypatch.setattr(settings, "follow_up_after_s", 0.3)
    joke = session_module.VoiceSession(None, provider_name="gemini", profile="joke")
    asyncio.run(go(joke))
    assert joke._follow_up_task is None


def test_the_prompt_carries_the_exception_only_where_the_follow_up_runs(monkeypatch):
    """Off: the "ไม่ถามต่อ" rule reads exactly as before (the sales room's
    PC); on: the exception is written into the rule itself, so the follow-up
    instruction never races the rule."""
    clause = "หรือเมื่อระบบแจ้งว่าลูกค้าเงียบไปหลังคำตอบ (ถามต่อหนึ่งประโยคตามที่แจ้ง)"
    monkeypatch.setattr(settings, "follow_up_after_s", 0.0)
    off = prompts.build_instructions("X", robot_face=True)
    assert clause not in off and "[ถามต่อเมื่อเงียบ]" not in off
    assert "(ดูข้อสิ่งอำนวยความสะดวก) ถ้าลูกค้าปิดเรื่อง" in off
    monkeypatch.setattr(settings, "follow_up_after_s", 4.0)
    on = prompts.build_instructions("X", robot_face=True)
    assert "(ดูข้อสิ่งอำนวยความสะดวก) " + clause + " ถ้าลูกค้าปิดเรื่อง" in on
    # owner 12 Oct: complete, not short
    assert "1. ตอบให้ครบถ้วนพอสำหรับสิ่งที่ถาม" in on
    assert "ไม่เกิน 2-3 ประโยค" not in on


def test_the_follow_up_text_is_one_question_and_the_rules_exception():
    text = session_module.FOLLOW_UP_TEXT
    assert "หนึ่งประโยค" in text and "ข้อยกเว้นของกฎไม่ถามต่อ" in text
    assert "ห้ามถามว่า 'มีอะไรให้ช่วยอีกไหมคะ'" in text
    assert "ห้ามเปิดเรื่องราคาเอง" in text


def test_thai_closings_are_words_not_substrings():
    """บาย is inside สบาย: "อยู่สบายไหม" is a question, not a goodbye."""
    for text in ("ขอบคุณมากครับ", "ไม่เป็นไรค่ะ", "แค่นี้ก่อนนะ", "บายค่ะ", "ไว้ก่อนนะ", "That's all, thanks"):
        assert session_module._guest_closed(text), text
    for text in ("อยู่สบายไหม", "ห้องนี้ผ่อนคลายสบายดีไหม", "ใกล้ทะเลไหม", "what is nearby"):
        assert not session_module._guest_closed(text), text


def test_question_detection_reads_the_particle_not_word_lists():
    """Review 12 Oct: "กี่" inside "เกี่ยว" read statements as questions, and
    "...แบบไหนดีคะ" was missed. คะ asks, ค่ะ / นะคะ tell."""
    q = session_module._ENDS_ON_QUESTION
    for text in ("สนใจห้องแบบไหนดีคะ", "มีคำถามเพิ่มเติมบ้างคะ", "ลูกค้าเคยมาพัทยาหรือยังคะ",
                 "สนใจดูภาพไหมคะ.", "Would you like to see it?"):
        assert q.search(text), text
    for text in ("ข้อมูลเกี่ยวกับสัญญาค่ะ", "ที่เกี่ยวข้องทีมขายจะแจ้งได้ค่ะ", "ไม่ต้องเตรียมอะไรค่ะ",
                 "ติดต่อทีมขายได้เลยนะคะ", "Sure, here it is."):
        assert not q.search(text), text


def test_a_tool_call_voids_the_armed_watcher_and_the_reply_re_arms_it():
    """gemini-3.8 completes a turn before its tool call; on the robot
    TURN_IDLE_TRACKS_TOOLS is off, so the tool_call itself must void the
    watcher, and the tool's spoken reply's turn_complete re-arms it."""
    src = pathlib.Path(session_module.__file__).read_text(encoding="utf-8")
    branch = src.split('elif event.kind == "tool_call":', 1)[1].split("elif ", 1)[0]
    assert 'self._follow_up_gen = getattr(self, "_follow_up_gen", 0) + 1' in branch
    assert "_answer_pending" not in branch          # the answer is still owed its follow-up


def test_an_announcement_voids_the_pending_follow_up(world, monkeypatch):
    """Her own news (robot_arrived, an invite...) is not an answer to follow up."""
    sess, state, said, rows = world
    src = pathlib.Path(events.__file__).read_text(encoding="utf-8")
    src = src[src.index("async def announce("):]
    assert 'if source not in ("follow_up", "respeak_silent_block"):' in src
    sess._answer_pending = True
    gen = sess._follow_up_gen
    sess._void_follow_up()
    assert sess._answer_pending is False and sess._follow_up_gen == gen + 1


def test_a_superseded_call_never_follows_up_into_the_new_one(world):
    sess, state, said, rows = world

    async def go():
        _answer(sess)
        session_module._active = object()        # a page reload took the robot over
        await _settle(sess)

    asyncio.run(go())
    assert said == []
    assert ("follow_up", {"sent": False, "why": "superseded"}) in rows


def test_blind_without_a_local_vad_gate(world):
    """VAD_MODE=gemini: nothing can tell the watcher the guest is talking."""
    sess, state, said, rows = world
    sess.provider._vad_gate = None
    _answer(sess)
    assert sess._follow_up_task is None

