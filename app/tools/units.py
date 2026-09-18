"""
The unit card: a screen of our own HTML instead of somebody else's website.

Two problems solved at once. Embedding third-party pages runs into
`X-Frame-Options` on most of the web (see `webstage.py`), and even where it
works the result is a desktop website on a touch panel with no keyboard. A
page this project renders itself has neither problem: same origin, so it
always frames; our design, so it can be read standing two metres away.

**The model chooses which unit, never what it costs.** That distinction is
the whole safety design, and it is the same one `data/documents/catalogue.json`
already makes for printing: the assistant picks from a list the sales team
owns, it does not compose the contents. A tool shaped
`show_unit(room="A801", price="3.29 ล้าน")` would hand
"ห้ามแต่งข้อมูลโครงการเองเด็ดขาด" — the project's oldest rule — to a machine
that mishears numbers, and put the result on a screen a customer can
photograph.

So the tool takes a room number, looks it up, and fails when it is not
there. A room that is not in the file has no price, and no price is the
correct answer.

Sample data exists (`data/units.sample.json`) for UI development. It is off
by default and the customer-facing tool now refuses to return its contents,
even when the development switch is on.
"""
from __future__ import annotations

import re
import hashlib

import json
import logging
from pathlib import Path
from datetime import datetime, timezone

from app import turnlog
from app.config import settings
from app.runtime_policy import (
    INVENTORY_SOURCE_ID, RuntimeResult, prepare_inventory_card,
    prepare_plan_asset, prepare_static_inventory_card,
)
from app.tools.registry import tool

logger = logging.getLogger("condo_voice.units")

_cache: dict | None = None
_cache_path: str | None = None
_cache_stamp: bytes | None = None
_cache_project: str | None = None


def _table_path() -> tuple[Path | None, bool]:
    """(file to read, whether it is the sample). (None, _) = no table at all.

    Split out from `_load` so a test can put a file with no `"sample"` flag
    on the fallback path and prove it is still marked — the case the flag
    alone cannot cover.
    """
    real = Path(settings.units_file).expanduser()
    if real.is_file():
        return real, False
    if not settings.units_sample:
        return None, False
    sample = Path(__file__).resolve().parent.parent.parent / "data" / "units.sample.json"
    return (sample, True) if sample.is_file() else (None, False)


def _load() -> dict:
    """Load the local table if it exists, else the development sample.

    Never both, and never a merge: a card built half from signed prices and
    half from invented ones is worse than either, because nothing on it says
    which half you are looking at.
    """
    global _cache, _cache_path, _cache_stamp, _cache_project

    path, fell_back = _table_path()
    if path is None:
        return {}

    try:
        content = path.read_bytes()
    except OSError:
        return {}
    stamp = hashlib.sha256(content).digest()
    if (_cache is not None and _cache_path == str(path)
            and _cache_stamp == stamp and _cache_project == settings.project_id):
        return _cache
    try:
        data = json.loads(content)
    except (OSError, ValueError):
        logger.exception("could not read %s", path)
        return {}
    if not fell_back:
        from app.data_sources import require_project_payload

        try:
            require_project_payload(data, "local_unit_inventory", settings.project_id)
        except ValueError:
            logger.error("local unit table has wrong project/source metadata: %s", path)
            return {}
    # Sample-ness is a property of *where the file came from*, not of a flag
    # inside it and not of the setting that allowed it. Two consequences,
    # both deliberate: a real table dropped into place stops being a sample
    # the moment it exists, with nobody remembering to flip anything; and a
    # fallback file that has lost its own `"sample": true` — copied, edited,
    # hand-written — is still treated as one. The flag is a courtesy; the
    # path is the fact.
    data["sample"] = bool(data.get("sample")) or fell_back
    _cache, _cache_path, _cache_stamp, _cache_project = (
        data, str(path), stamp, settings.project_id)
    return data


def reset() -> None:
    """Tests and a reload after the sales team drops the real file in."""
    global _cache, _cache_path, _cache_stamp, _cache_project
    _cache = _cache_path = _cache_stamp = _cache_project = None
    _live_cache.clear()
    _PLAN_VERIFIED.clear()


_STATUS_TH = {"available": "ว่าง", "reserved": "จอง", "sold": "ขายแล้ว"}


@tool(
    name="show_unit",
    description=(
        "แสดงการ์ดข้อมูลห้องบนจอ ใช้เมื่อลูกค้าถามถึงห้องใดห้องหนึ่งด้วยเลขห้อง "
        "เช่น 'ขอดูห้อง A801' 'ห้อง B305 ราคาเท่าไหร่' "
        "ใส่เฉพาะเลขห้องเท่านั้น ข้อมูลอื่นทั้งหมดระบบดึงจากตารางที่ฝ่ายขายอนุมัติเอง "
        "ถ้าไม่มีห้องนั้นในตาราง จะได้ error กลับมา ให้บอกลูกค้าตรงๆ ว่าไม่มีข้อมูล "
        "และให้ติดต่อฝ่ายขาย ห้ามเดาราคาหรือรายละเอียดห้องเองเด็ดขาด"
    ),
    parameters={
        "type": "object",
        "properties": {
            "room": {"type": "string", "description": "เลขห้อง เช่น A801"},
        },
        "required": ["room"],
    },
    tags=["units"],
    blocking=True,
)
def show_unit(room: str) -> dict:
    if live_configured():
        return show_unit_live(room)
    data = _load()
    if not data:
        return {"ok": False, "error": "no unit table",
                "instruction": ("ยังไม่มีตารางยูนิตในระบบ ให้บอกลูกค้าตรงๆ ว่ายังไม่มีข้อมูล "
                                "และให้ติดต่อฝ่ายขาย ห้ามเดาราคาหรือขนาดห้อง")}
    # Never hand a sample or an unsigned static export to a customer-facing
    # model, even when a development switch makes that file loadable.
    source_probe = prepare_static_inventory_card(
        data, {}, expected_project_id=settings.project_id,
        allow_price=settings.units_show_price)
    if not source_probe.allowed:
        logger.info("runtime inventory policy %s", source_probe.trace())
        return {"ok": False, "error": "inventory not disclosable",
                "policy_trace": source_probe.trace(),
                "instruction": "ข้อมูลห้องชุดนี้ยังไม่อนุญาตให้แจ้งลูกค้า ให้ติดต่อฝ่ายขาย ห้ามใช้ข้อมูลตัวอย่างหรือข้อมูลที่ยังไม่อนุมัติ"}
    normalized = (room or "").strip().upper().replace(" ", "")
    unit = next((u for u in data.get("units", [])
                 if str(u.get("room", "")).strip().upper() == normalized), None)
    if unit is None:
        rooms = [u.get("room") for u in data.get("units", [])][:8]
        return {"ok": False, "error": "unknown room", "asked": room,
                "rooms_available": rooms,
                "instruction": ("ไม่มีห้องนี้ในตาราง ให้บอกลูกค้าตรงๆ ว่าไม่พบเลขห้องนี้ "
                                "ถามเลขห้องซ้ำอีกครั้ง ห้ามแต่งข้อมูลห้องขึ้นมาเอง")}

    sample = bool(data.get("sample"))
    card = dict(unit)
    card["project_id"] = settings.project_id
    card["status_th"] = _STATUS_TH.get(str(unit.get("status", "")).lower(), "")
    card["sample"] = sample
    card["approved_by"] = data.get("approved_by") or ""
    card["effective_from"] = data.get("effective_from") or ""
    turnlog.record("show_unit", room=card.get("room"), sample=sample)

    prepared = prepare_static_inventory_card(
        data, card, expected_project_id=settings.project_id,
        allow_price=settings.units_show_price)
    logger.info("runtime inventory policy %s", prepared.trace())
    if not prepared.allowed:
        return {"ok": False, "error": "inventory not disclosable",
                "policy_trace": prepared.trace()}
    return {"ok": True, "screen": "unit", "unit": prepared.payload,
            "sample": False, "policy_trace": prepared.trace(),
            "instruction": "บอกข้อมูลที่อนุมัติแล้วตามการ์ดสั้นๆ ไม่ต้องอ่านทุกฟิลด์"}


