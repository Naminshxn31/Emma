"""Tests for the turn log.

Two things make this worth testing carefully rather than trusting.

It runs on every single turn, in front of customers, and its whole contract is
that it never gets in the way: a full disk, a read-only volume, a path that
doesn't exist — none of those may take the robot down mid-sentence. Code whose
job is "fail quietly" is exactly the code where a bug hides, because nothing
downstream ever complains.

And it was the least covered file in the project at 37%, for a reason worth
noticing: `conftest.py` disables logging for the whole suite so fixtures don't
pollute the operator's real log. That fix left the write path untestable by
accident — every test ran the "logging is off" branch and nothing else. These
tests opt back in against tmp_path, which is the distinction that was missing:
off for *incidental* logging, on for tests that are actually about it.
"""
from __future__ import annotations

import json

import pytest

from app import turnlog
from app.config import settings


@pytest.fixture
def logging_on(monkeypatch, tmp_path):
    """Turn logging back on, pointed somewhere disposable."""
    monkeypatch.setattr(settings, "turn_log", True)
    monkeypatch.setattr(settings, "turn_log_dir", str(tmp_path))
    turnlog.close()                     # drop any handle from an earlier test
    monkeypatch.setattr(turnlog, "_path", None)
    monkeypatch.setattr(turnlog, "_warned", False)
    yield tmp_path
    turnlog.close()


def read(tmp_path) -> list[dict]:
    """Rows written so far. No file at all is a legitimate result — several
    tests below are about things that must *not* be logged."""
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) <= 1, "expected at most one day file, got %s" % files
    if not files:
        return []
    return [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]


# ==================== it writes what it says it writes ====================


def test_a_turn_is_one_line_of_json(logging_on):
    turnlog.record("heard", text="ฟิตเนสมีอะไรบ้าง")
    turnlog.record("lookup", query="ฟิตเนส", showed="ew-024")

    rows = read(logging_on)
    assert [r["event"] for r in rows] == ["heard", "lookup"]
    assert rows[0]["text"] == "ฟิตเนสมีอะไรบ้าง"
    assert rows[1]["showed"] == "ew-024"
    assert all("t" in r for r in rows), "no timestamp — useless for correlating"


def test_thai_is_stored_readable_not_escaped(logging_on):
    """`ensure_ascii=False`. A log nobody can read at a glance doesn't get
    read, and \\u0e1f\\u0e34\\u0e15\\u0e40\\u0e19\\u0e2a is not something an
    operator will sit and decode."""
    turnlog.record("heard", text="ฟิตเนส")
    raw = next(logging_on.glob("*.jsonl")).read_text(encoding="utf-8")
    assert "ฟิตเนส" in raw


def test_empty_fields_are_dropped_but_falsy_ones_are_kept(logging_on):
    """`None` means "not applicable" and is noise. Zero is a *measurement* —
    `audio_lead_s: 0.0` is the log saying the picture and the voice were in
    step, which is the single most useful line in the file when they aren't.
    Dropping it with a truthiness test would silently delete the good news.
    """
    turnlog.record("screen", slide="ew-001", audio_lead_s=0.0, position=None)

    row = read(logging_on)[0]
    assert row["audio_lead_s"] == 0.0, "a zero measurement was thrown away"
    assert "position" not in row


# ==================== it must never take the robot down ====================


def test_an_unwritable_directory_does_not_raise(monkeypatch, tmp_path):
    """A read-only volume in the gallery must cost the log, not the robot."""
    blocked = tmp_path / "nope"
    blocked.write_text("i am a file, not a directory", encoding="utf-8")

    monkeypatch.setattr(settings, "turn_log", True)
    monkeypatch.setattr(settings, "turn_log_dir", str(blocked / "logs"))
    monkeypatch.setattr(turnlog, "_fh", None)
    monkeypatch.setattr(turnlog, "_path", None)
    monkeypatch.setattr(turnlog, "_warned", False)

    turnlog.record("heard", text="สวัสดี")      # must not raise


