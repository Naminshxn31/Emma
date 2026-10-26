"""drive_route's lidar-map shadow (19 ต.ค.): matched at every checkpoint and
logged, never used; frames on the move logged too.

What must hold, because this runs inside the controller that moves the robot:
the pose written to the board is the walls' fix whatever the map says, a map
built on other walls is refused (not compared and blamed), nothing the map
does can stop a run, and the drive record carries the frames on the way.
"""
import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

from app import robot_lanes as rl
from app import scan_match as sm
from tests.test_drive_route_legs import ROUND_TRIP
from tests.test_drive_route_legs import _main as _run_main
from tests.test_robot_drive import _ClearWatcher, _drive_route, _lane_runner
from tests.test_robot_lanes import raycast
from tests.test_scan_match import ROOM, ROOM_POSES

SM_SRC = Path(__file__).resolve().parents[1] / "app" / "scan_match.py"

ANCHORS = {"east": rl.Anchor("x", 6.0, 0.0, 4.0), "south": rl.Anchor("y", 0.0, 3.4, 6.0)}
META = {"anchors": {"east": ["x", 6.0, 0.0, 4.0], "south": ["y", 0.0, 3.4, 6.0]}, "doors": {}}


def _field(shift=(0.0, 0.0), meta=META):
    """The ROOM's map, its whole frame moved by `shift` (a map built in a frame
    that is not the route's)."""
    frames = [(run, raycast(ROOM, p, seed=i), (p[0] + shift[0], p[1] + shift[1], p[2]))
              for run in ("a", "b") for i, p in enumerate(ROOM_POSES)]
    grid = sm.grid_for([(pts, p) for _, pts, p in frames])
    return sm.Field.from_occupancy(grid, sm.occupancy(grid, frames), meta=dict(meta))


def _laser_doc(pts):
    a, d = np.arctan2(pts[:, 1], pts[:, 0]), np.hypot(pts[:, 0], pts[:, 1])
    return {"laser_points": [{"angle": float(x), "distance": float(y), "valid": True} for x, y in zip(a, d)]}


class _StopBoard:
    def __init__(self, truth, guess):
        self.truth, self.guess, self.set = truth, guess, []

    def pose(self):
        return self.guess

    def raw_scan(self):
        return _laser_doc(raycast(ROOM, self.truth, seed=42))

    def set_pose(self, pose):
        self.set.append(tuple(pose))

    def power(self):
        return {"dockingStatus": "not_on_dock"}


def _stop_runner(dr, board, shadow):
    r = dr.Runner.__new__(dr.Runner)
    r.b = board
    r.route = rl.Route(name="t", anchors=ANCHORS, doors={}, steps=[])
    r.say = lambda m: None
    r.logged = []
    r.log = lambda **e: r.logged.append(e)
    r.measured_at = {}
    r.shadow = shadow
    return r


STEP = rl.Step(do="checkpoint", use=("east", "south"), label="in the room")
TRUTH = (1.5, 1.2, 0.0)
GUESS = (1.56, 1.26, math.radians(1.5))


def test_the_board_gets_the_walls_fix_even_when_the_map_says_otherwise():
    dr = _drive_route()
    board = _StopBoard(TRUTH, GUESS)
    r = _stop_runner(dr, board, _field(shift=(0.20, 0.0)))       # a map 20 cm out
    fix = r.checkpoint(STEP)
    assert fix.accepted
    assert board.set == [tuple(fix.pose)]
    assert board.set[0][0] == pytest.approx(TRUTH[0], abs=0.02)
    row = next(e for e in r.logged if e["kind"] == "shadow_fix")
    assert row["est"][0] == pytest.approx(TRUTH[0] + 0.20, abs=0.02)   # it said so, and was only logged
    assert row["vs_walls"]["x_m"] == pytest.approx(0.20, abs=0.02)
    kinds = [e["kind"] for e in r.logged]
    assert kinds.index("checkpoint") < kinds.index("shadow_fix")


def test_a_map_in_the_routes_frame_agrees_with_the_walls():
    dr = _drive_route()
    r = _stop_runner(dr, _StopBoard(TRUTH, GUESS), _field())
    r.checkpoint(STEP)
    row = next(e for e in r.logged if e["kind"] == "shadow_fix")
    assert abs(row["vs_walls"]["x_m"]) < 0.015 and abs(row["vs_walls"]["y_m"]) < 0.015
    assert all(row["pinned"]) and row["score"] > 0.8
    assert row["seconds"] >= 0


def test_a_map_that_blows_up_is_a_logged_error_not_a_stopped_run(monkeypatch):
    dr = _drive_route()
    board = _StopBoard(TRUTH, GUESS)
    r = _stop_runner(dr, board, _field())
    monkeypatch.setattr(sm, "search", lambda *a, **k: (_ for _ in ()).throw(MemoryError("no room")))
    fix = r.checkpoint(STEP)
    assert fix.accepted and board.set == [tuple(fix.pose)]
    row = next(e for e in r.logged if e["kind"] == "shadow_fix")
    assert "MemoryError" in row["error"]


