"""M0.3b: display eligibility never grants customer-facing slide copy."""
from __future__ import annotations

import json
from types import SimpleNamespace

from app.config import settings
from app.presentation_policy import authorize_slide
from app.tools import canva_display, knowledge, slide_search, slides


APPROVAL = {
    "approval_status": "approved", "approved_by": "test_reviewer",
    "approved_at": "2026-01-01T00:00:00+07:00",
    "effective_at": "2026-01-01T00:00:00+07:00",
    "disclosure_scope": "customer", "content_state": "preliminary_concept",
}
BLOCKED_TEXT = "DRAFT_SENTINEL_DO_NOT_SEND"


def test_displayable_draft_never_releases_caption_notes_or_raw_metadata():
    slide = {**slides.load_slides()[0], "title_th": BLOCKED_TEXT,
             "summary_th": BLOCKED_TEXT, "detail_th": BLOCKED_TEXT,
             "transcript_th": BLOCKED_TEXT, "script_th": BLOCKED_TEXT,
             "ask_th": BLOCKED_TEXT, "hidden": BLOCKED_TEXT}
    shown = slides._public(slide)
    serialized_provider_input = json.dumps(shown, ensure_ascii=False)
    assert shown["capabilities"] == {"display": True, "model_text": False}
    assert shown["url"].startswith("/slides/")
    assert BLOCKED_TEXT not in serialized_provider_input
    assert not any(key in shown for key in ("type", "summary_th", "script", "ask", "hidden"))


def test_denied_policy_trace_does_not_echo_untrusted_metadata():
    slide = {**slides.load_slides()[0], "content_state": BLOCKED_TEXT,
             "title_th": BLOCKED_TEXT, "description": BLOCKED_TEXT}
    shown = slides._public(slide)
    entry = knowledge._entry(slide)
    assert BLOCKED_TEXT not in json.dumps(shown, ensure_ascii=False)
    assert BLOCKED_TEXT not in json.dumps(entry, ensure_ascii=False)
    assert shown["policy_trace"]["content_state"] == ""


def test_foreign_or_unregistered_slide_is_blocked_before_display_or_text(monkeypatch):
    original = slides.load_slides()[0]
    slide = {**original, **APPROVAL, "title_th": BLOCKED_TEXT,
             "project_id": "embassy_life"}
    blocked = slides._public(slide)
    assert "url" not in blocked and BLOCKED_TEXT not in json.dumps(blocked)
    assert blocked["capabilities"] == {"display": False, "model_text": False}

    monkeypatch.setattr(slides, "_slides", [slide])
    monkeypatch.setattr(slides, "search_slides", lambda query: [
        SimpleNamespace(slide=slide, found=True, confident=True)])
    output = slides.show_slide("anything")
    serialized_provider_input = json.dumps(output, ensure_ascii=False)
    assert output["ok"] is False and BLOCKED_TEXT not in serialized_provider_input

    fake = {**original, **APPROVAL, "file": "unregistered.jpg"}
    manifest = {"sources": {"slide_assets": {"project_id": settings.project_id,
              "files": [{"path": original["file"], "sha256": "0" * 64}]}}}
    result = authorize_slide(fake, project_id=settings.project_id,
                             asset_manifest=manifest)
    assert not result.display and result.asset == {}


def test_rag_filters_draft_before_index_and_reranker(monkeypatch):
    original = slides.load_slides()[0]
    approved = {**original, **APPROVAL, "id": "approved-test",
                "title_th": "ทดสอบข้อมูล", "summary_th": "คำตอบที่อนุมัติ"}
    draft = {**original, "id": "draft-test", "title_th": BLOCKED_TEXT,
             "summary_th": BLOCKED_TEXT, "detail_th": BLOCKED_TEXT}
    monkeypatch.setattr(slides, "load_slides", lambda: [approved, draft])
    monkeypatch.setattr(knowledge, "load_slides", lambda: [approved, draft])
    monkeypatch.setattr(settings, "search_semantic", False)
    monkeypatch.setattr(settings, "search_reranker_model", "test-reranker")
    slide_search.reset()
    seen = []

    def rerank(query, indices, indexed):
        seen.extend(indexed)
        return indices

    monkeypatch.setattr(slide_search, "_rerank", rerank)
    result = knowledge.search_condo_info("ทดสอบข้อมูล")
    serialized_provider_input = json.dumps(result, ensure_ascii=False)
    assert result["found"] is True
    assert seen and seen == [approved]
    assert BLOCKED_TEXT not in serialized_provider_input

    approved["approval_status"] = "draft"
    revoked = knowledge.search_condo_info("ทดสอบข้อมูล")
    assert revoked["results"] == [] and not revoked["found"]
    assert BLOCKED_TEXT not in json.dumps(revoked, ensure_ascii=False)
    slide_search.reset()


