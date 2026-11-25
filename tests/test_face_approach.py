"""Walking up to a scanned guest (app/face_approach.py). No robot: a fake board
with a little world in it, and the real event_walk over a fake board_walk, so the
stop paths under test are the real ones."""
from __future__ import annotations

import asyncio
import json
import math
import time
import types
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app import (consent, enrollment, event_host, event_walk, face_approach as fa, faces, facewatch, greeter,
                 guests, head_follow, robot_arm, robot_chassis, robot_face_link, session as session_module,
                 turnlog, wake, walk_frames)
from app.config import settings

SQUARE = [(-20.0, -20.0), (20.0, -20.0), (20.0, 20.0), (-20.0, 20.0)]
NAME = "คุณทดสอบ"


class World:
    """People and things on the floor; the board's answers come from here."""

    def __init__(self):
        self.pose = {"x": 0.0, "y": 0.0, "yaw": 0.0}
        self.people = [(3.5, 0.0)]    # world positions of a person's two legs, centred
        self.things: list[tuple[float, float]] = []
        self.quality = 80
        self.docking = "not_on_dock"
        self.action = None
        self.read_fails = 0
        self.walk_to = None
        self.arrive = True
        self.detour = 0.0
        self.freeze_after = None

    def scan(self) -> dict:
        # One return per beam, the nearest (a real scan: nothing is seen through
        # a leg). A wall all round, far enough to be nobody's business.
        bins = 1440
        ranges = [9.5] * bins
        x0, y0, yaw = self.pose["x"], self.pose["y"], self.pose["yaw"]

        def blob(wx, wy, radius):
            dx, dy = wx - x0, wy - y0
            r = math.hypot(dx, dy)
            mid = math.atan2(dy, dx) - yaw
            half = math.atan2(radius, r)
            for a in np.linspace(mid - half, mid + half, max(5, int(half * 2 / math.radians(0.25)))):
                i = int(((a + math.pi) % (2 * math.pi)) / (2 * math.pi) * bins) % bins
                ranges[i] = min(ranges[i], r)

        for wx, wy in self.people:
            blob(wx, wy - 0.1, 0.06)
            blob(wx, wy + 0.1, 0.06)
        for thing in self.things:
            blob(thing[0], thing[1], thing[2] if len(thing) > 2 else 0.1)
        pts = [{"angle": (i + 0.5) / bins * 2 * math.pi - math.pi, "distance": float(d), "valid": True}
               for i, d in enumerate(ranges)]
        return {"pose": dict(self.pose), "points": pts}


class FakeProc:
    """board_walk: rolls the pose to its goal, then exits 0. SIGTERM exits 2."""

    def __init__(self, world, goal):
        self.world, self.goal = world, goal
        self.returncode = None
        self._done = asyncio.Event()
        self.stdout = self
        self._said = False
        self.task = asyncio.create_task(self._roll())

    async def _roll(self):
        w = self.world
        start = (w.pose["x"], w.pose["y"])
        for i in range(1, 11):
            await asyncio.sleep(0.01)
            share = i / 10
            w.pose["x"] = start[0] + (self.goal[0] - start[0]) * share
            w.pose["y"] = start[1] + (self.goal[1] - start[1]) * share + w.detour * math.sin(share * math.pi)
        if w.arrive:
            self._finish(0)
        else:
            await asyncio.sleep(30)

    def _finish(self, code):
        if self.returncode is None:
            self.returncode = code
            self._done.set()

    async def readline(self):
        if not self._said:
            self._said = True      # board_walk's first word (event_walk's gate)
            return b'@@ {"started": true}\n'
        await self._done.wait()
        return b""

    async def wait(self):
        await self._done.wait()
        return self.returncode

    def terminate(self):
        self.task.cancel()
        self._finish(2)

    send_signal = lambda self, _sig: self.terminate()  # noqa: E731
    kill = terminate


