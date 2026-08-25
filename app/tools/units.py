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

Sample data exists (`data/units.sample.json`) because the real table has not
arrived yet and the card cannot be designed against nothing. It is off by
default, it is stamped `sample: true` all the way to the screen, and the
model is told in the tool result to say so out loud. A showroom must never
be one forgotten setting away from quoting invented prices.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from app import turnlog
from app.config import settings
from app.tools.registry import tool

logger = logging.getLogger("condo_voice.units")

_cache: dict | None = None
_cache_path: str | None = None


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
    """The approved table if it exists, else the sample if it is allowed.

    Never both, and never a merge: a card built half from signed prices and
    half from invented ones is worse than either, because nothing on it says
    which half you are looking at.
    """
    global _cache, _cache_path

    path, fell_back = _table_path()
    if path is None:
        return {}

    if _cache is not None and _cache_path == str(path):
        return _cache
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("could not read %s", path)
        return {}
    # Sample-ness is a property of *where the file came from*, not of a flag
    # inside it and not of the setting that allowed it. Two consequences,
    # both deliberate: a real table dropped into place stops being a sample
    # the moment it exists, with nobody remembering to flip anything; and a
    # fallback file that has lost its own `"sample": true` — copied, edited,
    # hand-written — is still treated as one. The flag is a courtesy; the
    # path is the fact.
    data["sample"] = bool(data.get("sample")) or fell_back
    _cache, _cache_path = data, str(path)
    return data


def reset() -> None:
    """Tests and a reload after the sales team drops the real file in."""
    global _cache, _cache_path, _PLAN_ASSETS
    _cache = _cache_path = None
    _live_cache.clear()
    _PLAN_ASSETS = None


def find(room: str) -> dict | None:
    room = (room or "").strip().upper().replace(" ", "")
    if not room:
        return None
    for unit in _load().get("units", []):
        if str(unit.get("room", "")).strip().upper() == room:
            return unit
    return None


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
)
def show_unit(room: str) -> dict:
    if live_configured():
        return show_unit_live(room)
    data = _load()
    if not data:
        return {"ok": False, "error": "no unit table",
                "instruction": ("ยังไม่มีตารางยูนิตในระบบ ให้บอกลูกค้าตรงๆ ว่ายังไม่มีข้อมูล "
                                "และให้ติดต่อฝ่ายขาย ห้ามเดาราคาหรือขนาดห้อง")}
    unit = find(room)
    if unit is None:
        rooms = [u.get("room") for u in data.get("units", [])][:8]
        return {"ok": False, "error": "unknown room", "asked": room,
                "rooms_available": rooms,
                "instruction": ("ไม่มีห้องนี้ในตาราง ให้บอกลูกค้าตรงๆ ว่าไม่พบเลขห้องนี้ "
                                "ถามเลขห้องซ้ำอีกครั้ง ห้ามแต่งข้อมูลห้องขึ้นมาเอง")}

    sample = bool(data.get("sample"))
    card = dict(unit)
    card["status_th"] = _STATUS_TH.get(str(unit.get("status", "")).lower(), "")
    card["sample"] = sample
    card["approved_by"] = data.get("approved_by") or ""
    card["effective_from"] = data.get("effective_from") or ""
    turnlog.record("show_unit", room=card.get("room"), sample=sample)

    if sample:
        # Said to the model every single time, not once at the start of the
        # conversation: an instruction that has to be remembered across
        # turns is one that gets dropped, and the thing being dropped here
        # is "these numbers are made up".
        note = ("นี่คือข้อมูล**ตัวอย่าง** ยังไม่ใช่ราคาจริง ต้องบอกลูกค้าทุกครั้งว่า "
                "ตัวเลขบนจอเป็นตัวอย่างสำหรับดูหน้าตาระบบ ยังไม่ใช่ราคาจริง "
                "ให้ติดต่อฝ่ายขายเพื่อขอราคาที่ใช้ได้จริง ห้ามพูดเหมือนเป็นราคาจริงเด็ดขาด")
    elif not card["approved_by"]:
        note = ("ตารางนี้ยังไม่มีผู้อนุมัติกำกับ ให้บอกราคาได้แต่ต้องเสริมว่า "
                "ขอให้ยืนยันกับฝ่ายขายอีกครั้ง")
    else:
        note = "บอกข้อมูลตามการ์ดสั้นๆ ไม่ต้องอ่านทุกฟิลด์"

    return {"ok": True, "screen": "unit", "unit": _apply_price_policy(card),
            "sample": sample, "instruction": note}


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
_live_cache: dict[str, tuple[float, dict]] = {}


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


def _live_get(params: dict) -> list[dict]:
    """One PostgREST read. Its own function so tests can stand it in."""
    import httpx

    headers = {"apikey": settings.inventory_key,
               "Authorization": f"Bearer {settings.inventory_key}"}
    r = httpx.get(f"{settings.inventory_url}/rest/v1/units", params=params,
                  headers=headers, timeout=8)
    r.raise_for_status()
    return r.json()


