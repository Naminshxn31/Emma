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
    # Measured 2026-08-24 with WAKE_DEBUG on: twenty seconds of speech at
    # peak 0.24-0.33 — unambiguously loud, unambiguously the name — passed
    # through these three without a single hit, then fired once later. So
    # the neighbourhood was too small, in the direction the docstring above
    # already guessed. AMA drops the doubled M (Thai says "อะ-หม่า" more
    # openly than English "EMMA"); IMMA raises the first vowel.
    #
    # Deliberately not widened past that. ANNA and ELMA encode cleanly too
    # and are ordinary English words — a wake word that fires on the room's
    # conversation is worse than one that needs saying twice.
    "emma": ["EMMA", "AMMA", "EMA", "AMA", "IMMA"],
}

#: Precomputed BPE for the variants, so the default name works even without
#: sentencepiece installed. Any *other* WAKE_WORD needs sentencepiece.
_KNOWN_ENCODINGS = {
    "EMMA": "▁E M MA",
    "AMMA": "▁A M MA",
    "EMA": "▁E MA",
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
    override = [s.strip().upper() for s in
                (settings.wake_spellings or "").split(",") if s.strip()]
    if override:
        return override
    return _VARIANTS.get(word, [word.upper()])


def _encode_keyword(word: str) -> str | None:
    """The KWS model wants BPE pieces, not letters. One line per spelling
    variant, every variant reporting the same @LABEL.

    `:boost` raises the score of the keyword path while it is being matched,
    `#threshold` is the score it must clear — both straight from the
    sherpa-onnx keywords-file format. `@LABEL` is what get_result returns.
    """
    word = word.strip().lower()
    spellings = _spellings_for(word)
    label = word.upper()

    encoded: list[str] = []
    try:
        import sentencepiece as spm

        sp = spm.SentencePieceProcessor()
        sp.load(str(_model_dir() / "bpe.model"))
        for spelling in spellings:
            encoded.append(" ".join(sp.encode(spelling, out_type=str)))
    except Exception:
        for spelling in spellings:
            pieces = _KNOWN_ENCODINGS.get(spelling)
            if pieces is not None:
                encoded.append(pieces)
        if not encoded:
            logger.warning(
                "cannot encode wake word %r: sentencepiece is not available "
                "and there is no precomputed encoding for it — only %s work "
                "without sentencepiece",
                word, sorted(_VARIANTS),
            )
            return None
    return "\n".join(
        "%s :%.1f #%.2f @%s" % (
            pieces, settings.wake_boost, settings.wake_threshold, label
        )
        for pieces in encoded
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
        import sherpa_onnx

        keywords_path = d / "keywords_active.txt"
        keywords_path.write_text(keyword_line + "\n", encoding="utf-8")
        _spotter = sherpa_onnx.KeywordSpotter(
            tokens=str(d / "tokens.txt"),
            encoder=str(d / "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
            decoder=str(d / "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
            joiner=str(d / "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
            keywords_file=str(keywords_path),
            num_trailing_blanks=1,
        )
        logger.info("wake word ready: %r", settings.wake_word)
    except Exception:
        logger.warning("could not load the wake-word model", exc_info=True)
        _load_failed = True
        _spotter = None
    return _spotter


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
        if settings.wake_debug:
            self._report(samples)
        self._stream.accept_waveform(16000, samples)
        hit = None
        while self._spotter.is_ready(self._stream):
            self._spotter.decode_stream(self._stream)
            result = self._spotter.get_result(self._stream)
            if result:
                hit = result
                self._spotter.reset_stream(self._stream)
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
        self._probe_at = now
        self._probe_peak = 0.0
        self._probe_sum = 0.0
        self._probe_n = 0


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
    global _spotter, _load_failed
    _spotter = None
    _load_failed = False
    _STANDBY.clear()
