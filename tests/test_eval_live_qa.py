"""The live Q&A harness: parsing and scoring helpers, never the network.

`scripts/eval_live_qa.py` drives the real Gemini Live session, so the suite
can only check the parts that decide what gets asked and how answers are
flagged — the ones that would make a run measure the wrong thing quietly.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import eval_live_qa as harness  # noqa: E402


def test_faq_parser_reads_thai_and_english_pairs(tmp_path):
    faq = tmp_path / "19_faq.md"
    faq.write_text(
        "# บทที่ 19\n\n## 19.1 ภาพรวมโครงการ\n\n"
        "**ถาม:** Embassy World คืออะไร  \n**ตอบ:** คอนโด Low-rise 8 ชั้น\n\n"
        "**ถาม:** กี่อาคาร  \n**ตอบ:** 6 อาคาร\n\n"
        "## English quick answers\n\n"
        "**Q:** Where is it?  \n**A:** Jomtien, Pattaya.\n",
        encoding="utf-8")
    cases = harness.parse_faq(faq)
    assert [c["id"] for c in cases] == ["F19.1-01", "F19.1-02", "FEN-01"]
    assert cases[0]["turns"] == ["Embassy World คืออะไร"]
    assert cases[0]["reference"] == "คอนโด Low-rise 8 ชั้น"
    assert cases[2]["lang"] == "en" and cases[2]["turns"] == ["Where is it?"]


def test_missing_faq_is_loud_not_an_empty_pass(tmp_path, capsys):
    # A run on a machine without the (gitignored) report must not look like
    # a clean FAQ score — it must say the FAQ was not measured.
    cases = harness.load_cases("faq", tmp_path / "nope.md")
    assert cases == []
    assert "NOT being measured" in capsys.readouterr().err


def test_flags_catch_filler_minutes_and_unsourced_numbers():
    turn = {"user": "ไปหาดกี่นาที", "complete": True, "audio_s": 6.0, "tools": [],
            "said": "ประมาณ 5 นาทีถึงหาด ห่าง 0.6 กม. สนใจดูผังไหมคะ"}
    flags = harness.flags_for({"expect": {}}, [turn], "ห่างหาดจอมเทียนเส้นตรงประมาณ 0.6 กม.")
    assert "t0:filler_question_at_end" in flags
    assert "t0:mentions_minutes" in flags
    # 0.6 is in the approved text; nothing else multi-digit was said.
    assert not any(f.startswith("t0:numbers_not_in_sources") for f in flags)

    invented = dict(turn, said="ค่าเช่าประมาณ 15000 บาทต่อเดือนค่ะ")
    flags = harness.flags_for({"expect": {}}, [invented], "ค่าส่วนกลาง 68 บาท")
    assert "t0:numbers_not_in_sources=15000" in flags


def test_flags_catch_the_banned_concept_caveat():
    """Owner 2026-10-10: never "เป็นแนวคิดเบื้องต้น อาจมีการเปลี่ยนแปลง". The
    run must flag it (Thai and English) and must not flag the plain answer
    or the project's own story ("แนวคิด One Place Many Worlds")."""
    base = {"user": "มีสระไหม", "complete": True, "audio_s": 4.0, "tools": []}
    for said in ("มี Sky Pool ค่ะ ตอนนี้ยังเป็นแนวคิดเบื้องต้นและอาจมีการเปลี่ยนแปลงนะคะ",
                 "There is a Sky Pool, though it is a preliminary concept and may change."):
        assert "t0:concept_caveat" in harness.flags_for({"expect": {}}, [dict(base, said=said)], "")
    for said in ("มี Sky Pool ที่ชั้น 3 อาคาร A ค่ะ",
                 "แนวคิดของเราคือ One Place Many Worlds ค่ะ",
                 "ราคาอาจเปลี่ยนแปลงตามห้องที่เหลือค่ะ",
                 "Prices may change with the units that remain."):
        assert "t0:concept_caveat" not in harness.flags_for({"expect": {}}, [dict(base, said=said)], "")


