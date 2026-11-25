#!/usr/bin/env python3
"""
False matches and misses as the gallery grows: can Emma name a scanned guest
at the launch event (25-26 Nov 2026), with a crowd in frame?

    python scripts/eval_face_crowd.py --scratch <dir>          # all of it
    python scripts/eval_face_crowd.py --scratch <dir> --sizes 60,80,110,160

Prints numbers only. Vectors of the head-camera faces go to --scratch, never
into the repo (the camera faces are people who never enrolled; this file keeps
no picture, no vector and no name). Delete the scratch directory afterwards.

Why this exists
---------------
`facewatch` was tuned on a gallery of ~10 people. A wrong name is the one
mistake a greeting cannot take back, and a gallery of 500 gives a stranger 50
times the chances -- so "loosen the threshold for the event" is not an answer
anybody may give without this table. `eval_faces.py` cannot answer it any more:
it needs D:\\Data (the broker photographs and their record numbers), and that
drive is not on this PC.

What exists, and what each set can and cannot say
-------------------------------------------------
* data/faces/eval_cache.npz: 305 studio/phone broker vectors + 43 staff
  portraits, the same AuraFace pack. No names, no record numbers left: people
  are recovered by connected components at cos >= 0.60 (`people_of`; an
  assumption, printed with its result). Used as DISTRACTORS and as "guests who
  never scanned". High quality on both ends: the optimistic side.
* data/faces/enrolled/<name>/*.jpg: the only real multi-pose scans, ~7 people.
  Shrunk into a 1024x768 q70 frame at N px (eval_face_distance.py's protocol):
  the genuine side, a ceiling (same session as the rest of their own poses).
  Their leave-one-person-out runs are the "same rig" impostors: pessimistic,
  the booth's camera is not the head camera.
* .tmp/walk-frames/*: real head-camera frames (19 deg lens). Every face in them
  against distractors that person cannot be = real robot-camera impostors,
  which nothing measured before. Staff mostly; a face already matching the real
  gallery is reported and left out, as is any distractor person who matches the
  real gallery (two staff sat for the broker session: known_overlaps.txt).
* Past the ~190 real distractor people the gallery is filled with synthetic
  mixes (`mix`): two real people's vectors summed and normalised. Their scores
  against real probes are checked against real people's (`qq`) before they are
  believed, and the row for N=500 is the one that leans on them.

What this CANNOT say: how a real crowd scores (motion blur, profile faces, bad
light at the event), and the booth camera's own rig. The event's walk frames
(WALK_FRAMES) and the first bench trial are where those numbers come from.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

from app import faces  # noqa: E402

CACHE = ROOT / "data" / "faces" / "eval_cache.npz"
GALLERY = ROOT / "data" / "faces" / "gallery.npz"
ENROLLED = ROOT / "data" / "faces" / "enrolled"
WALKS = ROOT / ".tmp" / "walk-frames"
FACE_M = 0.16             #: a face's width, metres (for range from px)
PX_PER_DEG = 54.0         #: the head camera at 1024 px (measured 4 Nov)
SIZES = (60, 80, 110, 160)
GALLERY_SIZES = (10, 50, 200, 500)
THRESHOLDS = (0.45, 0.50, 0.55, 0.60, 0.65, 0.70)
MARGIN = 0.10
SEEDS = 5


def people_of(vecs: np.ndarray, floor: float = 0.60) -> np.ndarray:
    """Person label per vector: connected components at cos >= floor."""
    sims = vecs @ vecs.T
    parent = list(range(len(vecs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in np.argwhere(np.triu(sims >= floor, 1)):
        parent[find(int(a))] = find(int(b))
    return np.array([find(i) for i in range(len(vecs))])


def studio() -> tuple[np.ndarray, np.ndarray]:
    data = np.load(CACHE, allow_pickle=True)    # this repo's own eval cache
    vecs = np.asarray(data["vecs"], np.float32)
    vecs = vecs[vecs.any(axis=1)]
    return vecs, people_of(vecs)


def mix(rng, vecs: np.ndarray, labels: np.ndarray, n: int) -> np.ndarray:
    """`n` synthetic people: two different real people's vectors, summed."""
    out = np.zeros((n, vecs.shape[1]), np.float32)
    for i in range(n):
        while True:
            a, b = rng.integers(0, len(vecs), 2)
            if labels[a] != labels[b]:
                break
        v = vecs[a] + vecs[b]
        out[i] = v / np.linalg.norm(v)
    return out


