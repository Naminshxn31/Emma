"""app/map_package.py + scripts/map_package.py: floor 1's map and places as files another robot reads.

Owner, 20 Nov 2026: "ระบบแผนที่ของเราอาจจะต้องเอาไปใช้กับหุ่นเสิร์ฟด้วย". The package is ROS map_server's
PGM + YAML for the grid and JSON in the same frame for everything else. What is held here: the
grid survives the round trip for every reader of ours (map_check, floor_sim), a place stays on
the same cell of the same wall, real map_server files read the ROS way, a changed file is said,
the board's dock marker is read only when every byte is accounted for. Everything is built
inline; the one test on the real 19 Nov file skips (saying what goes unchecked) without it.
"""
from __future__ import annotations

import importlib.util
import json
import math
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

from app import map_check as mc  # noqa: E402
from app import map_package as mp  # noqa: E402
from app import robot_profile as rp  # noqa: E402


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


fs = _load("floor_sim", "scripts/floor_sim.py")
pkg_cli = _load("map_package_cli", "scripts/map_package.py")
mc_cli = _load("map_check_cli", "scripts/map_check.py")


def _graded(seed=0, h=60, w=90):
    """A board-like grid: graded values -127..127 (a scan map), a float32 origin like the board's."""
    rng = np.random.default_rng(seed)
    cells = rng.integers(-127, 128, size=(h, w)).astype(np.int8)
    ox, oy, res = struct.unpack("<fff", struct.pack("<fff", -25.65, -15.1, 0.05))
    header = mp.explore_header(ox, oy, w, h, res)
    return mc.Grid(cells, ox, oy, res, header)


# ---- the grid ---------------------------------------------------------------------------------

def test_the_grid_survives_the_round_trip_for_every_reader_of_ours(tmp_path):
    """Trinary keeps what every tool of ours reads (occupied < -10, free > 10, the rest
    unknown); the origin and the cell size come back to the bit; the explore blob our
    simulator and board_walk's MapFit read is the original's but for the grades."""
    g = _graded()
    mp.write(tmp_path, name="t", grid=g, places={"dock": None}, profile=rp.EMMA.to_dict(), sources={})
    back = mp.read(tmp_path)["grid"]
    assert (back.w, back.h) == (g.w, g.h)
    assert (back.ox, back.oy, back.res) == (g.ox, g.oy, g.res)                     # float32 bits back
    assert np.array_equal(back.occ(), g.occ()) and np.array_equal(back.free(), g.free())
    assert set(np.unique(back.cells)) <= {mc.OCC_VALUE, 0, mc.FREE_VALUE}
    blob = mp.explore_blob(back)
    assert blob[:36] == g.header                                                   # the same header as the board's
    again = mc.parse_explore(blob)
    assert np.array_equal(again.occ(), g.occ())
    assert np.array_equal(fs.Grid(blob).occ, fs.Grid(mp.explore_blob(g)).occ)
    # the yaml is map_server's own words
    text = (tmp_path / "map.yaml").read_text(encoding="utf-8")
    for key in ("image: map.pgm", "mode: trinary", "resolution:", "origin: [", "negate: 0", "occupied_thresh: 0.65",
                "free_thresh: 0.196"):
        assert key in text


def test_a_place_stays_on_the_same_cell_of_the_same_wall(tmp_path):
    """A spot written in the map frame comes back within 1 cm (exactly, as JSON) AND the cell
    under it in the imported grid is the cell that was under it -- a row flip, an origin taken
    at the wrong corner or half a cell off would move the wall, not the spot."""
    cells = np.full((80, 120), mc.FREE_VALUE, dtype=np.int8)
    g = mc.Grid(cells, -12.5, -8.0, 0.05, mp.explore_header(-12.5, -8.0, 120, 80, 0.05))
    marks = {"a": (-11.93, -7.61), "b": (-7.04, -6.89), "c": (-6.52, -4.27)}       # near three corners and the dock's
    for x, y in marks.values():
        g.cells[int(math.floor((y - g.oy) / g.res)), int(math.floor((x - g.ox) / g.res))] = mc.OCC_VALUE
    spots = [{"id": k, "name": k, "pose": [x, y, 45.0]} for k, (x, y) in marks.items()]
    mp.write(tmp_path, name="t", grid=g, places={"dock": {"pose": [-7.04, -6.89, 171.0]}, "spots": spots},
             profile=rp.EMMA.to_dict(), sources={})
    pkg = mp.read(tmp_path)
    back = pkg["grid"]
    for sp in pkg["places"]["spots"]:
        x, y = sp["pose"][:2]
        assert math.hypot(x - marks[sp["id"]][0], y - marks[sp["id"]][1]) < 0.01
        r, c = int(math.floor((y - back.oy) / back.res)), int(math.floor((x - back.ox) / back.res))
        assert back.cells[r, c] == mc.OCC_VALUE, sp["id"]
    assert int(back.occ().sum()) == 3
    # the picture's top row is the map's top: the mark nearest the top is in the first rows of the PGM
    img, maxval = mp.read_pgm((tmp_path / "map.pgm").read_bytes())
    assert maxval == 255
    rows = np.nonzero(img == mp.PGM_OCC)[0]
    top = max(marks.values(), key=lambda p: p[1])
    assert rows.min() == g.h - 1 - int(math.floor((top[1] - g.oy) / g.res))