def test_it_complains_once_not_once_per_turn(monkeypatch, tmp_path, caplog):
    """A warning per turn would itself become the problem it is reporting —
    an unwritable disk would fill the console with the same line all day and
    bury the errors worth seeing."""
    blocked = tmp_path / "nope"
    blocked.write_text("not a directory", encoding="utf-8")

    monkeypatch.setattr(settings, "turn_log", True)
    monkeypatch.setattr(settings, "turn_log_dir", str(blocked / "logs"))
    monkeypatch.setattr(turnlog, "_fh", None)
    monkeypatch.setattr(turnlog, "_path", None)
    monkeypatch.setattr(turnlog, "_warned", False)

    with caplog.at_level("WARNING", logger="condo_voice.turnlog"):
        for _ in range(20):
            turnlog.record("heard", text="สวัสดี")

    complaints = [r for r in caplog.records if "turn log" in r.getMessage()]
    assert len(complaints) == 1, "warned %d times" % len(complaints)


def test_a_write_that_fails_midway_is_swallowed(logging_on, monkeypatch):
    """Instrumentation is not the job. If the handle dies under us — disk
    full, file removed — the turn still has to finish."""
    turnlog.record("heard", text="first")

    class Broken:
        def write(self, _):
            raise OSError("no space left on device")

        def flush(self):
            raise OSError("no space left on device")

    monkeypatch.setattr(turnlog, "_fh", Broken())
    monkeypatch.setattr(turnlog, "_path", turnlog._path)
    turnlog.record("heard", text="second")       # must not raise


def test_nothing_is_written_when_logging_is_off(monkeypatch, tmp_path):
    """The privacy switch. TURN_LOG=false has to mean no file at all, not an
    empty one — this records what visitors say out loud."""
    monkeypatch.setattr(settings, "turn_log", False)
    monkeypatch.setattr(settings, "turn_log_dir", str(tmp_path))
    monkeypatch.setattr(turnlog, "_fh", None)
    monkeypatch.setattr(turnlog, "_path", None)

    turnlog.record("heard", text="ความลับ")
    assert list(tmp_path.glob("*.jsonl")) == []


# ==================== the day boundary ====================


def test_it_rolls_over_to_a_new_file_the_next_day(logging_on, monkeypatch):
    """A demo that runs past midnight must not keep appending to yesterday.

    The handle is cached, so without the path check the file would only ever
    be reopened on restart — and a robot left running over a weekend would put
    three days in one file named after the Friday.
    """
    turnlog.record("heard", text="วันนี้")

    real_strftime = turnlog.time.strftime

    def tomorrow(fmt, *a):
        return "2099-01-01" if fmt == "%Y-%m-%d" else real_strftime(fmt, *a)

    monkeypatch.setattr(turnlog.time, "strftime", tomorrow)
    turnlog.record("heard", text="พรุ่งนี้")

    files = sorted(p.name for p in logging_on.glob("*.jsonl"))
    assert len(files) == 2, "kept writing into the same day: %s" % files
    assert "2099-01-01.jsonl" in files


def test_close_is_safe_to_call_twice(logging_on):
    turnlog.record("heard", text="x")
    turnlog.close()
    turnlog.close()          # must not raise


# ==================== wired into the real event pump ====================


def test_a_slide_change_records_the_audio_lead_at_that_instant(logging_on, monkeypatch):
    """The single most useful line in the file, and the one that is easiest to
    get subtly wrong.

    The lead has to be read *while* the picture is changing. Reading it a
    moment later — in a background task, or when the log is analysed — gives
    a number that has already drained toward zero, and the log would then say
    every slide was perfectly in sync while the operator watched it not be.
    """
    import asyncio

    from app import display
    from app.providers.base import ProviderEvent
    from app.session import VoiceSession
    from app.tools import slides

    sent = []

    class FakeWS:
        async def send_text(self, text):
            sent.append(text)

    class FakeProvider:
        output_sample_rate = 24000

        def events(self):
            async def gen():
                yield ProviderEvent(kind="tool_result", text="next_slide",
                                    data={"slide": {"id": "ew-002"}})
            return gen()

    sess = VoiceSession.__new__(VoiceSession)
    sess.ws = FakeWS()
    sess.provider = FakeProvider()
    sess._sent_audio_ms = 0.0
    sess._spoke_this_turn = False
    sess._nudge_index = None
    sess._nudge_count = 0

    slides.reset_state()
    slides.STATE["current"] = {"id": "ew-002", "position": 2}
    display.set_audio_lead(6400)          # six and a bit seconds still queued

    asyncio.run(sess._provider_to_browser())
    display.set_audio_lead(0)

    screens = [r for r in read(logging_on) if r["event"] == "screen"]
    assert screens, "a slide changed and nothing was recorded"
    assert screens[0]["slide"] == "ew-002"
    assert screens[0]["tool"] == "next_slide"
    assert screens[0]["audio_lead_s"] >= 6.0, (
        "recorded %.1fs — the lead was read after it had drained, which makes "
        "the log claim everything was in sync" % screens[0]["audio_lead_s"]
    )


