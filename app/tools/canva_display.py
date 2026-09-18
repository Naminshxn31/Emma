"""Drives a real Chromium window showing the live Canva design.

Canva's "view" links set `X-Frame-Options`/CSP so they refuse to load inside
an `<iframe>` on any other page — confirmed by hand, not assumed: embedding
`.../view?embed` inside a test page renders nothing, no console error either,
the frame is just empty. So a webpage in this project can never *contain* the
real presentation.

What Canva does not block is being opened directly as a normal, top-level
browser tab and driven by automation — that's not embedding, it's the same
thing a person clicking through it would do. This module launches exactly
one such window with Playwright and keeps it parked on whatever slide
`app/tools/slides.py` says is currently on screen, via `app/display.py`.

Deep-linking works by URL fragment: `.../view#42` opens the deck straight to
page 42, no need to click through 1..41 first (confirmed by hand too). So
syncing is just `page.goto(f"{CANVA_URL}#{n}")` — cheap enough to call on
every slide change.

Entirely opt-in. `CANVA_URL` unset (the default) means `goto()` is a no-op
and `playwright` is never imported, so a machine that hasn't `pip install
playwright && playwright install chromium` still runs everything else fine.
"""
from __future__ import annotations

import asyncio
import logging
import time
import re

from app.config import settings

logger = logging.getLogger("condo_voice.canva_display")

_playwright = None
_browser = None
_page = None

#: What the most recent launch was for, carried into the turn log.
_launch_reason: str = "slide"
_lock = asyncio.Lock()
_warned_missing_playwright = False

#: The page we most recently *wanted* to be on, and the background task
#: working towards it. `goto()` returns the instant it records the target;
#: everything slow happens out here.
_wanted: int | None = None
_task: asyncio.Task | None = None


def _base_url() -> str:
    """The deck URL without any page fragment.

    People copy the link out of Canva while looking at a page, so it arrives
    with that page baked in — the URL in `.env` ends `#54`, and the window
    opened on slide 54 of 59. `goto()` always stripped it; the initial load
    did not, so the mirror started on whatever page happened to be open when
    somebody copied the link.
    """
    return settings.canva_url.split("#", 1)[0]


#: Measured `slide id -> Canva page`, loaded from the active project's slide directory.
#: `None` means "never measured", which is the only reason the arithmetic
#: below is still reachable. See `_page_number`.
_PAGE_MAP: dict[str, int] | None = None
_PAGE_MAP_TOTAL: int | None = None
_PAGE_MAP_SCOPE: tuple | None = None


def _design_id(url: str) -> str | None:
    match = re.search(r"/design/([A-Za-z0-9_-]+)/", url or "")
    return match.group(1) if match else None


def _page_map_path():
    from pathlib import Path
    return Path(settings.slides_dir) / "canva_pages.json"


def load_page_map(force: bool = False) -> dict[str, int]:
    """Read only this project's measured design, invalidating by content hash."""
    global _PAGE_MAP, _PAGE_MAP_TOTAL, _PAGE_MAP_SCOPE
    import hashlib
    import json

    path = _page_map_path()
    try:
        data = path.read_bytes()
        stamp = hashlib.sha256(data).digest()
    except OSError:
        data = None
        stamp = None
    scope = (settings.project_id, str(path.resolve()), _design_id(settings.canva_url), stamp)
    if _PAGE_MAP is not None and _PAGE_MAP_SCOPE == scope and not force:
        return _PAGE_MAP
    try:
        if data is None:
            raise FileNotFoundError(path)
        raw = json.loads(data)
    except FileNotFoundError:
        _PAGE_MAP, _PAGE_MAP_TOTAL, _PAGE_MAP_SCOPE = {}, None, scope
        return _PAGE_MAP
    except (OSError, ValueError):
        logger.warning("could not read %s — refusing unmeasured Canva pages", path)
        _PAGE_MAP, _PAGE_MAP_TOTAL, _PAGE_MAP_SCOPE = {}, None, scope
        return _PAGE_MAP
    from app.data_sources import require_project_payload

    try:
        if not isinstance(raw, dict):
            raise ValueError("invalid Canva page mapping")
        require_project_payload(raw, "canva_page_mapping", settings.project_id)
        configured = _design_id(settings.canva_url)
        registered = _design_id(raw.get("deck_url", ""))
        if not registered or (settings.canva_url and configured != registered):
            raise ValueError("Canva design is not registered for this project")
        pages = raw.get("pages")
        total = raw.get("total")
        if (not isinstance(pages, dict) or not isinstance(total, int) or total < 1
                or any(not isinstance(key, str) or not isinstance(value, int)
                       or isinstance(value, bool) or not 1 <= value <= total
                       for key, value in pages.items())
                or len(set(pages.values())) != len(pages)):
            raise ValueError("invalid Canva page mapping")
    except ValueError:
        logger.error("canva page map is not scoped to this project/design: %s", path)
        _PAGE_MAP, _PAGE_MAP_TOTAL, _PAGE_MAP_SCOPE = {}, -1, scope
        return _PAGE_MAP
    _PAGE_MAP = dict(pages)
    _PAGE_MAP_TOTAL = total
    _PAGE_MAP_SCOPE = scope
    logger.info("canva page map: %d slides -> a deck of %s pages",
                len(_PAGE_MAP), _PAGE_MAP_TOTAL)
    return _PAGE_MAP


