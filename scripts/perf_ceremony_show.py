#!/usr/bin/env python3
"""How smooth are the ceremony's picture slides? Frames per second, CPU slowed.

    python scripts/perf_ceremony_show.py                 # 1920x1080, CPU x4, 4K-wide picture
    python scripts/perf_ceremony_show.py --cpu 6 --seconds 8

The method of scripts/perf_robot_face.py (headless Chromium, CPU throttled, frames
counted over a few seconds), for the event screen's slide engine
(client/assets/ceremony-show.js/.css). It answers one question the owner's picture
slides raised (17 Nov: "โชว์รูปต่างๆให้คนเห็นภาพ"): a picture must keep moving while it is
up -- is a drift by translate alone cheaper than a drift with a gentle scale? This
project measured "scaling a picture inside a frame" as janky on the ROBOT's WebView; the
event screen is a notebook's Chrome, so it is measured again here rather than assumed.

Each variant shows the same `film` slide (a 3840x2160 picture made here, full-bleed, a
second one cross-fading over it, a headline and a figure on the scrim) on the engine's
own stage with the page's show-mode classes:
    still   no drift at all (the floor: what the rest of the slide costs)
    pan     translate only
    zoom    translate + scale 1 -> 1.06 (what ceremony-show.css ships)
and, for the cross-fade, the 2 s in which the second picture comes in.

Headless Chromium rasters in software: the numbers compare the variants with each other
on this PC; they are not the notebook's frame rate. Nothing here talks to the robot, a
model or the network. Needs playwright and Pillow.
"""
from __future__ import annotations

import argparse
import http.server
import io
import json
import pathlib
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent
ASSETS = ROOT / "client" / "assets"
SHA_A, SHA_B = "a" * 64, "b" * 64

PROBE = """(ms) => new Promise(done => {
  const iv = []; let last = performance.now(); let n = 0; const end = last + ms;
  function f(t) { iv.push(t - last); last = t; n++; if (t < end) requestAnimationFrame(f); else {
    iv.sort((a, b) => a - b); done({frames: n, fps: +(n / (ms / 1000)).toFixed(1),
      p95: +iv[Math.floor(iv.length * .95)].toFixed(1), long: iv.filter(x => x > 50).length}); } }
  requestAnimationFrame(f);
})"""

VARIANTS = {
    "still": ".cs-img { animation: none !important; }",
    "pan": "@keyframes perf-pan { from { transform: translate3d(-2.4%, 1.6%, 0); } to { transform: translate3d(2.4%, -1.6%, 0); } }"
           " .cs-img { animation-name: perf-pan !important; }",
    "zoom": "",
}

PAGE = """<!doctype html><html class="mirror mirror-show"><head><meta charset="utf-8">
<style>html, body { margin: 0; height: 100%%; background: #05061a; font-family: 'Segoe UI', sans-serif; }
html.mirror.mirror-show { --mirror-foot: 22vh; } %s</style>
<link rel="stylesheet" href="/assets/ceremony-show.css"></head><body>
<script src="/assets/ceremony-show.js"></script>
<script>
  var pic = function (sha) { return { src: '/story-asset/' + sha, w: 3840, h: 2160 }; };
  window.go = function () {
    CeremonyShow.show({ template: 'film', kicker: 'Embassy World', title: 'Main common areas in Building A',
      shots: [{ asset: pic('%s') }, { asset: pic('%s'), caption: 'Ground floor' }],
      figure: { value: '155', unit: 'metres long', label: 'The swimming pool' },
      beats: [{ at: 0.5, show: 'shot:1' }, { at: 0.6, show: 'figure' }] });
  };
</script></body></html>"""


def _picture(seed: int) -> bytes:
    """A 4K-wide picture with real detail (flat colour would raster for free)."""
    from PIL import Image, ImageDraw

    im = Image.effect_noise((960, 540), 60).convert("RGB").resize((3840, 2160))
    draw = ImageDraw.Draw(im)
    for k in range(40):
        x = (k * 397 + seed * 131) % 3840
        draw.rectangle([x, (k * 211) % 2160, x + 300, (k * 211) % 2160 + 200],
                       outline=(255, 255, 255), width=6, fill=((k * 37) % 255, (seed * 90) % 255, 160))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=88)
    return out.getvalue()


