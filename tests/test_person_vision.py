"""The head camera says whether that is a person (app/person_vision.py), and the walk up to whoever she
sees uses it (app/face_approach.py, PERSON_VISION; owner 5 Nov 2026: "ให้กล้องช่วยตรวจดูไม่ได้หรอ แบบเห็นเป็นคน
ก็เข้าไปทัก"; docs/emma-face-approach.md section 11).

No robot: the fake board and little world of tests/test_person_approach.py, the real lidar tracker and the
real event_walk over a fake board_walk. Nothing here turns a wheel or a servo. The model's own answers,
on real head-camera frames, are at the end: they need the model and .tmp/walk-frames, and say so if not.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import time
import types
from pathlib import Path

import numpy as np
import pytest

from app import event_host, face_approach as fa, head_follow, lidar_people as lp, person_vision, session as session_module
from app.config import settings
from tests.test_face_approach import FakeProc, world  # noqa: F401 -- `world` is the fixture
from tests.test_person_approach import (FRAME, Grid, _candidate, _poll, _room_settled, _someone_standing, _walks_in,  # noqa: F401
                                        last_what, last_why, road)
from app.person_vision import Box

ROOT = Path(__file__).resolve().parent.parent
STANDING_BOX = Box(250, 100, 750, 760, 0.8)
WATCHER = types.SimpleNamespace(gallery=None, suppressed_at=None)


class Seen:
    """Something in the picture, located the way the door camera gives it."""

    def __init__(self, px, x=400):
        self.bbox = (x, 200, x + px, 200 + px)
        self.kps = None
        self.vec = None


@pytest.fixture
def eyes(road, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "person_vision", True)
    monkeypatch.setattr(settings, "person_vision_every_s", 0.0)
    monkeypatch.setattr(settings, "face_approach_every_s", 0.0)
    monkeypatch.setattr(settings, "head_search_tilt", 18)
    monkeypatch.setattr(person_vision, "available", lambda: True)
    monkeypatch.setattr(fa, "LOOK_TILT_S", 0.12)
    monkeypatch.setattr(fa, "LOOK_FRAME_S", 0.0)
    road.boxes = []
    road.asked = 0

    def people(frame):
        road.asked += 1
        return list(road.boxes)

    monkeypatch.setattr(person_vision, "people", people)
    return road


def _go_watching(c, found=()):
    """The approach, with the door camera's frames coming in while it runs."""
    async def main():
        task = asyncio.create_task(fa._approach(c))
        while not task.done():
            fa.observe(WATCHER, FRAME, list(found), time.monotonic())
            await asyncio.sleep(0.005)
        await task
    asyncio.run(main())


def _stood_all_along(w, where=(3.5, 0.0)):
    """Somebody there since before she stopped: background, never a track that walked."""
    _poll(w, where, n=16)
    assert fa._person_candidate(w.t) is None, "the lidar alone never had them"


def _glance(w):
    return asyncio.run(fa._scan_candidate(w.t))


def _body(cx=512.0, px=500, w=1024, head=0.0):
    return {"kind": "person", "via": "body", "name": None, "px": px, "cx": cx, "w": w, "head": head,
            "at": time.monotonic(), "faces": 0, "score": 0.8}


# ---- somebody who stood still all along ---------------------------------------------------------

def test_somebody_who_stood_still_all_along_is_looked_at_and_walked_up_to(eyes):
    _stood_all_along(eyes)
    c = _glance(eyes)
    assert c["via"] == "scan" and c["range_m"] == pytest.approx(3.5, abs=0.15)
    eyes.boxes = [STANDING_BOX]
    _go_watching(c)
    assert c["confirmed"] is True
    assert eyes.pans[0] == 0, "straight ahead: the head does not move sideways for it"
    assert any("to" in s for s in eyes.steps), "she walked"
    assert len(eyes.said) == 1 and last_what() == "greeted"


def test_a_chair_somebody_carried_in_gets_no_walk_no_word_no_hand(eyes):
    # The owner's chair. The lidar: walked in on its own feet and stood. The camera: no person.
    c = _someone_standing(eyes)
    eyes.boxes = []
    _go_watching(c)
    assert last_why() == "not_a_person"
    assert eyes.steps is None, "not a step"
    assert eyes.said == [] and eyes.shook == [], "not a word"
    assert eyes.tilts == [fa._tilt_for(c["range_m"]), 18], "two looks: level for somebody standing, lower for somebody sitting"


def test_the_chair_is_not_looked_at_again_every_ten_seconds(eyes):
    to = (3.5, 0.0)
    c = _someone_standing(eyes, to)
    _go_watching(c)
    assert last_why() == "not_a_person"
    _poll(eyes, to, n=40)                       # 20 s of it standing there: TRACK_RETRY_S is 10
    assert _candidate(eyes) is None
    assert _glance(eyes) is None, "and the glances know the place"


def test_somebody_sitting_is_found_by_the_second_look(eyes, monkeypatch):
    c = _someone_standing(eyes)
    real = head_follow.look_hint

    def looked(pct, pan=None):
        real(pct, pan)
        if pct == 18:
            eyes.boxes = [STANDING_BOX]         # only the lower look has them in the picture

    monkeypatch.setattr(head_follow, "look_hint", looked)
    _go_watching(c)
    assert c["confirmed"] is True and any("to" in s for s in eyes.steps)


def test_one_box_in_one_frame_is_not_a_person(eyes, monkeypatch):
    c = _someone_standing(eyes)
    calls = []

    def once(frame):
        if fa.STATE["phase"] != "looking":
            return []
        calls.append(1)
        return [STANDING_BOX] if len(calls) == 1 else []

    monkeypatch.setattr(person_vision, "people", once)
    _go_watching(c)
    assert len(calls) > 1, "it did keep looking"
    assert last_why() == "not_a_person" and eyes.steps is None


def test_a_face_in_the_picture_answers_the_look_without_the_model(eyes):
    c = _someone_standing(eyes)
    _go_watching(c, found=[Seen(90)])
    assert c["confirmed"] is True


def test_the_detector_s_tiny_answers_are_no_face_here_either(eyes):
    # 184 of 425 faces in the walk pictures were under 15 px: not a person there.
    c = _someone_standing(eyes)
    _go_watching(c, found=[Seen(12)])
    assert last_why() == "not_a_person"


# ---- a person's shape in the lens ---------------------------------------------------------------

def test_a_person_s_shape_for_three_looks_in_one_place_is_somebody_to_walk_up_to(eyes):
    eyes.boxes = [STANDING_BOX]
    for i in range(2):
        fa._note_body(FRAME, [], eyes.t + i)
        assert fa._eyes["body"] is None, "not yet"
    fa._note_body(FRAME, [], eyes.t + 2)
    body = fa._eyes["body"]
    assert body["via"] == "body" and body["cx"] == STANDING_BOX.cx
    assert fa._person_candidate(eyes.t + 2) is body
    assert fa._person_candidate(eyes.t + 3) is None, "once, then TRACK_RETRY_S"


