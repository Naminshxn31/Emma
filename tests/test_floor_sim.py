"""scripts/floor_sim.py: the lobby the robot's own walking code is tried in before the robot.

Owner, 17 Nov 2026: "เอาข้อมูลที่เคยเก็บทั้งหมดทำเป็น simulator ... ให้ระบบของเราลองเดินอยู่ตรงนั้น".
The logic tests build small rooms (and their own route / event / ceremony files) inline.
The five tests under "against the real nights" need that night's files under .tmp/
(gitignored: lidar and poses pulled from the robot) and skip, saying what is then unchecked,
on a machine without them. Those five also read the LIVE, gitignored
data/robot/waypoints/floor1.json: an edit there (the dock, a path) can turn them red --
on purpose, it is the file the robot walks by.
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "scripts")]


def _fs():
    spec = importlib.util.spec_from_file_location("floor_sim", ROOT / "scripts" / "floor_sim.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["floor_sim"] = mod
    spec.loader.exec_module(mod)
    return mod


fs = _fs()


def _wall(x0, y0, x1, y1, step=0.05):
    n = max(2, int(math.hypot(x1 - x0, y1 - y0) / step) + 1)
    return [(x0 + (x1 - x0) * i / (n - 1), y0 + (y1 - y0) * i / (n - 1)) for i in range(n)]


def _block(x0, y0, x1, y1, step=0.05):
    return [(x, y) for x in np.arange(x0, x1 + 1e-6, step) for y in np.arange(y0, y1 + 1e-6, step)]


ROOM = _wall(0, 0, 10, 0) + _wall(10, 0, 10, 6) + _wall(10, 6, 0, 6) + _wall(0, 6, 0, 0)


def _layout(extra=(), unseen=(), name="room", dock=(1.0, 3.0, 0.0)):
    """A 10 x 6 m room. `extra`: things that stand there and are NOT on the board's map;
    `unseen`: things the lidar's beam passes (under its plane) but the base meets."""
    board = fs.grid_from_points(ROOM)
    pts = ROOM + list(extra) + list(unseen)
    blob = fs.ws.explore_blob(ROOM, margin=1.5)
    truth = fs.Grid(blob)
    occ = truth.occ.copy()
    glass = np.zeros_like(occ)
    for group, mask in ((extra, None), (unseen, glass)):
        for x, y in group:
            r, c = truth.rc(x, y)
            occ[r, c] = True
            if mask is not None:
                mask[r, c] = True
    truth.occ = occ
    return fs.Layout(truth, board, dock, glass=glass, name=name)


def _bearing(pts):
    return np.degrees(np.arctan2(pts[:, 1], pts[:, 0])), np.hypot(pts[:, 0], pts[:, 1])


# ---- the lidar ---------------------------------------------------------------------------

def test_the_lidar_sees_the_wall_ahead_and_nothing_behind(monkeypatch):
    """Ray-cast over the truth: the wall 5 m ahead at 5 m, the arc of the real robot
    (-101..+108 deg), and a pillar standing behind her not at all."""
    monkeypatch.setattr(fs, "FURNITURE_RETURNS", 1.0)
    lay = _layout(extra=_block(3.4, 2.9, 3.6, 3.1))                       # a pillar 1.5 m BEHIND (she faces +x from x=5)
    w = fs.FloorWorld(lay, (5.0, 3.0, 0.0), roll_off_deg=0.0)
    a, r = _bearing(w.scan())
    ahead = r[np.abs(a) < 2]
    assert len(ahead) and abs(float(np.median(ahead)) - 5.0) < 0.06
    assert a.min() > -101.5 and a.max() < 108.5
    assert not ((r < 2.0) & (np.abs(a) > 100)).any()                      # the pillar is behind: no return
    w.base.yaw = math.pi                                                  # turned round: it is 1.4 m ahead
    a, r = _bearing(w.scan())
    assert abs(float(np.median(r[np.abs(a) < 2])) - 1.4) < 0.08


def test_a_thing_in_front_hides_the_wall_behind_it(monkeypatch):
    monkeypatch.setattr(fs, "FURNITURE_RETURNS", 1.0)
    lay = _layout(extra=_block(7.0, 2.5, 7.1, 3.5))
    w = fs.FloorWorld(lay, (5.0, 3.0, 0.0), roll_off_deg=0.0)
    a, r = _bearing(w.scan())
    assert float(r[np.abs(a) < 5].max()) < 2.3                            # never the wall at 5 m through it
    assert abs(float(np.median(r[np.abs(a) > 40][:50])) - 0) > 2.0        # to the side the room is still there


def test_what_is_under_the_lidar_plane_is_not_seen_and_stops_the_base():
    """A low thing (the floor bump's kind): no return, and the base meets it."""
    low = _block(6.0, 2.8, 6.2, 3.2)
    lay = _layout(unseen=low)
    w = fs.FloorWorld(lay, (5.0, 3.0, 0.0), roll_off_deg=0.0)
    a, r = _bearing(w.scan())
    assert float(r[np.abs(a) < 5].min()) > 4.5
    res = fs.run_walk(lay, {"dock": {"pose": [1.0, 3.0, 0.0]}}, [{"to": [8.0, 3.0], "speed": 0.3}], (5.0, 3.0, 0.0),
                      roll_off_deg=0.0)
    assert res["end"] == "stop" and any(t.startswith("base") for t in res["touched"]), res["reason"]
    assert res["pose"][0] < 5.9


# ---- the board's MoveTo -----------------------------------------------------------------

def test_the_board_refuses_a_target_too_near_its_own_map_and_takes_one_further_off():
    lay = _layout()
    doc = {"dock": {"pose": [1.0, 3.0, 0.0]}}
    near = fs.run_walk(lay, doc, [{"to": [9.0 + 1.0 - fs.REFUSE_MAP_M + 0.15, 3.0], "speed": 0.3}], (5.0, 3.0, 0.0), roll_off_deg=0.0)
    assert near["end"] == "stop" and fs.stop_kind(near["reason"]) == "board gave up", near["reason"]
    assert abs(near["pose"][0] - 5.0) < 0.1                               # it did not set out
    far = fs.run_walk(lay, doc, [{"to": [8.5, 3.0], "speed": 0.3}], (5.0, 3.0, 0.0), roll_off_deg=0.0)
    assert far["end"] == "done" and math.hypot(far["pose"][0] - 8.5, far["pose"][1] - 3.0) < 0.2, far


def test_the_board_goes_round_what_its_lidar_sees_by_its_base_not_by_the_arms(monkeypatch):
    """The two-map point: a table that stands in the room and not on the board's map. The
    board sees it and goes round -- keeping its BASE clear (BOARD_INFLATE_M), so the arms pass
    at about nothing (28 Oct, the table at (-7.5, -4.2)). The walk arrives; the gap is what the
    pre-flight reports."""
    monkeypatch.setattr(fs, "FURNITURE_RETURNS", 1.0)
    lay = _layout(extra=_block(6.4, 2.6, 6.8, 3.4))
    res = fs.run_walk(lay, {"dock": {"pose": [1.0, 3.0, 0.0]}}, [{"to": [8.5, 3.0], "speed": 0.3}], (4.0, 3.0, 0.0),
                      roll_off_deg=0.0)
    assert res["end"] == "done", res["reason"]
    assert res["min_base_gap"] > -0.02 and res["min_arm_gap"] < 0.10, res
    open_room = fs.run_walk(_layout(), {"dock": {"pose": [1.0, 3.0, 0.0]}}, [{"to": [8.5, 3.0], "speed": 0.3}],
                            (4.0, 3.0, 0.0), roll_off_deg=0.0)
    assert open_room["min_arm_gap"] > 1.0


def test_a_person_standing_on_the_leg_is_not_walked_into():
    """Someone stands on the leg (and steps away a few seconds after she comes near): the
    board holds for them or goes round, and the walk arrives without touching them. How wide
    it goes round is BOARD_INFLATE_M -- an assumption, so the gap is reported, not judged."""
    lay = _layout()
    person = {"people": [{"at": (6.5, 3.0), "from_s": 0.0, "to_s": 1e9, "leave_after_seen_s": 6.0}]}
    res = fs.run_walk(lay, {"dock": {"pose": [1.0, 3.0, 0.0]}, "patience_s": 45, "events": True},
                      [{"to": [8.5, 3.0], "speed": 0.3}], (4.0, 3.0, 0.0), roll_off_deg=0.0, **person)
    assert res["end"] == "done" and 0.0 < res["min_person_gap"] < 1.0, res
    # someone who does NOT step away, across the whole way: she stands, and the walk ends short
    wall_of_people = {"people": [{"at": (6.5, y), "from_s": 0.0, "to_s": 1e9} for y in np.arange(0.3, 5.8, 0.4)]}
    res = fs.run_walk(lay, {"dock": {"pose": [1.0, 3.0, 0.0]}}, [{"to": [8.5, 3.0], "speed": 0.3}], (4.0, 3.0, 0.0),
                      roll_off_deg=0.0, **wall_of_people)
    assert res["end"] == "stop" and res["min_person_gap"] > 0.0 and res["pose"][0] < 6.3, res


