#!/usr/bin/env python3
"""Is the ceremony film smooth? Chrome's own account of every frame it presented or dropped.

    python scripts/perf_ceremony_frames.py --cues s03_l03,s06_l03
    python scripts/perf_ceremony_frames.py                       # every line of the running order
    python scripts/perf_ceremony_frames.py --cues s02_l03 --host # with Khun Miki's stand-in question first

Why this and not a frame clock or a screen recording (18 Nov 2026, the owner on the scene videos:
"ไม่เห็นหรอว่ามันกระตุก ภาพสะดุด"):
- the page's frame clock (requestAnimationFrame deltas -- what the first fps numbers were) only says
  the main thread was free; it reads 60 while the compositor shows a stale picture;
- Chrome's screencast (CDP Page.startScreencast -- what the first scene videos were recorded with)
  leaves ~250 ms holes of its own whenever what changes on the page changes (its capturer re-learns
  the "animated region"): 112 holes of 267 ms in nine scenes, at the same moments whatever the
  capture cost -- and none of them in the frames Chrome actually drew (60 a second through all of
  them). A recording made that way stutters where the film does not.
So: a trace of the compositor's frame reports (`PipelineReporter`, category cc/benchmark). A frame
is counted against smoothness when Chrome itself says it was DROPPED and `affects_smoothness`.
A STALL here = consecutive dropped frames (>= --stall ms). Runs on this PC's GPU in a headless
Chromium against the real room-screen page, the lines played as scripts/preview_ceremony_show.py
plays them. Nothing talks to the robot or a model.

Runs of this (and any other preview) on one GPU disturb each other: take the lock around a run
(see .tmp/ceremony-show/STALL_PASS_BRIEF.md).
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FRAME_MS = 1000 / 60


def _module(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / ("%s.py" % name))
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


def account(events: list[dict], stall_ms: float) -> list[dict]:
    """Per span between two of our marks ("cue <id>" ... "end <id>"): frames, dropped frames and
    the runs of consecutive drops. `events` = the trace's events."""
    marks = sorted((e["ts"], e["name"]) for e in events
                   if e.get("cat", "").find("blink.user_timing") >= 0 and str(e.get("name", "")).startswith(("cue ", "end ")))
    frames: dict[int, dict] = {}
    for e in events:
        if e.get("name") != "PipelineReporter":
            continue
        fr = (e.get("args") or {}).get("frame_reporter")
        if not fr or "frame_sequence" not in fr:
            continue
        seq = int(fr["frame_sequence"])
        row = frames.setdefault(seq, {"ts": e["ts"], "dropped": False, "smooth": False})
        row["ts"] = min(row["ts"], e["ts"])
        # (a frame is reported once per reporter -- main and compositor "forked": presented by
        # either = on the screen; dropped only if no reporter presented it)
        state = str(fr.get("state") or "")
        if state.startswith("STATE_PRESENTED"):
            row["presented"] = True
        elif state == "STATE_DROPPED":
            row["dropped"] = True
            row["smooth"] = row["smooth"] or bool(fr.get("affects_smoothness"))
    order = sorted(frames)
    out = []
    spans = []
    for i, (ts, name) in enumerate(marks):
        if name.startswith("cue "):
            end = next((t for t, n in marks[i + 1:] if n == "end " + name[4:]), None)
            if end is not None:
                spans.append((name[4:], ts, end))
    for cue, begin, end in spans:
        inside = [s for s in order if begin <= frames[s]["ts"] <= end]
        lost = [s for s in inside if frames[s]["dropped"] and frames[s]["smooth"] and not frames[s].get("presented")]
        runs, run = [], []
        for s in lost:
            if run and s == run[-1] + 1:
                run.append(s)
            else:
                if run:
                    runs.append(run)
                run = [s]
        if run:
            runs.append(run)
        stalls = [{"into": round((frames[r[0]]["ts"] - begin) / 1e6, 2), "ms": round(len(r) * FRAME_MS)}
                  for r in runs if len(r) * FRAME_MS >= stall_ms]
        out.append({"cue": cue, "seconds": round((end - begin) / 1e6, 1), "frames": len(inside), "dropped": len(lost),
                    "smooth_pct": round(100 * (1 - len(lost) / max(1, len(inside))), 1), "stalls": stalls})
    return out


