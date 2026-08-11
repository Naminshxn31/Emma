"""
Hybrid retrieval over the slide library: BM25 for words, embeddings for
meaning, fused by rank.

Why this replaced a hand-written scorer
---------------------------------------
The previous version compared overlapping character n-grams. Thai has no
spaces, so n-grams were a way to avoid needing a tokeniser — but they compare
*letters*, not words, and unrelated Thai words share letters constantly. The
failure that forced the rewrite: **ราคา** (price) and **อาคาร** (building)
both contain "าคา", so a guest asking the price got a picture of a building.
Patching that with rarity weighting, longer n-grams and a hand-written list
of banned words made it better, but each fix was a workaround for using the
wrong comparison in the first place. A real tokeniser splits ราคาโครงการ into
["ราคา", "โครงการ"] and the collision simply doesn't exist.

The n-gram path is still here, as the fallback when pythainlp isn't
installed. It is worse; it is not broken.

The half that was missing entirely
----------------------------------
Lexical search can only match words that are literally present. Two things
that mattered here and no amount of BM25 tuning would fix:

- **Synonyms.** สระว่ายน้ำ, สระลากูน and SKY POOL are the same facility. The
  old code only connected them because I typed the missing word into the
  slide's keywords by hand — which does not scale and silently rots.
- **Other languages.** A Chinese guest asking 游泳池 shares no characters
  with any slide. Lexical search returns nothing, forever, unless a human
  writes Chinese keywords for all 144 slides.

Embeddings handle both without anyone maintaining a vocabulary.

Degrading
---------
Every layer is optional and the system answers without it:

    pythainlp missing  -> character n-grams instead of words
    no API key         -> BM25 only, no semantic matching
    no network / error -> BM25 only, logged once, conversation unaffected

The sales gallery keeps working; it just gets less clever.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import unicodedata
from pathlib import Path
from typing import Any

logger = logging.getLogger("condo_voice.retrieval")

# ============================ tokenisation ============================

_THAI = re.compile(r"[฀-๿]")

#: Han and kana. Written without spaces, and there's no segmenter here for
#: them, so they get character bigrams — the standard fallback in CJK
#: retrieval and enough for BM25 to have an opinion. Hangul is deliberately
#: not in this set: Korean *does* put spaces between words.
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿]")


def _chunks(text: str) -> list[str]:
    """Split into word-ish runs, in any writing system.

    Not a regex, because the obvious ones are both wrong. `[A-Za-z0-9]+|[฀-๿]+`
    silently dropped every other script on earth — "где бассейн" and 游泳池
    tokenised to nothing, so the lexical half of the search was dead for
    those guests. Replacing it with `\\w+` broke Thai and Hindi instead:
    Unicode combining marks (the vowels and tone marks in ส-ร-ะ-ว-่-า-ย) are
    category Mn, which `\\w` does not match, so it split inside words.

    Keeping letters, digits and marks, and treating everything else as a
    separator, is correct for all of them.
    """
    out: list[str] = []
    current: list[str] = []
    for ch in text:
        if unicodedata.category(ch)[0] in "LMN":
            current.append(ch)
        elif current:
            out.append("".join(current))
            current = []
    if current:
        out.append("".join(current))
    return out

_tokeniser: Any = None
_tokeniser_ready = False


def _build_tokeniser(vocabulary: list[str]) -> Any:
    """pythainlp's newmm segmenter, taught the words in this deck.

    The stock dictionary is general Thai, so the project's own vocabulary —
    ซาวน่า, ลากูน, สกายพูล, ฟิตเนส — gets shredded into meaningless pieces
    ("ซาวน่า" -> ซาว + น่า). Those words are already written down in the
    slide keywords, so the deck teaches the tokeniser its own terms and stays
    correct as slides are added.
    """
    from pythainlp.corpus.common import thai_words
    from pythainlp.util import dict_trie

    words = set(thai_words())
    for phrase in vocabulary:
        phrase = phrase.strip()
        # Multi-word English keywords ("lagoon pool") are handled by the
        # Latin path; only Thai runs need teaching.
        if phrase and _THAI.search(phrase) and len(phrase) > 1:
            words.add(phrase)
    return dict_trie(dict_source=words)


def init_tokeniser(vocabulary: list[str] | None = None) -> bool:
    """Returns True if real word segmentation is available."""
    global _tokeniser, _tokeniser_ready
    _tokeniser_ready = True
    try:
        _tokeniser = _build_tokeniser(vocabulary or [])
        logger.info("Thai word segmentation enabled (pythainlp)")
        return True
    except Exception:
        _tokeniser = None
        logger.warning(
            "pythainlp unavailable — falling back to character n-grams, which "
            "confuse Thai words that share letters. `pip install pythainlp` "
            "to fix.", exc_info=True,
        )
        return False


def _ngrams(text: str) -> list[str]:
    """Fallback when there's no segmenter. Same shape as the old scorer."""
    out: list[str] = []
    for size in (3, 4, 5):
        if len(text) <= size:
            out.append(text)
        else:
            out.extend(text[i:i + size] for i in range(len(text) - size + 1))
    return out


