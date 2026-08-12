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

It opens the deck through `canva_display` — the same launcher the gallery
uses, meaning the real Chrome channel and fullscreen. That is not tidiness:
Canva withholds some slide images from Playwright's bundled Chromium because
it recognises it as automated, and a run using a plain `chromium.launch()`
photographed pages with their artwork missing and then "matched" them,
at a distance of about eleven, to whichever of our frames was emptiest.

Needs `CANVA_URL` in `.env`, `playwright install chromium`, and Pillow.
Takes a few minutes; the window is fullscreen, so leave it alone while it
runs.
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
    return list(image.convert("L").resize(THUMB, Image.BILINEAR).tobytes())


def distance(a, b) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def spread(t) -> float:
    """Standard deviation of a thumbnail — how much is *on* the page.

    Canva's page template is a pale gradient and the artwork arrives on top
    of it, so a page that hasn't finished loading is a smooth wash. That is
    exactly what a guest saw on the gallery screen, and photographing it
    would write "this page is blank" into the mapping as though it were a
    fact about the deck.
    """
    mean = sum(t) / len(t)
    return (sum((v - mean) ** 2 for v in t) / len(t)) ** 0.5


#: Below this, the screenshot is a background with nothing on it yet.
#:
#: Measured, not picked. Across all 59 exported frames at this thumbnail
#: size the *faintest* real page reads 20.9 (`ew_001`, the near-black cover)
#: and the palest floor plans sit in the high twenties; a bare gradient of
#: the kind Canva paints before the artwork arrives reads 7.2. Fourteen is
#: the middle of that gap with room on both sides.
#:
#: The evidence this matters: the first run of this script slept a fixed
#: 2.5 s and matched ten consecutive pages to the same near-empty frame
#: with scores identical to one decimal place. Pages do not resemble each
#: other that exactly. It was photographing the loading state — the same
#: blank screen a guest had already reported seeing.
MIN_SPREAD = 14.0


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


#: Longest we will wait for one page to finish drawing. Generous: this runs
#: once, by hand, and a slow page costs seconds — a wrong mapping costs a
#: guest being shown the wrong room.
SETTLE_CAP_S = 20.0

#: Two consecutive readings this close mean the page has stopped changing.
STILL = 0.8


async def shoot(page):
    from PIL import Image
    import io
    image = Image.open(io.BytesIO(await page.screenshot()))
    return image, thumb(image)


async def step_to(page, number: int) -> None:
    """Move one page forward, or deep-link if we're not where we thought.

    Sequential, because Canva loads the deck as you walk it. The viewer's
    progress bar shows this plainly: after opening on page 1 only the first
    handful of ticks are lit, and jumping to page 35 shows a page that has
    not been drawn yet — a pale empty gradient, which is exactly what a
    guest reported seeing on the gallery screen. Walking is not politeness
    towards Canva, it is the only way the pages exist to be photographed.
    """
    here = (await page.evaluate("location.hash") or "").lstrip("#")
    if here.isdigit() and int(here) == number - 1:
        await page.keyboard.press("ArrowRight")
        return
    await page.evaluate("location.hash = '#%d'" % number)


async def capture(page, number: int):
    """Wait for the current page to actually finish drawing.

    The first version slept 2.5 s and moved on. That produced a mapping of
    22 pages out of 50, with ten consecutive pages all "matching" the same
    near-empty frame — because a fixed sleep doesn't wait for anything, it
    just hopes. The heavy pages, the floor plans and the room photographs,
    are precisely the ones that lose that race, which is why every page it
    got right was a simple one.

    So watch instead of hope: screenshot until two readings in a row agree,
    then take that. Returns `(image, thumbnail, settled, seconds)`.

    "Settled" is not the same as "usable", and conflating them threw away
    good measurements. Some pages in this deck are video — the logo reveal
    for one — and a playing video never stops changing, so it always hit the
    cap and was discarded. Page 44 was discarded that way while matching its
    frame at a distance of 0.9, which is as certain as this gets. Stillness
    is only ever a device for not photographing the loading state; whether
    the match is trustworthy is what `SAME` and `MARGIN` are for. So the
    caller scores an unsettled page anyway and just says so.
    """
    await step_to(page, number)
    await asyncio.sleep(0.8)

    started = asyncio.get_event_loop().time()
    image, previous = await shoot(page)
    while True:
        await asyncio.sleep(0.6)
        image, current = await shoot(page)
        waited = asyncio.get_event_loop().time() - started
        # Still on the background? That is not "settled", that is not loaded
        # yet — a blank page is perfectly stable.
        if distance(current, previous) <= STILL and spread(current) >= MIN_SPREAD:
            return image, current, True, waited
        if waited >= SETTLE_CAP_S:
            return image, current, False, waited
        previous = current


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


