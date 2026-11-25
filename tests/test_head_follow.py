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
    # a frame after the move, the guest further left (12 deg): the next step, clamped
    # (6 deg left over would be inside the 10 deg deadband -- owner 10 Nov)
    scene.face(12.0, t=101.5)
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
    monkeypatch.setattr(event_walk, "moving", lambda: True)   # the wheels (event_walk.moving)
    scene.face(20.0, t=102.0)
    tick(follow, 102.0)
    tick(follow, 104.0)
    assert face.ws.looks() == [(45, 0), (0, 0)]


def _still(monkeypatch, value):
    from app import walk_frames

    box = {"s": value}
    monkeypatch.setattr(walk_frames, "still_s", lambda now=None: box["s"])
    return box


def test_a_walk_nods_only_while_the_base_stands_and_never_pans(on, monkeypatch):
    """Owner 4 Nov: "หันตอนเดินไปด้วยไม่ได้หรอ ให้มันสแกนไปด้วย", then "ตอนเดินไม่เห็นก้มมองดู
    วัดพื้น หรือโต๊ะเลย", then "อย่าหันหัวไวเกินและถี่เกิน เดี๋ยวมอเตอร์ที่หัวจะไหม้" (12 Sep:
    ch8 smoked). On a walk: ahead and still while the base moves; one nod per
    stop once it has stood WALK_SCAN_STILL_S, inside the app's walk box (no pan,
    0..36 us down); ahead again when it moves off and when the walk ends."""
    source = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    assert 'head_walk_scan: bool = _get_bool("HEAD_WALK_SCAN", False)' in source, "off unless robot.env says"
    monkeypatch.setattr(settings, "head_walk_scan", True)
    Scene(monkeypatch)
    from app import event_walk

    face = Face()
    follow = head_follow.HeadFollow(face)
    walking = {"on": True}
    monkeypatch.setattr(event_walk, "walking", lambda: walking["on"])
    monkeypatch.setattr(event_walk, "moving", lambda: walking["on"])   # the wheels (event_walk.moving)
    still = _still(monkeypatch, None)                    # driving: nobody says it stands
    for t in (100.0, 101.0, 103.0, 106.0):
        tick(follow, t)
    assert face.ws.looks() == [(0, 0)], "ahead once, then nothing while it drives"
    still["s"] = 0.5                                     # just stopped: a turn's wobble
    tick(follow, 107.0)
    assert face.ws.looks() == [(0, 0)]
    still["s"] = 1.2
    for t in (108.0, 109.0, 111.0, 114.0, 118.0):        # one stop, held long
        tick(follow, t)
    assert face.ws.looks() == [(0, 0)] + list(head_follow.WALK_SCAN), "one nod per stop, held"
    still["s"] = None                                    # off again
    tick(follow, 119.0)
    assert face.ws.looks()[-1] == (0, 0)
    n = len(face.ws.looks())
    tick(follow, 121.0)
    assert len(face.ws.looks()) == n, "ahead is sent once"
    still["s"] = 2.0                                     # the next stop nods afresh
    tick(follow, 125.0)
    assert face.ws.looks()[-1] == head_follow.WALK_SCAN[0]
    # HeadLook: 100 % = 200 us; the walk box is no pan and 0..36 us down
    for pan, tilt in head_follow.WALK_SCAN:
        assert pan == 0 and 0 < tilt * 2 <= 36, (pan, tilt)
    assert head_follow.WALK_SCAN_DWELL_S >= 2.0, "the app's own floor between walk looks"
    walking["on"] = False
    tick(follow, 125.6)
    assert face.ws.looks()[-1] == (0, 0), "the walk is over: ahead again"


def test_the_head_never_goes_past_where_it_stops(on, monkeypatch):
    """4 Nov evening, the dock tilt ladder (5 % steps, the head camera's picture
    shift): the head stops going down at ~22-23 % and up at ~16 % -- past that
    ch8 pushes on a head that no longer moves (12 Sep: smoked). The walk nod was
    40 % and an 80 % try was made that day. The defaults sit under the stops,
    and the walk nod never passes the call's own cap, whatever WALK_SCAN says."""
    source = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    assert 'os.getenv("HEAD_TILT_DOWN_MAX", "18") or 18' in source
    assert 'os.getenv("HEAD_TILT_UP_MAX", "12") or 12' in source
    assert 'os.getenv("HEAD_SEARCH_TILT", "18") or 0' in source
    assert all(tilt <= 18 for _pan, tilt in head_follow.WALK_SCAN)
    monkeypatch.setattr(settings, "head_walk_scan", True)
    monkeypatch.setattr(settings, "head_tilt_down_max", 10)
    monkeypatch.setattr(head_follow, "WALK_SCAN", ((0, 40),))   # an old table
    Scene(monkeypatch)
    from app import event_walk

    face = Face()
    follow = head_follow.HeadFollow(face)
    monkeypatch.setattr(event_walk, "walking", lambda: True)
    monkeypatch.setattr(event_walk, "moving", lambda: True)   # the wheels (event_walk.moving)
    _still(monkeypatch, 3.0)
    for t in (100.0, 101.0, 104.0):
        tick(follow, t)
    assert face.ws.looks()[-1] == (0, 10), "the cap wins over the table"
    assert all(tilt <= 10 for _pan, tilt in face.ws.looks())


