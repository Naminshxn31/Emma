#!/usr/bin/env python3
"""The ceremony film, recorded scene by scene, with sound -- for people to watch away from this PC.

    python scripts/record_ceremony_scenes.py                       # every scene with lines
    python scripts/record_ceremony_scenes.py --scenes 3,4 --out .tmp/ceremony-show/scenes

Owner 18 Nov 2026: "ทำเสร็จก็ขอลิ้งผลลัพธ์หน่อย ของแต่ละซีนเลยนะ". One MP4 per scene of the
running order (the ceremony file's `order`): Khun Miki's question (the preview's stand-in voice,
scripts/preview_host_voice.py -- never on the day) and Emma's line, cue after cue, exactly as
scripts/preview_ceremony_show.py plays them to the real room-screen page (/mirror, show mode):
his voice as sound alone with his Thai words on a caption layer only the recording has, so the
screen between his question and her answer is what the hall sees.

Pictures: Chrome's own screencast of the page (CDP Page.screencastFrame, JPEG, each frame with
the time it was drawn) on this PC's GPU -- sharper than Playwright's built-in recorder, and the
frame times say how long each picture stayed. Sound: her takes and his, placed at the moment
each one started going to the page (the same wall clock as the frame times), mixed into one
track. Nothing here talks to the robot or a model; the lines are drafts until signed, as in the
preview. Output: <out>/scene_NN.mp4 + scenes.json (title, cues, seconds, bytes).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import importlib.util
import json
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RATE = 24000


def _module(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / ("%s.py" % name))
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


def scenes(playable: dict[str, dict]) -> list[dict]:
    """The running order's scenes that have lines: [{n, title, cues: [cue, ...]}]."""
    from app import event_script

    out = []
    for scene in event_script.order(event_script.load()) or []:
        cues = [playable[s["cue"]] for s in scene["steps"] if s.get("do") == "cue" and s.get("cue") in playable]
        if cues:
            out.append({"n": int(scene["n"]), "title": scene.get("title_th") or "", "cues": cues})
    return out


def mix(voices: list[tuple[bytes, float]], start: float, seconds: float) -> bytes:
    """One 24 kHz mono PCM16 track `seconds` long; each take placed at (its wall time - start)."""
    import numpy as np

    track = np.zeros(int(seconds * RATE) + 1, dtype=np.int32)
    for pcm, at in voices:
        a = np.frombuffer(pcm, dtype=np.int16).astype(np.int32)
        i = max(0, int(round((at - start) * RATE)))
        if i >= len(track):
            continue
        n = min(len(a), len(track) - i)
        track[i:i + n] += a[:n]
    return np.clip(track, -32768, 32767).astype(np.int16).tobytes()


