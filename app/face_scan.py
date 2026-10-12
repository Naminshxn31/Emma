"""
The face scan on the call screen: a staff PIN, the scanned person's own
"ยินยอม", the station's five poses, a name — and the robot knows them
without anybody restarting the server.

What this module owns and what it does not
-------------------------------------------
Every rule about a *face* is `enrollment.Session`'s (which step, whether
this frame is the shot, whether the batch is one person, where it goes on
disk, the consent record). The station holds one of those; this module
holds one per scan and adds only what the station never needed because it
sits on 127.0.0.1 with an operator at the keyboard:

- **a PIN**, because the call screen is on the LAN and on the robot's chest,
  where anybody can press the hold menu. A wrong PIN is counted per client
  and five of them lock that client out for a minute — a four-digit PIN
  with no counter is ten thousand requests, which is an afternoon.
- **the consent screen — only when there is text for it**. The text is the
  owner's, read from `data/policies/<consent.VERSION>.txt`, and nothing here
  writes policy wording, not even a placeholder — a placeholder is exactly
  the text somebody would agree to. With no file the screen is skipped, by
  the owner's decision (2026-09-29): the scan is for staff, who consented as
  staff, and whoever holds the PIN asks the person at the lens first. The
  record then says `consent.STAFF_ATTESTED`, not a policy version nobody
  was shown. Putting a file there brings the screen back, for the day the
  scan is used on anybody else; a file that is there but blank or not
  UTF-8 refuses the scan — that is a broken screen, not "no screen".
- **a name that is new**. A typed name on a LAN screen must not add shots to
  somebody who already exists, and must not re-consent somebody who revoked
  (`consent.record` clears a revocation — right at the station, where the
  person re-enrols themselves in front of an operator, wrong here). Adding
  to an existing person is the station's job and the refusal says so.
- **the door camera, paused**. A scan in progress is a stranger at the lens
  on purpose, turning their head for a minute — a textbook arrival. Held
  under this module's own key (`greeter.hold`), recomputed from what is
  open every time a scan starts, steps, saves or ends: until the latest
  open scan's deadline, or the grace after a save, whichever is later.
  Never `greeter.resume()` — ending one scan used to zero the one global
  deadline and wipe the grace another scan's save had just set.
- **time limits**. A scan lasts FACE_SCAN_SESSION_S *from its start* (cut
  to `greeter.MAX_PAUSE_S`, the longest the door camera can be held), not
  from its last request, and then it is dropped with its crops by a timer
  on the event loop — not by whichever request happens to come next. Both
  halves were bugs: the capture step polls every 200ms whether or not
  anybody is at the lens, so an overlay left open kept the scan (and the
  greeter's pause) alive all day; and a tab that died without its cancel
  kept somebody's face in memory until the next face-scan request, which
  on a quiet day was the next restart.
- **a save that finishes**. A cancel that lands while a save is writing
  waits for it: shots, a consent record and gallery rows cannot be
  half-undone, and the cancel used to answer "closed" while all three were
  being written behind it. The reply says what was filed instead.

State is module-level, one process, at most MAX_SESSIONS scans — which is
why MULTI_SESSION refuses the whole feature: the pause is one switch on one
door camera, the same shape as the `calc` sheet that two concurrent testers
would merge into one.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import secrets
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from app import consent, enrollment, faces, greeter
from app.config import settings

logger = logging.getLogger("condo_voice.face_scan")

#: Where the owner's consent wording lives, one file per policy version.
#: Committed with the repo (not under the gitignored data/faces/): the words
#: people agreed to are a record, not a secret.
POLICY_DIR = Path(__file__).resolve().parent.parent / "data" / "policies"

#: Two, not one: the PC's /preview and the robot's chest screen can each be
#: mid-scan, and a tab that died without its cancel should not block the
#: next person until the timeout. Not more: every open scan holds crops of
#: somebody's face in this process.
MAX_SESSIONS = 2

#: Wrong PINs per client before the lockout, and how long it lasts.
PIN_TRIES = 5
LOCKOUT_S = 60.0

#: The most clients the wrong-PIN table holds at once. Entries are
#: forgotten once they stop counting (a served lockout, slips older than a
#: lockout), so a real showroom — a PC, a robot, a phone or two — never gets
#: near this. It is here because the key is a client address: while uvicorn
#: still trusted X-Forwarded-For from 127.0.0.1, every rotated header was a
#: fresh key with a fresh five tries, and the table grew one entry per
#: guess. A full table refuses a *new* client without comparing its PIN —
#: dropping the oldest entries instead would hand their owners a new five.
MAX_FAILURE_KEYS = 64

#: The largest image one step may send, as the data URL's length. A full
#: 1080p frame as JPEG is well under a megabyte; four leaves room without
#: letting a LAN client hand the decoder whatever it likes.
MAX_IMAGE_BYTES = 4 * 1024 * 1024

#: What a nonsensical FACE_SCAN_SESSION_S (zero, negative, NaN) falls back
#: to. Zero would drop every scan the moment it started — a switch that is
#: on and cannot be used, with nothing saying why.
DEFAULT_SESSION_S = 600.0

#: After a save, the door camera is held this much longer rather than
#: resumed at once. The person just scanned is still at the lens, and the
#: greeter picks up the new gallery on its next poll — up to GALLERY_POLL_S
#: later. Resumed at once, it confirms them as a *stranger* in about 0.6s,
#: greets them as one and remembers them as one: the scan's whole promise
#: ("หุ่นจำได้แล้ว") broken in the first second after it was made.
SAVE_GRACE_S = greeter.GALLERY_POLL_S + 1.0

#: The key this module holds the door camera under (`greeter.hold`).
HOLD_KEY = "face-scan"

#: How long a finished save is remembered for the cancel that crossed it.
#: The page's close button stays live while the save is out, so "saved, and
#: then the cancel arrived" is a normal ordering, not an attack — and the
#: answer to that cancel is "it was filed", not "no such scan".
FILED_MEMORY_S = 60.0

#: How far past a deadline the expiry timer fires. More than the loop's
#: clock resolution (about 16ms on Windows), so a timer that wakes a tick
#: early re-arms instead of finding nothing due and never firing again.
_SWEEP_SLACK_S = 0.25

#: The refusals the routes share.
_GONE = "ไม่พบการสแกนนี้ หรือหมดเวลาแล้ว — เริ่มใหม่"
_INCOMPLETE = "ยังเก็บภาพไม่ครบทุกท่า — ทำให้ครบก่อนบันทึก"

#: Monotonic clock, named so a test can move it.
_clock = time.monotonic


class ScanError(Exception):
    """A refusal: the sentence the screen shows, and the HTTP status."""

    def __init__(self, reason: str, status: int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


@dataclass
class _Scan:
    created: float
    #: `created` + the session limit, fixed at the start. Not moved by any
    #: request: the capture step's preview loop posts five times a second
    #: whether or not anybody is at the lens, so a limit counted from the
    #: last request never ran out while the overlay stayed open — measured,
    #: a scan 4 hours old with the door camera still paused.
    deadline: float
    client: str
    consented: bool = False
    #: Started with no consent text on screen (see the module docstring):
    #: consented from the start, and the record says STAFF_ATTESTED.
    attested: bool = False
    enroll: enrollment.Session = field(default_factory=enrollment.Session)
    #: Held for the whole of a save, and waited on by a cancel. `Session.save`
    #: copies the batch and then writes shots, the consent record and the
    #: gallery rows without looking back; a cancel that only popped the scan
    #: answered "closed" while all of that landed behind it — and its bare
    #: resume took the post-save grace with it.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    #: The name the batch was filed under, once a save of it succeeded.
    saved: str | None = None


_sessions: dict[str, _Scan] = {}

#: client host -> [wrong PINs so far, locked until, last wrong PIN] (monotonic).
_failures: dict[str, list[float]] = {}

#: Monotonic end of the grace after the latest save. Its own deadline, not
#: a moment in the greeter's: closing some *other* scan inside those three
#: seconds must not take it back.
_grace_until = 0.0

#: session id -> (client, name as filed, when): saves that finished, for
#: the cancel that crossed them (FILED_MEMORY_S).
_filed: dict[str, tuple[str, str, float]] = {}

#: The event-loop timer that ends the next scan due (`_arm_sweep`).
_sweeper: asyncio.TimerHandle | None = None

#: FACE_SCAN_SESSION_S above the greeter's cap is said once, not per scan.
_said_capped = False

#: Around "is this name new" + the save. Two scans saving the same new name
#: in the same second would both pass the check and both write into one
#: folder — the append this module exists to refuse. A thread lock because
#: both halves run in `asyncio.to_thread`.
_names_lock = threading.Lock()


def reset() -> None:
    """Drop every scan, every PIN counter and this module's hold. For tests."""
    global _grace_until, _sweeper
    for scan in list(_sessions.values()):
        scan.enroll.discard()
    _sessions.clear()
    _failures.clear()
    _filed.clear()
    _grace_until = 0.0
    if _sweeper is not None:
        _sweeper.cancel()
        _sweeper = None
    greeter.release(HOLD_KEY)


