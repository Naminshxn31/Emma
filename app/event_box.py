"""She is in the box: an ARM for the one walk out, and a LATCH on every arm path (owner 23 Nov 2026: "หุ่นอยู่ในกล่อง
อาจจะต้องให้หุ่นเดินออกมาก่อน จึงค่อยยกมือนะ"; reviews 23 Nov).

The walk out of the box is single use, and only a person can say she is in the box (second review, 23 Nov: four ways
the server took her to be in the box when she was out, or partly out -- a pose jump, out but not seen still, a restart,
a staff clear after a lift-out -- and the opening or card 1 walked another whole forward_m from outside it):

* ARMED by an explicit staff statement that she is in the box -- the ceremony order's tick "ไฟห้องปิด ม่านปิด หุ่นพร้อมใน
  กล่อง" (`"in_box": true` on that manual step) or the Event page's two-tap "หุ่นอยู่ในกล่องแล้ว" (`arm`). Arming also SETS
  the latch: from "she is in the box" on, no beat, no camera handshake, no manual gesture.
* A PRESS CONSUMES THE ARM (`press`, before anything moves; kept on disk). Only "positively never moved" gives it back
  (`never_moved`): no walker started -- refused, cut in the build or the arm's rest, walking off. Every other outcome
  leaves it disarmed (`outcome`): set out and cut (`moved`, how far from the walker's own closing line), a pose jump or
  a killed walker or a restart (`unknown`), out but the wheels not seen still (`not_still`), out and still (`out`).
* Disarmed = card 1's walk out and the opening are refused (NOT_ARMED_TH). Resetting the ceremony order does not arm,
  nor does its "ถัดไป": the tick takes its own button's two taps (event_order.next). Partly out (`partial`) or a press
  under way = staff's word is refused (event_host.box_arm): out first (finish, or by hand + clear), then in again.

The LATCH (`held`) is what every arm path asks -- her talking arm (speech_gestures), every body request of the voice and
of staff (robot_body: the order's body steps, the cards' buttons, a fallback wave), the camera's handshake (robot_arm),
the pageant (event_pageant) -- and only for the Emma Event / Emma Ceremony apps' calls (`holds(session)`): the sales app
is never touched by it. The arm's REST and every stop still go. It holds the WHEELS too (third review, 23 Nov):
event_walk.start refuses every walk but the walk out of the box (`in_box`) -- for any caller, a MoveTo from inside it
hits it. It lets go when a walk out walked ALL of its forward_m
(event_walk.walked_m: the walker's closing line, never board_walk's "short but done") and the wheels stood still, or by
staff's two taps "หุ่นออกจากกล่องแล้ว — ปลดล็อกแขน" (`clear`, which disarms too).

Both expire at MIC_HOLD_RESET_HOUR the next morning, by the mic hold's own clock (wake._next_reset: local time of the
server, which the robot's launcher runs with TZ=ICT-7 -- Thai, not Android's China clock): a box from yesterday is
nobody's today.
Kept in the run folder (box.json): a restart neither re-arms her nor forgets she set out -- a press found under way at
the next start is `unknown`. Nothing here has run on the robot.
"""
from __future__ import annotations

import json
import logging
import time

from app import event_walk, turnlog

logger = logging.getLogger("condo_voice.event_box")

#: What every arm path that refuses says (robot_contracts.body_reason_th "in_box").
REASON = "in_box"
HELD_TH = "หุ่นยังอยู่ในกล่อง — ไม่ขยับแขน (แขนจะชนกล่อง)"
CLEAR_TH = "หุ่นออกจากกล่องแล้ว — ปลดล็อกแขน"
ARM_TH = "หุ่นอยู่ในกล่องแล้ว"
#: Card 1 / the opening pressed with no arm.
NOT_ARMED_TH = ("หุ่นออกจากกล่องไปแล้ว / ไม่แน่ใจว่าออกไปแล้ว — ถ้าหุ่นกลับเข้ากล่องจริงแล้ว กด 'หุ่นอยู่ในกล่องแล้ว' ก่อน "
                "(ออกไปบางส่วน: 'เดินออกให้ครบ' หรือปลดล็อกก่อน)")
