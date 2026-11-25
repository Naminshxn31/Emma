"""The ceremony film's ACTS (owner 17 Nov 2026 evening: "แยกเอเจนท์สำหรับออกแบบแต่ละส่วน แล้วก็ให้ทำ
ลองทำมาแล้วค่อยปรับ"): each part of the show is one JavaScript file on a shared core.

  * app/ceremony_slides.py -- the generic content model of an act's slide (no per-act code),
    the ceremony's own files (full-size pictures, depth maps, candidates, clips), the
    signature that covers them;
  * client/assets/ceremony-show.js -- the act loader, `ctx` and its factories, the gate
    (a beat's `key` = hidden until that beat), the shared primitives;
  * client/assets/ceremony-show/acts/*.js|css -- the acts themselves: a source scan (words
    reach the screen only through the factories) and a run of every act with every slide
    that names it in a real browser page;
  * docs/emma-ceremony-slides.md section 0 -- the contract the designers build on.

For a designer: `python -m pytest tests/test_ceremony_acts.py -q` must be green with your
act and slides in place; a failure names your file, the line and the rule.
"""
import copy
import hashlib
import importlib.util
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import ceremony_slides as cs
from app import event_script
from app.config import settings

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "client" / "assets" / "ceremony-show.js"
STYLE = ROOT / "client" / "assets" / "ceremony-show.css"
ACTS = ROOT / "client" / "assets" / "ceremony-show" / "acts"
SLIDES_DIR = ROOT / "data" / "ceremony" / "slides"
GUIDE = ROOT / "docs" / "emma-ceremony-slides.md"


@pytest.fixture
def real(monkeypatch):
    """The real ceremony, its slides and its files (conftest empties CEREMONY_FILE)."""
    monkeypatch.setattr(settings, "ceremony_file", "data/ceremony/floor1_ceremony.json")
    monkeypatch.setattr(settings, "stories_enabled", True)
    event_script.reset()
    cs.reset()
    yield cs.load()
    event_script.reset()
    cs.reset()


@pytest.fixture
def example(real, monkeypatch):
    """The real ceremony with the worked example as the slide of s04_l11, unsigned --
    whatever that cue's own file holds by now."""
    doc = {**real, "approved_by": None, "slides": {**real["slides"], "s04_l11": _slide()}}
    monkeypatch.setattr(cs, "_doc", lambda: doc)
    return doc


def _slide(**changes) -> dict:
    """The worked example -- the slide the guide shows in 0.2, for the line of s04_l11
    ("Swimming in the 155-metre pool myself!") -- with changes; None removes a field.
    (Not the per-cue file: that cue's designer replaces it.)"""
    slide = json.loads(re.search(r"```json\n(.*?)```", GUIDE.read_text(encoding="utf-8"), flags=re.S).group(1))
    for key, value in changes.items():
        if value is None:
            slide.pop(key, None)
        else:
            slide[key] = value
    return slide


#: Lines the bad cases are built against (the real cues' own words).
DEEP = "s06_l03"       # "... The project's main swimming pool is 1.3 metres deep. The building is 8 storeys high,
#                         the roof level is about 23 metres above road level, and the sales team estimates the
#                         indoor facilities at around 2,500 square metres in total."
JOKE = "s06_l07"       # "If there's ever an Embassy World superfan quiz... I'm sure I'd take first place!"
BEACH = "s03_l03"      # "Of course. Embassy World is in the Jomtien Second Road area, about 600 metres from
#                         Jomtien Beach in a straight line. Around the project you'll find ..."
AREAS = "s04_l06"      # "Our main common areas are in Building A: ... A real highlight is the swimming pool, a
#                         full 155 metres long. There are several more places to relax, too. ..."


def _act(cue_id: str, **fields) -> list[str]:
    slide = {"template": "act", "act": "example_panel"}
    slide.update(fields)
    return cs.problems(slide, event_script.cue(cue_id))


# ---- the worked example and the guide ----------------------------------------------------------

def test_the_worked_example_in_the_guide_is_a_slide_that_passes_and_its_act_is_in_the_folder(real):
    slide = _slide()
    assert slide["template"] == "act" and slide["act"] == "example_panel"
    assert cs.problems(slide, event_script.cue("s04_l11")) == []
    assert (ACTS / "example_panel.js").is_file() and (ACTS / "example_panel.css").is_file()
    guide = GUIDE.read_text(encoding="utf-8")
    assert guide.index("## 0. Acts") < guide.index("## 1. The line you illustrate")      # the contract comes first
    for word in ("ctx.text", "ctx.figure", "ctx.picture", "ctx.video", "ctx.opt", "ctx.after", "ctx.frame", "ctx.bg",
                 "fx.glass", "fx.console", "fx.sat", "fx.fan", "fx.plane", "fx.route", "fx.arc", "fx.camera", "fx.inset",
                 "fx.whip", "fx.typed", "fx.parallax", "fx.wipe", "fx.wheel", "ctx.light", "ctx.prev", "handover",
                 "cs-type-giant", "cs-lit", "cs-whipped", "words_before_she_says_them", "/ceremony-asset/"):
        assert word in guide, word


def test_the_page_gets_an_act_slide_rebuilt_field_by_field_with_its_files_addresses(example):
    out = cs.for_cue("s04_l11", drafts=True)
    act = cs.acts()["example_panel"]
    assert out["template"] == "act" and out["act"] == "example_panel" and out["v"] == act["v"] and out["css"] is True
    assert out["texts"] == {"kicker": "Embassy World", "line": "Swimming in the pool myself!"}
    assert out["figures"] == {"length": {"value": "155", "unit": "metre pool"}}
    pool = out["pictures"]["pool"]
    assert re.fullmatch(r"/story-asset/[0-9a-f]{64}", pool["src"]) and (pool["w"], pool["h"]) == (1080, 810)
    # the same picture at its source size, from the ceremony's own index -- by hash, never a path
    assert re.fullmatch(r"/ceremony-asset/[0-9a-f]{64}", pool["hires"]) and pool["hw"] > pool["w"]
    assert out["beats"] == [{"at": 0.4211, "do": "figure", "key": "length"}]
    assert out["options"]["panel"] == {"x": 46, "y": 5, "w": 49} and out["videos"] == {}
    assert "file" not in json.dumps(out) and "lagoon_scene58" not in json.dumps(out)       # no id, no file name
    # not signed = the robot's server sends none of it
    assert cs.for_cue("s04_l11") is None
    # a beat's own numbers travel with it; its place is resolved from her words
    slide = _slide(beats=[{"at_en": "155-metre", "do": "figure", "key": ["length"], "k": 1.2, "to": "pool", "on": True}])
    beat = cs.page_payload(slide, event_script.cue("s04_l11"))["beats"][0]
    assert beat == {"at": 0.4211, "do": "figure", "key": ["length"], "k": 1.2, "to": "pool", "on": True}


