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

    def __init__(self, detector, prefix_padding_ms: int, sample_rate: int = 16000,
                 min_rms: float = 0.0) -> None:
        self._detector = detector
        self._rate = sample_rate
        #: Bytes of pre-roll to keep: ms * (2 bytes/sample) * samples/ms.
        self._preroll_max = int(prefix_padding_ms * 2 * sample_rate / 1000)
        self._preroll = bytearray()
        self.in_speech = False
        #: Speech segments opened so far — read by the call's mic report.
        self.segments = 0
        #: Near-field floor (VAD_MIN_RMS): a speech segment may only *open*
        #: if its trigger chunk is at least this loud. Silero answers "is
        #: somebody speaking", not "is somebody speaking to us" — in the
        #: owner's room it opened the gate for other people's conversations
        #: across the room, and Emma answered words that were never aimed at
        #: her. Loudness is the one signal that separates the person at the
        #: microphone from the rest of the room. 0 = off (the default; the
        #: showroom wants far pickup). Applied only at the opening: once a
        #: segment is open, a sentence trailing quiet must not be chopped.
        self._min_rms = float(min_rms)
        self._floor_logged_at = 0.0
        #: The floor stands down for the session's first few seconds. A
        #: wake-opened session begins with the wake tail — the very speech
        #: that fired the detector, replayed in — and on 2026-08-26 the
        #: floor ate exactly that (rms 0.006 < 0.010): Emma woke, heard
        #: nothing, said nothing, and the owner concluded she wasn't awake.
        #: Whoever opened this session was addressing the machine by
        #: definition; distance-filtering their own opening words is the
        #: floor firing on the one utterance it exists to protect.
        import time

        self._floor_off_until = time.monotonic() + self.FLOOR_GRACE_S

    #: Seconds after opening during which the floor does not apply.
    FLOOR_GRACE_S = 3.0

    def stand_down(self) -> None:
        """Switch the floor off for the rest of this session.

        For a session the *machine* opened — the camera saw somebody at the
        door and Emma greeted them — the floor's premise is inverted. It
        exists to ignore people who are not talking to us; here Emma just
        addressed, by name, a person standing exactly where the floor says
        nobody worth hearing stands. Measured on 2026-08-31: อาซู่ greeted
        at the door, answered, and the session logged nothing — not one
        `heard` — while the page read LISTENING. VAD_MIN_RMS=0.01 sits at
        the top of what the owner's own voice measures *at the desk*
        (0.004-0.012, see config.py), so a voice from the doorway never had
        a chance. The opening grace did not help either: for a summoned
        session those three seconds are spent dialling and generating the
        greeting, gone before the person has anything to answer.
        """
        if self._min_rms > 0.0 and self._floor_off_until != float("inf"):
            logger.info("vad floor: standing down for this session — the "
                        "machine opened it and greeted somebody at a distance")
        self._floor_off_until = float("inf")

    def feed(self, pcm16: bytes) -> list[tuple[str, bytes]]:
        if not pcm16:
            return []
        import numpy as np

        samples = np.frombuffer(pcm16, dtype="<i2").astype("float32") / 32768.0
        self._detector.accept_waveform(samples)
        speaking = bool(self._detector.is_speech_detected())

        import time as _time

        if (speaking and not self.in_speech and self._min_rms > 0.0
                and _time.monotonic() > self._floor_off_until):
            rms = float(np.sqrt((samples * samples).mean()))
            if rms < self._min_rms:
                # Too far away to be talking to us. Treated as silence, so
                # the pre-roll keeps rolling — if the speaker steps closer
                # mid-sentence, the segment opens with its head intact.
                import time

                now = time.monotonic()
                if now - self._floor_logged_at > 5.0:
                    logger.info(
                        "vad floor: speech detected but under VAD_MIN_RMS "
                        "(rms=%.4f < %.3f) — not forwarded. Lower the floor "
                        "if this was the person at the microphone.",
                        rms, self._min_rms,
                    )
                    # Into the turn log too. The console line above is the
                    # only evidence the floor leaves, and the console is
                    # the one thing nobody has when "ไมค์ค้าง" is reported
                    # from a screenshot: a session with speech seen and no
                    # `heard` looks identical whether the browser sent
                    # nothing or the floor ate everything.
                    from app import turnlog

                    turnlog.record("vad_floor", rms=round(rms, 4),
                                   floor=self._min_rms)
                    self._floor_logged_at = now
                speaking = False

        actions: list[tuple[str, bytes]] = []
        if speaking and not self.in_speech:
            self.in_speech = True
            self.segments += 1
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
    logger.info("local VAD active: silence stays here, Gemini hears speech only%s",
                (" | near-field floor %.3f" % settings.vad_min_rms)
                if settings.vad_min_rms > 0 else "")
    return VadGate(detector, prefix_padding_ms=settings.vad_prefix_padding_ms,
                   min_rms=settings.vad_min_rms)


def reset_warnings() -> None:
    """Tests flip settings; the once-only warning must not stay spent."""
    global _warned_missing_model
    _warned_missing_model = False