#: ...on the ceremony page: only the tick's own button, two taps, arms it -- never "ถัดไป", never a reset of the order
#: (third review, 23 Nov: reset + "ถัดไป" went through the tick in one tap each)
NOT_ARMED_CEREMONY_TH = ("หุ่นออกจากกล่องไปแล้ว / ไม่แน่ใจว่าออกไปแล้ว — เปิดตัวซ้ำไม่ได้ (จะเดินออกอีกเต็มระยะจากที่ยืน) · ถ้าพาหุ่น"
                         "กลับเข้ากล่องจริงแล้ว แตะปุ่มของขั้น 'ไฟห้องปิด ม่านปิด หุ่นพร้อมในกล่อง' สองครั้ง (เริ่มลำดับใหม่ "
                         "หรือกด 'ถัดไป' ไม่ยืนยันให้)")
#: Staff's "she is in the box" while a walk out stopped part way, or nobody knows how far it went (third review, 23 Nov:
#: arming then blanked the distance walked, and the next press walked another whole forward_m from half out).
ARM_PARTIAL_TH = ("หุ่นออกจากกล่องไปบางส่วนแล้ว / ไม่รู้ว่าออกไปเท่าไหร่ — ยืนยันว่าอยู่ในกล่องตอนนี้ไม่ได้ · ใช้ 'เดินออกให้ครบ' "
                  "(ถ้ามี) หรือพาออกเองแล้วกด 'หุ่นออกจากกล่องแล้ว — ปลดล็อกแขน' ก่อน · ถ้าพาหุ่นกลับเข้ากล่องจริง: กดปลดล็อก"
                  "ก่อน แล้วค่อยยืนยันว่าอยู่ในกล่องใหม่")
#: The apps whose calls the latch holds (the Emma Event and the Emma Ceremony apps' profiles).
EVENT_PROFILES = ("event", "ceremony")
#: A walk out that walked this little did not move her (pose noise at a standstill).
MOVED_M = 0.03
#: How she left the box, the last press (or staff) said: None = nothing since the arm; "pending" = a press under way.
LEFT = (None, "pending", "moved", "unknown", "not_still", "out", "cleared")
_KEPT = ("in", "since", "why", "walked_m", "of_m", "unknown", "armed", "armed_by", "armed_at", "left", "pressed",
         "until")

STATE: dict = {"loaded": False}


def _blank() -> dict:
    return {"in": False, "since": None, "why": None, "walked_m": 0.0, "of_m": None, "unknown": False, "armed": False,
            "armed_by": None, "armed_at": None, "left": None, "pressed": None, "until": None}


STATE.update(_blank())


def reset() -> None:
    """Tests (conftest): nothing in memory, the file read again on the next ask (= a restart)."""
    STATE.clear()
    STATE.update(loaded=False, **_blank())


def _path():
    return event_walk.RUN_DIR / "box.json"


def _num(v, default=None):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _load() -> None:
    if STATE["loaded"]:
        _expire()
        return
    STATE["loaded"] = True
    try:
        doc = json.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except (OSError, ValueError):
        # a file that says something we cannot read: in the box and not armed (the side that keeps an arm down and
        # walks nobody another metre)
        logger.exception("event box: the latch file could not be read -- taken as in the box, not armed")
        STATE.update(_blank(), **{"in": True}, since=time.time(), why="unreadable", left="unknown", unknown=True,
                     until=_next_reset(time.time()))
        return
    if not isinstance(doc, dict):
        return
    left = doc.get("left") if doc.get("left") in LEFT else None
    STATE.update({"in": doc.get("in") is True, "since": _num(doc.get("since")), "why": doc.get("why"),
                  "walked_m": _num(doc.get("walked_m"), 0.0), "of_m": _num(doc.get("of_m")),
                  "unknown": doc.get("unknown") is True, "armed": doc.get("armed") is True,
                  "armed_by": doc.get("armed_by"), "armed_at": _num(doc.get("armed_at")), "left": left,
                  "pressed": doc.get("pressed"), "until": _num(doc.get("until"))})
    if STATE["left"] == "pending":
        # a press under way when the server went (Android's resets): the walk it asked for may have set out -- how far
        # is not known, and the arm is not given back (second review, 23 Nov)
        STATE.update(left="unknown", unknown=True, armed=False, **{"in": True})
        _save()
        turnlog.record("event_box", action="restart_unknown")
    _expire()


