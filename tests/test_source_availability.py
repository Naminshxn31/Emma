"""The CI contract must work without copied gallery assets."""
import hashlib
import json
from pathlib import Path

import pytest

from app import data_sources
from app.data_sources import source_path
from scripts import audit_data_sources as audit


def _source(source_id: str, availability: str, location: str, **extra) -> dict:
    return {
        "id": source_id,
        "project_id": "embassy_world",
        "availability": availability,
        "required_for": ["runtime", "presentation"] if availability != "review_only" else [],
        "label": source_id,
        "domain": "presentation",
        "kind": "json_file" if source_id == "slide_catalog" else "directory",
        "location": location,
        "required": availability != "review_only",
        "tracked": availability == "tracked",
        "authority": "reference",
        "customer_facing": "conditional" if availability != "review_only" else "forbidden",
        "uses": ["test"],
        "not_for": ["content approval"],
        **extra,
    }


def _fixture(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    index = tmp_path / "data/slides/index.json"
    index.parent.mkdir(parents=True)
    index.write_text(json.dumps({"source_id": "slide_catalog", "project_id": "embassy_world",
                                 "images": [{"file": "one.jpg", "source_id": "slide_catalog",
                                             "project_id": "embassy_world"}]}), encoding="utf-8")
    manifest = tmp_path / "data/registry/external_asset_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"schema_version": 1, "sources": {
        "slide_assets": {"project_id": "embassy_world", "files": [{
            "path": "one.jpg", "bytes": 5,
            "sha256": hashlib.sha256(b"slide").hexdigest(),
        }]},
    }}), encoding="utf-8")
    sources = [
        _source("slide_catalog", "tracked", "data/slides/index.json", count_field="images"),
        _source("slide_assets", "external_required", "data/slides",
                materialization="copy a vetted bundle",
                asset_manifest="data/registry/external_asset_manifest.json",
                asset_index_source="slide_catalog", asset_index_array="images",
                asset_index_field="file"),
        _source("old_review", "review_only", "data/review/not-present"),
    ]
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema_version": 1,
                                    "availability_schema_version": 1, "sources": sources}),
                        encoding="utf-8")
    return registry


def test_ci_accepts_manifest_without_private_assets_but_runtime_requires_them(tmp_path, monkeypatch):
    registry = _fixture(tmp_path, monkeypatch)
    ci_issues = audit.audit_registry(registry, mode="ci")
    assert audit.summary(ci_issues)["error"] == 0, ci_issues
    runtime = audit.audit_registry(registry, mode="runtime")
    assert any(issue.source == "slide_assets" and "external asset" in issue.message
               and issue.severity == "error" for issue in runtime)
    assert not any(issue.source == "old_review" for issue in runtime)
    assert any(issue.source == "old_review" and issue.severity == "warning"
               for issue in audit.audit_registry(registry, mode="full"))

    (tmp_path / "data/slides/one.jpg").write_bytes(b"slide")
    assert audit.summary(audit.audit_registry(registry, mode="runtime"))["error"] == 0
    (tmp_path / "data/slides/one.jpg").write_bytes(b"other")
    assert any("checksum mismatch" in issue.message
               for issue in audit.audit_registry(registry, mode="runtime"))


def test_ci_rejects_manifest_that_no_longer_matches_catalogue(tmp_path, monkeypatch):
    registry = _fixture(tmp_path, monkeypatch)
    manifest_path = tmp_path / "data/registry/external_asset_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sources"]["slide_assets"]["files"][0]["path"] = "wrong.jpg"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert any("manifest files do not match" in issue.message
               for issue in audit.audit_registry(registry, mode="ci"))


def test_ci_rejects_missing_external_manifest(tmp_path, monkeypatch):
    registry = _fixture(tmp_path, monkeypatch)
    (tmp_path / "data/registry/external_asset_manifest.json").unlink()
    assert any(issue.source == "slide_assets" and "invalid asset manifest" in issue.message
               for issue in audit.audit_registry(registry, mode="ci"))


def test_ci_rejects_unpinned_external_assets(tmp_path, monkeypatch):
    registry = _fixture(tmp_path, monkeypatch)
    data = json.loads(registry.read_text(encoding="utf-8"))
    data["sources"][1].pop("asset_manifest")
    registry.write_text(json.dumps(data), encoding="utf-8")
    assert any("integrity exemption" in issue.message
               for issue in audit.audit_registry(registry, mode="ci"))


def test_ci_rejects_missing_tracked_index_even_if_manifest_exists(tmp_path, monkeypatch):
    registry = _fixture(tmp_path, monkeypatch)
    (tmp_path / "data/slides/index.json").unlink()
    assert any(issue.source == "slide_catalog" and issue.severity == "error"
               and "missing tracked" in issue.message
               for issue in audit.audit_registry(registry, mode="ci"))


def test_absolute_windows_paths_and_unscoped_active_sources_fail_ci(tmp_path, monkeypatch):
    registry = _fixture(tmp_path, monkeypatch)
    data = json.loads(registry.read_text(encoding="utf-8"))
    data["sources"][0]["location"] = "C:/private/slides/index.json"
    data["sources"][0].pop("project_id")
    registry.write_text(json.dumps(data), encoding="utf-8")
    messages = [issue.message for issue in audit.audit_registry(registry, mode="ci")]
    assert any("repository-relative" in message for message in messages)
    assert any("needs project_id" in message for message in messages)

    monkeypatch.setattr(data_sources, "_sources", lambda: {
        "bad": {"project_id": "embassy_world", "location": "C:/private/facts.json"},
    })
    with pytest.raises(ValueError):
        source_path("bad", "embassy_world")


def test_all_runtime_project_sources_resolve_without_model_paths():
    registry = json.loads(audit.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    for source in registry["sources"]:
        if source["customer_facing"] in {"allowed", "conditional"}:
            assert source.get("project_id") or source.get("scope_exception") in audit.SCOPE_EXCEPTIONS
        active = set(source["required_for"]) & {"runtime", "presentation"}
        if not active or source["customer_facing"] == "forbidden":
            continue
        assert source.get("project_id"), source["id"]
        if source["availability"] == "tracked":
            assert source_path(source["id"], source["project_id"]).exists()
        with pytest.raises(ValueError):
            source_path(source["id"], "embassy_life")
