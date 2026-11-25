"""The ceremony said in English with the Thai as the screen's subtitle (owner 17 Nov 2026:
"Emma เป็นอังกฤษพร้อมซับไทยจะเอาขึ้นจอ เสียง eng").

`"speak": "en"` in the ceremony file (app/event_script.py): the cue's `en` is what she says,
`th` the subtitle the show team puts under their motion presentation
(scripts/export_ceremony_subtitles.py). Absent = Thai, as every file before it.
"""
from __future__ import annotations

import asyncio
import csv
import importlib.util
import io
import json
from pathlib import Path

import pytest

from app import event_cues, event_host, event_order, event_script
from app.config import settings
from tests.test_event_order import _walkable, call, calls, event, rows  # noqa: F401 -- fixtures

ROOT = Path(__file__).resolve().parent.parent
REAL = ROOT / "data" / "ceremony" / "floor1_ceremony.json"


def _cue(**kw) -> dict:
    raw = {"id": "c1", "scene": 1, "th": "สวัสดีค่ะ", "numbers": []}
    raw.update(kw)
    return event_script._normalise(raw)


def _sourced(text: str):
    return lambda name: text


POOL = {"th": "สระว่ายน้ำยาว 155 เมตรค่ะ", "numbers": [{"say": "155 เมตร", "source": "facts", "as": "155 เมตร"}]}


# ---- the number rule, in English ---------------------------------------------------------------

@pytest.mark.parametrize("en", [
    "The pool is 155 metres long.",
    "The 155-metre pool.",
    "The pool is one hundred and fifty-five metres long.",
    "The pool is one hundred fifty-five metres long.",
])
def test_an_english_line_may_say_the_thai_lines_numbers_in_digits_or_in_words(en):
    assert event_script.problems(_cue(en=en, **POOL), lookup=_sourced("สระยาว 155 เมตร"), speak="en") == []


@pytest.mark.parametrize("en, why", [
    ("The pool is 150 metres long.", {"en_number_extra:150", "en_number_missing:155"}),          # a wrong number
    ("The pool is very long.", {"en_number_missing:155"}),                                       # the number left out
    ("The pool is 155 metres long and 2 metres deep.", {"en_number_extra:2"}),                   # one nobody sourced
    ("The pool is 155 metres long, one of five.", {"en_number_extra:1", "en_number_extra:5"}),   # in words too
    ("The pool is five hundred metres long.", {"en_number_extra:500", "en_number_missing:155"}),
])
def test_a_wrong_missing_or_extra_number_in_the_english_is_a_breach(en, why):
    """6 Nov's review found "ห้าร้อยเมตร" through a digits-only rule; the English gets the same
    reading. Before 17 Nov any digit in `en` was simply refused (`en_numbers_unchecked`)."""
    assert set(event_script.problems(_cue(en=en, **POOL), lookup=_sourced("สระยาว 155 เมตร"), speak="en")) == why


def test_english_number_words_and_spelled_ordinals_are_read_as_figures():
    read = lambda s: [v for _a, _b, v in event_script.english_numerals(s)]   # noqa: E731
    assert read("one thousand sixty-six units") == [1066.0]
    assert read("one point three metres deep, eight storeys, twenty-three metres") == [1.3, 8.0, 23.0]
    assert read("two thousand five hundred square metres") == [2500.0]
    assert read("six hundred metres") == [600.0]
    # review 17 Nov: "on the eighth floor" is floor 8 -- an ordinal is a figure unless the cue's
    # `words_ok` says the phrase is not one ("first place", "Second Road")
    assert read("on the eighth floor, the fifteenth floor, the twenty-first") == [8.0, 15.0, 21.0]
    assert read("Jomtien Second Road, first place") == [2.0, 1.0]
    assert read("someone alone, a tone, bone") == []                         # words, never substrings


