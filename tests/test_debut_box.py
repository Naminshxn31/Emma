"""Card 1's debut out of the box (owner 23 Nov 2026: "แล้วก็หุ่นอยู่ในกล่องอาจจะต้องให้หุ่นเดินออกมาก่อน จึงค่อยยกมือนะ").

She starts INSIDE a box (the ceremony's opening: the box opens, she comes out). The scene's `walk_out_of_box`
(app/event_host.py): the walk out goes with the arms DOWN -- rested before the wheels, no pageant on the way, no
`right_clear_m` (the box's side is beside her) -- and only once it ARRIVED and the wheels stand still the hand comes
up (the standing pageant), the recorded line after it, the hand down after the line; then card 1's tour goes on.
Not out -- the walk held by a box door not open all the way and ended on its patience, walking off, a walk refused,
the wheels not seen still -- = no arm of any kind (the pageant's arm swings out sideways into the box), no tour, and
the card says why. Held inside the box, she asks nobody for the way.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

from app import event_host, event_pageant, event_tour, event_walk, robot_chassis
from app.config import settings
from tests.test_event_host import _settle, event, stage  # noqa: F401 -- fixtures, used by name
from tests.test_event_pageant import STARTED_THEN_ARRIVES, robot  # noqa: F401 -- fixture, used by name


def _line(m: float, end: bool = True) -> str:
    """board_walk's `@@ forward_line` of the walk ahead from the box (-7.3, -6.2, heading 0): `m` along it; `end` = the
    step's closing line (the one a walk out is measured by), not the one before the wheels."""
    return ("print('@@ ' + json.dumps({'forward_line': {'from': [-7.3, -6.2, 0.0], 'to': [%r, -6.2, 0.0], "
            "'on_dock': False%s}}), flush=True); " % (round(-7.3 + m, 4), ", 'end': True" if end else ""))


#: The walk out as board_walk walks it: its line before a wheel turns, its whole forward_m, done.
OUT_OF_THE_BOX = ("import json, time; time.sleep(0.4); print('@@ {\"started\": true}', flush=True); " + _line(0.0, end=False)
                  + "time.sleep(0.8); " + _line(1.0)
                  + "print('[ 0.1] step 1 done', flush=True); print('[ 0.2] path event done', flush=True)")
STOPPED_IN_THE_BOX = ("import json, time, sys; print('@@ {\"started\": true}', flush=True); " + _line(0.0, end=False)
                      + "time.sleep(0.3); "
                      "print('@@ {\"held\": 1, \"at\": [-7.1, -6.84]}', flush=True); time.sleep(0.6); "
                      + _line(0.0) + "print('[ 1.0] STOP: step 1 เดินออกไปข้างหน้า: something 0.05 m ahead for 45 s "
                      "(0.00 of 1.00 m)', flush=True); sys.exit(2)")


@pytest.fixture(autouse=True)
def _clean():
    event_pageant.reset()
    event_tour.reset()
    yield
    event_pageant.reset()
    event_tour.reset()


@pytest.fixture
def in_the_box(event, stage, robot, monkeypatch):  # noqa: F811
    """The tracked debut as it is (`walk_out_of_box`, the shared fixture turns it off for the 10 Nov road's tests),
    walking on, a fake board_walk (`box["script"]`), the arm's rest asked before the wheels (EVENT_WALK_ARM_REST, a
    call up), the board's pose readable (`box["poses"]`: one per read, the last kept)."""
    doc = json.loads(event.read_text(encoding="utf-8"))
    for s in doc["scenes"]:
        if s["id"] == "debut":
            s["walk_out_of_box"] = True
    event.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    event_host.reset()
    log = stage["log"]
    box = {"log": log, "script": OUT_OF_THE_BOX, "routes": [], "poses": [{"x": -7.3, "y": -6.2, "yaw": 0.0}]}

    async def spawn(route):
        box["routes"].append(json.loads(Path(route).read_text(encoding="utf-8"))["paths"]["event"]["steps"])
        log.append(("wheels", "walker"))
        return await asyncio.create_subprocess_exec(sys.executable, "-c", box["script"], stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT)

    async def rested():
        log.append(("rest", "arm"))
        return True

    async def call_up(run, autonomous):
        return True

    async def pose():
        return box["poses"].pop(0) if len(box["poses"]) > 1 else box["poses"][0]

    monkeypatch.setattr(event_walk, "_spawn", spawn)
    monkeypatch.setattr(event_walk, "_faces", lambda: True)
    monkeypatch.setattr(event_walk, "_other_walker", lambda: False)
    monkeypatch.setattr(event_walk, "_arm_rested", rested)
    monkeypatch.setattr(event_walk, "_call_up", call_up)
    monkeypatch.setattr(settings, "event_walk_enabled", True)
    monkeypatch.setattr(settings, "event_walk_arm_rest", True)
    monkeypatch.setattr(robot_chassis, "configured", lambda: True)
    monkeypatch.setattr(robot_chassis, "pose", pose)
    monkeypatch.setattr(event_host, "WHEELS_POSE_GAP_S", 0.05)
    from app import event_box

    event_box.arm("event_page")                           # staff: she is in the box (the one walk out armed)
    return box


