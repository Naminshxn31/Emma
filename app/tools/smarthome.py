"""
Sales-gallery lights and air conditioner, exposed as callable tools.

Ported from `emma`'s smarthome skill, with one deliberate change: handlers
return **facts**, not sentences. Emma returned pre-written Thai replies
because a cascade pipeline fed them straight to TTS. Here the model speaks
for itself, so a fixed Thai string would break the moment a guest asks in
Chinese. `{"ok": true, "lights": true}` lets it phrase the confirmation in
whatever language the conversation is in.

Because IR is one-way, `STATE` is what we last *sent*, not measured truth —
see `broadlink_ir`. Every result carries `hardware` ("ok" / "mock" /
"failed") so the model can tell the guest when a command didn't get through
instead of cheerfully claiming success.

More than one air conditioner (owner, 2026-10-09: "เพิ่มแอร์ของห้องนี้ให้
หน่อยเป็น แอร์ samsung" / "เพิ่มเป็นตัวที่สอง"). The original AC keeps its
`ac_*` codes and stays the default; every other unit is named in
IR_DEVICES_FILE with its own code prefix (`samsung_ac_on`, ...) in the same
codes file, learned with `scripts/learn_ir.py`. A unit takes part only once
its power codes exist on THIS machine — see `active_units` and
`_ac_declaration` for why that is the line between "unchanged" and "two
ACs".
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings
from app.tools import broadlink_ir
from app.tools.registry import tool

logger = logging.getLogger("condo_voice.smarthome")

AC_TEMP_MIN = 16
AC_TEMP_MAX = 30
FAN_SPEEDS = ("auto", "1", "2", "3")
AC_MODES = ("cool", "dry", "fan", "heat", "auto")

STATE: dict = {
    "lights": True,
    "ac": {"on": True, "temp": 25, "fan": "auto", "mode": "cool"},
    # Every AC other than the original, by unit id. The original stays at
    # STATE["ac"] so nothing that reads it (tests, snapshot()) changes.
    "ac_units": {},
}


def snapshot() -> dict:
    return {"lights": STATE["lights"], "ac": dict(STATE["ac"])}


# ---- which air conditioners this room has ----

#: The prefix of the AC this room always had. Its keys (ac_on, ac_temp_25,
#: ...) predate units: never renamed, never relearned, always the default.
ORIGINAL_PREFIX = "ac_"
#: What the remote showed while a whole-state unit's codes were learned,
#: unless IR_DEVICES_FILE says otherwise. learn_ir.py tells the owner to hold
#: the remote at exactly this before every press.
LEARN_BASELINE = {"mode": "cool", "fan": "auto", "temp": 25}

_UNIT_ID = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
_UNIT_PREFIX = re.compile(r"^[a-z][a-z0-9_]*_$")


@dataclass
class AcUnit:
    id: str
    name_th: str
    name_en: str
    prefix: str
    #: Learned from a remote that sends its WHOLE state with every press
    #: (Samsung). Each of its codes therefore also sets mode, fan and
    #: temperature to whatever the remote showed while it was learned
    #: (`learned_with`) — sending "fan 3" after "22 degrees" lands on 25.
    whole_state: bool = False
    learned_with: dict = field(default_factory=lambda: dict(LEARN_BASELINE))

    @property
    def original(self) -> bool:
        return self.prefix == ORIGINAL_PREFIX


ORIGINAL = AcUnit("main", "แอร์ตัวเดิม", "the original air conditioner", ORIGINAL_PREFIX)


def _devices_path() -> Path | None:
    raw = (settings.ir_devices_file or "").strip()
    return Path(raw).expanduser() if raw else None


def _parse_unit(entry) -> AcUnit | None:
    if not isinstance(entry, dict):
        return None
    uid, prefix = entry.get("id"), entry.get("prefix")
    if not isinstance(uid, str) or not _UNIT_ID.match(uid):
        return None
    if not isinstance(prefix, str) or not _UNIT_PREFIX.match(prefix):
        return None
    if prefix != ORIGINAL_PREFIX and prefix.startswith((ORIGINAL_PREFIX, "light_")):
        # `ac_2_` would live inside the original's key space; `light_` is
        # the lamp's. A second unit gets a name of its own or nothing.
        return None

    def _name(key: str) -> str:
        value = entry.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else uid

    learned = dict(LEARN_BASELINE)
    given = entry.get("learned_with")
    if isinstance(given, dict):
        if given.get("mode") in AC_MODES:
            learned["mode"] = given["mode"]
        if given.get("fan") in FAN_SPEEDS:
            learned["fan"] = given["fan"]
        t = given.get("temp")
        if isinstance(t, int) and not isinstance(t, bool) and AC_TEMP_MIN <= t <= AC_TEMP_MAX:
            learned["temp"] = t
    return AcUnit(uid, _name("name_th"), _name("name_en"), prefix,
                  bool(entry.get("whole_state", False)), learned)


def configured_units() -> list[AcUnit]:
    """The original AC first, then every other unit named in IR_DEVICES_FILE.

    A blank setting, a missing file or one that does not parse is the
    original alone — the room as every machine had it before units existed.
    A bad entry is skipped with a warning, never guessed into shape. For the
    original only the names are the owner's to change: its id may be renamed,
    its prefix and behaviour may not.
    """
    path = _devices_path()
    if path is None or not path.is_file():
        return [ORIGINAL]
    try:
        entries = json.loads(path.read_text(encoding="utf-8")).get("air_conditioners")
    except Exception:
        logger.warning("could not read %s — using the original air conditioner only", path)
        return [ORIGINAL]
    if not isinstance(entries, list):
        return [ORIGINAL]
    units = [ORIGINAL]
    for entry in entries:
        unit = _parse_unit(entry)
        if unit is None:
            logger.warning("%s: skipping an air conditioner entry that is not valid", path)
            continue
        if unit.original:
            units[0] = AcUnit(unit.id, unit.name_th, unit.name_en, ORIGINAL_PREFIX)
            continue
        units.append(unit)
    out: list[AcUnit] = []
    for unit in units:
        if any(u.id == unit.id or u.prefix == unit.prefix for u in out):
            logger.warning("%s: duplicate air conditioner id or prefix %r skipped", path, unit.id)
            continue
        out.append(unit)
    return out


def unit_code_suffixes() -> list[str]:
    """Every code a unit can be sent, in the order learn_ir.py teaches them.

    One list for both sides, so the script can never learn a key the tool
    does not send (or the other way round)."""
    return (["off", "on"] + [f"mode_{m}" for m in AC_MODES]
            + [f"temp_{t}" for t in range(AC_TEMP_MIN, AC_TEMP_MAX + 1)]
            + [f"fan_{f}" for f in FAN_SPEEDS])


def is_learned(unit: AcUnit, codes: dict) -> bool:
    """A unit exists for the model once both power codes are on this machine."""
    return bool(codes.get(f"{unit.prefix}on")) and bool(codes.get(f"{unit.prefix}off"))


def active_units() -> list[AcUnit]:
    """The ACs the model may address: the original, always, plus each other
    configured unit whose power codes are learned in this machine's codes
    file. Listing a unit in IR_DEVICES_FILE (a tracked file every machine
    pulls) therefore changes nothing until somebody learns it *there*."""
    units = configured_units()
    if len(units) == 1:
        return units
    codes = broadlink_ir.load_codes()
    return [units[0]] + [u for u in units[1:] if is_learned(u, codes)]


def _state_for(unit: AcUnit) -> dict:
    if unit.original:
        return STATE["ac"]
    per_unit = STATE.setdefault("ac_units", {})
    if unit.id not in per_unit:
        # Same "boot default" as the original: on, and what the remote is
        # assumed to show. What we last sent, never a reading of the room.
        learned = unit.learned_with
        per_unit[unit.id] = {"on": True, "temp": learned["temp"],
                             "fan": learned["fan"], "mode": learned["mode"]}
    return per_unit[unit.id]


def _describe(unit: AcUnit, state: dict) -> dict:
    return {"unit": unit.id, "name_th": unit.name_th, "name_en": unit.name_en, **state}


#: Attached to EVERY successful send — a rule the model must remember
#: across turns is a rule it loses (the UNITS_SAMPLE lesson). Live,
#: 2026-08-26 15:55: the hub accepted every frame ("ok" was truthful), the
#: lamp never reacted, the owner said "แปลว่าเปิดไฟไม่ได้" — and the model
#: argued back "เปิดได้ปกติเลยค่ะ ... ตอนนี้ไฟเปิดอยู่ค่ะ". IR is one-way:
#: "ok" means the hub took the payload, not that the room changed. Nobody
#: on this side can see the room; the person standing in it can.
IR_ONE_WAY_NOTE = (
    "ส่งสัญญาณถึงตัวส่ง IR แล้ว แต่ IR เป็นทางเดียว มองไม่เห็นว่าอุปกรณ์เปลี่ยนจริงไหม "
    "ถ้าผู้ใช้บอกว่าไฟหรือแอร์ไม่ตอบสนอง ให้เชื่อเขาทันที ห้ามเถียงว่า 'เปิดได้ปกติ' "
    "ให้แนะนำเช็คว่าหัวส่งสัญญาณหันหาอุปกรณ์และไม่มีของบัง"
)

#: Attached to every MOCK result. "failed" and "ok" each had an instruction
#: riding on the result; "mock" had none, so it fell back to rule 8 at the
#: end of the system prompt — and a rule there is a rule the model skips.
#: Measured live 2026-10-07 (scripts/eval_live_qa.py), 3 of 3 mock results
#: were announced as done: "ได้ค่ะ ปิดไฟให้แล้วนะคะ", "ปรับอุณหภูมิแอร์เป็น
#: ยี่สิบสามองศาให้เรียบร้อยแล้วค่ะ", "I've adjusted the air conditioning".
#: The robot tools carry exactly this kind of line on mock and were never
#: misreported. So the sentences it said are quoted back at it here.
IR_MOCK_NOTE = (
    "ยังไม่ได้ต่อตัวส่งสัญญาณ IR จริง ไม่มีสัญญาณถูกส่งออกไปเลย ห้ามพูดว่า "
    "'ปิดไฟให้แล้ว' 'ปรับแอร์ให้เรียบร้อยแล้ว' หรือ 'I've adjusted the air conditioning' "
    "ให้บอกตรงๆ ว่าตอนนี้ยังสั่งไฟหรือแอร์จากตรงนี้ไม่ได้"
)

#: get_room_status without an IR hub: the values are the boot defaults, not
#: the room. Measured on the robot face 9 Oct (no data/broadlink_device.json
#: there): "ตอนนี้ไฟเปิดอยู่ และแอร์ทำงานอยู่ที่อุณหภูมิยี่สิบห้าองศาเซลเซียสค่ะ".
ROOM_STATUS_MOCK_NOTE = (
    "ยังไม่ได้ต่อตัวส่งสัญญาณ IR จริง ค่าพวกนี้เป็นค่าเริ่มต้นของระบบ ไม่ใช่สถานะของห้องตอนนี้ "
    "ห้ามพูดว่า 'ตอนนี้ไฟเปิดอยู่ และแอร์ทำงานอยู่ที่ยี่สิบห้าองศา' "
    "ให้บอกตรงๆ ว่าตอนนี้ยังเช็คหรือสั่งไฟแอร์จากตรงนี้ไม่ได้"
)


def _worst(results: list[str]) -> str:
    """Worst outcome wins.

    Deliberately never upgrades a result: an earlier version treated "mock"
    as "ok" whenever a hub config file existed, so with the `broadlink`
    package missing the assistant cheerfully announced it had switched the
    air conditioner off while nothing had been sent at all.
    """
    if "failed" in results:
        return "failed"
    if "mock" in results:
        return "mock"
    return "ok"


@tool(
    name="set_lights",
    description=(
        "เปิดหรือปิดไฟในห้อง ใช้เมื่อถูกขอให้เปิดไฟ ปิดไฟ "
        "หรือบอกว่าห้องมืดหรือสว่างเกินไป"
    ),
    parameters={
        "type": "object",
        "properties": {
            "on": {"type": "boolean", "description": "true เพื่อเปิดไฟ, false เพื่อปิดไฟ"}
        },
        "required": ["on"],
    },
    confirm=False,
    tags=["smarthome"],
)
async def set_lights(on: bool) -> dict:
    # The learned remote has separate on/off frames, and the link drops
    # packets, so send explicitly every time rather than trusting STATE.
    # Broadlink performs blocking UDP auth/retries. Run it off the event loop;
    # otherwise one unreachable hub freezes Gemini's WebSocket and the UI.
    hardware = await asyncio.to_thread(
        broadlink_ir.send, "light_on" if on else "light_off", 2, 0.6
    )
    STATE["lights"] = bool(on)
    out = {"ok": hardware != "failed", "lights": STATE["lights"], "hardware": hardware}
    if hardware == "failed":
        out["instruction"] = (
            "ส่งคำสั่งไปที่ตัวควบคุมไฟไม่สำเร็จ ให้บอกผู้ใช้ตรงๆ ว่าไฟยังไม่ได้ปิดหรือเปิด "
            "ห้ามยืนยันว่าทำสำเร็จ"
        )
    elif hardware == "ok":
        out["instruction"] = IR_ONE_WAY_NOTE
    else:
        out["instruction"] = IR_MOCK_NOTE
    return out


#: "ใช้เมื่อลูกค้าบอกว่าร้อน" (the wording until 10 Oct) read small talk as a
#: request: "วันนี้อากาศร้อนจังเลยนะ" — a remark about the weather — made
#: Emma call this tool in both live content runs (docs/robot-content-review-
#: 2026-10-09.md). The sales room drives a real AC by IR with this exact
#: declaration, so the trigger sentence is quoted as a non-request (the rule
#: phrased as the thing about to happen is the one this model follows) and
#: "it's hot in here" becomes a question first, then a call on a yes.
_AC_DESCRIPTION = (
    "ควบคุมเครื่องปรับอากาศในห้อง เปิด ปิด ตั้งอุณหภูมิ ความแรงลม หรือโหมด "
    "ใช้เฉพาะเมื่อลูกค้าขอให้เปิด ปิด หรือปรับแอร์ชัดๆ หรือตอบตกลงหลังถูกถามว่าให้ปรับไหม "
    "คำบ่นเรื่องอากาศ เช่น 'วันนี้อากาศร้อนจังเลย' ไม่ใช่คำขอ ห้ามเรียกเครื่องมือนี้ ให้คุยต่อตามปกติ "
    "ถ้าบอกว่าในห้องร้อนหรือหนาว ให้ถามก่อนว่าอยากให้ปรับแอร์ไหม "
    "ระบุเฉพาะพารามิเตอร์ที่ลูกค้าต้องการเปลี่ยนเท่านั้น"
)
_AC_PARAMETERS = {
    "type": "object",
    "properties": {
        "on": {"type": "boolean", "description": "true เพื่อเปิดแอร์, false เพื่อปิด"},
        "temp": {
            "type": "integer",
            "description": f"อุณหภูมิเป็นองศาเซลเซียส {AC_TEMP_MIN}-{AC_TEMP_MAX}",
            "minimum": AC_TEMP_MIN,
            "maximum": AC_TEMP_MAX,
        },
        "fan": {
            "type": "string",
            "enum": list(FAN_SPEEDS),
            "description": "ความแรงลม auto หรือระดับ 1-3",
        },
        "mode": {
            "type": "string",
            "enum": list(AC_MODES),
            "description": "โหมดการทำงาน",
        },
    },
}


def _ac_declaration() -> tuple[str, dict]:
    """The declaration of set_air_conditioner on THIS machine.

    Why a hook on the one tool, and not a `unit` argument always or a second
    tool: the sales room's declarations must stay byte-identical after a pull
    (tests/data/body_nav_p0_snapshot.json), and data/ir_devices.json — which
    names the Samsung unit — is tracked, so every machine pulls it. An
    argument that was always there would change every machine's tools; a
    second tool cannot work at all, because "เปิดแอร์" with no unit named
    would still go to the original tool and silently pick the original AC.
    So the surface is decided by what only exists locally: a second unit's
    learned power codes. With one unit this returns the very objects the
    tool was registered with (identity, not just equality — tests check
    `is`), so nothing about the default declaration can drift.
    """
    units = active_units()
    if len(units) < 2:
        return _AC_DESCRIPTION, _AC_PARAMETERS
    parameters = copy.deepcopy(_AC_PARAMETERS)
    parameters["properties"]["unit"] = {
        "type": "string",
        "enum": [u.id for u in units],
        "description": "แอร์ตัวไหน: " + ", ".join(
            f"{u.id} = {u.name_th} ({u.name_en})" for u in units),
    }
    description = (
        _AC_DESCRIPTION
        + f" ห้องนี้มีแอร์ {len(units)} ตัว ใส่ unit เฉพาะเมื่อลูกค้าบอกชัดว่าตัวไหน "
        "ถ้าไม่ได้บอก ห้ามเลือกเอง ให้เรียกโดยไม่ใส่ unit แล้วทำตามที่เครื่องมือตอบกลับ "
        "ถ้าขอทุกตัว ให้เรียกทีละตัว"
    )
    return description, parameters


def _which_one(units: list[AcUnit], asked: str | None) -> dict:
    """Nothing sent: either no unit was named where there is more than one,
    or the one named is not an AC this room can drive.

    The sentences a guess would produce are quoted back — both said for real
    by this model about an AC command that never went out (2026-10-07, see
    IR_MOCK_NOTE and the power-skip comment in `_apply_simple`). A rule
    phrased as the words about to be said is the one it follows (CLAUDE.md
    "อ้างคำที่โมเดลพูดผิดจริงๆ").
    """
    names = " หรือ ".join(u.name_th for u in units)
    out = {"ok": False,
           "error": "unit required" if asked is None else "unknown unit",
           "units": [{"unit": u.id, "name_th": u.name_th, "name_en": u.name_en}
                     for u in units]}
    if len(units) == 1:
        out["instruction"] = (
            f"ห้องนี้มีแอร์ตัวเดียว ({units[0].name_th}) ไม่มีแอร์ชื่อนั้น ยังไม่ได้ส่งสัญญาณใดๆ "
            "ให้เรียก set_air_conditioner ใหม่โดยไม่ใส่ unit")
        return out
    lead = ("คำขอไม่ได้บอกว่าแอร์ตัวไหน" if asked is None
            else "ไม่มีแอร์ชื่อนั้นในห้องนี้")
    out["instruction"] = (
        f"{lead} ห้องนี้มีแอร์ {len(units)} ตัว ยังไม่ได้ส่งสัญญาณใดๆ ห้ามพูดว่า "
        "'ปรับแอร์ให้เรียบร้อยแล้ว' หรือ 'I've turned on the air conditioner for you' "
        f"และห้ามเลือกเอง ให้ถามสั้นๆ ว่า 'แอร์ตัวไหนคะ {names}' "
        "(ใช้ภาษาเดียวกับลูกค้า) แล้วเรียก set_air_conditioner ใหม่พร้อม unit "
        "ถ้าลูกค้าบอกว่าทุกตัว ให้เรียกทีละตัว")
    return out


def _pick(unit: str | None, units: list[AcUnit]) -> AcUnit | dict:
    if unit is None or (isinstance(unit, str) and not unit.strip()):
        return units[0] if len(units) == 1 else _which_one(units, None)
    key = str(unit).strip().lower()
    for candidate in units:
        if candidate.id == key:
            return candidate
    return _which_one(units, str(unit))


#: Quoted per field, like the other notes: the exact claim the model would
#: make about a setting this unit's codes could not apply in the same call.
_FIELD_CLAIMS = {"temp": ("อุณหภูมิ", "ปรับอุณหภูมิให้แล้ว"),
                 "mode": ("โหมด", "เปลี่ยนโหมดให้แล้ว"),
                 "fan": ("ความแรงลม", "ปรับความแรงลมให้แล้ว")}

_MISSING_CODE_NOTE = (
    "ยังไม่ได้สอนรหัสรีโมตของคำสั่งนี้ให้แอร์ตัวนี้ จึงไม่มีสัญญาณถูกส่งออกไป ห้ามพูดว่า "
    "'ปรับแอร์ให้เรียบร้อยแล้ว' ให้บอกตรงๆ ว่าแอร์ตัวนี้ยังตั้งค่านี้จากตรงนี้ไม่ได้"
)


def _send_for(unit: AcUnit, suffix: str, missing: list[str]) -> str:
    """One code of `unit`. The original's path is exactly what it always was
    (broadlink_ir.send); a later unit may be partly learned — learn_ir.py
    lets the owner skip a code — and a code that is not on this machine is
    named as such instead of surfacing as "the hub did not answer"."""
    key = f"{unit.prefix}{suffix}"
    if not unit.original and not broadlink_ir.load_codes().get(key):
        missing.append(suffix)
        return "failed"
    return broadlink_ir.send(key)


def _apply_simple(unit: AcUnit, state: dict, on, temp, fan, mode,
                  missing: list[str]) -> tuple[list[str], bool]:
    """Each setting is its own code. For the original unit this is the code
    that has always run, with `ac_` spelled as the unit's prefix."""
    results: list[str] = []
    clamped = False

    # Sent every time it is asked for, like set_lights, never skipped
    # because STATE already agrees. STATE is what we last sent, not the
    # room: the boot default is "on", and anybody with the wall remote
    # makes it stale. The skip turned "เปิดแอร์" on a freshly started
    # server into zero codes sent and an "ok" — measured 2026-10-07,
    # Emma: "I've turned on the air conditioner for you." Nothing went out.
    if on is not None:
        results.append(_send_for(unit, "on" if on else "off", missing))
        state["on"] = bool(on)

    if temp is not None:
        requested = int(temp)
        bounded = max(AC_TEMP_MIN, min(AC_TEMP_MAX, requested))
        clamped = bounded != requested
        state["temp"] = bounded
        if state["on"]:
            results.append(_send_for(unit, f"temp_{bounded}", missing))

    if fan is not None:
        speed = fan if fan in FAN_SPEEDS else "auto"
        state["fan"] = speed
        if state["on"]:
            results.append(_send_for(unit, f"fan_{speed}", missing))

    if mode is not None:
        chosen = mode if mode in AC_MODES else "cool"
        state["mode"] = chosen
        if state["on"]:
            results.append(_send_for(unit, f"mode_{chosen}", missing))
    return results, clamped


