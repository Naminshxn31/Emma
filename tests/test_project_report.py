"""The approved project report as a customer source (owner, 12 Oct 2026:
"ข้อมูลพวกนี้อนุมัติให้หุ่นตอบได้ จะมีแค่เรื่องราคาห้องเท่านั้นที่ห้ามพูด").

Built on a tmp folder + manifest: the real chapters are gitignored, and
conftest points the real manifest away for every other test.
"""
import hashlib
import json

import pytest

from app import project_report


def _manifest(folder, files, **over):
    data = {
        "source_id": "project_report", "project_id": "embassy_world",
        "approval_status": "approved", "approved_by": "pete",
        "approved_at": "2026-10-12T09:00:00+07:00", "effective_at": "2026-10-12T09:00:00+07:00",
        "disclosure_scope": "customer", "content_state": "completed",
        "folder": str(folder), "deny_terms": ["Thermal Galaxy", "Rooftop", "อู่ตะเภา"],
        "files": {name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in files},
    }
    data.update(over)
    return data


@pytest.fixture
def report(tmp_path, monkeypatch):
    folder = tmp_path / "report"
    folder.mkdir()
    (folder / "07_parking.md").write_text(
        "## ที่จอดรถ\n\n**ถาม:** โครงการมีที่จอดรถกี่คัน\n**ตอบ:** ชั้นใต้ดินมีช่องจอดรถยนต์ประมาณ 224 ช่อง "
        "และช่องจอดรถจักรยานยนต์ประมาณ 108 ช่อง ตามแบบผังล่าสุด\n"
        "ช่องจอดเช่าเพิ่มเดือนละ 1,500 บาท\n"
        "มี EV Charger ที่ชั้นใต้ดิน ใกล้ Thermal Galaxy เดิม\n", encoding="utf-8")
    (folder / "19_faq.md").write_text(
        "**ถาม:** สระว่ายน้ำกลางโครงการยาวเท่าไร\n**ตอบ:** สระกลางยาวประมาณ 155 เมตร ตามแบบ\n"
        "ห้อง A-201 ราคา 3,190,000 บาท\n"
        "ไปสนามบินอู่ตะเภาประมาณ 40–60 นาที\n", encoding="utf-8")
    path = tmp_path / "approval.json"

    def write(**over):
        path.write_text(json.dumps(_manifest(folder, ["07_parking.md", "19_faq.md"], **over),
                                   ensure_ascii=False), encoding="utf-8")
        project_report.reset()

    monkeypatch.setattr(project_report, "manifest_path", lambda project_id=None: path)
    write()
    return folder, write


def test_an_approved_report_answers_parking_and_the_pool(report):
    hits = project_report.search("ที่จอดรถกี่คัน")
    assert hits and hits[0]["source_id"] == "project_report"
    assert "224" in hits[0]["text"] and "108" in hits[0]["text"]
    assert any("155" in h["text"] for h in project_report.search("สระว่ายน้ำยาวเท่าไร"))


def test_money_and_overruled_terms_never_leave_the_server(report):
    """Room prices are the one thing the owner kept closed; Thermal Galaxy,
    Rooftop and Utapao's 40-60 minutes were overruled the same morning."""
    text = " ".join(h["text"] for q in ("ที่จอดรถ", "สระว่ายน้ำยาว", "ห้อง A-201 ราคา", "สนามบินอู่ตะเภา")
                    for h in project_report.search(q))
    for gone in ("บาท", "3,190,000", "1,500", "Thermal Galaxy", "อู่ตะเภา", "40–60"):
        assert gone not in text, gone


def test_deny_by_default(report):
    folder, write = report
    write(approval_status="draft")
    assert project_report.search("ที่จอดรถกี่คัน") == []
    write(disclosure_scope="internal")
    assert project_report.search("ที่จอดรถกี่คัน") == []
    write(project_id="embassy_life")
    assert project_report.search("ที่จอดรถกี่คัน") == []


def test_a_chapter_edited_after_the_approval_is_not_served(report):
    folder, write = report
    write()
    (folder / "07_parking.md").write_text("ที่จอดรถ 999 ช่อง\n", encoding="utf-8")
    project_report.reset()
    assert all("999" not in h["text"] for h in project_report.search("ที่จอดรถกี่ช่อง"))


