"""
The guests who scanned at the launch event's booth, on the robot (owner, 4 Nov
2026: "ให้คนแสกนหน้า ตัว จากตู้สแกน แล้วเก็บไว้ ... เวลาที่หุ่นเห็นหน้าคนนี้ก็เดินเข้าไปทักเลย").

Their vectors live in FACE_GUESTS_FILE, beside the staff gallery and not in it.
Empty setting = this module does nothing and the greeter loads what it always
loaded. A file of their own because of what happens to it:

* `build_face_gallery.py` rewrites gallery.npz from enrolled/ folders; a guest
  has no folder on the robot and would be gone with no word.
* their consent ends (the notice names the date -- a year, owner 23 Nov 2026:
  "จะให้หุ่นจำได้เวลาวันอื่นที่ลูกค้ามาดูโครงการ"): deleting the guests must not be
  able to touch a colleague.
* `robot_server.py deploy` never ships this file (it is not in EXTRA_GLOBS, and
  nothing under data/faces/ is deleted without --force: tests/test_guests.py).

Same rule as `show_unit(room)`: nothing here decides who somebody is. A row goes
in only with the consent record the booth wrote when the guest pressed "ยินยอม"
(`consent.record_witnessed`), the record goes in first (a row with no record
reads as MISSING, which FACE_REQUIRE_CONSENT=false tolerates), and a revocation
removes the rows as well as marking the record: the watcher already refuses the
name on the next frame, and a vector nobody may use has no reason to stay.
"""
from __future__ import annotations

import base64
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from app import consent, enrollment, faces
from app.config import settings

logger = logging.getLogger("condo_voice.guests")

ROOT = Path(__file__).resolve().parent.parent

#: What `Sighting.group` says, and `greeting_for` puts beside the name.
GROUP = "แขกงาน"
#: Rows one guest may bring. The booth sends MAX_ROWS (the poses `booth.ROWS`
#: keeps); more is not a person, it is somebody filling the gallery.
MAX_ROWS = 8
MAX_GUESTS = 1000
#: Around every load-change-save, with `faces.file_lock` inside it: the rebuild
#: lesson (2026-09-29) applied here from the start.
_lock = threading.Lock()
LOCK_WAIT_S = 3.0
_BUSY = "guest list is being written by another process"
#: A vector this far from unit length is not this model's output.
NORM_TOLERANCE = 0.01

_purged_at = [0.0]
PURGE_EVERY_S = 3600.0
#: A withdrawn guest's record (name, dates, "withdrawn") outlives the rows only
#: this long: long enough that a save still queued at a booth meets it, short
#: of the year the consent would have run (review 23 Nov: the screen says
#: "ถอนแล้ว ลบข้อมูลแล้ว", and a year of the name is not that).
REVOKED_KEEP_DAYS = 7.0


def enabled() -> bool:
    return bool((settings.face_guests_file or "").strip())


def path() -> Path | None:
    name = (settings.face_guests_file or "").strip()
    if not name:
        return None
    file = Path(name).expanduser()
    return file if file.is_absolute() else ROOT / file


def load() -> faces.Gallery:
    """The guest rows; an empty gallery with no file. ValueError for a file
    that cannot be used (`faces.Gallery.load`)."""
    file = path()
    if file is None or not file.is_file():
        return faces.Gallery([], np.zeros((0, 512), np.float32))
    return faces.Gallery.load(file)


def live_gallery(base_path: Path) -> faces.Gallery | None:
    """The staff gallery plus the guests, or None when there is neither. A
    damaged guest file is not a reason to lose the staff: said, and left out."""
    base = faces.Gallery.load(base_path) if base_path.is_file() else None
    try:
        guests = load()
    except ValueError as exc:
        logger.warning("guest list not used: %s", exc)
        return base
    if base is None:
        return guests if len(guests) else None
    return base.appended(guests.names, guests.vecs, guests.meta) if len(guests) else base


def count() -> int:
    """How many guests, for /guests/status: a number, never who."""
    try:
        return len(set(load().names))
    except ValueError:
        return 0


def decode_vectors(items) -> np.ndarray | None:
    """Base64 float32 rows, each unit length and of this model's width, or None."""
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ROWS:
        return None
    rows = []
    for item in items:
        if not isinstance(item, str):
            return None
        try:
            raw = base64.b64decode(item, validate=True)
        except ValueError:
            return None
        if len(raw) != 512 * 4:
            return None
        vec = np.frombuffer(raw, dtype=np.float32)
        if not np.all(np.isfinite(vec)) or abs(float(np.linalg.norm(vec)) - 1.0) > NORM_TOLERANCE:
            return None
        rows.append(vec)
    return np.stack(rows)


