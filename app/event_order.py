"""The launch ceremony's running order, pressed step by step on the staff phone.

Owner 7 Nov 2026: "ทำเป็นแบบกดสั่งด้วยนะ พวกซีนต่างๆ" -- the whole ceremony (nine
scenes of คุณมิกิ's script) run from the phone, one step at a time, following the host.
The steps are data (`order` in CEREMONY_FILE, app/event_script.py `order()` /
`order_problems()`); this module presses them, through the paths that already exist and
keep their own gates:

  screen  -> event_host.big_screen    (the room's big screen open/close: a scene's `tv` code)
  walk    -> event_host.go_spot       (EVENT_WALK_ENABLED, a recorded spot, event_walk's gates)
  body    -> robot_body.staff_request (every gesture gate but the heard guard -- see there)
  cue     -> event_cues.play          (approved lines only; `show` = a story beside the line)
  clip    -> event_host.play_clip     (a file and a big screen)
  handoff -> ceremony.handoff         (the robot face opens the Emma Event app, and says so)
  device  -> venue_devices.run        (the venue's lights / curtains: an IR/RF frame or an http call)
  manual  -> nothing runs: pressing marks it done (lights, curtains, "next page", printing --
             things not connected to anything; the step says so in its note)

**A device step is part of the order only on a machine that drives that device** (owner 20 Nov,
"เตรียมด้วยนะ หุ่นอาจจะต้องสั่งปิดไฟ ปิดม่านได้"; app/venue_devices.py). VENUE_DEVICES off, or the device
still `manual` in the venue file = the step is not in `_scenes()` at all: not shown, not pressed, not
"ถัดไป" -- the ceremony runs exactly as before (each step keeps its file index `i`, the page never shows
it). In use, it sits BEFORE the staff tick that confirms the room really changed, and the line saying
"are off" stays after that tick: sent is not done (IR/RF are one-way). So the tick right after a device
step in use reads the device's own confirm note, not the file's "หุ่นไม่ได้คุมไฟ". A device press is not a
line: it may go while her line is still heard or the robot moves (the lights go off on her "now"); every
other gate holds. A failed one says how to go on by hand (retry with "ถัดไป", or skip it and the team
does it -- the tick still waits). The red stop does not touch a light.

**Only with the Emma Ceremony app on the robot** (owner 9 Nov: "อันนี้ทำแยกกับอันเดิมนะ ไม่งั้น
จะปนกัน" -- app/ceremony.py): every step but a manual tick answers `not_ceremony_app`
otherwise, and its readiness says so. No `scene` step since then: the scenes are the Emma Event
app's, and opening the ceremony app is what starts the ceremony (the step 1.0 that switched the
mode is now the big screen's).

A cue with only a live brief (s4_recommend: คุณมิกิ asks, Emma answers from the
live data) opens her LIVE WINDOW (event_host.live_open, its `live_th` as the brief):
the ceremony is cue-only, and outside such a window she answers nobody. Staff press it
BEFORE คุณมิกิ asks; it stays running ("Emma กำลังฟังคุณมิกิ") and "ถัดไป" (or the step
again) closes it once she has answered (`live_close("done")`) -- only then is it taken.
The window closing by itself (its own limit: `live_state()` None) ends it done, with a
note; the red stop closes it (`staff_stopped`). Until 7 Nov the press was a tick and
the brief never reached the model.

What it holds to:

* **A step names an id, never words** (the `show_unit(room)` rule): the phone sends (n, i).
* **Readiness is the real gates' answer, read without pressing** (`ready`, `why_th`): a draft
  line, a film on the big screen, walking switched off, a spot nobody recorded, an arm
  latched, no call on the robot face. "ภาพไม่ขึ้น" for a story that is not offered is a
  warning, not "not ready": the line is the step, the picture rides along.
* **Nothing slow on the event loop.** The phone polls every second. The cue rows'
  content checks (number sources, report chapters) come from event_cues' own cached
  status, computed in a thread; the stories catalog is read in a thread here, cached.
  Review 7 Nov: a clean cue's empty problem list sent this module back to
  `event_script.problems()` on the loop, 11 times a poll, ~250 ms a poll.
* **`next` never jumps.** A step that is not ready answers `not_ready` and the order stays
  where it was -- staff skip it on purpose (`skip`), never by a second tap. A phone that
  says which step its button showed (`expect`) is refused when the order moved since.
* **`last` never rewinds.** It moves to a step that was taken (accepted by its path, or
  skipped) only when that step comes after it: a third handshake pressed again after the
  greeting must not bring the greeting back as "ถัดไป" (review 7 Nov, the skip case).
* **One press at a time, one line at a time.** The press gate is set before the first
  await (review 7 Nov: two presses at once both ran). While a ceremony line is being said
  (the order's, or one pressed on /ceremony/cue) every press but a tick is refused,
  "กำลังพูดอยู่". A cue stays `running` while event_cues says it is, AND while her voice
  of it still plays (`event_cues.line_audible`: a read line ends for the model at its
  turn_complete with ~20 s of her voice still to come -- review 7 Nov), then done/failed.
  A clip step stays `running` until the big screen says it plays (or failed).
* **The red stop wins over a press in flight** (`staff_stopped`, cut_gen, the walk's own
  count): a press that was still reading its readiness when it came runs nothing, and a
  walk that a press set out after it is stopped at once (review 7 Nov).
* **It survives a restart.** `last` and the marks are kept next to the walk's run file
  (`state_path`), with the order's digest and the time: an Android reset mid-ceremony
  must not hand staff step 1.0 again (the big screen opened again drops the film it
  fetched). Ignored when the order changed or it is older than EVENT_CEREMONY_MAX_S (or dated
  in the future: a clock that was behind); a step that was running when the server died
  comes back failed. The same age rule holds without a restart (`_expire_old`): a morning
  rehearsal's progress is cleared before the real ceremony, and the page says so.
* **One turn-log row per press** (`event_order`): n, i, do, ok, error, via. No words.
* **A joke is said straight through** (`"chain": true` on its set-up line; owner 23 Nov, watching the
  preview: "ฉากที่เล่นมุข ที่ตอนโดดลงสระ และแฟนพันธุ์แท้ให้พูดต่อเนื่องเลย ไม่ต้องเว้นช่วงมันดูเงียบ และช้า"):
  staff press the set-up once and the punchline -- the next step of the scene -- is pressed by the
  server the moment the set-up has been said (`_chain_watch`): its event_cues run over and ok. Not
  after the cut's AUDIBLE_SLACK_S, not after a second look at the page: the press goes in while the
  set-up's last seconds still play and event_cues waits them out before the punchline's first word
  (`_until_quiet`, every press's own wait) -- so the room hears no gap but the voice's own. It is a
  press like a staff press (`_run`, via "chain"), every gate but that one tail (`_line_busy(tail=
  False)`: another line, a press entering, a running mark still refuse it). A stop of any kind
  since the set-up was pressed, the set-up failed or cut, the order reset, or the punchline pressed
  or skipped by staff meanwhile = no punchline from here. A gate that refuses = the punchline waits
  for staff as before, and its step says why (`chain` in the snapshot) -- a set-up that failed or was
  cut says to press the SET-UP again (CHAIN_LEAD_FAILED_TH), and pressing it again retires the old
  chain at once (no passing "missed"). Not kept across a restart: the set-up comes back failed
  "restarted", the punchline is staff's. The room screen holds a line that arrives while the voice
  before it is still queued there until that voice has played out (client/voice-preview.html
  `mirrorSubNext`): the robot's clock is not the screen's, which plays her voice later by the Wi-Fi.

Nothing here has run on the robot yet.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import time
import unicodedata
from pathlib import Path

from app import event_script, turnlog

logger = logging.getLogger("condo_voice.event_order")

#: How much of a cue's line names its button when the file gives no label.
LABEL_CHARS = 40
#: The stories catalog's answer (names, which are offered), read in a thread and kept
#: this long -- the same trade as event_cues' STATUS_TTL_S for its rows.
STATIC_TTL_S = 10.0

#: `stops`: the red stops so far (`staff_stopped`). `changed_at`: monotonic time of the last
#: thing staff did to the order (marks, `last`), for the age rule. `cleared`: the old progress the
#: age rule cleared ({"at", "from"}), for the page, until the next press.
STATE: dict = {"marks": {}, "last": None, "pressing": None, "gen": 0, "restored": False, "persisted": None,
               "stops": 0, "changed_at": None, "seen": None, "cleared": None, "chain": None}
_STATIC: dict = {"at": None, "key": None, "value": None}
#: The venue device steps being pressed now, (n, i): their own gate instead of STATE["pressing"] (review
#: 20 Nov -- a device press must not hold every other step). Like `pressing`, reset() never clears it.
_DEVICE_PRESSING: set = set()

#: A chained line (`"chain": true`, owner 23 Nov): how often its watcher looks at the line before it...
CHAIN_POLL_S = 0.05
#: ...and how long at most: past event_cues' own bounds for one line (VOICE_WAIT_S 15 + LOCK_WAIT_S 45 +
#: READ_TURN_S 90, the line itself) -- a set-up still not over by then is not followed.
CHAIN_WAIT_S = 240.0
#: The watchers (strong references: a task nobody holds may be collected mid-wait).
_CHAIN_TASKS: set = set()
#: What a chained pair's steps say on the phone (`chain` in the snapshot).
CHAIN_LEAD_TH = "กดครั้งเดียว: บทถัดไปตามมาเองทันทีที่ Emma พูดบทนี้จบ ไม่ต้องกดซ้ำ (กดหยุดแดงระหว่างนี้ = ไม่เล่นต่อ)"
CHAIN_FOLLOW_TH = "ตามมาเองทันทีหลังบทก่อนหน้า ไม่ต้องกด — กดเองเฉพาะเมื่อไม่ตามมา"
CHAIN_WAITING_TH = "รอ Emma พูดบทก่อนหน้าจบ แล้วบทนี้ตามมาเอง — ไม่ต้องกด"
CHAIN_PRESSED_TH = "ตามมาเองหลังบทก่อนหน้าแล้ว"
CHAIN_MISSED_TH = "ไม่ได้ตามมาเอง: %s — กดบทนี้เองได้เลย"
#: ...but a set-up that failed or was cut is no set-up for this line (review 23 Nov): the SET-UP is
#: pressed again, and this line follows it by itself -- "ถัดไป" ("ตามลำดับ") still stands on this one
CHAIN_LEAD_FAILED_TH = ("ไม่ได้ตามมาเอง: บทก่อนหน้า (มุกเกริ่น) ไม่ได้พูดจบ — กดบทก่อนหน้าอีกครั้ง แล้วบทนี้จะตามมาเอง "
                        "(อย่ากดบทนี้เดี่ยวๆ: จะเป็นเฉลยที่ไม่มีมุกเกริ่น)")
#: ...and on the set-up itself, which "ถัดไป" stands on again (`_chain_redo`)
CHAIN_LEAD_AGAIN_TH = "บทนี้ไม่ได้พูดจบ — กดอีกครั้ง แล้วบทถัดไปจะตามมาเอง"
CHAIN_WHY_TH = {"stopped": "กดหยุดแล้ว", "lead_failed": "บทก่อนหน้าไม่ได้พูดจบ",
                "timeout": "บทก่อนหน้าไม่จบในเวลา", "error": "ผิดพลาด"}

ERRORS_TH = {
    "no_order": "บทพิธีไม่มีลำดับขั้น",
    "no_step": "ไม่พบขั้นนี้ในลำดับพิธี",
    "end": "จบลำดับพิธีแล้ว",
    "running": "Emma กำลังพูดอยู่ — รอให้จบก่อน",
    "moving": "หุ่นกำลังเดินหรือทำท่าอยู่ — รอให้จบก่อนแล้วค่อยกดบทพูด",
    "busy": "กำลังทำขั้นก่อนหน้าอยู่ — รอสักครู่",
    "stale": "ลำดับเปลี่ยนแล้ว — ดูหน้าจออีกครั้ง",
    "not_ready": "ขั้นนี้ยังทำไม่ได้",
    "bad_step": "ขั้นนี้ในไฟล์ผิด",
    "error": "ผิดพลาด — ขั้นนี้ยังไม่ได้ทำ",
    "checking": "กำลังตรวจบท…",
    "restarted": "เซิร์ฟเวอร์เริ่มใหม่ระหว่างขั้นนี้",
    "stopped": "กดหยุดแล้ว — ขั้นนี้ไม่ได้ทำ",
    "live": "ช่องตอบสดเปิดอยู่ — กด 'ถัดไป' เมื่อ Emma ตอบคุณมิกิจบ",
    "no_live": "เครื่องนี้ยังเปิดช่องตอบสดไม่ได้ (event_host.live_open)",
    "live_refused": "เปิดช่องตอบสดไม่ได้ — Emma จะไม่ตอบ",
    "not_ceremony_app": "เปิดแอป Emma Ceremony บนหุ่นก่อน",
    # the ceremony's opening (card 1, app/event_tour.py `begin_ceremony`) under way: it walks, waves, shakes hands
    # by itself -- a line, a walk or a gesture of the order now would land in the middle of it
    "opening": "การ์ดเปิดตัวกำลังทำต่อเนื่องอยู่ — รอให้จบ หรือกดหยุดสีแดงก่อน",
    "no_hand": "ขั้นนี้ไม่มีอะไรเหลือให้ทำเองแล้ว — ดูหน้าจออีกครั้ง",
    # the in-box tick (third review, 23 Nov): staff's word that she is in the box, never a reflex "ถัดไป"
    "in_box_tap": ("ขั้นยืนยันหุ่นอยู่ในกล่องกด 'ถัดไป' ไม่ได้ — เมื่อหุ่นอยู่ในกล่องจริง แตะปุ่มของขั้นนี้เองสองครั้ง"),
}

#: What a live step's mark says (event_host.live_open).
LIVE_RUNNING_TH = "Emma กำลังฟังคุณมิกิ"
LIVE_EXPIRED_TH = "ช่องตอบสดปิดเองแล้ว (ครบเวลา หรือเปลี่ยนฉาก) — ถ้าคุณมิกิยังถาม Emma จะไม่ตอบ"
LIVE_STOPPED_TH = "ปิดช่องตอบสดแล้ว (กดหยุด)"
#: The app's 60 s latch after a stop (B, the operator screen, the red stop's body stop): a
#: gesture or a walk pressed now is refused "latched" by the robot (review 7 Nov, A6).
LATCHED_TH = "เพิ่งกดหยุด — หุ่นล็อกแขนและการเดิน 60 วินาที รอสักครู่"
#: A screen step with no big screen set up on this machine (event_host.big_screen would fail).
SCREEN_UNSET_TH = "จอใหญ่ยังไม่ได้ตั้งค่า (ROOM_SCREENS / MIRROR_PORT)"
#: ...and one the room PC did not do (its own code after it, for whoever fixes the room).
SCREEN_FAILED_TH = {"open": "เปิดจอใหญ่ไม่สำเร็จ", "close": "ปิดจอใหญ่ไม่สำเร็จ"}


def reset() -> None:
    # `gen`: a press still in flight when the order is reset must not move the new
    # order's `last` when its path answers. `restored` False: the next use looks for
    # a saved order again (conftest; reset_order() says it already did).
    # `pressing` is NOT cleared (review 7 Nov): a press in flight (a walk's arm rest, a
    # line waiting for her voice) still holds the gate until it answers -- cleared, a
    # second press ran alongside it. conftest clears it itself between tests.
    # `chain` None: a watcher still waiting finds the order reset (`gen`) and presses nothing.
    STATE.update(marks={}, last=None, gen=int(STATE.get("gen") or 0) + 1,
                 restored=False, persisted=None, changed_at=None, seen=None, cleared=None, chain=None)
    _STATIC.update(at=None, key=None, value=None)


# ---- the steps --------------------------------------------------------------------------

def _scenes() -> list[dict] | None:
    scenes = event_script.order()
    return None if scenes is None else _visible(scenes)


def _visible(scenes: list[dict]) -> list[dict]:
    """The order without the device steps this machine does not drive (app/venue_devices.py `in_use`):
    VENUE_DEVICES off = every one of them gone, the order as it was before they were written in. A
    step keeps its file index `i` -- a press names (n, i) of the file."""
    from app import venue_devices
    from app.config import settings

    out = []
    for sc in scenes:
        # card 2 (`follow`, owner 24 Nov, app/ceremony_follow.py) is in the order only with CEREMONY_FOLLOW_HOST on:
        # off = the order exactly as it was (the device steps' rule)
        steps = [st for st in sc["steps"]
                 if (st["do"] != "device" or venue_devices.in_use(st.get("device")))
                 and (st["do"] != "follow" or settings.ceremony_follow_host)]
        out.append(sc if len(steps) == len(sc["steps"]) else {**sc, "steps": steps})
    return out


def _is_device(step: dict) -> bool:
    return step.get("do") == "device"


def _flat(scenes: list[dict]) -> list[tuple[int, int]]:
    return [(sc["n"], st["i"]) for sc in scenes if sc["n"] is not None for st in sc["steps"]]


def _find(scenes: list[dict], n, i) -> dict | None:
    for sc in scenes:
        if sc["n"] == n and sc["n"] is not None:
            for st in sc["steps"]:
                if st["i"] == i:
                    return st
    return None


def _after(scenes: list[dict], last) -> tuple[int, int] | None:
    """The step after `last` -- the first when nothing was taken yet, or when the
    file changed under a `last` it no longer has."""
    flat = _flat(scenes)
    if not flat:
        return None
    if last is None or tuple(last) not in flat:
        return flat[0]
    k = flat.index(tuple(last)) + 1
    return flat[k] if k < len(flat) else None


def _advance(scenes: list[dict], n, i) -> None:
    """`last` moves to (n, i) only forward -- never back over steps already taken."""
    flat = _flat(scenes)
    here = (n, i)
    last = STATE["last"]
    if (last is None or tuple(last) not in flat
            or (here in flat and flat.index(here) > flat.index(tuple(last)))):
        STATE["last"] = here


def _key(n, i) -> str:
    return "%s.%s" % (n, i)


def _cue_of(step: dict) -> dict | None:
    return event_script.cue(step.get("cue")) if step.get("do") == "cue" else None


def _ceremony_app() -> bool:
    """The Emma Ceremony app is on the robot (app/ceremony.py). Never raises: unknown = not on."""
    try:
        from app import ceremony

        return bool(ceremony.on())
    except Exception:                                    # noqa: BLE001
        return False


def _tick(step: dict) -> bool:
    """A press that runs nothing: a manual step. (A cue with only a live brief was one
    until 7 Nov; it opens her live window now -- `_is_live`.)"""
    return step.get("do") == "manual"


def _robot_moving() -> bool:
    """A walk or a gesture under way (event_host.robot_moving). Never raises: unknown = still."""
    try:
        from app import event_host

        probe = getattr(event_host, "robot_moving", None)
        return bool(callable(probe) and probe())
    except Exception:                                    # noqa: BLE001
        return False


def _is_live(step: dict) -> bool:
    """A cue with no line, only a brief for her live answer (s4_recommend)."""
    c = _cue_of(step)
    return c is not None and not c.get("th") and bool(c.get("live_th"))


def _live_api(name: str):
    """event_host.live_open / live_close / live_state, or None on a server without them."""
    from app import event_host

    fn = getattr(event_host, name, None)
    return fn if callable(fn) else None


def _live_state() -> dict | None:
    fn = _live_api("live_state")
    try:
        state = fn() if fn is not None else None
    except Exception:                                    # noqa: BLE001 -- unreadable = closed
        logger.exception("event order: the live window")
        return None
    return state if isinstance(state, dict) else None


def _live_close(why: str) -> bool:
    fn = _live_api("live_close")
    if fn is None:
        return False
    try:
        fn(why)
        return True
    except Exception:                                    # noqa: BLE001 -- the mark still ends
        logger.exception("event order: closing the live window")
        return False


def _live_mark() -> tuple[str, dict] | None:
    """The live step whose window is open (one at a time: opening another is refused)."""
    for key, mark in STATE["marks"].items():
        if mark.get("state") == "running" and mark.get("live"):
            return key, mark
    return None


def _opening_running() -> bool:
    """The ceremony's opening (app/event_tour.py) is under way. Never raises: unknown = not."""
    try:
        from app import event_tour

        return bool(event_tour.ceremony_running())
    except Exception:                                    # noqa: BLE001
        return False


