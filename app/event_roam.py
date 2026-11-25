"""Walking round the event by herself, and going home by herself.

Owner 5 Nov 2026: "เราปล่อยให้หุ่นเดินทั่วๆเองได้ไหม หรือให้กลับแท่นเองตามสถานการณ์". A scene with
`roam` in the event file: from stop to stop of a list staff have walked, a while at each. Not
wandering: the lidar cannot see what is under its plane (the bump by the balloons, chair wheels,
28 Oct) and the board's map has no tables, so only ground a person has checked is ground she
goes on.

She leaves a stop when nobody needs her: not while a guest is talking or she is, not during a
walk up to a guest (app/face_approach.py runs inside the same scene, between the walks), not
during a film.

She goes home on her own for two reasons, and comes back on her own:
* the battery under EVENT_ROAM_DOCK_BATTERY, until EVENT_ROAM_RESUME_BATTERY. She says so.
* nobody heard or faced her for EVENT_ROAM_IDLE_DOCK_S, until somebody is. She says nothing.

**A person still holds the leash.** Every leg is an event_walk: the staff page going quiet stops it
(5 s), and here it also turns this off for good: reopening a phone must not start a machine walking
(the same rule as face_approach). So does any human stop, a restart (nothing here survives one), and
the second leg in a row that does not arrive: a robot that keeps setting out into a problem is the
one thing that would make this worse than standing still. Staff get the reason on the page.

Never run on the robot yet.
"""
from __future__ import annotations

import asyncio
import logging
import time

from app import event_walk, turnlog
from app.config import settings

logger = logging.getLogger("condo_voice.event_roam")

TICK_S = 1.0
#: A guest's last word holds her this long: they are thinking, or she is about to answer.
GUEST_HOLD_S = 30.0
#: Somebody at the dock wakes her: seen or heard this recently.
WAKE_S = 5.0
MAX_FAILS = 2
#: Stops of a walk that a person chose or a dead man's switch made.
_ENDS_IT = {"staff", "voice", "desk", "control_lost", "face_gone", "shutdown", "replaced", "app_stop"}

PHASE_TH = {
    "off": "ปิด", "waiting": "รออยู่ที่จุดนี้", "busy": "มีแขกคุยอยู่ รอจนจบ", "walking": "กำลังเดินไปจุดถัดไป",
    "to_dock": "กำลังกลับแท่น", "charging": "ชาร์จแบตอยู่ เต็มแล้วเดินต่อเอง", "parked": "ไม่มีคนแถวนี้ พักที่แท่น มีคนมาแล้วเดินต่อเอง",
}
WHY_TH = {
    "staff": "ทีมงานกดหยุด", "voice": "มีคนสั่งหยุดด้วยเสียง", "desk": "กดหยุดจากหน้าคุมหุ่น", "scene": "เปลี่ยนฉาก",
    "control_lost": "หน้าคุมขาดการติดต่อ", "face_gone": "แอปบนหุ่นหลุด", "shutdown": "เซิร์ฟเวอร์ปิด",
    "replaced": "มีคำสั่งเดินอื่นมาแทน", "walk_trouble": "เดินไม่ถึงจุดติดกันสองครั้ง", "no_walk": "การเดินของงานปิดอยู่",
    "no_stops": "ไม่มีจุดที่เดินไปได้", "refused": "ระบบเดินไม่ยอมออกเดิน",
    "app_stop": "กดหยุดที่ตัวหุ่น",
    "ceremony_app": "หุ่นเปิดแอป Emma Ceremony อยู่",
}

STATE: dict = {"on": False, "scene": None, "stops": [], "i": -1, "phase": "off", "why": None, "why_th": None,
               "fails": 0, "seq": 0, "task": None, "dwell_s": 45.0, "last_seen": 0.0}


def reset() -> None:
    task = STATE.get("task")
    if task is not None and not task.done():
        try:
            task.cancel()
        except RuntimeError:             # its loop closed with an earlier test
            pass
    STATE.update(on=False, scene=None, stops=[], i=-1, phase="off", why=None, why_th=None, fails=0, seq=0,
                 task=None, dwell_s=45.0, last_seen=0.0)


def snapshot() -> dict:
    stops, i = STATE["stops"], STATE["i"]
    names = _names()
    return {"on": STATE["on"], "phase": STATE["phase"], "phase_th": PHASE_TH.get(STATE["phase"], STATE["phase"]),
            "why_th": STATE["why_th"], "stops": [names.get(s, s) for s in stops],
            "at": names.get(stops[i], stops[i]) if stops and 0 <= i < len(stops) else None}


