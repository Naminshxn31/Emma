"""See somebody, walk up, look, greet (FACE_APPROACH_ANYONE, owner 5 Nov 2026: "เวลาเห็นคนก็ให้
หุ่นเดินไปทัก ... ถ้ามีในข้อมูลเราก็ทัก"; docs/emma-face-approach.md section 9).

No robot: the fake board and little world of tests/test_face_approach.py, the real
lidar_people tracker over its scans, the real event_walk over a fake board_walk, and for the
naming the real facewatch.Watcher. Nothing here turns a wheel. Scan timing (FACE_DET_SIZE,
FACE_FAST_CONFIRM) is at the end of the file.
"""
from __future__ import annotations

import asyncio
import json
import math
import time
import types

import numpy as np
import pytest

from app import (event_host, event_walk, face_approach as fa, faces, facewatch, greeter, head_follow,
                 lidar_people as lp, robot_arm, robot_chassis, robot_face_link, session as session_module,
                 turnlog, wake)
from app.config import settings
from tests.test_face_approach import SQUARE, FakeProc, world  # noqa: F401 -- `world` is the fixture

FRAME = np.zeros((768, 1024, 3), np.uint8)


class Grid:
    """The board's map: free everywhere, a wall where the test puts one."""

    def __init__(self, walls=()):
        self.res, self.ox, self.oy = 0.05, -20.0, -20.0
        self._occ = np.zeros((800, 800), bool)
        for x0, y0, x1, y1 in walls:
            self._occ[int((y0 + 20) / 0.05):int((y1 + 20) / 0.05) + 1, int((x0 + 20) / 0.05):int((x1 + 20) / 0.05) + 1] = True

    def occ(self):
        return self._occ


@pytest.fixture
def road(world, monkeypatch):  # noqa: F811
    """Armed, standing, somebody's bench: the anybody road switched on."""
    monkeypatch.setattr(settings, "face_approach_anyone", True)
    monkeypatch.setattr(settings, "lidar_people", True)
    monkeypatch.setattr(settings, "lidar_people_every_s", 0.5)
    monkeypatch.setattr(settings, "face_approach_check_s", 0.25)
    monkeypatch.setattr(settings, "robot_greeting_gesture_enabled", True)
    monkeypatch.setattr(settings, "head_follow", False)
    monkeypatch.setattr(fa, "FACE_MORE_S", 0.1)
    monkeypatch.setattr(fa, "SECOND_LOOK_S", 0.1)
    monkeypatch.setattr(fa, "CHECK_POLL_S", 0.01)
    world.people = []
    world.steps = None

    async def spawn(route):
        doc = json.loads(route.read_text(encoding="utf-8"))
        steps = doc["paths"][event_walk.PATH_NAME]["steps"]
        world.steps = steps
        to = next((s for s in steps if "to" in s), None)
        if steps[-1].get("face") is not None:
            world.pose["yaw"] = math.radians(steps[-1]["face"])
        world.walk_to = to
        return FakeProc(world, to["to"] if to else (world.pose["x"], world.pose["y"]))

    monkeypatch.setattr(event_walk, "_spawn", spawn)

    async def grid():
        return world.grid

    world.grid = Grid()
    monkeypatch.setattr(fa, "_map_grid", grid)

    async def summon(reason="server"):
        world.rung = reason
        return 1

    world.rung = None
    monkeypatch.setattr(wake, "summon", summon)
    world.tilts = []
    world.pans = []
    def look_hint(pct, pan=None):
        world.tilts.append(pct)
        world.pans.append(pan)
        # A head that takes the look at once and stays (the real loop is the `live` fixture's):
        # where it went, for head_follow.at_look_pose.
        at = (head_follow._HEAD["pan_pct"] if pan is None else int(pan), int(pct))
        head_follow._LOOK["pose"] = at
        head_follow._HEAD["pan_pct"], head_follow._HEAD["tilt_pct"] = at

    monkeypatch.setattr(head_follow, "look_hint", look_hint)
    world.t = time.monotonic()
    lp.reset()
    yield world
    lp.reset()


def _poll(w, *people, n=1):
    """One or more lidar polls with `people` standing there."""
    w.people = list(people)
    for _ in range(n):
        w.t += 0.5
        asyncio.run(lp.poll_once(now=w.t))


def _room_settled(w):
    _poll(w, n=16)                          # 8 s of an empty room: the background means something


def _walks_in(w, to=(3.5, 0.0), frm=(5.0, 2.0), steps=8):
    for i in range(1, steps + 1):
        share = i / steps
        _poll(w, (frm[0] + (to[0] - frm[0]) * share, frm[1] + (to[1] - frm[1]) * share))


def _candidate(w):
    return fa._person_candidate(w.t)


def _someone_standing(w, to=(3.5, 0.0)):
    _room_settled(w)
    _walks_in(w, to)
    _poll(w, to, n=6)
    c = _candidate(w)
    assert c is not None, "a person who walked in and stood"
    return c


def _go(c):
    asyncio.run(fa._approach(c))


def _rows(monkeypatch):
    rows = []
    monkeypatch.setattr(turnlog, "record", lambda event, **f: rows.append((event, f)))
    return rows


def _phase_row(rows, phase):
    return next((f for e, f in rows if e == "face_approach" and f.get("phase") == phase), None)


def last_why():
    return (fa.STATE["last"] or {}).get("why")


def last_what():
    return (fa.STATE["last"] or {}).get("what")


# ---- who: the lidar's word --------------------------------------------------------------------

def test_somebody_who_walked_in_and_stood_is_a_candidate(road):
    c = _someone_standing(road)
    assert c["kind"] == "person" and c["range_m"] == pytest.approx(3.5, abs=0.15)
    assert c["bearing_deg"] == pytest.approx(0.0, abs=3.0)


def test_somebody_walking_past_never_is(road):
    _room_settled(road)
    for i in range(30):                     # 0.5 m a poll, across the whole of her reach
        _poll(road, (3.0, -7.0 + i * 0.5))
        assert _candidate(road) is None


def test_somebody_who_has_not_been_watched_that_long_is_not_standing_yet():
    # 0.5 m in three polls fits inside STILL_M of its own middle: a slow walker just seen.
    fo = lp.Follower()
    for i in range(3):
        fo.update([lp.Person(bearing=0.0, distance=3.0 + 0.25 * i, width=0.2, points=8, t=float(i) * 0.5)], i * 0.5)
    assert fo.tracks[0]["walked"] >= lp.WALKED_M
    assert fo.standing(1.0, 2.0) == []


def test_a_person_who_only_stopped_for_a_moment_is_not_yet(road):
    _room_settled(road)
    _walks_in(road)
    _poll(road, (3.5, 0.0), n=2)            # 1 s of the 2 s
    assert _candidate(road) is None


def test_furniture_is_nobody(road):
    road.things = [(2.5, 0.3, 0.03), (2.5, -0.3, 0.03), (3.0, 1.0, 0.25)]
    _poll(road, n=40)
    assert _candidate(road) is None


def test_something_that_appears_and_never_moves_is_nobody(road):
    # A chair put down out of the lidar's sight, a flicker on glass: the replays' two false
    # standers moved 0.00 and 0.02 m. Standing is not enough: it must have walked.
    _room_settled(road)
    road.things = [(3.0, 0.5, 0.2)]
    _poll(road, n=20)
    assert lp.people(road.t), "the tracker does see it"
    assert _candidate(road) is None


def test_nothing_new_means_anything_right_after_she_stopped(road):
    _poll(road, n=2)                        # 1 s of background
    _walks_in(road, steps=4)
    _poll(road, (3.5, 0.0), n=4)            # walked in and stood 2 s, in a background of 4.5 s
    assert lp.STATE["tracker"].stood_s(lp.STATE["tracker"].at) < lp.MIN_STAND_S
    assert _candidate(road) is None
    _poll(road, (3.5, 0.0), n=4)
    assert _candidate(road) is not None, "and once it means something, they count"


def test_a_slow_creep_of_the_base_throws_the_background_away(road):
    # 4 Nov replays: 4.2 "people" a scan off a base turning under MOVE_RESET per poll.
    tr = lp.Tracker()
    pts = road.scan()["points"]
    resets = tr.resets
    for i in range(10):
        tr.update(pts, {"x": 0.015 * i, "y": 0.0, "yaw": 0.0}, float(i))
    assert tr.resets > resets


def test_she_forgets_them_when_she_moves(road):
    _someone_standing(road)
    road.pose["x"] += 0.3                   # the frame the tracks were kept in is gone
    _poll(road, (3.5, 0.0), n=1)
    assert lp.STATE["follow"].tracks == [] or all(k["walked"] < lp.WALKED_M for k in lp.STATE["follow"].tracks)
    assert _candidate(road) is None


def test_it_reads_the_whole_scan_and_further_only_while_armed(road, monkeypatch):
    asked = []
    real = robot_chassis.laserscan

    async def laserscan(max_points=0):
        asked.append(max_points)
        return await real(max_points)

    monkeypatch.setattr(robot_chassis, "laserscan", laserscan)
    _poll(road)
    assert asked[-1] == 0 and lp.want_far() is True
    fa.STATE["staff_on"] = False
    assert lp.want_far() is False
    _poll(road, (4.5, 0.0))
    assert asked[-1] == lp.SCAN_POINTS
    assert not lp.people(road.t), "4.5 m is past the head's own 2.5 m"


def test_nobody_out_of_reach_is_a_candidate(road, monkeypatch):
    monkeypatch.setattr(settings, "face_approach_max_m", 1.0)
    _room_settled(road)
    _walks_in(road, (4.5, 0.0), (5.5, 2.0))
    _poll(road, (4.5, 0.0), n=6)
    assert _candidate(road) is None


def test_a_track_she_could_not_go_to_is_not_asked_about_every_tick(road):
    c = _someone_standing(road)
    road.things = [(1.5, 0.4, 0.1)]          # the way is blocked (and they are not hidden)
    _go(c)
    assert last_why() == "path_blocked"
    assert _candidate(road) is None
    road.t += fa.TRACK_RETRY_S + 1
    _poll(road, (3.5, 0.0), n=1)


# ---- the walk --------------------------------------------------------------------------------

def test_she_stops_further_off_than_for_a_named_guest_and_never_under_the_floor(road, monkeypatch):
    c = _someone_standing(road)
    _go(c)
    gap = 3.5 - road.walk_to["to"][0] - fa.HALF_LENGTH_M
    assert gap == pytest.approx(settings.face_approach_person_stop_m, abs=0.1)
    assert road.walk_to["speed"] <= fa.MAX_SPEED
    lp.reset()
    fa.reset()
    fa.STATE["staff_on"] = True
    event_walk.heartbeat()
    monkeypatch.setattr(settings, "face_approach_person_stop_m", 0.2)
    road.pose.update(x=0.0, y=0.0, yaw=0.0)
    c = _someone_standing(road)
    _go(c)
    assert 3.5 - road.walk_to["to"][0] - fa.HALF_LENGTH_M >= fa.STOP_FLOOR_M - 0.1


