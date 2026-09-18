"""Source-specific runtime routing before customer data reaches Emma."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any, Mapping

from app.data_sources import inventory_slug
from app.knowledge_policy import evaluate_claim, evaluate_live_inventory


INVENTORY_SOURCE_ID = "live_unit_inventory"
MAX_INVENTORY_AGE_S = 60.0
_CARD_FIELDS = frozenset({
    "project_id", "source_id", "room", "building", "floor", "type", "sqm",
    "aspect", "collection", "status", "status_th", "sample", "source",
    "updated_at",
})
_PRICE_FIELDS = frozenset({"price_thb", "base_price_thb"})
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PLAN_PATH = re.compile(r"/[A-Za-z0-9_-]+\.webp\Z")


@dataclass(frozen=True)
class RuntimeResult:
    allowed: bool
    project_id: str
    source_id: str
    reason: str
    payload: dict[str, Any]
    policy: str = "inventory_runtime_v1"

    def trace(self) -> dict[str, Any]:
        return {"policy": self.policy, "allowed": self.allowed,
                "project_id": self.project_id, "source_id": self.source_id,
                "reason": self.reason}


def prepare_inventory_card(
    card: Mapping[str, Any], *, expected_project_id: str,
    max_age_seconds: float, allow_price: bool, now: datetime | None = None,
) -> RuntimeResult:
    """Scope, freshness and field disclosure; deny result has no payload."""
    project_id = str(card.get("project_id") or "")
    source_id = str(card.get("source_id") or "")

    def denied(reason: str) -> RuntimeResult:
        return RuntimeResult(False, project_id, source_id, reason, {})

    if card.get("_project_slug") != inventory_slug(expected_project_id):
        return denied("wrong_or_missing_row_project")
    fetched_at = card.get("_fetched_at")
    if not isinstance(fetched_at, datetime):
        return denied("missing_fetch_timestamp")
    decision = evaluate_live_inventory(
        source_id=source_id, project_id=project_id,
        expected_project_id=expected_project_id, fetched_at=fetched_at,
        max_age_seconds=min(max(max_age_seconds, 1.0), MAX_INVENTORY_AGE_S),
        customer_visible=True, now=now,
    )
    if not decision.allowed:
        return denied(decision.reason)
    fields = _CARD_FIELDS | (_PRICE_FIELDS if allow_price else frozenset())
    payload = {key: card[key] for key in fields if key in card}
    return RuntimeResult(True, project_id, source_id,
                         "fresh_scoped_inventory", payload)


def prepare_static_inventory_card(
    source: Mapping[str, Any], card: Mapping[str, Any], *,
    expected_project_id: str, allow_price: bool,
) -> RuntimeResult:
    """A static export needs explicit approval; a sample is never customer data."""
    source_id = str(source.get("source_id") or "")
    project_id = str(source.get("project_id") or "")
    if source.get("sample"):
        return RuntimeResult(False, project_id, source_id, "sample_inventory", {})
    decision = evaluate_claim(source, expected_project_id)
    if not decision.allowed:
        return RuntimeResult(False, project_id, source_id, decision.reason, {})
    fields = _CARD_FIELDS | (_PRICE_FIELDS if allow_price else frozenset())
    payload = {key: card[key] for key in fields if key in card}
    payload.update(project_id=project_id, source_id=source_id,
                   source="local", sample=False)
    return RuntimeResult(True, project_id, source_id,
                         "approved_local_inventory_snapshot", payload)


def prepare_plan_asset(
    source: Mapping[str, Any], entry: Mapping[str, Any], *,
    expected_project_id: str, observed_sha256: str, content_type: str,
) -> RuntimeResult:
    """A valid image is displayable evidence, never approval of its wording."""
    project_id = str(source.get("project_id") or "")
    source_id = str(source.get("source_id") or "")

    def denied(reason: str) -> RuntimeResult:
        return RuntimeResult(False, project_id, source_id, reason, {},
                             "plan_asset_runtime_v1")

    if source_id != "floor_plan_assets" or project_id != expected_project_id:
        return denied("wrong_plan_source_or_project")
    path = entry.get("display_path")
    digest = entry.get("display_derivative_sha256")
    if not isinstance(path, str) or not _PLAN_PATH.fullmatch(path):
        return denied("invalid_plan_path_or_type")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        return denied("missing_or_invalid_plan_checksum")
    if entry.get("status") != "review-ready":
        return denied("plan_asset_not_ready")
    if content_type.split(";", 1)[0].strip().lower() != "image/webp":
        return denied("wrong_plan_content_type")
    if observed_sha256 != digest:
        return denied("plan_checksum_mismatch")
    return RuntimeResult(True, project_id, source_id,
                         "verified_scoped_plan_asset",
                         {"path": path, "floor": entry.get("floor")},
                         "plan_asset_runtime_v1")
