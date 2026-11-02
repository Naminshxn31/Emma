"""Offline fixed lines: drafts are never said, approval covers the words,
and no line may break a content rule (app/offline/lines.py)."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from app.offline import lines

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = ROOT / "data" / "projects" / "embassy_world" / "offline" / "lines.json"
sys.path.insert(0, str(ROOT / "scripts"))
import approve_offline_lines  # noqa: E402


@pytest.fixture
def copy(tmp_path, monkeypatch):
    """The shipped file in tmp, and the module reading it there."""
    dst = tmp_path / "lines.json"
    shutil.copy(SHIPPED, dst)
    monkeypatch.setattr(lines, "path", lambda project_id=None: dst)
    lines.reset()
    return dst


def test_the_shipped_file_is_this_projects_and_has_every_id():
    data = json.loads(SHIPPED.read_text(encoding="utf-8"))
    assert data["source_id"] == lines.SOURCE_ID and data["project_id"] == "embassy_world"
    assert {ln["id"] for ln in data["lines"]} >= set(lines.IDS)


def test_every_shipped_line_is_a_draft_nobody_signed():
    """Nothing here fills approved_by for a person (the 6 Oct facts rule):
    the file ships unsigned, so offline mode says no fixed line until the
    owner runs approve_offline_lines.py."""
    data = json.loads(SHIPPED.read_text(encoding="utf-8"))
    for line in data["lines"]:
        assert line["approved_by"] is None and line["approved_at"] is None, line["id"]
        assert not lines.approved(line)
    assert lines.servable(data) == {}


def test_every_shipped_draft_passes_the_content_rules():
    """No digits, no "ไม่มีข้อมูล", no "อยู่เองหรือลงทุน", no hedging, no
    promise to call staff, Thai polite, no markdown -- checked on the words
    the owner will be asked to sign."""
    data = json.loads(SHIPPED.read_text(encoding="utf-8"))
    for line in data["lines"]:
        assert lines.problems(line) == [], line["id"]


def test_no_shipped_line_raises_price_or_availability():
    """A fixed line cannot know whether the guest asked about money, and the
    router's commercial gate also catches opening hours, completion and
    booking: a line that names price is Emma raising it herself (owner 8 Oct;
    review 26 Oct reproduced "For prices and the units still available..."
    answering "What time does the gym open?")."""
    import re

    data = json.loads(SHIPPED.read_text(encoding="utf-8"))
    names = re.compile(r"ราคา|ห้องว่าง|ยังว่าง|\bprices?\b|\bpricing\b|\bcosts?\b|\bavailab", re.IGNORECASE)
    for line in data["lines"]:
        for lang in lines.LANGS:
            assert not names.search(line[lang]), (line["id"], lang)


def test_the_hand_offs_promise_nothing():
    """They are said exactly when a question did not match a story; "...I can
    tell you about our facilities" right after a facility question she could
    not answer (and on a session with no stories at all) was a promise she
    could not keep (review 26 Oct)."""
    import re

    data = json.loads(SHIPPED.read_text(encoding="utf-8"))
    offers = re.compile(r"ดิฉันเล่า|เล่าให้ฟัง|tell you about|I can|I'm happy to", re.IGNORECASE)
    for line in data["lines"]:
        if line["id"].startswith("handoff"):
            for lang in lines.LANGS:
                assert not offers.search(line[lang]), (line["id"], lang)


@pytest.mark.parametrize("th,en,breach", [
    ("มี 6 อาคารค่ะ", "There are six.", "digit_th"),
    ("ขออภัยค่ะ ไม่มีข้อมูลเรื่องนี้", "Sorry.", "no_info_th"),
    ("สนใจซื้ออยู่เองหรือลงทุนคะ", "Fine.", "live_or_invest_th"),
    ("เป็นแนวคิดเบื้องต้นค่ะ", "Fine.", "hedge_th"),
    ("เดี๋ยวเรียนเชิญพนักงานมาค่ะ", "Fine.", "calls_staff_th"),
    ("สวัสดี", "Hello.", "not_polite_th"),
    ("สวัสดีค่ะ", "I don't know that.", "no_info_en"),
    ("สวัสดีค่ะ", "Is it to live in yourself or to invest?", "live_or_invest_en"),
    ("สวัสดีค่ะ", "**Hello**", "markdown_en"),
])
def test_a_line_that_breaks_a_rule_is_refused(th, en, breach):
    assert breach in lines.problems({"id": "x_line", "th": th, "en": en})


def test_approval_signs_the_words_and_an_edit_withdraws_it(copy):
    done = approve_offline_lines.approve(copy, "pete", {"menu"}, audit=None)
    assert done == ["menu"]
    lines.reset()
    assert set(lines.servable()) == {"menu"}
    assert lines.text("menu", "th") and lines.text("menu", "en")
    # edited after the approval: a draft again, with nobody clearing a field
    data = json.loads(copy.read_text(encoding="utf-8"))
    for line in data["lines"]:
        if line["id"] == "menu":
            line["en"] = line["en"] + " Really."
    copy.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    lines.reset()
    assert lines.servable() == {}
    assert lines.text("menu", "en") is None


def test_an_approved_line_that_breaks_a_rule_is_still_not_said(copy):
    data = json.loads(copy.read_text(encoding="utf-8"))
    line = next(ln for ln in data["lines"] if ln["id"] == "ack")
    line["th"] = "มีห้องว่าง 5 ห้องค่ะ"
    line.update(approved_by="pete", approved_at="2026-10-26T00:00:00+07:00",
                approved_sha256=lines.digest(line))
    copy.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    lines.reset()
    assert "ack" not in lines.servable()


def test_the_approve_script_needs_a_name_and_refuses_a_broken_line(copy):
    with pytest.raises(SystemExit):
        approve_offline_lines.approve(copy, "  ", None, audit=None)
    data = json.loads(copy.read_text(encoding="utf-8"))
    data["lines"][0]["th"] = "ไม่ทราบค่ะ"
    copy.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SystemExit):
        approve_offline_lines.approve(copy, "pete", None, audit=None)
    assert all(ln["approved_by"] is None for ln in json.loads(copy.read_text(encoding="utf-8"))["lines"])


def test_revoke_puts_a_line_back_to_draft(copy):
    approve_offline_lines.approve(copy, "pete", None, audit=None)
    lines.reset()
    assert set(lines.servable()) == set(lines.IDS)
    approve_offline_lines.revoke(copy, {"ack"})
    lines.reset()
    assert "ack" not in lines.servable()


def test_another_projects_file_is_not_read(copy):
    data = json.loads(copy.read_text(encoding="utf-8"))
    data["project_id"] = "embassy_life"
    copy.write_text(json.dumps(data), encoding="utf-8")
    lines.reset()
    assert lines.load() is None and lines.servable() == {}


def test_a_third_language_falls_back_to_english(copy):
    approve_offline_lines.approve(copy, "pete", {"ack"}, audit=None)
    lines.reset()
    assert lines.text("ack", "zh") == lines.text("ack", "en")
