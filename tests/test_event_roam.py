"""Walking round the event by herself, and home by herself (owner 5 Nov 2026: "เราปล่อยให้หุ่นเดิน
ทั่วๆเองได้ไหม หรือให้กลับแท่นเองตามสถานการณ์"). app/event_roam.py, on fake walkers: nothing moves.

What these hold down:
* only stops people have walked, never off the list; never walking away from somebody talking;
* every human stop and a quiet staff page end it, and it does not start itself again;
* two legs in a row that fail end it, instead of setting out again;
* she goes home for the battery and for an empty room, and comes back by herself;
* every way round the floor hazard is computed from where she stands.
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from pathlib import Path

import pytest

from app import event_host, event_roam, event_walk, greeter
from app.config import settings
from tests.test_event_host import ARRIVES, WALKS_ON, _walker

ROOT = Path(__file__).resolve().parents[1]
TRACKED = ROOT / "tests" / "data" / "floor1_event_rehearsed.json"
FAILS = "import sys; print('[ 0.1] step 1', flush=True); print('@@ {\"stopped\": \"something 0.10 m ahead\"}', flush=True); sys.exit(2)"


@pytest.fixture
def event(tmp_path, monkeypatch):
    doc = json.loads(TRACKED.read_text(encoding="utf-8"))
    route = tmp_path / "route.json"
    route.write_text(json.dumps({"dock": {"pose": [-7.3, -6.2, 168.8], "approach": [-9.0, -6.2]}, "paths": {}}), encoding="utf-8")
    doc["route_file"] = str(route)
    path = tmp_path / "floor1_event.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "event_file", str(path))
    monkeypatch.setattr(event_walk, "RUN_DIR", tmp_path / ".run")
    monkeypatch.setattr(event_walk, "GATE_WAIT_S", 0.0)
    monkeypatch.setattr(event_roam, "TICK_S", 0.01)
    monkeypatch.setattr(settings, "event_roam_first_wait_s", 0.0)
    monkeypatch.setattr(settings, "event_roam_idle_dock_s", 0.0)
    event_host.reset()
    event_walk.heartbeat()
    return path


@pytest.fixture
def said(monkeypatch):
    out = []

    async def fake_say(text, source, still):
        out.append((source, text, still()))

    monkeypatch.setattr(event_host, "_say", fake_say)
    return out


def _quick(sc=None, dwell=0.0, stops=None):
    sc = dict(sc or event_host.scene("roam_lobby"))
    sc["roam"] = {"stops": stops or sc["roam"]["stops"], "dwell_s": dwell}
    return sc


async def _until(cond, wait=40.0):
    for _ in range(int(wait / 0.01)):
        if cond():
            return True
        await asyncio.sleep(0.01)
    return False


def _keep_page_alive():
    async def beat():
        while True:
            event_walk.heartbeat()
            await asyncio.sleep(0.05)

    return asyncio.create_task(beat())


def _walks(monkeypatch):
    """Every leg asked of the walker, by spot."""
    legs = []
    real = event_walk.start

    async def start(spot, name, steps, **kw):
        legs.append(spot)
        return await real(spot, name, steps, **kw)

    monkeypatch.setattr(event_walk, "start", start)
    return legs


# ---- the list -----------------------------------------------------------------------------------

def test_the_scene_walks_only_stops_people_have_walked_and_none_near_the_bump(event):
    sc = event_host.scene("roam_lobby")
    assert sc["approach"] and "ceremony" not in sc and sc["roam"]["dwell_s"] > 0
    doc = event_host.load()
    points = [doc["spots"][s]["pose"][:2] for s in sc["roam"]["stops"]]
    assert len(points) >= 3
    for a in points:
        for b in points:
            assert event_host.hazard_on_line(*a, *b) is None, (a, b)


def test_the_real_files_after_ceremony_roam_keeps_clear_of_the_counter_and_the_globe(monkeypatch):
    """25 Nov (owner: "ส่วนหลังพีธีก็ให้หุ่นเดินวนสักแปป ให้แขกได้ถ่ายรูปพูดคุย"): the event file's own roam, not the
    rehearsed snapshot -- its stops away from where guests stand after the ceremony (the counter's spot, the globe's),
    none of its legs across the floor bump, no walking up to guests (a dry run on the robot that holds her at a stop),
    and its first stop one the simulator walks to from beside the globe, where card 3 leaves her."""
    monkeypatch.setattr(settings, "event_file", str(ROOT / "data" / "events" / "floor1_event.json"))
    event_host.reset()
    sc = event_host.scene("roam_lobby")
    assert not sc["approach"] and sc["roam"]["dwell_s"] >= 60
    doc = event_host.load()
    stops = sc["roam"]["stops"]
    assert stops[0] == "entrance"
    points = [doc["spots"][s]["pose"][:2] for s in stops]
    assert len(points) >= 3
    for spot in ("beside_host", "beside_globe"):
        q = doc["spots"][spot]["pose"][:2] if doc["spots"][spot].get("pose") else event_host.spot_steps(doc["spots"][spot])[-1]["to"]
        assert min(math.hypot(p[0] - q[0], p[1] - q[1]) for p in points) >= 2.2, spot
    for a, b in zip(points, points[1:] + points[:1]):
        assert event_host.hazard_on_line(*a, *b) is None, (a, b)


def test_a_ceremony_never_roams(monkeypatch):
    """No scene is a ceremony since 9 Nov (owner: "ไม่งั้นจะปนกัน" -- the ceremony is the Emma
    Ceremony app): a scene's old `ceremony` flag no longer decides it. The ceremony app being on
    does -- the roam refuses to start (and its loop stops: tests/test_ceremony_app.py)."""
    from app import event_roam
    from tests.ceremony_app import ceremony_on

    doc = event_host._normalise({"scenes": [{"id": "x", "ceremony": True, "roam": {"stops": ["a", "b"]}}]}, {})
    assert doc["scenes"][0]["roam"] == {"stops": ["a", "b"]}
    monkeypatch.setattr(settings, "event_walk_enabled", True)
    monkeypatch.setattr(event_host, "spot_steps", lambda spot: [{"to": [0, 0]}])
    ceremony_on()
    out = event_roam.start(doc["scenes"][0])
    assert out["ok"] is False and out["error"] == "ceremony_app" and not event_roam.STATE["on"]


def test_with_walking_off_the_scene_says_so_and_does_nothing(event, said):
    assert settings.event_walk_enabled is False
    out = asyncio.run(event_host.start_scene("roam_lobby"))
    assert out["roam"]["ok"] is False and out["roam"]["error"] == "no_walk" and "ปิด" in out["roam"]["error_th"]
    assert event_roam.snapshot()["on"] is False


def test_unwalkable_stops_are_not_a_list(event, said, monkeypatch):
    _walker(monkeypatch, ARRIVES)
    sc = _quick(stops=["table_1", "table_2", "reception"])     # two with no pose yet
    out = event_roam.start(sc)
    assert out["ok"] is False and out["error"] == "no_stops"


# ---- walking the list --------------------------------------------------------------------------

def test_she_goes_round_the_list_in_order_and_round_again(event, said, monkeypatch):
    _walker(monkeypatch, ARRIVES)
    legs = _walks(monkeypatch)

    async def go():
        beat = _keep_page_alive()
        assert event_roam.start(_quick(stops=["reception", "lobby_east", "lobby_mid"]))["ok"]
        assert await _until(lambda: len(legs) >= 5)
        await event_roam.stop("staff")
        await event_walk.stop("staff")
        beat.cancel()

    asyncio.run(go())
    assert legs[:5] == ["reception", "lobby_east", "lobby_mid", "reception", "lobby_east"]


def test_she_does_not_walk_away_from_somebody_talking(event, said, monkeypatch):
    from app import session as session_module

    _walker(monkeypatch, ARRIVES)
    legs = _walks(monkeypatch)

    class _Call:
        provider = None
        _last_heard_at = time.monotonic()

        def _array_audible_until(self, now):
            return 0.0

    call = _Call()
    monkeypatch.setattr(session_module, "_active", call)
    monkeypatch.setattr(event_roam, "GUEST_HOLD_S", 30.0)

    async def go():
        beat = _keep_page_alive()
        event_roam.start(_quick())
        await asyncio.sleep(0.2)
        assert legs == [] and event_roam.snapshot()["phase"] == "busy"
        await event_roam.stop("staff")
        beat.cancel()

    asyncio.run(go())


def test_she_does_not_walk_off_in_the_middle_of_her_own_sentence(event, said, monkeypatch):
    # on its own: after somebody talking, the moment's grace would hide this for 5 s and a test would pass on that
    from app import session as session_module

    _walker(monkeypatch, ARRIVES)
    legs = _walks(monkeypatch)

    class _Call:
        provider = None
        _last_heard_at = -1e9

        def _array_audible_until(self, now):
            return now + 5

    monkeypatch.setattr(session_module, "_active", _Call())

    async def go():
        beat = _keep_page_alive()
        event_roam.start(_quick())
        await asyncio.sleep(0.2)
        assert legs == [] and event_roam.snapshot()["phase"] == "busy"
        await event_roam.stop("staff")
        beat.cancel()

    asyncio.run(go())


def test_a_walk_up_to_a_guest_goes_first(event, said, monkeypatch):
    from app import face_approach

    _walker(monkeypatch, ARRIVES)
    legs = _walks(monkeypatch)
    monkeypatch.setitem(face_approach.STATE, "phase", "walking")

    async def go():
        beat = _keep_page_alive()
        event_roam.start(_quick())
        await asyncio.sleep(0.15)
        assert legs == []
        await event_roam.stop("staff")
        beat.cancel()

    asyncio.run(go())


# ---- what ends it ----------------------------------------------------------------------------------

def test_a_quiet_staff_page_ends_it_and_it_does_not_come_back(event, said, monkeypatch):
    _walker(monkeypatch, ARRIVES)
    legs = _walks(monkeypatch)
    monkeypatch.setattr(settings, "event_walk_heartbeat_s", 0.05)

    async def go():
        event_walk.heartbeat()
        event_roam.start(_quick(dwell=30.0))
        assert await _until(lambda: not event_roam.STATE["on"])
        assert event_roam.snapshot()["why_th"] == event_roam.WHY_TH["control_lost"]
        count = len(legs)
        event_walk.heartbeat()                               # the phone came back
        await asyncio.sleep(0.1)
        assert event_roam.STATE["on"] is False and len(legs) == count

    asyncio.run(go())


def test_staff_stop_ends_it(event, said, monkeypatch):
    _walker(monkeypatch, WALKS_ON)

    async def go():
        beat = _keep_page_alive()
        event_roam.start(_quick())
        assert await _until(event_walk.walking)
        await event_host.stop()
        assert event_roam.STATE["on"] is False and not event_walk.walking()
        assert "หยุด" in event_roam.snapshot()["why_th"]
        beat.cancel()

    asyncio.run(go())


def test_staff_stop_while_she_stands_at_a_stop_ends_it_too(event, said, monkeypatch):
    # no leg under way: stopping the walk alone would leave the list to set out again when the wait ends
    _walker(monkeypatch, ARRIVES)
    legs = _walks(monkeypatch)

    async def go():
        beat = _keep_page_alive()
        event_roam.start(_quick(dwell=0.3))
        assert await _until(lambda: event_roam.STATE["i"] == 0 and event_roam.STATE["phase"] == "waiting")
        count = len(legs)
        await event_host.stop()
        assert event_roam.STATE["on"] is False
        await asyncio.sleep(0.5)                             # past the wait
        assert len(legs) == count
        beat.cancel()

    asyncio.run(go())


# ---- a stop in the moment the walker is being started ----------------------------------------------

def _slow_spawn(monkeypatch):
    """The fake walker, held at the spawn until the test lets go."""
    release = asyncio.Event()
    real = event_walk._spawn
    spawned = []

    async def spawn(route):
        await release.wait()
        proc = await real(route)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(event_walk, "_spawn", spawn)
    return release, spawned


def test_a_stop_while_the_walker_is_being_started_is_not_lost(event, said, monkeypatch):
    # "หยุด" in that moment found no process, returned False, and the robot set out
    _walker(monkeypatch, WALKS_ON)

    async def go():
        release, spawned = _slow_spawn(monkeypatch)
        beat = _keep_page_alive()
        started = asyncio.create_task(event_walk.start("reception", "x", [{"to": [-9.0, -6.0]}], dock=None))
        await asyncio.sleep(0.05)
        assert event_walk.walking() and event_walk.current().proc is None
        stopping = asyncio.create_task(event_walk.stop("voice"))
        await asyncio.sleep(0.05)
        release.set()
        assert await stopping is True
        await started
        run = event_walk.current()
        assert await _until(lambda: not event_walk.walking(), 10.0), run.state
        assert run.state == "stopped" and run.reason == "voice"
        assert spawned[0].returncode is not None             # really gone, not forgotten
        beat.cancel()

    asyncio.run(go())


def test_a_caller_cancelled_during_the_start_leaves_no_walker_running(event, said, monkeypatch):
    # a roam cancelled by a stop, in that moment: a cancelled spawn would leave a walker nobody reads
    _walker(monkeypatch, WALKS_ON)

    async def go():
        release, spawned = _slow_spawn(monkeypatch)
        beat = _keep_page_alive()
        started = asyncio.create_task(event_walk.start("reception", "x", [{"to": [-9.0, -6.0]}], dock=None))
        await asyncio.sleep(0.05)
        started.cancel()
        await asyncio.sleep(0.05)
        assert started.cancelled()
        release.set()
        run = event_walk.current()
        assert await _until(lambda: not event_walk.walking(), 10.0), run.state
        assert run.state == "stopped" and run.reason == "cancelled"
        assert spawned[0].returncode is not None
        beat.cancel()

    asyncio.run(go())


def test_a_voice_stop_mid_leg_ends_it(event, said, monkeypatch):
    _walker(monkeypatch, WALKS_ON)

    async def go():
        beat = _keep_page_alive()
        event_roam.start(_quick())
        assert await _until(event_walk.walking)
        await event_walk.stop_from("voice")
        assert await _until(lambda: not event_roam.STATE["on"])
        assert event_roam.snapshot()["why_th"] == event_roam.WHY_TH["voice"]
        beat.cancel()

    asyncio.run(go())


def test_a_scene_or_a_walk_staff_chose_ends_it(event, said, monkeypatch):
    _walker(monkeypatch, WALKS_ON)

    async def go():
        beat = _keep_page_alive()
        event_roam.start(_quick())
        assert await _until(event_walk.walking)
        await event_host.go_spot("reception")
        assert event_roam.STATE["on"] is False and event_roam.snapshot()["why_th"] == event_roam.WHY_TH["staff"]
        await event_walk.stop("staff")
        event_roam.start(_quick())
        assert await _until(event_walk.walking)
        await event_host.start_scene("introduce")
        assert event_roam.STATE["on"] is False and event_roam.snapshot()["why_th"] == event_roam.WHY_TH["scene"]
        beat.cancel()

    asyncio.run(go())


def test_two_legs_in_a_row_that_fail_end_it(event, said, monkeypatch):
    """Setting out again and again into a problem, with guests about, is worse than standing."""
    _walker(monkeypatch, FAILS)
    legs = _walks(monkeypatch)

    async def go():
        beat = _keep_page_alive()
        event_roam.start(_quick())
        assert await _until(lambda: not event_roam.STATE["on"])
        beat.cancel()

    asyncio.run(go())
    assert len(legs) == event_roam.MAX_FAILS
    assert event_roam.snapshot()["why_th"] == event_roam.WHY_TH["walk_trouble"]
    assert legs == ["reception", "lobby_east"], "the second try is the next stop, not the blocked one again"


def test_a_stop_she_could_not_reach_is_passed_over_not_tried_again(event, said, monkeypatch, tmp_path):
    """25 Nov, the after-ceremony crowd: a guest standing on a stop makes the board give up on it. The
    next leg goes to the stop after it; one that arrives puts her back on the list from there."""
    count = tmp_path / "legs.txt"
    script = ("import sys, pathlib; p = pathlib.Path(%r); n = int(p.read_text() or 0) if p.exists() else 0; "
              "p.write_text(str(n + 1))\n"
              "if n == 1:\n"
              "    print('[ 0.1] step 1', flush=True); print('@@ {\"stopped\": \"something 0.10 m ahead\"}', flush=True); "
              "sys.exit(2)\n"
              "print('[ 0.1] step 1 done', flush=True); print('[ 0.2] path event done', flush=True)") % str(count)
    _walker(monkeypatch, script)
    legs = _walks(monkeypatch)

    async def go():
        beat = _keep_page_alive()
        assert event_roam.start(_quick(stops=["reception", "lobby_east", "lobby_mid"]))["ok"]
        assert await _until(lambda: len(legs) >= 4)
        await event_roam.stop("staff")
        await event_walk.stop("staff")
        beat.cancel()

    asyncio.run(go())
    # the second leg (lobby_east) did not arrive: lobby_mid next, then round again from there
    assert legs[:4] == ["reception", "lobby_east", "lobby_mid", "reception"]


def test_nothing_survives_a_restart(event, said, monkeypatch):
    _walker(monkeypatch, ARRIVES)

    async def go():
        beat = _keep_page_alive()
        event_roam.start(_quick(dwell=30.0))
        await asyncio.sleep(0.05)
        await event_roam.stop("shutdown")
        event_roam.reset()
        await event_host.recover_after_restart()
        assert event_roam.STATE["on"] is False
        beat.cancel()

    asyncio.run(go())


# ---- going home ------------------------------------------------------------------------------------

def _battery(monkeypatch, level):
    now = {"level": level}
    monkeypatch.setattr(event_walk, "health_view", lambda: {"known": True, "board": True, "battery": now["level"],
                                                            "low_battery": False, "lost": False})
    return now


def test_low_battery_she_says_so_goes_home_and_comes_back_charged(event, said, monkeypatch):
    _walker(monkeypatch, ARRIVES)
    legs = _walks(monkeypatch)
    battery = _battery(monkeypatch, 90)
    monkeypatch.setattr(settings, "event_roam_dock_battery", 30)
    monkeypatch.setattr(settings, "event_roam_resume_battery", 70)

    async def go():
        beat = _keep_page_alive()
        event_roam.start(_quick(dwell=0.05, stops=["reception", "lobby_mid"]))
        assert await _until(lambda: len(legs) >= 1)
        battery["level"] = 25
        assert await _until(lambda: "dock" in legs)
        assert await _until(lambda: event_roam.snapshot()["phase"] == "charging")
        assert event_roam.STATE["on"] is True
        count = len(legs)
        await asyncio.sleep(0.1)
        assert len(legs) == count, "she stays until it is charged"
        battery["level"] = 75
        assert await _until(lambda: len(legs) > count)
        await event_roam.stop("staff")
        await event_walk.stop("staff")
        beat.cancel()

    asyncio.run(go())
    assert any(source == "event_roam_home" for source, _text, _still in said)


def test_low_battery_waits_for_the_guest_she_is_talking_to(event, said, monkeypatch):
    from app import session as session_module

    _walker(monkeypatch, ARRIVES)
    legs = _walks(monkeypatch)
    _battery(monkeypatch, 25)

    class _Call:
        provider = None
        _last_heard_at = time.monotonic()

        def _array_audible_until(self, now):
            return 0.0

    monkeypatch.setattr(session_module, "_active", _Call())

    async def go():
        beat = _keep_page_alive()
        event_roam.start(_quick())
        await asyncio.sleep(0.15)
        assert "dock" not in legs
        await event_roam.stop("staff")
        beat.cancel()

    asyncio.run(go())


def test_nobody_about_she_goes_home_silently_and_comes_back_when_somebody_is(event, said, monkeypatch):
    from app import focus

    _walker(monkeypatch, ARRIVES)
    legs = _walks(monkeypatch)
    monkeypatch.setattr(settings, "event_roam_idle_dock_s", 0.05)
    seen = {"age": None}
    monkeypatch.setattr(focus, "engaged_age", lambda now=None: seen["age"])

    async def go():
        beat = _keep_page_alive()
        event_roam.start(_quick(dwell=30.0, stops=["reception", "lobby_mid"]))
        assert await _until(lambda: event_roam.snapshot()["phase"] == "parked")
        assert "dock" in legs
        count = len(legs)
        await asyncio.sleep(0.05)
        assert len(legs) == count
        seen["age"] = 1.0                                    # somebody at the robot
        assert await _until(lambda: len(legs) > count or event_roam.snapshot()["phase"] != "parked")
        await event_roam.stop("staff")
        await event_walk.stop("staff")
        beat.cancel()

    asyncio.run(go())
    assert not any(source == "event_roam_home" for source, _t, _s in said)


# ---- the way round the bump --------------------------------------------------------------------------

def test_a_straight_leg_across_it_gets_a_way_round(event):
    steps = [{"do": "undock"}, {"to": [-9.0, -5.2], "speed": 0.3, "label": "x"}]
    out = event_host.keep_off_hazards(steps, (-7.0, 1.6))
    assert out[0] == {"do": "undock"} and out[-1] == steps[-1]
    assert len(out) == 3 and "อ้อม" in out[1]["label"]
    x, y = out[1]["to"]
    assert event_host.hazard_on_line(-7.0, 1.6, x, y) is None
    assert event_host.hazard_on_line(x, y, -9.0, -5.2) is None


def test_a_clear_leg_is_left_alone(event):
    steps = [{"do": "undock"}, {"to": [-8.6, 0.2], "speed": 0.3}]
    assert event_host.keep_off_hazards(steps, (-9.0, -5.2)) == steps


def test_the_way_to_the_dock_is_checked_too(event):
    dock = {"pose": [-7.3, -6.2, 168.8], "approach": [-9.0, -6.2]}
    out = event_host.keep_off_hazards([{"do": "dock"}], (-7.0, 1.6), dock)
    assert len(out) == 2 and out[-1] == {"do": "dock"} and "อ้อม" in out[0]["label"]


def test_the_dock_steps_check_aims_where_the_walk_script_really_goes(event):
    """Review 16 Nov: with `back_from_m` board_walk's dock() sends the board to the spot it
    reverses from (floor 1: 0.3 m from the approach point, which it then skips) -- the hazard
    check still aimed at the approach point. Same point as the script's, key by key; with no
    key, the approach point as ever."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("board_walk_for_host", event_host.ROOT / "scripts" / "board_walk.py")
    bw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bw)
    assert (event_host._DOCK_PREDOCK_M, event_host._DOCK_BACK_MAX_M, event_host._DOCK_AXIS_MAX_DEG,
            event_host._DOCK_APPROACH_SAME_M) == (bw.PREDOCK_M, bw.BACK_FROM_MAX_M, bw.AXIS_OFFSET_MAX_DEG, bw.APPROACH_SAME_M)
    old = {"pose": [-7.11, -6.78, 169.6], "approach": [-9.0, -6.2]}
    assert event_host.dock_way_in(old) == (-9.0, -6.2)
    assert event_host.dock_way_in({"pose": [-7.11, -6.78, 169.6]}) is None and event_host.dock_way_in(None) is None
    for extra in ({"back_from_m": 1.9}, {"back_from_m": 1.9, "axis_offset_deg": 2.3}, {"back_from_m": 2.4, "axis_offset_deg": -4.0},
                  {"back_from_m": 9}, {"back_from_m": 0.3}, {"axis_offset_deg": 2.3}, {"back_from_m": 1.9, "axis_offset_deg": 30}):
        dock = dict(old, **extra)
        far, line, (x, y, _) = bw.back_from_m({"dock": dock}), bw.dock_line({"dock": dock}), bw.dock_pose({"dock": dock})
        spot = (x + far * math.cos(line), y + far * math.sin(line))
        want = spot if far and math.hypot(spot[0] + 9.0, spot[1] + 6.2) < bw.APPROACH_SAME_M else (-9.0, -6.2)
        assert event_host.dock_way_in(dock) == pytest.approx(want), extra
    # an approach somewhere else is still the first point; none at all = the spot
    away = dict(old, back_from_m=1.9, approach=[-9.6, -5.0])
    assert event_host.dock_way_in(away) == (-9.6, -5.0)
    alone = {"pose": [-7.11, -6.78, 169.6], "back_from_m": 1.9}
    assert event_host.dock_way_in(alone) == pytest.approx((-7.11 + 1.9 * math.cos(math.radians(169.6)),
                                                           -6.78 + 1.9 * math.sin(math.radians(169.6))))
    # and the way round is drawn to that spot
    out = event_host.keep_off_hazards([{"do": "dock"}], (-7.0, 1.6), dict(old, back_from_m=1.9, axis_offset_deg=2.3))
    assert len(out) == 2 and out[-1] == {"do": "dock"} and "อ้อม" in out[0]["label"]
    sx, sy = event_host.dock_way_in(dict(old, back_from_m=1.9, axis_offset_deg=2.3))
    assert event_host.hazard_on_line(out[0]["to"][0], out[0]["to"][1], sx, sy) is None