def _names() -> dict:
    from app import event_host

    return {sid: spot["name_th"] for sid, spot in ((event_host.load() or {}).get("spots") or {}).items()}


def start(sc: dict) -> dict:
    """The scene's press. Refuses out loud: "she will wander" must not be believed when she won't."""
    from app import event_host

    roam = sc.get("roam") or {}
    spots = (event_host.load() or {}).get("spots") or {}
    stops = [s for s in roam.get("stops") or [] if event_host.spot_steps(spots.get(s)) is not None]
    why = ("ceremony_app" if event_host.ceremony_now()
           else "no_walk" if not settings.event_walk_enabled else "no_stops" if len(stops) < 2 else None)
    if why is not None:
        _set_off(why)
        return {"ok": False, "error": why, "error_th": WHY_TH[why]}
    STATE["seq"] += 1
    STATE.update(on=True, scene=sc["id"], stops=stops, i=-1, phase="waiting", why=None, why_th=None, fails=0,
                 dwell_s=float(roam.get("dwell_s", 45.0)), last_seen=time.monotonic())
    STATE["task"] = asyncio.create_task(_run(STATE["seq"]))
    turnlog.record("event_roam", phase="on", stops=len(stops))
    return {"ok": True, "stops": stops}


def _set_off(why: str) -> None:
    was_on = STATE["on"]
    STATE.update(on=False, phase="off", why=why, why_th=WHY_TH.get(why, why))
    STATE["seq"] += 1
    if was_on:
        turnlog.record("event_roam", phase="off", why=why)
        logger.info("roaming off: %s", why)


async def stop(why: str) -> None:
    """Off now. The walk under way, if any, is the caller's: `start_scene` and the staff stop each
    stop it themselves and say why."""
    task = STATE.get("task")
    if STATE["on"]:
        _set_off(why)
    if task is not None and not task.done() and task is not asyncio.current_task():
        task.cancel()


def _ceremony_on() -> bool:
    from app import event_host

    return bool(event_host.ceremony_now())


def page_alive() -> bool:
    beat = event_walk.STATE.get("heartbeat")
    return beat is not None and time.monotonic() - beat <= settings.event_walk_heartbeat_s


def _session():
    from app import session as session_module

    return session_module._active


def _guest_busy(now: float) -> bool:
    """Somebody is with her. She does not walk off in the middle of a sentence, theirs or hers."""
    from app import event_clips, face_approach

    if face_approach.STATE.get("phase") not in (None, "idle") or event_clips.active() or event_walk.walking():
        return True
    session = _session()
    if session is None:
        return False
    if session._array_audible_until(now) > now:
        return True
    return now - getattr(session, "_last_heard_at", -1e9) < GUEST_HOLD_S


def _someone_about(now: float) -> bool:
    from app import focus

    session = _session()
    if session is not None and now - getattr(session, "_last_heard_at", -1e9) < WAKE_S:
        return True
    try:
        age = focus.engaged_age(now)
    except Exception:                                    # noqa: BLE001
        return False
    return age is not None and age < WAKE_S


def _battery() -> float | None:
    view = event_walk.health_view()
    battery = view.get("battery") if view.get("known") and view.get("board") else None
    return battery if isinstance(battery, (int, float)) else None


async def _walk(spot_id: str, seq: int) -> str:
    """One leg. "arrived", "failed", "off" (this is over), or "busy" (a walk up to a guest got there first)."""
    from app import event_host

    doc = event_host.load() or {}
    spot = (doc.get("spots") or {}).get(spot_id)
    steps = await event_host.walk_steps(spot, doc)      # its big turns watched, as every walk to a spot
    done = asyncio.Event()
    result: dict = {}

    async def ended(run) -> None:
        result.update(state=run.state, reason=run.reason)
        if run.state == "arrived":
            event_host._count_arrival(run.spot)
        done.set()

    walk = await event_walk.start(spot_id, spot["name_th"] if spot else spot_id, steps, dock=event_host.dock_doc(doc),
                                  on_end=ended, autonomous=True)
    if not walk.get("ok"):
        if walk.get("error") == "busy":
            return "busy"
        if walk.get("error") == "low_battery":
            return "failed"
        turnlog.record("event_roam", phase="refused", why=walk.get("error"))
        STATE["why_th"] = walk.get("error_th")
        return "failed"
    await done.wait()
    if STATE["seq"] != seq:
        return "off"
    if result.get("state") == "arrived":
        return "arrived"
    if result.get("reason") in _ENDS_IT:
        _set_off(result["reason"])
        return "off"
    return "failed"


