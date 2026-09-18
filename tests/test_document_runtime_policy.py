"""M0.3b: a document is all-or-nothing until it has source-level approval."""
from __future__ import annotations

import json

from app.config import settings
from app.tools import documents, mydocs


APPROVAL = {
    "source_id": "printable_documents", "project_id": "embassy_world",
    "approval_status": "approved", "approved_by": "test_reviewer",
    "approved_at": "2026-01-01T00:00:00+07:00",
    "effective_at": "2026-01-01T00:00:00+07:00",
    "disclosure_scope": "customer", "content_state": "existing",
    "approval_unit": "entire_document",
}
BLOCKED_TEXT = "DRAFT_DOCUMENT_SENTINEL"


def test_mixed_status_catalogue_filters_before_match_list_and_print(monkeypatch, tmp_path):
    (tmp_path / "approved.pdf").write_bytes(b"%PDF-1.4 approved fixture")
    (tmp_path / "draft.pdf").write_bytes(b"%PDF-1.4 draft fixture")
    path = tmp_path / "catalogue.json"
    approved = {**APPROVAL, "name": "เอกสารอนุมัติ", "file": "approved.pdf",
                "about": "ข้อมูลที่อนุมัติ", "hidden": BLOCKED_TEXT}
    draft = {"name": BLOCKED_TEXT, "file": "draft.pdf",
             "about": BLOCKED_TEXT, "aliases": [BLOCKED_TEXT],
             "source_id": "printable_documents", "project_id": settings.project_id,
             "approval_status": "draft"}
    path.write_text(json.dumps({"documents": [approved, draft]}), encoding="utf-8")
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    monkeypatch.setattr(settings, "assistant_profile", "condo")
    printed = []
    monkeypatch.setattr(documents, "send_to_printer",
                        lambda file, copies: (printed.append(file), (True, "ok"))[1])

    listed = documents.list_documents()
    serialized_provider_input = json.dumps(listed, ensure_ascii=False)
    assert [item["name"] for item in listed["documents"]] == ["เอกสารอนุมัติ"]
    assert BLOCKED_TEXT not in serialized_provider_input
    assert documents.find(BLOCKED_TEXT) is None
    assert documents._print_document(BLOCKED_TEXT)["ok"] is False
    assert printed == []

    approved["approval_status"] = "draft"
    path.write_text(json.dumps({"documents": [approved, draft]}), encoding="utf-8")
    assert documents.list_documents()["documents"] == []
    assert documents._print_document("เอกสารอนุมัติ")["ok"] is False


def test_foreign_document_and_old_catalogue_cache_do_not_cross_project(monkeypatch, tmp_path):
    (tmp_path / "approved.pdf").write_bytes(b"%PDF-1.4 fixture")
    path = tmp_path / "catalogue.json"
    entry = {**APPROVAL, "name": "เอกสารอนุมัติ", "file": "approved.pdf"}
    path.write_text(json.dumps({"documents": [entry]}), encoding="utf-8")
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    monkeypatch.setattr(settings, "assistant_profile", "condo")
    assert len(documents.load_catalogue()) == 1

    monkeypatch.setattr(settings, "project_id", "embassy_life")
    assert documents.load_catalogue() == []
    assert documents.list_documents()["documents"] == []


def test_mixed_or_chunk_only_approval_does_not_release_whole_pdf(monkeypatch, tmp_path):
    (tmp_path / "mixed.pdf").write_bytes(b"%PDF-1.4 mixed fixture")
    path = tmp_path / "catalogue.json"
    entry = {**APPROVAL, "file": "mixed.pdf", "name": "เอกสารผสม",
             "about": BLOCKED_TEXT, "approval_unit": "chunk"}
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    monkeypatch.setattr(settings, "assistant_profile", "condo")
    path.write_text(json.dumps({"documents": [entry]}), encoding="utf-8")
    assert documents.list_documents()["documents"] == []
    entry["approval_unit"] = "entire_document"
    entry["mixed_status"] = True
    path.write_text(json.dumps({"documents": [entry]}), encoding="utf-8")
    assert documents.list_documents()["documents"] == []


def test_customer_rag_blocks_before_read_or_rerank(monkeypatch):
    monkeypatch.setattr(settings, "assistant_profile", "condo")
    monkeypatch.setattr(mydocs, "_get_index", lambda:
                        (_ for _ in ()).throw(AssertionError("draft corpus was read")))
    output = mydocs.search_my_documents(BLOCKED_TEXT)
    serialized_provider_input = json.dumps(output, ensure_ascii=False)
    assert output["results"] == []
    assert BLOCKED_TEXT not in serialized_provider_input
