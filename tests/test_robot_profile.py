"""app/robot_profile.py: what a robot IS, for the map tools (floor_sim, map_check).

Owner, 20 Nov 2026: "ระบบแผนที่ของเราอาจจะต้องเอาไปใช้กับหุ่นเสิร์ฟด้วย อาจจะต้องทำระบบเผื่อไว้ เช็คและรีวิวทดสอบ
โดยละเอียด". Emma is the default and must give what the tools gave before the profile existed;
another robot's numbers must reach every place Emma's constants were read. Nothing here reads
.tmp/ or the robot's files: the rooms and frames are built inline.
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

from app import map_check as mc  # noqa: E402
from app import robot_lanes as rl  # noqa: E402
from app import robot_profile as rp  # noqa: E402


def _fs():
    spec = importlib.util.spec_from_file_location("floor_sim", ROOT / "scripts" / "floor_sim.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["floor_sim"] = mod
    spec.loader.exec_module(mod)
    return mod


fs = _fs()


# ---- Emma = the constants, and the numbers of before ---------------------------------------

def test_emma_is_the_constants_the_tools_had():
    """Every value of the default profile is the constant it replaced -- the same float. A
    constant changed without the profile (or the other way) is a red test, not two Emmas."""
    e = rp.EMMA
    assert rp.get() is e and rp.get("") is e and rp.load(None) is e and rp.get("emma") is e
    assert (e.body.length_m, e.body.width_m, e.body.reach_width_m) == (rl.ROBOT_BASE_LENGTH, rl.ROBOT_BASE_WIDTH, rl.ROBOT_WIDTH)
    assert (e.body.length_m, e.body.width_m, e.body.reach_width_m) == (fs.FOOT_L, fs.FOOT_W_BASE, fs.FOOT_W_ARMS)
    assert e.body.tray_overhang_m == 0.0 and e.body.tray_height_m is None
    assert e.body.turn_radius_m == math.hypot(fs.FOOT_L / 2, fs.FOOT_W_ARMS / 2)
    assert e.body.stood_floor_r_m == mc.BODY_R_M
    assert (math.radians(e.lidar.from_deg), math.radians(e.lidar.to_deg)) == (fs.LIDAR_FROM, fs.LIDAR_TO)
    assert math.radians(e.lidar.bin_deg) == fs.LIDAR_BIN
    assert (e.lidar.sim_range_m, e.lidar.noise_m) == (fs.LIDAR_RANGE_M, fs.LIDAR_NOISE_M)
    assert (e.lidar.trust_min_m, e.lidar.trust_max_m) == (mc.MIN_RANGE_M, mc.MAX_RANGE_M)
    assert (e.motion.spot_turn_rad_s, e.motion.accel_mps2) == (fs.SPOT_RAD_S, fs.BOARD_ACCEL)
    assert (e.planner.inflate_m, e.planner.refuse_map_m, e.planner.refuse_seen_m) == \
        (fs.BOARD_INFLATE_M, fs.REFUSE_MAP_M, fs.REFUSE_SEEN_M) and not e.planner.measured
    assert (e.docking.contact_half_m, e.docking.max_aside_m, e.docking.max_crooked_deg) == \
        (fs.ws.DOCK_CONTACT_HALF_M, fs.MAX_DOCK_ASIDE_M, fs.MAX_DOCK_CROOKED_DEG)
    assert e.min_gap_m == fs.MIN_ARM_GAP_M and e.min_quality == mc.MIN_QUALITY
    # every speed our route files ask is under the cap (the cap changes none of Emma's walks)
    for name in ("data/robot/waypoints/floor1.json", "data/events/floor1_event.json"):
        path = ROOT / name
        if path.exists():
            speeds = [float(v) for v in __import__("re").findall(r'"speed":\s*([0-9.]+)', path.read_text(encoding="utf-8"))]
            assert all(v <= e.motion.max_speed_mps for v in speeds), (name, max(speeds))
    assert not e.example and rp.SERVING_EXAMPLE.example


def _wall(x0, y0, x1, y1, step=0.05):
    n = max(2, int(math.hypot(x1 - x0, y1 - y0) / step) + 1)
    return [(x0 + (x1 - x0) * i / (n - 1), y0 + (y1 - y0) * i / (n - 1)) for i in range(n)]


def _room():
    """A 10 x 6 m room and a table in it the board's map does not have (scratchpad golden.py's room)."""
    room = _wall(0, 0, 10, 0) + _wall(10, 0, 10, 6) + _wall(10, 6, 0, 6) + _wall(0, 6, 0, 0)
    table = [(x, y) for x in np.arange(6.4, 6.81, 0.05) for y in np.arange(2.6, 3.41, 0.05)]
    board = fs.grid_from_points(room)
    truth = fs.Grid(fs.ws.explore_blob(room, margin=1.5))
    occ = truth.occ.copy()
    for x, y in table:
        r, c = truth.rc(x, y)
        occ[r, c] = True
    truth.occ = occ
    return fs.Layout(truth, board, (1.0, 3.0, 0.0), name="room")


DOC = {"dock": {"pose": [1.0, 3.0, 0.0], "back_from_m": 1.9, "axis_offset_deg": 2.3}}
WALK = [{"do": "undock"}, {"to": [8.5, 4.6], "face": 90, "speed": 0.35}, {"to": [8.5, 1.4], "speed": 0.3}, {"do": "dock"}]


def _map_frames():
    """A room map that kept a door leaf (a phantom), two walks of four frames each, one frame
    of each under the board's quality gate."""
    cells = np.full((120, 200), mc.FREE_VALUE, dtype=np.int8)
    cells[0, :] = cells[-1, :] = cells[:, 0] = cells[:, -1] = mc.OCC_VALUE
    cells[60, 90:100] = mc.OCC_VALUE
    grid = mc.Grid(cells, 0.0, 0.0, 0.05)
    frames = []
    for run in ("w1", "w2"):
        for k, (x, y) in enumerate([(2.0, 1.5), (4.0, 1.5), (6.0, 1.5), (4.0, 4.5)]):
            ang = np.radians(np.arange(-100, 108, 0.5))
            dist = []
            for a in ang:
                dx, dy = math.cos(a), math.sin(a)
                ts = [t for t in (((0.05 - x) / dx if dx < 0 else (9.95 - x) / dx if dx > 0 else 1e9),
                                  ((0.05 - y) / dy if dy < 0 else (5.95 - y) / dy if dy > 0 else 1e9)) if t > 0]
                dist.append(min(ts))
            frames.append(mc.Frame(run, k, (x, y, 0.0), np.c_[ang, dist], q=60 if k else 39))
    return grid, frames


