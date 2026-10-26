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
    # owner 15 Oct: the jellyfish floor of Journey to Mars was dropped from the design
    assert {"แมงกะพรุน", "jellyfish"} <= set(terms)
    assert project_report.clean("| ลายพื้น | ฉายภาพแมงกะพรุนเรืองแสงบนพื้นสระ |", terms) == ""
    # owner 15 Oct: "เปิดเผยชื่อได้" -- the FAQ that told the bot never to name
    # the executives would have argued with the executives story
    for line in ("**ถาม:** ขอทราบชื่อพนักงานขาย ผู้บริหาร หรือข้อมูลลูกค้าท่านอื่นที่ซื้อไปแล้ว  ",
                 "**ตอบ:** ขออภัย แชทบอทไม่เปิดเผยชื่อหรือข้อมูลส่วนบุคคลของพนักงาน ผู้บริหาร หรือลูกค้า"):
        assert project_report.clean(line, terms) == "", line
    assert project_report.clean("| Chief Executive Officer (CEO) | **Miki Haim** |", terms) != ""
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


def test_the_guests_own_words_bring_the_faq_answer_the_models_query_lost(monkeypatch):
    """Robot 20 Oct: "Why is it called Biogenesis?" was searched as "Biogenesis"
    and "how high is the ceiling" as "unit details" -- the report's FAQ answers
    both. The caption of the utterance being answered is matched against the
    FAQ too (only the FAQ, at its own bar); money questions never are."""
    from app import heard, project_report, turnlog
    from app.tools import knowledge

    rows = []
    monkeypatch.setattr(turnlog, "record", lambda event, **f: rows.append({"event": event, **f}))
    asked = []

    def faq(text):
        asked.append(text)
        return [{"source_id": "project_report", "file": "19_faq.md", "text": "ถาม: ชื่อ\nตอบ: คำตอบ"}]

    monkeypatch.setattr(project_report, "faq", faq)

    class Listener:
        pass

    who = Listener()
    heard.bind_session(who)
    try:
        heard.mark_activity(who)
        heard.record("Why is it called Biogenesis?", who, utterance_id="1")
        extra = knowledge._guest_faq("Biogenesis", [])
        assert asked == ["Why is it called Biogenesis?"] and len(extra) == 1
        assert rows and rows[-1]["event"] == "lookup_guest_faq"
        # already brought by the model's own query: not twice -- even under
        # another question line (the Thai one, an English one)
        assert knowledge._guest_faq("Biogenesis", extra) == []
        assert knowledge._guest_faq("Biogenesis", [{"text": "ถาม: อีกคำถาม\nตอบ: คำตอบ"}]) == []
        # the model searched the guest's words as they were: nothing to add
        assert knowledge._guest_words("why is it called  Biogenesis?") is None
        # a money question is the price gate's, never the FAQ's
        heard.mark_activity(who)
        heard.record("How much is the price per square metre?", who, utterance_id="2")
        assert knowledge._guest_words("price") is None
        # new speech with no caption yet: the old words are not this question's
        heard.mark_activity(who)
        assert knowledge._guest_words("Biogenesis") is None
    finally:
        heard.forget(who)
        heard.bind_session(None)


def _english(folder, aliases):
    """The English questions file beside the (test) manifest."""
    path = project_report.manifest_path().with_name(project_report.FAQ_ALIASES)
    path.write_text(json.dumps({"chapter": "19_faq.md", "aliases": aliases}, ensure_ascii=False),
                    encoding="utf-8")
    return path


