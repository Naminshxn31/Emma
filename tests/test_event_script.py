"""The ceremony script's data: drafts never served, every number owned, sign-off
pinned to the words (app/event_script.py, scripts/approve_event_script.py) and the
scorer that measures how close a spoken line came (app/speech_match.py)."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from app import event_script, speech_match

ROOT = Path(__file__).resolve().parent.parent
#: The ceremony app's own file since 9 Nov (app/ceremony.py) -- it was data/events/floor1_event.script.json
REAL = ROOT / "data" / "ceremony" / "floor1_ceremony.json"
REPORT = ROOT / "data" / "personal-docs" / "embassy-world" / "project-report-2026-10-06"

SOURCES = {
    "facts": "ห้องชุดทั้งหมด 1,066 ยูนิต (Studio 117 · 1 ห้องนอน 801) ห่างหาดเส้นตรงประมาณ 0.6 กม.",
    "report:01_overview.md": "สระหลัก ยาว 155 เมตร",
}


def lookup(name):
    return SOURCES.get(name)


def _cue(**over) -> dict:
    raw = {"id": "s2_location", "scene": 2, "th": "ห่างจากหาดประมาณ 600 เมตรค่ะ",
           "numbers": [{"say": "600 เมตร", "source": "facts", "as": "0.6 กม.", "factor": 1000}]}
    raw.update(over)
    return event_script._normalise(raw)


def _as_drafts(doc: dict) -> dict:
    """The script with every approval taken off: what a test of "a draft is never served" needs,
    whatever the real file's state (the owner approved 29 cues on 12 Nov)."""
    import copy

    doc = copy.deepcopy(doc)
    for cue in doc.get("cues") or []:
        cue.update(approved_by=None, approved_at=None, approved_sha256=None, approved_voice_sha256=None)
    return doc


# ---- the real file -------------------------------------------------------------------

def _signed_before_its_english(c: dict) -> bool:
    """A signature that still matches the cue as it was BEFORE its English was added (17 Nov:
    the owner signed the Thai lines on 12 Nov, the ceremony became English-spoken after). The
    line is a draft again -- never said -- and nothing else about it changed since he signed."""
    thai = lambda s: any("\u0e00" <= ch <= "\u0e7f" for ch in s)       # noqa: E731
    before = dict(c, en=None, words_ok=[w for w in c.get("words_ok") or [] if thai(w)])   # + the English `words_ok`
    return bool(c.get("en")) and c.get("approved_sha256") == event_script.digest(before)


def test_the_real_scripts_signatures_match_their_words_and_a_draft_is_never_served():
    """Every approval in the real file (the owner's of 12 Nov) signs the words it sits on -- or
    the words as they were before the English was added, and then the cue is NOT served; the
    same file with its approvals taken off serves nothing."""
    doc = event_script.load(REAL)
    assert doc is not None
    cues = event_script.cues(doc)
    assert len(cues) >= 12
    for c in cues:
        assert c["approved_by"] is None or event_script.approved(c) or _signed_before_its_english(c), c["id"]
        if _signed_before_its_english(c):
            assert not event_script.approved(c) and c["id"] not in event_script.servable(doc), c["id"]
    assert not [c["id"] for c in cues if not c["th"] and not c["live_th"] and c["approved_by"]], \
        "a cue waiting for its words is never signed"
    assert event_script.servable(_as_drafts(doc)) == {}, "a draft must never be servable"


@pytest.mark.skipif(not REPORT.is_dir(), reason="NOT CHECKED: the project report (gitignored) is not on this "
                    "machine, so the ceremony lines' report-sourced numbers cannot be verified here")
def test_every_real_line_has_its_numbers_in_the_approved_data(monkeypatch):
    from app import project_report

    # conftest hides this machine's report from every other test; this one is
    # about the real lines against the real approval, so it puts it back
    monkeypatch.setattr(project_report, "manifest_path", lambda project_id=None: (
        ROOT / "data" / "projects" / "embassy_world" / "sales" / "project_report_approval.json"))
    doc = event_script.load(REAL)
    # the film check reads the event file, which conftest empties: only the words here
    bad = {c["id"]: [p for p in event_script.problems(c) if not p.startswith("clip_")]
           for c in event_script.cues(doc) if c["th"] or c["live_th"]}
    assert {k: v for k, v in bad.items() if v} == {}
    # the script's own "500 เมตร" would not pass -- the approved data says 0.6 km
    loc = event_script.cue("s03_l03", doc)
    wrong = copy.deepcopy(loc)
    wrong["th"] = wrong["th"].replace("600", "500")
    wrong["numbers"][0]["say"] = "500 เมตร"
    assert "number_row_0_mismatch" in event_script.problems(wrong)


