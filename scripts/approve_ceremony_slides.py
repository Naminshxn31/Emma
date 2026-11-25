#!/usr/bin/env python3
"""Record that a person has looked at the ceremony's motion slides and stands behind them.

    python scripts/approve_ceremony_slides.py --status
    python scripts/approve_ceremony_slides.py --by "pete"
    python scripts/approve_ceremony_slides.py --revoke

The slides (`<CEREMONY_FILE stem>.slides.json`, data/ceremony/floor1_ceremony.slides.json)
are our drawing of the owner's approved lines, so the whole file is a draft until a PERSON
signs it after watching the preview (scripts/preview_ceremony_show.py): this records who
and when and pins the digest of every slide entry (`approved_sha256`,
app/ceremony_slides.py `digest`). Any edit to any slide afterwards makes the file a draft
again, and a draft file sends no slide to the event screen -- the plain backdrop and the
Thai subtitle, as before the slides existed. Nothing in the code calls this, and nothing
writes `approved_by` on anyone's behalf.

Refuses while any slide breaks a content rule (a word or a number its cue's line does not
say, a banned word, an unknown template...): that slide would not be shown anyway, and a
signature should cover what the room actually sees. Fix or delete the slide first.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import ceremony_slides  # noqa: E402

AUDIT = ROOT / "data" / "registry" / "approval_audit.jsonl"
SOURCE_ID = "ceremony_slides"


def _now() -> str:
    return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


def status_lines(doc: dict) -> list[str]:
    state = "approved" if ceremony_slides.approved(doc) else (
        "CHANGED since approval" if doc.get("approved_by") else "draft")
    out = ["slides file: %s%s" % (state, (" (by %s, %s)" % (doc.get("approved_by"), doc.get("approved_at")))
                                  if doc.get("approved_by") else "")]
    for row in ceremony_slides.status(doc):
        out.append("%-18s %-8s %s" % (row["id"], row["template"] or "?",
                                      ("BREAKS: " + ", ".join(row["problems"])) if row["problems"] else "ok"))
    return out


def approve(path: Path, by: str, *, audit: Path | None = AUDIT) -> str:
    if not by.strip():
        raise SystemExit("--by needs the approver's name")
    doc = ceremony_slides.load(path)
    if doc is None:
        raise SystemExit("no slides file at %s (or it is not %s)" % (path, ceremony_slides.FORMAT))
    broken = [r for r in ceremony_slides.status(doc) if r["problems"]]
    if broken:
        raise SystemExit("not approved -- fix these first:\n  " + "\n  ".join(
            "%s: %s" % (r["id"], ", ".join(r["problems"])) for r in broken))
    at = _now()
    # the signature is over the slides as they are USED (every slides/<cue id>.json laid
    # over this file, and the pictures they name); it is written into the base file only --
    # the per-cue files stay as their authors wrote them
    sha = ceremony_slides.digest(doc)
    base = ceremony_slides.load_base(path)
    base.update({"approved_by": by.strip(), "approved_at": at, "approved_sha256": sha})
    path.write_text(json.dumps(base, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if audit is not None:
        with audit.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": at, "source_id": SOURCE_ID, "action": "approve", "by": by.strip(),
                                "sha256": sha, "slides": len(doc["slides"])}, ensure_ascii=False) + "\n")
    return sha


def revoke(path: Path) -> bool:
    doc = ceremony_slides.load_base(path)
    if doc is None or not doc.get("approved_by"):
        return False
    doc.update({"approved_by": None, "approved_at": None, "approved_sha256": None})
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--file", type=Path, default=None)
    ap.add_argument("--by", default="")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--revoke", action="store_true", help="the slides file back to a draft")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    path = args.file or ceremony_slides.path()
    if path is None or not path.is_file():
        print("no slides file (CEREMONY_FILE is empty, or %s is missing)" % path)
        return 2
    if args.revoke:
        print("back to a draft" if revoke(path) else "it was not approved")
        return 0
    if args.by:
        sha = approve(path, args.by)
        print("approved by %s: %s" % (args.by.strip(), sha))
    doc = ceremony_slides.load(path)
    if doc is None:
        print("%s is not a slides file (%s)" % (path, ceremony_slides.FORMAT))
        return 2
    for line in status_lines(doc):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
