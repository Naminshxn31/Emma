#!/usr/bin/env python3
"""Play the robot face's stories without Gemini: video, key frames, sync, fps.

    python scripts/record_stories.py --fixture            # one drawn story per template
    python scripts/record_stories.py                      # the project's catalog (draft or not)
    python scripts/record_stories.py --only room_types,sky_pool_where --lang en
    python scripts/record_stories.py --fixture --interrupt-at 3.0    # the end state on barge-in
    python scripts/record_stories.py --fixture --no-video            # fps without the recorder's cost
    python scripts/record_stories.py --fixture --reduced-motion

What it does, per story: serves the REAL robot face page (client/voice-preview.html
at /preview?app=1, the real /assets, the real /story-asset bytes by hash) from a
small local server, replaces the page's WebSocket with a fake server, presses
the mic, sends `ready`, then plays one answer the way Gemini delivers it:
speech_started, the present_story tool_result (the page payload app/stories.py
builds), then her audio in 100 ms PCM chunks generated 4x faster than real time
with her transcript in pieces (--piece-chars) at a realistic pace (--cps,
normalised characters per second; Thai ~14), then turn_complete. The pieces
come in Gemini's order by default (--order lead): each piece just BEFORE its
own audio -- the live runs of 12 Oct opened 91/91 answers with text and never
sent two pieces without audio between them, ~1.4 s of audio per piece.
--order trail puts each piece --text-lag seconds behind its audio instead. Headless Chromium at the robot's 1080x1920
portrait with the CPU slowed (--cpu, default 4) and WebGL off (no Emma on the
recording since the SVG puppet was deleted on 14 Oct -- see scripts/perf_robot_face.py
for why WebGL is off).

Out (default .tmp/story-recordings/<time>/): <story>.webm, <story>/beat_<k>.png
(each beat, 0.8 s after it fired), <story>/end.png, report.json with, per beat,
when her line started in the audio, when its cue word was spoken, and when the
page fired the beat (played-audio seconds) -- the sync check -- plus fps / p95 /
long frames over the story, page errors, and whether a tap reached the socket.

Approval does not matter here (a draft is exactly what needs watching before a
person approves it); nothing here talks to Gemini, the robot or the network.
Needs playwright.
"""
from __future__ import annotations

import argparse
import http.server
import json
import math
import re
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import stories  # noqa: E402

CLIENT = ROOT / "client"
EXPECTED = re.compile(r"WebGL|webgl", re.IGNORECASE)
RATE = 24000
CHUNK_MS = 100
GEN_SPEED = 4.0          # the model generates audio ~4x faster than it plays

