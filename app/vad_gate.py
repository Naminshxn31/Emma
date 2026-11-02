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

#: Longest speech segment before the detector closes it regardless of
#: silence. A minute without a 0.9-second pause is not a person asking a
#: question; twenty seconds (the library default) sometimes is.
MAX_SEGMENT_S = 60.0


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
                 min_rms: float = 0.0, floor_grace_s: float = 3.0,
                 focus_ratio: float = 0.0, head_hold_ms: int = 0) -> None:
        self._detector = detector
        self._rate = sample_rate
        #: Speaker focus (VAD_FOCUS_RATIO) — see `_for_detector`. 0 = off.
        self._focus_ratio = max(0.0, float(focus_ratio))
        self._energy = 0.0          # short-window mean square, every frame
        self._focus_level = 0.0     # the speaker's recent level, in speech
        self._focus_quiet_s = 0.0   # trailing run the detector heard as quiet
        self._focus_quiet_rms = 0.0  # loudest thing inside that run
        #: Bytes of pre-roll to keep: ms * (2 bytes/sample) * samples/ms.
        self._preroll_max = int(prefix_padding_ms * 2 * sample_rate / 1000)
        #: VAD_HEAD_HOLD_MS: how much MORE the pre-roll may hold while the
        #: detector hears speech that a door (the floor, listening focus)
        #: has not let through yet. Those doors re-ask on every frame and
        #: the comments below promised "the segment opens then, head
        #: intact" -- but the pre-roll was trimmed to prefix_padding_ms the
        #: whole time, so a sentence let in on its loudest syllable arrived
        #: beheaded. Robot log 28 Oct (the floor-1 media room, no face in
        #: the camera all evening): 73 of 124 opens came within 2 s of a
        #: denial, and a tester's first sentence reached the model as its
        #: last word or two; on 20 Oct English guests' questions arrived
        #: starting mid-sentence (a leading comma, a dangling "is ..."). 0 = the old
        #: 300 ms exactly (the sales-room PC); robot.env sets it.
        self._head_max = int(max(0, int(head_hold_ms)) * 2 * sample_rate / 1000)
        self._preroll = bytearray()
        self.in_speech = False
        #: Speech segments opened so far — read by the call's mic report.
        self.segments = 0
        #: The rms of the frame that opened the latest segment (None before one).
        self.open_rms: float | None = None
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

        self._floor_off_until = time.monotonic() + max(0.0, float(floor_grace_s))
        #: Listening focus (docs/listening-focus-2026-10-09.md): the closed
        #: segment, for whoever attributes it (`take_closed`). `keep_audio`
        #: is set by the session only while voice prints are on — then the
        #: bytes forwarded for the segment are kept until it closes, capped
        #: at MAX_SEGMENT_S (the detector's own valve) so memory is bounded.
        #: Off, nothing but two timestamps is held.
        self.keep_audio = False
        self._seg_t0 = 0.0
        self._seg_audio: bytearray | None = None
        self._closed: tuple[int, float, float, bytes] | None = None
        self._focus_logged_at = 0.0

    #: Seconds after opening during which the floor does not apply.
    FLOOR_GRACE_S = 3.0
    #: The floor under the floor. A muted or dead microphone reads exact
    #: zeros with a 1-LSB dither at the edges (peak 0.0001), and Silero
    #: flagged those edges as speech: measured 2026-09-01 14:35-14:38, a
    #: summoned session (near-field floor stood down) opened eight
    #: segments on a hardware-muted USB mic and Gemini answered the
    #: silence with "I want to know the price of the product." five times
    #: — 我们走吧 in English. The quietest real speech at the desk is
    #: 0.004-0.012 rms (config.py); this is ten times under that and five
    #: times over the dither. Applies always: stand_down lowers the
    #: near-field floor to *this*, never to nothing.
    SILENCE_RMS = 0.0005

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

    #: Smoothing for the focus level: about a syllable, so the gaps between
    #: consonants are not "quiet" but a finished sentence is.
    FOCUS_WINDOW_S = 0.1
    #: How fast the speaker's reference level relaxes — so someone who
    #: opens loud ("Emma!") and goes on at a normal voice is not measured
    #: against the shout. Measured: no decay at 0.30 split one real call
    #: segment; 3 s split none and still ended over -14 dB background.
    FOCUS_DECAY_S = 3.0

    def _for_detector(self, samples):
        """What the detector hears: the frame itself, or silence.

        **Speaker focus.** Silero answers "is anybody speaking", so in a
        gallery the end of a guest's turn waited for the *room* to go quiet:
        measured 2026-10-08 with the real model, a guest's 6.8 s question
        over other people talking 14 dB lower got its activity_end only when
        that talk stopped — 8 s later in the test mix, and with the 60 s
        segment valve, possibly a minute in a busy room, while the guest
        stood waiting for an answer that was never coming. The owner's ask
        ("โฟกัสเสียงคนนั้นที่กำลังพูดอยู่ พอพูดจบก็ตอบเลย") is exactly this.

        Once a segment is open, a frame much quieter than the person who
        opened it is fed to the detector as silence, so the one hangover
        clock (min_silence_duration) runs on *their* silence. Nothing is
        chopped: the real audio is still forwarded upstream — only the turn
        ends sooner. Not a second clock, and not the opening floor: this
        never decides whether a segment opens.

        Measured on 11.3 min of real call audio (51 segments): 0.30 split
        none; 0.35 split one, the same speaker continuing 4 dB softer after
        a 0.46 s pause — the margin is real but thin, hence the knob.
        """
        import math

        import numpy as np

        dt = len(samples) / self._rate
        mean_square = float((samples * samples).mean())
        self._energy += (1.0 - math.exp(-dt / self.FOCUS_WINDOW_S)) * (mean_square - self._energy)
        if not (self.in_speech and self._focus_ratio > 0.0):
            return samples
        smoothed = math.sqrt(max(self._energy, 0.0))
        self._focus_level = max(smoothed,
                                self._focus_level * math.exp(-dt / self.FOCUS_DECAY_S))
        if smoothed >= self._focus_ratio * self._focus_level:
            self._focus_quiet_s = 0.0
            self._focus_quiet_rms = 0.0
            return samples
        self._focus_quiet_s += dt
        self._focus_quiet_rms = max(self._focus_quiet_rms, smoothed)
        return np.zeros_like(samples)

    def _report_focus_end(self) -> None:
        """A number-only trace when focus ended a turn over real sound.

        Ending on true silence needs no record; ending while something was
        still audible (another conversation, a TV) is the case to tune
        VAD_FOCUS_RATIO from — "vad floor: ... rms=X" taught that a knob
        nobody can see the effect of gets tuned from theory.
        """
        if self._focus_quiet_s <= 0.0 or self._focus_quiet_rms < self.SILENCE_RMS * 4:
            return
        from app import turnlog

        turnlog.record("vad_focus_end",
                       quiet_ms=round(self._focus_quiet_s * 1000),
                       residual=round(self._focus_quiet_rms / max(self._focus_level, 1e-9), 3),
                       ratio=self._focus_ratio)

    def _drop_finished_segments(self) -> None:
        """Discard the audio sherpa-onnx keeps for every finished segment.

        The detector is also a recorder: each speech segment it closes is
        queued, samples and all, for a caller to `pop()`. This gate only
        ever asks `is_speech_detected()`, so nothing was popped — measured
        2026-10-07 with the real model, five utterances of the committed
        TTS left 11.2 s of speech (64 KB per second of a guest's voice)
        queued in the session, growing for its whole life and across every
        resume. Nothing reads it; it is a guest's voice held for no reason.
        Scripted test detectors have no queue, hence the getattr.
        """
        empty = getattr(self._detector, "empty", None)
        if empty is None:
            return
        while not empty():
            self._detector.pop()

    def feed(self, pcm16: bytes) -> list[tuple[str, bytes]]:
        if not pcm16:
            return []
        import numpy as np

        samples = np.frombuffer(pcm16, dtype="<i2").astype("float32") / 32768.0
        self._detector.accept_waveform(self._for_detector(samples))
        speaking = bool(self._detector.is_speech_detected())
        # What the detector said, before any door below says no to it: the
        # pre-roll may grow past prefix_padding_ms only while this is true.
        detector_speech = speaking
        self._drop_finished_segments()

        import time as _time

        if speaking and not self.in_speech:
            rms = float(np.sqrt((samples * samples).mean()))
            if rms < self.SILENCE_RMS:
                # Not "far away" — nothing. A segment opened here sends
                # silence-shaped bytes upstream, which is the one input a
                # speech model answers with a sentence nobody said.
                import time

                now = time.monotonic()
                if now - self._floor_logged_at > 5.0:
                    logger.info("vad: speech flagged on near-silent audio "
                                "(rms=%.5f < %.4f) — a muted or dead microphone, "
                                "not a voice; not forwarded", rms, self.SILENCE_RMS)
                    from app import turnlog

                    turnlog.record("vad_floor", rms=round(rms, 5),
                                   floor=self.SILENCE_RMS, why="silence")
                    self._floor_logged_at = now
                speaking = False
        if (speaking and not self.in_speech and self._min_rms > 0.0
                and _time.monotonic() > self._floor_off_until):
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
        if speaking and not self.in_speech:
            # Listening focus: the third test at the same door, after
            # silence and distance — is somebody in front of the robot the
            # one talking? Same shape as the floor on purpose: a "no" is
            # silence, the pre-roll keeps rolling, nothing goes upstream,
            # and the next frame asks again (a face turning toward the robot
            # mid-sentence opens the segment then, head intact). Asked only
            # when the switch is on; off, this branch is one attribute read.
            from app import focus

            if focus.enabled():
                allow, why = focus.may_open(_time.monotonic(), rms=rms)
                if not allow:
                    now = _time.monotonic()
                    if now - self._focus_logged_at > 5.0:
                        logger.info("vad focus: speech not forwarded — %s (rms=%.4f)",
                                    why, rms)
                        self._focus_logged_at = now
                    speaking = False

        actions: list[tuple[str, bytes]] = []
        if speaking and not self.in_speech:
            self.in_speech = True
            self.segments += 1
            # how loud the voice that opened this segment was (the turn log's
            # focus_open rms): the noise-reply gate's "near or far"
            self.open_rms = rms
            # The opener sets the reference — the person this turn belongs to.
            self._focus_level = float(np.sqrt(max(self._energy, 0.0)))
            self._focus_quiet_s = 0.0
            self._focus_quiet_rms = 0.0
            # Where the forwarded audio starts: the pre-roll is older than
            # this frame by its own length.
            self._seg_t0 = _time.monotonic() - len(self._preroll) / (2.0 * self._rate)
            if self.keep_audio:
                self._seg_audio = bytearray(self._preroll)
                self._seg_audio.extend(pcm16)
            actions.append(("start", b""))
            if self._preroll:
                actions.append(("audio", bytes(self._preroll)))
                self._preroll.clear()
            actions.append(("audio", pcm16))
        elif speaking:
            self._keep_segment_audio(pcm16)
            actions.append(("audio", pcm16))
        elif self.in_speech:
            # The detector's own min_silence_duration is the hangover: it
            # only flips false after the configured quiet stretch, so there
            # is no second timer here. Two clocks for one decision is this
            # project's most repeated bug.
            self.in_speech = False
            self._report_focus_end()
            self._keep_segment_audio(pcm16)
            audio, self._seg_audio = self._seg_audio, None
            self._closed = (self.segments, self._seg_t0, _time.monotonic(),
                            bytes(audio) if audio else b"")
            actions.append(("audio", pcm16))   # the tail that flipped it
            actions.append(("end", b""))
        else:
            self._preroll.extend(pcm16)
            # Speech a door is still refusing is the head of a sentence the
            # door may let in on its next frame (a louder syllable, a face
            # turning): kept, up to VAD_HEAD_HOLD_MS more. The detector's
            # "no" ends the run and the buffer shrinks back to the pre-roll
            # on this very frame, so a refused run never reaches the next one.
            cap = self._preroll_max + (self._head_max if detector_speech else 0)
            if len(self._preroll) > cap:
                del self._preroll[:len(self._preroll) - cap]
        return actions

    def _keep_segment_audio(self, pcm16: bytes) -> None:
        audio = self._seg_audio
        if audio is not None and len(audio) < int((MAX_SEGMENT_S + 1.0) * 2 * self._rate):
            audio.extend(pcm16)

    def take_closed(self) -> tuple[int, float, float, bytes] | None:
        """The segment the last `("end", ...)` closed, once: (segment number
        in this session, t0, t1, pcm16). t0..t1 (time.monotonic) spans what
        was forwarded — pre-roll through the frame that closed it, the
        VAD_SILENCE_MS hangover included. pcm16 is exactly those bytes when
        `keep_audio` was on, else b""."""
        closed, self._closed = self._closed, None
        return closed


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
        # 100 ms admitted desk taps and browser/USB transients as one-word
        # turns (measured as the phantom transcript "FB1"). Wait for sustained
        # speech; prefix padding keeps the first syllable in the sent audio.
        cfg.silero_vad.min_speech_duration = max(0, settings.vad_min_speech_ms) / 1000
        # The second clock nobody set. sherpa-onnx force-closes a segment at
        # max_speech_duration (library default 20 s) whatever the silence
        # says: measured 2026-10-07 with the real model, continuous TTS
        # speech with 300 ms pauses got activity_end at 20.8 s and a new
        # activity_start 0.5 s later — Gemini answers the first twenty
        # seconds while the guest is still mid-sentence, and with
        # HALF_DUPLEX the rest of what they say is dropped under her reply
        # (or, with HOLD_SPEECH, held and answered as a second question).
        # The end of a turn belongs to min_silence_duration alone (see the
        # hangover comment in VadGate.feed). Kept finite as a valve for a
        # source that never pauses — a TV, a crowd — not as a turn length.
        cfg.silero_vad.max_speech_duration = MAX_SEGMENT_S
        cfg.sample_rate = 16000
        # Sized to hold a whole segment: below that the detector grows its
        # buffer mid-speech and prints "Overflow!" to the console.
        return sherpa_onnx.VoiceActivityDetector(
            cfg, buffer_size_in_seconds=int(MAX_SEGMENT_S) + 10)
    except Exception:
        logger.exception("could not create the local VAD — falling back to "
                         "Gemini's own detection")
        return None


def for_session(min_rms: float | None = None):
    """The gate for a new session, or None when the mode is off/unavailable.

    Per-session on purpose: the detector carries decoder state, and two
    sessions sharing one would hear each other's tails — the same isolation
    rule the wake word's per-connection streams follow.

    `min_rms` is this call's floor (`settings.call_vad_floor(profile)` — a
    far-listening mode opens with its own); None = VAD_MIN_RMS as before.
    """
    if settings.vad_mode != "local":
        return None
    detector = _make_detector()
    if detector is None:
        return None
    floor = settings.vad_min_rms if min_rms is None else float(min_rms)
    logger.info("local VAD active: silence stays here, Gemini hears speech only%s",
                (" | near-field floor %.3f" % floor) if floor > 0 else " | no loudness floor")
    return VadGate(detector, prefix_padding_ms=settings.vad_prefix_padding_ms,
                   min_rms=floor,
                   floor_grace_s=settings.vad_floor_grace_s,
                   focus_ratio=settings.vad_focus_ratio,
                   head_hold_ms=getattr(settings, "vad_head_hold_ms", 0))


def reset_warnings() -> None:
    """Tests flip settings; the once-only warning must not stay spent."""
    global _warned_missing_model
    _warned_missing_model = False