# -- the owner's words ---------------------------------------------------------

def policy_path() -> Path:
    return POLICY_DIR / f"{consent.VERSION}.txt"


def policy_text() -> str | None:
    """The consent wording people press "ยินยอม" under, or None.

    Named by `consent.VERSION` because that is the version the record gets:
    the text shown and the version stamped on the record must be the same
    document, or the record says somebody agreed to words they never saw.
    None for a missing, empty or unreadable file — never a default.
    """
    try:
        raw = policy_path().read_bytes()
    except OSError:
        return None
    try:
        # utf-8-sig: Notepad's "UTF-8 with BOM" is how this file will most
        # likely be saved, and a BOM is not part of anybody's wording.
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        logger.warning("%s is not UTF-8 — the face scan stays refused until "
                       "it is saved as UTF-8", policy_path())
        return None
    return text if text.strip() else None


# -- gates -----------------------------------------------------------------------

def _ttl() -> float:
    """The session limit: how long a scan may last from its start.

    No longer than `greeter.MAX_PAUSE_S`. The limit is also how long the
    scan holds the door camera off, and the greeter cuts every hold at that
    cap — so FACE_SCAN_SESSION_S=1800 left a scan open and saveable for a
    quarter of an hour with the greeter already awake: the person still at
    the lens during the name form confirmed as a stranger, greeted as one
    and remembered as one. One limit for both, the shorter.
    """
    global _said_capped
    try:
        ttl = float(settings.face_scan_session_s)
    except (TypeError, ValueError):
        return DEFAULT_SESSION_S
    if not (math.isfinite(ttl) and ttl > 0):
        return DEFAULT_SESSION_S
    if ttl > greeter.MAX_PAUSE_S:
        if not _said_capped:
            _said_capped = True
            logger.warning("FACE_SCAN_SESSION_S=%.0f is longer than the door camera can "
                           "be held off — a scan lasts %.0fs", ttl, greeter.MAX_PAUSE_S)
        return greeter.MAX_PAUSE_S
    return ttl


