"""CEREMONY_HEAD_STILL: in a ceremony call the head does not move -- the arms speak.

Owner, 24 Nov 2026: "ไม่ต้องให้หุ่นหันหัวไปมาตอนพูดแล้วให้ขยับมือเหมือนที่ทำตอนแรกพอ".
The tests drive the real `head_follow` controller and the real `speech_gestures` over the real
`robot_face_link`, with a fake face page -- nothing reaches a robot.
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from pathlib import Path

import pytest

from app import facewatch, focus, head_follow, robot_arm, robot_face_link as link, speech_gestures
from app.config import settings

ROOT = Path(__file__).resolve().parents[1]
W, H = 1024, 768


class Page:
    def __init__(self, answer=None):
        self.sent: list[dict] = []
        self.answer = answer
        self.session = None
        self.headers = {"user-agent": "Mozilla/5.0 EmmaRobot/1.0"}

    async def send_text(self, text: str) -> None:
        frame = json.loads(text)
        self.sent.append(frame)
        if frame.get("type") == "body_command" and self.answer is not None:
            asyncio.get_running_loop().call_soon(
                link.receive, self.session,
                {"type": "body_ack", "command_id": frame["command_id"], "action": frame["action"],
                 "accepted": True, "reason": None, "status": "operator_mapped", "est_ms": 6000})

    def looks(self) -> list[tuple[int, int]]:
        return [(f["pan"], f["tilt"]) for f in self.sent if f["type"] == "head_look"]

    def commands(self) -> list[str]:
        return [f["action"] for f in self.sent if f["type"] == "body_command"]


class Face:
    def __init__(self, profile="ceremony", answer=True):
        self.profile = profile
        self.ws = Page(answer=answer or None)
        self.ws.session = self
        self._play_until = 0.0
        link.claim(self)
        link.receive(self, {"type": "body_caps", "enabled": True, "allow_direction_known": False,
                            "gestures": {"raise_right_arm": "operator_mapped",
                                         "raise_left_arm": "operator_mapped"},
                            "speech": True, "look": True})

    def speaks_for(self, seconds: float) -> None:
        self._play_until = time.monotonic() + seconds


@pytest.fixture
def on(monkeypatch):
    for flag in ("head_follow", "ceremony_head_still", "robot_body_enabled", "robot_arm_enabled",
                 "robot_arm_motion_enabled", "speech_gestures"):
        monkeypatch.setattr(settings, flag, True)
    monkeypatch.setattr(settings, "head_calibrate", True)     # a ceremony call must NOT sweep
    monkeypatch.setattr(settings, "head_walk_scan", True)
    monkeypatch.setattr(settings, "head_search_tilt", 30)
    monkeypatch.setattr(settings, "head_search_rms", 0.06)
    monkeypatch.setattr(settings, "head_pan_full_deg", 20.0)
    monkeypatch.setattr(settings, "head_tilt_full_deg", 20.0)
    monkeypatch.setattr(settings, "robot_body_ack_timeout_s", 0.5)
    monkeypatch.setattr(settings, "speech_gesture_first_s", 0.0)
    monkeypatch.setattr(settings, "speech_gesture_min_left_s", 1.0)
    monkeypatch.setattr(settings, "speech_gesture_gap_s", 0.15)
    monkeypatch.setattr(settings, "speech_gesture_beat_gap_s", 0.15)
    monkeypatch.setattr(settings, "speech_gesture_beat_min_left_s", 1.0)
    from app import turnlog

    monkeypatch.setattr(turnlog, "record", lambda kind, **f: None)
    head_follow.reset()
    yield
    head_follow.reset()


class Scene:
    """Everything that would turn a head: a face to the side, a loud voice, a lidar person."""

    def __init__(self, monkeypatch):
        self.cue = {"bearing": None, "source": None, "track": None}
        self.tracks: list[tuple] = []
        self.voice = (None, None)
        monkeypatch.setattr(focus, "attention", lambda t=None: dict(self.cue))
        monkeypatch.setattr(focus, "last_voice", lambda: self.voice)
        monkeypatch.setattr(facewatch, "latest_tracks", lambda: list(self.tracks))
        monkeypatch.setattr(facewatch, "latest_frame_size", lambda: (W, H))

    def face(self, bearing, t, track=1):
        self.cue = {"bearing": bearing, "source": "face", "track": track}
        self.tracks = [(track, (400.0, H / 2 - 80, 560.0, H / 2 + 80), None, t)]


def tick(follow, now):
    asyncio.run(follow.tick(now))


def _everything_that_turns_a_head(scene, face, follow, monkeypatch, start=100.0):
    """A face off to the side, a loud voice, a look-down hint from the walk-up, a walk with the base
    standing -- ticked for a minute."""
    now = start
    for i in range(30):
        now = start + i * 2.0
        scene.face(25.0 if i % 2 == 0 else -25.0, t=now)
        scene.voice = (now, 0.5)
        if i == 5:
            head_follow.look_hint(18, 30)
        if i >= 20:
            monkeypatch.setattr(head_follow, "_walking", lambda s: True)
            monkeypatch.setattr(head_follow, "_base_still_s", lambda: 5.0)
        tick(follow, now)
    return now


def test_a_ceremony_call_sends_one_centre_and_no_other_head_move(on, monkeypatch):
    scene = Scene(monkeypatch)
    face = Face("ceremony")
    follow = head_follow.HeadFollow(face)
    _everything_that_turns_a_head(scene, face, follow, monkeypatch)
    assert face.ws.looks() == [(0, 0)], "one look to centre at the start of the call, nothing else"
    assert head_follow.offset_deg() == (0.0, 0.0)


def test_the_centre_is_sent_even_when_the_head_is_believed_to_be_there_already(on, monkeypatch):
    """Where the head was left (a pageant, a call that ended off centre) is nobody's guess."""
    Scene(monkeypatch)
    head_follow._HEAD["known"] = False
    face = Face("ceremony")
    follow = head_follow.HeadFollow(face)
    tick(follow, 100.0)
    assert face.ws.looks() == [(0, 0)]
    assert head_follow._HEAD["known"] is True
    tick(follow, 110.0)
    tick(follow, 120.0)
    assert face.ws.looks() == [(0, 0)], "once"


