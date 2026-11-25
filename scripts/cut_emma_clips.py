#!/usr/bin/env python3
"""The Emma clips the film shows, cut to the part of the frame she is ever in.

    python scripts/cut_emma_clips.py                      # check every cut (exit 1 on a problem)
    python scripts/cut_emma_clips.py --make c_emma_dive_crop          # cut again + pin, "not checked" (not shown)
    python scripts/cut_emma_clips.py --mark c_emma_dive_crop --checked "what you looked at"   # after looking

23 Nov 2026 night, measured on the event notebook (Intel UHD, 6 logical CPUs, Chrome 157): Chrome
decodes VP9 with alpha on the CPU, and the whole-frame Emma clips cost it every pixel of a frame she
is only ever in part of -- c_emma_stage (s06) 35% of its frames dropped, c_emma_dive (s04_l11) 43%
where it began. The film names CUT copies instead: the same lossless render frames, the same encoder
settings, only the window round her (outside it every frame of the original is alpha 0). The act
lays the file at that window of the box it laid the whole frame in (the slide's `window` option,
the same numbers as the index row's `window`), so every pixel of her is where it was. The `...30`
copies are the even frames at 30 fps: fallbacks no slide names, for a screen that cannot draw 60.

A re-render of an original (scripts/render_emma_dive.py / render_emma_console.py --register)
leaves its cuts behind: the check says so (`cut_of_sha256` is the original the cut was made from)
and tests/test_ceremony_acts.py fails until the cut is made again with --make. A cut is usable on
the screen only with `checked` -- what a person looked at: --make pins it "not checked" (its slides
are not served), you look (laid over its picture at the act's framing, side by side with the
original at the same instants), then --mark writes what you saw.

Encoding is not byte-for-byte repeatable (libvpx row-mt): --make gives a new file and a new pin.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
ASSETS = ROOT / "data" / "ceremony" / "assets"
INDEX = ASSETS / "index.json"
LOCK = ROOT / ".tmp" / "ceremony-show" / "index.lock"          # the render tools' lock on index.json
RENDERS = ROOT / ".tmp" / "ceremony-show"

#: cut id -> the original, the lossless frames it was encoded from (60 fps PNG), the window
#: [x, y, w, h, W, H] (W x H = the original's frame), the act's seek points (forced key frames, as
#: the render scripts force them), libvpx's cpu-used as the render script had it, the rate.
#: `render` = the render script's own encode of those frames: it must be the original's pinned bytes, or
#: the frames are of a trial render nobody registered (review 23 Nov: the PNG count alone let one through)
_DIVE = {"from": "c_emma_dive", "frames": "emma-dive/frames", "render": "emma-dive/emma_dive.webm",
         "window": [464, 0, 1288, 824, 1920, 1080], "keys": [0, 0.6, 1.6, 4.4], "cpu": 2}
_SWIM = {"from": "c_emma_swim", "frames": "emma-dive/frames_swim", "render": "emma-dive/emma_swim.webm",
         "window": [600, 376, 320, 432, 1920, 1080], "keys": [0, 2.0, 2.6, 5.0, 6.5], "cpu": 2}
_STAGE = {"from": "c_emma_stage", "frames": "emma-console/frames", "render": "emma-console/emma_stage.webm",
          "window": [48, 120, 624, 792, 720, 960], "keys": [0, 3, 7], "cpu": 1}
CUTS = {
    "c_emma_dive_crop": {**_DIVE, "fps": 60}, "c_emma_dive_crop30": {**_DIVE, "fps": 30},
    "c_emma_swim_crop": {**_SWIM, "fps": 60}, "c_emma_swim_crop30": {**_SWIM, "fps": 30},
    "c_emma_stage_crop": {**_STAGE, "fps": 60}, "c_emma_stage_crop30": {**_STAGE, "fps": 30},
}
#: the least room left round her inside a window (px of the original frame)
MARGIN = 8
#: a cut's middle frame against the original's same instant inside the window: mean difference on her
#: (0..255). Two encodes of the same frames differ by ~1-3; a shifted window or another clip by far more.
SAME_FRAME = 8.0


def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _rows() -> list[dict]:
    return json.loads(INDEX.read_text(encoding="utf-8"))


def alpha_union(ffmpeg: str, path: Path, w: int, h: int) -> list[int] | None:
    """[x0, y0, x1, y1] round every pixel with alpha > 0 in any frame (libvpx decodes the alpha)."""
    import numpy as np

    proc = subprocess.Popen([ffmpeg, "-v", "error", "-c:v", "libvpx-vp9", "-i", str(path), "-vf", "alphaextract",
                             "-pix_fmt", "gray", "-f", "rawvideo", "-"], stdout=subprocess.PIPE)
    union = None
    while True:
        buf = proc.stdout.read(w * h)
        if len(buf) < w * h:
            break
        ys, xs = np.nonzero(np.frombuffer(buf, np.uint8).reshape(h, w))
        if len(xs):
            box = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
            union = box if union is None else [min(union[0], box[0]), min(union[1], box[1]),
                                               max(union[2], box[2]), max(union[3], box[3])]
    proc.wait()
    return union


def _still(ffmpeg: str, path: Path, t: float, w: int, h: int, crop: list[int] | None = None):
    """The frame at `t` s (a key frame: a seek, not a decode from the start), RGBA, optionally cropped."""
    import numpy as np

    vf = ["crop=%d:%d:%d:%d" % (crop[2], crop[3], crop[0], crop[1])] if crop else []
    out = subprocess.run([ffmpeg, "-v", "error", "-ss", "%.3f" % t, "-c:v", "libvpx-vp9", "-i", str(path)]
                         + (["-vf", ",".join(vf)] if vf else []) +
                         ["-frames:v", "1", "-pix_fmt", "rgba", "-f", "rawvideo", "-"], stdout=subprocess.PIPE).stdout
    return np.frombuffer(out, np.uint8).reshape(h, w, 4) if len(out) == w * h * 4 else None


def _frames_in(ffmpeg: str, path: Path) -> int | None:
    probe = Path(ffmpeg).with_name(Path(ffmpeg).name.replace("ffmpeg", "ffprobe"))
    try:
        out = subprocess.run([str(probe), "-v", "error", "-select_streams", "v", "-count_packets", "-show_entries",
                              "stream=nb_read_packets", "-of", "csv=p=0", str(path)], stdout=subprocess.PIPE, check=True).stdout
        return int(out.decode().strip())
    except (OSError, ValueError, subprocess.CalledProcessError):
        return None


def same_content(ffmpeg: str, row: dict, orig: dict) -> str | None:
    """Why the cut file is not the original's frames inside its window (None = it is): its frame count, and
    three key-frame instants against the original's same instants, cropped to the window, on her."""
    import numpy as np

    n = _frames_in(ffmpeg, ASSETS / row["file"])
    if n != row["frames"]:
        return "%s holds %s frames, its row says %s" % (row["file"], n, row["frames"])
    diffs = []
    for f in (0.25, 0.5, 0.75):
        t = round(row["duration"] * f * 2) / 2                   # (every 0.5 s is a key frame in both)
        a = _still(ffmpeg, ASSETS / row["file"], t, row["w"], row["h"])
        b = _still(ffmpeg, ASSETS / orig["file"], t, row["w"], row["h"], crop=row["window"])
        if a is None or b is None:
            return "no frame at %.1f s" % t
        her = (a[..., 3] > 8) | (b[..., 3] > 8)
        if her.any():
            card = np.array([128, 128, 128], float)
            ca = a[..., :3] * (a[..., 3:] / 255.0) + card * (1 - a[..., 3:] / 255.0)
            cb = b[..., :3] * (b[..., 3:] / 255.0) + card * (1 - b[..., 3:] / 255.0)
            diffs.append((t, float(np.abs(ca - cb)[her].mean())))
    if not diffs:
        return "she is in none of the frames looked at"
    worst = max(diffs, key=lambda d: d[1])
    return None if worst[1] <= SAME_FRAME else "at %.1f s it differs from %s's window by %.1f/255 on her" % (
        worst[0], orig["id"], worst[1])