def test_a_cue_waiting_for_its_words_is_empty_not_served():
    # the demo room's line (12 Nov script): no words until the sales team names the room
    c = event_script.cue("s05_l07", event_script.load(REAL))
    assert c is not None and not c["th"] and not c["live_th"]
    assert "empty" in event_script.problems(c)


# ---- numbers -------------------------------------------------------------------------

def test_a_number_with_no_row_is_unsourced():
    c = _cue(numbers=[])
    assert event_script.problems(c, lookup=lookup) == ["unsourced_number:600"]


def test_the_row_must_repeat_the_source_number():
    c = _cue(th="ห่างจากหาดประมาณ 700 เมตรค่ะ",
             numbers=[{"say": "700 เมตร", "source": "facts", "as": "0.6 กม.", "factor": 1000}])
    assert "number_row_0_mismatch" in event_script.problems(c, lookup=lookup)


def test_the_quoted_text_must_be_in_the_source():
    c = _cue(numbers=[{"say": "600 เมตร", "source": "facts", "as": "0.6 กิโลเมตร", "factor": 1000}])
    assert "number_row_0_not_in_source" in event_script.problems(c, lookup=lookup)


def test_an_unknown_or_unapproved_source_is_unavailable():
    c = _cue(numbers=[{"say": "600 เมตร", "source": "report:99_none.md", "as": "0.6 กม.", "factor": 1000}])
    assert "number_row_0_source_unavailable" in event_script.problems(c, lookup=lookup)


def test_a_unit_change_needs_its_factor():
    c = _cue(numbers=[{"say": "600 เมตร", "source": "facts", "as": "0.6 กม."}])
    assert "number_row_0_mismatch" in event_script.problems(c, lookup=lookup)
    assert event_script.problems(_cue(), lookup=lookup) == []


def test_every_number_must_be_covered_not_just_one():
    c = event_script._normalise({
        "id": "s3_units", "th": "มี 1,066 ยูนิต Studio 117 ห้อง และ 1 ห้องนอน 999 ห้องค่ะ",
        "numbers": [{"say": "1,066 ยูนิต", "source": "facts", "as": "1,066 ยูนิต"},
                    {"say": "Studio 117", "source": "facts", "as": "Studio 117"}]})
    probs = event_script.problems(c, lookup=lookup)
    assert "unsourced_number:1" in probs and "unsourced_number:999" in probs


def test_a_number_inside_a_longer_number_is_not_its_source():
    src = {"facts": "อาคาร 18 ชั้น ลึก 11.3 เมตร"}
    for say in ("8 ชั้น", "1.3 เมตร"):
        c = event_script._normalise({"id": "x_y", "th": "มี %sค่ะ" % say,
                                     "numbers": [{"say": say, "source": "facts", "as": say}]})
        assert "number_row_0_not_in_source" in event_script.problems(c, lookup=src.get), say
    c = event_script._normalise({"id": "x_y", "th": "สูง 18 ชั้นค่ะ",
                                 "numbers": [{"say": "8 ชั้น", "source": "facts", "as": "18 ชั้น"}]})
    assert "number_row_0_not_in_line" in event_script.problems(c, lookup=src.get)
    # and on the right: a quote ending in a digit is not the front of a longer number
    c = event_script._normalise({"id": "x_y", "th": "สระยาว 15 เมตรค่ะ",
                                 "numbers": [{"say": "ยาว 15", "source": "facts", "as": "ยาว 15"}]})
    assert "number_row_0_not_in_source" in event_script.problems(c, lookup={"facts": "สระหลัก ยาว 155 เมตร"}.get)
    c = event_script._normalise({"id": "x_y", "th": "สระยาว 155 เมตรค่ะ",
                                 "numbers": [{"say": "ยาว 15", "source": "facts", "as": "ยาว 15"}]})
    assert "number_row_0_not_in_line" in event_script.problems(c, lookup={"facts": "ยาว 15 เมตร"}.get)


def test_only_unit_changes_are_factors():
    c = event_script._normalise({"id": "x_y", "th": "มี 600 ตารางเมตรค่ะ",
                                 "numbers": [{"say": "600 ตารางเมตร", "source": "facts", "as": "18 ชั้น",
                                              "factor": 600 / 18}]})
    assert "number_row_0_bad_factor" in event_script.problems(c, lookup={"facts": "อาคาร 18 ชั้น"}.get)