def _app_latched() -> bool:
    from app import event_walk

    return bool(event_walk._app_latched())


def _stop_gen() -> tuple:
    """Every count a stop moves: the red stops here (`staff_stopped`), event_cues' cuts
    (`cut_gen`, when it has one), people's walk stops (event_walk.stops)."""
    from app import event_cues, event_walk

    gen = getattr(event_cues, "cut_gen", None)
    try:
        cut = gen() if callable(gen) else None
    except Exception:                                    # noqa: BLE001
        cut = None
    return int(STATE.get("stops") or 0), cut, event_walk.stops()


def staff_stopped() -> bool:
    """The red stop (app/event_control_server.py), synchronously before anything it awaits:
    a press still reading its readiness runs nothing (`_run` compares `_stop_gen`), and an
    open live window closes -- its step failed "stopped". True = a live window was open."""
    STATE["stops"] = int(STATE.get("stops") or 0) + 1
    live = _live_mark()
    if live is None:
        return False
    key, mark = live
    _live_close("staff")
    mark.update(state="failed", error="stopped", error_th=LIVE_STOPPED_TH)
    scenes = _scenes()
    if scenes is not None:
        _persist(scenes)
    turnlog.record("event_order", n=mark["at_step"][0], i=mark["at_step"][1], do="cue", ok=False,
                   error="stopped", via="stop")
    return True


# ---- kept across a restart -------------------------------------------------------------------

def state_path() -> Path | None:
    """Next to the walk's run file (data/events/.run/, gitignored). conftest points
    this at nothing: a test must never write the robot's real copy."""
    from app import event_walk

    return event_walk.RUN_DIR / "order.json"


