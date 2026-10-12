"""
"Probably somebody we know" (owner, 9 Oct 2026): the hold, the closer look,
the turn to check, and the handshake that goes with a name.

The owner's words: "บางทีหุ่นอาจจะจดจำใบหน้าแต่เพราะอาจจะอยู่ไกลทำให้หุ่นไม่
ชัวว่าเป็นคนรู้จัก พยายามให้หุ่นขยับเพื่อเช็ค ถ้าเป็นคนรู้จักก็ทักทายด้วยชื่อ
พร้อมจับมือ". Every switch here is off by default; most tests below are about
what each one must still refuse when it is on — a hold never names anybody,
a crop never moves the distance bar, a turn never happens on the dock, and
a hand is never offered to a stranger or to two people.
"""
from __future__ import annotations

import asyncio
import json
import math
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from app import faces, facewatch, greeter, robot_camera
from app.config import settings

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "client" / "voice-preview.html"


def _unit(*values: float) -> np.ndarray:
    v = np.zeros(512, np.float32)
    for i, x in enumerate(values):
        v[i] = x
    return v / np.linalg.norm(v)


ALICE = _unit(1)
BOB = _unit(0, 1)
LOOKALIKE = _unit(0.40, 0, 0.917)       # 0.40 against ต้า: in the band, under the 0.45 bar
UNKNOWN = _unit(0.2, 0.2, 1)            # ~0.19: a real stranger, under the band
CLOSE_SECOND = _unit(1, 0.9)            # 0.74 ต้า / 0.67 mix: inside the 0.10 margin
NAMES = ("ต้า", "mix")

SHARP = np.random.default_rng(7).integers(0, 255, (480, 640, 3), dtype=np.uint8)


@pytest.fixture(autouse=True)
def _consent_and_clean_state(monkeypatch):
    """Consent decided here, not by whatever people.json this machine holds;
    the greeter's turn memory and the camera feed are module state."""
    from app import consent

    monkeypatch.setattr(consent, "may_name", lambda name, **kw: (True, "ok"))
    monkeypatch.setattr(greeter, "_last_turn_at", 0.0)
    monkeypatch.setattr(greeter, "_turned_for", None)
    monkeypatch.setattr(greeter, "_turn_home", None)
    monkeypatch.setattr(robot_camera, "feed", robot_camera.RobotCameraFeed())


def _gallery() -> faces.Gallery:
    return faces.Gallery(list(NAMES), np.stack([ALICE, BOB]),
                         [{"group": "staff"}, {"group": "staff"}])


def _watcher(**kw) -> facewatch.Watcher:
    kw.setdefault("gallery", _gallery())
    kw.setdefault("threshold", 0.45)
    kw.setdefault("confirm_frames", 1)
    kw.setdefault("cooldown_s", 600)
    kw.setdefault("min_face_px", 110)
    kw.setdefault("track_wait_s", 0)
    kw.setdefault("probable_wait_s", 8.0)
    kw.setdefault("probable_min_px", 36)
    return facewatch.Watcher(**kw)


def _sees(monkeypatch, vec, width=200, extra=()):
    face = faces.Face((0, 0, width, width), 0.9, vec)
    found = [face, *extra]
    monkeypatch.setattr(faces, "locate", lambda frame, det_size=640: list(found))


def _sees_nothing(monkeypatch):
    monkeypatch.setattr(faces, "locate", lambda frame, det_size=640: [])


def _no_names(rows) -> None:
    text = json.dumps(rows, ensure_ascii=False)
    for name in NAMES:
        assert name not in text, rows


# -- 1. the hold ----------------------------------------------------------------

def test_an_ambiguous_score_holds_then_names_once_they_come_closer(monkeypatch):
    """The anonymous greeting used to win the race: approach frames score
    under the bar, a stranger needs only 2 of them. In the band, consented:
    held — and the first clear frames of the same person name them."""
    w = _watcher()
    _sees(monkeypatch, LOOKALIKE)
    for i in range(10):                             # 5x what a stranger needs
        assert w.see(SHARP, now=i * 0.2) is None
    assert w.status["held"] is True and w.status.get("probable") is True
    assert "ต้า" not in w.status["verdict"]         # the chest screen faces the room
    _sees(monkeypatch, ALICE)
    seen = w.see(SHARP, now=2.2)
    assert seen is not None and seen.kind == "known" and seen.name == "ต้า"
    assert seen.probable_s == pytest.approx(2.2)
    rows = w.drain_events()
    assert [r["phase"] for r in rows] == ["start", "named"]
    assert rows[1]["held"] == 10 and rows[1]["held_by"] == {"band": 10}
    _no_names(rows)


def test_a_far_known_face_is_held_and_never_named_until_it_is_close(monkeypatch):
    """Far is still a threshold: the hold keeps the arrival alive, it does
    not move FACE_MIN_PX. A perfect match at 60 px names nobody."""
    w = _watcher(confirm_frames=1)
    _sees(monkeypatch, ALICE, width=60)
    for i in range(10):
        assert w.see(SHARP, now=i * 0.2) is None
    assert "ไกล" in w.status["verdict"] and "ต้า" not in w.status["verdict"]
    assert w.look_closer["far"] is True and w.look_closer["probable"] is True
    _sees(monkeypatch, ALICE, width=200)
    seen = w.see(SHARP, now=2.2)
    assert seen is not None and seen.name == "ต้า"
    assert [r["phase"] for r in w.drain_events()] == ["start", "named"]


