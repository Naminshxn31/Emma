"""The ceremony's motion slides on the event screen (owner 17 Nov 2026: "ทำตัวพรีเซ้นต์โมชั่น
... ลิ้งระหว่างสไลด์กับเสียงหุ่น ... มีซับภาษาไทยด้านล่าง ... ขนาด 16:9").
app/ceremony_slides.py (the checks and the gate), app/face_mirror.py + app/event_cues.py
(the slide rides with the line's subtitle), client/assets/ceremony-show.js/.css (the
engine), client/voice-preview.html (the room screen's glue), scripts/approve_ceremony_slides.py,
scripts/preview_ceremony_show.py."""
import asyncio
import copy
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import ceremony_slides as cs
from app import event_cues, event_script, face_mirror
from app.config import settings

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "client" / "voice-preview.html"
ENGINE = ROOT / "client" / "assets" / "ceremony-show.js"
STYLE = ROOT / "client" / "assets" / "ceremony-show.css"
CEREMONY = ROOT / "data" / "ceremony" / "floor1_ceremony.json"
SLIDES = ROOT / "data" / "ceremony" / "floor1_ceremony.slides.json"


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def real(monkeypatch):
    """The real ceremony and its slides (conftest empties CEREMONY_FILE)."""
    monkeypatch.setattr(settings, "ceremony_file", "data/ceremony/floor1_ceremony.json")
    monkeypatch.setattr(settings, "stories_enabled", True)       # the pictures' address answers only then
    event_script.reset()
    cs.reset()
    yield cs.load()                                              # the base file with slides/<cue id>.json over it
    event_script.reset()
    cs.reset()


@pytest.fixture
def copied(monkeypatch, tmp_path):
    """A copy of both files a test may sign and edit -- the slides copy always UNSIGNED: the real file was signed by
    the owner on 24 Nov, and these tests are about a draft (CLAUDE.md: a test of an approval gate pins its own state,
    never what the real file happens to be today)."""
    shutil.copy(CEREMONY, tmp_path / "c.json")
    shutil.copy(SLIDES, tmp_path / "c.slides.json")
    _edit(tmp_path / "c.slides.json",
          lambda d: d.update({k: None for k in d if k.startswith("approved")}))
    monkeypatch.setattr(settings, "ceremony_file", str(tmp_path / "c.json"))
    event_script.reset()
    cs.reset()
    yield tmp_path / "c.slides.json"
    event_script.reset()
    cs.reset()


def _edit(path: Path, change) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    change(doc)
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    cs.reset()


#: A cue to check single rules against: the beach line's own words.
CUE = {"id": "x", "en": "Embassy World is about 600 metres from Jomtien Beach in a straight line, with a 155-metre "
                        "swimming pool and plenty of shopping.",
       "th": "Embassy World ห่างจากหาดจอมเทียนเป็นเส้นตรงประมาณ 600 เมตรค่ะ มีสระว่ายน้ำ 155 เมตร และแหล่งช้อปปิ้ง"}
GOOD = {"template": "number", "kicker": "Embassy World", "lead": "about", "value": "600", "unit": "metres",
        "label": "from Jomtien Beach in a straight line", "chips": ["Shopping"],
        "beats": [{"show": "value", "at_en": "about 600"}, {"show": "label", "at_en": "from Jomtien"},
                  {"show": "chip:0", "at_en": "shopping"}]}


def _with(**changes) -> dict:
    slide = copy.deepcopy(GOOD)
    for key, value in changes.items():
        if value is None:
            slide.pop(key, None)
        else:
            slide[key] = value
    return slide


# ---- the real file ----------------------------------------------------------------------

def test_every_worded_cue_has_a_slide_that_passes_and_the_wordless_one_has_none(real):
    cues = {c["id"]: c for c in event_script.cues()}
    worded = {cid for cid, c in cues.items() if c.get("en") and c.get("th")}
    assert len(worded) == 29 and set(real["slides"]) == worded
    assert "s05_l07" in cues and "s05_l07" not in real["slides"]        # the demo room: no words yet
    for row in cs.status(real):
        assert row["problems"] == [], row
        assert cs.for_cue(row["id"], drafts=True)["template"] == row["template"]
    # (which templates the set uses is the authors' business; every template is drawn and
    # checked by its own tests below, with slides built there)
    assert {s["template"] for s in real["slides"].values()} <= set(cs.TEMPLATES) | {cs.ACT}


def test_nobody_signed_the_slides_on_the_owners_behalf(real):
    """A draft until scripts/approve_ceremony_slides.py is run by a person: either unsigned,
    or signed for exactly these slides."""
    assert real["approved_by"] is None or cs.approved(real)
    source = (ROOT / "app" / "ceremony_slides.py").read_text(encoding="utf-8")
    assert "write_text" not in source and ".update({\"approved" not in source and "open(" not in source
    # and no code under app/ shows drafts: that door is the preview script's only
    for path in (ROOT / "app").rglob("*.py"):
        assert not re.search(r"for_cue\([^)]*drafts\s*=\s*True", path.read_text(encoding="utf-8")), path.name


def test_the_owners_fixed_figures_are_said_as_the_line_says_them(real):
    def words(cid):
        slide = real["slides"][cid]
        out = []

        def take(value):
            if isinstance(value, str):
                out.append(value)
            elif isinstance(value, list):
                for v in value:
                    take(v)
            elif isinstance(value, dict):
                for k, v in value.items():
                    if k not in ("beats", "template", "icon", "device", "state"):
                        take(v)
        take(slide)
        return " ".join(out).lower()

    beach = words("s03_l03")
    assert "600" in beach and "about" in beach and "straight line" in beach
    deep = words("s06_l03")
    for piece in ("1.3", "8", "23", "2,500", "around", "estimate", "about"):
        assert piece in deep
    assert "1,066" in words("s04_l03") and "155" in words("s04_l06") and "155" in words("s04_l11")
    everything = " ".join(words(cid) for cid in real["slides"])
    for banned in ("rooftop", "price", "promotion", "concept", "invest", "subject to change"):
        assert banned not in everything


#: A joke told over two cues: the set-up, then its punchline.
JOKES = {"s04_l09": "s04_l11", "s06_l05": "s06_l07"}


def _on_screen(slide: dict) -> tuple[list[str], list[str]]:
    """(every text a slide shows, every picture it names)."""
    texts, pictures = [], []

    def take(value, key=""):
        if isinstance(value, str):
            if key in ("asset", "backdrop"):
                pictures.append(value)
            elif key not in ("template", "icon", "device", "state", "fit", "side", "mood", "graphic",
                             "from_icon", "to_icon", "show", "at_en", "at_th"):
                texts.append(value)
        elif isinstance(value, list):
            for v in value:
                take(v, key)
        elif isinstance(value, dict):
            for k, v in value.items():
                take(v, k)
    take({k: v for k, v in slide.items() if k != "beats"})
    return texts, pictures


def test_a_jokes_set_up_does_not_give_the_punchline_away(real):
    """The rule, whatever templates the authors chose: a set-up cue shows no word, number,
    icon or picture of its punchline. Words and numbers are kept out by "only this cue's own
    line"; a picture by its `about` words, which must be this line's too -- so nothing the
    punchline alone says can back anything on the set-up's screen."""
    listed = cs.asset_registry()
    for setup_id, punch_id in JOKES.items():
        setup, punch = event_script.cue(setup_id), event_script.cue(punch_id)
        own = cs._words(setup["en"])
        only_punch = {w for w in cs._words(punch["en"]) if not cs._in_line(w, own) and w not in cs.ALLOWED_WORDS}
        punch_numbers = {t for t in cs._toks(punch["en"]) if t[0].isdigit()} - {t for t in cs._toks(setup["en"]) if t[0].isdigit()}
        slide = real["slides"][setup_id]
        assert cs.problems(slide, setup) == [], setup_id
        texts, pictures = _on_screen(slide)
        shown = cs._words(" ".join(texts))
        assert not {w for w in only_punch if cs._in_line(w, shown)}, (setup_id, shown & only_punch)
        assert not punch_numbers & {t for t in cs._toks(" ".join(texts)) if t[0].isdigit()}, setup_id
        for picture in pictures:
            # what puts the picture here is the SET-UP's own line (or it is an establishing picture),
            # and nothing it is "about" is a thing only the punchline says
            entry = listed.get(picture) or cs._described(picture)          # (a checked candidate: the index says)
            assert entry.get("establishing") is True or cs._backed(entry, cs._toks(setup["en"])), (setup_id, picture)
            assert not any(set(cs._toks(a)) & only_punch for a in entry["about"]), (setup_id, picture)
    # the two punchlines' giveaways, named: nothing of a pool on the first set-up, of winning on the second
    first, second = (" ".join(_on_screen(real["slides"][c])[0]).lower() for c in JOKES)
    assert not re.search(r"pool|swim|155", first) and not re.search(r"first|place|quiz|superfan", second)
    for p in _on_screen(real["slides"]["s04_l09"])[1]:
        # (review 17 Nov: it opened on the glowing lagoon -- the punchline's pool)
        # (a checked candidate of the index -- the logo page at its end, review 20 Nov -- is described
        # by the index, as above)
        assert not re.search(r"pool|lagoon|swim|campus_holo", p) and "pool" not in " ".join((listed.get(p) or cs._described(p))["about"]), p
    # ... and the checks that keep it so, on slides built here
    cue = event_script.cue("s04_l09")
    base = {"template": "hero", "title": "I've studied this project very carefully"}
    assert cs.problems(base, cue) == []
    assert "icon_not_in_line:pool" in cs.problems({**base, "icon": "pool"}, cue)
    assert "word_not_in_line:pool" in cs.problems({**base, "sub": "The pool"}, cue)
    assert "number_not_in_line:155" in cs.problems({**base, "sub": "155"}, cue)
    for pool_picture in ("lagoon_scene58", "liv_lagoon_night", "fac_b_aqua_cinema", "liv_kid_pool"):
        assert "asset_not_in_line:%s" % pool_picture in cs.problems({**base, "backdrop": pool_picture}, cue)
    # inside one cue: a `joke` slide's punchline and a "done" tick are never in the opening
    # (an act's slide: nothing before she reaches its words, tests/test_ceremony_acts.py)
    for cid, slide in real["slides"].items():
        if slide["template"] == cs.ACT:
            continue
        gated = {b["show"] for b in slide["beats"]}
        if slide["template"] == "joke" and slide.get("punch"):
            assert "punch" in gated, cid


# ---- one slide against its cue ------------------------------------------------------------

def test_a_clean_slide_passes_and_its_beats_are_places_in_the_line():
    assert cs.problems(GOOD, CUE) == []
    payload = cs.page_payload(GOOD, CUE)
    line = CUE["en"]
    assert payload["beats"] == [
        {"at": round(line.index("about 600") / len(line), 4), "show": "value"},
        {"at": round(line.index("from Jomtien") / len(line), 4), "show": "label"},
        {"at": round(line.index("shopping") / len(line), 4), "show": "chip:0"}]
    assert set(payload) == {"template", "kicker", "lead", "value", "unit", "label", "chips", "beats"}


@pytest.mark.parametrize("change, problem", [
    ({"value": "500"}, "number_not_in_line:500"),                      # a number the line does not say
    ({"label": "from Jomtien Beach in a straight line, 5 metres"}, "number_not_in_line:5"),
    ({"chips": ["Shopping", "Hospitals"]}, "word_not_in_line:hospitals"),   # a fact the line does not say
    ({"label": "from the beach, a walk"}, "word_not_in_line:walk"),
    ({"lead": None}, "number_lost_its_qualifier:600:about"),           # the owner's "about 600"
    ({"label": "from Jomtien Beach"}, "number_lost_its_qualifier:600:straight line"),
    ({"kicker": "Embassy World price"}, "banned:price"),
    ({"kicker": "Embassy World rooftop pool"}, "banned:rooftop"),
    ({"kicker": "Embassy World roof deck"}, "banned:roof deck"),
    ({"kicker": "Embassy World concept"}, "banned:concept"),
    ({"kicker": "Embassy World promotion"}, "banned:promotion"),
    ({"kicker": "Embassy World to invest"}, "banned:invest"),
    ({"kicker": "subject to change"}, "banned:subject to change"),
    ({"kicker": "Embassy World /assets/a.png"}, "banned:.png"),
    ({"kicker": "ดาดฟ้า"}, "thai_on_slide"),
    ({"kicker": "ซื้ออยู่เองหรือลงทุน"}, "thai_on_slide"),
    ({"template": "poster"}, "unknown_template"),
    ({"image": "/story-asset/abc"}, "unknown_field:image"),
    ({"from_icon": "rocket"}, "unknown_icon:from_icon"),
    ({"from_icon": "hospital"}, "icon_not_in_line:hospital"),          # an icon is a claim too
    ({"unit": None}, "missing:unit"),
    ({"value": "six hundred"}, "not_a_number:value"),
    ({"chips": "Shopping"}, "bad_list:chips"),
    ({"unit": ""}, "bad_text:unit"),
    ({"unit": "m" * 91}, "bad_text:unit"),
    ({"graphic": "map"}, "bad_choice:graphic"),
])
def test_what_a_slide_may_not_carry(change, problem):
    assert problem in cs.problems(_with(**change), CUE)


def test_a_slide_needs_a_cue_with_words():
    assert cs.problems(GOOD, None) == ["unknown_cue"]
    assert cs.problems(GOOD, {"id": "x", "en": None, "th": None}) == ["cue_has_no_words"]
    assert cs.problems(GOOD, {"id": "x", "en": CUE["en"], "th": None}) == ["cue_has_no_words"]
    assert cs.problems("hero", CUE) == ["not_a_slide"]


@pytest.mark.parametrize("beats, problem", [
    ([{"show": "value", "at_en": "about 700"}], "beat_not_in_line:en:value"),
    ([{"show": "value", "at_th": "ประมาณ 700"}], "beat_not_in_line:th:value"),
    ([{"show": "label", "at_en": "from Jomtien"}, {"show": "value", "at_en": "about 600"}], "beats_out_of_order:en:value"),
    ([{"show": "value", "at": 0.5}, {"show": "label", "at": 0.2}], "beats_out_of_order:at:label"),
    ([{"show": "price", "at": 0.5}], "beat_shows_nothing:price"),
    ([{"show": "chip:1", "at": 0.5}], "beat_shows_nothing:chip:1"),
    ([{"show": "value", "at": 0.1}, {"show": "value", "at": 0.2}], "beat_twice:value"),
    ([{"show": "value", "at": 1.5}], "bad_beat_at:value"),
    ([{"show": "value", "at": True}], "bad_beat_at:value"),
    ([{"show": "value"}], "beat_has_no_place:value"),
    ([{"show": "value", "at": 0.1, "delay_ms": 300}], "bad_beat:0"),
    ("value", "bad_beats"),
])
def test_what_a_beat_may_not_be(beats, problem):
    assert problem in cs.problems(_with(beats=beats), CUE)