def test_standing_inside_its_reach_she_just_leaves(event):
    steps = [{"to": [-9.0, -5.2], "speed": 0.3}]
    assert event_host.keep_off_hazards(steps, (-7.85, 0.9)) == steps


def test_a_scene_walk_is_routed_from_where_she_really_is(event, said, monkeypatch):
    from app import robot_chassis

    async def pose():
        return {"x": -7.0, "y": 1.6, "yaw": 0.0}

    monkeypatch.setattr(robot_chassis, "configured", lambda: True)
    monkeypatch.setattr(robot_chassis, "pose", pose)
    _walker(monkeypatch, ARRIVES)
    written = []
    real = event_walk.start

    async def start(spot, name, steps, **kw):
        written.append(steps)
        return await real(spot, name, steps, **kw)

    monkeypatch.setattr(event_walk, "start", start)
    asyncio.run(event_host.go_spot("reception"))
    assert any("อ้อม" in str(s.get("label")) for s in written[0])


def test_a_walk_out_of_the_dock_leaves_by_its_corridor_when_the_dock_says_so(event):
    """17 Nov, the floor simulator on the furniture of 16 Nov: dock -> entrance, planned straight
    by the board from the pre-dock spot: the centre line passes 0.22 m from the corner of the table
    of 28 Oct (cells x -7.62..-7.52, y -4.82..-4.62; an arm hit it on the way in), inside the base's
    own half-width. The way out takes the way in's corridor -- only for a dock that says so, only
    from the dock, once. (How near the arms come is the simulator's assumed planner margin.)"""
    dock = {"pose": [-7.11, -6.78, 169.6], "approach": [-9.0, -6.2], "leave_by_approach": True}
    steps = [{"do": "undock"}, {"to": [-8.6, 0.2], "speed": 0.3, "label": "x"}]
    out = event_host.leave_the_dock(steps, (-7.11, -6.78), dock)
    assert [s_.get("to") for s_ in out] == [None, [-9.0, -6.2], [-8.6, 0.2]] and out[1]["speed"] == 0.3
    assert out[0] == {"do": "undock"} and out[-1] == steps[-1]
    # the dock's own word: without it (floor 2, every route file until now) nothing changes
    assert event_host.leave_the_dock(steps, (-7.11, -6.78), dict(dock, leave_by_approach=False)) == steps
    assert event_host.leave_the_dock(steps, (-7.11, -6.78), {"pose": dock["pose"], "approach": dock["approach"]}) == steps
    assert event_host.leave_the_dock(steps, (-7.11, -6.78), None) == steps
    # from anywhere else it is not a way out of the dock (the lane: the test below)
    assert event_host.leave_the_dock(steps, (-9.0, -5.2), dock) == steps
    # a walk whose first point is that corridor already, and one with no point at all
    there = [{"do": "undock"}, {"to": [-9.0, -6.2], "speed": 0.3}, {"to": [-11.0, -6.9], "speed": 0.3}]
    assert event_host.leave_the_dock(there, (-7.11, -6.78), dock) == there
    assert event_host.leave_the_dock([{"do": "dock"}], (-7.11, -6.78), dock) == [{"do": "dock"}]
    # and the hazards are checked on the legs as they will be walked
    assert event_host.route_from(steps, (-7.11, -6.78), dock)[1]["to"] == [-9.0, -6.2]