def test_the_plan_transform_goes_there_and_back():
    t = {"turn_deg": -10.8, "pivot": [-9.6, -4.7], "shift": [-0.69, -0.1]}
    for pt in ((-9.6, -4.7), (0.0, 0.0), (-14.2, 3.3)):
        back = mp.map_to_plan(mp.plan_to_map(pt, t), t)
        assert math.hypot(back[0] - pt[0], back[1] - pt[1]) < 1e-9
    # the pivot moves by the shift alone; a point 1 m east of it turns 10.8 deg clockwise
    assert mp.plan_to_map((-9.6, -4.7), t) == pytest.approx((-10.29, -4.8))
    x, y = mp.plan_to_map((-8.6, -4.7), t)
    assert math.degrees(math.atan2(y + 4.8, x + 10.29)) == pytest.approx(-10.8)


def test_a_vendors_map_server_files_read_the_ros_way(tmp_path):
    """Files as other software writes them: comments in the PGM, ASCII P2, 16-bit, negate 1,
    `scale` grey levels, `raw` -- and a grid turned against its frame is refused, not guessed."""
    def write(name, pgm: bytes, **meta):
        (tmp_path / (name + ".pgm")).write_bytes(pgm)
        y = {"image": name + ".pgm", "resolution": 0.1, "origin": [1.0, 2.0, 0.0], "negate": 0,
             "occupied_thresh": 0.65, "free_thresh": 0.196}
        y.update(meta)
        (tmp_path / (name + ".yaml")).write_text("\n".join("%s: %s" % (k, v) for k, v in y.items()), encoding="utf-8")
        return mp.load_map_yaml(tmp_path / (name + ".yaml"))

    # 2 x 2, top row (y high) = occupied, free; bottom row = unknown, grey 100 (p 0.61: unknown)
    g = write("p5", b"P5\n# made by map_saver\n2 2\n255\n" + bytes([0, 254, 205, 100]))
    assert g.cells[1].tolist() == [mc.OCC_VALUE, mc.FREE_VALUE] and g.cells[0].tolist() == [0, 0]
    assert (g.ox, g.oy, g.res) == (1.0, 2.0, 0.1)
    p2 = write("p2", b"P2\n2 2\n255\n0 254\n205 100\n")
    assert np.array_equal(p2.cells, g.cells)
    sixteen = write("p16", b"P5\n2 2\n65535\n" + struct.pack(">4H", 0, 65000, 52685, 25700))
    assert np.array_equal(sixteen.cells, g.cells)
    neg = write("neg", b"P5\n2 2\n255\n" + bytes([255, 1, 50, 155]), negate=1)
    assert neg.cells[1].tolist() == [mc.OCC_VALUE, mc.FREE_VALUE] and neg.cells[0].tolist() == [0, 0]
    # raw: the value IS the occupancy, 0..100; anything else is unknown -- nav2's map_saver writes
    # unknown as 255 (review 20 Nov: 255 read as a wall, and this test once said it should)
    raw = write("raw", b"P5\n3 2\n255\n" + bytes([100, 0, 65, 255, 50, 101]), mode="raw")
    assert raw.cells[1].tolist() == [mc.OCC_VALUE, mc.FREE_VALUE, mc.OCC_VALUE]
    assert raw.cells[0].tolist() == [0, 0, 0]                                     # 255 and 101 unknown, 50 between
    # the header's maxval is the image's white: a maxval-100 image's 100 is free floor (review 20 Nov:
    # scaled by 255 it read 0.61 -- unknown), and so is a 16-bit image's 1000 under maxval 1000
    hundred = write("max100", b"P5\n3 1\n100\n" + bytes([0, 100, 85]))                    # 85: p 0.15, free
    assert hundred.cells[0].tolist() == [mc.OCC_VALUE, mc.FREE_VALUE, mc.FREE_VALUE]
    thousand = write("max1000", b"P5\n2 1\n1000\n" + struct.pack(">2H", 0, 1000))
    assert thousand.cells[0].tolist() == [mc.OCC_VALUE, mc.FREE_VALUE]
    p2max = write("p2max", b"P2\n2 1\n15\n0 15\n")
    assert p2max.cells[0].tolist() == [mc.OCC_VALUE, mc.FREE_VALUE]
    with pytest.raises(mp.PackageError, match="turned"):
        write("turned", b"P5\n2 2\n255\n" + bytes(4), origin=[1.0, 2.0, 0.3])
    with pytest.raises(mp.PackageError, match="not a PGM"):
        write("png", b"\x89PNG....")