#: Words that carry no topic. Needed because guests speak in questions:
#: "มีห้องฟิตเนสไหมคะ" is one topic word wrapped in five particles, and a
#: slide deck contains no particles at all — so every one of them counted as
#: "something the guest asked for that isn't here", and a perfectly good
#: match scored as a miss. Only ever applied when *judging* a match; the
#: index itself keeps every word.
_EXTRA_STOPWORDS = {
    # Thai question frames and politeness, beyond pythainlp's list.
    "มี", "ไหม", "มั้ย", "หรือ", "คะ", "ค่ะ", "ครับ", "อยาก", "ขอ", "ดู",
    "อยู่", "ที่ไหน", "ไหน", "อะไร", "ยัง", "เห็น", "ช่วย", "หน่อย", "บ้าง",
    "ได้", "เป็น", "ใคร", "ทำไม", "เท่าไหร่", "กี่", "นะ", "หรอ", "เหรอ",
    # English equivalents.
    "do", "does", "did", "you", "have", "has", "is", "are", "the", "a", "an",
    "any", "what", "where", "which", "who", "how", "there", "i", "we", "can",
    "could", "would", "please", "show", "me", "tell", "about", "of", "to",
    "and", "or", "in", "on", "at", "for", "with", "your", "it", "this",
}

_stopwords: set[str] | None = None


def stopwords() -> set[str]:
    global _stopwords
    if _stopwords is None:
        words = set(_EXTRA_STOPWORDS)
        try:
            from pythainlp.corpus import thai_stopwords

            words |= set(thai_stopwords())
        except Exception:
            pass
        _stopwords = words
    return _stopwords


def content_tokens(tokens: list[str]) -> list[str]:
    """Drop the words that don't name a topic.

    Falls back to the original list if *everything* was a stopword, so a
    query like "ที่ไหน" still ranks against something rather than nothing.
    """
    kept = [t for t in tokens if t not in stopwords() and len(t) > 1]
    return kept or tokens


def robust_tokens(text: str) -> list[str]:
    """Words, plus character n-grams of the Thai runs as a safety net.

    Thai segmentation is ambiguous and newmm sometimes picks wrong:
    "ทำเลอยู่ตรงไหน" comes out as ทำ|เลอ|ยู่|ตรงไหน, because เลอ really is a
    word — and the topic (ทำเล) vanishes. n-grams don't care where the
    boundaries are, so they catch what the segmenter drops.

    Used for **ranking only**. Judging whether a match is real still goes by
    words alone, because n-grams are exactly what confused ราคา with อาคาร;
    here they can only ever add a candidate, never certify one.
    """
    tokens = tokenize(text)
    for chunk in _chunks(unicodedata.normalize("NFKC", text or "").lower()):
        if _THAI.search(chunk):
            tokens.extend(_ngrams(chunk))
    return tokens


def tokenize(text: str) -> list[str]:
    if not text:
        return []
    text = unicodedata.normalize("NFKC", text).lower()
    if not _tokeniser_ready:
        init_tokeniser()

    tokens: list[str] = []
    for chunk in _chunks(text):
        if _THAI.search(chunk):
            if _tokeniser is not None:
                from pythainlp.tokenize import word_tokenize

                tokens.extend(
                    w for w in word_tokenize(chunk, custom_dict=_tokeniser, engine="newmm")
                    if w.strip()
                )
            else:
                tokens.extend(_ngrams(chunk))
        elif _CJK.search(chunk):
            # No spaces and no segmenter: bigrams, plus the single characters,
            # since a lot of Chinese content words are one character.
            tokens.extend(chunk)
            tokens.extend(chunk[i:i + 2] for i in range(len(chunk) - 1))
        else:
            # Latin, Cyrillic, Greek, Arabic, Devanagari, digits — all of
            # these are already space-delimited by the time _CHUNK ran.
            tokens.append(chunk)
    return tokens


