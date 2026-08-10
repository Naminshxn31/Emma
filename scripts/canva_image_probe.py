#!/usr/bin/env python3
"""
Find out *why* Canva's slide images go missing in the driven window.

    python scripts/canva_image_probe.py            # slides 2, 37, 47
    python scripts/canva_image_probe.py 47 12 30   # specific slides

Why
---
The automated Canva mirror shows the slide frame but the slide image is a
broken-image icon — on many pages, including the thumbnail strip — while the
same link opened by hand in a logged-in browser renders fully. A broken icon
means the <img> got an *error*, not that it is still loading. The only thing
that settles the cause is the failing request's status, so this reports it.

For each slide it opens by deep link it prints:

  * the HTTP status of every image request (how many 200 vs 4xx/5xx),
  * every broken <img> still in the page (no size filter this time), with its
    src and whether a src was even set,
  * a direct fetch() of each broken image's URL from inside the page, so you
    see the exact status the browser got (403 = needs auth/referer, 429 =
    rate-limited, 404 = gone, a network error = blocked/aborted).

Read the verdict line. 4xx across many slides in this fresh browser, while a
logged-in browser is fine, means Canva is gating the images on a real session
— the fix is to drive a logged-in/persistent profile, not to re-navigate.

Needs CANVA_URL in .env and `playwright install chromium`. Visible, not
kiosk, so you can watch. Ctrl-C is fine, but let a slide finish to see its
report.
"""
from __future__ import annotations

import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: Every broken image, with enough about it to tell a real slide asset from a
#: stray icon. No size filter — the last version's ≥300px gate is exactly why
#: it reported "clean" while the screen showed a broken picture.
_BROKEN_IMGS_JS = """
Array.from(document.images)
  .filter(img => img.complete && img.naturalWidth === 0)
  .map(img => {
    const r = img.getBoundingClientRect();
    return {src: img.currentSrc || img.src || '(no src)',
            boxW: Math.round(r.width), boxH: Math.round(r.height)};
  })
"""


#: How is the slide actually drawn, and can this browser draw it? Canva
#: paints slides on <canvas>/WebGL, not <img> — so 200s with no broken <img>
#: but a blank picture means the pixels arrived and were never rendered.
#: Reports canvas surfaces (and whether they're blank) plus whether WebGL is
#: even available, which is the usual reason an automated browser shows a
#: placeholder where a real one shows the slide.
_RENDER_INSPECT_JS = r"""
(() => {
  const canvases = Array.from(document.querySelectorAll('canvas')).map(c => {
    let kind = 'none', blank = null;
    try {
      if (c.getContext('webgl2') || c.getContext('webgl')) kind = 'webgl';
      else if (c.getContext('2d')) {
        kind = '2d';
        const g = c.getContext('2d');
        const d = g.getImageData(0, 0, Math.min(c.width,32), Math.min(c.height,32)).data;
        blank = d.every(v => v === 0);
      }
    } catch (e) { kind = 'err'; }
    const r = c.getBoundingClientRect();
    return {kind, blank, w: c.width, h: c.height,
            boxW: Math.round(r.width), boxH: Math.round(r.height)};
  });
  let webgl = 'no';
  try {
    const t = document.createElement('canvas');
    webgl = (t.getContext('webgl2') || t.getContext('webgl')) ? 'yes' : 'no';
  } catch (e) { webgl = 'err'; }
  return {canvasCount: canvases.length,
          big: canvases.filter(c => c.boxW >= 300 || c.boxH >= 300),
          webgl};
})()
"""


def _status_of(page, url: str):
    """Ask the page to fetch the URL and report what status it really gets."""
    js = (
        "fetch(%r, {mode:'no-cors'}).then(r => r.status + ' ' + r.type)"
        ".catch(e => 'ERR ' + e)" % url
    )
    return page.evaluate(js)


