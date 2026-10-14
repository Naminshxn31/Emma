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
def loaded(monkeypatch):
    import app.tools as tools_pkg
    from app.tools import slides as sl

    tools_pkg.load_tools()
    sl.reload_slides()
    from tests.approval_fixture import approved_slides

    monkeypatch.setattr(sl, "_slides", approved_slides(sl.load_slides()))
    return sl


def test_the_tool_is_declared_to_the_model(loaded):
    from app.providers.openai_realtime import build_session_config

    names = [t["name"] for t in build_session_config("marin", "x")["session"]["tools"]]
    assert "search_condo_info" in names


def test_the_tool_keeps_location_intent_in_its_query(loaded):
    entry = registry.get("search_condo_info")
    assert "ห้ามย่อเหลือเพียงชื่อสถานที่จน intent ตำแหน่งหาย" in entry.description
    query_help = entry.parameters["properties"]["query"]["description"]
    assert "ต้องคงคำว่าอยู่ตรงไหน/อยู่ชั้นไหน" in query_help


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
    # Owner 12 Oct: hand off to the sales team naturally, never "ไม่มีข้อมูล".
    assert "ส่งต่อทีมขาย" in out["instruction"]
    assert "ห้ามพูดว่าไม่มีข้อมูล" in out["instruction"]


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


def test_page_documents_reached_the_index(loaded, monkeypatch):
    """A per-page document (detail_*) is searchable and comes back as its own
    field.

    This counted the 70 old slides that carried one. The 2026-10-06 library
    carries none — its approved text is title and summary — so the count
    went to 0 with nothing broken. The mechanism is what has to keep
    working for the next import that brings documents, so it is pinned with
    a record that has a word nothing else in the deck contains.
    """
    library = loaded.load_slides()
    target = dict(library[3], detail_en="The lounge has zebrawood panelling.")
    monkeypatch.setattr(loaded, "_slides", [target] + [s for s in library
                                                       if s["id"] != target["id"]])
    out = run(registry.dispatch("search_condo_info", {"query": "zebrawood"}))
    assert out["found"] is True, "a word only the page document holds was not indexed"
    assert "zebrawood" in out["results"][0].get("description", "")


def test_the_two_kinds_of_text_stay_separate(loaded):
    """One is the developer's own words off the slide; the other a model's
    description of a picture. Merging them lets a generated sentence be
    quoted with the authority of the deck.

    Pinned on a record carrying both — the shipped deck carries neither, so
    the search that used to find one ("ขอดูเบสเมนต์") had nothing to check.
    """
    from app.tools.knowledge import _entry

    record = dict(loaded.load_slides()[0], transcript_th="ข้อความบนสไลด์",
                  detail_th="คำบรรยายภาพที่โมเดลเขียน")
    top = _entry(record)
    assert top["slide_text"] == "ข้อความบนสไลด์"
    assert top["description"] == "คำบรรยายภาพที่โมเดลเขียน"
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


def test_a_broad_question_gets_the_sections_order_once(loaded):
    """Owner 2026-10-10: a broad "what is there" gets the sections and a
    question, not the list. The order rides on the result, because the
    result's own "ตอบเฉพาะข้อมูลที่ตรงคำถาม" beat the prompt rule in the first
    run; a specific question does not get it. Each state instruction is said
    once, not once per hit."""
    out = run(registry.dispatch("search_condo_info", {"query": "สิ่งอำนวยความสะดวก"}))
    assert len(out["results"]) > 1
    assert out["instruction"].count("ประโยคเดียวที่ชวนเลือกชั้น") == 1
    for item in out["results"]:
        caution = item.get("content_state_instruction")
        if caution:
            assert out["instruction"].count(caution) == 1
    one = run(registry.dispatch("search_condo_info", {"query": "สระว่ายน้ำอยู่ตรงไหน"}))
    assert "ประโยคเดียวที่ชวนเลือกชั้น" not in one["instruction"]


@pytest.mark.parametrize("question", ["Embassy World คืออะไร", "ที่จอดรถ EV charger อยู่ไหน", "ผังชั้นใต้ดิน"])
def test_the_decks_own_concept_caveat_never_reaches_the_model(loaded, question):
    """The cover and the basement plan print "ระบุว่าเป็นแนวคิดเบื้องต้น
    เพื่อการนำเสนอเท่านั้น และอาจเปลี่ยนแปลงได้..." in their approved summaries;
    the owner banned that sentence on 2026-10-10, so search hands the
    summary back without it."""
    from app.tools.retrieval import CONCEPT_CAVEAT

    out = run(registry.dispatch("search_condo_info", {"query": question}))
    for item in out.get("results", []):
        for key in ("title", "detail", "slide_text", "description", "approved_script"):
            assert not CONCEPT_CAVEAT.search(str(item.get(key) or "")), (question, key, item.get(key))