def _page_number(slide_id: str) -> int | None:
    """Which page of the live Canva deck shows this slide? None if unknown.

    This used to be `ew-042` -> page 42, and that is only right while the
    images and the Canva design are the same sequence. They stopped being:
    the export in `data/slides/` has 59 frames, the design has fewer pages,
    so every id past the first divergence pointed at somebody else's slide —
    and the tail pointed past the end of the deck, which is the page that
    came up blank. Arithmetic on an id is not a measurement of another
    document.

    So the mapping is now measured (`scripts/canva_pages.py`) and read from
    disk. A slide that isn't in the measured mapping returns None and the
    window simply doesn't move: a mirror that lags is a nuisance, a mirror
    showing the wrong room while the robot describes this one is a lie told
    to a customer.

    An unmeasured or mismatched design is now unavailable; numeric IDs are
    not evidence of a page in a different Canva design.
    """
    match = re.match(rf"^{re.escape(settings.canva_deck_prefix)}-(\d+)$", slide_id)
    if not match:
        return None
    mapping = load_page_map()
    if not mapping:
        return None
    from app.tools import slides

    if slides.display_payload({"id": slide_id, "source_id": "slide_catalog",
                               "project_id": settings.project_id}) is None:
        return None
    page = mapping.get(slide_id)
    if page is None:
        logger.info("slide %s is not in the canva page map — not moving the "
                    "window", slide_id)
    return page


def deck_order() -> list[str]:
    """The deck as Canva has it: slide ids in live page order.

    This is the deck now. It used to be "every image of type `deck`, in
    filename order", which is our own export talking about itself — 59
    frames, some of them mid-transition, in an order nobody has looked at
    since. The presentation the sales team maintains is the Canva one, so
    that is the one the robot walks, however many pages it happens to have
    today.

    Empty when nothing has been measured, and the caller falls back to the
    old ordering — an unmeasured install still presents.
    """
    mapping = load_page_map()
    return [sid for sid, _ in sorted(mapping.items(), key=lambda kv: kv[1])]


def slide_on_page(number: int) -> str | None:
    """Which of our images is on that Canva page? None if we don't know.

    The inverse of `_page_number`, and needed for the same reason: a person
    clicking the Canva window reports a page number, and turning that back
    into `ew-0NN` by arithmetic is the same mistake in the other direction.
    """
    for slide_id, page in load_page_map().items():
        if page == number:
            return slide_id
    return None


def deck_total() -> int | None:
    """Pages in the live deck, as measured. None if never measured."""
    load_page_map()
    return _PAGE_MAP_TOTAL


