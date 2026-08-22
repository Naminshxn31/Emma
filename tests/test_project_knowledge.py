from app.tools import project_knowledge


def test_project_vocabulary_is_data_driven_and_multilingual():
    assert "SKY POOL" in project_knowledge.expand("游泳池在哪里")
    assert "BIOGENESIS" in project_knowledge.expand("где тренажерный зал")


def test_slide_intent_distinguishes_location_from_visual_request():
    assert project_knowledge.intent("สระว่ายน้ำอยู่ที่ไหน") == "locate"
    assert project_knowledge.intent("ขอดูสระว่ายน้ำหน่อย") == "show"
    assert project_knowledge.intent("สระว่ายน้ำมีอะไรบ้าง") == "explain"
    assert project_knowledge.preferred_slide_ids("สระว่ายน้ำอยู่ที่ไหน")[0] == "ew-054"
    assert project_knowledge.preferred_slide_ids("ขอดูสระว่ายน้ำหน่อย")[0] == "ew-055"


def test_absent_facility_is_an_explicit_fact():
    assert project_knowledge.unavailable("มีสนามกอล์ฟไหม")
    assert not project_knowledge.unavailable("มีฟิตเนสไหม")