# ============================== BM25 ==============================

#: Okapi BM25 defaults. k1 controls how fast repeated terms stop helping;
#: b how hard long documents are penalised.
BM25_K1 = 1.5
BM25_B = 0.75


class BM25:
    """Okapi BM25 over pre-tokenised documents.

    Implemented here rather than pulled in as a dependency: it is thirty
    lines, this deployment runs on a robot with no package manager attached,
    and the formula is fixed.
    """

    def __init__(self, documents: list[list[str]]) -> None:
        self.docs = documents
        self.n = len(documents)
        self.lengths = [len(d) for d in documents]
        self.avg_length = (sum(self.lengths) / self.n) if self.n else 0.0

        self.freqs: list[dict[str, int]] = []
        df: dict[str, int] = {}
        for doc in documents:
            counts: dict[str, int] = {}
            for token in doc:
                counts[token] = counts.get(token, 0) + 1
            self.freqs.append(counts)
            for token in counts:
                df[token] = df.get(token, 0) + 1

        # Lucene's variant: always positive, so a term in every document
        # contributes ~0 instead of going negative and actively penalising.
        self.idf = {
            t: math.log(1 + (self.n - c + 0.5) / (c + 0.5)) for t, c in df.items()
        }

    def scores(self, query_tokens: list[str]) -> list[float]:
        out = [0.0] * self.n
        if not self.n:
            return out
        for token in set(query_tokens):
            idf = self.idf.get(token)
            if idf is None:
                continue          # not in the corpus: nothing to score against
            for i, counts in enumerate(self.freqs):
                f = counts.get(token, 0)
                if not f:
                    continue
                norm = 1 - BM25_B + BM25_B * (self.lengths[i] / (self.avg_length or 1))
                out[i] += idf * (f * (BM25_K1 + 1)) / (f + BM25_K1 * norm)
        return out

    def coverage(self, query_tokens: list[str], index: int) -> float:
        """Share of the query's IDF mass this document actually contains.

        Ranking says which document is closest; this says whether "closest"
        means anything. Used to decide no-match, where BM25's own score is
        unusable because its scale moves with query length.
        """
        wanted = set(content_tokens(query_tokens))
        if not wanted:
            return 0.0
        # An unseen term is maximally informative: the guest asked for
        # something this deck never mentions.
        max_idf = math.log(1 + (self.n + 0.5) / 0.5) if self.n else 1.0
        total = sum(self.idf.get(t, max_idf) for t in wanted)
        if total <= 0:
            return 0.0
        counts = self.freqs[index]
        found = sum(self.idf[t] for t in wanted if t in counts and t in self.idf)
        return found / total


# ========================= semantic index =========================

EMBED_MODEL = "gemini-embedding-001"
#: Matryoshka truncation. 144 slides do not need 3072 dimensions, and the
#: smaller vector is faster to compare and cheaper to cache. Anything below
#: 3072 comes back un-normalised and MUST be normalised before use.
EMBED_DIM = 768
_EMBED_BATCH = 32


_numpy_checked = False
_numpy_ok = False


def numpy_available() -> bool:
    """Checked once, up front, rather than discovered mid-request.

    Semantic search is meant to be optional — every other missing piece
    degrades quietly. numpy didn't, because one `import numpy` sat outside
    the try block that was supposed to catch exactly this. The exception
    escaped through the search all the way into the tool result, so the
    robot told a guest "No module named 'numpy'" instead of showing a slide.
    """
    global _numpy_checked, _numpy_ok
    if not _numpy_checked:
        _numpy_checked = True
        try:
            import numpy  # noqa: F401

            _numpy_ok = True
        except Exception:
            _numpy_ok = False
            logger.warning(
                "numpy is not installed — semantic search is off, so synonyms "
                "and cross-language questions won't match. Lexical search "
                "still works. Fix with: pip install numpy"
            )
    return _numpy_ok


def _l2_normalise(vectors):
    import numpy as np

    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0, 1, norms)