@pytest.fixture
def world(monkeypatch, tmp_path):
    w = World()
    monkeypatch.setattr(enrollment, "ENROLLED", tmp_path / "enrolled")
    monkeypatch.setattr(settings, "face_approach", True)
    monkeypatch.setattr(settings, "event_walk_enabled", True)
    monkeypatch.setattr(settings, "event_file", str(tmp_path / "event.json"))
    monkeypatch.setattr(settings, "robot_camera_hfov_measured", 19.0)
    monkeypatch.setattr(settings, "robot_camera_mirrored", True)
    monkeypatch.setattr(settings, "robot_arm_motion_enabled", True)
    monkeypatch.setattr(settings, "face_group_greeting", True)
    (tmp_path / "event.json").write_text(json.dumps({"event": {}, "spots": {}, "scenes": []}), encoding="utf-8")
    monkeypatch.setattr(event_walk, "RUN_DIR", tmp_path / "run")
    monkeypatch.setattr(event_walk, "SCRIPT", tmp_path / "event.json")   # exists; never run
    event_walk.reset()
    event_host.STATE.update(scene=None, stop=0, since=None, seq=0)
    fa._save_area([list(c) for c in SQUARE])
    fa.STATE["staff_on"] = True
    event_walk.heartbeat()
    consent.record(NAME, by=NAME, version=consent.EVENT_GUEST, days=3)

    async def scan(max_points=0):
        if w.read_fails:
            w.read_fails -= 1
            raise OSError("board")
        return w.scan()

    async def pose():
        return dict(w.pose)

    async def quality():
        return w.quality

    async def power():
        return {"dockingStatus": w.docking}

    async def action():
        return w.action

    monkeypatch.setattr(robot_chassis, "configured", lambda: True)
    monkeypatch.setattr(robot_chassis, "laserscan", scan)
    monkeypatch.setattr(robot_chassis, "pose", pose)
    monkeypatch.setattr(robot_chassis, "localization_quality", quality)
    monkeypatch.setattr(robot_chassis, "power", power)
    monkeypatch.setattr(robot_chassis, "current_action", action)
    monkeypatch.setattr(robot_chassis, "run_fence", lambda: None)
    monkeypatch.setattr(robot_chassis, "desk_stopped_within", lambda s: False)
    monkeypatch.setattr(robot_chassis, "STATE", {"moving": False})
    monkeypatch.setattr(robot_face_link, "nav_activity", lambda session=None: "idle")
    monkeypatch.setattr(robot_face_link, "live_faces", lambda: ["face"])
    # the robot face's own line (`_line_ready`): a fake line is nobody's claim otherwise
    monkeypatch.setattr(robot_face_link, "is_robot_face", lambda s: s is not None)
    monkeypatch.setattr(robot_face_link, "server_walking", event_walk.walking)
    monkeypatch.setattr(greeter, "_app_latched", lambda link: False)
    monkeypatch.setattr(walk_frames, "base_moving", lambda: False)
    monkeypatch.setattr(head_follow, "moved_at", lambda: None)
    # A standing line that has heard nobody: not a conversation (greeter._in_conversation).
    line = types.SimpleNamespace(summoned=True, _greeting_turn_seen=False, _ever_heard=False,
                                 provider=object(), profile="event")
    monkeypatch.setattr(session_module, "_active", line)

    async def cancel():
        return None

    monkeypatch.setattr(robot_chassis, "cancel", cancel)

    async def spawn(route):
        doc = json.loads(route.read_text(encoding="utf-8"))
        step = doc["paths"][event_walk.PATH_NAME]["steps"][0]
        w.walk_to = step
        return FakeProc(w, step["to"])

    monkeypatch.setattr(event_walk, "_spawn", spawn)
    said: list[dict] = []
    shook: list[str] = []

    async def announce(text, *, source, still_relevant=None, summon=False, arm_greeting=False, **_):
        if still_relevant is not None and not still_relevant():
            return False
        said.append({"text": text, "source": source, "arm": arm_greeting, "walking": event_walk.walking()})
        return True

    async def greet(session=None):
        shook.append("hand")
        return True

    from app import events

    monkeypatch.setattr(events, "announce", announce)
    monkeypatch.setattr(robot_arm, "greet", greet)
    monkeypatch.setattr(fa, "PREFLIGHT_GAP_S", 0.01)
    monkeypatch.setattr(fa, "WATCH_EVERY_S", 0.01)
    monkeypatch.setattr(fa, "SETTLE_GAP_S", 0.01)
    monkeypatch.setattr(fa, "LOST_S", 0.03)
    monkeypatch.setattr(fa, "ARM_WAIT_S", 0.2)
    w.said, w.shook = said, shook
    yield w
    fa.reset()
    greeter.release("face-approach")
    event_walk.reset()


def candidate(**over) -> dict:
    # Straight ahead on a 1024 frame; 3.5 m reads ~140 px through the 19 deg lens.
    c = {"name": NAME, "meta": {"group": guests.GROUP, "source": "booth"}, "score": 0.72, "px": 140,
         "cx": 512.0, "w": 1024, "head": 0.0, "at": time.monotonic(), "faces": 3}
    c.update(over)
    return c


def go(c=None):
    c = c or candidate()
    fa._eyes["candidate"] = c
    asyncio.run(fa._approach(c))
    return c


def last_why():
    return (fa.STATE["last"] or {}).get("why")


# ---- the lens ----------------------------------------------------------------------------

def test_a_face_left_of_the_picture_is_right_of_the_robot_on_the_mirrored_lens(world):
    mapping = head_follow.camera_mapping()
    left_of_picture = fa.lidar_bearing(112.0, 1024, 0.0, mapping)
    assert left_of_picture < 0
    # 400 px at 54 px/deg
    assert abs(left_of_picture) == pytest.approx(400 / 54, abs=0.3)
    assert fa.lidar_bearing(512.0, 1024, 0.0, mapping) == pytest.approx(0.0, abs=1e-6)


def test_range_from_a_face_uses_the_measured_lens_not_the_assumed_one(world):
    assert fa.face_range_m(110, 1024, 19.0) == pytest.approx(4.45, abs=0.05)
    assert fa.true_hfov({"hfov_assumed": 70.0}) is None


# ---- the plan: no I/O ---------------------------------------------------------------------

def points(w):
    from app import robot_lanes as rl

    return rl.scan_points({"laser_points": w.scan()["points"]})


def test_the_goal_stops_a_metre_from_the_person_and_faces_them(world):
    p = fa.plan(points(world), world.pose, 0.0, 3.5, SQUARE)
    assert p["why"] is None
    assert p["goal"][0] == pytest.approx(3.5 - 1.0 - fa.HALF_LENGTH_M, abs=0.05)
    assert p["face_deg"] == pytest.approx(0.0, abs=2.0)
    step = fa.steps_for(p)[0]
    assert step["speed"] <= fa.MAX_SPEED and step["face"] == p["face_deg"]


def test_no_setting_can_stop_closer_or_walk_further_or_faster(world):
    world.people = [(4.0, 0.0)]
    p = fa.plan(points(world), world.pose, 0.0, 4.0, SQUARE, stop_m=0.1, max_m=99.0, speed=5.0)
    assert 4.0 - p["goal"][0] - fa.HALF_LENGTH_M >= fa.STOP_FLOOR_M - 0.05
    assert p["speed"] == fa.MAX_SPEED
    world.people = [(7.0, 0.0)]
    far = fa.plan(points(world), world.pose, 0.0, 7.0, SQUARE, max_m=99.0)
    assert far["why"] == "too_far"