def _digest(scenes: list[dict]) -> str:
    return hashlib.sha256(json.dumps(scenes, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _saved_marks() -> dict:
    return {key: {"state": m["state"], "at": m["at"], "at_step": list(m["at_step"]),
                  "error": m.get("error"), "error_th": m.get("error_th"),
                  # a device step's word on how far it got (venue_devices `hardware`): only on those
                  **({"hardware": m["hardware"]} if m.get("hardware") else {})}
            for key, m in STATE["marks"].items()}


def _persist(scenes: list[dict], *, force: bool = False) -> None:
    """Write `last` + the marks when they changed (atomically: tmp then replace). An
    order nobody has touched writes nothing; `force` (reset) writes even that, over an
    older copy."""
    body = {"last": list(STATE["last"]) if STATE["last"] else None, "marks": _saved_marks()}
    sig = json.dumps(body, ensure_ascii=False, sort_keys=True)
    if sig != STATE.get("seen"):
        # the time of the last thing staff did, kept in memory too (the age rule, `_expire_old`)
        STATE["seen"] = sig
        STATE["changed_at"] = time.monotonic() if (body["last"] or body["marks"]) else None
    path = state_path()
    if path is None:
        return
    if sig == STATE["persisted"] or (not force and STATE["persisted"] is None
                                     and body["last"] is None and not body["marks"]):
        return
    doc = {"about": "the ceremony's running order on the staff phone (app/event_order.py) -- "
                    "where it got to, so a restart does not start it again",
           "digest": _digest(scenes), "saved_at": time.time(), **body}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        STATE["persisted"] = sig
    except OSError:
        logger.exception("event order: could not keep where the order got to")


def _restore_once(scenes: list[dict]) -> None:
    """The first use after a start: where the order got to, if it is this order and
    from this ceremony."""
    if STATE["restored"]:
        return
    STATE["restored"] = True
    path = state_path()
    if path is None:
        return
    from app.config import settings

    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except (OSError, ValueError):
        logger.exception("event order: the kept order could not be read")
        return
    if not isinstance(doc, dict) or doc.get("digest") != _digest(scenes):
        logger.info("event order: the kept order is for another order -- starting at the first step")
        return
    saved = doc.get("saved_at")
    now = time.time()
    if not isinstance(saved, (int, float)) or isinstance(saved, bool) or saved > now + 60:
        # a clock nobody trusts: stamped ahead of now = written by a clock that was ahead, or read
        # by one still behind (a boot before the network set it) -- its age came out negative,
        # "fresh" for as long as the gap, and a rehearsal's order handed to the ceremony (review
        # 7 Nov, A7)
        logger.info("event order: the kept order is dated ahead of this clock -- starting at the first step")
        return
    if now - saved > settings.event_ceremony_max_s:
        if doc.get("last") or doc.get("marks"):
            STATE["cleared"] = {"at": now, "from": float(saved)}     # the page says it was cleared
        return                                   # yesterday's ceremony
    flat = _flat(scenes)
    last = doc.get("last")
    if isinstance(last, list) and len(last) == 2 and tuple(last) in flat:
        STATE["last"] = tuple(last)
    for key, raw in (doc.get("marks") or {}).items():
        if not isinstance(raw, dict) or not isinstance(raw.get("at_step"), list):
            continue
        at_step = tuple(raw["at_step"])
        if at_step not in flat or raw.get("state") not in ("running", "done", "failed", "skipped"):
            continue
        mark = {"state": raw["state"], "at": float(raw.get("at") or 0), "since": 0.0, "at_step": at_step,
                "error": raw.get("error"), "error_th": raw.get("error_th")}
        if raw.get("hardware") in ("ok", "sent", "mock", "failed", "unknown", "manual"):
            mark["hardware"] = raw["hardware"]
        if mark["state"] == "running":
            # nothing is watching it any more: the line, the walk -- whatever it was
            mark.update(state="failed", error="restarted", error_th=ERRORS_TH["restarted"])
        STATE["marks"][key] = mark
    turnlog.record("event_order", n=STATE["last"][0] if STATE["last"] else None,
                   i=STATE["last"][1] if STATE["last"] else None, do=None, ok=True, error=None, via="restore")
    # Not written back now: `saved_at` is the time of the last thing staff did, and a
    # server restarting every hour must not keep yesterday's order alive. The next
    # change writes (the failed marks with it).
    STATE["persisted"] = STATE["seen"] = json.dumps({"last": list(STATE["last"]) if STATE["last"] else None,
                                                     "marks": _saved_marks()}, ensure_ascii=False, sort_keys=True)
    STATE["changed_at"] = time.monotonic() - max(0.0, now - float(saved))


def _expire_old(scenes: list[dict]) -> None:
    """The age rule without a restart (review 7 Nov, B8): the order last touched longer
    ago than EVENT_CEREMONY_MAX_S is a rehearsal's -- cleared, so the real ceremony does
    not start at "ถัดไป: ฉาก 6". Never while a step is running. The page says so."""
    from app.config import settings

    at = STATE.get("changed_at")                     # monotonic: NTP setting the clock after a boot
    if at is None or not (STATE["marks"] or STATE["last"]):   # must not look like hours gone by
        return
    age = time.monotonic() - at
    if age <= settings.event_ceremony_max_s:
        return
    if any(m.get("state") == "running" for m in STATE["marks"].values()):
        return
    reset()
    STATE["restored"] = True
    now = time.time()
    STATE["cleared"] = {"at": now, "from": now - age}
    _persist(scenes, force=True)
    turnlog.record("event_order", n=None, i=None, do=None, ok=True, error=None, via="expired")


# ---- what each mark became -----------------------------------------------------------------

def _cue_runs() -> list[dict]:
    from app import event_cues

    runs = event_cues.STATE.get("runs")
    return list(runs.values()) if isinstance(runs, dict) else []


def _cue_last(cue_id: str) -> dict | None:
    from app import event_cues

    last = event_cues.STATE.get("last")
    entry = last.get(cue_id) if isinstance(last, dict) else None
    return entry if isinstance(entry, dict) else None


def _audible() -> bool:
    """Her voice of a ceremony line still to be heard (event_cues.line_audible): a read
    line's turn is over for the model at its turn_complete, ~20 s before the room has
    heard it (review 7 Nov, A3). A server without the probe = not audible."""
    from app import event_cues

    probe = getattr(event_cues, "line_audible", None)
    try:
        return bool(probe()) if callable(probe) else False
    except Exception:                                    # noqa: BLE001
        return False


#: The big screen's word on a film (app/event_clips.py STATE["phase"]).
_CLIP_GOING = ("opening", "asked")


def _settle_clip(mark: dict, *, warn_only: bool) -> None:
    """A film the step started, from the big screen's own word: running until it plays,
    done then; failed (or, for a line's film, done with a warning) when it did not.
    `clip_seq` = event_clips' seq of that film: moved = stopped or another film."""
    from app import event_clips

    st = event_clips.STATE
    phase, err = st.get("phase"), None
    if st.get("seq") != mark.get("clip_seq"):
        err = "คลิปถูกหยุดก่อนเริ่มเล่น" if phase == "stopped" else "มีคลิปอื่นเปิดมาแทน"
    elif phase in ("playing", "ended"):
        mark["state"] = "done"
        mark.pop("clip_seq", None)
        return
    elif phase in _CLIP_GOING:
        return                                           # the screen has not said yet
    elif phase == "failed":
        err = event_clips.ERRORS_TH.get(st.get("error") or "", event_clips.ERRORS_TH["error"])
    else:
        err = "คลิปไม่ได้เล่น"
    mark.pop("clip_seq", None)
    if warn_only:
        mark.update(state="done", error_th="วิดีโอไม่ขึ้น: " + err)
    else:
        mark.update(state="failed", error="clip", error_th=err)


def _settle(scenes: list[dict]) -> None:
    """A running mark becomes done/failed once its path says so (lazily, here)."""
    from app import event_clips, event_cues, event_walk

    for key, mark in STATE["marks"].items():
        if mark.get("state") != "running":
            continue
        if mark.get("live"):
            state = _live_state()
            if state is None or state.get("cue") != mark["live"]:
                # closed by itself (its own limit) -- or by something else: she no longer listens
                mark.update(state="done", error_th=LIVE_EXPIRED_TH)
                _advance(scenes, *mark["at_step"])
        elif mark.get("cue"):
            if mark.get("clip_seq") is not None:
                _settle_clip(mark, warn_only=True)       # the line is said; its film is going up
                continue
            if (mark.get("pending") or any(r.get("id") == mark["cue"] for r in _cue_runs())
                    or _audible()):
                continue
            last = _cue_last(mark["cue"])
            if last is not None and float(last.get("at") or 0) >= mark["at"] - 1.0:
                if last.get("ok"):
                    mark["state"] = "done"
                    clip = last.get("clip")
                    if isinstance(clip, dict):
                        # the line's film (s8_story_video), from the cue's own row (B4)
                        film = clip.get("id") or (event_script.cue(mark["cue"]) or {}).get("clip")
                        if not clip.get("ok"):
                            mark["error_th"] = "วิดีโอไม่ขึ้น: %s" % (clip.get("error_th") or clip.get("error")
                                                                      or ERRORS_TH["error"])
                        elif film and event_clips.STATE.get("clip") != film:
                            mark["error_th"] = "วิดีโอไม่ขึ้น: มีคลิปอื่นเปิดมาแทน"
                        else:
                            # cued: running until the big screen says it plays (or failed)
                            mark.update(state="running", clip_seq=event_clips.STATE.get("seq"))
                            _settle_clip(mark, warn_only=True)
                else:
                    mark.update(state="failed", error=last.get("error"),
                                error_th=event_cues.ERRORS_TH.get(last.get("error") or "", ERRORS_TH["error"]))
            else:
                mark["state"] = "done"      # it was accepted and nothing is saying it any more
        elif mark.get("clip_seq") is not None:
            _settle_clip(mark, warn_only=False)
        elif mark.get("tour") is not None:
            # the ceremony's opening (app/event_tour.py): running until its card ends, done when it went as planned
            # (the whole round, or all the recorded spots allowed -- its note says which), failed where it stopped
            from app import event_tour

            result = event_tour.ceremony_result(mark["tour"])
            if result is not None:
                ok, note = result
                if ok:
                    mark.update(state="done", error_th=note)
                else:
                    mark.update(state="failed", error="opening", error_th=note or ERRORS_TH["error"])
        elif mark.get("run"):
            run = event_walk.current()
            if run is None or run.id != mark["run"]:
                mark.update(state="failed", error="replaced", error_th="มีการเดินอื่นมาแทน")
            elif run.state == "arrived":
                mark["state"] = "done"
            elif run.state in ("stopped", "failed", "interrupted"):
                mark.update(state="failed", error=run.reason, error_th=run.view().get("reason_th"))
    _persist(scenes)


def _running(scenes: list[dict]) -> tuple[int, int] | None:
    """The step of the order still running, the latest pressed first."""
    found = None
    for key, mark in STATE["marks"].items():
        if mark.get("state") == "running" and (found is None or mark["since"] >= found[1]["since"]):
            found = (key, mark)
    return None if found is None else tuple(found[1]["at_step"])


def _line_busy(*, tail: bool = True) -> bool:
    """A ceremony line is being said or waiting to be -- one of the order's, or one
    pressed on /ceremony/cue (event_cues' own runs) -- or still being HEARD: her voice of a
    read line plays on ~20 s past the turn the model ended (review 7 Nov, A3).

    `tail=False` is a chained line's press alone (`_chain_watch`): the line before it is over on
    the server, its mark done, and only its last seconds still play -- event_cues itself waits them
    out before the next line's first word (`_until_quiet`: the page's own word on her voice for a
    recording; the tail with its slack for a reading). Everything else still counts: a running
    line of the order, any event_cues run, and a press still in its first awaits (`entering`)."""
    if any(m.get("state") == "running" and m.get("cue") and m.get("clip_seq") is None
           for m in STATE["marks"].values()):
        return True
    if _cue_runs():
        return True
    if not tail:
        from app import event_cues

        return bool(event_cues.STATE.get("entering"))
    return _audible()


# ---- readiness: the real gates, asked without pressing ---------------------------------------

def _clip_why(clip_id: str | None) -> str | None:
    from app import event_clips, room_screens

    clip = event_clips.table().get(clip_id or "")
    if clip is None:
        return event_clips.ERRORS_TH["no_clip"]
    if not clip["has_file"]:
        return event_clips.ERRORS_TH["no_file"]
    if not room_screens.offered():
        return event_clips.ERRORS_TH["no_screen"]
    return None


def _cut(text: str) -> str:
    """The head of a line for a button: LABEL_CHARS, never splitting a vowel or a tone
    mark from its letter."""
    if len(text) <= LABEL_CHARS:
        return text
    end = LABEL_CHARS
    while end < len(text) and unicodedata.category(text[end]) == "Mn":
        end += 1
    return text[:end].rstrip() + "…"


#: A cue's content problems the staff can read, the rest by code.
_CUE_PROBLEMS_TH = {
    "empty": "ยังไม่มีบท (รอเนื้อหา)",
    "clip_missing": "ยังไม่มีไฟล์วิดีโอของบทนี้ (data/events/clips/)",
    "clip_unknown": "ไม่พบวิดีโอของบทนี้ในไฟล์งาน",
}


def _cue_view(step: dict, ctx: dict) -> dict:
    """A cue step from event_cues' own row for it -- its content checks were made in a
    thread and cached there (`event_cues.snapshot`), and nothing here repeats them: no
    row yet = "กำลังตรวจบท…", never a check on the loop."""
    from app import event_cues

    cue_id = step["cue"]
    c = ctx["cues"].get(cue_id)
    row = ctx["cue_rows"].get(cue_id)
    out = {"line_th": None, "voice": None, "ready": False, "why_th": None, "show_th": None,
           "label": "Emma: %s" % cue_id}
    if c is None:
        out["why_th"] = ERRORS_TH["bad_step"] + ": ไม่พบบท %s" % cue_id
        return out
    if c.get("th"):
        out["line_th"] = c["th"]
        if (row or {}).get("speak") == "en" and c.get("en"):
            out["line_en"] = c["en"]             # what she SAYS; line_th is the screen's subtitle
        out["label"] = "Emma: «%s»" % _cut(c["th"])
        recorded = bool(row and row.get("recorded") and row.get("voice_approved"))
        out["voice"] = "recorded" if recorded else "read"
    elif c.get("live_th"):
        # her live window (event_host.live_open): the brief reaches the model now, so it is
        # approved and checked like a line, and she answers on the robot face's call
        out.update(voice="live", label="Emma ตอบสด: %s" % cue_id)
    else:
        out["label"] = "Emma: %s (ยังไม่มีบท)" % cue_id
    live = bool(not c.get("th") and c.get("live_th"))
    why = None
    if live and _live_api("live_open") is None:
        why = ERRORS_TH["no_live"]
    elif row is None:
        why = ERRORS_TH["checking"]
    else:
        problems = list(row.get("problems") or [])
        if problems:
            named = [_CUE_PROBLEMS_TH[p] for p in problems if p in _CUE_PROBLEMS_TH]
            why = " · ".join(named) if named else "%s (%s)" % (event_cues.ERRORS_TH["problems"],
                                                              ", ".join(problems[:3]))
        elif not c.get("th") and not live:
            why = _CUE_PROBLEMS_TH["empty"]
        elif not row.get("approved"):
            why = event_cues.ERRORS_TH["not_approved"]
        elif not ctx["call"]:
            why = event_cues.ERRORS_TH["no_call"]
        elif ctx["quiet"]:
            why = event_cues.ERRORS_TH["quiet"]          # what event_cues.play would answer
        elif c.get("clip") and not ctx["screens"]:
            # the line's film (s8_story_video) goes to the big screen after it: no screen = the
            # line is said and the film never comes (review 7 Nov, B4) -- the file is checked
            # by the cue's own problems (clip_missing)
            from app import event_clips

            why = "วิดีโอหลังบท: " + event_clips.ERRORS_TH["no_screen"]
    out["ready"] = why is None
    out["why_th"] = why
    show = step.get("show")
    if show:
        out["show_th"] = "ภาพ: %s" % ctx["story_names"].get(show, show)
        warn = None
        if ctx["stories"] is None:
            warn = "ภาพไม่ขึ้น: เรื่องเล่าปิดอยู่บนเครื่องนี้ (STORIES_ENABLED)"
        elif show not in ctx["stories"]:
            warn = "ภาพไม่ขึ้น: เรื่อง %s ยังไม่พร้อมหรือยังไม่อนุมัติ" % show
        if warn:
            out["why_th"] = warn if why is None else why + " · " + warn
    return out


def _view(step: dict, ctx: dict) -> dict:
    """{ready, why_th, label, line_th, show_th, voice} for one step."""
    from app import event_walk, robot_body
    from app import robot_contracts as rc

    do = step["do"]
    out = {"ready": True, "why_th": None, "label": None, "line_th": None, "show_th": None, "voice": None}
    doc = ctx["event"]
    if do is None:
        out.update(ready=False, why_th=ERRORS_TH["bad_step"] + " (do)", label="?")
    elif do == "screen":
        want = step.get("screen")
        out["label"] = {"open": "เปิดจอใหญ่", "close": "ปิดจอใหญ่"}.get(want or "", "จอใหญ่: %s" % want)
        if want not in ("open", "close"):
            out.update(ready=False, why_th=ERRORS_TH["bad_step"] + ": จอ %s" % want)
        elif not ctx["screens"]:
            out.update(ready=False, why_th=SCREEN_UNSET_TH)
    elif do == "handoff":
        out["label"] = "เปิดแอป Emma Event" if step.get("to") == "event" else "เปิดแอป %s" % step.get("to")
        if step.get("to") != "event":
            out.update(ready=False, why_th=ERRORS_TH["bad_step"] + ": แอป %s" % step.get("to"))
    elif do == "walk":
        from app import event_host
        from app.config import settings

        spot = (doc.get("spots") or {}).get(step["spot"])
        out["label"] = "เดินไป%s" % (spot["name_th"] if spot else step["spot"])
        why = []
        if spot is None:
            why.append(ERRORS_TH["bad_step"] + ": ไม่พบจุด %s" % step["spot"])
        if not settings.event_walk_enabled:
            why.append(event_walk.REASONS_TH["disabled"])
        if spot is not None and event_host.spot_steps(spot) is None:
            why.append(event_walk.REASONS_TH["no_steps"] + " — บันทึกที่หน้างานด้วยปุ่ม 'บันทึกตำแหน่งตอนนี้'")
        from app import event_box

        if event_box.held():
            why.append(event_walk.REASONS_TH["in_box"])  # event_walk.start refuses it (third review, 23 Nov)
        if not why and ctx["latched"]:
            why.append(LATCHED_TH)                       # the app refuses it for 60 s (review 7 Nov, A6)
        if why:
            out.update(ready=False, why_th=" · ".join(why))
    elif do == "body":
        out["label"] = "ท่า: %s" % rc.BODY_LABELS.get(step["action"] or "", step["action"])
        why = robot_body.staff_ready(step["action"] or "")
        if why:
            out.update(ready=False, why_th=robot_body.staff_reason_th(why))
        elif ctx["latched"]:
            # after a red stop the robot answers "latched" for 60 s: it showed ready and failed
            # when pressed (review 7 Nov, A6). robot_arm.armed() is the server's own latch, not this
            out.update(ready=False, why_th=LATCHED_TH)
    elif do == "cue":
        cue = _cue_view(step, ctx)
        out.update(ready=cue["ready"], why_th=cue["why_th"], label=cue["label"], line_th=cue["line_th"],
                   show_th=cue["show_th"], voice=cue["voice"])
        if cue.get("line_en"):
            out["line_en"] = cue["line_en"]
    elif do == "clip":
        clip = (doc.get("clips") or {}).get(step["clip"] or "")
        out["label"] = "คลิป: %s" % (clip["title_th"] if clip else step["clip"])
        why = _clip_why(step["clip"])
        if why:
            out.update(ready=False, why_th=why)
    elif do == "device":
        from app import venue_devices

        out["label"] = venue_devices.step_label(step.get("device"), step.get("action"))
        why = venue_devices.why_not(step.get("device"), step.get("action"))
        if why:
            out.update(ready=False, why_th=why)
        else:
            # pressable, and still worth a word: a rehearsal's dry run, the last command that failed,
            # the morning check that could not reach it (files and memory only: never the network)
            out["why_th"] = venue_devices.warning(step.get("device"))
    elif do == "follow":
        # card 2: the whole ceremony live (app/ceremony_follow.py) -- its own gates (cue-only call, a host file)
        from app import ceremony_follow

        out["label"] = "คุยสดกับคุณมิกิ (การ์ด 2)"
        why = ceremony_follow.start_refusal()
        if why:
            out.update(ready=False, why_th=why)
    elif do == "manual":
        out["label"] = "ทำเอง"
        if step.get("in_box"):
            from app import event_host

            # refused now (she walks, the opening runs, she is partly out): said before the press, not after
            why = event_host.box_arm_refusal()
            if why is not None:
                out.update(ready=False, why_th=why["error_th"])
    elif do == "opening":
        # card 1 as the ceremony's first step (owner 23 Nov, app/event_tour.py): ready on its own gates; what it
        # will do, what it will not, and its last press's progress ride with the step (`opening`)
        from app import event_tour

        out["label"] = "เปิดตัว (การ์ด 1)"
        opening = event_tour.ceremony_view(step.get("scene"), ctx.get("opening"),
                                           without_screen=bool(ctx.get("without_screen")))
        out["opening"] = opening
        why = [opening["why_th"]] if opening["why_th"] else []
        if not why and ctx["latched"]:
            why.append(LATCHED_TH)
        if ctx["latched"] or not ctx.get("ceremony_app"):
            out["opening"] = {**opening, "ready_without_screen": False}
        if why:
            out.update(ready=False, why_th=" · ".join(why))
        elif opening["warn_th"]:
            out["why_th"] = " · ".join(opening["warn_th"])  # pressable: a warning, at full strength on the page
    if do not in ("manual", "device", "opening") and ctx.get("opening_running"):
        # the opening walks, waves and shakes hands by itself: nothing else of the order goes in the middle of it
        out.update(ready=False, why_th=ERRORS_TH["opening"])
    if do != "manual" and not ctx.get("ceremony_app"):
        # every press but a tick is the ceremony app's (owner 9 Nov, "ไม่งั้นจะปนกัน"): said first,
        # it is what staff fix first -- the step's own reasons after it
        why = ERRORS_TH["not_ceremony_app"]
        out.update(ready=False, why_th=why + (" · " + out["why_th"] if out["why_th"] else ""))
    return out


def _static_now() -> dict:
    """In a thread: the stories catalog (every asset's stat, ~7 ms a call measured)."""
    from app import stories
    from app.config import settings

    offered = None
    names: dict[str, str] = {}
    try:
        catalog = stories.load_catalog()
        for story in (catalog or {}).get("stories") or []:
            if isinstance(story, dict) and story.get("id"):
                asks = story.get("questions_th") or []
                names[story["id"]] = ("%s (%s)" % (story["id"], asks[0])) if asks else story["id"]
        if settings.stories_enabled:
            offered = set(stories.available(catalog))
    except Exception:                                    # noqa: BLE001
        logger.exception("event order: the stories")
    return {"stories": offered, "story_names": names}


async def _static() -> dict:
    from app.config import settings

    now = time.monotonic()
    key = bool(settings.stories_enabled)
    if _STATIC["value"] is None or _STATIC["key"] != key or now - _STATIC["at"] >= STATIC_TTL_S:
        value = await asyncio.to_thread(_static_now)
        _STATIC.update(at=now, key=key, value=value)
    return _STATIC["value"]


def _quiet() -> bool:
    from app import event_cues

    probe = getattr(event_cues, "_quiet", None)
    try:
        return bool(probe()) if callable(probe) else False
    except Exception:                                    # noqa: BLE001 -- a probe that fails is "not quiet"
        return False


async def _context() -> dict:
    """What every step's readiness reads, once per snapshot. The two costly sources run
    in threads and are cached on their own (the cue rows in event_cues, the stories
    here); the rest is stat-cached files and module state."""
    from app import event_cues, event_host, room_screens

    try:
        cue_snap = await event_cues.snapshot()
    except Exception:                                    # noqa: BLE001 -- the order still shows
        logger.exception("event order: the cue rows")
        cue_snap = {"cues": [], "call": event_cues._live_call() is not None}
    static = await _static()
    script = event_script.load()
    opening = None
    # (this module's own `next` is the phone's "ถัดไป": no builtin next() here)
    scene_id = None
    for sc in _scenes() or []:
        for st in sc["steps"]:
            if st.get("do") == "opening" and scene_id is None:
                scene_id = st.get("scene")
    if scene_id is not None:
        from app import event_tour

        try:
            opening = await event_tour.ceremony_context(scene_id)   # the recorded line, the screen: in a thread
        except Exception:                                # noqa: BLE001 -- the order still shows
            logger.exception("event order: the opening's readiness")
    from app import event_tour

    return {"opening": opening, "opening_running": event_tour.ceremony_running(), "event": event_host.load() or {},
            "cues": {c["id"]: c for c in event_script.cues(script)},
            "cue_rows": {r["id"]: r for r in cue_snap.get("cues") or [] if isinstance(r, dict)},
            "call": bool(cue_snap.get("call")), "quiet": _quiet(),
            "stories": static["stories"], "story_names": static["story_names"],
            "screens": room_screens.offered(), "latched": _app_latched(),
            "ceremony_app": _ceremony_app()}


async def snapshot() -> dict | None:
    """The `order` object of GET /ceremony/state; None = the script has no order."""
    scenes = _scenes()
    if scenes is None:
        return None
    _restore_once(scenes)
    _expire_old(scenes)
    _settle(scenes)
    ctx = await _context()
    chains = _chain_views(scenes)
    box_of = _box_of_m(scenes)
    out = []
    for sc in scenes:
        if sc["n"] is None:
            continue
        steps = []
        prev = None
        for st in sc["steps"]:
            try:
                view = _view(st, ctx)
            except Exception:                            # noqa: BLE001 -- one bad step, not a dead page
                logger.exception("event order: step %s.%s", sc["n"], st["i"])
                view = {"ready": False, "why_th": ERRORS_TH["error"], "label": None,
                        "line_th": None, "show_th": None, "voice": None}
            mark = STATE["marks"].get(_key(sc["n"], st["i"]))
            if mark is not None and mark.get("state") == "running" and mark.get("live"):
                # her window is open: closing it is always possible (a call dropped mid-answer
                # must not leave a step nobody can end)
                view = {**view, "ready": True, "why_th": None}
            # a screen step says which way (the page's "เปิดจอใหญ่" / "ปิดจอใหญ่" hint)
            screen = {"screen": st.get("screen")} if st["do"] == "screen" else {}
            note_th = st["note_th"]
            if st["do"] == "manual" and prev is not None and _is_device(prev):
                # the tick that confirms a device step in use (a device step is in `scenes` only then):
                # the file's note says "หุ่นไม่ได้คุมไฟ" -- now it does, and the tick is still the proof
                from app import venue_devices

                note_th = venue_devices.confirm_note_th(prev.get("device"), prev.get("action"))
            box = {}
            if st["do"] == "manual" and st.get("in_box"):
                # what the tick does, with the walk it arms (third review, 23 Nov: ONE walk out of X m)
                from app import event_box

                box = {"in_box": True, "box_th": event_box.tick_th(box_of)}
                note_th = (note_th + " · " if note_th else "") + box["box_th"]
            prev = st
            # a step of a chained pair (the joke's set-up, its punchline) says so -- only those
            chain = {"chain": chains[(sc["n"], st["i"])]} if (sc["n"], st["i"]) in chains else {}
            steps.append({"i": st["i"], "do": st["do"] or "manual", **screen, **chain, **box,
                          "label_th": st["label_th"] or view["label"] or "?",
                          "note_th": note_th, "line_th": view["line_th"], "show_th": view["show_th"],
                          **({"line_en": view["line_en"]} if view.get("line_en") else {}),
                          "voice": view["voice"], "ready": bool(view["ready"]), "why_th": view["why_th"],
                          # the ceremony's opening: what one press does, its screen, its last press's progress
                          **({"opening": view["opening"]} if view.get("opening") else {}),
                          "mark": None if mark is None else {"state": mark["state"],
                                                             "error_th": mark.get("error_th"),
                                                             "at": mark["at"],
                                                             "live": bool(mark.get("live")),
                                                             # running on the big screen's word: a
                                                             # clip step, or a line's film after it
                                                             "film": mark.get("clip_seq") is not None,
                                                             # a device step: how far it got
                                                             **({"hardware": mark["hardware"]}
                                                                if mark.get("hardware") else {})}})
        out.append({"n": sc["n"], "title_th": sc["title_th"], "steps": steps})
    last = STATE["last"]
    nxt = _target(scenes)
    running = _running(scenes)
    return {"scenes": out, "last": list(last) if last else None,
            "next": list(nxt) if nxt else None, "running": list(running) if running else None,
            # the end of the order is said, not just a button gone (review 7 Nov, B8)
            "finished": nxt is None and last is not None,
            "live": _live_view(), "cleared_th": _cleared_th()}


def _box_of_m(scenes: list[dict]) -> float | None:
    """The opening's walk out of the box, in metres (its scene's forward_m) -- what the in-box tick arms."""
    from app import event_host

    for sc in scenes:
        for st in sc["steps"]:
            if st.get("do") == "opening":
                scene = event_host.scene(st["scene"]) if st.get("scene") else None
                return (scene or {}).get("forward_m")
    return None


def _target(scenes: list[dict]) -> tuple[int, int] | None:
    """What "ถัดไป" does now: close her open live window, else the step after `last` -- but a joke's
    set-up that was not said whole while "ถัดไป" would stand on its punchline: the set-up again
    (`_chain_redo`; its punchline then follows by itself)."""
    live = _live_mark()
    if live is not None:
        return tuple(live[1]["at_step"])
    after = _after(scenes, STATE["last"])
    redo = _chain_redo()
    if redo is not None and after == redo[1]:
        return redo[0]
    return after


def _chain_redo() -> tuple[tuple[int, int], tuple[int, int]] | None:
    """(set-up, punchline) of a chain whose set-up was not said whole (review 23 Nov: "ถัดไป" ("ตามลำดับ")
    stood on the punchline -- one tap, a punchline with no set-up), while nothing has happened to the
    pair since: the set-up's mark is still the one that failed, the punchline not pressed nor skipped
    after it. A set-up pressed again or skipped retires the chain (`_run`, `skip`)."""
    chain = STATE.get("chain")
    if not chain or chain.get("state") != "missed" or chain.get("why") != "lead_failed":
        return None
    lead, follow, mark = chain["lead"], chain["follow"], chain.get("mark")
    if mark is None or STATE["marks"].get(_key(*lead)) is not mark:
        return None
    taken = STATE["marks"].get(_key(*follow))
    if taken is not None and taken.get("since", 0.0) >= mark.get("since", 0.0):
        return None
    return lead, follow


def _live_view() -> dict | None:
    """The open live window for the page: its step and when it closes by itself (`until`,
    event_host's wall clock -- the page counts down against GET /ceremony/state's
    `server_time`: a value that changed every second would rebuild the order every poll)."""
    live = _live_mark()
    if live is None:
        return None
    until = (_live_state() or {}).get("until")
    ok = isinstance(until, (int, float)) and not isinstance(until, bool)
    return {"n": live[1]["at_step"][0], "i": live[1]["at_step"][1], "until": until if ok else None}


def _cleared_th() -> str | None:
    """Until the next press: the old progress the age rule cleared (staff who rehearsed in
    the morning see why the order is back at step 1)."""
    from app.config import settings

    cleared = STATE.get("cleared")
    if not cleared:
        return None
    limit = max(1, int(round(settings.event_ceremony_max_s / 3600.0)))
    when = ("เมื่อ %s" % time.strftime("%H:%M", time.localtime(cleared["from"]))
            if cleared["at"] - cleared["from"] < 20 * 3600 else "เมื่อวานหรือก่อนนั้น")
    return "ล้างความคืบหน้าเก่าแล้ว (กดครั้งล่าสุด%s — เก่าเกิน %d ชั่วโมง) เริ่มจากขั้นแรก" % (when, limit)


# ---- the press ---------------------------------------------------------------------------

def _refused(error: str, n=None, i=None, why_th: str | None = None) -> dict:
    out = {"ok": False, "error": error, "error_th": why_th or ERRORS_TH.get(error, ERRORS_TH["error"])}
    if n is not None:
        out.update(n=n, i=i)
    return out


def _log(n, i, do, ok: bool, error: str | None, via: str) -> None:
    turnlog.record("event_order", n=n, i=i, do=do, ok=ok, error=error, via=via)


async def _play_cue(cue_id: str, show: str | None) -> dict:
    """event_cues.play, with `show` only when the step has one -- and only when this
    event_cues takes it (it is being added alongside; without it, the line alone)."""
    from app import event_cues

    if show:
        try:
            takes_show = "show" in inspect.signature(event_cues.play).parameters
        except (TypeError, ValueError):
            takes_show = False
        if takes_show:
            return await event_cues.play(cue_id, by="staff", show=show)
        result = await event_cues.play(cue_id, by="staff")
        return {**result, "show": {"ok": False, "error": "unsupported",
                                   "error_th": "เครื่องนี้ยังขึ้นภาพพร้อมบทไม่ได้"}}
    return await event_cues.play(cue_id, by="staff")


async def _dispatch(step: dict, mark: dict, *, press: dict | None = None) -> dict:
    """Run one step through its own path. The result's `ok` = that path accepted it. `press`: this press of the
    order -- {"gen", "key", "stops", "without_screen"} -- for the step that runs on after its answer (the opening)."""
    from app import event_clips, event_host, event_walk, robot_body

    if _tick(step):
        if step.get("in_box"):
            # staff say she is in the box (second review 23 Nov): the opening's one walk out armed, the arm latch on --
            # refused while she walks or the opening runs (its own end says where she is)
            return event_host.box_arm("ceremony_tick")
        return {"ok": True}                               # a manual step: the press is it
    do = step["do"]
    if do == "follow":
        from app import ceremony_follow

        return await ceremony_follow.start(by="order")    # (owner 24 Nov) on for the rest of the ceremony; done at once
    if do == "opening":
        # card 1 as the ceremony's first step (owner 23 Nov, app/event_tour.py): the screen's countdown sent, or (staff's
        # deliberate "without the screen") the walk out about to go -- running until the card ends (`_settle`)
        from app import event_tour

        press = press or {}
        result = await event_tour.begin_ceremony(step.get("scene"), without_screen=bool(press.get("without_screen")),
                                                 order={"gen": press.get("gen"), "key": press.get("key"), "mark": mark,
                                                        "stops": press.get("stops")})
        if result.get("ok"):
            mark.update(state="running", tour=result.get("seq"))
        return result
    if do == "screen":
        # awaited (the room PC takes seconds): the step is done when the screen says so. by
        # "scene": the very press a scene's `tv` makes (room_screens keeps a "staff" close as
        # closed by hand, which the old ceremony scene's open/close never was)
        result = await event_host.big_screen(step["screen"], by="scene")
        if isinstance(result, dict) and not result.get("ok") and not result.get("error_th"):
            what = SCREEN_FAILED_TH.get(step["screen"], SCREEN_FAILED_TH["open"])
            result = {**result, "error_th": "%s (%s)" % (what, result.get("error") or "error")}
        return result
    if do == "handoff":
        from app import ceremony

        return await ceremony.handoff(step["to"])
    if do == "walk":
        seen = event_walk.stops()
        # The ceremony's own walk from before its first await (review of the split, 9 Nov): its
        # run id lands on the mark only when go_spot answers -- up to ~20 s (the arm's rest) --
        # and a ceremony call that re-dialled in between took the walk for the event's and
        # stopped it (ceremony._ceremony_walk). `walk_pending` says "ours" until then.
        mark["walk_pending"] = True
        try:
            result = await event_walk.unless_stopped(seen, await event_host.go_spot(step["spot"], stops_seen=seen))
        finally:
            mark.pop("walk_pending", None)
        if result.get("ok"):
            run = (result.get("walk") or {}).get("run") or {}
            mark.update(state="running", run=run.get("id"))
        return result
    if do == "body":
        return await robot_body.staff_request(step["action"])
    if do == "cue" and _is_live(step):
        return await _open_live(step, mark)
    if do == "cue":
        mark.update(state="running", cue=step["cue"], pending=True)
        try:
            result = await _play_cue(step["cue"], step.get("show"))
        finally:
            mark["pending"] = False
        show = result.get("show") if isinstance(result, dict) else None
        if isinstance(show, dict) and show.get("ok") is False:
            mark["error_th"] = "ภาพไม่ขึ้น: %s" % (show.get("error_th") or show.get("error") or "")
        return result
    if do == "clip":
        result = await event_host.play_clip(step["clip"], by="staff")
        if isinstance(result, dict) and result.get("ok"):
            # cued is not playing (review 7 Nov, B4): running until the big screen says so
            mark.update(state="running", clip_seq=event_clips.STATE.get("seq"))
        return result
    if do == "device":
        return await _device(step, mark)
    return {"ok": False, "error": "bad_step", "error_th": ERRORS_TH["bad_step"]}


#: After a device command that did not go: how the ceremony goes on (the tick after it still waits).
DEVICE_FALLBACK_TH = " — กด 'ถัดไป' ลองอีกครั้ง หรือ 'ข้ามขั้นนี้' แล้วให้ทีมทำเอง (ขั้นติ๊กยืนยันยังรออยู่)"
#: ...and after one that MAY have gone ("unknown"): the room first -- a toggle IR remote sent twice turns
#: the lights back on, a one-button curtain remote stops (review 20 Nov). The page asks two taps for it.
DEVICE_UNKNOWN_TH = (" — ดูห้องก่อน ถ้ายังไม่เปลี่ยนค่อยกดซ้ำ (แตะสองครั้ง) หรือ 'ข้ามขั้นนี้' แล้วให้ทีมทำเอง "
                     "(ขั้นติ๊กยืนยันยังรออยู่)")


async def _device(step: dict, mark: dict) -> dict:
    """The venue device, through app/venue_devices.run. Done = it went out (`hardware` says how far:
    "sent" is the hub's word, "ok" the device's own, "mock" a rehearsal) -- never "the room changed":
    the staff tick after it says that. A failure keeps the order where it was."""
    from app import venue_devices

    result = await venue_devices.run(step.get("device"), step.get("action"), by="ceremony")
    mark["hardware"] = result.get("hardware")
    if result.get("hardware") == "mock":
        mark["error_th"] = venue_devices.DRY_RUN_TH          # a done step that warns: nothing went out
    if not result.get("ok"):
        how = DEVICE_UNKNOWN_TH if result.get("hardware") == "unknown" else DEVICE_FALLBACK_TH
        result = {**result, "error_th": (result.get("error_th") or ERRORS_TH["error"]) + how}
    return result


async def _open_live(step: dict, mark: dict) -> dict:
    """Her live window for this cue, with its brief (the model reads it; nothing is said for
    her). Accepted = running until "ถัดไป" closes it, or it closes by itself."""
    c = _cue_of(step) or {}
    opener = _live_api("live_open")
    if opener is None:
        return {"ok": False, "error": "no_live", "error_th": ERRORS_TH["no_live"]}
    opened = opener(cue_id=step["cue"], brief_th=c.get("live_th"))
    if inspect.isawaitable(opened):
        opened = await opened
    if not opened:
        return {"ok": False, "error": "live_refused", "error_th": ERRORS_TH["live_refused"]}
    mark.update(state="running", live=step["cue"], error_th=None)
    return {"ok": True, "live": "open", "note_th": LIVE_RUNNING_TH}


def _close_live(scenes: list[dict], n, i, step: dict, mark: dict, via: str) -> dict:
    """"ถัดไป" (or the step again) while her window is open: she has answered. Never gated --
    like a stop, ending something must always be possible."""
    _live_close("done")
    mark.update(state="done", error_th=None)
    _advance(scenes, n, i)                               # taken now, not when it was opened
    STATE["cleared"] = None
    _persist(scenes)
    _log(n, i, "cue", True, None, via)
    return {"ok": True, "n": n, "i": i, "do": "cue", "result": {"ok": True, "live": "closed"}}


async def _run(scenes: list[dict], n, i, *, via: str, stops: tuple | None = None, chained: bool = False,
               without_screen: bool = False) -> dict:
    """One press. Everything up to setting the press gate is synchronous: a second
    press arriving while this one awaits anything finds the gate set.

    `chained` = the server pressing a chained line (`_chain_watch`), with `stops` as they were when
    the line before it was pressed: a stop since = nothing runs. Its one difference from a staff
    press: the tail of the line before it does not refuse it (`_line_busy(tail=False)`)."""
    if stops is None:
        stops = _stop_gen()                               # before any await: a red stop since = nothing runs
    step = _find(scenes, n, i)
    if step is None:
        return _refused("no_step", n, i)
    _settle(scenes)
    live = _live_mark()
    if live is not None and tuple(live[1]["at_step"]) == (n, i):
        return _close_live(scenes, n, i, step, live[1], via)
    tick = _tick(step)
    token = None
    if not tick:
        if not _ceremony_app():
            # the ceremony's presses are the ceremony app's (owner 9 Nov, "ไม่งั้นจะปนกัน"): with the
            # Event app (or nothing) on the robot, a line, a walk, the big screen, a handoff from
            # this order would be the ceremony's machinery running into the event's day
            _log(n, i, step["do"], False, "not_ceremony_app", via)
            return _refused("not_ceremony_app", n, i)
        if live is not None:
            # her window is open: a line, a walk, a gesture now is over her answer to the host
            _log(n, i, step["do"], False, "live", via)
            return _refused("live", n, i)
        if not _is_device(step) and step["do"] != "opening" and _opening_running():
            # the opening walks, waves and shakes hands by itself (app/event_tour.py): a line, a walk, a gesture of
            # the order now would land in the middle of it (the opening pressed again: its own readiness says so)
            _log(n, i, step["do"], False, "opening", via)
            return _refused("opening", n, i)
        if _line_busy(tail=not chained) and not _is_live(step) and not _is_device(step):
            # Not the live window: opening it says nothing, and it must be open BEFORE the
            # host answers -- s4_ask_view ends "บอกฉันได้เลยค่ะ" and the host replies at once;
            # refused until her tail played out, the start of that reply went up as silence
            # (review 7 Nov, round 3). Nor a venue device: it says nothing either, and its beat is
            # her "Turning off the lobby lights now" while that is still being heard (20 Nov).
            _log(n, i, step["do"], False, "running", via)
            return _refused("running", n, i)
        if step["do"] == "cue" and not _is_live(step) and _robot_moving():
            # A line while the robot moves: the mic is open then (a spoken stop must be
            # heard), so applause over a handshake or the arrival cut the line pressed
            # straight after as a barge-in (review 7 Nov, round 3). No step needs both at
            # once: the script walks, then speaks; shakes hands, then greets.
            _log(n, i, step["do"], False, "moving", via)
            return _refused("moving", n, i)
        if _is_device(step):
            # A venue device does not take the order's one press gate (review 20 Nov: a hub that does not
            # answer held every other step for up to VENUE_DEVICE_TIMEOUT_S): its own guard is per step --
            # the same step still sending is "busy" (and venue_devices allows one command per device).
            mine = STATE["marks"].get(_key(n, i))
            if (n, i) in _DEVICE_PRESSING or (mine is not None and mine.get("state") == "running"):
                _log(n, i, step["do"], False, "busy", via)
                return _refused("busy", n, i)
            _DEVICE_PRESSING.add((n, i))                   # before the first await, like the gate
            token = ("device", (n, i))
        elif STATE["pressing"] is not None:
            _log(n, i, step["do"], False, "busy", via)
            return _refused("busy", n, i)
        else:
            token = STATE["pressing"] = object()           # before the first await
    gen = STATE["gen"]
    STATE["cleared"] = None                               # the page's "cleared" note: a press reads on
    try:
        ctx = await _context()
        ctx["without_screen"] = without_screen            # the opening on purpose without the screen's countdown
        view = _view(step, ctx)
        if _stop_gen() != stops:
            # the red stop came while this press read its readiness (review 7 Nov, A2): it was
            # pressed before the stop, and runs nothing after it
            _log(n, i, step["do"], False, "stopped", via)
            return _refused("stopped", n, i)
        if not view["ready"]:
            _log(n, i, step["do"], False, "not_ready", via)
            return _refused("not_ready", n, i, view["why_th"] or ERRORS_TH["not_ready"])
        mark = {"state": "running", "at": time.time(), "since": time.monotonic(), "at_step": (n, i),
                "error_th": None}
        if STATE["gen"] == gen:
            if step.get("chain") and (STATE.get("chain") or {}).get("lead") == (n, i):
                # the set-up pressed again: its old chain is over NOW (review 23 Nov -- its watcher saw
                # the new mark before this press's own chain began, and put a passing "missed" on the
                # punchline); this press starts its own once accepted
                STATE["chain"] = None
            STATE["marks"][_key(n, i)] = mark
            _persist(scenes)                              # a death from here on comes back "failed"
        try:
            result = await _dispatch(step, mark, press={"gen": gen, "key": _key(n, i), "stops": stops,
                                                        "without_screen": without_screen})
        except Exception:                                # noqa: BLE001 -- the phone gets an answer
            logger.exception("event order: step %s.%s", n, i)
            result = {"ok": False, "error": "error", "error_th": ERRORS_TH["error"]}
    finally:
        if token is not None and STATE["pressing"] is token:
            STATE["pressing"] = None
        if isinstance(token, tuple) and token[0] == "device":
            _DEVICE_PRESSING.discard(token[1])
    ok = bool(isinstance(result, dict) and result.get("ok"))
    if ok:
        if mark["state"] == "running" and not (mark.get("cue") or mark.get("run") or mark.get("live")
                                               or mark.get("clip_seq") is not None or mark.get("tour") is not None):
            mark["state"] = "done"
        if STATE["gen"] == gen and not mark.get("live"):
            _advance(scenes, n, i)                        # a live window is taken when it is closed
        if STATE["gen"] == gen and step.get("chain") and mark.get("cue") and mark["state"] == "running":
            _chain_start(scenes, (n, i), mark, stops, gen)   # the joke's punchline follows by itself
    else:
        error = (result or {}).get("error") or "error"
        mark.update(state="failed", error=error,
                    error_th=(result or {}).get("error_th") or ERRORS_TH["error"])
        if STATE["gen"] == gen and step.get("chain") and step.get("do") == "cue":
            _chain_lead_refused(scenes, (n, i), mark)     # a joke's set-up not said: never "it follows"
    if STATE["gen"] == gen:
        _persist(scenes)
    _log(n, i, step["do"], ok, None if ok else mark.get("error"), via)
    out = {"ok": ok, "n": n, "i": i, "do": step["do"], "result": result}
    if not ok:
        out.update(error=mark.get("error"), error_th=mark.get("error_th"))
    return out


# ---- a chained line: the joke said straight through (owner 23 Nov) -----------------------------

def _chain_follower(scenes: list[dict], n, i) -> tuple[int, int] | None:
    """The step a `"chain": true` on (n, i) presses: the very next step of its scene in the file,
    when both are worded lines (not a live brief: its end is the model's; not a line with a film
    after it: the next line would start over the film). None = no chain here
    (event_script.order_problems names what is wrong with one that is written)."""
    if n is None:
        return None
    for sc in scenes:
        if sc["n"] != n:
            continue
        steps = sc["steps"]
        for k, st in enumerate(steps):
            if st["i"] != i:
                continue
            if not st.get("chain") or st["do"] != "cue" or k + 1 >= len(steps):
                return None
            after = steps[k + 1]
            # the file's next step, not one past a step this machine hides (a device step between
            # them is a file error: order_problems)
            if after["do"] != "cue" or after["i"] != i + 1:
                return None
            lead, follow = _cue_of(st), _cue_of(after)
            if (lead is None or follow is None or not lead.get("th") or not follow.get("th")
                    or lead.get("clip")):
                return None
            return n, after["i"]
    return None


def _chain_views(scenes: list[dict]) -> dict:
    """(n, i) -> the `chain` of a step in a chained pair, for the page: {role, state, th, warn}.
    `state` is this pair's chain now: "waiting" (the set-up is being said), "pressed" (the server
    pressed the punchline), "taken" (staff pressed or skipped it first), "missed" (it did not
    follow -- `th` says why and that staff press it), None (not pressed since)."""
    out = {}
    current = STATE.get("chain")
    for sc in scenes:
        for st in sc["steps"]:
            if not st.get("chain"):
                continue
            follow = _chain_follower(scenes, sc["n"], st["i"])
            if follow is None:
                continue
            lead = (sc["n"], st["i"])
            mine = current if current is not None and (current["lead"], current["follow"]) == (lead, follow) else None
            state = mine["state"] if mine is not None else None
            if state == "waiting" and (mine.get("task") is None or mine["task"].done()):
                state = None                       # nothing is watching it any more: never "waiting" for nothing
            again = state == "missed" and mine.get("why") == "lead_failed"
            out[lead] = {"role": "lead", "state": state, "th": CHAIN_LEAD_AGAIN_TH if again else CHAIN_LEAD_TH,
                         "warn": again}
            if again:
                th = CHAIN_LEAD_FAILED_TH
            elif state == "missed":
                th = CHAIN_MISSED_TH % (mine.get("why_th") or CHAIN_WHY_TH["error"])
            else:
                th = {"waiting": CHAIN_WAITING_TH, "pressed": CHAIN_PRESSED_TH}.get(state, CHAIN_FOLLOW_TH)
            out[follow] = {"role": "follow", "state": state, "th": th, "warn": state == "missed"}
    return out


def _chain_start(scenes: list[dict], lead: tuple[int, int], mark: dict, stops: tuple, gen: int) -> None:
    """The set-up of a chained pair was accepted: watch it, and press its punchline when it is said.
    One chain at a time (a newer one -- the set-up pressed again -- replaces it)."""
    follow = _chain_follower(scenes, *lead)
    if follow is None:
        return
    chain = {"lead": lead, "follow": follow, "state": "waiting", "why_th": None, "task": None, "mark": mark}
    STATE["chain"] = chain
    task = asyncio.get_running_loop().create_task(_chain_watch(scenes, chain, mark, stops, gen))
    chain["task"] = task
    _CHAIN_TASKS.add(task)
    task.add_done_callback(_CHAIN_TASKS.discard)


def _chain_lead_refused(scenes: list[dict], lead: tuple[int, int], mark: dict) -> None:
    """The set-up's press failed on its way (event_cues refused it -- `quiet`, `no_call` ...): no chain
    is watching, and the punchline must not say it follows (review 23 Nov) -- the set-up was not said,
    press it again (CHAIN_LEAD_FAILED_TH; "ถัดไป" on it: `_chain_redo`)."""
    follow = _chain_follower(scenes, *lead)
    if follow is not None:
        STATE["chain"] = {"lead": lead, "follow": follow, "state": "missed", "why": "lead_failed",
                          "why_th": CHAIN_WHY_TH["lead_failed"], "task": None, "mark": mark}


def _lead_said(mark: dict) -> bool | None:
    """The chained set-up: True = said whole (its event_cues run over and ok -- its last seconds may
    still be playing), False = not (failed, skipped, cut), None = not yet. `_settle`'s reading
    without its wait for the voice's tail: that wait is event_cues' own, before the next line."""
    state = mark.get("state")
    if state == "done":
        return True
    if state != "running":
        return False
    if mark.get("pending") or any(r.get("id") == mark["cue"] for r in _cue_runs()):
        return None
    last = _cue_last(mark["cue"])
    if last is not None and float(last.get("at") or 0) >= mark["at"] - 1.0:
        # a line cut short (her own stop, a newer line over it) is not a set-up to answer
        return bool(last.get("ok")) and not last.get("cut")
    return True                                          # accepted, and nothing is saying it any more


async def _chain_watch(scenes: list[dict], chain: dict, mark: dict, stops: tuple, gen: int) -> None:
    """Until the set-up is said, then its punchline pressed -- as a press (`_run`, via "chain")."""
    lead, follow = chain["lead"], chain["follow"]
    deadline = time.monotonic() + CHAIN_WAIT_S
    why = None
    try:
        while True:
            await asyncio.sleep(CHAIN_POLL_S)
            if STATE.get("chain") is not chain or STATE["gen"] != gen:
                return                                   # a newer chain, or the order was reset
            if STATE["marks"].get(_key(*lead)) is not mark:
                # the set-up's mark replaced by staff (skipped): the joke is theirs now. (A press of
                # it again retires this chain first -- `_run` -- so it never shows a passing "missed".)
                chain["state"] = "taken"
                return
            if _stop_gen() != stops:
                why = "stopped"                          # the red stop (or any stop) since the set-up
                break
            taken = STATE["marks"].get(_key(*follow))
            if taken is not None and taken.get("since", 0.0) >= mark["since"]:
                chain["state"] = "taken"                 # staff pressed (or skipped) the punchline first
                return
            said = _lead_said(mark)
            if said is False:
                why = "lead_failed"
                break
            if said is None or STATE["pressing"] is not None:
                # not said yet -- or a press in flight (staff's, maybe of the punchline itself):
                # looked at again once it has answered
                if time.monotonic() > deadline:
                    why = "timeout"
                    break
                continue
            break
        if why is not None:
            chain.update(state="missed", why=why, why_th=CHAIN_WHY_TH[why])
            _log(follow[0], follow[1], "cue", False, why, "chain")
            return
        # said: the set-up is done (what is left of it is its voice's last seconds, which event_cues
        # waits out before the punchline's first word) -- and the punchline is pressed NOW, no gap
        if mark.get("state") == "running":
            mark["state"] = "done"
            _persist(scenes)
        try:
            from app import event_cues

            event_cues.STATE["chained_next"] = (_find(scenes, *follow) or {}).get("cue")   # (read sooner: CHAIN_READ_LEAD_S)
        except Exception:                                # noqa: BLE001 -- the ordinary wait
            pass
        result = await _run(scenes, follow[0], follow[1], via="chain", stops=stops, chained=True)
        if result.get("ok"):
            chain["state"] = "pressed"
        else:
            chain.update(state="missed", why_th=result.get("error_th") or CHAIN_WHY_TH["error"])
    except asyncio.CancelledError:
        raise
    except Exception:                                    # noqa: BLE001 -- staff press it
        logger.exception("event order: the chain %s -> %s", lead, follow)
        chain.update(state="missed", why_th=CHAIN_WHY_TH["error"])
    finally:
        if chain["state"] == "waiting":
            chain.update(state="missed", why_th=CHAIN_WHY_TH["error"])


async def press(n, i, *, without_screen: bool = False) -> dict:
    """Run step (n, i), wherever the order is: staff following the host may go back
    or jump (`last` only ever moves forward). A step that is not ready is not run.
    `without_screen`: the opening step, started on purpose without the room screen's countdown (the page's own
    two-tap button, shown only while the screen cannot count down)."""
    scenes = _scenes()
    if scenes is None:
        return _refused("no_order")
    _restore_once(scenes)
    _expire_old(scenes)
    step = _find(scenes, n, i)
    if without_screen and (step is None or step.get("do") != "opening"):
        return _refused("bad_step", n, i)
    return await _run(scenes, n, i, via="press_no_screen" if without_screen else "press", without_screen=without_screen)


async def next(expect: tuple[int, int] | None = None) -> dict:  # noqa: A001 -- the phone's "ถัดไป"
    """Run the step after the last one taken (the first when none) -- or, while her live
    window is open, close it. Not ready = `not_ready` with its (n, i), and the order stays
    where it was. `expect` = the step the phone's button showed: the order moved since
    (another phone, a skip, a restart) = `stale`, nothing runs."""
    scenes = _scenes()
    if scenes is None:
        return _refused("no_order")
    _restore_once(scenes)
    _expire_old(scenes)
    _settle(scenes)
    target = _target(scenes)
    if expect is not None and (target is None or tuple(expect) != target):
        _log(expect[0], expect[1], None, False, "stale", "next")
        out = _refused("stale", expect[0], expect[1])
        out["next"] = list(target) if target else None
        return out
    if target is None:
        return _refused("end")
    step = _find(scenes, *target)
    if step is not None and _tick(step) and step.get("in_box"):
        # one tap of "ถัดไป" after a reset went through the tick and re-armed the walk out (third review, 23 Nov):
        # it takes the step's own button, two taps (the page's whyTwoTaps)
        _log(target[0], target[1], step["do"], False, "in_box_tap", "next")
        return _refused("in_box_tap", target[0], target[1])
    return await _run(scenes, target[0], target[1], via="next")


async def skip(n, i) -> dict:
    """Staff skip a step on purpose: marked skipped; the order moves past it only if
    it was ahead (skipping an earlier step never rewinds `last`)."""
    scenes = _scenes()
    if scenes is None:
        return _refused("no_order")
    _restore_once(scenes)
    _expire_old(scenes)
    step = _find(scenes, n, i)
    if step is None:
        return _refused("no_step", n, i)
    _settle(scenes)
    mark = STATE["marks"].get(_key(n, i))
    if mark is not None and mark.get("state") == "running":
        _log(n, i, step["do"], False, "running", "skip")
        return _refused("running", n, i)
    STATE["marks"][_key(n, i)] = {"state": "skipped", "at": time.time(), "since": time.monotonic(),
                                  "at_step": (n, i), "error_th": None}
    if (STATE.get("chain") or {}).get("lead") == (n, i):
        STATE["chain"] = None                         # a joke's set-up skipped: its punchline is staff's
    STATE["cleared"] = None
    _advance(scenes, n, i)
    _persist(scenes)
    _log(n, i, step["do"], True, None, "skip")
    return {"ok": True, "n": n, "i": i, "do": step["do"]}


async def mark_shown(n, i) -> dict:
    """CEREMONY_FOLLOW_CUES off (owner 24 Nov: she answers live): the host said this cue's line and its slide went
    up on the hall screen (app/ceremony_follow.py) -- the step is marked done the way a press would, WITHOUT playing
    anything, so the pointer moves on. A running step is refused; a taken one is left as it is."""
    scenes = _scenes()
    if scenes is None:
        return _refused("no_order")
    _restore_once(scenes)
    _expire_old(scenes)
    step = _find(scenes, n, i)
    if step is None:
        return _refused("no_step", n, i)
    _settle(scenes)
    mark = STATE["marks"].get(_key(n, i))
    if mark is not None and mark.get("state") == "running":
        return _refused("running", n, i)
    if mark is None or mark.get("state") not in ("done", "skipped"):
        STATE["marks"][_key(n, i)] = {"state": "done", "at": time.time(), "since": time.monotonic(),
                                      "at_step": (n, i), "error_th": None}
    STATE["cleared"] = None
    _advance(scenes, n, i)
    _persist(scenes)
    _log(n, i, step["do"], True, None, "shown")
    return {"ok": True, "n": n, "i": i, "do": step["do"], "shown": True}


#: Card 2 after a whole card 1 (`follow_after_opening`): how long it waits for the order's own mark of the opening and
#: for her last line or move of card 1 to be over.
AFTER_OPENING_WAIT_S = 20.0
#: The refusals that only mean "not yet" (a line still sounding, the opening's mark not settled, a move finishing).
_NOT_YET = ("running", "opening", "busy", "moving", "live")


async def follow_after_opening() -> dict:
    """Card 1 went the whole round and she stands at card 2's spot (owner 25 Nov 00:09: "...ไปถึงตำแหน่งหุ่นจะการ์ดสองเอง
    เลย"): the `follow` step after the opening step, in its scene, is pressed through the normal path and all its gates --
    card 2 starts by itself. Called by app/event_tour.py at the opening's whole end (`_card_two`), before the order's own
    runner may have marked the opening done: it waits for that mark, and for her last line or move of card 1, at most
    AFTER_OPENING_WAIT_S. A red stop since, a failed or skipped opening, a follow already taken, or none = nothing."""
    import asyncio

    stops = _stop_gen()
    deadline = time.monotonic() + AFTER_OPENING_WAIT_S
    last = None
    while True:
        scenes = _scenes()
        if scenes is None:
            return _refused("no_order")
        _restore_once(scenes)
        _expire_old(scenes)
        _settle(scenes)
        found = None
        for sc in scenes:
            steps = sc["steps"]
            for k, st in enumerate(steps):
                if st.get("do") != "opening":
                    continue
                for later in steps[k + 1:]:
                    if later.get("do") == "follow":
                        found = (sc["n"], st["i"], later["i"])
                        break
            if found is not None:
                break
        if found is None:
            return _refused("no_step")
        n, oi, fi = found
        opening = (STATE["marks"].get(_key(n, oi)) or {}).get("state")
        if (STATE["marks"].get(_key(n, fi)) or {}).get("state") in ("done", "running"):
            return {"ok": True, "n": n, "i": fi, "already": True}
        if _stop_gen() != stops:
            _log(n, fi, "follow", False, "stopped", "after_opening")
            return _refused("stopped", n, fi)
        if opening in ("failed", "skipped"):
            return _refused("opening", n, fi)
        if opening == "done":
            last = await _run(scenes, n, fi, via="after_opening", stops=stops)
            if last.get("ok") and _stop_gen() == stops:
                last = {**last, "opener": await _card_two_opener()}
            if last.get("ok") or last.get("error") not in _NOT_YET:
                return last
        if time.monotonic() >= deadline:
            out = last or _refused("opening", n, fi)
            _log(n, fi, "follow", False, "after_opening_timeout", "after_opening")
            return out
        await asyncio.sleep(0.5)


async def _card_two_opener() -> dict:
    """Her first words of card 2 to Khun Miki (owner 25 Nov: "อาจจะเป็นคนพูดเปิดกับมิกิเลย"): the event file's top-level
    `card2_open` = {file under data/events/clips/, sha256 of the take the owner picked by ear, text, lang} -- the scenes'
    `recorded` rules and road (event_host.recorded_pcm, event_cues.play_line: her reply first, the lock, the red stop).
    None in the file = nothing said: card 2 just listens. Never raises."""
    try:
        from app import event_cues, event_host

        path = event_host._path()
        raw = event_host._read_json(path) if path is not None else None
        if not isinstance(raw, dict) or raw.get("card2_open") is None:
            return {"ok": False, "error": "none"}
        rec = event_host._recorded_entry({"id": "card2_open", "recorded": raw["card2_open"]})
        if rec is None:
            return {"ok": False, "error": "format"}
        import asyncio

        pcm, why = await asyncio.to_thread(event_host.recorded_pcm, {"recorded": rec})
        if pcm is None:
            turnlog.record("event_order", do="card2_open", ok=False, error=why, via="after_opening")
            return {"ok": False, "error": why or "missing"}
        result = await event_cues.play_line("card2_open", rec["text"], pcm, lang=rec["lang"])
        turnlog.record("event_order", do="card2_open", ok=bool(result.get("ok")), error=result.get("error"),
                       via="after_opening")
        return {"ok": bool(result.get("ok")), "error": result.get("error")}
    except Exception:                                    # noqa: BLE001 -- card 2 listens all the same
        logger.exception("event order: card 2's first words")
        return {"ok": False, "error": "error"}


async def opening_hand(n, i, k) -> dict:
    """One step the ceremony's opening did not get to, done by hand from its own card on the ceremony page
    (app/event_tour.py `view()["hand"]`: a walk to a spot, a handshake) -- the order's old walk and handshake steps
    live on there, for a card that stopped short only (owner 23 Nov: the opening replaced them). The gates of a walk
    or a body step of the order: the ceremony app, no live window, no line being said, one press at a time, the
    app's 60 s latch, a stop since the press; never while the opening itself runs. The page asks two taps."""
    from app import event_host, event_tour, event_walk, robot_body

    scenes = _scenes()
    if scenes is None:
        return _refused("no_order")
    stops = _stop_gen()                                   # before any await: a red stop since = nothing runs
    step = _find(scenes, n, i)
    if step is None or step.get("do") != "opening":
        return _refused("no_step", n, i)
    view = event_tour.view() or {}
    hand = None                                           # (no builtin next() here: `next` is the phone's "ถัดไป")
    for h in view.get("hand") or [] if view.get("owner") == "ceremony" else []:
        if h["k"] == k:
            hand = h
    if hand is None or hand.get("done"):
        return _refused("no_hand", n, i)
    do = "walk" if hand["kind"] == "walk" else "body"
    wave = hand["kind"] == "wave"                         # the opening's own wave and line (event_tour.wave_by_hand)
    if not _ceremony_app():
        why = "not_ceremony_app"
    elif _live_mark() is not None:
        why = "live"
    elif _opening_running():
        why = "opening"
    elif _line_busy():
        why = "running"
    elif STATE["pressing"] is not None:
        why = "busy"
    else:
        why = None
    if why is not None:
        _log(n, i, do, False, why, "opening_hand")
        return _refused(why, n, i)
    # (the wave's gates are the pageant's own and the in-the-box latch: event_tour.wave_by_hand)
    gate = None if do == "walk" or wave else robot_body.staff_ready("handshake")
    if _app_latched() or gate:
        _log(n, i, do, False, "not_ready", "opening_hand")
        return _refused("not_ready", n, i, LATCHED_TH if not gate else robot_body.staff_reason_th(gate))
    token = STATE["pressing"] = object()                  # before the first await
    try:
        if do == "walk":
            seen = event_walk.stops()
            # the card's walk to a handshake by hand comes the card's way in (head-on at the counter, owner 23 Nov)
            result = await event_walk.unless_stopped(seen, await event_host.go_spot(
                hand["spot"], stops_seen=seen, head_on=bool(hand.get("head_on"))))
            run = ((result.get("walk") or {}).get("run") or {}).get("id") if result.get("ok") else None
            if run is not None:
                event_tour.hand_walk(run)                 # the ceremony's own walk: a re-dial does not stop it
        elif _stop_gen() != stops:
            result = _refused("stopped", n, i)
        elif wave:
            result = await event_tour.wave_by_hand(still=lambda: _stop_gen() == stops and _ceremony_app())
        else:
            result = await robot_body.staff_request("handshake")
    except Exception:                                    # noqa: BLE001 -- the phone gets an answer
        logger.exception("event order: the opening's step %s by hand", k)
        result = {"ok": False, "error": "error", "error_th": ERRORS_TH["error"]}
    finally:
        if STATE["pressing"] is token:
            STATE["pressing"] = None
    ok = bool(isinstance(result, dict) and result.get("ok"))
    if ok:
        event_tour.hand_pressed(k)
    _log(n, i, do, ok, None if ok else (result or {}).get("error"), "opening_hand")
    out = {"ok": ok, "n": n, "i": i, "do": do, "k": k, "result": result}
    if not ok:
        out.update(error=(result or {}).get("error") or "error", error_th=(result or {}).get("error_th") or ERRORS_TH["error"])
    return out


def reset_order() -> dict:
    """The phone's "เริ่มลำดับใหม่": every mark gone, back to the first step -- and the
    kept copy too (it must not come back after the next restart). Her live window, if
    one is open, closes with it. A press still in flight keeps its gate (reset())."""
    if _live_mark() is not None:
        _live_close("reset")
    reset()
    STATE["restored"] = True
    scenes = _scenes()
    if scenes is not None:
        _persist(scenes, force=True)
    turnlog.record("event_order", n=None, i=None, do=None, ok=True, error=None, via="reset")
    return {"ok": True}
