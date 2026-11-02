"""app/map_check.py + scripts/map_check.py: the robot's map against what its
lidar saw on real walks (31 Oct, round 4).

A made-up room: the map has a ghost (a person a mapping run kept) and a glass
wall the lidar sees through; the room has a table the map lacks. Walks are
simulated by marching each beam through the TRUE room at 1 cm.
"""
from __future__ import annotations

import json
import math
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

from app import map_check as mc

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import map_check as cli  # noqa: E402

RES = 0.05
W_M, H_M = 10.0, 8.0
GHOST = (3.0, 4.0)          # in the map, not in the room
TABLE = (6.0, 5.2)          # in the room, not in the map
PERSON = (5.0, 3.0)         # in the room during walk 1 only
GLASS_X = 8.6               # a wall in the map the lidar sees through


def _true_room(person: tuple[float, float] | None = None) -> np.ndarray:
    """Occupancy at 1 cm, row 0 = y 0."""
    k = 100
    occ = np.zeros((int(H_M * k), int(W_M * k)), dtype=bool)
    occ[:5, :] = occ[-5:, :] = True
    occ[:, :5] = occ[:, -5:] = True
    tx, ty = TABLE
    occ[int((ty - 0.2) * k):int((ty + 0.2) * k), int((tx - 0.3) * k):int((tx + 0.3) * k)] = True
    if person is not None:
        yy, xx = np.mgrid[0:occ.shape[0], 0:occ.shape[1]]
        occ |= (xx / k - person[0]) ** 2 + (yy / k - person[1]) ** 2 < 0.15 ** 2
    return occ


def _map(glass: bool = True) -> mc.Grid:
    h, w = int(H_M / RES), int(W_M / RES)
    cells = np.full((h, w), mc.FREE_VALUE, dtype=np.int8)
    cells[0, :] = cells[-1, :] = mc.OCC_VALUE
    cells[:, 0] = cells[:, -1] = mc.OCC_VALUE
    gx, gy = GHOST
    cells[int((gy - 0.15) / RES):int((gy + 0.15) / RES), int((gx - 0.15) / RES):int((gx + 0.15) / RES)] = mc.OCC_VALUE
    if glass:
        cells[int(1.0 / RES):int(6.5 / RES), int(GLASS_X / RES)] = mc.OCC_VALUE
    header = struct.pack("<ffIIf", 0.0, 0.0, w, h, RES) + b"\0" * 12 + struct.pack("<I", w * h)
    return mc.Grid(cells, 0.0, 0.0, RES, header)


def _scan(room: np.ndarray, pose: tuple[float, float, float], n: int = 360, noise_m: float = 0.02) -> np.ndarray:
    """Beams through the true room; ranges carry the lidar's ~2 cm noise
    (fixed seed per pose) -- without it the margin before a hit is never tested."""
    x, y, yaw = pose
    ang = np.radians(np.arange(n) * 360.0 / n - 180.0)
    r = np.arange(0.16, mc.MAX_RANGE_M, 0.01)
    gx = x + np.cos(ang + yaw)[:, None] * r[None, :]
    gy = y + np.sin(ang + yaw)[:, None] * r[None, :]
    col = np.clip((gx * 100).astype(int), 0, room.shape[1] - 1)
    row = np.clip((gy * 100).astype(int), 0, room.shape[0] - 1)
    hit = room[row, col]
    first = np.where(hit.any(axis=1), hit.argmax(axis=1), -1)
    keep = first >= 0
    rng = np.random.default_rng(int(abs(x * 1000 + y * 100 + yaw * 10)))
    dist = r[first[keep]] + rng.normal(0.0, noise_m, int(keep.sum()))
    return np.stack([ang[keep], dist], axis=1)


PATH = [(1.5 + 0.5 * i, 2.0, 0.0) for i in range(12)] + [(7.0 - 0.5 * i, 6.3, math.pi) for i in range(12)]


def _walk(name: str, *, person: bool, error=(0.0, 0.0, 0.0), path=PATH) -> list[mc.Frame]:
    room = _true_room(PERSON if person else None)
    out = []
    for i, p in enumerate(path):
        recorded = (p[0] + error[0], p[1] + error[1], p[2] + math.radians(error[2]))
        out.append(mc.Frame(name, i, recorded, _scan(room, p), q=70))
    return out


def _near(found, kind, where, tol=0.35):
    return [f for f in found if f["kind"] == kind and math.dist(f["centre"], where) < tol]


@pytest.fixture(scope="module")
def two_walks():
    return _walk("w1", person=True), _walk("w2", person=False)


def _check(grid, *walks, min_runs=2):
    ck = mc.Checker(grid)
    for w in walks:
        for f in w:
            ck.add(f)
    found, masks = ck.findings(min_runs)
    return ck, found, masks


