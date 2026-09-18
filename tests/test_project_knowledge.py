from app.tools import project_knowledge
from app.config import settings


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


def test_every_routed_slide_id_still_exists():
    """The alias file names slide ids by hand, and nothing else checks them.

    A wrong id fails silently and in the worst possible direction: the boost
    matches no slide, the routing quietly does nothing, and search falls back
    to exactly the cross-language guessing this file was added to stop. There
    is no error, no log line, and the only symptom is a slightly worse answer
    — which is invisible unless someone is looking for it.

    Re-importing the deck renumbers ids, so this is a matter of when.
    """
    import json
    from pathlib import Path

    library = {
        image["id"]
        for image in json.loads(
            (Path(settings.slides_dir) / "index.json").read_text(encoding="utf-8")
        )["images"]
    }
    assert library, "the slide library is empty — this test proves nothing"

    for entity in project_knowledge.entities():
        for intent, ids in (entity.get("preferred_slides") or {}).items():
            for slide_id in ids:
                assert slide_id in library, (
                    "%s/%s points at %s, which is not in the deck any more"
                    % (entity["id"], intent, slide_id)
                )


def test_every_routed_slide_can_actually_be_put_on_screen():
    """Pointing at a slide the live deck cannot navigate to is the same bug
    one step later: search finds the right answer and the screen cannot follow
    it. The Canva page map is measured from the real deck, so it is the
    authority on what is reachable."""
    import json
    from pathlib import Path

    pages = json.loads(
        (Path(settings.slides_dir) / "canva_pages.json").read_text(encoding="utf-8")
    )["pages"]
    assert pages, "the canva page map is empty — this test proves nothing"

    for entity in project_knowledge.entities():
        for intent, ids in (entity.get("preferred_slides") or {}).items():
            for slide_id in ids:
                assert slide_id in pages, (
                    "%s/%s routes to %s, which the live deck cannot show"
                    % (entity["id"], intent, slide_id)
                )
