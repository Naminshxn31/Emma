"""ท่านางงาม: the Emma Event debut's beauty-queen walk -- the server's half.

Owner 9 Nov 2026: "ขอปุ่มกดท่าเปิดตัวหุ่น ขอเป็นท่านางงามเลยได้ไหม ที่เดินแล้วหันหน้าโบกมือไปมา".
Asked whether the hand may wave while the wheels turn (every other gesture is refused on a walk:
an arm out had clipped a table's corner, robot_face_link._send) and whether the head may turn on a
walk (HeadLook.walkSafe: "อย่าหันหัวไวเกินและถี่เกิน", 4 Nov): "โบกไปด้วยระหว่างเดิน" and "ท่านี้จะใช้
เฉพาะโหมดนี้แก้กฏได้ เพราะถ้าจบงานก็กลับไปโหมดเดิม".

So the exception is this one motion, in this one app, on this one walk:
- the robot owns the motion (Pageant.java: the hand, the elbow, the head, their clamps and times,
  its own 45 s cap) -- this module says only "start" and "end" (robot_contracts.EVENT_BODY_ACTIONS,
  `style: "event"`), the rule that the server never holds a pulse;
- the server's own arm keys hold for it like for any gesture (`_gate`: ROBOT_BODY/ARM/ARM_MOTION
  and the desk's /arm STOP -- review 9 Nov: it went straight to the link and skipped both);
- only a scene marked `"pageant": true` asks for it (event_host), only the Emma Event app's page
  relays it and only the Emma Event app plays it (`not_event_app` everywhere else), and only an
  APK whose body_caps says `pageant` is asked;
- on a walk, only the walk that scene started (`walk_is_mine`; robot_face_link refuses its start on
  any other); its end is never refused by a walk -- the hand coming down is what a walk wants;
- a start belongs to the press that asked (`still`): one accepted after staff moved on is brought
  down at once (review 9 Nov: a retried start was accepted on the NEXT scene and never ended);
- the hand comes down (`end`) after her line, when the walk ends short, before any other walk
  rests the arm (event_walk._arm_rested), and at the next scene. `end` asks the APP whether a
  pageant is still up (its ack's est_ms: null = nothing), rather than trusting what the server
  believes. The red stop is the app's: it freezes the arm where it is and latches, like every
  gesture.

While it runs the head is the pageant's: head_follow sends no look, speech_gestures plays no
talking arm (`claims_arm`, from just before the debut's line), the camera's greetings wait.
Played standing at the dock on 10 Nov (the debut scene, walking off); never yet on a walk.
"""
from __future__ import annotations

import asyncio
import logging
import time

from app import robot_face_link, turnlog
from app.config import settings

logger = logging.getLogger("condo_voice.event_pageant")