# ---- the board's pose ---------------------------------------------------------------------

def _slides(world) -> bool:
    world.sleep(5.0)
    return world.max_err > 0.3


def test_the_pose_slides_facing_the_nook_and_comes_back_on_turning_away():
    """The one measured error (16 Nov): facing the dock's nook from nearer than ~1.5 m the
    board reads ~0.4 m further out along the dock's line; turned away, it is right again."""
    lay = _layout(name="20261116", dock=(1.0, 3.0, 0.0))
    w = fs.FloorWorld(lay, (2.0, 3.0, math.pi), roll_off_deg=0.0, nook_slides=1.0)   # 1 m out, facing the dock
    w.sleep(5.0)
    x, y, _ = w.pose()
    assert abs((x - 2.0) - fs.ws.SLIP_M) < 0.03 and abs(y - 3.0) < 0.03, (x, y)
    w.base.yaw = 0.0                                                      # facing out
    w.sleep(1.5)
    x, y, _ = w.pose()
    assert math.hypot(x - 2.0, y - 3.0) < 0.03
    # further out than the slide was ever seen, and a board taken at its word: none
    far = fs.FloorWorld(lay, (1.0 + fs.ws.SLIP_WITHIN_M + 0.5, 3.0, math.pi), roll_off_deg=0.0, nook_slides=1.0)
    far.sleep(5.0)
    assert far.max_err == 0.0
    sure = fs.FloorWorld(lay, (2.0, 3.0, math.pi), roll_off_deg=0.0, loc=False, nook_slides=1.0)
    sure.sleep(5.0)
    assert sure.max_err == 0.0
    # it does not slide every time she faces the nook (19 of 50 real frames): about NOOK_SLIDES of them
    slid = sum(1 for k in range(40) if _slides(fs.FloorWorld(lay, (2.0, 3.0, math.pi), roll_off_deg=0.0, seed=k)))
    assert 8 <= slid <= 24, slid


def test_q_follows_how_well_the_boards_map_explains_the_scan(monkeypatch):
    """Q ~ 40 * fit + 39.5 (950 real frames): a room that is its map reads high, the same
    room full of things the map does not have reads low -- what the walker's gates see."""
    monkeypatch.setattr(fs, "FURNITURE_RETURNS", 1.0)
    w = fs.FloorWorld(_layout(), (5.0, 3.0, 0.0), roll_off_deg=0.0)
    w.sleep(3.0)
    clutter = [p for x in np.arange(5.6, 9.0, 0.5) for p in _block(x, 1.0, x + 0.1, 5.0)]
    c = fs.FloorWorld(_layout(extra=clutter), (5.0, 3.0, 0.0), roll_off_deg=0.0)
    c.sleep(3.0)
    assert w.quality() >= 70 and c.quality() <= 55 and c.low_fit_s > 0, (w.quality(), c.quality())


# ---- the walker's own code in it ------------------------------------------------------------

def test_out_of_the_dock_round_the_room_and_home_facing_out():
    """The real Walker: undock, two legs, and the way home of 16 Nov (to the spot on the
    dock's line, turned out, reversed) -- on a base that rolls 2.3 deg off what the board reads."""
    lay = _layout(dock=(1.0, 3.0, 0.0))
    doc = {"dock": {"pose": [1.0, 3.0, 0.0], "back_from_m": 1.9, "axis_offset_deg": 2.3}}
    steps = [{"do": "undock"}, {"to": [6.0, 4.5], "face": 90, "speed": 0.3}, {"to": [7.0, 1.6], "speed": 0.3},
             {"do": "dock"}]
    for seed in range(4):
        res = fs.run_walk(lay, doc, steps, (1.0, 3.0, 0.0), seed=seed)
        assert res["end"] == "done" and res["on_dock"], (seed, res["reason"], res["said"][-3:])
        assert abs(res["aside_m"]) <= fs.MAX_DOCK_ASIDE_M and abs(res["crooked_deg"]) <= fs.MAX_DOCK_CROOKED_DEG, res
        assert res["min_arm_gap"] > 0.3


def test_the_preflights_bounds():
    """judge(): what a set of runs is held to."""
    ok = {"end": "done", "reason": "", "on_dock": True, "aside_m": 0.01, "crooked_deg": 0.4, "min_arm_gap": 0.3,
          "min_person_gap": 9.0, "min_hazard": {"bump": 1.2}, "touched": []}
    hz = [{"id": "bump", "x": 0, "y": 0, "radius_m": 0.75}]
    assert fs.judge("w", [ok] * 10, hz, plain=True, ends_docked=True) == []
    assert "arms" in fs.judge("w", [ok] * 9 + [dict(ok, min_arm_gap=0.01)], hz, plain=True, ends_docked=True)[0]
    gave_up = dict(ok, end="stop", reason="step 3: ended 0.91 m from the point (the board gave up on it)", on_dock=False)
    assert "arrived 9 of 10 (board gave up x1)" in fs.judge("w", [ok] * 9 + [gave_up], hz, plain=True, ends_docked=True)[0]
    assert "floor hazard bump" in fs.judge("w", [dict(ok, min_hazard={"bump": 0.5})], hz, plain=True, ends_docked=True)[0]
    assert "aside" in fs.judge("w", [dict(ok, aside_m=0.095, crooked_deg=1.7)], hz, plain=True, ends_docked=True)[0]
    assert "off the dock" in fs.judge("w", [dict(ok, on_dock=False)], hz, plain=True, ends_docked=True)[0]
    # with something put in the way the walk is not asked to arrive
    assert fs.judge("w", [gave_up], hz, plain=False, ends_docked=False) == []


# ---- against the real nights (data under .tmp/, gitignored) --------------------------------

_NIGHT_SKIP = ("the 16 Nov lidar/pose files (.tmp/map-check/20261116-floor1-dock, .tmp/floor1-20261116) or "
               "data/robot/waypoints/floor1.json are not on this machine: NOT checked here -- that the simulator "
               "reproduces the real night (the old way home failing, the new one docking) and the pre-flight "
               "of today's paths")


@pytest.fixture(scope="module")
def night():
    """16 Nov's furniture with the dock where it stood THAT night: the replays of that night."""
    lay = fs.load_layout("20261116", dock="night")
    if lay is None or not (ROOT / "data/robot/waypoints/floor1.json").exists():
        pytest.skip(_NIGHT_SKIP)
    return lay


@pytest.fixture(scope="module")
def today():
    """16 Nov's furniture with the dock where today's files put it (floor1.json, or the pose
    re-learnt at the robot): what the pre-flight walks today's paths in."""
    if not (ROOT / "data/robot/waypoints/floor1.json").exists():
        pytest.skip(_NIGHT_SKIP)
    lay = fs.load_layout()
    if lay is None:
        pytest.skip(_NIGHT_SKIP)
    return lay


OLD_WAY = {"dock": {"pose": [-7.11, -6.78, 169.6], "approach": [-9.0, -6.2], "estimated": False}, "min_quality": 40}


def test_the_night_of_16_nov_the_old_way_home_fails_from_in_front_of_the_nook(night):
    """Real, 21:11: to_dock twice from (-7.97, -6.55) facing the nook = CANNOT_REACH_TARGET, no
    movement. The simulator must not dock from there the old way."""
    runs = [fs.run_walk(night, OLD_WAY, [{"do": "dock"}], (-7.97, -6.55, -18.0), seed=k, settle_s=6.0, nook_slides=1.0)
            for k in range(6)]
    assert sum(r["on_dock"] for r in runs) <= 1, [(r["end"], r["reason"][:60]) for r in runs]
    assert all(r["max_err_m"] > 0.3 for r in runs)                        # the board's pose had slid


def test_the_night_of_16_nov_out_and_back_onto_the_dock_works_as_it_did(night):
    """Real, 20:34: 0.45 m out and back in by the pre-dock spot, docked 0.6 cm aside."""
    steps = fs.floor1_paths()["dock_out_in"]
    runs = [fs.run_walk(night, OLD_WAY, steps, night.dock, seed=k) for k in range(6)]
    assert sum(r["on_dock"] for r in runs) >= 5, [(r["end"], r["reason"][:60]) for r in runs]


def test_todays_way_home_docks_from_the_rehearsal_round(today):
    """dock.back_from_m of floor1.json: the whole rehearsal round and home, within the bounds --
    on the dock where floor1.json says it is today (20 Nov: the sim kept 16 Nov's dock after
    the real one was re-learnt 13 cm away on 19 Nov, and judged this round 10 cm aside)."""
    doc = fs.route_doc(today)
    if not doc["dock"].get("back_from_m"):
        pytest.skip("floor1.json's dock has no back_from_m on this machine: the facing-out way home is NOT checked here")
    # the walk docks to the file's pose; the layout's (physical) dock is the same pose -- or the
    # test would pass or fail for the wrong reason
    assert tuple(doc["dock"]["pose"]) == pytest.approx(today.dock), (doc["dock"]["pose"], today.dock, today.dock_from)
    runs = [fs.run_walk(today, doc, fs.floor1_paths()["event_rehearsal"], today.dock, seed=k) for k in range(4)]
    assert fs.judge("event_rehearsal", runs, today.hazards, plain=True, ends_docked=True) == [], \
        [(r["end"], r["reason"][:70], r["aside_m"]) for r in runs]
    assert all(v >= 0.75 for r in runs for v in r["min_hazard"].values())  # never over the floor bump


