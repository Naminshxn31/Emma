"""
The live Canva window.

Canva refuses to load in an iframe, so the real presentation can never be
embedded in this project's own pages. It can be opened as an ordinary browser
tab and driven, which is what `app/tools/canva_display.py` does.

The thing worth testing here isn't Playwright — it's that a browser window can
never make the robot wait. These run without Playwright installed.
"""
from __future__ import annotations

import asyncio

import pytest

from app.config import settings
from app.tools import canva_display


def run(coro):
    """No pytest-asyncio in this project; the other suites do the same."""
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(canva_display, "_wanted", None)
    monkeypatch.setattr(canva_display, "_task", None)
    monkeypatch.setattr(canva_display, "_page", None)
    # "we just moved it ourselves" is module state, and a test that moved the
    # deck leaves the next one inside the settle window with no idea why its
    # poll returns nothing.
    monkeypatch.setattr(canva_display, "_ours_at", 0.0)
    monkeypatch.setattr(canva_display, "_seen_page", None)
    monkeypatch.setattr(settings, "canva_url", "https://canva.test/design/x/view")
    monkeypatch.setattr(settings, "canva_deck_prefix", "ew")


class FakePage:
    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.visited: list[str] = []

    async def goto(self, url: str, **kwargs) -> None:
        if self.delay:
            await asyncio.sleep(self.delay)
        self.visited.append(url.rsplit("#", 1)[-1])


def _pretend_playwright_is_installed(monkeypatch):
    """Let `_ensure_page` get as far as `_launch_browser`.

    Without this, every "does it launch?" test is vacuous in CI: playwright
    isn't installed here, `_ensure_page` returns None at the import, and a
    test asserting no launch happened passes for a reason that has nothing to
    do with the code under test. Reverting the fix left both blanking tests
    green, which is how this was noticed.
    """
    import sys
    import types

    fake_async = types.ModuleType("playwright.async_api")

    class _Starter:
        async def start(self):
            return object()

    fake_async.async_playwright = lambda: _Starter()
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_async)


def _launcher(page, delay=0.0):
    # **_kw: `_ensure_page` takes `launch=` and `why=` now. A stub that
    # ignores them is right here — these tests are about navigation, and the
    # launch decision has its own tests below.
    async def _ensure(**_kw):
        if delay:
            await asyncio.sleep(delay)
        return page
    return _ensure


# ==================== the reason this file exists ====================


def test_goto_returns_before_the_window_is_ready(monkeypatch):
    """The bug this was written for.

    `display.show()` awaited this, and `display.show()` runs inside a tool
    call. Gemini 3.x function calling is synchronous — the model produces
    nothing until the tool returns — so the first `show_slide` gagged the
    robot for as long as Chromium took to start. Measured at four seconds,
    with a guest standing there having just asked to see something.
    """
    page = FakePage()
    monkeypatch.setattr(canva_display, "_ensure_page", _launcher(page, delay=1.0))

    async def body():
        started = asyncio.get_event_loop().time()
        await canva_display.goto("ew-042")
        elapsed = asyncio.get_event_loop().time() - started

        assert elapsed < 0.1, "goto() waited %.2fs for the browser" % elapsed
        assert page.visited == [], "and it hasn't navigated yet, which is fine"

        await asyncio.sleep(1.3)
        assert page.visited == ["42"], "the window catches up on its own"

    run(body())


def test_only_the_newest_slide_is_navigated_to(monkeypatch):
    """During a narrated tour the model can advance faster than Chromium can
    load. Replaying every intermediate page would leave the window visibly
    chasing the conversation; only where it ended up matters."""
    page = FakePage(delay=0.05)
    monkeypatch.setattr(canva_display, "_ensure_page", _launcher(page, delay=0.2))

    async def body():
        for n in (10, 11, 12, 13):
            await canva_display.goto("ew-%03d" % n)
        await asyncio.sleep(0.6)
        assert page.visited == ["13"], "expected only page 13, got %r" % page.visited

    run(body())


def test_a_broken_window_never_raises_into_the_tool(monkeypatch):
    class Broken(FakePage):
        async def goto(self, url, **kwargs):
            raise RuntimeError("chromium died")

    monkeypatch.setattr(canva_display, "_ensure_page", _launcher(Broken()))

    async def body():
        await canva_display.goto("ew-001")
        await asyncio.sleep(0.1)  # the failure happens out here, and is swallowed

    run(body())


# ==================== staying out of the way ====================


def test_nothing_happens_without_a_url(monkeypatch):
    """Opt-in. A machine with no Playwright installed must be unaffected."""
    monkeypatch.setattr(settings, "canva_url", "")

    def explode(**_kw):
        raise AssertionError("must not try to launch a browser")

    monkeypatch.setattr(canva_display, "_ensure_page", explode)
    run(canva_display.goto("ew-001"))


@pytest.mark.parametrize("slide_id", ["picture-25", "plan-02", "other-005", None, ""])
def test_slides_outside_the_deck_are_ignored(monkeypatch, slide_id):
    """Only the imported Canva deck has Canva pages. The 85 slides inherited
    from emma don't, and hiding the slide passes None."""
    def explode(**_kw):
        raise AssertionError("must not launch for %r" % slide_id)

    monkeypatch.setattr(canva_display, "_ensure_page", explode)
    run(canva_display.goto(slide_id))


