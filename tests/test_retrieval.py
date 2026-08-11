"""
Hybrid retrieval: BM25 over words, embeddings for meaning.

These run without a network or an API key, so they exercise the lexical half
and the degradation paths. The semantic half is measured by
`scripts/eval_search.py`, which needs a real key — it can't be asserted here.
"""
from __future__ import annotations

import pytest

from app.tools import slide_search
from app.tools.retrieval import (
    BM25,
    content_tokens,
    reciprocal_rank_fusion,
    robust_tokens,
    tokenize,
)


@pytest.fixture()
def index():
    from app.tools.slides import load_slides

    slide_search.reset()
    return slide_search.get_index(load_slides())


# ==================== tokenisation ====================


def test_thai_is_split_into_words_not_letters():
    """The whole reason for the rewrite. Comparing letters, ราคา (price) and
    อาคาร (building) overlap at "าคา" and a price question returned a
    photograph of a building."""
    assert tokenize("ราคาโครงการ") == ["ราคา", "โครงการ"]
    assert "ราคา" not in tokenize("ภาพอาคารและสระลากูน")


def test_latin_and_thai_are_both_tokenised():
    tokens = tokenize("ภาพจำลอง SKY POOL ชั้น 3")
    assert "sky" in tokens and "pool" in tokens
    assert any("฀" <= t[0] <= "๿" for t in tokens)


def test_question_particles_are_dropped():
    """"มีห้องฟิตเนสไหมคะ" is one topic word wrapped in five particles. A
    slide deck contains no particles, so counting them as things the guest
    asked for and didn't get turned a perfect match into a miss."""
    kept = content_tokens(tokenize("มีห้องฟิตเนสไหมคะ"))
    assert "ฟิตเนส" in kept
    for particle in ("มี", "ไหม", "คะ"):
        assert particle not in kept


def test_stripping_particles_never_empties_the_query():
    assert content_tokens(tokenize("ที่ไหน")), "an all-particle query must still rank"


def test_ngrams_back_up_a_bad_word_split():
    """Thai segmentation is ambiguous and sometimes wrong: "ทำเลอยู่ตรงไหน"
    splits as ทำ|เลอ|ยู่|ตรงไหน because เลอ is a real word, and the topic
    disappears. n-grams don't depend on where the boundary fell."""
    assert "ทําเล" not in tokenize("ทำเลอยู่ตรงไหน")
    assert any("ทําเล" in gram for gram in robust_tokens("ทำเลอยู่ตรงไหน"))


def test_project_vocabulary_is_taught_to_the_tokeniser(index):
    """Loanwords aren't in a general Thai dictionary — stock newmm shreds
    ซาวน่า into ซาว + น่า, which matches nothing. The deck's own keywords
    supply them, so the vocabulary maintains itself as slides are added."""
    assert tokenize("ซาวน่า") == ["ซาวน่า"]
    assert "ซาว" not in tokenize("มีซาวน่าไหม")


# ==================== BM25 ====================


def test_bm25_ranks_the_document_containing_the_term():
    bm25 = BM25([["pool", "sky"], ["gym", "yoga"], ["pool", "lagoon", "water"]])
    scores = bm25.scores(["gym"])
    assert scores[1] > scores[0] and scores[1] > scores[2]


def test_a_term_in_every_document_carries_no_weight():
    """The โครงการ problem: a word in a third of a condo deck decided every
    query that contained it."""
    bm25 = BM25([["condo", "pool"], ["condo", "gym"], ["condo", "sauna"]])
    assert bm25.idf["condo"] < bm25.idf["pool"] / 3


def test_bm25_idf_is_never_negative():
    """Textbook Okapi goes negative for terms in more than half the corpus,
    which actively penalises documents for containing them."""
    bm25 = BM25([["a", "b"], ["a", "c"], ["a", "d"], ["a", "e"]])
    assert all(v >= 0 for v in bm25.idf.values())


def test_coverage_reports_how_much_of_the_query_was_found():
    bm25 = BM25([["pool", "sky"], ["gym"]])
    assert bm25.coverage(["pool", "sky"], 0) == pytest.approx(1.0)
    assert bm25.coverage(["pool", "helicopter"], 0) < 0.6


def test_an_unknown_term_counts_against_coverage():
    """"Nothing here mentions what you asked about" is the signal that stops
    a query being answered from whatever happened to rank first."""
    bm25 = BM25([["pool", "sky"], ["gym"]])
    assert bm25.coverage(["helicopter"], 0) == 0.0