def test_leaving_the_dock_for_the_entrance_side_goes_by_the_corridor(today):
    """The pre-flight's first finding: straight from the pre-dock spot to the entrance side
    the arms pass the furniture at (-8.0, -4.8) within a few cm (0.04 m at the least in 20
    runs); by the dock's approach point, 0.19 m.

    Measured AWAY from the dock (> 0.6 m): with today's dock (19 Nov, 13 cm south-east of 16
    Nov's) set in 16 Nov's furniture, the arms leaving the dock pass the nook's thing at
    (-6.97, -7.12) at ~0.10 m, 0.3 m out -- the same for both ways out, and the nook was
    furnished again on 19 Nov. That gap is held to the pre-flight's own bound here."""
    from app import event_host as eh

    doc = fs.route_doc(today, event=True)
    target = [{"do": "undock"}, {"to": [-8.6, 0.2], "speed": 0.3}]
    straight = [fs.run_walk(today, doc, target, today.dock, seed=k) for k in range(3)]
    assert min(fs.min_arm_gap_beyond(r, today.dock, 0.6) for r in straight) < 0.10, [r["tight"] for r in straight]
    by_corridor = eh.leave_the_dock(target, today.dock[:2], dict(doc["dock"], leave_by_approach=True))
    assert len(by_corridor) == 3
    clear = [fs.run_walk(today, doc, by_corridor, today.dock, seed=k) for k in range(3)]
    assert all(r["end"] == "done" for r in clear)
    assert min(fs.min_arm_gap_beyond(r, today.dock, 0.6) for r in clear) > 0.15, [r["tight"] for r in clear]
    assert min(r["min_arm_gap"] for r in clear) >= fs.MIN_ARM_GAP_M, [(r["min_arm_gap"], r["arm_gap_at"]) for r in clear]


def test_a_small_preflight_of_the_event_walks_out_of_the_dock_is_inside_the_bounds(today):
    if not fs.route_doc(today)["dock"].get("leave_by_approach"):
        pytest.skip("floor1.json's dock has no leave_by_approach on this machine: the event walks out of the dock are NOT checked here")
    out_of_dock = [n for n in fs.event_plan(today)["walks"] if n.startswith("event dock ->")]
    if len(out_of_dock) < 3:
        pytest.skip("the event file has %d spot(s) with a position on this machine: the pre-flight of the walks out of the "
                    "dock is NOT checked here" % len(out_of_dock))
    res = fs.preflight(runs=2, only="event dock ->", layout=today, adverse=False, out=lambda *_: None)
    broken = [b for b in res["broken"] if b[0] not in fs.KNOWN_OUTSIDE]      # listed with why (the globe, 25 Nov)
    assert len(res["walks"]) == len(out_of_dock) and broken == [] and res["unexpected"] == [], broken


# ---- the pre-flight as a gate (inline files: nothing here reads .tmp/ or floor1.json) -------

@pytest.fixture
def gate(tmp_path, monkeypatch):
    """A 10 x 6 m room with its own route file, event file and ceremony file."""
    lay = _layout(name="gate", dock=(1.0, 3.0, 0.0))
    route = tmp_path / "route.json"
    route.write_text(json.dumps({
        "dock": {"pose": [1.0, 3.0, 0.0], "back_from_m": 1.9},
        "paths": {"good": {"steps": [{"do": "undock"}, {"to": [6.0, 3.0], "speed": 0.3}]},
                  "bad": {"steps": [{"do": "undock"}, {"to": [9.8, 3.0], "speed": 0.3}]}}}), encoding="utf-8")
    event = tmp_path / "event.json"
    event.write_text(json.dumps({
        "route_file": str(route),
        "spots": {"dock": {"dock": True, "name_th": "แท่น"}, "a": {"pose": [6.0, 4.0, 90], "name_th": "A"},
                  "b": {"pose": None, "name_th": "B"}, "c": {"pose": None, "name_th": "C"}},
        "scenes": [{"id": "s1", "spot": "a"}, {"id": "s2", "spot": "b"}, {"id": "s3", "spot": "c", "forward_m": 1.0}]},
        ensure_ascii=False), encoding="utf-8")
    cer = tmp_path / "ceremony.json"
    cer.write_text(json.dumps({"order": [{"n": 1, "steps": [{"do": "manual"}, {"do": "walk", "spot": "b"}]},
                                         {"n": 2, "steps": [{"do": "walk", "spot": "a"}]}]}), encoding="utf-8")
    monkeypatch.setattr(fs, "ROUTE_FILE", route)
    monkeypatch.setattr(fs, "EVENT_FILE", event)
    monkeypatch.setattr(fs, "CEREMONY_FILE", cer)
    monkeypatch.setattr(fs, "_REAL_FRAMES", [])                           # no real frames belong to this room
    return lay, tmp_path


def _quiet(*_):
    pass


def test_nothing_checked_is_an_error_never_a_pass(gate, monkeypatch):
    """Review 17 Nov: `--only nosuchwalk` ended "0 walk(s), 0 outside the bounds", exit 0; so
    did an unreadable route file (its paths were taken as none, its dock as a default)."""
    lay, tmp = gate
    with pytest.raises(fs.InputError, match="no walk matches"):
        fs.preflight(runs=1, only="nosuchwalk", layout=lay, adverse=False, out=_quiet)
    fs.ROUTE_FILE.write_text("{ not json", encoding="utf-8")
    with pytest.raises(fs.InputError, match="route file is not JSON"):
        fs.preflight(runs=1, layout=lay, adverse=False, out=_quiet)
    with pytest.raises(fs.InputError, match="is not JSON"):
        fs.floor1_paths()
    monkeypatch.setattr(fs, "ROUTE_FILE", tmp / "gone.json")
    with pytest.raises(fs.InputError, match="route file"):
        fs.preflight(runs=1, layout=lay, adverse=False, out=_quiet)
    with pytest.raises(fs.InputError):
        fs.route_doc(lay)
    monkeypatch.setattr(fs, "ROUTE_FILE", tmp / "route.json")
    (tmp / "route.json").write_text(json.dumps({"dock": {"pose": [1.0, 3.0, 0.0]}, "paths": {}}), encoding="utf-8")
    with pytest.raises(fs.InputError, match="no paths"):
        fs.preflight(runs=1, layout=lay, adverse=False, out=_quiet)
    monkeypatch.setattr(fs, "EVENT_FILE", tmp / "no_event.json")
    with pytest.raises(fs.InputError, match="event file"):
        fs.event_plan(lay)
    # and the command says so with its exit code
    printed = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: printed.append(" ".join(str(x) for x in a)))
    assert fs.main(["preflight", "--only", "nosuchwalk", "--runs", "1", "--no-adverse"]) == 2
    assert any("nothing was checked" in line for line in printed)


def test_a_walk_outside_the_bounds_fails_unless_it_is_on_the_known_list_and_the_list_cannot_rot(gate):
    """Review 17 Nov: the exit code was always 1 (two legacy paths), so a new break only turned
    "2 outside" into "3". Known ones are named with their reason and reported; anything else
    outside the bounds is `unexpected`; a known one that is inside again is said."""
    lay, _ = gate
    res = fs.preflight(runs=2, only="path ", layout=lay, adverse=False, out=_quiet, known={})
    assert [n for n, _ in res["unexpected"]] == ["path bad"] and res["known_outside"] == [], res["broken"]
    lines = []
    res = fs.preflight(runs=2, only="path ", layout=lay, adverse=False, out=lines.append,
                       known={"path bad": ("its point is in the wall, since ever", (r"^arrived \d+ of \d+ \(board gave up",),
                                           {"min_arrived": 0.0}),
                              "path good": ("was broken once", (r".",))})
    assert res["unexpected"] == [] and [n for n, _ in res["known_outside"]] == ["path bad"], res["broken"]
    assert res["known_now_inside"] == ["path good"]
    text = "\n".join(lines)
    assert "KNOWN outside the bounds: path bad" in text and "its point is in the wall" in text
    assert "INSIDE the bounds now: path good" in text
    assert text.startswith("WHAT THIS DOES NOT SHOW") and "upper bound" in text and "ASSUMED planner margin" in text
    # review 21 Nov: the list matched NAMES, so a known walk outside the bounds for a NEW reason passed as known
    # (reception_tour 20/20 -> 13/20, lobby_survey over a hazard). A reason its entry does not name is NEW.
    res = fs.preflight(runs=2, only="path bad", layout=lay, adverse=False, out=lines.append,
                       known={"path bad": ("its arms by a wall", (r"^arms -?0\.\d\d m from something solid at",))})
    assert [n for n, _ in res["unexpected"]] == ["path bad"] and res["known_outside"] == [], res
    assert res["unexpected"][0][1][0].startswith("arrived 0 of 2"), res["unexpected"]
    assert "a KNOWN walk, a reason its entry does not name" in "\n".join(lines)
    # re-review 21 Nov: "arrived \d+ of \d+" took ANY count as known -- a walk known to arrive 13 of 20 arriving 0 of 2
    # passed. The count is known down to the entry's own share; fewer is NEW; with no share, an "arrived" is never known.
    for share, new in ((0.5, True), (0.0, False), (None, True)):
        entry = ("arrives half", (r"^arrived \d+ of \d+ \(board gave up",)) + (() if share is None else ({"min_arrived": share},))
        res = fs.preflight(runs=2, only="path bad", layout=lay, adverse=False, out=_quiet, known={"path bad": entry})
        assert bool(res["unexpected"]) == new and bool(res["known_outside"]) != new, (share, res["unexpected"])
    # and the live list: reception_tour is known down to 13 of 20 (21 Nov), not 0 of 2
    assert fs.known_reasons("path reception_tour", ["arrived 13 of 20 (board gave up x7)"], fs.KNOWN_OUTSIDE) == []
    assert fs.known_reasons("path reception_tour", ["arrived 0 of 2 (board gave up x2)"], fs.KNOWN_OUTSIDE) != []