#: Pageant.MAX_MS + its lowering, and some: the app ends it by itself by then, a status that never
#: came (a dropped page) must not leave `active` true -- the head would never be followed again.
APP_MAX_S = 45.0 + 3.0
END_SLACK_S = 25.0
#: The walker must be out (board_walk's `@@ started`, state walking) within this of the scene's
#: start for the hand to come up on the way. After it, the debut waves at the spot instead.
START_WAIT_S = 8.0
#: Standing where she is, the pageant runs at least this long: one turn each way, however short her
#: line -- since 23 Nov (owner at the ceremony's opening on the robot: "ยกมือช้าไปหน่อย อยากให้ไวกว่านี้ได้ไหม")
#: the raise 1.5 + 1.2 + 0.8 + 0.8 = 4.3 s, the first side 4.5 s (its turn: the head's first, blind move) +
#: 3 x 1.2 s, the second 2 + 3 x 1.2 s = 18.0 s -- Pageant.java (until then 6.9 + 5.6 + 5.6 = 18.1 s, and 18.7 here;
#: tonight's first cut 16.2 s / 16.8). Counted from the app's own "started" when it came (`started_at`: the app
#: records it when the frames go, after its arm connect -- a cold connect took seconds the slack alone does not
#: cover, review 10 Nov); the 0.6 s slack is the app starting its frames after `since` (review 10 Nov: 13.0 cut the
#: right side's last stroke). Not more: the third side's turn starts at two sides' end.
#: tests/test_pageant_timing.py ties it to the app's own frames (PageantTiming.twoSidesMs + STANDING_SLACK_S).
MIN_STANDING_S = 18.6
#: Since 24 Nov MIN_STANDING_S and settings.event_pageant_lead_s are THIS SERVER'S v2 constants (the
#: APK built with 23 Nov's frames): what is used comes from the app's own body_caps (`_app_timing`) --
#: review 24 Nov found no version handshake: tonight's 2.4 / 16.8 with the APK of 10-23 Nov (its
#: raise 3.0 + 1.5 + 1.2 + 1.2 = 6.9 s, two sides 18.1 s) would start her line inside its 3 s first
#: raise frame and bring the hand down mid-stroke of its second side (16.8 < 18.1: the end 10 Nov's
#: review took out; 2.7 / 18.6 since, the same lead fault). An app that says no `pageant_v` is that APK: its
#: own known-good values, the ones this module ran with until 23 Nov.
V1_LEAD_S = 4.5
V1_MIN_STANDING_S = 18.7
#: Over the app's reported `pageant_two_sides_ms`: the app starts its frames after `since` (review 10 Nov:
#: 13.0 cut the right side's last stroke). MIN_STANDING_S is the current frames' 18.0 s + this.
STANDING_SLACK_S = 0.6
#: A reported timing outside these is not believed (the old APK's values instead): a lead longer than
#: any raise ever played (6.9 s), two sides longer than the app's own cap (Pageant.MAX_MS 45 s).
MAX_LEAD_MS = 10000
MAX_TWO_SIDES_MS = 45000
#: The hand stays up this long after her line's voice ends.
TAIL_S = 1.0
#: The longest a line is waited for before the hand comes down anyway.
LINE_WAIT_S = 40.0
#: A start the arm answers `busy` / `request_pending` is asked again this often for this long (an
#: earlier answer's arm still resting: event_host._gesture's rule for the greeting wave).
RETRY_S = 6.0
RETRY_EVERY_S = 0.5
#: `end(wait=True)`: the lowering (3 s), the beat after it (1 s) and group 99 to its #AGF (the app
#: gives that 15 s).
END_WAIT_S = 22.0
END_ASK_EVERY_S = 0.5
#: From just before the debut's line to the pageant's start (her voice may take VOICE_WAIT_S, the
#: start RETRY_S): her talking arm keeps off the arm meanwhile -- an arm held out by a beat when
#: the pageant takes it stays out until group 99 (review 9 Nov).
WANT_S = 40.0
#: Her line waits for the hand (owner 11 Nov, the debut pressed standing: "เหมือนมือจะขึ้นช้ากว่า
#: เสียงที่พูด"): the start went with her first word and the raise was 6.9 s (Pageant.java then: 3.0,
#: then 1.5 + 1.2 + 1.2), so the hand reached the top as her 7.4 s line ended. The start is asked
#: BEFORE the line now (event_host._say_line) and the line goes `lead_s()` after the app's "started" for
#: it (its frames going: the raise's first two stages, as the app reports them -- 2.7 s of the 4.3 s raise
#: since 23 Nov, 4.5 s of the old APK's 6.9) -- counted from the accept when no "started" came within this.
STARTED_WAIT_S = 5.0
#: A re-press of the scene while its standing hand is up keeps it up (owner 11 Nov 09:36: pressed
#: again 9 s after, the hand came down for the new scene, the app answered the new start `busy`
#: while lowering for 6 s, and the hand rose after her line was over and waved 19 s alone). Only
#: this early in the app's run: the app ends the run itself after its last whole side (Pageant.MAX_MS,
#: ~44.2 s of frames since 23 Nov, 40.5 before), and another line (7.4 s) with its tail must fit before that.
KEEP_WITHIN_S = 25.0

#: The camera's greetings wait while the pageant is up: the debut's own line greets the room, and a
#: head turning without the server's word moves every face box (facewatch's same-spot memory).
GREETER_KEY = "pageant"