def test_the_page_number_comes_from_the_slide_id():
    assert canva_display._page_number("ew-042") == 42
    assert canva_display._page_number("ew-001") == 1
    assert canva_display._page_number("picture-42") is None


def test_a_page_baked_into_the_link_is_ignored(monkeypatch):
    """Canva's Share link carries whatever page was open when it was copied.
    The one in .env ended `#54`, and the window opened on slide 54 of 59.
    `goto()` always stripped the fragment; the initial load did not."""
    monkeypatch.setattr(
        settings, "canva_url",
        "https://www.canva.com/design/X/Y/view?utm_content=X&utlId=h5acb8c831d#54",
    )
    base = canva_display._base_url()
    assert base.endswith("utlId=h5acb8c831d"), base
    assert "#" not in base
    assert "utm_content=X" in base, "the query string is part of the link, keep it"


def test_the_window_opens_on_page_one(monkeypatch):
    """Deterministic starting point, whatever link was pasted."""
    opened: list[str] = []

    class Page:
        async def goto(self, url, **kwargs):
            opened.append(url)

    class Browser:
        async def new_page(self, **kwargs):
            return Page()

    class PW:
        chromium = type("C", (), {"launch": staticmethod(
            lambda **kw: _coro(Browser()))})()

        async def stop(self):
            pass

    def _coro(value):
        async def _c():
            return value
        return _c()

    async def fake_start():
        return PW()

    monkeypatch.setattr(
        settings, "canva_url", "https://www.canva.com/design/X/Y/view#54")
    monkeypatch.setitem(
        __import__("sys").modules, "playwright.async_api",
        type("M", (), {"async_playwright": lambda: type(
            "S", (), {"start": staticmethod(fake_start)})()}),
    )

    async def body():
        page = await canva_display._ensure_page()
        assert page is not None
        assert opened == ["https://www.canva.com/design/X/Y/view#1"], opened

    run(body())


# ==================== keeping up with the tour ====================


class KeyPage(FakePage):
    def __init__(self, delay: float = 0.0) -> None:
        super().__init__(delay)
        self.keys: list[str] = []
        self.keyboard = self

    async def press(self, key: str) -> None:
        self.keys.append(key)


def test_one_slide_forward_is_a_keypress_not_a_reload(monkeypatch):
    """`page.goto(...#N)` re-navigates, and Canva answers by reloading its
    whole viewer — seconds of black screen and the slide image fetched
    again. During a tour that left the mirror a full slide behind the
    narration, which is the one thing it exists not to do. The viewer moves
    on arrow keys without reloading, and a tour steps one page at a time."""
    page = KeyPage()
    monkeypatch.setattr(canva_display, "_ensure_page", _launcher(page))
    monkeypatch.setattr(canva_display, "_at_page", 3)

    async def body():
        await canva_display.goto("ew-004")
        await asyncio.sleep(0.3)
        assert page.keys == ["ArrowRight"]
        assert page.visited == [], "must not have re-navigated"

    run(body())


def test_going_back_presses_the_other_arrow(monkeypatch):
    page = KeyPage()
    monkeypatch.setattr(canva_display, "_ensure_page", _launcher(page))
    monkeypatch.setattr(canva_display, "_at_page", 8)

    async def body():
        await canva_display.goto("ew-006")
        await asyncio.sleep(0.6)
        assert page.keys == ["ArrowLeft", "ArrowLeft"]

    run(body())


def test_a_long_jump_reloads_instead_of_arrowing(monkeypatch):
    """A guest asking to see something far away shouldn't cost forty
    keypresses."""
    page = KeyPage()
    monkeypatch.setattr(canva_display, "_ensure_page", _launcher(page))
    monkeypatch.setattr(canva_display, "_at_page", 2)

    async def body():
        await canva_display.goto("ew-045")
        await asyncio.sleep(0.4)
        assert page.keys == []
        assert page.visited == ["45"]

    run(body())


class TalkingPage(KeyPage):
    """A window that reports its own page, the way Canva's viewer does by
    rewriting location.hash."""

    def __init__(self, page: int = 1, moves: bool = True) -> None:
        super().__init__()
        self.page = page
        self.moves = moves

    async def press(self, key):
        await super().press(key)
        if self.moves:
            self.page += 1 if key == "ArrowRight" else -1

    async def goto(self, url, **kwargs):
        await super().goto(url, **kwargs)
        tail = url.rsplit("#", 1)[-1]
        if tail.isdigit():
            self.page = int(tail)

    async def evaluate(self, js):
        return "#%d" % self.page


def test_a_swallowed_keypress_is_caught_and_corrected(monkeypatch):
    """Open-loop, one missed keypress puts every slide after it out by one,
    silently and for the rest of the presentation. Reading the position back
    turns that into a correction."""
    page = TalkingPage(page=3, moves=False)      # arrows do nothing
    monkeypatch.setattr(canva_display, "_ensure_page", _launcher(page))
    monkeypatch.setattr(canva_display, "_at_page", 3)

    async def body():
        await canva_display.goto("ew-004")
        await asyncio.sleep(0.4)
        assert page.visited == ["4"], "should have deep-linked after the key failed"
        assert canva_display._at_page == 4

    run(body())


