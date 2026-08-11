"""
Tests for search_condo_info — the retrieval that lets the assistant answer
questions instead of only showing pictures.
"""
from __future__ import annotations

import asyncio

import pytest

from app.tools import registry


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def loaded():
    import app.tools as tools_pkg
    from app.tools import slides as sl

    tools_pkg.load_tools()
    sl.reload_slides()
    return sl


def test_the_tool_is_declared_to_the_model(loaded):
    from app.providers.openai_realtime import build_session_config

    names = [t["name"] for t in build_session_config("marin", "x")["session"]["tools"]]
    assert "search_condo_info" in names


@pytest.mark.parametrize("question", [
    "มีฟิตเนสไหม",
    "สระว่ายน้ำอยู่ชั้นไหน",
    "ห้องนอนเป็นยังไง",
    "do you have a gym",
])
def test_answers_spoken_questions_not_just_keywords(loaded, question):
    """Guests ask in whole sentences. Scoring only by "share of the query
    found" diluted those below the threshold — a real match was being
    reported as no information at all."""
    out = run(registry.dispatch("search_condo_info", {"query": question}))
    assert out["ok"] is True
    assert out["found"] is True, f"{question} returned nothing"
    assert out["results"][0]["detail"] or out["results"][0]["title"]


# ---- the screen must not contradict the answer ----
#
# Reported as "คำตอบกับสไลด์ไม่ตรง": asking ราคาโครงการ put an unrelated
# slide on screen while the reply correctly said there was no pricing yet.


@pytest.mark.parametrize("question", [
    "ราคาโครงการ",
    "ราคาเริ่มต้นเท่าไหร่",
    "โปรโมชั่นตอนนี้มีอะไรบ้าง",
    "ห้อง 1 ห้องนอนราคาเท่าไหร่",
    "how much is it",
    "any promotion",
    # A Thai sales gallery sells to Chinese, Russian, Japanese and Korean
    # buyers, and price is the first thing any of them asks. The guard
    # listed Thai and English words only, so all of these walked past it.
    "价格是多少",
    "多少钱",
    "цена квартиры",
    "いくらですか",
    "얼마예요",
])
def test_commercial_questions_are_refused_outright(loaded, question):
    """Slide summaries describe pictures; they are not an approved pricing
    source. Left to scoring, "ราคาโครงการ" matched every slide containing
    โครงการ — a word in a third of the deck."""
    from app.tools.knowledge import _is_commercial

    # Assert the guard itself, not just the outcome. Without embeddings a
    # Chinese query finds nothing anyway, so checking only `found is False`
    # passes even with the guard removed — which is exactly what happened
    # the first time this was written.
    assert _is_commercial(question), "%s slipped past the pricing guard" % question

    out = run(registry.dispatch("search_condo_info", {"query": question}))
    assert out["found"] is False, f"{question} must not be answered from slides"
    assert out["results"] == []
    assert "ฝ่ายขาย" in out["instruction"]


def test_a_commercial_question_never_changes_the_screen(loaded):
    """The specific reported failure. The words were right; the picture
    behind them was of something else entirely."""
    import app.tools.slides as sl

    run(registry.dispatch("show_slide", {"query": "ฟิตเนส"}))
    before = sl.current_slide()["id"]
    run(registry.dispatch("search_condo_info", {"query": "ราคาโครงการ"}))
    assert sl.current_slide()["id"] == before, "pricing question moved the screen"


def test_a_weak_match_leaves_the_screen_alone(loaded):
    """Answering in words and changing the picture are separate decisions.
    A doubtful hit may still be worth reading out; it is never worth
    showing, because the guest is looking at the screen.

    (This used to use "do you have a gym", which hybrid search now matches
    outright — a cross-language hit the old n-gram scorer could not make.)
    """
    import app.tools.slides as sl

    run(registry.dispatch("show_slide", {"query": "ฟิตเนส"}))
    before = sl.current_slide()["id"]
    run(registry.dispatch("search_condo_info", {"query": "ราคาหุ้นวันนี้"}))
    assert sl.current_slide()["id"] == before


def test_nonsense_finds_nothing(loaded):
    for query in ("zzzz ไม่มีอยู่จริง qqqq", "ราคาหุ้นวันนี้"):
        out = run(registry.dispatch("search_condo_info", {"query": query}))
        assert out["found"] is False, query


# ---- the imported page documents ----


def test_page_documents_reached_the_index(loaded):
    """85 slides carry per-page documents from the emma project. The Canva
    deck's 59 pages do not — those still need narration from the sales
    team, which no generated description can substitute for."""
    slides = loaded.load_slides()
    with_detail = [s for s in slides if s.get("detail_th")]
    assert len(with_detail) == 85
    assert not any(s["id"].startswith("ew-") for s in with_detail)