# ---- the readers of ours on an imported map ----------------------------------------------------

def _room_layout():
    def wall(x0, y0, x1, y1, step=0.05):
        n = max(2, int(math.hypot(x1 - x0, y1 - y0) / step) + 1)
        return [(x0 + (x1 - x0) * i / (n - 1), y0 + (y1 - y0) * i / (n - 1)) for i in range(n)]

    room = wall(0, 0, 10, 0) + wall(10, 0, 10, 6) + wall(10, 6, 0, 6) + wall(0, 6, 0, 0)
    board = fs.grid_from_points(room)
    truth = fs.Grid(fs.ws.explore_blob(room, margin=1.5))
    occ = truth.occ.copy()
    glass = np.zeros_like(occ)
    for x in np.arange(6.4, 6.81, 0.05):
        for y in np.arange(2.6, 3.41, 0.05):
            r, c = truth.rc(x, y)
            occ[r, c] = True
    for y in np.arange(4.4, 5.6, 0.05):                                         # a glass pane: solid, seen through
        r, c = truth.rc(3.0, y)
        occ[r, c] = glass[r, c] = True
    truth.occ = occ
    return fs.Layout(truth, board, (1.0, 3.0, 0.0), glass=glass, name="room"), glass


def _fs_grid_to_mc(g) -> mc.Grid:
    cells = np.where(g.occ, mc.OCC_VALUE, mc.FREE_VALUE).astype(np.int8)
    return mc.Grid(cells, g.ox, g.oy, g.res, bytes(g.blob[:36]))


def test_floor_sim_walks_an_imported_package_as_it_walked_the_original(tmp_path):
    lay, glass = _room_layout()
    dock = {"pose": [1.0, 3.0, 0.0], "back_from_m": 1.9, "axis_offset_deg": 2.3, "source": "test"}
    hz = [{"id": "bump", "x": 5.0, "y": 1.2, "radius_m": 0.5}]
    lay.hazards = hz
    mp.write(tmp_path, name="room", grid=_fs_grid_to_mc(lay.board), truth=_fs_grid_to_mc(lay.truth), glass=glass,
             places={"dock": dock, "spots": [{"id": "a", "name": "a", "pose": [8.0, 4.6, 90.0]}], "hazards": hz},
             profile=rp.EMMA.to_dict(), sources={})
    imp = fs.layout_from_package(tmp_path)
    assert imp.dock == lay.dock and np.array_equal(imp.truth.occ, lay.truth.occ) and np.array_equal(imp.board.occ, lay.board.occ)
    assert np.array_equal(imp.returns, lay.returns) and np.array_equal(imp.furniture, lay.furniture)
    assert fs.route_doc(imp)["dock"] == {k: v for k, v in dock.items() if k != "source"}
    steps = [{"do": "undock"}, {"to": [8.0, 4.6], "face": 90, "speed": 0.3}, {"to": [8.5, 1.4], "speed": 0.3}, {"do": "dock"}]
    doc = {"dock": {k: v for k, v in dock.items() if k != "source"}}
    for seed in range(2):
        a = fs.run_walk(lay, doc, steps, lay.dock, seed=seed)
        b = fs.run_walk(imp, fs.route_doc(imp), steps, imp.dock, seed=seed)
        for key in ("end", "s", "pose", "on_dock", "aside_m", "min_arm_gap", "min_hazard", "moves", "min_q"):
            assert a[key] == b[key], (key, a[key], b[key])
    where = {"a": (8.0, 4.6, 90.0, "a")}
    assert fs.spot_report(lay, where) == fs.spot_report(imp, where)
    # the package's own plan: every placed spot to every other, the dock among them
    plan = fs.package_plan(imp)
    assert set(plan["walks"]) == {"dock -> a", "a -> dock"} and plan["walks"]["a -> dock"][0] == [{"do": "dock"}]


