"""
The local VAD gate — silence must never leave the building.

Measured origin (data/logs/2026-08-24.jsonl): the recogniser, fed a quiet
room, emitted `我们走吧。` byte-identical five times and four of the day's
seven hangups followed it. The gate removes the raw material: what is never
sent cannot be hallucinated or metered.

The state machine is tested with a scripted detector because the properties
that matter — ordering, pre-roll, zero leakage during silence — belong to
the machine. One test drives the real Silero model over the committed TTS
wav so "it detects actual speech" is proven on any machine that has the
model, and *says what went untested* on one that does not.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.config import settings
from app.vad_gate import VadGate


class ScriptedDetector:
    """Says whatever the test tells it to, one answer per feed."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.fed = []

    def accept_waveform(self, samples):
        self.fed.append(len(samples))

    def is_speech_detected(self):
        return self.answers.pop(0) if self.answers else False


CHUNK = b"\x01\x02" * 160          # 20ms at 16kHz


def test_silence_produces_no_actions_at_all():
    """Not "less audio" — none. No bytes, no signals, nothing for the
    upstream recogniser to turn into a sentence nobody said."""
    gate = VadGate(ScriptedDetector([False] * 50), prefix_padding_ms=300)
    for _ in range(50):
        assert gate.feed(CHUNK) == []


def test_speech_begins_with_start_then_preroll_then_live_audio():
    """The detector needs frames to decide, so by the time it says yes the
    first syllable is history. Without the pre-roll every utterance arrives
    beheaded — "ปิดไฟ" reaching the model as "ดไฟ" — which is the job
    Gemini's own prefix_padding_ms did when detection ran upstream."""
    gate = VadGate(ScriptedDetector([False, False, True]), prefix_padding_ms=300)
    gate.feed(CHUNK)
    gate.feed(CHUNK)
    actions = gate.feed(CHUNK)
    assert [k for k, _ in actions] == ["start", "audio", "audio"]
    assert actions[1][1] == CHUNK * 2, "the buffered silence is the pre-roll"
    assert actions[2][1] == CHUNK, "then the frame that flipped the detector"


def test_the_preroll_is_capped_at_the_configured_padding():
    """An uncapped buffer would flush minutes of a quiet room into the
    session the moment somebody finally speaks."""
    gate = VadGate(ScriptedDetector([False] * 100 + [True]), prefix_padding_ms=100)
    for _ in range(100):
        gate.feed(CHUNK)
    actions = gate.feed(CHUNK)
    preroll = actions[1][1]
    assert len(preroll) <= 100 * 2 * 16   # 100ms * 2 bytes * 16 samples/ms


def test_speech_ending_sends_the_tail_then_the_end_signal():
    """The frame on which the detector flips false is the tail of the
    utterance's silence window and still gets forwarded; the signal follows
    it. There is no second hangover timer here — the detector's own
    min_silence_duration is the hangover, and two clocks for one decision
    is this project's most repeated bug."""
    gate = VadGate(ScriptedDetector([True, True, False, False]), prefix_padding_ms=300)
    gate.feed(CHUNK)
    gate.feed(CHUNK)
    actions = gate.feed(CHUNK)
    assert [k for k, _ in actions] == ["audio", "end"]
    assert gate.feed(CHUNK) == [], "back to buffering, nothing leaks"


def test_the_session_config_and_the_gate_agree(monkeypatch):
    """Exactly one detector per session. A gate sending activity signals
    into a session whose server-side detection is still on double-detects
    every utterance; disabling upstream with no gate deafens the robot."""
    from app import vad_gate
    from app.providers.gemini import GeminiProvider

    monkeypatch.setattr(vad_gate, "for_session",
                        lambda: VadGate(ScriptedDetector([]), prefix_padding_ms=0))
    with_gate = GeminiProvider("Kore", "x")._build_config()
    aad = with_gate["realtime_input_config"].automatic_activity_detection
    assert aad.disabled is True
    # And *only* disabled. The Live API rejects the whole session (1007:
    # "disabled is true, but the following fields were also set") if any
    # tuning knob rides along — seen on screen the first time local VAD ran,
    # as a robot that could not open a call at all.
    for stray in ("start_of_speech_sensitivity", "end_of_speech_sensitivity",
                  "prefix_padding_ms", "silence_duration_ms"):
        assert getattr(aad, stray, None) is None, stray

    monkeypatch.setattr(vad_gate, "for_session", lambda: None)
    without = GeminiProvider("Kore", "x")._build_config()
    assert without["realtime_input_config"].automatic_activity_detection.disabled is False