def encode(frames: list[tuple[Path, float]], end: float, voices, out: Path, ffmpeg: str, width: int, crf: int) -> float:
    """frames = (jpeg, drawn-at) in order; `end` = when the scene stopped. -> seconds."""
    start = frames[0][1]
    work = out.parent / (out.stem + "_work")
    work.mkdir(parents=True, exist_ok=True)
    lines = []
    for k, (f, at) in enumerate(frames):
        nxt = frames[k + 1][1] if k + 1 < len(frames) else end
        lines.append("file '%s'\nduration %.4f" % (f.as_posix(), max(0.001, nxt - at)))
    lines.append("file '%s'" % frames[-1][0].as_posix())       # (the concat demuxer's last-frame rule)
    (work / "frames.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    seconds = end - start
    with wave.open(str(work / "voice.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(mix(voices, start, seconds))
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(work / "frames.txt"),
                    "-i", str(work / "voice.wav"),
                    "-vf", "scale=%d:-2:flags=lanczos,fps=30" % width, "-c:v", "libx264", "-preset", "slow",
                    "-crf", str(crf), "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    "-c:a", "aac", "-b:a", "128k", "-t", "%.3f" % seconds, str(out)], check=True)
    shutil.rmtree(work, ignore_errors=True)
    return seconds


async def run(args) -> int:
    import uvicorn
    from playwright.async_api import async_playwright

    from app import face_mirror, mirror_server
    from app.config import settings

    pv = _module("preview_ceremony_show")
    vl = _module("voice_line")
    ffmpeg = vl.find_ffmpeg()
    settings.mirror_token = secrets.token_urlsafe(12)
    settings.stories_enabled = True
    clips = pv._clips()
    playable = {c["id"]: c for c in pv.lines(outside=True) if c["id"] in clips}   # (--cues: the backup line too)
    hosts = {} if args.no_host else pv._host_takes()
    todo = scenes(playable)
    if args.scenes:
        want = {int(x) for x in args.scenes.split(",") if x.strip()}
        todo = [s for s in todo if s["n"] in want]
    if args.cues:                                   # these lines alone, as one scene (a measuring run)
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
    report = []
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True, args=[
        "--autoplay-policy=no-user-gesture-required", "--ignore-gpu-blocklist", "--enable-gpu", "--use-angle=d3d11"])
    try:
        page = await browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
        page.on("pageerror", lambda e: print("  [page error] %s" % e))
        cdp = await page.context.new_cdp_session(page)
        # Khun Miki's caption layer (the preview's own; the day's page never has it)
        await page.add_init_script(path=str(ROOT / "scripts" / "preview_host_caption.js"))
        await page.goto(url)
        while not face_mirror.watching():
            await asyncio.sleep(0.2)
        await asyncio.sleep(2.0)
        face_mirror.call_started()
        face_mirror.publish_json({"type": "ready", "output_rate": RATE, "profile": "ceremony"})
        await asyncio.sleep(2.0)

        for scene in todo:
            frames: list[tuple[Path, float]] = []
            voices: list[tuple[bytes, float]] = []
            marks: list[tuple[str, float]] = []           # (cue id, wall time her/his voice began)
            shot_dir = Path(tempfile.mkdtemp(prefix="scene%02d_" % scene["n"], dir=out))

            def on_frame(p, shot_dir=shot_dir, frames=frames):
                at = (p.get("metadata") or {}).get("timestamp") or time.time()
                f = shot_dir / ("f%06d.jpg" % len(frames))
                f.write_bytes(base64.b64decode(p["data"]))
                frames.append((f, float(at)))
                asyncio.ensure_future(cdp.send("Page.screencastFrameAck", {"sessionId": p["sessionId"]}))

            cdp.on("Page.screencastFrame", on_frame)
            cap_w, cap_h = (int(x) for x in args.capture.lower().split("x")) if args.capture else (width, height)
            await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": args.quality,
                                                    "maxWidth": cap_w, "maxHeight": cap_h, "everyNthFrame": args.every})
            await asyncio.sleep(1.5)                      # the stage before the first word
            print("scene %d  %s  (%d lines)" % (scene["n"], scene["title"], len(scene["cues"])))
            for cue in scene["cues"]:
                print("   %s  %s" % (cue["id"], cue["en"][:60]))
                await pv.exchange(cue, clips[cue["id"]], hosts.get(cue["id"]), None,
                                  on_voice=lambda pcm, at, cue=cue: (voices.append((pcm, at)), marks.append((cue["id"], at))),
                                  on_caption=lambda text: page.evaluate("t => window.__pvHostCaption(t)", text))
                await asyncio.sleep(args.gap)
            await asyncio.sleep(args.tail)
            end = time.time()
            await cdp.send("Page.stopScreencast")
            cdp.remove_listener("Page.screencastFrame", on_frame)
            face_mirror.subtitle_cut()
            if not frames:
                print("  no frames for scene %d (no GPU frames?)" % scene["n"])
                continue
            # Where the SCREEN stood still: the gaps between the frames Chrome presented (a
            # page whose frame clock runs at 60 can still show a stale picture while the
            # compositor rasters a big layer -- owner 18 Nov, on scene 3: "ไม่เห็นหรอว่ามันกระตุก").
            t0 = frames[0][1]
            stalls = []
            for (_, a), (_, b) in zip(frames, frames[1:]):
                if b - a >= args.stall:
                    cue_id = next((cid for cid, at in reversed(marks) if at <= a), None)
                    since = a - next((at for cid, at in reversed(marks) if at <= a), t0)
                    stalls.append({"at": round(a - t0, 2), "ms": round((b - a) * 1000), "cue": cue_id, "into": round(since, 2)})
            worst = sorted(stalls, key=lambda r: -r["ms"])[:8]
            print("   stalls >= %d ms: %d  worst: %s" % (args.stall * 1000, len(stalls),
                  ", ".join("%d ms @%.1fs (%s +%.1fs)" % (r["ms"], r["at"], r["cue"], r["into"]) for r in worst) or "none"))
            target = out / ("scene_%02d.mp4" % scene["n"])
            if args.no_video:                             # a measuring run: the stalls alone
                if args.keep_frames:                      # ... and the frames themselves, to look at a stall
                    (shot_dir / "frames.json").write_text(json.dumps(
                        [[f.name, round(at - t0, 4)] for f, at in frames]), encoding="utf-8")
                    print("   frames kept: %s" % shot_dir)
                else:
                    shutil.rmtree(shot_dir, ignore_errors=True)
                report.append({"n": scene["n"], "title": scene["title"], "cues": [c["id"] for c in scene["cues"]],
                               "seconds": round(end - t0, 1), "frames": len(frames), "stalls": stalls})
                await asyncio.sleep(1.0)
                continue
            seconds = encode(frames, end, voices, target, ffmpeg, args.width, args.crf)
            shutil.rmtree(shot_dir, ignore_errors=True)
            size = target.stat().st_size
            print("   -> %s  %.1f s  %.1f MB  %d frames (%.0f fps)" % (target.name, seconds, size / 1e6, len(frames),
                                                                    len(frames) / max(1.0, seconds)))
            report.append({"n": scene["n"], "title": scene["title"], "file": target.name,
                           "cues": [{"id": c["id"], "th": c["th"], "en": c["en"],
                                     "host_th": (hosts.get(c["id"]) or {}).get("text")} for c in scene["cues"]],
                           "seconds": round(seconds, 1), "bytes": size, "stalls": stalls})
            await asyncio.sleep(2.0)
        (out / ("stalls.json" if args.no_video else "scenes.json")).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        face_mirror.call_ended()
    finally:
        await browser.close()
        await pw.stop()
        server.should_exit = True
        await serving
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--scenes", default="", help="comma-separated scene numbers of the running order (default: all)")
    ap.add_argument("--out", default=str(ROOT / ".tmp" / "ceremony-show" / "scenes"))
    ap.add_argument("--port", type=int, default=8830)
    ap.add_argument("--size", default="1920x1080", help="the page's screen")
    ap.add_argument("--width", type=int, default=1280, help="the video's width (height follows)")
    ap.add_argument("--crf", type=int, default=24)
    ap.add_argument("--gap", type=float, default=1.5, help="seconds between two lines")
    ap.add_argument("--tail", type=float, default=2.5, help="seconds after a scene's last line")
    ap.add_argument("--no-host", action="store_true", help="without Khun Miki's stand-in questions")
    ap.add_argument("--cues", default="", help="comma-separated cue ids played as one scene (for measuring)")
    ap.add_argument("--no-video", action="store_true", help="measure only: no MP4, stalls.json instead of scenes.json")
    ap.add_argument("--every", type=int, default=1, help="capture every Nth frame Chrome presents (2 = 30 a second)")
    ap.add_argument("--capture", default="", help="the captured picture's size (default: the page's), e.g. 1280x720")
    ap.add_argument("--quality", type=int, default=88, help="the captured JPEGs' quality")
    ap.add_argument("--keep-frames", action="store_true", help="with --no-video: keep the captured frames + frames.json")
    ap.add_argument("--stall", type=float, default=0.1, help="a gap between presented frames this long (s) is a stall")
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
