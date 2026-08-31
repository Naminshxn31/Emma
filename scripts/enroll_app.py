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
import base64
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

#: Shots captured but not yet saved. One station, one operator, one person in
#: front of the lens — a dict keyed by anything would be pretending
#: otherwise. Cleared on save and on discard.
_pending: list[dict] = []
_lock = threading.Lock()


def _decode(data_url: str) -> np.ndarray | None:
    import cv2

    _, _, payload = data_url.partition(",")
    try:
        raw = base64.b64decode(payload)
    except Exception:
        return None
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)


def _thumb(crop: np.ndarray, size: int = 160) -> str:
    import cv2

    h, w = crop.shape[:2]
    scale = size / max(h, w, 1)
    small = cv2.resize(crop, (max(1, int(w * scale)), max(1, int(h * scale))))
    ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode() if ok else ""


def _reference_width() -> int:
    """The face width from the first shot. 0 before there is one."""
    return _pending[0]["width"] if _pending else 0


def _remaining() -> list[dict]:
    """Steps not taken yet, minus the ones this camera cannot offer.

    A step nobody can satisfy is not a step. The distance shot needs room to
    step back into, and on a desk camera there is none: the sitter's face was
    119px, the step wanted 89px, and `FACE_MIN_PX` refuses anything under
    110px. Somebody stood there trying to obey an instruction that had no
    answer, which is worse than not asking.
    """
    done = {p.get("step") for p in _pending}
    reference = _reference_width()
    return [s for s in enrollment.STEPS
            if s["key"] not in done
            and (not reference or enrollment.step_applies(s, reference))]


def _state() -> dict:
    """Which step the station is on, and what has been taken so far."""
    done = {p.get("step") for p in _pending}
    reference = _reference_width()
    steps = []
    for step in enrollment.STEPS:
        skipped = bool(reference) and not enrollment.step_applies(step, reference)
        steps.append({
            "key": step["key"], "done": step["key"] in done, "skipped": skipped,
            "prompt": step["prompt"] + (" — ข้าม (กล้องใกล้เกินไป)" if skipped else ""),
        })
    todo = _remaining()
    # The hold time belongs to the step, not to the page: the step that asks
    # for a smile is the one nothing here can check, and the page has no way
    # to know which one that is.
    return {"steps": steps, "step": todo[0]["key"] if todo else None,
            "hold_ms": enrollment.hold_ms(todo[0]) if todo else enrollment.HOLD_MS,
            "prompt": todo[0]["prompt"] if todo else "ครบแล้ว — กดบันทึกได้"}


def _current_step() -> dict | None:
    todo = _remaining()
    return todo[0] if todo else None


def _scale_to_camera(frame: np.ndarray, full_width: int | None) -> float:
    """How many camera pixels one analysed pixel is worth.

    The preview loop sends a 640-wide copy because a box is all it needs;
    a capture sends the whole frame. Measuring face width on each of those
    and comparing the two numbers to the same thresholds was wrong twice
    over: `FACE_MIN_PX` silently doubled during the preview, so people had
    to sit far closer than the runtime will ever require, and the distance
    step compared a 640-scale width against a reference stored at full
    scale — the screen said 155px while the instruction said 307px, about
    the same face in the same second.

    So every width this module reasons about is in camera pixels.
    """
    if not full_width or frame.shape[1] <= 0:
        return 1.0
    return float(full_width) / float(frame.shape[1])


