"""Video from the robot's head camera, recorded on the PC while the robot works.

    python scripts/robot_cam_video.py record lobby_1104 [--minutes 45]
    python scripts/robot_cam_video.py stop lobby_1104      # from another shell (or Ctrl+C)

Owner 4 Nov 2026 night, on the floor-1 lobby survey: "ดูภาพหรือถ่ายวิดีโอจากหุ่นเลย"
then "อยากให้แสกนทั้ง lobby โดยละเอียดเก็บภาพ บันทึกวิดีโอ". The walk pictures
(WALK_FRAMES) are ~1.6 a second at best -- each one also fetches the lidar and
the pose -- so they are a record, not a video.

How: the face page in the Emma app already holds the head camera
(`window.__emmaCamStream`, the stream it sends to /ws/camera). Through the app
WebView's DevTools socket (`adb forward` to webview_devtools_remote_<pid>) a
MediaRecorder is started on THAT stream -- no second camera is opened, nothing
in the app or the page changes, and a page reload simply ends it. Chunks of
CHUNK_S come back over DevTools into .tmp/walk-video/<name>.webm (gitignored:
the pictures have people in them -- never commit them), then ffmpeg (if on
PATH, or FFMPEG=...) makes <name>.mp4 and a 4x <name>_x4.mp4.

What it never does: print the page's URL (it carries the WS token), keep more
than MAX_PAGE_CHUNKS unread chunks in the page (a stalled PC must not grow the
WebView's memory: newer chunks are dropped and counted instead), or touch the
robot's wheels, head or arms. Measured 4 Nov: 1024x768 at 30 fps, VP8 at
1.2 Mbit/s = ~9 MB a minute; 20 min of survey = 180 MB, nothing dropped."""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / ".tmp" / "walk-video"
#: The three Emma apps built from one source (-PemmaApp): whichever is running holds the face page and
#: the camera (opening one closes the other two). 9 Nov: the walk-up dry run is in Emma Event's calls.
APPS = ("com.emma.robot.control", "com.emma.robot.event", "com.emma.robot.ceremony")
PORT = 9333
CHUNK_S = 5
MAX_PAGE_CHUNKS = 24
BITRATE = 1_200_000

START_JS = """(function(){
  if (window.__camVideo && window.__camVideo.rec && window.__camVideo.rec.state === 'recording') return 'already';
  var s = window.__emmaCamStream; if (!s) return 'no camera';
  var st = {chunks: [], dropped: 0, rec: null, n: 0};
  var rec = new MediaRecorder(s, {mimeType: 'video/webm;codecs=vp8', videoBitsPerSecond: %d});
  rec.ondataavailable = function (e) {
    if (!e.data || !e.data.size) return;
    if (st.chunks.length >= %d) { st.dropped++; return; }
    st.chunks.push(e.data); st.n++;
  };
  rec.start(%d); st.rec = rec; window.__camVideo = st; return 'started';
})()""" % (BITRATE, MAX_PAGE_CHUNKS, CHUNK_S * 1000)

PULL_JS = """(async function(){
  var st = window.__camVideo; if (!st) return JSON.stringify({gone: true});
  var b = st.chunks.shift();
  var out = {left: st.chunks.length, dropped: st.dropped, state: st.rec ? st.rec.state : null, n: st.n};
  if (b) {
    var buf = new Uint8Array(await b.arrayBuffer()); var s = '';
    for (var i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
    out.b64 = btoa(s);
  }
  return JSON.stringify(out);
})()"""

STOP_JS = ("(function(){var st=window.__camVideo; if(st&&st.rec&&st.rec.state!=='inactive'){st.rec.stop();"
           " return 'stopping';} return 'none';})()")
CLEAR_JS = "window.__camVideo = null; 'cleared'"


def adb_exe() -> str:
    for cand in (ROOT / ".tmp/android-toolchain/sdk/platform-tools/adb.exe",
                 ROOT / "tools/android/platform-tools/adb.exe"):
        if cand.is_file():
            return str(cand)
    return shutil.which("adb") or "adb"


def adb(serial: str, *args: str) -> str:
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    return subprocess.run([adb_exe(), "-s", serial, *args], capture_output=True, text=True,
                          encoding="utf-8", env=env, timeout=30).stdout


def forward(serial: str) -> None:
    """The app's WebView DevTools socket -> 127.0.0.1:PORT (the socket name
    carries the app's pid; /proc/net/unix needs root on this build)."""
    pid = next((p for p in (adb(serial, "shell", "pidof", app).strip() for app in APPS) if p.isdigit()), "")
    if not pid:
        raise SystemExit("no Emma app is running on the robot (%s)" % ", ".join(APPS))
    sock = "webview_devtools_remote_%s" % pid
    if sock not in adb(serial, "shell", "su 0 sh -c 'grep -o %s /proc/net/unix | head -1'" % sock):
        raise SystemExit("no WebView DevTools socket for the app (pid %s)" % pid)
    adb(serial, "forward", "tcp:%d" % PORT, "localabstract:%s" % sock)


def page_socket(pages: list[dict]) -> str:
    """The face page's DevTools websocket. Only the websocket URL is used --
    the page URL (it carries the token) is never printed or kept."""
    for p in pages:
        if p.get("type") == "page" and "/preview" in (p.get("url") or ""):
            return p["webSocketDebuggerUrl"]
    raise SystemExit("the face page is not open in the app")