def test_someone_clicking_canva_by_hand_is_noticed(monkeypatch):
    """A person can drive the window too. Our idea of the position has to
    give way to what it actually reports."""
    page = TalkingPage(page=30)
    monkeypatch.setattr(canva_display, "_ensure_page", _launcher(page))
    monkeypatch.setattr(canva_display, "_at_page", 3)   # stale

    async def body():
        await canva_display.goto("ew-031")
        await asyncio.sleep(0.4)
        # 30 -> 31 is one step, not the 28 our stale number implied.
        assert page.keys == ["ArrowRight"]
        assert canva_display._at_page == 31

    run(body())


def test_a_silent_window_still_works(monkeypatch):
    """If Canva stops reporting its page, the sync must degrade to the old
    open-loop behaviour rather than stop."""
    page = KeyPage()          # no evaluate() -> read_page returns None
    monkeypatch.setattr(canva_display, "_ensure_page", _launcher(page))
    monkeypatch.setattr(canva_display, "_at_page", 5)

    async def body():
        await canva_display.goto("ew-006")
        await asyncio.sleep(0.4)
        assert page.keys == ["ArrowRight"]

    run(body())


def test_a_failed_move_forgets_where_it_was(monkeypatch):
    """Stepping from a position that turned out to be wrong walks the deck
    further out of sync every time. Once a move fails, reload."""
    class Broken(KeyPage):
        async def press(self, key):
            raise RuntimeError("window gone")

    monkeypatch.setattr(canva_display, "_ensure_page", _launcher(Broken()))
    monkeypatch.setattr(canva_display, "_at_page", 3)

    async def body():
        await canva_display.goto("ew-004")
        await asyncio.sleep(0.3)
        assert canva_display._at_page is None

    run(body())


# ============ the narration waits for the window, not the reverse ============


def test_next_slide_holds_until_canva_is_on_the_page(monkeypatch):
    """The last piece of getting picture and words to start together.

    Function calling is synchronous, so the model says nothing until the
    tool returns — holding here means the narration begins at the moment
    the guest sees the slide, instead of over the top of the previous one.
    """
    from app.tools import load_tools, registry

    load_tools()
    monkeypatch.setattr(settings, "slide_pause_s", 0.0)

    page = TalkingPage(page=1)
    monkeypatch.setattr(canva_display, "_ensure_page", _launcher(page))
    monkeypatch.setattr(canva_display, "_at_page", 1)

    async def body():
        await registry.dispatch("start_presentation", {"tour": "deck"})
        out = await registry.dispatch("next_slide", {})
        assert out["slide"]["id"] == "ew-002"
        assert page.page == 2, "canva should already be there when this returns"

    run(body())


def test_the_tool_call_is_never_held_longer_than_its_budget(monkeypatch):
    """Holding `next_slide` open is what keeps the picture and the words
    together — and it parks the model mid-turn for the length of a
    paragraph. A tour that narrates a few slides and then goes silent points
    straight at this, so the wait is capped rather than open-ended.

    An early slide is a much smaller problem than a tour that stops.
    """
    from app import display
    from app.tools import load_tools, registry

    load_tools()
    monkeypatch.setattr(settings, "canva_url", "")
    monkeypatch.setattr(settings, "slide_pause_s", 0.0)
    monkeypatch.setattr(settings, "slide_tool_budget_s", 0.5)

    async def body():
        await registry.dispatch("start_presentation", {"tour": "deck"})
        display.set_audio_lead(30_000)          # half a minute still queued
        started = asyncio.get_event_loop().time()
        out = await registry.dispatch("next_slide", {})
        held = asyncio.get_event_loop().time() - started
        assert out["ok"] is True
        assert held < 2.0, "held the model for %.1fs" % held

    run(body())


def test_starting_a_tour_also_waits_for_canva(monkeypatch):
    """Slide one had the same problem as the rest and was missed: the robot
    began the tour talking over a window that was still loading."""
    from app.tools import load_tools, registry

    load_tools()
    page = TalkingPage(page=0)
    monkeypatch.setattr(canva_display, "_ensure_page", _launcher(page))
    monkeypatch.setattr(canva_display, "_at_page", None)

    async def body():
        out = await registry.dispatch("start_presentation", {"tour": "deck"})
        assert out["slide"]["id"] == "ew-001"
        assert page.page == 1, "canva should be on the cover before it speaks"

    run(body())


def test_the_cover_is_up_before_a_cold_started_tour_speaks(monkeypatch):
    """The live regression, reproduced with a window that is actually slow.

    The first slide is the cold-start case: Chromium is still launching, and
    the model narrates the instant `start_presentation` returns. So the call
    must not return until the cover is up. The non-blocking version returned
    in microseconds with the page still at 0 — which is exactly the "พูดก่อน
    Canva เปิด" seen on screen. A launch that takes real time is what catches
    it; the instant-fake test above passed straight through the bug.
    """
    from app import display
    from app.tools import load_tools, registry

    load_tools()
    display.set_audio_lead(0)
    page = TalkingPage(page=0)
    monkeypatch.setattr(canva_display, "_ensure_page", _launcher(page, delay=0.4))
    monkeypatch.setattr(canva_display, "_at_page", None)

    async def body():
        started = asyncio.get_event_loop().time()
        out = await registry.dispatch("start_presentation", {"tour": "deck"})
        elapsed = asyncio.get_event_loop().time() - started
        assert out["slide"]["id"] == "ew-001"
        assert page.page == 1, "the cover must be up before the call returns"
        assert elapsed >= 0.4, (
            "returned in %.2fs — it did not wait for the slow window" % elapsed
        )

    run(body())