# ==================== the live inventory link ====================
#
# condo-inventory (Supabase/PostgreSQL) is the sales team's own system: the
# admin UI flips statuses, price_history/status_history record who did what,
# and the floor plan the sales desk stares at renders from the same rows.
# When the link is configured, the card answers from there — a static
# "available" that stopped being true yesterday was the biggest worry item 8
# carried, and the fix is to not have a copy at all.

import time as _time

#: room -> (monotonic stamp, card). Short TTL; see settings.inventory_cache_s.
_live_cache: dict[tuple[str, str], tuple[float, dict]] = {}


def live_configured() -> bool:
    return bool(settings.inventory_url and settings.inventory_key)


def _normalize_room(room: str) -> str | None:
    """"A801", "a-801", "A 801" -> "A-801" (the DB's unit_no shape).

    Guests speak room numbers without the dash; the pricelist writes them
    with it. Bare digits ("801") are refused rather than guessed: two
    buildings can share a floor plan, and the wrong building's 801 shown
    confidently is worse than one clarifying question.
    """
    import re

    q = (room or "").strip().upper().replace(" ", "").replace("-", "")
    m = re.fullmatch(r"([A-Z]+)(\d+)", q)
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}"


def _live_get(params: dict, table: str = "units") -> list[dict]:
    """One PostgREST read. Its own function so tests can stand it in."""
    import httpx

    headers = {"apikey": settings.inventory_key,
               "Authorization": f"Bearer {settings.inventory_key}"}
    r = httpx.get(f"{settings.inventory_url}/rest/v1/{table}", params=params,
                  headers=headers, timeout=8)
    r.raise_for_status()
    return r.json()


#: The join path from a unit to its project, `!inner` at every hop. The
#: inventory holds three projects (Embassy World / Life / One) with the same
#: building codes and overlapping unit numbers, so every read below is
#: scoped to settings.inventory_project — and a filter on a *left* embed does
#: not drop the parent row, it nulls the embed and keeps the unit: without
#: `!inner` the scope would be decorative. Measured 2026-09-11 before this:
#: the unit list on the gallery screen showed A-1405, an Embassy Life unit.
_SCOPE_EMBED = "floors!inner(floor_number,buildings!inner(code,projects!inner(slug)))"
_SCOPE_KEY = "floors.buildings.projects.slug"


def _scoped(params: dict, *, via: str = "") -> dict:
    """Add the project filter to one PostgREST query (mutates and returns).

    `via` is the embed prefix when the table is reached *through* units
    (promotions: "units."). The select must embed _SCOPE_EMBED on that same
    path or PostgREST rejects the filter — the test that checks every live
    read carries both is what keeps a new query from quietly widening.
    """
    from app.data_sources import inventory_slug

    expected = inventory_slug(settings.project_id)
    if settings.inventory_project != expected:
        raise ValueError("inventory project does not match the active project")
    params[f"{via}{_SCOPE_KEY}"] = f"eq.{expected}"
    return params


_LIVE_SELECT = ("id,unit_no,size_sqm,msize,view,side,collection,unit_option,"
                "base_price,promo_price,status,note,updated_at,"
                + _SCOPE_EMBED + ",unit_types(name)")

#: The sales team's per-unit promotions (condo-inventory, 27 Aug 2026):
#: `pricing_unit_promotions` is a separate table — a promotion never
#: overwrites the master prices — applied by an admin, lately from the
#: promo inbox (Gmail → local model → review → apply). Emma reads the
#: *result* of that workflow and nothing upstream of it.
_PROMO_SELECT = ("unit_id,active,thai_price,foreign_price,note,updated_at,"
                 "units!inner(id,unit_no,status,msize,size_sqm,view,side,unit_option,"
                 "collection,base_price,promo_price,updated_at,"
                 + _SCOPE_EMBED + ",unit_types(name))")


def _card_from_live(row: dict) -> dict:
    """DB row -> the card the screen already renders.

    promo_price is the selling price (TN/CN on the pricelist); base_price is
    the list price. Both are carried so the card can show the discount — but
    the model is told the promo price is *the* price.
    """
    floors = row.get("floors") or {}
    project_slug = ((floors.get("buildings") or {}).get("projects") or {}).get("slug")
    building = (floors.get("buildings") or {}).get("code") or ""
    unit_type = (row.get("unit_types") or {}).get("name") or ""
    price = row.get("promo_price") or row.get("base_price")
    return {
        "project_id": settings.project_id,
        "source_id": "live_unit_inventory",
        "_project_slug": project_slug,
        "_fetched_at": datetime.now(timezone.utc),
        "id": row.get("id"),
        "room": row.get("unit_no"),
        "building": building,
        "floor": floors.get("floor_number"),
        "type": unit_type,
        "sqm": row.get("msize") or row.get("size_sqm"),
        "aspect": " ".join(x for x in (row.get("view"), row.get("unit_option")) if x),
        "collection": row.get("collection"),
        "price_thb": price,
        "base_price_thb": row.get("base_price"),
        "status": row.get("status"),
        "status_th": _STATUS_TH.get(str(row.get("status", "")).lower(), ""),
        "photo": "", "floorplan": "",
        "sample": False,
        "source": "live",
        "updated_at": (row.get("updated_at") or "")[:16].replace("T", " "),
    }


def _live_find(room: str) -> dict | None:
    unit_no = _normalize_room(room)
    if unit_no is None:
        return None
    now = _time.monotonic()
    key = (settings.project_id, unit_no)
    hit = _live_cache.get(key)
    if hit and now - hit[0] < settings.inventory_cache_s:
        return hit[1]
    rows = _live_get(_scoped({"select": _LIVE_SELECT, "unit_no": f"eq.{unit_no}",
                              "limit": "1"}))
    if not rows:
        return None
    card = _card_from_live(rows[0])
    _live_cache[key] = (now, card)
    return card


