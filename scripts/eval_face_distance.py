#!/usr/bin/env python3
"""
How a known face scores as it gets smaller, and what a closer look buys.

    python scripts/eval_face_distance.py                 # sizes 30..110 px
    python scripts/eval_face_distance.py --sizes 40,50,60

Why this exists
---------------
The greeter's "probably someone we know" hold (FACE_PROBABLE_WAIT_S) and its
closer look (FACE_ZOOM_ENABLED) both need two numbers nobody had: what a
colleague scores when their face is 30-60 px wide, and what a stranger scores
at the same size. `eval_faces.py` answers the full-size question from studio
portraits; this answers the distance one. The robot camera has not been
walked yet, so it is synthetic, and it says so in every line it prints.

The protocol
------------
Each enrolled scan crop (data/faces/enrolled/<name>/*.jpg, ~770 px, face
~400 px) is shrunk with INTER_AREA (what a camera's downscale does) until its
face is N px wide, pasted into a 1024x768 frame (the robot's stream: server
log "robot camera: first frame 1024x768") and put through JPEG q70 (the face
page's `toBlob(..., 0.7)`). Then four looks at it:

  frame     faces.locate on the whole frame (det 640) + describe: the
            greeter today. The detector sees the frame at 640/1024 scale, so a
            50 px face is 31 px to it, and its five points are that coarse.
  crop      the same q70 frame, a region 4x the face around the box, located
            again: the detector sees the face big, its five points should
            land better. Same pixels. Measured 9 Oct: no gain (-0.01..+0.02
            p10), so the server does not do this on its own — the page's
            crop (next line) is the closer look that pays.
  crop95    that region from the raw frame at q95 instead of q70: what the
            face page's crop answer carries (same video pixels, less JPEG).
            (FACE_ZOOM_ENABLED.)
  sensor2x  the face at twice the pixels, q92: what a source with twice the
            stream's linear resolution would buy (an ImageCapture photo, if
            the robot's camera has one bigger than its video). Unmeasured on
            the robot; the page reports its photo capabilities when asked.

Genuine: leave-one-image-out (the probe's own vector removed). Impostor:
leave-one-person-out (all of that person's vectors removed) plus the faces
insightface ships in its package (t1.jpg, Tom_Hanks), which are nobody here.

Read the genuine numbers as a ceiling: the probe's own person is still in the
gallery through the other four poses of the *same* scan session — same day,
light and lens. A colleague walking in on another day scores lower (the
doorway measured 0.33-0.38 on the way in). The impostors share that rig
too, which pushes them up: both sides lean toward each other here.

Results, 9 Oct 2026 (35 crops, 7 people; p10 of the genuine score):
  face px   frame   crop95  sensor2x   undetected
  30        -       -       -          33/35 (the detector misses them)
  40        0.459   0.552   0.599       2/35
  50        0.478   0.510   0.519       0
  60        0.500   0.522   0.535       0
  80/110    0.507-0.521 / 0.527-0.534 / 0.530-0.531
One leave-one-person-out probe reached 0.561 at 80 px on the q70 frame
against another enrolled colleague (both are enrolled live, so the margin
rule stands between them); with the q95 crop no impostor passed 0.483.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import faces  # noqa: E402

ENROLLED = ROOT / "data" / "faces" / "enrolled"
FRAME_W, FRAME_H = 1024, 768
CROP_MARGIN = 0.45


def _encode(img, quality: int):
    import cv2

    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def _background(w: int, h: int) -> np.ndarray:
    import cv2

    rng = np.random.default_rng(3)
    noise = rng.integers(60, 190, (h // 16 + 1, w // 16 + 1, 3), dtype=np.uint8)
    return cv2.resize(noise, (w, h), interpolation=cv2.INTER_CUBIC)


def _paste(src, bbox, width_px: float, frame_w: int, frame_h: int, centre):
    """`src` scaled so its face (bbox) is `width_px` wide, pasted with the
    face centre at `centre`. Returns (frame, face box in frame coords)."""
    import cv2

    s = width_px / float(bbox[2] - bbox[0])
    small = cv2.resize(src, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    frame = _background(frame_w, frame_h)
    fx = (bbox[0] + bbox[2]) / 2 * s
    fy = (bbox[1] + bbox[3]) / 2 * s
    ox, oy = int(centre[0] - fx), int(centre[1] - fy)
    h, w = small.shape[:2]
    x0, y0 = max(0, ox), max(0, oy)
    x1, y1 = min(frame_w, ox + w), min(frame_h, oy + h)
    frame[y0:y1, x0:x1] = small[y0 - oy:y1 - oy, x0 - ox:x1 - ox]
    box = (ox + bbox[0] * s, oy + bbox[1] * s, ox + bbox[2] * s, oy + bbox[3] * s)
    return frame, box


def _nearest(found, box):
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    best, dist = None, None
    for f in found:
        fx, fy = (f.bbox[0] + f.bbox[2]) / 2, (f.bbox[1] + f.bbox[3]) / 2
        d = np.hypot(fx - cx, fy - cy)
        if d < max(8.0, (box[2] - box[0])) and (dist is None or d < dist):
            best, dist = f, d
    return best


def _vec(image, box):
    found = faces.locate(image)
    face = _nearest(found, box)
    if face is None:
        return None, None
    if faces.describe(image, face) is None:
        return None, None
    return face.vec, face


def _region(box, factor: float, w: int, h: int):
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    half = factor * (box[2] - box[0]) / 2
    x0, y0 = int(max(0, cx - half)), int(max(0, cy - half))
    x1, y1 = int(min(w, cx + half)), int(min(h, cy + half))
    return x0, y0, x1, y1


def looks(src, bbox, width_px: float) -> dict:
    """{method: vec or None} for one source face at one size."""
    centre = (FRAME_W * 0.58, FRAME_H * 0.40)
    raw, box = _paste(src, bbox, width_px, FRAME_W, FRAME_H, centre)
    q70 = _encode(raw, 70)
    out = {}
    vec, face = _vec(q70, box)
    out["frame"] = vec
    if face is None:
        # Nothing to aim a closer look at: the server only knows a box the
        # detector found.
        out["crop"] = out["crop95"] = out["sensor2x"] = None
        return out
    x0, y0, x1, y1 = _region(face.bbox, 4.0, FRAME_W, FRAME_H)
    local = tuple(v - o for v, o in zip(face.bbox, (x0, y0, x0, y0)))
    out["crop"] = _vec(q70[y0:y1, x0:x1].copy(), local)[0]
    out["crop95"] = _vec(_encode(raw[y0:y1, x0:x1].copy(), 95), local)[0]
    raw2, box2 = _paste(src, bbox, width_px * 2, FRAME_W * 2, FRAME_H * 2,
                        (centre[0] * 2, centre[1] * 2))
    a0, b0, a1, b1 = _region(box2, 4.0, FRAME_W * 2, FRAME_H * 2)
    local2 = tuple(v - o for v, o in zip(box2, (a0, b0, a0, b0)))
    out["sensor2x"] = _vec(_encode(raw2[b0:b1, a0:a1].copy(), 92), local2)[0]
    return out


def _score(vec, names, vecs, keep):
    """(best score, its name, best of a different name) over the kept rows."""
    sims = vecs[keep] @ vec
    kept = [n for n, k in zip(names, keep) if k]
    i = int(np.argmax(sims))
    others = [float(s) for s, n in zip(sims, kept) if n != kept[i]]
    return float(sims[i]), kept[i], (max(others) if others else -1.0)


def _sources():
    """[(person, path, image, bbox, vec)] for every enrolled crop."""
    out = []
    for person in sorted(p for p in ENROLLED.iterdir() if p.is_dir()):
        for path in sorted(person.glob("*.jpg")):
            img = faces.read_image(path)
            found = faces.detect(img) if img is not None else []
            if img is not None and not found:
                # The same padding `embed_file` falls back to: a face that
                # fills the crop is one SCRFD's anchors miss.
                h, w = img.shape[:2]
                pad = np.zeros((h * 2, w * 2, 3), img.dtype)
                pad[h // 2:h // 2 + h, w // 2:w // 2 + w] = img
                img, found = pad, faces.detect(pad)
            if not found:
                print("  skipped (no face):", path.name)
                continue
            out.append((person.name, path, img, found[0].bbox, found[0].vec))
    return out


def _strangers():
    """[(label, image, bbox)] from insightface's own sample pictures."""
    import insightface

    base = Path(insightface.__file__).parent / "data" / "images"
    out = []
    for name in ("t1.jpg", "Tom_Hanks_54745.png"):
        img = faces.read_image(base / name)
        if img is None:
            continue
        for i, f in enumerate(faces.locate(img)):
            if f.bbox[2] - f.bbox[0] >= 60:
                out.append(("%s#%d" % (name, i), img, f.bbox))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--sizes", default="30,40,50,60,80,110")
    ap.add_argument("--threshold", type=float, default=0.50)
    ap.add_argument("--margin", type=float, default=0.10)
    args = ap.parse_args()
    sizes = [int(s) for s in args.sizes.split(",")]
    if not faces.available():
        sys.exit("face models not installed")

    src = _sources()
    names = [s[0] for s in src]
    vecs = np.stack([s[4] for s in src]).astype(np.float32)
    # The people the real gallery holds that have no scan crop here: they
    # compete for every probe, as they do live.
    try:
        g = faces.Gallery.load(ROOT / "data" / "faces" / "gallery.npz")
        extra = [(n, v) for n, v in zip(g.names, g.vecs) if n not in set(names)]
    except Exception:
        extra = []
    names += [n for n, _ in extra]
    if extra:
        vecs = np.concatenate([vecs, np.stack([v for _, v in extra])])
    people = sorted(set(s[0] for s in src))
    print("SYNTHETIC (scan crops in a 1024x768 q70 frame), %d crops of %d people, "
          "gallery %d vectors" % (len(src), len(people), len(names)))

    strangers = _strangers()
    methods = ("frame", "crop", "crop95", "sensor2x")
    rows = {}
    for w in sizes:
        for m in methods:
            rows[(w, m)] = {"gen": [], "top1": 0, "named": 0, "wrong": 0, "miss": 0,
                            "imp": [], "imp_named": 0, "imp_miss": 0}
        for idx, (person, _path, img, bbox, _v) in enumerate(src):
            got = looks(img, bbox, w)
            keep_gen = [i != idx for i in range(len(names))]
            keep_imp = [n != person for n in names]
            for m in methods:
                r, v = rows[(w, m)], got[m]
                if v is None:
                    r["miss"] += 1
                    r["imp_miss"] += 1
                    continue
                s, who, s2 = _score(v, names, vecs, keep_gen)
                own = max(float(x) for x, n, k in zip(vecs @ v, names, keep_gen)
                          if k and n == person)
                r["gen"].append(own)
                r["top1"] += who == person
                nameable = s >= args.threshold and (s2 < 0 or s - s2 >= args.margin)
                r["named"] += nameable and who == person
                r["wrong"] += nameable and who != person
                si, _wi, s2i = _score(v, names, vecs, keep_imp)
                r["imp"].append(si)
                r["imp_named"] += si >= args.threshold and (s2i < 0 or si - s2i >= args.margin)
        for label, img, bbox in strangers:
            got = looks(img, bbox, w)
            for m in methods:
                r, v = rows[(w, m)], got[m]
                if v is None:
                    r["imp_miss"] += 1
                    continue
                si, _wi, s2i = _score(v, names, vecs, [True] * len(names))
                r["imp"].append(si)
                r["imp_named"] += si >= args.threshold and (s2i < 0 or si - s2i >= args.margin)
        print()
        print("face %3d px   genuine own-score          named  wrong  undetected | impostor best   named" % w)
        for m in methods:
            r = rows[(w, m)]
            g = np.array(r["gen"]) if r["gen"] else np.array([np.nan])
            im = np.array(r["imp"]) if r["imp"] else np.array([np.nan])
            print("  %-9s  p10 %.3f  med %.3f  top1 %2d/%2d  %2d     %2d     %2d        | "
                  "p90 %.3f max %.3f  %d"
                  % (m, np.nanpercentile(g, 10), np.nanmedian(g), r["top1"], len(src),
                     r["named"], r["wrong"], r["miss"],
                     np.nanpercentile(im, 90), np.nanmax(im), r["imp_named"]))

    print()
    print("band floor candidates, method 'frame' (what the hold sees before any closer look):")
    print("  floor   " + "  ".join("%3dpx gen>=/imp>=" % w for w in sizes))
    for floor in (0.20, 0.25, 0.28, 0.30, 0.33, 0.35, 0.40):
        cells = []
        for w in sizes:
            r = rows[(w, "frame")]
            gen = np.array(r["gen"])
            imp = np.array(r["imp"])
            cells.append("%5.0f%% / %3.0f%%  " % (100 * (gen >= floor).mean() if len(gen) else 0,
                                                 100 * (imp >= floor).mean() if len(imp) else 0))
        print("  %.2f    %s" % (floor, " ".join(cells)))


if __name__ == "__main__":
    main()
