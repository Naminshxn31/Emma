"""Robot-face navigation by voice: the existing robot tools, routed to the launcher.

docs/emma-body-nav-design-2026-10-08.md §2d (P2). When
`ROBOT_NAV_ROUTES_ENABLED=true` **and** the conversation is the robot face's
own session, `go_to_place` / `return_to_base` / `stop_moving` /
`get_robot_status` stop talking to the board's planner and talk to the face
page instead, which hands one place id to the app, which runs one *leg* of the
one verified route through `scripts/emma_nav.py`. One navigation system per
machine: the planner primitives refuse while routes own the base
(`robot_chassis` fence, §4).

The tool names, descriptions and parameters do not change — the sales room's
declarations stay byte-identical — so everything here is reached from inside
the handlers in `app/tools/robot.py`.

What this module decides, and why each rule exists:

* **Voice may not start a walk by default** (`ROBOT_NAV_VOICE=off`, safety
  review #6). The robot face talks to the public and "ไป" is in almost every
  sentence. `unlocked` = only inside a staff PIN unlock on this robot face
  (the same TTL); `on` is the owner's explicit choice. Stopping is never
  gated: voice can always stop a walk.
* **The unlock belongs to the face's own socket** (finding #23). It was
  keyed by client IP, and under adb reverse (the documented setup) the
  face's peer is 127.0.0.1 — so is every browser on the server PC, and
  `/robot-nav/unlock` accepts loopback without a token: a PIN typed at the
  desk enabled voice walks on the robot for 120 s. A `nav_unlock` frame on
  the face's /ws (session.py) unlocks that session only; an HTTP unlock
  still counts for a face on a LAN address, never for a loopback one.
* **The place is matched by whole words** (`robot_places.match`, the
  `find_place` rule). A wrong destination walks a guest across the building;
  no match costs one question.
* **Unverified legs are tap-only.** The app (`NavLauncher.goRefusal`) and
  the launcher (`emma_nav.plan_leg`) refuse a voice `go` on a leg with
  `verified:false` whatever the server says, and read no switch.
  ROBOT_NAV_VOICE_UNVERIFIED only skips the server's own early refusal
  here (review 2, finding #16): on a real robot it moves the same
  `leg_unverified` from the server to the robot, nothing more. It stays
  for scripts/eval_live_qa.py, whose scripted robot acks every go, so the
  model's side of a walk can be measured on the unverified floor-2 legs.
* **The heard guard names the place.** A fresh (after the latest
  activity_start, ≤ ROBOT_HEARD_MAX_AGE_S), unused caption of this session
  must itself match the same place. A motion word alone never counts:
  "ไปไหนมา" must not send the robot anywhere, and "กลับบ้าน" is a guest
  saying goodbye, not a request for the dock.
* **The server never chooses a run id.** The app generates it and returns it
  in the nav_ack; only a run id acked to *this* session's own `go`
  (`robot_face_link.issued_run`) is ever announced, so a second page that
  claims the user agent cannot fake an arrival into this conversation.
* **A gap in status is said once, and no stop is sent.** The run's own
  tether and the native red bar are the stops; a server that guessed a stop
  from silence would be a second, worse stop path.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from app import robot_contracts as rc

logger = logging.getLogger("condo_voice.robot_nav")

#: Always appended: 9.2% of 8 Oct eval turns came back in English to a Thai
#: speaker, mostly refusals (CLAUDE.md, docs/emma-live-qa-eval-2026-10-08.md).
SAME_LANGUAGE = "ตอบเป็นภาษาเดียวกับที่ผู้ใช้พูด"

#: Wrong PINs per client before the lockout, and how long it lasts. Five
#: minutes, not face_scan's one: the design's UI text is "ใส่ผิดหลายครั้ง
#: รอ 5 นาที" (§3), and this PIN unlocks motion rather than a camera.
PIN_TRIES = 5
LOCKOUT_S = 300.0
#: How long one PIN unlock lasts (§3: `{ok, ttl_s:120}`). Also the window in
#: which ROBOT_NAV_VOICE=unlocked lets voice start a walk on that face.
UNLOCK_TTL_S = 120.0
#: face_scan's bound, for face_scan's reason: a table keyed by client address
#: that only grows is a fresh five tries per spoofed address.
MAX_FAILURE_KEYS = 64

#: Words that, with กลับ, ask for the dock even when no alias is said
#: ("กลับฐาน"). Not บ้าน: "กลับบ้าน" is a guest leaving (design §2d step 3).
_DOCK_RETURN_TH = ("แท่น", "ฐาน")

_clock = time.monotonic


class PinError(Exception):
    """A refused PIN unlock: the Thai sentence and its HTTP status."""

    def __init__(self, reason: str, status: int):
        super().__init__(reason)
        self.reason = reason
        self.status = status


@dataclass
class _Run:
    """A walk this server started by voice on one session."""

    run_id: str
    place: str
    name_th: str
    last_seen: float
    announced: bool = False
    stale_announced: bool = False


@dataclass
class _SessionNav:
    session: object
    runs: dict[str, _Run] = field(default_factory=dict)
    #: run ids whose terminal state was already announced (any path).
    announced: set[str] = field(default_factory=set)
    #: (run_id, state) pairs already written to the turn log, oldest first,
    #: at most LOGGED_KEPT (`_first_log`). The status link is a session that
    #: lives all day and a page can send any valid run id, so a plain set
    #: grew without bound (review 10 Oct).
    logged: dict[tuple[str, str], None] = field(default_factory=dict)
    watch: asyncio.Task | None = None


#: id(session) -> _SessionNav. Keyed by id with the object kept for an
#: identity check, like robot_face_link and heard: test doubles are bare
#: objects that cannot be weakly referenced.
_STATE: dict[int, _SessionNav] = {}
#: client host -> [wrong PINs so far, locked until, last wrong PIN].
_failures: dict[str, list[float]] = {}
#: client host -> monotonic end of its unlock (HTTP; never trusted for a
#: loopback face — see `unlocked`).
_unlocks: dict[str, float] = {}
#: id(session) -> (session, monotonic end) for a PIN typed over that face's
#: own socket (finding #23).
_session_unlocks: dict[int, tuple[object, float]] = {}
#: Strong references, for the reason robot_face_link keeps them: an
#: unreferenced task can be collected mid-flight, and a dropped "ถึงแล้ว" is
#: a silent failure.
_TASKS: set[asyncio.Task] = set()


def _now() -> float:
    return _clock()


def _nav(session, create: bool = False) -> _SessionNav | None:
    entry = _STATE.get(id(session))
    if entry is not None and entry.session is session:
        return entry
    if not create:
        return None
    entry = _SessionNav(session)
    _STATE[id(session)] = entry
    return entry


#: How many (run_id, state) pairs a session remembers as logged. The newest
#: is never the one dropped, and the status link's 10 s beat re-sends only
#: the latest run's terminal state — so a real run is logged once.
LOGGED_KEPT = 64


def _first_log(nav: _SessionNav, key: tuple[str, str]) -> bool:
    """True the first time `key` is seen on this session (then remembered)."""
    if key in nav.logged:
        return False
    nav.logged[key] = None
    while len(nav.logged) > LOGGED_KEPT:
        del nav.logged[next(iter(nav.logged))]
    return True


def _record(kind: str, **fields: Any) -> None:
    try:
        from app import turnlog

        turnlog.record(kind, **fields)
    except Exception:
        logger.debug("turnlog failed for %s", kind, exc_info=True)


# ---- who may route, and the places table ----


def routes_session():
    """The robot face session this call belongs to when routes mode is on,
    else None (and the caller takes today's path, byte for byte)."""
    from app.config import settings
    from app.tools.registry import robot_face_session

    if not settings.robot_nav_routes_enabled:
        return None
    return robot_face_session.get()


def load_places():
    """The server's copy of the places table, or None when it is not set up
    or not valid. Never repaired: a table wrong in one field is wrong."""
    from app import robot_places
    from app.config import settings

    path = (settings.robot_nav_places_file or "").strip()
    if not path:
        return None
    from pathlib import Path

    p = Path(path)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent / p
    try:
        return robot_places.load(p)
    except (OSError, ValueError) as exc:
        logger.warning("places file %s unusable: %s", p, exc)
        return None


def _dock(places):
    return next((p for p in places.places if p.kind == "dock"), None) or places.place("dock")


# ---- the PIN unlock (/robot-nav/unlock), shared with ROBOT_NAV_VOICE=unlocked ----


def _pin_ok(pin) -> bool:
    from app.config import settings

    expected = (settings.robot_nav_pin or "").strip()
    if not isinstance(pin, str) or not expected:
        return False
    # Encoded before compare_digest, which refuses non-ASCII str: a
    # Thai-keyboard slip would otherwise be a TypeError and a 500.
    return secrets.compare_digest(pin.strip().encode("utf-8"), expected.encode("utf-8"))


def _forget_old_failures(now: float) -> None:
    for key, (count, locked_until, last) in list(_failures.items()):
        if (now >= locked_until) if locked_until else (now - last >= LOCKOUT_S):
            del _failures[key]
    for key, until in list(_unlocks.items()):
        if now >= until:
            del _unlocks[key]
    for key, (_session, until) in list(_session_unlocks.items()):
        if now >= until:
            del _session_unlocks[key]


def unlock(pin, client: str, *, session=None) -> dict:
    """Check the staff PIN for `client` (the TCP peer). Raises PinError.

    With `session` (the robot face's own socket, finding #23) the unlock is
    that session's; without, it is the HTTP endpoint's, keyed by `client`.
    The lockout is keyed by `client` either way.

    The PIN is a customer deterrent, not a security boundary (§3): the
    boundary is WS_TOKEN, the trusted-origin bridge and the robot's own
    nav_enabled. It still gets the face-scan lockout, because a four-digit
    PIN without one is a PIN anybody at the screen can walk through.
    """
    from app.config import settings

    if settings.nav_panel_mode() == "off":
        raise PinError("ฟังก์ชันนำทางปิดอยู่", 403)
    if not (settings.robot_nav_pin or "").strip():
        raise PinError("ยังไม่ได้ตั้งรหัส ROBOT_NAV_PIN — ให้เจ้าหน้าที่ตั้งค่าก่อน", 503)
    now = _now()
    _forget_old_failures(now)
    entry = _failures.get(client)
    if entry is None and len(_failures) >= MAX_FAILURE_KEYS:
        raise PinError("ใส่ผิดหลายครั้ง รอ 5 นาที", 429)
    count, locked_until = (int(entry[0]), entry[1]) if entry else (0, 0.0)
    if locked_until:
        # Refused without comparing: a lockout that still checks the PIN is
        # a lockout that still answers the guess.
        raise PinError("ใส่ผิดหลายครั้ง รอ 5 นาที", 429)
    if _pin_ok(pin):
        _failures.pop(client, None)
        if session is not None:
            _session_unlocks[id(session)] = (session, now + UNLOCK_TTL_S)
        else:
            _unlocks[client] = now + UNLOCK_TTL_S
        _record("robot_nav_unlock", ok=True, via="face" if session is not None else "http")
        return {"ok": True, "ttl_s": int(UNLOCK_TTL_S)}
    count += 1
    _record("robot_nav_unlock", ok=False)
    if count >= PIN_TRIES:
        _failures[client] = [0, now + LOCKOUT_S, now]
        logger.warning("robot nav: %d wrong PINs from %s — locked for %.0fs",
                       PIN_TRIES, client or "?", LOCKOUT_S)
        raise PinError("ใส่ผิดหลายครั้ง รอ 5 นาที", 429)
    _failures[client] = [count, 0.0, now]
    raise PinError("รหัสไม่ถูกต้อง", 403)


def _client_host(session) -> str | None:
    ws = getattr(session, "ws", None)
    client = getattr(ws, "client", None)
    host = getattr(client, "host", None)
    return host if isinstance(host, str) and host else None


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        import ipaddress

        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return address.is_loopback or (mapped is not None and mapped.is_loopback)


def unlocked(session) -> bool:
    """Is this robot face inside a PIN unlock?

    An unlock sent over this session's own socket always counts. An HTTP
    unlock counts only when the face's address is a LAN one, where the
    HTTP peer and the socket peer are the same machine. Never for a
    loopback face (finding #23): under adb reverse 127.0.0.1 is the robot
    AND every browser on the server PC. A face whose address cannot be
    read is never unlocked by HTTP."""
    entry = _session_unlocks.get(id(session))
    if entry is not None and entry[0] is session and _now() < entry[1]:
        return True
    host = _client_host(session)
    if host is None or _is_loopback(host):
        return False
    until = _unlocks.get(host)
    return until is not None and _now() < until


def voice_may_start(session) -> tuple[bool, str | None]:
    from app.config import settings

    mode = settings.nav_voice_mode()
    if mode == "on":
        return True, None
    if mode == "unlocked" and unlocked(session):
        return True, None
    return False, "voice_disabled"


# ---- results ----


def _with_language(text: str) -> str:
    return "%s %s" % (text, SAME_LANGUAGE)


def _instruction(reason: str, name: str | None, *, stop_sent: bool = False,
                 not_a_request: bool = False) -> str:
    th = rc.nav_reason_th(reason)
    if reason == "voice_disabled":
        return ("ปิดการสั่งเดินด้วยเสียงไว้ ห้ามบอกว่ากำลังพาไปหรือกำลังเดิน "
                "ให้บอกตรงๆ ว่าเจ้าหน้าที่เป็นคนสั่งหุ่นเดินจากหน้าจอหุ่น")
    if reason == "heard_missing" and not_a_request:
        # Live eval 9 Oct (R-heard-where-room): "ห้องประชุมใหญ่อยู่ไหน" got
        # the generic text below and Emma asked where to take them. Quoted,
        # because only the quoted sentence stops the model saying it.
        return ("ลูกค้าไม่ได้ขอให้พาไป แค่ถามทางหรือบอกว่าไม่ไป ห้ามสั่งเดินเอง "
                "ห้ามพูดว่า 'ไม่แน่ใจว่าต้องการให้พาไปที่ไหนนะคะ' หรือ 'กำลังพาไปค่ะ' "
                "ให้ตอบเรื่องที่เขาถามหรือรับคำเป็นคำพูด จะเสนอพาไปก็ได้")
    if reason == "heard_missing":
        # Live eval 9 Oct (R-heard-going-home): "กลับบ้านก่อนนะ" is a
        # goodbye, and Emma answered 'ไม่แน่ใจว่าต้องการให้กลับที่จุดไหน'.
        return ("ไม่ได้ยินคำขอให้พาไปที่ชัดเจน ห้ามสั่งเดินเอง ห้ามพูดว่า 'กำลังพาไปค่ะ' "
                "ถ้าลูกค้าบอกลาหรือพูดเรื่องอื่น ให้ตอบตามที่เขาพูด ห้ามพูดว่า "
                "'ไม่แน่ใจว่าต้องการให้กลับที่จุดไหน' ถ้าเขาขอให้พาไปจริงแต่ไม่ได้ยินชื่อจุด "
                "ให้ถามสั้นๆ ว่าต้องการไปที่ไหน")
    if reason == "timeout" and stop_sent:
        # Finding #5: the page relays the go whether or not the ack made it
        # back, so "สั่งเดินไม่ได้" could be said over a robot leaving. The
        # link has sent a nav stop after it (robot_face_link._after_timeout).
        return ("หุ่นไม่ตอบรับคำสั่ง ยังไม่รู้ว่าออกเดินหรือไม่ ส่งคำสั่งหยุดตามไปแล้ว ห้ามสั่งซ้ำเอง "
                "ห้ามบอกว่ากำลังเดิน ให้บอกว่าหุ่นไม่ตอบรับ ถ้าหุ่นยังขยับให้กดปุ่มหยุดฉุกเฉิน (E-stop) บนตัวหุ่น")
    if reason in ("timeout", "disconnected", "request_pending"):
        # Not "สั่งเดินไม่ได้" either (finding #5): whether it left is unknown.
        return ("หุ่นไม่ตอบรับคำสั่ง ยังไม่รู้ว่าออกเดินหรือไม่ ห้ามสั่งซ้ำเอง "
                "ห้ามบอกว่ากำลังเดิน ให้บอกว่าหุ่นไม่ตอบรับ ถ้าหุ่นขยับให้กดปุ่มหยุดฉุกเฉิน (E-stop) บนตัวหุ่นทันที")
    where = "ไป%s" % name if name else "เดิน"
    return ("ยังสั่งหุ่น%sไม่ได้: %s ห้ามบอกว่ากำลังเดินหรือถึงแล้ว ให้บอกตรงๆ "
            "แล้วเสนอเรียกเจ้าหน้าที่" % (where, th))


def _refused(reason: str, *, place=None, name=None, hardware: str = "refused",
             extra: dict | None = None, stop_sent: bool = False,
             not_a_request: bool = False) -> dict:
    _record("robot_nav_refused", reason=reason, place=place)
    out = {"ok": False, "hardware": hardware, "place": place, "reason": reason,
           "reason_th": rc.nav_reason_th(reason),
           "instruction": _with_language(_instruction(reason, name, stop_sent=stop_sent,
                                                      not_a_request=not_a_request))}
    if extra:
        out.update(extra)
    return out


def _place_names(places) -> list[str]:
    return [p.name_th for p in places.places]


# ---- go_to_place / return_to_base ----


def _heard_place(session, target, places, *, dock: bool) -> tuple[bool, str | None]:
    """The place-name heard guard, fresh and single-use (design §2d step 3).

    (True, None) only when a caption of this session, recorded after its
    latest activity_start and young enough, names `target` itself — and
    spends that caption. A refusal spends nothing; its second element says
    why when the caption was not a request at all ("not_a_request").
    """
    from app import heard, robot_places

    caption = heard.caption_for(session)
    if caption is None:
        return False, None
    # Review #18: "อย่าไปห้องประชุมใหญ่" and "ห้องประชุมใหญ่อยู่ไหน" name the
    # place too. A sentence that refuses the walk or only asks the way is
    # not a request, whatever it names -- checked before the match and
    # before the dock branch, and it spends nothing (the guest may still
    # say "พาไป..." next).
    if heard.declines_or_asks_where(caption.text):
        return False, "not_a_request"
    named = robot_places.match(caption.text, places)
    ok = named is not None and named.id == target.id
    if not ok and dock:
        ok = (heard.has_words(caption.text, ("กลับ",))
              and heard.has_words(caption.text, _DOCK_RETURN_TH))
    return (ok and heard.consume(session, caption)), None


def _leg_check(session, places, target) -> str | None:
    """Server-side leg refusal for voice, or None. "From" is the robot's
    last reported `here`; when that is unknown the app decides — except
    that a place no verified leg reaches can be refused here already."""
    from app import robot_face_link, robot_places
    from app.config import settings

    # Not caps.here alone: right after a walk the caps can still name the
    # place it left from, and "กลับแท่นชาร์จ" in big_room was answered
    # already_there (review 2, finding #5). A newer nav_status decides.
    here = robot_face_link.here(session)
    # Server-side only (finding #16): the robot refuses unverified voice legs
    # whatever this says. See the module docstring.
    allow_unverified = settings.robot_nav_voice_unverified
    if rc.valid_id(here):
        if here == target.id:
            return "already_there"
        leg = robot_places.leg(places, here, target.id)
        if leg is None:
            return "no_leg"
        if not leg.verified and not allow_unverified:
            return "leg_unverified"
        return None
    inbound = [l for l in places.legs if l.to == target.id]
    if not inbound:
        return "no_leg"
    if not allow_unverified and not any(l.verified for l in inbound):
        return "leg_unverified"
    return None


async def go(session, request: str | None, *, dock: bool = False) -> dict:
    """`go_to_place(place)` (or `return_to_base()` with dock=True) on the
    robot face in routes mode. Returns within ROBOT_NAV_ACK_TIMEOUT_S."""
    from app import robot_face_link, robot_places
    from app.config import settings

    # 0. The voice key, before anything else is even looked at.
    may, why = voice_may_start(session)
    if not may:
        return _refused(why)
    places = load_places()
    if places is None:
        return _refused("places_stale", hardware="failed")
    # 1. Which place, by whole words.
    if dock:
        target = _dock(places)
    else:
        target = robot_places.match(request or "", places) if isinstance(request, str) else None
    if target is None:
        return _refused("unknown_place", extra={
            "known_places": _place_names(places),
            "instruction": _with_language(
                "ไม่รู้จักจุดนี้ ห้ามเดาและห้ามพาไป จุดที่ไปได้คือ %s "
                "ให้ถามว่าหมายถึงจุดไหน" % ", ".join(_place_names(places)))})
    # 2. Legs not yet verified are tap-only.
    leg_reason = _leg_check(session, places, target)
    if leg_reason is not None:
        return _refused(leg_reason, place=target.id, name=target.name_th)
    # 3. The caption must name this place itself.
    if settings.robot_nav_require_heard:
        heard_ok, why = _heard_place(session, target, places, dock=dock)
        if not heard_ok:
            # Same code either way (the contract table has no other), but a
            # guest who only asked the way or said no must not be asked
            # where to go (review 2, finding #27).
            return _refused("heard_missing", place=target.id, name=target.name_th,
                            not_a_request=why == "not_a_request",
                            extra={"heard": why} if why else None)
    # 4. One command, one place id; the app owns the leg and the run id.
    ack = await robot_face_link.request(
        session, "nav", {"action": "go", "place": target.id, "origin": "voice"},
        settings.robot_nav_ack_timeout_s)
    if not ack.get("accepted"):
        reason = ack.get("reason") or "bad_request"
        hardware = "failed" if reason in ("timeout", "disconnected", "request_pending",
                                          "not_robot_face", "bad_request") else "refused"
        return _refused(reason, place=target.id, name=target.name_th, hardware=hardware,
                        stop_sent=ack.get("stop_sent") is True)
    run_id = ack.get("run_id")
    if rc.valid_run_id(run_id):
        nav = _nav(session, create=True)
        nav.runs[run_id] = _Run(run_id=run_id, place=target.id, name_th=target.name_th,
                                last_seen=_now())
        _arm_watch(session, run_id)
    _record("robot_nav_go", place=target.id, accepted=True)
    return {
        "ok": True, "hardware": "requested", "place": target.id, "name": target.name_th,
        "run_id": run_id,
        "instruction": _with_language(
            "ส่งคำสั่งให้หุ่นไป%sแล้ว หุ่นจะเก็บแขนก่อนออกเดิน ยังไม่ได้ยืนยันว่าออกเดิน "
            "ห้ามบอกว่าถึงแล้ว ระหว่างเดินพูดสั้นๆ รอข้อความแจ้งว่าถึง" % target.name_th),
    }


# ---- stop_moving ----


async def stop(session) -> dict:
    """A nav stop AND a body stop, always, both at once.

    Sent even when nothing is believed to be moving (the existing rule), and
    never through the board: a DELETE of the current action mid-run reads to
    drive_route as a link drop, and it then *redoes the step* (§4 "STOP
    itself causing a retry"). `request` never answers a stop with
    `request_pending`, so a pending `go` cannot hold this back.

    Sent on `session` AND on every other live face socket at once (its call
    and its status link, `robot_face_link.request_stop`): the face page
    holds both, one that refuses, times out or dropped is not the end of a
    stop, and a quiet one must not hold the others back by its timeout.
    """
    from app import event_walk, robot_face_link
    from app.config import settings

    # the event host's own walk, if one is under way (a no-op otherwise)
    await event_walk.stop_from("voice")
    nav_ack, body_ack = await asyncio.gather(
        robot_face_link.request_stop(session, "nav", settings.robot_nav_ack_timeout_s),
        robot_face_link.request_stop(session, "body", settings.robot_body_ack_timeout_s))
    sent = bool(nav_ack.get("accepted"))
    _record("robot_nav_stop", nav_accepted=sent, body_accepted=bool(body_ack.get("accepted")),
            reason=nav_ack.get("reason"), via=nav_ack.get("via"))
    return {
        "ok": sent, "hardware": "requested" if sent else "failed",
        "nav_stop": {"accepted": sent, "reason": nav_ack.get("reason")},
        "body_stop": {"accepted": bool(body_ack.get("accepted")),
                      "reason": body_ack.get("reason")},
        "instruction": _with_language(
            # live eval 9 ต.ค.: the bare "ห้ามพูดว่า 'หยุดแล้ว'" let 'หยุดให้เรียบร้อยแล้วค่ะ' through
            "ส่งคำสั่งหยุดแล้ว แต่ยังยืนยันไม่ได้ว่าหุ่นหยุดจริง ให้ตอบว่า 'ส่งคำสั่งหยุดแล้วค่ะ' "
            "ห้ามพูดว่า 'หยุดแล้ว' 'หยุดให้เรียบร้อยแล้วค่ะ' หรือ 'เรียบร้อยค่ะ' "
            "ถ้ามีอันตรายให้บอกคนให้กดปุ่มหยุดฉุกเฉิน (E-stop) บนตัวหุ่น"
            if sent else
            "ส่งคำสั่งหยุดไม่สำเร็จ ห้ามบอกว่าหยุดแล้ว ให้บอกคนตรงนั้นให้กดปุ่มหยุดฉุกเฉิน (E-stop) บนตัวหุ่นทันที แล้วแจ้งเจ้าหน้าที่"),
    }


# ---- a stop from anywhere else (the desk STOP, stop_moving off the face) ----

#: Desk hints for a stop that could only reach the board. The board's
#: DELETE is what drive_route reads as a link drop: it renews and redoes
#: the step, so to a leg run it is a ~1 s pause, never a stop (finding #0).
STOP_HINT_NO_FACE = "หุ่นกำลังเดินเส้นทาง แต่หน้าหุ่นไม่ได้เชื่อมต่อ — กดหยุดบนหุ่น/ปุ่มฉุกเฉินทันที"
STOP_HINT_UNKNOWN = ("เซิร์ฟเวอร์มองไม่เห็นว่าหุ่นกำลังเดินเส้นทางอยู่หรือไม่ "
                     "(หน้าหุ่นไม่ได้เชื่อมต่อ หรือไม่ได้รายงานสถานะการเดิน) "
                     "ส่งได้แค่คำสั่งพักฐานล้อ ถ้าหุ่นยังเดิน — กดหยุดบนหุ่น/ปุ่มฉุกเฉินทันที")
#: The same two cases, for the model. Quoted, like `stop`: only the quoted
#: sentence keeps 'หยุดให้เรียบร้อยแล้วค่ะ' out of its mouth (live eval 9 ต.ค.).
STOP_SAY_NO_FACE = _with_language(
    "หุ่นกำลังเดินเส้นทาง แต่ติดต่อหน้าจอหุ่นไม่ได้ จึงหยุดการเดินจากที่นี่ไม่ได้ "
    "ห้ามพูดว่า 'หยุดแล้ว' 'ส่งคำสั่งหยุดแล้วค่ะ' หรือ 'เรียบร้อยค่ะ' "
    "ให้บอกคนตรงนั้นให้กดปุ่มหยุดฉุกเฉิน (E-stop) บนตัวหุ่นทันที แล้วแจ้งเจ้าหน้าที่")
STOP_SAY_UNKNOWN = _with_language(
    "ส่งคำสั่งพักฐานล้อแล้ว แต่ตรวจไม่ได้ว่าหุ่นกำลังเดินเส้นทางอยู่หรือไม่ "
    "ถ้ากำลังเดินอยู่ คำสั่งนี้หยุดไม่ได้ ห้ามพูดว่า 'หยุดแล้ว' 'หยุดให้เรียบร้อยแล้วค่ะ' "
    "หรือ 'เรียบร้อยค่ะ' ให้ตอบว่า 'ส่งคำสั่งหยุดแล้วค่ะ' แล้วบอกว่าถ้าหุ่นยังเดินอยู่ "
    "ให้กดปุ่มหยุดฉุกเฉิน (E-stop) บนตัวหุ่นทันที")
#: The unknown case when a connected (but blind) face ACCEPTED the run's
#: stop as well (`stop_plan` "unknown" with a face, review 10 Oct): the stop
#: did reach the app, yet nobody can vouch for what is running.
STOP_HINT_UNKNOWN_FACE = ("เซิร์ฟเวอร์มองไม่เห็นว่าหุ่นกำลังเดินเส้นทางอยู่หรือไม่ "
                          "ส่งคำสั่งหยุดผ่านหน้าหุ่นและพักฐานล้อแล้ว แต่ยืนยันไม่ได้ "
                          "ถ้าหุ่นยังเดิน — กดหยุดบนหุ่น/ปุ่มฉุกเฉินทันที")
STOP_SAY_UNKNOWN_FACE = _with_language(
    "ส่งคำสั่งหยุดผ่านหน้าจอหุ่นแล้ว แต่ตรวจไม่ได้ว่าหุ่นกำลังเดินเส้นทางอยู่หรือไม่ "
    "ห้ามพูดว่า 'หยุดแล้ว' 'หยุดให้เรียบร้อยแล้วค่ะ' หรือ 'เรียบร้อยค่ะ' "
    "ให้ตอบว่า 'ส่งคำสั่งหยุดแล้วค่ะ' แล้วบอกว่าถ้าหุ่นยังเดินอยู่ "
    "ให้กดปุ่มหยุดฉุกเฉิน (E-stop) บนตัวหุ่นทันที")


def stop_plan():
    """Which stop a caller that is not the walking face's own routes session
    must send: ("face", face) | ("face_gone", None) | ("unknown", None) |
    ("board", None). Shared by the desk STOP (/robot/command) and voice
    `stop_moving`, so the two cannot drift apart again.

    * a run active or stale -> through the face that reported it (or the
      current face): the run's own stop, no board DELETE (finding #0); a
      voice stop from a desk tab or the kiosk Chrome used to DELETE
      (review 2, finding #4). That face may be the page's status link — the
      walk started between calls is reported there — and `stop` sends on
      the page's other socket (and every other live face) at the same time;
    * a run but no face to reach -> the board is all that is left, and the
      caller must say it is not a stop;
    * nothing seen, but a leg runner can exist here (`leg_runner_possible`)
      -> no face can vouch right now (no page connected, its status link
      down or silent, the app's run.pid poll stale; before the link: any
      walk between calls — review 2, findings #0-#2): the board DELETE
      goes, and the caller says it cannot confirm;
    * otherwise the board, as before — the sales-room desk machine.
    """
    from app import robot_face_link

    activity = robot_face_link.nav_activity()
    if activity in ("active", "stale"):
        face = robot_face_link.walking_face() or robot_face_link.current_face()
        return ("face", face) if face is not None else ("face_gone", None)
    if activity == "unknown" and robot_face_link.leg_runner_possible():
        # The face that is connected but cannot vouch for idle (its status
        # link silent, the app's poll stale) is still the only road to the
        # run's own stop: the caller sends that stop through it TOO, beside
        # the board DELETE (review 10 Oct — this used to give up on the face
        # and send the board's pause alone). None when no face is connected.
        return "unknown", robot_face_link.current_face()
    return "board", None


# ---- get_robot_status ----


def status(session) -> dict:
    """`nav` and `body` for get_robot_status (design §2d)."""
    from app import robot_face_link
    from app.config import settings

    caps = robot_face_link.caps(session, "nav") or {}
    places = load_places()
    reported = {p.get("id"): p for p in caps.get("places") or [] if isinstance(p, dict)}
    rows = []
    if places is not None:
        for place in places.places:
            card = reported.get(place.id)
            reason = card.get("reason") if card else None
            rows.append({
                "id": place.id, "name_th": place.name_th,
                # None = the robot has not reported; never guessed as available.
                "available": (card.get("available") is True) if card else None,
                "reason": reason if reason in rc.NAV_REASONS else None,
            })
    may, _why = voice_may_start(session)
    here = robot_face_link.here(session)      # finding #5: not caps.here alone
    body_state = None
    try:
        from app import robot_body

        body_state = dict(getattr(robot_body, "STATE", {}) or {})
    except Exception:
        body_state = None
    return {
        "nav": {
            "here": here,
            "run": robot_face_link.nav_status(session),
            "activity": robot_face_link.nav_activity(session),
            "places": rows,
            "voice_can_start": may,
            "voice_mode": settings.nav_voice_mode(),
        },
        "body": body_state,
    }


def status_instruction(nav: dict) -> str:
    if not nav["voice_can_start"]:
        text = ("การสั่งหุ่นเดินด้วยเสียงปิดอยู่ ห้ามเสนอพาไปเอง ถ้ามีคนขอให้พาไป "
                "ให้บอกว่าเจ้าหน้าที่สั่งหุ่นเดินจากหน้าจอหุ่นได้")
    else:
        text = ("จุดที่พาไปได้คือรายการใน nav.places ที่ available เป็น true เท่านั้น "
                "ห้ามเสนอจุดนอกรายการ available เป็น null คือหุ่นยังไม่รายงาน")
    return _with_language(text)


# ---- what the face page reports: terminal announcements and the watchdog ----


def _step_label_th(event: dict) -> str:
    """Where the robot stopped, in Thai. drive_route's own `label` and
    `detail` are English and for the operator; they are never spoken."""
    if event.get("state") == "refused":
        return "จุดเดิม ยังไม่ได้ออกเดิน"
    step = event.get("step")
    if not isinstance(step, int) or isinstance(step, bool):
        return "จุดที่ไม่ทราบ"
    do = event.get("do")
    door = event.get("door")
    if do == "door" and isinstance(door, str) and rc.valid_id(door.lower()):
        return "ประตู %s (ขั้น %d)" % (door, step)
    phase = {"checkpoint": "จุดตรวจตำแหน่ง", "drive": "ระหว่างทาง",
             "dock": "หน้าแท่นชาร์จ"}.get(do, "ระหว่างทาง")
    return "%s (ขั้น %d)" % (phase, step)


def _terminal_reason(event: dict) -> str:
    reason = event.get("reason")
    if reason in rc.NAV_REASONS:
        return reason
    # A terminal state with no code still gets a true sentence, never the
    # "bad request" fallback read to a guest about a walk.
    return {"stopped": "stopped", "refused": "refused"}.get(event.get("state"), "error")


def _name_for(session, run_id: str, place_id) -> str:
    nav = _nav(session)
    run = nav.runs.get(run_id) if nav else None
    if run is not None:
        return run.name_th
    places = load_places()
    place = places.place(place_id) if places is not None and rc.valid_id(place_id) else None
    return place.name_th if place is not None else "ที่หมาย"


def _is_latest(session, run_id: str) -> bool:
    from app import robot_face_link
    from app import session as session_module

    if session_module._active is not session:
        return False
    latest = robot_face_link.nav_status(session) or {}
    return latest.get("run_id") == run_id


async def _announce(session, text: str, run_id: str, still) -> None:
    from app import events
    from app import session as session_module

    # events.announce speaks into whichever session is `_active`. A face
    # session that is not it (another tab took over) must not have its
    # arrival read to somebody else.
    if session_module._active is not session:
        logger.info("robot face is not the active session — run %s not announced", run_id[:8])
        _record("robot_nav_announce", sent=False, reason="not active")
        return
    await events.announce(text, source="robot_nav", still_relevant=still)


async def on_nav_status(session, event: dict) -> None:
    """A validated `nav_status` from the face (robot_face_link handler)."""
    from app import robot_face_link

    state = event.get("state")
    run_id = event.get("run_id")
    origin = event.get("origin")
    nav = _nav(session, create=True)
    if rc.valid_run_id(run_id):
        run = nav.runs.get(run_id)
        if run is not None:
            run.last_seen = _now()
        if state in rc.NAV_TERMINAL and _first_log(nav, (run_id, state)):
            # Codes and ids only: no speech, no English detail.
            _record("robot_nav_run", state=state, origin=origin if origin in (
                "tap", "voice", "pc") else None, place=event.get("place")
                if rc.valid_id(event.get("place")) else None,
                reason=event.get("reason") if event.get("reason") in rc.NAV_REASONS else None)
    if state not in rc.NAV_TERMINAL or not rc.valid_run_id(run_id):
        return
    # Taps are staff driving; they are recorded above and never announced.
    if origin != "voice":
        return
    # The announce gate (safety review #9): only a run id that came back in
    # a nav_ack to a nav_command THIS session's server sent.
    if not robot_face_link.issued_run(session, run_id):
        logger.info("terminal nav_status for a run this session did not start — ignored")
        return
    if run_id in nav.announced:
        return
    nav.announced.add(run_id)
    run = nav.runs.get(run_id)
    if run is not None:
        run.announced = True
    name = _name_for(session, run_id, event.get("place"))
    if state == "arrived":
        text = "ถึง%sแล้ว บอกสั้นๆ ว่าถึงแล้ว" % name
    else:
        text = ("ไป%sไม่สำเร็จ: %s หุ่นหยุดอยู่ที่ %s บอกตรงๆ แล้วเสนอเรียกเจ้าหน้าที่"
                % (name, rc.nav_reason_th(_terminal_reason(event)),
                   _step_label_th(event)))
    await _announce(session, _with_language(text), run_id,
                    lambda: _is_latest(session, run_id))


def _arm_watch(session, run_id: str) -> None:
    from app.config import settings

    if settings.robot_nav_status_stale_s <= 0:
        return
    nav = _nav(session, create=True)
    old, nav.watch = nav.watch, None
    if old is not None and not old.done():
        old.cancel()
    task = asyncio.ensure_future(_watch(session, run_id))
    nav.watch = task
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def _watch(session, run_id: str) -> None:
    """One announcement when a voice run goes quiet; never a stop."""
    from app.config import settings

    while True:
        nav = _nav(session)
        run = nav.runs.get(run_id) if nav else None
        if run is None or run.announced or run.stale_announced:
            return
        stale_s = settings.robot_nav_status_stale_s
        if stale_s <= 0:
            return
        left = run.last_seen + stale_s - _now()
        if left > 0:
            await asyncio.sleep(left)
            continue
        run.stale_announced = True
        _record("robot_nav_stale", place=run.place, seconds=stale_s)

        def still() -> bool:
            from app import session as session_module

            current = _nav(session)
            r = current.runs.get(run_id) if current else None
            return (session_module._active is session and r is not None
                    and not r.announced
                    and _now() - r.last_seen >= settings.robot_nav_status_stale_s)

        await _announce(session, _with_language(
            "ขาดการติดต่อกับหุ่นระหว่างเดิน ห้ามบอกว่าถึงแล้ว บอกสั้นๆ ว่ายังไม่ทราบสถานะหุ่น "
            "ถ้าหุ่นยังขยับอยู่ให้กดปุ่มหยุดฉุกเฉิน (E-stop) บนตัวหุ่น"), run_id, still)
        return


def on_session_closed(session, event: dict) -> None:
    nav = _nav(session)
    if nav is None:
        return
    if nav.watch is not None and not nav.watch.done():
        nav.watch.cancel()
    del _STATE[id(session)]


def _forget_unlock(session, event: dict) -> None:
    entry = _session_unlocks.get(id(session))
    if entry is not None and entry[0] is session:
        del _session_unlocks[id(session)]


def reset_state() -> None:
    """conftest. Handlers stay registered (wiring, like @tool)."""
    for nav in list(_STATE.values()):
        task = nav.watch
        nav.watch = None
        if task is not None and not task.done():
            try:
                task.cancel()
            except RuntimeError:
                pass        # its loop is already closed
    _STATE.clear()
    _failures.clear()
    _unlocks.clear()
    _session_unlocks.clear()
    _TASKS.clear()


def _wire() -> None:
    from app import robot_face_link

    robot_face_link.on("nav_status", on_nav_status)
    robot_face_link.on("session_closed", on_session_closed)
    robot_face_link.on("session_closed", _forget_unlock)


_wire()

__all__ = ["go", "stop", "stop_plan", "status", "status_instruction", "unlock", "unlocked",
           "voice_may_start", "routes_session", "load_places", "on_nav_status",
           "reset_state", "PinError", "PIN_TRIES", "LOCKOUT_S", "UNLOCK_TTL_S"]
