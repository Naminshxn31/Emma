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
    "emma": ["EMMA", "AMMA", "EMA"],
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


def _encode_keyword(word: str) -> str | None:
    """The KWS model wants BPE pieces, not letters. One line per spelling
    variant, every variant reporting the same @LABEL.

    `:boost` raises the score of the keyword path while it is being matched,
    `#threshold` is the score it must clear — both straight from the
    sherpa-onnx keywords-file format. `@LABEL` is what get_result returns.
    """
    word = word.strip().lower()
    spellings = _VARIANTS.get(word, [word.upper()])
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

    def __init__(self) -> None:
        self._spotter = _get_spotter()
        self._stream = self._spotter.create_stream() if self._spotter else None

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
        self._stream.accept_waveform(16000, samples)
        hit = None
        while self._spotter.is_ready(self._stream):
            self._spotter.decode_stream(self._stream)
            result = self._spotter.get_result(self._stream)
            if result:
                hit = result
                self._spotter.reset_stream(self._stream)
        return hit


def reset() -> None:
    """Tests swap models and settings; the singleton must not outlive them."""
    global _spotter, _load_failed
    _spotter = None
    _load_failed = False