def test_a_phrase_the_approver_listed_is_not_a_figure_in_either_language():
    c = _cue(th="กันน้ำได้ร้อยเปอร์เซ็นต์ค่ะ", en="One hundred percent waterproof.",
             words_ok=["ร้อยเปอร์เซ็นต์", "one hundred percent"])
    assert event_script.problems(c, speak="en") == []
    assert "en_number_extra:100" in event_script.problems(_cue(th="กันน้ำได้ร้อยเปอร์เซ็นต์ค่ะ",
                                                               en="One hundred percent waterproof.",
                                                               words_ok=["ร้อยเปอร์เซ็นต์"]), speak="en")


@pytest.mark.parametrize("en, why", [
    ("Welcome to the eighth floor.", "en_number_extra:8"),               # a spelled ordinal with no Thai number
    ("Hundreds of units are ready.", "en_quantity_word:hundreds"),       # an amount with no figure and no source
    ("It is twice the size.", "en_quantity_word:twice"),
    ("Half of them face the sea.", "en_quantity_word:half"),
    ("A dozen pools.", "en_quantity_word:dozen"),
    ("Relax on the roof deck.", "overruled_en"),                         # the cut rooftop by another name
    ("Relax on the sky terrace.", "overruled_en"),
])
def test_ordinals_quantity_words_and_the_cut_roof_are_refused_in_a_spoken_english_line(en, why):
    """Review 17 Nov, fail closed: none of these is in today's script. An idiom is the
    approver's to allow per cue (`words_ok`), as "first place" and "Second Road" are."""
    c = _cue(en=en)
    assert why in event_script.problems(c, speak="en")
    assert event_script.problems(_cue(en="I'd take first place!", words_ok=["first place"]), speak="en") == []
    assert event_script.problems(_cue(en="Half of them.", words_ok=["Half of them"]), speak="en") == []