def test_no_calibration_sweep_in_a_ceremony_call(on, monkeypatch):
    Scene(monkeypatch)
    monkeypatch.setattr(head_follow, "_calibration", lambda: None)
    face = Face("ceremony")
    follow = head_follow.HeadFollow(face)
    tick(follow, 100.0)
    tick(follow, 110.0)
    assert face.ws.looks() == [(0, 0)], "a sweep would turn the head left, right and down"
    assert follow.off is None


def test_the_sales_call_is_not_touched(on, monkeypatch):
    scene = Scene(monkeypatch)
    face = Face("condo")
    follow = head_follow.HeadFollow(face)
    monkeypatch.setattr(settings, "head_calibrate", False)
    scene.face(25.0, t=100.0)
    tick(follow, 100.0)
    tick(follow, 102.0)
    assert any(pan != 0 for pan, _tilt in face.ws.looks()), "a sales call still turns to the face"


def test_the_event_call_is_not_touched(on, monkeypatch):
    scene = Scene(monkeypatch)
    face = Face("event")
    follow = head_follow.HeadFollow(face)
    monkeypatch.setattr(settings, "head_calibrate", False)
    scene.face(25.0, t=100.0)
    tick(follow, 100.0)
    tick(follow, 102.0)
    assert any(pan != 0 for pan, _tilt in face.ws.looks())


def test_the_switch_off_gives_todays_behaviour(on, monkeypatch):
    scene = Scene(monkeypatch)
    monkeypatch.setattr(settings, "ceremony_head_still", False)
    monkeypatch.setattr(settings, "head_calibrate", False)
    face = Face("ceremony")
    follow = head_follow.HeadFollow(face)
    scene.face(25.0, t=100.0)
    tick(follow, 100.0)
    tick(follow, 102.0)
    assert any(pan != 0 for pan, _tilt in face.ws.looks())


