"""The places table: where the robot can be sent, and which leg of which route.

docs/emma-body-nav-design-2026-10-08.md §2f. One tracked JSON file
(`data/robot-places/floor2.json`) read on two machines: the server (matching a
spoken request to a place, names for the UI) and the robot's launcher
(`scripts/emma_nav.py`, which turns `here -> place` into `--from-step/--to-step`
of the one verified route).

**Stdlib only at import.** `emma_nav.py` imports this on the robot under a bare
Python with no pythainlp; `match()` is server-side and imports the tokenizer
inside itself. Moving that import to the top breaks the first walk.

**Never "repaired".** `load` raises `ValueError` naming the first bad field,
and `validate` does the same against the route's step list. A places file that
is wrong in one field is wrong — the launcher refuses everything
(`places_stale`) rather than walk a leg built on a guess, the same rule as the
unit-plan builder skipping a sheet whose header names another room.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.robot_contracts import ID_RE

_ID = re.compile(ID_RE)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

#: Steps a leg may end on: a measured, stationary pose (design §2g). A leg
#: that ended on a `drive` would hand the next leg a pose nobody measured.
LEG_END_STEPS = ("checkpoint", "dock")

DEFAULT_ARRIVE_TOLERANCE_M = 0.25
DEFAULT_ARRIVE_TOLERANCE_DEG = 15.0


@dataclass(frozen=True)
class Place:
    id: str
    name_th: str
    name_en: str
    kind: str
    aliases_th: tuple[str, ...] = ()
    aliases_en: tuple[str, ...] = ()
    arrive_tolerance_m: float = DEFAULT_ARRIVE_TOLERANCE_M
    arrive_tolerance_deg: float = DEFAULT_ARRIVE_TOLERANCE_DEG

    def names(self) -> tuple[str, ...]:
        """Every name a request may use for this place."""
        return (self.name_th, self.name_en, *self.aliases_th, *self.aliases_en)


@dataclass(frozen=True)
class Leg:
    frm: str
    to: str
    from_step: int
    to_step: int
    min_battery: int
    verified: bool
    est_s: float | None = None
    doors: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class Places:
    format: int
    route: str
    route_sha256: str
    places: tuple[Place, ...]
    legs: tuple[Leg, ...] = field(default=())

    def place(self, place_id: str) -> Place | None:
        return next((p for p in self.places if p.id == place_id), None)


def _bad(where: str, why: str) -> ValueError:
    return ValueError("%s: %s" % (where, why))


def _field(where: str, key: str) -> str:
    return "%s.%s" % (where, key) if where else key


def _str(obj: dict, key: str, where: str, *, required: bool = True) -> str:
    value = obj.get(key, None if required else "")
    if not isinstance(value, str) or (required and not value.strip()):
        raise _bad(_field(where, key), "must be a non-empty string"
                   if required else "must be a string")
    return value


def _strs(obj: dict, key: str, where: str) -> tuple[str, ...]:
    value = obj.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise _bad(_field(where, key), "must be a list of non-empty strings")
    return tuple(value)


def _int(obj: dict, key: str, where: str, lo: int, hi: int | None = None) -> int:
    value = obj.get(key)
    # bool is an int in Python; `true` as a step number is a typo, not step 1.
    if not isinstance(value, int) or isinstance(value, bool) or value < lo or (
            hi is not None and value > hi):
        raise _bad(_field(where, key), "must be an integer %s" % (
            ">= %d" % lo if hi is None else "in %d..%d" % (lo, hi)))
    return value


def _positive(obj: dict, key: str, where: str, default: float | None) -> float | None:
    if key not in obj:
        return default
    value = obj[key]
    if value is None and default is None:
        return None
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value) or value <= 0):
        raise _bad(_field(where, key), "must be a positive number")
    return float(value)


def parse(data: object) -> Places:
    """Build `Places` from decoded JSON, or raise ValueError naming the field."""
    if not isinstance(data, dict):
        raise _bad("places file", "must be a JSON object")
    if data.get("format") != 1 or isinstance(data.get("format"), bool):
        raise _bad("format", "must be 1")
    route = _str(data, "route", "")
    sha = data.get("route_sha256")
    if not isinstance(sha, str) or not _SHA256.match(sha):
        raise _bad("route_sha256", "must be 64 lower-case hex characters")
    raw_places = data.get("places")
    if not isinstance(raw_places, list) or not raw_places:
        raise _bad("places", "must be a non-empty list")
    places: list[Place] = []
    for i, raw in enumerate(raw_places):
        where = "places[%d]" % i
        if not isinstance(raw, dict):
            raise _bad(where, "must be an object")
        pid = raw.get("id")
        if not isinstance(pid, str) or not _ID.fullmatch(pid):
            raise _bad(where + ".id", "must match %s" % ID_RE)
        if any(p.id == pid for p in places):
            raise _bad(where + ".id", "duplicate id %r" % pid)
        kind = raw.get("kind")
        if not isinstance(kind, str) or not _ID.fullmatch(kind):
            raise _bad(where + ".kind", "must match %s" % ID_RE)
        places.append(Place(
            id=pid, name_th=_str(raw, "name_th", where), name_en=_str(raw, "name_en", where),
            kind=kind, aliases_th=_strs(raw, "aliases_th", where),
            aliases_en=_strs(raw, "aliases_en", where),
            arrive_tolerance_m=_positive(raw, "arrive_tolerance_m", where,
                                         DEFAULT_ARRIVE_TOLERANCE_M),
            arrive_tolerance_deg=_positive(raw, "arrive_tolerance_deg", where,
                                           DEFAULT_ARRIVE_TOLERANCE_DEG),
        ))
    ids = {p.id for p in places}
    raw_legs = data.get("legs", [])
    if not isinstance(raw_legs, list):
        raise _bad("legs", "must be a list")
    legs: list[Leg] = []
    for i, raw in enumerate(raw_legs):
        where = "legs[%d]" % i
        if not isinstance(raw, dict):
            raise _bad(where, "must be an object")
        frm, to = raw.get("from"), raw.get("to")
        if frm not in ids:
            raise _bad(where + ".from", "unknown place %r" % (frm,))
        if to not in ids:
            raise _bad(where + ".to", "unknown place %r" % (to,))
        if frm == to:
            raise _bad(where, "from and to are the same place")
        if any(leg.frm == frm and leg.to == to for leg in legs):
            raise _bad(where, "duplicate leg %s -> %s" % (frm, to))
        from_step = _int(raw, "from_step", where, 1)
        to_step = _int(raw, "to_step", where, 1)
        if from_step > to_step:
            raise _bad(where, "from_step %d is after to_step %d" % (from_step, to_step))
        verified = raw.get("verified")
        if not isinstance(verified, bool):
            raise _bad(where + ".verified", "must be true or false")
        legs.append(Leg(
            frm=frm, to=to, from_step=from_step, to_step=to_step,
            min_battery=_int(raw, "min_battery", where, 0, 100), verified=verified,
            # Null until filled from measured run summaries: no number is
            # invented here (design §2f).
            est_s=_positive(raw, "est_s", where, None),
            doors=_strs(raw, "doors", where), note=_str(raw, "note", where, required=False),
        ))
    return Places(format=1, route=route, route_sha256=sha,
                  places=tuple(places), legs=tuple(legs))


def load(path: str | Path) -> Places:
    """Read and parse a places file. Raises ValueError (naming the bad field)
    for any content problem, OSError for a missing file."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _bad("places file", "is not JSON (%s)" % exc) from exc
    return parse(data)