def test_no_person_on_the_lidar_where_the_face_is_means_no_walk(world):
    world.people = []
    assert fa.plan(points(world), world.pose, 0.0, 3.5, SQUARE)["why"] == "no_lidar_target"
    world.people = [(3.5, 3.0)]       # somebody else, off to the side
    assert fa.plan(points(world), world.pose, 0.0, 3.5, SQUARE)["why"] == "no_lidar_target"


def test_a_table_leg_in_the_straight_corridor_blocks_the_walk(world):
    # The board's floor-1 map lacks the furniture: the lidar, now, is the check.
    world.things = [(1.5, 0.2)]
    p = fa.plan(points(world), world.pose, 0.0, 3.5, SQUARE)
    assert p["why"] == "path_blocked" and p["blocked_at_m"] == pytest.approx(1.5, abs=0.2)


def test_something_beside_the_corridor_does_not_block_it(world):
    world.things = [(1.5, 0.9)]
    assert fa.plan(points(world), world.pose, 0.0, 3.5, SQUARE)["why"] is None


def test_a_goal_the_board_would_refuse_is_not_sent(world):
    world.people = [(3.5, 0.0)]
    world.things = [(2.15, 0.575, 0.02)]     # beside the goal, outside the corridor
    assert fa.plan(points(world), world.pose, 0.0, 3.5, SQUARE)["why"] == "goal_tight"


def test_goal_or_line_outside_the_marked_area_is_refused(world):
    small = [(-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)]
    assert fa.plan(points(world), world.pose, 0.0, 3.5, small)["why"] == "outside_area"
    assert fa.plan(points(world), world.pose, 0.0, 3.5, [])["why"] == "no_area"
    # the goal inside, hard by the edge: the robot's half width would be over it
    tight = [(-1.0, -1.0), (2.3, -1.0), (2.3, 1.0), (-1.0, 1.0)]
    assert fa.plan(points(world), world.pose, 0.0, 3.5, tight)["why"] == "outside_area"


def test_somebody_within_reach_needs_no_walk(world):
    world.people = [(1.4, 0.0)]
    p = fa.plan(points(world), world.pose, 0.0, 1.4, SQUARE)
    assert p["why"] is None and p.get("close") is True


def test_no_room_to_turn_is_refused(world, monkeypatch):
    from app import robot_lanes as rl

    world.people = [(3.0, 1.5)]
    monkeypatch.setattr(rl, "turn_sweep_gap", lambda *a, **k: 0.01)
    bearing = math.degrees(math.atan2(1.5, 3.0))
    assert fa.plan(points(world), world.pose, bearing, math.hypot(3.0, 1.5), SQUARE)["why"] == "no_turn_room"


# ---- the happy road -----------------------------------------------------------------------

def test_it_walks_up_stops_then_says_the_name_then_offers_the_hand(world):
    go()
    assert world.walk_to is not None
    assert world.walk_to["to"][0] == pytest.approx(2.15, abs=0.1)
    assert len(world.said) == 1 and NAME in world.said[0]["text"]
    assert world.said[0]["walking"] is False           # fully stopped before a word
    assert world.said[0]["arm"] is False               # the hand comes after, on its own
    assert world.shook == ["hand"]
    assert fa.STATE["phase"] == "idle"


def test_the_name_goes_to_the_one_she_walked_up_to_not_everybody_in_frame(world):
    # FACE_GROUP_GREETING is on in the fixture (robot.env has it on); a crowd in
    # frame must not turn this greeting into "Hello everyone".
    go(candidate(faces=5))
    assert NAME in world.said[0]["text"]
    assert greeter.GROUP_GREETING_TH not in world.said[0]["text"]


def test_a_person_in_reach_is_greeted_without_a_step(world):
    world.people = [(1.4, 0.0)]
    go(candidate(px=350))
    assert world.walk_to is None
    assert len(world.said) == 1 and world.shook == ["hand"]


def test_after_greeting_she_stays_where_she_stopped(world):
    go()
    here = dict(world.pose)
    assert fa.STATE["phase"] == "idle" and event_walk.walking() is False
    assert math.hypot(here["x"] - 2.15, here["y"]) < 0.1


def test_one_attempt_per_person_whatever_came_of_it(world):
    world.arrive = False
    world.people = [(3.5, 0.0)]

    async def scenario():
        c = candidate()
        fa._eyes["candidate"] = c
        task = asyncio.create_task(fa._approach(c))
        await asyncio.sleep(0.2)
        world.people = [(3.5, 0.8)]    # they walked off: the walk ends
        await task

    asyncio.run(scenario())
    assert last_why() == "moved"
    world.walk_to = None
    world.people = [(3.5, 0.0)]
    world.pose.update(x=0.0, y=0.0)
    fa.STATE["last_any"] = -math.inf
    go()
    assert world.walk_to is None and last_why() == "person_cooldown"


# ---- every reason not to leave --------------------------------------------------------------

def _set(obj, key, value):
    def apply(monkeypatch, w):
        monkeypatch.setattr(obj, key, value)
    return apply


def _world(key, value):
    def apply(monkeypatch, w):
        setattr(w, key, value)
    return apply


def _revoked(monkeypatch, w):
    consent.revoke(NAME)


def _no_record(monkeypatch, w):
    consent.save({})


def _expired(monkeypatch, w):
    consent.record(NAME, by=NAME, version=consent.EVENT_GUEST, days=3,
                   now=datetime.now(timezone.utc) - timedelta(days=10))


def _page_quiet(monkeypatch, w):
    event_walk.STATE["heartbeat"] = time.monotonic() - 60