def test_what_has_no_position_is_listed_not_dropped(gate):
    """Review 17 Nov: spots with no pose (the stage, the tables) were dropped without a word.
    Every spot, scene walk and ceremony walk that cannot be checked is named."""
    lay, _ = gate
    plan = fs.event_plan(lay)
    assert set(plan["where"]) == {"dock", "a"}
    nc = "\n".join(plan["not_checked"])
    for want in ("spot b (B): no position", "spot c (C): no position", "scene s2 walks to b: no position",
                 "scene s3 walks to c: no position", "ceremony part 1 walks to b: no position"):
        assert want in nc, (want, nc)
    # s3's spot has no position, so its `forward_m` is the walk the event takes: walked since 21 Nov
    assert "event s3: 1.0 m ahead out of the dock" in plan["walks"] and "straight ahead" not in nc
    # the ceremony's walk that CAN be checked is one of the walks, and so is its way home
    assert any(n.startswith("ceremony 2:") and n.endswith("-> a") for n in plan["walks"]), list(plan["walks"])
    assert "ceremony end: a -> dock" in plan["walks"]
    lines = []
    res = fs.preflight(runs=1, only="event dock -> a", layout=lay, adverse=False, out=lines.append, known={})
    assert "NOT CHECKED (%d):" % len(plan["not_checked"]) in "\n".join(lines) and res["not_checked"] == plan["not_checked"]


def test_positions_a_person_gives_are_walked_and_each_spot_is_looked_at_on_its_own(gate, tmp_path):
    lay, _ = gate
    marked = tmp_path / "marked.json"
    marked.write_text(json.dumps({"floor": 1, "frame": "robot map (board coordinates)", "marked": [
        {"id": "b", "name": "B marked", "x": 7.5, "y": 2.0, "face_deg": 180, "nearest_thing_m": 2.0},
        {"id": "custom_1", "name": "by the wall", "x": 9.7, "y": 3.0, "face_deg": None}],
        "not_marked": ["c"]}), encoding="utf-8")
    spots = fs.load_spots(marked)
    assert spots["b"] == {"x": 7.5, "y": 2.0, "face_deg": 180.0, "name": "B marked", "source": "marked"}
    plan = fs.event_plan(lay, spots)
    assert plan["standing"] == ["custom_1"] and "b" in plan["where"]
    assert not any("spot b" in line or "to b:" in line for line in plan["not_checked"])
    assert any("spot c" in line for line in plan["not_checked"])
    for want in ("event dock -> b", "event a -> b", "event b -> dock", "ceremony end: a -> dock"):
        assert want in plan["walks"], (want, list(plan["walks"]))
    assert any(n.startswith("ceremony 1:") and n.endswith("-> b") for n in plan["walks"])
    assert not any("custom_1" in n for n in plan["walks"])                 # a standing spot: no file walks to it
    lines = []
    res = fs.preflight(runs=2, only="-> b", layout=lay, adverse=False, out=lines.append, spots=spots, known={})
    assert res["unexpected"] == [] and len(res["walks"]) >= 3, res["broken"]
    rows = {r["id"]: r for r in res["spots"]}
    assert rows["b"]["arm_gap_m"] > 0.5 and rows["b"]["turn_gap_m"] > 0.5 and not rows["b"]["board_refuses"]
    wall = rows["custom_1"]                                               # 0.3 m from the wall: no body, no turn, no target
    assert wall["board_refuses"] and wall["turn_gap_m"] < 0 and wall["arm_gap_m"] < fs.MIN_ARM_GAP_M
    text = "\n".join(lines)
    assert "the board would refuse it as a target" in text and "no room for a full turn" in text
    # the robot's own recorded-spots file is read too; a file that is neither is an error
    rec = tmp_path / "event.spots.json"
    rec.write_text(json.dumps({"spots": {"b": {"pose": [7.5, 2.0, 180.0], "recorded_at": "x"}, "c": {"pose": None}}}), encoding="utf-8")
    assert fs.load_spots(rec) == {"b": {"x": 7.5, "y": 2.0, "face_deg": 180.0, "name": "b", "source": "recorded on the robot"}}
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"points": []}), encoding="utf-8")
    with pytest.raises(fs.InputError, match="neither"):
        fs.load_spots(bad)
    with pytest.raises(fs.InputError):
        fs.load_spots(tmp_path / "not_there.json")


def test_the_sims_dock_is_where_the_robot_docks_today(gate):
    """20 Nov: the sim kept 16 Nov's dock after the real one was re-learnt 13 cm away (19 Nov),
    so today's way home "docked 10 cm aside" in the sim while the robot sat on its contacts.
    The dock of the pre-flight's layout is floor1.json's -- or the pose the staff page re-learnt
    at the robot ("วัดแท่นใหม่", the spots file beside the event file, or one pulled from the
    robot) -- and the event walks dock where the robot's own route documents send them."""
    _, tmp = gate
    lay = _layout(name=fs.CURRENT, dock=(9.0, 5.0, 90.0))                  # whatever the night had
    fs.apply_todays_dock(lay)
    assert lay.dock == (1.0, 3.0, 0.0) and lay.relearnt is None and "floor1.json" in lay.dock_from
    (tmp / "event.spots.json").write_text(json.dumps(
        {"spots": {}, "dock": {"pose": [1.0, 3.12, 2.0], "recorded_at": "2026-11-20 08:00", "was": [1.0, 3.0]}}),
        encoding="utf-8")
    fs.apply_todays_dock(lay)
    assert lay.dock == (1.0, 3.12, 2.0) and lay.relearnt == (1.0, 3.12, 2.0) and "re-learnt" in lay.dock_from
    assert fs.route_doc(lay, event=True)["dock"]["pose"] == [1.0, 3.12, 2.0]
    assert fs.route_doc(lay)["dock"]["pose"] == [1.0, 3.0, 0.0]            # a floor1.json path reads the file as it is
    lines = fs.dock_lines(lay)
    assert any("floor1.json's paths" in line and "12.0 cm" in line for line in lines), lines
    assert not any("the event walks" in line for line in lines), lines
    # an event walk out and home sits on the contacts of the dock that is there
    steps = [{"do": "undock"}, {"to": [6.0, 3.6], "speed": 0.3}, {"do": "dock"}]
    for seed in range(2):
        res = fs.run_walk(lay, fs.route_doc(lay, event=True), steps, lay.dock, seed=seed, loc=False, roll_off_deg=0.0)
        assert res["end"] == "done" and res["on_dock"] and abs(res["aside_m"]) <= fs.MAX_DOCK_ASIDE_M, res["reason"]
    # a spots file pulled from the robot and given on the command line wins; one with the dock
    # alone is a position, not "nothing in it"
    pulled = tmp / "pulled.spots.json"
    pulled.write_text(json.dumps({"spots": {}, "dock": {"pose": [1.0, 2.9, -1.0], "recorded_at": "x"}}), encoding="utf-8")
    assert fs.apply_todays_dock(lay, pulled).dock == (1.0, 2.9, -1.0)
    assert fs.load_spots(pulled) == {}
    # what the robot cannot read it does not walk by, and neither does the sim
    (tmp / "event.spots.json").write_text("{ half a file", encoding="utf-8")
    assert fs.apply_todays_dock(lay).dock == (1.0, 3.0, 0.0)


def test_a_touch_with_a_thing_in_the_way_is_counted_in_the_summary(gate, monkeypatch):
    """Review 17 Nov: a table that is not on the board's map is the event's own situation; a
    touch there was a note at the end of a line. It is a total of its own in the result and in
    the report -- and stays out of the exit code only for the reason its constant gives."""
    lay, _ = gate
    real = fs.run_walk

    def run_walk(layout, doc, steps, start, **kw):
        res = real(layout, doc, steps, start, **{k: v for k, v in kw.items() if k not in ("things", "people")})
        if kw.get("things"):
            res["min_person_gap"] = -0.01                                 # the arms on the thing left on the leg
        return res

    monkeypatch.setattr(fs, "run_walk", run_walk)
    lines = []
    res = fs.preflight(runs=5, only="path good", layout=lay, adverse=True, out=lines.append, known={})
    assert res["unexpected"] == [], res["broken"]                          # the plain walk is inside the bounds
    assert res["touches"] == {"thing left on a leg": ["path good"]}, res["touches"]
    assert any("1 of 1 walks TOUCH with 'thing left on a leg'" in line for line in lines)
    assert fs.ADVERSE_TOUCH_SETS_EXIT is False
    monkeypatch.setattr(fs, "ADVERSE_TOUCH_SETS_EXIT", True)
    res = fs.preflight(runs=5, only="path good", layout=lay, adverse=True, out=lambda *_: None, known={})
    assert [n for n, _ in res["unexpected"]] == ["path good"]


