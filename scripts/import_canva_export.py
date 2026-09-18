#!/usr/bin/env python3
"""
Take a fresh Canva image export and make it the deck.

    python scripts/import_canva_export.py ~/Downloads/EmbassyWorld
    python scripts/import_canva_export.py ~/Downloads/EmbassyWorld --apply

Why
---
The Canva deck was edited: three sections were redesigned and it went from
59 pages to 50. `scripts/canva_pages.py` measured that from the live deck —
the offsets step at exactly the places pages disappeared — and it means 18
pages of the presentation have no picture and no script on this side. A
mapping table can point at what exists; it cannot conjure the pages that
don't.

So re-export from Canva and rebuild the deck from it. After that the images
*are* the deck, page number and slide id agree by construction, and the
mapping table is an identity — nothing left to drift.

What this does and does not do
------------------------------
It carries over the approved wording for every page that didn't change,
matched by comparing the pictures rather than by counting. That is the
whole value: without it, re-exporting throws away every script, title and
keyword that was ever written.

It does **not** write content for pages that are new. Those get a blank
entry and are listed at the end, alongside the old slides that used to sit
in that part of the deck, so their scripts can be reused or rewritten by
someone entitled to decide what the project claims. A slide with no script
is narrated from its summary, and a slide with neither is skipped — both
are better than an invented one.

Nothing is overwritten without `--apply`, and `--apply` keeps a backup.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402

THUMB = (64, 36)

#: Same picture. Comparing an export against an export, not against a
#: screenshot, so the identical pages come out at essentially zero and this
#: is generous by a wide margin.
SAME = 12.0
MARGIN = 1.5


def thumb(path: Path):
    from PIL import Image
    return list(Image.open(path).convert("L").resize(THUMB, Image.BILINEAR).tobytes())


def distance(a, b) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def pdf_pages(pdf: Path, out: Path, first: int, last: int) -> list[Path]:
    """Render a PDF export to page images.

    Canva's "Download as PDF" is one file instead of a folder of images, and
    it is what actually gets shared, so accept it directly rather than
    making somebody export twice.

    Rendered at the deck's own 1920x1080 so the pictures match what the
    screen shows and what `canva_pages.py` photographs.
    """
    import fitz
    doc = fitz.open(pdf)
    last = min(last or doc.page_count, doc.page_count)
    if first < 1 or first > last:
        raise SystemExit("ช่วงหน้าไม่ถูกต้อง: %d-%d จากทั้งหมด %d"
                         % (first, last, doc.page_count))
    out.mkdir(parents=True, exist_ok=True)
    made = []
    for number, index in enumerate(range(first - 1, last), 1):
        page = doc[index]
        scale = 1920 / page.rect.width
        target = out / ("page - %d.png" % number)
        page.get_pixmap(matrix=fitz.Matrix(scale, scale)).save(target)
        made.append(target)
    print("อ่าน %s หน้า %d-%d (%d หน้า จากทั้งไฟล์ %d หน้า)"
          % (pdf.name, first, last, len(made), doc.page_count))
    return made


def exported_pages(folder: Path) -> list[Path]:
    """The export, in page order.

    Canva names files like `Embassy World Present V.3 - 12.jpg`, and the
    trailing number is the page. Sorting these as strings puts 10 before 2,
    which would silently shuffle the deck — so sort on the number.
    """
    files = [p for p in folder.iterdir()
             if p.suffix.lower() in (".jpg", ".jpeg", ".png")]
    numbered = []
    for path in files:
        found = re.findall(r"(\d+)", path.stem)
        if not found:
            raise SystemExit("ไม่มีเลขหน้าในชื่อไฟล์: %s" % path.name)
        numbered.append((int(found[-1]), path))
    numbered.sort()
    pages = [p for _n, p in numbered]
    if [n for n, _p in numbered] != list(range(1, len(pages) + 1)):
        raise SystemExit(
            "เลขหน้าในชื่อไฟล์ไม่ต่อเนื่อง 1..%d — ตรวจโฟลเดอร์ก่อน\n  ได้: %s"
            % (len(pages), [n for n, _p in numbered]))
    return pages


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", help="a PDF exported from Canva, or a folder of images")
    ap.add_argument("--pages", metavar="A-B",
                    help="only this range of the PDF, e.g. 67-107. Canva files "
                         "often hold two versions of a deck back to back")
    ap.add_argument("--apply", action="store_true",
                    help="actually replace the active project slide directory (keeps a backup)")
    args = ap.parse_args()

    source = Path(args.source).expanduser()
    if not source.exists():
        raise SystemExit("ไม่พบไฟล์หรือโฟลเดอร์: %s" % source)

    first, last = 1, 0
    if args.pages:
        try:
            head, _, tail = args.pages.partition("-")
            first, last = int(head), int(tail or head)
        except ValueError:
            raise SystemExit("--pages ต้องเป็นรูปแบบ 67-107")

    slides_dir = Path(settings.slides_dir)
    index_path = slides_dir / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    prefix = settings.canva_deck_prefix

    old = [item for item in index["images"] if item["type"] == "deck"]
    old_thumbs = [(item, thumb(slides_dir / item["file"])) for item in old]
    if source.is_dir():
        if args.pages:
            raise SystemExit("--pages ใช้ได้กับไฟล์ PDF เท่านั้น")
        pages = exported_pages(source)
    else:
        pages = pdf_pages(source, slides_dir.parent / "canva-render", first, last)

    print("เด็คเดิม %d เฟรม  ->  export ใหม่ %d หน้า" % (len(old), len(pages)))
    print()

    entries = []
    fresh = []
    carried_from: set[str] = set()
    for number, path in enumerate(pages, 1):
        shot = thumb(path)
        scored = sorted(((distance(shot, t), item) for item, t in old_thumbs),
                        key=lambda pair: pair[0])
        best_d, best = scored[0]
        second_d = scored[1][0] if len(scored) > 1 else 999.0
        carried = best_d <= SAME and best_d * MARGIN <= second_d

        entry = {
            "id": "%s-%03d" % (prefix, number),
            "file": "%s_%03d.jpg" % (prefix, number),
            "type": "deck",
            "source_id": "slide_catalog",
            "project_id": settings.project_id,
        }
        if carried:
            carried_from.add(best["id"])
            # Everything a human decided about this picture. `silent` and
            # the approval flags were missing here, and both fail silently
            # in the worst direction: a re-import would have un-marked the
            # seven animation frames, so the robot reads the same line
            # seven times again, and it would have quietly withdrawn every
            # signature from every approved script. The picture is the
            # same picture — that is the whole basis for carrying the
            # script — so it is the basis for carrying the sign-off too.
            for key in ("title_th", "title_en", "summary_th", "summary_en",
                        "keywords_th", "keywords_en", "script_th", "ask_th",
                        "silent", "script_approved", "script_approved_by"):
                if key in best:
                    entry[key] = best[key]
            print("  หน้า %2d  <- %s  (%.1f)  %s"
                  % (number, best["id"], best_d, best.get("title_th", "")))
        else:
            entry.update({
                "title_th": "", "title_en": "",
                "summary_th": "", "summary_en": "",
                "keywords_th": [], "keywords_en": [],
            })
            fresh.append(number)
            print("  หน้า %2d  <- (ไม่มีของเดิมที่ตรง)  ใกล้สุดคือ %s ที่ %.1f"
                  % (number, best["id"], best_d))
        entries.append(entry)

    print()
    print("นำข้อมูลเดิมมาใช้ได้ %d หน้า / ต้องเขียนใหม่ %d หน้า"
          % (len(pages) - len(fresh), len(fresh)))
    print("  ในนั้นเป็นหน้าเงียบ %d | บทที่อนุมัติแล้ว %d"
          % (sum(1 for e in entries if e.get("silent")),
             sum(1 for e in entries if e.get("script_approved"))))

    if fresh:
        print()
        print("หน้าที่ต้องมีบทใหม่: %s" % ", ".join(str(n) for n in fresh))
        print("สไลด์เดิมที่ไม่ถูกใช้แล้ว — บทของพวกนี้อาจเอามาใช้ต่อได้:")
        for item in old:
            if item["id"] not in carried_from:
                script = (item.get("script_th") or "").strip()
                print("  %-8s %-38s %s" % (
                    item["id"], item.get("title_th", ""),
                    (script[:44] + "…") if len(script) > 45 else script))
        print()
        print("ผมไม่เขียนบทให้หน้าใหม่ เพราะไม่มีในเอกสารพรีเซนต์")
        print("หน้าที่ไม่มีบทจะบรรยายจาก summary และถ้าไม่มีทั้งคู่จะถูกข้าม")

    if not args.apply:
        print()
        print("(ยังไม่ได้แตะไฟล์อะไร — ใส่ --apply ถ้าผลข้างบนถูกต้อง)")
        return 0

    backup = slides_dir.parent / "slides-backup"
    if backup.exists():
        shutil.rmtree(backup)
    shutil.copytree(slides_dir, backup)
    print()
    print("สำรองของเดิมไว้ที่ %s" % backup)

    from PIL import Image
    for number, path in enumerate(pages, 1):
        target = slides_dir / ("%s_%03d.jpg" % (prefix, number))
        Image.open(path).convert("RGB").save(target, quality=92)
    for item in old:
        stale = slides_dir / item["file"]
        if int(item["id"].rsplit("-", 1)[1]) > len(pages) and stale.exists():
            stale.unlink()

    index["images"] = [i for i in index["images"] if i["type"] != "deck"] + entries
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2),
                          encoding="utf-8")

    # Page number and id now agree by construction. Written out rather than
    # left to the fallback, so the deck order is a stated fact either way.
    (slides_dir / "canva_pages.json").write_text(json.dumps(
        {"source_id": "canva_page_mapping", "project_id": settings.project_id,
         "total": len(pages),
         "deck_url": settings.canva_url.split("#", 1)[0],
         "note": "rebuilt from a canva export — id and page agree",
         "pages": {"%s-%03d" % (prefix, n): n for n in range(1, len(pages) + 1)}},
        ensure_ascii=False, indent=2), encoding="utf-8")

    print("เขียน %d ภาพ, index.json และ canva_pages.json แล้ว" % len(pages))
    print("รัน pytest แล้วเปิดพรีเซนต์ดูหนึ่งรอบก่อนใช้งานจริง")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
