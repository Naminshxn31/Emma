"""
Tests for slide presentation: search, ordered tours, and the display feed.

Uses the real slide index shipped in data/slides so the tests fail if the
deck is missing or its shape changes — the tools are useless without it.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.tools import registry


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def slides():
    import app.tools as tools_pkg
    from app.tools import slides as sl

    tools_pkg.load_tools()
    sl.reload_slides()
    sl.reset_state()
    return sl


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


# ==================== the deck itself ====================


def test_slide_index_is_present_and_shaped(slides):
    library = slides.load_slides()
    assert len(library) >= 50, "slide index missing or truncated"
    required = {"id", "file", "type", "title_th", "title_en", "keywords_th"}
    assert required <= set(library[0])


def test_slide_presentation_does_not_claim_a_physical_tour(slides):
    description = registry.get("start_presentation").description

    assert "ดูภาพรวมบนจอ" in description
    assert "พาชม" not in description


def test_every_slide_file_exists(slides):
    """A broken path shows an empty screen next to a talking robot."""
    from pathlib import Path

    base = Path(settings.slides_dir)
    missing = [s["file"] for s in slides.load_slides()
               if not (base / s["file"]).is_file()]
    assert not missing, f"{len(missing)} slide files missing: {missing[:3]}"


def test_slide_ids_are_unique(slides):
    ids = [s["id"] for s in slides.load_slides()]
    assert len(ids) == len(set(ids))


def test_default_catalog_contains_only_its_project(slides):
    library = slides.load_slides()
    assert len(library) == 135
    assert all(s["project_id"] == settings.project_id for s in library)
    assert not any(s["type"] == "other-project" for s in library)


def test_foreign_slide_in_catalog_fails_closed(slides, tmp_path, monkeypatch):
    source = Path(settings.slides_dir) / "index.json"
    catalog = json.loads(source.read_text(encoding="utf-8"))
    catalog["images"].append({
        "id": "foreign", "file": "foreign.jpg", "source_id": "slide_catalog",
        "project_id": "embassy_life",
    })
    (tmp_path / "index.json").write_text(
        json.dumps(catalog, ensure_ascii=False), encoding="utf-8",
    )
    monkeypatch.setattr(settings, "slides_dir", str(tmp_path))
    slides.reload_slides()
    assert slides.load_slides() == []


# ==================== search ====================


def test_finds_a_slide_in_thai(slides):
    out = run(registry.dispatch("show_slide", {"query": "ผังห้อง"}))
    assert out["ok"] is True
    assert out["slide"]["url"].startswith("/slides/")


@pytest.mark.parametrize("query,expected_type", [
    ("ทำเลที่ตั้ง", "location-map"),
    ("ผังห้อง", "floor-plan"),
    ("ฟิตเนส", "facility"),
    ("ห้องนอน", "unit-plan"),
])
def test_thai_queries_reach_the_right_kind_of_slide(slides, query, expected_type):
    """Thai has no spaces, so whole-string containment failed on near
    misses: "ทำเลที่ตั้ง" matched nothing despite a "แผนที่ทำเล" slide.
    Character n-grams fixed that; this pins the behaviour down.

    Asserts the *subject*, not the slide type. It used to demand
    `type == "location-map"`, and went red once the Canva deck's own pages
    were titled properly — because "ทำเลที่ตั้ง" started returning
    "ทำเลที่ตั้งโครงการ — หาดจอมเทียน" from the official deck instead of
    emma's generic map. That is the better answer, so the expectation was
    what needed changing.
    """
    out = run(registry.dispatch("show_slide", {"query": query}))
    assert out["ok"] is True, f"{query} found nothing"
    if out["slide"]["type"] == expected_type:
        return
    title = (out["slide"].get("title_th") or "") + " " + (out["slide"].get("title_en") or "")
    subject = {
        "location-map": ("ทำเล", "แผนที่", "location", "map"),
        "facility": ("ฟิตเนส", "biogenesis", "gym", "fitness"),
        "floor-plan": ("ผัง", "แปลน", "plan", "floor"),
        "unit-plan": ("ห้อง", "unit", "room", "bedroom"),
    }[expected_type]
    assert any(w.lower() in title.lower() for w in subject), (
        "%r returned %r, which is neither type %s nor about that subject"
        % (query, title.strip(), expected_type)
    )


def test_generated_narration_is_not_labelled_approved(slides):
    """`script_is_approved_copy` tells the assistant a human signed off on
    these exact words and it should deliver them as written. It was set for
    any slide that had a script at all — including the 59 drafted from the
    slide images — so the robot was reciting unreviewed sentences about a
    multi-million-baht property while the system vouched for them.

    Drafts are still spoken, which keeps the narration on the deck's own
    material. They are simply not passed off as approved.
    """
    from app.tools.slides import _public, load_slides

    drafts = [s for s in load_slides()
              if (s.get("script_th") or s.get("script_en")) and not s.get("script_approved")]
    assert drafts, "fixture should contain unapproved narration"

    shown = _public(drafts[0])
    assert shown.get("script"), "a draft is still worth speaking"
    assert shown.get("script_is_draft") is True
    assert "script_is_approved_copy" not in shown


def test_approval_is_recorded_with_a_name(slides):
    """An approval nobody's name is attached to isn't one."""
    from app.tools.slides import _public

    approved = _public({
        "id": "x", "file": "x.jpg", "script_th": "...",
        "project_id": settings.project_id,
        "script_approved": True, "script_approved_by": "ฝ่ายขาย Empire Group",
    })
    assert approved["script_is_approved_copy"] is True
    assert approved["script_approved_by"] == "ฝ่ายขาย Empire Group"
    assert "script_is_draft" not in approved


def test_thai_words_that_merely_share_letters_do_not_match(slides):
    """The bug that put a building on screen for a price question.

    Thai has no word boundaries, so the matcher works on character n-grams —
    and **ราคา** (price) sits inside **อาคาร** (building) at trigram level:
    both contain "าคา". With trigrams alone that read as a 50% match. Longer
    n-grams tell them apart, and rarity weighting stops the accidental
    overlap outvoting the half of the query that matched nothing.
    """
    from app.tools.retrieval import tokenize
    from app.tools.slides import search_slides

    # The fix is upstream of scoring: a tokeniser splits on words, so the two
    # never meet. Comparing letters, they overlapped at "าคา".
    assert not set(tokenize("ราคาโครงการ")) & set(tokenize("ภาพอาคารและสระลากูน")), (
        "ราคา and อาคาร still share a token: %r vs %r"
        % (tokenize("ราคาโครงการ"), tokenize("ภาพอาคารและสระลากูน"))
    )
    assert search_slides("ราคา")[0].found is False


def test_common_words_stop_matching_everything(slides):
    """โครงการ appears in a large share of a condo deck. Counting it like
    any other word made it the deciding signal in every query that contained
    it, which is how "ราคาโครงการ" ranked slides about the developer."""
    from app.tools import slide_search
    from app.tools.slides import load_slides

    index = slide_search.get_index(load_slides())
    assert index.bm25 is not None
    common = index.bm25.idf.get("โครงการ", 0.0)
    rare = index.bm25.idf.get("ซาวน่า", 0.0)
    assert rare > 0, "the deck should contain ซาวน่า"
    assert common < rare * 0.75, (
        "a word in a third of the deck (โครงการ, idf %.2f) must count for "
        "clearly less than a specific one (ซาวน่า, idf %.2f)" % (common, rare)
    )


def test_a_query_the_deck_never_mentions_scores_near_zero(slides):
    """An absent gram is maximally rare, not averagely rare. Treating it as
    average let the matched half of "ราคาโครงการ" carry the whole decision."""
    from app.tools.slides import search_slides

    for query in ("โปรโมชั่น", "การเมือง", "zzzz ไม่มีอยู่จริง qqqq"):
        assert search_slides(query)[0].found is False, query


def test_other_projects_do_not_outrank_this_ones_slides(slides):
    """The library includes competitor decks for comparison; asking for a
    facility was surfacing another development's version of it."""
    out = run(registry.dispatch("show_slide", {"query": "สระว่ายน้ำ"}))
    assert out["ok"] is True
    assert out["slide"]["type"] != "other-project"


def test_off_topic_queries_are_rejected(slides):
    """Scoring is fuzzy by design, so the floor matters — otherwise an
    unrelated question puts an arbitrary slide on a large screen.

    Note the limit of this layer: character n-grams cannot separate
    "การเมือง" (politics) from "ในเมือง" (in the city), so genuinely
    ambiguous overlaps still score highly. Topic scope is enforced by the
    system prompt, which forbids answering off-topic questions at all; this
    threshold only stops obvious noise.
    """
    for query in ("ราคาหุ้นวันนี้", "zzzz ไม่มีอยู่จริง qqqq"):
        out = run(registry.dispatch("show_slide", {"query": query}))
        assert out["ok"] is False, f"{query} should not match anything"


def test_finds_the_same_kind_of_slide_in_english(slides):
    """Guests switch language mid-conversation; search has to follow."""
    out = run(registry.dispatch("show_slide", {"query": "floor plan"}))
    assert out["ok"] is True


def test_unmatched_query_shows_nothing_rather_than_something_wrong(slides):
    """A wrong floor plan on screen is worse than a blank one."""
    out = run(registry.dispatch("show_slide", {"query": "zzzz ไม่มีอยู่จริง qqqq"}))
    assert out["ok"] is False
    assert slides.current_slide() is None


def test_showing_a_slide_sets_the_current_slide(slides):
    run(registry.dispatch("show_slide", {"query": "สระว่ายน้ำ"}))
    current = slides.current_slide()
    assert current and current["url"].startswith("/slides/")


# ==================== ordered tours ====================