def test_a_stuck_window_does_not_hold_the_conversation(monkeypatch):
    """A mirror is never worth stalling a guest for. If Canva won't move,
    the robot talks anyway."""
    class Stuck(TalkingPage):
        async def press(self, key):
            await asyncio.sleep(5)

        async def goto(self, url, **kwargs):
            await asyncio.sleep(5)

    monkeypatch.setattr(canva_display, "_ensure_page", _launcher(Stuck(page=1)))
    monkeypatch.setattr(canva_display, "_at_page", 1)

    async def body():
        started = asyncio.get_event_loop().time()
        arrived = await canva_display.arrive("ew-002", timeout=0.4)
        assert arrived is False
        assert asyncio.get_event_loop().time() - started < 1.5

    run(body())


def test_no_canva_configured_means_no_wait(monkeypatch):
    monkeypatch.setattr(settings, "canva_url", "")

    async def body():
        assert await canva_display.arrive("ew-002") is False

    run(body())


# ============ following a person who drives the deck themselves ============


def test_a_page_we_did_not_set_is_reported_once(monkeypatch):
    """A salesperson at the screen will click the deck too. Until now the
    robot had no idea and kept narrating a slide nobody was looking at."""
    page = TalkingPage(page=12)
    monkeypatch.setattr(canva_display, "_page", page)
    monkeypatch.setattr(canva_display, "_at_page", 3)
    monkeypatch.setattr(canva_display, "_seen_page", None)

    async def body():
        # First sighting only arms it — the page has to hold still.
        assert await canva_display.poll_external() is None
        assert await canva_external() == 12
        # And it is reported once, not on every poll after.
        assert await canva_display.poll_external() is None

    async def canva_external():
        return await canva_display.poll_external()

    run(body())


def test_flicking_through_narrates_only_where_they_stop(monkeypatch):
    """Someone hunting for a slide passes over several. Reporting each one
    would have the robot narrate five pages nobody looked at."""
    page = TalkingPage(page=3)
    monkeypatch.setattr(canva_display, "_page", page)
    monkeypatch.setattr(canva_display, "_at_page", 3)
    monkeypatch.setattr(canva_display, "_seen_page", None)

    async def body():
        reported = []
        for stop in (7, 8, 9, 20, 20):
            page.page = stop
            got = await canva_display.poll_external()
            if got is not None:
                reported.append(got)
        assert reported == [20], "got %r" % reported

    run(body())


def test_our_own_move_is_not_mistaken_for_a_person(monkeypatch):
    page = TalkingPage(page=6)
    monkeypatch.setattr(canva_display, "_page", page)
    monkeypatch.setattr(canva_display, "_at_page", 6)

    async def body():
        assert await canva_display.poll_external() is None

    run(body())


def test_nothing_is_polled_while_a_move_is_in_flight(monkeypatch):
    """Mid-navigation the page is briefly whatever it was — reading it then
    would look like a person had moved it backwards."""
    page = TalkingPage(page=99)
    monkeypatch.setattr(canva_display, "_page", page)
    monkeypatch.setattr(canva_display, "_at_page", 3)

    async def body():
        async with canva_display._lock:
            assert await canva_display.poll_external() is None

    run(body())


def test_our_own_move_is_not_read_back_as_a_person(monkeypatch):
    """The bug that made a finished tour start over from slide one.

    Canva animates between pages and updates its fragment on its own
    schedule, so a poll landing just after one of our moves reads a page
    that isn't where we asked for — and the watcher took that for somebody
    driving, dragged the tour there, and narrated it again. Readings taken
    while our own move is still settling are not evidence of anything.
    """
    import time

    page = TalkingPage(page=4)          # mid-animation, still on the old page
    monkeypatch.setattr(canva_display, "_page", page)
    monkeypatch.setattr(canva_display, "_at_page", 5)
    monkeypatch.setattr(canva_display, "_seen_page", None)
    monkeypatch.setattr(canva_display, "_ours_at", time.monotonic())

    async def body():
        assert await canva_display.poll_external() is None
        assert await canva_display.poll_external() is None

        # Once it has settled, a genuine change is followed as before.
        monkeypatch.setattr(canva_display, "_ours_at", 0.0)
        assert await canva_display.poll_external() is None    # first sighting
        assert await canva_display.poll_external() == 4

    run(body())


def test_the_page_the_tour_is_already_on_is_not_re_narrated():
    """A stale reading of the current page is how the deck ends up playing
    itself twice — the transcript that ran 1..59 and then opened again with
    "ยินดีต้อนรับสู่ Embassy World"."""
    from app.tools import load_tools, slides

    load_tools()
    slides.reload_slides()
    assert slides.follow_external_page(5) is not None
    assert slides.follow_external_page(5) is None, "same page, nothing to say"
    assert slides.follow_external_page(9) is not None, "a real move still works"


def test_a_page_outside_the_deck_is_ignored():
    """A stray click on some other design must not drag the conversation."""
    from app.tools import load_tools, slides

    load_tools()
    slides.reload_slides()
    assert slides.follow_external_page(9999) is None


