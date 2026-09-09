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
def test_simulator_detector_hears_without_recording_diagnostics(monkeypatch):
    monkeypatch.setattr(settings, "wake_enabled", True)
    monkeypatch.setattr(settings, "wake_debug", True)
    monkeypatch.setattr(settings, "wake_enroll", True)

    def forbidden(*args):
        raise AssertionError("simulation must not record rehearsal audio")

    monkeypatch.setattr(wake.WakeStream, "_save_clip", forbidden)
    monkeypatch.setattr(wake.WakeStream, "_report", forbidden)
    monkeypatch.setattr(wake, "_get_shadow_spotter", forbidden)
    stream = wake.WakeStream(diagnostics=False)
    assert stream.ok and stream._cap is None
    pcm = _pcm("emma.wav")
    hits = [hit for i in range(0, len(pcm), 3200)
            if (hit := stream.feed(pcm[i:i + 3200]))]
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


def _hits(name: str) -> list[str]:
    stream = wake.WakeStream()
    pcm = _pcm(name)
    return [h for h in (stream.feed(pcm[i:i + 3200])
                        for i in range(0, len(pcm), 3200)) if h]


@needs_model
def test_a_thai_mouth_saying_the_name_wakes(monkeypatch):
    """The gap every green run hid until 2026-08-25: emma.wav is an
    *English* TTS voice, so the suite verified an accent nobody in this
    house speaks with. These fixtures are Thai neural TTS saying "เอ็มม่า"
    — the female one is the exact utterance the old five-spelling list
    missed while the owner stood there repeating the name."""
    monkeypatch.setattr(settings, "wake_enabled", True)
    assert _hits("emma_thai_f.wav"), \
        "the plain female เอ็มม่า — the reported miss — went unheard"
    assert _hits("emma_thai_m.wav"), "the male เอ็มม่า went unheard"


@needs_model
def test_debug_mode_reports_a_near_miss_instead_of_nothing(monkeypatch, caplog):
    """The undiagnosable case: the RMS report says "speech level reached",
    the name still does not fire, and nothing says whether it scored just
    under the threshold or never resembled the keyword at all. The shadow
    detector (same spellings, floor threshold) makes the first case speak:
    a shadow hit with no real hit is a score in the gap, and the log names
    the one knob that fixes it."""
    import logging

    monkeypatch.setattr(settings, "wake_enabled", True)
    monkeypatch.setattr(settings, "wake_debug", True)
    # A threshold nothing real can clear, so the name lands in the gap.
    monkeypatch.setattr(settings, "wake_threshold", 0.9)
    with caplog.at_level(logging.WARNING, logger="condo_voice.wake"):
        assert _hits("emma_thai_f.wav") == [], "0.9 should be unreachable"
    assert "NEAR MISS" in caplog.text
    assert "WAKE_THRESHOLD" in caplog.text, "the fix must be named"


def test_the_shadow_ear_only_exists_while_debugging(monkeypatch):
    """A second full decode of every standby frame is a debugging tool, not
    a tax the gallery pays around the clock."""
    monkeypatch.setattr(settings, "wake_enabled", True)
    monkeypatch.setattr(settings, "wake_debug", False)
    stream = wake.WakeStream()
    assert stream._shadow_stream is None


def test_the_wake_hit_is_acknowledged_before_the_dial():
    """Between the name being heard and Emma's greeting sit 2-4 seconds of
    getUserMedia + WebSocket + Gemini setup, and dead air there reads as
    "she didn't hear me" — the owner repeats the name into a session that is
    already opening. The chime and the status line are the only responses
    that can be instant, so they must come before startCall, not after."""
    src = (Path(__file__).resolve().parent.parent / "client"
           / "index.html").read_text(encoding="utf-8")
    handler = src[src.index("evt.type === 'wake'"):]
    handler = handler[:handler.index("startCall()")]
    assert "playWakeChime()" in handler, "no instant acknowledgement"
    assert "sleepNote(" in handler, "the screen must say it heard"
    assert "playWakeChime" in src.split("function playWakeChime", 1)[1][:2000] or \
        "wakeCtx" in src.split("function playWakeChime", 1)[1][:2000], \
        "the chime must use the already-unlocked wake AudioContext"


@needs_model
def test_debug_mode_keeps_the_audio_a_miss_actually_heard(monkeypatch, tmp_path):
    """The 2026-08-26 dead end: real calls of the name scored under even the
    shadow's floor while Thai-TTS passes everything — the difference between
    those worlds is the audio itself, and thresholds cannot show a waveform.
    Speech that fires nothing gets written to disk so the next tuning round
    runs against the owner's actual voice instead of a proxy."""
    monkeypatch.setattr(settings, "wake_enabled", True)
    monkeypatch.setattr(settings, "wake_debug", True)
    monkeypatch.setattr(settings, "wake_debug_dir", str(tmp_path))
    monkeypatch.setattr(settings, "wake_threshold", 0.9)   # nothing can fire
    stream = wake.WakeStream()
    pcm = _pcm("emma_thai_f.wav")
    for i in range(0, len(pcm), 3200):
        stream.feed(pcm[i:i + 3200])
    # The report tick is wall-clock (every ~2s); rewind it so the next
    # chunk closes the window without the test sleeping through it.
    stream._probe_at -= 3.0
    stream.feed(pcm[:3200])
    clips = list(tmp_path.glob("miss-*.wav"))
    assert clips, "speech fired nothing and no evidence was kept"

    import wave as _wave

    with _wave.open(str(clips[0])) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        assert w.getnframes() > 16000, "less than a second is not evidence"