def serve(pictures: dict) -> tuple[http.server.ThreadingHTTPServer, int]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            path = self.path.split("?")[0]
            body, kind = None, "text/html; charset=utf-8"
            if path.startswith("/page/"):
                body = (PAGE % (VARIANTS.get(path[6:], ""), SHA_A, SHA_B)).encode("utf-8")
            elif path.startswith("/assets/") and (ASSETS / path[8:]).is_file() and "/" not in path[8:]:
                body = (ASSETS / path[8:]).read_bytes()
                kind = "text/css" if path.endswith(".css") else "text/javascript"
            elif path.startswith("/story-asset/") and path[13:] in pictures:
                body, kind = pictures[path[13:]], "image/jpeg"
            if body is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


PLAY = """(o) => new Promise(done => {
  const iv = []; let last = performance.now(); let n = 0; const t0 = last;
  function f(t) {
    iv.push(t - last); last = t; n++;
    const k = Math.min(1, (t - t0) / o.ms);
    CeremonyShow.progress(k);
    if (k < 1) requestAnimationFrame(f); else {
      CeremonyShow.settle();
      iv.sort((a, b) => a - b);
      done({frames: n, fps: +(n / ((t - t0) / 1000)).toFixed(1), p95: +iv[Math.floor(iv.length * .95)].toFixed(1),
            long: iv.filter(x => x > 50).length, state: CeremonyShow.state()});
    }
  }
  requestAnimationFrame(f);
})"""