def test_following_a_page_hands_over_that_page_s_script():
    from app.tools import load_tools, slides

    load_tools()
    slides.reload_slides()
    order = slides.follow_external_page(5)
    assert order is not None
    assert order["slide"]["position"] == 5
    assert slides.STATE["index"] == 4, "the tour moves with the screen"
    assert order["slide"]["script"] in order["text"]
    assert "ห้ามแต่งใหม่" in order["text"]


def test_startup_does_not_put_a_window_on_the_screen(monkeypatch):
    """Checking by *opening* the window meant a fullscreen browser sat over
    the desktop from the moment the server started. The check still has to
    prove Chromium works — it just does it headlessly and closes it."""
    monkeypatch.setattr(settings, "canva_open_at_start", False)

    def explode(**_kw):
        raise AssertionError("startup must not open the visible window")

    monkeypatch.setattr(canva_display, "_ensure_page", explode)

    launched: list[bool] = []

    class Browser:
        async def close(self):
            launched.append(True)

    class Chromium:
        @staticmethod
        async def launch(**kwargs):
            assert kwargs.get("headless") is True, "must not be visible"
            return Browser()

    class PW:
        chromium = Chromium()

        async def stop(self):
            pass

    async def start():
        return PW()

    monkeypatch.setitem(
        __import__("sys").modules, "playwright.async_api",
        type("M", (), {"async_playwright": lambda: type(
            "S", (), {"start": staticmethod(start)})()}),
    )

    async def body():
        ok, detail = await canva_display.self_check()
        assert ok is True, detail
        assert launched == [True], "should have proved chromium runs"
        assert "presentation" in detail

    run(body())


def test_shutdown_cancels_work_in_flight(monkeypatch):
    """Server stopping with a navigation half-done shouldn't hang the exit."""
    page = FakePage(delay=5.0)
    monkeypatch.setattr(canva_display, "_ensure_page", _launcher(page, delay=5.0))

    async def body():
        await canva_display.goto("ew-007")
        assert canva_display._task is not None
        await asyncio.wait_for(canva_display.shutdown(), timeout=2.0)
        assert canva_display._task is None

    run(body())


# ==================== the picture waits for the ear ====================


def test_screens_wait_for_the_queued_audio(monkeypatch):
    """The model finishes producing a narration in about two seconds but it
    takes nine to say, and the browser queues all of it. `next_slide` fires
    at production time, so every screen jumped five to ten seconds ahead of
    what the guest was hearing — the robot describing the airports with the
    map of Pattaya already up."""
    from app import display

    display.set_audio_lead(0)
    monkeypatch.setattr(display, "_reveal_seq", 0)
    shown: list = []

    async def fake_reveal(slide):
        shown.append(slide)

    monkeypatch.setattr(display, "_reveal", fake_reveal)

    async def body():
        display.set_audio_lead(600)
        await display.show({"id": "ew-005"})
        assert shown == [], "must not appear while the guest is still six seconds back"
        await asyncio.sleep(0.75)
        assert shown == [{"id": "ew-005"}]

    run(body())


def test_no_call_means_no_delay(monkeypatch):
    """Nobody listening — a script or a test driving the tools directly gets
    the slide straight away, as before."""
    from app import display

    display.set_audio_lead(0)
    shown: list = []

    async def fake_reveal(slide):
        shown.append(slide)

    monkeypatch.setattr(display, "_reveal", fake_reveal)

    async def body():
        await display.show({"id": "ew-001"})
        assert shown == [{"id": "ew-001"}]

    run(body())


def test_a_later_slide_supersedes_one_still_waiting(monkeypatch):
    from app import display

    display.set_audio_lead(0)
    monkeypatch.setattr(display, "_reveal_seq", 0)
    shown: list = []

    async def fake_reveal(slide):
        shown.append(slide["id"])

    monkeypatch.setattr(display, "_reveal", fake_reveal)

    async def body():
        display.set_audio_lead(500)
        await display.show({"id": "ew-005"})
        await display.show({"id": "ew-006"})
        await asyncio.sleep(0.7)
        assert shown == ["ew-006"], "got %r" % shown

    run(body())


# ==================== pacing the tour ====================


def test_the_tour_waits_for_the_guest_to_hear_the_slide(monkeypatch):
    """The robot used to ask "shall we go on?" after every page. Told to
    advance by itself, it advances at the speed it can *generate* — through
    all 59 pages in about a minute, with minutes of speech queued behind it
    and no way for anyone to interrupt out. So next_slide waits."""
    from app import display
    from app.tools import load_tools, registry

    load_tools()
    monkeypatch.setattr(settings, "slide_pause_s", 0.0)

    async def body():
        await registry.dispatch("start_presentation", {"tour": "deck"})
        display.set_audio_lead(700)
        started = asyncio.get_event_loop().time()
        out = await registry.dispatch("next_slide", {})
        waited = asyncio.get_event_loop().time() - started
        assert out["ok"] is True
        assert 0.5 < waited < 2.0, "waited %.2fs" % waited

    run(body())


def test_a_barge_in_stops_the_wait_immediately(monkeypatch):
    """Interrupting empties the audio queue. Sitting out the rest of a wait
    for speech that was thrown away would leave the guest talking to a robot
    that has stopped responding."""
    from app import display
    from app.tools import load_tools, registry

    load_tools()
    monkeypatch.setattr(settings, "slide_pause_s", 0.0)

    async def body():
        await registry.dispatch("start_presentation", {"tour": "deck"})
        display.set_audio_lead(10_000)

        async def barge_in():
            await asyncio.sleep(0.3)
            display.set_audio_lead(0)

        asyncio.create_task(barge_in())
        started = asyncio.get_event_loop().time()
        await registry.dispatch("next_slide", {})
        assert asyncio.get_event_loop().time() - started < 1.5

    run(body())


