"""The call-mic debug recorder (CALL_DEBUG).

The call path writes no audio by design (turnlog is numbers only), so tuning
the near-field floor by ear needed a WAKE_DEBUG equivalent for a live call.
What these guard: it is silent unless switched on, it keeps only the newest
CALL_DEBUG_MAX_S (a rolling buffer, tail kept), and the customer audio lands
in the module's directory — redirected to a tmp path here so the suite never
writes into the repo's data/.
"""
from __future__ import annotations

import wave

import pytest

from app import session as session_mod
from app.config import settings
from app.session import VoiceSession


def _session() -> VoiceSession:
    # Built without __init__, the way the other session pump tests do — the
    # recorder only ever touches settings and self._call_cap (via getattr).
    return VoiceSession.__new__(VoiceSession)


def test_off_records_nothing(monkeypatch):
    monkeypatch.setattr(settings, "call_debug", False)
    s = _session()
    s._call_debug_feed(b"\x01\x02" * 1000)
    assert getattr(s, "_call_cap", None) is None


def test_on_buffers_and_rolls_to_the_cap_keeping_the_tail(monkeypatch):
    monkeypatch.setattr(settings, "call_debug", True)
    monkeypatch.setattr(settings, "call_debug_max_s", 1)  # cap = 1s = 32000 bytes
    cap = 1 * session_mod.CALL_DEBUG_RATE * 2

    s = _session()
    s._call_debug_feed(b"\x01" * 10000)
    s._call_debug_feed(b"\x02" * 30000)   # total 40000 > cap

    buf = s._call_cap
    assert len(buf) == cap                 # trimmed to the cap
    assert buf[0] == 1 and buf[-1] == 2    # oldest dropped, newest kept
    assert buf.count(1) == 2000            # only the tail of the first chunk survives


def test_flush_writes_a_16k_mono_wav_and_resets(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "call_debug", True)
    monkeypatch.setattr(settings, "call_debug_max_s", 180)
    monkeypatch.setattr(session_mod, "CALL_DEBUG_DIR", tmp_path / "call_debug")

    s = _session()
    s._call_debug_feed(b"\x07\x00" * 8000)   # 16000 bytes = 8000 frames
    s._flush_call_debug()

    clips = list((tmp_path / "call_debug").glob("call-*.wav"))
    assert len(clips) == 1
    with wave.open(str(clips[0]), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == session_mod.CALL_DEBUG_RATE
        assert w.getnframes() == 8000
    assert s._call_cap is None               # buffer released after the write


def test_flush_with_no_buffer_is_a_safe_noop(monkeypatch, tmp_path):
    monkeypatch.setattr(session_mod, "CALL_DEBUG_DIR", tmp_path / "call_debug")
    s = _session()
    s._flush_call_debug()                    # must not raise
    assert not (tmp_path / "call_debug").exists()
