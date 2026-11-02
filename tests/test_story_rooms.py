"""The room-type pictures and the story rows (owner, 27 Oct, on the robot).

- "รูปห้องไม่ต้องแสดง dimension และก็ภาพเล็ก ไม่ต้องแสดงไดเมนชั่น" -- the eight
  plans are drawn again without the architect's dimensions
  (`build_story_assets._without_dimensions`, recipe `strip_dimensions`).
- developer_projects' two timeline rows took its two chips by position, so
  "Amazon -> Embassy Life" opened the executives story: `"tap": false`.
- Three or four bedrooms: "มีค่ะ แต่จะเป็นลักษณะ combine ..." -- a policy line.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STORIES = ROOT / "data/projects/embassy_world/stories"
UNIT_PDF = ROOT / ".tmp/story-sources/web/5.room types/260615_EMB3_UNIT.pdf"


def _builder():
    if str(ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts"))
    import build_story_assets

    return build_story_assets


def _layout_recipes():
    return [r for r in _builder().load_recipes(STORIES) if r["id"].startswith("layout_")]


def test_every_room_type_plan_is_drawn_without_dimensions():
    recipes = _layout_recipes()
    assert len(recipes) == 8
    assert all(r.get("strip_dimensions") is True for r in recipes), "a plan still shows its dimensions"


def test_the_redrawn_plan_has_no_figures_no_dimension_lines_and_keeps_the_walls():
    """On the real drawing (owner's material, gitignored -- only on a machine
    that has it)."""
    if not UNIT_PDF.exists():
        pytest.skip("260615_EMB3_UNIT.pdf not in .tmp/story-sources: the dimension strip is NOT being checked here")
    import fitz

    b = _builder()
    doc = fitz.open(UNIT_PDF)
    for recipe in _layout_recipes():
        page = doc[recipe["page"] - 1]
        s = 72.0 / recipe["dpi"]
        area = fitz.Rect(*[v * s for v in recipe["crop"]])
        drawn, new = b._without_dimensions(page, recipe)
        with drawn:
            assert not re.search(r"\d", new.get_text()), recipe["id"] + ": a figure is still on the plan"
            kept = new.get_drawings()
            near = area + (-80, -80, 80, 80)     # the crop is the walls; the dimensions ran outside
            dims = [fitz.Rect(d["rect"]) for d in page.get_drawings()
                    if d.get("layer") in b.DIMENSION_LAYERS and fitz.Rect(d["rect"]).intersects(near)]
            assert dims, recipe["id"] + ": the source has its dimension layers"
            kept_rects = {tuple(round(v, 1) for v in fitz.Rect(d["rect"])) for d in kept}
            assert not kept_rects & {tuple(round(v, 1) for v in r) for r in dims}, recipe["id"]
            walls = [d for d in page.get_drawings()
                     if d.get("layer") == "WALL" and fitz.Rect(d["rect"]).intersects(area)]
            assert len(kept) >= len(walls), recipe["id"] + ": the walls went with them"
            # no thin line ends on a dimension tick: those are its dimension
            # and extension lines, drawn on layer "0" (B107, B113: 7 each)
            ticks = [fitz.Rect(d["rect"]) + (-1.5, -1.5, 1.5, 1.5) for d in page.get_drawings()
                     if d.get("layer") in b.DIMENSION_LAYERS and fitz.Rect(d["rect"]).intersects(near)]
            on_tick = [d for d in kept if (d.get("width") or 0) < 0.4
                       and any(t.contains(p) for p in b._vector_points(d) for t in ticks)]
            assert not on_tick, recipe["id"] + ": a dimension line is still drawn"
    # STUDIO B107: the slanted wall's reference line ran beside it, inside the
    # walls' box -- the case the "enclosed" test is for
    b107 = next(r for r in _layout_recipes() if r["id"] == "layout_studio_b107")
    drawn, new = b._without_dimensions(doc[b107["page"] - 1], b107)
    with drawn:
        long_lines = [d for d in new.get_drawings() if len(d["items"]) == 1 and d["items"][0][0] == "l"
                      and abs(d["items"][0][2] - d["items"][0][1]) > 150]
        assert not long_lines, "the slanted wall's reference line is back"


def test_a_row_can_opt_out_of_the_position_binding():
    from app import stories

    catalog = json.loads((STORIES / "stories.json").read_text(encoding="utf-8"))
    story = next(s for s in catalog["stories"] if s["id"] == "developer_projects")
    page = stories.page_payload(story, catalog)
    assert [item.get("tap") for item in page["params"]["items"]] == [None, None], \
        "a timeline row opens a chip's story"
    assert {t["id"] for t in page["taps"]} == {"story_developer_projects_developer",
                                              "story_developer_projects_executives"}, "the chips stay"
    # and the rule it opts out of still binds where it is right
    compare = next(s for s in catalog["stories"] if s["id"] == "unit_compare_1br_2br")
    page = stories.page_payload(compare, catalog)
    assert [item.get("tap") for item in page["params"]["items"]] == [t["id"] for t in page["taps"]]


def test_three_or_four_bedrooms_are_a_combined_special_unit():
    from app.prompts import SALES_HOST_BLOCK

    line = next(ln for ln in SALES_HOST_BLOCK.splitlines() if ln.startswith("- ห้องสามห้องนอนหรือสี่ห้องนอน"))
    for words in ("ตอบว่ามี", "ห้องรวม (combine)", "หนึ่งห้องนอนหรือสองห้องนอน", "ห้องพิเศษ",
                  "ติดต่อทีมขายโดยตรง", "ห้ามตอบว่าไม่มี"):
        assert words in line, words
    assert not re.search(r"\d", line)


def test_a_date_heard_in_a_misheard_room_name_is_not_answered():
    """Robot 27 Oct 17:23: "บันจี้ยิม" heard as "วันที่..." -- she told the
    date, twice. Dates are out of scope; a name that sounds like a room is
    that room, else ask again."""
    from app.prompts import SALES_HOST_BLOCK

    # "ตอนนี้": the opening hours and the completion date are project questions (review 27 Oct)
    assert "วันที่หรือเวลาตอนนี้" in SALES_HOST_BLOCK
    line = next(ln for ln in SALES_HOST_BLOCK.splitlines() if ln.startswith("- ชื่อห้องส่วนกลางในจุดเด่น"))
    assert "ถามทวน" in line and "ตอบวันที่" in line


def test_the_straight_lines_of_the_room_are_drawn():
    """Review 27 Oct: a horizontal or vertical one-segment path has a rect
    with no area, and an empty Rect "intersects" nothing -- every one of them
    was dropped: the window glass, the shower trays, the fixtures."""
    if not UNIT_PDF.exists():
        pytest.skip("260615_EMB3_UNIT.pdf not in .tmp/story-sources: the straight lines are NOT being checked here")
    import fitz

    b = _builder()
    doc = fitz.open(UNIT_PDF)
    for recipe in _layout_recipes():
        drawn, new = b._without_dimensions(doc[recipe["page"] - 1], recipe)
        with drawn:
            straight = [d for d in new.get_drawings() if len(d["items"]) == 1 and d["items"][0][0] == "l"
                        and (abs(d["items"][0][1].x - d["items"][0][2].x) < 0.01
                             or abs(d["items"][0][1].y - d["items"][0][2].y) < 0.01)]
            assert len(straight) >= 5, recipe["id"] + ": the room's straight lines are gone"


def _two_stories():
    return {
        "saunas_where": {"questions_th": ["มีซาวน่าไหม", "ซาวน่าอยู่ไหน"], "questions_en": ["Is there a sauna?"]},
        "room_types": {"questions_th": ["ห้องมีกี่แบบ"], "questions_en": ["How many room types?"]},
        "salt_sauna_where": {"questions_th": ["ซาวน่าเกลืออยู่ไหน"], "questions_en": ["Is there a salt sauna?"]},
    }


def test_the_story_index_follows_the_tokenizer(monkeypatch):
    """Audit 27 Oct: a robot-face search before the slide index taught the
    tokenizer the deck's words built the story-question index with the bare
    dictionary ("ซาวน่า" -> ซาว + น่า) and kept it -- 76 listed questions
    found no story until a restart. The tokenizer's generation is in the key."""
    from app import stories
    from app.tools import retrieval

    monkeypatch.setattr(retrieval, "_tokeniser", None)
    monkeypatch.setattr(retrieval, "_tokeniser_ready", False)
    monkeypatch.setattr(retrieval, "_tokeniser_gen", retrieval._tokeniser_gen)
    stories.reset()
    offered = {"saunas_where": {"questions_th": ["มีซาวน่าไหม"]}, "room_types": {"questions_th": ["ห้องมีกี่แบบ"]}}
    stories.story_for_query("ห้องมีกี่แบบ", offered)          # the index, built with the bare dictionary
    retrieval.init_tokeniser(["ซาวน่า"])                      # then the deck teaches its words
    # "ซาวน่า" is one word now; the index's story said ซาว + น่า
    assert stories.story_for_query("ซาวน่า", offered) == "saunas_where", "the index kept the old tokenizer's words"
    stories.reset()


