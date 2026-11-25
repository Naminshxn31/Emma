#!/usr/bin/env python3
"""Watch the ceremony's motion slides on this PC, as the event screen will show them.

    python scripts/preview_ceremony_show.py                    # prints a URL; open it in Chrome
    python scripts/preview_ceremony_show.py --only s04_l11,s06_l03
    python scripts/preview_ceremony_show.py --from s05_l03 --gap 4
    python scripts/preview_ceremony_show.py --shots .tmp/ceremony-show/shots   # no window: screenshots
    python scripts/preview_ceremony_show.py --only s04_l06 --shots .tmp/ceremony-show/shots --shots-every 2

Owner 17 Nov 2026: "ทำตัวพรีเซ้นต์โมชั่น ... ลิ้งระหว่างสไลด์กับเสียงหุ่น ... มีซับภาษาไทย
ด้านล่าง ... สำหรับขึ้นจออีเว้นท์ ขนาด 16:9 ... ลองทำมา".

This starts the REAL room-screen listener (app/mirror_server.py) on 127.0.0.1 with a
throwaway token and plays the ceremony's lines through app/face_mirror.py exactly as the
robot's server does on the day: the line's Thai subtitle and its slide in one message,
then her English voice (the preview clips in data/ceremony/voice/) as the call's audio --
so the page that opens is the day's page, its slide driven by the voice it plays.

Different from the day, on purpose: the slides are shown although the slides file is a
DRAFT (`ceremony_slides.for_cue(drafts=True)`; on the robot an unsigned file sends none --
sign with scripts/approve_ceremony_slides.py after watching this). The content checks are
the same: a slide that breaks one is not shown here either, and is named below.

Nothing here talks to the robot, the network or a model. The lines are the preview clips
of the cues as the file has them now, approved or not (`event_script.servable(drafts=True)`,
the measuring scripts' door: this is a look on this PC, not the stage -- there a draft is
never said, and nothing here changes that); the cues still waiting for a signature are
named. A line with no preview clip is skipped and named.

As the day runs (owner 23 Nov 2026, watching this): a whole run ends at the running order's
last line, the thank-you (the backup quotation line is outside the order: `--only
s05_backup_quote`); a joke's punchline (`"chain": true` on its set-up) comes the moment the
set-up's voice ends -- no stand-in host line, no --gap, no pause before its first word. That is
the day with listened-to recordings; a line Gemini reads live starts ~1-2 s after the text.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
import json
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RATE = 24000
CHUNK = RATE * 2 // 10            # 0.1 s of PCM16 mono


def _clips() -> dict[str, bytes]:
    """cue id -> the preview take of its English line (24 kHz mono PCM16)."""
    from app import event_voice
    from app.offline.voice import read_wav

    base = event_voice.folder()
    index = event_voice.load_index(base) if base else None
    out: dict[str, bytes] = {}
    for clip in ((index or {}).get("clips") or {}).values():
        if isinstance(clip, dict) and clip.get("lang") == "en" and isinstance(clip.get("cue"), str):
            pcm = read_wav(base / str(clip.get("file")))
            if pcm:
                out[clip["cue"]] = pcm
    return out


def lines(only: list[str] | None = None, start: str | None = None, *, outside: bool = False) -> list[dict]:
    """The lines to play: the running order's cues, each with words (approved or not: see
    the module's note). `only` picks ids in the order given -- any worded cue, the backup
    line outside the order too. `outside` = the cues no step presses (the backup quotation
    line) after the order's, as the recording scripts' table of what they may be asked for.

    Owner 23 Nov 2026 ("ฉากสุดท้ายเอาออกให้มันจบตรงขอบคุณพอ"): a whole run ends at the order's
    last line, the thank-you -- the backup line used to come after it, a scene the day never has."""
    from app import event_script

    doc = event_script.load()
    ok = event_script.servable(doc, drafts=True)
    seq: list[str] = []
    for scene in event_script.order(doc) or []:
        for step in scene["steps"]:
            if step["do"] == "cue" and step["cue"] in ok and step["cue"] not in seq:
                seq.append(step["cue"])
    extra = [cid for cid in ok if cid not in seq]
    worded = [cid for cid in seq + extra if ok[cid].get("en") and ok[cid].get("th")]
    seq = [cid for cid in worded if cid in seq or outside]
    if only:
        missing = [cid for cid in only if cid not in worded]
        if missing:
            raise SystemExit("not a playable cue (unknown, breaks a content rule, or no words): %s" % ", ".join(missing))
        seq = list(only)
    elif start:
        if start not in seq:
            raise SystemExit("not a playable cue: %s" % start)
        seq = seq[seq.index(start):]
    return [ok[cid] for cid in seq]


def chained() -> dict[str, str]:
    """{punchline cue id: its set-up's cue id}: the order's `"chain": true` pairs, read the way
    the day reads them (app/event_order.py `_chain_follower`). Owner 23 Nov 2026: "ฉากที่เล่นมุข
    ที่ตอนโดดลงสระ และแฟนพันธุ์แท้ให้พูดต่อเนื่องเลย ไม่ต้องเว้นช่วงมันดูเงียบ และช้า" -- on the day the
    punchline goes the moment the set-up has been said; here, no stand-in host line and no --gap
    between the two either (`watch`, `run`), and no pause before its first word (`play`)."""
    from app import event_order, event_script

    scenes = event_script.order(event_script.load()) or []
    out: dict[str, str] = {}
    for sc in scenes:
        for st in sc["steps"]:
            follow = event_order._chain_follower(scenes, sc["n"], st["i"]) if st.get("chain") else None
            if follow is not None:
                after = next(s for s in sc["steps"] if s["i"] == follow[1])
                out[after["cue"]] = st["cue"]
    return out


def _follows(todo: list[dict], k: int, pairs: dict[str, str]) -> bool:
    """todo[k] is the punchline of the line right before it in this run."""
    return 0 < k < len(todo) and pairs.get(todo[k]["id"]) == todo[k - 1]["id"]


class Controls:
    """The watcher's buttons (owner 18 Nov 2026: "ตอนเล่นมันกดกลับกดหยุดไม่ได้หรอ จะได้เช็ค"):
    pause / play, previous, replay, next, a cue picked from the list. The page half is
    scripts/preview_ceremony_ctl.js (it stops the page's clocks); this half stops the line's
    clock here and sends no voice while paused, and jumps between lines."""

    def __init__(self, cues: list[dict]):
        self.cues = [{"id": c["id"], "en": str(c.get("en") or "")[:60]} for c in cues]
        self.i = 0
        self.paused = False
        self.done = False
        self.seq = 0                       # one more each jump
        self._pause_at: float | None = None
        self._paused_total = 0.0
        self.jump: tuple[str, int | None] | None = None
        self.caption = ""                  # Khun Miki's words while his stand-in voice plays

    def set_paused(self, on: bool) -> None:
        now = time.monotonic()
        if on and not self.paused:
            self._pause_at = now
        elif not on and self.paused and self._pause_at is not None:
            self._paused_total += now - self._pause_at
            self._pause_at = None
        self.paused = on

    def paused_s(self) -> float:
        """Seconds paused so far (a line's clock is wall time less this)."""
        extra = time.monotonic() - self._pause_at if self.paused and self._pause_at is not None else 0.0
        return self._paused_total + extra

    def press(self, cmd: str, i=None) -> None:
        if cmd == "pause":
            self.set_paused(True)
        elif cmd == "play":
            self.set_paused(False)
        elif cmd in ("prev", "next", "replay", "goto"):
            target = None
            if cmd == "goto":
                try:
                    target = max(0, min(len(self.cues) - 1, int(i)))
                except (TypeError, ValueError):
                    return
            self.jump = (cmd, target)

    def snapshot(self) -> dict:
        n = len(self.cues)
        i = max(0, min(n - 1, self.i)) if n else 0
        return {"i": i, "n": n, "id": self.cues[i]["id"] if n else None, "paused": self.paused,
                "done": self.done, "seq": self.seq, "cues": self.cues, "caption": self.caption}

    async def sleep(self, seconds: float) -> None:
        """`seconds` of unpaused time (inside a line's task: a jump cancels the task)."""
        start, began = self.paused_s(), time.monotonic()
        while (time.monotonic() - began) - (self.paused_s() - start) < seconds:
            await asyncio.sleep(0.05)

    async def wait(self, seconds: float | None = None, task: asyncio.Task | None = None):
        """Until `task` is done, `seconds` of unpaused time have gone, or a jump is pressed
        (returned as (cmd, target)); None otherwise. Never over while paused -- `seconds` 0 too
        (a joke's punchline that follows at once waits for play like any line)."""
        start = self.paused_s()
        began = time.monotonic()
        while True:
            if self.jump is not None:
                jump, self.jump = self.jump, None
                return jump
            if task is not None and task.done():
                return None
            if (seconds is not None and not self.paused
                    and (time.monotonic() - began) - (self.paused_s() - start) >= seconds):
                return None
            await asyncio.sleep(0.05)


#: How long one screenshot of the headless page holds the line's loop (measured 23 Nov: ~1.1 s at
#: 1920x1080 without WebGL) -- a set-up's last shot must be over before its voice is.
SHOT_S = 1.2


def shot_marks(seconds: float, every: float = 0.0, *, chained: bool = False) -> list[tuple[str, float]]:
    """The screenshots of one line, (name, seconds into her voice): just after it starts, at its
    middle and at its end ("a", "b", "c") -- or, with `every`, each `every` seconds of the line
    ("t00.0", "t02.0", ...: the motion as a sequence) and once more after its end. A set-up whose
    punchline follows at once (`chained`) has none at or after the end of its voice: a shot there
    (awaited, ~SHOT_S) was a pause before the punchline that the day never has -- the punchline's
    own first shot is the moment after."""
    if every > 0:
        step = max(0.5, float(every))
        marks = [("t%04.1f" % (k * step), max(0.3, k * step)) for k in range(int(seconds / step) + 1)]
        if chained:
            return [m for m in marks if m[1] <= seconds - SHOT_S] or marks[:1]
        marks.append(("t%04.1f" % (seconds + 1.5), seconds + 1.5))
        return marks
    marks = [("a", 0.9), ("b", max(1.2, seconds / 2))]
    return marks if chained else marks + [("c", seconds + 0.5)]


async def play(cue: dict, pcm: bytes, *, on_mark=None, every: float = 0.0, ctl: Controls | None = None,
               on_voice=None, lead_s: float = 0.4, tail_s: float = 0.3, chained: bool = False) -> float:
    """One line, as event_cues sends it: the subtitle (+ its slide), her voice, the end.
    `on_mark(name)` is awaited at `shot_marks`. With `ctl`, the line's clock stops while the
    watcher has paused it, and no voice goes out then.

    `lead_s` = the press, then her first sound (0 for a punchline that follows its set-up: no
    press, the line goes the moment the set-up's voice ends); `tail_s` = how long past her voice
    the line is held before its end goes out (0 for a set-up: its punchline is next, at once);
    `chained` = this line is such a set-up (`shot_marks`)."""
    from app import ceremony_slides, face_mirror

    seconds = len(pcm) / 2 / RATE
    line = {"id": cue["id"], "text": cue["th"], "scene": cue.get("scene_th"), "seconds": seconds}
    slide = ceremony_slides.for_cue(cue["id"], lang="en", drafts=True)
    if slide is not None:
        line["slide"] = slide
    face_mirror.subtitle(line)
    if lead_s > 0:
        await asyncio.sleep(lead_s)                # the press, then her first sound
    began = time.monotonic()
    if on_voice is not None:                       # (scripts/record_ceremony_scenes.py: where the sound goes)
        on_voice(pcm, time.time())
    held = ctl.paused_s() if ctl else 0.0

    def elapsed() -> float:
        return time.monotonic() - began - ((ctl.paused_s() - held) if ctl else 0.0)

    marks = shot_marks(seconds, every, chained=chained) if on_mark else []
    sent = 0
    while sent < len(pcm) or marks:
        if ctl and ctl.paused:
            await asyncio.sleep(0.05)
            continue
        # ahead of real time, as a recording goes out: the page queues it -- every piece that is
        # due, not one a pass (a screenshot awaited below took ~1 s a pass: 0.1 s of her voice a
        # second, her voice ran dry on the page and its silence guard took the slide down, 21 Nov)
        while sent < len(pcm) and (sent / 2 / RATE) < elapsed() * 2 + 0.5:
            face_mirror.publish_audio(pcm[sent:sent + CHUNK])
            sent += CHUNK
        if chained and marks and elapsed() > seconds - SHOT_S:
            # (shots behind their times -- each ~SHOT_S, asked every 0.5 s: a backlog of them must not
            # hold her punchline back; 23 Nov, --shots-every 0.5 put 3.4 s between the two)
            marks.clear()
        if marks and elapsed() >= marks[0][1]:
            await on_mark(marks.pop(0)[0])
        await asyncio.sleep(0.02)
    while elapsed() < seconds + tail_s:
        await asyncio.sleep(0.02)
    face_mirror.publish_json({"type": "turn_complete"})
    face_mirror.subtitle_end(cue["id"])
    return seconds


CTL_JS = Path(__file__).with_name("preview_ceremony_ctl.js")
CAPTION_JS = Path(__file__).with_name("preview_host_caption.js")
try:
    # (module level: this file's annotations are strings -- `from __future__` -- and FastAPI
    # resolves them in the module's globals; a local import left the body a query param, 422)
    from fastapi import Request as _Request
except ImportError:                                  # the room-screen app needs it anyway
    _Request = None


def add_controls(app, ctl: Controls) -> None:
    """This process's copy of the room-screen app only: the control script injected as the
    first script of /mirror, and its two routes. Nothing of it exists on the robot."""
    from fastapi.responses import JSONResponse, Response

    async def ctl_js() -> Response:
        return Response(CTL_JS.read_text(encoding="utf-8"), media_type="text/javascript",
                        headers={"Cache-Control": "no-store"})

    async def ctl_get() -> JSONResponse:
        return JSONResponse(ctl.snapshot(), headers={"Cache-Control": "no-store"})

    async def ctl_post(request: _Request) -> JSONResponse:
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if isinstance(body, dict):
            ctl.press(str(body.get("cmd") or ""), body.get("i"))
        return JSONResponse(ctl.snapshot())

    async def caption_js() -> Response:
        return Response(CAPTION_JS.read_text(encoding="utf-8"), media_type="text/javascript",
                        headers={"Cache-Control": "no-store"})

    app.add_api_route("/preview-ctl.js", ctl_js, methods=["GET"])
    app.add_api_route("/preview-host-caption.js", caption_js, methods=["GET"])
    app.add_api_route("/preview-ctl", ctl_get, methods=["GET"])
    app.add_api_route("/preview-ctl", ctl_post, methods=["POST"])

    async def inject(request, call_next):
        if request.url.path != "/mirror":
            return await call_next(request)
        # (the page whole every time: a 304 would hand back a copy without the controls)
        request.scope["headers"] = [(k, v) for k, v in request.scope["headers"]
                                    if k.lower() not in (b"if-none-match", b"if-modified-since")]
        response = await call_next(request)
        if response.status_code != 200 or "text/html" not in response.headers.get("content-type", ""):
            return response
        body = b"".join([chunk async for chunk in response.body_iterator])
        body = body.replace(b"<head>", b'<head><script src="/preview-ctl.js"></script>'
                                        b'<script src="/preview-host-caption.js"></script>', 1)
        out = Response(body, status_code=200, media_type="text/html")
        out.raw_headers = [(k, v) for k, v in out.raw_headers if k.lower() != b"content-type"] + [
            (k, v) for k, v in response.raw_headers if k.lower() not in (b"content-length",)]
        return out

    app.middleware("http")(inject)


async def run(args) -> int:
    import uvicorn

    from app import ceremony_slides, face_mirror, mirror_server
    from app.config import settings

    settings.mirror_token = secrets.token_urlsafe(12)
    settings.stories_enabled = True
    only = [x.strip() for x in args.only.split(",") if x.strip()] or None
    todo = lines(only, args.start)
    clips = _clips()
    doc = ceremony_slides.load()
    print("slides file: %s" % ("none" if doc is None else "approved" if ceremony_slides.approved(doc)
                               else "DRAFT (shown here; the robot sends none until it is signed)"))
    for row in ceremony_slides.status(doc) if doc else []:
        if row["problems"]:
            print("  NOT SHOWN %s: %s" % (row["id"], ", ".join(row["problems"])))
    from app import event_script
    drafts = [c["id"] for c in todo if not event_script.approved(c)]
    if drafts:
        print("cues NOT approved as they are now (%d) -- previewed here, never said on the stage until signed "
              "(scripts/approve_event_script.py): %s" % (len(drafts), ", ".join(drafts)))
    for cue in todo:
        if cue["id"] not in clips:
            print("  no preview clip for %s -- skipped" % cue["id"])
    todo = [c for c in todo if c["id"] in clips]
    if not todo:
        print("nothing to play")
        return 2

    ctl = None if args.shots or args.no_controls else Controls(todo)
    if ctl is not None:
        add_controls(mirror_server.app, ctl)
    server = uvicorn.Server(uvicorn.Config(mirror_server.app, host="127.0.0.1", port=args.port, lifespan="off",
                                           log_level="warning", access_log=False))
    serving = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started or serving.done():
            break
        await asyncio.sleep(0.05)
    if not server.started:
        serving.cancel()
        print("could not listen on 127.0.0.1:%d (taken?) -- try --port" % args.port)
        return 2
    url = "http://127.0.0.1:%d/mirror?token=%s&sound=1" % (args.port, settings.mirror_token)
    browser = page = playwright = None
    try:
        if args.shots:
            from playwright.async_api import async_playwright

            out = Path(args.shots)
            out.mkdir(parents=True, exist_ok=True)
            playwright = await async_playwright().start()
            # (no WebGL by default: the software renderer of a headless page eats the CPU and
            # the frames arrive seconds late. --webgl asks for this PC's GPU instead -- the way
            # to look at a parallax picture; without a GPU it falls back to the same software)
            flags = (["--ignore-gpu-blocklist", "--enable-gpu", "--use-angle=d3d11"] if args.webgl
                     else ["--disable-webgl", "--disable-3d-apis"])
            browser = await playwright.chromium.launch(headless=True, args=[
                "--autoplay-policy=no-user-gesture-required"] + flags)
            width, height = (int(x) for x in args.size.lower().split("x"))
            page = await browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
            # what the engine says about a slide or an act (an act that threw is "down"): here,
            # where its author is looking
            page.on("console", lambda m: print("  [page %s] %s" % (m.type, m.text))
                    if m.type in ("warning", "error") and "ceremony-show" in m.text else None)
            # (without --webgl the hidden 3D Emma says she has no WebGL: not the film's news)
            page.on("pageerror", lambda e: print("  [page error] %s" % e)
                    if args.webgl or "WebGL" not in str(e) else None)
            await page.goto(url)
        else:
            print("\nopen this in Chrome (full screen: F11), then the lines start:\n\n  %s\n" % url)
            if ctl is not None:
                print("controls (move the mouse: a bar at the top): Space pause/play, <- previous, "
                      "-> next, R replay, the list = go to a line\n")
        while not face_mirror.watching():
            await asyncio.sleep(0.2)
        await asyncio.sleep(1.5 if args.shots else 3.0)
        face_mirror.call_started()
        face_mirror.publish_json({"type": "ready", "output_rate": RATE, "profile": "ceremony"})
        await asyncio.sleep(1.5)
        if page is not None:
            await page.screenshot(path=str(Path(args.shots) / "00_backdrop.png"))
        report = []
        pairs = chained()
        # todo[k] is a set-up whose punchline is todo[k + 1]: no gap, no hold after its voice
        sets_up = [_follows(todo, k + 1, pairs) for k in range(len(todo))]
        every = args.shots_every if args.shots else 0.0
        last = {c["id"]: shot_marks(len(clips[c["id"]]) / 2 / RATE, every, chained=sets_up[k])[-1][0]
                for k, c in enumerate(todo)}
        if args.shots and args.shots_every > 0:
            for c in todo:
                for old in Path(args.shots).glob(c["id"] + "_t*.png"):
                    old.unlink()                     # this cue's older sequence only
        for n, cue in enumerate(todo, 1):
            async def mark(name: str, cue=cue, n=n) -> None:
                if page is None:
                    return
                # named by the cue alone: shooting one cue again replaces only its own pictures
                await page.screenshot(path=str(Path(args.shots) / ("%s_%s.png" % (cue["id"], name))))
                if name == last[cue["id"]]:
                    report.append({"cue": cue["id"], "state": await page.evaluate(
                        "() => ({show: window.CeremonyShow ? window.CeremonyShow.state() : null,"
                        " mirror: window.EmmaMirror ? window.EmmaMirror.state() : null,"
                        " sub: (document.querySelector('.mirror-sub-text') || {}).textContent || null})")})

            if ctl is not None:
                break                                 # the watcher's loop below plays them
            lead, follows = sets_up[n - 1], _follows(todo, n - 1, pairs)
            print("%2d/%d %-18s %s%s" % (n, len(todo), cue["id"], cue["en"][:70],
                                         "  (follows at once)" if follows else ""))
            await play(cue, clips[cue["id"]], on_mark=mark if page is not None else None, every=args.shots_every,
                       lead_s=0.0 if follows else 0.4, tail_s=0.0 if lead else 0.3, chained=lead)
            if lead:
                continue                              # its punchline at once: no shots after it, no --gap
            if page is not None and args.shots_after > 0:
                # (review 20 Nov: what a line does once it is over -- the logo page, the zoom into the
                # video's screen -- happens after its last mark; `_zN.N` = N.N s after its end)
                for old in Path(args.shots).glob(cue["id"] + "_z*.png"):
                    old.unlink()
                step, t0, k = max(0.5, args.shots_every or 1.0), time.monotonic(), 0
                while k * step <= args.shots_after:
                    while time.monotonic() - t0 < k * step:
                        await asyncio.sleep(0.02)
                    await page.screenshot(path=str(Path(args.shots) / ("%s_z%04.1f.png" % (cue["id"], k * step))))
                    k += 1
            await asyncio.sleep(args.gap)
        if ctl is not None:
            await watch(ctl, todo, clips, args)
        if page is not None:
            # the other cues' rows of an earlier run stay
            target = Path(args.shots) / "report.json"
            try:
                kept = [r for r in json.loads(target.read_text(encoding="utf-8"))
                        if isinstance(r, dict) and r.get("cue") not in last]
            except (OSError, ValueError):
                kept = []
            target.write_text(json.dumps(kept + report, ensure_ascii=False, indent=1), encoding="utf-8")
            print("screenshots: %s" % Path(args.shots).resolve())
        await asyncio.sleep(args.hold)
        face_mirror.call_ended()
        await asyncio.sleep(0.5)
    finally:
        if browser is not None:
            await browser.close()
        if playwright is not None:
            await playwright.stop()
        server.should_exit = True
        await serving
    return 0


def _host_takes() -> dict:
    try:
        spec = importlib.util.spec_from_file_location("preview_host_voice", ROOT / "scripts" / "preview_host_voice.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.takes()
    except Exception as e:                    # a look on this PC: Emma's lines still play
        print("Khun Miki's voice not loaded: %s" % e)
        return {}


async def say_host(text: str, pcm: bytes, *, ctl: Controls | None = None, on_voice=None, on_caption=None) -> None:
    """His stand-in voice as SOUND ALONE, his Thai words on the preview's own caption layer
    (scripts/preview_host_caption.js). Review 18 Nov: sent as a subtitle with no slide, his line
    made the page clear the stage -- the preview and the recorded scenes showed a cut between his
    question and her answer that the hall never sees (there, nothing goes to the screen while he
    speaks). Paused with the watcher's pause, like her lines."""
    from app import face_mirror

    async def caption(value: str) -> None:
        if ctl is not None:
            ctl.caption = value
        if on_caption is not None:
            r = on_caption(value)
            if asyncio.iscoroutine(r):
                await r

    seconds = len(pcm) / 2 / RATE
    await caption(text)
    try:
        await asyncio.sleep(0.3)
        began = time.monotonic()
        held = ctl.paused_s() if ctl else 0.0
        if on_voice is not None:
            on_voice(pcm, time.time())

        def elapsed() -> float:
            return time.monotonic() - began - ((ctl.paused_s() - held) if ctl else 0.0)

        sent = 0
        while sent < len(pcm):
            # (every piece that is due, as `play`)
            while sent < len(pcm) and not (ctl and ctl.paused) and (sent / 2 / RATE) < elapsed() * 2 + 0.5:
                face_mirror.publish_audio(pcm[sent:sent + CHUNK])
                sent += CHUNK
            await asyncio.sleep(0.02)
        while elapsed() < seconds + 0.2:
            await asyncio.sleep(0.05)
    finally:
        await caption("")


async def exchange(cue: dict, pcm: bytes, host: dict | None, ctl: Controls | None, on_voice=None,
                   on_caption=None, **line) -> None:
    """Owner 18 Nov 2026: "ขอทำเป็นได้ยินคุณมิกิถามด้วย ขอซับไทย แต่ของอีเว้นไม่ต้องนะ อันนี้ใช้ตรวจ".
    His question (a stand-in voice as sound alone, his Thai words on the preview's caption layer --
    `say_host`), a breath, then her line. Preview only: on the day he speaks for himself and the
    screen carries her lines alone. `line` = `play`'s lead_s / tail_s / chained (a joke's pair)."""
    if host:
        await say_host(host["text"], host["pcm"], ctl=ctl, on_voice=on_voice, on_caption=on_caption)
        await (ctl.sleep(1.0) if ctl is not None else asyncio.sleep(1.0))
    await play(cue, pcm, ctl=ctl, on_voice=on_voice, **line)


async def watch(ctl: Controls, todo: list[dict], clips: dict, args) -> None:
    """The lines with the watcher's buttons: a jump cuts the line being said (her voice
    and its slide come down, as the red stop does) and starts the line asked for. After
    the last line the page stays, waiting for a button, until Ctrl+C.

    A joke's punchline (`chained`) follows its set-up as on the day: no stand-in host line
    before it ever (on the day nobody speaks between the two), and -- when it comes on from its
    set-up, not from a button -- no --gap and no pause before its first word. Replaying the
    set-up plays the joke whole; a button that lands on the punchline plays it alone, as a staff
    press of it would."""
    from app import face_mirror

    hosts = {} if args.no_host else _host_takes()
    pairs = chained()
    if not args.no_host:
        missing = [c["id"] for c in todo if c["id"] not in hosts]
        print("Khun Miki's voice (a stand-in, preview only): %d line(s) -- %s"
              % (len(hosts), "render with scripts/preview_host_voice.py" if not hosts else
                 "no words before: " + (", ".join(missing) or "none")))
    i, from_line = 0, False            # from_line: todo[i] came on by itself after todo[i - 1]
    while True:
        if i >= len(todo):
            if not ctl.done:
                ctl.done = True
                print("\nthe last line is over -- the buttons still work; Ctrl+C closes")
            jump = await ctl.wait()
        else:
            ctl.i, ctl.done = i, False
            cue = todo[i]
            follows = from_line and _follows(todo, i, pairs)
            lead = _follows(todo, i + 1, pairs)
            print("%2d/%d %-18s %s%s" % (i + 1, len(todo), cue["id"], cue["en"][:70],
                                         "  (follows at once)" if follows else ""))
            host = None if cue["id"] in pairs else hosts.get(cue["id"])
            task = asyncio.create_task(exchange(cue, clips[cue["id"]], host, ctl, lead_s=0.0 if follows else 0.4,
                                                tail_s=0.0 if lead else 0.3))
            jump = await ctl.wait(task=task)
            if jump is None:
                await task
                # its punchline at once (still not while the watcher has paused: `wait` holds then)
                jump = await ctl.wait(seconds=0.0 if lead else args.gap)
            else:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if jump is None:
            i += 1
            from_line = True
            continue
        from_line = False
        cmd, target = jump
        face_mirror.publish_json({"type": "interrupted"})   # her queued voice goes
        face_mirror.subtitle_cut()                          # and the slide with it
        ctl.set_paused(False)
        ctl.seq += 1
        last = min(i, len(todo) - 1)
        i = {"prev": max(0, last - 1), "next": min(len(todo) - 1, last + 1), "replay": last}.get(cmd, target)
        if cmd == "next" and last == len(todo) - 1 and ctl.done:
            i = len(todo) - 1
        await asyncio.sleep(0.6)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", default="", help="comma-separated cue ids, played in this order")
    ap.add_argument("--from", dest="start", default=None, help="start at this cue of the running order")
    ap.add_argument("--gap", type=float, default=2.5, help="seconds between two lines (default 2.5)")
    ap.add_argument("--hold", type=float, default=6.0, help="seconds the last slide stays before the call ends")
    ap.add_argument("--shots-after", type=float, default=0.0,
                    help="with --shots: go on shooting this many seconds after each line has ended (<cue>_zN.N.png)")
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--shots", default="", help="a folder: headless Chromium, three screenshots of every line")
    ap.add_argument("--shots-every", type=float, default=0.0,
                    help="with --shots: a screenshot every N seconds of each line instead of start/middle/end")
    ap.add_argument("--size", default="1920x1080", help="the screenshots' screen (2048x1080 is ~1.9:1)")
    ap.add_argument("--no-host", action="store_true",
                    help="without Khun Miki's questions (scripts/preview_host_voice.py renders them; never on the day)")
    ap.add_argument("--no-controls", action="store_true",
                    help="no pause/previous/next bar on the page (it is injected into this preview's copy only)")
    ap.add_argument("--webgl", action="store_true",
                    help="with --shots: a page with WebGL on this PC's GPU (parallax pictures); default is none")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