FAKE_SOCKET = r"""
(() => {
  class FakeWS {
    constructor(url) {
      this.url = String(url); this.readyState = 0; this.binaryType = 'blob';
      this.onopen = null; this.onmessage = null; this.onclose = null; this.onerror = null;
      if (/\/ws\?/.test(this.url)) window.__callWS = this;
      setTimeout(() => { this.readyState = 1; if (this.onopen) this.onopen({}); }, 20);
    }
    send(data) {
      (window.__sent = window.__sent || []).push(typeof data === 'string' ? data : '[binary]');
    }
    close() { this.readyState = 3; }
    addEventListener() {}
  }
  FakeWS.CONNECTING = 0; FakeWS.OPEN = 1; FakeWS.CLOSING = 2; FakeWS.CLOSED = 3;
  window.WebSocket = FakeWS;
  // the server side of the call: JSON frames and generated PCM16 audio
  window.__emit = (obj) => { const w = window.__callWS; if (w && w.onmessage) w.onmessage({ data: JSON.stringify(obj) }); };
  window.__audio = (ms, amp, seed) => {
    const n = Math.round(24000 * ms / 1000), a = new Int16Array(n);
    for (let i = 0; i < n; i++) {
      const t = (seed * n + i) / 24000;
      const env = amp * (0.55 + 0.45 * Math.sin(2 * Math.PI * 4.2 * t));
      a[i] = Math.round(32767 * env * Math.sin(2 * Math.PI * 190 * t));
    }
    const w = window.__callWS; if (w && w.onmessage) w.onmessage({ data: a.buffer });
  };
  // the timeline runs here, on the page's clock: [ms, kind, payload]
  window.__play = (events) => {
    const t0 = performance.now();
    window.__timers = events.map(([ms, kind, p]) => setTimeout(() => {
      if (kind === 'json') window.__emit(p);
      else if (kind === 'audio') window.__audio(p.ms, p.amp, p.seed);
    }, ms));
    return t0;
  };
  // the guest talks over her: the server stops generating that answer
  window.__stop = () => { (window.__timers || []).forEach(clearTimeout); window.__timers = []; };
  // beats as they fire, with the played-audio second they fired at
  window.__beats = [];
  window.__watch = () => {
    let last = 0;
    clearInterval(window.__watchId);
    window.__watchId = setInterval(() => {
      const s = window.EmmaStory && window.EmmaStory.state();
      if (!s) return;
      while (last < s.fired) { window.__beats.push({ beat: last, played: s.played, at: performance.now() }); last++; }
      window.__state = s;
    }, 25);
  };
  // frames over the story
  window.__fpsStart = () => {
    const iv = [], at = []; let last = performance.now(); window.__fpsRun = true;
    function f(t) { iv.push(t - last); at.push(t); last = t; if (window.__fpsRun) requestAnimationFrame(f); }
    requestAnimationFrame(f);
    // long tasks (a story's construction is one): their durations
    window.__long = [];
    try {
      const po = new PerformanceObserver(l => l.getEntries().forEach(e => window.__long.push(+e.duration.toFixed(1))));
      po.observe({ entryTypes: ['longtask'] });
    } catch (e) {}
    window.__fpsStop = () => {
      window.__fpsRun = false;
      const total = iv.reduce((a, b) => a + b, 0), s = iv.slice().sort((a, b) => a - b);
      // the lowest frame rate over any one-second window (a stall is a few
      // long frames, which an average over the whole story hides)
      let lo = Infinity, j = 0, acc = 0, t = 0;
      const ends = iv.map(x => (t += x));
      for (let k = 0; k < ends.length; k++) {
        while (ends[k] - ends[j] > 1000) j++;
        if (ends[k] >= 1000) lo = Math.min(lo, k - j + 1);
      }
      const med = s[Math.floor(s.length * .5)] || 0;
      // the frame rate while something MOVES: the 1.2 s after each beat fired
      // (the whole-story numbers are mostly idle frames, review 12 Oct)
      const beatFps = (window.__beats || []).map(b => +(at.filter(t => t >= b.at && t < b.at + 1200).length / 1.2).toFixed(1));
      return { frames: iv.length, fps: +(iv.length / (total / 1000)).toFixed(1),
               beat_fps: beatFps, beat_fps_min: beatFps.length ? Math.min(...beatFps) : null,
               long_tasks: (window.__long || []).slice(),
               fps_p50: med ? +(1000 / med).toFixed(1) : null, fps_min: isFinite(lo) ? lo : null,
               p95: +(s[Math.floor(s.length * .95)] || 0).toFixed(1), long: iv.filter(x => x > 50).length,
               worst: +(s[s.length - 1] || 0).toFixed(1) };
    };
  };
})();
"""


def _norm(text: str) -> str:
    return stories._NORM.sub("", (text or "").lower())