def validate(places: Places, route_steps: list) -> None:
    """Check the legs against the route's step list; raise ValueError if not.

    Steps are 1-based, as drive_route numbers them. Every leg must end on a
    checkpoint or dock step inside the route. Legs may not overlap, with one
    exception: two legs may share **exactly one** boundary step when that
    step is a checkpoint that ends one leg and starts the other — leg 2 of the
    round trip re-runs step 10 (a stationary measurement) so its D11 pass
    starts from a measured pose (build review #2).
    """
    if not isinstance(route_steps, list) or not route_steps:
        raise _bad("route", "has no steps")
    n = len(route_steps)

    def do(step: int) -> str | None:
        raw = route_steps[step - 1]
        return raw.get("do") if isinstance(raw, dict) else None

    for i, leg in enumerate(places.legs):
        where = "legs[%d]" % i
        if leg.to_step > n:
            raise _bad(where + ".to_step", "%d is past the route's %d steps" % (leg.to_step, n))
        if do(leg.to_step) not in LEG_END_STEPS:
            raise _bad(where + ".to_step", "step %d is %r, not a checkpoint or dock"
                       % (leg.to_step, do(leg.to_step)))
    legs = list(places.legs)
    for i, a in enumerate(legs):
        for j in range(i + 1, len(legs)):
            b = legs[j]
            lo, hi = max(a.from_step, b.from_step), min(a.to_step, b.to_step)
            if lo > hi:
                continue
            shared_boundary = (lo == hi and do(lo) == "checkpoint"
                               and ((a.to_step == lo == b.from_step)
                                    or (b.to_step == lo == a.from_step)))
            if not shared_boundary:
                raise _bad("legs[%d]/legs[%d]" % (i, j),
                           "overlap on steps %d..%d" % (lo, hi))


def leg(places: Places, frm: str, to: str) -> Leg | None:
    return next((l for l in places.legs if l.frm == frm and l.to == to), None)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def match(request: str, places: Places) -> Place | None:
    """The place a request names, or None.

    `robot_link.find_place`'s rule, for the same reason: **every word** of one
    of the place's names (name_th, name_en, or one alias) must appear in the
    request, as whole words from `retrieval.tokenize`. The most specific
    match wins (a name whose words are a strict superset of another match's);
    two places still tied is ambiguous and returns None — a wrong destination
    walks a guest across the building, no match costs one question.

    Thai without the real word segmenter would fall back to character
    n-grams, which is how `ราคา` matched `อาคาร`; then a Thai name never
    matches at all rather than matching wrongly.
    """
    from app.tools import retrieval

    wanted = set(retrieval.tokenize(request or ""))
    if not wanted:
        return None
    # Read, never re-initialised: `init_tokeniser()` rebuilds the trie and
    # would throw away the deck vocabulary the slide search taught it.
    # `tokenize` above has already initialised it if nobody had.
    segmenter = retrieval._tokeniser is not None

    def words(name: str) -> frozenset[str]:
        if not segmenter and any("\u0e00" <= ch <= "\u0e7f" for ch in name):
            return frozenset()
        return frozenset(retrieval.tokenize(name))

    best: dict[str, frozenset[str]] = {}
    for place in places.places:
        hits = [w for w in (words(n) for n in place.names()) if w and w <= wanted]
        if hits:
            best[place.id] = max(hits, key=len)
    if not best:
        return None
    maximal = [pid for pid, w in best.items()
               if not any(w < other for o, other in best.items() if o != pid)]
    return places.place(maximal[0]) if len(maximal) == 1 else None