def test_english_questions_bring_back_the_approved_thai_answer(report, caplog):
    """Robot 20 Oct: an English question shares no word with a Thai FAQ
    question, so the report's own answer never came back in English. The
    English questions (project_report_faq_en.json) are tied to a Thai question
    by its exact text and carry no answer of their own."""
    folder, write = report
    assert project_report.faq("How long is the central swimming pool?") == []
    path = _english(folder, [
        {"th": "สระว่ายน้ำกลางโครงการยาวเท่าไร", "en": ["How long is the central swimming pool?",
                                                       "Is the Thermal Galaxy pool long?"]},
        {"th": "คำถามที่ไม่มีในรายงานแล้ว", "en": ["What is this?"]},
    ])
    with caplog.at_level("WARNING", logger="condo_voice.project_report"):
        hits = project_report.faq("How long is the central swimming pool?")   # no reset: the file is in the key
    assert len(hits) == 1 and "155" in hits[0]["text"]
    assert hits[0]["text"].endswith("สระกลางยาวประมาณ 155 เมตร ตามแบบ"), "the approved answer, nothing added"
    assert "no longer has" in caplog.text, "an English question whose Thai one is gone is left out, loudly"
    index = project_report._get_index(project_report.load_manifest())
    english = [f["q"] for f in index["faq"] if f.get("alias")]
    assert english == ["How long is the central swimming pool?"], "an overruled term stays out in English too"
    # Thai is judged on the Thai questions alone, as before
    thai = project_report.faq("สระว่ายน้ำกลางโครงการยาวเท่าไร")
    assert thai and thai[0]["text"].startswith("ถาม: สระว่ายน้ำกลางโครงการยาวเท่าไร")
    path.unlink()
    assert project_report.faq("How long is the central swimming pool?") == []


def test_the_english_questions_do_not_move_the_word_weights(report):
    """Rarity is counted over the report's own questions: with the English
    counted in, "developer" turned common and "Who is the developer?" -- an
    FAQ question word for word -- fell under the information bar (20 Oct)."""
    folder, write = report
    before = dict(project_report._get_index(project_report.load_manifest())["faq_idf"])
    _english(folder, [{"th": "สระว่ายน้ำกลางโครงการยาวเท่าไร",
                       "en": ["How long is the pool?", "How long is the main pool?"]}])
    after = project_report._get_index(project_report.load_manifest())
    assert after["faq_idf"] == before and after["faq_own"] == 2


def test_the_banned_concept_caveat_is_cut_from_report_text():
    """Owner 10 Oct: never "แนวคิดเบื้องต้น / อาจเปลี่ยนแปลง". Report passages never
    went through the cut the facts and slides go through, and say it their own
    ways; a tapped level-3 picture came back as "the co-working space is still
    not designed" (21 Oct). Facts beside it stay -- including "concept images"
    inside a fact's own parenthesis."""
    th = ("ชั้น 3 ของอาคาร A มี Social Lounge และ Sky Pool ภาพเรนเดอร์ของชั้นนี้เป็นภาพแนวคิด "
          "ผู้นำเสนอยอมรับว่าส่วน co-working ยังไม่ได้ออกแบบ")
    out = project_report.clean(th, [])
    assert "Social Lounge" in out and "Sky Pool" in out
    assert "เป็นภาพแนวคิด" not in out and "ยังไม่ได้ออกแบบ" not in out
    out = project_report.clean("ภาพของชั้นนี้เป็นภาพไฟนอล 19 ภาพ แต่ยังเป็นภาพเรนเดอร์ที่อาจปรับเปลี่ยนได้", [])
    assert "ภาพไฟนอล" in out and "ปรับเปลี่ยน" not in out
    en = ("Yes: the Biogenesis gym (shown as a double-height hall in the concept images) on the ground floor. "
          "Images are concept illustrations. The co-working design is still not designed per the presenter.")
    out = project_report.clean(en, [])
    assert out.startswith("Yes: the Biogenesis gym (shown as a double-height hall in the concept images)")
    assert "Images are concept" not in out and "not designed" not in out
    # 22 Oct review: a hyphen slipped past "preliminary\s+concept" (intro-004.summary_en)
    from app.tools.retrieval import strip_concept_caveat
    out = strip_concept_caveat("Earth seen from space. The preliminary-concept disclaimer sits bottom right.")
    assert out == "Earth seen from space."
    # 26 Oct robot review: 114 served lines still said it in the report's own words
    # -- a two-word English phrase inside a Thai caption (no piece matched alone),
    # "ภาพประกอบแนวคิด / ไม่ใช่แบบสุดท้าย", and a price word elsewhere in a Thai line
    # (Thai has no full stop, so the line was one "sentence" and stayed whole)
    caption = ("![ภาพประกอบแนวคิด (Preliminary Concept) ไม่ใช่แบบสุดท้าย — ด้านหน้าอาคารหลัก "
               "ผนังกระจกบานใหญ่](img/deck_p14.jpg)")
    out = project_report.clean(caption, [])
    assert "แนวคิด" not in out and "Preliminary" not in out and "สุดท้าย" not in out
    assert "ด้านหน้าอาคารหลัก ผนังกระจกบานใหญ่" in out
    faq = ("**ตอบ:** เป็นภาพประกอบแนวคิด (Preliminary Concept) ที่ผู้พัฒนาระบุว่าอาจเปลี่ยนแปลงได้โดยไม่แจ้งล่วงหน้า "
           "ภาพ facility ชั้น G มีภาพไฟนอล 19 ภาพ ราคาห้องสอบถามทีมขาย")
    out = project_report.clean(faq, [])
    assert "แนวคิด" not in out and "เปลี่ยนแปลง" not in out
    assert "ภาพไฟนอล 19 ภาพ" in out and "ราคาห้องสอบถามทีมขาย" in out
    # the price fact itself is still not the caveat
    assert strip_concept_caveat("ราคาเริ่มต้นอาจมีการเปลี่ยนแปลงตามห้องที่เหลือ") == \
        "ราคาเริ่มต้นอาจมีการเปลี่ยนแปลงตามห้องที่เหลือ"