def _no_corners(monkeypatch, w):
    fa._save_area([])


def _cooling(monkeypatch, w):
    fa.STATE["by_name"][NAME] = time.monotonic()


def _gap(monkeypatch, w):
    fa.STATE["last_any"] = time.monotonic()


def _scene(monkeypatch, w):
    event_host.STATE["since"] = time.time()


def _talking(monkeypatch, w):
    monkeypatch.setattr(greeter, "_conversation_state", lambda s, now=None: "exchange")


def _fenced(monkeypatch, w):
    def fence():
        raise robot_chassis.Fenced("routes")
    monkeypatch.setattr(robot_chassis, "run_fence", fence)


def _stop_latched(monkeypatch, w):
    monkeypatch.setattr(greeter, "_app_latched", lambda link: True)


def _desk(monkeypatch, w):
    monkeypatch.setattr(robot_chassis, "desk_stopped_within", lambda s: True)


def _disarmed(monkeypatch, w):
    monkeypatch.setattr(robot_arm, "armed", lambda: False)


def _walking(monkeypatch, w):
    monkeypatch.setattr(event_walk, "walking", lambda: True)


def _nav(state):
    def apply(monkeypatch, w):
        monkeypatch.setattr(robot_face_link, "nav_activity", lambda session=None: state)
    return apply


def _board_down(monkeypatch, w):
    async def boom():
        raise OSError("board")
    monkeypatch.setattr(robot_chassis, "current_action", boom)


def _driving(monkeypatch, w):
    monkeypatch.setattr(robot_chassis, "STATE", {"moving": True})


REFUSALS = [
    ("off", _set(settings, "face_approach", False)),
    ("walk_off", _set(settings, "event_walk_enabled", False)),
    ("not_armed", _page_quiet),
    ("not_armed", lambda m, w: fa.STATE.update(staff_on=False)),
    ("no_area", _no_corners),
    ("not_calibrated", _set(settings, "robot_camera_hfov_measured", 0.0)),
    ("consent", _revoked),
    ("consent", _no_record),
    ("consent", _expired),
    ("person_cooldown", _cooling),
    ("gap", _gap),
    ("scene_recent", _scene),
    ("walking", _walking),
    ("walking", _nav("active")),
    ("walking", _nav("stale")),
    ("nav_unknown", _nav("unknown")),
    ("stop_latched", _stop_latched),
    ("desk_stopped", _desk),
    ("arm_locked", _disarmed),
    ("arm_locked", _set(settings, "robot_arm_motion_enabled", False)),
    ("conversation", _talking),
    ("no_chassis", _set(robot_chassis, "configured", lambda: False)),
    ("fenced", _fenced),
    ("driving", _driving),
    ("docked", _world("docking", "on_dock")),
    ("dock_unknown", _world("docking", None)),
    ("board_busy", _world("action", {"action_id": 1, "stage": "RUNNING", "state": {"status": 1}})),
    ("board_unreadable", _board_down),
    ("low_quality", _world("quality", 40)),
    ("low_quality", _world("quality", None)),
    ("no_lidar_target", _world("people", [])),
    ("path_blocked", _world("things", [(1.5, 0.3)])),
]


@pytest.mark.parametrize("why,setup", REFUSALS, ids=[r[0] + "-%d" % i for i, r in enumerate(REFUSALS)])
def test_it_does_not_leave_and_says_nothing(world, monkeypatch, why, setup):
    setup(monkeypatch, world)
    go()
    assert last_why() == why
    assert world.walk_to is None
    assert world.said == [] and world.shook == []
    assert NAME not in fa.STATE["by_name"] or why == "person_cooldown"   # no attempt spent


def test_a_person_on_their_way_past_is_not_chased(world, monkeypatch):
    scans = [0]
    real = world.scan

    def moving():
        scans[0] += 1
        world.people = [(3.5, 0.3 * scans[0])]
        return real()

    world.scan = moving
    go()
    assert last_why() in ("moving_target", "no_lidar_target")
    assert world.walk_to is None


def test_a_person_drifting_past_within_the_cone_is_called_moving(world):
    scans = [-1]
    real = world.scan

    def moving():
        scans[0] += 1
        world.people = [(3.5, 0.15 * scans[0])]
        return real()

    world.scan = moving
    go()
    assert last_why() == "moving_target" and world.walk_to is None


def test_somebody_who_left_the_picture_during_the_checks_is_not_walked_to(world, monkeypatch):
    real = fa._preflight

    async def and_they_left(c):
        p = await real(c)
        fa._eyes["candidate"] = None
        return p

    monkeypatch.setattr(fa, "_preflight", and_they_left)
    go()
    assert last_why() == "gone" and world.walk_to is None


# ---- every reason to stop on the way ---------------------------------------------------------

def _mid_walk(world, change, *, also=None):
    world.arrive = False

    async def scenario():
        c = candidate()
        fa._eyes["candidate"] = c
        task = asyncio.create_task(fa._approach(c))
        for _ in range(200):
            await asyncio.sleep(0.01)
            if event_walk.walking():
                break
        assert event_walk.walking()
        result = change()
        if asyncio.iscoroutine(result):
            await result
        await asyncio.wait_for(task, 5.0)

    asyncio.run(scenario())


def _assert_stopped(world, why):
    assert last_why() == why or (fa.STATE["last"] or {}).get("why") == why
    assert event_walk.walking() is False
    assert world.said == [] and world.shook == []
    run = event_walk.current()
    assert run is not None and run.state == "stopped"


