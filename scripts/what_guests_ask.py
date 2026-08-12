#!/usr/bin/env python3
"""
What did guests actually ask, and what did the robot fail to answer?

    python scripts/what_guests_ask.py --days 7
    python scripts/what_guests_ask.py --days 30 --save

Why
---
The one change that would measurably improve slide search is keywords on the
slides: all 65 currently carry fewer than three, and that is why real
questions and off-topic ones score in the same range — no threshold
separates them, which `scripts/eval_search.py` measured three times.

The words worth adding are the ones guests really use. Those cannot be
invented at a desk; they are in `data/logs/`, in the guest's own phrasing,
and they vanish when the log is deleted after `TURN_LOG_KEEP_DAYS`.

So this reads the logs and answers three questions:

1. **What was asked and not found?** The direct input to keyword work.
2. **What was asked and found the wrong thing first?** A different fix —
   the words are there but attached to the wrong slide.
3. **Which slides does anyone ever ask to see?** A slide nobody ever
   requests does not need keywords; a slide requested constantly does.

It prints Thai text verbatim, because a summary that paraphrases the
question destroys the only thing the question was useful for. `--save`
writes counts only, no guest text, to `data/log-summaries/`.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402


def load(days: int) -> list[dict]:
    folder = Path(settings.turn_log_dir if hasattr(settings, "turn_log_dir")
                  else "data/logs")
    today = dt.date.today()
    rows = []
    for back in range(days):
        path = folder / ("%s.jsonl" % (today - dt.timedelta(days=back)))
        if not path.exists():
            continue
        day = path.stem
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue          # a half-written last line is normal
            # The date lives in the filename, not in the rows. Carrying it
            # here is what lets everything below group by day without each
            # section re-deriving it from a path it no longer has.
            row["day"] = day
            rows.append(row)
    return rows


def _session_seconds(rows: list[dict]) -> dict[str, float]:
    """How long each day's sessions ran, from the log's own timestamps.

    `session_start` and `session_end` carry `t` as HH:MM:SS. A session that
    crashed has no end; it is credited up to the last line seen that day
    rather than dropped, because an unfinished session is exactly the shape
    of the problem this section is looking for.
    """
    out: dict[str, float] = {}
    open_at: dict[str, float] = {}
    last_at: dict[str, float] = {}

    def seconds(stamp: str) -> float | None:
        parts = (stamp or "").split(":")
        if len(parts) != 3:
            return None
        try:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        except ValueError:
            return None

    for row in rows:
        day = row.get("day") or ""
        now = seconds(row.get("t", ""))
        if now is None:
            continue
        last_at[day] = now
        if row.get("event") == "session_start":
            open_at[day] = now
        elif row.get("event") == "session_end" and day in open_at:
            out[day] = out.get(day, 0.0) + max(0.0, now - open_at.pop(day))
    for day, started in open_at.items():
        out[day] = out.get(day, 0.0) + max(0.0, last_at.get(day, started) - started)
    return out


def head(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--save", action="store_true",
                    help="write a counts-only summary (no guest text)")
    args = ap.parse_args()

    rows = load(args.days)
    if not rows:
        print("ไม่มี log ในช่วง %d วันที่ผ่านมา" % args.days)
        return 0

    sessions = sum(1 for r in rows if r.get("event") == "session_start")
    heard = [r.get("text", "").strip() for r in rows if r.get("event") == "heard"]
    heard = [h for h in heard if h]
    lookups = [r for r in rows if r.get("event") == "lookup"]
    missed = [r for r in lookups if not r.get("showed")]
    refused = [r for r in rows if r.get("event") == "commercial_question"]

    print("ช่วง %d วัน | %d ครั้งที่เปิดคุย | ลูกค้าพูด %d ครั้ง | ค้นหา %d ครั้ง"
          % (args.days, sessions, len(heard), len(lookups)))

    if lookups:
        rate = 100.0 * (len(lookups) - len(missed)) / len(lookups)
        print("ค้นแล้วได้สไลด์ %d จาก %d (%.0f%%)"
              % (len(lookups) - len(missed), len(lookups), rate))

    head("1. ถามแล้วไม่เจอ — เอาคำพวกนี้ไปเติมเป็น keyword")
    if not missed:
        print("  ไม่มี")
    for query, n in collections.Counter(
            r.get("query", "") for r in missed).most_common(args.top):
        near = next((r for r in missed if r.get("query") == query), {})
        close = near.get("best_title") or ""
        print("  %3dx  %-38s %s" % (n, query, ("ใกล้สุด: " + close) if close else ""))

    head("2. ถามเรื่องราคา/โปรโมชั่น — ตอบจาก condo_facts.json")
    if not refused:
        print("  ไม่มี")
    for query, n in collections.Counter(
            r.get("query", "") for r in refused).most_common(args.top):
        print("  %3dx  %s" % (n, query))

    head("3. สไลด์ที่ถูกขอให้เปิดดู")
    shown = collections.Counter(
        r.get("showed") for r in lookups if r.get("showed"))
    if not shown:
        print("  ไม่มี")
    for slide, n in shown.most_common(args.top):
        title = next((r.get("title") for r in lookups
                      if r.get("showed") == slide and r.get("title")), "")
        print("  %3dx  %-10s %s" % (n, slide, title or ""))

    head("4. การใช้งานต่อวัน — ดูว่าโควตาพอไหม")
    per_day = collections.Counter()
    minutes = collections.Counter()
    for r in rows:
        day = r.get("day") or ""
        if r.get("event") == "session_start":
            per_day[day] += 1
    # Session length from the log's own clock. Not a bill — the provider is
    # the only thing that can say what was actually charged — but it is the
    # number that moves, and a day that jumps from twenty minutes to four
    # hours is visible here before it is visible anywhere else.
    for path_day, seconds in _session_seconds(rows).items():
        minutes[path_day] = seconds / 60.0
    if not per_day and not minutes:
        print("  log ไม่ได้บันทึกวันที่ไว้ในแต่ละบรรทัด — ดูรวมทั้งช่วงแทน")
        print("  เปิดคุย %d ครั้ง | รวม %.0f นาที" % (sessions, sum(minutes.values())))
    for day in sorted(set(per_day) | set(minutes), reverse=True)[:14]:
        print("  %-12s %2d ครั้ง  %6.1f นาที" % (day or "(ไม่ทราบวัน)",
                                                 per_day.get(day, 0),
                                                 minutes.get(day, 0.0)))
    print("  รวม %.0f นาทีในช่วงนี้" % sum(minutes.values()))
    print("  ประมาณค่าใช้จ่ายถ้าเป็นแบบเสียเงิน: ~%.0f บาท"
          % (sum(minutes.values()) * 0.023 * 33))
    print("  (in $0.005 + out $0.018 ต่อนาที ที่ 33 บาท/ดอลลาร์ — ประมาณคร่าวๆ")
    print("   ตัวเลขจริงดูที่ ai.google.dev เท่านั้น)")

    head("5. คำที่ลูกค้าพูดบ่อย (ดิบ ไม่ตัดคำ)")
    words = collections.Counter()
    for line in heard:
        for w in line.split():
            if len(w) > 1:
                words[w] += 1
    for word, n in words.most_common(args.top):
        print("  %3dx  %s" % (n, word))

    if args.save:
        out = Path("data/log-summaries")
        out.mkdir(parents=True, exist_ok=True)
        path = out / ("asked-%s.json" % dt.date.today())
        # Counts only. The point of the summary is to survive log deletion
        # without becoming a second copy of what visitors said out loud.
        path.write_text(json.dumps({
            "days": args.days, "sessions": sessions,
            "heard_lines": len(heard), "lookups": len(lookups),
            "missed": len(missed), "commercial_questions": len(refused),
            "slides_shown": dict(shown),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print()
        print("เขียนสรุป (เฉพาะตัวเลข ไม่มีข้อความลูกค้า): %s" % path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
