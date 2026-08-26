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
    _settings.ws_token = ""          # the LAN gate; tests opt in explicitly
    _settings.wake_enabled = False
    # WAKE_ENROLL=true in the machine's .env (an enrollment session) would
    # make every wake test save clips into data/wake_enroll — the machine-
    # state trap with a recorder attached. Tests that mean it set it.
    _settings.wake_enroll = False
    # And WAKE_DEBUG=true in the same .env made an enroll test pass for the
    # wrong reason before this pin existed: the capture tick only ran
    # because debug happened to be on here. The suite must not inherit it.
    _settings.wake_debug = False
    # The shipped defaults, re-measured 2026-08-25 (see app/wake.py): the
    # detection fixtures assert against these exact values.
    _settings.wake_threshold = 0.10
    _settings.wake_boost = 3.0
    # Two more knobs whose *default* is right for the gallery and wrong for a
    # 20-second test timeout. Both were the documented trap — "green on a
    # machine that is missing something" — reaching its payday: they only
    # ever hung on a machine that had everything.
    #
    # embed_provider: with EMBED_PROVIDER=local in .env, one
    # `search_condo_info` call imports sentence-transformers and encodes the
    # whole deck. CI has no such package, so `choose_provider()` returned
    # "off" there and the tests were quick and green;
    # `test_answers_spoken_questions_not_just_keywords` hung here instead.
    # Pinning "off" makes every machine run the search CI actually verified.
    # Tests that mean to exercise semantic search patch `choose_provider`.
    #
    # canva_warm_deck: `_ensure_page()` finishes by walking the whole deck
    # once (WARM_STEP_S 0.55s per page, ~33s for 59 pages) so a later jump
    # lands on a drawn page instead of a blank. Fine at 08:00 in a showroom,
    # fatal inside a test that drives `_ensure_page` for real — which
    # `test_a_fullscreen_failure_does_not_cost_us_the_window` does.
    _settings.embed_provider = "off"
    _settings.canva_warm_deck = False
    # And the biggest one of the family, found the hard way: the owner added
    # TOOL_GROUPS to .env to switch on the unit card, and nineteen robot and
    # slide tests went red on a machine where nothing had been edited. The
    # tools those tests exercise simply stopped being registered.
    #
    # Blank = "every group", which is the gallery's own default and the
    # configuration the tests were written against. Tests that mean a subset
    # set it themselves.
    _settings.tool_groups = ""
    # VAD_MODE=local on the owner's machine would make every provider test
    # construct a real Silero detector (and change what send_audio emits).
    # Pin to the mode the tests were written against; VAD tests set "local"
    # themselves with a scripted detector.
    _settings.vad_mode = "gemini"
    # The live inventory link would send every unit test's show_unit call to
    # the real Supabase — network in unit tests, and results that change
    # when the sales team sells a room. Same family as every pin above.
    _settings.inventory_url = ""
    _settings.inventory_key = ""
    _settings.units_show_price = False
    _settings.searxng_url = ""       # เครื่องที่มี SearXNG ต้องไม่เปลี่ยนสี suite   # นโยบายราคา: default ปิด
    # MULTI_SESSION on the office test server would un-supersede every
    # takeover test and strip the tool groups the robot/slide tests need —
    # the TOOL_GROUPS trap again, one knob over. Tests that mean the shared
    # mode set it themselves.
    _settings.multi_session = False
    # The web stage turns `open_in_browser` and `play_youtube` into "put it
    # on the screen" instead of `os.startfile`. A test that leaves it on
    # rewires four other tests that never mentioned it — and because
    # monkeypatch restores whatever it found at setup, one leak is enough to
    # make the failure depend on test order. Pinned here, like the profile
    # and the LAN gate, for the same reason: the suite must not change
    # colour because of a value some other test set.
    _settings.web_stage = False
    display._reveal_task = None
    canva_display._task = None
    display._audio_lead_ms = 0.0
    display._audio_until = 0.0
    # Subtitle state is module-level too, and its queue holds a *task* plus
    # monotonic deadlines — both of the leak shapes this fixture exists for.
    display.reset_subtitle()

    # Reminder state is module-level too: a watcher task belongs to a dead
    # loop the moment its test ends, and a cached item list from one test's
    # tmp_path poisons the next. Only touched if something imported it —
    # importing here would register its tools into every test's registry.
    import sys as _sys

    _rem = _sys.modules.get("app.tools.reminders")
    if _rem is not None:
        _rem.reset()
    _mem = _sys.modules.get("app.memory_store")
    if _mem is not None:
        _mem.reset()
    _docs = _sys.modules.get("app.tools.mydocs")
    if _docs is not None:
        _docs.reset()
    # The embed_provider="off" pin above closes the front door, and a
    # back door stayed open: a SemanticIndex loaded from this machine's
    # embeddings.npz embeds queries with the provider recorded in the cache
    # (deliberately — vectors from two spaces must never be compared), so
    # `similarities()` reaches `_local_encoder()` regardless of the setting.
    # On a machine with sentence-transformers installed that import can take
    # longer than the 20s test timeout, and with random test ordering it
    # only sometimes does — the worst kind of red. Stub the encoder to fail
    # fast; slide_search already catches that and finishes lexical-only,
    # which is the configuration the tests were written against. Tests that
    # mean to exercise the encoder patch it themselves (test_retrieval).
    _ret = _sys.modules.get("app.tools.retrieval")
    if _ret is not None:
        _ret._local_encoder = lambda: None
    _slide_search = _sys.modules.get("app.tools.slide_search")
    if _slide_search is not None:
        # One failed embed latches semantic off "for the rest of this run" —
        # right in production, but a latch that survives into the next test
        # makes results depend on ordering. Fresh per test.
        if getattr(_slide_search, "_index", None) is not None:
            _slide_search._index._embedding_failed = False

    _calc = _sys.modules.get("app.tools.calc")
    if _calc is not None:
        _calc.reset()
    _wake = _sys.modules.get("app.wake")
    if _wake is not None:
        _wake._STANDBY.clear()
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

    from app.tools import canva_display, webstage

    async def refuse(_args):
        raise AssertionError(
            "a test tried to launch a real browser. Patch `_launch_browser` in "
            "the test if that is intended — see tests/conftest.py"
        )

    # Every module that can open a window, by name. The guard is per-module
    # on purpose (a shared launcher would be one patch point, but then the
    # module the guard did not name is the one that gets away — the exact
    # shape of the bug this fixture exists for). A test below asserts the
    # list is complete, so a third window cannot be added quietly.
    for module in (canva_display, webstage):
        monkeypatch.setattr(module, "_launch_browser", refuse)
    monkeypatch.setattr(webstage, "_page", None)
    monkeypatch.setattr(webstage, "_task", None)
    monkeypatch.setattr(webstage, "_wanted", None)

@pytest.fixture(autouse=True)
def _forget_what_was_heard():
    """"What the guest last said" is module state, and it now decides whether
    a tool acts. A test that leaves "ปิดสไลด์" behind arms the next one."""
    from app import heard
    heard.forget()
    yield
    heard.forget()
