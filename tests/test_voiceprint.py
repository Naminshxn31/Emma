"""Voice prints (app/voiceprint.py): per-session clustering of speech segments.

Most of this runs on synthetic embeddings and a fake embedder, because the
thing that must hold everywhere is the logic -- ordinals, the threshold, the
cap, reset, the queue, the off switch. One test runs the real pinned model on
committed wavs when this machine has it, and says what goes unchecked when it
does not.
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
import types
from pathlib import Path

import numpy as np
import pytest

from app import voiceprint as vp
from app.config import settings

RATE = vp.SAMPLE_RATE
DIM = 16


def _unit(*xs):
    v = np.zeros(DIM, dtype=np.float32)
    v[: len(xs)] = xs
    return v / np.linalg.norm(v)


def _at_cos(c: float, axis: int = 1):
    """A unit vector whose cosine to e0 is exactly `c`."""
    v = np.zeros(DIM, dtype=np.float32)
    v[0], v[axis] = c, np.sqrt(1 - c * c)
    return v


#: The fake embedder reads the voice from the audio: every sample of a test
#: segment is `voice * 1000` (a constant level, so trimming keeps all of it).
VOICES = {1: _unit(1), 2: _unit(0, 1), 3: _unit(0, 0, 1), 4: _unit(0, 0, 0, 1)}


def _pcm(voice: int, seconds: float) -> bytes:
    return np.full(int(seconds * RATE), voice * 1000, dtype="<i2").tobytes()


def _fake_embed(x):
    return VOICES[int(round(float(x[0]) * 32768 / 1000))]


@pytest.fixture
def seen(monkeypatch):
    """Voice prints ON with a fake embedder and a stub focus; returns the
    list of observe_voice calls (segment, t0, t1, ordinal, confidence, thread)."""
    calls: list = []
    focus = types.ModuleType("app.focus")
    focus.observe_voice = lambda seg, t0, t1, cluster, conf: calls.append(
        (seg, t0, t1, cluster, conf, threading.get_ident()))
    monkeypatch.setitem(sys.modules, "app.focus", focus)
    monkeypatch.setattr(settings, "voiceprint_enabled", True)
    monkeypatch.setattr(settings, "voiceprint_model", "")
    monkeypatch.setattr(settings, "multi_session", False)
    monkeypatch.setattr(vp, "_load_embedder", lambda: _fake_embed)
    _fresh()
    yield calls
    _fresh()


def _fresh():
    vp.shutdown()
    vp._embedder = None
    vp._load_failed = False
    for k in vp._stats:
        vp._stats[k] = 0
    vp.reset()          # with the switch off in teardown: clears, starts nothing
    vp.shutdown()


@pytest.fixture(autouse=True)
def _no_worker_left_behind():
    yield
    vp.shutdown()


# ---- the clustering (pure) -------------------------------------------------------

def test_ordinals_follow_first_appearance():
    c = vp.Clusters()
    got = [c.assign(VOICES[v], 2.0).ordinal for v in (2, 2, 4, 2, 1, 4)]
    # Voice 2 spoke first, so it is คนที่ 1 whatever its vector -- ordinals
    # are order of appearance in this session, not identities.
    assert got == [1, 1, 2, 1, 3, 2]
    assert len(c) == 3


def test_threshold_decides_join_or_new():
    c = vp.Clusters()
    c.assign(_at_cos(1.0), 2.0)
    joined = c.assign(_at_cos(vp.THRESHOLD + 0.02), 2.0)
    assert (joined.ordinal, joined.new) == (1, False)
    fresh = vp.Clusters()
    fresh.assign(_at_cos(1.0), 2.0)
    apart = fresh.assign(_at_cos(vp.THRESHOLD - 0.02), 2.0)
    assert (apart.ordinal, apart.new) == (2, True)


def test_centroid_is_the_speech_weighted_mean():
    c = vp.Clusters(threshold=0.0)
    c.assign(_unit(1), 3.0)
    c.assign(_unit(0, 1), 1.0)
    want = _unit(3, 1)
    assert float(c._centroid[0] @ want) == pytest.approx(1.0, abs=1e-6)
    assert c.weight[0] == pytest.approx(4.0)


def test_at_the_cap_the_nearest_voice_is_named_with_zero_confidence_and_not_moved():
    c = vp.Clusters(cap=2)
    c.assign(VOICES[1], 2.0)
    c.assign(VOICES[2], 2.0)
    before = [x.copy() for x in c._centroid]
    a = c.assign(_unit(0.3, 0, 1), 2.0)     # far from both, nearest is voice 1
    assert (a.ordinal, a.confidence, a.new) == (1, 0.0, False)
    assert len(c) == 2
    # A forced assignment must not drag a voice toward a stranger.
    assert all(np.array_equal(b, x) for b, x in zip(before, c._centroid))


def test_confidence_is_lower_for_short_and_ambiguous_segments():
    c = vp.Clusters()
    c.assign(VOICES[1], 2.0)
    long_ = c.assign(_at_cos(0.95), 2.0).confidence
    c2 = vp.Clusters()
    c2.assign(VOICES[1], 2.0)
    short = c2.assign(_at_cos(0.95), 0.8).confidence
    assert long_ == pytest.approx(1.0)
    assert short == pytest.approx(0.5)
    # Two voices both above the threshold and nearly as close: unsure.
    c3 = vp.Clusters(threshold=0.40)
    c3.assign(_unit(1), 2.0)
    c3.assign(_unit(0, 1), 2.0)
    between = c3.assign(_unit(1, 0.98), 2.0)
    assert between.confidence < 0.2


def test_voiced_trims_the_quiet_head_and_tail_only():
    quiet = np.full(int(0.5 * RATE), 0.002, dtype=np.float32)
    loud = np.full(int(1.0 * RATE), 0.2, dtype=np.float32)
    x = np.concatenate([quiet, loud, quiet, loud, quiet])
    v = vp.voiced(x)
    # the pause in the middle stays; the gate's pre-roll and hangover go
    assert len(v) == int(2.5 * RATE)
    assert len(vp.voiced(np.zeros(RATE, dtype=np.float32))) == 0


# ---- the session path -------------------------------------------------------------

def test_segments_reach_focus_with_their_ordinals(seen):
    assert vp.segment(11, 1.0, 3.0, _pcm(1, 2.0))
    assert vp.segment(12, 3.5, 5.0, _pcm(2, 1.5))
    assert vp.segment(13, 6.0, 8.0, _pcm(1, 2.0))
    assert vp.flush()
    assert [(s, t0, t1, o) for s, t0, t1, o, _c, _t in seen] == [
        (11, 1.0, 3.0, 1), (12, 3.5, 5.0, 2), (13, 6.0, 8.0, 1)]
    assert all(0.0 <= c <= 1.0 for *_x, c, _t in seen)
    assert vp.label(12)[0] == 2


def test_short_segments_are_skipped(seen):
    # Raw audio shorter than MIN_SPEECH_S: not even queued.
    assert not vp.segment(1, 0.0, 0.5, _pcm(1, vp.MIN_SPEECH_S - 0.05))
    # Long enough raw, but mostly the gate's hangover: skipped after trimming.
    hangover = np.zeros(int(1.2 * RATE), dtype="<i2").tobytes()
    assert vp.segment(2, 0.0, 1.6, _pcm(1, 0.4) + hangover)
    assert vp.flush()
    assert seen == []
    assert vp._stats["short"] == 2


def test_reset_forgets_every_voice(seen):
    vp.segment(1, 0, 1, _pcm(3, 2.0))
    vp.segment(2, 1, 2, _pcm(4, 2.0))
    assert vp.flush()
    vp.reset()
    vp.segment(3, 5, 6, _pcm(4, 2.0))
    assert vp.flush()
    # Voice 4 was คนที่ 2 last session; in the new one it spoke first.
    assert [o for _s, _a, _b, o, _c, _t in seen] == [1, 2, 1]
    assert vp.snapshot()["voices"] == [{"ordinal": 1, "segments": 1, "speech_s": 2.0}]
    assert vp.label(1) is None


def test_work_from_before_a_reset_is_never_delivered(seen, monkeypatch):
    started, release = threading.Event(), threading.Event()

    def slow(x):
        started.set()
        release.wait(5)
        return _fake_embed(x)

    monkeypatch.setattr(vp, "_load_embedder", lambda: slow)
    vp._embedder = None
    vp.segment(1, 0, 1, _pcm(1, 2.0))     # being embedded when the session ends
    assert started.wait(5)
    vp.segment(2, 1, 2, _pcm(2, 2.0))     # still queued when the session ends
    vp.reset()
    release.set()
    assert vp.flush()
    assert seen == []
    assert vp.snapshot()["voices"] == []


def test_a_full_queue_drops_instead_of_blocking(seen, monkeypatch):
    started, release = threading.Event(), threading.Event()

    def slow(x):
        started.set()
        release.wait(5)
        return _fake_embed(x)

    monkeypatch.setattr(vp, "_load_embedder", lambda: slow)
    vp._embedder = None
    assert vp.segment(0, 0, 1, _pcm(1, 1.0))
    assert started.wait(5)                 # the worker holds segment 0
    t = time.monotonic()
    ok = [vp.segment(i, 0, 1, _pcm(1, 1.0)) for i in range(1, vp.QUEUE_MAX + 2)]
    assert time.monotonic() - t < 0.5
    assert ok == [True] * vp.QUEUE_MAX + [False]
    assert vp._stats["dropped"] == 1
    release.set()
    assert vp.flush()
    assert len(seen) == vp.QUEUE_MAX + 1


def test_results_come_back_on_the_sessions_event_loop(seen):
    async def call():
        loop_thread = threading.get_ident()
        assert vp.segment(5, 0.0, 2.0, _pcm(1, 2.0))
        for _ in range(200):
            if seen:
                break
            await asyncio.sleep(0.01)
        return loop_thread

    loop_thread = asyncio.run(call())
    assert len(seen) == 1
    # focus is touched from one thread only: the worker hands back, it does
    # not call into focus itself.
    assert seen[0][5] == loop_thread


def test_focus_hears_inside_the_callers_context(seen, monkeypatch):
    """session.py submits from its own thread under the session's copied
    contextvars (turnlog rows belong to a session). The print is computed on
    another thread; focus must still be called inside the caller's context."""
    import contextvars

    who = contextvars.ContextVar("who", default="nobody")
    got = []
    focus = sys.modules["app.focus"]
    monkeypatch.setattr(focus, "observe_voice", lambda *a: got.append(who.get()), raising=False)

    def caller():
        who.set("session-7")
        vp.segment(1, 0.0, 2.0, _pcm(1, 2.0))

    t = threading.Thread(target=caller)   # no event loop: the backbone's thread
    t.start()
    t.join()
    assert vp.flush()
    assert got == ["session-7"]