def _covers(window: list[int], union: list[int] | None) -> bool:
    """Her box inside the window with MARGIN to spare on each side (or all the room the frame has there)."""
    x, y, w, h, W, H = window
    return union is not None and 0 <= x and 0 <= y and x + w <= W and y + h <= H and (
        union[0] - x >= min(MARGIN, union[0]) and union[1] - y >= min(MARGIN, union[1]) and
        x + w - union[2] >= min(MARGIN, W - union[2]) and y + h - union[3] >= min(MARGIN, H - union[3]))


def check(ffmpeg: str) -> list[str]:
    """What is wrong with the cuts as the index has them now."""
    rows = {r["id"]: r for r in _rows()}
    bad: list[str] = []
    unions: dict[str, list[int] | None] = {}
    for cut_id, c in CUTS.items():
        row, orig = rows.get(cut_id), rows.get(c["from"])
        if orig is None:
            bad.append("%s: its original %s is not in the index" % (cut_id, c["from"]))
            continue
        if row is None:
            bad.append("%s: not in the index (--make it)" % cut_id)
            continue
        x, y, w, h, W, H = c["window"]
        if (W, H) != (orig["w"], orig["h"]):
            bad.append("%s: the window is of a %dx%d frame, %s is %dx%d" % (cut_id, W, H, c["from"], orig["w"], orig["h"]))
        if row.get("window") != c["window"] or (row.get("w"), row.get("h")) != (w, h):
            bad.append("%s: the index row's window/size %s %sx%s is not this script's %s" % (
                cut_id, row.get("window"), row.get("w"), row.get("h"), c["window"]))
        if row.get("cut_of") != c["from"] or row.get("cut_of_sha256") != orig["sha256"]:
            bad.append("%s: cut from %s, not from the %s pinned now (%s): an original re-rendered -- cut again (--make)"
                       % (cut_id, (row.get("cut_of_sha256") or "?")[:12], c["from"], orig["sha256"][:12]))
        if _sha(ASSETS / row["file"]) != row["sha256"]:
            bad.append("%s: %s is missing or not the bytes the index pins" % (cut_id, row["file"]))
        elif row.get("window") == c["window"]:
            # (the content itself -- review 23 Nov: a row pointing at another encode with its pins in order,
            # e.g. the 60 fps row at the 30 fps file, passed every other check)
            why = same_content(ffmpeg, row, orig)
            if why:
                bad.append("%s: not %s inside its window: %s" % (cut_id, c["from"], why))
        if row.get("fps") != float(c["fps"]):
            bad.append("%s: %s fps in the index, %s here" % (cut_id, row.get("fps"), c["fps"]))
        if c["from"] not in unions:
            unions[c["from"]] = alpha_union(ffmpeg, ASSETS / orig["file"], orig["w"], orig["h"])
        if not _covers(c["window"], unions[c["from"]]):
            bad.append("%s: %s has her outside the window (or within %d px of its edge): her box %s, window %s" % (
                cut_id, c["from"], MARGIN, unions[c["from"]], c["window"]))
    return bad