def test_no_setting_walks_further_or_faster(road, monkeypatch):
    monkeypatch.setattr(settings, "face_approach_max_m", 99.0)
    monkeypatch.setattr(settings, "face_approach_speed", 5.0)
    from app import robot_lanes as rl

    road.people = [(6.5, 0.0)]
    pts = rl.scan_points({"laser_points": road.scan()["points"]})
    far = fa.plan(pts, road.pose, 0.0, 6.5, SQUARE, stop_m=1.0, lidar_range=True)
    assert far["why"] == "too_far"
    road.people = [(4.0, 0.0)]
    pts = rl.scan_points({"laser_points": road.scan()["points"]})
    near = fa.plan(pts, road.pose, 0.0, 4.0, SQUARE, stop_m=1.0, lidar_range=True)
    assert near["speed"] == fa.MAX_SPEED


def test_somebody_round_to_the_side_gets_a_spot_turn_first(road):
    c = _someone_standing(road, to=(1.0, 3.0))
    _go(c)
    assert [s.get("do") or "to" for s in road.steps] == ["turn", "to"]
    bearing = math.degrees(math.atan2(3.0, 1.0))
    assert road.steps[0]["face"] == pytest.approx(bearing, abs=3.0)
    assert road.steps[1]["face"] == pytest.approx(bearing, abs=3.0)


def test_somebody_almost_ahead_needs_no_turn(road):
    c = _someone_standing(road, to=(3.5, 0.3))
    _go(c)
    assert [s.get("do") or "to" for s in road.steps] == ["to"]


def test_somebody_within_reach_at_her_side_gets_only_the_turn(road):
    c = _someone_standing(road, to=(0.0, 1.5))
    _go(c)
    assert [s.get("do") for s in road.steps] == ["turn"]
    assert road.walk_to is None
    assert (road.pose["x"], road.pose["y"]) == (0.0, 0.0)


def test_somebody_within_reach_ahead_is_looked_at_without_a_step(road):
    c = _someone_standing(road, to=(1.5, 0.0))
    _go(c)
    assert road.steps is None
    assert last_what() == "greeted"


def _mid_walk(road, change):
    road.arrive = False

    async def scenario():
        c = _someone_standing_async(road)
        task = asyncio.create_task(fa._approach(c))
        for _ in range(300):
            await asyncio.sleep(0.01)
            if event_walk.walking():
                break
        assert event_walk.walking()
        result = change()
        if asyncio.iscoroutine(result):
            await result
        await asyncio.wait_for(task, 5.0)

    asyncio.run(scenario())


def _someone_standing_async(road):
    tr = lp.STATE["tracker"]
    fo = lp.STATE["follow"]

    def poll(*people, n=1):
        road.people = list(people)
        for _ in range(n):
            road.t += 0.5
            scan = road.scan()
            found = tr.update(scan["points"], scan["pose"], road.t, range_m=(lp.RANGE_M[0], lp.FAR_RANGE_M))
            fo.update(found, road.t)

    poll(n=16)
    for i in range(1, 9):
        poll((5.0 - 1.5 * i / 8, 2.0 - 2.0 * i / 8))
    poll((3.5, 0.0), n=6)
    c = fa._person_candidate(road.t)
    assert c is not None
    return c


def test_a_person_who_walks_away_ends_it_and_nothing_is_said(road):
    _mid_walk(road, lambda: setattr(road, "people", [(3.5, 1.5)]))
    assert last_why() in ("moved", "lost_target")
    assert road.said == [] and road.shook == []
    assert event_walk.walking() is False


def test_an_attempt_counts_whatever_came_of_it_never_chase(road):
    _mid_walk(road, lambda: setattr(road, "people", [(3.5, 1.5)]))
    assert fa._greeted_here((3.5, 0.0), time.monotonic()) is True
    assert fa.STATE["last_any"] > 0


def test_a_person_who_comes_to_meet_her_stops_her_short_of_the_floor(road, monkeypatch):
    # Under MOVED_M from where they stood, and the plan's goal would put her 0.5 m from them.
    monkeypatch.setattr(settings, "face_approach_person_stop_m", 1.0)

    def closer():
        road.people = [(3.0, 0.0)]

    _mid_walk(road, closer)
    assert (fa.STATE["last"] or {}).get("what") in ("met", "greeted", "aborted")
    assert event_walk.current().stop_requested == "approach_met"
    gap = 3.0 - road.pose["x"] - fa.HALF_LENGTH_M
    assert gap >= fa.STOP_FLOOR_M


def test_a_person_who_stayed_put_is_not_taken_for_one_who_came(road, monkeypatch):
    monkeypatch.setattr(settings, "face_approach_person_stop_m", 1.0)
    c = _someone_standing(road)
    _go(c)
    assert event_walk.current().stop_requested is None
    assert last_what() == "greeted"


@pytest.mark.parametrize("reason", ["voice", "staff", "desk", "control_lost", "face_gone"])
def test_a_human_stop_turns_it_off_and_it_does_not_restart_itself(road, reason):
    _mid_walk(road, lambda: event_walk.stop(reason))
    assert event_walk.walking() is False
    assert road.said == [] and road.shook == []
    assert fa.STATE["staff_on"] is False
    event_walk.heartbeat()
    assert fa.armed() is False
    assert fa._person_candidate(road.t) is None or fa.armed() is False


def test_the_page_going_quiet_turns_it_off(road):
    _someone_standing(road)
    event_walk.STATE["heartbeat"] = time.monotonic() - 60
    asyncio.run(fa.tick())
    assert fa.STATE["staff_on"] is False
    assert fa.STATE["task"] is None


def test_a_walk_up_to_somebody_is_never_resumed(road):
    _mid_walk(road, lambda: event_walk.stop("staff"))
    assert event_walk.current().can_resume() is False
    assert event_walk.current().spot == event_walk.NO_RESUME_SPOT


def test_voice_cannot_start_it():
    source = open(fa.__file__, encoding="utf-8").read() + open(lp.__file__, encoding="utf-8").read()
    assert "@tool" not in source


# ---- every reason not to leave ----------------------------------------------------------------

def _set(obj, key, value):
    return lambda m, w: m.setattr(obj, key, value)


def _world(key, value):
    return lambda m, w: setattr(w, key, value)


def _walled(m, w):
    w.grid = Grid([(2.0, -2.0, 2.1, 2.0)])    # glass across the way, on the map


def _no_map(m, w):
    w.grid = None


def _hazard(m, w):
    m.setattr(event_host, "hazard_on_line", lambda *a: {"id": "bump"})


def _keep_out(m, w):
    m.setattr(event_host, "keep_out_on_line", lambda *a: True)


REFUSALS = [
    ("off", _set(settings, "face_approach", False)),
    ("walk_off", _set(settings, "event_walk_enabled", False)),
    ("not_armed", lambda m, w: fa.STATE.update(staff_on=False)),
    ("ceremony", _set(event_host, "ceremony_now", lambda: True)),
    ("no_area", lambda m, w: fa._save_area([])),
    ("gap", lambda m, w: fa.STATE.update(last_any=time.monotonic())),
    ("scene_recent", lambda m, w: event_host.STATE.update(since=time.time())),
    ("walking", _set(event_walk, "walking", lambda: True)),
    ("nav_unknown", _set(robot_face_link, "nav_activity", lambda session=None: "unknown")),
    ("stop_latched", _set(greeter, "_app_latched", lambda link: True)),
    ("desk_stopped", _set(robot_chassis, "desk_stopped_within", lambda s: True)),
    ("conversation", _set(greeter, "_conversation_state", lambda s, now=None: "exchange")),
    ("no_chassis", _set(robot_chassis, "configured", lambda: False)),
    ("docked", _world("docking", "on_dock")),
    ("low_quality", _world("quality", 40)),
    ("no_map", _no_map),
    ("behind_wall", _walled),
    ("floor_hazard", _hazard),
    ("keep_out", _keep_out),
    ("path_blocked", _world("things", [(1.5, 0.2)])),
    ("outside_area", lambda m, w: fa._save_area([[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0]])),
]


@pytest.mark.parametrize("why,setup", REFUSALS, ids=[r[0] for r in REFUSALS])
def test_it_does_not_leave_and_says_nothing(road, monkeypatch, why, setup):
    c = _someone_standing(road)
    setup(monkeypatch, road)
    _go(c)
    assert last_why() == why
    assert road.steps is None and road.walk_to is None
    assert road.said == [] and road.shook == []


def test_a_person_seen_through_the_glass_is_not_walked_to_though_the_way_looks_clear(road):
    """The lidar sees through glass: 11-14% of the lobby's returns come from beyond a map wall."""
    c = _someone_standing(road)
    road.grid = Grid([(3.0, -3.0, 3.05, 3.0)])
    _go(c)
    assert last_why() == "behind_wall"


def test_a_person_moving_during_the_checks_is_not_left_for(road):
    c = _someone_standing(road)
    scans = [-1]
    real = road.scan

    def drifting():
        scans[0] += 1
        road.people = [(3.5, 0.15 * scans[0])]
        return real()

    road.scan = drifting
    _go(c)
    assert last_why() == "moving_target"
    assert road.steps is None


def test_somebody_she_just_dealt_with_is_not_walked_to_again(road):
    c = _someone_standing(road)
    _go(c)
    assert last_what() == "greeted"
    road.pose.update(x=0.0, y=0.0, yaw=0.0)
    fa.STATE["last_any"] = -math.inf
    lp.reset()
    road.steps = None
    _room_settled(road)
    _walks_in(road, (3.8, 0.4))             # the same person, a little further along
    _poll(road, (3.8, 0.4), n=6)
    assert _candidate(road) is None


def test_after_the_gap_somebody_else_is_walked_to(road):
    c = _someone_standing(road)
    _go(c)
    road.pose.update(x=0.0, y=0.0, yaw=0.0)
    fa.STATE["last_any"] = -math.inf
    lp.reset()
    _room_settled(road)
    _walks_in(road, (2.5, 3.0), (4.0, 4.0))
    _poll(road, (2.5, 3.0), n=6)
    assert _candidate(road) is not None


