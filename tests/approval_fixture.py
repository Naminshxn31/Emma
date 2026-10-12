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


def scripted_slides(slides, asks=None):
    """Synthetic *approved scripts* (and questions) for the script mechanics.

    The 2026-10-06 library (intro-001..048) carries no script_th and no
    ask_th anywhere. SPEAK_SCRIPT, KEEP_GOING, ASK_AND_WAIT and FOLLOW_PAGE
    still exist for the day the sales team writes narration, and the tests
    that guard them used to borrow the real deck's draft scripts — so they
    went red the day the deck changed, for nothing the code did. They pin
    their own copy here instead, the way the approval tests pin their own
    state. The text is visibly a test string so it can never be mistaken for
    sales copy. `asks` maps slide id -> question.
    """
    asks = asks or {}
    out = []
    for slide in approved_slides(slides):
        scripted = {**slide, "script_th": "บททดสอบของ %s ค่ะ" % slide["id"],
                    "script_approved": True, "script_approved_by": "test_reviewer"}
        if slide["id"] in asks:
            scripted["ask_th"] = asks[slide["id"]]
        out.append(scripted)
    return out
