"""The robot face's motion stories (app/stories.py, owner 12 Oct 2026).

The rules under test are the ones every customer source here pays for: the
model picks WHICH story (an enum, nothing else), every word and number is the
approved catalog's, a draft or an edited catalog offers nothing, a picture is
served only while its bytes still hash to the pin, a tap sends an id and the
server owns the words, the sales-room desk never sees any of it -- and on the
page, the story follows her AUDIO, never the guest's words, and stays light
enough for the robot's GPU.

Each test builds its own catalog from scripts/story_fixture.py (drawn
pictures, approval state pinned by the test), never the project's.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import stories
from app.config import settings

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "client" / "voice-preview.html"
ENGINE = ROOT / "client" / "assets" / "emma-story.js"
STYLE = ROOT / "client" / "assets" / "emma-story.css"


# ---- fixtures -----------------------------------------------------------------------

@pytest.fixture(scope="module")
def _built(tmp_path_factory):
    from scripts.story_fixture import build

    base = tmp_path_factory.mktemp("stories")
    return build(base, approved=False)


@pytest.fixture
def catalog_dir(_built, tmp_path, monkeypatch):
    """A fresh copy of the fixture catalog (draft) as the project's stories folder."""
    root = tmp_path / "stories"
    shutil.copytree(_built, root)
    monkeypatch.setattr(stories, "folder", lambda project_id=None: root)
    stories.reset()
    yield root
    stories.reset()


def _approve(root):
    from scripts.story_fixture import approve

    catalog = approve(root)
    stories.reset()
    return catalog


def _units_tools():
    """show_room_type registered (a story tap leads to it), even if another
    test cleared the registry while app.tools.units stayed imported."""
    import app.tools.registry as reg
    import app.tools.units as units_module

    entry = reg.get("show_room_type")
    if entry is None or entry.handler is not units_module.show_room_type:
        for name in [n for n, t in list(reg._REGISTRY.items()) if t.handler.__module__ == units_module.__name__]:
            reg._REGISTRY.pop(name, None)
        importlib.reload(units_module)


@pytest.fixture
def approved(catalog_dir, monkeypatch):
    _units_tools()
    _approve(catalog_dir)
    monkeypatch.setattr(settings, "stories_enabled", True)
    monkeypatch.setattr(settings, "tool_groups", "stories,units,knowledge")
    return catalog_dir


def _tool():
    import app.tools.registry as reg
    import app.tools.stories as tool_module

    entry = reg.get("present_story")
    if entry is None or entry.handler is not tool_module.present_story:
        # another test cleared the registry while the module stayed imported
        reg._REGISTRY.pop("present_story", None)
        importlib.reload(tool_module)
        entry = reg.get("present_story")
    return entry


@contextlib.contextmanager
def _as(profile="condo", face=True):
    import app.tools.registry as reg

    p = reg.session_profile.set(profile)
    f = reg.robot_face_session.set(object() if face else None)
    try:
        yield
    finally:
        reg.robot_face_session.reset(f)
        reg.session_profile.reset(p)


def _catalog(root):
    return json.loads((root / "stories.json").read_text(encoding="utf-8"))


def _write(root, catalog):
    (root / "stories.json").write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    stories.reset()


# ---- approval: deny by default ----------------------------------------------------------

def test_the_build_writes_a_draft_and_a_draft_offers_nothing(catalog_dir, monkeypatch):
    catalog = _catalog(catalog_dir)
    assert catalog["approval_status"] == "draft" and catalog["approved_by"] == ""
    monkeypatch.setattr(settings, "stories_enabled", True)
    assert stories.approval_problem(catalog) == "not_approved"
    assert stories.available() == {}
    out = stories.tool_result("fx_room_types")
    assert out["ok"] is False and "ไม่มีข้อมูล" in out["instruction"]   # quoted as forbidden
    assert "ห้ามพูดว่าไม่มีข้อมูล" in out["instruction"]


def test_an_edit_after_approval_is_a_draft_again(approved):
    assert set(stories.available()) == {
        "fx_room_types", "fx_facilities", "fx_ground", "fx_sky_pool", "fx_tower", "fx_studio",
        "fx_nearby", "fx_units_total", "fx_smart_home", "fx_travel", "fx_where", "fx_around"}
    catalog = _catalog(approved)
    catalog["stories"][0]["lines"][1]["th"] = "สตูดิโอ ขนาด 30 ตารางเมตร"      # a number nobody approved
    _write(approved, catalog)
    assert stories.approval_problem(_catalog(approved)) == "changed_since_approval"
    assert stories.available() == {}


@pytest.mark.parametrize("field,value,reason", [
    ("approved_by", "", "missing_metadata"),
    ("disclosure_scope", "internal", "not_customer_visible"),
    ("approved_at", "yesterday", "invalid_timestamp"),
    ("approved_at", "2999-01-01T00:00:00+07:00", "not_effective"),
])
def test_every_approval_field_is_checked(approved, field, value, reason):
    catalog = _catalog(approved)
    catalog[field] = value
    assert stories.approval_problem(catalog) == reason


def test_another_projects_catalog_is_not_read(approved):
    catalog = _catalog(approved)
    catalog["project_id"] = "embassy_life"
    _write(approved, catalog)
    assert stories.load_catalog() is None and stories.available() == {}


def test_remerging_keeps_an_approval_only_while_nothing_changed(approved):
    from scripts import build_story_assets as builder

    catalog, _ = builder.merge(approved)
    assert catalog["approval_status"] == "approved"          # same content, same approval
    design = approved / "design" / "fixture.json"
    data = json.loads(design.read_text(encoding="utf-8"))
    data[0]["lines"][0]["en"] = "We have three kinds of room."
    design.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    catalog, _ = builder.merge(approved)
    assert catalog["approval_status"] == "draft" and catalog["approved_by"] == ""


