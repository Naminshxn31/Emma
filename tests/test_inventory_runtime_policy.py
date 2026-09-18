"""M0.3 inventory slice: direct adapter calls must not bypass disclosure."""
from datetime import datetime, timedelta, timezone
import json

import pytest

from app.config import settings
from app.runtime_policy import prepare_inventory_card
from app.tools import units


ROW = {
    "id": "test-unit", "unit_no": "A-203", "status": "available",
    "msize": 34.0, "base_price": 3_690_000, "promo_price": 3_290_000,
    "note": "internal note must not be sent",
    # A row may be unchanged for months while the scoped read was just made.
    "updated_at": "2026-01-01T00:00:00+00:00",
    "floors": {"floor_number": 2, "buildings": {
        "code": "A", "projects": {"slug": "embassy-world"}}},
    "unit_types": {"name": "1BR"},
}


@pytest.fixture(autouse=True)
def fresh_units():
    units.reset()
    yield
    units.reset()


def live(monkeypatch, rows):
    monkeypatch.setattr(settings, "inventory_url", "https://test.invalid")
    monkeypatch.setattr(settings, "inventory_key", "test-key")
    monkeypatch.setattr(settings, "units_show_price", False)

    def fake_get(params, table="units"):
        return rows if table == "units" else []

    monkeypatch.setattr(units, "_live_get", fake_get)


def test_direct_live_adapter_blocks_foreign_and_missing_row_scope(monkeypatch):
    foreign = {**ROW, "floors": {"floor_number": 2, "buildings": {
        "code": "A", "projects": {"slug": "embassy-life"}}}}
    live(monkeypatch, [foreign])
    result = units.show_unit_live("A203")
    assert result["ok"] is False and result["policy_trace"]["reason"] == "wrong_or_missing_row_project"
    assert "unit" not in result and "internal note" not in str(result)

    units.reset()
    live(monkeypatch, [{**ROW, "floors": {"floor_number": 2, "buildings": {"code": "A"}}}])
    assert units.show_unit_live("A203")["policy_trace"]["reason"] == "wrong_or_missing_row_project"


def test_stale_cached_card_and_missing_fetch_time_have_no_payload(monkeypatch):
    live(monkeypatch, [ROW])
    stale = units._card_from_live(ROW)
    stale["_fetched_at"] = datetime.now(timezone.utc) - timedelta(seconds=90)
    monkeypatch.setattr(units, "_live_find", lambda room: stale)
    result = units.show_unit_live("A203")
    assert result["policy_trace"]["reason"] == "stale_inventory"
    assert "unit" not in result and "price_thb" not in str(result)

    missing = dict(stale)
    missing.pop("_fetched_at")
    monkeypatch.setattr(units, "_live_find", lambda room: missing)
    assert units.show_unit_live("A203")["policy_trace"]["reason"] == "missing_fetch_timestamp"


def test_runtime_router_denies_untrusted_source_and_wrong_project():
    card = units._card_from_live(ROW)
    base = dict(expected_project_id=settings.project_id,
                max_age_seconds=30, allow_price=False)
    untrusted = prepare_inventory_card({**card, "source_id": "other_adapter"}, **base)
    assert not untrusted.allowed and untrusted.payload == {}
    assert untrusted.reason == "untrusted_or_wrong_project"
    foreign = prepare_inventory_card({**card, "project_id": "embassy_life"}, **base)
    assert not foreign.allowed and foreign.payload == {}


def test_only_disclosed_fields_reach_emma(monkeypatch):
    live(monkeypatch, [ROW])
    out = units.show_unit_live("A203")
    assert out["ok"] is True and out["policy_trace"]["allowed"] is True
    assert out["unit"]["room"] == "A-203"
    for forbidden in ("note", "promo_note", "_fetched_at", "_project_slug",
                      "price_thb", "base_price_thb", "id"):
        assert forbidden not in out["unit"]
    assert out["unit"]["source_id"] == "live_unit_inventory"

    monkeypatch.setattr(settings, "units_show_price", True)
    units.reset()
    priced = units.show_unit_live("A203")["unit"]
    assert priced["price_thb"] == 3_290_000
    assert "note" not in priced


