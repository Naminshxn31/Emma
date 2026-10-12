"""
Per-unit plan crops on the unit card (2026-10-06).

The sales team's furniture sheets — one PDF per unit — are cropped to the
plan drawing by scripts/build_unit_plans.py and served by app/unit_plans.py.
The failure everything here is shaped around: the wrong room's plan on a
card a customer can photograph. So the builder refuses a sheet whose own
title names another room, and the server refuses any image whose index
entry or bytes no longer agree with what the builder wrote.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from app import unit_plans
from app.config import settings
from app.tools import units

ROOT = Path(__file__).resolve().parents[1]


def _builder():
    spec = importlib.util.spec_from_file_location(
        "build_unit_plans", ROOT / "scripts" / "build_unit_plans.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sheet(title: str) -> bytes:
    """An A3-landscape sheet shaped like the sales team's: title top left,
    a vector plan in the middle, a furniture panel on the right."""
    fitz = pytest.importorskip("fitz", reason="PyMuPDF missing: the builder is not being tested")
    doc = fitz.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text((60, 95), title, fontsize=14)
    page.draw_rect(fitz.Rect(300, 150, 600, 500), width=3)       # the room
    page.draw_line((300, 350), (450, 350), width=2)              # a wall
    page.draw_rect(fitz.Rect(500, 400, 590, 490), width=1)       # bathroom
    page.draw_rect(fitz.Rect(900, 150, 1150, 600), width=1)      # furniture panel
    page.draw_rect(fitz.Rect(20, 20, 1170, 822), width=1)        # page frame
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def plans(tmp_path, monkeypatch):
    """A built folder (two real sheets through the real builder, one of them
    misnamed) with the runtime pointed at it."""
    builder = _builder()
    src = tmp_path / "Furniture PDF" / "A"
    src.mkdir(parents=True)
    (src / "A-201.pdf").write_bytes(_sheet("1 BEDROOM 33.5 SQ.M.(A-201)"))
    # Renamed by hand: the file says A-202, the sheet says A-203.
    (src / "A-202.pdf").write_bytes(_sheet("1 BEDROOM 33.5 SQ.M.(A-203)"))
    out = tmp_path / "unit-plans"
    index = builder.build(tmp_path / "Furniture PDF", out, settings.project_id, "test")
    monkeypatch.setattr(unit_plans, "_folder", lambda: out)
    unit_plans.reset()
    return out, index


def test_builder_refuses_a_sheet_whose_title_names_another_room(plans):
    out, index = plans
    assert index["count"] == 1 and list(index["units"]) == ["A-201"]
    assert index["skipped"] == [{"file": "A-202.pdf", "title": "1 BEDROOM 33.5 SQ.M.(A-203)",
                                 "reason": "title says A-203, file says A-202"}]
    assert not (out / "A-202.webp").exists() and not (out / "A-203.webp").exists()


def test_builder_crops_to_the_plan_not_the_sheet(plans):
    from PIL import Image

    out, _ = plans
    with Image.open(out / "A-201.webp") as image:
        w, h = image.size
    # The drawing is 300x350 pt; at 150 dpi plus a small margin that is
    # ~650x730 px. The whole sheet would be ~2480x1750.
    assert 600 < w < 700 and 650 < h < 760, (w, h)


def test_the_card_gets_the_plan_by_its_hash(plans):
    out, index = plans
    sha = index["units"]["A-201"]["sha256"]
    assert sha == hashlib.sha256((out / "A-201.webp").read_bytes()).hexdigest()
    assert unit_plans.plan_url("A-201") == f"/unit-plan/{sha}"
    assert unit_plans.plan_body(sha) == (out / "A-201.webp").read_bytes()
    assert unit_plans.plan_url("A-202") is None, "the misnamed sheet must not appear"


def test_bytes_changed_after_the_build_are_not_served(plans):
    out, index = plans
    sha = index["units"]["A-201"]["sha256"]
    (out / "A-201.webp").write_bytes(b"RIFF....WEBP another room")
    assert unit_plans.plan_url("A-201") is None
    assert unit_plans.plan_body(sha) is None


def test_an_index_pointing_one_room_at_another_rooms_file_is_refused(plans):
    out, index = plans
    raw = json.loads((out / "index.json").read_text(encoding="utf-8"))
    raw["units"]["A-301"] = dict(raw["units"]["A-201"])     # same file, other room
    (out / "index.json").write_text(json.dumps(raw), encoding="utf-8")
    unit_plans.reset()
    assert unit_plans.plan_url("A-301") is None
    assert unit_plans.plan_url("A-201") is not None


def test_an_index_from_another_project_is_ignored(plans):
    out, _ = plans
    raw = json.loads((out / "index.json").read_text(encoding="utf-8"))
    raw["project_id"] = "embassy_life"
    (out / "index.json").write_text(json.dumps(raw), encoding="utf-8")
    unit_plans.reset()
    assert unit_plans.plan_url("A-201") is None


def test_no_folder_keeps_the_labelled_blank(monkeypatch):
    # conftest already points the folder at nothing; this is the CI machine
    # and every sales-room machine that never ran the builder.
    assert unit_plans.plan_url("A-201") is None
    assert unit_plans.plan_body("0" * 64) is None


UNIT = {
    "id": "u-a201", "unit_no": "A-201", "size_sqm": 33.4, "msize": 33.5,
    "view": "POOLABC", "side": "SOUTH", "collection": "LAGOON", "unit_option": None,
    "base_price": 3690000.0, "promo_price": 3290000.0, "status": "available",
    "note": None, "updated_at": "2026-09-30T03:45:47+00:00",
    "floors": {"floor_number": 2, "buildings": {
        "code": "A", "projects": {"slug": "embassy-world"}}},
    "unit_types": {"name": "1 Bedroom"},
}


@pytest.fixture
def live(monkeypatch):
    rows = {"A-201": UNIT, "A-202": {**UNIT, "id": "u-a202", "unit_no": "A-202"}}

    def fake_get(params, table="units"):
        if table != "units":
            return []
        wanted = str(params.get("unit_no", "")).removeprefix("eq.")
        return [rows[wanted]] if wanted in rows else []

    monkeypatch.setattr(settings, "inventory_url", "https://x.supabase.co")
    monkeypatch.setattr(settings, "inventory_key", "k")
    monkeypatch.setattr(units, "_live_get", fake_get)
    units._live_cache.clear()


def test_show_unit_puts_the_verified_plan_on_the_card(plans, live):
    _, index = plans
    out = units.show_unit("A201")
    assert out["ok"] and out["unit"]["floorplan"] == (
        "/unit-plan/" + index["units"]["A-201"]["sha256"])
    other = units.show_unit("A202")
    assert other["ok"] and "floorplan" not in other["unit"], \
        "a room without a verified sheet keeps the blank"


def test_the_plan_endpoint_sits_behind_the_token_gate(plans, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    out, index = plans
    sha = index["units"]["A-201"]["sha256"]
    monkeypatch.setattr(settings, "ws_token", "s3cret")
    client = TestClient(app)
    assert client.get(f"/unit-plan/{sha}").status_code == 403
    assert client.get(f"/unit-plan/{sha}", params={"token": "wrong"}).status_code == 403
    ok = client.get(f"/unit-plan/{sha}", params={"token": "s3cret"})
    assert ok.status_code == 200 and ok.headers["content-type"] == "image/webp"
    assert ok.content == (out / "A-201.webp").read_bytes()
    assert client.get("/unit-plan/" + "0" * 64, params={"token": "s3cret"}).status_code == 404
    assert client.get("/unit-plan/../index.json", params={"token": "s3cret"}).status_code == 404


def test_the_page_sends_the_token_only_to_its_own_plan_path():
    """The <img> for the plan cannot carry a header, so the token rides in
    the URL — and must never be attached to somebody else's URL."""
    for name in ("index.html", "voice-preview.html"):
        source = (ROOT / "client" / name).read_text(encoding="utf-8")
        start = source.index("if (u.floorplan)")
        block = source[start:start + 900]
        assert "/unit-plan/" in block and "location.origin" in block, name
