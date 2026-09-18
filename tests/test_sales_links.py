"""
Sales-list items 1, 5, 6, 9 and 17 wired to condo-inventory (2026-09-01).

What condo-inventory had by then, and what each tool reads from it:

  list_promotions     `pricing_unit_promotions` — the admin-applied rows
                      (27 Aug), lately fed by the promo inbox. Emma reads
                      the result of that workflow, nothing upstream.
  compare_unit_types  the same `units` rows the card reads, aggregated:
                      counts, sizes, floors, views. No prices — policy.
  show_quotation      the sales app's own Excel-replica quotation page
                      (29 Aug; ?currency= added 1 Sep) opened on the stage
                      for the unit asked. The figures are the team's own.
  show_map            a sales-confirmed Google Maps link from
                      condo_facts.json, or an honest "no map yet".

The standing rules hold on every path: prices never enter a tool result
unless UNITS_SHOW_PRICE is on, nothing is invented, and a system that
cannot be reached says so.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import prompts
from app.config import settings
from app.tools import units

UNIT = {
    "id": "8f1c-unit-a203", "unit_no": "A-203", "size_sqm": 33.72, "msize": 34.0,
    "view": "POOLABC", "side": "SOUTH", "collection": "LAGOON", "unit_option": None,
    "base_price": 3690000.0, "promo_price": 3290000.0, "status": "available",
    "note": None, "updated_at": "2026-08-22T03:45:47.222261+00:00",
    "floors": {"floor_number": 2, "buildings": {"code": "A"}},
    "unit_types": {"name": "1BR"},
}
PROMO_ROW = {
    "unit_id": "8f1c-unit-a203", "thai_price": 3100000.0, "foreign_price": 3500000.0,
    "note": "ราคาพิเศษเดือนกันยายน ฟรีค่าโอน", "updated_at": "2026-09-01T02:00:00+00:00",
    "units": UNIT,
}


class _Calls(list):
    """The fake's call log, with the answer table hung on it."""
    answers: dict


@pytest.fixture
def live(monkeypatch):
    """A fake PostgREST: records every (table, params) and answers by table."""
    calls = _Calls()
    answers = {"units": [UNIT], "pricing_unit_promotions": [PROMO_ROW]}

    def fake_get(params, table="units"):
        calls.append((table, dict(params)))
        return list(answers.get(table, []))

    monkeypatch.setattr(settings, "inventory_url", "https://x.supabase.co")
    monkeypatch.setattr(settings, "inventory_key", "k")
    monkeypatch.setattr(settings, "units_show_price", False)
    monkeypatch.setattr(units, "_live_get", fake_get)
    units.reset()
    calls.answers = answers
    return calls


# ==================== 5. promotions ====================

def test_promotions_come_from_the_applied_rows_only(live):
    out = units.list_promotions()
    assert out["ok"] and out["screen"] == "promotions" and out["count"] == 1
    table, params = live[-1]
    assert table == "pricing_unit_promotions"
    assert params["active"] == "eq.true"
    assert params["units.status"] == "neq.sold", "a sold unit's promotion is not an offer"
    p = out["promotions"][0]
    assert p["room"] == "A-203" and p["promo"] is True
    assert p["promo_note"] == "ราคาพิเศษเดือนกันยายน ฟรีค่าโอน"


def test_promotion_prices_follow_the_price_policy(live, monkeypatch):
    out = units.list_promotions()
    p = out["promotions"][0]
    assert "promo_thai_thb" not in p and "price_thb" not in p
    assert "ไม่พูดตัวเลขราคา" in out["instruction"]

    monkeypatch.setattr(settings, "units_show_price", True)
    shown = units.list_promotions()["promotions"][0]
    assert shown["promo_thai_thb"] == 3100000.0 and shown["promo_foreign_thb"] == 3500000.0


def test_no_promotions_is_said_not_invented(live):
    live.answers["pricing_unit_promotions"] = []
    out = units.list_promotions()
    assert out["ok"] and out["count"] == 0
    assert "ยังไม่มีโปรโมชั่น" in out["instruction"]


