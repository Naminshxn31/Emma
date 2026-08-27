# -*- coding: utf-8 -*-
"""Fold newly-extracted shards into the merged corpus — append-only.

    python scripts/corpus_merge.py [--corpus D:/Data/emma-corpus] [--dry-run]

The pipeline when new files land in D:\\Data:

    1. python D:/Data/emma-corpus/emma_extract.py   (writes jsonl/ shards)
    2. python scripts/corpus_merge.py               (this — appends new chunks)
    3. python scripts/contextualize_corpus.py       (resume-safe, only new)
    4. python scripts/corpus_to_docs.py             (render for mydocs)

Why append-only is the whole design and not a nicety: chunk ids pair the
merged corpus with `data/emma-corpus/emma-corpus-contextual.jsonl`, whose
contexts were written *for the exact text* each id had at the time. And
pdftotext on this machine lays pages out differently from the machine that
built the corpus (measured 2026-08-26: same brochure, same id, entirely
different text under it — 57/58 chunks beyond whitespace). Re-merging an
existing document would therefore silently re-pair old contexts with new
text. So: an id already in the merged file is never touched, and a shard
whose ids all exist is skipped entirely.

Rules inherited from the original merge (see D:/Data/emma-corpus/README.md):
CAD exports (Update Column / Update Floor) stay out of the -clean file, and
exact-duplicate chunk text is dropped from -clean (the original cut 36.8%
near-dups; exact match is the honest approximation for increments).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def is_cad_export(source: str) -> bool:
    name = Path(source).name
    return name.startswith("Update Column") or name.startswith("Update Floor")


def normalized_name(source: str) -> str:
    """Filename with size-variant markers and copy counters stripped.

    "(Small Size)" / "low size" PDFs are re-exports of the same document,
    and "(1)" is a download counter — the original merge kept exactly one
    of each family. Content similarity CANNOT make this call: measured
    2026-08-26, genuinely-different weekly MEP reports score 0.96-0.99
    token containment against each other (template + same room names),
    the same range as true duplicates. The filename is the only signal
    that separates them, so the filename is the rule."""
    import re

    stem = Path(source.replace("\\", "/")).stem.lower()
    stem = re.sub(r"\(\s*small size\s*\)|small size|low size", " ", stem)
    stem = re.sub(r"\(\d+\)\s*$", " ", stem)
    return re.sub(r"\s+", " ", stem).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="D:/Data/emma-corpus")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    corpus = Path(args.corpus)
    full_path = corpus / "emma-corpus.jsonl"
    clean_path = corpus / "emma-corpus-clean.jsonl"
    shard_dir = corpus / "jsonl"
    if not (full_path.is_file() and clean_path.is_file() and shard_dir.is_dir()):
        print("ไม่พบ corpus ที่ %s (ต้องมี emma-corpus.jsonl, "
              "emma-corpus-clean.jsonl และโฟลเดอร์ jsonl/)" % corpus)
        return 2

    known_ids: set[str] = set()
    known_names: set[str] = set()
    clean_texts: set[str] = set()
    for line in full_path.open(encoding="utf-8"):
        r = json.loads(line)
        known_ids.add(r["id"])
        known_names.add(normalized_name(r.get("source", "")))
    for line in clean_path.open(encoding="utf-8"):
        clean_texts.add(json.loads(line)["text"])

    # One-time exclusions, one source path per line ("/" separators).
    # Chunk-level exact-text dedup catches re-attached documents when both
    # copies were extracted on the same machine; these are the ones whose
    # originals were extracted elsewhere, so exact match can never fire.
    skip_file = corpus / "_merge_skip.txt"
    skip_sources = set()
    if skip_file.is_file():
        skip_sources = {
            s.strip() for s in skip_file.read_text(encoding="utf-8").splitlines()
            if s.strip()
        }

    new_full: list[dict] = []
    new_clean: list[dict] = []
    for shard in sorted(shard_dir.glob("*.jsonl")):
        rows = [json.loads(l) for l in shard.open(encoding="utf-8")]
        if any(r["id"] in known_ids for r in rows):
            # Document already merged. Its shard may have been re-extracted
            # on a different machine with different text — appending any of
            # it would mismatch the contexts written for the original.
            continue
        src = rows[0].get("source", "") if rows else ""
        if src.replace("\\", "/") in skip_sources:
            print("ข้าม (skip-list): %s" % src)
            continue
        if normalized_name(src) in known_names:
            print("ข้าม (ฉบับย่อ/ก๊อปของเอกสารที่มีแล้ว): %s" % src)
            continue
        for r in rows:
            new_full.append(r)
            if is_cad_export(r.get("source", "")) or r["text"] in clean_texts:
                continue
            clean_texts.add(r["text"])
            new_clean.append(r)

    print("shard ใหม่: %d chunk -> เข้า clean %d chunk"
          % (len(new_full), len(new_clean)))
    if args.dry_run or not new_full:
        return 0

    with full_path.open("a", encoding="utf-8") as f:
        for r in new_full:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with clean_path.open("a", encoding="utf-8") as f:
        for r in new_clean:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("ต่อท้ายแล้ว — ขั้นถัดไป: python scripts/contextualize_corpus.py "
          "แล้ว python scripts/corpus_to_docs.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
