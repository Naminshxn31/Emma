"""
MULTI_SESSION — one server, many testers, no machine.

The showroom architecture is "a robot standing in a room": one Canva window,
one screen, one tour position, and therefore exactly one voice session, with
newest-wins takeover. The office wants everyone to try the assistant at
once, and forking the project for that was rejected for the same reason the
emma profile didn't fork: twenty-plus fixed bugs must not need fixing twice.

So MULTI_SESSION=true changes exactly three decisions, each in one place:

  - `enabled_tool_groups` forces the tool set down to groups that are safe
    to share (config.py — the single point everything reads),
  - `handle_connection` stops superseding and stops assigning `_active`
    (session.py — nobody owns the robot, so announce can deliver to nobody),
  - the display writers refuse input (display.py — N conversations must not
    interleave on one subtitle queue).

And the fourth decision is that with the switch off, nothing anywhere
changes — the showroom default must stay byte-identical after a pull.
"""
from __future__ import annotations

import asyncio

import pytest

from app.config import MULTI_SESSION_DEFAULT, MULTI_SESSION_SAFE, settings


# ---- the tool-set decision -------------------------------------------------

def test_multi_session_strips_machine_bound_groups(monkeypatch):
    """The .env of the office server names computer and smarthome (it is the
    owner's Emma machine); shared mode must refuse them even so — "everyone
    can try it" must not mean "everyone can press keys on the host"."""
    monkeypatch.setattr(settings, "multi_session", True)
    monkeypatch.setattr(settings, "tool_groups",
                        "smarthome,computer,robot,slides,documents,"
                        "reminders,memory,calc,units,knowledge,websearch,mydocs")
    assert settings.enabled_tool_groups() == {
        "units", "knowledge", "websearch", "mydocs"}


def test_multi_session_blank_groups_means_the_safe_default(monkeypatch):
    """Blank TOOL_GROUPS means "everything" on the condo profile — and
    "everything" includes the printer. Under multi-session it must collapse
    to the safe default instead, with mydocs/websearch still opt-in."""
    monkeypatch.setattr(settings, "multi_session", True)
    monkeypatch.setattr(settings, "tool_groups", "")
    monkeypatch.setattr(settings, "assistant_profile", "condo")
    assert settings.enabled_tool_groups() == set(MULTI_SESSION_DEFAULT)


def test_multi_session_filters_the_profile_defaults_too(monkeypatch):
    """Emma's built-in default carries smarthome and computer. The filter has
    to sit *after* the profile branch, or the profile default becomes a way
    around it."""
    monkeypatch.setattr(settings, "multi_session", True)
    monkeypatch.setattr(settings, "tool_groups", "")
    monkeypatch.setattr(settings, "assistant_profile", "emma")
    assert settings.enabled_tool_groups() == {"mydocs", "websearch"}


def test_the_safe_list_is_an_allow_list_of_known_groups():
    """Every name in the safe sets must be a real group, and the default must
    be exactly the non-opt-in survivors — two hand-written lists that agree
    today and drift apart silently is the eval-vs-tool measuring bug."""
    from app import tools

    real = set(tools._TOOL_MODULES.values())
    assert MULTI_SESSION_SAFE <= real
    assert set(MULTI_SESSION_DEFAULT) == MULTI_SESSION_SAFE - tools._OPT_IN


def test_calc_is_not_shareable_until_its_state_is_per_session():
    """calc merges numbers into module STATE across calls; two concurrent
    testers would merge budgets into one sheet and see each other's figures.
    If someone makes that state per-session, this test is the reminder to
    add calc to MULTI_SESSION_SAFE — until then it must stay out."""
    assert "calc" not in MULTI_SESSION_SAFE
    assert "slides" not in MULTI_SESSION_SAFE, "one Canva window, one STATE"


def test_off_means_byte_identical_behavior(monkeypatch):
    """The seam rule: the showroom pulls, nothing changes. With the switch
    off the group computation must be exactly what it always was — including
    None meaning "all groups"."""
    monkeypatch.setattr(settings, "multi_session", False)
    monkeypatch.setattr(settings, "tool_groups", "")
    monkeypatch.setattr(settings, "assistant_profile", "condo")
    assert settings.enabled_tool_groups() is None
    monkeypatch.setattr(settings, "tool_groups", "smarthome,computer")
    assert settings.enabled_tool_groups() == {"smarthome", "computer"}


# ---- the takeover decision -------------------------------------------------

class FakeWS:
    def __init__(self):
        self.closed = False
        self.sent = []

    async def send_text(self, text):
        self.sent.append(text)

    async def close(self):
        self.closed = True


