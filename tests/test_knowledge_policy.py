"""M0.2 disclosure contract: authorization and construction state are separate."""
from datetime import datetime, timedelta, timezone
import json

from app.config import ACTIVE_PROJECT_ID
from app.knowledge_policy import evaluate_claim, evaluate_live_inventory, state_instruction


NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)


def claim(**updates):
    data = {
        "source_id": "test.claim", "project_id": ACTIVE_PROJECT_ID,
        "approval_status": "approved", "approved_by": "test_reviewer",
        "approved_at": "2026-09-17T00:00:00+07:00",
        "effective_at": "2026-09-18T00:00:00+07:00",
        "expires_at": None, "disclosure_scope": "customer",
        "content_state": "existing",
    }
    data.update(updates)
    return data


def test_approved_passes_and_trace_identifies_source():
    decision = evaluate_claim(claim(), ACTIVE_PROJECT_ID, now=NOW)
    assert decision.allowed
    assert decision.trace()["source_id"] == "test.claim"
    assert decision.trace()["reason"] == "approved_customer_claim"


def test_draft_missing_expired_wrong_project_and_private_are_blocked():
    cases = [
        (claim(approval_status="draft"), "not_approved"),
        (claim(approved_at=None), "missing_metadata"),
        (claim(expires_at="2026-09-18T00:00:00+07:00"), "expired"),
        (claim(project_id="embassy_life"), "wrong_project"),
        (claim(disclosure_scope="internal"), "not_customer_visible"),
    ]
    for source, reason in cases:
        decision = evaluate_claim(source, ACTIVE_PROJECT_ID, now=NOW)
        assert not decision.allowed and decision.reason == reason


def test_approved_concept_preserves_future_wording():
    decision = evaluate_claim(claim(content_state="developing"), ACTIVE_PROJECT_ID, now=NOW)
    assert decision.allowed and decision.content_state == "developing"
    assert "กำลังพัฒนา" in state_instruction(decision.content_state)
    assert "สร้างเสร็จ" in state_instruction(decision.content_state)


def test_existing_draft_facts_and_sales_context_do_not_enter_prompt():
    from app.prompts import load_facts, load_sales_context, build_instructions

    assert "Empire Group" not in load_facts()
    assert "Biogenesis" not in load_sales_context()["scope_rule"]
    prompt = build_instructions("Embassy World")
    assert "Biogenesis" not in prompt
    assert "155ม." not in prompt


def test_individual_claim_cannot_override_draft_source(tmp_path):
    from app.prompts import load_facts

    source = claim(approval_status="draft", facts=[dict(
        claim(), label="ทดสอบ", value="ห้ามแสดง")])
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    assert "ห้ามแสดง" not in load_facts(str(path))


def test_approved_developing_fact_is_qualified_in_prompt(tmp_path):
    from app.prompts import load_facts

    source = claim(content_state="developing", facts=[{"label": "ตัวอย่าง", "value": "พื้นที่ทดสอบ"}])
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    rendered = load_facts(str(path))
    assert "พื้นที่ทดสอบ" in rendered and "กำลังพัฒนา" in rendered


def test_slide_asset_can_be_displayed_without_releasing_draft_copy():
    from app.tools.slides import _public, load_slides
    from app.tools.knowledge import _entry

    slide = {**load_slides()[0], "title_th": "คำอ้างที่ยังไม่อนุมัติ",
             "summary_th": "รายละเอียดที่ยังไม่อนุมัติ", "script_th": "บท draft"}
    shown = _public(slide)
    assert shown["url"] == "/slides/" + slide["file"]
    assert not shown["policy_trace"]["allowed"]
    assert shown["capabilities"] == {"display": True, "model_text": False}
    assert not any(word in str(shown) for word in ("คำอ้างที่ยังไม่อนุมัติ", "รายละเอียดที่ยังไม่อนุมัติ", "บท draft"))
    assert _entry(slide) == {"policy_trace": shown["policy_trace"]}


def test_customer_search_does_not_turn_draft_slides_into_answers(monkeypatch):
    from types import SimpleNamespace
    from app.tools import knowledge, slides

    slide = {"id": "test", "file": "test.jpg", "source_id": "slide_catalog",
             "project_id": ACTIVE_PROJECT_ID, "title_th": "หัวข้อ draft",
             "summary_th": "คำอ้าง draft"}
    monkeypatch.setattr(knowledge, "load_slides", lambda: [slide])
    monkeypatch.setattr(slides, "search_slides", lambda query: [
        SimpleNamespace(slide=slide, found=True, confident=True)])
    result = knowledge.search_condo_info("หัวข้อ draft")
    assert result["found"] is False and result["results"] == []
    assert result["policy_trace"][0]["source_id"] == "slide_catalog"
    assert "คำอ้าง draft" not in str(result)


def test_customer_document_search_denies_unreviewed_text(monkeypatch):
    from app.tools import mydocs

    monkeypatch.setattr(mydocs.settings, "assistant_profile", "condo")
    result = mydocs.search_my_documents("Embassy World")
    assert result["found"] is False and result["results"] == []
    assert result["policy_trace"][0]["reason"] == "missing_metadata"


def test_live_inventory_uses_freshness_and_disclosure_not_human_approval():
    base = dict(source_id="live_unit_inventory", project_id=ACTIVE_PROJECT_ID,
                expected_project_id=ACTIVE_PROJECT_ID, fetched_at=NOW - timedelta(seconds=2),
                max_age_seconds=10, customer_visible=True, now=NOW)
    assert evaluate_live_inventory(**base).allowed
    assert evaluate_live_inventory(**{**base, "fetched_at": NOW - timedelta(seconds=11)}).reason == "stale_inventory"
    assert evaluate_live_inventory(**{**base, "customer_visible": False}).reason == "not_customer_visible"
    assert evaluate_live_inventory(**{**base, "project_id": "embassy_life"}).reason == "untrusted_or_wrong_project"
