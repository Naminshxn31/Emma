"""SPEECH_GESTURES: Emma's arms move right, then left, while she talks.

Owner, 15 Oct: "ให้ขยับซ้ายขวาสลับกันตอนคุย". The tests drive the real
`speech_gestures` pacing, the real `robot_body` gates and the real
`robot_face_link` with a fake face page that answers through
`robot_face_link.receive` -- nothing reaches a robot.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import time

import pytest

from app import focus, robot_face_link as link, speech_gestures
from app.config import Settings, settings

MAPPED = {"raise_right_arm": "operator_mapped", "raise_left_arm": "operator_mapped"}


class Page:
    def __init__(self, answer):
        self.sent: list[dict] = []
        self.answer = answer
        self.session = None
        self.headers = {"user-agent": "Mozilla/5.0 EmmaRobot/1.0"}

    async def send_text(self, text: str) -> None:
        frame = json.loads(text)
        self.sent.append(frame)
        ack = self.answer(frame)
        if ack is not None:
            asyncio.get_running_loop().call_soon(
                link.receive, self.session,
                {"type": "body_ack", "command_id": frame["command_id"],
                 "action": frame["action"], **ack})

    def commands(self) -> list[str]:
        return [f["action"] for f in self.sent if f["type"] == "body_command"]


class Face:
    """The robot face's session as speech_gestures sees it: a socket and the
    server's playback clock of her voice."""

    def __init__(self, answer=None, claim=True):
        self.ws = Page(answer or (lambda f: {"accepted": True, "reason": None,
                                             "status": "operator_mapped", "est_ms": 6000}))
        self.ws.session = self
        self._play_until = 0.0
        if claim:
            link.claim(self)

    def speaks_for(self, seconds: float) -> None:
        self._play_until = time.monotonic() + seconds


def caps(face, gestures=MAPPED, enabled=True):
    link.receive(face, {"type": "body_caps", "enabled": enabled,
                        "allow_direction_known": False, "gestures": dict(gestures)})


@pytest.fixture
def on(monkeypatch):
    for flag in ("robot_body_enabled", "robot_arm_enabled", "robot_arm_motion_enabled",
                 "speech_gestures"):
        monkeypatch.setattr(settings, flag, True)
    monkeypatch.setattr(settings, "robot_body_ack_timeout_s", 0.5)
    monkeypatch.setattr(settings, "speech_gesture_first_s", 0.0)
    monkeypatch.setattr(settings, "speech_gesture_min_left_s", 1.0)
    monkeypatch.setattr(settings, "speech_gesture_gap_s", 0.15)
    rows = []
    from app import turnlog

    monkeypatch.setattr(turnlog, "record", lambda kind, **f: rows.append((kind, f)))
    return rows


def talk(face, seconds, run_for):
    """Her reply arrives (one audio chunk) and plays for `seconds`; the test
    watches for `run_for` seconds."""

    async def scenario():
        face.speaks_for(seconds)
        speech_gestures.on_audio(face)
        await asyncio.sleep(run_for)
        speech_gestures.on_closed(face)

    asyncio.run(scenario())


def test_off_by_default():
    field = {f.name: f for f in dataclasses.fields(Settings)}["speech_gestures"]
    assert field.default is False, "the sales room PC must not change on a pull"
    face = Face()
    caps(face)
    talk(face, 10, 0.3)
    assert face.ws.commands() == []


def test_a_long_answer_moves_right_then_left(on):
    face = Face()
    caps(face)
    talk(face, 1.6, 1.0)
    sent = face.ws.commands()
    assert sent[:2] == ["raise_right_arm", "raise_left_arm"], sent
    assert all(a != b for a, b in zip(sent, sent[1:])), "sides alternate"
    assert len(sent) <= 5, "stops once less than MIN_LEFT of her voice is left"


def test_the_next_answer_continues_with_the_other_side(on, monkeypatch):
    monkeypatch.setattr(settings, "speech_gesture_gap_s", 5.0)
    face = Face()
    caps(face)
    talk(face, 3, 0.2)
    talk(face, 3, 0.2)
    assert face.ws.commands() == ["raise_right_arm"], "the gap holds across answers"
    monkeypatch.setattr(settings, "speech_gesture_gap_s", 0.0)
    talk(face, 3, 0.2)
    assert face.ws.commands()[1] == "raise_left_arm"


def test_a_short_reply_gets_no_gesture(on):
    face = Face()
    caps(face)
    talk(face, 0.6, 0.3)        # less than min_left (1.0) to play
    assert face.ws.commands() == []


def test_a_pose_the_robot_cannot_play_is_never_sent(on):
    face = Face()
    caps(face, gestures={"raise_right_arm": "needs_mapping", "raise_left_arm": "needs_mapping"})
    talk(face, 3, 0.3)
    talk(face, 3, 0.3)
    assert face.ws.commands() == []
    skipped = [f for k, f in on if k == "speech_gesture"]
    assert skipped == [{"action": "raise_right_arm", "sent": False, "reason": "not_playable"}], \
        "one row per reason, not one per answer"


def test_the_robots_switch_off_sends_nothing(on):
    face = Face()
    caps(face, enabled=False)
    talk(face, 3, 0.3)
    assert face.ws.commands() == []


