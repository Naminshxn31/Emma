#!/usr/bin/env python3
"""Watch "ตอบสด"'s topic slides on this PC, as the event screen shows them (owner 23 Nov 2026).

    python scripts/preview_ceremony_motion.py                  # prints a URL: open it in Chrome (F11)
    python scripts/preview_ceremony_motion.py --gap 6 --loop   # the demo lines again and again (the notebook)
    python scripts/preview_ceremony_motion.py --shots .tmp/ceremony-show/motion   # headless screenshots
    python scripts/preview_ceremony_motion.py --check          # headless: the page's own state, exit 1 if wrong

The REAL room-screen listener (app/mirror_server.py) on 127.0.0.1 with a throwaway token, and the REAL
path of the day: a stand-in ceremony call is claimed (app/ceremony.py), "ตอบสด" is opened
(`adhoc_open` -> the logo loop), and each host line goes through `ceremony_motion.on_host_words` exactly
as the robot's receive loop feeds it his transcribed words; the matcher decides, face_mirror sends, the
page shows the slide settled. At the end a press of the script (`script_press`) takes it down.

Different from the day, on purpose: the slides file is a DRAFT, and the robot sends no slide until it is
signed (scripts/approve_ceremony_slides.py) -- here `ceremony_motion.DRAFTS` shows them. The host's
lines are printed on this console, never on the event screen (her live words have no subtitle).
Nothing here talks to the robot, the network or a model.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import secrets
import struct
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: (the host's words, what the event screen should do) -- every topic, a press, the logo's cases. A list =
#: the words arriving in pieces, as the transcription sends them (one utterance); "@picture" = her own
#: answer's picture on the robot face (a tool_result clear through face_mirror): the topic slide stays.
DEMO = [
    ("Emma, where exactly is the project? Is it close to Jomtien Beach?", "location"),
    ("@picture", "keep"),
    ("Wow, great.", "keep"),
    (["Which is better, the gym", " or the sea view", " from the units?"], "logo"),     # two topics: none
    ("And how many units are there? Studios, one bedroom?", "units"),
    ("Emma, did you go to school?", "logo"),                                         # about her
    ("Tell us about the swimming pool, Emma", "facilities"),
    ("How much?", "logo"),                                                           # money: never under a slide
    ("Emma, tell us about the swimming pool and the gym.", "facilities"),
    ("Emma, you're so smart! Are you nervous up here on stage?", "logo"),             # about her
    ("เอ็มม่า โครงการมีระบบสมาร์ทโฮมไหม ใช้แอปอะไร", "technology"),     # (no slide: its line settles on the logo)
    ("Emma, what's the price of a two-bedroom unit?", "logo"),
    ("ทำเลอยู่ใกล้ทะเลไหมครับ", "location"),
]


class _Call:
    """The Emma Ceremony app's call as app/ceremony.py claims it (no socket: the event screen is all)."""
    profile, provider, _robot_face_claim, _page_muted = "ceremony", None, True, False

    async def _story_picture(self, data):
        return None


def _claim():
    from app import ceremony
    from app import session as session_module

    call = _Call()
    session_module._active = call
    ceremony.claim(call, replies="cues")
    return call


def _expected(state: dict | None, want: str, cue_of: dict, beats_of: dict, before: dict | None) -> bool:
    show = (state or {}).get("show") or {}
    if want == "logo" or (want in cue_of and not cue_of[want]):     # (a topic without a slide: the logo)
        return not show.get("up")
    if want in cue_of:
        # up, settled (every beat's end state, nothing waiting) -- and THAT topic's slide: its act, every one
        # of its beats done (review 23 Nov: location and facilities are one act; "up" alone saw neither)
        want_act, want_beats = beats_of[want]
        return (bool(show.get("up")) and show.get("settled") is True and not show.get("waiting")
                and not show.get("failed") and show.get("act") == want_act
                and sorted(show.get("did") or []) == sorted(want_beats))
    old = (before or {}).get("show") or {}               # "keep": what was up, still up and unchanged
    return bool(old.get("up")) and all(show.get(k) == old.get(k) for k in ("up", "act", "did", "settled"))