def test_search_condo_info_carries_the_report_and_never_for_money(report, monkeypatch):
    from app.tools import knowledge

    out = knowledge.search_condo_info("โครงการมีที่จอดรถกี่คัน")
    assert out["found"] is True
    assert any(r.get("source_id") == "project_report" for r in out["results"])
    assert knowledge.REPORT_INSTRUCTION in out["instruction"]
    assert "ห้ามพูดราคาห้อง" in knowledge.REPORT_INSTRUCTION
    money = knowledge.search_condo_info("ห้อง A-201 ราคาเท่าไหร่")
    assert money["results"] == [] and "project_report" not in json.dumps(money, ensure_ascii=False)


def test_the_real_manifest_pins_only_customer_chapters():
    """The tracked manifest: approved, customer scope, and the pricing,
    sales-status, source-conflict and method chapters left out."""
    from pathlib import Path

    real = json.loads((Path(__file__).resolve().parent.parent / "data" / "projects" / "embassy_world"
                       / "sales" / "project_report_approval.json").read_text(encoding="utf-8"))
    assert real["approval_status"] == "approved" and real["disclosure_scope"] == "customer"
    for name in ("16_pricing.md", "17_sales_status.md", "20_conflicts_sources.md", "00_front.md",
                 "_BRIEF.md", "90_appendix_units.md"):
        assert name not in real["files"], name
    assert all(len(pin) == 64 for pin in real["files"].values())
    assert {"Thermal Galaxy", "Rooftop", "อู่ตะเภา"} <= set(real["deny_terms"])


def test_a_heading_that_names_an_overruled_thing_takes_its_section(tmp_path, monkeypatch):
    """Review 12 Oct: line by line, "## 12.12 สวนบนดาดฟ้า ..." survived while the
    line saying the Rooftop Garden is in no drawing was cut, and Emma said
    "บนดาดฟ้ามีสวนลอยฟ้า". Under "### 5.5.1 สนามบินอู่ตะเภา" the bullets that
    never name the airport ("≈ 55 นาที") passed. A section can span chunks."""
    deny = ["Rooftop", "อู่ตะเภา", "สวนบนดาดฟ้า"]
    text = ("## 12.11 แสงสว่าง\nไฟ LED ฝังทางเดิน\n\n"
            "## 12.12 สวนบนดาดฟ้า และ Eco Dashboard\n**Rooftop Garden:** ไม่พบในแบบ\n"
            "ดาดฟ้าเป็นพื้นเรียบ\n### 12.12.1 รายละเอียด\nบนหลังคามีจากุซซี่\n\n"
            "## 12.13 พื้นที่สีเขียว\nพื้นที่สีเขียว 4,256 ตร.ม.\n")
    out = project_report.clean(text, deny)
    assert "ไฟ LED" in out and "4,256" in out and "12.13" in out
    for gone in ("12.12", "Rooftop", "พื้นเรียบ", "จากุซซี่", "12.12.1"):
        assert gone not in out, gone
    # across chunks of one file: the state carries the open section
    state = {}
    first = project_report.clean("### 5.5.1 สนามบินอู่ตะเภา\n- ≈ 41 กม. (≈ 55 นาที)", deny, state)
    second = project_report.clean("- ให้ตอบ 40–60 นาที\n### 5.5.2 รถไฟ\nยังไม่เปิด", deny, state)
    assert first == "" and "55" not in second and "40–60" not in second and "ยังไม่เปิด" in second


def test_the_real_manifest_cuts_the_rooftop_in_thai_too():
    """"Rooftop" alone never matched the Thai chapters (สวนบนดาดฟ้า, สวนดาดฟ้า,
    จากุซซี่บนดาดฟ้า), nor the lines that give Utapao 40-60 minutes without
    naming it. "ดาดฟ้า" alone stays: the roof deck's height is approved."""
    from pathlib import Path

    real = json.loads((Path(__file__).resolve().parent.parent / "data" / "projects" / "embassy_world"
                       / "sales" / "project_report_approval.json").read_text(encoding="utf-8"))
    terms = real["deny_terms"]
    assert {"สวนบนดาดฟ้า", "สวนดาดฟ้า", "จากุซซี่บนดาดฟ้า", "สวนบนหลังคา", "40–60"} <= set(terms)
    assert "ดาดฟ้า" not in terms
    assert project_report.clean("พื้นดาดฟ้าสูงประมาณ 23 เมตรจากระดับถนน", terms) != ""
    for line in ("ระเบียงหรือสวนดาดฟ้ามีต้นไม้ใหญ่และโซฟา", "ดาดฟ้า: \"ภายหลังจะทำสวน ตอนนี้ยังไม่มี\"",
                 "| ดาดฟ้าใช้ทำอะไรได้ | รายการ facility ทางการมี ...", "ให้ตอบเป็นช่วง ประมาณ 40–60 นาที"):
        assert project_report.clean(line, terms) == "", line


