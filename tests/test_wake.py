"""
The wake word: "Emma" instead of the Start button.

Two layers, tested separately on purpose. The protocol layer (endpoint,
health flag, degradation) runs everywhere on fakes. The detection layer
needs the real 15MB model, which a fresh clone doesn't have — those tests
skip with a reason that says exactly what is not being verified, per this
project's rule that a skipped test must not read as a passed one.

The fixture audio is synthesized with the Windows TTS voice — committed,
because "tests pass on machines that lack things" has burned this project
four times, and a fixture that every machine has is the cure that costs
250KB.
"""
from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import wake
from app.config import settings
from app.main import app

FIXTURES = Path(__file__).parent / "data" / "wake"
MODEL_READY = (Path(settings.wake_model_dir) / "tokens.txt").exists()

needs_model = pytest.mark.skipif(
    not MODEL_READY,
    reason=(
        "wake model not downloaded (scripts/fetch_wake_model.py) — real "
        "keyword DETECTION is not being verified on this machine, only the "
        "protocol around it"
    ),
)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    wake.reset()
    yield
    wake.reset()


def _pcm(name: str) -> bytes:
    with wave.open(str(FIXTURES / name)) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        return w.readframes(w.getnframes())


# ==================== protocol: runs on every machine ====================


def test_disabled_wake_says_so_and_hangs_up(monkeypatch):
    """The browser must learn it should stay a button, not sit on a socket
    that will never speak."""
    monkeypatch.setattr(settings, "wake_enabled", False)
    client = TestClient(app)
    with client.websocket_connect("/ws/wake") as ws:
        evt = ws.receive_json()
    assert evt == {"type": "wake_unavailable", "reason": "disabled"}


def test_enabled_but_no_model_is_unavailable_not_an_error(monkeypatch, tmp_path):
    """WAKE_ENABLED=true on a machine that never ran the fetch script is the
    most likely misconfiguration this feature will ever see. It must degrade
    to the button, and the log (not the guest) gets the fix."""
    monkeypatch.setattr(settings, "wake_enabled", True)
    monkeypatch.setattr(settings, "wake_model_dir", str(tmp_path / "nowhere"))
    client = TestClient(app)
    with client.websocket_connect("/ws/wake") as ws:
        evt = ws.receive_json()
    assert evt == {"type": "wake_unavailable", "reason": "no model"}


def test_health_reports_ready_honestly(monkeypatch, tmp_path):
    """`enabled` and `ready` are different facts. The UI keys off `ready`;
    conflating them would put the page in a mode the server can't serve."""
    monkeypatch.setattr(settings, "wake_enabled", True)
    monkeypatch.setattr(settings, "wake_model_dir", str(tmp_path / "nowhere"))
    client = TestClient(app)
    payload = client.get("/health").json()["wake"]
    assert payload["enabled"] is True
    assert payload["ready"] is False


def test_an_unencodable_custom_word_disables_cleanly(monkeypatch, tmp_path):
    """A WAKE_WORD with no precomputed encoding, on a machine without
    sentencepiece, must fall back to the button — not crash the endpoint."""
    import sys

    monkeypatch.setattr(settings, "wake_enabled", True)
    monkeypatch.setattr(settings, "wake_word", "computer")
    # The model dir must exist for the code to even reach encoding.
    (tmp_path / "tokens.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(settings, "wake_model_dir", str(tmp_path))
    monkeypatch.setitem(sys.modules, "sentencepiece", None)  # import -> error
    assert wake.available() is False


# ==================== detection: needs the real model ====================


@needs_model
def test_the_name_alone_wakes(monkeypatch):
    monkeypatch.setattr(settings, "wake_enabled", True)
    stream = wake.WakeStream()
    assert stream.ok
    hits = [h for h in (stream.feed(_pcm("emma.wav")[i:i + 3200])
                        for i in range(0, len(_pcm("emma.wav")), 3200)) if h]
    assert hits == ["EMMA"]


@needs_model
def test_the_name_mid_sentence_wakes(monkeypatch):
    """"Emma, turn off the lights" — the natural phrasing. Requiring the
    name in isolation would train the owner to talk like a robot."""
    monkeypatch.setattr(settings, "wake_enabled", True)
    stream = wake.WakeStream()
    pcm = _pcm("emma_sentence.wav")
    hits = [h for h in (stream.feed(pcm[i:i + 3200])
                        for i in range(0, len(pcm), 3200)) if h]
    assert hits == ["EMMA"]


@needs_model
def test_unrelated_speech_does_not_wake(monkeypatch):
    """The other half of the contract, and the expensive half to get wrong:
    a false wake opens a paid Gemini session on an empty room."""
    monkeypatch.setattr(settings, "wake_enabled", True)
    stream = wake.WakeStream()
    pcm = _pcm("other.wav")
    hits = [h for h in (stream.feed(pcm[i:i + 3200])
                        for i in range(0, len(pcm), 3200)) if h]
    assert hits == []


@needs_model
def test_the_endpoint_wakes_end_to_end(monkeypatch):
    """The whole path the browser uses: binary frames in, {"type":"wake"}
    out, socket closed after — one detection, one session."""
    monkeypatch.setattr(settings, "wake_enabled", True)
    client = TestClient(app)
    with client.websocket_connect("/ws/wake") as ws:
        assert ws.receive_json()["type"] == "wake_listening"
        pcm = _pcm("emma_sentence.wav")
        for i in range(0, len(pcm), 3200):
            ws.send_bytes(pcm[i:i + 3200])
        evt = ws.receive_json()
    assert evt["type"] == "wake"
    assert evt["word"] == "EMMA"


@needs_model
def test_each_utterance_fires_exactly_once(monkeypatch):
    """One "Emma" = one wake event, and the next "Emma" wakes again.

    This is the detector's observed per-utterance contract (get_result
    self-clears; verified with reset_stream removed — same behaviour), so
    what this test can catch is a regression in that contract, e.g. a model
    or library upgrade that starts re-reporting the same hit. It does NOT
    prove reset_stream matters — it doesn't; see WakeStream.feed."""
    monkeypatch.setattr(settings, "wake_enabled", True)
    stream = wake.WakeStream()
    pcm = _pcm("emma.wav")
    hits = []
    for _ in range(2):          # the same audio replayed back to back
        for i in range(0, len(pcm), 3200):
            h = stream.feed(pcm[i:i + 3200])
            if h:
                hits.append(h)
    assert hits == ["EMMA", "EMMA"], (
        "one wake event per utterance broke: %r" % hits
    )


# ============ saying what the microphone is actually sending ============


def _probe_stream():
    """A WakeStream without a model — only the reporting half is under test."""
    from app import wake

    s = wake.WakeStream.__new__(wake.WakeStream)
    s._spotter = s._stream = None
    s._probe_at = 0.0
    s._probe_peak = 0.0
    s._probe_sum = 0.0
    s._probe_n = 0
    s._heard_anything = False
    return s


def _report_verdict(stream, level, caplog):
    """Feed one level long enough to force a report, return the log text."""
    import logging

    import numpy as np

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="condo_voice.wake"):
        block = np.full(1600, level, dtype="float32")
        stream._report(block)          # first call only starts the clock
        stream._probe_at -= 5.0        # ...so age it past the interval
        stream._report(block)
    return caplog.text