def test_the_reports_other_wordings_of_the_caveat_are_cut_too():
    """Pre-commit review 26 Oct: built the way the robot serves it, 75 report
    lines still said it -- 15 of them FAQ answers, which 15 English phrasings
    bring back too -- "How many treadmills does Biogenesis have?" came back "...จึงควร
    บอกลูกค้าว่ารายละเอียดจริงอาจเปลี่ยนก่อนส่งมอบ". Plain อาจเปลี่ยน (no แปลง),
    อาจต่าง / จะต่างจากภาพ, ยังพัฒนาต่อได้, the order to tell every customer, the
    presenter's English inside a Thai line; the facts beside them stay."""
    clean = lambda text: project_report.clean(text, [])  # noqa: E731
    out = clean("ตามภาพไฟนอลเห็นลู่วิ่ง 8 เครื่อง ภาพไม่ระบุยี่ห้อหรือรุ่น และเป็นภาพเรนเดอร์ "
                "จึงควรบอกลูกค้าว่ารายละเอียดจริงอาจเปลี่ยนก่อนส่งมอบ ส่วนรายการเครื่องในแปลนไม่ใช้")
    assert out == "ตามภาพไฟนอลเห็นลู่วิ่ง 8 เครื่อง ภาพไม่ระบุยี่ห้อหรือรุ่น ส่วนรายการเครื่องในแปลนไม่ใช้"
    out = clean("มีจักรยาน 3 คัน (ภาพเป็นภาพเรนเดอร์ รายละเอียดจริงอาจต่างได้) ส่วนตารางคลาสยังไม่ประกาศ")
    assert out == "มีจักรยาน 3 คัน ส่วนตารางคลาสยังไม่ประกาศ"
    out = clean("ลาน Playscape พื้นที่ 137 ตารางเมตร เป็นแบบแนวคิดที่ยังพัฒนาต่อได้ และยังไม่สรุปช่วงอายุผู้ใช้")
    assert out == "ลาน Playscape พื้นที่ 137 ตารางเมตร และยังไม่สรุปช่วงอายุผู้ใช้"
    out = clean("| 8 | ห้องซาวน่า | ผู้นำเสนอ: หน้าตาจริงจะต่างจากภาพ | แจ้งลูกค้าทุกครั้ง |")
    assert "ต่างจากภาพ" not in out and "แจ้งลูกค้า" not in out and "ห้องซาวน่า" in out
    out = clean("พันธุ์ไม้ 34 ชื่อ ชนิดไม้ วัสดุ และตำแหน่งอาจเปลี่ยนในแบบถัดไป")
    assert out == "พันธุ์ไม้ 34 ชื่อ"
    # an English quotation inside a Thai line goes whole, like an English sentence
    out = clean('ห้องขนาด 6.75 × 5.00 เมตร ผู้นำเสนอพูดว่า "It will look a little bit different" [01:33:37]')
    assert out == "ห้องขนาด 6.75 × 5.00 เมตร [01:33:37]"
    out = clean('ภาพในเด็คติดป้าย "PRELIMINARY CONCEPT — FOR ILLUSTRATIVE PURPOSES ONLY. SUBJECT TO FURTHER '
                'DEVELOPMENT AND CHANGE WITHOUT PRIOR NOTICE" ส่วนหน้า 2 ไม่มีป้าย')
    assert out == "ภาพในเด็คติดป้าย ส่วนหน้า 2 ไม่มีป้าย"
    # ...a THAI quotation is cut by piece: the report's suggested answer keeps its count
    out = clean('จึงตอบลูกค้าว่า "ตามภาพไฟนอลมีจักรยาน 3 คัน รายละเอียดจริงอาจต่างได้)"')
    assert out == 'จึงตอบลูกค้าว่า "ตามภาพไฟนอลมีจักรยาน 3 คัน'
    # the presenter "admitted that the co-working part..." goes with what he admitted
    out = clean('มี Sky Pool ผู้นำเสนอยอมรับว่าส่วน co-working "ยังไม่ได้ออกแบบ" (still not designed)')
    assert out == "มี Sky Pool"
    # an English parenthesis goes alone: the gym's ceiling in the same sentence stays
    out = clean("A double-height gym (ceiling about 6 m) overlooks the lagoon; the Sky Pool has a "
                "transparent floor (concept, not final).")
    assert out == "A double-height gym (ceiling about 6 m) overlooks the lagoon; the Sky Pool has a transparent floor."
    # the conclusion of a caveat just cut is the caveat too (19_faq:1067)...
    out = clean("ภาพทั้งหมดเป็นภาพเรนเดอร์หรือภาพประกอบแนวคิด จึงไม่ใช่การรับประกัน และสระลึก 1.30 ม.")
    assert out == "และสระลึก 1.30 ม."
    # ...and "does not promise it will be built as pictured" (03_concept:211)
    out = clean("ภาพนี้ไม่ยืนยันว่าอาคารจะสร้างตรงตามภาพ อาคารสูง 30 ชั้น")
    assert out == "อาคารสูง 30 ชั้น"