def test_the_servers_keys_and_the_desk_disarm_hold(on, monkeypatch):
    from app import robot_arm

    face = Face()
    caps(face)
    monkeypatch.setattr(settings, "robot_body_enabled", False)
    talk(face, 3, 0.3)
    monkeypatch.setattr(settings, "robot_body_enabled", True)
    monkeypatch.setattr(robot_arm, "_armed", False)
    talk(face, 3, 0.3)
    assert face.ws.commands() == []


def test_the_desk_page_never_gestures(on):
    face = Face(claim=False)
    talk(face, 3, 0.3)
    assert face.ws.commands() == []


def test_a_refusal_keeps_the_side(on):
    answers = iter([{"accepted": False, "reason": "busy"}] + [{"accepted": True, "reason": None}] * 5)
    face = Face(answer=lambda f: next(answers))
    caps(face)
    talk(face, 1.5, 0.5)
    sent = face.ws.commands()
    assert sent[:2] == ["raise_right_arm", "raise_right_arm"], "busy: the same side again later"


def test_a_cooldown_is_asked_again_soon_not_after_the_whole_gap(on, monkeypatch):
    """Robot 15 Oct: 'cooldown' 3 s after the greeting's gesture, then the
    full gap lost ten seconds of the answer."""
    monkeypatch.setattr(settings, "speech_gesture_gap_s", 30.0)
    monkeypatch.setattr(speech_gestures, "RETRY_AFTER_S", 0.2)
    answers = iter([{"accepted": False, "reason": "cooldown"}, {"accepted": True, "reason": None}])
    face = Face(answer=lambda f: next(answers))
    caps(face)
    talk(face, 5, 0.6)
    assert face.ws.commands() == ["raise_right_arm", "raise_right_arm"]


def test_a_refusal_that_will_not_pass_soon_waits_the_whole_gap(on, monkeypatch):
    monkeypatch.setattr(settings, "speech_gesture_gap_s", 30.0)
    monkeypatch.setattr(speech_gestures, "RETRY_AFTER_S", 0.2)
    face = Face(answer=lambda f: {"accepted": False, "reason": "latched"})
    caps(face)
    talk(face, 5, 0.6)
    assert face.ws.commands() == ["raise_right_arm"]


def test_a_barge_in_stops_further_gestures(on, monkeypatch):
    monkeypatch.setattr(settings, "speech_gesture_gap_s", 0.3)
    face = Face()
    caps(face)

    async def scenario():
        face.speaks_for(5)
        speech_gestures.on_audio(face)
        await asyncio.sleep(0.1)
        speech_gestures.on_interrupted(face)
        await asyncio.sleep(0.6)

    asyncio.run(scenario())
    assert face.ws.commands() == ["raise_right_arm"]


def test_an_app_that_plays_beats_gets_the_speech_style_and_the_beat_pace(on, monkeypatch):
    """Owner 15 Oct, after the first build: "ท่ายังช้าแล้วดูไม่เป็นธรรมชาติ". A build
    whose caps say speech plays back to back -- the BEAT knobs pace it."""
    monkeypatch.setattr(settings, "speech_gesture_gap_s", 30.0)
    monkeypatch.setattr(settings, "speech_gesture_min_left_s", 30.0)
    monkeypatch.setattr(settings, "speech_gesture_beat_gap_s", 0.15)
    monkeypatch.setattr(settings, "speech_gesture_beat_min_left_s", 1.0)
    face = Face()
    link.receive(face, {"type": "body_caps", "enabled": True, "allow_direction_known": False,
                        "gestures": dict(MAPPED), "speech": True})
    talk(face, 1.6, 1.0)
    frames = [f for f in face.ws.sent if f["type"] == "body_command"]
    assert [f["action"] for f in frames][:2] == ["raise_right_arm", "raise_left_arm"]
    assert all(f.get("style") == "speech" for f in frames)


def test_an_older_app_gets_the_plain_command(on):
    face = Face()
    caps(face)                       # no "speech" in its caps
    talk(face, 1.6, 0.3)
    frames = [f for f in face.ws.sent if f["type"] == "body_command"]
    assert frames and all("style" not in f for f in frames)


@pytest.mark.parametrize("payload", [
    {"action": "nod", "style": "speech"},             # a head id
    {"action": "handshake", "style": "speech"},       # a vendor group
    {"action": "raise_left_arm", "style": "fast"},    # any other style
])
def test_the_link_sends_a_style_only_with_a_speech_pose(on, payload):
    face = Face()

    async def scenario():
        return await link.request(face, "body", payload, 0.2)

    ack = asyncio.run(scenario())
    assert ack["reason"] == "bad_request" and face.ws.sent == []


def test_focus_does_not_count_her_explaining_hand_as_moving(on, monkeypatch):
    from app import robot_body

    now = time.time()
    speech_gestures.LAST.update(action="raise_right_arm", at=now)
    robot_body.STATE.update(last_action="raise_right_arm", state="started", at=now + 0.3)
    assert focus._robot_moving() is False
    # a guest's "ยกแขนซ้าย" (another id) is still a moving robot
    robot_body.STATE.update(last_action="raise_left_arm", state="started", at=now + 0.3)
    assert focus._robot_moving() is True
    # and so is the same id asked for long after ours
    robot_body.STATE.update(last_action="raise_right_arm", state="started", at=now + 30)
    assert focus._robot_moving() is True
