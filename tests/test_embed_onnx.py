"""Semantic search without torch: the ONNX export of the PC's MiniLM (app/embed_onnx.py).

The robot runs the whole server since 9 Oct 2026 and has no torch, so its
query embeds raised and search was lexical-only. The ONNX engine exists to put
the robot's queries in the SAME space as the PC-built embeddings.npz -- which
is a claim about numbers, checked by the equivalence test at the bottom
wherever both engines and the model are present. Everything above it runs on
every machine with fakes: pooling, truncation, the sha256 gate, the
missing-model fallback, the one-load lock, and the rules that keep two engines
from ever becoming two spaces.
"""
from __future__ import annotations

import hashlib
import importlib.util
import logging
import sys
import threading
import time
import types
from pathlib import Path

import numpy as np
import pytest

from app import embed_onnx
from app.tools import retrieval

ROOT = Path(__file__).resolve().parents[1]


# ---- fakes ---------------------------------------------------------------------------

class _Meta:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeSession:
    """Token vector = [token id, 1.0]; padding positions hold 1000s, so any
    pooling that lets padding vote is off by hundreds."""

    def __init__(self, inputs=("input_ids", "attention_mask", "token_type_ids")) -> None:
        self.inputs = inputs
        self.calls: list[dict] = []

    def get_inputs(self):
        return [_Meta(n) for n in self.inputs]

    def get_outputs(self):
        return [_Meta("last_hidden_state")]

    def run(self, names, feed):
        assert names == ["last_hidden_state"]
        self.calls.append({k: np.array(v) for k, v in feed.items()})
        ids = feed["input_ids"]
        hidden = np.stack([ids.astype(np.float32), np.ones(ids.shape, np.float32)], axis=-1)
        hidden[feed["attention_mask"] == 0] = 1000.0
        return [hidden]


class _Enc:
    def __init__(self, ids, mask) -> None:
        self.ids, self.attention_mask = ids, mask


class FakeTokenizer:
    """One token per word, id = len(word) + 2; pads to the longest like
    `tokenizers` with padding enabled."""

    def __init__(self) -> None:
        self.truncation = None
        self.padding = None

    def enable_truncation(self, max_length, **_kw):
        self.truncation = max_length

    def token_to_id(self, token):
        return 1 if token == "<pad>" else None

    def enable_padding(self, pad_id=0, pad_token="[PAD]", **_kw):
        self.padding = (pad_id, pad_token)

    def encode_batch(self, texts):
        seqs = [[len(w) + 2 for w in t.split()][: self.truncation] for t in texts]
        width = max(len(s) for s in seqs)
        pad = self.padding[0]
        return [_Enc(s + [pad] * (width - len(s)), [1] * len(s) + [0] * (width - len(s))) for s in seqs]


def _fake_runtime(monkeypatch, *, refuse: bool = False) -> list:
    """Stand-ins for onnxruntime + tokenizers in sys.modules, so the loader
    tests mean the same on a machine without either (CI) as on the robot.
    Without this, `load()` returns None at the import on such a machine and
    a "refuses wrong bytes" test passes for a reason it never checked."""
    created: list = []
    ort = types.ModuleType("onnxruntime")

    class SessionOptions:
        intra_op_num_threads = 0
        inter_op_num_threads = 0

    class InferenceSession(FakeSession):
        def __init__(self, path, sess_options=None, providers=None):
            if refuse:
                raise AssertionError("onnxruntime was handed bytes that failed their pin")
            super().__init__()
            created.append((path, sess_options, providers))

    ort.SessionOptions = SessionOptions
    ort.InferenceSession = InferenceSession
    tok = types.ModuleType("tokenizers")

    class Tokenizer:
        @staticmethod
        def from_file(path):
            return FakeTokenizer()

    tok.Tokenizer = Tokenizer
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)
    monkeypatch.setitem(sys.modules, "tokenizers", tok)
    return created


def _model_dir(tmp_path: Path) -> Path:
    d = tmp_path / "model"
    d.mkdir()
    for name in embed_onnx.FILES:
        (d / name).write_bytes(b"not the pinned bytes: " + name.encode())
    return d


def _pin_to(monkeypatch, directory: Path) -> None:
    files = {name: (path, hashlib.sha256((directory / name).read_bytes()).hexdigest(), size)
             for name, (path, _digest, size) in embed_onnx.FILES.items()}
    monkeypatch.setattr(embed_onnx, "FILES", files)