def test_far_faces_are_not_described_without_the_hold(monkeypatch):
    """Off, a far face costs what it always did: no embedding at all."""
    described = []
    monkeypatch.setattr(faces, "describe", lambda img, face, det_size=640: described.append(1))
    w = _watcher(probable_wait_s=0)
    _sees(monkeypatch, None, width=60)
    for i in range(3):
        assert w.see(SHARP, now=i * 0.2) is None
    assert described == [] and w.drain_events() == []


def test_a_far_arrival_whose_hold_ran_out_is_not_described_again(monkeypatch):
    """A colleague sitting far all day: one hold, it runs out, and no new one
    can start until the frame has been empty -- so the embedding (the
    expensive half, on the robot's CPU) stops too. Back after an empty
    frame: described again."""
    described = []

    def describe(img, face, det_size=640):
        described.append(1)
        face.vec = ALICE
        return face

    present = {"on": True}
    monkeypatch.setattr(faces, "describe", describe)
    monkeypatch.setattr(faces, "locate", lambda frame, det_size=640:
                        [faces.Face((0, 0, 60, 60), 0.9)] if present["on"] else [])
    w = _watcher(probable_wait_s=1.0)
    for i in range(8):
        assert w.see(SHARP, now=i * 0.2) is None
    assert [r["phase"] for r in w.drain_events()] == ["start", "expired"]
    before = len(described)
    for i in range(10):
        assert w.see(SHARP, now=2.0 + i * 0.2) is None
    assert len(described) == before, "spent: nothing an embedding could decide"
    present["on"] = False
    for t in (4.5, 7.0):
        w.see(SHARP, now=t)
    present["on"] = True
    w.see(SHARP, now=7.2)
    assert len(described) == before + 1
    assert [r["phase"] for r in w.drain_events()] == ["start"]


def test_the_hold_runs_out_and_the_greeting_has_no_name(monkeypatch):
    """Somebody in the band who never gets clearer: after the wait they are
    greeted — as a stranger, without a name. The wait is a bound."""
    w = _watcher(probable_wait_s=3.0)
    _sees(monkeypatch, LOOKALIKE)
    seen, t = None, 0.0
    while seen is None and t < 10:
        seen = w.see(SHARP, now=t)
        t = round(t + 0.2, 1)
    assert seen is not None and seen.kind == "stranger" and seen.name is None
    assert 3.0 <= t <= 3.8                          # the wait, then 2 frames
    rows = w.drain_events()
    assert [r["phase"] for r in rows] == ["start", "expired"]
    _no_names(rows)


def test_walking_away_ends_the_hold_and_greets_nobody(monkeypatch):
    w = _watcher()
    _sees(monkeypatch, LOOKALIKE)
    for i in range(4):
        assert w.see(SHARP, now=i * 0.2) is None
    _sees_nothing(monkeypatch)
    for t in (1.0, 2.0, 3.5):
        assert w.see(SHARP, now=t) is None
    assert [r["phase"] for r in w.drain_events()] == ["start", "lost"]


def test_a_clear_stranger_is_greeted_as_fast_as_ever_during_a_hold(monkeypatch):
    """A sharp, close, frontal face scoring under the band is a stranger
    after all — the hold must not slow the doorway down for them."""
    w = _watcher()
    _sees(monkeypatch, LOOKALIKE)
    assert w.see(SHARP, now=0.0) is None
    _sees(monkeypatch, UNKNOWN)
    assert w.see(SHARP, now=0.2) is None
    seen = w.see(SHARP, now=0.4)
    assert seen is not None and seen.kind == "stranger"


def test_off_the_band_is_a_stranger_at_the_old_speed(monkeypatch):
    w = _watcher(probable_wait_s=0)
    _sees(monkeypatch, LOOKALIKE)
    assert w.see(SHARP, now=0.0) is None
    seen = w.see(SHARP, now=0.2)
    assert seen is not None and seen.kind == "stranger" and w.drain_events() == []


# -- never a name on the paid-for refusals -----------------------------------------

def test_a_close_second_is_held_but_never_named(monkeypatch):
    w = _watcher(probable_wait_s=2.0)
    _sees(monkeypatch, CLOSE_SECOND)
    seen, t = None, 0.0
    while seen is None and t < 10:
        seen = w.see(SHARP, now=t)
        t = round(t + 0.2, 1)
    assert seen.kind == "stranger" and seen.name is None
    assert seen.meta.get("unnamed", "").startswith("margin")
    assert t >= 2.0                                 # held first: the names may part


def test_two_faces_are_never_named_and_not_held(monkeypatch):
    """"Company" does not get better with distance: no hold, no name."""
    other = faces.Face((300, 0, 480, 180), 0.9, BOB)
    w = _watcher()
    _sees(monkeypatch, ALICE, extra=(other,))
    assert w.see(SHARP, now=0.0) is None
    seen = w.see(SHARP, now=0.2)
    assert seen.kind == "stranger" and seen.name is None
    assert seen.meta["unnamed"].startswith("company")
    assert w.drain_events() == []