def test_no_map_no_rows():
    dr = _drive_route()
    r = _stop_runner(dr, _StopBoard(TRUTH, GUESS), None)
    r.checkpoint(STEP)
    assert not [e for e in r.logged if e["kind"] == "shadow_fix"]


def test_a_map_built_on_other_walls_is_refused_and_says_which(tmp_path):
    dr = _drive_route()
    route = rl.Route(name="t", anchors=ANCHORS, doors={"D1": rl.Door("y", 0.0, 2.5, 3.4)}, steps=[])
    good = dict(META, doors={"D1": ["y", 0.0, 2.5, 3.4]})
    p = tmp_path / "f.npz"
    _field(meta=good).save(p)
    fd, why = dr.load_shadow_field(route, p)
    assert fd is not None, why
    # 8 ต.ค.: mr_north moved 17 cm under the logs -- the same, here
    moved = dict(good, anchors=dict(good["anchors"], south=["y", 0.17, 3.4, 6.0]))
    _field(meta=moved).save(p)
    fd, why = dr.load_shadow_field(route, p)
    assert fd is None and "south" in why
    door_moved = dict(good, doors={"D1": ["y", 0.0, 2.33, 3.23]})
    _field(meta=door_moved).save(p)
    fd, why = dr.load_shadow_field(route, p)
    assert fd is None and "D1" in why
    _field(meta={}).save(p)
    assert dr.load_shadow_field(route, p)[0] is None
    assert dr.load_shadow_field(route, tmp_path / "missing.npz")[0] is None
    (tmp_path / "junk.npz").write_bytes(b"not a map")
    fd, why = dr.load_shadow_field(route, tmp_path / "junk.npz")
    assert fd is None and "not loaded" in why
    assert dr.load_shadow_field(route, None) == (None, "off (--no-shadow-map)")


def _scan_match_without_scipy(monkeypatch):
    """app/scan_match.py imported afresh with scipy unimportable: the robot's
    nav Python has numpy only (robot_python.py). Afresh, so a scipy import
    moved to the module's top goes red here too, not only one moved into
    Field.load/search/lookup -- the module already imported above did its
    imports while scipy was there."""
    monkeypatch.setitem(sys.modules, "scipy", None)
    monkeypatch.setitem(sys.modules, "scipy.ndimage", None)
    name = "scan_match_numpy_only"
    spec = importlib.util.spec_from_file_location(name, SM_SRC)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, mod)          # dataclasses look their module up
    spec.loader.exec_module(mod)
    return mod


def test_a_saved_field_loads_with_numpy_alone_and_scores_the_same(tmp_path, monkeypatch):
    fd = _field()                                       # built on the PC: scipy is needed here
    p = tmp_path / "f.npz"
    fd.save(p)
    pts = sm.voxel(raycast(ROOM, TRUTH, seed=3))
    want = fd.score(pts, TRUTH)
    # Pre-commit review 26 ต.ค. #17: this used to load and score with scipy
    # importable (it comes in with the face extra on every PC), so a scipy
    # import moved into the load/match path stayed green here while on the
    # robot every checkpoint logged "No module named 'scipy'" and the map
    # quietly stopped. From here on, what the robot runs, with what it has.
    nsm = _scan_match_without_scipy(monkeypatch)
    with pytest.raises(ImportError):
        import scipy.ndimage  # noqa: F401 -- the block holds
    back = nsm.Field.load(p)
    assert back.L.dtype == np.uint8 and back.meta["anchors"] == META["anchors"]
    assert back.score(pts, TRUTH) == pytest.approx(want, abs=0.005)
    # ...and Runner.shadow_fix's own calls: voxel, search from the wheels' guess, pinned/apply
    m = nsm.search(back, nsm.voxel(raycast(ROOM, TRUTH, seed=3)), GUESS)
    assert m.pose[0] == pytest.approx(TRUTH[0], abs=0.02) and m.pose[1] == pytest.approx(TRUTH[1], abs=0.02)
    assert len(m.pinned()) == 3 and len(m.apply()) == 3
    with np.load(p, allow_pickle=False) as z:          # nothing in it needs pickle
        assert set(z.files) == {"L", "grid", "pad", "meta"}


# -- main(): which map a run loads -------------------------------------------------------
# Pre-commit review 26 ต.ค. #8/#18: --shadow-map's default was str(ROOT / ...)
# fixed at import, so a test that patched dr.ROOT still loaded this PC's
# gitignored map (shadow_map loaded=true in a test's run log) and a clean
# checkout took "no map file" -- and no test ran main() against a map at all,
# so dropping the load_shadow_field line in _main stayed green.

def _round_trip_meta(**extra):
    """A map built on the round trip's own walls and doors (the frame check passes)."""
    return dict({"anchors": {n: [a["axis"], a["at"], a["lo"], a["hi"]] for n, a in ROUND_TRIP["anchors"].items()},
                 "doors": {n: [d["axis"], d["at"], d["lo"], d["hi"]] for n, d in ROUND_TRIP["doors"].items()}},
                **extra)


