"""
Local voice-activity gate: silence never leaves the building.

With Gemini's own VAD (`VAD_MODE=gemini`, the default) the microphone streams
upstream continuously and Google decides where speech starts and ends. That
works, and it has two measured costs:

- **Silence is what hallucinated transcripts are made of.** On 2026-08-24
  the recogniser emitted `我们走吧。` — the same canned sentence, byte for
  byte, five times across the day — and four of the day's seven hangups
  followed it. Nobody said it; it is what a speech model produces when it is
  fed quiet. (The Whisper community documents the same failure as "Thank you
  for watching!") A guard now stops the *hangup*, but the honest fix is that
  quiet should never be sent at all.

- **The meter runs on silence.** An open session streams the room upstream
  whether anybody is talking or not.

`VAD_MODE=local` runs Silero VAD (via sherpa-onnx, the package the wake word
already uses) on this machine, tells Gemini to switch its own detection off,
and forwards audio only while somebody is actually speaking — wrapped in the
explicit `activity_start` / `activity_end` signals the Live API accepts for
exactly this arrangement. This is the pattern the current voice frameworks
(Pipecat, LiveKit) converged on: acoustic VAD at the edge, the expensive
model only ever hears speech.

The gate itself is a pure state machine over a detector interface, because
the thing that must be testable — ordering, pre-roll, no leakage during
silence — is the machine, not the neural network. The sherpa detector plugs
in behind it; tests use a scripted one.
"""
from __future__ import annotations

import logging

from app.config import settings

logger = logging.getLogger("condo_voice.vad")

_warned_missing_model = False


class VadGate:
    """Turns a continuous PCM stream into speech segments with signals.

    `feed()` returns an ordered list of actions:

        ("start", b"")     speech just began — send activity_start
        ("audio", chunk)   audio to forward (pre-roll first, then live)
        ("end", b"")       speech ended — send activity_end

    **Pre-roll.** The detector needs a few frames to decide that speech has
    started, so by the time it says yes, the first syllable has already
    passed. The gate keeps the last `prefix_padding_ms` of audio while
    silent and flushes it the moment speech begins — the same job Gemini's
    own `prefix_padding_ms` did when detection ran upstream. Without it,
    every utterance arrives beheaded: "ปิดไฟ" reaches the model as "ดไฟ",
    and short commands vanish entirely.
    """

    def __init__(self, detector, prefix_padding_ms: int, sample_rate: int = 16000) -> None:
        self._detector = detector
        self._rate = sample_rate
        #: Bytes of pre-roll to keep: ms * (2 bytes/sample) * samples/ms.
        self._preroll_max = int(prefix_padding_ms * 2 * sample_rate / 1000)
        self._preroll = bytearray()
        self.in_speech = False

    def feed(self, pcm16: bytes) -> list[tuple[str, bytes]]:
        if not pcm16:
            return []
        import numpy as np

        samples = np.frombuffer(pcm16, dtype="<i2").astype("float32") / 32768.0
        self._detector.accept_waveform(samples)
        speaking = bool(self._detector.is_speech_detected())

        actions: list[tuple[str, bytes]] = []
        if speaking and not self.in_speech:
            self.in_speech = True
            actions.append(("start", b""))
            if self._preroll:
                actions.append(("audio", bytes(self._preroll)))
                self._preroll.clear()
            actions.append(("audio", pcm16))
        elif speaking:
            actions.append(("audio", pcm16))
        elif self.in_speech:
            # The detector's own min_silence_duration is the hangover: it
            # only flips false after the configured quiet stretch, so there
            # is no second timer here. Two clocks for one decision is this
            # project's most repeated bug.
            self.in_speech = False
            actions.append(("audio", pcm16))   # the tail that flipped it
            actions.append(("end", b""))
        else:
            self._preroll.extend(pcm16)
            if len(self._preroll) > self._preroll_max:
                del self._preroll[:len(self._preroll) - self._preroll_max]
        return actions


def _make_detector():
    """A sherpa-onnx Silero detector per session, or None with a loud reason.

    None must never be quiet: `VAD_MODE=local` with no model would otherwise
    just mean "behaves exactly like before", which is the misconfiguration
    shape this project keeps paying for — the switch is on, nothing changed,
    nothing said why.
    """
    global _warned_missing_model
    from pathlib import Path

    model = Path(settings.vad_model).expanduser()
    if not model.is_file():
        if not _warned_missing_model:
            logger.warning(
                "VAD_MODE=local but %s is missing — falling back to Gemini's "
                "own detection. Fetch it once with: "
                "python scripts/fetch_vad_model.py", model,
            )
            _warned_missing_model = True
        return None
    try:
        import sherpa_onnx

        cfg = sherpa_onnx.VadModelConfig()
        cfg.silero_vad.model = str(model)
        cfg.silero_vad.threshold = 0.5
        # Reuse the knobs the Gemini-side VAD already had, so switching modes
        # does not silently change how patient the robot is. VAD_SILENCE_MS
        # was just raised to 900 because 500 cut off Thai speakers pausing
        # mid-sentence; that decision must survive the mode switch.
        cfg.silero_vad.min_silence_duration = settings.vad_silence_ms / 1000
        cfg.silero_vad.min_speech_duration = 0.1
        cfg.sample_rate = 16000
        return sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=30)
    except Exception:
        logger.exception("could not create the local VAD — falling back to "
                         "Gemini's own detection")
        return None


def for_session():
    """The gate for a new session, or None when the mode is off/unavailable.

    Per-session on purpose: the detector carries decoder state, and two
    sessions sharing one would hear each other's tails — the same isolation
    rule the wake word's per-connection streams follow.
    """
    if settings.vad_mode != "local":
        return None
    detector = _make_detector()
    if detector is None:
        return None
    logger.info("local VAD active: silence stays here, Gemini hears speech only")
    return VadGate(detector, prefix_padding_ms=settings.vad_prefix_padding_ms)


def reset_warnings() -> None:
    """Tests flip settings; the once-only warning must not stay spent."""
    global _warned_missing_model
    _warned_missing_model = False