def test_at_the_dock_is_its_lane_up_to_the_approach_point_not_a_circle_round_it(event):
    """Review 17 Nov: stopped 0.7-1.0 m out in the nook (a red stop in the 1.9 m reverse, a way
    home that failed) the next press went straight -- the radius round the dock pose was 0.7 m.
    The dock's lane: to a side of its line by 0.7 m at the most, from on it to 0.3 m short of
    the approach point; the reception, beside the lane, is not it."""
    dock = {"pose": [-7.11, -6.78, 169.6], "approach": [-9.0, -6.2], "leave_by_approach": True}
    ux, uy = math.cos(math.radians(169.6)), math.sin(math.radians(169.6))
    steps = [{"do": "undock"}, {"to": [-8.6, 0.2], "speed": 0.3}]
    for out in (0.0, 0.45, 0.8, 1.0, 1.5):
        at = (-7.11 + out * ux, -6.78 + out * uy)
        assert event_host.at_the_dock(at, dock), out
        assert len(event_host.leave_the_dock(steps, at, dock)) == 3, out
    for at in ((-9.0, -6.2), (-8.98, -6.44), (-9.0, -5.2), (-8.6, 0.2), (-7.11 - 0.9 * uy, -6.78 + 0.9 * ux)):
        assert not event_host.at_the_dock(at, dock), at
        assert event_host.leave_the_dock(steps, at, dock) == steps, at
    assert not event_host.at_the_dock((-7.11, -6.78), {"pose": [-7.11, -6.78, 169.6]})      # no approach point: no lane