@pytest.mark.parametrize("why,change", [
    ("moved", lambda w: setattr(w, "people", [(3.5, 1.0)])),
    ("lost_target", lambda w: setattr(w, "people", [])),
    ("low_quality", lambda w: setattr(w, "quality", 30)),
    ("staff_off", lambda w: fa.set_enabled(False)),
    ("staff_off", lambda w: event_walk.STATE.update(heartbeat=time.monotonic() - 60)),
    ("scene_changed", lambda w: event_host.STATE.update(seq=99)),
    ("board_unreadable", lambda w: setattr(w, "read_fails", 50)),
])
def test_the_walk_stops_when_its_reason_goes(world, why, change):
    _mid_walk(world, lambda: change(world))
    _assert_stopped(world, why)


def test_somebody_speaking_to_her_on_the_way_stops_the_walk(world, monkeypatch):
    def talk():
        monkeypatch.setattr(greeter, "_conversation_state", lambda s, now=None: "exchange")
    _mid_walk(world, talk)
    _assert_stopped(world, "conversation")


def test_somebody_mid_sentence_near_her_does_not_stop_the_walk(world, monkeypatch):
    """FACE_GREET_LAPSE_S's "pending" -- a voice near her, not yet answered -- is no reason to
    stop: in a crowd it is always true, and the person walked to was marked done (critic 6 Nov)."""
    calls = []

    def near():
        def state(s, now=None):
            calls.append(1)
            return "pending"
        monkeypatch.setattr(greeter, "_conversation_state", state)
    _mid_walk(world, near)
    assert calls, "the watch never asked"
    assert event_walk.current().stop_requested != "approach_conversation"


def test_a_planner_detour_stops_the_walk(world):
    # The map lacks a table, so the board goes round something it believes in
    # (or through a table it does not): a walk that leaves its line is not ours.
    world.detour = 0.8
    world.arrive = False
    go()
    assert last_why() == "off_line"
    assert world.said == []


@pytest.mark.parametrize("reason", ["voice", "staff", "desk", "control_lost", "face_gone"])
def test_every_existing_stop_path_stops_it_and_turns_it_off(world, reason):
    _mid_walk(world, lambda: event_walk.stop(reason))
    assert event_walk.walking() is False
    assert world.said == [] and world.shook == []
    assert fa.STATE["staff_on"] is False               # on again only by a person


def test_a_scene_wins_at_once_and_leaves_the_switch_on(world):
    _mid_walk(world, lambda: event_walk.stop("replaced"))
    assert event_walk.walking() is False
    assert world.said == []
    assert fa.STATE["staff_on"] is True


# ---- on arrival -----------------------------------------------------------------------------

def _arrive_then(world, monkeypatch, change):
    real = fa._settled

    async def settled():
        ok = await real()
        change()
        return ok

    monkeypatch.setattr(fa, "_settled", settled)
    go()


def test_nobody_there_on_arrival_no_name_is_said(world, monkeypatch):
    _arrive_then(world, monkeypatch, lambda: setattr(world, "people", []))
    assert last_why() == "lost_on_arrival" and world.said == [] and world.shook == []


def test_a_revocation_on_the_way_is_honoured_on_arrival(world, monkeypatch):
    _arrive_then(world, monkeypatch, lambda: consent.revoke(NAME))
    assert last_why() == "consent" and world.said == [] and world.shook == []


def test_a_scene_switched_on_arrival_takes_it(world, monkeypatch):
    _arrive_then(world, monkeypatch, lambda: event_host.STATE.update(seq=99))
    assert last_why() == "scene_changed" and world.said == []


def test_no_handshake_or_word_until_the_base_has_stopped(world, monkeypatch):
    async def never():
        return False

    monkeypatch.setattr(fa, "_settled", never)
    go()
    assert last_why() == "not_settled" and world.said == [] and world.shook == []


def test_the_hand_waits_for_the_arm_gate_and_gives_up_rather_than_reach_while_moving(world, monkeypatch):
    monkeypatch.setattr(robot_face_link, "server_walking", lambda: True)
    assert asyncio.run(fa._shake(event_host.STATE.get("seq"))) is False
    assert world.shook == []


def test_a_stop_latch_after_the_words_keeps_the_hand_back(world, monkeypatch):
    monkeypatch.setattr(greeter, "_app_latched", lambda link: True)
    assert asyncio.run(fa._shake(event_host.STATE.get("seq"))) is False


@pytest.mark.parametrize("what", ["staff_stop", "new_scene", "ceremony"])
def test_a_stop_or_a_scene_during_the_arm_wait_keeps_the_hand_back(world, monkeypatch, what):
    """Review 6 Nov: the arm waits seconds for walk_frames' motion hold. Staff STOP on the phone
    (no app latch), a new scene, or the ceremony in that wait still sent arm_greeting."""
    seq = event_host.STATE.get("seq")
    if what == "staff_stop":
        fa._turn_off("staff")
    elif what == "new_scene":
        monkeypatch.setitem(event_host.STATE, "seq", (seq or 0) + 1)
    else:
        monkeypatch.setattr(event_host, "ceremony_now", lambda: True)
    assert asyncio.run(fa._shake(seq)) is False
    assert world.shook == []


# ---- staff's switch ---------------------------------------------------------------------------

def test_staff_cannot_turn_it_on_without_an_area_or_the_walk_switch(world, monkeypatch):
    fa.STATE["staff_on"] = False
    fa._save_area([])
    assert fa.set_enabled(True)["error"] == "no_area"
    fa._save_area([list(c) for c in SQUARE])
    monkeypatch.setattr(settings, "event_walk_enabled", False)
    assert fa.set_enabled(True)["error"] == "walk_off"
    monkeypatch.setattr(settings, "event_walk_enabled", True)
    assert fa.set_enabled(True)["on"] is True