def test_a_specific_pool_location_returns_only_the_best_chunk(loaded):
    out = run(registry.dispatch(
        "search_condo_info", {"query": "Sky Pool อยู่ตรงไหน"}
    ))

    assert out["found"] is True
    assert len(out["results"]) == 1
    # The floor the pool is on, not a literal title: "ชั้น 3 — ผัง Sky Pool"
    # was the old deck's; the 2026-10-06 one calls the same plan "ผังชั้น 3
    # โซนส่วนกลาง". What matters is that the location answer is the floor-3
    # plan and nothing about the other pools rides along.
    assert "ชั้น 3" in out["results"][0]["title"]
    spoken_surface = str(out["results"])
    assert "Aqua Cinema" not in spoken_surface
    assert "ลากูน" not in spoken_surface
    assert "ตอบเฉพาะข้อมูลที่ตรงคำถาม" in out["instruction"]
    # ...and a bare "pool" is the main pool, the Water Lagoon (owner 12 Oct)
    out = run(registry.dispatch("search_condo_info", {"query": "สระว่ายน้ำอยู่ตรงไหน"}))
    assert "ลากูน" in out["results"][0]["title"]


def test_unapproved_marketing_copy_keeps_status_but_not_the_copy(loaded):
    from app.tools.knowledge import _entry
    slide = dict(loaded.load_slides()[0], script_th="บทที่ยังไม่อนุมัติ",
                 script_approved=False)
    top = _entry(slide)
    assert top["script_is_draft"] is True
    assert top["content_state"] == "preliminary_concept"
    assert "draft_script" not in top
    assert "approved_script" not in top
    assert "บทที่ยังไม่อนุมัติ" not in str(top)


def test_common_area_dimensions_never_reach_customer_facing_knowledge(loaded, tmp_path):
    import json

    from app.prompts import load_facts
    from app.tools.knowledge import _entry

    # The facts half runs on a file the test writes. It used to read the live
    # file and assert "16 โครงการ" was absent: true while that file was
    # draft, red the day the owner approved it (2026-09-28) with nothing
    # wrong. Signed: the lagoon keeps its name and loses its size. Draft:
    # none of it arrives.
    signed = {"source_id": "project_facts", "project_id": "embassy_world",
              "approval_status": "approved", "approved_by": "test_reviewer",
              "approved_at": "2026-01-01T00:00:00+07:00",
              "effective_at": "2026-01-01T00:00:00+07:00",
              "disclosure_scope": "customer", "content_state": "existing",
              "facts": [{"label": "จุดเด่น", "value": "ลากูน 155ม., Water Lagoon 155 เมตร"},
                        {"label": "ผู้พัฒนา", "value": "บริษัททดสอบ 16 โครงการ"}]}
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(signed, ensure_ascii=False), encoding="utf-8")
    facts = load_facts(str(path))
    assert "ลากูน" in facts and "155" not in facts
    path.write_text(json.dumps({**signed, "approval_status": "draft"}, ensure_ascii=False),
                    encoding="utf-8")
    facts = load_facts(str(path))
    assert "ลากูน" not in facts and "16 โครงการ" not in facts, \
        "unapproved figures must not reach the model"

    entry = _entry({
        "source_id": "slide_catalog",
        "project_id": "embassy_world",
        "title_th": "สระลากูน",
        "summary_th": "สระลากูนยาว 155 เมตร อยู่หน้าอาคาร",
        "detail_th": "พื้นที่ส่วนกลาง 4,000 ตร.ม. พร้อมสวน",
        "script_th": "ลากูน 155ม. พร้อมพื้นที่พักผ่อน",
    })
    surface = str(entry)
    assert "155" not in surface and "4,000" not in surface
    assert not entry["policy_trace"]["allowed"]
    assert "script_is_draft" not in entry and "draft_script" not in entry


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
    target["script_approved"] = True
    target["script_approved_by"] = "test_reviewer"
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


def test_a_gallery_without_the_slides_group_answers_but_never_shows_a_slide(
        loaded, monkeypatch):
    """Owner, 8 Oct 2026: "เอาสไลด์ออกก่อน". The gallery's TOOL_GROUPS has no
    `slides`, yet this knowledge-group tool put slide images up as a side
    effect of answering — hidden only while the asset manifest was stale.
    The answer must survive; the screen must not move."""
    from app.config import settings
    from app.tools import slides as sl

    run(registry.dispatch("show_slide", {"query": "social club"}))
    before = sl.current_slide()["id"]
    monkeypatch.setattr(settings, "tool_groups",
                        "knowledge,smarthome,mydocs,units,calc,robot")

    out = run(registry.dispatch("search_condo_info", {"query": "มีซาวน่าไหม"}))

    assert out["found"] is True and out["results"], "the answer itself must survive"
    assert "now_showing" not in out
    assert sl.current_slide()["id"] == before