def test_unverified_unit_photo_and_plan_links_are_not_disclosed():
    card = units._card_from_live(ROW)
    card.update(photo="https://untrusted.test/photo.jpg",
                floorplan="https://untrusted.test/plan.webp")
    result = prepare_inventory_card(
        card, expected_project_id=settings.project_id,
        max_age_seconds=30, allow_price=False)
    assert result.allowed
    assert "photo" not in result.payload and "floorplan" not in result.payload


def test_row_mutation_time_is_not_fetch_freshness(monkeypatch):
    live(monkeypatch, [ROW])
    assert units.show_unit_live("A203")["ok"] is True


def test_mixed_project_list_is_all_or_nothing(monkeypatch):
    foreign = {**ROW, "unit_no": "B-801", "floors": {"floor_number": 8,
               "buildings": {"code": "B", "projects": {"slug": "embassy-life"}}}}
    live(monkeypatch, [ROW, foreign])
    for result in (units.find_units(), units.compare_unit_types()):
        assert result["ok"] is False
        assert "units" not in result and "types" not in result
        assert result["policy_trace"]["reason"] == "wrong_or_missing_row_project"


def test_unexpected_status_from_backend_is_blocked(monkeypatch):
    live(monkeypatch, [{**ROW, "status": "sold"}])
    assert units.find_units()["policy_trace"]["reason"] == "unexpected_inventory_status"
    assert units.compare_unit_types()["policy_trace"]["reason"] == "unexpected_inventory_status"


def test_promotion_free_text_and_foreign_unit_do_not_reach_emma(monkeypatch):
    live(monkeypatch, [])
    promo = {"active": True, "note": "unapproved free-text promotion",
             "thai_price": 1, "units": ROW}
    monkeypatch.setattr(units, "_live_get", lambda params, table="units":
                        [promo] if table == "pricing_unit_promotions" else [])
    out = units.list_promotions()
    assert out["ok"] and "unapproved free-text promotion" not in str(out)
    assert "promo_note" not in out["promotions"][0]

    foreign = {**ROW, "floors": {"floor_number": 2, "buildings": {
        "code": "A", "projects": {"slug": "embassy-life"}}}}
    monkeypatch.setattr(units, "_live_get", lambda params, table="units":
                        [{**promo, "units": foreign}] if table == "pricing_unit_promotions" else [])
    blocked = units.list_promotions()
    assert blocked["ok"] is False and "promotions" not in blocked


def test_quotation_and_price_endpoint_stop_on_wrong_project(monkeypatch):
    from app.tools import webstage

    foreign = {**ROW, "floors": {"floor_number": 2, "buildings": {
        "code": "A", "projects": {"slug": "embassy-life"}}}}
    live(monkeypatch, [foreign])
    opened = []
    monkeypatch.setattr(webstage, "enabled", lambda: True)
    monkeypatch.setattr(webstage, "request", lambda url: opened.append(url))
    result = units.show_quotation("A203")
    assert result["ok"] is False and "room" not in result
    assert opened == []
    assert units.price_pair("A203") is None


def test_revoked_static_approval_is_not_kept_alive_by_file_cache(monkeypatch, tmp_path):
    source = {
        "source_id": "local_unit_inventory", "project_id": settings.project_id,
        "approval_status": "approved", "approved_by": "test_reviewer",
        "approved_at": "2026-01-01T00:00:00+07:00",
        "effective_at": "2026-01-01T00:00:00+07:00",
        "disclosure_scope": "customer", "content_state": "existing",
        "units": [{"room": "A203", "status": "available"}],
    }
    path = tmp_path / "units.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    monkeypatch.setattr(settings, "inventory_url", "")
    monkeypatch.setattr(settings, "units_file", str(path))
    assert units.show_unit("A203")["ok"] is True

    source["approval_status"] = "draft"
    path.write_text(json.dumps(source), encoding="utf-8")
    blocked = units.show_unit("A203")
    assert blocked["ok"] is False and "unit" not in blocked
    assert blocked["policy_trace"]["reason"] == "not_approved"