def enrolled_vectors(sizes, cache: Path) -> dict:
    """{px: (vecs, person index, pose index)}, plus the full-size rows (px 0):
    the gallery rows of those people. Cached in scratch."""
    import eval_face_distance as efd

    if cache.is_file():
        data = np.load(cache, allow_pickle=False)
        return {int(k[1:]): (data[k], data["p" + k[1:]], data["i" + k[1:]])
                for k in data.files if k.startswith("v")}
    rows: dict[int, list] = {0: []}
    for s in sizes:
        rows[s] = []
    people = sorted(p for p in ENROLLED.iterdir() if p.is_dir())
    for who, folder in enumerate(people):
        for pose, path in enumerate(sorted(folder.glob("*.jpg"))):
            img = faces.read_image(path)
            face = faces.embed_file(path)
            if img is None or face is None:
                continue
            rows[0].append((face.vec, who, pose))
            found = faces.locate(img)
            if not found:
                # the crop was padded for embed_file; shrink from the padded box
                continue
            bbox = found[0].bbox
            for s in sizes:
                raw, box = efd._paste(img, bbox, float(s), efd.FRAME_W, efd.FRAME_H,
                                      (efd.FRAME_W * 0.5, efd.FRAME_H * 0.4))
                vec, _ = efd._vec(efd._encode(raw, 70), box)
                if vec is not None:
                    rows[s].append((vec, who, pose))
    out = {}
    save = {}
    for s, entry in rows.items():
        v = np.stack([e[0] for e in entry]) if entry else np.zeros((0, 512), np.float32)
        p = np.array([e[1] for e in entry], int)
        i = np.array([e[2] for e in entry], int)
        out[s] = (v, p, i)
        save.update({"v%d" % s: v, "p%d" % s: p, "i%d" % s: i})
    np.savez(cache, **save)
    return out


def walk_faces(cache: Path) -> dict:
    """Every face in every real head-camera frame on this PC. Vectors stay in
    scratch. {"vec", "px", "box", "run", "frame", "size", "tsec"}."""
    if cache.is_file():
        data = np.load(cache, allow_pickle=False)
        return {k: data[k] for k in data.files}
    vecs, px, boxes, runs, frames, sizes = [], [], [], [], [], []
    t0 = time.monotonic()
    timing = {"locate": [], "describe": []}
    total = 0
    run_dirs = sorted(p for p in WALKS.iterdir() if p.is_dir()) if WALKS.is_dir() else []
    for r, run in enumerate(run_dirs):
        for path in sorted(run.glob("*.jpg")):
            if path.name.startswith("sheet"):
                continue
            img = faces.read_image(path)
            if img is None:
                continue
            total += 1
            t = time.perf_counter()
            found = faces.locate(img)
            timing["locate"].append(time.perf_counter() - t)
            for face in found:
                t = time.perf_counter()
                if faces.describe(img, face) is None:
                    continue
                timing["describe"].append(time.perf_counter() - t)
                x1, y1, x2, y2 = face.bbox
                vecs.append(face.vec)
                px.append(x2 - x1)
                boxes.append(face.bbox)
                runs.append(r)
                frames.append(int(path.stem) if path.stem.isdigit() else -1)
                sizes.append(img.shape[:2])
        print("  %s: %d frames so far, %d faces, %.0fs" % ("run%d" % r, total, len(vecs),
                                                           time.monotonic() - t0), flush=True)
    out = {"vec": np.stack(vecs) if vecs else np.zeros((0, 512), np.float32),
           "px": np.array(px, int), "box": np.array(boxes, int).reshape(-1, 4),
           "run": np.array(runs, int), "frame": np.array(frames, int),
           "size": np.array(sizes, int).reshape(-1, 2),
           "frames_read": np.array(total),
           "ms_locate": np.array(np.median(timing["locate"]) * 1000 if timing["locate"] else 0.0),
           "ms_describe": np.array(np.median(timing["describe"]) * 1000 if timing["describe"] else 0.0)}
    np.savez(cache, **out)
    return out