def test_the_caveat_cut_was_narrowed_to_the_reports_own_subjects():
    """Fix round's review (26 Oct): bare "ชนิด/ตำแหน่ง...อาจเปลี่ยน",
    "แจ้งลูกค้าทุกครั้ง", "ยังพัฒนาต่อ" and "look a little different" would
    have cut facts the owner adds later. Each still cuts the report's wording."""
    from app.tools.retrieval import strip_concept_caveat

    assert strip_concept_caveat("| ภาพ facility | แจ้งลูกค้าทุกครั้ง |") == "| ภาพ facility | |"
    assert strip_concept_caveat("ภาพ concept ต้องแจ้งลูกค้าเสมอ") == ""
    assert strip_concept_caveat("รายละเอียดจริงอาจเปลี่ยนก่อนส่งมอบ ห้องมีระเบียง") == "ห้องมีระเบียง"
    assert strip_concept_caveat("ผังต้นไม้ใหญ่ 62 ต้น ชนิดและตำแหน่งเป็นไปตามแบบแนวคิดและอาจปรับ") == "ผังต้นไม้ใหญ่ 62 ต้น"
    assert strip_concept_caveat("It will look a little bit different from the render.") == ""


@pytest.mark.parametrize("fact", [
    "ทำเลจอมเทียนยังพัฒนาต่อเนื่อง",
    "ทำเลยังพัฒนาต่อได้อีกมาก",
    "ตำแหน่งที่จอดรถอาจเปลี่ยนตามการจัดสรร ห้องละ 1 คัน",
    "ทีมขายจะแจ้งลูกค้าทุกครั้งเมื่อมีโปรโมชั่นใหม่",
    "Units look a little different from floor to floor.",
    "ความเห็นของผู้บริหาร ไม่ใช่การรับประกัน",
    "วิวสวน/ลากูนและวิวด้านหน้าอาจเปลี่ยนหากที่ดินรอบโครงการถูกพัฒนา (บทที่ 4)",
    "ตลาด ดอกเบี้ย และกฎหมายอาจเปลี่ยนในช่วงรอ",
    "ราคาเริ่มต้นอาจมีการเปลี่ยนแปลงตามห้องที่เหลือ",
    "ขนาดตามสัญญาอาจปรับตามการรังวัดจริง",
    "เวลาเปิดทำการมาจากเพจ ณ วันที่ค้น อาจเปลี่ยน",
    "แพ็กเกจเสริม ราคายังไม่ final",
    "นิยามของโครงการ/ฝ่ายขายอาจต่างออกไป ควรยืนยันก่อนใช้ในใบเสนอราคา",
    "ตามภาพแนวคิด Ice Bath มีอ่างแช่น้ำกลม 3 ใบ",
    "ขนาดห้องที่บอกลูกค้าใช้ตามไฟล์ Unit Sold ทุกกรณี",
    "Prices may change with the units that remain.",
])
def test_facts_that_share_the_caveats_verbs_stay(fact):
    """The new patterns are scoped to the pictures/design/details: a view that
    may change if the land around is built on, an investment caution, the
    starting price, the contract size, a clinic's hours -- facts a buyer
    needs, measured over every served line (26 Oct)."""
    from app.tools.retrieval import strip_concept_caveat

    assert strip_concept_caveat(fact) == fact
    if not project_report._MONEY.search(fact):          # a money line never leaves the report at all
        assert project_report.clean(fact, []) == fact