def test_a_face_the_door_greets_during_the_checks_means_no_walk(road, monkeypatch):
    c = _someone_standing(road)
    real = fa._preflight

    async def and_the_door_spoke(c2, grid=None):
        p = await real(c2, grid)
        fa.note_greeted(None)
        return p

    monkeypatch.setattr(fa, "_preflight", and_the_door_spoke)
    _go(c)
    # The lidar's person in front of the lens is who the door greeted: remembered, so that one.
    assert last_why() == "person_cooldown" and road.steps is None
    assert road.said == []


def test_a_face_the_door_greets_with_nobody_on_the_lidar_there_still_means_no_walk(road, monkeypatch):
    c = _someone_standing(road)
    real = fa._preflight

    async def and_the_door_spoke(c2, grid=None):
        p = await real(c2, grid)
        monkeypatch.setattr(lp, "people", lambda now=None, max_age=0: [])
        fa.note_greeted(None)
        return p

    monkeypatch.setattr(fa, "_preflight", and_the_door_spoke)
    _go(c)
    assert last_why() == "door_greeted" and road.steps is None


def test_somebody_the_lidar_lost_during_the_checks_is_not_left_for(road, monkeypatch):
    c = _someone_standing(road)
    real = fa._preflight

    async def and_they_went(c2, grid=None):
        p = await real(c2, grid)
        lp.STATE["follow"].reset()
        return p

    monkeypatch.setattr(fa, "_preflight", and_they_went)
    _go(c)
    assert last_why() == "gone" and road.steps is None


def test_the_door_camera_is_not_held_up_by_her_thinking_only_by_her_walking(road):
    stranger = facewatch.Sighting(kind="stranger", name=None, score=0.2)
    fa.STATE.update(phase="planning", kind="person")
    assert fa.holds_greeting(stranger) is False
    fa.STATE.update(phase="walking")
    assert fa.holds_greeting(stranger) is True
    fa.STATE.update(phase="greeting")
    assert fa.holds_greeting(stranger) is True
    fa.STATE.update(phase="checking")
    assert fa.holds_greeting(stranger) is False
    fa.STATE.update(phase="planning", kind="guest")
    assert fa.holds_greeting(stranger) is True


# ---- at the stop ------------------------------------------------------------------------------

def _during_check(road, monkeypatch, act):
    """Runs `act` once she is looking at the stop."""
    real = fa.asyncio.sleep
    did = [False]

    async def sleep(seconds):
        if fa.STATE["phase"] == "checking" and not did[0]:
            did[0] = True
            result = act()
            if asyncio.iscoroutine(result):
                await result
        await real(seconds)

    monkeypatch.setattr(fa.asyncio, "sleep", sleep)


def test_nothing_is_said_until_she_has_stopped_and_looked(road, monkeypatch):
    seen = {}

    def at_the_stop():
        seen.update(walking=event_walk.walking(), said=list(road.said), paused=greeter.paused())

    _during_check(road, monkeypatch, at_the_stop)
    c = _someone_standing(road)
    _go(c)
    assert seen["walking"] is False and seen["said"] == []
    assert seen["paused"] is False, "the door camera has its frames back"


def test_the_door_cameras_greeting_is_the_greeting(road, monkeypatch):
    _during_check(road, monkeypatch, lambda: fa.note_greeted(None))
    c = _someone_standing(road)
    rows = _rows(monkeypatch)
    _go(c)
    assert road.said == [], "she added nothing to it"
    row = _phase_row(rows, "greeted")
    assert row["via"] == "door" and row["walked"] is True


def test_no_face_at_all_is_a_plain_greeting_to_the_one_she_walked_up_to(road, monkeypatch):
    # FACE_GROUP_GREETING is on (robot.env): a crowd in the room must not make this "Hello everyone"
    monkeypatch.setattr(settings, "face_group_greeting", True)
    c = _someone_standing(road)
    rows = _rows(monkeypatch)
    _go(c)
    assert len(road.said) == 1
    text = road.said[0]["text"]
    assert road.said[0]["source"] == "face_approach" and road.said[0]["walking"] is False
    assert greeter.GROUP_GREETING_TH not in text and greeter.GROUP_GREETING_EN not in text
    assert road.shook == [], "no hand without a face: it may be a chair somebody carried in"
    row = _phase_row(rows, "greeted")
    assert row["via"] == "plain"
    assert row["spread_m"] == pytest.approx(0.0, abs=0.05), "the bench's evidence for a bar"


def test_a_face_the_door_could_not_decide_on_gets_the_hand_with_the_plain_greeting(road, monkeypatch):
    def seen():
        fa._eyes["face_at"] = time.monotonic() + 5

    _during_check(road, monkeypatch, seen)
    c = _someone_standing(road)
    _go(c)
    assert len(road.said) == 1 and road.shook == ["hand"]


def test_the_named_only_switch_keeps_the_hand_back_from_a_plain_greeting(road, monkeypatch):
    monkeypatch.setattr(settings, "face_handshake_named_only", True)
    c = _someone_standing(road)
    _go(c)
    assert len(road.said) == 1 and road.shook == []


def test_a_face_scan_opened_at_the_stop_is_not_talked_over(road, monkeypatch):
    _during_check(road, monkeypatch, lambda: greeter.hold("face-scan", 30.0))
    c = _someone_standing(road)
    try:
        _go(c)
    finally:
        greeter.release("face-scan")
    assert last_why() == "not_said" and road.said == []


def test_a_chair_leg_a_metre_off_is_not_them_still_being_there(road, monkeypatch):
    def left():
        road.people = []
        road.things = [(3.5, 0.8, 0.1)]

    _during_check(road, monkeypatch, left)
    c = _someone_standing(road)
    _go(c)
    assert last_why() == "lost_on_arrival" and road.said == []


def test_somebody_who_left_before_the_greeting_gets_no_words(road, monkeypatch):
    _during_check(road, monkeypatch, lambda: setattr(road, "people", []))
    c = _someone_standing(road)
    _go(c)
    assert last_why() == "lost_on_arrival" and road.said == []


def test_somebody_gone_when_she_arrives_gets_no_look_at_all(road, monkeypatch):
    real = fa._settled

    async def settled():
        ok = await real()
        road.people = []
        return ok

    monkeypatch.setattr(fa, "_settled", settled)
    c = _someone_standing(road)
    _go(c)
    assert last_why() == "lost_on_arrival" and road.said == [] and road.tilts == []


def test_a_face_already_greeted_stays_ungreeted(road, monkeypatch):
    def seen_again():
        now = time.monotonic()
        fa._eyes.update(face_at=now, suppressed_at=now)

    _during_check(road, monkeypatch, seen_again)
    c = _someone_standing(road)
    _go(c)
    assert last_why() == "already_greeted" and road.said == []


def test_somebody_talking_to_her_at_the_stop_needs_no_greeting(road, monkeypatch):
    def talk():
        monkeypatch.setattr(greeter, "_conversation_state", lambda s, now=None: "exchange")

    _during_check(road, monkeypatch, talk)
    c = _someone_standing(road)
    _go(c)
    assert road.said == [] and last_what() == "greeted"


def test_staff_switching_off_or_a_new_scene_at_the_stop_ends_it_silently(road, monkeypatch):
    _during_check(road, monkeypatch, lambda: fa.set_enabled(False))
    c = _someone_standing(road)
    _go(c)
    assert last_why() == "staff_off" and road.said == []


def test_a_new_scene_at_the_stop_ends_it_silently(road, monkeypatch):
    _during_check(road, monkeypatch, lambda: event_host.STATE.update(seq=99))
    c = _someone_standing(road)
    _go(c)
    assert last_why() == "scene_changed" and road.said == []


def test_the_head_looks_down_for_the_range_and_once_more_at_most(road):
    c = _someone_standing(road)
    _go(c)
    assert len(road.tilts) == 2
    # 1.75 m from her centre: atan(0.15 / 1.75) = 4.9 deg at 0.75 deg a percent
    assert road.tilts[0] == pytest.approx(7, abs=1)
    assert road.tilts[1] == settings.head_search_tilt


def test_the_detectors_tiny_constant_answer_is_not_a_face_at_the_stop(road, monkeypatch):
    # 560 refusal rows on the robot carry a "seen" of 9-11 px; 184 of 425 faces in the walk
    # pictures are under 15 px. She must look lower and greet plainly as if nobody were in view.
    w = types.SimpleNamespace(suppressed_at=None)

    def tiny():
        fa.observe(w, FRAME, [Located(None, x=500, width=10)], time.monotonic())

    _during_check(road, monkeypatch, tiny)
    c = _someone_standing(road)
    rows = _rows(monkeypatch)
    _go(c)
    assert fa._eyes["face_at"] is None
    assert len(road.tilts) == 2, "it did not stop her looking lower"
    assert _phase_row(rows, "greeted")["face"] is False


def test_a_real_face_at_the_stop_is_one(road, monkeypatch):
    w = types.SimpleNamespace(suppressed_at=None)

    def real():
        fa.observe(w, FRAME, [Located(None, x=300, width=280)], time.monotonic())

    _during_check(road, monkeypatch, real)
    c = _someone_standing(road)
    rows = _rows(monkeypatch)
    _go(c)
    assert fa._eyes["face_at"] is not None
    assert _phase_row(rows, "greeted")["face"] is True


def test_no_second_look_when_a_face_is_in_view(road, monkeypatch):
    def face():
        fa._eyes["face_at"] = time.monotonic() + 5

    _during_check(road, monkeypatch, face)
    c = _someone_standing(road)
    _go(c)
    assert len(road.tilts) == 1


def test_she_never_tilts_past_the_stops_whatever_the_range(road, monkeypatch):
    monkeypatch.setattr(settings, "head_tilt_down_max", 18)
    assert fa._tilt_for(0.3) <= 18
    monkeypatch.setattr(settings, "head_tilt_down_max", 5)
    assert fa._tilt_for(1.0) == 5


def test_a_person_who_shifted_is_turned_to_before_she_looks(road, monkeypatch):
    real = fa._settled

    async def settled():
        ok = await real()
        road.people = [(3.5, 0.35)]           # 0.35 m at 1.75 m: past the lens's half width
        return ok

    monkeypatch.setattr(fa, "_settled", settled)
    c = _someone_standing(road)
    _go(c)
    assert [s.get("do") for s in road.steps] == ["turn"]
    assert road.pose["yaw"] != 0.0