def test_waiting_gives_up_rather_than_hanging(monkeypatch):
    """If a browser reports a queue and then goes away, the tour must not
    stall forever."""
    from app import display

    display.set_audio_lead(30_000)

    async def body():
        waited = await display.wait_until_heard(max_wait=0.4)
        assert waited < 1.0

    run(body())


def test_a_nonsense_lead_cannot_freeze_the_screens(monkeypatch):
    from app import display

    display.set_audio_lead(10 ** 9)
    assert display._audio_lead_ms <= 30_000
    display.set_audio_lead(-5)
    assert display._audio_lead_ms == 0


def test_the_display_feed_does_not_wait_on_the_browser(monkeypatch):
    """The integration point, not the unit. `display.show()` is called from
    inside `registry.dispatch`, so anything it awaits is time the model
    spends unable to speak."""
    from app import display

    monkeypatch.setattr(canva_display, "_ensure_page", _launcher(FakePage(), delay=2.0))

    async def body():
        started = asyncio.get_event_loop().time()
        await display.show({"id": "ew-042", "url": "/slides/x.jpg"})
        return asyncio.get_event_loop().time() - started

    assert run(body()) < 0.1


def test_a_tour_starts_the_window_even_with_the_greeting_still_queued(monkeypatch):
    """The lead guard that stops Canva running ahead must not swallow the
    cold start.

    At `start_presentation` the model has just said "ได้ค่ะ เดี๋ยวพาชมนะคะ"
    and that audio is still in the browser's queue, so the lead is *not*
    zero — a guard applied blindly would skip the one call that exists to
    stop the robot narrating a window that hasn't loaded. There is no page
    to be early relative to yet, so the first slide holds regardless.
    """
    from app import display
    from app.tools import load_tools, registry

    load_tools()
    page = TalkingPage(page=0)
    monkeypatch.setattr(canva_display, "_ensure_page", _launcher(page))
    monkeypatch.setattr(canva_display, "_at_page", None)
    display.set_audio_lead(4000)          # the greeting, still being spoken

    async def body():
        out = await registry.dispatch("start_presentation", {"tour": "deck"})
        assert out["slide"]["id"] == "ew-001"
        assert page.page == 1, (
            "the cover was skipped because the greeting was still playing"
        )

    run(body())
    display.set_audio_lead(0)


def test_a_dead_canva_window_is_replaced_not_reused(monkeypatch):
    """"canva ไม่ขึ้น" with a clean log, and restarting the session doesn't help.

    `_page` is module state that outlives voice sessions. When the window dies
    — the previous session was killed by the go_away abort, or somebody closed
    it by hand — the handle stayed cached and `_ensure_page` returned it
    forever. Every call after that is fire-and-forget with errors swallowed,
    so nothing raised and nothing was logged; the slides simply stopped
    moving, for the life of the process.
    """
    class Window:
        def __init__(self, closed):
            self._closed = closed

        def is_closed(self):
            return self._closed

    monkeypatch.setattr(canva_display, "_browser", None)
    assert canva_display._is_dead(Window(closed=True)) is True
    assert canva_display._is_dead(Window(closed=False)) is False


def test_a_handle_that_cannot_answer_counts_as_dead(monkeypatch):
    """Conservative on purpose. Relaunching a healthy window costs seconds
    once; keeping a dead one costs every slide change for the rest of the day
    and says nothing about it."""
    class Hostile:
        def is_closed(self):
            raise RuntimeError("target closed")

    monkeypatch.setattr(canva_display, "_browser", None)
    assert canva_display._is_dead(Hostile()) is True


def test_a_disconnected_browser_makes_its_page_dead(monkeypatch):
    """The page object can look fine while the browser process behind it is
    gone — which is exactly what a crashed Chromium leaves behind."""
    class Page:
        def is_closed(self):
            return False

    class Browser:
        def is_connected(self):
            return False

    monkeypatch.setattr(canva_display, "_browser", Browser())
    assert canva_display._is_dead(Page()) is True


def test_ensure_page_actually_drops_the_dead_handle(monkeypatch):
    """The three tests above pin `_is_dead` itself; reverting the guard in
    `_ensure_page` left them all green, which means they proved a helper and
    not the behaviour. This one drives the caller: a dead handle has to be
    torn down before anything tries to relaunch, or the new window inherits a
    half-closed driver."""
    import asyncio

    class Window:
        def is_closed(self):
            return True

    dropped = []

    async def fake_shutdown():
        dropped.append(True)
        canva_display._page = None

    monkeypatch.setattr(canva_display, "_page", Window())
    monkeypatch.setattr(canva_display, "_browser", None)
    monkeypatch.setattr(canva_display, "_shutdown_quietly", fake_shutdown)

    asyncio.run(canva_display._ensure_page())
    assert dropped, "kept the dead window and tried to reuse it"