async def _launch_browser(args: list[str]):
    """Launch the window. Returns (context_or_browser, page).

    Two things have to be true at once and they pull in different directions.

    **The real Chrome channel.** Canva serves some slide images to a normal
    browser but not to Playwright's bundled Chromium, which it recognises as
    automated — the "Image not found" placeholder on a few slides while the
    rest render. Driving the actual Chrome channel is what makes those load.

    **A persistent context, not `launch()`.** This is what made `--kiosk` do
    nothing. `browser.new_page()` opens a *new* window through CDP, and window
    flags — `--kiosk`, `--start-fullscreen`, `--start-maximized` — only apply
    to the browser's own first window, which `launch()` leaves sitting on
    about:blank. So the flags were passed, accepted, and applied to a window
    nobody ever looked at, while the slides went to an ordinary tabbed window.
    The screenshot showed it plainly: address bar, tab strip, minimise button.
    `launch_persistent_context()` hands back that first window as `pages[0]`,
    which is the one the flags actually shaped.

    Falls back the whole way down — persistent+channel, persistent+bundled,
    plain launch — because a mirror with browser chrome around it still shows
    the deck, and no mirror at all does not.
    """
    import tempfile
    from pathlib import Path

    profile = Path(tempfile.gettempdir()) / "condo-voice-canva-profile"
    channel = (settings.canva_browser_channel or "").strip()

    for use_channel in ([channel] if channel else []) + [None]:
        try:
            context = await _playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                headless=settings.canva_headless,
                args=args,
                no_viewport=True,      # let the window size decide, not a box
                **({"channel": use_channel} if use_channel else {}),
            )
            page = context.pages[0] if context.pages else await context.new_page()
            logger.info(
                "canva window launched (%s, persistent profile)",
                use_channel or "bundled chromium",
            )
            # Into the turn log too. "เปิดไม่เต็มจอ" is unanswerable from a
            # screenshot alone — it looks the same whether the kiosk flags
            # were ignored, the persistent launch fell back, or Canva's own
            # viewer kept its header. Recording which path ran turns three
            # guesses into one fact.
            from app import turnlog

            turnlog.record("canva_launch", how="persistent",
                           channel=use_channel or "bundled", kiosk=settings.canva_kiosk,
                           # Why a window opened, not just that one did. A
                           # `canva_launch` one second after
                           # `close_presentation` was unattributable from the
                           # log alone, and working out which of two code
                           # paths had done it took longer than fixing it.
                           reason=_launch_reason)
            return context, page
        except Exception:
            logger.warning(
                "could not launch a persistent %s window — trying the next option",
                use_channel or "bundled chromium",
            )

    # Last resort: the old path. Kiosk will not apply, but the deck will show.
    browser = await _playwright.chromium.launch(
        headless=settings.canva_headless, args=args
    )
    page = await browser.new_page(viewport={"width": 1920, "height": 1080})
    logger.warning(
        "canva window opened without kiosk — persistent launch failed, so the "
        "browser's own chrome will be visible around the slide"
    )
    from app import turnlog

    turnlog.record("canva_launch", how="fallback_launch", kiosk=False,
                   reason=_launch_reason)
    return browser, page


def _window_args() -> list[str]:
    """Chrome flags for the window we are about to drive.

    Deliberately does not include `--app=<url>`, and the absence is the point.
    Adding it looked like the obvious way to lose the tab strip; it produced a
    fullscreen window that was entirely black. `--app` makes Chrome open the
    URL in a window *of its own*, so the persistent context's `pages[0]` is a
    different page — every goto and every arrow key went somewhere nobody
    could see, while the visible window sat on nothing.

    That is the same mistake as `launch()` + `new_page()`, committed a second
    time while fixing the first: flags on one window, content on another. With
    a persistent context, `--kiosk` already applies to the window being driven.
    """
    if not settings.canva_kiosk:
        return []
    return ["--start-fullscreen", "--kiosk"]


def _is_dead(page) -> bool:
    """Is this handle pointing at a window that no longer exists?

    Deliberately conservative: anything we can't confirm is alive counts as
    dead. Relaunching a healthy window costs a few seconds once; keeping a
    dead one costs every slide change for the rest of the day, silently.
    """
    try:
        if page.is_closed():
            return True
    except Exception:
        return True
    # A persistent context has no `is_connected` — only a Browser does. Asking
    # for it and treating the AttributeError as "dead" would relaunch on every
    # single slide, forever. Absence of the method is not evidence of death.
    is_connected = getattr(_browser, "is_connected", None)
    if is_connected is not None:
        try:
            if not is_connected():
                return True
        except Exception:
            return True
    return False


async def _shutdown_quietly() -> None:
    """Drop the current window and its driver, ignoring anything it says on
    the way out. Called when we already know it is broken."""
    global _playwright, _browser, _page, _at_page, _seen_page
    for closer in (_page, _browser, _playwright):
        if closer is None:
            continue
        try:
            await (closer.stop() if hasattr(closer, "stop") else closer.close())
        except Exception:
            pass
    _page = _browser = _playwright = None
    _at_page = None
    _seen_page = None


def _record_fullscreen(how: str | None) -> None:
    """Always called, on every path, including the failures.

    The log said `canva_launch: persistent, chrome, kiosk=true` three times
    and carried no `canva_fullscreen` line at all — which is not "fullscreen
    failed", it is "we never got that far", and the two need different fixes.
    An outcome that only gets recorded when things go well is not evidence.
    """
    from app import turnlog

    turnlog.record("canva_fullscreen", how=how or "none", worked=how is not None)