def test_no_consent_is_never_named_and_not_held(monkeypatch):
    from app import consent

    monkeypatch.setattr(consent, "may_name", lambda name, **kw: (False, "revoked"))
    w = _watcher()
    _sees(monkeypatch, LOOKALIKE)
    assert w.see(SHARP, now=0.0) is None
    seen = w.see(SHARP, now=0.2)
    assert seen.kind == "stranger" and seen.name is None
    _sees(monkeypatch, ALICE, width=60)              # far + perfect: still nothing to hold
    assert w.see(SHARP, now=0.4) is None
    assert w.drain_events() == []


# -- 2. the closer look ------------------------------------------------------------

CROP_IMG = np.full((300, 300, 3), 90, np.uint8)


def _sees_with_crop(monkeypatch, frame_vec, crop_vec, crop_box=(75, 75, 225, 225), width=200):
    frame_face = faces.Face((0, 0, width, width), 0.9, None)
    vecs = {"frame": frame_vec, "crop": crop_vec}

    def locate(img, det_size=640):
        if img is CROP_IMG:
            return [faces.Face(crop_box, 0.9, None)]
        return [faces.Face(frame_face.bbox, 0.9, None)]

    def describe(img, face, det_size=640):
        face.vec = vecs["crop" if img is CROP_IMG else "frame"]
        return face

    monkeypatch.setattr(faces, "locate", locate)
    monkeypatch.setattr(faces, "describe", describe)


def _crop(region=(-50, -50, 250, 250), scale=1.0):
    return {"frame": CROP_IMG, "region": list(region), "scale": scale}


def test_a_crop_of_the_same_face_decides_that_frame(monkeypatch):
    """The page's q95 crop replaces the q70 frame's vector — once."""
    w = _watcher(confirm_frames=2)
    _sees_with_crop(monkeypatch, LOOKALIKE, ALICE)
    assert w.see(SHARP, now=0.0, crop=_crop()) is None       # named frame 1 of 2
    assert w.status.get("name") == "ต้า"
    seen = w.see(SHARP, now=0.2, crop=_crop())
    assert seen is not None and seen.name == "ต้า"


def test_a_crop_of_another_place_is_ignored(monkeypatch):
    w = _watcher(confirm_frames=1)
    _sees_with_crop(monkeypatch, UNKNOWN, ALICE)
    far_away = _crop(region=(400, 300, 700, 600))           # maps nowhere near (0,0,200,200)
    assert w.see(SHARP, now=0.0, crop=far_away) is None
    assert w.status.get("name") is None


def test_a_crop_never_moves_the_distance_bar(monkeypatch):
    """A crop sharpens the vector, not the distance: a 60 px face whose crop
    matches perfectly is still somebody across the room."""
    w = _watcher(confirm_frames=1)
    _sees_with_crop(monkeypatch, LOOKALIKE, ALICE, crop_box=(75, 75, 135, 135), width=60)
    for i in range(6):
        assert w.see(SHARP, now=i * 0.2, crop=_crop(region=(-75, -75, 225, 225))) is None


def test_a_closer_look_is_wanted_without_the_hold_only_with_zoom_on(monkeypatch):
    """Zoom alone (no hold) still asks about a face in the band; with both
    off the watcher wants nothing and decides exactly as before."""
    _sees(monkeypatch, LOOKALIKE)
    w = _watcher(probable_wait_s=0)
    assert w.see(SHARP, now=0.0) is None and w.look_closer is None
    monkeypatch.setattr(settings, "face_zoom_enabled", True)
    w = _watcher(probable_wait_s=0)
    assert w.see(SHARP, now=0.0) is None
    assert w.look_closer["bbox"] == (0, 0, 200, 200) and w.look_closer["probable"] is False
    seen = w.see(SHARP, now=0.2)                    # no hold: the old speed
    assert seen is not None and seen.kind == "stranger" and w.drain_events() == []


def test_the_feed_keeps_crops_apart_from_frames():
    feed = robot_camera.RobotCameraFeed()
    feed.push(b"\xff\xd8frame-1")
    rid = feed.request_crop((10, 20, 60, 70), 1024, 768)
    seq, ctl = feed.control()
    assert ctl["crop"] == {"id": rid, "box": [10, 20, 60, 70], "w": 1024, "h": 768,
                           "pad": 1.5, "photo": False}
    import cv2

    ok, jpeg = cv2.imencode(".jpg", np.full((40, 40, 3), 128, np.uint8))
    head = json.dumps({"id": rid, "region": [0, 0, 100, 100], "scale": 1.0, "src": "video",
                       "video": [1024, 768], "video_max": [1024, 768], "photo_max": None})
    feed.push(robot_camera.CROP_TAG + head.encode() + b"\n" + jpeg.tobytes())
    assert feed.latest()[1] == b"\xff\xd8frame-1"           # the frame stream untouched
    crop = feed.take_crop()
    assert crop is not None and crop["frame"].shape == (40, 40, 3) and crop["region"] == [0, 0, 100, 100]
    assert feed.take_crop() is None                          # one look, once


def test_the_feed_drops_old_answers_and_garbage():
    feed = robot_camera.RobotCameraFeed()
    first = feed.request_crop((0, 0, 50, 50), 1024, 768)
    feed.request_crop((0, 0, 50, 50), 1024, 768)
    import cv2

    jpeg = cv2.imencode(".jpg", np.zeros((8, 8, 3), np.uint8))[1].tobytes()
    stale = json.dumps({"id": first, "region": [0, 0, 8, 8], "scale": 1.0})
    feed.push(robot_camera.CROP_TAG + stale.encode() + b"\n" + jpeg)
    assert feed.take_crop() is None                          # answers a superseded request
    feed.push(robot_camera.CROP_TAG + b"not json\n" + jpeg)
    feed.push(robot_camera.CROP_TAG + b'{"id": 2, "region": [0, 0, 8, 8], "scale": 1e9}\n' + jpeg)
    assert feed.take_crop() is None