_LIVE_SELECT = ("unit_no,size_sqm,msize,view,side,collection,unit_option,"
                "base_price,promo_price,status,note,updated_at,"
                "floors(floor_number,buildings(code)),unit_types(name)")


def _card_from_live(row: dict) -> dict:
    """DB row -> the card the screen already renders.

    promo_price is the selling price (TN/CN on the pricelist); base_price is
    the list price. Both are carried so the card can show the discount — but
    the model is told the promo price is *the* price.
    """
    floors = row.get("floors") or {}
    building = (floors.get("buildings") or {}).get("code") or ""
    unit_type = (row.get("unit_types") or {}).get("name") or ""
    price = row.get("promo_price") or row.get("base_price")
    return {
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
    hit = _live_cache.get(unit_no)
    if hit and now - hit[0] < settings.inventory_cache_s:
        return hit[1]
    rows = _live_get({"select": _LIVE_SELECT, "unit_no": f"eq.{unit_no}",
                      "limit": "1"})
    if not rows:
        return None
    card = _card_from_live(rows[0])
    _live_cache[unit_no] = (now, card)
    return card


def _apply_price_policy(card: dict) -> dict:
    """Remove the numbers, keep the filterability.

    Stripping at the tool boundary rather than hiding in the UI is the
    point: a field that is not in the payload cannot be leaked by a CSS
    mistake, and the model cannot read a price aloud that it never
    received. The DB still has the numbers, and find_units still filters on
    them server-side — "งบสามล้านซื้อห้องไหนได้" works without a single baht
    figure leaving this function.
    """
    if settings.units_show_price:
        return card
    card = dict(card)
    card.pop("price_thb", None)
    card.pop("base_price_thb", None)
    return card


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
    turnlog.record("show_unit", room=card["room"], sample=False, source="live")
    card = _apply_price_policy(card)
    if settings.units_show_price:
        note = ("ข้อมูลสดจากระบบผังขาย ณ ตอนนี้ สถานะ %s เป็นสถานะจริง "
                "ราคาที่แสดงคือราคาขายจริง บอกข้อมูลสั้นๆ ห้ามคำนวณหรือปัดราคาเอง"
                % (card.get("status_th") or card.get("status")))
    else:
        note = ("ข้อมูลสดจากระบบผังขาย ณ ตอนนี้ สถานะ %s เป็นสถานะจริง "
                "นโยบายคือไม่เปิดเผยตัวเลขราคา ถ้าลูกค้าถามราคา ให้บอกว่า "
                "ราคาและโปรโมชั่นล่าสุดขอให้คุยกับฝ่ายขายโดยตรง ห้ามพูดหรือเดาตัวเลขราคา"
                % (card.get("status_th") or card.get("status")))
    return {"ok": True, "screen": "unit", "unit": card, "sample": False,
            "instruction": note}


@tool(
    name="find_units",
    description=(
        "ค้นห้องว่างจากระบบผังขายตามเงื่อนไข เช่น ราคาไม่เกินสี่ล้าน ขอห้องวิวสระ "
        "ตึก A มีอะไรว่าง ใส่เฉพาะเงื่อนไขที่ลูกค้าพูดเอง "
        "ผลลัพธ์คือรายการจริงจากระบบ ให้อ่านจากรายการเท่านั้น "
        "ห้ามแต่งห้องหรือราคาเพิ่ม ถ้ารายการว่างให้บอกตรงๆ ว่าไม่พบตามเงื่อนไข"
    ),
    parameters={
        "type": "object",
        "properties": {
            "max_price_thb": {"type": "number", "description": "ราคาไม่เกิน (บาท)"},
            "min_price_thb": {"type": "number", "description": "ราคาตั้งแต่ (บาท)"},
            "building": {"type": "string", "description": "รหัสตึก เช่น A"},
            "view_contains": {"type": "string",
                              "description": "คำในชื่อวิว เช่น POOL, LAGOON"},
        },
    },
    tags=["units"],
)
def find_units(max_price_thb: float | None = None,
               min_price_thb: float | None = None,
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
    params: dict = {"select": _LIVE_SELECT, "status": "eq.available",
                    "order": "promo_price.asc", "limit": "8"}
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
    try:
        rows = _live_get(params)
    except Exception:
        logger.exception("live inventory unreachable")
        return {"ok": False, "error": "inventory unreachable",
                "instruction": ("เช็คระบบผังขายไม่ได้ตอนนี้ ให้บอกลูกค้าตรงๆ "
                                "ว่าขอตรวจสอบกับฝ่ายขาย ห้ามแต่งรายการเอง")}
    units_found = [_apply_price_policy(_card_from_live(r)) for r in rows]
    # The query is capped at 8 rows, and "ทั้งหมด 8 ห้อง" was said on screen
    # about a budget that actually fits 105 — the cap spoken as the total.
    # A full count costs a second request; honesty costs a word.
    capped = len(rows) >= 8
    count_phrase = (f"อย่างน้อย {len(units_found)} ห้อง (แสดง {len(units_found)} "
                    "รายการราคาต่ำสุด อาจมีมากกว่านี้)"
                    if capped else f"{len(units_found)} ห้อง")
    turnlog.record("find_units", count=len(units_found), capped=capped,
                   max_price=max_price_thb, building=building)
    return {"ok": True, "screen": "unitlist", "units": units_found,
            "count": len(units_found),
            "capped": capped,
            "instruction": ((f"พบ{count_phrase} " "อ่านจากรายการเท่านั้น ไล่ 2-3 ห้องแรก "
                             "สั้นๆ เลขห้อง ขนาด ราคา ห้ามแต่งห้องเพิ่ม "
                             "รายการนี้เป็นห้องว่างจริง ณ ตอนนี้จากระบบผังขาย")
                            if settings.units_show_price else
                            (f"พบ{count_phrase} " "อ่านจากรายการเท่านั้น ไล่เลขห้องกับขนาด "
                             "2-3 ห้องแรกสั้นๆ ห้ามแต่งห้องเพิ่ม ทุกห้องในรายการอยู่ในงบที่ถาม "
                             "แต่นโยบายคือไม่เปิดเผยตัวเลขราคา ห้ามพูดหรือเดาราคา "
                             "ให้บอกว่าราคาแน่นอนคุยกับฝ่ายขายโดยตรง"))}

# ==================== the live floor plan ====================
#
# The Canva deck's plan slide is a PRELIMINARY CONCEPT rendering; the sales
# system has the real thing — per-floor composite images plus every unit's
# position (percent coordinates) and live status. `show_plan` puts that on
# the stage: the same picture the sales desk stares at, colored by what is
# actually sold *right now*, with no prices anywhere on it (policy).

_PLAN_ASSETS: dict | None = None


def _plan_paths() -> dict[int, str]:
    """floor number -> image path, from the copied assets manifest."""
    global _PLAN_ASSETS
    if _PLAN_ASSETS is None:
        import json
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent.parent / "data" / "floor-plan-assets.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            _PLAN_ASSETS = {int(f["floor"]): f["display_path"]
                            for f in raw.get("floors", []) if f.get("display_path")}
        except (OSError, ValueError):
            logger.exception("could not read floor-plan-assets.json")
            _PLAN_ASSETS = {}
    return _PLAN_ASSETS


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
)
def show_plan(floor: int = 1, building: str | None = None) -> dict:
    if not live_configured():
        return {"ok": False, "error": "no live inventory",
                "instruction": ("เครื่องนี้ยังไม่ได้ต่อระบบผังขาย ให้บอกลูกค้าว่า "
                                "ดูผังกับฝ่ายขายได้โดยตรง ห้ามบรรยายผังจากความจำ")}
    image_path = _plan_paths().get(int(floor))
    if not image_path:
        return {"ok": False, "error": "no plan image", "floor": floor,
                "instruction": ("ไม่มีภาพผังของชั้นนี้ (มีชั้น %s) ให้บอกลูกค้าตรงๆ"
                                % ", ".join(str(k) for k in sorted(_plan_paths())))}
    try:
        rows = _live_get({
            "select": ("unit_no,status,pos_x,pos_y,width,height,poly,"
                       "floors!inner(floor_number,buildings(code))"),
            "floors.floor_number": f"eq.{int(floor)}",
            "limit": "500",
        })
    except Exception:
        logger.exception("live inventory unreachable")
        return {"ok": False, "error": "inventory unreachable",
                "instruction": ("เช็คระบบผังขายไม่ได้ตอนนี้ ให้บอกลูกค้าตรงๆ "
                                "ว่าขอเปิดผังกับฝ่ายขาย ห้ามบรรยายผังจากความจำ")}
    want = (building or "").strip().upper()
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
    return {"ok": True, "screen": "plan",
            "image": settings.inventory_plan_base + image_path,
            "floor": int(floor), "building": want or None,
            "units": marks, "counts": counts,
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
                      params={"select": "id", "limit": "1"},
                      headers={"apikey": settings.inventory_key,
                               "Authorization": f"Bearer {settings.inventory_key}",
                               "Prefer": "count=exact"},
                      timeout=4)
        r.raise_for_status()
        total = (r.headers.get("content-range") or "?/?").split("/")[-1]
        return True, f"unit inventory: LIVE ({total} units at {settings.inventory_url.split('//')[-1]})"
    except Exception as exc:
        return False, ("unit inventory: CONFIGURED BUT UNREACHABLE (%s) — "
                       "show_unit will refuse honestly; check the URL/key in .env"
                       % type(exc).__name__)