def test_presentation_walks_through_slides_in_order(slides):
    start = run(registry.dispatch("start_presentation", {"tour": "facilities"}))
    assert start["ok"] is True and start["total"] > 1
    assert start["slide"]["position"] == 1

    second = run(registry.dispatch("next_slide", {}))
    assert second["finished"] is False
    assert second["slide"]["position"] == 2
    assert second["slide"]["id"] != start["slide"]["id"]


def test_presentation_reports_when_it_reaches_the_end(slides):
    run(registry.dispatch("start_presentation", {"tour": "location"}))
    result = {"finished": False}
    for _ in range(40):
        result = run(registry.dispatch("next_slide", {}))
        if result.get("finished"):
            break
    assert result["finished"] is True


def test_previous_slide_goes_back(slides):
    run(registry.dispatch("start_presentation", {"tour": "facilities"}))
    run(registry.dispatch("next_slide", {}))
    back = run(registry.dispatch("previous_slide", {}))
    assert back["ok"] is True and back["slide"]["position"] == 1


def test_cannot_go_back_past_the_first_slide(slides):
    run(registry.dispatch("start_presentation", {"tour": "facilities"}))
    assert run(registry.dispatch("previous_slide", {}))["ok"] is False


def test_next_slide_without_a_presentation_is_reported(slides):
    assert run(registry.dispatch("next_slide", {}))["ok"] is False


def test_unknown_tour_falls_back_to_the_default(slides):
    """A hallucinated tour name should still present something sensible."""
    out = run(registry.dispatch("start_presentation", {"tour": "not-a-tour"}))
    assert out["ok"] is True and out["tour"] == slides.DEFAULT_TOUR


def test_hide_slide_clears_the_screen(slides):
    run(registry.dispatch("show_slide", {"query": "สระว่ายน้ำ"}))
    assert slides.current_slide() is not None
    run(registry.dispatch("hide_slide", {}))
    assert slides.current_slide() is None


def test_the_no_preview_rule_leads_the_instruction(slides):
    """Every slide was still ending with a hand-off the script doesn't have
    — "...building better tomorrows ค่ะ เดี๋ยวไปดูภาพรวมโครงการกันต่อนะคะ".

    The rule forbidding it existed, buried after four other clauses, and the
    model read it last and ignored it. Same failure as the prompt rules that
    lost to the tool results: position decides whether a constraint lands.
    """
    from app.tools.slides import KEEP_GOING

    assert KEEP_GOING.index("ห้ามเติมประโยค") < 80, "the boundary must come first"
    # Quoting the padding it actually produces is what makes it matchable.
    assert "เดี๋ยวไปดู" in KEEP_GOING
    assert "สไลด์ถัดไป" in KEEP_GOING


def test_the_scripts_themselves_do_not_trail_off_into_the_next_slide(slides):
    """If the deck's own copy ended with hand-offs, no instruction could
    fix it — the model would be following the script correctly. Four slides
    do end that way, and they are section dividers whose whole content is
    "next we'll show you the basement". Anything beyond a handful means the
    narration, not the prompt, is what needs editing.
    """
    import re

    from app.tools.slides import load_slides

    lead = re.compile(r"(เดี๋ยว|ต่อไป|สไลด์ถัดไป|กันต่อ|มาดู)")
    trailing = [
        s["id"] for s in load_slides()
        if s.get("script_th") and lead.search(s["script_th"][-40:])
    ]
    assert len(trailing) <= 6, (
        "%d scripts end by announcing the next slide: %s" % (len(trailing), trailing)
    )


def test_asking_for_a_slide_by_name_still_gets_the_script(slides):
    """`show_slide` handed the model the approved copy and said nothing
    about it, so "ขอดูฟิตเนส" got improvised narration while the tour got
    the real thing — the same slide, two levels of trust, decided only by
    how the guest happened to ask."""
    out = run(registry.dispatch("show_slide", {"query": "ฟิตเนส"}))
    assert out["slide"].get("script"), "fixture slide should carry a script"
    assert "script" in out["instruction"]
    assert "ห้ามแต่งเนื้อหาใหม่" in out["instruction"]


def test_a_slide_with_no_script_is_not_told_to_read_one(slides):
    """The 85 inherited slides have no narration. Telling the model to
    follow a script that isn't there invites it to invent one."""
    from app.tools.slides import load_slides

    bare = next(s for s in load_slides()
                if not (s.get("script_th") or s.get("script_en")))
    out = run(registry.dispatch("show_slide", {"query": bare["title_th"]}))
    if out.get("ok") and not out["slide"].get("script"):
        assert "instruction" not in out


@pytest.mark.parametrize("page,expected_position", [(1, 1), (5, 5), (24, 24)])
def test_a_page_can_be_asked_for_by_number(slides, page, expected_position):
    """`show_slide` matches text, and a number is terrible text: "หน้า 24",
    "24" and "ไปหน้าที่ 24" each landed on a different slide and none was
    page 24. A page number is a coordinate, not a topic."""
    out = run(registry.dispatch("go_to_page", {"page": page}))
    assert out["ok"] is True
    assert out["slide"]["position"] == expected_position
    assert slides.STATE["index"] == page - 1, "the tour moves with it"


def test_going_to_a_page_hands_over_that_page_s_script(slides):
    out = run(registry.dispatch("go_to_page", {"page": 5}))
    assert out["slide"]["script"], "page 5 has narration"
    assert "script" in out["instruction"]


def test_a_page_that_does_not_exist_says_so(slides):
    out = run(registry.dispatch("go_to_page", {"page": 999}))
    assert out["ok"] is False
    # Not the literal "59": that was a fact about one export of one deck,
    # and it went stale the day the deck was re-imported. The number the
    # robot quotes has to come from the deck it is actually presenting.
    total = len(slides._build_deck("deck"))
    assert str(total) in out["instruction"], "tell the guest how many there are"


def test_starting_a_tour_also_says_to_keep_going(slides):
    """The tour died at slide one. `next_slide` carried the instruction to
    narrate-and-advance; `start_presentation` didn't, so the model read out
    the cover and stopped — nothing had told it there were 58 more pages and
    no one was going to ask it to continue."""
    out = run(registry.dispatch("start_presentation", {"tour": "deck"}))
    assert out["ok"] is True
    assert "next_slide" in out["instruction"]
    assert "ห้ามถามว่าจะไปสไลด์ถัดไปไหม" in out["instruction"]


def test_every_advance_repeats_the_dont_ask_instruction(slides):
    """The prompt already forbids asking "shall we go on?" and the model
    asked anyway, page after page. A rule near the top of a 2,900-character
    system prompt loses to whatever the model feels like doing sixty slides
    later; an instruction on the tool result lands at the decision itself."""
    run(registry.dispatch("start_presentation", {"tour": "deck"}))
    out = run(registry.dispatch("next_slide", {}))
    assert "ห้ามถามว่าจะไปสไลด์ถัดไปไหม" in out["instruction"]
    assert "next_slide" in out["instruction"]


def test_answering_a_question_says_the_tour_is_still_open(slides):
    """Returning from a detour it asked permission to carry on — it had no
    way to know a deck was still open behind the question.

    Modelled as a barge-in, because that is what asking mid-narration is:
    the guest talked over the slide, so it is still owed to them and
    next_slide resumes rather than advances.
    """
    run(registry.dispatch("start_presentation", {"tour": "deck"}))
    run(registry.dispatch("next_slide", {}))

    slides.pause_for_barge_in()
    out = run(registry.dispatch("show_slide", {"query": "ฟิตเนส"}))
    assert "next_slide" in out["presentation"]
    assert "ห้ามถามว่าจะดูต่อไหม" in out["presentation"]

    resumed = run(registry.dispatch("next_slide", {}))
    assert resumed["resumed"] is True
    assert "ห้ามถามว่าจะไปสไลด์ถัดไปไหม" in resumed["instruction"]


def test_no_tour_means_no_resume_hint(slides):
    """Showing a slide outside a presentation must not tell the model to
    call next_slide — there is nothing to advance."""
    out = run(registry.dispatch("show_slide", {"query": "ฟิตเนส"}))
    assert "presentation" not in out


def test_a_question_pauses_the_tour_instead_of_ending_it(slides):
    """A guest asking to see the gym halfway through a tour has not cancelled
    the tour. This used to throw the deck away, so "บรรยายต่อจากเมื่อกี้"
    came back as "no presentation in progress" — the assistant kept talking
    from memory while the picture stayed frozen on the gym.

    (The previous test here asserted the opposite, reasoning that next_slide
    might resume a tour the guest had moved on from. But next_slide is only
    ever called by the model, and only when it means to advance a
    presentation; it does not fire on its own.)

    The barge-in below is what makes this the *resume* case specifically —
    see `test_a_question_asked_after_the_narration_finished_does_not_replay_it`
    for the same detour without one, which carries on instead.
    """
    run(registry.dispatch("start_presentation", {"tour": "deck"}))
    run(registry.dispatch("next_slide", {}))
    run(registry.dispatch("next_slide", {}))
    at_slide = slides.current_slide()["id"]

    slides.pause_for_barge_in()
    run(registry.dispatch("show_slide", {"query": "ฟิตเนส"}))
    assert slides.current_slide()["id"] != at_slide, "the detour is on screen"
    assert slides.STATE["detour"] is True
    assert slides.STATE["deck"], "the tour survives the question"

    resumed = run(registry.dispatch("next_slide", {}))
    assert resumed["ok"] is True
    assert resumed.get("resumed") is True
    assert resumed["slide"]["id"] == at_slide, "picks up where the tour was"

    onward = run(registry.dispatch("next_slide", {}))
    assert onward["slide"]["id"] != at_slide, "and carries on from there"


# ==================== the display window ====================


def test_display_page_is_served(client):
    resp = client.get("/display")
    assert resp.status_code == 200
    assert "ws/display" in resp.text