def decide(probes, rows, row_person, threshold, margin=MARGIN):
    """Per probe: (best score, best person, named?) under the naming rule:
    best >= threshold and best - best of a different person >= margin."""
    sims = probes @ rows.T
    best_row = sims.argmax(axis=1)
    best = sims[np.arange(len(probes)), best_row]
    who = row_person[best_row]
    other = sims.copy()
    other[row_person[None, :] == who[:, None]] = -1.0
    runner = other.max(axis=1) if rows.shape[0] else np.full(len(probes), -1.0)
    named = (best >= threshold) & ((best - runner) >= margin)
    return best, who, named


def gallery_of(rng, n, real_rows, real_person, pool, pool_person):
    """`n` people: the real people, then real distractors, then mixes."""
    people = np.unique(pool_person)
    rng.shuffle(people)
    have = len(np.unique(real_person)) if len(real_person) else 0
    want = max(0, n - have)
    real = people[:want]
    keep = np.isin(pool_person, real)
    rows = [real_rows, pool[keep]] if len(real_rows) else [pool[keep]]
    labels = [real_person, pool_person[keep] + 10_000] if len(real_rows) else [pool_person[keep] + 10_000]
    short = want - len(real)
    if short > 0:
        rows.append(mix(rng, pool, pool_person, short))
        labels.append(np.arange(short) + 1_000_000)
    return np.concatenate(rows), np.concatenate(labels)