def test_the_hint_reaches_the_head_only_standing_and_inside_its_limits(monkeypatch):
    from tests.test_head_follow import Face

    monkeypatch.setattr(settings, "head_follow", True)
    monkeypatch.setattr(settings, "head_calibrate", False)
    monkeypatch.setattr(settings, "head_tilt_down_max", 18)
    monkeypatch.setattr(settings, "head_tilt_up_max", 12)
    from app import focus, walk_frames

    monkeypatch.setattr(focus, "attention", lambda t=None: {"bearing": None, "source": None, "track": None})
    monkeypatch.setattr(robot_face_link, "nav_activity", lambda session=None: "idle")

    monkeypatch.setattr(walk_frames, "base_moving", lambda: False)
    face = Face()
    follower = head_follow.HeadFollow(face)
    head_follow.reset()
    head_follow.look_hint(99)
    asyncio.run(follower.tick())
    assert face.ws.looks() == [(0, 18)], "clamped to the head's measured stop"
    assert head_follow._hinted_tilt(time.monotonic()) is None, "used up: the head's own loop has it back"
    asyncio.run(follower.tick())
    assert len(face.ws.looks()) == 1, "once"
    head_follow.look_hint(10)
    monkeypatch.setattr(walk_frames, "base_moving", lambda: True)
    monkeypatch.setattr(settings, "head_walk_scan", False)
    follower.sent_at = -math.inf
    asyncio.run(follower.tick())
    assert (0, 10) not in face.ws.looks(), "never on the move"


# ---- the door's rules at the stop: the real Watcher ---------------------------------------------

class Located:
    def __init__(self, vec, x=400, width=280):
        self.bbox = (x, 200, x + width, 200 + width)
        self.kps = None
        self.vec = vec
        self.det_score = 0.9
        self.area = width * width


def unit(seed):
    v = np.random.default_rng(seed).normal(size=512).astype(np.float32)
    return v / np.linalg.norm(v)


def _door(road, monkeypatch, frames):
    """The stop, with the real Watcher and greeter._greet deciding on these located faces."""
    from app import consent

    consent.record("คุณแขก", by="คุณแขก", version=consent.EVENT_GUEST, days=3)
    gallery = faces.Gallery(["คุณแขก", "พนักงาน"], np.stack([unit(1), unit(2)]),
                            [{"group": "event-guest", "source": "booth"}, {"group": "staff"}])
    watcher = facewatch.Watcher(gallery, threshold=0.5, confirm_frames=2, margin=0.1, name_when_alone=True,
                                min_face_px=50, cooldown_s=300, track_wait_s=0, probable_wait_s=0)
    monkeypatch.setattr(faces, "locate", lambda frame, det_size=None: list(frames))
    monkeypatch.setattr(facewatch.Watcher, "_publish", lambda self, frame, now: None)

    async def look():
        for _ in range(8):
            sighting = watcher.see(FRAME)
            if sighting is not None and not fa.holds_greeting(sighting):
                await greeter._greet(sighting)
                return
            await asyncio.sleep(0.01)

    _during_check(road, monkeypatch, look)
    return watcher


def test_somebody_in_our_data_alone_in_frame_is_named_at_the_stop(road, monkeypatch):
    _door(road, monkeypatch, [Located(unit(1))])
    rows = _rows(monkeypatch)
    c = _someone_standing(road)
    _go(c)
    assert len(road.said) == 1 and "คุณแขก" in road.said[0]["text"]
    assert _phase_row(rows, "greeted")["via"] == "door"
    assert "คุณแขก" not in json.dumps([r for r in rows if r[0] == "face_approach"], ensure_ascii=False, default=str)


def test_somebody_not_in_our_data_gets_a_greeting_with_no_name(road, monkeypatch):
    _door(road, monkeypatch, [Located(unit(50))])
    c = _someone_standing(road)
    _go(c)
    assert len(road.said) == 1 and "คุณแขก" not in road.said[0]["text"]


def test_a_guest_under_the_events_bar_is_not_named(road, monkeypatch):
    mixed = unit(1) * 0.55 + unit(77) * math.sqrt(1 - 0.55 ** 2)
    _door(road, monkeypatch, [Located((mixed / np.linalg.norm(mixed)).astype(np.float32))])
    c = _someone_standing(road)
    _go(c)
    assert all("คุณแขก" not in s["text"] for s in road.said)


def test_a_guest_with_somebody_beside_them_is_not_named(road, monkeypatch):
    _door(road, monkeypatch, [Located(unit(1)), Located(unit(50), x=100)])
    c = _someone_standing(road)
    _go(c)
    assert len(road.said) == 1 and "คุณแขก" not in road.said[0]["text"]


def test_a_withdrawn_guest_is_not_named(road, monkeypatch):
    from app import consent

    _door(road, monkeypatch, [Located(unit(1))])
    consent.revoke("คุณแขก")
    c = _someone_standing(road)
    _go(c)
    assert all("คุณแขก" not in s["text"] for s in road.said)


def test_somebody_the_door_already_greeted_gets_nothing_at_the_stop(road, monkeypatch):
    """Real Watcher: they spoke to her an hour ago, or five minutes: the name is in its cooldown."""
    watcher = _door(road, monkeypatch, [Located(unit(1))])
    watcher._last_greeted["name:คุณแขก"] = time.monotonic()
    real_see = watcher.see

    def see(frame, now=None, crop=None):
        result = real_see(frame, now, crop)
        fa.observe(watcher, frame, watcher._located or [], time.monotonic())
        return result

    watcher.see = see
    c = _someone_standing(road)
    _go(c)
    assert road.said == [] and last_why() == "already_greeted"


def test_the_camera_log_never_carries_a_guests_name(road, monkeypatch):
    _door(road, monkeypatch, [Located(unit(1))])
    rows = _rows(monkeypatch)
    c = _someone_standing(road)
    _go(c)
    assert "คุณแขก" not in json.dumps(rows, ensure_ascii=False, default=str)


# ---- a face she can see but not greet yet ("เห็นไม่ชัด") -------------------------------------------

def test_while_armed_the_door_waits_for_a_face_she_could_walk_up_to(road):
    assert fa.door_min_px(50) == settings.face_approach_door_px
    fa.STATE["staff_on"] = False
    assert fa.door_min_px(50) == 50
    fa.STATE["staff_on"] = True
    settings.face_approach_anyone = False
    assert fa.door_min_px(50) == 50


def test_a_far_stranger_is_not_greeted_across_the_lobby_while_armed(road, monkeypatch):
    far = Located(unit(50), width=100)
    monkeypatch.setattr(faces, "locate", lambda frame, det_size=None: [far])
    monkeypatch.setattr(facewatch.Watcher, "_publish", lambda self, frame, now: None)
    watcher = facewatch.Watcher(faces.Gallery(["พนักงาน"], unit(2).reshape(1, -1), [{"group": "staff"}]),
                                confirm_frames=2, min_face_px=50, track_wait_s=0, probable_wait_s=0)
    assert all(watcher.see(FRAME) is None for _ in range(8))
    fa.STATE["staff_on"] = False
    assert any(watcher.see(FRAME) is not None for _ in range(8)), "off, the door is what it was"


def test_a_far_face_is_a_reason_to_walk_and_is_never_described(road, monkeypatch):
    monkeypatch.setattr(settings, "face_approach_every_s", 0.0)
    monkeypatch.setattr(facewatch, "_head_now", lambda: (0.0, 0.0))
    monkeypatch.setattr(faces, "describe", lambda *a: pytest.fail("described"))
    gallery = faces.Gallery([], np.zeros((0, 512), np.float32))
    w = types.SimpleNamespace(gallery=gallery)
    t = time.monotonic()
    face = Located(None, x=442, width=100)    # straight ahead, ~4.9 m at the 19 deg lens
    fa.observe(w, FRAME, [face], t)
    fa.observe(w, FRAME, [face], t + 0.4)
    assert fa._eyes["far"] is None
    fa.observe(w, FRAME, [face], t + 0.8)
    assert fa._eyes["far"]["px"] == 100
    c = fa._person_candidate(t + 0.8)
    assert c["via"] == "camera" and c["name"] is None
    assert fa._person_candidate(t + 0.9) is None, "and once"
    road.people = [(4.6, 0.0)]
    _go(c)
    assert road.walk_to is not None
    assert road.walk_to["to"][0] == pytest.approx(4.6 - settings.face_approach_person_stop_m - fa.HALF_LENGTH_M, abs=0.15)


def _far_looks(face, t, n=3, w=None):
    w = w or types.SimpleNamespace(gallery=faces.Gallery([], np.zeros((0, 512), np.float32)))
    for i in range(n):
        fa.observe(w, FRAME, [face], t + 0.4 * i)
    return t + 0.4 * (n - 1)


def test_the_detectors_tiny_answer_is_never_a_person_to_walk_to(road, monkeypatch):
    monkeypatch.setattr(settings, "face_approach_every_s", 0.0)
    monkeypatch.setattr(facewatch, "_head_now", lambda: (0.0, 0.0))
    for width in (9, 11, 40, 79):
        fa._eyes.update(far=None, far_tracks=None)
        t = _far_looks(Located(None, x=500, width=width), time.monotonic(), n=8)
        assert fa._eyes["far"] is None, width
        assert fa._person_candidate(t) is None


def test_a_face_big_enough_for_the_door_is_not_a_reason_to_walk(road, monkeypatch):
    monkeypatch.setattr(settings, "face_approach_every_s", 0.0)
    monkeypatch.setattr(facewatch, "_head_now", lambda: (0.0, 0.0))
    _far_looks(Located(None, x=400, width=settings.face_approach_door_px), time.monotonic(), n=8)
    assert fa._eyes["far"] is None


def test_a_far_face_she_could_not_go_to_is_not_asked_about_every_tick(road, monkeypatch):
    monkeypatch.setattr(settings, "face_approach_every_s", 0.0)
    monkeypatch.setattr(facewatch, "_head_now", lambda: (0.0, 0.0))
    face = Located(None, x=442, width=100)
    t = _far_looks(face, time.monotonic())
    assert fa._person_candidate(t) is not None
    t = _far_looks(face, t + 0.4)
    assert fa._person_candidate(t) is None
    t = _far_looks(face, t + fa.TRACK_RETRY_S)
    assert fa._person_candidate(t) is not None


def test_a_far_face_with_nobody_on_the_lidar_there_is_a_poster(road, monkeypatch):
    road.people = []
    c = {"kind": "person", "via": "camera", "name": None, "px": 100, "cx": 512.0, "w": 1024, "head": 0.0,
         "at": time.monotonic(), "faces": 1}
    _go(c)
    assert last_why() == "no_lidar_target" and road.steps is None


def test_a_far_face_needs_the_measured_lens(road, monkeypatch):
    monkeypatch.setattr(settings, "robot_camera_hfov_measured", 0.0)
    road.people = [(4.6, 0.0)]
    c = {"kind": "person", "via": "camera", "name": None, "px": 100, "cx": 512.0, "w": 1024, "head": 0.0,
         "at": time.monotonic(), "faces": 1}
    _go(c)
    assert last_why() == "not_calibrated"


# ---- the dry run ------------------------------------------------------------------------------

