"""Which source wins, what she just said, how big the common area is (owner 24 Nov 2026,
"กฎลำดับแหล่ง + เห็นบทที่เพิ่งพูด" -- app/ceremony.py `SOURCE_ORDER_TH`, `live_extras`, `common_area_th`).

A cross-check (.tmp/ceremony-data-check/README_TH.md) found sources that disagree and a note that carried only a cue's
id. What these hold down:

* the rule is in the ceremony's live prompt and beside its tool results while a window is open -- and in NO other
  profile's prompt (the sales, condo, event, emma, joke, translator prompts are as they were);
* the text of the lines she just said (the last two) reaches her only when the cue is servable (approved, clean) --
  never a draft, never a cut line, never a cue that has aged out;
* the common-area figure is read from the approved cue's own `numbers` row when it runs (change the row, the
  prompt follows; unsigned = no figure, hand over to the sales team);
* nothing of it appears while no window is open (the cue-only ceremony runs none of her tools).
"""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path

import pytest

from app import ceremony, event_cues, event_host, event_script
from app.config import settings
from app.prompts import build_instructions
from tests.test_event_order import REAL_SCRIPT, _signed, event  # noqa: F401 -- fixture

ROOT = Path(__file__).resolve().parents[1]


def _doc(signed: bool) -> dict:
    doc = json.loads(REAL_SCRIPT.read_text(encoding="utf-8"))
    cues = []
    for c in doc["cues"]:
        raw = {k: v for k, v in c.items() if not k.startswith("approved")}
        cues.append(_signed(raw) if signed else dict(raw, approved_by=None, approved_at=None, approved_sha256=None))
    return dict(doc, cues=cues)


def _use(doc: dict) -> None:
    Path(settings.ceremony_file).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    event_script.reset()
    ceremony._SERVED.update(at=0.0, doc=None, map={})


@pytest.fixture
def signed(event, monkeypatch):                             # noqa: F811
    # the number rows' sources (facts, the report, a story) are not loaded in a test: the rows themselves stay
    monkeypatch.setattr(event_script, "number_problems", lambda c, lookup=None: [])
    _use(_doc(True))
    yield
    event_script.reset()


@pytest.fixture
def unsigned(event, monkeypatch):                           # noqa: F811
    monkeypatch.setattr(event_script, "number_problems", lambda c, lookup=None: [])
    _use(_doc(False))
    yield
    event_script.reset()


def _said(cue_id: str, ago: float = 1.0) -> None:
    event_cues.STATE["said_log"].append({"id": cue_id, "at": time.monotonic() - ago})


def _window() -> None:
    assert ceremony.live_open(cue_id=ceremony.ADHOC_ID, brief_th="x")


# ---- the rule: the ceremony's live prompt only ------------------------------------------------------

def test_the_source_order_is_a_rule_without_a_figure_or_a_sentence_to_parrot():
    rule = ceremony.SOURCE_ORDER_TH
    for mark in ("«", "»", '"', "“", "”"):
        assert mark not in rule
    assert not any(ch.isdigit() for ch in rule.replace("บท 23", ""))
    for words in ("สิ่งที่คุณเพิ่งพูดในบทพิธี", "ข้อมูลโครงการที่อนุมัติ", "รายงานบท 23", "รายงานส่วนอื่นและเรื่องเล่า",
                  "ส่งต่อทีมขายยืนยัน"):
        assert words in rule, words
    order = [rule.index(w) for w in ("เพิ่งพูดในบทพิธี", "ข้อมูลโครงการที่อนุมัติ", "รายงานบท 23", "เรื่องเล่า")]
    assert order == sorted(order)


def test_the_rule_and_the_common_area_are_in_the_ceremony_prompt_only(signed):
    text = build_instructions(settings.project_name, languages=settings.reply_languages, robot_name=settings.robot_name,
                              profile="ceremony", robot_face=True)
    assert ceremony.SOURCE_ORDER_TH in text and ceremony.common_area_th() in text
    for profile in ("condo", "event", "emma", "joke", "translator"):
        for face in (False, True):
            other = build_instructions(settings.project_name, languages=settings.reply_languages,
                                       robot_name=settings.robot_name, profile=profile, robot_face=face)
            assert ceremony.SOURCE_ORDER_TH not in other and ceremony.COMMON_AREA_ASK_TH not in other, (profile, face)
    # the event app's block (the Emma Event profile) is not the ceremony's
    assert ceremony.SOURCE_ORDER_TH not in event_host.event_block()
    assert event_host.scene_note() is None or ceremony.SOURCE_ORDER_TH not in event_host.scene_note()


