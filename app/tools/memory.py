"""
The memory tools: what lets "จำไว้ด้วย" actually mean something.

Thin on purpose — storage and the prompt block live in `app/memory_store.py`
so that `prompts.py` can read the facts without importing anything that
registers tools. This module is only the `@tool` surface.

The one rule that matters here is inherited from the whole project: what is
stored is what the owner *said to store*, read back on request, and nothing
else. "จำได้ไหม" about something absent from the store gets "ไม่มีในความจำ" —
the prompt says so — because an assistant that invents memories about a real
person is broken in the way that ends trust permanently, not the way a bug
report fixes.
"""
from __future__ import annotations

from app import memory_store, turnlog
from app.tools.registry import tool


@tool(
    name="remember",
    description=(
        "บันทึกข้อเท็จจริงถาวรเกี่ยวกับเจ้าของ ใช้เมื่อเจ้าของบอกให้จำ เช่น "
        "'จำไว้ว่า...' หรือบอกข้อมูลของตัวเองที่ควรจำ (ชื่อคน ของโปรด ตารางประจำ) "
        "เก็บเป็นประโยคสั้นๆ หนึ่งเรื่องต่อครั้ง ข้อมูลจะติดตัวคุณทุกบทสนทนา"
    ),
    parameters={
        "type": "object",
        "properties": {
            "fact": {"type": "string",
                     "description": "ข้อเท็จจริงที่จะจำ เขียนเป็นประโยคบอกเล่าสั้นๆ"},
        },
        "required": ["fact"],
    },
    tags=["memory"],
)
def remember(fact: str) -> dict:
    item = memory_store.add(fact)
    if item is None:
        return {"ok": False, "error": "empty fact"}
    turnlog.record("memory_added", id=item["id"])
    return {
        "ok": True, "id": item["id"], "remembered": item["text"],
        # The session that stored it was built before it existed; the model
        # already has it in conversation context, but the promise it should
        # speak is about *next time*.
        "note": "บันทึกแล้ว จะติดตัวไปทุกบทสนทนาต่อจากนี้",
    }


@tool(
    name="forget_memory",
    description=(
        "ลบความจำหนึ่งรายการ ใช้เมื่อเจ้าของบอกให้ลืม หรือบอกว่าข้อมูลที่จำไว้ผิด "
        "ระบุ id จาก list_memories ถ้าเจ้าของแก้ข้อมูล ให้ลบอันเก่าแล้ว remember อันใหม่"
    ),
    parameters={
        "type": "object",
        "properties": {"id": {"type": "string", "description": "id ของความจำ"}},
        "required": ["id"],
    },
    tags=["memory"],
)
def forget_memory(id: str) -> dict:
    item = memory_store.remove(id)
    if item is None:
        return {"ok": False, "error": "not found",
                "instruction": "ไม่พบรายการนี้ ให้เรียก list_memories ดู id ก่อน"}
    turnlog.record("memory_removed", id=id)
    return {"ok": True, "forgot": item["text"]}


@tool(
    name="list_memories",
    description="ดูทุกอย่างที่จำไว้เกี่ยวกับเจ้าของ พร้อม id สำหรับแก้หรือลบ",
    parameters={"type": "object", "properties": {}},
    tags=["memory"],
)
def list_memories() -> dict:
    items = [{"id": i["id"], "fact": i["text"]} for i in memory_store.facts()]
    return {"ok": True, "items": items, "count": len(items)}