class Page:
    def __init__(self) -> None:
        import websocket                      # websocket-client: the eval's own dependency

        pages = json.load(urlopen("http://127.0.0.1:%d/json" % PORT, timeout=10))
        self.ws = websocket.create_connection(page_socket(pages), suppress_origin=True, timeout=30)
        self.n = 0

    def ev(self, expr: str, wait: bool = False):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": "Runtime.evaluate",
                                 "params": {"expression": expr, "returnByValue": True, "awaitPromise": wait}}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self.n:
                return m.get("result", {}).get("result", {}).get("value")


def stop_file(name: str) -> Path:
    return OUT_DIR / (name + ".webm.stop")


def record(name: str, minutes: float, serial: str) -> list[Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stop_file(name).unlink(missing_ok=True)
    forward(serial)
    page = Page()
    started = page.ev(START_JS)
    print("recorder:", started, flush=True)
    if started not in ("started", "already"):
        raise SystemExit("could not start: %s" % started)
    parts = [OUT_DIR / (name + ".webm")]
    fh = open(parts[-1], "wb")
    t0, total, stopping, last_note = time.monotonic(), 0, False, 0.0
    try:
        while True:
            if not stopping and (stop_file(name).exists() or time.monotonic() - t0 > minutes * 60):
                stopping = True
                try:
                    print("stop:", page.ev(STOP_JS), flush=True)
                except Exception as e:        # noqa: BLE001 -- 4 Nov: the robot rebooted, the page went with
                    print("stop: the page is gone (%s) -- keeping what came" % type(e).__name__, flush=True)
                    break                     # its recorder; what was pulled is on disk
                time.sleep(1.5)
            try:
                r = json.loads(page.ev(PULL_JS, wait=True) or "{}")
            except Exception as e:            # noqa: BLE001 -- adb or the page hiccuped: reconnect
                if stopping:
                    break                     # nothing more will come
                print("pull failed (%s) -- reconnecting" % type(e).__name__, flush=True)
                time.sleep(3)
                try:
                    forward(serial)
                    page = Page()
                except BaseException as e2:   # noqa: BLE001 -- SystemExit too: the app is restarting
                    print("  not back yet (%s)" % type(e2).__name__, flush=True)
                continue
            if r.get("gone"):
                if stopping:
                    break
                # the page reloaded: the recorder went with it -- a new part
                fh.close()
                parts.append(OUT_DIR / ("%s.part%d.webm" % (name, len(parts) + 1)))
                fh = open(parts[-1], "wb")
                time.sleep(2)
                print("page reloaded -- new part:", page.ev(START_JS), flush=True)
                continue
            if r.get("b64"):
                data = base64.b64decode(r["b64"])
                fh.write(data)
                fh.flush()
                total += len(data)
                continue                      # drain a backlog first
            if stopping and r.get("state") == "inactive" and not r.get("left"):
                break
            if time.monotonic() - last_note >= 60:
                last_note = time.monotonic()
                print("%4.0f s: %.1f MB, dropped %s" % (last_note - t0, total / 1e6, r.get("dropped")), flush=True)
            time.sleep(2.5)
    except KeyboardInterrupt:
        try:
            print("stop (Ctrl+C):", page.ev(STOP_JS), flush=True)
        except Exception:                     # noqa: BLE001 -- the page is gone
            pass
    finally:
        fh.close()
        try:
            page.ev(CLEAR_JS)
        except Exception:                     # noqa: BLE001 -- the page is gone: nothing to clear
            pass
    print("done: %.1f MB in %d part(s), %.0f s" % (total / 1e6, len(parts), time.monotonic() - t0), flush=True)
    return parts


def ffmpeg_exe() -> str | None:
    return os.environ.get("FFMPEG") or shutil.which("ffmpeg")


def to_mp4(webm: Path) -> list[Path]:
    """<name>.mp4 (H.264, the MediaRecorder's webm has no duration) and a 4x
    <name>_x4.mp4 to look through quickly. No ffmpeg = the webm stays."""
    ff = ffmpeg_exe()
    if not ff or not webm.is_file() or not webm.stat().st_size:
        return []
    out = [webm.with_suffix(".mp4"), webm.with_name(webm.stem + "_x4.mp4")]
    common = ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an"]
    subprocess.run([ff, "-v", "error", "-y", "-i", str(webm), "-crf", "25", *common, str(out[0])], check=True)
    subprocess.run([ff, "-v", "error", "-y", "-i", str(webm), "-vf", "setpts=PTS/4", "-r", "30", "-crf", "26",
                    *common, str(out[1])], check=True)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("what", choices=["record", "stop"])
    ap.add_argument("name", help="file name under .tmp/walk-video (letters, digits, - and _)")
    ap.add_argument("--minutes", type=float, default=45.0)
    ap.add_argument("--serial", default="192.168.0.2:5555")
    ap.add_argument("--no-mp4", action="store_true")
    args = ap.parse_args(argv)
    if not args.name.replace("-", "").replace("_", "").isalnum():
        raise SystemExit("name: letters, digits, - and _ only")
    if args.what == "stop":
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        stop_file(args.name).touch()
        print("asked %s to stop" % args.name)
        return 0
    parts = record(args.name, args.minutes, args.serial)
    if not args.no_mp4:
        for part in parts:
            for made in to_mp4(part):
                print("wrote", made.relative_to(ROOT))
        if not ffmpeg_exe():
            print("no ffmpeg on PATH (or FFMPEG=...): the .webm plays in a browser")
    stop_file(args.name).unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