def test_off_loads_and_starts_nothing(monkeypatch):
    def refuse():
        raise AssertionError("voice prints are off: the model must not load")

    monkeypatch.setattr(settings, "voiceprint_enabled", False)
    monkeypatch.setattr(vp, "_load_embedder", refuse)
    vp.shutdown()
    vp.reset()
    assert vp.segment(1, 0.0, 2.0, _pcm(1, 2.0)) is False
    assert vp._thread is None and vp._q is None
    assert vp.snapshot()["enabled"] is False


def test_multi_session_turns_it_off(monkeypatch):
    monkeypatch.setattr(settings, "voiceprint_enabled", True)
    monkeypatch.setattr(settings, "multi_session", True)
    monkeypatch.setattr(vp, "_load_embedder", lambda: _fake_embed)
    vp.shutdown()
    assert not vp.enabled()
    assert vp.segment(1, 0.0, 2.0, _pcm(1, 2.0)) is False
    assert vp._thread is None


def test_a_missing_model_is_off_and_loud(monkeypatch, caplog, tmp_path):
    monkeypatch.setattr(settings, "voiceprint_enabled", True)
    monkeypatch.setattr(settings, "multi_session", False)
    monkeypatch.setattr(settings, "voiceprint_model", str(tmp_path / "nope.onnx"))
    vp.shutdown()
    vp._embedder, vp._load_failed = None, False
    with caplog.at_level("WARNING", logger="condo_voice.voiceprint"):
        assert vp._get_embedder() is None
    assert "fetch_voiceprint_model.py" in caplog.text
    assert vp.segment(1, 0.0, 2.0, _pcm(1, 2.0)) is False
    vp._load_failed = False