def test_image_search_never_embeds_or_reranks_draft_labels(monkeypatch):
    slide_search.reset()
    monkeypatch.setattr(settings, "search_semantic", True)
    monkeypatch.setattr(settings, "search_reranker_model", "external-model")
    monkeypatch.setattr(slide_search.SlideSearch, "_build_semantic", lambda *args:
                        (_ for _ in ()).throw(AssertionError("draft entered embedding")))
    monkeypatch.setattr(slide_search, "_rerank", lambda *args:
                        (_ for _ in ()).throw(AssertionError("draft entered reranker")))
    assert slides.search_slides("ผังห้อง")
    slide_search.reset()


def test_slide_catalog_cache_rechecks_project_and_revoked_copy(monkeypatch, tmp_path):
    original = slides.load_slides()[0]
    path = tmp_path / "index.json"
    approved = {**original, **APPROVAL, "title_th": "อนุมัติสำหรับทดสอบ"}
    path.write_text(json.dumps({"source_id": "slide_catalog",
                                "project_id": settings.project_id,
                                "images": [approved]}), encoding="utf-8")
    monkeypatch.setattr(settings, "slides_dir", str(tmp_path))
    slides.reload_slides()
    assert slides._public(slides.load_slides()[0])["capabilities"]["model_text"]

    approved["approval_status"] = "draft"
    approved["title_th"] = BLOCKED_TEXT
    path.write_text(json.dumps({"source_id": "slide_catalog",
                                "project_id": settings.project_id,
                                "images": [approved]}), encoding="utf-8")
    reloaded = slides.load_slides()[0]
    assert reloaded["approval_status"] == "draft"
    assert BLOCKED_TEXT not in json.dumps(slides._public(reloaded), ensure_ascii=False)

    monkeypatch.setattr(settings, "project_id", "embassy_life")
    assert slides.load_slides() == []
    slides.reload_slides()


def test_current_slide_cache_reauthorizes_before_provider_reuse(monkeypatch):
    source = {**slides.load_slides()[0], **APPROVAL,
              "title_th": "อนุมัติสำหรับทดสอบ"}
    monkeypatch.setattr(slides, "load_slides", lambda: [source])
    slides.reset_state()
    assert slides.show_current(source)["capabilities"]["model_text"]

    source["approval_status"] = "draft"
    source["title_th"] = BLOCKED_TEXT
    cached = slides.current_slide()
    assert cached["capabilities"] == {"display": True, "model_text": False}
    assert BLOCKED_TEXT not in json.dumps(cached, ensure_ascii=False)

    monkeypatch.setattr(settings, "project_id", "embassy_life")
    assert slides.current_slide() is None
    slides.reset_state()


def test_canva_mapping_is_project_and_design_scoped_across_cache(monkeypatch, tmp_path):
    import shutil
    from pathlib import Path

    shutil.copy(Path(settings.slides_dir) / "index.json", tmp_path / "index.json")
    first = slides.load_slides()[0]
    assert first["id"].startswith("picture-")
    # The selected ID must be a deck page, not a picture-only slide.
    page_id = next(s["id"] for s in slides.load_slides() if s["id"].startswith("ew-"))
    path = tmp_path / "canva_pages.json"
    source = {"source_id": "canva_page_mapping", "project_id": settings.project_id,
              "deck_url": "https://canva.test/design/testdeck/view",
              "total": 1, "pages": {page_id: 1}, "title": BLOCKED_TEXT,
              "description": BLOCKED_TEXT}
    path.write_text(json.dumps(source), encoding="utf-8")
    monkeypatch.setattr(settings, "slides_dir", str(tmp_path))
    monkeypatch.setattr(settings, "canva_url", source["deck_url"])
    canva_display.load_page_map(force=True)
    assert canva_display._page_number(page_id) == 1
    assert BLOCKED_TEXT not in json.dumps(canva_display.load_page_map())

    monkeypatch.setattr(settings, "canva_url", "https://canva.test/design/other/view")
    assert canva_display._page_number(page_id) is None
    monkeypatch.setattr(settings, "canva_url", source["deck_url"])
    source["project_id"] = "embassy_life"
    path.write_text(json.dumps(source), encoding="utf-8")
    assert canva_display._page_number(page_id) is None


def test_manual_canva_page_click_does_not_announce_draft_copy(monkeypatch):
    catalog = slides.load_slides()
    target = next(s for s in catalog if s["id"] == "ew-001")
    draft = {**target, "title_th": BLOCKED_TEXT, "summary_th": BLOCKED_TEXT,
             "script_th": BLOCKED_TEXT, "ask_th": BLOCKED_TEXT}
    monkeypatch.setattr(slides, "_slides", [draft] + [s for s in catalog if s is not target])
    slides.reset_state()
    canva_display.load_page_map(force=True)
    assert slides.follow_external_page(1) is None
    shown = slides.current_slide()
    assert shown["capabilities"] == {"display": True, "model_text": False}
    assert BLOCKED_TEXT not in json.dumps(shown, ensure_ascii=False)
    slides.reset_state()