def _require_switched_on() -> None:
    if not settings.face_scan_enabled:
        raise ScanError("การสแกนใบหน้าปิดอยู่ — เปิดด้วย FACE_SCAN_ENABLED=true ใน .env", 403)
    if not (settings.face_scan_pin or "").strip():
        raise ScanError("ยังไม่ได้ตั้งรหัส FACE_SCAN_PIN ใน .env — "
                        "สแกนใบหน้าต้องมีรหัสพนักงานก่อน", 403)
    if settings.multi_session:
        raise ScanError("เครื่องนี้เปิด MULTI_SESSION อยู่ — การสแกนใบหน้า"
                        "ใช้ได้เฉพาะเครื่องที่ประจำหุ่นตัวเดียว", 403)


def _require_models() -> None:
    if not faces.available():
        raise ScanError("ยังไม่มีโมเดลใบหน้าบนเครื่องนี้ — "
                        "รัน python scripts/fetch_face_model.py ก่อน", 503)


def _pin_ok(pin) -> bool:
    if not isinstance(pin, str):
        return False
    # Encoded before `compare_digest`, which refuses non-ASCII str — the
    # same trap as the WS_TOKEN gate: a Thai-keyboard slip would otherwise
    # be a TypeError, and a 500 reads as "the gate is broken".
    return secrets.compare_digest(pin.strip().encode("utf-8"),
                                  (settings.face_scan_pin or "").strip().encode("utf-8"))