def test_numbers_in_thai_words_need_a_source_too():
    c = _cue(th="สระยาวห้าร้อยเมตร และมีหนึ่งหมื่นยูนิตค่ะ", numbers=[])
    probs = event_script.problems(c, lookup=lookup)
    assert "unsourced_number:ห้าร้อย" in probs and "unsourced_number:หนึ่งหมื่น" in probs
    # words that only contain numeral letters are not numbers (the ราคา-in-อาคาร bug)
    assert event_script.problems(_cue(th="ฉันสามารถเป็นแฟนพันธุ์แท้ที่ห้างค่ะ", numbers=[]), lookup=lookup) == []
    # a Thai-word number repeats its source's digits through a row
    c = event_script._normalise({"id": "x_y", "th": "อยู่ชั้นสามค่ะ",
                                 "numbers": [{"say": "ชั้นสาม", "source": "facts", "as": "ชั้น 3"}]})
    assert event_script.problems(c, lookup={"facts": "ชั้น 3 อาคาร A"}.get) == []
    c["numbers"][0]["as"] = "ชั้น 4"
    assert "number_row_0_mismatch" in event_script.problems(c, lookup={"facts": "ชั้น 4 อาคาร A"}.get)
    assert [event_script._thai_value(w) for *_, w in event_script.thai_numerals("หนึ่งร้อยห้าสิบห้า ยี่สิบเอ็ด")] \
        == [155.0, 21.0]


def test_what_counts_as_a_number_does_not_depend_on_which_index_loaded():
    # eval 7 Nov 00:12: retrieval.tokenize learns the deck's words when its index
    # builds; "ชั้นสาม" became one word mid-run and s3_facility left servable()
    from app.tools import retrieval

    c = event_script._normalise({"id": "x_y", "th": "อยู่ชั้นสามค่ะ",
                                 "numbers": [{"say": "ชั้นสาม", "source": "facts", "as": "ชั้น 3"}]})
    src = {"facts": "ชั้น 3 อาคาร A"}.get
    assert event_script.problems(c, lookup=src) == []
    retrieval.init_tokeniser(["ชั้นสาม"])
    try:
        assert retrieval.tokenize("ชั้นสาม") == ["ชั้นสาม"]          # the trap is real
        assert event_script.problems(c, lookup=src) == []
        assert event_script.thai_numerals("อยู่ชั้นสามค่ะ") == [(8, 11, "สาม")]
    finally:
        retrieval.init_tokeniser()


def test_an_idiom_is_listed_not_sourced():
    c = _cue(th="ฉันกันน้ำได้ร้อยเปอร์เซ็นต์ค่ะ", numbers=[])
    assert event_script.problems(c, lookup=lookup) == ["unsourced_number:ร้อย"]
    c = event_script._normalise({"id": "x_y", "th": "ฉันกันน้ำได้ร้อยเปอร์เซ็นต์ค่ะ", "words_ok": ["ร้อยเปอร์เซ็นต์"]})
    assert event_script.problems(c, lookup=lookup) == []
    signed = _signed(c)
    assert not event_script.approved(dict(signed, words_ok=["ร้อยเปอร์เซ็นต์", "อีกคำ"]))


def test_a_cue_that_rolls_a_missing_film_is_not_served(monkeypatch):
    from app import event_clips

    monkeypatch.setattr(event_clips, "table", lambda: {"story": {"has_file": False}})
    c = _cue(clip="story")
    assert "clip_missing" in event_script.problems(c, lookup=lookup)
    assert "clip_unknown" in event_script.problems(_cue(clip="other"), lookup=lookup)
    monkeypatch.setattr(event_clips, "table", lambda: {"story": {"has_file": True}})
    assert event_script.problems(c, lookup=lookup) == []


def test_report_chapters_are_read_only_while_their_pin_matches(monkeypatch, tmp_path):
    from app import project_report

    (tmp_path / "01_overview.md").write_text("สระหลัก ยาว 155 เมตร\n", encoding="utf-8")
    manifest = {"approval_status": "approved", "approved_by": "x", "approved_at": "t", "folder": str(tmp_path),
                "files": {"01_overview.md": "0" * 64}, "deny_terms": []}
    monkeypatch.setattr(project_report, "load_manifest", lambda *a, **k: manifest)
    monkeypatch.setattr(project_report, "approved", lambda m: True)
    monkeypatch.setattr(event_script, "ROOT", Path("/"))
    assert event_script._report_chapter("01_overview.md") is None          # pin does not match
    manifest["files"]["01_overview.md"] = project_report._sha256(tmp_path / "01_overview.md")
    assert "155" in event_script._report_chapter("01_overview.md")
    assert event_script._report_chapter("../01_overview.md") is None
    monkeypatch.setattr(project_report, "approved", lambda m: False)
    assert event_script._report_chapter("01_overview.md") is None