# ---- 21 Nov: the debut's walk ahead, the counter's top, the event day in one world ----------------
# (owner 20 Nov: "เรื่องการเดินก็เช็คแล้วทำในหน้าจำลอง" -- the debut out of the "box" in the pageant pose,
# right to the counter where Khun Miki speaks, 60-70 cm from the table, the handshake; then home)

def _pageant_gate(gate):
    """The gate's event file with s3 the debut: a metre ahead, the pageant."""
    lay, tmp = gate
    doc = json.loads(fs.EVENT_FILE.read_text(encoding="utf-8"))
    for sc in doc["scenes"]:
        if sc["id"] == "s3":
            sc["pageant"] = True
    fs.EVENT_FILE.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return lay, tmp


def test_the_debut_walk_ahead_is_walked_by_the_real_forward_with_the_pageant_arm(gate):
    """Until 21 Nov the debut's `forward_m` was a "not simulated" line. Now it is a walk out of the dock,
    the real board_walk `forward` with the pageant's side watch, and the arm's own gap is judged."""
    lay, _ = _pageant_gate(gate)
    plan = fs.event_plan(lay)
    name = "event s3: 1.0 m ahead out of the dock, the pageant arm out"
    assert name in plan["walks"] and name in plan["pageant"], list(plan["walks"])
    steps, start = plan["walks"][name]
    assert steps == fs.debut_steps({"forward_m": 1.0, "pageant": True}) and tuple(start) == lay.dock
    assert steps[0]["right_clear_m"] > 0.0
    assert not any("walk straight ahead" in line for line in plan["not_checked"]), plan["not_checked"]
    assert "scene s3 walks to c: no position" in "\n".join(plan["not_checked"])     # the stage still has none
    res = fs.preflight(runs=2, only="ahead out of the dock", layout=lay, adverse=False, out=_quiet, known={})
    row = res["walks"][name]
    assert res["broken"] == [] and row["arrived"] == 2, res["broken"]
    assert row["min_side_arm_gap"] > 0.8                                  # an empty room: on the dock the wall behind, 0.84


def test_the_pageant_arm_is_judged_against_what_stands_above_the_lidar(gate):
    """A thing at shoulder height, past the side watch's strip at the lidar's plane but inside the arm's
    reach (PAGEANT_ARM_BOX): the walk goes, and the pre-flight says the arm meets it."""
    lay, _ = _pageant_gate(gate)
    # she walks +x from (1, 3) along y = 3: her right is -y; the arm reaches to y = 3 - 0.60
    lay.put_above([{"id": "shelf", "box": (1.5, 1.9, 2.30, 2.45)}])
    name = "event s3: 1.0 m ahead out of the dock, the pageant arm out"
    res = fs.preflight(runs=1, only="ahead out of the dock", layout=lay, adverse=False, out=_quiet, known={})
    row = res["walks"][name]
    assert row["arrived"] == 1                                            # the lidar never saw it: nothing held her
    assert row["min_side_arm_gap"] < 0.0 and row["min_arm_gap"] > fs.MIN_ARM_GAP_M, row   # the arm at rest is clear
    assert [n for n, _ in res["unexpected"]] == [name] and "pageant arm" in res["unexpected"][0][1][0], res["unexpected"]


def test_the_debut_walk_ahead_stands_for_a_thing_on_its_arm_side():
    """The walker's own side watch (right_clear_m) runs in the sim: a thing at the lidar's plane 0.5 m right
    of her line holds the walk, and the patience ends it there."""
    lay = _layout(extra=_block(1.4, 2.45, 2.2, 2.55), name="gate", dock=(1.0, 3.0, 0.0))
    steps = fs.debut_steps({"forward_m": 1.0, "pageant": True})
    res = fs.run_walk(lay, {"dock": {"pose": [1.0, 3.0, 0.0]}, "events": True, "patience_s": 10}, steps, (1.0, 3.0, 0.0),
                      roll_off_deg=0.0, pageant=True)
    assert res["end"] == "stop" and "on the right" in res["reason"], res["reason"]
    plain = fs.run_walk(lay, {"dock": {"pose": [1.0, 3.0, 0.0]}}, [dict(steps[0], right_clear_m=0.0)], (1.0, 3.0, 0.0),
                        roll_off_deg=0.0)
    assert plain["end"] == "done"                                         # without the watch she walks past it


def test_debut_steps_are_what_the_event_writes(tmp_path, monkeypatch):
    """floor_sim walks what event_host._walk_ahead hands event_walk -- read off the real call."""
    import asyncio

    from app import event_host as eh
    from app import event_walk
    from app.config import settings

    doc = json.loads((ROOT / "data/events/floor1_event.json").read_text(encoding="utf-8"))
    route = tmp_path / "route.json"
    route.write_text(json.dumps({"dock": {"pose": [-7.1, -6.84, 168.0], "approach": [-9.0, -6.2]}, "paths": {}}),
                     encoding="utf-8")
    doc["route_file"] = str(route)
    path = tmp_path / "floor1_event.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "event_file", str(path))
    monkeypatch.setattr(settings, "event_walk_enabled", False)
    eh._cache.update(key=None)
    got = []

    async def start(spot, name, steps, **kw):
        got.append(steps)
        return {"ok": True}

    monkeypatch.setattr(event_walk, "start", start)
    debut = next(s for s in eh.load()["scenes"] if s.get("forward_m") and s.get("pageant"))
    asyncio.run(eh._walk_ahead(debut, 1, None))
    eh._cache.update(key=None)
    assert got == [fs.debut_steps(debut)], got


def test_the_counter_top_is_for_the_arms_only(monkeypatch):
    """ABOVE_LIDAR: the counter's top (owner 20 Nov: the table the arm hit on 28 Oct) -- no scan returns
    from it, the base is not stopped by it, the arms' rectangle and the handshake meet it."""
    monkeypatch.setattr(fs, "FURNITURE_RETURNS", 1.0)
    lay = _layout()
    lay.put_above([{"id": "top", "box": (6.0, 6.4, 2.0, 4.0)}])
    w = fs.FloorWorld(lay, (5.0, 3.0, 0.0), roll_off_deg=0.0)
    a, r = _bearing(w.scan())
    assert float(r[np.abs(a) < 5].min()) > 4.5                            # the lidar sees the wall, not the top
    w.sleep(0.2)
    assert w.min_arm_gap == pytest.approx(1.0 - 0.30, abs=0.03)           # 6.0 - (5.0 + 0.30)
    assert w.min_base_gap > 1.0                                           # the room's walls, not the top
    at = fs._at_the_counter(w)
    assert at["front_to_top"] == pytest.approx(0.70, abs=0.03)
    assert at["hand_to_top"] == pytest.approx(0.70 - fs.HANDSHAKE_PAST_FRONT_M, abs=0.03)
    res = fs.run_walk(lay, {"dock": {"pose": [1.0, 3.0, 0.0]}}, [{"to": [7.5, 3.0], "speed": 0.3}], (4.0, 3.0, 0.0),
                      roll_off_deg=0.0, loc=False)
    assert res["end"] == "done" and res["min_arm_gap"] < 0.0, res        # under it the base went, the arms did not fit


def test_the_counter_spot_is_the_event_files_beside_host(monkeypatch):
    """COUNTER_SPOT (the floor1.json ways home start there) is the event file's `beside_host` of 23 Nov."""
    # 24 Nov evening: the owner moved Khun Miki's spot and the walk out grew to 1.3 m; this holds the numbers of 23 Nov
    monkeypatch.setattr(fs, "EVENT_FILE", ROOT / "tests" / "data" / "floor1_event_rehearsed.json")
    assert fs.counter_spot() == pytest.approx(fs.COUNTER_SPOT)


def test_the_paths_that_start_out_of_the_box_or_in_front_of_the_dock_start_there(gate):
    lay, _ = gate
    assert fs.starts_for("event_counter_test", [{"forward": 1.0}, {"to": [5.0, 3.0]}], lay) == [lay.dock]
    starts = fs.starts_for("dock_via_reception", [{"to": [5.0, 3.0]}, {"do": "dock"}], lay)
    assert starts[1] == fs.COUNTER_SPOT
    assert starts[0] == pytest.approx((1.0 + 1.76, 3.0, 0.0))             # n20k: 0.14 m in front of the 1.9 m spot
    assert any(s == pytest.approx((2.76, 3.0, 0.0)) for s in fs.starts_for("to_dock", [{"do": "dock"}], lay))


