"""What EmmaJoke says on the robot face — the content review of 9 Oct 2026.

docs/robot-content-review-2026-10-09.md: the server on the robot ran the
`joke` profile all day (98 calls, 532 heard, 397 replies). Each test below
pins one rule that answers a failure counted in that log, and quotes the
wrong sentence the rule quotes — quoting is what this model obeys, so a
rewording that drops the quote is a regression even if it reads the same.

The sales room (condo) and the owner's assistant (emma) must not move:
their prompts are hashed in tests/data/body_nav_p0_snapshot.json and
body_nav_p6_snapshot.json, which only the joke entries were re-taken for.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from app.config import settings
from app.prompts import (EMMA_GREETING, GREETING, JOKE_GREETING, build_instructions,
                         greeting_for)

ROOT = Path(__file__).resolve().parent.parent


def _joke(face: bool = False) -> str:
    return build_instructions("Embassy World", profile="joke", robot_name="Emma",
                              robot_face=face)


def test_the_robot_face_does_not_treat_every_visitor_as_its_owner():
    """17 of 397 replies called the person in front คุณเจ้าของ, บอส or
    ท่านประธาน, or said "เป็นผู้ช่วยส่วนตัวของคุณ" — the joke profile wore the
    owner's-assistant introduction ("คนที่คุยกับคุณคือเจ้าของของคุณ")."""
    for face in (False, True):
        text = _joke(face)
        assert "คนที่คุยกับคุณคือเจ้าของ" not in text
        assert "ผู้ช่วยส่วนตัวสั่งงานด้วยเสียง" not in text
        assert text.startswith('คุณคือ "Emma" หุ่นยนต์ต้อนรับ')
        assert "ห้ามเรียกเจ้าของ บอส หรือท่าน" in text
        assert "ห้ามอ้างเป็นผู้ช่วยส่วนตัวของใคร" in text
        # The reception answer to "who are you / what can you do".
        assert "คุยเล่น ตอบความรู้ทั่วไป แปลภาษา" in text
    # The owner's own assistant keeps its introduction.
    emma = build_instructions("Embassy World", profile="emma", robot_name="Emma")
    assert "คนที่คุยกับคุณคือเจ้าของของคุณ" in emma


def test_no_loose_offer_after_an_answer():
    """34% of the robot's replies (45% on the PC's joke sessions) ended on an
    offer like this one; the condo prompt quotes its ban and measures 4%."""
    text = _joke()
    # Wrong/right pair: the first wording listed the phrase as an example
    # and the live re-run (10 Oct, C14) still said it word for word after a
    # compliment — the condo block's ผิด/ถูก shape is the one at 4%.
    assert ('ห้ามปิดท้ายด้วยข้อเสนอลอยๆ ผิด: "ขอบคุณค่ะ มีอะไรให้เอ็มม่าช่วยดูแลเพิ่มเติมไหมคะ" '
            'ถูก: "ขอบคุณค่ะ"') in text
    assert '"บอกได้เลยนะคะ"' in text
    # A greeting may still ask how to help, and a goodbye is short.
    assert "คนทักให้ทักกลับแล้วถามว่าให้ช่วยอะไร" in text
    assert "คนขอบคุณหรือลาให้ลาสั้นๆ" in text


def test_people_talking_to_each_other_are_not_talking_to_emma():
    """Most of the day's speech was staff talk and a presentation; Emma
    commented on other people's money and work and summed up outcomes nobody
    reported ("โอนเงินเรียบร้อยแล้ว")."""
    text = _joke()
    assert "คุณได้ยินทั้งห้อง" in text
    assert "ที่ไม่ได้ถามหรือสั่งคุณ: ตอบแค่ \"ค่ะ\"" in text
    assert '"โอนเงินเรียบร้อยแล้ว สบายใจได้เลยนะคะ"' in text


def test_the_chest_light_refusal_is_said_once_and_never_into_a_presentation():
    """30 refusals in 17 calls, up to six in one — mostly into talk that never
    asked anything. The wall itself is unchanged (the tool gate)."""
    text = _joke()
    assert "ไม่ได้ดูข้อมูลโครงการ" in text and "ไฟที่หน้าอก" in text
    assert "เรื่องแตะไฟบอกครั้งเดียวพอ" in text
    assert '"เรื่องโครงการถามทีมขายได้เลยค่ะ"' in text
    assert "คนคุยหรือพรีเซนต์โครงการกันเองโดยไม่ได้ถามคุณ ห้ามแทรก" in text