def test_a_nod_gets_its_time_before_the_head_comes_back(on, monkeypatch):
    """The base moving off right after a nod: back ahead only after the dwell,
    not a second move on top of the first."""
    monkeypatch.setattr(settings, "head_walk_scan", True)
    Scene(monkeypatch)
    from app import event_walk

    face = Face()
    follow = head_follow.HeadFollow(face)
    monkeypatch.setattr(event_walk, "walking", lambda: True)
    monkeypatch.setattr(event_walk, "moving", lambda: True)   # the wheels (event_walk.moving)
    still = _still(monkeypatch, 3.0)
    tick(follow, 100.0)
    assert face.ws.looks() == [head_follow.WALK_SCAN[0]]
    still["s"] = None
    tick(follow, 101.0)
    assert face.ws.looks() == [head_follow.WALK_SCAN[0]]
    tick(follow, 100.0 + head_follow.WALK_SCAN_DWELL_S + 0.1)
    assert face.ws.looks() == [head_follow.WALK_SCAN[0], (0, 0)]


def test_the_base_stands_still_from_the_walk_pictures_poses(monkeypatch):
    from app import walk_frames

    walk_frames.forget_still()
    assert walk_frames.still_s(10.0) is None, "no pose: no claim"
    walk_frames._note_pose({"x": 1.0, "y": 2.0, "yaw": 0.0}, 10.0)
    walk_frames._note_pose({"x": 1.004, "y": 2.0, "yaw": 0.003}, 11.0)
    assert walk_frames.still_s(11.5) == pytest.approx(1.5)
    walk_frames._note_pose({"x": 1.008, "y": 2.0, "yaw": 0.0}, 12.0)    # creeping, under 1 cm
    walk_frames._note_pose({"x": 1.012, "y": 2.0, "yaw": 0.0}, 12.5)    # 1.2 cm from the stop
    assert walk_frames.still_s(12.5) == pytest.approx(0.0)
    walk_frames._note_pose({"x": 1.012, "y": 2.0, "yaw": 0.02}, 13.0)   # 1.1 deg: a turn
    assert walk_frames.still_s(13.0) == pytest.approx(0.0)
    assert walk_frames.still_s(13.0 + walk_frames.STILL_STALE_S + 0.1) is None, "a silent board is not still"
    walk_frames._note_pose(None, 14.0)
    assert walk_frames.still_s(14.0) is None


