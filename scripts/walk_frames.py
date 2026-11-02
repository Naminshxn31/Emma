"""Pull a walk's pictures off the robot and draw contact sheets (app/walk_frames.py).

Owner, 31 Oct 2026: "ลองให้เดินแล้วดูภาพประกอบด้วยจะได้ประเมินถูก". The robot's
server keeps, while it walks, the head camera's frame + the board's pose,
quality and lidar every second (WALK_FRAMES=true in robot.env). This:

    python scripts/walk_frames.py list                 runs on the robot
    python scripts/walk_frames.py pull [RUN | --last [N]]  -> .tmp/walk-frames/RUN/ (N newest; map_check wants >= 2)
    python scripts/walk_frames.py sheet .tmp/walk-frames/RUN
                                                       -> RUN/sheet_00.jpg ... + path.png + summary.txt

Each tile: the picture, its second and number, the board's quality, and a
top-down lidar view with the robot's measured body (0.70 x 0.60 m) drawn at
the lidar's centre -- WHERE the lidar sits on the body has never been
measured, so read the box as "about here", not to the centimetre. The tiles
where the lidar put something nearest on each side come first, and
summary.txt says which they were: a picture with a chair in it and nothing
near in the lidar is a chair below the scan plane (28 Oct).

The pictures have people in them: .tmp/ is gitignored, and nothing here
uploads anything.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import subprocess
import sys
import tarfile
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent)]

import robot_python as rp  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REMOTE = "/data/local/emma-server/code/data/robot/walk-frames"
OUT = ROOT / ".tmp" / "walk-frames"
#: The robot's body, measured (memory "stcm-plan-maps": 0.70 x 0.60 m; 0.78 m
#: across the arms). Length along x (forward), width along y.
BODY_L, BODY_W, ARMS_W = 0.70, 0.60, 0.78
TILE_W, PIC_H, RADAR = 480, 360, 200
PER_SHEET = 12
COLS = 3


def _robot(serial: str) -> rp.Robot:
    return rp.Robot(serial)


def _root_out(robot: rp.Robot, script: str, timeout: float = 600) -> bytes:
    """`script` as root; its stdout as bytes (exec-out: no tty, binary safe)."""
    cmd = "%s 0 sh -c %s" % (rp.SU, "'" + script.replace("'", "'\\''") + "'")
    r = subprocess.run([robot.adb, "-s", robot.serial, "exec-out", cmd], capture_output=True, timeout=timeout)
    if r.returncode != 0:
        raise rp.Fail("adb exec-out: %s" % r.stderr.decode("utf-8", "replace").strip())
    return r.stdout


def runs(robot: rp.Robot) -> list[str]:
    raw = _root_out(robot, "ls -1 %s 2>/dev/null" % REMOTE).decode("utf-8", "replace")
    return sorted(line.strip() for line in raw.splitlines() if line.strip())


def pull(robot: rp.Robot, name: str) -> Path:
    if "/" in name or name.startswith("."):
        raise rp.Fail("not a run name: %r" % name)
    data = _root_out(robot, "cd %s && tar cf - %s" % (REMOTE, name), timeout=1800)
    if not data:
        raise rp.Fail("no such run on the robot: %s" % name)
    OUT.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(OUT, filter="data")
    return OUT / name


def load(folder: Path) -> list[dict]:
    rows = []
    path = folder / "frames.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue   # a half-written last line (the server stopped mid-write)
    return rows


def nearest(rows: list[dict]) -> dict[str, dict | None]:
    """Per side, the record whose lidar return was nearest."""
    best: dict[str, dict | None] = {"front": None, "left": None, "right": None, "back": None}
    for r in rows:
        clear = r.get("clear") or {}
        for side in best:
            d = clear.get(side)
            if d is not None and (best[side] is None or d < best[side]["clear"][side]):
                best[side] = r
    return best


def walk_line(record: dict) -> str:
    """The walk's own words at that record: the event host's spot/state and
    board_walk's step line, or the app's place and state."""
    w = record.get("walk") or {}
    if w.get("by") == "event":
        text = "%s %s" % (w.get("spot") or "", w.get("state") or "")
        if w.get("reason"):
            text += " (%s)" % w["reason"]
        if w.get("step"):
            text += ": %s" % w["step"]
        return text.strip()
    if w.get("by") == "app":
        return " ".join(str(w[k]) for k in ("place", "state", "phase", "step", "reason") if w.get(k) is not None)
    return ""


def changes(rows: list[dict]) -> dict[int, str]:
    """Records where the walk's state (or step line) changed: where it
    started each leg, where it stopped and why."""
    out: dict[int, str] = {}
    last = None
    for r in rows:
        w = r.get("walk") or {}
        if not w:
            continue
        key = (w.get("state"), w.get("step"), w.get("place"), w.get("spot"))
        if key != last:
            out[r["n"]] = "-> %s" % (w.get("state") or "?")
        last = key
    return out


def summary(rows: list[dict], end: dict | None = None) -> str:
    pics = [r for r in rows if r.get("file")]
    qs = [r["q"] for r in rows if isinstance(r.get("q"), int)]
    lines = ["records %d, pictures %d, seconds %.0f" % (len(rows), len(pics), rows[-1]["s"] if rows else 0)]
    if qs:
        lines.append("quality min %d / median %d / max %d" % (min(qs), sorted(qs)[len(qs) // 2], max(qs)))
    for side, r in nearest(rows).items():
        if r is not None:
            lines.append("nearest %-5s %.2f m at #%d (%.1f s)%s" % (
                side, r["clear"][side], r["n"], r["s"], "" if r.get("file") else " -- no picture"))
    turned = [r for r in rows if r.get("head") and (abs(r["head"][0]) >= 3 or abs(r["head"][1]) >= 3)]
    if turned:
        lines.append("head turned on %d records (pictures there do not look ahead)" % len(turned))
    by_n = {r["n"]: r for r in rows}
    for n in changes(rows):
        lines.append("#%d %.1f s  %s" % (n, by_n[n]["s"], walk_line(by_n[n])))
    if end:
        lines.append("ended: %s" % walk_line({"walk": end}))
    return "\n".join(lines)


def _font(size: int):
    from PIL import ImageFont

    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def radar(record: dict, size: int = RADAR, span_m: float = 2.0):
    """Top-down lidar around the robot: forward = up, robot's left = left."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (size, size), (18, 20, 26))
    d = ImageDraw.Draw(img)
    c, k = size / 2, size / (2 * span_m)

    def at(fwd: float, left: float) -> tuple[float, float]:
        return c - left * k, c - fwd * k

    for ring in (0.5, 1.0, 1.5):
        d.ellipse([c - ring * k, c - ring * k, c + ring * k, c + ring * k], outline=(45, 50, 60))
    hl, hw, ha = BODY_L / 2, BODY_W / 2, ARMS_W / 2
    d.rectangle([*at(hl, ha), *at(-hl, -ha)], outline=(90, 90, 120))          # across the arms
    d.rectangle([*at(hl, hw), *at(-hl, -hw)], outline=(120, 200, 255), width=2)
    d.line([at(0, 0), at(hl + 0.15, 0)], fill=(120, 200, 255), width=2)
    for angle, dist in record.get("scan") or []:
        if dist <= span_m * 1.5:
            x, y = at(dist * math.cos(angle), dist * math.sin(angle))
            near = dist < 0.6
            d.ellipse([x - 1.5, y - 1.5, x + 1.5, y + 1.5], fill=(255, 90, 80) if near else (230, 230, 140))
    return img