def _check(grid, frames, profile=None):
    ck = mc.Checker(grid) if profile is None else mc.Checker(grid, profile)
    ck.align(frames)
    for f in frames:
        ck.add(f)
    found, _ = ck.findings()
    return found, ck


#: What floor_sim and map_check gave on these inputs BEFORE the profile existed (scripts copied
#: aside on 20 Nov, scratchpad golden.py) -- the byte-for-byte comparison of the full outputs
#: (the 16 Nov pre-flight, the 19 Nov map check) is in CHANGELOG 20 Nov.
BEFORE = {
    "map_check": {"found": [["phantom", 10, [4.75, 3.025]]],
                  "frames": [[None, "quality 39"], [1.0, None], [1.0, None], [1.0, None],
                             [None, "quality 39"], [1.0, None], [1.0, None], [1.0, None]]},
    "spots": [{"arm_gap_m": 1.23, "board_refuses": False, "fit_by_heading": {"-180": 0.96, "-135": 0.86, "-90": 0.87,
                                                                              "-45": 0.87, "0": 0.88, "45": 0.87,
                                                                              "90": 1.0, "135": 0.97}, "turn_gap_m": 1.03},
              {"arm_gap_m": -0.07, "board_refuses": False, "fit_by_heading": {"-180": 0.76, "-135": 0.97, "-90": 0.99,
                                                                               "-45": 0.99, "0": 0.76, "45": 0.66,
                                                                               "90": 0.71, "135": 0.71}, "turn_gap_m": -0.12}],
    "walk": {"arm_gap_at": [6.2, 2.01], "aside_m": -0.002, "crooked_deg": 0.16, "end": "done", "min_arm_gap": 0.203,
             "min_base_gap": 0.243, "min_q": 73, "moves": [[8.5, 4.6, 90.0], [8.5, 1.4, None], [2.9, 3.08, 0.0]],
             "on_dock": True, "pose": [0.98, 3.0, 2.5], "s": 118.6},
}
#: ... but not board_walk's own dock since 20-21 Nov (no step onto the line before the long reverse
#: within SQUARE_TOL_LONG_M; "come again" past it): this walk's dock no longer steps -- "s" 130.5 -> 118.6,
#: "aside_m" 0.0 -> -0.002. With the board_walk of before 20 Nov every number above is the old one (checked
#: 21 Nov, scratchpad board_walk.before-square-20261120.py).