def test_a_walk_nobody_reports_still_scans_when_the_base_moves(on, monkeypatch):
    """Floor 1 walks run board_walk over adb: the app sees no walk and the event
    host runs none. The board's poses say the base moves (walk_frames.Motion) --
    that is a walk too, or the head would follow faces through it."""
    from app import event_walk, walk_frames

    monkeypatch.setattr(settings, "head_walk_scan", True)
    scene = Scene(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    monkeypatch.setattr(event_walk, "walking", lambda: False)
    monkeypatch.setattr(event_walk, "moving", lambda: False)   # the wheels (event_walk.moving)
    moving = {"on": True}
    monkeypatch.setattr(walk_frames, "base_moving", lambda: moving["on"])
    scene.face(20.0, t=100.0)
    tick(follow, 100.0)
    tick(follow, 102.0)
    assert face.ws.looks() == [(0, 0)], "ahead on the move, not the guest"
    moving["on"] = False
    assert head_follow._walking(face) is False


def test_base_motion_is_the_motion_checks_answer(monkeypatch):
    from app import walk_frames

    m = walk_frames.Motion()
    monkeypatch.setattr(walk_frames, "_motion", m)
    assert walk_frames.base_moving() is False
    m.until = __import__("time").monotonic() + 5.0
    assert walk_frames.base_moving() is True


def test_an_app_without_the_walk_box_keeps_the_walk_looking_ahead(on, monkeypatch):
    """An APK from before 4 Nov refuses a turn on a walk ("navigating"): asked
    once, then ahead for the rest of the walk -- not a refusal every 2 s."""
    monkeypatch.setattr(settings, "head_walk_scan", True)
    Scene(monkeypatch)
    from app import event_walk

    face = Face()
    follow = head_follow.HeadFollow(face)
    monkeypatch.setattr(event_walk, "walking", lambda: True)
    monkeypatch.setattr(event_walk, "moving", lambda: True)   # the wheels (event_walk.moving)
    _still(monkeypatch, 3.0)
    tick(follow, 100.0)
    follow.ack({"seq": _seq(face), "accepted": False, "reason": "navigating", "ms": None})
    for t in (100.6, 102.0, 104.0, 106.0):
        tick(follow, t)
    assert face.ws.looks() == [head_follow.WALK_SCAN[0], (0, 0)]


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
    # "first": the step of the head's lock on the first person (HEAD_FOLLOW_FIRST)
    assert rows == [{"why": "first", "pan": 45, "tilt": 0, "bearing": 15.0, "elev": 0.0}]


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
    assert [f for k, f in on if k == "head_look"][-1]["why"] == "first", "a re-send is not a row"


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
    scene.face(12.0, t=103.5)
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


# ---- a look asked from outside (face_approach): when it is over, and where (review 6 Nov) ---------

def test_an_outside_look_is_over_when_the_app_says_and_a_refusal_puts_the_pose_back(on):
    face = Face()
    hf = head_follow.HeadFollow(face)
    sent = 100.0
    assert asyncio.run(hf._send(20, 10, sent, "approach", force=True))
    # unanswered: the app's slowest move (from a head nobody knows), not the loop's 0.9 s
    assert head_follow.settled_at() == pytest.approx(sent + head_follow.UNACKED_MOVE_S + head_follow.CAMERA_LAG_S)
    hf.ack({"seq": hf.seq, "accepted": True, "ms": 1200})
    assert head_follow.settled_at() == pytest.approx(sent + 1.2 + head_follow.CAMERA_LAG_S)
    head_follow._LOOK["pose"] = (30, 10)
    assert asyncio.run(hf._send(30, 10, sent + 5.0, "approach", force=True))
    assert head_follow.at_look_pose()
    hf.ack({"seq": hf.seq, "accepted": False, "reason": "busy"})
    assert (head_follow._HEAD["pan_pct"], head_follow._HEAD["tilt_pct"]) == (20, 10), "where the last accepted look left it"
    assert not head_follow.at_look_pose(), "frames from here are not of the place it was sent to"


def test_the_loop_s_own_moves_keep_their_short_settle(on):
    face = Face()
    hf = head_follow.HeadFollow(face)
    assert asyncio.run(hf._send(20, 0, 50.0, "face", force=True))
    assert head_follow.settled_at() == pytest.approx(50.0 + head_follow.SETTLE_S)



def test_a_slow_outside_look_holds_the_head_for_the_whole_look(on, monkeypatch):
    """A 3 s move (an unanswered look, or a head nobody knew) settles at 3.4 s and is looked through
    for 3 s more: the idle branch's centring at SEARCH_HOLD_S (6 s) cut it (review 6 Nov)."""
    scene = Scene(monkeypatch)
    face = Face()
    hf = head_follow.HeadFollow(face)
    head_follow.look_hint(10, 20)
    asyncio.run(hf.tick(100.0))
    assert face.ws.looks(), "the look went out"
    assert hf.hold_until >= hf.settle_at + head_follow.LOOK_HOLD_S
    hf.ack({"seq": hf.seq, "accepted": True, "ms": 6000})     # slower than the unanswered guess
    assert hf.hold_until >= hf.settle_at + head_follow.LOOK_HOLD_S
    del scene


def test_a_refusal_keeps_the_settle_of_a_move_still_under_way(on):
    face = Face()
    hf = head_follow.HeadFollow(face)
    assert asyncio.run(hf._send(20, 10, 100.0, "approach", force=True))
    hf.ack({"seq": hf.seq, "accepted": True, "ms": 3000})
    on_its_way = head_follow.settled_at()
    assert asyncio.run(hf._send(40, 10, 101.0, "approach", force=True))
    hf.ack({"seq": hf.seq, "accepted": False, "reason": "busy"})
    assert head_follow.settled_at() == pytest.approx(on_its_way), "the first move is still going"


def test_when_nobody_will_send_the_head_anything_its_pose_is_not_known_until_the_app_centres_it(on):
    face = Face()
    hf = head_follow.HeadFollow(face)
    assert asyncio.run(hf._send(30, 10, head_follow._clock(), "face", force=True))
    head_follow.call_ended()
    assert (head_follow._HEAD["pan_pct"], head_follow._HEAD["tilt_pct"]) == (0, 0), "where the app will put it"
    assert head_follow.settled_at() >= head_follow._clock() + head_follow.APP_IDLE_CENTRE_S
    # and the call's follow turning off is the same
    head_follow.reset()
    hf2 = head_follow.HeadFollow(Face())
    assert asyncio.run(hf2._send(30, 10, head_follow._clock(), "face", force=True))
    hf2._stop("sweep_failed")
    assert head_follow.settled_at() >= head_follow._clock() + head_follow.APP_IDLE_CENTRE_S


def test_a_call_with_a_head_follow_tells_head_follow_when_it_ends():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "app" / "session.py").read_text(encoding="utf-8")
    end = text.split("robot_face_link.session_closed(session)", 1)[1].split("_reset_conversation_state()", 1)[0]
    # only for the call that owned the head (a superseded call closing late), never able to break the cleanup
    assert 'if getattr(session, "_head_follow", None) is not None and _active is session:' in end
    assert "call_ended()" in end and "except Exception:" in end



