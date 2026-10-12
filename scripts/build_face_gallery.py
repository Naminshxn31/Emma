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

A source that is not on this machine is not a source that is empty
------------------------------------------------------------------
The staff portraits live on D:, and on the showroom machine there is no
D:/Data/รูปพนักงาน. A rebuild there used to find every CSV row's photo
"not found" and write a gallery without a single staff portrait vector —
the colleagues were listed as problems at the bottom, the gallery was
replaced anyway, and the robot stopped knowing them. With the call screen
now enrolling people and the rebuild being the way to tidy up after it,
that became one keystroke away on the machine that matters.

So when a photograph (or the whole folder, or the CSV, or the broker JSON)
is *missing*, the vectors the previous gallery already holds for it are
carried over, with a loud line saying which and why. A photograph that is
present but has no clear subject is still dropped — that is a judgement
about the photo, not an absence. Revoked and expired consent still leave,
carried or not. Camera shots are never carried: `data/faces/enrolled/` is
this machine's own folder, and a missing one was deleted on purpose.
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

#: How long a rebuild waits for the gallery's lock (`faces.file_lock`). A
#: live append from the call screen holds it for milliseconds; half a
#: minute is another rebuild still running, and then this one refuses.
LOCK_WAIT_S = 30.0
_BUSY = ("แกลเลอรีกำลังถูกเขียนโดยโปรแกรมอื่น (อาจมี build-face-gallery อีกหน้าต่างรันอยู่) — "
         "ยังไม่ได้สร้างใหม่ ปิดอันนั้นหรือรอให้เสร็จแล้วรันอีกครั้ง")


def previous_gallery(path: Path) -> faces.Gallery | None:
    """The gallery this run is about to replace, or None.

    Only a gallery from *this* model can lend vectors: carrying a vector
    from another embedding space into this one is the two-spaces bug the
    model tag exists to stop. So an unreadable or foreign file lends
    nothing, and says so, because then a missing source really does mean
    those people drop out.
    """
    if not path.is_file():
        return None
    try:
        return faces.Gallery.load(path)
    except ValueError as exc:
        print(f"\n!!! the existing gallery cannot lend any vectors: {exc}\n"
              f"    anybody whose photo is missing below will NOT be in the new gallery.\n")
        return None


def _carried(previous: faces.Gallery | None, keep) -> list[dict]:
    """Rows of the previous gallery for which `keep(name, meta)` is true."""
    if previous is None:
        return []
    return [{"name": name, "vec": previous.vecs[i], "meta": dict(meta), "carried": True}
            for i, (name, meta) in enumerate(zip(previous.names, previous.meta))
            if keep(name, meta)]


def _is_staff_portrait(meta: dict) -> bool:
    return meta.get("group") == "staff" and meta.get("source") != "camera"


def _say_carried(carried: list[dict], why: str) -> None:
    if not carried:
        return
    print(f"\n!!! {len(carried)} vector(s) CARRIED OVER from the previous gallery, "
          f"not rebuilt — {why}")
    for e in carried:
        print(f"  carried {e['meta'].get('group', '?'):7s} {e['name']}", flush=True)
    print("    They are the vectors the last build made from those photos. Run this "
          "again on a machine that has them to rebuild them.\n")


def staff_entries(csv_path: Path, photo_dir: Path,
                  previous: faces.Gallery | None = None) -> tuple[list, list]:
    """(entries, problems) from the labelled CSV.

    A row whose photograph is missing keeps the previous gallery's vector
    for that same name and file, if there is one. Matched on both, so a
    row the owner deleted or renamed in the CSV is not resurrected by it.
    """
    if not csv_path.is_file():
        print(f"no {csv_path} yet — run scripts/label_staff_faces.py first")
        # No CSV is a missing source too, not a list of nobody: every staff
        # portrait already in the gallery stays until there is a CSV to
        # rebuild them from.
        carried = _carried(previous, lambda name, meta: _is_staff_portrait(meta))
        _say_carried(carried, f"{csv_path} is not on this machine")
        return carried, []
    entries, problems, carried = [], [], []
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("name") or "").strip()
            fname = (row.get("file") or "").strip()
            if not name or not fname:
                continue
            path = photo_dir / fname
            if not path.is_file():
                kept = _carried(previous, lambda n, meta, name=name, fname=fname:
                                n == name and _is_staff_portrait(meta)
                                and meta.get("photo") == fname)
                if kept:
                    carried += kept
                else:
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
    _say_carried(carried, f"the photo folder {photo_dir} is not on this machine"
                 if not photo_dir.is_dir() else f"their photos are missing from {photo_dir}")
    return entries + carried, problems