def price_pair(room: str) -> dict | None:
    """Both nationalities' prices for one unit — the tap-to-reveal card.

    Deliberately a plain function, NOT a @tool: the standing order is that
    prices never pass through the model, and this path honours it — the
    numbers travel HTTP endpoint → browser on a human's tap, while the tool
    results Gemini reads stay stripped by runtime policy. (Do not move
    this between a @tool decorator and its def — the decorator-twin bug.)

    Field naming decoded from the pricing engine (condo-inventory
    lib/pricing.js): promo_price is Price CN/TN — the THAI price, pricelist
    column R — and base_price is Price FN, the FOREIGNER price, column S.
    The sales app's old card showed them as "ตั้ง/โปรฯ", a discount that
    never existed.
    """
    if not live_configured():
        return None
    unit_no = _normalize_room(room)
    if unit_no is None:
        return None
    try:
        rows = _live_get(_scoped({"select": "unit_no,promo_price,base_price,msize,"
                                            + _SCOPE_EMBED,
                                  "unit_no": f"eq.{unit_no}", "limit": "1"}))
    except Exception:
        logger.exception("price_pair: live inventory unreachable")
        return None
    if not rows:
        return None
    r = rows[0]
    prepared = _prepared_live_card(_card_from_live(r), allow_price=False)
    if not prepared.allowed:
        logger.warning("price_pair blocked by inventory policy %s", prepared.trace())
        return None
    pair = {"room": r.get("unit_no"), "thai": r.get("promo_price"),
            "foreign": r.get("base_price"), "sqm": r.get("msize")}
    # The active promotion rides along on the same tap: the person who
    # pressed wanted the number that applies today, and the sales app's
    # own quotation defaults to the promotion price when there is one.
    try:
        promo = _active_promotion(r.get("unit_no"))
    except Exception:
        logger.exception("price_pair: promotion lookup failed")
        promo = None
    if promo:
        pair.update(promo_thai=promo.get("thai_price"),
                    promo_foreign=promo.get("foreign_price"))
    return pair


def _active_promotion(unit_no: str | None) -> dict | None:
    """The active row of `pricing_unit_promotions` for one unit, or None."""
    if not unit_no or not live_configured():
        return None
    rows = _live_get(_scoped({"select": "unit_id,active,thai_price,foreign_price,note,updated_at,"
                                        "units!inner(unit_no,status," + _SCOPE_EMBED + ")",
                              "active": "eq.true", "units.unit_no": f"eq.{unit_no}",
                              "limit": "1"}, via="units."),
                     table="pricing_unit_promotions")
    if not rows:
        return None
    if rows[0].get("active") is not True:
        return None
    unit = rows[0].get("units") or {}
    if unit.get("status") == "sold":
        return None
    prepared = _prepared_live_card(_card_from_live(unit), allow_price=False)
    return rows[0] if prepared.allowed else None


def _prepared_live_card(card: dict, *, allow_price: bool | None = None):
    prepared = prepare_inventory_card(
        card, expected_project_id=settings.project_id,
        max_age_seconds=settings.inventory_cache_s,
        allow_price=(settings.units_show_price if allow_price is None else allow_price),
    )
    logger.info("runtime inventory policy %s", prepared.trace())
    return prepared


def _blocked_inventory(prepared) -> dict:
    # The rejected card never enters the tool result, even as a debug field.
    return {"ok": False, "error": "inventory policy blocked",
            "policy_trace": prepared.trace(),
            "instruction": "ข้อมูลห้องจากระบบขายยังตรวจขอบเขตหรือความสดไม่ได้ ให้ตรวจสอบกับฝ่ายขาย ห้ามตอบจากข้อมูลที่ถูกบล็อก"}


def _blocked_inventory_state(reason: str) -> dict:
    return _blocked_inventory(RuntimeResult(
        False, settings.project_id, INVENTORY_SOURCE_ID, reason, {}))


def show_unit_live(room: str) -> dict:
    """The live half of show_unit. Split out so each path stays readable."""
    if _normalize_room(room) is None:
        return {"ok": False, "error": "bad room number", "asked": room,
                "instruction": ("เลขห้องต้องมีตัวอักษรตึกนำหน้า เช่น A801 "
                                "ให้ถามลูกค้าว่าตึกไหนห้องเลขอะไร ห้ามเดาตึกเอง")}
    try:
        card = _live_find(room)
    except Exception:
        logger.exception("live inventory unreachable")
        # Honest, never quietly stale: falling back to a sample here would
        # put invented prices on screen at the exact moment nobody can check
        # them against the real system. Same rule as mock-is-not-ok.
        return {"ok": False, "error": "inventory unreachable",
                "instruction": ("เช็คสถานะสดจากระบบผังขายไม่ได้ตอนนี้ "
                                "ให้บอกลูกค้าตรงๆ ว่าขอตรวจสอบกับฝ่ายขายอีกครั้ง "
                                "ห้ามบอกสถานะหรือราคาจากความจำ")}
    if card is None:
        return {"ok": False, "error": "unknown room", "asked": room,
                "instruction": ("ไม่พบเลขห้องนี้ในระบบผังขาย ให้ทวนเลขห้องกับลูกค้า "
                                "อีกครั้ง ห้ามแต่งข้อมูลห้องขึ้นมาเอง")}
    prepared = _prepared_live_card(card)
    if not prepared.allowed:
        return _blocked_inventory(prepared)
    turnlog.record("show_unit", room=card["room"], sample=False, source="live")
    try:
        promo = _active_promotion(card["room"])
    except Exception:
        logger.exception("promotion lookup failed — card goes out without it")
        promo = None
    card = prepared.payload
    if promo:
        # A live promotion flag is operational data; its free-text note and
        # monetary figures need their own disclosure contract, not the unit
        # status policy. Do not hand either to Emma in this slice.
        card["promo"] = True
    if settings.units_show_price:
        note = ("ข้อมูลสดจากระบบผังขาย ณ ตอนนี้ สถานะ %s เป็นสถานะจริง "
                "ราคาที่แสดงคือราคาขายจริง บอกข้อมูลสั้นๆ ห้ามคำนวณหรือปัดราคาเอง"
                % (card.get("status_th") or card.get("status")))
    else:
        note = ("ข้อมูลสดจากระบบผังขาย ณ ตอนนี้ สถานะ %s เป็นสถานะจริง "
                "นโยบายคือไม่เปิดเผยตัวเลขราคา ถ้าลูกค้าถามราคา ให้บอกว่า "
                "ราคาและโปรโมชั่นล่าสุดขอให้คุยกับฝ่ายขายโดยตรง ห้ามพูดหรือเดาตัวเลขราคา"
                % (card.get("status_th") or card.get("status")))
    if promo:
        note += " ห้องนี้มีโปรโมชั่นในระบบ รายละเอียดและตัวเลขให้ฝ่ายขายยืนยัน"
    return {"ok": True, "screen": "unit", "unit": card, "sample": False,
            "policy_trace": prepared.trace(), "instruction": note}