@pytest.fixture
def fresh_loader(monkeypatch):
    """The real `_local_encoder` with no model cached and no latch -- conftest
    stubs the public name for every test, and module state must not leak."""
    monkeypatch.setattr(retrieval, "_local", None)
    monkeypatch.setattr(retrieval, "_local_unavailable", False)
    monkeypatch.setattr(retrieval, "LOCAL_BACKEND", "auto")
    # whatever EMBED_LOCAL_MODEL this machine's environment says
    monkeypatch.setattr(retrieval, "LOCAL_MODEL", embed_onnx.MODEL)
    return retrieval._local_encoder_impl


# ---- the two steps sentence-transformers runs for this model ---------------------------

def test_mean_pooling_averages_only_the_tokens_the_mask_keeps():
    """Pooling(mean_tokens): padding never votes. Batched texts of different
    lengths are where a mask-blind mean goes wrong -- by hundreds here."""
    enc = embed_onnx.OnnxEncoder(FakeSession(), FakeTokenizer())
    out = enc.encode(["aa bbbb", "c"])          # ids [4, 6] and [3, <pad>]
    assert out.dtype == np.float32 and out.shape == (2, 2)
    np.testing.assert_allclose(out, [[5.0, 1.0], [3.0, 1.0]])


def test_texts_are_truncated_at_128_and_padded_with_the_pad_id():
    """max_seq_length 128 is sentence_bert_config.json at the pinned commit.
    A different cut is a different vector for every slide over 128 tokens --
    most of them. Exercised through the real `tokenizers` API, so a wrong
    keyword argument fails here rather than on the robot."""
    tokenizers = pytest.importorskip(
        "tokenizers", reason="tokenizers not installed: the real truncation/padding calls were NOT checked")
    from tokenizers import models, pre_tokenizers

    tok = tokenizers.Tokenizer(models.WordLevel({"<unk>": 0, "<pad>": 1, "a": 2}, unk_token="<unk>"))
    tok.pre_tokenizer = pre_tokenizers.Whitespace()
    session = FakeSession()
    embed_onnx.OnnxEncoder(session, tok).encode(["a " * 300, "a"])
    feed = session.calls[0]
    assert embed_onnx.MAX_SEQ_LENGTH == 128
    assert feed["input_ids"].shape == (2, 128)
    assert feed["attention_mask"][1].sum() == 1 and (feed["input_ids"][1, 1:] == 1).all()
    assert feed["input_ids"].dtype == np.int64


def test_token_type_ids_are_zeros_and_only_sent_when_the_graph_asks():
    session = FakeSession()
    embed_onnx.OnnxEncoder(session, FakeTokenizer()).encode(["aa bbbb"])
    assert (session.calls[0]["token_type_ids"] == 0).all()
    bare = FakeSession(inputs=("input_ids", "attention_mask"))
    embed_onnx.OnnxEncoder(bare, FakeTokenizer()).encode(["aa"])
    assert "token_type_ids" not in bare.calls[0]       # onnxruntime rejects unknown inputs


def test_batches_come_back_in_order():
    session = FakeSession()
    texts = ["w" * (i % 7 + 1) for i in range(70)]
    out = embed_onnx.OnnxEncoder(session, FakeTokenizer()).encode(texts)
    assert len(session.calls) == 3                      # 32 + 32 + 6, like sentence-transformers
    np.testing.assert_allclose(out[:, 0], [len(t) + 2 for t in texts])


def test_the_onnx_engine_is_normalised_by_the_same_step_as_sentence_transformers(monkeypatch):
    """No Normalize module in this model: `_embed_local` L2-normalises for
    both engines, so they cannot drift apart on it."""
    enc = embed_onnx.OnnxEncoder(FakeSession(), FakeTokenizer())
    monkeypatch.setattr(retrieval, "_local_encoder", lambda: enc)
    out = retrieval._embed_local(["aa bbbb", "c"])
    np.testing.assert_allclose(np.linalg.norm(out, axis=1), [1.0, 1.0], rtol=1e-6)
    np.testing.assert_allclose(out[0], np.array([5.0, 1.0]) / np.hypot(5, 1), rtol=1e-6)


# ---- loading: verified bytes, once, or not at all ----------------------------------------

def test_bytes_that_fail_their_pin_never_reach_onnxruntime(tmp_path, monkeypatch, caplog):
    """Present is not enough: other weights under this signature rank slides
    with plausible numbers and no error -- the mixed-space failure through a
    copied folder instead of a setting."""
    _fake_runtime(monkeypatch, refuse=True)
    directory = _model_dir(tmp_path)
    with caplog.at_level(logging.WARNING, logger="condo_voice.retrieval"):
        assert embed_onnx.load(directory) is None
    assert "sha256" in caplog.text