def test_ghost_is_phantom_table_is_new_glass_is_a_line_and_a_person_is_not_new(two_walks):
    ck, found, _ = _check(_map(), *two_walks)
    assert _near(found, "phantom", GHOST), found
    assert _near(found, "new", TABLE), found
    lines = [f for f in found if f["kind"] == "line"]
    assert lines and all(abs(f["centre"][0] - GLASS_X) < 0.1 for f in lines)
    assert not _near(found, "new", PERSON)            # one walk's passer-by
    assert not [f for f in found if f["kind"] == "phantom" and abs(f["centre"][0] - GLASS_X) < 0.2]
    assert all(not r.get("skipped") for r in ck.frames)


def test_one_walk_is_never_enough(two_walks):
    _, found, _ = _check(_map(), two_walks[1])
    assert not [f for f in found if f["kind"] in ("phantom", "new", "line")]


def test_the_boards_pose_a_little_off_is_refined_not_read_as_a_wrong_map():
    """The board's pose carries a bias the same way every walk (~6 cm at the
    dock, 28 Oct) -- laid as it is, every wall would read as a phantom."""
    walks = (_walk("w1", person=False, error=(0.08, 0.08, 2.0)), _walk("w2", person=False, error=(0.07, 0.09, 2.0)))
    ck, found, _ = _check(_map(), *walks)
    assert _near(found, "phantom", GHOST) and _near(found, "new", TABLE)
    # the outer walls, where a 5 cm offset would have read as phantoms all round
    walls = [f for f in found if f["kind"] in ("phantom", "line")
             and (f["box"][0] < 0.3 or f["box"][1] < 0.3 or f["box"][2] > W_M - 0.3 or f["box"][3] > H_M - 0.3)]
    assert not walls, walls
    rows = [r for r in ck.frames if r.get("pose")]
    assert np.mean([r["fit"] for r in rows]) > np.mean([r["fit_board"] for r in rows])


def test_a_frame_whose_pose_is_lost_is_skipped_not_used():
    room = _true_room()
    lost = mc.Frame("w1", 0, (4.0, 5.5, 1.2), _scan(room, (1.5, 2.0, 0.0)))
    ck = mc.Checker(_map())
    row = ck.add(lost)
    assert row["skipped"].startswith("pose not on this map")
    assert not ck.passed                                   # nothing laid on the map
    low = ck.add(mc.Frame("w1", 1, (1.5, 2.0, 0.0), _scan(room, (1.5, 2.0, 0.0)), q=20))
    assert low["skipped"] == "quality 20"


def test_where_the_robot_stood_is_floor_even_if_both_walks_hit_it():
    """A person in a doorway in both walks, gone when the robot drives
    through it -- must not become a wall across the door."""
    spot = (4.5, 2.0)
    frames = []
    for name in ("w1", "w2"):
        for i, p in enumerate(PATH):
            here = math.dist(p[:2], spot) < 0.3
            room = _true_room(None if here else spot)
            frames.append(mc.Frame(name, i, p, _scan(room, p), q=70))
    ck = mc.Checker(_map())
    for f in frames:
        ck.add(f)
    found, _ = ck.findings()
    assert not _near(found, "new", spot), [f for f in found if f["kind"] == "new"]


def test_cleaned_applies_phantoms_and_new_and_leaves_lines_and_kept(two_walks):
    grid = _map()
    _, found, _ = _check(grid, *two_walks)
    ghost = _near(found, "phantom", GHOST)[0]
    table = _near(found, "new", TABLE)[0]
    line = next(f for f in found if f["kind"] == "line")
    clean, applied = mc.cleaned(grid, found, keep=[table["id"]])
    assert ghost["id"] in applied and table["id"] not in applied and line["id"] not in applied

    def cell(g, x, y):
        return int(g.cells[int(y / RES), int(x / RES)])

    assert cell(grid, *GHOST) == mc.OCC_VALUE and cell(clean, *GHOST) == mc.FREE_VALUE
    edge = (TABLE[0], TABLE[1] - 0.19)          # the side the lidar saw (it never sees inside)
    assert cell(clean, *edge) == mc.FREE_VALUE                        # kept: a person said no
    assert cell(clean, GLASS_X, 3.0) == mc.OCC_VALUE                  # glass stays a wall
    clean2, applied2 = mc.cleaned(grid, found, clear=[line["id"]])
    assert cell(clean2, *edge) == mc.OCC_VALUE and line["id"] in applied2
    assert cell(clean2, GLASS_X, 3.0) == mc.FREE_VALUE                # a person looked and said clear it
    assert grid.cells is not clean.cells and cell(grid, *GHOST) == mc.OCC_VALUE   # never in place


