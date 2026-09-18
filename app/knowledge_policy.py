"""Customer disclosure decisions for project knowledge (M0.2).

Metadata is authorization, not a description of construction progress. A
presentation asset may be displayed without authorizing its copy as a claim.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping


POLICY_VERSION = "customer_claim_v1"
CONTENT_STATES = {"existing", "completed", "developing", "preliminary_concept", "proposed"}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    source_id: str
    project_id: str
    content_state: str = ""

    def trace(self) -> dict:
        """Safe to include in a tool result or audit event: no claim text."""
        return {
            "policy": POLICY_VERSION,
            "allowed": self.allowed,
            "reason": self.reason,
            "source_id": self.source_id,
            "project_id": self.project_id,
            "content_state": self.content_state,
        }


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def evaluate_claim(source: Mapping, project_id: str, *, now: datetime | None = None) -> Decision:
    """Deny by default; the source/claim metadata must explicitly allow a guest."""
    source_id = str(source.get("source_id") or "")
    actual_project = str(source.get("project_id") or "")
    state = str(source.get("content_state") or "")

    def result(allowed: bool, reason: str) -> Decision:
        return Decision(allowed, reason, source_id, actual_project, state)

    if not source_id or not actual_project:
        return result(False, "missing_identity")
    if actual_project != project_id:
        return result(False, "wrong_project")
    if source.get("approval_status") and source["approval_status"] != "approved":
        return result(False, "not_approved")
    required = ("approval_status", "approved_by", "approved_at", "effective_at",
                "disclosure_scope", "content_state")
    if any(not source.get(key) for key in required):
        return result(False, "missing_metadata")
    if source["disclosure_scope"] != "customer":
        return result(False, "not_customer_visible")
    if state not in CONTENT_STATES:
        return result(False, "invalid_content_state")
    approved = _timestamp(source["approved_at"])
    effective = _timestamp(source["effective_at"])
    expiry = _timestamp(source.get("expires_at")) if source.get("expires_at") else None
    if not approved or not effective or (source.get("expires_at") and not expiry):
        return result(False, "invalid_timestamp")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("policy clock must be timezone-aware")
    if approved > current or effective > current:
        return result(False, "not_effective")
    if expiry and expiry <= current:
        return result(False, "expired")
    return result(True, "approved_customer_claim")


def state_instruction(state: str) -> str:
    """Approval never turns a concept/developing claim into a completed one."""
    return {
        "developing": "ต้องบอกว่ากำลังพัฒนา ห้ามพูดว่าสร้างเสร็จหรือเปิดใช้งานแล้ว",
        "preliminary_concept": "ต้องบอกว่าเป็นแนวคิดเบื้องต้นและอาจเปลี่ยนแปลงได้",
        "proposed": "ต้องบอกว่าเป็นสิ่งที่เสนอไว้ ยังไม่ยืนยันว่าเกิดขึ้นแล้ว",
    }.get(state, "")


def evaluate_live_inventory(
    *, source_id: str, project_id: str, expected_project_id: str,
    fetched_at: datetime, max_age_seconds: float, customer_visible: bool,
    now: datetime | None = None,
) -> Decision:
    """Operational data uses trusted adapter + freshness, not human approval."""
    def result(allowed: bool, reason: str) -> Decision:
        return Decision(allowed, reason, source_id, project_id, "live")

    if source_id != "live_unit_inventory" or project_id != expected_project_id:
        return result(False, "untrusted_or_wrong_project")
    if not customer_visible:
        return result(False, "not_customer_visible")
    current = now or datetime.now(timezone.utc)
    if fetched_at.tzinfo is None or current.tzinfo is None:
        return result(False, "invalid_timestamp")
    age = (current - fetched_at).total_seconds()
    if age < 0 or age > max_age_seconds:
        return result(False, "stale_inventory")
    return result(True, "fresh_trusted_inventory")