def _forget_old_failures(now: float) -> None:
    """Drop the counters that no longer count: a lockout that has been
    served (a fresh five follows it anyway), and slips older than a lockout.

    Forgetting a slip after LOCKOUT_S gives a guesser nothing the lockout
    does not: four guesses a minute unlocked, or five and a minute locked.
    What it stops is the table only ever growing — nothing used to remove
    an entry whose count stayed under five."""
    for key, (count, locked_until, last) in list(_failures.items()):
        if (now >= locked_until) if locked_until else (now - last >= LOCKOUT_S):
            del _failures[key]


def _check_pin(pin, client: str) -> None:
    now = _clock()
    _forget_old_failures(now)
    entry = _failures.get(client)
    if entry is None and len(_failures) >= MAX_FAILURE_KEYS:
        # Refused without comparing, like a lockout, and for everybody new
        # until the table drains: this many clients guessing inside a
        # minute is one client with many addresses.
        raise ScanError("มีการใส่ PIN ผิดจากหลายเครื่องเกินไป — รอ %d วินาทีแล้วลองใหม่"
                        % int(LOCKOUT_S), 429)
    count, locked_until = (int(entry[0]), entry[1]) if entry else (0, 0.0)
    if locked_until:
        # Still running — a served one was forgotten above. Refused without
        # comparing: a lockout that still checks the PIN is a lockout that
        # still answers the guess.
        raise ScanError("ใส่ PIN ผิดหลายครั้ง — รออีก %d วินาทีแล้วลองใหม่"
                        % max(1, math.ceil(locked_until - now)), 429)
    if _pin_ok(pin):
        _failures.pop(client, None)
        return
    count += 1
    if count >= PIN_TRIES:
        _failures[client] = [0, now + LOCKOUT_S, now]
        logger.warning("face scan: %d wrong PINs from %s — locked for %.0fs",
                       PIN_TRIES, client or "?", LOCKOUT_S)
        raise ScanError("ใส่ PIN ผิด %d ครั้ง — รอ %d วินาทีแล้วลองใหม่"
                        % (PIN_TRIES, int(LOCKOUT_S)), 429)
    _failures[client] = [count, 0.0, now]
    raise ScanError("รหัส PIN ไม่ถูกต้อง", 403)


# -- sessions ------------------------------------------------------------------

def _hold_greeter() -> None:
    """Point the door camera's hold at what is open *now*.

    Until the latest open scan's deadline or the end of the post-save
    grace, whichever is later; released when neither is left. Recomputed
    from this module's own state after every change, under its own key, so
    that ending one scan gives back exactly its share: the one global
    deadline this used to push could only be zeroed, which took another
    scan's grace with it, or left alone, which kept a saved scan's pause
    running for its whole limit because a second scan was still open.
    Never past a scan's own deadline — the hold is that deadline.
    """
    until = max([_grace_until, *(scan.deadline for scan in _sessions.values())])
    greeter.hold(HOLD_KEY, until - _clock())