STRIP_H = 14
#: The strip's colours: nearer than these (metres) under that column.
STRIP_STEPS = ((0.6, (235, 60, 50)), (1.0, (245, 150, 40)), (2.0, (235, 215, 70)))
STRIP_FAR = (70, 170, 90)
STRIP_NONE = (45, 45, 52)


def lidar_strip(record: dict, width: int, camera: dict | None) -> list[tuple[int, int, int] | None]:
    """Per picture column (at `width`), the colour of the nearest lidar return
    in that direction -- what the lidar saw, laid under what the camera saw.
    A chair in the picture over a green/grey strip is a chair the lidar
    missed (below its plane, 28 Oct). Columns map to bearings with the
    camera's assumed field of view plus where the head was turned, and to the
    lidar's angles through the head's lidar check when it held; without one
    it is the guess (+ = left on both), and the sheet says so."""
    scan = record.get("scan") or []
    if not scan:
        return [None] * width
    cam = camera or {}
    hfov = float(cam.get("hfov_assumed") or 70.0)
    a = cam.get("lidar_a")
    b = cam.get("lidar_b")
    a = float(a) if isinstance(a, (int, float)) and a else 1.0
    b = float(b) if isinstance(b, (int, float)) else 0.0
    head = (record.get("head") or [0.0, 0.0])[0] or 0.0
    focal = (width / 2.0) / math.tan(math.radians(hfov / 2.0))
    pts = [(math.degrees(ang), dist) for ang, dist in scan]
    out = []
    for col in range(width):
        bearing = math.degrees(math.atan2(width / 2.0 - (col + 0.5), focal)) + head
        lidar = a * bearing + b
        near = [d for ang, d in pts if abs((ang - lidar + 180) % 360 - 180) <= 1.2]
        if not near:
            out.append(STRIP_NONE)
            continue
        d = min(near)
        out.append(next((c for limit, c in STRIP_STEPS if d < limit), STRIP_FAR))
    return out