@pytest.mark.parametrize("second", [Box(800, 100, 1000, 700, 0.8), None])
def test_somebody_going_past_is_not(eyes, second):
    eyes.boxes = [STANDING_BOX]
    fa._note_body(FRAME, [], eyes.t)
    fa._note_body(FRAME, [], eyes.t + 1)
    eyes.boxes = [second] if second else []
    fa._note_body(FRAME, [], eyes.t + 2)
    assert fa._eyes["body"] is None
    eyes.boxes = [STANDING_BOX]
    fa._note_body(FRAME, [], eyes.t + 3)
    assert fa._eyes["body"] is None, "the count starts again"


def test_the_look_keeps_its_pace(eyes, monkeypatch):
    monkeypatch.setattr(settings, "person_vision_every_s", 1.0)
    eyes.boxes = [STANDING_BOX]
    for i in range(10):
        fa._note_body(FRAME, [], eyes.t + i * 0.1)
    assert eyes.asked == 1, "97 ms on one core: once a second"


def test_a_face_the_door_can_greet_is_the_door_s(eyes):
    eyes.boxes = [STANDING_BOX]
    door = int(settings.face_approach_door_px)
    for i in range(5):
        fa._note_body(FRAME, [Seen(door)], eyes.t + i)
    assert fa._eyes["body"] is None and eyes.asked == 0, "and it does not even ask"


def test_she_walks_to_a_shape_with_no_second_look(eyes):
    _stood_all_along(eyes)
    eyes.boxes = [STANDING_BOX]
    c = _body()
    _go_watching(c)
    assert any("to" in s for s in eyes.steps)
    assert all(pan is None for pan in eyes.pans), "the camera has seen them already: no sideways look"
    assert len(eyes.said) == 1 and eyes.shook == [], "no face: words, and no hand"


def test_a_person_on_a_poster_has_no_legs_on_the_lidar(eyes):
    eyes.people = []
    _poll(eyes, n=16)
    _go_watching(_body())
    assert last_why() == "no_lidar_target" and eyes.steps is None


def test_a_box_goes_to_the_nearest_thing_in_its_cone(eyes):
    # They are a little off the box's middle; something behind them is dead on it. Not "closest to the line".
    eyes.people = [(3.5, 0.15)]
    eyes.things = [(4.6, 0.0, 0.08)]
    points, pose = asyncio.run(fa._scan())
    p = fa._plan_now(_body(), points, pose, Grid())
    assert p["range_m"] == pytest.approx(3.5, abs=0.15)


def test_what_never_showed_a_face_is_not_walked_to_twice(eyes):
    # The one thing the replay called a person with no face: a shape under black cloth, 3 of 8 calls.
    _stood_all_along(eyes)
    eyes.boxes = [STANDING_BOX]
    _go_watching(_body())
    assert len(eyes.said) == 1
    assert fa._not_person_here((3.5, 0.0), time.monotonic())
    eyes.steps = None
    fa.STATE.update(last_any=-math.inf, spots=[], retry_at=-math.inf)   # past the gap and the 10 minutes
    _go_watching(_body())
    assert last_why() == "not_a_person" and eyes.steps is None and len(eyes.said) == 1


def test_somebody_who_walked_in_is_not_set_aside_for_their_back_at_the_stop(eyes):
    # Only the road that has no other check. A track has been looked at already, and it walked in.
    c = _someone_standing(eyes)
    eyes.boxes = [STANDING_BOX]
    _go_watching(c)
    assert c["confirmed"] is True and len(eyes.said) == 1
    assert not fa._not_person_here((3.5, 0.0), time.monotonic())


def test_a_face_at_the_stop_sets_nothing_aside(eyes):
    # ~1.4 m from her: a real face there is ~330 px through the 19 deg lens
    _stood_all_along(eyes)
    eyes.boxes = [STANDING_BOX]
    _go_watching(_body(), found=[Seen(330, x=350)])
    assert not fa._not_person_here((3.5, 0.0), time.monotonic())


def test_a_face_far_behind_what_she_stopped_at_is_not_their_face(eyes):
    """Review 6 Nov: a 90 px face -- somebody ~5 m behind -- anywhere in the picture was "seen" at the
    stop, and the hand went out to the chair she had walked up to."""
    _stood_all_along(eyes)
    eyes.boxes = [STANDING_BOX]
    _go_watching(_body(), found=[Seen(90)])
    assert eyes.shook == [], "no hand without their own face"
    assert fa._not_person_here((3.5, 0.0), time.monotonic()), "a no-face greeting on the box road, as before"


# ---- what is worth a glance ---------------------------------------------------------------------

def test_what_the_map_has_is_not_worth_a_glance(eyes):
    eyes.grid = Grid(walls=[(3.3, -0.3, 3.7, 0.3)])
    _stood_all_along(eyes)
    assert _glance(eyes) is None


def test_outside_staff_s_area_is_not_worth_a_glance(eyes):
    fa._save_area([[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0]])
    _stood_all_along(eyes)
    assert _glance(eyes) is None


def test_one_glance_per_look_every_s(eyes):
    _stood_all_along(eyes)
    assert _glance(eyes) is not None
    eyes.t += 1.5
    assert asyncio.run(fa._scan_candidate(eyes.t)) is None, "the head is a servo"
    _poll(eyes, (3.5, 0.0), n=44)               # 22 s
    assert _glance(eyes) is not None


def test_a_glance_goes_through_the_list_once_a_second_not_five_times(eyes, monkeypatch):
    _stood_all_along(eyes)
    fa.STATE["looked_at"] = eyes.t              # a glance just went
    calls = []
    real = fa.area

    def area():
        calls.append(1)
        return real()

    monkeypatch.setattr(fa, "area", area)
    fa.STATE["not_people"] = [(3.5, 0.0, eyes.t + 1000)]     # nothing to hand out: it keeps asking
    for i in range(5):
        lp.STATE["tracker"].at = eyes.t + 30 + i * 0.2
        assert asyncio.run(fa._scan_candidate(eyes.t + 30 + i * 0.2)) is None
    assert len(calls) == 1


def test_no_glances_at_zero_and_none_with_the_switch_off(eyes, monkeypatch):
    _stood_all_along(eyes)
    monkeypatch.setattr(settings, "face_approach_look_every_s", 0.0)
    assert _glance(eyes) is None
    monkeypatch.setattr(settings, "face_approach_look_every_s", 20.0)
    monkeypatch.setattr(settings, "person_vision", False)
    assert _glance(eyes) is None