def make(ffmpeg: str, cut_id: str, checked: str) -> None:
    c = CUTS[cut_id]
    rows = _rows()
    orig = next(r for r in rows if r["id"] == c["from"])
    frames = RENDERS / c["frames"]
    pngs = sorted(frames.glob("f*.png"))
    total = int(round(orig["duration"] * 60))
    if len(pngs) != total:
        raise SystemExit("%s: %d frames in %s, %s is %d frames at 60 fps -- not the render it was encoded from"
                         % (cut_id, len(pngs), frames, c["from"], total))
    if _sha(RENDERS / c["render"]) != orig["sha256"]:
        raise SystemExit("%s: %s is not the pinned %s -- the frames in %s are of another render (register it with its "
                         "render script first, then cut)" % (cut_id, c["render"], c["from"], frames))
    union = alpha_union(ffmpeg, ASSETS / orig["file"], orig["w"], orig["h"])
    if not _covers(c["window"], union):
        raise SystemExit("%s: her box in %s is %s, the window %s does not hold it with %d px to spare: widen CUTS"
                         % (cut_id, c["from"], union, c["window"], MARGIN))
    x, y, w, h, _W, _H = c["window"]
    fps = c["fps"]
    vf = "crop=%d:%d:%d:%d" % (w, h, x, y)
    if fps != 60:
        vf += ",select='not(mod(n\\,%d))',setpts=N/%d/TB" % (60 // fps, fps)
    tmp = ASSETS / (cut_id + ".tmp.webm")
    try:
        _encode_and_pin(ffmpeg, cut_id, c, orig, frames, vf, union, tmp, checked)
    finally:
        tmp.unlink(missing_ok=True)       # (a failed encode or Ctrl-C leaves nothing behind)


def _encode_and_pin(ffmpeg, cut_id, c, orig, frames, vf, union, tmp, checked) -> None:
    x, y, w, h, _W, _H = c["window"]
    fps = c["fps"]
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-xerror", "-framerate", "60", "-i", str(frames / "f%04d.png"),
                    "-vf", vf, "-r", str(fps), "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "30",
                    "-row-mt", "1", "-deadline", "good", "-cpu-used", str(c["cpu"]), "-g", str(fps // 2),
                    "-keyint_min", str(fps // 2), "-auto-alt-ref", "0",
                    "-force_key_frames", ",".join("%g" % k for k in c["keys"]), "-an", str(tmp)], check=True)
    # (a frame ffmpeg could not read is skipped, not an error: one bad PNG gave a clip a frame short that
    # was pinned as whole -- the count is checked before anything is pinned)
    want = int(round(orig["duration"] * fps))
    got = _frames_in(ffmpeg, tmp)
    if got != want:
        raise SystemExit("%s: the encode holds %s frames, %d wanted -- not pinned (a frame of %s unreadable?)"
                         % (cut_id, got, want, frames))
    data = tmp.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    # (a name of its own: the clip the index pins now keeps its name and bytes until the new index is in
    # place -- an index that cannot be written leaves the old pair as it was; review 23 Nov)
    dest = ASSETS / ("%s.%s.webm" % (cut_id, sha[:12]))
    entry = {
        "id": cut_id, "kind": "video", "file": dest.name, "sha256": sha, "w": w, "h": h,
        "from_pin": None, "candidate": True, "checked": checked, "ok": True, "depth": None,
        "about": orig["about"], "class": orig["class"],
        "shows": orig["shows"] + " -- cut to the part of the frame she is ever in (%s)" % c["from"],
        "text": "", "source": orig["source"] + " -- the window %s of %s" % (c["window"], c["from"]),
        "alpha": True, "bytes": len(data), "duration": orig["duration"], "fps": float(fps),
        "frames": int(round(orig["duration"] * fps)), "picked_by": orig.get("picked_by", []), "people": [],
        "window": c["window"], "cut_of": c["from"], "cut_of_sha256": orig["sha256"],
        "hand": "scripts/cut_emma_clips.py --make %s: the %dx%d window at (%d, %d) of %s's %dx%d frame (her box in "
                "every frame of it: %s), from its lossless render frames (.tmp/ceremony-show/%s); libvpx-vp9 yuva420p "
                "CRF 30, row-mt, deadline good, cpu-used %d, GOP %d, auto-alt-ref 0, key frames forced at %s s, %d fps, "
                "no sound" % (cut_id, w, h, x, y, c["from"], orig["w"], orig["h"], union, c["frames"], c["cpu"],
                              fps // 2, " / ".join("%g" % k for k in c["keys"]), fps),
    }
    before: dict = {}

    def put(rows: list[dict]) -> None:
        at = next((i for i, r in enumerate(rows) if r["id"] == cut_id), None)
        if at is None:
            after = max(i for i, r in enumerate(rows) if r["id"] == c["from"] or r.get("cut_of") == c["from"])
            rows.insert(after + 1, entry)
        else:
            before["file"] = rows[at].get("file")
            rows[at] = entry
        before["named"] = {r.get("file") for r in rows}
    tmp.replace(dest)
    try:
        _edit_index(put)
    except BaseException:
        dest.unlink(missing_ok=True)             # (no row names it: the old clip and pin stay as they were)
        raise
    old = before.get("file")
    if old and old != dest.name and old not in before["named"]:
        (ASSETS / old).unlink(missing_ok=True)   # (the clip it replaced, named by no row now)
    print("registered", cut_id, sha, len(data), "bytes", "%dx%d" % (w, h), entry["frames"], "frames", "->", dest.name)


def mark(cut_ids: list[str], checked: str) -> None:
    """Write what a person looked at into these cut rows (the clip is shown only with it)."""
    def put(rows: list[dict]) -> None:
        for cut_id in cut_ids:
            row = next((r for r in rows if r["id"] == cut_id), None)
            if row is None:
                raise SystemExit("%s is not in the index (--make it first)" % cut_id)
            row["checked"] = checked
    _edit_index(put)
    print("checked:", ", ".join(cut_ids))


def _edit_index(edit) -> None:
    """index.json changed by `edit(rows)` inside the render tools' lock, read inside it (never from an
    earlier read), written back in its own formatting (indent 1, ensure_ascii off, its line ends) --
    through a file beside it and a rename (a crash mid-write never leaves the film half an index)."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    waited = 0
    while True:
        try:
            LOCK.mkdir()
            break
        except FileExistsError:
            if waited > 120:
                raise SystemExit("the index lock is held (%s): nothing written" % LOCK)
            time.sleep(2)
            waited += 2
    try:
        raw = INDEX.read_bytes()
        eol = "\r\n" if b"\r\n" in raw else "\n"
        rows = json.loads(raw.decode("utf-8"))
        if (json.dumps(rows, indent=1, ensure_ascii=False) + "\n").replace("\n", eol).encode("utf-8") != raw:
            raise SystemExit("index.json is not in the formatting this writes (indent 1, ensure_ascii off): not touched")
        edit(rows)
        staged = INDEX.with_name(INDEX.name + ".tmp")
        try:
            staged.write_bytes((json.dumps(rows, indent=1, ensure_ascii=False) + "\n").replace("\n", eol).encode("utf-8"))
            os.replace(staged, INDEX)
        finally:
            staged.unlink(missing_ok=True)
    finally:
        LOCK.rmdir()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--make", help="comma-separated cut ids to cut again from the lossless frames and pin, 'not "
                    "checked' (%s)" % ", ".join(CUTS))
    ap.add_argument("--mark", help="comma-separated cut ids: write --checked into their rows (after you looked)")
    ap.add_argument("--checked", help="with --mark: what a person looked at (the clip is usable only with it)")
    ap.add_argument("--ffmpeg")
    args = ap.parse_args()
    from voice_line import find_ffmpeg

    ffmpeg = find_ffmpeg(args.ffmpeg)
    for listed in (args.make, args.mark):
        for cut_id in (listed or "").split(",") if listed else []:
            if cut_id not in CUTS:
                raise SystemExit("unknown cut %r (%s)" % (cut_id, ", ".join(CUTS)))
    if args.mark and not (args.checked and args.checked.strip() and args.checked.strip().lower() != "not checked"):
        raise SystemExit("--mark needs --checked: what you looked at -- a clip nobody looked at is not shown")
    for cut_id in (args.make or "").split(",") if args.make else []:
        make(ffmpeg, cut_id, "not checked")
    if args.mark:
        mark(args.mark.split(","), args.checked.strip())
    if args.make and not args.mark:
        print("pinned 'not checked' (their slides are not served): look at them, then --mark %s --checked \"...\"" % args.make)
    bad = check(ffmpeg)
    for line in bad:
        print("PROBLEM", line)
    if not bad:
        print("ok: %d cuts, each from the original pinned now, her inside every window" % len(CUTS))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
