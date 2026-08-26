#!/usr/bin/env python3
"""
Contextual Retrieval pass over the emma corpus (Anthropic's technique).

    python scripts/contextualize_corpus.py [--corpus D:/Data/emma-corpus] [--limit N]

A chunk like "งานเทคอนกรีตโครงสร้างเสา ชั้นที่ 5 | 1 | 0.4" carries nothing
that says which project, which report, which week — BM25 cannot find it and
an answer built on it cannot say where it came from. This script asks Gemini
to write one short Thai context sentence per chunk (which document, which
project, which date, which section) and stores it alongside the original
text. `corpus_to_docs.py` then renders context+text into data/personal-docs/
where the existing mydocs BM25 picks it up — no index code changes at all.
Anthropic measured -49% retrieval failures for BM25+embeddings with exactly
this preparation.

Free-tier survival, learned from build_embeddings.py's history:
- chunks go up in batches of 10 (≈190 requests for the whole corpus, inside
  the free RPD; one request per chunk would blow through it),
- every finished chunk id lands in _context_done.txt immediately — rerunning
  after a 429 storm or a new document drop continues, never restarts,
- 429/5xx get exponential backoff with generous patience: this is offline
  data preparation, nobody is standing in front of it (the one place this
  project is allowed to wait — a tool call is not).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402

# flash-lite: offline data preparation where plainer prose costs nothing,
# and the lite tier has its own (larger) free-tier daily quota — the first
# full run died at 50/1879 when regular flash's daily allowance ran out.
# Probed 2026-08-26: gemini-2.5-flash-lite still appears in ListModels but
# 404s on generateContent (retired from serving); 3.5-flash-lite answers.
MODEL = "gemini-3.5-flash-lite"
BATCH = 10

PROMPT = """คุณกำลังเตรียมเอกสารสำหรับระบบค้นหา ต่อไปนี้คือชิ้นส่วน (chunk) จากเอกสารเดียวกัน:

เอกสาร: {title}
หมวด: {category}
วันที่ของเอกสาร: {date}

สำหรับ chunk แต่ละชิ้นด้านล่าง เขียน "บริบท" ภาษาไทยสั้นๆ 1-2 ประโยค (ไม่เกิน 60 คำ)
ที่บอกว่า chunk นี้มาจากเอกสารอะไร โครงการไหน วันที่ใด และกำลังพูดถึงส่วนไหนของเอกสาร
เพื่อให้คนที่เห็นเฉพาะ chunk นี้ชิ้นเดียวเข้าใจที่มาได้ทันที ระบุชื่อโครงการเสมอถ้ารู้
(เช่น The Embassy, Embassy Life) ห้ามแต่งข้อเท็จจริงที่ไม่อยู่ในข้อมูลที่ให้

ตอบเป็น JSON array เท่านั้น: [{{"id": "...", "context": "..."}}, ...]

chunks:
{chunks}"""


def call_gemini(prompt: str, key: str) -> str:
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"{MODEL}:generateContent?key={key}")
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json"},
    }).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        out = json.loads(r.read())
    return out["candidates"][0]["content"]["parts"][0]["text"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="D:/Data/emma-corpus")
    # Output defaults to the repo's data tree, not the corpus drive: D: was
    # measured at 0 bytes free the day this ran. Derived, private, gitignored
    # — same standing as personal-docs.
    ap.add_argument("--out", default="data/emma-corpus")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N chunks (pilot runs)")
    args = ap.parse_args()

    corpus = Path(args.corpus)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = corpus / "emma-corpus-clean.jsonl"
    out_path = out_dir / "emma-corpus-contextual.jsonl"
    done_path = out_dir / "_context_done.txt"
    if not src.is_file():
        print(f"no corpus at {src}")
        return 1
    key = settings.gemini_api_key
    if not key:
        print("GEMINI_API_KEY is not set in .env")
        return 1

    rows = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    done = set(done_path.read_text(encoding="utf-8").splitlines()
               ) if done_path.exists() else set()
    todo = [r for r in rows if r["id"] not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(rows)} chunks total, {len(done)} done, {len(todo)} to go")

    # Batch within one source document, so every chunk in a request shares
    # the same title/category/date and the model sees its neighbours.
    by_doc: dict[str, list[dict]] = {}
    for r in todo:
        by_doc.setdefault(r["source"], []).append(r)

    n_done = 0
    with out_path.open("a", encoding="utf-8") as out, \
            done_path.open("a", encoding="utf-8") as done_f:
        for source, doc_rows in by_doc.items():
            for i in range(0, len(doc_rows), BATCH):
                batch = doc_rows[i:i + BATCH]
                chunk_json = json.dumps(
                    [{"id": r["id"], "text": r["text"][:1500]} for r in batch],
                    ensure_ascii=False)
                prompt = PROMPT.format(
                    title=batch[0].get("title", source),
                    category=batch[0].get("category_th", ""),
                    date=batch[0].get("date", "ไม่ระบุ"),
                    chunks=chunk_json)
                delay = 10.0
                for attempt in range(8):
                    try:
                        text = call_gemini(prompt, key)
                        ctx = {c["id"]: c["context"] for c in json.loads(text)}
                        break
                    except Exception as exc:
                        print(f"  retry in {delay:.0f}s: {exc}")
                        time.sleep(delay)
                        delay = min(delay * 2, 120)
                else:
                    print("giving up on this batch — rerun later, "
                          "progress is saved")
                    return 2
                for r in batch:
                    r2 = dict(r)
                    r2["context"] = ctx.get(r["id"], "")
                    out.write(json.dumps(r2, ensure_ascii=False) + "\n")
                    done_f.write(r["id"] + "\n")
                out.flush()
                done_f.flush()
                n_done += len(batch)
                print(f"  {n_done}/{len(todo)}  {source[:60]}")
                time.sleep(7)   # ~8 req/min, under the free-tier RPM
    print(f"done: {n_done} chunks contextualized -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
