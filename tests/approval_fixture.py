"""Synthetic approvals for presentation mechanics tests only.

The real catalog stays unreviewed; these copies test the display/tour engine
after a hypothetical owner approval, without changing any production source.
"""


def approved_slides(slides):
    metadata = {
        "approval_status": "approved", "approved_by": "test_reviewer",
        "approved_at": "2026-01-01T00:00:00+07:00",
        "effective_at": "2026-01-01T00:00:00+07:00",
        "expires_at": None, "disclosure_scope": "customer",
        "content_state": "preliminary_concept",
    }
    return [{**slide, **metadata,
             **({"script_approved": True, "script_approved_by": "test_reviewer"}
                if slide.get("script_th") or slide.get("script_en") else {})}
            for slide in slides]