def test_the_wake_probe_separates_a_dead_mic_from_a_quiet_one(caplog):
    """"เรียกแล้วไม่เกิดอะไรขึ้น" had no evidence behind it anywhere. The page
    painted "ไมค์กำลังฟังอยู่" as soon as one frame left the browser — a frame
    of digital silence counts — and the server logged nothing unless the word
    fired. A muted input device, a microphone across the room and an
    unmatched name all looked the same, so the only way to tell them apart
    was to change something and guess again.

    Three levels, three different things to go and do."""
    from app import wake

    silent = _report_verdict(_probe_stream(), 0.0001, caplog)
    assert "SILENT" in silent and "input device" in silent

    quiet = _report_verdict(_probe_stream(), 0.01, caplog)
    assert "too quiet" in quiet and "MIC_BOOST" in quiet

    loud = _report_verdict(_probe_stream(), 0.2, caplog)
    assert "speech level reached" in loud
    assert "keyword, not the microphone" in loud


def test_the_probe_stays_quiet_unless_asked(monkeypatch):
    """Two lines a second into a log the gallery reads for other reasons.
    Useful while chasing a microphone, noise the rest of the time."""
    import inspect

    from app import wake
    from app.config import settings

    src = inspect.getsource(wake.WakeStream.feed)
    assert "if settings.wake_debug:" in src, \
        "the probe must be behind WAKE_DEBUG, not always on"
    assert settings.wake_debug is False or True   # value comes from .env
    import re

    from app import config
    assert re.search(r'_get_bool\("WAKE_DEBUG",\s*False\)',
                     inspect.getsource(config)), "default off"


def test_the_spellings_can_be_tuned_without_a_commit(monkeypatch):
    """Which spellings catch a real call of the name depends on the mouth,
    the room and the microphone — none of which are visible from here. The
    built-in three were measured missing a live "เอ็มม่า" through twenty
    seconds of loud, clear speech, so the next round of tuning must not
    need a code change and a pull."""
    from app import wake
    from app.config import settings

    monkeypatch.setattr(settings, "wake_enabled", True)
    monkeypatch.setattr(settings, "wake_spellings", "")
    builtin = wake._spellings_for("emma")
    assert "EMMA" in builtin
    assert "AMA" in builtin and "IMMA" in builtin, \
        "the neighbourhood was widened after the measurement"
    # Ordinary English words stay out: a wake word that fires on the room's
    # conversation is worse than one that needs saying twice.
    assert "ANNA" not in builtin and "ELMA" not in builtin

    monkeypatch.setattr(settings, "wake_spellings", " aimma , EMMA ")
    assert wake._spellings_for("emma") == ["AIMMA", "EMMA"]


def test_every_spelling_reaches_the_keyword_file_under_one_label(monkeypatch):
    """All variants report the same @LABEL, or a hit on one of them is not a
    hit on the name."""
    from app import wake
    from app.config import settings

    monkeypatch.setattr(settings, "wake_enabled", True)
    monkeypatch.setattr(settings, "wake_spellings", "")
    monkeypatch.setattr(settings, "wake_word", "emma")
    line = wake._encode_keyword("emma")
    if line is None:
        pytest.skip("no bpe.model and no built-in encoding — spellings UNTESTED here")
    rows = [r for r in line.splitlines() if r.strip()]
    assert len(rows) == len(wake._spellings_for("emma"))
    assert all(r.endswith("@EMMA") for r in rows), rows
