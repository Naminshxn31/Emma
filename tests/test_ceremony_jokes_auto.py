"""The chain jokes that play by themselves in live-first (owner 24 Nov: "ส่วนมุกบางทีเราอาจะไม่ต้องรอมิกิตอบโต้ ...
ให้หุ่นเล่นไปเองเลย แบบต่อจากบทก่อนหน้าไปเลย", app/ceremony_follow.py `_joke_tick`, CEREMONY_JOKES_AUTO).

* the cue before a joke's set-up was taken + her live answer to it is over + the host quiet -> the set-up is pressed once
  through the staff press path, and the order's chain rule says the punchline after it;
* the host speaking = it waits; after JOKE_GIVE_UP_S staff are told ("มุกรอจังหวะ — กดเองได้") and it is theirs;
* staff pressing anything else / skipping it / a pause = cancelled; the switch off, or cues on = nothing at all.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import ceremony, ceremony_follow as follow, ceremony_motion, ceremony_slides, event_cues, event_host
from app import event_order, event_script, face_mirror
from app.config import settings
from tests.test_ceremony_follow import F1, begin, hear, run
from tests.test_event_order import _signed, call, calls, event, rows              # noqa: F401 -- fixtures
from tests.test_event_order_chain import Voice, _until

SCRIPT = {"format": event_script.FORMAT, "host_th": "คุณมิกิ", "replies": "cues", "cues": [
    _signed({"id": cid, "scene": 2, "th": "ข้อความตัวอย่างของบท%sค่ะ" % word, "numbers": []})
    for cid, word in (("f1", "อัลฟา"), ("j_setup", "เบตา"), ("j_punch", "แกมมา"), ("f4", "เดลต้า"))], "order": [
    {"n": 1, "title_th": "เปิด", "steps": [{"do": "follow"}]},
    {"n": 2, "title_th": "คุย", "steps": [
        {"do": "cue", "cue": "f1"}, {"do": "cue", "cue": "j_setup", "chain": True}, {"do": "cue", "cue": "j_punch"},
        {"do": "cue", "cue": "f4"}, {"do": "manual", "label_th": "ติ๊ก"}]}]}
HOSTS = {"format": "emma-ceremony-host-lines-v1", "lines": {
    "f1": {"kind": "host", "host_th": F1},
    "j_setup": {"kind": "host", "host_th": "ว้าว คุณจำรายละเอียดได้ดีมากเลยนะเอ็มม่า"},
    "j_punch": {"kind": "chain_follow", "note_th": "เฉลยมุก"},
    "f4": {"kind": "manual", "note_th": "ไม่มีบทคุณมิกิ"}}}


@pytest.fixture
def jokes(event, call, monkeypatch):                                              # noqa: F811
    base = Path(settings.ceremony_file)
    base.write_text(json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8")
    base.with_suffix(".host.json").write_text(json.dumps(HOSTS, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "ceremony_follow_host", True)
    monkeypatch.setattr(settings, "ceremony_follow_cues", False)
    monkeypatch.setattr(settings, "ceremony_follow_answer_all", True)
    monkeypatch.setattr(settings, "ceremony_jokes_auto", True)
    monkeypatch.setattr(follow, "KEEP_S", 3600.0)
    monkeypatch.setattr(follow, "HOLD_S", 0.6)
    monkeypatch.setattr(follow, "POLL_S", 0.1)
    monkeypatch.setattr(follow, "JOKE_SETTLE_S", 0.05)
    monkeypatch.setattr(follow, "JOKE_HOST_QUIET_S", 0.05)
    monkeypatch.setattr(ceremony_slides, "for_cue",
                        lambda cue, lang="en", drafts=False: {"template": "act", "cue": cue})
    monkeypatch.setattr(face_mirror, "motion", lambda slide, cue: None)

    async def no_nudge(*a, **k):
        return None

    monkeypatch.setattr(follow, "_nudge", no_nudge)
    voice = Voice(monkeypatch)
    ceremony_motion.reset()
    event_script.reset()
    event_order.reset()
    follow.reset()
    yield voice
    follow.reset()
    ceremony_motion.reset()


def _session():
    return ceremony.active_session()


def _gate(in_speech):
    follow.STATE["session"].provider = SimpleNamespace(_vad_gate=SimpleNamespace(in_speech=in_speech))
    return follow.STATE["session"].provider._vad_gate


async def _before_joke_answered(voice, *, answer_done=True):
    """He said f1's line (its step taken, the slide up), she answered it live; `answer_done` = her turn is over."""
    await begin()
    await asyncio.sleep(0.1)
    hear(F1)
    await follow._decide()
    assert event_order.STATE["last"] == (2, 0)
    follow.on_answer_words(_session(), "Of course, here is the answer to that")
    if answer_done:
        follow.on_answer_done(_session())
    await asyncio.sleep(0.1)                         # (his caption is older than the quiet time now)


