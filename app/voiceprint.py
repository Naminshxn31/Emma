"""
Voice prints: which voice spoke each speech segment -- within ONE session.

Input 3 of the listening-focus arbiter (docs/listening-focus-2026-10-09.md).
When the local VAD gate closes a segment, the session hands its audio to
`segment()`. A worker thread embeds it (sherpa-onnx speaker embedding), puts
it in an online cluster of *this session's* voices, and reports the cluster's
ordinal -- 1, 2, ... in order of first appearance ("คนที่ 1", "คนที่ 2") -- to
`focus.observe_voice`. Focus uses it to keep talking to the main speaker and
to carry a speaker's ordinal across moments the camera does not see them.

**Voice prints are biometric data (PDPA).** A voice print identifies a person
the way a face does, so this module is session-only by construction:

- embeddings and centroids live in this process's memory for one session and
  are dropped by `reset()` (session start AND end). Nothing here writes a
  file, logs a vector, or sends one anywhere -- `snapshot()` shows counts,
  ordinals and similarity scores, never an embedding;
- there are no names here. A cluster is an ordinal and nothing more. A name
  can reach a voice only through focus, from a face the camera saw speaking
  in the same session (consent.py rules) -- never from this module;
- nothing is enrolled. There is no gallery to match next week's visitor
  against, and there must never be one without the consent machinery faces
  have (record, revoke, expiry).

**Never blocks the audio path.** `segment()` copies the bytes and queues them
(`QUEUE_MAX`); a full queue drops the segment -- a missed voice print makes
focus fall back on its other inputs, a stalled audio loop makes the robot
deaf. The model loads in the worker thread, at the first `reset()` (session
start) when the switch is on, so the first segment does not wait for it.
Where the result is delivered depends on the caller. A caller running an
event loop gets it back on that loop (`call_soon_threadsafe`). The server is
NOT that caller: session.py hands segments over from its own "voiceprint"
thread (the audio path must not wait on a queue put either), which has no
loop -- so in production `focus.observe_voice` runs on this module's
"voiceprint-embed" worker thread. That is safe because focus and turnlog
serialise with their own locks; nothing relies on one thread (this said
"focus is only ever touched from one thread" until review 10 Oct, which was
true only of the tests). Either way it runs inside the caller's contextvars
(copied at `segment()`): focus writes turnlog rows, and a row written from a
bare worker thread would belong to no session.

Off (`VOICEPRINT_ENABLED=false`, the default) = nothing is imported, loaded,
or started. Also off under `MULTI_SESSION`: the clusters are module state,
and two concurrent conversations would share one set of ordinals (the reason
`calc` is not in MULTI_SESSION_SAFE).

**The model and the threshold were chosen by measurement (9 Oct 2026).**
Candidates from the sherpa-onnx `speaker-recongition-models` release, run on
the same eval set: 2,356 windows of 0.8 / 1.2 / 2.0 / 3.0 s cut from real
speech -- 39 Thai speakers (Common Voice Thai, CC0), 12 English (LibriSpeech
dev-clean), 3 Chinese (sherpa-onnx test wavs) -- plus 20 edge-tts voices
(Thai and English). Same-speaker pairs exclude windows of the same utterance.
EER, real speech:

    model (MB)                         0.8 s  1.2 s  all   Thai  ms/1s  ms/3s (PC, 2 thr)
    3D-Speaker CAM++ zh+en adv. (28)    9.9%   4.1%  5.7%  8.2%   12     26   <- chosen
    3D-Speaker ERes2Net base 200k (40)  9.9%   5.0%   --   8.3%   20     60
    3D-Speaker ERes2NetV2 (71)          9.7%   5.5%   --   8.6%   47    141
    NeMo TitaNet small (40)            11.0%   6.2%   --  10.1%    9     22
    3D-Speaker ERes2Net VoxCeleb (27)  13.9%   7.8%   --  12.6%   21     57
    WeSpeaker ResNet34-LM (27)         ~22% all windows, ~36% on Chinese
    WeSpeaker CAM++-LM, 3D-Speaker CAM++ VoxCeleb: ~45% (chance) through sherpa

CAM++ zh+en: best or tied on real Thai and real short windows, the smallest
file, and the second fastest. Against the speaker's centroid (49 Thai/English
speakers, 1.2-2 s): clean 0.68 mean (98.7% >= THRESHOLD); with a second voice
mixed in at -10 dB 0.62 (96.1%), at -6 dB 0.55 (86.8%) -- and the segment
matched the *background* voice 2.6% / 3.9% of the time (other speakers'
baseline: 1.3%); white noise at 10 dB SNR 0.52 (86.8%), 5 dB 0.47 (73.7%).
Licence Apache-2.0 (ModelScope iic/speech_campplus_sv_zh_en_16k-common_advanced).
The TTS voices separate worse for every model (Thai TTS voices share one
acoustic front end) -- they were not used to set the threshold. No audio from
the robot's own mic array was available to this measurement: re-check the
threshold from the robot's `voiceprint:` log lines before relying on it there.
"""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import importlib
import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from app.config import settings