def test_the_two_kinds_of_text_stay_separate(loaded):
    """One is the developer's own words off the slide; the other a model's
    description of a picture. Merging them lets a generated sentence be
    quoted with the authority of the deck."""
    out = run(registry.dispatch("search_condo_info", {"query": "ขอดูเบสเมนต์"}))
    top = out["results"][0]
    assert "slide_text" in top or "description" in top
    if "slide_text" in top and "description" in top:
        assert top["slide_text"] != top["description"]


def test_facility_details_are_findable(loaded):
    """Things a guest asks about that appear in no title, summary or
    keyword — only in the description of what the picture shows."""
    for query in ("ลู่วิ่ง", "ม่านน้ำ", "ศาลานั่งเล่น", "จุดชาร์จรถไฟฟ้า"):
        out = run(registry.dispatch("search_condo_info", {"query": query}))
        assert out["found"] is True, query


def test_returns_several_results_to_choose_from(loaded):
    out = run(registry.dispatch("search_condo_info", {"query": "สิ่งอำนวยความสะดวก"}))
    assert out["found"] is True
    assert 1 <= len(out["results"]) <= 4


def test_results_carry_a_way_to_show_the_slide(loaded):
    """So the assistant can offer "อยากดูภาพไหมคะ" after answering."""
    out = run(registry.dispatch("search_condo_info", {"query": "ฟิตเนส"}))
    assert out["results"][0]["slide_query"]


def test_price_questions_find_nothing_here(loaded):
    """Prices belong in data/condo_facts.json, which is authored and approved. Slide
    summaries are descriptive and must never become a pricing source."""
    out = run(registry.dispatch("search_condo_info", {"query": "ราคาเท่าไหร่"}))
    assert out["found"] is False


def test_no_result_tells_the_model_not_to_guess(loaded):
    out = run(registry.dispatch("search_condo_info", {"query": "zzzz qqqq ไม่มีจริง"}))
    assert out["found"] is False
    assert "ห้ามเดา" in out["instruction"]


def test_results_warn_against_quoting_them_as_pricing(loaded):
    out = run(registry.dispatch("search_condo_info", {"query": "ฟิตเนส"}))
    assert "ราคา" in out["note"]


def test_prompt_requires_lookup_before_answering():
    from app.prompts import build_instructions

    assert "search_condo_info" in build_instructions("X")


def test_narration_script_is_offered_as_approved_copy(loaded, monkeypatch):
    library = loaded.load_slides()
    target = dict(library[0])
    target["script_th"] = "บทที่อนุมัติแล้ว"
    monkeypatch.setattr(loaded, "_slides", [target] + library[1:])

    out = run(registry.dispatch("search_condo_info", {"query": target["title_th"]}))
    assert out["results"][0]["approved_script"] == "บทที่อนุมัติแล้ว"


def test_answering_a_question_also_updates_the_screen(loaded):
    """The assistant described a sauna while a lounge slide stayed up. The
    picture contradicting the words is worse than showing nothing, so a
    lookup puts its best match on screen as part of answering."""
    from app.tools import slides as sl

    run(registry.dispatch("show_slide", {"query": "social club"}))
    before = sl.current_slide()["id"]

    out = run(registry.dispatch("search_condo_info", {"query": "มีซาวน่าไหม"}))
    after = sl.current_slide()["id"]

    assert out["now_showing"], "lookup should report what it put on screen"
    assert after != before, "screen still showing the previous topic"
    assert after == out["now_showing"]["id"]


def test_a_lookup_that_finds_nothing_leaves_the_screen_alone(loaded):
    """Better to keep the last relevant slide than blank the screen."""
    from app.tools import slides as sl

    run(registry.dispatch("show_slide", {"query": "ฟิตเนส"}))
    before = sl.current_slide()["id"]
    run(registry.dispatch("search_condo_info", {"query": "zzzz qqqq ไม่มีจริง"}))
    assert sl.current_slide()["id"] == before


def test_lookup_is_tagged_so_displays_are_updated(loaded):
    """Without the slides tag the registry wouldn't push to /display."""
    entry = registry.get("search_condo_info")
    assert "slides" in entry.tags