def test_slide_images_are_served(client, slides):
    first = slides.load_slides()[0]
    assert client.get(f"/slides/{first['file']}").status_code == 200


def test_display_receives_the_current_slide_on_connect(client, slides):
    """A screen switched on mid-presentation must not sit there blank."""
    run(registry.dispatch("show_slide", {"query": "สระว่ายน้ำ"}))
    expected = slides.current_slide()

    with client.websocket_connect("/ws/display") as ws:
        evt = ws.receive_json()
    assert evt["type"] == "slide"
    assert evt["slide"]["id"] == expected["id"]


def test_display_is_updated_when_a_slide_changes(client, slides):
    with client.websocket_connect("/ws/display") as ws:
        ws.receive_json()  # initial state
        run(registry.dispatch("show_slide", {"query": "ผังห้อง"}))
        evt = ws.receive_json()
    assert evt["type"] == "slide"
    assert evt["slide"] is not None


def test_display_is_told_when_the_screen_is_cleared(client, slides):
    run(registry.dispatch("show_slide", {"query": "สระว่ายน้ำ"}))
    with client.websocket_connect("/ws/display") as ws:
        ws.receive_json()
        run(registry.dispatch("hide_slide", {}))
        evt = ws.receive_json()
    assert evt["slide"] is None


def test_non_slide_tools_do_not_touch_the_display(client, slides, monkeypatch):
    """Turning on a light shouldn't blank the screen."""
    from app import display

    sent = []

    async def spy(payload):
        sent.append(payload)

    monkeypatch.setattr(display, "show", lambda slide: spy({"slide": slide}))
    run(registry.dispatch("get_room_status", {}))
    assert sent == []


# ==================== provider wiring ====================


def test_slide_tools_are_declared_to_the_model(slides):
    from app.providers.openai_realtime import build_session_config

    names = [t["name"] for t in build_session_config("marin", "x")["session"]["tools"]]
    for expected in ("show_slide", "start_presentation", "next_slide"):
        assert expected in names


def test_prompt_forbids_narrating_a_slide_that_is_not_shown():
    from app.prompts import build_instructions

    text = build_instructions("X")
    # Matched loosely: the wording gets trimmed whenever the prompt budget
    # tightens, but the rule itself must survive.
    assert "ห้ามบรรยายภาพที่ไม่ได้แสดง" in text
    assert "next_slide" in text  # it must know to advance itself


def test_official_deck_is_the_default_tour(slides):
    """A sales person walks the authored deck, not an assembled selection."""
    out = run(registry.dispatch("start_presentation", {}))
    assert out["ok"] is True
    assert out["tour"] == "deck"
    assert out["total"] >= 50


def test_deck_is_presented_in_authored_order(slides):
    out = run(registry.dispatch("start_presentation", {"tour": "deck"}))
    first = out["slide"]["id"]
    second = run(registry.dispatch("next_slide", {}))["slide"]["id"]
    assert first < second, "deck slides must advance in page order"


def test_falls_back_to_overview_without_an_imported_deck(slides, monkeypatch):
    """Before anyone imports a PDF the assistant should still be able to
    present, rather than refusing."""
    library = [s for s in slides.load_slides() if s.get("type") != "deck"]
    monkeypatch.setattr(slides, "_slides", library)
    out = run(registry.dispatch("start_presentation", {"tour": "deck"}))
    assert out["ok"] is True and out["tour"] == "overview"


# ==================== narration scripts ====================


def test_script_is_passed_to_the_model_when_present(slides, monkeypatch):
    """Approved sales copy has to reach the model, or it improvises."""
    library = slides.load_slides()
    target = dict(library[0])
    target["script_th"] = "สวัสดีค่ะ ยินดีต้อนรับ"
    target["script_approved"] = True
    target["script_approved_by"] = "ฝ่ายขาย"
    monkeypatch.setattr(slides, "_slides", [target] + library[1:])

    out = run(registry.dispatch("show_slide", {"query": target["title_th"]}))
    assert out["ok"] is True
    assert out["slide"]["script"] == "สวัสดีค่ะ ยินดีต้อนรับ"
    assert out["slide"]["script_is_approved_copy"] is True


def test_slides_without_a_script_omit_the_key(slides, monkeypatch):
    """An empty string would invite the model to 'read' nothing aloud;
    absence is unambiguous."""
    library = slides.load_slides()
    plain = {k: v for k, v in library[0].items()
             if k not in ("script_th", "script_en")}
    monkeypatch.setattr(slides, "_slides", [plain] + library[1:])

    out = run(registry.dispatch("show_slide", {"query": plain["title_th"]}))
    assert "script" not in out["slide"]


def test_prompt_tells_the_model_to_follow_the_script_without_embellishing():
    from app.prompts import build_instructions

    text = build_instructions("X")
    assert "script" in text
    assert "ห้ามเพิ่มตัวเลข" in text  # no inventing prices on top of approved copy


@pytest.mark.parametrize("heading", [
    "หน้า 7", "สไลด์ 7", "## 7", "7.", "Slide 7:", "[7]", "page 7",
])
def test_narration_parser_accepts_common_heading_styles(heading):
    """People paste from Word, Docs, Notion — don't make them reformat."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "add_narration", "scripts/add_narration.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    parsed = module.parse(f"{heading}\nเนื้อหาสคริปต์")
    assert parsed == {7: "เนื้อหาสคริปต์"}, f"{heading!r} not recognised"


def test_conversation_window_also_receives_the_slide(slides):
    """Slides used to go only to /display. With no second window open, a
    presentation reported success while the screen stayed empty — the
    failure was completely invisible."""
    js = _client_js_for_slides()
    assert "function showStage" in js
    assert "'slide' in evt" in js


def test_conversation_window_warns_when_no_display_is_connected(slides):
    js = _client_js_for_slides()
    assert "no fullscreen display connected" in js


def _client_js_for_slides() -> str:
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    return (root / "client" / "index.html").read_text(encoding="utf-8")


# ==================== keeping a tour moving ====================


def test_should_continue_tour_tracks_the_deck_position(slides):
    """The pure decision behind the server-side nudge: is a slide still owed?
    True while slides remain or a detour needs resuming, False once the last
    slide has been narrated with nothing paused — where the tour must end and
    the nudging stop."""
    S = slides.STATE

    S.update({"deck": [], "index": -1, "detour": False})
    assert slides.should_continue_tour() is False, "no tour, nothing to continue"

    S.update({"deck": ["a", "b", "c"], "index": 0, "detour": False})
    assert slides.should_continue_tour() is True, "two slides still ahead"

    S.update({"index": 2, "detour": False})
    assert slides.should_continue_tour() is False, "last slide narrated — done"

    S.update({"detour": True})
    assert slides.should_continue_tour() is True, "a question left a slide to resume"


def test_a_stalled_tour_is_nudged_then_left_alone(slides):
    """When the model narrates a slide and stops, the server pushes next_slide
    in as a turn — but only so many times at one slide. A model that ignores
    two nudges won't obey a third; better silence than pestering a stuck tour.
    Real forward progress resets the budget."""
    from app.session import VoiceSession

    import asyncio

    from app import display

    sess = _lone_session()

    class FakeProvider:
        def __init__(self):
            self.sent = []

        async def send_text(self, text):
            self.sent.append(text)
            # A model that ignores the nudge still *finishes the turn* — it
            # answers (or calls next_slide silently) and turn_complete
            # arrives. Since 276abc4 `announce` waits on that before the
            # next injection; a fake that never completes leaves
            # `turn_idle` cleared, the second nudge parks for TURN_WAIT_S,
            # and the third trips "bound to a different event loop"
            # because each asyncio.run() below is a new loop.
            sess.turn_idle.set()

    sess._spoke_this_turn = True     # it narrated the slide, then stopped
    sess.provider = FakeProvider()
    display.set_audio_lead(0)        # nothing queued: the nudge fires promptly

    slides.STATE.update({"deck": ["a", "b", "c"], "index": 0, "detour": False})

    async def nudge_and_settle():
        await sess._nudge_tour_if_stalled()
        if sess._nudge_task is not None:
            await sess._nudge_task

    # Stuck at slide 1: nudge, nudge, then give up (MAX_TOUR_NUDGES == 2).
    for _ in range(3):
        asyncio.run(nudge_and_settle())
    assert sess.provider.sent == [slides.CONTINUE_NUDGE] * 2, sess.provider.sent

    # The tour actually advanced — the budget resets and it will nudge again.
    slides.STATE.update({"index": 1})
    asyncio.run(nudge_and_settle())
    assert len(sess.provider.sent) == 3

    # Reaching the final slide ends the tour: no more nudging.
    slides.STATE.update({"index": 2, "detour": False})
    asyncio.run(nudge_and_settle())
    assert len(sess.provider.sent) == 3, "must not nudge once the deck is done"


def test_a_provider_without_send_text_never_breaks_the_nudge(slides):
    """send_text is a no-op on the base provider. A tour running on one that
    doesn't support it must simply not advance by this route, not error."""
    from app.providers.base import VoiceProvider
    from app.session import VoiceSession

    class BareProvider(VoiceProvider):
        """Implements only the required surface, so it inherits the base
        no-op send_text — the case of a provider that can't be nudged."""
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return None
        async def send_audio(self, pcm16): return None
        def events(self): return iter(())

    import asyncio

    from app import display

    sess = _lone_session()
    sess._spoke_this_turn = True
    sess.provider = BareProvider.__new__(BareProvider)  # base send_text = no-op
    display.set_audio_lead(0)

    slides.STATE.update({"deck": ["a", "b"], "index": 0, "detour": False})

    async def body():
        await sess._nudge_tour_if_stalled()
        if sess._nudge_task is not None:
            await sess._nudge_task       # must not raise

    asyncio.run(body())


# ==================== no lead-ins between slides ====================