def _map_frames():
    """A room map that kept a door leaf (a phantom) and two walks of four frames each."""
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
            frames.append(mc.Frame(run, k, (x, y, 0.0), np.c_[ang, dist], q=60))
    return grid, frames


def test_map_check_finds_the_same_on_an_imported_map(tmp_path):
    grid, frames = _map_frames()
    grid = mc.Grid(grid.cells, grid.ox, grid.oy, grid.res, mp.explore_header(grid.ox, grid.oy, grid.w, grid.h, grid.res))
    mp.write(tmp_path / "p", name="t", grid=grid, places={}, profile=rp.EMMA.to_dict(), sources={})
    for path in (tmp_path / "p" / "map.yaml", tmp_path / "p"):                    # a yaml, a package folder
        back, kind = mc_cli.load_map(path)
        assert kind == "ros"
        found = []
        for g in (grid, back):
            ck = mc.Checker(g)
            ck.align(frames)
            for f in frames:
                ck.add(f)
            found.append([(f["kind"], f["cells"], f["centre"], f["rc"]) for f in ck.findings()[0]])
        assert found[0] == found[1] and found[0], found
    # and its clean map comes out in the vendor's format too
    walks = tmp_path / "w"
    for run in ("w1", "w2"):
        d = walks / run
        d.mkdir(parents=True)
        d.joinpath("frames.jsonl").write_text("\n".join(json.dumps(
            {"n": f.n, "pose": list(f.pose), "q": f.q, "scan": f.scan.tolist()}) for f in frames if f.run == run), encoding="utf-8")
    res = mc_cli.run([walks / "w1", walks / "w2"], map_path=tmp_path / "p" / "map.yaml", stcm=None, min_runs=2, keep=[],
                     clear=[], out=tmp_path / "out")
    assert (tmp_path / "out" / "clean.yaml").exists() and res["found"]
    clean = mp.load_map_yaml(tmp_path / "out" / "clean.yaml")
    assert not clean.occ()[60, 90:100].any()                                      # the phantom of the test is cleared there


# ---- the board's layers, integrity, checks -----------------------------------------------------

def _stcm(layers: list[tuple[dict, bytes]]) -> bytes:
    """An .stcm as measured (memory "stcm-plan-maps"): "STCM", u16 header length, u32 layer
    count at 0x0C; each layer u32 length incl. itself, u16 key count, u16-length strings."""
    head = bytearray(b"STCM" + struct.pack("<H", 0x12) + b"\0" * 6 + struct.pack("<I", len(layers)) + b"\0\0")
    body = b""
    for kv, payload in layers:
        meta = struct.pack("<H", len(kv))
        for k, v in kv.items():
            meta += struct.pack("<H", len(k)) + k.encode() + struct.pack("<H", len(v)) + v.encode()
        body += struct.pack("<I", 4 + len(meta) + len(payload)) + meta + payload
    return bytes(head) + body


def _pose_entry(ident, x, y, yaw, meta):
    out = struct.pack("<H", len(ident)) + ident.encode() + b"\0" + struct.pack("<fff", x, y, yaw) + b"\0"
    out += struct.pack("<H", len(meta))
    for k, v in meta.items():
        out += struct.pack("<H", len(k)) + k.encode() + struct.pack("<H", len(v)) + v.encode()
    return out