#: `on`: the app accepted a start and has not reported it over; `seq`: the event host's press it
#: belongs to; `since`: wall time just before the accepted request (statuses are stamped on
#: arrival -- a fast failure reported before the ack was handled still counts); `ended_at`: the
#: app's last terminal status for "pageant" (`_on_status`, its own: robot_body.STATE keeps only
#: the latest row of any gesture); `accepted_at`: the last accepted start, for `end` to ask the
#: app; `asking`: a start's request in flight; `wanted_until`: claims_arm before the start;
#: `timing`: what the accepting app said of its frames' timing (`_app_timing`).
STATE: dict = {"on": False, "seq": None, "since": 0.0, "run_id": None, "want_run": None,
               "ended_at": 0.0, "accepted_at": 0.0, "asking": False, "wanted_until": 0.0,
               "started_at": 0.0, "asked_at": 0.0, "end_asked_at": 0.0, "timing": None}


def reset() -> None:
    _over()
    STATE.update(on=False, seq=None, since=0.0, run_id=None, want_run=None, ended_at=0.0,
                 accepted_at=0.0, asking=False, wanted_until=0.0, started_at=0.0, asked_at=0.0,
                 end_asked_at=0.0, timing=None)


def _whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _app_timing(session, est_ms) -> dict:
    """What the app that accepted the start said of its pageant's timing (review 24 Nov: no version
    handshake), read at the accept -- an app's caps do not change within a session. {"v", "lead_ms",
    "two_sides_ms", "why"}:
    - v None: no body_caps from that session at all. Never a real app's start (robot_face_link refuses
      a pageant to a face whose caps do not say it plays one) -- only a test's own link: the knobs.
    - v 1: caps without `pageant_v` (the APK of 10-23 Nov), a version past 2 without its numbers, or
      numbers not believed: V1_LEAD_S / V1_MIN_STANDING_S, the longer wait of the two.
    - v 2 without numbers: this server's own v2 constants (the knobs).
    - with numbers (the APK's PageantTiming, from its own frames): those.
    The cross-check: the start ack's est_ms is the run the app actually started (its frames and its
    lowering, BodyController.requestPageant). A run shorter than the two sides the caps report is not
    the run those caps describe."""
    caps = robot_face_link.caps(session, "body") if session is not None else None
    if not caps:
        return {"v": None, "lead_ms": None, "two_sides_ms": None, "why": "no_caps"}
    v = caps.get("pageant_v")
    old = {"v": 1, "lead_ms": None, "two_sides_ms": None}
    if not _whole(v) or v < 2:
        return dict(old, why="old_app")
    lead, two = caps.get("pageant_lead_ms"), caps.get("pageant_two_sides_ms")
    if not (_whole(lead) and _whole(two)):
        if v == 2:
            return {"v": 2, "lead_ms": None, "two_sides_ms": None, "why": "v2_constants"}
        return dict(old, why="unknown_version")
    if not (0 < lead <= MAX_LEAD_MS and lead < two <= MAX_TWO_SIDES_MS):
        return dict(old, why="implausible")
    if _whole(est_ms) and est_ms < two:
        return dict(old, why="est_ms_disagrees")
    return {"v": v, "lead_ms": lead, "two_sides_ms": two, "why": "caps"}


def lead_s() -> float:
    """hand_up's lead for the pageant up now: the app's own raise (`pageant_lead_ms`), this server's
    v2 constant settings.event_pageant_lead_s when the app gave only its version (or no caps reached
    us), V1_LEAD_S for the old APK. EVENT_PAGEANT_LEAD_S 0 = no wait at all whatever the app (the line
    with the start, as before 11 Nov; the tests' default)."""
    knob = float(settings.event_pageant_lead_s)
    if knob <= 0:
        return 0.0
    timing = STATE.get("timing") or {}
    if timing.get("lead_ms"):
        return timing["lead_ms"] / 1000.0
    return V1_LEAD_S if timing.get("v") == 1 else knob


def min_standing_s() -> float:
    """end_after_line's standing minimum for the pageant up now: the app's own two sides
    (`pageant_two_sides_ms`) + STANDING_SLACK_S, this server's v2 constant MIN_STANDING_S when the app
    gave only its version (or no caps reached us), V1_MIN_STANDING_S for the old APK."""
    timing = STATE.get("timing") or {}
    if timing.get("two_sides_ms"):
        return timing["two_sides_ms"] / 1000.0 + STANDING_SLACK_S
    return V1_MIN_STANDING_S if timing.get("v") == 1 else MIN_STANDING_S