def test_the_switch_is_off_in_code_and_pinned_off_for_tests():
    source = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    assert 'ceremony_head_still: bool = _get_bool("CEREMONY_HEAD_STILL", False)' in source
    robot_env = (ROOT / "data" / "robot-server" / "robot.env").read_text(encoding="utf-8")
    assert "\nCEREMONY_HEAD_STILL=true" in robot_env


def test_staff_body_commands_still_reach_the_head(on, monkeypatch):
    """The still head is head_follow's loop only: a body command (move_body, the staff pages) goes
    round it and is sent as ever."""
    Scene(monkeypatch)
    face = Face("ceremony")

    async def scenario():
        return await link.request(face, "body", {"action": "raise_right_arm"}, 0.5)

    ack = asyncio.run(scenario())
    assert ack.get("accepted") is True
    assert face.ws.commands() == ["raise_right_arm"]


# ---- her arms while she speaks in the ceremony ---------------------------------------


def talk(face, seconds, run_for):
    async def scenario():
        face.speaks_for(seconds)
        speech_gestures.on_audio(face)
        await asyncio.sleep(run_for)
        speech_gestures.on_closed(face)

    asyncio.run(scenario())


def test_a_ceremony_live_answer_alternates_right_then_left_with_the_head_untouched(on, monkeypatch):
    Scene(monkeypatch)
    face = Face("ceremony")
    follow = head_follow.HeadFollow(face)
    tick(follow, 100.0)
    talk(face, 1.6, 1.0)
    sent = face.ws.commands()
    assert sent[:2] == ["raise_right_arm", "raise_left_arm"], sent
    assert {f.get("style") for f in face.ws.sent if f["type"] == "body_command"} == {"speech"}
    tick(follow, 110.0)
    assert face.ws.looks() == [(0, 0)], "the arms spoke; the head did not"


def test_no_gesture_while_the_base_walks(on, monkeypatch):
    face = Face("ceremony")
    monkeypatch.setattr(link, "server_walking", lambda: True)
    talk(face, 3, 0.5)
    assert face.ws.commands() == [], "refused as navigating before it leaves the server"


def test_no_gesture_after_a_stop(on, monkeypatch):
    face = Face("ceremony")
    monkeypatch.setattr(robot_arm, "_armed", False)
    talk(face, 3, 0.4)
    assert face.ws.commands() == []


def test_no_gesture_while_the_pageant_has_the_arm(on, monkeypatch):
    from app import event_pageant

    face = Face("ceremony")
    monkeypatch.setattr(event_pageant, "claims_arm", lambda: True)
    talk(face, 3, 0.4)
    assert face.ws.commands() == []


def test_a_barge_in_ends_the_gestures_of_that_answer(on):
    face = Face("ceremony")

    async def scenario():
        face.speaks_for(5)
        speech_gestures.on_audio(face)
        await asyncio.sleep(0.05)
        speech_gestures.on_interrupted(face)
        n = len(face.ws.commands())
        await asyncio.sleep(0.5)
        return n, len(face.ws.commands())

    before, after = asyncio.run(scenario())
    assert before == after


def test_the_speech_switch_off_sends_no_gesture(on, monkeypatch):
    monkeypatch.setattr(settings, "speech_gestures", False)
    face = Face("ceremony")
    talk(face, 3, 0.4)
    assert face.ws.commands() == []


@pytest.mark.parametrize("gesture", ["_pageant", "_tour"])
def test_after_card_1s_pageant_or_tour_the_head_is_centred_once_more(on, monkeypatch, gesture):
    """Card 1 runs inside the ceremony call (24 Nov, walking chat review): its pageant (group 99) and its handshake
    (group 6) leave the head where nobody knows. One more centre after it, then still again -- not left off centre
    for the rest of the ceremony."""
    scene = Scene(monkeypatch)
    face = Face("ceremony")
    follow = head_follow.HeadFollow(face)
    tick(follow, 100.0)
    assert face.ws.looks() == [(0, 0)]
    monkeypatch.setattr(head_follow, gesture, lambda: True)
    tick(follow, 110.0)
    tick(follow, 112.0)
    assert face.ws.looks() == [(0, 0)], "nothing while card 1 has the head"
    monkeypatch.setattr(head_follow, gesture, lambda: False)
    scene.face(25.0, t=120.0)
    tick(follow, 120.0)
    assert face.ws.looks() == [(0, 0), (0, 0)], "one centre after it -- not a look at the face"
    for k in range(5):
        scene.face(-25.0, t=130.0 + k)
        tick(follow, 130.0 + k * 2)
    assert face.ws.looks() == [(0, 0), (0, 0)], "and still again"