#: Local, offline embeddings. Small enough to ship on the gallery PC and
#: multilingual, which is the whole reason semantic search exists here — a
#: Chinese or Russian question has to find a Thai slide.
LOCAL_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
_local = None


def local_embeddings_installed() -> bool:
    """Is the offline backend *available*, without paying to find out.

    `find_spec` locates the package without executing it. That distinction is
    the whole point: importing `sentence_transformers` pulls in `transformers`,
    which walks its own `models/` directory opening every file to build an
    import map, and on Windows that took long enough to blow a twenty-second
    test timeout. The stack looked like a hang. It was a library loading.

    `choose_provider()` only ever wanted to know whether the option exists —
    and it asks that question at *startup*, to print one line in the banner. On
    a machine with the package installed and no API key, answering it by
    loading a whole transformer model is a cost paid by everyone, forever, for
    a log message.

    Actually loading the model stays in `_local_encoder`, where it is paid by
    whoever genuinely embeds something.
    """
    import importlib.util

    return importlib.util.find_spec("sentence_transformers") is not None


def _local_encoder():
    """Load the offline model once, or None if it isn't installed."""
    global _local
    if _local is not None:
        return _local
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        logger.info(
            "local embeddings unavailable — `pip install sentence-transformers` "
            "to run search without the API"
        )
        return None
    try:
        logger.info("loading local embedding model %s (first run downloads it)", LOCAL_MODEL)
        _local = SentenceTransformer(LOCAL_MODEL)
        return _local
    except Exception:
        logger.warning("could not load the local embedding model", exc_info=True)
        return None


def embedding_signature(provider: str) -> str:
    """Which embedding space a set of vectors lives in.

    Folded into the cache fingerprint, and that is not bookkeeping — it is the
    one thing that stops this feature producing confident nonsense.

    Vectors from two different models are not comparable. Cosine similarity
    between a Gemini document vector and a MiniLM query vector does not error;
    it returns a number, and that number ranks slides at random. Build the
    index on the API, lose the API, fall back to local for queries, and search
    quietly starts answering "ฟิตเนส" with the executive team photo — with no
    warning anywhere, because nothing failed.

    So the provider is part of the identity of the cache. Change it and the
    index is stale by definition.
    """
    if provider == "local":
        return "local:%s" % LOCAL_MODEL
    return "gemini:%s:%d" % (EMBED_MODEL, EMBED_DIM)


def _looks_like_a_rate_limit(exc: Exception) -> bool:
    """A quota refusal, as opposed to a bad key or a broken request.

    Matched on text because the SDK raises a generic ClientError for both,
    and retrying a 401 forever would be worse than failing fast.
    """
    text = str(exc).lower()
    return any(
        marker in text
        for marker in ("429", "resource_exhausted", "rate limit", "quota", "exceeded")
    )


def _embed_local(texts: list[str]):
    """Offline embeddings, L2-normalised to match the API path."""
    import numpy as np

    model = _local_encoder()
    if model is None:
        raise RuntimeError("local embedding model is not available")
    vectors = model.encode(list(texts), convert_to_numpy=True, show_progress_bar=False)
    return _l2_normalise(np.asarray(vectors, dtype="float32"))


#: Query vectors already paid for, keyed by text. Off unless a caller turns it
#: on — see `use_query_cache`.
#:
#: Written for `scripts/eval_search.py`. Embedding 127 questions back to back
#: exceeded the free tier's 100 requests per minute *for the embedding model*,
#: and the run continued with semantic search silently switched off: every
#: question after the 429 was scored lexically, reported a similarity of
#: -1.00, and was quietly dropped from the calibration. The suggested
#: threshold that came out the other side was computed on a fraction of the
#: questions, and looked exactly as authoritative as a complete one.
#:
#: A cache fixes it properly rather than by sleeping: the same question asked
#: twice costs one request ever, so re-running the eval after a change is both
#: free and instant. Never used by the live robot, where each guest question is
#: genuinely new and staleness would be a bug.
_QUERY_CACHE: dict[str, Any] = {}
_query_cache_path: Path | None = None


def use_query_cache(path: Path | None) -> None:
    """Turn the query-embedding cache on, backed by `path`. None turns it off."""
    global _query_cache_path, _QUERY_CACHE

    _query_cache_path = path
    _QUERY_CACHE = {}
    if path is None or not path.exists():
        return
    try:
        import numpy as np

        with np.load(path, allow_pickle=False) as data:
            _QUERY_CACHE = {k: data[k] for k in data.files}
        logger.info("query cache: %d questions already embedded", len(_QUERY_CACHE))
    except Exception:
        logger.warning("could not read the query cache at %s — starting empty", path)