def test_a_walk_resumed_after_she_was_put_on_the_dock_leaves_by_the_corridor_too(event, monkeypatch):
    """Review 17 Nov: 'เดินต่อ' replayed the steps left, routed from where she stood when the walk
    was first pressed. Pushed onto the dock in between, that was [undock, straight to the spot]."""
    from app import robot_chassis

    dock = {"pose": [-7.11, -6.78, 169.6], "approach": [-9.0, -6.2], "leave_by_approach": True}
    started = []

    async def start(spot, name, steps, **kw):
        started.append(steps)
        return {"ok": True}

    async def pose():
        return {"x": -7.11, "y": -6.78, "yaw": 2.96}

    monkeypatch.setattr(event_walk, "start", start)
    monkeypatch.setattr(robot_chassis, "configured", lambda: True)
    monkeypatch.setattr(robot_chassis, "pose", pose)

    def stopped(d):
        run = event_walk.Run("entrance", "ฝั่งทางเข้า")
        run.steps = [{"do": "undock"}, {"to": [-9.0, -5.2], "speed": 0.3}, {"to": [-8.6, 0.2], "speed": 0.3}]
        run.done, run.state, run.reason, run.dock = 2, "stopped", "staff", d
        event_walk.STATE["run"] = run

    stopped(dock)
    assert asyncio.run(event_walk.resume_last())["ok"]
    assert [s_.get("to") for s_ in started[-1]] == [None, [-9.0, -6.2], [-8.6, 0.2]], started[-1]
    # a dock that does not ask for that way out: the steps left, as they always were -- and
    # nothing is asked of the board for it (no pose read, no re-routing)
    asked = []

    async def never(steps, d):
        asked.append(d)
        return steps

    monkeypatch.setattr(event_host, "steps_from_pose", never)
    stopped({"pose": dock["pose"], "approach": dock["approach"]})
    assert asyncio.run(event_walk.resume_last())["ok"]
    assert started[-1] == [{"do": "undock"}, {"to": [-8.6, 0.2], "speed": 0.3}] and asked == []


