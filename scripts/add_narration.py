"""
Attach narration scripts to slides.

Write one plain-text file with a page number heading before each script.
All of these headings work, so you can paste from almost any document
without reformatting:

    หน้า 1
    สวัสดีค่ะ ยินดีต้อนรับสู่โครงการ Embassy World...

    ## 2
    โครงการนี้ตั้งอยู่ใจกลางเมือง...

    Slide 3:
    สิ่งอำนวยความสะดวกของเรา...

Then:

    python scripts/add_narration.py narration.txt

Numbers refer to **pages of the imported deck** (slide 1 = page 1), matched
against ids like `ew-001`. Use `--prefix` if your deck was imported under a
different name.

Nothing is overwritten silently: slides that already have a script are
reported and skipped unless you pass `--replace`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INDEX = ROOT / "data" / "slides" / "index.json"

# "หน้า 12" / "สไลด์ 12" / "## 12" / "12." / "Slide 12:" / "[12]"
_HEADING = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\[)?(?:หน้า|สไลด์|slide|page|p\.?)?\s*(\d{1,3})\s*(?:\])?\s*[:.\)-]?\s*$",
    re.IGNORECASE,
)


def parse(text: str) -> dict[int, str]:
    """Split the file into {page number: script}."""
    scripts: dict[int, list[str]] = {}
    current: int | None = None
    for line in text.splitlines():
        match = _HEADING.match(line)
        if match and line.strip():
            current = int(match.group(1))
            scripts.setdefault(current, [])
            continue
        if current is not None:
            scripts[current].append(line)

    return {
        number: "\n".join(lines).strip()
        for number, lines in scripts.items()
        if "\n".join(lines).strip()
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("source", help="text file of narration scripts")
    parser.add_argument("--index", default=str(DEFAULT_INDEX))
    parser.add_argument("--prefix", default="ew",
                        help="slide id prefix the numbers refer to (default: ew)")
    parser.add_argument("--lang", default="th", choices=["th", "en"],
                        help="which language field to fill (default: th)")
    parser.add_argument("--replace", action="store_true",
                        help="overwrite scripts that are already present")
    args = parser.parse_args()

    source = Path(args.source).expanduser()
    if not source.is_file():
        sys.exit(f"not found: {source}\n  looked in: {Path.cwd()}")

    index_path = Path(args.index).expanduser()
    if not index_path.is_file():
        sys.exit(f"no slide index at {index_path} — import a deck first")

    scripts = parse(source.read_text(encoding="utf-8"))
    if not scripts:
        sys.exit(
            "no page headings found.\n"
            "Each script needs a line with just its page number above it, e.g.\n"
            "    หน้า 1\n    <script text>\n\n    หน้า 2\n    <script text>"
        )

    raw = json.loads(index_path.read_text(encoding="utf-8"))
    slides = raw["images"] if isinstance(raw, dict) else raw
    by_id = {s["id"]: s for s in slides}

    field = f"script_{args.lang}"
    applied, skipped, missing = 0, [], []
    for number, script in sorted(scripts.items()):
        slide_id = f"{args.prefix}-{number:03d}"
        slide = by_id.get(slide_id)
        if slide is None:
            missing.append(number)
            continue
        if slide.get(field) and not args.replace:
            skipped.append(number)
            continue
        slide[field] = script
        applied += 1

    index_path.write_text(
        json.dumps({"images": slides}, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    print(f"attached {applied} script(s) to {args.prefix}-* slides ({field})")
    if skipped:
        print(f"skipped {len(skipped)} that already had one: {skipped[:10]}"
              f"{'…' if len(skipped) > 10 else ''}  (use --replace to overwrite)")
    if missing:
        print(f"no such slide for page(s): {missing[:10]}"
              f"{'…' if len(missing) > 10 else ''}  — check --prefix")

    total = sum(1 for s in slides if s.get("script_th") or s.get("script_en"))
    print(f"{total} of {len(slides)} slides now have narration")
    print("Restart uvicorn to load the change.")


if __name__ == "__main__":
    main()
