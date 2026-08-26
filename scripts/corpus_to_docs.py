#!/usr/bin/env python3
"""
Render the contextualized corpus into Emma's searchable documents.

    python scripts/corpus_to_docs.py

Reads data/emma-corpus/emma-corpus-contextual.jsonl (the output of
contextualize_corpus.py) and writes one .md per source document into
data/personal-docs/corpus/. From there the existing mydocs index does
everything — it re-fingerprints on the next question, no restart, no new
index code. The context sentence rides *inside the same paragraph block* as
its chunk on purpose: mydocs packs files by blank-line blocks, and a
context separated from its text by a blank line could land in a different
chunk, which would un-solve the exact problem it exists to solve.

Idempotent: the corpus/ subfolder is cleared and rewritten every run, so a
re-run after new documents (or better contexts) never leaves stale files.
Only that subfolder — the rest of personal-docs belongs to the owner.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402

SRC = Path("data/emma-corpus/emma-corpus-contextual.jsonl")


def slug(source: str) -> str:
    s = re.sub(r"[^\w฀-๿]+", "-", source, flags=re.UNICODE)
    return s.strip("-")[:120] or "doc"


def one_block(row: dict) -> str:
    """Context + text as a single blank-line-free block."""
    text = re.sub(r"\n\s*\n+", "\n", row["text"].strip())
    ctx = (row.get("context") or "").strip()
    return f"[{ctx}]\n{text}" if ctx else text


def main() -> int:
    if not SRC.is_file():
        print(f"no contextual corpus at {SRC} — run "
              "scripts/contextualize_corpus.py first")
        return 1
    rows = [json.loads(l) for l in SRC.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    by_source: dict[str, list[dict]] = {}
    for r in rows:
        by_source.setdefault(r["source"], []).append(r)

    out_dir = Path(settings.personal_docs_dir).expanduser() / "corpus"
    if out_dir.is_dir():
        for old in out_dir.glob("*.md"):
            old.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)

    for source, doc_rows in by_source.items():
        doc_rows.sort(key=lambda r: r["id"])
        head = (f"# {doc_rows[0].get('title', source)}\n"
                f"หมวด: {doc_rows[0].get('category_th', '')} · "
                f"ไฟล์ต้นทาง: {source} · "
                f"วันที่เอกสาร: {doc_rows[0].get('date', 'ไม่ระบุ')}")
        body = "\n\n".join(one_block(r) for r in doc_rows)
        (out_dir / f"{slug(source)}.md").write_text(
            head + "\n\n" + body + "\n", encoding="utf-8")
    print(f"{len(by_source)} documents, {len(rows)} chunks -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