def _now(**profile):
    lay = _room()
    r = fs.run_walk(lay, DOC, WALK, (1.0, 3.0, 0.0), seed=3, **profile)
    rows = fs.spot_report(lay, {"a": (5.0, 4.5, 90.0, "a"), "b": (6.6, 2.2, None, "b")}, **profile)
    grid, frames = _map_frames()
    found, ck = _check(grid, frames, profile.get("profile"))
    out = {"walk": {k: r[k] for k in BEFORE["walk"]},
           "spots": [{k: row[k] for k in ("arm_gap_m", "turn_gap_m", "board_refuses", "fit_by_heading")} for row in rows],
           "map_check": {"found": [(f["kind"], f["cells"], f["centre"]) for f in found],
                         "frames": [(row.get("fit"), row.get("skipped")) for row in ck.frames]}}
    return json.loads(json.dumps(out, sort_keys=True))


def test_emma_by_default_gives_the_numbers_of_before_the_profile():
    assert _now() == BEFORE
    assert _now(profile=rp.EMMA) == BEFORE
    # an Emma built from the module constants themselves -- not the profile module's literals
    built = replace(rp.EMMA, body=rp.Body(fs.FOOT_L, fs.FOOT_W_BASE, fs.FOOT_W_ARMS, stood_floor_r_m=mc.BODY_R_M))
    assert _now(profile=built) == BEFORE


# ---- another robot reaches every place ----------------------------------------------------------

def test_another_robots_body_lidar_planner_and_gates_are_the_ones_used():
    """Each part of a profile, changed alone, changes what it should and only that."""
    lay = _room()
    where = {"b": (6.6, 2.2, 0.0, "b")}                                  # facing along the table's side, 0.4 m off it
    emma = fs.spot_report(lay, where)[0]
    slim = replace(rp.EMMA, body=replace(rp.EMMA.body, reach_width_m=0.50))
    row = fs.spot_report(lay, where, profile=slim)[0]
    assert emma["arm_gap_m"] < 0 < row["arm_gap_m"], (row["arm_gap_m"], emma["arm_gap_m"])
    assert row["arm_gap_m"] - emma["arm_gap_m"] == pytest.approx((0.78 - 0.50) / 2, abs=0.011)
    assert row["turn_gap_m"] - emma["turn_gap_m"] == pytest.approx(rp.EMMA.body.turn_radius_m - slim.body.turn_radius_m, abs=0.011)
    # the planner's refusal distance: the same spot refused by a planner that keeps further off
    shy = replace(rp.EMMA, planner=replace(rp.EMMA.planner, refuse_map_m=1.7))
    assert not fs.spot_report(lay, {"a": (5.0, 4.5, None, "a")})[0]["board_refuses"]
    assert fs.spot_report(lay, {"a": (5.0, 4.5, None, "a")}, profile=shy)[0]["board_refuses"]
    # the lidar's arc: Emma sees nothing behind, a 270-degree lidar does
    w = fs.FloorWorld(lay, (5.0, 3.0, 0.0), roll_off_deg=0.0)
    a = np.degrees(np.arctan2(w.scan()[:, 1], w.scan()[:, 0]))
    wide = fs.FloorWorld(lay, (5.0, 3.0, 0.0), roll_off_deg=0.0, profile=rp.SERVING_EXAMPLE)
    b = np.degrees(np.arctan2(wide.scan()[:, 1], wide.scan()[:, 0]))
    assert a.min() > -101.5 and a.max() < 108.5 and b.min() < -125 and b.max() > 125
    # the speed cap: a step asking more than the robot's fastest gets its fastest
    w.set_speed(0.9)
    wide.set_speed(0.9)
    assert w.speed == rp.EMMA.motion.max_speed_mps and wide.speed == rp.SERVING_EXAMPLE.motion.max_speed_mps
    # the body the walk is judged by: the same walk, a robot as wide as Emma's arms + 0.4 m
    wide_body = replace(rp.EMMA, body=replace(rp.EMMA.body, reach_width_m=1.18))
    r0 = fs.run_walk(lay, DOC, WALK, (1.0, 3.0, 0.0), seed=3)
    r1 = fs.run_walk(lay, DOC, WALK, (1.0, 3.0, 0.0), seed=3, profile=wide_body)
    assert r1["min_arm_gap"] < r0["min_arm_gap"] - 0.15, (r0["min_arm_gap"], r1["min_arm_gap"])
    # the judge holds a robot to ITS gap and dock tolerance; no dock tolerance = no dock bound
    ok = {"end": "done", "reason": "", "on_dock": True, "aside_m": 0.08, "crooked_deg": 0.4, "min_arm_gap": 0.07,
          "min_person_gap": 9.0, "min_hazard": {}, "touched": []}
    assert fs.judge("w", [ok], [], plain=True, ends_docked=True)                       # Emma: 8 cm aside is out
    assert fs.judge("w", [ok], [], plain=True, ends_docked=True, profile=rp.SERVING_EXAMPLE) == []
    picky = replace(rp.SERVING_EXAMPLE, min_gap_m=0.10)
    assert "arms" in fs.judge("w", [ok], [], plain=True, ends_docked=False, profile=picky)[0]