logger = logging.getLogger("condo_voice.voiceprint")

ROOT = Path(__file__).resolve().parent.parent

# ---- the model (pins) ---------------------------------------------------------

MODEL_FILE = "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
#: The k2-fsa release tag really is spelled "recongition".
MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
             "speaker-recongition-models/" + MODEL_FILE)
#: Matches the release's own checksum.txt (verified 9 Oct 2026).
MODEL_SHA256 = "aa3cfc16963a10586a9393f5035d6d6b57e98d358b347f80c2a30bf4f00ceba2"
MODEL_BYTES = 28_281_164
#: Repo-relative (gitignored data/models/): scripts/fetch_voiceprint_model.py
#: on a PC, robot_server.py's FETCH_ON_ROBOT on the robot.
REL_PATH = "data/models/voiceprint/" + MODEL_FILE
DEFAULT_MODEL = ROOT / REL_PATH

SAMPLE_RATE = 16000
#: Two cores, like FACE_THREADS and the embedding model: the face recogniser
#: shares the robot's CPU. PC, 2 threads: 12 ms per 1 s segment, 26 ms per 3 s.
THREADS = 2

# ---- the decision (measured, see the module docstring) -----------------------

#: Cosine at or above which a segment joins the closest voice of this session.
#: CAM++ zh+en, real speech, windows 0.8-3 s (pairs of different utterances):
#:   same speaker  mean 0.53  p10 0.37  p5 0.30   (Thai: 0.51 / 0.35 / 0.29)
#:   other speaker mean 0.12  p95 0.33  p99 0.42  (Thai: 0.18 / 0.37 / 0.45)
#:   EER 5.7% at 0.32 (Thai 8.2% at 0.33; 0.8 s windows 9.9%; 2-3 s 1.8%)
#: Pairwise, 0.40 lets 1.5% of other-speaker pairs through (Thai 2.7%) and
#: rejects 14% of same-speaker pairs. What decides is the online clustering
#: itself, replayed on the measured embeddings: 1,000 conversations of 12
#: segments, a main speaker opening and holding ~60%, one or two other voices,
#: 39 real Thai speakers --
#:   threshold  main -> #1   other -> #1   clusters / true voices
#:     0.35       94.0%         8.4%           1.05
#:     0.40       89.9%         3.7%           1.17   <- chosen
#:     0.45       83.2%         1.5%           1.33
#: (replayed through `Clusters` below, not a copy of it; 53 speakers incl.
#: English/Chinese: 90.1% / 2.0% / 1.16 at 0.40.)
#: Biased against "other -> #1" on purpose: a bystander taken for the main
#: speaker IS the bug focus exists to fix (owner, 9 Oct: the robot answered
#: two people chatting nearby). A main speaker split into a second ordinal is
#: recoverable -- focus rule 5 still admits a voice whose engaged face's
#: mouth moves. Requiring >= 1 s of speech to open a new voice was measured
#: too (main -> #1 94.3% at 0.40, other -> #1 5.3%, 7.5% left undecided) and
#: not taken: more machinery, a third outcome for focus, a marginal gain.
#: Re-measure on the robot's own audio before trusting these numbers there.
THRESHOLD = 0.40
#: Similarity that counts as "certainly this voice" (confidence 1.0): above
#: the other-speaker p99 (0.42-0.45), around the same-speaker mean.
SURE_SAME = 0.60
#: Similarity that counts as "certainly a different voice": about the
#: other-speaker mean (0.12 all, 0.18 Thai).
SURE_DIFFERENT = 0.15
#: Two voices both above THRESHOLD and closer than this: an ambiguous segment.
MARGIN = 0.10
#: Speech shorter than this is not embedded (spec: ~0.7 s). Measured: at
#: 0.8 s the EER is already 9.9% vs 4.1% at 1.2 s; shorter is mostly guessing.
MIN_SPEECH_S = 0.7
#: Confidence scales with speech length up to this (0.8 s -> 0.5, 2 s -> 1).
FULL_CONFIDENCE_S = 2.0
#: Only the first seconds of voiced audio are embedded: the person who OPENED
#: the segment is the one focus judged (`may_open`), and a minute-long segment
#: (a TV, a crowd -- MAX_SEGMENT_S is 60) must not hold the worker for seconds.
MAX_EMBED_S = 6.0
#: Voices kept per session. A sales conversation has 1-3; more is a crowd or
#: fragmentation, and new ordinals past this would mean nothing. At the cap a
#: far segment is reported as its nearest voice with confidence 0.
MAX_CLUSTERS = 6
#: Segments waiting for the worker. ~12-30 ms each on a PC: a full queue means
#: the worker is stuck or the CPU is, and dropping is the right answer.
QUEUE_MAX = 4
#: Leading/trailing 20 ms frames quieter than this fraction of the segment's
#: loud frames (p95 RMS) are trimmed before anything is judged: the gate's
#: segment carries its pre-roll and the whole VAD hangover (VAD_SILENCE_MS),
#: so 0.4 s of "ครับ" arrives as ~1.6 s of audio. The trim is what makes
#: MIN_SPEECH_S count speech. For the embedding itself it is neutral --
#: measured: 1.2-2 s windows padded with 0.3 s + 0.9 s of room noise (RMS
#: 0.003) score 0.69 against their speaker's centroid untrimmed, 0.68
#: trimmed, 0.68 clean.
TRIM_RATIO = 0.10
#: ... and anything under this RMS is never speech (digital silence, dither).
TRIM_FLOOR = 0.0005


