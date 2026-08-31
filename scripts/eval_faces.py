#!/usr/bin/env python3
"""
Measure face recognition before anything greets anybody by name, and find
the threshold from data instead of from a blog post.

    python scripts/eval_faces.py                 # everything
    python scripts/eval_faces.py --rebuild       # ignore the embedding cache
    python scripts/eval_faces.py --det-size 320  # cheaper detector, same protocol

Why this exists
---------------
`SEARCH_MIN_SIMILARITY` has sat in this project's config since the start,
never measured, and the day somebody did measure the search thresholds the
two distributions turned out to overlap completely. A face threshold is the
same kind of number with a worse failure: search picking the wrong slide is
one wrong picture, and a greeting picking the wrong person is the robot
calling a stranger by a colleague's name out loud, in front of them.

The protocol
------------
The broker set is the only data here that can be measured at all, because it
is the only set with **two photographs of the same person**: 147 of the 157
records in `data_master.json` have a second frame. First photo enrols, the
rest are exam papers. The staff portraits are one-per-person, so they cannot
grade themselves — they serve here as *strangers*, which is the other half
of the question and the half that is usually skipped.

What gets reported is the decision the visitor actually meets, not the
cosine underneath it:

    correct      right person, spoken to by name
    wrong name   greeted as somebody else            <- the expensive one
    silent       no name confident enough to say
    stranger     someone not enrolled, greeted anyway

A threshold is only worth having if it can drive "wrong name" and "stranger"
to zero while leaving "correct" usable. That is the number this prints.

Strangers who are not strangers
-------------------------------
The first run reported two staff portraits matching a broker above 0.74 and
concluded "do not ship a name". Both pairs turned out to be photographs of
the same person: two Embassy staff sat for the broker session as well, so
the negative set had positives in it. The model had been right and the
measurement was wrong — the project's own recurring mistake, a metric
sitting one layer away from the thing it claims to score.

So a match this high is now reported for a human to look at, and only the
ones written into `data/faces/known_overlaps.txt` stop counting against the
threshold. Nothing gets excluded because a number was inconvenient; it gets
excluded because somebody opened both photographs.

The gap this cannot close
-------------------------
Every photograph here is a studio or phone portrait: front on, lit, close.
The camera at the entrance is none of those things. A threshold calibrated
only on this set is `emma.wav` all over again — the wake word measured with
English text-to-speech, green for weeks, deaf to the mouth that owned it.
Re-run this with `--probe-dir` pointed at frames grabbed from the real
camera before trusting a single number below.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import faces  # noqa: E402

BROKER_JSON = Path("D:/Data/database/data_master.json")
BROKER_PHOTOS = Path("D:/Data/database/Photo_Matching/photo_previews_jpg")
STAFF_DIR = Path("D:/Data/รูปพนักงาน")
CACHE = ROOT / "data" / "faces" / "eval_cache.npz"
#: Staff portraits that are the *same person* as an enrolled broker record.
#: One `<staff file>` per line, `#` comments allowed. Only ever added after
#: looking at both photographs side by side — see the module docstring.
OVERLAPS = ROOT / "data" / "faces" / "known_overlaps.txt"


def known_overlaps() -> set[str]:
    if not OVERLAPS.is_file():
        return set()
    out = set()
    for line in OVERLAPS.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            out.add(line.split()[0])
    return out


def load_cache(path: Path) -> dict[str, np.ndarray]:
    """Embeddings keyed by file path.

    Cached for the same reason `eval_search.py` caches its query vectors:
    the run takes minutes, and a measurement nobody re-runs because it is
    slow stops being a measurement.
    """
    if not path.is_file():
        return {}
    data = np.load(str(path), allow_pickle=True)
    if str(data.get("model_tag", "")) != faces.MODEL_TAG:
        print("cache was built with a different model, ignoring it")
        return {}
    return {str(k): v for k, v in zip(data["keys"], data["vecs"])}


def save_cache(path: Path, cache: dict[str, np.ndarray]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = list(cache)
    np.savez(
        path,
        keys=np.array(keys, dtype=object),
        vecs=np.stack([cache[k] for k in keys]) if keys else np.zeros((0, 512), np.float32),
        model_tag=faces.MODEL_TAG,
    )


def embed_all(paths: list[Path], cache: dict, det_size: int) -> tuple[dict, list[Path]]:
    """Vectors for every file, plus the ones no single face was found in."""
    missed: list[Path] = []
    todo = [p for p in paths if str(p) not in cache]
    t0 = time.monotonic()
    for i, p in enumerate(todo, 1):
        face = faces.embed_file(p, det_size=det_size)
        if face is None:
            missed.append(p)
            # A sentinel keeps the failure in the cache, so a re-run does
            # not spend a minute rediscovering the same unreadable file.
            cache[str(p)] = np.zeros(512, np.float32)
        else:
            cache[str(p)] = face.vec
        if i % 25 == 0 or i == len(todo):
            rate = (time.monotonic() - t0) / i
            print(f"  embedded {i}/{len(todo)}  ({rate:.2f}s/photo)", flush=True)
    for p in paths:
        if str(p) not in cache:
            continue
        if not cache[str(p)].any() and p not in missed:
            missed.append(p)
    return cache, missed


def broker_identities() -> list[dict]:
    """One entry per record that has photographs on disk.

    Identity is the record number, never the name: six first names repeat in
    this file (two people called May, two called JJ), and folding them
    together would score two different faces as one person and quietly
    report it as an error rate.
    """
    records = json.loads(BROKER_JSON.read_text(encoding="utf-8"))["records"]
    out = []
    for r in records:
        photos = [BROKER_PHOTOS / f"{p}.jpg" for p in r.get("photos", [])]
        photos = [p for p in photos if p.is_file()]
        if photos:
            out.append({
                "id": r["no"],
                "name": r.get("name") or f"(no name #{r['no']})",
                "company": r.get("company_raw", ""),
                "photos": photos,
            })
    return out


def staff_photos() -> list[Path]:
    if not STAFF_DIR.is_dir():
        return []
    exts = {".jpg", ".jpeg", ".png"}
    return sorted(p for p in STAFF_DIR.iterdir() if p.suffix.lower() in exts)


def sweep(gallery_vecs, gallery_ids, probes, strangers, thresholds, reviewed):
    """The table. One row per threshold, counted the way a visitor meets it."""
    rows = []
    p_vecs = np.stack([v for v, _ in probes]) if probes else np.zeros((0, 512), np.float32)
    p_ids = [i for _, i in probes]
    sims = p_vecs @ gallery_vecs.T if len(p_vecs) else np.zeros((0, len(gallery_ids)))
    best = sims.argmax(axis=1) if len(sims) else np.zeros(0, int)
    best_sim = sims.max(axis=1) if len(sims) else np.zeros(0)
    hit = np.array([gallery_ids[b] == pid for b, pid in zip(best, p_ids)]) if len(best) else np.zeros(0, bool)

    s_vecs = np.stack([v for v, _ in strangers]) if strangers else np.zeros((0, 512), np.float32)
    s_all = s_vecs @ gallery_vecs.T if len(s_vecs) else np.zeros((0, len(gallery_ids)))
    s_sims = s_all.max(axis=1) if len(s_all) else np.zeros(0)
    s_best = s_all.argmax(axis=1) if len(s_all) else np.zeros(0, int)

    # A reviewed overlap is a photograph of somebody who *is* enrolled.
    # Leaving it in the negatives scores a correct answer as a failure.
    counted = np.array([name not in reviewed for _, name in strangers], bool)         if strangers else np.zeros(0, bool)

    for t in thresholds:
        spoke = best_sim >= t
        rows.append({
            "t": t,
            "correct": int((spoke & hit).sum()),
            "wrong": int((spoke & ~hit).sum()),
            "silent": int((~spoke).sum()),
            "stranger": int((s_sims[counted] >= t).sum()),
        })
    return rows, best_sim, hit, s_sims, s_best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="ignore the embedding cache")
    ap.add_argument("--det-size", type=int, default=640)
    ap.add_argument("--probe-dir", type=Path, default=None,
                    help="extra probes from the real camera, files named <id>_*.jpg")
    args = ap.parse_args()

    if not faces.available():
        print("face models not found under data/faces/models/buffalo_l/")
        return 1

    ids = broker_identities()
    staff = staff_photos()
    print(f"broker records with photos: {len(ids)}   staff portraits: {len(staff)}")
    print(f"model: {faces.MODEL_TAG}   det_size: {args.det_size}\n")

    cache = {} if args.rebuild else load_cache(CACHE)
    all_paths = [p for r in ids for p in r["photos"]] + staff
    print(f"embedding {len(all_paths)} photos ({len(cache)} cached)...")
    cache, missed = embed_all(all_paths, cache, args.det_size)
    save_cache(CACHE, cache)

    def vec(p: Path):
        v = cache.get(str(p))
        return None if v is None or not v.any() else v

    # --- enrol on the first usable photo, examine on the rest -------------
    g_vecs, g_ids, g_names, probes = [], [], [], []
    no_enrol = []
    for r in ids:
        usable = [p for p in r["photos"] if vec(p) is not None]
        if not usable:
            no_enrol.append(r)
            continue
        g_vecs.append(vec(usable[0]))
        g_ids.append(r["id"])
        g_names.append(f"{r['name']} ({r['company']})")
        for p in usable[1:]:
            probes.append((vec(p), r["id"]))
    g_vecs = np.stack(g_vecs)

    strangers = [(vec(p), p.name) for p in staff if vec(p) is not None]
    reviewed = known_overlaps()

    print(f"\ndetection: {len(missed)} of {len(all_paths)} photos gave no single face")
    for p in missed[:12]:
        print(f"    {p.parent.name}/{p.name}")
    if len(missed) > 12:
        print(f"    ... and {len(missed) - 12} more")
    print(f"enrolled: {len(g_ids)} people    exam photos: {len(probes)}    "
          f"strangers: {len(strangers)}")
    if no_enrol:
        print(f"no usable photo at all: {[r['name'] for r in no_enrol]}")

    thresholds = [round(x, 2) for x in np.arange(0.20, 0.75, 0.05)]
    rows, best_sim, hit, s_sims, s_best = sweep(
        g_vecs, g_ids, probes, strangers, thresholds, reviewed)

    print("\n--- the decision a visitor meets ---")
    print(f"{'thresh':>7} {'correct':>8} {'wrong name':>11} {'silent':>7} {'stranger greeted':>17}")
    for r in rows:
        print(f"{r['t']:>7.2f} {r['correct']:>8} {r['wrong']:>11} {r['silent']:>7} "
              f"{r['stranger']:>17}")

    n = len(probes)
    print(f"\nrank-1 correct, ignoring any threshold: {int(hit.sum())}/{n}"
          f" ({100 * hit.mean():.1f}%)" if n else "")

    if n:
        gen = best_sim[hit]
        imp = best_sim[~hit]
        print(f"\ncosine of the right person   min {gen.min():.3f}  "
              f"p5 {np.percentile(gen, 5):.3f}  median {np.median(gen):.3f}")
        if len(imp):
            print(f"cosine when it picked wrong  max {imp.max():.3f}  "
                  f"median {np.median(imp):.3f}")
    if len(s_sims):
        keep = np.array([n not in reviewed for _, n in strangers], bool)
        kept = s_sims[keep]
        if len(kept):
            print(f"cosine of a stranger's nearest match  max {kept.max():.3f}  "
                  f"p95 {np.percentile(kept, 95):.3f}  median {np.median(kept):.3f}"
                  f"   ({len(strangers) - int(keep.sum())} reviewed overlaps held out)")

    # Anything this close is either the same person photographed twice or a
    # false accept, and the two look identical from here. Print them; do not
    # decide. The first run of this script decided, and it decided wrong.
    look = [(float(s_sims[i]), name, g_names[int(s_best[i])])
            for i, (_, name) in enumerate(strangers)
            if s_sims[i] >= 0.45 and name not in reviewed]
    if look:
        print(f"\n--- {len(look)} stranger matches need a human look ---")
        for sim, name, who in sorted(look, reverse=True):
            print(f"  {sim:.3f}  {name}  <->  {who}")
        print("  open both. Same person? add the filename to")
        print(f"  {OVERLAPS.relative_to(ROOT)} — otherwise it is a false accept")

    # The number that decides whether this is shippable: is there any
    # threshold that never says a wrong name and never greets a stranger?
    clean = [r for r in rows if r["wrong"] == 0 and r["stranger"] == 0]
    print("\n--- verdict ---")
    if look:
        print(f"{len(look)} stranger matches are still unreviewed — the table above "
              "counts them as failures, which may or may not be what they are")
    if clean and n:
        best = max(clean, key=lambda r: r["correct"])
        print(f"safest threshold {best['t']:.2f}: greets {best['correct']}/{n} correctly "
              f"({100 * best['correct'] / n:.1f}%), never wrong, never a stranger")
    else:
        print("no threshold separates them cleanly on this data — do not ship a name")

    # Two records of the same person under different numbers would show up
    # here as a pair of strangers that match too well to be strangers.
    sims = g_vecs @ g_vecs.T
    np.fill_diagonal(sims, -1)
    hi = np.argwhere(sims > 0.55)
    seen = set()
    pairs = []
    for i, j in hi:
        key = tuple(sorted((int(i), int(j))))
        if key in seen:
            continue
        seen.add(key)
        pairs.append((g_names[i], g_names[j], float(sims[i, j])))
    if pairs:
        print(f"\n--- {len(pairs)} pairs of enrolled records look like the same face ---")
        for a, b, s in sorted(pairs, key=lambda x: -x[2])[:15]:
            print(f"  {s:.3f}  {a}  <->  {b}")
        print("  (worth a human look: a duplicate record enrols one person twice,")
        print("   and every exam paper for them then has two right answers)")
    return 0


if __name__ == "__main__":
    # Thai names in the output, and a Windows console that defaults to
    # cp874. Same trap as `subprocess(encoding="utf-8")` in documents.py:
    # setting the env var here would already be too late for stdout.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass
    sys.exit(main())