def _judge(frame: np.ndarray, full_width: int | None = None) -> dict:
    """Everything the preview needs, decided here rather than in the page.

    The browser owns the camera and the drawing; it does not own any rule
    about whether a shot counts. Keeping the judgement on this side is what
    lets the command line and the station stay one implementation.
    """
    found = faces.locate(frame)
    scale = _scale_to_camera(frame, full_width)
    base = {"box": None, "width": 0, "yaw": 0.0,
            "frame": [int(frame.shape[1]), int(frame.shape[0])]}
    if not found:
        return {**base, "ok": False, "reason": "ยังไม่เห็นหน้า",
                "say": enrollment.SPOKEN["no_face"], **_state()}

    face = found[0]
    width = int(round((face.bbox[2] - face.bbox[0]) * scale))
    base.update(box=list(face.bbox), width=width)

    if len(found) > 1:
        runner_up = found[1].area
        if runner_up > 0 and face.area < faces.DOMINANT_RATIO * runner_up:
            return {**base, "ok": False,
                    "reason": f"มี {len(found)} หน้าในภาพ — ทีละคน",
                    "say": enrollment.SPOKEN["crowd"], **_state()}
    if width < enrollment.portrait_min_px():
        return {**base, "ok": False,
                "reason": f"เข้ามาใกล้อีกนิด ({width}px ขอ {enrollment.portrait_min_px()}px)",
                "say": enrollment.SPOKEN["closer"], **_state()}
    if not _pending and width < enrollment.first_shot_min_px():
        # The first shot sets the scale every later step is measured
        # against, so it is the one place to insist on room: a first
        # portrait this small has no step-back, and the station used to
        # find that out only after promising five steps and delivering
        # four. One more step toward the lens now is the whole fix.
        return {**base, "ok": False,
                "reason": f"เข้ามาใกล้อีกนิด ({width}px ขอ "
                          f"{enrollment.first_shot_min_px()}px เผื่อท่าถอยหลัง)",
                "say": enrollment.SPOKEN["closer"], **_state()}
    if face.kps is None:
        return {**base, "ok": False, "reason": "อ่านใบหน้าไม่ได้",
                "say": enrollment.SPOKEN["unreadable"], **_state()}

    yaw, _pitch = enrollment.head_pose(face)
    base["yaw"] = round(yaw, 3)

    step = _current_step()
    if step is None:
        return {**base, "ok": False, "reason": "ครบแล้ว — กดบันทึกได้",
                "say": enrollment.SPOKEN["done"], **_state()}

    with _lock:
        sides = {p["side"] for p in _pending if p["side"]}
        reference = _reference_width() or width
    ready, why = enrollment.step_ready(step, yaw, width, reference, sides)
    # What to say is the instruction itself, never the version of it carrying
    # pixel counts: those are for whoever is running the station.
    return {**base, "ok": ready, "reason": "" if ready else why,
            "say": "" if ready else step["prompt"], **_state()}


def _capture(frame: np.ndarray, full_width: int | None = None) -> dict:
    scale = _scale_to_camera(frame, full_width)
    need = (enrollment.first_shot_min_px() if not _pending
            else enrollment.portrait_min_px())
    face, why = enrollment.usable_face(frame, min_px=need / scale)
    if face is None:
        return {"ok": False, "reason": why, **_state()}
    yaw, _ = enrollment.head_pose(face)
    width = int(round((face.bbox[2] - face.bbox[0]) * scale))

    with _lock:
        step = _current_step()
        if step is None:
            return {"ok": False, "reason": "ครบแล้ว — กดบันทึกได้", **_state()}
        sides = {p["side"] for p in _pending if p["side"]}
        reference = _reference_width() or width
        ready, why = enrollment.step_ready(step, yaw, width, reference, sides)
        if not ready:
            return {"ok": False, "reason": why, **_state()}
        # A shot that the batch already has teaches the gallery nothing, and
        # five of those are one photograph counted five times. Held to the
        # same standard whether the operator pressed the button or the hold
        # timer fired.
        if not enrollment.is_novel(face.vec, [p["vec"] for p in _pending]):
            return {"ok": False, "reason": "ภาพนี้เหมือนใบเดิม ลองขยับอีกนิด",
                    **_state()}
        _pending.append({"vec": face.vec, "crop": enrollment.crop_face(frame, face),
                         "step": step["key"], "width": width,
                         "side": 0 if step["side"] == 0 else (-1 if yaw < 0 else 1)})
        shots = [{"thumb": _thumb(p["crop"]), "step": p["step"]} for p in _pending]
        spread = enrollment.shots_agree([p["vec"] for p in _pending])
    return {"ok": True, "shots": shots, "spread": round(spread, 3), **_state()}


def _save(name: str, replace: bool) -> dict:
    name = (name or "").strip()
    if not name:
        return {"ok": False, "reason": "ยังไม่ได้ใส่ชื่อ"}
    with _lock:
        if not _pending:
            return {"ok": False, "reason": "ยังไม่ได้เก็บภาพ"}
        vecs = [p["vec"] for p in _pending]
        crops = [p["crop"] for p in _pending]

    refusal = enrollment.review(vecs, name)
    if refusal:
        return {"ok": False, "reason": refusal}

    total = enrollment.save_shots(name, crops, replace=replace)
    with _lock:
        _pending.clear()
    return {"ok": True, "name": name, "total": total,
            "spread": round(enrollment.shots_agree(vecs), 3),
            "enrolled": enrollment.enrolled(), **_state()}


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
            with _lock:
                _pending.clear()
            self._send({"ok": True, **_state()})
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