def test_the_acts_folder_is_the_list_and_an_acts_tag_moves_with_its_bytes(real, tmp_path, monkeypatch):
    assert "example_panel" in cs.acts()
    # (+ the debut's sound and video, asked for when the screen opens: tests/test_ceremony_reveal.py)
    assert set(cs.acts_manifest()) <= {"acts", "idle", "reveal"} and cs.acts_manifest()["acts"]["example_panel"]["css"] is True
    folder = tmp_path / "acts"
    folder.mkdir()
    (folder / "one.js").write_text("CeremonyShow.register('one', function () {});", encoding="utf-8")
    (folder / "Bad-Name.js").write_text("x", encoding="utf-8")
    (folder / "notes.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(cs, "acts_dir", lambda: folder)
    got = cs.acts()
    assert set(got) == {"one"} and got["one"]["css"] is False and got["one"]["css_sha256"] is None
    before = got["one"]["v"]
    (folder / "one.css").write_text(".cs-act-one { opacity: 1; }", encoding="utf-8")
    assert cs.acts()["one"]["css"] is True and cs.acts()["one"]["v"] != before        # its stylesheet is part of it
    assert "unknown_act:example_panel" in _act("s04_l11", texts={"line": "Swimming"})
    assert _act("s04_l11", act="one", texts={"line": "Swimming"}) == []
    assert "bad_act" in _act("s04_l11", act="../one") and "bad_act" in _act("s04_l11", act=7)


# ---- the generic content model: every rule of the templates, with no per-act code --------------

def test_an_acts_words_obey_every_rule_of_the_templates(real):
    def text(words: str) -> list[str]:
        return _act(AREAS, texts={"t": words})

    assert text("Main common areas in Building A") == []
    assert "word_not_in_line:sauna" in text("Main common areas and sauna")
    assert "number_not_in_line:156" in text("Our main common areas 156")
    assert "banned:price" in text("Our main price") and "banned:roof" in text("Our roof")
    assert "symbol_on_slide" in text("Our main + common areas") and "thai_on_slide" in text("Our main สระ")
    assert "bad_text:texts.t" in text("Our main " * 12) and "bad_text:texts.t" in text("  ")
    assert "number_not_with_its_words:3" in text("Our 3")                # a number stays beside its own word
    assert text("Floor 3") == []
    # unknown fields, keys and shapes
    assert "unknown_field:title" in _act(AREAS, title="Our main common areas")
    assert "bad_key:texts.Title" in _act(AREAS, texts={"Title": "Our main"})
    assert "bad_map:texts" in _act(AREAS, texts=["Our main"])
    assert "bad_map:texts" in _act(AREAS, texts={"t%d" % i: "Our" for i in range(25)})
    assert "key_twice:a" in _act(AREAS, texts={"a": "Our main"}, pictures={"a": {"asset": "liv_trees"}})
    assert "unknown_field:pictures.p.zoom" in _act(AREAS, pictures={"p": {"asset": "liv_trees", "zoom": 2}})
    assert "missing:figures.f[0].value" in _act(AREAS, figures={"f": {"unit": "metres long"}})


def test_a_figure_is_one_number_with_its_own_words_and_the_owners_qualifiers(real):
    def figure(**fields) -> list[str]:
        beat = [{"at_en": fields.pop("at"), "do": "show", "key": "f"}] if "at" in fields else []
        return _act(fields.pop("cue", DEEP), figures={"f": fields}, beats=beat)

    assert figure(value="1.3", unit="metres deep", label="Main swimming pool", at="main swimming pool") == []
    assert figure(lead="about", value="23", unit="metres above road level", label="Roof level", at="the roof level") == []
    assert figure(lead="around", value="2,500", unit="square metres", label="The sales team estimates", at="the sales team") == []
    # never a bare number
    assert "bare_figure:f" in figure(value="8", label="storeys high", at="The building is")
    # the number beside ITS unit and ITS lead -- every word below is in the line somewhere
    assert "number_not_with_its_words:8" in figure(value="8", unit="metres deep", at="The building is")
    assert "number_not_with_its_words:1.3" in figure(lead="about", value="1.3", unit="metres deep", at="main swimming pool")
    assert "not_a_number:figures.f[0].value" in figure(value="eight", unit="storeys", at="The building is")
    # the owner's qualifiers stay INSIDE the figure
    assert "number_lost_its_qualifier:23:about" in figure(value="23", unit="metres above road level", at="the roof level")
    assert "number_lost_its_qualifier:2500:estimate" in figure(lead="around", value="2,500", unit="square metres", at="the sales team")
    assert "number_lost_its_qualifier:600:straight line" in figure(cue=BEACH, lead="about", value="600", unit="metres", at="about 600")
    assert figure(cue=BEACH, lead="about", value="600", unit="metres", label="in a straight line", at="about 600") == []
    # a qualifier elsewhere on the slide does not count
    assert "number_lost_its_qualifier:600:straight line" in _act(
        BEACH, figures={"f": {"lead": "about", "value": "600", "unit": "metres"}}, texts={"t": "In a straight line"},
        beats=[{"at_en": "about 600", "do": "show", "key": ["f", "t"]}])
    # a label is made of the words of the sentence its number is said in
    assert "label_not_with_its_number:1.3:storeys" in figure(value="1.3", unit="metres deep", label="Storeys high",
                                                             at="the sales team")
    # the roof only as a height
    assert "banned:roof" in figure(lead="about", value="23", unit="metres above road level", label="Roof level pool",
                                   at="the roof level")


def test_nothing_is_on_the_screen_before_she_has_reached_its_words(real):
    """A beat's `key` = "this comes on the screen here" (the engine hides it until then), so a
    text, figure or caption may only use what she has said by the end of THAT sentence; with
    no beat it is up from the start and may only use the first sentence."""
    punch = {"punch": "First place"}
    assert "words_before_she_says_them:punch:first" in _act(JOKE, texts=punch)              # the opening: the set-up
    assert "words_before_she_says_them:punch:place" in _act(JOKE, texts=punch, beats=[{"at_en": "If there's", "do": "x", "key": "punch"}])
    assert _act(JOKE, texts=punch, beats=[{"at_en": "I'm sure", "do": "x", "key": "punch"}]) == []
    assert _act(JOKE, texts={"setup": "Embassy World superfan quiz"}) == []                 # the first sentence's own words
    # a figure before its number
    early = _act(AREAS, figures={"pool": {"value": "155", "unit": "metres long"}})
    assert "words_before_she_says_them:pool:155" in early and "words_before_she_says_them:pool:long" in early
    assert _act(AREAS, figures={"pool": {"value": "155", "unit": "metres long"}},
                beats=[{"at_en": "A real highlight", "do": "x", "key": "pool"}]) == []
    # the FIRST beat that names a key is when it appears; a later one changes nothing
    twice = [{"at_en": "Our main", "do": "a", "key": "pool"}, {"at_en": "A real highlight", "do": "b", "key": "pool"}]
    assert "words_before_she_says_them:pool:155" in _act(AREAS, figures={"pool": {"value": "155", "unit": "metres long"}}, beats=twice)
    # words she has ALREADY said are fine later; "to" points at something without bringing it in
    assert _act(AREAS, texts={"t": "Building A"}, beats=[{"at_en": "There are several", "do": "x", "key": "t"}]) == []
    assert "words_before_she_says_them:t:relax" in _act(AREAS, texts={"t": "Places to relax"},
                                                        beats=[{"at_en": "A real highlight", "do": "x", "to": "t"}])
    # a picture's caption comes with its picture
    pool = {"p": {"asset": "lagoon_scene58", "caption": "The swimming pool"}}
    assert "words_before_she_says_them:p:swimming" in _act(AREAS, pictures=pool, beats=[{"at_en": "Our main", "do": "x", "key": "p"}])


def test_a_picture_is_backed_where_it_appears_as_on_the_templates(example):
    pool = {"p": {"asset": "lagoon_scene58"}}
    at = lambda piece: [{"at_en": piece, "do": "x", "key": "p"}]          # noqa: E731
    assert _act(AREAS, pictures=pool, beats=at("A real highlight")) == []
    assert "asset_not_near_its_beat:lagoon_scene58" in _act(AREAS, pictures=pool, beats=at("There are several"))
    assert _act(AREAS, pictures=pool) == []                               # the opening: backed by the line
    assert "asset_not_in_line:lagoon_scene58" in _act(JOKE, pictures=pool)
    assert _act(JOKE, pictures={"p": {"asset": "liv_trees"}}) == []       # an establishing picture may open any cue
    assert "asset_not_near_its_beat:liv_trees" in _act(JOKE, pictures={"p": {"asset": "liv_trees"}}, beats=at("I'm sure"))
    captioned = {"p": {"asset": "lagoon_scene58", "caption": "Building A"}}
    assert "caption_not_of_its_picture:lagoon_scene58" in _act(AREAS, pictures=captioned)
    assert "unknown_asset:no_such_picture" in _act(AREAS, pictures={"p": {"asset": "no_such_picture"}})
    assert "asset_excluded:plan_ground_site" in _act(AREAS, pictures={"p": {"asset": "plan_ground_site"}})
    assert "bad_choice:pictures.p[0].fit" in _act(AREAS, pictures={"p": {"asset": "lagoon_scene58", "fit": "stretch"}})
    assert "not_a_video:lagoon_scene58" in _act(AREAS, videos={"v": {"asset": "lagoon_scene58"}})
    # pictures need the stories' pictures served on this machine
    assert cs.for_cue("s04_l11", drafts=True) is not None
    settings.stories_enabled = False
    cs.reset()
    assert cs.for_cue("s04_l11", drafts=True) is None


def test_options_and_a_beats_own_fields_can_hold_no_words(real):
    ok = {"panel": {"x": 46.5, "y": -5, "on": True}, "route": [[1, 2], [3.5, 4]], "mode": "night", "deep": {"a": {"b": {"c": [1]}}}}      # five containers deep: the most
    assert _act(AREAS, options=ok) == []
    for bad in ({"title": "Sky Pool"}, {"title": "two words"}, {"t": ["ok", "Not ok"]}, {"Title": 1}, {"a": None},
                {"a": float("nan")}, {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}, {"a": {"B b": 1}}):
        assert any(p.startswith("bad_options") for p in _act(AREAS, options=bad)), bad
    assert "bad_options" in _act(AREAS, options=["a"])
    assert "options_too_big" in _act(AREAS, options={"p": [[i, i] for i in range(390)], "q": [[i, i] for i in range(390)]})
    beat = lambda **fields: _act(AREAS, texts={"t": "Our main"}, beats=[{"at": 0.1, "do": "show", **fields}])      # noqa: E731
    assert beat(key="t", k=1.2, to="t", rect=[1, 2, 3, 4]) == []
    assert "bad_beat_field:0.say" in beat(say="Sky Pool is here") and "bad_beat_field:0.Say" in beat(Say=1)
    assert "bad_beat_field:0.deep" in beat(deep={"a": {"b": [1]}})
    assert "beat_shows_nothing:nope" in beat(key="nope") and "bad_beat_key:0" in beat(key=7)
    assert "bad_beat_do:0" in _act(AREAS, beats=[{"at": 0.1, "do": "Show me"}]) and "bad_beat_do:0" in _act(AREAS, beats=[{"at": 0.1}])
    assert "beat_has_no_place:0:show" in _act(AREAS, beats=[{"do": "show"}])
    assert "beat_not_in_line:en:0:show" in _act(AREAS, beats=[{"do": "show", "at_en": "a sauna"}])
    assert "beats_out_of_order:en:1:b" in _act(AREAS, beats=[{"do": "a", "at_en": "A real highlight"}, {"do": "b", "at_en": "Our main"}])
    assert "bad_beat_at:0:show" in _act(AREAS, beats=[{"do": "show", "at": 1.5}])
    assert "too_many_beats" in _act(AREAS, beats=[{"do": "x", "at": 0.5}] * 41) and "bad_beats" in _act(AREAS, beats={"do": "x"})
    # ... and the page gets a copy of the options, not anything else that was in the file
    assert cs.page_payload(_slide(options=ok), event_script.cue("s04_l11"))["options"] == ok


# ---- the ceremony's own files: full size, depth, candidates, clips -----------------------------

PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000" "1f15c489" "0000000d49444154789c6360000002000001" "e221bc33" "0000000049454e44ae426082")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def index(example, tmp_path, monkeypatch):
    """A ceremony assets folder a test owns: write(rows) sets its index.json."""
    folder = tmp_path / "assets"
    folder.mkdir()
    monkeypatch.setattr(cs, "index_dir", lambda: folder)
    serial = [0]

    def write(rows: list[dict]) -> Path:
        serial[0] += 1                                   # (a new size every time: the stat cache sees it)
        (folder / "index.json").write_text(json.dumps(rows) + " " * serial[0], encoding="utf-8")
        return folder

    write([])
    return write


def _candidate(folder: Path, name: str = "aerial", data: bytes = PNG + b"aerial", **fields) -> dict:
    (folder / (name + ".png")).write_bytes(data)
    row = {"id": name, "kind": "picture", "file": name + ".png", "sha256": _sha(data), "w": 4000, "h": 2250,
           "candidate": True, "checked": "looked at 17 Nov: no price, no people, no unit numbers", "ok": True,
           "about": ["common areas"], "shows": "the site from the air", "people": []}
    row.update(fields)
    return row


def test_a_candidate_is_usable_only_when_the_index_says_a_person_checked_it(index):
    folder = index([])
    pictures = {"p": {"asset": "aerial"}}
    assert "unknown_asset:aerial" in _act(AREAS, pictures=pictures)
    index([_candidate(folder)])
    assert _act(AREAS, pictures=pictures) == []
    for checked in (False, None, "", "  ", "not yet", "no", "pending", "unchecked", "TODO", "Not checked", 1):
        index([_candidate(folder, checked=checked)])
        assert "asset_not_checked:aerial" in _act(AREAS, pictures=pictures), checked
    for checked in (True, "no price / no people / no unit numbers"):          # a note of what was looked for
        index([_candidate(folder, checked=checked)])
        assert _act(AREAS, pictures=pictures) == [], checked
    index([_candidate(folder, ok=False)])
    assert "asset_excluded:aerial" in _act(AREAS, pictures=pictures)
    index([_candidate(folder, about=[])])
    assert "asset_not_described:aerial" in _act(AREAS, pictures=pictures)
    index([_candidate(folder, shows="")])
    assert "asset_not_described:aerial" in _act(AREAS, pictures=pictures)
    # the bytes are pinned: another file under the same name is not that picture
    index([_candidate(folder)])
    (folder / "aerial.png").write_bytes(PNG + b"swapped!")
    assert "asset_changed:aerial" in _act(AREAS, pictures=pictures)
    (folder / "aerial.png").unlink()
    assert "asset_file_missing:aerial" in _act(AREAS, pictures=pictures)
    # backed like any picture: by what it is `about`
    index([_candidate(folder, about=["sauna"])])
    assert "asset_not_in_line:aerial" in _act(AREAS, pictures=pictures)
    index([_candidate(folder, about=["sauna"], establishing=True)])
    assert _act(AREAS, pictures=pictures) == []
    # the pictures' own list still overrules the index
    index([_candidate(folder, name="plan_ground_site")])
    assert "asset_excluded:plan_ground_site" in _act(AREAS, pictures={"p": {"asset": "plan_ground_site"}})
    # a file name cannot leave the folder, an id is an id
    for row in (_candidate(folder, file="../aerial.png"), _candidate(folder, file="C:/x/aerial.png"),
                _candidate(folder, file="/aerial.png"), _candidate(folder, sha256="abc"), {**_candidate(folder), "id": "Bad Id"}):
        index([row])
        assert cs.asset_index() == {}, row


def test_the_page_gets_the_full_size_file_and_its_depth_map_by_hash_and_only_those_are_served(index):
    folder = index([])
    big, depth, soft = PNG + b"big", PNG + b"depth", PNG + b"soft"
    (folder / "pool.webp").write_bytes(big)
    (folder / "pool.depth.png").write_bytes(depth)
    (folder / "pool.depth_soft.png").write_bytes(soft)
    (folder / "secret.png").write_bytes(PNG + b"secret")
    row = {"id": "lagoon_scene58", "kind": "picture", "file": "pool.webp", "sha256": _sha(big), "w": 2273, "h": 1705,
           "from_pin": "lagoon_scene58", "candidate": False, "checked": "same cut as the pin", "ok": True,
           "depth": {"file": "pool.depth.png", "sha256": _sha(depth), "near": "white",
                     "soft": {"file": "pool.depth_soft.png", "sha256": _sha(soft)}}}
    index([row])
    pool = cs.for_cue("s04_l11", drafts=True)["pictures"]["pool"]
    assert pool["hires"] == "/ceremony-asset/" + _sha(big) and (pool["hw"], pool["hh"]) == (2273, 1705)
    assert pool["depth"] == "/ceremony-asset/" + _sha(soft) and "depth_near" not in pool      # the smoothed one moves a picture
    assert pool["src"].startswith("/story-asset/")                                            # the catalogue's copy stays the fallback
    assert cs.asset_file(_sha(big)) == (folder / "pool.webp", "image/webp")
    assert cs.asset_file(_sha(soft)) == (folder / "pool.depth_soft.png", "image/png")
    assert cs.asset_file(_sha(depth)) is None and cs.asset_file(_sha(PNG + b"secret")) is None     # not named = not served
    # no soft one: the measured map; a dark-is-near map says so
    index([{**row, "depth": {"file": "pool.depth.png", "sha256": _sha(depth), "near": "black"}}])
    pool = cs.for_cue("s04_l11", drafts=True)["pictures"]["pool"]
    assert pool["depth"] == "/ceremony-asset/" + _sha(depth) and pool["depth_near"] == "dark"
    # a depth map that is not the bytes the index pinned is not used; the picture still is
    (folder / "pool.depth.png").write_bytes(PNG + b"another one")
    pool = cs.for_cue("s04_l11", drafts=True)["pictures"]["pool"]
    assert "depth" not in pool and pool["hires"] == "/ceremony-asset/" + _sha(big)
    # the full-size file changed, or its entry is not ok: the catalogue's copy alone, and nothing served
    (folder / "pool.webp").write_bytes(PNG + b"swapped")
    pool = cs.for_cue("s04_l11", drafts=True)["pictures"]["pool"]
    assert "hires" not in pool and pool["src"].startswith("/story-asset/")
    assert cs.asset_file(_sha(big)) is None and cs.asset_file(_sha(PNG + b"swapped")) is None
    (folder / "pool.webp").write_bytes(big)
    index([{**row, "ok": False}])
    assert "hires" not in cs.for_cue("s04_l11", drafts=True)["pictures"]["pool"] and cs.asset_file(_sha(big)) is None
    # the full size of a picture the ceremony's list excludes is not served either
    (folder / "site.webp").write_bytes(PNG + b"site")
    index([{"id": "plan_ground_site", "file": "site.webp", "sha256": _sha(PNG + b"site"), "from_pin": "plan_ground_site", "ok": True,
            "checked": "x"}])
    assert cs.asset_file(_sha(PNG + b"site")) is None


def test_a_clip_is_a_checked_candidate_of_the_index_and_is_backed_like_a_picture(index):
    folder = index([])
    clip = b"\x00\x00\x00\x18ftypmp42" + b"logo clip"
    (folder / "logo.mp4").write_bytes(clip)
    row = {"id": "logo_clip", "kind": "video", "file": "logo.mp4", "sha256": _sha(clip), "w": 1918, "h": 1080,
           "candidate": True, "checked": True, "ok": True, "about": ["common areas"], "shows": "the project's own logo clip",
           "people": []}
    index([row])
    slide = {"template": "act", "act": "example_panel", "videos": {"logo": {"asset": "logo_clip"}}}
    cue = event_script.cue(AREAS)
    assert cs.problems(slide, cue) == []
    assert cs.page_payload(slide, cue)["videos"] == {"logo": {"src": "/ceremony-asset/" + _sha(clip), "w": 1918, "h": 1080,
                                                              "type": "video/mp4"}}
    assert cs.asset_file(_sha(clip)) == (folder / "logo.mp4", "video/mp4")
    assert "not_a_picture:logo_clip" in _act(AREAS, pictures={"p": {"asset": "logo_clip"}})
    assert "asset_not_near_its_beat:logo_clip" in _act(AREAS, videos={"logo": {"asset": "logo_clip"}},
                                                      beats=[{"at_en": "There are several", "do": "play", "key": "logo"}])
    index([{**row, "checked": False}])
    assert "asset_not_checked:logo_clip" in cs.problems(slide, cue) and cs.asset_file(_sha(clip)) is None


def test_the_signature_covers_every_file_a_slide_names_and_the_act_that_draws_it(index, tmp_path, monkeypatch):
    folder = index([])
    doc = {"slides": {"s04_l11": _slide()}}
    plain = cs.digest({"slides": {"x": {"template": "hero", "title": "Hello"}}})
    assert plain == hashlib.sha256(json.dumps({"x": {"template": "hero", "title": "Hello"}}, ensure_ascii=False,
                                              sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    seen = [cs.digest(doc)]

    def changed() -> bool:
        seen.append(cs.digest(doc))
        return seen[-1] != seen[-2] and seen[-1] not in seen[:-2]

    big, depth = PNG + b"big", PNG + b"depth"
    (folder / "pool.webp").write_bytes(big)
    (folder / "pool.depth.png").write_bytes(depth)
    row = {"id": "lagoon_scene58", "file": "pool.webp", "sha256": _sha(big), "from_pin": "lagoon_scene58", "ok": True, "checked": "x"}
    index([row])
    assert changed()                                      # a full-size file appeared for its picture
    index([{**row, "depth": "pool.depth.png"}])
    assert changed()                                      # ... its depth map
    (folder / "pool.depth.png").write_bytes(PNG + b"deeper")
    assert changed()                                      # ... the depth map's bytes
    (folder / "pool.webp").write_bytes(PNG + b"bigger")
    assert changed()                                      # ... the picture's bytes
    index([{**row, "depth": "pool.depth.png", "ok": False}])
    assert changed()                                      # ... what the index says of it
    # a candidate: its check is part of what was signed
    doc["slides"]["s04_l06"] = {"template": "act", "act": "example_panel", "pictures": {"p": {"asset": "aerial"}}}
    index([_candidate(folder)])
    assert changed()
    index([_candidate(folder, checked=False)])
    assert changed()
    # the act's own two files
    acts = tmp_path / "acts"
    shutil.copytree(ACTS, acts)
    monkeypatch.setattr(cs, "acts_dir", lambda: acts)
    seen.append(cs.digest(doc))
    (acts / "example_panel.js").write_text((acts / "example_panel.js").read_text(encoding="utf-8") + "\n// moved\n", encoding="utf-8")
    assert changed()
    (acts / "example_panel.css").write_text(".cs-act-example_panel { opacity: 1; }", encoding="utf-8")
    assert changed()
    (acts / "example_panel.js").unlink()
    assert changed()                                      # gone: not what was signed either


def test_the_screens_listener_serves_the_acts_list_and_the_index_files_behind_its_cookie(example, monkeypatch):
    from fastapi.testclient import TestClient

    from app import face_mirror, mirror_server

    monkeypatch.setattr(settings, "mirror_token", "room-secret")
    face_mirror.reset()
    pool = cs.for_cue("s04_l11", drafts=True)["pictures"]["pool"]
    with TestClient(mirror_server.app) as client:
        for path in ("/ceremony-acts.json", pool["hires"], "/assets/ceremony-show/acts/example_panel.js"):
            assert client.get(path).status_code == 403, path                    # no cookie: nothing
        assert client.get("/mirror?token=room-secret").status_code == 200
        listed = client.get("/ceremony-acts.json")
        assert listed.status_code == 200 and "no-cache" in listed.headers["cache-control"]
        assert listed.json() == cs.acts_manifest() and listed.json()["acts"]["example_panel"]["v"] == cs.acts()["example_panel"]["v"]
        got = client.get(pool["hires"])
        assert got.status_code == 200 and got.headers["content-type"] == "image/webp"
        assert hashlib.sha256(got.content).hexdigest() == pool["hires"].rsplit("/", 1)[1]        # the bytes its address names
        assert "max-age" in got.headers["cache-control"] and got.headers["x-content-type-options"] == "nosniff"
        # kept for the event's days (review 22 Nov: an hour meant s06's 2.8 MB clip came over the hall's Wi-Fi
        # as her line started, whatever was played through the day before)
        keep = int(re.search(r"max-age=(\d+)", got.headers["cache-control"]).group(1))
        assert keep >= 3 * 24 * 3600 and "immutable" in got.headers["cache-control"]
        if "depth" in pool:
            assert client.get(pool["depth"]).status_code == 200
        for path in ("/ceremony-asset/" + "0" * 64, "/ceremony-asset/index.json", "/ceremony-asset/..%2Findex.json",
                     "/ceremony-asset/" + "A" * 64):
            assert client.get(path).status_code == 404, path
        js = client.get("/assets/ceremony-show/acts/example_panel.js?v=" + cs.acts()["example_panel"]["v"])
        assert js.status_code == 200 and "CeremonyShow.register('example_panel'" in js.text
    face_mirror.reset()


# ---- the engine: what an act is written against -------------------------------------------------

def _acts(cases: dict) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required: NOT checked here -- when an act's file is loaded, what its factories put "
                    "in the page, what stays hidden until which beat, what happens when it throws")
    result = subprocess.run([node, "tests/client_ceremony_acts.cjs"], cwd=ROOT, input=json.dumps(cases),
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


A, B, C, D = ("/story-asset/" + "a" * 64, "/ceremony-asset/" + "b" * 64, "/ceremony-asset/" + "c" * 64,
              "/ceremony-asset/" + "d" * 64)
PAGE_SLIDE = {
    "template": "act", "act": "probe", "v": "abc123abc123", "css": True,
    "texts": {"title": "Main common areas"},
    "figures": {"n": {"lead": "about", "value": "600", "unit": "metres", "label": "in a straight line"}},
    "pictures": {"pic": {"src": A, "w": 1080, "h": 810, "alpha": False, "hires": B, "hw": 2160, "hh": 1620, "depth": C,
                         "caption": "The swimming pool"}},
    "videos": {"clip": {"src": D, "w": 1918, "h": 1080, "type": "video/mp4"}},
    "beats": [{"at": 0.3, "do": "num", "key": "n"}, {"at": 0.6, "do": "show", "key": ["pic"]},
              {"at": 0.8, "do": "push", "to": "pic", "k": 1.2}],
    "options": {"panel": {"x": 46}, "points": [[1, 2], [3, 4]], "mode": "night", "bad": "Two words", "on": False},
}
PROBE = """
CeremonyShow.register('probe', function (ctx) {
  var log = window.__log;
  log.push(['factory', ctx.late]);
  var title = ctx.text('title'), fig = ctx.figure('n', { glow: true }), pic = ctx.picture('pic');
  var again = ctx.text('title', { split: 'words', cls: 'cs-type-h2' });
  fig.setAttribute('data-t', 'fig');
  again.setAttribute('data-t', 'again');
  ctx.stage.setAttribute('data-t', 'box');
  ctx.add(ctx.stage, title, fig, pic && pic.el, pic && pic.caption, again, ctx.text('nope'), 'words that are not a node');
  log.push(['has', ctx.has('title'), ctx.has('n'), ctx.has('pic'), ctx.has('clip'), ctx.has('nope')]);
  log.push(['pic', pic.url, pic.w, pic.h, pic.depth, pic.fit, !!pic.caption]);
  if (window.__flags.video) {
    var v = ctx.video('clip');
    log.push(['video', !!v]);
    if (v) { ctx.add(ctx.stage, v.el); v.play(); }
  }
  return {
    beat: function (b, late) { log.push(['beat', b['do'], late, b.key === undefined ? null : b.key, b.k === undefined ? null : b.k]); },
    progress: function (f) { log.push(['progress', f]); },
    settle: function () { log.push(['settle']); },
    clear: function () { log.push(['clear']); }
  };
});
"""


def _log(look: dict, what: str) -> list:
    return [row for row in look["log"] if row[0] == what]


def test_an_act_is_loaded_when_a_slide_names_it_and_runs_on_her_voice_alone():
    got = _acts({"flow": {"acts": {"probe": PROBE}, "steps": [
        {"show": PAGE_SLIDE}, {"tick": 30}, {"tick": 40}, {"progress": 0.2}, {"progress": 0.35}, {"tick": 3000},
        {"progress": 0.9}, {"progress": 0.95}, {"settle": True}, {"clear": True}, {"tick": 1000}]}})["flow"]["seen"]   # (down: 0.8 s)
    # asked for by its own tag (the address moves with the bytes), its stylesheet with it
    assert got[0]["ret"] is True and got[0]["asked"] == ["link /assets/ceremony-show/acts/probe.css?v=abc123abc123",
                                                         "script /assets/ceremony-show/acts/probe.js?v=abc123abc123"]
    assert got[0]["state"]["started"] is False and got[0]["log"] == [] and got[0]["roots"] == ["cs-slide cs-t-act cs-act-probe"]
    # its file is here: the opening is built, with no entrance class yet; one frame later it is live
    assert got[1]["state"]["started"] is True and _log(got[1], "factory") == [["factory", False]]
    assert "cs-live" not in got[1]["roots"][0] and "cs-live" in got[2]["roots"][0]
    # THE GATE: what a factory made for a beat's key is hidden until that beat; the rest is the opening
    assert got[1]["hidden"] == ["n", "pic", "pic"] and got[1]["keyed"] == {"title": 2, "n": 1, "pic": 2}
    assert got[1]["state"]["waiting"] == ["n", "pic"]
    assert got[3]["hidden"] == ["n", "pic", "pic"] and _log(got[3], "beat") == [] and _log(got[3], "progress") == [["progress", 0.2]]
    assert got[4]["hidden"] == ["pic", "pic"] and _log(got[4], "beat") == [["beat", "num", False, "n", None]]
    # no clock in the engine: time alone brings nothing
    assert _log(got[5], "beat") == _log(got[4], "beat") and got[5]["hidden"] == ["pic", "pic"]
    # the rest, in the line's order, each once; a beat without a key hides nothing
    assert _log(got[6], "beat") == [["beat", "num", False, "n", None], ["beat", "show", False, ["pic"], None],
                                    ["beat", "push", False, None, 1.2]]
    assert got[6]["hidden"] == [] and got[6]["state"]["shown"] == ["n", "pic"] and got[6]["state"]["did"] == ["num", "show", "push"]
    assert len(_log(got[7], "beat")) == 3 and _log(got[7], "progress")[-1] == ["progress", 0.95]
    assert _log(got[8], "settle") == [["settle"]] and len(_log(got[8], "beat")) == 3 and got[8]["state"]["settled"] is True
    # down: nothing is up any more -- but the shot is still SEEN going (the sheet of light takes
    # it away, 18 Nov), so it lets go of what it holds (its clear(), its frames, its clips) when
    # it is gone, not before: an outgoing shot does not freeze in its cut
    assert _log(got[9], "clear") == [] and got[9]["state"] == {"up": False, "idle": False} and got[9]["slides"] == 1
    assert _log(got[10], "clear") == [["clear"]]
    assert got[10]["slides"] == 0 and got[10]["timers"] == 0 and got[10]["frames"] == 0
    # a second line of the same act: its file is not fetched again
    again = _acts({"x": {"acts": {"probe": PROBE}, "steps": [{"show": PAGE_SLIDE}, {"tick": 30}, {"show": PAGE_SLIDE},
                                                             {"tick": 1500}]}})["x"]["seen"]                # (the sheet: 1.2 s)
    assert len(again[2]["asked"]) == 2 and again[2]["state"]["started"] is True
    assert _log(again[2], "clear") == [] and len(_log(again[2], "factory")) == 2                # the first is still seen under the cut
    assert _log(again[3], "clear") == [["clear"]] and again[3]["slides"] == 1                   # ... and lets go when it is covered


def test_what_the_factories_put_in_the_page_is_the_slides_content_and_nothing_else():
    got = _acts({"flow": {"acts": {"probe": PROBE}, "steps": [
        {"show": PAGE_SLIDE}, {"tick": 100}, {"load": "fail"}, {"load": True}, {"tick": 5000}, {"settle": True}]}})["flow"]["seen"]
    words = "Main common areas about 600 metres in a straight line The swimming pool Main common areas"
    assert got[1]["text"] == words and "not a node" not in got[1]["text"]            # ctx.add takes elements, never a string
    assert got[5]["text"] == words                                                    # a figure never counts: its own value, always
    assert got[1]["classes"]["fig"] == "cs-fig cs-fig-glow cs-await" and got[5]["classes"]["fig"] == "cs-fig cs-fig-glow"
    # (a headline carries its halo by default -- the film's type, 18 Nov: cs-text-glow)
    assert got[1]["classes"]["again"] == "cs-text cs-type-h2 cs-text-glow" and got[1]["vars"]["again"] == {"--n": "3"}
    assert any("no text \"nope\"" in w for w in got[1]["warnings"])                   # a key the slide lacks: null, and said
    assert _log(got[1], "has") == [["has", True, True, True, True, False]]
    # the full-size file first; the catalogue's copy when it does not come; a decoded picture is counted
    assert _log(got[1], "pic") == [["pic", B, 2160, 1620, C, "cover", True]]
    assert got[1]["imgs"] == [B] and got[2]["imgs"] == [A] and got[1]["state"]["pictures"] == 1
    assert got[2]["state"]["loaded"] == 0 and got[3]["state"]["loaded"] == 1
    # only the two kinds of address the server makes are ever asked for
    for bad in ("http://evil.example/x.png", "/story-asset/../secret", "/slides/intro.jpg", "data:image/png;base64,AAAA", ""):
        slide = copy.deepcopy(PAGE_SLIDE)
        slide["pictures"]["pic"] = {"src": bad, "w": 10, "h": 10, "hires": bad, "depth": bad}
        slide["videos"]["clip"]["src"] = bad
        seen = _acts({"x": {"acts": {"probe": PROBE}, "flags": {"video": True}, "steps": [{"show": slide}, {"tick": 100}]}})["x"]["seen"][1]
        assert seen["imgs"] == [] and seen["videos"] == [] and _log(seen, "pic") == [["pic", None, 10, 10, None, "cover", False]], bad
        assert _log(seen, "video") == [["video", False]]


OPTS = """
CeremonyShow.register('probe', function (ctx) {
  var log = window.__log, shape = ctx.svg('path', { d: 'M0 0', onclick: 'x()', href: '/x', cls: 'mine', fill: 7 });
  log.push(['opt', ctx.opt('panel.x', 1), ctx.opt('points.1.0', 0), ctx.opt('mode', 'x'), ctx.opt('bad', 'fallback'),
            ctx.opt('nope.deep', 7), ctx.opt('on', true), typeof ctx.opt('panel', null), ctx.opt('', 5), ctx.opt(null, 6)]);
  log.push(['svg', ctx.svg('text', {}), ctx.svg('foreignObject', {}), ctx.svg('script', {}), ctx.svg('image', {}), shape.attrs]);
  log.push(['el', ctx.el('a b').className, ctx.el('<i>').className]);
  return {};
});
"""


def test_a_clip_is_always_muted_and_options_reach_an_act_as_values_never_as_words():
    seen = _acts({"x": {"acts": {"probe": PROBE}, "flags": {"video": True}, "steps": [{"show": PAGE_SLIDE}, {"tick": 100}, {"clear": True}]}})["x"]["seen"]
    assert seen[1]["videos"] == [{"src": D, "muted": True, "loop": False, "attrs": ["muted", "playsinline"], "played": 1,
                                  "time": None, "rate": None}]
    got = _acts({"x": {"acts": {"probe": OPTS}, "steps": [{"show": PAGE_SLIDE}, {"tick": 100}]}})["x"]["seen"][1]
    assert _log(got, "opt") == [["opt", 46, 3, "night", "fallback", 7, False, "object", 5, 6]]      # "Two words" is not a value
    # shapes only: no text, nothing that loads or runs; no handler, no address on a shape
    assert _log(got, "svg") == [["svg", None, None, None, None, {"d": "M0 0", "class": "mine", "fill": "7"}]]
    assert _log(got, "el") == [["el", "a b", ""]]


def test_an_act_whose_code_comes_after_her_voice_began_or_on_a_screen_that_joined_late_shows_the_state():
    got = _acts({
        "slow": {"acts": {"probe": PROBE}, "loadMs": 500, "steps": [
            {"show": PAGE_SLIDE}, {"progress": 0.4}, {"tick": 600}, {"progress": 0.7}, {"tick": 500}, {"progress": 0.9}]},
        "late": {"acts": {"probe": PROBE}, "register": ["probe"], "steps": [
            {"show": PAGE_SLIDE, "late": True}, {"progress": 0.7}, {"tick": 500}, {"progress": 0.9}]},
        "over": {"acts": {"probe": PROBE}, "loadMs": 500, "steps": [
            {"show": PAGE_SLIDE}, {"progress": 0.4}, {"settle": True}, {"tick": 600}]}})
    slow = got["slow"]["seen"]
    assert slow[1]["log"] == [] and slow[1]["state"]["started"] is False          # nothing of hers runs before her code is here
    assert slow[2]["log"][:4] == [["factory", True], ["has", True, True, True, True, False],
                                  ["pic", B, 2160, 1620, C, "cover", True], ["beat", "num", True, "n", None]]
    assert "cs-instant" in slow[2]["roots"][0] and "cs-live" in slow[2]["roots"][0] and slow[2]["hidden"] == ["pic", "pic"]
    assert _log(slow[3], "beat")[-1] == ["beat", "show", True, ["pic"], None]      # still catching up: no entrance
    assert "cs-instant" not in slow[4]["roots"][0]
    assert _log(slow[5], "beat")[-1] == ["beat", "push", False, None, 1.2]
    late = got["late"]["seen"]
    assert late[0]["log"][0] == ["factory", True] and "cs-instant" in late[0]["roots"][0] and "cs-live" in late[0]["roots"][0]
    assert late[0]["hidden"] == ["n", "pic", "pic"] and _log(late[0], "beat") == []          # never the end state ahead of her words
    assert [row[1:3] for row in _log(late[1], "beat")] == [["num", True], ["show", True]]
    assert "cs-instant" not in late[2]["roots"][0] and _log(late[3], "beat")[-1][1:3] == ["push", False]
    # the line ended while its code was still coming: every beat as a state, then settle
    over = got["over"]["seen"]
    assert over[2]["log"] == [] and [row[1:3] for row in _log(over[3], "beat")] == [["num", True], ["show", True], ["push", True]]
    assert _log(over[3], "settle") == [["settle"]] and over[3]["hidden"] == []


def test_an_act_that_throws_or_does_not_load_is_taken_down_to_the_plain_stage():
    boom = lambda where: PROBE.replace(where, where + " throw new Error('boom');", 1)        # noqa: E731
    cases = {
        "factory": {"acts": {"probe": boom("var log = window.__log;")}},
        "beat": {"acts": {"probe": boom("beat: function (b, late) {")}},
        "progress": {"acts": {"probe": boom("progress: function (f) {")}},
        "settle": {"acts": {"probe": boom("settle: function () {")}},
        "missing": {"acts": {"probe": None}},
        "silent": {"acts": {"probe": "/* a file that registers nothing */"}},
    }
    for case in cases.values():
        case["steps"] = [{"show": PAGE_SLIDE}, {"tick": 100}, {"progress": 0.5}, {"progress": 1}, {"settle": True}, {"tick": 100},
                         {"clear": True}, {"tick": 800}, {"show": {**PAGE_SLIDE, "act": "fine"}}, {"tick": 100}]
        case["acts"]["fine"] = PROBE.replace("'probe'", "'fine'")
    got = _acts(cases)
    for name, when in (("factory", 1), ("beat", 2), ("progress", 2), ("settle", 4), ("missing", 1), ("silent", 1)):
        seen = got[name]["seen"]
        assert seen[0]["ret"] is True, name
        assert [s["state"].get("failed") for s in seen[:6]] == [False] * when + [True] * (6 - when), name
        assert seen[5]["boxes"] == [0] and seen[5]["text"] == "", name               # its box is gone: the plain stage, no half frame
        assert seen[5]["stage"] == "cs-stage cs-up", name                            # ... and the stage is still up for the subtitle
        assert any("act probe is down" in w for w in seen[5]["warnings"]), name
        assert seen[5]["frames"] == 0, name
        assert seen[9]["state"]["act"] == "fine" and seen[9]["state"]["started"] is True and seen[9]["state"]["failed"] is False, name
    # what threw in a beat was told to let go
    assert _log(got["beat"]["seen"][5], "clear") == [["clear"]]


FX = """
CeremonyShow.register('probe', function (ctx) {
  var fx = ctx.fx, call = window.__call = {}, log = window.__log;
  function tag(e, name) { e.setAttribute('data-t', name); return e; }
  var scene = tag(ctx.el('scene'), 'scene'), cam = fx.camera(scene);
  var glass = tag(fx.glass('mine'), 'glass'), con = fx.console('c'), sat = tag(fx.sat(ctx.text('title'), 's'), 'sat');
  tag(con.el, 'console'); tag(con.body, 'body');
  var cards = [ctx.el('a'), ctx.el('b'), ctx.el('c')], fan = tag(fx.fan(cards.concat([null, 'x']), { spread: 40, lift: 3, tilt: 6 }), 'fan');
  cards.forEach(function (c, i) { tag(c.parentNode, 'fan' + i); });
  var layer = tag(ctx.el('layer'), 'layer');
  var plane = tag(fx.plane(layer, ctx.figure('n'), [[10, 10], [60, 20], [70, 80], [5, 60]], { w: 30, h: 10 }), 'plane');
  var route = fx.route([[0, 0], [50, 50], [100, 0]], { aspect: 2, width: 0.5 });
  var outline = fx.route([[10, 10], [90, 10], [50, 90]], { close: true });
  var arc = fx.arc([10, 50], [90, 50], { bend: 0.5, aspect: 2 });
  tag(route.el, 'route'); tag(outline.el, 'outline'); tag(arc.el, 'arc');
  var inset = tag(fx.inset(scene, [20, 30, 40, 22.5], ctx.el('next')), 'inset');
  var typed = fx.typed(ctx.text('title', { split: 'chars' }));
  tag(typed.el, 'typed');
  var pic = ctx.picture('pic'), plx = fx.parallax(pic, { amount: 2, zoom: 1.1 });
  tag(pic.el, 'plxpic'); tag(plx.el, 'plx');
  var a = tag(ctx.el('a2'), 'whipout'), b = tag(ctx.el('b2'), 'whipin');
  ctx.add(ctx.stage, scene, glass, con.el, sat, fan, layer, route.el, outline.el, arc.el, typed.el, plx.el, a, b);
  call.cam = function (m, v, ms, ease) { cam[m](v, ms, ease); return cam.view(); };
  call.route = function (m, x) { route[m](x); };
  call.outline = function (m, x) { outline[m](x); };
  call.typed = function (m, x) { typed[m](x); };
  call.plx = function (m, x, y) { return m === 'gl' ? plx.gl() : plx[m](x, y); };
  call.whip = function (ms, dir) { fx.whip(a, b, { ms: ms, dir: dir }); };
  call.frames = function () { ctx.frame(function () { log.push(['frame']); }); };
  call.after = function (ms) { ctx.after(ms, function () { log.push(['after']); }); };
  call.bg = function (m) { ctx.bg(m); };
  call.quad = function () { return CeremonyShow.quad(300, 100, [[100, 100], [600, 200], [700, 800], [50, 600]]); };
  return {};
});
"""


def _through(m: list, x: float, y: float) -> tuple[float, float]:
    """A point of a box through a CSS matrix3d (column-major, with its perspective divide)."""
    w = m[3] * x + m[7] * y + m[15]
    return (m[0] * x + m[4] * y + m[12]) / w, (m[1] * x + m[5] * y + m[13]) / w


def test_the_shared_primitives_do_what_the_guide_says():
    step = lambda name, *args: {"call": name, "args": list(args)}          # noqa: E731
    steps = [
        {"show": PAGE_SLIDE}, {"tick": 100},                                                   # 0, 1
        step("quad"),                                                                           # 2
        step("cam", "set", {"x": 30, "y": 40, "scale": 2}),                                     # 3
        step("cam", "to", {"rect": [20, 30, 40, 22.5]}, 1000, "push"),                          # 4
        step("cam", "push", 2, 500), step("cam", "pan", 5, -5, 300), step("cam", "drift"),      # 5, 6, 7
        {"size": {"stage": [1920, 1080], "layer": [1920, 1080]}, "resize": True},               # 8
        step("route", "draw", 0.25), step("route", "draw", 0), step("route", "play", 800), {"tick": 40},     # 9..12
        step("outline", "draw", 1),                                                             # 13
        step("typed", "set", 0.5), step("typed", "play", 160), {"frame": 5}, {"frame": 7},      # 14..17
        step("plx", "set", 1, -1), step("plx", "gl"),                                           # 18, 19
        step("whip", 400, "right"), {"tick": 470},                                              # 20, 21
        step("frames"), {"frame": 3}, step("after", 100), step("bg", "off"),                    # 22..25
        {"clear": True}, {"frame": 2, "tick": 900}, {"frame": 1},                               # 26, 27, 28
    ]
    got = _acts({"fx": {"acts": {"probe": FX}, "steps": steps}})["fx"]["seen"]
    assert got[1]["state"]["failed"] is False, got[1]["warnings"]
    # ---- words standing in a picture: the box's corners land on the four points
    m = got[2]["ret"]
    for (x, y), want in (((0, 0), (100, 100)), ((300, 0), (600, 200)), ((300, 100), (700, 800)), ((0, 100), (50, 600))):
        assert _through(m, x, y) == pytest.approx(want, abs=1e-6)
    assert "cs-placed" not in got[1]["classes"]["plane"]                    # not seen before it stands where it belongs
    placed = got[8]["classes"]["plane"], got[8]["styles"]["plane"]
    assert "cs-placed" in placed[0] and placed[1]["width"] == "calc(30 * var(--cu))" and placed[1]["height"] == "calc(10 * var(--cu))"
    m = [float(v) for v in re.fullmatch(r"matrix3d\((.*)\)", placed[1]["transform"]).group(1).split(",")]
    for (x, y), want in (((0, 0), (192, 108)), ((576, 0), (1152, 216)), ((576, 192), (1344, 864)), ((0, 192), (96, 648))):
        assert _through(m, x, y) == pytest.approx(want, abs=0.01)           # 30cu x 10cu on a 1920 stage -> % of the layer
    # ---- the camera: a point of the group at the stage's centre, a scale; a rectangle to fill the stage
    cam = got[3]["styles"]["scene"]
    assert "cs-cam" in got[3]["classes"]["scene"] and cam["transition"] == "none"
    assert cam["transform"] == "translate(50%, 50%) rotate(0deg) scale(2) translate(-30%, -40%)"
    assert got[4]["ret"] == {"x": 40, "y": 41.25, "scale": 2.5, "rotate": 0}
    assert got[4]["styles"]["scene"]["transition"] == "transform 1000ms cubic-bezier(.3, 0, .1, 1)"
    assert got[4]["styles"]["scene"]["transform"] == "translate(50%, 50%) rotate(0deg) scale(2.5) translate(-40%, -41.25%)"
    assert got[5]["ret"]["scale"] == 5 and got[6]["ret"] == {"x": 45, "y": 36.25, "scale": 5, "rotate": 0}
    assert got[7]["ret"]["scale"] == pytest.approx(5.3) and got[7]["styles"]["scene"]["transition"] == "transform 14000ms linear"
    assert got[1]["styles"]["inset"] == {"left": "20%", "top": "30%", "width": "40%", "height": "22.5%"}     # the portal's far side
    # ---- panels, cards
    assert got[1]["classes"]["glass"] == "cs-glass cs-panel mine" and got[1]["classes"]["console"] == "cs-glass cs-panel cs-panel-console c"
    assert got[1]["classes"]["sat"] == "cs-glass cs-sat s"
    fan = [got[1]["vars"]["fan%d" % i] for i in range(3)]
    assert [v["--ry"] for v in fan] == ["20deg", "0deg", "-20deg"] and [v["--tz"] for v in fan] == ["-3", "0", "-3"]
    assert [v["--rz"] for v in fan] == ["-6deg", "0deg", "6deg"] and got[1]["tags"].get("div", 0) > 3
    # ---- a line drawn along its length
    paths = {p["cls"] + ":" + p["d"]: p for p in got[9]["paths"]}
    line = paths["cs-route-line:M 0 0 L 500 250 L 1000 0"]                   # % of the picture -> a 1000-wide box of its shape
    assert line["attrs"]["stroke-width"] == "5" and line["attrs"]["pathLength"] == "1"
    assert line["style"] == {"transition": "none", "strokeDashoffset": "0.75"}
    assert paths["cs-route-glow:M 0 0 L 500 250 L 1000 0"]["attrs"]["stroke-width"] == "17"
    assert "cs-route-line:M 100 250 Q 500 -150 900 250" in paths            # an arc, lifted off the straight line
    assert "cs-drawn" in got[9]["classes"]["route"] and "cs-drawn" not in got[10]["classes"]["route"]
    played = {p["cls"] + ":" + p["d"]: p for p in got[12]["paths"]}["cs-route-line:M 0 0 L 500 250 L 1000 0"]
    assert played["style"] == {"transition": "stroke-dashoffset 800ms linear", "strokeDashoffset": "0"}
    closed = [p for p in got[13]["paths"] if p["d"].endswith(" Z")]
    assert {p["cls"] for p in closed} == {"cs-route-fill", "cs-route-glow", "cs-route-line"}
    assert [p["in"] for p in closed if p["cls"] == "cs-route-fill"] == [True]          # an outline complete: filled
    assert [p["in"] for p in got[1]["paths"] if p["cls"] == "cs-route-fill"] == [False]
    # ---- typed text: every letter is there from the start, shown in turn; frames end when it is done
    letters = len("Maincommonareas")
    assert letters == 15 and got[1]["typed"] == [0, 15] and got[14]["typed"] == [8, 15]              # half of it, to a letter
    # played: on from the letters already shown, never one taken back (review 18 Nov: a play from
    # nothing again retyped s05_l11's title), at the play's pace (five frames = 64 ms of 160)
    assert got[16]["typed"] == [14, 15] and got[17]["typed"] == [15, 15] and got[17]["frames"] == 0
    assert "cs-typed-done" in got[17]["classes"]["typed"] and "cs-typed-done" not in got[14]["classes"]["typed"]
    # ---- a picture with depth: without WebGL the same picture, moved by transform -- never nothing
    assert got[18]["styles"]["plxpic"]["transform"] == "translate3d(calc(-2 * var(--cu)), calc(2 * var(--cu)), 0) scale(1.1)"
    assert got[19]["ret"] is False and got[19]["state"]["gl"] == 0 and got[19]["imgs"] == [B]
    assert "cs-plx-pic" in got[1]["classes"]["plxpic"]
    # ---- a whip: out, in, streaks -- and nothing of it left afterwards
    assert got[20]["classes"]["whipout"] == "a2 cs-whip-out cs-whip-right" and got[20]["classes"]["whipin"] == "b2 cs-whip-in cs-whip-right"
    assert got[20]["streaks"] == 1 and got[21]["streaks"] == 0
    assert got[21]["classes"]["whipout"] == "a2 cs-whipped" and got[21]["classes"]["whipin"] == "b2"
    # ---- frames and timers die with the slide; the stage's background comes back
    assert len(_log(got[23], "frame")) == 3 and got[23]["frames"] == 1
    assert got[25]["stage"] == "cs-stage cs-up cs-bg-off"
    # (18 Nov: a shot going down is still seen -- the sheet of light takes it away -- so its
    # frames go on until it is gone: an outgoing shot does not freeze. Its timers stop at once.)
    assert got[26]["stage"] == "cs-stage" and len(_log(got[27], "frame")) == 5 and _log(got[27], "after") == []
    assert got[28]["frames"] == 0 and len(_log(got[28], "frame")) == 5 and got[28]["slides"] == 0


def test_every_act_of_the_folder_is_preloaded_and_a_changed_one_is_fetched_again():
    two = PROBE.replace("log.push(['factory', ctx.late]);", "log.push(['factory', 'v2']);")
    manifest = {"acts": {"probe": {"v": "aaa111aaa111", "css": False}, "Bad Name": {"v": "x", "css": True}, "../x": {"v": "1"}}}
    got = _acts({
        "pre": {"acts": {"probe": PROBE, "probe@bbb222bbb222": two}, "manifest": manifest, "steps": [
            {"tick": 0}, {"tick": 50}, {"show": {**PAGE_SLIDE, "v": "aaa111aaa111", "css": False}},
            {"show": {**PAGE_SLIDE, "v": "bbb222bbb222", "css": False}}, {"tick": 50}]},
        "none": {"acts": {"probe": PROBE}, "manifest": None, "steps": [{"tick": 50}, {"show": PAGE_SLIDE}, {"tick": 50}]},
    })
    pre = got["pre"]["seen"]
    assert pre[0]["asked"] == ['fetch /ceremony-acts.json {"cache":"no-store","credentials":"same-origin"}',
                               "script /assets/ceremony-show/acts/probe.js?v=aaa111aaa111"]       # only names that are names
    assert pre[1]["acts"] == ["probe"]
    assert pre[2]["state"]["started"] is True and len(pre[2]["asked"]) == 2                     # there already: no wait, no fetch
    assert pre[3]["asked"][2:] == ["script /assets/ceremony-show/acts/probe.js?v=bbb222bbb222"] and pre[3]["state"]["started"] is False
    assert _log(pre[4], "factory") == [["factory", False], ["factory", "v2"]]                   # the new bytes, not the old act
    none = got["none"]["seen"]
    assert none[2]["state"]["started"] is True and len(none[2]["asked"]) == 3                   # no list: on demand


def test_the_engine_has_one_clock_for_beats_and_the_stage_rules_of_an_act_are_in_the_stylesheet():
    js = ENGINE.read_text(encoding="utf-8")
    # frames exist for an act's polish (ctx.frame); a beat is fired from progress() and settle() only
    assert js.count("requestAnimationFrame") == 2 and js.count("fire(run,") == 3
    assert "function catchUp(run, late) {" in js and "catchUp(run, run.instant);" in js
    assert "setInterval" not in js and "Date.now" not in js and "url(" not in js and "<img" not in js
    assert "var FILE = /^\\/(?:story-asset|ceremony-asset)\\/[0-9a-f]{64}$/;" in js
    plain = re.sub(r"/\*.*?\*/", "", STYLE.read_text(encoding="utf-8"), flags=re.S)
    assert ".cs-await { opacity: 0 !important; visibility: hidden !important; }" in plain
    box = plain[plain.index(".cs-slide > .cs-act-stage {"):]
    box = box[:box.index("}")]
    assert "width: calc(100 * var(--cu)); height: calc(56.25 * var(--cu));" in box and "overflow: hidden" in box
    assert ".cs-t-act.cs-instant, .cs-t-act.cs-instant * { transition: none !important; }" in plain
    assert ".cs-stage.cs-bg-off .cs-bg > span { animation-play-state: paused; }" in plain
    for token in ("--cs-night: #03050d", "--cs-blue: 47, 107, 255", "--cs-sky: 77, 163, 255", "--cs-cyan: 110, 231, 255",
                  "--cs-ink: #f4f7ff", "--cs-glass: rgba(18, 34, 96, .34)", "--cs-glass-edge: rgba(130, 175, 255, .75)"):
        assert token in plain, token
    # the shared stylesheet keeps the rules it asks of an act (its one definition of the unit
    # is the only raw vw/vh
    # ... and its @font-face rules: the film's own faces, files beside the acts -- Inter and, since
    # 22 Nov, Kantalad's four; tests/test_ceremony_slides.py pins them)
    core = plain.replace("--cu: min(1vw, 1.7778vh)", "")
    faces = re.findall(r"@font-face \{.*?\}", core, flags=re.S)
    assert len(faces) == 5 and all(f.count("url(") == 1 and 'url("ceremony-show/fonts/' in f for f in faces)
    for face in faces:
        core = core.replace(face, "")
    assert css_problems("core", core, prefixed=False) == []


# ---- the source scan: words reach the screen only through the factories ------------------------

def _split_js(source: str) -> tuple[str, list[tuple[int, str]]]:
    """An act's source with comments and the insides of strings blanked (line numbers kept),
    and its strings as (line, text)."""
    out, strings, i, line, n = [], [], 0, 1, len(source)
    while i < n:
        ch, two = source[i], source[i:i + 2]
        if two == "//":
            end = source.find("\n", i)
            end = n if end < 0 else end
            out.append(" " * (end - i))
            i = end
        elif two == "/*":
            end = source.find("*/", i + 2)
            end = n if end < 0 else end + 2
            out.append(re.sub(r"[^\n]", " ", source[i:end]))
            line += source.count("\n", i, end)
            i = end
        elif ch in "'\"`":
            j, text = i + 1, []
            while j < n and source[j] != ch:
                if source[j] == "\\" and j + 1 < n:
                    text.append(source[j:j + 2])
                    j += 2
                    continue
                text.append(source[j])
                j += 1
            body = "".join(text)
            strings.append((line, body))
            out.append(ch + re.sub(r"[^\n]", " ", body) + ch)
            line += body.count("\n")
            i = j + 1
        else:
            out.append(ch)
            if ch == "\n":
                line += 1
            i += 1
    return "".join(out), strings


#: What an act's code may not use, and why (docs/emma-ceremony-slides.md 0.5).
JS_CODE_RULES = (
    ("words outside ctx.text / ctx.figure",
     r"\b(?:textContent|innerText|innerHTML|outerHTML|outerText|nodeValue|insertAdjacent(?:HTML|Text|Element)|"
     r"createTextNode|createDocumentFragment|createRange|DOMParser|fillText|strokeText|measureText|getContext)\b|"
     r"\bdocument\s*\.\s*write"),
    ("a DOM call that takes strings (use ctx.add or appendChild)",
     r"(?<!\bctx)\.\s*(?:append|prepend|before|after|replaceWith|replaceChildren)\s*\("),
    ("an element made outside ctx.el / ctx.svg / the factories", r"\b(?:createElementNS|cloneNode|importNode|adoptNode)\b"),
    ("a picture or a file outside ctx.picture / ctx.video",
     r"\bnew\s+Image\b|\bImage\s*\(|\.\s*(?:src|srcset|href|srcObject|poster)\b|\bsrcdoc\b|\bfetch\s*\(|"
     r"\b(?:XMLHttpRequest|WebSocket|EventSource|sendBeacon|importScripts|Worker)\b|\bimport\s*\("),
    ("code made from strings", r"\beval\s*\(|\bnew\s+Function\b|\bFunction\s*\("),
    ("a clock of its own (use ctx.after / ctx.frame; WHEN something appears is progress())",
     r"\b(?:setTimeout|setInterval|requestAnimationFrame|requestIdleCallback|setImmediate)\b"),
    ("classes set wholesale (use classList and the cls option)", r"\.\s*className\b|\bcssText\b"),
    ("reaching outside the act's own box (use ctx.root, ctx.stage and the factories' handles)",
     r"\b(?:parentNode|parentElement|closest|querySelector(?:All)?|getElementsBy\w+|getElementById|childNodes|children|"
     r"firstChild|lastChild|firstElementChild|lastElementChild|nextSibling|previousSibling|nextElementSibling|"
     r"previousElementSibling|ownerDocument|getRootNode|documentElement|elementFromPoint)\b|\bdocument\s*\.\s*(?:body|head)\b"),
    ("storage or navigation", r"\b(?:localStorage|sessionStorage|indexedDB|postMessage)\b|\bdocument\s*\.\s*cookie|"
                              r"\bwindow\s*\.\s*open|\blocation\s*\.|\bhistory\s*\."),
)
JS_STRING_RULES = (
    ("the gate's class", r"cs-await"),
    ("the core's credit line (a map's licence: never an act's)", r"cs-credit"),
    ("a figure's inner pieces (a figure is one piece)", r"cs-fig-(?:lead|value|unit|label|row)"),
    ("!important", r"important"),
    ("an address", r"\burl\s*\(|^\s*(?:https?:|data:|blob:|//)"),
    ("markup", r"<\s*[A-Za-z/!]"),
    ("a size not in cu or % (1px hairlines apart)", r"(?<![\w.#-])(?!(?:0|1)px\b)\d*\.?\d+(?:px|vw|vh|vmin|vmax|rem|pt)\b"),
)
ELEMENTS = ("div", "span", "i", "b", "em", "section")


def js_problems(name: str, source: str) -> list[str]:
    """Why an act's .js may not ship ([] = it may): '<line>: <rule>: <what>'."""
    code, strings = _split_js(source)
    bad: list[str] = []

    def line_of(index: int) -> int:
        return code.count("\n", 0, index) + 1

    for rule, pattern in JS_CODE_RULES:
        for m in re.finditer(pattern, code):
            bad.append("%d: %s: %s" % (line_of(m.start()), rule, m.group().strip()))
    for line, text in strings:
        for rule, pattern in JS_STRING_RULES:
            m = re.search(pattern, text)
            if m:
                bad.append("%d: %s: %r" % (line, rule, text[:60]))
    plain = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group()), source, flags=re.S)
    plain = re.sub(r"(?m)(^|[^:'\"\\])//.*$", lambda m: m.group(1), plain)
    for m in re.finditer(r"\bcreateElement\s*\(\s*([^)]*)\)", plain):
        tag = re.fullmatch(r"\s*(['\"])([A-Za-z]+)\1\s*", m.group(1))
        if tag is None or tag.group(2).lower() not in ELEMENTS:
            bad.append("%d: createElement of anything but %s: %s" % (plain.count("\n", 0, m.start()) + 1, "/".join(ELEMENTS), m.group(1).strip()))
    for m in re.finditer(r"\bsetAttribute\s*\(\s*(['\"])(class|style|src|href|srcdoc|xlink:href)\1", plain, flags=re.I):
        bad.append("%d: setAttribute(%r): use classList, style.setProperty and the factories" % (plain.count("\n", 0, m.start()) + 1, m.group(2)))
    registered = re.findall(r"CeremonyShow\s*\.\s*register\s*\(\s*(['\"])([^'\"]*)\1", plain)
    if [r[1] for r in registered] != [name]:
        bad.append("1: the file must register exactly its own name %r: %s" % (name, [r[1] for r in registered]))
    return bad


_MOVES = ("transform", "opacity", "stroke-dashoffset")


def _top_level(value: str) -> list[str]:
    """`value` split at its top-level commas. (A regex split cut `var(--cs-ease, cubic-bezier(...))`
    -- the motion identity's own form, 18 Nov skill pass -- in two: its comma sits inside nested
    brackets, and the half left over read as a transition of a property called "cubic-bezier(".)"""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(value):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            parts.append(value[start:i])
            start = i + 1
    parts.append(value[start:])
    return parts


def css_problems(name: str, css: str, *, prefixed: bool = True) -> list[str]:
    """Why an act's .css may not ship ([] = it may). `prefixed=False` = the shared
    stylesheet: the same motion and content rules, without the per-act selector prefix."""
    plain = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group()), css, flags=re.S)
    bad: list[str] = []

    def line_of(index: int) -> int:
        return plain.count("\n", 0, index) + 1

    def declarations(body: str, at: int, selector: str, keyframes: bool) -> None:
        for m in re.finditer(r"([\w-]+)\s*:\s*([^;{}]+)", body):
            prop, value, where = m.group(1).lower(), m.group(2).strip(), line_of(at + m.start())
            if prop.startswith("--"):
                continue
            if prop == "content" and value not in ('""', "''", "none", "normal"):
                bad.append("%d: content may only be \"\" or none: %s" % (where, value))
            if keyframes and prop not in _MOVES + ("animation-timing-function",):
                bad.append("%d: a keyframe may move only transform and opacity: %s" % (where, prop))
            if prop in ("transition", "transition-property"):
                for part in _top_level(value):
                    first = part.strip().split()[0] if part.strip() else ""
                    if first not in _MOVES + ("none", "visibility"):
                        bad.append("%d: a transition may move only transform and opacity: %s" % (where, part.strip()))
            if prop in ("box-shadow", "text-shadow"):
                for part in _top_level(value):
                    lengths = re.findall(r"(?<![\w#(.,-])-?\d*\.?\d+(?:[a-z%]+)?(?![\w(])|calc\([^)]*(?:\([^)]*\)[^)]*)*\)",
                                         re.sub(r"rgba?\([^)]*\)|#[0-9a-fA-F]+", "", part))
                    if len(lengths) >= 3 and not re.fullmatch(r"-?0(?:[a-z%]+)?", lengths[2]):
                        bad.append("%d: a blurred %s (draw the glow with a gradient)" % (where, prop))
            if prefixed and "cs-fig-" in selector and (
                    (prop in ("display", "visibility") and re.search(r"none|hidden|collapse", value))
                    or (prop in ("opacity", "font-size", "width", "height", "max-width", "max-height") and re.fullmatch(r"0+(?:\.0+)?[a-z%]*", value))
                    or prop in ("clip", "clip-path", "mask", "mask-image", "text-indent")
                    or (prop == "color" and "transparent" in value)):
                bad.append("%d: hides a piece of a figure (a figure is one piece): %s { %s: %s }" % (where, selector.strip(), prop, value))
        for what, pattern in (("attr()", r"\battr\s*\("), ("url()", r"\burl\s*\("), ("a counter", r"\bcounters?\s*\(|counter-(?:reset|increment)"),
                              ("!important", r"!\s*important"), ("backdrop-filter", r"backdrop-filter"), ("will-change", r"will-change"),
                              ("a blur filter", r"\bblur\s*\(|\bdrop-shadow\s*\("), ("list-style / quotes", r"\blist-style|\bquotes\s*:"),
                              ("a size not in cu, % or em (1px hairlines apart)",
                               r"(?<![\w.#-])(?!(?:0|1)px\b)\d*\.?\d+(?:px|vw|vh|vmin|vmax|rem|pt)\b")):
            if what == "!important" and not prefixed:
                continue                                       # (the core's own gate and resets)
            for m in re.finditer(pattern, body):
                bad.append("%d: %s: %s" % (line_of(at + m.start()), what, body[max(0, m.start() - 30):m.end() + 20].strip().replace("\n", " ")))

    i, n = 0, len(plain)
    while i < n:
        brace = plain.find("{", i)
        if brace < 0:
            if plain[i:].strip():
                bad.append("%d: text outside a rule" % line_of(i))
            break
        prelude = plain[i:brace].strip()
        depth, j = 1, brace + 1
        while j < n and depth:
            depth += {"{": 1, "}": -1}.get(plain[j], 0)
            j += 1
        body = plain[brace + 1:j - 1]
        where = line_of(brace)
        if prelude.startswith("@"):
            frames = re.fullmatch(r"@keyframes\s+([\w-]+)", prelude)
            if frames is None:
                bad.append("%d: only @keyframes: %s" % (where, prelude[:40]))
            else:
                if prefixed and not frames.group(1).startswith(name + "-"):
                    bad.append("%d: a keyframes name starts with %s-: %s" % (where, name, frames.group(1)))
                for m in re.finditer(r"\{([^{}]*)\}", body):
                    declarations(m.group(1), brace + 1 + m.start(1), prelude, True)
        else:
            for selector in prelude.split(","):
                if prefixed and not re.match(r"\.cs-act-%s(?![\w-])" % re.escape(name), selector.strip()):
                    bad.append("%d: a selector starts with .cs-act-%s: %s" % (where, name, selector.strip()[:60]))
                if prefixed and "cs-await" in selector:
                    bad.append("%d: the gate's class in a selector: %s" % (where, selector.strip()[:60]))
                if prefixed and "cs-credit" in selector:
                    bad.append("%d: the credit line in a selector: %s" % (where, selector.strip()[:60]))
            declarations(body, brace + 1, prelude, False)
        i = j
    return bad