def test_the_approval_script_needs_a_person_and_pins_the_digest(catalog_dir, tmp_path):
    from scripts import approve_stories

    with pytest.raises(SystemExit):
        approve_stories.approve(catalog_dir / "stories.json", "  ", "completed", audit=None)
    catalog = approve_stories.approve(catalog_dir / "stories.json", "reviewer", "completed",
                                      audit=tmp_path / "audit.jsonl")
    assert catalog["approved_by"] == "reviewer"
    assert catalog["approved_sha256"] == stories.content_digest(catalog)
    assert "reviewer" in (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert approve_stories.revoke(catalog_dir / "stories.json")["approval_status"] == "draft"


# ---- who sees the tool ------------------------------------------------------------------

def test_off_by_default_even_on_the_robot_face(approved, monkeypatch):
    entry = _tool()
    monkeypatch.setattr(settings, "stories_enabled", False)
    import app.tools.registry as reg

    with _as("condo", face=True):
        assert not reg.allowed(entry)


def test_only_the_robot_faces_own_condo_session_sees_it(approved):
    import app.tools.registry as reg

    entry = _tool()
    with _as("condo", face=True):
        assert reg.allowed(entry)
    with _as("condo", face=False):                  # the sales-room desk page
        assert not reg.allowed(entry)
    for profile in ("joke", "emma", "translator"):
        with _as(profile, face=True):
            assert not reg.allowed(entry), profile


def test_an_opt_in_group_never_loaded_by_a_blank_tool_groups(approved, monkeypatch):
    from app import tools
    import app.tools.registry as reg

    assert "stories" in tools._OPT_IN
    assert "app.tools.stories" not in tools._modules_to_load(None)
    monkeypatch.setattr(settings, "tool_groups", "")
    with _as("condo", face=True):
        assert not reg.allowed(_tool())
    monkeypatch.setattr(settings, "multi_session", True)
    monkeypatch.setattr(settings, "tool_groups", "stories")
    with _as("condo", face=True):
        assert not reg.allowed(_tool()), "shared test server: not a shared screen"


def test_a_draft_catalog_hides_the_tool(catalog_dir, monkeypatch):
    import app.tools.registry as reg

    monkeypatch.setattr(settings, "stories_enabled", True)
    monkeypatch.setattr(settings, "tool_groups", "stories")
    with _as("condo", face=True):
        assert not reg.allowed(_tool())


def test_the_model_picks_a_topic_and_nothing_else(approved):
    from app.tools import registry as reg
    from app.tools import stories as tool_module

    assert set(tool_module.PARAMETERS["properties"]) == {"topic"}
    assert tool_module.PARAMETERS["required"] == ["topic"]
    with _as("condo", face=True):
        description, parameters = reg.declaration(_tool())
    assert set(parameters["properties"]) == {"topic"}
    assert parameters["properties"]["topic"]["enum"] == sorted(stories.available())
    assert "fx_tower = ตึกสูงกี่ชั้น" in description
    # one string chosen from the catalog's ids: nothing a number could ride in
    assert parameters["properties"]["topic"]["type"] == "string"
    assert '"number"' not in json.dumps(parameters) and '"integer"' not in json.dumps(parameters)


def test_an_unknown_topic_is_refused_not_guessed(approved):
    for topic in ("fx_nope", "", None, 3, "FX_TOWER"):
        out = stories.tool_result(topic)
        assert out["ok"] is False and out["error"] == "unknown_story"


# ---- what the model and the page get --------------------------------------------------------

def test_the_tool_result(approved):
    with _as("condo", face=True):
        out = asyncio.run(_tool().handler(topic="fx_room_types")) if asyncio.iscoroutinefunction(
            _tool().handler) else _tool().handler(topic="fx_room_types")
    assert out["ok"] and out["screen"] == "story" and out["topic"] == "fx_room_types"
    assert [line["th"] for line in out["say"]][1] == "สตูดิโอ ขนาด 24 ถึง 29 ตารางเมตร"
    page = out["story"]
    assert page["template"] == "stack" and len(page["beats"]) == 5
    assert "สตูดิโอ" in page["beats"][1]["cues"] and "studio" in page["beats"][1]["cues"]
    assert page["beats"][1]["w"]["th"] == stories.norm_len("สตูดิโอ ขนาด 24 ถึง 29 ตารางเมตร")
    src = page["params"]["items"][0]["asset"]["src"]
    assert re.fullmatch(r"/story-asset/[0-9a-f]{64}", src)
    # rows carry their taps (one per item, by position), and each tap is an id + labels only
    assert [i["tap"] for i in page["params"]["items"]] == [t["id"] for t in page["taps"]]
    assert all(set(t) == {"id", "label_en", "label_th"} for t in page["taps"])
    # every clause of the instruction that this engine invites
    for words in ("ตามลำดับ", "ภาษาเดียวกับที่ลูกค้าพูด", "ห้ามเพิ่มตัวเลข", "ห้ามพูดราคา",
                  "\"ไม่มีข้อมูล\"", "ห้ามเรียก present_story ซ้ำ", "ดูบนจอนะคะ"):
        assert words in out["instruction"], words
    # preliminary_concept: the owner's ban on the hedge rides along, quoted
    assert "ห้ามพูดว่า \"เป็นแนวคิดเบื้องต้น\"" in out["instruction"]
    assert len(json.dumps(out, ensure_ascii=False)) < 6000


def test_the_next_question_is_routed_to_the_next_story(approved):
    """Robot log 14 Oct: after the overview story asked which floor, "ขอ
    เริ่มต้นจากชั้น basement" was answered from memory with no picture. The
    result now names the story's own follow-ups (its taps that lead to an
    offered story) and quotes the reply she gave instead; a story with no
    such tap carries no such line."""
    out = stories.tool_result("fx_facilities")
    assert "ชั้น G -> fx_ground" in out["instruction"]
    assert "ก่อนพูดทุกครั้ง" in out["instruction"] and "ชั้นใต้ดินของเรามีสิ่งอำนวยความสะดวกครบครัน" in out["instruction"]
    assert "ห้ามเรียก present_story ซ้ำในคำตอบนี้" in out["instruction"]       # still once per answer
    plain = stories.tool_result("fx_room_types")                              # taps lead to a tool, not a story
    assert "คำถามถัดไปของลูกค้าเป็นคำถามใหม่" not in plain["instruction"]
    offered = stories.available()
    assert stories.follow_ups({"id": "a", "taps": [{"next_story": "fx_nope", "label_th": "x"},
                                                   {"next_story": "a", "label_th": "self"}]}, offered) == []


def _search_tool():
    import app.tools.knowledge as knowledge
    import app.tools.registry as reg

    entry = reg.get("search_condo_info")
    if entry is None or entry.handler is not knowledge.search_condo_info:
        reg._REGISTRY.pop("search_condo_info", None)
        knowledge = importlib.reload(knowledge)
        entry = reg.get("search_condo_info")
    return knowledge, entry


def test_a_search_for_a_story_topic_is_handed_to_the_story(approved):
    """Robot log 14 Oct (owner's own call): parking and "a hospital nearby"
    were searched instead of told, the parking search put the basement-and-
    spa slide up, and the repeats got no picture. Where present_story is
    offered, a search that is a story's topic comes back as an order to tell
    that story -- no answer text to read instead, no slide. Anywhere else the
    search is the search."""
    _tool()
    knowledge, _ = _search_tool()
    with _as("condo", face=True):
        out = knowledge.search_condo_info("ห่างทะเลไหม")
        assert out["story_topic"] == "fx_nearby" and out["results"] == []
        assert 'present_story(topic="fx_nearby")' in out["instruction"]
        assert "ห้ามตอบจากความจำ" in out["instruction"]
        # a price is still the price gate's, never a story
        assert "story_topic" not in knowledge.search_condo_info("ค่าส่วนกลางเท่าไหร่")
    with _as("condo", face=False):
        assert "story_topic" not in knowledge.search_condo_info("ห่างทะเลไหม")


def test_a_search_after_the_story_was_told_searches(approved):
    """Robot 20 Oct: after fitness_where, "Why biogenesis?" and "What is so
    special about this gym?" were searched -- for what the lines did not say
    -- and handed back to the same story: its lines three times running. The
    story already up is never the answer to the search that follows it; a
    call that has not seen it still gets the story."""
    from app import stories, turnlog

    _tool()
    knowledge, _ = _search_tool()
    with _as("condo", face=True):
        token = turnlog.session_id.set("told")
        try:
            assert stories.tool_result("fx_nearby")["ok"]
            assert stories.last_shown() == "fx_nearby"
            assert "story_topic" not in knowledge.search_condo_info("ห่างทะเลไหม")
        finally:
            turnlog.session_id.reset(token)
        token = turnlog.session_id.set("another call")
        try:
            assert knowledge.search_condo_info("ห่างทะเลไหม")["story_topic"] == "fx_nearby"
        finally:
            turnlog.session_id.reset(token)


def test_the_story_just_told_is_just_told_for_a_while(approved, monkeypatch):
    """Review 26 Oct: `last_shown` lasted the whole call -- only another story
    replaced it -- so a guest back on the topic minutes later, after other
    answers had taken the screen, got a plain search and no story (the 14 Oct
    "the repeats got no picture" failure the hand-off exists for). The
    follow-ups right after the story still search (20 Oct: 24 and 25 s after
    it), several in a row; past the window the topic goes to its story again;
    another story takes over at once."""
    from app import turnlog

    _tool()
    knowledge, _ = _search_tool()
    now = [1000.0]
    monkeypatch.setattr(stories, "_clock", lambda: now[0])
    window = stories.SHOWN_WINDOW_S
    assert 60 <= window <= 120          # the longest story (26 s) and a few follow-ups on it
    with _as("condo", face=True):
        token = turnlog.session_id.set("told")
        try:
            assert stories.tool_result("fx_nearby")["ok"]
            # each follow-up about it keeps it "just told" for another window
            for step in (25, window - 5, window - 5):
                now[0] += step
                assert "story_topic" not in knowledge.search_condo_info("ห่างทะเลไหม"), step
            # another answer in between does not keep it
            knowledge.search_condo_info("ล็อบบี้ตกแต่งแบบไหน")
            now[0] += window + 1
            assert stories.last_shown() is None
            assert knowledge.search_condo_info("ห่างทะเลไหม")["story_topic"] == "fx_nearby"
            # told again: just told again
            assert stories.tool_result("fx_nearby")["ok"]
            now[0] += 10
            assert "story_topic" not in knowledge.search_condo_info("ห่างทะเลไหม")
            # another story takes over at once, inside the window too
            assert stories.tool_result("fx_ground")["ok"]
            assert stories.last_shown() == "fx_ground"
            assert knowledge.search_condo_info("ห่างทะเลไหม")["story_topic"] == "fx_nearby"
        finally:
            turnlog.session_id.reset(token)


def test_a_reset_forgets_the_story_each_call_told(approved):
    """Review 26 Oct: conftest resets this module before every test, but the
    story each call last told survived it -- and every test without a turn-log
    session shares the key "", so a story an earlier test told decided
    whether a later test's search was handed to its story."""
    with _as("condo", face=True):
        assert stories.tool_result("fx_nearby")["ok"]
    assert stories.last_shown() == "fx_nearby"
    stories.reset()
    assert stories.last_shown() is None
    # rows of calls long over make room; the call telling a story keeps its own
    stories._SHOWN.update({"call %d" % i: ("fx_nearby", -1e9) for i in range(300)})
    with _as("condo", face=True):
        assert stories.tool_result("fx_ground")["ok"]
    assert list(stories._SHOWN) == [""] and stories.last_shown() == "fx_ground"


def test_the_map_on_a_face_without_a_web_window_is_the_location_story(approved, monkeypatch):
    """Robot 20 Oct: "show me the location on Google Maps" -> "I am unable to
    display the map". The robot has no web window; its face has the map."""
    from app.tools import units, webstage

    monkeypatch.setattr(webstage, "enabled", lambda: False)
    _tool()
    with _as("condo", face=True):
        monkeypatch.setattr(stories, "story_for_query", lambda q, offered=None: "fx_nearby")
        assert 'present_story(topic="fx_nearby")' in units._no_web_map()
    with _as("condo", face=False):
        assert units._no_web_map() == units.NO_WEB_MAP


def test_the_search_declaration_names_the_story_first_only_where_it_is_offered(approved):
    import app.tools.registry as reg

    _tool()
    knowledge, entry = _search_tool()
    assert knowledge.SEARCH_STORY_EXAMPLES in knowledge.SEARCH_DESCRIPTION   # the cut really cuts
    with _as("condo", face=True):
        description, parameters = reg.declaration(entry)
        assert description.startswith(knowledge.SEARCH_STORIES_FIRST)
        assert "ที่จอดรถกี่คัน" not in description and "โรงพยาบาลกี่กิโล" not in description
        assert parameters is knowledge.SEARCH_PARAMETERS
    with _as("condo", face=False):
        description, parameters = reg.declaration(entry)
        assert description is knowledge.SEARCH_DESCRIPTION and parameters is knowledge.SEARCH_PARAMETERS


def test_the_questions_that_missed_on_the_robot_match_their_story():
    """The real catalog's questions (read only): what the model searched or
    the guest asked on 14 Oct names its story; asking words, a fee, a price
    and a topic no story covers name none."""
    catalog = json.loads((ROOT / "data/projects/embassy_world/stories/stories.json").read_text(encoding="utf-8"))
    offered = {s["id"]: s for s in catalog["stories"]}
    for query, topic in (("parking", "parking"), ("How many parking space are there?", "parking"),
                         # owner 21 Oct: hospitals and schools have their own stories now
                         # (narrow question = its own story); nearby_places is the overview
                         ("Is there EV charging?", "parking"), ("hospital nearby", "nearby_hospitals"),
                         ("school nearby", "nearby_schools"), ("Is there a hospital nearby?", "nearby_hospitals"),
                         ("how many units", "unit_count"), ("ที่จอดรถมีกี่คัน", "parking")):
        assert stories.story_for_query(query, offered) == topic, query
    for query in ("แนะนำโครงการหน่อย", "parking fee", "ค่าส่วนกลาง", "pet policy", "", None):
        assert stories.story_for_query(query, offered) is None, query


def test_the_questions_the_robot_handed_to_sales_on_15_oct_reach_their_story():
    """Owner 15 Oct: "เช็คข้อมูลกับคำถามทั้งหมด อย่าให้หลุด". The robot logs of
    12-15 Oct, guests' words and the model's own queries, each answered "ทีม
    ขายจะให้รายละเอียด" while a story held the number: a joining word ("ของ",
    "นี้", "ที่นี่"), the project's own name, or a phrasing no story listed
    pulled the match under its bar. 16 projects had no story at all."""
    catalog = json.loads((ROOT / "data/projects/embassy_world/stories/stories.json").read_text(encoding="utf-8"))
    offered = {s["id"]: s for s in catalog["stories"]}
    for query, topic in (
            ("ความหนาของพื้นโครงการ", "floor_heights"),                    # the model's query, 15 Oct
            ("พื้นของโครงการที่นี่หนากี่เซนติเมตร", "floor_heights"),
            ("ระยะฝ้าของห้องพักสูงอยู่เท่าไหร่", "floor_heights"),
            ("พื้นที่ของฟิตเนสขนาดเท่าไหร่", "fitness_where"),
            ("ขนาดพื้นที่โซเชียลคลับขนาดเท่าไหร่", "social_lounge_where"),
            ("ขอชื่อโครงการทั้ง 16 โครงการ", "developer_projects"),
            ("ผลงานของผู้สร้างโครงการนี้มีอะไรบ้าง มีที่ไหนบ้าง", "developer_projects"),
            ("โครงการ Embassy World ห่างจากเซ็นทรัลพัทยา กี่กิโลเมตร", "travel_malls"),   # 12 Oct
            ("โครงการ Embassy World ห่างจาก Terminal 21 กี่กิโลเมตร", "travel_malls"),
            ("ที่นี่มีที่จอดรถกี่คัน", "parking"),
            ("สระว่ายน้ำลึกเท่าไหร่", "masterplan_lagoon"),
            # what must not move: the name is the concept story's question
            ("Embassy World คืออะไร", "project_concept"),
            ("มีกี่อาคาร", "campus_buildings")):
        assert stories.story_for_query(query, offered) == topic, query
    # the lagoon's 1.30 m is not the Sky Pool's or the kid pool's
    for query in ("Sky Pool ลึกเท่าไหร่", "สระเด็กลึกเท่าไหร่", "Aqua Cinema ลึกเท่าไหร่"):
        assert stories.story_for_query(query, offered) != "masterplan_lagoon", query
    # each of those stories carries the number it is asked for
    say = {sid: " ".join(l["th"] for l in s["lines"]) for sid, s in offered.items()}
    for sid, fact in (("floor_heights", "25 เซนติเมตร"), ("fitness_where", "197"), ("social_lounge_where", "74.3"),
                      ("travel_malls", "9.9"), ("developer_projects", "Hyde Park"), ("developer_projects", "Embassy Life")):
        assert fact in say[sid], (sid, fact)


def test_a_question_with_a_number_gets_its_motion():
    """Owner 15 Oct: "ทำพวก motion เรื่องคำถามต่างๆ ที่มีตัวเลขหรือรูปภาพด้วย".
    The report FAQ's number questions that no story told: land, units per
    building, Bangkok, and the sizes/counts the facility and room stories
    left out. Each reaches the story that says the number."""
    catalog = json.loads((ROOT / "data/projects/embassy_world/stories/stories.json").read_text(encoding="utf-8"))
    offered = {s["id"]: s for s in catalog["stories"]}
    say = {sid: " ".join(l["th"] for l in s["lines"]) for sid, s in offered.items()}
    for query, topic, fact in (
            ("ที่ดินกี่ไร่", "site_area", "15,556"),
            ("ที่ดินที่นี่ขนาดเท่าไหร่ครับ", "site_area", "9 ไร่"),          # robot log 15 Oct
            ("แต่ละตึกมีกี่ห้อง", "units_by_building", "อาคาร E 184"),
            # owner 22 Oct: "เข้า กทม. ขับรถประมาณ 1 ชั่วโมง 30 นาที" (was the report's 104 minutes)
            ("ไปกรุงเทพกี่ชั่วโมง", "travel_bangkok", "1 ชั่วโมงครึ่ง"),
            ("How long to Bangkok?", "travel_bangkok", "ดอนเมือง"),
            # owner 21 Oct: one decimal at most, the area -- not 12.825 x 5.85
            ("Junior World ใหญ่แค่ไหน", "kids_room_where", "75 ตารางเมตร"),
            ("Sky Pool ใหญ่แค่ไหน", "sky_pool_where", "143"),
            ("สะพานข้ามสระมีกี่แห่ง", "masterplan_lagoon", "สะพานข้ามสระ 3 แห่ง"),
            ("ห้องปั่นจักรยานมีจักรยานกี่คัน", "spinning_class_where", "3 คัน"),
            ("พิลาทิสมี Reformer กี่เครื่อง", "pilates_where", "Reformer 4 เครื่อง"),
            ("สตูดิโอมีแอร์กี่เครื่อง", "unit_studio", "แอร์ 1 เครื่อง"),
            ("สองห้องนอนเตียงขนาดเท่าไหร่", "unit_two_bedroom", "6 ฟุต")):
        assert stories.story_for_query(query, offered) == topic, query
        assert fact in say[topic], (topic, fact)
    # the airports and the buildings keep their own questions beside the new ones
    for query, topic in (("How long to the airport?", "travel_airports"), ("มีกี่อาคาร", "campus_buildings"),
                         ("How many units are there?", "unit_count"), ("มีตึกอะไรบ้าง", "campus_buildings")):
        assert stories.story_for_query(query, offered) == topic, query


def test_a_repeat_question_is_told_again_not_answered_from_memory():
    from app.prompts import STORIES_FACE_LINE

    assert "ที่จอดรถ" in STORIES_FACE_LINE and "โรงพยาบาล" in STORIES_FACE_LINE
    assert "ถามซ้ำ" in STORIES_FACE_LINE
    assert "Yes, there is underground parking available for the residents." in STORIES_FACE_LINE


def test_the_model_is_handed_the_lines_not_the_page(approved):
    """Integration 12 Oct: the page payload (2-5 KB of picture URLs, geometry
    and labels per call) went to the model too and stayed in the history for
    the rest of the call. `screen_only` keeps it for the screen: the model is
    handed ok / topic / say / instruction, the page and session the whole."""
    from app import tools

    with _as("condo", face=True):
        out = _tool().handler(topic="fx_room_types")
    view = tools.model_view("present_story", out)
    assert "story" not in view and set(view) == set(out) - {"story"}
    assert view["say"] == out["say"] and view["instruction"] == out["instruction"]
    assert out["story"]["params"], "the page still gets the whole result"
    # every other tool's result is handed over as the very same object
    other = {"ok": True, "story": "x"}
    assert tools.model_view("show_unit", other) is other
    assert tools.model_view("no_such_tool", other) is other
    assert tools.model_view("present_story", {"ok": False, "error": "unknown_story"}) == {
        "ok": False, "error": "unknown_story"}
    # both providers hand the model the view, never the raw result
    for path in ("app/providers/gemini.py", "app/providers/openai_realtime.py"):
        assert "tools.model_view(name, result)" in (ROOT / path).read_text(encoding="utf-8"), path


def test_a_story_does_not_fight_the_search_or_the_room_type_screen():
    """The condo rules say project facts are searched "ก่อนเสมอ"; the story
    must name itself the exception, or the two pull the same question two ways
    (and a search brings its own answer picture). A chosen room type is a real
    available unit (show_room_type, owner 12 Oct), not a story."""
    from app.prompts import STORIES_FACE_LINE
    from app.tools import stories as tool_module

    assert "แทน search_condo_info" in STORIES_FACE_LINE
    assert "ไม่ต้องเรียก search_condo_info" in tool_module.DESCRIPTION
    assert "show_room_type" in tool_module.DESCRIPTION
    assert "ไม่ต้องเรียก search_condo_info" in stories.INSTRUCTION


def test_a_story_is_not_all_the_project_knows():
    """Robot log 15 Oct 18:00: pools_all (no depth) answered "how deep", then
    the Thai repeat got "ข้อมูลความลึกของสระว่ายน้ำไม่มีระบุในระบบค่ะ" with no
    search -- "บรรทัดเหล่านี้คือคำตอบครบแล้ว" read as "nothing else exists".
    The instruction now sends anything the lines do not say to the search,
    quoting the reply she gave instead."""
    assert "คำตอบครบแล้ว" not in stories.INSTRUCTION
    assert "ให้เรียก search_condo_info ก่อนตอบ" in stories.INSTRUCTION
    assert "ข้อมูลความลึกของสระว่ายน้ำไม่มีระบุในระบบค่ะ" in stories.INSTRUCTION


def test_the_main_pools_depth_is_told_and_only_for_the_main_pool():
    """Owner 15 Oct: "ความลึกสระว่ายน้ำไม่มีหรอ". The report's 1.30 m (the
    developer's figure) is the outdoor main pool's; it says never to reuse it
    for the Sky Pool or the basement pools. Read from the designs, so a draft
    edit is checked before anybody approves it."""
    design = ROOT / "data" / "projects" / "embassy_world" / "stories" / "design"
    lines = {}
    for path in design.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        for story in (data["stories"] if isinstance(data, dict) else data):
            for line in story.get("lines", []):
                lines.setdefault(story["id"], []).append(line)
    # owner 21 Oct: numbers are said with one decimal at most -- "1.3", once "1.30"
    depth = re.compile(r"(?<![\d.])1\.30?(?![\d.])")
    for sid in ("masterplan_lagoon", "pools_all"):
        assert any(depth.search(l["th"]) and depth.search(l["en"]) for l in lines[sid]), sid
    for sid, story_lines in lines.items():
        for line in story_lines:
            if depth.search(line["th"]) or depth.search(line["en"]):
                assert re.search(r"สระหลัก|ลากูน|สระยาว", line["th"]), (sid, line["th"])
                assert not re.search(r"Sky Pool|Aqua|Kid Pool|Private Pool", line["th"] + line["en"]), (sid, line)


def test_no_page_payload_carries_a_price_notes_or_the_guests_questions(approved):
    catalog = _catalog(approved)
    for story in stories.available().values():
        payload = stories.page_payload(story, catalog)
        text = json.dumps(payload, ensure_ascii=False)
        assert not stories._MONEY.search(text), story["id"]
        for key in ("notes_th", "sources", "questions_th", "questions_en"):
            assert '"%s"' % key not in text, (story["id"], key)
        # her lines: the model's; zoom = the pictures a guest can tap (src, label, id)
        assert set(payload) == {"id", "template", "beats", "params", "taps", "zoom"}
        assert all(set(z) == {"src", "label_en", "tap"} for z in payload["zoom"])


def test_a_tap_whose_tool_is_not_offered_is_not_drawn(approved, monkeypatch):
    monkeypatch.setattr(settings, "tool_groups", "stories")      # no units: no show_room_type
    with _as("condo", face=True):
        out = stories.tool_result("fx_room_types")
    assert out["story"]["taps"] == []
    assert all("tap" not in item for item in out["story"]["params"]["items"])
    with _as("condo", face=True):
        ground = stories.tool_result("fx_ground")
    assert [t["id"] for t in ground["story"]["taps"]] == ["story_fx_ground_back"]   # next story: still there


# ---- content that never reaches a guest ------------------------------------------------------

@pytest.mark.parametrize("line", [
    "ราคาเริ่มต้น 2.9 ล้านบาท", "Prices from 2,990,000 THB", "price 3000", "ห้องละ 3 ล้าน",
    "เป็นแนวคิดเบื้องต้น", "อาจมีการเปลี่ยนแปลง", "This is a preliminary concept", "may change",
    "ตอนนี้ไม่มีข้อมูลค่ะ", "I don't know", "มี Rooftop ด้วย", "Thermal Galaxy", "อู่ตะเภา 40-60 นาที",
])
def test_banned_words_keep_a_story_off_the_screen(line):
    from scripts.story_fixture import stories as fixture

    story = fixture()[0]
    story["lines"][0]["th"] = line
    assert stories.banned_text(story), line


def test_the_room_price_may_be_named_without_a_figure():
    """The smart-home set MUST be called "not included in the room price" (owner)."""
    from scripts.story_fixture import stories as fixture

    smart = [s for s in fixture() if s["id"] == "fx_smart_home"][0]
    assert stories.banned_text(smart) == []


def test_structure_problems_are_caught(catalog_dir):
    from scripts.story_fixture import stories as fixture

    assets = _catalog(catalog_dir)["assets"]
    story = fixture()[0]
    assert stories.problems(story, assets) == []
    bad = json.loads(json.dumps(story))
    bad["params"]["items"][0]["line"] = 9
    bad["taps"][0]["tool"] = {"name": "move_body", "args": {"action": "nod"}}
    bad["params"]["items"][1]["asset"] = "not_pinned"
    bad["template"] = "video"
    found = " | ".join(stories.problems(bad, assets))
    for words in ("line index 9", "tool must be one of", "'not_pinned' not pinned", "unknown template"):
        assert words in found, words


def test_the_build_reports_numbers_no_cited_source_carries(catalog_dir):
    from scripts import build_story_assets as builder

    design = catalog_dir / "design" / "fixture.json"
    data = json.loads(design.read_text(encoding="utf-8"))
    data[0]["lines"][1]["th"] = "สตูดิโอ ขนาด 27.5 ตารางเมตร"
    data[0]["sources"] = ["facts:แบบห้องและขนาด", "report:16_pricing.md", "facts:ไม่มีหัวข้อนี้"]
    design.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    _catalog_after, report = builder.merge(catalog_dir, write=False)
    entry = report["fx_room_types"]
    assert "27.5" in entry["unsourced"]
    assert any("16_pricing" in s for s in entry["bad_sources"])           # an excluded chapter
    assert any("ไม่มีหัวข้อนี้" in s for s in entry["bad_sources"])
    assert report["fx_tower"]["unsourced"] == []                           # 8, 23, 6: all cited


# ---- pictures -------------------------------------------------------------------------------

def test_a_changed_picture_drops_its_story_and_is_not_served(approved):
    catalog = _catalog(approved)
    entry = catalog["assets"]["fx_photo_6"]                               # Sky Pool, smart home
    sha = entry["sha256"]
    assert stories.asset_body(sha) is not None
    path = approved / "assets" / entry["file"]
    path.write_bytes(path.read_bytes() + b"\0")
    stories.reset()
    offered = stories.available()
    assert "fx_sky_pool" not in offered and "fx_smart_home" not in offered
    assert "fx_tower" in offered                                         # its pictures still hash
    assert stories.asset_body(sha) is None


def test_pictures_of_a_draft_or_switched_off_catalog_are_not_served(catalog_dir, monkeypatch):
    sha = next(iter(_catalog(catalog_dir)["assets"].values()))["sha256"]
    monkeypatch.setattr(settings, "stories_enabled", True)
    assert stories.asset_body(sha) is None                                # draft
    _approve(catalog_dir)
    assert stories.asset_body(sha) is not None
    monkeypatch.setattr(settings, "stories_enabled", False)
    assert stories.asset_body(sha) is None


def test_a_quotation_or_rooftop_source_is_refused_by_name(catalog_dir, tmp_path):
    from scripts import build_story_assets as builder

    src = tmp_path / "src"
    src.mkdir()
    (src / "01_F-113_Room34_quotation_primary.png").write_bytes(b"x")
    (src / "Rooftop view.png").write_bytes(b"x")
    for name in ("01_F-113_Room34_quotation_primary.png", "Rooftop view.png"):
        with pytest.raises(builder.RecipeError, match="not allowed"):
            builder.check_recipe({"id": "x1", "source": name, "format": "webp"}, src)
    with pytest.raises(builder.RecipeError):
        builder.check_recipe({"id": "x2", "source": "../../../Windows/win.ini", "format": "webp"}, src)
    # and at serve time, whatever the hash says
    catalog = _catalog(catalog_dir)
    entry = dict(next(iter(catalog["assets"].values())), source="ue/x_quotation_primary.png")
    assert stories.asset_problem(next(iter(catalog["assets"])), entry) == "banned source"


def test_check_judges_a_fresh_checkout_against_the_pictures_it_just_rendered(_built, tmp_path, capsys):
    """`--check` writes nothing; it used to read asset_pins.json from disk, so
    on a checkout without one (assets/ and the pins are build output) every
    story came back "NOT OFFERED: asset not pinned" (integration, 12 Oct)."""
    from scripts import build_story_assets as builder

    root = tmp_path / "stories"
    for part in ("design", "recipes"):
        shutil.copytree(_built / part, root / part)
    assert builder.main(["--root", str(root), "--sources", str(_built.parent / "src"), "--check"]) == 0
    out = capsys.readouterr().out
    assert "NOT OFFERED" not in out and "not pinned" not in out
    assert sorted(p.name for p in root.iterdir()) == ["design", "recipes"]     # nothing written


def test_the_build_is_deterministic_and_crops(tmp_path):
    from PIL import Image

    from scripts import build_story_assets as builder

    src = tmp_path / "src"
    src.mkdir()
    image = Image.new("RGB", (2000, 1000), (40, 80, 160))
    image.paste((250, 250, 250), (1500, 0, 2000, 1000))                # a baked UI panel on the right
    image.save(src / "render.png")
    root = tmp_path / "stories"
    (root / "recipes").mkdir(parents=True)
    (root / "recipes" / "a.json").write_text(json.dumps([
        {"id": "hero", "source": "render.png", "crop": [0, 0, 1500, 1000], "max_w": 1080, "format": "webp"},
        {"id": "plan", "source": "render.png", "crop": None, "max_w": 400, "format": "png",
         "post": ["lines_to_alpha:#bad6ff"]},
    ]), encoding="utf-8")
    pins1, problems = builder.build_assets(root, src)
    assert problems == []
    first = (root / "assets" / "hero.webp").read_bytes()
    pins2, _ = builder.build_assets(root, src)
    assert pins1 == pins2 and (root / "assets" / "hero.webp").read_bytes() == first
    assert (pins1["hero"]["w"], pins1["hero"]["h"]) == (1080, 720)          # cropped, then sized
    hero = Image.open(root / "assets" / "hero.webp").convert("RGB")
    assert hero.getpixel((1070, 360))[0] < 120                             # the panel is gone
    assert Image.open(root / "assets" / "plan.png").mode == "RGBA"
    with pytest.raises(builder.RecipeError, match="crop"):
        builder.render({"id": "bad", "source": "render.png", "crop": [0, 0, 3000, 10]}, src)


def test_a_thin_baked_mark_is_painted_out_from_its_neighbours(tmp_path):
    """Review 12 Oct: a website callout's leader line ran from the top edge
    into the Aqua Cinema pool on the basement plan. `inpaint` blends each row
    from the pixels beside the box; `fill` takes the ground colour where the
    mark crosses printed letters. Both stay small (a wide box would smear)."""
    from PIL import Image

    from scripts import build_story_assets as builder

    src = tmp_path / "src"
    src.mkdir()
    image = Image.new("RGB", (200, 100), (60, 60, 110))
    image.paste((10, 10, 10), (99, 0, 101, 60))                           # the leader line
    image.paste((20, 220, 160), (90, 70, 98, 80))                         # a letter beside it
    image.save(src / "plan.png")
    recipe = {"id": "p", "source": "plan.png", "crop": None, "max_w": 200, "format": "png",
              "post": ["inpaint:98,0,102,60", "fill:98,70,102,80@95,90"]}
    data, w, h = builder.render(recipe, src)
    import io
    out = Image.open(io.BytesIO(data)).convert("RGB")
    assert out.getpixel((100, 30)) == (60, 60, 110)                       # the line is gone
    assert out.getpixel((100, 75)) == (60, 60, 110)                       # filled with the ground
    assert out.getpixel((92, 75)) == (20, 220, 160)                       # the letter untouched
    for op in ("inpaint:0,0,5,5", "inpaint:10,0,90,5", "fill:0,0,199,99@1,1", "blur:3"):
        with pytest.raises(builder.RecipeError):
            builder.render(dict(recipe, post=[op]), src)


def test_a_missing_letter_is_copied_in_from_its_own_line(tmp_path):
    """Owner 13 Oct: the basement slide's 'CRYTAL STONE SAUNA' fixed in the
    picture, the slide kept as it looks. `insert` copies a letter from the
    same line in before a column and squeezes the band back to its width:
    the label stays inside its pill, nothing outside the band moves."""
    import io

    from PIL import Image

    from scripts import build_story_assets as builder

    src = tmp_path / "src"
    src.mkdir()
    ground, ink, border = (60, 60, 110), (20, 220, 160), (240, 240, 240)
    image = Image.new("RGB", (200, 40), ground)
    image.paste(ink, (150, 10, 157, 31))                                  # the letter, columns 150-156
    image.paste(border, (0, 0, 10, 40))                                   # the pill's edge, outside the band
    image.save(src / "label.png")
    recipe = {"id": "l", "source": "label.png", "crop": None, "max_w": 200, "format": "png",
              "post": ["insert:20,5,179,35@60:148,158"]}
    data, w, h = builder.render(recipe, src)
    out = Image.open(io.BytesIO(data)).convert("RGB")
    assert (w, h) == (200, 40)
    assert image.getpixel((62, 20)) == ground
    assert out.getpixel((62, 20))[1] > 180                                # the copy, in before column 60
    assert out.getpixel((154, 20))[1] > 180                               # the original, squeezed a little left
    assert out.getpixel((5, 20)) == border and out.getpixel((62, 2)) == ground   # outside the band
    for op in ("insert:20,5,179,35@60:148,180",                           # letter outside the band
               "insert:20,5,179,35@10:148,158",                           # insert point outside the band
               "insert:20,5,179,35@60:100,140",                           # more than 15% of the band
               "insert:20,5,250,35@60:148,158"):                          # band outside the picture
        with pytest.raises(builder.RecipeError):
            builder.render(dict(recipe, post=[op]), src)


def test_the_route_serves_by_hash_behind_the_token(approved, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    entry = next(e for e in _catalog(approved)["assets"].values() if e["format"] == "webp")
    sha = entry["sha256"]
    monkeypatch.setattr(settings, "ws_token", "s3cret")
    client = TestClient(app)
    assert client.get(f"/story-asset/{sha}").status_code == 403
    ok = client.get(f"/story-asset/{sha}", params={"token": "s3cret"})
    assert ok.status_code == 200 and ok.headers["content-type"] == "image/webp"
    assert ok.content == (approved / "assets" / entry["file"]).read_bytes()
    assert client.get("/story-asset/" + "0" * 64, params={"token": "s3cret"}).status_code == 404
    assert client.get("/story-asset/stories.json", params={"token": "s3cret"}).status_code == 404


# ---- taps: an id in, the catalog's words out ------------------------------------------------

class _Provider:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(text)


def _session():
    from app import session as session_module

    sess = session_module.VoiceSession(None, provider_name="gemini")
    sess.provider = _Provider()
    sess.profile = "condo"
    sess._robot_face_claim = True
    return sess


def test_a_story_tap_works_only_while_that_story_is_on_screen(approved):
    sess = _session()
    with _as("condo", face=True):
        result = stories.tool_result("fx_ground")
        # not on screen yet: nothing
        asyncio.run(sess._quick_ask({"type": "quick_ask", "id": "story_fx_ground_back", "lang": "en"}))
        assert sess.provider.sent == []
        sess._note_screen("present_story", result, False)
        # its button, and one "more" tap per picture (a tapped picture, 16 Oct)
        assert sess._story_taps == {"story_fx_ground_back"} | {z["tap"] for z in result["story"]["zoom"]}
        assert all(t.startswith("story_fx_ground_more_") for t in sess._story_taps - {"story_fx_ground_back"})
        asyncio.run(sess._quick_ask({"type": "quick_ask", "id": "story_fx_ground_back", "lang": "en"}))
        assert len(sess.provider.sent) == 1
        text = sess.provider.sent[0]
        assert text.startswith('[ลูกค้าแตะปุ่ม "ทุกชั้น" บนจอ ไม่ได้พูด')
        assert 'present_story(topic="fx_facilities")' in text and "ภาษาอังกฤษ" in text
        # another story's tap, or a made-up one: nothing
        sess._quick_ask_at = -1e9
        for bad in ("story_fx_room_types_studio", "story_anything", "story_fx_ground_back x"):
            asyncio.run(sess._quick_ask({"type": "quick_ask", "id": bad}))
        assert len(sess.provider.sent) == 1


def test_every_picture_of_every_story_can_be_tapped_large():
    """Owner 16 Oct: "กดรูปบนจอ ... ขยายภาพนั้นและอธิบายเพิ่มเติม". Each picture of
    the real catalog is listed once for the page with its own "more" tap id;
    the assembly's hologram flipbook is not a picture to open."""
    catalog = json.loads((ROOT / "data/projects/embassy_world/stories/stories.json").read_text(encoding="utf-8"))
    total = 0
    for story in catalog["stories"]:
        payload = stories.page_payload(story, catalog)
        zoom = payload["zoom"]
        assert len(zoom) == len(stories.zoom_targets(story)), story["id"]
        assert len({z["src"] for z in zoom}) == len(zoom), story["id"]
        for k, z in enumerate(zoom):
            assert z["src"].startswith("/story-asset/") and z["tap"] == stories.more_tap_id(story["id"], k)
        frames = set((story.get("params") or {}).get("frames") or [])
        final = (story.get("params") or {}).get("final_asset")
        assert not frames - {final} & {t["asset"] for t in stories.zoom_targets(story)}, story["id"]
        result = {"story": payload}
        assert {z["tap"] for z in zoom} <= stories.tap_ids(result)
        total += len(zoom)
    assert total >= 130
    # a label is the picture's own: the room model is not "Interior mood"
    studio = next(s for s in catalog["stories"] if s["id"] == "unit_studio")
    labels = {t["asset"]: t["label_en"] for t in stories.zoom_targets(studio)}
    assert labels[studio["params"]["solid_asset"]] == ""
    assert all(labels[a] == studio["params"]["mood_tag_en"] for a in studio["params"]["mood_assets"])


def test_a_tapped_picture_asks_emma_for_more_and_keeps_the_screen(approved, monkeypatch):
    """The page sent a picture's id: the server's words go out (the report's
    passages about it, the story she told quoted so it is not repeated, the
    rules every report answer carries), and the story stays this answer's
    screen -- its taps live on, an answer picture does not replace it."""
    seen = []
    monkeypatch.setattr(stories, "_more_facts", lambda story, label: seen.append(label) or ["ข้อเท็จจริงจากรายงาน 123"])
    sess = _session()
    with _as("condo", face=True):
        result = stories.tool_result("fx_ground")
        sess._note_screen("present_story", result, False)
        more = result["story"]["zoom"][0]
        asyncio.run(sess._quick_ask({"type": "quick_ask", "id": more["tap"], "lang": "en"}))
        assert len(sess.provider.sent) == 1
        text = sess.provider.sent[0]
        assert text.startswith('[ลูกค้าแตะภาพ "') and "ภาพขยายอยู่บนจอแล้ว" in text
        assert "ข้อมูล: ข้อเท็จจริงจากรายงาน 123" in text and "ไม่ต้องเรียกเครื่องมือ" in text
        assert "ห้ามพูดราคา" in text and "ห้ามพูดว่า 'ไม่มีข้อมูล'" in text
        assert seen == [more["label_en"]]
        assert sess._screen_staged is True and sess._story_owns_answer is True
        assert more["tap"] in sess._story_taps
        # an answer picture in this answer does not take the screen or the taps
        sess._note_screen("search_condo_info", {"ok": True, "slide": {"id": "intro-041"}}, True)
        assert more["tap"] in sess._story_taps
        # a picture number past the story's, another story's picture: nothing
        sess._quick_ask_at = -1e9
        for bad in (stories.more_tap_id("fx_ground", 99), stories.more_tap_id("fx_room_types", 0),
                    "story_fx_ground_more_x"):
            asyncio.run(sess._quick_ask({"type": "quick_ask", "id": bad}))
        assert len(sess.provider.sent) == 1


def test_a_tapped_picture_speaks_the_talk_and_without_facts_still_answers(approved, monkeypatch):
    monkeypatch.setattr(stories, "_more_facts", lambda story, label: [])
    with _as("condo", face=True):
        result = stories.tool_result("fx_ground")
        tap = stories.find_tap("fx_ground", result["story"]["zoom"][0]["tap"])
    assert tap is not None and tap["more"] is True
    english = stories.tap_text(tap, "th", "en")
    assert english.startswith('[The guest tapped the picture "') and "answer in English" in english
    assert "ข้อมูล:" not in english and "ถามว่าอยากดูส่วนไหนของโครงการต่อ" in english
    assert "ไม่ต้องเรียกเครื่องมือ" in english


def test_a_tapped_picture_is_worked_out_off_the_event_loop(approved, monkeypatch):
    """Review 26 Oct: a picture's text runs up to six report FAQ matches and
    three passage searches (55 ms median, 185 ms worst over 163 picture taps
    on the PC, several times that on the robot) -- on the session's event
    loop, where her audio to the page waits behind it. It runs in a worker
    thread, and the call's turn-log session still resolves there."""
    import threading

    from app import turnlog

    seen = {}

    def facts(story, label):
        seen.update(thread=threading.get_ident(), call=turnlog.session_id.get())
        return ["ข้อเท็จจริงจากรายงาน 123"]

    monkeypatch.setattr(stories, "_more_facts", facts)
    sess = _session()

    async def tap(tap_id):
        seen["loop"] = threading.get_ident()
        await sess._quick_ask({"type": "quick_ask", "id": tap_id, "lang": "en"})

    with _as("condo", face=True):
        token = turnlog.session_id.set("tapping")
        try:
            result = stories.tool_result("fx_ground")
            sess._note_screen("present_story", result, False)
            asyncio.run(tap(result["story"]["zoom"][0]["tap"]))
        finally:
            turnlog.session_id.reset(token)
    assert len(sess.provider.sent) == 1 and "ข้อมูล: ข้อเท็จจริงจากรายงาน 123" in sess.provider.sent[0]
    assert seen["thread"] != seen["loop"]
    assert seen["call"] == "tapping"


def test_a_tap_is_not_answered_with_the_guests_last_sentence(approved, monkeypatch):
    """Review 26 Oct: search_condo_info puts the report's FAQ answer to the
    guest's own words first (robot 20 Oct: the model searched "Biogenesis"
    for "Why is it called Biogenesis?"). Those words are the latest caption
    after the latest activity_start, at most 30 s old -- and a tap marked no
    activity, so a search the model made for a tapped topic within 30 s of
    the guest's last sentence got that sentence's answer first. A tap is new
    activity now, as speech is."""
    from app import heard, project_report
    from app.tools import slides as slides_mod

    knowledge, _ = _search_tool()
    guest = {"source_id": "project_report", "file": "19_faq.md",
             "text": "ถาม: ฟิตเนสมีลู่วิ่งกี่เครื่อง\nตอบ: คำตอบของคำถามที่ลูกค้าพูด"}
    passage = {"source_id": "project_report", "file": "12_facilities.md", "text": "ข้อความเรื่องห้องพิลาทิส"}
    asked = []
    monkeypatch.setattr(project_report, "faq", lambda text: asked.append(text) or [dict(guest)])
    monkeypatch.setattr(knowledge, "_report_hits", lambda query: [dict(passage)])
    monkeypatch.setattr(slides_mod, "search_disclosed_slide_text", lambda query: [])
    sess = _session()
    heard.bind_session(sess)
    try:
        heard.mark_activity(sess)
        heard.record("ฟิตเนสมีลู่วิ่งกี่เครื่อง", sess, utterance_id="1")
        # a spoken question: the FAQ answer to the guest's own words, first
        out = knowledge.search_condo_info("ฟิตเนส")
        assert asked == ["ฟิตเนสมีลู่วิ่งกี่เครื่อง"]
        assert [r["text"] for r in out["results"]] == [guest["text"], passage["text"]]
        # the guest taps a button on the story; the model searches for the tap
        with _as("condo", face=True):
            result = stories.tool_result("fx_ground")
            sess._note_screen("present_story", result, False)
            asyncio.run(sess._quick_ask({"type": "quick_ask", "id": "story_fx_ground_back", "lang": "en"}))
        assert len(sess.provider.sent) == 1
        asked.clear()
        out = knowledge.search_condo_info("ห้องพิลาทิส")
        assert asked == [] and [r["text"] for r in out["results"]] == [passage["text"]]
    finally:
        heard.forget(sess)
        heard.bind_session(None)


def _more_tap(topic, k=0):
    with _as("condo", face=True):
        result = stories.tool_result(topic)
        return stories.find_tap(topic, result["story"]["zoom"][k]["tap"])


def test_a_tapped_place_is_told_what_it_is_like_whats_inside_and_why_the_name(approved, monkeypatch):
    """Owner 21 Oct: "โดยเฉพาะ facility ที่ต้องอธิบายว่าทำไมชื่อนี้ แล้วด้านในมีอะไร
    บ้าง". A picture named for a place asks the report's own FAQ the three
    things -- what it is like, what is inside, why the name -- and an answer
    that only gives its size (what the story just told) is not one of them."""
    from app import project_report

    answers = {
        "Sky Pool เป็นยังไง เป็นแบบไหน":
            "ถาม: Sky Pool เป็นยังไง\nตอบ: สระโค้งที่ยื่นออกจากอาคาร พื้นสระบางส่วนเป็นกระจกใส มองลงไปเห็นยิมข้างล่าง",
        "ใน Sky Pool มีอะไรบ้าง ทำอะไรได้บ้าง":
            "ถาม: Sky Pool ใหญ่เท่าไร มีกี่ที่นั่ง\nตอบ: กว้างประมาณ 27 เมตร ยื่นออกจากแนวอาคารประมาณ 9 เมตร",
        "What is in Sky Pool, and what can you do there?":
            "ถาม: What can you do at the Sky Pool?\nตอบ: Swim above the gym and rest on the loungers on the stone deck.",
        "ทำไมชื่อ Sky Pool ชื่อนี้หมายถึงอะไร":
            "ถาม: ทำไมชื่อ Sky Pool\nตอบ: Sky แปลว่าท้องฟ้า เป็นสระที่ยกขึ้นไปอยู่สูงเหนือโถงยิม ต่างจากสระลากูน",
    }
    asked = []

    def faq(query):
        asked.append(query)
        return [{"source_id": "project_report", "text": answers[query]}] if query in answers else []

    monkeypatch.setattr(project_report, "faq", faq)
    monkeypatch.setattr(project_report, "search", lambda q: pytest.fail("three answers need no passage search"))
    tap = _more_tap("fx_sky_pool")
    assert tap["label_en"] == "SKY POOL"
    facts = stories._more_facts(tap["story"], tap["label_en"])
    assert asked[0] == "Sky Pool เป็นยังไง เป็นแบบไหน"             # the name as written, not shouted
    assert len(facts) == 3 and "กระจกใส" in facts[0] and "loungers" in facts[1] and "ท้องฟ้า" in facts[2]
    assert not any("27 เมตร" in f for f in facts)
    text = stories.tap_text(tap, "th", "en")
    assert text.startswith('[The guest tapped the picture "SKY POOL"') and "answer in English" in text
    assert "ชื่อนี้มาจากอะไร" in text and "ความหมายตรงตัวของคำในชื่อ" in text
    assert "ห้ามแต่งเองว่าโครงการตั้งชื่อนี้เพราะอะไร" in text
    assert "ห้ามพูดซ้ำขนาด ตำแหน่ง หรือชั้นที่เรื่องเพิ่งเล่า" in text and "Sky Pool อยู่ที่ชั้น 3" in text
    assert "ไม่ต้องเรียกเครื่องมือ" in text and "ห้ามพูดราคา" in text and "27 เมตร" not in text


def test_a_tapped_place_never_takes_another_rooms_answer_or_a_note(approved, monkeypatch):
    """What the report search hands back for a place that the report does not
    describe by name, measured 21 Oct on the real catalog: another room's
    answer, the list naming every room, a chunk cut from its heading (another
    room's render), a citation, a question cut from its answer, the report's
    list of what it lacks. None of them is this place's fact; with nothing
    left she says what the picture is, without its size again."""
    from app import project_report

    monkeypatch.setattr(project_report, "faq", lambda q: [
        {"text": "ถาม: Biogenesis เป็นยังไง\nตอบ: ฟิตเนสโถงกระจกโค้งสูงสองชั้นที่ยื่นออกไปทางลากูนค่ะ"}])
    monkeypatch.setattr(project_report, "search", lambda q: [
        {"text": "**Q:** What facilities does the project have? **A:** Biogenesis, Spinning Class, Sky Pool, "
                 "Junior World and more, on three floors of building A."},
        {"text": "ภาพ concept แสดงอะไร: ห้องโทนมืดทองแดงแบบห้องนักบิน เสากลางรองรับจอโค้งขนาดใหญ่"},
        {"text": "ที่มา: บทที่ 11; Deck หน้า 42–47; แบบ DWG ชั้น 3 (Sky Pool)\n### 11.4 Sky Pool"},
        {"text": "ตัวอย่างคำตอบ ถาม: Sky Pool ลึกเท่าไร ใช้ได้กี่โมง"},
        {"text": "ข้อมูลที่ยังไม่มี: ความลึกน้ำและเวลาเปิด-ปิดของ Sky Pool ยังไม่มีในเอกสารใด"},
    ])
    tap = _more_tap("fx_sky_pool")
    assert stories._more_facts(tap["story"], tap["label_en"]) == []
    text = stories.tap_text(tap, "th", "en")
    assert "ข้อมูล:" not in text and "ห้องนักบิน" not in text and "Biogenesis" not in text
    assert "ถามว่าอยากดูส่วนไหนของโครงการต่อ" in text
    # she cannot see the picture: with nothing to tell, she must not say what
    # is in it ("Green area" became "the Stepped Garden by the pool", 21 Oct)
    assert "ห้ามบอกว่าในภาพมีอะไร" in text and "ห้ามเดาว่าภาพนี้คือที่ไหน" in text
    assert "เล่าสั้นๆ หนึ่งถึงสองประโยคว่าในภาพนี้คืออะไร" not in text
    assert 'พูดถึงภาพได้แค่ชื่อของภาพ ("SKY POOL")' in text and "ห้ามพูดซ้ำสิ่งที่เพิ่งเล่า" in text


def test_half_a_shared_name_is_not_the_place_and_a_room_the_story_never_names_is_not_its_fact(approved,
                                                                                              monkeypatch):
    """"Smart Home" is half of "Smart Air": the word only Smart Air has must be
    there ("JOURNEY TO MARS" took the corridor's "Journey to the Future คืออะไร",
    21 Oct). And for a picture with no name of its own (the ground floor plan),
    an answer about a room the story never mentions is that room's ("Why is it
    called the Hydrotherapy Capsule?" was the ground floor's "more")."""
    from app import project_report

    smart_home = {"text": "ถาม: Smart Home เป็นยังไง\nตอบ: แผงควบคุมติดผนังสั่งไฟและม่านในห้องได้จากที่เดียว"}
    smart_air = {"text": "ถาม: Smart Air เป็นยังไง\nตอบ: เครื่องฟอกอากาศที่วัดฝุ่นและปรับอากาศในห้องให้เอง"}
    monkeypatch.setattr(project_report, "faq",
                        lambda q: [smart_air] if "Smart Air" in q and "ทำไม" in q else [smart_home])
    monkeypatch.setattr(project_report, "search", lambda q: [])
    with _as("condo", face=True):
        story = stories.tool_result("fx_smart_home")
    air = stories.available()["fx_smart_home"]
    assert story["ok"]
    assert stories._more_facts(air, "Smart Air") == [
        "ถาม: Smart Air เป็นยังไง ตอบ: เครื่องฟอกอากาศที่วัดฝุ่นและปรับอากาศในห้องให้เอง"]
    # the ground floor plan: no label, the story's question is the search
    monkeypatch.setattr(project_report, "search", lambda q: [
        {"text": "ถาม: ทำไมชื่อ Sky Pool\nตอบ: Sky แปลว่าท้องฟ้า เป็นสระที่ยกขึ้นไปอยู่สูงเหนือโถงยิม"},
        {"text": "ถาม: ชั้น G มีอะไรบ้าง\nตอบ: นอกจากห้องออกกำลังกาย มีล็อบบี้และทางเดิน Journey to the Future ค่ะ"},
    ])
    ground = stories.available()["fx_ground"]
    assert stories.zoom_targets(ground)[0]["label_en"] == ""
    facts = stories._more_facts(ground, "")
    assert facts == ["ถาม: ชั้น G มีอะไรบ้าง ตอบ: นอกจากห้องออกกำลังกาย มีล็อบบี้และทางเดิน Journey to the Future ค่ะ"]
    # a name inside another place's name is not that place (the view "Garden"
    # took "What is the Stepped Garden like?", live 21 Oct): "Pool" is not the Sky Pool
    monkeypatch.setattr(project_report, "faq", lambda q: [
        {"text": "ถาม: Sky Pool เป็นยังไง\nตอบ: สระโค้งยื่นออกจากอาคาร มองลงไปเห็นยิมข้างล่างได้ค่ะ"}])
    monkeypatch.setattr(project_report, "search", lambda q: [])
    assert stories._more_facts(ground, "Pool") == []


def test_a_picture_with_no_label_takes_the_reports_answer_to_the_storys_own_question(approved, monkeypatch):
    """The building model, a floor plan, a room model: the report's own answer to
    one of the story's questions, and only that -- searched by the question, the
    building model got the high-speed rail ("ตึกสูง" ~ "ความเร็วสูง", 21 Oct)."""
    from app import project_report

    asked = []

    def faq(query):
        asked.append(query)
        if query == "ชั้น G มีอะไรบ้าง":
            return [{"text": "ถาม: ชั้น G มีอะไรบ้าง\nตอบ: นอกจากห้องออกกำลังกาย มีล็อบบี้และทางเดินไปยังห้องต่างๆ ค่ะ"}]
        return []

    monkeypatch.setattr(project_report, "faq", faq)
    monkeypatch.setattr(project_report, "search", lambda q: pytest.fail("the story's own answer is enough"))
    ground = stories.available()["fx_ground"]
    assert stories._more_facts(ground, "") == [
        "ถาม: ชั้น G มีอะไรบ้าง ตอบ: นอกจากห้องออกกำลังกาย มีล็อบบี้และทางเดินไปยังห้องต่างๆ ค่ะ"]
    assert asked == ["ชั้น G มีอะไรบ้าง"]


def test_a_fact_about_another_room_type_or_floor_is_not_this_pictures(approved, monkeypatch):
    """Every unit story's "Interior mood" picture took the 1-Bedroom "34 sq m"
    FAQ (live 21 Oct: on the 2-Bedroom she said "I don't have specific details
    about that"); the ground floor plan took the third floor's list. A fact
    naming a unit type or a floor the story never names -- and not the story's
    own -- is not this picture's; one naming the story's own type stays."""
    from app import project_report

    one_bedroom = {"text": 'ถาม: ที่ทีมขายพูดถึง "ห้อง 34 ตร.ม." คือห้องแบบไหน\nตอบ: คือแบบ 1-Bedroom ซึ่งขนาดที่แสดง'
                           "ส่วนใหญ่คือ 34 ตร.ม. เป็นแบบห้องเดียวที่ออกแบบภายในไฟนอลแล้ว"}
    kitchen = {"text": "ถาม: ครัวเป็นแบบเปิดหรือปิด\nตอบ: ครัวเปิดเชื่อมกับพื้นที่นั่งเล่น Studio มีเตา Hob ส่วน "
                       "1-Bedroom และ 2-Bedroom มีเตาสองหัวพร้อมเครื่องดูดควัน"}
    third = {"text": "ถาม: ชั้น 3 มีอะไรบ้าง\nตอบ: ชั้น 3 ของอาคาร A มี Social Lounge, Co-working Lounge และ "
                     "Common Sphere ค่ะ"}
    # the studio's room model (no label): the report's answers to its questions
    monkeypatch.setattr(project_report, "faq", lambda q: [one_bedroom, kitchen])
    monkeypatch.setattr(project_report, "search", lambda q: [])
    studio = stories.available()["fx_studio"]
    assert stories.zoom_targets(studio)[0]["label_en"] == ""
    facts = stories._more_facts(studio, "")
    assert len(facts) == 1 and "เตา Hob" in facts[0]
    # the ground floor plan: the third floor's list is not its fact
    monkeypatch.setattr(project_report, "faq", lambda q: [third])
    ground = stories.available()["fx_ground"]
    assert not any("ชั้น 3" in f for f in stories._more_facts(ground, ""))
    assert stories._kind_values("Pilates Studio และ Cycling Studio")["unit"] == set()


def test_a_caption_takes_only_a_passage_on_its_storys_topic(approved, monkeypatch):
    """A caption names no place, so its story's own questions are the topic: a
    passage must share a word with them that few stories' questions use. Live
    21 Oct, "Interior mood · design style" found the co-working lounge's
    target-group table by the word "mood"."""
    from app import project_report

    dtv = {"text": "หัวข้อ สิ่งที่ผู้นำเสนอกล่าว กลุ่มเป้าหมาย ฟรีแลนซ์ต่างชาติที่เข้าไทยด้วยวีซ่า DTV mood board "
                   "มุมสำหรับวิดีโอคอลและประชุมออนไลน์"}
    studio_fact = {"text": "ถาม: ห้องสตูดิโอเป็นยังไง\nตอบ: สตูดิโอเป็นห้องโล่งไม่มีผนังกั้นห้องนอน ครัวอยู่ติดทางเข้า"}
    monkeypatch.setattr(project_report, "faq", lambda q: [])
    monkeypatch.setattr(project_report, "search", lambda q: [dtv])
    studio = stories.available()["fx_studio"]
    assert stories._more_facts(studio, "Interior mood · show-unit renders") == []
    # on topic by its word "Studio" -- but it opens on another place the story
    # never names: the Pilates Studio was the Studio unit's mood fact (21 Oct)
    pilates = {"text": "ตอบ: มี Pilates Studio ที่ชั้น G อาคาร A ห้องมีขนาด 6.75 × 5.00 เมตร ตามภาพไฟนอลเป็นสตูดิโอ"
                       "โทนส้มพีช"}
    monkeypatch.setattr(project_report, "search", lambda q: [dtv, pilates, studio_fact])
    assert stories._more_facts(studio, "Interior mood · show-unit renders") == [
        "ถาม: ห้องสตูดิโอเป็นยังไง ตอบ: สตูดิโอเป็นห้องโล่งไม่มีผนังกั้นห้องนอน ครัวอยู่ติดทางเข้า"]


def test_a_portrait_or_a_caption_gets_the_plain_ask_not_the_meaning_of_a_name(approved, monkeypatch):
    """The place ask (what is inside, why the name) only when a fact's question
    names the place: an executive's portrait whose fact is "ใครเป็นผู้บริหาร"
    would otherwise be asked what the name Miki Haim means."""
    monkeypatch.setattr(stories, "_more_facts", lambda story, label: [
        "ถาม: ใครเป็นผู้บริหาร Empire Group ตอบ: คุณ Miki Haim เป็น CEO ของกลุ่มบริษัทค่ะ"])
    tap = dict(_more_tap("fx_sky_pool"), label_en="Miki Haim")
    text = stories.tap_text(tap, "th", "en")
    assert "เล่ารายละเอียดเพิ่มเติมของสิ่งในภาพนี้" in text and "ชื่อนี้มาจากอะไร" not in text
    assert "ความหมายตรงตัวของคำในชื่อ" not in text


def test_the_draft_facility_chapter_answers_the_day_it_is_pinned_and_not_before(approved, tmp_path, monkeypatch):
    """The facility-description chapter (draft 21 Oct) goes through the report's
    own gate: unpinned -- here as on the owner's PC, "_draft_..." beside the
    approved chapters -- it answers nothing; the day the owner approves and
    pins it, the tapped picture's facts come from it with no code change."""
    import hashlib

    from app import project_report

    folder = tmp_path / "report"
    folder.mkdir()
    (folder / "19_faq.md").write_text(
        "**ถาม:** Sky Pool ใหญ่เท่าไร\n**ตอบ:** กว้างประมาณ 27 เมตร ผิวน้ำประมาณ 143 ตารางเมตร ตามแบบ\n",
        encoding="utf-8")
    chapter = ("## Sky Pool\n\n**ถาม:** Sky Pool เป็นยังไง เป็นแบบไหน\n**ตอบ:** สระโค้งที่ยื่นออกจากอาคาร "
               "พื้นสระบางส่วนเป็นกระจกใส มองลงไปเห็นยิมข้างล่างได้\n\n"
               "**ถาม:** ทำไมชื่อ Sky Pool\n**ตอบ:** Sky แปลว่าท้องฟ้า "
               "เป็นสระที่อยู่สูงเหนือโถงยิม ต่างจากสระลากูนที่อยู่ระดับพื้นดิน\n")
    (folder / "_draft_21_facility_descriptions.md").write_text(chapter, encoding="utf-8")
    path = tmp_path / "approval.json"

    def pin(*names):
        path.write_text(json.dumps({
            "source_id": "project_report", "project_id": "embassy_world", "approval_status": "approved",
            "approved_by": "pete", "approved_at": "2026-10-12T09:00:00+07:00",
            "effective_at": "2026-10-12T09:00:00+07:00", "disclosure_scope": "customer",
            "content_state": "completed", "folder": str(folder), "deny_terms": [],
            "files": {n: hashlib.sha256((folder / n).read_bytes()).hexdigest() for n in names}},
            ensure_ascii=False), encoding="utf-8")
        project_report.reset()

    monkeypatch.setattr(project_report, "manifest_path", lambda project_id=None: path)
    tap = _more_tap("fx_sky_pool")
    pin("19_faq.md")
    assert stories._more_facts(tap["story"], tap["label_en"]) == []    # only its size: not "more"
    (folder / "21_facility_descriptions.md").write_text(chapter, encoding="utf-8")
    pin("19_faq.md", "21_facility_descriptions.md")
    facts = stories._more_facts(tap["story"], tap["label_en"])
    assert any("กระจกใส" in f for f in facts) and any("ท้องฟ้า" in f for f in facts)
    assert not any("27 เมตร" in f for f in facts)
    project_report.reset()


def test_the_page_opens_the_picture_it_can_see_and_keeps_the_screen_for_more():
    """The page side, read from source (a DOM test runs in record_stories
    --tap-pictures, a real click on every visible picture): the picture is the
    topmost VISIBLE one under the finger, a picture inside a row that leads to
    a story sends nothing more (the row's tap is already on its way), the large
    view moves by opacity/transform only, and a "more" tap keeps the story as
    the answer's screen."""
    engine = ENGINE.read_text(encoding="utf-8")
    body = engine[engine.index("Story.prototype.onPictureTap"):engine.index("Story.prototype.showZoom")]
    assert "elementsFromPoint" in body and "seen(n, this.root)" in body
    assert "closest('[data-tap]')" in body and "this.opts.onTap(pick._zoom.tap, true)" in body
    css = STYLE.read_text(encoding="utf-8")
    view = css[css.index(".es-root .es-zoomview {"):css.index(".es-root .es-zv-hint")]
    assert "backdrop-filter" not in view and "filter:" not in view and "infinite" not in view
    page = PAGE.read_text(encoding="utf-8")
    tap = page[page.index("onTap: function (id, keep)"):page.index("onReport:")]
    assert "if (sent && keep) { answerStaged = true; storyOwns = true; }" in tap


def test_a_tool_tap_names_the_catalogs_tool_and_arguments(approved):
    sess = _session()
    with _as("condo", face=True):
        sess._note_screen("present_story", stories.tool_result("fx_room_types"), False)
        asyncio.run(sess._quick_ask({"type": "quick_ask", "id": "story_fx_room_types_1br"}))
    assert sess.provider.sent and 'show_room_type(type="1br")' in sess.provider.sent[0]


def test_another_picture_takes_the_taps_with_it(approved):
    sess = _session()
    with _as("condo", face=True):
        sess._guest_started_turn()
        sess._note_screen("present_story", stories.tool_result("fx_ground"), False)
        # an answer picture in the same answer: the story keeps the screen
        sess._note_screen("search_condo_info", {"ok": True, "slide": {"id": "intro-041"}}, True)
        assert sess._story_taps
        # the next question's unit card replaces it
        sess._guest_started_turn()
        sess._note_screen("show_unit", {"ok": True, "screen": "unit", "unit": {}}, False)
        assert sess._story_taps == set()
        asyncio.run(sess._quick_ask({"type": "quick_ask", "id": "story_fx_ground_back"}))
    assert sess.provider.sent == []


def test_an_answer_without_a_picture_clears_the_overview_at_turn_complete(approved):
    """The page's "each answer owns the screen", mirrored for the taps."""
    from app import session as session_module
    from app.providers.base import ProviderEvent

    class WS:
        async def send_text(self, text):
            pass

        async def send_bytes(self, data):
            pass

    class Provider:
        output_sample_rate = 24000

        def __init__(self, evs):
            self.evs = evs

        async def events(self):
            for e in self.evs:
                yield e

    async def scenario():
        sess = session_module.VoiceSession(WS())
        sess._robot_face_claim = True
        sess._note_screen("present_story", stories.tool_result("fx_ground"), False)
        sess._screen_owed = False
        # her follow-up (no guest turn): the overview stays
        sess.provider = Provider([ProviderEvent(kind="assistant_transcript", text="สนใจชั้นไหนคะ"),
                                  ProviderEvent(kind="turn_complete")])
        await sess._provider_to_browser()
        assert sess._story_taps
        # a guest question answered with words only: gone
        sess._guest_started_turn()
        sess.provider = Provider([ProviderEvent(kind="assistant_transcript", text="ได้ค่ะ"),
                                  ProviderEvent(kind="turn_complete")])
        await sess._provider_to_browser()
        assert sess._story_taps == set()

    with _as("condo", face=True):
        asyncio.run(scenario())


# ---- the prompt: a line only where the tool is -----------------------------------------------

def test_the_prompt_names_present_story_only_where_it_is_offered(approved, monkeypatch):
    from app.prompts import STORIES_FACE_LINE, build_instructions
    from app.tools import stories as _registers_present_story  # noqa: F401 -- run alone, nothing else imports it

    for layout in ("v1", "v2"):
        monkeypatch.setattr(settings, "prompt_layout", layout)
        with _as("condo", face=True):
            assert STORIES_FACE_LINE in build_instructions("Embassy World", profile="condo", robot_face=True)
        with _as("condo", face=False):
            assert "present_story" not in build_instructions("Embassy World", profile="condo")
        monkeypatch.setattr(settings, "stories_enabled", False)
        with _as("condo", face=True):
            assert "present_story" not in build_instructions("Embassy World", profile="condo",
                                                             robot_face=True)
        monkeypatch.setattr(settings, "stories_enabled", True)


def test_a_broad_facility_question_is_the_overview_story_where_it_is_offered(approved, monkeypatch):
    """Owner 13 Oct: the overview names each floor's places. Live 12 Oct the
    host rule ("ห้ามไล่ชื่อ ให้ตอบประโยคเดียว") won: the broad question got the
    hook sentence and present_story was never called. Where the overview
    story is offered the rule points at it; everywhere else it is the owner's
    one sentence, unchanged."""
    from app.prompts import FACILITY_STORY_CLAUSE, build_instructions
    from app.tools import stories as _registers_present_story  # noqa: F401

    overview = dict(stories.available()["fx_facilities"], id="facilities_overview")
    real = stories.available
    for layout in ("v1", "v2"):
        monkeypatch.setattr(settings, "prompt_layout", layout)
        with _as("condo", face=True):
            monkeypatch.setattr(stories, "available", lambda catalog=None: {"facilities_overview": overview})
            text = build_instructions("Embassy World", profile="condo", robot_face=True)
            assert FACILITY_STORY_CLAUSE in text and "ห้ามไล่ชื่อ ให้ตอบประโยคเดียว" not in text
            monkeypatch.setattr(stories, "available", real)
            # approved catalog without the overview: the owner's sentence
            text = build_instructions("Embassy World", profile="condo", robot_face=True)
            assert FACILITY_STORY_CLAUSE not in text and "ห้ามไล่ชื่อ ให้ตอบประโยคเดียว" in text
        with _as("condo", face=False):
            monkeypatch.setattr(stories, "available", lambda catalog=None: {"facilities_overview": overview})
            text = build_instructions("Embassy World", profile="condo")
            assert FACILITY_STORY_CLAUSE not in text and "ห้ามไล่ชื่อ ให้ตอบประโยคเดียว" in text
            monkeypatch.setattr(stories, "available", real)


# ---- deploy ------------------------------------------------------------------------------------

def test_deploy_ships_story_pictures_only_with_the_switch_on(approved, tmp_path):
    from scripts import robot_server

    root = tmp_path / "repo"
    stories_dir = root / "data" / "projects" / "embassy_world" / "stories"
    shutil.copytree(approved, stories_dir)
    conf = root / "data" / "robot-server"
    conf.mkdir(parents=True)
    (conf / "robot.env").write_text("ANSWER_IMAGES=true\n", encoding="utf-8")
    assert robot_server.story_asset_files(root) == set()
    (conf / "robot.env").write_text("# on\nSTORIES_ENABLED=true\n", encoding="utf-8")
    files = robot_server.story_asset_files(root)
    pinned = {e["file"] for e in _catalog(approved)["assets"].values()}
    assert {Path(f).name for f in files} == pinned
    assert all(f.startswith("data/projects/embassy_world/stories/assets/") for f in files)


def test_the_story_pictures_are_gitignored_and_the_robot_turns_on_only_an_approved_catalog():
    """The robot's stories went on when the owner approved them (13 Oct, "อนุมัติ
    เลย ชื่อ pete ... ลงหุ่น"). The switch and the group come together, and a
    switched-on robot.env never ships beside a draft or an edited catalog."""
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/projects/*/stories/assets/" in ignore
    env = (ROOT / "data" / "robot-server" / "robot.env").read_text(encoding="utf-8")
    on = re.search(r"^STORIES_ENABLED=(\S+)", env, re.M)
    if not (on and on.group(1).lower() in ("1", "true", "yes", "on")):
        return
    groups = re.search(r"^TOOL_GROUPS=(.*)$", env, re.M).group(1).split(",")
    assert "stories" in [g.strip() for g in groups]
    catalog = json.loads((ROOT / "data" / "projects" / "embassy_world" / "stories" / "stories.json")
                         .read_text(encoding="utf-8"))
    assert catalog["approval_status"] == "approved" and catalog["approved_by"]
    assert catalog["approved_sha256"] == stories.content_digest(catalog), "edited after the approval"


# ---- the page ----------------------------------------------------------------------------------

def _between(text, start, end):
    return text.split(start, 1)[1].split(end, 1)[0]


def test_the_page_loads_the_engine_and_draws_a_story_only_on_the_robot_face():
    html = PAGE.read_text(encoding="utf-8")
    assert '<script src="/assets/emma-story.js?v=' in html
    assert '<link rel="stylesheet" href="/assets/emma-story.css?v=' in html
    branch = _between(html, "case 'tool_result': {", "break;")
    assert "else if (r.screen === 'story' && r.story && appShell) showStory(r.story);" in branch
    # the story wins its answer's screen over an answer picture
    assert "if ('slide' in evt && evt.slide && !storyOwns) showSlide(evt.slide);" in branch
    show = _between(html, "function showStory(story) {", "\n    }\n")
    assert "openStage();" in show and "storyOwns = true;" in show
    # a tap goes through the topic buttons' door; a tapped picture keeps the screen
    assert "var sent = sendQuickAsk(id);" in show and "return sent;" in show
    # the token only on this server's own story pictures
    assert "u.origin === location.origin && u.pathname.indexOf('/story-asset/') === 0" in show


def test_the_story_follows_her_audio_and_never_hears_the_guest():
    html = PAGE.read_text(encoding="utf-8")
    assert "storyHook('audio', at, buf.duration);" in _between(html, "function playChunk(arrayBuffer) {", "src.onended")
    assert "storyHook('transcript', evt.text || '');" in _between(html, "case 'assistant_transcript':", "break;")
    assert "storyHook('turnComplete');" in _between(html, "case 'turn_complete':", "break;")
    started = _between(html, "case 'speech_started':", "break;")
    assert "storyHook('settle');" in started and "storyOwns = false;" in started
    assert "storyHook('settle');" in _between(html, "function stopPlayback() {", "\n    }\n")
    # the guest's words: never fed to the story, never in the engine
    assert "storyHook" not in _between(html, "case 'user_transcript':", "break;")
    engine = ENGINE.read_text(encoding="utf-8")
    for word in ("user_transcript", "curUser", "userLine", "innerHTML"):
        assert word not in engine, word
    # any other picture, or a clear, ends the story and frees it
    assert "endStory();" in _between(html, "function openStage() {", "}")
    assert "endStory();" in _between(html, "function clearStage() {", "\n    }\n")


def test_the_engine_stays_light_on_the_robot():
    """Measured on this page (10 Oct): blur/backdrop-filter/drifting layers/
    idle infinite animations took the robot face from 30 to 14 fps."""
    css = STYLE.read_text(encoding="utf-8")
    js = ENGINE.read_text(encoding="utf-8")
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for banned in ("backdrop-filter", "filter:", "blur(", "clip-path", "infinite", "will-change"):
        assert banned not in body, banned
    for decl in re.findall(r"transition\s*:\s*([^;}]+)", body):
        for part in re.split(r",(?![^(]*\))", decl):          # commas outside cubic-bezier(...)
            prop = part.strip().split()[0]
            assert prop in ("transform", "opacity", "none"), decl
    for frames in re.findall(r"@keyframes[^{]+\{((?:[^{}]*\{[^}]*\})+)", body):
        for prop in re.findall(r"([a-z-]+)\s*:", frames):
            assert prop in ("transform", "opacity"), prop
    # No blurred shadow at all, even a still one: it widens every layer that
    # moves (21 Oct: p95 33 -> 17 ms when the room-type cards' shadows came
    # off). Review 26 Oct: blurred text-shadows came back on the map labels,
    # inside the sliding map, past a check that only banned "blur(". A shadow
    # is offset-x offset-y [blur [spread]] colour; its third length is the blur.
    colour = r"(?:rgba?|hsla?)\((?:[^()]|\([^()]*\))*\)|#[0-9a-fA-F]{3,8}\b"
    number = r"-?(?:\d+\.?\d*|\.\d+)"

    def blurred(value):
        if value.strip() == "none":
            return False
        for shadow in re.split(r",(?![^(]*\))", value):
            bare = re.sub(colour, " ", shadow)
            if "(" in bare:                              # a var()/calc() blur cannot be judged
                return True
            lengths = [t for t in bare.split() if re.fullmatch(number + r"[a-z%]*", t)]
            if len(lengths) >= 3 and float(re.match(number, lengths[2]).group()) != 0:
                return True
        return False

    assert blurred("0 0 .5vw #161b47, 0 0 1vw #161b47") and blurred("0 1px 2px rgba(0, 0, 0, .4)")
    assert blurred(".2vw 0 #10143a, 0 0 .6vw rgb(var(--es-teal))") and blurred("0 0 var(--b) #000")
    assert not blurred(".2vw 0 #161b47, -.2vw 0 #161b47") and not blurred("none")
    assert not blurred("0 0 0 2px rgba(255, 255, 255, .3)") and not blurred("inset 0 -.3vw 0 #000")
    for prop, value in re.findall(r"(?<![\w-])(box-shadow|text-shadow)\s*:\s*([^;}]+)", body):
        assert not blurred(value), (prop, value)
    # the script never animates anything but transform/opacity either
    for banned in (".style.transition", ".style.animation", "boxShadow", ".style.filter", "backdropFilter",
                   "textShadow", "box-shadow", "text-shadow"):
        assert banned not in js, banned
    assert "@keyframes" not in js
    # no ring behind or under Emma: rings only live on a picture (.es-spot in a box)
    assert ".mascot-wrap" not in body.replace("html.app-shell body.emma-story .mascot-wrap { width:", "")


# The harness sleeps through every template's own timers (16.5 s for eleven
# payloads, 21 Oct); with journey's rings view it ran past the suite's 20 s
# cap. Its node call already allows 60 s.
@pytest.mark.timeout(60)
def test_the_clock_and_every_template_in_node(approved, tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to run the story clock and templates -- they are NOT being checked")
    from scripts.story_fixture import VIEW_STORY

    catalog = _catalog(approved)
    payloads = [stories.page_payload(s, catalog) for s in stories.available().values()]
    # every template, and every template's other view (journey's rings), drawn
    assert set(p["template"] for p in payloads) == set(stories.TEMPLATES)
    views = {(p["template"], p["params"].get("view")) for p in payloads if p["params"].get("view")}
    assert views == set(VIEW_STORY)
    path = tmp_path / "payloads.json"
    path.write_text(json.dumps(payloads, ensure_ascii=False), encoding="utf-8")
    result = subprocess.run([node, "tests/client_story_clock.cjs", str(path)], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "%d templates PASS" % len(payloads) in result.stdout


def test_the_harness_timeline_is_one_answer_in_order(approved):
    from scripts import record_stories

    catalog = _catalog(approved)
    story = stories.available()["fx_ground"]
    events, lines = record_stories.timeline(story, stories.page_payload(story, catalog), "th", 14.0, 0.15)
    assert [e[0] for e in events] == sorted(e[0] for e in events)
    kinds = [e[2].get("type") for e in events if e[1] == "json"]
    assert kinds[0] == "speech_started" and kinds[-1] == "turn_complete" and "tool_result" in kinds
    said = "".join(e[2]["text"] for e in events if e[1] == "json" and e[2].get("type") == "assistant_transcript")
    assert stories._NORM.sub("", said) == stories._NORM.sub("", "".join(l["th"] for l in story["lines"]))
    assert [l["line"] for l in lines] == list(range(len(story["lines"])))
    assert all(b["start"] < a["start"] for b, a in zip(lines, lines[1:]))
    # Gemini's order by default (live 12 Oct: every answer opens with text,
    # never two pieces of transcript without audio between them)
    flow = [("text" if e[1] == "json" else "audio") for e in events
            if e[1] == "audio" or e[2].get("type") == "assistant_transcript"]
    assert flow[0] == "text"
    assert not any(a == b == "text" for a, b in zip(flow, flow[1:]))
    trail, _ = record_stories.timeline(story, stories.page_payload(story, catalog), "th", 14.0, 0.15, "trail")
    first = next(i for i, e in enumerate(trail) if e[1] == "audio")
    assert all(not (e[1] == "json" and e[2].get("type") == "assistant_transcript") for e in trail[:first])
    """Elsewhere a tap is a chip on the overview: a card that claimed a tap
    the template never draws would leave the guest nothing to press. A drill
    card may (review 12 Oct: the Sky Pool card on the level 3 plan led
    nowhere while a story of its own existed)."""
    catalog = _catalog(approved)
    story = json.loads(json.dumps(stories.available()["fx_ground"]))
    story["params"]["items"][0]["tap"] = "story_fx_ground_back"
    payload = stories.page_payload(story, catalog)
    assert payload["params"]["items"][0]["tap"] == "story_fx_ground_back"
    assert [t["id"] for t in payload["taps"]] == ["story_fx_ground_back"]
    feats = json.loads(json.dumps(stories.available()["fx_smart_home"]))
    feats["taps"] = [{"id": "story_fx_smart_home_back", "label_en": "Back", "label_th": "กลับ",
                      "next_story": "fx_ground", "tool": None}]
    feats["params"]["items"][0]["tap"] = "story_fx_smart_home_back"
    payload = stories.page_payload(feats, catalog)
    assert "tap" not in payload["params"]["items"][0]
    assert [t["id"] for t in payload["taps"]] == ["story_fx_smart_home_back"]
    slabs = stories.page_payload(stories.available()["fx_facilities"], catalog)
    assert slabs["params"]["floors"][0]["tap"] == "story_fx_facilities_g"      # bound by next_story
    assert "next_story" not in json.dumps(slabs["params"])


def test_the_switch_alone_closes_the_catalog(approved, monkeypatch):
    assert stories.offered() is True
    monkeypatch.setattr(settings, "stories_enabled", False)
    assert stories.offered() is False
    assert stories.tool_result("fx_tower")["ok"] is False


def test_a_card_that_is_a_button_stretches_its_photo_on_the_robots_webview():
    """13 Oct, the robot's own screen: the floor card (a <button> when it opens
    a story) showed no photo. Chrome 124 -- the robot's WebView -- defaults
    buttons to align-items: flex-start, so the photo box in the column was
    0 px wide; the PC's newer Chromium has no such default and looked right.
    Every story button laid out as a flex column says how it aligns."""
    css = STYLE.read_text(encoding="utf-8")
    block = re.search(r"\.es-fc \{([^}]*)\}", css).group(1)
    assert "flex-direction: column" in block and "align-items: stretch" in block
    # and its pictures beat the stage's own `.vp-stage img { contain }`
    assert ".es-root .es-fc-ph img" in css and ".es-root .es-thumb img" in css


def test_every_icon_a_design_names_is_drawn_by_the_engine():
    """13 Oct: nearby_places named "hospital" and "school", which the page
    did not draw -- an unknown icon falls back to the house, the very mark of
    the project on the same screen. Every icon in every design file must be
    one the engine has."""
    engine = ENGINE.read_text(encoding="utf-8")
    block = engine[engine.index("var ICONS = {"):]
    block = block[:block.index("};")]
    drawn = set(re.findall(r"^\s*([a-z_]+):\s*'", block, re.M))
    # food / sight: the nearby restaurants and attractions stories (21 Oct)
    assert {"home", "hospital", "school", "food", "sight"} <= drawn
    named = set()

    def walk(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if k == "icon" and isinstance(v, str):
                    named.add(v)
                else:
                    walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    for path in sorted((stories.folder() / "design").glob("*.json")):
        walk(json.loads(path.read_text(encoding="utf-8")))
    assert named, "no design names an icon -- the walk found nothing to check"
    assert named <= drawn, sorted(named - drawn)
    # the rings view draws its own two: by car (every time) and on foot
    assert {"shop", "car", "walk"} <= drawn


def _design_stories():
    for path in sorted((stories.folder() / "design").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("stories"), list):
            data = data["stories"]
        yield from (data if isinstance(data, list) else [data])


def test_the_nearby_rings_say_drive_time_and_never_a_direction():
    """Owner 21 Oct: the nearby stories (7-Eleven, hospitals, schools,
    restaurants, places to visit) had no motion that read as "what is around
    us". They are drawn now as rings of drive time round the project's pin
    (journey, `view: "rings"`). The approved figures are drive times with no
    bearing, so a direction must never be implied: the engine spreads the
    places evenly and draws no compass there, and every such story says on
    screen that it is not a map. A place's minutes (`bar`) put it on the
    scale; a walk's own line exists and comes no earlier than its place."""
    engine = ENGINE.read_text(encoding="utf-8")
    rings = engine[engine.index("  function aroundRings(S, p) {"):engine.index("  function discBlocks(")]
    assert "'es-nb-n'" not in rings and "'N'" not in rings, "a compass on the drive-time rings"
    assert "if (p.view === 'rings') return aroundRings(S, p);" in engine
    told = {}
    for story in _design_stories():
        params = story.get("params") or {}
        if params.get("view") != "rings":
            continue
        sid = story["id"]
        told[sid] = story
        assert story["template"] == "journey", sid
        assert "not a map" in params.get("footnote_en", ""), sid
        lines = len(story["lines"])
        for row in params["rows"]:
            assert isinstance(row.get("bar"), list) and row["bar"], (sid, row["label_en"])
            assert 0 <= row["bar"][0] <= params["bar_max"], (sid, row["label_en"])
            assert 0 <= row["line"] < lines, (sid, row["label_en"])
            walk = row.get("walk")
            if walk is not None:
                assert walk.get("value_en"), (sid, row["label_en"])
                if "line" in walk:
                    assert row["line"] <= walk["line"] < lines, (sid, row["label_en"])
    assert {"nearby_places", "nearby_7eleven", "nearby_hospitals", "nearby_schools", "nearby_restaurants",
            "nearby_attractions"} <= set(told), sorted(told)


def test_a_story_report_is_logged_as_numbers_and_the_id_only(monkeypatch):
    """The page reports each story once (emma-story.js Story.report): when
    every beat went up and where its cue word fell in her voice, and the
    frame times. The reviewer (13 Oct) could not judge sync or smoothness
    from silent recordings; real conversations now leave the numbers. Text
    smuggled into the report never reaches the log; a malformed one writes
    nothing."""
    from app import session as session_module
    from app import turnlog

    rows = []
    monkeypatch.setattr(turnlog, "record", lambda event, **f: rows.append((event, f)))
    sess = session_module.VoiceSession(None, provider_name="gemini", profile="condo")
    sess._story_report({"id": "pools_all", "interrupted": False, "audio_s": 21.4,
                        "beats": [{"at": 0.1, "cue": 0.0}, {"at": 3.52, "cue": 3.1, "said": "ลูกค้าพูดว่า"},
                                  {"at": None, "cue": None}, "nonsense"],
                        "frames": 1290, "fps": 59.6, "p95_ms": 16.8, "long_frames": 2,
                        "heard": "เบอร์ 081"})
    assert len(rows) == 1
    event, row = rows[0]
    assert event == "story_report" and row["topic"] == "pools_all"
    assert row["at_s"] == [0.1, 3.52, None, None] and row["cue_s"] == [0.0, 3.1, None, None]
    assert row["lag_ms"] == [100, 420, None, None]
    assert row["fps"] == 59.6 and row["p95_ms"] == 16.8 and row["long_frames"] == 2 and row["frames"] == 1290
    assert "081" not in repr(row) and "ลูกค้า" not in repr(row)
    for bad in (None, "x", {"id": "Pools All!", "beats": []}, {"id": "pools_all", "beats": "x"},
                {"id": "a" * 65, "beats": []}):
        sess._story_report(bad)
    assert len(rows) == 1
    sess._story_report({"id": "pools_all", "beats": [{"at": float("nan"), "cue": 1e9}], "fps": True})
    assert rows[-1][1]["at_s"] == [None] and rows[-1][1]["cue_s"] == [None] and rows[-1][1]["fps"] is None
    # a JSON integer past 1e308 overflows float(): dropped, the call goes on (review 13 Oct)
    sess._story_report({"id": "pools_all", "beats": [{"at": 10 ** 400, "cue": 1}], "frames": 10 ** 400})
    assert rows[-1][1]["at_s"] == [None] and rows[-1][1]["frames"] is None


def test_a_card_or_slab_that_is_not_shown_takes_no_tap_and_the_big_card_swallows_one():
    """Review 13 Oct, hit-tested in Chromium: hidden floor cards are only
    transparent, so the LAST card in the DOM took every tap ("›" on Fitness
    opened the kids' room); the stack's big card let a tap through to the
    small rows under it (STUDIO sent the 1-bedroom)."""
    css = STYLE.read_text(encoding="utf-8")
    assert ".es-root .es-fc:not(.on), .es-root .es-slab:not(.on) { pointer-events: none; }" in css
    assert ".es-root .es-big { pointer-events: auto; }" in css