def test_ensure_page_leaves_a_healthy_window_alone(monkeypatch):
    """The other half: a live window must not be torn down and relaunched on
    every slide, which would make each advance cost a browser start."""
    import asyncio

    class Window:
        def is_closed(self):
            return False

    alive = Window()
    dropped = []

    async def fake_shutdown():
        dropped.append(True)

    monkeypatch.setattr(canva_display, "_page", alive)
    monkeypatch.setattr(canva_display, "_browser", None)
    monkeypatch.setattr(canva_display, "_shutdown_quietly", fake_shutdown)

    assert asyncio.run(canva_display._ensure_page()) is alive
    assert dropped == [], "relaunched a window that was working fine"


def test_kiosk_launches_a_persistent_context_not_a_detached_page(monkeypatch):
    """Why the window kept opening with tabs and an address bar despite
    CANVA_KIOSK=true.

    `--kiosk` and `--start-fullscreen` only shape the browser's *own* first
    window. `browser.new_page()` opens a different one through CDP, so the
    flags were accepted and applied to a blank window nobody ever saw, while
    the slides went to an ordinary tabbed window next to it.

    `launch_persistent_context()` returns that first window as `pages[0]` —
    the one the flags actually shaped. This pins that we ask for it, and that
    the kiosk args are what we ask with.
    """
    import asyncio

    from app.config import settings

    seen = {}

    class Ctx:
        def __init__(self, page):
            self.pages = [page]

    class Chromium:
        async def launch_persistent_context(self, **kwargs):
            seen.update(kwargs)
            return Ctx(TalkingPage(page=1))

        async def launch(self, **kwargs):     # must not be reached
            seen["fell_back"] = True
            raise AssertionError("used launch() instead of a persistent context")

    class PW:
        chromium = Chromium()

    monkeypatch.setattr(settings, "canva_kiosk", True)
    monkeypatch.setattr(canva_display, "_playwright", PW())

    args = ["--start-fullscreen", "--kiosk"]
    _ctx, page = asyncio.run(canva_display._launch_browser(args))

    assert page is not None
    assert seen.get("args") == args, "the kiosk flags never reached the browser"
    assert seen.get("user_data_dir"), "a persistent context needs a profile dir"
    assert seen.get("no_viewport") is True, (
        "a fixed viewport would letterbox the slide inside a fullscreen window"
    )


def test_a_persistent_context_is_not_mistaken_for_a_dead_window(monkeypatch):
    """A BrowserContext has no `is_connected` — only a Browser does. Asking for
    it and treating the AttributeError as "dead" would relaunch the window on
    every single slide, forever. Absence of the method is not evidence."""
    class Context:
        pass                      # no is_connected, like the real thing

    class Page:
        def is_closed(self):
            return False

    monkeypatch.setattr(canva_display, "_browser", Context())
    assert canva_display._is_dead(Page()) is False


def test_a_long_jump_moves_inside_the_viewer_before_reloading_it(monkeypatch):
    """"ตอนขอไปดูแต่ละส่วน canva ไปช้า".

    Arrow keys only cover MAX_STEPS. Past that the old path called
    `page.goto(url#n)` — a full navigation, so Canva tears the deck down and
    rebuilds it, several seconds of empty wall. On a tour that never showed,
    because jumps are one page at a time. On "ขอดูฟิตเนส" from slide 59 it is
    the whole experience: the guest asks to see something and the screen goes
    blank first.

    The viewer publishes its page in `location.hash` and is a single-page app,
    so writing the hash back is what its own controls do. No reload.
    """
    import asyncio

    class Viewer:
        def __init__(self):
            self.page = 59
            self.reloaded = []

        async def evaluate(self, script, arg=None):
            if arg is not None:              # the hash write
                self.page = arg
                return None
            return f"#{self.page}"           # read_page's own query

        async def goto(self, url, **kw):
            self.reloaded.append(url)

    viewer = Viewer()
    monkeypatch.setattr(canva_display, "read_page",
                        lambda page: _immediate(page.page))

    moved = asyncio.run(canva_display._jump_to(viewer, 24))
    assert moved is True
    assert viewer.page == 24
    assert viewer.reloaded == [], "reloaded the viewer instead of moving inside it"


def test_a_viewer_that_ignores_the_hash_still_falls_back(monkeypatch):
    """Canva's markup is theirs. If writing the hash does nothing, the jump
    has to report failure so the caller can still deep-link — a slow slide
    beats no slide."""
    import asyncio

    class Stubborn:
        page = 59

        async def evaluate(self, script, arg=None):
            return None                      # accepts the write, ignores it

    monkeypatch.setattr(canva_display, "read_page",
                        lambda page: _immediate(59))

    assert asyncio.run(canva_display._jump_to(Stubborn(), 24)) is False


async def _immediate(value):
    return value


