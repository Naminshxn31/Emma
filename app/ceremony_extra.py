"""The ceremony's extra lines (CEREMONY_EXTRA_LINES, owner 24 Nov 2026 ~19:20): what the script does not answer, as script.

"ก็ไม่ทำบทให้หมดเลย จะได้เสียงเดียวกันไม่ต้อง Gemini  เราก็เอาโมชั่นบทนั้นมาใช้ ... ก็ให้มันทำเป็นบททั้งหมดเลย จะได้ไม่มีปัญหา ก็เหมือนเพิ่มสคริปให้เยอะขึ้น"

`data/ceremony/floor1_ceremony.extra.json` (scripts/build_ceremony_extra.py) is a script file of its own: one line per
approved motion story the script does not answer, its English said from a recording of her voice, its Thai the hall's
subtitle, the story itself the picture. Same signatures as the ceremony's lines (app/event_script.py), never in the
running order, never in her prompt. A line is offered only when a person has signed its words AND its recording: nothing
here is ever read live.

`data/ceremony/floor1_ceremony.extra.host.json` = which of Khun Miki's questions it answers (patterns,
app/ceremony_call.by_meaning); the script's own lines are tried first (app/ceremony_follow.py).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from app.config import settings

logger = logging.getLogger("condo_voice.ceremony_extra")

PREFIX = "x_"
HOST_FORMAT = "emma-ceremony-extra-host-v1"
#: servable() and the recordings' status read files and sources: looked at again after this long.
FRESH_S = 5.0
_STOCK: dict = {}


def reset() -> None:
    _STOCK.clear()


def path() -> Path | None:
    from app import event_script

    script = event_script.path()
    return None if script is None else script.with_suffix(".extra.json")


def on() -> bool:
    from app import ceremony_follow

    return bool(settings.ceremony_extra_lines and ceremony_follow.name_first())


def topic_of(cue_id: str | None) -> str | None:
    return cue_id[len(PREFIX):] if isinstance(cue_id, str) and cue_id.startswith(PREFIX) else None


def _doc() -> dict | None:
    from app import event_script

    target = path()
    if target is None or not target.is_file():
        return None
    stat = target.stat()
    key = (str(target), stat.st_mtime_ns, stat.st_size)
    if _STOCK.get("doc_key") != key:
        _STOCK.update(doc_key=key, doc=event_script.load(target), served=None)
    return _STOCK["doc"]


def servable() -> dict[str, dict]:
    """id -> line, signed in words and voice, its story approved. Never raises; {} when anything is missing."""
    try:
        from app import event_cues, event_script, stories

        doc = _doc()
        if doc is None or not settings.stories_enabled:
            return {}
        held = _STOCK.get("served")
        if held is not None and time.monotonic() - held[0] < FRESH_S:
            return held[1]
        have = stories.available()
        out = {}
        for cue_id, cue in event_script.servable(doc).items():
            if topic_of(cue_id) not in have:
                continue
            line = event_script.spoken(cue, doc)
            if event_cues._voice_status(line).get("approved"):
                out[cue_id] = line
        _STOCK["served"] = (time.monotonic(), out)
        return out
    except Exception:                                    # noqa: BLE001 -- the script, never its extras
        logger.exception("the ceremony's extra lines")
        return {}


def pick(cue_id: str) -> tuple[dict | None, str | None]:
    """event_cues' own question: (the line as it is said, None), or (None, why not)."""
    line = servable().get(cue_id) if on() else None
    return (line, None) if line is not None else (None, "no_cue")


def rules() -> dict:
    """cue id -> the patterns of the questions it answers, for the lines that may be said now."""
    if not on():
        return {}
    target = path()
    host = None if target is None else target.with_name(target.name.replace(".extra.json", ".extra.host.json"))
    try:
        stat = host.stat()
        key = (str(host), stat.st_mtime_ns, stat.st_size)
        if _STOCK.get("host_key") != key:
            raw = json.loads(host.read_text(encoding="utf-8"))
            never = [p for p in raw.get("never") or [] if isinstance(p, str)]
            rows = {}
            for cue_id, row in (raw.get("lines") or {}).items() if raw.get("format") == HOST_FORMAT else ():
                rows[cue_id] = {"means": [p for p in row.get("means") or [] if isinstance(p, str)],
                                "not": [p for p in row.get("not") or [] if isinstance(p, str)],
                                "not_all": never, "next": [], "alike": None, "tell": False}
            _STOCK.update(host_key=key, host=rows)
        rows = _STOCK["host"]
    except (OSError, ValueError, AttributeError):
        logger.warning("the extra lines' questions could not be read", exc_info=True)
        return {}
    offered = servable()
    return {cue_id: row for cue_id, row in rows.items() if cue_id in offered}