def test_the_reports_faq_answer_reaches_her_without_the_caveat_in_thai_and_english(report):
    """The live case of 26 Oct, end to end: the FAQ answer -- by its Thai
    question and by its English phrasing -- is what a tapped Biogenesis
    picture and an English guest get. The count stays, the caveat goes."""
    folder, write = report
    (folder / "19_faq.md").write_text(
        "**ถาม:** Biogenesis มีลู่วิ่งกี่เครื่อง\n**ตอบ:** ตามภาพไฟนอลเห็นลู่วิ่ง 8 เครื่อง ภาพไม่ระบุยี่ห้อหรือรุ่น "
        "และเป็นภาพเรนเดอร์ จึงควรบอกลูกค้าว่ารายละเอียดจริงอาจเปลี่ยนก่อนส่งมอบ ส่วนรายการเครื่องในแปลนไม่ใช้\n\n"
        "**ถาม:** มีห้องปั่นจักรยานไหม\n**ตอบ:** มี Spinning Class ตามภาพไฟนอลมีจักรยาน 3 คัน "
        "(ภาพเป็นภาพเรนเดอร์ รายละเอียดจริงอาจต่างได้) ส่วนตารางคลาสยังไม่ประกาศ\n", encoding="utf-8")
    write()
    _english(folder, [{"th": "Biogenesis มีลู่วิ่งกี่เครื่อง", "en": ["How many treadmills does Biogenesis have?"]}])
    answers = {}
    for q in ("Biogenesis มีลู่วิ่งกี่เครื่อง", "How many treadmills does Biogenesis have?", "มีห้องปั่นจักรยานไหม"):
        hits = project_report.faq(q)
        assert hits, q
        answers[q] = hits[0]["text"]
        for gone in ("อาจเปลี่ยน", "อาจต่าง", "บอกลูกค้า", "เรนเดอร์"):
            assert gone not in answers[q], (q, gone)
    assert "ลู่วิ่ง 8 เครื่อง" in answers["How many treadmills does Biogenesis have?"]
    assert answers["How many treadmills does Biogenesis have?"].endswith(answers["Biogenesis มีลู่วิ่งกี่เครื่อง"]
                                                                         .split("\n")[1])
    assert "จักรยาน 3 คัน" in answers["มีห้องปั่นจักรยานไหม"]


#: The English questions as tracked in the repo (robot 20 Oct). Every test
#: above writes its own copy; this one is what the robot gets.
TRACKED_FAQ_EN = (project_report.ROOT / "data" / "projects" / "embassy_world" / "sales"
                  / project_report.FAQ_ALIASES)
TRACKED_MANIFEST = TRACKED_FAQ_EN.with_name("project_report_approval.json")


def test_the_tracked_english_faq_file_is_well_formed():
    """Pre-commit review 26 Oct: no test loaded the tracked file. A hand edit
    that leaves a trailing comma, renames "chapter" or reshapes an alias
    turns `_faq_aliases` into [] with one log warning -- every English
    question loses its FAQ match on the robot (the 20 Oct "how high is the
    ceiling" -> sales team failure) and CI stays green. And it carries no
    answer text: the answer is always the approved Thai one."""
    data = json.loads(TRACKED_FAQ_EN.read_text(encoding="utf-8"))
    assert data["chapter"] == "19_faq.md"
    aliases = data["aliases"]
    assert isinstance(aliases, list) and aliases
    deny = [str(t).lower() for t in json.loads(TRACKED_MANIFEST.read_text(encoding="utf-8"))["deny_terms"] if t]
    for item in aliases:
        assert isinstance(item, dict) and set(item) == {"th", "en"}, item
        assert isinstance(item["th"], str) and item["th"].strip(), item
        assert isinstance(item["en"], list) and item["en"], item
        for en in item["en"]:
            assert isinstance(en, str) and en.strip(), item
            assert not any(term in en.lower() for term in deny), (en, "an overruled term in English")