def _close(sid: str, why: str) -> None:
    scan = _sessions.pop(sid, None)
    if scan is None:
        return
    # Emptied here rather than left to the garbage collector: a thread still
    # holding the Session (a look in flight) would keep the crops alive.
    scan.enroll.discard()
    logger.info("face scan %s (%d still open)", why, len(_sessions))
    _hold_greeter()
    _arm_sweep()


def _forget_old_filings(now: float) -> None:
    for sid, (_client, _name, at) in list(_filed.items()):
        if now - at >= FILED_MEMORY_S:
            del _filed[sid]


def _sweep() -> None:
    """End every scan past its deadline, with its crops.

    A scan in the middle of a save is left to the save: its files are
    being written, and the save ends the scan either way (saved), or keeps
    it on a refusal — past its deadline by then, so the next sweep ends it.
    """
    now = _clock()
    _forget_old_filings(now)
    for sid, scan in list(_sessions.items()):
        if now >= scan.deadline and not scan.lock.locked():
            _close(sid, "expired")


def _on_sweep_timer() -> None:
    global _sweeper
    _sweeper = None
    _sweep()
    _arm_sweep()


def _arm_sweep() -> None:
    """Schedule the next expiry on the event loop's own clock.

    Expiry used to happen only inside a request (`_get`, `start`), so a
    robot tab killed mid-scan — its keepalive cancel lost with it — kept
    the person's crops in this process until somebody next opened a scan,
    or until a restart. One timer, for the scan due first, re-armed on
    every start and every end. Without a running loop (a test calling in
    directly) there is nothing to schedule on, and the next request's
    sweep still ends it.
    """
    global _sweeper
    if _sweeper is not None:
        _sweeper.cancel()
        _sweeper = None
    if not _sessions:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    due = min(scan.deadline for scan in _sessions.values())
    _sweeper = loop.call_later(max(due - _clock(), 0.0) + _SWEEP_SLACK_S, _on_sweep_timer)


def _get(sid, client: str) -> _Scan:
    _sweep()
    scan = _sessions.get(sid) if isinstance(sid, str) else None
    # Bound to the client that started it. Another device holding the id is
    # answered exactly like a wrong id, so a mismatch teaches it nothing.
    if scan is None or scan.client != client:
        raise ScanError(_GONE, 404)
    return scan


def _require_consent(scan: _Scan) -> None:
    if not scan.consented:
        raise ScanError("ยังไม่ได้กดยินยอม — ให้คนที่จะถูกสแกนอ่านแล้วกดยินยอมก่อน", 409)


def _check_image(image) -> str:
    if not isinstance(image, str) or not image:
        raise ScanError("ไม่ได้ส่งภาพมา", 400)
    if len(image) > MAX_IMAGE_BYTES:
        raise ScanError("ภาพใหญ่เกิน 4 MB — ส่งภาพที่เล็กกว่านี้", 413)
    return image


def _gallery_names() -> list[str]:
    """Names in the live gallery. ScanError when there is a file that
    cannot be read: `add_to_gallery` would refuse it anyway, *after* the
    shots and the consent were written — a person scanned, told they are
    known, and greeted as a stranger. Better said before the five poses."""
    path = Path(settings.face_gallery).expanduser()
    if not path.is_file():
        return []
    try:
        return list(faces.Gallery.load(path).names)
    except ValueError:
        raise ScanError("แกลเลอรีใบหน้าเดิมอ่านไม่ได้หรือสร้างจากโมเดลอื่น — "
                        "ให้รัน build-face-gallery.cmd ก่อน แล้วค่อยสแกน", 503) from None


def _key(name: str) -> str:
    # One person, one key: case (Windows folders ignore it anyway), spacing
    # and composed/decomposed marks must not make "the same name" new.
    return unicodedata.normalize("NFC", " ".join(str(name).split())).casefold()