GOOD_JS = "CeremonyShow.register('mine', function (ctx) { var e = ctx.el('a'); ctx.add(ctx.stage, e, ctx.text('t')); return {}; });"


@pytest.mark.parametrize("line", [
    "e.textContent = 'Sky Pool';", "e.innerHTML = '<b>x</b>';", "e.innerText = x;", "e.insertAdjacentHTML('beforeend', x);",
    "e.appendChild(document.createTextNode(ctx.slide.texts.t));", "document.write(x);", "e.append('Sky Pool');", "e.prepend(x);",
    "e.before(x);", "e.after(x);", "e.replaceWith(x);", "e.replaceChildren(x);", "c.getContext('2d').fillText('x', 0, 0);",
    "document.createElementNS('http://www.w3.org/2000/svg', 'text');", "var copy = e.cloneNode(true);",
    "document.createElement('img');", "document.createElement('canvas');", "document.createElement(tag);", "document.createElement('iframe');",
    "var i = new Image();", "e.src = '/x.png';", "a.href = x;", "fetch('/secret');", "new XMLHttpRequest();", "new WebSocket(u);",
    "import('/x.js');", "eval(x);", "new Function('return 1')();", "setTimeout(go, 500);", "setInterval(go, 16);",
    "requestAnimationFrame(go);", "window.requestAnimationFrame(go);", "e.className = 'in';", "e.style.cssText = x;",
    "e.setAttribute('class', 'x');", "e.setAttribute('style', 'opacity: 1');", "e.classList.remove('cs-await');",
    "e.style.setProperty('opacity', '1', 'important');", "var q = '.cs-fig-lead';", "e.style.backgroundImage = 'url(/x.png)';",
    "e.style.width = '640px';", "e.style.left = '12vw';", "var html = '<div>Sky Pool</div>';", "localStorage.x = 1;",
    "location.href = x;", "var u = 'https://example.com/x';", "var w = new Worker(u);",
    "var root = ctx.stage.parentNode;", "ctx.stage.parentElement.classList.add('x');", "e.closest('.cs-slide');",
    "var v = e.querySelector('video');", "e.children[0].remove();", "document.body.appendChild(e);",
    "document.documentElement.classList.add('x');", "var c = 'cs-credit';",
])
def test_the_scan_refuses_every_other_way_an_acts_code_could_put_something_on_the_screen(line):
    assert js_problems("mine", GOOD_JS) == []
    assert js_problems("mine", GOOD_JS + "\n" + line) != [], line


def test_the_scan_reads_code_not_comments_and_lets_the_contract_through():
    fine = """
    /* e.textContent = 'x'; setTimeout(go, 1); the gate is cs-await */
    // e.innerHTML = '<b>'; location.href
    CeremonyShow.register('mine', function (ctx) {
      var fx = ctx.fx, box = document.createElement('div'), dot = document.createElement("span");
      ctx.after(300, function () { box.classList.add('in'); });           // ctx.after is not Element.after
      var stop = ctx.frame(function (t, dt) {});
      box.style.setProperty('--x', String(ctx.opt('location.x', 1)));     // a path named "location" is a path
      box.style.width = 'calc(40 * var(--cu))'; box.style.borderWidth = '1px'; box.style.top = '12.5%';
      box.setAttribute('data-part', 'title'); box.appendChild(dot);
      var shape = ctx.svg('path', { d: 'M 0 0 L 10 10' });
      ctx.add(ctx.stage, box, shape, ctx.figure('n'), ctx.text('history'));
      return { beat: function (b, late) { if (b.do === 'show') box.classList.add('in'); } };
    });
    """
    assert js_problems("mine", fine) == []
    assert js_problems("mine", GOOD_JS.replace("'mine'", "'other'")) != []                  # its own name, exactly once
    assert js_problems("mine", GOOD_JS + GOOD_JS) != []
    assert js_problems("mine", "var a = 1;") != []


GOOD_CSS = """.cs-act-mine .title { font-size: calc(4 * var(--cu)); opacity: 0; transition: opacity .5s ease, transform .8s ease; border: 1px solid red; }
.cs-act-mine.cs-live .title::after { content: ""; animation: mine-in 1s both; box-shadow: 0 0 0 1px rgba(1, 2, 3, .5); }
@keyframes mine-in { from { opacity: 0; transform: translate3d(0, calc(2 * var(--cu)), 0); } to { opacity: 1; transform: none; } }
.cs-act-mine .cs-fig { font-size: calc(7 * var(--cu)); opacity: 0; }
.cs-act-mine .cs-fig-unit { font-size: .4em; color: red; }
"""


@pytest.mark.parametrize("rule", [
    ".title { opacity: 1; }", ".cs-act-mine2 .title { opacity: 1; }", ".cs-act-mine .a, .b { opacity: 1; }",
    ".cs-act-mine .a::before { content: \"Sky Pool\"; }", ".cs-act-mine .a::after { content: attr(data-x); }",
    ".cs-act-mine .a { content: counter(n); }", ".cs-act-mine .a { background: url(/x.png); }",
    ".cs-act-mine .a { opacity: 1 !important; }", ".cs-act-mine .cs-await { opacity: 1; }",
    ".cs-act-mine .a { backdrop-filter: blur(4px); }", ".cs-act-mine .a { filter: blur(2px); }",
    ".cs-act-mine .a { filter: drop-shadow(0 0 4px blue); }", ".cs-act-mine .a { will-change: transform; }",
    ".cs-act-mine .a { box-shadow: 0 0 20px blue; }", ".cs-act-mine .a { text-shadow: 0 0 calc(1 * var(--cu)) blue; }",
    ".cs-act-mine .a { box-shadow: 0 0 0 1px red, 0 2px 12px rgba(0, 0, 0, .5); }",
    ".cs-act-mine .a { transition: all .3s; }", ".cs-act-mine .a { transition: width .3s; }", ".cs-act-mine .a { transition: .3s; }",
    ".cs-act-mine .a { transition-property: left; }", "@keyframes mine-x { to { width: 10%; } }", "@keyframes grow { to { opacity: 1; } }",
    "@media (min-width: 1px) { .cs-act-mine .a { opacity: 1; } }", "@import 'x.css';", "@font-face { font-family: x; }",
    ".cs-act-mine .a { width: 640px; }", ".cs-act-mine .a { left: 12vw; }", ".cs-act-mine .a { font-size: 2rem; }",
    ".cs-act-mine .cs-fig-lead { display: none; }", ".cs-act-mine .cs-fig-unit { visibility: hidden; }",
    ".cs-act-mine .cs-fig-label { opacity: 0; }", ".cs-act-mine .cs-fig-lead { font-size: 0; }",
    ".cs-act-mine .cs-fig-unit { color: transparent; }", ".cs-act-mine .cs-fig-lead { clip-path: inset(50%); }",
    ".cs-act-mine .a { list-style: \"Sky Pool\"; }", ".cs-act-mine .cs-credit { opacity: 0; }",
])
def test_the_scan_refuses_an_acts_stylesheet_that_draws_words_leaves_its_corner_or_breaks_the_motion_rules(rule):
    assert css_problems("mine", GOOD_CSS) == []
    assert css_problems("mine", GOOD_CSS + rule + "\n") != [], rule


def _act_files() -> list[Path]:
    return sorted(p for p in ACTS.glob("*") if p.suffix in (".js", ".css"))


def test_every_act_file_in_the_folder_passes_the_scan():
    """A designer's file: only the contract's ways onto the screen. A failure reads
    '<file>:<line>: <rule>: <what>'."""
    files = _act_files()
    assert any(p.name == "example_panel.js" for p in files)
    bad: list[str] = []
    for path in files:
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,31}", path.stem):
            bad.append("%s: an act's name is lower-case letters, digits and _ (2-32)" % path.name)
            continue
        source = path.read_text(encoding="utf-8")
        found = js_problems(path.stem, source) if path.suffix == ".js" else css_problems(path.stem, source)
        bad += ["%s:%s" % (path.name, p) for p in found]
        if path.suffix == ".css" and not path.with_suffix(".js").is_file():
            bad.append("%s: a stylesheet without its act" % path.name)
    assert bad == [], "\n" + "\n".join(bad)


def test_no_act_draws_the_dim_frame_panels_the_owner_turned_down():
    """Owner 20 Nov, review PDF page 1, arrows on four dim glass panels with lines in them in the
    stage's sky: "ไม่ชอบ ดูแปลกๆเป็นกรอบๆ". The core's went that day; smarthome_brand's console
    drew four of its own in all six s08 cues until the audit of 22 Nov. No act draws them."""
    for path in _act_files():
        code = path.read_text(encoding="utf-8")
        assert not re.search(r"glass\(\s*['\"]ghost|\.ghost\b|['\"]gline['\"]|\.gline\b", code), path.name


# ---- every act, run with every slide that names it, in a real page --------------------------------

SHELL = """<!doctype html><html class="mirror mirror-show"><head><meta charset="utf-8">
<style>html, body { margin: 0; height: 100%; background: #000; font-family: 'Segoe UI', sans-serif; }
html.mirror.mirror-show { --mirror-foot: 22vh; }</style>
<link rel="stylesheet" href="/assets/ceremony-show.css"></head><body><script src="/assets/ceremony-show.js"></script></body></html>"""

WALK = """async (slide) => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const eng = window.CeremonyShow, seen = [];
  const ret = eng.show(slide);
  for (let i = 0; i < 80 && !(eng.state().started || eng.state().failed); i++) await wait(50);
  // Looked at from the moment the slide is BROUGHT IN (18 Nov: a new slide is held, built and
  // unseen, under the frame before until its first pictures are in -- here, with no frame before,
  // under a stage that is not up yet, so nothing of it can be seen). Sampled inside that hold, the
  // first looks saw nothing whatever the act did, and the cheat runs below went red or green by
  // the page's timing. (The stage's own fade-in is past its first frame by the first look.)
  const brought = () => { const r = document.querySelector('.cs-slide.cs-t-act');
    return !!document.querySelector('.cs-stage.cs-up') && !!r && r.getAttribute('data-cut') !== 'hold'; };
  for (let i = 0; i < 150 && !brought(); i++) await wait(20);
  const cameUp = brought();
  await wait(60);
  const stage = document.querySelector('.cs-act-stage');
  function hiddenBy(e) {
    for (let x = e; x && x !== document.body; x = x.parentElement) {
      const cs = getComputedStyle(x);
      if (cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity === 0) return true;
    }
    return false;
  }
  function look(f) {
    const texts = [], keyed = [], made = [], media = [];
    if (stage && stage.isConnected) {
      const w = document.createTreeWalker(stage, NodeFilter.SHOW_TEXT);
      for (let n = w.nextNode(); n; n = w.nextNode()) {
        const t = n.nodeValue.replace(/\\s+/g, ' ').trim();
        if (!t) continue;
        const owner = n.parentElement.closest('[data-k]');
        texts.push({ t: t, key: owner ? owner.getAttribute('data-k') : null, kind: owner ? owner.className : null,
                     hidden: hiddenBy(n.parentElement) });
      }
      stage.querySelectorAll('[data-k]').forEach(e => keyed.push({ key: e.getAttribute('data-k'), hidden: hiddenBy(e) }));
      stage.querySelectorAll('*').forEach(e => {
        ['::before', '::after'].forEach(p => { const c = getComputedStyle(e, p).content;
          if (c && c !== 'none' && c !== 'normal' && c !== '""' && c !== "''") made.push(e.className + p + ' ' + c); });
        const bg = getComputedStyle(e).backgroundImage;
        if (bg && bg.indexOf('url(') >= 0) made.push(e.className + ' background ' + bg.slice(0, 80));
        if (e.tagName === 'IMG' || e.tagName === 'VIDEO' || e.tagName === 'IFRAME' || e.tagName === 'OBJECT' || e.tagName === 'EMBED')
          media.push({ tag: e.tagName, src: e.getAttribute('src'), from: e.getAttribute('data-src'), muted: e.muted === true });
        if (e.tagName === 'text' || e.tagName === 'tspan' || e.tagName === 'foreignObject' || e.tagName === 'INPUT' || e.tagName === 'TEXTAREA')
          made.push('element ' + e.tagName);
      });
    }
    seen.push({ f: f, state: eng.state(), texts: texts, keyed: keyed, made: made, media: media });
  }
  look(-1);
  for (const f of slide.__steps) { eng.progress(f); await wait(40); look(f); }
  eng.settle(); await wait(120); look(2);
  eng.clear(); await wait(60);
  return { ret: ret, seen: seen, cameUp: cameUp };
}"""


@pytest.fixture(scope="module")
def chromium():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright is not installed -- NOT checked here: that no act puts a word on the event screen "
                    "that is not one of its slide's checked strings, or shows a thing before its beat")
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True, args=["--disable-webgl", "--disable-3d-apis"])
    except Exception as exc:                               # no Chromium downloaded for playwright
        pw.stop()
        pytest.skip("headless Chromium is not installed (%s) -- NOT checked here: that no act puts a word on the "
                    "event screen that is not one of its slide's checked strings" % str(exc).splitlines()[0])
    yield browser
    browser.close()
    pw.stop()


def _page(browser, acts_dir: Path = ACTS):
    """A page with the engine and the acts, every request answered from this repo (no server,
    no network): the stylesheet, the engine, an act's files; pictures are a 1x1 PNG."""
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    said: list[str] = []
    page.on("console", lambda m: said.append(m.text) if m.type in ("warning", "error") else None)
    page.on("pageerror", lambda e: said.append("page error: %s" % e))

    def answer(route):
        path = route.request.url.split("//", 1)[1].split("/", 1)[1].split("?")[0]
        if path == "":
            return route.fulfill(body=SHELL, content_type="text/html")
        if path.startswith("assets/ceremony-show/acts/"):
            file = acts_dir / path.rsplit("/", 1)[1]
        elif path in ("assets/ceremony-show.js", "assets/ceremony-show.css", "assets/ceremony-show/fonts/Inter.ttf") or re.fullmatch(
                r"assets/ceremony-show/fonts/Kantalad-(?:Regular|Medium|SemiBold|Bold)\.otf", path):
            file = ROOT / "client" / path
        elif re.fullmatch(r"(?:story|ceremony)-asset/[0-9a-f]{64}", path):
            return route.fulfill(body=PNG, content_type="image/png")
        else:
            return route.fulfill(status=404, body="")
        if not file.is_file():
            return route.fulfill(status=404, body="")
        kind = {".css": "text/css", ".ttf": "font/ttf", ".otf": "font/otf"}.get(file.suffix, "text/javascript")
        return route.fulfill(body=file.read_bytes(), content_type=kind)

    page.route("**/*", answer)
    page.goto("http://ceremony.test/")
    page.wait_for_function("() => !!window.CeremonyShow")
    return page, said


def _walk(page, payload: dict) -> dict:
    ats = sorted({b["at"] for b in payload["beats"]})
    steps = sorted({0.0} | {max(0.0, round(a - 0.004, 4)) for a in ats} | {min(1.0, round(a + 0.004, 4)) for a in ats} | {1.0})
    return page.evaluate(WALK, {**payload, "__steps": steps})