def test_the_tour_does_not_preview_or_repeat_the_next_slide(slides):
    """First the model guessed the next topic (and got it wrong); giving it the
    real upcoming title fixed the accuracy but made it announce the next slide
    and then repeat that title on arrival — circular and unnatural. The
    resolution is no preview at all: results carry no upcoming-title field, and
    the instruction forbids teasing the next slide or opening with its heading.
    """
    out = run(registry.dispatch("start_presentation", {"tour": "deck"}))
    assert "next_up" not in out, "no upcoming-title field should be exposed"

    nxt = run(registry.dispatch("next_slide", {}))
    assert "next_up" not in nxt
    # Asserts the constraint, not one phrasing of it — the wording moved to
    # the front of the instruction (and started quoting the exact hand-off
    # the model kept adding) because buried at the end it was being ignored.
    assert "ห้ามลงท้ายด้วยการบอกว่าจะไปดูอะไรต่อ" in out["instruction"]
    assert "ห้ามขึ้นต้นด้วยการประกาศชื่อหัวข้อสไลด์" in out["instruction"]


# ==================== speaking the deck's own script ====================


def test_each_deck_slide_hands_the_model_a_script_to_speak(slides):
    """"พูดตามลิ้ง": every deck page ships its own script, and the tour must
    surface it so the model delivers that instead of improvising."""
    out = run(registry.dispatch("start_presentation", {"tour": "deck"}))
    assert out["slide"].get("script"), "the cover should carry a script"

    nxt = run(registry.dispatch("next_slide", {}))
    assert nxt["slide"].get("script"), "each advanced slide should carry its script"


# ==================== a barge-in pauses, never skips ====================


def test_a_barge_in_resumes_the_same_slide_instead_of_skipping(slides):
    """The reported bug: talking over the narration skipped the slide. The
    position is remembered — a barge-in just has to be treated as a pause so
    the next advance re-narrates it rather than stepping past."""
    run(registry.dispatch("start_presentation", {"tour": "deck"}))
    run(registry.dispatch("next_slide", {}))
    here = slides.STATE["index"]

    assert slides.pause_for_barge_in() is True
    assert slides.STATE["detour"] is True

    out = run(registry.dispatch("next_slide", {}))
    assert out.get("resumed") is True, "should have resumed, not advanced"
    assert slides.STATE["index"] == here, "the deck must not have moved on"


def test_a_barge_in_outside_a_tour_is_a_no_op(slides):
    slides.STATE.update({"deck": [], "index": -1, "detour": False})
    assert slides.pause_for_barge_in() is False
    assert slides.STATE["detour"] is False


# ==================== a Thai script, a guest who isn't Thai ====================


def test_script_delivery_instructions_permit_translating_into_the_guests_language():
    """All 59 scripts are Thai (script_en is empty everywhere), and rule 2 of
    the prompt tells the assistant to answer in whatever language the guest
    speaks. So the two rules meet head-on the moment a Chinese or English
    guest asks for a tour.

    Every instruction that hands the model a script has to resolve that
    collision itself, because the model reads them at the moment it is about
    to speak. Without the clause it either reads Thai at someone who doesn't
    read Thai, or decides for itself that "speak only the script" outranks
    "answer in their language" — and which one it picks is not knowable.

    Regression: the permission existed, and was lost twice — once when
    KEEP_GOING was rewritten to stop the "เดี๋ยวไปดู...กันต่อนะคะ" padding,
    and once when prompt rule 13 was tightened.
    """
    from app.tools.slides import FOLLOW_PAGE, KEEP_GOING, SPEAK_SCRIPT

    for name, text in [
        ("KEEP_GOING", KEEP_GOING),
        ("SPEAK_SCRIPT", SPEAK_SCRIPT),
        ("FOLLOW_PAGE", FOLLOW_PAGE),
    ]:
        assert "แปล" in text, "%s never says the script may be translated" % name


def test_translating_a_script_does_not_licence_changing_it():
    """The risk of granting translation is that it doubles as a licence to
    paraphrase — "I was translating" covers a lot. Each instruction that
    allows translation must, in the same breath, require the content survive
    it. This matters more than usual here: the scripts carry project facts
    the assistant is otherwise forbidden from inventing."""
    from app.tools.slides import FOLLOW_PAGE, KEEP_GOING, SPEAK_SCRIPT

    for name, text in [
        ("KEEP_GOING", KEEP_GOING),
        ("SPEAK_SCRIPT", SPEAK_SCRIPT),
        ("FOLLOW_PAGE", FOLLOW_PAGE),
    ]:
        assert "ครบ" in text, "%s permits translation without requiring completeness" % name


def test_the_prompt_agrees_that_a_script_may_be_translated():
    """The tool-result instructions and the system prompt have to say the same
    thing. Rule 13 is the one the model carries into every turn."""
    from app.prompts import BASE_INSTRUCTIONS

    rule = [ln for ln in BASE_INSTRUCTIONS.splitlines() if ln.startswith("13.")]
    assert rule, "rule 13 (script fidelity) is missing"
    assert "แปล" in rule[0], "rule 13 does not permit translating the script"
    assert "ครบ" in rule[0], "rule 13 permits translation without requiring completeness"


def test_the_off_topic_ban_does_not_read_as_a_ban_on_translating_the_script():
    """The scope guardrail lists things the assistant must refuse to be used
    for, and "แปลภาษา" sat in it next to "เขียนโปรแกรม" — meaning "don't be a
    translation service". Sitting bare in a ban list, it also reads as "don't
    translate", which is now exactly what rule 13 asks for. The ban has to be
    phrased so it can only bite the service, not the narration."""
    from app.prompts import BASE_INSTRUCTIONS

    banned = [ln for ln in BASE_INSTRUCTIONS.splitlines() if "เขียนโปรแกรม" in ln]
    assert banned, "the off-topic ban list is missing"
    assert "แปลภาษา" not in banned[0], (
        "a bare 'แปลภาษา' in the ban list contradicts rule 13"
    )


# ============ the picture must not run ahead of the voice ============


def test_canva_is_not_moved_while_narration_is_still_unheard(slides, monkeypatch):
    """The reported bug, in one assertion.

    Two things drive the Canva window on every advance and they were on
    different clocks. `display.show()` holds its reveal back by however much
    speech is still sitting in the browser's queue; `_point_canva_at` moved
    the window the instant the tool ran. Whenever `next_slide` stopped
    waiting with audio left over — which is every slide whose script outlasts
    SLIDE_TOOL_BUDGET_S — Canva jumped to the next page while the guest was
    still hearing the previous one, and the web display, obeying the lead,
    stayed behind. Two windows, two different pages.

    Nothing is lost by skipping it: the deferred reveal drives Canva too, so
    the page still turns — at the moment the words for it begin.
    """
    from app import display

    moved: list[str] = []

    async def fake_arrive(slide_id, timeout=None):
        moved.append(slide_id)

    monkeypatch.setattr("app.tools.canva_display.arrive", fake_arrive)

    display.set_audio_lead(9000)          # nine seconds still to be heard
    run(slides._point_canva_at("ew-002"))
    assert moved == [], (
        "moved Canva to ew-002 with 9s of narration unheard — the guest is "
        "looking at the next page while hearing the last one"
    )

    display.set_audio_lead(0)             # caught up
    run(slides._point_canva_at("ew-002"))
    assert moved == ["ew-002"], "should move once the guest has caught up"


def test_a_caught_up_tour_still_waits_for_canva_before_narrating(slides, monkeypatch):
    """The guard must not cost us the thing it sits next to. When there *is*
    no audio queued — the cold first slide, where Chromium is still starting
    — `_point_canva_at` has to block until the window is actually on the
    page, or the model narrates over a loading screen."""
    from app import display

    arrived = []

    async def fake_arrive(slide_id, timeout=None):
        arrived.append((slide_id, timeout))

    monkeypatch.setattr("app.tools.canva_display.arrive", fake_arrive)
    display.set_audio_lead(0)
    run(slides._point_canva_at("ew-001"))
    assert arrived and arrived[0][0] == "ew-001"
    assert arrived[0][1] == settings.canva_arrival_timeout_s, (
        "the arrival wait must stay bounded"
    )


def test_a_silent_turn_is_never_nudged(slides):
    """The runaway, from the live log.

    CONTINUE_NUDGE ends "อย่าพูดอย่างอื่น". The model obeys it exactly: calls
    next_slide, says nothing, ends the turn. That silent turn looks identical
    to a stall, so the old code nudged again — and the deck walked itself from
    slide 19 to slide 45 without a word being spoken, roughly a second a page.

    MAX_TOUR_NUDGES was no defence: it counts repeats at the *same* index, and
    every nudge moved the index, so the counter reset each round and the cap
    was never reached. This test drives that exact loop and requires it to
    stop at the first turn, so the nudge can never again be the thing that
    keeps the nudge going.
    """
    from app.session import VoiceSession

    class FakeProvider:
        def __init__(self):
            self.sent = []

        async def send_text(self, text):
            self.sent.append(text)

    sess = VoiceSession.__new__(VoiceSession)
    sess._nudge_index = None
    sess._nudge_count = 0
    sess._spoke_this_turn = False        # advanced without narrating
    sess.provider = FakeProvider()

    slides.STATE.update({"deck": [str(i) for i in range(59)], "index": 18,
                         "detour": False})

    # Each round: nudge would fire -> deck advances -> turn ends silent again.
    for _ in range(26):
        run(sess._nudge_tour_if_stalled())
        slides.STATE["index"] += 1

    assert sess.provider.sent == [], (
        "nudged a model that never spoke — this is the loop that skipped 26 "
        "slides in silence"
    )


# ==================== asking the guest something ====================