def test_every_nonsense_query_the_suite_asserts_on_is_also_measured():
    """Calibrate against the same questions you assert against.

    `scripts/eval_search.py` reported "rejects all 9 bad questions" and the
    suite still had two red tests, because the tests use nonsense strings the
    eval list had never seen. A threshold measured on one set and asserted on
    another is not measured at all.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "eval_search", "scripts/eval_search.py")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except SystemExit:
        pass

    measured = {q.lstrip("!") for q, _ in module.DEFAULT_QUESTIONS}
    asserted = {"zzzz ไม่มีอยู่จริง qqqq", "ราคาหุ้นวันนี้", "zzzz qqqq ไม่มีจริง"}
    missing = asserted - measured
    assert not missing, (
        "these are asserted on but never measured, so the threshold was "
        "calibrated blind to them: %s" % sorted(missing)
    )


def test_a_slide_ranked_eighth_cannot_answer_on_its_own(loaded, monkeypatch):
    """The gap between what the eval measured and what the tool did.

    `scripts/eval_search.py` judges `hits[0]` and reported every nonsense
    question rejected. This tool judged *every* hit, so a slide the ranking
    put eighth could still answer — and two tests kept failing while the
    calibration said everything was fine.

    Ranking is Reciprocal Rank Fusion over BM25 and cosine, so first place is
    the best compromise between the two, not the highest similarity. A late
    hit carrying a high cosine is exactly the case this produces, and it is
    only reachable on a machine where the semantic half actually runs — which
    is why it never showed up in CI.
    """
    from app.tools import registry
    from app.tools import slides as slides_mod
    from app.tools.slide_search import Hit

    deck = loaded.load_slides()[:9]
    ranked = [
        Hit(slide=s, score=1.0 - i / 10, coverage=0.0,
            # Nothing is close enough until the eighth, which sneaks over the
            # line on similarity alone.
            similarity=0.90 if i == 8 else 0.10, standout=0.0)
        for i, s in enumerate(deck)
    ]
    monkeypatch.setattr(slides_mod, "search_slides", lambda q: ranked)

    out = run(registry.dispatch("search_condo_info", {"query": "อะไรก็ตามที่เด็คไม่มี"}))
    assert out["found"] is False, (
        "a slide the ranking put eighth answered a question the first eight "
        "could not"
    )
    assert "ห้ามเดา" in out["instruction"]


def test_the_top_hit_still_brings_its_neighbours(loaded, monkeypatch):
    """The other half: when the closest slide *is* close enough, the other
    qualifying ones still come along. Narrowing the rule must not turn every
    answer into a single slide."""
    from app.tools import registry
    from app.tools import slides as slides_mod
    from app.tools.slide_search import Hit

    deck = loaded.load_slides()[:3]
    ranked = [
        Hit(slide=s, score=1.0 - i / 10, coverage=1.0, similarity=0.90, standout=3.0)
        for i, s in enumerate(deck)
    ]
    monkeypatch.setattr(slides_mod, "search_slides", lambda q: ranked)

    out = run(registry.dispatch("search_condo_info", {"query": "ขอดูสระว่ายน้ำ"}))
    assert out["found"] is True
    assert len(out["results"]) > 1, "narrowed the rule into a single-slide answer"


def test_questions_only_an_approved_fact_can_answer_never_reach_the_slides():
    """Found by measuring, not by thinking of them.

    `scripts/eval_search.py` showed these three walking straight past the
    commercial guard into the slide search, and coming back with a picture:

        ห้องขายเปิดกี่โมง   -> ภาพจำลอง COMMON SPHERE ชั้น 3
        โอนได้เมื่อไหร่     -> ทุกวันนี้เราไปได้ไกลกว่าที่ฝันไว้
        ต่างชาติซื้อได้ไหม  -> ความฝันไม่เคยเปลี่ยน

    None of them is about money, which is why the list missed them, but each
    is answerable only from something a person signed off. Opening hours is
    one of the four fields still blank in `condo_facts.json`; ownership and
    handover are commitments a developer makes. A caption written by a model
    looking at a photograph is not a source for any of them.
    """
    from app.tools.knowledge import _is_commercial

    for query in ("ห้องขายเปิดกี่โมง", "ต่างชาติซื้อได้ไหม", "โอนได้เมื่อไหร่",
                  "what time do you open", "can foreigners buy", "freehold or leasehold"):
        assert _is_commercial(query), query


def test_ordinary_questions_are_not_swept_up_by_that_list():
    """The cost of widening the guard is refusing real questions, and the
    words involved — เวลา, โอน, ต่างชาติ — turn up in innocent sentences."""
    from app.tools.knowledge import _is_commercial

    for query in ("มีฟิตเนสไหม", "สระว่ายน้ำอยู่ชั้นไหน", "ขอดูห้องนอน",
                  "ไปสนามบินยังไง", "ชั้น 3 มีอะไร", "where is the lobby",
                  "ขอดูผังโครงการ", "มีที่ให้เด็กเล่นไหม", "ทำเลอยู่ตรงไหน"):
        assert not _is_commercial(query), query