def _kinds(log):
    return [e[:2] for e in log]


def _press(box, until, timeout=10.0):
    async def go():
        out = await event_host.start_scene("debut")
        assert await _settle(until, timeout), _kinds(box["log"])
        await asyncio.sleep(0.3)                          # nothing comes after it
        return out
    return asyncio.run(go())


def _arm_asks(log):
    """Every arm request of any kind: the pageant (the link), a gesture (staff_request)."""
    return [e for e in log if e[0] in ("pageant", "arm")]


def test_the_tracked_debut_walks_out_of_the_box_first():
    doc = json.loads((Path(__file__).resolve().parents[1] / "data/events/floor1_event.json").read_text(encoding="utf-8"))
    debut = next(s for s in doc["scenes"] if s["id"] == "debut")
    assert debut["walk_out_of_box"] is True and debut["pageant"] and debut["forward_m"] == 1.3
    # the distance is labelled a guess to measure against the real box (robot ~0.7 m deep)
    assert "forward_m 1.3 ม." in debut["box_note_th"] and "ยังไม่เคยเดินในกล่องจริง" in debut["box_note_th"]
    parsed = event_host._normalise(doc, {})["scenes"][0]
    assert parsed["id"] == "debut" and parsed["walk_out_of_box"] is True


def test_out_of_the_box_the_hand_comes_up_only_once_she_is_out_and_still(in_the_box, robot):
    box = in_the_box
    out = _press(box, lambda: ("pageant", "pageant_end") in _kinds(box["log"]))
    assert out["walk"]["ok"], out
    kinds = _kinds(box["log"])
    # the arm rested, then the wheels; the hand only after she arrived; her line after the hand; the hand down after
    assert kinds.index(("rest", "arm")) < kinds.index(("wheels", "walker")) < kinds.index(("pageant", "pageant")) \
        < kinds.index(("say", "event_arrived")) < kinds.index(("pageant", "pageant_end")), kinds
    # nothing for the arm while the walker ran (a pageant asked during a walk sees that run still going)
    assert all(state == "arrived" for _, state in robot["walker_out"]), robot["walker_out"]
    assert ("arm", "greet_arm") not in box["log"]
    # no side watch on the way out: the arm is down, and the box's side would hold her for good
    assert box["routes"][0] == [{"forward": 1.0, "speed": event_host.FORWARD_SPEED, "label": "เดินออกไปข้างหน้า",
                                 "short_ok": False}]
    p = event_host.perform_view()
    assert p["phase"] == "done" and p["gesture"] == "pageant" and p["gesture_ok"] is True


def test_a_box_she_cannot_leave_is_no_wave_no_line_and_no_tour(in_the_box, monkeypatch):
    """The box door not open all the way: the walk out holds (no "excuse me" to a door), its patience ends it -- no
    arm at all, the debut's line not said, card 1's tour ends there and the card says so."""
    box = in_the_box
    box["script"] = STOPPED_IN_THE_BOX
    monkeypatch.setattr(settings, "event_tour", True)
    monkeypatch.setattr(event_tour, "_where", lambda: asyncio.sleep(0, (-7.3, -6.2, 0.0)))
    _press(box, lambda: event_tour.STATE["ended"] is not None)
    assert _arm_asks(box["log"]) == [], box["log"]
    assert not any(e[0] == "say" and e[1] in ("event_arrived", "event_held") for e in box["log"]), box["log"]
    view = event_tour.view()
    assert event_tour.STATE["ended"] == "debut_cut" and "ไม่โบก ไม่ไปต่อ" in json.dumps(view, ensure_ascii=False)
    assert event_host.perform_view()["phase"] == "cut"
    assert [e for e in box["log"] if e == ("wheels", "walker")] == [("wheels", "walker")]   # no other walk


def test_walking_off_in_the_box_the_line_with_no_arm(in_the_box, monkeypatch):
    box = in_the_box
    monkeypatch.setattr(settings, "event_walk_enabled", False)
    out = _press(box, lambda: (event_host.perform_view() or {}).get("phase") == "done")
    assert out["walk"]["ok"] is False
    assert _arm_asks(box["log"]) == [] and ("say", "event_scene") in _kinds(box["log"]), box["log"]
    p = event_host.perform_view()
    assert p["gesture_ok"] is False and p["gesture_error_th"] == event_host.IN_BOX_TH
    from app import event_box

    assert event_box.armed() and event_box.held()         # no walker set out: the one walk out is still hers