def _name_taken(name: str) -> bool:
    key = _key(name)
    root = enrollment.ENROLLED
    if root.is_dir() and any(p.is_dir() and _key(p.name) == key for p in root.iterdir()):
        return True
    # A record with no folder is still a person — most of all a revoked
    # one, whose folder may be gone while the revocation must stand.
    try:
        people = consent.load()
    except consent.Unreadable as exc:
        # A damaged people.json refuses every save (it cannot say who is
        # revoked) — said in Thai as 503, not a bare 500 that invites a
        # retry that can never succeed.
        raise ScanError(str(exc), 503) from None
    if any(_key(n) == key for n in people):
        return True
    return any(_key(n) == key for n in _gallery_names())


#: `enrollment.review` answers in English for the operator at the station,
#: and one of its answers names the *other* person the face matched. On a
#: LAN screen, read by the person being scanned, that is somebody else's
#: name handed to a stranger. Mapped by prefix; anything else without a
#: Thai character gets the general sentence, so no English and no name
#: ever reaches this screen from that side.
_REVIEW_THAI = (
    ("this face is already in the gallery",
     "ใบหน้านี้มีอยู่ในระบบแล้วภายใต้ชื่ออื่น — ให้พนักงานตรวจที่สถานีลงทะเบียน"),
    ("the shots do not all look like the same person",
     "ภาพที่เก็บไม่ใช่คนเดียวกันทั้งหมด (มีคนอื่นเข้ามาในภาพ) — ปิดแล้วเริ่มสแกนใหม่"),
)
_SAVE_REFUSED = "บันทึกไม่สำเร็จ — ให้พนักงานตรวจที่สถานีลงทะเบียน"
_GALLERY_REFUSED = ("บันทึกภาพแล้ว แต่ยังเพิ่มเข้าแกลเลอรีไม่ได้ — "
                    "ให้พนักงานรัน build-face-gallery.cmd")


def _has_thai(text: str) -> bool:
    return any("฀" <= ch <= "๿" for ch in text)


def _screen_reason(reason: str, fallback: str = _SAVE_REFUSED) -> str:
    for prefix, thai in _REVIEW_THAI:
        if reason.startswith(prefix):
            return thai
    if _has_thai(reason):
        return reason
    return fallback


#: The other English source: `usable_face`, which `Session.capture` answers
#: with verbatim ("come closer", "2 faces - only one person please"). It
#: reaches this screen whenever the full-size capture frame disagrees with
#: the preview that passed a moment earlier — near the size bar, or after a
#: small movement — and it lands in the one line the person at the lens is
#: reading. Mapped to the sentences `Session.judge` already says for the
#: same four conditions, and applied to look replies too: the rule is "no
#: English on this screen", not "no English from capture".
_FRAME_THAI = (
    ("no face", "ยังไม่เห็นหน้า"),
    ("come closer", "เข้ามาใกล้อีกนิด"),
    ("could not read the face", "อ่านใบหน้าไม่ได้"),
)
_FRAME_CROWD = re.compile(r"(\d+) faces\b")
_FRAME_REFUSED = "เก็บภาพไม่สำเร็จ ลองอีกครั้ง"


def _frame_reason(reason) -> str:
    reason = reason if isinstance(reason, str) else ""
    if not reason or _has_thai(reason):
        return reason                       # "" is "this frame is the shot"
    for english, thai in _FRAME_THAI:
        if reason.startswith(english):
            return thai
    crowd = _FRAME_CROWD.match(reason)
    if crowd:
        return f"มี {crowd.group(1)} หน้าในภาพ — ทีละคน"
    return _FRAME_REFUSED


def _frame_reply(reply: dict) -> dict:
    if "reason" in reply:
        reply["reason"] = _frame_reason(reply["reason"])
    return reply