async def _home_and_wait(seq: int, why: str) -> bool:
    """Back on the dock, until the reason has gone. False = this is over."""
    from app import event_host

    if why == "charge":
        await event_host._say(event_host._system("แบตเหลือน้อย หุ่นกำลังกลับแท่น",
                                                 "บอกแขกที่อยู่ใกล้ๆ สั้นๆ หนึ่งประโยคว่าขอตัวไปชาร์จแบตเตอรี่สักครู่ เป็นภาษาของบทสนทนา"),
                              "event_roam_home", lambda: STATE["seq"] == seq)
    STATE["phase"] = "to_dock"
    leg = await _walk("dock", seq)
    if leg != "arrived":
        if leg == "failed":
            _set_off("walk_trouble")
        return False
    STATE["phase"] = "charging" if why == "charge" else "parked"
    STATE["i"] = -1
    turnlog.record("event_roam", phase=STATE["phase"])
    while STATE["seq"] == seq:
        await asyncio.sleep(TICK_S)
        if not page_alive():
            _set_off("control_lost")
            return False
        if event_walk._app_latched():
            # B while she charges or is parked: past its 60 s she would set out again (review 6 Nov).
            _set_off("app_stop")
            return False
        if _ceremony_on():
            _set_off("ceremony_app")                     # parked through the ceremony, not set out after it
            return False
        now = time.monotonic()
        if why == "charge":
            battery = _battery()
            if battery is not None and battery >= settings.event_roam_resume_battery:
                break
        elif _someone_about(now):
            break
    STATE["last_seen"] = time.monotonic()
    return STATE["seq"] == seq


async def _run(seq: int) -> None:
    try:
        ready_at = time.monotonic() + float(settings.event_roam_first_wait_s)
        while STATE["seq"] == seq:
            await asyncio.sleep(TICK_S)
            if STATE["seq"] != seq:
                return
            now = time.monotonic()
            if not page_alive():
                _set_off("control_lost")
                return
            if event_walk._app_latched():
                # B on the robot while she waits at a stop: off, as any person's stop (review 6 Nov).
                _set_off("app_stop")
                return
            if _ceremony_on():
                # The Emma Ceremony app opened (owner 9 Nov, "ไม่งั้นจะปนกัน"): the roam is the Event
                # app's, and its next stop must not be walked through the ceremony. The claim stops
                # it too (app/ceremony.py); this is the loop's own word, every tick.
                _set_off("ceremony_app")
                return
            if _someone_about(now):
                STATE["last_seen"] = now
            battery = _battery()
            if battery is not None and battery < settings.event_roam_dock_battery and not _guest_busy(now):
                if not await _home_and_wait(seq, "charge"):
                    return
                ready_at = time.monotonic()
                continue
            limit = settings.event_roam_idle_dock_s
            if limit > 0 and now - STATE["last_seen"] > limit and not _guest_busy(now):
                if not await _home_and_wait(seq, "idle"):
                    return
                ready_at = time.monotonic()
                continue
            if _guest_busy(now):
                STATE["phase"] = "busy"
                ready_at = max(ready_at, now + 5.0)     # a moment after they go
                continue
            STATE["phase"] = "waiting"
            if now < ready_at:
                continue
            stops = STATE["stops"]
            # After a leg that did not arrive, the stop after it (25 Nov, the after-ceremony crowd): a guest
            # standing on a stop made the board give up on it, and the same stop again a minute later was the
            # second failure that ended the roam. Two in a row still end it (MAX_FAILS), now at two places.
            i = (STATE["i"] + 1 + STATE["fails"]) % len(stops)
            STATE["phase"] = "walking"
            leg = await _walk(stops[i], seq)
            if leg == "off":
                return
            if leg == "busy":
                continue
            if leg == "failed":
                STATE["fails"] += 1
                if STATE["fails"] >= MAX_FAILS:
                    _set_off("walk_trouble")
                    return
                ready_at = time.monotonic() + STATE["dwell_s"]
                continue
            STATE.update(i=i, fails=0, phase="waiting")
            ready_at = time.monotonic() + STATE["dwell_s"]
    except asyncio.CancelledError:
        raise
    except Exception:                                    # noqa: BLE001 -- a loop that dies says so
        logger.exception("roaming stopped")
        _set_off("refused")
