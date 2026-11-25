"""A floor's map, places and rules as files another robot can read (the portable map package).

Owner, 20 Nov 2026: "ระบบแผนที่ของเราอาจจะต้องเอาไปใช้กับหุ่นเสิร์ฟด้วย อาจจะต้องทำระบบเผื่อไว้".
Everything we know about floor 1 lives in formats only our code and the Slamtec board read:
the board's .stcm and explore blob, floor1.json, the event file, the robot's spots file, the
map check's findings, a plan transform under .tmp/. A serving robot of another make reads none
of them. The format nearly every robot vendor can take in or convert from is ROS map_server's
occupancy grid (a PGM picture + a YAML of resolution and origin); everything else -- dock,
spots, ways, hazards, no-go -- is plain JSON in THE SAME FRAME as that grid.

A package is a directory:

  package.json      what is here: format/version, the frame, every file's sha256, every source
                    and its sha256, the robot it was made with, what is NOT in it
  map.pgm/.yaml     the occupancy grid to navigate on (trinary: 0 occupied, 254 free, 205
                    unknown -- what `map_saver` writes; ROS thresholds 0.65 / 0.196)
  lidar_truth.pgm/.yaml  (optional) what >= 2 walks' lidar agreed is there, where it differs
  glass.pgm/.yaml   (optional) cells that are solid but the lidar sees through (glass signs,
                    glass doors: the map keeps them on purpose -- CLAUDE.md, never cleared)
  places.json       dock, spots, ways, hazards, no-go zones, the board's own markers
  transform.json    (optional) the architect's plan -> this map's frame, and back
  preview.png       for people (scripts/map_package.py draws it)

The frame: metres, the board's map frame (x, y as floor1.json and the board's poses), yaw in
degrees counter-clockwise from +x. ROS's YAML `origin` is the lower-left corner of the
lower-left cell -- the explore grid's (ox, oy) exactly, so no point moves between the two.
Floats are written with repr(): the board's float32 origin comes back to the bit.

What a trinary grid keeps: occupied / free / unknown. The board's grid grades cells
-127..127; every tool of ours reads it through the same cut (occupied < -10, free > 10:
board_walk.MapFit, map_check.Grid, floor_sim.Grid), so the grades between are not used by
anything and are not exported. tests/test_map_package.py holds map_check's and floor_sim's
results on an imported map to the original's.

numpy only (no PIL, no PyYAML needed): the drawing is the script's.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from pathlib import Path
from typing import Any

import numpy as np

from app import map_check as mc

FORMAT = "condo-voice/map-package"
VERSION = 1

#: map_saver's trinary values and the thresholds a map_server reads them with
PGM_OCC, PGM_FREE, PGM_UNKNOWN = 0, 254, 205
OCC_THRESH, FREE_THRESH = 0.65, 0.196

FRAME = {
    "name": "map",
    "units": "m",
    "axes": "the robot board's map frame (floor1.json, the board's poses): right-handed, x and y as the board reads them",
    "yaw": "degrees, counter-clockwise from +x (as floor1.json); ROS quaternions take it in radians",
    "grid_origin": "the YAML `origin` = the lower-left corner of the lower-left cell (ROS map_server) = the board's explore origin",
    "image_rows": "PGM row 0 is the TOP of the map (largest y); the board's grid row 0 is the bottom",
}


class PackageError(Exception):
    """A package (or a vendor's map) that cannot be read as one: said, never guessed round."""


# ---- grids ---------------------------------------------------------------------------------
def grid_pgm(grid: mc.Grid) -> bytes:
    """The grid as a binary PGM (P5), trinary, top row first."""
    occ, free = grid.occ(), grid.free()
    img = np.full(grid.cells.shape, PGM_UNKNOWN, dtype=np.uint8)
    img[free] = PGM_FREE
    img[occ] = PGM_OCC
    return b"P5\n%d %d\n255\n" % (grid.w, grid.h) + img[::-1].tobytes()


def mask_pgm(mask: np.ndarray) -> bytes:
    """A boolean layer of the same geometry (glass): True = 0 (black), False = 254."""
    img = np.where(mask, PGM_OCC, PGM_FREE).astype(np.uint8)
    return b"P5\n%d %d\n255\n" % (mask.shape[1], mask.shape[0]) + img[::-1].tobytes()


def grid_yaml(grid: mc.Grid, image: str) -> str:
    return ("image: %s\nmode: trinary\nresolution: %r\norigin: [%r, %r, 0.0]\nnegate: 0\n"
            "occupied_thresh: %r\nfree_thresh: %r\n" % (image, float(grid.res), float(grid.ox), float(grid.oy),
                                                      OCC_THRESH, FREE_THRESH))


def read_pgm(blob: bytes) -> tuple[np.ndarray, int]:
    """A PGM (P5 binary or P2 ASCII, 8 or 16 bit, # comments) -> (values, the header's maxval),
    row 0 = top. The maxval is the image's white -- a maxval-100 image's 100 is white (review
    20 Nov: scaling by 255 instead read its free floor as unknown)."""
    magic = blob[:2]
    if magic not in (b"P5", b"P2"):
        raise PackageError("not a PGM (starts %r)" % blob[:2])
    tokens: list[bytes] = []
    pos = 2
    while len(tokens) < 3:
        m = re.compile(rb"\s*(#[^\n]*\n\s*)*([0-9]+)").match(blob, pos)
        if not m:
            raise PackageError("PGM header cut short")
        tokens.append(m.group(2))
        pos = m.end()
    w, h, maxval = (int(t) for t in tokens)
    if not (0 < maxval < 65536) or w <= 0 or h <= 0:
        raise PackageError("PGM %d x %d max %d" % (w, h, maxval))
    if magic == b"P2":
        vals = np.array(blob[pos:].split(), dtype=np.int64)
        if len(vals) < w * h:
            raise PackageError("PGM holds %d of %d values" % (len(vals), w * h))
        return vals[: w * h].reshape(h, w), maxval
    pos += 1                                                  # the single whitespace after maxval
    width = 1 if maxval < 256 else 2
    data = blob[pos:pos + w * h * width]
    if len(data) < w * h * width:
        raise PackageError("PGM holds %d of %d bytes" % (len(data), w * h * width))
    return np.frombuffer(data, dtype=np.uint8 if width == 1 else ">u2").reshape(h, w).astype(np.int64), maxval


def _yaml_value(text: str) -> Any:
    text = text.split("#", 1)[0].strip()
    if text.startswith("[") and text.endswith("]"):
        return [_yaml_value(v) for v in text[1:-1].split(",") if v.strip()]
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text.strip("'\"")


def read_yaml(text: str) -> dict:
    """A map_server YAML (flat `key: value`, inline lists). PyYAML when it is installed."""
    try:
        import yaml  # noqa: PLC0415

        doc = yaml.safe_load(text)
        if isinstance(doc, dict):
            return doc
    except ImportError:
        pass
    except Exception as exc:                              # noqa: BLE001 -- a broken file is the caller's to hear
        raise PackageError("map YAML: %s" % exc) from None
    out = {}
    for line in text.splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, v = line.split(":", 1)
            out[k.strip()] = _yaml_value(v)
    return out


def explore_header(ox: float, oy: float, w: int, h: int, res: float) -> bytes:
    """The board's explore-blob header (board_walk.MapFit, map_check.parse_explore)."""
    head = struct.pack("<ffIIf", ox, oy, w, h, res)
    return head + b"\0" * (32 - len(head)) + struct.pack("<I", w * h)


def load_map_yaml(path: str | Path) -> mc.Grid:
    """A ROS map_server map (YAML + its image) -> the board-style grid our tools read:
    occupied -127, free 127, unknown 0, row 0 = the bottom, with an explore header.

    ROS semantics: p = (maxval - v) / maxval (negate 0) or v / maxval (negate 1), maxval =
    the PGM header's; occupied when p > occupied_thresh, free when p < free_thresh, else
    unknown; mode `raw` = the value is the occupancy itself, 0..100 -- anything else is
    UNKNOWN (nav2's map_saver writes unknown as 255; review 20 Nov: it read as a wall). A
    rotated origin (yaw != 0) is refused: every tool here takes the grid's axes for the frame's."""
    path = Path(path)
    try:
        meta = read_yaml(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PackageError("map YAML %s: %s" % (path, exc)) from None
    for key in ("image", "resolution", "origin"):
        if key not in meta:
            raise PackageError("map YAML %s has no `%s`" % (path, key))
    origin = meta["origin"]
    if not isinstance(origin, list) or len(origin) < 2:
        raise PackageError("map YAML %s: origin %r" % (path, origin))
    if len(origin) > 2 and abs(float(origin[2])) > 1e-9:
        raise PackageError("map YAML %s: the grid is turned %.4f rad against its frame -- not supported (ask for "
                           "an unrotated export, or turn the places with it)" % (path, float(origin[2])))
    image = Path(str(meta["image"]))
    image = image if image.is_absolute() else path.parent / image
    try:
        raw, maxval = read_pgm(image.read_bytes())
    except OSError as exc:
        raise PackageError("map image %s: %s" % (image, exc)) from None
    h, w = raw.shape
    mode = str(meta.get("mode") or "trinary")
    occ_t = float(meta.get("occupied_thresh", OCC_THRESH))
    free_t = float(meta.get("free_thresh", FREE_THRESH))
    if mode == "raw":
        known = (raw >= 0) & (raw <= 100)
        occ, free = known & (raw >= 100 * occ_t), known & (raw <= 100 * free_t)
    else:
        p = raw / float(maxval) if int(meta.get("negate", 0)) else (maxval - raw) / float(maxval)
        occ, free = p > occ_t, p < free_t
    cells = np.zeros((h, w), dtype=np.int8)
    cells[free] = mc.FREE_VALUE
    cells[occ] = mc.OCC_VALUE
    cells = cells[::-1].copy()                                # row 0 = the bottom
    ox, oy, res = float(origin[0]), float(origin[1]), float(meta["resolution"])
    return mc.Grid(cells, ox, oy, res, explore_header(ox, oy, w, h, res))


def load_mask_yaml(path: str | Path, like: mc.Grid) -> np.ndarray:
    """A boolean layer (glass) of the same geometry as `like`: occupied pixels = True."""
    g = load_map_yaml(path)
    if (g.w, g.h) != (like.w, like.h) or abs(g.ox - like.ox) > 1e-6 or abs(g.oy - like.oy) > 1e-6 or abs(g.res - like.res) > 1e-9:
        raise PackageError("%s is not laid on the same grid as the map" % path)
    return g.occ()


def explore_blob(grid: mc.Grid) -> bytes:
    """board_walk/floor_sim's explore blob for any grid (an imported one has a made header)."""
    header = grid.header if len(grid.header) == 36 else explore_header(grid.ox, grid.oy, grid.w, grid.h, grid.res)
    return header + grid.cells.astype(np.int8).tobytes()


# ---- the board's other layers ----------------------------------------------------------------
def stcm_layers(blob: bytes) -> list[dict]:
    """Every layer of a Slamtec .stcm but the grid: its keys, and what of it is read.

    Read: `pose-map` layers (home_dock_pose, point_of_interest) -- per entry a u16-length
    id, one byte, x/y/yaw as float32 (yaw in radians), one byte, a u16 count of u16-length
    key/value strings. Measured on our floor-1 files (one entry: the board's dock marker
    (-7.078, -6.240) 168.8 deg, the 28 Oct registration -- board_walk does not use it). The
    reading must account for every byte of the layer or the layer is reported unread.
    NOT read: line maps (virtual walls / tracks) and rectangle areas (forbidden) -- empty on
    floor 1 (count 0); a layer with entries is listed with its count and size, unread."""
    out = []
    for kv, p, end in mc._stcm_layers(blob):
        usage = kv.get("usage", "")
        if usage == "explore":
            continue
        row = {"usage": usage, "type": kv.get("type"), "count": int(kv.get("count") or 0), "bytes": end - p,
               "read": False, "entries": []}
        if row["count"] == 0 and end == p:
            row["read"] = True
        elif "pose-map" in (kv.get("type") or ""):
            entries = _pose_map(blob[p:end], row["count"])
            if entries is not None:
                row["read"], row["entries"] = True, entries
        out.append(row)
    return out


def _pose_map(data: bytes, count: int) -> list[dict] | None:
    pos, entries = 0, []
    try:
        for _ in range(count):
            (n,) = struct.unpack_from("<H", data, pos)
            ident = data[pos + 2:pos + 2 + n].decode("utf-8")
            pos += 2 + n + 1
            x, y, yaw = struct.unpack_from("<fff", data, pos)
            pos += 12 + 1
            (k,) = struct.unpack_from("<H", data, pos)
            pos += 2
            meta = {}
            for _ in range(k):
                (a,) = struct.unpack_from("<H", data, pos)
                key = data[pos + 2:pos + 2 + a].decode("utf-8")
                pos += 2 + a
                (a,) = struct.unpack_from("<H", data, pos)
                meta[key] = data[pos + 2:pos + 2 + a].decode("utf-8")
                pos += 2 + a
            entries.append({"id": ident, "pose": [round(x, 4), round(y, 4), round(math.degrees(yaw), 2)], "metadata": meta})
    except (struct.error, UnicodeDecodeError):
        return None
    return entries if pos == len(data) else None


# ---- the plan's frame ------------------------------------------------------------------------
def plan_to_map(pt, t: dict) -> tuple[float, float]:
    """robot = R(turn) * (plan - pivot) + pivot + shift (plan_to_robot.json)."""
    a = math.radians(float(t["turn_deg"]))
    px, py = float(pt[0]) - t["pivot"][0], float(pt[1]) - t["pivot"][1]
    return (math.cos(a) * px - math.sin(a) * py + t["pivot"][0] + t["shift"][0],
            math.sin(a) * px + math.cos(a) * py + t["pivot"][1] + t["shift"][1])


def map_to_plan(pt, t: dict) -> tuple[float, float]:
    a = -math.radians(float(t["turn_deg"]))
    px, py = float(pt[0]) - t["pivot"][0] - t["shift"][0], float(pt[1]) - t["pivot"][1] - t["shift"][1]
    return (math.cos(a) * px - math.sin(a) * py + t["pivot"][0], math.sin(a) * px + math.cos(a) * py + t["pivot"][1])


# ---- writing and reading a package -----------------------------------------------------------
def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write(out: str | Path, *, name: str, grid: mc.Grid, places: dict, profile: dict, sources: dict,
          truth: mc.Grid | None = None, glass: np.ndarray | None = None, transform: dict | None = None,
          not_in_it: list[str] | None = None, notes: list[str] | None = None, extra_files: dict | None = None) -> dict:
    """Write a package; returns its manifest. `extra_files`: {name: bytes} (the preview)."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    files: dict[str, bytes] = {"map.pgm": grid_pgm(grid), "map.yaml": grid_yaml(grid, "map.pgm").encode()}
    layers = {"map": "map.yaml"}
    if truth is not None:
        files["lidar_truth.pgm"] = grid_pgm(truth)
        files["lidar_truth.yaml"] = grid_yaml(truth, "lidar_truth.pgm").encode()
        layers["lidar_truth"] = "lidar_truth.yaml"
    if glass is not None:
        like = truth if truth is not None else grid
        if glass.shape != like.cells.shape:
            raise PackageError("the glass layer is %s, the grid it belongs to %s" % (glass.shape, like.cells.shape))
        files["glass.pgm"] = mask_pgm(glass)
        files["glass.yaml"] = grid_yaml(like, "glass.pgm").encode()
        layers["glass"] = "glass.yaml"
    files["places.json"] = json.dumps(dict(places, frame="map"), ensure_ascii=False, indent=1).encode()
    if transform is not None:
        files["transform.json"] = json.dumps(transform, ensure_ascii=False, indent=1).encode()
    files.update(extra_files or {})
    for fname, data in files.items():
        (out / fname).write_bytes(data)
    manifest = {
        "format": FORMAT, "version": VERSION, "name": name, "frame": FRAME, "layers": layers,
        "grid": {"width": grid.w, "height": grid.h, "resolution": float(grid.res), "origin": [float(grid.ox), float(grid.oy), 0.0]},
        "made_with_robot": profile, "sources": sources,
        "files": {fname: sha256(data) for fname, data in sorted(files.items())},
        "not_in_it": list(not_in_it or []), "notes": list(notes or []),
    }
    (out / "package.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


def read(path: str | Path, *, verify: bool = True) -> dict:
    """A package -> {"manifest", "grid", "truth", "glass", "places", "transform", "dir"}.
    `verify`: every file's sha256 must match the manifest (a hand-edited file is said)."""
    path = Path(path)
    try:
        manifest = json.loads((path / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PackageError("%s is not a map package (package.json: %s)" % (path, exc)) from None
    if manifest.get("format") != FORMAT:
        raise PackageError("%s: format %r, not %s" % (path, manifest.get("format"), FORMAT))
    if int(manifest.get("version") or 0) > VERSION:
        raise PackageError("%s: version %s is newer than this reader (%d)" % (path, manifest.get("version"), VERSION))
    if verify:
        for fname, digest in (manifest.get("files") or {}).items():
            try:
                got = sha256((path / fname).read_bytes())
            except OSError:
                raise PackageError("%s: %s is missing" % (path, fname)) from None
            if got != digest:
                raise PackageError("%s: %s was changed after the package was written (sha256)" % (path, fname))
    layers = manifest.get("layers") or {}
    grid = load_map_yaml(path / layers.get("map", "map.yaml"))
    truth = load_map_yaml(path / layers["lidar_truth"]) if layers.get("lidar_truth") else None
    glass = load_mask_yaml(path / layers["glass"], truth if truth is not None else grid) if layers.get("glass") else None
    try:
        places = json.loads((path / "places.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PackageError("%s: places.json: %s" % (path, exc)) from None
    transform = None
    if (path / "transform.json").exists():
        transform = json.loads((path / "transform.json").read_text(encoding="utf-8"))
    return {"manifest": manifest, "grid": grid, "truth": truth, "glass": glass, "places": places,
            "transform": transform, "dir": path}


# ---- checking a package for a robot ------------------------------------------------------------
def _nearest_occ(grid: mc.Grid, x: float, y: float, reach: float = 2.0) -> float:
    row, col = int(math.floor((y - grid.oy) / grid.res)), int(math.floor((x - grid.ox) / grid.res))
    n = int(reach / grid.res) + 1
    r0, r1, c0, c1 = max(0, row - n), min(grid.h, row + n + 1), max(0, col - n), min(grid.w, col + n + 1)
    rr, cc = np.nonzero(grid.occ()[r0:r1, c0:c1])
    if not len(rr):
        return reach
    wx, wy = grid.world(rr + r0, cc + c0)
    return float(np.hypot(wx - x, wy - y).min())


def check(pkg: dict, profile) -> list[dict]:
    """What a robot of `profile` (app/robot_profile.py) would meet on this package, from the
    grid alone (no walking: floor_sim does that): every place inside the grid and on floor
    the grid knows; the body fits there (half its widest point clear of anything occupied);
    room to turn; the dock not standing in a wall the board's map still has. Each row:
    {"what", "id", "ok", "detail"}."""
    grid = pkg["truth"] if pkg.get("truth") is not None else pkg["grid"]
    places = pkg["places"]
    half = profile.body.reach_width_m / 2
    turn = profile.body.turn_radius_m
    rows = []

    def inside(x, y):
        return grid.ox <= x < grid.ox + grid.w * grid.res and grid.oy <= y < grid.oy + grid.h * grid.res

    def cell(x, y):
        return int(grid.cells[int(math.floor((y - grid.oy) / grid.res)), int(math.floor((x - grid.ox) / grid.res))])

    dock = places.get("dock") or {}
    if dock.get("pose"):
        x, y = dock["pose"][0], dock["pose"][1]
        if not inside(x, y):
            rows.append({"what": "dock", "id": "dock", "ok": False, "detail": "outside the grid"})
        else:
            d = _nearest_occ(pkg["grid"], x, y)
            rows.append({"what": "dock", "id": "dock", "ok": d >= 0.15,
                         "detail": "the map has an occupied cell %.2f m from the dock pose%s" % (
                             d, " -- the robot sits there: a stale wall in the map (a planner may refuse the dock, "
                                "a vendor importer may reject it)" if d < 0.15 else "")})
    for sp in places.get("spots") or []:
        pose = sp.get("pose")
        if not pose:
            continue
        x, y = pose[0], pose[1]
        if not inside(x, y):
            rows.append({"what": "spot", "id": sp["id"], "ok": False, "detail": "outside the grid"})
            continue
        known = cell(x, y)
        d = _nearest_occ(grid, x, y)
        bad = []
        if known < mc.OCC_BELOW:
            bad.append("on an occupied cell")
        elif known <= mc.FREE_ABOVE:
            bad.append("on unknown floor")
        if d < half + profile.min_gap_m:
            bad.append("the body does not fit: nearest thing %.2f m, half its widest point %.2f" % (d, half))
        elif d < turn + 0.03:
            bad.append("no room to turn on the spot: nearest thing %.2f m, it sweeps %.2f" % (d, turn))
        rows.append({"what": "spot", "id": sp["id"], "ok": not bad,
                     "detail": "; ".join(bad) or "nearest thing %.2f m (half-width %.2f, turn %.2f)" % (d, half, turn)})
    for hz in places.get("hazards") or []:
        ok = inside(hz["x"], hz["y"])
        rows.append({"what": "hazard", "id": hz.get("id"), "ok": ok,
                     "detail": "keep the centre %.2f m off (a thing under the lidar plane: in no grid)" % hz.get("radius_m", 0.0)
                     if ok else "outside the grid"})
    return rows