def test_no_promise_without_a_tool_and_emergencies_go_to_staff():
    """Observed: offering to call emergency services for someone feeling
    faint, and promising to "coordinate and check" a clock problem."""
    text = _joke()
    assert '"เดี๋ยวเอ็มม่าประสานงานตรวจสอบให้นะคะ"' in text
    assert '"ให้เอ็มม่าช่วยติดต่อหน่วยงานฉุกเฉินให้ไหมคะ"' in text
    assert "แจ้งเจ้าหน้าที่ที่อยู่ใกล้ที่สุด" in text
    assert 'ห้ามบอกว่าระบบหรือทุกอย่าง "ปกติดี" ถ้าผลเครื่องมือไม่ได้บอก' in text
    assert "ห้ามชมหน้าตาหรือเสื้อผ้า" in text


def test_it_never_denies_a_language_it_just_answered_in():
    """5 replies said it did not understand Hebrew, after Hebrew replies."""
    assert '"ดิฉันไม่เข้าใจภาษาฮีบรู"' in _joke()


def test_rule_7_names_no_capability_list():
    """"(เช่น คุมไฟ แอร์ สั่งให้หุ่นเดิน)" became Emma's self-description on
    a robot whose lights are mock and whose voice walks are off ("my functions
    are limited to controlling the lights and air conditioner") — and it
    contradicted the nav line's voice-off clause."""
    for face in (False, True):
        assert "คุมไฟ แอร์ สั่งให้หุ่นเดิน" not in _joke(face)
    assert "7. สิ่งที่ทำได้ดูจากรายการเครื่องมือของคุณ ถูกสั่งให้เรียกเครื่องมือทันที" in _joke()


def test_the_paid_for_lessons_survive_the_rewrite():
    """CLAUDE.md: every profile keeps the rules bought with real bugs."""
    text = _joke()
    for kept in ("ห้ามเดา", "ทวนยืนยัน", "mock=", "ห้ามอ่าน markdown", "ห้ามสลับเป็นครับ",
                 "ห้ามตอบเนื้อหาโครงการจากความรู้เดิม"):
        assert kept in text, kept


def test_joke_greets_a_stranger_and_the_others_do_not_move():
    """EMMA_GREETING says "คนที่คุยรู้จักคุณอยู่แล้ว" — true at the owner's
    desk, false at the robot."""
    assert greeting_for("joke") == JOKE_GREETING
    assert "รู้จักคุณอยู่แล้ว" not in JOKE_GREETING
    assert "แนะนำตัวว่าเป็น Emma" in JOKE_GREETING
    assert "ห้ามเรียกคนตรงหน้าว่าเจ้าของหรือบอส" in JOKE_GREETING
    assert greeting_for("emma") == EMMA_GREETING
    assert greeting_for("condo") == GREETING
    assert greeting_for("typo") == GREETING            # unknown still lands on condo


def test_room_status_on_mock_says_it_is_not_the_room(monkeypatch):
    """The robot has no IR hub file: get_room_status answered the boot
    defaults with source=mock and no instruction, and Emma read them out as
    the room ("ตอนนี้ไฟเปิดอยู่ และแอร์ทำงานอยู่ที่...ยี่สิบห้าองศา...")."""
    import app.tools as tools_pkg
    from app.tools import broadlink_ir, registry
    from app.tools.smarthome import ROOM_STATUS_MOCK_NOTE

    tools_pkg.load_tools()
    monkeypatch.setattr(broadlink_ir, "available", lambda: False)
    out = asyncio.run(registry.dispatch("get_room_status", {}))
    assert out["source"] == "mock"
    assert out["instruction"] == ROOM_STATUS_MOCK_NOTE
    assert "ห้ามพูดว่า 'ตอนนี้ไฟเปิดอยู่ และแอร์ทำงานอยู่ที่ยี่สิบห้าองศา'" in ROOM_STATUS_MOCK_NOTE
    # With a hub the reading is the last command, unchanged.
    monkeypatch.setattr(broadlink_ir, "available", lambda: True)
    out = asyncio.run(registry.dispatch("get_room_status", {}))
    assert out["source"] == "last_command"
    assert "instruction" not in out


# ---- the content suite of scripts/eval_live_qa.py -------------------------

sys.path.insert(0, str(ROOT / "scripts"))

import eval_live_qa as harness  # noqa: E402


def test_content_suite_file_is_well_formed():
    import json

    data = json.loads(harness.CONTENT_SCENARIOS.read_text(encoding="utf-8"))
    assert harness.validate_content_cases(data) == []
    cases = harness.load_cases("content", Path("nowhere"))
    assert cases and all(c["suite"] == "content" for c in cases)
    assert harness.resolve_profile("content", None) == "joke"


def test_content_validator_rejects_what_would_measure_nothing():
    bad = {"cases": [
        {"id": "X1", "cat": "c", "rubric": "r", "turns": ["a"]},
        {"id": "C2", "cat": "c", "rubric": "r", "turns": ["a"],
         "turn_expect": [{"lang": "xx"}, {}]},
        {"id": "C3", "cat": "c", "rubric": "r", "turns": ["a"],
         "expect": {"said_none": ["("]}, "turn_expect": [{"max_len": 3}]},
    ]}
    problems = " | ".join(harness.validate_content_cases(bad))
    assert "X1" in problems                               # wrong id prefix
    assert "C2: turn_expect must be a list no longer than turns" in problems
    assert "C3: bad regex" in problems
    assert "C3: turn_expect[0] unknown key max_len" in problems


