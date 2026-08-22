"""Shared test setup.

The turn log is the important thing here. It defaults to on, which is right
for a robot in a showroom and wrong for a test suite: running the tests wrote
several hundred lines of fixture data into the same file the operator reads to
find out what happened with real visitors.

That is not a cosmetic problem. The whole point of the log is to answer
questions with evidence, and the first time it was read the report said 61
sessions and 33 pricing questions — every one of them from pytest. A record
that mixes real events with invented ones is worse than no record: it looks
authoritative and it is wrong.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_turn_log(monkeypatch):
    """Keep the tests out of data/logs/.

    autouse so it cannot be forgotten. A test that genuinely wants to exercise
    logging should point `turn_log_dir` at tmp_path itself.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "turn_log", False)


@pytest.fixture(autouse=True)
def _fresh_async_state():
    """Give every test its own locks, tasks and audio clock.

    These tests run `asyncio.run()` per test, so **every test is a different
    event loop**, while `display._lock`, `canva_display._lock`, `_reveal_task`
    and `_task` are created once at import and live for the whole session.
    That combination has two failure modes and both of them are hangs, which
    is the worst kind because the stack trace points at the innocent test that
    happened to run next.

    A lock is left `locked` if whatever held it was abandoned when its loop
    closed. Nothing can ever release it again — its owner's loop is gone — so
    the next test to reach `async with _lock` waits forever.

    The audio clock is worse because it looks reasonable. `_audio_until` is a
    plain `time.monotonic()` deadline, so a test that sets a long lead and
    doesn't wait it out leaves it in the future, and the next test's
    `next_slide` sits in `wait_until_heard` for the full
    `SLIDE_TOOL_BUDGET_S` — twenty seconds, which is over pytest's timeout.
    Observed on Windows with Python 3.14 as
    `test_the_tour_waits_for_the_guest_to_hear_the_slide` hanging, having
    passed everywhere else.

    Cheap to reset and impossible to forget, so reset it.
    """
    import asyncio

    from app import display, events
    from app.tools import canva_display

    display._lock = asyncio.Lock()
    canva_display._lock = asyncio.Lock()
    events._lock = asyncio.Lock()

    # A session left registered by an earlier test is the same class of leak
    # as the locks, and it fails in the more embarrassing direction: the next
    # test's announcements are delivered to a provider belonging to a session
    # that no longer exists, and the assertions read as if they had gone
    # nowhere. Cleared on both sides so neither order can carry it.
    from app import session as session_module

    session_module._active = None

    # The suite must not change colour when the machine's .env does — the
    # documented trap ("tests reading machine state") in its newest costume.
    # Turning the owner's Emma profile on in .env turned an OpenAI prompt
    # test red, because the live session built Emma's instructions instead
    # of the receptionist's. Pin the profile and wake knobs to defaults;
    # tests that mean a different value set it themselves.
    from app.config import settings as _settings

    _settings.assistant_profile = "condo"
    _settings.wake_enabled = False
    _settings.wake_threshold = 0.25
    _settings.wake_boost = 2.0
    display._reveal_task = None
    canva_display._task = None
    display._audio_lead_ms = 0.0
    display._audio_until = 0.0
    yield
    # Drop, don't cancel: by now the loop these belong to is already closed,
    # so there is nothing left to cancel them with. Holding the reference is
    # what would carry a dead task into the next loop.
    display._reveal_task = None
    canva_display._task = None
    session_module._active = None


@pytest.fixture(autouse=True)
def _no_real_browser(monkeypatch, request):
    """Running the tests must never open a browser window.

    It did. On 2026-08-11 `pytest` on the developer's Windows machine put a
    **fullscreen kiosk Chrome** on screen showing
    `DNS_PROBE_FINISHED_NXDOMAIN` for `canva.test` — the fake URL from
    `test_canva_display.py`'s own fixture. `canva_kiosk` is true by default, so
    it had no address bar and no close button.

    The cause is worth writing down, because it is the mirror image of a
    mistake this file already knows about.

    `test_ensure_page_actually_drops_the_dead_handle` deliberately drives the
    real `_ensure_page`. Its fake shutdown sets `_page = None`, execution falls
    through to the launch, and on a machine where `playwright` is genuinely
    installed that launch is genuinely a browser. On CI, where it isn't, the
    import fails and `_ensure_page` returns None — so the test passed for a
    reason that had nothing to do with what it was testing, and the damage only
    ever appeared on the one machine nobody runs CI on.

    That is the same shape as `_pretend_playwright_is_installed` in
    `test_canva_display.py`, only inverted: there a missing package made a test
    vacuous, here it made a test *harmless*.

    So the guard goes here rather than in one test. Anything that reaches an
    actual launch fails loudly instead of taking over the screen, and a test
    that means to exercise launching patches `_launch_browser` itself — its own
    monkeypatch runs after this one and wins.

    Two tests drive `_launch_browser` on purpose, with a fake playwright
    underneath. They opt out with `@pytest.mark.allow_browser_launch`, which
    has to be written deliberately and is greppable.
    """
    if request.node.get_closest_marker("allow_browser_launch"):
        return

    from app.tools import canva_display

    async def refuse(_args):
        raise AssertionError(
            "a test tried to launch a real browser. Patch `_launch_browser` in "
            "the test if that is intended — see tests/conftest.py"
        )

    monkeypatch.setattr(canva_display, "_launch_browser", refuse)

@pytest.fixture(autouse=True)
def _forget_what_was_heard():
    """"What the guest last said" is module state, and it now decides whether
    a tool acts. A test that leaves "ปิดสไลด์" behind arms the next one."""
    from app import heard
    heard.forget()
    yield
    heard.forget()