def test_the_route_files_dock_is_read_once_until_the_file_changes(event, monkeypatch):
    """Review 17 Nov: every press asks for the dock now; the route file is not read again for it."""
    doc = event_host.load()
    reads = []
    real = event_host._read_json
    monkeypatch.setattr(event_host, "_read_json", lambda path: (reads.append(str(path)), real(path))[1])
    first = event_host.dock_doc(doc)
    for _ in range(5):
        assert event_host.dock_doc(doc) == first
    assert len(reads) == 1 and first["approach"] == [-9.0, -6.2]
    first["approach"] = "changed by a caller"                             # a copy is handed out
    assert event_host.dock_doc(doc)["approach"] == [-9.0, -6.2]
    route = Path(doc["route_file"])
    route.write_text(json.dumps({"dock": {"pose": [1.0, 2.0, 3.0], "approach": [4.0, 5.0], "leave_by_approach": True}}),
                     encoding="utf-8")
    assert event_host.dock_doc(doc)["pose"] == [1.0, 2.0, 3.0] and len(reads) == 2


def test_the_corridor_out_is_asked_for_with_no_hazards_in_the_file(event, monkeypatch):
    """steps_from_here used to return at once for an event file with no floor hazards."""
    from app import robot_chassis

    dock = {"pose": [-7.11, -6.78, 169.6], "approach": [-9.0, -6.2], "leave_by_approach": True}
    monkeypatch.setattr(event_host, "dock_doc", lambda doc: dock)
    monkeypatch.setattr(robot_chassis, "configured", lambda: True)

    async def pose():
        return {"x": -7.11, "y": -6.78, "yaw": 2.96}

    monkeypatch.setattr(robot_chassis, "pose", pose)
    steps = [{"do": "undock"}, {"to": [-9.0, -5.2], "speed": 0.3}]
    out = asyncio.run(event_host.steps_from_here(steps, {"hazards": []}))
    assert [s_.get("to") for s_ in out] == [None, [-9.0, -6.2], [-9.0, -5.2]]
    monkeypatch.setattr(event_host, "dock_doc", lambda doc: dict(dock, leave_by_approach=False))
    assert asyncio.run(event_host.steps_from_here(steps, {"hazards": []})) == steps