def _save_new(scan: _Scan, name: str) -> dict:
    with _names_lock:
        if _name_taken(name):
            raise ScanError("ชื่อนี้มีอยู่แล้ว — ถ้าจะสแกนเพิ่มให้ใช้สถานีลงทะเบียน", 409)
        # The one consent writer: Session.save -> save_shots ->
        # consent.record(name, by=name). The person pressed ยินยอม on the
        # owner's text (or, with no text, staff asked them — and the version
        # says which) and then stood for five poses; this module records
        # nothing of its own.
        version = consent.STAFF_ATTESTED if scan.attested else consent.VERSION
        return scan.enroll.save(name, add_to_gallery=True, consent_version=version)


def _judge(scan: _Scan, image: str, full_width, what: str) -> dict:
    frame = enrollment.decode_data_url(image)
    if frame is None:
        raise ScanError("อ่านภาพไม่ได้ — ลองใหม่", 400)
    return getattr(scan.enroll, what)(frame, full_width)


# -- the steps -------------------------------------------------------------------

async def start(pin, client: str) -> dict:
    """PIN in, a new scan and the owner's consent text out — or, with no
    text, a scan that is consented already (`consented: true`) and goes
    straight to the camera."""
    _require_switched_on()
    _sweep()
    # The PIN before anything else is said about this machine: a caller
    # without it learns neither whether the models are here nor whether
    # the owner has written the consent text yet.
    _check_pin(pin, client)
    _require_models()
    text = policy_text()
    if text is None and policy_path().exists():
        # Somebody put a file there to be shown and it cannot be: that is a
        # broken consent screen, not the owner's "no screen" — refused
        # rather than quietly treated as staff-attested.
        raise ScanError(f"ไฟล์ data/policies/{consent.VERSION}.txt ว่างหรืออ่านไม่ได้ "
                        "(ต้องบันทึกเป็น UTF-8) — แก้ไฟล์ หรือลบทิ้งถ้าไม่ใช้หน้ายินยอม", 503)
    await asyncio.to_thread(_gallery_names)
    if len(_sessions) >= MAX_SESSIONS:
        raise ScanError("มีการสแกนค้างอยู่ %d รายการแล้ว — ปิดอันเดิมก่อน หรือรอให้หมดเวลา"
                        % MAX_SESSIONS, 409)
    now = _clock()
    sid = secrets.token_urlsafe(16)
    attested = text is None
    scan = _Scan(created=now, deadline=now + _ttl(), client=client,
                 consented=attested, attested=attested)
    _sessions[sid] = scan
    _hold_greeter()
    _arm_sweep()
    logger.info("face scan started (%d open%s)", len(_sessions),
                ", staff-attested" if attested else "")
    return {"ok": True, "session": sid, "consent_text": text,
            "consent_version": consent.STAFF_ATTESTED if attested else consent.VERSION,
            "consented": scan.consented, **scan.enroll.state()}


async def answer_consent(sid, agree, client: str) -> dict:
    """The scanned person's answer. Only JSON `true` is a yes; anything
    else ends the scan — a malformed answer must fail toward "no"."""
    if agree is not True:
        # Honoured whatever the switches say now: ending a scan only ever
        # removes a face from memory. Through the same door as cancel, so a
        # "no" that lands while a save is writing waits for it and says what
        # was filed, instead of answering "closed" over it (review, round 2).
        return await _end_scan(sid, client, "declined")
    scan = _get(sid, client)
    _require_switched_on()
    scan.consented = True
    # Every step re-asserts the hold — up to this scan's deadline, which
    # no step moves.
    _hold_greeter()
    return {"ok": True, **scan.enroll.state()}


async def look(sid, image, full_width, client: str) -> dict:
    """One preview frame: where the face is, whether it is the shot."""
    _require_switched_on()
    # The session before the models, as `start` puts the PIN before them:
    # the other order answered a made-up id with 503 "no face model" on a
    # machine without the models and 404 on one with them — the fact
    # `start` keeps behind the PIN, handed to anybody without one.
    scan = _get(sid, client)
    _require_models()
    _require_consent(scan)
    image = _check_image(image)
    _hold_greeter()
    return _frame_reply(await asyncio.to_thread(_judge, scan, image, full_width, "judge"))


