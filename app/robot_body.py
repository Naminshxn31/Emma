"""Voice control of the robot's own head and arms, decided on the server side.

docs/emma-body-nav-design-2026-10-08.md §2d (package P1). The model picks one
name from a fixed vocabulary (`robot_contracts.BODY_ACTIONS`); this module
decides whether that name may be *sent*, sends only the name to the robot face
page through `robot_face_link`, and turns the app's synchronous accept/refuse
into a tool result the model cannot misread as "done".

What it never does, on purpose:

- **hold a pulse, a channel or a group number.** The Android app's gesture
  table owns those; it is the only path that has moved the arm (group 6, 30
  Sep). The same rule as `show_unit(room)` and not `show_unit(room, price)`.
- **wait for the gesture to finish.** An ack means "queued after the cheap
  gates passed". Completion arrives later as `body_status`, goes to the
  turnlog and `STATE`, and is never announced: a robot that narrates its own
  arm is chatty, and the tool already told the truth (requested, not done).
- **refuse a stop.** `stop` skips the server gate and the heard guard; the
  registry (`preempt`) and the face link exempt it from "busy" and
  `request_pending` (safety review #1).

The heard guard is the `heard.py` precedent ("bit like" closed the deck in
front of a guest) applied to motion, tightened by safety review #7: the
caption must be this session's, newer than its latest `activity_start`, at
most ROBOT_HEARD_MAX_AGE_S old, not already spent on another motion, and must
hold the action's own verb (and side) as words. Failing it *asks*; it never
moves anything.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from app import heard, robot_contracts as rc, robot_face_link
from app.config import settings

logger = logging.getLogger("condo_voice.robot_body")

#: Gestures that are stored action groups on the board (group 3, group 6,
#: group 99). `ROBOT_ARM_GROUPS_ENABLED` is the switch that says a recording
#: whose extent nobody can state may play (robot_arm.run_group's docstring),
#: so a voice path to the same groups must pass it too.
GROUP_ACTIONS = ("greet_arm", "handshake", "rest")

#: Statuses the app will play without an operator opt-in.
_PLAYABLE = ("verified", "operator_mapped")

#: Appended to every instruction. The 8 Oct eval measured 9.2% of replies
#: drifting into English after an English greeting; a tool result written in
#: Thai pulls the same way unless it says otherwise.
_SAME_LANGUAGE = " ตอบเป็นภาษาเดียวกับที่ผู้ใช้พูด"

#: For get_robot_status (P2). One robot, one body: the latest gesture the app
#: reported, never a guess about where a joint is.
STATE: dict[str, Any] = {"last_action": None, "state": None, "reason": None, "at": None}

#: id(session) -> (session, (seq, state)) of the last body_status recorded,
#: so a page that re-sends the same frame does not log it twice.
_SEEN: dict[int, tuple[object, tuple[int, str]]] = {}


def reset_state() -> None:
    """conftest (through sys.modules, the calc rule)."""
    STATE.update(last_action=None, state=None, reason=None, at=None)
    _SEEN.clear()


def _label(action: str) -> str:
    return rc.BODY_LABELS.get(action, action)


def _labels_th(ids: list[str] | None) -> str:
    if ids is None:
        return "ยังไม่ทราบ (หุ่นยังไม่ได้ส่งรายการท่ามา)"
    if not ids:
        return "ยังไม่มีท่าที่พร้อมใช้"
    return ", ".join(_label(i) for i in ids)


def _playable(status: str | None, allow_direction_known: bool) -> bool:
    return status in _PLAYABLE or (status == "direction_known" and allow_direction_known)


def available(session) -> list[str] | None:
    """Ids the robot says it can play right now, from this session's cached
    `body_caps`; None when the page has not sent any. `stop` is never listed:
    it is not a gesture, and it is always possible."""
    caps = robot_face_link.caps(session, "body") if session is not None else None
    if not caps:
        return None
    if caps.get("enabled") is not True:
        return []
    allow = caps.get("allow_direction_known") is True
    gestures = caps.get("gestures") or {}
    return [a for a in rc.BODY_ACTION_IDS
            if a != "stop" and _playable(gestures.get(a), allow)]


def _cached_refusal(session, action: str) -> str | None:
    """Step 5: what the robot already told us about this id, without a round
    trip. Only the mapping statuses are trusted from the cache — they change
    on the operator screen, which re-sends `body_caps`; every other refusal
    (busy, latched, foreground) is the robot's to decide at the moment."""
    caps = robot_face_link.caps(session, "body")
    if not caps:
        return None
    status = (caps.get("gestures") or {}).get(action)
    if status == "needs_mapping":
        return "needs_mapping"
    if status == "direction_known" and caps.get("allow_direction_known") is not True:
        return "direction_unconfirmed"
    return None