def test_a_beat_is_placed_in_the_language_she_says_the_line_in():
    slide = _with(beats=[{"show": "value", "at_en": "about 600", "at_th": "ประมาณ 600"},
                         {"show": "label", "at_th": "เมตรค่ะ"},
                         {"show": "chip:0", "at": 0.9}])
    assert cs.problems(slide, CUE) == []
    en, th = cs.norm(CUE["en"]), cs.norm(CUE["th"])
    said_en = {b["show"]: b["at"] for b in cs.beats_of(slide, CUE, "en")[0]}
    said_th = {b["show"]: b["at"] for b in cs.beats_of(slide, CUE, "th")[0]}
    assert said_en["value"] == round(en.index("about 600") / len(en), 4)
    assert said_th["value"] == round(th.index("ประมาณ 600") / len(th), 4)
    assert said_en["value"] != said_th["value"]
    # only the Thai piece is given: it places the beat under an English reading too
    assert said_en["label"] == said_th["label"] == round(th.index("เมตรค่ะ") / len(th), 4)
    assert said_en["chip:0"] == 0.9
    # sent in voice order, whatever the file's order of equal-less places
    assert [b["at"] for b in cs.beats_of(slide, CUE, "en")[0]] == sorted(said_en.values())


def test_every_engine_icon_is_backed_and_every_backed_icon_is_drawn():
    got = _engine({"x": {"steps": []}})["x"]
    assert sorted(got["icons"]) == sorted(cs.ICONS)
    assert sorted(got["templates"]) == sorted(cs.TEMPLATES)


# ---- the gate: a draft sends nothing ------------------------------------------------------

def test_an_unsigned_file_serves_no_slide_and_the_preview_door_is_separate(copied, caplog):
    assert cs.approved(cs.load()) is False
    with caplog.at_level("WARNING"):
        assert cs.for_cue("s03_l03") is None
        assert cs.for_cue("s03_l03") is None
    assert sum("a draft" in r.message for r in caplog.records) == 1          # said once, not per press
    assert cs.for_cue("s03_l03", drafts=True)["value"] == "600"


def test_signing_serves_the_slides_and_any_edit_makes_them_drafts_again(copied):
    approve = _script("approve_ceremony_slides")
    with pytest.raises(SystemExit):
        approve.approve(copied, "  ", audit=None)
    sha = approve.approve(copied, "tester", audit=None)
    cs.reset()
    doc = cs.load()
    assert doc["approved_by"] == "tester" and doc["approved_sha256"] == sha == cs.digest(doc) and cs.approved(doc)
    assert cs.for_cue("s03_l03")["template"] == "number"
    assert cs.for_cue("s05_l07") is None and cs.for_cue("nope") is None and cs.for_cue(None) is None
    # one word of one slide changed: nothing is served until somebody signs again
    _edit(copied, lambda d: d["slides"]["s09_l02"].update(kicker="Enjoy"))
    assert cs.approved(cs.load()) is False
    assert cs.for_cue("s03_l03") is None and cs.for_cue("s09_l02") is None
    assert "CHANGED since approval" in approve.status_lines(cs.load())[0]
    # a signature copied onto other slides is no signature
    _edit(copied, lambda d: d.update(approved_sha256="0" * 64))
    assert cs.for_cue("s03_l03") is None
    assert approve.revoke(copied) is True and cs.load()["approved_by"] is None


def test_the_approval_refuses_while_a_slide_breaks_a_rule(copied):
    approve = _script("approve_ceremony_slides")
    _edit(copied, lambda d: d["slides"]["s03_l03"].update(value="500"))
    with pytest.raises(SystemExit) as stop:
        approve.approve(copied, "tester", audit=None)
    assert "s03_l03" in str(stop.value) and "number_not_in_line:500" in str(stop.value)
    assert cs.load()["approved_by"] is None


def test_one_bad_slide_is_not_served_and_the_others_are(copied, caplog):
    approve = _script("approve_ceremony_slides")
    approve.approve(copied, "tester", audit=None)
    # the cue's words change under a signed slide: checked against the line every time
    script = copied.with_name("c.json")
    doc = json.loads(script.read_text(encoding="utf-8"))
    for c in doc["cues"]:
        if c["id"] == "s03_l03":
            c["en"], c["th"] = c["en"].replace("600", "700"), c["th"].replace("600", "700")
    script.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    event_script.reset()
    cs.reset()
    with caplog.at_level("WARNING"):
        assert cs.for_cue("s03_l03") is None
    assert any("s03_l03" in r.message and "number_not_in_line:600" in r.message for r in caplog.records)
    assert cs.for_cue("s04_l03")["value"] == "1,066"


def test_a_broken_or_missing_file_is_no_slides_and_never_an_error(copied):
    for body in ("{", "[]", json.dumps({"format": "other", "slides": {}}),
                 json.dumps({"format": cs.FORMAT, "slides": []}),
                 json.dumps({"format": cs.FORMAT, "approved_by": "x", "approved_at": "y", "slides": {"s03_l03": 5}})):
        copied.write_text(body, encoding="utf-8")
        cs.reset()
        assert cs.for_cue("s03_l03") is None and cs.for_cue("s03_l03", drafts=True) is None
    copied.unlink()
    cs.reset()
    assert cs.for_cue("s03_l03", drafts=True) is None
    settings.ceremony_file = ""
    cs.reset()
    assert cs.path() is None and cs.for_cue("s03_l03", drafts=True) is None


# ---- the server's message -----------------------------------------------------------------

def _screen() -> asyncio.Queue:
    face_mirror.reset()
    queue: asyncio.Queue = asyncio.Queue()
    face_mirror._clients.add(queue)
    return queue


def _got(queue: asyncio.Queue) -> list[dict]:
    out = []
    while not queue.empty():
        item = queue.get_nowait()
        if isinstance(item, str):
            out.append(json.loads(item))
    return out


def test_the_slide_rides_with_the_subtitle_and_a_joining_screen_is_told_it_is_late():
    queue = _screen()
    face_mirror.call_started()
    slide = {"template": "hero", "title": "Hello", "beats": [{"at": 0.0, "show": "title"}]}
    face_mirror.subtitle({"id": "a", "text": "สวัสดีค่ะ", "seconds": 3, "slide": slide})
    sent = [m for m in _got(queue) if m["type"] == "mirror_subtitle"]
    assert sent == [{"type": "mirror_subtitle", "id": "a", "text": "สวัสดีค่ะ", "seconds": 3.0, "slide": slide}]
    # joined before her first sound of it: the line as it went out (the screen's own clock will do)
    assert json.loads(face_mirror.snapshot()[-1]) == sent[0]
    face_mirror.publish_audio(b"\x00\x00" * 240)
    began = face_mirror._state["subtitle_voice_at"]
    face_mirror.publish_audio(b"\x00\x00" * 240)
    assert face_mirror._state["subtitle_voice_at"] == began          # her FIRST sound of this line
    face_mirror._state["subtitle_voice_at"] = began - 4.0            # ... four seconds ago
    joined = json.loads(face_mirror.snapshot()[-1])
    assert 4.0 <= joined.pop("late") < 5.0 and joined == sent[0]
    # over: a screen that joins now gets no slide of a line nobody is saying
    face_mirror.subtitle_end("a")
    assert all("slide" not in json.loads(x) for x in face_mirror.snapshot())
    # no slide = the message as it was before slides existed, to the byte
    face_mirror.subtitle({"id": "b", "text": "คำ"})
    assert _got(queue)[-1] == {"type": "mirror_subtitle", "id": "b", "text": "คำ"}
    assert json.loads(face_mirror.snapshot()[-1]) == {"type": "mirror_subtitle", "id": "b", "text": "คำ"}
    for bad in (["hero"], "hero", {"title": "x"}, {"template": 5}):
        face_mirror.subtitle({"id": "c", "text": "คำ", "slide": bad})
        assert "slide" not in _got(queue)[-1]


def test_the_faces_own_stream_cannot_put_a_slide_on_the_screen():
    slide = {"template": "hero", "title": "x"}
    assert face_mirror.view({"type": "mirror_subtitle", "text": "x", "slide": slide}) is None
    out = face_mirror.view({"type": "assistant_transcript", "text": "x", "slide": slide})
    assert out == {"type": "assistant_transcript", "text": "x"}
    out = face_mirror.view({"type": "tool_result", "result": {"screen": "clear", "slide": slide}})
    assert out == {"type": "tool_result", "result": {"screen": "clear"}}


def test_a_cue_sends_its_slide_only_from_a_signed_file(copied):
    cue = event_script.spoken(event_script.cue("s03_l03"))
    assert cue["lang"] == "en" and cue["sub_th"]
    queue = _screen()
    face_mirror.call_started()
    _got(queue)
    event_cues._subtitle({"id": cue["id"], "cue": cue}, 12.0)
    draft = _got(queue)
    assert len(draft) == 1 and "slide" not in draft[0] and draft[0]["text"] == cue["sub_th"]   # as today
    _script("approve_ceremony_slides").approve(copied, "tester", audit=None)
    cs.reset()
    event_cues._subtitle({"id": cue["id"], "cue": cue}, 12.0)
    signed = _got(queue)[0]
    assert signed["slide"] == cs.for_cue("s03_l03") and signed["slide"]["value"] == "600"
    assert {k: v for k, v in signed.items() if k != "slide"} == draft[0]


def test_a_slide_that_blows_up_never_costs_the_line_its_subtitle(copied, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("slides")

    monkeypatch.setattr(cs, "for_cue", boom)
    cue = event_script.spoken(event_script.cue("s03_l03"))
    queue = _screen()
    face_mirror.call_started()
    _got(queue)
    run = {"id": cue["id"], "cue": cue}
    event_cues._subtitle(run, 5.0)
    assert [m["type"] for m in _got(queue)] == ["mirror_subtitle"] and run["subtitled"] is True
    # ... and inside for_cue itself, an error is "no slide"
    monkeypatch.undo()
    monkeypatch.setattr(settings, "ceremony_file", str(copied.with_name("c.json")))
    monkeypatch.setattr(cs, "problems", boom)
    assert cs.for_cue("s03_l03", drafts=True) is None


# ---- the engine -----------------------------------------------------------------------------

def _node(script: str, cases: dict, *, stdin: bool) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required: NOT checked here -- which part of a ceremony slide is on the event "
                    "screen at which point of her voice, and that a stop takes it down")
    args = [node, script] + ([] if stdin else [json.dumps(cases)])
    result = subprocess.run(args, cwd=ROOT, input=json.dumps(cases) if stdin else None,
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def _engine(cases: dict) -> dict:
    return _node("tests/client_ceremony_show.cjs", cases, stdin=True)


def _glue(cases: dict) -> dict:
    return _node("tests/client_mirror_subtitle.cjs", cases, stdin=False)


HERO = {"template": "hero", "kicker": "Embassy World", "icon": "spark", "title": "Hello, everyone", "sub": "I'm Emma",
        "chips": ["Sales team", "Agents"],
        "beats": [{"at": 0, "show": "title"}, {"at": 0.3, "show": "sub"}, {"at": 0.6, "show": "chip:0"},
                  {"at": 0.8, "show": "chip:1"}]}


def test_before_her_voice_only_the_opening_and_then_each_part_at_its_beat():
    got = _engine({"x": {"steps": [
        {"show": HERO}, {"tick": 100},
        {"progress": 0}, {"progress": 0.29}, {"progress": 0.31}, {"progress": 0.7}, {"progress": 1},
        {"tick": 5000}]}})["x"]["seen"]
    assert got[0]["ret"] is True and got[0]["shown"] == [] and got[0]["on"] and got[0]["stageUp"]
    assert got[1]["shown"] == ["icon", "kicker"]                      # no beat names them: the opening
    assert got[1]["hidden"] == ["chip:0", "chip:1", "sub", "title"]
    assert got[2]["shown"] == ["icon", "kicker", "title"]             # her first sound
    assert got[3]["shown"] == got[2]["shown"]                         # 0.29: not yet
    assert "sub" in got[4]["shown"] and "chip:0" not in got[4]["shown"]
    assert "chip:0" in got[5]["shown"] and "chip:1" not in got[5]["shown"]
    assert got[6]["hidden"] == [] and got[7]["hidden"] == []
    assert got[7]["state"]["settled"] is False and got[7]["live"] == 1    # still up: nothing takes it down by itself


def test_no_clock_of_its_own_moves_a_beat():
    got = _engine({"x": {"steps": [{"show": HERO}, {"tick": 60000}]}})["x"]["seen"]
    assert got[1]["shown"] == ["icon", "kicker"]                      # a minute with no voice: the opening still


def test_the_line_over_settles_it_a_stop_takes_it_down_and_the_next_line_replaces_it():
    stats = {"template": "stats", "stats": [{"value": "8", "unit": "storeys", "label": "The building"},
                                             {"lead": "around", "value": "2,500", "unit": "square metres", "label": "Indoor"}],
             "beats": [{"at": 0.2, "show": "stat:0"}, {"at": 0.7, "show": "stat:1"}]}
    got = _engine({
        "settle": {"steps": [{"show": HERO}, {"tick": 100}, {"progress": 0.31}, {"settle": True}, {"tick": 3000}]},
        "clear": {"steps": [{"show": HERO}, {"tick": 100}, {"progress": 0.5}, {"clear": True}, {"tick": 1000},
                            {"progress": 1}, {"settle": True}]},
        "next": {"steps": [{"show": HERO}, {"tick": 100}, {"progress": 1}, {"show": stats}, {"tick": 100},
                           {"tick": 1000}, {"progress": 0.25}]},
        "bad": {"steps": [{"show": HERO}, {"tick": 100}, {"show": {"template": "poster", "title": "x"}}, {"tick": 1000},
                          {"show": None}, {"show": {"template": "list", "title": "x"}}]},
    })
    settle = got["settle"]["seen"]
    assert settle[3]["hidden"] == [] and settle[3]["state"]["settled"] is True
    assert settle[4]["live"] == 1 and settle[4]["on"]                 # it stays
    clear = got["clear"]["seen"]
    assert clear[3]["live"] == 0 and not clear[3]["on"] and not clear[3]["stageUp"] and clear[3]["state"] == {"up": False, "idle": False}
    assert clear[4]["slides"] == 0                                    # faded, then out of the page
    assert clear[6]["state"] == {"up": False, "idle": False} and clear[6]["live"] == 0   # a late progress/settle raises nothing
    nxt = got["next"]["seen"]
    assert nxt[3]["live"] == 1 and nxt[3]["slides"] == 2 and "cs-t-stats" in nxt[3]["kinds"][0]
    assert nxt[5]["slides"] == 1 and nxt[5]["shown"] == []            # the old one gone, the new one's opening
    assert nxt[6]["shown"] == ["stat:0"]
    bad = got["bad"]["seen"]
    assert bad[2]["ret"] is False and bad[2]["live"] == 0 and not bad[2]["on"]     # unknown template: the backdrop
    assert bad[4]["ret"] is False and bad[5]["ret"] is False and bad[5]["live"] == 0   # a list with no items: not drawn


def test_a_screen_that_joined_mid_line_gets_the_opening_and_never_the_end_state_early():
    """Review 17 Nov: `late` used to settle the slide -- a joke's punchline or a "done" tick
    on the screen ahead of her words."""
    joke = {"template": "joke", "title": "If there is ever a quiz", "tail": True, "punch": "First place!",
            "icon": "trophy", "beats": [{"at": 0.7, "show": "icon"}, {"at": 0.7, "show": "punch"}]}
    done = {"template": "status", "state": "done", "device": "lights_on", "title": "The lobby lights are on",
            "beats": [{"at": 0, "show": "title"}]}
    got = _engine({"joke": {"steps": [{"show": joke, "late": True}, {"progress": 0.5}, {"tick": 2000},
                                      {"progress": 0.75}]},
                   "done": {"steps": [{"show": done, "late": True}, {"tick": 2000}, {"progress": 0.1}]}})
    seen = got["joke"]["seen"]
    assert seen[0]["shown"] == ["title"] and seen[0]["state"]["settled"] is False     # there at once, no beat fired
    assert "cs-instant" in seen[0]["kinds"][0]
    assert seen[1]["shown"] == ["title"] and seen[2]["shown"] == ["title"]            # 0.5: still the set-up
    assert "cs-instant" not in seen[2]["kinds"][0]                                    # later parts animate in again
    assert seen[3]["hidden"] == []
    tick = got["done"]["seen"]
    assert "title" not in tick[0]["shown"] and "title" not in tick[1]["shown"]        # no tick without her voice
    assert "title" in tick[2]["shown"]


def test_a_figure_only_ever_shows_its_own_value():
    """Picture review 17 Nov: "around 2,000 square metres" was on the screen on its way to 2,500 --
    a wrong figure a guest can photograph. The project's rule (the story engine's countStep): no
    value on the way that reads as one. Every figure of the ceremony, sampled every 40 ms."""
    cases = {}
    for value in ("1,066", "600", "155", "23", "8", "1.3", "2,500"):
        number = {"template": "number", "value": value, "unit": "units", "beats": [{"at": 0.1, "show": "value"}]}
        film = {"template": "film", "shots": [{"asset": _pic(1)}, {"asset": _pic(2), "figure": {"value": value, "unit": "m"}}],
                "figure": {"value": value, "unit": "m"},
                "beats": [{"at": 0.1, "show": "shot:1"}, {"at": 0.1, "show": "figure"}]}
        stats = {"template": "stats", "stats": [{"value": value, "unit": "m", "label": "x"}, {"value": "8", "unit": "m", "label": "y"}],
                 "beats": [{"at": 0.1, "show": "stat:0"}]}
        for name, slide in (("number", number), ("film", film), ("stats", stats)):
            cases["%s %s" % (name, value)] = {"steps": [{"show": slide}, {"load": True}, {"tick": 100}, {"progress": 0.2}]
                                              + [{"tick": 40}] * 60}
    for name, got in _engine(cases).items():
        value = name.split(" ")[1]
        for step in got["seen"]:
            for text, counting in step["nums"]:
                assert not counting and text in (value, "8"), (name, text)
        assert value in got["seen"][-1]["landed"], name                 # it arrives (a small pop), as itself
    js = ENGINE.read_text(encoding="utf-8")
    assert "countUp" not in js and "cs-counting" not in js and "toLocaleString" not in js
    assert "cs-counting" not in STYLE.read_text(encoding="utf-8")


def test_every_real_slide_draws_and_shows_only_its_own_words(real):
    cases = {}
    # (the templates' slides; an act's slide is run with its own code in a real page --
    # tests/test_ceremony_acts.py, every act slide of the folder)
    drawn = {cid: slide for cid, slide in real["slides"].items() if slide["template"] != cs.ACT}
    assert len(drawn) + len([s for s in real["slides"].values() if s["template"] == cs.ACT]) == 29
    for cid in drawn:
        payload = cs.for_cue(cid, drafts=True)
        cases[cid] = {"steps": [{"show": payload}, {"tick": 100}, {"progress": 1}, {"tick": 3000}, {"settle": True}]}
    got = _engine(cases)
    for cid, slide in drawn.items():
        seen = got[cid]["seen"]
        assert seen[0]["ret"] is True and seen[2]["hidden"] == [], cid
        gated = {b["show"] for b in slide["beats"]}
        assert set(seen[1]["shown"]) & gated == set(), cid           # nothing a beat names is in the opening
        assert set(seen[2]["shown"]) >= set(cs.keys_of(slide)), cid
        # every word on the screen is a word of the slide entry (the engine adds none)
        entry = " ".join(re.findall(r"[A-Za-z0-9,.]+", json.dumps(slide, ensure_ascii=False))).lower()
        for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9,.]*", seen[3]["text"]):
            assert word.lower().strip(",.") in entry, (cid, word)