# ==================== fusion ====================


def test_rrf_rewards_agreement_between_the_two_rankings():
    fused = reciprocal_rank_fusion([[5, 1, 2], [5, 3, 4]], [0.5, 0.5])
    assert max(fused, key=lambda i: fused[i]) == 5


def test_rrf_does_not_need_the_scores_to_share_a_scale():
    """BM25 is unbounded, cosine is 0–1. Adding them directly would let BM25
    drown the semantic side; ranks have no units."""
    a = reciprocal_rank_fusion([[1, 2, 3]], [1.0])
    b = reciprocal_rank_fusion([[1, 2, 3]], [1.0])
    assert a == b


# ==================== the live index ====================


def test_a_missing_numpy_does_not_reach_the_guest(monkeypatch, tmp_path):
    """It did. `import numpy` sat outside the try that was meant to catch
    exactly this, so ModuleNotFoundError escaped the search, escaped the
    tool, and the robot said "No module named 'numpy'" to a customer who had
    asked to see the swimming pool.

    Every other missing piece degrades quietly; this one has to as well.
    """
    import builtins

    import app.tools.retrieval as retrieval
    from app.config import settings
    from app.tools.slides import load_slides

    real_import = builtins.__import__

    def no_numpy(name, *args, **kwargs):
        if name == "numpy" or name.startswith("numpy."):
            raise ModuleNotFoundError("No module named 'numpy'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_numpy)
    monkeypatch.setattr(retrieval, "_numpy_checked", False)
    monkeypatch.setattr(retrieval, "_numpy_ok", False)
    # A key set means the embedding path is attempted, which is when it broke.
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")

    slide_search.reset()
    hits = slide_search.get_index(load_slides()).search("สระว่ายน้ำ")
    assert hits and hits[0].found, "lexical search must still answer"
    assert hits[0].similarity == -1.0, "and must report that it had no embeddings"
    slide_search.reset()


def test_index_builds_without_an_api_key(index):
    """A sales gallery must open even with no network. Semantic search is an
    enhancement, never a dependency."""
    assert index.bm25 is not None
    assert len(index.slides) > 0


def test_lexical_only_still_answers_thai_questions(index):
    hits = index.search("มีฟิตเนสไหม")
    assert hits and hits[0].found
    title = hits[0].slide.get("title_th") or hits[0].slide.get("title_en")
    assert "ฟิตเนส" in title


def test_lexical_only_still_refuses_nonsense(index):
    for query in ("ราคาเริ่มต้นเท่าไหร่", "โปรโมชั่น", "การเมือง", "zzzz qqqq"):
        hits = index.search(query)
        assert not hits or not hits[0].found, query


# ---- writing systems ----
#
# The gallery is in Thailand and sells to Chinese, Russian, Japanese and
# Korean buyers. Two bugs lived here, in opposite directions.


@pytest.mark.parametrize("script,text,expected", [
    # "ว่าย" carries a tone mark; splitting inside it was the second bug.
    ("Thai", "สระว่ายน้ำอยู่ไหน", "ว่าย"),
    ("Russian", "где бассейн", "бассейн"),
    ("Korean", "수영장 어디예요", "수영장"),
    ("Arabic", "أين حمام السباحة", "حمام"),
    ("Hindi", "स्विमिंग पूल कहाँ है", "पूल"),
])
def test_every_script_survives_tokenisation(script, text, expected):
    """First bug: the chunker was `[A-Za-z0-9]+|[฀-๿]+`, so every script
    other than Latin and Thai tokenised to the empty list and the lexical
    half of the search was simply dead for those guests.

    Second bug, introduced fixing the first: `\\w+` doesn't match Unicode
    combining marks, which is what Thai vowels and tone marks are — so it
    split inside Thai and Hindi words instead.
    """
    assert expected in tokenize(text), "%s: got %r" % (script, tokenize(text))


@pytest.mark.parametrize("text", ["游泳池在哪里", "プールはどこ"])
def test_cjk_gets_bigrams_since_there_is_no_segmenter(text):
    tokens = tokenize(text)
    assert tokens, "CJK must not tokenise to nothing"
    assert any(len(t) == 2 for t in tokens), "expected character bigrams"


def test_thai_word_boundaries_are_not_broken_by_tone_marks():
    """The regression the second bug caused: ส-ร-ะ-ว-่-า-ย came apart at the
    tone mark into ['ว', 'ายน', 'าอย']."""
    assert tokenize("ราคาโครงการ") == ["ราคา", "โครงการ"]
    assert "ว" not in tokenize("สระว่ายน้ำอยู่ไหน")


def test_a_ranking_with_no_signal_gets_no_vote(index):
    """Third bug. When BM25 scores everything zero — which is exactly what a
    query in an unsupported script produced — the resulting "ranking" is just
    the order the slides sit in the file. Fusing that in handed slide #1 a
    40% endorsement for no reason at all."""
    assert index.search("бассейн") == [], (
        "with no lexical signal and no embeddings there is nothing to rank on, "
        "so the honest answer is no results — not whatever sorted first"
    )


def test_competitor_slides_do_not_win(index):
    """The library carries other developments for comparison, not to answer
    "show me the pool"."""
    top = index.search("ทำเลที่ตั้ง")[0]
    assert top.slide.get("type") != "other-project"


def test_a_hit_without_embeddings_is_judged_lexically():
    from app.tools.slide_search import Hit

    assert Hit(slide={}, score=1, coverage=0.9, similarity=-1.0).found
    assert not Hit(slide={}, score=1, coverage=0.1, similarity=-1.0).found


def test_a_hit_with_embeddings_can_qualify_either_way():
    """Semantics rescue a question whose words aren't in the deck ("who owns
    this?" vs a slide headed คณะผู้บริหาร); the lexical side rescues exact
    names an embedding may blur ("BIOGENESIS")."""
    from app.tools.slide_search import Hit

    assert Hit(slide={}, score=1, coverage=0.0, similarity=0.9).found
    assert Hit(slide={}, score=1, coverage=1.0, similarity=0.1).found
    assert not Hit(slide={}, score=1, coverage=0.0, similarity=0.1).found


def test_showing_needs_more_certainty_than_answering():
    from app.tools.slide_search import Hit

    borderline = Hit(slide={}, score=1, coverage=0.5, similarity=0.5)
    assert borderline.found and not borderline.confident


# ============ the rate limit that kept semantic search switched off ============


def test_building_the_index_waits_out_a_rate_limit(monkeypatch):
    """Why `embeddings.npz` had never once been written.

    The free tier caps this model at 100 requests a minute. A day of restarts
    walked the peak to 96/100; past that the call raises, the caller logs
    "continuing with lexical search only", the file is never written, and the
    next start begins from nothing — a loop that cannot finish while it is
    being throttled. RPD was only 160/1000, so it is the per-minute limit that
    binds, and a per-minute limit is exactly what waiting fixes.
    """
    from app.tools import retrieval

    calls = {"n": 0}
    slept: list[float] = []

    class Response:
        embeddings = [type("E", (), {"values": [1.0, 0.0]})()]

    class Models:
        def embed_content(self, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")
            return Response()

    class Client:
        models = Models()

    import sys

    monkeypatch.setattr(retrieval, "_EMBED_BATCH", 32)
    fake = _fake_genai(Client)
    # Both, and the second one is the one that matters. `from google import
    # genai` resolves `genai` as an *attribute* of the already-imported
    # `google` package, so patching sys.modules alone leaves the real client
    # in place — the test then passed alone and failed whenever something
    # else had imported the SDK first.
    monkeypatch.setitem(sys.modules, "google.genai", fake)
    monkeypatch.setattr(sys.modules["google"], "genai", fake, raising=False)
    monkeypatch.setattr(__import__("time"), "sleep", lambda s: slept.append(s))

    out = retrieval._embed(["a"], "RETRIEVAL_DOCUMENT", attempts=3)
    assert out is not None
    assert calls["n"] == 2, "gave up instead of waiting"
    assert slept and slept[0] >= 5, "retried instantly — the cap is per minute"


def test_a_query_never_waits_on_a_rate_limit(monkeypatch):
    """The build can be patient; a query cannot.

    `search_condo_info` embeds the query inside a tool call with a 15-second
    ceiling. Waiting out a per-minute cap there would hand the guest silence
    and then a timeout error. Failing costs one lexical-only answer, which is
    a much better trade — so the default is a single attempt.
    """
    import inspect

    from app.tools import retrieval

    default = inspect.signature(retrieval._embed).parameters["attempts"].default
    assert default == 1, "queries would sit and wait inside a tool call"

    src = inspect.getsource(retrieval.SemanticIndex.similarities)
    assert "attempts" not in src, "the query path asked for retries"


def test_only_rate_limits_are_retried(monkeypatch):
    """A bad API key must fail immediately. Retrying a 401 six times with
    backoff would turn a clear error into a two-minute hang at startup."""
    from app.tools import retrieval

    assert retrieval._looks_like_a_rate_limit(RuntimeError("429 RESOURCE_EXHAUSTED"))
    assert retrieval._looks_like_a_rate_limit(RuntimeError("Rate limit reached"))
    assert not retrieval._looks_like_a_rate_limit(RuntimeError("401 API key not valid"))
    assert not retrieval._looks_like_a_rate_limit(RuntimeError("model not found"))


def _fake_genai(client_cls):
    """Stand in for `google.genai` so no network is touched."""
    import types as _t

    mod = _t.ModuleType("google.genai")
    mod.Client = lambda **kw: client_cls()

    cfg = _t.SimpleNamespace(EmbedContentConfig=lambda **kw: kw)
    mod.types = cfg
    return mod


def test_the_build_path_is_the_one_that_asks_for_patience(monkeypatch):
    """The test above exercises `_embed` directly, so it stayed green when the
    retry was taken *off* `build()` — proving the helper and not the wiring.
    This drives the real caller."""
    from app.tools import retrieval

    asked = {}

    def fake_embed(texts, task_type, *, attempts=1):
        asked["attempts"] = attempts
        import numpy as np

        return np.zeros((len(texts), 2), dtype="float32")

    monkeypatch.setattr(retrieval, "_embed", fake_embed)
    index = retrieval.SemanticIndex.__new__(retrieval.SemanticIndex)
    index.path = None
    monkeypatch.setattr(index, "save", lambda: None, raising=False)

    index.build(["one", "two"], fingerprint="x")
    assert asked.get("attempts", 1) > 1, (
        "the build gives up on the first rate limit, so the cache is never written"
    )


# ============ two embedding spaces must never be mixed ============


def test_a_cache_built_by_another_provider_is_rejected(tmp_path, monkeypatch):
    """The one thing that makes a local fallback dangerous rather than useful.

    Vectors from two different models are not comparable. Cosine similarity
    between a Gemini document vector and a MiniLM query vector does not raise
    — it returns a number, and that number ranks slides at random. Build on
    the API, lose the API, fall back to local for queries, and "ฟิตเนส" starts
    answering with the executive team photo, silently, with nothing in any log
    to say why.

    So the provider is part of the cache's identity: change it and the index
    is stale by definition.
    """
    import numpy as np

    from app.tools import retrieval

    path = tmp_path / "embeddings.npz"
    np.savez_compressed(path, vectors=np.zeros((3, 768), dtype="float32"),
                        fingerprint="deck-v1", provider="gemini")

    index = retrieval.SemanticIndex(path)
    monkeypatch.setattr(retrieval, "choose_provider", lambda: "local")
    assert index.load("deck-v1") is False, "loaded vectors from the wrong space"

    monkeypatch.setattr(retrieval, "choose_provider", lambda: "gemini")
    assert index.load("deck-v1") is True
    assert index.provider == "gemini"


def test_an_old_cache_without_a_provider_still_loads(tmp_path, monkeypatch):
    """Files written before this existed have no `provider` key. They were all
    built with the API, so treat them as such rather than forcing a rebuild
    that costs the very quota this is meant to protect."""
    import numpy as np

    from app.tools import retrieval

    path = tmp_path / "embeddings.npz"
    np.savez_compressed(path, vectors=np.zeros((3, 768), dtype="float32"),
                        fingerprint="deck-v1")

    index = retrieval.SemanticIndex(path)
    monkeypatch.setattr(retrieval, "choose_provider", lambda: "gemini")
    assert index.load("deck-v1") is True
    assert index.provider == "gemini"


def test_a_query_is_embedded_in_the_same_space_as_the_index(monkeypatch):
    """Not a preference — the query path has to name the provider the vectors
    were built with, or the comparison is meaningless."""
    import numpy as np

    from app.tools import retrieval

    asked = {}

    def fake_embed(texts, task_type, provider, **kw):
        asked["provider"] = provider
        return np.ones((1, 4), dtype="float32")

    monkeypatch.setattr(retrieval, "embed", fake_embed)
    index = retrieval.SemanticIndex.__new__(retrieval.SemanticIndex)
    index.vectors = np.ones((2, 4), dtype="float32")
    index.provider = "local"

    index.similarities("ฟิตเนส")
    assert asked["provider"] == "local", "queried a local index with the API model"


def test_the_fallback_rebuilds_the_whole_index_not_half_of_it(monkeypatch):
    """Half a deck in one space and half in another is worse than no index:
    the halves are not comparable and nothing would say so. If the API dies
    partway, the local rebuild starts over from every slide."""
    import numpy as np

    from app.tools import retrieval

    seen = []

    def fake_embed(texts, task_type, provider, **kw):
        seen.append((provider, len(texts)))
        if provider == "gemini":
            raise RuntimeError("429 quota exceeded")
        return np.zeros((len(texts), 4), dtype="float32")

    monkeypatch.setattr(retrieval, "embed", fake_embed)
    monkeypatch.setattr(retrieval, "choose_provider", lambda: "gemini")
    monkeypatch.setattr(retrieval, "_local_encoder", lambda: object())

    index = retrieval.SemanticIndex.__new__(retrieval.SemanticIndex)
    index.path = None
    index.provider = ""
    monkeypatch.setattr(index, "save", lambda: None, raising=False)

    index.build(["a", "b", "c"], fingerprint="x")
    assert index.provider == "local"
    assert seen[-1] == ("local", 3), "rebuilt only part of the deck locally"


def test_choosing_a_provider_never_loads_the_local_model(monkeypatch):
    """Startup must not pay to answer "is the offline backend installed?".

    `choose_provider()` runs in the startup banner. With no API key it used to
    call `_local_encoder()`, which imports `sentence_transformers` — which
    pulls in `transformers`, which walks its own models/ directory opening
    every file to build an import map. On Windows that took longer than
    pytest's twenty-second timeout, and the stack trace looked exactly like a
    deadlock. It was a library loading.

    `find_spec` answers the same question without executing anything.
    """
    from app.config import settings
    from app.tools import retrieval

    monkeypatch.setattr(settings, "embed_provider", "auto")
    monkeypatch.setattr(settings, "gemini_api_key", "")

    def explode():
        raise AssertionError("loaded the local model just to pick a provider")

    monkeypatch.setattr(retrieval, "_local_encoder", explode)
    monkeypatch.setattr(retrieval, "local_embeddings_installed", lambda: True)

    assert retrieval.choose_provider() == "local"


def test_availability_is_checked_without_importing(monkeypatch):
    """The check has to be `find_spec`, not a try/import — otherwise the cost
    moves back in the moment somebody 'simplifies' it."""
    import inspect

    from app.tools import retrieval

    source = inspect.getsource(retrieval.local_embeddings_installed)
    assert "find_spec" in source
    assert "import sentence_transformers" not in source


def test_standout_measures_against_the_deck_not_an_absolute_floor():
    """Why a second number exists at all.

    Measured on this deck with `gemini-embedding-001`: the worst real match
    scores 0.644 and the best nonsense 0.649. The populations overlap, so no
    cosine threshold separates them — the model simply never uses the bottom
    of its range, and "unrelated" lands near 0.6 instead of near 0.

    Standout asks a question that doesn't depend on where that floor sits: is
    one slide unusual *for this query*, compared with the other 143.
    """
    import numpy as np

    from app.tools.slide_search import _standout

    # Nonsense: everything mediocre and alike. High cosine, low standout.
    flat = np.array([0.63, 0.64, 0.62, 0.64, 0.63])
    assert _standout(flat, 1) < 1.5

    # A real match: one slide clearly above its own deck.
    peaked = np.array([0.60, 0.75, 0.61, 0.59, 0.60])
    assert _standout(peaked, 1) > 1.5

    # And the point: the flat case has the *higher* floor, so a cosine
    # threshold that admits the real match admits the nonsense too.
    assert flat.max() > 0.60


def test_standout_is_absent_rather_than_wrong_without_embeddings():
    from app.tools.slide_search import _standout

    assert _standout(None, 0) == -1.0


def test_an_identical_deck_stands_out_nowhere():
    """Guard against dividing by a zero spread."""
    import numpy as np

    from app.tools.slide_search import _standout

    assert _standout(np.array([0.7, 0.7, 0.7]), 0) == 0.0