@dataclass(frozen=True)
class Assignment:
    ordinal: int        # 1, 2, ... in order of first appearance in this session
    confidence: float   # 0..1
    similarity: float   # cosine to the chosen voice (-1 for the first voice)
    new: bool           # this segment opened the voice


class Clusters:
    """Online clustering of one session's voices. Pure: no threads, no model.

    A segment joins the most similar centroid when the cosine reaches
    `threshold`, else it opens a new voice (until `cap`). Centroids are the
    speech-length-weighted mean of their unit embeddings, renormalised, and
    are only updated by segments that cleared the threshold -- a forced
    assignment at the cap must not drag a voice toward a stranger.
    """

    def __init__(self, threshold: float = THRESHOLD, cap: int = MAX_CLUSTERS) -> None:
        self.threshold = float(threshold)
        self.cap = int(cap)
        self._sum: list = []          # weighted sums (unnormalised)
        self._centroid: list = []     # unit vectors
        self.weight: list[float] = []
        self.count: list[int] = []

    def __len__(self) -> int:
        return len(self._centroid)

    def assign(self, emb, speech_s: float) -> Assignment:
        import numpy as np

        e = np.asarray(emb, dtype=np.float32).ravel()
        n = float(np.linalg.norm(e))
        if not n > 0:
            raise ValueError("empty embedding")
        e = e / n
        w = max(float(speech_s), 1e-3)
        scale = min(1.0, max(0.5, w / FULL_CONFIDENCE_S))

        if not self._centroid:
            self._open(e, w)
            return Assignment(1, round(scale, 3), -1.0, True)

        sims = np.array([float(c @ e) for c in self._centroid])
        order = np.argsort(-sims)
        best = int(order[0])
        s = float(sims[best])
        s2 = float(sims[order[1]]) if len(order) > 1 else -1.0

        if s >= self.threshold:
            conf = _clip((s - self.threshold) / (SURE_SAME - self.threshold))
            if s2 >= self.threshold:
                conf *= _clip((s - s2) / MARGIN)
            self._update(best, e, w)
            return Assignment(best + 1, round(conf * scale, 3), round(s, 4), False)

        if len(self._centroid) >= self.cap:
            # Nearest, and say we do not know: confidence 0, centroid untouched.
            return Assignment(best + 1, 0.0, round(s, 4), False)

        conf = _clip((self.threshold - s) / (self.threshold - SURE_DIFFERENT))
        self._open(e, w)
        return Assignment(len(self._centroid), round(conf * scale, 3), round(s, 4), True)

    def _open(self, e, w: float) -> None:
        self._sum.append(e * w)
        self._centroid.append(e.copy())
        self.weight.append(w)
        self.count.append(1)

    def _update(self, i: int, e, w: float) -> None:
        import numpy as np

        self._sum[i] = self._sum[i] + e * w
        self._centroid[i] = self._sum[i] / float(np.linalg.norm(self._sum[i]))
        self.weight[i] += w
        self.count[i] += 1