def encode_vector(vec) -> str:
    return base64.b64encode(np.asarray(vec, dtype=np.float32).tobytes()).decode("ascii")


def _same_face_elsewhere(vecs: np.ndarray, name: str) -> bool:
    """Is one of these rows somebody else already known here, at the naming bar?

    One person under two names is never named at all: every frame has a "second
    person" inside the margin (8 Oct: 23 of 27 "recognised but not named" lines
    were one colleague enrolled twice). Better said at the booth, where they are
    standing, than discovered by a robot that never greets them. Tested row by
    row, as `enrollment.review` does and for its reason.

    At the event's bar, not the door's: two different guests over 0.50 happen in
    a gallery this size (studio photos against 200 people, 3.6%), and telling
    somebody "this face is already here" for a stranger's resemblance is a person
    turned away. Missing a real duplicate costs only that name never being said."""
    known = live_gallery(Path(settings.face_gallery).expanduser())
    if known is None or not len(known):
        return False
    sims = vecs @ known.vecs.T
    for row in sims:
        for i in np.argsort(-row)[:5]:
            if row[int(i)] < max(settings.face_threshold, settings.face_approach_threshold):
                break
            if known.names[int(i)] != name:
                return True
    return False


def add(payload: dict) -> dict:
    """One guest from the booth. {"ok": True, "state": recorded|already} or
    {"ok": False, "error": key}. Never raises for what a client sent.

    Refuses rather than repairs: a wrong model tag is a gallery that ranks
    strangers above the right person (faces.py "Two embedding spaces").
    """
    if not enabled():
        return {"ok": False, "error": "off"}
    try:
        name = enrollment.safe_person_name(payload.get("name") if isinstance(payload.get("name"), str) else "")
    except ValueError:
        return {"ok": False, "error": "bad_name"}
    if payload.get("model_tag") != faces.MODEL_TAG:
        return {"ok": False, "error": "wrong_model"}
    vecs = decode_vectors(payload.get("vectors"))
    if vecs is None:
        return {"ok": False, "error": "bad_vectors"}
    file = path()
    try:
        with _lock, faces.file_lock(file, LOCK_WAIT_S, _BUSY):
            current = load()
            people = set(current.names)
            if name not in people and len(people) >= MAX_GUESTS:
                return {"ok": False, "error": "full"}
            # Before the record: refused here, nothing was written.
            if _same_face_elsewhere(vecs, name):
                return {"ok": False, "error": "same_face"}
            if name in _staff_names():
                return {"ok": False, "error": "name_taken"}
            if name in people and not _same_person(vecs, current, name):
                # Another face under a name a guest already holds (a second booth,
                # a booth that was wiped): it would replace that guest's rows and
                # record with somebody else's. The same face scanning again is
                # still a re-scan (review 23 Nov).
                return {"ok": False, "error": "name_taken"}
            state = consent.record_witnessed(name, payload.get("consent"), str(payload.get("station") or ""))
            if state == "revoked":
                return {"ok": False, "error": "revoked"}
            if state == "taken":
                return {"ok": False, "error": "name_taken"}
            keep = [i for i, n in enumerate(current.names) if n != name]
            if state == "already" and name in people:
                return {"ok": True, "state": state}
            rows = faces.Gallery([current.names[i] for i in keep], current.vecs[keep],
                                 [current.meta[i] for i in keep])
            # `since`: the day they scanned, so a greeting on a later visit is a
            # welcome back, not a first meeting (greeter.greeting_for).
            since = str((payload.get("consent") or {}).get("consent_at") or "")
            metas = [{"group": GROUP, "source": "booth", "since": since} for _ in vecs]
            rows.appended([name] * len(vecs), vecs, metas).save(file)
    except faces.LockBusy:
        return {"ok": False, "error": "busy"}
    except consent.Unreadable:
        return {"ok": False, "error": "consent_unreadable"}
    except ValueError as exc:
        # record_witnessed's refusals, or a guest file that cannot be read.
        logger.info("guest refused: %s", type(exc).__name__)
        return {"ok": False, "error": "bad_consent"}
    except OSError:
        logger.exception("guest list could not be written")
        return {"ok": False, "error": "write_failed"}
    logger.info("guest added: %d rows, %d guests", len(vecs), len(set(load().names)))
    return {"ok": True, "state": state}