def test_a_slide_with_a_question_tells_the_model_to_wait(slides):
    """A slide carrying `ask_th` must come back with the waiting orders, not
    the advancing ones. KEEP_GOING ends "call next_slide immediately, don't
    wait for an answer" — under that instruction a question isn't a question,
    it's something the robot says on its way past."""
    run(registry.dispatch("start_presentation", {"tour": "deck"}))
    for _ in range(20):
        out = run(registry.dispatch("next_slide", {}))
        if out.get("slide", {}).get("ask"):
            break
    else:
        raise AssertionError("no slide in the first 20 carries a question")

    assert out["instruction"] is slides.ASK_AND_WAIT
    assert "ห้ามเรียก next_slide" in out["instruction"]
    assert slides.STATE["awaiting"] == out["slide"]["id"]


def test_the_tour_stops_pushing_while_a_question_is_open(slides):
    """The nudge fires on turn_complete, and asking a question *is* completing
    a turn. Without this the robot asks "เคยมาเที่ยวจอมเทียนไหมคะ" and is
    pushed onto the next slide before the guest can answer — worse than never
    asking, because it looks like it doesn't care what they say."""
    slides.STATE.update({"deck": ["a", "b", "c"], "index": 0, "detour": False})
    assert slides.should_continue_tour() is True

    slides._arm_question({"id": "b", "ask_th": "เคยมาไหมคะ"})
    assert slides.should_continue_tour() is False, "pushed on over an open question"


def test_a_question_nobody_answers_does_not_strand_the_tour(slides, monkeypatch):
    """The other half. A gallery is full of people who won't talk to a robot,
    and waiting is only safe if it ends by itself: after the grace period the
    question is treated as declined and the tour carries on, which is what was
    asked for — pause a beat, then continue on your own."""
    slides.STATE.update({"deck": ["a", "b", "c"], "index": 0, "detour": False})
    slides._arm_question({"id": "b", "ask_th": "เคยมาไหมคะ"})
    assert slides.should_continue_tour() is False

    # Pretend the grace period has elapsed with nothing said.
    slides.STATE["asked_at"] -= settings.tour_ask_grace_s + 1
    assert slides.should_continue_tour() is True, "the tour never recovered"
    assert slides.STATE["awaiting"] is None


def test_a_guest_starting_to_answer_stops_the_clock(slides):
    """Cleared on speech_started, not on the transcript. A guest thinking out
    loud through "เอ่อ... เคยมาค่ะ ตอนเด็กๆ" takes several seconds, and the
    grace timer must not expire and start the next slide over the top of
    them mid-sentence."""
    slides.STATE.update({"deck": ["a", "b", "c"], "index": 0, "detour": False})
    slides._arm_question({"id": "b", "ask_th": "เคยมาไหมคะ"})

    assert slides.answer_received() is True
    assert slides.STATE["awaiting"] is None
    assert slides.answer_received() is False, "nothing to clear the second time"


def test_returning_from_a_detour_does_not_re_ask(slides):
    """The guest broke off to look at something else and came back. Asking
    them again the thing they walked away from reads as nagging — and a guest
    who keeps asking questions would be asked it on every single return."""
    slides.STATE.update({"deck": ["a", "b", "c"], "index": 1, "detour": True,
                         "unheard": True, "awaiting": "b"})
    out = run(registry.dispatch("next_slide", {}))
    assert out.get("resumed") is True
    assert slides.STATE["awaiting"] is None
    assert out["instruction"] is slides.KEEP_GOING


def test_the_seeded_questions_are_open_ended_and_on_topic(slides):
    """These get spoken to real customers, so they are worth pinning.

    Yes/no questions kill a conversation — "เคยมาเที่ยวจอมเทียนไหมคะ" ends at
    "ไม่เคย" unless something invites more. And a question must belong to the
    page it sits on: one that would work anywhere is one worth cutting.
    """
    asked = [s for s in slides.load_slides() if s.get("ask_th")]
    assert asked, "no slide asks the guest anything"

    for slide in asked:
        q = slide["ask_th"]
        assert q.strip().endswith(("คะ", "ครับ", "คะ ", "บ้าง")) or "บ้าง" in q, (
            "%s asks %r — no polite particle, it will sound abrupt spoken"
            % (slide["id"], q)
        )
        assert len(q) <= 80, (
            "%s asks %r — too long to hold in the ear" % (slide["id"], q)
        )


def test_a_question_asked_after_the_narration_finished_does_not_replay_it(slides):
    """Answering a question always put the tour back on the *same* slide and
    narrated it again from the top.

    That is right when the guest cut in mid-sentence — they never heard the
    end of it. It is wrong when they waited politely for the page to finish
    and then asked something: they heard it, and playing it at them a second
    time is the robot not having noticed they were listening.

    The two look identical from the deck's point of view — both leave a
    detour outstanding — so the difference has to be recorded at the moment
    it happens, which is the barge-in.
    """
    run(registry.dispatch("start_presentation", {"tour": "deck"}))
    for _ in range(4):
        run(registry.dispatch("next_slide", {}))
    here = slides.STATE["index"]

    # Guest waits for the narration to end, then asks to see something.
    slides.show_current(slides._by_id("ew-024"))
    assert slides.STATE["detour"] is True

    out = run(registry.dispatch("next_slide", {}))
    assert not out.get("resumed"), "replayed a slide the guest had already heard"
    assert slides.STATE["index"] == here + 1, "the tour did not move on"


def test_a_slide_cut_off_by_a_barge_in_is_still_resumed(slides):
    """The other side of the same coin, and the reason the distinction has to
    exist rather than always advancing: a guest who interrupts halfway through
    never heard the rest, and skipping it loses content silently."""
    run(registry.dispatch("start_presentation", {"tour": "deck"}))
    for _ in range(4):
        run(registry.dispatch("next_slide", {}))
    here = slides.STATE["index"]

    slides.pause_for_barge_in()          # talked over mid-narration
    slides.show_current(slides._by_id("ew-024"))

    out = run(registry.dispatch("next_slide", {}))
    assert out.get("resumed") is True, "skipped a slide nobody finished hearing"
    assert slides.STATE["index"] == here
    assert slides.STATE["unheard"] is False, "the flag must not stick"


# ============ the clock that rescues an unanswered question ============


def _lone_session():
    """A VoiceSession with just enough wired up to run one watcher task."""
    from app.session import VoiceSession

    class FakeProvider:
        def __init__(self):
            self.sent = []

        async def send_text(self, text):
            self.sent.append(text)

    # Build it the way the app does, then swap the provider. Setting the
    # fields by hand is what left two older tests behind when `_nudge_task`
    # was added — the same drift `reset_state()` was written to end.
    sess = VoiceSession(ws=None)
    sess.provider = FakeProvider()
    return _as_the_live_session(sess)


def _as_the_live_session(sess):
    """Register the session the way `handle_connection` does.

    `events.announce` looks the live session up instead of being handed one —
    that is what lets the robot, and later a camera, announce something
    without holding a reference to the conversation. The catch is what an
    unregistered session does to a test: the announcement is dropped, and
    every assertion of the form `sent == []` then passes without testing
    anything at all. That is most of the assertions on this path.

    `conftest.py::_fresh_async_state` clears `_active` again afterwards.
    """
    from app import session as session_module

    session_module._active = sess
    return sess


def _run_watcher(sess, slides, ticks=3):
    """Run `_resume_after_silence` for a few of its one-second ticks without
    actually waiting: the loop's only timing dependency is asyncio.sleep."""
    import asyncio

    async def body():
        slept = {"n": 0}
        real_sleep = asyncio.sleep

        async def fast(_seconds):
            slept["n"] += 1
            if slept["n"] > ticks:
                raise asyncio.CancelledError
            await real_sleep(0)

        import app.session as sess_mod

        original = sess_mod.asyncio.sleep
        sess_mod.asyncio.sleep = fast
        try:
            await sess._resume_after_silence()
        except asyncio.CancelledError:
            pass
        finally:
            sess_mod.asyncio.sleep = original

    asyncio.run(body())


def test_an_unanswered_question_restarts_the_tour_by_itself(slides):
    """The failure this exists to prevent has no error and no log line: the
    robot asks "เคยมาเที่ยวจอมเทียนไหมคะ", nobody answers, and it stands there
    forever. Every other path that restarts a tour hangs off turn_complete,
    and a waiting robot will never emit another one — no speech, no tool call,
    no turn. There is nothing left to trigger on, so this needs its own clock.
    """
    sess = _lone_session()
    slides.STATE.update({"deck": ["a", "b", "c"], "index": 0, "detour": False})
    slides._arm_question({"id": "b", "ask_th": "เคยมาไหมคะ"})

    # Still inside the grace period: the silence belongs to the guest.
    _run_watcher(sess, slides, ticks=2)
    assert sess.provider.sent == [], "cut the guest off while they were thinking"

    # Grace expired with nothing said.
    slides.STATE["asked_at"] -= settings.tour_ask_grace_s + 1
    _run_watcher(sess, slides, ticks=2)
    assert slides.CONTINUE_NUDGE in sess.provider.sent, "the tour never recovered"


def test_the_watcher_stays_quiet_when_no_question_is_open(slides):
    """It runs for the whole call, so the common case has to cost nothing and
    do nothing — a stray nudge here would advance a deck mid-narration."""
    sess = _lone_session()
    slides.STATE.update({"deck": ["a", "b", "c"], "index": 0, "detour": False,
                         "awaiting": None})

    _run_watcher(sess, slides, ticks=5)
    assert sess.provider.sent == []


def test_the_watcher_does_not_restart_a_finished_tour(slides):
    """A question on the *last* slide expires like any other. Nudging then
    would push past the end of the deck."""
    sess = _lone_session()
    slides.STATE.update({"deck": ["a", "b", "c"], "index": 2, "detour": False})
    slides._arm_question({"id": "c", "ask_th": "สนใจห้องแบบไหนคะ"})
    slides.STATE["asked_at"] -= settings.tour_ask_grace_s + 1

    _run_watcher(sess, slides, ticks=3)
    assert sess.provider.sent == [], "nudged a tour that had already finished"


