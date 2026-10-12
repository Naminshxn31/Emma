"""
Hearing "Emma" inside a live call (FOCUS_WAKE) — Look and Talk's other half.

Google's Look and Talk listens to a recognised person who is close and
looking; outside that zone you say the wake word. Listening focus built the
zone (app/focus.py rule 2: a camera-opened call hears only a face in front)
and nothing for outside it: a guest calling the robot from beside the lens
was denied "no face in front" on every word, with no way through. This is
that way through. On a hit the session calls `focus.heard_name`, which waives
rule 2 for FOCUS_WAKE_WINDOW_S; rules 3-5 still judge every face in view.

Same model, same spellings and the same per-spelling asymmetry as the
standby ear (app/wake.py — none of that is re-decided here), its own bar
(FOCUS_WAKE_THRESHOLD), and none of the standby's diagnostics: no clips,
no shadow detector. Call audio is customer speech; nothing here keeps it.

Cost, measured 9 Oct 2026 on this PC over 677 s of real call audio
(data/call_debug, 20 ms frames as the page sends them): 15.4 ms of CPU per
second of audio with one thread, 1.5% of one core. The library default of
two threads spent 59 ms per second for the SAME wall time (10.2 s vs 10.7 s
over the set) — four times the CPU for nothing, hence THREADS = 1. It runs
on the event loop, like the standby ear and the Silero gate beside it, so
the robot's number is the one that matters: every call writes a
`focus_wake_cpu` row (ms per second of audio) at its end. Read it there
before believing this paragraph about an ARM board.
"""
from __future__ import annotations

import logging
import threading
import time

from app.config import settings

logger = logging.getLogger("condo_voice.call_wake")

#: sherpa-onnx threads for the in-call spotter. See the module docstring:
#: two threads cost 4x the CPU for no wall-time gain on 20 ms frames.
THREADS = 1
#: The keywords file this ear writes next to the model (the standby ear
#: writes keywords_active.txt — two bars, two files).
KEYWORDS_FILE = "keywords_call.txt"

_spotter = None
_failed = False
_load_lock = threading.Lock()


def load():
    """The process's in-call spotter, built on first use, or None (said once).

    Blocking — the model load is a few hundred milliseconds — so the session
    calls it through `asyncio.to_thread`, never on the audio path. Needs the
    wake model but not WAKE_ENABLED: the robot runs the standing line, not
    the standby ear, and this is a different ear.
    """
    global _spotter, _failed
    with _load_lock:
        if _spotter is not None or _failed:
            return _spotter
        from app import wake

        model = wake._model_dir()
        if not (model / "tokens.txt").exists():
            logger.warning("FOCUS_WAKE is on but the wake model is missing at %s — "
                           "the name cannot be heard in a call. Run "
                           "scripts/fetch_wake_model.py (and deploy it)", model)
            _failed = True
            return None
        line = wake._encode_keyword(settings.wake_word,
                                    base=float(settings.focus_wake_threshold))
        if line is None:
            _failed = True
            return None
        try:
            _spotter = wake._make_spotter(line, KEYWORDS_FILE, num_threads=THREADS)
            logger.info("in-call wake word ready: %r (bar %.2f)", settings.wake_word,
                        settings.focus_wake_threshold)
        except Exception:
            logger.warning("could not load the in-call wake-word model", exc_info=True)
            _failed = True
            _spotter = None
        return _spotter


class CallEars:
    """One call's ear for the name. `feed` runs on the audio path."""

    def __init__(self, spotter, sample_rate: int = 16000) -> None:
        self._spotter = spotter
        self._stream = spotter.create_stream()
        self._rate = int(sample_rate)
        #: Wall time spent in `feed`, and seconds of audio fed — the cost
        #: on the event loop, reported per call (`report`).
        self.busy_s = 0.0
        self.audio_s = 0.0
        self.hits = 0

    def feed(self, pcm16: bytes) -> str | None:
        """The keyword label on a hit, else None. Same per-utterance contract
        as WakeStream.feed (get_result clears itself; reset_stream after a
        hit follows upstream)."""
        if not pcm16:
            return None
        import numpy as np

        started = time.perf_counter()
        samples = np.frombuffer(pcm16, dtype="<i2").astype("float32") / 32768.0
        self._stream.accept_waveform(self._rate, samples)
        hit = None
        while self._spotter.is_ready(self._stream):
            self._spotter.decode_stream(self._stream)
            result = self._spotter.get_result(self._stream)
            if result:
                hit = result
                self._spotter.reset_stream(self._stream)
        self.busy_s += time.perf_counter() - started
        self.audio_s += len(pcm16) / (2.0 * self._rate)
        if hit:
            self.hits += 1
        return hit

    def report(self) -> dict:
        """Numbers for the `focus_wake_cpu` row: no audio, no text."""
        per_s = (self.busy_s / self.audio_s * 1000.0) if self.audio_s > 0 else None
        return {"audio_s": round(self.audio_s, 1),
                "ms_per_s": None if per_s is None else round(per_s, 1),
                "hits": self.hits}


def reset() -> None:
    """Tests swap settings and models; the singleton must not outlive them."""
    global _spotter, _failed
    with _load_lock:
        _spotter = None
        _failed = False
