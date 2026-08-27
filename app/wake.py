"""
Hearing the assistant's name, without paying anyone to listen for it.

The money problem this solves is written in the roadmap: a Gemini Live
session left open all day in an empty room burns the daily quota for
nothing. The wake word is what makes "always on" financially real — the
session opens when someone says "Emma" and closes when the room goes quiet
(`IDLE_TIMEOUT_S`), so the paid part of the pipeline only runs while a
conversation is actually happening. Privacy falls out of the same design:
until the name is heard, audio reaches this process and nothing else.

Why sherpa-onnx keyword spotting and not openwakeword: the plan assumed
openwakeword's ready-made models, but its shelf has `hey_jarvis` and no
`emma`, and a custom openwakeword model means a synthetic-training pipeline
nobody here wants to own. sherpa-onnx KWS takes the keyword as *text*,
encoded with the model's own BPE at startup — changing the name is an .env
edit, not a training run. Measured on this machine before committing to it:
~15ms of compute per second of audio, "Emma" alone and mid-sentence both
detected, unrelated speech rejected.

The detector is deliberately per-connection state, module-level singleton
factory: one browser holds one stream. If a second wake connection appears
(a reloaded tab racing its own ghost), each gets its own stream and they
don't share decoder state.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger("condo_voice.wake")

#: Spelling variants per wake word, all reported under one label. The KWS
#: model maps *text* to sound through English training data, and the owner
#: says the name with a Thai mouth — "เอ็มม่า" lands somewhere between EMMA,
#: AMMA and EMA depending on distance and emphasis. One spelling misses real
#: calls; the variants catch the neighbourhood. (First real-mic session:
#: TTS-tested EMMA alone did not fire on a live "เอ็มม่า".)
_VARIANTS = {
    # Two spellings, not five — measured 2026-08-25 against Thai neural-TTS
    # renderings of the name (both th-TH voices, three written forms, three
    # speaking rates; wavs in tests/data/wake/). The previous five-spelling
    # list scored 5/8 with a false positive; this pair at threshold 0.10
    # scores 7/8 with none. The counterintuitive part, kept here so nobody
    # "improves" it back: **spellings compete inside one decoder beam, so
    # adding weak ones makes the good ones worse.** With all five loaded,
    # the plain female "เอ็มม่า" — the exact call the owner reported missing
    # — was caught by nothing; with just these two, EMMA catches it.
    #
    # Of the removed three: AMMA and EMA matched zero probes at any
    # threshold (dead weight in the beam), and AMA fired on the English
    # phrase "a comma". IMMA earns its slot as the only spelling that heard
    # the short female "เอ็มม่า" at every threshold tried.
    #
    # Known remaining miss: a deep male voice saying the name at the head of
    # a sentence (7/8). No spelling or boost reached it — boost 8 made
    # everything worse — so it is accepted, not unknown.
    #
    # The third slot is earned from *real* clips, not TTS: the owner's mouth
    # renders "เอ็มม่า" as "เอ็มอา/อิหม่า" (Gemini-transcribed miss clips,
    # 2026-08-26) — a softened /m/ coda no TTS produces. First filled with
    # AH MA (caught the compressed-chain clips); re-measured after the
    # standby compressor came out and A MA won the rematch: it hears the
    # clean-chain "เอ็มม่า" up to bar 0.08 (highest of 22 candidates) with
    # the same false-fire profile. (Both plausibly match "อาม่า", grandma —
    # on the owner's home machine that trade was taken knowingly.)
    #
    # The same clips also bounded what spelling tuning can do: about half of
    # the real failed attempts were speech even *Gemini* could not transcribe
    # — too quiet or too far. No keyword list fixes those; the microphone does.
    "emma": ["EMMA", "IMMA", "A MA"],
}

#: Per-spelling threshold scale: this spelling's bar = WAKE_THRESHOLD × the
#: factor. A *scale* rather than an absolute so WAKE_THRESHOLD stays the one
#: knob — raise it against false fires and every spelling rises with it.
#:
#: Added after the first live morning with the shadow ear (2026-08-26): a
#: real "เอ็มม่า" NEAR-MISSED — heard, scored under 0.10 — and the owner
#: reported saying the name several times per wake. The measured combination
#: that catches more without new false fires is EMMA at half the bar with
#: IMMA staying at it: EMMA@0.05 *alone* fires on "อิ่มมาก", but with IMMA
#: present at 0.10 the beam gives that utterance to the IMMA path, which
#: then rejects it — measured clean on the whole negative set, and guarded
#: by the other_imm_trap test. Halving IMMA as well brings the false fire
#: back (measured); resist the symmetry.
_SPELLING_SCALE = {
    "EMMA": 0.5,
    # 0.8, from the rematch on clean-chain clips: the confirmed "เอ็มม่า"
    # scores just under the full bar on this path (hit at 0.08, miss at
    # 0.10) and the false-fire profile at 0.8x measured identical to 1.0x —
    # margin that costs real catches is not margin.
    "A MA": 0.8,
}

#: Precomputed BPE for the variants, so the default name works even without
#: sentencepiece installed. Any *other* WAKE_WORD needs sentencepiece.
_KNOWN_ENCODINGS = {
    "EMMA": "▁E M MA",
    "IMMA": "▁I M MA",
    "A MA": "▁A ▁MA",
}

_spotter = None
_load_failed = False


def _model_dir() -> Path:
    return Path(settings.wake_model_dir).expanduser()


def _spellings_for(word: str) -> list[str]:
    """Which spellings of the name to listen for.

    `WAKE_SPELLINGS` overrides the built-in list, because tuning this is the
    one part of the wake word that cannot be done from here: it depends on a
    particular mouth, a particular room and a particular microphone. The
    built-ins were still missing a live "เอ็มม่า" after a first round of
    guessing, and the next round should not need a code change and a pull.

    Turn on `WAKE_DEBUG` while tuning — it says whether the microphone is
    even reaching the detector, which is the question that has to be settled
    before any spelling can be judged.
    """
    override = [s.strip().upper().split(":", 1)[0] for s in
                (settings.wake_spellings or "").split(",") if s.strip()]
    if override:
        return override
    return _VARIANTS.get(word, [word.upper()])


def _override_thresholds() -> dict[str, float]:
    """Per-spelling thresholds from WAKE_SPELLINGS' `NAME:0.05` syntax.

    Same reason the list itself is tunable from .env: which bar each
    spelling deserves depends on a mouth and a room, and the built-in table
    was itself corrected from a live morning's log. A malformed number is
    skipped (the spelling still listens, at the default bar) rather than
    taking the whole wake word down.
    """
    out: dict[str, float] = {}
    for part in (settings.wake_spellings or "").split(","):
        if ":" not in part:
            continue
        name, _, value = part.strip().upper().partition(":")
        try:
            out[name.strip()] = float(value)
        except ValueError:
            logger.warning("WAKE_SPELLINGS: %r is not a number — %s listens "
                           "at WAKE_THRESHOLD instead", value, name.strip())
    return out


def _encode_keyword(word: str, threshold: float | None = None) -> str | None:
    """The KWS model wants BPE pieces, not letters. One line per spelling
    variant, every variant reporting the same @LABEL.

    `:boost` raises the score of the keyword path while it is being matched,
    `#threshold` is the score it must clear — both straight from the
    sherpa-onnx keywords-file format. `@LABEL` is what get_result returns.

    `threshold` overrides the configured one — the shadow detector listens
    for the same spellings at a floor value.
    """
    word = word.strip().lower()
    spellings = _spellings_for(word)
    label = word.upper()

    pairs: list[tuple[str, str]] = []
    try:
        import sentencepiece as spm

        sp = spm.SentencePieceProcessor()
        sp.load(str(_model_dir() / "bpe.model"))
        for spelling in spellings:
            pairs.append((spelling, " ".join(sp.encode(spelling, out_type=str))))
    except Exception:
        for spelling in spellings:
            pieces = _KNOWN_ENCODINGS.get(spelling)
            if pieces is not None:
                pairs.append((spelling, pieces))
        if not pairs:
            logger.warning(
                "cannot encode wake word %r: sentencepiece is not available "
                "and there is no precomputed encoding for it — only %s work "
                "without sentencepiece",
                word, sorted(_VARIANTS),
            )
            return None

    overrides = _override_thresholds()

    def bar(spelling: str) -> float:
        # An explicit argument (the shadow's floor) flattens everything;
        # otherwise WAKE_SPELLINGS' `NAME:0.05` wins as an absolute, then
        # WAKE_THRESHOLD scaled by the built-in per-spelling factor.
        if threshold is not None:
            return threshold
        if spelling in overrides:
            return overrides[spelling]
        return settings.wake_threshold * _SPELLING_SCALE.get(spelling, 1.0)

    return "\n".join(
        "%s :%.1f #%.2f @%s" % (
            pieces, settings.wake_boost, bar(spelling), label
        )
        for spelling, pieces in pairs
    )


def available() -> bool:
    """Can this machine hear its name? Cheap after the first call."""
    return _get_spotter() is not None


def _get_spotter():
    global _spotter, _load_failed
    if _spotter is not None or _load_failed:
        return _spotter
    if not settings.wake_enabled:
        return None

    d = _model_dir()
    if not (d / "tokens.txt").exists():
        # Not an error: the model is a one-time 15MB download that a fresh
        # clone won't have. Name the command, so the log is the fix.
        logger.warning(
            "wake word is enabled but the model is missing at %s — "
            "run: python scripts/fetch_wake_model.py", d,
        )
        _load_failed = True
        return None

    keyword_line = _encode_keyword(settings.wake_word)
    if keyword_line is None:
        _load_failed = True
        return None

    try:
        _spotter = _make_spotter(keyword_line, "keywords_active.txt")
        logger.info("wake word ready: %r", settings.wake_word)
    except Exception:
        logger.warning("could not load the wake-word model", exc_info=True)
        _load_failed = True
        _spotter = None
    return _spotter


def _make_spotter(keyword_line: str, filename: str):
    import sherpa_onnx

    d = _model_dir()
    keywords_path = d / filename
    keywords_path.write_text(keyword_line + "\n", encoding="utf-8")
    return sherpa_onnx.KeywordSpotter(
        tokens=str(d / "tokens.txt"),
        encoder=str(d / "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        decoder=str(d / "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        joiner=str(d / "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        keywords_file=str(keywords_path),
        num_trailing_blanks=1,
    )


#: The score floor the shadow detector listens at. Not zero: at 0.0 every
#: keyword path fires constantly and the near-miss signal means nothing.
SHADOW_THRESHOLD = 0.02

_shadow_spotter = None
_shadow_failed = False


def _get_shadow_spotter():
    """The same spellings at the floor threshold — WAKE_DEBUG's second ear.

    The question WAKE_DEBUG could not answer was the detector-side half:
    the RMS report proves a voice reached the socket, and then a missed
    name is still two different problems — "scored just under the
    threshold" (fix: lower WAKE_THRESHOLD a step) and "never resembled the
    keyword at all" (fix: pronunciation or WAKE_SPELLINGS, and no threshold
    will help). sherpa's spotter returns no score, so the shadow answers by
    construction: it hears everything above the floor, and a shadow hit
    with no real hit is, by definition, a score in the gap.
    """
    global _shadow_spotter, _shadow_failed
    if _shadow_spotter is not None or _shadow_failed:
        return _shadow_spotter
    keyword_line = _encode_keyword(settings.wake_word,
                                   threshold=SHADOW_THRESHOLD)
    if keyword_line is None:
        _shadow_failed = True
        return None
    try:
        _shadow_spotter = _make_spotter(keyword_line, "keywords_shadow.txt")
    except Exception:
        logger.warning("could not build the shadow detector", exc_info=True)
        _shadow_failed = True
    return _shadow_spotter


class WakeStream:
    """One browser's ears. Feed it PCM16 mono 16kHz; it says the name back
    when it hears it."""

    #: Speech, measured on this project's own microphone chain, sits around
    #: 0.05-0.2 RMS. The page's own meter calls anything above 0.02 speech.
    #: Below ~0.005 is a room with nobody in it — or an input that is not
    #: the one being spoken into.
    QUIET = 0.005
    SPEECH = 0.02

    def __init__(self) -> None:
        self._spotter = _get_spotter()
        self._stream = self._spotter.create_stream() if self._spotter else None
        self._probe_at = 0.0
        self._probe_peak = 0.0
        self._probe_sum = 0.0
        self._probe_n = 0
        self._heard_anything = False
        # WAKE_DEBUG's second ear: same spellings at the floor threshold.
        # Only while debugging — it is a second full decode of every frame.
        self._shadow = (_get_shadow_spotter()
                        if settings.wake_debug and self._spotter else None)
        self._shadow_stream = (self._shadow.create_stream()
                               if self._shadow else None)
        # WAKE_DEBUG's third tool: keep what the detector actually heard.
        # The morning of 2026-08-26 exhausted the score-side diagnostics —
        # real calls of the name scored under even the shadow's floor, while
        # Thai-TTS renderings pass every test. The one thing that separates
        # those worlds is the audio itself (mic + room + the browser's
        # compressor/AGC/NS chain), and no amount of staring at thresholds
        # reveals a waveform. So under debug, speech that produces no hit is
        # written to data/wake_debug/ for offline analysis against the real
        # voice. Owner's own machine, debug mode only, pruned to the newest
        # twenty clips — this is a tuning instrument, not a recorder.
        # WAKE_ENROLL widens the capture from "misses only" to "every speech
        # window, hits included" — the collection step for teaching the
        # matcher the owner's own voice. A hit is the *best* enrollment
        # sample there is, which is exactly why the miss-only rule flips.
        self._cap = (bytearray()
                     if settings.wake_debug or settings.wake_enroll else None)
        self._cap_max = 16000 * 2 * 6          # the last ~6 seconds
        self._hit_this_window = False
        if settings.wake_enroll:
            logger.warning(
                "WAKE_ENROLL is on: every speech window on this standby "
                "socket is being saved to %s. Say the name 10-15 times, "
                "then turn it off.", settings.wake_enroll_dir)

    @property
    def ok(self) -> bool:
        return self._stream is not None

    def feed(self, pcm16: bytes) -> str | None:
        """Returns the keyword label on detection, else None.

        `reset_stream` after a hit follows the upstream examples. Measured
        here with and without on the fixture audio: behaviour is identical —
        `get_result` clears itself per detection — so the reset is belt and
        braces against decoder state leaking between detections, not the
        thing preventing a repeat-fire. Recorded so nobody "proves" it
        load-bearing later with a test that can only stay green.
        """
        if self._stream is None or not pcm16:
            return None
        import numpy as np

        samples = (
            np.frombuffer(pcm16, dtype="<i2").astype("float32") / 32768.0
        )
        if self._cap is not None:
            self._cap.extend(pcm16)
            if len(self._cap) > self._cap_max:
                del self._cap[:len(self._cap) - self._cap_max]
        self._stream.accept_waveform(16000, samples)
        hit = None
        while self._spotter.is_ready(self._stream):
            self._spotter.decode_stream(self._stream)
            result = self._spotter.get_result(self._stream)
            if result:
                hit = result
                self._spotter.reset_stream(self._stream)
        if hit and self._cap is not None:
            self._hit_this_window = True
            if settings.wake_enroll:
                # The browser closes this socket right after a hit (one
                # detection, one session), so no report tick will follow —
                # and a successful call is the best enrollment sample there
                # is. Save it now or never.
                self._save_clip("enroll")
            elif settings.wake_debug:
                # A hit clip, because a FALSE wake is a hit too. 2026-08-27
                # 08:52: room chatter near-missed for 15 straight seconds,
                # then fired — Emma greeted a conversation nobody was having
                # with her — and the one clip that could say which spelling
                # fired on what sound was the one this branch used to throw
                # away ("a successful wake is not a miss"). The miss clips
                # alone could not reproduce the hit offline.
                self._save_clip("hit")
            else:
                self._cap.clear()
        if settings.wake_debug or settings.wake_enroll:
            # After decoding, so the report tick can tell a window with a
            # hit from a window of speech that produced nothing. Enrollment
            # rides the same tick — it must not depend on WAKE_DEBUG also
            # happening to be on (it was, on the machine this was built on,
            # which is exactly how that coupling would have shipped unseen).
            self._report(samples)
        if self._shadow_stream is not None:
            shadow_hit = None
            self._shadow_stream.accept_waveform(16000, samples)
            while self._shadow.is_ready(self._shadow_stream):
                self._shadow.decode_stream(self._shadow_stream)
                result = self._shadow.get_result(self._shadow_stream)
                if result:
                    shadow_hit = result
                    self._shadow.reset_stream(self._shadow_stream)
            if shadow_hit and not hit:
                # The gap, caught in the act: the name was recognisable at
                # the floor but scored under WAKE_THRESHOLD. This line is
                # the difference between "lower the threshold a step" and
                # "no threshold will help" — before it, both looked like
                # nothing happening.
                logger.warning(
                    "wake NEAR MISS: %r scored between %.2f and %.2f — the "
                    "name was heard but not accepted. Lower WAKE_THRESHOLD "
                    "one step (e.g. -0.02) if this keeps appearing on real "
                    "calls of the name.",
                    shadow_hit, SHADOW_THRESHOLD, settings.wake_threshold,
                )
                from app import turnlog

                turnlog.record("wake_near_miss", word=shadow_hit,
                               threshold=settings.wake_threshold)
        if hit:
            logger.info("wake word heard: %r", hit)
        return hit

    def _report(self, samples) -> None:
        """Say what the microphone is sending, roughly twice a minute.

        Deliberately reports the *audio*, not the detector's confidence.
        Confidence answers "was that the name", and the question that could
        not be answered was the one underneath it: is there a voice in here
        at all. A near-miss score and a silent room are different problems
        with different fixes, and only one of them is about the wake word.
        """
        import math
        import time

        n = len(samples)
        if not n:
            return
        rms = math.sqrt(float((samples * samples).sum()) / n)
        self._probe_peak = max(self._probe_peak, rms)
        self._probe_sum += rms
        self._probe_n += 1
        if rms >= self.SPEECH:
            self._heard_anything = True

        now = time.monotonic()
        if not self._probe_at:
            self._probe_at = now
            return
        if now - self._probe_at < 2.0:
            return
        avg = self._probe_sum / self._probe_n
        if self._probe_peak < self.QUIET:
            verdict = ("SILENT — nothing is reaching this socket. Check which "
                       "input device the browser picked (the mic icon in the "
                       "address bar), and that it is not muted in Windows")
        elif self._probe_peak < self.SPEECH:
            verdict = ("too quiet to be speech — room noise only. Move closer, "
                       "or raise MIC_BOOST in .env")
        else:
            verdict = "speech level reached — if the name still misses, it is the keyword, not the microphone"
        logger.info("wake audio: avg=%.4f peak=%.4f (speech >= %.2f) — %s",
                    avg, self._probe_peak, self.SPEECH, verdict)
        if self._cap and self._probe_peak >= self.SPEECH:
            if settings.wake_enroll:
                # Collection mode: every speech window, hit or not.
                self._save_clip("enroll")
            elif not self._hit_this_window:
                self._save_clip("miss")
        self._hit_this_window = False
        self._probe_at = now
        self._probe_peak = 0.0
        self._probe_sum = 0.0
        self._probe_n = 0

    def _save_clip(self, kind: str) -> None:
        """Keep what the detector actually heard, for offline analysis.

        "miss" (WAKE_DEBUG): speech that fired nothing — the recording of
        exactly the thing every threshold and spelling had been tuned
        *around* instead of *against*. "enroll" (WAKE_ENROLL): every speech
        window, hits included — the owner teaching the matcher their own
        voice. Written locally only, pruned, never on any log clock.
        """
        import time
        import wave
        from pathlib import Path

        directory, keep = ((settings.wake_enroll_dir, 60) if kind == "enroll"
                           else (settings.wake_debug_dir, 20))
        try:
            d = Path(directory).expanduser()
            d.mkdir(parents=True, exist_ok=True)
            path = d / time.strftime(f"{kind}-%Y%m%d-%H%M%S.wav")
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(bytes(self._cap))
            self._cap.clear()
            clips = sorted(d.glob(f"{kind}-*.wav"))
            for old in clips[:-keep]:
                old.unlink(missing_ok=True)
            logger.info("wake %s clip saved: %s", kind, path)
        except Exception:
            logger.exception("could not save the wake %s clip", kind)


#: Standby browsers currently holding a /ws/wake socket. Normally the wake
#: flow is one-directional — audio in, "wake" out on detection — but a
#: reminder falling due while the line is parked needs the *server* to be
#: able to ring the room: summon() pushes the same {"type": "wake"} the
#: keyword would have produced, and the browser dials exactly as if the
#: owner had said the name.
_STANDBY: set = set()


def register(ws) -> None:
    _STANDBY.add(ws)


def unregister(ws) -> None:
    _STANDBY.discard(ws)


async def summon(reason: str = "server") -> int:
    """Ask every standby browser to open a session. Returns how many heard.

    Dead sockets are dropped rather than raised on — a tab that vanished
    without closing is the normal case, not the exceptional one.
    """
    import json

    delivered = 0
    for ws in list(_STANDBY):
        try:
            await ws.send_text(json.dumps(
                {"type": "wake", "word": settings.wake_word, "reason": reason}
            ))
            delivered += 1
        except Exception:
            _STANDBY.discard(ws)
    return delivered


def reset() -> None:
    """Tests swap models and settings; the singleton must not outlive them."""
    global _spotter, _load_failed, _shadow_spotter, _shadow_failed
    _spotter = None
    _load_failed = False
    _shadow_spotter = None
    _shadow_failed = False
    _STANDBY.clear()
