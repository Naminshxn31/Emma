"""Per-unit plan images for the unit card's "แปลนรายยูนิต" slot.

The sales team's furniture sheets (one PDF per unit, 1,066 of them, owner
2026-10-06: "ข้อมูลโครงการทั้งหมด ... ถูกต้องทั้งหมด") are cropped to the plan
drawing by `scripts/build_unit_plans.py`, which writes one webp per unit and
an `index.json` pinning each file's sha256. Nothing here is a tool: the
model never picks a picture. `show_unit` asks for the room the inventory
already returned, and the screen fetches the image by its hash.

The failure this guards against is the wrong room's plan on a card a
customer can photograph — quieter than a wrong number and just as wrong.
So an image is served only when every link holds: the index belongs to this
project, the entry for A-201 names exactly `A-201.webp` (the builder only
writes that after reading "(A-201)" in the sheet's own title), and the
bytes on disk still hash to what the builder recorded. A file swapped,
re-rendered or copied half-way after the build simply stops appearing; the
card falls back to its labelled blank.

The folder is generated and gitignored (`unit_plan_assets` in the source
registry): a machine without it shows the blank, exactly as before.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from app.config import settings

logger = logging.getLogger("condo_voice.unit_plans")

SOURCE_ID = "unit_plan_assets"
INDEX_NAME = "index.json"
#: A plan crop is ~100 KB; anything near this is not one of ours.
MAX_BYTES = 4 * 1024 * 1024

_ROOM = re.compile(r"[A-Z]{1,2}-\d{3,4}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")

_index_cache: tuple[tuple, dict] | None = None
#: (path, mtime_ns, size) -> sha256 of the bytes, so a card does not hash a
#: file on every lookup but a changed file is re-hashed.
_digest_cache: dict[tuple[str, int, int], str] = {}


def _folder() -> Path | None:
    from app.data_sources import source_path

    try:
        return source_path(SOURCE_ID, settings.project_id)
    except (KeyError, ValueError):
        return None


def _index() -> dict:
    """room -> entry, from the folder's index.json; {} when anything is off."""
    global _index_cache
    folder = _folder()
    if folder is None:
        return {}
    path = folder / INDEX_NAME
    try:
        stat = path.stat()
    except OSError:
        return {}
    key = (str(path), stat.st_mtime_ns, stat.st_size, settings.project_id)
    if _index_cache is not None and _index_cache[0] == key:
        return _index_cache[1]
    units: dict = {}
    try:
        from app.data_sources import require_project_payload

        raw = json.loads(path.read_text(encoding="utf-8"))
        require_project_payload(raw, SOURCE_ID, settings.project_id)
        if isinstance(raw.get("units"), dict):
            units = raw["units"]
    except (OSError, ValueError):
        logger.warning("unit plan index unreadable or for another project: %s", path)
    _index_cache = (key, units)
    return units


def _verified_path(room: str, entry: object) -> Path | None:
    """The file for this room, only if index and bytes still agree."""
    if not isinstance(entry, dict) or not _ROOM.fullmatch(room or ""):
        return None
    name, digest = entry.get("file"), entry.get("sha256")
    # The entry for A-201 may name A-201.webp and nothing else: an index
    # that maps one room to another room's file is the exact failure this
    # module exists for, whoever wrote the index.
    if name != f"{room}.webp" or not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        return None
    folder = _folder()
    if folder is None:
        return None
    path = folder / name
    try:
        stat = path.stat()
    except OSError:
        return None
    if stat.st_size > MAX_BYTES:
        return None
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    actual = _digest_cache.get(key)
    if actual is None:
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return None
        _digest_cache[key] = actual
    if actual != digest:
        logger.warning("unit plan %s does not match its index — not shown", name)
        return None
    return path


def plan_url(room: str | None) -> str | None:
    """Content-addressed URL for the card, or None (the card keeps its blank)."""
    entry = _index().get(room or "")
    if _verified_path(room or "", entry) is None:
        return None
    return f"/unit-plan/{entry['sha256']}"


def plan_body(sha256: str) -> bytes | None:
    """The bytes behind a /unit-plan/<sha256> URL, re-checked on every read."""
    if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
        return None
    for room, entry in _index().items():
        if isinstance(entry, dict) and entry.get("sha256") == sha256:
            path = _verified_path(room, entry)
            if path is None:
                return None
            try:
                body = path.read_bytes()
            except OSError:
                return None
            # Hashed again here, not trusted from the cache: these are the
            # bytes that actually leave the server.
            return body if hashlib.sha256(body).hexdigest() == sha256 else None
    return None


def reset() -> None:
    global _index_cache
    _index_cache = None
    _digest_cache.clear()