def test_nothing_is_persisted_or_named():
    """PDPA: voice prints live in memory for one session. A write, a save or
    a name in this module is a design change, not a refactor."""
    import ast

    tree = ast.parse(Path(vp.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported |= {node.module or ""} | {"%s.%s" % (node.module, a.name) for a in node.names}
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            assert name not in {"write", "save", "savez", "savez_compressed", "dump", "tofile"}, name
            if name == "open":
                modes = [a.value for a in node.args[1:2] if isinstance(a, ast.Constant)]
                assert all(set(m) <= set("rb") for m in modes), modes
    for bad in ("pickle", "json", "app.turnlog", "app.consent", "app.faces", "app.greeter"):
        assert not any(i == bad or i.startswith(bad + ".") for i in imported), bad
    snap_keys = {"enabled", "model", "loaded", "load_failed", "threshold", "voices", "recent", "stats"}
    assert set(vp.snapshot()) == snap_keys


def test_fetch_pins_match_the_robot_layout():
    assert vp.REL_PATH.startswith("data/models/") and vp.REL_PATH.endswith(vp.MODEL_FILE)
    assert vp.MODEL_URL.endswith("/" + vp.MODEL_FILE)
    assert len(vp.MODEL_SHA256) == 64


# ---- the real model ----------------------------------------------------------------

WAKE = Path(__file__).parent / "data" / "wake"


def _wav_pcm(name: str) -> bytes:
    import wave

    with wave.open(str(WAKE / name)) as w:
        assert w.getframerate() == RATE and w.getnchannels() == 1
        return w.readframes(w.getnframes())


def test_real_model_separates_two_committed_voices(monkeypatch):
    if not vp.DEFAULT_MODEL.is_file():
        pytest.skip("no speaker model at %s (python scripts/fetch_voiceprint_model.py): UNCHECKED -- that "
                    "the pinned CAM++ weights load through sherpa-onnx, embed real audio, and keep two "
                    "voices apart at THRESHOLD. The clustering logic above is still checked." % vp.REL_PATH)
    try:
        import sherpa_onnx  # noqa: F401
    except ImportError:
        pytest.skip("sherpa-onnx not installed: the real speaker model was NOT checked")
    calls: list = []
    focus = types.ModuleType("app.focus")
    focus.observe_voice = lambda seg, t0, t1, cluster, conf: calls.append((seg, cluster))
    monkeypatch.setitem(sys.modules, "app.focus", focus)
    monkeypatch.setattr(settings, "voiceprint_enabled", True)
    monkeypatch.setattr(settings, "voiceprint_model", "")
    monkeypatch.setattr(settings, "multi_session", False)
    vp.shutdown()
    vp._embedder, vp._load_failed = None, False
    vp.reset()
    try:
        x = np.frombuffer(_wav_pcm("other.wav"), dtype="<i2").astype(np.float32) / 32768
        v = vp.voiced(x)
        half = len(v) // 2
        a = (v[:half] * 32768).astype("<i2").tobytes()      # one English TTS voice,
        b = (v[half:] * 32768).astype("<i2").tobytes()      # two halves of one sentence
        vp.segment(1, 0, 1, a)
        vp.segment(2, 1, 2, _wav_pcm("other_imm_trap.wav"))   # a Thai voice
        vp.segment(3, 2, 3, b)
        vp.segment(4, 3, 4, _wav_pcm("emma_thai_m.wav"))      # 1.8 s file, ~0.4 s of speech
        assert vp.flush(30)
        assert vp._embedder is not None, "the pinned model did not load"
        assert calls == [(1, 1), (2, 2), (3, 1)]
        assert vp._stats["short"] == 1
    finally:
        vp.shutdown()
        vp._embedder, vp._load_failed = None, False
        monkeypatch.setattr(settings, "voiceprint_enabled", False)
        vp.reset()


def test_the_docstring_says_where_the_server_actually_delivers(seen):
    """Review 10 Oct: the module said "focus is only ever touched from one
    thread". session.py calls `segment()` from its own "voiceprint" thread,
    which has no event loop, so in the server the result is delivered on the
    embedding worker thread -- true only of the tests, before. The thread is
    pinned here, and the sentence that described the other world is gone."""
    names = []
    focus = sys.modules["app.focus"]
    focus.observe_voice = lambda *a: names.append(threading.current_thread().name)

    def caller():                       # session.py's "voiceprint" thread: no loop
        vp.segment(1, 0.0, 2.0, _pcm(1, 2.0))

    t = threading.Thread(target=caller, name="voiceprint")
    t.start()
    t.join()
    assert vp.flush()
    assert names == ["voiceprint-embed"]
    doc = vp.__doc__
    assert "focus is only ever touched from one thread, and" not in doc
    assert '"voiceprint-embed" worker thread' in doc
    assert "serialise with their own locks" in doc