def test_pinned_bytes_load_on_two_cpu_threads(tmp_path, monkeypatch):
    """The positive control for the test above (the gate, not something else,
    is what refused), and the robot's CPU budget: the face recogniser shares
    these cores."""
    created = _fake_runtime(monkeypatch)
    directory = _model_dir(tmp_path)
    _pin_to(monkeypatch, directory)
    enc = embed_onnx.load(directory)
    assert isinstance(enc, embed_onnx.OnnxEncoder)
    path, options, providers = created[0]
    assert Path(path) == directory / "model.onnx"
    assert options.intra_op_num_threads == embed_onnx.THREADS == 2 and options.inter_op_num_threads == 1
    assert providers == ["CPUExecutionProvider"]


def test_missing_model_files_behave_exactly_as_before(tmp_path, monkeypatch, caplog, fresh_loader):
    """The robot before the files arrive (or a PC that never fetched them):
    no sentence-transformers, no ONNX files -> None, said once, and the
    query embed raises the same error slide_search already turns into
    lexical search."""
    monkeypatch.setattr(embed_onnx, "DIR", tmp_path / "nothing-here")
    monkeypatch.setattr(retrieval, "_st_encoder", lambda: None)
    tried = []
    real_onnx = retrieval._onnx_encoder
    monkeypatch.setattr(retrieval, "_onnx_encoder", lambda: tried.append(1) or real_onnx())
    with caplog.at_level(logging.INFO, logger="condo_voice.retrieval"):
        assert fresh_loader() is None
        assert fresh_loader() is None
    assert len(tried) == 1, "retried the load on every query"
    assert caplog.text.count("local embeddings unavailable") == 1
    assert "fetch_embed_model.py" in caplog.text
    monkeypatch.setattr(retrieval, "_local_encoder", fresh_loader)
    with pytest.raises(RuntimeError, match="local embedding model is not available"):
        retrieval._embed_local(["ฟิตเนส"])


def test_the_model_loads_once_when_two_first_queries_race(monkeypatch, fresh_loader):
    """The boot warm-up and a guest's first question both reach the loader
    from worker threads. Two concurrent loads = twice the 470 MB and twice
    the wait."""
    calls = []
    model = object()

    def slow_load():
        calls.append(1)
        time.sleep(0.05)
        return model

    monkeypatch.setattr(retrieval, "_st_encoder", lambda: None)
    monkeypatch.setattr(retrieval, "_onnx_encoder", slow_load)
    results = []
    threads = [threading.Thread(target=lambda: results.append(fresh_loader())) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert results == [model] * 8


def test_auto_keeps_the_pc_on_sentence_transformers_and_the_robot_on_onnx(monkeypatch, fresh_loader):
    st, onnx = object(), object()

    def engine(name, value):
        def load():
            used.append(name)
            return value
        return load

    cases = [  # backend, st available, expected, engines tried
        ("auto", True, st, ["st"]),               # the PC: unchanged
        ("auto", False, onnx, ["st", "onnx"]),    # the robot
        ("onnx", True, onnx, ["onnx"]),           # how the PC re-measures ONNX
        ("sentence-transformers", False, None, ["st"]),
        ("Onyx", False, onnx, ["st", "onnx"]),    # a typo is auto, not an error
    ]
    for backend, has_st, expected, tried in cases:
        used: list[str] = []
        monkeypatch.setattr(retrieval, "_local", None)
        monkeypatch.setattr(retrieval, "_local_unavailable", False)
        monkeypatch.setattr(retrieval, "LOCAL_BACKEND", backend)
        monkeypatch.setattr(retrieval, "_st_encoder", engine("st", st if has_st else None))
        monkeypatch.setattr(retrieval, "_onnx_encoder", engine("onnx", onnx))
        assert fresh_loader() is expected, backend
        assert used == tried, backend


def test_the_onnx_files_serve_only_the_model_they_are(monkeypatch, tmp_path):
    """EMBED_LOCAL_MODEL=BAAI/bge-m3 with these files on disk must not hand
    out MiniLM vectors under the name local:BAAI/bge-m3."""
    monkeypatch.setattr(retrieval, "LOCAL_MODEL", "BAAI/bge-m3")

    def explode(directory=None):
        raise AssertionError("loaded MiniLM for another model's signature")

    monkeypatch.setattr(embed_onnx, "load", explode)
    assert retrieval._onnx_encoder() is None
    directory = _model_dir(tmp_path)
    monkeypatch.setattr(embed_onnx, "DIR", directory)
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a: None if name == "sentence_transformers" else real_find_spec(name, *a))
    assert retrieval.local_embeddings_installed() is False