def broker_entries(previous: faces.Gallery | None = None) -> tuple[list, list]:
    """One entry per photograph, not per person.

    Two frames of the same face are two rows with the same name, and that is
    on purpose: `Gallery.match` takes the nearest neighbour, so a second
    angle is a second chance to be recognised rather than an average that is
    slightly wrong for both.

    Missing sources carry over like the staff ones: no JSON keeps every
    broker vector the previous gallery had; a missing photo keeps the
    vector for that same record and file.
    """
    if not BROKER_JSON.is_file():
        print(f"no {BROKER_JSON} — skipping brokers")
        carried = _carried(previous, lambda name, meta: meta.get("group") == "broker")
        _say_carried(carried, f"{BROKER_JSON} is not on this machine")
        return carried, []
    records = json.loads(BROKER_JSON.read_text(encoding="utf-8"))["records"]
    entries, problems, carried = [], [], []
    for r in records:
        name = (r.get("name") or "").strip()
        if not name:
            problems.append((f"record #{r['no']}", "", "record has no name"))
            continue
        got = 0
        for stem in r.get("photos", []):
            path = BROKER_PHOTOS / f"{stem}.jpg"
            if not path.is_file():
                kept = _carried(previous, lambda n, meta, name=name, r=r, path=path:
                                n == name and meta.get("group") == "broker"
                                and meta.get("record") == r["no"]
                                and meta.get("photo") == path.name)
                carried += kept
                got += len(kept)
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
    _say_carried(carried, f"their photos are missing from {BROKER_PHOTOS}")
    return entries + carried, problems


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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-brokers", action="store_true",
                    help="also enrol the agent records from data_master.json")
    ap.add_argument("--staff-dir", type=Path, default=STAFF_DIR)
    ap.add_argument("--list", action="store_true", help="print the existing gallery and stop")
    args = ap.parse_args(argv)

    if args.list:
        if not OUT.is_file():
            print(f"no gallery at {OUT}")
            return 1
        try:
            g = faces.Gallery.load(OUT)
        except ValueError as exc:
            print(exc)
            return 1
        print(f"{len(g)} entries, model {g.model_tag}")
        for name, meta in zip(g.names, g.meta):
            print(f"  {meta.get('group', '?'):7s} {name}")
        return 0

    if not faces.available():
        print("face models not found — run scripts/fetch_face_model.py first")
        return 1

    # The whole read-build-write under the gallery's cross-process lock.
    # The call screen now appends people to this file while the server
    # runs, and this script replaces the file from a listing it took
    # minutes earlier: a scan saved in between was on the screen as
    # "หุ่นจำได้แล้ว" and gone from the gallery when this finished (review,
    # 2026-09-29) — and an append straddling this save could put back
    # vectors this run had just dropped for withdrawn consent. Holding the
    # lock makes the append wait a moment and then refuse out loud (the
    # scan says to run this again) instead of either losing the other.
    try:
        with faces.file_lock(OUT, LOCK_WAIT_S, _BUSY):
            return _build(args)
    except faces.LockBusy as exc:
        print(f"\n!!! {exc}")
        return 1


def _build(args) -> int:
    """`main`'s rebuild, called with the gallery lock held."""
    from app import consent

    # Asked once now so an unreadable people.json stops the run before
    # minutes of embedding, not after; asked again at the end (below),
    # because that read is the one that decides.
    try:
        consent.load()
    except consent.Unreadable as exc:
        print(f"\n!!! {exc}\n    the gallery was NOT rebuilt.")
        return 1

    # Read before anything is rebuilt: it is the only place the vectors of
    # a source that is not on this machine still exist.
    previous = previous_gallery(OUT)

    entries, problems = staff_entries(STAFF_CSV, args.staff_dir, previous)
    staff_names = {e["name"] for e in entries}
    camera, camera_problems = enrolled_entries(staff_names)
    entries += camera
    problems += camera_problems
    if args.with_brokers:
        more, more_problems = broker_entries(previous)
        entries += more
        problems += more_problems
    elif previous is not None:
        # Not a missing source — a decision, made by leaving the flag off
        # (see "Why staff-only is the default"). Said out loud all the same,
        # because the gallery is about to lose people.
        brokers = sum(1 for m in previous.meta if m.get("group") == "broker")
        if brokers:
            print(f"\nnote: {brokers} broker vector(s) in the previous gallery are "
                  f"left out — run with --with-brokers to keep them")

    # Withdrawn or expired consent: out of the gallery, not merely unnamed.
    # The watcher already refuses to name them from the next frame; this
    # is where their vectors stop existing.
    try:
        people = consent.load()
    except consent.Unreadable as exc:
        # Refused, not built as if nobody had withdrawn: the unreadable
        # file may be exactly where a revocation is, and this is the step
        # that decides whose vectors keep existing.
        print(f"\n!!! {exc}\n    the gallery was NOT rebuilt.")
        return 1
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
    # Atomic (see `faces.write_atomically`): the server may be reading this
    # file, and a rebuild killed halfway must leave the old gallery, not a
    # torn one.
    gallery.save(OUT)

    groups: dict[str, int] = {}
    for e in entries:
        groups[e["meta"]["group"]] = groups.get(e["meta"]["group"], 0) + 1
    print(f"\nwrote {OUT}: {len(entries)} vectors "
          + ", ".join(f"{n} {g}" for g, n in sorted(groups.items())))
    print(f"distinct names: {len(set(e['name'] for e in entries))}")
    carried = sum(1 for e in entries if e.get("carried"))
    if carried:
        print(f"of those, {carried} CARRIED OVER from the previous gallery (see above)")

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