def test_an_old_crop_request_is_not_repeated_with_a_later_control(monkeypatch):
    feed = robot_camera.RobotCameraFeed()
    clock = [100.0]
    monkeypatch.setattr(robot_camera.time, "monotonic", lambda: clock[0])
    feed.request_crop((0, 0, 50, 50), 1024, 768)
    assert "crop" in feed.control()[1]
    clock[0] += robot_camera.CROP_REQUEST_TTL_S + 0.1
    feed.set_control(zoom=2.0)
    assert "crop" not in feed.control()[1] and feed.control()[1]["zoom"] == 2.0


def test_the_page_and_the_server_agree_on_the_tag():
    html = PAGE.read_text(encoding="utf-8")
    assert "var CROP_TAG = '\\u0000EMMACROP\\n';" in html
    assert robot_camera.CROP_TAG == "\u0000EMMACROP\n".encode()


# -- the greeter asks only with the switch on ------------------------------------

class _Cap:
    def __init__(self, frames):
        self.frames = list(frames)

    def read(self):
        if not self.frames:
            return False, None
        return True, self.frames.pop(0)

    def release(self):
        pass


def _loop(monkeypatch, look, frames=3, crops=(), take=None, **conf):
    """Run the real greeter loop over `frames` frames with a watcher that
    always wants a closer look at `look`. Returns (requests, see-crops)."""
    from app import camera, events

    asked, given = [], []
    queue = list(crops)

    class _Watcher:
        threshold, cooldown_s = 0.45, 600.0
        someone_at = None

        def __init__(self):
            self.look_closer = None

        def see(self, frame, now=None, crop=None):
            given.append(crop)
            self.look_closer = dict(look) if look else None
            return None

    monkeypatch.setattr(facewatch.Watcher, "from_settings", classmethod(lambda cls: _Watcher()))
    monkeypatch.setattr(camera, "open_camera", lambda *a, **k: _Cap([np.zeros((8, 8, 3), np.uint8)] * frames))
    monkeypatch.setattr(events, "announce", lambda *a, **k: None)
    monkeypatch.setattr(robot_camera.feed, "request_crop",
                        lambda bbox, w, h, photo=False: asked.append((tuple(bbox), w, h, photo)))
    monkeypatch.setattr(robot_camera.feed, "take_crop",
                        take or (lambda *a, **k: queue.pop(0) if queue else None))
    monkeypatch.setattr(settings, "face_fps", 100.0)
    monkeypatch.setattr(greeter, "ZOOM_INTERVAL_S", 0.0)
    for key, value in conf.items():
        monkeypatch.setattr(settings, key, value)
    asyncio.run(greeter.run())
    return asked, given


LOOK = {"bbox": (10, 10, 60, 60), "w": 1024, "h": 768, "px": 50, "bearing": 20.0,
        "far": True, "probable": True, "hold": 1.0}


def test_no_closer_look_is_asked_for_with_the_switch_off(monkeypatch):
    asked, given = _loop(monkeypatch, LOOK, crops=[{"frame": CROP_IMG}],
                         face_zoom_enabled=False, face_camera_source="robot")
    assert asked == [] and given == [None, None, None]


def test_no_closer_look_from_a_lens_with_no_page(monkeypatch):
    asked, _ = _loop(monkeypatch, LOOK, face_zoom_enabled=True, face_camera_source="local")
    assert asked == []


def test_the_switch_on_asks_and_hands_each_crop_over_once(monkeypatch):
    crop = {"frame": CROP_IMG, "region": [0, 0, 300, 300], "scale": 1.0}
    asked, given = _loop(monkeypatch, LOOK, crops=[crop],
                         face_zoom_enabled=True, face_camera_source="robot")
    assert asked and asked[0] == ((10, 10, 60, 60), 1024, 768, False)
    assert given == [crop, None, None]


def test_the_crop_is_decoded_off_the_event_loop(monkeypatch):
    """The event loop also carries the call's audio; a q95 crop's JPEG
    decode belongs on the recognition thread with `see()`."""
    import threading

    loop_threads, crop_threads = [], []
    crop = {"frame": CROP_IMG, "region": [0, 0, 300, 300], "scale": 1.0}

    def take_crop(*a, **k):
        crop_threads.append(threading.get_ident())
        return dict(crop)

    real = greeter._ask_closer

    def ask(want, last_ask, now):
        loop_threads.append(threading.get_ident())
        return real(want, last_ask, now)

    monkeypatch.setattr(greeter, "_ask_closer", ask)
    _asked, given = _loop(monkeypatch, LOOK, take=take_crop, face_zoom_enabled=True,
                          face_camera_source="robot")
    assert given and all(g is not None for g in given)
    assert crop_threads and loop_threads
    assert not set(crop_threads) & set(loop_threads)


# -- 3. the turn to check ----------------------------------------------------------

def _ring(radius: float, n: int = 400) -> list[dict]:
    return [{"angle": -math.pi + 2 * math.pi * i / n, "distance": radius, "valid": True}
            for i in range(n)]