def _stcm(grid: mc.Grid) -> bytes:
    def layer(kv: dict, payload: bytes) -> bytes:
        body = struct.pack("<H", len(kv))
        for k, v in kv.items():
            body += struct.pack("<H", len(k)) + k.encode() + struct.pack("<H", len(v)) + v.encode()
        body += payload
        return struct.pack("<I", 4 + len(body)) + body

    layers = [layer({"usage": "home_dock_pose"}, b"\x01\x02\x03"),
              layer({"usage": "explore", "dimension_width": str(grid.w), "dimension_height": str(grid.h),
                     "origin_x": "%.4f" % grid.ox, "origin_y": "%.4f" % grid.oy, "resolution_x": "%.4f" % grid.res},
                    grid.cells.tobytes()),
              layer({"usage": "rectangle_area"}, b"zz")]
    head = b"STCM" + struct.pack("<H", 0x12) + b"\0" * 6 + struct.pack("<I", len(layers)) + b"\0\0"
    return head + b"".join(layers)


def test_stcm_round_trip_replaces_only_the_grid():
    grid = _map()
    blob = _stcm(grid)
    parsed = mc.parse_stcm(blob)
    assert (parsed.cells == grid.cells).all() and (parsed.w, parsed.h) == (grid.w, grid.h)
    changed = mc.Grid(grid.cells.copy(), grid.ox, grid.oy, grid.res)
    changed.cells[10, 10] = mc.OCC_VALUE
    out = mc.stcm_with(blob, changed)
    diff = [i for i, (a, b) in enumerate(zip(blob, out)) if a != b]
    assert len(out) == len(blob) and len(diff) == 1
    assert mc.parse_stcm(out).cells[10, 10] == mc.OCC_VALUE
    other = mc.Grid(grid.cells[:-1].copy(), grid.ox, grid.oy, grid.res)
    with pytest.raises(ValueError):
        mc.stcm_with(blob, other)
    assert mc.parse_explore(mc.explore_bytes(grid)).cells.tobytes() == grid.cells.tobytes()


def test_the_picture_is_the_frame_that_looked_at_it_and_says_where():
    finding = {"centre": [5.0, 2.0]}
    placed = {("w", 0): (3.0, 2.0, 0.0), ("w", 1): (3.0, 2.0, math.pi), ("w", 2): (3.0, 3.0, 0.0)}
    frames = [mc.Frame("w", 0, placed[("w", 0)], np.zeros((0, 2)), picture="0000.jpg"),
              mc.Frame("w", 1, placed[("w", 1)], np.zeros((0, 2)), picture="0001.jpg"),
              mc.Frame("w", 2, placed[("w", 2)], np.zeros((0, 2)), picture="0002.jpg", head_deg=-26.6)]
    pick = mc.picture_for(finding, frames, placed)
    assert pick["picture"] == "0000.jpg" and pick["column"] == pytest.approx(0.5, abs=0.01)
    # facing away: never; from (3,3) the finding is 26.6 deg to the right -- with
    # the head turned right that far it is in the middle of that picture
    only_turned = mc.picture_for(finding, frames[1:], placed)
    assert only_turned["picture"] == "0002.jpg" and only_turned["column"] == pytest.approx(0.5, abs=0.02)
    straight = mc.picture_for(finding, [mc.Frame("w", 2, placed[("w", 2)], np.zeros((0, 2)), picture="x.jpg")], placed)
    assert straight["column"] > 0.6                       # right of the middle without the head turn
    # the head's lidar check: a mirrored lidar (a = -1) flips which side
    mirrored = mc.picture_for(finding, [mc.Frame("v", 2, placed[("w", 2)], np.zeros((0, 2)), picture="x.jpg")],
                              {("v", 2): placed[("w", 2)]}, {"v": {"lidar_a": -1.0, "lidar_b": 0.0}})
    assert mirrored["column"] < 0.4


def _walk_folder(root: Path, name: str, frames: list[mc.Frame], grid: mc.Grid, pictures: bool = False) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    if pictures:
        from PIL import Image

        for fr in frames:
            Image.new("RGB", (64, 48), (90, 90, 90)).save(folder / ("%04d.jpg" % fr.n))
    with (folder / "frames.jsonl").open("w", encoding="utf-8") as f:
        for fr in frames:
            f.write(json.dumps({"n": fr.n, "pose": {"x": fr.pose[0], "y": fr.pose[1], "yaw": fr.pose[2]},
                                "scan": fr.scan.round(3).tolist(), "q": fr.q,
                                "file": ("%04d.jpg" % fr.n) if pictures else None, "head": [0.0, 0.0]}) + "\n")
        f.write('{"n": 999, "pose": {"x"')                 # the half-written last line
    (folder / "map.bin").write_bytes(mc.explore_bytes(grid))
    (folder / "run.json").write_text(json.dumps({"camera": None}), encoding="utf-8")
    return folder