def test_a_dry_run_covers_it_all_and_moves_and_says_and_tilts_nothing(road, monkeypatch):
    monkeypatch.setattr(settings, "face_approach_dry_run", True)
    monkeypatch.setattr(settings, "event_walk_enabled", False)
    fa.STATE["staff_on"] = False
    assert fa.set_enabled(True)["on"] is True
    c = _someone_standing(road, to=(1.0, 3.0))
    rows = _rows(monkeypatch)
    _go(c)
    assert road.steps is None and road.said == [] and road.shook == [] and road.tilts == [] and road.rung is None
    row = _phase_row(rows, "would_walk")
    assert row["kind"] == "person" and row["seen_by"] == "lidar" and row["turn"] is True
    assert row["bearing_deg"] == pytest.approx(math.degrees(math.atan2(3.0, 1.0)), abs=3.0)
    assert row["travel_m"] == pytest.approx(math.hypot(1.0, 3.0) - 1.4 - fa.HALF_LENGTH_M, abs=0.15)


def test_a_dry_run_measures_the_same_person_again_soon(road, monkeypatch):
    monkeypatch.setattr(settings, "face_approach_dry_run", True)
    c = _someone_standing(road)
    _go(c)
    road.t += 0.5
    left = fa.STATE["spots"][-1][2] + settings.face_approach_person_cooldown_s - time.monotonic()
    assert 0 < left <= fa.DRY_RUN_REPEAT_S


# ---- off, and staff's switch ------------------------------------------------------------------

def test_off_a_person_standing_there_is_still_nobody(road, monkeypatch):
    c = _someone_standing(road)
    assert c is not None
    monkeypatch.setattr(settings, "face_approach_anyone", False)
    assert fa._person_candidate(road.t) is None
    fa._eyes["far"] = {"kind": "person", "via": "camera", "at": road.t}
    assert fa._person_candidate(road.t) is None


def test_off_nobody_is_a_candidate_and_the_lidar_reads_as_it_did(world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "lidar_people", True)
    monkeypatch.setattr(settings, "head_follow", True)
    assert settings.face_approach_anyone is False
    assert fa._person_candidate(time.monotonic()) is None
    assert lp.want_far() is False
    assert fa.door_min_px(50) == 50
    assert lp.enabled() is True
    monkeypatch.setattr(settings, "head_follow", False)
    assert lp.enabled() is False


def test_off_a_guest_walk_is_one_moveto_as_it_was(world):  # noqa: F811
    assert fa.steps_for({"goal": (1.0, 2.0), "face_deg": 90.0, "speed": 0.2, "bearing_deg": 90.0}) == [
        {"to": [1.0, 2.0], "face": 90.0, "speed": 0.2, "label": "เข้าหาแขก"}]


def test_staff_cannot_turn_it_on_without_the_lidar(road, monkeypatch):
    fa.STATE["staff_on"] = False
    monkeypatch.setattr(settings, "lidar_people", False)
    assert fa.set_enabled(True)["error"] == "no_lidar"
    monkeypatch.setattr(settings, "lidar_people", True)
    assert fa.set_enabled(True)["on"] is True


def test_a_ceremony_scene_cannot_turn_it_on(road, monkeypatch):
    fa.STATE["staff_on"] = False
    monkeypatch.setattr(event_host, "ceremony_now", lambda: True)
    assert fa.set_enabled(True)["error"] == "ceremony"


def test_the_staff_page_says_which_road_and_no_names(road):
    c = _someone_standing(road)
    _go(c)
    view = fa.snapshot()
    assert view["anyone"] is True and view["last"]["what"] == "greeted"


def test_the_event_file_keeps_her_out_of_the_multimedia_room():
    doc = event_host._normalise(json.loads(open("data/events/floor1_event.json", encoding="utf-8").read()), {})
    assert doc["no_walk_up_below_y"] > -7.78 + 0.7             # the door's leaf, the spot's own margin


def test_the_keep_out_line_stops_a_walk_toward_the_door(road, monkeypatch):
    monkeypatch.setattr(event_host, "load", lambda: {"no_walk_up_below_y": -1.0, "hazards": []})
    assert event_host.keep_out_on_line(0.0, 0.0, 0.0, -2.0) is True
    assert event_host.keep_out_on_line(0.0, 0.0, 0.0, -0.5) is False
    monkeypatch.setattr(event_host, "load", lambda: {"hazards": []})
    assert event_host.keep_out_on_line(0.0, 0.0, 0.0, -2.0) is False


# ---- the map -----------------------------------------------------------------------------------

def test_the_map_read_is_kept_and_refused_when_unreadable(world, monkeypatch):  # noqa: F811
    import struct

    calls = []
    answer = [OSError("board")]

    async def raw(method, path):
        calls.append(path)
        if isinstance(answer[0], Exception):
            raise answer[0]
        return answer[0]

    monkeypatch.setattr(robot_chassis, "request_raw", raw)
    assert asyncio.run(fa._map_grid()) is None
    assert calls == [robot_chassis.MAP_EXPLORE]
    answer[0] = struct.pack("<ffIIf", -1.0, -1.0, 4, 4, 0.5) + bytes(12) + struct.pack("<I", 16) + bytes(16)
    grid = asyncio.run(fa._map_grid())
    assert grid is not None and grid.res == 0.5
    assert asyncio.run(fa._map_grid()) is grid and len(calls) == 2, "kept, not read every look"


def test_sight_line_sees_a_wall_between_and_not_one_beside():
    grid = Grid([(2.0, -2.0, 2.05, 2.0)])
    assert fa.sight_blocked(grid, 0.0, 0.0, 4.0, 0.0) is True
    assert fa.sight_blocked(grid, 0.0, 0.0, 1.5, 0.0) is False
    assert fa.sight_blocked(grid, 0.0, 3.0, 4.0, 3.0) is False


# ---- scanning faster --------------------------------------------------------------------------

class _Det:
    def __init__(self):
        self.calls = []

    def detect(self, image, input_size=None, metric="default"):
        self.calls.append(input_size)
        return np.zeros((0, 5)), None


def test_det_size_default_is_exactly_the_old_call(monkeypatch):
    det = _Det()
    monkeypatch.setattr(faces, "_analyzer", lambda det_size=640: types.SimpleNamespace(det_model=det))
    faces.locate(FRAME)
    assert det.calls == [None]


def test_det_size_setting_goes_to_the_door_frames(monkeypatch):
    det = _Det()
    monkeypatch.setattr(faces, "_analyzer", lambda det_size=640: types.SimpleNamespace(det_model=det))
    monkeypatch.setattr(settings, "face_det_size", 320)
    faces.locate(FRAME)
    assert det.calls == [(320, 320)]


def test_a_portrait_for_the_gallery_is_always_found_at_640(monkeypatch):
    from app import enrollment

    det = _Det()
    monkeypatch.setattr(faces, "_analyzer", lambda det_size=640: types.SimpleNamespace(det_model=det))
    monkeypatch.setattr(settings, "face_det_size", 320)
    enrollment.usable_face(FRAME)
    assert det.calls == [None]
    faces.detect(FRAME)
    assert det.calls == [None, None]


def _fast_watcher(monkeypatch, found, **kw):
    monkeypatch.setattr(faces, "locate", lambda frame, det_size=None: list(found))
    monkeypatch.setattr(facewatch.Watcher, "_publish", lambda self, frame, now: None)
    gallery = faces.Gallery(["พนักงาน"], unit(2).reshape(1, -1), [{"group": "staff"}])
    return facewatch.Watcher(gallery, confirm_frames=2, min_face_px=50, cooldown_s=300, track_wait_s=0,
                             probable_wait_s=0, **kw)


def test_only_a_name_being_confirmed_hurries(monkeypatch):
    monkeypatch.setattr(settings, "face_fast_confirm", True)
    w = _fast_watcher(monkeypatch, [Located(unit(2))])
    assert w.see(FRAME, 100.0) is None
    assert w.deciding is True and greeter._frame_gap(w, 0.167) == greeter.FAST_FRAME_GAP_S
    monkeypatch.setattr(faces, "locate", lambda frame, det_size=None: [])
    w.see(FRAME, 100.1)
    assert w.deciding is False and greeter._frame_gap(w, 0.167) == 0.167


def test_a_stranger_keeps_face_fps_pace(monkeypatch):
    # At ~10 fps from the camera, no sleep is closer-together frames: 4 in 0.57 s instead of 1.44 s
    # (PC), a colleague walking in under the bar gets the anonymous greeting, and the same-spot
    # rules then keep their name back (review 5 Nov).
    monkeypatch.setattr(settings, "face_fast_confirm", True)
    w = _fast_watcher(monkeypatch, [Located(unit(50))])
    assert w.see(FRAME, 100.0) is None
    assert w.deciding is False and greeter._frame_gap(w, 0.167) == 0.167


def test_a_name_needs_its_frames_at_least_three_tenths_apart(monkeypatch):
    monkeypatch.setattr(settings, "face_fast_confirm", True)
    w = _fast_watcher(monkeypatch, [Located(unit(2))])
    assert w.see(FRAME, 100.00) is None
    assert w.see(FRAME, 100.10) is None, "two frames, 0.1 s apart: nearly one picture"
    assert w.deciding is True, "still hurrying to the next one"
    assert w.see(FRAME, 100.20) is None
    assert w.see(FRAME, 100.31) is not None


def test_off_two_frames_name_at_any_spacing_as_before(monkeypatch):
    w = _fast_watcher(monkeypatch, [Located(unit(2))])
    assert w.see(FRAME, 100.00) is None
    assert w.see(FRAME, 100.01) is not None


def test_somebody_already_greeted_is_not_a_reason_to_hurry(monkeypatch):
    monkeypatch.setattr(settings, "face_fast_confirm", True)
    w = _fast_watcher(monkeypatch, [Located(unit(2))])
    sighting = None
    for i in range(4):
        sighting = sighting or w.see(FRAME, 100.0 + i * 0.2)
    assert sighting is not None
    monkeypatch.setattr(faces, "locate", lambda frame, det_size=None: [])
    w.see(FRAME, 101.0)                     # they looked away: the streak starts again...
    monkeypatch.setattr(faces, "locate", lambda frame, det_size=None: [Located(unit(2))])
    assert w.see(FRAME, 101.2) is None
    assert w.deciding is False              # ...but there is nothing to decide about them


def test_the_number_of_frames_needed_does_not_change(monkeypatch):
    monkeypatch.setattr(settings, "face_fast_confirm", True)
    w = _fast_watcher(monkeypatch, [Located(unit(50))])
    results = [w.see(FRAME, 100.0 + i * 0.4) for i in range(4)]
    assert results[:3] == [None, None, None] and results[3] is not None, "a stranger: twice the name's two"
    w2 = _fast_watcher(monkeypatch, [Located(unit(2))])
    assert w2.see(FRAME, 100.0) is None and w2.see(FRAME, 100.4) is not None