def test_the_dock_area_the_lidar_saw_empty_is_empty_today_only(today, night):
    """SEEN_EMPTY: 20 Nov's scans on the dock saw nothing within 1.2 m in the lidar's arc; the 16 Nov truth
    there held the debut's walk ahead on its arm's side (the real one walked its metre in 10 s, n20j)."""
    x, y, deg = fs.SEEN_EMPTY[0]["at"]

    def inside(lay):
        p = lay.solid
        a = np.degrees(np.arctan2(p[:, 1] - y, p[:, 0] - x)) - deg
        a = (a + 180.0) % 360.0 - 180.0
        return int(((np.hypot(p[:, 0] - x, p[:, 1] - y) < 1.2) & (a > -101) & (a < 108)).sum())

    assert today.cleared > 0 and inside(today) == 0
    assert inside(night) > 0                                              # the replays of 16 Nov keep that night
    # behind-right of the dock, the old line at x -6.83: map_check of 20 Nov saw THROUGH y -6.67..-6.57 (phantom #26,
    # 5 runs) -- out; y -6.55..-6.07 nobody has seen either way -- it stays, for the owner to look at
    x0, x1, y0, y1 = fs.SEEN_EMPTY[1]["box"]

    def cells(lay, ya, yb):
        p = lay.solid
        return int(((p[:, 0] >= x0) & (p[:, 0] <= x1) & (p[:, 1] >= ya) & (p[:, 1] <= yb)).sum())

    assert cells(today, y0, y1) == 0 and cells(night, y0, y1) > 0
    assert cells(today, -6.55, -6.05) > 0


def test_the_event_day_in_one_world(today, tmp_path, monkeypatch):
    """The owner's order (20 Nov): out of the dock a metre ahead, the pageant arm out; to the counter by
    the dock's corridor, 60-70 cm from the table's top, the handshake's hand clear of it; home both ways,
    on the contacts. A gate, not a measurement (the arm sizes are assumed)."""
    # 24 Nov evening: the owner moved Khun Miki's spot and the walk out grew to 1.3 m; this holds the numbers of 23 Nov
    monkeypatch.setattr(fs, "EVENT_FILE", ROOT / "tests" / "data" / "floor1_event_rehearsed.json")
    seqs = fs.event_day_sequences(today)
    assert len(seqs) == 2, list(seqs)
    res = fs.event_day(2, 0, layout=today, out=_quiet, png_dir=tmp_path)
    for name, sm in res.items():
        # Until 23 Nov the one thing outside the bounds: the pageant arm ON the dock, 0.03 m from the old line at
        # x -6.83 (the arm rose there 4.5 s before the wheels). Since 23 Nov (owner: "หุ่นอยู่ในกล่อง ... เดินออกมาก่อน
        # จึงค่อยยกมือ") she walks out of the box with the arms down and the arm rises where she arrived, a metre out:
        # nothing outside the bounds, nothing known
        assert sm["bad"] == [] and sm["new"] == [], (name, sm["bad"])
        debut = next(r for w, r in sm["by_walk"].items() if w.startswith("card 1 debut:"))
        assert "arms down" in next(w for w in sm["by_walk"] if w.startswith("card 1 debut:"))
        assert debut["min_side_arm_gap"] > fs.MIN_ARM_GAP_M and 0.9 < math.hypot(
            debut["side_arm_at"][0] - today.dock[0], debut["side_arm_at"][1] - today.dock[1]) < 1.1, debut
        for what, row in sm["by_walk"].items():
            assert row["done"] == row["n"] == 2, (name, what, row)
        assert sm["docked"] == 2
        # the board's MoveTo miss at the counter (assumed model): 0.51-0.77 m over 20 runs on 23 Nov, 0.53-0.83 before the
        # debut stood 22 s waving out of the box (the same code, another random stream) -- the tour's own floor
        assert fs.TOUR_BACK_FRONT_M[0] <= sm["front_to_top"][0] and sm["front_to_top"][1] <= 0.85, sm["front_to_top"]
        assert sm["hand_to_top"][0] > fs.MIN_ARM_GAP_M and sm["counter_turn_gap"] > fs.MIN_ARM_GAP_M
        assert Path(sm["png"]).stat().st_size > 10_000
    # the walk to the counter is the event layer's: out by the dock's corridor first
    legs = seqs[next(iter(seqs))]
    steps = legs[1]["steps"](fs.dock_line_pose(today, 1.0))
    assert [s.get("to") for s in steps if s.get("to")][0] == pytest.approx(fs.route_doc(today)["dock"]["approach"])


def test_the_event_day_is_the_staff_pages_cards(today, tmp_path, monkeypatch):
    """Owner 21 Nov ("ลองซีนในจำลองตามฉากที่เดินให้หน่อย ... ทำการ์ดกดเป็นซีนวันจริง"): the simulated day is the
    cards as pressed -- card 1 the debut, card 2 the counter's handshake (its own spot), the dock's card home.
    The counter's card is the scene at beside_host with the handshake: moved elsewhere (or a scene without the
    handshake standing there) = no event day, never beside_host taken behind the card's back; beside_host moved =
    the walk moves; no counter card = no event day."""
    import json

    legs = fs.event_day_sequences(today)[next(iter(fs.event_day_sequences(today)))]
    assert [leg["what"].split(":")[0] for leg in legs] == ["card 1 debut", "card 2 counter_handshake",
                                                          "card 3 guest_handshake", "card 11 rest"]
    doc = json.loads(fs.EVENT_FILE.read_text(encoding="utf-8"))
    card = next(s for s in doc["scenes"] if s["id"] == "counter_handshake")
    # the card walks to another spot: it is no longer the counter's card
    doc["spots"]["counter_moved"] = {"name_th": "x", "steps": [{"to": [-8.9, -4.6], "face": 0, "speed": 0.45}]}
    card["spot"] = "counter_moved"
    # ... and a scene with no handshake standing at beside_host is not the counter's card
    next(s for s in doc["scenes"] if s["id"] == "welcome")["spot"] = "beside_host"
    path = tmp_path / "event.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(fs, "EVENT_FILE", path)
    assert fs.event_day_sequences(today) == {}            # the counter card is the one at beside_host with the handshake
    card["spot"] = "beside_host"
    next(s for s in doc["scenes"] if s["id"] == "welcome")["spot"] = "reception"
    doc["spots"]["beside_host"]["steps"][0]["to"] = [-8.9, -4.6]
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    legs = fs.event_day_sequences(today)[next(iter(fs.event_day_sequences(today)))]
    steps = legs[1]["steps"](fs.dock_line_pose(today, 1.0))
    assert [s["to"] for s in steps if s.get("to")][-1] == pytest.approx([-8.9, -4.6])
    # no counter card at all: no event day -- and the pre-flight SAYS so (review 21 Nov: it used to drop it, exit 0)
    doc["scenes"] = [s for s in doc["scenes"] if s["id"] != "counter_handshake"]
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    assert fs.event_day_sequences(today) == {}
    res = fs.preflight(runs=1, only="event dock -> beside_host", layout=today, adverse=False, out=lambda *_: None)
    assert any(line.startswith("the event day:") for line in res["not_checked"]), res["not_checked"]


def test_the_event_days_picture_knows_its_walks_by_their_cards():
    """Review 21 Nov: the labels gained their card ("card 2 counter_handshake: ...") and the picture's sums
    (startswith "home") matched none of them -- "ออกไปแล้วเข้าใหม่ 0 รอบ" every time, English rows."""
    assert fs._walk_role("card 1 debut: 1.0 m ahead, the pageant arm out") == "debut"
    assert fs._walk_role("card 1 debut: 1.0 m ahead, arms down, the pageant arm out at the end") == "debut"
    assert fs._walk_role("card 2 counter_handshake: to the counter (beside_host), the handshake there") == "counter"
    assert fs._walk_role("card 3 guest_handshake: to the guest (guest_front), the handshake there") == "guest"
    assert fs._thai_walk("card 3 guest_handshake: to the guest (guest_front), the handshake there") == \
        "ไปหาแขก (card 3 guest_handshake)"
    assert fs._walk_role("card 10 rest: home, the event's dock walk") == "home"
    assert fs._walk_role("home: floor1.json dock_via_reception") == "home"
    assert fs._walk_role("card 3 after_ceremony: something else") is None
    assert fs._thai_walk("card 10 rest: home, the event's dock walk") == "กลับแท่น (card 10 rest)"


