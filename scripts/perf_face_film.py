#!/usr/bin/env python3
"""The ceremony's film on the robot's own chest screen: does it come up, in sync, and how smooth is it?

    python scripts/perf_face_film.py --shots .tmp/face-film
    python scripts/perf_face_film.py --cues s03_l03,s02_l03,s04_l11 --cpu 4
    python scripts/perf_face_film.py --no-autoplay-flag      # what the frame's AudioContext does without the WebView's setting

Owner 24 Nov 2026 (app/face_film.py, CEREMONY_FACE_FILM): "อยากจะให้ตอนพูด จอหุ่น กับจอคอมนี้ขึ้นพรีเซ้นต์โมชั่นเหมือนกันเลย".

The REAL call server (app.main, the switch on) is served on 127.0.0.1 and the face page is opened in headless
Chromium at the robot's 1080x1920 with the CPU slowed (default x4, like scripts/perf_robot_face.py). Its call socket
is faked (nothing reaches a model), and the call's `ready` carries a real one-time ticket -- so the frame enters
by the door the robot uses (/mirror?film=1&ticket=..., a cookie, /ws/mirror, the acts, the pictures). The film's
lines are played through app/face_mirror.py exactly as scripts/preview_ceremony_show.py does (the preview takes of her
English lines, the slides file as it is, drafts included -- a look on this PC, not the stage).

Measured per line, 1 s into her voice, 6 s: frames per second of the face page (the top document) and of the
frame, from requestAnimationFrame. The first row is today's face page (the same call, no ticket = no frame).

WebGL is off (like perf_robot_face.py: headless draws Emma's body in software and eats the throttled CPU), so the
mascot is not drawn in any row -- what this measures is the page around her and the film. The robot's own WebView
(RK3588, Chrome 124) is NOT measured here and is slower than a throttled desktop in ways nobody has quantified:
watch it at the rehearsal.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["CEREMONY_FACE_FILM"] = "true"                 # (read when app.main is imported: the routes exist)
os.environ["MIRROR_TOKEN"] = "perf-face-film-token-0123456789"
os.environ.setdefault("GEMINI_API_KEY", "perf-not-a-key")

PROBE = """(ms) => new Promise(done => {
  const iv = []; let last = performance.now(); let n = 0; const end = last + ms;
  function f(t) { iv.push(t - last); last = t; n++; if (t < end) requestAnimationFrame(f); else {
    iv.sort((a, b) => a - b); done({frames: n, fps: +(n / (ms / 1000)).toFixed(1),
      p95: +iv[Math.floor(iv.length * .95)].toFixed(1), long: iv.filter(x => x > 50).length}); } }
  requestAnimationFrame(f);
})"""

# The call's socket is faked; every other socket (the frame's /ws/mirror) is the real one.
FAKE_SOCKET = r"""
(() => {
  const RealWS = window.WebSocket;
  class FakeWS {
    constructor(url) {
      this.url = String(url); this.readyState = 0; this.binaryType = 'blob';
      window.__callWS = this;
      setTimeout(() => { this.readyState = 1; if (this.onopen) this.onopen({}); }, 20);
    }
    send() {}
    close() { this.readyState = 3; }
    addEventListener() {}
  }
  function WS(url, protocols) {
    if (/\/ws\?/.test(String(url))) return new FakeWS(url);
    return protocols ? new RealWS(url, protocols) : new RealWS(url);
  }
  WS.CONNECTING = 0; WS.OPEN = 1; WS.CLOSING = 2; WS.CLOSED = 3;
  window.WebSocket = WS;
  window.__emit = (obj) => { const w = window.__callWS; if (w && w.onmessage) w.onmessage({ data: JSON.stringify(obj) }); };
})();
"""

FRAME_STATE = """() => ({
  show: document.documentElement.classList.contains('mirror-show'),
  film: !!window.CeremonyShow, ceremony: window.CeremonyShow ? window.CeremonyShow.state() : null,
  mirror: window.EmmaMirror ? window.EmmaMirror.state() : null,
  sub: (document.querySelector('.mirror-sub-text') || {}).textContent || null,
  mascotCanvas: !!document.querySelector('canvas.mascot-3d.emma-3d-ready') })"""


def _preview():
    spec = importlib.util.spec_from_file_location("preview_ceremony_show", ROOT / "scripts" / "preview_ceremony_show.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def run(args) -> int:
    import uvicorn
    from playwright.async_api import async_playwright

    from app import face_film, face_mirror
    from app import main as main_module
    from app.config import settings

    settings.ws_token = ""                                  # (no token: the page's own routes and the fake call)
    settings.ceremony_face_film = True
    settings.mirror_token = os.environ["MIRROR_TOKEN"]
    settings.stories_enabled = True
    pv = _preview()
    todo = pv.lines([c for c in args.cues.split(",") if c])
    clips = pv._clips()
    todo = [c for c in todo if c["id"] in clips]
    if not todo:
        print("no playable cue")
        return 2
    server = uvicorn.Server(uvicorn.Config(main_module.app, host="127.0.0.1", port=args.port, lifespan="off",
                                           log_level="warning", access_log=False))
    serving = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started or serving.done():
            break
        await asyncio.sleep(0.05)
    if not server.started:
        print("could not listen on 127.0.0.1:%d" % args.port)
        return 2
    shots = Path(args.shots)
    shots.mkdir(parents=True, exist_ok=True)
    rows = []
    async with async_playwright() as p:
        flags = ["--disable-webgl", "--disable-3d-apis", "--use-fake-ui-for-media-stream",
                 "--use-fake-device-for-media-stream"]
        if not args.no_autoplay_flag:
            flags.append("--autoplay-policy=no-user-gesture-required")     # = the WebView's setMediaPlaybackRequiresUserGesture(false)
        browser = await p.chromium.launch(headless=True, args=flags)
        for mode in args.modes.split(","):
            with_film = mode != "base"
            ctx = await browser.new_context(viewport={"width": 1080, "height": 1920}, device_scale_factor=1,
                                            permissions=["microphone"])
            await ctx.add_init_script(FAKE_SOCKET)
            page = await ctx.new_page()
            page.on("pageerror", lambda e: print("  [page error] %s" % e) if "WebGL" not in str(e) else None)
            cdp = await ctx.new_cdp_session(page)
            await page.goto("http://127.0.0.1:%d/preview?app=1&ceremony=1" % args.port)
            await page.wait_for_timeout(3000)
            await page.click(".control.mic")
            await page.wait_for_function("() => !!window.__callWS && window.__callWS.readyState === 1", timeout=8000)
            face_mirror.reset()
            face_mirror.call_started()
            face_mirror.publish_json({"type": "ready", "output_rate": pv.RATE, "profile": "ceremony"})
            ready = {"type": "ready", "provider": "gemini", "profile": "ceremony", "voice": "Kore", "input_rate": 16000,
                     "output_rate": pv.RATE, "half_duplex": True, "barge_in": False, "hold_speech": False}
            if with_film:
                ready["film_ticket"] = face_film.ticket()
                if mode == "half":
                    ready["film_scale"] = 0.5          # = CEREMONY_FACE_FILM_SCALE=0.5 (session.py puts it in `ready`)
            await page.evaluate("(r) => window.__emit(r)", ready)
            await page.wait_for_timeout(2500)
            frame = next((f for f in page.frames if "/mirror" in f.url), None)
            row = {"mode": mode, "film": with_film, "frame_found": frame is not None}
            if with_film:
                row["classes"] = await page.evaluate("() => document.documentElement.className")
                row["frame_box"] = await page.evaluate(
                    "() => { const e = document.querySelector('iframe.face-film'); const r = e.getBoundingClientRect();"
                    " return {box: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],"
                    " layout: [e.offsetWidth, e.offsetHeight], inner: [e.contentWindow.innerWidth, e.contentWindow.innerHeight]}; }")
                row["hall_screens_counted"] = face_mirror.screens()
            await cdp.send("Emulation.setCPUThrottlingRate", {"rate": args.cpu})
            row["lines"] = []
            for cue in todo:
                pcm = clips[cue["id"]]
                task = asyncio.create_task(pv.play(cue, pcm, lead_s=0.4, tail_s=0.3))
                await asyncio.sleep(1.4)
                probes = [page.evaluate(PROBE, 6000)]
                if frame is not None:
                    probes.append(frame.evaluate(PROBE, 6000))
                got = await asyncio.gather(*probes)
                line = {"cue": cue["id"], "face": got[0]}
                if frame is not None:
                    line["frame"] = got[1]
                    line["frame_state"] = await frame.evaluate(FRAME_STATE)
                    if with_film:
                        await page.screenshot(path=str(shots / ("layout_%s.png" % cue["id"])))
                row["lines"].append(line)
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                face_mirror.publish_json({"type": "interrupted"})
                face_mirror.subtitle_cut()
                await asyncio.sleep(0.8)
            # the call ends: the frame goes, the face is as it was
            await page.evaluate("() => { const s = window.__callWS; if (s && s.onclose) { s.readyState = 3; s.onclose({}); } }")
            await page.wait_for_timeout(500)
            row["after_call"] = await page.evaluate(
                "() => ({frames: document.querySelectorAll('iframe.face-film').length,"
                " on: document.documentElement.classList.contains('face-film-on')})")
            rows.append(row)
            await ctx.close()
        await browser.close()
    server.should_exit = True
    await serving
    print(json.dumps(rows, ensure_ascii=False, indent=1))
    (shots / "perf_face_film.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cues", default="s03_l03,s02_l03,s04_l11", help="cue ids (default: location_site, emma_console, emma_dive)")
    ap.add_argument("--cpu", type=int, default=4)
    ap.add_argument("--port", type=int, default=8793)
    ap.add_argument("--shots", default=str(ROOT / ".tmp" / "face-film"))
    ap.add_argument("--modes", default="base,film", help="base = today's face (no frame), film, half = CEREMONY_FACE_FILM_SCALE 0.5")
    ap.add_argument("--no-autoplay-flag", action="store_true")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
