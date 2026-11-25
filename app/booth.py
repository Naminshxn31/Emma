"""
The launch event's scan booth: a guest reads the notice, presses "ยินยอม", turns
through the poses, gives a name, and the robot knows them a few seconds later.

    scripts/booth_app.py   the HTTP front (127.0.0.1 only, like enroll_app.py)
    client/booth.html      the screen the guest stands at
    app/guests.py          where it lands, on the robot

The owner builds the booth himself: a PC or a Windows tablet with a camera (his
word, 5 Nov). 127.0.0.1 because a browser opens a camera only on https or
localhost, and because this looks at faces and files them under names.

Every rule about a face is `enrollment.Session`'s, the third front end on it; the
name rule, the English-to-Thai mapping and the PIN counter are `face_scan`'s.
What this adds is what neither the station nor the call screen needed:

* **Its own store** (`configure`): data/faces/booth/, never this PC's gallery. A
  guest filed in the sales room's gallery would be greeted there by name.
* **The words are the owner's**: data/policies/event-guest-v1.txt. No file = no
  scan. No "skip the screen" here: `face_scan`'s staff-attested road exists for
  staff who consented as staff; a guest has only this notice. Its blanks (the
  end date, who holds the data, whom to ask) are filled from this machine's
  settings (`notice`): a blank left on the screen = no scan, never a
  placeholder somebody agreed to.
* **Consent ends on one stated date**: `until` is required at startup and capped
  at consent.MAX_GUEST_DAYS. Owner, 23 Nov 2026: kept past the event so the robot
  knows them when they come back to see the project -- a year, said on the
  notice, not the default of a staff record.
* **The push**: every save and revoke goes through an outbox file first, so a
  robot behind a dead Wi-Fi (the multimedia room, Android restarting) costs a
  delay and never a guest. In order, so a revoke cannot overtake its own save.
* **Pictures don't outlive the push**: the robot takes vectors; the crops here
  are deleted the moment it has accepted them. This machine keeps vectors only to
  refuse one face under two names, until the consent's end (`purge`, which runs
  only while this program is open -- `wipe` for a booth that will not be opened
  again: the robot keeps its own copy and its own end).
"""
from __future__ import annotations

import hashlib
import http.client
import json
import logging
import re
import shutil
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from app import consent, enrollment, face_scan, faces, guests
from app.config import settings

logger = logging.getLogger("condo_voice.booth")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STORE = ROOT / "data" / "faces" / "booth"
POLICY_DIR = ROOT / "data" / "policies"
#: Crops in memory of somebody who walked off mid-scan are dropped after this.
IDLE_S = 90.0
#: A hand-over from the check-in kiosk waits this long for its page to ask for it.
HANDOFF_S = 60.0
#: How long a save waits for the robot's answer, so the guest can be told. The
#: outbox keeps trying after that.
PUSH_TIMEOUT_S = 4.0
RETRY_S = 5.0

#: Which poses go to the robot. See docs/emma-face-approach.md: side poses raised
#: strangers' best scores more than they helped their owner.
ROWS = ("front", "far", "front2")


def policy_path() -> Path:
    return POLICY_DIR / ("%s.txt" % consent.EVENT_GUEST)


def policy_text() -> str | None:
    """The notice as written, blanks and all, or None for a missing, empty or
    broken file. Never a default: a placeholder is exactly the text somebody
    would agree to."""
    try:
        text = policy_path().read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None
    return text if text.strip() else None


#: The notice's blanks and the setting that fills each. The date is the
#: booth's own `until`, so the words and the record cannot disagree.
BLANKS = {"controller": "BOOTH_CONTROLLER", "contact": "BOOTH_CONTACT"}
_BLANK = re.compile(r"\{([a-z_]+)\}")

_TH_MONTHS = ("มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม",
              "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม")
_EN_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
              "September", "October", "November", "December")
#: Bangkok, written out: the booth machine's own zone may be anything, and the
#: robot's is China's (CLAUDE.md "นาฬิกาหุ่นเป็นเวลาจีน").
BANGKOK = timezone(timedelta(hours=7))


def _day_before_midnight(until: datetime) -> datetime:
    """Midnight is the start of a day: "until 00:00 on 1 Dec" reads as 30 Nov
    to a person. The date a guest is told is the last day they are kept."""
    local = until.astimezone(BANGKOK)
    return local - timedelta(seconds=1) if (local.hour, local.minute, local.second) == (0, 0, 0) else local


def thai_date(until: datetime) -> str:
    day = _day_before_midnight(until)
    return "%d %s %d" % (day.day, _TH_MONTHS[day.month - 1], day.year + 543)


def english_date(until: datetime) -> str:
    day = _day_before_midnight(until)
    return "%d %s %d" % (day.day, _EN_MONTHS[day.month - 1], day.year)