def test_off_every_frame_keeps_the_sleep_as_before(monkeypatch):
    w = _fast_watcher(monkeypatch, [Located(unit(2))])
    w.see(FRAME, 100.0)
    assert greeter._frame_gap(w, 0.167) == 0.167


def test_a_companion_under_the_raised_door_bar_is_still_company(road, monkeypatch):
    # While armed the door greets from 126 px; a 115 px face beside a 180 px one still means
    # the name must not be said to two people.
    w = _fast_watcher(monkeypatch, [])
    faces_here = [Located(unit(2), x=500, width=180), Located(unit(50), x=100, width=115)]
    monkeypatch.setattr(faces, "locate", lambda frame, det_size=None: list(faces_here))
    w.see(FRAME, 100.0)
    assert w.min_face_px == settings.face_approach_door_px, "armed"
    assert w._usable(faces_here) == 2


def test_a_greeting_with_nothing_playing_has_no_extra_pause(monkeypatch):
    calls = []

    async def announce(text, **kw):
        calls.append(kw)
        return True

    from app import events

    monkeypatch.setattr(events, "announce", announce)
    sighting = facewatch.Sighting(kind="stranger", name=None, score=0.2, faces_in_frame=1)
    asyncio.run(greeter._greet(sighting))
    assert "then_pause" not in calls[-1], "off: as it was"
    monkeypatch.setattr(settings, "face_fast_confirm", True)
    asyncio.run(greeter._greet(sighting))
    assert calls[-1]["then_pause"] == 0.0


def test_the_log_says_how_long_a_decision_took_and_how_long_frames_take(monkeypatch):
    monkeypatch.setattr(settings, "face_fast_confirm", True)
    w = _fast_watcher(monkeypatch, [Located(unit(50))])
    results = [w.see(FRAME, 100.0 + i) for i in range(4)]
    assert results[3].track_s == pytest.approx(3.0)
    rows = _rows(monkeypatch)
    perf = greeter._Perf()
    w._located = [Located(None, width=200)]
    perf.note(w, 0.2)
    w._located = [Located(None, width=10)]       # the detector's constant tiny answer: no face
    perf.note(w, 0.05)
    perf.at -= greeter.PERF_EVERY_S + 1
    perf.note(w, 0.05)
    row = next(f for e, f in rows if e == "face_perf")
    assert row["with_face"] == 1 and row["face_p50_ms"] == 200 and row["empty_p50_ms"] == 50


def test_off_no_face_perf_row(monkeypatch):
    rows = _rows(monkeypatch)
    w = types.SimpleNamespace(_located=[1])
    perf = greeter._Perf()
    perf.at -= greeter.PERF_EVERY_S + 1
    perf.note(w, 0.2)
    assert rows == []


def test_no_closer_look_at_a_face_that_is_big_already(monkeypatch):
    from app import robot_camera

    asked = []
    monkeypatch.setattr(robot_camera.feed, "request_crop", lambda *a, **k: asked.append(a))
    want = {"bbox": (0, 0, 200, 200), "w": 1024, "h": 768, "px": 200}
    greeter._ask_closer(want, -100.0, 0.0)
    assert asked, "off: as it was"
    asked.clear()
    monkeypatch.setattr(settings, "face_fast_confirm", True)
    greeter._ask_closer(want, -100.0, 0.0)
    assert asked == []
    greeter._ask_closer(dict(want, px=120), -100.0, 0.0)
    assert asked