def test_send_audio_translates_gate_actions_into_activity_signals(monkeypatch):
    """The wire format Gemini's manual mode expects: activity_start, audio
    blobs, activity_end — and during silence, no call at all."""
    import asyncio

    from app import vad_gate
    from app.providers.gemini import GeminiProvider

    monkeypatch.setattr(
        vad_gate, "for_session",
        lambda: VadGate(ScriptedDetector([False, True, True, False]),
                        prefix_padding_ms=300))
    provider = GeminiProvider("Kore", "x")

    calls = []

    class FakeSession:
        async def send_realtime_input(self, **kw):
            calls.append({k: v for k, v in kw.items() if v is not None})

    provider._session = FakeSession()

    async def run():
        for _ in range(4):
            await provider.send_audio(CHUNK)

    asyncio.run(run())
    kinds = [next(iter(c)) for c in calls]
    assert kinds == ["activity_start", "audio", "audio", "audio", "audio",
                     "activity_end"], kinds
    assert calls[0]["activity_start"] is not None


def test_a_missing_model_falls_back_loudly_not_silently(monkeypatch, caplog, tmp_path):
    """VAD_MODE=local with no model behaving "exactly like before" is the
    misconfiguration shape this project keeps paying for: the switch is on,
    nothing changed, nothing said why. The warning names the one command
    that fixes it."""
    import logging

    from app import vad_gate

    vad_gate.reset_warnings()
    monkeypatch.setattr(settings, "vad_mode", "local")
    monkeypatch.setattr(settings, "vad_model", str(tmp_path / "nope.onnx"))
    with caplog.at_level(logging.WARNING, logger="condo_voice.vad"):
        assert vad_gate.for_session() is None
    assert "fetch_vad_model.py" in caplog.text


QUIET_CHUNK = b"\x30\x00" * 160     # amplitude 48/32768 ≈ 0.0015 RMS-ish
LOUD_CHUNK = (int(0.1 * 32767)).to_bytes(2, "little", signed=True) * 160


def test_the_floor_keeps_far_speech_out_entirely():
    """Silero answers "is somebody speaking", not "is somebody speaking to
    us" — in the owner's room it opened the gate for conversations across
    the room, and Emma answered words never aimed at her (measured on
    screen, 2026-08-26: she narrated somebody's off-mic cursing back at
    them). Loudness is the one signal that separates the person at the
    microphone from the rest of the room, so quiet speech must produce NO
    actions — not quieter ones."""
    gate = VadGate(ScriptedDetector([True] * 10), prefix_padding_ms=100,
                   min_rms=0.03)
    gate._floor_off_until -= 10         # past the opening grace; see below
    for _ in range(10):
        assert gate.feed(QUIET_CHUNK) == []
    assert not gate.in_speech


def test_the_floor_opens_for_the_person_at_the_desk():
    """The twin: speech above the floor passes with its pre-roll intact —
    the floor rejects distance, not the first syllable."""
    gate = VadGate(ScriptedDetector([False, True, True]), prefix_padding_ms=100,
                   min_rms=0.03)
    gate._floor_off_until -= 10         # past the opening grace
    gate.feed(QUIET_CHUNK)              # room noise into the pre-roll
    actions = gate.feed(LOUD_CHUNK)
    assert [k for k, _ in actions] == ["start", "audio", "audio"]
    assert gate.feed(LOUD_CHUNK) == [("audio", LOUD_CHUNK)]