def test_a_choices_pick_lights_one_option():
    choice = {"template": "choice", "title": "Which view?", "pick": 1,
              "options": [{"text": "The sea", "icon": "sea"}, {"text": "The pool", "icon": "pool"}],
              "beats": [{"at": 0.2, "show": "option:0"}, {"at": 0.4, "show": "option:1"}, {"at": 0.8, "show": "pick"}]}
    got = _engine({"x": {"steps": [{"show": choice}, {"tick": 100}, {"progress": 0.5}, {"progress": 0.9}]}})["x"]["seen"]
    assert got[2]["picked"] == [] and got[3]["picked"] == ["The pool"]


# ---- the room screen's glue -----------------------------------------------------------------

LINE = {"id": "x", "text": "คำแรก คำสอง", "seconds": 10,
        "slide": {"template": "hero", "title": "Hello", "beats": [{"at": 0.5, "show": "title"}]}}
PLAIN = {"id": "y", "text": "คำแรก คำสอง", "seconds": 10}


def test_the_slide_runs_on_the_voice_this_screen_plays():
    got = _glue({"x": {"engine": True, "steps": [
        {"mode": True}, {"show": LINE},
        {"at": 0}, {"at": 3},                        # no voice yet: shown, and not a step of progress
        {"head": 13, "at": 3.1},                     # her voice starts at 3.1
        {"at": 5.6}, {"at": 8.1}, {"at": 13.0},
        {"end": {"id": "x"}}, {"at": 13.2}, {"at": 15.0}]}})["x"]
    log = got["slides"]
    assert log[0] == ["show", "hero", False]
    assert log[1] == ["progress", 0]                                  # only once she is heard
    assert log[2] == ["progress", 0.25] and log[3] == ["progress", 0.5]
    assert ["progress", 0.99] in log
    assert log[-1] == ["settle"] and "clear" not in [e[0] for e in log]
    assert got["slideUp"] is True and got["seen"][-1][1] is None      # the words are down, the slide stays


def test_a_stop_the_call_over_and_leaving_the_ceremony_take_the_slide_down():
    steps = [{"mode": True}, {"show": LINE}, {"head": 11, "at": 1}, {"at": 4}]
    over = steps + [{"end": {"id": "x"}}, {"at": 11.5}, {"at": 13}]
    got = _glue({
        "cut": {"engine": True, "steps": steps + [{"end": {"id": "x", "cut": True}}]},
        "call_over": {"engine": True, "steps": over + [{"end": {"cut": True}}]},       # mirror_end, a socket closed
        "late_end": {"engine": True, "steps": over + [{"end": {"id": "w", "cut": True}}]},
        "left": {"engine": True, "steps": over + [{"mode": False}]},
        "no_voice": {"engine": True, "steps": [{"mode": True}, {"show": LINE}, {"at": 1}, {"at": 14}, {"at": 16.5}]},
        "next_plain": {"engine": True, "steps": over + [{"show": PLAIN}]},
        "next_slide": {"engine": True, "steps": over + [{"show": {**LINE, "id": "z"}}]},
        "picture_in_line": {"engine": True, "steps": steps + [{"picture": True}]},
        "picture_after": {"engine": True, "steps": over + [{"picture": True}]},
        "joined": {"engine": True, "steps": [{"mode": True}, {"show": {**LINE, "late": 4.2}}]},
        "refused": {"engine": "refuses", "steps": [{"mode": True}, {"show": LINE}, {"head": 11, "at": 1}, {"at": 4}]},
        "no_engine": {"steps": over + [{"picture": True}, {"end": {"cut": True}}, {"mode": False}]},
        "plain_only": {"engine": True, "steps": [{"mode": True}, {"show": PLAIN}, {"head": 11, "at": 1}, {"at": 4},
                                                 {"end": {"id": "y"}}, {"at": 11.5}, {"at": 13}]},
    })
    kinds = {k: [e[0] for e in v["slides"]] for k, v in got.items()}
    assert kinds["cut"][-1] == "clear" and "settle" not in kinds["cut"] and got["cut"]["slideUp"] is False
    assert kinds["call_over"][-2:] == ["settle", "clear"] and got["call_over"]["slideUp"] is False
    assert kinds["late_end"][-1] == "settle" and got["late_end"]["slideUp"] is True     # another line's stop
    assert kinds["left"][-1] == "clear"
    assert kinds["no_voice"] == ["show", "clear"]                     # nobody said it: not left on the stage
    assert kinds["next_plain"][-2:] == ["settle", "clear"]            # a line with no slide: the backdrop
    assert kinds["next_slide"][-2:] == ["settle", "show"] and got["next_slide"]["slideUp"] is True
    assert kinds["picture_in_line"].count("clear") == 0               # the slide wins while its line is said
    assert kinds["picture_after"][-1] == "clear"                      # ... and gives way to the next picture
    assert got["joined"]["slides"] == [["show", "hero", True]]
    assert kinds["refused"] == ["show"] and got["refused"]["slideUp"] is False     # the engine could not draw it
    assert got["no_engine"]["slides"] == [] and got["no_engine"]["slideUp"] is False
    assert got["plain_only"]["slides"] == []                          # no slide in the message: nothing asked of the engine


def test_her_voice_stopping_mid_line_with_no_end_takes_the_slide_down_not_to_its_end():
    """Review 18 Nov: 6 s of silence with no end from the server settled the act -- the line
    was not said to its end, and settling puts up its punchline or its "Off" state unheard."""
    start = [{"mode": True}, {"show": LINE}, {"head": 4, "at": 1}, {"at": 3}]
    got = _glue({
        # (frames still coming -- the robot talking to this screen, her voice not: HER silence, the
        # 6 s; with nothing coming at all it is the link's, test_a_gap_in_the_link_keeps_the_slide_...)
        "silent": {"engine": True, "steps": start + [{"link": 4.5, "at": 4.5}, {"link": 8, "at": 8},
                                                     {"link": 11, "at": 11}]},
        "said": {"engine": True, "steps": start + [{"end": {"id": "x"}}, {"at": 4.5}, {"at": 5.0}, {"at": 6.5}]},
    })
    kinds = [e[0] for e in got["silent"]["slides"]]
    assert kinds[-1] == "clear" and "settle" not in kinds
    assert got["silent"]["slideUp"] is False and got["silent"]["seen"][-1][1] is None
    # ...and through those 6 s the beats waited where her voice ran out (3 s of her 10). Review
    # 23 Nov: they ran on to 0.7 and 1.0 -- its punchline on the stage, unheard, before it went down
    assert max(e[1] for e in got["silent"]["slides"] if e[0] == "progress") <= 0.3
    assert [e[0] for e in got["said"]["slides"]][-1] == "settle" and got["said"]["slideUp"] is True


# A recorded line as it reaches the screen: her voice from 1 s, sent 2 s ahead of her
# (RECORDED_LEAD_S), a frame a second until 3 s, when the link goes quiet; her voice queued
# here runs out at 5 s, 4 s into her 10.
GAP = [{"mode": True}, {"link": 0}, {"show": LINE}, {"at": 0},
       {"head": 3, "link": 1, "at": 1}, {"head": 4, "link": 2, "at": 2}, {"head": 5, "link": 3, "at": 3}]


def test_a_gap_in_the_link_keeps_the_slide_and_its_beats_wait_for_her_voice():
    """The open risk of 21 Nov (robot -> screen is Wi-Fi; a recorded line goes out only 2 s
    ahead of her): her voice ran dry here mid-line with nothing arriving, its beats ran on to
    the end, and 6 s later the slide went down under a line still being said. Now it waits
    where her voice ran out (0.4), goes on with the rest of her voice and settles at its end;
    after 15 s of nothing at all (the robot gone) it goes down, never further than she got."""
    got = _glue({
        "gap": {"engine": True, "steps": GAP + [{"at": 4}, {"at": 5.0}, {"at": 7}, {"at": 9}, {"at": 11}, {"at": 12},
                                               {"head": 18.5, "link": 12.5, "at": 12.6}, {"end": {"id": "x"}},
                                               {"at": 15}, {"at": 18.5}, {"at": 20.0}, {"at": 20.5}]},
        "gone": {"engine": True, "steps": GAP + [{"at": 5.0}, {"at": 10}, {"at": 19.9}, {"at": 20.1}]},
        "too_long": {"engine": True, "steps": GAP + [{"at": 5.0}, {"at": 20.1}, {"head": 31, "link": 25, "at": 25.1},
                                                    {"end": {"id": "x"}}, {"at": 26}]},
        # the slide's own animation frames between two ticks: during the hold, and in the moment
        # the rest of her voice has arrived but no tick has moved the line's clock on yet
        "frames": {"engine": True, "frames": True, "steps": GAP + [{"at": 5.0}, {"frame": 6}, {"frame": 9},
                                                                  {"at": 9.1}, {"frame": 11},
                                                                  {"head": 18.5, "link": 12.5}, {"frame": 12.55},
                                                                  {"at": 12.6}, {"frame": 12.7}]},
    })

    def progress(case):
        return [e[1] for e in got[case]["slides"] if e[0] == "progress"]

    kinds = {k: [e[0] for e in v["slides"]] for k, v in got.items()}
    assert progress("gap")[4:10] == [0.4] * 6                         # 5.0 to 12.6: where her voice ran out
    assert 0.4 < progress("gap")[10] < 1 and progress("gap")[-1] == 1     # on with the rest of her voice
    assert "clear" not in kinds["gap"] and kinds["gap"][-1] == "settle" and got["gap"]["slideUp"] is True
    assert kinds["gone"][-1] == "clear" and "settle" not in kinds["gone"] and max(progress("gone")) == 0.4
    assert got["gone"]["slideUp"] is False and got["gone"]["seen"][-2][1] is not None   # up at 19.9
    assert kinds["too_long"][-1] == "clear" and kinds["too_long"].count("show") == 1    # never back
    assert got["too_long"]["slideUp"] is False and max(progress("too_long")) == 0.4
    assert max(progress("frames")[:-1]) == 0.4 and got["frames"]["slideUp"] is True    # never the gap ahead
    assert progress("frames")[-1] == 0.41                             # 0.1 s of the rest: on from 0.4



