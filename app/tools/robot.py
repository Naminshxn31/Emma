"""Walking, stopping, and gesturing — the Astronaut robot as callable tools.

Same rule as `smarthome`: handlers return **facts**, not sentences, so the
model phrases the confirmation in whatever language the guest is speaking.
Every result carries `hardware` ("ok" / "mock" / "failed") so it can tell
somebody the robot didn't actually move, instead of announcing a journey that
never happened.

Nothing here holds a tool call open while the robot walks. See
`robot_link.send`.
"""
from __future__ import annotations

import logging

from app.tools import robot_link
from app.tools.registry import tool

logger = logging.getLogger("condo_voice.robot")


@tool(
    name="go_to_place",
    description=(
        "พาลูกค้าเดินไปยังจุดที่ระบุในห้องขาย ใช้เมื่อลูกค้าขอให้พาไปดู เช่น "
        "'พาไปดูห้องตัวอย่าง' 'ขอไปดูโซนสระว่ายน้ำ' 'พาไปที่โต๊ะเซลส์' — "
        "หุ่นยนต์จะเริ่มเดินทันทีและใช้เวลาสักครู่ ระหว่างเดินยังคุยกับลูกค้าได้ตามปกติ "
        "เมื่อถึงแล้วจะมีข้อความแจ้งอีกที ห้ามบอกว่าถึงแล้วก่อนได้รับแจ้ง"
    ),
    parameters={
        "type": "object",
        "properties": {
            "place": {
                "type": "string",
                "description": "ชื่อจุดหมาย ตามที่ลูกค้าพูด เช่น ห้องตัวอย่าง สระว่ายน้ำ ฟิตเนส",
            }
        },
        "required": ["place"],
    },
    tags=["robot"],
)
async def go_to_place(place: str) -> dict:
    """Start walking. Returns as soon as the order is sent, never on arrival.

    The whole reason this returns early is in `robot_link.send`: a synchronous
    function call held open for a thirty-second walk is thirty seconds of a
    robot standing mute next to a customer it just offered to guide.
    """
    target = robot_link.find_place(place)
    if target is None:
        # Do not guess. A wrong slide is corrected with a sentence; a robot
        # leading somebody to the wrong room is not.
        known = ", ".join(robot_link.places())
        return {
            "ok": False,
            "error": "unknown place",
            "known_places": robot_link.places(),
            "instruction": (
                "ไม่รู้จักจุดนี้ในแผนที่ ห้ามเดาและห้ามพาไป "
                + ("จุดที่ไปได้คือ %s ให้ถามลูกค้าว่าหมายถึงจุดไหน" % known
                   if known else
                   "ยังไม่มีจุดไหนในแผนที่เลย ให้บอกลูกค้าตรงๆ แล้วเสนอเรียกเจ้าหน้าที่")
            ),
        }

    hardware = await robot_link.send("move_to_point", place=target)
    if hardware == "ok":
        robot_link.STATE["moving"] = True
        robot_link.STATE["destination"] = target

    return {
        "ok": hardware != "failed",
        "place": target,
        "moving": robot_link.STATE["moving"],
        "hardware": hardware,
        "instruction": (
            "บอกลูกค้าว่ากำลังพาไป แล้วชวนคุยระหว่างทางได้ "
            "ห้ามบอกว่าถึงแล้ว รอจนกว่าจะมีข้อความแจ้งว่าถึง"
            if hardware == "ok" else
            "ยังไม่ได้เชื่อมต่อหุ่นยนต์จริง ให้บอกลูกค้าตรงๆ ว่ายังพาไปไม่ได้ "
            "ห้ามบอกว่ากำลังเดินไป"
        ),
    }


@tool(
    name="stop_moving",
    description=(
        "หยุดหุ่นยนต์ทันที ใช้เมื่อลูกค้าบอกว่า หยุด รอก่อน อย่าเพิ่งไป "
        "ไม่ไปแล้ว stop wait — หรือเมื่อรู้สึกว่ามีอะไรขวางทาง"
    ),
    parameters={"type": "object", "properties": {}},
    tags=["robot"],
)
async def stop_moving() -> dict:
    """Cancel navigation.

    Sent even when we believe the robot is standing still. Our idea of
    `moving` comes from what we last sent plus what the app reported, and if
    those have drifted the guest is looking at the disagreement — a robot that
    replies "I'm not moving" while rolling towards someone is the worst
    possible time to trust a cached flag.
    """
    hardware = await robot_link.send("cancel_navigation")
    was_moving = robot_link.STATE["moving"]
    robot_link.STATE["moving"] = False
    robot_link.STATE["destination"] = None
    return {
        "ok": hardware != "failed",
        "was_moving": was_moving,
        "hardware": hardware,
        "instruction": "หยุดแล้ว ตอบรับสั้นๆ แล้วถามว่าลูกค้าต้องการอะไรต่อ",
    }


@tool(
    name="return_to_base",
    description=(
        "ให้หุ่นยนต์กลับไปยังจุดจอดหรือแท่นชาร์จ ใช้เมื่อลูกค้าบอกว่าดูเสร็จแล้ว "
        "หรือเจ้าหน้าที่สั่งให้กลับที่"
    ),
    parameters={"type": "object", "properties": {}},
    tags=["robot"],
)
async def return_to_base() -> dict:
    hardware = await robot_link.send("go_home")
    if hardware == "ok":
        robot_link.STATE["moving"] = True
        robot_link.STATE["destination"] = "base"
    return {
        "ok": hardware != "failed",
        "hardware": hardware,
        "instruction": "กำลังกลับจุดจอด ให้กล่าวลาลูกค้าสั้นๆ",
    }


@tool(
    name="get_robot_status",
    description=(
        "ดูสถานะหุ่นยนต์ตอนนี้ — แบตเตอรี่ กำลังเดินอยู่ไหม ไปไหนอยู่ "
        "และจุดไหนบ้างที่พาไปได้ ใช้เมื่อลูกค้าถามว่าพาไปไหนได้บ้าง"
    ),
    parameters={"type": "object", "properties": {}},
    tags=["robot"],
)
def get_robot_status() -> dict:
    state = robot_link.snapshot()
    state["places"] = list(robot_link.places())
    state["hardware"] = "ok" if robot_link.available() else "mock"
    if not robot_link.available():
        state["instruction"] = (
            "ยังไม่ได้เชื่อมต่อหุ่นยนต์จริง ถ้าลูกค้าถามเรื่องการพาไป "
            "ให้บอกตรงๆ ว่ายังพาไปไม่ได้"
        )
    return state
