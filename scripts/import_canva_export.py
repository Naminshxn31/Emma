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
    ap.add_argument("folder", help="folder of images exported from Canva")
    ap.add_argument("--apply", action="store_true",
                    help="actually replace data/slides (keeps a backup)")
    args = ap.parse_args()

    folder = Path(args.folder).expanduser()
    if not folder.is_dir():
        raise SystemExit("ไม่พบโฟลเดอร์: %s" % folder)

    slides_dir = Path(settings.slides_dir)
    index_path = slides_dir / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    prefix = settings.canva_deck_prefix

    old = [item for item in index["images"] if item["type"] == "deck"]
    old_thumbs = [(item, thumb(slides_dir / item["file"])) for item in old]
    pages = exported_pages(folder)

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
        }
        if carried:
            carried_from.add(best["id"])
            for key in ("title_th", "title_en", "summary_th", "summary_en",
                        "keywords_th", "keywords_en", "script_th", "ask_th"):
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
        {"total": len(pages),
         "deck_url": settings.canva_url.split("#", 1)[0],
         "note": "rebuilt from a canva export — id and page agree",
         "pages": {"%s-%03d" % (prefix, n): n for n in range(1, len(pages) + 1)}},
        ensure_ascii=False, indent=2), encoding="utf-8")

    print("เขียน %d ภาพ, index.json และ canva_pages.json แล้ว" % len(pages))
    print("รัน pytest แล้วเปิดพรีเซนต์ดูหนึ่งรอบก่อนใช้งานจริง")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