def test_after_a_call_the_head_is_not_known_until_the_next_call_centres_it(on, monkeypatch):
    """Review 6 Nov: the app centres the head itself only when nothing stops it (latched, the operator
    screen, the background, a handshake): after a call the head may stay turned. Nothing reads where it
    points until the next call's follow has sent one slow centre."""
    scene = Scene(monkeypatch)
    hf = head_follow.HeadFollow(Face())
    assert asyncio.run(hf._send(30, 10, head_follow._clock(), "face", force=True))
    head_follow.call_ended()
    assert head_follow.settled_at() == math.inf and head_follow._HEAD["known"] is False
    face = Face()
    nxt = head_follow.HeadFollow(face)
    asyncio.run(nxt.tick(head_follow._clock()))
    assert face.ws.looks()[-1][:2] == (0, 0), "one centre first"
    assert head_follow._HEAD["known"] is True and head_follow.settled_at() < math.inf
    del scene


def test_a_call_whose_follow_turned_off_counts_on_the_apps_own_centring(on):
    hf = head_follow.HeadFollow(Face())
    assert asyncio.run(hf._send(30, 10, head_follow._clock(), "face", force=True))
    hf._stop("sweep_failed")
    assert head_follow._HEAD["known"] is True, "this call ticks no more: nothing would centre it"
    assert head_follow._clock() + head_follow.APP_IDLE_CENTRE_S <= head_follow.settled_at() < math.inf


def test_her_own_walk_up_to_somebody_gets_no_nod_scan(on, monkeypatch):
    """The walk scan nods 18% whenever the base stands still on a walk; on the few metres of her own walk
    up to somebody the head has already looked (review 6 Nov: 6-7 moves in 20 s)."""
    import types as _types

    from app import event_walk

    monkeypatch.setattr(settings, "head_walk_scan", True)
    monkeypatch.setattr(event_walk, "walking", lambda: True)
    monkeypatch.setattr(event_walk, "moving", lambda: True)   # the wheels (event_walk.moving)
    spot = {"id": event_walk.NO_RESUME_SPOT}
    monkeypatch.setattr(event_walk, "current", lambda: _types.SimpleNamespace(spot=spot["id"]))
    scene = Scene(monkeypatch)
    _still(monkeypatch, 5.0)                             # the base stands: a scene walk would nod here
    face = Face()
    hf = head_follow.HeadFollow(face)
    for i in range(4):
        asyncio.run(hf.tick(100.0 + i * 3.0))
    assert all(tilt == 0 for _pan, tilt, *_ in face.ws.looks()), face.ws.looks()
    spot["id"] = "reception"                             # the control: a scene walk at the same stop nods
    other = Face()
    scene_walk = head_follow.HeadFollow(other)
    for i in range(4):
        asyncio.run(scene_walk.tick(200.0 + i * 3.0))
    assert any(tilt > 0 for _pan, tilt, *_ in other.ws.looks()), other.ws.looks()
    del scene