def _on_screen_problems(payload: dict, got: dict, said: list[str]) -> list[str]:
    """What a run of an act put on the screen that its slide did not give it."""
    bad: list[str] = []
    allowed: dict[str, list[str]] = {k: [v] for k, v in payload["texts"].items()}
    for key, fig in payload["figures"].items():
        allowed[key] = [fig[f] for f in ("lead", "value", "unit", "label") if f in fig]
    for key, pic in payload["pictures"].items():
        allowed[key] = [pic["caption"]] if "caption" in pic else []
    files = {p.get(f) for p in payload["pictures"].values() for f in ("src", "hires")} | {v["src"] for v in payload["videos"].values()}
    first: dict[str, float] = {}
    for beat in payload["beats"]:
        for key in ([beat["key"]] if isinstance(beat.get("key"), str) else beat.get("key") or []):
            first.setdefault(key, beat["at"])
    if got["ret"] is not True:
        bad.append("show() refused the slide")
    if got.get("cameUp") is not True:
        bad.append("the slide was never brought in (held for 3 s)")
    for look in got["seen"]:
        state, f = look["state"], look["f"]
        if state.get("failed") or not state.get("started"):
            bad.append("the act is down at %s: %s" % (f, "; ".join(said) or "it never started"))
            break
        for text in look["texts"]:
            if text["key"] is None:
                bad.append("words outside a factory at %s: %r" % (f, text["t"]))
            elif not any(text["t"] in whole for whole in allowed.get(text["key"], [])):
                bad.append("words that are not this slide's %r at %s: %r" % (text["key"], f, text["t"]))
        for item in look["keyed"]:
            if item["key"] in first and f < first[item["key"]] and not item["hidden"]:
                bad.append("%r is on the screen at %s, before its beat at %s" % (item["key"], f, first[item["key"]]))
        bad += ["drawn by CSS at %s: %s" % (f, m) for m in look["made"]]
        for media in look["media"]:
            # (a picture keyed on black is the core's copy of a named file, in memory: blob: from that file)
            src = media["from"] if str(media["src"]).startswith("blob:") else media["src"]
            if media["tag"] not in ("IMG", "VIDEO") or src not in files:
                bad.append("a file the slide did not name at %s: %s %s" % (f, media["tag"], media["src"]))
            if media["tag"] == "VIDEO" and not media["muted"]:
                bad.append("a clip with sound at %s" % f)
    return bad


def _act_slides() -> list[str]:
    out = []
    for path in sorted(SLIDES_DIR.glob("*.json")):
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("template") == "act":
                out.append(path.stem)
        except ValueError:
            pass
    return out


@pytest.mark.parametrize("cue_id", _act_slides())
def test_every_act_slide_runs_in_a_real_page_and_shows_only_what_the_slide_gave_it(cue_id, real, chromium):
    """The dynamic half of the scan: the act's own code, with its own slide, in Chromium --
    every text on the screen is one of the slide's checked strings inside the element a
    factory made for it, nothing made for a beat's key is seen before that beat, no CSS
    draws content, no file is loaded that the slide did not name, and the act does not throw."""
    payload = cs.for_cue(cue_id, drafts=True)
    assert payload is not None, "%s is not served: %s" % (cue_id, cs.problems(real["slides"][cue_id], event_script.cue(cue_id)))
    page, said = _page(chromium)
    try:
        got = _walk(page, payload)
    finally:
        page.close()
    assert _on_screen_problems(payload, got, said) == [], cue_id
    assert got["seen"][-1]["state"]["settled"] is True


# Act time runs WARP times faster than the test waits (requestAnimationFrame's clock only: an
# act's typing runs on it) -- a wall-clock typer shows its whole text within one hold.
WARP = 25
TYPED = """async (slide) => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const eng = window.CeremonyShow, seen = [];
  eng.show(slide);
  for (let i = 0; i < 80 && !(eng.state().started || eng.state().failed); i++) await wait(50);
  function look(f) {
    document.querySelectorAll('.cs-act-stage .cs-typed').forEach(e => {
      const chars = e.querySelectorAll('.cs-tc');
      let on = 0; chars.forEach(c => { if (c.classList.contains('cs-on')) on++; });
      seen.push({ f: f, key: e.getAttribute('data-k'), n: chars.length, on: on, said: e.__said || null });
    });
  }
  for (const f of slide.__steps) { eng.progress(f); await wait(160); look(f); }
  eng.settle(); await wait(160); look(2);
  eng.clear(); await wait(60);
  return seen;
}"""


def _warped_page(chromium):
    """A page whose requestAnimationFrame clock runs WARP times faster than the test waits:
    an act's own tweens (driven by ctx.frame) land within one short hold."""
    page, said = _page(chromium)
    page.add_init_script("(() => { const r = window.requestAnimationFrame.bind(window);"
                         " window.requestAnimationFrame = fn => r(t => fn(t * %d)); })()" % WARP)
    page.reload()
    page.wait_for_function("() => !!window.CeremonyShow")
    return page, said


@pytest.mark.parametrize("cue_id", _act_slides())
def test_typed_text_never_runs_ahead_of_her_voice(cue_id, real, chromium):
    """Review 18 Nov: s06_l07's punchline was typed from "I'm sure" on a 1.5 s wall clock, and
    read before she said "first place". Held at each beat for ~4 s of act time, a typed text
    shows no more of itself than her voice has said of it -- and every typed text is a piece
    of her line (one the core can follow)."""
    payload = cs.for_cue(cue_id, drafts=True)
    assert payload is not None
    page, said = _warped_page(chromium)
    ats = sorted({b["at"] for b in payload["beats"]})
    steps = sorted({min(1.0, round(a + 0.004, 4)) for a in ats} | {min(1.0, round(a + 0.05, 4)) for a in ats})
    try:
        seen = page.evaluate(TYPED, {**payload, "__steps": steps})
    finally:
        page.close()
    bad = []
    for row in seen:
        if row["said"] is None:
            bad.append("typed text %r is not a piece of her line" % row["key"])
            continue
        if row["f"] > 1:
            continue
        a, b = row["said"]
        voice = max(0.0, min(1.0, (row["f"] - a) / (b - a)))
        if row["on"] > round(voice * row["n"]) + 1:
            bad.append("%r shows %d of %d letters at %s; her voice has said %d" % (
                row["key"], row["on"], row["n"], row["f"], round(voice * row["n"])))
    assert sorted(set(bad)) == [], cue_id


TYPED_AGAIN = """async () => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  // (the slide on the screen now: the one before is still fading out under it)
  const box = () => { const all = document.querySelectorAll('.cs-act-stage'); return all[all.length - 1]; };
  const eng = window.CeremonyShow, on = () => box().querySelectorAll('.cs-tc.cs-on').length;
  eng.register('typetwice', function (ctx) {
    var t = ctx.fx.typed(ctx.text('line', { split: 'chars' }));
    ctx.add(ctx.stage, t.el);
    return { beat: function (b) { if (b['do'] === 'type') t.play(1300); }, settle: function () { t.set(1); } };
  });
  const slide = { template: 'act', act: 'typetwice', texts: { line: 'Main common areas' }, figures: {}, pictures: {},
                  videos: {}, beats: [{ at: 0.1, do: 'type' }, { at: 0.5, do: 'type' }], options: {} };
  const out = {};
  eng.show(slide);
  eng.progress(0.2); await wait(500); out.first = on();                 // the first play is under way
  eng.progress(0.6); out.again = [];
  for (let i = 0; i < 6; i++) { await wait(25); out.again.push(on()); }   // a second beat plays it again
  await wait(1600); out.end = on();
  out.n = box().querySelectorAll('.cs-tc').length;
  eng.clear(); await wait(50);
  eng.show(slide);
  eng.progress(0.2); await wait(150);
  eng.settle(); out.settled = [];                                        // settle's set(1), a play still running
  for (let i = 0; i < 8; i++) { await wait(40); out.settled.push(on()); }
  eng.clear(); await wait(50);
  return out;
}"""


def test_a_typed_text_played_again_goes_on_from_its_letters_and_settle_stops_a_play(chromium):
    """Review 18 Nov: s05_l11 plays its title on two beats -- the second play started again from
    no letters (at "for this unit" the title dropped from 12 letters to 0 and retyped), and a
    play still running undid settle's set(1). A new play goes on from what is shown; set() from
    outside stops a play."""
    page, said = _page(chromium)
    try:
        got = page.evaluate(TYPED_AGAIN)
    finally:
        page.close()
    assert got["n"] == 15 and 3 <= got["first"] < 15, got             # (15 letters: spaces are not typed)
    assert min(got["again"]) >= got["first"], got                      # never a letter taken back
    assert got["end"] == 15, got
    assert got["settled"] == [15] * 8, got


CUT_OR_JOIN = """async () => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const eng = window.CeremonyShow;
  // an act with modes that hands its mode on (as emma_console / smarthome_brand), and one that hands nothing on
  eng.register('modal', function (ctx) {
    ctx.add(ctx.stage, ctx.text('line'));
    return { handover: function () { return { mode: ctx.opt('mode', 'a') }; } };
  });
  eng.register('plain', function (ctx) { ctx.add(ctx.stage, ctx.text('line')); return {}; });
  const slide = (act, mode) => ({ template: 'act', act: act, texts: { line: 'Main common areas' }, figures: {}, pictures: {},
                                  videos: {}, beats: [], options: mode ? { mode: mode } : {} });
  const seen = [];
  for (const [act, mode] of [['modal', 'a'], ['modal', 'b'], ['modal', 'b'], ['plain', null], ['plain', null]]) {
    eng.show(slide(act, mode));
    // (it comes up once it has been drawn, unseen: a few frames of this machine)
    for (let i = 0; i < 300 && Array.from(document.querySelectorAll('.cs-slide.cs-t-act')).pop().getAttribute('data-cut') === 'hold'; i++) await wait(5);
    const roots = Array.from(document.querySelectorAll('.cs-slide.cs-t-act'));
    const now = roots[roots.length - 1], before = roots.length > 1 ? roots[roots.length - 2] : null;
    seen.push({ act: act, mode: mode, comes: getComputedStyle(now).animationName, cut: now.getAttribute('data-cut'),
                goes: before ? getComputedStyle(before).animationName : null, went: before ? before.getAttribute('data-cut') : null,
                sheet: document.querySelector('.cs-gate').getAnimations().filter(a => a.playState === 'running').length });
    await wait(1500);                         // (the sheet of the line before is over: 1.2 s)
  }
  eng.clear();
  const last = Array.from(document.querySelectorAll('.cs-slide.cs-t-act')).pop();
  seen.push({ act: 'down', comes: null, cut: null, goes: getComputedStyle(last).animationName, went: last.getAttribute('data-cut'),
              sheet: document.querySelector('.cs-gate').getAnimations().filter(a => a.playState === 'running').length });
  await wait(1400);
  seen.push({ act: 'gone', left: document.querySelectorAll('.cs-slide').length });
  return seen;
}"""


def test_a_line_joins_the_one_before_only_when_that_one_hands_on_into_it(chromium):
    """Review 18 Nov: join or cut was decided by the act's NAME -- s04_l06 -> s04_l09,
    s06_l07 -> s07_l02, s08_l16 -> s09_l02 are one act in another mode, and the new frame
    popped in with no move. A join is the act going on in the mode it handed over (its
    handover()); anything else is THE CUT (18 Nov afternoon, owner: "ดูเรื่อง transition ด้วย"):
    the sheet of light crosses, the new shot's window opening behind it (cs-cut-in) as the old
    one's closes (cs-cut-out); a line going down to the logo goes the same way."""
    page, said = _page(chromium)
    try:
        seen = page.evaluate(CUT_OR_JOIN)
    finally:
        page.close()
    assert [(r["comes"], r["cut"], r["goes"], r["went"], r["sheet"]) for r in seen[:6]] == [
        ("cs-cut-in", "in", None, None, 1),                # the first line: the sheet brings it
        ("cs-cut-in", "in", "cs-cut-out", "out", 1),       # the same act in another mode: a cut
        ("none", "join", "none", "join", 0),               # the same act going on in its mode: a join (no sheet, the old only fades)
        ("cs-cut-in", "in", "cs-cut-out", "out", 1),       # another act
        ("cs-cut-in", "in", "cs-cut-out", "out", 1),       # the same act, but it hands nothing on: a cut
        (None, None, "cs-cut-out", "down", 1),             # down to the logo: the sheet takes it away
    ], seen
    assert seen[6] == {"act": "gone", "left": 0}


IN_A_SCENE = """async () => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const eng = window.CeremonyShow;
  eng.register('modal', function (ctx) {
    ctx.add(ctx.stage, ctx.text('line'));
    return { handover: function () { return { mode: ctx.opt('mode', 'a') }; } };
  });
  eng.register('plain', function (ctx) { ctx.add(ctx.stage, ctx.text('line')); return {}; });
  const slide = (act, mode) => ({ template: 'act', act: act, texts: { line: 'Main common areas' }, figures: {}, pictures: {},
                                  videos: {}, beats: [], options: mode ? { mode: mode } : {} });
  const run = async (act, mode, cue) => {
    eng.show(slide(act, mode), cue ? { cue: cue } : undefined);
    const roots = () => Array.from(document.querySelectorAll('.cs-slide.cs-t-act'));
    for (let i = 0; i < 300 && roots().pop().getAttribute('data-cut') === 'hold'; i++) await wait(5);
    const all = roots(), now = all[all.length - 1], before = all.length > 1 ? all[all.length - 2] : null;
    const sheet = document.querySelector('.cs-gate').getAnimations().filter(a => a.playState === 'running');
    const row = { cue: cue, cut: now.getAttribute('data-cut'), comes: getComputedStyle(now).animationName,
                  went: before ? before.getAttribute('data-cut') : null, goes: before ? getComputedStyle(before).animationName : null,
                  sheet: sheet.length, sheetMs: sheet.length ? sheet[0].effect.getTiming().duration : null,
                  comesMs: now.getAnimations().filter(a => a.effect.target === now).map(a => a.effect.getTiming().duration),
                  veil: getComputedStyle(now, '::after').animationName };
    // a hundred milliseconds in: a dissolve has no edge -- both shots whole across the screen, the
    // new one coming up over the old; the old one is still there, whole
    await wait(100);
    row.mid = { now: +getComputedStyle(now).opacity, before: before ? +getComputedStyle(before).opacity : null,
                left: Math.round(now.getBoundingClientRect().left), beforeIn: before ? before.isConnected : null };
    await wait(1500);
    row.after = { now: +getComputedStyle(now).opacity, beforeIn: before ? before.isConnected : null };
    return row;
  };
  const seen = [];
  seen.push(await run('plain', null, 's02_l03'));          // the first line: a scene begins -- the sheet
  seen.push(await run('modal', 'a', 's02_l05'));           // the same scene, another act: a dissolve
  seen.push(await run('modal', 'a', 's02_l09'));           // ... the act going on in its mode: still a join
  seen.push(await run('modal', 'b', 's03_l03'));           // a new scene: the sheet
  seen.push(await run('plain', null, 's03_l05'));          // the same scene again: a dissolve
  seen.push(await run('plain', null, null));               // no cue to read: taken as a new scene
  seen.push(await run('plain', null, 's05_backup_quote')); // after one without a scene: the sheet
  seen.push(await run('plain', null, 's05_l11'));          // s05_backup_quote is of scene 5
  eng.clear(); await wait(1200);
  return seen;
}"""


def test_inside_a_scene_a_line_dissolves_in_and_the_sheet_of_light_is_a_new_scenes(chromium):
    """Review 19 Nov: the same 0.56 s light-bar wipe came on ~16 handovers, ~2 screen widths a
    second -- the fastest thing in the film -- and some snapped dark UI to a bright picture
    (+80 luma in 0.3 s). Inside a scene (the cue's sNN_) the next line DISSOLVES in over 1.2 s,
    no hard edge, the shot before staying whole under it until it is covered; the sheet of
    light marks a scene change only, slower (1.2 s), the shot it uncovers graded through a veil.
    A join stays a join."""
    page, said = _page(chromium)
    try:
        seen = page.evaluate(IN_A_SCENE)
    finally:
        page.close()
    assert [w for w in said if "404" not in w] == [], said
    assert [(r["cue"], r["cut"], r["comes"], r["went"], r["goes"], r["sheet"]) for r in seen] == [
        ("s02_l03", "in", "cs-cut-in", None, None, 1),
        ("s02_l05", "blend", "cs-blend-in", "under", "cs-blend-out", 0),
        ("s02_l09", "join", "none", "join", "none", 0),
        ("s03_l03", "in", "cs-cut-in", "out", "cs-cut-out", 1),
        ("s03_l05", "blend", "cs-blend-in", "under", "cs-blend-out", 0),
        (None, "in", "cs-cut-in", "out", "cs-cut-out", 1),
        ("s05_backup_quote", "in", "cs-cut-in", "out", "cs-cut-out", 1),
        ("s05_l11", "blend", "cs-blend-in", "under", "cs-blend-out", 0),
    ], seen
    for r in seen:
        if r["cut"] == "in":
            assert r["sheetMs"] >= 800 and r["comesMs"] == [r["sheetMs"]] and r["veil"] == "cs-cut-veil", r   # slower; graded
        if r["cut"] == "blend":
            assert r["comesMs"] == [1200] and r["veil"] == "none", r                                         # 1.2 s, no veil, no sheet
            # no edge: the new shot is the whole box from its first frame, coming up over the old one, whole
            assert r["mid"]["left"] == 0 and 0 < r["mid"]["now"] < 0.5 and r["mid"]["before"] == 1 and r["mid"]["beforeIn"], r
            assert r["after"] == {"now": 1, "beforeIn": False}, r                                            # covered, then gone


TOGGLE = """async (slide) => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const eng = window.CeremonyShow, seen = [];
  eng.show(slide);
  for (let i = 0; i < 80 && !(eng.state().started || eng.state().failed); i++) await wait(50);
  const pos = () => { const t = document.querySelector('.cs-act-stage [class*="toggle"]');
    return t ? (String(t.className).match(/pos-(on|off|mid)/) || [null, null])[1] : 'none'; };
  seen.push(pos());
  for (const f of slide.__steps) { eng.progress(f); await wait(60); seen.push(pos()); }
  eng.settle(); await wait(120); seen.push(pos());
  eng.clear(); await wait(60);
  return seen;
}"""


@pytest.mark.parametrize("cue_id,before,after", [("s08_l06", "on", "on"), ("s08_l08", "on", "off"),
                                                ("s08_l10", "off", "off"), ("s08_l12", "off", "on"),
                                                ("s08_l14", "off", "off"), ("s08_l16", "off", "on")])
def test_a_request_line_never_shows_the_device_done(cue_id, before, after, real, chromium):
    """Review 18 Nov: on "Turning the lobby lights on now" the toggle went to its middle -- blue,
    read as on -- before "The lobby lights are on." The state changes on the line that says so."""
    payload = cs.for_cue(cue_id, drafts=True)
    page, said = _page(chromium)
    try:
        seen = page.evaluate(TOGGLE, {**payload, "__steps": [0.0, 0.3, 0.6, 0.9, 1.0]})
    finally:
        page.close()
    assert seen[0] == before and seen[-1] == after, (cue_id, seen)
    assert "mid" not in seen and set(seen) <= {before, after}, (cue_id, seen)


# Every state an act reaches, landed (transitions and animations take no time here), and where
# each keep-out box of a picture falls on the screen: a real person in the owner's pictures.
KEEP_OUT = """async (slide) => {
  const BOXES = slide.__boxes;
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const st = document.createElement('style');
  st.textContent = '*, *::before, *::after { transition-duration: 0s !important; transition-delay: 0s !important;'
    + ' animation-duration: 0s !important; animation-delay: 0s !important; }';
  document.head.appendChild(st);
  const eng = window.CeremonyShow, seen = [];
  eng.show(slide);
  for (let i = 0; i < 80 && !(eng.state().started || eng.state().failed); i++) await wait(50);
  const stage = document.querySelector('.cs-stage') || document.body;
  function shown(e) {
    let o = 1;
    for (let x = e; x && x !== document.body; x = x.parentElement) {
      const cs = getComputedStyle(x);
      if (cs.visibility === 'hidden' || cs.display === 'none') return 0;
      o *= +cs.opacity;
    }
    return o;
  }
  function clip(e) {
    const s = stage.getBoundingClientRect();
    let r = { l: Math.max(0, s.left), t: Math.max(0, s.top), r: Math.min(innerWidth, s.right), b: Math.min(innerHeight, s.bottom) };
    for (let x = e.parentElement; x && x !== document.body; x = x.parentElement) {
      const cs = getComputedStyle(x);
      if (cs.overflow !== 'visible' || cs.clipPath !== 'none') {
        const q = x.getBoundingClientRect();
        r = { l: Math.max(r.l, q.left), t: Math.max(r.t, q.top), r: Math.min(r.r, q.right), b: Math.min(r.b, q.bottom) };
      }
    }
    return r;
  }
  function look(f) {
    document.querySelectorAll('.cs-apic[data-k]').forEach(el => {
      const known = BOXES[el.getAttribute('data-k')];
      if (!known) return;
      const o = shown(el);
      if (o < 0.04) return;
      const img = el.querySelector('img') || el, q = img.getBoundingClientRect();
      if (q.width < 2 || q.height < 2) return;
      const cs = getComputedStyle(img), [W, H] = known.wh, fit = cs.objectFit === 'contain' ? 'contain' : 'cover';
      const pos = String(cs.objectPosition || '50% 50%').split(' ').map(v => /%$/.test(v) ? parseFloat(v) / 100 : 0.5);
      const k = fit === 'cover' ? Math.max(q.width / W, q.height / H) : Math.min(q.width / W, q.height / H);
      const dw = W * k, dh = H * k, ox = q.left + (q.width - dw) * pos[0], oy = q.top + (q.height - dh) * (pos[1] == null ? 0.5 : pos[1]);
      const c = clip(img);
      // covered: in the picture's own layout (before any transform -- a card turned in 3D has the
      // patch turned with it), a patch of the core holds the whole box
      const lw = img.offsetWidth, lh = img.offsetHeight;
      const lk = fit === 'cover' ? Math.max(lw / W, lh / H) : Math.min(lw / W, lh / H);
      const ldw = W * lk, ldh = H * lk, lox = (lw - ldw) * pos[0], loy = (lh - ldh) * (pos[1] == null ? 0.5 : pos[1]);
      // (where the patch really is: its frame's offset plus the frame's own 2D translate)
      const patches = Array.from(el.querySelectorAll('.cs-keepout')).filter(p => shown(p) >= o * 0.99).map(p => {
        const f = p.parentElement, m = new DOMMatrixReadOnly(getComputedStyle(f).transform === 'none' ? undefined : getComputedStyle(f).transform);
        const fl = f.offsetLeft + m.m41, ft = f.offsetTop + m.m42;
        return { l: fl + p.offsetLeft, t: ft + p.offsetTop, r: fl + p.offsetLeft + p.offsetWidth, b: ft + p.offsetTop + p.offsetHeight };
      });
      known.boxes.forEach(b => {
        const bx0 = ox + b[0] * dw, by0 = oy + b[1] * dh, bx1 = ox + b[2] * dw, by1 = oy + b[3] * dh;
        const x0 = Math.max(c.l, bx0), y0 = Math.max(c.t, by0), x1 = Math.min(c.r, bx1), y1 = Math.min(c.b, by1);
        if (!(x1 - x0 > 1 && y1 - y0 > 1)) return;
        const L = [lox + b[0] * ldw, loy + b[1] * ldh, lox + b[2] * ldw, loy + b[3] * ldh];
        const covered = patches.some(r => r.l <= L[0] + 1 && r.t <= L[1] + 1 && r.r >= L[2] - 1 && r.b >= L[3] - 1);
        seen.push({ f: f, key: el.getAttribute('data-k'), box: b, at: [Math.round(x0), Math.round(y0), Math.round(x1), Math.round(y1)],
                    px: Math.round(by1 - by0), covered: covered, opacity: Math.round(o * 100) / 100,
                    ...(covered ? {} : { layout: L.map(Math.round), patches: patches.map(r => [r.l, r.t, r.r, r.b]) }) });
      });
    });
  }
  look(0);
  for (const f of slide.__steps) { eng.progress(f); await wait(80); look(f); }
  eng.settle(); await wait(120); look(2);
  eng.clear(); await wait(60);
  return seen;
}"""


#: The tallest a keep-out box may stand on a 1080-p stage (the box is the person plus a quarter
#: each way): a far card or a whole facade, not a camera that pushed in on somebody.
MAX_KEEP_OUT_PX = 96


@pytest.mark.parametrize("cue_id", _act_slides())
def test_no_slide_shows_a_real_person_in_the_owners_pictures(cue_id, real, chromium):
    """Review 18 Nov: s06_l03's push-in landed on a seated woman, a photo cut-out in the egg
    pavilion. The index boxes every real person found in a picture (`people` / `keep_out`); at
    every beat, landed: a box on the stage is painted over by the core and stays small."""
    payload = cs.for_cue(cue_id, drafts=True)
    assert payload is not None
    ats = sorted({b["at"] for b in payload["beats"]})
    steps = sorted({0.0, 1.0} | {min(1.0, round(a + 0.004, 4)) for a in ats}
                   | {round((a + b) / 2, 4) for a, b in zip(ats, ats[1:] + [1.0])})
    slide = real["slides"][cue_id]
    boxes = {}
    for key, row in (slide.get("pictures") or {}).items():
        found = cs.keep_out(row["asset"])
        if found:
            pic = payload["pictures"][key]
            boxes[key] = {"boxes": found, "wh": [pic.get("hw", pic["w"]), pic.get("hh", pic["h"])]}
    page, said = _warped_page(chromium)
    try:
        seen = page.evaluate(KEEP_OUT, {**payload, "__steps": steps, "__boxes": boxes})
    finally:
        page.close()
    # a box on the stage: painted over by the core, and never pushed in on (MAX_KEEP_OUT_PX at 1080 p)
    bad = [row for row in seen if not row["covered"] or row["px"] > MAX_KEEP_OUT_PX]
    assert bad == [], cue_id


def test_a_candidate_with_no_real_person_check_is_refused_and_a_bad_box_drops_the_file(index):
    """`people` on every candidate (18 Nov): absent = nobody checked = never on the screen."""
    folder = index([])
    row = _candidate(folder, "nobody")
    row.pop("people")
    index([row])
    assert cs.act_asset_problem("nobody") == "people_not_checked"
    for people in (None, "none", [{"box": [0.5, 0.5, 0.4, 0.6]}], [{"box": [0.1, 0.1, 0.2]}], [{"box": [0, 0, 1.5, 1]}]):
        index([{**row, "people": people}])
        assert cs.act_asset_problem("nobody") == "people_not_checked", people
    index([{**row, "people": []}])
    assert cs.act_asset_problem("nobody") is None and "keep_out" not in cs._act_picture("nobody")
    index([{**row, "people": [{"box": [0.4, 0.5, 0.45, 0.6], "what": "a cut-out"}]}])
    assert cs.act_asset_problem("nobody") is None
    assert cs._act_picture("nobody")["keep_out"] == [[0.4, 0.5, 0.45, 0.6]]
    # a full-size file whose keep-out cannot be read is not used at all
    index([{**row, "people": [], "keep_out": [{"box": "the pavilion"}]}])
    assert cs.act_asset_problem("nobody") == "asset_excluded"


SITE_JS = """async (slide) => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const eng = window.CeremonyShow;
  eng.show(slide);
  for (let i = 0; i < 150 && !(eng.state().started || eng.state().failed); i++) await wait(20);
  const q = s => document.querySelector('.cs-act-stage ' + s);
  const at = f => { eng.progress(f); return wait(80).then(() => ({
    video: !!(q('.sitev video')), muted: !!(q('.sitev video') && q('.sitev video').muted),
    bk: q('.sitebk') ? q('.sitebk').className : null, v: q('.sitev') ? q('.sitev').className : null })); };
  const early = await at(0.05), past = await at(slide.__pool + 0.01);
  eng.clear(); await wait(60);
  return { early: early, past: past };
}"""


def test_the_owners_clips_are_on_their_cards_and_the_black_goes_with_the_pool(real, chromium):
    """Owner 22 Nov (review PDF p2 and p13, "เปลี่ยนเป็นวิดีโอนี้ วิดีโอ 1.mp4"): s02_l05's left card plays the clip,
    s06_l03 opens on it over a black sheet that goes with the pool's beat (the stage comes back under the panes).
    Review 22 Nov: the walk above loads every asset as a 1x1 PNG and checked neither -- a card left empty, a black
    sheet left over the whole line, a clip never played all passed."""
    card = cs.for_cue("s02_l05", drafts=True)
    page, said = _page(chromium)
    try:
        got = _walk(page, card)
    finally:
        page.close()
    clip = card["videos"]["site"]["src"]
    shown = [m for look in got["seen"] for m in look["media"] if m["tag"] == "VIDEO" and clip in (m["src"], m["from"])]
    assert shown and all(m["muted"] for m in shown), (clip, [look["media"] for look in got["seen"]][:3])
    line = cs.for_cue("s06_l03", drafts=True)
    pool = next(b["at"] for b in line["beats"] if "pool_tag" in (b.get("key") or []))
    page, said = _page(chromium)
    try:
        got = page.evaluate(SITE_JS, {**line, "__pool": pool})
    finally:
        page.close()
    assert got["early"]["video"] and got["early"]["muted"], got
    assert "gone" not in (got["early"]["bk"] or "gone") and "gone" not in (got["early"]["v"] or "gone"), got
    assert "gone" in got["past"]["bk"] and "gone" in got["past"]["v"], got


#: The Emma clips cut to the part of the frame she is ever in (23 Nov night): per act, the option that holds
#: the window, the box the clip is laid in, and that box's shape -- the dive / swim clips fill the 16:9
#: stage, the stage clip a 3:4 box.
CUT_CLIPS = {"emma_dive": ("window", ".cs-act-emma_dive .emma", 16 / 9),
             "emma_console": ("emma.window", ".cs-act-emma_console .ec-emb", 3 / 4)}


def _opt_at(options, path: str):
    for part in path.split("."):
        options = options.get(part) if isinstance(options, dict) else None
    return options


def _cut_clip_slides() -> list[str]:
    out = []
    for cue_id in _act_slides():
        slide = json.loads((SLIDES_DIR / (cue_id + ".json")).read_text(encoding="utf-8"))
        if slide.get("act") in CUT_CLIPS and "emma" in (slide.get("videos") or {}):
            out.append(cue_id)
    return out