def save_query_cache() -> None:
    if _query_cache_path is None or not _QUERY_CACHE:
        return
    try:
        import numpy as np

        _query_cache_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(_query_cache_path, **_QUERY_CACHE)
    except Exception:
        logger.warning("could not save the query cache", exc_info=True)


def embed(texts: list[str], task_type: str, provider: str, *, attempts: int = 1):
    """Embed with a named provider. Never mixes — see `embedding_signature`."""
    # One query, cache on: answer from disk if this exact question has been
    # embedded before. Only ever a single query — batches are index builds,
    # which have their own cache and must not be short-circuited per item.
    if _query_cache_path is not None and len(texts) == 1:
        import numpy as np

        key = "%s\x00%s\x00%s" % (provider, task_type, texts[0])
        hit = _QUERY_CACHE.get(key)
        if hit is not None:
            return np.asarray([hit])

    if provider == "local":
        vectors = _embed_local(texts)
    else:
        # attempts>1 when the cache is on: the eval asks 127 questions in a
        # row and the free tier allows 100 embeddings a minute, so without
        # backoff the run trips a 429 partway and finishes with semantic
        # search silently switched off — which is how a calibration got
        # computed on a fraction of its own question list and still printed a
        # confident number.
        vectors = _embed(texts, task_type,
                         attempts=max(attempts, 5 if _query_cache_path else 1))

    if _query_cache_path is not None and len(texts) == 1:
        key = "%s\x00%s\x00%s" % (provider, task_type, texts[0])
        _QUERY_CACHE[key] = vectors[0]
    return vectors


def choose_provider() -> str:
    """Which embedding backend to build with, from EMBED_PROVIDER.

    `auto` prefers the API — it is better, and this deck is small enough that
    building costs a handful of requests — but falls back to local rather than
    leaving search switched off. The decision is made once, for a whole index,
    and recorded in the cache.
    """
    from app.config import settings

    choice = (settings.embed_provider or "auto").strip().lower()
    if choice in {"gemini", "api"}:
        return "gemini"
    if choice == "local":
        return "local"
    if choice in {"off", "none", "false"}:
        return "off"
    # auto
    if settings.gemini_api_key:
        return "gemini"
    # `local_embeddings_installed`, not `_local_encoder`: this runs in the
    # startup banner, and loading the model here would make every boot pay for
    # a question that only needed to know whether the package exists.
    if local_embeddings_installed():
        logger.info("no GEMINI_API_KEY — using local embeddings")
        return "local"
    return "off"


def _embed(texts: list[str], task_type: str, *, attempts: int = 1):
    """One or more embeddings from the Gemini API, L2-normalised.

    `attempts` is the whole reason this has retry logic, and why the default
    is 1 rather than something generous.

    The free tier caps this model at 100 requests a minute. Building the
    index is 144 slides in batches of 32 — five requests — but every server
    start with no cached `embeddings.npz` does it again, and a day of
    restarts walked the peak to 96/100. Past that the call raises, the caller
    logs "continuing with lexical search only", **the file is never written**,
    and the next start begins from nothing. A loop that cannot finish while
    it is being throttled, which is why semantic search had never once run.
    (RPD was only 160/1000 — it is the per-minute limit that binds, and a
    per-minute limit is exactly what waiting fixes.)

    So the build path asks for patience. The *query* path must not: it runs
    inside a tool call with a 15-second ceiling, and a guest asking "มีฟิตเนส
    ไหม" would get silence and then a timeout error. Failing there costs one
    lexical-only answer, which is a far better trade.
    """
    import time

    import numpy as np
    from google import genai
    from google.genai import types

    from app.config import settings

    client = genai.Client(api_key=settings.gemini_api_key)
    vectors: list[list[float]] = []
    for start in range(0, len(texts), _EMBED_BATCH):
        batch = texts[start:start + _EMBED_BATCH]
        for attempt in range(1, attempts + 1):
            try:
                response = client.models.embed_content(
                    model=EMBED_MODEL,
                    contents=batch,
                    config=types.EmbedContentConfig(
                        task_type=task_type, output_dimensionality=EMBED_DIM
                    ),
                )
                break
            except Exception as exc:
                if attempt >= attempts or not _looks_like_a_rate_limit(exc):
                    raise
                # The cap is per *minute*, so the wait has to be able to reach
                # a minute. 5s, 10s, 20s, 40s, 60s, 60s...
                pause = min(5 * 2 ** (attempt - 1), 60)
                logger.info(
                    "embedding rate-limited (batch %d, attempt %d/%d) — waiting %ds",
                    start // _EMBED_BATCH + 1, attempt, attempts, pause,
                )
                time.sleep(pause)
        vectors.extend(e.values for e in response.embeddings)
    return _l2_normalise(np.asarray(vectors, dtype="float32"))


