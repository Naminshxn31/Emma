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