# ---- content rules -------------------------------------------------------------------

@pytest.mark.parametrize("line,flag", [
    ("ขออภัยค่ะ ไม่มีข้อมูลเรื่องนี้ค่ะ", "no_info_th"),
    ("ซื้อไว้อยู่เองหรือลงทุนคะ", "live_or_invest_th"),
    ("ทั้งหมดเป็นแนวคิดเบื้องต้นค่ะ", "hedge_th"),
    ("มี Rooftop ให้ชมค่ะ", "overruled_th"),
    ("ได้เลย", "not_polite_th"),
    ("**ได้เลย**ค่ะ", "markdown_th"),
])
def test_content_rules(line, flag):
    assert flag in event_script.problems(_cue(th=line, numbers=[]), lookup=lookup)


def test_the_hosts_name_may_follow_the_polite_ending():
    assert event_script.problems(_cue(th="ได้แน่ๆ เลยค่ะคุณมิกิ", numbers=[]), lookup=lookup) == []


# ---- approval ------------------------------------------------------------------------

def _signed(c: dict) -> dict:
    c = dict(c)
    c.update(approved_by="pete", approved_at="2026-11-06T22:00:00+07:00", approved_sha256=event_script.digest(c))
    return c


def test_an_edit_after_approval_is_a_draft_again():
    c = _signed(_cue())
    assert event_script.approved(c)
    for field, value in (("th", "ห่างจากหาดประมาณ 600 เมตรเท่านั้นค่ะ"), ("clip", "opening"), ("live_th", "x")):
        assert not event_script.approved(dict(c, **{field: value})), field
    moved = copy.deepcopy(c)
    moved["numbers"][0]["source"] = "report:01_overview.md"
    assert not event_script.approved(moved)


def test_no_name_no_approval():
    c = _signed(_cue())
    assert not event_script.approved(dict(c, approved_by="  "))
    assert not event_script.approved(dict(c, approved_at=None))


def test_servable_keeps_content_rules_even_for_drafts(tmp_path):
    doc = {"format": event_script.FORMAT, "cues": [
        {"id": "a_ok", "th": "ขอบคุณทุกคนค่ะ"},
        {"id": "b_bad", "th": "ไม่มีข้อมูลค่ะ"},
    ]}
    assert event_script.servable(doc) == {}
    assert set(event_script.servable(doc, drafts=True)) == {"a_ok"}


