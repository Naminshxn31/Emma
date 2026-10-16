"""The only thing on the server that touches body/nav frames on the face's /ws.

docs/emma-body-nav-design-2026-10-08.md §2a. The robot's face page
(`/preview?app=1`, user agent `EmmaRobot/`) is a voice client *and* the bridge
to the Android app's gesture table and nav launcher. The server sends it a
`body_command` / `nav_command` naming one thing, and waits a couple of seconds
for the page to relay the app's synchronous accept/refuse back as an ack.
Nothing here holds a tool call open while anything moves: an ack means
"queued", the outcome arrives later as status.

No new port — the same reasoning as `robot_link.py`'s docstring. And no
number that moves anything ever leaves here: an action id or a place id,
validated against `robot_contracts`, and the robot owns the rest.

**Two sockets per face page, one rulebook** (2026-10-09, the real fix for
review 2 finding #0). The call's `/ws` carries the bridge only while a call
is open, so a panel-tap walk or a PC `robot_python run` between calls left
`nav_activity()` at "unknown" and the desk STOP could not reach the app. The
page now also holds `/ws/robot-face` from load (`serve_status_link`): a
`StatusLink` object stands in for the session, so `claim`, `receive`,
`request` and `session_closed` are the same code for both. The link only
REPORTS and only ever carries a STOP: `_send` refuses anything else on it
(`not_robot_face`), so a second socket can never become a way to start
motion. It beats a nav_status every `LINK_BEAT_S` even while idle, and a
link silent for `LINK_SILENT_S` cannot vouch for "idle" (`_blind`).

**All state is per session.** Pending acks, the latest nav frame, issued run
ids and the face-token check are keyed by the session object. A second page
that merely claims `EmmaRobot/` therefore cannot answer another session's
commands or fake an arrival into it (`issued_run`). It can report itself
"active" and hold the wheel fence — a denial of service that fails safe —
but it can no longer *release* it: the fence reads the most cautious face
(`_FACE_NAV`), and only the face that reported a run may end it. Until the
fix for finding #4 (2026-10-09) one global "latest frame from any face" was
the fence, and a second page's `idle` opened it while the robot walked.

**An ack that comes after the timeout still counts** (finding #5). The page
relays a command whether or not the server is still waiting, so a `go` whose
ack was late is a robot walking while the model was told it could not. A
non-stop request that times out sends a stop of its own kind at once, and a
late *accepted* ack (`_TIMED_OUT`) sends another and enters the run into
`issued_run`, so its end is still announced.

**A stop is never refused here** (safety review #1). `request(..., {"action":
"stop"})` goes out at once with its own command id whatever is outstanding;
the pending request keeps waiting for its own ack. "busy" belongs to the arm
(decided on the robot); the link's own refusal is `request_pending`.

Registry API for the packages that build on this (P1 body, P2 nav):

    request(session, kind, payload, timeout) -> ack dict       (awaitable)
    receive(session, event, *, raw_size=None)                  session.py routes inbound frames here
    on(message_type, handler) / off(message_type, handler)     body_status/body_caps/nav_status/nav_caps/session_closed
    nav_activity(session=None) -> "idle"|"active"|"stale"|"unknown"
    walking_face()                                             the live face reporting a run, or None
    live_faces() / stop_route(first) / request_stop(first, kind, timeout)
                                                               a stop on every live face socket at once
    serve_status_link(websocket) / is_status_link(session)     /ws/robot-face (reports + stops only)
    leg_runner_possible() -> bool                              may "unknown" hide a walk? (routes on / launcher seen)
    here(session) -> place id | None                           caps.here, overridden by a newer nav_status
    issued_run(session, run_id) -> bool
    claim(session) / is_robot_face(session) / current_face()
    caps(session, kind) / nav_status(session)                  latest validated frames
    session_closed(session)                                    session.py's finally
    reset_state()                                              conftest
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import secrets
import time
from collections.abc import Callable

from app import robot_contracts as rc

logger = logging.getLogger("condo_voice.robot_face_link")

KINDS = ("body", "nav")

#: message types a handler may subscribe to. Acks are not here: they belong to
#: the request that is waiting for them, and nothing else may consume one.
HANDLED_TYPES = ("body_status", "body_caps", "nav_status", "nav_caps", "session_closed")

#: The `action` a nav_command may carry. `go` names a place; `stop` names none.
NAV_ACTIONS = ("go", "stop")
NAV_ORIGINS = ("voice",)

_ACTIVE_NAV_STATES = tuple(s for s in rc.NAV_STATES
                           if s not in rc.NAV_TERMINAL and s != "idle")


class _Pending:
    __slots__ = ("session", "kind", "command_id", "action", "future", "stop")

    def __init__(self, session, kind, command_id, action, future, stop):
        self.session = session
        self.kind = kind
        self.command_id = command_id
        self.action = action
        self.future = future
        self.stop = stop


#: (id(session), kind, command_id) -> _Pending. Every request, stops included.
_PENDING: dict[tuple[int, str, str], _Pending] = {}
#: (id(session), kind) -> command_id of the one outstanding NON-stop request.
_SLOT: dict[tuple[int, str], str] = {}
#: id(session) -> session, for every page that claimed the EmmaRobot/ UA.
_CLAIMED: dict[int, object] = {}
#: id(session) -> session, for claimed pages whose face_hello matched the token.
_VERIFIED: dict[int, object] = {}
#: id(session) -> (session, {"body_caps": ev, "nav_caps": ev, "nav_status": ev})
_FRAMES: dict[int, tuple[object, dict[str, dict]]] = {}
#: id(session) -> (session, monotonic time of its latest nav frame)
_NAV_AT: dict[int, tuple[object, float]] = {}
#: id(session) -> (session, its latest run-bearing nav frame, when), for
#: every face that reported one. Survives the face's disconnect on purpose:
#: a robot that was walking when its page dropped is still walking, so
#: `nav_activity()` with no session must go on reporting active -> stale,
#: not "unknown" (which a fence might read as "nothing running").
#:
#: Per face, and the global answer is the MOST CAUTIOUS entry (finding #4).
#: It used to be one slot that every face's frame overwrote — a second page
#: claiming EmmaRobot/ (the other ZC-3588A with the same APK, a page left
#: over after a reconnect) reporting `idle` opened the wheel fence and the
#: arm port while the real robot walked. A disconnected face's entry goes
#: only when a live face reports the same run id (the page came back) or an
#: active run with another id supersedes it.
_FACE_NAV: dict[int, tuple[object, dict, float]] = {}
#: Ranks for "most cautious": a fence must never read a weaker state over a
#: stronger one, whichever face sent it. "blind" is a CONNECTED face that
#: cannot vouch for idle (`_blind`: its link went silent or never said its
#: poll is fresh, or the app said its run.pid poll is stale); it reads
#: "unknown" outside this module but ranks above "idle" — another face's
#: idle must not hide it, whereas "unknown"
#: (rank 0) is only a face that never reported anything.
_CAUTION = {"unknown": 0, "idle": 1, "blind": 2, "stale": 3, "active": 4}
#: (id(session), kind, command_id) -> (session, action, when, timeout) for a
#: NON-stop request that timed out (finding #5). The page relays a command
#: whatever the server is still waiting for; an accepted ack arriving within
#: LATE_ACK_TTL_S is a robot that started after the model was told no.
_TIMED_OUT: dict[tuple[int, str, str], tuple[object, str, float, float, int, bool]] = {}
LATE_ACK_TTL_S = 30.0
#: (id(session), kind) -> (session, send sequence) of the latest NON-stop
#: command put on the wire. A counter, not a clock: Windows' monotonic clock
#: ticks every ~15 ms, and a go sent right after a timeout ties with it.
#: Integrator's check of #5: go A times out (a stop follows it on the wire),
#: the model sends go B, then A's late accepted ack arrives -- the late-ack
#: stop would land after B and stop the walk the model was told started.
_LAST_SENT: dict[tuple[int, str], tuple[object, int]] = {}
_SEQ = 0
#: id(session) -> (session, set of run ids acked to a nav_command it sent)
_ISSUED: dict[int, tuple[object, set[str]]] = {}
#: Latched for the life of the process once any robot face reports
#: `nav_caps.installed: true` (review 2, finding #0). Since the status link
#: the face reports between calls too, but "unknown" still happens — the app
#: closed, the page not loaded, the link down or silent, an APK without the
#: link — and on a machine that HAS a leg runner that is "cannot see", not
#: "nothing running". See `leg_runner_possible`.
_LEG_RUNNER_SEEN = False
#: Run ids remembered per face for "has this face already reported that run
#: over" (`_caps_overtaken`). A session lasts minutes; the bound is for a
#: page that invents ids.
_RUNS_KEPT = 64
_FRAME_SEQ = 0
_HANDLERS: dict[str, list[Callable]] = {}
#: Strong references to handler tasks: an un-referenced task can be garbage
#: collected mid-flight (asyncio docs), and a dropped arrival announcement
#: is a silent failure of exactly the kind this project keeps paying for.
_TASKS: set[asyncio.Task] = set()
_warned_no_token = False
_clock: Callable[[], float] = time.monotonic


def _now() -> float:
    return _clock()


# ---- the status link: the face page's second socket ----

#: The page beats a nav_status on its status link this often even while
#: idle (the 5 s beat during a walk is unchanged). The page keeps its own
#: copy (LINK_BEAT_MS in voice-preview.html).
LINK_BEAT_S = 10.0
#: A status link that has sent nothing for three beats cannot vouch for
#: "idle": a frozen WebView, a half-open TCP socket, a paused app. Its idle
#: reads "unknown" (rank "blind"), so the review-2 cannot-tell rules apply.
#: An active frame keeps its own active -> stale clock
#: (ROBOT_NAV_STATUS_STALE_S); silence never LOWERS anything.
LINK_SILENT_S = 3 * LINK_BEAT_S
#: All a status link may send in: its token hello, its reports, and the acks
#: of the stops sent on it. Nothing a call does (audio, nav_unlock, ...).
LINK_INBOUND = ("face_hello",) + tuple(rc.INBOUND_TYPES)


class StatusLink:
    """`/ws/robot-face`: the face page's reporting socket, open from page load
    whether or not a call is. Shaped like a VoiceSession where this module
    looks (`.ws`), so claim/receive/request/session_closed are the code the
    call already uses — two copies of "is this frame from the robot face"
    would drift. It is never a tool's session: `registry.robot_face_session`
    is set by `VoiceSession.run` only, so no tool can pick it for a command.
    """

    __slots__ = ("ws", "seen_at")

    def __init__(self, ws):
        self.ws = ws
        #: monotonic time of the newest frame that counted (see `receive`)
        self.seen_at: float | None = None


def is_status_link(session) -> bool:
    return isinstance(session, StatusLink)


# ---- who is the robot face ----


def claim(session) -> None:
    """A page whose user agent says `EmmaRobot/`. With no ROBOT_FACE_TOKEN
    that is the whole test, as before this module existed; with one, the
    page is the face only after a matching `face_hello`."""
    global _warned_no_token
    from app.config import settings

    _CLAIMED[id(session)] = session
    if not settings.robot_face_token:
        if not _warned_no_token:
            _warned_no_token = True
            logger.info("robot face identified by user agent only — "
                        "ROBOT_FACE_TOKEN is not set (design §2a, owner decision #14)")


def is_robot_face(session) -> bool:
    """Claimed by user agent, and — when ROBOT_FACE_TOKEN is set — proven by
    `face_hello`. Read at the moment of use, so setting the token takes
    effect for every later frame and command."""
    from app.config import settings

    if session is None or _CLAIMED.get(id(session)) is not session:
        return False
    if not settings.robot_face_token:
        return True
    return _VERIFIED.get(id(session)) is session


def current_face():
    """The most recently claimed page that still counts as the robot face, or
    None. For callers with no session of their own (the /arm stop endpoint,
    the chassis fence)."""
    for session in reversed(list(_CLAIMED.values())):
        if is_robot_face(session):
            return session
    return None


def _hello(session, event: dict) -> None:
    from app.config import settings

    expected = settings.robot_face_token
    if not expected:
        return          # nothing to prove: the user agent decides
    if _CLAIMED.get(id(session)) is not session:
        logger.warning("face_hello from a page that is not EmmaRobot/ — ignored")
        return
    host = _client_host(session)
    now = _now()
    tries = _HELLO_WRONG.get(host)
    if tries is not None and tries[1] > now:
        # Locked: not even compared. Said once per lock, not per frame.
        if not tries[2]:
            tries[2] = True
            logger.warning("face_hello from %s refused for %.0fs more — %d wrong tokens",
                           host, tries[1] - now, HELLO_MAX_WRONG)
        return
    token = event.get("token")
    if isinstance(token, str) and secrets.compare_digest(
            token.encode("utf-8"), expected.encode("utf-8")):
        _VERIFIED[id(session)] = session
        _HELLO_WRONG.pop(host, None)
        logger.info("robot face verified by face_hello")
        _record("face_hello", ok=True)
        return
    logger.warning("face_hello with the wrong token — this page is not the robot face")
    _record("face_hello", ok=False)
    count = (tries[0] if tries is not None else 0) + 1
    if count >= HELLO_MAX_WRONG:
        _HELLO_WRONG[host] = [0, now + HELLO_LOCK_S, False]
        _record("face_hello_locked", lock_s=HELLO_LOCK_S)
    else:
        _HELLO_WRONG[host] = [count, float("-inf"), False]
    if len(_HELLO_WRONG) > 256:
        for key in [k for k, v in _HELLO_WRONG.items() if v[1] <= now and v[0] == 0]:
            del _HELLO_WRONG[key]


#: Wrong face_hello tokens per client host (review 10 Oct: none was ever
#: limited — every socket, and every frame on it, could guess again). After
#: HELLO_MAX_WRONG wrong ones the host's hellos are not even compared for
#: HELLO_LOCK_S; a right one clears the count. Per host, like the nav PIN's
#: lockout, because a fresh socket would otherwise be a fresh counter.
HELLO_MAX_WRONG = 5
HELLO_LOCK_S = 60.0
#: host -> [wrong so far, locked until, lock already logged]
_HELLO_WRONG: dict[str, list] = {}


def _client_host(session) -> str:
    client = getattr(getattr(session, "ws", None), "client", None)
    return str(getattr(client, "host", None) or "?")


# ---- server -> page ----


def _refusal(reason: str, command_id: str | None = None) -> dict:
    return {"accepted": False, "reason": reason, "command_id": command_id}


async def request(session, kind: str, payload: dict, timeout: float) -> dict:
    """Send one command to the robot face and wait for its ack.

    Returns the sanitised ack, or `{"accepted": False, "reason": …}` with
    `not_robot_face` / `bad_request` / `request_pending` / `disconnected` /
    `timeout`. Never raises into a tool.

    One outstanding NON-stop request per (session, kind). `action == "stop"`
    bypasses that slot entirely: sent at once, own command id, own wait.

    A non-stop request that times out has put a stop of the same kind on the
    wire before this returns (finding #5): see `_after_timeout`.
    """
    sent = await _send(session, kind, payload)
    if isinstance(sent, dict):
        return sent
    return await _wait(sent, timeout)


async def _send(session, kind: str, payload: dict):
    """Validate, register and send one command. A refusal dict, or the
    `_Pending` now on the wire (its slot held for a non-stop)."""
    if kind not in KINDS or not isinstance(payload, dict):
        return _refusal("bad_request")
    action = payload.get("action")
    if kind == "body" and action not in rc.BODY_ACTION_IDS:
        return _refusal("bad_request")
    style = payload.get("style")
    if style is not None and not (kind == "body" and style == "speech"
                                  and action in rc.SPEECH_BODY_ACTIONS):
        return _refusal("bad_request")
    if kind == "nav":
        if action not in NAV_ACTIONS:
            return _refusal("bad_request")
        if action == "go" and not rc.valid_id(payload.get("place")):
            return _refusal("bad_request")
        if "origin" in payload and payload["origin"] not in NAV_ORIGINS:
            return _refusal("bad_request")
    stop = action == "stop"
    if is_status_link(session) and not stop:
        # The status link exists so a STOP can reach the app between calls.
        # It must never be a way to START anything: a go/gesture is the
        # call's, where the heard guard, the unlock and the announce gate
        # live. Refused here, before anything is registered or sent, so no
        # caller (current_face(), walking_face(), a future one) can widen it.
        logger.warning("%s %s refused on the status link — it only carries a stop", kind, action)
        _record("face_link_refused", command=kind, action=action)
        return _refusal("not_robot_face")
    if not is_robot_face(session):
        return _refusal("not_robot_face")
    ws = getattr(session, "ws", None)
    if ws is None:
        return _refusal("disconnected")
    slot = (id(session), kind)
    if not stop:
        held = _SLOT.get(slot)
        if held is not None and (id(session), kind, held) in _PENDING:
            return _refusal("request_pending")

    command_id = secrets.token_hex(16)
    future = asyncio.get_running_loop().create_future()
    pending = _Pending(session, kind, command_id, action, future, stop)
    _PENDING[(id(session), kind, command_id)] = pending
    if not stop:
        _SLOT[slot] = command_id
    # Whitelisted fields only: the grammar is closed (§2b), and whatever a
    # caller adds to the payload must not ride along to the bridge.
    message = {"type": "%s_command" % kind, "command_id": command_id, "action": action}
    if style == "speech":
        message["style"] = "speech"
    if kind == "nav":
        if action == "go":
            message["place"] = payload["place"]
        message["origin"] = payload.get("origin", "voice")
    try:
        await ws.send_text(json.dumps(message, ensure_ascii=False))
    except BaseException as exc:
        _release(pending)
        if not isinstance(exc, Exception):
            raise
        # A movement command must expose a failed transport — not
        # VoiceSession._send_json, which swallows (robot_link.send's rule).
        logger.warning("could not send %s_command %s", kind, action)
        return _refusal("disconnected", command_id)
    if not stop:
        global _SEQ
        _SEQ += 1
        _LAST_SENT[slot] = (session, _SEQ)
    _record("%s_command" % kind, action=action, place=payload.get("place"))
    return pending


def _release(pending: _Pending) -> None:
    _PENDING.pop((id(pending.session), pending.kind, pending.command_id), None)
    slot = (id(pending.session), pending.kind)
    if not pending.stop and _SLOT.get(slot) == pending.command_id:
        _SLOT.pop(slot, None)


async def _wait(pending: _Pending, timeout: float) -> dict:
    try:
        try:
            return await asyncio.wait_for(asyncio.shield(pending.future), timeout=timeout)
        except asyncio.TimeoutError:
            _record("%s_ack" % pending.kind, action=pending.action, accepted=False,
                    reason="timeout")
            out = _refusal("timeout", pending.command_id)
            if not pending.stop:
                # Whether the follow-up stop left: the tool may only say
                # "a stop was sent" when it was.
                out["stop_sent"] = await _after_timeout(pending, timeout)
            return out
    finally:
        _release(pending)


async def _after_timeout(pending: _Pending, timeout: float) -> bool:
    """Finding #5: the page relays a command whether or not the server is
    still waiting, so "no ack" is "may be moving" — a `go` that started
    after the model was told it could not. A stop of the same kind is on
    the wire before the tool answers (stops bypass the slot and the body
    gate), and a tombstone lets a late accepted ack send another and enter
    its run into `issued_run`, so the run's end is still announced."""
    now = _now()
    for old in [k for k, v in _TIMED_OUT.items() if now - v[2] > LATE_ACK_TTL_S]:
        del _TIMED_OUT[old]
    key = (id(pending.session), pending.kind, pending.command_id)
    _TIMED_OUT[key] = (pending.session, pending.action, now, timeout, _SEQ, False)
    sent = await _send_stop(pending.session, pending.kind, timeout, why="ack_timeout")
    tomb = _TIMED_OUT.get(key)
    if tomb is not None:
        # Remember whether that stop really left: only then does it sit on
        # the wire ahead of any later command (see `_late_ack`).
        _TIMED_OUT[key] = tomb[:5] + (sent,)
    return sent


async def _send_stop(session, kind: str, timeout: float, *, why: str) -> bool:
    """Put a stop of `kind` on the wire now; its ack is waited for in a task
    kept here, so the caller is not held for it. True when it was sent."""
    _record("%s_stop_after" % kind, why=why)
    sent = await _send(session, kind, {"action": "stop"})
    if isinstance(sent, dict):
        logger.warning("%s stop after %s not sent: %s", kind, why, sent.get("reason"))
        return False
    _keep(asyncio.ensure_future(_wait(sent, timeout)))
    return True


def _keep(task: asyncio.Task) -> None:
    _TASKS.add(task)
    task.add_done_callback(_finished)


# ---- page -> server ----


def _record(kind: str, **fields) -> None:
    try:
        from app import turnlog

        turnlog.record(kind, **fields)
    except Exception:
        logger.debug("turnlog failed for %s", kind, exc_info=True)


def _size_of(event: dict) -> int:
    try:
        return len(json.dumps(event, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return rc.MAX_INBOUND_BYTES + 1


def _reason(kind: str, value) -> str | None:
    table = rc.BODY_REASONS if kind == "body" else rc.NAV_REASONS
    if value is None:
        return None
    # An unknown code from the page is not passed on to be spoken: the tool
    # turns codes into Thai through the table, and a code that is not in it
    # reads as a bad request rather than as a guess.
    return value if isinstance(value, str) and value in table else "bad_request"


def _int_or_none(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _sanitise_ack(pending: _Pending, event: dict) -> dict:
    accepted = event.get("accepted") is True
    reason = _reason(pending.kind, event.get("reason"))
    if not accepted and reason is None:
        reason = "bad_request"
    ack = {"accepted": accepted, "reason": None if accepted else reason,
           "command_id": pending.command_id, "action": pending.action}
    if pending.kind == "body":
        status = event.get("status")
        ack["status"] = status if status in rc.BODY_STATUSES else None
        ack["est_ms"] = _int_or_none(event.get("est_ms"))
    else:
        run_id = event.get("run_id")
        ack["run_id"] = run_id if rc.valid_run_id(run_id) else None
        if accepted and pending.action == "go" and ack["run_id"] is None:
            # An accepted `go` with no usable run id would be a walk this
            # session can never be told the end of (issued_run gates the
            # announcement). Reported as accepted — the robot is moving —
            # but without the id nothing later can be attributed to it.
            logger.warning("nav_ack accepted without a valid run_id")
    return ack


def _ack(session, kind: str, event: dict) -> None:
    command_id = event.get("command_id")
    if not rc.valid_command_id(command_id):
        return
    pending = _PENDING.get((id(session), kind, command_id))
    if pending is None or pending.session is not session or pending.future.done():
        if pending is None and _late_ack(session, kind, command_id, event):
            return
        # Another session's id, a repeat, or a guess.
        logger.info("%s_ack for no pending command — ignored", kind)
        return
    ack = _sanitise_ack(pending, event)
    if kind == "nav" and ack["accepted"] and ack.get("run_id"):
        _issue(session, ack["run_id"])
    _record("%s_ack" % kind, action=pending.action, accepted=ack["accepted"],
            reason=ack["reason"])
    pending.future.set_result(ack)


def _late_ack(session, kind: str, command_id: str, event: dict) -> bool:
    """An ack for a request of THIS session that already timed out (finding
    #5). Accepted = the robot may have started after the model was told it
    could not: another stop of the same kind, and a nav run id goes into
    `issued_run` so its stopped/terminal status is still announced."""
    tomb = _TIMED_OUT.pop((id(session), kind, command_id), None)
    if tomb is None or tomb[0] is not session:
        return False
    _owner, action, at, timeout, seq, stop_sent = tomb
    if _now() - at > LATE_ACK_TTL_S:
        return False
    if event.get("accepted") is not True:
        _record("%s_ack" % kind, action=action, accepted=False, reason="late")
        return True
    if kind == "nav":
        run_id = event.get("run_id")
        if rc.valid_run_id(run_id):
            _issue(session, run_id)
    # Either kind (review 2, finding #14): on the app a body stop and a nav
    # stop are the same stop (MainActivity.bodyStop/navStop both call
    # stopBodyAndNav), so a late nod's body stop after a newer `go` stops
    # that walk too. Only the 60 s stop latches hid it.
    newer = max((entry[1] for entry in (_LAST_SENT.get((id(session), k)) for k in KINDS)
                 if entry is not None and entry[0] is session), default=None)
    if stop_sent and newer is not None and newer > seq:
        # The timeout's stop is already on the wire AHEAD of the newer
        # command (the socket is ordered), so it covers this late start.
        # Another stop now would land after the newer command and stop the
        # walk/gesture the model was told was accepted (integrator, #5).
        _record("late_ack_superseded", command=kind, action=action)
        return True
    logger.warning("late %s_ack accepted for %s — sending a stop", kind, action)
    _record("late_ack_cancelled", command=kind, action=action)
    # receive() is synchronous: the stop goes out from a task kept here.
    _keep(asyncio.ensure_future(_send_stop(session, kind, timeout, why="late_ack")))
    return True


def _issue(session, run_id: str) -> None:
    entry = _ISSUED.get(id(session))
    if entry is None or entry[0] is not session:
        entry = (session, set())
        _ISSUED[id(session)] = entry
    entry[1].add(run_id)


def _valid_frame(mtype: str, event: dict) -> bool:
    if mtype == "body_status":
        return (_int_or_none(event.get("seq")) is not None
                and (event.get("action") in rc.BODY_ACTION_IDS)
                and event.get("state") in rc.BODY_STATUS_STATES)
    if mtype == "body_caps":
        gestures = event.get("gestures")
        return (isinstance(event.get("enabled"), bool)
                and isinstance(gestures, dict)
                and all(g in rc.BODY_ACTION_IDS and s in rc.BODY_STATUSES
                        for g, s in gestures.items()))
    if mtype == "nav_status":
        state = event.get("state")
        run_id = event.get("run_id")
        place = event.get("place")
        return (state in rc.NAV_STATES
                and (run_id is None or rc.valid_run_id(run_id))
                and (place is None or rc.valid_id(place)))
    if mtype == "nav_caps":
        places = event.get("places", [])
        run = event.get("run")
        return (isinstance(event.get("enabled"), bool)
                and isinstance(places, list)
                and all(isinstance(p, dict) and rc.valid_id(p.get("id")) for p in places)
                and (run is None or (isinstance(run, dict)
                                     and run.get("state") in rc.NAV_STATES)))
    return False


def receive(session, event: dict, *, raw_size: int | None = None) -> None:
    """Route one inbound frame from a page. Called by session.py for
    `INBOUND_TYPES` and `face_hello`; never raises.

    Dropped silently-but-logged: frames from a page that is not the robot
    face, frames over MAX_INBOUND_BYTES, and ids that fail the contracts.
    Acks resolve their request by (session, kind, command_id); the rest go
    to the handlers registered with `on`.
    """
    try:
        if not isinstance(event, dict):
            return
        mtype = event.get("type")
        size = raw_size if raw_size is not None else _size_of(event)
        if size > rc.MAX_INBOUND_BYTES:
            logger.warning("%s frame of %d bytes dropped (limit %d)",
                           mtype, size, rc.MAX_INBOUND_BYTES)
            return
        if mtype == "face_hello":
            _hello(session, event)
            return
        if mtype not in rc.INBOUND_TYPES:
            return
        if not is_robot_face(session):
            logger.info("%s from a page that is not the robot face — ignored", mtype)
            return
        if is_status_link(session):
            # Liveness for `_blind`: only frames that counted (with
            # ROBOT_FACE_TOKEN, after the hello), so an unproven page cannot
            # keep a link "fresh".
            session.seen_at = _now()
        kind = mtype.split("_", 1)[0]
        if mtype.endswith("_ack"):
            _ack(session, kind, event)
            return
        if not _valid_frame(mtype, event):
            logger.info("malformed %s dropped", mtype)
            return
        entry = _FRAMES.get(id(session))
        if entry is None or entry[0] is not session:
            entry = (session, {})
            _FRAMES[id(session)] = entry
        frames = entry[1]
        frames[mtype] = dict(event)
        if kind == "nav":
            _note_order(frames, mtype, event)
        if (kind == "nav" and not _caps_says_nothing(frames.get("nav_latest"), event)
                and not _caps_overtaken(frames, event)):
            frames["nav_latest"] = dict(event)
            now = _now()
            _NAV_AT[id(session)] = (session, now)
            _note_face_nav(session, event, now)
        _dispatch(mtype, session, event)
    except Exception:
        logger.exception("robot face frame could not be routed")


def _dispatch(mtype: str, session, event: dict) -> None:
    for handler in list(_HANDLERS.get(mtype, ())):
        try:
            result = handler(session, dict(event))
            if inspect.isawaitable(result):
                try:
                    _keep(asyncio.ensure_future(result))
                except RuntimeError:
                    # No running loop (a synchronous caller): nothing can
                    # run it, so close it rather than leak "never awaited".
                    if inspect.iscoroutine(result):
                        result.close()
                    logger.warning("%s handler not run — no event loop", mtype)
        except Exception:
            logger.exception("%s handler failed", mtype)


def _finished(task: asyncio.Task) -> None:
    _TASKS.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.error("robot face handler task failed", exc_info=task.exception())


def on(message_type: str, handler: Callable) -> None:
    """Subscribe `handler(session, event)` to a validated inbound frame, or
    to "session_closed" (event = {"type": "session_closed"}). A coroutine
    handler is scheduled, not awaited. Registering the same handler twice
    is one registration. Handlers survive `reset_state` (they are wiring,
    like `@tool`); tests that add one remove it with `off`."""
    if message_type not in HANDLED_TYPES:
        raise ValueError("cannot subscribe to %r" % message_type)
    handlers = _HANDLERS.setdefault(message_type, [])
    if handler not in handlers:
        handlers.append(handler)


def off(message_type: str, handler: Callable) -> None:
    handlers = _HANDLERS.get(message_type, [])
    if handler in handlers:
        handlers.remove(handler)


# ---- what the page last told us ----


def _frames(session) -> dict[str, dict]:
    entry = _FRAMES.get(id(session))
    return entry[1] if entry is not None and entry[0] is session else {}


def caps(session, kind: str) -> dict | None:
    """The latest validated `body_caps` / `nav_caps` of this session."""
    return _frames(session).get("%s_caps" % kind)


def nav_status(session) -> dict | None:
    return _frames(session).get("nav_status")


def _state_of(frame: dict | None) -> str | None:
    if frame is None:
        return None
    if frame.get("type") == "nav_caps":
        run = frame.get("run")
        return run.get("state") if isinstance(run, dict) else "idle"
    return frame.get("state")


def _run_id_of(frame: dict | None):
    if frame is None:
        return None
    if frame.get("type") == "nav_caps":
        run = frame.get("run")
        return run.get("run_id") if isinstance(run, dict) else None
    return frame.get("run_id")


def _caps_says_nothing(latest: dict | None, event: dict) -> bool:
    """A `nav_caps` that would LOWER this face's active run (finding #4).

    The page builds `run` from the app's cached places (refreshed every
    10 s), so a caps frame during a walk can say `run: null` — the panel's
    pullCaps on openNav does exactly that. Caps may raise the state (a PC
    run can be seen there first) but never clear a run: only a nav_status
    from the face that reported it may."""
    if event.get("type") != "nav_caps":
        return False
    if _state_of(latest) not in _ACTIVE_NAV_STATES:
        return False
    return _state_of(event) not in _ACTIVE_NAV_STATES


def _note_order(frames: dict, mtype: str, event: dict) -> None:
    """When each nav frame of this face arrived, and which runs its status
    has named (and whether it called them over) — for `_caps_overtaken` and
    `here`. Also the leg-runner latch (`leg_runner_possible`)."""
    global _FRAME_SEQ, _LEG_RUNNER_SEEN
    _FRAME_SEQ += 1
    frames.setdefault("nav_order", {})[mtype] = _FRAME_SEQ
    if mtype == "nav_caps" and event.get("installed") is True:
        _LEG_RUNNER_SEEN = True
    if mtype != "nav_status":
        return
    run_id = event.get("run_id")
    if not rc.valid_run_id(run_id):
        return
    runs = frames.setdefault("nav_runs", {})
    over = runs.pop(run_id, False) or event.get("state") in rc.NAV_TERMINAL
    runs[run_id] = over          # re-inserted: newest last, oldest trimmed first
    while len(runs) > _RUNS_KEPT:
        runs.pop(next(iter(runs)))


def _caps_overtaken(frames: dict, event: dict) -> bool:
    """A `nav_caps` whose `run` this face's own status has already ended
    (review 2, finding #5) — it may not RAISE the face to active.

    The page sends caps from the app's places cache, and when a run turns
    terminal it forces one at once — before the app's refresh has landed, so
    the cache can still say `run: running` for the run that just arrived.
    That frame came after the terminal status, `_caps_says_nothing` only
    blocks lowering, and it raised the face back to active; the next caps
    (`run: null`) could not lower it and the page sends no new status while
    idle, so the wheel fence and the arm port stayed shut until the server
    restarted. Blocked when this face reported that run id terminal, or
    named it before and its newest status since is terminal (one leg at a
    time: the app starts a new run only after the old one ended). A run
    with no valid id (a PC run) is never assumed over — that would open the
    fence on a guess."""
    if event.get("type") != "nav_caps":
        return False
    run = event.get("run")
    if not isinstance(run, dict) or run.get("state") not in _ACTIVE_NAV_STATES:
        return False
    run_id = run.get("run_id")
    if not rc.valid_run_id(run_id):
        return False
    runs = frames.get("nav_runs") or {}
    if runs.get(run_id) is True:
        return True
    status = frames.get("nav_status")
    return (run_id in runs and status is not None
            and status.get("state") in rc.NAV_TERMINAL)


def here(session) -> str | None:
    """The place this face last said the robot stands at, or None.

    `nav_caps.here` comes from the same places cache as `_caps_overtaken`'s
    stale run: right after a walk it can still name the place the walk
    left from ("dock" while the robot stands in big_room), and voice
    `กลับแท่นชาร์จ` was answered "อยู่ที่นี่แล้ว" (review 2, finding #5). A
    nav_status that arrived after the caps — or caps the status has
    overtaken — decides instead: arrived = its place; under way, stopped or
    failed = somewhere on the route (None, and the app decides the leg);
    idle or refused = it did not move, so the caps still say where."""
    frames = _frames(session)
    caps_frame = frames.get("nav_caps")
    status = frames.get("nav_status")
    order = frames.get("nav_order") or {}
    caps_ok = caps_frame is not None and not _caps_overtaken(frames, caps_frame)
    caps_here = caps_frame.get("here") if caps_ok else None
    if not rc.valid_id(caps_here):
        caps_here = None
    if status is None or (caps_ok and order.get("nav_caps", 0) > order.get("nav_status", 0)):
        return caps_here
    state = status.get("state")
    if state == "arrived":
        place = status.get("place")
        return place if rc.valid_id(place) else None
    if state in ("idle", "refused"):
        return caps_here
    return None


def leg_runner_possible() -> bool:
    """Can a leg run exist on this machine that the server cannot see?

    ROBOT_NAV_ROUTES_ENABLED, or a face reported the launcher installed at
    any time since the server started (review 2, finding #0). Then a
    nav_activity() of "unknown" means "no face can vouch right now" (no page
    connected, its status link down or silent, the app's poll stale), not
    "nothing is walking" — the desk STOP and the wheel fence must not read
    it as idle. False on the sales-room desk machine: no face, no routes."""
    from app.config import settings

    return bool(settings.robot_nav_routes_enabled) or _LEG_RUNNER_SEEN


def _note_face_nav(session, event: dict, now: float) -> None:
    """Record this face's frame for the global fence, and drop disconnected
    faces' entries this report supersedes: the same run id (the page came
    back — a PC run's null id included, or reloading the page during one
    would hold the fence for ever), or an active run with an id of its own
    (one robot runs one leg at a time). A live face's entry is never
    touched by another face: it speaks for itself."""
    _FACE_NAV[id(session)] = (session, dict(event), now)
    if event.get("type") != "nav_status":
        return
    run_id = _run_id_of(event)
    active_new_run = _state_of(event) in _ACTIVE_NAV_STATES and run_id is not None
    for key, (other, frame, _at) in list(_FACE_NAV.items()):
        if other is session or _CLAIMED.get(key) is other:
            continue
        other_run = _run_id_of(frame)
        if run_id == other_run or (active_new_run and run_id != other_run):
            del _FACE_NAV[key]


def _activity_of(frame: dict | None, at: float) -> str:
    if frame is None:
        return "unknown"
    state = _state_of(frame)
    if state not in _ACTIVE_NAV_STATES:
        return "idle"
    from app.config import settings

    if _now() - at > settings.robot_nav_status_stale_s:
        return "stale"
    return "active"


def _blind(session) -> bool:
    """A connected face whose "idle" proves nothing.

    * The app said its run.pid poll is stale (`poll_stale` on its latest
      nav_status: su wedged, the poller paused in the background, the first
      second after start). Its idle is then "no run of MINE", and a PC
      `robot_python run` would be invisible — the app's own gestures already
      read that as nav_unknown. Both sockets carry the flag, so the call
      reads it the same way (an APK without it never sends it: no change).
    * A status link silent for LINK_SILENT_S: it beats every LINK_BEAT_S, so
      three missed beats is a page that stopped talking, not a quiet robot.
    * A status link whose latest nav_status does not SAY `poll_stale: false`
      (adversarial review, 9 ต.ค.). An APK built before the flag (it has
      EmmaBridge/1, so the page runs the bridge and opens the link) sends a
      bare idle while its poll is stale — and between calls that idle was
      read as vouched: the wheel fence opened where it had said run_unknown
      before the link existed, and the desk STOP answered "stop" over a PC
      run. The app states its poll on every status now (NavLauncher.status)
      and the page opens the link only for an app that does; this is the
      server's half, so neither side alone keeps an old APK's idle from
      counting. A call keeps the old rule (`True` only): its idle meant idle
      before the link, and an old APK must not change what a call reads.
    """
    status = _frames(session).get("nav_status")
    if status is not None and status.get("poll_stale") is True:
        return True
    if is_status_link(session):
        if status is None or status.get("poll_stale") is not False:
            return True
        seen = session.seen_at
        return seen is None or _now() - seen > LINK_SILENT_S
    return False


def _face_state(session, frame: dict | None, at: float) -> str:
    """`_activity_of`, with "blind" for an idle no-one can vouch for."""
    state = _activity_of(frame, at)
    if state == "idle" and _blind(session):
        return "blind"
    return state


def nav_activity(session=None) -> str:
    """idle | active | stale | unknown, from the latest nav frame.

    "active" = a non-terminal run state (from nav_status, or nav_caps.run —
    which includes a PC-started run, §2e `pc_run`); "stale" = active and no
    nav frame for ROBOT_NAV_STATUS_STALE_S; "idle" = idle or terminal
    reported by a face that can vouch for it (`_blind`); "unknown" = no face
    frame ever seen, or a blind one. With no session: the most cautious face
    (finding #4) — each face's latest frame, outliving that face's socket,
    ranked active > stale > blind > idle > unknown. Shared by the body gate
    and the wheel fence so neither owns the other's state.
    """
    if session is not None:
        entry = _NAV_AT.get(id(session))
        if entry is None or entry[0] is not session:
            return "unknown"
        state = _face_state(session, _frames(session).get("nav_latest"), entry[1])
        return "unknown" if state == "blind" else state
    worst = "unknown"
    for face, frame, at in list(_FACE_NAV.values()):
        state = _face_state(face, frame, at)
        if _CAUTION[state] > _CAUTION[worst]:
            worst = state
    return "unknown" if worst == "blind" else worst


def walking_face():
    """The connected robot face whose own latest report is a run active or
    stale, else None — where a desk STOP must go (finding #0)."""
    for key, (face, frame, at) in reversed(list(_FACE_NAV.items())):
        if (_CLAIMED.get(key) is face and is_robot_face(face)
                and _activity_of(frame, at) in ("active", "stale")):
            return face
    return None


def live_faces() -> list:
    """Every connected page that counts as the robot face (call sessions and
    status links), most recently claimed first."""
    return [s for s in reversed(list(_CLAIMED.values())) if is_robot_face(s)]


def stop_route(first=None) -> list:
    """Every socket a stop goes to: `first` (the caller's choice), the face
    that reported the run, then every other live face, newest claim first.
    `request_stop` sends on all of them at once; the order only decides
    which refusal is reported when none accepts.

    The same page holds up to two sockets (its call and its status link).
    Either carries a stop to the same app; one that refuses, times out or
    has dropped must not be the end of it (design point 3, 2026-10-09)."""
    route: list = []
    for face in (first, walking_face(), *live_faces()):
        if face is not None and not any(face is seen for seen in route):
            route.append(face)
    return route


#: How long `request_stop` keeps waiting past the ack timeout for a socket
#: whose send has not even returned (flow control: the call's socket also
#: streams the reply's audio). Only the WAIT ends there — the stop's own task
#: is kept and runs on, so a congested socket still delivers it late.
STOP_SEND_GRACE_S = 1.0


async def _stop_via(face, kind: str, timeout: float):
    return face, await request(face, kind, {"action": "stop"}, timeout)


def _stop_refusal(route: list, refused: dict, waiting: bool) -> dict:
    """The answer when no socket accepted: a socket still waiting is
    "timeout" (it may yet have reached the app); else the first refusal in
    route order that says more than "not a face"."""
    if waiting:
        out = _refusal("timeout")
    else:
        acks = [refused[id(face)] for face in route if id(face) in refused]
        out = next((a for a in acks if a.get("reason") != "not_robot_face"), None) \
            or (acks[0] if acks else _refusal("not_robot_face"))
        out = dict(out)
    out["via"] = None
    return out


async def request_stop(first, kind: str, timeout: float) -> dict:
    """A stop of `kind` on EVERY socket of `stop_route(first)` at once.

    The first accepted ack is the answer, and nothing is cancelled when it
    comes: the other sockets' stops are already on their way and their acks
    are waited for in tasks kept here. Nothing to send to -> the
    `not_robot_face` refusal `request` itself would give. The ack carries
    `via` ("call" | "status_link") for the caller's log line.

    Not one socket after another (adversarial review, 9 ต.ค.), which is how
    this first worked — measured with a probe, three ways a stop reached a
    connected robot late or not at all:
    * each socket that stayed quiet cost a whole ack timeout before the next
      was tried: three quiet faces ahead of the live one = 1.5 s at a 0.5 s
      timeout, 9 s at the real 3 s (a voice tool's ceiling is 15 s);
    * the first ack ended it, so a stop the WRONG page accepted (the other
      ZC-3588A with the same APK — its status link makes it a face all day —
      or any page that claims EmmaRobot/ without ROBOT_FACE_TOKEN) never went
      to the robot that walks: the /arm stop's `current_face()` is the newest
      claim, and a reconnect makes that the other device;
    * the ack timeout starts only after `send_text` returns, so a socket
      whose send blocked held the stop with no timeout at all, and the
      fallback never ran.
    The cost of sending to all: the app gets the same stop twice from its
    two sockets (both are `stopBodyAndNav`; the second only renews the 60 s
    latch), and every other live face is stopped too — a stop to a robot
    that is not moving is a no-op plus a latch, which is the safe side.
    """
    route = stop_route(first)
    if not route:
        out = _refusal("not_robot_face")
        out["via"] = None
        return out
    tasks = {asyncio.ensure_future(_stop_via(face, kind, timeout)): face for face in route}
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout + STOP_SEND_GRACE_S
    waiting = set(tasks)
    refused: dict[int, dict] = {}
    try:
        while waiting:
            left = deadline - loop.time()
            if left <= 0:
                break
            done, waiting = await asyncio.wait(waiting, timeout=left,
                                               return_when=asyncio.FIRST_COMPLETED)
            for task in [t for t in tasks if t in done]:     # route order
                face = tasks[task]
                try:
                    ack = dict(task.result()[1])
                except BaseException:                        # cancelled: no answer
                    ack = _refusal("disconnected")
                if ack.get("accepted") is True:
                    via = "status_link" if is_status_link(face) else "call"
                    if face is not route[0]:
                        _record("%s_stop_fallback" % kind, via=via, after=",".join(
                            "%s:%s" % ("status_link" if is_status_link(f) else "call",
                                       refused[id(f)].get("reason"))
                            for f in route if id(f) in refused))
                    ack["via"] = via
                    return ack
                refused[id(face)] = ack
        return _stop_refusal(route, refused, bool(waiting))
    finally:
        # Answered or not, every stop already sent keeps its own wait (and
        # its `_release`); a cancelled caller (the tool ceiling) must not
        # take a stop that is still leaving down with it.
        for task in tasks:
            if not task.done():
                _keep(task)


def issued_run(session, run_id: str) -> bool:
    """True only for a run id that came back in a nav_ack to a nav_command
    THIS session's server sent. The announce gate: a page that claims the
    user agent can neither fake an arrival into another session nor take
    credit for a run it did not start."""
    if not rc.valid_run_id(run_id):
        return False
    entry = _ISSUED.get(id(session))
    return entry is not None and entry[0] is session and run_id in entry[1]


# ---- lifecycle ----


def session_closed(session) -> None:
    """The session's socket is gone: every ack it was waiting for resolves
    `disconnected`, handlers hear about it, and its per-session state goes.
    Its `_FACE_NAV` entry stays while it says a run is active or stale (see
    the comment there); an idle one goes, so the table does not grow."""
    for key, pending in list(_PENDING.items()):
        if pending.session is session and not pending.future.done():
            pending.future.set_result(_refusal("disconnected", pending.command_id))
    was_face = is_robot_face(session)
    for store in (_CLAIMED, _VERIFIED):
        if store.get(id(session)) is session:
            del store[id(session)]
    for store in (_FRAMES, _NAV_AT, _ISSUED):
        entry = store.get(id(session))
        if entry is not None and entry[0] is session:
            del store[id(session)]
    for key in [k for k in _SLOT if k[0] == id(session)]:
        _SLOT.pop(key, None)
    for key in [k for k, v in _LAST_SENT.items() if v[0] is session]:
        del _LAST_SENT[key]
    for key in [k for k, v in _TIMED_OUT.items() if v[0] is session]:
        del _TIMED_OUT[key]
    face_nav = _FACE_NAV.get(id(session))
    if (face_nav is not None and face_nav[0] is session
            and _activity_of(face_nav[1], face_nav[2]) not in ("active", "stale")):
        del _FACE_NAV[id(session)]
    if was_face:
        _dispatch("session_closed", session, {"type": "session_closed"})


def _is_closed_socket(exc: BaseException) -> bool:
    """A send/receive failure that means "the other end is gone"."""
    if isinstance(exc, OSError):              # ClientDisconnected, ConnectionResetError
        return True
    try:
        from websockets.exceptions import ConnectionClosed
    except Exception:                         # pragma: no cover - always installed
        return False
    return isinstance(exc, ConnectionClosed)


async def serve_status_link(websocket) -> None:
    """`/ws/robot-face`, called by main.py AFTER `_reject_unauthorized` (the
    WS_TOKEN gate every socket has). The face page opens it at load when it
    runs in the robot's app (`EmmaBridge/1`) and keeps it with a backoff.

    * Only a user agent with `EmmaRobot/` — the call's own claim — and with
      ROBOT_FACE_TOKEN set, its frames count only after a matching
      `face_hello` (`is_robot_face`, the same check as the call).
    * In: LINK_INBOUND only, size-checked BEFORE parsing; `receive` does the
      rest (ids, shapes, the per-face state). Out: `face_link_ready` once —
      the page resets its backoff on the server's own first message, never
      on onopen, because accept-then-close (the token gate) also opens
      (CLAUDE.md "ปิดทางเข้าเดียวไม่พอ") — and then only stops (`_send`).
    * Closed = `session_closed`: a run it reported active stays active ->
      stale for the fences; an idle entry goes.
    Rows it causes in the turn log say `origin: face_status_link`, so a run
    logged on both sockets (the call's and the link's) can be told apart.
    """
    from starlette.websockets import WebSocketDisconnect

    await websocket.accept()
    agent = websocket.headers.get("user-agent", "")
    if "EmmaRobot/" not in agent:
        # The page stops for good on this code too (only the robot's app
        # ever opens this socket); a desk browser has no business here.
        await websocket.send_text(json.dumps({"type": "error", "code": "not_robot_face"}))
        await websocket.close()
        return
    try:
        from app import turnlog

        turnlog.origin.set("face_status_link")
    except Exception:
        logger.debug("turnlog origin not set for the status link", exc_info=True)
    link = StatusLink(websocket)
    claim(link)
    logger.info("robot face status link open")
    _record("face_status_link", state="open")
    try:
        await websocket.send_text(json.dumps({"type": "face_link_ready"}))
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return
            text = message.get("text")
            if not isinstance(text, str):
                continue                 # binary means nothing here
            # len() first: a str longer than the limit in characters is
            # longer in bytes, and a huge frame is never even encoded.
            size = len(text)
            if size <= rc.MAX_INBOUND_BYTES:
                size = len(text.encode("utf-8"))
            if size > rc.MAX_INBOUND_BYTES:
                logger.warning("status link frame of %d+ bytes dropped (limit %d)",
                               size, rc.MAX_INBOUND_BYTES)
                continue
            try:
                event = json.loads(text)
            except ValueError:
                continue
            if not isinstance(event, dict) or event.get("type") not in LINK_INBOUND:
                continue
            receive(link, event, raw_size=size)
    except (WebSocketDisconnect, RuntimeError):
        return
    except Exception as exc:
        # A socket that died under a send or a receive is a closed link, not
        # a server fault: uvicorn's ClientDisconnected (an OSError), a reset
        # TCP connection, websockets' ConnectionClosed. Only the first two
        # names above were caught, so a page that vanished mid-send printed
        # a full ASGI traceback for every reconnect of the backoff (review
        # 10 Oct). One line, the type, nothing of the frame.
        if not _is_closed_socket(exc):
            raise
        logger.info("robot face status link dropped (%s)", type(exc).__name__)
        return
    finally:
        session_closed(link)
        logger.info("robot face status link closed")
        _record("face_status_link", state="closed")


def reset_state() -> None:
    """Test isolation (conftest). Handlers are kept: see `on`."""
    global _warned_no_token, _LEG_RUNNER_SEEN
    _PENDING.clear()
    _SLOT.clear()
    _CLAIMED.clear()
    _VERIFIED.clear()
    _FRAMES.clear()
    _NAV_AT.clear()
    _ISSUED.clear()
    _TASKS.clear()
    _FACE_NAV.clear()
    _TIMED_OUT.clear()
    _LAST_SENT.clear()
    _HELLO_WRONG.clear()
    _warned_no_token = False
    _LEG_RUNNER_SEEN = False
