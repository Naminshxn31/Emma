"""
Import a slide deck (PDF export, or a folder of images) into data/slides/.

Canva "view" links can't be read programmatically — export the deck from
Canva as **PDF** (Share → Download → PDF Standard) and point this at the
file:

    python scripts/import_slides.py "Embassy World Present V.3.pdf"

Each page becomes one slide. Any text on the page is pulled out and used as
the title/summary, which is what the assistant searches when a guest asks to
see something. That extraction is a starting point, not a finished index —
open `data/slides/index.json` afterwards and fix the titles and keywords for
the slides that matter. A slide the model can't find is a slide it will
never show.

Options:
    --append        keep existing slides and add these after them
    --prefix NAME   filename prefix for the generated images (default: deck)
    --max-width N   long edge of rendered slides in px (default 1920)
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "data" / "slides"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def _clean(text: str) -> str:
    return _collapse_letterspacing(re.sub(r"\s+", " ", (text or "")).strip())


def _collapse_letterspacing(text: str) -> str:
    """Undo Canva-style letter spacing: "3 R D F L O O R" -> "3RDFLOOR".

    Decorative letter spacing lands in the PDF text layer as a real space
    between every character, which destroys the word for search: n-grams of
    "units" match nothing in "T H E U N I T S". The spacing also removes the
    genuine word breaks, so the result runs together — still far better,
    because "theunits" at least contains "unit".
    """
    tokens = text.split()
    if len(tokens) < 4:
        return text
    singles = sum(1 for t in tokens if len(t) == 1)
    if singles / len(tokens) >= 0.7:
        return "".join(tokens)
    return text


def _titles_from_text(text: str) -> tuple[str, str]:
    """First meaningful line becomes the title, the rest the summary."""
    lines = [_clean(l) for l in (text or "").splitlines()]
    lines = [l for l in lines if len(l) > 1]
    if not lines:
        return "", ""
    title = lines[0][:120]
    summary = _clean(" ".join(lines[1:]))[:400]
    return title, summary


def _keywords(title: str, summary: str) -> list[str]:
    words = re.findall(r"[^\s,./•·|–—-]{3,}", f"{title} {summary}")
    seen, out = set(), []
    for w in words:
        key = w.lower()
        if key not in seen:
            seen.add(key)
            out.append(w)
    return out[:12]


def _page_spans(page) -> list[tuple[float, str]]:
    """(font size, text) for every non-empty span on the page."""
    spans = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = _clean(span.get("text", ""))
                if text:
                    spans.append((round(span.get("size", 0), 1), text))
    return spans


def _find_boilerplate(pages_spans: list[list[tuple[float, str]]]) -> set[str]:
    """Text repeated across most pages: watermarks, disclaimers, page chrome.

    Detected by frequency rather than a hardcoded list, so this works on any
    deck. Necessary because the largest text on a slide is often a watermark
    — this deck stamps "PRELIMINARY CONCEPT" at 30pt on every single page,
    which would otherwise become every slide's title.
    """
    from collections import Counter

    seen = Counter()
    for spans in pages_spans:
        for _size, text in set((s, t) for s, t in spans):
            seen[text.lower()] += 1
    threshold = max(2, int(len(pages_spans) * 0.4))
    return {text for text, count in seen.items() if count >= threshold}


def from_pdf(pdf: Path, out_dir: Path, prefix: str, max_width: int) -> list[dict]:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        sys.exit(
            "PyMuPDF is required to import a PDF.\n"
            "    pip install pymupdf\n"
            "Alternatively, export the deck as PNG/JPG images and point this "
            "script at that folder instead."
        )

    doc = fitz.open(pdf)
    pages_spans = [_page_spans(p) for p in doc]
    boilerplate = _find_boilerplate(pages_spans)
    if boilerplate:
        preview = sorted(boilerplate)[:3]
        print(f"ignoring {len(boilerplate)} repeated line(s) as boilerplate, "
              f"e.g. {preview}")

    slides = []
    for number, (page, spans) in enumerate(zip(doc, pages_spans), start=1):
        # Scale so the long edge lands near max_width — a 1080p chest screen
        # gains nothing from a 3000px render, and 59 of them add up fast.
        zoom = max_width / max(page.rect.width, page.rect.height)
        name = f"{prefix}_{number:03d}.jpg"
        page.get_pixmap(matrix=fitz.Matrix(zoom, zoom)).save(out_dir / name, "jpeg")

        content = [(size, text) for size, text in spans
                   if text.lower() not in boilerplate]
        content.sort(key=lambda pair: -pair[0])
        title = content[0][1][:120] if content else ""
        summary = _clean(" ".join(t for _s, t in content[1:]))[:400]
        title = title or f"สไลด์ที่ {number}"

        slides.append({
            "id": f"{prefix}-{number:03d}",
            "file": name,
            "type": "deck",
            "title_th": title,
            "title_en": title,
            "summary_th": summary,
            "summary_en": summary,
            "keywords_th": _keywords(title, summary),
            "keywords_en": _keywords(title, summary),
        })

    unnamed = sum(1 for s in slides if s["title_th"].startswith("สไลด์ที่"))
    print(f"rendered {len(slides)} pages (long edge ~{max_width}px)")
    if unnamed:
        print(f"{unnamed} page(s) had no usable text — give them titles in "
              f"index.json or the assistant can't find them")
    return slides


def from_folder(folder: Path, out_dir: Path, prefix: str) -> list[dict]:
    files = sorted(
        p for p in folder.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES
    )
    if not files:
        sys.exit(f"no images found in {folder}")
    slides = []
    for number, src in enumerate(files, start=1):
        name = f"{prefix}_{number:03d}{src.suffix.lower()}"
        if src.resolve() != (out_dir / name).resolve():
            shutil.copyfile(src, out_dir / name)
        title = f"สไลด์ที่ {number}"
        slides.append({
            "id": f"{prefix}-{number:03d}",
            "file": name,
            "type": "deck",
            "title_th": title,
            "title_en": f"Slide {number}",
            "summary_th": "",
            "summary_en": "",
            "keywords_th": [],
            "keywords_en": [],
        })
    print(f"copied {len(slides)} images")
    print(
        "NOTE: image imports have no text to extract. Edit "
        "data/slides/index.json and give each slide a real title and "
        "keywords, or the assistant won't be able to find them."
    )
    return slides


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", help="PDF file, or a folder of images")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--prefix", default="deck")
    parser.add_argument("--max-width", type=int, default=1920,
                        help="long edge of the rendered slides in pixels")
    parser.add_argument("--append", action="store_true",
                        help="add to the existing index instead of replacing it")
    args = parser.parse_args()

    source = Path(args.source).expanduser()
    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    index_path = out_dir / "index.json"

    if not source.exists():
        # A bare filename is resolved against the shell's working directory,
        # which is almost never where a fresh Canva download lands.
        hint = [f"not found: {source}", f"  looked in: {Path.cwd()}"]
        if not source.is_absolute():
            downloads = Path.home() / "Downloads" / source.name
            if downloads.is_file():
                hint.append(f"\nFound it here instead — use the full path:\n  {downloads}")
            else:
                hint.append(
                    "\nPass the full path, e.g.\n"
                    f'  python scripts/import_slides.py "{Path.home() / "Downloads" / source.name}"\n'
                    "or drag the file from Explorer onto the terminal window to "
                    "paste its path."
                )
        hint.append(
            "\nNo file yet? Export it from Canva first:\n"
            "  Share -> Download -> File type: PDF Standard -> Download"
        )
        sys.exit("\n".join(hint))

    if source.is_dir():
        slides = from_folder(source, out_dir, args.prefix)
    elif source.suffix.lower() == ".pdf":
        slides = from_pdf(source, out_dir, args.prefix, args.max_width)
    else:
        sys.exit("source must be a .pdf file or a folder of images")

    existing = []
    if args.append and index_path.is_file():
        try:
            raw = json.loads(index_path.read_text(encoding="utf-8"))
            existing = raw["images"] if isinstance(raw, dict) else raw
        except Exception:
            print("warning: existing index unreadable — replacing it")

    combined = existing + slides
    index_path.write_text(
        json.dumps({"images": combined}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"wrote {index_path} ({len(combined)} slides total)")
    print("Restart uvicorn, then open http://localhost:8000/display")


if __name__ == "__main__":
    main()
