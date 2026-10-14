"""Render the drawn plans for the robot face's stories (SVG -> PNG).

The owner asked for the basement plan drawn anew (13 Oct: "ภาพชั้นใต้ดินไม่ค่อย
สวยเลยทำแบบวาดขึ้นมาใหม่ได้ไหม", and the slide's own "CRYTAL STONE SAUNA"
misspelling). The drawings live as SVG next to the stories
(data/projects/<project>/stories/drawn/*.svg, tracked and reviewable); this
renders each to a PNG in the story-sources folder (.tmp/story-sources/drawn/),
where a recipe in stories/recipes/*.json picks it up like any other picture
-- build_story_assets.py then sizes, encodes and pins it as usual.

    python scripts/draw_story_plans.py            # every drawing, 2x
    python scripts/draw_story_plans.py --scale 3

Chromium (Playwright) renders it, so the text uses the same font engine as
the page; the SVG's own width/height is the frame, so a drawing traced over a
recipe's crop keeps that crop's coordinates (rings and zooms stay put).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import ACTIVE_PROJECT_ID  # noqa: E402

DRAWN = ROOT / "data" / "projects" / ACTIVE_PROJECT_ID / "stories" / "drawn"
OUT = ROOT / ".tmp" / "story-sources" / "drawn"


def frame(svg: str) -> tuple[int, int]:
    """The drawing's own size, from its root width/height attributes."""
    head = svg[:svg.index(">")]
    w = re.search(r'\bwidth="(\d+)"', head)
    h = re.search(r'\bheight="(\d+)"', head)
    if not (w and h):
        raise SystemExit("an SVG here must give width and height on its root element")
    return int(w.group(1)), int(h.group(1))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scale", type=float, default=2.0, help="device pixels per SVG unit")
    args = ap.parse_args()
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    drawings = sorted(DRAWN.glob("*.svg"))
    if not drawings:
        print("no drawings in", DRAWN)
        return 1
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for path in drawings:
                svg = path.read_text(encoding="utf-8")
                w, h = frame(svg)
                page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=args.scale)
                page.set_content("<!doctype html><html><body style='margin:0;background:#000'>%s</body></html>" % svg)
                page.wait_for_timeout(200)
                target = OUT / (path.stem + ".png")
                page.screenshot(path=str(target), clip={"x": 0, "y": 0, "width": w, "height": h})
                page.close()
                print("%s -> %s (%dx%d @%gx)" % (path.name, target.relative_to(ROOT), w, h, args.scale))
        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
