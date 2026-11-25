"""The Event page's "กลับแท่น" (owner 11 Nov 2026: "ขอปุ่มกลับแท่น"). That morning the debut walked a
metre straight off the floor-2 dock while the board held floor 1's map: a dock walk (MoveTo to the
dock on the map) would have driven for floor 1's dock. The way home after a walk ahead is the same
line backwards -- velocity only, no map, either floor.

What these hold down:
* board_walk's `forward` says the line it came out on (`@@ forward_line`, also when stopped part way);
  its `back_to` backs along it onto the dock with the dock's own back-in, from ON that line only
  (aside, heading, distance), stops on a pose jump, and passes no map gate;
* event_walk keeps the line (off the dock = new, from its end along it = longer, elsewhere =
  gone), forgets it once docked along it or after a walk that set out on the map, never resumes
  a way back, and lets a way home past the battery floor;
* event_host.go_home: the line = back along it (refused, in Thai, off it), none = the map's dock walk;
* the page: the button whenever she stands off the dock, two taps, saying which way it will go.
Nothing here moves a robot: fake bases and a fake walker.
"""
from __future__ import annotations

import asyncio
import json
import math
import time

import pytest

from app import event_host, event_walk
from app.config import settings
from tests.test_debut_forward import _Base, _forward_walker
from tests.test_event_host import _settle, _walker, event  # noqa: F401 -- fixtures, used by name
from tests.test_event_page_debut import EVENT_PAGE, _state, served  # noqa: F401 -- fixture
from tests.test_event_order_page import Page, chromium  # noqa: F401 -- the fixture, used by name


# ---- board_walk: the way back ----------------------------------------------------------

class _Docking(_Base):
    """A base that reaches the dock's contacts once it has backed to the dock pose (`dock` along its
    heading from where the test puts it)."""

    def __init__(self, out=1.0, aside=0.0, turn_deg=0.0, on_dock=False, **kw):
        h = math.radians(30)
        super().__init__(x=1.0 + out * math.cos(h) - aside * math.sin(h),
                         y=2.0 + out * math.sin(h) + aside * math.cos(h), h=h + math.radians(turn_deg), **kw)
        self.dock = (1.0, 2.0, h)
        self.docked = on_dock

    def power(self):
        dx, dy, h = self.dock
        if -0.03 <= (self.x - dx) * math.cos(h) + (self.y - dy) * math.sin(h) <= 0.005:
            self.docked = True                           # the contacts: not anywhere behind them
        return {"dockingStatus": "on_dock" if self.docked else "not_on_dock", "isCharging": self.docked}

    def move(self, dt):
        super().move(dt)
        self.power()


def _dock_deg(base):
    return [base.dock[0], base.dock[1], math.degrees(base.dock[2])]


def _backer(monkeypatch, base, events=True):
    bw, w, clock = _forward_walker(monkeypatch, base, {"events": events})
    return bw, w, clock


def test_back_to_backs_along_the_line_onto_the_dock(monkeypatch, capsys):
    base = _Docking(out=1.0)
    bw, w, clock = _backer(monkeypatch, base)
    w.step({"back_to": _dock_deg(base), "label": "ถอยกลับแท่น"}, 1)
    out = capsys.readouterr().out
    assert base.docked and base.vx == 0.0
    assert min(base.asked) < 0 and max(base.asked) <= 0.0      # backing, never ahead
    assert "ON THE DOCK" in out and '@@ {"docked": 1}' in out


@pytest.mark.parametrize("pose, why", [
    ({"out": 1.0, "aside": 0.2}, "aside"),                     # pushed sideways
    ({"out": 1.0, "turn_deg": 10.0}, "off"),                   # turned
    ({"out": 3.0}, "out"),                                     # further than the back-in's 40 s
    ({"out": -0.3}, "out"),                                    # behind the dock (another line)
])
def test_back_to_moves_only_from_on_its_line(monkeypatch, capsys, pose, why):
    base = _Docking(**pose)
    bw, w, clock = _backer(monkeypatch, base)
    with pytest.raises(bw.Stop, match="not on the line it came out on"):
        w.step({"back_to": _dock_deg(base)}, 1)
    assert base.asked == [], "nothing was driven"
    assert '@@ {"line_lost": 1}' in capsys.readouterr().out, "it does not describe where she is: gone"


