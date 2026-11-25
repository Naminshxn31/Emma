#!/usr/bin/env python3
"""The pictures a ceremony slide may use, laid out for whoever authors a slide.

    python scripts/build_ceremony_asset_catalog.py            # -> .tmp/ceremony-show/assets/

Writes `catalog.md` (every pinned picture of the stories catalogue: id, pixels, shape,
what it shows, the words of a line that back it, text baked into it, usable or excluded
and why) and contact sheets `sheet_NN.png` (thumbnails with their ids under them), so a
slide's author chooses by LOOKING and then names the picture by its id.

The pictures are the stories catalogue's own (data/projects/<project>/stories/assets,
pinned by sha256 in stories.json -- they already passed that pipeline: cropped, masked,
approved). Which of them a ceremony slide may show, what each one shows and which words
back it is data/ceremony/slide_assets.json, checked by app/ceremony_slides.py: a picture
that is not listed there as usable is not shown (deny by default). This script only
reads; it changes neither file.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

THUMB_W, THUMB_H, COLS, PER_SHEET = 380, 260, 5, 20


def rows() -> list[dict]:
    from app import ceremony_slides, stories

    catalog = stories.load_catalog() or {}
    listed = ceremony_slides.asset_registry()
    out = []
    for asset_id, pin in sorted((catalog.get("assets") or {}).items()):
        entry = listed.get(asset_id) or {}
        why = ceremony_slides.asset_problem(asset_id, catalog=catalog)
        out.append({"id": asset_id, "w": pin.get("w"), "h": pin.get("h"), "alpha": bool(pin.get("alpha")),
                    "file": stories.assets_dir() / str(pin.get("file")),
                    "kind": entry.get("kind") or "?", "shows": entry.get("shows") or "",
                    "about": entry.get("about") or [], "text": entry.get("text") or "",
                    "state": "ok" if why is None else "excluded: %s" % why})
    return out


def write(out: Path) -> tuple[int, int]:
    from PIL import Image, ImageDraw

    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("sheet_*.png"):
        old.unlink()
    listing = rows()
    lines = ["# Pictures a ceremony slide may use", "",
             "Built by `scripts/build_ceremony_asset_catalog.py` from the stories catalogue's pins and "
             "`data/ceremony/slide_assets.json`. Name a picture by its **id**. `about` = the words of the cue's "
             "English line that back the picture (one of them must be in the line). Pixels are the file's own: "
             "the stage is 1920 px wide at 16:9, so most pictures are enlarged ~1.8x when shown full-bleed.", "",
             "| sheet | id | px | shape | kind | shows | about (any one in the line) | baked-in text | state |",
             "|---|---|---|---|---|---|---|---|---|"]
    ok = 0
    for i, row in enumerate(listing):
        sheet = i // PER_SHEET + 1
        shape = "%.2f:1" % (row["w"] / row["h"]) if row["w"] and row["h"] else "?"
        ok += row["state"] == "ok"
        lines.append("| %02d | `%s` | %sx%s | %s | %s | %s | %s | %s | %s |" % (
            sheet, row["id"], row["w"], row["h"], shape, row["kind"], row["shows"],
            ", ".join(row["about"]) or "-", row["text"] or "-", row["state"]))
    (out / "catalog.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for start in range(0, len(listing), PER_SHEET):
        part = listing[start:start + PER_SHEET]
        height = ((len(part) + COLS - 1) // COLS) * (THUMB_H + 34)
        sheet = Image.new("RGB", (COLS * THUMB_W, height), (24, 24, 40))
        draw = ImageDraw.Draw(sheet)
        for k, row in enumerate(part):
            x, y = (k % COLS) * THUMB_W, (k // COLS) * (THUMB_H + 34)
            try:
                picture = Image.open(row["file"]).convert("RGBA")
                picture.thumbnail((THUMB_W - 12, THUMB_H - 8))
                under = Image.new("RGBA", picture.size, (70, 70, 96, 255))      # see-through pictures on grey
                under.alpha_composite(picture)
                sheet.paste(under.convert("RGB"), (x + (THUMB_W - picture.width) // 2, y + 4))
            except OSError:
                draw.text((x + 8, y + 8), "(file missing)", fill=(255, 120, 120))
            good = row["state"] == "ok"
            draw.text((x + 6, y + THUMB_H + 2), row["id"], fill=(170, 255, 190) if good else (255, 140, 140))
            draw.text((x + 6, y + THUMB_H + 16), ("%sx%s " % (row["w"], row["h"])) + ("ok" if good else row["state"])[:44],
                      fill=(200, 200, 215))
        sheet.save(out / ("sheet_%02d.png" % (start // PER_SHEET + 1)))
    return len(listing), ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=ROOT / ".tmp" / "ceremony-show" / "assets")
    args = ap.parse_args(argv)
    total, ok = write(args.out)
    print("%d pictures (%d usable) -> %s" % (total, ok, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
