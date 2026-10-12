"""
Who agreed to be recognised, when, and until when.

`data/faces/people.json` — one record per name, the record the outside
review (docs/review/, 2026-09-01) said a face system must have before it
says anybody's name out loud:

    {"โชกุน": {"consent_at": "2026-08-29T16:00:53+07:00",
               "consent_by": "โชกุน",
               "consent_version": "face-policy-v1",
               "expires_at": "2027-08-29T16:00:53+07:00",
               "revoked_at": null}}

Three facts about this file that the code below enforces:

- **Enrolling yourself at the station is consent.** `enrollment.save_shots`
  records it: the person stood in front of the camera, took five shots of
  themselves and pressed save. Nobody else can do that for them, which is
  what makes it consent rather than a checkbox.
- **Staff portraits from `staff.csv` are not.** Those pictures came from HR
  and were labelled by the owner; the people in them never did anything.
  Their records are simply absent, and `FACE_REQUIRE_CONSENT` decides what
  absence means: tolerated (default, so a pull changes nothing) or "greet
  without the name" — the switch to flip once consent has actually been
  collected, not before. **Nothing in this module invents a consent record
  for anybody.**
- **A revocation is honoured immediately, everywhere.** `status()` is read
  at decision time by the watcher, not only when the gallery is rebuilt,
  so `scripts/revoke_face.py` takes effect on the next frame; the rebuild
  then drops the vectors too. Revoked and expired are never tolerated,
  whatever the switch says — they are explicit, absence is not.

And one about the file itself: **a file that is there but cannot be read
is not an empty file.** `load()` used to answer every read or parse error
with `{}` — "everybody unrecorded" — and the review of 2026-09-29 showed
where that goes: a revoked person reads as MISSING, which is tolerated, so
the watcher names them; and the next `record()` saved `{}` plus one entry,
deleting every other record and every revocation in one write. Now only a
*missing* file is `{}`. An unreadable one raises `Unreadable`: writers
refuse, and `status()` answers UNREADABLE, which nothing ever names.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("condo_voice.consent")

#: Bump when the policy text people agree to changes; old records keep the
#: version they were given, and a future check can decide what to do with
#: records from an older policy.
VERSION = "face-policy-v1"

#: Stamped instead of VERSION when a scan on the call screen showed no
#: consent text: the scan was opened with the staff PIN, it is for staff who
#: already consented as staff, and the operator asks the person at the lens
#: before starting (owner, 2026-09-29: "ไม่ต้องมี เพราะใช้กับพนักงานซึ่งได้รับ
#: ความยินยอมแล้ว แล้วก็คนที่จะเข้าได้ก็ต้องถามคนที่จะแสกนอยู่แล้ว"). A string
#: of its own, never VERSION: a record must say which words the person saw,
#: and here they saw none — the record says so rather than naming a policy
#: document nobody read.
STAFF_ATTESTED = "staff-attested"

OK, MISSING, REVOKED, EXPIRED = "ok", "missing", "revoked", "expired"

#: The file is there and cannot be read. Never allowed to name anybody,
#: whatever FACE_REQUIRE_CONSENT says: a revocation might be in there.
UNREADABLE = "unreadable"

#: Around every load-modify-save. Two enrolments finishing together in one
#: server (two kiosks, one process) would each load the file, add their own
#: person and save — and the second save drops the first person's record.
#: Atomic replace stops a torn file; it does nothing for a lost update.
#: This orders the threads of one process; `faces.file_lock` (taken inside
#: it) orders the processes — see `_locked`.
_write_lock = threading.Lock()

#: How long a writer waits for another process's write of this file. Those
#: take about ten milliseconds; five seconds is somebody else's write that
#: is stuck, and then the answer is a refusal, not a write over it.
LOCK_WAIT_S = 5.0
_BUSY = ("ไฟล์ความยินยอม (people.json) กำลังถูกโปรแกรมอื่นเขียนอยู่ — "
         "ยังไม่ได้บันทึก ลองใหม่อีกครั้ง")

#: How often, and how long apart, `load()` retries a read Windows refused.
#: A reader that opens the file during another process's `os.replace` gets
#: PermissionError (measured in the review: 141 of them against 74 writes
#: in a tight loop). The window is microseconds; this outwaits it without
#: holding up a camera frame for long.
_READ_ATTEMPTS = 10
_READ_PAUSE_S = 0.02

#: The warning for an unreadable file is said once a minute, not once per
#: camera frame — the watcher asks on every one.
_WARN_EVERY_S = 60.0
_last_warned = [0.0]


class Unreadable(ValueError):
    """people.json exists and cannot be read. The message is Thai: it ends
    up in front of whoever pressed save, and it names no one."""


def people_path() -> Path:
    """Next to the enrolment store, so a test that relocates one relocates
    both — and a machine's real records never take a test's writes."""
    from app import enrollment

    return enrollment.ENROLLED.parent / "people.json"


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc).astimezone()


def _unreadable(path: Path, detail: object) -> Unreadable:
    now = time.monotonic()
    if now - _last_warned[0] >= _WARN_EVERY_S:
        _last_warned[0] = now
        logger.warning("could not read %s: %s — nobody is named and nothing is "
                       "written to it until it is fixed", path, detail)
    return Unreadable("อ่านไฟล์ความยินยอม (data/faces/people.json) ไม่ได้ — "
                      "ไม่เขียนทับ และระหว่างนี้หุ่นจะไม่เรียกชื่อใคร "
                      "ให้ผู้ดูแลแก้ไฟล์หรือกู้จากสำเนาก่อน")


def load() -> dict[str, dict]:
    """Every record, by name. `{}` only when there is no file.

    Raises `Unreadable` for a file that is there and cannot be used: not
    UTF-8, not JSON, not an object of objects, or a read Windows still
    refuses after `_READ_ATTEMPTS`. A BOM is not a reason — Notepad's
    "UTF-8 with BOM" is a plausible way for this file to be saved once by
    hand, and the BOM is not part of anybody's record.
    """
    path = people_path()
    for attempt in range(_READ_ATTEMPTS):
        try:
            raw = path.read_bytes()
            break
        except FileNotFoundError:
            return {}
        except PermissionError as exc:
            if attempt == _READ_ATTEMPTS - 1:
                raise _unreadable(path, exc) from None
            time.sleep(_READ_PAUSE_S)
        except OSError as exc:
            raise _unreadable(path, exc) from None
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except ValueError as exc:                   # UnicodeDecodeError is one too
        raise _unreadable(path, exc) from None
    if not isinstance(data, dict) or not all(isinstance(e, dict) for e in data.values()):
        # A record that is not an object is a damaged file, not a person
        # without a record: `status` would read it as MISSING (tolerated),
        # and `revoke` would crash on it.
        raise _unreadable(path, "not an object of records")
    return data


def save(people: dict[str, dict]) -> None:
    """Replace the file in one step.

    `status()` reads this file at decision time on every frame the watcher
    judges, and enrolment is now something the running server does, not
    only an offline station. An in-place write leaves a window where that
    read gets half a JSON document — which `load()` now refuses, and
    `status()` answers as UNREADABLE, but for that frame nobody could be
    named. Same bytes as the old `write_text` (UTF-8, the platform's
    newline), only the swap is new.

    Callers that change one record go through `record`/`revoke`, which
    hold the cross-process lock around load-modify-save; this only writes.
    """
    from app.faces import write_atomically

    text = json.dumps(people, ensure_ascii=False, indent=2) + "\n"
    data = text.replace("\n", os.linesep).encode("utf-8")
    write_atomically(people_path(), lambda fh: fh.write(data))


def _locked():
    """The cross-process half of the lock around load-modify-save.

    `_write_lock` only orders one process, and this file has writers in
    three: the server (the call screen's scan), the enrolment station and
    `revoke_face.py`. The review
    (2026-09-29) forced a revoke between the server's load and its save and
    the revocation was gone — the revoked person named again. Waits
    `LOCK_WAIT_S`, then refuses (`faces.LockBusy`, a ValueError in Thai).
    """
    from app import faces

    return faces.file_lock(people_path(), LOCK_WAIT_S, _BUSY)


def record(name: str, by: str | None = None, version: str = VERSION,
           days: float | None = None, now: datetime | None = None) -> dict:
    """Write (or renew) `name`'s consent. Returns the record.

    Renewing clears an earlier revocation on purpose: somebody who revoked
    and later enrolled themselves again has consented again.

    Raises `Unreadable` (a ValueError) rather than write over a file it
    cannot read: `{}` plus this one entry was every other record and every
    revocation deleted in one save. `faces.LockBusy` when another process
    holds the file too long.
    """
    from app.config import settings

    when = _now(now)
    days = settings.face_consent_days if days is None else days
    entry = {
        "consent_at": when.isoformat(timespec="seconds"),
        "consent_by": by or name,
        "consent_version": version,
        "expires_at": (when + timedelta(days=days)).isoformat(timespec="seconds"),
        "revoked_at": None,
    }
    with _write_lock, _locked():
        people = load()
        people[name] = entry
        save(people)
    logger.info("consent recorded for %s (until %s)", name, entry["expires_at"])
    return entry


def revoke(name: str, now: datetime | None = None) -> bool:
    """Mark `name` as withdrawn. Returns False if there was no record —
    which is still a revocation in effect: see `status`.

    Refuses on an unreadable file exactly like `record`: rewriting it from
    `{}` would drop every *other* revocation to add this one."""
    with _write_lock, _locked():
        people = load()
        entry = people.get(name) or {}
        had = bool(entry)
        entry["revoked_at"] = _now(now).isoformat(timespec="seconds")
        people[name] = entry
        save(people)
    logger.info("consent revoked for %s", name)
    return had


def status(name: str, now: datetime | None = None, people: dict | None = None) -> str:
    """OK, MISSING, REVOKED, EXPIRED — or UNREADABLE — for `name`, as of `now`.

    UNREADABLE instead of an exception because the watcher asks on every
    frame and must keep greeting; instead of MISSING because MISSING is
    tolerated by default, and the unreadable file may be exactly where
    somebody's revocation is. Only when this reads the file itself: a
    caller that passes `people` did its own `load()` and met the error
    there.
    """
    if people is None:
        try:
            people = load()
        except Unreadable:
            return UNREADABLE
    entry = people.get(name)
    if not entry:
        return MISSING
    if entry.get("revoked_at"):
        return REVOKED
    expires = entry.get("expires_at")
    if expires:
        try:
            if datetime.fromisoformat(expires) <= _now(now):
                return EXPIRED
        except ValueError:
            return EXPIRED                     # an unreadable expiry is not "forever"
    if not entry.get("consent_at"):
        return MISSING
    return OK


def may_name(name: str, now: datetime | None = None, people: dict | None = None) -> tuple[bool, str]:
    """(allowed, status). The one rule the watcher asks.

    MISSING is allowed unless FACE_REQUIRE_CONSENT is on. REVOKED, EXPIRED
    and UNREADABLE are never allowed — the last fails closed: while the
    file cannot be read nobody is named, rather than everybody (revoked
    people included) being read as merely unrecorded.
    """
    from app.config import settings

    state = status(name, now=now, people=people)
    if state == OK:
        return True, state
    if state == MISSING and not settings.face_require_consent:
        return True, state
    return False, state