def test_the_boards_dock_marker_is_read_only_when_every_byte_is_accounted_for():
    grid = np.full((4, 5), 127, dtype=np.int8)
    explore = ({"usage": "explore", "dimension_width": "5", "dimension_height": "4", "origin_x": "-1.0", "origin_y": "-2.0",
                "resolution_x": "0.05", "type": "vnd.slamtec.map-layer/vnd.grid-map+binary"}, grid.tobytes())
    marker = _pose_entry("home_dock", -7.078, -6.24, math.radians(168.8), {"display_name": "home_dock"})
    blob = _stcm([explore, ({"usage": "home_dock_pose", "count": "1", "type": "vnd.slamtec.map-layer/vnd.pose-map+binary"}, marker),
                  ({"usage": "virtual_walls", "count": "0", "type": "vnd.slamtec.map-layer/vnd.line-map+binary"}, b""),
                  ({"usage": "rectangle_area", "count": "2", "type": "vnd.slamtec.map-layer/vnd.rectangle-area-map-v2+binary"},
                   b"\x01" * 40)])
    assert mc.parse_stcm(blob).w == 5
    layers = {l["usage"]: l for l in mp.stcm_layers(blob)}
    dock = layers["home_dock_pose"]
    assert dock["read"] and dock["entries"][0]["id"] == "home_dock"
    assert dock["entries"][0]["pose"] == pytest.approx([-7.078, -6.24, 168.8], abs=1e-3)
    assert dock["entries"][0]["metadata"] == {"display_name": "home_dock"}
    assert layers["virtual_walls"]["read"] and not layers["virtual_walls"]["entries"]
    assert not layers["rectangle_area"]["read"] and layers["rectangle_area"]["count"] == 2   # listed, not guessed
    # one byte too many: the layer is not what we measured -- unread, not half-read
    blob2 = _stcm([explore, ({"usage": "home_dock_pose", "count": "1", "type": "vnd.slamtec.map-layer/vnd.pose-map+binary"},
                             marker + b"\0")])
    assert not {l["usage"]: l for l in mp.stcm_layers(blob2)}["home_dock_pose"]["read"]


def test_the_real_floor1_stcm_holds_the_stale_dock_marker():
    path = ROOT / ".tmp/map-check/20261119-floor1-upload/pass2/clean.stcm"
    if not path.exists():
        pytest.skip("the 19 Nov floor-1 .stcm (.tmp/, gitignored) is not on this machine: NOT checked here -- that our "
                    "reading of the board's pose layer gives its registered dock marker (-7.078, -6.240)")
    layers = {l["usage"]: l for l in mp.stcm_layers(path.read_bytes())}
    assert layers["home_dock_pose"]["read"]
    assert layers["home_dock_pose"]["entries"][0]["pose"][:2] == pytest.approx([-7.078, -6.240], abs=0.002)
    assert all(l["count"] == 0 for u, l in layers.items() if u != "home_dock_pose")


def test_a_changed_file_or_a_newer_package_is_said(tmp_path):
    g = _graded()
    mp.write(tmp_path, name="t", grid=g, places={}, profile=rp.EMMA.to_dict(), sources={})
    mp.read(tmp_path)
    places = tmp_path / "places.json"
    places.write_text(places.read_text(encoding="utf-8").replace("map", "map "), encoding="utf-8")
    with pytest.raises(mp.PackageError, match="changed"):
        mp.read(tmp_path)
    assert mp.read(tmp_path, verify=False)["places"]
    man = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    (tmp_path / "package.json").write_text(json.dumps(dict(man, version=mp.VERSION + 1)), encoding="utf-8")
    with pytest.raises(mp.PackageError, match="newer"):
        mp.read(tmp_path)
    with pytest.raises(mp.PackageError, match="not a map package"):
        mp.read(tmp_path / "nothing")


def test_check_says_what_a_robot_would_trip_on():
    cells = np.full((60, 100), mc.FREE_VALUE, dtype=np.int8)
    cells[30, 20] = mc.OCC_VALUE                                                  # a stale wall cell under the dock
    cells[:, 60] = mc.OCC_VALUE                                                   # a wall at x = 3.0
    cells[10:20, 80:90] = 0                                                       # unknown floor
    g = mc.Grid(cells, 0.0, 0.0, 0.05)
    places = {"dock": {"pose": [1.02, 1.52, 0.0]},
              "spots": [{"id": "open", "pose": [1.5, 2.5, None]}, {"id": "by_wall", "pose": [2.6, 2.0, None]},
                        {"id": "turn_only", "pose": [2.55, 1.0, None]}, {"id": "unknown", "pose": [4.25, 0.75, None]},
                        {"id": "out", "pose": [9.0, 9.0, None]}],
              "hazards": [{"id": "bump", "x": 1.0, "y": 0.5, "radius_m": 0.75}]}
    rows = {r["id"]: r for r in mp.check({"grid": g, "truth": None, "places": places}, rp.EMMA)}
    assert not rows["dock"]["ok"] and "stale wall" in rows["dock"]["detail"]
    assert rows["open"]["ok"] and rows["bump"]["ok"]
    assert not rows["by_wall"]["ok"] and "does not fit" in rows["by_wall"]["detail"]
    assert not rows["turn_only"]["ok"] and "turn" in rows["turn_only"]["detail"]
    assert not rows["unknown"]["ok"] and "unknown" in rows["unknown"]["detail"]
    assert not rows["out"]["ok"] and "outside" in rows["out"]["detail"]
    # a narrower robot fits by the wall where Emma's arms do not
    small = {r["id"]: r for r in mp.check({"grid": g, "truth": None, "places": places}, rp.SERVING_EXAMPLE)}
    assert small["by_wall"]["ok"], small["by_wall"]


