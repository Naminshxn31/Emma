"""
The search index over the slide library.

Holds the BM25 index, the embeddings, and the rule for deciding whether a
result is good enough to say out loud or to put on the screen. Built lazily
on first use and rebuilt whenever the deck changes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.tools import project_knowledge
from app.tools.retrieval import (
    BM25,
    SemanticIndex,
    content_tokens,
    fingerprint_of,
    init_tokeniser,
    numpy_available,
    reciprocal_rank_fusion,
    robust_tokens,
    tokenize,
)

logger = logging.getLogger("condo_voice.slide_search")

_reranker = None
_reranker_failed = False

#: Title and keywords are written *for* retrieval, so they count for more
#: than prose. Repetition is how you weight a field in single-field BM25.
_FIELD_REPEATS = {
    "title_th": 3, "title_en": 3,
    "keywords_th": 3, "keywords_en": 3,
    "summary_th": 1, "summary_en": 1,
    # What the picture shows, imported from emma/knowledge/pages by
    # scripts/import_page_docs.py. This is what a guest asking "มีลู่วิ่งไหม"
    # is actually after and no title mentions it. Weighted at 1: it is long,
    # and repeating long text would let one verbose slide dominate BM25's
    # length normalisation.
    "detail_th": 1, "detail_en": 1,
}

# `transcript_*` — every word printed on the slide — is deliberately NOT in
# that table, which is the opposite of what I expected before measuring.
# Against the real deck, lexical only:
#
#                       facility-detail recall   full eval
#   before import              10/12               29/32
#   + transcript only          12/12               27/32
#   + description only         12/12               30/32   <- best
#   + both                     12/12               29/32
#
# The transcripts recover the same recall and cost precision, because a
# floor-plan transcript is word soup — BLDG.E, KEY PLAN, OFFICE — that
# matches loosely and means little. It stays in index.json, where it is the
# developer's own approved wording and the right thing to quote from; it
# just isn't useful for finding a slide.

#: How much of the question must be present before we claim to have found
#: anything. Lexical only — see `Hit.confident` for the semantic side.
MIN_COVERAGE = 0.34

#: Competitor slides rank below this project's own, all else equal.
OTHER_PROJECT_PENALTY = 0.35

#: Cosine similarity, which unlike BM25 is an absolute 0–1 measure and so can
#: carry a fixed threshold. These are starting values from what the model
#: reports for related vs unrelated text; `scripts/eval_search.py` measures
#: them against your own deck and tells you where the gap actually is.
MIN_SIMILARITY = float(settings.search_min_similarity)
SHOW_SIMILARITY = float(settings.search_show_similarity)

def _standout(similarities, index: int) -> float:
    """How far slide `index` sits above the deck's own average, in sigmas.

    Deliberately relative. The absolute cosine cannot be thresholded on this
    deck — measured, not assumed — because the embedding model's floor is
    around 0.6 rather than 0. Asking "is this slide unusual *for this query*"
    sidesteps where the floor happens to be.

    Returns -1.0 when there is nothing to measure, matching `similarity`.
    """
    if similarities is None or len(similarities) < 2:
        return -1.0
    mean = float(similarities.mean())
    spread = float(similarities.std())
    if spread < 1e-9:          # every slide identical; nothing stands out
        return 0.0
    return (float(similarities[index]) - mean) / spread


@dataclass
class Hit:
    slide: dict
    score: float          # fused rank score, for ordering only
    coverage: float       # share of the question's lexical mass present
    similarity: float     # cosine, or -1.0 when embeddings are unavailable
    #: How far this slide's similarity stands above the rest of the deck's,
    #: in standard deviations. -1.0 when embeddings are unavailable.
    #:
    #: Measured because the raw cosine turned out not to separate anything.
    #: On this deck, `gemini-embedding-001` scores 0.644 for the *worst real
    #: match* and 0.649 for the *best piece of nonsense* — the distributions
    #: overlap, so no threshold exists. That is a property of the model, not
    #: of the deck: it never uses the bottom of its range, so "unrelated"
    #: lands around 0.6 rather than near zero.
    #:
    #: A quantity that doesn't care where the floor sits: for a real question
    #: one slide should stand out *from its own deck*, while nonsense is
    #: uniformly mediocre against all 144. Reported by scripts/eval_search.py
    #: alongside the cosine so the two can be compared on real numbers before
    #: anything starts depending on it.
    standout: float = -1.0
    min_similarity: float = MIN_SIMILARITY
    show_similarity: float = SHOW_SIMILARITY
    min_coverage: float = MIN_COVERAGE
    domain_match: bool = False

    @property
    def found(self) -> bool:
        """Worth answering from."""
        if self.similarity >= 0:
            return self.similarity >= self.min_similarity or self.coverage >= self.min_coverage
        return self.coverage >= self.min_coverage

    @property
    def confident(self) -> bool:
        """Worth *showing*. Higher bar: the guest is looking at the screen,
        and a picture that contradicts the words costs more than no picture.
        """
        if self.domain_match:
            return True
        if self.similarity >= 0:
            return self.similarity >= self.show_similarity
        # Lexical-only fallback: demand most of the question be present.
        return self.coverage >= 0.6


def _document_text(slide: dict) -> str:
    parts: list[str] = []
    for field, repeats in _FIELD_REPEATS.items():
        value = slide.get(field)
        if isinstance(value, list):
            value = " ".join(str(v) for v in value)
        if value:
            parts.extend([str(value)] * repeats)
    return " ".join(parts)


def _rerank(query: str, indices: list[int], slides: list[dict]) -> list[int]:
    """Optional local cross-encoder pass over an already-small candidate set."""
    global _reranker, _reranker_failed
    model_name = settings.search_reranker_model.strip()
    if not model_name or _reranker_failed or len(indices) < 2:
        return indices
    try:
        if _reranker is None:
            from sentence_transformers import CrossEncoder
            _reranker = CrossEncoder(model_name)
            logger.info("local reranker ready: %s", model_name)
        pairs = [(query, _document_text(slides[i])) for i in indices]
        scores = _reranker.predict(pairs, show_progress_bar=False)
        return [i for _, i in sorted(zip(scores, indices), reverse=True)]
    except Exception:
        _reranker_failed = True
        logger.warning("local reranker unavailable — keeping hybrid ranking", exc_info=True)
        return indices


class SlideSearch:
    def __init__(self) -> None:
        self.slides: list[dict] = []
        self.bm25: BM25 | None = None
        self.semantic: SemanticIndex | None = None
        self._embedding_failed = False

    # ---- building ----

    def build(self, slides: list[dict]) -> None:
        self.slides = slides
        if not slides:
            self.bm25, self.semantic = None, None
            return

        # Teach the tokeniser this project's vocabulary before tokenising
        # anything, or its own keywords come out shredded.
        vocabulary: list[str] = []
        for slide in slides:
            for field in ("keywords_th", "keywords_en"):
                vocabulary.extend(str(k) for k in (slide.get(field) or []))
        init_tokeniser(vocabulary)

        documents = [robust_tokens(_document_text(s)) for s in slides]
        self.bm25 = BM25(documents)
        logger.info("indexed %d slides for lexical search", len(slides))

        self._build_semantic(slides)

    def _build_semantic(self, slides: list[dict]) -> None:
        if not settings.search_semantic or not settings.gemini_api_key:
            logger.info("semantic search off — lexical only")
            self.semantic = None
            return

        if not numpy_available():
            self.semantic = None
            return

        path = Path(settings.slides_dir).expanduser() / "embeddings.npz"
        fingerprint = fingerprint_of([_document_text(s) for s in slides])
        index = SemanticIndex(path)

        if index.load(fingerprint):
            self.semantic = index
            logger.info("loaded cached slide embeddings")
            return

        try:
            index.build([_document_text(s) for s in slides], fingerprint)
            self.semantic = index
        except Exception:
            # A sales gallery that answers slightly worse beats one that
            # doesn't start. Say so loudly, once, then carry on.
            logger.warning(
                "could not embed the slides — continuing with lexical search "
                "only. Cross-language and synonym matching will not work.",
                exc_info=True,
            )
            self.semantic = None

    # ---- searching ----

    def search(self, query: str, limit: int = 8) -> list[Hit]:
        if not self.bm25 or not self.slides:
            return []
        if project_knowledge.unavailable(query):
            return []

        # Two token sets, for the two decisions. Ranking gets n-grams too, so
        # a bad segmentation still finds the slide; judging the match gets
        # words only, so a letter coincidence can't certify one. Question
        # particles are dropped from both — a slide deck contains none, so
        # scoring them buries the one word that mattered.
        words = content_tokens(tokenize(query))
        lexical = self.bm25.scores(content_tokens(robust_tokens(project_knowledge.expand(query))))
        preferred_ids = project_knowledge.preferred_slide_ids(query)
        preferred_rank = {slide_id: rank for rank, slide_id in enumerate(preferred_ids)}
        if preferred_ids:
            peak = max(lexical, default=0.0) + 1.0
            for i, slide in enumerate(self.slides):
                if slide.get("id") in preferred_rank:
                    lexical[i] += peak / (preferred_rank[slide["id"]] + 1)
        by_lexical = sorted(range(len(self.slides)), key=lambda i: -lexical[i])

        similarities = None
        if self.semantic is not None and not self._embedding_failed:
            try:
                similarities = self.semantic.similarities(query)
            except Exception:
                self._embedding_failed = True
                logger.warning(
                    "embedding the query failed — semantic search is off for "
                    "the rest of this run", exc_info=True,
                )

        # A ranking with no signal in it must not get a vote. When BM25 finds
        # nothing — every score zero, which is what a query in a script the
        # tokeniser doesn't cover produces — the order is just the order the
        # slides happen to sit in the file. Feeding that into the fusion gave
        # slide #1 a 40% endorsement for no reason.
        lexical_has_signal = any(score > 0 for score in lexical)

        rankings: list[list[int]] = []
        weights: list[float] = []
        if lexical_has_signal:
            # Slightly favour meaning over spelling: the lexical side is the
            # one that produced the original bug.
            rankings.append(by_lexical)
            weights.append(0.4 if similarities is not None else 1.0)
        if similarities is not None:
            by_semantic = sorted(range(len(self.slides)), key=lambda i: -similarities[i])
            rankings.append(by_semantic)
            weights.append(0.6 if lexical_has_signal else 1.0)
        if not rankings:
            return []

        provider = self.semantic.provider if self.semantic is not None else None
        if provider == "local":
            min_similarity = settings.search_local_min_similarity
            show_similarity = settings.search_local_show_similarity
            min_coverage = settings.search_local_min_coverage
        else:
            min_similarity = MIN_SIMILARITY
            show_similarity = SHOW_SIMILARITY
            min_coverage = MIN_COVERAGE

        fused = reciprocal_rank_fusion(rankings, weights)

        # The library carries competitors' decks for comparison. They answer
        # "how do we compare" and nothing else — asking to see the pool was
        # surfacing another development's pool ahead of this project's.
        for i, slide in enumerate(self.slides):
            if slide.get("type") == "other-project" and i in fused:
                fused[i] *= OTHER_PROJECT_PENALTY

        # A strong literal match is more precise than a semantic compromise.
        # RRF remains useful when wording differs or scripts differ, but it
        # must not move "สระว่ายน้ำ" from the pool slide to Aqua Cinema merely
        # because both are about water.  Route high-coverage queries through
        # BM25 first; semantic search still owns cross-language/synonym cases.
        lexical_coverages = {
            i: self.bm25.coverage(words, i) for i in fused
        }
        if preferred_ids:
            # A matched multilingual/domain alias is lexical evidence too.
            # Without this, the right Chinese/Russian slide ranks first and is
            # then rejected because the Thai deck shares no literal glyphs.
            for i, slide in enumerate(self.slides):
                if i in lexical_coverages and slide.get("id") in preferred_rank:
                    lexical_coverages[i] = 1.0
        strong_lexical = lexical_has_signal and any(
            coverage >= min_coverage for coverage in lexical_coverages.values()
        )
        candidate_count = max(limit, settings.search_reranker_candidates)
        if strong_lexical:
            order = sorted(
                fused,
                key=lambda i: (-lexical[i], -lexical_coverages[i], -fused[i]),
            )[:candidate_count]
        else:
            order = sorted(fused, key=lambda i: -fused[i])[:candidate_count]

        candidates = order[:candidate_count]
        if settings.search_reranker_model.strip() and not preferred_ids:
            order = _rerank(query, candidates, self.slides)[:limit]
        else:
            order = order[:limit]

        return [
            Hit(
                slide=self.slides[i],
                score=fused[i],
                coverage=lexical_coverages[i],
                similarity=float(similarities[i]) if similarities is not None else -1.0,
                standout=_standout(similarities, i),
                min_similarity=min_similarity,
                show_similarity=show_similarity,
                min_coverage=min_coverage,
                domain_match=self.slides[i].get("id") in preferred_rank,
            )
            for i in order
        ]

    @property
    def semantic_enabled(self) -> bool:
        return self.semantic is not None and not self._embedding_failed


_index: SlideSearch | None = None


def get_index(slides: list[dict]) -> SlideSearch:
    global _index
    if _index is None or _index.slides is not slides:
        _index = SlideSearch()
        _index.build(slides)
    return _index


def reset() -> None:
    global _index, _reranker, _reranker_failed
    _index = None
    _reranker = None
    _reranker_failed = False
    project_knowledge.reset()
