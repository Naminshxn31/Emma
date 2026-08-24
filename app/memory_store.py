"""
What Emma knows about her owner, across sessions.

Storage only — no tools, no registry import, and that split is the point:
`prompts.py` reads this file to build every Emma session, and importing a
module that carries `@tool` decorators registers those tools as a side
effect, whether the profile's tool groups wanted them or not. The tools live
in `app/tools/memory.py`; anything that just needs the facts comes here.

The file sits next to `data/reminders.json` and shares its rule, not the
log's: this is the owner's own data, told to Emma on purpose, kept until the
owner says otherwise. `data/logs/` is strangers' speech on a 30-day clock.
The two must never share a path or a deletion policy — mixing them is how a
retention cleanup eats someone's memory, or how fear of that quietly kills
the cleanup.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path

from app.config import settings

logger = logging.getLogger("condo_voice.memory")

#: How many facts ride along in the prompt, newest first. The Live API
#: re-processes the whole system instruction every turn, so an unbounded
#: memory would slowly tax every reply's latency. Fifty short facts is
#: roughly the condo facts block's weight; past that the older ones stay in
#: the file (list_memories still shows them) but leave the prompt.
PROMPT_FACT_LIMIT = 50

_facts: list[dict] | None = None


def _path() -> Path:
    return Path(settings.memory_file).expanduser()


def _load() -> list[dict]:
    global _facts
    if _facts is None:
        try:
            _facts = json.loads(_path().read_text(encoding="utf-8"))["facts"]
        except FileNotFoundError:
            _facts = []
        except Exception:
            logger.exception("could not read %s — starting empty", _path())
            _facts = []
    return _facts


def _save() -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"facts": _load()}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def facts() -> list[dict]:
    return list(_load())


def add(text: str) -> dict | None:
    """Store one fact. Returns None for an exact duplicate — remembering the
    same sentence twice pads the prompt without adding a thing."""
    text = (text or "").strip()
    if not text:
        return None
    for existing in _load():
        if existing["text"] == text:
            return existing
    item = {"id": uuid.uuid4().hex[:8], "text": text, "created": time.time()}
    _load().append(item)
    _save()
    return item


def remove(fact_id: str) -> dict | None:
    items = _load()
    for i, item in enumerate(items):
        if item["id"] == fact_id:
            items.pop(i)
            _save()
            return item
    return None


def prompt_block() -> str:
    """The memory section of Emma's instructions.

    Explicit when empty, on purpose: "ยังไม่มีอะไรถูกบันทึก" gives the model
    something true to say to "จำได้ไหมว่า..." — with no section at all it
    guesses, and inventing memories about a real person is the worst lie an
    assistant can tell.
    """
    items = _load()
    if not items:
        return "ความจำเกี่ยวกับเจ้าของ: ยังไม่มีอะไรถูกบันทึกไว้"
    shown = sorted(items, key=lambda i: i["created"])[-PROMPT_FACT_LIMIT:]
    if len(items) > PROMPT_FACT_LIMIT:
        logger.warning(
            "memory has %d facts; only the newest %d ride in the prompt",
            len(items), PROMPT_FACT_LIMIT,
        )
    lines = "\n".join("- %s" % i["text"] for i in shown)
    return "ความจำเกี่ยวกับเจ้าของ (สิ่งที่เขาเคยบอกให้จำ):\n%s" % lines


def reset() -> None:
    """Tests point the store elsewhere; the cache must not outlive that."""
    global _facts
    _facts = None
