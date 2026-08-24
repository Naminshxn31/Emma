"""
Timers, alarms and reminders — the first tools whose job *starts* after the
tool call returns.

Everything else in this registry acts now and reports now. A reminder is a
promise about later, and later has three problems the rest of the codebase
has already met one at a time:

1. **Later may be mid-sentence.** Firing straight into `send_text` is the
   barge-in bug (tour nudge, robot arrival) — the alarm would cut off the
   very person it is for. Delivery goes through `events.announce`, always.

2. **Later may be after the session hung up.** The hybrid mode parks the
   line when the room goes quiet, so the moment an alarm rings there is
   usually *no session at all*. `announce(summon=True)` asks the standby
   browser to dial first — the same path a spoken "Emma" takes — and speaks
   once the line is up. An alarm that only works while a conversation
   happens to be open is a decoration.

3. **Later may be after a restart.** The store is a JSON file, not module
   state; `ensure_watcher` is re-armed at startup when anything is pending.
   A reminder that dies with the process teaches the owner to never trust
   the feature again, once.

Times are epoch seconds internally. The model supplies absolute times as
local "YYYY-MM-DD HH:MM" — it knows the current time because Emma's prompt
carries it (a Live model has no clock of its own).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path

from app import turnlog
from app.config import settings
from app.tools.registry import tool

logger = logging.getLogger("condo_voice.reminders")

#: How long a due item keeps retrying delivery before it is marked missed.
#: An alarm ten minutes late is still an alarm; three hours late it is
#: noise claiming to be help. Missed items stay in the file so
#: list_reminders can answer "ทำไมไม่ปลุก" honestly.
OVERDUE_GIVE_UP_S = 600.0

_items: list[dict] | None = None
_watcher: asyncio.Task | None = None


def _path() -> Path:
    return Path(settings.reminders_file).expanduser()


def _load() -> list[dict]:
    global _items
    if _items is None:
        try:
            _items = json.loads(_path().read_text(encoding="utf-8"))["items"]
        except FileNotFoundError:
            _items = []
        except Exception:
            logger.exception("could not read %s — starting empty", _path())
            _items = []
    return _items


def _save() -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"items": _load()}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def _fmt(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")


def pending() -> list[dict]:
    return [i for i in _load() if i["status"] == "pending"]


def ensure_watcher() -> None:
    """Arm the clock, on the running loop. Safe to call repeatedly.

    Lazy rather than always-on: no loop at import time (the module-level
    asyncio trap this project's conftest documents at length), and no task
    at all while there is nothing to wait for.
    """
    global _watcher
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return                      # no loop yet; startup or a tool call re-arms
    if _watcher is None or _watcher.done():
        _watcher = loop.create_task(_watch())


async def _watch() -> None:
    while True:
        await asyncio.sleep(1.0)
        try:
            await deliver_due()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("reminder delivery failed; will retry")


async def deliver_due() -> None:
    """Fire everything due. Split from the loop so tests can call one tick."""
    now = time.time()
    changed = False
    for item in _load():
        if item["status"] != "pending" or item["due"] > now:
            continue
        overdue = now - item["due"]
        if item["kind"] == "timer":
            text = (
                "จับเวลาที่ตั้งไว้ครบแล้ว%s ให้บอกเจ้าของตอนนี้สั้นๆ"
                % ((" (%s)" % item["label"]) if item["label"] else "")
            )
        else:
            text = (
                "ถึงเวลาที่เจ้าของให้เตือนไว้: %s (ตั้งไว้เวลา %s) "
                "ให้บอกเจ้าของตอนนี้" % (item["label"], _fmt(item["due"]))
            )
        from app import events

        delivered = await events.announce(text, source="reminder", summon=True)
        if delivered:
            item["status"] = "done"
            changed = True
            turnlog.record("reminder_fired", id=item["id"], label=item["label"],
                           late_s=round(overdue))
        elif overdue > OVERDUE_GIVE_UP_S:
            # Nobody to tell, for ten straight minutes. Give up out loud in
            # the log rather than ringing forever — and the item stays in
            # the file as "missed", because "ทำไมไม่ปลุก" deserves a record.
            item["status"] = "missed"
            changed = True
            turnlog.record("reminder_missed", id=item["id"], label=item["label"])
            logger.warning("reminder %r missed — no one to deliver it to",
                           item["label"])
    if changed:
        _save()


def _add(kind: str, label: str, due: float) -> dict:
    item = {
        "id": uuid.uuid4().hex[:8],
        "kind": kind,
        "label": (label or "").strip(),
        "due": due,
        "created": time.time(),
        "status": "pending",
    }
    _load().append(item)
    _save()
    ensure_watcher()
    turnlog.record("reminder_set", id=item["id"], kind=kind, label=item["label"],
                   due=_fmt(due))
    return item


@tool(
    name="set_timer",
    description=(
        "จับเวลานับถอยหลัง ใช้เมื่อเจ้าของบอกให้จับเวลา เช่น 'จับเวลา 10 นาที' "
        "'อีกครึ่งชั่วโมงเตือนที' ครบแล้วจะประกาศเอง"
    ),
    parameters={
        "type": "object",
        "properties": {
            "minutes": {"type": "number", "description": "จำนวนนาที เช่น 10 หรือ 0.5"},
            "label": {"type": "string", "description": "จับเวลาเรื่องอะไร (ไม่บังคับ)"},
        },
        "required": ["minutes"],
    },
    tags=["reminders"],
)
def set_timer(minutes: float, label: str = "") -> dict:
    try:
        minutes = float(minutes)
    except (TypeError, ValueError):
        return {"ok": False, "error": "minutes must be a number"}
    if not 0 < minutes <= 24 * 60:
        # "อะไรนะ 50" was a real print job. A misheard number here becomes a
        # 3am alarm instead of paper, so bound it and make the model confirm.
        return {"ok": False, "error": "out of range",
                "instruction": "จำนวนนาทีผิดปกติ ให้ทวนกับเจ้าของก่อนตั้งใหม่"}
    item = _add("timer", label, time.time() + minutes * 60)
    return {"ok": True, "id": item["id"], "rings_at": _fmt(item["due"]),
            "minutes": minutes}


@tool(
    name="set_reminder",
    description=(
        "ตั้งเตือนตามเวลา ใช้เมื่อเจ้าของบอกให้เตือนอะไรตอนกี่โมง เช่น "
        "'เตือนประชุมบ่ายสาม' 'พรุ่งนี้เจ็ดโมงปลุกด้วย' "
        "คำนวณเวลาจากเวลาปัจจุบันในคำแนะนำของคุณ แล้วส่งเป็นรูปแบบเต็ม"
    ),
    parameters={
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "เตือนเรื่องอะไร จะถูกพูดตอนถึงเวลา"},
            "at": {"type": "string",
                   "description": "เวลาที่จะเตือน รูปแบบ YYYY-MM-DD HH:MM (เวลาท้องถิ่น)"},
        },
        "required": ["message", "at"],
    },
    tags=["reminders"],
)
def set_reminder(message: str, at: str) -> dict:
    try:
        due = datetime.strptime(at.strip(), "%Y-%m-%d %H:%M").timestamp()
    except ValueError:
        return {"ok": False, "error": "bad time format",
                "instruction": "รูปแบบเวลาไม่ถูกต้อง ต้องเป็น YYYY-MM-DD HH:MM"}
    if due < time.time() - 60:
        # The model computed a time already gone — usually it mis-read "now"
        # or the owner said something ambiguous. Never accept silently: an
        # alarm for the past fires instantly, which reads as a haunted robot.
        return {"ok": False, "error": "time is in the past",
                "instruction": "เวลาที่คำนวณได้ผ่านมาแล้ว ให้ถามเจ้าของว่าหมายถึงเมื่อไหร่"}
    if not (message or "").strip():
        return {"ok": False, "error": "empty message"}
    item = _add("reminder", message, due)
    return {"ok": True, "id": item["id"], "at": _fmt(due), "message": item["label"]}


@tool(
    name="list_reminders",
    description="ดูรายการจับเวลาและการเตือนที่ตั้งไว้ รวมทั้งที่พลาดไป",
    parameters={"type": "object", "properties": {}},
    tags=["reminders"],
)
def list_reminders() -> dict:
    items = [
        {"id": i["id"], "kind": i["kind"], "label": i["label"],
         "at": _fmt(i["due"]), "status": i["status"]}
        for i in _load()
        if i["status"] in ("pending", "missed")
    ]
    return {"ok": True, "items": items, "count": len(items)}


@tool(
    name="cancel_reminder",
    description=(
        "ยกเลิกจับเวลาหรือการเตือน ระบุ id จาก list_reminders "
        "ถ้าเจ้าของพูดชื่อเรื่อง ให้เรียก list_reminders หา id ก่อน"
    ),
    parameters={
        "type": "object",
        "properties": {"id": {"type": "string", "description": "id ของรายการ"}},
        "required": ["id"],
    },
    tags=["reminders"],
)
def cancel_reminder(id: str) -> dict:
    for item in _load():
        if item["id"] == id and item["status"] == "pending":
            item["status"] = "cancelled"
            _save()
            turnlog.record("reminder_cancelled", id=id, label=item["label"])
            return {"ok": True, "cancelled": item["label"] or item["kind"]}
    return {"ok": False, "error": "not found",
            "instruction": "ไม่พบรายการนี้ ให้เรียก list_reminders แล้วดู id ที่ถูกต้อง"}


def reset() -> None:
    """Tests re-point the store and run one loop per test; both leak."""
    global _items, _watcher
    _items = None
    _watcher = None