def test_out_but_the_wheels_not_seen_still_is_the_line_with_no_arm(in_the_box, monkeypatch):
    box = in_the_box
    monkeypatch.setattr(event_host, "WHEELS_STILL_WAIT_S", 0.4)
    box["poses"] = [{"x": -7.3 - 0.05 * k, "y": -6.2, "yaw": 0.0} for k in range(400)]   # creeping on
    _press(box, lambda: (event_host.perform_view() or {}).get("gesture_error_th") == event_host.NOT_STILL_TH)
    assert _arm_asks(box["log"]) == [] and ("say", "event_arrived") in _kinds(box["log"]), box["log"]
    from app import event_box

    # out, all of it, not seen still: the arm locked until staff look, the walk out used (never pressed again)
    assert event_box.held() and event_box.view()["left"] == "not_still" and not event_box.armed()


def test_a_red_stop_while_the_wheels_are_watched_is_no_line_and_no_arm(in_the_box, monkeypatch):
    box = in_the_box
    monkeypatch.setattr(event_host, "WHEELS_STILL_WAIT_S", 1.0)
    box["poses"] = [{"x": -7.3 - 0.05 * k, "y": -6.2, "yaw": 0.0} for k in range(400)]   # still creeping
    real = event_host._wheels_still

    async def watched(still):
        event_walk.STATE["stops"] = event_walk.stops() + 1    # the red stop, while the wheels are watched
        return await real(still)

    monkeypatch.setattr(event_host, "_wheels_still", watched)
    _press(box, lambda: (event_host.perform_view() or {}).get("phase") == "stopped")
    assert _arm_asks(box["log"]) == [] and ("say", "event_arrived") not in _kinds(box["log"]), box["log"]


def test_an_unreadable_pose_after_the_walk_is_no_wave(in_the_box, monkeypatch):
    box = in_the_box

    async def gone():
        if box.get("walked"):
            raise RuntimeError("the board does not answer")
        box["walked"] = True                              # the walk ahead's own read (its floor hazard)
        return {"x": -7.3, "y": -6.2, "yaw": 0.0}

    monkeypatch.setattr(robot_chassis, "pose", gone)
    _press(box, lambda: (event_host.perform_view() or {}).get("gesture_error_th") == event_host.NOT_STILL_TH)
    assert _arm_asks(box["log"]) == []


def test_the_walk_and_wave_of_10_nov_is_still_the_road_without_the_field(event, stage, robot, monkeypatch):  # noqa: F811
    """The shared fixture's debut (no `walk_out_of_box`): the hand up as the wheels turn, and the side watch."""
    from tests.test_debut_forward import _at_the_dock, _spawner

    routes: list = []
    _spawner(monkeypatch, routes, STARTED_THEN_ARRIVES)
    _at_the_dock(monkeypatch)

    async def go():
        await event_host.start_scene("debut")
        assert await _settle(lambda: ("pageant", "pageant_end") in _kinds(stage["log"]))

    asyncio.run(go())
    assert routes[0]["paths"]["event"]["steps"][0]["right_clear_m"] == event_host.PAGEANT_RIGHT_CLEAR_M
    assert robot["walker_out"] and robot["walker_out"][0] == (True, "walking"), robot["walker_out"]


def test_card_1s_tour_goes_on_only_after_the_hand_is_down(in_the_box, monkeypatch):
    """One press: out of the box, the wave and the line there, the hand down -- and only then the walk to the
    counter (its own arm rest first)."""
    box = in_the_box
    monkeypatch.setattr(settings, "event_tour", True)
    monkeypatch.setattr(event_tour, "TICK_S", 0.05)
    monkeypatch.setattr(event_walk, "_app_latched", lambda: False)
    monkeypatch.setattr(event_host, "ceremony_now", lambda: False)

    async def where():
        return (-8.1, -6.6, 170.0)

    monkeypatch.setattr(event_tour, "_where", where)
    monkeypatch.setattr(event_tour, "_chassis", lambda: True)

    async def go():
        stop = time.monotonic() + 15.0

        async def poll():
            while time.monotonic() < stop:
                event_walk.heartbeat()
                await asyncio.sleep(0.1)

        p = asyncio.create_task(poll())
        try:
            await event_host.start_scene("debut")
            assert await _settle(lambda: sum(1 for e in box["log"] if e == ("wheels", "walker")) >= 2, 12.0), \
                _kinds(box["log"])
        finally:
            p.cancel()
            await event_walk.stop("staff")
            event_tour.reset()

    asyncio.run(go())
    kinds = _kinds(box["log"])
    second = [k for k, e in enumerate(kinds) if e == ("wheels", "walker")][1]
    assert kinds.index(("pageant", "pageant")) < kinds.index(("pageant", "pageant_end")) < second, kinds
    # the counter's leg rested the arm again before its wheels
    assert ("rest", "arm") in kinds[kinds.index(("pageant", "pageant_end")):second], kinds