def test_a_cut_clip_names_its_window_and_the_window_is_its_file(real):
    """23 Nov night, measured on the event notebook (Intel UHD, Chrome 157): Chrome decodes VP9 with alpha on
    the CPU, and the Emma clips cost it their whole frame -- 1920x1080 of pool where she is only ever in part of
    it, 720x960 drawn 588 px tall -- 35-43% of their frames dropped. They are cut to that part, and the act lays
    the file at `window` = [x, y, w, h, W, H] of its box. A file and a window that do not agree put her in the
    wrong place, or stretched, with no error anywhere: the window's w x h is the file's own size, it lies
    inside W x H, W:H is the box's shape, and a file that is not the box's shape has a window."""
    index = {r["id"]: r for r in json.loads((ROOT / "data" / "ceremony" / "assets" / "index.json").read_text(encoding="utf-8"))}
    cut = 0
    slides = {c: json.loads((SLIDES_DIR / (c + ".json")).read_text(encoding="utf-8")) for c in _cut_clip_slides()}
    for cue_id, slide in slides.items():
        path, _box, shape = CUT_CLIPS[slide["act"]]
        entry = index[slide["videos"]["emma"]["asset"]]
        win = _opt_at(slide.get("options") or {}, path)
        if win is None:
            assert "window" not in entry, (cue_id, entry["id"], "a cut file and no window: she is drawn stretched over the box")
            assert abs(entry["w"] / entry["h"] - shape) < 0.01, (cue_id, entry["id"], "a file cut smaller than its box, no window")
            continue
        cut += 1
        assert len(win) == 6 and all(isinstance(v, int) for v in win), (cue_id, win)
        x, y, w, h, W, H = win
        # (the place is the file's: scripts/cut_emma_clips.py writes it in the row -- review 23 Nov: a window of
        # the right size at the wrong place passed every check and put her elsewhere)
        assert win == entry.get("window"), (cue_id, win, entry["id"], entry.get("window"))
        assert (w, h) == (entry["w"], entry["h"]), (cue_id, win, entry["id"], entry["w"], entry["h"])
        assert 0 <= x and x + w <= W and 0 <= y and y + h <= H, (cue_id, win)
        assert abs(W / H - shape) < 0.001, (cue_id, win, shape)
    assert cut == 5, cut                     # s04_l11 / l13 / l15 (dive, swim) and s06_l05 / l07 (stage)
    # every cut is of the original pinned NOW: a re-rendered original (render_emma_*.py --register) leaves the
    # film on a cut of the old one -- make it again (scripts/cut_emma_clips.py --make <id>)
    for row in index.values():
        if "cut_of" in row:
            orig = index[row["cut_of"]]
            assert row.get("cut_of_sha256") == orig["sha256"], (row["id"], "cut from another %s" % orig["id"])
            assert row["window"][4:] == [orig["w"], orig["h"]] and row["window"][2:4] == [row["w"], row["h"]], row["id"]
            assert row["duration"] == orig["duration"] and row["fps"] in (orig["fps"], orig["fps"] / 2), row["id"]
    # the dive line loads the clip its next line plays (`ahead`, never seen): the same file, or the preload is wasted
    for cue_id, slide in slides.items():
        if "swim" in slide["videos"]:
            nxt = [s for s in slides.values() if s.get("continues") == cue_id]
            assert nxt and all(s["videos"]["emma"] == slide["videos"]["swim"] for s in nxt), (cue_id, slide["videos"])


def test_every_cut_clip_holds_her_whole_and_is_cut_from_the_pinned_original(real):
    """scripts/cut_emma_clips.py's own check: the original decoded with libvpx (its alpha), every pixel of her in
    every frame inside the window with room to spare, each cut row's window / size / rate / source as the script
    makes it, the files the bytes the index pins."""
    pytest.importorskip("numpy", reason="NOT checked here: that every cut Emma clip holds all of her")
    spec = importlib.util.spec_from_file_location("cut_emma_clips", ROOT / "scripts" / "cut_emma_clips.py")
    cut = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cut)
    from voice_line import find_ffmpeg
    try:
        ffmpeg = find_ffmpeg(None)
    except SystemExit:
        pytest.skip("no ffmpeg on this machine -- NOT checked: that every cut Emma clip holds all of her")
    # the clips are the files the index rows NAME, never <id>.webm: --make writes <id>.<sha12>.webm and removes
    # the clip it replaced (cut_emma_clips._encode_and_pin), so after any re-cut the look for <id>.webm skipped
    # this whole check -- "not on this machine", with every clip on it (24 Nov). None of them here = skipped, by
    # name; some here and some not = the index and the folder disagree and the film is a clip short: red, by name
    rows = {r["id"]: r for r in json.loads(cut.INDEX.read_text(encoding="utf-8"))}
    named = {rows[i]["file"] for c_id, c in cut.CUTS.items() for i in (c_id, c["from"]) if (rows.get(i) or {}).get("file")}
    gone = sorted(f for f in named if not (cut.ASSETS / f).is_file())
    if named and len(gone) == len(named):
        pytest.skip("none of the Emma clips the index names is on this machine (data/ceremony/assets is not in git): "
                    "%s -- NOT checked: that every cut Emma clip holds all of her" % ", ".join(gone))
    assert not gone, ("the index names Emma clips that are not in data/ceremony/assets, where the others are: %s -- "
                      "copy them in, or make them again (a cut: scripts/cut_emma_clips.py --make)" % ", ".join(gone))
    assert cut.check(ffmpeg) == []


CUT_JS = """async (slide) => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const eng = window.CeremonyShow;
  eng.show(slide);
  for (let i = 0; i < 150 && !(eng.state().started || eng.state().failed); i++) await wait(20);
  await wait(150);
  const box = document.querySelector(slide.__box), clip = box && box.querySelector(':scope > .cs-avid');
  const v = clip && clip.querySelector('video');
  const out = { started: eng.state().started === true, box: box ? [box.offsetWidth, box.offsetHeight] : null,
    clip: clip ? [clip.offsetLeft, clip.offsetTop, clip.offsetWidth, clip.offsetHeight] : null,
    video: v ? [v.offsetLeft, v.offsetTop, v.offsetWidth, v.offsetHeight, getComputedStyle(v).objectFit] : null };
  eng.clear(); await wait(60);
  return out;
}"""


def _cut_clip_problems(page, cue_id: str) -> list[str]:
    """Where the act laid the cut clip of `cue_id`, against its window (layout boxes: the act's squash /
    swim transforms are not part of it)."""
    slide = json.loads((SLIDES_DIR / (cue_id + ".json")).read_text(encoding="utf-8"))
    path, sel, _shape = CUT_CLIPS[slide["act"]]
    x, y, w, h, W, H = _opt_at(slide["options"], path)
    payload = cs.for_cue(cue_id, drafts=True)
    got = page.evaluate(CUT_JS, {**payload, "__box": sel})
    if not got["started"] or not got["box"] or not got["clip"] or not got["video"]:
        return ["%s: no clip in %s (%s)" % (cue_id, sel, got)]
    bw, bh = got["box"]
    want = [x / W * bw, y / H * bh, w / W * bw, h / H * bh]
    bad = ["%s: the clip at %s, its window is %s of a %sx%s box" % (cue_id, got["clip"], [round(v, 1) for v in want], bw, bh)
           ] if any(abs(a - b) > 1.01 for a, b in zip(got["clip"], want)) else []
    vx, vy, vw, vh, _fit = got["video"]
    if (vx, vy) != (0, 0) or abs(vw - got["clip"][2]) > 1 or abs(vh - got["clip"][3]) > 1:
        bad.append("%s: the video does not fill its window: %s in %s" % (cue_id, got["video"], got["clip"]))
    return bad


@pytest.mark.parametrize("cue_id", _cut_clip_slides())
def test_a_cut_clip_is_laid_exactly_at_its_window(cue_id, real, chromium):
    """The act half of the test above, in Chromium: the cut clip's box is its window of the box the whole
    frame was laid in (to a pixel at 1920x1080), and the video fills it -- so every pixel of her is where the
    whole-frame clip drew it."""
    page, said = _page(chromium)
    try:
        assert _cut_clip_problems(page, cue_id) == [], said
    finally:
        page.close()


def test_the_building_is_the_owners_final_entrance_and_its_floors_are_where_the_file_has_them():
    """Owner 23 Nov (his frame of s06_l03's "The building is 8 storeys high ..."): "เปลี่ยนเป็นภาพพื้นหลังเป็น
    ภาพใหม่" -- the old wavy-balcony render (rm_signature) gave way to his final Main Entrance 01, and the
    storeys, the roof and road levels drawn on it are measured on THAT file (options.fac_*): eight storeys, each
    line on the lit slab edge of the block between the wings (the brightest row within a third of a storey),
    the two lowest a lobby of two storeys under the six floors of flats."""
    slide = json.loads((SLIDES_DIR / "s06_l03.json").read_text(encoding="utf-8"))
    assert slide["pictures"]["facade"]["asset"] == "fin_entrance"
    o = slide["options"]
    floors, (x0, x1) = o["fac_floors"], o["fac_x"]
    assert len(floors) - 1 == int(slide["figures"]["stor"]["value"]) == 8
    assert all(a > b for a, b in zip(floors, floors[1:])), floors              # ground first, up to the roof
    assert 0 < o["fac_step_x"] < x0 < x1 < o["fac_dim_x"] < 100 and len(o["fac_box"]) == 3
    # the lobby is two storeys: its floor lies two of the flats' storeys under their lowest slab
    pitch = (floors[2] - floors[8]) / 6
    assert abs((floors[0] - floors[2]) / (2 * pitch) - 1) <= 0.03 and abs(floors[1] - (floors[0] + floors[2]) / 2) <= 0.1
    np = pytest.importorskip("numpy", reason="NOT checked here: that s06_l03's floor lines lie on the file's slabs")
    image = pytest.importorskip("PIL.Image", reason="NOT checked here: that s06_l03's floor lines lie on the file's slabs")
    entry = next(r for r in json.loads((ROOT / "data" / "ceremony" / "assets" / "index.json").read_text(encoding="utf-8"))
                 if r.get("id") == "fin_entrance")
    path = ROOT / "data" / "ceremony" / "assets" / entry["file"]
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
        pytest.skip("fin_entrance is not on this machine as the index pins it -- NOT checked: s06_l03's floor "
                    "lines against the file's own slabs")
    g = np.asarray(image.open(path).convert("L"), dtype=float)
    hgt, wid = g.shape
    row = g[:, round(x0 / 100 * wid):round(x1 / 100 * wid)].mean(axis=1)
    local = np.convolve(np.pad(row, 7, mode="edge"), np.ones(15) / 15, mode="same")[7:-7]
    lift = row - local                                                          # a thin lit line stands out of its rows
    for i in range(2, 9):                                                       # the six slabs and the roof
        y = floors[i] / 100 * hgt
        band = range(int(y) - 30, int(y) + 31)
        best = max(band, key=lambda j: lift[j])
        assert abs(best - y) <= 3 and lift[best] >= 10, (i, round(y, 1), best, round(float(lift[best]), 1))


FACADE_JS = """async (slide) => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const eng = window.CeremonyShow;
  eng.show(slide);
  for (let i = 0; i < 150 && !(eng.state().started || eng.state().failed); i++) await wait(20);
  for (const f of slide.__steps) { eng.progress(f); await wait(120); }
  await wait(1900);
  const q = s => document.querySelector('.cs-act-stage ' + s), all = s => Array.from(document.querySelectorAll('.cs-act-stage ' + s));
  const r = e => { const b = e.getBoundingClientRect(); return [b.left, b.top, b.right, b.bottom]; };
  const look = () => ({ stage: r(document.querySelector('.cs-act-stage')), pic: r(q('.fac .cs-apic')), svg: r(q('.fac .fsvg')),
    bands: all('.fac .sband').map(r), steps: all('.fac .step').map(r), lvl: all('.fac .lvl').map(r), dim: r(q('.fac .dimline')),
    tags: [r(q('.fac .tgroof')), r(q('.fac .tgroad'))] });
  const one = look(); await wait(700); const two = look();
  eng.clear(); await wait(60);
  return [one, two];
}"""


def test_the_buildings_lines_ride_its_picture_and_land_on_its_floors(real, chromium):
    """Owner 23 Nov: on the new front every storey band, dash, level line and the 23 m arrow is where the file
    has that floor (options.fac_*, in % of the picture) -- in the picture's own layer, so the core's slow wander
    of the picture (up to 0.8cu) and its zoom carry them along; the tags sit on their lines, and the road line is
    above the subtitle's box. Before: the lines stood in the box at 16:10 numbers of the old render."""
    payload = cs.for_cue("s06_l03", drafts=True)
    o = json.loads((SLIDES_DIR / "s06_l03.json").read_text(encoding="utf-8"))["options"]
    beats = {b["do"]: b["at"] for b in payload["beats"]}
    steps = sorted({a for a in beats.values() if a <= beats["road2"]} | {round(beats["road2"] + 0.004, 4)})
    assert steps[-1] < beats["indoor"]
    page, said = _page(chromium)
    try:
        looks = page.evaluate(FACADE_JS, {**payload, "__steps": steps})
    finally:
        page.close()
    fl, (x0, x1) = o["fac_floors"], o["fac_x"]
    for look in looks:
        (pl, pt, pr, pb), (sl, st, sr, sb) = look["pic"], look["stage"]
        fx, fy, cu = (lambda p: pl + p / 100 * (pr - pl)), (lambda p: pt + p / 100 * (pb - pt)), (sr - sl) / 100
        near = lambda a, b, tol=1.5: abs(a - b) <= tol
        assert all(near(a, b, 0.5) for a, b in zip(look["svg"], look["pic"])), look       # one layer with the picture
        assert len(look["bands"]) == len(look["steps"]) == 8, look
        for k, ((l, t, r, b), step) in enumerate(zip(look["bands"], look["steps"])):
            assert near(t, fy(fl[k + 1])) and near(b, fy(fl[k])) and near(l, fx(x0)) and near(r, fx(x1)), (k, look)
            assert near(step[0], fx(o["fac_step_x"])) and t <= step[1] < step[3] <= b, (k, step, look)
        roof, road = look["lvl"]
        assert near(roof[1], fy(fl[8])) and near(road[1], fy(fl[0])) and near(roof[0], fx(x0)), look
        dim = look["dim"]
        assert near(dim[1], fy(fl[8])) and near(dim[3], fy(fl[0])) and near(dim[0], fx(o["fac_dim_x"]), 2.5), look
        assert st < roof[1] and road[1] < st + 43.8 * cu, look                    # on the stage, above the subtitle's box
        for tag, line in zip(look["tags"], (roof, road)):
            assert abs((tag[1] + tag[3]) / 2 - line[1]) <= cu and tag[3] < st + 43.8 * cu, (tag, line)


def test_the_owners_front_is_pushed_no_further_than_its_pixels_allow():
    """Owner 22 Nov (review PDF p4): s02_l09's full-screen front is the owner's LINE_ALBUM picture, which exists at
    2048 px only -- its push is held to 1.12 at the most (`sig_cam`; the act's own default goes to 1.24). Review
    22 Nov: the act ignoring the option passed every test."""
    opts = json.loads((SLIDES_DIR / "s02_l09.json").read_text(encoding="utf-8"))["options"]
    assert len(opts["sig_cam"]) == 6 and max(opts["sig_cam"][2], opts["sig_cam"][5]) <= 1.12
    js = (ACTS / "living_photos.js").read_text(encoding="utf-8")
    assert "ctx.opt('sig_cam.' + i, d)" in js
    assert re.search(r"scene\(\{ pic: 'p_sig', from: \[SC\[0\], SC\[1\], SC\[2\]\], to: \[SC\[3\], SC\[4\], SC\[5\]\]", js)


def test_the_closing_hero_is_the_owners_new_front_held_near_its_pixels():
    """Audit 22 Nov (review PDF p4, "เปลี่ยนเป็นรูปนี้"): s10_l05's closing hero was the old entrance render while the
    wheel beside it showed the owner's new front -- now the new front (2048 px), its drift zoom near 1:1 (`end_zoom`),
    and the act never lets that zoom past its old 1.06. Review 22 Nov: undoing either passed every test."""
    slide = json.loads((SLIDES_DIR / "s10_l05.json").read_text(encoding="utf-8"))
    assert slide["pictures"]["endpic"]["asset"] == "fin_front_1120"
    assert slide["options"]["end_zoom"] <= 1.03
    js = (ACTS / "smarthome_brand.js").read_text(encoding="utf-8")
    assert "zoom: Math.min(1.06, ctx.opt('end_zoom', 1.06))" in js


def test_page_9s_words_are_upper_case_without_a_hyphen_or_unit():
    """Owner 20 Nov, review PDF p9: "ขอตัวพิมพ์ใหญ่ให้หมด และ 1 bedroom 2 bedroom ไม่ต้องมี - และคำว่า unit" -- every
    word of s04_l03 upper case (the figure keeps its UNITS: a number goes with its unit), and the bedroom labels, there
    and on s05_l06's card, with no hyphen and no "unit". Review 22 Nov: undoing it passed every test."""
    s4 = json.loads((SLIDES_DIR / "s04_l03.json").read_text(encoding="utf-8"))
    words = list(s4["texts"].values()) + [v for fig in s4["figures"].values() for v in fig.values() if isinstance(v, str)]
    assert words and all(w == w.upper() for w in words), words
    s6 = json.loads((SLIDES_DIR / "s05_l06.json").read_text(encoding="utf-8"))
    labels = [s4["texts"][k] for k in ("studios", "onebr", "twobr")] + [s6["texts"]["twobr"]]
    assert all(w == w.upper() and "-" not in w and "UNIT" not in w for w in labels), labels


def test_a_units_views_title_never_lands_on_the_one_going_out():
    """Audit 22 Nov: s05_l06 opened with s05_l03's title still on screen under its own for a moment ("What kind of
    view..." over "I'd suggest starting with"). The line going out takes its title and caption away in 0.2 s (the
    core's .cs-out), the new line's title waits 0.22 s. Review 22 Nov: undoing it passed every test."""
    css = (ACTS / "units_views.css").read_text(encoding="utf-8")
    js = (ACTS / "units_views.js").read_text(encoding="utf-8")
    assert re.search(r"\.cs-act-units_views\.cs-out \.top,[^{]*\.cs-out \.caps\s*\{[^}]*opacity:\s*0", css)
    assert re.search(r"\.cs-act-units_views \.top\.after\.on\s*\{[^}]*transition:[^};]*\.22s", css)
    assert "kick.box.classList.add('after')" in js


def test_the_real_index_checks_every_candidate_for_people_and_boxes_the_ones_found(real):
    rows = json.loads((ROOT / "data" / "ceremony" / "assets" / "index.json").read_text(encoding="utf-8"))
    # a candidate with no `people` is one nobody boxed (the gym renders' swimmers): refused, and no slide names it
    unboxed = [r["id"] for r in rows if r.get("candidate") and not isinstance(r.get("people"), list)]
    assert all(cs.act_asset_problem(aid) == "people_not_checked" for aid in unboxed), unboxed
    named = {row.get("asset") for path in SLIDES_DIR.glob("*.json")
             for group in ("pictures", "videos") for row in (json.loads(path.read_text(encoding="utf-8")).get(group) or {}).values()}
    assert not named & set(unboxed), named & set(unboxed)
    boxed = {r["id"] for r in rows if r.get("people")} | {r["from_pin"] for r in rows if r.get("keep_out")}
    assert {"c_lagoon_aerial_day", "c_lagoon_aerial_night", "c_entrance_aerial", "c_lagoon_path",
            "lagoon_scene58", "rm_signature", "liv_garden", "liv_lawn"} <= boxed
    for aid in boxed:
        assert cs.keep_out(aid), aid


# A figure on the screen, landed: its number and the words that say what it is (lead, unit,
# label) each whole on the stage -- not off its edge, not cut by a frame round them.
FIGS = """async (slide) => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const st = document.createElement('style');
  st.textContent = '*, *::before, *::after { transition-duration: 0s !important; transition-delay: 0s !important;'
    + ' animation-duration: 0s !important; animation-delay: 0s !important; }';
  document.head.appendChild(st);
  const eng = window.CeremonyShow, seen = [];
  eng.show(slide);
  for (let i = 0; i < 80 && !(eng.state().started || eng.state().failed); i++) await wait(50);
  const stage = document.querySelector('.cs-stage') || document.body;
  function shown(e) {
    let o = 1;
    for (let x = e; x && x !== document.body; x = x.parentElement) {
      const cs = getComputedStyle(x);
      if (cs.visibility === 'hidden' || cs.display === 'none') return 0;
      o *= +cs.opacity;
    }
    return o;
  }
  function visible(e) {
    const s = stage.getBoundingClientRect(), q = e.getBoundingClientRect();
    let r = { l: Math.max(0, s.left), t: Math.max(0, s.top), r: Math.min(innerWidth, s.right), b: Math.min(innerHeight, s.bottom) };
    for (let x = e.parentElement; x && x !== document.body; x = x.parentElement) {
      const cs = getComputedStyle(x);
      if (cs.overflow !== 'visible' || cs.clipPath !== 'none') {
        const c = x.getBoundingClientRect();
        r = { l: Math.max(r.l, c.left), t: Math.max(r.t, c.top), r: Math.min(r.r, c.right), b: Math.min(r.b, c.bottom) };
      }
    }
    const w = Math.max(0, Math.min(r.r, q.right) - Math.max(r.l, q.left)), h = Math.max(0, Math.min(r.b, q.bottom) - Math.max(r.t, q.top));
    return q.width * q.height > 0 ? (w * h) / (q.width * q.height) : 1;
  }
  function look(f) {
    document.querySelectorAll('.cs-act-stage .cs-fig').forEach(fig => {
      const o = shown(fig);
      if (o < 0.3) return;
      fig.querySelectorAll('.cs-fig-lead, .cs-fig-value, .cs-fig-unit, .cs-fig-label').forEach(part => {
        if (shown(part) < 0.3) return;
        const v = visible(part);
        if (v < 0.9) seen.push({ f: f, key: (fig.closest('[data-k]') || fig).getAttribute('data-k'),
                                 part: part.className, text: part.textContent, inside: Math.round(v * 100) / 100 });
      });
    });
  }
  // (a beat held ~6 s of act time: what an act sets a moment after a beat (ctx.after, real time) has happened)
  for (const f of slide.__steps) { eng.progress(f); await wait(250); look(f); }
  eng.settle(); await wait(120); look(2);
  eng.clear(); await wait(60);
  return seen;
}"""


@pytest.mark.parametrize("cue_id", _act_slides())
def test_a_figures_number_and_its_words_are_whole_on_the_stage(cue_id, real, chromium):
    """Review 18 Nov: "about" off the left edge (s03_l03), "UNITS IN TOTAL" cut to a sliver
    under 1,066 (s04_l03), "MAIN SWIMMING POOL" cut at the left (s06_l03). Whenever a figure
    shows, its number and every word that says what it is are at least 90% on the stage."""
    payload = cs.for_cue(cue_id, drafts=True)
    ats = sorted({b["at"] for b in payload["beats"]})
    steps = sorted({1.0} | {min(1.0, round(a + 0.004, 4)) for a in ats}
                   | {round((a + b) / 2, 4) for a, b in zip(ats, ats[1:] + [1.0])})
    page, said = _warped_page(chromium)
    try:
        seen = page.evaluate(FIGS, {**payload, "__steps": steps})
    finally:
        page.close()
    assert seen == [], cue_id


def test_the_lagoon_outline_follows_the_plans_own_water():
    """Review 18 Nov: the hand-placed outline zigzagged over lawn and paving and missed the west
    channel. It is traced from the plan's water pixels now: every point sits on the water's
    edge, and the lagoon's west end and east end are inside it.
    Owner 23 Nov ("เปลี่ยนเป็น plan นี้ ... สีโมชั่นในสระ ดีแล้วเอามาใช้กับภาพใหม่เลย"): the card's plan is his
    ground-floor plan, c_plan_ground_1123 -- the outline and its middle line are traced again on THAT
    picture's water (the old site plan's points left the new water at 16 of 218 places). On this plan the
    lagoon is the main water from its west end to its east end under its bridges; the pool by Building F
    and the channel by Building E are pools of their own, closed in by paths and lawn -- not the 155 metres."""
    cv2 = pytest.importorskip("cv2", reason="NOT checked here: that the lagoon outline lies on the plan's water")
    import numpy as np

    slide = json.loads((SLIDES_DIR / "s04_l06.json").read_text(encoding="utf-8"))
    assert slide["pictures"]["p_plan"]["asset"] == "c_plan_ground_1123"
    rows = json.loads((ROOT / "data" / "ceremony" / "assets" / "index.json").read_text(encoding="utf-8"))
    row = next(r for r in rows if r["id"] == "c_plan_ground_1123")
    plan = ROOT / "data" / "ceremony" / "assets" / row["file"]
    if not plan.is_file():
        pytest.skip("the plan file is not on this machine -- NOT checked: the outline against its water")
    im = cv2.imread(str(plan), cv2.IMREAD_UNCHANGED)
    hgt, wid = im.shape[:2]
    assert (wid, hgt) == (row["w"], row["h"]) and im.shape[2] == 4
    hsv = cv2.cvtColor(im[:, :, :3], cv2.COLOR_BGR2HSV).astype(int)
    # (this plan's turquoise water: hue 93-100, saturation 96-139 -- the old card's looser test, hue
    # 85-112 / saturation >= 50, takes the new plan's pale-blue 1-bedroom blocks and Building A's navy
    # for water too)
    water = ((hsv[:, :, 0] >= 86) & (hsv[:, :, 0] <= 104) & (hsv[:, :, 1] >= 80) & (hsv[:, :, 2] >= 70)
             & (im[:, :, 3] > 0)).astype(np.uint8)
    near = cv2.dilate(water, np.ones((25, 25), np.uint8))           # (12 px of the ~2000 px plan)
    # (s04_l11 left the plan 18 Nov: the owner wanted Emma diving into the pool -- act emma_dive,
    # an eye-level picture with no outline; s04_l13 / s04_l15 followed the same day: "ให้หุ่นว่ายไปมา",
    # the same shot going on)
    pts, cen = slide["options"]["lagoon"], slide["options"]["centre"]
    off = [pt for pt in pts if not near[min(hgt - 1, int(pt[1] / 100 * hgt)), min(wid - 1, int(pt[0] / 100 * wid))]]
    assert len(pts) >= 100 and off == [], (len(pts), off[:5])
    poly = np.array([[pt[0] / 100 * wid, pt[1] / 100 * hgt] for pt in pts], np.float32)
    # a smooth line, not a zigzag: the hand-placed one turned sharper than 60 degrees 35 times
    v = np.roll(poly, -1, 0) - poly
    ang = np.arctan2(v[:, 1], v[:, 0])
    turn = np.abs((np.roll(ang, -1) - ang + np.pi) % (2 * np.pi) - np.pi)
    assert int((turn > np.radians(60)).sum()) <= 3, int((turn > np.radians(60)).sum())
    # it holds the lagoon's water (the water joined over its bridges and islands): nearly all of it
    # inside, and little else -- most of what the outline holds is water
    fill = np.zeros((hgt, wid), np.uint8)
    cv2.fillPoly(fill, [poly.round().astype(np.int32)], 1)
    body = cv2.morphologyEx(water, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    joined = cv2.morphologyEx(body, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (45, 45)))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(joined, connectivity=8)
    lagoon = body & (labels == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))).astype(np.uint8)
    assert (lagoon & fill).sum() / lagoon.sum() >= 0.95
    assert (body & fill).sum() / fill.sum() >= 0.75
    # its west end (by the egg pavilion) and its east end (by Building B) are inside; the pool by
    # Building F and the channel by Building E are not
    for x, y in ((0.20, 0.60), (0.85, 0.46), (0.26, 0.30), (0.14, 0.40)):
        assert water[int(y * hgt), int(x * wid)], (x, y)          # (each place is water on this picture)
    assert cv2.pointPolygonTest(poly, (0.20 * wid, 0.60 * hgt), False) >= 0
    assert cv2.pointPolygonTest(poly, (0.85 * wid, 0.46 * hgt), False) >= 0
    assert cv2.pointPolygonTest(poly, (0.26 * wid, 0.30 * hgt), False) < 0
    assert cv2.pointPolygonTest(poly, (0.14 * wid, 0.40 * hgt), False) < 0
    # the middle line runs down the lagoon, west end to east end (its glints and rings stand on it):
    # inside the outline and on the water (or a bridge over it: within 12 px of water) at every point
    assert len(cen) >= 20 and cen[0][0] < 30 < 80 < cen[-1][0]
    astray = [p for p in cen if cv2.pointPolygonTest(poly, (p[0] / 100 * wid, p[1] / 100 * hgt), False) < 0
              or not near[min(hgt - 1, int(p[1] / 100 * hgt)), min(wid - 1, int(p[0] / 100 * wid))]]
    assert astray == [], astray


OUTLINE = """async (slide) => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const eng = window.CeremonyShow;
  eng.show(slide);
  for (let i = 0; i < 80 && !(eng.state().started || eng.state().failed); i++) await wait(50);
  eng.settle(); await wait(200);
  const out = { routes: Array.from(document.querySelectorAll('.cs-act-stage svg.cs-route')).map(e => e.getAttribute('class')),
                plan: !!document.querySelector('.cs-act-stage .lp-plan') };
  eng.clear(); await wait(60);
  return out;
}"""


@pytest.mark.parametrize("cue_id,outline", [("s04_l06", True), ("s04_l13", False), ("s04_l15", False)])
def test_the_lagoon_outline_is_drawn_only_over_the_plan(cue_id, outline, real, chromium):
    """Alone on navy the outline is a shape nobody can read (review 18 Nov): with the plan it is
    drawn, without it neither the outline nor its centre line is."""
    payload = cs.for_cue(cue_id, drafts=True)
    page, said = _page(chromium)
    try:
        got = page.evaluate(OUTLINE, payload)
    finally:
        page.close()
    plain = [c for c in got["routes"] if not re.search(r"lp-(trail|ringline|wake)", c)]
    assert got["plan"] is outline and bool(plain) is outline, (cue_id, got)


POOL_CARD = """async (slide) => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const eng = window.CeremonyShow;
  eng.show(slide);
  for (let i = 0; i < 80 && !(eng.state().started || eng.state().failed); i++) await wait(50);
  eng.progress(slide.__at); await wait(400);
  const plan = document.querySelector('.cs-act-stage .lp-plan');
  const out = { plan: !!plan };
  if (plan) {
    // (its layout box: the shot round it may be part-way through a scale)
    out.ratio = plan.offsetWidth / plan.offsetHeight;
    out.boxes = Array.from(plan.querySelectorAll('svg.cs-route')).map(s => s.getAttribute('viewBox'));
    const rip = plan.querySelector('.lp-ripple');
    out.ripple = rip ? [rip.style.left, rip.style.top] : null;
  }
  eng.clear(); await wait(60);
  return out;
}"""