def test_the_page_going_quiet_turns_it_off_not_just_pauses_it(world):
    event_walk.STATE["heartbeat"] = time.monotonic() - 60
    asyncio.run(fa.tick())
    assert fa.STATE["staff_on"] is False
    event_walk.heartbeat()
    assert fa.armed() is False            # the phone back is not a person pressing on


def test_a_stop_on_a_scenes_walk_while_armed_also_turns_it_off(world):
    run = event_walk.Run("spot", "ที่")
    run.stop_requested, run.state, run.ended = "voice", "stopped", time.monotonic()
    event_walk.STATE["run"] = run
    fa.STATE["armed_at"] = run.ended - 5
    asyncio.run(fa.tick())
    assert fa.STATE["staff_on"] is False


def test_a_stop_from_before_it_was_armed_does_not_turn_it_off(world):
    run = event_walk.Run("spot", "ที่")
    run.stop_requested, run.state, run.ended = "voice", "stopped", time.monotonic() - 100
    event_walk.STATE["run"] = run
    fa.STATE["armed_at"] = time.monotonic()
    asyncio.run(fa.tick())
    assert fa.STATE["staff_on"] is True


def test_the_phone_page_never_carries_a_name(world):
    go()
    assert NAME not in json.dumps(fa.snapshot(), ensure_ascii=False)


def test_the_turn_log_never_carries_a_name(world, monkeypatch):
    rows = []
    monkeypatch.setattr(turnlog, "record", lambda event, **f: rows.append((event, f)))
    go()
    assert any(e == "face_approach" and f.get("phase") == "greeted" for e, f in rows)
    assert NAME not in json.dumps(rows, ensure_ascii=False, default=str)


def test_a_walk_to_a_guest_is_never_resumed(world):
    # "เดินต่อ" (and a restart's recover) carries a stopped walk on from its step.
    # Here that step is a place a guest stood, some time ago: a chase with extra steps.
    world.arrive = False
    _mid_walk(world, lambda: event_walk.stop("staff"))
    assert event_walk.current().can_resume() is False
    run = event_walk.Run(fa.SPOT, "x")
    run.state, run.reason, run.steps = "interrupted", "restarted", [{"to": [1, 1]}]
    assert run.can_resume() is False


def test_something_in_the_way_mid_walk_ends_the_walk_not_waits_it_out(world):
    def held():
        event_walk.current().held_at = [1.0, 0.0]
    _mid_walk(world, held)
    _assert_stopped(world, "path_blocked")


def test_a_stopped_walk_up_shows_on_the_staff_page_as_a_reason_not_a_crash(world):
    _mid_walk(world, lambda: setattr(world, "people", []))
    view = event_walk.snapshot()["run"]
    assert view["state"] == "stopped" and "แขก" in view["reason_th"]


def test_a_dry_run_works_it_out_and_moves_and_says_nothing(world, monkeypatch):
    monkeypatch.setattr(settings, "face_approach_dry_run", True)
    monkeypatch.setattr(settings, "event_walk_enabled", False)
    rows = []
    monkeypatch.setattr(turnlog, "record", lambda event, **f: rows.append((event, f)))
    fa.STATE["staff_on"] = False
    assert fa.set_enabled(True)["on"] is True          # no walking switch needed for this
    go()
    assert world.walk_to is None and world.said == [] and world.shook == []
    row = next(f for e, f in rows if e == "face_approach" and f.get("phase") == "would_walk")
    assert row["travel_m"] == pytest.approx(2.15, abs=0.1) and row["range_m"] == pytest.approx(3.5, abs=0.1)
    assert NAME not in json.dumps(rows, ensure_ascii=False, default=str)
    assert fa.snapshot()["last"]["travel_m"] == row["travel_m"]


def test_a_dry_run_can_measure_the_same_person_again_soon(world, monkeypatch):
    monkeypatch.setattr(settings, "face_approach_dry_run", True)
    go()
    left = fa.STATE["by_name"][NAME] + settings.face_approach_person_cooldown_s - time.monotonic()
    assert 0 < left <= fa.DRY_RUN_REPEAT_S


def test_with_the_walking_switch_off_only_a_dry_run_is_possible(world, monkeypatch):
    monkeypatch.setattr(settings, "event_walk_enabled", False)
    fa.STATE["staff_on"] = False
    assert fa.set_enabled(True)["error"] == "walk_off"


def test_after_a_refusal_it_does_not_ask_the_board_again_at_once(world):
    world.docking = "on_dock"

    async def scenario():
        fa._eyes["candidate"] = candidate()
        await fa.tick()
        await fa.STATE["task"]
        assert last_why() == "docked"
        first = fa.STATE["task"]
        fa._eyes["candidate"] = candidate()
        await fa.tick()
        assert fa.STATE["task"] is first

    asyncio.run(scenario())


def test_the_eyes_keep_looking_while_it_plans_and_stop_while_it_walks(world, monkeypatch):
    gallery = faces.Gallery([NAME], unit(1).reshape(1, -1), [{"group": guests.GROUP, "source": "booth"}])
    w = types.SimpleNamespace(gallery=gallery)
    monkeypatch.setattr(settings, "face_approach_every_s", 0.0)
    monkeypatch.setattr(facewatch, "_sharpness", lambda frame, box: 500.0)
    monkeypatch.setattr(facewatch, "_head_now", lambda: (0.0, 0.0))
    fa.STATE["phase"] = "planning"
    now = time.monotonic()
    for i in range(3):
        fa.observe(w, FRAME, [Face(600, unit(1))], now + i * 0.01)
    assert fa._eyes["candidate"] is not None
    fa._eyes["candidate"] = None
    fa.STATE["phase"] = "walking"
    for i in range(6):
        fa.observe(w, FRAME, [Face(600, unit(1))], now + 1 + i * 0.01)
    assert fa._eyes["candidate"] is None