def test_an_english_nobody_says_never_blocks_the_thai_line_and_is_still_reported(tmp_path):
    """Review 17 Nov: the number rule ran on any `en`, so a Thai-spoken file lost its Thai line
    to a translation nobody would hear. Unspoken = not a breach; `status` and the approve
    script's listing still name what is wrong with it. Spoken = a breach."""
    raw = {"id": "c1", "scene": 1, "th": "สวัสดีค่ะ", "en": "Welcome to all eight hundred of you.", "numbers": []}
    c = event_script._normalise(raw)
    raw.update(approved_by="test", approved_at="2026-11-17 10:00", approved_sha256=event_script.digest(c))
    thai = {"format": event_script.FORMAT, "cues": [raw]}
    assert "c1" in event_script.servable(thai) and event_script.status(thai)[0]["problems"] == []
    assert event_script.status(thai)[0]["en_problems"] == ["en_number_extra:800"]
    english = dict(thai, speak="en")
    assert event_script.servable(english) == {} and "en_number_extra:800" in event_script.status(english)[0]["problems"]
    spec = importlib.util.spec_from_file_location("approve_event_script", ROOT / "scripts" / "approve_event_script.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    listing = "\n".join(mod.status_lines(thai))
    assert "ENGLISH (not spoken): en_number_extra:800" in listing and "BREAKS" not in listing
    assert "BREAKS: en_number_extra:800" in "\n".join(mod.status_lines(english))


def test_a_number_she_said_in_english_words_is_a_number_said():
    """event_voice.unspoken_numbers gates a recorded clip and scores a live reading; it knew
    Thai number words only, so an English reading in words was "a number dropped"."""
    from app import event_voice

    line = "The pool is 155 metres long and 1.3 metres deep, on floor 8."
    assert event_voice.unspoken_numbers(
        "The pool is one hundred and fifty-five metres long and one point three metres deep, on the eighth floor.", line) == []
    assert event_voice.unspoken_numbers("The pool is long and 1.3 metres deep, on floor 8.", line) == [155.0]
    assert event_voice.unspoken_numbers("สระยาวร้อยห้าสิบห้าเมตร ลึก 1.3 เมตร ชั้น 8", line) == []      # Thai words as before


def test_both_live_modes_speak_the_language_the_file_is_said_in(tmp_path, monkeypatch):
    """EVENT_SCRIPT_PROMPT and EVENT_SCRIPT_TOOL (both off by default). With an English-spoken
    file the Thai rule ("...เป็นภาษาไทย ห้ามแปล", THAI_WINS) round an English line was an order
    to say it in Thai (review 17 Nov): the block and the tool's result give the English line
    under an English rule that says the line wins; a Thai file gets exactly what it got."""
    from app.tools import ceremony as tool

    target = tmp_path / "floor1_ceremony.json"
    monkeypatch.setattr(settings, "ceremony_file", str(target))
    monkeypatch.setattr(settings, "event_script_prompt", True)
    monkeypatch.setattr(settings, "event_script_tool", False)
    monkeypatch.setattr(event_host, "ceremony_now", lambda: True)
    monkeypatch.setattr(tool, "_door_open", lambda: True)

    async def no_recording(cue_id, *, by="staff", show=None):
        return {"ok": True, "mode": "say_yourself", "line": event_cues._pick(cue_id)[0]["th"]}

    monkeypatch.setattr(event_cues, "play", no_recording)
    thai = lambda s: any("\u0e00" <= ch <= "\u0e7f" for ch in s)       # noqa: E731

    target.write_text(json.dumps(_doc("en"), ensure_ascii=False), encoding="utf-8")
    event_script.reset()
    block = event_host.script_block()
    assert "«Hello, Khun Miki.»" in block and "«สวัสดีค่ะคุณมิกิ»" not in block
    assert event_host.SCRIPT_RULE_EN % {"host": "พิธีกร"} in block and "เป็นภาษาไทย" not in block
    got = asyncio.run(tool.say_script_line("c1"))
    assert got["instruction"] == tool.SAY_EN % "Hello, Khun Miki."
    assert not thai(got["instruction"]) and tool.THAI_WINS not in got["instruction"]
    assert "in English" in got["instruction"] and "do not translate" in got["instruction"]

    target.write_text(json.dumps(_doc(), ensure_ascii=False), encoding="utf-8")
    event_script.reset()
    block = event_host.script_block()
    assert block == "\n".join([event_host.SCRIPT_HEAD, event_host.SCRIPT_RULE % {"host": "พิธีกร"},
                               event_host.SCRIPT_LINE % {"ask": event_host.script_ask(event_script.cue("c1"), "พิธีกร"),
                                                         "th": "สวัสดีค่ะคุณมิกิ"},
                               event_host.SCRIPT_TAIL % {"host": "พิธีกร"}])
    assert asyncio.run(tool.say_script_line("c1"))["instruction"] == tool.SAY % "สวัสดีค่ะคุณมิกิ"
    event_script.reset()


# ---- which language is said --------------------------------------------------------------------

def _doc(speak=None, **cue) -> dict:
    raw = {"id": "c1", "scene": 1, "th": "สวัสดีค่ะคุณมิกิ", "en": "Hello, Khun Miki.", "numbers": []}
    raw.update(cue)
    c = event_script._normalise(raw)
    raw.update(approved_by="test", approved_at="2026-11-17 10:00", approved_sha256=event_script.digest(c))
    doc = {"format": event_script.FORMAT, "cues": [raw]}
    if speak is not None:
        doc["speak"] = speak
    return doc


def test_a_file_that_does_not_say_speaks_thai_exactly_as_before():
    doc = _doc()
    assert event_script.speak(doc) == "th" and event_script.speak({"speak": "fr"}) == "th"
    c = event_script.servable(doc)["c1"]
    assert event_script.spoken(c, doc) is c                                    # the very cue: nothing swapped
    assert event_script.read_text(c) == event_script.READ_RULE % "สวัสดีค่ะคุณมิกิ"
    assert "speak" not in event_script.status(doc)[0] and "en" not in event_script.status(doc)[0]


def test_with_speak_en_the_english_is_the_line_and_the_thai_the_subtitle():
    doc = _doc("en")
    c = event_script.spoken(event_script.servable(doc)["c1"], doc)
    assert (c["th"], c["lang"], c["sub_th"], c["en"]) == ("Hello, Khun Miki.", "en", "สวัสดีค่ะคุณมิกิ", "Hello, Khun Miki.")
    turn = event_script.read_text(c, c["lang"])
    assert turn == event_script.READ_RULE_EN % "Hello, Khun Miki."
    # the turn that asks for English is in English and quotes only the English: a Thai rule
    # ("...เป็นภาษาไทย ห้ามแปล") round an English line is two orders in one turn
    assert not any("฀" <= ch <= "๿" for ch in turn) and "in English" in turn and "do not translate" in turn
    assert event_script.status(doc)[0]["en"] == "Hello, Khun Miki."


def test_a_line_with_no_english_is_never_said_in_an_english_ceremony():
    doc = _doc("en", en=None)
    assert event_script.servable(doc) == {} and event_script.status(doc)[0]["problems"] == ["no_en_line"]
    assert "c1" in event_script.servable(_doc(en=None))                        # the same line in a Thai file: fine


def test_the_approval_covers_the_english_so_adding_or_editing_it_is_a_draft_again():
    """The owner signed 29 Thai lines on 12 Nov; the English added on 17 Nov was never under
    that signature. `digest` always held both texts: no cue is approved until signed again."""
    doc = _doc("en")
    assert "c1" in event_script.servable(doc)
    doc["cues"][0]["en"] = "Hello there, Khun Miki."
    assert event_script.servable(doc) == {}
    thai_only = _doc(en=None)
    thai_only["cues"][0]["en"] = "Hello, Khun Miki."                           # English added after a Thai-only signature
    thai_only["speak"] = "en"
    assert event_script.servable(thai_only) == {}


def test_the_player_reads_and_plays_the_english_when_the_file_says_so(tmp_path, monkeypatch):
    """event_cues picks the spoken cue: the read turn, the recorded clip's language and the
    caption language all follow `speak`; a Thai file picks the cue it always did."""
    target = tmp_path / "floor1_ceremony.json"
    monkeypatch.setattr(settings, "ceremony_file", str(target))
    for speak, line, lang in (("en", "Hello, Khun Miki.", "en"), (None, "สวัสดีค่ะคุณมิกิ", "th")):
        target.write_text(json.dumps(_doc(speak), ensure_ascii=False), encoding="utf-8")
        event_script.reset()
        c, why = event_cues._pick("c1")
        assert why is None and c["th"] == line and (c.get("lang") or "th") == lang
        assert event_cues._lang(c) == lang
        assert event_script.read_text(c, c.get("lang") or "th").endswith("«%s»" % line)
    src = (ROOT / "app" / "event_cues.py").read_text(encoding="utf-8")
    assert 'event_script.read_text(c, c.get("lang") or "th")' in src
    assert 'play_recorded(c["th"], pcm, lang=c.get("lang") or "th", wait_tools=True' in src
    assert "clip_sha(c, _lang(c))" in src and "voice.pcm(c, _lang(c), sha=sha)" in src
    event_script.reset()


# ---- the real file -----------------------------------------------------------------------------

def _real() -> dict:
    return json.loads(REAL.read_text(encoding="utf-8"))


def test_the_real_ceremony_is_spoken_in_english_and_every_line_has_its_english():
    doc = _real()
    assert event_script.speak(doc) == "en"
    cues = event_script.cues(doc)
    lines = [c for c in cues if c["th"]]
    assert len(lines) == 29 and all(c["en"] for c in lines)
    # the one empty cue (the demo room's line waits for the room number) has no English either
    assert [c["id"] for c in cues if not c["th"]] == ["s05_l07"] and not event_script.cue("s05_l07", doc)["en"]
    for c in lines:
        assert event_script.en_number_problems(c) == [], c["id"]
        assert "คุณมิกิ" not in c["en"] and not any("฀" <= ch <= "๿" for ch in c["en"]), c["id"]
        assert ("Khun Miki" in c["en"]) == ("คุณมิกิ" in c["th"]), c["id"]
    # the owner's numbers stay as approved (17 Nov: keep Emma's lines, the LED changes)
    assert "about 600 metres" in event_script.cue("s03_l03", doc)["en"] and "600 เมตร" in event_script.cue("s03_l03", doc)["th"]
    assert "around 2,500 square metres" in event_script.cue("s06_l03", doc)["en"]
    # the owner cut the rooftop: no English name for a usable roof (review 17 Nov, "roof deck")
    assert "roof level" in event_script.cue("s06_l03", doc)["en"]
    assert not any(event_script._EN_OVERRULED.search(c["en"]) for c in lines)
    assert all(event_script.problems(c, lookup=lambda name: None, speak="en") == [] or
               all(p.startswith("number_row_") for p in event_script.problems(c, lookup=lambda name: None, speak="en"))
               for c in lines)


def test_the_subtitle_csv_and_the_paired_script_are_in_step_with_the_ceremony_file():
    """The show team's hand-over is generated (scripts/export_ceremony_subtitles.py): a line
    changed without the CSV or the doc is a subtitle that no longer matches what she says.
    Timing is left out of the comparison -- it is measured from rendered clips, which are not
    in the repo."""
    spec = importlib.util.spec_from_file_location("export_ceremony_subtitles", ROOT / "scripts" / "export_ceremony_subtitles.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = mod.target(REAL)
    assert out.is_file(), "run: python scripts/export_ceremony_subtitles.py"
    text = out.read_text(encoding="utf-8-sig")
    assert mod.words(text) == mod.words(mod.render(_real(), clips=False))
    state = mod.in_step(REAL, _real(), mod.DOC)
    assert all(state.values()), "run: python scripts/export_ceremony_subtitles.py -- %s" % state
    paired = mod.DOC.read_text(encoding="utf-8")
    assert "For the motion-presentation team" in paired and "lines in the running order" in paired
    assert "`s05_l07`" not in paired                                          # the empty line: out of the order (24 Nov)
    got = list(csv.DictReader(io.StringIO(text)))
    pressed = [r for r in got if r["order"]]
    assert [int(r["order"]) for r in pressed] == list(range(1, len(pressed) + 1)) and len(pressed) == 28
    assert [r["cue"] for r in got if not r["order"]] == ["s05_backup_quote"]      # pressed by hand, not in the order
    assert all(r["en"] and r["th"] and float(r["seconds"]) > 0 and r["timing"] in ("clip", "estimate") for r in got)


def test_the_whole_running_order_walks_through_to_the_handover(event, calls, call, monkeypatch):
    """Rehearsal on a desk (owner 17 Nov: "ลองซ้อมเรื่องบทไปก่อน จำลองไปแล้วปรับ"): the real
    order pressed "ถัดไป" from the first step to the last with every path stubbed and the three
    stage spots recorded. Every line the owner will sign is reached exactly once, in file
    order; the one line with no words yet (the demo room's) stops "ถัดไป" with its reason until
    staff skip it; the last step is the hand-over to Emma Event; then the order is at its end."""
    doc = _real()
    for raw in doc["cues"]:                               # signed for this tmp copy only (tests pin approvals)
        c = event_script._normalise(raw)
        if c["th"]:
            raw.update(approved_by="test", approved_at="2026-11-17 10:00", approved_sha256=event_script.digest(c))
    (event.parent / "floor1_ceremony.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    event_script.reset()
    event_order.reset()
    # where a number comes from is checked against the real approved sources elsewhere
    # (tests/test_event_script.py); conftest serves no facts, so here every sourced line
    # would read "source unavailable" -- the English-against-Thai number rule stays on
    monkeypatch.setattr(event_script, "number_problems", lambda c, **kw: [])
    monkeypatch.setattr(settings, "event_walk_enabled", True)
    event_host.spots_path().write_text(json.dumps({"spots": {
        k: {"pose": [-9.0, -3.0, 0.0]} for k in ("stage_centre", "beside_host", "beside_globe")}}), encoding="utf-8")
    event_host.reset()
    assert event_script.order_problems(doc, event_host._normalise(json.loads(event.read_text(encoding="utf-8")), {})) == []
    refused, guard = [], 0
    while guard < 200:
        guard += 1
        got = asyncio.run(event_order.next())
        if got.get("ok"):
            continue
        if got.get("error") == "end":
            break
        refused.append((got.get("n"), got.get("i"), got.get("error"), got.get("error_th")))
        assert asyncio.run(event_order.skip(got["n"], got["i"]))["ok"], got
    steps = [(sc["n"], st) for sc in event_script.order(doc) for st in sc["steps"]]
    said = [c[1] for c in calls if c[0] == "cue"]
    want = [st["cue"] for _n, st in steps if st["do"] == "cue" and st["cue"] != "s05_l07"]
    assert said == want and len(said) == 28
    # 23 Nov (owner: "ช่วงพิธีอยู่แอปพิธีทั้งหมด"): the walks out to the stage and beside Khun Miki are card 1's, one
    # `opening` step now (app/event_tour.py, its own walks -- not the order's walk steps); the globe's is the order's own
    assert [c for c in calls if c[0] == "walk"] == [("walk", "beside_globe")]
    assert calls[-1] == ("handoff", "event")
    # what "ถัดไป" would not run, each named to the staff: the in-box tick (staff's word that she is in the box -- its
    # own button, two taps, never "ถัดไป": third review 23 Nov), card 1's opening on a desk with no room screen (its own
    # two-tap "without the screen" is staff's, on purpose -- never "ถัดไป"), and the line with no words
    # (the demo room's empty line is out of the order since 24 Nov: question 9 goes straight on to the quotation's offer)
    assert [(n, e) for n, _i, e, _w in refused] == [(1, "in_box_tap"), (1, "not_ready")], refused
    assert all(w for _n, _i, _e, w in refused), refused
    snap = asyncio.run(event_order.snapshot())
    spoken_rows = [st for sc in snap["scenes"] for st in sc["steps"] if st.get("line_en")]
    assert len(spoken_rows) == 28 and all(st["line_th"] for st in spoken_rows)   # the page has both texts


def test_the_export_check_goes_red_when_a_line_changes_under_the_csv(tmp_path, capsys):
    """`--check` is what a person runs before handing the CSV over: an English line edited
    after the export (or a subtitle) must say OUT OF STEP, not pass."""
    spec = importlib.util.spec_from_file_location("export_ceremony_subtitles", ROOT / "scripts" / "export_ceremony_subtitles.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    copy = tmp_path / "floor1_ceremony.json"
    doc = _real()
    copy.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    assert mod.main(["--file", str(copy)]) == 0 and mod.main(["--file", str(copy), "--check"]) == 0
    paired = copy.with_name("floor1_ceremony.script-en-th.md")
    assert paired.is_file() and "The lobby lights are off." in paired.read_text(encoding="utf-8")
    next(c for c in doc["cues"] if c["id"] == "s08_l08")["en"] = "The lobby lights are now off."
    copy.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    capsys.readouterr()
    assert mod.main(["--file", str(copy), "--check"]) == 1
    out = capsys.readouterr().out
    assert out.count("OUT OF STEP") == 2                                   # the CSV and the doc, each named
    # the doc alone edited by hand: caught too
    assert mod.main(["--file", str(copy)]) == 0
    paired.write_text(paired.read_text(encoding="utf-8").replace("The lobby lights are now off.", "Lights off."), encoding="utf-8")
    assert mod.main(["--file", str(copy), "--check"]) == 1