def test_the_pool_card_takes_its_plans_shape_and_the_rings_stand_on_its_water(real, chromium):
    """Owner 23 Nov: the "155 metres long" card shows his ground-floor plan (1984 x 758). The act had the
    old site plan's 1963 x 773 written into the card's height and into the outline's layout, and the rings'
    place written into its stylesheet: the outline traced on the new picture would have been drawn
    stretched, the rings on whatever the old place is on the new plan. The card is the picture's own
    shape, the outline is laid out in it, and the rings stand on the water's middle line."""
    payload = cs.for_cue("s04_l06", drafts=True)
    pic = payload["pictures"]["p_plan"]
    at = next(b["at"] for b in payload["beats"] if b.get("do") == "length")
    page, said = _page(chromium)
    try:
        got = page.evaluate(POOL_CARD, {**payload, "__at": min(1.0, at + 0.01)})
    finally:
        page.close()
    aspect = pic["w"] / pic["h"]
    assert got["plan"] and abs(got["ratio"] / aspect - 1) < 0.005, (got, aspect)
    assert got["boxes"] and all(abs(1000 / float(b.split()[3]) / aspect - 1) < 0.002 for b in got["boxes"]), got
    cen = payload["options"]["centre"]
    mid = cen[round((len(cen) - 1) / 3)]
    assert got["ripple"] and all(got["ripple"]), got        # (placed by the act, not the stylesheet's old spot)
    assert [float(v.rstrip("%")) for v in got["ripple"]] == pytest.approx(mid, abs=0.01), (got, mid)


LOGO_OPENING = """async (slide) => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const eng = window.CeremonyShow;
  eng.show(slide);
  for (let i = 0; i < 80 && !(eng.state().started || eng.state().failed); i++) await wait(50);
  const brought = () => { const r = document.querySelector('.cs-slide.cs-t-act');
    return !!document.querySelector('.cs-stage.cs-up') && !!r && r.getAttribute('data-cut') !== 'hold'; };
  for (let i = 0; i < 150 && !brought(); i++) await wait(20);
  await wait(300);
  function shown(e) {
    let o = 1;
    for (let x = e; x && x !== document.body; x = x.parentElement) {
      const cs = getComputedStyle(x);
      if (cs.visibility === 'hidden' || cs.display === 'none') return 0;
      o *= +cs.opacity;
    }
    return o;
  }
  const stage = document.querySelector('.cs-act-stage');
  const logo = stage.querySelector('.cs-apic[data-k="logo"]');
  const open = { drawing: stage.querySelectorAll('.lp-sksvg, .lp-band, .lp-pen').length, logo: !!logo,
                 shown: logo ? Math.round(shown(logo) * 100) / 100 : 0, keyed: !!(logo && logo.classList.contains('cs-keyed')),
                 bg_off: document.querySelector('.cs-stage').classList.contains('cs-bg-off'), did: eng.state().did.slice() };
  eng.settle(); await wait(2800);                       // (past the end page's 1.8 s and its fade, had there been one)
  const root = stage.querySelector('.cs-act-living_photos') || document.querySelector('.cs-act-living_photos');
  const end = { ending: !!(root && root.classList.contains('lp-ending')), endlogo: !!document.querySelector('.cs-act-stage .lp-endlogo') };
  eng.clear(); await wait(60);
  return { open: open, end: end };
}"""


def test_the_pool_jokes_set_up_opens_on_the_logo_and_hands_on_with_no_logo_page(real, chromium):
    """Owner 23 Nov on s04_l09 ("ฉากนี้ ... ให้เปลี่ยนเป็นโลโก้ ตอนแรก"): it opened on the drawn wireframe of
    Building A -- now it opens on the logo (the opening clip's last frame, keyed on black, on the stage's
    light), before her first word, with no drawing anywhere. And as s04_l11 (the punchline) follows at once,
    the line no longer goes to a logo page of its own when it is over: its last frame is what the
    punchline comes in from."""
    slide = json.loads((SLIDES_DIR / "s04_l09.json").read_text(encoding="utf-8"))
    assert slide["pictures"]["logo"]["asset"] == "c_logo_end" and slide["options"].get("open") == "logo"
    assert "end" not in slide["options"]
    payload = cs.for_cue("s04_l09", drafts=True)
    page, said = _page(chromium)
    try:
        got = page.evaluate(LOGO_OPENING, payload)
    finally:
        page.close()
    assert got["open"]["did"] == [], got                   # (the opening: no beat of hers yet)
    assert got["open"]["logo"] and got["open"]["shown"] >= 0.5 and got["open"]["drawing"] == 0, got   # (the stage still fading up)
    assert got["open"]["bg_off"] is False, got             # (the logo stands on the stage's own light)
    assert got["end"] == {"ending": False, "endlogo": False}, got
    assert got["open"]["keyed"], got                        # (its black taken out by the core: no box round it)


STAGE_LIGHT = """async (slide) => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const eng = window.CeremonyShow;
  eng.show(slide);
  for (let i = 0; i < 80 && !(eng.state().started || eng.state().failed); i++) await wait(50);
  for (let i = 0; i < 150 && !document.querySelector('.cs-stage.cs-up'); i++) await wait(20);
  await wait(300);
  const st = document.querySelector('.cs-stage');
  const out = { failed: !!eng.state().failed, cls: Array.from(st.classList).filter(c => /^cs-(up|bg-)/.test(c)).sort() };
  eng.clear(); await wait(60);
  return out;
}"""


def test_the_map_of_the_place_stands_on_no_stage_background(real, chromium):
    """23 Nov, the event notebook (integrated GPU): s03_l03 at 21-26 fps, the worst of the film. The map
    covers the whole screen (the owner's pictures, the sea's flat colour to every edge, the fog 97% opaque
    over the horizon), and the stage's living background behind it -- nine moving layers, four offscreen
    passes and ~80 tiles a frame -- was drawn for nobody. It is off (hidden and paused) under the map."""
    payload = cs.for_cue("s03_l03", drafts=True)
    assert payload["act"] == "location_site" and payload["options"]["mode"] == "map"
    page, said = _page(chromium)
    try:
        got = page.evaluate(STAGE_LIGHT, payload)
    finally:
        page.close()
    assert not got["failed"] and got["cls"] == ["cs-bg-off", "cs-up"], (got, said)


def test_the_worked_example_runs_in_a_real_page(example, chromium):
    payload = cs.for_cue("s04_l11", drafts=True)
    assert payload["act"] == "example_panel"
    page, said = _page(chromium)
    try:
        got = _walk(page, payload)
    finally:
        page.close()
    assert _on_screen_problems(payload, got, said) == []
    assert got["seen"][0]["state"]["waiting"] == ["length"] and got["seen"][-1]["state"]["did"] == ["figure"]


CHEATS = {
    "words": "var e = ctx.el('x'); e.textContent = 'Sky Pool on the roof'; ctx.add(ctx.stage, e);",
    "early": "f.classList.remove('cs-await');",
    "early_style": "f.style.setProperty('opacity', '1', 'important'); f.style.setProperty('visibility', 'visible', 'important');",
    "css_words": "var s = document.createElement('style'); s.textContent = '.cs-act-cheat .x::after { content: \"Sky Pool\"; }'; document.head.appendChild(s); ctx.add(ctx.stage, ctx.el('x'));",
    "picture": "var i = document.createElement('img'); i.src = '/assets/emma/secret.png'; ctx.add(ctx.stage, i);",
    "throws": "ctx.nope();",
}


@pytest.mark.parametrize("name", sorted(CHEATS))
def test_the_run_catches_an_act_that_goes_round_the_factories(name, example, chromium, tmp_path):
    """The bad cases, built here: each cheat is refused by the source scan AND seen by the run.
    (18 Nov: the opening's picture comes late on purpose -- the hall's Wi-Fi -- so the new slide is
    held for the whole hold under a stage not yet up: a run that looked inside the hold saw nothing
    and missed `early` / `early_style` whenever the page was slow. The run looks from bring-in on.)"""
    import time

    honest = ("CeremonyShow.register('cheat', function (ctx) { var t = ctx.text('line'), f = ctx.figure('length'), "
              "p = ctx.picture('pool'); ctx.add(ctx.stage, p.el, t, f); %s return {}; });")
    payload = {**cs.for_cue("s04_l11", drafts=True), "act": "cheat", "css": False, "v": "000000000000"}
    assert "pool" in payload["pictures"] and not any(b.get("key") == "pool" for b in payload["beats"])   # of the opening
    folder = tmp_path / "acts"
    folder.mkdir()

    def late(route):
        time.sleep(0.7)                                                  # past the hold's bound (480 ms)
        route.fulfill(body=PNG, content_type="image/png")

    def run(code: str) -> list[str]:
        (folder / "cheat.js").write_text(honest % code, encoding="utf-8")
        page, said = _page(chromium, folder)
        page.route(re.compile(r".*/(?:story|ceremony)-asset/[0-9a-f]{64}$"), late)
        try:
            return _on_screen_problems(payload, _walk(page, payload), said)
        finally:
            page.close()

    assert run("") == []                                                 # the honest one passes
    assert run(CHEATS[name]) != [], name
    if name != "throws":
        assert js_problems("cheat", honest % CHEATS[name]) != [], name


# ---- her renderer rests under the ceremony's screen ----------------------------------------------

def test_the_hidden_3d_emma_draws_nothing_while_the_ceremonys_screen_is_up():
    """html.mirror-show hides her with CSS, which stops no WebGL: measured 17 Nov on this PC
    (RTX 5060, headless Chromium, the example act up) ~4,185 draw calls a second with her
    loop running hidden, 60 with it resting. Her own loop asks -- not the page's shared
    message handlers."""
    mascot = (ROOT / "client" / "assets" / "emma-3d-lab" / "mascot.js").read_text(encoding="utf-8")
    loop = mascot[mascot.index("renderer.setAnimationLoop(() => {"):]
    loop = loop[:loop.index("});")]
    rest = "if (document.documentElement.classList.contains('mirror-show')) { clock.getDelta(); return; }"
    assert rest in loop and loop.index(rest) < loop.index("renderStep(")
    assert "if (document.hidden) { clock.getDelta(); return; }" in loop                   # the older rest stays
    html = (ROOT / "client" / "voice-preview.html").read_text(encoding="utf-8")
    assert "/assets/emma-3d-lab/mascot.js?v=20261124-film" in html                        # a new address: no stale copy
    # the class exists only on the room screen, in the ceremony's show mode
    assert html.count("root.classList.toggle('mirror-show', !!on);") == 1
    assert "mirror-show" not in (ROOT / "client" / "assets" / "emma-story.js").read_text(encoding="utf-8")


# ---- the preview: where a designer looks ---------------------------------------------------------

def test_the_preview_can_look_at_parallax_with_webgl_and_prints_what_an_act_reported():
    """`--webgl` asks for a page on this PC's GPU (the default has none: a headless page's
    software renderer makes every frame late), and an act that is "down" is said in the
    terminal of whoever is shooting it."""
    source = (ROOT / "scripts" / "preview_ceremony_show.py").read_text(encoding="utf-8")
    assert 'ap.add_argument("--webgl", action="store_true"' in source
    assert ('flags = (["--ignore-gpu-blocklist", "--enable-gpu", "--use-angle=d3d11"] if args.webgl\n'
            '                     else ["--disable-webgl", "--disable-3d-apis"])') in source
    assert '"--autoplay-policy=no-user-gesture-required"] + flags)' in source
    assert 'page.on("console"' in source and '"ceremony-show" in m.text' in source and 'page.on("pageerror"' in source
    # an act slide goes through the same door as any slide: the checks, drafts only here
    assert "ceremony_slides.for_cue(cue[\"id\"], lang=\"en\", drafts=True)" in source


# ---- the stage's light, the hand-over, a wipe, a wheel, a clip's handle ---------------------------

FX2 = """
CeremonyShow.register('probe', function (ctx) {
  var fx = ctx.fx, call = window.__call = {}, log = window.__log;
  function tag(e, name) { e.setAttribute('data-t', name); return e; }
  log.push(['prev', ctx.prev]);
  tag(ctx.stage, 'box');
  var wipe = fx.wipe(ctx.el('over'), { dir: window.__flags.dir || 'right' });
  tag(wipe.el, 'wipe'); tag(wipe.el.children[0], 'wipein');
  var wheel = fx.wheel([ctx.el('a'), ctx.el('b'), ctx.el('c'), ctx.el('d'), 'not a card'], { radius: 20 });
  tag(wheel.el.children[0], 'ring');
  wheel.slots.forEach(function (slot, i) { tag(slot, 'slot' + i); });
  var sat = tag(fx.sat(ctx.figure('n')), 'sat'), free = tag(fx.sat(ctx.text('title')), 'free');
  var plx = fx.parallax(ctx.picture('pic'));
  tag(plx.el, 'plx');
  var v = ctx.video('clip');
  ctx.add(ctx.stage, wipe.el, wheel.el, sat, free, plx.el, v.el);
  call.wipe = function (m, f, ms) { wipe[m](f, ms); return wipe.at(); };
  call.wheel = function (i, ms) { wheel.turn(i, ms); return wheel.at(); };
  call.light = function (level, ms) { ctx.light(level, ms); };
  call.bg = function (mode) { ctx.bg(mode); };
  call.video = function (m, x) { return v[m](x); };
  return { handover: function () { if (window.__flags.sulk) throw new Error('no'); return { wheel: wheel.at(), note: 'kept' }; } };
});
"""


def test_the_stages_light_a_wipe_a_wheel_and_a_clips_handle():
    step = lambda name, *args: {"call": name, "args": list(args)}          # noqa: E731
    steps = [
        # (the slide has a clip in its opening: it comes up when the clip has a frame, or after
        # the hold -- no clip ever has one here; an act's timers count from that moment)
        {"show": PAGE_SLIDE}, {"tick": 600},                                                   # 0, 1
        step("wipe", "set", 0.25), step("wipe", "to", 1, 800), {"tick": 900},                   # 2, 3, 4
        step("wheel", 2, 600), step("wheel", 5, 0),                                             # 5, 6
        step("light", 0.2, 500), step("bg", "dim"), step("light", 1, 0),                        # 7, 8, 9
        step("video", "seek", 3), step("video", "rate", 20), step("video", "time"),             # 10, 11, 12
        {"progress": 0.35}, {"progress": 0.65},                                                 # 13, 14
        step("light", 0.3, 200), {"show": {**PAGE_SLIDE, "options": {"second": True}}}, {"tick": 600},      # 15, 16, 17
    ]
    got = _acts({"x": {"acts": {"probe": FX2}, "steps": steps}})["x"]["seen"]
    assert got[1]["state"]["failed"] is False, got[1]["warnings"]
    # ---- a wipe by transform alone: the clip box one way, what is in it the other -- they cancel
    assert got[1]["styles"]["wipe"] == {"transition": "none", "transform": "translateX(-100%)"}
    assert got[1]["styles"]["wipein"] == {"transition": "none", "transform": "translateX(100%)"}
    assert got[1]["classes"]["wipe"] == "cs-wipe cs-wipe-right"
    assert got[2]["styles"]["wipe"]["transform"] == "translateX(-75%)" and got[2]["styles"]["wipein"]["transform"] == "translateX(75%)"
    assert got[2]["ret"] == 0.25 and "cs-wiping" in got[2]["classes"]["wipe"]            # the light at the cut, while there is a cut
    move = "transform 800ms cubic-bezier(.65, 0, .35, 1)"
    assert got[3]["styles"]["wipe"] == {"transition": move, "transform": "translateX(0%)"}
    assert got[3]["styles"]["wipein"] == {"transition": move, "transform": "translateX(0%)"}
    assert "cs-wiping" in got[3]["classes"]["wipe"] and "cs-wiping" not in got[4]["classes"]["wipe"]
    down = _acts({"x": {"acts": {"probe": FX2}, "flags": {"dir": "up"}, "steps": [{"show": PAGE_SLIDE}, {"tick": 100}]}})["x"]["seen"][1]
    assert down["styles"]["wipe"]["transform"] == "translateY(100%)" and down["styles"]["wipein"]["transform"] == "translateY(-100%)"
    # ---- a wheel: cards round a cylinder, one transform turns it, the front card is marked
    assert [got[1]["styles"]["slot%d" % i]["transform"] for i in range(4)] == [
        "rotateY(%ddeg) translateZ(calc(20 * var(--cu)))" % a for a in (0, 90, 180, 270)]
    assert got[1]["styles"]["ring"] == {"transition": "none", "transform": "translateZ(calc(-20 * var(--cu))) rotateY(0deg)"}
    assert got[1]["classes"]["slot0"] == "cs-wheel-card cs-front" and got[1]["classes"]["slot2"] == "cs-wheel-card"
    assert got[5]["styles"]["ring"] == {"transition": "transform 600ms cubic-bezier(.65, 0, .35, 1)",
                                        "transform": "translateZ(calc(-20 * var(--cu))) rotateY(-180deg)"}
    assert got[5]["classes"]["slot2"] == "cs-wheel-card cs-front" and got[5]["classes"]["slot0"] == "cs-wheel-card"
    assert got[6]["ret"] == 5 and got[6]["classes"]["slot1"] == "cs-wheel-card cs-front"          # round again
    # ---- the stage's light: the shared background and this act's glass follow one value
    assert got[1]["bg"] == {"transition": "", "opacity": ""} and "--cs-light" not in got[1]["vars"]["box"]
    assert got[7]["bg"] == {"transition": "opacity 500ms ease", "opacity": "0.2"} and got[7]["vars"]["box"]["--cs-light"] == "0.2"
    assert got[8]["bg"] == {"transition": "none", "opacity": "0.2"} and got[8]["stage"] == "cs-stage cs-up cs-bg-dim"   # a mode keeps the light
    assert got[9]["bg"] == {"transition": "none", "opacity": ""}
    # ---- a clip's handle: a place, a speed (held to what a browser plays), never its sound
    clip = got[12]["videos"][0]
    assert clip["time"] == 3 and clip["rate"] == 8 and clip["muted"] is True and got[12]["ret"] == 3
    # ---- what wraps a keyed thing waits with it: a card round a figure, the canvas of a picture
    assert "cs-await" in got[1]["classes"]["sat"] and "cs-await" in got[1]["classes"]["plx"]
    assert "cs-await" not in got[1]["classes"]["free"]                                    # (a text no beat brings in)
    assert "cs-await" not in got[13]["classes"]["sat"] and "cs-await" in got[13]["classes"]["plx"]
    assert "cs-await" not in got[14]["classes"]["plx"]
    # ---- the next line of the part starts from what this one held; the light is the stage's again
    assert _log(got[1], "prev") == [["prev", None]]
    # (the frame before holds until the next is up: the stage is still its own -- light and all)
    assert got[15]["bg"]["opacity"] == "0.3" and got[16]["bg"]["opacity"] == "0.3"
    assert got[17]["bg"] == {"transition": "", "opacity": ""}
    handed = _log(got[17], "prev")[1][1]
    assert handed["act"] == "probe" and handed["state"] == {"wheel": 5, "note": "kept"} and handed["settled"] is False
    assert handed["slide"]["options"]["mode"] == "night"
    # a hand-over that throws, an act that is down: nothing is handed, and nothing stops
    sulk = _acts({"x": {"acts": {"probe": FX2}, "flags": {"sulk": True}, "steps": [
        {"show": PAGE_SLIDE}, {"tick": 600}, {"show": PAGE_SLIDE}, {"tick": 100}]}})["x"]["seen"]
    assert _log(sulk[3], "prev")[1][1]["state"] is None and sulk[3]["state"]["failed"] is False


def test_an_acts_older_stylesheet_goes_when_its_newer_one_is_asked_for():
    got = _acts({"x": {"acts": {"probe": PROBE}, "steps": [
        {"show": {**PAGE_SLIDE, "v": "aaa111aaa111"}}, {"tick": 50}, {"show": {**PAGE_SLIDE, "v": "bbb222bbb222"}}, {"tick": 50}]}})["x"]["seen"]
    assert got[1]["links"] == ["/assets/ceremony-show/acts/probe.css?v=aaa111aaa111"]
    assert got[3]["links"] == ["/assets/ceremony-show/acts/probe.css?v=bbb222bbb222"]      # one stylesheet of an act, never two


def test_nothing_in_the_shared_stylesheet_shows_through_a_hidden_layer():
    """Seen 17 Nov in the real page: a route of the layer that had been whipped out, lit across
    the next scene -- its `visibility: visible` showed through its hidden parent. What the
    core hides it hides by opacity, and nothing of an act's primitives sets visibility back."""
    plain = re.sub(r"/\*.*?\*/", "", STYLE.read_text(encoding="utf-8"), flags=re.S)
    acts = plain[plain.index(".cs-slide.cs-t-act {"):]
    # (the core's own credit line, the idle stage and the logo clip's wrapper are not in any layer an act
    # hides -- the wrapper is hidden by visibility once the clip's own opacity fade is over: its blend
    # made the whole stage an offscreen pass, 23 Nov)
    core = [".cs-credit.cs-on {", "html.mirror.mirror-show .cs-stage.cs-idle-on {", ".cs-stage.cs-dbt-settle .cs-idle-m, "]
    kept = [line for line in acts.splitlines() if not any(line.startswith(c) for c in core)]
    assert not any("visibility: visible" in line for line in kept)
    assert ".cs-whipped { opacity: 0 !important; visibility: hidden !important; }" in acts
    assert ".cs-route.cs-drawn { opacity: 1; }" in acts and ".cs-plane.cs-placed { opacity: 1; }" in acts
    # the halo of a figure and of a giant word stays with it, over whatever it stands on
    assert ".cs-fig-row { position: relative; isolation: isolate;" in acts
    assert "position: relative; isolation: isolate; font-size: calc(15 * var(--cu))" in acts
    # the stage's light reaches this act's glass and what it marks
    assert ".cs-act-stage .cs-glass::before, .cs-act-stage .cs-lit { opacity: var(--cs-light, 1); transition: opacity .9s ease; }" in acts
    js = ENGINE.read_text(encoding="utf-8")
    assert "var GL_MOST = 4;" in js and "&& run.glAsked < GL_MOST) {" in js      # a page has few WebGL contexts; Emma's is one



# ---- integration, 17 Nov night ----------------------------------------------------------------

def test_a_beats_piece_of_the_line_is_found_whatever_its_case(real):
    """A builder's beat `"at_en": "a real highlight"` was refused for the line's capital A."""
    cue = event_script.cue(AREAS)
    upper = {"at_en": "A real highlight", "do": "x"}
    lower = {"at_en": "a REAL highlight", "do": "x"}
    assert cs._act_beats({"beats": [upper]}, cue)[0] == cs._act_beats({"beats": [lower]}, cue)[0]
    assert cs._act_beats({"beats": [lower]}, cue)[1] == []
    assert "beat_not_in_line:en:0:x" in _act(AREAS, beats=[{"at_en": "a sauna", "do": "x"}])


def test_an_establishing_picture_is_backed_where_she_names_the_project_and_buildings_back_units(real):
    """Integration 17 Nov night: the closing, the fan of everything she studied and the story
    could only reuse three or four pictures, and the court of buildings could not open
    "1,066 units". An establishing picture shows the project itself, so a sentence that
    names the project backs it, on a beat too; a picture of the buildings the units are in
    is about "units"."""
    listed = cs.asset_registry()
    for k in ("liv_garden", "bld_c_holo", "fac_ue_building_a", "liv_trees", "rm_signature"):
        assert listed[k]["establishing"] is True, k
    # ... and none of them is a picture of the pool: where she names the project in the pool
    # joke's set-up ("I've studied this project very carefully..."), no establishing picture
    # may give the punchline away
    pool = {"pool", "swimming", "lagoon", "water"}
    for k, e in list(listed.items()) + list(cs.asset_index().items()):
        if e.get("establishing") is True:
            assert not pool & {w for a in e.get("about") or [] for w in cs._toks(a)}, k
    for k in ("bld_c_holo", "fac_ue_building_a", "bld_c_real"):
        assert "units" in listed[k]["about"], k
    index = cs.asset_index()
    for k in ("c_campus_holo", "c_entrance_aerial", "c_holo_facade"):
        assert index[k]["establishing"] is True, k
    assert index["c_site_top"]["establishing"] is False            # the roofs carry lettering: not establishing
    assert not any(index[k]["establishing"] for k in index if k.startswith("c_fac_") or k.startswith("prj_"))
    # where she names the project: on a beat
    story = "s09_l02"
    on = lambda asset, piece: _act(story, pictures={"p": {"asset": asset}}, beats=[{"at_en": piece, "do": "x", "key": "p"}])  # noqa: E731
    assert on("liv_garden", "the story of Embassy World") == []
    assert on("c_campus_holo", "the story of Embassy World") == []
    # ... and not a picture that is not establishing, and not where she does not name it
    assert "asset_not_near_its_beat:fac_g_biogenesis" in on("fac_g_biogenesis", "the story of Embassy World")
    assert "asset_not_near_its_beat:bld_c_holo" in _act(AREAS, pictures={"p": {"asset": "bld_c_holo"}},
                                                        beats=[{"at_en": "A real highlight", "do": "x", "key": "p"}])
    # the buildings back "units"
    units = "s04_l03"
    assert _act(units, pictures={"p": {"asset": "bld_c_holo"}}, beats=[{"at_en": "1,066", "do": "x", "key": "p"}]) == []
    assert _act(units, pictures={"p": {"asset": "c_bld_b_holo"}}) == []
    assert cs._names_project(cs._toks("Thank you all for being part of Embassy World today."))
    assert cs._names_project(cs._toks("Please take a look at the project details."))
    assert not cs._names_project(cs._toks("Our main common areas are in Building A."))


def test_a_map_carries_its_openstreetmap_credit_and_only_a_licence_line(index):
    """CLAUDE.md: "เครดิต OSM บนภาพต้องอยู่ (ODbL)". The index carries the credit of a map made
    from OpenStreetMap; the page gets it with the picture; the core draws it."""
    folder = index([])
    big = PNG + b"map"
    (folder / "map.webp").write_bytes(big)
    row = {"id": "map_nearby_places", "file": "map.webp", "sha256": _sha(big), "from_pin": "map_nearby_places", "ok": True,
           "checked": "x", "credit": "© OpenStreetMap contributors · OpenFreeMap"}
    index([row])
    slide = {"template": "act", "act": "example_panel", "pictures": {"m": {"asset": "map_nearby_places"}}}
    cue = event_script.cue(BEACH)
    assert cs.problems(slide, cue) == []
    assert cs.page_payload(slide, cue)["pictures"]["m"]["credit"] == "© OpenStreetMap contributors · OpenFreeMap"
    for bad in ("Sky Pool on the roof", "© OpenStreetMap <b>", "© " + "x" * 60, 7, "© OSM · price 3,000,000"):
        index([{**row, "credit": bad}])
        assert "credit" not in cs.page_payload(slide, cue)["pictures"]["m"], bad
    # a picture that is not a map: no credit
    assert "credit" not in cs.page_payload({"template": "act", "act": "example_panel", "pictures": {"m": {"asset": "liv_trees"}}},
                                           cue)["pictures"]["m"]
    # the credit is part of what a signature holds
    index([row])
    before = cs.digest({"slides": {"s03_l03": slide}})
    index([{**row, "credit": "© OpenStreetMap contributors"}])
    assert cs.digest({"slides": {"s03_l03": slide}}) != before


def test_the_real_maps_carry_the_credit_and_the_builder_keeps_it(real):
    index = cs.asset_index()
    maps = [k for k, e in index.items() if k.startswith("map_") and e["from_pin"]]
    osm = [k for k in maps if k != "map_jomtien"]                        # (map_jomtien is the project's own slide)
    assert osm and all(index[k]["credit"] == "© OpenStreetMap contributors · OpenFreeMap" for k in osm)
    import importlib.util
    spec = importlib.util.spec_from_file_location("hires", ROOT / "scripts" / "build_ceremony_hires.py")
    hires = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hires)
    from app import stories
    pins = stories.load_catalog()["assets"]
    assert hires.map_credit(pins["map_nearby_places"]) == "© OpenStreetMap contributors · OpenFreeMap"
    assert hires.map_credit(pins["map_jomtien"]) is None and hires.map_credit(pins["liv_trees"]) is None


def _hires():
    import importlib.util
    spec = importlib.util.spec_from_file_location("hires", ROOT / "scripts" / "build_ceremony_hires.py")
    hires = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hires)
    return hires


def test_the_builder_never_drops_an_entry_registered_by_hand():
    """18 Nov 2026: the Emma 3D clips and the owner's final-render sheets were in the index
    without being candidates; one run of build_ceremony_hires.py took every such entry out of the
    index (and a full run deleted its files). A partial run now touches nothing it did not build;
    a full run keeps what carries `hand` and still removes a withdrawn pin/candidate."""
    hires = _hires()
    previous = {"built": {"id": "built"}, "hand": {"id": "hand", "hand": "rendered by me"},
                "withdrawn": {"id": "withdrawn"}}
    entries = {"built": {"id": "built"}}
    assert hires.carry_over(previous, entries, {"built"}) == (["hand", "withdrawn"], [])   # a run for some ids
    assert hires.carry_over(previous, entries, None) == (["hand"], ["withdrawn"])          # a full run
    assert hires.carry_over(previous, {"built": {}, "hand": {}}, None) == ([], ["withdrawn"])  # built this run: not carried


def test_every_ceremony_index_entry_is_built_or_marked_by_hand(real):
    """The other half of the rule above: an entry nobody can rebuild (not a pin of the stories'
    catalogue, not in asset_candidates.json) must say who made it -- or the next full run of the
    builder removes it from the film."""
    rows = json.loads((ROOT / "data" / "ceremony" / "assets" / "index.json").read_text(encoding="utf-8"))
    doc = json.loads((ROOT / "data" / "ceremony" / "asset_candidates.json").read_text(encoding="utf-8"))
    listed = {c.get("id") for c in doc.get("candidates") or []}
    orphans = [r["id"] for r in rows if r.get("candidate") and r["id"] not in listed and not r.get("hand")]
    assert orphans == [], "registered by hand but not marked `hand` (who made it, how): %s" % orphans


