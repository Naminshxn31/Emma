"""Resolve project data by registered source ID, never by a model-supplied path."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "registry" / "source_registry.json"


@lru_cache(maxsize=4)
def project_identity(project_id: str) -> dict:
    """Read identity from the server registry, never from model arguments."""
    path = source_path("project_identity", project_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    require_project_payload(data, "project_identity", project_id)
    return data


def inventory_slug(project_id: str) -> str:
    return str(project_identity(project_id)["inventory_slug"])


@lru_cache(maxsize=1)
def _sources() -> dict[str, dict]:
    sources = json.loads(REGISTRY.read_text(encoding="utf-8"))["sources"]
    result = {item["id"]: item for item in sources}
    if len(result) != len(sources):
        raise ValueError("duplicate source_id in source registry")
    return result


def source_path(source_id: str, project_id: str) -> Path:
    """Return a registered path only when its project matches server context."""
    source = _sources()[source_id]
    if source.get("project_id") != project_id:
        raise ValueError(f"{source_id} does not belong to {project_id}")
    location = source.get("location")
    if not isinstance(location, str) or not location or "\\" in location:
        raise ValueError(f"{source_id} has no local path")
    path = (ROOT / location).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError(f"{source_id} path escapes repository")
    return path


def require_project_payload(payload: dict, source_id: str, project_id: str) -> None:
    """Fail closed before project data enters prompts, search, or tool results."""
    if (payload.get("source_id") != source_id
            or payload.get("project_id") != project_id):
        raise ValueError(f"{source_id} payload has wrong project or source ID")