def test_card_3_turns_round_and_stops_in_front_of_the_guest(today, tmp_path, monkeypatch):
    """Owner 22 Nov ("ตอนจับมือกับคุณมิกิ ก็ให้หันหลังเดินไปจับมือกับแขก"): card 3 turns her round at the counter
    and walks straight up to the guest. With the guest there (where is ASSUMED: HANDSHAKE_GUEST_AT_M) she ends
    done in front of the guest -- the base's front 0.6-0.7 m from the guest, before the base's own 0.5 m hold, the
    hand short of the body -- facing away from the counter; with nobody there she walks the whole 1.2 m and ends
    done too (the hand is given there). No card 3 in the file = the day as before (cards 1, 2, home)."""
    import json

    seqs = fs.event_day_sequences(today)
    name = next(n for n in seqs if "event's dock walk" in n)
    # nothing known outside the bounds for the day since 23 Nov (the arm no longer rises on the dock)
    assert "counter > guest > home" in name and name not in fs.KNOWN_OUTSIDE
    legs = seqs[name]
    run = fs.run_sequence(today, legs, seed=3)
    assert len(run["parts"]) == 4 and all(p["end"] == "done" for p in run["parts"]), [p["reason"] for p in run["parts"]]
    g = run["parts"][2]
    assert 0.55 <= g["front_to_guest"] <= 0.70 and 0.0 < g["hand_to_guest"] <= 0.45, g
    assert abs((g["at_guest"][2] + 360.0) % 360.0 - 180.0) < 6.0            # her back to the counter
    assert g["walked_to_guest_m"] < 1.1 and not fs._held(g)                 # met, never held for the guest
    assert any("met someone" in line for line in g["said"])
    assert fs.guest_faults(fs.sequence_summary([run])["guest"]) == []
    # nobody there: the whole way, done, and nothing to say about a guest
    alone = fs.run_sequence(today, [dict(leg, guests=[]) if leg.get("guest_handshake") else leg for leg in legs], seed=3)
    a = alone["parts"][2]
    assert a["end"] == "done" and a["front_to_guest"] is None and 1.1 <= a["walked_to_guest_m"] <= 1.45, a
    # the faults it is judged by
    assert fs.guest_faults([{"front_to_guest": 0.45, "hand_to_guest": 0.15}])[0].startswith("stopped 0.45 m")
    assert fs.guest_faults([{"front_to_guest": 0.62, "hand_to_guest": -0.01}])[0].startswith("the handshake's hand")
    # no card 3 in the event file: the day of 21 Nov
    doc = json.loads(fs.EVENT_FILE.read_text(encoding="utf-8"))
    doc["scenes"] = [s for s in doc["scenes"] if s["id"] != "guest_handshake"]
    path = tmp_path / "event.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(fs, "EVENT_FILE", path)
    old = fs.event_day_sequences(today)
    assert [leg["what"].split(":")[0] for leg in old[next(iter(old))]] == ["card 1 debut", "card 2 counter_handshake",
                                                                           "card 10 rest"]
    assert next(iter(old)) not in fs.KNOWN_OUTSIDE


GUEST_DAY = "event day: dock > ahead > counter > guest > home by the event's dock walk"


def _a_hand_in_the_guest(rows):
    return ["the handshake's hand reaches into card 3's guest (-0.01 m)"] if rows else []


def test_card_3_cannot_see_behind_her_and_the_staff_rule_is_room_enough(today):
    """Second review 22 Nov: the turn round at the counter is checked by the lidar, which does not see behind her --
    someone close behind her back is touched (the card tells staff: half a metre clear behind before pressing).
    At that half metre: nobody touched (she then meets that person ahead of her, at once -- they are the guest)."""
    seqs = fs.event_day_sequences(today)
    leg = next(leg for leg in seqs[GUEST_DAY] if leg.get("guest_handshake"))
    cx, cy, _ = fs.counter_spot()

    def behind(gap):
        person = {"at": (cx - (fs.FOOT_L / 2 + gap + fs.PERSON_R), cy), "from_s": 0.0, "to_s": 1e9}
        return fs.run_sequence(today, [dict(leg, start=(cx, cy, 0.0), guests=[])], seed=1, people=[person])["parts"][0]

    close = behind(0.05)
    assert close["min_person_gap"] <= 0.0, close["min_person_gap"]                   # the limitation, shown
    room = behind(0.50)
    assert room["min_person_gap"] > 0.0 and room["end"] == "done", (room["min_person_gap"], room["reason"])


def test_card_3s_gate_wants_the_guest_met_and_her_back_to_the_counter():
    """Second review 22 Nov: the gate judged only a hand into the guest and a stop nearer than the hold -- a card 3
    that ended "done" 1.33 m from the guest (never met) or facing the wrong way passed."""
    ok = {"front_to_guest": 0.65, "hand_to_guest": 0.35, "meet_m": 0.7, "face_err_deg": 3.0}
    assert fs.guest_faults([ok]) == []
    assert "never met the guest" in fs.guest_faults([dict(ok, front_to_guest=1.33, hand_to_guest=1.03)])[0]
    assert "facing" in fs.guest_faults([dict(ok, face_err_deg=25.0)])[0]
    assert "facing" in fs.guest_faults([dict(ok, front_to_guest=None, hand_to_guest=None, face_err_deg=25.0)])[0]


def test_preflight_spots_move_card_3_with_the_counter(today, tmp_path):
    """Second review 22 Nov: `preflight --spots` (the robot's own positions) moved beside_host but not card 3's
    first point -- "X -> guest_front" turned at the old counter."""
    import json

    spots = tmp_path / "spots.json"
    spots.write_text(json.dumps({"spots": {"beside_host": {"pose": [-8.80, -4.40, 10.0]}}}),
                     encoding="utf-8")
    plan = fs.event_plan(today, fs.load_spots(spots))
    steps = plan["walks"]["event dock -> guest_front"][0]
    assert [s["to"] for s in steps if s.get("to")][-1] == [-8.80, -4.40]
    # where the turn leaves her (round from 10 deg: two halves since 23 Nov, event_tour.TURN_SPLIT_DEG)
    assert [s for s in steps if s.get("do") == "turn"][-1]["face"] == -170.0


def test_card_3s_guest_fault_reaches_the_gate(today, monkeypatch):
    """Review 22 Nov: card 3's guest was judged in event_day only -- the pre-flight (the gate before the robot)
    never asked. A fault in front of the guest is NEW there (the day's KNOWN entry names the pageant arm only)."""
    monkeypatch.setattr(fs, "guest_faults", _a_hand_in_the_guest)
    res = fs.preflight(runs=1, only=GUEST_DAY, layout=today, adverse=False, out=_quiet)
    assert any(n == GUEST_DAY and any("card 3's guest" in b for b in bad) for n, bad in res["unexpected"]), res["unexpected"]


def test_card_3s_guest_fault_reaches_the_event_days_report(today, monkeypatch):
    monkeypatch.setattr(fs, "guest_faults", _a_hand_in_the_guest)
    day = fs.event_day(1, 0, layout=today, out=_quiet)
    assert any("card 3's guest" in b for b in day[GUEST_DAY]["new"]), day[GUEST_DAY]["new"]


def test_the_report_redirected_to_a_file_does_not_die_on_a_thai_name(today, tmp_path):
    """Review 21 Nov: redirected on Windows (cp1252) the pre-flight died with UnicodeEncodeError at "NOT CHECKED"
    (Thai spot names) and exited 1 -- the gate's own failure code."""
    import os
    import subprocess

    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    r = subprocess.run([sys.executable, fs.__file__, "preflight", "--only", "event day", "--runs", "1", "--no-adverse"],
                       cwd=str(ROOT), env=env, capture_output=True, timeout=600)
    text = (r.stdout + r.stderr).decode("utf-8", errors="replace")
    assert "UnicodeEncodeError" not in text and "NOT CHECKED" in text and "walk(s):" in text, text[-2000:]


def test_a_guest_in_the_way_of_cards_1_and_2(today):
    """Owner 21 Nov ("อยากรู้ตอนวันงานถ้ามีคนยืนบัง ระหว่างทางที่เตรียมไว้หุ่นจะทำยังไง" + "เพิ่มเลย ทดสอบด้วย"): a guest
    standing on card 1's walk ahead and on card 2's walk. She stands still for them and never touches them; a guest
    who steps away -> she goes on and greets at the counter; a guest who stays -> the card stops after the patience,
    no greeting, nothing walks on. A gate, not a measurement (card 2's margin round a person is the board's, assumed)."""
    seqs = fs.event_day_sequences(today)
    legs = seqs[next(iter(seqs))]
    rep = fs._in_the_way_report(today, legs, 2, 0, _quiet)
    assert len(rep) == 4, list(rep)
    for case, row in rep.items():
        assert row["bad"] == [], (case, row)
        if case.startswith("card 1"):
            # ours: always stands still, never near the guest; through only once the guest is gone
            assert row["held"] == row["n"] == 2 and row["touch_gap"] > fs.MIN_ARM_GAP_M, (case, row)
            if case.endswith("and stays"):
                assert row["done"] == 0 and row["counter"] == 0 and all(s > 40 for s in row["stopped_s"]), (case, row)
        if case.endswith("steps away after 20 s"):
            assert row["done"] == 2 and row["counter"] == 2, (case, row)
    # card 2 is the board's: in 10 runs it went ROUND the guest once or twice instead of waiting, the arms 0.02 m
    # from them with its assumed margin (BOARD_INFLATE_M) -- printed every run, not judged here: only the robot can
    # tell how wide its board keeps round a person