def tile(folder: Path, record: dict, tag: str = "", camera: dict | None = None):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (TILE_W, PIC_H + RADAR + 44), (10, 11, 14))
    if record.get("file"):
        pic = Image.open(folder / record["file"]).convert("RGB")
        pic.thumbnail((TILE_W, PIC_H - STRIP_H))
        left = (TILE_W - pic.width) // 2
        img.paste(pic, (left, 0))
        strip = ImageDraw.Draw(img)
        for col, colour in enumerate(lidar_strip(record, pic.width, camera)):
            if colour is not None:
                strip.line([(left + col, pic.height), (left + col, pic.height + STRIP_H - 2)], fill=colour)
    img.paste(radar(record), (0, PIC_H + 44))
    d = ImageDraw.Draw(img)
    clear = record.get("clear") or {}

    def fmt(side: str) -> str:
        v = clear.get(side)
        return "-" if v is None else "%.2f" % v

    pose = record.get("pose") or {}
    head = "#%d  %.1fs  q%s %s" % (record["n"], record["s"], record.get("q", "-"), tag)
    where = "x %.2f y %.2f" % (pose.get("x") or 0, pose.get("y") or 0) if pose else "no pose"
    head_deg = record.get("head") or [0, 0]
    if abs(head_deg[0]) >= 3 or abs(head_deg[1]) >= 3:
        where += "  head %+.0f/%+.0f" % (head_deg[0], head_deg[1])
    said = walk_line(record)
    if said:
        where = (where + "  " + said)[:62]
    d.text((6, PIC_H + 2), head, fill=(255, 255, 255), font=_font(18))
    d.text((6, PIC_H + 23), where, fill=(170, 170, 180), font=_font(15))
    f = _font(17)
    x0 = RADAR + 14
    for i, (label, side) in enumerate((("front", "front"), ("left", "left"), ("right", "right"), ("back", "back"))):
        v = clear.get(side)
        color = (255, 110, 100) if v is not None and v < 0.6 else (220, 220, 220)
        d.text((x0, PIC_H + 56 + i * 30), "%-5s %s m" % (label, fmt(side)), fill=color, font=f)
    d.text((x0, PIC_H + 56 + 4 * 30 + 6), "lidar to nearest\nreturn, metres", fill=(120, 120, 130), font=_font(13))
    if record.get("file") and record.get("scan"):
        checked = bool(camera and camera.get("lidar_a"))
        d.text((x0, PIC_H + 56 + 4 * 30 + 38), "strip = lidar (" + ("checked" if checked else "angles assumed") + ")",
               fill=(120, 120, 130), font=_font(13))
    return img


