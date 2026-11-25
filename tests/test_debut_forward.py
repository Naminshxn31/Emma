"""The debut walks ahead (owner 10 Nov, pressed on floor 2: "กดแล้วหุ่นไม่เห็นเดินไปด้านหน้าเลย ...
ให้พูดภาษาอังกฤษด้วย ... แบบ hello Im emmaaa").

What these hold down:
* board_walk's `forward` step: straight ahead from its own start pose, under our own velocity --
  the full distance, a guest in the strip ahead waited for (and then the end), a pose jump / a
  stall / a board event stops it where it is;
* a route of forward steps alone passes no map gate (floor 2 that morning: floor 1's map, Q48, a
  fit nobody passes) -- any MoveTo in it and the gates are as ever;
* the debut's `forward_m`: its spot not recorded = this walk, which is no arrival at the spot (not
  counted, not resumed), and a floor hazard on the line = no walk; recorded = the spot's walk;
* the line is English, always (the owner chose "อังกฤษอย่างเดียว").
"""
from __future__ import annotations

import asyncio
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

from app import event_host, event_walk
from app.config import settings
from tests.test_board_walk import _Clock, _walk
from tests.test_event_host import _settle, event, line_at_the_spot, said, stage  # noqa: F401 -- fixtures

ROOT = Path(__file__).resolve().parents[1]


# ---- board_walk: the step --------------------------------------------------------------

class _Base:
    """A base on a straight floor: velocity in, pose out (heading `h`); `wall` metres ahead of the
    lidar a row of returns across the strip (None = nothing), `free_at` the clock time it goes."""

    def __init__(self, x=1.0, y=2.0, h=math.radians(30), wall=None, free_at=None, stuck=False):
        self.x, self.y, self.h = x, y, h
        self.vx, self.wall, self.free_at, self.stuck = 0.0, wall, free_at, stuck
        self.queue: list = []
        self.jump_at: float | None = None
        self.clock = None
        self.asked: list[float] = []

    def pose(self):
        return (self.x, self.y, self.h)

    def events(self):
        out, self.queue = self.queue, []
        return out

    def quality(self):
        return 48

    def current(self):
        return None

    def travelled(self, x0, y0):
        return (self.x - x0) * math.cos(self.h) + (self.y - y0) * math.sin(self.h)

    def move(self, dt):
        if self.jump_at is not None and self.clock.t >= self.jump_at:
            self.x += 0.6                                # the board relocalized onto another map
            self.jump_at = None
        if self.free_at is not None and self.clock.t >= self.free_at:
            self.wall = None
        if not self.stuck:
            self.x += self.vx * dt * math.cos(self.h)
            self.y += self.vx * dt * math.sin(self.h)
            if self.wall is not None:
                self.wall -= self.vx * dt

    def scan(self):
        if self.wall is None:
            return np.zeros((0, 2))
        return np.array([[self.wall, y] for y in np.arange(-0.6, 0.61, 0.05)])


