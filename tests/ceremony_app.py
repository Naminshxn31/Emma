"""The Emma Ceremony app's mode in a test (app/ceremony.py, owner 9 Nov 2026).

On the robot the mode is on while the ceremony app's face call is the active call: session.run
claims it (`ceremony.claim`) and the call's end or a takeover turns it off. A test stands that in
the same way -- a call of profile `ceremony` made `session._active` and claimed -- rather than
writing a flag: the probes then answer from the very state the robot's would (conftest clears
both `_active` and the claim between tests).
"""
from __future__ import annotations

from types import SimpleNamespace


def ceremony_on(replies: str = "cues", session=None):
    """The ceremony app's call is up and claimed. `session` = a fake call of the test's own (its
    profile becomes "ceremony"); none = a bare stand-in with no provider."""
    from app import ceremony
    from app import session as session_module

    call = session if session is not None else SimpleNamespace(
        profile="ceremony", provider=None, _robot_face_claim=True, _page_muted=False)
    call.profile = "ceremony"
    session_module._active = call
    assert ceremony.claim(call, replies=replies)
    return call


def ceremony_off(why: str = "ended") -> None:
    """That call ended (its cleanup in session.handle_connection)."""
    from app import ceremony
    from app import session as session_module

    call = ceremony.STATE["session"]
    if call is not None:
        ceremony.release(call, why)
    if session_module._active is call:
        session_module._active = None
