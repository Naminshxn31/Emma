"""Separate display permission from customer-facing slide copy."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping

from app.knowledge_policy import evaluate_claim


_IMAGE_NAME = re.compile(r"[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp|gif|avif)\Z", re.I)
_ASSET_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


@dataclass(frozen=True)
class PresentationDecision:
    project_id: str
    source_id: str
    display: bool
    model_text: bool
    reason: str
    asset: dict

    def trace(self) -> dict:
        return {"policy": "presentation_runtime_v1",
                "project_id": self.project_id, "source_id": self.source_id,
                "capabilities": {"display": self.display,
                                 "model_text": self.model_text},
                "reason": self.reason}


def authorize_slide(slide: Mapping, *, project_id: str,
                    asset_manifest: Mapping) -> PresentationDecision:
    """Never pass source text or arbitrary metadata as display identifiers."""
    def denied(reason: str) -> PresentationDecision:
        return PresentationDecision(project_id, "slide_catalog", False, False,
                                    reason, {})

    if (slide.get("source_id") != "slide_catalog"
            or slide.get("project_id") != project_id):
        return denied("wrong_slide_source_or_project")
    asset_id, filename = slide.get("id"), slide.get("file")
    if (not isinstance(asset_id, str) or not _ASSET_ID.fullmatch(asset_id)
            or not isinstance(filename, str) or not _IMAGE_NAME.fullmatch(filename)):
        return denied("invalid_slide_asset_identifier")
    source = (asset_manifest.get("sources") or {}).get("slide_assets") or {}
    if source.get("project_id") != project_id:
        return denied("wrong_slide_asset_project")
    if not any(item.get("path") == filename
               and isinstance(item.get("sha256"), str)
               and re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
               for item in source.get("files", []) if isinstance(item, dict)):
        return denied("slide_asset_not_registered")
    text = evaluate_claim(slide, project_id)
    return PresentationDecision(project_id, "slide_catalog", True, text.allowed,
                                "display_asset_and_disclosed_text" if text.allowed
                                else "display_asset_only",
                                {"id": asset_id, "url": f"/slides/{filename}"})


def customer_slide_trace(decision, project_id: str, *, valid_source: bool = True) -> dict:
    """Provider-safe trace: denied metadata is never echoed back as copy."""
    allowed = bool(decision.allowed and valid_source)
    return {"policy": "customer_claim_v1", "allowed": allowed,
            "source_id": "slide_catalog", "project_id": project_id,
            "reason": decision.reason if valid_source else "wrong_slide_source",
            "content_state": decision.content_state if allowed else ""}