def test_the_floor_never_chops_an_open_segment():
    """Applied only at the opening: a sentence trailing off quietly is still
    the same sentence, and cutting it mid-word is the beheading bug at the
    other end."""
    gate = VadGate(ScriptedDetector([True, True, True]), prefix_padding_ms=100,
                   min_rms=0.03)
    gate.feed(LOUD_CHUNK)               # opens
    assert gate.feed(QUIET_CHUNK) == [("audio", QUIET_CHUNK)], \
        "the quiet tail of an open segment must still be forwarded"


def test_the_floor_stands_down_for_the_opening_seconds():
    """A wake-opened session begins with the wake tail — the very speech
    that fired the detector, replayed in — and on 2026-08-26 the floor ate
    exactly that (rms 0.006 < 0.010): Emma woke, heard nothing, said
    nothing, and the owner concluded she wasn't awake. Whoever opened the
    session was addressing the machine by definition, so for the first few
    seconds quiet speech passes."""
    gate = VadGate(ScriptedDetector([True]), prefix_padding_ms=100,
                   min_rms=0.03)
    actions = gate.feed(QUIET_CHUNK)      # quiet, but inside the grace
    assert actions and actions[0][0] == "start", \
        "the wake tail was distance-filtered at the door"


def test_zero_grace_applies_the_near_field_floor_from_the_first_frame():
    gate = VadGate(ScriptedDetector([True]), prefix_padding_ms=100,
                   min_rms=0.03, floor_grace_s=0)

    assert gate.feed(QUIET_CHUNK) == []
    assert gate.segments == 0


def test_stand_down_switches_the_floor_off_for_the_session():
    """A session the machine opened itself (camera at the door) greets a
    person who is far away by construction. 2026-08-31: อาซู่ answered the
    greeting from the doorway and the session logged not one `heard` — the
    floor (0.01, already at the top of the owner's *desk* readings) ate
    every word, long after the opening grace had been spent on the dial.
    """
    gate = VadGate(ScriptedDetector([True] * 4), prefix_padding_ms=100,
                   min_rms=0.03)
    gate._floor_off_until -= 10         # past the opening grace
    assert gate.feed(QUIET_CHUNK) == [], "floor is on until told otherwise"
    gate.stand_down()
    actions = gate.feed(QUIET_CHUNK)
    assert actions and actions[0][0] == "start", \
        "after stand_down the doorway voice must open the gate"


def test_no_floor_means_the_old_behaviour_exactly():
    """Default 0 = off: the showroom wants far pickup, and a pull must not
    change what it hears."""
    gate = VadGate(ScriptedDetector([True]), prefix_padding_ms=100)
    actions = gate.feed(QUIET_CHUNK)
    assert [k for k, _ in actions][0] == "start"


def test_for_session_carries_the_configured_floor(monkeypatch):
    """The setting must reach the gate — a knob wired to nothing is the
    misconfiguration shape this project keeps paying for."""
    from app import vad_gate

    monkeypatch.setattr(settings, "vad_mode", "local")
    monkeypatch.setattr(settings, "vad_min_rms", 0.07)
    monkeypatch.setattr(vad_gate, "_make_detector",
                        lambda: ScriptedDetector([]))
    gate = vad_gate.for_session()
    assert gate._min_rms == pytest.approx(0.07)


def test_local_detector_requires_sustained_speech_to_reject_clicks(monkeypatch, tmp_path):
    """A one-frame desk tap must not become a user turn and model reply."""
    import sys
    from types import SimpleNamespace

    from app import vad_gate

    model = tmp_path / "silero.onnx"
    model.write_bytes(b"model placeholder")

    class VadModelConfig:
        def __init__(self):
            self.silero_vad = SimpleNamespace(
                model="", threshold=0, min_silence_duration=0,
                min_speech_duration=0)
            self.sample_rate = 0

    captured = {}

    def detector(config, buffer_size_in_seconds):
        captured["config"] = config
        captured["buffer"] = buffer_size_in_seconds
        return SimpleNamespace()

    fake_sherpa = SimpleNamespace(
        VadModelConfig=VadModelConfig,
        VoiceActivityDetector=detector,
    )
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake_sherpa)
    monkeypatch.setattr(settings, "vad_model", str(model))
    monkeypatch.setattr(settings, "vad_min_speech_ms", 250)

    assert vad_gate._make_detector() is not None
    assert captured["config"].silero_vad.min_speech_duration == pytest.approx(0.25)