def test_availability_counts_onnx_without_opening_the_model(monkeypatch, tmp_path):
    """The startup banner asks; it must not hash 470 MB to answer (the
    `find_spec` rule, extended to the ONNX engine)."""
    directory = _model_dir(tmp_path)
    monkeypatch.setattr(retrieval, "LOCAL_MODEL", embed_onnx.MODEL)
    monkeypatch.setattr(embed_onnx, "DIR", directory)
    monkeypatch.setattr(embed_onnx, "sha256_file", lambda p: (_ for _ in ()).throw(
        AssertionError("hashed the model to answer 'is it installed'")))
    present = {"onnxruntime", "tokenizers"}
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a: object() if name in present else None)
    assert retrieval.local_embeddings_installed() is True
    (directory / "model.onnx").unlink()
    assert retrieval.local_embeddings_installed() is False


# ---- one space, one signature; the eval cache keeps the engines apart ------------------------

def test_both_engines_share_one_signature(monkeypatch):
    """Measured equal (bottom of this file), so an index either machine
    builds is valid on both -- the engine is not part of the signature."""
    signature = retrieval.embedding_signature("local")
    assert signature == "local:%s" % retrieval.LOCAL_MODEL
    for backend in ("auto", "onnx", "sentence-transformers"):
        monkeypatch.setattr(retrieval, "LOCAL_BACKEND", backend)
        assert retrieval.embedding_signature("local") == signature
    if "EMBED_LOCAL_MODEL" not in __import__("os").environ:
        assert retrieval.LOCAL_MODEL == embed_onnx.MODEL


def test_the_eval_cache_never_answers_an_onnx_query_with_a_pc_vector(monkeypatch, tmp_path):
    """data/eval_query_cache.npz holds 106 eval questions embedded by
    sentence-transformers under "local". An ONNX eval reading those would
    report having measured ONNX while measuring the PC."""
    enc = embed_onnx.OnnxEncoder(FakeSession(), FakeTokenizer())
    monkeypatch.setattr(retrieval, "_local_encoder", lambda: enc)
    stale = np.array([0.0, 1.0], dtype=np.float32)
    monkeypatch.setattr(retrieval, "_query_cache_path", tmp_path / "cache.npz")
    monkeypatch.setattr(retrieval, "_QUERY_CACHE", {"local\x00RETRIEVAL_QUERY\x00aa bbbb": stale})
    out = retrieval.embed(["aa bbbb"], "RETRIEVAL_QUERY", "local")
    np.testing.assert_allclose(out[0], np.array([5.0, 1.0]) / np.hypot(5, 1), rtol=1e-6)
    assert "local+onnx\x00RETRIEVAL_QUERY\x00aa bbbb" in retrieval._QUERY_CACHE
    # the PC engine keeps its old keys (no re-embedding of August's cache)
    monkeypatch.setattr(retrieval, "_local_encoder", lambda: object())
    assert retrieval._cache_space("local") == "local" and retrieval._cache_space("gemini") == "gemini"


def test_the_pins_are_a_commit_and_a_sha256_per_file():
    assert len(embed_onnx.REVISION) == 40 and int(embed_onnx.REVISION, 16) >= 0
    assert set(embed_onnx.FILES) == {"model.onnx", "tokenizer.json"}
    for name, (path, digest, size) in embed_onnx.FILES.items():
        url = embed_onnx.url(name)
        assert url.startswith("https://huggingface.co/%s/resolve/%s/" % (embed_onnx.MODEL, embed_onnx.REVISION))
        assert url.endswith("/" + path) and "/resolve/main/" not in url
        assert len(digest) == 64 and int(digest, 16) >= 0 and size > 0
    # fp32, not the int8 export that measured min cosine 0.971
    assert embed_onnx.FILES["model.onnx"][0] == "onnx/model.onnx"
    assert embed_onnx.fetch_map() == {"%s/%s" % (embed_onnx.REL_DIR, n): embed_onnx.url(n)
                                      for n in embed_onnx.FILES}
    assert embed_onnx.DIR == ROOT / embed_onnx.REL_DIR


# ---- the measurement: same vectors as the PC ------------------------------------------------