def test_somebody_greeted_is_not_glanced_at(eyes):
    _stood_all_along(eyes)
    fa._remember_spot((3.5, 0.0), eyes.t)
    assert _glance(eyes) is None


def test_a_glance_she_could_not_have_walked_is_set_aside_without_a_head_move(eyes):
    _stood_all_along(eyes)
    eyes.things = [(2.5, 0.0, 0.25)]            # a table between her and them
    fa.STATE["spots"] = []
    c = {"kind": "person", "via": "scan", "name": None, "world": (3.5, 0.0), "range_m": 3.5, "bearing_deg": 0.0,
         "at": eyes.t, "faces": 0}
    _go_watching(c)
    assert last_why() in ("path_blocked", "no_lidar_target")
    assert eyes.tilts == [] and fa._not_person_here((3.5, 0.0), time.monotonic())


def test_the_lidar_lists_them_only_while_that_road_is_armed(eyes):
    _stood_all_along(eyes)
    assert any(abs(t.distance - 3.5) < 0.2 for t in lp.things(eyes.t))
    assert lp.people(eyes.t) == [], "background, as ever"
    fa.STATE["staff_on"] = False
    _poll(eyes, (3.5, 0.0))
    assert lp.things(eyes.t) == [], "not armed: not read, and yesterday's list is not an answer"


# ---- turning to look -----------------------------------------------------------------------------

@pytest.mark.parametrize("bearing,pan", [(0.0, 0), (10.0, 27), (-10.0, -27), (22.0, 59), (25.0, 60), (-25.0, -60),
                                         (29.0, None), (-29.0, None), (90.0, None)])
def test_how_far_the_head_goes(monkeypatch, bearing, pan):
    monkeypatch.setattr(settings, "head_follow", True)
    monkeypatch.setattr(settings, "head_pan_max", 60)
    assert fa._pan_for(bearing) == pan


def test_with_the_head_off_only_the_lens_is_left(monkeypatch):
    monkeypatch.setattr(settings, "head_follow", False)
    assert fa._pan_for(5.0) == 0 and fa._pan_for(8.0) is None


def test_the_head_looks_that_way_and_is_held_inside_its_limits(monkeypatch):
    from tests.test_head_follow import Face

    monkeypatch.setattr(settings, "head_follow", True)
    monkeypatch.setattr(settings, "head_calibrate", False)
    monkeypatch.setattr(settings, "head_pan_max", 60)
    monkeypatch.setattr(settings, "head_tilt_down_max", 18)
    monkeypatch.setattr(settings, "head_tilt_up_max", 12)
    from app import focus, robot_face_link, walk_frames

    monkeypatch.setattr(focus, "attention", lambda t=None: {"bearing": None, "source": None, "track": None})
    monkeypatch.setattr(robot_face_link, "nav_activity", lambda session=None: "idle")
    monkeypatch.setattr(walk_frames, "base_moving", lambda: False)
    face = Face()
    follower = head_follow.HeadFollow(face)
    head_follow.reset()
    head_follow.look_hint(99, 200)
    asyncio.run(follower.tick())
    assert face.ws.looks() == [(60, 18)]
    follower.sent_at = -math.inf
    head_follow.look_hint(5)
    asyncio.run(follower.tick())
    assert face.ws.looks()[-1] == (60, 5), "no pan asked for: where it is"


def test_too_far_round_a_dry_run_says_so_and_turns_no_wheel(eyes, monkeypatch):
    monkeypatch.setattr(settings, "head_follow", True)
    monkeypatch.setattr(settings, "face_approach_dry_run", True)
    side = (1.75, 3.03)                         # 3.5 m, 60 deg to her left
    c = _someone_standing(eyes, side)
    eyes.boxes = [STANDING_BOX]
    _go_watching(c)
    assert last_what() == "would_turn"
    assert eyes.steps is None and eyes.pose["yaw"] == 0.0 and eyes.tilts == []
    _poll(eyes, side, n=30)
    assert _candidate(eyes) is None


def test_too_far_round_she_turns_the_base_and_then_looks(eyes, monkeypatch):
    monkeypatch.setattr(settings, "head_follow", True)
    side = (1.75, 3.03)
    c = _someone_standing(eyes, side)
    eyes.boxes = [STANDING_BOX]
    _go_watching(c)
    assert c["confirmed"] is True
    assert eyes.pose["yaw"] == pytest.approx(math.radians(60), abs=0.1)
    assert eyes.pans[0] == 0, "now it is in front of her"
    assert any("to" in s for s in eyes.steps)


def test_after_a_turn_she_waits_for_the_base_to_count_as_still(eyes, monkeypatch):
    # walk_frames holds "moving" 15 s past the last move: looked at before that, every gate says "walking".
    from app import event_walk, robot_face_link

    monkeypatch.setattr(settings, "head_follow", True)
    side = (1.75, 3.03)
    c = _someone_standing(eyes, side)
    eyes.boxes = [STANDING_BOX]
    real = fa._settled
    until = {"t": -math.inf}

    async def settled():
        ok = await real()
        until["t"] = time.monotonic() + 0.1
        return ok

    monkeypatch.setattr(fa, "_settled", settled)
    monkeypatch.setattr(robot_face_link, "server_walking",
                        lambda: event_walk.walking() or time.monotonic() < until["t"])
    _go_watching(c)
    assert c["confirmed"] is True, last_why()


def test_she_does_not_spin_from_one_thing_to_the_next(eyes, monkeypatch):
    monkeypatch.setattr(settings, "head_follow", True)
    side = (1.75, 3.03)
    c = _someone_standing(eyes, side)
    fa.STATE["turned_at"] = time.monotonic()
    _go_watching(c)
    assert last_why() == "turned_recently" and eyes.steps is None


def test_no_turn_to_look_near_the_edge_of_staff_s_area(eyes, monkeypatch):
    monkeypatch.setattr(settings, "head_follow", True)
    side = (1.75, 3.03)
    c = _someone_standing(eyes, side)
    monkeypatch.setattr(fa, "_room_to_turn", lambda poly, x, y: False)
    _go_watching(c)
    assert eyes.steps is None and last_why() == "outside_area"


def test_a_call_whose_head_is_off_looks_only_with_the_lens(eyes, monkeypatch):
    monkeypatch.setattr(settings, "head_follow", True)
    monkeypatch.setattr(session_module._active, "_head_follow", types.SimpleNamespace(off="diverging"), raising=False)
    c = _someone_standing(eyes, (3.5, 1.0))     # 16 deg: the head would have reached
    eyes.boxes = [STANDING_BOX]
    _go_watching(c)
    assert fa.STATE["turned_at"] > -math.inf, "turned the base instead"
    # ...and the lens looked with the head where it is: no hint with a pan went to a head nobody
    # follows (6 Nov: the hint was never taken, and every such look ended "head_busy")
    assert c.get("confirmed") is True
    assert all(pan is None for pan in eyes.pans), eyes.pans