async def _go_fullscreen(page) -> None:
    """Fill the screen with the slide, not with browser and Canva furniture.

    Two separate layers of chrome, and only one of them is ours:

    - The *browser* window. `CANVA_KIOSK` already passes --kiosk, which
      handles this one.
    - Canva's own viewer, which keeps a header, a page counter and a border
      even inside a kiosk browser. That is what leaves the slide sitting in a
      letterbox with "40 / 59" printed under it — fine for a laptop, wrong for
      a sales gallery where the deck *is* the display.

    Best effort by design. Canva's markup is theirs to change without telling
    us, so this tries the labelled control first, then the keyboard shortcut,
    then the browser's own fullscreen API, and gives up quietly. Failing means
    a slightly smaller picture; raising here would mean no picture at all.
    """
    # A real click first: the Fullscreen API refuses without user activation,
    # and Canva needs focus before it takes keys anyway.
    try:
        await page.click("body", timeout=3000)
    except Exception:
        pass

    for label in ('button[aria-label*="ull screen" i]',
                  'button[aria-label*="ullscreen" i]',
                  'button[title*="ull screen" i]'):
        try:
            await page.click(label, timeout=1500)
            logger.info("canva: entered fullscreen via %s", label)
            _record_fullscreen("button")
            return
        except Exception:
            continue

    # Canva's viewer shortcut.
    try:
        await page.keyboard.press("f")
        await asyncio.sleep(0.4)
        if await page.evaluate("() => !!document.fullscreenElement"):
            logger.info("canva: entered fullscreen with the 'f' shortcut")
            _record_fullscreen("shortcut")
            return
    except Exception:
        pass

    try:
        await page.evaluate(
            "() => document.documentElement.requestFullscreen "
            "&& document.documentElement.requestFullscreen()"
        )
        await asyncio.sleep(0.4)
        if await page.evaluate("() => !!document.fullscreenElement"):
            logger.info("canva: entered fullscreen via the Fullscreen API")
            _record_fullscreen("api")
            return
    except Exception:
        pass

    logger.info(
        "canva: could not enter fullscreen — the slide still shows, just with "
        "the viewer's own header and page counter around it"
    )
    _record_fullscreen(None)


async def _ensure_page(*, launch: bool = True, why: str = "slide"):
    """Launch the window on first use; reuse it after that.

    `launch=False` means "use the window if there is one, but do not open one
    for this". Blanking passes it, and the reason is a log line: one second
    after `close_presentation` the log recorded `canva_launch` and then
    `canva_fullscreen`, and the guest — who had just asked for the screen to
    go away — got a brand new fullscreen window showing black.

    There was already a guard against that in `_sync`, and it was too narrow:
    it only covered `_page is None`. A *dead* handle takes the branch below
    instead, tears the corpse down and opens a replacement, which is right for
    a slide and absurd for a blank. Deciding it here, from what the caller
    actually wants, is the version that cannot be got wrong by the next path
    that reaches this function.
    """
    global _playwright, _browser, _page, _warned_missing_playwright, _at_page
    global _launch_reason
    if _page is not None and not _is_dead(_page):
        return _page
    if not launch:
        # Note this covers both "no window" and "dead window" — the second is
        # the one that got away.
        logger.info("not opening a canva window just to %s", why)
        if _page is not None:
            await _shutdown_quietly()
        return None
    _launch_reason = why
    if _page is not None:
        # Held a handle to a window that no longer exists. Everything here is
        # fire-and-forget with errors swallowed, so a dead handle doesn't
        # raise — it just quietly does nothing, for the rest of the process.
        # The symptom is "canva ไม่ขึ้น" with a clean log, which is the worst
        # kind: the operator restarts the *session* and it doesn't help,
        # because the corpse is module state that outlives sessions.
        #
        # Two ways to get here, both seen: the previous voice session died
        # (the go_away abort took the whole process's session down with it),
        # or somebody closed the Canva window by hand.
        logger.info("the canva window is gone — opening a new one")
        await _shutdown_quietly()

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        if not _warned_missing_playwright:
            logger.warning(
                "CANVA_URL is set but the `playwright` package isn't installed — "
                "the Canva window will not open. Run: pip install playwright "
                "&& playwright install chromium"
            )
            _warned_missing_playwright = True
        return None

    try:
        _playwright = await async_playwright().start()
        args = _window_args()
        # Hide the "I am automated" bit Canva keys off to withhold some slide
        # images. Cheap, and pairs with using the real Chrome channel below.
        args.append("--disable-blink-features=AutomationControlled")
        _browser, _page = await _launch_browser(args)
        # Page 1, explicitly — not "wherever the copied link pointed".
        await _page.goto(f"{_base_url()}#1", wait_until="domcontentloaded")
        # Canva only takes arrow keys once something in the deck has focus.
        try:
            await _page.click("body", timeout=3000)
        except Exception:
            pass
        _at_page = 1
        logger.info("canva display window opened: %s", _base_url())
    except Exception:
        logger.exception("could not launch the canva display window")
        _page = None
        return None

    # Outside the launch try/except on purpose. Cosmetics must not be able to
    # cost us the window: in here a stray Playwright error would have been
    # caught by the block above, which sets `_page = None` — and a null page
    # is how "canva ไม่ขึ้น" happens. A deck with a header around it is a
    # complaint; no deck at all is a dead demo.
    try:
        await _go_fullscreen(_page)
    except Exception:
        logger.exception("could not put the canva window fullscreen — carrying on")
        _record_fullscreen(None)

    # The deck warm-up used to run here, and it does not belong here. See
    # `open_and_warm()` for what that cost: this function is first reached on
    # a *guest's* first slide, holding a tool call that is allowed five
    # seconds, and the walk takes about thirty-three.
    return _page


