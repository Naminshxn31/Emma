#!/usr/bin/env python3
"""Record that a person has read Emma's offline lines and stands behind them.

    python scripts/approve_offline_lines.py --status
    python scripts/approve_offline_lines.py --by "pete"                 # every clean line
    python scripts/approve_offline_lines.py --by "pete" --ids menu,ack
    python scripts/approve_offline_lines.py --revoke ack

The lines (data/projects/<project>/offline/lines.json) start as drafts, and a
draft is never spoken or shown (app/offline/lines.py, deny by default). This
is the sign-off, a separate step a PERSON runs after reading both languages:
it records who and when and pins the digest of the exact words
(`approved_sha256`), so editing a line afterwards makes it a draft again
until somebody runs this again. Nothing in the code calls it, and nothing
writes `approved_by` on anyone's behalf (the facts' rule, 6 Oct).

Refuses a line that breaks a content rule (a digit, "ไม่มีข้อมูล", "อยู่เอง
หรือลงทุน", ...): it would not be said anyway, and an approval should cover
what is actually said. After approving, render the audio:
`python scripts/render_offline_voice.py --dry-run` first.
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

from app.offline import lines  # noqa: E402

AUDIT = ROOT / "data" / "registry" / "approval_audit.jsonl"


def _now() -> str:
    return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


def status_lines(data: dict) -> list[str]:
    out = []
    for line in data.get("lines") or []:
        state = "approved" if lines.approved(line) else (
            "CHANGED since approval" if line.get("approved_by") else "draft")
        bad = lines.problems(line)
        out.append("%-20s %-24s%s" % (line.get("id"), state, ("  BREAKS: " + ",".join(bad)) if bad else ""))
    return out


def approve(path: Path, by: str, ids: set[str] | None = None, *, audit: Path | None = AUDIT) -> list[str]:
    if not by.strip():
        raise SystemExit("--by needs the approver's name")
    data = json.loads(path.read_text(encoding="utf-8"))
    chosen = [ln for ln in data.get("lines") or [] if ids is None or ln.get("id") in ids]
    if ids is not None and {ln.get("id") for ln in chosen} != ids:
        raise SystemExit("no such line id: %s" % ", ".join(sorted(ids - {ln.get("id") for ln in chosen})))
    broken = {ln.get("id"): lines.problems(ln) for ln in chosen if lines.problems(ln)}
    if broken:
        raise SystemExit("not approved -- fix these first:\n  " + "\n  ".join(
            "%s: %s" % (k, ", ".join(v)) for k, v in broken.items()))
    at = _now()
    done = []
    for line in chosen:
        if lines.approved(line):
            continue
        line.update({"approved_by": by.strip(), "approved_at": at, "approved_sha256": lines.digest(line)})
        done.append(line["id"])
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if audit is not None and done:
        with audit.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": at, "source_id": lines.SOURCE_ID, "action": "approve",
                                "by": by.strip(), "lines": done}, ensure_ascii=False) + "\n")
    return done


def revoke(path: Path, ids: set[str]) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    done = []
    for line in data.get("lines") or []:
        if line.get("id") in ids and line.get("approved_by"):
            line.update({"approved_by": None, "approved_at": None, "approved_sha256": None})
            done.append(line["id"])
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return done


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--file", type=Path, default=None)
    ap.add_argument("--by", default="")
    ap.add_argument("--ids", default="", help="comma-separated line ids (default: all)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--revoke", default="", help="comma-separated line ids back to draft")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    path = args.file or lines.path()
    if not path.is_file():
        print("no offline lines at %s" % path)
        return 1
    if args.revoke:
        done = revoke(path, {i.strip() for i in args.revoke.split(",") if i.strip()})
        print("back to draft: %s" % (", ".join(done) or "(nothing was approved)"))
        return 0
    if args.status or not args.by:
        data = json.loads(path.read_text(encoding="utf-8"))
        print("\n".join(status_lines(data)))
        return 0
    ids = {i.strip() for i in args.ids.split(",") if i.strip()} or None
    done = approve(path, args.by, ids)
    print("approved by %s: %s" % (args.by.strip(), ", ".join(done) or "(already approved)"))
    print("next: python scripts/render_offline_voice.py --dry-run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