# ---- every way out of a look ---------------------------------------------------------------------

def _during_look(monkeypatch, what):
    real = head_follow.look_hint

    def looked(pct, pan=None):
        real(pct, pan)
        what()

    monkeypatch.setattr(head_follow, "look_hint", looked)


def test_staff_off_during_a_look_ends_it(eyes, monkeypatch):
    c = _someone_standing(eyes)
    _during_look(monkeypatch, lambda: fa.STATE.update(staff_on=False))
    _go_watching(c)
    assert last_why() == "not_armed" and eyes.steps is None


def test_a_scene_during_a_look_ends_it(eyes, monkeypatch):
    c = _someone_standing(eyes)
    _during_look(monkeypatch, lambda: event_host.STATE.update(seq=event_host.STATE["seq"] + 1))
    _go_watching(c)
    assert last_why() == "scene_changed" and eyes.steps is None


def test_somebody_talking_to_her_during_a_look_ends_it(eyes, monkeypatch):
    c = _someone_standing(eyes)

    def talk():
        session_module._active._ever_heard = True

    _during_look(monkeypatch, talk)
    _go_watching(c)
    assert last_why() == "conversation" and eyes.steps is None


def test_the_door_greeting_somebody_during_a_look_ends_it(eyes, monkeypatch):
    c = _someone_standing(eyes)
    _during_look(monkeypatch, lambda: fa.STATE.update(door_at=time.monotonic()))
    _go_watching(c)
    assert last_why() == "door_greeted" and eyes.steps is None


def test_the_door_may_greet_during_a_look(eyes):
    fa.STATE.update(phase="looking", kind="person")
    assert fa.holds_greeting(types.SimpleNamespace(kind="known")) is False


def test_the_corridor_is_asked_again_after_the_look(eyes, monkeypatch):
    c = _someone_standing(eyes)
    eyes.boxes = [STANDING_BOX]
    real = person_vision.people

    def blocked(frame):
        if fa.STATE["phase"] == "looking":
            eyes.things = [(2.0, 0.0, 0.25)]    # somebody stepped in while she was looking
        return real(frame)

    monkeypatch.setattr(person_vision, "people", blocked)
    _go_watching(c)
    assert c["confirmed"] is True, "she saw them"
    assert eyes.steps is None and last_why() in ("path_blocked", "no_lidar_target"), "and the way is not clear any more"


# ---- nothing to see with -------------------------------------------------------------------------

def test_no_model_no_look_no_walk(eyes, monkeypatch):
    c = _someone_standing(eyes)
    monkeypatch.setattr(person_vision, "available", lambda: False)
    _go_watching(c)
    assert last_why() == "no_vision" and eyes.steps is None


def test_staff_are_told_when_they_press(eyes, monkeypatch):
    fa.STATE["staff_on"] = False
    monkeypatch.setattr(person_vision, "available", lambda: False)
    result = fa.set_enabled(True)
    assert result["ok"] is False and result["error"] == "no_vision"
    monkeypatch.setattr(settings, "person_vision", False)
    assert fa.set_enabled(True)["ok"] is True


def test_no_line_no_look(eyes, monkeypatch):
    """The head turns inside a call (head_follow). She rings for one now (owner 6 Nov: "ถ้าเห็นคน ...
    เปิดสาย"); none comes up: no look, no walk."""
    c = _someone_standing(eyes)
    monkeypatch.setattr(session_module, "_active", None)
    monkeypatch.setattr(fa, "LINE_WAIT_S", 0.3)
    _go_watching(c)
    assert last_why() == "no_line" and eyes.steps is None and eyes.tilts == []
    assert eyes.rung == "face_approach"


def test_seeing_somebody_rings_for_the_line_the_look_needs(eyes, monkeypatch):
    """It used to refuse "no_line" here and wait for the standing line: with the line parked while nobody
    is about (owner 6 Nov: "ไม่มีใครคุยกับหุ่นหรือหุ่นไม่เห็นก็พักสายไว้"), the look would never come."""
    from app import wake

    line = session_module._active
    c = _someone_standing(eyes)
    monkeypatch.setattr(session_module, "_active", None)

    async def summon(reason="server"):
        eyes.rung = reason
        session_module._active = line
        return 1

    monkeypatch.setattr(wake, "summon", summon)
    _go_watching(c)
    assert eyes.rung == "face_approach" and eyes.tilts, "the look went ahead on the line it rang for"


def test_with_the_switch_off_the_lidar_s_word_stands(road):  # noqa: F811
    assert settings.person_vision is False
    c = _someone_standing(road)
    asyncio.run(fa._approach(c))
    assert any("to" in s for s in road.steps) and road.said


def test_a_dry_run_looks_says_and_does_not_move(eyes, monkeypatch):
    monkeypatch.setattr(settings, "face_approach_dry_run", True)
    c = _someone_standing(eyes)
    eyes.boxes = [STANDING_BOX]
    _go_watching(c)
    assert last_what() == "would_walk" and eyes.steps is None and eyes.said == []
    assert eyes.tilts, "the head does look: it is the only way to know what she would have walked to"


# ---- from the review of 5 Nov: the gates before a wheel ------------------------------------------

def _closes(eyes, monkeypatch, what):
    """`what` happens right after the last preflight before the base would turn."""
    real = fa._preflight

    async def preflight(c, grid=None):
        result = await real(c, grid)
        what()
        return result

    monkeypatch.setattr(fa, "_preflight", preflight)


def _latched(monkeypatch):
    from app import greeter

    return lambda: monkeypatch.setattr(greeter, "_app_latched", lambda link: True)


@pytest.mark.parametrize("gate,why", [("latch", "stop_latched"), ("arm", "arm_locked"), ("joystick", "driving"),
                                      ("scene", "scene_changed"), ("talk", "conversation"), ("quality", "low_quality"),
                                      ("door", "door_greeted")])
def test_a_stop_pressed_just_before_a_look_s_turn_turns_no_wheel(eyes, monkeypatch, gate, why):
    from app import robot_arm, robot_chassis

    monkeypatch.setattr(settings, "head_follow", True)
    closes = {
        "latch": _latched(monkeypatch),
        "arm": lambda: monkeypatch.setattr(robot_arm, "armed", lambda: False),
        "joystick": lambda: robot_chassis.STATE.update(moving=True),
        "scene": lambda: event_host.STATE.update(seq=event_host.STATE["seq"] + 1),
        "talk": lambda: setattr(session_module._active, "_ever_heard", True),
        "quality": lambda: setattr(eyes, "quality", 10),
        "door": lambda: fa.STATE.update(door_at=time.monotonic()),
    }
    c = _someone_standing(eyes, (1.75, 3.03))   # 60 deg: only the base can look
    eyes.boxes = [STANDING_BOX]
    _closes(eyes, monkeypatch, closes[gate])
    _go_watching(c)
    assert eyes.steps is None and eyes.pose["yaw"] == 0.0, "not a wheel"
    assert last_why() == why