def test_every_tracked_english_question_names_a_served_thai_question():
    """An English question is tied to its Thai question by exact text; one
    whose Thai question the served chapter no longer has is dropped (logged
    only). Checked against the approved chapter itself -- it lives in the
    gitignored personal-docs, so a machine without it cannot check this."""
    import hashlib

    manifest = json.loads(TRACKED_MANIFEST.read_text(encoding="utf-8"))
    data = json.loads(TRACKED_FAQ_EN.read_text(encoding="utf-8"))
    chapter = project_report.ROOT / manifest["folder"] / "19_faq.md"     # the pinned chapter, by name
    if not chapter.is_file():
        pytest.skip(f"{chapter} is not on this machine (gitignored personal-docs): NOT checked that the "
                    "English questions still name served Thai questions of the approved FAQ")
    assert hashlib.sha256(chapter.read_bytes()).hexdigest() == manifest["files"]["19_faq.md"], \
        "the approved FAQ chapter changed since its pin: it is not served, so no English question is"
    served = {p["q"] for p in project_report._faq_pairs(chapter.read_text(encoding="utf-8"), "19_faq.md",
                                                         manifest["deny_terms"])}
    stale = [item["th"] for item in data["aliases"] if item["th"] not in served]
    assert stale == [], f"{len(stale)} English question(s) name a Thai question the served FAQ no longer has"


def test_a_spoken_filler_does_not_cost_the_english_question_its_match(report):
    """Robot 20 Oct: "Okay, how high is the ceiling..." lost a fifth of its
    word cover to "okay". Fillers (_FAQ_FILLERS) carry no question."""
    folder, write = report
    _english(folder, [{"th": "สระว่ายน้ำกลางโครงการยาวเท่าไร", "en": ["How long is the central swimming pool?"]}])
    plain = project_report.faq("How long is the central swimming pool?")
    assert plain
    assert project_report.faq("Okay, um, so how long is the central swimming pool?") == plain


def test_a_travel_word_finds_the_reports_thai_table(report):
    """Robot 20 Oct: the travel chapters head their tables in Thai (ร้านอาหารและ
    คาเฟ่, ท่องเที่ยว); "nearest restaurant" got the convenience stores and
    "tourist attraction" the banks -- both went to the sales team."""
    folder, write = report
    (folder / "07_parking.md").write_text(
        "### ร้านอาหารและคาเฟ่ใกล้โครงการ\n| ร้าน | ระยะ (กม.) |\n|---|---|\n"
        "| ร้านอาหารทะเลริมหาด | 1.2 |\n| คาเฟ่สวนกาแฟ | 0.8 |\n\n"
        "### สถานที่ท่องเที่ยวใกล้โครงการ\n| สถานที่ | ระยะ (กม.) |\n|---|---|\n"
        "| สวนน้ำ | 3.5 |\n| ตลาดน้ำ | 6.0 |\n", encoding="utf-8")
    write()
    food = project_report.search("restaurants and cafes")
    assert food and "ร้านอาหาร" in food[0]["text"]
    sights = project_report.search("tourist attractions")
    assert sights and "ท่องเที่ยว" in sights[0]["text"]


def test_the_owners_humanoid_robots_are_not_contradicted_by_the_social_club_slide():
    """Owner 22 Oct: the Social Lounge has humanoid robots making and serving
    drinks. The approved slide intro-040 still notes "Robots could be or could
    not be humanoid" -- OVERRULED cuts that clause, the room stays."""
    from app.tools.retrieval import OVERRULED

    assert OVERRULED.search("Robots could be or could not be humanoid.")
    assert OVERRULED.search("หุ่นยนต์อาจไม่เป็นรูปทรงมนุษย์")
    assert not OVERRULED.search("a humanoid robot serves drinks in the Social Lounge")