async def goto(slide_id: str | None) -> None:
    """Ask the live Canva window to move to the page for this slide id.

    **Returns immediately.** This is called from inside a tool call, and the
    model cannot say anything until that tool returns — on Gemini 3.x
    function calling is synchronous, so awaiting the browser here meant the
    robot stopped mid-conversation while Chromium started up. Measured at
    four seconds of silence on the first `show_slide`, in front of a guest
    who had just asked to see something.

    The window is a *mirror* of the presentation, not part of it. It is
    allowed to be a second behind; it is not allowed to make anyone wait.

    Silent no-op for anything that isn't from the configured deck.
    """
    global _wanted, _task

    if not settings.canva_url:
        return

    if not slide_id:
        # `hide_slide` — the guest is done looking at slides. This used to
        # return here and do nothing, so the robot said "the screen is clear
        # now" while the Canva window carried on showing the last slide.
        _wanted = BLANK
    else:
        number = _page_number(slide_id)
        if number is None:
            return
        _wanted = number
    if _task is None or _task.done():
        # Keep the reference: a bare create_task can be garbage-collected
        # mid-flight, which loses the navigation and the traceback with it.
        _task = asyncio.create_task(_sync())


#: Sentinel target meaning "show nothing". Not a page number.
BLANK = 0

#: A black page rather than about:blank — white glare on a gallery screen is
#: worse than the slide that was there. Kept as a data URL so it works with
#: no network and nothing to serve.
_BLANK_URL = "data:text/html,<body style='margin:0;background:%23000'></body>"

#: The page the window is believed to be on, so a step can be taken instead
#: of a jump. None means "unknown", which forces a reload.
_at_page: int | None = None

#: Above this many pages apart, arrowing there costs more than reloading.
MAX_STEPS = 6


async def read_page(page) -> int | None:
    """What page does Canva itself say it is on? None if it won't say.

    The sync is otherwise open-loop — press a key, assume it landed — so one
    swallowed keypress puts every slide after it out by one, silently and
    permanently. Reading the position back turns that into a correction.

    Canva's viewer rewrites `location.hash` as it moves, so the URL is the
    cheapest place to look. Measured against the real deck with
    `scripts/canva_probe.py`: `#1 -> #2 -> #3 -> #4` on arrow keys and `#20`
    on a deep link, exactly tracking the page. Still an observation about
    today's Canva rather than a documented contract, so this returns None
    instead of guessing when the fragment isn't a number, and everything
    above degrades to open-loop when it does. Re-run the probe if the sync
    ever starts drifting again.
    """
    try:
        fragment = await page.evaluate("location.hash")
    except Exception:
        return None
    fragment = (fragment or "").lstrip("#").strip()
    return int(fragment) if fragment.isdigit() else None


#: Page seen on the last poll, so a change has to hold still for one cycle
#: before it counts. Someone flicking through five pages to find one should
#: produce one narration, not five.
_seen_page: int | None = None


#: When we last moved the deck ourselves. Readings taken too soon after are
#: not evidence of anything: Canva animates between pages, the fragment
#: updates on its own schedule, and a move that timed out leaves `_at_page`
#: pointing somewhere the window hasn't reached yet.
_ours_at: float = 0.0

#: How long to disbelieve the window after one of our own moves. Long enough
#: to cover the animation and a slow image; short enough that a person taking
#: over mid-tour is followed within a breath.
SETTLE_S = 3.0


def mark_ours() -> None:
    """Record that the last move was ours, not a person's."""
    global _ours_at
    _ours_at = time.monotonic()