class _Server:
    def __init__(self, catalog: dict, assets: Path):
        catalog_ref, assets_ref = catalog, assets

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=str(CLIENT), **k)

            def log_message(self, *a):
                pass

            def _send(self, body: bytes, ctype: str, code: int = 200):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = self.path.split("?")[0]
                if path == "/preview":
                    return self._send((CLIENT / "voice-preview.html").read_bytes(), "text/html; charset=utf-8")
                if path == "/voices":
                    return self._send(json.dumps({"voices": [{"id": "Kore", "name": "Kore"}],
                                                  "default": "Kore"}).encode(), "application/json")
                if path == "/health":
                    return self._send(json.dumps({
                        "ok": True, "provider": "gemini", "api_key_configured": True,
                        "wake": {"enabled": False, "ready": False, "standby": False},
                        "auto_connect": False, "transcript_keep_min": 5,
                        "mic": {"boost": 1, "boost_call": 1, "noise_suppression": False, "compressor": False,
                                "agc": False, "listen": {"split": False, "far_profiles": [], "default_far": False}},
                        "robot": {}}).encode(), "application/json")
                m = re.fullmatch(r"/story-asset/([0-9a-f]{64})", path)
                if m:
                    found = stories.asset_body(m.group(1), catalog_ref, require_approval=False, base=assets_ref)
                    if found is None:
                        return self._send(b"", "text/plain", 404)
                    return self._send(found[0], found[1])
                return super().do_GET()

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.port = self.httpd.server_address[1]

    def close(self):
        self.httpd.shutdown()


def timeline(story: dict, payload: dict, lang: str, cps: float, text_lag: float,
             order: str = "lead", piece_chars: int = 18) -> tuple[list, list[dict]]:
    """(page events, per-line timing in played-audio seconds)."""
    events: list = []
    t = 0.0
    events.append([0, "json", {"type": "speech_started"}])
    events.append([250, "json", {"type": "heard_end"}])
    # what the guest said (the ASR caption): must never be drawn on this screen
    events.append([600, "json", {"type": "user_transcript", "text": "GUEST-WORDS-NEVER 0812345678",
                                 "utterance_id": "u1"}])
    events.append([900, "json", {"type": "tool_call", "name": "present_story"}])
    result = {"ok": True, "screen": "story", "topic": story["id"], "story": payload,
              "say": [{"th": l["th"], "en": l["en"]} for l in story["lines"]]}
    if payload is not None:
        events.append([1000, "json", {"type": "tool_result", "name": "present_story", "result": result}])
    send = 1300.0                 # ms on the page clock: when the first chunk goes out
    played = 0.0                  # seconds of her answer before this line
    lines = []
    seed = 0
    gap_s = 0.25
    for k, line in enumerate(story["lines"]):
        text = line.get(lang) or line["en"]
        n = max(1, len(_norm(text)))
        dur = n / cps
        # where the cue starts in this line (what the beat should wait for)
        cues = [c for f in ("cue_th", "cue_en", "cue_zh") for c in (line.get(f) or [])]
        nt = _norm(text)
        pos = min([nt.find(_norm(c)) for c in cues if _norm(c) and nt.find(_norm(c)) >= 0] or [0])
        lines.append({"line": k, "start": round(played, 3), "cue": round(played + dur * pos / n, 3),
                      "dur": round(dur, 3), "text": text})
        chunks = max(1, math.ceil(dur * 1000 / CHUNK_MS))
        size = max(1, int(piece_chars))
        pieces = [text[i:i + size] for i in range(0, len(text), size)] or [text]
        if k:
            pieces[0] = " " + pieces[0]
        line_send_start = send
        for c in range(chunks):
            events.append([round(send), "audio", {"ms": CHUNK_MS, "amp": 0.09, "seed": seed}])
            seed += 1
            send += CHUNK_MS / GEN_SPEED
        done = 0
        for j, piece in enumerate(pieces):
            start_frac = done / max(1, len(_norm(text)))
            done += len(_norm(piece))
            frac_end = done / max(1, len(_norm(text)))
            if order == "lead":
                # just before the chunk its audio starts in (sorted after: ties
                # go to the earlier event, so the text is 1 ms ahead)
                first = min(chunks - 1, int(start_frac * chunks))
                at = line_send_start + first * CHUNK_MS / GEN_SPEED - 1
            else:
                at = line_send_start + frac_end * chunks * CHUNK_MS / GEN_SPEED + text_lag * 1000
            events.append([round(max(1000, at)), "json", {"type": "assistant_transcript", "text": piece}])
        played += chunks * CHUNK_MS / 1000
        gap_chunks = int(gap_s * 1000 / CHUNK_MS)
        for c in range(gap_chunks):
            events.append([round(send), "audio", {"ms": CHUNK_MS, "amp": 0.0, "seed": seed}])
            seed += 1
            send += CHUNK_MS / GEN_SPEED
        played += gap_chunks * CHUNK_MS / 1000
    events.append([round(send + 200), "json", {"type": "turn_complete"}])
    events.sort(key=lambda e: e[0])          # stable: equal times keep their order
    return events, lines