@tool(
    name="find_units",
    description=(
        "ค้นห้องว่างจากระบบผังขายตามเงื่อนไข เช่น ขอห้อง 2 ห้องนอน ราคาไม่เกินสี่ล้าน "
        "ขอห้องวิวสระ หรือตึก A มีอะไรว่าง ใส่เฉพาะเงื่อนไขที่ลูกค้าพูดเองเท่านั้น "
        "ห้ามอนุมานหรือเติม building/view เอง ถ้าลูกค้าบอกเพียงจำนวนห้องนอนให้ใส่ bedrooms อย่างเดียว "
        "ผลลัพธ์คือรายการจริงจากระบบ ให้อ่านจากรายการเท่านั้น "
        "ห้ามแต่งห้องหรือราคาเพิ่ม ถ้ารายการว่างให้บอกตรงๆ ว่าไม่พบตามเงื่อนไข"
    ),
    parameters={
        "type": "object",
        "properties": {
            "max_price_thb": {"type": "number", "description": "ราคาไม่เกิน (บาท)"},
            "min_price_thb": {"type": "number", "description": "ราคาตั้งแต่ (บาท)"},
            "bedrooms": {
                "type": "integer",
                "minimum": 0,
                "maximum": 4,
                "description": "จำนวนห้องนอนที่ลูกค้าระบุเอง: 0 คือ Studio, 1 คือ 1 Bedroom, 2 คือ 2 Bedroom",
            },
            "building": {
                "type": "string",
                "description": "รหัสตึก เช่น A; ใส่เมื่อคำพูดลูกค้าระบุตึกเท่านั้น ห้ามเดาหรือใส่ค่าเริ่มต้น",
            },
            "view_contains": {"type": "string",
                              "description": "คำในชื่อวิว เช่น POOL, LAGOON; ใส่เมื่อคำพูดลูกค้าระบุวิวเท่านั้น ห้ามเดา"},
        },
    },
    tags=["units"],
    blocking=True,
)
def find_units(max_price_thb: float | None = None,
               min_price_thb: float | None = None,
               bedrooms: int | None = None,
               building: str | None = None,
               view_contains: str | None = None) -> dict:
    """Item 11 on the sales list, running against the real inventory.

    Availability is the default filter, not an option: the question this
    answers is "what can I buy", and a sold unit in that list is an
    embarrassment at the desk. The model may narrow further, never widen.
    """
    if not live_configured():
        return {"ok": False, "error": "no live inventory",
                "instruction": ("เครื่องนี้ยังไม่ได้ต่อระบบผังขาย ให้บอกลูกค้าว่า "
                                "ขอเช็ครายการห้องว่างกับฝ่ายขาย ห้ามแต่งรายการเอง")}
    # A budget question reads best-first: promo_price.asc answered "มีเงิน
    # 10 ล้าน ซื้อห้องไหนได้บ้าง" with the eight cheapest studios in the
    # building — measured 2026-08-27: the budget actually reached 59 LAGOON
    # units of 52sqm and the screen showed eight 25sqm twins instead. With
    # a cap, descending; without one, the cheapest entry point stays the
    # honest default and that path is byte-identical to before.
    descending = bool(max_price_thb)
    budget_filtered = min_price_thb is not None or max_price_thb is not None
    if bedrooms is not None and (isinstance(bedrooms, bool) or bedrooms not in range(5)):
        return {"ok": False, "error": "invalid bedroom count",
                "instruction": "จำนวนห้องนอนต้องเป็นศูนย์ถึงสี่ ให้ถามลูกค้าใหม่ ห้ามเดา"}
    select = (_LIVE_SELECT.replace("unit_types(name)", "unit_types!inner(name)")
              if bedrooms is not None else _LIVE_SELECT)
    params: dict = _scoped({"select": select, "status": "eq.available",
                            "order": ("promo_price.desc" if descending
                                      else "promo_price.asc"),
                            "limit": "48" if descending else "8"})
    price_parts = []
    if min_price_thb:
        price_parts.append(f"promo_price.gte.{int(min_price_thb)}")
    if max_price_thb:
        price_parts.append(f"promo_price.lte.{int(max_price_thb)}")
    if len(price_parts) == 1:
        params["promo_price"] = price_parts[0][len("promo_price."):]
    elif price_parts:
        # PostgREST takes one filter per key; ranges combine under `and=`.
        params["and"] = "(%s)" % ",".join(price_parts)
    if view_contains:
        params["view"] = f"ilike.*{view_contains.strip()}*"
    if building:
        params["unit_no"] = f"like.{building.strip().upper()}-*"
    if bedrooms is not None:
        params["unit_types.name"] = (
            "eq.Studio" if bedrooms == 0 else f"eq.{bedrooms} Bedroom"
        )
    try:
        rows = _live_get(params)
    except Exception:
        logger.exception("live inventory unreachable")
        return {"ok": False, "error": "inventory unreachable",
                "instruction": ("เช็คระบบผังขายไม่ได้ตอนนี้ ให้บอกลูกค้าตรงๆ "
                                "ว่าขอตรวจสอบกับฝ่ายขาย ห้ามแต่งรายการเอง")}
    total = None
    if descending:
        # Variety beats eight twins: one unit per size first (the guest is
        # choosing a room type, not a room number), then fill from the top.
        # When the 48-row page is full the cheap end is fetched too, so the
        # spread covers both ends of the budget instead of one price band.
        total_known = len(rows) < 48
        pool = list(rows)
        if not total_known:
            have = {r.get("unit_no") for r in pool}
            cheap = _live_get({**params, "order": "promo_price.asc"})
            pool += [r for r in cheap if r.get("unit_no") not in have]
        else:
            total = len(rows)
        picked, seen_sizes = [], set()
        for r in pool:
            if len(picked) >= 8:
                break
            size = r.get("msize") or r.get("size_sqm")
            if size in seen_sizes:
                continue
            seen_sizes.add(size)
            picked.append(r)
        for r in pool:
            if len(picked) >= 8:
                break
            if r not in picked:
                picked.append(r)
        picked.sort(key=lambda r: -(r.get("promo_price") or r.get("base_price") or 0))
        rows = picked
    if any(row.get("status") != "available" for row in rows):
        return _blocked_inventory_state("unexpected_inventory_status")
    prepared_cards = [_prepared_live_card(_card_from_live(r)) for r in rows]
    blocked = next((item for item in prepared_cards if not item.allowed), None)
    if blocked:
        return _blocked_inventory(blocked)
    units_found = [item.payload for item in prepared_cards]
    trace = (prepared_cards[0] if prepared_cards else RuntimeResult(
        True, settings.project_id, INVENTORY_SOURCE_ID,
        "fresh_scoped_inventory_empty", {})).trace()
    # The query is capped, and "ทั้งหมด 8 ห้อง" was once said on screen
    # about a budget that actually fits 105 — the cap spoken as the total.
    # A full count costs a second request; honesty costs a word.
    if descending:
        capped = total is None
        count_phrase = (f"ทั้งหมด {total} ห้องในงบ (แสดง {len(units_found)} "
                        "ห้องคละขนาด เรียงจากราคาสูงในงบลงมา)"
                        if total is not None else
                        f"หลายสิบห้องในงบ (แสดงตัวอย่าง {len(units_found)} "
                        "ห้องคละขนาด เรียงจากราคาสูงในงบลงมา อาจมีมากกว่านี้)")
    else:
        capped = len(rows) >= 8
        count_phrase = (f"อย่างน้อย {len(units_found)} ห้อง (แสดงตัวอย่าง "
                        f"{len(units_found)} รายการ อาจมีมากกว่านี้)"
                        if capped else f"{len(units_found)} ห้อง")
    turnlog.record("find_units", count=len(units_found), capped=capped,
                   max_price=max_price_thb, bedrooms=bedrooms, building=building)
    if settings.units_show_price:
        instruction = (
            f"พบ{count_phrase} อ่านจากรายการเท่านั้น ไล่สองถึงสามห้องแรกสั้นๆ "
            "เลขห้อง ขนาด ราคา ห้ามแต่งห้องเพิ่ม "
            "รายการนี้เป็นห้องว่างจริง ณ ตอนนี้จากระบบผังขาย"
        )
    else:
        instruction = (
            f"พบ{count_phrase} อ่านจากรายการเท่านั้น ไล่เลขห้องกับขนาด "
            "สองถึงสามห้องแรกสั้นๆ ห้ามแต่งห้องเพิ่ม "
            "รายการนี้เป็นห้องว่างจริง ณ ตอนนี้จากระบบผังขาย"
        )
        if budget_filtered:
            instruction += (
                " ทุกห้องในรายการอยู่ในงบที่ถาม แต่นโยบายคือไม่เปิดเผยตัวเลขราคา "
                "ห้ามพูดหรือเดาราคา ให้บอกว่าราคาแน่นอนคุยกับฝ่ายขายโดยตรง"
            )
    return {"ok": True, "screen": "unitlist", "units": units_found,
            "count": len(units_found),
            "total": total,          # known exact total, or None when capped
            "capped": capped,
            "policy_trace": trace,
            "instruction": instruction}