async def _report(page, title: str, responses: list) -> None:
    print("\n==================== %s ====================" % title)

    imgs = [(s, u) for (kind, s, u) in responses if kind == "image"]
    by_status = Counter(s for s, _ in imgs)
    print("  image responses seen: %d  ->  %s"
          % (len(imgs), dict(by_status) or "none"))
    for status, url in imgs:
        if status >= 400:
            print("    BAD %d  %s" % (status, url[:95]))

    # Canva loads big design assets by fetch/XHR, not <img>, so a failed one
    # hides from the image-only view above. Catch every non-2xx, any type.
    others = [(k, s, u) for (k, s, u) in responses
              if k != "image" and isinstance(s, int) and s >= 400]
    if others:
        print("  other failed responses (xhr/fetch/media/...):")
        for k, s, u in others[:15]:
            print("    [%s] %d %s" % (k, s, u[:88]))

    try:
        not_found = await page.evaluate(
            "document.body.innerText.includes('Image not found')")
    except Exception:
        not_found = False
    print("  Canva 'Image not found' on page: %s" % not_found)

    broken = await page.evaluate(_BROKEN_IMGS_JS)
    print("  broken <img> in the DOM: %d" % len(broken))
    for img in broken[:12]:
        print("    %s  box=%dx%d" % (str(img["src"])[:85], img["boxW"], img["boxH"]))

    # The decisive part: what status does the browser actually get for them?
    checked = set()
    for img in broken[:8]:
        src = img["src"]
        if not src or src == "(no src)" or src in checked:
            continue
        checked.add(src)
        try:
            status = await _status_of(page, src)
        except Exception as exc:
            status = "eval-failed: %s" % exc
        print("    fetch -> %-16s %s" % (status, src[:80]))

    # How the slide is drawn — the part that matters once the bytes arrive.
    try:
        render = await page.evaluate(_RENDER_INSPECT_JS)
    except Exception as exc:
        render = {"canvasCount": "eval-failed: %s" % exc, "big": [], "webgl": "?"}
    print("  render: canvases=%s  webgl=%s"
          % (render.get("canvasCount"), render.get("webgl")))
    for c in render.get("big", [])[:6]:
        print("    big canvas %s  buf=%dx%d box=%dx%d blank=%s"
              % (c["kind"], c["w"], c["h"], c["boxW"], c["boxH"], c["blank"]))

    bad = [s for s, _ in imgs if s >= 400]
    big = render.get("big", [])
    blank_2d = any(c.get("kind") == "2d" and c.get("blank") for c in big)
    if bad:
        codes = set(bad)
        if codes & {401, 403}:
            verdict = "images 401/403 — gated on a session this browser lacks (log in)"
        elif 429 in codes:
            verdict = "images 429 — rate-limited (too many/automation-flagged)"
        elif any(s >= 500 for s in codes):
            verdict = "images 5xx — Canva served an error"
        else:
            verdict = "images 4xx — see the codes above"
    elif not_found:
        verdict = ("Canva shows 'Image not found' — it withheld this slide's "
                   "asset from this browser. Drive real Chrome + hide the "
                   "automation flag (see CANVA_BROWSER_CHANNEL).")
    elif broken:
        verdict = ("images broken but no 4xx logged — likely blocked before a "
                   "response (bot/automation) or never requested")
    elif render.get("webgl") != "yes":
        verdict = ("bytes load fine but WebGL is %s — the slide is drawn on a "
                   "GL canvas this browser can't paint (launch-flag fix)"
                   % render.get("webgl"))
    elif blank_2d:
        verdict = ("bytes load fine, WebGL ok, but a large canvas is blank — "
                   "rendering ran and produced nothing (GPU/paint issue)")
    elif not big:
        verdict = ("no large canvas found — the slide surface didn't mount "
                   "(needs more load time, or the viewer didn't render this page)")
    else:
        verdict = "nothing broken and a canvas is present — slide should be visible"
    print("  >> %s" % verdict)


async def main() -> int:
    from app.config import settings

    slides = [int(a) for a in sys.argv[1:]] or [2, 37, 47]

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
    # Launch exactly the way the app now does — real Chrome channel plus the
    # automation flag hidden — so re-running this validates the fix directly.
    args = ["--disable-blink-features=AutomationControlled"]
    channel = (settings.canva_browser_channel or "").strip()
    try:
        browser = await pw.chromium.launch(
            headless=False, channel=channel or None, args=args)
        print("launched channel=%r (the app's setting)" % (channel or "chromium"))
    except Exception as exc:
        print("channel %r unavailable (%s) — using bundled Chromium" % (channel, exc))
        browser = await pw.chromium.launch(headless=False, args=args)
    page = await browser.new_page(viewport={"width": 1280, "height": 800})

    # Collect responses synchronously — status/url/resource_type are plain
    # properties, so no task-scheduling race like the first version had.
    responses: list = []
    page.on("response", lambda r: responses.append(
        (r.request.resource_type, r.status, r.url)))

    try:
        for n in slides:
            responses.clear()
            print("\n>>> deep-linking to slide %d ..." % n)
            await page.goto("%s#%d" % (base, n), wait_until="domcontentloaded")
            await asyncio.sleep(6)   # let images attempt to load / fail
            await _report(page, "slide %d" % n, list(responses))

        print("\nLeaving the window open for 20s so you can watch.")
        await asyncio.sleep(20)
    finally:
        await browser.close()
        await pw.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