def test_back_to_on_the_dock_already_moves_nothing(monkeypatch, capsys):
    base = _Docking(out=0.0, on_dock=True)
    bw, w, clock = _backer(monkeypatch, base)
    w.step({"back_to": _dock_deg(base)}, 1)
    assert base.asked == [] and '@@ {"docked": 1}' in capsys.readouterr().out


def test_a_pose_jump_while_backing_stops_it_where_it_is(monkeypatch, capsys):
    base = _Docking(out=1.0)
    bw, w, clock = _backer(monkeypatch, base)
    base.jump_at = clock.t + 2.0                         # the board relocalizes onto another map
    with pytest.raises(bw.Stop, match="pose jumped"):
        w.step({"back_to": _dock_deg(base)}, 1)
    assert base.vx == 0.0 and not base.docked
    assert w.b is not None and not isinstance(w.b, bw._JumpGuard), "the board is its own again"
    assert '@@ {"line_lost": 1}' in capsys.readouterr().out, "the server forgets a line in two frames"


def test_a_way_back_passes_no_map_gate_and_a_moveto_with_it_does():
    from tests.test_board_walk import _walk

    bw = _walk()
    assert not bw.needs_map([{"back_to": [0, 0, 0]}])
    assert not bw.needs_map([{"forward": 1.0}, {"back_to": [0, 0, 0]}, {"do": "wait", "s": 1}])
    assert bw.needs_map([{"back_to": [0, 0, 0]}, {"to": [1, 1]}])
    assert bw.needs_map([{"do": "dock"}])                # the map's dock walk keeps its gates


def test_forward_says_the_line_it_came_out_on(monkeypatch, capsys):
    base = _Docking(out=0.0, on_dock=True)
    bw, w, clock = _forward_walker(monkeypatch, base, {"events": True})
    w.step({"forward": 1.0, "speed": 0.15}, 1)
    lines = [json.loads(l[3:]) for l in capsys.readouterr().out.splitlines() if l.startswith("@@ ")]
    said = [d["forward_line"] for d in lines if "forward_line" in d]
    # first before a wheel turns (the way back needs only the dock pose: a SIGKILL 3 s after the red
    # stop can come before the end's), then where it ended
    assert len(said) == 2 and said[0]["to"] == said[0]["from"]
    line = said[-1]
    assert line["on_dock"] is True
    assert line["from"][:2] == [1.0, 2.0] and abs(line["from"][2] - 30.0) < 1e-6
    assert math.hypot(line["to"][0] - 1.0, line["to"][1] - 2.0) == pytest.approx(1.0, abs=0.03)


def test_forward_stopped_part_way_still_says_its_line(monkeypatch, capsys):
    base = _Docking(out=0.0, on_dock=True, stuck=True)  # caught on a chair's foot below the lidar
    bw, w, clock = _forward_walker(monkeypatch, base, {"events": True})
    with pytest.raises(bw.Stop):
        w.step({"forward": 1.0, "speed": 0.15}, 1)
    assert any('"forward_line"' in l for l in capsys.readouterr().out.splitlines())


