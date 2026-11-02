"""At a room's big screen the face rule gives way to a clear voice.

Owner 29 Oct, after staff tested the robot in the floor-1 media room with its
face on the TV: "ผ่อน 'ต้องเห็นหน้า' ตอนหุ่นอยู่หน้าจอใหญ่ ... ผ่อนเลย ฟังจาก
เสียงที่ได้ยินชัดเอา". The robot log of 28 Oct: a usable face in 1 of 297
listening decisions (people watched the TV, not the head camera), the
testers' first sentences at a normal voice (first frames 0.031 and 0.044)
refused "no face in front", 27 calls hung up "no face" 30 s after the last
words, and the silence follow-up vetoed "nobody_in_front" with the testers
standing there.

"At the big screen" is app/room_screens.at_screen (the pose inside the
screen's `near` area by the same hysteresis that opens and closes it, or its
face up on that TV within radius + 1 m); "clear" is FOCUS_SCREEN_RMS
(robot.env 0.03, 0 = off everywhere else).
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from app import focus, room_screens
from app.config import settings

T = 1000.0
ROOM = "ห้องทดสอบ"


@pytest.fixture
def rows(monkeypatch):
    from app import turnlog

    out: list[dict] = []
    monkeypatch.setattr(turnlog, "record", lambda event, **f: out.append({"event": event, **f}))
    return out


@pytest.fixture
def table(monkeypatch):
    monkeypatch.setattr(room_screens, "screens",
                        lambda: [{"name": ROOM, "agent": "http://pc", "near": {"x": 0.0, "y": 0.0, "radius": 3.0}}])
    room_screens.reset()
    yield
    room_screens.reset()


def _poll(x, now):
    room_screens.decide({"x": x, "y": 0.0}, now=now)


# ---- where "at the big screen" is ----------------------------------------------------

def test_in_by_the_rule_that_opens_the_screen_out_by_the_one_that_closes_it(table, rows):
    _poll(2.0, 0.0)
    assert room_screens.at_screen(0.0) is None, "one reading is not enough"
    _poll(2.0, 2.0)
    assert room_screens.at_screen(2.0) == ROOM
    _poll(3.6, 4.0)                                   # the edge band: still there
    assert room_screens.at_screen(4.0) == ROOM
    _poll(4.5, 6.0)
    assert room_screens.at_screen(6.0) == ROOM, "one reading out is not leaving"
    _poll(4.5, 8.0)
    assert room_screens.at_screen(8.0) is None
    states = [(r["state"], r["screen"]) for r in rows if r["event"] == "at_screen"]
    assert states == [("enter", ROOM), ("leave", ROOM)]
    assert [r for r in rows if r["event"] == "at_screen"][0]["distance_m"] == 2.0


def test_its_face_up_on_that_tv_counts_at_the_edge(table, rows):
    """28 Oct: staff opened the media room's screen with the robot 3.88-4.13 m
    from it (radius 3.0) -- the TV showed her face and the room watched it."""
    room_screens.STATE.update({"open": ROOM, "by": "staff"})
    _poll(3.9, 0.0)
    assert room_screens.at_screen(0.0) == ROOM
    _poll(4.2, 2.0)
    _poll(4.2, 4.0)
    assert room_screens.at_screen(4.0) is None


def test_a_presence_no_pose_confirms_runs_out(table):
    _poll(1.0, 0.0)
    _poll(1.0, 2.0)
    assert room_screens.at_screen(2.0) == ROOM
    room_screens.decide(None, now=4.0)                # the chassis stopped answering
    assert room_screens.at_screen(2.0 + room_screens.PRESENCE_STALE_S - 0.1) == ROOM
    assert room_screens.at_screen(2.0 + room_screens.PRESENCE_STALE_S + 0.1) is None


# ---- the listening gate -------------------------------------------------------------

@pytest.fixture
def at_room(monkeypatch):
    monkeypatch.setattr(settings, "focus_enabled", True)
    monkeypatch.setattr(settings, "focus_screen_rms", 0.03)
    monkeypatch.setattr(room_screens, "at_screen", lambda now=None: ROOM)
    focus.observe_faces([], T)                        # the camera runs, nobody in it


def test_a_clear_voice_at_the_screen_needs_no_face(at_room, rows):
    assert focus.may_open(T + 0.1, rms=0.031) == (True, focus.AT_SCREEN)
    assert focus.may_open(T + 0.2, rms=0.044) == (True, focus.AT_SCREEN)
    opened = [r for r in rows if r["event"] == "focus_open"]
    assert opened and opened[0]["reason"] == focus.AT_SCREEN and opened[0]["at_screen"]


def test_a_voice_under_the_level_is_still_refused_and_says_where_it_was(at_room, rows):
    assert focus.may_open(T + 0.1, rms=0.025) == (False, focus.NO_FACE)
    denied = [r for r in rows if r["event"] == "focus_deny"]
    assert denied and denied[0]["at_screen"] is True


def test_away_from_the_screen_the_rule_is_as_it_was(at_room, monkeypatch):
    monkeypatch.setattr(room_screens, "at_screen", lambda now=None: None)
    assert focus.may_open(T + 0.1, rms=0.031) == (False, focus.NO_FACE)


def test_off_by_default_even_at_the_screen(monkeypatch):
    monkeypatch.setattr(settings, "focus_enabled", True)
    monkeypatch.setattr(room_screens, "at_screen", lambda now=None: ROOM)
    assert settings.focus_screen_rms == 0
    assert focus.at_big_screen() is None
    focus.observe_faces([], T)
    assert focus.may_open(T + 0.1, rms=0.5) == (False, focus.NO_FACE)


def test_the_gate_lets_the_whole_first_sentence_in(at_room):
    """With the head hold: refused at 0.025, clear at 0.031 -- the model gets
    the sentence from its first word (the 28 Oct testers' case)."""
    from app.vad_gate import VadGate
    from tests.test_vad_gate import ScriptedDetector

    soft = (int(0.025 * 32767)).to_bytes(2, "little", signed=True) * 160
    clear = (int(0.031 * 32767)).to_bytes(2, "little", signed=True) * 160
    gate = VadGate(ScriptedDetector([True] * 41), prefix_padding_ms=300,
                   min_rms=0.02, floor_grace_s=0, head_hold_ms=1500)
    for _ in range(40):
        assert gate.feed(soft) == []
    actions = gate.feed(clear)
    assert [k for k, _ in actions] == ["start", "audio", "audio"]
    assert actions[1][1] == soft * 40


# ---- the call is not ended or left silent for want of a face ------------------------

def _session():
    from app import session as session_module

    sess = session_module.VoiceSession(None, provider_name="gemini", profile="condo")
    sess.summoned = True
    sess._greeting_turn_seen = True
    return sess


def _idle_run(monkeypatch, sess, iterations=4):
    from tests.test_focus import _idle_run as run

    return run(monkeypatch, sess, iterations)


def test_no_thirty_second_hang_up_at_the_screen(monkeypatch, rows):
    monkeypatch.setattr(settings, "focus_enabled", True)
    monkeypatch.setattr(settings, "idle_timeout_s", 120)
    focus.observe_faces([], time.monotonic())
    sess = _session()
    sess._last_heard_at = time.monotonic() - (sess.FOCUS_AWAY_S + 1)

    monkeypatch.setattr(settings, "focus_screen_rms", 0.03)
    monkeypatch.setattr(room_screens, "at_screen", lambda now=None: ROOM)
    assert _idle_run(monkeypatch, sess) == [], "hung up 'no face' at the screen"

    # ...but nobody heard for IDLE_TIMEOUT_S still ends it, on the voice's clock
    sess._last_heard_at = time.monotonic() - 121
    sent = _idle_run(monkeypatch, sess)
    assert sent and sent[-1]["type"] == "idle_timeout"
    assert [r for r in rows if r["event"] == "idle_timeout"][-1]["why"] is None


def test_away_from_the_screen_the_thirty_seconds_stay(monkeypatch, rows):
    monkeypatch.setattr(settings, "focus_enabled", True)
    monkeypatch.setattr(settings, "idle_timeout_s", 120)
    monkeypatch.setattr(settings, "focus_screen_rms", 0.03)
    monkeypatch.setattr(room_screens, "at_screen", lambda now=None: None)
    focus.observe_faces([], time.monotonic())
    sess = _session()
    sess._last_heard_at = time.monotonic() - (sess.FOCUS_AWAY_S + 1)
    sent = _idle_run(monkeypatch, sess)
    assert sent and sent[-1]["type"] == "idle_timeout"
    assert [r for r in rows if r["event"] == "idle_timeout"][-1]["why"] == "no_face"


def test_the_follow_up_question_is_not_vetoed_for_want_of_a_face_there(monkeypatch):
    monkeypatch.setattr(settings, "focus_enabled", True)
    now = time.monotonic()
    focus.observe_faces([], now)
    sess = _session()
    later = now + 5.0
    focus.observe_faces([], later)
    assert sess._follow_up_veto(later) == "nobody_in_front"
    monkeypatch.setattr(settings, "focus_screen_rms", 0.03)
    monkeypatch.setattr(room_screens, "at_screen", lambda now=None: ROOM)
    assert sess._follow_up_veto(later) is None


# ---- settings ------------------------------------------------------------------------

def test_the_robot_turns_it_on_and_the_pc_default_is_off():
    root = Path(__file__).resolve().parent.parent
    assert 'FOCUS_SCREEN_RMS", "0"' in (root / "app" / "config.py").read_text(encoding="utf-8")
    env = (root / "data" / "robot-server" / "robot.env").read_text(encoding="utf-8")
    assert "\nFOCUS_SCREEN_RMS=0.03\n" in env
    assert "\nROOM_SCREENS_ENABLED=true\n" in env, "at_screen needs the screens' watcher"