# ---- the command, on files like ours -------------------------------------------------------------

def test_export_puts_our_files_into_one_frame_and_import_reads_them(tmp_path, capsys):
    cells = np.full((200, 200), mc.FREE_VALUE, dtype=np.int8)
    cells[0, :] = cells[-1, :] = cells[:, 0] = cells[:, -1] = mc.OCC_VALUE
    g = mc.Grid(cells, -12.0, -8.0, 0.05, mp.explore_header(-12.0, -8.0, 200, 200, 0.05))
    (tmp_path / "map.bin").write_bytes(mp.explore_blob(g))
    (tmp_path / "route.json").write_text(json.dumps({
        "dock": {"pose": [-7.11, -6.78, 169.6], "approach": [-9.0, -6.2], "back_from_m": 1.9, "note": "x"},
        "places": {"reception": {"pose": [-9.0, -5.2, 90], "note": "y"}},
        "paths": {"tour": {"steps": [{"do": "undock"}, {"to": [-9.0, -5.2], "speed": 0.3},
                                     {"creep": [[-9.5, -5.0], [-10.0, -4.8]]}, {"forward": 0.8}, {"do": "dock"}]}}}),
        encoding="utf-8")
    (tmp_path / "event.json").write_text(json.dumps({
        "spots": {"dock": {"dock": True}, "a": {"pose": [-9.5, -3.0, None], "name_th": "เอ"},
                  "b": {"pose": None, "name_th": "บี"}, "c": {"pose": None, "name_th": "ซี", "via": [[-10.0, -2.0]]}},
        "floor_hazards": [{"id": "bump", "x": -7.85, "y": 0.8, "radius_m": 0.75}],
        "no_walk_up_below_y": -7.0}, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "spots.json").write_text(json.dumps({
        "spots": {"c": {"pose": [-10.5, -2.5, 30.0], "recorded_at": "2026-11-20 08:00"}},
        "dock": {"pose": [-7.04, -6.89, 171.0], "recorded_at": "2026-11-20 07:50"}}), encoding="utf-8")
    out = tmp_path / "pkg"
    rc = pkg_cli.main(["export", "--map", str(tmp_path / "map.bin"), "--route", str(tmp_path / "route.json"),
                       "--event", str(tmp_path / "event.json"), "--spots", str(tmp_path / "spots.json"),
                       "--transform", str(tmp_path / "none.json"), "--out", str(out)])
    assert rc == 0, capsys.readouterr().out
    pkg = mp.read(out)
    pl = pkg["places"]
    assert pl["frame"] == "map"
    assert pl["dock"]["pose"] == [-7.04, -6.89, 171.0] and pl["dock"]["file_pose"] == [-7.11, -6.78, 169.6]
    assert "re-learnt" in pl["dock"]["source"] and pl["dock"]["approach"] == [-9.0, -6.2] and "note" not in pl["dock"]
    spots = {s["id"]: s for s in pl["spots"]}
    assert spots["a"]["pose"] == [-9.5, -3.0, None] and spots["c"]["pose"] == [-10.5, -2.5, 30.0]
    assert "recorded at the robot" in spots["c"]["source"] and spots["c"]["via"] == [[-10.0, -2.0]]
    assert [u["id"] for u in pl["unplaced"]] == ["b"]
    tour = next(w for w in pl["ways"] if w["id"] == "tour")
    assert tour["points"] == [[-9.0, -5.2], [-9.5, -5.0], [-10.0, -4.8]] and set(tour["emma_steps"]) >= {"creep", "forward", "dock"}
    assert pl["hazards"][0]["id"] == "bump" and pl["no_go"][0]["rule"] == "y < -7.00"
    man = pkg["manifest"]
    assert man["made_with_robot"]["id"] == "emma" and set(man["files"]) >= {"map.pgm", "map.yaml", "places.json", "preview.png"}
    assert man["sources"]["spots"]["sha256"] and any("knee height" in n for n in man["not_in_it"])
    assert "no plan transform" in " ".join(man["notes"])
    # import: the same; a folder that is not a package is an error, never a pass
    assert pkg_cli.main(["import", str(out)]) == 0
    assert pkg_cli.main(["import", str(tmp_path)]) == 2
    # and floor_sim walks it for another robot (its dock is the package's, not floor1.json's)
    lay = fs.layout_from_package(out)
    assert lay.dock == (-7.04, -6.89, 171.0) and fs.route_doc(lay)["dock"]["pose"] == [-7.04, -6.89, 171.0]
    lines = []
    res = fs.profile_check(rp.SERVING_EXAMPLE, layout=lay, runs=1, out=lines.append)
    assert "a -> c" in res["walks"] and res["walks"]["a -> c"]["arrived"] == 1
    assert any("AN EXAMPLE" in line for line in lines) and any("spot b" in n for n in res["not_checked"])


def test_export_reads_the_spots_file_as_the_robot_does(tmp_path):
    """Review 20 Nov: a re-learnt dock with no heading ([x, y, null]) crashed the export on
    float(None) -- the robot ignores such a dock (event_host._relearnt) and walks by floor1.json;
    so does the package now, and says so. A recorded spot that is not numbers is not a position."""
    from app import event_host

    cells = np.full((200, 200), mc.FREE_VALUE, dtype=np.int8)
    g = mc.Grid(cells, -12.0, -8.0, 0.05, mp.explore_header(-12.0, -8.0, 200, 200, 0.05))
    route = {"dock": {"pose": [-7.11, -6.78, 169.6], "approach": [-9.0, -6.2]}}
    event = {"spots": {"a": {"pose": [-9.5, -3.0, None]}, "b": {"pose": None}}}
    for bad in ([-7.0, -6.9, None], [-7.0, "x", 171.0], [-7.0], "here"):
        spots = tmp_path / "s.json"
        spots.write_text(json.dumps({"dock": {"pose": bad, "recorded_at": "x"},
                                     "spots": {"b": {"pose": ["?", None]}}}), encoding="utf-8")
        assert event_host._relearnt({"pose": bad}) is None
        places, notes = pkg_cli.places_doc(route=route, event=event, spots_file=spots, stcm_layers=[], grid=g)
        assert places["dock"]["pose"] == [-7.11, -6.78, 169.6] and places["dock"]["source"].startswith("floor1.json"), bad
        assert any("not one the robot uses" in n for n in notes)
        assert [u["id"] for u in places["unplaced"]] == ["b"]
    # the command says FAILED for what it cannot read, never a traceback
    (tmp_path / "map.bin").write_bytes(mp.explore_blob(g))
    (tmp_path / "route.json").write_text(json.dumps({"dock": {"pose": [-7.1, None, 1.0]}}), encoding="utf-8")
    assert pkg_cli.main(["export", "--map", str(tmp_path / "map.bin"), "--route", str(tmp_path / "route.json"),
                         "--transform", str(tmp_path / "none.json"), "--out", str(tmp_path / "pkg")]) == 2


def test_floor_sim_profile_takes_the_robots_dock_from_its_spots_file(tmp_path, monkeypatch):
    """Review 20 Nov: `floor_sim.py profile --spots <robot file>` loaded the layout with no spots
    file, so the dock re-learnt at the robot was dropped (preflight passed it)."""
    spots = tmp_path / "robot.spots.json"
    spots.write_text(json.dumps({"dock": {"pose": [-7.04, -6.89, 171.0], "recorded_at": "19 Nov"}, "spots": {}}),
                     encoding="utf-8")
    seen = []

    def load_layout(*a, **k):
        seen.append(k.get("spots_file"))
        return None                                                       # -> nothing to walk in: exit 2

    monkeypatch.setattr(fs, "load_layout", load_layout)
    assert fs.main(["profile", "--spots", str(spots)]) == 2
    assert seen == [str(spots)]
    seen.clear()
    with pytest.raises(fs.InputError):
        fs.profile_check(rp.SERVING_EXAMPLE, spots_file=spots, out=lambda *_: None)
    assert seen == [spots]