def test_the_report_never_hands_her_the_words_no_information():
    """Owner 12 Oct: never "ไม่มีข้อมูล" -- the report's "ยังไม่มีข้อมูล..." for an
    unsettled figure reaches her as "ยังไม่สรุป"."""
    assert project_report.clean("ความสูงเพดานจริงยังไม่มีข้อมูลยืนยัน", []) == "ความสูงเพดานจริงยังไม่สรุปยืนยัน"



def test_the_reports_own_question_gets_the_reports_own_answer(report):
    """12 Oct: "เพดานห้องสูงเท่าไร" found nothing (the passages say ความสูง
    เพดาน) although the report's FAQ asks exactly that. The guest's question
    is matched to the FAQ's question and the FAQ's own answer comes back,
    cleaned like every passage; overruled sections never answer."""
    folder, write = report
    (folder / "19_faq.md").write_text(
        "## 1. ห้อง\n\n**ถาม:** เพดานห้องสูงเท่าไร\n**ตอบ:** ตามโมเดลสถาปนิกพื้นถึงพื้นประมาณ 2.84 เมตร ฝ้าจริงยังไม่สรุป\n\n"
        "**ถาม:** ห้องมีเฟอร์นิเจอร์ให้ไหม\n**ตอบ:** มีแพ็กเกจเฟอร์นิเจอร์ตามแบบห้อง ราคา 99,000 บาท\n\n"
        "## 9. ดาดฟ้า Rooftop\n\n**ถาม:** บนดาดฟ้ามีอะไร\n**ตอบ:** สวนลอยฟ้าและจากุซซี่ตามแบบเดิมของโครงการ\n\n"
        # enough questions for word rarity to mean something (the real FAQ has 301)
        "## 10. อื่นๆ\n\n" + "".join(f"**ถาม:** จุดบริการที่ {i} อยู่ชั้นไหน\n**ตอบ:** จุดบริการที่ {i} อยู่ชั้น G ตามแบบผัง\n\n"
                                    for i in range(40)),
        encoding="utf-8")
    write()
    hits = project_report.search("เพดานห้องสูงเท่าไร")
    assert hits and hits[0]["text"].startswith("ถาม: เพดานห้องสูงเท่าไร")
    assert "2.84" in hits[0]["text"]
    # a question with no distinctive word carries nothing to match
    assert not any(h["text"].startswith("ถาม:") for h in project_report.search("มีห้องแบบไหนบ้าง"))
    # an overruled section answers nothing; an answer that was only money is gone
    text = " ".join(h["text"] for q in ("บนดาดฟ้ามีอะไร", "ห้องมีเฟอร์นิเจอร์ให้ไหม")
                    for h in project_report.search(q))
    assert "จากุซซี่" not in text and "99,000" not in text


def test_a_synonym_is_another_way_to_say_it_not_one_more_word(report):
    """Live 12 Oct: "สองห้องนอนมีกี่ห้องน้ำ" got the 1-Bedroom answer. The
    synonym (2-Bedroom) was added to the question, so a passage needed สอง
    AND 2-Bedroom; the table that answers says only 2-Bedroom and tied with
    every 1-Bedroom passage -- and a 1-Bedroom passage that happens to say
    สอง ("หน้าต่างสองบาน") covered the question better than the answer."""
    folder, write = report
    (folder / "07_parking.md").write_text(
        "ห้อง 1-Bedroom มีห้องนอน 1 ห้อง ห้องน้ำ 1 ห้อง ห้องนอนกว้าง ห้องน้ำมีเครื่องทำน้ำอุ่น "
        "ห้องนอนมีหน้าต่างสองบาน ห้องน้ำมีอ่างล้างหน้า ห้องนอนและห้องน้ำอยู่ติดกัน\n", encoding="utf-8")
    (folder / "19_faq.md").write_text(
        "| มีกี่ห้องน้ำ / แอร์ | ห้อง 2-Bedroom มีห้องนอน 2 ห้อง และห้องน้ำ 2 ห้อง แต่ละห้องมีเครื่องทำน้ำอุ่น |\n",
        encoding="utf-8")
    write()
    hits = project_report.search("สองห้องนอนมีกี่ห้องน้ำ")
    assert hits and "2-Bedroom" in hits[0]["text"]
    # the report's English name must be in the passage: "bedroom" alone
    # (the digit is not a word) never lets the 1-Bedroom passage count as one
    assert all("1-Bedroom" not in h["text"] for h in hits[:1])