def notice(text: str, until: datetime, fields: dict[str, str]) -> tuple[str | None, str | None]:
    """(the words with every blank filled, None) or (None, why not). A blank
    this machine has no value for, or one nobody defined, is a refusal: what
    the guest reads is what they agree to."""
    values = {"until_th": thai_date(until), "until_en": english_date(until)}
    for key, setting in BLANKS.items():
        value = (fields.get(key) or "").strip()
        if value:
            values[key] = value
    missing = []
    for key in _BLANK.findall(text):
        if key not in values and key not in missing:
            missing.append(key)
    if missing:
        names = ", ".join(BLANKS.get(k, "{%s}" % k) for k in missing)
        return None, "ข้อความแจ้งยังมีช่องว่าง: ตั้ง %s ในไฟล์ .env ของเครื่องตู้ก่อน — ไม่เปิดสแกน" % names
    return _BLANK.sub(lambda m: values[m.group(1)], text), None


def return_url(value: str) -> str | None:
    """BOOTH_RETURN_URL: where the screen goes when a guest is done, for a booth
    the check-in kiosk hands over to on the same PC (owner, 23 Nov: "จุดเช็กอินกับ
    ตู้สแกนหน้าเป็นคอมเครื่องเดียวกัน"). From this machine's settings only, never
    from the link that opened the page (that would be an open redirect), and
    https or this machine itself only."""
    url = (value or "").strip()
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return url
    if parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost"):
        return url
    return None


def parse_until(value: str, now: datetime | None = None) -> datetime:
    """The moment guests' consent ends. ValueError in plain words for a value
    that is not a zoned time, not in the future, or further than the cap."""
    try:
        until = datetime.fromisoformat((value or "").strip())
    except ValueError:
        raise ValueError("BOOTH_CONSENT_UNTIL ต้องเป็นเวลาแบบ 2027-12-02T00:00:00+07:00") from None
    if until.tzinfo is None:
        raise ValueError("BOOTH_CONSENT_UNTIL ต้องมีเขตเวลา เช่น +07:00")
    now = now or datetime.now(timezone.utc).astimezone()
    if until <= now:
        raise ValueError("BOOTH_CONSENT_UNTIL ผ่านไปแล้ว")
    if until - now > timedelta(days=consent.MAX_GUEST_DAYS):
        raise ValueError("BOOTH_CONSENT_UNTIL ไกลเกิน %d วัน" % consent.MAX_GUEST_DAYS)
    return until


def cert_pin(path: Path) -> str:
    """The sha256 of the robot's certificate, from the same PEM deploy sends it."""
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(_first_pem(path))).hexdigest()


def _first_pem(path: Path) -> str:
    text = Path(path).read_text(encoding="ascii")
    end = "-----END CERTIFICATE-----"
    return text[:text.index(end) + len(end)]


class Link:
    """HTTPS to the robot, the certificate pinned. Not checked by hostname: the
    robot's DHCP address drifts (memory: the PC's did three times in a month), and
    a pin is stricter than any name on it. The key goes out only after the pin
    matched."""

    def __init__(self, url: str, key: str, pin: str):
        parts = urlsplit(url)
        self.host, self.port = parts.hostname, parts.port or 443
        self.key, self.pin = key, pin

    def post(self, path: str, doc: dict, timeout: float = PUSH_TIMEOUT_S) -> tuple[int, dict]:
        return self._request("POST", path, doc, timeout)

    def get(self, path: str, timeout: float = PUSH_TIMEOUT_S) -> tuple[int, dict]:
        """/guests/status: is the robot there, is the key right, same model."""
        return self._request("GET", path, None, timeout)

    def _request(self, method: str, path: str, doc: dict | None, timeout: float) -> tuple[int, dict]:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE         # the pin below is the check
        conn = http.client.HTTPSConnection(self.host, self.port, timeout=timeout, context=ctx)
        try:
            conn.connect()
            der = conn.sock.getpeercert(binary_form=True)
            if hashlib.sha256(der).hexdigest() != self.pin:
                raise ssl.SSLError("the robot's certificate is not the pinned one")
            headers = {"Authorization": "Bearer " + self.key}
            body = None
            if doc is not None:
                body = json.dumps(doc, ensure_ascii=False).encode("utf-8")
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body, headers)
            reply = conn.getresponse()
            raw = reply.read()
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                body = {}
            return reply.status, body if isinstance(body, dict) else {}
        finally:
            conn.close()


