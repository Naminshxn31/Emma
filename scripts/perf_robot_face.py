"""How smooth is the robot face page? Frames per second, CPU slowed like the robot.

    python scripts/perf_robot_face.py                       # client/voice-preview.html
    python scripts/perf_robot_face.py a.html b.html         # compare variants
    python scripts/perf_robot_face.py --cpu 6 --webgl a.html
    python scripts/perf_robot_face.py --no-answers          # the two idle states only

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

Then her ANSWERS (27 Oct, the motion captions): the same page in a call over
a fake socket (nothing leaves the machine), playing long answers the way
Gemini delivers them -- 100 ms PCM chunks generated 4x faster than they play,
her transcript in 18-character pieces each just before its own audio -- in
Thai and in English, beside Emma (the bubble) and over a card on the stage
(the caption band). Timed for 6 s from 0.5 s into each answer, while the
text streams in and the caption follows her voice. Before the captions the
idle states were the only thing measured: a speaking caption never ran here.

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

# The call's server side, in the page: a WebSocket that answers nothing by
# itself, and a player for a timeline of [ms, 'json' | 'audio', payload].
FAKE_SOCKET = r"""
(() => {
  class FakeWS {
    constructor(url) {
      this.url = String(url); this.readyState = 0; this.binaryType = 'blob';
      if (/\/ws\?/.test(this.url)) window.__callWS = this;
      setTimeout(() => { this.readyState = 1; if (this.onopen) this.onopen({}); }, 20);
    }
    send() {}
    close() { this.readyState = 3; }
    addEventListener() {}
  }
  FakeWS.CONNECTING = 0; FakeWS.OPEN = 1; FakeWS.CLOSING = 2; FakeWS.CLOSED = 3;
  window.WebSocket = FakeWS;
  window.__emit = (obj) => { const w = window.__callWS; if (w && w.onmessage) w.onmessage({ data: JSON.stringify(obj) }); };
  window.__audio = (ms, seed) => {
    const n = Math.round(24000 * ms / 1000), a = new Int16Array(n);
    for (let i = 0; i < n; i++) {
      const t = (seed * n + i) / 24000;
      a[i] = Math.round(32767 * 0.09 * (0.55 + 0.45 * Math.sin(2 * Math.PI * 4.2 * t)) * Math.sin(2 * Math.PI * 190 * t));
    }
    const w = window.__callWS; if (w && w.onmessage) w.onmessage({ data: a.buffer });
  };
  window.__play = (events) => {
    window.__timers = events.map(([ms, kind, p]) => setTimeout(() => {
      if (kind === 'json') window.__emit(p); else window.__audio(p.ms, p.seed);
    }, ms));
  };
  window.__stop = () => { (window.__timers || []).forEach(clearTimeout); window.__timers = []; };
})();
"""

# Long answers in her two languages (the fixture stories' own lines, joined):
# long enough that the text is still streaming in for the whole probe.
ANSWERS = {
    "th": ("ในโครงการของเรามีสิ่งอำนวยความสะดวกมากมายถึงสามชั้นค่ะ ชั้นใต้ดินเป็นสปาและสระว่ายน้ำในร่ม "
           "ชั้น G มีฟิตเนส Biogenesis ริมสระ ห้องปั่นจักรยาน พิลาทิสสตูดิโอ บันจี้ยิมและโยคะ "
           "รวมถึง Junior World สำหรับเด็ก ส่วนชั้นสามของอาคาร A มีเลานจ์และ Sky Pool "
           "ที่พื้นสระบางส่วนเป็นกระจก มองลงไปเห็นฟิตเนสข้างล่างได้ค่ะ ลูกค้าอยากเห็นชั้นไหนเป็นพิเศษไหมคะ"),
    "en": ("We have facilities on three floors. The basement has the spa and the indoor pools. "
           "The ground floor has the Biogenesis fitness by the pool, a Spinning Class studio, a Pilates "
           "Studio, Bungee Gym and Yoga, and Junior World for the children. And level 3 of building A has "
           "the lounges and the Sky Pool, whose glass floor looks down into the fitness below. "
           "Which floor would you like to see?"),
}
RATE = {"th": 12.5, "en": 15.0}       # her measured voice, chars of text a second
CHUNK_MS, GEN_SPEED, PIECE = 100, 4.0, 18

UNIT_CARD = {"type": "tool_result", "name": "show_unit", "result": {
    "screen": "unit", "unit": {"room": "A-801", "floor": 8, "type": "1 Bedroom", "sqm": 34,
                               "status": "available", "status_th": "ว่าง"}}}


def answer_timeline(text: str, cps: float, card: bool) -> list:
    """One answer as Gemini sends it: each transcript piece just before its audio."""
    events: list = [[0, "json", {"type": "speech_started"}]]
    if card:
        events.append([150, "json", UNIT_CARD])
    send = 300.0
    seed = 0
    for k in range(0, len(text), PIECE):
        piece = text[k:k + PIECE]
        events.append([round(send) - 1, "json", {"type": "assistant_transcript", "text": piece}])
        chunks = max(1, round(len(piece) / cps * 1000 / CHUNK_MS))
        for _ in range(chunks):
            events.append([round(send), "audio", {"ms": CHUNK_MS, "seed": seed}])
            seed += 1
            send += CHUNK_MS / GEN_SPEED
    events.append([round(send + 200), "json", {"type": "turn_complete"}])
    events.sort(key=lambda e: e[0])
    return events


def _server(current: dict) -> http.server.ThreadingHTTPServer:
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(ROOT), **k)

        def log_message(self, *a):
            pass

        def _send(self, data: bytes, ctype: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/preview":
                return self._send(current["page"].read_bytes(), "text/html; charset=utf-8")
            # the call's boot handshake (the page dials only with a key "configured")
            if path == "/voices":
                return self._send(json.dumps({"voices": [{"id": "Kore", "name": "Kore"}],
                                              "default": "Kore"}).encode(), "application/json")
            if path == "/health":
                return self._send(json.dumps({
                    "ok": True, "provider": "gemini", "api_key_configured": True,
                    "mic": {"boost": 1, "boost_call": 1, "noise_suppression": False, "compressor": False,
                            "agc": False, "listen": {"split": False, "far_profiles": [], "default_far": False}},
                }).encode(), "application/json")
            return super().do_GET()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def measure_answers(page) -> dict:
    """Her answers, bubble and band, Thai and English (see the docstring)."""
    page.click(".control.mic")
    page.wait_for_function("() => !!window.__callWS && window.__callWS.readyState === 1", timeout=5000)
    page.evaluate("""() => window.__emit({type: 'ready', provider: 'gemini', profile: 'condo', voice: 'Kore',
        input_rate: 16000, output_rate: 24000, half_duplex: true, barge_in: false, hold_speech: false,
        quick_asks: {brand: 'Embassy World', langs: [], hint: {}, items: []}})""")
    page.wait_for_timeout(800)
    out = {}
    for where, card in (("bubble", False), ("band", True)):
        for lang in ("th", "en"):
            page.evaluate("(ev) => window.__play(ev)", answer_timeline(ANSWERS[lang], RATE[lang], card))
            page.wait_for_timeout(500)
            out["%s_%s" % (where, lang)] = page.evaluate(PROBE, 6000)
            # the guest cuts in: her queued audio stops, the screen clears
            page.evaluate("() => { window.__stop(); window.__emit({type: 'speech_started'});"
                          " window.__emit({type: 'interrupted'}); window.__emit({type: 'tool_result',"
                          " result: {screen: 'clear'}}); }")
            page.wait_for_timeout(700)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pages", nargs="*", type=pathlib.Path, default=[ROOT / "voice-preview.html"])
    ap.add_argument("--cpu", type=int, default=4, help="CPU slowdown (default 4)")
    ap.add_argument("--webgl", action="store_true", help="keep WebGL on (see the docstring)")
    ap.add_argument("--no-answers", action="store_true", help="the idle states only, no call")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    current = {"page": args.pages[0].resolve()}
    srv = _server(current)
    port = srv.server_address[1]
    launch = ["--autoplay-policy=no-user-gesture-required", "--use-fake-ui-for-media-stream",
              "--use-fake-device-for-media-stream"]
    if not args.webgl:
        launch += ["--disable-webgl", "--disable-3d-apis"]
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=launch)
            for page_path in args.pages:
                current["page"] = page_path.resolve()
                ctx = browser.new_context(viewport={"width": 1080, "height": 1920}, device_scale_factor=1,
                                          permissions=["microphone"])
                ctx.add_init_script(FAKE_SOCKET)
                page = ctx.new_page()
                cdp = ctx.new_cdp_session(page)
                page.goto(f"http://127.0.0.1:{port}/preview?app=1&cue=0")
                page.wait_for_timeout(7000)
                cdp.send("Emulation.setCPUThrottlingRate", {"rate": args.cpu})
                row = {"page": page_path.name}
                for state in ("listening", "speaking"):
                    page.evaluate(SET_STATE, state)
                    page.wait_for_timeout(1000)
                    row[state] = page.evaluate(PROBE, 6000)
                page.evaluate(SET_STATE, "listening")
                if not args.no_answers:
                    row.update(measure_answers(page))
                print(json.dumps(row, ensure_ascii=False), flush=True)
                ctx.close()
            browser.close()
    finally:
        srv.shutdown()


if __name__ == "__main__":
    main()