class _Still:
    def __init__(self):
        self.until = None

    def hold_still(self, until):
        self.until = until


def _robot(monkeypatch, *, docking="not_on_dock", nav="idle", ring=2.0, moving=None,
           action=None, motion=True, where=None):
    from app import robot_chassis, robot_face_link
    from app import session as session_module

    sent = []

    async def power():
        return {"dockingStatus": docking} if docking is not None else {}

    async def current_action():
        return action

    async def laserscan(max_points=0):
        return {"points": _ring(ring)}

    # The pose turns with every nudge, so headings add up as on the robot
    # (a caller's own dict is moved, so it can walk the robot elsewhere).
    where = {"x": 0.0, "y": 0.0, "yaw": 0.0} if where is None else where

    async def pose():
        return dict(where)

    async def nudge(forward_m=0.0, turn_rad=0.0):
        sent.append((forward_m, turn_rad))
        where["yaw"] += turn_rad

    monkeypatch.setattr(settings, "robot_chassis_motion_enabled", motion)
    monkeypatch.setattr(robot_chassis, "configured", lambda: True)
    monkeypatch.setattr(robot_chassis, "power", power)
    monkeypatch.setattr(robot_chassis, "current_action", current_action)
    monkeypatch.setattr(robot_chassis, "laserscan", laserscan)
    monkeypatch.setattr(robot_chassis, "nudge", nudge)
    monkeypatch.setattr(robot_chassis, "pose", pose)
    monkeypatch.setattr(robot_chassis, "run_fence", lambda: None)
    monkeypatch.setitem(robot_chassis.STATE, "moving", moving)
    monkeypatch.setattr(robot_face_link, "nav_activity", lambda session=None: nav)
    monkeypatch.setattr(session_module, "_active", None)
    rows = []
    monkeypatch.setattr(greeter.turnlog, "record", lambda event, **f: rows.append((event, f)))
    return sent, rows


def _turn(want=None):
    still = _Still()
    turned = asyncio.run(greeter._turn_to_check(still, dict(want or LOOK)))
    return turned, still


def test_a_turn_off_the_dock_with_room_is_in_place_and_bounded(monkeypatch):
    sent, rows = _robot(monkeypatch)
    monkeypatch.setattr(settings, "face_turn_max_deg", 90.0)   # .env cannot raise the ceiling
    turned, still = _turn(dict(LOOK, bearing=70.0))
    assert turned is True
    assert sent == [(0.0, pytest.approx(math.radians(30.0)))]   # never a step toward anybody
    assert still.until is not None
    assert rows == [("face_turn", {"turned": True, "bearing": 70.0, "angle": 30.0, "px": 50,
                                   "docking": "not_on_dock", "returns": 400,
                                   "gap_m": rows[0][1]["gap_m"], "offset": 0.0})]


def test_a_turn_to_the_right_is_negative(monkeypatch):
    sent, _ = _robot(monkeypatch)
    turned, _ = _turn(dict(LOOK, bearing=-12.0))
    assert turned is True and sent == [(0.0, pytest.approx(math.radians(-12.0)))]


@pytest.mark.parametrize("setup, why", [
    ({"docking": "on_dock"}, "docked"),
    ({"docking": None}, "dock_unknown"),
    ({"nav": "active"}, "walking"),
    ({"nav": "stale"}, "walking"),
    ({"nav": "unknown"}, "nav_unknown"),
    ({"ring": 0.5}, "no_room"),
    ({"motion": False}, "motion_disabled"),
    ({"moving": True}, "driving"),
    ({"action": {"state": {"status": 1}}}, "board_busy"),
])
def test_a_turn_is_refused(monkeypatch, setup, why):
    sent, rows = _robot(monkeypatch, **setup)
    turned, still = _turn()
    assert turned is False and sent == [] and still.until is None
    assert rows[0][0] == "face_turn" and rows[0][1]["turned"] is False
    assert rows[0][1]["why"] == why


def test_no_turn_while_somebody_is_talking(monkeypatch):
    from app import session as session_module

    sent, rows = _robot(monkeypatch)

    class _Talking:
        summoned = False

    monkeypatch.setattr(session_module, "_active", _Talking())
    turned, _ = _turn()
    assert turned is False and sent == [] and rows[0][1]["why"] == "live_session"


def test_a_centred_face_needs_no_turn_and_one_turn_per_cooldown(monkeypatch):
    sent, rows = _robot(monkeypatch)
    assert _turn(dict(LOOK, bearing=3.0))[0] is False and rows[-1][1]["why"] == "centred"
    assert _turn()[0] is True
    assert _turn()[0] is False and rows[-1][1]["why"] == "cooldown"
    assert len(sent) == 1


