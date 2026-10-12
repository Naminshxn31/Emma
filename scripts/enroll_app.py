#!/usr/bin/env python3
"""
The enrolment station: open it once, enrol everybody, close it.

    python scripts/enroll_app.py          # opens http://127.0.0.1:8770

Why this is a page and not the command line
-------------------------------------------
`scripts/enroll_face.py` takes one name per run, which means walking back to
a terminal between colleagues — forty-three round trips for the staff
photographs, with the person being enrolled standing there for each one.

But the reason it *has* to be a page is narrower than convenience: the names
are Thai. `cv2.putText` has no Thai glyphs and `cv2.waitKey` cannot receive
Thai keystrokes, so an OpenCV window can neither show a name nor accept one,
ever. The same conclusion `units.py` reached for the room card and
`label_staff_faces.py` reached for the naming sheet — when a screen has to
carry Thai and be read from where somebody is standing, this project serves
its own HTML.

The browser pays for itself twice over: `enumerateDevices()` gives real
device names, so "Elgato Facecam" is picked from a list instead of guessed
at as a capture index. That was a whole afternoon on the owner's machine,
where index 0 is a virtual camera showing a picture of a logo.

What is deliberately *not* separate
-----------------------------------
Every rule about when a face may be enrolled lives in `app/enrollment.py`
and is shared with the command line. Two front ends, one set of refusals.
Forking them would be the four-exits bug again: each copy correct on the day
it was written and quietly different a month later.

The guided capture went the same way when the robot's call screen needed
it: the steps, the judging and the save are `enrollment.Session`, and this
file holds one (`SESSION`) and serves HTTP. The `_judge`/`_capture`/`_save`
names below are one-line forwards, kept so nothing that called them breaks.

Bound to 127.0.0.1, always
--------------------------
This thing looks at faces and files them under names. It is not on the
network and there is no flag to put it there — `warn_if_open_to_the_network`
exists in this project because a default of `HOST=0.0.0.0` with `WS_TOKEN=""`
shipped once already. It is also a separate process from `app.main`, so the
showroom server is byte-identical to what a `git pull` gives anybody else.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import enrollment, faces, voice  # noqa: E402
from app.config import settings  # noqa: E402

PAGE = (ROOT / "scripts" / "enroll_app.html")

#: Bumped whenever the page and this file have to change together.
#:
#: The page is read off disk on every request; this module is loaded once,
#: when the station starts. So a station left running across an edit serves
#: the *new* page against the *old* server, and the new page's features
#: quietly do not exist — no step list, no novelty check, no limit on how
#: many shots pile up. That happened, and the page said nothing because its
#: fetch errors were swallowed. The page checks this number on load and
#: refuses to run against a server that does not match.
PROTOCOL = 8

#: The person in front of the lens, and the shots taken of them so far. One
#: station, one operator, one person — so one `Session`, and every rule it
#: applies lives in `app/enrollment.py` beside the server endpoint's.
SESSION = enrollment.Session()

# The same function objects, not copies: a copy here is the drift the
# module docstring is about.
_decode = enrollment.decode_data_url
_thumb = enrollment.thumbnail
_scale_to_camera = enrollment.scale_to_camera


def _state() -> dict:
    return SESSION.state()


def _judge(frame: np.ndarray, full_width: int | None = None) -> dict:
    return SESSION.judge(frame, full_width)


def _capture(frame: np.ndarray, full_width: int | None = None) -> dict:
    return SESSION.capture(frame, full_width)


def _save(name: str, replace: bool) -> dict:
    # Without add_to_gallery: the station's documented second step is the
    # rebuild (build-face-gallery.cmd, printed at startup), which re-embeds
    # every folder anyway. The live append exists for the call screen,
    # where the person enrolled is standing there waiting to be known.
    #
    # existing_ok: this station filed names before the name rule existed
    # (`Tom_2`, a long Thai name with a title), and the rule then refused
    # to let it top those people up — the exact name failed the rule and
    # the tidied one was refused as a second name for the same face. An
    # exact, existing folder inside enrolled/ is accepted here; the call
    # screen never passes this.
    return SESSION.save(name, replace=replace, existing_ok=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):  # quiet: one line per analysed frame is noise
        pass

    def _send(self, payload: dict | bytes, content_type="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(
            payload, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path in ("/", "/index.html"):
            self._send(PAGE.read_bytes(), "text/html; charset=utf-8")
        elif path == "/version":
            # `speaks` is what the page's notice is built from. Asking here
            # beats finding out one silent sentence at a time.
            self._send({"protocol": PROTOCOL, "speaks": bool(settings.gemini_api_key)})
        elif path == "/enrolled":
            self._send({"enrolled": enrollment.enrolled()})
        elif path == "/voice":
            wanted = parse_qs(query).get("text", [""])[0]
            audio = voice.clip(wanted)
            if audio is None:
                # 404 is the honest answer and the page has a beep for it.
                # Nothing about a sentence that cannot be rendered should
                # stop somebody being enrolled.
                self.send_error(404)
                return
            self._send(audio.read_bytes(), "audio/wav")
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self.send_error(400)
            return

        if self.path == "/look":
            frame = _decode(body.get("image", ""))
            self._send(_judge(frame, body.get("fullWidth")) if frame is not None
                       else {"ok": False, "reason": "bad frame", "box": None, **_state()})
        elif self.path == "/capture":
            frame = _decode(body.get("image", ""))
            self._send(_capture(frame, body.get("fullWidth")) if frame is not None
                       else {"ok": False, "reason": "bad frame"})
        elif self.path == "/save":
            self._send(_save(body.get("name", ""), bool(body.get("replace"))))
        elif self.path == "/discard":
            self._send(SESSION.discard())
        else:
            self.send_error(404)


def _already_running(port: int) -> int | None:
    """The protocol of a station already on this port, or None if it is free.

    -1 means something is listening but it is not one of these — an old
    build with no `/version`, or an unrelated program.
    """
    import socket
    import urllib.error
    import urllib.request

    with socket.socket() as probe:
        probe.settimeout(0.4)
        if probe.connect_ex(("127.0.0.1", port)) != 0:
            return None
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/version",
                                    timeout=1.5) as response:
            return int(json.loads(response.read())["protocol"])
    except (urllib.error.URLError, ValueError, KeyError, TimeoutError, OSError):
        return -1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--no-open", action="store_true", help="do not open a browser")
    args = ap.parse_args()

    if not faces.available():
        print("face models not found — run scripts/fetch_face_model.py")
        return 1
    if not PAGE.is_file():
        print(f"missing {PAGE}")
        return 1

    url = f"http://127.0.0.1:{args.port}/"
    running = _already_running(args.port)
    if running is not None:
        # Binding would raise "address already in use" and the window would
        # close on a traceback, while the browser carried on talking to the
        # station that is already there. That is how a new page ended up
        # paired with an old server for a whole enrolment session.
        if running == PROTOCOL:
            print(f"a station is already running at {url} — using that one.")
            if not args.no_open:
                webbrowser.open(url)
            return 0
        print(f"An OLDER station is already running at {url}"
              f" (protocol {running}, this one is {PROTOCOL}).")
        print("Close its window first — look for another 'Condo Voice - Enrol"
              " faces' console.")
        print("If you cannot find it:")
        print(f"    powershell \"Get-NetTCPConnection -LocalPort {args.port} "
              f"-State Listen | ForEach-Object {{ Stop-Process -Id "
              f"$_.OwningProcess -Force }}\"")
        print("Then run this again.")
        return 1

    # 127.0.0.1 and nothing else. See the module docstring.
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as exc:
        print(f"could not listen on {url}: {exc}")
        print(f"something else has the port. Try:  enroll_app.py --port {args.port + 1}")
        return 1
    print(f"enrolment station: {url}")
    print(f"gallery threshold {settings.face_threshold:.2f}, "
          f"min face {settings.face_min_px}px, {settings.face_threads} threads")
    already = enrollment.enrolled()
    if already:
        print("already enrolled from a camera: "
              + ", ".join(f"{n} ({c})" for n, c in already))
    print("\nwhen you are done:  python scripts/build_face_gallery.py")
    print("ctrl-c to stop\n")
    if settings.gemini_api_key:
        # On a thread, and started before the browser opens: the first
        # instruction is spoken about a second after somebody stands there,
        # and rendering it must not be something a request waits behind.
        # Every line after the first run is a cache hit.
        print(f"voice: {voice.state()}")
        threading.Thread(target=voice.warm, daemon=True).start()
    else:
        print("no GEMINI_API_KEY — the station will beep instead of speaking")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass
    sys.exit(main())
