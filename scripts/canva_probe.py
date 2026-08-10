#!/usr/bin/env python3
"""
Ask the real Canva window what it knows about itself.

    python scripts/canva_probe.py

Why
---
The slide sync is currently open-loop: we press ArrowRight and *assume* the
deck moved. Nothing ever asks Canva where it actually is, so if one keypress
is swallowed every page after it is wrong, silently.

Closing that loop needs a way to read the current page back. There are
several candidates and no documentation for any of them, so this opens the
real deck, drives it, and prints what each one reports. Then the sync can be
built on whichever actually works instead of on a guess.

Run it on the machine with the Canva window; it needs CANVA_URL in .env and
`playwright install chromium`. It opens a normal (not kiosk) window so you
can watch what happens.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Places a page number might be hiding. Each is tried in the live page and
# reported verbatim — including the ones that come back empty, because
# knowing what *doesn't* work is the point.
PROBES = {
    "location.hash": "location.hash",
    "location.href tail": "location.href.slice(-24)",
    "aria current page": (
        "document.querySelector('[aria-current=\"page\"]')?.textContent ?? ''"
    ),
    "any 'N / M' text": (
        "(document.body.innerText.match(/\\b\\d{1,3}\\s*\\/\\s*\\d{1,3}\\b/) || [''])[0]"
    ),
    "input value": (
        "document.querySelector('input[type=\"number\"], input[aria-label*=\"age\"]')"
        "?.value ?? ''"
    ),
    "title": "document.title",
}


async def snapshot(page) -> dict:
    out = {}
    for label, js in PROBES.items():
        try:
            out[label] = await page.evaluate(js)
        except Exception as exc:
            out[label] = "<error: %s>" % exc
    return out


def show(label: str, data: dict) -> None:
    print("\n--- %s ---" % label)
    for key, value in data.items():
        text = str(value).replace("\n", " ")[:70]
        print("    %-20s %s" % (key, repr(text)))


async def main() -> int:
    from app.config import settings

    if not settings.canva_url:
        print("CANVA_URL is not set in .env")
        return 1

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("pip install playwright && playwright install chromium")
        return 1

    base = settings.canva_url.split("#", 1)[0]
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=False)
    page = await browser.new_page(viewport={"width": 1280, "height": 800})

    try:
        print("opening %s#1" % base[:60])
        await page.goto(f"{base}#1", wait_until="domcontentloaded")
        await asyncio.sleep(4)
        try:
            await page.click("body", timeout=3000)
        except Exception:
            pass
        show("after loading page 1", await snapshot(page))

        # Does it move on an arrow key, and does anything report the change?
        for step in (1, 2, 3):
            await page.keyboard.press("ArrowRight")
            await asyncio.sleep(1.5)
            show("after ArrowRight x%d (expect page %d)" % (step, step + 1),
                 await snapshot(page))

        # And does a deep link still work once the viewer is running?
        await page.goto(f"{base}#20", wait_until="domcontentloaded")
        await asyncio.sleep(4)
        show("after goto #20", await snapshot(page))

        print("\nLook for a probe whose value tracks the page number.")
        print("If one does, the sync can verify every move instead of assuming.")
        print("If none does, arrow keys stay open-loop and we keep a periodic")
        print("re-sync by deep link as the safety net.")
        print("\nLeaving the window open for 20s so you can see the state.")
        await asyncio.sleep(20)
    finally:
        await browser.close()
        await pw.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