def _over() -> None:
    """It ended (the app said so, or its cap passed): the camera's greetings may come again."""
    STATE["on"] = False
    try:
        from app import greeter

        greeter.release(GREETER_KEY)
    except Exception:                                    # noqa: BLE001
        pass


def _on_status(session, event: dict) -> None:
    """The app's own word that a pageant started, or ended (done / stopped / failed)."""
    if event.get("action") != "pageant":
        return
    if event.get("state") in ("done", "stopped", "failed"):
        STATE["ended_at"] = time.time()
    elif event.get("state") == "started":
        STATE["started_at"] = time.time()


def _standing_from() -> float:
    """Where MIN_STANDING_S counts from: the app's "started" for this request when it came, else
    the request (`since`)."""
    since = float(STATE["since"])
    started = float(STATE.get("started_at") or 0.0)
    return started if started >= since else since


robot_face_link.on("body_status", _on_status)


def active(seq: int | None = None) -> bool:
    """The pageant is up on the robot (accepted, not yet reported over) -- for `seq`'s press when
    given. A status that never came ends it at APP_MAX_S + END_SLACK_S."""
    if not STATE["on"]:
        return False
    if float(STATE["ended_at"]) >= float(STATE["since"]):
        _over()
        return False
    if time.time() - float(STATE["since"]) > APP_MAX_S + END_SLACK_S:
        _over()
        return False
    return seq is None or STATE["seq"] == seq


def ending() -> bool:
    """The app took an end of the run up now (its hand on the way down, `active` until its done):
    not a hand to keep for another line, nor one her line waits for (review 11 Nov: a re-press
    MIN_STANDING_S-25 s in "kept" a hand the last line's end was bringing down)."""
    return float(STATE["end_asked_at"]) >= float(STATE["since"]) > 0


def want() -> None:
    """The debut's line on a walk whose start was refused (event_host._say_line): her talking arm
    keeps off the arm until the line's end lets go (`unwant`) -- `claims_arm`."""
    STATE["wanted_until"] = time.monotonic() + WANT_S


def unwant() -> None:
    STATE["wanted_until"] = 0.0


def claims_arm() -> bool:
    """For speech_gestures: the pageant has the arm, is being asked for, or is about to be."""
    return active() or bool(STATE["asking"]) or time.monotonic() < float(STATE["wanted_until"])


def walk_is_mine() -> bool:
    """robot_face_link's question for a start during a walk: the event walk under way is the one the
    debut asked the pageant for (`start(run=...)` names it before it asks)."""
    from app import event_walk

    run = event_walk.current()
    return (run is not None and STATE["want_run"] is not None and run.id == STATE["want_run"]
            and run.state == "walking")


def _session():
    from app import robot_body

    return robot_body._staff_session()


def _gate() -> str | None:
    """robot_body._request's steps 2 and 2b: the server's arm keys and the desk's /arm STOP."""
    from app import robot_arm, robot_body

    if not robot_body._server_gate("pageant"):
        return "server_disabled"
    if not robot_arm.armed():
        return "latched"
    from app import event_box

    if event_box.holds(_session()):
        return event_box.REASON                         # in the box: the arm swings out sideways into it
    return None


async def _ask(session, action: str) -> dict:
    try:
        return await robot_face_link.request(session, "body", {"action": action, "style": "event"},
                                             settings.robot_body_ack_timeout_s)
    except Exception:                                    # noqa: BLE001 -- the phone says so
        logger.exception("the pageant's %s could not be sent", action)
        return {"accepted": False, "reason": "disconnected"}


