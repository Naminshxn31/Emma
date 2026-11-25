#!/usr/bin/env python3
"""
The launch event's scan booth (docs/emma-face-approach.md).

    python scripts/booth_app.py            # opens http://127.0.0.1:8771

    python scripts/booth_app.py --check    # everything a rehearsal needs, robot included
    python scripts/booth_app.py --new-key  # a GUEST_SYNC_KEY, printed only
    python scripts/booth_app.py --list     # names on this booth, to withdraw one
    python scripts/booth_app.py --wipe     # after the event, a booth that will not be opened again

In .env of the booth machine (nothing starts without the first; see `main`):

    BOOTH_CONSENT_UNTIL=2027-12-02T00:00:00+07:00   when every guest's consent ends
    BOOTH_CONTROLLER=...                            who holds the data (on the notice)
    BOOTH_CONTACT=...                               whom a guest asks (on the notice)
    BOOTH_PIN=...                                   staff, to withdraw somebody
    GUEST_SYNC_URL=https://<robot ip>:8004          the robot's listener
    GUEST_SYNC_KEY=...                              the same key as the robot's .env
    GUEST_SYNC_CERT=certs/lan-cert.pem              the certificate deploy sends the robot
    BOOTH_RETURN_URL=https://.../kiosk              the check-in kiosk on this PC that hands guests over

and the owner's notice in data/policies/event-guest-v1.txt: no file, no scan, and
a blank in it this machine cannot fill, no scan. Bound to 127.0.0.1 and no flag for it, for
enroll_app.py's reason and one more: a browser opens a camera only on https or
localhost. Every rule is in app/booth.py and app/enrollment.py.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import booth, face_scan, faces, voice  # noqa: E402
from app.config import settings  # noqa: E402

PAGE = ROOT / "client" / "booth.html"
#: Larger than a frame (enroll_app.py has no limit: it trusts its operator; this
#: one is a public screen's page).
MAX_BODY = 8 * 1024 * 1024

BOOTH: booth.Booth | None = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _send(self, payload, content_type="application/json", status=200):
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # Never inside another page: a frame of this booth could start a scan under
        # somebody else's page (review 23 Nov).
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _own_page(self) -> bool:
        """Another site's page can POST to 127.0.0.1 and cannot be stopped by the
        bind. A browser says where it came from; only this page may act. And the
        Host must be this machine's own name for itself: a page on a name that
        re-resolves to 127.0.0.1 (DNS rebinding) is its own origin to the browser,
        Origin == Host and all (review 23 Nov)."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        if host not in ("127.0.0.1", "localhost"):
            return False
        origin = self.headers.get("Origin")
        return origin is None or urlsplit(origin).netloc == self.headers.get("Host")

    def do_GET(self):
        if not self._own_page():
            self.send_error(403)
            return
        path, _, query = self.path.partition("?")
        if path in ("/", "/index.html"):
            self._send(PAGE.read_bytes(), "text/html; charset=utf-8")
        elif path == "/status":
            self._send(BOOTH.status())
        elif path == "/voice":
            audio = voice.clip(parse_qs(query).get("text", [""])[0])
            if audio is None:
                self.send_error(404)
            else:
                self._send(audio.read_bytes(), "audio/wav")
        else:
            self.send_error(404)

    def _hand_over(self, length: int) -> None:
        """POST /handoff from the check-in kiosk on this PC (a form, so a navigation:
        Chrome's local-network gate does not stop it, measured on 151). Its Origin is
        "null" there, so it cannot be told from any other page; what it can do is
        only a name in the box of a scan the guest still has to agree to. The Host
        check (rebinding) holds as for everything else."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        if host not in ("127.0.0.1", "localhost") or length > 4096:
            self.send_error(403)
            return
        form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        BOOTH.hand_over((form.get("guest") or [""])[0])
        self.send_response(303)
        self.send_header("Location", "/#handoff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        if self.path == "/handoff":
            self._hand_over(length)
            return
        if not self._own_page() or length > MAX_BODY:
            self.send_error(403)
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            self.send_error(400)
            return
        routes = {
            "/start": lambda: BOOTH.start(body.get("lang")),
            "/lang": lambda: BOOTH.set_lang(body.get("lang")),
            "/consent": lambda: BOOTH.answer(body.get("agree")),
            "/look": lambda: BOOTH.look(body.get("image", ""), body.get("fullWidth")),
            "/capture": lambda: BOOTH.capture(body.get("image", ""), body.get("fullWidth")),
            "/save": lambda: BOOTH.save(body.get("name")),
            "/cancel": lambda: BOOTH.cancel(),
            "/revoke": lambda: BOOTH.revoke(body.get("name"), body.get("pin")),
            "/claim": lambda: {"ok": True, "name": BOOTH.claim_hand_over()},
            "/alive": lambda: BOOTH.alive(),
            "/camera": lambda: _set_camera(body.get("id")),
        }
        route = routes.get(self.path)
        if route is None:
            self.send_error(404)
            return
        # In the guest's language at the door: the booth's methods answer in the scan's Thai. The language
        # is the one on the screen that asked (every POST carries it): a /start and a /lang crossing on this
        # threaded server cannot hand one guest two languages (review 23 Nov). Staff's words are not the
        # guest's: /revoke, and a /start that failed on setup, stay as written (Thai, with the setting to fix).
        lang = body.get("lang") if body.get("lang") in booth.LANGS else BOOTH.lang
        if self.path in GUEST_ROUTES:
            BOOTH.lang = lang
        try:
            reply = route()
            if self.path in GUEST_ROUTES and not (self.path == "/start" and not reply.get("ok")):
                reply = booth.in_language(reply, lang)
            self._send(reply)
        except face_scan.ScanError as exc:
            self._send({"ok": False, "reason": exc.reason}, status=exc.status)
        except ValueError as exc:
            reply = {"ok": False, "reason": str(exc)}
            self._send(booth.in_language(reply, lang) if self.path in GUEST_ROUTES else reply, status=409)


#: What the guest reads: put into their language. Everything else here is for staff.
GUEST_ROUTES = {"/start", "/lang", "/consent", "/look", "/capture", "/save"}


class BoothServer(ThreadingHTTPServer):
    """One booth per port. HTTPServer sets SO_REUSEADDR, and on Windows that lets a
    second process bind the same port: both answer, and one guest's scan is split
    between two booths that each hold half of it (23 Nov: a test booth started
    beside the owner's real one and took some of its requests)."""
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def _set_camera(value) -> dict:
    BOOTH.camera = value[:200] if isinstance(value, str) and value.strip() else None
    return {"ok": True}


def _housekeeping() -> None:
    """A walked-off guest's crops, the robot's queue, the robot's state for the
    corner, and the consent's end."""
    purged = time.monotonic()
    while True:
        time.sleep(booth.RETRY_S)
        try:
            BOOTH.sweep()
            BOOTH.flush()
            for name, why in BOOTH.take_late_refusals():
                # The staff's terminal, not the screen: this guest was told Emma would
                # know them in a moment, and she will not. They scan again with staff.
                print("หุ่นปฏิเสธการสแกนของ %s (%s) — ตู้ลบข้อมูลนั้นแล้ว ให้เขาสแกนใหม่กับเจ้าหน้าที่" % (name, why),
                      flush=True)
            BOOTH.check_robot()
            if time.monotonic() - purged > 3600.0:
                purged = time.monotonic()
                BOOTH.purge()
        except Exception as exc:  # noqa: BLE001 -- a queue that fails retries; a scan goes on
            print("booth housekeeping: %s" % type(exc).__name__, flush=True)


def _cert_path() -> Path:
    cert = Path(os.getenv("GUEST_SYNC_CERT", "").strip() or str(ROOT / "certs" / "lan-cert.pem"))
    return cert if cert.is_absolute() else ROOT / cert


def _link() -> tuple[booth.Link | None, str | None]:
    """The robot, or why there is none."""
    url, key = os.getenv("GUEST_SYNC_URL", "").strip(), os.getenv("GUEST_SYNC_KEY", "").strip()
    if not (url and key):
        missing = " / ".join(n for n, v in (("GUEST_SYNC_URL", url), ("GUEST_SYNC_KEY", key)) if not v)
        hint = " (https://<ip หุ่น>:8004)" if not url else " (python scripts/booth_app.py --new-key)"
        return None, "%s ยังไม่ได้ตั้ง%s: สแกนได้ แต่ข้อมูลรออยู่ในตู้จนกว่าจะตั้ง" % (missing, hint)
    if not url.startswith("https://"):
        return None, "GUEST_SYNC_URL ต้องขึ้นต้นด้วย https:// เช่น https://192.168.0.2:8004"
    try:
        pin = booth.cert_pin(_cert_path())
    except (OSError, ValueError) as exc:
        return None, "อ่านใบรับรองของหุ่นไม่ได้ (%s): %s — ก๊อป certs/lan-cert.pem จากเครื่องที่ deploy หุ่นมา" % (
            _cert_path(), type(exc).__name__)
    return booth.Link(url, key, pin), None


def _fields() -> dict[str, str]:
    return {key: os.getenv(setting, "").strip() for key, setting in booth.BLANKS.items()}


def _back() -> str | None:
    """BOOTH_RETURN_URL, if it is one the booth may send a screen to."""
    raw = os.getenv("BOOTH_RETURN_URL", "").strip()
    back = booth.return_url(raw)
    if raw and back is None:
        print("BOOTH_RETURN_URL ใช้ไม่ได้ (ต้องเป็น https:// หรือ http://127.0.0.1): %s" % raw)
    return back


def check() -> int:
    """Everything a rehearsal needs from this machine, in one list. 0 = ready."""
    bad = 0

    def line(ok: bool | None, text: str) -> None:
        nonlocal bad
        mark = {True: "[ ok ]", False: "[ !! ]", None: "[ -- ]"}[ok]
        if ok is False:
            bad += 1
        print("%s %s" % (mark, text))

    line(faces.available(), "โมเดลใบหน้า (%s)" % faces.MODEL_TAG if faces.available()
         else "ไม่มีโมเดลใบหน้า: python scripts/fetch_face_model.py")
    until = None
    if not os.getenv("BOOTH_CONSENT_UNTIL", "").strip():
        line(False, "BOOTH_CONSENT_UNTIL ยังไม่ได้ตั้ง (วันที่ลบข้อมูลแขก เช่น 2027-12-02T00:00:00+07:00 = เก็บถึง 1 ธ.ค. 2570)")
    else:
        try:
            until = booth.parse_until(os.getenv("BOOTH_CONSENT_UNTIL", ""))
            line(True, "เก็บข้อมูลแขกถึง %s (BOOTH_CONSENT_UNTIL)" % booth.thai_date(until))
        except ValueError as exc:
            line(False, str(exc))
    raw = booth.policy_text()
    if raw is None:
        line(False, "ไม่มีข้อความแจ้ง %s" % booth.policy_path())
    else:
        # Checked with a stand-in date when the real one is missing: every problem in
        # one run (owner's first run, 23 Nov, saw three lines and not the fourth).
        stand_in = until or datetime.now(timezone.utc).astimezone() + timedelta(days=1)
        text, why = booth.notice(raw, stand_in, _fields())
        line(text is not None, "ข้อความแจ้งครบทุกช่อง" if text is not None else why)
    # A failure, not a note: the notice promises a withdrawal "at this booth".
    back = _back()
    line(True if back else None, "จบแล้วกลับไปหน้าเช็กอิน: %s" % back if back
         else "BOOTH_RETURN_URL ไม่ได้ตั้ง: ตู้ใช้เดี่ยวๆ ได้ แต่ปุ่มจากหน้าเช็กอินจะไม่กลับไปหน้าเช็กอินเอง")
    line(bool(os.getenv("BOOTH_PIN", "").strip()),
         "BOOTH_PIN ตั้งแล้ว (ถอนความยินยอมที่ตู้)" if os.getenv("BOOTH_PIN", "").strip()
         else "BOOTH_PIN ยังไม่ได้ตั้ง: ถอนความยินยอมที่ตู้ไม่ได้ ทั้งที่ข้อความแจ้งสัญญาไว้")
    link, why = _link()
    if link is None:
        line(False, why)
    else:
        line(True, "ใบรับรองของหุ่น %s (ปัก sha256 %s…)" % (_cert_path().name, link.pin[:12]))
        if until is not None:
            b = booth.Booth(until=until, station=os.getenv("BOOTH_STATION", "booth-1"), link=link,
                            fields=_fields())
            robot = b.check_robot(force=True)
            text = booth.ROBOT_STATES_TH.get(robot["state"], robot["state"])
            if robot["state"] == "ok" and robot.get("guests") is not None:
                text += " (มีแขกบนหุ่น %d คน)" % robot["guests"]
            line(robot["state"] == "ok", "%s — %s" % (os.getenv("GUEST_SYNC_URL", "").strip(), text))
            pending = b.outbox.pending()
            if pending:
                why = b.flush().get("error")
                line(None, "รอส่งถึงหุ่น %d รายการ (ส่งไม่ได้เพราะ: %s)" % (pending, why))
            else:
                line(True, "ไม่มีข้อมูลค้างส่ง")
            unreadable = list(b.outbox.folder.glob("*.bad")) if b.outbox.folder.is_dir() else []
            if unreadable:
                line(None, "ไฟล์คิวที่อ่านไม่ได้ %d ไฟล์ (ลบเองในรอบ purge ถัดไปขณะตู้เปิด)" % len(unreadable))
    print()
    print("พร้อมซ้อม" if not bad else "ยังไม่พร้อม: แก้ %d ข้อที่มี [ !! ]" % bad)
    return 0 if not bad else 1


def list_guests() -> int:
    """Names on this booth, for a withdrawal: in this terminal only."""
    try:
        until = booth.parse_until(os.getenv("BOOTH_CONSENT_UNTIL", ""))
    except ValueError:
        until = datetime.now(timezone.utc).astimezone() + timedelta(days=1)
    rows = booth.Booth(until=until, link=None).guest_list()
    for r in rows:
        print("%s  ลงทะเบียน %s  หมดอายุ %s%s" % (r["name"], str(r["consent_at"])[:16], str(r["expires_at"])[:10],
                                               "  (ถอนแล้ว)" if r["revoked"] else ""))
    print("%d คน" % len(rows))
    return 0


def wipe() -> int:
    """The booth's own store, gone: for a booth PC that will not be opened again
    after the event, since its purge runs only while it is open."""
    try:
        until = booth.parse_until(os.getenv("BOOTH_CONSENT_UNTIL", ""))
    except ValueError:
        until = datetime.now(timezone.utc).astimezone() + timedelta(days=1)
    link, _why = _link()
    b = booth.Booth(until=until, link=link)
    if link is not None:
        b.flush()
    out = b.wipe()
    if not out["ok"]:
        if out.get("pending"):
            print("ยังมี %d รายการรอส่งถึงหุ่น — ต่อหุ่นให้ส่งครบก่อน (check-scan-booth.cmd) แล้วค่อยล้าง" % out["pending"])
        else:
            print(out.get("reason"))
        return 1
    print("ลบข้อมูลแขกบนเครื่องตู้แล้ว (%s) — ข้อมูลบนหุ่นยังอยู่จนถึงวันที่บนข้อความแจ้ง" % b.store)
    return 0


def main() -> int:
    global BOOTH
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8771)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--check", action="store_true", help="ตรวจเครื่องตู้ + การเชื่อมหุ่น แล้วจบ (ไม่เปิดกล้อง)")
    ap.add_argument("--new-key", action="store_true", help="สุ่ม GUEST_SYNC_KEY ใหม่ (พิมพ์ออกจอเท่านั้น)")
    ap.add_argument("--list", action="store_true", help="รายชื่อแขกบนตู้นี้ สำหรับการถอนความยินยอม")
    ap.add_argument("--wipe", action="store_true",
                    help="ลบข้อมูลแขกทั้งหมดบนเครื่องตู้ (หลังงาน ถ้าจะไม่เปิดตู้อีก) — หุ่นยังจำได้ตามวันของมันเอง")
    args = ap.parse_args()

    if args.new_key:
        print("GUEST_SYNC_KEY=%s" % secrets.token_urlsafe(32))
        print("ใส่บรรทัดนี้ใน .env ของเครื่องตู้ และใน .env ของ PC ที่ส่งค่าขึ้นหุ่น แล้วรัน: "
              "python scripts/robot_server.py secrets")
        return 0
    if args.check:
        return check()
    if args.list:
        return list_guests()
    if args.wipe:
        return wipe()

    if not faces.available():
        print("face models not found: python scripts/fetch_face_model.py")
        return 1
    try:
        until = booth.parse_until(os.getenv("BOOTH_CONSENT_UNTIL", ""))
    except ValueError as exc:
        print(exc)
        return 1
    raw = booth.policy_text()
    if raw is None:
        print("no approved notice in %s: guests cannot be scanned until the owner's words are there"
              % booth.policy_path())
    else:
        _text, why = booth.notice(raw, until, _fields())
        if why:
            print(why)
    settings.face_scan_pin = os.getenv("BOOTH_PIN", "").strip()
    link, why = _link()
    if why:
        print(why)
    BOOTH = booth.Booth(until=until, station=os.getenv("BOOTH_STATION", "booth-1"), link=link,
                        fields=_fields(), back=_back())
    print(booth.ROBOT_STATES_TH.get(BOOTH.check_robot(force=True)["state"], ""))
    gone = BOOTH.purge()
    if gone:
        print("removed %d guests past their consent" % gone)
    try:
        server = BoothServer(("127.0.0.1", args.port), Handler)
    except OSError:
        print("มีตู้สแกนเปิดอยู่แล้วที่พอร์ต %d — ใช้หน้าต่างนั้น (ปิดหน้าต่างเก่าก่อน ถ้าจะเปิดใหม่)" % args.port)
        return 1
    threading.Thread(target=_housekeeping, daemon=True).start()
    target = "http://127.0.0.1:%d/" % args.port
    print("scan booth: %s  (consent until %s)" % (target, until.isoformat(timespec="minutes")))
    if settings.gemini_api_key:
        threading.Thread(target=voice.warm, daemon=True).start()
    if BOOTH.back:
        # One PC for check-in and the booth: the kiosk's browser opens the booth itself.
        # A second booth tab here would share the one scan and cancel a handed-over guest's.
        print("BOOTH_RETURN_URL ตั้งไว้: ไม่เปิดแท็บตู้เอง — หน้าเช็กอินจะเปิดให้เมื่อแขกกดปุ่ม")
    elif not args.no_open:
        webbrowser.open(target)
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
