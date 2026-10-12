"""Light speech enhancement on the call stream, before the VAD (ENHANCE_ENABLED).

sherpa-onnx's streaming GTCRN denoiser (16 kHz, 256-sample shift, 0.056x
real time on one core of the server PC), one per session -- it carries
state, and two calls sharing one would hear each other's tails (the VAD's
isolation rule). Off by default. Measured 9 Oct 2026 with
scripts/eval_enhance.py (Silero with the call's settings, 20 ms frames, two
TTS sets: 40 s of SAPI voices / the 30 s committed in tests/data/wake):

* Clean speech goes through unharmed: SI-SDR 36.0 / 33.9 dB, VAD identical.
* Stationary (pink) noise -- a fan, the HVAC: it helps every time. At 0 dB
  recall 0.91 -> 0.99 / 0.19 -> 1.00; at 10-20 dB the VAD's false opens in
  the target's silences 0.10-0.37 -> 0.00 and the delay from the end of a
  turn to the VAD closing 160-940 ms -> 0-40 ms; SI-SDR +5 to +8 dB.
* Other people talking -- the robot's actual problem -- at 0-5 dB: nothing
  (false opens 0.96-0.98 either way: babble IS speech, a denoiser keeps it).
  At 10-20 dB it depends on the voices: through the VadGate the SAPI set's
  turn end fell 9.5 s -> 0.19 s at 10 dB, the committed set got WORSE
  (false 0.64 -> 0.86 at 20 dB, SI-SDR 15.5 -> 6.1 dB).
* Babble + noise at 0 dB: worse in both sets (false 0.53 / 0.74 -> 0.95 /
  0.98): it takes the noise away and leaves the crowd for Silero to follow.

So: not the tool for "many people around" (that is focus and the beam), and
inconsistent where it might help; worth an A/B only where the noise is a
machine. Never with the page's own noise suppression on as well (?ns=0):
two suppressors in a row compound their artifacts.

Placement: after the source switch (page or array), before mic_report and
the VAD -- so the VAD, the call recording and Gemini all get the same
bytes, and mic_report's levels are the levels the floor applies to.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from app.config import settings

logger = logging.getLogger("condo_voice.enhance")

_WARNED: set[str] = set()
RATE = 16000


def _once(key: str, message: str, *args) -> None:
    if key not in _WARNED:
        _WARNED.add(key)
        logger.warning(message, *args)


class Enhancer:
    """bytes in, bytes out. The denoiser returns whole 256-sample shifts, so
    one 20 ms page frame can come back empty and the next with 32 ms: the
    caller sends nothing for an empty one (the stream stays continuous)."""

    def __init__(self, denoiser) -> None:
        self._denoiser = denoiser
        self.frames_in = 0
        self.frames_out = 0

    def process(self, pcm16: bytes) -> bytes:
        if not pcm16:
            return b""
        x = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
        self.frames_in += len(x)
        y = np.asarray(self._denoiser(x, RATE).samples, dtype=np.float32)
        self.frames_out += len(y)
        if not len(y):
            return b""
        return np.clip(np.round(y * 32768.0), -32768, 32767).astype("<i2").tobytes()


def make_denoiser(model: str | Path):
    """A sherpa-onnx OnlineSpeechDenoiser on the GTCRN model, or None with a
    loud reason (a switch that is on and silently does nothing is the
    misconfiguration this project keeps paying for)."""
    path = Path(model).expanduser()
    if not path.is_file():
        _once("model", "ENHANCE_ENABLED but %s is missing — the call is NOT enhanced. "
                       "Fetch it once with: python scripts/fetch_enhance_model.py", path)
        return None
    try:
        import sherpa_onnx as so

        config = so.OnlineSpeechDenoiserConfig(
            model=so.OfflineSpeechDenoiserModelConfig(
                gtcrn=so.OfflineSpeechDenoiserGtcrnModelConfig(model=str(path)), num_threads=1))
        return so.OnlineSpeechDenoiser(config)
    except Exception:
        _once("create", "could not create the GTCRN denoiser — the call is NOT enhanced")
        logger.debug("denoiser failed", exc_info=True)
        return None


def for_session(input_rate: int) -> Enhancer | None:
    if not settings.enhance_enabled:
        return None
    if input_rate != RATE:
        _once("rate", "ENHANCE_ENABLED: provider input is %s Hz, GTCRN is 16 kHz — not enhanced", input_rate)
        return None
    denoiser = make_denoiser(settings.enhance_model)
    if denoiser is None:
        return None
    logger.info("speech enhancement on for this call (GTCRN, %s)", Path(settings.enhance_model).name)
    return Enhancer(denoiser)


def reset_warnings() -> None:
    _WARNED.clear()