def test_the_script_reads_walk_folders_writes_the_report_and_checks_itself(tmp_path, two_walks):
    grid = _map()
    paths = [_walk_folder(tmp_path, "w1", two_walks[0], grid, pictures=True),
             _walk_folder(tmp_path, "w2", two_walks[1], grid),
             _walk_folder(tmp_path, "w3", _walk("w3", person=False), grid),
             _walk_folder(tmp_path, "w4", _walk("w4", person=False), grid)]
    out = tmp_path / "out"
    res = cli.run(paths, map_path=None, stcm=None, min_runs=2, keep=[], clear=[], out=out)
    text = "\n".join(res["lines"])
    assert (out / "report.png").is_file() and (out / "clean_explore.bin").is_file()
    assert (out / "pictures.jpg").is_file()                           # each finding as the camera saw it
    doc = json.loads((out / "findings.json").read_text("utf-8"))
    ghost = next(f for f in doc["findings"] if f["kind"] == "phantom" and math.dist(f["centre"], GHOST) < 0.35)
    assert ghost["picture"]["run"] == "w1" and 0.0 <= ghost["picture"]["column"] <= 1.0
    assert doc["walks"] == ["w1", "w2", "w3", "w4"] and doc["applied"]
    assert text.count("self-check:") == 2 and "nothing was sent to the robot" in text
    clean = mc.parse_explore((out / "clean_explore.bin").read_bytes())
    assert clean.cells[int(GHOST[1] / RES), int(GHOST[0] / RES)] == mc.FREE_VALUE
    # self-check: the other half's lidar fits the cleaned map better
    m = [ln for ln in res["lines"] if ln.startswith("self-check")]
    for ln in m:
        before, after = (float(v) for v in ln.split("fit mean ")[1].split(",")[0].split(" -> "))
        assert after > before


def test_the_script_refuses_one_walk_agreement_and_a_walk_it_cannot_read(tmp_path, capsys):
    assert cli.main([str(tmp_path), "--min-runs", "1", "--map", str(tmp_path / "x.bin")]) == 1
    assert "passers-by" in capsys.readouterr().err
    assert cli.main([str(tmp_path / "nothing-here"), "--map", str(tmp_path / "x.bin")]) == 1
    assert "not a walk" in capsys.readouterr().err


def test_board_walk_records_and_scan_files_are_walks_too(tmp_path):
    rec = tmp_path / "rec1"
    rec.mkdir()
    room = _true_room()
    pose = (1.5, 2.0, 0.0)
    pts = [{"angle": float(a), "distance": float(d), "valid": True} for a, d in _scan(room, pose)]
    (rec / "step_01.json").write_text(json.dumps({"step": 1, "pose": list(pose), "quality": 66,
                                                  "scan": {"laser_points": pts}}), encoding="utf-8")
    frames, info = cli.load_walk(rec)
    assert info["kind"] == "board_walk record" and frames[0].q == 66 and len(frames[0].scan) == len(pts)
    one = tmp_path / "f1_scan_door.json"
    one.write_text(json.dumps({"laser_points": pts, "pose": {"x": 1.5, "y": 2.0, "yaw": 0.0}}), encoding="utf-8")
    frames, info = cli.load_walk(one)
    assert info["kind"] == "scan" and frames[0].pose == pose


@pytest.mark.parametrize("noise_m", [0.0, 0.02])
def test_a_wall_drawn_10_cm_off_is_not_reported(noise_m):
    """Map walls are never exact (scan maps 5 cm cells, plan maps 10-30 cm off,
    memory "stcm-plan-maps"): the last 10 cm before a return is not "seen
    through", so a wall drawn up to that much in front of the real one is
    not a phantom line all along it -- however quiet the lidar is (with this
    one's ~2 cm noise the hit tolerance alone would cover it; with none it
    would not)."""
    grid = _map(glass=False)
    grid.cells[:, -1] = mc.FREE_VALUE
    grid.cells[1:-1, -3] = mc.OCC_VALUE           # east wall drawn 2 cells (10 cm) inside the room
    room = _true_room()
    walks = [[mc.Frame(name, i, p, _scan(room, p, noise_m=noise_m), q=70) for i, p in enumerate(PATH)]
             for name in ("w1", "w2")]
    _, found, _ = _check(grid, *walks)
    east = [f for f in found if f["kind"] in ("phantom", "line") and f["box"][2] > W_M - 0.5]
    assert not east, east