async def run(args) -> int:
    import uvicorn
    from playwright.async_api import async_playwright

    from app import face_mirror, mirror_server
    from app.config import settings

    pv = _module("preview_ceremony_show")
    settings.mirror_token = secrets.token_urlsafe(12)
    settings.stories_enabled = True
    clips = pv._clips()
    only = [x.strip() for x in args.cues.split(",") if x.strip()] or None
    todo = [c for c in pv.lines(only) if c["id"] in clips]
    hosts = pv._host_takes() if args.host else {}
    if not todo:
        print("nothing to measure")
        return 2
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
    rows: list[dict] = []
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True, args=[
        "--autoplay-policy=no-user-gesture-required", "--ignore-gpu-blocklist", "--enable-gpu", "--use-angle=d3d11"])
    try:
        page = await browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
        await page.add_init_script(path=str(ROOT / "scripts" / "preview_host_caption.js"))
        cdp = await page.context.new_cdp_session(page)
        await page.goto(url)
        while not face_mirror.watching():
            await asyncio.sleep(0.2)
        await asyncio.sleep(2.0)
        face_mirror.call_started()
        face_mirror.publish_json({"type": "ready", "output_rate": pv.RATE, "profile": "ceremony"})
        await asyncio.sleep(3.0)
        for cue in todo:
            events: list[dict] = []
            done = asyncio.Event()

            def collected(p, events=events):
                events.extend(p.get("value") or [])

            def complete(p, done=done):
                done.set()

            cdp.on("Tracing.dataCollected", collected)
            cdp.on("Tracing.tracingComplete", complete)
            await cdp.send("Tracing.start", {"transferMode": "ReportEvents", "traceConfig": {
                "includedCategories": ["cc", "benchmark", "blink.user_timing"], "recordMode": "recordAsMuchAsPossible"}})
            # the whole exchange is the span: the cut in, his question (if any), her line, the line going down
            await page.evaluate("n => performance.mark(n)", "cue " + cue["id"])
            await pv.exchange(cue, clips[cue["id"]], hosts.get(cue["id"]), None,
                              on_caption=lambda text: page.evaluate("t => window.__pvHostCaption(t)", text))
            await asyncio.sleep(args.tail)
            await page.evaluate("n => performance.mark(n)", "end " + cue["id"])
            await asyncio.sleep(0.2)
            await cdp.send("Tracing.end")
            await asyncio.wait_for(done.wait(), 120)
            cdp.remove_listener("Tracing.dataCollected", collected)
            cdp.remove_listener("Tracing.tracingComplete", complete)
            got = account(events, args.stall)
            for row in got:
                rows.append(row)
                print("%-18s %5.1f s  %5d frames  dropped %3d  smooth %5.1f%%  stalls>=%d ms: %s" % (
                    row["cue"], row["seconds"], row["frames"], row["dropped"], row["smooth_pct"], args.stall,
                    ", ".join("%d ms @+%.1fs" % (s["ms"], s["into"]) for s in row["stalls"]) or "none"))
            if not got:
                print("%-18s no frame reports in the trace (no GPU frames?)" % cue["id"])
            await asyncio.sleep(1.0)
        face_mirror.call_ended()
    finally:
        await browser.close()
        await pw.stop()
        server.should_exit = True
        await serving
    if args.json:
        Path(args.json).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(r["frames"] for r in rows)
    lost = sum(r["dropped"] for r in rows)
    print("\n%d lines, %d frames, dropped %d (%.2f%%), stalls: %d" % (
        len(rows), total, lost, 100 * lost / max(1, total), sum(len(r["stalls"]) for r in rows)))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cues", default="", help="comma-separated cue ids (default: every line)")
    ap.add_argument("--host", action="store_true", help="Khun Miki's stand-in question before each line (the logo clip plays under it)")
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--port", type=int, default=8850)
    ap.add_argument("--tail", type=float, default=2.5, help="seconds measured after a line's end (the line going down, the logo coming in)")
    ap.add_argument("--stall", type=float, default=50.0, help="consecutive dropped frames this long (ms) are a stall")
    ap.add_argument("--json", default="", help="write the rows here")
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