def _apply_whole_state(unit: AcUnit, state: dict, on, temp, fan, mode,
                       missing: list[str]) -> tuple[list[str], bool, list[str], list[str]]:
    """A remote that sends its whole state (Samsung): every learned code also
    carries the mode/fan/temperature the remote showed while it was learned.

    So STATE follows the frame actually sent — "fan 3" after "22 degrees"
    really is 25 degrees again, and saying 22 would be the stale-STATE lie in
    a new place. And of temperature/mode/fan only ONE can be honoured per
    call: sending two frames means the second silently undoes the first. The
    first asked for, in that order, is sent; the rest come back as
    `not_applied`. Power always goes first and never conflicts.
    """
    results: list[str] = []
    clamped = False
    learned = unit.learned_with
    if on is not None:
        results.append(_send_for(unit, "on" if on else "off", missing))
        state["on"] = bool(on)
        if on:
            state.update(temp=learned["temp"], fan=learned["fan"], mode=learned["mode"])

    wanted: list[tuple[str, object]] = []
    if temp is not None:
        requested = int(temp)
        bounded = max(AC_TEMP_MIN, min(AC_TEMP_MAX, requested))
        clamped = bounded != requested
        wanted.append(("temp", bounded))
    if mode is not None:
        wanted.append(("mode", mode if mode in AC_MODES else "cool"))
    if fan is not None:
        wanted.append(("fan", fan if fan in FAN_SPEEDS else "auto"))

    not_applied: list[str] = []
    changed_too: list[str] = []
    # Off: nothing is sent and nothing is remembered — the next power-on
    # frame brings the learned state, not this one, so remembering it would
    # be a promise the unit cannot keep.
    if wanted and state["on"]:
        field_name, value = wanted[0]
        results.append(_send_for(unit, f"{field_name}_{value}", missing))
        frame = {"temp": learned["temp"], "fan": learned["fan"], "mode": learned["mode"]}
        frame[field_name] = value
        changed_too = [k for k in ("temp", "mode", "fan")
                       if k != field_name and state.get(k) != frame[k]]
        state.update(frame)
        not_applied = [name for name, _ in wanted[1:]]
    return results, clamped, not_applied, changed_too