def test_concurrent_sessions_do_not_supersede_each_other(monkeypatch):
    """The whole point of the mode: a colleague opening the page must not
    hang up the colleague who was mid-sentence."""
    from app import session as sess_mod

    monkeypatch.setattr(settings, "multi_session", True)

    first_ws, second_ws = FakeWS(), FakeWS()
    first = sess_mod.VoiceSession.__new__(sess_mod.VoiceSession)
    first.ws = first_ws
    sess_mod._active = first

    async def fake_run(self):
        pass

    monkeypatch.setattr(sess_mod.VoiceSession, "run", fake_run)
    asyncio.run(sess_mod.handle_connection(second_ws))

    assert not first_ws.closed, "the second tester hung up the first"
    assert "superseded" not in "".join(first_ws.sent)
    # And the stale _active we planted was left alone — multi mode neither
    # reads nor writes the slot (cleared here only so later tests start clean).
    assert sess_mod._active is first
    sess_mod._active = None


def test_multi_sessions_never_claim_the_active_slot(monkeypatch):
    """`_active` is how events.announce finds a mouth to speak through. On a
    shared server there is no robot, so no session may ever be the one an
    announcement lands on — an alarm or robot event delivered into a random
    tester's conversation is the handover-privacy bug with N victims."""
    from app import session as sess_mod

    monkeypatch.setattr(settings, "multi_session", True)
    sess_mod._active = None

    seen = []

    async def fake_run(self):
        seen.append(sess_mod._active)

    monkeypatch.setattr(sess_mod.VoiceSession, "run", fake_run)
    asyncio.run(sess_mod.handle_connection(FakeWS()))

    assert seen == [None], "a multi-mode session became _active"
    assert sess_mod._active is None


def test_single_mode_takeover_still_works(monkeypatch):
    """The revert-proof twin: with the switch off, newest still wins and the
    old tab is still told why. Guards against the filter accidentally
    becoming the default path."""
    from app import session as sess_mod

    monkeypatch.setattr(settings, "multi_session", False)

    first_ws = FakeWS()
    first = sess_mod.VoiceSession.__new__(sess_mod.VoiceSession)
    first.ws = first_ws
    sess_mod._active = first

    async def fake_run(self):
        pass

    monkeypatch.setattr(sess_mod.VoiceSession, "run", fake_run)
    asyncio.run(sess_mod.handle_connection(FakeWS()))

    assert first_ws.closed
    assert "superseded" in "".join(first_ws.sent)
    assert sess_mod._active is None


def test_multi_sessions_do_not_poll_the_canva_window():
    """The follower narrates whatever page a person puts on the shared Canva
    window — into whichever conversation is running. N testers × one window
    is the barge-in bug with strangers. The job must be gated at creation,
    like the idle watcher above it: a disabled feature must not be present
    as a task at all."""
    import inspect

    from app import session as sess_mod

    src = inspect.getsource(sess_mod.VoiceSession.run)
    # `_follow_canva` also appears in the comment block above the gate, so
    # the end of the slice must be searched *after* the start.
    start = src.index("canva_poll_s")
    canva_gate = src[start:src.index("_follow_canva", start)]
    assert "multi_session" in canva_gate, \
        "the canva follower is not gated on MULTI_SESSION"


# ---- the machine-screen decision -------------------------------------------

def test_display_writers_accept_nothing_in_multi_mode(monkeypatch):
    """One subtitle queue, N conversations: without this, whatever /display
    is open interleaves strangers' words — the transcript-privacy rule
    broken structurally. The writers must refuse input, not merely happen
    never to be called."""
    from app import display

    monkeypatch.setattr(settings, "multi_session", True)

    async def body():
        await display.say("คำพูดของคนทดลองคนหนึ่ง")

    asyncio.run(body())
    assert display.subtitle_state()["pending"] == 0
    assert display.subtitle_state()["shown"] == ""

    display.set_audio_lead(9000)
    assert display.remaining_lead() == 0.0, \
        "a tester's browser moved the machine's audio clock"


def test_display_writers_still_work_in_single_mode(monkeypatch):
    """Revert-proof twin: the guard must be a gate, not a wall."""
    from app import display

    monkeypatch.setattr(settings, "multi_session", False)

    async def body():
        await display.say("สวัสดีค่ะ")
        # Read before yielding: the drain task is scheduled but has not run,
        # so the chunk must still be queued right here.
        return display.subtitle_state()["pending"]

    assert asyncio.run(body()) == 1
    display.set_audio_lead(5000)
    assert display.remaining_lead() > 0.0