# ==================== the live floor plan ====================
#
# The Canva deck's plan slide is a PRELIMINARY CONCEPT rendering; the sales
# system has the real thing — per-floor composite images plus every unit's
# position (percent coordinates) and live status. `show_plan` puts that on
# the stage: the same picture the sales desk stares at, colored by what is
# actually sold *right now*, with no prices anywhere on it (policy).

_PLAN_VERIFIED: dict[tuple[str, str], tuple[float, str, str]] = {}


def _plan_paths() -> dict[int, str]:
    """floor number -> image path, from the copied assets manifest."""
    from app.data_sources import require_project_payload, source_path
    try:
        path = source_path("floor_plan_assets", settings.project_id)
        raw = json.loads(path.read_text(encoding="utf-8"))
        require_project_payload(raw, "floor_plan_assets", settings.project_id)
        return {int(f["floor"]): f["display_path"]
                for f in raw.get("floors", []) if f.get("display_path")}
    except (OSError, ValueError, KeyError, TypeError):
        logger.warning("could not load a floor-plan manifest for the active project")
        return {}


def _verified_plan_asset(floor: int) -> RuntimeResult:
    """Verify a deployed derivative against the project manifest before use."""
    import hashlib
    import httpx

    from app.data_sources import source_path

    try:
        source = json.loads(source_path("floor_plan_assets", settings.project_id).read_text(
            encoding="utf-8"))
        entry = next((item for item in source.get("floors", [])
                      if item.get("floor") == floor), None)
        if not isinstance(entry, dict):
            raise ValueError("floor not present in asset manifest")
        path = entry.get("display_path")
        expected = entry.get("display_derivative_sha256")
        # Validate metadata before using its path in a request.
        metadata = prepare_plan_asset(
            source, entry, expected_project_id=settings.project_id,
            observed_sha256=expected if isinstance(expected, str) else "",
            content_type="image/webp")
        if not metadata.allowed:
            return metadata
        url = settings.inventory_plan_base.rstrip("/") + path
        key = (url, expected)
        now = _time.monotonic()
        cached = _PLAN_VERIFIED.get(key)
        if cached and now - cached[0] < 300:
            digest, media_type = cached[1], cached[2]
        else:
            digest_state = hashlib.sha256()
            count = 0
            with httpx.stream("GET", url, timeout=8, follow_redirects=False) as response:
                response.raise_for_status()
                media_type = response.headers.get("content-type", "")
                for chunk in response.iter_bytes():
                    count += len(chunk)
                    if count > 20 * 1024 * 1024:
                        raise ValueError("plan asset exceeds size limit")
                    digest_state.update(chunk)
            digest = digest_state.hexdigest()
        result = prepare_plan_asset(
            source, entry, expected_project_id=settings.project_id,
            observed_sha256=digest, content_type=media_type)
        if result.allowed:
            _PLAN_VERIFIED[key] = (now, digest, media_type)
        return result
    except Exception:
        logger.exception("plan asset could not be verified for floor %s", floor)
        return RuntimeResult(False, settings.project_id, "floor_plan_assets",
                             "plan_asset_unavailable", {})


def _valid_plan_shape(row: dict) -> bool:
    """Only bounded numeric coordinates may pass to the screen/model."""
    import math

    def coordinate(value, *, positive=False) -> bool:
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and (0 < value <= 100 if positive
                                              else 0 <= value <= 100))

    if row.get("pos_x") is None:
        return True
    if not (coordinate(row.get("pos_x")) and coordinate(row.get("pos_y"))
            and coordinate(row.get("width"), positive=True)
            and coordinate(row.get("height"), positive=True)):
        return False
    polygon = row.get("poly")
    if polygon is None:
        return True
    return (isinstance(polygon, list) and 3 <= len(polygon) <= 100
            and all(isinstance(point, list) and len(point) == 2
                    and all(coordinate(value) for value in point)
                    for point in polygon))


@tool(
    name="show_plan",
    description=(
        "แสดงผังโครงการชั้นที่ขอบนจอ พร้อมสถานะห้องสดจากระบบผังขาย "
        "(ว่าง/จอง/ขายแล้ว ระบายสีบนผังจริง) ใช้เมื่อลูกค้าขอดูผัง ผังโครงการ "
        "ผังชั้น หรือถามว่าห้องว่างอยู่โซนไหน ชั้นเริ่มต้นคือ 1 "
        "ระบุตึกได้ถ้าลูกค้าเจาะจง ผลลัพธ์มีจำนวนห้องแต่ละสถานะ ให้พูดจากตัวเลขนั้น "
        "ห้ามบรรยายห้องที่ไม่อยู่ในผล ห้ามพูดราคา"
    ),
    parameters={
        "type": "object",
        "properties": {
            "floor": {"type": "integer", "description": "ชั้น (1-8) ค่าเริ่มต้น 1"},
            "building": {"type": "string",
                         "description": "รหัสตึก เช่น A ถ้าลูกค้าเจาะจงตึก"},
        },
    },
    tags=["units"],
    blocking=True,
)
def show_plan(floor: int = 1, building: str | None = None) -> dict:
    if not live_configured():
        return {"ok": False, "error": "no live inventory",
                "instruction": ("เครื่องนี้ยังไม่ได้ต่อระบบผังขาย ให้บอกลูกค้าว่า "
                                "ดูผังกับฝ่ายขายได้โดยตรง ห้ามบรรยายผังจากความจำ")}
    try:
        floor_number = int(floor)
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid floor"}
    image_path = _plan_paths().get(floor_number)
    if not image_path:
        return {"ok": False, "error": "no plan image", "floor": floor,
                "instruction": ("ไม่มีภาพผังของชั้นนี้ (มีชั้น %s) ให้บอกลูกค้าตรงๆ"
                                % ", ".join(str(k) for k in sorted(_plan_paths())))}
    asset = _verified_plan_asset(floor_number)
    logger.info("runtime plan policy %s", asset.trace())
    if not asset.allowed:
        return {"ok": False, "error": "plan asset policy blocked",
                "policy_trace": asset.trace(),
                "instruction": "ยังตรวจภาพผังชั้นนี้ไม่ได้ ให้ฝ่ายขายเปิดผังที่ยืนยันแล้ว ห้ามบรรยายจากภาพที่ถูกบล็อก"}
    try:
        rows = _live_get(_scoped({
            "select": "unit_no,status,pos_x,pos_y,width,height,poly," + _SCOPE_EMBED,
            "floors.floor_number": f"eq.{floor_number}",
            "limit": "500",
        }))
    except Exception:
        logger.exception("live inventory unreachable")
        return {"ok": False, "error": "inventory unreachable",
                "instruction": ("เช็คระบบผังขายไม่ได้ตอนนี้ ให้บอกลูกค้าตรงๆ "
                                "ว่าขอเปิดผังกับฝ่ายขาย ห้ามบรรยายผังจากความจำ")}
    want = (building or "").strip().upper()
    prepared_rows = [_prepared_live_card(_card_from_live(row), allow_price=False)
                     for row in rows]
    blocked = next((item for item in prepared_rows if not item.allowed), None)
    if blocked:
        return _blocked_inventory(blocked)
    if any((row.get("floors") or {}).get("floor_number") != floor_number
           for row in rows):
        return _blocked_inventory_state("wrong_plan_floor")
    if any(str(row.get("status", "")).lower() not in {"available", "reserved", "sold"}
           or not _valid_plan_shape(row) for row in rows):
        return _blocked_inventory_state("invalid_plan_overlay")
    marks, counts = [], {"available": 0, "reserved": 0, "sold": 0}
    for r in rows:
        st = str(r.get("status", "")).lower()
        code = ((r.get("floors") or {}).get("buildings") or {}).get("code", "")
        dim = bool(want) and code != want
        if st in counts and not dim:
            counts[st] += 1
        if r.get("pos_x") is None:
            continue
        marks.append({"no": r["unit_no"], "status": st, "dim": dim,
                      "x": r["pos_x"], "y": r["pos_y"],
                      "w": r["width"], "h": r["height"],
                      "poly": r.get("poly")})
    turnlog.record("show_plan", floor=floor, building=want or None,
                   units=len(marks))
    scope = f"ตึก {want} " if want else ""
    return {"ok": True, "screen": "plan", "project_id": settings.project_id,
            "image": settings.inventory_plan_base.rstrip("/") + asset.payload["path"],
            "floor": floor_number, "building": want or None,
            "units": marks, "counts": counts,
            "policy_trace": {"asset": asset.trace(), "inventory": (
                prepared_rows[0] if prepared_rows else RuntimeResult(
                    True, settings.project_id, INVENTORY_SOURCE_ID,
                    "fresh_scoped_inventory_empty", {})).trace()},
            "instruction": (f"ผังชั้น {floor} {scope}ขึ้นจอแล้ว สถานะสดจากระบบผังขาย: "
                            f"ว่าง {counts['available']} จอง {counts['reserved']} "
                            f"ขายแล้ว {counts['sold']} ห้อง "
                            "พูดจากตัวเลขนี้สั้นๆ ห้ามพูดราคา ห้ามบรรยายห้องรายห้องที่ไม่ได้ถูกถาม")}