def test_a_dead_provider_does_not_kill_the_watcher(slides):
    """It runs for the life of the call. If one send fails — socket already
    closing — it must not take the task down, or every later question in the
    session would hang with no clock behind it."""
    sess = _lone_session()

    async def explode(text):
        raise RuntimeError("socket closed")

    sess.provider.send_text = explode
    slides.STATE.update({"deck": ["a", "b", "c"], "index": 0, "detour": False})
    slides._arm_question({"id": "b", "ask_th": "เคยมาไหมคะ"})
    slides.STATE["asked_at"] -= settings.tour_ask_grace_s + 1

    _run_watcher(sess, slides, ticks=3)      # must not raise


def test_the_watcher_holds_off_even_if_should_continue_tour_stops_helping(slides, monkeypatch):
    """`should_continue_tour()` also checks for an open question, so the
    watcher's own check is redundant *today* — a revert test showed deleting
    it changes nothing. It stays because the redundancy is the point: this
    loop must not depend on a guard living inside a function that answers a
    different question ("does the tour still owe a slide?").

    Simulated here by making that function unconditionally true, which is what
    someone tidying it up would effectively do. The watcher must still keep
    quiet while a guest is mid-answer.
    """
    sess = _lone_session()
    slides.STATE.update({"deck": ["a", "b", "c"], "index": 0, "detour": False})
    slides._arm_question({"id": "b", "ask_th": "เคยมาไหมคะ"})

    monkeypatch.setattr(slides, "should_continue_tour", lambda: True)
    _run_watcher(sess, slides, ticks=3)

    assert sess.provider.sent == [], (
        "interrupted a guest who was still inside the grace period"
    )


# ==================== the guest can say stop ====================


def test_a_guest_can_actually_stop_the_presentation(slides):
    """From a live session, and the transcript is the whole bug report:

        ลูกค้า: หยุด พรีเซนต์
        ผู้ช่วย: ได้ค่ะ หยุดพรีเซนต์เรียบร้อยแล้วค่ะ
        ผู้ช่วย: โครงการนี้พัฒนาโดย Empire Group ผู้มีผลงาน...

    Saying so was all the model could do. There was no tool for it, the deck
    stayed in STATE, `should_continue_tour()` stayed true, and the server's
    own nudge pushed the next slide in. The guest asked twice and was
    overridden twice — which is worse than not understanding them at all.
    """
    run(registry.dispatch("start_presentation", {"tour": "deck"}))
    run(registry.dispatch("next_slide", {}))
    assert slides.should_continue_tour() is True

    out = run(registry.dispatch("stop_presentation", {}))
    assert out["stopped"] is True
    assert slides.STATE["deck"] == []
    assert slides.should_continue_tour() is False, "the nudge will restart it"
    assert "ห้ามเรียก next_slide" in out["instruction"]


def test_stopping_leaves_the_picture_where_it_was(slides):
    """Stopping the narration and blanking the screen are different requests.
    Somebody who says "พอแล้ว" while looking at the gym usually wants to carry
    on looking at the gym — `hide_slide` is the one that clears it."""
    run(registry.dispatch("start_presentation", {"tour": "deck"}))
    run(registry.dispatch("next_slide", {}))
    on_screen = slides.current_slide()["id"]

    run(registry.dispatch("stop_presentation", {}))
    assert slides.current_slide() is not None, "the screen went blank"
    assert slides.current_slide()["id"] == on_screen


def test_stopping_when_nothing_is_running_is_not_an_error(slides):
    """Guests repeat themselves when a robot doesn't react fast enough. The
    second "หยุด" must not produce an apology or an error."""
    slides.reset_state()
    out = run(registry.dispatch("stop_presentation", {}))
    assert out["ok"] is True
    assert out["stopped"] is False


# ==================== ...and then carry on ====================


def test_a_stopped_tour_can_be_picked_back_up(slides):
    """"ทำไมไม่ไปต่อ" — 2026-08-10, and the log is the report:

        09:13:52  next_slide            -> ew-002
        09:13:57  stop_presentation
        09:14:56  show_slide            -> ew-012
        09:15:03  start_presentation    -> ew-001

    Between the second and third line the guest asked "Can you speak
    Chinese?". The model heard a change of subject as a change of mind and
    stopped the tour, and `stop_presentation` called `reset_state()` — deck,
    index and tour all gone. There was then no way back at all: the only tool
    that produces a deck is `start_presentation`, and it starts at the cover,
    so a guest two slides in was offered slide one again.
    """
    run(registry.dispatch("start_presentation", {"tour": "deck"}))
    run(registry.dispatch("next_slide", {}))
    at = slides.current_slide()["position"]

    run(registry.dispatch("stop_presentation", {}))
    assert slides.STATE["deck"] == [], "stopping still has to stop"

    out = run(registry.dispatch("resume_presentation", {}))
    assert out["resumed"] is True
    assert out["position"] == at + 1, "resuming restarted somewhere else"
    assert slides.should_continue_tour() is True


def test_resuming_repeats_the_slide_the_guest_talked_over(slides):
    """Where a resume lands depends on whether the slide was heard.

    Cut off mid-narration, the guest never got this slide, so resuming means
    saying it again — the same reasoning `unheard` already encodes for
    `next_slide` coming back from a detour. Getting this wrong is quiet: the
    tour continues, and one slide is simply never presented.
    """
    run(registry.dispatch("start_presentation", {"tour": "deck"}))
    run(registry.dispatch("next_slide", {}))
    at = slides.current_slide()["position"]
    slides.pause_for_barge_in()          # talked over, mid-sentence

    run(registry.dispatch("stop_presentation", {}))
    out = run(registry.dispatch("resume_presentation", {}))
    assert out["position"] == at, "skipped the slide the guest never heard"


def test_starting_a_new_tour_forgets_the_parked_one(slides):
    """Otherwise a `resume_presentation` later in the conversation drags the
    guest back into a presentation that is no longer running."""
    run(registry.dispatch("start_presentation", {"tour": "deck"}))
    run(registry.dispatch("next_slide", {}))
    run(registry.dispatch("stop_presentation", {}))

    run(registry.dispatch("start_presentation", {"tour": "deck"}))
    assert slides.STATE["parked"] is None

    run(registry.dispatch("hide_slide", {}))
    out = run(registry.dispatch("resume_presentation", {}))
    assert out["resumed"] is False


def test_resuming_with_nothing_parked_says_so_instead_of_failing(slides):
    """A guest who says "ไปต่อ" out of nowhere gets an answer, not an error
    and not a presentation they didn't ask for."""
    slides.reset_state()
    out = run(registry.dispatch("resume_presentation", {}))
    assert out["resumed"] is False
    assert "start_presentation" in out["instruction"]


def test_a_language_question_is_not_a_request_to_stop(slides):
    """The description is the fix, so the description is what's tested.

    "Can you speak Chinese?" routed to `stop_presentation`. Nothing in the
    code could have prevented that — the model chooses — so the guard has to
    be in the text the model reads, and it has to name the case rather than
    describe the tool in general terms.
    """
    from app.tools import load_tools

    entry = next(t for t in load_tools() if t.name == "stop_presentation")
    text = entry.description
    assert "ขอเปลี่ยนภาษา" in text, "the case that actually happened isn't named"
    assert "ห้ามใช้เมื่อลูกค้าแค่ถามคำถาม" in text
    assert "resume_presentation" in text, "a stop that can't be undone reads as final"


def test_hiding_the_slide_clears_every_piece_of_tour_state(slides):
    """It used to name the keys it reset and had fallen behind, leaving
    `detour`, `awaiting` and `unheard` set on a deck that no longer existed —
    the same drift `reset_state()` was written to end."""
    run(registry.dispatch("start_presentation", {"tour": "deck"}))
    run(registry.dispatch("next_slide", {}))
    slides.pause_for_barge_in()
    slides._arm_question({"id": "x", "ask_th": "เคยมาไหมคะ"})

    run(registry.dispatch("hide_slide", {}))
    for key, blank in slides._blank_state().items():
        assert slides.STATE[key] == blank, "%s survived hide_slide" % key


def test_closing_the_presentation_shuts_the_canva_window(slides, monkeypatch):
    """Once Canva fills a wall-mounted screen there has to be a spoken way out.
    `stop_presentation` leaves the picture up and `hide_slide` leaves the
    browser parked — neither gets a fullscreen window off the wall, and a
    sales gallery has no keyboard."""
    # `close_presentation` now refuses unless somebody asked for it — echo
    # from the speakers was transcribed as "bit like" and closed the deck in
    # a live run. Saying so out loud here is part of the test: closing is a
    # thing a guest requests, not a thing that happens.
    from app import heard
    heard.record("ปิดสไลด์ด้วยค่ะ")

    closed = []

    async def fake_shutdown():
        closed.append(True)

    monkeypatch.setattr("app.tools.canva_display.shutdown", fake_shutdown)

    run(registry.dispatch("start_presentation", {"tour": "deck"}))
    run(registry.dispatch("next_slide", {}))

    out = run(registry.dispatch("close_presentation", {}))
    assert out["closed"] is True
    assert closed, "the Canva window was left open"
    assert slides.current_slide() is None, "the display was left showing a slide"
    assert slides.STATE["deck"] == []