def _record(said: list[str]) -> list[dict]:
    return [{"user": "u", "said": s, "complete": True, "tools": [], "audio_s": 1.0}
            for s in said]


def test_content_flags_catch_what_the_log_showed():
    case = {"id": "C9", "suite": "content",
            "expect": {"mode_switch_max": 1},
            "turn_expect": [{"lang": "th", "max_chars": 30}, {"lang": "he"}, {}]}
    turns = _record([
        "สวัสดีค่ะ คุณเจ้าของ มีอะไรให้เอ็มม่าช่วยดูแลเพิ่มเติมไหมคะ",
        "โหมดนี้ไม่ได้ดูข้อมูลโครงการ แตะไฟที่หน้าอกได้ค่ะ",
        "ขออภัยค่ะ ดิฉันไม่เข้าใจภาษาฮีบรู แตะไฟที่หน้าอกได้ค่ะ",
    ])
    flags = harness.flags_for(case, turns, "")
    assert "t0:owner_address" in flags
    assert "t0:filler_offer" in flags
    assert any(f.startswith("t0:too_long=") for f in flags)
    assert "t1:wrong_lang=th" in flags
    assert "t2:denies_language" in flags
    assert "mode_switch_repeated=2>1" in flags


def test_content_flags_stay_quiet_on_a_good_record():
    case = {"id": "C8", "suite": "content",
            "turn_expect": [{"silent_ok": True, "max_chars": 30},
                            {"filler_ok": True, "lang": "th"}, {"lang": "en"}]}
    turns = _record(["", "สวัสดีค่ะ ให้เอ็มม่าช่วยอะไรดีคะ", "I'm Emma, the reception robot."])
    turns[0]["complete"] = False                 # silence may never complete
    assert harness.flags_for(case, turns, "") == []
    # The same silence without silent_ok is still a finding.
    case["turn_expect"][0] = {}
    assert "t0:silent" in harness.flags_for(case, turns, "")


# ---- the same lessons for the sales host on the robot face (10 Oct) ----------

def _condo(face: bool) -> str:
    return build_instructions("Embassy World", profile="condo", robot_name="Emma",
                              robot_face=face)


def test_the_sales_host_on_the_robot_face_carries_the_content_lessons():
    """Owner 10 Oct: the robot face runs condo by default, far-listening. The
    five joke lessons that are not about joke's persona apply to it, each
    with its quoted wrong sentence; and staff talk is NOT an off-topic
    question — the condo scope rule would otherwise answer it with an
    apology, the robot interrupting the Hebrew presentation."""
    from app.prompts import CONDO_FACE_CONTENT_BLOCK

    text = _condo(True)
    assert CONDO_FACE_CONTENT_BLOCK in text
    assert 'ไม่ใช่คำถามนอกเรื่อง: ตอบแค่ "ค่ะ" ห้ามขอโทษ ห้ามบอกว่าดูแลเฉพาะโครงการ' in text
    assert '"โอนเงินเรียบร้อยแล้ว สบายใจได้เลยนะคะ"' in text
    assert '"เดี๋ยวเอ็มม่าประสานงานตรวจสอบให้นะคะ"' in text
    assert "ตอบได้แม้นอกโครงการ: บอกทันทีให้แจ้งเจ้าหน้าที่ที่อยู่ใกล้ที่สุด" in text
    assert 'ห้ามบอกว่าระบบหรือทุกอย่าง "ปกติดี" ถ้าผลเครื่องมือไม่ได้บอก' in text
    assert '"ดิฉันไม่เข้าใจภาษาฮีบรู"' in text
    assert "ห้ามชมหน้าตาหรือเสื้อผ้า" in text
    # Approved numbers live in facts and tools only (the SALES_HOST_BLOCK rule).
    assert not any(ch.isdigit() for ch in CONDO_FACE_CONTENT_BLOCK)


def test_the_sales_room_desk_prompt_does_not_get_the_robot_face_lessons():
    """The desk prompt is byte-identical (snapshot) and over its budget; the
    joke and emma prompts keep their own wording, not a second copy."""
    from app.prompts import CONDO_FACE_CONTENT_BLOCK

    assert CONDO_FACE_CONTENT_BLOCK not in _condo(False)
    assert "บนหน้าหุ่นคุณได้ยินทั้งห้อง" not in _condo(False)
    for profile in ("joke", "emma"):
        for face in (False, True):
            text = build_instructions("Embassy World", profile=profile, robot_name="Emma",
                                      robot_face=face)
            assert CONDO_FACE_CONTENT_BLOCK not in text, (profile, face)