def test_the_guest_cases_judge_what_matters():
    """in_the_way_verdict on made-up runs: a touch, no standing still, going through a guest who stayed, a guest
    never met, a stop that is not the patience's -- each named; the board's card 2 is not blamed for a touch."""
    def part(end="done", gap=0.4, held=True, s=30.0, why="step 1: something 0.50 m ahead -- held 45 s"):
        return {"end": end, "reason": "" if end == "done" else why, "s": s,
                "min_person_gap": gap, "events": [{"held": 1, "at": [0, 0]}] if held else []}

    ours_stays = {"card_leg": 0, "leaves": False, "ours": True}
    ours_leaves = {"card_leg": 0, "leaves": True, "ours": True}
    board_stays = {"card_leg": 1, "leaves": False, "ours": False}
    assert fs.in_the_way_verdict(ours_stays, {"parts": [part("stop", s=47.0)]}) == []
    assert fs.in_the_way_verdict(ours_stays, {"parts": [part("done")]}) == ["got there through a guest who stayed"]
    assert fs.in_the_way_verdict(ours_leaves, {"parts": [part(gap=-0.01), part()]}) == ["touched the guest"]
    assert fs.in_the_way_verdict(ours_leaves, {"parts": [part(held=False), part()]}) == ["did not stand still for the guest"]
    assert fs.in_the_way_verdict(board_stays, {"parts": [part(), part("stop", gap=-0.2, s=51.0,
                                                                     why="board gave up on the leg")]}) == []
    # the touch printed for a case is the least over every walk -- the way home past a guest who stayed too
    assert fs.in_the_way_touch(board_stays, {"parts": [part(gap=0.9), part(gap=0.024), part(gap=-0.008)]}) == -0.008
    # review 21 Nov: a route that no longer passes the guest; a wait far past the patience
    assert fs.in_the_way_verdict(board_stays, {"parts": [part(), part(gap=9.0)]})[0].startswith("never met the guest")
    assert fs.in_the_way_verdict(ours_stays, {"parts": [part("stop", s=460.0)]})[0].startswith(
        "stopped, but not at the patience")
    assert fs.in_the_way_verdict(ours_stays, {"parts": [part("stop", s=47.0, why="the board's pose jumped")]})[0].startswith(
        "stopped, but not at the patience")


def test_a_card_2_guest_counts_only_while_card_2_walks(today, monkeypatch):
    """Review 21 Nov: the 20 s of card 2's guest started 4 s into card 1 (within 2 m of the walk ahead), so the
    board waited 8 s, not 20. The guest of a card starts counting on that card."""
    # 24 Nov evening: the owner moved Khun Miki's spot and the walk out grew to 1.3 m; this holds the numbers of 23 Nov
    monkeypatch.setattr(fs, "EVENT_FILE", ROOT / "tests" / "data" / "floor1_event_rehearsed.json")
    seqs = fs.event_day_sequences(today)
    legs = seqs[next(iter(seqs))]
    spec = next(v for k, v in fs.in_the_way_cases(legs, today).items()
                if k.startswith("card 2") and "steps away" in k)
    # seed 102, not 100 (23 Nov): card 2 now goes to the counter's head-on point first (owner "ไกลกว่าโต๊ะเคาน์เตอร์แล้ว
    # ค่อยเดินเข้ามา"), its guest halfway along that, and with seed 100 the board went ROUND the guest in the open lobby
    # instead of waiting (the board's choice, its margin assumed) -- no wait, nothing to time the guest's clock by
    run = fs.run_sequence(today, legs, seed=102, people=[dict(p) for p in spec["people"]])
    plain = fs.run_sequence(today, legs, seed=102)
    card2, alone = run["parts"][1], plain["parts"][1]
    assert fs._held(card2) and alone["end"] == "done", (card2["events"], alone["reason"])
    # she meets the guest about 2 m before the guest's spot; the guest then stands 20 s: card 2 takes at least
    # ~12 s longer than without a guest (with the old clock it was ~8 s longer)
    assert card2["end"] == "done" and card2["s"] >= alone["s"] + 12.0, (card2["s"], alone["s"])
    # ... and the clock itself, whatever the route: on the head-on route the debut ends ~1.9 m from this guest, at the
    # edge of the 2 m that starts it, so the timing above alone no longer tells the old clock from the new
    import types

    w = types.SimpleNamespace(base=types.SimpleNamespace(x=0.0, y=0.0, t=5.0), leg_index=0,
                              people=[{"at": (1.0, 0.0), "leg": 1, "leave_after_seen_s": 20.0, "to_s": 1e9}])
    fs._arm_people(w)                                     # card 1 walks within 2 m of card 2's guest: not counting
    assert "seen_at" not in w.people[0]
    w.leg_index = 1
    fs._arm_people(w)
    assert w.people[0]["seen_at"] == 5.0 and w.people[0]["to_s"] == 25.0


def test_the_preflight_and_the_event_day_carry_a_guest_fault(today, monkeypatch):
    """Review 21 Nov: no test went through preflight(adverse=True) / event_day(in_the_way=) -- deleting the lines
    that put card 1's fault into the exit code stayed green. Here a fault is put in and must come out; and the
    guest cases are not walks (they were counted as 4 more)."""
    res = fs.preflight(runs=1, only="in the way", layout=today, adverse=True, out=_quiet)
    assert len(res["in_the_way"]) == 4 and res["walks"] == {} and res["unexpected"] == [], res["unexpected"]
    real = fs.in_the_way_verdict
    monkeypatch.setattr(fs, "in_the_way_verdict",
                        lambda case, run: real(case, run) + (["touched the guest"] if case["ours"] else []))
    res = fs.preflight(runs=1, only="in the way", layout=today, adverse=True, out=_quiet)
    assert [n for n, _ in res["unexpected"]] and all(n.startswith("in the way: card 1") for n, _ in res["unexpected"])
    day = fs.event_day(1, 0, layout=today, out=_quiet, in_the_way=1)
    first = next(iter(day.values()))
    assert "touched the guest" in first["new"], first["new"]


def test_card_2s_faults_reach_the_exit_code_but_the_boards_choice_does_not(today, monkeypatch):
    """Re-check 21 Nov: card 2's NEW was a filter on message prefixes -- "never got to the guest's card" (card 1
    stopping early in a card-2 case) was dropped, and replacing the filter with [] stayed green. Every card-2 fault
    is NEW except the board's own choice (BOARD_ONLY_FAULT); a touch on an earlier card is judged; the stop's bound
    follows event_walk's patience; "in the way" with --no-adverse is an error, not a pass."""
    def part(end="done", gap=0.4, s=20.0):
        return {"end": end, "reason": "" if end == "done" else "board gave up on the leg", "s": s,
                "min_person_gap": gap, "events": [{"held": 1}]}

    board_leaves = {"card_leg": 1, "leaves": True, "ours": False}
    assert fs.in_the_way_verdict(board_leaves, {"parts": [part(gap=-0.01), part()]}) == ["touched the guest on an earlier card"]
    assert fs.in_the_way_verdict(board_leaves, {"parts": [part("stop", s=10.0)]})[0].startswith("never got to the guest's card")
    real = fs.in_the_way_verdict
    for fault, counted in (("never got to the guest's card (x)", True), (fs.BOARD_ONLY_FAULT + " (board gave up)", False)):
        monkeypatch.setattr(fs, "in_the_way_verdict",
                            lambda case, run, fault=fault: real(case, run) + ([] if case["ours"] else [fault]))
        res = fs.preflight(runs=1, only="in the way", layout=today, adverse=True, out=_quiet)
        names = [n for n, _ in res["unexpected"]]
        assert bool(names) == counted and all(n.startswith("in the way: card 2") for n in names), (fault, names)
    from app import event_walk
    monkeypatch.setattr(event_walk, "PATIENCE_S", 100.0)
    assert fs.guest_stop_by_s() == 100.0 + fs.GUEST_STOP_SLACK_S
    with pytest.raises(fs.InputError):
        fs.preflight(runs=1, only="in the way", layout=today, adverse=False, out=_quiet)


def test_a_stop_the_board_drives_ends_with_its_attempt_under_way():
    """23 Nov: card 2's guest who stays stopped it at 50.5-51.1 s in 7 of 9 runs and at 55.5 / 56.7 in two -- board_walk
    asks whether the patience is spent only after the MoveTo under way ends, and the board holds that one up to its
    own give-up. Which runs: the world's random draws alone (the debut's wave standing moved them). The board's leg
    gets BOARD_GIVE_UP_S more; ours (card 1's walk ahead, the patience itself) does not; patience x10 is still caught."""
    def part(s):
        return {"end": "stop", "reason": "step 2: ended 1.10 m from the point (the board gave up on it)", "s": s,
                "min_person_gap": 0.4, "events": [{"held": 1}]}

    board = {"card_leg": 1, "leaves": False, "ours": False}
    first = {"end": "done", "s": 9.0, "min_person_gap": 2.0, "events": []}
    assert not any("not at the patience" in b for b in fs.in_the_way_verdict(board, {"parts": [first, part(56.7)]}))
    assert any("not at the patience" in b for b in fs.in_the_way_verdict(board, {"parts": [first, part(450.0)]}))
    assert fs.guest_stop_by_s(board=True) == fs.guest_stop_by_s() + fs.BOARD_GIVE_UP_S
    ours = {"card_leg": 0, "leaves": False, "ours": True}
    assert any("not at the patience" in b for b in fs.in_the_way_verdict(ours, {"parts": [part(56.7)]}))