def test_closing_still_clears_the_screen_if_the_window_will_not_close(slides, monkeypatch):
    """A browser that refuses to die must not leave the tour running. The
    screen is logically off either way, and the next launch already copes
    with a dead handle."""
    from app import heard
    heard.record("ปิดสไลด์ด้วยค่ะ")

    async def refuses():
        raise RuntimeError("browser is wedged")

    monkeypatch.setattr("app.tools.canva_display.shutdown", refuses)

    run(registry.dispatch("start_presentation", {"tour": "deck"}))
    out = run(registry.dispatch("close_presentation", {}))

    assert out["ok"] is True
    assert slides.STATE["deck"] == []
    assert slides.current_slide() is None


def test_the_three_ways_to_end_a_presentation_stay_distinct(slides):
    """They are easy to collapse into one another by accident, and each answers
    a different sentence a guest actually says:

        "หยุดพรีเซนต์"  stop_presentation  — stop talking, keep the picture
        "ปิดจอ"         hide_slide         — blank the screen, keep the browser
        "ปิดพรีเซนต์"   close_presentation — put it all away
    """
    run(registry.dispatch("start_presentation", {"tour": "deck"}))
    run(registry.dispatch("next_slide", {}))
    run(registry.dispatch("stop_presentation", {}))
    assert slides.current_slide() is not None, "stop_presentation blanked the screen"

    run(registry.dispatch("start_presentation", {"tour": "deck"}))
    run(registry.dispatch("hide_slide", {}))
    assert slides.current_slide() is None


# ============ the nudge must not talk over the guest ============


def test_the_nudge_waits_until_the_guest_has_heard_the_slide(slides):
    """Measured, not guessed: over one real session, 21 of 81 slides with a
    script longer than 40 characters delivered under 60% of it — one as little
    as 16%, so the guest heard one sentence of six and the deck moved on.

    The cause is that `turn_complete` means the model stopped *generating*,
    not that anyone heard it. It generates several times faster than it
    speaks, so at that moment there can still be twenty seconds of narration
    queued in the browser. The nudge goes in as a user turn, and a user turn
    during playback is a barge-in — Gemini cancels its own generation and
    drops the rest. The mechanism built to keep the tour moving was cutting
    the tour off.

    Same audio-lead rule as the picture, one path further out: anything that
    changes what the guest *experiences* has to be timed against their ear.
    """
    import asyncio

    from app import display
    from app.session import VoiceSession

    class FakeProvider:
        def __init__(self):
            self.sent = []

        async def send_text(self, text):
            self.sent.append(text)

    sess = VoiceSession.__new__(VoiceSession)
    sess._nudge_index = None
    sess._nudge_count = 0
    sess._spoke_this_turn = True
    sess._nudge_task = None
    sess.provider = FakeProvider()
    _as_the_live_session(sess)

    slides.STATE.update({"deck": ["a", "b", "c"], "index": 0, "detour": False})

    async def body():
        display.set_audio_lead(20_000)      # twenty seconds still unheard
        await sess._nudge_tour_if_stalled()
        await asyncio.sleep(0.05)
        assert sess.provider.sent == [], (
            "nudged with 20s of narration still queued — Gemini reads that as "
            "a barge-in and drops the rest of the script"
        )

        display.set_audio_lead(0)           # the guest has caught up
        # wait_until_heard polls at 0.25s, then pauses another 0.4s
        await asyncio.sleep(1.2)
        assert slides.CONTINUE_NUDGE in sess.provider.sent, "the tour never resumed"

    asyncio.run(body())
    display.set_audio_lead(0)


def test_a_tour_that_advances_on_its_own_is_not_nudged_afterwards(slides):
    """The wait opens a window the old code didn't have: while we hold off,
    the model usually calls next_slide by itself. Firing the nudge anyway
    would push a second advance and skip a slide nobody heard."""
    import asyncio

    from app import display
    from app.session import VoiceSession

    class FakeProvider:
        def __init__(self):
            self.sent = []

        async def send_text(self, text):
            self.sent.append(text)

    sess = VoiceSession.__new__(VoiceSession)
    sess.provider = FakeProvider()
    slides.STATE.update({"deck": ["a", "b", "c"], "index": 0, "detour": False})

    async def body():
        display.set_audio_lead(0)
        task = asyncio.create_task(sess._nudge_once_heard(index=0))
        slides.STATE["index"] = 1          # the model advanced by itself
        await task

    asyncio.run(body())
    assert sess.provider.sent == [], "nudged a tour that had already moved on"


def test_a_finished_deck_does_not_restart_itself_later(slides):
    """"ทำไมถึงเปลี่ยนสไลด์เอง", from a live session.

    The tour ran to slide 59 and stopped. Later — a different topic, minutes
    on — the guest said "ขอดูฟิตเนส". `show_slide` set `detour`,
    `should_continue_tour()` saw a detour and said yes, the nudge fired, and
    `next_slide` narrated slide 59 over the top of the thing they had just
    asked to see. From the guest's side the robot answered them and then
    started presenting again, unprompted.

    The position is only worth keeping while a slide is still owed. At the end
    of the deck there isn't one.
    """
    slides.STATE.update({"deck": ["a", "b"], "index": 1, "detour": False})
    out = run(registry.dispatch("next_slide", {}))
    assert out["finished"] is True
    assert slides.STATE["deck"] == [], "the finished deck was left loaded"

    # Minutes later: the guest asks to see something.
    slides.show_current(slides._by_id("ew-024"))
    assert slides.should_continue_tour() is False, (
        "a finished tour came back to life and will narrate over the guest"
    )


def test_the_last_slide_stays_on_screen_after_the_deck_ends(slides):
    """Retiring the deck must not blank the wall. The guest is still looking
    at it, and the conversation usually carries on from there."""
    slides.STATE.update({"deck": ["a", "b"], "index": 0, "detour": False})
    run(registry.dispatch("next_slide", {}))     # -> b, the last one
    showing = slides.current_slide()

    run(registry.dispatch("next_slide", {}))     # -> finished
    assert slides.current_slide() == showing, "the screen went blank at the end"


def test_the_end_of_the_deck_tells_the_model_to_stop_calling_next_slide(slides):
    """Without it the model keeps calling into an empty deck and getting
    "no presentation in progress" — an error where a summary belongs."""
    slides.STATE.update({"deck": ["a"], "index": 0, "detour": False})
    out = run(registry.dispatch("next_slide", {}))
    assert "ห้ามเรียก next_slide" in out.get("instruction", "")


def test_the_tour_survives_a_session_resume(slides):
    """Fixing the go_away crash traded a dead robot for a silent one.

    Gemini caps session length and warns with `go_away`; the provider now
    closes and rejoins with a resumption handle instead of being killed. But a
    resumed model is awake and *idle* — it has no turn to finish and no reason
    to speak. Every route that restarts a tour hangs off `turn_complete`, and
    across a reconnect there is no turn at all, so a tour that was mid-flight
    just stops. Live log, ending exactly there:

        next_slide -> ew-049 (49/59)
        Gemini go_away (50s left) — resuming now
        resumed the Gemini Live session past the duration cap
        <nothing, ever again>
    """
    import asyncio

    from app import display
    from app.providers.base import ProviderEvent
    from app.session import VoiceSession

    class FakeWS:
        async def send_text(self, text): pass

    class FakeProvider:
        output_sample_rate = 24000

        def __init__(self):
            self.sent = []

        async def send_text(self, text):
            self.sent.append(text)

        def events(self):
            async def gen():
                yield ProviderEvent(kind="resumed")
            return gen()

    sess = VoiceSession.__new__(VoiceSession)
    sess.ws = FakeWS()
    sess.provider = FakeProvider()
    sess._sent_audio_ms = 0.0
    sess._spoke_this_turn = False
    sess._nudge_index = None
    sess._nudge_count = 0
    sess._nudge_task = None
    _as_the_live_session(sess)

    slides.STATE.update({"deck": ["a", "b", "c"], "index": 0, "detour": False})
    display.set_audio_lead(0)

    async def body():
        await sess._provider_to_browser()
        if sess._nudge_task is not None:
            await sess._nudge_task

    asyncio.run(body())
    assert slides.CONTINUE_NUDGE in sess.provider.sent, (
        "the tour was left standing after the session resumed"
    )


def test_a_resume_with_no_tour_running_says_nothing(slides):
    """Resuming during an ordinary conversation must not make the robot
    suddenly start presenting."""
    import asyncio

    from app import display
    from app.providers.base import ProviderEvent
    from app.session import VoiceSession

    class FakeWS:
        async def send_text(self, text): pass

    class FakeProvider:
        output_sample_rate = 24000

        def __init__(self):
            self.sent = []

        async def send_text(self, text):
            self.sent.append(text)

        def events(self):
            async def gen():
                yield ProviderEvent(kind="resumed")
            return gen()

    sess = VoiceSession.__new__(VoiceSession)
    sess.ws = FakeWS()
    sess.provider = FakeProvider()
    sess._sent_audio_ms = 0.0
    sess._spoke_this_turn = False
    sess._nudge_index = None
    sess._nudge_count = 0
    sess._nudge_task = None

    slides.reset_state()
    display.set_audio_lead(0)

    async def body():
        await sess._provider_to_browser()
        if sess._nudge_task is not None:
            await sess._nudge_task

    asyncio.run(body())
    assert sess.provider.sent == []


def test_asking_about_one_page_does_not_start_a_presentation(slides):
    """"อันนี้ตอนสั่งไปหน้าอื่นๆ แล้วมันจะพรีเซนต์อีกรอบหรอ" — yes, it did.

        คุณ: อธิบายสไลด์หน้าที่ 2   -> go_to_page -> narrates page 2   ✓
        คุณ: หน้า 50                -> go_to_page -> narrates page 50  ✓
        สไลด์ถัดไป · next slide                                        ✗
        Emma: Co-working Lounge...  (page 51)                          ✗
        สไลด์ถัดไป · next slide                                        ✗

    `go_to_page` was returning KEEP_GOING, which ends "พูดจบแล้วเรียก
    next_slide ทันที" — the order that makes a *tour* advance itself. Sent
    with the answer to a question about one page, it turned that answer into
    a presentation starting from page 50.
    """
    run(registry.dispatch("start_presentation", {"tour": "deck"}))
    out = run(registry.dispatch("go_to_page", {"page": 50}))

    assert out["slide"]["id"] == "ew-050"
    assert "next_slide" not in out["instruction"], (
        "the answer carried the tour's advance order"
    )
    assert slides.should_continue_tour() is False, (
        "the nudge will set the deck off from page 50 on its own"
    )