def test_b_on_the_robot_stops_the_reverse(monkeypatch):
    """Review 11 Nov: B or the robot's own screen takes the velocity action -- VelocitySession took it
    up again and the reverse went on (forward always stopped on it)."""
    import app.slamware_sdk as sw

    base = _Docking(out=1.0)
    bw, w, clock = _backer(monkeypatch, base)
    calls = []

    class Taken:
        def __init__(self, sdk, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            base.vx = 0.0

        def set(self, vx, w_):
            base.vx = vx
            base.asked.append(vx)
            calls.append(vx)
            return len(calls) == 5 and vx != 0.0         # taken away at the fifth tick

    monkeypatch.setattr(sw, "VelocitySession", Taken)
    with pytest.raises(bw.Stop, match="taken away"):
        w.step({"back_to": _dock_deg(base)}, 1)
    assert base.vx == 0.0 and not base.docked
    assert calls[5:] in ([0.0], []), "nothing driven after it was taken"


def test_the_red_stop_mid_walk_still_says_the_line(monkeypatch, capsys):
    """The red button is SIGTERM -> KeyboardInterrupt: the line it got to is still said (review 11 Nov:
    only done and the walker's own halts said it)."""
    base = _Docking(out=0.0, on_dock=True)
    bw, w, clock = _forward_walker(monkeypatch, base, {"events": True})
    move, t_start = base.move, clock.t

    def interrupted(dt):
        move(dt)
        if clock.t - t_start > 3.0:
            raise KeyboardInterrupt

    clock.on_sleep = interrupted
    with pytest.raises(KeyboardInterrupt):
        w.step({"forward": 1.0, "speed": 0.15}, 1)
    lines = [json.loads(l[3:]) for l in capsys.readouterr().out.splitlines() if l.startswith("@@ ")]
    line = [d["forward_line"] for d in lines if "forward_line" in d][-1]
    assert line["on_dock"] is True and 0.05 < math.hypot(line["to"][0] - 1.0, line["to"][1] - 2.0) < 0.6


def test_a_jump_on_the_way_out_says_the_line_is_lost(monkeypatch, capsys):
    base = _Docking(out=0.0, on_dock=True)
    bw, w, clock = _forward_walker(monkeypatch, base, {"events": True})
    base.jump_at = clock.t + 2.0
    with pytest.raises(bw.Stop, match="pose jumped"):
        w.step({"forward": 1.0, "speed": 0.15}, 1)
    lines = [json.loads(l[3:]) for l in capsys.readouterr().out.splitlines() if l.startswith("@@ ")]
    # its start's word, then the line lost -- never an end in the jumped frame
    assert lines[-1] == {"line_lost": 1}
    assert all(d["forward_line"]["to"] == d["forward_line"]["from"] for d in lines if "forward_line" in d)


# ---- event_walk: the line kept -----------------------------------------------------------

@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(event_walk, "RUN_DIR", tmp_path / ".run")
    return tmp_path / ".run"


def test_off_the_dock_a_line_is_kept_and_a_walk_on_from_its_end_makes_it_longer(run_dir):
    event_walk._note_forward_line({"from": [0.0, 0.0, 90.0], "to": [0.0, 1.0, 90.0], "on_dock": True})
    assert event_walk.back_line()["dock"] == [0.0, 0.0, 90.0]
    assert event_walk.back_view() == {"out_m": 1.0}
    # pressed again: on from where it ended, along it
    event_walk._note_forward_line({"from": [0.01, 1.0, 90.5], "to": [0.0, 2.0, 90.0], "on_dock": False})
    assert event_walk.back_line()["end"] == [0.0, 2.0, 90.0] and event_walk.back_view() == {"out_m": 2.0}
    # from anywhere else (pushed, turned): no way back
    event_walk._note_forward_line({"from": [0.5, 2.0, 90.0], "to": [0.5, 3.0, 90.0], "on_dock": False})
    assert event_walk.back_line() is None


def test_a_line_from_a_turned_robot_is_not_joined(run_dir):
    event_walk._note_forward_line({"from": [0.0, 0.0, 90.0], "to": [0.0, 1.0, 90.0], "on_dock": True})
    event_walk._note_forward_line({"from": [0.0, 1.0, 100.0], "to": [-0.17, 1.98, 100.0], "on_dock": False})
    assert event_walk.back_line() is None


def test_an_old_or_broken_line_is_no_line(run_dir):
    event_walk._note_forward_line({"from": [0.0, 0.0, 90.0], "to": [0.0, 1.0, 90.0], "on_dock": True})
    doc = json.loads((run_dir / "back_line.json").read_text(encoding="utf-8"))
    doc["at"] = time.time() - event_walk.BACK_LINE_MAX_S - 1
    (run_dir / "back_line.json").write_text(json.dumps(doc), encoding="utf-8")
    assert event_walk.back_line() is None and not (run_dir / "back_line.json").exists()
    (run_dir / "back_line.json").write_text("{not json", encoding="utf-8")
    assert event_walk.back_line() is None
    event_walk._note_forward_line({"from": [0.0, "x", 90.0], "to": [0.0, 1.0, 90.0], "on_dock": True})
    assert event_walk.back_line() is None


def test_a_walk_ahead_from_anywhere_on_the_line_makes_it_longer(run_dir):
    """Review 11 Nov: the way back stopped half way, the debut pressed again from there -- still on it."""
    event_walk._note_forward_line({"from": [0.0, 0.0, 90.0], "to": [0.0, 1.0, 90.0], "on_dock": True})
    event_walk._note_forward_line({"from": [0.02, 0.5, 91.0], "to": [0.0, 1.5, 90.0], "on_dock": False})
    assert event_walk.back_line()["end"] == [0.0, 1.5, 90.0]


def test_a_line_longer_than_the_way_back_may_back_is_none(run_dir):
    event_walk._note_forward_line({"from": [0.0, 0.0, 90.0], "to": [0.0, 1.0, 90.0], "on_dock": True})
    event_walk._note_forward_line({"from": [0.0, 1.0, 90.0], "to": [0.0, 2.0, 90.0], "on_dock": False})
    assert event_walk.back_line() is not None
    event_walk._note_forward_line({"from": [0.0, 2.0, 90.0], "to": [0.0, 3.0, 90.0], "on_dock": False})
    assert event_walk.back_line() is None


def test_a_lost_line_and_a_server_start_forget_it(run_dir):
    event_walk._note_forward_line({"from": [0.0, 0.0, 90.0], "to": [0.0, 1.0, 90.0], "on_dock": True})
    run = event_walk.Run("forward", "ด้านหน้า")
    event_walk._on_line(run, {"line_lost": 1})
    assert event_walk.back_line() is None
    event_walk._note_forward_line({"from": [0.0, 0.0, 90.0], "to": [0.0, 1.0, 90.0], "on_dock": True})
    asyncio.run(event_walk.recover_after_restart())
    assert event_walk.back_line() is None, "carried while off, the board keeps a stale pose"


def test_docked_along_it_the_line_is_gone_and_any_other_walks_docked_word_is_not_it(run_dir):
    event_walk._note_forward_line({"from": [0.0, 0.0, 90.0], "to": [0.0, 1.0, 90.0], "on_dock": True})
    other = event_walk.Run("dock", "แท่น")
    other.steps = [{"do": "dock"}]
    event_walk._on_line(other, {"docked": 1})
    assert event_walk.back_line() is not None
    back = event_walk.Run(event_host.BACK_SPOT, event_host.BACK_NAME_TH)
    back.steps = [{"back_to": [0.0, 0.0, 90.0]}]
    event_walk._on_line(back, {"docked": 1})
    assert event_walk.back_line() is None


def test_a_way_back_is_never_resumed_and_the_phone_says_press_it_again(run_dir):
    run = event_walk.Run(event_host.BACK_SPOT, event_host.BACK_NAME_TH)
    run.steps, run.state, run.reason = [{"back_to": [0.0, 0.0, 0.0]}], "stopped", "staff"
    assert not run.can_resume()
    # no way back kept (a restart forgot it): the hint does not point at one (review 11 Nov)
    assert "กลับแท่น' อีกครั้ง" not in run.view()["next_th"]
    event_walk._note_forward_line({"from": [0.0, 0.0, 0.0], "to": [1.0, 0.0, 0.0], "on_dock": True})
    assert "กลับแท่น" in run.view()["next_th"] and "อีกครั้ง" in run.view()["next_th"]
    # a pose jump is the "lost" hint (push her onto the dock, set the pose), never "press it again"
    run.reason = "step 1 ถอยกลับแท่น: the board's pose jumped 0.60 m / 0° while backing -- stopped where it is"
    assert "ตั้งตำแหน่งที่แท่น" in run.view()["next_th"]


# ---- the walks themselves (a fake walker) ------------------------------------------------

FORWARD_OFF_DOCK = ("import json; print('@@ ' + json.dumps({'started': True}), flush=True); "
                    "print('@@ ' + json.dumps({'forward_line': {'from': [0.0, 0.0, 90.0], 'to': [0.0, 1.0, 90.0], "
                    "'on_dock': True}}), flush=True); print('@@ ' + json.dumps({'step': 1}), flush=True)")
DOCKS = ("import json; print('@@ ' + json.dumps({'started': True}), flush=True); "
         "print('@@ ' + json.dumps({'docked': 1}), flush=True); print('@@ ' + json.dumps({'step': 1}), flush=True)")
MAP_WALK = "import json; print('@@ ' + json.dumps({'started': True}), flush=True)"
REFUSED_AT_GATE = "import sys; print('STOP: localization quality 41 < 50', flush=True); sys.exit(2)"


def _routes(monkeypatch):
    """What each spawned walker was asked to walk."""
    seen = []
    spawn = event_walk._spawn

    async def keep(route):
        seen.append(json.loads(route.read_text(encoding="utf-8"))["paths"]["event"]["steps"])
        return await spawn(route)

    monkeypatch.setattr(event_walk, "_spawn", keep)
    return seen


def test_home_after_a_walk_ahead_backs_along_it(event, monkeypatch):  # noqa: F811
    _walker(monkeypatch, FORWARD_OFF_DOCK)
    event_walk._note_forward_line({"from": [0.0, 0.0, 90.0], "to": [0.0, 1.0, 90.0], "on_dock": True})
    _walker(monkeypatch, DOCKS)
    routes = _routes(monkeypatch)

    async def go():
        out = await event_host.go_home()
        assert await _settle(lambda: event_walk.current() is not None and event_walk.current().state == "arrived")
        return out

    out = asyncio.run(go())
    assert out["ok"] and out["way"] == "back"
    assert routes == [[{"back_to": [0.0, 0.0, 90.0], "label": "ถอยกลับแท่น"}]]
    assert event_walk.back_line() is None                 # docked along it


def test_home_with_no_line_is_the_maps_dock_walk(event, monkeypatch):  # noqa: F811
    _walker(monkeypatch, DOCKS)
    routes = _routes(monkeypatch)
    out = asyncio.run(event_host.go_home())
    assert out["way"] == "map"
    assert routes and routes[0][-1].get("do") == "dock"


def test_home_off_the_line_says_why_in_thai_and_moves_nothing(event, monkeypatch):  # noqa: F811
    from app import robot_chassis

    _walker(monkeypatch, DOCKS)
    routes = _routes(monkeypatch)
    event_walk._note_forward_line({"from": [0.0, 0.0, 90.0], "to": [0.0, 1.0, 90.0], "on_dock": True})

    async def pose():
        return {"x": 0.3, "y": 1.0, "yaw": math.radians(90.0)}           # pushed 30 cm aside

    monkeypatch.setattr(robot_chassis, "configured", lambda: True)
    monkeypatch.setattr(robot_chassis, "pose", pose)
    out = asyncio.run(event_host.go_home())
    assert out["ok"] is False and out["error"] == "off_the_line" and "เข็นหุ่นกลับแท่นเอง" in out["error_th"]
    assert routes == []
    # one rule: the line only while she stands on it -- the next press is the map's (the button says so)
    assert event_walk.back_line() is None
    assert asyncio.run(event_host.go_home())["way"] == "map"


def test_home_while_the_ceremony_app_is_on_is_refused(event, monkeypatch):  # noqa: F811
    monkeypatch.setattr(event_host, "ceremony_now", lambda: True)
    out = asyncio.run(event_host.go_home())
    assert out["ok"] is False and out["error"] == event_host.CEREMONY_ERROR


def test_a_way_back_is_a_way_home_past_the_battery_floor(event, monkeypatch):  # noqa: F811
    _walker(monkeypatch, DOCKS)
    monkeypatch.setattr(event_walk, "health_view", lambda: {"known": True, "board": True, "low_battery": True})
    event_walk._note_forward_line({"from": [0.0, 0.0, 90.0], "to": [0.0, 1.0, 90.0], "on_dock": True})
    out = asyncio.run(event_host.go_home())
    assert out["ok"], out


def test_a_map_walk_that_set_out_leaves_the_line_and_one_refused_at_its_gate_keeps_it(event, monkeypatch):  # noqa: F811
    event_walk._note_forward_line({"from": [0.0, 0.0, 90.0], "to": [0.0, 1.0, 90.0], "on_dock": True})
    _walker(monkeypatch, REFUSED_AT_GATE)

    async def go(spot):
        await event_host.go_spot(spot)
        assert await _settle(lambda: event_walk.current() is not None
                             and event_walk.current().state not in ("starting", "walking"))

    asyncio.run(go("reception"))
    assert event_walk.back_line() is not None, "refused at its own gate: never moved"
    _walker(monkeypatch, MAP_WALK)
    asyncio.run(go("reception"))
    assert event_walk.back_line() is None


def test_a_card_3_stop_keeps_its_own_advice_when_it_has_some(run_dir):
    """Second review 22 Nov: a walk ahead's "press the card again" overrode every kind -- for card 3 (a MoveTo, a
    turn, a walk ahead) a lost pose said "press again" (a MoveTo from a pose that just jumped). Lost / the phone /
    the face keep their own move; anything else is "press again, from the scene's first point"."""
    run = event_walk.Run("guest_front", "หน้าแขก")
    run.steps = [{"do": "undock"}, {"to": [-8.62, -4.2]}, {"do": "turn", "face": 180},
                 {"forward": 1.2, "meet_m": 0.7}]
    run.state = "stopped"
    run.reason = "step 4 เดินตรงไปหาแขก: the board's pose jumped 0.60 m / 0° in 0.3 s -- stopped where it is (0.20 of 1.20 m)"
    assert run.view()["kind"] == "lost" and "ตั้งตำแหน่งที่แท่น" in run.view()["next_th"]
    run.reason = "control_lost"
    assert run.view()["next_th"] == event_walk._NEXT_TH["control"][1]
    run.reason = "step 3: no room to turn 180° here: the arms would pass 0.01 m from something"
    assert "จุดแรกของฉาก" in run.view()["next_th"]


def test_card_3s_map_steps_leave_the_line_too(event, monkeypatch):  # noqa: F811
    """Review 22 Nov: card 3 is a MoveTo, a turn and a walk ahead. Stopped before its walk ahead said a line of its
    own, the debut's line was KEPT (one of its steps was a line step) and "กลับแท่น" offered the way back along
    it from somewhere else. Any map step in a walk that set out = the line is gone."""
    event_walk._note_forward_line({"from": [0.0, 0.0, 90.0], "to": [0.0, 1.0, 90.0], "on_dock": True})
    _walker(monkeypatch, MAP_WALK)

    async def go():
        await event_host.go_spot("guest_front")
        assert await _settle(lambda: event_walk.current() is not None
                             and event_walk.current().state not in ("starting", "walking"))

    asyncio.run(go())
    assert any("forward" in s for s in event_walk.current().steps)
    assert event_walk.back_line() is None

def test_the_endpoint_is_the_hosts(monkeypatch):
    from app import event_control_server as server

    src = (event_host.ROOT / "app" / "event_control_server.py").read_text(encoding="utf-8")
    assert '@app.post("/event/home")' in src and "event_host.go_home(stops_seen=seen, way=way)" in src
    assert callable(server.home)


# ---- the review of all of 11 Nov's changes -------------------------------------------------

def test_a_slow_read_is_not_a_person_stopping_the_reverse(monkeypatch):
    """VelocitySession.set says True for a gap past the watchdog too: only operation_fail (B / the
    robot's screen) is a person's stop -- a 1.1 s read ended a floor-1 dock as "a person stopped it"."""
    import app.slamware_sdk as sw

    base = _Docking(out=1.0)
    bw, w, clock = _backer(monkeypatch, base)
    calls = []

    class Gappy:
        last_renew = None

        def __init__(self, sdk, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            base.vx = 0.0

        def set(self, vx, w_):
            calls.append(vx)
            if len(calls) == 5:
                self.last_renew = "gap"                  # taken up again with a zero: ramp from standstill
                base.vx = 0.0
                return True
            base.vx = vx
            base.asked.append(vx)
            return False

    monkeypatch.setattr(sw, "VelocitySession", Gappy)
    w.step({"back_to": _dock_deg(base)}, 1)
    assert base.docked


def test_the_session_says_why_it_took_the_action_up_again():
    import app.slamware_sdk as sw

    class Sdk:
        def __init__(self):
            self.answers = []

        def call(self, name, args=None):
            if name == "velocitycontrol":
                return {"code": 1, "id": 7}
            return {"codestr": self.answers.pop(0) if self.answers else "done"}

    t = [0.0]
    sdk = Sdk()
    v = sw.VelocitySession(sdk, clock=lambda: t[0], sleep=lambda s: None)
    v.open()
    assert v.set(0.1, 0.0) is False and v.last_renew is None
    sdk.answers = ["operation_fail"]
    assert v.set(0.1, 0.0) is True and v.last_renew == "taken"
    t[0] += 2.0
    assert v.set(0.1, 0.0) is True and v.last_renew == "gap"


def test_home_does_only_the_way_the_button_showed(event, monkeypatch):  # noqa: F811
    _walker(monkeypatch, DOCKS)
    routes = _routes(monkeypatch)
    # confirmed "back", but the line is gone (a restart, 30 min): no map walk instead
    out = asyncio.run(event_host.go_home(way="back"))
    assert out["ok"] is False and out["error"] == "way_changed" and "ไม่ได้เดินตามแผนที่แทน" in out["error_th"]
    assert routes == []
    # confirmed "map", but there is a way back now: neither
    event_walk._note_forward_line({"from": [0.0, 0.0, 90.0], "to": [0.0, 1.0, 90.0], "on_dock": True})
    out = asyncio.run(event_host.go_home(way="map"))
    assert out["ok"] is False and out["error"] == "way_changed" and routes == []
    out = asyncio.run(event_host.go_home(way="back"))
    assert out["ok"] and out["way"] == "back"


def test_the_endpoint_wants_the_way():
    from fastapi.testclient import TestClient

    from app import event_control_server as server

    src = (event_host.ROOT / "app" / "event_control_server.py").read_text(encoding="utf-8")
    body = src[src.index('@app.post("/event/home")'):]
    body = body[:body.index("\n\n\n")]
    assert 'if way not in ("back", "map")' in body and "400" in body
    assert server.home is not None and TestClient is not None


def test_b_while_seating_backs_no_more(monkeypatch):
    """`_seat` stopped by B: `_stays_docked` opened a new session and reversed up to 3 s after it."""
    import app.slamware_sdk as sw

    base = _Docking(out=0.05)                             # just off the contacts
    bw, w, clock = _backer(monkeypatch, base)
    opened = []

    class Taken:
        last_renew = "taken"

        def __init__(self, sdk=None, *a, **k):
            opened.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            base.vx = 0.0

        def set(self, vx, w_):
            base.vx = 0.0
            return vx != 0.0                             # B: every non-zero send is taken away

    monkeypatch.setattr(sw, "VelocitySession", Taken)
    w._taken = False
    w._seat(Taken(), base.x, base.y)
    assert w._taken is True
    n = len(opened)
    ok, why = w._stays_docked(base.dock[0], base.dock[1], math.cos(base.dock[2]), math.sin(base.dock[2]), 0.04)
    assert (ok, why) == (False, bw.TAKEN_AWAY) and len(opened) == n, "no new session to reverse after B"


def test_an_unpowered_dock_is_backed_into_by_no_more_than_the_docks_own_overshoot(monkeypatch):
    """The dock pose is where it sat ON the contacts (measured): with no power to say "on_dock" (4 Nov) it does not
    stop at the estimated pose's 10 cm. Since 24 Nov (D1, the charger 22 cm deeper than its recorded pose and the
    board's pose flipping 14-22 cm on it) it goes on past the overshoot for the contacts at the slow speed, by no more
    than DOCK_SEEK_M asked of the wheels, then stops and does not try again -- the price of the seek: a dock with no
    power is pushed until the wheels stall or that cap."""
    base = _Docking(out=1.0)
    base.power = lambda: {"dockingStatus": "not_on_dock", "isCharging": False}
    bw, w, clock = _backer(monkeypatch, base)
    with pytest.raises(bw.Stop, match="past the dock pose"):
        w.step({"back_to": _dock_deg(base)}, 1)
    dx, dy, h = base.dock
    assert (base.x - dx) * math.cos(h) + (base.y - dy) * math.sin(h) > -(bw.OVERSHOOT_M + bw.DOCK_SEEK_M + 0.04)


# ---- the page (last: its browser fixture holds an event loop that asyncio.run cannot share) ------------------------------------------------------------------------

def _off_dock(back=None, docking="not_on_dock"):
    s = _state()
    s["walk"]["health"]["docking"] = docking
    s["walk"]["back"] = back
    s["walk"]["run"] = None
    return s


def test_the_page_offers_home_off_the_dock_two_taps_and_says_which_way(chromium, served):  # noqa: F811
    page = Page(chromium, _off_dock(back={"out_m": 1.0}), html=EVENT_PAGE, path="/event-control")
    try:
        assert page.visible("#homeBtn")
        assert page.text("#homeBtn") == "กลับแท่น — ถอยตามทางที่เดินออกมา (ไม่เกิน 1.0 ม.)"
        page.page.click("#homeBtn")
        page.page.wait_for_function("document.getElementById('homeBtn').innerText.indexOf('แตะอีกครั้ง') >= 0")
        assert "ดูข้างหลังหุ่น" in page.text("#homeBtn")      # she backs blind
        assert page.sent == [], "one tap sends nothing"
        page.page.wait_for_timeout(450)                    # a second, deliberate tap (TWO_TAP_MIN_MS)
        page.page.click("#homeBtn")
        for _ in range(40):
            if page.sent:
                break
            page.page.wait_for_timeout(50)
        assert page.sent and page.sent[0] == ("/event/home", {"way": "back"})
        page.redraw(_off_dock(back=None))
        page.page.wait_for_function("document.getElementById('homeBtn').innerText.indexOf('แผนที่') >= 0")
        assert page.text("#homeBtn") == "กลับแท่น (ตามแผนที่ ชั้น 1)"
        page.redraw(_off_dock(back={"out_m": 1.0}, docking="on_dock"))
        page.page.wait_for_function("document.getElementById('homeBtn').hidden")
    finally:
        page.close()


def test_something_right_behind_from_the_start_stops_it_in_seconds(monkeypatch):
    """Review 11 Nov: the dock's own back-in counts a stall from 5 cm backed -- a robot that cannot
    move at all would push what is behind it until the bumper or 40 s. The way back counts at once."""
    base = _Docking(out=1.0, stuck=True)
    bw, w, clock = _backer(monkeypatch, base)
    t0 = clock.t
    with pytest.raises(bw.Stop, match="wheels stopped"):
        w.step({"back_to": _dock_deg(base)}, 1)
    assert clock.t - t0 < 5.0 and base.vx == 0.0


def test_a_walk_from_past_the_lines_end_or_turned_is_not_joined(run_dir):
    """Pushed further out along it, or turned, then walked ahead: not the line the way back knows."""
    event_walk._note_forward_line({"from": [0.0, 0.0, 90.0], "to": [0.0, 1.0, 90.0], "on_dock": True})
    event_walk._note_forward_line({"from": [0.0, 1.5, 90.0], "to": [0.0, 2.0, 90.0], "on_dock": False})
    assert event_walk.back_line() is None
    event_walk._note_forward_line({"from": [0.0, 0.0, 90.0], "to": [0.0, 1.0, 90.0], "on_dock": True})
    event_walk._note_forward_line({"from": [0.0, 1.0, 100.0], "to": [-0.008, 1.05, 100.0], "on_dock": False})
    assert event_walk.back_line() is None


def test_a_back_in_that_missed_the_contacts_says_push_it_in(monkeypatch, tmp_path):
    """16 Nov, floor 2: the way back was spent (backed to where it set out, in front of the dock) and
    board_walk said "0.04 m past the dock POSE and no contact" -- read as "pose" the phone said
    "หุ่นไม่แน่ใจตำแหน่งตัวเอง" and pointed at 'ตั้งตำแหน่งที่แท่น'; and with no line left, at 'เดินต่อ'."""
    monkeypatch.setattr(event_walk, "RUN_DIR", tmp_path / ".run")
    said = ("step 1 ถอยกลับแท่น: not on the dock (0.04 m past the dock pose and no contact), "
            "at (-9.663, -7.967) -115.7°")
    assert event_walk.kind_of(said) == "dock"
    assert event_walk.kind_of("step 1: localization quality 31") == "lost"
    assert event_walk.kind_of("the lidar does not lie on this map at the board's pose (fit 0.15 < 0.20, Q41): x") == "lost"
    run = event_walk.Run(event_host.BACK_SPOT, event_host.BACK_NAME_TH)
    run.steps, run.state, run.done, run.reason = [{"back_to": [-9.68, -8.01, -116.0]}], "stopped", 0, said
    view = run.view()
    assert view["reason_th"] == "เข้าแท่นไม่สำเร็จ" and "ไม่แน่ใจตำแหน่ง" not in view["reason_th"]
    assert "เข็นหุ่นเข้าแท่นเอง" in view["next_th"] and "เดินต่อ" not in view["next_th"]