def _heard_words(text: str) -> list[str]:
    """Whole words of a caption, Latin always, Thai only with the segmenter.

    Without pythainlp `retrieval.tokenize` falls back to character n-grams,
    which hands `ยก` out of `ยกเลิก` as if it were a word — the `ราคา`/`อาคาร`
    bug, now able to lift an arm. So the Thai part of a caption authorises
    nothing on such a machine and the model asks instead; the same rule as
    `robot_places.match`. `_tokeniser` is read, never re-initialised:
    `init_tokeniser()` would wipe the deck vocabulary.
    """
    from app.tools import retrieval

    words = retrieval.tokenize(text or "")
    if retrieval._tokeniser is None:
        words = [w for w in words if not any("฀" <= ch <= "๿" for ch in w)]
    return words


def heard_ok(session, action: str) -> bool:
    """Step 4. Fresh (after the latest activity_start, within the age bound,
    `heard.caption_for`), the right words (`robot_contracts.heard_matches`),
    and unused (`heard.consume`, shared with nav so one sentence moves the
    robot once). Consumed only when the words match: a caption that asked
    for nothing is not spent by a call it did not authorise."""
    if action == "stop" or not settings.robot_body_require_heard:
        return True
    caption = heard.caption_for(session)
    if caption is None:
        return False
    if not rc.heard_matches(action, _heard_words(caption.text)):
        return False
    return heard.consume(session, caption)


def _server_gate(action: str) -> bool:
    """Step 2: the server's own keys. ROBOT_ARM_ENABLED and
    ROBOT_ARM_MOTION_ENABLED are the existing hardware interlock (D2) — a
    voice path that skipped them would be the one way round the switch
    that says the arm has no proven stop."""
    if not (settings.robot_body_enabled and settings.robot_arm_enabled
            and settings.robot_arm_motion_enabled):
        return False
    if action in GROUP_ACTIONS and not settings.robot_arm_groups_enabled:
        return False
    return True


def _result(action: str, hardware: str, reason: str | None, instruction: str, *,
            ok: bool = False, status: str | None = None, est_ms: int | None = None,
            avail: list[str] | None = None) -> dict:
    return {
        "ok": ok,
        "hardware": hardware,
        "action": action,
        "reason": reason,
        "status": status,
        "est_s": round(est_ms / 1000.0, 1) if isinstance(est_ms, int) else None,
        "available": avail,
        "instruction": instruction + _SAME_LANGUAGE,
    }