def test_her_voice_right_behind_the_first_frame_after_a_gap_still_finds_its_line():
    """Review 23 Nov: a recorded line sends each word's text a moment before its audio
    (gemini.py play_recorded), so after an outage the small text frame lands first. That frame
    said "the link is alive" -> the 6 s, counted from her voice running out, had long passed, and
    the tick before her audio cleared the line. Now the 6 s waits 2 s from the first frame seen
    after the gap (and that mark is new for each gap); frames with no voice still come down."""
    hold = [{"at": 5.0}, {"at": 7}, {"at": 9}, {"at": 11}, {"at": 12}]
    got = _glue({
        # text at 12.5, a tick at 12.55, her audio at 12.6
        "text_first": {"engine": True, "steps": GAP + hold + [{"link": 12.5, "at": 12.55},
                                                             {"head": 18.5, "link": 12.6, "at": 12.6},
                                                             {"end": {"id": "x"}}, {"at": 15}, {"at": 18.5},
                                                             {"at": 20.5}]},
        # two gaps in one line, the text first both times: the grace of the first is not the second's
        "two_gaps": {"engine": True, "steps": GAP + hold + [{"link": 12.5, "at": 12.55},
                                                           {"head": 15.5, "link": 12.6, "at": 12.6},
                                                           {"link": 13.5, "at": 14}, {"at": 15.6}, {"at": 18},
                                                           {"at": 20}, {"at": 22}, {"link": 22.5, "at": 22.55},
                                                           {"head": 26.5, "link": 22.6, "at": 22.6},
                                                           {"end": {"id": "x"}}, {"at": 24}, {"at": 26.5},
                                                           {"at": 28.5}]},
        # frames again, and her voice never: down 2 s after the first of them
        "frames_no_voice": {"engine": True, "steps": GAP + [{"at": 5.0}, {"at": 9}, {"link": 12.5, "at": 12.55},
                                                           {"link": 13.5, "at": 14}, {"link": 14.5, "at": 14.5},
                                                           {"at": 14.6}]},
    })
    for case in ("text_first", "two_gaps"):
        kinds = [e[0] for e in got[case]["slides"]]
        assert "clear" not in kinds and kinds[-1] == "settle" and got[case]["slideUp"] is True, (case, kinds)
        assert all(up is not None for _, up in got[case]["seen"][:-1]), case    # the words up to her line's end
        assert got[case]["seen"][-1][1] is None, case
    late = dict((t, up) for t, up in got["frames_no_voice"]["seen"])
    assert late[14.5] is not None and late[14.6] is None
    kinds = [e[0] for e in got["frames_no_voice"]["slides"]]
    assert kinds[-1] == "clear" and "settle" not in kinds


def test_the_slide_never_steps_back_when_her_voice_and_the_end_land_together_in_a_hold():
    """Review 23 Nov: in a hold the place stood at where her voice ran out, but once the line's end
    was in, the length was the true one read with the clock not yet moved on -- 0.4, 0.308, 0.449
    (an act's typed words un-typed and typed again). While held the line keeps the length it had."""
    got = _glue({"x": {"engine": True, "frames": True, "steps": GAP + [
        {"at": 5.0}, {"frame": 6}, {"at": 9}, {"head": 14, "link": 9.02}, {"end": {"id": "x"}},
        {"frame": 9.05}, {"at": 9.1}, {"frame": 9.2}]}})["x"]
    seen = [e[1] for e in got["slides"] if e[0] == "progress"]
    assert all(b >= a for a, b in zip(seen, seen[1:])), seen
    assert seen[-3] == 0.4 and seen[-2] > 0.4                          # held, then on with the true length


def test_the_page_loads_the_engine_only_on_the_room_screen_and_the_shared_handlers_never_name_it():
    source = PAGE.read_text(encoding="utf-8")
    assert source.count("mirrorSlideAssets()") == 2                   # its definition and startMirror
    start = source[source.index("    function startMirror() {"):source.index("      sock.onopen = function () { mirrorRetry = 0; };")]
    assert "mirrorSlideAssets();" in start
    assert "ceremony-show" not in source.replace("'/assets/ceremony-show.css?v=' + CEREMONY_SHOW_V", "").replace(
        "'/assets/ceremony-show.js?v=' + CEREMONY_SHOW_V", "").replace("client/assets/ceremony-show.js", "")
    handler = source[source.index("    function onMessage("):source.index("    // ---- the room screen (mirror): watch the robot's call ----")]
    assert "mirrorSlide" not in handler and "CeremonyShow" not in handler
    hook = source[source.index("    function storyHook("):]
    hook = hook[:hook.index("\n    }\n")]
    assert "mirrorSlide" not in hook and "CeremonyShow" not in hook
    # every use of the engine sits in the subtitle's own block (the mirror socket's code)
    block = source[source.index("    var MIRROR_SUB_CHARS = "):source.index("    // ---- a film over the face, on the room screen only")]
    assert source.count("CeremonyShow") == block.count("CeremonyShow")
    sock = source[source.index("      sock.onmessage = function (m) {"):source.index("      sock.onclose = function () {")]
    assert "if (t === 'tool_result' && said.result && typeof said.result.screen === 'string') mirrorSlidePicture();" in sock


# The engine's cache tag and what it was set for: /assets has no Cache-Control, so the
# notebook's browser may keep an older engine unless ?v= changes (the emma-story rule,
# tests/test_stories.py ENGINE_TAG). Change ceremony-show.js or .css and this fails until
# CEREMONY_SHOW_V in voice-preview.html is bumped and the pin set to the new content.
SHOW_TAG = ("20261125-c", "1060c7dd00e46072")


def test_the_engines_cache_tag_moves_with_the_engine():
    digest = hashlib.sha256()
    for path in (ENGINE, STYLE):                     # line endings as git stores them
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    tags = re.findall(r"var CEREMONY_SHOW_V = '([\w-]+)';", PAGE.read_text(encoding="utf-8"))
    assert tags == [SHOW_TAG[0]], tags
    assert digest.hexdigest()[:16] == SHOW_TAG[1], (
        "ceremony-show.js/.css changed: bump CEREMONY_SHOW_V in voice-preview.html and set "
        "SHOW_TAG = (<new tag>, %r)" % digest.hexdigest()[:16])


def test_the_event_screens_words_are_kantalad_in_the_ceremony_only():
    """Owner 22 Nov, the review PDF's page 1: "ใช้ font นี้", its arrow ending on the Thai subtitle --
    "เปลี่ยนทั้งหมดเลยนะ". The subtitle box and the no-slide backdrop's name and scene take the film's
    face under html.mirror.mirror-show; nothing else on the page names it (the robot's own face keeps
    its type), and the phrase runs that keep a Thai phrase on one line are untouched."""
    source = PAGE.read_text(encoding="utf-8")
    style = source[:source.index("</style>")]
    assert source.count("Kantalad") == style.count("Kantalad")              # (not in any script)
    style = re.sub(r"/\*.*?\*/", "", style, flags=re.S)
    rules = [m.group(0) for m in re.finditer(r"[^{}]*\{[^{}]*Kantalad[^{}]*\}", style)]
    assert len(rules) == 1 and style.count("Kantalad") == 1, rules
    selectors = [s.strip() for s in rules[0][:rules[0].index("{")].split(",")]
    assert sorted(selectors) == sorted(["html.mirror.mirror-show .mirror-sub-text", "html.mirror.mirror-show .mirror-back-name",
                                        "html.mirror.mirror-show .mirror-back-scene"]), selectors
    assert 'font-family: "Kantalad", "Inter",' in rules[0]
    assert ".mirror-sub-p { white-space: nowrap; }" in style


def test_the_slides_only_exist_in_the_ceremonys_show_mode_and_move_by_transform_and_opacity():
    css = STYLE.read_text(encoding="utf-8")
    plain = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    assert ".cs-stage { display: none; }" in plain
    assert "html.mirror.mirror-show .cs-stage {" in plain
    stage = plain[plain.index("html.mirror.mirror-show .cs-stage {"):]
    stage = stage[:stage.index("}")]
    # Owner 18 Nov: "อยากให้มันเต็มจอเลยไม่ต้องแบ่งพท้นที่ให้ซับ แต่ให้ซับมีกล่องข้อความใสๆ ... แต่ก็อย่า
    # ให้อะไรมาทับหรือซ้อนตรงนี้" -- the stage is the whole screen (it used to end at the subtitle
    # band, `bottom: var(--mirror-foot ...)`); what must stay out of the subtitle box's zone is
    # held above the content's floor, 43.8cu: the fixed templates' box and the map credit's foot
    # (the acts' own words: tests/test_ceremony_acts.py, in a real page)
    assert " bottom: 0;" in stage and "--mirror-foot" not in plain and "overflow: hidden" in stage
    assert "--cs-floor: calc(43.8 * var(--cu));" in stage
    assert ".cs-slide:not(.cs-t-act) { bottom: auto; height: var(--cs-floor); }" in plain
    credit = plain[plain.index(".cs-credit {"):]
    credit = credit[:credit.index("}")]
    assert "top: calc(var(--cs-floor) - .7 * var(--cu)); transform: translate3d(0, -100%, 0);" in credit and "bottom" not in credit
    assert "--cu: min(1vw, 1.7778vh)" in stage                                       # 16:9, held by the height when wider
    # ... and that definition is the only raw vw/vh in the file
    rest = plain.replace("--cu: min(1vw, 1.7778vh)", "")
    assert not re.search(r"\d(?:vw|vh|vmin|vmax)\b", rest)
    # the background moves only while a slide is up; no permanent layer hints
    assert ".cs-stage:not(.cs-up):not(.cs-idle-on) .cs-bg > span { animation-play-state: paused; }" in plain
    assert "will-change" not in plain
    bg = plain[plain.index(".cs-bg, .cs-bg > span"):plain.index(".cs-slide {")]
    assert bg.count("infinite") == 2                                                  # the beam and the arc
    # (owner 20 Nov, review PDF p.1, arrows on the four dim panels in the stage's sky: "ไม่ชอบ ดูแปลก
    # ๆเป็นกรอบๆ" -- no ghost panel is drawn, by the stylesheet or by the engine)
    assert "cs-ghost" not in plain and "cs-ghost" not in ENGINE.read_text(encoding="utf-8")
    # the project's motion rules
    assert "box-shadow" not in plain and "backdrop-filter" not in plain and "filter:" not in plain
    for body in re.findall(r"@keyframes [\w-]+ \{(.*?\})\s*\}", plain, flags=re.S):
        for prop in re.findall(r"([a-z-]+)\s*:", re.sub(r"[\d.]+%|from|to", "", body)):
            assert prop in ("transform", "opacity"), prop
    for value in re.findall(r"transition:([^;]+);", plain):
        for prop in re.findall(r"(?:^|,)\s*([a-z-]+)", value):
            assert prop in ("transform", "opacity", "visibility", "none"), prop
    # sized by the stage, not in pixels (hairline borders and the stars' dots apart)
    for size in re.findall(r"(\d*\.?\d+)px", plain):
        assert float(size) <= 2.2, size
    assert not re.search(r"\d(?:rem|pt)\b", plain)
    # the engine draws no picture: no <img>, no url(), no address of an asset
    js = ENGINE.read_text(encoding="utf-8")
    # the one kind of picture the engine draws: the stories catalogue's, by its hash, on this listener
    assert "var ASSET = /^\\/story-asset\\/[0-9a-f]{64}$/;" in js
    assert "if (!a || typeof a.src !== 'string' || !ASSET.test(a.src)) return box;" in js
    # (two places make a picture: a template's and an act's factory -- both from the server's
    # own two kinds of address and nothing else, tests/test_ceremony_acts.py)
    assert js.count("createElement('img')") == 2 and "<img" not in js and "url(" not in js
    # ... and the only addresses in the stylesheet are the film's own faces, beside the acts on this
    # listener (owner 18 Nov: the film's type is Inter -- the variable font, in the repo; owner 22 Nov:
    # Jipatype's Kantalad in front of it, "เปลี่ยนทั้งหมดเลยนะ" -- four static faces, licensed for the
    # event only: gitignored, shipped by name, scripts/robot_server.py)
    faces = re.findall(r"@font-face \{(.*?)\}", plain, flags=re.S)
    assert len(faces) == 5 and plain.count("url(") == 5
    assert 'font-family: "Inter";' in faces[0] and 'src: url("ceremony-show/fonts/Inter.ttf") format("truetype");' in faces[0]
    assert "font-display: block" in faces[0] and "font-weight: 100 900" in faces[0]
    font = ROOT / "client" / "assets" / "ceremony-show" / "fonts" / "Inter.ttf"
    assert hashlib.sha256(font.read_bytes()).hexdigest() == "29160a80ff49ddcab2c97711247e08b1fab27a484a329ce8b813d820dc559031"
    assert b"fvar" in font.read_bytes()[:400]                                     # the variable font: any weight 100-900
    # each Kantalad face covers one stretch of weights, the nearest stroke (the comment above them)
    kantalad = {"Regular": "100 349", "Medium": "350 499", "SemiBold": "500 649", "Bold": "650 1000"}
    for face, (name, weights) in zip(faces[1:], kantalad.items()):
        assert 'font-family: "Kantalad";' in face and "font-display: block" in face and "font-weight: %s;" % weights in face, face
        assert 'src: url("ceremony-show/fonts/Kantalad-%s.otf") format("opentype");' % name in face, face
    # the four files this PC holds are the ones the owner's Jipatype app gave (22 Nov)
    pins = {"Regular": "1ade1c5c8853dadcbea0dee2ecbd31bb83fc345cc059c28fbbf136c8606da3db",
            "Medium": "a8a19004df662dc14b0bffafe5befb97b012f350d2246d64b566b65e97552b0c",
            "SemiBold": "72c43a0d0c45e20b07112027b9ca334b5729e3a781c7045fe958954b21bbca5f",
            "Bold": "61dc5e24f55a57587c55c8ebe656479511bf86ecd78ed6639b068f28da7a99b6"}
    for name, sha in pins.items():
        file = ROOT / "client" / "assets" / "ceremony-show" / "fonts" / ("Kantalad-%s.otf" % name)
        if file.is_file():
            assert hashlib.sha256(file.read_bytes()).hexdigest() == sha, file.name
    assert '--cs-font: "Kantalad", "Inter", "Segoe UI", system-ui' in plain and "font-family: var(--cs-font);" in plain
    # the engine asks for every face when the screen opens (no word drawn in a fallback and swapped)
    assert """var FACES = ['300 1em "Kantalad"', '470 1em "Kantalad"', '600 1em "Kantalad"', '700 1em "Kantalad"', '600 1em "Inter"'];""" in js
    # no clock for WHEN a part appears: the frame loop is an act's polish (ctx.frame), and the
    # templates' reveal() is called from progress() and settle() alone
    assert "setInterval" not in js and "Date.now" not in js and js.count("requestAnimationFrame") == 2
    progress = js[js.index("  function progress(f) {"):js.index("  function settle() {")]
    assert "reveal(run, run.beats[i].show)" in progress and "later(" not in progress


def _rule(plain: str, selector: str) -> str:
    """The body of the rule whose selector list is exactly `selector`."""
    at = plain.index("\n" + selector + " {") + 1
    return plain[at:plain.index("}", at)]