async def run(write: bool, keep: Path | None, forced_total: int | None) -> int:
    from app.tools import canva_display

    if not settings.canva_url:
        print("CANVA_URL is not set in .env — nothing to measure.")
        return 2

    frames = local_frames()
    print("ภาพในเครื่อง: %d เฟรม (%s-001 ... )" % (len(frames), settings.canva_deck_prefix))

    # Open the window the *product* opens, not a browser of this script's
    # own. `canva_display._launch_browser` drives the real Chrome channel and
    # hides the automation flag, and its docstring says why: "Canva serves
    # some slide images to a normal browser but not to Playwright's bundled
    # Chromium, which it recognises as automated." This script had its own
    # plain `chromium.launch()`, so Canva withheld exactly those images and
    # the run photographed pages with the artwork missing — which then
    # matched, at a distance of about eleven, whichever of our frames was
    # emptiest. Twenty-eight pages "unmatched" for a reason that was written
    # down in this repo already.
    #
    # It is also fullscreen, so there is no Canva header or page counter in
    # the frame and the screenshot is the page, framed exactly like the
    # export it is being compared against.
    #
    # The lesson is the one this project keeps relearning: a measurement
    # taken one layer away from the thing being judged reads as rigour and
    # produces numbers about something else. Measure through the same code
    # the gallery runs.
    canva_display.settings.canva_warm_deck = False   # this walk is the warm-up
    page = await canva_display._ensure_page(why="measure the page mapping")
    if page is None:
        print("เปิดหน้าต่าง canva ไม่ได้ — ดูข้อความข้างบน")
        return 1
    print("เปิดด้วยหน้าต่างเดียวกับตอนพรีเซนต์จริง (chrome จริง + เต็มจอ)")

    try:
        total = forced_total or await total_pages(page)
        if total is None:
            print("อ่านจำนวนหน้าของ canva ไม่ได้ — หยุดไว้ก่อน ดีกว่าเดา")
            print("  ใส่ --total ถ้ารู้จำนวนหน้าอยู่แล้ว")
            return 1
        print("หน้าใน canva จริง: %d" % total)
        if total != len(frames):
            print("  ต่างจากภาพในเครื่อง %d หน้า — นี่คือสาเหตุที่บางหน้าไม่ตรง"
                  % abs(total - len(frames)))

        mapping: dict[str, int] = {}
        rows = []
        stalled = []
        for number in range(1, total + 1):
            image, shot, settled, waited = await capture(page, number)
            scored = sorted(((distance(shot, t), sid) for sid, t in frames))
            best, second = scored[0], scored[1] if len(scored) > 1 else (999.0, "-")
            # Confidence decides, not stillness. A video page never settles
            # and is no less identifiable for it.
            ok = best[0] <= SAME and best[0] * MARGIN <= second[0]
            if not settled:
                stalled.append(number)
            if ok:
                mapping[best[1]] = number
            rows.append((number, best[1], best[0], second[1], second[0], ok))
            why = "<-- ไม่มั่นใจ ข้าม" if not ok else ""
            if not settled:
                why += "  (ภาพยังขยับ น่าจะเป็นวิดีโอ)"
            print("  หน้า %2d -> %-8s (%.1f)   รองลงมา %-8s (%.1f)  %4.1fวิ %s"
                  % (number, best[1], best[0], second[1], second[0], waited, why))
            if keep:
                side_by_side(image, best[1] if ok else "", number, best[0], keep)
    finally:
        await canva_display.shutdown()

    unplaced = [sid for sid, _ in frames if sid not in mapping]
    blank = [n for n, _sid, score, _s2, _d2, ok in rows if not ok]
    print("\nจับคู่ได้ %d หน้า จาก %d" % (len(mapping), total))
    if unplaced:
        print("ภาพที่ไม่มีหน้าใน canva (%d): %s" % (len(unplaced), ", ".join(unplaced)))
        print("  ภาพพวกนี้จะไม่อยู่ในการพรีเซนต์ และจะไม่สั่งให้หน้าต่าง canva ขยับ")
        print("  แต่ยังค้นหาเจอและขึ้นบนจอของเราได้ตามปกติ")
    if blank:
        print("หน้า canva ที่ยังจับคู่ไม่ได้: %s" % ", ".join(str(n) for n in blank))
        print("  เปิดรูปเทียบใน --keep-shots ดูว่าเป็นหน้าใหม่ที่ยังไม่ได้ export จริง")
    if stalled:
        print("หน้าที่ภาพไม่หยุดขยับใน %.0f วินาที: %s"
              % (SETTLE_CAP_S, ", ".join(str(n) for n in stalled)))
        print("  ส่วนใหญ่คือหน้าวิดีโอ ซึ่งไม่มีวันหยุดขยับ ยังจับคู่ได้ตามปกติ")
        print("  ถ้าคะแนนดีพอ")
    if len(blank) > total * 0.2:
        print("\nหน้าที่จับคู่ไม่ได้มีลักษณะเดียวกันหมด: คะแนนที่ดีที่สุดก็ยังแย่")
        print("  (30 ขึ้นไป) แปลว่าหน้าพวกนี้ 'ไม่เหมือนภาพไหนเลยที่เรามี'")
        print("  ไม่ใช่ 'เหมือนหลายภาพจนเลือกไม่ถูก' — ต่างกันคนละเรื่อง")
        print("  อย่างหลังคือปัญหาการวัด อย่างแรกคือเด็คถูกแก้ไปแล้ว")
        print("  ทางแก้คือ export ภาพจาก canva ใหม่ ไม่ใช่ปรับเลขในสคริปต์นี้")
    if len(mapping) < total * 0.9:
        print("\n** จับคู่ได้ไม่ถึง 90%% — อย่าเพิ่ง --write **")
        print("   ตารางที่ไม่ครบทำให้สไลด์ที่หายไปไม่ถูกพรีเซนต์เลย")
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
                    help="save every canva page beside its match, to check by eye")
    ap.add_argument("--total", type=int,
                    help="pages in the deck, if canva won't say (fullscreen "
                         "hides its own counter)")
    args = ap.parse_args()
    keep = Path(args.keep_shots) if args.keep_shots else None
    if keep:
        keep.mkdir(parents=True, exist_ok=True)
    return asyncio.run(run(args.write, keep, args.total))


if __name__ == "__main__":
    raise SystemExit(main())