def test_answer_images_show_the_picture_without_the_slides_group(loaded, monkeypatch):
    """Owner, 2026-10-11: "ให้บริการ / ให้ชม อาจจะเป็นรูปภาพให้เขาดู" ->
    "ทุกเรื่องที่มีภาพตรง". ANSWER_IMAGES brings the matching picture back on
    a machine whose TOOL_GROUPS has no `slides` (the robot) — the picture,
    not the presentation: no slide tool becomes visible to the model."""
    from app.config import settings
    from app.tools import registry as reg

    monkeypatch.setattr(settings, "tool_groups", "knowledge,mydocs,units,calc,robot")
    monkeypatch.setattr(settings, "answer_images", True)

    out = run(registry.dispatch("search_condo_info", {"query": "มีซาวน่าไหม"}))

    assert out["found"] is True
    assert out["now_showing"] and out["now_showing"].get("url")
    assert out["note"].startswith("แสดงภาพที่ตรงกับคำตอบบนจอแล้ว")
    visible = {t.name for t in reg.all_tools() if reg.allowed(t)}
    assert not visible & {"show_slide", "next_slide", "start_presentation"}


def test_answer_images_say_so_when_no_picture_went_up(loaded, monkeypatch):
    """2026-10-11: "มีรูปไหม" searched "facilities", the match was too broad
    to change the screen, and Emma said "มีภาพ...ให้ชมบนจอค่ะ" anyway. With
    ANSWER_IMAGES the result now says no picture went up; without it the
    sales room's result is unchanged."""
    from app.config import settings
    from app.tools import knowledge

    monkeypatch.setattr(settings, "tool_groups", "knowledge,mydocs,units,calc,robot")
    monkeypatch.setattr(knowledge, "confident_enough_to_show", lambda *a, **k: False)
    monkeypatch.setattr(settings, "answer_images", True)
    out = run(registry.dispatch("search_condo_info", {"query": "มีซาวน่าไหม"}))
    assert "now_showing" not in out
    assert out["note"].startswith("ไม่ได้ขึ้นภาพบนจอ ห้ามพูดว่ามีภาพให้ชม")
    monkeypatch.setattr(settings, "answer_images", False)
    out = run(registry.dispatch("search_condo_info", {"query": "มีซาวน่าไหม"}))
    assert "ไม่ได้ขึ้นภาพบนจอ" not in out["note"]


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


def test_an_explicit_overview_still_brings_the_top_hits(loaded, monkeypatch):
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

    out = run(registry.dispatch(
        "search_condo_info", {"query": "ภาพรวมสิ่งอำนวยความสะดวกมีอะไรบ้าง"}
    ))
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


def test_a_yield_question_is_a_money_question():
    """Found by measuring, 2026-09-12: running the guard the way
    `search_my_documents` does showed "ผลตอบแทนการลงทุนกี่เปอร์เซ็นต์" walking
    straight past it and answering found=True from the marketing web dump —
    the library the guard's own comment names as full of "yields 7-10%". The
    list had ราคา/งบ/ดอกเบี้ย but no word for *yield* or *return*, which is the
    money question that copy answers with its most confident unsigned numbers.
    """
    from app.tools.knowledge import _is_commercial

    for query in ("ผลตอบแทนการลงทุนกี่เปอร์เซ็นต์",
                  "ซื้อปล่อยเช่าได้ผลตอบแทนเท่าไหร่",
                  "ค่าเช่าเดือนละเท่าไหร่", "rental yield ที่นี่เท่าไหร่",
                  "what is the ROI", "expected return on investment"):
        assert _is_commercial(query), query


def test_investing_as_a_reason_to_live_here_is_not_a_price_question():
    """The cost of the yield words is refusing the market-strategy doc's own
    story. "ทำไมพัทยาน่าลงทุน" asks why the place is worth choosing, not for a
    number — so ลงทุน is deliberately left out of the guard."""
    from app.tools.knowledge import _is_commercial

    for query in ("ทำไมพัทยาน่าลงทุน", "โครงการนี้น่าอยู่ไหม",
                  "ทำไมคนสนใจลงทุนที่นี่"):
        assert not _is_commercial(query), query