def test_the_kiosk_window_is_the_one_we_drive(monkeypatch):
    """`--app=<url>` gave a fullscreen window that was completely black.

    It makes Chrome open the URL in a window of its own, so the persistent
    context's `pages[0]` is a *different* page — every goto and every arrow
    key went somewhere invisible while the visible window sat on nothing.
    The same "flags on one window, content on another" mistake as
    `launch()` + `new_page()`, committed a second time while fixing the first.

    So: no `--app`. With a persistent context, `--kiosk` already applies to
    the window being driven.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "canva_kiosk", True)
    args = canva_display._window_args()
    assert not any(a.startswith("--app") for a in args), (
        "--app opens a second window; the slides would go to the invisible one"
    )
    assert "--kiosk" in args

    monkeypatch.setattr(settings, "canva_kiosk", False)
    assert canva_display._window_args() == []


def test_blanking_a_closed_window_does_not_reopen_it(monkeypatch):
    """"ตอนสั่งปิดเหมือน canva จะค้างๆ และขึ้นอะไรแปลกๆแทน".

    `close_presentation` shuts the browser down. Then the slide tools' display
    push runs `display.show(None)` -> `goto(None)` -> `_sync`, which called
    `_ensure_page()` unconditionally and relaunched Chromium from scratch —
    purely to render a black `data:text/html,...` URL. The guest asked for the
    screen to go away and watched a new window open showing the data URL.

    Patches `_launch_browser`, not `_ensure_page`. The first fix guarded this
    inside `_sync` with `if target == BLANK and _page is None`, and a test that
    stubbed `_ensure_page` could only ever confirm that one guard — which is
    how the *other* way in survived it. See the dead-handle test below.
    """
    import asyncio

    launched = []

    async def fake_launch(_args):
        launched.append(True)
        return None, TalkingPage(page=1)

    _pretend_playwright_is_installed(monkeypatch)
    monkeypatch.setattr(canva_display, "_page", None)
    monkeypatch.setattr(canva_display, "_playwright", None)
    monkeypatch.setattr(canva_display, "_launch_browser", fake_launch)
    monkeypatch.setattr(canva_display, "_wanted", canva_display.BLANK)

    asyncio.run(canva_display._sync())
    assert launched == [], "relaunched the browser just to show a black page"


def test_blanking_does_not_reopen_a_window_whose_handle_died(monkeypatch):
    """The half the first fix missed, seen live on 2026-08-10.

    The log reads: `close_presentation` at 09:15:09, then `canva_launch` at
    09:15:10 and `canva_fullscreen` at 09:15:13 — a brand new fullscreen
    window, opened one second after the guest asked for the screen to go away,
    showing nothing but black. That is `_ensure_page` doing exactly what it is
    written to do: it found a page handle pointing at a window that no longer
    existed, tore it down, and opened a replacement.

    `if target == BLANK and _page is None` never saw it, because `_page` was
    not None — it was dead, which is a different branch. Reviving a window is
    right for a slide and absurd for a blank, so the decision now travels with
    the caller's intent instead of being inferred from module state.
    """
    import asyncio

    launched = []

    class DeadPage:
        def is_closed(self):
            return True

    async def fake_launch(_args):
        launched.append(True)
        return None, TalkingPage(page=1)

    _pretend_playwright_is_installed(monkeypatch)
    monkeypatch.setattr(canva_display, "_page", DeadPage())
    monkeypatch.setattr(canva_display, "_browser", None)
    monkeypatch.setattr(canva_display, "_playwright", None)
    monkeypatch.setattr(canva_display, "_launch_browser", fake_launch)
    monkeypatch.setattr(canva_display, "_wanted", canva_display.BLANK)

    asyncio.run(canva_display._sync())
    assert launched == [], "opened a new window to blank a window that was gone"
    assert canva_display._page is None, "kept hold of a dead page handle"


def test_blanking_a_live_window_still_works(monkeypatch):
    """The other half: with a window open, `hide_slide` must still black it
    out rather than leaving the last slide on the wall."""
    import asyncio

    page = TalkingPage(page=4)
    monkeypatch.setattr(canva_display, "_page", page)
    monkeypatch.setattr(canva_display, "_ensure_page", _launcher(page))
    monkeypatch.setattr(canva_display, "_wanted", canva_display.BLANK)

    asyncio.run(canva_display._sync())
    assert any("data:text/html" in u for u in page.visited), (
        "the window was left showing the last slide"
    )


def test_a_fullscreen_failure_does_not_cost_us_the_window(monkeypatch):
    """Fullscreen is cosmetics; the window is the product.

    `_go_fullscreen` used to sit inside the same try/except as the launch, so
    a stray Playwright error in it was caught by a handler that sets
    `_page = None` — and a null page is exactly how "canva ไม่ขึ้น" happens.
    A deck with a header around it is a complaint. No deck at all is a dead
    demo in front of a customer.
    """
    import asyncio

    from app.config import settings

    page = TalkingPage(page=1)

    async def explode(_page):
        raise RuntimeError("canva changed its markup again")

    async def fake_launch(args):
        return None, page

    # playwright isn't installed in the test environment, and `_ensure_page`
    # imports it before anything else. Stand in for it so the rest of the
    # function actually runs.
    import sys
    import types

    fake_pw = types.ModuleType("playwright")
    fake_async = types.ModuleType("playwright.async_api")

    class _Starter:
        async def start(self):
            return object()

    fake_async.async_playwright = lambda: _Starter()
    monkeypatch.setitem(sys.modules, "playwright", fake_pw)
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_async)

    monkeypatch.setattr(settings, "canva_kiosk", True)
    monkeypatch.setattr(canva_display, "_page", None)
    monkeypatch.setattr(canva_display, "_playwright", None)
    monkeypatch.setattr(canva_display, "_launch_browser", fake_launch)
    monkeypatch.setattr(canva_display, "_go_fullscreen", explode)

    got = asyncio.run(canva_display._ensure_page())
    assert got is page, "the window was thrown away because fullscreen failed"