def _refuse(session, action: str, hardware: str, reason: str, *,
            avail: list[str] | None = None, status: str | None = None,
            stop_sent: bool = False) -> dict:
    th = _label(action)
    if action == "stop":
        # Review 2, finding #15: a stop that timed out or never left fell
        # through to the gesture text, "ห้ามสั่งซ้ำเอง" — the wrong way round
        # for a stop, which is idempotent and never refused on a retry
        # (registry preempt, the link's stop bypass).
        hardware = "failed"
        text = ("ส่งคำสั่งหยุดแขนไม่สำเร็จ: %s ยังไม่รู้ว่าแขนหยุดหรือไม่ ห้ามพูดว่า 'หยุดแล้ว' "
                "'แขนหยุดแล้ว' หรือ 'เรียบร้อยค่ะ' สั่งหยุดซ้ำได้ "
                "และให้บอกคนตรงนั้นกดปุ่มหยุดฉุกเฉิน (E-stop) บนตัวหุ่นทันที"
                % rc.body_reason_th(reason))
    elif reason in ("needs_mapping", "direction_unconfirmed"):
        text = ("ท่า%sยังไม่ได้ตั้งค่าบนตัวหุ่น ห้ามบอกว่าทำแล้ว ให้บอกตรงๆ ว่ายังทำท่านี้ไม่ได้ "
                "ท่าที่ทำได้ตอนนี้คือ %s" % (th, _labels_th(avail)))
    elif reason in ("busy", "cooldown", "rate_limited"):
        text = "หุ่นยังทำท่าก่อนหน้าไม่จบ ห้ามสั่งซ้ำเอง ให้บอกให้รอสักครู่"
    elif reason == "navigating":
        text = "หุ่นกำลังเดินอยู่ จึงขยับแขนไม่ได้เพื่อความปลอดภัย ให้บอกตรงๆ"
    elif reason == "nav_unknown":
        # Review 2, finding #6: the app answers nav_unknown when its own nav
        # poll is not fresh (before the first poll, or for good when su or
        # emma_nav fails) — "cannot tell", not "walking". Sharing the branch
        # above had Emma tell a guest the robot was walking while it stood.
        text = ("ตอนนี้หุ่นตรวจสถานะการเดินของตัวเองไม่ได้ จึงยังไม่ขยับแขน "
                "ห้ามพูดว่า 'หุ่นกำลังเดินอยู่' ให้บอกตรงๆ ว่ายังทำท่า%sไม่ได้ตอนนี้" % th)
    elif reason == "heard_missing":
        text = "ไม่ได้ยินคำสั่งท่าทางชัดเจน ห้ามขยับเอง ให้ถามสั้นๆ ว่าต้องการให้ทำท่าอะไร"
    elif reason == "server_disabled":
        text = "ตอนนี้ปิดการสั่งท่าทางไว้ ห้ามบอกว่าทำแล้ว ให้บอกตรงๆ ว่ายังสั่งท่าไม่ได้"
    elif reason == "timeout" and stop_sent:
        # Finding #5: the page relays the gesture whether or not its ack made
        # it back, so the arm may be moving; the link has sent a body stop.
        text = ("หุ่นไม่ตอบรับคำสั่ง ยังไม่รู้ว่าขยับหรือไม่ ส่งคำสั่งหยุดแขนตามไปแล้ว ห้ามสั่งซ้ำเอง "
                "ห้ามบอกว่าทำแล้ว ให้บอกว่าหุ่นไม่ตอบรับ ถ้าแขนยังขยับให้กดปุ่มหยุดฉุกเฉิน (E-stop) บนตัวหุ่น")
    elif reason in ("timeout", "disconnected", "request_pending"):
        text = ("หุ่นไม่ตอบรับคำสั่ง ยังไม่รู้ว่าขยับหรือไม่ ห้ามสั่งซ้ำเอง "
                "ห้ามบอกว่าทำแล้ว ให้บอกว่าหุ่นไม่ตอบรับ ถ้าแขนขยับให้กดปุ่มหยุดฉุกเฉิน (E-stop) บนตัวหุ่น")
    else:
        # disabled / latched / operator_screen / not_foreground /
        # arm_unavailable / not_robot_face / bad_request: the robot's own
        # Thai reason, and the one sentence that must not follow it.
        text = ("หุ่นไม่ทำท่า%s เพราะ%s ห้ามบอกว่าทำแล้ว ห้ามสั่งซ้ำเอง ให้บอกตรงๆ"
                % (th, rc.body_reason_th(reason)))
    _record("body_refused", action=action, reason=reason, hardware=hardware)
    return _result(action, hardware, reason, text, status=status, avail=avail)


def _record(kind: str, **fields) -> None:
    try:
        from app import turnlog

        turnlog.record(kind, **fields)
    except Exception:
        logger.debug("turnlog failed for %s", kind, exc_info=True)


#: Link-level answers: nothing on the robot decided anything. "failed", not
#: "refused" — the model is told it does not know whether anything moved.
_LINK_REASONS = ("timeout", "disconnected", "request_pending", "not_robot_face")


