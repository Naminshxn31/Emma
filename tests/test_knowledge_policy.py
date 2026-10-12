"""M0.2 disclosure contract: authorization and construction state are separate."""
from datetime import datetime, timedelta, timezone
import json

from app.config import ACTIVE_PROJECT_ID
from app.knowledge_policy import (carries_claim, evaluate_claim, evaluate_live_inventory,
                                  state_instruction)


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


def test_only_content_free_values_count_as_blank():
    for blank in (None, "", "   ", "-", " — ", "...", "–/–",
                  # a hand-edited file: str() would print these as "False"/"['x']"
                  False, True, [], {}, ["x"], {"a": 1}):
        assert not carries_claim(blank), repr(blank)
    # Any letter or digit, in any script, is a claim; and a sentence that is
    # really a note-to-self is not guessed at (the console warns instead).
    for value in ("ก", "7", "- เปิด 10 โมง", "Sky Pool", "ดูจาก DB", 0, 7, 0.5):
        assert carries_claim(value), value


def test_approved_concept_preserves_future_wording():
    decision = evaluate_claim(claim(content_state="developing"), ACTIVE_PROJECT_ID, now=NOW)
    assert decision.allowed and decision.content_state == "developing"
    assert "กำลังพัฒนา" in state_instruction(decision.content_state)
    assert "สร้างเสร็จ" in state_instruction(decision.content_state)


def test_a_concept_claim_is_said_plainly_never_caveated():
    """Owner 2026-10-10: "อย่าบอกว่าเป็นเพียงแนวคิดอาจจะมีการเปลี่ยนแปลง ห้ามพูด
    แบบนี้เป็นอันขาด". The instruction quotes both banned phrases (a bare "say
    it plainly" left the model hedging on the preliminary_concept label) and
    no longer orders either; *when* it opens still goes to the sales team,
    because no approved fact says."""
    text = state_instruction("preliminary_concept")
    assert 'ห้ามพูดว่า "เป็นแนวคิดเบื้องต้น"' in text
    assert '"อาจมีการเปลี่ยนแปลง"' in text
    assert "ต้องบอกว่าเป็นแนวคิด" not in text
    assert "ทีมขายยืนยัน" in text
    # Not banned (owner 2026-10-11): "ให้ชม" can mean showing a picture.
    assert "ให้ชม" not in text and "ให้บริการ" not in text


def test_the_concept_caveat_is_cut_from_project_text_not_price():
    """The approved deck prints the caveat itself (cover, theme page,
    basement plan) and search returns those summaries as `detail`. It is cut
    by clause before a model reads it; a price clause stays, and so does the
    common-area dimension rule that shares the door."""
    from app.tools.retrieval import sanitize_common_area_dimensions, strip_concept_caveat

    cover = ("ใต้โลโก้เป็นชื่อโครงการ EMBASSY WORLD ตัวอักษรโลหะ มุมขวาล่างระบุว่าเป็นแนวคิดเบื้องต้น "
             "เพื่อการนำเสนอเท่านั้น และอาจเปลี่ยนแปลงได้โดยไม่ต้องแจ้งล่วงหน้า")
    assert strip_concept_caveat(cover) == "ใต้โลโก้เป็นชื่อโครงการ EMBASSY WORLD ตัวอักษรโลหะ"
    english = ("EMBASSY WORLD appears in metallic type below. A disclaimer in the lower right marks "
               "the whole deck as a preliminary concept for illustrative purposes only.")
    assert strip_concept_caveat(english) == "EMBASSY WORLD appears in metallic type below."
    price = "1 ห้องนอนเริ่มต้น 2.79 ล้านบาท (ราคาอาจเปลี่ยนแปลงตามห้องที่เหลือ)"
    assert strip_concept_caveat(price) == price
    plain = "สระ Sky Pool อยู่ชั้น 3 อาคาร A"
    assert strip_concept_caveat(plain) == plain
    assert sanitize_common_area_dimensions("ลากูนยาว 155 เมตร ป้ายท้ายหน้าระบุว่าเป็นแนวคิดเบื้องต้น") == "ลากูน"


#: Text only this test puts into the files, so the assertions never depend on
#: what the owner's facts happen to say on the day it runs.
MARK = "ข้อความทดสอบที่ห้ามถึงลูกค้า"


