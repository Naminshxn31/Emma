"""How smooth is the robot face page? Frames per second, CPU slowed like the robot.

    python scripts/perf_robot_face.py                       # client/voice-preview.html
    python scripts/perf_robot_face.py a.html b.html         # compare variants
    python scripts/perf_robot_face.py --cpu 6 --webgl a.html

Why this exists (2026-10-10): the first "anime presenter" theme looked fine
in a screenshot and made the robot's page visibly slow — the owner saw it
before any number did. Measured here it was 29.7 -> 13.7 fps while
listening, and every fix that brought it back (no drifting layer, no
backdrop-filter, no idle infinite animation, --voice off <html>) was found
by switching one thing off and measuring again. Screenshots cannot show this.

Each page is served at /preview?app=1 from a tiny static server (the rest of
client/ beside it, like app/main.py serves it), opened in headless Chromium
at the robot's 1080x1920 portrait, CPU throttled, and timed for 6 s in two
states: listening, and speaking with mouth-level events at 30 Hz.

WebGL is off by default on purpose: headless renders Emma's 3D body in
software (SwiftShader), which takes the whole throttled CPU (2-3 fps for
every variant) and hides everything else. With it off her spot is empty (the
SVG puppet that stood in was deleted, 14 Oct) and what is measured is the page
around her. `--webgl` keeps it.

Nothing here talks to the server, Gemini or the robot. Needs playwright.
"""
from __future__ import annotations

import argparse
import http.server
import json
import pathlib
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent / "client"

PROBE = """(ms) => new Promise(done => {
  const iv = []; let last = performance.now(); let n = 0; const end = last + ms;
  function f(t) { iv.push(t - last); last = t; n++; if (t < end) requestAnimationFrame(f); else {
    iv.sort((a, b) => a - b); done({frames: n, fps: +(n / (ms / 1000)).toFixed(1),
      p95: +iv[Math.floor(iv.length * .95)].toFixed(1), long: iv.filter(x => x > 50).length}); } }
  requestAnimationFrame(f);
})"""

SET_STATE = """(s) => { document.body.dataset.emmaState = s;
  window.__mouth && clearInterval(window.__mouth);
  if (s === 'speaking') { const w = document.querySelector('.mascot-wrap'); let t = 0;
    window.__mouth = setInterval(() => { t += .2; const lv = (Math.sin(t) + 1) / 2;
      w.dispatchEvent(new CustomEvent('emma-mouth-level', {detail: {level: lv}}));
      w.style.setProperty('--mouth-scale', lv.toFixed(3)); }, 33); } }"""


def _server(current: dict) -> http.server.ThreadingHTTPServer:
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(ROOT), **k)

        def log_message(self, *a):
            pass

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/preview":
                data = current["page"].read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            return super().do_GET()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pages", nargs="*", type=pathlib.Path, default=[ROOT / "voice-preview.html"])
    ap.add_argument("--cpu", type=int, default=4, help="CPU slowdown (default 4)")
    ap.add_argument("--webgl", action="store_true", help="keep WebGL on (see the docstring)")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    current = {"page": args.pages[0].resolve()}
    srv = _server(current)
    port = srv.server_address[1]
    launch = [] if args.webgl else ["--disable-webgl", "--disable-3d-apis"]
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=launch)
            for page_path in args.pages:
                current["page"] = page_path.resolve()
                page = browser.new_page(viewport={"width": 1080, "height": 1920}, device_scale_factor=1)
                cdp = page.context.new_cdp_session(page)
                page.goto(f"http://127.0.0.1:{port}/preview?app=1")
                page.wait_for_timeout(7000)
                cdp.send("Emulation.setCPUThrottlingRate", {"rate": args.cpu})
                row = {"page": page_path.name}
                for state in ("listening", "speaking"):
                    page.evaluate(SET_STATE, state)
                    page.wait_for_timeout(1000)
                    row[state] = page.evaluate(PROBE, 6000)
                print(json.dumps(row, ensure_ascii=False), flush=True)
                page.close()
            browser.close()
    finally:
        srv.shutdown()


if __name__ == "__main__":
    main()