def test_promotions_by_building_filter_the_joined_unit(live):
    units.list_promotions(building="b")
    assert live[-1][1]["units.unit_no"] == "like.B-*"


def test_the_unit_card_carries_its_promotion(live):
    out = units.show_unit("A203")
    card = out["unit"]
    assert card["promo"] is True and "ฟรีค่าโอน" in card["promo_note"]
    assert "promo_thai_thb" not in card, "the figure waits for the tap"
    assert "มีโปรโมชั่น" in out["instruction"]


def test_the_tap_reveals_the_promotion_figure_too(live):
    pair = units.price_pair("A203")
    assert pair["thai"] == 3290000.0 and pair["promo_thai"] == 3100000.0
    assert pair["promo_note"].startswith("ราคาพิเศษ")


def test_a_promotion_lookup_failure_does_not_take_the_card_down(live, monkeypatch):
    real = units._live_get

    def flaky(params, table="units"):
        if table == "pricing_unit_promotions":
            raise RuntimeError("promotions table missing")
        return real(params, table)

    monkeypatch.setattr(units, "_live_get", flaky)
    units.reset()
    out = units.show_unit("A203")
    assert out["ok"] and "promo" not in out["unit"]


# ==================== 6. compare unit types ====================

def test_unit_types_are_compared_by_counts_sizes_floors_and_views(live):
    studio = dict(UNIT, id="u2", unit_no="A-305", msize=25.0, view="LAGOON", side="NORTH",
                  unit_option="Corner", floors={"floor_number": 3, "buildings": {"code": "A"}},
                  unit_types={"name": "STUDIO"})
    two = dict(UNIT, id="u3", unit_no="B-801", msize=52.0, view="SEA", side=None,
               floors={"floor_number": 8, "buildings": {"code": "B"}},
               unit_types={"name": "2BR"})
    live.answers["units"] = [UNIT, studio, dict(studio, id="u4", unit_no="A-405",
                                                 floors={"floor_number": 4, "buildings": {"code": "A"}}), two]
    out = units.compare_unit_types()
    assert out["ok"] and out["screen"] == "unittypes" and out["total_available"] == 4
    assert live[-1][1]["status"] == "eq.available"
    by = {t["type"]: t for t in out["types"]}
    assert [t["type"] for t in out["types"]] == ["STUDIO", "1BR", "2BR"], "smallest first"
    assert by["STUDIO"]["available"] == 2
    assert by["STUDIO"]["floor_min"] == 3 and by["STUDIO"]["floor_max"] == 4
    assert by["STUDIO"]["views"] == ["LAGOON (2)"] and by["STUDIO"]["options"] == ["Corner (2)"]
    assert by["2BR"]["sqm_min"] == 52.0 and by["2BR"]["buildings"] == ["B"]
    for t in out["types"]:
        assert not any(k.endswith("_thb") for k in t), "no prices in a comparison"


def test_compare_can_be_narrowed_to_a_building(live):
    units.compare_unit_types(building="a")
    assert live[-1][1]["unit_no"] == "like.A-*"


# ==================== 1 + 9. the quotation on the stage ====================

@pytest.fixture
def stage(monkeypatch):
    from app.tools import webstage

    opened = []
    monkeypatch.setattr(settings, "web_stage", True)
    monkeypatch.setattr(settings, "inventory_plan_base", "https://inv.example")
    monkeypatch.setattr(webstage, "request", lambda url: opened.append(url))
    return opened


def test_the_quotation_is_the_sales_apps_own_page_for_that_unit(live, stage):
    out = units.show_quotation("A203", ownership="foreign", currency="usd")
    assert out["ok"] and out["screen"] == "web"
    assert stage == ["https://inv.example/quotation/8f1c-unit-a203?ownership=foreign&currency=USD"]
    assert "ห้ามอ่านราคา" in out["instruction"]
    assert "ล็อกอิน" in out["instruction"]


def test_the_quotation_defaults_to_thai_baht(live, stage):
    units.show_quotation("A203", ownership="thai")
    assert stage[-1].endswith("?ownership=thai&currency=THB")


