#!/usr/bin/env python3
"""Record that a person has watched the robot face's stories and stands behind them.

    python scripts/approve_stories.py --status
    python scripts/approve_stories.py --by "pete"
    python scripts/approve_stories.py --by "pete" --state completed --note "ดูครบทุกเรื่องแล้ว"
    python scripts/approve_stories.py --revoke

The catalog (data/projects/<project>/stories/stories.json) is built as a
draft by scripts/build_story_assets.py, and a draft is never offered to a
guest (app/stories.py, deny by default). This is the sign-off, deliberately
a separate step a PERSON runs after watching every story
(scripts/record_stories.py): it records who and when, and pins the digest of
exactly what was approved -- the lines, the numbers, the pictures' hashes.
Edit anything after that and the catalog is a draft again until someone runs
this again. Nothing in the code ever calls it, and nothing writes
`approved_by` on anyone's behalf.

Refuses while any story has a problem (it would not be offered anyway, and an
approval should cover what was actually reviewed).
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

from app import stories  # noqa: E402
from app.knowledge_policy import CONTENT_STATES  # noqa: E402

AUDIT = ROOT / "data" / "registry" / "approval_audit.jsonl"
FIELDS = ("approval_status", "approved_by", "approved_at", "effective_at", "disclosure_scope",
          "content_state", "approved_sha256")


def _now() -> str:
    return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


def status(catalog: dict) -> str:
    problem = stories.approval_problem(catalog)
    return "approved" if problem is None else "not approved (%s)" % problem


def approve(path: Path, by: str, state: str, note: str = "", *, audit: Path | None = AUDIT) -> dict:
    catalog = json.loads(path.read_text(encoding="utf-8"))
    if not by.strip():
        raise SystemExit("--by needs the approver's name")
    if state not in CONTENT_STATES:
        raise SystemExit("--state must be one of %s" % sorted(CONTENT_STATES))
    assets = catalog.get("assets") or {}
    broken = {}
    for story in catalog.get("stories") or []:
        issues = stories.problems(story, assets)
        for ref in stories.asset_refs((story or {}).get("params") or {}) if isinstance(story, dict) else []:
            why = stories.asset_problem(ref, assets.get(ref), path.parent / "assets")
            if why:
                issues.append("asset %s: %s" % (ref, why))
        if issues:
            broken[str((story or {}).get("id"))] = issues
    if broken:
        lines = ["%s: %s" % (sid, "; ".join(v[:3])) for sid, v in broken.items()]
        raise SystemExit("not approved -- fix these first (build_story_assets.py shows them all):\n  "
                         + "\n  ".join(lines))
    before = {k: catalog.get(k) for k in FIELDS}
    at = _now()
    catalog.update({
        "approval_status": "approved",
        "approved_by": by.strip(),
        "approved_at": at,
        "effective_at": at,
        "disclosure_scope": "customer",
        "content_state": state,
    })
    if note:
        catalog["approval_note"] = note
    catalog["approved_sha256"] = stories.content_digest(catalog)
    path.write_text(json.dumps(catalog, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if audit is not None:
        changes = [{"field": k, "from": before[k], "to": catalog.get(k)} for k in FIELDS
                   if before[k] != catalog.get(k)]
        with audit.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": at, "source_id": stories.SOURCE_ID, "action": "approve",
                                "by": by.strip(), "policy": "customer_claim_v1",
                                "changes": changes}, ensure_ascii=False) + "\n")
    return catalog


def revoke(path: Path) -> dict:
    catalog = json.loads(path.read_text(encoding="utf-8"))
    catalog.update({"approval_status": "draft", "approved_by": "", "approved_at": "",
                    "effective_at": "", "approved_sha256": ""})
    catalog.pop("approval_note", None)
    path.write_text(json.dumps(catalog, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return catalog


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--catalog", type=Path, default=stories.catalog_path())
    ap.add_argument("--by", default="")
    ap.add_argument("--state", default="preliminary_concept",
                    help="content_state (default: the facilities' own, preliminary_concept)")
    ap.add_argument("--note", default="")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--revoke", action="store_true")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    if not args.catalog.is_file():
        print("no catalog at %s -- run scripts/build_story_assets.py first" % args.catalog)
        return 1
    if args.revoke:
        revoke(args.catalog)
        print("revoked: the catalog is a draft")
        return 0
    if args.status or not args.by:
        catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
        print("%d stories: %s" % (len(catalog.get("stories") or []), status(catalog)))
        return 0
    catalog = approve(args.catalog, args.by, args.state, args.note)
    print("approved by %s at %s (%d stories)" % (catalog["approved_by"], catalog["approved_at"],
                                                 len(catalog.get("stories") or [])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