class Outbox:
    """Saves and revokes on their way to the robot: one file each, in order."""

    def __init__(self, folder: Path, link: Link | None):
        self.folder = folder
        self.link = link
        self._lock = threading.Lock()
        self._seq = 0

    def add(self, kind: str, doc: dict) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._seq += 1
            name = "%017d-%04d-%s.json" % (time.time_ns() // 1000, self._seq, kind)
        path = self.folder / name
        faces.write_atomically(path, lambda fh: fh.write(json.dumps(
            {"kind": kind, "doc": doc}, ensure_ascii=False).encode("utf-8")))
        return path

    def pending(self) -> int:
        return len(list(self.folder.glob("*.json"))) if self.folder.is_dir() else 0

    def flush(self, on_done=None) -> dict:
        """Oldest first; stops at the first that could not be delivered, so
        nothing overtakes it. {"sent": n, "pending": n, "error": ...}."""
        sent = 0
        with self._lock:
            files = sorted(self.folder.glob("*.json")) if self.folder.is_dir() else []
            for path in files:
                try:
                    entry = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    logger.warning("an unreadable outbox file was set aside: %s", path.name)
                    path.replace(path.with_suffix(".bad"))
                    continue
                state, detail = self._deliver(entry)
                if state == "later":
                    return {"sent": sent, "pending": len(files) - sent, "error": detail}
                path.unlink(missing_ok=True)
                sent += 1
                if on_done is not None:
                    on_done(entry, state, detail)
        return {"sent": sent, "pending": 0, "error": None}

    def _deliver(self, entry: dict) -> tuple[str, str | None]:
        if self.link is None:
            return "later", "no_link"
        try:
            status, body = self.link.post("/guests/" + str(entry.get("kind")), entry.get("doc") or {})
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            return "later", type(exc).__name__
        if 200 <= status < 300:
            return "accepted", None
        error = str(body.get("error") or status)
        if 400 <= status < 500 and error in GUEST_REFUSALS:
            return "refused", error
        # Everything else is this setup's fault, not the guest's: a wrong key,
        # a robot still on the old 30-day rule ("bad_consent"), a different face
        # model, the listener off, a wrong address (404). Kept, and said in the
        # corner, until it is fixed -- review 23 Nov: these used to count as the
        # guest's refusal, and a refusal now deletes the scan.
        return "later", error


#: The robot's answers that are about this guest's scan and will not change on a
#: retry: the only ones that end it (`Booth.flush` lets the scan go).
GUEST_REFUSALS = {"same_face", "revoked", "name_taken", "bad_name"}

#: The robot's refusals, for the guest in front of the screen. No other name, and
#: nothing they could act on but to ask the staff.
REFUSED_TH = {
    "same_face": "ใบหน้านี้มีอยู่ในระบบของหุ่นแล้วภายใต้ชื่ออื่น — กรุณาแจ้งเจ้าหน้าที่",
    "revoked": "ชื่อนี้เพิ่งถอนความยินยอม — กรุณาแจ้งเจ้าหน้าที่",
    "name_taken": "ชื่อนี้ซ้ำกับคนที่ Emma รู้จักอยู่แล้ว — กรุณาแจ้งเจ้าหน้าที่ แล้วสแกนใหม่ด้วยชื่อที่เติมนามสกุลหรืออักษรย่อ",
}
_REFUSED = "หุ่นยังรับข้อมูลนี้ไม่ได้ — กรุณาแจ้งเจ้าหน้าที่"


# -- the guest's language -------------------------------------------------------------
# Owner, 23 Nov: "อยากให้สามารถเลือกภาษาได้ th en" -- "ค่าเริ่มต้น en". The scan's own words are
# enrollment's and face_scan's, in Thai, shared with the staff station and the call screen;
# the booth puts what ITS guest reads and hears into English here, at its own door
# (scripts/booth_app.py), and leaves those modules alone. Staff-only text (the corner,
# the withdraw panel, --check) stays Thai.
LANGS = ("en", "th")
#: Where the notice file turns from Thai to English (data/policies/event-guest-v1.txt).
NOTICE_SPLIT = "———— English ————"

EN = {
    # the poses (enrollment.STEPS) and what the camera says about a frame (enrollment.SPOKEN, judge)
    "มองตรงมาที่กล้อง": "Look straight at the camera",
    "หันหน้าไปทางขวาของคุณช้าๆ": "Slowly turn your face to your right",
    "หันไปอีกด้านหนึ่ง": "Now slowly turn to the other side",
    "ถอยห่างออกไปสักก้าว": "Take one step back",
    "กลับมามองตรง แล้วยิ้มค้างไว้": "Look straight again and hold a smile",
    "ยังไม่เห็นหน้า": "I can't see your face yet",
    "ทีละคนนะคะ": "One person at a time, please",
    "เข้ามาใกล้อีกนิด": "Come a little closer",
    "อ่านใบหน้าไม่ได้": "I can't read your face, please try again",
    "ครบแล้ว กดบันทึกได้": "All done. Press Save",
    "ครบแล้ว — กดบันทึกได้": "All done. Press Save",
    "ภาพนี้เหมือนใบเดิม ลองขยับอีกนิด": "That looks like the last picture, move a little",
    "ข้ามข้อนี้ — กล้องอยู่ใกล้เกินกว่าจะถอยได้": "Skipped: the camera is too close to step back",
    "เก็บภาพไม่สำเร็จ ลองอีกครั้ง": "Couldn't take the picture, please try again",
    "อ่านภาพไม่ได้ — ลองใหม่": "Couldn't read the picture, please try again",
    "ไม่ได้ส่งภาพมา": "Couldn't read the picture, please try again",
    # the booth's own
    "ยังไม่ได้ยินยอม": "Please read the notice and agree first",
    "ยังไม่ได้เริ่ม หรือยังไม่ได้ยินยอม": "Please read the notice and agree first",
    "ยังเก็บภาพไม่ครบทุกท่า": "Not every pose is done yet",
    "ชื่อนี้มีอยู่แล้ว — เติมนามสกุลหรืออักษรย่อ": "That name is taken. Add a surname or an initial",
    "ใบหน้านี้มีอยู่ในระบบของหุ่นแล้วภายใต้ชื่ออื่น — กรุณาแจ้งเจ้าหน้าที่":
        "This face is already registered under another name. Please ask our staff",
    "ชื่อนี้เพิ่งถอนความยินยอม — กรุณาแจ้งเจ้าหน้าที่": "This name was just withdrawn. Please ask our staff",
    "ชื่อนี้ซ้ำกับคนที่ Emma รู้จักอยู่แล้ว — กรุณาแจ้งเจ้าหน้าที่ แล้วสแกนใหม่ด้วยชื่อที่เติมนามสกุลหรืออักษรย่อ":
        "Emma already knows someone by this name. Please ask our staff, and scan again with a surname or an initial",
    "หุ่นยังรับข้อมูลนี้ไม่ได้ — กรุณาแจ้งเจ้าหน้าที่": "Emma couldn't take this yet. Please ask our staff",
    # face_scan's refusals on save
    "ใบหน้านี้มีอยู่ในระบบแล้วภายใต้ชื่ออื่น — ให้พนักงานตรวจที่สถานีลงทะเบียน":
        "This face is already registered under another name. Please ask our staff",
    "ภาพที่เก็บไม่ใช่คนเดียวกันทั้งหมด (มีคนอื่นเข้ามาในภาพ) — ปิดแล้วเริ่มสแกนใหม่":
        "Someone else came into the pictures. Please start again",
    "บันทึกไม่สำเร็จ — ให้พนักงานตรวจที่สถานีลงทะเบียน": "Couldn't save. Please ask our staff",
    # the name rules (enrollment.safe_person_name)
    "ยังไม่ได้ใส่ชื่อ": "Please type a name",
    "ชื่อนี้มีอยู่แล้ว": "That name is taken. Add a surname or an initial",
    "ชื่อขึ้นต้นหรือลงท้ายด้วยจุดไม่ได้": "A name can't start or end with a dot",
    "ชื่อมีจุดติดกันสองตัวไม่ได้": "A name can't have two dots in a row",
    "ชื่อมีอักขระพิเศษที่มองไม่เห็น — พิมพ์ใหม่": "The name has a hidden character. Please type it again",
    "ชื่อใช้ได้เฉพาะตัวอักษร ตัวเลข เว้นวรรค และ . - '": "Use letters, numbers, spaces and . - ' only",
    "ชื่อนี้ใช้เป็นโฟลเดอร์ไม่ได้": "That name can't be used. Please try another",
    "ชื่อนี้ Windows สงวนไว้ ใช้เป็นชื่อไม่ได้": "That name can't be used. Please try another",
}
_EN_PATTERNS = (
    (re.compile(r"^มี (\d+) หน้าในภาพ — ทีละคน$"), lambda m: "%s faces in the picture, one person at a time" % m.group(1)),
    (re.compile(r"^เข้ามาใกล้อีกนิด \(.*\)$"), lambda m: EN["เข้ามาใกล้อีกนิด"]),
    (re.compile(r"^ชื่อยาวเกิน (\d+) ตัวอักษร$"), lambda m: "A name can be at most %s characters" % m.group(1)),
)
#: Anything Thai with no English here: a refusal nobody wrote English for. Said plainly,
#: never shown in Thai to a guest who chose English.
EN_FALLBACK = "Something isn't right. Please ask our staff"


def _is_thai(text: str) -> bool:
    return any("\u0e00" <= ch <= "\u0e7f" for ch in text)


def to_en(text):
    """One guest-facing sentence in English (see EN)."""
    if not isinstance(text, str) or not text or not _is_thai(text):
        return text
    if text in EN:
        return EN[text]
    for suffix, english in ((" — ข้าม (กล้องใกล้เกินไป)", " (skipped: camera too close)"),):
        if text.endswith(suffix):
            head = to_en(text[: -len(suffix)])
            return head + english if head != EN_FALLBACK else EN_FALLBACK
    m = re.match(r"^(.+?) \(ตอนนี้ \d+px ขอ \d+px\)$", text)
    if m:
        return to_en(m.group(1))
    for pattern, render in _EN_PATTERNS:
        m = pattern.match(text)
        if m:
            return render(m)
    return EN_FALLBACK


def in_language(reply, lang):
    """A booth reply in the guest's language: what they read (prompt, reason, steps)
    and hear (say). Thai is the scan's own; nothing changes for it."""
    if lang != "en" or not isinstance(reply, dict):
        return reply
    out = dict(reply)
    for key in ("prompt", "reason", "say"):
        if key in out:
            out[key] = to_en(out[key])
    if isinstance(out.get("steps"), list):
        out["steps"] = [dict(s, prompt=to_en(s.get("prompt"))) if isinstance(s, dict) else s for s in out["steps"]]
    return out


def notice_in(text: str, lang: str) -> str:
    """The notice in one language: the file holds both, Thai first (NOTICE_SPLIT)."""
    if NOTICE_SPLIT not in text:
        return text
    th, en = text.split(NOTICE_SPLIT, 1)
    return (en if lang == "en" else th).strip() + "\n"


#: How the robot answered the last /guests/status, for the corner and --check.
ROBOT_STATES_TH = {
    "ok": "หุ่นพร้อมรับข้อมูล",
    "no_link": "ยังไม่ได้ตั้งที่อยู่หุ่น (GUEST_SYNC_URL / GUEST_SYNC_KEY)",
    "unreachable": ("ติดต่อหุ่นไม่ได้ — ตู้กับหุ่นอยู่ Wi-Fi วงเดียวกันไหม, ที่อยู่ใน GUEST_SYNC_URL "
                    "ยังเป็นของหุ่นไหม, หุ่นเปิดแอป Emma อยู่ไหม"),
    "certificate": "ใบรับรองของหุ่นไม่ตรงกับ GUEST_SYNC_CERT — ใช้ไฟล์ certs/lan-cert.pem ชุดเดียวกับที่ส่งขึ้นหุ่น",
    "key": "หุ่นปฏิเสธกุญแจ — GUEST_SYNC_KEY ของตู้กับของหุ่นไม่ตรงกัน (robot_server.py secrets)",
    "locked": "หุ่นล็อกตู้ไว้ 1 นาที (กุญแจผิดหลายครั้ง)",
    "model": "โมเดลใบหน้าของตู้กับหุ่นคนละรุ่น — หุ่นจะไม่รับข้อมูล",
    "old": "หุ่นยังใช้โค้ดเก่า (เก็บได้ 30 วัน) — python scripts/robot_server.py deploy ก่อน",
    "off": "หุ่นยังไม่เปิดรับแขก (FACE_GUESTS_FILE / GUEST_SYNC_PORT ใน robot.env แล้ว deploy)",
    "error": "หุ่นตอบผิดปกติ",
}
ROBOT_CHECK_S = 30.0


class Booth:
    def __init__(self, store: Path = DEFAULT_STORE, until: datetime | None = None, station: str = "booth-1",
                 link: Link | None = None, fields: dict[str, str] | None = None, back: str | None = None):
        if until is None:
            raise ValueError("BOOTH_CONSENT_UNTIL ยังไม่ได้ตั้ง — ไม่มีวันหมดอายุ ไม่เปิดสแกน")
        self.store = Path(store)
        self.until = until
        self.station = station
        self.fields = dict(fields or {})
        self.back = back
        self.outbox = Outbox(self.store / "outbox", link)
        self._lock = threading.RLock()
        self._scan: enrollment.Session | None = None
        self._consented = False
        self._touched = 0.0
        self._results: dict[str, tuple[str, str | None]] = {}
        #: Names a `save` is waiting on right now: their result is that save's to tell.
        self._waiting: set[str] = set()
        #: Refusals nobody stood there for (the queue delivered them later): the
        #: corner counts them, the terminal names them (`take_late_refusals`).
        self.late_refused = 0
        self._push_error: str | None = None
        self._robot: dict = {"state": "no_link" if link is None else None, "guests": None, "at": 0.0}
        self._handoff: tuple[str, float] | None = None
        #: The camera staff picked in the corner, for every page after (a hand-over is a
        #: fresh page load and used to fall back to the default camera each time). An id, no picture.
        self.camera: str | None = None
        #: This guest's language, as the page last sent it (every POST carries it; the
        #: page starts every guest in English, owner 23 Nov "ค่าเริ่มต้น en"). Replies are
        #: put into it at booth_app's door; the methods here answer in the scan's Thai.
        self.lang = "en"
        self._configure()

    def _configure(self) -> None:
        """This process's enrolment store is the booth's own, not the machine's."""
        self.store.mkdir(parents=True, exist_ok=True)
        enrollment.ENROLLED = self.store / "enrolled"
        settings.face_gallery = str(self.store / "gallery.npz")
        # `enrollment.review`'s same-face check at the event's bar (guests._same_face_elsewhere).
        settings.face_threshold = max(settings.face_threshold, settings.face_approach_threshold)

    # -- one guest ----------------------------------------------------------

    def _alive(self) -> None:
        self._touched = time.monotonic()

    def notice(self) -> tuple[str | None, str | None]:
        """The words this guest reads, in their language, or why there are none.
        Every blank is checked in the whole file, both languages."""
        raw = policy_text()
        if raw is None:
            return None, "ยังไม่มีข้อความแจ้งเตือน (data/policies/%s.txt) — ไม่เปิดสแกน" % consent.EVENT_GUEST
        text, why = notice(raw, self.until, self.fields)
        return (notice_in(text, self.lang), None) if text is not None else (None, why)

    def set_lang(self, lang) -> dict:
        """The guest tapped EN / ไทย: the notice again in that language."""
        if lang in LANGS:
            self.lang = lang
        text, _why = self.notice()
        return {"ok": True, "lang": self.lang, "consent_text": text}

    def start(self, lang=None) -> dict:
        """The notice, and a fresh scan. Whatever was open is dropped."""
        if lang in LANGS:
            self.lang = lang
        text, why = self.notice()
        if text is None:
            return {"ok": False, "reason": why}
        with self._lock:
            if self._scan is not None:
                self._scan.discard()
            self._scan, self._consented = enrollment.Session(), False
            self._alive()
            return {"ok": True, "consent_text": text, "consent_version": consent.EVENT_GUEST, "lang": self.lang,
                    "until": self.until.isoformat(timespec="minutes"), **self._scan.state()}

    def answer(self, agree) -> dict:
        """Only JSON true is a yes; anything else ends the scan."""
        with self._lock:
            if agree is not True or self._scan is None:
                self.cancel()
                return {"ok": True, "closed": True}
            self._consented = True
            self._alive()
            return {"ok": True, **self._scan.state()}

    def _live(self) -> enrollment.Session | None:
        with self._lock:
            self._alive()
            return self._scan if self._consented else None

    def look(self, image, full_width=None) -> dict:
        scan = self._live()
        frame = enrollment.decode_data_url(image)
        if scan is None or frame is None:
            return {"ok": False, "reason": "ยังไม่ได้เริ่ม หรือยังไม่ได้ยินยอม", "box": None}
        return face_scan._frame_reply(scan.judge(frame, full_width))

    def capture(self, image, full_width=None) -> dict:
        scan = self._live()
        frame = enrollment.decode_data_url(image)
        if scan is None or frame is None:
            return {"ok": False, "reason": "ยังไม่ได้เริ่ม หรือยังไม่ได้ยินยอม"}
        return face_scan._frame_reply(scan.capture(frame, full_width))

    def alive(self) -> dict:
        """The page says the guest is still typing their name: typing touched nothing
        here, and a slow reader saved into "ยังไม่ได้ยินยอม" (review 23 Nov). The 90 s
        for somebody who walked off stays: it counts from their last keystroke."""
        return {"ok": self._live() is not None}

    def cancel(self) -> dict:
        with self._lock:
            if self._scan is not None:
                self._scan.discard()
            self._scan, self._consented = None, False
        return {"ok": True, "closed": True}

    def sweep(self, now: float | None = None) -> None:
        """Somebody's face does not stay in memory because they walked away."""
        now = time.monotonic() if now is None else now
        with self._lock:
            if self._scan is not None and now - self._touched > IDLE_S:
                self.cancel()

    def save(self, name) -> dict:
        """File them here, queue them for the robot, and try once while they wait."""
        with self._lock:
            scan = self._scan
            if scan is None or not self._consented:
                return {"ok": False, "reason": "ยังไม่ได้ยินยอม"}
            if scan.state()["step"] is not None:
                return {"ok": False, "reason": "ยังเก็บภาพไม่ครบทุกท่า"}
            try:
                name = enrollment.safe_person_name(name if isinstance(name, str) else "")
                if face_scan._name_taken(name):
                    return {"ok": False, "reason": "ชื่อนี้มีอยู่แล้ว — เติมนามสกุลหรืออักษรย่อ"}
            except face_scan.ScanError as exc:
                return {"ok": False, "reason": exc.reason}
            except ValueError as exc:
                return {"ok": False, "reason": str(exc)}
            rows = scan.vectors(ROWS)
            # An answer about an earlier scan under this name (refused, let go)
            # must not be told to this guest as theirs.
            self._results.pop(name, None)
            settings.face_consent_days = (self.until - datetime.now(timezone.utc).astimezone()) / timedelta(days=1)
            result = scan.save(name, add_to_gallery=True, consent_version=consent.EVENT_GUEST)
            if not result.get("ok"):
                return {"ok": False, "reason": face_scan._screen_reason(str(result.get("reason") or ""))}
            self._scan, self._consented = None, False
            record = consent.load().get(result["name"]) or {}
            self._waiting.add(result["name"])
            self.outbox.add("add", {
                "name": result["name"], "vectors": [guests.encode_vector(v) for v in rows],
                "model_tag": faces.MODEL_TAG, "consent": record, "station": self.station})
        try:
            outcome = self.flush()
            return {"ok": True, "name": result["name"], "until": self.until.isoformat(timespec="minutes"),
                    **self._robot_says(result["name"], outcome)}
        finally:
            self._waiting.discard(result["name"])

    def _robot_says(self, name: str, outcome: dict) -> dict:
        state, detail = self._results.pop(name, (None, None))
        if state == "accepted":
            return {"robot": "accepted"}
        if state == "refused":
            return {"robot": "refused", "reason": REFUSED_TH.get(detail or "", _REFUSED)}
        return {"robot": "pending", "pending": outcome.get("pending", 0)}

    def flush(self) -> dict:
        refused: set[str] = set()

        def done(entry, state, detail):
            who = (entry.get("doc") or {}).get("name")
            if entry.get("kind") == "add" and isinstance(who, str):
                self._results[who] = (state, detail)
                if state == "accepted":
                    self._drop_shots(who)
                elif state == "refused":
                    refused.add(who)

        outcome = self.outbox.flush(done)
        self._push_error = outcome.get("error")
        if refused:
            # The robot will never take this scan (its file is gone from the
            # outbox, and only GUEST_REFUSALS get here): the pictures, vectors and
            # record have no use left, and with a year's consent they would have
            # sat here a year. The guest is told to ask the staff and scans again.
            # After the outbox's lock, not inside it: `save` holds this booth's
            # lock while it adds. `purge` retries what a busy file stops here.
            with self._lock:
                try:
                    self._forget_here(refused)
                    consent.forget(refused)
                except Exception as exc:  # noqa: BLE001 -- the queue goes on; purge tries again
                    logger.warning("a refused scan could not be removed yet: %s", type(exc).__name__)
        return outcome

    def take_late_refusals(self) -> list[tuple[str, str]]:
        """Refusals the queue met with nobody waiting at the screen, once each:
        the guest was told "Emma will know you in a moment" and she will not.
        For the staff's terminal (names) -- the corner shows only a count."""
        out = []
        for name, (state, detail) in list(self._results.items()):
            if state == "refused" and name not in self._waiting:
                self._results.pop(name, None)
                out.append((name, detail or ""))
            elif state == "accepted" and name not in self._waiting:
                self._results.pop(name, None)
        self.late_refused += len(out)
        return out

    def _drop_shots(self, name: str) -> None:
        """The robot has its vectors: the pictures have no further use."""
        folder = enrollment.ENROLLED / name
        for shot in folder.glob("*.jpg") if folder.is_dir() else ():
            try:
                shot.unlink()
            except OSError as exc:
                logger.warning("a shot could not be removed: %s", exc)

    def revoke(self, name, pin, client: str = "local") -> dict:
        """Staff, with the PIN, for somebody who changed their mind: here, and on
        the robot as soon as it can be told. Done by the outbox's order, so a save
        still waiting is delivered first and then withdrawn."""
        if not (settings.face_scan_pin or "").strip():
            # face_scan's own gate refuses an empty PIN before it compares; this
            # road called the compare directly, and "" == "" let anybody at the
            # public screen withdraw anybody (review 23 Nov).
            return {"ok": False, "reason": "ยังไม่ได้ตั้ง BOOTH_PIN — ถอนที่ตู้ไม่ได้ แจ้งผู้ดูแลระบบ"}
        face_scan._check_pin(pin, client)
        try:
            name = enrollment.safe_person_name(name if isinstance(name, str) else "")
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        with self._lock:
            had = consent.revoke(name, version=consent.EVENT_GUEST, log_name=False)
            # The robot's copy first: a local file that will not delete (busy,
            # damaged) must not keep the robot naming somebody who withdrew.
            self.outbox.add("revoke", {"name": name})
            self._forget_here({name})
        outcome = self.flush()
        return {"ok": True, "had": bool(had), "robot_pending": outcome.get("pending", 0)}

    def _forget_here(self, names: set[str]) -> None:
        for name in names:
            try:
                folder = enrollment._person_folder(name)
            except ValueError:
                continue
            shutil.rmtree(folder, ignore_errors=True)
        path = Path(settings.face_gallery)
        if path.is_file():
            with faces.file_lock(path, enrollment.GALLERY_LOCK_WAIT_S, "gallery busy"):
                current = faces.Gallery.load(path)
                keep = [i for i, n in enumerate(current.names) if n not in names]
                if len(keep) != len(current):
                    faces.Gallery([current.names[i] for i in keep], current.vecs[keep],
                                  [current.meta[i] for i in keep]).save(path)

    def purge(self, now: datetime | None = None) -> int:
        """Past the consent: name, vectors, record, gone from this machine. A
        withdrawal's record goes REVOKED_KEEP_DAYS after it (guests.py's rule),
        and so do queue files nobody could read (`.bad`: a guest's vectors)."""
        now = now or datetime.now(timezone.utc).astimezone()
        for bad in self.outbox.folder.glob("*.bad") if self.outbox.folder.is_dir() else ():
            try:
                bad.unlink()
            except OSError as exc:
                logger.warning("an unreadable queue file could not be removed: %s", exc)
        try:
            people = consent.load()
        except consent.Unreadable:
            return 0

        def ended(e: dict) -> bool:
            ends = consent._when(e.get("expires_at"))
            if ends is None or ends <= now:
                return True
            withdrawn = consent._when(e.get("revoked_at"))
            return withdrawn is not None and withdrawn + timedelta(days=guests.REVOKED_KEEP_DAYS) <= now

        over = {n for n, e in people.items() if ended(e)}
        if over:
            with self._lock:
                self._forget_here(over)
                consent.forget(over)
        return len(over)

    def wipe(self) -> dict:
        """Everything of the guests' on this machine, for a booth that will not be
        opened again (purge runs only while it is open). The robot keeps its own
        copy and its own end date. Refused while the queue still holds anything:
        that is a scan or a withdrawal the robot has not had."""
        pending = self.outbox.pending()
        if pending:
            return {"ok": False, "pending": pending}
        if consent.people_path().parent != self.store or Path(settings.face_gallery).parent != self.store:
            # Never the machine's own staff store: only what `_configure` pointed here.
            return {"ok": False, "reason": "not the booth's own store"}
        with self._lock:
            if self._scan is not None:
                self._scan.discard()
            self._scan, self._consented = None, False
            for part in (self.store / "enrolled", self.store / "outbox"):
                shutil.rmtree(part, ignore_errors=True)
            for part in (Path(settings.face_gallery), consent.people_path()):
                part.unlink(missing_ok=True)
        return {"ok": True}

    def check_robot(self, now: float | None = None, force: bool = False) -> dict:
        """Ask the robot's /guests/status, at most every ROBOT_CHECK_S. The
        corner says whether a scan will reach it before somebody stands there;
        a rehearsal finds the wrong key at the first minute, not the first guest."""
        now = time.monotonic() if now is None else now
        link = self.outbox.link
        if link is None:
            self._robot = {"state": "no_link", "guests": None, "at": now}
            return dict(self._robot)
        if not force and self._robot.get("state") is not None and now - self._robot["at"] < ROBOT_CHECK_S:
            return dict(self._robot)
        guests_n = None
        try:
            code, body = link.get("/guests/status")
        except ssl.SSLError:
            state = "certificate"
        except (OSError, http.client.HTTPException):
            state = "unreachable"
        else:
            if code == 200 and body.get("ok"):
                state = "ok" if body.get("model_tag") == faces.MODEL_TAG else "model"
                guests_n = body.get("guests") if isinstance(body.get("guests"), int) else None
                # A robot still on the 30-day rule refuses every record this booth
                # writes ("expiry out of range"): said here, before a guest scans.
                need = (self.until - datetime.now(timezone.utc).astimezone()) / timedelta(days=1)
                most = body.get("max_days")
                if state == "ok" and not (isinstance(most, (int, float)) and most >= need):
                    state = "old"
            elif code == 401:
                state = "key"
            elif code == 429:
                state = "locked"
            elif code == 404:
                state = "off"
            else:
                state = "error"
        self._robot = {"state": state, "guests": guests_n, "at": now}
        return dict(self._robot)

    def guest_list(self) -> list[dict]:
        """For staff at this machine's terminal (booth_app.py --list), so a guest
        who asks to be withdrawn can be found by the name they typed. Never on
        the screen: the screen is where the public stands."""
        try:
            people = consent.load()
        except consent.Unreadable:
            return []
        return sorted(({"name": n, "consent_at": e.get("consent_at"), "expires_at": e.get("expires_at"),
                        "revoked": bool(e.get("revoked_at"))} for n, e in people.items()),
                      key=lambda r: str(r["consent_at"]))

    def hand_over(self, name) -> None:
        """The check-in kiosk on this PC sends the guest's registered name, in a form
        POST's body: never in a URL, which the browser keeps in its history (review
        23 Nov). Only a prefill for the name box: the guest still reads the notice,
        agrees and may change it. The last one wins; nobody's name waits past HANDOFF_S."""
        text = " ".join(str(name or "").split())[:80]
        self._handoff = (text, time.monotonic()) if text else None

    def claim_hand_over(self, now: float | None = None) -> str | None:
        """The page asks once for the name it was opened with; then it is gone."""
        now = time.monotonic() if now is None else now
        held, self._handoff = self._handoff, None
        if held is None or now - held[1] > HANDOFF_S:
            return None
        return held[0]

    def status(self) -> dict:
        """What the screen's corner shows. How many are waiting: never who."""
        robot = self._robot.get("state")
        return {"pending": self.outbox.pending(), "linked": self.outbox.link is not None,
                "until": self.until.isoformat(timespec="minutes"), "robot": robot,
                "robot_th": ROBOT_STATES_TH.get(robot or "", ""),
                # Why the queue is stuck (a code: "bad_consent", "wrong_model", ...) and
                # how many scans the robot refused after the guest had walked away.
                "push_error": self._push_error if self.outbox.pending() else None,
                "late_refused": self.late_refused,
                # Where a handed-over screen goes back to (the check-in kiosk), or None.
                "return_url": self.back, "camera": self.camera}