def live_probe() -> tuple[bool, str]:
    """One line of truth for the boot banner.

    Every quiet-failure system in this project earns a boot line (semantic
    search, the Canva window, the robot link) because their shared failure
    mode is "off the whole time and nobody noticed". The inventory link is
    the newest member: a paste-damaged key already produced a session where
    the robot apologised for the sales system being down while it was fine.
    """
    if not live_configured():
        return True, "unit inventory: OFF (no INVENTORY_SUPABASE_URL/KEY) — show_unit uses the file/sample chain"
    try:
        import httpx

        r = httpx.get(f"{settings.inventory_url}/rest/v1/units",
                      # Scoped like every read: the banner once said "2540
                      # units" — three projects' worth — for a gallery that
                      # may show one of them.
                      params=_scoped({"select": "id," + _SCOPE_EMBED, "limit": "1"}),
                      headers={"apikey": settings.inventory_key,
                               "Authorization": f"Bearer {settings.inventory_key}",
                               "Prefer": "count=exact"},
                      timeout=4)
        r.raise_for_status()
        total = (r.headers.get("content-range") or "?/?").split("/")[-1]
        return True, (f"unit inventory: LIVE ({total} units of {settings.inventory_project} "
                      f"at {settings.inventory_url.split('//')[-1]})")
    except Exception as exc:
        return False, ("unit inventory: CONFIGURED BUT UNREACHABLE (%s) — "
                       "show_unit will refuse honestly; check the URL/key in .env"
                       % type(exc).__name__)


# ==================== promotions, comparison, quotation, map ====================
#
# Sales list items 5, 6, 1/9 and 17 (docs/agent-list-สถานะ.md), wired on
# 2026-09-01 to what condo-inventory actually has by then: a promotions
# table, a quotation sheet with a URL, and the same units the card reads.
# Nothing here invents a number: promotions come from the admin's applied
# rows, the quotation is the sales app's own page put on the stage, the
# comparison is counts and sizes, and the map link is whatever the sales
# team wrote into condo_facts.json — or "no map yet".


@tool(
    name="list_promotions",
    description=(
        "รายการโปรโมชั่นที่ฝ่ายขายเปิดอยู่ตอนนี้จากระบบผังขาย ใช้เมื่อลูกค้าถามว่า "
        "มีโปรโมชั่นอะไรบ้าง ห้องไหนมีโปร โปรล่าสุดคืออะไร "
        "ผลลัพธ์คือรายการจริง ณ ตอนนี้ ให้อ่านจากรายการเท่านั้น ห้ามแต่งโปรหรือตัวเลขเพิ่ม "
        "ถ้ารายการว่างให้บอกตรงๆ ว่าตอนนี้ยังไม่มีโปรโมชั่นในระบบ"
    ),
    parameters={
        "type": "object",
        "properties": {
            "building": {"type": "string", "description": "รหัสตึก เช่น A (ไม่ใส่ = ทุกตึก)"},
        },
    },
    tags=["units"],
    blocking=True,
)
def list_promotions(building: str | None = None) -> dict:
    if not live_configured():
        return {"ok": False, "error": "no live inventory",
                "instruction": ("เครื่องนี้ยังไม่ได้ต่อระบบผังขาย ให้บอกลูกค้าว่า "
                                "โปรโมชั่นล่าสุดสอบถามฝ่ายขาย ห้ามแต่งโปรเอง")}
    params = _scoped({"select": _PROMO_SELECT, "active": "eq.true",
                      "units.status": "neq.sold", "order": "updated_at.desc",
                      "limit": "24"}, via="units.")
    if building:
        params["units.unit_no"] = f"like.{building.strip().upper()}-*"
    try:
        rows = _live_get(params, table="pricing_unit_promotions")
    except Exception:
        logger.exception("promotions unreachable")
        return {"ok": False, "error": "inventory unreachable",
                "instruction": ("เช็คโปรโมชั่นจากระบบผังขายไม่ได้ตอนนี้ ให้บอกลูกค้าตรงๆ "
                                "ว่าขอตรวจสอบกับฝ่ายขาย ห้ามแต่งโปรเอง")}
    items = []
    for row in rows:
        unit = row.get("units") or {}
        if row.get("active") is not True or unit.get("status") == "sold":
            return _blocked_inventory_state("unexpected_promotion_state")
        prepared = _prepared_live_card(_card_from_live(unit), allow_price=False)
        if not prepared.allowed:
            return _blocked_inventory(prepared)
        card = prepared.payload
        card["promo"] = True
        items.append(card)
    trace = (prepared if items else RuntimeResult(
        True, settings.project_id, INVENTORY_SOURCE_ID,
        "fresh_scoped_inventory_empty", {})).trace()
    turnlog.record("list_promotions", count=len(items), building=building)
    if not items:
        return {"ok": True, "screen": "promotions", "promotions": [], "count": 0,
                "policy_trace": trace,
                "instruction": ("ตอนนี้ยังไม่มีโปรโมชั่นเปิดอยู่ในระบบผังขาย ให้บอกลูกค้าตรงๆ "
                                "และแนะนำให้สอบถามฝ่ายขายเผื่อมีโปรที่ยังไม่ลงระบบ")}
    return {"ok": True, "screen": "promotions", "promotions": items, "count": len(items),
            "policy_trace": trace,
            "instruction": (f"มีห้องที่ติดธงโปรโมชั่น {len(items)} ห้องในระบบ "
                            "บอกได้เฉพาะเลขห้องและการมีโปรโมชั่น "
                            "รายละเอียด เงื่อนไข และตัวเลขให้ฝ่ายขายยืนยัน ห้ามแต่งเพิ่ม")}