def _tap_next(story: dict, tap_id) -> str | None:
    for tap in story.get("taps") or []:
        if isinstance(tap, dict) and tap.get("id") == tap_id:
            return tap.get("next_story")
    return None


def check_picture_taps(page, payload: dict, sdir: Path) -> dict:
    """Owner 16 Oct: "กดรูปบนจอ ... ขยายภาพนั้นและอธิบายเพิ่มเติม". On the overview,
    every picture the guest can SEE gets a real mouse click at its centre (the
    browser's own hit test, as a finger): the large view must open, the page
    must send the right id (a row's own story tap, else the picture's "more"
    tap), a "more" tap must keep the story on screen through turn_complete
    (the answer it asks for owns no new picture), and a tap on the large view
    must close it. One screenshot of the first large view."""
    zoom = {z["src"]: z for z in payload.get("zoom") or []}
    targets = page.evaluate("""(srcs) => {
        const out = [], seen = new Set();
        const visible = (n) => { for (; n && !n.classList.contains('es-root'); n = n.parentElement) {
            const cs = getComputedStyle(n);
            if (cs.visibility === 'hidden' || cs.display === 'none' || parseFloat(cs.opacity) < 0.3) return false; }
            return true; };
        for (const im of document.querySelectorAll('.es-root img.es-zoomable')) {
            const src = new URL(im.src).pathname;
            if (!srcs.includes(src) || seen.has(src) || !visible(im)) continue;
            const r = im.getBoundingClientRect();
            if (r.width < 8 || r.height < 8) continue;
            const x = r.left + r.width / 2, y = r.top + r.height / 2;
            const top = document.elementsFromPoint(x, y).find((n) => n.classList && n.classList.contains('es-zoomable'));
            if (top !== im) continue;                    // covered by another picture here
            const bound = im.closest('[data-tap]');
            seen.add(src);
            out.push({src, x, y, bound: bound ? bound.getAttribute('data-tap') : null});
        }
        return out; }""", list(zoom))
    results = []
    for k, t in enumerate(targets):
        page.evaluate("() => { window.__sent = []; }")
        page.mouse.click(t["x"], t["y"])
        page.wait_for_timeout(450)
        opened = page.evaluate("() => !!document.querySelector('.es-root .es-zoomview.on')")
        if k == 0:
            page.screenshot(path=str(sdir / "zoom.png"))
        sent = [json.loads(s) for s in page.evaluate("() => window.__sent || []") if s.startswith("{")]
        asks = [s.get("id") for s in sent if s.get("type") == "quick_ask"]
        want = t["bound"] or zoom[t["src"]]["tap"]
        kept = None
        if not t["bound"]:
            page.evaluate("() => window.__emit({type: 'turn_complete'})")
            page.wait_for_timeout(200)
            kept = page.evaluate("() => !!document.querySelector('.es-root .es-zoomview.on')")
        page.mouse.click(t["x"], t["y"])                  # on the large view: closes it
        page.wait_for_timeout(250)
        closed = page.evaluate("() => !document.querySelector('.es-root .es-zoomview')")
        results.append({"src": t["src"], "label": zoom[t["src"]]["label_en"], "bound": t["bound"],
                        "opened": opened, "sent": asks, "ok_id": asks == [want], "kept": kept, "closed": closed})
    every = len(zoom)
    good = [r for r in results if r["opened"] and r["ok_id"] and r["closed"] and r["kept"] is not False]
    return {"pictures": every, "visible": len(results), "ok": len(good), "results": results}