async def request(action: str) -> dict:
    """§2d steps 1–7, in order; the first that fails decides the result.

    Returns facts plus a Thai instruction: `ok` only when the robot accepted,
    `hardware` requested | refused | failed | mock — never "done".
    """
    from app.tools.registry import robot_face_session

    # 1. the vocabulary
    if action not in rc.BODY_ACTION_IDS:
        _record("body_refused", action=None, reason="bad_request", hardware="refused")
        return _result(str(action)[:32], "refused", "bad_request",
                       "ไม่มีท่านี้ในรายการ ห้ามบอกว่าทำแล้ว ให้บอกตรงๆ ว่าทำท่านี้ไม่ได้")
    stop = action == "stop"
    session = robot_face_session.get()

    # 2. the server's keys — a stop skips them: the state of a switch must
    #    never be the reason somebody cannot stop a machine (/arm's rule).
    if not stop and not _server_gate(action):
        return _refuse(session, action, "mock", "server_disabled")

    # 2b. a STOP on the desk's /arm disarms until somebody presses "arm" again
    #     (robot_arm.stop). The camera greeting and request_handshake honour
    #     it; the voice did not (review 12 Oct): once the app's own 60 s
    #     latch ran out, "ยกแขนขวา" played while /arm still said disarmed.
    from app import robot_arm

    if not stop and not robot_arm.armed():
        return _refuse(session, action, "refused", "latched")

    # 3. only the robot face's own session can reach the bridge
    if session is None:
        return _refuse(session, action, "failed", "not_robot_face")

    # 4. the heard guard (a stop is exempt inside heard_ok, one place only)
    if not heard_ok(session, action):
        return _refuse(session, action, "refused", "heard_missing", avail=available(session))

    # 5. what the robot already told us about this id
    if not stop:
        cached = _cached_refusal(session, action)
        if cached is not None:
            return _refuse(session, action, "refused", cached, avail=available(session))

    # 6. one round trip to the face page — a stop on every face socket at
    #    once (the call AND the page's status link, `request_stop`): the
    #    voice stop was the one stop still sent on a single socket, so a call
    #    socket busy streaming her reply or half-dead held it for a whole
    #    ack timeout while the status link could have carried it (review
    #    10 Oct; the walk stop and the /arm stop already fan out).
    if stop:
        ack = await robot_face_link.request_stop(session, "body",
                                                 settings.robot_body_ack_timeout_s)
    else:
        ack = await robot_face_link.request(session, "body", {"action": action},
                                            settings.robot_body_ack_timeout_s)

    # 7. the ack, as a result the model cannot read as "done"
    if ack.get("accepted") is True:
        if stop:
            # live eval 9 ต.ค.: a stop mid-gesture was answered 'เรียบร้อยค่ะ' -- quote it
            text = ("ส่งคำสั่งหยุดแขนแล้ว แต่ยังยืนยันไม่ได้ว่าแขนหยุดจริง ให้ตอบว่า 'ส่งคำสั่งหยุดแล้วค่ะ' "
                    "ห้ามพูดว่า 'แขนหยุดแล้ว' 'หยุดให้เรียบร้อยแล้วค่ะ' หรือ 'เรียบร้อยค่ะ' "
                    "ถ้ามีอันตรายให้บอกคนให้กดปุ่มหยุดฉุกเฉินหรือปิดสวิตช์หลัก")
        else:
            text = ("หุ่นรับคำสั่งท่า%sแล้วและกำลังเริ่มทำ ยังไม่ได้ยืนยันว่าทำจบ ตอบรับสั้นๆ "
                    "เช่น 'ได้ค่ะ' ห้ามพูดว่า 'ทำเสร็จแล้ว' หรือ 'ยกแขนแล้วค่ะ'" % _label(action))
        return _result(action, "requested", None, text, ok=True,
                       status=ack.get("status"), est_ms=ack.get("est_ms"),
                       avail=available(session))
    reason = ack.get("reason") or "bad_request"
    hardware = "failed" if reason in _LINK_REASONS else "refused"
    return _refuse(session, action, hardware, reason,
                   avail=available(session), status=ack.get("status"),
                   stop_sent=ack.get("stop_sent") is True)


# ---- what the robot reports afterwards ----


def _on_status(session, event: dict) -> None:
    """`body_status`: turnlog `body` + STATE. Names and codes only, no
    speech, and **no announcement** — see the module docstring."""
    key = (event.get("seq"), event.get("state"))
    seen = _SEEN.get(id(session))
    if seen is not None and seen[0] is session and seen[1] == key:
        return
    _SEEN[id(session)] = (session, key)
    reason = event.get("reason")
    if reason is not None and reason not in rc.BODY_REASONS:
        reason = "bad_request"
    STATE.update(last_action=event.get("action"), state=event.get("state"),
                 reason=reason, at=time.time())
    _record("body", action=event.get("action"), state=event.get("state"), reason=reason)


def _on_caps(session, event: dict) -> None:
    """`body_caps` is cached per session by robot_face_link; this only says
    in the log what the robot now offers, so "why did she refuse nod" is
    answerable from data/logs/ (counts and ids, nothing spoken)."""
    gestures = event.get("gestures") or {}
    _record("body_caps", enabled=event.get("enabled"),
            allow_direction_known=event.get("allow_direction_known") is True,
            playable=[a for a in rc.BODY_ACTION_IDS
                      if _playable(gestures.get(a), event.get("allow_direction_known") is True)])


def _on_closed(session, event: dict) -> None:
    entry = _SEEN.get(id(session))
    if entry is not None and entry[0] is session:
        del _SEEN[id(session)]


robot_face_link.on("body_status", _on_status)
robot_face_link.on("body_caps", _on_caps)
robot_face_link.on("session_closed", _on_closed)