async def capture(sid, image, full_width, client: str) -> dict:
    """Take the shot the current step asks for, if this frame is it."""
    _require_switched_on()
    scan = _get(sid, client)              # before the models: see `look`
    _require_models()
    _require_consent(scan)
    image = _check_image(image)
    _hold_greeter()
    return _frame_reply(await asyncio.to_thread(_judge, scan, image, full_width, "capture"))


async def save(sid, name, client: str) -> dict:
    """File the batch under a new name, into the live gallery, and end."""
    global _grace_until
    _require_switched_on()
    scan = _get(sid, client)
    _require_consent(scan)
    try:
        name = enrollment.safe_person_name(name if isinstance(name, str) else "")
    except ValueError as exc:
        raise ScanError(str(exc), 400) from None
    async with scan.lock:
        if _sessions.get(sid) is not scan:
            # Ended while this request waited for the lock — a cancel, the
            # clock, or the first tap of a double tap that already saved.
            raise ScanError(_GONE, 404)
        if scan.enroll.state()["step"] is not None:
            # Every pose the step flow asks for (a step this camera cannot
            # offer is skipped by the flow itself). The page only shows the
            # name form once they are done, but the rule is the server's:
            # `Session.save` refuses only an *empty* batch, and one shot
            # "agrees with itself" (shots_agree is 1.0 for one), so a direct
            # POST after a single capture filed one frame whose same-person
            # check never ran.
            raise ScanError(_INCOMPLETE, 409)
        _hold_greeter()
        result = await asyncio.to_thread(_save_new, scan, name)
        if result.get("ok"):
            scan.saved = str(result.get("name") or name)
            now = _clock()
            _filed[sid] = (client, scan.saved, now)
            _grace_until = max(_grace_until, now + SAVE_GRACE_S)
            _close(sid, "saved")
    if not result.get("ok"):
        # The batch is kept (Session.save keeps it on a refusal) and so is
        # the scan, until its own deadline: the next move is the person's.
        # (Past that deadline already, the sweep that skipped it while the
        # save held the lock ends it within _SWEEP_SLACK_S.)
        raise ScanError(_screen_reason(str(result.get("reason") or "")), 409)
    reply = {"ok": True, "name": scan.saved, "total": result.get("total"),
             "gallery_total": result.get("gallery_total")}
    if result.get("gallery_reason"):
        # Saved and consented, but not in the live gallery: said beside the
        # success, never instead of it — "not saved" would invite a second
        # save of the same person.
        reply["gallery_reason"] = _screen_reason(str(result["gallery_reason"]),
                                                 _GALLERY_REFUSED)
    return reply


async def cancel(sid, client: str) -> dict:
    """End a scan and forget its shots. Needs no switch: only removes.

    A save already writing is waited for, not cut: its shots, consent
    record and gallery rows cannot be half-undone, and the cancel used to
    answer "closed" while all three landed behind it (and its bare resume
    took the post-save grace along). If the save got there first, the
    reply says so — `saved` is the name as filed — whether it finished
    while this waited or a moment before this arrived.
    """
    return await _end_scan(sid, client, "cancelled")


async def _end_scan(sid, client: str, why: str) -> dict:
    """Every way a scan is ended by the page: cancel and a consent "no".
    One implementation, because the first fix of the save race covered
    cancel and left the consent route racing exactly as before."""
    _forget_old_filings(_clock())
    filed = _filed.get(sid) if isinstance(sid, str) else None
    if filed is not None and filed[0] == client:
        return {"ok": True, "closed": True, "saved": filed[1]}
    scan = _get(sid, client)
    async with scan.lock:
        if scan.saved is None:
            _close(sid, why)
    if scan.saved is not None:
        return {"ok": True, "closed": True, "saved": scan.saved}
    return {"ok": True, "closed": True}