def test_the_sales_profile_prompts_are_byte_for_byte_what_they_were(signed):
    """The existing snapshot tests (tests/test_profiles.py, test_prompt_v2.py) pin the sales prompts; this adds that the
    ceremony's block is the only place the new words exist, by building each profile's prompt twice (rule on / the
    ceremony's helpers patched to nothing) and comparing."""
    build = lambda p: build_instructions(settings.project_name, languages=settings.reply_languages,       # noqa: E731
                                         robot_name=settings.robot_name, profile=p, robot_face=True)
    before = {p: build(p) for p in ("condo", "event", "emma", "joke", "translator")}
    orig_rules, orig_extras = ceremony.prompt_rules, ceremony.live_extras
    ceremony.prompt_rules = lambda: []
    ceremony.live_extras = lambda: None
    try:
        after = {p: build(p) for p in before}
    finally:
        ceremony.prompt_rules, ceremony.live_extras = orig_rules, orig_extras
    assert before == after


# ---- beside her tool results ------------------------------------------------------------------------------

def test_with_a_window_open_the_note_has_the_order_the_common_area_and_what_she_just_said(signed):
    _window()
    _said("s06_l03")
    note = event_host.ceremony_note()
    assert ceremony.SOURCE_ORDER_TH in note and ceremony.common_area_th() in note and event_host.CEREMONY_RULE in note
    cue = event_script.cue("s06_l03")
    assert "«%s»" % " ".join(cue["en"].split()) in note and "ห้ามพูดซ้ำ" in note
    assert "2,500" in note                                  # the number she said is in front of her


def test_no_window_no_extras(signed):
    _said("s06_l03")
    note = event_host.ceremony_note()
    assert note == event_host.CEREMONY_RULE or note.endswith(event_host.CEREMONY_RULE)
    assert ceremony.SOURCE_ORDER_TH not in (note or "") and ceremony.live_extras() is None


def test_only_the_last_two_lines_and_only_while_recent(signed):
    _window()
    _said("s02_l03", 30)
    _said("s03_l03", 20)
    _said("s06_l03", 10)
    note = ceremony.live_extras()
    en = lambda c: " ".join(event_script.cue(c)["en"].split())          # noqa: E731
    assert en("s06_l03") in note and en("s03_l03") in note and en("s02_l03") not in note
    event_cues.STATE["said_log"][-1]["at"] = time.monotonic() - event_cues.SAID_NOTE_S - 5
    note = ceremony.live_extras()
    assert en("s06_l03") not in note


def test_a_line_longer_than_the_cut_is_cut(signed, monkeypatch):
    _window()
    monkeypatch.setattr(ceremony, "SAID_TEXT_CHARS", 30)
    _said("s06_l03")
    assert "…»" in ceremony.recent_cues_th()


def test_a_draft_or_unsigned_cue_text_never_reaches_her(unsigned):
    _window()
    _said("s06_l03")
    extras = ceremony.live_extras()
    cue = event_script.cue("s06_l03")
    assert cue["en"] not in (extras or "") and ceremony.recent_cues_th() is None
    assert ceremony.SOURCE_ORDER_TH in extras                # the rule itself is not data: it stays


def test_a_cue_that_drifted_after_signing_is_not_shown(signed):
    doc = _doc(True)
    for c in doc["cues"]:
        if c["id"] == "s06_l03":
            c["en"] = c["en"] + " Extra words after the signature."
    _use(doc)
    _window()
    _said("s06_l03")
    assert ceremony.recent_cues_th() is None


# ---- the common area: the figure is the cue's own ------------------------------------------------------

def test_the_common_area_figure_comes_from_the_approved_cue_and_follows_a_re_signed_script(signed):
    rule = ceremony.common_area_th()
    row = next(r for r in event_script.cue("s06_l03")["numbers"] if "ตารางเมตร" in r["say"])
    assert row["say"] in rule and "ส่งต่อทีมขาย" in rule and "ทีมขายประมาณ" in rule
    # the figure is no literal of this code (a re-signed script is the single source)
    src = (ROOT / "app" / "ceremony.py").read_text(encoding="utf-8")
    assert "2,500" not in src and "2500" not in src
    # a re-signed script with another row: the rule follows it
    doc = _doc(True)
    for c in doc["cues"]:
        if c["id"] == "s06_l03":
            for r in c["numbers"]:
                if "ตารางเมตร" in r["say"]:
                    r["say"] = "3,000 ตารางเมตร"
            c["th"] = c["th"].replace("2,500 ตารางเมตร", "3,000 ตารางเมตร")
            c["en"] = c["en"].replace("2,500 square metres", "3,000 square metres")
    resigned = copy.deepcopy(doc)
    resigned["cues"] = [_signed({k: v for k, v in c.items() if not k.startswith("approved")}) for c in doc["cues"]]
    _use(resigned)
    served = event_script.servable(event_script.load()).get("s06_l03")
    if served is not None:                                  # (the number-source check may refuse a figure the report lacks)
        assert "3,000 ตารางเมตร" in ceremony.common_area_th()
    else:
        assert ceremony.common_area_th() == ceremony.COMMON_AREA_HANDOVER_TH