def _same_person(vecs: np.ndarray, current: faces.Gallery, name: str) -> bool:
    """Do these rows match the rows already kept under `name`, at the naming bar?"""
    mine = [i for i, n in enumerate(current.names) if n == name]
    if not mine:
        return True
    best = float((vecs @ current.vecs[mine].T).max())
    return best >= max(settings.face_threshold, settings.face_approach_threshold)


def _staff_names() -> set[str]:
    """Names in the robot's own gallery. A guest under a colleague's name would
    share one consent record with them: the guest's withdrawal or end would be
    the colleague's (review 23 Nov)."""
    base = Path(settings.face_gallery).expanduser()
    if not base.is_file():
        return set()
    try:
        return set(faces.Gallery.load(base).names)
    except ValueError:
        return set()


def _drop_rows(names: set[str]) -> int:
    file = path()
    if file is None or not file.is_file():
        return 0
    current = load()
    keep = [i for i, n in enumerate(current.names) if n not in names]
    dropped = len(current) - len(keep)
    if dropped:
        faces.Gallery([current.names[i] for i in keep], current.vecs[keep],
                      [current.meta[i] for i in keep]).save(file)
    return dropped


def revoke(name) -> dict:
    """Withdraw a guest: the record says so, and the rows are gone.

    Recorded even when nobody by that name is here yet: `consent.revoke` writes
    it, so a save still queued at the booth arrives to a refusal."""
    if not enabled():
        return {"ok": False, "error": "off"}
    try:
        name = enrollment.safe_person_name(name if isinstance(name, str) else "")
    except ValueError:
        return {"ok": False, "error": "bad_name"}
    try:
        with _lock, faces.file_lock(path(), LOCK_WAIT_S, _BUSY):
            # Stamped as a guest's (a name that never arrived is still purged) and
            # kept out of server.log.
            had = consent.revoke(name, version=consent.EVENT_GUEST, log_name=False)
            dropped = _drop_rows({name})
    except faces.LockBusy:
        return {"ok": False, "error": "busy"}
    except ValueError:
        return {"ok": False, "error": "consent_unreadable"}
    except OSError:
        logger.exception("guest could not be removed")
        return {"ok": False, "error": "write_failed"}
    return {"ok": True, "had": bool(had), "rows": dropped}


def purge(now: datetime | None = None) -> dict:
    """Drop every guest past their consent: rows and record. The revoked keep
    only their record, for REVOKED_KEEP_DAYS (a queued save must still meet
    it) or until it would have expired, whichever is first. Staff are not looked at: only records
    stamped EVENT_GUEST.

    And a row whose own guest record is gone (a `deploy --force` that put this
    PC's people.json over the robot's, a hand edit): nothing says when it ends
    any more, so nothing would ever remove it, and FACE_REQUIRE_CONSENT=false
    reads a missing record as fine. A year of that is a face kept with no
    consent behind it. `add` writes the record before the row under this same
    lock, so a row being added is never seen without its record."""
    if not enabled():
        return {"rows": 0, "records": 0}
    now = now or datetime.now(timezone.utc).astimezone()
    try:
        with _lock, faces.file_lock(path(), LOCK_WAIT_S, _BUSY):
            people = consent.load()
            guests = {n: e for n, e in people.items() if e.get("consent_version") == consent.EVENT_GUEST}
            over = {n for n in guests if consent._when(guests[n].get("expires_at")) is None
                    or consent._when(guests[n]["expires_at"]) <= now}
            revoked = {n for n in guests if guests[n].get("revoked_at")}
            stale = {n for n in revoked if (consent._when(guests[n]["revoked_at"]) is None or
                     consent._when(guests[n]["revoked_at"]) + timedelta(days=REVOKED_KEEP_DAYS) <= now)}
            orphans = set(load().names) - set(guests)
            rows = _drop_rows(over | revoked | orphans)
            records = consent.forget(over | stale)
    except (ValueError, OSError) as exc:
        logger.warning("guest purge skipped: %s", type(exc).__name__)
        return {"rows": 0, "records": 0, "skipped": True}
    if rows or records:
        logger.info("guest purge: %d rows, %d records removed", rows, records)
    return {"rows": rows, "records": records}


def purge_due() -> bool:
    """Asked by the greeter's loop: an hour apart, first at the first ask."""
    if not enabled():
        return False
    now = time.monotonic()
    if _purged_at[0] and now - _purged_at[0] < PURGE_EVERY_S:
        return False
    _purged_at[0] = now
    return True


def reset() -> None:
    _purged_at[0] = 0.0
