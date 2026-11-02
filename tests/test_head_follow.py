"""HEAD_FOLLOW: the robot's real head turns toward the person she talks with.

Owner, 31 Oct 2026: "เวลาคุยอยากให้หุ่นลองหมุนหัวเพื่อหันหน้าไปคุยกับคน ...
ก้มหน้าหรือขึ้นลง เพื่อจับหน้าคน". The tests drive the real `head_follow`
controller, the real `robot_face_link.look` and the real facewatch dedupe
helper, with a fake face page -- nothing reaches a robot.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import math
from pathlib import Path

import numpy as np
import pytest

from app import facewatch, focus, head_follow, robot_face_link as link
from app.config import Settings, settings

ROOT = Path(__file__).resolve().parents[1]
W, H = 1024, 768


class Page:
    def __init__(self):
        self.sent: list[dict] = []
        self.headers = {"user-agent": "Mozilla/5.0 EmmaRobot/1.0"}

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    def looks(self) -> list[tuple[int, int]]:
        return [(f["pan"], f["tilt"]) for f in self.sent if f["type"] == "head_look"]


class Face:
    def __init__(self, look=True):
        self.ws = Page()
        link.claim(self)
        link.receive(self, {"type": "body_caps", "enabled": True, "allow_direction_known": False,
                            "gestures": {}, "speech": True, **({"look": True} if look else {})})


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(settings, "head_follow", True)
    monkeypatch.setattr(settings, "head_pan_full_deg", 20.0)
    monkeypatch.setattr(settings, "head_tilt_full_deg", 20.0)
    monkeypatch.setattr(settings, "head_pan_max", 60)
    monkeypatch.setattr(settings, "head_tilt_down_max", 40)
    monkeypatch.setattr(settings, "head_tilt_up_max", 20)
    monkeypatch.setattr(settings, "head_search_tilt", 30)
    monkeypatch.setattr(settings, "head_search_rms", 0.06)
    # the loop's own tests run on the two numbers above; the sweep has its own
    monkeypatch.setattr(settings, "head_calibrate", False)
    rows = []
    from app import turnlog

    monkeypatch.setattr(turnlog, "record", lambda kind, **f: rows.append((kind, f)))
    head_follow.reset()
    yield rows
    head_follow.reset()


class Scene:
    """What focus and facewatch would say: one face (or none) and a voice."""

    def __init__(self, monkeypatch):
        self.cue = {"bearing": None, "source": None, "track": None}
        self.tracks: list[tuple] = []
        self.voice = (None, None)
        monkeypatch.setattr(focus, "attention", lambda t=None: dict(self.cue))
        monkeypatch.setattr(focus, "last_voice", lambda: self.voice)
        monkeypatch.setattr(facewatch, "latest_tracks", lambda: list(self.tracks))
        monkeypatch.setattr(facewatch, "latest_frame_size", lambda: (W, H))

    def face(self, bearing, t, cy=H / 2, track=1, source="face"):
        self.cue = {"bearing": bearing, "source": source, "track": track}
        self.tracks = [(track, (400.0, cy - 80, 560.0, cy + 80), None, t)]

    def nobody(self):
        self.cue = {"bearing": None, "source": None, "track": None}
        self.tracks = []


def tick(follow, now):
    asyncio.run(follow.tick(now))


def test_off_by_default_in_code_and_nothing_is_sent():
    source = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    assert 'head_follow: bool = _get_bool("HEAD_FOLLOW", False)' in source, \
        "the sales room PC and a robot that pulls must not start moving a servo"
    assert {f.name for f in dataclasses.fields(Settings)} >= {"head_follow"}
    face = Face()
    follow = head_follow.HeadFollow(face)
    tick(follow, 100.0)
    assert face.ws.looks() == []
    assert head_follow.offset_deg() == (0.0, 0.0)


def test_a_face_to_the_left_turns_the_head_left_in_steps(on, monkeypatch):
    scene = Scene(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    scene.face(15.0, t=100.0)                       # + = the robot's left
    tick(follow, 100.0)
    # GAIN 0.6 of 15 deg over a 20 deg full span = 45 % -> a step, not a jump
    assert face.ws.looks() == [(45, 0)]
    assert head_follow.offset_deg() == (9.0, 0.0)
    # the same bearing again from a frame taken BEFORE the move settled: not
    # corrected twice (the overshoot a camera on the head would cause)
    scene.face(15.0, t=100.5)
    tick(follow, 101.2)
    assert face.ws.looks() == [(45, 0)]
    # a frame after the move: the error left (6 deg) is the next step, clamped
    scene.face(6.0, t=101.5)
    tick(follow, 101.6)
    assert face.ws.looks() == [(45, 0), (60, 0)], "HEAD_PAN_MAX caps the runtime range"


def test_a_face_to_the_right_turns_right_and_the_deadband_holds_still(on, monkeypatch):
    scene = Scene(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    scene.face(-10.0, t=100.0)
    tick(follow, 100.0)
    assert face.ws.looks() == [(-30, 0)]
    scene.face(3.0, t=102.0)                        # inside the deadband
    tick(follow, 102.5)
    assert face.ws.looks() == [(-30, 0)]


def test_one_look_a_second_at_most(on, monkeypatch):
    scene = Scene(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    scene.face(15.0, t=100.0)
    tick(follow, 100.0)
    scene.face(15.0, t=100.95)
    tick(follow, 100.96)                            # fresh frame, but < SEND_GAP_S
    assert len(face.ws.looks()) == 1


def test_a_face_above_tilts_up_and_below_tilts_down_within_their_limits(on, monkeypatch):
    scene = Scene(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    scene.face(0.0, t=100.0, cy=150)                # well above the axis
    tick(follow, 100.0)
    pan, tilt = face.ws.looks()[-1]
    assert pan == 0 and tilt == -20, "up is capped at HEAD_TILT_UP_MAX"
    scene.face(0.0, t=102.0, cy=700)                # below
    tick(follow, 102.0)
    assert face.ws.looks()[-1][1] > -20


def test_no_target_for_a_while_brings_the_head_back_to_centre(on, monkeypatch):
    scene = Scene(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    scene.face(15.0, t=100.0)
    tick(follow, 100.0)
    scene.nobody()
    tick(follow, 103.0)
    assert face.ws.looks() == [(45, 0)]
    tick(follow, 100.0 + head_follow.IDLE_S + 0.1)
    assert face.ws.looks() == [(45, 0), (0, 0)]
    assert head_follow.offset_deg() == (0.0, 0.0)


def test_a_loud_voice_with_no_face_looks_down_once_and_room_chatter_does_not(on, monkeypatch):
    scene = Scene(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    scene.nobody()
    scene.voice = (100.0, 0.03)                     # room chatter level
    tick(follow, 101.5)
    assert face.ws.looks() == []
    scene.voice = (102.0, 0.08)                     # a voice at the robot
    tick(follow, 102.5)
    assert face.ws.looks() == [(0, 30)]
    scene.voice = (104.0, 0.09)
    tick(follow, 104.5)
    assert face.ws.looks() == [(0, 30)], "not repeated within SEARCH_EVERY_S"
    # it holds the look down for SEARCH_HOLD_S, then centres
    tick(follow, 102.5 + head_follow.SEARCH_HOLD_S + 0.1)
    assert face.ws.looks() == [(0, 30), (0, 0)]


def test_walking_centres_the_head_once_and_then_sends_nothing(on, monkeypatch):
    scene = Scene(monkeypatch)
    from app import event_walk

    face = Face()
    follow = head_follow.HeadFollow(face)
    scene.face(15.0, t=100.0)
    tick(follow, 100.0)
    monkeypatch.setattr(event_walk, "walking", lambda: True)
    scene.face(20.0, t=102.0)
    tick(follow, 102.0)
    tick(follow, 104.0)
    assert face.ws.looks() == [(45, 0), (0, 0)]


def test_look_goes_only_to_a_robot_face_that_says_it_can(on):
    plain = Face(look=False)
    assert asyncio.run(link.look(plain, 10, 0)) is False, "an older APK is never asked"
    face = Face()
    assert asyncio.run(link.look(face, 10, -5)) is True
    assert face.ws.sent[-1] == {"type": "head_look", "pan": 10, "tilt": -5}
    assert asyncio.run(link.look(face, 10, -5, seq=3)) is True
    assert face.ws.sent[-1] == {"type": "head_look", "pan": 10, "tilt": -5, "seq": 3}
    for bad in ((101, 0), (0, -101), (True, 0), (1.5, 0), ("10", 0)):
        assert asyncio.run(link.look(face, *bad)) is False, bad
    for seq in (0, -1, True, 1.5, "3", 2 ** 31):
        assert asyncio.run(link.look(face, 1, 1, seq=seq)) is False, seq
    stranger = Page()

    class NotClaimed:
        ws = stranger

    assert asyncio.run(link.look(NotClaimed(), 10, 0)) is False
    assert stranger.sent == []


def test_look_never_rides_the_status_link(on):
    class Ws(Page):
        pass

    holder = link.StatusLink(Ws())
    link.claim(holder)
    link.receive(holder, {"type": "body_caps", "enabled": True, "allow_direction_known": False,
                          "gestures": {}, "look": True})
    assert asyncio.run(link.look(holder, 10, 0)) is False


def test_head_look_rows_carry_numbers_only(on, monkeypatch):
    scene = Scene(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    scene.face(15.0, t=100.0)
    tick(follow, 100.0)
    rows = [f for kind, f in on if kind == "head_look"]
    assert rows == [{"why": "face", "pan": 45, "tilt": 0, "bearing": 15.0, "elev": 0.0}]


# ---- facewatch: a box remembered before the head turned ------------------

def test_a_box_from_before_a_turn_is_moved_to_where_that_spot_is_now(on, monkeypatch):
    frame = np.zeros((H, W, 3), dtype=np.uint8)
    box = (100.0, 300.0, 260.0, 460.0)
    monkeypatch.setattr(head_follow, "_HEAD", {"pan_deg": 0.0, "tilt_deg": 0.0})
    assert facewatch._in_head_frame(box, (0.0, 0.0), frame) == box
    # the head turned 10 deg to the robot's left: the scene slides right
    monkeypatch.setattr(head_follow, "_HEAD", {"pan_deg": 10.0, "tilt_deg": 0.0})
    moved = facewatch._in_head_frame(box, (0.0, 0.0), frame)
    focal = (W / 2) / math.tan(math.radians(facewatch.ROBOT_CAMERA_HFOV_DEG / 2))
    assert moved[0] - box[0] == pytest.approx(focal * math.tan(math.radians(10)))
    assert moved[1] == box[1]
    # a person centred by the turn overlaps their own greeting box again
    person_now = tuple(v + focal * math.tan(math.radians(10)) for v in (box[0],)) + (300.0,)
    person_now = (person_now[0], 300.0, person_now[0] + 160.0, 460.0)
    assert facewatch._overlap(person_now, box) < facewatch.Watcher.SAME_SPOT_IOU
    assert facewatch._overlap(person_now, moved) > 0.9
    # tilted down: the scene moves up
    monkeypatch.setattr(head_follow, "_HEAD", {"pan_deg": 0.0, "tilt_deg": 5.0})
    assert facewatch._in_head_frame(box, (0.0, 0.0), frame)[1] < box[1]
    # an entry from before this existed (no "head") is compared as it was
    assert facewatch._in_head_frame(box, None, frame) == box


def test_with_head_follow_off_the_dedupe_is_unchanged(monkeypatch):
    monkeypatch.setattr(settings, "head_follow", False)
    monkeypatch.setattr(head_follow, "_HEAD", {"pan_deg": 12.0, "tilt_deg": 3.0})
    frame = np.zeros((H, W, 3), dtype=np.uint8)
    box = (100.0, 300.0, 260.0, 460.0)
    assert facewatch._head_now() == (0.0, 0.0)
    assert facewatch._in_head_frame(box, (0.0, 0.0), frame) == box


def test_the_greeting_boxes_remember_where_the_head_was():
    source = (ROOT / "app" / "facewatch.py").read_text(encoding="utf-8")
    assert source.count('"head": _head_now()') == 3          # _named + both stranger entries
    assert source.count("_in_head_frame(") == 3               # the def + both same-spot checks


# ---- session wiring --------------------------------------------------------

def test_the_engagement_watcher_runs_for_head_follow_and_turns_only_a_robot_face(on, monkeypatch):
    from app import session as session_mod

    monkeypatch.setattr(focus, "enabled", lambda: True)
    monkeypatch.setattr(settings, "focus_attention", False)
    monkeypatch.setattr(settings, "focus_goodbye", False)
    monkeypatch.setattr(settings, "focus_invite", False)
    s = session_mod.VoiceSession.__new__(session_mod.VoiceSession)
    s.summoned = False
    s._head_follow = None
    assert s._wants_engagement_watch() is True
    called = []

    async def fake_tick(self, now=None):
        called.append(now)

    monkeypatch.setattr(head_follow.HeadFollow, "tick", fake_tick)
    asyncio.run(s._turn_head(5.0))
    assert called == [] and s._head_follow is None, "a desk page has no head"
    link.claim(s)
    asyncio.run(s._turn_head(6.0))
    assert called == [6.0] and isinstance(s._head_follow, head_follow.HeadFollow)


# ---- 31 Oct, round 2: what the app answered, the 10 s centring, a loop going the wrong way ----

def _seq(face):
    return [f["seq"] for f in face.ws.sent if f["type"] == "head_look"][-1]


def test_a_head_held_on_a_still_guest_is_sent_again_before_the_apps_10_s_centring(on, monkeypatch):
    scene = Scene(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    scene.face(15.0, t=100.0)
    tick(follow, 100.0)
    # centred by that turn, the guest stays: no new step...
    for t in (101.5, 102.5, 103.5):
        scene.face(1.0, t=t)
        tick(follow, t)
    assert face.ws.looks() == [(45, 0)]
    # ...but before HeadLook.IDLE_CENTRE_MS (10 s) the same pose goes again
    scene.face(1.0, t=104.1)
    tick(follow, 104.1)
    assert face.ws.looks() == [(45, 0), (45, 0)]
    assert head_follow.HOLD_RESEND_S < 10.0
    assert [f for k, f in on if k == "head_look"][-1]["why"] == "face", "a re-send is not a row"


def test_a_refused_look_is_not_where_the_head_is(on, monkeypatch):
    scene = Scene(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    scene.face(15.0, t=100.0)
    tick(follow, 100.0)
    follow.ack({"type": "head_look_ack", "seq": _seq(face), "accepted": True, "reason": None, "ms": 800})
    assert follow.confirmed == (45, 0)
    scene.face(10.0, t=101.5)
    tick(follow, 101.5)
    assert face.ws.looks()[-1] == (60, 0)
    follow.ack({"type": "head_look_ack", "seq": _seq(face), "accepted": False, "reason": "busy", "ms": None})
    assert (follow.pan, follow.tilt) == (45, 0)
    assert head_follow.offset_deg() == (9.0, 0.0), "the dedupe's offset is the head's, not the refused one"
    assert [f for k, f in on if k == "head_look"][-1] == {"why": "refused", "reason": "busy",
                                                          "pan": 60, "tilt": 0}
    assert follow.off is None, "busy passes"
    # an answer to some other look (late, unknown seq, junk) changes nothing
    for junk in ({"seq": 999, "accepted": False, "reason": "busy"}, {"seq": "1", "accepted": False},
                 {"seq": True, "accepted": False}, "nope"):
        follow.ack(junk)
    assert (follow.pan, follow.tilt) == (45, 0)


def test_the_switch_off_on_the_robot_stops_asking_for_the_call(on, monkeypatch):
    scene = Scene(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    scene.face(15.0, t=100.0)
    tick(follow, 100.0)
    follow.ack({"seq": _seq(face), "accepted": False, "reason": "disabled", "ms": None})
    assert follow.off == "app_disabled"
    scene.face(15.0, t=102.0)
    tick(follow, 102.0)
    assert len(face.ws.looks()) == 1


def test_the_next_step_waits_for_the_move_the_app_said_it_takes(on, monkeypatch):
    scene = Scene(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    scene.face(15.0, t=100.0)
    tick(follow, 100.0)
    # from an unknown position the app moves slowly (T3000)
    follow.ack({"seq": _seq(face), "accepted": True, "reason": None, "ms": 3000})
    scene.face(12.0, t=102.0)            # mid-move: after SETTLE_S, before 3 s + lag
    tick(follow, 102.0)
    assert face.ws.looks() == [(45, 0)], "a picture of the head still turning is not the error"
    scene.face(6.0, t=103.5)
    tick(follow, 103.5)
    assert face.ws.looks() == [(45, 0), (60, 0)]


def test_a_loop_that_turns_the_wrong_way_stops_itself(on, monkeypatch):
    """The head turns RIGHT for "+" (a sign never checked): every step pushes
    the guest further out. Two such steps and the call stops following."""
    scene = Scene(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    guest = 12.0                                   # degrees to the robot's left
    t = 100.0
    for _ in range(12):
        actual = -(follow.pan / 100.0) * 20.0      # the real head: "+" turns it right
        scene.face(guest - actual, t=t)
        tick(follow, t)
        t += 1.5
        if follow.off:
            break
    assert follow.off == "diverging"
    sent = len(face.ws.looks())
    assert sent <= 3, "stopped within DIVERGE_STRIKES steps (+ the first)"
    assert [f for k, f in on if k == "head_look"][-1]["reason"] == "diverging"
    scene.face(30.0, t=t)
    tick(follow, t + 2)
    assert len(face.ws.looks()) == sent


@pytest.mark.parametrize("pace", [1.5, 6.0, 9.0])
def test_a_guest_who_walks_on_does_not_look_like_a_wrong_way_loop(on, monkeypatch, pace):
    scene = Scene(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    guest, t = 10.0, 100.0
    for _ in range(10):
        actual = follow.pan / 100.0 * 20.0         # the head turns the right way
        scene.face(guest - actual, t=t)
        tick(follow, t)
        guest += pace                              # degrees per step, to the robot's left
        t += 1.5
    assert follow.off is None


def test_a_direction_from_the_array_does_not_turn_the_head(on, monkeypatch):
    scene = Scene(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    scene.cue = {"bearing": 30.0, "source": "doa", "track": None}
    tick(follow, 100.0)
    assert face.ws.looks() == []
    # nor does it hold a turned head there: it is no target, so the idle centring runs
    scene.face(15.0, t=101.0)
    tick(follow, 101.0)
    assert face.ws.looks() == [(45, 0)]
    scene.cue = {"bearing": 30.0, "source": "doa", "track": None}
    scene.tracks = []
    tick(follow, 101.0 + head_follow.IDLE_S + 0.1)
    assert face.ws.looks() == [(45, 0), (0, 0)]


def test_session_hands_the_answer_to_its_own_controller_only():
    source = (ROOT / "app" / "session.py").read_text(encoding="utf-8")
    block = source[source.index('if event.get("type") == "head_look_ack":'):]
    block = block[:block.index("continue")]
    assert "self._head_follow is not None and robot_face_link.is_robot_face(self)" in block
    assert "self._head_follow.ack(event)" in block
