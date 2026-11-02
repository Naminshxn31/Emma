"""Check the robot's map against what its lidar saw on real walks (app/map_check.py).

    python scripts/map_check.py .tmp/walk-frames/RUN1 .tmp/walk-frames/RUN2 [...]
    python scripts/map_check.py --last 3                 # the newest pulled walk-frame runs
    python scripts/map_check.py REC_DIR ... --map board.stcm --stcm board.stcm

Each argument is ONE walk: a walk-frames run folder (frames.jsonl, + the map
the board had, map.bin), a board_walk --record folder (step_*.json) or a
single scan .json. Separate walks are what tell a table from a person.

Writes to --out (default .tmp/map-check/<time>/): report.png (the map, what
disagrees, numbered), pictures.jpg (each finding as the head camera saw it,
walk-frame runs only), findings.json, and the cleaned map -- clean.stcm (the
--stcm file with only its grid replaced) or clean_explore.bin. Nothing goes
to the robot: uploading is a separate step, on the owner's go.

With 2 x --min-runs walks or more it also checks ITSELF: the map is cleaned
from half the walks and the lidar of the other half is scored on it.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import map_check as mc  # noqa: E402

WALKS = ROOT / ".tmp" / "walk-frames"
OUT = ROOT / ".tmp" / "map-check"
COLORS = {"phantom": (230, 40, 40), "new": (40, 110, 235), "line": (200, 60, 200), "changing": (240, 160, 30)}
MAX_PICTURES = 12


class Fail(Exception):
    pass


# -- reading walks -------------------------------------------------------------
def _pose(raw) -> tuple[float, float, float] | None:
    if isinstance(raw, dict) and raw.get("x") is not None:
        return float(raw["x"]), float(raw["y"]), float(raw.get("yaw") or 0.0)
    if isinstance(raw, (list, tuple)) and len(raw) >= 3:
        return float(raw[0]), float(raw[1]), float(raw[2])
    return None


def _points(raw) -> np.ndarray:
    if isinstance(raw, dict):
        raw = raw.get("laser_points") or []
    pts = []
    for p in raw or []:
        if isinstance(p, dict):
            if p.get("valid", True):
                pts.append((float(p["angle"]), float(p["distance"])))
        elif isinstance(p, (list, tuple)) and len(p) >= 2:
            pts.append((float(p[0]), float(p[1])))
    return np.array(pts, dtype=float).reshape(-1, 2)


def load_walk(path: Path) -> tuple[list[mc.Frame], dict]:
    """(frames, info) for one walk; info: kind, folder, map (path or None), camera."""
    name = path.stem if path.is_file() else path.name
    info: dict = {"name": name, "folder": path if path.is_dir() else path.parent, "map": None, "camera": None}
    frames: list[mc.Frame] = []
    if path.is_dir() and (path / "frames.jsonl").is_file():
        info["kind"] = "walk frames"
        for line in (path / "frames.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            pose = _pose(r.get("pose"))
            if pose is None or not r.get("scan"):
                continue
            head = r.get("head") or [0.0, 0.0]
            frames.append(mc.Frame(name, int(r.get("n", len(frames))), pose, _points(r["scan"]), r.get("q"),
                                   r.get("file"), float(head[0] or 0.0)))
        if (path / "map.bin").is_file():
            info["map"] = path / "map.bin"
        try:
            cam = json.loads((path / "run.json").read_text(encoding="utf-8")).get("camera")
            info["camera"] = cam if isinstance(cam, dict) else None
        except (OSError, ValueError, AttributeError):
            pass
    elif path.is_dir() and any(path.glob("step_*.json")):
        info["kind"] = "board_walk record"
        for f in sorted(path.glob("step_*.json")):
            r = json.loads(f.read_text(encoding="utf-8"))
            pose = _pose(r.get("pose"))
            if pose is not None and r.get("scan"):
                frames.append(mc.Frame(name, int(r.get("step", len(frames))), pose, _points(r["scan"]),
                                       r.get("quality")))
    elif path.is_file() and path.suffix == ".json":
        info["kind"] = "scan"
        r = json.loads(path.read_text(encoding="utf-8"))
        pose = _pose(r.get("pose"))
        if pose is not None:
            frames.append(mc.Frame(name, 0, pose, _points(r), r.get("quality")))
    else:
        raise Fail("not a walk: %s (want a walk-frames run, a board_walk --record folder or a scan .json)" % path)
    if not frames:
        raise Fail("no frames with a pose and a lidar scan in %s" % path)
    return frames, info


def load_map(path: Path) -> tuple[mc.Grid, str]:
    blob = path.read_bytes()
    if blob[:4] == b"STCM":
        return mc.parse_stcm(blob), "stcm"
    return mc.parse_explore(blob), "explore"


# -- drawing -------------------------------------------------------------------
def _font(size: int):
    from PIL import ImageFont

    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def report_png(grid: mc.Grid, masks: dict, found: list[dict], rows: list[dict], out: Path) -> None:
    from PIL import Image, ImageDraw

    poses = [r["pose"] for r in rows if r.get("pose")]
    xs = [p[0] for p in poses] + [f["centre"][0] for f in found]
    ys = [p[1] for p in poses] + [f["centre"][1] for f in found]
    if not xs:
        return
    pad = 3.0
    c0 = max(0, int((min(xs) - pad - grid.ox) / grid.res))
    c1 = min(grid.w, int((max(xs) + pad - grid.ox) / grid.res) + 1)
    r0 = max(0, int((min(ys) - pad - grid.oy) / grid.res))
    r1 = min(grid.h, int((max(ys) + pad - grid.oy) / grid.res) + 1)
    cells = grid.cells[r0:r1, c0:c1]
    rgb = np.full(cells.shape + (3,), 205, dtype=np.uint8)
    rgb[cells > mc.FREE_ABOVE] = (250, 250, 248)
    rgb[cells < mc.OCC_BELOW] = (40, 40, 46)
    for kind in ("changing", "new", "phantom"):
        if kind in masks:
            rgb[masks[kind][r0:r1, c0:c1]] = COLORS[kind]
    for f in found:         # a "line" is a phantom by mask; its own colour on top
        if f["kind"] == "line":
            rr = np.asarray(f["rc"][0]) - r0
            cc = np.asarray(f["rc"][1]) - c0
            ok = (rr >= 0) & (rr < rgb.shape[0]) & (cc >= 0) & (cc < rgb.shape[1])
            rgb[rr[ok], cc[ok]] = COLORS["line"]
    img = Image.fromarray(rgb[::-1].copy())           # row 0 is the bottom of the map
    k = max(1.0, min(4.0, 1100 / max(img.width, img.height)))
    img = img.resize((int(img.width * k), int(img.height * k)), Image.NEAREST)
    d = ImageDraw.Draw(img)

    def at(x: float, y: float) -> tuple[float, float]:
        return ((x - grid.ox) / grid.res - c0) * k, (r1 - (y - grid.oy) / grid.res) * k

    for p in poses:
        px, py = at(p[0], p[1])
        d.ellipse([px - 2, py - 2, px + 2, py + 2], fill=(40, 170, 80))
    f14 = _font(14)
    for f in found:
        if f["kind"] == "changing" or f["cells"] < 6:
            continue
        px, py = at(*f["centre"])
        d.text((px + 5, py - 7), str(f["id"]), fill=COLORS[f["kind"]], font=f14, stroke_width=2,
               stroke_fill=(255, 255, 255))
    banner = Image.new("RGB", (img.width, 44), (255, 255, 255))
    bd = ImageDraw.Draw(banner)
    bd.text((8, 4), "red = map has it, lidar saw through (phantom)   blue = lidar hit, map free (new)", fill=(30, 30, 30), font=f14)
    bd.text((8, 23), "purple = long thin phantom (glass or wall drawn off: never auto-cleared)   orange = changes   green = robot",
            fill=(30, 30, 30), font=f14)
    full = Image.new("RGB", (img.width, img.height + 44), (255, 255, 255))
    full.paste(banner, (0, 0))
    full.paste(img, (0, 44))
    full.save(out)


def pictures_jpg(found: list[dict], picks: dict[int, dict], folders: dict[str, Path], out: Path) -> bool:
    from PIL import Image, ImageDraw

    tiles = []
    for f in found:
        pick = picks.get(f["id"])
        if not pick or len(tiles) >= MAX_PICTURES:
            continue
        path = folders[pick["run"]] / pick["picture"]
        try:
            pic = Image.open(path).convert("RGB")
        except OSError:
            continue
        pic.thumbnail((420, 320))
        d = ImageDraw.Draw(pic)
        x = pick["column"] * pic.width
        d.line([(x, 0), (x, pic.height)], fill=COLORS[f["kind"]], width=3)
        tile = Image.new("RGB", (420, 320 + 40), (0, 0, 0))
        tile.paste(pic, ((420 - pic.width) // 2, 0))
        td = ImageDraw.Draw(tile)
        td.text((6, 322), "#%d %s %.1fx%.1f m, %d walks" % (f["id"], f["kind"], f["length_m"], f["width_m"], f["runs"]),
                fill=COLORS[f["kind"]], font=_font(15))
        td.text((6, 340), "%s #%d, %.1f m away" % (pick["run"][:28], pick["n"], pick["distance_m"]),
                fill=(200, 200, 200), font=_font(13))
        tiles.append(tile)
    if not tiles:
        return False
    cols = 3
    rows_n = math.ceil(len(tiles) / cols)
    sheet = Image.new("RGB", (cols * 426, rows_n * 366), (0, 0, 0))
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % cols) * 426, (i // cols) * 366))
    sheet.save(out, quality=85)
    return True


# -- the run -------------------------------------------------------------------
def _check(grid: mc.Grid, frames: list[mc.Frame], min_runs: int):
    ck = mc.Checker(grid)
    for f in frames:
        ck.add(f)
    found, masks = ck.findings(min_runs)
    return ck, found, masks


def self_test(grid: mc.Grid, walks: list[list[mc.Frame]], min_runs: int) -> list[str]:
    """Clean from every other walk, score the rest on it -- and the other way."""
    if len(walks) < 2 * min_runs:
        return ["self-check: needs %d walks or more (have %d) -- not run" % (2 * min_runs, len(walks))]
    lines = []
    halves = (walks[0::2], walks[1::2])
    for build, test in (halves, halves[::-1]):
        _, found, _ = _check(grid, [f for w in build for f in w], min_runs)
        clean, applied = mc.cleaned(grid, found)
        before, after = mc.Checker(grid), mc.Checker(clean)
        fb = [before.place(f)[2] for w in test for f in w]
        fa = [after.place(f)[2] for w in test for f in w]
        lines.append("self-check: cleaned from %d walks (%d changes), lidar of the other %d on it: "
                     "fit mean %.2f -> %.2f, worst %.2f -> %.2f"
                     % (len(build), len(applied), len(test), np.mean(fb), np.mean(fa), min(fb), min(fa)))
    return lines


def run(paths: list[Path], *, map_path: Path | None, stcm: Path | None, min_runs: int,
        keep: list[int], clear: list[int], out: Path) -> dict:
    walks, infos = [], []
    for p in paths:
        frames, info = load_walk(p)
        walks.append(frames)
        infos.append(info)
    names = [i["name"] for i in infos]
    if len(set(names)) != len(names):
        raise Fail("two walks with the same name: %s" % names)
    notes = []
    if map_path is None:
        maps = [i["map"] for i in infos if i["map"] is not None]
        if not maps:
            raise Fail("no map: these walks did not keep one (map.bin) -- give --map")
        digests = {hashlib.sha256(m.read_bytes()).hexdigest() for m in maps}
        if len(digests) > 1:
            notes.append("the walks were on %d different maps -- checked against the first (%s)"
                         % (len(digests), maps[0].parent.name))
        map_path = maps[0]
    grid, kind = load_map(map_path)
    frames = [f for w in walks for f in w]
    ck, found, masks = _check(grid, frames, min_runs)
    placed = {(r["run"], r["n"]): tuple(r["pose"]) for r in ck.frames if r.get("pose")}
    cams = {i["name"]: i["camera"] for i in infos}
    folders = {i["name"]: i["folder"] for i in infos}
    picks = {}
    with_pictures = [fr for fr in frames if fr.picture]
    for f in found:
        pick = mc.picture_for(f, with_pictures, placed, cams) if with_pictures else None
        if pick:
            picks[f["id"]] = pick
            f["picture"] = pick
    clean, applied = mc.cleaned(grid, found, keep=keep, clear=clear)
    out.mkdir(parents=True, exist_ok=True)
    report_png(grid, masks, found, ck.frames, out / "report.png")
    has_pics = pictures_jpg(found, picks, folders, out / "pictures.jpg")
    written = []
    if kind == "stcm":
        (out / "clean.stcm").write_bytes(mc.stcm_with(map_path.read_bytes(), clean))
        written.append("clean.stcm")
    else:
        (out / "clean_explore.bin").write_bytes(mc.explore_bytes(clean))
        written.append("clean_explore.bin")
        if stcm is not None:
            (out / "clean.stcm").write_bytes(mc.stcm_with(stcm.read_bytes(), clean))
            written.append("clean.stcm")
    used = [r for r in ck.frames if r.get("pose")]
    skipped = [r for r in ck.frames if r.get("skipped")]
    counts = {k: sum(1 for f in found if f["kind"] == k) for k in ("phantom", "new", "line", "changing")}
    lines = ["map: %s (%s, %d x %d at %.2f m)" % (map_path, kind, grid.w, grid.h, grid.res),
             "walks: %d (%s) -- frames used %d, skipped %d" % (len(walks), ", ".join(names), len(used), len(skipped))]
    if used:
        fb = [r["fit_board"] for r in used]
        fa = [r["fit"] for r in used]
        lines.append("lidar on the map: %.2f mean at the board's poses, %.2f after refining (worst %.2f)"
                     % (np.mean(fb), np.mean(fa), min(fa)))
    lines.append("findings: %d phantom, %d new, %d line, %d changing -- %d applied to the clean map"
                 % (counts["phantom"], counts["new"], counts["line"], counts["changing"], len(applied)))
    lines += notes
    lines += self_test(grid, walks, min_runs)
    lines.append("wrote: report.png%s, findings.json, %s -> %s"
                 % (", pictures.jpg" if has_pics else "", ", ".join(written), out))
    lines.append("nothing was sent to the robot")
    doc = {"map": str(map_path), "walks": names, "min_runs": min_runs, "keep": keep, "clear": clear,
           "applied": applied, "frames": ck.frames, "findings": found, "summary": lines}
    (out / "findings.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"lines": lines, "found": found, "applied": applied, "out": out}


def _ids(text: str) -> list[int]:
    return [int(t) for t in text.replace(",", " ").split() if t.strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("walks", nargs="*", type=Path, help="one per walk")
    ap.add_argument("--last", type=int, default=0, help="the newest N runs under .tmp/walk-frames")
    ap.add_argument("--map", type=Path, help="the map to check (.stcm or explore .bin); default the walks' map.bin")
    ap.add_argument("--stcm", type=Path, help="with an explore map: write clean.stcm from this .stcm too")
    ap.add_argument("--min-runs", type=int, default=mc.MIN_RUNS, help="walks that must agree (default 2)")
    ap.add_argument("--keep", default="", help="finding ids to leave as they are (looked at, wrong)")
    ap.add_argument("--clear", default="", help="'line'/'changing' ids to apply anyway (looked at, right)")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    paths = list(a.walks)
    if a.last:
        runs = sorted(p for p in WALKS.iterdir() if p.is_dir()) if WALKS.is_dir() else []
        paths += runs[-a.last:]
    try:
        if not paths:
            raise Fail("no walks given")
        if a.min_runs < 2:
            raise Fail("--min-runs below 2 would write one walk's passers-by into the map")
        out = a.out or OUT / _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        res = run(paths, map_path=a.map, stcm=a.stcm, min_runs=a.min_runs,
                  keep=_ids(a.keep), clear=_ids(a.clear), out=out)
    except (Fail, ValueError, OSError) as exc:
        print("FAILED:", exc, file=sys.stderr)
        return 1
    print("\n".join(res["lines"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