async def start(seq: int, *, run=None, still=None) -> dict:
    """Ask the app for the pageant. {"ok", "error", "error_th"}: ok = the robot accepted it, never
    "it waved" (the mock rule). `run`: the debut's event walk (None = where she stands). `still`:
    the press is still the one that asked -- checked before every try and after the answer."""
    from app import robot_contracts as rc

    still = still or (lambda: True)

    def no(code: str) -> dict:
        turnlog.record("event_pageant", phase="start", ok=False, error=code, walking=run is not None)
        return {"ok": False, "error": code, "error_th": rc.body_reason_th(code) if code != "moot" else MOOT_TH}

    try:
        reason = _gate()
        if reason is not None:
            return no(reason)
        session = _session()
        if session is None:
            return no("not_robot_face")
        # up for this press -- and not on its way down (review 14 Nov, round 2: pressed after a
        # "เอามือลง" or while the line's end lowered it, this said "accepted" with the hand going down)
        if active(seq) and not ending():
            return {"ok": True, "error": None, "error_th": None}
        STATE.update(want_run=run.id if run is not None else None, asking=True)
        # An older pageant not yet reported over (still lowering, its group 99): its `done` can reach
        # the server after this request left and before the ack -- read against a `since` taken
        # before the request it ends the NEW one at once (re-check 9 Nov, probed). Then `since` is
        # the ack's time; a fast failure of the new one is still caught by `end` asking the app.
        older = float(STATE["accepted_at"]) > 0 and float(STATE["ended_at"]) < float(STATE["since"]) \
            and time.time() - float(STATE["accepted_at"]) < APP_MAX_S + END_SLACK_S
        deadline = time.monotonic() + RETRY_S
        while True:
            if not still():
                STATE["want_run"] = None
                return no("moot")
            since = time.time()
            ack = await _ask(session, "pageant")
            if ack.get("accepted") is True or ack.get("reason") not in ("busy", "request_pending") \
                    or time.monotonic() + RETRY_EVERY_S > deadline:
                break
            await asyncio.sleep(RETRY_EVERY_S)
            if active(seq) and not ending():
                return {"ok": True, "error": None, "error_th": None}     # the walk's own start got there
            reason = _gate()
            if reason is not None:
                return no(reason)
        if ack.get("accepted") is not True:
            STATE["want_run"] = None
            return no(ack.get("reason") or "bad_request")
        STATE.update(on=True, seq=seq, since=time.time() if older else since, accepted_at=time.time(),
                     run_id=STATE["want_run"], asked_at=since, timing=_app_timing(session, ack.get("est_ms")))
    finally:
        STATE["asking"] = False
        unwant()
    try:
        from app import greeter

        greeter.hold(GREETER_KEY, APP_MAX_S + END_SLACK_S)
    except Exception:                                    # noqa: BLE001 -- a greeting is not worth a failure
        logger.exception("the camera's greetings could not be held for the pageant")
    timing = STATE["timing"] or {}
    turnlog.record("event_pageant", phase="start", ok=True, walking=run is not None, timing_v=timing.get("v"),
                   timing_why=timing.get("why"), lead_s=lead_s(), min_standing_s=min_standing_s())
    if not still():
        # staff moved on while the answer was on its way: it was asked for a scene that is gone
        await end("moot")
        return no("moot")
    return {"ok": True, "error": None, "error_th": None}


#: The phone's word for a start the press outran.
MOOT_TH = "เปลี่ยนฉากหรือกดหยุดก่อนหุ่นตอบ จึงเอามือลง"


async def hand_up(seq: int, still) -> bool:
    """Wait until the hand leads her line by `lead_s()` (the accepting app's own): from the app's "started"
    for this start (`asked_at`: a "started" before the request is an older run's), or from the
    accept when no "started" came within STARTED_WAIT_S. True = say the line now -- also when the
    pageant ended meanwhile (the line goes without it); False = the press is gone (`still`)."""
    lead = lead_s()
    while True:
        if not still():
            return False
        if lead <= 0 or not active(seq) or ending():
            return True
        now = time.time()
        started = float(STATE.get("started_at") or 0.0)
        if started >= float(STATE["asked_at"]) > 0:
            if now >= started + lead:
                return True
        elif now >= float(STATE["accepted_at"]) + max(lead, STARTED_WAIT_S):
            return True
        await asyncio.sleep(0.1)


def on_a_walk() -> bool:
    """The hand up now came up on a walk (the debut's: `start(run=...)`), not where she stands."""
    return STATE["run_id"] is not None


def can_keep(scene_was: str | None, scene_id: str) -> bool:
    """A press of the same scene again: its standing hand is up, early enough in the app's run for
    another line (KEEP_WITHIN_S) -- it stays up for it (`keep`) instead of coming down and up."""
    return (scene_was == scene_id and active() and not on_a_walk() and not ending()
            and time.time() - _standing_from() < KEEP_WITHIN_S)


