"""
A real Chromium window on the owner's screen, driven by voice.

Why a window and not a panel inside `/display`: most of the web refuses to be
embedded. Canva taught this project that already — its view links set
`X-Frame-Options`/CSP and render nothing inside an `<iframe>`, confirmed by
hand rather than assumed (see `canva_display.py`) — and Google, Facebook,
banks and most news sites behave the same. Worse, a blocked frame usually
fires no error at all: the screen simply goes white and nothing says why.
Driving a browser window sidesteps the entire class, because nothing is
being embedded.

Deliberately **not** the Canva window. `canva_display._page` carries the
deck's position (`_at_page`), its warm-up state and its resync logic; sending
that handle to YouTube would leave every one of them describing a page that
is no longer there. Two purposes, two handles — much cheaper than the bug
where the mirror believes it is on slide 12 and the window is playing a music
video.

The launch shape below is copied from `canva_display` on purpose, because
every line of it was paid for: a *persistent context* rather than `launch()`
+ `new_page()` (window flags apply only to the browser's own first window, so
`--kiosk` on a `new_page()` window shapes a blank one nobody is looking at),
the real Chrome channel first with a fallback to bundled Chromium, and its
own profile directory so the two windows never fight over one.
"""
from __future__ import annotations

import asyncio
import logging

from app import turnlog
from app.config import settings

logger = logging.getLogger("condo_voice.webstage")

_playwright = None
_browser = None
_page = None
_lock = asyncio.Lock()
_task: asyncio.Task | None = None
#: Newest request wins — "no, the other one" while the first is still loading
#: should land on the second, not play both in turn.
_wanted: str | None = None
_warned_missing_playwright = False


def enabled() -> bool:
    """Is the screen allowed to be used this way at all?

    Off by default. On the gallery machine `open_in_browser` opens the staff's
    own browser, and a receptionist that starts putting arbitrary web pages on
    the presentation screen because a visitor asked is a different product.
    """
    return bool(settings.web_stage)


def is_open() -> bool:
    return _page is not None


def _window_args() -> list[str]:
    if not settings.web_stage_kiosk:
        return []
    return ["--start-fullscreen", "--kiosk"]


async def _launch_browser(args: list[str]):
    """Open the window. Returns (context_or_browser, page).

    Its own function rather than one imported from `canva_display`, for a
    reason that is not style: `tests/conftest.py` blocks browser launches by
    patching a named function *per module*, so a launcher shared between two
    modules leaves whichever module the guard does not name free to open a
    real window during the test run. That is the exact shape of the bug where
    pytest put a fullscreen kiosk Chrome on the developer's screen.
    """
    import tempfile
    from pathlib import Path

    profile = Path(tempfile.gettempdir()) / "condo-voice-webstage-profile"
    channel = (settings.web_stage_channel or "").strip()

    for use_channel in ([channel] if channel else []) + [None]:
        try:
            context = await _playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                headless=False,
                args=args,
                no_viewport=True,
                **({"channel": use_channel} if use_channel else {}),
            )
            page = context.pages[0] if context.pages else await context.new_page()
            logger.info("web stage window opened (%s)",
                        use_channel or "bundled chromium")
            return context, page
        except Exception:
            logger.warning("could not launch a persistent %s window - trying next",
                           use_channel or "bundled chromium")
    browser = await _playwright.chromium.launch(headless=False, args=args)
    return browser, await browser.new_page()


def _is_dead(page) -> bool:
    """Anything we cannot confirm is alive counts as dead.

    Same rule and the same reason as the Canva window: a handle to a window
    the owner closed by hand swallows every later command silently, and the
    symptom is "it stopped working" with a clean log. Note `BrowserContext`
    has no `is_connected` — only `Browser` does — so an `AttributeError` here
    must not be read as death, or every request opens another window.
    """
    try:
        if page.is_closed():
            return True
    except Exception:
        return True
    return False


async def _ensure_page(*, launch: bool = True):
    """The window, opening one only if that is what the caller actually wants.

    `launch=False` is how closing stays closed. The Canva path shipped this
    guard twice before it held: the first version covered only "no window",
    and a *dead* handle took the other branch and opened a replacement — so
    asking for the screen to go away produced a brand new window instead.
    """
    global _playwright, _browser, _page, _warned_missing_playwright

    if _page is not None and not _is_dead(_page):
        return _page
    if not launch:
        # Covers both "no window" and "dead window" — the second is the one
        # that got away last time.
        if _page is not None:
            await _shutdown_quietly()
        return None
    if _page is not None:
        logger.info("the web stage window is gone - opening a new one")
        await _shutdown_quietly()

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        if not _warned_missing_playwright:
            logger.warning(
                "WEB_STAGE is on but `playwright` is not installed, so nothing "
                "can be put on screen. Run: pip install playwright && "
                "playwright install chromium"
            )
            _warned_missing_playwright = True
        return None

    try:
        _playwright = await async_playwright().start()
        _browser, _page = await _launch_browser(_window_args())
    except Exception:
        logger.exception("could not open the web stage window")
        _page = None
        return None
    return _page


async def _shutdown_quietly() -> None:
    global _playwright, _browser, _page
    try:
        if _browser is not None:
            await _browser.close()
        if _playwright is not None:
            await _playwright.stop()
    except Exception:
        logger.info("web stage teardown was not clean", exc_info=True)
    finally:
        _playwright = _browser = _page = None


def request(url: str) -> None:
    """Put this page on the screen. **Returns immediately.**

    Called from inside a tool call, and function calling on Gemini is
    synchronous — the model cannot say a word until the tool returns. Waiting
    for Chromium here is the bug `start_presentation` already shipped once:
    four measured seconds of silence in front of someone who had just asked
    for something.
    """
    global _wanted, _task

    _wanted = url
    if _task is None or _task.done():
        # Keep the reference: a bare create_task can be collected mid-flight,
        # taking the navigation and its traceback with it.
        _task = asyncio.create_task(_sync())


async def _sync() -> None:
    global _wanted

    async with _lock:
        while (target := _wanted) is not None:
            _wanted = None
            # Everything that changes what the person is *looking at* waits
            # for the words that announced it. Emma finishes generating
            # "เดี๋ยวเปิดให้นะคะ" several times faster than she says it, so
            # navigating now starts the video over the top of her own
            # sentence — and a page with sound is a worse collision than a
            # slide: two voices at once, rather than a picture that is early.
            from app import display

            await display.wait_until_heard(max_wait=20.0)
            if _wanted is not None:
                continue          # superseded while we waited
            page = await _ensure_page()
            if page is None:
                return            # no window; a later request can retry
            try:
                await page.goto(target, wait_until="domcontentloaded")
                turnlog.record("web_stage", url=target, ok=True)
            except Exception:
                logger.exception("could not show %s", target)
                turnlog.record("web_stage", url=target, ok=False)


async def close() -> None:
    """Close the window. Never opens one."""
    global _wanted

    _wanted = None
    async with _lock:
        await _ensure_page(launch=False)
    turnlog.record("web_stage_close")


async def shutdown() -> None:
    """Server is stopping."""
    global _task

    if _task is not None and not _task.done():
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):
            pass
    _task = None
    async with _lock:
        await _shutdown_quietly()


def reset() -> None:
    """Tests: drop every handle and task without touching a real browser."""
    global _playwright, _browser, _page, _task, _wanted, _warned_missing_playwright

    _playwright = _browser = _page = None
    _task = None
    _wanted = None
    _warned_missing_playwright = False