async def poll_external() -> int | None:
    """Has somebody moved the deck by hand? Returns the page, once.

    The robot drives Canva, but a salesperson standing at the screen will
    click it too — and until now the robot had no idea, so it carried on
    narrating a slide nobody was looking at any more.

    Only reports changes it didn't cause: `_at_page` is what we last set, so
    anything else on screen came from outside. Skips while a move of ours is
    in flight, and requires the new page to survive one full cycle, so
    flicking through to find something narrates the destination rather than
    every page on the way.
    """
    global _at_page, _seen_page

    if not settings.canva_url or _page is None or _lock.locked():
        _seen_page = None
        return None

    # A move of ours is still settling — anything read now is our own doing
    # half-finished, not a person. Believing it was what walked the tour
    # backwards mid-presentation and started it over from slide one.
    if time.monotonic() - _ours_at < SETTLE_S:
        _seen_page = None
        return None

    actual = await read_page(_page)
    if actual is None or actual == _at_page:
        _seen_page = None
        return None

    if actual != _seen_page:
        _seen_page = actual          # first sighting — let it settle
        return None

    _seen_page = None
    _at_page = actual
    logger.info("canva was moved by hand to page %d", actual)
    return actual


async def _resync(page) -> None:
    """Correct our idea of where the window is, if it will tell us."""
    global _at_page
    actual = await read_page(page)
    if actual is None:
        return
    if _at_page is not None and actual != _at_page:
        logger.info("canva was on page %d, not %d — corrected", actual, _at_page)
    _at_page = actual


#: Pause between pages while warming the deck. Long enough for the viewer to
#: ask for the next page's artwork, short enough that fifty pages is under a
#: minute of startup.
WARM_STEP_S = 0.55

_warmed = False


async def warm_deck(page, total: int | None = None) -> int:
    """Walk the whole deck once so every page is drawn before a guest arrives.

    Canva's viewer loads pages as you reach them. Opening the deck and then
    jumping to page 35 doesn't show page 35 — it shows the page template,
    a pale empty gradient, while the artwork is still being fetched. The
    viewer says so itself: the progress bar under the page counter lights up
    only as far as you have walked.

    That is the blank screen a guest saw, and no amount of waiting on our
    side fixes it, because nothing had been asked for. The tour jumps around
    — a question pulls the screen onto a facility twenty pages away and back
    — so "only ever move one page" isn't available either.

    Walking the deck once at startup is. Afterwards the pages are in the
    viewer and a jump lands on a picture. Costs about half a minute, once,
    before anybody is standing there.
    """
    global _at_page, _warmed
    if total is None:
        total = deck_total()
    if not total:
        return 0
    try:
        await page.evaluate("location.hash = '#1'")
        await asyncio.sleep(1.0)
        for _ in range(total - 1):
            await page.keyboard.press("ArrowRight")
            await asyncio.sleep(WARM_STEP_S)
        await page.evaluate("location.hash = '#1'")
        await asyncio.sleep(0.5)
    except Exception:
        logger.warning("could not warm the canva deck", exc_info=True)
        return 0
    _at_page = 1
    _warmed = True
    logger.info("warmed %d canva pages", total)
    return total


async def open_and_warm() -> None:
    """Open the window and walk the deck once, at boot — off anyone's clock.

    Warming used to happen at the end of `_ensure_page`, which sounds like
    the same thing and is not. `_ensure_page` is first reached when a *guest*
    asks for the first slide: `start_presentation` calls
    `_point_canva_at(hold=True)`, whose whole reason for existing is to hold
    the narration until the cover is actually on screen, and it is bounded by
    `CANVA_ARRIVAL_TIMEOUT_S` — five seconds, against a walk that takes about
    thirty-three. So with `CANVA_WARM_DECK` on (the default) that wait timed
    out *every time*, the robot narrated the cover over a blank window — the
    exact bug `hold=True` was written to fix — and the walk was cancelled
    part-done, leaving the far pages unwarmed anyway. Two mechanisms, each
    one correct, and the newer one quietly disabled the older one; neither
    had a symptom that pointed at the other.

    Half a minute of walking is only free while nobody is watching, which is
    what `warm_deck`'s own docstring already assumed ("before anybody is
    standing there"). So it runs from the startup hook, in the background,
    and never from a path a conversation is waiting on.

    Note this opens the Canva window at boot rather than on the first slide.
    That is the point — the pages have to be fetched before a guest arrives —
    and `CANVA_WARM_DECK=false` turns the whole thing off.
    """
    if not settings.canva_url or not settings.canva_warm_deck:
        return
    if not settings.canva_open_at_start:
        # `CANVA_OPEN_AT_START` already existed, and its entire reason for
        # defaulting to off is "a fullscreen browser sitting over the desktop
        # from boot is in the way while you're working". Warming needs a
        # window; moving the warm-up to startup without checking this setting
        # meant the flag that exists to prevent exactly that stopped working,
        # silently — observed on the owner's machine the first time it ran.
        #
        # New mechanism, old mechanism, straight into each other: the thing
        # this file's notes warn about every time. So it loses, loudly. The
        # feature genuinely requires a window at boot — that is a property of
        # walking a deck, not a config bug — and the operator gets told which
        # switch buys it instead of finding a browser over their desktop.
        logger.warning(
            "CANVA_WARM_DECK is on but CANVA_OPEN_AT_START is off, so the "
            "deck will NOT be warmed: walking it needs the window open "
            "before anyone is watching. Set CANVA_OPEN_AT_START=true to warm "
            "at boot, or CANVA_WARM_DECK=false to stop saying this. Until "
            "then a jump to a far page can land on a blank while Canva "
            "fetches it."
        )
        return
    try:
        # Under the lock for the same reason `_sync` takes it: a guest who
        # starts a tour during the walk must queue behind it rather than
        # drive the same window with the same arrow keys at the same time.
        async with _lock:
            if _warmed:
                return
            page = await _ensure_page(why="warm the deck before opening")
            if page is None:
                return
            await warm_deck(page)
    except Exception:
        # Never fatal, and never noisy enough to look like a failed boot: a
        # cold deck shows blanks, no deck shows nothing at all.
        logger.exception("could not warm the canva deck — carrying on")