def run_story(browser, port: int, story: dict, payload: dict, out: Path, args) -> dict:
    sdir = out / story["id"]
    sdir.mkdir(parents=True, exist_ok=True)
    kwargs = {"viewport": {"width": 1080, "height": 1920}, "device_scale_factor": 1,
              "permissions": ["microphone"]}
    if args.reduced_motion:
        kwargs["reduced_motion"] = "reduce"
    if not args.no_video:
        kwargs["record_video_dir"] = str(out / "_video")
        kwargs["record_video_size"] = {"width": 540, "height": 960}
    ctx = browser.new_context(**kwargs)
    ctx.add_init_script(FAKE_SOCKET)
    page = ctx.new_page()
    errors: list[str] = []

    def note(text: str) -> None:
        # WebGL is off on purpose (see the docstring): the 3D Emma's own
        # "could not create a context" lines are expected, not the story's.
        if not EXPECTED.search(text):
            errors.append(text)

    page.on("pageerror", lambda e: note("pageerror: %s" % e))
    page.on("console", lambda m: note("console.%s: %s" % (m.type, m.text)) if m.type == "error" else None)
    page.goto("http://127.0.0.1:%d/preview?app=1&cue=0" % port)
    page.wait_for_function("() => document.querySelector('.control.mic')", timeout=15000)
    page.wait_for_timeout(1500)
    page.click(".control.mic")
    page.wait_for_function("() => !!window.__callWS && window.__callWS.readyState === 1", timeout=5000)
    page.evaluate("""() => window.__emit({type: 'ready', provider: 'gemini', profile: 'condo', voice: 'Kore',
        input_rate: 16000, output_rate: 24000, session_limit_min: 15, auto_resumes: true,
        half_duplex: true, barge_in: false, hold_speech: false, hold_floor: 0.04,
        quick_asks: {brand: 'Embassy World', langs: [], hint: {}, items: []}})""")
    page.wait_for_timeout(800)
    cdp = ctx.new_cdp_session(page)
    if args.cpu > 1:
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": args.cpu})
    events, lines = timeline(story, None if args.baseline else payload, args.lang, args.cps, args.text_lag,
                             args.order, args.piece_chars)
    page.evaluate("() => { window.__watch(); window.__fpsStart(); }")
    page.evaluate("(ev) => window.__play(ev)", events)
    total_audio = lines[-1]["start"] + lines[-1]["dur"] + 0.25 if lines else 0
    shot = {}
    interrupted = None
    deadline = time.time() + 1.3 + total_audio + 8 + (4 if args.cpu > 1 else 0)
    while time.time() < deadline:
        beats = page.evaluate("() => window.__beats.length")
        state = page.evaluate("() => window.EmmaStory && window.EmmaStory.state()") or {}
        for k in range(beats):
            if k not in shot:
                shot[k] = time.time() + 0.8
        for k, due in list(shot.items()):
            if due and time.time() >= due:
                page.screenshot(path=str(sdir / ("beat_%d.png" % k)))
                shot[k] = 0
        if (args.interrupt_at is not None and interrupted is None and state.get("played") is not None
                and state["played"] >= args.interrupt_at):
            page.evaluate("() => { window.__stop(); window.__emit({type: 'speech_started'});"
                          " window.__emit({type: 'interrupted'}); }")
            page.wait_for_timeout(300)
            page.screenshot(path=str(sdir / "interrupted.png"))
            interrupted = page.evaluate("() => window.EmmaStory.state()")
        if state.get("ended") and all(v == 0 for v in shot.values()) and len(shot) >= state.get("beats", 0):
            break
        if args.baseline and time.time() > deadline - 6:
            break
        time.sleep(0.05)
    page.wait_for_timeout(2500)          # the overview's own motion (a list growing) has landed
    fps = page.evaluate("() => window.__fpsStop()")
    if args.cpu > 1:
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": 1})
    page.screenshot(path=str(sdir / "end.png"))
    beats = page.evaluate("() => window.__beats")
    state = page.evaluate("() => window.EmmaStory && window.EmmaStory.state()")
    # a tap on the overview must reach the socket as a quick_ask id, nothing more
    tap = None
    tap_next = None
    tap_ids = [t["id"] for t in payload.get("taps") or []]
    if tap_ids:
        # a tap that leads to another story first (the tap-through), else any
        by_story = [t["id"] for t in payload.get("taps") or []
                    if (getattr(args, "by_id", None) or {}).get(_tap_next(story, t["id"]))]
        ok = page.evaluate("""(prefer) => {
            let b = null;
            for (const id of prefer) { b = document.querySelector('.es-root .es-tap.on[data-tap="' + id + '"]'); if (b) break; }
            b = b || document.querySelector('.es-root .es-tap.on');
            if (!b) return null; b.click(); return b.getAttribute('data-tap'); }""", by_story)
        page.wait_for_timeout(300)
        sent = [json.loads(s) for s in page.evaluate("() => window.__sent || []") if s.startswith("{")]
        asks = [s for s in sent if s.get("type") == "quick_ask"]
        tap = {"clicked": bool(ok), "sent": asks[-1] if asks else None,
               "ok": bool(asks) and asks[-1].get("id") in tap_ids and set(asks[-1]) <= {"type", "id", "lang"}}
        nxt = (getattr(args, "by_id", None) or {}).get(_tap_next(story, ok)) if ok else None
        if tap["ok"] and nxt is not None and getattr(args, "tap_through", False):
            # What the server does next: the model calls present_story(next)
            # and answers -- no guest voice (a tap), the same page, the old
            # story must be gone and the new one playing alone.
            npayload = stories.page_payload(nxt, getattr(args, "catalog", None) or {})
            nev, _ = timeline(nxt, npayload, args.lang, args.cps, args.text_lag, args.order, args.piece_chars)
            nev = [e for e in nev if not (e[1] == "json" and e[2].get("type") in (
                "speech_started", "heard_end", "user_transcript"))]
            page.evaluate("(ev) => window.__play(ev)", nev)
            page.wait_for_timeout(4000)
            page.screenshot(path=str(sdir / "tap_next.png"))
            st = page.evaluate("() => window.EmmaStory && window.EmmaStory.state()") or {}
            tap_next = {"tap": ok, "next_story": nxt["id"], "playing": st.get("id"),
                        "fired": st.get("fired"),
                        "stories_on_screen": page.evaluate("() => document.querySelectorAll('.es-root').length")}
        elif ok:
            call = next((t.get("tool") for t in story.get("taps") or [] if t.get("id") == ok), None)
            if call:
                tap_next = {"tap": ok, "tool": call.get("name"), "args": call.get("args")}
    pictures = check_picture_taps(page, payload, sdir) if getattr(args, "tap_pictures", False) else None
    guest_text_on_screen = page.evaluate("() => document.body.innerText.indexOf('GUEST-WORDS-NEVER') >= 0")
    ctx.close()
    video = None
    if not args.no_video:
        try:
            vid = page.video.path()
            video = out / ("%s.webm" % story["id"])
            Path(vid).replace(video)
        except Exception:
            video = None
    sync = []
    for b in beats or []:
        k = b["beat"]
        if k < len(lines):
            expect = lines[k]["cue"] if k else 0.0
            row = {"beat": k, "line_start": lines[k]["start"], "cue_spoken": expect,
                   "fired_at": round(b["played"] or 0, 3), "late_by": round((b["played"] or 0) - expect, 3)}
            if interrupted is not None and args.interrupt_at is not None and expect > args.interrupt_at:
                row["by_interrupt"] = True           # the end state, not a sync miss
            sync.append(row)
    return {"id": story["id"], "template": story["template"], "lines": len(lines),
            "audio_s": round(total_audio, 2), "sync": sync, "fps": fps, "tap": tap, "tap_next": tap_next,
            "ended": bool(state and state.get("ended")), "interrupted": interrupted,
            "errors": errors, "guest_text_on_screen": guest_text_on_screen, "pictures": pictures,
            "video": str(video) if video else None}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fixture", action="store_true", help="the drawn fixture stories (one per template)")
    ap.add_argument("--root", type=Path, default=None, help="a stories folder (default: the project's)")
    ap.add_argument("--only", default="", help="comma-separated story ids")
    ap.add_argument("--lang", default="th", choices=("th", "en"))
    ap.add_argument("--cps", type=float, default=14.0, help="normalised characters per second")
    ap.add_argument("--order", default="lead", choices=("lead", "trail"),
                    help="transcript pieces just before their audio (Gemini's order) or behind it")
    ap.add_argument("--piece-chars", type=int, default=18, help="characters per transcript piece")
    ap.add_argument("--text-lag", type=float, default=0.15,
                    help="--order trail: seconds the transcript trails its audio")
    ap.add_argument("--cpu", type=int, default=4)
    ap.add_argument("--interrupt-at", type=float, default=None, help="guest speaks at this played second")
    ap.add_argument("--reduced-motion", action="store_true")
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--baseline", action="store_true",
                    help="the same answers WITHOUT the story (fps of the page around it)")
    ap.add_argument("--tap-through", action="store_true",
                    help="after the overview, tap a story tap and play the story it leads to (tap_next.png)")
    ap.add_argument("--tap-pictures", action="store_true",
                    help="after the overview, tap every picture the guest can see (a real click): it must open "
                         "large, send its tap id, keep the screen for a 'more' answer, and close on a tap")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    out = args.out or (ROOT / ".tmp" / "story-recordings" / time.strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True, exist_ok=True)
    if args.fixture:
        from scripts.story_fixture import build

        root = build(out / "fixture", size="screen")
    else:
        root = (args.root or stories.folder()).resolve()
    catalog = json.loads((root / stories.CATALOG_NAME).read_text(encoding="utf-8"))
    wanted = {s.strip() for s in args.only.split(",") if s.strip()}
    assets = catalog.get("assets") or {}
    todo = []
    for story in catalog.get("stories") or []:
        if wanted and story.get("id") not in wanted:
            continue
        issues = stories.problems(story, assets)
        for ref in stories.asset_refs(story.get("params") or {}):
            why = stories.asset_problem(ref, assets.get(ref), root / "assets")
            if why:
                issues.append("asset %s: %s" % (ref, why))
        if issues:
            print("skip %s: %s" % (story.get("id"), "; ".join(issues[:3])))
            continue
        todo.append(story)
    if not todo:
        print("no playable story")
        return 1
    args.catalog = catalog
    args.by_id = {s.get("id"): s for s in catalog.get("stories") or [] if isinstance(s, dict)}
    server = _Server(catalog, root / "assets")
    from playwright.sync_api import sync_playwright

    report = {"root": str(root), "cpu": args.cpu, "lang": args.lang, "cps": args.cps, "order": args.order,
              "piece_chars": args.piece_chars, "text_lag": args.text_lag, "video": not args.no_video,
              "stories": []}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=[
                "--autoplay-policy=no-user-gesture-required", "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream", "--disable-webgl", "--disable-3d-apis"])
            for story in todo:
                payload = stories.page_payload(story, catalog)
                row = run_story(browser, server.port, story, payload, out, args)
                report["stories"].append(row)
                late = [s["late_by"] for s in row["sync"][1:] if not s.get("by_interrupt")]
                lt = row["fps"].get("long_tasks") or []
                print("%-26s %-9s beats %d/%d  late %s  fps %s (beats min %s) p95 %s long %s task %s  tap %s  errors %d" % (
                    row["id"], row["template"], len(row["sync"]), row["lines"],
                    ("%.2f..%.2f s" % (min(late), max(late))) if late else "-",
                    row["fps"].get("fps"), row["fps"].get("beat_fps_min"), row["fps"].get("p95"),
                    row["fps"].get("long"), max(lt) if lt else "-",
                    None if row["tap"] is None else row["tap"]["ok"], len(row["errors"])))
                pics = row.get("pictures")
                if pics is not None:
                    bad = [r["label"] or r["src"][-8:] for r in pics["results"]
                           if not (r["opened"] and r["ok_id"] and r["closed"] and r["kept"] is not False)]
                    print("%-26s pictures %d  visible %d  tap ok %d%s" % (
                        "", pics["pictures"], pics["visible"], pics["ok"], ("  FAILED " + ", ".join(bad)) if bad else ""))
            browser.close()
    finally:
        server.close()
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n%s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
