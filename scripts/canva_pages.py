#!/usr/bin/env python3
"""
Measure how the live Canva deck lines up with the images in `data/slides/`.

    python scripts/canva_pages.py            # measure and show
    python scripts/canva_pages.py --write    # ...and save the mapping

Why this exists
---------------
`canva_display` used to work out the Canva page from the slide id: `ew-047`
means page 47. That is arithmetic on our own filename, not a fact about the
other document, and the two documents are not the same length — the export
in `data/slides/` has 59 frames and the design has fewer pages. So the
mirror showed the wrong slide from the first divergence onwards, and the
ids past the end of the deck landed on nothing at all, which is the page
that came up blank.

Nobody can fix that by counting slides by hand and hoping. This opens the
real deck, walks every page, screenshots each one, and matches it against
our images, so the mapping is *observed*. What it writes to
`data/slides/canva_pages.json` is the answer to "which page of the live deck
actually shows this picture".

Matching is a coarse greyscale thumbnail comparison, which is enough because
the pictures are the same pictures — but it prints the score for every page
so a bad match is visible rather than silently written. Anything it is not
confident about it leaves out, and a slide left out simply doesn't move the
window.

Needs `CANVA_URL` in `.env`, `playwright install chromium`, and Pillow.
Opens a normal window so you can watch. Takes a couple of minutes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402

#: Thumbnail size for comparison. Small on purpose: we are identifying which
#: picture this is, not inspecting it, and a big thumbnail mostly measures
#: JPEG noise and the viewer's scaling.
THUMB = (64, 36)

#: Mean absolute difference (0-255) below which two thumbnails are called the
#: same picture. Set from the measured gap, not from taste — the script
#: prints the runner-up for every page so you can see the margin.
SAME = 18.0

#: How much better the best match has to be than the second best before it
#: counts. A page that matches two of our frames almost equally well is a
#: page we cannot place, and guessing is what caused the original problem.
MARGIN = 1.35


def thumb(image):
    from PIL import Image
    return list(image.convert("L").resize(THUMB, Image.BILINEAR).getdata())


def distance(a, b) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def local_frames() -> list[tuple[str, list[int]]]:
    """Our exported images, as (slide id, thumbnail)."""
    from PIL import Image
    folder = Path(settings.slides_dir)
    index = json.loads((folder / "index.json").read_text(encoding="utf-8"))
    out = []
    for item in index["images"]:
        if not item["id"].startswith(settings.canva_deck_prefix + "-"):
            continue
        out.append((item["id"], thumb(Image.open(folder / item["file"]))))
    return out


async def total_pages(page) -> int | None:
    """How many pages does the deck have?

    Two ways, because neither is documented. The viewer usually renders an
    "N / M" counter; failing that, ask for a page far past the end and see
    where Canva clamps us — the deck's own answer to "how far can I go".
    """
    try:
        text = await page.evaluate(
            "(document.body.innerText.match(/\\b(\\d{1,3})\\s*\\/\\s*(\\d{1,3})\\b/)"
            " || ['','',''])[2]"
        )
        if str(text).isdigit():
            return int(text)
    except Exception:
        pass
    try:
        await page.evaluate("location.hash = '#999'")
        await asyncio.sleep(3)
        fragment = (await page.evaluate("location.hash") or "").lstrip("#")
        if fragment.isdigit() and int(fragment) < 999:
            return int(fragment)
    except Exception:
        pass
    return None


async def capture(page, number: int):
    """Go to a page, let it settle, return (image, thumbnail)."""
    from PIL import Image
    import io
    await page.evaluate("location.hash = '#%d'" % number)
    # Canva animates in, and the artwork arrives after the background. Too
    # short a wait here photographs the empty gradient — which is exactly
    # what the gallery screen was showing, and would quietly poison the
    # mapping with "this page is blank".
    await asyncio.sleep(2.5)
    image = Image.open(io.BytesIO(await page.screenshot()))
    return image, thumb(image)


def side_by_side(shot, slide_id: str, number: int, score: float, folder: Path):
    """Save `canva page | our image` as one picture, to be checked by eye.

    The scores below say two pictures are similar; they cannot say the
    matching is *right*. Fifty of these opened in a folder can, in about a
    minute, and that is the only check that doesn't take my word for it.
    """
    from PIL import Image, ImageDraw
    W, H = 640, 360
    sheet = Image.new("RGB", (W * 2, H + 28), "white")
    sheet.paste(shot.convert("RGB").resize((W, H)), (0, 28))
    if slide_id:
        path = Path(settings.slides_dir) / ("%s.jpg" % slide_id.replace("-", "_"))
        if path.exists():
            sheet.paste(Image.open(path).resize((W, H)), (W, 28))
    d = ImageDraw.Draw(sheet)
    d.text((8, 8), "canva page %d" % number, fill="black")
    d.text((W + 8, 8), "%s   (score %.1f)" % (slide_id or "ไม่มีคู่", score),
           fill="black")
    folder.mkdir(parents=True, exist_ok=True)
    sheet.save(folder / ("page_%03d.png" % number))


async def run(write: bool, keep: Path | None) -> int:
    from playwright.async_api import async_playwright

    if not settings.canva_url:
        print("CANVA_URL is not set in .env — nothing to measure.")
        return 2

    frames = local_frames()
    print("ภาพในเครื่อง: %d เฟรม (%s-001 ... )" % (len(frames), settings.canva_deck_prefix))

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1600, "height": 900})
        await page.goto(settings.canva_url.split("#", 1)[0], wait_until="load")
        await asyncio.sleep(6)

        total = await total_pages(page)
        if total is None:
            print("อ่านจำนวนหน้าของ canva ไม่ได้ — หยุดไว้ก่อน ดีกว่าเดา")
            await browser.close()
            return 1
        print("หน้าใน canva จริง: %d" % total)
        if total != len(frames):
            print("  ต่างจากภาพในเครื่อง %d หน้า — นี่คือสาเหตุที่บางหน้าไม่ตรง"
                  % abs(total - len(frames)))

        mapping: dict[str, int] = {}
        rows = []
        for number in range(1, total + 1):
            image, shot = await capture(page, number)
            scored = sorted(((distance(shot, t), sid) for sid, t in frames))
            best, second = scored[0], scored[1] if len(scored) > 1 else (999.0, "-")
            ok = best[0] <= SAME and best[0] * MARGIN <= second[0]
            if ok:
                mapping[best[1]] = number
            rows.append((number, best[1], best[0], second[1], second[0], ok))
            print("  หน้า %2d -> %-8s (%.1f)   รองลงมา %-8s (%.1f) %s"
                  % (number, best[1], best[0], second[1], second[0],
                     "" if ok else "<-- ไม่มั่นใจ ข้าม"))
            if keep:
                side_by_side(image, best[1] if ok else "", number, best[0], keep)

        await browser.close()

    unplaced = [sid for sid, _ in frames if sid not in mapping]
    blank = [n for n, _sid, score, _s2, _d2, ok in rows if not ok]
    print("\nจับคู่ได้ %d หน้า จาก %d" % (len(mapping), total))
    if unplaced:
        print("ภาพที่ไม่มีหน้าใน canva (%d): %s" % (len(unplaced), ", ".join(unplaced)))
        print("  ภาพพวกนี้จะไม่อยู่ในการพรีเซนต์ และจะไม่สั่งให้หน้าต่าง canva ขยับ")
        print("  แต่ยังค้นหาเจอและขึ้นบนจอของเราได้ตามปกติ")
    if blank:
        print("หน้า canva ที่หาภาพคู่ไม่ได้: %s" % ", ".join(str(n) for n in blank))
        print("  เปิดรูปเทียบใน --keep-shots ดูก่อนว่าเป็นหน้าใหม่ที่ยังไม่ได้ export")
        print("  หรือแค่ตอนแคปหน้ายังโหลดไม่เสร็จ")
    print("\nลำดับพรีเซนต์หลังจากนี้จะเป็นลำดับหน้าของ canva %d หน้า" % len(mapping))

    if write:
        path = Path(settings.slides_dir) / "canva_pages.json"
        path.write_text(json.dumps(
            {"total": total, "deck_url": settings.canva_url.split("#", 1)[0],
             "pages": mapping}, ensure_ascii=False, indent=2), encoding="utf-8")
        print("เขียนแล้ว: %s" % path)
    else:
        print("\n(ยังไม่ได้เขียนไฟล์ — ใส่ --write ถ้าผลข้างบนดูถูกต้อง)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true",
                    help="save data/slides/canva_pages.json")
    ap.add_argument("--keep-shots", metavar="DIR",
                    help="save every canva page as a png, to check by eye")
    args = ap.parse_args()
    keep = Path(args.keep_shots) if args.keep_shots else None
    if keep:
        keep.mkdir(parents=True, exist_ok=True)
    return asyncio.run(run(args.write, keep))


if __name__ == "__main__":
    raise SystemExit(main())