def measure_acts(args) -> int:
    """Every act slide on the REAL room-screen page (app/mirror_server.py, the real pictures),
    played through from its first beat to its end on a frame clock (her voice's length at 14
    characters a second), CPU slowed: frames per second per cue, then per act."""
    import asyncio
    import secrets
    import sys

    sys.path.insert(0, str(ROOT))

    async def run() -> dict:
        import uvicorn
        from playwright.async_api import async_playwright

        from app import ceremony_slides, event_script, face_mirror, mirror_server
        from app.config import settings

        settings.mirror_token = secrets.token_urlsafe(12)
        settings.stories_enabled = True
        cues = [c for c in event_script.cues() if c.get("en") and c.get("th")]
        server = uvicorn.Server(uvicorn.Config(mirror_server.app, host="127.0.0.1", port=args.port, lifespan="off",
                                               log_level="warning", access_log=False))
        serving = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.05)
        rows: dict = {}
        width, height = (int(x) for x in args.size.lower().split("x"))
        try:
            async with async_playwright() as pw:
                flags = (["--ignore-gpu-blocklist", "--enable-gpu", "--use-angle=d3d11"] if args.webgl
                         else ["--disable-webgl", "--disable-3d-apis"])
                browser = await pw.chromium.launch(headless=True, args=["--autoplay-policy=no-user-gesture-required"] + flags)
                page = await browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
                await page.goto("http://127.0.0.1:%d/mirror?token=%s" % (args.port, settings.mirror_token))
                while not face_mirror.watching():
                    await asyncio.sleep(0.2)
                await asyncio.sleep(1.0)
                face_mirror.call_started()
                face_mirror.publish_json({"type": "ready", "output_rate": 24000, "profile": "ceremony"})
                await asyncio.sleep(1.5)
                cdp = await page.context.new_cdp_session(page)
                for cue in cues:
                    if args.only and cue["id"] not in args.only.split(","):
                        continue
                    slide = ceremony_slides.for_cue(cue["id"], drafts=True)
                    if not slide or slide.get("template") != "act":
                        continue
                    await cdp.send("Emulation.setCPUThrottlingRate", {"rate": 1})
                    await page.evaluate("s => CeremonyShow.show(s)", slide)
                    for _ in range(80):
                        st = await page.evaluate("() => CeremonyShow.state()")
                        if st.get("started") and st.get("loaded", 0) >= st.get("pictures", 0):
                            break
                        await asyncio.sleep(0.1)
                    await asyncio.sleep(0.8)
                    await cdp.send("Emulation.setCPUThrottlingRate", {"rate": args.cpu})
                    ms = min(args.cap, len(cue["en"]) / 14.0) * 1000
                    got = await page.evaluate(PLAY, {"ms": ms})
                    rows.setdefault(slide["act"], {})[cue["id"]] = {k: got[k] for k in ("fps", "p95", "long", "frames")}
                    print("  %-16s %-18s %5.1f fps  p95 %5.1f ms  %2d frames > 50 ms  (gl %s)" % (
                        slide["act"], cue["id"], got["fps"], got["p95"], got["long"], got["state"].get("gl")))
                await page.evaluate("() => CeremonyShow.clear()")
                face_mirror.call_ended()
                await browser.close()
        finally:
            server.should_exit = True
            await serving
        return rows

    rows = asyncio.run(run())
    print("%s, CPU x%g, each line played start to end (headless, %s)" % (args.size, args.cpu,
                                                                         "GPU" if args.webgl else "software raster"))
    for act, cues in sorted(rows.items()):
        fps = [r["fps"] for r in cues.values()]
        print("  %-16s %2d cues  mean %5.1f fps  worst %5.1f fps  worst p95 %5.1f ms" % (
            act, len(fps), sum(fps) / len(fps), min(fps), max(r["p95"] for r in cues.values())))
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cpu", type=float, default=4.0, help="CPU throttling rate (default 4)")
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--json", default="", help="also write the numbers to this file")
    ap.add_argument("--acts", action="store_true", help="every act slide of the ceremony on the real page, per cue and per act")
    ap.add_argument("--webgl", action="store_true", help="with --acts: a page on this PC's GPU (parallax drawn by WebGL)")
    ap.add_argument("--cap", type=float, default=14.0, help="with --acts: seconds of a line played at most")
    ap.add_argument("--port", type=int, default=8796)
    ap.add_argument("--only", default="", help="with --acts: these cue ids (comma-separated)")
    args = ap.parse_args(argv)
    if args.acts:
        return measure_acts(args)
    from playwright.sync_api import sync_playwright

    server, port = serve({SHA_A: _picture(1), SHA_B: _picture(2)})
    width, height = (int(x) for x in args.size.lower().split("x"))
    rows = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--disable-webgl", "--disable-3d-apis"])
            for name in VARIANTS:
                page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
                cdp = page.context.new_cdp_session(page)
                page.goto("http://127.0.0.1:%d/page/%s" % (port, name))
                page.evaluate("go()")
                page.wait_for_function("CeremonyShow.state().loaded === 2", timeout=20000)
                page.wait_for_timeout(1500)                     # the opening is in, the picture decoded
                cdp.send("Emulation.setCPUThrottlingRate", {"rate": args.cpu})
                hold = page.evaluate(PROBE, args.seconds * 1000)
                page.evaluate("CeremonyShow.progress(0.7)")     # the second picture and the figure come in
                fade = page.evaluate(PROBE, 2000)
                rows[name] = {"hold": hold, "crossfade": fade}
                page.close()
            browser.close()
    finally:
        server.shutdown()
    print("%dx%d, CPU x%g, %gs hold / 2s cross-fade (headless, software raster)" % (width, height, args.cpu, args.seconds))
    for name, row in rows.items():
        print("  %-6s hold %5.1f fps (p95 %5.1f ms, %2d frames > 50 ms) | cross-fade %5.1f fps (p95 %5.1f ms, %2d > 50 ms)" % (
            name, row["hold"]["fps"], row["hold"]["p95"], row["hold"]["long"],
            row["crossfade"]["fps"], row["crossfade"]["p95"], row["crossfade"]["long"]))
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