@tool(
    name="compare_unit_types",
    description=(
        "เปรียบเทียบแบบห้องที่ยังว่าง (สตูดิโอ / 1 ห้องนอน / 2 ห้องนอน ...) จากระบบผังขาย: "
        "จำนวนห้องว่าง ขนาด ชั้น วิวและทิศที่มี ใช้เมื่อลูกค้าถามว่า 1 ห้องนอนกับ 2 ห้องนอน "
        "ต่างกันยังไง แบบไหนมีวิวอะไร ห้องแบบนี้มีกี่ห้อง "
        "ผลลัพธ์คือตัวเลขจริง ณ ตอนนี้ ห้ามแต่งขนาดหรือวิวเพิ่ม"
    ),
    parameters={
        "type": "object",
        "properties": {
            "building": {"type": "string", "description": "รหัสตึก เช่น A (ไม่ใส่ = ทุกตึก)"},
        },
    },
    tags=["units"],
    blocking=True,
)
def compare_unit_types(building: str | None = None) -> dict:
    if not live_configured():
        return {"ok": False, "error": "no live inventory",
                "instruction": ("เครื่องนี้ยังไม่ได้ต่อระบบผังขาย ให้บอกลูกค้าว่า "
                                "ขอเช็คแบบห้องกับฝ่ายขาย ห้ามแต่งข้อมูลเอง")}
    params = _scoped({"select": ("unit_no,msize,size_sqm,view,side,unit_option,collection,"
                                 "status," + _SCOPE_EMBED + ",unit_types(name)"),
                      "status": "eq.available", "limit": "2000"})
    if building:
        params["unit_no"] = f"like.{building.strip().upper()}-*"
    try:
        rows = _live_get(params)
    except Exception:
        logger.exception("live inventory unreachable")
        return {"ok": False, "error": "inventory unreachable",
                "instruction": ("เช็คระบบผังขายไม่ได้ตอนนี้ ให้บอกลูกค้าตรงๆ "
                                "ว่าขอตรวจสอบกับฝ่ายขาย ห้ามแต่งข้อมูลเอง")}
    if any(row.get("status") != "available" for row in rows):
        return _blocked_inventory_state("unexpected_inventory_status")
    prepared_rows = [_prepared_live_card(_card_from_live(row), allow_price=False)
                     for row in rows]
    blocked = next((item for item in prepared_rows if not item.allowed), None)
    if blocked:
        return _blocked_inventory(blocked)
    groups: dict[str, dict] = {}
    for r in rows:
        kind = (r.get("unit_types") or {}).get("name") or "ไม่ระบุแบบ"
        g = groups.setdefault(kind, {"type": kind, "available": 0, "sizes": [],
                                     "floors": set(), "views": {}, "sides": {},
                                     "options": {}, "buildings": set()})
        g["available"] += 1
        size = r.get("msize") or r.get("size_sqm")
        if size:
            g["sizes"].append(float(size))
        floors = r.get("floors") or {}
        if floors.get("floor_number") is not None:
            g["floors"].add(int(floors["floor_number"]))
        code = (floors.get("buildings") or {}).get("code")
        if code:
            g["buildings"].add(code)
        for key, field in (("views", "view"), ("sides", "side"), ("options", "unit_option")):
            v = r.get(field)
            if v:
                g[key][v] = g[key].get(v, 0) + 1
    types = []
    for g in sorted(groups.values(), key=lambda g: (min(g["sizes"]) if g["sizes"] else 0)):
        types.append({
            "type": g["type"],
            "available": g["available"],
            "sqm_min": min(g["sizes"]) if g["sizes"] else None,
            "sqm_max": max(g["sizes"]) if g["sizes"] else None,
            "floor_min": min(g["floors"]) if g["floors"] else None,
            "floor_max": max(g["floors"]) if g["floors"] else None,
            "buildings": sorted(g["buildings"]),
            # Most common first, so "วิวอะไรบ้าง" reads the real spread.
            "views": [f"{k} ({n})" for k, n in sorted(g["views"].items(), key=lambda kv: -kv[1])],
            "sides": [f"{k} ({n})" for k, n in sorted(g["sides"].items(), key=lambda kv: -kv[1])],
            "options": [f"{k} ({n})" for k, n in sorted(g["options"].items(), key=lambda kv: -kv[1])],
        })
    turnlog.record("compare_unit_types", types=len(types), units=len(rows), building=building)
    if not types:
        return {"ok": True, "screen": "unittypes", "types": [], "building": building,
                "policy_trace": RuntimeResult(True, settings.project_id,
                                               INVENTORY_SOURCE_ID,
                                               "fresh_scoped_inventory_empty", {}).trace(),
                "instruction": "ไม่พบห้องว่างตามเงื่อนไข ให้บอกลูกค้าตรงๆ"}
    return {"ok": True, "screen": "unittypes", "types": types, "building": building,
            "policy_trace": prepared_rows[0].trace(),
            "total_available": len(rows),
            "instruction": (f"ห้องว่างทั้งหมด {len(rows)} ห้อง แบ่งเป็น {len(types)} แบบ ขึ้นจอแล้ว "
                            "เริ่มคำตอบด้วยความแตกต่างด้านการใช้งานอย่างน้อยหนึ่งประโยคก่อนพูดตัวเลข "
                            "จากนั้นใช้จำนวน ขนาด ชั้น และวิว/ทิศเป็นข้อมูลประกอบเมื่อเกี่ยวข้อง "
                            "ห้ามสรุปว่าแบบใดเหมาะลงทุนหรืออยู่เองโดยไม่มีข้อมูลจากลูกค้า "
                            "ห้ามแต่งขนาดหรือวิว ตอบความแตกต่างแล้วจบทันที ห้ามถามคำถามต่อท้าย "
                            + ("" if settings.units_show_price else
                               "ไม่พูดตัวเลขราคา ถ้าลูกค้าบอกงบให้ใช้ find_units แทน"))}