def _next_reset(now: float) -> float:
    from app import wake

    return wake._next_reset(now)


def _expire() -> None:
    until = STATE.get("until")
    if until is not None and time.time() >= until and (STATE["in"] or STATE["armed"] or STATE["left"]):
        # the morning after (MIC_HOLD_RESET_HOUR): yesterday's box is nobody's today
        STATE.update(_blank())
        _save()
        turnlog.record("event_box", action="expired")


def _save() -> None:
    try:
        event_walk.RUN_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _path().with_suffix(".tmp")
        tmp.write_text(json.dumps({k: STATE[k] for k in _KEPT}), encoding="utf-8")
        tmp.replace(_path())
    except OSError:
        logger.exception("event box: the latch could not be kept")


# ---- the questions ------------------------------------------------------------------------------------------------

def held() -> bool:
    """The latch: she may be in the box -- no arm (the event code's own question; arm paths ask `holds`)."""
    _load()
    return bool(STATE["in"])


def holds(session) -> bool:
    """Every arm path's question: the latch, for a call of the Emma Event or the Emma Ceremony app only -- the sales
    app (and any desk) never reads it (review 23 Nov: a leftover latch silently took sales Emma's arm)."""
    if getattr(session, "profile", None) not in EVENT_PROFILES:
        return False
    return held()


def armed() -> bool:
    """Staff said she is in the box and no press has used that since: the one walk out may be pressed."""
    _load()
    return bool(STATE["armed"])


def partial() -> bool:
    """A press set out and did not get her out (or nobody knows): neither in nor out."""
    _load()
    return bool(STATE["in"]) and STATE["left"] in ("moved", "unknown", "not_still")


def pending() -> bool:
    """A press of the walk out under way (its arm used, its walk not ended)."""
    _load()
    return STATE["left"] == "pending"


def tick_th(of_m: float | None) -> str:
    """What the ceremony's in-box tick does, on its step (third review, 23 Nov: it arms ONE walk out of X m)."""
    walk = ("ตรงไปข้างหน้า %.2f ม." % float(of_m)) if isinstance(of_m, (int, float)) and of_m > 0 else "ระยะของฉากเปิดตัว"
    return ("ติ๊กนี้ยืนยันว่าหุ่นอยู่ในกล่องจริง: แขนล็อก และขั้นเปิดตัวเดินออกได้ 1 ครั้ง (%s) · ใช้แล้วหมด · แตะสองครั้ง" % walk)


def left_m() -> float | None:
    """What is left of a walk out that set out and stopped short, measured -- or None (none, not known, all walked)."""
    _load()
    if not STATE["in"] or STATE["left"] != "moved" or STATE["unknown"] or not STATE["of_m"]:
        return None
    return max(0.0, round(float(STATE["of_m"]) - float(STATE["walked_m"] or 0.0), 2))


def out_known() -> bool:
    """Not in the box and not armed after a press or staff's word: the card's steps are by hand from here."""
    _load()
    return not STATE["in"] and not STATE["armed"] and STATE["left"] in ("out", "cleared")


# ---- what people and walks say ------------------------------------------------------------------------------------