#: Thai, English and Chinese beyond the eval set, including the shapes that
#: stress tokenisation: empty, one character, far past 128 tokens.
_PROBES = [
    "สวัสดีค่ะ ยินดีต้อนรับสู่ Embassy World", "ห้องนอนมีกี่ห้อง", "ใกล้ทะเลไหม", "มีที่จอดรถไหม",
    "สระว่ายน้ำบนดาดฟ้าเปิดกี่โมง", "ผ่อนคลายที่ซาวน่า", "ขอดูแบบห้องหน่อยค่ะ", "อยากได้ห้องวิวทะเล",
    "Where is the nearest beach?", "How far is the airport?", "This condominium has a sky pool.",
    "Can I see a two-bedroom unit?", "Is there a gym for residents?", "pet friendly?",
    "我想看看样板间", "这个项目离海边多远", "停车场在哪里", "有健身房吗", "可以养宠物吗",
    "", "ก", "x" * 3000, "ฟิตเนส " * 400,
]


def _l2(v):
    v = np.asarray(v, dtype=np.float64)
    return v / np.where(np.linalg.norm(v, axis=1, keepdims=True) == 0, 1, np.linalg.norm(v, axis=1, keepdims=True))


@pytest.mark.timeout(300)
def test_the_robot_engine_puts_text_in_the_pcs_space():
    """The claim the robot's whole semantic search rests on: the ONNX
    engine's vectors ARE sentence-transformers' vectors, so the PC-built
    embeddings.npz is valid on the robot without a rebuild. Bar: mean cosine
    >= 0.999, min >= 0.995 (measured 2026-10-09: min 0.9999998 on 180 texts;
    the int8 export missed it at 0.971). Runs where both engines and the
    model exist -- the PC after `python scripts/fetch_embed_model.py`."""
    missing = [m for m in ("sentence_transformers", "onnxruntime", "tokenizers")
               if importlib.util.find_spec(m) is None]
    if missing:
        pytest.skip("%s not installed: ONNX-vs-sentence-transformers equivalence (why the robot may use the "
                    "PC's embeddings.npz) was NOT checked" % ", ".join(missing))
    if not embed_onnx.files_present():
        pytest.skip("no ONNX model here (python scripts/fetch_embed_model.py): ONNX-vs-sentence-transformers "
                    "equivalence was NOT checked")
    if retrieval.LOCAL_MODEL != embed_onnx.MODEL:
        pytest.skip("EMBED_LOCAL_MODEL=%s is not the exported model: equivalence was NOT checked"
                    % retrieval.LOCAL_MODEL)
    st = retrieval._st_encoder()
    if st is None:
        pytest.skip("sentence-transformers could not load %s (offline, no HF cache?): equivalence was NOT checked"
                    % retrieval.LOCAL_MODEL)
    onnx = embed_onnx.load()
    assert onnx is not None, "the model files are present but the loader refused them -- see the log"
    assert st.max_seq_length == embed_onnx.MAX_SEQ_LENGTH

    from app.config import settings
    from app.knowledge_policy import evaluate_claim
    from app.tools.slide_search import _document_text
    from app.tools.slides import load_slides

    sys.path.insert(0, str(ROOT / "scripts"))
    import eval_search

    slides = [s for s in load_slides() if s.get("source_id") == "slide_catalog"
              and evaluate_claim(s, settings.project_id).allowed]
    docs = [_document_text(s) for s in slides]
    questions = [q.lstrip("!") for q, _ in eval_search.load_questions(None)]
    texts = list(dict.fromkeys(docs + questions + _PROBES))
    assert len(texts) >= 40

    a = _l2(st.encode(texts, convert_to_numpy=True, show_progress_bar=False))
    b = _l2(onnx.encode(texts))
    cos = (a * b).sum(axis=1)
    worst = int(np.argmin(cos))
    assert cos.mean() >= 0.999 and cos.min() >= 0.995, (
        "ONNX is not the PC's space: n=%d mean %.6f min %.6f (worst %r)"
        % (len(texts), cos.mean(), cos.min(), texts[worst][:60]))

    # And the robot's real use: its vectors against the index the PC built.
    index = Path(settings.slides_dir).expanduser() / "embeddings.npz"
    if docs and index.is_file():
        with np.load(index, allow_pickle=False) as data:
            fresh = (str(data["fingerprint"]) == retrieval.fingerprint_of(docs)
                     and str(data["provider"]) == "local")
            stored = np.asarray(data["vectors"]) if fresh else None
        if stored is not None:
            against = (_l2(onnx.encode(docs)) * _l2(stored)).sum(axis=1)
            assert against.min() >= 0.995, "ONNX slide vectors vs embeddings.npz: min %.6f" % against.min()