def test_what_moves_on_the_stage_carries_no_mask_blend_or_clip_of_its_own():
    """23 Nov, the event notebook (integrated GPU): held stages at 40-47 fps, the held logo at 40. A
    mask, a clip-path or a blend on a layer that MOVES is an offscreen pass every frame (seven of
    them in the living stage, ~4 screens of extra fill a frame), and a mask on a layer that SCALES
    is drawn again at every new scale (the held logo's breathing: ~470 tiles a second, measured
    with Chrome's trace). The looks are on still children; the movers move only."""
    css = STYLE.read_text(encoding="utf-8")
    plain = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    js = ENGINE.read_text(encoding="utf-8")
    looks = ("mask", "clip-path", "mix-blend-mode", "background")
    for mover, child in ((".cs-beam", ".cs-beam > i"), (".cs-search", ".cs-search > i"), (".cs-arc-run", ".cs-arc-run > i")):
        moves, still = _rule(plain, mover), _rule(plain, child)
        assert not any(k in moves for k in looks), (mover, moves)
        assert "animation" not in still and "transform" not in still and ("mask-image" in still or "clip-path" in still), child
    assert "animation: cs-beam " in _rule(plain, ".cs-beam") and "animation: cs-arc-run " in _rule(plain, ".cs-arc-run")
    assert "add(h('span', 'cs-beam'), h('i'))" in js and "add(h('i', 'cs-arc-run'), h('i'))" in js
    assert js.count("add(h('span', 'cs-search cs-search-") == 2
    # the dust: both depths under ONE mask
    assert "add(h('span', 'cs-motes'), h('i', 'cs-motes-far'), h('i', 'cs-motes-near'))" in js and "cs-motes cs-motes-" not in js
    assert "mask-image" in _rule(plain, ".cs-motes") and "animation" not in _rule(plain, ".cs-motes")
    # the logo clip: its blend and its feathered edges on a wrapper that never moves, its moves on the
    # video -- and the wrapper is out of the drawing while the clip is not shown (its blend made the
    # whole stage an offscreen pass under every line), back the moment the clip is
    wrap, video = _rule(plain, ".cs-idle-m"), _rule(plain, ".cs-idle video")
    assert "mix-blend-mode: screen" in wrap and "mask-image" in wrap and "animation" not in wrap and "transform" not in wrap
    assert "visibility: hidden; transition: visibility 0s linear .35s;" in wrap
    assert not any(k in video for k in ("mask", "mix-blend-mode")) and "transform: scale(1.06)" in video
    assert "idleEl.appendChild(add(h('span', 'cs-idle-m'), v));" in js
    shown = (".cs-stage.cs-idle-on .cs-idle-m, .cs-stage.cs-dbt-on .cs-idle-m, .cs-stage.cs-dbt-hit .cs-idle-m,\n"
             ".cs-stage.cs-dbt-settle .cs-idle-m, .cs-stage.cs-dbt-fade .cs-idle-m { visibility: visible; transition: none; }")
    assert shown in plain
    # every class under which the clip's own opacity can be above 0 is one the wrapper is shown under
    for rule in re.findall(r"([^{}]*\.cs-idle video[^{}]*)\{([^}]*)\}", plain):
        if re.search(r"opacity:\s*1", rule[1]):
            assert any(c in rule[0] for c in ("cs-idle-on", "cs-dbt-hit")), rule[0]
    # ... and the page's own backdrop under the stage is not drawn while the stage covers it
    assert ("html.mirror.mirror-show body:has(> .cs-stage.cs-up, > .cs-stage.cs-idle-on, > .cs-stage.cs-dbt-on)::before,"
            in plain and "visibility: hidden; transition: visibility 0s linear .5s;" in plain)


def test_the_preview_plays_the_days_lines_in_order_and_is_the_only_door_for_drafts(real, monkeypatch):
    # (a cue's number sources are not served under conftest: the lines' own checks are
    # event_script's tests, not this one's)
    monkeypatch.setattr(event_script, "_problems_in", lambda c, lang: [])
    preview = _script("preview_ceremony_show")
    ids = [c["id"] for c in preview.lines()]
    in_order = [st["cue"] for sc in event_script.order() for st in sc["steps"] if st["do"] == "cue"]
    # owner 23 Nov ("ฉากสุดท้ายเอาออกให้มันจบตรงขอบคุณพอ"): a whole run ends at the thank-you, as the
    # day does -- the backup quotation line is outside the order and no longer played after it
    assert ids == [c for c in in_order if c != "s05_l07"] and ids[-1] == "s10_l05"
    assert [c["id"] for c in preview.lines(only=["s04_l11", "s03_l03"])] == ["s04_l11", "s03_l03"]
    assert [c["id"] for c in preview.lines(start="s09_l02")] == ["s09_l02", "s10_l05"]
    assert [c["id"] for c in preview.lines(only=["s05_backup_quote"])] == ["s05_backup_quote"]   # still playable
    assert [c["id"] for c in preview.lines(outside=True)] == ids + ["s05_backup_quote"]   # the recording scripts' table
    with pytest.raises(SystemExit):
        preview.lines(only=["s05_l07"])                              # no words: nothing to play
    source = (ROOT / "scripts" / "preview_ceremony_show.py").read_text(encoding="utf-8")
    assert 'host="127.0.0.1"' in source and "0.0.0.0" not in source
    assert "approved_by" not in source and "write_text" in source and "report.json" in source


def test_a_status_slides_device_is_a_claim_too():
    lights = {"template": "status", "state": "done", "device": "lights_on", "title": "Embassy World"}
    assert "icon_not_in_line:lights_on" in cs.problems(lights, CUE)             # the beach line has no lights
    cue = {"id": "y", "en": "The lobby lights are on.", "th": "เปิดไฟล็อบบี้เรียบร้อยแล้วค่ะ"}
    assert cs.problems({**lights, "title": "The lobby lights are on"}, cue) == []
    assert "icon_not_in_line:curtain" in cs.problems({**lights, "device": "curtain", "title": "The lights"}, cue)


def test_the_preview_sends_a_line_as_the_cue_code_does(real, monkeypatch):
    """The subtitle with its slide in one message (the draft file's, through the same checks),
    her voice, then the line's end -- and a slide that breaks a rule is not shown there either."""
    preview = _script("preview_ceremony_show")
    cue = event_script.cue("s03_l03")
    queue = _screen()
    face_mirror.call_started()
    _got(queue)
    seconds = asyncio.run(preview.play(cue, b"\x00\x00" * 2400))
    assert seconds == pytest.approx(0.1)
    sent = _got(queue)
    assert [m["type"] for m in sent] == ["mirror_subtitle", "turn_complete", "mirror_subtitle_end"]
    assert sent[0] == {"type": "mirror_subtitle", "id": "s03_l03", "text": cue["th"], "scene": cue["scene_th"],
                       "seconds": 0.1, "slide": cs.for_cue("s03_l03", drafts=True)}
    assert sent[2] == {"type": "mirror_subtitle_end", "id": "s03_l03", "cut": False}
    monkeypatch.setattr(cs, "problems", lambda slide, cue: ["word_not_in_line:x"])
    cs.reset()
    asyncio.run(preview.play(cue, b"\x00\x00" * 2400))
    assert "slide" not in _got(queue)[0]


# ---- review 17 Nov: the red stop in a line's tail ---------------------------------------------

def test_a_stop_always_tells_the_screen_and_only_in_a_ceremony_or_with_a_line_up():
    queue = _screen()
    face_mirror.call_started()
    face_mirror.publish_json({"type": "ready", "output_rate": 24000, "profile": "condo"})
    _got(queue)
    face_mirror.subtitle_cut()
    assert _got(queue) == []                                         # any other call: not one message more
    face_mirror.subtitle({"id": "a", "text": "คำ"})
    _got(queue)
    face_mirror.subtitle_cut()
    assert _got(queue) == [{"type": "mirror_subtitle_end", "cut": True}] and face_mirror._state["subtitle"] is None
    # the ceremony: the line is "over" at the server (nothing held) and still playing on the screen
    face_mirror.publish_json({"type": "ready", "output_rate": 24000, "profile": "ceremony"})
    face_mirror.subtitle({"id": "b", "text": "คำ"})
    face_mirror.subtitle_end("b")
    _got(queue)
    face_mirror.subtitle_cut()
    assert _got(queue) == [{"type": "mirror_subtitle_end", "cut": True}]             # no id: whatever is up


def test_the_red_stop_reaches_the_screen_even_when_it_finds_nothing_to_stop(monkeypatch):
    monkeypatch.setattr(event_cues.turnlog, "record", lambda *a, **k: None)
    queue = _screen()
    face_mirror.call_started()
    face_mirror.publish_json({"type": "ready", "output_rate": 24000, "profile": "ceremony"})
    _got(queue)
    assert asyncio.run(event_cues.cut("staff")) is False             # no press, no tail, no call of ours
    assert _got(queue) == [{"type": "mirror_subtitle_end", "cut": True}]

    def boom():
        raise RuntimeError("mirror")

    monkeypatch.setattr(face_mirror, "subtitle_cut", boom)           # the screen never costs the stop
    assert asyncio.run(event_cues.cut("staff")) is False


def test_a_stop_in_the_lines_tail_takes_the_slide_down_and_never_to_its_end_state():
    """A line the model reads is over at the server at turn_complete with most of its voice still
    queued here: the stop then arrives as the face's `interrupted` (and the server's cut with no
    id). Before: playHead fell back to now, the line's length collapsed, progress(1), settle."""
    heard = [{"mode": True}, {"show": LINE}, {"head": 11, "at": 1}, {"at": 4.2}]
    got = _glue({
        # still being said
        "mid": {"engine": True, "steps": heard + [{"interrupt": True}, {"at": 4.3}, {"at": 6.5}]},
        # over at the server, a third of it played here
        "tail": {"engine": True, "steps": heard + [{"end": {"id": "x"}}, {"at": 4.3}, {"interrupt": True},
                                                   {"at": 4.4}, {"at": 6.5}, {"at": 9}]},
        # the server's own cut, no id, in the same tail -- without the interrupted
        "tail_cut": {"engine": True, "steps": heard + [{"end": {"id": "x"}}, {"at": 4.3}, {"end": {"cut": True}},
                                                       {"head": 4.3, "at": 4.4}, {"at": 6.5}]},
        # a recorded line goes out right behind an `interrupted` of what she said before: not its own
        "before_voice": {"engine": True, "steps": [{"mode": True}, {"show": LINE}, {"at": 1}, {"interrupt": True},
                                                   {"head": 12, "at": 2}, {"at": 7}]},
        "fresh": {"engine": True, "steps": [{"mode": True}, {"show": LINE}, {"head": 5, "at": 0.2}, {"interrupt": True},
                                            {"head": 12, "at": 0.3}, {"at": 0.4}]},
    })
    for case in ("mid", "tail", "tail_cut"):
        log = got[case]["slides"]
        assert log[-1] == ["clear"], (case, log)
        assert ["settle"] not in log and ["progress", 1] not in log, (case, log)
        assert max(e[1] for e in log if e[0] == "progress") < 0.4, (case, log)
        assert got[case]["slideUp"] is False and got[case]["seen"][-1][1] is None      # the words went with it
    assert [e[0] for e in got["before_voice"]["slides"]].count("clear") == 0
    assert got["before_voice"]["slides"][-1] == ["progress", 0.5] and got["before_voice"]["slideUp"] is True
    assert "clear" not in [e[0] for e in got["fresh"]["slides"]] and got["fresh"]["slideUp"] is True
    source = PAGE.read_text(encoding="utf-8")
    sock = source[source.index("      sock.onmessage = function (m) {"):source.index("      sock.onclose = function () {")]
    assert "if (t === 'interrupted') mirrorSubInterrupted();" in sock
    assert sock.index("mirrorSubInterrupted();") < sock.index("onMessage(m);")       # before stopPlayback moves playHead


def test_a_screen_that_joined_mid_line_picks_the_beats_up_where_her_voice_is():
    late = {**LINE, "late": 4}                           # 4 s of a 10 s line already said
    got = _glue({
        "x": {"engine": True, "steps": [{"mode": True}, {"show": late}, {"at": 1},
                                        {"head": 7, "at": 1.1}, {"at": 3.1},
                                        {"end": {"id": "x"}}, {"at": 4.1}, {"at": 7.0}]},
        "fresh": {"engine": True, "steps": [{"mode": True}, {"show": LINE}, {"head": 11, "at": 1.1}, {"at": 3.1}]},
    })
    log = got["x"]["slides"]
    assert log[0] == ["show", "hero", True]
    assert log[1] == ["progress", 0.4]                   # her first sound here: already 4 s in
    assert log[2] == ["progress", 0.6]
    assert log[3] == ["progress", 0.707]                 # over at the server: (3 + 4) / (5.9 + 4)
    assert log[4] == ["progress", 1]
    assert got["fresh"]["slides"][1:] == [["progress", 0], ["progress", 0.2]]


# ---- review 17 Nov: what the checks let through ------------------------------------------------

DEEP = {"id": "d", "en": "The project's main swimming pool is 1.3 metres deep. The building is 8 storeys high, the roof "
                         "level is about 23 metres above road level, and the sales team estimates the indoor facilities "
                         "at around 2,500 square metres in total. Embassy World has 1,066 units, with 20% more to see.",
        "th": "สระลึก 1.3 เมตร อาคารสูง 8 ชั้น สูงประมาณ 23 เมตร ทีมขายประมาณ 2,500 ตารางเมตร 1,066 ยูนิต 20%"}
TILES = {"template": "stats", "title": "The building", "stats": [
    {"value": "1.3", "unit": "metres deep", "label": "Main swimming pool"},
    {"value": "8", "unit": "storeys high", "label": "The building"},
    {"lead": "about", "value": "23", "unit": "metres above road level", "label": "Roof level"},
    {"lead": "around", "value": "2,500", "unit": "square metres in total", "label": "Indoor facilities · sales team estimate"}]}


def _tiles(index: int, **changes) -> dict:
    slide = copy.deepcopy(TILES)
    for key, value in changes.items():
        if value is None:
            slide["stats"][index].pop(key, None)
        else:
            slide["stats"][index][key] = value
    return slide


def test_the_figures_tiles_pass_as_the_line_says_them():
    assert cs.problems(TILES, DEEP) == []


@pytest.mark.parametrize("slide, problem", [
    # a roof is a height here and nothing else
    ({**TILES, "title": "Swimming pool at the roof level"}, "banned:roof"),
    ({**TILES, "title": "Roof level facilities"}, "banned:roof"),
    ({**TILES, "title": "The roof"}, "banned:roof"),
    (_tiles(2, label="Roof level pool"), "banned:roof"),
    # marks that change what a figure says, and letters that are not English
    (_tiles(3, unit="+ square metres in total"), "symbol_on_slide"),
    ({**TILES, "title": "20% more"}, "symbol_on_slide"),
    ({**TILES, "title": "The building €"}, "symbol_on_slide"),
    ({**TILES, "title": "The building (high)"}, "symbol_on_slide"),
    ({**TILES, "title": "The building 大楼"}, "symbol_on_slide"),
    ({**TILES, "title": "Thé building"}, "symbol_on_slide"),
    ({**TILES, "title": "The building\nhigh"}, "symbol_on_slide"),
    # a number as the line writes it, to the comma
    ({"template": "number", "value": "10,66", "unit": "units"}, "number_not_in_line:10,66"),
    ({"template": "number", "value": "1066", "unit": "units"}, "number_not_in_line:1066"),
    ({"template": "number", "value": "2500", "unit": "square metres"}, "number_not_in_line:2500"),
    ({**TILES, "title": "The building is 8,0 storeys"}, "number_not_in_line:8,0"),
    # a figure with another figure's unit or lead
    (_tiles(1, unit="metres deep"), "number_not_with_its_words:8"),
    (_tiles(2, unit="storeys high"), "number_not_with_its_words:23"),
    (_tiles(0, unit="square metres"), "number_not_with_its_words:1.3"),
    (_tiles(0, lead="about"), "number_not_with_its_words:1.3"),
    (_tiles(1, lead="around"), "number_not_with_its_words:8"),
    ({**TILES, "title": "The building is 23 storeys high"}, "number_not_with_its_words:23"),
    ({**TILES, "title": "The building 8"}, "number_not_with_its_words:8"),
    ({"template": "hero", "title": "Embassy World", "chips": ["1,066"]}, "number_not_with_its_words:1,066"),
    # the qualifier on the figure's own tile, not somewhere on the slide
    (_tiles(2, lead=None), "number_lost_its_qualifier:23:about"),
    (_tiles(3, lead=None), "number_lost_its_qualifier:2500:around"),
    (_tiles(3, label="Indoor facilities"), "number_lost_its_qualifier:2500:estimate"),
    # a tile's label from another figure's clause
    (_tiles(1, label="Main swimming pool"), "label_not_with_its_number:8:pool"),
    (_tiles(0, label="Indoor facilities"), "label_not_with_its_number:1.3:indoor"),
])
def test_what_the_first_checks_let_through(slide, problem):
    assert problem in cs.problems(slide, DEEP)