@tool(
    name="show_quotation",
    description=(
        "เปิดใบเสนอราคาของระบบผังขายขึ้นจอสำหรับห้องหนึ่ง ใช้เมื่อพนักงานหรือลูกค้าขอ "
        "ใบเสนอราคา/quotation ของห้องนั้น ระบุสัญชาติผู้ซื้อ (thai/foreign — ถ้าไม่รู้ให้ถามก่อน "
        "เพราะราคาและงวดต่างกัน) และสกุลเงินถ้าลูกค้าขอ เช่น USD EUR INR "
        "ตัวเลขทั้งหมดอยู่บนจอ ห้ามอ่านราคา งวด หรืออัตราแลกเปลี่ยนออกเสียง "
        "หน้านี้เป็นของพนักงานขาย ต้องล็อกอินอยู่บนจอ"
    ),
    parameters={
        "type": "object",
        "properties": {
            "room": {"type": "string", "description": "เลขห้อง เช่น A203"},
            "ownership": {"type": "string", "enum": ["thai", "foreign"],
                          "description": "thai = คนไทย, foreign = ต่างชาติ"},
            "currency": {"type": "string",
                         "description": "รหัสสกุลเงิน 3 ตัว เช่น THB USD EUR INR (ไม่ใส่ = THB)"},
        },
        "required": ["room", "ownership"],
    },
    tags=["units"],
    blocking=True,
)
def show_quotation(room: str, ownership: str = "thai", currency: str | None = None) -> dict:
    """Item 1 (and the fee table of item 9) — not by generating a document.

    The sales app already has the quotation sheet, an Excel replica the
    team prints, with the payment schedule and the excluded costs (transfer
    1%, common fee, sinking fund, meter fees) and a live exchange rate with
    its source and time. Emma opens *that page* on the stage for the unit
    asked, so every figure on the screen is the sales team's own — the
    document a robot generates itself is the liability the print path
    already refused to take (docs/เอกสารที่ให้หุ่นยนต์พิมพ์.md).
    """
    from app.tools import webstage

    ownership = (ownership or "thai").strip().lower()
    if ownership not in ("thai", "foreign"):
        return {"ok": False, "error": "ownership must be thai or foreign",
                "instruction": "ถามลูกค้าก่อนว่าซื้อในชื่อคนไทยหรือต่างชาติ แล้วเรียกใหม่"}
    code = (currency or "THB").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        return {"ok": False, "error": "bad currency code",
                "instruction": "สกุลเงินต้องเป็นรหัส 3 ตัว เช่น USD ให้ถามลูกค้าใหม่"}
    if not webstage.enabled():
        return {"ok": False, "error": "web stage off",
                "instruction": ("เครื่องนี้ไม่ได้เปิดจอเว็บ (WEB_STAGE) ให้บอกว่าขอให้ฝ่ายขาย "
                                "เปิดใบเสนอราคาให้จากระบบผังขายโดยตรง")}
    if not live_configured():
        return {"ok": False, "error": "no live inventory",
                "instruction": "เครื่องนี้ยังไม่ได้ต่อระบบผังขาย ให้บอกว่าขอใบเสนอราคาจากฝ่ายขาย"}
    try:
        card = _live_find(room)
    except Exception:
        logger.exception("live inventory unreachable")
        return {"ok": False, "error": "inventory unreachable",
                "instruction": "เช็คระบบผังขายไม่ได้ตอนนี้ ให้บอกลูกค้าตรงๆ"}
    if card is None or not card.get("id"):
        return {"ok": False, "error": "unknown room", "asked": room,
                "instruction": "ไม่พบเลขห้องนี้ในระบบผังขาย ให้ทวนเลขห้องกับลูกค้า"}
    prepared = _prepared_live_card(card, allow_price=False)
    if not prepared.allowed:
        return _blocked_inventory(prepared)
    if card.get("status") == "sold":
        return {"ok": False, "error": "sold", "room": card["room"],
                "instruction": "ห้องนี้ขายแล้ว ออกใบเสนอราคาไม่ได้ ให้เสนอห้องอื่นด้วย find_units"}
    url = (f"{settings.inventory_plan_base}/quotation/{card['id']}"
           f"?ownership={ownership}&currency={code}")
    webstage.request(url)
    turnlog.record("show_quotation", room=card["room"], ownership=ownership, currency=code)
    return {"ok": True, "screen": "web", "room": card["room"], "ownership": ownership,
            "currency": code,
            "instruction": (f"ใบเสนอราคาห้อง {card['room']} ({'คนไทย' if ownership == 'thai' else 'ต่างชาติ'}"
                            f", {code}) กำลังขึ้นจอ ให้บอกสั้นๆ ว่าขึ้นจอแล้ว "
                            "ตัวเลขทั้งหมดอยู่บนจอ ห้ามอ่านราคา งวด หรืออัตราแลกเปลี่ยน "
                            "ถ้าจอขึ้นหน้าเข้าสู่ระบบ ให้บอกพนักงานขายล็อกอินก่อน "
                            "ใบเสนอราคาเป็นของฝ่ายขาย พนักงานเป็นคนกรอกชื่อลูกค้าและกดพิมพ์เอง")}


@tool(
    name="show_map",
    description=(
        "แสดงแผนที่ตำแหน่งโครงการบนจอ (Google Maps) ใช้เมื่อลูกค้าถามว่าโครงการอยู่ตรงไหน "
        "เดินทางยังไง ใกล้อะไร ถ้ายังไม่มีลิงก์แผนที่ที่ฝ่ายขายยืนยัน จะได้ error กลับมา "
        "ให้บอกทำเลตามข้อมูลโครงการที่มี ห้ามเดาพิกัดหรือระยะทาง"
    ),
    parameters={"type": "object", "properties": {}},
    tags=["units"],
    blocking=True,
)
def show_map() -> dict:
    """Item 17 — a fixed, sales-confirmed pin, never a search the model
    typed. The earlier route ("search Google Maps for the project name")
    put the wrong place on the screen once; a pin somebody signed off on
    cannot. The link lives in condo_facts.json under the same rule as
    every other project fact there: empty until the sales team fills it."""
    from app import prompts
    from app.tools import webstage

    # The same file the prompt's project facts come from — not the search
    # knowledge file, which is what the first version read (and found no
    # map in, with the owner's link sitting in condo_facts.json).
    try:
        facts = json.loads(Path(prompts.CONDO_FACTS_FILE).read_text(encoding="utf-8"))
        from app.data_sources import require_project_payload

        require_project_payload(facts, "project_facts", settings.project_id)
    except Exception:
        facts = {}
    from app.knowledge_policy import evaluate_claim

    decision = evaluate_claim(facts, settings.project_id)
    logger.info("runtime map knowledge policy %s", decision.trace())
    if not decision.allowed:
        return {"ok": False, "error": "map source policy blocked",
                "policy_trace": decision.trace(),
                "instruction": "ยังไม่มีลิงก์แผนที่ที่อนุมัติให้แสดง กรุณาให้ฝ่ายขายยืนยัน"}
    link = ((facts.get("map") or {}).get("url") or "").strip()
    from urllib.parse import urlsplit

    try:
        parsed = urlsplit(link)
        allowed_host = parsed.hostname in {"www.google.com", "maps.app.goo.gl",
                                           "goo.gl", "maps.google.com"}
        allowed_path = (parsed.hostname != "www.google.com"
                        or parsed.path.startswith("/maps"))
        valid_link = (parsed.scheme == "https" and allowed_host and allowed_path
                      and parsed.port in (None, 443)
                      and not parsed.username and not parsed.password)
    except ValueError:
        valid_link = False
    if not valid_link:
        return {"ok": False, "error": "no confirmed map link",
                "instruction": ("ยังไม่มีลิงก์แผนที่ที่ฝ่ายขายยืนยันในระบบ ให้บอกทำเลด้วยคำพูด "
                                "ตามข้อมูลโครงการที่มี ห้ามเดาพิกัด ระยะทาง หรือเวลาเดินทาง")}
    if not webstage.enabled():
        return {"ok": False, "error": "web stage off",
                "instruction": "เครื่องนี้ไม่ได้เปิดจอเว็บ ให้บอกทำเลด้วยคำพูดตามข้อมูลโครงการ"}
    webstage.request(link)
    turnlog.record("show_map")
    return {"ok": True, "screen": "web", "project_id": settings.project_id,
            "instruction": ("แผนที่โครงการกำลังขึ้นจอ บอกสั้นๆ ว่าขึ้นจอแล้ว "
                            "อธิบายทำเลได้เฉพาะตามข้อมูลโครงการ ห้ามเดาระยะทางหรือนาที")}