def test_a_stop_while_she_settles_sends_no_head_move(eyes, monkeypatch):
    monkeypatch.setattr(settings, "head_follow", True)
    c = _someone_standing(eyes, (1.75, 3.03))
    real = fa._settled

    async def settled():
        ok = await real()
        fa.STATE["staff_on"] = False
        return ok

    monkeypatch.setattr(fa, "_settled", settled)
    _go_watching(c)
    assert eyes.tilts == [] and last_why() == "not_armed"


def test_a_stop_on_the_robot_turns_the_walk_off_until_somebody_presses_on(eyes, monkeypatch):
    # It used to come back by itself when the app's 60 s latch ran out.
    assert fa.armed()
    _latched(monkeypatch)()
    asyncio.run(fa.tick())
    assert fa.STATE["staff_on"] is False
    assert fa.set_enabled(True) == {"ok": False, "error": "stop_latched", "error_th": fa.REASONS_TH["stop_latched"]}


# ---- from the review: the whole way round, with the head's own loop and a lidar poller running ----

REAL_HINT = head_follow.look_hint
REAL_MOVED_AT = head_follow.moved_at


@pytest.fixture
def live(eyes, monkeypatch):
    """The real hint -> the head's loop -> the settle -> the frames, and the lidar polled all along."""
    from app import focus, walk_frames  # noqa: F401
    from tests.test_head_follow import Face

    monkeypatch.setattr(settings, "head_follow", True)
    monkeypatch.setattr(settings, "head_calibrate", False)
    monkeypatch.setattr(settings, "head_pan_max", 60)
    monkeypatch.setattr(settings, "head_pan_full_deg", -94.44)
    monkeypatch.setattr(settings, "head_tilt_full_deg", 181.81)
    monkeypatch.setattr(settings, "head_tilt_down_max", 18)
    monkeypatch.setattr(settings, "head_tilt_up_max", 12)
    monkeypatch.setattr(head_follow, "look_hint", REAL_HINT)
    monkeypatch.setattr(head_follow, "moved_at", REAL_MOVED_AT)
    monkeypatch.setattr(head_follow, "FORCE_GAP_S", 0.0)
    # the fake face sends no head_look_ack: an unanswered look counts as the slowest move, made short here
    monkeypatch.setattr(head_follow, "UNACKED_MOVE_S", 0.0)
    monkeypatch.setattr(fa, "HEAD_SETTLE_S", 0.03)
    monkeypatch.setattr(fa, "LOOK_TILT_S", 0.2)
    monkeypatch.setattr(fa, "LOOK_WAIT_S", 0.3)
    monkeypatch.setattr(focus, "attention", lambda t=None: {"bearing": None, "source": None, "track": None})
    head_follow.reset()
    eyes.face = Face()
    eyes.follower = head_follow.HeadFollow(eyes.face)
    yield eyes
    head_follow.reset()


def _run_live(w, c, frame_faces=lambda: []):
    async def main():
        task = asyncio.create_task(fa._approach(c))
        while not task.done():
            await w.follower.tick()
            fa.observe(WATCHER, FRAME, frame_faces(), time.monotonic())
            w.t = time.monotonic()
            await lp.poll_once(now=w.t)
            await asyncio.sleep(0.01)
        await task
    asyncio.run(main())


def _live_standing(w, where):
    c = _someone_standing(w, where)
    c["at"] = time.monotonic()
    return c


def test_a_face_the_head_left_behind_does_not_answer_the_look(live):
    # A chair at 10 deg, a person dead ahead of the lens until the head turns: then they are out of it.
    c = _live_standing(live, (3.45, 0.61))

    def faces():
        return [] if head_follow.offset_deg()[0] else [Seen(90, x=470)]

    _run_live(live, c, faces)
    assert live.face.ws.looks()[0][0] == fa._pan_for(c["bearing_deg"]), "the head did go there"
    assert last_why() == "not_a_person" and live.steps is None and live.said == []


def test_a_face_being_followed_holds_the_head_and_nothing_is_concluded(live, monkeypatch):
    from app import focus

    monkeypatch.setattr(focus, "attention", lambda t=None: {"bearing": 1.0, "source": "face", "track": 1})
    c = _live_standing(live, (3.45, 0.61))
    _run_live(live, c, lambda: [Seen(90, x=470)])
    assert last_why() == "head_busy" and live.steps is None
    assert not fa._not_person_here((3.45, 0.61), time.monotonic()), "she has not looked at it: nothing is known"


def test_somebody_there_is_seen_only_after_the_head_arrived(live):
    c = _live_standing(live, (3.45, 0.61))
    live.boxes = [STANDING_BOX]
    asked_before = []
    real = person_vision.people

    def people(frame):
        if fa.STATE["phase"] == "looking" and not live.face.ws.looks():
            asked_before.append(1)
        return real(frame)

    person_vision.people = people
    try:
        _run_live(live, c)
    finally:
        person_vision.people = real
    assert c["confirmed"] is True and any("to" in s for s in live.steps)
    assert asked_before == [], "no frame from before the head was told"


def test_a_track_she_turned_the_base_for_is_not_lost_with_the_lidar_s_frame(live):
    # The poller throws the Follower away while the base turns: she looked, saw them, and said "gone".
    c = _live_standing(live, (1.75, 3.03))
    live.boxes = [STANDING_BOX]
    _run_live(live, c)
    assert c["confirmed"] is True
    assert last_why() != "gone" and any("to" in s for s in live.steps), last_why()


# ---- from the review: how often, and what counts ---------------------------------------------------

def test_no_more_than_one_look_in_the_look_gap_whatever_asks_for_it(eyes):
    c = _someone_standing(eyes)
    fa.STATE["head_at"] = time.monotonic()
    _go_watching(c)
    assert last_why() == "looked_recently" and eyes.tilts == []
    assert not fa._not_person_here((3.5, 0.0), time.monotonic()), "it comes back: nothing was looked at"


def test_a_look_is_at_least_ten_seconds_after_the_last_even_with_glances_set_faster(eyes, monkeypatch):
    monkeypatch.setattr(settings, "face_approach_look_every_s", 1.0)
    c = _someone_standing(eyes)
    fa.STATE["head_at"] = time.monotonic() - 5.0
    _go_watching(c)
    assert last_why() == "looked_recently"