def _clip(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else float(x)


def voiced(samples, rate: int = SAMPLE_RATE):
    """`samples` without the quiet head and tail (20 ms frames, see TRIM_*)."""
    import numpy as np

    x = np.asarray(samples, dtype=np.float32)
    f = int(0.02 * rate)
    n = len(x) // f
    if n == 0:
        return x[:0]
    rms = np.sqrt((x[: n * f].reshape(n, f) ** 2).mean(axis=1))
    bar = max(TRIM_FLOOR, TRIM_RATIO * float(np.percentile(rms, 95)))
    loud = np.flatnonzero(rms > bar)
    if not len(loud):
        return x[:0]
    return x[loud[0] * f:(loud[-1] + 1) * f]


# ---- session state + worker ----------------------------------------------------

_lock = threading.Lock()
_gen = 0                       # bumped by reset(); stale work is dropped
_clusters = Clusters()
_recent: deque = deque(maxlen=16)   # (segment, ordinal, confidence, similarity, speech_s, ms)
_by_segment: dict[int, tuple[int, float]] = {}
_stats = {"queued": 0, "short": 0, "dropped": 0, "embedded": 0, "stale": 0, "failed": 0}

_q: queue.Queue | None = None
_thread: threading.Thread | None = None
_embedder = None
_load_failed = False
_warned: set[str] = set()
_STOP = object()


def enabled() -> bool:
    if not settings.voiceprint_enabled:
        return False
    if getattr(settings, "multi_session", False):
        _warn_once("multi", "VOICEPRINT_ENABLED is ignored with MULTI_SESSION: voice clusters "
                            "are per process, and concurrent conversations would share ordinals")
        return False
    return True


def segment(segment_id: int, t0: float, t1: float, pcm16_bytes: bytes) -> bool:
    """Queue one closed speech segment (16 kHz mono PCM16). Never blocks.

    Returns True when queued. False: the switch is off, the audio is shorter
    than MIN_SPEECH_S, or the queue is full (dropped, counted in snapshot()).
    The result arrives later through `focus.observe_voice(segment_id, t0, t1,
    ordinal, confidence)` -- on the caller's event loop if it had one, else on
    the embedding thread (the server's case: see the module docstring).
    """
    if not enabled() or _load_failed:
        return False
    if len(pcm16_bytes) // 2 < int(MIN_SPEECH_S * SAMPLE_RATE):
        _stats["short"] += 1
        return False
    q = _ensure_worker()
    loop = _running_loop()
    ctx = contextvars.copy_context()
    try:
        q.put_nowait((_gen, int(segment_id), float(t0), float(t1), bytes(pcm16_bytes), loop, ctx))
    except queue.Full:
        _stats["dropped"] += 1
        logger.info("voiceprint: queue full, segment %s dropped", segment_id)
        return False
    _stats["queued"] += 1
    return True


def reset() -> None:
    """New session / session end: forget every voice (see the PDPA note).

    Also starts the worker (and with it the model load) when the switch is
    on, so the first segment of the session does not wait for the model.
    """
    global _gen, _clusters
    with _lock:
        _gen += 1
        _clusters = Clusters()
        _recent.clear()
        _by_segment.clear()
        q = _q
        if q is not None:
            while True:
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
                q.task_done()
    if enabled():
        _ensure_worker()


def label(segment_id: int) -> tuple[int, float] | None:
    """(ordinal, confidence) of a segment already processed this session."""
    with _lock:
        return _by_segment.get(int(segment_id))


def snapshot() -> dict:
    """Debug view. Counts, ordinals and scores only -- never an embedding."""
    with _lock:
        voices = [{"ordinal": i + 1, "segments": _clusters.count[i],
                   "speech_s": round(_clusters.weight[i], 1)} for i in range(len(_clusters))]
        recent = [{"segment": r[0], "ordinal": r[1], "confidence": r[2], "similarity": r[3],
                   "speech_s": r[4], "ms": r[5]} for r in _recent]
    return {"enabled": enabled(), "model": _model_path().name,
            "loaded": _embedder is not None, "load_failed": _load_failed,
            "threshold": THRESHOLD, "voices": voices, "recent": recent, "stats": dict(_stats)}


def flush(timeout: float = 5.0) -> bool:
    """Wait until every queued segment is processed (tests, shutdown)."""
    q = _q
    if q is None:
        return True
    deadline = time.monotonic() + timeout
    with q.all_tasks_done:
        while q.unfinished_tasks:
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            q.all_tasks_done.wait(left)
    return True


def shutdown(timeout: float = 2.0) -> None:
    """Stop the worker (tests). The next segment()/reset() starts a new one."""
    global _q, _thread
    q, t = _q, _thread
    _q, _thread = None, None
    if q is not None:
        try:
            q.put_nowait(_STOP)
        except queue.Full:
            pass
    if t is not None:
        t.join(timeout)


def _running_loop():
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _ensure_worker() -> queue.Queue:
    global _q, _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            _q = queue.Queue(maxsize=QUEUE_MAX)
            _thread = threading.Thread(target=_worker, args=(_q,), name="voiceprint-embed", daemon=True)
            _thread.start()
        return _q


def _worker(q: queue.Queue) -> None:
    _get_embedder()          # load now, not under the first segment
    while True:
        job = q.get()
        try:
            if job is _STOP:
                return
            _process(*job)
        except Exception:
            _stats["failed"] += 1
            logger.exception("voiceprint: segment failed")
        finally:
            q.task_done()


def _process(gen: int, seg: int, t0: float, t1: float, pcm: bytes, loop, ctx) -> None:
    import numpy as np

    if gen != _gen:
        _stats["stale"] += 1
        return
    embed = _get_embedder()
    if embed is None:
        return
    x = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
    x = voiced(x)
    speech_s = len(x) / SAMPLE_RATE
    if speech_s < MIN_SPEECH_S:
        _stats["short"] += 1
        return
    x = x[: int(MAX_EMBED_S * SAMPLE_RATE)]
    started = time.perf_counter()
    emb = embed(x)
    ms = (time.perf_counter() - started) * 1000
    with _lock:
        if gen != _gen:        # reset() while embedding: this voice is gone
            _stats["stale"] += 1
            return
        a = _clusters.assign(emb, min(speech_s, MAX_EMBED_S))
        _by_segment[seg] = (a.ordinal, a.confidence)
        if len(_by_segment) > 256:
            _by_segment.pop(next(iter(_by_segment)))
        _recent.append((seg, a.ordinal, a.confidence, a.similarity, round(speech_s, 2), round(ms, 1)))
    _stats["embedded"] += 1
    logger.info("voiceprint: segment %d -> voice %d%s conf %.2f sim %.2f (%.1fs speech, %.0f ms)",
                seg, a.ordinal, " (new)" if a.new else "", a.confidence, a.similarity, speech_s, ms)
    if loop is not None:
        try:
            loop.call_soon_threadsafe(_deliver, gen, seg, t0, t1, a.ordinal, a.confidence, context=ctx)
            return
        except RuntimeError:   # the session's loop is closed: nobody to tell
            return
    ctx.run(_deliver, gen, seg, t0, t1, a.ordinal, a.confidence)


def _deliver(gen: int, seg: int, t0: float, t1: float, ordinal: int, confidence: float) -> None:
    if gen != _gen:
        return
    try:
        focus = importlib.import_module("app.focus")
    except ImportError:
        _warn_once("nofocus", "voiceprint: app.focus is not available -- voice clusters go nowhere")
        return
    try:
        focus.observe_voice(seg, t0, t1, ordinal, confidence)
    except Exception:
        logger.exception("voiceprint: focus.observe_voice failed")


def _model_path() -> Path:
    raw = (settings.voiceprint_model or "").strip()
    if not raw:
        return DEFAULT_MODEL
    p = Path(raw).expanduser()
    return p if p.is_absolute() else ROOT / p


def _get_embedder():
    global _embedder, _load_failed
    if _embedder is not None or _load_failed:
        return _embedder
    try:
        _embedder = _load_embedder()
    except Exception:
        logger.exception("voiceprint: could not load the speaker model")
        _embedder = None
    if _embedder is None:
        _load_failed = True
    return _embedder


def _load_embedder():
    """The embedding function, or None with a loud reason (never quietly off:
    the switch on and nothing happening is the shape this project keeps
    paying for)."""
    import numpy as np

    path = _model_path()
    if not path.is_file():
        logger.warning("VOICEPRINT_ENABLED but the speaker model is missing at %s -- "
                       "run: python scripts/fetch_voiceprint_model.py", path)
        return None
    if path.resolve() == DEFAULT_MODEL.resolve():
        # The pinned file or nothing: a half-written download is a protobuf
        # error at best, and THRESHOLD is only true for these exact weights.
        digest = _sha256(path)
        if digest != MODEL_SHA256:
            logger.warning("voiceprint: %s does not match its pin (sha256 %s) -- re-run "
                           "scripts/fetch_voiceprint_model.py --force", path, digest[:12])
            return None
    else:
        logger.warning("voiceprint: VOICEPRINT_MODEL=%s is not the measured model; THRESHOLD=%.2f "
                       "was measured on %s -- re-measure before trusting the ordinals",
                       path, THRESHOLD, MODEL_FILE)
    import sherpa_onnx

    cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(path), num_threads=THREADS,
                                                      provider="cpu")
    extractor = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)

    def embed(samples):
        stream = extractor.create_stream()
        stream.accept_waveform(sample_rate=SAMPLE_RATE, waveform=samples)
        stream.input_finished()
        v = np.asarray(extractor.compute(stream), dtype=np.float32)
        return v / max(float(np.linalg.norm(v)), 1e-9)

    logger.info("voiceprint ready: %s (dim %d, threshold %.2f)", path.name, extractor.dim, THRESHOLD)
    return embed


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _warn_once(key: str, msg: str) -> None:
    if key not in _warned:
        _warned.add(key)
        logger.warning(msg)