def test_the_call_uses_its_own_mic_boost_not_standbys():
    """The two modes want opposite microphones: standby's compressor lets
    the name carry across the room, and the same compressor lifts everyone
    else's conversation into the call. startMic must build its chain with
    the call boost; the wake path keeps the standby one."""
    src = (Path(__file__).parent.parent / "client" / "index.html").read_text(
        encoding="utf-8")
    call = src[src.index("async function startMic"):]
    call = call[:call.index("worklet = new AudioWorkletNode")]
    assert "buildMicChain(audioCtx, micSource, micCallBoost)" in call
    wake_path = src[src.index("async function startWakeMode"):]
    wake_path = wake_path[:wake_path.index("wakeWorklet = new AudioWorkletNode")]
    assert "micCallBoost" not in wake_path, \
        "standby must keep far-field reach — that is how the name is heard"


def test_the_real_detector_hears_the_committed_speech():
    """Real Silero over the committed TTS wav: start, audio, end — on a
    machine with the model. On one without, this line is what is going
    untested: that the detector detects actual speech at all."""
    import numpy as np

    model = Path(settings.vad_model)
    if not model.is_file():
        pytest.skip("no silero_vad.onnx — real speech detection is UNTESTED "
                    "here; fetch with scripts/fetch_vad_model.py")
    import sherpa_onnx

    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = str(model)
    cfg.silero_vad.min_silence_duration = 0.3
    cfg.sample_rate = 16000
    detector = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=30)
    gate = VadGate(detector, prefix_padding_ms=300)

    import wave

    w = wave.open(str(Path(__file__).parent / "data" / "wake" / "emma_sentence.wav"))
    pcm = w.readframes(w.getnframes())
    # The utterance, then over a second of silence so the end can fire.
    stream = pcm + b"\x00" * 16000 * 2 * 2
    kinds = []
    for i in range(0, len(stream), 640):
        kinds += [k for k, _ in gate.feed(stream[i:i + 640])]
    assert "start" in kinds and "end" in kinds
    assert kinds.index("start") < kinds.index("end")
    assert kinds.count("start") >= 1 and kinds[0] != "end"


def test_the_gate_counts_the_segments_it_opened():
    """Read by the call's microphone report: "speech level reached but
    Silero opened no segment" is a different repair from "no audio"."""
    gate = VadGate(ScriptedDetector([True, True, False, False, True]), prefix_padding_ms=100)
    assert gate.segments == 0
    for _ in range(5):
        gate.feed(LOUD_CHUNK)
    assert gate.segments == 2


MUTED_CHUNK = (b"\x00\x00" * 159) + b"\x03\x00"   # exact zeros with a 1-LSB edge


def test_a_muted_microphone_never_opens_a_segment_even_with_the_floor_down():
    """2026-09-01 14:35-14:38: a hardware-muted USB mic (peak 0.0001),
    the near-field floor stood down for a camera-opened session, Silero
    flagging the dither edges as speech - and Gemini answering the
    silence with "I want to know the price of the product." five times.
    The floor under the floor is not a knob and stand_down does not
    touch it."""
    gate = VadGate(ScriptedDetector([True] * 11), prefix_padding_ms=100, min_rms=0.03)
    gate.stand_down()
    for _ in range(10):
        assert gate.feed(MUTED_CHUNK) == []
    assert gate.segments == 0
    # ...and it is a floor, not a wall: real quiet speech still opens.
    assert gate.feed(QUIET_CHUNK)[0][0] == "start"
