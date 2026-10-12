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


def carries_claim(value: object) -> bool:
    """Whether a fact value says anything that could be spoken as an answer.

    A box holding only punctuation ("-", "—", "...") is how people write
    "nothing here" on a form. It carries no claim, but rendered as a value
    it becomes a fact the model may say ("ราคาเริ่มต้น: -"). Found
    2026-09-29: the owner blanked three facts by typing "-" and approved.
    Letters and digits in any script count (Thai consonants are
    alphabetic), so only content-free strings read as blank. A note like
    "ดูจาก DB" is deliberately NOT caught: it is a sentence, and guessing
    which sentences are notes-to-self is the substring game this repo has
    lost twice. The console tells the owner to leave the box empty instead.

    Only text and numbers can be claims. `str(False)` is "False" and a list
    prints its brackets, so anything else a hand-edited file might hold is
    blank rather than a fact rendered from its Python spelling.
    """
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return False
    return any(ch.isalnum() for ch in str(value))


def state_instruction(state: str) -> str:
    """What a content state makes Emma say (or not say) about a claim.

    preliminary_concept used to force the caveat "เป็นแนวคิดเบื้องต้นและอาจ
    เปลี่ยนแปลงได้" onto every facility, every slide and every search hit.
    The owner overruled it on 2026-10-10: "อย่าบอกว่าเป็นเพียงแนวคิดอาจจะมี
    การเปลี่ยนแปลง ห้ามพูดแบบนี้เป็นอันขาด ... มีคนถามว่ามีอะไรก็บอกไปตามนั้นเลย".
    The state stays (the data console still records it), but its instruction
    now quotes the banned sentence instead of ordering it — saying nothing
    leaves the model hedging on its own when a result is labelled
    preliminary_concept. A question about *when* it opens is still not
    answerable from the facts, so that one still goes to the sales team.

    "ให้บริการ" and "ให้ชม" were banned here for one evening, after the first
    run answered "มีฟิตเนสให้ชมค่ะ". The owner undid it on 2026-10-11: "ให้
    บริการ / ให้ชม อาจจะเป็นรูปภาพให้เขาดู ไม่ได้หมายถึงพาไปดู" — showing a
    picture is a fine meaning of the words. The walk-there reading is rule
    6's job ("ฟิตเนสอยู่ในตัวโครงการค่ะ ไม่ได้อยู่ที่ห้องขายนี้").
    """
    return {
        "developing": "ต้องบอกว่ากำลังพัฒนา ห้ามพูดว่าสร้างเสร็จหรือเปิดใช้งานแล้ว",
        "preliminary_concept": ('บอกได้เลยว่าที่นี่มีอะไร ห้ามพูดว่า "เป็นแนวคิดเบื้องต้น" '
                                'หรือ "อาจมีการเปลี่ยนแปลง" ถูกถามว่าเสร็จหรือเปิดใช้เมื่อไหร่ให้ทีมขายยืนยัน'),
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