def _put_map(root, **meta):
    maps = root / "data" / "robot" / "maps"
    maps.mkdir(parents=True, exist_ok=True)
    _field(meta=_round_trip_meta(**meta)).save(maps / "floor2_lidar_field.npz")


def _main_shadow(tmp_path, monkeypatch, *extra):
    """drive_route.main() on the round trip with ROOT = tmp_path (the legs'
    helper: fake board, --sdk off): its shadow_map row, and the map the
    runner held when the route would have started."""
    dr = _drive_route()
    held = []
    monkeypatch.setattr(dr.signal, "signal", lambda sig, h: None)
    monkeypatch.setattr(dr.Runner, "preflight", lambda self, **kw: None)
    monkeypatch.setattr(dr.Runner, "run", lambda self, *a: held.append(self.shadow))
    code, events, _ = _run_main(dr, tmp_path, monkeypatch, *extra)
    assert code == 0
    (row,) = [e for e in events if e["kind"] == "shadow_map"]
    return row, held


def test_main_loads_the_map_under_the_root_it_runs_from(tmp_path, monkeypatch):
    _put_map(tmp_path, runs=2, last_run="the tmp root's own run")
    row, held = _main_shadow(tmp_path, monkeypatch)
    assert row["loaded"] is True and "the tmp root's own run" in row["why"], row
    assert isinstance(held[0], sm.Field)                 # the runner has it, not only the log row


def test_main_without_a_map_under_its_root_loads_none_and_says_where_it_looked(tmp_path, monkeypatch):
    row, held = _main_shadow(tmp_path, monkeypatch)
    assert row["loaded"] is False and held == [None], row
    assert row["why"].startswith("no map file "), row
    assert Path(row["why"][len("no map file "):]) == tmp_path / "data" / "robot" / "maps" / "floor2_lidar_field.npz"


def test_no_shadow_map_loads_nothing_even_with_a_map_there(tmp_path, monkeypatch):
    _put_map(tmp_path)
    monkeypatch.setattr(sm.Field, "load", classmethod(lambda cls, p: pytest.fail("a map loaded with --no-shadow-map")))
    row, held = _main_shadow(tmp_path, monkeypatch, "--no-shadow-map")
    assert (row["loaded"], row["why"]) == (False, "off (--no-shadow-map)") and held == [None]


def test_a_shadow_map_given_on_the_command_line_is_the_one_loaded(tmp_path, monkeypatch):
    _put_map(tmp_path, last_run="the default one")
    other = tmp_path / "elsewhere.npz"
    _field(meta=_round_trip_meta(last_run="named on the command line")).save(other)
    row, held = _main_shadow(tmp_path, monkeypatch, "--shadow-map", str(other))
    assert row["loaded"] is True and row["why"].startswith("elsewhere.npz: "), row
    assert "named on the command line" in row["why"] and isinstance(held[0], sm.Field)


class _WallWatcher(_ClearWatcher):
    """A wall 0.8 m to the left all along; frames carry the pose they were taken at."""
    def __init__(self, runner):
        super().__init__()
        self.r = runner

    def points(self):
        xs = np.linspace(-1.0, 3.0, 400)
        return np.c_[xs, np.full(400, 0.8)]

    def frame(self):
        return self.points(), tuple(self.r.sdk.pose)


def test_the_drive_record_carries_frames_on_the_move(monkeypatch):
    dr = _drive_route()
    r = _lane_runner(dr, (1.0, -2.6, 0.0), monkeypatch)
    r.watcher = lambda: _WallWatcher(r)
    r.drive_velocity(rl.Step(do="drive", to=(2.46, -2.60), label="east to the junction"))
    rec = r.logged[-1]
    seen = rec["seen"]
    assert len(seen) >= 2
    alongs = [s["along"] for s in seen]
    assert alongs == sorted(alongs) and all(b - a >= dr.SCAN_LOG_EVERY_M - 0.06 for a, b in zip(alongs, alongs[1:]))
    s = seen[0]
    xy = np.array(s["xy_mm"]).reshape(-1, 2)
    assert all(isinstance(v, int) for v in s["xy_mm"][:10])
    assert np.allclose(xy[:, 1], 800, atol=1)                   # the wall, in millimetres
    assert len(xy) <= 4.0 / dr.SCAN_LOG_VOXEL_M + 2            # thinned to one per 2 cm
    json.dumps(rec)                                            # the record still writes as JSON


def test_scan_record_keeps_scan_order_and_millimetres():
    dr = _drive_route()
    pts = np.array([[1.0, 0.0], [1.001, 0.0005], [1.0, 0.5], [0.5, 1.0]])
    rec = dr.scan_record(pts, (1, 2, 0.1), 0.6, 0.05, 0.3)
    assert rec["xy_mm"] == [1000, 0, 1000, 500, 500, 1000]       # the near-duplicate thinned, order kept
    assert rec["pose"] == [1, 2, 0.1] and rec["along"] == 0.6