def test_the_common_area_unsigned_hands_over_with_no_figure(unsigned):
    rule = ceremony.common_area_th()
    assert rule == ceremony.COMMON_AREA_HANDOVER_TH and "ส่งต่อทีมขาย" in rule
    assert not any(ch.isdigit() for ch in rule)


def test_the_prompt_says_it_once_per_session_and_the_note_follows_the_file(signed):
    first = ceremony.common_area_th()
    _use(_doc(False))
    assert ceremony.common_area_th() == ceremony.COMMON_AREA_HANDOVER_TH != first


# ---- the script's approved facts: "เอาตามบทเลย เพราะเหมือนจอ led จะผิด" (owner 24 Nov) ---------------------

def test_the_script_wins_rule_and_the_facts_block_ride_with_a_window_open(signed):
    _window()
    note = event_host.ceremony_note()
    assert ceremony.SCRIPT_WINS_TH in note and "ใช้ตัวเลข/ถ้อยคำตามบทเสมอ" in ceremony.SCRIPT_WINS_TH
    facts = ceremony.script_facts_th()
    assert facts in note and facts in "\n".join(event_host.ceremony_block().split("\n"))
    # the beach question's context carries the script's 600 m (not the story's driving minutes)
    assert "about 600 metres from Jomtien Beach" in note
    for figure in ("1,066 units", "155 metres", "1.3 metres deep", "8 storeys", "about 23 metres", "2,500 square metres"):
        assert figure in facts, figure
    assert "นาที" not in facts                              # no driving minutes of a story in the script's facts


def test_no_joke_cue_and_no_whole_line_is_in_the_facts_block(signed, monkeypatch):
    from app import ceremony_follow

    # (this tmp ceremony has no host file beside it: the two answers said in two lines, as the tracked one names them)
    monkeypatch.setattr(ceremony_follow, "answer_in_two", lambda cue: cue in ("s02_l05", "s05_l08", "s05_l13"))
    facts = ceremony.script_facts_th()
    doc = event_script.load()
    jokes = ceremony._joke_cues(doc)
    assert jokes == {"s04_l09", "s04_l11", "s04_l13", "s06_l05", "s06_l07"}      # (her laps; not the chains of questions 1 and 9)
    for cue in event_script.cues(doc):
        en = " ".join((cue.get("en") or "").split())
        assert not en or en not in facts, cue["id"]          # never a line she could say again whole
        if cue["id"] in jokes and en:
            for sentence in ceremony._SENTENCE_END.split(en):
                assert sentence.strip(" .") not in facts, (cue["id"], sentence)
    assert "ห้ามพูดซ้ำ" not in facts and "ไม่ใช่ประโยคให้พูดซ้ำ" in facts
    # facts, not speech: no suggestion to a guest, and a long line is cut to the clause with the figure
    assert "suggest" not in facts and "recommend" not in facts
    body = facts.split("): ", 1)[1]
    assert all(len(part.split()) <= 20 for part in body.split(" · "))


def test_with_nothing_signed_there_is_no_facts_block_and_the_common_area_hands_over(unsigned):
    assert ceremony.script_facts_th() is None
    assert ceremony.SCRIPT_FACTS_TH.split("%s")[0] not in "".join(ceremony.prompt_rules())
    _window()
    note = event_host.ceremony_note()
    assert ceremony.SCRIPT_FACTS_TH.split("%s")[0] not in note
    assert ceremony.COMMON_AREA_HANDOVER_TH in note and ceremony.SCRIPT_WINS_TH in note


def test_the_facts_block_is_in_the_ceremony_context_only(signed):
    facts = ceremony.script_facts_th()
    for profile in ("condo", "event", "emma", "joke", "translator"):
        other = build_instructions(settings.project_name, languages=settings.reply_languages,
                                   robot_name=settings.robot_name, profile=profile, robot_face=True)
        assert facts not in other and ceremony.SCRIPT_WINS_TH not in other, profile
    assert facts in build_instructions(settings.project_name, languages=settings.reply_languages,
                                       robot_name=settings.robot_name, profile="ceremony", robot_face=True)
    assert event_host.scene_note() is None or facts not in event_host.scene_note()
    assert ceremony.live_extras() is None                    # no window, no extras


def test_the_facts_follow_a_re_signed_script_and_leave_out_a_cue_that_drifted(signed):
    doc = _doc(True)
    for c in doc["cues"]:
        if c["id"] == "s03_l03":
            c["en"] = c["en"] + " Extra words after the signature."          # signed copy no longer matches
    _use(doc)
    facts = ceremony.script_facts_th()
    assert "about 600 metres" not in facts and "1,066 units" in facts
