#!/usr/bin/env python3
"""
Record that a human has read a slide's narration and stands behind it.

    python scripts/approve_narration.py --list
    python scripts/approve_narration.py --show ew-017
    python scripts/approve_narration.py ew-001 ew-002 --by "คุณสมชาย (ฝ่ายขาย)"
    python scripts/approve_narration.py --all --by "ฝ่ายการตลาด Empire Group"
    python scripts/approve_narration.py --revoke ew-017

Why this exists
---------------
The robot's tool results carry a flag saying whether a script is approved
copy, and the assistant is told to deliver approved copy as written. That is
the right design only while the flag is true — and it was hard-coded true for
every script in the index, including the 59 drafted from the slide images by
a model.

So the robot was speaking unreviewed sentences about a property people buy
for millions of baht, and the system was actively telling it those sentences
had been signed off. A draft is fine to speak: it keeps the narration on the
deck's own material rather than improvised. Claiming it was approved is not.

This is the sign-off step, and deliberately a separate one: someone reads the
words, then runs this. It records who, and when.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def load(path: Path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw, (raw["images"] if isinstance(raw, dict) else raw)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ids", nargs="*", help="slide ids to approve")
    parser.add_argument("--index", type=Path, default=Path("data/slides/index.json"))
    parser.add_argument("--by", default="", help="who is approving")
    parser.add_argument("--all", action="store_true", help="approve every draft")
    parser.add_argument("--revoke", nargs="+", metavar="ID", help="withdraw approval")
    parser.add_argument("--list", action="store_true", help="show what needs review")
    parser.add_argument("--show", metavar="ID", help="print one script in full")
    args = parser.parse_args()

    raw, slides = load(args.index)
    by_id = {s["id"]: s for s in slides}
    scripted = [s for s in slides if s.get("script_th") or s.get("script_en")]

    if args.show:
        slide = by_id.get(args.show)
        if not slide:
            print("no slide %r" % args.show)
            return 1
        print("%s — %s" % (slide["id"], slide.get("title_th") or slide.get("title_en")))
        print("approved: %s%s" % (
            bool(slide.get("script_approved")),
            " by %s" % slide["script_approved_by"] if slide.get("script_approved_by") else "",
        ))
        print("\n%s" % (slide.get("script_th") or slide.get("script_en")))
        return 0

    if args.list or not (args.ids or args.all or args.revoke):
        approved = [s for s in scripted if s.get("script_approved")]
        pending = [s for s in scripted if not s.get("script_approved")]
        print("%d slides carry narration" % len(scripted))
        print("  %d approved" % len(approved))
        print("  %d awaiting review" % len(pending))
        if pending:
            print("\nThe assistant will speak these, flagged as drafts:")
            for s in pending[:20]:
                print("  %-10s %s" % (s["id"], (s.get("title_th") or "")[:52]))
            if len(pending) > 20:
                print("  ... and %d more" % (len(pending) - 20))
            print("\nRead them with --show <id>, then approve with --by.")
        return 0

    if args.revoke:
        for sid in args.revoke:
            slide = by_id.get(sid)
            if slide:
                slide.pop("script_approved", None)
                slide.pop("script_approved_by", None)
                slide.pop("script_approved_at", None)
        print("withdrew approval for %d slide(s)" % len(args.revoke))
    else:
        if not args.by:
            print("--by is required: an approval with no name behind it is not one.")
            return 1
        targets = scripted if args.all else [by_id[i] for i in args.ids if i in by_id]
        if not targets:
            print("nothing matched")
            return 1
        stamp = datetime.date.today().isoformat()
        for slide in targets:
            slide["script_approved"] = True
            slide["script_approved_by"] = args.by
            slide["script_approved_at"] = stamp
        print("approved %d slide(s) as %s on %s" % (len(targets), args.by, stamp))

    args.index.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote %s" % args.index)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
