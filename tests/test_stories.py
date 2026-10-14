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
        "fx_nearby", "fx_units_total", "fx_smart_home", "fx_travel", "fx_where"}
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
                         ("Is there EV charging?", "parking"), ("hospital nearby", "nearby_places"),
                         ("school nearby", "nearby_places"), ("Is there a hospital nearby?", "nearby_places"),
                         ("how many units", "unit_count"), ("ที่จอดรถมีกี่คัน", "parking")):
        assert stories.story_for_query(query, offered) == topic, query
    for query in ("แนะนำโครงการหน่อย", "parking fee", "ค่าส่วนกลาง", "pet policy", "", None):
        assert stories.story_for_query(query, offered) is None, query


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


def test_no_page_payload_carries_a_price_notes_or_the_guests_questions(approved):
    catalog = _catalog(approved)
    for story in stories.available().values():
        payload = stories.page_payload(story, catalog)
        text = json.dumps(payload, ensure_ascii=False)
        assert not stories._MONEY.search(text), story["id"]
        for key in ("notes_th", "sources", "questions_th", "questions_en"):
            assert '"%s"' % key not in text, (story["id"], key)
        assert set(payload) == {"id", "template", "beats", "params", "taps"}   # her lines: the model's


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
        assert sess._story_taps == {"story_fx_ground_back"}
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
    assert "onTap: function (id) { return sendQuickAsk(id); }" in show
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
    # the script never animates anything but transform/opacity either
    for banned in (".style.transition", ".style.animation", "boxShadow", ".style.filter", "backdropFilter"):
        assert banned not in js, banned
    assert "@keyframes" not in js
    # no ring behind or under Emma: rings only live on a picture (.es-spot in a box)
    assert ".mascot-wrap" not in body.replace("html.app-shell body.emma-story .mascot-wrap { width:", "")


def test_the_clock_and_every_template_in_node(approved, tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to run the story clock and templates -- they are NOT being checked")
    catalog = _catalog(approved)
    payloads = [stories.page_payload(s, catalog) for s in stories.available().values()]
    assert sorted(p["template"] for p in payloads) == sorted(stories.TEMPLATES)
    path = tmp_path / "payloads.json"
    path.write_text(json.dumps(payloads, ensure_ascii=False), encoding="utf-8")
    result = subprocess.run([node, "tests/client_story_clock.cjs", str(path)], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "%d templates PASS" % len(stories.TEMPLATES) in result.stdout


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
    assert {"home", "hospital", "school"} <= drawn
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
