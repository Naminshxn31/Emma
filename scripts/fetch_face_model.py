#!/usr/bin/env python3
"""
Download the face model pack (one time, ~190MB).

    python scripts/fetch_face_model.py                    # auraface (default)
    python scripts/fetch_face_model.py --pack buffalo_l   # research comparison

Same rule as the wake word and the slide images: weights that one command
restores do not belong in git history. This puts the pack where
`app/faces.py` looks for it (`data/faces/models/<pack>/`).

Why the default changed from buffalo_l (2026-08-31): a licence, not a
benchmark. The buffalo_l weights are "available for non-commercial research
purposes only" (insightface python-package README) and a condo sales gallery
is neither. AuraFace (fal/AuraFace-v1, Apache-2.0) is the same ArcFace
family behind the same SCRFD detector, trained on commercially usable data.
Its LICENSE.md is downloaded alongside the weights on purpose — the reason
this pack exists must travel with it.

Only detection and recognition are fetched. The pack also publishes
age/gender and dense-landmark models; `app/faces.py` refuses to load those
(see `allowed_modules` there), so pulling them would be bandwidth spent on
models a machine must not be running on visitors.

Switching packs = a new vector space. The gallery must be rebuilt
(`python scripts/build_face_gallery.py`) and every threshold re-measured
(`python scripts/eval_faces.py`) — the numbers in config.py say which model
they were measured on.
"""
from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

DEST = Path("data/faces") / "models"

#: What each pack needs on disk, and where the auraface files come from.
#: buffalo_l has no URLs here because insightface downloads it itself.
AURAFACE_BASE = "https://huggingface.co/fal/AuraFace-v1/resolve/main/"
AURAFACE_FILES = ("scrfd_10g_bnkps.onnx", "glintr100.onnx", "LICENSE.md")

MARKERS = {"auraface": "glintr100.onnx", "buffalo_l": "w600k_r50.onnx"}


def fetch_auraface(target: Path) -> int:
    target.mkdir(parents=True, exist_ok=True)
    for name in AURAFACE_FILES:
        dst = target / name
        if dst.is_file() and dst.stat().st_size > 0:
            print(f"already there: {dst.name}")
            continue
        print(f"downloading {name} ...")
        # Download beside and rename: a half-written recognition model is
        # worse than a missing one, because `available()` only checks that
        # the file exists.
        part = dst.with_suffix(dst.suffix + ".part")
        urllib.request.urlretrieve(AURAFACE_BASE + name, part)
        part.replace(dst)
        print(f"  {dst.stat().st_size:,} bytes")
    return 0


def fetch_buffalo(target: Path) -> int:
    try:
        from insightface.app import FaceAnalysis
    except ImportError:
        print("insightface is not installed:")
        print("    pip install insightface onnxruntime")
        return 1
    print("downloading buffalo_l (~280MB) ...")
    print("NOTE: these weights are licensed for non-commercial research "
          "use only — comparison runs, not the showroom.")
    # `prepare` is what triggers the download, so it has to be called even
    # though nothing is being recognised here.
    app = FaceAnalysis(name="buffalo_l", root=str(DEST.parent),
                       providers=["CPUExecutionProvider"],
                       allowed_modules=["detection", "recognition"])
    app.prepare(ctx_id=-1, det_size=(640, 640))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", choices=sorted(MARKERS), default="auraface")
    args = ap.parse_args()

    target = DEST / args.pack
    marker = target / MARKERS[args.pack]
    if marker.is_file():
        print(f"already there: {target}")
        return 0

    rc = fetch_auraface(target) if args.pack == "auraface" else fetch_buffalo(target)
    if rc:
        return rc
    if not marker.is_file():
        print(f"download finished but {marker.name} is missing — pack layout changed?")
        return 1
    print(f"done: {target}")
    print("next: python scripts/build_face_gallery.py   (vectors are per-model)")
    print("then: python scripts/eval_faces.py           (thresholds are per-model)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