def arm(by: str) -> None:
    """Staff: she is in the box (the ceremony's tick, the Event page's "หุ่นอยู่ในกล่องแล้ว"). The one walk out may be
    pressed, and the latch holds from now -- until the next MIC_HOLD_RESET_HOUR at most."""
    _load()
    now = time.time()
    STATE.update(_blank(), **{"in": True}, since=now, why="armed:" + str(by)[:24], armed=True, armed_by=str(by)[:24],
                 armed_at=now, until=_next_reset(now))
    _save()
    turnlog.record("event_box", action="armed", by=str(by)[:24])


def press(why: str, of_m: float | None) -> bool:
    """A walk out pressed: the arm is used NOW (on disk -- a restart before its end finds it `unknown`). False = not
    armed: refuse the press."""
    _load()
    if not STATE["armed"]:
        return False
    STATE.update(armed=False, pressed=str(why)[:24], left="pending", walked_m=0.0, unknown=False,
                 of_m=_num(of_m), **{"in": True}, until=_next_reset(time.time()))
    _save()
    turnlog.record("event_box", action="pressed", why=str(why)[:24])
    return True


def never_moved() -> None:
    """The press's walk never started (refused, cut before its walker began, walking off): the arm comes back."""
    _load()
    if STATE["left"] != "pending":
        return
    STATE.update(armed=True, left=None, pressed=None, walked_m=0.0, unknown=False)
    _save()
    turnlog.record("event_box", action="never_moved")


def outcome(kind: str, walked: float | None = None) -> None:
    """How a walk out that SET OUT ended: "out" (all of it, the wheels still: the latch goes), "not_still" (all of it,
    not seen still), "moved" (stopped short: `walked` from its closing line, added to what earlier walks walked),
    "unknown" (how far is not known). Never armed after it."""
    _load()
    if kind == "out":
        STATE.update(**{"in": False}, armed=False, left="out", why="walked_out", unknown=False)
    elif kind == "not_still":
        STATE.update(**{"in": True}, armed=False, left="not_still",
                     walked_m=round(_num(walked, 0.0) or float(STATE["of_m"] or 0.0), 3))
    elif kind == "moved" and walked is not None:
        STATE.update(**{"in": True}, armed=False, left="moved",
                     walked_m=round(float(STATE["walked_m"] or 0.0) + max(0.0, float(walked)), 3))
    else:
        STATE.update(**{"in": True}, armed=False, left="unknown", unknown=True)
    STATE["until"] = STATE["until"] or _next_reset(time.time())
    _save()
    turnlog.record("event_box", action=kind if kind in ("out", "not_still", "moved") else "unknown",
                   walked_m=STATE["walked_m"], of_m=STATE["of_m"])


def walk_ended(run, of_m: float | None, *, tol_m: float, first_step: bool = False) -> str:
    """A walk out (or the rest of one) ended: where that leaves her, from the walker's own lines. Returns the kind:
    "never" (no walker started: the arm back), "full" (all of it walked: the caller checks the wheels and says "out"
    or "not_still"), "moved", "unknown". `first_step`: the walk out is only the walker's first step (card 1's walk out
    and on to the corner in one, owner 25 Nov ~04:25) -- its closing line says how far it went, whatever came after."""
    if not getattr(run, "saw_started", False):
        never_moved()
        return "never"
    walked = event_walk.walked_m(run)
    want = float(of_m or 0.0)
    ended_ok = getattr(run, "state", None) == "arrived" or first_step
    if ended_ok and walked is not None and want > 0 and walked >= want - tol_m:
        outcome("not_still", STATE["walked_m"] + walked if STATE["left"] == "moved" else walked)
        return "full"                                    # (the wheels' word turns it "out")
    if walked is None:
        outcome("unknown")
        return "unknown"
    outcome("moved", walked)
    return "moved"