def test_turns_never_add_up_past_the_limit_from_where_she_was_put(monkeypatch):
    """Nothing turns her back after a check. One 30 degree turn per far face,
    all day, on whichever side they stand, was a random walk of the heading
    -- until the lens faced a wall. Every turn stays within FACE_TURN_MAX_DEG
    of the heading she had before the first one; turning back is fine; moved
    elsewhere (walked, driven, docked), the new spot is home."""
    where = {"x": 1.0, "y": 2.0, "yaw": 0.5}
    sent, rows = _robot(monkeypatch, where=where)

    def again(bearing):
        monkeypatch.setattr(greeter, "_last_turn_at", 0.0)        # past the cooldown
        return _turn(dict(LOOK, bearing=bearing))[0]

    assert again(25.0) is True                                    # 25 of 30
    assert again(25.0) is False and rows[-1][1]["why"] == "at_limit"   # 5 left: not worth it
    assert rows[-1][1]["offset"] == pytest.approx(25.0)
    assert again(-20.0) is True                                   # back toward home: fine
    assert again(30.0) is True and rows[-1][1]["angle"] == pytest.approx(25.0)   # cut at +30
    assert math.degrees(where["yaw"] - 0.5) == pytest.approx(30.0)
    assert len(sent) == 3
    where["x"] += 1.0                                             # she was moved: a new home
    assert again(25.0) is True
    assert math.degrees(where["yaw"] - 0.5) == pytest.approx(55.0)


def test_no_turn_without_a_readable_pose(monkeypatch):
    from app import robot_chassis

    sent, rows = _robot(monkeypatch)

    async def blind():
        return {}

    monkeypatch.setattr(robot_chassis, "pose", blind)
    assert _turn()[0] is False and sent == [] and rows[-1][1]["why"] == "pose_unreadable"


def test_the_fences_of_the_primitive_still_apply(monkeypatch):
    from app import robot_chassis

    sent, rows = _robot(monkeypatch)

    async def fenced(forward_m=0.0, turn_rad=0.0):
        raise robot_chassis.Fenced("routes_own_the_base")

    monkeypatch.setattr(robot_chassis, "nudge", fenced)
    assert _turn()[0] is False and rows[-1][1]["why"] == "routes_own_the_base"


def test_the_loop_only_turns_with_the_switch_on(monkeypatch):
    calls = []

    async def fake_turn(watcher, want):
        calls.append(want)
        return False

    monkeypatch.setattr(greeter, "_turn_to_check", fake_turn)
    _loop(monkeypatch, LOOK, face_turn_to_check=False)
    assert calls == []
    _loop(monkeypatch, LOOK, face_turn_to_check=True)
    assert len(calls) == 1                         # one per probable hold
    _loop(monkeypatch, dict(LOOK, far=False, hold=2.0), face_turn_to_check=True)
    assert len(calls) == 1                         # near faces: no turn


# -- 4. the handshake goes with a name ---------------------------------------------

def _greet_with(monkeypatch, sighting, **conf):
    from app import events

    got, rows = [], []

    async def fake_announce(text, *, source, **kw):
        got.append(kw.get("arm_greeting"))
        return True

    monkeypatch.setattr(events, "announce", fake_announce)
    monkeypatch.setattr(greeter.turnlog, "record", lambda event, **f: rows.append((event, f)))
    for key, value in conf.items():
        monkeypatch.setattr(settings, key, value)
    asyncio.run(greeter._greet(sighting))
    return got[0], rows[0][1]


def _known(faces_in_frame=1):
    return facewatch.Sighting(kind="known", name="ต้า", score=0.7, meta={"group": "staff"},
                              faces_in_frame=faces_in_frame)


def test_a_named_greeting_alone_offers_the_handshake(monkeypatch):
    arm, row = _greet_with(monkeypatch, _known(), face_handshake_named_only=True,
                           robot_greeting_gesture_enabled=True)
    assert arm is True and row["handshake"] is True and row["handshake_why"] == "named_alone"


@pytest.mark.parametrize("sighting, gesture, why", [
    (facewatch.Sighting(kind="stranger", name=None, score=0.2, faces_in_frame=1), True, "stranger"),
    (_known(faces_in_frame=2), True, "company"),
    (_known(faces_in_frame=0), True, "company"),               # not counted = not alone
    (_known(), False, "gesture_off"),
])
def test_no_handshake_for_strangers_company_or_with_gestures_off(monkeypatch, sighting, gesture, why):
    arm, row = _greet_with(monkeypatch, sighting, face_handshake_named_only=True,
                           robot_greeting_gesture_enabled=gesture)
    assert arm is False and row["handshake"] is False and row["handshake_why"] == why


def test_switch_off_keeps_the_old_gesture_and_the_old_row(monkeypatch):
    stranger = facewatch.Sighting(kind="stranger", name=None, score=0.2)
    arm, row = _greet_with(monkeypatch, stranger, face_handshake_named_only=False)
    assert arm is True and "handshake" not in row


def test_the_watcher_counts_the_faces_it_named(monkeypatch):
    """`faces_in_frame` comes from the deciding frame: with
    FACE_NAME_WHEN_ALONE off a name can be said with company — the
    handshake still is not offered."""
    other = faces.Face((300, 0, 480, 180), 0.9, BOB)
    w = _watcher(name_when_alone=False, probable_wait_s=0)
    _sees(monkeypatch, ALICE, extra=(other,))
    seen = w.see(SHARP, now=0.0)
    assert seen.name == "ต้า" and seen.faces_in_frame == 2
    w2 = _watcher(probable_wait_s=0)
    _sees(monkeypatch, ALICE)
    assert w2.see(SHARP, now=0.0).faces_in_frame == 1


# -- the page answers a crop request, once ------------------------------------------

