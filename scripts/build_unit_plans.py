"""Crop the sales team's per-unit furniture sheets to the plan drawing.

    python scripts/build_unit_plans.py "C:/Users/.../data embassyworld.zip"
    python scripts/build_unit_plans.py "D:/.../Furniture PDF"

Input: one PDF per unit named like `A-201.pdf` (the "Furniture PDF" folder,
1,066 files, owner 2026-10-06), as a folder or anywhere inside a zip.
Output: `<unit_plan_assets>/<ROOM>.webp` plus `index.json` pinning each
file's sha256 — the folder `app/unit_plans.py` serves from. Gitignored;
rerun on any machine that has the PDFs, or copy the folder.

Each sheet is A3 landscape: title "STUDIO 25.0 SQ.M.(B-124)" top left, the
plan drawing (vector) in the middle, the furniture package list on the
right, terms at the bottom. Only the drawing goes on the card — the card is
a third of the screen wide, and the list and terms are unreadable there.

The one check that matters: **the room in the sheet's own title must equal
the file name.** A file renamed by hand is otherwise the wrong room's plan
on a card a customer can photograph, with nothing anywhere saying so. A
sheet that fails it is skipped and listed in index.json, never "fixed".
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_STEM = re.compile(r"([A-Z]{1,2}-\d{3,4})\.pdf\Z", re.IGNORECASE)
_TITLE_ROOM = re.compile(r"\(\s*([A-Z]{1,2}-\d{3,4})\s*\)")
DPI = 150
QUALITY = 82


def _sources(src: Path):
    """(room, pdf bytes, source name) for every per-unit PDF under src."""
    if src.is_file() and src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src) as archive:
            for info in archive.infolist():
                name = info.filename.rsplit("/", 1)[-1]
                m = _STEM.fullmatch(name)
                if m and not info.is_dir():
                    yield m.group(1).upper(), archive.read(info), name
        return
    for path in sorted(src.rglob("*.pdf")):
        m = _STEM.fullmatch(path.name)
        if m:
            yield m.group(1).upper(), path.read_bytes(), path.name


def plan_clip(page):
    """The plan drawing's box: every vector stroke that is not the page
    frame, the right-hand furniture panel, the header or the terms block.
    Measured on Studio / 1BR / 2BR sheets — the drawing sits in the left
    60% between ~8% and ~62% of the height on all of them."""
    import fitz

    width, height = page.rect.width, page.rect.height
    box = fitz.Rect()
    for drawing in page.get_drawings():
        r = drawing["rect"]
        if r.width > width * 0.6 or r.height > height * 0.8:
            continue
        if r.x0 > width * 0.6 or r.y0 > height * 0.62 or r.y1 < height * 0.08:
            continue
        box |= r
    return box


def render(pdf: bytes, room: str) -> tuple[bytes | None, str, str]:
    """(webp bytes or None, title, reason when None)."""
    import fitz
    from PIL import Image

    with fitz.open(stream=pdf, filetype="pdf") as doc:
        if doc.page_count < 1:
            return None, "", "no pages"
        page = doc[0]
        lines = [line.strip() for line in page.get_text().splitlines()]
        title = next((line for line in lines if "SQ" in line.upper() and _TITLE_ROOM.search(line)), "")
        m = _TITLE_ROOM.search(title)
        if not m:
            return None, title, "title has no room number"
        if m.group(1).upper() != room:
            return None, title, f"title says {m.group(1)}, file says {room}"
        box = plan_clip(page)
        if box.is_empty or box.width < 100 or box.height < 100:
            return None, title, "no plan drawing found"
        clip = box + (-6, -6, 6, 6)
        # Keep the title text out of the crop: on 2-bedroom sheets the
        # drawing starts just under it.
        hits = page.search_for(f"({room})")
        if hits:
            clip.y0 = max(clip.y0, hits[0].y1 + 2)
        clip &= page.rect
        pix = page.get_pixmap(dpi=DPI, clip=clip, alpha=False)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        out = io.BytesIO()
        image.save(out, "WEBP", quality=QUALITY, method=6)
        return out.getvalue(), title, ""


def build(src: Path, out_dir: Path, project_id: str, source_label: str) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    units: dict[str, dict] = {}
    skipped: list[dict] = []
    for room, pdf, name in _sources(src):
        if room in units:
            skipped.append({"file": name, "reason": "duplicate room"})
            continue
        body, title, reason = render(pdf, room)
        if body is None:
            skipped.append({"file": name, "title": title, "reason": reason})
            continue
        target = out_dir / f"{room}.webp"
        target.write_bytes(body)
        units[room] = {
            "file": target.name,
            "sha256": hashlib.sha256(body).hexdigest(),
            "title": title,
            "source_pdf": name,
            "source_sha256": hashlib.sha256(pdf).hexdigest(),
        }
    index = {
        "source_id": "unit_plan_assets",
        "project_id": project_id,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "generator": "scripts/build_unit_plans.py",
        "source_label": source_label,
        "crop": {"dpi": DPI, "webp_quality": QUALITY},
        "count": len(units),
        "skipped": skipped,
        "units": dict(sorted(units.items())),
    }
    (out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return index


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    from app.config import settings
    from app.data_sources import source_path

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="Furniture PDF folder or a zip containing it")
    parser.add_argument("--label", default="", help="where the PDFs came from (kept in index.json)")
    args = parser.parse_args()
    if not args.source.exists():
        print(f"ไม่พบ {args.source}")
        return 1
    out_dir = source_path("unit_plan_assets", settings.project_id)
    index = build(args.source, out_dir, settings.project_id,
                  args.label or args.source.name)
    total = sum(f.stat().st_size for f in out_dir.glob("*.webp"))
    print(f"แปลน {index['count']} ห้อง -> {out_dir} ({total / 1e6:.1f} MB)")
    for item in index["skipped"]:
        print(f"  ข้าม {item['file']}: {item['reason']}")
    return 0 if index["count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