def _real_file_with_mark(tmp_path, source_id, approved):
    """The project's real file plus a marker, with the permission set here."""
    from app.data_sources import source_path

    data = json.loads(source_path(source_id, ACTIVE_PROJECT_ID).read_text(encoding="utf-8"))
    if source_id == "project_facts":
        data["facts"] = list(data.get("facts") or []) + [{"label": "ทดสอบ", "value": MARK}]
    else:
        data["scope_rule"] = str(data.get("scope_rule") or "") + " " + MARK
    # expires_at is pinned too: an expiry the owner sets on the real file
    # must not be what decides this pair.
    permission = {key: claim()[key] for key in (
        "approval_status", "approved_by", "approved_at", "effective_at",
        "expires_at", "disclosure_scope", "content_state")} if approved else dict(
        approval_status="draft", disclosure_scope="internal",
        approved_by="", approved_at=None, effective_at=None, expires_at=None)
    data.update(permission)
    path = tmp_path / f"{source_id}-{'approved' if approved else 'draft'}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _point_prompts_at(monkeypatch, facts, context):
    import app.prompts as prompts

    real = prompts.source_path
    monkeypatch.setattr(prompts, "CONDO_FACTS_FILE", str(facts))
    monkeypatch.setattr(prompts, "source_path", lambda source_id, project_id: (
        context if source_id == "project_sales_context" else real(source_id, project_id)))
    return prompts


def test_draft_facts_and_sales_context_do_not_enter_prompt(tmp_path, monkeypatch):
    """The mechanism, not the owner's data. This test used to read the live
    files and assert they were still draft: true when written, red the day
    the owner approved his facts in /data-console (2026-09-28) with no code
    wrong. Approving is his decision; what must hold on every day is that a
    draft source never reaches the prompt."""
    prompts = _point_prompts_at(
        monkeypatch,
        _real_file_with_mark(tmp_path, "project_facts", approved=False),
        _real_file_with_mark(tmp_path, "project_sales_context", approved=False))

    assert MARK not in prompts.load_facts()
    assert MARK not in prompts.load_sales_context()["scope_rule"]
    assert MARK not in prompts.build_instructions("Embassy World")


def test_the_same_files_approved_do_reach_the_prompt(tmp_path, monkeypatch):
    """Proof the test above is not vacuous: identical files, permission
    granted, and the marker arrives. The approval is the only difference."""
    prompts = _point_prompts_at(
        monkeypatch,
        _real_file_with_mark(tmp_path, "project_facts", approved=True),
        _real_file_with_mark(tmp_path, "project_sales_context", approved=True))

    assert MARK in prompts.load_facts()
    assert MARK in prompts.load_sales_context()["scope_rule"]
    assert MARK in prompts.build_instructions("Embassy World")


def test_approved_facts_still_lose_their_common_area_dimensions(tmp_path):
    """The other half of the old test, which must survive approval: a
    lagoon size is stripped even from signed-off facts (retrieval's
    sanitize_common_area_dimensions), because the number is not confirmed."""
    from app.prompts import load_facts

    source = claim(source_id="project_facts", facts=[
        {"label": "จุดเด่น", "value": "ลากูน 155ม., Water Lagoon 155 เมตร, Biogenesis"}])
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    rendered = load_facts(str(path))
    assert "Biogenesis" in rendered
    assert "155" not in rendered


def test_individual_claim_cannot_override_draft_source(tmp_path):
    from app.prompts import load_facts

    source = claim(source_id="project_facts", approval_status="draft", facts=[dict(
        claim(), label="ทดสอบ", value="ห้ามแสดง")])
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    assert "ห้ามแสดง" not in load_facts(str(path))


def test_approved_developing_fact_is_qualified_in_prompt(tmp_path):
    from app.prompts import load_facts

    source = claim(source_id="project_facts", content_state="developing", facts=[{"label": "ตัวอย่าง", "value": "พื้นที่ทดสอบ"}])
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    rendered = load_facts(str(path))
    assert "พื้นที่ทดสอบ" in rendered and "กำลังพัฒนา" in rendered


def test_slide_asset_can_be_displayed_without_releasing_draft_copy():
    from app.tools.slides import _public, load_slides
    from app.tools.knowledge import _entry

    # Approval stripped: the real catalogue was approved on 2026-10-06, and a
    # copy of an approved record is approved — this test then read the
    # owner's sign-off as a leak. The state under test is pinned here.
    record = {key: value for key, value in load_slides()[0].items()
              if key not in ("approval_status", "approved_by", "approved_at",
                             "effective_at", "expires_at", "disclosure_scope",
                             "content_state")}
    slide = {**record, "title_th": "คำอ้างที่ยังไม่อนุมัติ",
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
    # The disclosed-text index reads slides.load_slides itself. Without this
    # the test searched the real catalogue and passed only while that was a
    # draft — it went red the day the owner approved it (2026-10-06).
    monkeypatch.setattr(slides, "load_slides", lambda: [slide])
    monkeypatch.setattr(slides, "search_slides", lambda query: [
        SimpleNamespace(slide=slide, found=True, confident=True)])
    from app.tools import slide_search
    slide_search.reset()
    result = knowledge.search_condo_info("หัวข้อ draft")
    slide_search.reset()
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