def qq(pool, labels, probes) -> None:
    """Do synthetic people score like real ones? Percentiles of the same
    probes' scores against each, and their top scores."""
    rng = np.random.default_rng(11)
    synth = mix(rng, pool, labels, 2000)
    real = pool
    a, b = (probes @ real.T).ravel(), (probes @ synth.T).ravel()
    qs = (50, 90, 99, 99.9, 99.99)
    print("  scores of the same %d probes  " % len(probes)
          + "  ".join("p%s" % q for q in qs) + "   max")
    print("    real people   %d rows  " % len(real) + "  ".join("%.3f" % np.percentile(a, q) for q in qs)
          + "   %.3f" % a.max())
    print("    synthetic     %d rows " % len(synth) + "  ".join("%.3f" % np.percentile(b, q) for q in qs)
          + "   %.3f" % b.max())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--sizes", default=",".join(str(s) for s in SIZES))
    args = ap.parse_args()
    args.scratch.mkdir(parents=True, exist_ok=True)
    sizes = tuple(int(s) for s in args.sizes.split(","))

    if not faces.available():
        print("face models not found")
        return 1
    pool, labels = studio()
    real_gallery = faces.Gallery.load(GALLERY)
    # A distractor person who IS somebody in the real gallery is no distractor.
    same = (pool @ real_gallery.vecs.T).max(axis=1) >= 0.55
    overlap = np.isin(labels, labels[same])
    print("studio vectors %d = %d people at cos>=0.60 (singletons %d); left out as staff overlap: %d people"
          % (len(pool), len(np.unique(labels)),
             int((np.bincount(np.unique(labels, return_inverse=True)[1]) == 1).sum()),
             len(np.unique(labels[overlap]))))
    pool, labels = pool[~overlap], labels[~overlap]

    print("embedding the enrolled scans at sizes %s ..." % (sizes,), flush=True)
    mine = enrolled_vectors(sizes, args.scratch / "enrolled.npz")
    rows0, person0, pose0 = mine[0]
    print("real scans: %d rows of %d people" % (len(rows0), len(np.unique(person0))))

    print("reading the head-camera frames ...", flush=True)
    walk = walk_faces(args.scratch / "walk.npz")
    wv = walk["vec"]
    print("head-camera frames: %d  faces: %d  face px p10/p50/p90: %s  | PC ms locate %.0f describe %.0f"
          % (int(walk["frames_read"]), len(wv),
             "/".join(str(int(np.percentile(walk["px"], q))) for q in (10, 50, 90)) if len(wv) else "-",
             float(walk["ms_locate"]), float(walk["ms_describe"])))
    if len(wv):
        known = (wv @ real_gallery.vecs.T).max(axis=1) >= real_gallery_bar()
        print("  of those, already at the naming bar against the real gallery (people we know): %d" % int(known.sum()))
        wv_strangers = wv
    else:
        wv_strangers = wv

    print("\n-- do synthetic people score like real ones? --")
    if len(wv):
        qq(pool, labels, wv)
    held = pool[np.isin(labels, np.unique(labels)[:30])]
    qq(pool[~np.isin(labels, np.unique(labels)[:30])], labels[~np.isin(labels, np.unique(labels)[:30])], held)

    print("\n-- wrong names: faces that never scanned, against N scanned people (single frame, rule: score >= T, margin %.2f) --" % MARGIN)
    probe_sets = {}
    if len(wv):
        probe_sets["head camera (real, %d faces)" % len(wv)] = (wv_strangers, None)
    for name, (probes, _) in probe_sets.items():
        print(" ", name)
        print("    N    " + "  ".join("T=%.2f" % t for t in THRESHOLDS) + "   (share of probes named as somebody; mean of %d draws)" % SEEDS)
        for n in GALLERY_SIZES:
            cells = []
            for t in THRESHOLDS:
                hits = []
                for seed in range(SEEDS):
                    rng = np.random.default_rng(seed)
                    rows, rp = gallery_of(rng, n, np.zeros((0, 512), np.float32), np.zeros(0, int), pool, labels)
                    _, _, named = decide(probes, rows, rp, t)
                    hits.append(float(named.mean()))
                cells.append("%.4f" % float(np.mean(hits)))
            print("    %-4d " % n + "  ".join(cells))

    # Studio people who never scanned: hold half of the people out.
    uniq = np.unique(labels)
    rng = np.random.default_rng(5)
    rng.shuffle(uniq)
    out_people = uniq[: len(uniq) // 3]
    probes = pool[np.isin(labels, out_people)]
    rest = pool[~np.isin(labels, out_people)]
    rest_l = labels[~np.isin(labels, out_people)]
    print("  studio portraits (real, %d photos of %d people, optimistic quality)" % (len(probes), len(out_people)))
    print("    N    " + "  ".join("T=%.2f" % t for t in THRESHOLDS))
    for n in GALLERY_SIZES:
        cells = []
        for t in THRESHOLDS:
            hits = []
            for seed in range(SEEDS):
                g = np.random.default_rng(seed)
                rows, rp = gallery_of(g, n, np.zeros((0, 512), np.float32), np.zeros(0, int), rest, rest_l)
                _, _, named = decide(probes, rows, rp, t)
                hits.append(float(named.mean()))
            cells.append("%.4f" % float(np.mean(hits)))
        print("    %-4d " % n + "  ".join(cells))

    # Same rig: each real scanned person, removed from the gallery, shrunk at
    # each size -- against the other scanned people + distractors.
    print("\n-- wrong names, same rig (leave-one-person-out, the pessimistic side) --")
    print("    px  N    " + "  ".join("T=%.2f" % t for t in THRESHOLDS))
    for s in sizes:
        vs, ps, _ = mine[s]
        for n in GALLERY_SIZES:
            cells = []
            for t in THRESHOLDS:
                wrong = 0
                total = 0
                for seed in range(SEEDS):
                    g = np.random.default_rng(seed)
                    for who in np.unique(ps):
                        keep = person0 != who
                        rows, rp = gallery_of(g, n, rows0[keep], person0[keep], pool, labels)
                        probes = vs[ps == who]
                        _, _, named = decide(probes, rows, rp, t)
                        wrong += int(named.sum())
                        total += len(probes)
                cells.append("%.4f" % (wrong / max(total, 1)))
            print("    %-3d %-4d " % (s, n) + "  ".join(cells))

    print("\n-- misses and wrong names, a scanned person at each size (leave-one-image-out: a ceiling; "
          "same session as their own rows) --")
    print("    px  N    " + "  ".join("T=%.2f" % t for t in THRESHOLDS) + "   (named right / named wrong)")
    for s in sizes:
        vs, ps, pz = mine[s]
        for n in GALLERY_SIZES:
            cells = []
            for t in THRESHOLDS:
                right, wrong, total = 0, 0, 0
                for seed in range(SEEDS):
                    g = np.random.default_rng(seed)
                    for k in range(len(vs)):
                        keep = ~((person0 == ps[k]) & (pose0 == pz[k]))
                        rows, rp = gallery_of(g, n, rows0[keep], person0[keep], pool, labels)
                        _, who, named = decide(vs[k:k + 1], rows, rp, t)
                        if named[0]:
                            right += int(who[0] == ps[k])
                            wrong += int(who[0] != ps[k])
                        total += 1
                cells.append("%.2f/%.3f" % (right / max(total, 1), wrong / max(total, 1)))
            print("    %-3d %-4d " % (s, n) + "  ".join(cells))

    # Per-pair tail, to read 500 another way than through the mixes.
    if len(wv):
        sims = (wv @ pool.T).ravel()
        print("\n-- per-pair rate, head-camera faces against real distractors (%d pairs) --" % len(sims))
        for t in (0.35, 0.40, 0.45, 0.50, 0.55, 0.60):
            p = float((sims >= t).mean())
            print("    T=%.2f  p=%.2e   P(any of 500 >= T) if independent = %.3f" % (t, p, 1 - (1 - p) ** 500))

    # Five poses per person vs one.
    same_rig = []
    one_rig = []
    for who in np.unique(person0):
        probes = mine[sizes[-1]][0][mine[sizes[-1]][1] == who]
        others = rows0[person0 != who]
        ot_p = person0[person0 != who]
        ot_z = pose0[person0 != who]
        sims = probes @ others.T
        same_rig.append(sims.max(axis=1))
        firsts = np.array([np.flatnonzero(ot_p == o)[np.argmin(ot_z[ot_p == o])] for o in np.unique(ot_p)])
        one_rig.append(sims[:, firsts].max(axis=1))
    same_rig = np.concatenate(same_rig)
    one_rig = np.concatenate(one_rig)
    print("\n-- gallery rows per person: best impostor score, all poses vs the first (front) pose only, same rig --")
    print("    all poses  p50 %.3f  p90 %.3f  max %.3f" % (np.median(same_rig), np.percentile(same_rig, 90), same_rig.max()))
    print("    front only p50 %.3f  p90 %.3f  max %.3f" % (np.median(one_rig), np.percentile(one_rig, 90), one_rig.max()))

    # What a large gallery costs at runtime.
    rows = np.random.default_rng(0).normal(size=(2500, 512)).astype(np.float32)
    rows /= np.linalg.norm(rows, axis=1, keepdims=True)
    names = ["guest %d" % (i // 5) for i in range(len(rows))]
    g = faces.Gallery(names, rows, [{"group": "guest"} for _ in names])
    t = time.perf_counter()
    for _ in range(50):
        g.match2(rows[3])
    print("\n-- 500 people x 5 rows on this PC: match2 %.2f ms a call, vectors %.1f MB --"
          % ((time.perf_counter() - t) / 50 * 1000, rows.nbytes / 1e6))
    path = args.scratch / "g500.npz"
    t = time.perf_counter()
    g.save(path)
    saved = time.perf_counter() - t
    t = time.perf_counter()
    faces.Gallery.load(path)
    print("    save %.0f ms, load %.0f ms, file %.1f MB" % (saved * 1000, (time.perf_counter() - t) * 1000,
                                                             path.stat().st_size / 1e6))
    path.unlink()

    # The body: what the 19 deg lens actually has of a person.
    print("\n-- the body in the head camera: the window at each distance (54 px/deg, 1024x768) --")
    print("    range   frame wide x tall   face px")
    for d in (1.35, 2, 3, 4, 5, 6, 8, 10):
        import math

        wide = 2 * d * math.tan(math.radians(1024 / PX_PER_DEG / 2))
        tall = 2 * d * math.tan(math.radians(768 / PX_PER_DEG / 2))
        px = PX_PER_DEG * math.degrees(2 * math.atan(FACE_M / 2 / d))
        print("    %4.2f m  %.2f x %.2f m       %d" % (d, wide, tall, px))
    if len(wv):
        h = walk["size"][:, 0]
        w = walk["size"][:, 1]
        fh = (walk["box"][:, 3] - walk["box"][:, 1]).clip(1)
        fw = (walk["box"][:, 2] - walk["box"][:, 0]).clip(1)
        below = (h - walk["box"][:, 3]) / fh
        side = np.minimum(walk["box"][:, 0], w - walk["box"][:, 2]) / fw
        print("  real faces: frame below the chin in face-heights, p10/p50/p90 %.1f/%.1f/%.1f" %
              tuple(np.percentile(below, q) for q in (10, 50, 90)))
        for need in (1.5, 3.0, 6.0):
            print("    >= %.1f face heights below (and 0.5 wide each side): %.0f%% of %d"
                  % (need, 100 * float(((below >= need) & (side >= 0.5)).mean()), len(wv)))
    return 0


def real_gallery_bar() -> float:
    from app.config import settings

    return settings.face_threshold


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass
    sys.exit(main())
