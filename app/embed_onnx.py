"""The local embedding model without torch: the ONNX export of the SAME model.

Why this exists (9 Oct 2026): the whole Emma server now runs on the robot
(scripts/robot_server.py -- Python 3.14 on Android arm64). torch and
sentence-transformers are not there and must not be (hundreds of MB of wheels
for one matrix multiply a minute), so every query embed raised "local
embedding model is not available" and slide search ran lexical-only. The slide
index (embeddings.npz) is built on the PC with sentence-transformers; the robot
needs to put its *queries* in that same space or the index is useless to it.

So this is not "a small model for the robot". It is the same weights from the
same repo commit (`REVISION` = the snapshot in the PC's HF cache that built the
index), exported to ONNX by the model's own publisher, run through the same two
steps sentence-transformers runs for this model (modules.json at that commit):

    0  Transformer   max_seq_length 128 (sentence_bert_config.json)
    1  Pooling       mean over the attention mask (1_Pooling/config.json)

and nothing else -- there is no Normalize module; `retrieval._embed_local`
L2-normalises the output of either backend the same way. Same vectors, so
`embedding_signature` stays "local:<model>" and an index built by either
machine is valid on both. That claim is MEASURED, not assumed:
tests/test_embed_onnx.py compares the two backends text by text wherever both
exist (mean cosine >= 0.999, min >= 0.995), and the numbers are in CLAUDE.md.

Kept out of app/tools/ on purpose: importing anything under app.tools runs the
tool registry. scripts/robot_server.py and scripts/fetch_embed_model.py read
the pins below, and this module imports nothing but the standard library at
load time -- numpy, onnxruntime and tokenizers only inside `load()`.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger("condo_voice.retrieval")

#: The one model this export IS. retrieval refuses to serve any other
#: EMBED_LOCAL_MODEL through these files: MiniLM vectors under another model's
#: signature would be two spaces with one name -- the bug the signature exists
#: to make impossible.
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
#: The repo commit of the PC's sentence-transformers cache (refs/main on
#: 2026-10-09), i.e. the weights that built embeddings.npz. The ONNX file and
#: the tokenizer come from this commit, not from `main`, so the pins below stay
#: true if the publisher re-exports later.
REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
#: Where the files live, relative to the repo root (gitignored: 480 MB of
#: weights that one command restores -- scripts/fetch_embed_model.py on a PC,
#: robot_server.py's FETCH_ON_ROBOT on the robot).
REL_DIR = "data/models/paraphrase-multilingual-MiniLM-L12-v2"
DIR = Path(__file__).resolve().parent.parent / REL_DIR

#: file on disk -> (path in the HF repo at REVISION, sha256, bytes).
#:
#: fp32 (`onnx/model.onnx`), chosen by measurement on 2026-10-09 against
#: sentence-transformers on this PC (48 indexed slides + 126 eval questions +
#: Thai/English/Chinese probes): fp32 min cosine 0.9999998, eval_search 112/126
#: with identical per-question outcomes; the int8 export of the same commit
#: (`onnx/model_qint8_arm64.onnx`, 118 MB, ~2x faster) mean 0.9929 min 0.9712
#: -- under the bar (mean >= 0.999, min >= 0.995), so it would need its own
#: signature and a rebuilt index on every machine. The robot has 16 GB of RAM
#: and downloads at ~6 MB/s, so 470 MB costs ~80 s once.
#: The tokenizer is byte-identical to the one sentence-transformers loads on
#: the PC (same sha256 as the HF cache's snapshot).
FILES: dict[str, tuple[str, str, int]] = {
    "model.onnx": ("onnx/model.onnx",
                   "10f7a088420252b26caf819236ca2c9d2987afd0fc06fec7553b542a5655a05a", 470301610),
    "tokenizer.json": ("tokenizer.json",
                       "2c3387be76557bd40970cec13153b3bbf80407865484b209e655e5e4729076b8", 9081518),
}
#: sentence_bert_config.json at REVISION. Longer text is cut here by both
#: backends; a different number here is a different vector for every slide
#: longer than 128 tokens (most of them).
MAX_SEQ_LENGTH = 128
#: Two cores, like FACE_THREADS: the robot's face recogniser runs on the same
#: CPU, and a one-question embed is small (PC, 2 threads: median 4.8 ms, p95
#: 6 ms -- sentence-transformers with every core: ~12 ms). onnxruntime's
#: default is every core.
THREADS = 2
_BATCH = 32         # sentence-transformers' default batch size


def url(name: str) -> str:
    """The pinned download URL of FILES[name] (immutable: a commit, not main)."""
    return "https://huggingface.co/%s/resolve/%s/%s" % (MODEL, REVISION, FILES[name][0])


def fetch_map() -> dict[str, str]:
    """repo-relative path -> URL, for robot_server.FETCH_ON_ROBOT."""
    return {"%s/%s" % (REL_DIR, name): url(name) for name in FILES}


def files_present(directory: Path | None = None) -> bool:
    """Are the files there -- without reading them (startup banner path)."""
    directory = Path(directory or DIR)
    return all((directory / name).is_file() for name in FILES)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def mean_pool(hidden, mask):
    """sentence-transformers' Pooling(mean_tokens): the average of the token
    vectors the attention mask keeps, divided by max(count, 1e-9) -- padding
    never votes. hidden (batch, seq, dim), mask (batch, seq) -> (batch, dim)."""
    import numpy as np

    weights = np.asarray(mask)[..., None].astype(np.float32)
    summed = (np.asarray(hidden, dtype=np.float32) * weights).sum(axis=1)
    return summed / np.clip(weights.sum(axis=1), 1e-9, None)


class OnnxEncoder:
    """`SentenceTransformer.encode` for this one model: tokenise (truncate at
    MAX_SEQ_LENGTH, pad to the longest in the batch), run the transformer,
    mean-pool. Not normalised -- `retrieval._embed_local` does that for both
    backends, so the two cannot drift apart on that step.

    `session` and `tokenizer` are passed in so the pooling and the
    tokenizer settings are testable with fakes; `load()` builds the real ones.
    Both are safe to share between threads once configured here (onnxruntime
    sessions are; the tokenizer is only read after __init__).
    """

    def __init__(self, session, tokenizer, max_seq_length: int = MAX_SEQ_LENGTH,
                 batch_size: int = _BATCH) -> None:
        tokenizer.enable_truncation(max_length=max_seq_length)
        pad_id = tokenizer.token_to_id("<pad>")
        tokenizer.enable_padding(pad_id=1 if pad_id is None else pad_id, pad_token="<pad>")
        self.session = session
        self.tokenizer = tokenizer
        self.max_seq_length = max_seq_length
        self.batch_size = batch_size
        self._inputs = {i.name for i in session.get_inputs()}
        outputs = [o.name for o in session.get_outputs()]
        # The publisher's export names it; fall back to the first output so a
        # re-export with another name fails the equivalence test, not silently.
        self._output = "last_hidden_state" if "last_hidden_state" in outputs else outputs[0]

    def encode(self, texts, **_ignored):
        """Same call shape as SentenceTransformer.encode; kwargs like
        convert_to_numpy/show_progress_bar are accepted and meaningless here."""
        import numpy as np

        texts = [str(t) for t in texts]
        chunks = []
        for start in range(0, len(texts), self.batch_size):
            batch = self.tokenizer.encode_batch(texts[start:start + self.batch_size])
            ids = np.asarray([e.ids for e in batch], dtype=np.int64)
            mask = np.asarray([e.attention_mask for e in batch], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._inputs:
                # XLM-R has one segment type; the PC's tokenizer does not even
                # send this input, and the model reads it as zeros.
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = self.session.run([self._output], feed)[0]
            chunks.append(mean_pool(hidden, mask))
        if not chunks:
            return np.zeros((0, 0), dtype=np.float32)
        return np.concatenate(chunks).astype(np.float32)


def load(directory: Path | None = None) -> OnnxEncoder | None:
    """The encoder, or None with one log line saying why. Never raises.

    The sha256 of every file is checked before onnxruntime sees it (~0.3 s
    for 470 MB, once per process, in the search warm-up thread). A file that
    is merely *present* is not enough: other weights under this signature
    would rank slides with plausible numbers and no error anywhere -- the
    mixed-space failure, arriving through a copied folder instead of a
    setting. A truncated download fails here too, instead of inside
    onnxruntime with a protobuf message nobody can act on.
    """
    directory = Path(directory or DIR)
    missing = [name for name in FILES if not (directory / name).is_file()]
    if missing:
        logger.info("ONNX embedding model not on this machine (%s missing in %s) -- "
                    "fetch it with: python scripts/fetch_embed_model.py",
                    ", ".join(missing), directory)
        return None
    try:
        import numpy  # noqa: F401
        import onnxruntime as ort
        from tokenizers import Tokenizer
    except ImportError as exc:
        logger.info("ONNX embeddings unavailable: %s", exc)
        return None
    bad = [name for name, (_path, digest, _size) in FILES.items()
           if sha256_file(directory / name) != digest]
    if bad:
        logger.warning("ONNX embedding files do not match their pinned sha256 (%s) -- not "
                       "loading them: other bytes are another embedding space. Re-fetch with: "
                       "python scripts/fetch_embed_model.py --force", ", ".join(bad))
        return None
    try:
        options = ort.SessionOptions()
        options.intra_op_num_threads = THREADS
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(str(directory / "model.onnx"), sess_options=options,
                                       providers=["CPUExecutionProvider"])
        tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        encoder = OnnxEncoder(session, tokenizer)
    except Exception:
        logger.warning("could not load the ONNX embedding model", exc_info=True)
        return None
    logger.info("local embedding model ready: ONNX %s (%d threads, sha256 verified)", MODEL, THREADS)
    return encoder