def test_the_qualifier_must_sit_with_its_number_on_a_plain_text_too():
    # "about" on the kicker does not qualify a 600 on a chip
    slide = {"template": "hero", "kicker": "about", "title": "Jomtien Beach", "chips": ["600 metres"]}
    bad = cs.problems(slide, CUE)
    assert "number_lost_its_qualifier:600:about" in bad and "number_lost_its_qualifier:600:straight line" in bad
    ok = {"template": "hero", "title": "about 600 metres from Jomtien Beach in a straight line"}
    assert cs.problems(ok, CUE) == []
    # "waterproof" is not a roof
    cue = {"id": "w", "en": "The day I'm completely waterproof.", "th": "วันที่กันน้ำ"}
    assert cs.problems({"template": "joke", "title": "The day I'm completely waterproof"}, cue) == []


def test_no_line_opens_on_an_empty_stage(real):
    """Review 17 Nov: frames with nothing but the small kicker. Every slide has something up
    before her voice -- a headline, an icon, the device -- or its first part comes with her
    first sound (a figure, a punchline, "done": the reveals)."""
    for cid, slide in real["slides"].items():
        if slide["template"] == cs.ACT:
            continue                                   # (an act draws its own opening on the shared stage)
        gated = {b["show"] for b in slide["beats"]}
        opening = [k for k in cs.keys_of(slide) if k not in gated]
        first = min((b["at"] for b in cs.for_cue(cid, drafts=True)["beats"]), default=1.0)
        assert opening or slide["template"] == "status" or first == 0, cid
        if slide["template"] == "status" and slide["state"] == "done":
            assert "title" in gated, cid                             # "done" and its tick: never before she says so


# ---- pictures (owner 17 Nov: "โชว์รูปต่างๆให้คนเห็นภาพไม่ใช่แค่มีแค่โมชั่นหนังสือ") --------------------

POOLS = {"id": "p", "en": "Our main common areas are on the basement level and the ground floor. The swimming pool "
                          "is 155 metres long, and the roof level is about 23 metres above road level.",
         "th": "พื้นที่ส่วนกลางอยู่ชั้นใต้ดินและชั้นล่าง สระยาว 155 เมตร"}
FILM = {"template": "film", "title": "Main common areas",
        "shots": [{"asset": "fac_g_biogenesis"}, {"asset": "fac_b_aqua_cinema", "caption": "Basement level"}],
        "figure": {"value": "155", "unit": "metres long", "label": "The swimming pool"},
        "beats": [{"show": "shot:1", "at_en": "the basement level"}, {"show": "figure", "at_en": "155 metres"}]}


def _film(**changes) -> dict:
    slide = copy.deepcopy(FILM)
    slide.update(changes)
    return slide


def test_a_picture_slide_passes_and_its_pictures_go_out_as_pinned_addresses_only(real):
    from app import stories

    assert cs.problems(FILM, POOLS) == []
    pins = stories.load_catalog()["assets"]
    out = cs.page_payload(FILM, POOLS)
    assert out["shots"][0] == {"asset": {"src": "/story-asset/" + pins["fac_g_biogenesis"]["sha256"],
                                         "w": pins["fac_g_biogenesis"]["w"], "h": pins["fac_g_biogenesis"]["h"],
                                         "alpha": False}}
    assert out["shots"][1]["caption"] == "Basement level"
    sent = json.dumps(out)
    assert "fac_g_biogenesis" not in sent and ".webp" not in sent and "assets/" not in sent      # no id, no file, no path
    assert stories.asset_body(pins["fac_g_biogenesis"]["sha256"]) is not None                 # ... and it is served
    # a dimmed picture behind a text slide
    hero = {"template": "hero", "title": "Main common areas", "backdrop": "fac_b_aqua_cinema"}
    assert cs.problems(hero, POOLS) == []
    assert cs.page_payload(hero, POOLS)["backdrop"]["src"] == "/story-asset/" + pins["fac_b_aqua_cinema"]["sha256"]


@pytest.mark.parametrize("slide, problem", [
    (_film(shots=[{"asset": "no_such_picture"}]), "unknown_asset:no_such_picture"),
    (_film(shots=[{"asset": "exec_ceo"}]), "asset_excluded:exec_ceo"),                       # a person's photo
    (_film(shots=[{"asset": "prj_app"}]), "asset_excluded:prj_app"),                         # baked-in claims
    (_film(shots=[{"asset": "parking_plan"}]), "asset_excluded:parking_plan"),
    (_film(shots=[{"asset": "map_jomtien"}]), "asset_not_in_line:map_jomtien"),              # the line names no beach
    (_film(shots=[{"asset": "sh_curtain"}]), "asset_not_in_line:sh_curtain"),
    (_film(shots=[{"asset": "../stories.json"}]), "unknown_asset:../stories.json"),
    (_film(shots=[{"asset": 5}]), "bad_asset:shots[0].asset"),
    (_film(shots=[{"caption": "Basement level"}]), "missing:shots[0].asset"),
    (_film(shots=[{"asset": "fac_g_biogenesis", "url": "/x.png"}]), "unknown_field:shots.url"),
    (_film(shots=[]), "bad_list:shots"),
    (_film(shots=[{"asset": "fac_g_biogenesis"}] * 7), "bad_list:shots"),
    # a caption and a figure are words and numbers like any other
    (_film(shots=[{"asset": "fac_g_biogenesis", "caption": "Sky Pool"}]), "word_not_in_line:sky"),
    (_film(shots=[{"asset": "fac_g_biogenesis", "caption": "Rooftop pool"}]), "banned:rooftop"),
    (_film(shots=[{"asset": "fac_g_biogenesis", "caption": "Pool at the roof level"}]), "banned:roof"),
    (_film(figure={"value": "155", "unit": "metres above road level"}), "number_not_with_its_words:155"),
    (_film(figure={"value": "23", "unit": "metres above road level"}), "number_lost_its_qualifier:23:about"),
    (_film(figure={"value": "150", "unit": "metres long"}), "number_not_in_line:150"),
    # the first picture is the establishing shot: never on a beat (an empty stage until then)
    (_film(beats=[{"show": "shot:0", "at": 0.2}]), "first_picture_on_a_beat:shot:0"),
    ({"template": "spotlight", "title": "Main common areas", "pictures": [{"asset": "fac_g_biogenesis"}],
      "beats": [{"show": "picture:0", "at": 0}]}, "first_picture_on_a_beat:picture:0"),
    ({"template": "mosaic", "tiles": [{"asset": "fac_g_biogenesis"}, {"asset": "fac_b_ice_bath"}]}, "bad_list:tiles"),
    ({"template": "hero", "title": "Main common areas", "backdrop": "exec_ceo"}, "asset_excluded:exec_ceo"),
])
def test_what_a_picture_slide_may_not_carry(real, slide, problem):
    assert problem in cs.problems(slide, POOLS)


def test_a_picture_is_only_the_catalogues_pinned_bytes_listed_as_usable(real, monkeypatch):
    from app import stories

    assert cs.asset_problem("fac_g_biogenesis") is None
    # not the bytes the catalogue pinned (a file swapped on disk)
    monkeypatch.setattr(stories, "asset_problem", lambda asset_id, entry, base=None: "missing or changed")
    assert "asset_changed:fac_g_biogenesis" in cs.problems(FILM, POOLS)
    monkeypatch.undo()
    monkeypatch.setattr(settings, "ceremony_file", "data/ceremony/floor1_ceremony.json")
    # the catalogue's own approval gone: its pictures are not served, so not shown
    monkeypatch.setattr(stories, "approved", lambda catalog=None: False)
    assert "catalogue_not_approved:fac_g_biogenesis" in cs.problems(FILM, POOLS)
    monkeypatch.undo()
    monkeypatch.setattr(settings, "ceremony_file", "data/ceremony/floor1_ceremony.json")
    # deny by default: a pinned picture nobody listed, one listed without its words
    listed = dict(cs.asset_registry())
    monkeypatch.setattr(cs, "asset_registry", lambda: {k: v for k, v in listed.items() if k != "fac_g_biogenesis"})
    assert "asset_not_listed:fac_g_biogenesis" in cs.problems(FILM, POOLS)
    monkeypatch.setattr(cs, "asset_registry", lambda: {**listed, "fac_g_biogenesis": {"ok": True, "kind": "render"}})
    assert "asset_not_described:fac_g_biogenesis" in cs.problems(FILM, POOLS)
    monkeypatch.setattr(cs, "asset_registry", lambda: {**listed, "fac_g_biogenesis": {**listed["fac_g_biogenesis"], "ok": "yes"}})
    assert "asset_excluded:fac_g_biogenesis" in cs.problems(FILM, POOLS)


def test_the_pictures_list_covers_the_catalogue_and_keeps_out_what_it_must(real):
    from app import stories

    listed = cs.asset_registry()
    pins = stories.load_catalog()["assets"]
    assert set(listed) == set(pins) and len(pins) > 90                 # every pinned picture was looked at
    for asset_id, entry in listed.items():
        assert isinstance(entry.get("shows"), str) and entry["shows"], asset_id
        if entry["ok"]:
            assert entry["kind"] in cs.ASSET_KINDS and entry["about"], asset_id
            assert cs.asset_problem(asset_id) is None, asset_id        # on disk, hashing to its pin
        else:
            assert entry.get("why"), asset_id
        assert not cs.BANNED.search(entry["shows"].replace("NOT a roof", "")), asset_id
    out = {a for a, e in listed.items() if not e["ok"]}
    assert {"exec_ceo", "exec_cfo", "exec_coo", "exec_gm", "prj_app", "parking_plan", "prj_dev_awards"} <= out
    assert not any(stories.BANNED_SOURCE.search(str(pins[a].get("source"))) for a in listed if listed[a]["ok"])


def test_pictures_need_the_stories_switch_and_a_text_slide_does_not(real, monkeypatch):
    with_pictures = [c for c, sl in real["slides"].items() if cs._used_assets(sl, set())]
    without = [c for c, sl in real["slides"].items() if not cs._used_assets(sl, set())]
    assert with_pictures and without
    assert all(cs.for_cue(c, drafts=True) is not None for c in with_pictures + without)
    monkeypatch.setattr(settings, "stories_enabled", False)             # /story-asset answers 404 then
    assert all(cs.for_cue(c, drafts=True) is None for c in with_pictures)
    assert all(cs.for_cue(c, drafts=True) is not None for c in without)


def _figures(slide: dict) -> list[dict]:
    """Every figure a slide shows, whatever its template or act."""
    if slide.get("template") == cs.ACT:
        return [f for f in (slide.get("figures") or {}).values() if isinstance(f, dict)]
    return [row for row, _tile in cs._figures_of(slide)]


def test_the_lines_with_figures_carry_them_with_their_own_words_whatever_their_template(real):
    """Re-pinned 17 Nov night to the rules, not to a template: the cues were films and
    spotlights, now they are acts -- what must stay is the figures and their words."""
    pool = [f for f in _figures(real["slides"]["s04_l06"]) if f["value"] == "155"]
    assert pool and "metres" in pool[0]["unit"]
    beach = [f for f in _figures(real["slides"]["s03_l03"]) if f["value"] == "600"]
    assert beach and beach[0]["lead"] == "about" and beach[0]["unit"] == "metres"
    assert "straight line" in " ".join(str(beach[0].get(k, "")) for k in ("lead", "unit", "label"))
    deep = {f["value"]: f for f in _figures(real["slides"]["s06_l03"])}
    assert set(deep) == {"1.3", "8", "23", "2,500"}
    assert deep["23"]["lead"] == "about" and deep["2,500"]["lead"] == "around"
    assert "estimate" in deep["2,500"].get("label", "") + deep["2,500"].get("unit", "")
    for cue_id in ("s04_l06", "s03_l03", "s06_l03", "s04_l03", "s04_l11"):
        assert cs.problems(real["slides"][cue_id], event_script.cue(cue_id)) == [], cue_id
    # the places of s04_l06 each get time on the screen as she names them (integration review:
    # "each place gets < 1 s")
    beats = cs.for_cue("s04_l06", drafts=True)["beats"]
    seconds = len(event_script.cue("s04_l06")["en"]) / 14.0
    places = [b["at"] for b in beats if b.get("to") in ("bsmt", "gym", "cow", "gar", "pool")]
    assert len(places) == 5
    assert min(b - a for a, b in zip(places, places[1:])) * seconds >= 0.9, [round((b - a) * seconds, 2) for a, b in zip(places, places[1:])]


# ---- one file per cue -------------------------------------------------------------------------

def test_a_per_cue_file_replaces_its_cues_slide_and_a_bad_one_takes_down_only_its_own(copied, caplog):
    folder = copied.parent / "slides"
    folder.mkdir()
    assert cs.cue_dir(copied) == folder
    (folder / "s09_l02.json").write_text(json.dumps(
        {"template": "hero", "title": "The story of Embassy World", "kicker": "Enjoy"}), encoding="utf-8")
    assert cs.for_cue("s09_l02", drafts=True)["kicker"] == "Enjoy"       # seen without a reset: the files are the key
    assert cs.load_base(copied)["slides"]["s09_l02"]["kicker"] == "Please enjoy"       # the base file is as it was
    (folder / "s04_l03.json").write_text("{ not json", encoding="utf-8")
    (folder / "s04_l15.json").write_text("[1, 2]", encoding="utf-8")
    (folder / "Not A Cue.json").write_text(json.dumps({"template": "hero", "title": "x"}), encoding="utf-8")
    (folder / "s07_l02.json").write_text(json.dumps({"template": "hero", "title": "Free tickets"}), encoding="utf-8")
    with caplog.at_level("WARNING"):
        assert cs.for_cue("s04_l03", drafts=True) is None                # never back to the base file's older slide
        assert cs.for_cue("s04_l15", drafts=True) is None
        assert cs.for_cue("s07_l02", drafts=True) is None                # a readable file that breaks a rule
    assert cs.for_cue("s04_l06", drafts=True)["template"] == "list"      # the others stand (this copy has no per-cue film)
    assert cs.for_cue("s09_l02", drafts=True)["kicker"] == "Enjoy"
    assert "Not A Cue" not in cs.load(copied)["slides"]
    rows = {r["id"]: r["problems"] for r in cs.status(cs.load(copied))}
    assert rows["s04_l03"] == ["unknown_template"] and "word_not_in_line:free" in rows["s07_l02"]