def _forward_walker(monkeypatch, base, doc=None):
    bw = _walk()
    clock = _Clock(on_sleep=base.move)
    base.clock = clock
    monkeypatch.setattr(bw, "time", clock)
    import app.slamware_sdk as sw

    class Session:
        def __init__(self, sdk, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            base.vx = 0.0

        def set(self, vx, w_):
            assert w_ == 0.0                             # straight: nothing turns it
            base.vx = vx
            base.asked.append(vx)
            return False

    monkeypatch.setattr(sw, "VelocitySession", Session)
    w = bw.Walker(base, doc or {"dock": {"pose": [0, 0, 0]}}, False, False)
    w.sdk = object()
    monkeypatch.setattr(w, "scan", base.scan)
    return bw, w, clock


def test_forward_goes_the_distance_along_its_own_heading(monkeypatch, capsys):
    base = _Base()
    bw, w, clock = _forward_walker(monkeypatch, base)
    w.step({"forward": 1.0, "speed": 0.15, "label": "เดินออกไปข้างหน้า"}, 1)
    assert 0.99 <= base.travelled(1.0, 2.0) <= 1.03
    assert max(base.asked) <= 0.15 + 1e-9 and base.vx == 0.0
    assert "forward 1.00 m: done" in capsys.readouterr().out
    # a step's own speed is capped, and so is the distance
    base = _Base()
    bw, w, clock = _forward_walker(monkeypatch, base)
    w.step({"forward": 9.0, "speed": 1.0}, 1)
    assert max(base.asked) <= bw.FORWARD_SPEED_MAX and base.travelled(1.0, 2.0) <= bw.FORWARD_MAX_M + 0.03


def test_forward_waits_for_a_guest_ahead_then_goes_on(monkeypatch, capsys):
    base = _Base(wall=0.70, free_at=1010.0)              # 0.40 m ahead of the front face for 10 s
    bw, w, clock = _forward_walker(monkeypatch, base, {"dock": {"pose": [0, 0, 0]}, "patience_s": 45.0,
                                                        "events": True})
    w.step({"forward": 1.0}, 2)
    out = capsys.readouterr().out
    assert '@@ {"held": 2' in out and '"moving": 2' in out
    assert 0.99 <= base.travelled(1.0, 2.0) <= 1.03 and clock.t > 1010.0


def test_forward_gives_up_on_a_guest_who_stays(monkeypatch):
    base = _Base(wall=0.70)
    bw, w, clock = _forward_walker(monkeypatch, base, {"dock": {"pose": [0, 0, 0]}, "patience_s": 45.0})
    with pytest.raises(bw.Stop, match="something 0.40 m ahead for 45 s"):
        w.step({"forward": 1.0}, 1)
    assert base.vx == 0.0 and base.travelled(1.0, 2.0) < 0.01


def test_forward_near_its_end_a_guest_ahead_is_the_end(monkeypatch, capsys):
    base = _Base(wall=1.75)                              # met with ~0.15 m to go
    bw, w, clock = _forward_walker(monkeypatch, base)
    w.step({"forward": 1.0}, 1)
    assert "short, something" in capsys.readouterr().out and base.travelled(1.0, 2.0) > 0.8


def test_forward_up_to_a_guest_ends_in_front_of_the_guest(monkeypatch, capsys):
    """Card 3 (owner 22 Nov: "หันหลังเดินไปจับมือกับแขก"): `meet_m` = whoever is in the strip nearer than that to
    the front face is where the walk was going -- it ends there, done, never held for the patience (the guest
    waits for her). Nobody = the whole way. Without meet_m nothing changes (the hold, the patience: above)."""
    base = _Base(wall=1.80)                              # 1.50 m ahead of the front face
    bw, w, clock = _forward_walker(monkeypatch, base, {"dock": {"pose": [0, 0, 0]}, "patience_s": 45.0,
                                                        "events": True})
    w.step({"forward": 1.2, "speed": 0.15, "meet_m": 0.7}, 3)
    out = capsys.readouterr().out
    gap = base.wall - 0.30
    assert "met someone" in out and '"held"' not in out and base.vx == 0.0
    assert 0.6 <= gap < 0.7 and 0.8 <= base.travelled(1.0, 2.0) <= 0.9 and clock.t - 1000.0 < 15.0
    # the guest already near at the start: the end at once, not 45 s of waiting
    base = _Base(wall=0.70)                              # 0.40 m ahead
    bw, w, clock = _forward_walker(monkeypatch, base, {"dock": {"pose": [0, 0, 0]}, "patience_s": 45.0})
    w.step({"forward": 1.2, "meet_m": 0.7}, 3)
    assert base.travelled(1.0, 2.0) < 0.01 and clock.t - 1000.0 < 2.0
    # nobody: the whole 1.2 m
    base = _Base()
    bw, w, clock = _forward_walker(monkeypatch, base)
    w.step({"forward": 1.2, "meet_m": 0.7}, 3)
    assert 1.19 <= base.travelled(1.0, 2.0) <= 1.23 and "forward 1.20 m: done" in capsys.readouterr().out
    # its bounds: never under the base's own hold, never over FORWARD_MEET_MAX_M
    for asked, wall, lo, hi in ((0.2, 1.80, bw.FORWARD_HOLD_M - 0.1, bw.FORWARD_HOLD_M),
                                (5.0, 2.40, bw.FORWARD_MEET_MAX_M - 0.1, bw.FORWARD_MEET_MAX_M)):
        base = _Base(wall=wall)
        bw, w, clock = _forward_walker(monkeypatch, base)
        w.step({"forward": 2.0, "meet_m": asked}, 3)
        assert lo <= base.wall - 0.30 < hi, (asked, base.wall)


@pytest.mark.parametrize("trouble,match", [
    ("jump", "pose jumped"),
    ("stall", "not moving"),
    ("bumper", "board event"),
])
def test_forward_stops_where_it_is(monkeypatch, trouble, match):
    base = _Base(stuck=trouble == "stall")
    bw, w, clock = _forward_walker(monkeypatch, base)
    if trouble == "jump":
        base.jump_at = 1003.0
    if trouble == "bumper":
        orig = base.move

        def bump(dt):
            orig(dt)
            if clock.t >= 1002.0 and not getattr(base, "bumped", False):
                base.bumped = True
                base.queue.append({"type": "BUMPER_TRIGGERED"})
        clock.on_sleep = bump
    with pytest.raises(bw.Stop, match=match):
        w.step({"forward": 1.0}, 1)
    assert base.vx == 0.0


def test_a_forward_only_route_passes_no_map_gate_and_any_moveto_does_not(monkeypatch):
    bw = _walk()

    class B:
        def quality(self):
            return 30

        def current(self):
            return None

        def explore_map(self):
            raise AssertionError("the lidar's fit was asked for a walk that does not use the map")

    doc = {"dock": {"pose": [0, 0, 0]}, "min_quality": 40, "min_fit": 0.20}
    w = bw.Walker(B(), doc, False, False)
    ran = []
    monkeypatch.setattr(w, "step", lambda s, i, through=False: ran.append(s))
    w.run([{"forward": 1.0}, {"do": "wait", "s": 1}], 1)
    assert len(ran) == 2
    with pytest.raises(bw.Stop, match="localization quality 30"):
        w.run([{"forward": 1.0}, {"to": [1.0, 0.0]}], 1)
    with pytest.raises(bw.Stop, match="localization quality 30"):
        w.run([{"do": "undock"}, {"forward": 1.0}], 1)  # the undock goes by the dock's pose
    assert bw.needs_map([]) and bw.needs_map([{"do": "wait", "s": 1}]) and not bw.needs_map([{"forward": 1}])
    # card 1's guests in an arc round one spot (25 Nov): a turn on the spot goes to no point on the map -- the guests
    # round her fill the lidar's arc (the simulator: fit 0.17); with a MoveTo, a glide or an undock: the gates as ever
    turn = {"do": "turn", "face": 100.0, "look": True}
    assert not bw.needs_map([turn]) and not bw.needs_map([turn, {"do": "wait", "s": 1}])
    # a turn that asks for the gates keeps them (the wave's turn out of the box: refused softly at a low fit)
    assert bw.needs_map([{"do": "turn", "face": 85.0, "map": True}])
    assert bw.needs_map([turn, {"to": [1.0, 0.0]}]) and bw.needs_map([turn, {"glide": [[1.0, 0.0]]}])
    assert bw.needs_map([{"do": "undock"}, turn])
    ran.clear()
    w.run([turn], 1)                                     # Q30: a turn alone still runs
    assert len(ran) == 1


def test_a_looking_turn_is_the_glides_turn_and_a_plain_one_is_as_it_was(monkeypatch):
    """Review 25 Nov ~11:20: the arc's turn between guests checked the arms' room once and refused at once -- a guest
    0.1 m nearer than planned ended the card. `look` turns by the glide's turn: looked ahead every tick, held."""
    bw = _walk()

    class B:
        def pose(self):
            return (1.0, 2.0, 0.5)

    w = bw.Walker.__new__(bw.Walker)
    w.b = B()
    calls, said = [], []
    monkeypatch.setattr(w, "_drop_events", lambda why: None, raising=False)
    monkeypatch.setattr(w, "_sdk", lambda: None, raising=False)
    monkeypatch.setattr(w, "_glide_turn", lambda v, i, label, yaw, tol: calls.append(("glide_turn", label, round(yaw, 4))),
                        raising=False)
    monkeypatch.setattr(w, "turn_to", lambda yaw, **kw: calls.append(("turn_to", round(yaw, 4))), raising=False)
    monkeypatch.setattr(w, "say", lambda text: said.append(text), raising=False)

    class V:
        def __init__(self, sdk):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    import app.slamware_sdk as sdk

    monkeypatch.setattr(sdk, "VelocitySession", V)
    w.step({"do": "turn", "face": 100.0, "look": True, "label": "หันหาแขก 2"}, 3)
    w.step({"do": "turn", "face": 100.0, "w_spin": 0.3}, 4)
    import math

    assert calls == [("glide_turn", "หันหาแขก 2", round(math.radians(100.0), 4)), ("turn_to", round(math.radians(100.0), 4))]
    assert said and said[0].startswith("step 3 หันหาแขก 2: facing")


def test_after_box_steps_are_gated_at_their_first_step_not_in_the_box(monkeypatch):
    """Review 25 Nov 04:5x: card 1's walk out and on to the corner in one walker -- the glide made the whole walker a map
    route, its Q / fit gates read in the box (not on the board's map: the walk out never had them). They run at the
    first `after_box` step instead: the walk out walks, and a failed gate there is a Stop after it (the card's
    corner_short: she waves where she stands)."""
    bw = _walk()
    q = {"v": 30}

    class B:
        def quality(self):
            return q["v"]

        def current(self):
            return None

        def explore_map(self):
            raise AssertionError("no fit asked: min_fit 0")

    doc = {"dock": {"pose": [0, 0, 0]}, "min_quality": 40}
    w = bw.Walker(B(), doc, False, False)
    ran = []
    monkeypatch.setattr(w, "step", lambda s, i, through=False: ran.append(s))
    route = [{"forward": 1.3, "short_ok": False}, {"glide": [[1.0, 0.0]], "after_box": True}]
    with pytest.raises(bw.Stop, match="after the walk out"):
        w.run(route, 1)                                  # Q 30: the walk out walked, the glide not
    assert ran == [route[0]]
    q["v"] = 60
    del ran[:]
    w.run(route, 1)
    assert ran == route
    with pytest.raises(bw.Stop, match="localization quality 30 < 40 --"):
        q["v"] = 30
        w.run([{"forward": 1.3}, {"glide": [[1.0, 0.0]]}], 1)   # not marked: the start's gate as ever


# ---- event_walk / event_host: the debut ----------------------------------------------

def test_a_walk_ahead_is_never_resumed():
    run = event_walk.Run(event_host.FORWARD_SPOT, event_host.FORWARD_NAME_TH)
    run.steps, run.state, run.done = [{"forward": 1.0}], "stopped", 0
    assert run.can_resume() is False
    run2 = event_walk.Run("reception", "x")
    run2.steps, run2.state, run2.done = [{"do": "undock"}, {"to": [0, 0]}], "stopped", 1
    assert run2.can_resume() is True


def _spawner(monkeypatch, routes, script):
    async def spawn(route):
        routes.append(json.loads(Path(route).read_text(encoding="utf-8")))
        return await asyncio.create_subprocess_exec(sys.executable, "-c", script, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT)

    monkeypatch.setattr(event_walk, "_spawn", spawn)
    monkeypatch.setattr(event_walk, "_faces", lambda: True)
    monkeypatch.setattr(event_walk, "_other_walker", lambda: False)
    monkeypatch.setattr(settings, "event_walk_enabled", True)


ARRIVES = "print('[ 0.1] step 1 done', flush=True); print('[ 0.2] path event done', flush=True)"


def _arrived(stage):
    return next((e[2] for e in stage["log"] if e[0] == "say" and e[1] == "event_arrived"), None)


def _at_the_dock(monkeypatch):
    """The floor-1 dock, away from the file's floor hazard: the pose read the walk ahead needs."""
    from app import robot_chassis

    async def pose():
        return {"x": -7.3, "y": -6.2, "yaw": 0.0}

    monkeypatch.setattr(robot_chassis, "configured", lambda: True)
    monkeypatch.setattr(robot_chassis, "pose", pose)


def test_the_debut_walks_a_metre_ahead_while_the_stage_is_not_recorded(event, stage, monkeypatch):  # noqa: F811
    line_at_the_spot(event)                              # the arrival's words, read below
    routes: list = []
    _spawner(monkeypatch, routes, ARRIVES)
    _at_the_dock(monkeypatch)
    monkeypatch.setattr(event_host, "_on_walk", lambda *a, **k: asyncio.sleep(0))

    async def go():
        out = await event_host.start_scene("debut")
        assert out["walk"]["ok"], out
        assert await _settle(lambda: _arrived(stage) is not None)
        return out

    out = asyncio.run(go())
    assert out["walk"]["run"]["spot"] == event_host.FORWARD_SPOT
    steps = routes[0]["paths"]["event"]["steps"]
    # the pageant's arm is out to the right (10 Nov's side pose): that side is watched too
    assert steps == [{"forward": 1.0, "speed": event_host.FORWARD_SPEED, "label": "เดินออกไปข้างหน้า",
                      "right_clear_m": event_host.PAGEANT_RIGHT_CLEAR_M}]
    arrived = _arrived(stage)
    assert "หุ่นเดินถึงด้านหน้าแล้ว" in arrived and "«Hello everyone! I'm Emmaaa!" in arrived
    assert "เป็นภาษาอังกฤษเสมอ" in arrived
    # a metre ahead is no arrival at the stage, and nothing on the spots file says so
    saved = json.loads(event_host.spots_path().read_text(encoding="utf-8")) if event_host.spots_path().exists() else {}
    assert event_host.FORWARD_SPOT not in (saved.get("spots") or {})
    assert "stage_centre" not in (saved.get("spots") or {})


def test_with_the_stage_recorded_the_debut_walks_there(event, stage, monkeypatch):  # noqa: F811
    line_at_the_spot(event)
    event_host.spots_path().write_text(json.dumps({"spots": {"stage_centre": {"pose": [-9.0, -3.0, 0.0]}}}),
                                       encoding="utf-8")
    event_host.reset()
    routes: list = []
    _spawner(monkeypatch, routes, ARRIVES)
    monkeypatch.setattr(event_host, "_on_walk", lambda *a, **k: asyncio.sleep(0))

    async def go():
        out = await event_host.start_scene("debut")
        assert await _settle(lambda: _arrived(stage) is not None)
        return out

    out = asyncio.run(go())
    assert out["walk"]["run"]["spot"] == "stage_centre" and "หุ่นเดินถึงกลางเวที" in _arrived(stage)
    assert not any("forward" in s for s in routes[0]["paths"]["event"]["steps"])


def test_a_floor_hazard_ahead_is_no_walk(event, stage, monkeypatch):  # noqa: F811
    from app import robot_chassis

    routes: list = []
    _spawner(monkeypatch, routes, ARRIVES)
    monkeypatch.setattr(robot_chassis, "configured", lambda: True)

    async def at_the_balloons():                          # floor 1's hazard at (-7.85, 0.8), r 0.75
        return {"x": -7.85, "y": -0.5, "yaw": math.pi / 2}

    monkeypatch.setattr(robot_chassis, "pose", at_the_balloons)
    out = asyncio.run(event_host.start_scene("debut"))
    assert out["walk"]["ok"] is False and out["walk"]["error"] == "hazard" and routes == []
    assert "ที่เลเซอร์มองไม่เห็น" in out["walk"]["error_th"]

    async def elsewhere():
        return {"x": -7.3, "y": -6.2, "yaw": 0.0}

    monkeypatch.setattr(robot_chassis, "pose", elsewhere)
    monkeypatch.setattr(event_host, "_on_walk", lambda *a, **k: asyncio.sleep(0))
    out = asyncio.run(event_host.start_scene("debut"))
    assert out["walk"]["ok"] is True and len(routes) == 1


def test_forward_m_is_a_distance_or_nothing():
    doc = {"spots": {"s": {"name_th": "s", "pose": None}},
           "scenes": [{"id": "a", "spot": "s", "forward_m": 1.0}, {"id": "b", "spot": "s", "forward_m": 5},
                      {"id": "c", "spot": "s", "forward_m": True}, {"id": "d", "spot": "s", "forward_m": "1"},
                      {"id": "e", "spot": "s", "forward_m": -1}, {"id": "f", "spot": "s"}]}
    got = {s["id"]: s["forward_m"] for s in event_host._normalise(doc, {})["scenes"]}
    assert got == {"a": 1.0, "b": None, "c": None, "d": None, "e": None, "f": None}


def test_walking_off_the_debut_is_still_acted_where_she_stands(event, said):  # noqa: F811
    assert settings.event_walk_enabled is False            # the robot today
    out = asyncio.run(event_host.start_scene("debut"))
    assert out["walk"]["ok"] is False and out["walk"]["error"] == "disabled"


def test_the_tracked_debut_is_english_and_walks_a_metre():
    doc = json.loads((ROOT / "data" / "events" / "floor1_event.json").read_text(encoding="utf-8"))
    debut = next(s for s in doc["scenes"] if s["id"] == "debut")
    assert debut["forward_m"] == 1.3                       # owner 24 Nov: the box is 90 cm (was a guess of 1.0)
    assert "«Hello everyone! I'm Emmaaa!" in debut["on_arrive_th"] and "ภาษาอังกฤษเสมอ" in debut["on_arrive_th"]
    assert "ภาษาไทยเสมอ" not in debut["on_arrive_th"]
    quoted = debut["on_arrive_th"].split("«")[1].split("»")[0]
    assert not any(ch.isdigit() for ch in quoted)          # no number in her mouth


# ---- review 10 Nov ----------------------------------------------------------------------

def test_forward_stops_when_what_was_asked_outruns_the_pose(monkeypatch):
    """Corrections under the jump bar sliding the pose back: the distance would have been the
    pose's alone (9-10 cm corrections on these maps)."""
    base = _Base()
    bw, w, clock = _forward_walker(monkeypatch, base)
    real = base.move

    def slides_back(dt):
        real(dt)
        base.x -= 0.6 * base.vx * dt * math.cos(base.h)  # the pose keeps 40% of the driving
        base.y -= 0.6 * base.vx * dt * math.sin(base.h)
    clock.on_sleep = slides_back
    with pytest.raises(bw.Stop, match="asked for 1.3"):   # 1.0 + max(0.25, 0.30 x 1.0)
        w.step({"forward": 1.0}, 1)
    assert base.vx == 0.0


def test_forward_near_its_end_when_asked_outruns_the_pose_is_there(monkeypatch, capsys):
    """25 Nov 02:18 on the robot: the walk out of the box stopped "asked for 1.56 m and the pose says 1.28
    (1.28 of 1.30 m)" and the whole opening ended. Within FORWARD_NEAR_DONE_M of the end the step stops
    all the same, as done; further back it is still a Stop (the test above)."""
    base = _Base()
    bw, w, clock = _forward_walker(monkeypatch, base)
    real = base.move

    def slides_a_little(dt):
        real(dt)
        base.x -= 0.255 * base.vx * dt * math.cos(base.h)  # the pose keeps 74.5%: asked passes 1.30 m near 0.97
        base.y -= 0.255 * base.vx * dt * math.sin(base.h)
    clock.on_sleep = slides_a_little
    events: list = []
    monkeypatch.setattr(w, "event", lambda **k: events.append(k))
    w.step({"forward": 1.0, "speed": 0.15}, 1)             # no Stop
    assert "the last creep, near enough" in capsys.readouterr().out
    assert [e for e in events if "near_done" in e] and events[-1].get("forward_line", {}).get("end") is True
    assert 1.0 - bw.FORWARD_NEAR_DONE_M <= base.travelled(1.0, 2.0) < 1.0 - bw.FORWARD_END_TOL_M
    assert base.vx == 0.0


def test_forward_stops_when_the_velocity_action_is_taken_away(monkeypatch):
    """B / the robot's screen DELETE the board's action: VelocitySession takes it up again and
    would drive on."""
    base = _Base()
    bw, w, clock = _forward_walker(monkeypatch, base)
    import app.slamware_sdk as sw

    real = sw.VelocitySession

    class TakenAway(real):
        n = 0

        def set(self, vx, w_):
            TakenAway.n += 1
            super().set(vx, w_)
            return TakenAway.n == 6                       # renewed: a zero went

    monkeypatch.setattr(sw, "VelocitySession", TakenAway)
    with pytest.raises(bw.Stop, match="taken away"):
        w.step({"forward": 1.0}, 1)
    assert base.vx == 0.0 and base.travelled(1.0, 2.0) < 0.2


def test_a_slow_read_is_not_a_pose_jump(monkeypatch, capsys):
    """A 1 s hiccup between two reads at 0.15 m/s is 15 cm of real driving."""
    base = _Base()
    bw, w, clock = _forward_walker(monkeypatch, base)
    reads = {"n": 0}

    def slow_scan():
        reads["n"] += 1
        if reads["n"] == 8:                               # the base keeps rolling through the hiccup
            clock.t += 1.0
            base.move(1.0)
        return base.scan()

    monkeypatch.setattr(w, "scan", slow_scan)
    w.step({"forward": 1.0}, 1)
    assert "forward 1.00 m: done" in capsys.readouterr().out


def test_the_last_centimetres_are_watched_too(monkeypatch):
    """At the end speed (0.06) a frozen pose left nothing but the time cap."""
    base = _Base()
    bw, w, clock = _forward_walker(monkeypatch, base)
    real = base.move

    def freezes(dt):
        if base.travelled(1.0, 2.0) < 0.90:
            real(dt)
    clock.on_sleep = freezes
    with pytest.raises(bw.Stop, match="not moving"):
        w.step({"forward": 1.0}, 1)


def test_held_time_is_patiences_not_the_drivings(monkeypatch):
    base = _Base(wall=0.70, free_at=1040.0)              # a guest for 40 s of the 45 s patience
    bw, w, clock = _forward_walker(monkeypatch, base, {"dock": {"pose": [0, 0, 0]}, "patience_s": 45.0})
    w.step({"forward": 1.0}, 1)
    assert 0.99 <= base.travelled(1.0, 2.0) <= 1.03


def test_a_fallback_keeps_the_map_gates():
    bw = _walk()
    assert bw.needs_map([{"forward": 1.0, "fallback": [{"to": [1.0, 0.0]}]}])


def test_walking_off_reads_no_pose_and_an_unreadable_pose_is_no_walk(event, stage, monkeypatch):  # noqa: F811
    from app import robot_chassis

    reads = []

    async def pose():
        reads.append(1)
        raise TimeoutError

    monkeypatch.setattr(robot_chassis, "configured", lambda: True)
    monkeypatch.setattr(robot_chassis, "pose", pose)
    out = asyncio.run(event_host.start_scene("debut"))           # walking off, as on the robot today
    assert out["walk"]["error"] == "disabled" and reads == []
    routes: list = []
    _spawner(monkeypatch, routes, ARRIVES)                       # walking on
    out = asyncio.run(event_host.start_scene("debut"))
    assert out["walk"]["ok"] is False and out["walk"]["error"] == "no_pose" and routes == [] and reads == [1]
    async def readable():                                        # a pose there IS, but no chassis
        return {"x": -7.3, "y": -6.2, "yaw": 0.0}            # configured to say it is the board's

    monkeypatch.setattr(robot_chassis, "pose", readable)
    monkeypatch.setattr(robot_chassis, "configured", lambda: False)
    out = asyncio.run(event_host.start_scene("debut"))
    assert out["walk"]["error"] == "no_pose" and routes == []


def test_the_phone_points_at_the_tile_not_at_a_resume_button():
    run = event_walk.Run(event_host.FORWARD_SPOT, event_host.FORWARD_NAME_TH)
    run.steps, run.state, run.done = [{"forward": 1.0}], "stopped", 0
    run.reason = "step 1 เดินออกไปข้างหน้า: something 0.40 m ahead for 45 s (0.10 of 1.00 m)"
    view = run.view()
    assert view["kind"] == "blocked" and view["can_resume"] is False
    assert "เดินต่อ" not in view["next_th"] and "กดฉากนี้อีกครั้ง" in view["next_th"]
    assert event_walk.kind_of("step 1 x: the board's velocity action was taken away (B ...) -- stopped") == "person"
    assert event_walk.kind_of("step 1 x: 23 s of driving and not there (0.5 of 1.0 m)") == "blocked"
    other = event_walk.Run("reception", "x")
    other.steps, other.state, other.done, other.reason = [{"to": [0, 0]}], "stopped", 0, run.reason
    assert "เดินต่อ" in other.view()["next_th"]


def test_the_arms_side_is_watched_when_asked(monkeypatch, capsys):
    """Review 10 Nov: the pageant's arm is out to the robot's right; a guest just right of the line
    was never "ahead". With right_clear_m the walk holds for returns there, and goes on once clear."""
    base = _Base()
    bw, w, clock = _forward_walker(monkeypatch, base, {"dock": {"pose": [0, 0, 0]}, "patience_s": 45.0,
                                                        "events": True})
    beside = np.array([[0.1 + 0.02 * k, -0.52] for k in range(6)])        # 0.16 m past the right edge

    def scan():
        return beside if clock.t < 1008.0 else np.zeros((0, 2))

    monkeypatch.setattr(w, "scan", scan)
    w.step({"forward": 1.0, "right_clear_m": 0.30}, 3)
    out = capsys.readouterr().out
    assert '@@ {"held": 3' in out and clock.t > 1008.0 and 0.99 <= base.travelled(1.0, 2.0) <= 1.03
    # the same returns, no right_clear_m: not the walk's business (the left side never is)
    base = _Base()
    bw, w, clock = _forward_walker(monkeypatch, base)
    monkeypatch.setattr(w, "scan", lambda: beside)
    w.step({"forward": 1.0}, 1)
    assert clock.t < 1015.0
    left_side = np.array([[0.1 + 0.02 * k, 0.52] for k in range(6)])
    assert not bw.right_side_hit(left_side, 0.30, 0.8) and bw.right_side_hit(beside, 0.30, 0.8)
    assert not bw.right_side_hit(beside, 0.10, 0.8)                         # past the asked width


def test_a_guest_who_stays_on_the_arms_side_ends_the_walk(monkeypatch):
    base = _Base()
    bw, w, clock = _forward_walker(monkeypatch, base, {"dock": {"pose": [0, 0, 0]}, "patience_s": 45.0})
    beside = np.array([[0.1 + 0.02 * k, -0.50] for k in range(6)])
    monkeypatch.setattr(w, "scan", lambda: beside)
    with pytest.raises(bw.Stop, match="on the right, where the arm is"):
        w.step({"forward": 1.0, "right_clear_m": 0.30}, 1)
    assert base.vx == 0.0


def test_the_box_takes_a_walk_out_as_out_as_near_as_the_walker_takes_it_as_done():
    """Review 25 Nov 02:3x: the walker ended 3-5 cm short as done, the box (0.03) called it "debut_short"."""
    from app import event_host

    assert event_host.WALKED_OUT_TOL_M >= _walk().FORWARD_NEAR_DONE_M


def test_the_walk_out_of_the_box_at_tonights_ratio_finishes_on_its_own(monkeypatch, capsys):
    """25 Nov 02:18: the pose kept ~82% of what was asked on the 1.30 m walk out. With the share-of-distance margin it
    is no longer at the trigger: done at its distance, not "near enough", not a Stop."""
    base = _Base()
    bw, w, clock = _forward_walker(monkeypatch, base)
    real = base.move

    def keeps_82(dt):
        real(dt)
        base.x -= 0.18 * base.vx * dt * math.cos(base.h)
        base.y -= 0.18 * base.vx * dt * math.sin(base.h)
    clock.on_sleep = keeps_82
    w.step({"forward": 1.3, "speed": 0.15}, 1)
    assert "forward 1.30 m: done" in capsys.readouterr().out
    assert base.travelled(1.0, 2.0) >= 1.3 - bw.FORWARD_END_TOL_M