def keep(seq: int) -> None:
    """The hand up is the new press's now: its line ends it (`end_after_line(seq)`), the last
    press's does not (it checks `seq`)."""
    STATE["seq"] = seq
    turnlog.record("event_pageant", phase="kept", seq=seq)


def _maybe_up() -> bool:
    """A pageant was accepted recently enough that the app may still have it up -- whatever the
    server believes (`active`). `end` asks the app then; a redundant end is a no-op there."""
    return active() or time.time() - float(STATE["accepted_at"]) < APP_MAX_S + END_SLACK_S


async def end(why: str, *, wait: bool = False) -> bool:
    """Bring the hand down (the app: one slow frame, then group 99). Nothing when no pageant was
    accepted lately. `wait` (a walk about to rest the arm, which the app refuses `busy` meanwhile):
    a start still in flight is waited for, then the app is asked again until it says nothing is up
    (its ack's est_ms null), END_WAIT_S at most. True = the app said so, or nothing was up."""
    if wait:
        deadline = time.monotonic() + RETRY_S + float(settings.robot_body_ack_timeout_s) + 1.0
        while STATE["asking"] and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
    if not _maybe_up():
        return True
    session = _session()
    deadline = time.monotonic() + END_WAIT_S
    first = True
    while True:
        ack = await _ask(session, "pageant_end") if session is not None \
            else {"accepted": False, "reason": "not_robot_face"}
        if first:
            turnlog.record("event_pageant", phase="end", why=why, sent=ack.get("accepted") is True,
                           reason=None if ack.get("accepted") else ack.get("reason"))
            first = False
        if ack.get("accepted") is True and ack.get("est_ms") is None:
            STATE["accepted_at"] = 0.0
            _over()
            return True                                  # the app has no pageant up
        if ack.get("accepted") is True:
            STATE["end_asked_at"] = time.time()          # coming down (`ending`)
        if not wait or time.monotonic() + END_ASK_EVERY_S > deadline:
            return not active()
        if ack.get("accepted") is not True and ack.get("reason") != "request_pending":
            # nobody to ask -- or a timeout, after which the link already sent a body STOP (the arm
            # is frozen and latched; asking again would only hear "nothing up"): what the server knows
            return not active()
        await asyncio.sleep(END_ASK_EVERY_S)


async def on_walk(seq: int, run, still) -> dict:
    """The debut's walk: the hand comes up once the walker is out (state walking, past its own
    gate), not during the arm's rest before it. {"ok", ...} as `start`, or "walk_ended" when the
    walk never got going in START_WAIT_S."""
    deadline = time.monotonic() + START_WAIT_S
    while not (run.saw_started and run.state == "walking"):
        if run.state not in ("starting", "walking") or not still() or time.monotonic() > deadline:
            turnlog.record("event_pageant", phase="start", ok=False, error="walk_ended", walking=True)
            return {"ok": False, "error": "walk_ended", "error_th": "การเดินไม่ได้ออกตัว"}
        await asyncio.sleep(0.1)
    return await start(seq, run=run,
                       still=lambda: still() and run.state == "walking")


async def end_after_line(seq: int, *, standing: bool) -> None:
    """After the scene's line: the hand comes down once her voice is over (TAIL_S after it), and
    standing not before `min_standing_s()` (the accepting app's two sides) from the start -- one turn each
    way. Ended meanwhile (the
    press changed, a stop, its status): asked down anyway -- the app answers a no-op."""
    from app import session as session_module

    deadline = time.monotonic() + LINE_WAIT_S
    quiet_since = None
    while time.monotonic() < deadline:
        if not active(seq):
            break
        session = session_module._active
        until = float(getattr(session, "_play_until", 0.0) or 0.0) if session is not None else 0.0
        now = time.monotonic()
        if until > now:
            quiet_since = None
        elif quiet_since is None:
            quiet_since = now
        long_enough = not standing or time.time() - _standing_from() >= min_standing_s()
        if quiet_since is not None and now - quiet_since >= TAIL_S and long_enough:
            break
        await asyncio.sleep(0.25)
    if active() and STATE["seq"] != seq:
        return                                           # a later press's pageant: not this line's to end
    await end("line_done")