def test_no_pose_goes_as_it_was_and_the_walkers_own_gate_decides(event, monkeypatch):
    from app import robot_chassis

    monkeypatch.setattr(robot_chassis, "configured", lambda: False)
    steps = [{"to": [-9.0, -5.2]}]
    assert asyncio.run(event_host.steps_from_here(steps, event_host.load())) == steps


@pytest.mark.parametrize("who", ["voice", "desk"])
def test_a_voice_or_desk_stop_while_she_stands_at_a_stop_ends_it(event, said, monkeypatch, who):
    """Review 6 Nov: stop_from turned walking-up off and stopped a walk, never the roam. At a stop there
    is no walk to stop, and she set out for the next one once the wait ran out."""
    _walker(monkeypatch, ARRIVES)
    legs = _walks(monkeypatch)

    async def go():
        beat = _keep_page_alive()
        event_roam.start(_quick(dwell=0.3))
        assert await _until(lambda: event_roam.STATE["i"] == 0 and event_roam.STATE["phase"] == "waiting")
        count = len(legs)
        await event_walk.stop_from(who)
        assert event_roam.STATE["on"] is False and event_roam.STATE["why"] == who
        await asyncio.sleep(0.5)                             # past the wait
        assert len(legs) == count
        beat.cancel()

    asyncio.run(go())


