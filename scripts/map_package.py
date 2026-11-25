"""Floor 1's map, places and rules as a portable package another robot can read (app/map_package.py).

Owner, 20 Nov 2026: "ระบบแผนที่ของเราอาจจะต้องเอาไปใช้กับหุ่นเสิร์ฟด้วย อาจจะต้องทำระบบเผื่อไว้".

    python scripts/map_package.py export --map <board .stcm | explore .bin | ROS .yaml> --out DIR
           [--truth <grid>] [--findings findings.json] [--route data/robot/waypoints/floor1.json]
           [--event data/events/floor1_event.json] [--spots <robot spots file | marking page JSON>]
           [--transform plan_to_robot.json] [--profile emma] [--name floor1]
    python scripts/map_package.py import DIR            # read it back: the grid, the places, what is wrong
    python scripts/map_package.py check DIR --profile serving_example   # the places against a robot's body

`export` writes (app/map_package.py has the format): map.pgm/.yaml (ROS map_server), the
optional lidar_truth and glass layers, places.json (dock, spots, ways, hazards, no-go, the
board's own markers -- one frame), transform.json, preview.png (for people), package.json.
`--findings`: a map check's findings.json made ON THIS MAP: its phantoms are the glass layer
(the lidar sees through them; they stay solid -- CLAUDE.md, never cleared). Nothing goes to
any robot: a package is files.

`import` and `check` exit 1 when something a robot would trip on is found (a dock inside a
wall the map still has, a spot its body does not fit), 2 when the package cannot be read.
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
from app import map_package as mp  # noqa: E402
from app import robot_profile as rp  # noqa: E402

ROUTE = ROOT / "data" / "robot" / "waypoints" / "floor1.json"
EVENT = ROOT / "data" / "events" / "floor1_event.json"
TRANSFORM = ROOT / ".tmp" / "map-check" / "20261104-floor1-b" / "plan_to_robot.json"
OUT = ROOT / ".tmp" / "map-package"

#: What no package of ours holds, written into every manifest.
NOT_IN_IT = [
    "anything above or below the lidar plane of the robot that made the map (Emma's, 'knee height', never measured): "
    "table tops, counters, a tray's height -- and things lower than the plane (the lobby floor bump is a hazard here, "
    "not a cell)",
    "people and chairs (`changing` in the map check) -- never written into a map",
    "the event's own tables: not in the furniture of the nights we walked",
    "the multimedia room behind its closed door (`--keep` on 4 Nov)",
    "a robot's own docking: where Emma's dock stands is here, how another robot docks is its vendor's",
    "virtual walls / forbidden areas of the board's .stcm with entries (none on floor 1): listed by count, not read",
]


class Fail(Exception):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path, what: str) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise Fail("%s cannot be read (%s): %s" % (what, path, type(e).__name__)) from None
    except ValueError as e:
        raise Fail("%s is not JSON (%s): %s" % (what, path, e)) from None
    if not isinstance(doc, dict):
        raise Fail("%s is not a JSON object (%s)" % (what, path))
    return doc


def load_grid(path: Path) -> tuple[mc.Grid, str, bytes | None]:
    """(grid, kind, the .stcm bytes when it is one)."""
    if path.suffix.lower() in (".yaml", ".yml"):
        return mp.load_map_yaml(path), "ros", None
    blob = path.read_bytes()
    if blob[:4] == b"STCM":
        return mc.parse_stcm(blob), "stcm", blob
    return mc.parse_explore(blob), "explore", None


def _glass(findings_path: Path, grid: mc.Grid) -> tuple[np.ndarray, int]:
    """The phantom cells of a map check made on this grid (glass the map keeps)."""
    doc = _json(findings_path, "the findings file")
    mask = np.zeros(grid.cells.shape, dtype=bool)
    n = 0
    for f in doc.get("findings") or []:
        if f.get("kind") in ("phantom", "line") and f.get("rc"):
            r, c = np.asarray(f["rc"][0]), np.asarray(f["rc"][1])
            ok = (r >= 0) & (r < grid.h) & (c >= 0) & (c < grid.w)
            mask[r[ok], c[ok]] = True
            n += 1
    return mask, n


def _ways(route: dict) -> list[dict]:
    """floor1.json's paths as point lists: every MoveTo point and creep point; Emma's own
    moves (forward / back_to / turns / dock) named, not drawn."""
    out = []
    for name, path in (route.get("paths") or {}).items():
        pts, emma_only = [], set()
        for s in path.get("steps") or []:
            if "to" in s:
                pts.append([float(s["to"][0]), float(s["to"][1])])
            elif "creep" in s:
                pts += [[float(q[0]), float(q[1])] for q in s["creep"]]
                emma_only.add("creep")
            elif "forward" in s or "back_to" in s:
                emma_only.add("forward" if "forward" in s else "back_to")
            elif s.get("do") in ("dock", "undock", "turn"):
                emma_only.add(s["do"])
            for f in s.get("fallback") or []:
                if "to" in f:
                    pts.append([float(f["to"][0]), float(f["to"][1])])
        out.append({"id": name, "points": pts, "emma_steps": sorted(emma_only),
                    "about": (path.get("about") or "")[:200]})
    return out


def places_doc(*, route: dict | None, event: dict | None, spots_file: Path | None, stcm_layers: list[dict],
               grid: mc.Grid) -> tuple[dict, list[str]]:
    """places.json from what we have, and notes on where each part came from."""
    notes: list[str] = []
    places: dict = {"dock": None, "spots": [], "unplaced": [], "ways": [], "named_places": [], "hazards": [],
                    "no_go": [], "board_markers": []}
    # the robot's own reading of its spots file (event_host._relearnt / _pose): what it ignores --
    # a dock pose with no heading, a pose that is not numbers -- the package ignores too (review
    # 20 Nov: [x, y, null] crashed the export on float(None) while the robot walks by floor1.json)
    from app import event_host

    saved = _json(spots_file, "the spots file") if spots_file else {}
    dock = dict((route or {}).get("dock") or {})
    if dock.get("pose"):
        file_pose = [float(v) for v in dock["pose"][:3]]
        learnt = event_host._relearnt(saved.get("dock"))
        if saved.get("dock") is not None and learnt is None:
            notes.append("the spots file's dock is not one the robot uses (no heading, or not numbers): "
                         "the dock is floor1.json's, as on the robot")
        out = {k: dock[k] for k in ("pose", "approach", "back_from_m", "axis_offset_deg", "leave_by_approach", "estimated")
               if k in dock}
        out["style"] = "reverse_onto_contacts (Emma backs onto its contacts; `back_from_m` = reversing from that far out on its line)"
        out["file_pose"] = file_pose
        if learnt:
            out["pose"] = [float(v) for v in learnt["pose"][:3]]
            out["source"] = "re-learnt at the robot %s (วัดแท่นใหม่, %s)" % (learnt.get("recorded_at"), spots_file.name)
        else:
            out["source"] = "floor1.json (%s)" % (ROUTE.name if route else "?")
        places["dock"] = out
    for sid, sp in ((event or {}).get("spots") or {}).items():
        if not isinstance(sp, dict):
            continue
        name = sp.get("name_th") or sid
        if sp.get("dock"):
            continue
        pose = sp.get("pose")
        mine = (saved.get("spots") or {}).get(sid) if isinstance(saved.get("spots"), dict) else None
        src = "the event file"
        if isinstance(mine, dict) and event_host._pose(mine.get("pose")) is not None:
            pose, src = event_host._pose(mine.get("pose")), "recorded at the robot (%s)" % mine.get("recorded_at")
        else:
            pose = event_host._pose(pose)
        if not pose and sp.get("steps"):
            last = [s for s in sp["steps"] if "to" in s][-1]
            pose, src = [last["to"][0], last["to"][1], last.get("face")], "the last point of its steps (the event file)"
        if not pose:
            places["unplaced"].append({"id": sid, "name": name})
            continue
        row = {"id": sid, "name": name, "pose": [float(pose[0]), float(pose[1]), None if len(pose) < 3 or pose[2] is None
                                                 else float(pose[2])], "source": src}
        if sp.get("via"):
            row["via"] = [[float(p[0]), float(p[1])] for p in sp["via"]]
        places["spots"].append(row)
    for m in saved.get("marked") or []:                     # the marking page's own marks
        try:
            row = {"id": str(m["id"]), "name": str(m.get("name") or m["id"]),
                   "pose": [float(m["x"]), float(m["y"]), None if m.get("face_deg") is None else float(m["face_deg"])],
                   "source": "marked on the marking page"}
        except (KeyError, TypeError, ValueError):
            raise Fail("the marking file has a mark that is not {id, x, y}: %r" % (m,)) from None
        places["spots"] = [s for s in places["spots"] if s["id"] != row["id"]] + [row]
        places["unplaced"] = [u for u in places["unplaced"] if u["id"] != row["id"]]
    for name, pl in ((route or {}).get("places") or {}).items():
        if isinstance(pl, dict) and isinstance(pl.get("pose"), list):
            places["named_places"].append({"id": name, "pose": [float(v) if v is not None else None for v in pl["pose"][:3]]})
    places["ways"] = _ways(route or {})
    for h in (event or {}).get("floor_hazards") or []:
        if isinstance(h, dict) and "x" in h:
            places["hazards"].append({"id": h.get("id"), "name": h.get("name_th") or h.get("id"), "x": float(h["x"]),
                                      "y": float(h["y"]), "radius_m": float(h.get("radius_m") or 0.0),
                                      "kind": "under the lidar plane: in no grid, keep the robot's centre this far off"})
    keep = (event or {}).get("no_walk_up_below_y")
    if keep is not None:
        y = float(keep)
        x0, x1 = grid.ox, grid.ox + grid.w * grid.res
        places["no_go"].append({"id": "no_walk_up_below_y", "kind": "half_plane", "rule": "y < %.2f" % y,
                                "polygon": [[x0, y], [x1, y], [x1, grid.oy], [x0, grid.oy]],
                                "applies_to": "walks up to guests (Emma's face_approach): the multimedia room's side",
                                "about": ((event or {}).get("about_no_walk_up") or "")[:300]})
    for layer in stcm_layers:
        if layer["usage"] == "home_dock_pose":
            for e in layer["entries"]:
                places["board_markers"].append({"kind": "home_dock_pose", "id": e["id"], "pose": e["pose"],
                                                "note": "the dock marker registered on the Slamtec board -- board_walk does "
                                                        "NOT use it (floor1.json's dock is the dock); stale since the dock moved"})
        elif layer["count"]:
            places["no_go" if "area" in layer["usage"] else "board_markers"].append(
                {"kind": layer["usage"], "count": layer["count"], "read": layer["read"],
                 "note": "a layer of the board's .stcm with entries this reader does not read (%d bytes)" % layer["bytes"]})
    if spots_file:
        notes.append("spots and the dock re-learnt at the robot from %s" % spots_file)
    return places, notes


def preview_png(grid: mc.Grid, places: dict, path: Path, glass: np.ndarray | None = None, profile=None) -> None:
    """The map with everything on it, for people: grey unknown, white free, dark occupied,
    cyan glass, the dock (with its approach), spots, ways, hazards, no-go."""
    from PIL import Image, ImageDraw, ImageFont

    pts = []
    if places.get("dock"):
        pts.append(places["dock"]["pose"][:2])
    pts += [s["pose"][:2] for s in places.get("spots") or []]
    pts += [p for w in places.get("ways") or [] for p in w["points"]]
    occ_r, occ_c = np.nonzero(grid.occ())
    if len(occ_r):
        wx, wy = grid.world(occ_r, occ_c)
        pts += [[float(np.percentile(wx, 1)), float(np.percentile(wy, 1))], [float(np.percentile(wx, 99)), float(np.percentile(wy, 99))]]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    pad = 1.0
    c0 = max(0, int((min(xs) - pad - grid.ox) / grid.res))
    c1 = min(grid.w, int((max(xs) + pad - grid.ox) / grid.res) + 1)
    r0 = max(0, int((min(ys) - pad - grid.oy) / grid.res))
    r1 = min(grid.h, int((max(ys) + pad - grid.oy) / grid.res) + 1)
    cells = grid.cells[r0:r1, c0:c1]
    rgb = np.full(cells.shape + (3,), 200, dtype=np.uint8)
    rgb[cells > mc.FREE_ABOVE] = (252, 252, 250)
    rgb[cells < mc.OCC_BELOW] = (40, 40, 46)
    if glass is not None and glass.shape == grid.cells.shape:
        rgb[glass[r0:r1, c0:c1]] = (40, 190, 220)
    img = Image.fromarray(rgb[::-1].copy())
    k = max(1.0, min(5.0, 1400 / max(img.width, img.height)))
    img = img.resize((int(img.width * k), int(img.height * k)), Image.NEAREST)
    d = ImageDraw.Draw(img, "RGBA")

    def at(x, y):
        return ((x - grid.ox) / grid.res - c0) * k, (r1 - (y - grid.oy) / grid.res) * k

    def font(size):
        for name in ("tahoma.ttf", "arial.ttf", "DejaVuSans.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    f13 = font(13)
    for z in places.get("no_go") or []:
        if z.get("polygon"):
            d.polygon([at(*p) for p in z["polygon"]], fill=(220, 60, 60, 40), outline=(200, 40, 40, 160))
    for w in places.get("ways") or []:
        if len(w["points"]) > 1:
            d.line([at(*p) for p in w["points"]], fill=(120, 120, 220, 110), width=2)
    m_per_px = grid.res / k
    for h in places.get("hazards") or []:
        cx, cy = at(h["x"], h["y"])
        r = h["radius_m"] / m_per_px
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(230, 130, 0, 255), width=2)
        d.text((cx + r + 2, cy - 7), "hazard %s" % h["id"], fill=(200, 100, 0), font=f13)
    half = (profile.body.reach_width_m / 2 if profile else 0.39) / m_per_px
    for s in places.get("spots") or []:
        cx, cy = at(*s["pose"][:2])
        d.ellipse([cx - half, cy - half, cx + half, cy + half], outline=(40, 150, 70, 200), width=2)
        if s["pose"][2] is not None:
            a = math.radians(s["pose"][2])
            d.line([cx, cy, cx + 1.6 * half * math.cos(a), cy - 1.6 * half * math.sin(a)], fill=(40, 150, 70), width=2)
        d.text((cx + half + 2, cy - 7), s["id"], fill=(20, 110, 50), font=f13, stroke_width=2, stroke_fill=(255, 255, 255))
    dk = places.get("dock")
    if dk:
        cx, cy = at(*dk["pose"][:2])
        a = math.radians(dk["pose"][2])
        d.rectangle([cx - 6, cy - 6, cx + 6, cy + 6], outline=(200, 30, 160), width=3)
        d.line([cx, cy, cx + 30 * math.cos(a), cy - 30 * math.sin(a)], fill=(200, 30, 160), width=3)
        d.text((cx + 9, cy + 4), "dock", fill=(170, 20, 140), font=f13, stroke_width=2, stroke_fill=(255, 255, 255))
        if dk.get("approach"):
            ax, ay = at(*dk["approach"][:2])
            d.line([cx, cy, ax, ay], fill=(200, 30, 160, 120), width=1)
            d.ellipse([ax - 4, ay - 4, ax + 4, ay + 4], outline=(200, 30, 160), width=2)
    for m in places.get("board_markers") or []:
        if m.get("pose"):
            mx, my = at(*m["pose"][:2])
            d.line([mx - 5, my - 5, mx + 5, my + 5], fill=(150, 150, 150), width=2)
            d.line([mx - 5, my + 5, mx + 5, my - 5], fill=(150, 150, 150), width=2)
    banner = Image.new("RGB", (img.width, 40), (255, 255, 255))
    bd = ImageDraw.Draw(banner)
    bd.text((8, 3), "map frame, %.2f m cells, 1 m = %.0f px -- dark occupied, white free, grey unknown, cyan glass (solid, lidar "
            "sees through)" % (grid.res, 1 / m_per_px), fill=(30, 30, 30), font=f13)
    bd.text((8, 21), "magenta = dock (+ approach), green = spots (circle = %s's half-width), orange = floor hazards, red = no-go, "
            "blue = ways, grey x = the board's stale dock marker" % (profile.id if profile else "emma"), fill=(30, 30, 30), font=f13)
    full = Image.new("RGB", (img.width, img.height + 40), (255, 255, 255))
    full.paste(banner, (0, 0))
    full.paste(img, (0, 40))
    full.save(path)


def export(a) -> dict:
    map_path = Path(a.map)
    try:
        grid, kind, stcm_blob = load_grid(map_path)
    except (OSError, ValueError, mp.PackageError) as e:
        raise Fail("the map cannot be read (%s): %s" % (map_path, e)) from None
    truth = None
    sources = {"map": {"path": str(map_path), "kind": kind, "sha256": _sha(map_path)}}
    if a.truth:
        truth, tkind, _ = load_grid(Path(a.truth))
        if (truth.w, truth.h) != (grid.w, grid.h) or abs(truth.ox - grid.ox) > 1e-6 or abs(truth.oy - grid.oy) > 1e-6:
            raise Fail("--truth is not laid on the map's grid")
        sources["lidar_truth"] = {"path": a.truth, "kind": tkind, "sha256": _sha(Path(a.truth))}
    glass = None
    notes = []
    if a.findings:
        glass, n = _glass(Path(a.findings), truth if truth is not None else grid)
        sources["glass"] = {"path": a.findings, "sha256": _sha(Path(a.findings)), "phantoms": n}
        notes.append("glass = the %d phantom/line findings of %s (kept in the map on purpose: glass signs and doors)" % (n, a.findings))
    route = _json(Path(a.route), "the route file") if a.route else None
    event = _json(Path(a.event), "the event file") if a.event else None
    for key, val in (("route", a.route), ("event", a.event), ("spots", a.spots)):
        if val:
            sources[key] = {"path": val, "sha256": _sha(Path(val))}
    layers = mp.stcm_layers(stcm_blob) if stcm_blob else []
    places, pnotes = places_doc(route=route, event=event, spots_file=Path(a.spots) if a.spots else None,
                                stcm_layers=layers, grid=grid)
    notes += pnotes
    if stcm_blob:
        notes.append("the board's .stcm layers: " + ", ".join("%s %d%s" % (l["usage"], l["count"], "" if l["read"] else " (not read)")
                                                            for l in layers))
    transform = None
    tpath = Path(a.transform) if a.transform else TRANSFORM
    if tpath.exists():
        t = _json(tpath, "the plan transform")
        transform = dict(t, inverse="plan = R(-turn_deg) * (map - pivot - shift) + pivot",
                         applies_to="the architect's floor-1 plan (metres) -> this package's map frame",
                         source={"path": str(tpath), "sha256": _sha(tpath)})
    else:
        notes.append("no plan transform (%s not on this machine)" % tpath)
    profile = rp.load(a.profile)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    preview = out / "preview.png"
    preview_png(truth if truth is not None else grid, places, preview, glass, profile)
    manifest = mp.write(out, name=a.name, grid=grid, places=places, profile=profile.to_dict(), sources=sources,
                        truth=truth, glass=glass, transform=transform, not_in_it=NOT_IN_IT,
                        notes=notes + ["made %s by scripts/map_package.py" % _dt.datetime.now().strftime("%Y-%m-%d %H:%M")],
                        extra_files={"preview.png": preview.read_bytes()})
    return manifest


def show(path: Path, profile) -> int:
    try:
        pkg = mp.read(path)
    except mp.PackageError as e:
        print("ERROR: %s" % e)
        return 2
    m, g, pl = pkg["manifest"], pkg["grid"], pkg["places"]
    print("package %s (%s v%s): %d x %d at %.3f m, origin (%.3f, %.3f); layers %s" % (
        m.get("name"), m.get("format"), m.get("version"), g.w, g.h, g.res, g.ox, g.oy, ", ".join(m.get("layers") or {})))
    print("  occupied %d, free %d, unknown %d cells" % (int(g.occ().sum()), int(g.free().sum()),
                                                        int(g.cells.size - g.occ().sum() - g.free().sum())))
    if pl.get("dock"):
        print("  dock (%.3f, %.3f) %.1f deg -- %s" % (tuple(pl["dock"]["pose"][:3]) + (pl["dock"].get("source"),)))
    print("  spots %d placed, %d without a position; ways %d; hazards %d; no-go %d; board markers %d" % (
        len(pl.get("spots") or []), len(pl.get("unplaced") or []), len(pl.get("ways") or []), len(pl.get("hazards") or []),
        len(pl.get("no_go") or []), len(pl.get("board_markers") or [])))
    for line in rp.describe(profile):
        print(line)
    rows = mp.check(pkg, profile)
    bad = [r for r in rows if not r["ok"]]
    for r in rows:
        print("  %s %-6s %-16s %s" % ("  " if r["ok"] else "!!", r["what"], r["id"], r["detail"]))
    print("%d place(s) checked, %d a robot of %s would trip on" % (len(rows), len(bad), profile.id))
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--map", required=True, help="the grid to navigate on: a board .stcm, an explore .bin, or a ROS .yaml")
    e.add_argument("--truth", help="optional: what the lidar agreed is there, on the same grid")
    e.add_argument("--findings", help="optional: a map check's findings.json on this map (phantoms = glass)")
    e.add_argument("--route", default=str(ROUTE) if ROUTE.exists() else None, help="floor1.json (the dock, the ways)")
    e.add_argument("--event", default=str(EVENT) if EVENT.exists() else None, help="the event file (spots, hazards, no-go)")
    e.add_argument("--spots", help="the robot's spots file (re-learnt dock, recorded spots) or the marking page's JSON")
    e.add_argument("--transform", help="plan_to_robot.json (default: the 4 Nov one under .tmp/ when it is there)")
    e.add_argument("--profile", default="emma", help="the robot the map was made with (the manifest says so)")
    e.add_argument("--name", default="floor1")
    e.add_argument("--out", help="default .tmp/map-package/<time>")
    for name in ("import", "check"):
        s = sub.add_parser(name)
        s.add_argument("dir")
        s.add_argument("--profile", default="emma" if name == "import" else "serving_example")
    a = ap.parse_args(argv)
    if a.cmd == "export":
        a.out = a.out or str(OUT / _dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
        try:
            manifest = export(a)
        except (Fail, OSError, ValueError, KeyError, TypeError, mp.PackageError) as exc:
            print("FAILED: %s" % exc, file=sys.stderr)
            return 2
        print("wrote %s: %s" % (a.out, ", ".join(manifest["files"])))
        for n in manifest["notes"]:
            print("  " + n)
        print("nothing was sent to any robot")
        return show(Path(a.out), rp.load(a.profile))
    try:
        profile = rp.load(a.profile)
    except (KeyError, ValueError, OSError) as exc:
        print("ERROR: %s" % exc)
        return 2
    return show(Path(a.dir), profile)


if __name__ == "__main__":
    raise SystemExit(main())
