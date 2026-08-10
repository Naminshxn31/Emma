#!/usr/bin/env python3
"""Read data/logs/*.jsonl and report what actually happened.

Written because of a specific mistake worth not repeating. Looking at the
project's ten tools, I quoted a general figure — "speech-to-speech is fine up
to about three tools, cascaded is safer past five" — and treated it as a
finding about *this* robot. It wasn't. Every failure seen in testing had a
concrete cause that was found and fixed: an instruction at the wrong end of
the prompt, a nudge that fed itself, two clocks driving one window. None of
them was the tool count.

The honest position is that nobody knows yet whether ten tools is a problem
here, and the only thing that can settle it is what the robot does in front of
real visitors. This turns the log into that answer.

    python scripts/analyze_log.py                # today
    python scripts/analyze_log.py --days 7
    python scripts/analyze_log.py data/logs/2026-08-07.jsonl

What to look for, and what each thing would mean:

  Tool mix          A tool that is never called is one the model can't tell
                    apart from its neighbours — the case for merging it.
                    A tool called far more than the others may be absorbing
                    calls meant for something else.

  Screen lead       Every slide change that happened with speech still queued.
                    Should be near zero now; if it isn't, the audio-lead guard
                    has a hole in it.

  Blank lookups     Questions that matched nothing well enough to show. High
                    counts mean the deck is missing content people ask for —
                    a retrieval problem, not a tool problem.

  Commercial        How often visitors ask about price, room types, promotions
                    and opening hours. This is the business case for filling in
                    data/condo_facts.json, in numbers rather than opinions.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load(paths: list[Path]) -> list[dict]:
    rows: list[dict] = []
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue        # a torn last line after a crash; skip it
            row["_file"] = path.name
            rows.append(row)
    return rows


def resolve(args) -> list[Path]:
    if args.files:
        return [Path(f) for f in args.files]
    log_dir = ROOT / "data" / "logs"
    if args.days <= 1:
        return [log_dir / f"{date.today():%Y-%m-%d}.jsonl"]
    return [
        log_dir / f"{date.today() - timedelta(days=n):%Y-%m-%d}.jsonl"
        for n in range(args.days)
    ]


def section(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="*", help="log files (default: today's)")
    ap.add_argument("--days", type=int, default=1, help="how many days back")
    ap.add_argument("--verbose", action="store_true", help="list every problem row")
    args = ap.parse_args()

    paths = resolve(args)
    rows = load(paths)
    if not rows:
        print("ไม่พบ log — รันเซิร์ฟเวอร์แล้วคุยกับหุ่นยนต์สักรอบก่อน")
        print("(ที่หา: %s)" % ", ".join(str(p) for p in paths))
        return 1

    kinds = Counter(r.get("event") for r in rows)
    sessions = kinds.get("session_start", 0)
    print("อ่าน %d บรรทัด จาก %d ไฟล์ / %d เซสชัน"
          % (len(rows), len({r["_file"] for r in rows}), sessions))
    if kinds.get("session_takeover"):
        print("  มีการยึดเซสชัน %d ครั้ง (เปิดหลายแท็บ)" % kinds["session_takeover"])

    # ---- the canva window ------------------------------------------------
    # First, because "เปิดไม่เต็มจอ" and "canva ไม่ขึ้น" look identical from a
    # screenshot and have three different causes between them.
    launches = [r for r in rows if r.get("event") == "canva_launch"]
    fulls = [r for r in rows if r.get("event") == "canva_fullscreen"]
    if launches or fulls:
        section("หน้าต่าง Canva")
        for r in launches:
            if r.get("how") == "persistent":
                print("  เปิดแบบ persistent (%s) kiosk=%s  <- ถูกต้อง"
                      % (r.get("channel"), r.get("kiosk")))
            else:
                print("  เปิดแบบ fallback — kiosk ใช้ไม่ได้ จะเห็นแถบเบราว์เซอร์")
                print("    -> launch_persistent_context ล้มเหลว ดู warning ใน console")
        for r in fulls:
            if r.get("worked"):
                print("  เข้าเต็มจอสำเร็จด้วยวิธี: %s" % r.get("how"))
            else:
                print("  เข้าเต็มจอไม่สำเร็จ — Canva ยังมีหัวและเลขหน้าล้อมภาพอยู่")
                print("    -> ปุ่ม/ปุ่มลัด/Fullscreen API ไม่ได้ผลทั้งสามทาง")

    # ---- tools -----------------------------------------------------------
    tools = Counter(r.get("name") for r in rows if r.get("event") == "tool")
    section("เครื่องมือที่ถูกเรียก")
    if not tools:
        print("  ยังไม่มีการเรียกเครื่องมือเลย")
    else:
        total = sum(tools.values())
        for name, n in tools.most_common():
            print("  %-20s %4d  %5.1f%%" % (name, n, 100 * n / total))

        # The question this script exists to answer.
        from app.tools import load_tools

        registered = {t.name for t in load_tools()}
        unused = sorted(registered - set(tools))
        if unused:
            print()
            print("  ไม่เคยถูกเรียกเลย: %s" % ", ".join(unused))
            print("  → ตัวที่ไม่เคยถูกเรียกคือตัวที่โมเดลแยกไม่ออกจากตัวข้างๆ")
            print("    เป็นหลักฐานว่าควรยุบรวม ไม่ใช่แค่รู้สึกว่าเครื่องมือเยอะ")

    # ---- picture vs voice -------------------------------------------------
    screens = [r for r in rows if r.get("event") == "screen"]
    early = [r for r in screens if (r.get("audio_lead_s") or 0) >= 1.0]
    section("ภาพนำเสียง")
    print("  เปลี่ยนภาพทั้งหมด %d ครั้ง" % len(screens))
    if screens:
        worst = max(screens, key=lambda r: r.get("audio_lead_s") or 0)
        print("  เปลี่ยนตอนยังมีเสียงค้าง >1 วิ: %d ครั้ง (%.0f%%)"
              % (len(early), 100 * len(early) / len(screens)))
        print("  แย่สุด: %.1f วิ ที่ %s" % (worst.get("audio_lead_s") or 0, worst.get("slide")))
        if early:
            print("  → ควรใกล้ศูนย์ ถ้าไม่ใช่ แปลว่าตัวกันภาพแซงเสียงยังมีรูรั่ว")
        if args.verbose:
            for r in early:
                print("     %s  %s  lead %.1fs" % (r.get("t"), r.get("slide"), r.get("audio_lead_s")))

    # ---- retrieval --------------------------------------------------------
    lookups = [r for r in rows if r.get("event") == "lookup"]
    blank = [r for r in lookups if not r.get("showed")]
    section("การค้นหา")
    print("  ค้นทั้งหมด %d ครั้ง / ไม่ขึ้นสไลด์ %d ครั้ง (%.0f%%)"
          % (len(lookups), len(blank), 100 * len(blank) / len(lookups) if lookups else 0))
    if blank:
        print("  คำถามที่ค้นแล้วไม่ขึ้นภาพ:")
        for q, n in Counter(r.get("query") for r in blank).most_common(10):
            print("    %-40s %d" % ((q or "")[:40], n))
        print("  → ถ้าซ้ำๆ กันแปลว่าเด็คขาดเนื้อหาที่คนถาม ไม่ใช่ปัญหาเครื่องมือ")

    # ---- what people actually want ---------------------------------------
    commercial = [r for r in rows if r.get("event") == "commercial_question"]
    section("คำถามที่ตอบไม่ได้ (ราคา/โปรโมชั่น/ห้อง/เวลาทำการ)")
    print("  ถูกถาม %d ครั้ง" % len(commercial))
    if commercial:
        for q, n in Counter(r.get("query") for r in commercial).most_common(10):
            print("    %-40s %d" % ((q or "")[:40], n))
        print()
        print("  → นี่คือคำถามที่ลูกค้าอยากรู้ที่สุดและหุ่นยนต์ตอบไม่ได้เลย")
        print("    เป็นตัวเลขไว้คุยกับฝ่ายขายว่าควรกรอก data/condo_facts.json แค่ไหน")

    # ---- the phone remote -------------------------------------------------
    # Separated from the tool counts on purpose. A tool called because a
    # salesperson pressed a button says nothing about whether the *model*
    # can tell that tool apart from its neighbours, which is the question the
    # tool mix above exists to answer. Mixed together, a busy weekend on the
    # remote would read as a model that suddenly got decisive.
    remote = [r for r in rows if r.get("event") == "remote"]
    if remote:
        section("สั่งจากรีโมท (มือถือ)")
        print("  กดปุ่มทั้งหมด %d ครั้ง" % len(remote))
        for action, n in Counter(r.get("action") for r in remote).most_common():
            print("    %-12s %d" % (action, n))
        print("  → เครื่องมือที่ถูกเรียกจากปุ่มพวกนี้ก็นับรวมอยู่ในหัวข้อด้านบนด้วย")
        print("    ถ้าตัวเลขสองฝั่งใกล้กันมาก แปลว่าเกือบทุกอย่างมาจากคน ไม่ใช่จากบทสนทนา")

    # ---- mishearings ------------------------------------------------------
    heard = [r.get("text", "") for r in rows if r.get("event") == "heard"]
    section("สิ่งที่หุ่นยนต์ได้ยิน")
    print("  %d ประโยค" % len(heard))
    if heard:
        short = [h for h in heard if len(h.strip()) <= 3]
        print("  สั้นผิดปกติ (<=3 ตัวอักษร, มักคือฟังไม่ออก): %d" % len(short))
        if args.verbose:
            for h in heard[-20:]:
                print("     %s" % h[:70])
        else:
            print("  (ใช้ --verbose เพื่อดู 20 ประโยคล่าสุด)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