def path_png(rows: list[dict], marks: dict[int, str], out: Path) -> None:
    from PIL import Image, ImageDraw

    pts = [(r["n"], r["pose"]["x"], r["pose"]["y"]) for r in rows
           if isinstance(r.get("pose"), dict) and r["pose"].get("x") is not None]
    if len(pts) < 2:
        return
    xs, ys = [p[1] for p in pts], [p[2] for p in pts]
    pad = 0.5
    x0, x1, y0, y1 = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad
    k = 700 / max(x1 - x0, y1 - y0)
    w, h = int((x1 - x0) * k) + 40, int((y1 - y0) * k) + 40
    img = Image.new("RGB", (w, h), (250, 250, 248))
    d = ImageDraw.Draw(img)

    def at(x: float, y: float) -> tuple[float, float]:
        return 20 + (x - x0) * k, h - 20 - (y - y0) * k      # map y up

    d.line([at(x, y) for _, x, y in pts], fill=(40, 90, 200), width=3)
    f = _font(14)
    for n, x, y in pts:
        if n in marks or n % 10 == 0:
            px, py = at(x, y)
            color = (220, 50, 40) if n in marks else (90, 90, 90)
            d.ellipse([px - 4, py - 4, px + 4, py + 4], fill=color)
            d.text((px + 6, py - 8), "#%d %s" % (n, marks.get(n, "")), fill=color, font=f)
    d.text((10, 6), "path (board map coordinates, metres); red = nearest per side", fill=(60, 60, 60), font=f)
    img.save(out)


def sheet(folder: Path) -> list[Path]:
    from PIL import Image

    rows = load(folder)
    if not rows:
        raise rp.Fail("no records in %s" % folder)
    camera = None
    try:
        camera = json.loads((folder / "run.json").read_text(encoding="utf-8")).get("camera")
    except (OSError, ValueError, AttributeError):
        pass
    camera = camera if isinstance(camera, dict) else None
    first = {}
    for side, r in nearest(rows).items():
        if r is not None:
            first.setdefault(r["n"], []).append("nearest " + side)
    for n, tag in changes(rows).items():
        first.setdefault(n, []).append(tag)
    order = [r for r in rows if r["n"] in first] + [r for r in rows if r["n"] not in first and r.get("file")]
    outs = []
    for page in range(0, len(order), PER_SHEET):
        chunk = order[page:page + PER_SHEET]
        rows_n = math.ceil(len(chunk) / COLS)
        th = PIC_H + RADAR + 44
        img = Image.new("RGB", (COLS * TILE_W + (COLS - 1) * 6, rows_n * th + (rows_n - 1) * 6), (0, 0, 0))
        for i, r in enumerate(chunk):
            img.paste(tile(folder, r, ", ".join(first.get(r["n"], [])), camera),
                      ((i % COLS) * (TILE_W + 6), (i // COLS) * (th + 6)))
        out = folder / ("sheet_%02d.jpg" % (page // PER_SHEET))
        img.save(out, quality=85)
        outs.append(out)
    path_png(rows, {n: "/".join(s.replace("nearest ", "") for s in v if s.startswith("nearest"))
                    for n, v in first.items() if any(s.startswith("nearest") for s in v)},
             folder / "path.png")
    end = None
    try:
        end = json.loads((folder / "run.json").read_text(encoding="utf-8")).get("end")
    except (OSError, ValueError, AttributeError):
        pass
    text = summary(rows, end if isinstance(end, dict) else None)
    (folder / "summary.txt").write_text(text + "\n", encoding="utf-8")
    print(text)
    return outs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("what", choices=["list", "pull", "sheet"])
    ap.add_argument("run", nargs="?")
    ap.add_argument("--last", nargs="?", const=1, default=0, type=int,
                    help="pull: the newest run, or the newest N (scripts/map_check.py needs 2 or more)")
    ap.add_argument("--serial", default="192.168.1.197:5555")
    a = ap.parse_args(argv)
    try:
        if a.what == "sheet":
            if not a.run:
                raise rp.Fail("sheet needs a folder (.tmp/walk-frames/RUN)")
            for out in sheet(Path(a.run)):
                print(out)
            return 0
        robot = _robot(a.serial)
        names = runs(robot)
        if a.what == "list":
            print("\n".join(names) or "(no walks recorded -- WALK_FRAMES on? walked since?)")
            return 0
        wanted = names[-a.last:] if a.last > 0 and names else ([a.run] if a.run else [])
        if not wanted:
            raise rp.Fail("pull needs a run name or --last")
        for name in wanted:
            folder = pull(robot, name)
            print(folder)
            for out in sheet(folder):
                print(out)
    except rp.Fail as exc:
        print("FAILED:", exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