def test_a_sold_unit_gets_no_quotation(live, stage):
    live.answers["units"] = [dict(UNIT, status="sold")]
    out = units.show_quotation("A203", ownership="thai")
    assert out["ok"] is False and out["error"] == "sold" and stage == []


def test_the_quotation_refuses_a_made_up_ownership_or_currency(live, stage):
    assert units.show_quotation("A203", ownership="alien")["ok"] is False
    assert units.show_quotation("A203", ownership="thai", currency="dollars")["ok"] is False
    assert stage == []


def test_the_quotation_needs_the_stage(live, monkeypatch):
    monkeypatch.setattr(settings, "web_stage", False)
    out = units.show_quotation("A203", ownership="thai")
    assert out["ok"] is False and out["error"] == "web stage off"


def test_the_inventory_page_accepts_the_currency_parameter():
    """The other half lives in condo-inventory: ?currency= on the sheet."""
    page = Path(r"C:\Users\Name\Documents\GitHub\condo-inventory\app\(sales)\quotation\[unitId]\page.tsx")
    if not page.is_file():
        pytest.skip("condo-inventory checkout not present — the ?currency= half is unverified here")
    src = page.read_text(encoding="utf-8")
    assert 'search.get("currency")' in src
    assert 'search.get("ownership")' in src


# ==================== 17. the map ====================

def test_no_confirmed_map_link_means_no_map_and_no_guessing(monkeypatch, tmp_path, stage):
    facts = tmp_path / "facts.json"
    facts.write_text(json.dumps({"source_id": "project_facts", "project_id": settings.project_id,
                                 "map": {"url": None}}), encoding="utf-8")
    monkeypatch.setattr(prompts, "CONDO_FACTS_FILE", str(facts))
    out = units.show_map()
    assert out["ok"] is False and stage == []
    assert "ห้ามเดาพิกัด" in out["instruction"]


def test_a_confirmed_map_link_goes_on_the_stage(monkeypatch, tmp_path, stage):
    facts = tmp_path / "facts.json"
    facts.write_text(json.dumps({"source_id": "project_facts", "project_id": settings.project_id,
                                 "map": {"url": "https://maps.app.goo.gl/abc123"}}), encoding="utf-8")
    monkeypatch.setattr(prompts, "CONDO_FACTS_FILE", str(facts))
    out = units.show_map()
    assert out["ok"] and stage == ["https://maps.app.goo.gl/abc123"]


def test_only_a_google_maps_link_counts_as_the_map(monkeypatch, tmp_path, stage):
    facts = tmp_path / "facts.json"
    facts.write_text(json.dumps({"source_id": "project_facts", "project_id": settings.project_id,
                                 "map": {"url": "https://evil.example/x"}}), encoding="utf-8")
    monkeypatch.setattr(prompts, "CONDO_FACTS_FILE", str(facts))
    assert units.show_map()["ok"] is False and stage == []


def test_the_facts_file_map_slot_is_empty_or_a_confirmed_google_maps_link():
    """Filled by the owner on 2026-09-01 (resolves to "Embassy World
    Pattaya", 12.8848 N 100.8862 E). Empty is also a valid state; a link
    to anywhere but Google Maps is not."""
    facts = json.loads(Path(prompts.CONDO_FACTS_FILE).read_text(encoding="utf-8"))
    assert "map" in facts
    url = facts["map"].get("url")
    assert url in (None, "") or url.startswith(("https://maps.app.goo.gl/", "https://www.google.com/maps"))
    if url:
        assert facts["map"].get("confirmed_by"), "a filled link says who confirmed it"


# ==================== the screen ====================

def test_the_page_renders_the_two_new_screens():
    page = Path("client/index.html").read_text(encoding="utf-8")
    assert "r.screen === 'promotions'" in page and "function showPromotions(" in page
    assert "r.screen === 'unittypes'" in page and "function showUnitTypes(" in page
    assert "u.promo" in page, "the unit card shows its promotion badge"


def test_all_four_tools_are_in_the_units_group():
    from app.tools import registry

    for name in ("list_promotions", "compare_unit_types", "show_quotation", "show_map"):
        spec = registry.get(name)
        assert spec is not None, name
        assert "units" in spec.tags
