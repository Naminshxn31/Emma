"""Project-specific vocabulary and slide intent routing, kept out of code."""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

from app.config import settings
from app.data_sources import require_project_payload

logger = logging.getLogger("condo_voice.project_knowledge")


def _path() -> Path:
    configured = settings.project_knowledge_file
    return Path(configured).expanduser()


@lru_cache(maxsize=1)
def entities() -> list[dict]:
    try:
        payload = json.loads(_path().read_text(encoding="utf-8"))
        require_project_payload(payload, "project_vocabulary", settings.project_id)
        return payload.get("entities", [])
    except Exception:
        logger.warning("could not load project knowledge from %s", _path(), exc_info=True)
        return []


def matches(query: str) -> list[dict]:
    folded = query.casefold()
    return [
        entity for entity in entities()
        if any(str(alias).casefold() in folded for alias in entity.get("aliases", []))
    ]


def unavailable(query: str) -> bool:
    return any(not entity.get("available", True) for entity in matches(query))


def expand(query: str) -> str:
    hints = [str(entity.get("search_hint")) for entity in matches(query) if entity.get("search_hint")]
    return query if not hints else query + " " + " ".join(hints)


def intent(query: str) -> str:
    if re.search(r"อยู่(?:ตรง|ที่|ชั้น)|ที่ไหน|where|在哪里|どこ|어디|где", query, re.I):
        return "locate"
    if re.search(r"ขอดู|ดูหน่อย|ให้ดู|show|看|見せ|보여|покаж", query, re.I):
        return "show"
    return "explain"


def preferred_slide_ids(query: str) -> list[str]:
    wanted = intent(query)
    ids: list[str] = []
    for entity in matches(query):
        choices = entity.get("preferred_slides", {})
        for slide_id in choices.get(wanted, choices.get("explain", [])):
            if slide_id not in ids:
                ids.append(slide_id)
    return ids


def reset() -> None:
    entities.cache_clear()