def test_a_thing_past_the_head_s_reach_gets_no_glance_and_no_turn(eyes, monkeypatch):
    monkeypatch.setattr(settings, "head_follow", True)
    _stood_all_along(eyes, (1.75, 3.03))        # 60 deg to her left, standing still all along
    assert _glance(eyes) is None
    assert fa.STATE["looked_at"] == -math.inf, "and no glance was spent on it"


def test_a_box_at_the_edge_of_the_picture_is_somebody_else(eyes):
    c = _someone_standing(eyes)
    eyes.boxes = [Box(950, 100, 1020, 700, 0.9)]
    _go_watching(c)
    assert last_why() == "not_a_person"


def test_a_face_at_the_edge_of_the_picture_is_somebody_else(eyes):
    c = _someone_standing(eyes)
    _go_watching(c, found=[Seen(90, x=930)])
    assert last_why() == "not_a_person"


def test_past_the_head_s_reach_the_middle_of_the_picture_moves_with_it(eyes, monkeypatch):
    # 26 deg with the head at its stop: 3.8 deg off the middle is where they are.
    monkeypatch.setattr(settings, "head_follow", True)
    c = _someone_standing(eyes, (3.15, 1.54))
    # +3.5 deg to the frame's edge: a person 3.5 m off is ~390 px wide, cut at the edge (the box's
    # width must fit the lidar's range since 6 Nov; this test's first box was 100 px = ~14 m)
    eyes.boxes = [Box(700, 100, 1024, 700, 0.9)]
    _go_watching(c)
    assert c["confirmed"] is True


def test_an_old_count_of_boxes_is_not_finished_by_one_new_box(eyes):
    eyes.boxes = [STANDING_BOX]
    fa._note_body(FRAME, [], eyes.t)
    fa._note_body(FRAME, [], eyes.t + 1)
    fa._note_body(FRAME, [], eyes.t + 600)
    assert fa._eyes["body"] is None


def test_a_board_that_did_not_answer_is_no_verdict_on_the_place(eyes, monkeypatch):
    _stood_all_along(eyes)
    c = _glance(eyes)
    eyes.read_fails = 1
    _go_watching(c)
    assert last_why() == "no_scan"
    assert not fa._not_person_here(c["world"], time.monotonic())


# ---- the detector's own code, on a fake session ----------------------------------------------------

class FakeSession:
    """Answers with what the test put in the model's own output grid."""

    def __init__(self, rows):
        self.rows = rows
        self.fed = None

    def get_inputs(self):
        return [types.SimpleNamespace(name="images")]

    def run(self, _names, feed):
        self.fed = feed["images"]
        return [np.array([self.rows], dtype=np.float32)]