def test_the_owner_ordered_section_question_is_not_filler():
    """A broad facility question is answered with the sections and "which
    one" (owner 2026-10-10) — the filler flag must not punish exactly that."""
    base = {"user": "มีอะไรให้ใช้บ้าง", "complete": True, "audio_s": 5.0, "tools": []}
    for said in ("มีชั้น G ชั้นใต้ดิน ชั้น 3 อาคาร A ภายนอก และเทคโนโลยีค่ะ อยากฟังส่วนไหนคะ",
                 "We have the ground floor, the basement and the rooftop. Which area would you like to hear about?"):
        flags = harness.flags_for({"expect": {}}, [dict(base, said=said)], "")
        assert "t0:filler_question_at_end" not in flags, said
    offer = dict(base, said="มีสระค่ะ สนใจดูผังไหมคะ")
    assert "t0:filler_question_at_end" in harness.flags_for({"expect": {}}, [offer], "")
    # After a specific question the same "ส่วนไหน" is filler again.
    specific = {"user": "มีสระไหม ฟิตเนสไหม", "complete": True, "audio_s": 5.0, "tools": [],
                "said": "มีค่ะ ฟิตเนสอยู่ชั้น G อยากทราบรายละเอียดส่วนไหนเพิ่มเติมไหมคะ"}
    assert "t0:filler_question_at_end" in harness.flags_for({"expect": {}}, [specific], "")


def test_flags_check_expected_and_forbidden_tools():
    turn = {"user": "x", "said": "ค่ะ", "complete": True, "audio_s": 1.0,
            "tools": [{"name": "show_unit", "args": {}, "result": {"ok": True}}]}
    case = {"expect": {"tools_any": ["find_units"], "tools_none": ["show_unit"]}}
    flags = harness.flags_for(case, [turn], "")
    assert "expected_tool_not_called=find_units" in flags
    assert "forbidden_tool_called=show_unit" in flags


def test_scenario_file_is_well_formed():
    data = json.loads(harness.SCENARIOS.read_text(encoding="utf-8"))
    ids = [c["id"] for c in data["cases"]]
    assert len(ids) == len(set(ids)), "duplicate scenario ids"
    for case in data["cases"]:
        assert case["id"].startswith("S"), case["id"]
        assert case.get("cat") and case.get("rubric"), case["id"]
        assert isinstance(case["turns"], list), case["id"]
        for name in (case.get("expect") or {}).get("tools_any", []):
            assert isinstance(name, str)
    # Every one of the 16 approved script entries has at least one scenario.
    script = json.loads((ROOT / "data/projects/embassy_world/sales/qa_script.json")
                        .read_text(encoding="utf-8"))
    covered = {c["cat"][:2] for c in data["cases"]}
    for n in range(1, len(script["entries"]) + 1):
        assert f"{n:02d}" in covered, f"qa_script q{n:02d} has no scenario"


def test_flags_catch_telling_the_guest_there_is_no_information():
    """Owner 2026-10-12: never "ไม่มีข้อมูล / ไม่ทราบ / ไม่มีในระบบ"; hand over to
    the sales team naturally. A polite "ไม่ทราบว่า...คะ" question is fine."""
    base = {"user": "เลี้ยงสัตว์ได้ไหม", "complete": True, "audio_s": 4.0, "tools": []}
    for said in ("ขออภัยค่ะ ดิฉันไม่มีข้อมูลเรื่องนี้ค่ะ",
                 "I don't have that information in my system."):
        assert "t0:says_no_info" in harness.flags_for({"expect": {}}, [dict(base, said=said)], "")
    for said in ("เรื่องนี้ทีมขายของเราจะให้รายละเอียดได้ครบที่สุดค่ะ สอบถามที่โต๊ะทีมขายได้เลยนะคะ",
                 "ไม่ทราบว่าสนใจห้องแบบไหนคะ"):
        assert "t0:says_no_info" not in harness.flags_for({"expect": {}}, [dict(base, said=said)], "")