def test_an_ordinary_thai_question_is_not_mistaken_for_a_price_question():
    """The worst bug found in the search work, and the oldest one.

    `_is_commercial` matched its terms as substrings. Thai is written without
    spaces, so a word is constantly found inside an unrelated one:

        ยังไ*งบ*้าง   contains งบ    (budget)
        *ผ่อน*คลาย    contains ผ่อน  (instalment)

    "เป็นยังไงบ้าง" is about as common as Thai phrasing gets, and "ผ่อนคลาย"
    is what a guest says about a spa — which this project is full of. Both
    were being refused with "no pricing data, please ask the sales team".

    Same failure as ราคา matching inside อาคาร, which is the reason the whole
    retrieval layer was rewritten. It had reappeared one function away, where
    it costs more: a wrong slide is a picture, a wrong refusal is the robot
    declining to answer a question it can answer.
    """
    from app.tools.knowledge import _is_commercial

    for query in ("ที่ออกกำลังกายเป็นยังไงบ้าง", "ห้องเป็นยังไงบ้าง",
                  "บรรยากาศเป็นยังไงบ้าง", "ซาวน่าช่วยผ่อนคลายไหม",
                  "มีที่นั่งผ่อนคลายไหม"):
        assert not _is_commercial(query), query


def test_the_price_questions_still_get_caught_after_that_change():
    """Tightening the match must not open the gate it was guarding."""
    from app.tools.knowledge import _is_commercial

    for query in ("ราคาเริ่มต้นเท่าไหร่", "ค่าส่วนกลางเท่าไหร่",
                  "ผ่อนดาวน์ยังไง", "งบประมาณเท่าไหร่", "มีส่วนลดไหม",
                  "ห้องขายเปิดกี่โมง", "ต่างชาติซื้อได้ไหม", "โอนได้เมื่อไหร่",
                  "how much is a unit", "价格是多少", "いくらですか",
                  "얼마예요", "цена квартиры"):
        assert _is_commercial(query), query


def test_a_refused_money_question_points_back_at_the_approved_facts(loaded):
    """The slides are never a price source, but the approved facts block is.
    The old instruction ("บอกลูกค้าตรงๆ ว่ายังไม่มีข้อมูลส่วนนี้") rode on the
    tool result and beat the facts every time: live eval 2026-10-07 measured
    0 of 30 price questions answered while condo_facts carried approved
    starting prices. It must still forbid the captions and name sales."""
    out = run(registry.dispatch("search_condo_info", {"query": "ราคาเริ่มต้นเท่าไหร่"}))
    assert out["found"] is False and out["results"] == []
    assert "ข้อมูลโครงการ' ใน system prompt" in out["instruction"]
    assert "ห้ามเดาหรืออ้างอิงจากคำบรรยายภาพ" in out["instruction"]
    assert "ส่งต่อทีมขาย" in out["instruction"]


def test_nothing_in_the_slides_is_not_nothing_known(loaded):
    """"ชั้น 3 มีอะไรบ้าง" and "โครงการอยู่ตรงไหน" were answered "no data"
    in the live eval while the facts block listed both."""
    out = run(registry.dispatch("search_condo_info", {"query": "zzzz ไม่มีอยู่จริง qqqq"}))
    assert out["found"] is False
    assert "ข้อมูลโครงการ' ใน system prompt" in out["instruction"]
    assert "ห้ามเดา" in out["instruction"]


def test_answering_from_the_facts_never_licenses_extending_them(loaded):
    """Pointing the model at the facts block made it answer more — and, in
    the 2026-10-08 re-test, extend: "Smart Security" (in-unit, concept)
    became "ครอบคลุมทั้งโครงการ", the ban on guaranteeing returns became
    "ไม่มีนโยบายการันตีค่าเช่า". Both instructions now say: only what is
    written, no derived policy or condition."""
    money = run(registry.dispatch("search_condo_info", {"query": "มีการันตีค่าเช่าไหม"}))
    assert "ห้ามสรุปนโยบายหรือเงื่อนไขที่ไม่ได้เขียน" in money["instruction"]
    nothing = run(registry.dispatch("search_condo_info", {"query": "zzzz ไม่มีอยู่จริง qqqq"}))
    assert "ให้ตอบเฉพาะที่เขียนไว้ตรงๆ ห้ามขยายความ" in nothing["instruction"]



def test_every_search_result_says_only_what_it_found(loaded):
    """Owner 12 Oct: "พยายามให้ตอบตามข้อมูลจริง อย่าให้มันคิดเอง". The robot logs
    showed descriptions added to true facts ("มีอุปกรณ์ครบครัน" for the gym,
    "วิวทะเล" for a room); the rule rides on every result, quoting them."""
    from app.tools import knowledge

    out = run(registry.dispatch("search_condo_info", {"query": "ฟิตเนสอยู่ไหน"}))
    assert out["found"] is True
    assert knowledge.GROUNDED_ONLY in out["instruction"]
    assert "'มีอุปกรณ์ครบครัน'" in knowledge.GROUNDED_ONLY and "'วิวทะเล'" in knowledge.GROUNDED_ONLY