async def _step_to(page, target: int) -> bool:
    """Move by arrow key, the way a person sitting in front of it would.

    `page.goto(...#N)` re-navigates, and Canva responds by reloading its
    whole viewer — several seconds of black screen and a re-download of the
    slide image. During a narrated tour that put the mirror a full slide
    behind the conversation, which is the thing it exists to avoid.

    The viewer already moves on arrow keys without reloading anything, and a
    tour advances one page at a time, so the common case is a single
    keypress. Returns False if the gap is too wide to be worth stepping.
    """
    if _at_page is None:
        return False
    delta = target - _at_page
    if delta == 0:
        return True
    if abs(delta) > MAX_STEPS:
        return False

    key = "ArrowRight" if delta > 0 else "ArrowLeft"
    for _ in range(abs(delta)):
        await page.keyboard.press(key)
        await asyncio.sleep(0.12)      # let the viewer animate between pages
    return True


async def _jump_to(page, target: int) -> bool:
    """Move a long way without reloading the viewer.

    Arrow keys only cover MAX_STEPS; past that the old code fell back to
    `page.goto(url#n)`, which is a full navigation — Canva tears the deck down
    and builds it again, about three and a half seconds of blank screen. That
    is fine on a tour, where jumps are one page at a time, and terrible on
    "ขอดูฟิตเนส" from slide 59, where the guest asked to see something and
    watched the wall go empty first.

    The viewer already publishes its position in `location.hash` — that is how
    `read_page` works — and it is a single-page app, so writing the hash back
    is the same thing clicking its own controls does. No reload, no rebuild.

    Returns False if the viewer ignored it, so the caller can still deep-link.
    """
    try:
        await page.evaluate(
            "n => { if (location.hash !== '#' + n) location.hash = '#' + n; }",
            target,
        )
    except Exception:
        return False

    # Give the viewer a moment, then believe only what it reports back.
    for _ in range(8):
        await asyncio.sleep(0.12)
        where = await read_page(page)
        if where == target:
            return True
    return False


async def _sync() -> None:
    """Catch the window up to `_wanted`, however far behind it has fallen."""
    global _wanted, _at_page

    async with _lock:
        while (target := _wanted) is not None:
            _wanted = None
            # Blanking a window that is already gone must not bring it back.
            #
            # `close_presentation` shuts the browser down, and then the slide
            # tools' own display push runs `display.show(None)` -> `goto(None)`
            # -> here. With an unconditional `_ensure_page()` that relaunched
            # Chromium from scratch purely to display a black data: URL — the
            # guest asked for the screen to go away and watched a new window
            # open showing "data:text/html,<body style=...>" instead.
            page = await _ensure_page(
                launch=target != BLANK,
                why="blank the screen" if target == BLANK else f"show page {target}",
            )
            if page is None:
                if target == BLANK:
                    # Nothing to blank. Keep draining the queue rather than
                    # returning — a slide asked for while this was in flight
                    # would otherwise be dropped, because `goto` only starts a
                    # new task when this one has finished.
                    continue
                return          # the launch failed; a later slide can retry
            # Slides can change faster than Chromium can navigate — during a
            # narrated tour the model may advance twice while the first page
            # is still loading. Only the newest one is worth showing, so
            # skip anything already superseded rather than replaying them.
            if _wanted is not None:
                continue
            try:
                if target == BLANK:
                    await page.goto(_BLANK_URL, wait_until="domcontentloaded")
                    _at_page = None   # the deck isn't loaded any more
                    continue
                # Where does Canva say it is? Someone may have clicked it by
                # hand, or a keypress may have gone nowhere.
                await _resync(page)

                if await _step_to(page, target):
                    _at_page = target
                    await _resync(page)      # confirm the keys actually took
                    if _at_page != target:
                        # They didn't. Deep-link instead of drifting further.
                        await page.goto(f"{_base_url()}#{target}",
                                        wait_until="domcontentloaded")
                        _at_page = target
                    continue

                # Too far for arrow keys. Try moving inside the running viewer
                # before resorting to a navigation that reloads the whole deck.
                if await _jump_to(page, target):
                    _at_page = target
                    continue

                await page.goto(f"{_base_url()}#{target}", wait_until="domcontentloaded")
                _at_page = target
            except Exception:
                _at_page = None       # position no longer trustworthy
                logger.exception("failed to sync the canva window to page %s", target)