def test_analyze_log_reports_the_scan_and_the_walks_in_numbers(capsys):
    import importlib.util
    from pathlib import Path

    path = Path(fa.__file__).resolve().parents[1] / "scripts" / "analyze_log.py"
    spec = importlib.util.spec_from_file_location("analyze_log_person", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.face_speed([
        {"event": "face_perf", "empty_p50_ms": 120, "face_p50_ms": 400},
        {"event": "face_seen", "track_s": 0.9, "name": "คุณแขก"},
        {"event": "face_approach", "phase": "refused", "why": "path_blocked"},
        {"event": "face_approach", "phase": "greeted", "via": "plain"},
    ])
    out = capsys.readouterr().out
    assert "400 ms" in out and "0.90 s" in out and "path_blocked 1" in out and "plain 1" in out
    assert "คุณแขก" not in out
    mod.face_speed([{"event": "said"}])
    assert capsys.readouterr().out == ""


# ---- review 5 Nov: a stop with nothing walking yet ------------------------------------------------

def test_the_staffs_stop_while_she_is_still_checking_is_a_stop(road, monkeypatch):
    monkeypatch.setattr(fa, "PREFLIGHT_GAP_S", 0.05)

    async def scenario():
        c = _someone_standing_async(road)
        task = asyncio.create_task(fa._approach(c))
        await asyncio.sleep(0.03)
        assert fa.STATE["phase"] == "planning"
        await event_host.stop()
        await asyncio.wait_for(task, 5.0)

    asyncio.run(scenario())
    assert road.steps is None and road.said == []
    assert fa.STATE["staff_on"] is False


@pytest.mark.parametrize("reason", ["voice", "desk"])
def test_voice_or_desk_with_no_walk_in_flight_turns_it_off(road, reason):
    assert fa.armed() is True and event_walk.walking() is False
    asyncio.run(event_walk.stop_from(reason))
    assert fa.STATE["staff_on"] is False


def test_a_stop_during_the_last_board_read_means_no_walk(road, monkeypatch):
    c = _someone_standing(road)
    real = fa._board_refusal
    calls = [0]

    async def and_a_stop_comes(*a):
        calls[0] += 1
        result = await real(*a)
        if calls[0] == 2:
            fa._turn_off("voice")            # after the last gate read `armed()`
        return result

    monkeypatch.setattr(fa, "_board_refusal", and_a_stop_comes)
    _go(c)
    assert last_why() == "not_armed" and road.steps is None


def test_a_stop_between_arriving_and_the_turn_means_no_turn(road, monkeypatch):
    real = fa._settled

    async def settled():
        ok = await real()
        road.people = [(3.5, 0.35)]
        fa._turn_off("staff")
        return ok

    monkeypatch.setattr(fa, "_settled", settled)
    c = _someone_standing(road)
    _go(c)
    assert last_why() == "staff_off"
    assert [s.get("do") or "to" for s in road.steps] == ["to"], "the walk there, and no turn"
    assert road.said == []


# ---- review 5 Nov: the strip ahead ----------------------------------------------------------------

def test_somebody_else_stepping_in_front_stops_her_at_once(road):
    def in_the_way():
        road.people = [(3.5, 0.0), (road.pose["x"] + fa.HALF_LENGTH_M + 0.2, 0.0)]

    _mid_walk(road, in_the_way)
    assert last_why() == "path_blocked"
    assert event_walk.current().stop_requested == "approach_path_blocked"
    assert road.said == []


def test_a_target_closing_past_the_tracking_radius_does_not_walk_on(road):
    def rushes_up():
        road.people = [(road.pose["x"] + fa.HALF_LENGTH_M + 0.3, 0.0)]

    started = [None]
    _mid_walk(road, lambda: (started.__setitem__(0, time.monotonic()), rushes_up())[1])
    assert time.monotonic() - started[0] < fa.LOST_S + 2.0
    assert event_walk.current().stop_requested in ("approach_met", "approach_path_blocked")


def test_the_target_in_the_strip_is_met_not_blocked(road):
    async def closer():
        await asyncio.sleep(0.2)             # she has rolled to her goal, they are within TRACK_RADIUS_M
        road.people = [(road.pose["x"] + fa.HALF_LENGTH_M + 0.7, 0.0)]

    _mid_walk(road, closer)
    assert event_walk.current().stop_requested == "approach_met"


def test_somebody_beside_the_strip_lets_her_walk_on(road):
    road.arrive = True
    from app import robot_lanes as rl

    road.people = [(1.0, 1.0)]
    pts = rl.scan_points({"laser_points": road.scan()["points"]})
    p = {"start": (0.0, 0.0), "goal": (2.0, 0.0)}
    assert len(fa.strip_ahead(pts, road.pose, p)) == 0
    road.people = [(1.0, 0.0)]
    pts = rl.scan_points({"laser_points": road.scan()["points"]})
    assert len(fa.strip_ahead(pts, road.pose, p)) > 0


def test_the_strip_is_along_her_line_not_her_heading(road):
    from app import robot_lanes as rl

    road.people = [(1.0, 0.0)]
    pose = dict(road.pose)
    pts = rl.scan_points({"laser_points": road.scan()["points"]})
    # she is still turning: facing 90 deg from the line she is about to walk
    turned = dict(pose, yaw=math.pi / 2)
    rotated = np.column_stack([pts[:, 1], -pts[:, 0]])          # the same world, seen from there
    assert len(fa.strip_ahead(rotated, turned, {"start": (0.0, 0.0), "goal": (2.0, 0.0)})) > 0


def test_something_past_the_goal_inside_the_strip_refuses_the_walk_before_it_starts(road):
    # Or she sets out and breaks it off at the end, in front of the guest.
    from app import robot_lanes as rl

    road.people = [(3.5, 0.0)]
    road.things = [(3.0, 0.45, 0.05)]        # 1.25 m past a goal at 1.75, beside their line
    pts = rl.scan_points({"laser_points": road.scan()["points"]})
    assert fa.plan(pts, road.pose, 0.0, 3.5, SQUARE, stop_m=1.4, lidar_range=True)["why"] == "path_blocked"


def test_the_persons_own_front_leg_does_not_block_the_walk(road):
    from app import robot_lanes as rl

    road.people = [(3.5, 0.0)]
    road.things = [(3.25, 0.0, 0.03)]        # a leg stepped forward: nearer than their middle
    pts = rl.scan_points({"laser_points": road.scan()["points"]})
    assert fa.plan(pts, road.pose, 0.0, 3.5, SQUARE, stop_m=1.0, lidar_range=True)["why"] is None


# ---- review 5 Nov: spot turns and staff's area ------------------------------------------------------

NEAR_EDGE = [[-0.3, -8.0], [8.0, -8.0], [8.0, 8.0], [-0.3, 8.0]]       # she stands 0.3 m from its west edge


def test_a_spot_turn_for_somebody_within_reach_needs_room_from_the_edge(road):
    c = _someone_standing(road, to=(0.0, 1.5))
    fa._save_area(NEAR_EDGE)
    _go(c)
    assert last_why() == "outside_area" and road.steps is None


def test_a_spot_turn_outside_the_area_is_refused(road):
    c = _someone_standing(road, to=(0.0, 1.5))
    fa._save_area([[1.0, -8.0], [8.0, -8.0], [8.0, 8.0], [1.0, 8.0]])
    _go(c)
    assert last_why() == "outside_area" and road.steps is None


def test_somebody_within_reach_straight_ahead_needs_no_room_she_does_not_move(road):
    c = _someone_standing(road, to=(1.5, 0.0))
    fa._save_area(NEAR_EDGE)
    _go(c)
    assert road.steps is None and last_what() == "greeted"


def test_a_walk_that_starts_with_a_turn_needs_that_room_too(road):
    c = _someone_standing(road, to=(1.0, 3.0))
    fa._save_area([[-0.45, -8.0], [8.0, -8.0], [8.0, 8.0], [-0.45, 8.0]])     # 0.45: enough to walk, not to turn
    _go(c)
    assert last_why() == "outside_area" and road.steps is None


def test_the_whole_line_keeps_the_margin_not_only_the_goal(road):
    from app import robot_lanes as rl

    road.people = [(3.5, 0.0)]
    pts = rl.scan_points({"laser_points": road.scan()["points"]})
    # a notch reaches to 0.2 m of the line half way along; the goal is well clear of it
    notched = [(-5.0, -5.0), (0.8, -5.0), (0.8, -0.2), (1.0, -0.2), (1.0, -5.0), (8.0, -5.0), (8.0, 5.0), (-5.0, 5.0)]
    assert fa.plan(pts, road.pose, 0.0, 3.5, notched, stop_m=1.4, lidar_range=True)["why"] == "outside_area"
    assert fa.plan(pts, road.pose, 0.0, 3.5, SQUARE, stop_m=1.4, lidar_range=True)["why"] is None


def test_no_turn_at_the_edge_at_the_stop_she_looks_from_where_she_stands(road, monkeypatch):
    real = fa._settled

    async def settled():
        ok = await real()
        road.people = [(3.5, 0.35)]
        fa._save_area([[road.pose["x"] - 0.3, -8.0], [8.0, -8.0], [8.0, 8.0], [road.pose["x"] - 0.3, 8.0]])
        return ok

    monkeypatch.setattr(fa, "_settled", settled)
    c = _someone_standing(road)
    rows = _rows(monkeypatch)
    _go(c)
    assert [s.get("do") or "to" for s in road.steps] == ["to"], "no turn"
    assert _phase_row(rows, "no_reaim") is not None
    assert len(road.said) == 1, "and she still greets"


# ---- review 5 Nov: a guest's name and the turn log ---------------------------------------------------

def _said_rows(monkeypatch, *script, private=1):
    from app.providers.base import ProviderEvent
    from app.session import VoiceSession

    class FakeWS:
        async def send_text(self, text):
            pass

    class FakeProvider:
        output_sample_rate = 24000

        def events(self):
            async def gen():
                for kind, text in script:
                    if kind == "guest":
                        sess._guest_started_turn()
                    else:
                        yield ProviderEvent(kind=kind, text=text)
            return gen()

    sess = VoiceSession.__new__(VoiceSession)
    sess.ws = FakeWS()
    sess.provider = FakeProvider()
    sess._sent_audio_ms = 0.0
    sess._spoke_this_turn = False
    sess._nudge_index = None
    sess._nudge_count = 0
    sess._said_private = private
    rows = _rows(monkeypatch)
    asyncio.run(sess._provider_to_browser())
    return [f for e, f in rows if e == "said"]


def test_the_answer_to_a_guests_greeting_leaves_no_text_in_the_log(monkeypatch):
    rows = _said_rows(monkeypatch, ("assistant_transcript", "สวัสดีค่ะคุณแขก"), ("turn_complete", ""),
                      ("assistant_transcript", "ค่ะ"))
    assert [r["text"] for r in rows] == ["", ""], "a late piece of the same turn too"
    assert rows[0]["withheld"] is True and rows[0]["chars"] == len("สวัสดีค่ะคุณแขก")


def test_once_somebody_has_spoken_she_is_logged_as_ever(monkeypatch):
    rows = _said_rows(monkeypatch, ("assistant_transcript", "สวัสดีค่ะคุณแขก"), ("turn_complete", ""),
                      ("guest", ""), ("assistant_transcript", "โครงการอยู่ที่พัทยาค่ะ"))
    assert [r["text"] for r in rows] == ["", "โครงการอยู่ที่พัทยาค่ะ"]


def test_a_cough_during_the_greetings_own_turn_does_not_let_the_name_through(monkeypatch):
    rows = _said_rows(monkeypatch, ("guest", ""), ("assistant_transcript", "สวัสดีค่ะคุณแขก"))
    assert [r["text"] for r in rows] == [""]


def test_nobodys_greeting_is_private_by_default(monkeypatch):
    rows = _said_rows(monkeypatch, ("assistant_transcript", "สวัสดีค่ะ"), private=0)
    assert [r["text"] for r in rows] == ["สวัสดีค่ะ"]


def _announcing_session(monkeypatch, fail=False):
    from app import display, events

    async def send_text(text):
        if fail:
            raise OSError("gone")

    async def heard(max_wait=0, then_pause=0):
        return 0.0

    session = types.SimpleNamespace(provider=types.SimpleNamespace(send_text=send_text), metrics=None)
    monkeypatch.setattr(session_module, "_active", session)
    monkeypatch.setattr(display, "wait_until_heard", heard)
    return session, events


def test_announce_marks_the_turn_only_when_asked(monkeypatch):
    session, events = _announcing_session(monkeypatch)
    assert asyncio.run(events.announce("x", source="test")) is True
    assert getattr(session, "_said_private", 0) == 0
    assert asyncio.run(events.announce("x", source="test", said_private=True)) is True
    assert session._said_private == 1


def test_a_greeting_that_never_went_out_marks_nothing(monkeypatch):
    session, events = _announcing_session(monkeypatch, fail=True)
    assert asyncio.run(events.announce("x", source="test", said_private=True)) is False
    assert session._said_private == 0


def _door_said(monkeypatch, sighting):
    seen = {}

    async def announce(text, **kw):
        seen.update(kw)
        return True

    from app import events

    monkeypatch.setattr(events, "announce", announce)
    asyncio.run(greeter._greet(sighting))
    return seen


def test_the_door_marks_a_booth_guests_greeting_and_no_other(monkeypatch):
    booth = facewatch.Sighting(kind="known", name="คุณแขก", score=0.7, meta={"source": "booth"}, faces_in_frame=1)
    assert _door_said(monkeypatch, booth).get("said_private") is True
    staff = facewatch.Sighting(kind="known", name="พนักงาน", score=0.7, meta={"group": "staff"}, faces_in_frame=1)
    assert "said_private" not in _door_said(monkeypatch, staff)
    unnamed = facewatch.Sighting(kind="stranger", name=None, score=0.7, meta={"unnamed": "company", "source": "booth"},
                                 faces_in_frame=2)
    assert "said_private" not in _door_said(monkeypatch, unnamed)


def test_the_walk_up_to_a_named_guest_marks_it_too(world, monkeypatch):  # noqa: F811
    from tests.test_face_approach import candidate

    seen = {}
    from app import events

    real = events.announce

    async def announce(text, **kw):
        seen.update(kw)
        return await real(text, **kw)

    monkeypatch.setattr(events, "announce", announce)
    c = candidate()
    fa._eyes["candidate"] = c
    asyncio.run(fa._approach(c))
    assert seen.get("said_private") is True


# ---- review 5 Nov: nobody pressed anything -----------------------------------------------------------

def test_an_autonomous_walk_does_not_pet_the_dead_mans_switch(road):
    async def scenario():
        event_walk.STATE["heartbeat"] = old = time.monotonic() - 4.0
        result = await event_walk.start("x", "x", [{"do": "turn", "face": 10.0}], dock=None, autonomous=True)
        assert result["ok"] is True
        assert event_walk.STATE["heartbeat"] == old
        await event_walk.stop("staff")

    asyncio.run(scenario())


def test_a_pressed_walk_still_does(road):
    async def scenario():
        event_walk.STATE["heartbeat"] = old = time.monotonic() - 4.0
        await event_walk.start("x", "x", [{"do": "turn", "face": 10.0}], dock=None)
        assert event_walk.STATE["heartbeat"] > old
        await event_walk.stop("staff")

    asyncio.run(scenario())


def test_her_walk_up_and_a_roaming_leg_are_autonomous(road, monkeypatch):
    from app import event_roam

    kinds = []

    async def start(spot, name, steps, **kw):
        kinds.append(kw.get("autonomous"))
        return {"ok": False, "error": "x", "error_th": "x"}

    monkeypatch.setattr(event_walk, "start", start)
    c = _someone_standing(road)
    _go(c)
    assert kinds == [True]

    async def here(steps, doc):
        return [{"to": [1.0, 1.0]}]

    monkeypatch.setattr(event_host, "load", lambda: {"spots": {"a": {"name_th": "a", "pose": [1.0, 1.0, None]}}})
    monkeypatch.setattr(event_host, "steps_from_here", here)
    monkeypatch.setattr(event_host, "dock_doc", lambda doc: None)
    asyncio.run(event_roam._walk("a", 0))
    assert kinds == [True, True]


# ---- a line she hears "หยุด" on, before a wheel turns (owner 6 Nov) ----------------------------

def _line():
    line = types.SimpleNamespace(summoned=True, _greeting_turn_seen=False, _ever_heard=False,
                                 provider=object(), profile="event", ears=[])
    return line


def test_with_no_line_she_rings_for_one_before_a_wheel_turns(road, monkeypatch):
    """Owner 6 Nov: "ถ้าเห็นคนหุ่นจะเดินเข้าไป แล้วตอนเดินก็เปิดสายเลย". Review: with no line up she walked
    3 m deaf and rang only at the stop, so a "หยุด" on the way reached nobody."""
    line = _line()
    line.open_ears = lambda why: line.ears.append((why, road.steps))
    monkeypatch.setattr(session_module, "_active", None)
    rung = []

    async def summon(reason="server"):
        rung.append((reason, road.steps))
        session_module._active = line
        return 1

    monkeypatch.setattr(wake, "summon", summon)
    _go(_someone_standing(road))
    assert rung[:1] == [("face_approach", None)], "rung before a single step"
    assert road.steps is not None, "and then she walked"
    assert line.ears and line.ears[0] == ("face_approach", None), \
        "hearing from before the first step, not after a greeting"


def test_no_line_comes_up_no_walk(road, monkeypatch):
    monkeypatch.setattr(session_module, "_active", None)
    monkeypatch.setattr(fa, "LINE_WAIT_S", 0.3)
    _go(_someone_standing(road))
    assert road.rung == "face_approach" and road.steps is None
    assert (fa.STATE["last"] or {}).get("why") == "no_line"


def test_under_a_mic_hold_she_walks_up_to_nobody(road, monkeypatch):
    """The mute: the page's mic is shut and a "หยุด" would not be heard -- no ring, no look, no walk."""
    monkeypatch.setattr(wake, "mic_held", lambda: True)
    monkeypatch.setattr(session_module, "_active", None)
    _go(_someone_standing(road))
    assert road.rung is None and road.steps is None and road.said == [] and road.shook == []
    assert (fa.STATE["last"] or {}).get("why") == "mic_held"


def test_the_offline_voice_is_not_a_line_to_walk_on(monkeypatch):
    monkeypatch.setattr(robot_face_link, "is_robot_face", lambda s: s is not None)
    assert fa._line_ready(types.SimpleNamespace(provider=object()))
    assert not fa._line_ready(types.SimpleNamespace(provider=object(), _offline=True)), "it calls no tool"
    assert not fa._line_ready(types.SimpleNamespace(provider=None)), "still dialling"
    assert not fa._line_ready(None)


def test_a_desk_tab_is_not_the_robots_line(monkeypatch):
    """A tab opened through `forward` supersedes the robot face: a "หยุด" said at the robot reaches it not."""
    monkeypatch.setattr(robot_face_link, "is_robot_face", lambda s: False)
    assert not fa._line_ready(types.SimpleNamespace(provider=object()))


# ---- what changes during the dial is asked after it (review 6 Nov) ------------------------------

@pytest.mark.parametrize("meanwhile", ["mute", "scene", "door"])
def test_what_changes_during_the_dial_stops_the_walk(road, monkeypatch, meanwhile):
    """The dial takes seconds. A mute, a scene press or the door camera greeting somebody in them
    used to walk her anyway: only `armed()` was asked after the wait."""
    line = _line()
    line.open_ears = lambda why: None
    monkeypatch.setattr(session_module, "_active", None)
    held = {"on": False}
    monkeypatch.setattr(wake, "mic_held", lambda: held["on"])

    async def summon(reason="server"):
        road.rung = reason
        if meanwhile == "mute":
            held["on"] = True
        elif meanwhile == "scene":
            monkeypatch.setitem(event_host.STATE, "since", time.time())
        else:
            fa.STATE["door_at"] = time.monotonic()
        session_module._active = line
        return 1

    monkeypatch.setattr(wake, "summon", summon)
    _go(_someone_standing(road))
    assert road.rung == "face_approach"
    assert road.steps is None, "no wheel turned"
    assert road.said == [] and road.shook == []


def _during_walk(road, monkeypatch, act):
    """Runs `act` once while she walks."""
    real = fa.asyncio.sleep
    did = [False]

    async def sleep(seconds):
        if fa.STATE["phase"] == "walking" and event_walk.walking() and not did[0]:
            did[0] = True
            act()
        await real(seconds)

    monkeypatch.setattr(fa.asyncio, "sleep", sleep)


@pytest.mark.parametrize("what", ["line_dropped", "muted"])
def test_nobody_left_to_say_stop_to_ends_the_walk(road, monkeypatch, what):
    """With the line parked when unused, a summoned line nobody greeted ends after 30 s; a mute can
    come any time. Either way nobody can stop her by voice any more: she stops."""
    held = {"on": False}
    monkeypatch.setattr(wake, "mic_held", lambda: held["on"])

    def act():
        if what == "line_dropped":
            session_module._active = None
        else:
            held["on"] = True

    monkeypatch.setattr(session_module, "_active", session_module._active)   # restored after
    _during_walk(road, monkeypatch, act)
    _go(_someone_standing(road))
    assert road.steps is not None, "she set out"
    assert (fa.STATE["last"] or {}).get("why") == ("no_line" if what == "line_dropped" else "mic_held")
    assert road.said == [] and road.shook == []


def test_the_approach_holds_its_line_while_it_runs(road, monkeypatch):
    """`keep_line` from the dial to the stop: a summoned line nobody greeted would end after
    SUMMONED_ABANDON_S in the middle of a long look and walk."""
    kept = []
    line = session_module._active
    line.keep_line = lambda: kept.append(fa.STATE["phase"])
    _go(_someone_standing(road))
    assert road.steps is not None
    assert "walking" in kept and kept, "held while it walked"


def test_a_line_that_drops_before_the_wheels_turn_stops_it(road, monkeypatch):
    """Up when it was rung, gone by the last gates (a dropped call during the preflight): no walk."""
    c = _someone_standing(road)
    monkeypatch.setattr(session_module, "_active", session_module._active)   # restored after
    real = fa.asyncio.sleep
    did = [False]

    async def sleep(seconds):
        if fa.STATE["phase"] == "planning" and not did[0]:
            did[0] = True
            session_module._active = None
        await real(seconds)

    monkeypatch.setattr(fa.asyncio, "sleep", sleep)
    monkeypatch.setattr(fa, "LINE_WAIT_S", 0.2)
    _go(c)
    assert did[0] and road.steps is None
    assert (fa.STATE["last"] or {}).get("why") == "no_line"


# ---- the cone after the clusters (review 6 Nov) ------------------------------------------------

def _wall(x, y0, y1, step=0.01):
    ys = np.arange(y0, y1, step)
    return np.stack([np.full_like(ys, x), ys], axis=1)


def test_a_wall_ahead_is_not_cut_down_to_a_person_by_the_cone():
    """Cut to a 7 deg cone first, a wall 3 m ahead came out 0.73 m wide -- person-sized -- and the
    box road walked at it."""
    points = _wall(3.0, -2.0, 2.0)
    assert fa.find_person(points, 0.0, 0.6, 4.75, cone_deg=fa.CONE_DEG, nearest=True) is None


def test_somebody_in_front_of_the_wall_is_still_found():
    legs = np.array([[2.4, y] for y in np.arange(-0.16, -0.04, 0.01)] + [[2.4, y] for y in np.arange(0.04, 0.16, 0.01)])
    wall = _wall(3.0, -2.0, 2.0)
    # one return per beam: the legs hide the wall behind them
    edge = np.degrees(np.arctan2(0.17, 2.4))
    wall = wall[np.abs(np.degrees(np.arctan2(wall[:, 1], wall[:, 0]))) > edge]
    points = np.concatenate([wall, legs])
    found = fa.find_person(points, 0.0, 0.6, 4.75, cone_deg=fa.CONE_DEG, nearest=True)
    assert found is not None and abs(found.distance - 2.4) < 0.1


def test_somebody_at_the_cone_s_edge_is_found_whole():
    """Their middle just outside the cone, a leg inside: found (a walk's "moved", not "lost")."""
    legs = np.array([[3.0, y] for y in np.arange(0.36, 0.48, 0.01)] + [[3.0, y] for y in np.arange(0.56, 0.68, 0.01)])
    found = fa.find_person(legs, 0.0, 0.6, 4.75, cone_deg=7.0)
    assert found is not None and found.bearing > 7.0


# ---- one head scale (review 6 Nov) ---------------------------------------------------------------

def test_a_turned_head_reads_at_the_scale_it_aims_with(monkeypatch):
    """Aimed at 0.37 deg/% (`_pan_for`), read at the sweep's 0.23 (`lidar_bearing`): a face centred at
    pan 60 read 13.5 deg against 22.2, and the box road walked to whatever else stood in the cone."""
    monkeypatch.setattr(settings, "head_follow", True)
    monkeypatch.setattr(settings, "head_calibrate", False)
    monkeypatch.setattr(settings, "head_pan_full_deg", -94.44)
    monkeypatch.setattr(settings, "head_pan_max", 60)
    mapping = {"hfov_measured": 19.0, "mirrored": True}
    head = 60 / 100.0 * -94.44                      # what head_follow records at pan 60
    bearing = fa.lidar_bearing(512.0, 1024, head, mapping)
    assert bearing == pytest.approx(60 * fa.PAN_DEG_PER_PCT, abs=0.3)
    assert fa._pan_for(bearing) == 60
    # straight ahead it is the lens alone, as before
    assert fa.lidar_bearing(512.0, 1024, 0.0, mapping) == pytest.approx(0.0, abs=0.1)


def test_a_face_scan_in_progress_keeps_her_head_on_it(road, monkeypatch):
    c = _someone_standing(road)
    monkeypatch.setitem(greeter._holds, "face-approach", time.monotonic() + 30.0)
    assert fa._still_allowed(c) != "scanning", "her own hold on the door camera is not a scan"
    monkeypatch.setitem(greeter._holds, "face_scan", time.monotonic() + 30.0)
    assert fa._still_allowed(c) == "scanning"



# ---- the greeting at the stop: what changed while she got there (review 6 Nov, all-round) -------------

@pytest.mark.parametrize("what", ["muted", "door", "staff_off"])
@pytest.mark.parametrize("when", ["check", "announce"])
def test_what_changed_at_the_stop_keeps_her_greeting_back(road, monkeypatch, what, when):
    """`when=announce`: the change lands while the greeting waits for her voice to finish -- after the
    stop's check loop, which catches the door and staff off on its own polls. That window is only
    `_still_greetable` (the all-round review's mutation run: with `when=check` alone, dropping the
    door and staff-off terms from it stayed green)."""
    held = {"on": False}
    monkeypatch.setattr(wake, "mic_held", lambda: held["on"])

    def act():
        if what == "muted":
            held["on"] = True                            # a tap at the stop: the greeting would reopen the mic
        elif what == "door":
            fa.STATE["door_at"] = time.monotonic()       # the door camera greeted them first
        else:
            fa._turn_off("staff")

    if when == "check":
        _during_check(road, monkeypatch, act)
    else:
        from app import events

        fake = events.announce

        async def announce(text, **kw):
            if kw.get("source") == "face_approach":
                result = act()
                if asyncio.iscoroutine(result):
                    await result
            return await fake(text, **kw)

        monkeypatch.setattr(events, "announce", announce)
    _go(_someone_standing(road))
    assert road.said == [] and road.shook == []


def test_a_face_scan_at_the_stop_keeps_the_base_from_turning(road, monkeypatch):
    real = fa._settled

    async def settled():
        ok = await real()
        road.people = [(3.5, 0.35)]                       # shifted past the lens: a turn would follow
        greeter._holds["face_scan"] = time.monotonic() + 30.0
        return ok

    monkeypatch.setattr(fa, "_settled", settled)
    monkeypatch.setattr(greeter, "_holds", dict(greeter._holds))
    _go(_someone_standing(road))
    assert all(s.get("do") != "turn" for s in road.steps), road.steps
