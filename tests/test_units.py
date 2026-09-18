"""
The unit card — a screen of our own HTML, and the rule that keeps it honest.

The card exists so the sales team can see the shape of the thing before their
spreadsheet arrives. That makes it the single most dangerous screen in the
project: a plausible-looking price, in a showroom, on something a customer
can photograph. Every test here is about the difference between showing a
number and inventing one.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.tools import units


@pytest.fixture(autouse=True)
def _fresh():
    units.reset()
    yield
    units.reset()


def test_the_model_picks_the_room_never_the_price(monkeypatch):
    """The safety design in one line. A tool shaped
    `show_unit(room=..., price=...)` would hand "ห้ามแต่งข้อมูลโครงการเอง
    เด็ดขาด" to a machine that mishears numbers. Same shape as
    data/documents/catalogue.json: the assistant picks from a list the sales
    team owns, it does not compose the contents."""
    from app.tools import load_tools

    entry = next(t for t in load_tools() if t.name == "show_unit")
    assert set(entry.parameters["properties"]) == {"room"}, \
        "the only thing the model may choose is which room"
    assert "ห้ามเดาราคา" in entry.description


def test_an_unknown_room_has_no_price_and_that_is_the_answer(monkeypatch, tmp_path):
    """A room that is not in the table has no price. Returning an error is
    the correct behaviour, and the instruction has to stop the model
    smoothing it over."""
    import json

    source = {
        "source_id": "local_unit_inventory", "project_id": settings.project_id,
        "approval_status": "approved", "approved_by": "test_reviewer",
        "approved_at": "2026-01-01T00:00:00+07:00",
        "effective_at": "2026-01-01T00:00:00+07:00",
        "disclosure_scope": "customer", "content_state": "existing",
        "units": [{"room": "A801", "status": "available"}],
    }
    path = tmp_path / "units.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    monkeypatch.setattr(settings, "units_file", str(path))
    out = units.show_unit("Z999")
    assert out["ok"] is False and out["error"] == "unknown room"
    assert "ห้ามแต่งข้อมูลห้องขึ้นมาเอง" in out["instruction"]


def test_the_sample_table_is_off_by_default():
    """A showroom must never be one forgotten setting away from quoting
    invented prices. The watermark and the spoken warning are the second and
    third lines of defence; this is the first."""
    import inspect
    import re

    from app import config

    assert re.search(r'_get_bool\("UNITS_SAMPLE",\s*False\)',
                     inspect.getsource(config))


def test_with_no_table_at_all_it_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "units_sample", False)
    monkeypatch.setattr(settings, "units_file", str(tmp_path / "nope.json"))
    out = units.show_unit("A801")
    assert out["ok"] is False and out["error"] == "no unit table"
    assert "ติดต่อฝ่ายขาย" in out["instruction"]


def test_sample_data_is_blocked_before_reaching_emma(monkeypatch):
    """A development sample may load, but it is not customer data."""
    monkeypatch.setattr(settings, "units_sample", True)
    out = units.show_unit("A801")
    assert out["ok"] is False and out["error"] == "inventory not disclosable"
    assert out["policy_trace"]["reason"] == "sample_inventory"
    assert "unit" not in out and "price_thb" not in str(out)


def test_a_real_table_stops_being_a_sample_without_anyone_flipping_anything(
        monkeypatch, tmp_path):
    """Sample-ness is a property of the file, not of the setting that allowed
    it. The day the spreadsheet lands, the warning has to go on its own —
    leaving it to a second setting is leaving it to be forgotten."""
    import json

    real = tmp_path / "units.json"
    real.write_text(json.dumps({
        "source_id": "local_unit_inventory", "project_id": settings.project_id,
        "approval_status": "approved", "approved_by": "test_reviewer",
        "approved_at": "2026-01-01T00:00:00+07:00",
        "effective_at": "2026-01-01T00:00:00+07:00",
        "disclosure_scope": "customer", "content_state": "existing",
        "units": [{"room": "A801", "price_thb": 3100000, "status": "available"}],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "units_file", str(real))
    monkeypatch.setattr(settings, "units_sample", True)   # still on, and irrelevant

    out = units.show_unit("A801")
    assert out["sample"] is False
    assert out["unit"]["source_id"] == "local_unit_inventory"
    assert "approved_by" not in out["unit"]
    assert "ตัวอย่าง" not in out["instruction"]


def test_an_unapproved_real_table_does_not_reach_emma(monkeypatch, tmp_path):
    """A confirmation warning is not a disclosure gate."""
    import json

    real = tmp_path / "units.json"
    real.write_text(json.dumps({
        "source_id": "local_unit_inventory", "project_id": settings.project_id,
        "units": [{"room": "A801", "price_thb": 3100000}],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "units_file", str(real))
    out = units.show_unit("A801")
    assert out["ok"] is False and "unit" not in out
    assert out["policy_trace"]["reason"] == "missing_metadata"


def test_the_card_labels_what_is_missing_instead_of_hiding_it():
    """The card is going in front of the sales team. A blank that names what
    belongs in it asks for the spreadsheet more clearly than an email has."""
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "client" / "index.html").read_text(
        encoding="utf-8")
    body = js[js.index("function showUnit(u)"):]
    body = body[:body.index("\n//: Put a framed page")]
    assert "ภาพห้อง — รอจากฝ่ายขาย" in body
    assert "แปลนรายยูนิต — รอจากฝ่ายขาย" in body
    assert "ราคา — รอจากฝ่ายขาย" in body
    assert "ตัวอย่างเท่านั้น — ยังไม่ใช่ข้อมูลจริง" in body

    # Reading the renderer alone passes with the call site deleted — checked,
    # it did. The tool result is what actually puts the card on the stage.
    res = js[js.index("case 'tool_result': {"):]
    res = res[:res.index(chr(10) + "    }")]
    assert "showUnit(r.unit)" in res


def test_a_fallback_file_is_a_sample_even_without_saying_so(monkeypatch, tmp_path):
    """The `"sample": true` flag inside the file is a courtesy; where the file
    came from is the fact. A fallback that has lost its flag — copied,
    edited, written by hand — must still carry the warning, because the one
    thing that must not depend on someone remembering is this one."""
    import json
    from pathlib import Path

    plain = tmp_path / "borrowed.json"
    plain.write_text(json.dumps({
        "units": [{"room": "A801", "price_thb": 9999999}],
    }), encoding="utf-8")
    monkeypatch.setattr(units, "_table_path", lambda: (Path(plain), True))

    out = units.show_unit("A801")
    assert out["ok"] is False and "unit" not in out
    assert out["policy_trace"]["reason"] == "sample_inventory"


def test_the_card_is_its_own_group_not_part_of_the_slide_deck(monkeypatch):
    """Filing it under "slides" meant Emma's machine could not have the card
    without also getting `start_presentation` — and a tool the model can see
    is a tool it will eventually call, which is how a condo deck ends up
    being narrated in the owner's living room.

    Found the honest way: the card was built, and asking for room A801 got
    "เอมม่าไม่สามารถเปิดสไลด์ได้ค่ะ" because the module never loaded."""
    from app.tools import _TOOL_MODULES, _modules_to_load

    assert _TOOL_MODULES["app.tools.units"] == "units"
    assert _TOOL_MODULES["app.tools.slides"] == "slides"

    # Asking for the card must not drag the deck in with it.
    only_units = _modules_to_load({"units"})
    assert "app.tools.units" in only_units
    assert "app.tools.slides" not in only_units

    # And the gallery, which names no groups at all, still gets both.
    everything = _modules_to_load(None)
    assert "app.tools.units" in everything and "app.tools.slides" in everything


def test_a_sample_switch_with_no_tool_behind_it_is_said_out_loud(monkeypatch, caplog):
    """UNITS_SAMPLE=true and no `units` group loads nothing and says nothing:
    the assistant answers "เอมม่าทำสิ่งนั้นไม่ได้ค่ะ", which is exactly what it
    says when the feature was never built. Two very different problems with
    one symptom, and the boot log knew the difference all along."""
    import asyncio
    import logging

    import app.main as main
    from app import tools as tools_pkg

    monkeypatch.setattr(settings, "units_sample", True)
    monkeypatch.setattr(settings, "canva_url", "")
    monkeypatch.setattr(settings, "tool_groups", "smarthome")
    # `load_tools()` caches after the first call, and by now the suite has
    # loaded everything — so the real registry would report show_unit as
    # present no matter what TOOL_GROUPS says. Stand in for the machine that
    # actually has the setting on and the group off.
    monkeypatch.setattr(tools_pkg, "load_tools",
                        lambda: [type("T", (), {"name": "set_lights"})()])

    with caplog.at_level(logging.WARNING, logger="condo_voice"):
        asyncio.run(main._log_effective_config())
    assert "UNITS_SAMPLE is on" in caplog.text
    # And it prints the line to paste, rather than describing it.
    assert "TOOL_GROUPS=" in caplog.text and "units" in caplog.text


def test_a_misspelt_group_name_is_not_silent():
    """`TOOL_GROUPS=...,unit` loads exactly as much as leaving it out. Silence
    there means the only evidence of a typo is the assistant refusing to do
    something it was built to do."""
    from app.tools import unknown_groups

    assert unknown_groups({"smarthome", "unit"}) == {"unit"}
    assert unknown_groups({"smarthome", "units"}) == set()
    assert unknown_groups(None) == set(), "no list named means no typo possible"


# ============ the live inventory link ============
#
# A row captured from the real system on 2026-08-25, so the mapping is
# tested against the shape the DB actually sends, not a guess at it.
LIVE_ROW = {
    "unit_no": "A-203", "size_sqm": 33.72, "msize": 34.0, "view": "POOLABC",
    "side": None, "collection": "LAGOON", "unit_option": None,
    "base_price": 3690000.0, "promo_price": 3290000.0, "status": "available",
    "note": None, "updated_at": "2026-08-22T03:45:47.222261+00:00",
    "floors": {"floor_number": 2, "buildings": {
        "code": "A", "projects": {"slug": "embassy-world"}}},
    "unit_types": {"name": "1BR"},
}


def _live_on(monkeypatch, rows):
    calls = []

    def fake_get(params):
        calls.append(params)
        return rows

    monkeypatch.setattr(settings, "inventory_url", "https://x.supabase.co")
    monkeypatch.setattr(settings, "inventory_key", "k")
    monkeypatch.setattr(units, "_live_get", fake_get)
    return calls


def test_spoken_room_numbers_reach_the_db_in_its_own_shape(monkeypatch):
    """Guests say "A801"; the pricelist writes "A-801". And bare digits are
    refused rather than guessed — two buildings can share a floor plan, and
    the wrong building's 801 shown confidently is worse than one clarifying
    question."""
    assert units._normalize_room("A801") == "A-801"
    assert units._normalize_room("a 801") == "A-801"
    assert units._normalize_room("A-801") == "A-801"
    assert units._normalize_room("801") is None

    calls = _live_on(monkeypatch, [LIVE_ROW])
    units.show_unit("A203")
    assert calls[0]["unit_no"] == "eq.A-203"

    bare = units.show_unit("801")
    assert bare["ok"] is False and bare["error"] == "bad room number"
    assert "ห้ามเดาตึกเอง" in bare["instruction"]


def test_the_live_card_carries_the_pricelists_own_meaning(monkeypatch):
    """promo_price is the selling price, base_price the list price, msize the
    customer-facing size — the mapping is the pricelist's semantics, checked
    against a captured real row. Mapping is tested on the internal card:
    the tool boundary strips prices by policy (next test), but the numbers
    must stay correct underneath because the budget filter runs on them."""
    u = units._card_from_live(LIVE_ROW)
    assert u["price_thb"] == 3290000.0 and u["base_price_thb"] == 3690000.0
    assert u["sqm"] == 34.0, "msize (customer-facing) wins over size_sqm"
    assert u["floor"] == 2 and u["building"] == "A" and u["type"] == "1BR"
    assert u["source"] == "live"

    _live_on(monkeypatch, [LIVE_ROW])
    out = units.show_unit("A203")
    assert out["sample"] is False
    assert "สด" in out["instruction"]


def test_prices_never_leave_the_server_unless_switched_on(monkeypatch):
    """The owner's instruction, verbatim: ห้ามโชว์ราคา — but "ถ้ามีเงินเท่านี้
    ซื้อห้องไหนได้บ้าง" must still work. So the price stays a server-side
    filter and is stripped from the payload: a field that is not sent cannot
    be leaked by a CSS mistake, and the model cannot read aloud a number it
    never received."""
    calls = _live_on(monkeypatch, [LIVE_ROW])
    out = units.show_unit("A203")
    assert "price_thb" not in out["unit"] and "base_price_thb" not in out["unit"]
    assert "ห้ามพูดหรือเดาตัวเลขราคา" in out["instruction"]

    lst = units.find_units(max_price_thb=3_500_000)
    assert calls[-1]["promo_price"] == "lte.3500000", "the filter still runs"
    assert all("price_thb" not in x for x in lst["units"])
    assert "ห้ามพูดหรือเดาราคา" in lst["instruction"]

    # The explicit opt-in for a machine whose policy is to show them.
    monkeypatch.setattr(settings, "units_show_price", True)
    units.reset()
    shown = units.show_unit("A203")
    assert shown["unit"]["price_thb"] == 3290000.0


def test_an_unreachable_inventory_is_said_not_papered_over(monkeypatch):
    """Falling back to the sample here would put invented prices on screen at
    the exact moment nobody can check them against the real system. Honest
    failure, same rule as mock-is-not-ok."""
    monkeypatch.setattr(settings, "inventory_url", "https://x.supabase.co")
    monkeypatch.setattr(settings, "inventory_key", "k")

    def boom(params):
        raise RuntimeError("network down")

    monkeypatch.setattr(units, "_live_get", boom)
    monkeypatch.setattr(settings, "units_sample", True)   # sample armed, and ignored
    out = units.show_unit("A203")
    assert out["ok"] is False and out["error"] == "inventory unreachable"
    assert "unit" not in out, "no card at all beats an invented one"
    assert "ห้ามบอกสถานะหรือราคาจากความจำ" in out["instruction"]


def test_the_cache_keeps_ว่าง_meaning_now(monkeypatch):
    """One fetch inside the TTL window, a fresh one after reset — 30s of
    cache is a courtesy to the DB, not a licence to serve yesterday."""
    calls = _live_on(monkeypatch, [LIVE_ROW])
    monkeypatch.setattr(settings, "inventory_cache_s", 60.0)
    units.show_unit("A203")
    units.show_unit("A-203")
    assert len(calls) == 1, "second ask inside the TTL must hit the cache"
    units.reset()
    units.show_unit("A203")
    assert len(calls) == 2


def test_find_units_defaults_to_available_and_never_widens(monkeypatch):
    """The question this answers is "what can I buy" — a sold unit in that
    list is an embarrassment at the desk. Availability is the default
    filter, not an option the model may drop."""
    calls = _live_on(monkeypatch, [LIVE_ROW])
    out = units.find_units(max_price_thb=3_500_000, building="a")
    p = calls[0]
    assert p["status"] == "eq.available"
    assert p["promo_price"] == "lte.3500000"
    assert p["unit_no"] == "like.A-*"
    assert out["screen"] == "unitlist" and out["count"] == 1

    # A range folds into PostgREST's and=() — one filter per key otherwise.
    calls.clear()
    units.find_units(min_price_thb=3_000_000, max_price_thb=4_000_000)
    assert calls[0]["and"] == "(promo_price.gte.3000000,promo_price.lte.4000000)"

    # And with no link configured, it says so instead of inventing a list.
    monkeypatch.setattr(settings, "inventory_url", "")
    off = units.find_units(max_price_thb=1)
    assert off["ok"] is False and "ห้ามแต่งรายการเอง" in off["instruction"]


def test_find_units_filters_two_bedrooms_without_inventing_a_building_or_view(monkeypatch):
    row = dict(LIVE_ROW)
    row["unit_no"] = "D-401"
    row["unit_types"] = {"name": "2 Bedroom"}
    calls = _live_on(monkeypatch, [row])

    out = units.find_units(bedrooms=2)

    params = calls[0]
    assert params["unit_types.name"] == "eq.2 Bedroom"
    assert "unit_types!inner(name)" in params["select"]
    assert "unit_no" not in params, "no customer building means no building filter"
    assert "view" not in params, "no customer view means no view filter"
    assert {item["type"] for item in out["units"]} == {"2 Bedroom"}


def test_find_units_schema_tells_the_model_not_to_invent_filters():
    from app.tools import load_tools

    entry = next(t for t in load_tools() if t.name == "find_units")
    bedrooms = entry.parameters["properties"]["bedrooms"]
    assert bedrooms["type"] == "integer" and bedrooms["minimum"] == 0
    assert "ห้ามอนุมานหรือเติม building/view เอง" in entry.description
    assert "ใส่เมื่อคำพูดลูกค้าระบุตึกเท่านั้น" in entry.parameters["properties"]["building"]["description"]
    assert "ใส่เมื่อคำพูดลูกค้าระบุวิวเท่านั้น" in entry.parameters["properties"]["view_contains"]["description"]


def test_compare_unit_types_result_leads_with_use_not_inventory_numbers(monkeypatch):
    _live_on(monkeypatch, [LIVE_ROW])

    out = units.compare_unit_types()

    assert "เริ่มคำตอบด้วยความแตกต่างด้านการใช้งาน" in out["instruction"]
    assert "เป็นข้อมูลประกอบเมื่อเกี่ยวข้อง" in out["instruction"]
    assert "ห้ามสรุปว่าแบบใดเหมาะลงทุนหรืออยู่เอง" in out["instruction"]
    assert "ห้ามถามคำถามต่อท้าย" in out["instruction"]
    assert "เทียบให้ฟังสั้นๆ จากตัวเลข" not in out["instruction"]


def test_pasted_annotation_junk_does_not_take_the_link_down(monkeypatch):
    """The first live outage of this link: the setup note's annotation arrow
    pasted into .env along with the value, and show_unit reported the sales
    system unreachable while it was fine. Values are copied by hand on
    purpose (secrets stay out of tooling), so hand-paste accidents are part
    of the design — and neither a URL nor a key may contain whitespace, so
    cutting at the first space loses nothing real."""
    # The helper, not Settings(): dataclass field defaults are evaluated
    # once at class definition, so a fresh Settings() would show the
    # machine's env, not the monkeypatched one — the first version of this
    # test "passed" against live values and proved nothing.
    from app.config import _get_token

    monkeypatch.setenv("JUNKY",
                       "https://x.supabase.co   ← ค่าจาก NEXT_PUBLIC_SUPABASE_URL")
    assert _get_token("JUNKY") == "https://x.supabase.co"
    monkeypatch.setenv("JUNKY", "sb_secret_abc   ← ค่าจาก SERVICE_ROLE")
    assert _get_token("JUNKY") == "sb_secret_abc"
    monkeypatch.setenv("JUNKY", "   ")
    assert _get_token("JUNKY") == ""


# ============ the live floor plan ============


def _verified_plan(monkeypatch):
    from app.runtime_policy import RuntimeResult

    monkeypatch.setattr(units, "_verified_plan_asset", lambda floor: RuntimeResult(
        True, settings.project_id, "floor_plan_assets",
        "verified_scoped_plan_asset", {"floor": floor, "path": "/fpg1.webp"}))


def test_the_plan_filters_by_floor_and_counts_only_the_asked_building(monkeypatch):
    """The image is a per-floor composite of every building, so the query
    filters by floor via the joined table — and when the guest asked about
    one building, the *numbers spoken* must be that building's, while other
    buildings stay visible but dimmed (the guest is looking at a map)."""
    rows = [
        {"unit_no": "F-101", "status": "available", "pos_x": 10, "pos_y": 10,
         "width": 2, "height": 3, "poly": None,
         "floors": {"floor_number": 1, "buildings": {
             "code": "F", "projects": {"slug": "embassy-world"}}}},
        {"unit_no": "A-101", "status": "sold", "pos_x": 50, "pos_y": 10,
         "width": 2, "height": 3, "poly": [[1, 2], [3, 4], [5, 6]],
         "floors": {"floor_number": 1, "buildings": {
             "code": "A", "projects": {"slug": "embassy-world"}}}},
    ]
    _verified_plan(monkeypatch)
    calls = _live_on(monkeypatch, rows)
    out = units.show_plan(1, building="f")
    assert calls[0]["floors.floor_number"] == "eq.1"
    assert "floors!inner" in calls[0]["select"],         "without !inner the floor filter silently returns every floor"
    assert out["counts"] == {"available": 1, "reserved": 0, "sold": 0},         "the spoken counts are the asked building's only"
    marks = {m["no"]: m for m in out["units"]}
    assert marks["A-101"]["dim"] is True and marks["F-101"]["dim"] is False
    assert marks["A-101"]["poly"], "rotated rooms keep their polygon"
    assert out["image"].endswith("/fpg1.webp")
    # And the policy holds here too: nothing about prices in the payload.
    assert "price" not in str(out).lower() or "ห้ามพูดราคา" in out["instruction"]


def test_a_floor_with_no_image_says_which_floors_exist(monkeypatch):
    _live_on(monkeypatch, [])
    out = units.show_plan(99)
    assert out["ok"] is False and out["error"] == "no plan image"
    assert "1" in out["instruction"], "name the floors that do exist"


def test_a_capped_list_is_never_spoken_as_the_total(monkeypatch):
    """Said on screen: "ทั้งหมด 8 ห้อง" about a budget that fits 105 — the
    query cap spoken as the answer. The budget page is 48 rows now: a full
    page still hedges, and fewer than a page IS the exact total, said as
    one."""
    calls = _live_on(monkeypatch, [LIVE_ROW] * 48)
    full = units.find_units(max_price_thb=10_000_000)
    assert full["capped"] is True and full["total"] is None
    assert "อาจมีมากกว่านี้" in full["instruction"]

    calls.clear()
    monkeypatch.setattr(units, "_live_get", lambda p: [LIVE_ROW] * 3)
    few = units.find_units(max_price_thb=10_000_000)
    assert few["capped"] is False and few["total"] == 3
    assert "ทั้งหมด 3 ห้องในงบ" in few["instruction"]


def test_the_boot_banner_tells_the_truth_about_the_link(monkeypatch):
    """Three states, three different mornings: off (file chain), live (with
    the unit count), and configured-but-unreachable — the one that already
    happened via a paste-damaged key, producing a robot that apologised for
    the sales system being down while it was fine."""
    monkeypatch.setattr(settings, "inventory_url", "")
    ok, msg = units.live_probe()
    assert ok and "OFF" in msg

    monkeypatch.setattr(settings, "inventory_url", "https://x.supabase.co")
    monkeypatch.setattr(settings, "inventory_key", "k")

    class R:
        headers = {"content-range": "0-0/1082"}

        def raise_for_status(self):
            pass

    import sys
    import types as t

    fake = t.ModuleType("httpx")
    fake.get = lambda *a, **kw: R()
    monkeypatch.setitem(sys.modules, "httpx", fake)
    ok, msg = units.live_probe()
    assert ok and "1082" in msg and "LIVE" in msg

    def boom(*a, **kw):
        raise ConnectionError("no route")

    fake.get = boom
    ok, msg = units.live_probe()
    assert not ok and "UNREACHABLE" in msg and "ConnectionError" in msg


def _live_row(no, msize, price):
    row = dict(LIVE_ROW)
    row.update({"unit_no": no, "msize": msize, "promo_price": price,
                "base_price": price})
    return row


def test_a_budget_question_reads_best_first_with_a_size_spread(monkeypatch):
    """Measured live, 2026-08-27: "มีเงิน 10 ล้าน ซื้อห้องไหนได้บ้าง" showed
    eight 25sqm studio twins — promo_price.asc put the whole answer in the
    cheapest band while the budget actually reached 59 LAGOON units of
    52sqm that were never shown. With a budget cap the query flips to
    descending, pulls the cheap end too when the page is full, and picks
    one unit per size before filling — the guest is choosing a room type,
    not a room number."""
    big = [_live_row(f"D-1{i:02d}", 52.0, 7_000_000 - i * 1000) for i in range(48)]
    small = [_live_row(f"F-1{i:02d}", 25.0, 2_500_000 + i * 1000) for i in range(48)]
    calls = []

    def fake_get(params):
        calls.append(dict(params))
        return big if "desc" in params["order"] else small

    from app.config import settings

    monkeypatch.setattr(settings, "inventory_url", "https://x.supabase.co")
    monkeypatch.setattr(settings, "inventory_key", "k")
    monkeypatch.setattr(units, "_live_get", fake_get)

    out = units.find_units(max_price_thb=10_000_000)
    assert calls[0]["order"] == "promo_price.desc"
    assert calls[0]["limit"] == "48"
    assert calls[1]["order"] == "promo_price.asc", \
        "a full page must fetch the cheap end so the spread covers the range"
    sizes = {u["sqm"] for u in out["units"]}
    assert sizes == {52.0, 25.0}, "one per size first — not eight twins"
    # Prices are stripped by policy before leaving the server, so order is
    # asserted through what survives: the 52sqm top end leads, the cheap
    # end trails.
    assert out["units"][0]["sqm"] == 52.0, "best-first for a budget"
    assert out["units"][-1]["room"].startswith("F-"), "cheap end included, last"
    assert out["capped"] is True and out["total"] is None
    assert "คละขนาด" in out["instruction"]


def test_a_short_budget_answer_reports_its_exact_total(monkeypatch):
    """Fewer rows than the page means the list IS the answer — say the real
    total instead of hedging. The cap once got spoken as the total; the
    reverse (a known total hedged as 'maybe more') is the same dishonesty
    mirrored."""
    rows = [_live_row("B-101", 52.0, 6_900_000), _live_row("B-102", 34.0, 3_900_000)]
    from app.config import settings

    monkeypatch.setattr(settings, "inventory_url", "https://x.supabase.co")
    monkeypatch.setattr(settings, "inventory_key", "k")
    monkeypatch.setattr(units, "_live_get", lambda p: list(rows))

    out = units.find_units(max_price_thb=10_000_000)
    assert out["total"] == 2
    assert "ทั้งหมด 2 ห้องในงบ" in out["instruction"]


def test_no_budget_keeps_the_cheapest_first_default(monkeypatch):
    """No cap keeps the query order but does not make price part of speech."""
    calls = _live_on(monkeypatch, [LIVE_ROW] * 8)
    out = units.find_units(building="a")
    assert calls[0]["order"] == "promo_price.asc"
    assert calls[0]["limit"] == "8"
    assert "ราคา" not in out["instruction"], "a non-budget request must not introduce price"
    assert out["total"] is None


def test_the_unit_list_renders_cards_and_the_plan_labels_sold():
    """The owner's ask, 2026-08-27: the flat row list was hard to read from
    standing distance, and the plan "ยังไม่เหมือน" the sales team's own —
    theirs prints SOLD across the box, ours only tinted it. Cards in a
    grid; HTML labels (not SVG text — the stretched viewBox distorts
    glyphs)."""
    from pathlib import Path

    src = (Path(__file__).parent.parent / "client" / "index.html").read_text(
        encoding="utf-8")
    lst = src[src.index("function showUnitList"):]
    lst = lst[:lst.index("function showPlan")] if "function showPlan" in lst else lst[:6000]
    assert "repeat(auto-fill" in lst, "the list must be a card grid"

    plan = src[src.index("function showPlan"):]
    plan = plan[:plan.index("function showFrame")] if "function showFrame" in plan else plan[:8000]
    assert "'SOLD'" in plan and "'จอง'" in plan
    assert "document.createElementNS(NS, 'text')" not in plan, \
        "SVG text stretches with the viewBox — labels are HTML"


# ============ tap-to-reveal prices: two nationalities, zero model access ============


def test_price_pair_maps_the_pricelist_columns_correctly(monkeypatch):
    """Decoded from the pricing engine itself (condo-inventory
    lib/pricing.js): promo_price is Price CN/TN — the THAI price, pricelist
    column R — and base_price is Price FN, the FOREIGNER price, column S.
    The sales app's old card labelled them "ตั้ง/โปรฯ", a discount that
    never existed. Swapping these two silently misquotes every customer of
    the wrong nationality, so the mapping is pinned."""
    calls = _live_on(monkeypatch, [
        {"unit_no": "A-801", "promo_price": 2_790_000, "base_price": 3_190_000,
         "msize": 33.5, "floors": {"buildings": {
             "code": "A", "projects": {"slug": "embassy-world"}}}},
    ])
    out = units.price_pair("A801")
    assert out == {"room": "A-801", "thai": 2_790_000,
                   "foreign": 3_190_000, "sqm": 33.5}
    assert calls[0]["unit_no"] == "eq.A-801"

    # Bare numbers never guess a building; no live link means no answer.
    assert units.price_pair("801") is None
    from app.config import settings
    monkeypatch.setattr(settings, "inventory_url", "")
    assert units.price_pair("A801") is None


def test_price_pair_is_not_a_tool():
    """The whole design: prices reach the browser through /unit_price on a
    human's tap and NEVER through a tool result — the model cannot read a
    number it never received. price_pair registered as a tool would undo
    the standing ห้ามโชว์ราคา order in one decorator."""
    from app.tools import load_tools, registry

    load_tools()
    assert "price_pair" not in registry._REGISTRY
    assert "unit_price" not in registry._REGISTRY


def test_the_price_endpoint_sits_behind_the_same_token_gate(monkeypatch):
    """This endpoint hands out money figures; WS_TOKEN guards every socket
    for the same reason, and 'some browser on the LAN' must not be enough
    here either."""
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "ws_token", "s3cret")
    monkeypatch.setattr(units, "price_pair",
                        lambda room: {"room": room, "thai": 1, "foreign": 2,
                                      "sqm": 3.0})
    client = TestClient(app)
    assert client.get("/unit_price", params={"room": "A801"}).status_code == 403
    assert client.get("/unit_price",
                      params={"room": "A801", "token": "wrong"}).status_code == 403
    ok = client.get("/unit_price", params={"room": "A801", "token": "s3cret"})
    assert ok.status_code == 200 and ok.json()["thai"] == 1

    monkeypatch.setattr(units, "price_pair", lambda room: None)
    assert client.get("/unit_price",
                      params={"room": "Z999", "token": "s3cret"}).status_code == 404


def test_the_reveal_buttons_never_reach_the_kiosk():
    """The robot's chest screen is the most public surface in the building
    and a passer-by's tap is not a sales decision. The buttons live in the
    live-source branch only, gated on body.kiosk, and the numbers come from
    /unit_price at tap time — the tool payload stays priceless."""
    from pathlib import Path

    src = (Path(__file__).parent.parent / "client" / "index.html").read_text(
        encoding="utf-8")
    fn = src[src.index("function showUnit("):]
    fn = fn[:fn.index("function showUnitList")]
    assert "ราคาคนไทย" in fn and "ราคาต่างชาติ" in fn
    assert "classList.contains('kiosk')" in fn
    assert "/unit_price?room=" in fn
    assert "u.source === 'live'" in fn


# ============ one project, not the whole inventory ============


def test_every_live_read_is_scoped_to_our_project(monkeypatch):
    """The sales team's inventory holds three projects since 2026-09-03
    (Embassy World, Embassy Life, Embassy One) with the same building codes
    and overlapping unit numbers. Measured 2026-09-11: the gallery screen
    listed A-1405 as one of ours — an Embassy Life unit. Every read carries
    the project filter, and the select embeds the path `!inner` at each hop
    (a filter on a left embed keeps the row and nulls the embed instead of
    dropping it — the floor-filter lesson, one level deeper)."""
    calls = []

    def fake_get(params, table="units"):
        calls.append((table, dict(params)))
        return []

    monkeypatch.setattr(settings, "inventory_url", "https://x.supabase.co")
    monkeypatch.setattr(settings, "inventory_key", "k")
    monkeypatch.setattr(settings, "inventory_project", "embassy-world")
    monkeypatch.setattr(units, "_live_get", fake_get)
    _verified_plan(monkeypatch)
    units._live_cache.clear()

    units.show_unit("A1405")
    units.find_units()
    units.find_units(max_price_thb=5_000_000)
    units.show_plan(1)
    units.compare_unit_types()
    units.list_promotions()
    units.price_pair("A1405")
    units._active_promotion("A-1405")
    assert len(calls) >= 8, "every live entry point above must reach the DB"
    for table, params in calls:
        via = "units." if table == "pricing_unit_promotions" else ""
        assert params.get(via + "floors.buildings.projects.slug") == "eq.embassy-world", \
            (table, params)
        sel = params["select"]
        assert "floors!inner(" in sel and "buildings!inner(" in sel \
            and "projects!inner(slug)" in sel, (table, sel)


def test_inventory_setting_cannot_cross_the_active_project(monkeypatch):
    monkeypatch.setattr(settings, "inventory_project", "embassy-life")
    with pytest.raises(ValueError, match="does not match"):
        units._scoped({"select": "id"})


def test_a_blank_project_setting_means_ours_not_everything():
    """An unset knob must narrow, never widen (WS_TOKEN="" once meant "no
    door"). Blank INVENTORY_PROJECT is Embassy World. A subprocess, because
    Settings reads the environment at import and reloading app.config in
    this process would hand every other test a second `settings` object."""
    import os
    import subprocess
    import sys

    env = {**os.environ, "INVENTORY_PROJECT": "   "}
    out = subprocess.run(
        [sys.executable, "-c",
         "from app.config import settings; print(settings.inventory_project)"],
        env=env, capture_output=True, text=True, encoding="utf-8", timeout=60,
        cwd=str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    assert out.returncode == 0, out.stderr[-800:]
    assert out.stdout.strip() == "embassy-world"
