#!/usr/bin/env python3
"""Watch the ceremony's debut (card 1, app/ceremony_reveal.py) on this PC -- the room screen's own page,
with its sound: the countdown, the black, the hit, the owner's wave -- and then the first line (s02_l03)
coming in from the held logo.

    python scripts/preview_ceremony_reveal.py                          # prints a URL: open it in Chrome (F11)
    python scripts/preview_ceremony_reveal.py --shots <folder> [--every 0.5] [--size 2048x1080]
    python scripts/preview_ceremony_reveal.py --perf [--cpu 4]         # frame times: idle, the build, the wave

The page is the event screen's (/mirror?sound=1: it plays the effect aloud, as the hall's screen plays
Emma's voice). It opens in the ceremony's show mode, the opening clip plays once as it does on the day,
and then the press: `await ceremony_reveal.start()` exactly as card 1 calls it. Headless (--shots, --perf):
Chromium without WebGL (and its own H.264 decoder: the room's Chrome decodes on the GPU), its numbers
compare with each other on this PC, they are not the notebook's. Nothing here talks to the robot or a
model.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PROBE = """(ms) => new Promise(done => {
  const iv = []; let last = performance.now(); let n = 0; const end = last + ms;
  function f(t) { iv.push(t - last); last = t; n++; if (t < end) requestAnimationFrame(f); else {
    iv.sort((a, b) => a - b); done({frames: n, fps: +(n / (ms / 1000)).toFixed(1),
      p95: +iv[Math.floor(iv.length * .95)].toFixed(1), long: iv.filter(x => x > 50).length}); } }
  requestAnimationFrame(f);
})"""


def _preview():
    spec = importlib.util.spec_from_file_location("preview_ceremony_show", ROOT / "scripts" / "preview_ceremony_show.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def run(args) -> int:
    import uvicorn

    from app import ceremony_reveal, face_mirror, mirror_server
    from app.config import settings

    settings.mirror_token = secrets.token_urlsafe(12)
    settings.stories_enabled = True
    P = _preview()
    spec = ceremony_reveal.timeline()
    print("debut: %s" % json.dumps(spec))
    server = uvicorn.Server(uvicorn.Config(mirror_server.app, host="127.0.0.1", port=args.port, lifespan="off",
                                           log_level="warning", access_log=False))
    serving = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started or serving.done():
            break
        await asyncio.sleep(0.05)
    if not server.started:
        print("could not listen on 127.0.0.1:%d -- try --port" % args.port)
        return 2
    url = "http://127.0.0.1:%d/mirror?token=%s&sound=1" % (args.port, settings.mirror_token)
    headless = bool(args.shots or args.perf)
    playwright = browser = page = None
    out = Path(args.shots) if args.shots else None
    try:
        if headless:
            from playwright.async_api import async_playwright

            playwright = await async_playwright().start()
            # (--gpu: this PC's GPU composites and decodes, as the room notebook's Chrome does; WebGL stays
            # off either way -- the robot's face is not on this screen in show mode)
            gpu = ["--use-angle=d3d11", "--ignore-gpu-blocklist", "--enable-gpu-rasterization"] if args.gpu else []
            browser = await playwright.chromium.launch(headless=True, args=[
                "--autoplay-policy=no-user-gesture-required", "--disable-webgl", *gpu])
            width, height = (int(x) for x in args.size.lower().split("x"))
            page = await browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
            page.on("console", lambda m: print("  [page %s] %s" % (m.type, m.text)) if m.type in ("warning", "error") else None)
            await page.goto(url)
            if out:
                out.mkdir(parents=True, exist_ok=True)
                for old in out.glob("reveal_*.png"):
                    old.unlink()
        else:
            print("\nopen this in Chrome (full screen: F11; sound on), the reveal starts once the page is there:\n\n  %s\n" % url)
        while not face_mirror.watching():
            await asyncio.sleep(0.2)
        await asyncio.sleep(1.5)
        face_mirror.call_started()
        face_mirror.publish_json({"type": "ready", "output_rate": P.RATE, "profile": "ceremony"})
        # (the opening clip plays once as the screen comes into show mode: the press comes after it)
        await asyncio.sleep(args.wait)
        cdp = None
        if page is not None and args.perf:
            cdp = await page.context.new_cdp_session(page)
            await cdp.send("Emulation.setCPUThrottlingRate", {"rate": args.cpu})
            idle = await page.evaluate(PROBE, 3000)
            print("idle stage (the held logo), CPU x%s: %s" % (args.cpu, idle))
        got = await ceremony_reveal.start()
        # A watcher's window narrower than a hall screen (ready() counts only landscape >= 1000 px,
        # so a staff phone is not "the screen") is refused no_show_screen: say so and wait for the
        # page to come back bigger instead of ending the preview (23 Nov: the owner's first look
        # ended here).
        while page is None and not got.get("ok") and got.get("why") == "no_show_screen":
            print("the page's window is too small to count as the hall screen -- maximise Chrome "
                  "(or F11) and reload the page; waiting ...", flush=True)
            await asyncio.sleep(3)
            got = await ceremony_reveal.start()
        print("start(): %s" % {k: v for k, v in got.items() if k != "numbers_in_s"})
        if not got.get("ok"):
            return 1
        began = time.monotonic()
        if page is not None and args.perf:
            during = await page.evaluate(PROBE, int((got["zero_in_s"] - 0.3) * 1000))
            print("the build, CPU x%s: %s" % (args.cpu, during))
            await asyncio.sleep(max(0.0, got["impact_in_s"] - (time.monotonic() - began)))
            settle = spec["settle_s"] - spec["impact_s"]
            during = await page.evaluate(PROBE, int(settle * 1000))
            print("the hit and the wave (%.1f s), CPU x%s: %s" % (settle, args.cpu, during))
        if page is not None and out:
            k = 0
            while True:
                due = began + k * args.every
                if due > began + got["length_s"] + 1.0:
                    break
                await asyncio.sleep(max(0.0, due - time.monotonic()))
                state = await page.evaluate("() => window.CeremonyShow ? window.CeremonyShow.state() : null")
                # (named by the time it is taken: a headless screenshot holds the page ~1 s, so the shots
                # are as dense as that allows, never what --every asked)
                at = time.monotonic() - began
                await page.screenshot(path=str(out / ("reveal_t%04.1f.png" % at)))
                print("  t=%4.1f %s" % (at, json.dumps((state or {}).get("reveal"))))
                k = max(k + 1, int((time.monotonic() - began) / args.every) + 1)
        else:
            await asyncio.sleep(max(0.0, got["length_s"] + 1.0 - (time.monotonic() - began)))
        if args.line:
            todo = P.lines([args.line])
            clips = P._clips()
            if todo and todo[0]["id"] in clips:

                async def mark(name: str) -> None:
                    if page is not None and out:
                        await page.screenshot(path=str(out / ("reveal_line_%s.png" % name)))
                print("then %s: %s" % (todo[0]["id"], todo[0]["en"][:70]))
                await P.play(todo[0], clips[todo[0]["id"]], on_mark=mark if out else None, every=args.every * 2 if out else 0)
        await asyncio.sleep(args.hold)
        face_mirror.call_ended()
        await asyncio.sleep(0.3)
        if out:
            print("screenshots: %s" % out.resolve())
    finally:
        if browser is not None:
            await browser.close()
        if playwright is not None:
            await playwright.stop()
        server.should_exit = True
        await serving
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=8792)
    ap.add_argument("--shots", default="", help="a folder: headless Chromium, a screenshot every --every seconds")
    ap.add_argument("--every", type=float, default=0.5)
    ap.add_argument("--size", default="1920x1080", help="the screen (2048x1080 is ~1.9:1)")
    ap.add_argument("--wait", type=float, default=8.0, help="seconds in show mode before the press (the opening clip)")
    ap.add_argument("--line", default="s02_l03", help="the line after the reveal ('' = none)")
    ap.add_argument("--hold", type=float, default=2.0)
    ap.add_argument("--perf", action="store_true", help="headless: frame times of the idle stage and of the reveal")
    ap.add_argument("--cpu", type=float, default=4, help="with --perf: CPU slowed this many times")
    ap.add_argument("--gpu", action="store_true", help="headless on this PC's GPU (else Chromium's software raster)")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