def _output(cells):
    """cells: (stride-8 column, row, width px, height px, objectness, person score, chair score)."""
    n = sum((person_vision.SIZE // s) ** 2 for s in person_vision.STRIDES)
    rows = np.zeros((n, 85), dtype=np.float32)
    side = person_vision.SIZE // 8
    for col, row, w, h, obj, person, chair in cells:
        at = row * side + col
        rows[at, 0:2] = 0.0
        rows[at, 2:4] = (math.log(w / 8.0), math.log(h / 8.0))
        rows[at, 4] = obj
        rows[at, 5 + person_vision.PERSON] = person
        rows[at, 5 + 56] = chair
    return rows


def _wired(monkeypatch, cells):
    grids, strides = [], []
    for stride in person_vision.STRIDES:
        n = person_vision.SIZE // stride
        xs, ys = np.meshgrid(np.arange(n), np.arange(n))
        grids.append(np.stack((xs, ys), 2).reshape(-1, 2))
        strides.append(np.full((n * n, 1), stride))
    session = FakeSession(_output(cells))
    person_vision._state.update(session=session, grid=np.concatenate(grids).astype(np.float32),
                                strides=np.concatenate(strides).astype(np.float32))
    monkeypatch.setattr(settings, "person_vision", True)
    return session


def test_a_box_comes_back_in_the_frame_s_own_pixels(monkeypatch):
    # 1024 x 768 into 416: ratio 0.40625. A cell at (26, 26) of stride 8 is (208, 208) in the input.
    session = _wired(monkeypatch, [(26, 26, 100, 200, 0.9, 0.9, 0.0)])
    boxes = person_vision.people(np.zeros((768, 1024, 3), np.uint8))
    assert len(boxes) == 1
    box = boxes[0]
    assert box.cx == pytest.approx(208 / 0.40625, abs=2)
    assert box.width == pytest.approx(100 / 0.40625, abs=2) and box.height == pytest.approx(200 / 0.40625, abs=2)
    assert box.score == pytest.approx(0.81, abs=0.01)
    assert session.fed.shape == (1, 3, 416, 416) and session.fed.dtype == np.float32
    assert session.fed[0, 0, 415, 0] == person_vision.FILL, "letterboxed, under the picture"


def test_a_chair_is_not_a_person_and_a_weak_box_is_nothing(monkeypatch):
    _wired(monkeypatch, [(10, 10, 100, 100, 0.9, 0.0, 0.9), (30, 30, 100, 100, 0.9, 0.4, 0.0)])
    assert person_vision.people(np.zeros((768, 1024, 3), np.uint8)) == []


def test_two_boxes_on_one_person_are_one_and_the_best_comes_first(monkeypatch):
    _wired(monkeypatch, [(26, 26, 100, 200, 0.9, 0.9, 0.0), (27, 26, 100, 200, 0.9, 0.7, 0.0),
                         (5, 5, 40, 80, 0.9, 0.95, 0.0)])
    boxes = person_vision.people(np.zeros((768, 1024, 3), np.uint8))
    assert len(boxes) == 2 and boxes[0].score > boxes[1].score


def test_a_box_past_the_picture_s_edge_is_cut_at_it(monkeypatch):
    _wired(monkeypatch, [(1, 1, 300, 300, 0.9, 0.9, 0.0)])
    box = person_vision.people(np.zeros((768, 1024, 3), np.uint8))[0]
    assert box.x1 == 0 and box.y1 == 0


def test_the_switch_off_asks_nothing_of_the_disk(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "person_vision", False)
    monkeypatch.setattr(person_vision, "MODEL", tmp_path / "nope.onnx")
    assert person_vision.people(np.zeros((8, 8, 3), np.uint8)) == []
    assert person_vision.available() is False and person_vision._state["failed"] is False


def test_no_file_is_one_line_and_no_person(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(settings, "person_vision", True)
    monkeypatch.setattr(person_vision, "MODEL", tmp_path / "nope.onnx")
    assert person_vision.people(np.zeros((8, 8, 3), np.uint8)) == []
    assert person_vision.available() is False
    assert person_vision._state["failed"] is True
    assert sum("person vision off" in r.getMessage() for r in caplog.records) == 1


def test_another_file_under_that_name_is_refused(monkeypatch, tmp_path, caplog):
    other = tmp_path / "yolox_nano.onnx"
    other.write_bytes(b"not the model")
    monkeypatch.setattr(settings, "person_vision", True)
    monkeypatch.setattr(person_vision, "MODEL", other)
    assert person_vision.available() is False
    assert any("not the pinned model" in r.getMessage() for r in caplog.records)


def test_a_frame_that_fails_is_a_frame_lost(monkeypatch):
    session = _wired(monkeypatch, [])
    monkeypatch.setattr(session, "run", lambda *_: (_ for _ in ()).throw(RuntimeError("boom")))
    assert person_vision.people(np.zeros((768, 1024, 3), np.uint8)) == []


def test_the_model_reaches_the_robot_pinned():
    from scripts import robot_server

    assert robot_server.FETCH_ON_ROBOT["data/models/person/yolox_nano.onnx"] == person_vision.URL


# ---- the real model, on real pictures --------------------------------------------------------------

FRAMES = ROOT / ".tmp" / "walk-frames"
A_PERSON = ["20261104-105403-app/0040.jpg", "20261104-105403-app/0042.jpg"]
A_CHAIR = ["20261104-193715-moving/1525.jpg", "20261104-202043-moving/1009.jpg"]


@pytest.fixture
def real_model(monkeypatch):
    if not person_vision.MODEL.is_file():
        pytest.skip("no data/models/person/yolox_nano.onnx: the real detector is NOT being checked "
                    "(python scripts/fetch_person_model.py)")
    assert hashlib.sha256(person_vision.MODEL.read_bytes()).hexdigest() == person_vision.SHA256
    monkeypatch.setattr(settings, "person_vision", True)
    person_vision.reset()
    yield
    person_vision.reset()


def test_the_real_model_sees_nobody_in_a_blank_picture(real_model):
    assert person_vision.people(np.full((768, 1024, 3), 128, np.uint8)) == []


@pytest.mark.parametrize("name", A_PERSON)
def test_the_real_model_sees_a_person_in_a_real_picture(real_model, name):
    import cv2

    frame = cv2.imread(str(FRAMES / name))
    if frame is None:
        pytest.skip(f"{name} is not on this machine: a real picture of a person is NOT being checked")
    boxes = person_vision.people(frame)
    assert boxes and boxes[0].score >= settings.person_vision_score


@pytest.mark.parametrize("name", A_CHAIR)
def test_the_real_model_does_not_call_a_real_chair_a_person(real_model, name):
    import cv2

    frame = cv2.imread(str(FRAMES / name))
    if frame is None:
        pytest.skip(f"{name} is not on this machine: a real picture of a chair is NOT being checked")
    assert person_vision.people(frame) == []


def test_a_mute_during_the_look_ends_it(eyes, monkeypatch):
    """The look turns the head on the line it rang for: muted while it looks, it stops looking and
    walks nowhere (review 6 Nov: the look loop never asked the hold)."""
    from app import wake

    c = _someone_standing(eyes)
    held = {"on": False}
    monkeypatch.setattr(wake, "mic_held", lambda: held["on"])
    real = head_follow.look_hint

    def looked(pct, pan=None):
        real(pct, pan)
        held["on"] = True

    monkeypatch.setattr(head_follow, "look_hint", looked)
    _go_watching(c)
    assert last_why() == "mic_held" and eyes.steps is None


# ---- chunk 2 of the 6 Nov review: what the picture says must fit what the lidar found -------------

def test_a_person_far_behind_a_chair_does_not_make_the_chair_a_person(eyes):
    """A chair carried in 2.2 m off; somebody 15 m behind it, in the middle of the picture: a 40 px
    box. Before 6 Nov any box near the middle answered the look."""
    c = _someone_standing(eyes, (2.2, 0.0))
    eyes.boxes = [Box(492, 300, 532, 400, 0.9)]
    _go_watching(c)
    assert not c.get("confirmed") and eyes.steps is None
    assert last_why() == "not_a_person"


def test_a_small_face_in_the_middle_is_somebody_else_far_off(eyes):
    """An 89 px face reads ~5.5 m through the 19 deg lens; the thing is at 2.2 m."""
    c = _someone_standing(eyes, (2.2, 0.0))
    eyes.boxes = []
    _go_watching(c, found=[Seen(89, x=468)])
    assert not c.get("confirmed") and eyes.steps is None


def test_their_own_face_at_their_range_still_answers_the_look(eyes):
    c = _someone_standing(eyes, (2.2, 0.0))
    eyes.boxes = []
    _go_watching(c, found=[Seen(220, x=402)])          # 0.16 m at 2.2 m: ~222 px
    assert c["confirmed"] is True


def test_with_no_frame_looked_at_nothing_is_concluded(eyes):
    """The door camera's frames held (a face scan, its loop busy): she did not see that place, so it
    is not set aside as "not a person" for five minutes (review 6 Nov)."""
    c = _someone_standing(eyes)
    asyncio.run(fa._approach(c))                       # no frames at all
    assert last_why() == "no_frames" and eyes.steps is None
    assert not fa._not_person_here((3.5, 0.0), time.monotonic())


def test_frames_while_the_head_is_still_on_its_way_do_not_answer_the_look(live, monkeypatch):
    """Review 6 Nov: the look counted frames 1.3 s after it was sent; a move from a head nobody knows
    takes the app 3 s. A bystander on the sweep path, in the middle of the picture on the way, confirmed
    the chair. Now an unanswered look counts as the slowest move (head_follow.UNACKED_MOVE_S)."""
    monkeypatch.setattr(head_follow, "UNACKED_MOVE_S", 0.5)
    c = _live_standing(live, (3.45, 0.61))             # a chair at 10 deg
    sent = {}

    def faces():
        if live.face.ws.looks() and "at" not in sent:
            sent["at"] = time.monotonic()
        if "at" in sent and time.monotonic() - sent["at"] < 0.4:
            return [Seen(140, x=442)]                  # 3.5 m away, dead centre, passing
        return []

    _run_live(live, c, faces)
    assert live.face.ws.looks(), "the head was told"
    assert last_why() == "not_a_person" and live.steps is None


def test_a_look_the_app_refused_answers_nothing(live):
    """Review 6 Nov: the hint counted as taken when sent; the app refused it ("busy"), the head stayed,
    and the frames of where it stayed answered the look. Now: nothing concluded."""
    c = _live_standing(live, (3.45, 0.61))
    answered = {"done": False}

    def faces():
        if live.face.ws.looks() and not answered["done"] and live.follower.pending is not None:
            answered["done"] = True
            live.follower.ack({"seq": live.follower.pending["seq"], "accepted": False, "reason": "busy"})
        return [Seen(140, x=442)]                      # whoever stands in front, where the head stayed

    _run_live(live, c, faces)
    assert answered["done"] and last_why() == "head_busy" and live.steps is None
    assert not fa._not_person_here((3.45, 0.61), time.monotonic()), "she never looked there"


def test_the_box_road_does_not_walk_to_what_the_map_has(eyes):
    """Review 6 Nov: a box's cone found a table leg or a wall the map has. The scan road refused those;
    the box and far-face roads did not."""
    eyes.grid = Grid(walls=[(3.4, -0.3, 3.6, 0.3)])
    _stood_all_along(eyes)
    eyes.boxes = [STANDING_BOX]
    _go_watching(_body())
    assert last_why() == "on_the_map" and eyes.steps is None


def test_no_frame_counts_for_any_road_before_the_head_has_settled(eyes, monkeypatch):
    """`observe`'s own gate (the box road, far faces, guests): a picture from a head still on its way
    is not of where it points (review 6 Nov)."""
    monkeypatch.setattr(settings, "head_follow", True)
    monkeypatch.setattr(head_follow, "moved_at", lambda: None)
    eyes.boxes = [STANDING_BOX]
    head_follow._HEAD["settle_at"] = time.monotonic() + 5.0
    fa.observe(WATCHER, FRAME, [], time.monotonic())
    assert fa._eyes["body_run"] == [] and eyes.asked == 0
    head_follow._HEAD["settle_at"] = time.monotonic() - 1.0
    fa._eyes["at"] = -math.inf
    fa.observe(WATCHER, FRAME, [], time.monotonic())
    assert eyes.asked == 1, "settled: the frame is looked at"


def test_a_head_taken_elsewhere_mid_look_answers_nothing(live):
    """The look counted, then the face loop took the head away: the frames from there are not of that
    place, and the place is not set aside (review 6 Nov)."""
    c = _live_standing(live, (3.45, 0.61))
    moved = {"done": False}

    def faces():
        if fa._eyes["counting"] and not moved["done"]:
            moved["done"] = True
            head_follow._set_head(0, 0)              # what a face being followed does to the head
        return []

    _run_live(live, c, faces)
    assert moved["done"] and last_why() == "head_busy" and live.steps is None
    assert not fa._not_person_here((3.45, 0.61), time.monotonic())


# ---- the second review of chunk 2 (6 Nov) -------------------------------------------------------

def test_the_box_road_does_not_walk_to_a_chair_for_somebody_far_behind_it(eyes):
    """The box road has no look first: a 40 px box (somebody ~35 m off) in the cone of a thing 3.5 m
    away walked her to the thing."""
    _stood_all_along(eyes)
    eyes.boxes = [STANDING_BOX]
    _go_watching(_body(px=40))
    assert last_why() == "box_too_far" and eyes.steps is None and eyes.said == []


def test_the_box_road_still_walks_to_a_box_that_fits(eyes):
    _stood_all_along(eyes)
    eyes.boxes = [STANDING_BOX]
    _go_watching(_body(px=400))                          # ~3.4 m from the box's width; the lidar has 3.5
    assert last_why() != "box_too_far" and eyes.steps is not None


def test_with_this_call_s_follow_off_the_lens_still_looks(live):
    """A failed sweep turns this call's follow off: no tick takes a hint, and every look ended
    "head_busy". Now: one look, the lens where the head is, nothing sent to the head."""
    from app import session as session_module

    live.follower.off = "sweep_failed"
    session_module._active._head_follow = live.follower
    c = _live_standing(live, (3.5, 0.0))                 # dead ahead, inside the lens
    live.boxes = [STANDING_BOX]
    _run_live(live, c)
    assert c["confirmed"] is True and live.face.ws.looks() == []



# ---- the all-round review of 6 Nov ---------------------------------------------------------------

def test_at_the_head_s_reach_a_person_at_the_picture_s_other_edge_does_not_answer(eyes, monkeypatch):
    """The window was widened round the picture's middle: at 26 deg (head at its 22 deg stop) it took
    the whole frame, and a person at the far opposite edge confirmed the target."""
    monkeypatch.setattr(settings, "head_follow", True)
    c = _someone_standing(eyes, (3.15, 1.54))
    eyes.boxes = [Box(0, 100, 300, 700, 0.9)]            # the other side of the picture
    _go_watching(c)
    assert not c.get("confirmed") and eyes.steps is None


def test_with_the_head_s_place_unknown_a_look_concludes_nothing(eyes, monkeypatch):
    monkeypatch.setattr(head_follow, "settled_at", lambda: math.inf)
    c = _someone_standing(eyes)
    eyes.boxes = [STANDING_BOX]
    _go_watching(c)
    assert last_why() == "head_unknown" and eyes.steps is None
    assert not fa._not_person_here((3.5, 0.0), time.monotonic())


def test_no_line_is_rung_for_a_look_that_would_be_refused(eyes, monkeypatch):
    """The line comes before the plan; the head's look gap after it. A glance road inside the gap spent a
    dial on nothing (review 6 Nov)."""
    monkeypatch.setattr(session_module, "_active", None)
    c = _someone_standing(eyes)
    fa.STATE["head_at"] = time.monotonic()
    _go_watching(c)
    assert last_why() == "looked_recently" and eyes.rung is None


def test_right_after_the_look_before_the_walk_the_stop_tilts_once(eyes):
    """The look before the walk, then a tilt at the stop and a lower one: 6-7 head moves in 20 s against
    "no look more often than every 20 s" (review 6 Nov). The stop keeps the tilt the face needs."""
    c = _someone_standing(eyes)
    eyes.boxes = [STANDING_BOX]
    _go_watching(c)
    assert c["confirmed"] is True and len(eyes.tilts) == 2, eyes.tilts   # the look, and the stop's one


def test_the_dry_run_row_has_the_picture_s_bearing_and_comes_once_a_minute(eyes, monkeypatch):
    rows = []
    monkeypatch.setattr(fa.turnlog, "record", lambda e, **f: rows.append((e, f)))
    monkeypatch.setattr(settings, "face_approach_dry_run", True)
    _stood_all_along(eyes)
    eyes.boxes = [STANDING_BOX]
    _go_watching(_body())
    walk = [f for e, f in rows if e == "face_approach" and f.get("phase") == "would_walk"]
    assert walk and walk[0]["camera_deg"] == pytest.approx(0.0, abs=1.0), walk
    assert fa.DRY_RUN_REPEAT_S >= 60.0
