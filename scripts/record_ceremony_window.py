#!/usr/bin/env python3
"""The ceremony film recorded from a REAL Chrome window, scene by scene, with sound.

    python scripts/record_ceremony_window.py                 # every scene with lines
    python scripts/record_ceremony_window.py --scenes 3      # one scene
    python scripts/record_ceremony_window.py --cues s03_l03  # these lines as one clip (scene 0)

Why not scripts/record_ceremony_scenes.py (the first recorder): that one records Chrome's
screencast, and the screencast's capturer leaves ~250 ms holes of its own every time what changes
on the page changes -- the owner, on those videos (18 Nov 2026): "ไม่เห็นหรอว่ามันกระตุก ภาพสะดุด".
Chrome itself drew 60 frames a second through every one of those holes
(scripts/perf_ceremony_frames.py). So this one never asks Chrome for pictures: it opens the real
page in a real (headed) Chromium window on this PC's GPU, and ffmpeg takes the window's own
pixels from Windows (Windows.Graphics.Capture -- ffmpeg's `gfxcapture`), encoded on the GPU.
What is in the file is what the window showed, stalls included if there are any.

How it holds together:
- the window: an app window (no tabs, no address bar), scale 1, its page exactly --size; it is
  sent behind the other windows and makes no sound (--mute-audio: the page's audio clock still
  runs, which is what the film follows). Chrome is told not to stop drawing a covered window.
- sync: before each scene the page shows a magenta plate for a moment. In the recording the
  plate gives (a) the page's rectangle inside the captured window -> the crop, and (b) the frame
  where it goes away = the wall-clock moment it was taken away -> where the voices go.
- sound: her takes and Khun Miki's stand-in question (preview only), mixed into one track at the
  moments they started going to the page, as in the first recorder.
- the day's continuity (18 Nov night): the screen is never cleared between scenes -- a scene's
  clip opens on the held frame of the line before it (played unfilmed first when the recording
  starts mid-ceremony), and a recording from the first line films the logo opening.
Nothing talks to the robot or a model. The lines are drafts until signed, as in the preview.
Output: <out>/scene_NN.mp4 + scenes.json.
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import importlib.util
import json
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RATE = 24000
PLATE = (255, 0, 255)
#: how long the filmed opening runs before the first line (the owner's logo clip is 6.7 s, then it holds)
OPENING_S = 8.0


def _module(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / ("%s.py" % name))
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


# ---- the window (Win32) ----
user32 = ctypes.windll.user32 if sys.platform == "win32" else None
HWND_BOTTOM, SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 1, 0x0001, 0x0002, 0x0010


def find_window(title: str) -> int:
    found = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HWND, wintypes.LPARAM)

    def each(hwnd, _):
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        if title in buf.value and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return 1

    user32.EnumWindows(proc(each), 0)
    return int(found[0]) if found else 0


def send_behind(hwnd: int, front: int) -> None:
    """Behind every other window, and the window the person was in gets the keys back."""
    user32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(HWND_BOTTOM), 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    if front and front != hwnd:
        user32.SetForegroundWindow(wintypes.HWND(front))


# ---- reading the plate out of the recording ----
def plate_in(raw: Path, ffmpeg: str, seconds: float = 6.0):
    """-> (crop x, y, w, h of the page inside the captured frame; video time the plate went away).
    Looks at the first `seconds` of the raw recording."""
    import numpy as np

    probe = subprocess.run([ffmpeg, "-hide_banner", "-i", str(raw)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    import re
    m = re.search(r"Video:.*?(\d{3,5})x(\d{3,5})", probe.stderr)
    if not m:
        raise RuntimeError("cannot read the recording's size")
    w, h = int(m.group(1)), int(m.group(2))
    fps = 60
    data = subprocess.run([ffmpeg, "-loglevel", "error", "-t", "%.2f" % seconds, "-i", str(raw), "-vf", "fps=%d" % fps,
                           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    n = len(data) // (w * h * 3)
    frames = np.frombuffer(data[:n * w * h * 3], dtype=np.uint8).reshape(n, h, w, 3)

    def is_plate(px):
        return (px[..., 0] > 200) & (px[..., 1] < 70) & (px[..., 2] > 200)

    share = [float(is_plate(f[::8, ::8]).mean()) for f in frames]
    on = [i for i, s in enumerate(share) if s > 0.5]
    if not on:
        raise RuntimeError("the sync plate is not in the recording (was the window captured?)")
    last = on[-1]
    mask = is_plate(frames[on[len(on) // 2]])
    ys, xs = np.where(mask)
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
    return (x0, y0, x1 - x0, y1 - y0), (last + 1) / fps


def mix(voices: list[tuple[bytes, float]], seconds: float) -> bytes:
    """One 24 kHz mono PCM16 track; `voices` = (pcm, seconds from the clip's start)."""
    import numpy as np

    track = np.zeros(int(seconds * RATE) + 1, dtype=np.int32)
    for pcm, at in voices:
        a = np.frombuffer(pcm, dtype=np.int16).astype(np.int32)
        i = max(0, int(round(at * RATE)))
        if i >= len(track):
            continue
        n = min(len(a), len(track) - i)
        track[i:i + n] += a[:n]
    return np.clip(track, -32768, 32767).astype(np.int16).tobytes()


async def run(args) -> int:
    import uvicorn
    from playwright.async_api import async_playwright

    from app import face_mirror, mirror_server
    from app.config import settings

    if user32 is None:
        print("Windows only (Windows.Graphics.Capture)")
        return 2
    pv = _module("preview_ceremony_show")
    first = _module("record_ceremony_scenes")
    vl = _module("voice_line")
    ffmpeg = vl.find_ffmpeg()
    settings.mirror_token = secrets.token_urlsafe(12)
    settings.stories_enabled = True
    clips = pv._clips()
    playable = {c["id"]: c for c in pv.lines(outside=True) if c["id"] in clips}   # (--cues: the backup line too)
    hosts = {} if args.no_host else pv._host_takes()
    todo = first.scenes(playable)
    if args.scenes:
        want = {int(x) for x in args.scenes.split(",") if x.strip()}
        todo = [s for s in todo if s["n"] in want]
    if args.cues:
        ids = [x.strip() for x in args.cues.split(",") if x.strip()]
        missing = [i for i in ids if i not in playable]
        if missing:
            print("not a playable cue: %s" % ", ".join(missing))
            return 2
        todo = [{"n": 0, "title": "cues " + ",".join(ids), "cues": [playable[i] for i in ids]}]
    if not todo:
        print("no scene to record")
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    server = uvicorn.Server(uvicorn.Config(mirror_server.app, host="127.0.0.1", port=args.port, lifespan="off",
                                           log_level="warning", access_log=False))
    serving = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started or serving.done():
            break
        await asyncio.sleep(0.05)
    if not server.started:
        serving.cancel()
        print("could not listen on 127.0.0.1:%d -- try --port" % args.port)
        return 2
    url = "http://127.0.0.1:%d/mirror?token=%s&sound=1" % (args.port, settings.mirror_token)
    width, height = (int(x) for x in args.size.lower().split("x"))
    px, py = (int(x) for x in args.at.split(","))
    title = "EMMA-REC-" + secrets.token_hex(4)
    front = user32.GetForegroundWindow()
    profile = tempfile.mkdtemp(prefix="emma-rec-")
    report = []
    pw = await async_playwright().start()
    ctx = await pw.chromium.launch_persistent_context(
        profile, headless=False, no_viewport=True, ignore_default_args=["--enable-automation"],
        args=["--app=about:blank", "--window-position=%d,%d" % (px, py), "--window-size=%d,%d" % (width + 16, height + 48),
              "--force-device-scale-factor=1", "--mute-audio", "--autoplay-policy=no-user-gesture-required",
              "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
              "--disable-background-timer-throttling", "--disable-features=CalculateNativeWinOcclusion",
              "--hide-scrollbars", "--ignore-gpu-blocklist"])
    try:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await ctx.add_init_script(path=str(ROOT / "scripts" / "preview_host_caption.js"))
        cdp = await ctx.new_cdp_session(page)
        # the page exactly --size: grow or shrink the window by what its frame takes
        win = await cdp.send("Browser.getWindowForTarget")
        for _ in range(3):
            inner = await page.evaluate("() => [innerWidth, innerHeight]")
            if inner == [width, height]:
                break
            b = (await cdp.send("Browser.getWindowForTarget"))["bounds"]
            await cdp.send("Browser.setWindowBounds", {"windowId": win["windowId"], "bounds": {
                "width": b["width"] + width - inner[0], "height": b["height"] + height - inner[1]}})
            await asyncio.sleep(0.4)
        inner = await page.evaluate("() => [innerWidth, innerHeight]")
        if inner != [width, height]:
            print("the window's page is %sx%s, not %dx%d (does it fit the screen at --at?)" % (inner[0], inner[1], width, height))
            return 2
        await page.goto(url)
        hwnd = 0
        for _ in range(20):                               # (the page sets its own title as it loads)
            await page.evaluate("t => { document.title = t; }", title)
            await asyncio.sleep(0.3)
            hwnd = find_window(title)
            if hwnd:
                break
        if not hwnd:
            print("cannot find the recording window (its title never became %s)" % title)
            return 2
        send_behind(hwnd, front)
        while not face_mirror.watching():
            await asyncio.sleep(0.2)
        await asyncio.sleep(2.0)
        # The day's screen, not a scene's: owner 18 Nov night, "ตอนรอระหว่างบทไม่ต้องมีเลย ...
        # ไม่อยากให้มีคั่น" -- the logo OPENS the ceremony once, and between two lines (scenes too)
        # the line before's slide stays until the next comes in from it. The first recorder of
        # this file cut the screen after every scene, so each clip began on the logo: the day
        # never shows that, and it hid every scene-to-scene transition. Now: a recording from the
        # ceremony's first line films the opening (ready goes out once the camera runs); one that
        # begins later is first brought to where the day would be -- the line before it is played
        # unfilmed, so the clip opens on that line's held frame.
        ordered = [c for s in first.scenes(playable) for c in s["cues"]]
        start_id = todo[0]["cues"][0]["id"]
        opening = bool(ordered) and ordered[0]["id"] == start_id
        if not opening:
            face_mirror.call_started()
            face_mirror.publish_json({"type": "ready", "output_rate": RATE, "profile": "ceremony"})
            await asyncio.sleep(2.5)
            at = [c["id"] for c in ordered].index(start_id) if start_id in [c["id"] for c in ordered] else -1
            if at > 0:
                prev = ordered[at - 1]
                print("bringing the screen to %s's held frame first (not filmed)" % prev["id"])
                await pv.exchange(prev, clips[prev["id"]], None, None)
                await asyncio.sleep(1.0)
        await page.evaluate("t => { document.title = t; }", title)

        plate_on = """() => { const d = document.createElement('div'); d.id = 'rec-plate';
            d.style.cssText = 'position:fixed;inset:0;background:rgb(255,0,255);z-index:2147483647'; document.body.appendChild(d); }"""
        plate_off = "() => { const d = document.getElementById('rec-plate'); if (d) d.remove(); }"

        for scene in todo:
            raw = out / ("scene_%02d_raw.mkv" % scene["n"])
            cap = subprocess.Popen(
                [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi",
                 "-i", "gfxcapture=hwnd=%d:capture_cursor=0:max_framerate=60" % hwnd,
                 "-c:v", "h264_nvenc", "-preset", "p5", "-rc", "constqp", "-qp", "17", "-g", "30",
                 "-fps_mode", "cfr", "-r", "60", str(raw)],
                stdin=subprocess.PIPE, stderr=subprocess.PIPE)
            await asyncio.sleep(2.0)                       # the capture is running
            if cap.poll() is not None:
                print("ffmpeg could not capture the window: %s" % cap.stderr.read().decode("utf-8", "replace")[-400:])
                return 2
            voices: list[tuple[bytes, float]] = []
            await page.evaluate(plate_on)
            await asyncio.sleep(0.5)
            await page.evaluate(plate_off)
            off_at = time.time()                           # the plate went away about now
            if opening:
                # the ceremony's opening, filmed: the show mode comes on, the logo clip plays once
                opening = False
                face_mirror.call_started()
                face_mirror.publish_json({"type": "ready", "output_rate": RATE, "profile": "ceremony"})
                await asyncio.sleep(OPENING_S)
            else:
                await asyncio.sleep(1.2)                   # the held frame before the first word
            print("scene %d  %s  (%d lines)" % (scene["n"], scene["title"], len(scene["cues"])))
            for cue in scene["cues"]:
                print("   %s  %s" % (cue["id"], cue["en"][:60]))
                await pv.exchange(cue, clips[cue["id"]], hosts.get(cue["id"]), None,
                                  on_voice=lambda pcm, at: voices.append((pcm, at)),
                                  on_caption=lambda text: page.evaluate("t => window.__pvHostCaption(t)", text))
                await asyncio.sleep(args.gap)
            await asyncio.sleep(args.tail)
            end_at = time.time()
            try:
                cap.stdin.write(b"q")
                cap.stdin.flush()
            except OSError:
                pass
            await asyncio.get_running_loop().run_in_executor(None, cap.wait)
            # (no subtitle_cut here: the scene's last slide stays, as on the day, and the next
            # scene's first line comes in from it)

            (cx, cy, cw, ch), plate_end = await asyncio.get_running_loop().run_in_executor(None, plate_in, raw, ffmpeg)
            start = plate_end + 0.15                       # the clip begins just after the plate
            seconds = (end_at - off_at) - 0.15
            sound = [(pcm, (at - off_at) - 0.15) for pcm, at in voices]
            wav = out / ("scene_%02d_voice.wav" % scene["n"])
            with wave.open(str(wav), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(RATE)
                w.writeframes(mix(sound, seconds))
            target = out / ("scene_%02d.mp4" % scene["n"])
            # (one file may be at most ~15 MB where these are shared: the bitrate is capped by the length)
            cap_kbps = max(500, int((args.max_mb * 8000) / seconds) - 128)
            vf = "crop=%d:%d:%d:%d,scale=%d:-2:flags=lanczos,fps=%d" % (cw, ch, cx, cy, args.width, args.fps)
            subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-ss", "%.3f" % start, "-i", str(raw), "-i", str(wav),
                            "-vf", vf, "-c:v", "libx264", "-preset", "slow", "-crf", str(args.crf),
                            "-maxrate", "%dk" % cap_kbps, "-bufsize", "%dk" % (cap_kbps * 2),
                            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-c:a", "aac", "-b:a", "112k",
                            "-t", "%.3f" % seconds, str(target)], check=True)
            if not args.keep_raw:
                raw.unlink(missing_ok=True)
            wav.unlink(missing_ok=True)
            size = target.stat().st_size
            print("   -> %s  %.1f s  %.1f MB  page %dx%d at (%d,%d) in the window" % (target.name, seconds, size / 1e6, cw, ch, cx, cy))
            report.append({"n": scene["n"], "title": scene["title"], "file": target.name,
                           "cues": [{"id": c["id"], "th": c["th"], "en": c["en"],
                                     "host_th": (hosts.get(c["id"]) or {}).get("text")} for c in scene["cues"]],
                           "seconds": round(seconds, 1), "bytes": size})
            await asyncio.sleep(1.5)
        (out / "scenes.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        face_mirror.call_ended()
    finally:
        await ctx.close()
        await pw.stop()
        server.should_exit = True
        await serving
        shutil.rmtree(profile, ignore_errors=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--scenes", default="", help="comma-separated scene numbers of the running order (default: all)")
    ap.add_argument("--cues", default="", help="comma-separated cue ids recorded as one clip (scene 0)")
    ap.add_argument("--out", default=str(ROOT / ".tmp" / "ceremony-show" / "scenes"))
    ap.add_argument("--port", type=int, default=8860)
    ap.add_argument("--size", default="1920x1080", help="the page inside the window")
    ap.add_argument("--at", default="-2560,0", help="the window's top-left on the desktop (physical pixels; it must fit a screen)")
    ap.add_argument("--width", type=int, default=1280, help="the video's width (height follows)")
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--crf", type=int, default=23)
    ap.add_argument("--max-mb", type=float, default=14.0, help="a scene's file stays under about this size")
    ap.add_argument("--gap", type=float, default=1.5, help="seconds between two lines")
    ap.add_argument("--tail", type=float, default=2.5, help="seconds after a scene's last line")
    ap.add_argument("--no-host", action="store_true", help="without Khun Miki's stand-in questions")
    ap.add_argument("--keep-raw", action="store_true", help="keep the window's raw recording beside the clip")
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