def test_after_an_arm_gesture_ends_the_head_is_centred_again_once(on, monkeypatch):
    """Owner 25 Nov ~01:55: "ทำไมกลับมาไม่ตรงเหมือนตอนรี" -- group 99 (rest, after every arm gesture), group 6 and group 3
    drive the head channel too; nothing told the head back. A gesture's end = the head is not known: centred once more."""
    from app import robot_body

    Scene(monkeypatch)
    face = Face("ceremony")
    follow = head_follow.HeadFollow(face)
    tick(follow, 100.0)
    assert face.ws.looks() == [(0, 0)]
    tick(follow, 105.0)
    assert face.ws.looks() == [(0, 0)]
    robot_body._on_status(face, {"type": "body_status", "seq": 7, "action": "raise_right_arm", "state": "started"})
    tick(follow, 106.0)
    assert face.ws.looks() == [(0, 0)]                                       # (not while it runs)
    robot_body._on_status(face, {"type": "body_status", "seq": 7, "action": "raise_right_arm", "state": "done"})
    tick(follow, 108.0)
    tick(follow, 112.0)
    assert face.ws.looks() == [(0, 0), (0, 0)], "one more centre after the gesture, then nothing"
    robot_body._on_status(face, {"type": "body_status", "seq": 8, "action": "stop", "state": "done"})
    tick(follow, 120.0)
    assert face.ws.looks() == [(0, 0), (0, 0)]                               # (a stop moves no group)


def test_every_ceremony_centre_is_written_down(on, monkeypatch):
    """25 Nov: two centres after group 99 went out 2-3 s after a logged one and the 5 s log throttle hid them -- the log
    read as "no centre after the rest". Every ceremony centre now has its row."""
    from app import robot_body

    rows = []
    monkeypatch.setattr(head_follow, "_record", lambda **kw: rows.append(kw))
    Scene(monkeypatch)
    face = Face("ceremony")
    follow = head_follow.HeadFollow(face)
    tick(follow, 100.0)
    for k, at in enumerate((101.0, 103.0, 104.5)):                          # (three gestures ending 1.5-2 s apart)
        robot_body._on_status(face, {"type": "body_status", "seq": 20 + k, "action": "rest", "state": "done"})
        tick(follow, at)
    assert len(face.ws.looks()) == 4
    assert [r["why"] for r in rows] == ["ceremony_centre"] * 4



def test_a_refused_centre_is_asked_again_after_a_while_not_forgotten(on, monkeypatch):
    """Review 25 Nov: a red stop mid-handshake -> "stopped" -> the centre look -> the app refuses it (its 60 s latch) ->
    nothing asked again, and the head stayed where the stop froze it. Now: asked again every TRIM_RESEND_S."""
    from app import robot_body

    Scene(monkeypatch)
    face = Face("ceremony")
    follow = head_follow.HeadFollow(face)
    tick(follow, 100.0)
    robot_body._on_status(face, {"type": "body_status", "seq": 7, "action": "handshake", "state": "stopped"})
    tick(follow, 101.0)
    assert len(face.ws.looks()) == 2
    seq = [f["seq"] for f in face.ws.sent if f["type"] == "head_look"][-1]
    monkeypatch.setattr(head_follow, "_clock", lambda: 101.2)
    follow.ack({"type": "head_look_ack", "seq": seq, "accepted": False, "reason": "latched", "ms": None})
    tick(follow, 102.0)
    assert len(face.ws.looks()) == 2                                 # (not straight away: the latch answers every ask)
    tick(follow, 101.2 + head_follow.TRIM_RESEND_S + 0.1)
    assert len(face.ws.looks()) == 3                                 # (asked again)