def test_an_explicit_advance_puts_the_tour_back_in_charge(slides):
    """Taking the wheel is not permanent. If the guest says "ไปต่อ" after
    jumping around, the tour has to resume driving itself — otherwise every
    page-number question would quietly end the presentation for good."""
    run(registry.dispatch("start_presentation", {"tour": "deck"}))
    run(registry.dispatch("go_to_page", {"page": 50}))
    assert slides.STATE["manual"] is True

    run(registry.dispatch("next_slide", {}))
    assert slides.STATE["manual"] is False
    assert slides.should_continue_tour() is True


def test_a_page_with_a_question_still_asks_it_when_jumped_to(slides):
    """Dropping KEEP_GOING must not drop the question with it.

    Finds a page that carries a question rather than naming one. Page 55 was
    hard-coded, and re-importing the deck moved the questions to different
    page numbers — the test then failed for a reason that had nothing to do
    with what it tests."""
    deck = slides._build_deck("deck")
    page = next((n for n, sid in enumerate(deck, 1)
                 if slides._by_id(sid).get("ask_th")), None)
    assert page, "no slide in the deck carries a question"

    run(registry.dispatch("start_presentation", {"tour": "deck"}))
    out = run(registry.dispatch("go_to_page", {"page": page}))

    assert out["slide"].get("ask"), "the seeded question vanished"
    assert "ask" in out["instruction"] and "รอคำตอบ" in out["instruction"]


def test_every_eval_expectation_names_something_in_the_deck(slides):
    """Catches an expectation naming a slide that does not exist.

    **It would not have caught the bug it was written after**, and that is
    worth stating rather than implying. `scripts/eval_search.py` expected a
    slide titled "GYM" for "do you have a gym"; the only slides with that word
    are "Bungee Gym & Yoga", a bungee fitness class rather than the gym, which
    is branded BIOGENESIS. So the search was right and the eval called it
    wrong four times — but "GYM" *is* in the deck, so this check passes either
    way. Reverting the fix left it green.

    What it does cover is the next failure along: a slide renamed or a typo in
    an expectation, which turns into a phantom failure nobody can explain.
    Telling the two apart — a word on the wrong slide — needs the eval run
    itself, which needs the API.
    """
    import importlib.util

    from app.tools.slides import load_slides

    spec = importlib.util.spec_from_file_location(
        "eval_search", "scripts/eval_search.py")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except SystemExit:
        pass

    titles = " ".join(
        (s.get("title_th") or "") + " " + (s.get("title_en") or "")
        for s in load_slides()
    ).lower()

    # The file, not just the built-in list — the file is where the questions
    # actually live now, so checking only the fallback would check the half
    # nobody edits.
    for question, expected in list(module.DEFAULT_QUESTIONS) + list(
            module.load_questions(None)):
        if expected is None:
            continue
        assert expected.lower() in titles, (
            "the eval expects %r for %r, and no slide in the deck has it — "
            "the expectation is wrong, not the search" % (expected, question)
        )


# ==================== the deck belongs to canva ====================


def _measured(tmp_path, monkeypatch, pages: dict, total: int = 50):
    """Point the slides dir at a copy of the real one, plus a page map."""
    import shutil
    from app.tools import canva_display

    src = __import__("pathlib").Path(settings.slides_dir)
    shutil.copy(src / "index.json", tmp_path / "index.json")
    (tmp_path / "canva_pages.json").write_text(
        json.dumps({"source_id": "canva_page_mapping", "project_id": settings.project_id,
                    "total": total, "pages": pages}), encoding="utf-8")
    monkeypatch.setattr(settings, "slides_dir", str(tmp_path))
    monkeypatch.setattr(canva_display, "_PAGE_MAP", None)
    monkeypatch.setattr(canva_display, "_PAGE_MAP_TOTAL", None)
    canva_display.load_page_map(force=True)


def test_the_deck_is_presented_in_canva_page_order(slides, tmp_path, monkeypatch):
    """Not filename order.

    `data/slides/` is our own export and it disagrees with the live design —
    59 frames against a shorter deck, some of them caught mid-transition. The
    guest is looking at Canva, so Canva decides what comes after what.
    """
    _measured(tmp_path, monkeypatch, {"ew-031": 3, "ew-001": 1, "ew-024": 2})
    assert slides._build_deck("deck") == ["ew-001", "ew-024", "ew-031"]


def test_frames_with_no_canva_page_are_not_presented(slides, tmp_path, monkeypatch):
    """The transitions, and anything deleted from the design after export.

    This is the fix for the screenshot: our side said "Junior World 31/59"
    while the Canva window showed the Journey to Mars tunnel. A frame that
    isn't a page of the deck can't be a stop on a tour of the deck.
    """
    _measured(tmp_path, monkeypatch, {"ew-001": 1, "ew-024": 2})
    deck = slides._build_deck("deck")
    assert "ew-018" not in deck and "ew-059" not in deck
    assert deck == ["ew-001", "ew-024"]


def test_the_deck_follows_however_many_pages_canva_has(slides, tmp_path, monkeypatch):
    """"ไม่ว่าหน้าจะมีเท่าไหร่" — the length is whatever was measured, and
    `total` is not allowed to become a second hard-coded 59."""
    _measured(tmp_path, monkeypatch,
              {"ew-%03d" % n: n for n in range(1, 13)}, total=12)
    assert len(slides._build_deck("deck")) == 12


def test_an_unmeasured_deck_still_presents(slides, tmp_path, monkeypatch):
    """No measurement, no regression: the export presents in its own order."""
    import shutil
    from app.tools import canva_display

    src = __import__("pathlib").Path(settings.slides_dir)
    shutil.copy(src / "index.json", tmp_path / "index.json")
    monkeypatch.setattr(settings, "slides_dir", str(tmp_path))
    monkeypatch.setattr(canva_display, "_PAGE_MAP", None)
    canva_display.load_page_map(force=True)
    # However many frames the export happens to hold — the point is that it
    # presents them all, not that there are 59 of them.
    expected = sum(1 for s in slides.load_slides() if s.get("type") == "deck")
    assert len(slides._build_deck("deck")) == expected


def test_a_canva_page_click_lands_on_the_slide_that_page_shows(
        slides, tmp_path, monkeypatch):
    """A salesperson clicking to page 3 must move the tour to whatever is
    *on* page 3 — not to `ew-003`, which is the arithmetic that started all
    of this."""
    _measured(tmp_path, monkeypatch, {"ew-001": 1, "ew-024": 2, "ew-031": 3})
    slides.reload_slides()
    shown = slides.move_to_deck_page(3)
    assert shown is not None and shown["id"] == "ew-031"
    assert shown["position"] == 3 and shown["total"] == 3


# ============ animation frames are not slides ============


def test_a_silent_frame_is_told_to_say_nothing(slides):
    """Canva builds animations by duplicating a page. "ONE PLACE. MANY
    WORLDS." is seven pages with one more circle lit each time, and the
    export sees seven slides. Narrating each one reads the same line seven
    times at a guest watching one picture move."""
    frame = {"id": "ew-017", "silent": True}
    assert slides._narration_instruction(frame) == slides.PASS_THROUGH
    assert "ห้ามพูด" in slides.PASS_THROUGH


def test_a_silent_frame_never_asks_a_question(slides):
    """`ask_th` and `silent` on the same slide would stop the deck dead
    waiting for an answer to a question nobody heard asked."""
    frame = {"id": "ew-017", "silent": True, "ask_th": "เคยไปพัทยาไหมคะ"}
    assert slides._narration_instruction(frame) == slides.PASS_THROUGH


def test_the_shipped_deck_has_no_silent_slide_carrying_a_script(slides):
    """The two must never both be set: one says speak these exact words, the
    other says say nothing. Enforced against the real index because that is
    where the mistake would actually live."""
    for slide in slides.load_slides():
        if slide.get("silent"):
            assert not slide.get("script_th"), slide["id"]
            assert not slide.get("ask_th"), slide["id"]


def test_every_deck_slide_can_be_narrated_or_is_deliberately_silent(slides):
    """A slide with neither a script nor a summary makes the model improvise
    in a sales gallery, which is the one thing it must not do."""
    for slide in slides.load_slides():
        if slide.get("type") != "deck" or slide.get("silent"):
            continue
        assert slide.get("script_th") or slide.get("summary_th"), slide["id"]


def test_reimporting_keeps_what_a_human_decided(tmp_path):
    """`silent` and the approval flags have to survive a re-import.

    Both fail silently and in the worst direction. Losing `silent` puts the
    seven "ONE PLACE" animation frames back into the narration, so the robot
    reads the same line seven times at a guest. Losing `script_approved`
    withdraws every signature without telling anyone — and the flag exists
    precisely to tell the model "a human read these exact words".

    Checks the carry list itself, because the failure is a *missing* key and
    no amount of running the script on unchanged data would reveal it.
    """
    source = Path(__file__).resolve().parent.parent / "scripts" / "import_canva_export.py"
    text = source.read_text(encoding="utf-8")
    carried = text.split('for key in (', 1)[1].split('):', 1)[0]
    for key in ("silent", "script_approved", "script_approved_by",
                "script_th", "ask_th", "keywords_th"):
        assert '"%s"' % key in carried, "re-import would drop %r" % key
