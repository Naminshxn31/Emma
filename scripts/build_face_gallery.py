#!/usr/bin/env python3
"""
Build the gallery the robot matches against: names and vectors, no pictures.

    python scripts/build_face_gallery.py                 # staff only
    python scripts/build_face_gallery.py --with-brokers  # and the agent set
    python scripts/build_face_gallery.py --list          # who is in it now

Reads three sources and writes one `.npz` of 512-float vectors to
`data/faces/gallery.npz`:

    data/faces/staff.csv       names typed against the studio portraits
    data/faces/enrolled/<name> face crops captured from the real camera
    data_master.json           the agent records, only with --with-brokers

The middle one carries the most weight per shot, because it is the only
source photographed through the instrument that will do the recognising.

Two groups, one file, and a `group` field on every entry, because they are
not the same thing to whoever is standing at the door: a colleague arriving
for a shift and an agent bringing a client want different sentences, and the
difference has to survive as far as whatever composes the greeting.

Why staff-only is the default
-----------------------------
The 305 broker photographs were sent to a LINE chat to build a contact list.
Enrolling them into a machine that recognises people at an entrance is a
different purpose from the one they were handed over for, and it is not this
script's call to make quietly by defaulting to it. `--with-brokers` is one
flag and it is deliberate.

Enrolment is per-person, and it fails loudly
--------------------------------------------
A photograph with no clear subject enrols nobody (see `app/faces.py`) and is
listed at the end of the run. A name in the CSV whose photograph could not
be read is a person the robot will not greet, and the only place that can be
noticed is here.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import faces  # noqa: E402

STAFF_DIR = Path("D:/Data/รูปพนักงาน")
STAFF_CSV = ROOT / "data" / "faces" / "staff.csv"
BROKER_JSON = Path("D:/Data/database/data_master.json")
BROKER_PHOTOS = Path("D:/Data/database/Photo_Matching/photo_previews_jpg")
ENROLLED_DIR = ROOT / "data" / "faces" / "enrolled"
OUT = ROOT / "data" / "faces" / "gallery.npz"


def staff_entries(csv_path: Path, photo_dir: Path) -> tuple[list, list]:
    """(entries, problems) from the labelled CSV."""
    if not csv_path.is_file():
        print(f"no {csv_path} yet — run scripts/label_staff_faces.py first")
        return [], []
    entries, problems = [], []
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("name") or "").strip()
            fname = (row.get("file") or "").strip()
            if not name or not fname:
                continue
            path = photo_dir / fname
            if not path.is_file():
                problems.append((fname, name, "file not found"))
                continue
            face = faces.embed_file(path)
            if face is None:
                problems.append((fname, name, "no clear subject in the photo"))
                continue
            entries.append({
                "name": name,
                "vec": face.vec,
                "meta": {"group": "staff", "role": (row.get("role") or "").strip(),
                         "photo": fname},
            })
            print(f"  staff   {name}", flush=True)
    return entries, problems


def broker_entries() -> tuple[list, list]:
    """One entry per photograph, not per person.

    Two frames of the same face are two rows with the same name, and that is
    on purpose: `Gallery.match` takes the nearest neighbour, so a second
    angle is a second chance to be recognised rather than an average that is
    slightly wrong for both.
    """
    if not BROKER_JSON.is_file():
        print(f"no {BROKER_JSON} — skipping brokers")
        return [], []
    records = json.loads(BROKER_JSON.read_text(encoding="utf-8"))["records"]
    entries, problems = [], []
    for r in records:
        name = (r.get("name") or "").strip()
        if not name:
            problems.append((f"record #{r['no']}", "", "record has no name"))
            continue
        got = 0
        for stem in r.get("photos", []):
            path = BROKER_PHOTOS / f"{stem}.jpg"
            if not path.is_file():
                continue
            face = faces.embed_file(path)
            if face is None:
                continue
            entries.append({
                "name": name,
                "vec": face.vec,
                "meta": {"group": "broker", "company": r.get("company_raw", ""),
                         "record": r["no"], "photo": path.name},
            })
            got += 1
        if not got:
            problems.append((f"record #{r['no']}", name, "no usable photo"))
        else:
            print(f"  broker  {name} ({got})", flush=True)
    return entries, problems


def enrolled_entries(known_staff: set[str]) -> tuple[list, list]:
    """Face crops captured from the camera by `scripts/enroll_face.py`.

    These are the important ones. Every other picture feeding this gallery
    is a studio or phone portrait, and the camera at the door is a different
    instrument — a shot taken *through that camera* is enrolment at the
    place the question gets asked.

    Merged rather than replacing anything, and merged here rather than being
    written straight into the gallery, because otherwise the next run of
    this script would silently delete them. That failure has a name in this
    project: a new mechanism walking into an old one, in the file that warns
    about it.
    """
    if not ENROLLED_DIR.is_dir():
        return [], []
    entries, problems = [], []
    for folder in sorted(p for p in ENROLLED_DIR.iterdir() if p.is_dir()):
        name = folder.name
        shots = sorted(folder.glob("*.jpg"))
        got = 0
        for shot in shots:
            face = faces.embed_file(shot)
            if face is None:
                problems.append((shot.name, name, "no clear subject in the crop"))
                continue
            entries.append({
                "name": name,
                "vec": face.vec,
                # A camera shot of somebody who is also in staff.csv is the
                # same colleague, so the group has to agree — otherwise the
                # robot greets them as staff or as a guest depending on
                # which vector happened to be nearest.
                "meta": {"group": "staff" if name in known_staff else "enrolled",
                         "photo": f"enrolled/{name}/{shot.name}", "source": "camera"},
            })
            got += 1
        if got:
            print(f"  camera  {name} ({got})", flush=True)
        else:
            problems.append((folder.name, name, "no usable shots in the folder"))
    return entries, problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-brokers", action="store_true",
                    help="also enrol the agent records from data_master.json")
    ap.add_argument("--staff-dir", type=Path, default=STAFF_DIR)
    ap.add_argument("--list", action="store_true", help="print the existing gallery and stop")
    args = ap.parse_args()

    if args.list:
        if not OUT.is_file():
            print(f"no gallery at {OUT}")
            return 1
        g = faces.Gallery.load(OUT)
        print(f"{len(g)} entries, model {g.model_tag}")
        for name, meta in zip(g.names, g.meta):
            print(f"  {meta.get('group', '?'):7s} {name}")
        return 0

    if not faces.available():
        print("face models not found — run scripts/fetch_face_model.py first")
        return 1

    entries, problems = staff_entries(STAFF_CSV, args.staff_dir)
    staff_names = {e["name"] for e in entries}
    camera, camera_problems = enrolled_entries(staff_names)
    entries += camera
    problems += camera_problems
    if args.with_brokers:
        more, more_problems = broker_entries()
        entries += more
        problems += more_problems

    # Withdrawn or expired consent: out of the gallery, not merely unnamed.
    # The watcher already refuses to name them from the next frame; this
    # is where their vectors stop existing.
    from app import consent

    people = consent.load()
    withdrawn = sorted({e["name"] for e in entries
                        if consent.status(e["name"], people=people)
                        in (consent.REVOKED, consent.EXPIRED)})
    if withdrawn:
        entries = [e for e in entries if e["name"] not in withdrawn]
        print(f"\nleft out (consent revoked/expired): {', '.join(withdrawn)}")
    for e in entries:
        e["meta"]["consent"] = consent.status(e["name"], people=people)

    if not entries:
        print("nothing to enrol")
        return 1

    gallery = faces.Gallery(
        [e["name"] for e in entries],
        np.stack([e["vec"] for e in entries]),
        [e["meta"] for e in entries],
    )
    gallery.save(OUT)

    groups: dict[str, int] = {}
    for e in entries:
        groups[e["meta"]["group"]] = groups.get(e["meta"]["group"], 0) + 1
    print(f"\nwrote {OUT}: {len(entries)} vectors "
          + ", ".join(f"{n} {g}" for g, n in sorted(groups.items())))
    print(f"distinct names: {len(set(e['name'] for e in entries))}")

    if problems:
        print(f"\n--- {len(problems)} could not be enrolled ---")
        for fname, name, why in problems:
            print(f"  {fname:24s} {name:20s} {why}")
        print("  these people will not be greeted by name. Fix the photo or the CSV.")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass
    sys.exit(main())