async def arrive(slide_id: str | None, timeout: float = 8.0) -> bool:
    """Move the window there and **wait until it has actually arrived**.

    Unlike `goto()`, this blocks — on purpose, and only from `next_slide`.
    Function calling is synchronous, so the model can't say anything until
    the tool returns; holding here is what makes the narration start at the
    moment the guest sees the slide rather than while the previous one is
    still up.

    Bounded, because a mirror is never worth stalling a conversation for: on
    timeout it gives up and lets the robot talk anyway. Returns whether the
    window really got there.
    """
    if not settings.canva_url or not slide_id:
        return False
    target = _page_number(slide_id)
    if target is None:
        return False

    global _wanted
    _wanted = target

    try:
        await asyncio.wait_for(_sync(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.info("canva did not reach page %d within %.0fs — carrying on",
                    target, timeout)
        return False
    except Exception:
        logger.exception("canva move to page %d failed", target)
        return False

    mark_ours()
    return _at_page == target


async def shutdown() -> None:
    """Close the window cleanly on server shutdown."""
    global _playwright, _browser, _page, _wanted, _task, _at_page

    _wanted = None
    if _task is not None and not _task.done():
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):
            pass
    _task = None

    async with _lock:
        try:
            if _browser is not None:
                await _browser.close()
            if _playwright is not None:
                await _playwright.stop()
        except Exception:
            logger.exception("error closing the canva display window")
        finally:
            _playwright = _browser = _page = None
            _at_page = None


def is_active() -> bool:
    """For a health-check or a log line — is a window actually open right now."""
    return _page is not None


async def self_check() -> tuple[bool, str]:
    """Say at startup whether the Canva window will work, without opening one.

    `goto()` swallows everything on purpose: a guest mid-conversation should
    never be told the mirror is broken. That is the right call during a
    conversation and the wrong one while somebody is setting this up — the
    window simply didn't appear and the only trace was a traceback in a log
    nobody was reading.

    But checking by *opening* it meant a fullscreen browser sat over the
    desktop from the moment the server started. So the check launches a
    headless Chromium, closes it, and reports — proving the install works
    while leaving the screen alone. The real window opens when a
    presentation does, unless CANVA_OPEN_AT_START says otherwise.
    """
    if not settings.canva_url:
        return True, "CANVA_URL not set — Canva mirror off"

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return False, (
            "`playwright` is not installed, so the Canva window cannot open.\n"
            "    Fix:  pip install playwright"
        )

    if settings.canva_open_at_start:
        page = await _ensure_page()
        if page is None:
            return False, _CHROMIUM_HELP
        where = _base_url().split("?", 1)[0]
        if settings.canva_warm_deck:
            # Said here because the walk takes about half a minute and looks
            # like a possessed browser to anyone who does not know it is
            # deliberate — arrow keys firing across the whole deck with
            # nobody touching the machine.
            return True, f"window open at {where} — warming the deck now"
        return True, "window open at %s" % where

    # Prove Chromium is really installed without putting anything on screen.
    try:
        pw = await async_playwright().start()
        try:
            browser = await pw.chromium.launch(headless=True)
            await browser.close()
        finally:
            await pw.stop()
    except Exception:
        logger.exception("chromium could not be started")
        return False, _CHROMIUM_HELP

    return True, "ready — the window opens when a presentation starts"


_CHROMIUM_HELP = (
    "Playwright is installed but Chromium would not start. Most often the "
    "browser itself was never downloaded.\n"
    "    Fix:  playwright install chromium\n"
    "    (the traceback above says which it was)"
)