def test_no_capture_buffer_outside_debug_mode(monkeypatch):
    """Standby audio is a room's private sound. The capture exists for the
    owner tuning their own machine under WAKE_DEBUG — with the flag off
    there must be no buffer at all, not an unused one."""
    monkeypatch.setattr(settings, "wake_enabled", True)
    monkeypatch.setattr(settings, "wake_debug", False)
    assert wake.WakeStream()._cap is None


def test_miss_clips_are_pruned_to_twenty(monkeypatch, tmp_path):
    """A tuning instrument, not a recorder: the folder must not grow without
    bound on a machine left in debug mode for a week."""
    monkeypatch.setattr(settings, "wake_debug_dir", str(tmp_path))
    for i in range(25):
        (tmp_path / f"miss-20260826-{i:06d}.wav").write_bytes(b"x")
    s = wake.WakeStream.__new__(wake.WakeStream)
    s._cap = bytearray(b"\x00\x01" * 16000)
    s._save_clip("miss")
    assert len(list(tmp_path.glob("miss-*.wav"))) <= 20


@needs_model
def test_enroll_mode_keeps_the_successful_calls_too(monkeypatch, tmp_path):
    """The matcher needs the owner's voice saying the name *well* — and a
    hit closes the socket immediately, so the clip must be saved at the hit,
    not on a report tick that will never come. Without this, enrollment
    collects only the worst takes and the matcher learns to hear mumbling."""
    monkeypatch.setattr(settings, "wake_enabled", True)
    monkeypatch.setattr(settings, "wake_enroll", True)
    monkeypatch.setattr(settings, "wake_enroll_dir", str(tmp_path))
    stream = wake.WakeStream()
    pcm = _pcm("emma_thai_f.wav")
    got = [h for i in range(0, len(pcm), 3200)
           if (h := stream.feed(pcm[i:i + 3200]))]
    assert got, "the fixture stopped firing — this test needs a hit"
    assert list(tmp_path.glob("enroll-*.wav")), \
        "the successful call was not kept"


@needs_model
def test_enroll_mode_keeps_misses_as_enrollment_not_bug_evidence(monkeypatch, tmp_path):
    """In enrollment, a window of speech with no hit is a sample, not a bug
    report — it goes to the enroll folder under the enroll name."""
    monkeypatch.setattr(settings, "wake_enabled", True)
    monkeypatch.setattr(settings, "wake_enroll", True)
    monkeypatch.setattr(settings, "wake_enroll_dir", str(tmp_path))
    monkeypatch.setattr(settings, "wake_threshold", 0.9)   # nothing fires
    stream = wake.WakeStream()
    pcm = _pcm("emma_thai_f.wav")
    for i in range(0, len(pcm), 3200):
        stream.feed(pcm[i:i + 3200])
    stream._probe_at -= 3.0
    stream.feed(pcm[:3200])
    assert list(tmp_path.glob("enroll-*.wav"))


@needs_model
def test_the_imm_sound_in_thai_speech_does_not_wake(monkeypatch):
    """"เดี๋ยวไปกินข้าวกันไหม อิ่มมากเลย" — ordinary Thai with an "อิ่มมา"
    in it, the nearest real-speech trap to IMMA. At the shipped threshold it
    must stay quiet; if this fires, the threshold or the spelling list has
    drifted into the room's own conversation."""
    monkeypatch.setattr(settings, "wake_enabled", True)
    assert _hits("other_imm_trap.wav") == []


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
    s._cap = None                  # no capture: only the reporting half here
    s._hit_this_window = False
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
    assert "if self._diagnostics and (settings.wake_debug or settings.wake_enroll):" in src, \
        "the probe must be behind an explicit switch, not always on"
    import re

    from app import config
    cfg_src = inspect.getsource(config)
    assert re.search(r'_get_bool\("WAKE_DEBUG",\s*False\)', cfg_src), "default off"
    assert re.search(r'_get_bool\("WAKE_ENROLL",\s*False\)', cfg_src), \
        "enrollment records the room — it must never be a default"


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
    assert "IMMA" in builtin, \
        "the only spelling that heard the short female เอ็มม่า was dropped"
    # Small on purpose: the 2026-08-25 measurement showed spellings compete
    # inside one decoder beam — the five-spelling list caught *fewer* Thai
    # TTS calls (5/8, one false positive) than EMMA+IMMA alone (7/8, none).
    # Widening this list back is how the name gets harder to say again.
    assert len(builtin) <= 3, "the beam-competition measurement was undone"
    # Ordinary English words stay out: a wake word that fires on the room's
    # conversation is worse than one that needs saying twice.
    assert "ANNA" not in builtin and "ELMA" not in builtin

    monkeypatch.setattr(settings, "wake_spellings", " aimma , EMMA ")
    assert wake._spellings_for("emma") == ["AIMMA", "EMMA"]


