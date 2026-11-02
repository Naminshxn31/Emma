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
                    the project facts source, in numbers rather than opinions.
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


def is_internal(row: dict) -> bool:
    """A row the machine caused, not a guest.

    Marked at the source since 2026-10-07 (`turnlog.origin`, set by the
    startup search warm-up). Older logs carry no mark, and the warm-up was
    most of their "lookups": over 2026-09-29..10-01 the top "customer query"
    was "warm up", the boot-time call that loads the search model. Those
    unmarked rows are recognised the only way they can be — the warm-up's
    fixed query outside any voice session. That half of the test can go once
    the last unmarked log ages out (TURN_LOG_KEEP_DAYS).
    """
    if row.get("origin"):
        return True
    return (row.get("event") == "lookup" and row.get("query") == "warm up"
            and not row.get("session_id"))


def _pct(values: list[float], p: float) -> float:
    """Linear-interpolated percentile; `values` must be non-empty."""
    xs = sorted(values)
    k = (len(xs) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


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


def latency(rows: list[dict]) -> None:
    """How long a guest waits, from the numbers the session records.

    `end_signal_to_audio_ms` starts when the local VAD gate closes the
    segment — which is already VAD_SILENCE_MS (+~0.1 s of detector decay,
    measured with the real model) after their last syllable — and stops at
    the first reply audio reaching this server. Both halves are printed
    because they are tuned in different places: the first is a .env knob,
    the second is the model.
    """
    firsts = [r for r in rows if r.get("event") == "metric_first_audio"
              and isinstance(r.get("end_signal_to_audio_ms"), (int, float))]
    reopens = [r for r in rows if r.get("event") == "metric_vad_reopen"
               and isinstance(r.get("gap_ms"), (int, float))]
    if not firsts and not reopens:
        return
    section("จังหวะการตอบ (ลูกค้าพูดจบ -> เสียงแรกของ Emma)")
    if firsts:
        lat = [r["end_signal_to_audio_ms"] for r in firsts]
        print("  ด่าน VAD ตัดจบ -> เสียงแรกถึงเซิร์ฟเวอร์: n=%d  p50 %.0f ms  p90 %.0f ms"
              % (len(lat), _pct(lat, 50), _pct(lat, 90)))
        print("  (บวกเวลารอเงียบของด่าน VAD ก่อนหน้านั้นอีก ≈ VAD_SILENCE_MS + 100 ms"
              " และเวลาเล่นเสียงฝั่งเบราว์เซอร์ที่ไม่ได้วัด)")
        # Answers that went through tools carry `tool_hops` (app/metrics.py,
        # 28 Oct: before it they logged no end signal at all, so this line
        # only described the fast, tool-free answers).
        direct = [r["end_signal_to_audio_ms"] for r in firsts if not r.get("tool_hops")]
        chained = [r["end_signal_to_audio_ms"] for r in firsts if r.get("tool_hops")]
        if chained:
            print("  ตอบตรง n=%d p50 %.0f ms p90 %.0f ms / ผ่านเครื่องมือ n=%d p50 %.0f ms p90 %.0f ms"
                  % (len(direct), _pct(direct, 50) if direct else 0, _pct(direct, 90) if direct else 0,
                     len(chained), _pct(chained, 50), _pct(chained, 90)))
        taps = [r["end_signal_to_audio_ms"] for r in firsts if r.get("end_signal_source") == "tap"]
        if taps:
            print("  แตะปุ่มถาม -> เสียงแรก: n=%d p50 %.0f ms" % (len(taps), _pct(taps, 50)))
        thoughts: dict[str, int] = {}
        for r in rows:
            tid, n = r.get("observed_turn_id"), r.get("thoughts_token_count")
            if r.get("event") == "metric_usage" and tid and type(n) is int:
                thoughts[tid] = max(n, thoughts.get(tid, 0))
        paired = [(r["end_signal_to_audio_ms"], thoughts[r["turn_id"]])
                  for r in firsts if r.get("turn_id") in thoughts]
        if len(paired) >= 10:
            cut = _pct([t for _, t in paired], 50)
            low = [ms for ms, t in paired if t <= cut]
            high = [ms for ms, t in paired if t > cut]
            if low and high:
                print("  thinking tokens <= %.0f: p50 %.0f ms (n=%d) / > %.0f: p50 %.0f ms (n=%d)"
                      % (cut, _pct(low, 50), len(low), cut, _pct(high, 50), len(high)))
                print("  → ส่วนที่แกว่งคือการคิดของโมเดล ไม่ใช่ท่อเสียงของเรา")
    if reopens:
        gaps = [r["gap_ms"] for r in reopens]
        quick = [g for g in gaps if g <= 3000]
        ends = len(firsts) + len(reopens)
        knobs = sorted({str(r.get("silence_ms")) for r in reopens})
        print("  ลูกค้าพูดต่อทั้งที่ Emma ยังไม่ได้ตอบ: %d ครั้ง (ภายใน 3 วิ %d = %.0f%% ของการตัดจบ)"
              " ที่ VAD_SILENCE_MS=%s"
              % (len(gaps), len(quick), 100 * len(quick) / ends if ends else 0,
                 "/".join(knobs)))
        print("  → แต่ละครั้งคือคนที่ยังพูดไม่จบแต่ด่าน VAD ตัดสินว่าจบแล้ว"
              " ใช้ตัวเลขนี้เทียบก่อน/หลังปรับ VAD_SILENCE_MS")


def focus(rows: list[dict]) -> None:
    """Listening focus (docs/listening-focus-2026-10-09.md): what it let
    through, what it held back and why, hour by hour. Numbers only — the
    rows carry no text, and nothing here prints a name.

    The thresholds (FOCUS_FACE_MIN_PX, FOCUS_YAW_MAX_DEG, the far floor)
    are tuned from these lines, not from theory: `px`/`yaw` of opens is
    where the people the robot did talk to stood, and `rms` of opens is the
    level a floor would have to stay under.
    """
    opens = [r for r in rows if r.get("event") == "focus_open"]
    denies = [r for r in rows if r.get("event") == "focus_deny"]
    labels = [r for r in rows if r.get("event") == "focus_label"]
    away = [r for r in rows if r.get("event") == "idle_timeout" and r.get("why") == "no_face"]
    missing = [r for r in rows if r.get("event") == "focus_unavailable"]
    has_noise = any(r.get("event") == "noise_reply_dropped" for r in rows)
    stalled = [r for r in rows if r.get("event") == "focus_note_stalled"]
    engaged = [r for r in rows if r.get("event") in
               ("focus_wake", "focus_wake_cpu", "focus_release", "focus_gap", "focus_goodbye")]
    if not (opens or denies or labels or away or missing or stalled or engaged or has_noise):
        return
    section("โฟกัสการฟัง (ฟังเฉพาะคนหน้าหุ่น)")
    if missing:
        print("  เปิด FOCUS แต่ไม่มี VAD ในเครื่อง %d เซสชัน — ด่านนี้ไม่ได้ทำงานเลย"
              " (ต้อง VAD_MODE=local)" % len(missing))
    print("  เปิดให้ฟัง %d ช่วงพูด / ไม่ฟัง %d ครั้ง (นับเป็นช่วงที่ถูกปัด ไม่ใช่เฟรม)"
          % (len(opens), len(denies)))
    for reason, n in Counter(r.get("reason") for r in denies).most_common():
        print("    ไม่ฟังเพราะ %-28s %4d" % (reason, n))
    hours = sorted({str(r.get("t", ""))[:2] for r in opens + denies if r.get("t")})
    if hours:
        print("  รายชั่วโมง (เปิด/ไม่ฟัง):")
        for h in hours:
            o = sum(1 for r in opens if str(r.get("t", "")).startswith(h))
            d = sum(1 for r in denies if str(r.get("t", "")).startswith(h))
            print("    %s:00  %4d / %4d" % (h, o, d))
    for key, label in (("px", "ความกว้างหน้า (px)"), ("yaw", "หน้าหัน (องศา)"),
                       ("rms", "ระดับเสียงตอนเริ่มพูด (rms)")):
        for name, group in (("เปิด", opens), ("ไม่ฟัง", denies)):
            xs = [r[key] for r in group if isinstance(r.get(key), (int, float))]
            if xs:
                print("  %s ตอน%s: n=%d  p10 %.3g  p50 %.3g  p90 %.3g"
                      % (label, name, len(xs), _pct(xs, 10), _pct(xs, 50), _pct(xs, 90)))
    if labels:
        whys = Counter(r.get("why") for r in labels)
        named = sum(1 for r in labels if r.get("named"))
        print("  ระบุผู้พูดแล้ว %d ช่วง: %s | มีชื่อ %d"
              % (len(labels), ", ".join("%s %d" % kv for kv in whys.most_common()), named))
    if away:
        print("  วางสายเพราะไม่มีใครหน้ากล้อง %d ครั้ง" % len(away))
    noise = [r for r in rows if r.get("event") == "noise_reply_dropped"]
    if noise:
        # NOISE_REPLY_NEAR_RMS (owner 29 Oct): her reply to a quiet sound that
        # made 0-4 letters of caption, not played
        xs = [r["rms"] for r in noise if isinstance(r.get("rms"), (int, float))]
        print("  ไม่ตอบเสียงเบาที่ฟังไม่ออก %d ครั้ง%s" % (
            len(noise), "  (rms p50 %.3g, สูงสุด %.3g)" % (_pct(xs, 50), max(xs)) if xs else ""))
    if stalled:
        # The speaker note held a turn open (gemini._note_stall_guard): that
        # reply came NOTE_STALL_S late and notes are off until restart.
        print("  !! บันทึกผู้พูดทำให้หุ่นค้างไม่ตอบ %d ครั้ง — ปิด FOCUS_SPEAKER_LABELS"
              " (ระบบหยุดส่งเองจนรีสตาร์ต)" % len(stalled))
    print("  → ไม่ฟังเพราะ 'no face in front' ทั้งที่มีคนคุยอยู่จริง = FOCUS_FACE_MIN_PX/YAW แน่นเกิน"
          " ดู px/yaw ตอนเปิดเทียบ")
    _engagement(rows)


def stories(rows: list[dict]) -> None:
    """The robot face's stories in real conversations (`story_report`, one
    per story): how far each picture went up after the word that cues it
    (`lag_ms`, + = after the word; her voice's own text/audio ratio places
    the word), and the frame times with her voice and the 3D Emma running.
    The answer to "does the picture follow her voice on the robot" that a
    silent screen recording cannot give (reviewer, 13 Oct)."""
    reports = [r for r in rows if r.get("event") == "story_report"]
    if not reports:
        return
    section("Stories on the robot face (story_report)")
    lags = [v for r in reports for v in (r.get("lag_ms") or []) if isinstance(v, (int, float))]
    cut = sum(1 for r in reports if r.get("interrupted"))
    print(f"  stories: {len(reports)} ({cut} cut short by the guest)")
    if lags:
        print(f"  picture after its word: p10 {_pct(lags, 10):.0f} ms  p50 {_pct(lags, 50):.0f} ms  "
              f"p90 {_pct(lags, 90):.0f} ms  (n={len(lags)}; under 0 = before the word)")
    fps = [r["fps"] for r in reports if isinstance(r.get("fps"), (int, float))]
    p95 = [r["p95_ms"] for r in reports if isinstance(r.get("p95_ms"), (int, float))]
    longs = sum(int(r.get("long_frames") or 0) for r in reports)
    if fps:
        p95_text = f"{_pct(p95, 50):.1f} ms" if p95 else "—"
        print(f"  fps: p50 {_pct(fps, 50):.1f}  worst {min(fps):.1f}  · frame p95 p50 {p95_text}"
              f"  · frames over 50 ms: {longs}")
    by = Counter(r.get("topic") for r in reports)
    print("  most shown: " + ", ".join(f"{k} {v}" for k, v in by.most_common(8)))


def head(rows: list[dict]) -> None:
    """The real head (HEAD_FOLLOW, 31 Oct, round 2): what the sweep measured, what the
    loop sent and what the app refused. The bench reads its first run here."""
    cal = [r for r in rows if r.get("event") == "head_calibration"]
    looks = [r for r in rows if r.get("event") == "head_look"]
    fenced = [r for r in rows if r.get("event") == "face_link_refused" and r.get("why") == "event_walk"]
    lidar_rows = [r for r in rows if r.get("event") in ("head_lidar_check", "lidar_people")]
    if not (cal or looks or fenced or lidar_rows):
        return
    section("Head follow (head_calibration / head_look)")
    for r in cal[-3:]:
        if r.get("ok"):
            print("  sweep OK: pan %+.1f deg / tilt %+.1f deg at 100%% (texture %s)%s" % (
                r.get("pan_deg_per_100") or 0, r.get("tilt_deg_per_100") or 0, r.get("texture"),
                "  <- '+' turns the head to the robot's RIGHT on this robot"
                if (r.get("pan_deg_per_100") or 0) < 0 else ""))
        else:
            print("  sweep FAILED: %s  shifts %s" % (r.get("reason"), r.get("shifts")))
    by = Counter(r.get("why") for r in looks)
    if looks:
        print("  looks by why: " + ", ".join(f"{k} {v}" for k, v in by.most_common()))
    refused = Counter(r.get("reason") for r in looks if r.get("why") == "refused")
    if refused:
        print("  refused by the app: " + ", ".join(f"{k} {v}" for k, v in refused.most_common())
              + "  (busy = a gesture/group held the head; disabled = the voice-gesture switch is off)")
    off = Counter(r.get("reason") for r in looks if r.get("why") in ("off", "calibrate"))
    if off:
        print("  stopped following: " + ", ".join(f"{k} {v}" for k, v in off.most_common()))
    if fenced:
        print("  arm commands refused during the event host's walk: %d" % len(fenced))
    checks = [r for r in rows if r.get("event") == "head_lidar_check"]
    if checks:
        r = checks[-1]
        if r.get("trusted"):
            print("  lidar check HOLDS (%s pairs): lidar = %.2f x camera %+.1f deg, off by %.1f deg typically"
                  " -> camera ~%s deg wide%s" % (
                      r.get("n"), r.get("a") or 0, r.get("b") or 0, r.get("mad_deg") or 0, r.get("hfov_deg"),
                      "  (lidar angles run the other way)" if (r.get("a") or 0) < 0 else ""))
        else:
            print("  lidar check not (yet) holding: %s (%s pairs, spread %s deg, head_ratio %s)" % (
                r.get("why"), r.get("n"), r.get("spread_deg"), r.get("head_ratio")))
    seen = [r for r in rows if r.get("event") == "lidar_people"]
    turned = sum(1 for r in looks if r.get("why") == "lidar")
    if seen or turned:
        print("  lidar people rows %d · head turned toward a lidar person %d times" % (len(seen), turned))


def array_mic(rows: list[dict]) -> None:
    """ARRAY_MIC (2 Nov, docs/emma-array-mic-2026-11-02.md): which calls heard
    with the mic array alone, every hand-back to the page and why, and the
    levels every threshold saw on each source -- the robot A/B that sets
    ARRAY_AUDIO_GAIN reads its numbers here (numbers only, like every row)."""
    sole = {r.get("session_id") for r in rows
            if r.get("event") == "array_source" and r.get("why") == "sole"} - {None}
    backs = [r for r in rows if r.get("event") == "array_mic_handback"]
    states = [r for r in rows if r.get("event") == "mic_state" and str(r.get("why") or "").startswith("array_")]
    if not (sole or backs or states):
        return
    section("Array mic (ARRAY_MIC: the app holds the mic array alone)")
    handed = {r.get("session_id") for r in backs if r.get("phase") == "call"}
    print("  calls on the array alone: %d (handed back mid-call: %d)" % (len(sole), len(sole & handed)))
    by = Counter("%s/%s:%s" % (r.get("phase"), r.get("scope") or "?", r.get("why")) for r in backs)
    if by:
        print("  hand-backs: " + ", ".join(f"{k} {v}" for k, v in by.most_common())
              + "  (phase start = not live at ready, call = went quiet for ARRAY_MIC_DEAF_S;"
                " scope call = that call only, run = until the app restarts)")
    dead = [float(r["dead_ms"]) for r in backs if isinstance(r.get("dead_ms"), (int, float))]
    if dead:
        print("  silent before a mid-call hand-back: p50 %.0f ms, max %.0f ms" % (_pct(dead, 50), max(dead)))
    page = Counter("%s:%s" % (r.get("why"), r.get("state")) for r in states)
    if page:
        print("  the page: " + ", ".join(f"{k} {v}" for k, v in page.most_common())
              + "  (timeout = the app never said its capture ended; no_clock = the silent source never ticked)")
    gaps = [float(r["max_gap_ms"]) for r in rows
            if r.get("event") == "array_report" and r.get("sole") and isinstance(r.get("max_gap_ms"), (int, float))]
    if gaps:
        print("  longest chunk gap per report: p50 %.0f ms, p95 %.0f ms, max %.0f ms (ARRAY_MIC_DEAF_S must sit well above)"
              % (_pct(gaps, 50), _pct(gaps, 95), max(gaps)))
    sole_rows = [r for r in rows if r.get("event") == "array_report" and r.get("sole")]
    held = sum(int(r.get("stall_held") or 0) for r in sole_rows)
    stalls = [float(r["loop_stall_ms"]) for r in sole_rows if isinstance(r.get("loop_stall_ms"), (int, float))
              and r["loop_stall_ms"] > 0]
    if held or stalls:
        print("  event-loop stalls: %d windows with one (longest %.0f ms) · hand-backs deferred by a stall: %d"
              % (len(stalls), max(stalls) if stalls else 0, held))
    # The A/B: the same thresholds, two sources. Sessions handed back mid-call
    # are in neither column (half of each).
    on_array = sole - handed
    for name, pick in (("array", lambda sid: sid in on_array),
                       ("page ", lambda sid: sid not in sole and sid not in handed)):
        parts = []
        for event, field in (("focus_open", "rms"), ("vad_floor", "rms"), ("mic_report", "avg")):
            xs = [float(r[field]) for r in rows if r.get("event") == event and pick(r.get("session_id"))
                  and isinstance(r.get(field), (int, float))]
            if xs:
                parts.append("%s %s p10/p50/p90 %.4f/%.4f/%.4f (n %d)" % (
                    event, field, _pct(xs, 10), _pct(xs, 50), _pct(xs, 90), len(xs)))
        if parts:
            print("  %s: %s" % (name, " · ".join(parts)))


def _engagement(rows: list[dict]) -> None:
    """The reception-robot pieces (docs/listening-focus-2026-10-09.md,
    "Engagement"): the in-call name, invitations, full-mode releases, the
    goodbye — and the two numbers the robot's own log must supply: what the
    in-call ear costs on its CPU, and how long a guest goes missing from the
    camera while still there (the goodbye's bar)."""
    def of(event):
        return [r for r in rows if r.get("event") == event]

    wake, cpu, gaps = of("focus_wake"), of("focus_wake_cpu"), of("focus_gap")
    releases, goodbyes = of("focus_release"), of("focus_goodbye")
    said = Counter((r.get("source"), bool(r.get("sent"))) for r in of("announce")
                   if r.get("source") in ("focus_invite", "focus_goodbye"))
    if not (wake or cpu or gaps or releases or goodbyes or said):
        return
    if wake:
        own = sum(1 for r in wake if r.get("ignored") == "own_voice")
        out = sum(1 for r in wake if r.get("in_view") is False)
        print("  ได้ยินชื่อระหว่างสาย %d ครั้ง (นอกกล้อง %d, เสียงตัวเอง %d)"
              % (len(wake), out, own))
    ms = [r["ms_per_s"] for r in cpu if isinstance(r.get("ms_per_s"), (int, float))]
    if ms:
        print("  หูฟังชื่อในสาย ใช้ CPU p50 %.1f / p90 %.1f ms ต่อเสียง 1 วินาที (%d สาย)"
              % (_pct(ms, 50), _pct(ms, 90), len(ms)))
    for source, label in (("focus_invite", "เชิญมายืนหน้าหุ่น"), ("focus_goodbye", "กล่าวลา")):
        if said[(source, True)] or said[(source, False)]:
            print("  %s: พูด %d / ไม่พูด (สถานการณ์เปลี่ยน) %d"
                  % (label, said[(source, True)], said[(source, False)]))
    if releases:
        print("  โหมด full: คนหลักเดินออกแล้วปล่อยให้คนอื่นคุย %d ครั้ง" % len(releases))
    ended = sum(1 for r in goodbyes if r.get("ended"))
    if goodbyes:
        print("  กล่าวลาแล้ววางสาย %d / กลับมาคุยต่อ %d" % (ended, len(goodbyes) - ended))
    xs = [r["gap_s"] for r in gaps if isinstance(r.get("gap_s"), (int, float))]
    if xs:
        print("  หน้าหายจากกล้องแล้วกลับมา: n=%d  p50 %.1f  p90 %.1f  สูงสุด %.1f วินาที"
              % (len(xs), _pct(xs, 50), _pct(xs, 90), max(xs)))
        print("  → FOCUS_GOODBYE_AFTER_S ต้องสูงกว่าช่วงที่คนเดิมยังยืนอยู่ (ดู p90)")


def main() -> int:
    # Thai on a stdout that is not UTF-8 — a pipe, a redirect, a cp1252
    # console — raised UnicodeEncodeError on the first header and the
    # script printed nothing at all. Same fix as audit_data_sources.py.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="*", help="log files (default: today's)")
    ap.add_argument("--days", type=int, default=1, help="how many days back")
    ap.add_argument("--verbose", action="store_true", help="list every problem row")
    ap.add_argument("--save", action="store_true",
                    help="write counts and topics to data/log-summaries/ so the "
                         "analysis outlives TURN_LOG_KEEP_DAYS")
    args = ap.parse_args()

    paths = resolve(args)
    rows = load(paths)
    if not rows:
        print("ไม่พบ log — รันเซิร์ฟเวอร์แล้วคุยกับหุ่นยนต์สักรอบก่อน")
        print("(ที่หา: %s)" % ", ".join(str(p) for p in paths))
        return 1

    internal = [r for r in rows if is_internal(r)]
    rows = [r for r in rows if not is_internal(r)]
    kinds = Counter(r.get("event") for r in rows)
    sessions = kinds.get("session_start", 0)
    print("อ่าน %d บรรทัด จาก %d ไฟล์ / %d เซสชัน"
          % (len(rows), len({r["_file"] for r in rows}), sessions))
    if internal:
        print("  ไม่นับ %d บรรทัดที่เครื่องทำเอง (เช่น warm-up การค้นตอนบูต)" % len(internal))
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
        print("    เป็นตัวเลขไว้คุยกับฝ่ายขายว่าควรกรอกไฟล์ facts ของโครงการแค่ไหน")

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

    latency(rows)
    focus(rows)
    stories(rows)
    head(rows)
    array_mic(rows)

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

    if args.save:
        _save_summary(rows)
    return 0


def _save_summary(rows: list[dict]) -> None:
    """Keep the findings, not the sentences.

    The reason this exists: raw transcripts are deleted after
    TURN_LOG_KEEP_DAYS because they are recordings of members of the public,
    and the obvious objection is that deleting them destroys the thing they
    were collected for. It doesn't have to. Almost every question worth asking
    of this log — what do guests ask about, what can the robot not answer,
    which languages turn up, which slides are missing — is answered by counts.

    So counts are what gets kept. Deliberately **no free text**: no `heard`,
    no `said`, no query strings. A summary that quietly carried the questions
    verbatim would be the archive again under a different name, and it would
    be the version nobody remembers to check.
    """
    import json
    from collections import Counter

    out_dir = ROOT / "data" / "log-summaries"
    out_dir.mkdir(parents=True, exist_ok=True)

    days = sorted({r["_file"].removesuffix(".jsonl") for r in rows})
    name = "%s_to_%s.json" % (days[0], days[-1]) if days else "empty.json"

    kinds = Counter(r.get("event") for r in rows)
    tools = Counter(r.get("name") for r in rows if r.get("event") == "tool")
    lookups = [r for r in rows if r.get("event") == "lookup"]
    summary = {
        "days": days,
        "sessions": kinds.get("session_start", 0),
        "guest_utterances": kinds.get("heard", 0),
        "tool_calls": dict(tools),
        "lookups": len(lookups),
        "lookups_that_showed_nothing": sum(1 for r in lookups if not r.get("showed")),
        # The number that justifies filling in condo_facts.json, kept as a
        # number rather than as the questions themselves.
        "commercial_questions": kinds.get("commercial_question", 0),
        "screen_changes": kinds.get("screen", 0),
        "prints": kinds.get("print", 0),
        "remote_presses": kinds.get("remote", 0),
        # Listening focus, as counts: reasons are fixed machine strings.
        "focus_opens": kinds.get("focus_open", 0),
        "noise_replies_dropped": kinds.get("noise_reply_dropped", 0),
        "focus_denies": dict(Counter(r.get("reason") for r in rows
                                     if r.get("event") == "focus_deny")),
    }
    path = out_dir / name
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print("บันทึกสรุปไว้ที่ %s" % path)
    print("  (เก็บเฉพาะตัวเลข ไม่มีคำพูดของลูกค้าเลย จึงเก็บไว้ได้ถาวร)")


if __name__ == "__main__":
    raise SystemExit(main())