def test_b_on_the_robot_while_she_waits_at_a_stop_ends_it(event, said, monkeypatch):
    _walker(monkeypatch, ARRIVES)
    legs = _walks(monkeypatch)
    latched = {"on": False}
    monkeypatch.setattr(greeter, "_app_latched", lambda link: latched["on"])

    async def go():
        beat = _keep_page_alive()
        event_roam.start(_quick(dwell=0.3))
        assert await _until(lambda: event_roam.STATE["i"] == 0 and event_roam.STATE["phase"] == "waiting")
        count = len(legs)
        latched["on"] = True
        assert await _until(lambda: not event_roam.STATE["on"])
        assert event_roam.STATE["why"] == "app_stop"
        await asyncio.sleep(0.5)
        assert len(legs) == count
        beat.cancel()

    asyncio.run(go())


def test_b_while_she_is_parked_at_the_dock_ends_it(event, said, monkeypatch):
    """Review 6 Nov: the dock wait watched the staff page only. B there, and once its 60 s latch ran
    out she left the dock and roamed on."""
    from app import focus

    _walker(monkeypatch, ARRIVES)
    legs = _walks(monkeypatch)
    monkeypatch.setattr(settings, "event_roam_idle_dock_s", 0.05)
    monkeypatch.setattr(focus, "engaged_age", lambda now=None: None)
    latched = {"on": False}
    monkeypatch.setattr(greeter, "_app_latched", lambda link: latched["on"])

    async def go():
        beat = _keep_page_alive()
        event_roam.start(_quick(dwell=30.0, stops=["reception", "lobby_mid"]))
        assert await _until(lambda: event_roam.snapshot()["phase"] == "parked")
        count = len(legs)
        latched["on"] = True
        assert await _until(lambda: not event_roam.STATE["on"])
        assert event_roam.STATE["why"] == "app_stop"
        latched["on"] = False                                # the latch runs out
        await asyncio.sleep(0.3)
        assert len(legs) == count
        beat.cancel()

    asyncio.run(go())