def test_emma_listens_at_half_the_bar_and_imma_at_the_full_one(monkeypatch):
    """The 2026-08-26 morning log: a real "เอ็มม่า" NEAR-MISSED under 0.10
    and the owner called the name several times per wake. Measured fix:
    EMMA at half the threshold, IMMA staying — EMMA@0.05 alone false-fires
    on "อิ่มมาก", but IMMA at the full bar takes that utterance in the beam
    and rejects it. The asymmetry is the fix; this test is what keeps
    somebody from tidying it into symmetry."""
    monkeypatch.setattr(settings, "wake_enabled", True)
    monkeypatch.setattr(settings, "wake_spellings", "")
    monkeypatch.setattr(settings, "wake_word", "emma")
    monkeypatch.setattr(settings, "wake_threshold", 0.10)
    line = wake._encode_keyword("emma")
    if line is None:
        pytest.skip("no encoding available — per-spelling bars UNTESTED here")
    rows = dict()
    for row in line.splitlines():
        # "<pieces> :<boost> #<threshold> @LABEL"
        rows[row.split()[0].lstrip("▁")] = float(row.split("#")[1].split()[0])
    assert rows["E"] == pytest.approx(0.05), "EMMA must sit at half the bar"
    assert rows["I"] == pytest.approx(0.10), \
        "IMMA at the full bar is what keeps อิ่มมาก out — measured, not style"
    # A MA sits just under the bar (0.8x): the owner's confirmed clean
    # "เอ็มม่า" hits this path at 0.08 and misses at 0.10 — measured, and
    # the false-fire profile at 0.8x was identical to 1.0x.
    assert rows["A"] == pytest.approx(0.08), \
        "A MA must sit at 0.8x — the confirmed real call lives at 0.08"
    # A scale, not an absolute: WAKE_THRESHOLD stays the one knob.
    monkeypatch.setattr(settings, "wake_threshold", 0.20)
    line = wake._encode_keyword("emma")
    assert "#0.10" in line and "#0.20" in line and "#0.16" in line


def test_wake_spellings_can_carry_their_own_bars(monkeypatch):
    """`WAKE_SPELLINGS=AIMMA:0.07,EMMA` — tuning per spelling without a
    commit, same reason the list itself is env-tunable. A malformed number
    must cost that one bar, never the whole wake word."""
    monkeypatch.setattr(settings, "wake_enabled", True)
    monkeypatch.setattr(settings, "wake_spellings", "EMMA:0.07, IMMA")
    monkeypatch.setattr(settings, "wake_threshold", 0.10)
    assert wake._spellings_for("emma") == ["EMMA", "IMMA"]
    line = wake._encode_keyword("emma")
    if line is None:
        pytest.skip("no encoding available — override bars UNTESTED here")
    assert "#0.07" in line, "the explicit bar never reached the keyword file"
    assert "#0.10" in line, "the bare spelling must keep the default bar"

    monkeypatch.setattr(settings, "wake_spellings", "EMMA:oops, IMMA")
    assert wake._spellings_for("emma") == ["EMMA", "IMMA"], \
        "a malformed bar must not take the spelling with it"


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


def test_debug_mode_keeps_the_hit_clip_too(monkeypatch, tmp_path):
    """2026-08-27 08:52: a false wake — room chatter fired the detector and
    Emma greeted a conversation nobody was having with her. The miss clips
    could not reproduce the hit offline, and the audio that actually fired
    was the audio this path used to throw away ("a successful wake is not a
    miss"). A false wake IS a hit; under WAKE_DEBUG the hit clip is the
    evidence, kept under its own name so the miss-pruning cannot eat it."""
    from app.config import settings

    monkeypatch.setattr(settings, "wake_debug", True)
    monkeypatch.setattr(settings, "wake_enroll", False)
    monkeypatch.setattr(settings, "wake_debug_dir", str(tmp_path))

    stream = _probe_stream()
    stream._cap = bytearray(b"\x01\x02" * 4000)   # capture on, as debug mode has it
    stream._diagnostics = True
    stream._cap_max = 16000 * 2 * 6
    stream._hit_this_window = False

    class OneHitSpotter:
        def __init__(self):
            self.fired = False

        def is_ready(self, s):
            if self.fired:
                return False
            self.fired = True
            return True

        def decode_stream(self, s): pass

        def get_result(self, s):
            return "EMMA"

        def reset_stream(self, s): pass

    class FakeStream:
        def accept_waveform(self, rate, samples): pass

    stream._spotter = OneHitSpotter()
    stream._stream = FakeStream()
    stream._shadow_stream = None
    monkeypatch.setattr("app.wake._get_shadow_spotter", lambda: None)
    hit = stream.feed(b"\x01\x02" * 160)
    assert hit == "EMMA"
    clips = list(tmp_path.glob("hit-*.wav"))
    assert len(clips) == 1, "the firing audio must be kept, not cleared"