def test_a_tool_that_changes_nothing_on_screen_is_not_logged_as_a_slide_change(logging_on):
    """`set_lights` must not appear in the picture-vs-voice numbers. Padding
    that count with tool calls that never touched the screen would make the
    audio-lead statistics meaningless."""
    import asyncio

    from app.providers.base import ProviderEvent
    from app.session import VoiceSession
    from app.tools import slides

    class FakeWS:
        async def send_text(self, text): pass

    class FakeProvider:
        output_sample_rate = 24000

        def events(self):
            async def gen():
                yield ProviderEvent(kind="tool_result", text="set_lights",
                                    data={"ok": True, "state": "on"})
            return gen()

    sess = VoiceSession.__new__(VoiceSession)
    sess.ws = FakeWS()
    sess.provider = FakeProvider()
    sess._sent_audio_ms = 0.0
    sess._spoke_this_turn = False
    slides.reset_state()

    asyncio.run(sess._provider_to_browser())

    assert [r for r in read(logging_on) if r["event"] == "screen"] == []


def test_a_failed_tool_leaves_its_error_in_the_log(logging_on):
    """2026-08-26 16:26, live: play_youtube failed ("มีข้อผิดพลาดนิดหน่อยค่ะ"),
    the identical call worked from a fresh process minutes later, and the
    log held only the tool's name — a transient failure with no evidence
    anywhere is the WAKE_DEBUG hole again. The error text is written by the
    tool, never by the guest, so keeping it breaks no privacy rule. Success
    results stay unlogged: 59 tools' worth of ok:true is noise."""
    import asyncio

    from app.providers.base import ProviderEvent
    from app.session import VoiceSession

    class FakeWS:
        async def send_text(self, text): pass

    class FakeProvider:
        output_sample_rate = 24000

        def events(self):
            async def gen():
                yield ProviderEvent(kind="tool_result", text="play_youtube",
                                    data={"ok": False,
                                          "error": "window handle is dead"})
                yield ProviderEvent(kind="tool_result", text="set_lights",
                                    data={"ok": True, "state": "on"})
            return gen()

    sess = VoiceSession.__new__(VoiceSession)
    sess.ws = FakeWS()
    sess.provider = FakeProvider()
    sess._sent_audio_ms = 0.0
    sess._spoke_this_turn = False
    from app.tools import slides
    slides.reset_state()

    asyncio.run(sess._provider_to_browser())

    errors = [r for r in read(logging_on) if r["event"] == "tool_error"]
    assert len(errors) == 1, "exactly the failure, not the success"
    assert errors[0]["name"] == "play_youtube"
    assert "window handle is dead" in errors[0]["error"]


def test_old_logs_are_forgotten(monkeypatch, tmp_path):
    """These are recordings of members of the public. Nothing was deleting them.

    Keeping them was never decided — it was what happened when no code said
    otherwise, which is the worst way for a retention policy to come about.
    """
    import time

    from app import turnlog
    from app.config import settings

    monkeypatch.setattr(settings, "turn_log", True)
    monkeypatch.setattr(settings, "turn_log_dir", str(tmp_path))
    monkeypatch.setattr(settings, "turn_log_keep_days", 30)
    monkeypatch.setattr(turnlog, "_fh", None)
    monkeypatch.setattr(turnlog, "_path", None)

    old = tmp_path / "2020-01-01.jsonl"
    recent = tmp_path / (time.strftime("%Y-%m-%d") + ".jsonl")
    old.write_text('{"event":"heard","text":"เบอร์ผม 081-234-5678"}\n', encoding="utf-8")
    recent.write_text('{"event":"heard"}\n', encoding="utf-8")

    turnlog.record("session_start")
    turnlog.close()

    assert not old.exists(), "a five-year-old transcript is still on disk"
    assert recent.exists(), "deleted a log inside the retention window"