CREDITED = {**PAGE_SLIDE, "pictures": {**PAGE_SLIDE["pictures"],
                                       "map": {"src": A, "w": 1080, "h": 800, "alpha": False,
                                               "credit": "© OpenStreetMap contributors · OpenFreeMap"}},
            "beats": PAGE_SLIDE["beats"] + [{"at": 0.7, "do": "map", "key": "map"}]}
MAPPER = PROBE.replace("  return {\n    beat:", """  var m = ctx.picture('map');
  m.el.setAttribute('data-t', 'map');
  var holder = ctx.el('holder');
  holder.setAttribute('data-t', 'holder');
  ctx.add(ctx.stage, ctx.add(holder, m.el));
  window.__call = { whip: function () { holder.classList.add('cs-whipped'); }, back: function () { holder.classList.remove('cs-whipped'); } };
  return {
    beat:""", 1)


def test_the_core_draws_a_maps_credit_while_the_map_shows_and_no_act_can_take_it_away():
    steps = [{"show": CREDITED}, {"tick": 100}, {"load": True}, {"tick": 100},
             {"progress": 0.5}, {"tick": 100}, {"progress": 0.75}, {"tick": 3000},
             {"call": "whip", "progress": 0.8}, {"progress": 0.81}, {"progress": 0.82, "tick": 3000}, {"call": "back"}, {"settle": True}, {"tick": 3000}, {"clear": True}, {"tick": 900}]
    got = _acts({"x": {"acts": {"probe": MAPPER}, "steps": steps}})["x"]["seen"]
    assert got[3]["state"]["credit"] is None                             # its beat has not come: the map is hidden
    assert got[7]["state"]["credit"] == "© OpenStreetMap contributors · OpenFreeMap"
    assert got[7]["credit"] == ["cs-credit cs-on", "© OpenStreetMap contributors · OpenFreeMap"]
    assert got[10]["state"]["credit"] is None                            # whipped out of sight: no credit
    assert got[11]["state"]["credit"] is None                            # (nothing has looked since it came back)
    assert got[13]["state"]["credit"] == "© OpenStreetMap contributors · OpenFreeMap"   # back, and after the line is over
    assert got[15]["credit"][0] == "cs-credit"                           # the slide down: the credit too
    # it is the stage's, not the act's: outside the act's slide, after it in the page
    assert got[7]["creditParent"] == "cs-stage cs-up" and got[7]["text"].count("OpenStreetMap") == 0
    plain = re.sub(r"/\*.*?\*/", "", STYLE.read_text(encoding="utf-8"), flags=re.S)
    assert ".cs-credit.cs-on { opacity: 1 !important; visibility: visible !important; display: block !important; }" in plain
    assert ".cs-slide { z-index: 1; }" in plain and "z-index: 30;" in plain[plain.index(".cs-credit {"):]


KEYED = """
CeremonyShow.register('probe', function (ctx) {
  var log = window.__log, fx = ctx.fx, call = window.__call = {};
  var pic = ctx.picture('pic', { keyed: 'black' }), wrap = ctx.el('wrap', 'pic'), free = ctx.el('free'), no = ctx.el('no', 'nope');
  wrap.setAttribute('data-t', 'wrap'); free.setAttribute('data-t', 'free'); no.setAttribute('data-t', 'no');
  var held = ctx.hold(ctx.el('held'), 'n'); held.setAttribute('data-t', 'held');
  var scene = ctx.el('scene'), cam = fx.camera(scene), next = ctx.el('next');
  var door = fx.portal(cam, scene, [60, 10, 30, 20], next, { shape: [[0, 0], [100, 0], [50, 100]] });
  door.el.setAttribute('data-t', 'door');
  scene.setAttribute('data-t', 'scene');
  ctx.add(ctx.stage, ctx.add(wrap, pic.el), free, no, held, scene);
  log.push(['root', ctx.root === ctx.stage.parentNode]);
  call.open = function (ms) { door.open(ms, function () { log.push(['landed']); }); return cam.view(); };
  return {};
});
"""


def test_the_core_keys_a_picture_on_black_wraps_a_keyed_thing_and_opens_a_portal():
    steps = [{"show": PAGE_SLIDE}, {"tick": 100}, {"call": "open", "args": [800]}, {"tick": 400}, {"tick": 500},
             {"progress": 0.65}, {"call": "open", "args": [0]}]
    got = _acts({"x": {"acts": {"probe": KEYED}, "steps": steps}})["x"]["seen"]
    assert got[1]["state"]["failed"] is False, got[1]["warnings"]
    assert _log(got[1], "root") == [["root", True]]                      # ctx.root: the act's slide root
    # a wrapper with a key waits with it; without one, or with a key the slide lacks, it does not
    assert "cs-await" in got[1]["classes"]["wrap"] and "cs-await" in got[1]["classes"]["held"]
    assert "cs-await" not in got[1]["classes"]["free"] and "cs-await" not in got[1]["classes"]["no"]
    assert "cs-await" not in got[5]["classes"]["wrap"]
    # the portal: B in a polygon of A's rectangle, unseen until it opens; the camera fills the stage with it
    assert got[1]["classes"]["door"] == "cs-inset cs-portal" and got[1]["styles"]["door"]["clipPath"] == "polygon(0% 0%, 100% 0%, 50% 100%)"
    assert got[1]["styles"]["door"]["left"] == "60%" and got[1]["styles"]["door"]["width"] == "30%"
    assert got[2]["ret"] == {"x": 75, "y": 20, "scale": pytest.approx(100 / 20), "rotate": 0}     # it covers the stage
    assert got[2]["classes"]["door"] == "cs-inset cs-portal cs-open"
    assert got[2]["styles"]["scene"]["transition"] == "transform 800ms cubic-bezier(.3, 0, .1, 1)"
    assert _log(got[3], "landed") == [] and _log(got[4], "landed") == [["landed"]]    # when the push is done
    assert _log(got[6], "landed") == [["landed"], ["landed"]]                     # no time = at once
    # keyed on black: asked for as the plain file, keyed in the page (the harness has no canvas: the plain picture stays)
    assert got[1]["imgs"] == [B]


def test_the_idle_clip_plays_between_lines_only_in_the_ceremonys_show_mode(real):
    manifest = {"acts": {}, "idle": {"src": D, "w": 1918, "h": 1080, "type": "video/mp4"}}
    got = _acts({"x": {"acts": {"probe": PROBE}, "manifest": manifest, "showMode": True, "steps": [
        {"tick": 0}, {"show": PAGE_SLIDE}, {"tick": 100}, {"clear": True}, {"mode": False}, {"mode": True}]}})["x"]["seen"]
    assert got[0]["idle"] == {"on": True, "src": D, "muted": True, "loop": False, "played": 1, "paused": 0, "time": 0}
    # (18 Nov: pressed, the line's slide is not up yet -- the logo HOLDS until it is, then rests under it)
    assert got[1]["idle"]["on"] is True and got[1]["idle"]["paused"] == 0
    assert got[2]["idle"]["on"] is False and got[2]["idle"]["paused"] == 1      # a line came up: the clip rests under it
    # (owner 18 Nov night: it OPENS the ceremony, once -- never an interstitial. Idle again
    # after a stop: the stage shows it, its last frame held, not played over)
    assert got[3]["idle"]["on"] is True and got[3]["idle"]["played"] == 1
    assert got[4]["idle"]["on"] is False                                         # not the ceremony's screen: nothing
    assert got[5]["idle"]["on"] is True and got[5]["idle"]["played"] == 1
    off = _acts({"x": {"acts": {"probe": PROBE}, "manifest": manifest, "steps": [{"tick": 0}]}})["x"]["seen"][0]
    assert off["idle"]["on"] is False and off["idle"]["played"] == 0
    assert cs.acts_manifest()["idle"]["src"].startswith("/ceremony-asset/")    # the real one: the project's logo clip
    plain = re.sub(r"/\*.*?\*/", "", STYLE.read_text(encoding="utf-8"), flags=re.S)
    assert "html.mirror.mirror-show .cs-stage.cs-idle-on { opacity: 1; visibility: visible;" in plain
    assert ".cs-stage:not(.cs-up):not(.cs-idle-on) .cs-bg > span { animation-play-state: paused; }" in plain


def test_whip_streaks_are_sparse_lines_not_a_grating():
    plain = re.sub(r"/\*.*?\*/", "", STYLE.read_text(encoding="utf-8"), flags=re.S)
    streak = plain[plain.index(".cs-whip-streak {"):plain.index("@keyframes cs-streak-l")]
    assert "repeating-linear-gradient" not in streak
    assert streak.count("no-repeat") == 14


def test_deploy_ships_the_ceremony_files_the_slides_name_and_no_other(tmp_path):
    """data/ceremony/assets/ is gitignored (80 MB): `robot_server.py deploy` sends the files a
    slide names (its full size, its depth map, its licence) and the idle clip -- nothing else."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("robot_server_for_test", ROOT / "scripts" / "robot_server.py")
    rs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rs)
    root = tmp_path
    (root / "data" / "ceremony" / "slides").mkdir(parents=True)
    (root / "app").mkdir()
    (root / "app" / "main.py").write_text("x", encoding="utf-8")        # what a path out of the folder would reach
    folder = root / "data" / "ceremony" / "assets"
    (folder / "licenses").mkdir(parents=True)
    for name in ("a.webp", "a.depth.png", "a.depth_soft.png", "b.webp", "c.webp", "logo.mp4", "licenses/L.txt", "ico.svg"):
        (folder / name).write_bytes(b"x")
    rows = [{"id": "liv_a", "file": "a.webp", "from_pin": "liv_a",
             "depth": {"file": "a.depth.png", "soft": {"file": "a.depth_soft.png"}}},
            {"id": "c_b", "file": "b.webp", "candidate": True},
            {"id": "c_unused", "file": "c.webp", "candidate": True},
            {"id": "c_logo_reveal", "file": "logo.mp4", "candidate": True},       # the opening clip (IDLE_CLIP)
            {"id": "c_ico", "file": "ico.svg", "licence": {"files": ["licenses/L.txt"]}},
            {"id": "c_evil", "file": "../../../app/main.py"}]
    (folder / "index.json").write_text(json.dumps(rows), encoding="utf-8")
    (root / "data" / "ceremony" / "floor1_ceremony.slides.json").write_text(
        json.dumps({"slides": {"x": {"template": "hero", "backdrop": "c_b"}}}), encoding="utf-8")
    (root / "data" / "ceremony" / "slides" / "s1.json").write_text(
        json.dumps({"template": "act", "pictures": {"p": {"asset": "liv_a"}, "q": {"asset": "c_ico"}, "e": {"asset": "c_evil"}}}),
        encoding="utf-8")
    got = rs.ceremony_film_files(root)
    base = "data/ceremony/assets/"
    assert got == {base + n for n in ("a.webp", "a.depth.png", "a.depth_soft.png", "b.webp", "logo.mp4", "ico.svg", "licenses/L.txt")}
    # the real one: what the slides name, a fraction of the folder
    real = rs.ceremony_film_files(ROOT)
    assert real and all(p.startswith(base) for p in real)
    assert len(real) < len(list((ROOT / base).glob("*")))
    assert rs.ceremony_film_files in [rs.ceremony_film_files] and "files |= ceremony_film_files(root)" in (
        ROOT / "scripts" / "robot_server.py").read_text(encoding="utf-8")


def test_the_scan_reads_a_curve_named_with_a_fallback():
    """The film's motion identity (18 Nov skill pass) names its curves `var(--cs-ease, cubic-bezier(...))`:
    the scan must read that as one curve, and still refuse a transition of anything but transform/opacity."""
    ok = (".cs-act-mine .a { opacity: 0; transition: transform 500ms var(--cs-ease, cubic-bezier(0.4, 0, 0.2, 1)) 80ms,"
          " opacity 300ms var(--cs-ease-out, cubic-bezier(0.3, 0, 1, 1)); }")
    assert css_problems("mine", ok) == []
    bad = ".cs-act-mine .a { transition: transform 500ms var(--cs-ease, cubic-bezier(0.4, 0, 0.2, 1)), width .3s; }"
    assert any("width" in p for p in css_problems("mine", bad))


# ---- a shot carried over from the line before (`continues`) ------------------------------------

def _carried(monkeypatch, real, slides: dict):
    """The real ceremony with these act slides in place of the cues' own."""
    doc = {**real, "approved_by": None, "slides": {**real["slides"], **slides}}
    monkeypatch.setattr(cs, "_doc", lambda: doc)
    return doc


def test_a_slide_that_continues_the_line_before_may_hold_that_lines_shot(real, monkeypatch):
    """Owner 18 Nov, on the dark water after Emma's dive: "ทำให้มันต่อเนื่องกันกับอันก่อนหน้าก็ได้ โดยให้
    หุ่นว่ายไปมา". "...show off a few laps" and "With pleasure, Khun Miki!" never say pool; the
    pool is backed by the line they continue ("Swimming in the 155-metre pool myself!")."""
    panel = {"template": "act", "act": "example_panel"}
    pool = {"p": {"asset": "lagoon_scene58"}}
    laps = {**panel, "pictures": pool}
    _carried(monkeypatch, real, {"s04_l11": {**panel, "pictures": pool}, "s04_l13": {**laps, "continues": "s04_l11"}})
    # without the claim: the old answer, at the opening and on a beat
    assert "asset_not_in_line:lagoon_scene58" in cs.problems(laps, event_script.cue("s04_l13"))
    on_beat = [{"at_en": "show off", "do": "x", "key": "p"}]
    assert "asset_not_near_its_beat:lagoon_scene58" in cs.problems({**laps, "beats": on_beat}, event_script.cue("s04_l13"))
    # with it: held
    assert cs.problems({**laps, "continues": "s04_l11"}, event_script.cue("s04_l13")) == []
    assert cs.problems({**laps, "continues": "s04_l11", "beats": on_beat}, event_script.cue("s04_l13")) == []
    # two lines back, through the line between (which continues the first itself)
    assert cs.problems({**laps, "continues": "s04_l13"}, event_script.cue("s04_l15")) == []
    # the page is not told (the core joins by act and mode; this is the checker's own field)
    assert "continues" not in cs.page_payload({**laps, "continues": "s04_l11"}, event_script.cue("s04_l13"))


def test_continues_is_only_the_line_directly_before_in_the_same_act_and_loosens_nothing_else(real, monkeypatch):
    panel = {"template": "act", "act": "example_panel"}
    pool = {"p": {"asset": "lagoon_scene58"}}
    laps = {**panel, "pictures": pool}
    _carried(monkeypatch, real, {"s04_l11": {**panel, "pictures": pool}, "s04_l13": dict(laps),
                                 "s04_l09": {**panel, "pictures": pool}})
    says = lambda slide, cue_id: cs.problems(slide, event_script.cue(cue_id))      # noqa: E731
    # not the line directly before: two back, a later line, another scene's line, itself
    assert "continues_not_the_line_before:s04_l11" in says({**laps, "continues": "s04_l11"}, "s04_l15")
    assert "continues_not_the_line_before:s04_l15" in says({**laps, "continues": "s04_l15"}, "s04_l13")
    assert "continues_not_the_line_before:s03_l03" in says({**laps, "continues": "s03_l03"}, "s04_l13")
    assert "bad_continues" in says({**laps, "continues": "s04_l13"}, "s04_l13")
    # ... and a line whose neighbour does not continue is not carried through it
    assert "asset_not_in_line:lagoon_scene58" in says({**laps, "continues": "s04_l13"}, "s04_l15")
    # the first line of a scene has no line before it (the scene before is another shot)
    assert "continues_not_the_line_before:s03_l03" in says({**laps, "continues": "s03_l03"}, "s04_l03")
    # junk
    for junk in (5, "", "S04_L11", ["s04_l11"]):
        assert "bad_continues" in says({**laps, "continues": junk}, "s04_l13"), junk
    # another act's shot is not this act's to hold
    doc = cs._doc()
    monkeypatch.setattr(cs, "_doc", lambda: {**doc, "slides": {**doc["slides"], "s04_l11": {"template": "act", "act": "living_photos"}}})
    assert "continues_another_act:s04_l11" in says({**laps, "continues": "s04_l11"}, "s04_l13")
    monkeypatch.setattr(cs, "_doc", lambda: doc)
    # nothing else is loosened: the caption is of its picture, a picture nobody's words back stays out,
    # and words are not shown before she says them
    held = {**laps, "continues": "s04_l11"}
    assert "caption_not_of_its_picture:lagoon_scene58" in says({**held, "pictures": {"p": {"asset": "lagoon_scene58", "caption": "Building A"}}}, "s04_l13")
    assert "asset_not_in_line:rm_signature" in says({**held, "pictures": {"p": {"asset": "rm_signature"}}}, "s04_l13") \
        or "asset_not_in_line:fac_3_coworking" in says({**held, "pictures": {"p": {"asset": "fac_3_coworking"}}}, "s04_l13")
    monkeypatch.setattr(cs, "_doc", lambda: {**doc, "slides": {**doc["slides"], "s04_l03": dict(panel)}})
    early = {**panel, "continues": "s04_l03", "texts": {"t": "Places to relax"},
             "beats": [{"at_en": "Our main", "do": "x", "key": "t"}]}
    assert "words_before_she_says_them:t:relax" in says(early, AREAS)      # (AREAS = s04_l06, the line after s04_l03)


# ---- the film's cut, its type and its floor (the core, 18 Nov afternoon) ---------------------------

def _slow_page(browser, slow: dict[str, float]):
    """`_page`, but a picture whose address ends with a key of `slow` takes that many seconds to
    come (the hall's Wi-Fi): the engine holds the frame before for it, for a bounded time."""
    import time

    page, said = _page(browser)

    def late(route):
        path = route.request.url.split("?")[0]
        for tail, seconds in slow.items():
            if path.endswith(tail):
                time.sleep(seconds)
        route.fulfill(body=PNG, content_type="image/png")

    page.route(re.compile(r".*/ceremony-asset/[0-9a-f]{64}$"), late)
    return page, said


HOLD = """async (src) => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const eng = window.CeremonyShow, seen = [], arrived = [];
  const shot = function (ctx) {
    const p = ctx.picture('pic');
    ctx.add(ctx.stage, p.el, ctx.text('line'));
    ctx.after(50, function () { arrived.push('after'); });
    return { arrive: function (cut) { arrived.push(cut.kind + ':' + cut.ms + ':' + ctx.cut.kind); } };
  };
  eng.register('one', shot); eng.register('two', shot);
  const slide = (act, pic, beats) => ({ template: 'act', act: act, texts: { line: 'Main common areas' }, figures: {},
    pictures: { pic: { src: pic, w: 10, h: 10 } }, videos: {}, beats: beats || [], options: {} });
  const roots = () => Array.from(document.querySelectorAll('.cs-slide.cs-t-act'));
  const look = (at) => { const all = roots(), r = all[all.length - 1], before = all.length > 1 ? all[all.length - 2] : null;
    seen.push({ at: at, cut: r.getAttribute('data-cut'), live: r.classList.contains('cs-live'), opacity: +getComputedStyle(r).opacity,
                loaded: !!r.querySelector('.cs-apic.cs-loaded'), picture: r.querySelector('.cs-apic-in') ? +getComputedStyle(r.querySelector('.cs-apic-in')).opacity : null,
                before: before ? before.classList.contains('cs-out') : null, did: eng.state().did, arrived: arrived.slice() }); };
  eng.show(slide('one', src.fast));
  await wait(700); look('first');
  eng.show(slide('two', src.slow, [{ at: 0.3, 'do': 'said' }]));
  look('pressed');
  eng.progress(0.35);                         // her voice does not wait for the picture
  await wait(100); look('holding');
  await wait(800); look('up');
  eng.show(slide('one', src.never));
  await wait(250); look('still holding');
  await wait(600); look('the hold is over');
  eng.clear(); await wait(600);
  return seen;
}"""


def test_a_new_slide_holds_under_the_frame_before_until_its_first_pictures_are_decoded(chromium):
    """Owner 18 Nov, the scene videos: pictures appeared a moment after their cut. A new slide is
    in the page at once (built, drawn at next to nothing) but the frame before HOLDS until the
    pictures of its opening are decoded -- for at most the hold; her voice and the beats never
    wait, and the act's own timers and its arrive() count from the moment it comes up."""
    fast, slow, never = ("/ceremony-asset/" + c * 64 for c in "abc")
    page, said = _slow_page(chromium, {"b" * 64: 0.3, "c" * 64: 1.2})
    try:
        seen = {row["at"]: row for row in page.evaluate(HOLD, {"fast": fast, "slow": slow, "never": never})}
    finally:
        page.close()
    assert [w for w in said if "404" not in w] == [], said          # (the page's own /ceremony-acts.json: not served here)
    first = seen["first"]
    assert first["cut"] == "in" and first["live"] and first["loaded"] and first["arrived"] == ["cut:1200:cut", "after"]
    # pressed: the new one is there, unseen (not 0: its words must still read as shown to the
    # gate's checks), the frame before whole -- and it stays so while the picture is on its way
    for at in ("pressed", "holding"):
        row = seen[at]
        assert row["cut"] == "hold" and 0 < row["opacity"] < 0.01 and row["before"] is False and not row["live"] and not row["loaded"], at
    # ... while her voice goes on: the beat fired in the hold, the act's timers did not start
    assert seen["holding"]["did"] == ["said"] and seen["holding"]["arrived"] == first["arrived"]
    # the picture came (0.3 s): the slide is up with it decoded and whole -- no fade of its own
    # behind the cut -- the frame before goes, the act arrives
    up = seen["up"]
    assert up["cut"] == "in" and up["live"] and up["loaded"] and up["picture"] == 1 and up["before"] is True
    assert up["arrived"] == first["arrived"] + ["cut:1200:cut", "after"]
    # a picture that does not come: the frame before holds for the hold's time and no longer
    assert seen["still holding"]["cut"] == "hold" and seen["still holding"]["before"] is False
    over = seen["the hold is over"]
    assert over["cut"] == "in" and not over["loaded"] and over["before"] is True and over["live"]
    js = ENGINE.read_text(encoding="utf-8")
    assert "var CUT = { hold: 480, soft: 240, into: 1200, down: 800, blend: 1200, fade: 300 };" in js


UNSEEN = """
CeremonyShow.register('probe', function (ctx) {
  var pic = ctx.picture('pic');
  if (window.__flags.unseen) pic.el.checkVisibility = function () { return false; };
  ctx.add(ctx.stage, pic.el, ctx.text('title'));
  return {};
});
"""


def test_the_hold_has_two_bounds_the_shorter_for_a_picture_nobody_could_see_yet():
    """A picture of the opening that has not come: the frame before holds for it -- 480 ms when
    it would be seen the moment the slide comes up, 240 ms when it could not be seen yet anyway
    (an act that builds every picture of its line at once and brings them in itself: 18 Nov,
    s02_l05 held the full time for nine pictures of later beats). On the engine's own clock."""
    slide = {**PAGE_SLIDE, "beats": [], "videos": {}}
    steps = [{"show": slide}, {"tick": 100}, {"tick": 200}, {"tick": 200}]                  # at 100, 300, 500 ms
    seen = _acts({"x": {"acts": {"probe": UNSEEN}, "steps": steps}})["x"]["seen"]
    unseen = _acts({"x": {"acts": {"probe": UNSEEN}, "flags": {"unseen": True}, "steps": steps}})["x"]["seen"]
    assert [row["state"]["started"] for row in seen] == [False, True, True, True]
    assert [row["stage"] for row in seen] == ["cs-stage", "cs-stage", "cs-stage", "cs-stage cs-up"]
    assert [row["stage"] for row in unseen] == ["cs-stage", "cs-stage", "cs-stage cs-up", "cs-stage cs-up"]
    # ... and none at all once it is decoded
    came = _acts({"x": {"acts": {"probe": UNSEEN}, "steps": [{"show": slide}, {"tick": 100}, {"load": True}]}})["x"]["seen"]
    assert [row["stage"] for row in came] == ["cs-stage", "cs-stage", "cs-stage cs-up"]


CLIP = """async (src) => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const eng = window.CeremonyShow, seen = [];
  const shot = function (ctx) {
    const v = ctx.video('clip');
    ctx.add(ctx.stage, v.el, ctx.text('line'));
    return { handover: function () { return { on: true }; } };
  };
  eng.register('diver', shot); eng.register('other', shot);
  const slide = (act) => ({ template: 'act', act: act, texts: { line: 'Main common areas' }, figures: {}, pictures: {},
    videos: { clip: { src: src, w: 16, h: 9 } }, beats: [], options: {} });
  const clip = () => Array.from(document.querySelectorAll('.cs-slide.cs-t-act video')).pop();
  for (const next of ['diver', 'other']) {
    eng.show(slide('diver')); await wait(900);
    const old = clip(), root = old.closest('.cs-slide');
    eng.show(slide(next));
    const row = { next: next, pressed: old.getAttribute('src') };
    const fresh = Array.from(document.querySelectorAll('.cs-slide.cs-t-act')).pop();
    for (let i = 0; i < 300 && fresh.getAttribute('data-cut') === 'hold'; i++) await wait(5);
    row.cut = fresh.getAttribute('data-cut');
    row.going = [root.classList.contains('cs-out'), root.isConnected, old.getAttribute('src')];
    await wait(1700);
    row.gone = [root.isConnected, old.getAttribute('src')];
    seen.push(row);
    eng.clear(); await wait(600);
  }
  return seen;
}"""


def test_a_shot_going_down_keeps_its_clip_until_it_is_gone(chromium):
    """Review 18 Nov (Emma in the pool, three lines that join): the line going down lost its clip
    the moment the next was pressed, and faded for 0.3 s with a hole where Emma had been. A
    slide's clips are let go when its root leaves the page -- a join and a cut alike (a shot
    under the sheet of light is still on the screen)."""
    src = "/ceremony-asset/" + "d" * 64
    page, said = _page(chromium)
    try:
        seen = page.evaluate(CLIP, src)
    finally:
        page.close()
    assert [row["next"] for row in seen] == ["diver", "other"]
    assert [row["cut"] for row in seen] == ["join", "in"]
    for row in seen:
        assert row["pressed"] == src, row
        assert row["going"] == [True, True, src], row                    # going down, still seen: its clip is still its own
        assert row["gone"] == [False, None], row                         # out of the page: let go


TYPE = """async () => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const eng = window.CeremonyShow, t = {}, seen = {};
  eng.register('typeset', function (ctx) {
    t.head = ctx.text('head', { cls: 'cs-type-h1' });
    t.label = ctx.text('label', { cls: 'cs-type-label' });
    t.old = ctx.text('label', { reveal: 'depth' });
    t.joke = ctx.text('joke', { reveal: 'pop' });
    t.still = ctx.text('label', { reveal: false });
    t.words = ctx.text('label', { split: 'words' });
    t.number = ctx.text('number');
    t.fig = ctx.figure('beach', { glow: true });
    t.icon = ctx.fx.halo(ctx.el('mark'));
    ctx.add(ctx.stage, t.head, t.label, t.old, t.joke, t.still, t.words, t.number, t.fig, t.icon);
    return {};
  });
  eng.show({ template: 'act', act: 'typeset', figures: { beach: { lead: 'about', value: '600', unit: 'metres', label: 'in a straight line' } },
             texts: { head: 'All the main common areas of our project', label: 'Embassy World', joke: 'Of course not!', number: 'about 600 metres' },
             pictures: {}, videos: {}, beats: [], options: {} });
  const pieces = e => Array.from(e.querySelectorAll('.cs-tg'));
  const lit = e => pieces(e).map(p => +getComputedStyle(p).opacity);
  const value = () => t.fig.querySelector('.cs-fig-value').textContent;
  const values = [value()];
  const root = t.head.closest('.cs-slide');
  for (let i = 0; i < 200 && !root.classList.contains('cs-live'); i++) await wait(5);
  // the letters light up left to right: sampled as they go
  seen.wave = [];
  for (let i = 0; i < 24; i++) { await wait(40); seen.wave.push(lit(t.head)); values.push(value()); }
  await wait(900);
  seen.wave.push(lit(t.head));
  const cs = e => getComputedStyle(e);
  seen.head = { family: cs(t.head).fontFamily, weight: +cs(t.head).fontWeight, style: cs(t.head).fontStyle, 'case': cs(t.head).textTransform,
                rv: t.head.getAttribute('data-rv'), cls: t.head.className, letters: pieces(t.head).length,
                first: cs(pieces(t.head)[0]).color, last: cs(pieces(t.head).pop()).color, lit: lit(t.head),
                inline: cs(t.head.querySelector('.cs-tl')).display, step: t.head.style.getPropertyValue('--rvs'),
                size: parseFloat(cs(t.head).fontSize) / (parseFloat(cs(t.head.closest('.cs-act-stage')).width) / 100) };
  seen.face = document.fonts.check('600 20px Inter');
  seen.kantalad = ['300', '470', '600', '700'].map(w => document.fonts.check(w + ' 20px Kantalad'));
  seen.label = { 'case': cs(t.label).textTransform, style: cs(t.label).fontStyle, rv: t.label.getAttribute('data-rv'), cls: t.label.className,
                 spacing: parseFloat(cs(t.label).letterSpacing) || 0 };
  seen.rv = { old: t.old.getAttribute('data-rv'), joke: t.joke.getAttribute('data-rv'), still: t.still.getAttribute('data-rv'),
              words: t.words.getAttribute('data-rv'), fig: t.fig.getAttribute('data-rv'),
              jokePieces: pieces(t.joke).map(p => p.className.split(' ')[0]), wordPieces: pieces(t.words).length,
              stillPieces: pieces(t.still).length, number: t.number.style.getPropertyValue('--rvs') };
  seen.fig = { text: t.fig.textContent, pieces: t.fig.querySelectorAll('.cs-tg, .cs-tl, .cs-tc').length, values: Array.from(new Set(values)),
               'case': cs(t.fig.querySelector('.cs-fig-label')).textTransform, weight: +cs(t.fig.querySelector('.cs-fig-value')).fontWeight,
               unit: +cs(t.fig.querySelector('.cs-fig-unit')).fontWeight };
  seen.halo = { icon: t.icon.className, glow: !!t.icon.querySelector('.cs-halo-glow'), head: getComputedStyle(t.head, '::before').content !== 'none' };
  // ... and leave: cooled and gone, left to right, sooner than they came
  t.head.classList.add('cs-leave'); t.fig.classList.add('cs-leave');
  seen.leaving = [];
  for (let i = 0; i < 16; i++) { await wait(30); seen.leaving.push(lit(t.head)); }
  await wait(400); seen.left = { head: lit(t.head), fig: +cs(t.fig.querySelector('.cs-fig-row')).opacity };
  t.head.classList.remove('cs-leave');
  await wait(1200); seen.back = lit(t.head);
  eng.clear(); await wait(500);
  return seen;
}"""


