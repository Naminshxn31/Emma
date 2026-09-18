"""M0.3 plan route: a scoped, verified asset and safe live overlay are required."""
from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from app.config import settings
from app.runtime_policy import RuntimeResult, prepare_plan_asset
from app.tools import units


IMAGE = b"synthetic test webp bytes"
DIGEST = hashlib.sha256(IMAGE).hexdigest()
SOURCE = {"source_id": "floor_plan_assets", "project_id": "embassy_world"}
ENTRY = {"floor": 1, "status": "review-ready", "display_path": "/fpg1.webp",
         "display_derivative_sha256": DIGEST}
ROW = {"unit_no": "A-101", "status": "available", "pos_x": 10, "pos_y": 20,
       "width": 4, "height": 3, "poly": None,
       "floors": {"floor_number": 1, "buildings": {
           "code": "A", "projects": {"slug": "embassy-world"}}}}


@pytest.fixture(autouse=True)
def fresh_units():
    units.reset()
    yield
    units.reset()


def test_plan_router_denies_foreign_missing_metadata_bad_type_and_checksum():
    def decide(source=SOURCE, entry=ENTRY, digest=DIGEST, media_type="image/webp"):
        return prepare_plan_asset(source, entry, expected_project_id="embassy_world",
                                  observed_sha256=digest, content_type=media_type)

    approved = decide()
    assert approved.allowed and approved.trace()["policy"] == "plan_asset_runtime_v1"
    assert approved.payload == {"path": "/fpg1.webp", "floor": 1}
    for result in (
        decide({**SOURCE, "project_id": "embassy_life"}),
        decide({**SOURCE, "source_id": "other"}),
        decide(entry={**ENTRY, "display_derivative_sha256": None}),
        decide(entry={**ENTRY, "display_path": "/other.pdf"}),
        decide(entry={**ENTRY, "display_path": "//host/plan.webp"}),
        decide(entry={**ENTRY, "status": "draft"}),
        decide(digest="0" * 64),
        decide(media_type="text/html"),
    ):
        assert not result.allowed and result.payload == {}
        assert result.trace()["policy"] == "plan_asset_runtime_v1"


def test_plan_verifier_checks_actual_bytes_and_mime(monkeypatch, tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({**SOURCE, "floors": [ENTRY]}), encoding="utf-8")
    monkeypatch.setattr("app.data_sources.source_path", lambda source_id, project_id: manifest)
    monkeypatch.setattr(settings, "inventory_plan_base", "https://inventory.test")

    class Response:
        def __init__(self, body, media_type):
            self.body, self.headers = body, {"content-type": media_type}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield self.body

    requests = []
    reply = [IMAGE, "image/webp"]

    def stream(method, url, **kwargs):
        requests.append((method, url, kwargs))
        return Response(*reply)

    monkeypatch.setattr(httpx, "stream", stream)
    valid = units._verified_plan_asset(1)
    assert valid.allowed and valid.payload["path"] == "/fpg1.webp"
    assert requests[0][1] == "https://inventory.test/fpg1.webp"
    assert requests[0][2]["follow_redirects"] is False
    assert units._verified_plan_asset(1).allowed and len(requests) == 1

    units._PLAN_VERIFIED.clear()
    reply[0] = b"changed bytes"
    corrupt = units._verified_plan_asset(1)
    assert corrupt.reason == "plan_checksum_mismatch" and corrupt.payload == {}

    reply[:] = [IMAGE, "text/html"]
    wrong_type = units._verified_plan_asset(1)
    assert wrong_type.reason == "wrong_plan_content_type" and wrong_type.payload == {}


def test_foreign_plan_manifest_is_rejected_before_fetch(monkeypatch, tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({**SOURCE, "project_id": "embassy_life",
                                    "floors": [ENTRY]}), encoding="utf-8")
    monkeypatch.setattr("app.data_sources.source_path", lambda source_id, project_id: manifest)
    monkeypatch.setattr(httpx, "stream", lambda *args, **kwargs:
                        pytest.fail("foreign manifest must not trigger asset fetch"))
    result = units._verified_plan_asset(1)
    assert not result.allowed and result.payload == {}
    assert result.reason == "wrong_plan_source_or_project"


def test_blocked_plan_asset_never_queries_inventory_or_returns_image(monkeypatch):
    monkeypatch.setattr(settings, "inventory_url", "https://inventory.test")
    monkeypatch.setattr(settings, "inventory_key", "test-key")
    monkeypatch.setattr(units, "_verified_plan_asset", lambda floor: RuntimeResult(
        False, settings.project_id, "floor_plan_assets", "plan_checksum_mismatch", {},
        "plan_asset_runtime_v1"))
    monkeypatch.setattr(units, "_live_get", lambda *args, **kwargs:
                        pytest.fail("blocked asset must stop before database access"))
    result = units.show_plan(1)
    assert not result["ok"] and "image" not in result and "units" not in result
    assert result["policy_trace"]["reason"] == "plan_checksum_mismatch"


def test_plan_overlay_denies_foreign_floor_status_and_unbounded_coordinates(monkeypatch):
    monkeypatch.setattr(settings, "inventory_url", "https://inventory.test")
    monkeypatch.setattr(settings, "inventory_key", "test-key")
    monkeypatch.setattr(units, "_verified_plan_asset", lambda floor: RuntimeResult(
        True, settings.project_id, "floor_plan_assets", "verified_scoped_plan_asset",
        {"path": "/fpg1.webp", "floor": floor}, "plan_asset_runtime_v1"))
    rows = [[ROW]]
    monkeypatch.setattr(units, "_live_get", lambda *args, **kwargs: rows[0])
    prepared = units.show_plan(1)
    assert prepared["pending_display"] is True and prepared["ok"] is False

    cases = [
        ({**ROW, "floors": {"floor_number": 1, "buildings": {
            "code": "A", "projects": {"slug": "embassy-life"}}}},
         "wrong_or_missing_row_project"),
        ({**ROW, "floors": {**ROW["floors"], "floor_number": 2}}, "wrong_plan_floor"),
        ({**ROW, "status": "internal_hold"}, "invalid_plan_overlay"),
        ({**ROW, "pos_x": 105}, "invalid_plan_overlay"),
    ]
    for row, reason in cases:
        rows[0] = [ROW, row]
        result = units.show_plan(1)
        assert not result["ok"] and "image" not in result and "units" not in result
        assert result["policy_trace"]["reason"] == reason