def test_keep_days_zero_keeps_everything(monkeypatch, tmp_path):
    """An escape hatch for anyone with a reason — but it has to be chosen."""
    from app import turnlog
    from app.config import settings

    monkeypatch.setattr(settings, "turn_log", True)
    monkeypatch.setattr(settings, "turn_log_dir", str(tmp_path))
    monkeypatch.setattr(settings, "turn_log_keep_days", 0)
    monkeypatch.setattr(turnlog, "_fh", None)
    monkeypatch.setattr(turnlog, "_path", None)

    old = tmp_path / "2020-01-01.jsonl"
    old.write_text("{}\n", encoding="utf-8")
    turnlog.record("session_start")
    turnlog.close()
    assert old.exists()


def test_retention_uses_the_filename_not_the_mtime(monkeypatch, tmp_path):
    """A copied folder, a restored backup or a sync client all rewrite mtimes.
    None of those is a reason to start the retention clock again."""
    import os
    import time

    from app import turnlog
    from app.config import settings

    monkeypatch.setattr(settings, "turn_log", True)
    monkeypatch.setattr(settings, "turn_log_dir", str(tmp_path))
    monkeypatch.setattr(settings, "turn_log_keep_days", 30)
    monkeypatch.setattr(turnlog, "_fh", None)
    monkeypatch.setattr(turnlog, "_path", None)

    old = tmp_path / "2020-01-01.jsonl"
    old.write_text("{}\n", encoding="utf-8")
    os.utime(old, (time.time(), time.time()))   # as if just copied here

    turnlog.record("session_start")
    turnlog.close()
    assert not old.exists(), "trusted the mtime and kept a 2020 transcript"


def test_a_file_that_is_not_ours_is_left_alone(monkeypatch, tmp_path):
    from app import turnlog
    from app.config import settings

    monkeypatch.setattr(settings, "turn_log", True)
    monkeypatch.setattr(settings, "turn_log_dir", str(tmp_path))
    monkeypatch.setattr(settings, "turn_log_keep_days", 1)
    monkeypatch.setattr(turnlog, "_fh", None)
    monkeypatch.setattr(turnlog, "_path", None)

    stranger = tmp_path / "notes.jsonl"
    stranger.write_text("{}\n", encoding="utf-8")
    turnlog.record("session_start")
    turnlog.close()
    assert stranger.exists(), "deleted a file it did not create"


def test_a_spoken_turn_with_no_audio_is_logged_as_silent(logging_on):
    """2026-08-27, page parked overnight: morning transcripts flowed, tools
    fired, speakers silent. Two entirely different faults paint that same
    screen — Gemini sending text without audio, or the machine's output
    device dying in its sleep — and the log could not tell them apart. A
    turn that produced a transcript but (almost) no audio now leaves a
    silent_turn line; a silent morning WITHOUT these lines means the audio
    reached the browser and the fault is the machine's speakers."""
    import asyncio

    from app.providers.base import ProviderEvent
    from app.session import VoiceSession
    from app.tools import slides

    class FakeWS:
        async def send_text(self, text): pass
        async def send_bytes(self, data): pass

    def session_with(events_list):
        class FakeProvider:
            output_sample_rate = 24000

            def events(self):
                async def gen():
                    for e in events_list:
                        yield e
                return gen()

        sess = VoiceSession.__new__(VoiceSession)
        sess.ws = FakeWS()
        sess.provider = FakeProvider()
        sess._sent_audio_ms = 0.0
        sess._spoke_this_turn = False
        sess._nudge_index = None
        sess._nudge_count = 0
        return sess

    slides.reset_state()
    # Transcript, no audio: the silent shape.
    asyncio.run(session_with([
        ProviderEvent(kind="assistant_transcript", text="สวัสดีค่ะ"),
        ProviderEvent(kind="turn_complete"),
    ])._provider_to_browser())
    # Transcript with real audio: one second of PCM, nothing to report.
    asyncio.run(session_with([
        ProviderEvent(kind="assistant_transcript", text="สวัสดีค่ะ"),
        ProviderEvent(kind="audio", audio=b"\x00" * 48000),
        ProviderEvent(kind="turn_complete"),
    ])._provider_to_browser())

    silent = [r for r in read(logging_on) if r["event"] == "silent_turn"]
    assert len(silent) == 1, "exactly the audioless turn, not the audible one"