def clear(why: str) -> bool:
    """Staff: she is out of the box (moved by hand, or never in one) -- the latch goes, and no walk out is armed."""
    _load()
    was = bool(STATE["in"])
    left = STATE["left"] if STATE["left"] == "out" else "cleared"
    STATE.update(_blank(), why=why, left=left, until=_next_reset(time.time()))
    _save()
    if was:
        turnlog.record("event_box", action="cleared", why=why)
    return was


# ---- the pages ----------------------------------------------------------------------------------------------------

def view() -> dict:
    """For both staff pages: {"in", "armed", "partial", "left", "walked_m", "of_m", "left_m", "unknown", "text_th",
    "clear_th", "arm_th", "until"}."""
    _load()
    out = {"in": bool(STATE["in"]), "armed": bool(STATE["armed"]), "partial": partial(), "left": STATE["left"],
           "walked_m": round(float(STATE["walked_m"] or 0.0), 2), "of_m": STATE["of_m"], "left_m": left_m(),
           "unknown": bool(STATE["unknown"]), "until": STATE["until"], "clear_th": CLEAR_TH, "arm_th": ARM_TH,
           "out_known": out_known()}
    if not STATE["in"]:
        out["text_th"] = ("หุ่นออกจากกล่องแล้ว — แขนใช้ได้ · ทำส่วนที่เหลือด้วยปุ่มทำเอง" if out["out_known"]
                          else "ยังไม่ได้บอกว่าหุ่นอยู่ในกล่อง — กด 'หุ่นอยู่ในกล่องแล้ว' ก่อนเปิดตัว")
    elif STATE["armed"]:
        out["text_th"] = "หุ่นอยู่ในกล่อง (ทีมงานยืนยันแล้ว) — แขนล็อก · พร้อมเดินออกครั้งเดียว"
    elif STATE["left"] == "pending":
        out["text_th"] = "กำลังเปิดตัว — แขนล็อกจนเดินออกครบและล้อนิ่ง"
    elif STATE["left"] == "unknown":
        out["text_th"] = ("ไม่แน่ใจว่าหุ่นออกจากกล่องไปแล้วเท่าไหร่ (ล้อหมุนแต่ไม่รู้ระยะ / ตำแหน่งกระโดด / เซิร์ฟเวอร์เริ่มใหม่) — "
                          "แขนยังล็อก · ดูหุ่นจริง พาออกเองแล้วกดปลดล็อก")
    elif STATE["left"] == "not_still":
        out["text_th"] = ("หุ่นเดินออกครบระยะแล้ว แต่ไม่เห็นล้อหยุดนิ่ง — แขนยังล็อก · ดูหุ่นจริงแล้วกด 'หุ่นออกจากกล่องแล้ว — "
                          "ปลดล็อกแขน'")
    elif STATE["left"] == "moved":
        out["text_th"] = ("หุ่นเริ่มเดินออกแล้วหยุด (ได้ %.2f จาก %.2f ม.) — แขนยังล็อก · กด 'เดินออกให้ครบ' หรือพาออกเองแล้วกด"
                          "ปลดล็อก" % (out["walked_m"], float(STATE["of_m"] or 0.0)))
    else:
        out["text_th"] = "หุ่นอยู่ในกล่อง — แขนล็อก"
    return out


def readiness_row() -> dict:
    """The morning card's row (app/event_readiness.py): what the box says now -- information, never NO-GO."""
    v = view()
    th = ("กล่อง: หุ่นอยู่ในกล่อง พร้อมเดินออก" if v["armed"] else "กล่อง: แขนล็อก (หุ่นอยู่/อาจอยู่ในกล่อง)" if v["in"]
          else "กล่อง: ยังไม่ได้ยืนยันว่าหุ่นอยู่ในกล่อง")
    detail = v["text_th"]
    if v["until"]:
        detail += " · ล้างเองตอน %s" % time.strftime("%d/%m %H:%M", time.localtime(v["until"]))
    return {"ok": None, "th": th, "detail_th": detail}
