"""scripts/analyze_log.py: the numbers must be the customers', and the
script must print them on whatever terminal it is run from.

Every row here is synthetic. The real logs hold strangers' speech, so
nothing from data/logs/ is copied into a fixture.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _al():
    path = ROOT / "scripts" / "analyze_log.py"
    spec = importlib.util.spec_from_file_location("analyze_log_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_log(tmp_path, rows):
    path = tmp_path / "2026-01-01.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                    encoding="utf-8")
    return path


WARM_UP = {"t": "08:00:00", "event": "lookup", "query": "warm up",
           "showed": None, "found": False, "reason": "nothing matched"}
MARKED = {**WARM_UP, "origin": "startup_warm_up"}
GUEST = {"t": "10:00:00", "event": "lookup", "session_id": "s1",
         "query": "ห้องทดสอบอยู่ชั้นไหน", "showed": None, "reason": "nothing matched"}


def test_the_cli_prints_on_a_stdout_that_is_not_utf8(tmp_path):
    """Piped, redirected, or a cp1252 console: the first Thai header raised
    UnicodeEncodeError and the script printed nothing. PYTHONIOENCODING
    forces the narrow encoding on every OS, so this cannot pass merely
    because the machine running it happens to default to UTF-8."""
    log = _write_log(tmp_path, [{"t": "09:00:00", "event": "session_start",
                                 "session_id": "s1"}, GUEST])
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    env.pop("PYTHONUTF8", None)
    result = subprocess.run(
        [sys.executable, "scripts/analyze_log.py", str(log)],
        cwd=ROOT, env=env, capture_output=True, encoding="utf-8",
        errors="replace", check=False, timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert "อ่าน" in result.stdout


def test_the_startup_warm_up_is_not_a_customer():
    """131 of 165 "lookups" over 2026-09-24..10-06 were the boot-time call
    that loads the search model, and "warm up" topped the list of
    questions customers asked. Marked rows and the unmarked rows of older
    logs are both the machine's; a guest's lookup — even one with no
    session, like the text chat — is not."""
    al = _al()
    assert al.is_internal(MARKED)
    assert al.is_internal(WARM_UP), "logs written before the mark existed"
    assert not al.is_internal(GUEST)
    assert not al.is_internal({**GUEST, "session_id": None})
    assert not al.is_internal({**WARM_UP, "session_id": "s1"}), \
        "a guest who literally said 'warm up' is still a guest"


def test_the_report_counts_only_customer_lookups(tmp_path, monkeypatch, capsys):
    al = _al()
    log = _write_log(tmp_path, [MARKED, WARM_UP, WARM_UP, GUEST])
    monkeypatch.setattr(sys, "argv", ["analyze_log.py", str(log)])
    assert al.main() == 0
    out = capsys.readouterr().out
    assert "ค้นทั้งหมด 1 ครั้ง" in out
    assert "warm up" not in out
    assert "ไม่นับ 3 บรรทัด" in out


def test_the_report_splits_the_wait_into_its_halves(tmp_path, monkeypatch, capsys):
    """The wait after a guest stops talking has two halves tuned in
    different places — the VAD hangover (.env) and the model — so the
    report gives the model's half, its spread by thinking tokens, and how
    often the hangover was already too short."""
    al = _al()
    rows = []
    for i in range(12):
        rows.append({"event": "metric_first_audio", "turn_id": "t%d" % i,
                     "end_signal_to_audio_ms": 1000 + 100 * i,
                     "end_signal_source": "gemini_local_vad_decision"})
        rows.append({"event": "metric_usage", "observed_turn_id": "t%d" % i,
                     "thoughts_token_count": 10 * i})
    rows.append({"event": "metric_vad_reopen", "gap_ms": 400, "silence_ms": 900})
    log = _write_log(tmp_path, rows)
    monkeypatch.setattr(sys, "argv", ["analyze_log.py", str(log)])
    assert al.main() == 0
    out = capsys.readouterr().out
    assert "n=12  p50 1550 ms" in out
    assert "thinking tokens <= 55" in out
    assert "พูดต่อทั้งที่ Emma ยังไม่ได้ตอบ: 1 ครั้ง" in out
    assert "VAD_SILENCE_MS=900" in out


def test_turnlog_marks_rows_the_machine_made(tmp_path, monkeypatch):
    """The mark is a property of the context, not of each call site: every
    row the warm-up's lookup writes carries it, and nothing else does."""
    from app import turnlog
    from app.config import settings

    monkeypatch.setattr(settings, "turn_log", True)
    monkeypatch.setattr(settings, "turn_log_dir", str(tmp_path))
    monkeypatch.setattr(settings, "turn_log_keep_days", 0)
    try:
        turnlog.record("lookup", query="guest")
        token = turnlog.origin.set("startup_warm_up")
        try:
            turnlog.record("lookup", query="warm up")
        finally:
            turnlog.origin.reset(token)
    finally:
        turnlog.close()
    rows = [json.loads(line) for f in tmp_path.glob("*.jsonl")
            for line in f.read_text(encoding="utf-8").splitlines()]
    assert [r.get("origin") for r in rows] == [None, "startup_warm_up"]