def test_drafts_true_is_only_for_scripts_never_the_app():
    for path in (ROOT / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if path.name == "event_script.py":
            text = text.split("def servable", 1)[0]
        assert not re.search(r"drafts\s*=\s*True", text), path


def test_read_text_quotes_the_line_and_live_cues_have_none():
    c = _cue()
    assert "«ห่างจากหาดประมาณ 600 เมตรค่ะ»" in event_script.read_text(c)
    assert event_script.read_text(_cue(th=None, live_th="แนะนำห้อง", numbers=[])) is None


def test_the_script_is_the_ceremony_file_not_a_part_of_the_event_file(monkeypatch):
    """Owner 9 Nov: "อันนี้ทำแยกกับอันเดิมนะ ไม่งั้นจะปนกัน" -- CEREMONY_FILE, whatever EVENT_FILE says."""
    from app import event_host
    from app.config import settings

    monkeypatch.setattr(settings, "event_file", "data/events/floor1_event.json")
    monkeypatch.setattr(settings, "ceremony_file", "data/ceremony/floor1_ceremony.json")
    assert event_script.path() == event_host.ROOT / "data" / "ceremony" / "floor1_ceremony.json"
    monkeypatch.setattr(settings, "event_file", "")
    assert event_script.path() == REAL                   # no event file: the ceremony still has its own
    monkeypatch.setattr(settings, "ceremony_file", "")
    assert event_script.path() is None


# ---- the sign-off script -------------------------------------------------------------

@pytest.fixture
def script_copy(tmp_path):
    """The real script as drafts (the sign-off tests sign it themselves)."""
    target = tmp_path / "event.script.json"
    target.write_text(json.dumps(_as_drafts(json.loads(REAL.read_text(encoding="utf-8"))), ensure_ascii=False),
                      encoding="utf-8")
    return target


def _approve_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("approve_event_script", ROOT / "scripts" / "approve_event_script.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_approving_all_skips_the_unfinished_cue_and_signs_what_runtime_checks(script_copy, tmp_path, monkeypatch):
    mod = _approve_module()
    real_before = REAL.read_bytes()
    monkeypatch.setattr(event_script, "source_text", lambda name: "x")
    monkeypatch.setattr(event_script, "number_problems", lambda c, lookup=None: [])
    audit = tmp_path / "audit.jsonl"
    done = mod.approve(script_copy, "pete", None, audit=audit)
    assert "s05_l07" not in done and "s03_l03" in done
    doc = json.loads(script_copy.read_text(encoding="utf-8"))
    signed = {c["id"]: c for c in event_script.cues(doc)}
    assert event_script.approved(signed["s03_l03"])
    assert not event_script.approved(signed["s05_l07"])
    assert json.loads(audit.read_text(encoding="utf-8").splitlines()[0])["by"] == "pete"
    assert REAL.read_bytes() == real_before                                      # the real file untouched


def test_approving_a_broken_cue_by_id_refuses(script_copy):
    mod = _approve_module()
    with pytest.raises(SystemExit):
        mod.approve(script_copy, "pete", {"s05_l07"}, audit=None)
    with pytest.raises(SystemExit):
        mod.approve(script_copy, "  ", None, audit=None)


def test_the_recording_is_pinned_only_when_listened(script_copy, monkeypatch):
    mod = _approve_module()
    monkeypatch.setattr(event_script, "number_problems", lambda c, lookup=None: [])
    monkeypatch.setattr(mod, "_clip_sha", lambda cue: "a" * 64)
    done = mod.approve(script_copy, "pete", {"s10_l05"}, audit=None)
    assert done == ["s10_l05"]
    raw = next(r for r in json.loads(script_copy.read_text(encoding="utf-8"))["cues"] if r["id"] == "s10_l05")
    assert raw.get("approved_voice_sha256") is None            # words signed, audio not heard
    assert mod.approve(script_copy, "pete", {"s10_l05"}, audit=None, voice=True) == ["s10_l05:voice"]
    doc = json.loads(script_copy.read_text(encoding="utf-8"))
    cue = event_script.cue("s10_l05", doc)
    assert cue["approved_voice_sha256"] == "a" * 64 and event_script.approved(cue)
    # a new render is a new clip: listening again re-pins it, the words stay signed
    monkeypatch.setattr(mod, "_clip_sha", lambda cue: "b" * 64)
    assert mod.approve(script_copy, "pete", {"s10_l05"}, audit=None, voice=True) == ["s10_l05:voice"]
    assert mod.revoke(script_copy, {"s10_l05"}) == ["s10_l05"]
    cue = event_script.cue("s10_l05", json.loads(script_copy.read_text(encoding="utf-8")))
    assert cue["approved_voice_sha256"] is None and not event_script.approved(cue)


def test_revoke_returns_a_cue_to_draft(script_copy, monkeypatch):
    mod = _approve_module()
    monkeypatch.setattr(event_script, "number_problems", lambda c, lookup=None: [])
    mod.approve(script_copy, "pete", {"s10_l05"}, audit=None)
    assert mod.revoke(script_copy, {"s10_l05"}) == ["s10_l05"]
    doc = json.loads(script_copy.read_text(encoding="utf-8"))
    assert not event_script.approved(event_script.cue("s10_l05", doc))


# ---- the scorer ----------------------------------------------------------------------

def test_fold_drops_spaces_punctuation_and_thousands_commas_only():
    assert speech_match.fold("มี 1,066 ยูนิตค่ะ!") == "มี1066ยูนิตค่ะ"
    assert speech_match.fold("ที่นี่") == "ที่นี่"                       # tone marks and vowels kept


def test_fidelity_and_numbers():
    line = "โครงการของเรามีทั้งหมด 1,066 ยูนิตค่ะ"
    assert speech_match.fidelity(line, line) == 1.0
    assert speech_match.fidelity("โครงการของเรามีทั้งหมด1066ยูนิตค่ะ", line) == 1.0
    assert speech_match.fidelity("", line) == 0.0
    assert speech_match.number_check("มี 1066 ยูนิต 500 ห้อง", line) == {"missing": [], "extra": [500.0]}
