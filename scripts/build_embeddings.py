#!/usr/bin/env python3
"""Build the active project's slide embeddings cache once, patiently.

Semantic search had never run in this project, and the cause was not the code
that uses it. The free tier caps the embedding model at 100 requests a minute;
the index build is a handful of requests, but every server start without a
cached file does it again, and a day of restarts walked the peak to 96/100.
Past that the build raised, the caller logged "continuing with lexical search
only", the file was never written — and the next start began from nothing. A
loop that cannot finish while it is being throttled.

Doing it here instead of at startup separates the two concerns. A server
should come up now; a one-off index build can afford to sit and wait.

    python scripts/build_embeddings.py
    python scripts/build_embeddings.py --force    # rebuild even if cached
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="rebuild even if cached")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from app.config import settings
    from app.tools import slide_search
    from app.tools.slides import load_slides

    from app.tools.retrieval import choose_provider, embedding_signature

    provider = choose_provider()
    if provider == "off":
        print("ไม่มีทางสร้าง embeddings ได้เลย — เลือกอย่างใดอย่างหนึ่ง:")
        print("  1) ตั้ง GEMINI_API_KEY ใน .env")
        print("  2) pip install sentence-transformers  แล้วตั้ง EMBED_PROVIDER=local")
        return 1
    print("ใช้: %s" % embedding_signature(provider))
    if provider == "local":
        print("(ครั้งแรกจะดาวน์โหลดโมเดล ~470MB หลังจากนั้นทำงานออฟไลน์ ไม่ใช้โควตา)")

    cache = Path(settings.slides_dir).expanduser() / "embeddings.npz"
    if cache.exists() and not args.force:
        print("มีอยู่แล้ว: %s" % cache)
        print("ถ้าแก้สไลด์แล้วอยากสร้างใหม่ ใช้ --force")
        return 0
    if args.force and cache.exists():
        cache.unlink()

    slides = load_slides()
    if not slides:
        print("ไม่พบสไลด์ — เช็ค SLIDES_DIR ใน .env")
        return 1

    print("กำลัง embed %d สไลด์ ..." % len(slides))
    print("ถ้าโดนจำกัดโควตา มันจะรอแล้วลองใหม่เอง (สูงสุด 6 ครั้งต่อชุด)")
    started = time.monotonic()

    slide_search.reset()
    index = slide_search.get_index(slides)
    if getattr(index, "semantic", None) is None:
        print()
        print("สร้างไม่สำเร็จ — ดูข้อความข้างบนว่าติดตรงไหน")
        print("ถ้าเป็นเรื่องโควตา ลองใหม่อีกครั้งในอีกสักครู่ หรือเปิด billing")
        return 1

    print("เสร็จใน %.0f วินาที -> %s" % (time.monotonic() - started, cache))
    print("การค้นหาข้ามภาษา (จีน/รัสเซีย/ญี่ปุ่น) จะทำงานหลังรีสตาร์ตเซิร์ฟเวอร์")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
