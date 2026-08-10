#!/usr/bin/env python3
"""
Fold the per-page documents from the `emma` project into the slide index.

    python scripts/import_page_docs.py ../emma/knowledge/pages
    python scripts/import_page_docs.py ../emma/knowledge/pages --dry-run

What these documents are
------------------------
`emma/knowledge/pages/{th,en}/<slide-id>.txt` — one file per slide, in both
languages, each holding far more than the one-line summary currently in the
index:

    SOURCE: doc data/picture/25.jpg
    TYPE: facility
    TITLE: ภาพอาคารและสระลากูน Embassy World

    ข้อความที่ถอด:      <- every word printed on the slide
    ...
    คำแปล:              <- translation of that (Thai files only)
    ...
    คำอธิบายภาพ:        <- what the picture actually shows
    ...

Why the two halves are kept apart
---------------------------------
They have different standing, and conflating them is how a robot ends up
stating something nobody approved:

- **Transcribed text** is what the developer's own deck says. It is as
  authoritative as the slide the guest is looking at.
- **Visual description** was written by a model looking at the image. It is
  excellent for *finding* the right slide and fine for describing a room out
  loud, but it is not approved copy and must never be the source of a number.

So they land in separate fields, and `search_condo_info` labels which is
which. The prompt already forbids quoting any figure that isn't in
CONDO_FACTS; this keeps that line visible in the data too.

Covers the 85 slides inherited from `emma`. The 59 pages of the Canva deck
(`ew-*`) have no documents — those still need narration from the sales team.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: Section headings, per language. The Thai files carry a translation block
#: that the English ones don't need.
_SECTIONS = {
    "th": {
        "transcript": "ข้อความที่ถอด:",
        "translation": "คำแปล:",
        "detail": "คำอธิบายภาพ:",
    },
    "en": {
        "transcript": "Transcribed text:",
        "detail": "Visual description:",
    },
}

_HEADER = re.compile(r"^(SOURCE|TYPE|TITLE|PROJECT):\s*(.*)$", re.M)


def parse(text: str, language: str) -> dict:
    """Split one page document into its sections."""
    sections = _SECTIONS[language]
    out: dict[str, str] = {}

    for key, heading in sections.items():
        start = text.find(heading)
        if start < 0:
            continue
        body_start = start + len(heading)
        # Runs until the next known heading, whichever comes first.
        ends = [
            text.find(other, body_start)
            for other in sections.values()
            if text.find(other, body_start) > -1
        ]
        body = text[body_start:min(ends)] if ends else text[body_start:]
        out[key] = body.strip()

    headers = {m.group(1).lower(): m.group(2).strip() for m in _HEADER.finditer(text)}
    out["title"] = headers.get("title", "")
    return out


def _clean(text: str) -> str:
    """Collapse the layout of a slide's text into one searchable line.

    Transcribed slide text arrives as one word per line, which is how it sits
    on the slide but not how anyone would say it.
    """
    lines = [ln.strip() for ln in text.splitlines()]
    return " ".join(ln for ln in lines if ln)


#: Boilerplate stamped on every page of the deck. It matched every query
#: equally and told the reader nothing — the same problem that made
#: "โครงการ" useless as a search term.
_BOILERPLATE = (
    "preliminary concept",
    "for illustrative purposes only",
    "subject to further",
    "development and change without prior notice",
    "แนวคิดเบื้องต้น",
    "ใช้เพื่อประกอบการอธิบายเท่านั้น",
    "อาจมีการพัฒนาและเปลี่ยนแปลง",
)


def _drop_boilerplate(text: str) -> str:
    kept = []
    for line in text.splitlines():
        low = line.strip().lower()
        if low and not any(b in low for b in _BOILERPLATE):
            kept.append(line.strip())
    return " ".join(kept)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pages", type=Path, help="emma/knowledge/pages")
    parser.add_argument("--index", type=Path, default=Path("data/slides/index.json"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not (args.pages / "th").is_dir():
        print("No th/ folder under %s" % args.pages)
        return 1

    raw = json.loads(args.index.read_text(encoding="utf-8"))
    slides = raw["images"] if isinstance(raw, dict) else raw
    by_id = {s["id"]: s for s in slides}

    updated, missing, unmatched = 0, [], []

    for language in ("th", "en"):
        folder = args.pages / language
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.txt")):
            slide = by_id.get(path.stem)
            if slide is None:
                unmatched.append(path.stem)
                continue

            doc = parse(path.read_text(encoding="utf-8"), language)

            transcript = _drop_boilerplate(_clean(doc.get("transcript", "")))
            detail = _clean(doc.get("detail", ""))
            translation = _drop_boilerplate(_clean(doc.get("translation", "")))
            if translation:
                transcript = (transcript + " " + translation).strip()

            if transcript:
                slide["transcript_%s" % language] = transcript
            if detail:
                slide["detail_%s" % language] = detail
            if transcript or detail:
                updated += 1

    missing = sorted(sid for sid in by_id if "detail_th" not in by_id[sid])

    print("%d slide/language documents imported" % updated)
    if unmatched:
        print("  %d documents had no matching slide: %s" % (len(unmatched), unmatched[:5]))
    print("  %d slides still have no document" % len(missing))
    if missing:
        print("    these are the Canva deck pages; they need narration from the")
        print("    sales team, not a generated description: %s ..." % ", ".join(missing[:6]))

    if args.dry_run:
        print("\n--dry-run, nothing written")
        return 0

    args.index.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\nwrote %s" % args.index)
    print("The embeddings fingerprint has changed, so they rebuild on next start.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