_CROP_HARNESS = r"""'use strict';
// Runs the page's REAL ?cam=1 camera sender (cut out verbatim) against a fake
// DOM/camera/socket and a virtual clock, and answers one camctl crop.
const fs = require('fs');
const vm = require('vm');
const [, , PAGE] = process.argv;
const html = fs.readFileSync(PAGE, 'utf8');
const marker = "// ---- the robot's own camera, sent to the server (`?cam=1`) ----";
const at = html.indexOf(marker);
if (at < 0) throw new Error('marker not found');
const code = html.slice(at, html.indexOf('</script>', at));
let now = 1e6, nextId = 1;
const timers = new Map();
const clock = {
  setTimeout(fn, ms) { const id = nextId++; timers.set(id, { at: now + Math.max(0, +ms || 0), fn, every: 0 }); return id; },
  setInterval(fn, ms) { const id = nextId++; const e = Math.max(1, +ms || 0); timers.set(id, { at: now + e, fn, every: e }); return id; },
  clearTimeout(id) { timers.delete(id); }, clearInterval(id) { timers.delete(id); },
};
async function flush() { for (let i = 0; i < 20; i++) await new Promise(r => setImmediate(r)); }
async function advance(ms) {
  const end = now + ms;
  await flush();
  for (;;) {
    let best = null, bestId = 0;
    for (const [id, t] of timers) if (t.at <= end && (!best || t.at < best.at)) { best = t; bestId = id; }
    if (!best) break;
    now = best.at;
    if (best.every) best.at += best.every; else timers.delete(bestId);
    best.fn();
    await flush();
  }
  now = end;
  await flush();
}
const draws = [];
function el(tag) {
  return { tagName: tag, style: {}, children: [], appendChild(c) { this.children.push(c); return c; },
    remove() {}, addEventListener() {},
    getContext() { const c = this; return { drawImage(...a) { draws.push({ w: c.width, h: c.height, args: a.slice(1) }); } }; },
    toBlob(cb, type, q) { cb(new Blob([new Uint8Array([0xff, 0xd8, Math.round((q || 0) * 100)])])); },
    width: 0, height: 0 };
}
function video() {
  const v = el('video');
  v.play = () => Promise.resolve();
  Object.defineProperty(v, 'videoWidth', { get() { return this.srcObject ? 1920 : 0; } });
  Object.defineProperty(v, 'videoHeight', { get() { return this.srcObject ? 1080 : 0; } });
  Object.defineProperty(v, 'currentTime', { get() { return now / 1000; } });
  return v;
}
const sockets = [];
class FakeWS {
  constructor(url) {
    this.url = url; this.readyState = 0; this.bufferedAmount = 0; this.sent = [];
    sockets.push(this);
    clock.setTimeout(() => { this.readyState = 1; if (this.onopen) this.onopen(); }, 5);
  }
  send(data) { this.sent.push(data); }
  close() { this.readyState = 3; }
  serverSays(obj) { this.onmessage({ data: JSON.stringify(obj) }); }
}
FakeWS.OPEN = 1;
let applied = 0;
const track = { stop() {}, getCapabilities() { return { width: { max: 1920 }, height: { max: 1080 }, zoom: { min: 1, max: 4 } }; },
  applyConstraints() { applied += 1; return Promise.resolve(); } };
const stream = { getVideoTracks: () => [track], getTracks: () => [track] };
const sb = {
  URLSearchParams, WebSocket: FakeWS, Promise, JSON, Math, Number, Object, Blob,
  setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
  setInterval: clock.setInterval, clearInterval: clock.clearInterval,
  Date: { now: () => now }, console: { log() {}, warn() {}, error() {} },
  location: { search: '?cam=1&camdebug=0', protocol: 'https:', host: 'robot.test' },
  document: { hidden: false, readyState: 'complete', body: el('body'), addEventListener() {},
    createElement: t => (t === 'video' ? video() : el(t)) },
  navigator: { mediaDevices: { getUserMedia: () => Promise.resolve(stream),
    enumerateDevices: () => Promise.resolve([{ kind: 'videoinput', deviceId: 'c0' }]) } },
  addEventListener() {},
};
sb.window = sb;
async function crops(ws) {
  const out = [];
  for (const d of ws.sent) {
    const b = Buffer.from(await d.arrayBuffer());
    if (b[0] !== 0) continue;
    const tag = b.slice(0, 10).toString('latin1');
    const nl = b.indexOf(10, 10);
    out.push({ tag, head: JSON.parse(b.slice(10, nl).toString('utf8')), jpeg: [...b.slice(nl + 1)] });
  }
  return out;
}
(async () => {
  vm.createContext(sb);
  vm.runInContext(code, sb);
  await advance(3000);
  const ws = sockets[0];
  ws.serverSays({ type: 'camctl', fps: 6 });
  await advance(300);
  const before = (await crops(ws)).length;
  ws.serverSays({ type: 'camctl', crop: { id: 1, box: [100, 100, 150, 150], w: 1024, h: 768, pad: 1.5, photo: true } });
  await advance(300);
  ws.serverSays({ type: 'camctl', zoom: 1, crop: { id: 1, box: [100, 100, 150, 150], w: 1024, h: 768, pad: 1.5 } });
  await advance(300);
  const got = await crops(ws);
  const cropDraws = draws.filter(d => d.args.length === 8);
  // The next request re-sends the same zoom (latest-wins control): the
  // camera must not be reconfigured for it.
  ws.serverSays({ type: 'camctl', zoom: 1, crop: { id: 2, box: [100, 100, 150, 150], w: 1024, h: 768 } });
  await advance(300);
  const after = (await crops(ws)).length;
  console.log(JSON.stringify({ before, got, cropDraw: cropDraws[0] || null, applied, after }));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


def test_the_page_answers_each_crop_request_once_at_q95(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("NOT CHECKED: the face page's crop answer (node is not installed here)")
    harness = tmp_path / "crop.cjs"
    harness.write_text(_CROP_HARNESS, encoding="utf-8")
    res = subprocess.run([node, str(harness), str(PAGE)], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert res.returncode == 0, res.stderr[-1500:]
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert out["before"] == 0                      # no crop without a request
    assert len(out["got"]) == 1                    # id 1 answered once, not again on the re-send
    crop = out["got"][0]
    assert crop["tag"] == "\u0000EMMACROP\n"
    head = crop["head"]
    assert head["id"] == 1 and head["src"] == "video"   # no ImageCapture here: the video it is
    # box 50 px + 1.5 face a side = 200 px of frame, centred on the face
    assert head["region"] == [25, 25, 225, 225]
    assert head["video"] == [1920, 1080] and head["video_max"] == [1920, 1080]
    assert crop["jpeg"][:3] == [0xFF, 0xD8, 95]    # the JPEG, encoded at q95
    # Cut from the camera's own pixels (1920 wide), not the 1024 frame:
    # 200 frame px = 375 video px, scale 1.875.
    assert head["scale"] == pytest.approx(1920 / 1024)
    assert out["cropDraw"]["w"] == 375
    assert out["after"] == 2                       # a new id is a new look
    assert out["applied"] == 1                     # zoom 1 applied once, not per crop request


def _photo_harness() -> str:
    """The crop harness with a camera that has a bigger photo than its video
    (FACE_ZOOM_PHOTO takes the still path) and a preview that stands still
    while the still is being taken -- what Android may do."""
    h = _CROP_HARNESS
    h = h.replace("let now = 1e6, nextId = 1;", "let now = 1e6, nextId = 1, frozenAt = null;")
    h = h.replace("Object.defineProperty(v, 'currentTime', { get() { return now / 1000; } });",
                  "Object.defineProperty(v, 'currentTime', { get() { return frozenAt !== null ? frozenAt : now / 1000; } });")
    h = h.replace("const sb = {", """class FakeImageCapture {
  getPhotoCapabilities() { return Promise.resolve({ imageWidth: { max: 3840 }, imageHeight: { max: 2160 }, fillLightMode: ['off'] }); }
  takePhoto() { frozenAt = now / 1000; return new Promise(() => {}); }
}
const sb = {
  ImageCapture: FakeImageCapture,""")
    main = h.index("(async () => {")
    return h[:main] + r"""
async function frames(ws, from) {
  let n = 0;
  for (const d of ws.sent.slice(from)) {
    const b = Buffer.from(await d.arrayBuffer());
    if (b[0] === 0xff && b[1] === 0xd8 && b[2] === 70) n += 1;
  }
  return n;
}
(async () => {
  vm.createContext(sb);
  vm.runInContext(code, sb);
  await advance(3000);
  const ws = sockets[0];
  ws.serverSays({ type: 'camctl', fps: 6 });
  await advance(500);
  const live = await frames(ws, 0);
  ws.serverSays({ type: 'camctl', crop: { id: 1, box: [100, 100, 150, 150], w: 1024, h: 768, pad: 1.5, photo: true } });
  await advance(300);                                  // first look: caps not known yet, a video crop
  ws.serverSays({ type: 'camctl', crop: { id: 2, box: [100, 100, 150, 150], w: 1024, h: 768, pad: 1.5, photo: true } });
  const mark = ws.sent.length;
  await advance(2000);                                 // the still is out, the preview stands still
  const frozen = await frames(ws, mark);
  frozenAt = null;                                     // the preview moves again (still out)
  const mark2 = ws.sent.length;
  await advance(1000);
  const moving = await frames(ws, mark2);
  console.log(JSON.stringify({ live, frozen, moving }));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


def test_a_frozen_preview_during_a_still_is_not_sent_as_new_frames(tmp_path):
    """FACE_ZOOM_PHOTO: a still can stand the preview still, and the frame
    loop kept sending that one picture every tick -- the greeter's
    confirmation (3 frames for a name) counting one moment three times."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("NOT CHECKED: frames during a still (node is not installed here)")
    harness = tmp_path / "photo.cjs"
    harness.write_text(_photo_harness(), encoding="utf-8")
    res = subprocess.run([node, str(harness), str(PAGE)], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert res.returncode == 0, res.stderr[-1500:]
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert out["live"] >= 2, out                   # the harness does send frames
    assert out["frozen"] <= 1, out                 # the moment it stood at, once -- not N frames
    assert out["moving"] >= 4, out                 # a moving preview is sent, still out or not


def test_the_watchers_decisions_reach_the_turn_log(monkeypatch):
    rows = []
    monkeypatch.setattr(greeter.turnlog, "record", lambda event, **f: rows.append((event, f)))

    class _W:
        def drain_events(self):
            return [{"event": "face_probable", "phase": "start", "px": 52, "score": 0.41}]

    greeter._log_watch_events(_W())
    greeter._log_watch_events(object())            # a double without a queue: nothing, no error
    assert rows == [("face_probable", {"phase": "start", "px": 52, "score": 0.41})]