class SemanticIndex:
    """Slide embeddings, built once and cached next to the slide index."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.vectors = None
        self.fingerprint = ""
        #: Which embedding space `self.vectors` live in. Queries must use the
        #: same one; mixing two returns plausible numbers and wrong slides.
        self.provider = ""

    # -- persistence --

    def load(self, fingerprint: str) -> bool:
        try:
            import numpy as np

            with np.load(self.path, allow_pickle=False) as data:
                if str(data["fingerprint"]) != fingerprint:
                    logger.info("slide embeddings are stale — deck changed")
                    return False
                cached = str(data["provider"]) if "provider" in data else ""
                wanted = choose_provider()
                if cached and wanted != "off" and cached != wanted:
                    # Not a preference — a correctness requirement. Querying
                    # a Gemini-built index with a local encoder ranks slides
                    # at random and reports no error at all.
                    logger.info(
                        "slide embeddings were built with %r but %r is active — rebuilding",
                        cached, wanted,
                    )
                    return False
                self.vectors = data["vectors"]
                self.fingerprint = fingerprint
                self.provider = cached or "gemini"
                return True
        except Exception:
            return False

    def save(self) -> None:
        import numpy as np

        self.path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            self.path, vectors=self.vectors, fingerprint=self.fingerprint,
            provider=self.provider or "gemini",
        )

    # -- use --

    def build(self, texts: list[str], fingerprint: str) -> None:
        provider = choose_provider()
        if provider == "off":
            raise RuntimeError("no embedding provider available")
        logger.info("embedding %d slides with %s", len(texts), embedding_signature(provider))
        try:
            # Patient on purpose — this runs once and the result is cached to
            # disk. Waiting out a per-minute cap here is what stops every
            # restart from starting over. See `_embed`.
            self.vectors = embed(texts, "RETRIEVAL_DOCUMENT", provider, attempts=6)
        except Exception:
            # The fallback is whole-index, never per-batch. Half a deck in one
            # space and half in another would be worse than no index at all:
            # the halves are not comparable, and nothing would say so.
            if provider != "gemini" or _local_encoder() is None:
                raise
            logger.warning(
                "the embedding API failed — rebuilding the whole index locally",
                exc_info=True,
            )
            provider = "local"
            self.vectors = embed(texts, "RETRIEVAL_DOCUMENT", provider)
        self.provider = provider
        self.fingerprint = fingerprint
        self.save()

    def similarities(self, query: str):
        """Cosine similarity to every slide. Both sides are unit vectors, so
        the dot product is the cosine."""
        if self.vectors is None:
            return None
        # Same space as the vectors were built in, always. A query embedded
        # by a different model would be compared against these by cosine
        # similarity, which returns a number rather than an error.
        return self.vectors @ embed(
            [query], "RETRIEVAL_QUERY", self.provider or "gemini"
        )[0]


# ============================== fusion ==============================

#: Reciprocal rank fusion. Combines rankings without needing the two scores
#: to share a scale — BM25 is unbounded and cosine similarity is 0–1, so
#: adding them directly would be meaningless. k=60 is the value from the
#: original RRF paper and is not sensitive.
RRF_K = 60


def reciprocal_rank_fusion(rankings: list[list[int]], weights: list[float]) -> dict[int, float]:
    """`rankings` are lists of document indices, best first."""
    fused: dict[int, float] = {}
    for ranking, weight in zip(rankings, weights):
        for rank, index in enumerate(ranking):
            fused[index] = fused.get(index, 0.0) + weight / (RRF_K + rank + 1)
    return fused


def fingerprint_of(payload: Any) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
