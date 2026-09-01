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
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("condo_voice.consent")

#: Bump when the policy text people agree to changes; old records keep the
#: version they were given, and a future check can decide what to do with
#: records from an older policy.
VERSION = "face-policy-v1"

OK, MISSING, REVOKED, EXPIRED = "ok", "missing", "revoked", "expired"


def people_path() -> Path:
    """Next to the enrolment store, so a test that relocates one relocates
    both — and a machine's real records never take a test's writes."""
    from app import enrollment

    return enrollment.ENROLLED.parent / "people.json"


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc).astimezone()


def load() -> dict[str, dict]:
    path = people_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("could not read %s: %s — treating everybody as unrecorded", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def save(people: dict[str, dict]) -> None:
    path = people_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(people, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def record(name: str, by: str | None = None, version: str = VERSION,
           days: float | None = None, now: datetime | None = None) -> dict:
    """Write (or renew) `name`'s consent. Returns the record.

    Renewing clears an earlier revocation on purpose: somebody who revoked
    and later enrolled themselves again has consented again.
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
    people = load()
    people[name] = entry
    save(people)
    logger.info("consent recorded for %s (until %s)", name, entry["expires_at"])
    return entry


def revoke(name: str, now: datetime | None = None) -> bool:
    """Mark `name` as withdrawn. Returns False if there was no record —
    which is still a revocation in effect: see `status`."""
    people = load()
    entry = people.get(name) or {}
    had = bool(entry)
    entry["revoked_at"] = _now(now).isoformat(timespec="seconds")
    people[name] = entry
    save(people)
    logger.info("consent revoked for %s", name)
    return had


def status(name: str, now: datetime | None = None, people: dict | None = None) -> str:
    """OK, MISSING, REVOKED or EXPIRED for `name`, as of `now`."""
    entry = (load() if people is None else people).get(name)
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

    MISSING is allowed unless FACE_REQUIRE_CONSENT is on. REVOKED and
    EXPIRED are never allowed.
    """
    from app.config import settings

    state = status(name, now=now, people=people)
    if state == OK:
        return True, state
    if state == MISSING and not settings.face_require_consent:
        return True, state
    return False, state