def test_the_startup_warm_up_marks_what_it_logs(monkeypatch):
    """The source of the mark: the warm-up runs the customer lookup in a
    worker thread, and the thread's rows must still say who asked."""
    from app import main, turnlog
    from app.tools import knowledge

    seen = []
    monkeypatch.setattr(knowledge, "search_condo_info",
                        lambda query: seen.append(turnlog.origin.get()))

    async def boot():
        await main._warm_search()
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        await asyncio.gather(*pending)

    asyncio.run(boot())
    assert seen == ["startup_warm_up"]
    assert turnlog.origin.get() is None, "the mark must not leak past the warm-up"


def test_the_report_has_a_listening_focus_section(tmp_path, monkeypatch, capsys):
    """Listening focus (docs/listening-focus-2026-10-09.md): opens and
    denies by reason, hour by hour, and the px/yaw/rms the thresholds are
    tuned from — numbers only."""
    al = _al()
    rows = [
        {"t": "10:00:02", "event": "focus_open", "reason": "ok", "faces": 1,
         "px": 120, "yaw": 5.0, "rms": 0.05},
        {"t": "10:00:05", "event": "focus_deny", "reason": "no face in front",
         "faces": 0, "px": 40, "rms": 0.02},
        {"t": "11:00:05", "event": "focus_deny", "reason": "no camera", "faces": 0},
        {"t": "11:00:06", "event": "focus_label", "why": "mouth", "ordinal": 1,
         "named": True},
        {"t": "11:00:09", "event": "idle_timeout", "quiet_s": 31, "why": "no_face"},
    ]
    log = _write_log(tmp_path, rows)
    monkeypatch.setattr(sys, "argv", ["analyze_log.py", str(log)])
    assert al.main() == 0
    out = capsys.readouterr().out
    assert "เปิดให้ฟัง 1 ช่วงพูด / ไม่ฟัง 2 ครั้ง" in out
    assert "no face in front" in out and "no camera" in out
    assert "10:00     1 /    1" in out and "11:00     0 /    1" in out
    assert "วางสายเพราะไม่มีใครหน้ากล้อง 1 ครั้ง" in out
    assert "มีชื่อ 1" in out
    assert "ค้างไม่ตอบ" not in out


def test_a_stalled_speaker_note_is_shouted_in_the_report(tmp_path, monkeypatch, capsys):
    """gemini._note_stall_guard: a labelled turn got no reply. Alone in a log
    it must still open the section — it is the one row the owner must see."""
    al = _al()
    log = _write_log(tmp_path, [{"t": "12:00:00", "event": "focus_note_stalled", "wait_s": 6.0}])
    monkeypatch.setattr(sys, "argv", ["analyze_log.py", str(log)])
    assert al.main() == 0
    assert "บันทึกผู้พูดทำให้หุ่นค้างไม่ตอบ 1 ครั้ง" in capsys.readouterr().out


def test_the_head_section_reads_the_first_bench_run(capsys):
    al = _al()
    rows = [
        {"event": "head_calibration", "ok": True, "pan_deg_per_100": -18.4, "tilt_deg_per_100": 21.0,
         "texture": 9.1, "shifts": {}},
        {"event": "head_look", "why": "face", "pan": 30, "tilt": 0},
        {"event": "head_look", "why": "refused", "reason": "busy", "pan": 40, "tilt": 0},
        {"event": "head_look", "why": "off", "reason": "app_disabled", "pan": 0, "tilt": 0},
        {"event": "face_link_refused", "command": "body", "action": "nod", "why": "event_walk"},
    ]
    al.head(rows)
    out = capsys.readouterr().out
    assert "sweep OK: pan -18.4 deg / tilt +21.0 deg" in out and "RIGHT" in out
    assert "refused by the app: busy 1" in out
    assert "stopped following: app_disabled 1" in out
    assert "event host's walk: 1" in out
    al.head([{"event": "lookup"}])
    assert capsys.readouterr().out == "", "no head rows, no section"


def test_the_head_section_reads_the_lidar_check(capsys):
    al = _al()
    al.head([{"event": "head_lidar_check", "n": 30, "a": -0.82, "b": 2.0, "mad_deg": 1.1, "hfov_deg": 60.0,
              "trusted": True},
             {"event": "lidar_people", "n": 1, "nearest_deg": 12.0, "nearest_m": 0.8},
             {"event": "head_look", "why": "lidar", "pan": 40, "tilt": 30}])
    out = capsys.readouterr().out
    assert "lidar check HOLDS (30 pairs)" in out and "~60.0 deg wide" in out and "other way" in out
    assert "turned toward a lidar person 1 times" in out
    al.head([{"event": "head_lidar_check", "n": 20, "trusted": False, "why": "disagree", "spread_deg": 8.0}])
    assert "not (yet) holding: disagree" in capsys.readouterr().out