def test_corners_come_from_the_robots_own_pose(world):
    fa._save_area([])
    world.pose.update(x=1.25, y=-2.5)
    assert asyncio.run(fa.add_corner())["corners"] == 1
    assert fa.area() == [(1.25, -2.5)]
    world.quality = 20
    assert asyncio.run(fa.add_corner())["error"] == "low_quality"
    assert asyncio.run(fa.add_corner(clear=True))["corners"] == 0


# ---- who: observe ------------------------------------------------------------------------------

class Face:
    def __init__(self, x, vec, width=140):
        self.bbox = (x, 100, x + width, 100 + width)
        self.kps = None
        self.vec = vec
        self.det_score = 0.9


def unit(seed):
    v = np.random.default_rng(seed).normal(size=512).astype(np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture
def eyes(world, monkeypatch):
    gallery = faces.Gallery(
        [NAME, "เพื่อนร่วมงาน"], np.stack([unit(1), unit(2)]),
        [{"group": guests.GROUP, "source": "booth"}, {"group": "staff"}])

    class W:
        pass

    w = W()
    w.gallery = gallery
    monkeypatch.setattr(settings, "face_approach_every_s", 0.0)
    monkeypatch.setattr(settings, "face_approach_frames", 3)
    monkeypatch.setattr(settings, "face_approach_threshold", 0.60)
    monkeypatch.setattr(facewatch, "_sharpness", lambda frame, box: 500.0)
    monkeypatch.setattr(facewatch, "_head_now", lambda: (0.0, 0.0))
    return w


FRAME = np.zeros((768, 1024, 3), np.uint8)


def test_a_guest_confirmed_on_their_own_track_with_others_in_frame(eyes):
    other = unit(50)
    for i in range(3):
        fa.observe(eyes, FRAME, [Face(100, other), Face(600, unit(1))], 10.0 + i)
    c = fa._eyes["candidate"]
    assert c is not None and c["name"] == NAME and c["faces"] == 2
    assert c["cx"] == pytest.approx(670.0)


def test_two_frames_are_not_enough(eyes):
    for i in range(2):
        fa.observe(eyes, FRAME, [Face(600, unit(1))], 10.0 + i)
    assert fa._eyes["candidate"] is None


def test_under_the_events_bar_is_nobody_even_past_the_doors(eyes, monkeypatch):
    # 0.55 passes FACE_THRESHOLD and not FACE_APPROACH_THRESHOLD.
    v = unit(1) * 0.55 + unit(77) * math.sqrt(1 - 0.55 ** 2)
    v = v / np.linalg.norm(v)
    for i in range(6):
        fa.observe(eyes, FRAME, [Face(600, v.astype(np.float32))], 10.0 + i)
    assert fa._eyes["candidate"] is None


def test_a_close_second_is_nobody(eyes, monkeypatch):
    v = unit(1) * 0.7 + unit(2) * 0.68
    v = (v / np.linalg.norm(v)).astype(np.float32)
    for i in range(6):
        fa.observe(eyes, FRAME, [Face(600, v)], 10.0 + i)
    assert fa._eyes["candidate"] is None


def test_staff_are_not_walked_up_to(eyes):
    for i in range(6):
        fa.observe(eyes, FRAME, [Face(600, unit(2))], 10.0 + i)
    assert fa._eyes["candidate"] is None


def test_a_face_too_small_is_not_judged(eyes):
    for i in range(6):
        fa.observe(eyes, FRAME, [Face(600, unit(1), width=60)], 10.0 + i)
    assert fa._eyes["candidate"] is None


def test_frames_taken_while_the_head_moved_are_not_used(eyes, monkeypatch):
    monkeypatch.setattr(head_follow, "moved_at", lambda: time.monotonic())
    for i in range(6):
        fa.observe(eyes, FRAME, [Face(600, unit(1))], 10.0 + i)
    assert fa._eyes["candidate"] is None


def test_at_most_the_configured_faces_are_described_per_look(eyes, monkeypatch):
    monkeypatch.setattr(settings, "face_approach_max_faces", 2)
    calls = []

    def describe(frame, face):
        calls.append(face)
        face.vec = unit(9)
        return face

    monkeypatch.setattr(faces, "describe", describe)
    fa.observe(eyes, FRAME, [Face(10 + i * 200, None) for i in range(5)], 10.0)
    assert len(calls) == 2


def test_nothing_is_described_unless_staff_have_it_on(eyes, monkeypatch):
    fa.STATE["staff_on"] = False
    monkeypatch.setattr(faces, "describe", lambda *a: pytest.fail("described"))
    fa.observe(eyes, FRAME, [Face(600, None)], 10.0)


def test_the_door_greeting_waits_while_she_is_on_her_way_or_a_guest_is_confirming(world):
    stranger = facewatch.Sighting(kind="stranger", name=None, score=0.2)
    assert fa.holds_greeting(stranger) is False
    fa._eyes["candidate"] = candidate()
    assert fa.holds_greeting(stranger) is True
    fa._eyes["candidate"] = None
    fa.STATE["phase"] = "walking"
    assert fa.holds_greeting(facewatch.Sighting(kind="known", name="x", score=0.8)) is True
    fa.STATE["phase"] = "idle"
    fa.STATE["staff_on"] = False
    fa._eyes["candidate"] = candidate()
    assert fa.holds_greeting(stranger) is False


def test_greeted_at_the_door_means_not_walked_up_to(world):
    fa.note_greeted(NAME)
    go()
    assert last_why() == "person_cooldown"


# ---- off by default ---------------------------------------------------------------------------

def test_off_the_watcher_never_touches_it(monkeypatch):
    monkeypatch.setattr(settings, "focus_enabled", False)
    monkeypatch.setattr(settings, "asd_enabled", False)
    monkeypatch.setattr(faces, "locate", lambda frame: [])
    monkeypatch.setattr(fa, "observe", lambda *a: pytest.fail("observed"))
    w = facewatch.Watcher(faces.Gallery([], np.zeros((0, 512), np.float32)))
    assert w.see(FRAME) is None


def test_off_no_task_starts(monkeypatch):
    async def scenario():
        fa.start()
        return fa.STATE.get("loop")

    assert asyncio.run(scenario()) is None


def test_off_the_staff_page_state_has_no_new_field():
    from fastapi.testclient import TestClient

    from app import event_control_server as ecs

    settings.event_control_key = "k" * 16
    client = TestClient(ecs.app, cookies={ecs.COOKIE: ecs._cookie_value()})
    assert "approach" not in client.get("/event/state").json()


def test_on_the_staff_page_shows_it_and_switches_it(world):
    from fastapi.testclient import TestClient

    from app import event_control_server as ecs

    settings.event_control_key = "k" * 16
    fa.STATE["staff_on"] = False
    client = TestClient(ecs.app, cookies={ecs.COOKIE: ecs._cookie_value()})
    assert client.get("/event/state").json()["approach"]["on"] is False
    assert client.post("/event/approach", json={"on": True}).json()["on"] is True
    client.get("/event/state")
    assert fa.armed() is True
    assert client.post("/event/approach", json={"on": "yes"}).json()["on"] is False   # only true is on


def test_no_key_no_way_to_switch_it(world):
    from fastapi.testclient import TestClient

    from app import event_control_server as ecs

    settings.event_control_key = "k" * 16
    fa.STATE["staff_on"] = False
    assert TestClient(ecs.app).post("/event/approach", json={"on": True}).status_code == 403
    assert fa.STATE["staff_on"] is False


def test_voice_cannot_start_it():
    # The walking tools are hidden from the event profile; this has no tool at all.
    source = (fa.__file__ and open(fa.__file__, encoding="utf-8").read())
    assert "@tool" not in source


# ---- the line, the face, a stop seen late (review 6 Nov) -------------------------------------------

def test_the_guest_road_rings_for_a_line_before_it_walks(world, monkeypatch):
    """The body, camera and guest roads walked with no call (no look first), and rang at the stop."""
    line = types.SimpleNamespace(summoned=True, _greeting_turn_seen=False, _ever_heard=False,
                                 provider=object(), profile="event", ears=[])
    line.open_ears = lambda why: line.ears.append((why, world.walk_to))
    monkeypatch.setattr(session_module, "_active", None)
    world.walk_to = None
    rung = []

    async def summon(reason="server"):
        rung.append((reason, world.walk_to))
        session_module._active = line
        return 1

    monkeypatch.setattr(wake, "summon", summon)
    go()
    assert rung[:1] == [("face_approach", None)] and world.walk_to is not None
    assert line.ears and line.ears[0] == ("face_approach", None), "hearing before the first step"


def test_each_gate_refuses_a_mic_hold_on_its_own(world, monkeypatch):
    """Two layers: every road's first gate, and the line asked for after the awaits (a hold can
    start in between)."""
    from app import wake as wake_module

    monkeypatch.setattr(wake_module, "mic_held", lambda: True)
    assert fa._still_allowed(candidate()) == "mic_held"
    assert asyncio.run(fa._line_up()) == "mic_held"


def test_the_guest_road_does_not_walk_under_a_mic_hold(world, monkeypatch):
    monkeypatch.setattr(wake, "mic_held", lambda: True)
    world.walk_to = None
    go()
    assert last_why() == "mic_held" and world.walk_to is None and world.said == []


def _no_candidates(monkeypatch):
    async def none(now):
        return None

    monkeypatch.setattr(fa, "_candidate", lambda now, name=None: None)
    monkeypatch.setattr(fa, "_person_candidate", lambda now: None)
    monkeypatch.setattr(fa, "_scan_candidate", none)


def test_the_robot_face_gone_with_no_walk_turns_it_off(world, monkeypatch):
    """Review 6 Nov: only a walk noticed the face going. When the app came back she walked up to
    people with nobody pressing on."""
    _no_candidates(monkeypatch)
    faces = {"up": ["face"]}
    monkeypatch.setattr(robot_face_link, "live_faces", lambda: faces["up"])
    monkeypatch.setattr(event_walk, "FACE_GRACE_S", 0.05)
    asyncio.run(fa.tick())
    assert fa.STATE["staff_on"] is True
    faces["up"] = []
    asyncio.run(fa.tick())
    assert fa.STATE["staff_on"] is True, "a moment's gap is not gone"
    time.sleep(0.1)
    asyncio.run(fa.tick())
    assert fa.STATE["staff_on"] is False


def test_a_human_stop_on_a_scene_walk_is_seen_once_its_walk_has_ended(world, monkeypatch):
    """Review 6 Nov: a stop waits for its walker; the first tick saw it with `ended` unset, marked it
    seen, and never looked again -- the feature stayed on after a person's stop."""
    _no_candidates(monkeypatch)
    run = types.SimpleNamespace(stop_requested="face_gone", ended=None)
    monkeypatch.setattr(event_walk, "current", lambda: run)
    asyncio.run(fa.tick())
    assert fa.STATE["staff_on"] is True
    run.ended = time.monotonic()
    asyncio.run(fa.tick())
    assert fa.STATE["staff_on"] is False


def test_b_on_the_robot_is_a_persons_stop():
    assert "app_stop" in fa.HUMAN_STOPS and event_walk.kind_of("app_stop") == "person"