async def run(args) -> int:
    import uvicorn

    from app import ceremony, ceremony_motion, ceremony_slides, face_mirror, mirror_server
    from app.config import settings

    settings.mirror_token = secrets.token_urlsafe(12)
    settings.stories_enabled = True
    settings.turn_log = False                            # a look on this PC: nothing in data/logs
    ceremony_motion.DRAFTS = True
    cue_of = {k: v["cue"] for k, v in ceremony_motion.TOPICS.items()}
    beats_of = {}
    for k, cue in cue_of.items():
        slide = ceremony_slides.for_cue(cue, lang="en", drafts=True) if cue else None
        beats_of[k] = ((slide or {}).get("act"), [b["do"] for b in (slide or {}).get("beats") or []])
    lines = DEMO
    if args.lines:
        lines = [(x["text"], x.get("expect", "keep")) for x in json.loads(Path(args.lines).read_text(encoding="utf-8"))]
    headless = bool(args.shots or args.check)
    # (timeout_graceful_shutdown: on Windows' Python 3.14 a socket the browser reset can raise inside the
    # proactor's connection_lost before the server forgets it -- uvicorn then waits for it forever)
    server = uvicorn.Server(uvicorn.Config(mirror_server.app, host="127.0.0.1", port=args.port, lifespan="off",
                                           log_level="warning", access_log=False, timeout_graceful_shutdown=2))
    serving = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started or serving.done():
            break
        await asyncio.sleep(0.05)
    if not server.started:
        serving.cancel()
        print("could not listen on 127.0.0.1:%d (taken?) -- try --port" % args.port)
        return 2
    url = "http://127.0.0.1:%d/mirror?token=%s" % (args.port, settings.mirror_token)
    browser = page = playwright = None
    failures = []
    try:
        if headless:
            from playwright.async_api import async_playwright

            playwright = await async_playwright().start()
            browser = await playwright.chromium.launch(headless=True, args=[
                "--autoplay-policy=no-user-gesture-required", "--disable-webgl", "--disable-3d-apis"])
            width, height = (int(x) for x in args.size.lower().split("x"))
            page = await browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
            await page.goto(url)
        else:
            print("\nopen this in Chrome (full screen: F11):\n\n  %s\n" % url)
        while not face_mirror.watching():
            await asyncio.sleep(0.2)
        await asyncio.sleep(1.5 if headless else 3.0)
        face_mirror.call_started()
        face_mirror.publish_json({"type": "ready", "output_rate": 24000, "profile": "ceremony"})
        await asyncio.sleep(1.5)
        call = _claim()
        out = Path(args.shots) if args.shots else None
        if out is not None:
            out.mkdir(parents=True, exist_ok=True)

        last = {"state": None}

        async def look(name: str, want: str, wait: float = 0.0) -> None:
            if page is None:
                return
            await asyncio.sleep(args.settle + wait)
            state = await page.evaluate("() => ({show: window.CeremonyShow ? window.CeremonyShow.state() : null})")
            ok = _expected(state, want, cue_of, beats_of, last["state"])
            last["state"] = state
            if not ok:
                failures.append((name, want, state))
            print("     -> %s (%s)" % ("ok" if ok else "NOT %s" % want,
                                      json.dumps({k: (state.get("show") or {}).get(k)
                                                  for k in ("up", "act", "settled", "waiting", "idle")})))
            if out is not None:
                await page.screenshot(path=str(out / ("%s.png" % name)))

        rounds = 0
        while True:
            rounds += 1
            if headless and rounds == 1:
                # the line before, still in its last words' tail on this screen when staff open ตอบสด (review
                # 24 Nov: its slide settled and stayed the whole window) -- the pool joke's, a slide of the
                # script's own: it ends, then goes
                tail = ceremony_slides.for_cue("s04_l13", lang="en", drafts=True)
                face_mirror.subtitle({"id": "s04_l13", "text": "บทก่อนหน้า ค่ะ", "seconds": 2.0, "slide": tail})
                tone = b"".join(struct.pack("<h", int(3000 * math.sin(2 * math.pi * 220 * i / 24000)))
                                for i in range(48000))                  # 2 s of her "voice"
                for k in range(0, len(tone), 4800):
                    face_mirror.publish_audio(tone[k:k + 4800])
                await asyncio.sleep(2.2)
                face_mirror.subtitle_end("s04_l13")
                await asyncio.sleep(0.3)                 # (her voice played out; the line's 1.8 s tail)
            assert ceremony.adhoc_open().get("ok"), "ตอบสด did not open"
            print("ตอบสด open: the logo loop")
            await look("00_open", "logo", 2.0 if headless and rounds == 1 else 0.0)
            for k, (text, want) in enumerate(lines, 1):
                if text == "@picture":                   # her answer's picture on the face (a clear here)
                    face_mirror.publish_json({"type": "tool_result", "name": "present_story",
                                              "result": {"screen": "clear"}})
                else:
                    at, said = time.monotonic(), ""
                    for piece in (text if isinstance(text, list) else [text]):
                        said += piece
                        ceremony_motion.on_host_words(call, SimpleNamespace(text=said, at=at))
                        await asyncio.sleep(0.3)         # (pieces closer than QUIET_S: one decision)
                print("%2d %-11s %s" % (k, "[%s]" % want, text if isinstance(text, str) else "".join(text)))
                await look("%02d_%s" % (k, want), want, ceremony_motion.QUIET_S)
                if page is None:
                    await asyncio.sleep(args.gap)
            async with ceremony.script_press():
                print("a press that says no line: the topic slide goes after PRESS_LINE_WAIT_S")
            await look("99_press", "logo", ceremony_motion.PRESS_LINE_WAIT_S)
            if not args.loop or headless:
                break
            await asyncio.sleep(args.gap)
        face_mirror.call_ended()
        await asyncio.sleep(0.5)
    finally:
        if browser is not None:
            await browser.close()
        if playwright is not None:
            await playwright.stop()
        server.should_exit = True
        await serving
    if args.check or args.shots:
        print("checked: %d wrong" % len(failures))
        for f in failures:
            print("  NOT %s at %s: %s" % (f[1], f[0], json.dumps(f[2])))
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):              # (Thai lines on a Windows console: cp1252 cannot print them)
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8796)
    ap.add_argument("--gap", type=float, default=6.0, help="seconds between two host lines in a window (default 6)")
    ap.add_argument("--loop", action="store_true", help="over and over (a window only)")
    ap.add_argument("--lines", default="", help="a JSON list of {text, expect} instead of the demo")
    ap.add_argument("--shots", default="", help="a folder: headless Chromium, a screenshot after every line")
    ap.add_argument("--check", action="store_true", help="headless: the page's state after every line, exit 1 if wrong")
    ap.add_argument("--settle", type=float, default=1.6,
                    help="seconds to wait (after QUIET_S) before looking (headless)")
    ap.add_argument("--size", default="1920x1080")
    return asyncio.run(run(ap.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