def test_the_films_type_is_one_upright_face_in_the_slides_own_case_and_its_letters_light_up(chromium):
    """Owner 18 Nov afternoon, with Apple's MacBook Pro film: "ปรับพวกฟ้อนกับการแสดงข้อความตัวหนังสือ
    ให้มันประมาณนี้". Kantalad since 22 Nov (Inter behind it), upright, sentence case as the slide gives it, white running to pale
    teal across the phrase; the letters light up left to right inside half a second (nothing
    flies in), and leave sooner than they came; a figure is one piece with its own value."""
    page, said = _page(chromium)
    try:
        seen = page.evaluate(TYPE)
    finally:
        page.close()
    assert [w for w in said if "404" not in w] == [], said
    head = seen["head"]
    assert seen["face"] is True and head["family"].startswith("Kantalad, Inter,") and 500 <= head["weight"] <= 600
    # every Kantalad face asked for when the screen opened is there (this PC has the licensed files;
    # without them the deploy stops -- scripts/robot_server.py -- and this run falls back to Inter)
    if all((ROOT / "client" / "assets" / "ceremony-show" / "fonts" / ("Kantalad-%s.otf" % n)).is_file()
           for n in ("Regular", "Medium", "SemiBold", "Bold")):
        assert seen["kantalad"] == [True, True, True, True], seen["kantalad"]
    assert head["style"] == "normal" and head["case"] == "none" and 3.9 <= head["size"] <= 4.6      # a phrase, not a wall
    assert head["rv"] == "light" and "cs-text-glow" in head["cls"] and head["letters"] == 33 and head["inline"] == "inline"
    assert head["first"] != head["last"] and head["last"] == "color(srgb 0.65098 0.847059 0.968627)"      # white ... to ice blue
    assert head["lit"] == [1] * 33
    # the wave: at every moment a letter is at least as lit as the one to its right, and at some
    # moment the left is lit while the right is not yet; nothing ever goes dark again; the engine
    # closes the steps up so the whole phrase is lit inside 0.6 s (a hero title's budget)
    assert any(row[0] > row[-1] for row in seen["wave"])
    for row in seen["wave"]:
        assert all(a >= b - 0.001 for a, b in zip(row, row[1:])), row
    for before, after in zip(seen["wave"], seen["wave"][1:]):
        assert all(b >= a - 0.02 for a, b in zip(before, after))
    assert seen["wave"][-1] == [1] * 33 and 40 + 32 * int(head["step"].rstrip("ms")) + 260 <= 660
    # a label: small, light, the slide's case, normal tracking -- no forced capitals anywhere
    assert seen["label"]["case"] == "none" and seen["label"]["style"] == "normal" and seen["label"]["spacing"] < 1
    plain = re.sub(r"/\*.*?\*/", "", STYLE.read_text(encoding="utf-8"), flags=re.S)
    acts = plain[plain.index(".cs-slide.cs-t-act {"):]
    assert "text-transform" not in acts and "font-style: italic" not in acts and "text-shadow" not in acts
    # the morning's entrances still answer to their names (they mean `light`); a joke may pop,
    # by word; a text with no entrance and one split by words are in pieces too (the colour)
    rv = seen["rv"]
    assert rv["old"] == "light" and rv["joke"] == "pop" and rv["still"] is None and rv["words"] is None and rv["fig"] == "light"
    assert rv["jokePieces"] == ["cs-tw"] * 3 and rv["wordPieces"] == 2 and rv["stillPieces"] == 12
    assert rv["number"] == "0ms"                                   # a number is never a moment without its words
    # a figure: one piece, its own value from the first frame to the last, sentence case
    fig = seen["fig"]
    assert fig["text"] == "about600metresin a straight line" and fig["pieces"] == 0 and fig["values"] == ["600"]
    assert fig["case"] == "none" and fig["weight"] > fig["unit"]
    assert seen["halo"] == {"icon": "cs-halo", "glow": True, "head": True}
    # leaving: from the left, and gone inside ~0.3 s; taking the class off brings them back
    assert any(row[0] < row[-1] for row in seen["leaving"])
    for row in seen["leaving"]:
        assert all(a <= b + 0.001 for a, b in zip(row, row[1:])), row
    assert seen["left"] == {"head": [0] * 33, "fig": 0} and seen["back"] == [1] * 33
    plain_leave = plain[plain.index(".cs-t-act .cs-text.cs-leave .cs-tg {"):]
    assert "cs-rv-cool 160ms" in plain_leave and "min(calc(var(--i, 0) * 10ms), 120ms)" in plain_leave      # 280 ms: shorter than it came


# the room screen's own order (client/voice-preview.html mirrorSlideAssets): the stylesheet and the
# engine added in one moment -- here the stylesheet lands 600 ms after the engine has run
LATE_CSS_SHELL = """<!doctype html><html class="mirror mirror-show"><head><meta charset="utf-8"></head><body><script>
(function () {
  var js = document.createElement('script');
  js.src = '/assets/ceremony-show.js';
  document.head.appendChild(js);
  setTimeout(function () {
    var css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = '/assets/ceremony-show.css';
    document.head.appendChild(css);
    window.__cssAt = performance.now();
  }, 600);
})();
</script></body></html>"""


def test_the_faces_are_asked_for_when_the_stylesheet_lands_after_the_engine(chromium):
    """Review 22 Nov night: with the stylesheet 150 ms late there were no font requests at all --
    warmFont ran before the @font-face rules existed, so Medium and Bold came only with the first
    slide, and `block` held its words invisible for the trip over the hall's Wi-Fi. Now all four
    Kantalad faces are asked for once the rules land, before any slide is shown (the request is the
    test, not the file: a PC without the licensed faces gets 404s for the same four names)."""
    page = chromium.new_page(viewport={"width": 1920, "height": 1080})
    asked: list[str] = []

    def answer(route):
        path = route.request.url.split("//", 1)[1].split("/", 1)[1].split("?")[0]
        if path == "":
            return route.fulfill(body=LATE_CSS_SHELL, content_type="text/html")
        face = re.fullmatch(r"assets/ceremony-show/fonts/(Kantalad-\w+\.otf|Inter\.ttf)", path)
        if face:
            asked.append(face.group(1))
        if path in ("assets/ceremony-show.js", "assets/ceremony-show.css") or face:
            file = ROOT / "client" / path
            if file.is_file():
                kind = {".css": "text/css", ".ttf": "font/ttf", ".otf": "font/otf"}.get(file.suffix, "text/javascript")
                return route.fulfill(body=file.read_bytes(), content_type=kind)
        return route.fulfill(status=404, body="")

    page.route("**/*", answer)
    try:
        page.goto("http://ceremony.test/")
        page.wait_for_function("() => !!window.CeremonyShow")
        engine_first = page.evaluate("() => window.__cssAt === undefined")
        page.wait_for_function("() => window.__cssAt !== undefined")
        for _ in range(40):                          # up to 4 s after the stylesheet
            if len({a for a in asked if a.startswith("Kantalad-")}) == 4:
                break
            page.wait_for_timeout(100)
        no_slide = page.evaluate("() => !document.querySelector('.cs-stage') && !window.CeremonyShow.state().started")
    finally:
        page.close()
    assert engine_first                              # the case under test: the engine ran with no @font-face
    assert sorted({a for a in asked if a.startswith("Kantalad-")}) == [
        "Kantalad-Bold.otf", "Kantalad-Medium.otf", "Kantalad-Regular.otf", "Kantalad-SemiBold.otf"], asked
    assert no_slide                                  # all of it before the first slide

FLOOR = """async (slide) => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const st = document.createElement('style');
  st.textContent = '*, *::before, *::after { transition-duration: 0s !important; transition-delay: 0s !important;'
    + ' animation-duration: 0s !important; animation-delay: 0s !important; }';
  document.head.appendChild(st);
  const eng = window.CeremonyShow, seen = [];
  eng.show(slide);
  for (let i = 0; i < 80 && !(eng.state().started || eng.state().failed); i++) await wait(50);
  await wait(120);
  const stage = document.querySelector('.cs-stage'), box = document.querySelector('.cs-act-stage');
  if (!box) return { seen: seen, floor: null };
  const cu = box.getBoundingClientRect().width / 100, top = box.getBoundingClientRect().top;
  const floor = top + 43.8 * cu, slack = 0.25 * cu;
  function shown(e) {
    let o = 1;
    for (let x = e; x && x !== document.body; x = x.parentElement) {
      const cs = getComputedStyle(x);
      if (cs.visibility === 'hidden' || cs.display === 'none') return 0;
      o *= +cs.opacity;
    }
    return o;
  }
  function clip(e) {
    const s = stage.getBoundingClientRect();
    let r = { l: Math.max(0, s.left), t: Math.max(0, s.top), r: Math.min(innerWidth, s.right), b: Math.min(innerHeight, s.bottom) };
    for (let x = e.parentElement; x && x !== document.body; x = x.parentElement) {
      const cs = getComputedStyle(x);
      if (cs.overflow !== 'visible' || cs.clipPath !== 'none') {
        const q = x.getBoundingClientRect();
        r = { l: Math.max(r.l, q.left), t: Math.max(r.t, q.top), r: Math.min(r.r, q.right), b: Math.min(r.b, q.bottom) };
      }
    }
    return r;
  }
  function look(f) {
    document.querySelectorAll('.cs-act-stage .cs-text, .cs-act-stage .cs-fig-row, .cs-act-stage .cs-fig-label, .cs-act-stage .cs-acap').forEach(el => {
      if (shown(el) < 0.05) return;
      const c = clip(el), range = document.createRange();
      range.selectNodeContents(el);
      let low = -1;
      for (const r of range.getClientRects()) {
        const x0 = Math.max(r.left, c.l), x1 = Math.min(r.right, c.r), y0 = Math.max(r.top, c.t), y1 = Math.min(r.bottom, c.b);
        if (x1 - x0 > 1 && y1 - y0 > 1) low = Math.max(low, y1);
      }
      if (low > floor + slack) {
        const owner = el.closest('[data-k]');
        seen.push({ f: f, key: owner ? owner.getAttribute('data-k') : null, words: el.textContent.replace(/[ ]+/g, ' ').slice(0, 40),
                    cu: Math.round((low - top) / cu * 10) / 10 });
      }
    });
    const credit = document.querySelector('.cs-credit.cs-on');
    if (credit && credit.getBoundingClientRect().bottom > floor + slack) seen.push({ f: f, key: 'the map credit', cu: Math.round((credit.getBoundingClientRect().bottom - top) / cu * 10) / 10 });
  }
  look(0);
  for (const f of slide.__steps) { eng.progress(f); await wait(80); look(f); }
  eng.settle(); await wait(120); look(2);
  const credit = !!document.querySelector('.cs-credit.cs-on');
  eng.clear(); await wait(60);
  return { seen: seen, floor: Math.round(floor), credit: credit };
}"""


@pytest.mark.parametrize("cue_id", _act_slides())
def test_no_word_figure_or_credit_stands_in_the_subtitle_boxs_zone(cue_id, real, chromium):
    """Owner 18 Nov afternoon: "อยากให้มันเต็มจอเลยไม่ต้องแบ่งพท้นที่ให้ซับ แต่ให้ซับมีกล่องข้อความใสๆ ... แต่ก็
    อย่าให้อะไรมาทับหรือซ้อนตรงนี้". The film is the whole screen now and the Thai line sits in a
    see-through box in its lowest 22%: below 43.8cu there may be light, floors and pictures --
    never a word, a figure, a caption or the map's credit. At every beat, landed (the band used
    to HIDE what broke this; now it shows)."""
    payload = cs.for_cue(cue_id, drafts=True)
    assert payload is not None
    ats = sorted({b["at"] for b in payload["beats"]})
    steps = sorted({0.0, 1.0} | {min(1.0, round(a + 0.004, 4)) for a in ats}
                   | {round((a + b) / 2, 4) for a, b in zip(ats, ats[1:] + [1.0])})
    page, said = _warped_page(chromium)
    try:
        got = page.evaluate(FLOOR, {**payload, "__steps": steps})
    finally:
        page.close()
    assert got["floor"] == 841                                         # 43.8cu of a 1920 x 1080 screen
    low = sorted({"%s %r at %s: down to %scu" % (row["key"], row.get("words", ""), row["f"], row["cu"]) for row in got["seen"]})
    assert low == [], "%s -- below the content's floor (43.8cu):\n%s" % (cue_id, "\n".join(low))


def test_the_floor_check_sees_a_word_a_figure_and_a_caption_below_the_floor(chromium):
    """The check above, checked: the same act with its words just above the floor passes; lowered
    into the subtitle box's zone, each of them is named -- but not one that is not shown, nor one
    cut off by the box it sits in."""
    page, said = _page(chromium)
    try:
        page.evaluate("""() => window.CeremonyShow.register('lowwords', function (ctx) {
          const at = ctx.opt('top', 40), put = (e, x) => { e.style.position = 'absolute'; e.style.left = x + '%'; e.style.top = 'calc(' + at + ' * var(--cu))'; return e; };
          const pic = ctx.picture('pic'), hidden = put(ctx.text('line'), 70), cut = ctx.el('cut');
          hidden.style.opacity = '0';
          cut.style.position = 'absolute'; cut.style.left = '85%'; cut.style.top = 'calc(30 * var(--cu))';
          cut.style.width = 'calc(10 * var(--cu))'; cut.style.height = 'calc(12 * var(--cu))'; cut.style.overflow = 'hidden';
          ctx.add(cut, put(ctx.text('line'), 0));
          ctx.add(ctx.stage, put(ctx.text('line', { cls: 'cs-type-h2' }), 5), put(ctx.figure('n'), 30), put(pic.caption, 55), hidden, cut);
          return {};
        })""")
        slide = {"template": "act", "act": "lowwords", "texts": {"line": "Main common areas"}, "videos": {}, "beats": [],
                 "figures": {"n": {"lead": "about", "value": "600", "unit": "metres"}},
                 "pictures": {"pic": {"src": "/ceremony-asset/" + "a" * 64, "w": 10, "h": 10, "caption": "The swimming pool"}}}
        # (33cu, was 36: the check reads the words' boxes, and a face's box is its declared ascent
        # + descent -- Kantalad's, the film's face since 22 Nov, is 1.76em against Inter's 1.21 (room
        # for Thai marks; the letters themselves are the same height). At 36 the 7cu figure's box
        # reached 45.6cu in Kantalad; 33 is above the floor in either face)
        above = page.evaluate(FLOOR, {**slide, "options": {"top": 33}, "__steps": [0.5]})
        below = page.evaluate(FLOOR, {**slide, "options": {"top": 45}, "__steps": [0.5]})
    finally:
        page.close()
    assert above["seen"] == [] and above["floor"] == 841
    assert {row["key"] for row in below["seen"]} == {"line", "n", "pic"}
    assert {row["words"] for row in below["seen"]} == {"Main common areas", "about600metres", "The swimming pool"}
    assert all(row["cu"] > 44 for row in below["seen"])


def test_the_stage_is_the_whole_screen_and_the_cores_own_things_keep_off_the_subtitle_box(chromium):
    """The stage ended at the subtitle band; it is the screen now. Its own things are laid out
    for that: the floor runs to the bottom edge, the credit's foot is on the content's floor,
    and the sheet of light of a cut is dim down there."""
    page, said = _page(chromium)
    try:
        got = page.evaluate("""async () => {
          const wait = ms => new Promise(r => setTimeout(r, ms));
          const eng = window.CeremonyShow;
          eng.register('mapper', function (ctx) { const p = ctx.picture('map'); ctx.add(ctx.stage, p.el); return {}; });
          eng.show({ template: 'act', act: 'mapper', texts: {}, figures: {}, videos: {}, beats: [], options: {},
                     pictures: { map: { src: '/ceremony-asset/' + 'a'.repeat(64), w: 10, h: 10, credit: 'c OpenStreetMap contributors' } } });
          await wait(2000);                   // (its hold, then the sheet's 1.2 s: the shot has landed)
          const box = e => { const r = e.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)]; };
          const stage = document.querySelector('.cs-stage'), credit = document.querySelector('.cs-credit');
          return { stage: box(stage), act: box(document.querySelector('.cs-act-stage')), credit: box(credit), on: credit.classList.contains('cs-on'),
                   grid: box(document.querySelector('.cs-grid')), ghosts: Array.from(document.querySelectorAll('.cs-ghost')).map(g => box(g)[3]) };
        }""")
    finally:
        page.close()
    assert got["stage"] == [0, 0, 1920, 1080] and got["act"] == [0, 0, 1920, 1080]
    assert got["on"] is True and 800 <= got["credit"][3] <= 841 and got["credit"][2] <= 1900       # above the floor, at the right
    assert got["grid"][3] >= 1080                                                                   # the floor runs off the bottom edge
    assert all(bottom <= 841 + 30 for bottom in got["ghosts"])                                      # the dim panels stay above the box


CREDIT_CUT = """async () => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const eng = window.CeremonyShow, rows = [];
  eng.register('mapper', function (ctx) { const p = ctx.picture('map'); ctx.add(ctx.stage, p.el); return {}; });
  eng.register('other', function (ctx) { const p = ctx.picture('pic'); ctx.add(ctx.stage, p.el); return {}; });
  eng.show({ template: 'act', act: 'mapper', texts: {}, figures: {}, videos: {}, beats: [], options: {},
             pictures: { map: { src: '/ceremony-asset/' + 'a'.repeat(64), w: 10, h: 10, credit: 'c OpenStreetMap contributors' } } });
  await wait(1200); eng.settle(); await wait(200);
  const mapRoot = document.querySelector('.cs-act-mapper'), credit = document.querySelector('.cs-credit');
  const before = eng.state().credit;
  eng.show({ template: 'act', act: 'other', texts: {}, figures: {}, videos: {}, beats: [], options: {},
             pictures: { pic: { src: '/ceremony-asset/' + 'b'.repeat(64), w: 10, h: 10 } } });
  for (let i = 0; i < 100; i++) {
    const r = mapRoot.getBoundingClientRect();
    rows.push({ cut: mapRoot.getAttribute('data-cut'), inPage: mapRoot.isConnected, left: Math.round(r.left),
                credit: credit.classList.contains('cs-on') });
    await wait(25);
  }
  eng.clear(); await wait(600);
  return { before: before, rows: rows };
}"""


def test_a_maps_credit_goes_with_the_map_when_the_light_takes_it_and_not_before(chromium):
    """Review 18 Nov (s03_l03 -> s04_l06): the OpenStreetMap line stayed on over the next shot
    until the map's slide left the page -- ~0.9 s after the sheet of light had taken the map. It
    follows what is SEEN of the map in its closing window: on while any real part of the map
    shows (ODbL), gone once the light has passed it, before its slide leaves the page."""
    page, said = _page(chromium)
    try:
        got = page.evaluate(CREDIT_CUT)
    finally:
        page.close()
    assert [w for w in said if "404" not in w] == [], said
    rows = got["rows"]
    assert got["before"] == "c OpenStreetMap contributors"
    going = [r for r in rows if r["cut"] == "out" and r["inPage"]]
    assert going, rows                                                  # the cut was seen
    # while the map still fills more than a sixth of the screen in its closing window: its line is up
    assert all(r["credit"] for r in rows if r["inPage"] and r["left"] < 1600), rows
    # the light has taken it, its slide is still in the page: the line is already going
    assert any(not r["credit"] for r in going), rows
    assert not rows[-1]["inPage"] and not rows[-1]["credit"]


def test_a_map_dissolving_away_inside_its_scene_keeps_its_credit_while_any_of_it_shows(chromium):
    """19 Nov: inside a scene the next line dissolves in over the map (no sheet). The map is
    still on the screen under it -- whole for the first part, then fading -- so its
    OpenStreetMap line stays up while any of it shows (ODbL), and goes with it."""
    page, said = _page(chromium)
    try:
        got = page.evaluate(CREDIT_CUT
                            .replace("w: 10, h: 10, credit: 'c OpenStreetMap contributors' } } });",
                                     "w: 10, h: 10, credit: 'c OpenStreetMap contributors' } } }, { cue: 's03_l03' });", 1)
                            .replace("pictures: { pic: { src: '/ceremony-asset/' + 'b'.repeat(64), w: 10, h: 10 } } });",
                                     "pictures: { pic: { src: '/ceremony-asset/' + 'b'.repeat(64), w: 10, h: 10 } } }, { cue: 's03_l05' });", 1)
                            .replace("rows.push({ cut: mapRoot.getAttribute('data-cut'),",
                                     "rows.push({ seen: +getComputedStyle(mapRoot).opacity, cut: mapRoot.getAttribute('data-cut'),", 1))
    finally:
        page.close()
    assert [w for w in said if "404" not in w] == [], said
    rows = got["rows"]
    assert got["before"] == "c OpenStreetMap contributors"
    under = [r for r in rows if r["cut"] == "under" and r["inPage"]]
    assert under and all(r["left"] == 0 for r in under), rows           # a dissolve: the map's box never moves
    assert all(r["credit"] for r in under if r["seen"] > 0.02), rows    # any of it shows: its line is up
    assert not rows[-1]["inPage"] and not rows[-1]["credit"]            # gone with it


READS = """async () => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const drawn = [];
  const real = CanvasRenderingContext2D.prototype.drawImage;
  CanvasRenderingContext2D.prototype.drawImage = function (src) {
    drawn.push(src && src.constructor ? src.constructor.name : typeof src);
    return real.apply(this, arguments);
  };
  const eng = window.CeremonyShow;
  eng.register('reader', function (ctx) {
    const person = ctx.picture('person'), model = ctx.picture('model', { keyed: 'black' });
    ctx.add(ctx.stage, person.el, model.el);
    return {};
  });
  eng.show({ template: 'act', act: 'reader', texts: {}, figures: {}, videos: {}, beats: [], options: {},
             pictures: { person: { src: '/ceremony-asset/' + 'a'.repeat(64), w: 10, h: 10, keep_out: [[0.4, 0.4, 0.6, 0.6]] },
                         model: { src: '/ceremony-asset/' + 'b'.repeat(64), w: 10, h: 10 } } });
  for (let i = 0; i < 60 && !(document.querySelector('.cs-keepout.cs-ko-filled') && document.querySelector('.cs-apic.cs-keyed.cs-loaded')); i++) await wait(25);
  const got = { drawn: drawn.slice(), filled: !!document.querySelector('.cs-keepout.cs-ko-filled'),
                keyed: !!document.querySelector('.cs-apic.cs-keyed.cs-loaded'), up: eng.state().up };
  CanvasRenderingContext2D.prototype.drawImage = real;
  eng.clear(); await wait(500);
  return got;
}"""


def test_the_cores_own_reading_of_a_picture_never_decodes_it_on_the_pages_thread(chromium):
    """Measured 18 Nov (a trace of the cut s03_l03 -> s04_l06: 434 ms of frames lost): the cover
    over a real person, a model keyed on black and a depth picture's textures each read their
    <img> -- which the page had decoded for the GPU, so each read decoded the whole render again
    on the page's own thread (119 ms for one 2560 x 1600), in the hold and the cut's first
    frames. The core reads ImageBitmaps of the file, decoded by the browser's decoder threads:
    no canvas of the core is ever handed an <img>."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    # (a well-formed picture: the page's 1x1 PNG has a bad checksum the <img> forgives and a
    # bitmap decoder does not -- the core then reads the <img>, its fallback, and this proves nothing)
    rows = b"".join(b"\x00" + b"".join(bytes([30 * x % 256, 120, 20 * y % 256, 255]) for x in range(8)) for y in range(8))
    good = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    page, said = _page(chromium)
    page.route(re.compile(r".*/ceremony-asset/[0-9a-f]{64}$"), lambda route: route.fulfill(body=good, content_type="image/png"))
    try:
        got = page.evaluate(READS)
    finally:
        page.close()
    assert [w for w in said if "404" not in w] == [], said
    assert got["filled"] and got["keyed"] and got["up"], got           # the cover is the picture's own; the model is keyed
    assert got["drawn"] and set(got["drawn"]) <= {"ImageBitmap", "HTMLCanvasElement"}, got["drawn"]
    js = ENGINE.read_text(encoding="utf-8")
    # ... and the depth picture's textures go up as bitmaps too (WebGL is off in these pages)
    assert "var jobs = [pixels(pic.url), pixels(pic.depth)];" in js and "both(bms[0], bms[1], false)" in js
    # (and nothing asks the GPU to finish its work while the page waits)
    gl = js[js.index("var both = function (photo, depth, flipped) {"):js.index("var viaImages = function () {")]
    assert "gl.getError()" not in gl[:gl.index("setTimeout(function () {")]


LATE_CLOSE = """async () => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const eng = window.CeremonyShow, shot = function (ctx) { ctx.add(ctx.stage, ctx.text('line')); return {}; };
  eng.register('one', shot); eng.register('two', shot);
  const slide = act => ({ template: 'act', act: act, texts: { line: 'Main common areas' }, figures: {}, pictures: {}, videos: {}, beats: [], options: {} });
  eng.show(slide('one')); await wait(900);
  const old = document.querySelector('.cs-act-one');
  eng.show(slide('two'));
  for (let i = 0; i < 100 && old.getAttribute('data-cut') !== 'out'; i++) await wait(5);
  // a page under load: the closing starts late and runs slow (a quarter of its speed here)
  const closing = old.getAnimations();
  closing.forEach(a => { a.playbackRate = 0.25; });
  const seen = { closing: closing.length };
  await wait(1500); seen.past = old.isConnected;
  await wait(700); seen.later = old.isConnected;
  eng.clear(); await wait(600);
  return seen;
}"""


def test_a_shot_going_down_leaves_the_page_when_its_window_has_closed_not_on_the_clock(chromium):
    """Measured 18 Nov in a page under load: the closing of the shot going down started up to a
    quarter of a second after it was asked for (an animation starts with the next frame drawn,
    and that frame has a new shot to draw) -- and the shot was taken out of the page on the
    clock, 0.64 s after the cut began, with a tenth of its window still open: it went out at
    once. It now leaves when its closing has run; a closing that is slower still is waited for
    half a second at most."""
    page, said = _page(chromium)
    try:
        seen = page.evaluate(LATE_CLOSE)
    finally:
        page.close()
    assert [w for w in said if "404" not in w] == [], said
    assert seen["closing"] >= 1                                         # the window's closing animation
    assert seen["past"] is True                                         # past the clock (1.28 s), still closing: still in the page
    assert seen["later"] is False                                       # ... and gone, at most half a second later


GLOW_BOX = """async () => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const eng = window.CeremonyShow;
  let head = null;
  eng.register('glowbox', function (ctx) {
    const plate = ctx.el('plate');
    plate.style.position = 'absolute'; plate.style.left = '0'; plate.style.top = '0';
    plate.style.width = '100%'; plate.style.height = '100%'; plate.style.background = '#000';
    head = ctx.text('head', { cls: 'cs-type-h1' });
    head.style.position = 'absolute'; head.style.left = 'calc(30 * var(--cu))'; head.style.top = 'calc(20 * var(--cu))';
    ctx.add(ctx.stage, plate, head);
    ctx.bg('off');
    return {};
  });
  eng.show({ template: 'act', act: 'glowbox', texts: { head: 'What kind of view do you like?' }, figures: {}, pictures: {},
             videos: {}, beats: [], options: {} });
  for (let i = 0; i < 100 && !(head && head.closest('.cs-live')); i++) await wait(20);
  await wait(1500);
  const r = head.getBoundingClientRect(), em = parseFloat(getComputedStyle(head).fontSize);
  return { l: r.left - 0.8 * em, t: r.top - 0.62 * em, r: r.right + 0.8 * em, b: r.bottom + 0.62 * em,
           em: em, glow: head.classList.contains('cs-text-glow') };
}"""


def test_a_headlines_glow_is_a_soft_light_with_no_edges(chromium):
    """Review 18 Nov (the units / smarthome and living photos frames): every headline sat on a
    pale RECTANGLE -- the halo's two glows were ellipses larger than their box, still at a sixth of
    their light where the box cut them off. On black, the light just inside the halo's box and just
    outside it are now the same: it fades to nothing inside its box, on every side."""
    from io import BytesIO

    from PIL import Image

    page, said = _page(chromium)
    try:
        box = page.evaluate(GLOW_BOX)
        shot = Image.open(BytesIO(page.screenshot())).convert("L")
    finally:
        page.close()
    assert box["glow"] is True                                          # a headline has its halo by default
    l, t, r, b = (int(round(box[k])) for k in ("l", "t", "r", "b"))

    def mean(x0, y0, x1, y1):
        px = [shot.getpixel((x, y)) for x in range(x0, x1) for y in range(y0, y1)]
        return sum(px) / len(px)

    mid0, mid1 = l + (r - l) // 5, r - (r - l) // 5
    cy0, cy1 = t + (b - t) // 3, b - (b - t) // 3
    steps = {
        "top": mean(mid0, t + 2, mid1, t + 5) - mean(mid0, t - 5, mid1, t - 2),
        "bottom": mean(mid0, b - 5, mid1, b - 2) - mean(mid0, b + 2, mid1, b + 5),
        "left": mean(l + 2, cy0, l + 5, cy1) - mean(l - 5, cy0, l - 2, cy1),
        "right": mean(r - 5, cy0, r - 2, cy1) - mean(r + 2, cy0, r + 5, cy1),
    }
    assert all(abs(v) < 2.5 for v in steps.values()), steps            # no edge: the box is not seen
    # ... and the halo is there: lit above the words, between them and the box's edge
    em, x = box["em"], l + int((r - l) * 0.32)
    above = mean(x - 20, t + int(0.3 * em), x + 20, t + int(0.45 * em))
    assert above - mean(x - 20, t - 12, x + 20, t - 4) > 6, above