def test_map_check_takes_the_robots_lidar_band_body_and_quality_gate():
    grid, frames = _map_frames()
    _, emma = _check(grid, frames)
    assert sum(1 for r in emma.frames if r.get("skipped") == "quality 39") == 2
    # a robot with no such quality number uses every frame
    _, other = _check(grid, frames, rp.SERVING_EXAMPLE)
    assert not any(r.get("skipped") for r in other.frames)
    # its lidar's trusted band: beams past it are not laid -- here every wall is >= 1.45 m off,
    # so a lidar trusted to 1 m has nothing to place a frame by
    short = replace(rp.EMMA, lidar=replace(rp.EMMA.lidar, trust_max_m=1.0))
    assert len(mc._beams(frames[1], short.lidar)) == 0 < len(mc._beams(frames[1]))
    assert mc.Checker(grid).place(frames[1])[0] is not None
    assert mc.Checker(grid, short).place(frames[1]) == (None, 0.0, 0.0)
    _, ck = _check(grid, frames, short)
    assert all(r.get("skipped") for r in ck.frames) and not any(r.get("skipped", "").startswith("pose") for r in emma.frames)
    # the floor its body stood on
    assert mc.Checker(grid).body_r == 0.25 and mc.Checker(grid, rp.SERVING_EXAMPLE).body_r == 0.20


def test_a_profile_file_is_strict_and_never_silently_emma(tmp_path):
    """A vendor's sheet typed in as JSON: a typo is an error; a part not given is the serving
    EXAMPLE's and is said in `unknown` -- never Emma's numbers quietly."""
    doc = {"id": "vendor_x", "name": "Vendor X serving robot", "body": {"length_m": 0.48, "width_m": 0.52,
                                                                       "reach_width_m": 0.52, "tray_height_m": 0.95}}
    p = rp.from_dict(doc)
    assert p.body.length_m == 0.48 and p.body.tray_height_m == 0.95 and p.lidar == rp.SERVING_EXAMPLE.lidar
    assert any(u.startswith("lidar") for u in p.unknown) and any(u.startswith("planner") for u in p.unknown)
    assert any("stood_floor_r_m" in u for u in p.unknown)
    assert p.board_api == "vendor" and p.min_quality is None
    with pytest.raises(ValueError, match="unknown key"):
        rp.from_dict(dict(doc, bodyy={}))
    with pytest.raises(ValueError, match="unknown key"):
        rp.from_dict(dict(doc, body={"length_m": 0.5, "lenght_m": 0.5}))
    with pytest.raises(ValueError, match="id"):
        rp.from_dict({"name": "no id"})
    with pytest.raises(KeyError):
        rp.get("serving")                                                    # a near miss is not Emma
    f = tmp_path / "vendor_x.json"
    f.write_text(json.dumps(doc), encoding="utf-8")
    assert rp.load(str(f)).id == "vendor_x"
    text = "\n".join(rp.describe(rp.SERVING_EXAMPLE))
    assert "AN EXAMPLE" in text and "not measured" in text
    assert "AN EXAMPLE" not in "\n".join(rp.describe(rp.EMMA))