def test_a_listed_question_word_for_word_is_its_storys():
    """Audit 27 Oct: 45 of the 733 listed questions ("Is there a gym?",
    "มีซาวน่าไหม", "ห้องมีกี่แบบ") scored the same for two stories and went
    to none. Listed by exactly one story = that story; listed by two = still
    the word score (it could be either)."""
    from app import stories

    stories.reset()
    offered = _two_stories()
    assert stories.story_for_query("มีซาวน่าไหม", offered) == "saunas_where"
    assert stories.story_for_query("  Is there a SAUNA ? ", offered) == "saunas_where"
    offered["salt_sauna_where"]["questions_en"].append("Is there a sauna?")
    stories.reset()
    assert stories.story_for_query("Is there a sauna?", offered) is None
    stories.reset()


def test_a_null_row_is_refused_before_it_reaches_the_page():
    """Audit 27 Oct: params.rows [null] passed build, approval and serving and
    threw in the page's template -- an empty stage under the close button."""
    from app import stories

    story = {"id": "x_story", "template": "journey", "lines": [{"th": "ก", "en": "a"}],
             "params": {"rows": [None]}, "sources": ["facts:x"]}
    assert "rows must be a list of objects" in stories.problems(story, {})
    story["params"]["rows"] = [{"label_en": "A", "line": 0}]
    assert not [p for p in stories.problems(story, {}) if "list of objects" in p]