@tool(
    name="set_air_conditioner",
    description=_AC_DESCRIPTION,
    parameters=_AC_PARAMETERS,
    tags=["smarthome"],
    declare=_ac_declaration,
)
async def set_air_conditioner(
    on: bool | None = None,
    temp: int | None = None,
    fan: str | None = None,
    mode: str | None = None,
    unit: str | None = None,
) -> dict:
    units = active_units()
    several = len(units) > 1
    if on is None and temp is None and fan is None and mode is None:
        if not several:
            return {"ok": False, "error": "no change requested", "ac": dict(STATE["ac"])}
        return {"ok": False, "error": "no change requested",
                "air_conditioners": [_describe(u, _state_for(u)) for u in units]}

    # Never a guess. With two units and none named, nothing is sent and no
    # STATE moves; the result tells the model to ask (see _which_one).
    picked = _pick(unit, units)
    if isinstance(picked, dict):
        return picked
    state = _state_for(picked)
    missing: list[str] = []

    # Everything below runs off the event loop, like set_lights and for the
    # same measured reason — worse here, because one AC request can be up to
    # FOUR sends, and with the hub unreachable each one walks the full
    # auth-retry-then-discovery path. Seen live: "สั่งปิดแอร์" with the hub
    # absent froze the whole voice session for the duration.
    not_applied: list[str] = []
    changed_too: list[str] = []
    if picked.whole_state:
        results, clamped, not_applied, changed_too = await asyncio.to_thread(
            _apply_whole_state, picked, state, on, temp, fan, mode, missing)
    else:
        results, clamped = await asyncio.to_thread(
            _apply_simple, picked, state, on, temp, fan, mode, missing)

    def _tagged(out: dict) -> dict:
        # The original unit on a one-AC machine answers exactly as it always
        # did; naming the unit is only for a room that has a choice.
        if several or not picked.original:
            out["unit"] = picked.id
            out["name_th"] = picked.name_th
            out["name_en"] = picked.name_en
        return out

    if not results:
        # Only reachable now when the AC is off by our last command and the
        # request was temperature/fan/mode alone. `_worst([])` is "ok", which
        # is how an empty send list used to come back as a success.
        out = {"ok": False, "ac": dict(state), "hardware": "failed",
               "error": "nothing sent: the AC is off",
               "instruction": ("แอร์ปิดอยู่ตามคำสั่งล่าสุด จึงยังไม่ได้ส่งสัญญาณอะไรเลย "
                               "ห้ามบอกว่าปรับแล้ว ให้ถามว่าจะให้เปิดแอร์ไหม")}
        if clamped:
            out["note"] = f"temperature clamped to {AC_TEMP_MIN}-{AC_TEMP_MAX}"
        return _tagged(out)
    hardware = _worst(results)

    out = {"ok": hardware != "failed", "ac": dict(state), "hardware": hardware}
    if missing:
        out["missing_codes"] = missing
        out["instruction"] = _MISSING_CODE_NOTE
    elif hardware == "failed":
        # Same contract as set_lights: a command that never reached the
        # hardware must not come back looking like success.
        out["instruction"] = (
            "ส่งคำสั่งไปที่แอร์ไม่สำเร็จ (ตัวส่งสัญญาณไม่ตอบ) ให้บอกผู้ใช้ตรงๆ "
            "ห้ามยืนยันว่าทำสำเร็จ"
        )
    elif hardware == "ok":
        out["instruction"] = IR_ONE_WAY_NOTE
    else:
        out["instruction"] = IR_MOCK_NOTE
    notes = []
    if clamped:
        # Tell the model, so it can mention the limit rather than silently
        # confirming a temperature the unit never got.
        notes.append(f"temperature clamped to {AC_TEMP_MIN}-{AC_TEMP_MAX}")
    if changed_too:
        notes.append("this unit's codes carry the remote's whole state, so "
                     + ", ".join(f"{k} is now {state[k]}" for k in changed_too))
    if notes:
        out["note"] = "; ".join(notes)
    if not_applied:
        out["not_applied"] = not_applied
        labels = " ".join(_FIELD_CLAIMS[k][0] for k in not_applied)
        claims = " ".join(f"'{_FIELD_CLAIMS[k][1]}'" for k in not_applied)
        out["instruction"] += (
            f" แอร์ตัวนี้ส่งค่าได้ทีละอย่าง ครั้งนี้ยังไม่ได้เปลี่ยน{labels} ห้ามพูดว่า {claims} "
            f"ให้บอกว่าตั้งได้ทีละอย่าง แล้วถามว่าจะให้เปลี่ยน{labels}ต่อไหม")
    return _tagged(out)


@tool(
    name="get_room_status",
    description="ดูสถานะไฟและแอร์ในห้องตอนนี้ ใช้เมื่อถูกถามว่าตอนนี้เปิดอยู่ไหม หรือกี่องศา",
    parameters={"type": "object", "properties": {}},
    tags=["smarthome"],
)
def get_room_status() -> dict:
    status = snapshot()
    units = active_units()
    if len(units) > 1:
        # Every unit, each with its name. A lone "ac" next to a list would be
        # read as *the* air conditioner, which in this room it no longer is.
        del status["ac"]
        status["air_conditioners"] = [_describe(u, dict(_state_for(u))) for u in units]
    status["ok"] = True
    # Be explicit that this is what we commanded, not a sensor reading.
    status["source"] = "last_command" if broadlink_ir.available() else "mock"
    if status["source"] == "mock":
        # "source: mock" alone did not stop it: on the robot (no IR hub
        # config) a three-word fragment of room talk got the boot defaults
        # read out as the room —
        # the same missing-instruction-on-mock gap IR_MOCK_NOTE closed for
        # the setters. Quoted, because quoting is what this model obeys.
        status["instruction"] = ROOM_STATUS_MOCK_NOTE
    return status