def test_the_joke_goes_once_after_her_answer_and_its_punchline_follows(jokes):
    voice = jokes

    async def go():
        await _before_joke_answered(voice, answer_done=False)
        await follow._tick()
        assert voice.ids() == []                                   # she is still answering: wait
        follow.on_answer_done(_session())
        await follow._tick()
        assert voice.ids() == ["j_setup"]
        voice.said("j_setup")
        assert await _until(lambda: "j_punch" in voice.ids())      # the order's chain rule
        for _ in range(3):
            await follow._tick()
        return voice.ids()

    assert run(go()) == ["j_setup", "j_punch"]                     # once, never again


def test_the_press_closes_the_window_and_the_keeper_reopens_it_after_the_chain(jokes):
    voice = jokes

    async def go():
        await _before_joke_answered(voice)
        assert ceremony._adhoc_live(time.time()) is not None
        await follow._tick()
        assert voice.ids() == ["j_setup"] and ceremony._adhoc_live(time.time()) is None   # script_press closed it
        voice.said("j_setup")
        assert await _until(lambda: "j_punch" in voice.ids())
        voice.said("j_punch", tail=False)
        await asyncio.sleep(0.1)
        await event_order.snapshot()                                     # (the page's poll settles the finished lines)
        await follow._tick()                                       # after the chain: as after any staff press
        return ceremony._adhoc_live(time.time()) is not None

    assert run(go()) is True


def test_the_host_speaking_makes_it_wait_and_twenty_seconds_hands_it_to_staff(jokes, monkeypatch):
    voice = jokes
    monkeypatch.setattr(follow, "JOKE_GIVE_UP_S", 0.2)

    async def go():
        await _before_joke_answered(voice)
        gate = _gate(True)                                         # a segment of his is open
        await follow._tick()
        await follow._tick()
        assert voice.ids() == [] and follow.snapshot().get("joke_th") is None
        await asyncio.sleep(0.25)
        await follow._tick()
        assert follow.snapshot()["joke_th"] == follow.JOKE_STAFF_TH     # staff may press it
        gate.in_speech = False
        for _ in range(3):
            await follow._tick()
        return voice.ids()

    assert run(go()) == []                                         # given up: never over him, never late by itself


def test_the_host_stopping_in_time_lets_it_go(jokes):
    voice = jokes

    async def go():
        await _before_joke_answered(voice)
        gate = _gate(True)
        await follow._tick()
        assert voice.ids() == []
        gate.in_speech = False
        await asyncio.sleep(0.1)
        await follow._tick()
        return voice.ids()

    assert run(go()) == ["j_setup"]


def test_a_press_gate_up_makes_it_wait(jokes, monkeypatch):
    from app import event_walk

    voice = jokes
    moving = {"on": False}
    monkeypatch.setattr(event_walk, "walking", lambda: moving["on"])

    async def go():
        await _before_joke_answered(voice)
        moving["on"] = True
        await follow._tick()
        assert voice.ids() == []                                   # the robot is moving: the press would be refused
        moving["on"] = False
        await follow._tick()
        return voice.ids()

    assert run(go()) == ["j_setup"]


@pytest.mark.parametrize("how", ["other_press", "skipped_by_staff", "paused"])
def test_staff_doing_anything_else_cancels_it(jokes, how):
    voice = jokes

    async def go():
        await _before_joke_answered(voice, answer_done=False)
        await follow._tick()                                       # armed, her answer not over
        assert follow.STATE["joke"] is not None
        if how == "other_press":
            assert (await event_order.press(2, 4))["ok"]           # the tick two steps on: the order moved
        elif how == "skipped_by_staff":
            assert (await event_order.skip(2, 1))["ok"]
        else:
            assert follow.pause("stop")
        follow.on_answer_done(_session())
        await asyncio.sleep(0.1)
        for _ in range(3):
            await follow._tick()
        return voice.ids()

    assert run(go()) == []
    assert follow.STATE["joke"] is None


@pytest.mark.parametrize("what", ["switch_off", "cues_on", "follow_off"])
def test_the_switch_off_or_the_pressed_ceremony_does_nothing(jokes, monkeypatch, what):
    voice = jokes
    if what == "switch_off":
        monkeypatch.setattr(settings, "ceremony_jokes_auto", False)
    elif what == "cues_on":
        monkeypatch.setattr(settings, "ceremony_follow_cues", True)

    async def go():
        await begin()
        await asyncio.sleep(0.1)
        await event_order.mark_shown(2, 0)
        if what == "follow_off":
            follow.stop("test")
        follow.STATE["answer_done_at"] = time.monotonic()
        await asyncio.sleep(0.1)
        for _ in range(3):
            await follow._tick()
        return voice.ids()

    assert run(go()) == []
    assert follow.STATE["joke"] is None
