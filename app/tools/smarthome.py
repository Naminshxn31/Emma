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
"""
from __future__ import annotations

import asyncio

from app.tools import broadlink_ir
from app.tools.registry import tool

AC_TEMP_MIN = 16
AC_TEMP_MAX = 30
FAN_SPEEDS = ("auto", "1", "2", "3")
AC_MODES = ("cool", "dry", "fan", "heat", "auto")

STATE: dict = {
    "lights": True,
    "ac": {"on": True, "temp": 25, "fan": "auto", "mode": "cool"},
}


def snapshot() -> dict:
    return {"lights": STATE["lights"], "ac": dict(STATE["ac"])}


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
    return out


@tool(
    name="set_air_conditioner",
    description=(
        "ควบคุมเครื่องปรับอากาศในห้อง เปิด ปิด ตั้งอุณหภูมิ ความแรงลม หรือโหมด "
        "ใช้เมื่อลูกค้าบอกว่าร้อน หนาว หรือขอปรับแอร์ "
        "ระบุเฉพาะพารามิเตอร์ที่ลูกค้าต้องการเปลี่ยนเท่านั้น"
    ),
    parameters={
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
    },
    tags=["smarthome"],
)
async def set_air_conditioner(
    on: bool | None = None,
    temp: int | None = None,
    fan: str | None = None,
    mode: str | None = None,
) -> dict:
    if on is None and temp is None and fan is None and mode is None:
        return {"ok": False, "error": "no change requested", "ac": dict(STATE["ac"])}

    # Everything below runs off the event loop, like set_lights and for the
    # same measured reason — worse here, because one AC request can be up to
    # FOUR sends, and with the hub unreachable each one walks the full
    # auth-retry-then-discovery path. Seen live: "สั่งปิดแอร์" with the hub
    # absent froze the whole voice session for the duration.
    def _apply() -> tuple[list[str], bool]:
        results: list[str] = []
        clamped = False

        if on is not None and bool(on) != STATE["ac"]["on"]:
            results.append(broadlink_ir.send("ac_on" if on else "ac_off"))
            STATE["ac"]["on"] = bool(on)
        elif on is not None:
            STATE["ac"]["on"] = bool(on)

        if temp is not None:
            requested = int(temp)
            bounded = max(AC_TEMP_MIN, min(AC_TEMP_MAX, requested))
            clamped = bounded != requested
            STATE["ac"]["temp"] = bounded
            if STATE["ac"]["on"]:
                results.append(broadlink_ir.send(f"ac_temp_{bounded}"))

        if fan is not None:
            speed = fan if fan in FAN_SPEEDS else "auto"
            STATE["ac"]["fan"] = speed
            if STATE["ac"]["on"]:
                results.append(broadlink_ir.send(f"ac_fan_{speed}"))

        if mode is not None:
            chosen = mode if mode in AC_MODES else "cool"
            STATE["ac"]["mode"] = chosen
            if STATE["ac"]["on"]:
                results.append(broadlink_ir.send(f"ac_mode_{chosen}"))
        return results, clamped

    results, clamped = await asyncio.to_thread(_apply)
    hardware = _worst(results)

    out = {"ok": hardware != "failed", "ac": dict(STATE["ac"]), "hardware": hardware}
    if hardware == "failed":
        # Same contract as set_lights: a command that never reached the
        # hardware must not come back looking like success.
        out["instruction"] = (
            "ส่งคำสั่งไปที่แอร์ไม่สำเร็จ (ตัวส่งสัญญาณไม่ตอบ) ให้บอกผู้ใช้ตรงๆ "
            "ห้ามยืนยันว่าทำสำเร็จ"
        )
    elif hardware == "ok":
        out["instruction"] = IR_ONE_WAY_NOTE
    if clamped:
        # Tell the model, so it can mention the limit rather than silently
        # confirming a temperature the unit never got.
        out["note"] = f"temperature clamped to {AC_TEMP_MIN}-{AC_TEMP_MAX}"
    return out


@tool(
    name="get_room_status",
    description="ดูสถานะไฟและแอร์ในห้องตอนนี้ ใช้เมื่อถูกถามว่าตอนนี้เปิดอยู่ไหม หรือกี่องศา",
    parameters={"type": "object", "properties": {}},
    tags=["smarthome"],
)
def get_room_status() -> dict:
    status = snapshot()
    status["ok"] = True
    # Be explicit that this is what we commanded, not a sensor reading.
    status["source"] = "last_command" if broadlink_ir.available() else "mock"
    return status