def test_the_pageant_owns_the_head_and_the_next_tick_recentres_it(on, monkeypatch):
    """ท่านางงาม (owner 9 Nov, app/event_pageant.py): the app turns the head itself -- no look while it
    runs (the app would refuse it busy), and its end is group 99, so the head is where nobody knows:
    one slow centre first."""
    from app import event_pageant

    scene = Scene(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    up = {"v": True}
    monkeypatch.setattr(event_pageant, "active", lambda seq=None: up["v"])
    scene.face(15.0, t=100.0)
    tick(follow, 100.0)
    tick(follow, 102.0)
    assert face.ws.looks() == []
    up["v"] = False
    scene.nobody()
    tick(follow, 104.0)
    assert face.ws.looks() == [(0, 0)]


# ---- the first person (HEAD_FOLLOW_FIRST, owner 10 Nov) ----------------------
#
# "เวลาหุ่นเห็นหน้าใครคนแรก อยากให้กล้องแทร็กตามคนนั้นเวลาเขายืนคุยหรืออยู่รอบๆ แต่พอ
# เขาไปหุ่นก็กลับมาตรงเหมือนเดิม ไม่ต้องหันไปมาตลอดเวลา". The robot's log that day: the
# cue jumped between the faces in view (+24.6 then -29.6) and the head with it.

def _box(bearing: float) -> tuple:
    """A face box whose centre facewatch reads at `bearing` (the units of the cue)."""
    focal = (W / 2.0) / math.tan(math.radians(facewatch.ROBOT_CAMERA_HFOV_DEG / 2.0))
    cx = W / 2.0 - focal * math.tan(math.radians(bearing))
    return (cx - 80.0, H / 2 - 80, cx + 80.0, H / 2 + 80)


class Crowd(Scene):
    """Several faces in one frame, their boxes where their bearings say."""

    def see(self, t, people, cue=None, source="face"):
        self.tracks = [(track, _box(bearing), None, t) for track, bearing in people]
        if cue is None:
            self.cue = {"bearing": None, "source": None, "track": None}
        else:
            self.cue = {"bearing": dict(people)[cue], "source": source, "track": cue}


def _locks(rows):
    return [f for kind, f in rows if kind == "head_lock"]


def test_the_head_stays_with_the_first_person_while_the_cue_jumps_to_another(on, monkeypatch):
    crowd = Crowd(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    crowd.see(100.0, [(1, 15.0), (2, -20.0)], cue=1)
    tick(follow, 100.0)
    assert face.ws.looks() == [(45, 0)]             # the first person: 9 of their 15 deg
    # attention turns to the other one (the visiting round, or their mouth moved): the head stays
    crowd.see(101.5, [(1, 6.0), (2, -29.0)], cue=2, source="visit")
    tick(follow, 101.5)
    crowd.see(102.5, [(1, 6.0), (2, -29.0)], cue=2, source="mouth")
    tick(follow, 102.5)
    assert face.ws.looks() == [(45, 0)]
    # the first person steps further left: the head follows THEM, not the cue
    crowd.see(103.0, [(1, 13.0), (2, -29.0)], cue=2, source="mouth")
    tick(follow, 103.0)
    assert face.ws.looks() == [(45, 0), (60, 0)]
    assert {r["why"] for kind, r in on if kind == "head_look"} == {"first"}
    assert follow.lock["track"] == 1 and follow.lock["elsewhere"] == 1
    assert _locks(on) == [{"state": "on", "source": "face", "bearing": 15.0, "pan": 0, "faces": 2}]


def test_with_the_switch_off_the_head_follows_the_cue_as_before(on, monkeypatch):
    monkeypatch.setattr(settings, "head_follow_first", False)
    crowd = Crowd(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    crowd.see(100.0, [(1, 15.0), (2, -20.0)], cue=1)
    tick(follow, 100.0)
    crowd.see(101.5, [(1, 6.0), (2, -29.0)], cue=2, source="visit")
    tick(follow, 101.5)
    assert face.ws.looks() == [(45, 0), (-42, 0)]
    assert _locks(on) == []


def test_a_new_track_at_the_first_persons_spot_is_still_them(on, monkeypatch):
    """A head move slides the box past the tracker's overlap: a new id, the same person."""
    crowd = Crowd(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    crowd.see(100.0, [(1, 15.0)], cue=1)
    tick(follow, 100.0)
    # after the 9 deg turn they are track 7 at +12 (the room's 21: 6 from where they stood);
    # the other face is the room's -16, 31 away
    crowd.see(101.5, [(7, 12.0), (2, -25.0)], cue=2)
    tick(follow, 101.5)
    assert face.ws.looks() == [(45, 0), (60, 0)]
    assert follow.lock["track"] == 7 and follow.lock["relocks"] == 1
    # a frame from before that move settled is nobody's spot: never taken again from it
    crowd.see(101.9, [(8, -1.0)], cue=8)
    tick(follow, 102.0)
    assert follow.lock["track"] == 7 and follow.lock["relocks"] == 1


def test_when_the_first_person_goes_the_head_holds_then_centres_then_takes_the_next(on, monkeypatch):
    crowd = Crowd(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    crowd.see(100.0, [(1, 15.0)], cue=1)
    tick(follow, 100.0)
    # they walk off; somebody else stays in view, far from where they stood
    for t in (101.5, 103.0, 104.5, 105.9):
        crowd.see(t, [(2, -25.0)], cue=2)
        tick(follow, t)
    assert all(pan >= 45 for pan, _tilt in face.ws.looks()), "nobody else is turned to meanwhile"
    assert follow.lock is not None
    # LOCK_LOST_S after they were last seen (100.0): back to centre in that tick
    crowd.see(106.1, [(2, -25.0)], cue=2)
    tick(follow, 106.1)
    assert face.ws.looks()[-1] == (0, 0)
    assert follow.lock is None
    off = _locks(on)[-1]
    assert off["state"] == "off" and off["why"] == "gone" and off["relocks"] == 0
    # a frame from before the head got back is not where anybody is
    crowd.see(106.6, [(2, -25.0)], cue=2)
    tick(follow, 106.7)
    assert face.ws.looks()[-1] == (0, 0)
    # from centre, the one in view is the next first person
    crowd.see(107.1, [(2, -25.0)], cue=2)
    tick(follow, 107.1)
    assert face.ws.looks()[-1] == (-60, 0)
    assert follow.lock["track"] == 2


def test_a_search_down_keeps_the_lock_and_finds_them_again(on, monkeypatch):
    """A voice at the robot with their face gone (they stepped up to the chest screen): the
    look down keeps the head's turn, so their spot still holds and they are the same person."""
    crowd = Crowd(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    crowd.see(100.0, [(1, 15.0)], cue=1)
    tick(follow, 100.0)
    crowd.see(102.5, [])
    crowd.voice = (102.8, 0.08)
    tick(follow, 103.0)
    assert face.ws.looks() == [(45, 0), (45, 30)]
    assert follow.lock is not None
    crowd.see(104.5, [(9, 3.0), (2, -25.0)], cue=2)
    tick(follow, 104.5)
    assert follow.lock["track"] == 9 and follow.lock["relocks"] == 1
    assert face.ws.looks() == [(45, 0), (45, 30)], "inside the deadband: no move"


def test_a_walk_ends_the_lock(on, monkeypatch):
    from app import event_walk

    crowd = Crowd(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    crowd.see(100.0, [(1, 15.0)], cue=1)
    tick(follow, 100.0)
    monkeypatch.setattr(event_walk, "moving", lambda: True)
    tick(follow, 102.0)
    assert follow.lock is None and _locks(on)[-1]["why"] == "walk"


def test_head_lock_rows_carry_numbers_only(on, monkeypatch):
    crowd = Crowd(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    crowd.see(100.0, [(1, 15.0)], cue=1)
    tick(follow, 100.0)
    for t in (102.0, 108.0):
        crowd.see(t, [])
        tick(follow, t)
    rows = _locks(on)
    assert [r["state"] for r in rows] == ["on", "off"]
    for row in rows:
        for key, value in row.items():
            assert isinstance(value, (int, float)) or key in ("state", "source", "why"), (key, value)


# ---- HEAD_LOCK_BY=speaker (owner 12 Nov: "อยากให้หันตามคนที่พูดคนแรกหรืออยู่ด้านหน้า") ---------

@pytest.fixture
def speaker(on, monkeypatch):
    monkeypatch.setattr(settings, "head_lock_by", "speaker")
    return on


def test_lock_by_is_first_in_code_and_speaker_on_the_robot():
    source = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    assert 'head_lock_by: str = os.getenv("HEAD_LOCK_BY", "first")' in source
    robot = (ROOT / "data" / "robot-server" / "robot.env").read_text(encoding="utf-8").splitlines()
    assert "HEAD_LOCK_BY=speaker" in robot


def test_a_face_crossing_the_lens_is_no_lock_and_no_turn_but_one_standing_there_is(speaker, monkeypatch):
    """12 Nov with "first": 37 locks, all on the biggest face in view, 35 gone within 10 s."""
    crowd = Crowd(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    for t in (100.0, 100.5, 101.0):                     # walking across: a second in the picture
        crowd.see(t, [(1, 15.0 - 10 * (t - 100.0))], cue=1)
        tick(follow, t)
    crowd.see(101.5, [])
    tick(follow, 101.5)
    assert face.ws.looks() == [] and follow.lock is None and _locks(speaker) == []
    # somebody stops in front of her: taken once they have stood HEAD_FRONT_STAND_S --
    # through one frame the detector missed (a blink is not a new person)
    for t, people in ((110.0, [(2, 15.0)]), (111.0, [(2, 15.0)]), (111.5, []),
                      (112.0, [(2, 15.0)])):
        crowd.see(t, people, cue=2 if people else None)
        tick(follow, t)
    assert follow.lock is None and face.ws.looks() == []
    crowd.see(112.6, [(2, 15.0)], cue=2)
    tick(follow, 112.6)
    assert follow.lock["track"] == 2 and _locks(speaker)[-1]["source"] == "front"
    assert face.ws.looks() == [(45, 0)]


@pytest.mark.parametrize("source", ["mouth", "main"])
def test_the_face_whose_voice_opened_her_ears_is_taken_at_once(speaker, monkeypatch, source):
    crowd = Crowd(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    # the one straight ahead would be "front" later; the one who spoke wins now
    crowd.see(100.0, [(1, 15.0), (2, -2.0)], cue=1, source=source)
    tick(follow, 100.0)
    assert follow.lock["track"] == 1 and _locks(speaker)[-1]["source"] == "speaker"
    assert face.ws.looks() == [(45, 0)]


def test_a_lone_faces_label_counts_only_after_a_moment_in_the_picture(speaker, monkeypatch):
    """Review 12 Nov: "only_face" labels a face crossing the lens while somebody off camera
    talks. Two of the robot's frames (SPEAKER_MIN_STAND_S) first."""
    crowd = Crowd(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    crowd.see(100.0, [(1, 15.0)], cue=1, source="label")
    tick(follow, 100.0)
    assert follow.lock is None and face.ws.looks() == []
    crowd.see(101.0, [(1, 15.0)], cue=1, source="label")
    tick(follow, 101.0)
    assert follow.lock["track"] == 1 and _locks(speaker)[-1]["source"] == "speaker"
    assert face.ws.looks() == [(45, 0)]


def test_on_the_robots_frame_timing_a_face_in_view_still_holds_off_the_search(speaker, monkeypatch):
    """Review 12 Nov: a robot frame with a face arrives ~1 s after it was taken, about once a
    second. The guard read frame time + 1 s and never held: a loud voice looked down and the
    silent face in the middle was taken as the voice. Frames 1 s apart, published 0.97 s late,
    the loop ticking every 0.5 s, a loud voice throughout: no look away, and they are taken
    as "front" once they have stood there."""
    crowd = Crowd(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    t = 100.97
    while t < 104.6:
        frame = float(int(t - 0.97))                  # the newest frame published by now
        crowd.see(frame, [(1, 15.0)], cue=1)
        crowd.voice = (t - 0.3, 0.08)
        tick(follow, t)
        t = round(t + 0.5, 2)
    assert (0, 30) not in face.ws.looks() and not [r for k, r in speaker if k == "head_look"
                                                      and r.get("why") in ("search", "lidar")]
    assert follow.lock["track"] == 1 and _locks(speaker)[-1]["source"] == "front"
    assert face.ws.looks() == [(45, 0)]


def test_a_slow_minute_of_the_camera_does_not_restart_somebody_standing(speaker, monkeypatch):
    """Review 12 Nov: ~2.5 s between frames with a face in the robot's worst minute; a stand
    restarted after FOCUS_HOLD_S (2 s) never reached HEAD_FRONT_STAND_S."""
    crowd = Crowd(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    for frame in (100.0, 102.4):
        crowd.see(frame, [(1, -12.0)], cue=1)
        tick(follow, frame + 0.5)
    assert follow.lock is None
    crowd.see(104.8, [(1, -12.0)], cue=1)
    tick(follow, 105.3)
    assert follow.lock["track"] == 1 and _locks(speaker)[-1]["source"] == "front"


def test_with_a_lock_the_search_still_finds_them_beside_a_bystander(speaker, monkeypatch):
    """Review 12 Nov: the guard blocked the look down while a bystander stayed in view -- the
    one who spoke stepped under the camera, the lock ran out and the head turned to the silent
    bystander. With a lock it is the 10 Nov search: it finds them again."""
    crowd = Crowd(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    crowd.see(100.0, [(1, 15.0), (2, -25.0)], cue=1, source="mouth")
    tick(follow, 100.0)
    assert follow.lock["track"] == 1 and face.ws.looks() == [(45, 0)]
    crowd.see(102.5, [(2, -34.0)], cue=2)               # they stepped up to the chest screen
    crowd.voice = (102.8, 0.08)
    tick(follow, 103.0)
    assert face.ws.looks() == [(45, 0), (45, 30)] and follow.lock["track"] == 1
    crowd.see(104.5, [(9, 3.0), (2, -34.0)], cue=2)
    tick(follow, 104.5)
    assert follow.lock["track"] == 9 and follow.lock["relocks"] == 1


def test_speaker_mode_without_the_first_person_lock_is_the_cue_as_before(speaker, monkeypatch):
    monkeypatch.setattr(settings, "head_follow_first", False)
    crowd = Crowd(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    crowd.see(100.0, [(1, 15.0), (2, -20.0)], cue=1)
    tick(follow, 100.0)
    assert face.ws.looks() == [(45, 0)] and _locks(speaker) == []


def test_of_several_standing_the_one_straight_ahead_not_the_biggest(speaker, monkeypatch):
    crowd = Crowd(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    for t in (100.0, 101.0, 102.0, 102.6):          # attention's cue: the bigger face, to the side
        crowd.see(t, [(1, 20.0), (2, -12.0)], cue=1)
        tick(follow, t)
    assert follow.lock["track"] == 2 and _locks(speaker)[-1]["source"] == "front"
    assert face.ws.looks() == [(-36, 0)]


def test_a_face_found_where_a_loud_voice_aimed_the_head_is_the_voice(speaker, monkeypatch):
    crowd = Crowd(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    crowd.see(100.0, [])
    crowd.voice = (100.8, 0.08)
    tick(follow, 101.5)
    assert face.ws.looks() == [(0, 30)]                 # the look down for the voice
    crowd.see(103.0, [(5, 4.0)], cue=5)                 # a frame after the move: somebody there
    tick(follow, 103.0)
    assert follow.lock["track"] == 5 and _locks(speaker)[-1]["source"] == "voice_look"
    # a frame from BEFORE that look settled would not have counted
    follow2 = head_follow.HeadFollow(Face())
    crowd.see(200.0, [])
    crowd.voice = (200.8, 0.08)
    tick(follow2, 201.5)
    crowd.see(201.9, [(6, 4.0)], cue=6)
    tick(follow2, 202.0)
    assert follow2.lock is None


def test_a_face_not_yet_taken_holds_off_the_look_away_for_a_voice(speaker, monkeypatch):
    """The voice may be theirs: the segment's close says so ("only_face" -> attention's
    label). Looking down or turning to the lidar's person now would leave them."""
    crowd = Crowd(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    crowd.see(100.0, [(1, 15.0)], cue=1)
    crowd.voice = (100.2, 0.08)
    tick(follow, 100.5)
    assert face.ws.looks() == [] and follow.lock is None
    crowd.see(101.0, [(1, 15.0)], cue=1, source="label")    # the segment closed: it was theirs
    tick(follow, 101.0)
    assert follow.lock["track"] == 1 and face.ws.looks() == [(45, 0)]


# ---- review 10 Nov (each reproduced red before the fix) ----------------------

def test_a_missed_frame_of_the_first_person_never_hands_the_head_to_the_one_beside_them(on, monkeypatch):
    crowd = Crowd(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    crowd.see(100.0, [(1, 5.0), (2, -15.0)], cue=1)     # A inside the deadband, B beside them
    tick(follow, 100.0)
    assert follow.lock["track"] == 1 and face.ws.looks() == []
    crowd.see(100.5, [(2, -15.0)], cue=2)                # the detector misses A (turned to B)
    tick(follow, 100.5)
    crowd.see(101.0, [(1, 5.0), (2, -15.0)], cue=1, source="mouth")
    tick(follow, 101.0)
    crowd.see(102.6, [(1, 5.0), (2, -15.0)], cue=2, source="mouth")
    tick(follow, 102.6)
    assert follow.lock["track"] == 1 and follow.lock["relocks"] == 0
    assert face.ws.looks() == [], "B is 20 from A's spot, inside LOCK_NEAR_DEG, and was seen with A"


def test_a_frozen_camera_frame_is_nobody(on, monkeypatch):
    """facewatch keeps its last frame when the camera stalls or a face scan pauses the greeter."""
    crowd = Crowd(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    crowd.see(100.0, [(1, 15.0)], cue=1)
    tick(follow, 100.0)
    crowd.see(101.5, [(1, 6.0)], cue=None)               # the last frame, never replaced
    for i in range(1, 40):
        tick(follow, 101.5 + 0.5 * i)
    assert follow.lock is None and _locks(on)[-1]["why"] == "gone"
    assert face.ws.looks()[-1] == (0, 0)


def test_a_refused_centring_still_comes_back_to_centre_before_the_next_person(on, monkeypatch):
    crowd = Crowd(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    crowd.see(100.0, [(1, 15.0)], cue=1)
    tick(follow, 100.0)
    follow.ack({"seq": follow.seq, "accepted": True, "ms": 300})
    for t in (101.5, 103.0, 104.5, 105.9):
        crowd.see(t, [(2, -25.0)], cue=2)
        tick(follow, t)
    crowd.see(106.1, [(2, -25.0)], cue=2)
    tick(follow, 106.1)
    assert face.ws.looks()[-1] == (0, 0)
    follow.ack({"seq": follow.seq, "accepted": False, "reason": "busy"})   # a speech gesture held it
    assert (follow.pan, follow.relock_blocked) == (45, True)
    crowd.see(106.9, [(2, -25.0)], cue=2)
    tick(follow, 106.9)
    assert follow.lock is None, "the bystander is not locked from the head still turned"
    assert face.ws.looks()[-1] == (0, 0), "the centring is asked again"


def test_a_pan_trim_is_where_every_look_starts_from(on, monkeypatch):
    """Owner 25 Nov: at the app's own centre the head looks left of the body. HEAD_PAN_TRIM_PCT moves every look the server
    asks by that much (never past the app's span); 0 = the frames as they were."""
    from app.config import settings

    face = Face()
    monkeypatch.setattr(settings, "head_pan_trim_pct", -15, raising=False)
    assert asyncio.run(link.look(face, 0, 0)) is True and face.ws.sent[-1] == {"type": "head_look", "pan": -15, "tilt": 0}
    assert asyncio.run(link.look(face, -95, 5)) is True and face.ws.sent[-1]["pan"] == -100
    assert asyncio.run(link.look(face, 101, 0)) is False                 # what is asked is still checked as asked
    monkeypatch.setattr(settings, "head_pan_trim_pct", 0, raising=False)
    assert asyncio.run(link.look(face, 10, 0)) is True and face.ws.sent[-1]["pan"] == 10