def test_a_map_overlay_made_for_another_picture_is_not_offered(tmp_path):
    """The real map's pins are % of ONE picture (render_story_maps.py writes
    both, the overlay naming the PNG's sha256 the recipe pins). A map
    re-rendered without its overlay -- or the other way round -- puts every
    pin on the wrong street, silently: the build refuses the story."""
    from scripts import build_story_assets as builder
    from scripts.story_fixture import build

    root = build(tmp_path)
    catalog = json.loads((root / "stories.json").read_text(encoding="utf-8"))
    story = next(s for s in catalog["stories"] if s["id"] == "fx_map")
    assert story["params"]["project"] and story["params"]["rows"][0]["pin"], "the overlay was inlined"
    assert story["params"]["rows"][1]["walk_route"]
    over_path = root / "maps" / "fx_map.json"
    over = json.loads(over_path.read_text(encoding="utf-8"))
    over["image_sha256"] = "0" * 64
    over_path.write_text(json.dumps(over), encoding="utf-8")
    _catalog, report = builder.merge(root, write=False)
    assert any("another picture" in p for p in report["fx_map"]["problems"]), report["fx_map"]
    # and a design never carries the pins itself: the build writes them
    design = json.loads((root / "design" / "fixture.json").read_text(encoding="utf-8"))
    rows = next(s for s in design if s["id"] == "fx_map")["params"]["rows"]
    assert all("pin" not in r and "route" not in r for r in rows)


def test_the_map_base_is_not_a_picture_to_enlarge():
    from app import stories

    story = {"id": "m", "params": {"map_asset": "map_x", "rows": [{"label_en": "A"}]}}
    assert stories.zoom_targets(story) == []


def test_a_story_tap_in_another_language_names_it():
    """Audit 27 Oct: a German guest's story tap was told "This conversation
    is in English", a Japanese one's "in Chinese" (the language came from the
    letters of her reply). The server's own language of the call is said."""
    from app import stories

    story = {"id": "s", "lines": [{"th": "ก", "en": "a"}], "questions_th": ["ก"], "params": {}}
    tap = {"id": "story_s_x", "label_en": "Hospitals", "label_th": "โรงพยาบาล", "next_story": "nearby_hospitals"}
    assert "This conversation is in German: answer in German" in stories.tap_text(tap, None, "de")
    assert "in English" in stories.tap_text(tap, None, "en")
    more = stories.tap_more_text({"more": True, "story": story, "label_en": "Lobby"}, None, "ja")
    assert "This conversation is in Japanese: answer in Japanese." in more