def test_the_signature_covers_the_per_cue_files_and_is_written_to_the_base_file_only(copied):
    approve = _script("approve_ceremony_slides")
    folder = copied.parent / "slides"
    folder.mkdir()
    one = folder / "s09_l02.json"
    one.write_text(json.dumps({"template": "hero", "title": "The story of Embassy World"}), encoding="utf-8")
    before = one.read_bytes()
    sha = approve.approve(copied, "tester", audit=None)
    assert cs.approved(cs.load(copied)) and cs.digest(cs.load(copied)) == sha
    assert sha != cs.digest(cs.load_base(copied))                        # not the base file's slides alone
    assert one.read_bytes() == before                                    # the author's file is untouched
    assert cs.load_base(copied)["slides"]["s09_l02"].get("kicker") == "Please enjoy"   # ... and not folded into the base
    assert cs.for_cue("s09_l02") == {"template": "hero", "title": "The story of Embassy World", "beats": []}
    # a per-cue file edited, added or removed after the signature: a draft again
    one.write_text(json.dumps({"template": "hero", "title": "The story of Embassy World", "kicker": "Enjoy"}),
                   encoding="utf-8")
    assert cs.for_cue("s09_l02") is None and cs.for_cue("s04_l03") is None
    one.write_bytes(before)
    assert cs.for_cue("s04_l03")["value"] == "1,066"
    (folder / "s07_l02.json").write_text(json.dumps({"template": "hero", "title": "Please enjoy the video"}),
                                         encoding="utf-8")
    assert cs.for_cue("s04_l03") is None
    # a broken per-cue slide blocks the signature like any broken slide
    (folder / "s07_l02.json").write_text("{", encoding="utf-8")
    with pytest.raises(SystemExit) as stop:
        approve.approve(copied, "tester", audit=None)
    assert "s07_l02" in str(stop.value)


def test_the_signature_covers_the_pictures_a_slide_names(real, monkeypatch):
    doc = {"slides": {"p": FILM}}
    signed = cs.digest(doc)
    assert signed != cs.digest({"slides": {"p": {**FILM, "shots": FILM["shots"][:1]}}})
    listed = dict(cs.asset_registry())
    # what the list says of a picture it uses changed (un-excluded, re-described): a new thing to look at
    monkeypatch.setattr(cs, "asset_registry", lambda: {**listed, "fac_g_biogenesis": {**listed["fac_g_biogenesis"],
                                                                                    "about": ["anything"]}})
    assert cs.digest(doc) != signed
    monkeypatch.setattr(cs, "asset_registry", lambda: listed)
    assert cs.digest(doc) == signed
    # a picture no slide uses does not touch the signature; a text-only set signs as before pictures existed
    monkeypatch.setattr(cs, "asset_registry", lambda: {**listed, "rm_lobby": {"ok": False}})
    assert cs.digest(doc) == signed
    plain = {"slides": {"x": GOOD}}
    assert cs.digest(plain) == hashlib.sha256(json.dumps(plain["slides"], ensure_ascii=False, sort_keys=True,
                                                         separators=(",", ":")).encode("utf-8")).hexdigest()


# ---- the engine draws them -------------------------------------------------------------------

def _pic(n: int) -> dict:
    return {"src": "/story-asset/" + ("%x" % n) * 64, "w": 1080, "h": 675, "alpha": False}


def test_pictures_are_asked_for_when_the_slide_arrives_and_a_part_stays_clear_until_its_picture_is_ready():
    film = {"template": "film", "title": "Main common areas",
            "shots": [{"asset": _pic(1)}, {"asset": _pic(2), "caption": "Basement level"}, {"asset": _pic(3)}],
            "figure": {"value": "155", "unit": "metres long"},
            "beats": [{"at": 0.3, "show": "shot:1"}, {"at": 0.6, "show": "shot:2"}, {"at": 0.7, "show": "figure"}]}
    got = _engine({"x": {"steps": [
        {"show": film}, {"tick": 100},                  # nothing fetched yet
        {"load": 1}, {"progress": 0.35},                # the first is in; the second's beat comes before its bytes
        {"load": 2}, {"tick": 1000},                    # ... it fades in when ready, and the first goes under
        {"load": True}, {"progress": 0.65}, {"tick": 1000}, {"progress": 1}]}})["x"]["seen"]
    assert got[0]["imgs"] == [_pic(1)["src"], _pic(2)["src"], _pic(3)["src"]]       # all three, at once: preloaded
    assert got[0]["state"]["pictures"] == 3 and got[0]["state"]["loaded"] == 0
    assert "shot:0" in got[1]["shown"] and "shot:0" in got[1]["clear"]              # on its beat and still clear
    assert "shot:0" not in got[2]["clear"] and got[2]["state"]["loaded"] == 1
    assert "shot:1" in got[3]["shown"] and "shot:1" in got[3]["clear"]              # never an empty box
    assert "shot:1" not in got[4]["clear"]
    # the launch-film look: the picture before swings aside and STAYS as a dim panel (depth);
    # only the ones older than that stop and go
    assert got[5]["under"] == [] and got[5]["past"] == ["shot:0"]
    assert "shot:2" not in got[5]["shown"]
    assert "shot:2" in got[7]["shown"] and got[8]["under"] == ["shot:0"] and got[8]["past"] == ["shot:0", "shot:1"]
    assert got[9]["hidden"] == []
    assert "Basement level" in got[9]["text"]


def test_the_engine_draws_no_picture_from_any_other_address():
    for src in ("https://example.com/a.png", "/slides/a.png", "/story-asset/abc", "javascript:alert(1)",
                "/story-asset/" + "a" * 64 + "?x=1", "//evil/story-asset/" + "a" * 64):
        film = {"template": "film", "shots": [{"asset": {"src": src, "w": 1, "h": 1}}], "beats": []}
        got = _engine({"x": {"steps": [{"show": film}, {"tick": 100}]}})["x"]["seen"]
        assert got[1]["imgs"] == [] and got[1]["clear"] == ["shot:0"], src          # no element, and the part stays clear


def test_spotlight_mosaic_and_a_backdrop_are_drawn_with_their_parts():
    spot = {"template": "spotlight", "title": "Jomtien", "side": "right", "sub": "Second Road",
            "pictures": [{"asset": _pic(1)}, {"asset": _pic(2), "caption": "Jomtien Beach", "fit": "contain"}],
            "figure": {"lead": "about", "value": "600", "unit": "metres", "label": "in a straight line"},
            "chips": ["Shopping"], "beats": [{"at": 0.4, "show": "picture:1"}, {"at": 0.5, "show": "figure"},
                                             {"at": 0.8, "show": "chip:0"}]}
    mosaic = {"template": "mosaic", "title": "Common areas", "foot": "More",
              "tiles": [{"asset": _pic(1), "label": "Basement"}, {"asset": _pic(2), "label": "Ground"},
                        {"asset": _pic(3)}],
              "beats": [{"at": 0.2, "show": "tile:0"}, {"at": 0.4, "show": "tile:1"}, {"at": 0.6, "show": "tile:2"}]}
    hero = {"template": "hero", "title": "Hello", "backdrop": _pic(4), "beats": []}
    got = _engine({
        "spot": {"steps": [{"show": spot}, {"load": True}, {"tick": 100}, {"progress": 0.45}, {"progress": 1}]},
        "mosaic": {"steps": [{"show": mosaic}, {"load": True}, {"tick": 100}, {"progress": 0.5}, {"progress": 1}]},
        "hero": {"steps": [{"show": hero}, {"tick": 100}, {"load": True}]},
    })
    spot_seen = got["spot"]["seen"]
    assert spot_seen[2]["shown"] == ["picture:0", "sub", "title"] and "cs-t-spotlight" in spot_seen[2]["kinds"][0]
    assert "picture:1" in spot_seen[3]["shown"] and "figure" not in spot_seen[3]["shown"]
    assert spot_seen[4]["hidden"] == [] and "metres in a straight line" in spot_seen[4]["text"] and "Jomtien Beach" in spot_seen[4]["text"]
    tiles = got["mosaic"]["seen"]
    assert tiles[2]["shown"] == ["foot", "title"] and tiles[3]["shown"] == ["foot", "tile:0", "tile:1", "title"]
    assert tiles[4]["hidden"] == [] and len(tiles[4]["imgs"]) == 3
    back = got["hero"]["seen"]
    assert back[1]["imgs"] == [_pic(4)["src"]] and back[1]["clear"] == ["cs-backdrop"] and back[2]["clear"] == []
    css = STYLE.read_text(encoding="utf-8")
    assert ".cs-needs:not(.cs-loaded) { opacity: 0 !important; }" in css
    assert ".cs-shot.cs-under .cs-img { animation-play-state: paused; }" in css


# ---- the tools around them ---------------------------------------------------------------------

def test_the_preview_can_shoot_a_line_every_n_seconds(real):
    preview = _script("preview_ceremony_show")
    seen = []

    async def mark(name):
        seen.append(name)

    asyncio.run(preview.play(event_script.cue("s04_l15"), b"\x00\x00" * 24000 * 1, on_mark=mark, every=0.5))
    assert seen == ["t00.0", "t00.5", "t01.0", "t02.5"]                # each half second, and once after its end
    seen.clear()
    asyncio.run(preview.play(event_script.cue("s04_l15"), b"\x00\x00" * 2400, on_mark=mark))
    assert seen == ["a", "b", "c"]
    source = (ROOT / "scripts" / "preview_ceremony_show.py").read_text(encoding="utf-8")
    assert '"%s_%s.png" % (cue["id"], name)' in source                 # by the cue: one cue's shots never replace another's
    assert 'glob(c["id"] + "_t*.png")' in source and "rmtree" not in source



def test_a_slow_screenshot_never_starves_the_page_of_her_voice(real, monkeypatch):
    """Review 21 Nov: with a shot every second the preview's slides dropped mid-line -- it sent
    one piece of her voice a pass, and a shot took ~1 s a pass. Every piece that is due goes
    now. Each "shot" here moves the preview's clock 0.9 s without waiting."""
    preview = _script("preview_ceremony_show")
    clock = {"skew": 0.0}
    real_monotonic = preview.time.monotonic
    monkeypatch.setattr(preview, "time", type("T", (), {"monotonic": staticmethod(lambda: real_monotonic() + clock["skew"]),
                                                       "time": staticmethod(preview.time.time)}))
    sent = {"s": 0.0, "began": None}
    behind = []

    def publish(chunk):
        if sent["began"] is None:
            sent["began"] = preview.time.monotonic()
        sent["s"] += len(chunk) / 2 / preview.RATE

    async def shot(name):
        if sent["began"] is not None and sent["s"] < 2.95:         # (her voice not all out yet)
            behind.append(round(preview.time.monotonic() - sent["began"] - sent["s"], 2))
        clock["skew"] += 0.9

    monkeypatch.setattr(face_mirror, "publish_audio", publish)
    asyncio.run(preview.play(event_script.cue("s04_l15"), b"\x00\x00" * 24000 * 3, on_mark=shot, every=0.5))
    assert sent["s"] == pytest.approx(3.0) and behind
    assert max(behind) <= 0, behind                                     # her voice queued ahead at every shot


def test_the_hosts_stand_in_voice_goes_out_as_soon_as_it_is_due(monkeypatch):
    """The same rule in `say_host` (Khun Miki's stand-in voice): after a stalled moment every
    piece that is due goes in that same pass -- seen here as nothing else of the loop running
    before the last of them is out."""
    preview = _script("preview_ceremony_show")
    clock = {"skew": 0.0}
    real_monotonic = preview.time.monotonic
    monkeypatch.setattr(preview, "time", type("T", (), {"monotonic": staticmethod(lambda: real_monotonic() + clock["skew"]),
                                                       "time": staticmethod(preview.time.time)}))
    sent = {"n": 0}
    next_turn = []

    def publish(chunk):
        sent["n"] += 1
        if sent["n"] == 2:                       # the loop stalled 2 s here (a screenshot, a busy machine)
            clock["skew"] += 2.0
            asyncio.get_running_loop().call_soon(lambda: next_turn.append(sent["n"]))

    monkeypatch.setattr(face_mirror, "publish_audio", publish)
    asyncio.run(preview.say_host("สวัสดีครับ", b"\x00\x00" * 24000 * 3))
    assert sent["n"] == 30 and next_turn == [30]                       # all 3 s of it, in that pass


def test_the_asset_catalogue_lists_every_picture_with_its_state(real, tmp_path):
    build = _script("build_ceremony_asset_catalog")
    total, ok = build.write(tmp_path)
    rows = {r["id"]: r for r in build.rows()}
    assert total == len(rows) > 90 and ok == sum(r["state"] == "ok" for r in rows.values()) > 60
    assert rows["exec_ceo"]["state"] == "excluded: asset_excluded" and rows["fac_g_biogenesis"]["state"] == "ok"
    text = (tmp_path / "catalog.md").read_text(encoding="utf-8")
    assert "| `fac_g_biogenesis` | 1080x675 |" in text and "excluded: asset_excluded" in text
    assert len(list(tmp_path.glob("sheet_*.png"))) == (total + 19) // 20


def test_every_example_in_the_authoring_guide_is_a_slide_that_passes(real):
    """docs/emma-ceremony-slides.md is what the page authors copy from: an example that the
    checks refuse would be copied into every scene."""
    guide = (ROOT / "docs" / "emma-ceremony-slides.md").read_text(encoding="utf-8")
    blocks = [json.loads(b) for b in re.findall(r"```json\n(.*?)```", guide, flags=re.S)]
    cues = ["s04_l11", "s04_l06", "s03_l03", "s04_l06", "s09_l02"]
    assert [b["template"] for b in blocks] == ["act", "film", "spotlight", "mosaic", "hero"]
    for slide, cue_id in zip(blocks, cues):
        assert cs.problems(slide, event_script.cue(cue_id)) == [], (slide["template"], cue_id)
    for name in cs.TEMPLATES:
        assert "`%s`" % name in guide, name                             # every template is described


# ---- integration, 17 Nov evening: a figure per shot, the end title, contained pictures ----------

def test_a_shots_own_figure_is_bound_to_the_line_like_any_figure(real):
    deep = {"template": "film", "title": "The building", "end": "Embassy World", "shots": [
        {"asset": "liv_trees"},
        {"asset": "lagoon_islands_aerial", "figure": {"value": "1.3", "unit": "metres deep", "label": "Main swimming pool"}},
        {"asset": "rm_signature", "figure": {"lead": "about", "value": "23", "unit": "metres above road level",
                                             "label": "Roof level"}}],
        "beats": [{"show": "shot:1", "at_en": "main swimming pool"}, {"show": "shot:2", "at_en": "the roof level"},
                  {"show": "end", "at": 0.95}]}
    assert cs.problems(deep, DEEP) == [] and "end" in cs.keys_of(deep)
    out = cs.page_payload(deep, DEEP)
    assert out["end"] == "Embassy World" and out["shots"][2]["figure"]["lead"] == "about"

    def shot(i, **figure):
        slide = copy.deepcopy(deep)
        slide["shots"][i]["figure"] = {k: v for k, v in {**slide["shots"][i]["figure"], **figure}.items() if v is not None}
        return slide

    assert "number_not_with_its_words:1.3" in cs.problems(shot(1, unit="storeys high"), DEEP)
    assert "number_not_in_line:1.5" in cs.problems(shot(1, value="1.5"), DEEP)
    assert "number_lost_its_qualifier:23:about" in cs.problems(shot(2, lead=None), DEEP)
    assert "banned:roof" in cs.problems(shot(2, label="Roof level pool"), DEEP)
    assert "word_not_in_line:terrace" in cs.problems(shot(2, label="Terrace"), DEEP)
    assert "missing:shots[1].figure[0].unit" in cs.problems(shot(1, unit=None), DEEP)
    assert "word_not_in_line:tour" in cs.problems({**deep, "end": "Embassy World tour"}, DEEP)
    assert "beat_shows_nothing:end" in cs.problems({k: v for k, v in deep.items() if k != "end"}, DEEP)
    # (the real line with four numbers: test_the_lines_with_figures_carry_them_with_their_own_words...)


def test_a_shots_figure_and_the_end_title_are_drawn():
    film = {"template": "film", "title": "Ready", "end": "Embassy World",
            "shots": [{"asset": _pic(1)},
                      {"asset": _pic(2), "figure": {"lead": "around", "value": "2,500", "unit": "square metres", "label": "Indoor"}}],
            "chips": ["Talk"], "beats": [{"at": 0.3, "show": "shot:1"}, {"at": 0.9, "show": "end"}]}
    steps = [{"show": film}, {"load": True}, {"tick": 100}, {"progress": 0.35}] + [{"tick": 110}] * 12 + [{"progress": 0.95}]
    got = _engine({"x": {"steps": steps}})["x"]["seen"]
    assert "around" in got[2]["text"] and "square metres" in got[2]["text"] and "Indoor" in got[2]["text"]
    assert got[2]["nums"] == [["2,500", False]] and got[2]["ended"] is False and got[2]["landed"] == []
    assert all(step["nums"] == [["2,500", False]] for step in got)                    # never another value
    assert got[3]["landed"] == ["2,500"] and "end" not in got[-2]["shown"]            # it lands when its picture comes
    assert "end" in got[-1]["shown"] and got[-1]["ended"] is True                      # the side words give way to it
    css = STYLE.read_text(encoding="utf-8")
    assert ".cs-ended .cs-film-words, .cs-ended .cs-scrim, .cs-ended .cs-shot .cs-cap { opacity: 0; }" in css
    assert ".cs-over-figure .cs-unit, .cs-over-figure .cs-lead { white-space: nowrap; }" in css
    fit = css[css.index(".cs-fit .cs-img {"):]
    fit = fit[:fit.index("}")]
    assert "mask-image: linear-gradient(90deg" in fit and "mask-composite: intersect" in fit    # four feathered edges:
    assert "radial-gradient" not in fit                                                         # no rectangle, no porthole


def test_the_plans_with_unit_numbers_areas_or_dimensions_are_out(real):
    listed = cs.asset_registry()
    for asset_id in ("plan_ground_site", "fac_plan_g"):
        assert listed[asset_id]["ok"] is False and listed[asset_id]["why"], asset_id
        assert cs.asset_problem(asset_id) == "asset_excluded"
    assert all("MIRRORED" in listed[a]["text"] for a in ("campus_real", "campus_holo_f2054", "campus_holo_f1814"))
    used = set()
    for slide in real["slides"].values():
        cs._used_assets(slide, used)
    assert not used & {a for a, e in listed.items() if not e["ok"]}


def test_one_shots_figure_gives_way_to_the_next_and_sits_above_the_scrim():
    """Seen 17 Nov: drawn inside its picture, a shot's figure was under the film's scrim and read
    as a ghost; and two figures must never share the place."""
    film = {"template": "film", "title": "Ready", "shots": [
        {"asset": _pic(1)},
        {"asset": _pic(2), "figure": {"value": "8", "unit": "storeys high", "label": "The building"}},
        {"asset": _pic(3), "figure": {"lead": "about", "value": "23", "unit": "metres", "label": "Roof level"}}],
        "beats": [{"at": 0.3, "show": "shot:1"}, {"at": 0.6, "show": "shot:2"}]}
    got = _engine({"x": {"steps": [{"show": film}, {"load": True}, {"tick": 100}, {"progress": 0.4}, {"progress": 0.7}]}})["x"]["seen"]
    assert got[3]["gone"] == [] and "shot:1" in got[3]["shown"]
    assert got[4]["gone"] == ["The building 8 storeys high"] and "shot:2" in got[4]["shown"]
    js = ENGINE.read_text(encoding="utf-8")
    words = js[js.index("TEMPLATES.film = function"):js.index("TEMPLATES.spotlight = function")]
    assert "(run.shotFigures || []).forEach(function (f) { each.appendChild(f); });" in words     # with the words, not in the reel
    css = STYLE.read_text(encoding="utf-8")
    assert ".cs-shot-figure.cs-gone { opacity: 0 !important;" in css
    # under the end title the last panel pulls back (a camera move, not a dimmer)
    assert re.search(r"\.cs-ended \.cs-reel \{ opacity: 1; transform: translate3d\([^;]+\) scale\(\.5\); \}", css)


# ---- picture review, 17 Nov: a picture belongs where its own words are said ---------------------

VIEWS = {"id": "v", "en": "To begin, what kind of view do you like? Would you enjoy a view of the sea, or a ground-floor unit "
                          "facing the pool? Our main common areas are on the basement level and floor 3. A real highlight "
                          "is the swimming pool, a full 155 metres long. There is a Smart Home concept in every unit.",
         "th": "ชอบวิวแบบไหนคะ วิวทะเล หรือห้องชั้นล่างฝั่งสระ ส่วนกลางอยู่ชั้นใต้ดินและชั้น 3 สระยาว 155 เมตร Smart Home ทุกยูนิต"}


def _spot(*pictures, **more) -> dict:
    return {"template": "spotlight", "title": "What kind of view do you like?",
            "pictures": [{"asset": "liv_lagoon_night"}] + [dict(p) for p in pictures], **more}


def test_the_backed_slides_of_this_line_pass(real):
    good = _spot({"asset": "fac_b_aqua_cinema", "caption": "Basement level"}, {"asset": "fac_3_sky_pool", "caption": "Floor 3"},
                 {"asset": "lagoon_islands_aerial", "caption": "The swimming pool"}, {"asset": "sh_aircon", "caption": "Smart Home"},
                 beats=[{"show": "picture:1", "at_en": "on the basement level"}, {"show": "picture:2", "at_en": "floor 3"},
                        {"show": "picture:3", "at_en": "the swimming pool"}, {"show": "picture:4", "at_en": "Smart Home"}])
    assert cs.problems(good, VIEWS) == []
    # an establishing picture opens a cue whose line says nothing of it
    hello = {"id": "h", "en": "Hello, everyone. I am Emma.", "th": "สวัสดีค่ะ"}
    for opener in ("rm_signature", "liv_trees", "campus_holo_f1240"):
        assert cs.problems({"template": "film", "title": "Hello, everyone", "shots": [{"asset": opener}]}, hello) == []
        assert cs.problems({"template": "hero", "title": "Hello, everyone", "backdrop": opener}, hello) == []


@pytest.mark.parametrize("slide, problem", [
    # the reviewer's cases
    (_spot({"asset": "map_jomtien"}, beats=[{"show": "picture:1", "at_en": "a view of the sea"}]),
     "asset_not_near_its_beat:map_jomtien"),                                        # a map is not a sea view
    (_spot({"asset": "mood_living", "caption": "Smart Home"}, beats=[{"show": "picture:1", "at_en": "Smart Home"}]),
     "caption_not_of_its_picture:mood_living"),                                     # a living room is not Smart Home
    (_spot({"asset": "prj_tech_home"}, beats=[{"show": "picture:1", "at_en": "A real highlight"}]),
     "asset_not_near_its_beat:prj_tech_home"),
    (_spot({"asset": "fac_3_sky_pool", "caption": "The 155 metres swimming pool"},
           beats=[{"show": "picture:1", "at_en": "floor 3"}]), "caption_not_of_its_picture:fac_3_sky_pool"),
    (_spot({"asset": "fac_3_sky_pool", "caption": "Ground floor"}, beats=[{"show": "picture:1", "at_en": "floor 3"}]),
     "caption_not_of_its_picture:fac_3_sky_pool"),                                  # "floor 3" is not "ground floor"
    (_spot({"asset": "fac_3_sky_pool"}, beats=[{"show": "picture:1", "at_en": "the swimming pool"}]),
     "asset_not_near_its_beat:fac_3_sky_pool"),                                     # ... nor the 155-metre pool
    # backed somewhere in the line is not backed where it appears
    (_spot({"asset": "fac_b_aqua_cinema"}, beats=[{"show": "picture:1", "at_en": "a view of the sea"}]),
     "asset_not_near_its_beat:fac_b_aqua_cinema"),
    (_spot({"asset": "sh_aircon"}, beats=[{"show": "picture:1", "at_en": "A real highlight"}]),
     "asset_not_near_its_beat:sh_aircon"),
    (_spot({"asset": "lagoon_islands_aerial"}, beats=[{"show": "picture:1", "at": 0.05}]),
     "asset_not_near_its_beat:lagoon_islands_aerial"),
    # an establishing picture opens any cue; on a beat it needs its own words like any other
    (_spot({"asset": "liv_trees"}, beats=[{"show": "picture:1", "at_en": "a view of the sea"}]),
     "asset_not_near_its_beat:liv_trees"),
    (_spot({"asset": "rm_signature", "caption": "A view of the sea"}), "caption_not_of_its_picture:rm_signature"),
    # an opening picture that is neither: the line must say it
    ({"template": "film", "title": "What kind of view do you like?", "shots": [{"asset": "rm_lobby"}]}, "asset_not_in_line:rm_lobby"),
    ({"template": "hero", "title": "What kind of view do you like?", "backdrop": "liv_park"}, "asset_not_in_line:liv_park"),
    ({"template": "mosaic", "title": "What kind of view do you like?",
      "tiles": [{"asset": "fac_b_ice_bath", "label": "Floor 3"}, {"asset": "fac_3_social", "label": "Floor 3"},
                {"asset": "fac_3_coworking", "label": "Floor 3"}]}, "caption_not_of_its_picture:fac_b_ice_bath"),
    # the mirrored roof lettering: out everywhere
    (_spot({"asset": "campus_real"}), "asset_excluded:campus_real"),
    ({"template": "hero", "title": "What kind of view do you like?", "backdrop": "campus_holo_f2054"}, "asset_excluded:campus_holo_f2054"),
])
def test_a_picture_must_be_backed_where_it_appears(real, slide, problem):
    assert problem in cs.problems(slide, VIEWS)


def test_the_pictures_words_are_specific_and_the_openers_are_few(real):
    listed = cs.asset_registry()
    generic = {"project", "view", "living", "floor", "home", "sea", "building", "embassy", "world", "embassy world",
               "atmosphere", "design", "technology", "family life", "plans"}
    for asset_id, entry in listed.items():
        assert not generic & {a.lower() for a in entry["about"]}, asset_id
    openers = {a for a, e in listed.items() if e.get("establishing")}
    # (widened 17 Nov night with the project seen whole -- the buildings, the garden between them;
    # never a picture of the pool: the pool joke's set-up names the project)
    assert openers == {"rm_signature", "liv_trees", "campus_holo_f1060", "campus_holo_f1120", "campus_holo_f1180",
                       "campus_holo_f1240", "liv_garden", "bld_c_holo", "fac_ue_building_a"} and all(listed[a]["ok"] for a in openers)
    # no usable picture shows the mirrored BUILDING lettering
    assert not [a for a, e in listed.items() if e["ok"] and "MIRRORED" in e.get("text", "")]
    # the sentence a beat is in
    line = cs.norm(VIEWS["en"])
    assert cs._sentence_at(line, 0.0) == "To begin, what kind of view do you like?"
    assert cs._sentence_at(line, line.index("floor 3") / len(line)) == "Our main common areas are on the basement level and floor 3."
    assert cs._sentence_at(line, 1.0) == "There is a Smart Home concept in every unit."


def test_the_caption_before_is_out_before_the_next_and_a_drawing_gets_a_lit_panel():
    film = {"template": "film", "shots": [{"asset": _pic(1), "caption": "One"}, {"asset": _pic(2), "caption": "Two"},
                                          {"asset": {**_pic(3), "alpha": True}, "caption": "Plan", "fit": "contain"}],
            "beats": [{"at": 0.3, "show": "shot:1"}, {"at": 0.6, "show": "shot:2"}]}
    got = _engine({"x": {"steps": [{"show": film}, {"load": True}, {"tick": 100}, {"progress": 0.35}, {"progress": 0.65}]}})["x"]["seen"]
    assert got[2]["capoff"] == [] and got[3]["capoff"] == ["shot:0"]              # at once, not after the cross-fade
    assert got[4]["capoff"] == ["shot:0", "shot:1"] and got[4]["drawings"] == 1
    css = STYLE.read_text(encoding="utf-8")
    assert ".cs-shot.cs-capoff .cs-cap { opacity: 0; transition: opacity .15s ease; }" in css
    assert ".cs-drawing { background: linear-gradient(180deg, #2c3270, #232859); }" in css
    assert ".cs-film-words .cs-chip, .cs-backdrop ~ div .cs-chip { background: rgba(5, 6, 26, .86);" in css


def test_the_slide_hears_where_her_voice_is_every_frame_not_only_on_the_subtitles_tick():
    """The film's acts type and move in step with her: seven reports a second (the subtitle's
    150 ms tick) land a beat up to 0.15 s late and move in jerks. Same clock, same sum."""
    steps = [{"mode": True}, {"show": {**LINE, "seconds": 10}}, {"frame": 0.0}, {"head": 10, "frame": 0.5}, {"frame": 3.0},
             {"at": 3.1}, {"frame": 3.2}, {"end": {"cut": True}}, {"frame": 3.3}, {"frame": 3.4}]
    got = _glue({"frames": {"engine": True, "frames": True, "steps": steps}, "hidden": {"engine": True, "steps": steps}})
    seen = got["frames"]
    # no voice yet: nothing is told; her first sound starts the line's count at once
    assert seen["slides"] == [["show", "hero", False], ["progress", 0.0], ["progress", 0.25], ["progress", 0.26],
                              ["progress", 0.27], ["clear"]]
    frames = [row[1] for row in seen["seen"] if str(row[1]).startswith("frames:")]
    assert frames == ["frames:1", "frames:1", "frames:1", "frames:1", "frames:0", "frames:0"]     # the line down: the frames end
    # a hidden tab has no frames: the tick alone, as before
    assert got["hidden"]["slides"] == [["show", "hero", False], ["progress", 0.0], ["clear"]]
    html = PAGE.read_text(encoding="utf-8")
    assert html.count("window.CeremonyShow.progress(mirrorSlideAt(r, now));") == 2                # one sum, both callers
