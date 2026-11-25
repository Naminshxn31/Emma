"""Card 1's whole opening in one press: the debut, the counter, the guests and back (owner 23 Nov 2026).

Owner, 23 Nov 2026: "การ์ดแรก ให้หุ่นเปิดตัวตามที่ซ้อมไว้แล้วก็พูดทักทายกับท่านางงาม จากนั้นไปหน้าเคาน์เตอร์แล้วจับมือมิกิ
แล้วหันหลัง ไปจับมือกับแขกสักสามจุดใกล้ๆตรงนั้น แล้วก็กลับมา ไม่ต้องให้หุ่นขยับหัวนะ แล้วก็กลับมาหน้าเคาน์เตอร์เพื่อคุยกับ
คุณมิกิ" -- ONE press of card 1 does all of it; staff stop it only when something is wrong. The talk with Khun Miki
after it is the ceremony's (the Emma Ceremony app), not this.

What one press does (a scene's `tour` in the event file, EVENT_TOUR on -- robot.env):
1. the scene itself (event_host.start_scene): since 23 Nov (owner: "หุ่นอยู่ในกล่อง ... เดินออกมาก่อน จึงค่อยยกมือ") the
   debut walks out of the box with the arms down, and only out and still raises the pageant and says the recorded
   line (the scene's `walk_out_of_box`); not out = no wave and no tour;
2. once that walk ARRIVED and its line and wave are over: a walk to the counter (`counter`: card 2's
   beside_host) and the handshake there (Khun Miki) -- by the tour's `via` points when it names some (owner 24 Nov: "จุดที่
   ออกตัวไปจุดแรกให้ตั้งฉากนะ ไปยืนแนวนั้นแล้วก็ค่อยหันขวา": straight on out of the box to the line of Khun Miki's spot,
   stop, turn right, straight up that line);
3. for every guest spot in `guests` (marked beforehand: a robot pose facing where that guest stands), one after
   another: a walk there, the handshake;
4. a walk back to the counter (its spot faces the counter: Khun Miki) -- or to the tour's `end` spot when it names one
   that is marked (owner 24 Nov: "สุดท้ายก็เดินมาข้างคุณมิกิที่เคาร์เตอร์", a place of its own). No handshake.
Nothing is said at a handshake, except a spot the tour gives a recorded line (`lines`; owner 24 Nov: "จับมือแล้วหุ่นก็พูดว่า
hello ka khun miki"): it plays as the hand goes out, and the next walk waits for her voice. Each handshake
is waited out -- the app's own `done` for it, after its slow lowering and group 99 -- before the next walk, whose
own arm rest (event_walk._arm_rested) is asked as for every walk. A leg whose first point lies behind her (more
than TURN_FIRST_DEG off her heading: the counter -> the first guest, the last guest -> the counter) starts with a
watched slow turn on the spot (board_walk `turn` + `w_spin`, card 3's rule of 22 Nov: the lidar sees nothing
behind her, and the board's own MoveTo would turn her round at its own speed).

A guest spot not recorded = no round of the marked spots (never a walk to a spot nobody measured, never a round with a
gap in it): instead ONE guest card 3's way, when the tour names its `walk_up` spot (owner 23 Nov: "ส่วนจุดแขกตอนนี้ให้
หุ่นหันหลังแล้วเดินไปตรงๆก็เจอคนแล้วทักทายได้ไหม") -- guest_front's own steps: turned round slowly at the counter, straight
ahead slowly up to whoever stands there (board_walk `forward` + `meet_m`), the hand only to someone met (board_walk's
`@@ met`; nobody within its metres = no hand), then back to the counter facing it. No `walk_up`: she stays at the
counter after Khun Miki's hand. A counter not recorded = the debut alone. The staff page says which before the
press (`planned`, event_host.snapshot).

It ends -- for good: nothing carries it on by itself, a restart included -- at the first of: a person's stop
(the red button, a voice "หยุด", the desk's: event_walk.stops()), another press (a scene, a spot, home, the dock:
event_host STATE["seq"]), a walk it did not start, the staff page quiet (event_roam.page_alive), the robot face
gone (event_walk.FACE_GRACE_S), the app's stop latch (B), the Emma Ceremony app, EVENT_TOUR turned off, a leg
refused or not arriving (its own dead man's switches end it first), a handshake refused / failed / stopped / never
answered, a server restart (tour.json: the page says where it was cut). The card says which step it was on and
what is left; staff finish by hand with the "สั่งเอง ทีละอย่าง" card (a walk to a spot, the handshake).

While it runs -- from the press to its end -- her head is still (owner: "ไม่ต้องให้หุ่นขยับหัวนะ"): head_follow
sends no look at all (its face loop, its look down, its lidar look, its walk scan and a face approach's hint all
go through it), the camera's greetings wait (their handshake is group 6, which turns the head too), her talking
arm keeps off (speech_gestures), and a voice's gesture or handshake is refused busy -- the tour's own handshake is
staff's press (robot_body.staff_request). The pageant's own head sway on the debut is the app's (Pageant.java).

Two owners, one card (owner 23 Nov: "ช่วงพิธีอยู่แอปพิธีทั้งหมด"): the Event page's card 1 (`pressed` / `debut_ended`, owned
by event_host's press), and the ceremony's opening step (`begin_ceremony`, owned by app/event_order.py's press of an
`opening` step in the Emma Ceremony app -- with the room screen's debut first (its build, black, the hit; the owner
took the countdown out on 23 Nov evening) and the walk out of the box on its
hit; see the section below). The same steps after the debut, the same holds, the same ends.
Never run on the robot yet.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import time

from app import event_walk, turnlog
from app.config import settings

logger = logging.getLogger("condo_voice.event_tour")

#: A leg whose first point lies more than this off her heading starts with a watched slow turn to face it: the
#: point is behind her, outside the lidar's arc (-101..+108 deg, scripts/floor_sim.py), and the board's MoveTo would
#: turn her round itself. Not 90: a guest beside the last one is ~90 deg off, and pose noise flipped that leg between
#: the board's turn and ours run to run in the simulator.
TURN_FIRST_DEG = 100.0
#: That turn's rate (rad/s): card 3's (guest_front, 22 Nov), inside board_walk's TURN_W_SPIN_MIN..MAX.
TURN_W_SPIN = 0.3
#: A first point nearer than this: no turn of ours -- the board's own last turn to the spot's heading.
TURN_MIN_M = 0.30
#: A walk that ENDS IN THE HANDSHAKE at a spot with `head_on_m` (the event file's beside_host: Khun Miki's counter) comes
#: the last of the way straight in (owner 23 Nov, after card 1 ran on the robot: "ตอนเดินปรับเป็นเดินให้ไกลกว่าโต๊ะเคาน์เตอร์
#: แล้วค่อยเดินเข้ามาจับมือกับคุณมิกิ" -- that run went from the dock's approach point north along the counter's front
#: and turned to the table on the spot, (-8.57, -4.26) -1 deg): first to a point `head_on_m` back from where the spot
#: leaves her, along its heading and already facing it, then the spot's own last point straight ahead -- people see
#: her walk up to him head-on. Computed from the spot's end, so a pose recorded at the robot keeps it. Never past this.
HEAD_ON_MAX_M = 2.0
#: A turn of ours larger than this is two turns of half, the same way round (23 Nov, the simulator): at ~180 deg
#: the shorter way flips with a tenth of a degree of pose noise, and board_walk's turn (robot_lanes.turn_command)
#: stops each time the error changes sign ("past it: stop first") -- the reception -> the multimedia door from her
#: recorded pose (exactly 180 deg) dithered for 10 s and ran out of its 20 s in 5 of 20 runs. Each half is ~90 deg:
#: one way only, and each its own 20 s.
TURN_SPLIT_DEG = 150.0
#: A tour's `via`: at most this many points, each [x, y, heading]: she stops there, turns to the heading, then goes on.
VIA_MAX = 3
#: ... and the first of them within this of the way she stands when the walk sets out, or it is left out.
VIA_AHEAD_DEG = 25.0
#: A tour's `glide` (owner 24 Nov, after the first whole round: "การเดินเข้าไปแต่ละจุดช้า เหมือนต้องวัดหรืออะไร ทำให้เนียบเลย"):
#: its walks after the debut are board_walk's `glide` steps -- our own straight lines and turns -- at this speed (m/s)
#: instead of the board's MoveTo and its hunting arrivals. Kept within these.
GLIDE_MIN, GLIDE_MAX = 0.15, 0.50
#: The round's last walk: stopped this near its point by something ahead is there (board_walk GLIDE_SHORT_MAX_M).
END_SHORT_OK_M = 0.6
#: The turn toward the guests before the wave (scene `wave_face`): its cruise rate, rad/s (card 3's turns beside people).
WAVE_TURN_W = 0.3
#: The music's way down (the hall page's duck, ~0.4 s) before her line starts.
DUCK_LEAD_S = 0.4


async def _corner_ahead(sc: dict):
    """(x, y, face, speed) of the tour's corner when the debut waves there (`wave_at_via`, owner 25 Nov ~03:10) and it lies
    ahead of where she stands in the box (read before the walk out: that heading is also the first leg's `via_heading`)
    -- or None: no `wave_at_via`, no tour (EVENT_TOUR off, no counter), no corner, not ahead (the box moved), no pose."""
    face = sc.get("wave_face")
    if not sc.get("wave_at_via") or not settings.event_tour or not isinstance(face, (int, float)) or isinstance(face, bool):
        return None
    from app import event_host

    tour = parse(sc.get("tour"), (event_host.load() or {}).get("spots") or {}) or {}
    via = tour.get("via") if tour.get("counter") else None
    at = await _where()
    if not via or at is None or not via_ahead(via, at):
        return None
    STATE["via_heading"] = at[2]
    return float(via[0][0]), float(via[0][1]), float(face), float(tour.get("glide") or event_host.DEFAULT_SPEED)
#: The catwalk music is off this long before Khun Miki's hand goes up (owner 24 Nov: "ตอนยกมือก็ดับเพลงก่อน").
HAND_AFTER_MUSIC_S = 0.8
#: A handshake the app says is over within this of its start ended by the app's own #AGF. Its blind timer (no answer
#: from the arm) would say `done` no earlier than ~29 s (BodyController: 5 s hold + 3 s lowering + 1 s + 15 s, 24 Nov's
#: handshakes took 20): that one does not stand for the arm at rest.
HANDSHAKE_SURE_S = 24.0
#: From the press the head, the greetings and the talking arm are held this long at most before the debut's walk
#: has ended (its 45 s patience, the line, the wave): the runner refreshes it from there, HOLD_S at a time -- a
#: tour whose task died lets go by itself.
DEBUT_HOLD_S = 240.0
#: Longer than any one await of the run between two ticks: a leg's start (the call 8 s, the arm's rest 12 s, the
#: walker's gate 6 s) or a handshake's request (the call 8 s, its retries 6 s and the ack).
HOLD_S = 60.0
#: After the debut's walk: its line and its hand (and her voice) are waited for this long at most -- then the first
#: leg's own arm rest brings any hand down (event_walk._arm_rested -> event_pageant.end).
DEBUT_DONE_WAIT_S = 60.0
#: The handshake: the app's "started" within HANDSHAKE_START_S of the accepted request, its terminal row within
#: HANDSHAKE_DONE_S (group 6 ~13 s -- its own fallback 30 s --, the hold 5 s, the slow lowering 3 s, group 99 up to
#: 15 s: BodyGestures / BodyController).
HANDSHAKE_START_S = 8.0
HANDSHAKE_DONE_S = 60.0
#: A leg whose wheels have not moved at all (WHEELS_MOVED_M / WHEELS_MOVED_DEG of the board's pose) this long after
#: its walker set out ends the tour (review 23 Nov): the board does not report the robot's E-stop -- pressed during a
#: handshake and let go within the leg's 45 s patience, she used to drive on to the next guest by herself. Someone
#: standing right in front of her start looks the same: the card ends, staff go on by hand.
WHEELS_STILL_END_S = 15.0
# (review 23 Nov: 8 s ended the card when a guest stood in front of her at a crowded counter as a leg began. The board
# cannot tell that guest from the robot's E-stop -- both are a MoveTo "held", wheels still -- so the rule cannot wait
# for "not held"; 15 s lets a guest step aside, and still catches an E-stop left in. One released within it drives on:
# the card tells staff to press the red stop after an E-stop.)
WHEELS_MOVED_M = 0.03
WHEELS_MOVED_DEG = 3.0
#: A handshake's recorded line: her voice is waited out this long at most after the hand is down, then the next walk.
LINE_OVER_S = 15.0
#: How often the board's pose is read for that during a leg.
POSE_EVERY_S = 0.5
TICK_S = 0.2
GREETER_KEY = "event_tour"

#: Why it ended, for the phone. A key of event_walk.REASONS_TH is said in its words.
WHY_TH = {
    "done": "ครบทุกขั้น — หุ่นยืนที่จุดสุดท้ายของรอบ",
    "done_counter": "จับมือคุณมิกิแล้ว ยืนรอที่หน้าเคาน์เตอร์ (ไม่ไปหาแขก: จุดแขกยังบันทึกไม่ครบ)",
    "not_run": "ไม่ได้ไปต่อ",
    "walking_off": "การเดินปิดอยู่ — เปิดตัวตรงที่หุ่นยืน ไม่ไปเคาน์เตอร์/แขก",
    "debut_refused": "เดินเปิดตัวไม่ได้ จึงไม่ไปต่อ",
    "debut_cut": "เดินเปิดตัวไม่ถึง (ออกจากกล่องไม่ได้?) — ไม่โบก ไม่ไปต่อ",
    "no_counter": "ยังไม่ได้บันทึกจุดหน้าเคาน์เตอร์ — เปิดตัวอย่างเดียว",
    "replaced": "มีการกดคำสั่งอื่น (ฉาก จุด กลับแท่น) หรือมีการเดินอื่นแทน",
    "ceremony_app": "หุ่นเปิดแอป Emma Ceremony",
    "switched_off": "สวิตช์การ์ดต่อเนื่อง (EVENT_TOUR) ถูกปิด",
    "no_pose": "อ่านตำแหน่งหุ่นไม่ได้ — ไม่รู้ว่าต้องหมุนก่อนเดินหรือไม่ จึงไม่เดิน",
    "no_spot": "จุดถัดไปไม่มีตำแหน่งแล้ว (ไฟล์จุดเปลี่ยน)",
    "leg_refused": "หุ่นไม่ออกเดินขั้นถัดไป",
    "leg_cut": "เดินไม่ถึงจุด",
    "wave_turn_cut": "ไปมุม/หันหาแขกก่อนโบกไม่สำเร็จ",
    "no_call": "หน้าหุ่นเปิดสายไม่ขึ้น — ท่าจับมือต้องใช้สาย",
    "handshake_refused": "หุ่นไม่รับท่าจับมือ",
    "handshake_failed": "ท่าจับมือไม่สำเร็จ",
    "handshake_stopped": "ท่าจับมือถูกหยุด",
    "handshake_silent": "หุ่นไม่บอกว่าเริ่มท่าจับมือ — ไม่รู้ว่าแขนยื่นออกไปไหม",
    "handshake_no_end": "หุ่นไม่บอกว่าจับมือเสร็จ — ไม่เดินต่อจนกว่าจะรู้ว่าแขนกลับท่าพัก",
    "error": "ผิดพลาดในเซิร์ฟเวอร์",
    "hand_down": "ทีมงานกดเอามือลง — ไม่จับมือและไม่เดินต่อ",
    "wheels_still": ("ล้อไม่ขยับเลย 15 วิตั้งแต่เริ่มเดินท่อนนี้ — ปุ่มหยุดฉุกเฉิน (E-stop) บนตัวหุ่นอาจกดค้าง หรือมีคน/ของ"
                     "ยืนขวางตรงหน้า (บอร์ดแยกสองอย่างนี้ไม่ได้) หุ่นไม่ไปต่อเอง"),
    "restarted": "เครื่องหุ่นรีบูตหรือเซิร์ฟเวอร์เริ่มใหม่ระหว่างการ์ด",
    # the ceremony's opening (owner 23 Nov: "ช่วงพิธีอยู่แอปพิธีทั้งหมด", `begin_ceremony`)
    "reveal_refused": "จอห้องไม่เริ่มเปิดตัว — หุ่นไม่ออกจากกล่อง",
    "reveal_cut": "จอเปิดตัวถูกหยุดก่อนเสียงกระแทก — หุ่นไม่ออกจากกล่อง ไม่โบก ไม่ไปต่อ",
    "ceremony_app_off": "แอป Emma Ceremony ไม่ได้อยู่บนหุ่นแล้ว (สายหลุด/เปลี่ยนแอป)",
    "order_reset": "กดเริ่มลำดับพิธีใหม่",
    "tour_off": "เปิดตัวแล้ว — การ์ดต่อเนื่องปิดอยู่ (EVENT_TOUR) จึงไม่ไปเคาน์เตอร์/แขก",
    # review 23 Nov: a walk out that ended short of its forward_m is not out (board_walk's "near enough" is not)
    "debut_short": "เดินออกจากกล่องไม่ครบ — ไม่โบก ไม่พูดประโยค ไม่ไปต่อ (แขนยังล็อก)",
    "not_armed": "หุ่นออกจากกล่องไปแล้ว / ไม่แน่ใจว่าออกไปแล้ว — กดเปิดตัวซ้ำไม่ได้ (จะเดินไปอีกเต็มระยะจากตรงที่ยืน)",
    "not_still": ("ออกจากกล่องแล้วแต่ไม่เห็นล้อหยุดนิ่ง — แขนยังล็อก ไม่ไปต่อเอง · ดูหุ่นแล้วกด 'หุ่นออกจากกล่องแล้ว — "
                  "ปลดล็อกแขน' แล้วทำต่อด้วยมือ"),
}
#: The ends that are the card going as planned (the phone's green): the whole round, or all a recorded file
#: allowed (the counter only, the debut only).
GOOD = ("done", "done_counter", "no_counter", "tour_off")
#: What a step is called on the phone.
PHASE_TH = {"debut": "กำลังเปิดตัว", "after_debut": "รอเปิดตัวจบ (ประโยคและมือลง)", "walking": "กำลังเดิน",
            "settling": "รอหุ่นนิ่งก่อนยื่นมือ", "handshake": "ยื่นมือจับมือ รอแขนกลับท่าพัก",
            "reveal": "จอเปิดตัว (ดนตรีขึ้น → จอมืด → เสียงกระแทก) — หุ่นรอในกล่อง (แขนพัก)", "walk_out": "เดินออกจากกล่อง (แขนลง)",
            "waving": "ยกมือโบก + พูดประโยคเปิดตัว"}
#: The ceremony's opening phases: before its tour (EVENT_TOUR is the tour's switch, not the debut's).
OPENING = ("reveal", "walk_out", "waving")

#: `seq`: the press it belongs to (event_host's, or the ceremony's own: CEREMONY_SEQ_BASE up); `owner`: "event" (card
#: 1 of the Event page) | "ceremony" (the ceremony's opening step: `order` = that press of app/event_order.py -- its
#: gen, its step's key and mark, its stops); `plan`: its steps after the scene's own walk (`planned`); `at`: the step
#: under way (None: still the scene's own); `phase`: debut | reveal | walk_out | waving | after_debut | walking |
#: settling | handshake; `ended`: why it ended (WHY_TH / event_walk.REASONS_TH key) or None; `until`: the hold's end
#: (`active`); `leg`: its walk now; `reveal`: the screen's run (app/ceremony_reveal.py); `opening`: the ceremony's
#: first three steps for the phone ({key, state, note_th}).
STATE: dict = {"seq": None, "scene": None, "plan": [], "at": None, "phase": None, "ended": None, "detail_th": None,
               "since": None, "ended_at": None, "until": 0.0, "stops_seen": None, "task": None, "leg": None,
               "face_lost_at": None, "held_said": 0.0, "missing": [], "counter_ok": False, "owner": "event",
               "order": None, "reveal": None, "opening": [], "call_lost_at": None, "impact": None,
               "box_press": False, "box_run": None, "via_heading": None, "at_corner": False, "corner_walk": False}


def reset() -> None:
    task = STATE.get("task")
    if task is not None and not task.done():
        with contextlib.suppress(RuntimeError):          # its loop closed with an earlier test
            task.cancel()
    _let_go()
    STATE.update(seq=None, scene=None, plan=[], at=None, phase=None, ended=None, detail_th=None, since=None,
                 ended_at=None, until=0.0, stops_seen=None, task=None, leg=None, face_lost_at=None, held_said=0.0,
                 missing=[], counter_ok=False, owner="event", order=None, reveal=None, opening=[], hand_done=[], skipped=[], met=None,
                 hand_run=None, call_lost_at=None, impact=None, box_press=False, box_run=None, via_heading=None, at_corner=False, corner_walk=False)
    _CONTEXT.update(at=None, key=None, value=None)


# ---- the plan (pure: the staff page and scripts/floor_sim.py read it too) -------------------------------------

def parse(raw, spots: dict) -> dict | None:
    """A scene's `tour` from the event file -> {"counter": id, "guests": [ids], "staff_th": str} or None (logged).
    The counter and every guest a spot to stand at: in `spots`, not the dock, not one that walks on from another
    (`from_spot`), the guests distinct and not the counter."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        logger.warning("a scene's tour is not an object: %r -- no tour", raw)
        return None
    counter, guests = raw.get("counter"), raw.get("guests")

    def standable(sid) -> bool:
        sp = spots.get(sid) if isinstance(sid, str) else None
        return bool(sp) and not sp.get("dock") and "from_spot" not in sp

    if (not standable(counter) or not isinstance(guests, list) or not 1 <= len(guests) <= 6
            or not all(standable(g) for g in guests) or len(set(guests)) != len(guests) or counter in guests):
        logger.warning("a scene's tour names a counter or guests that are not spots to stand at: %r -- no tour", raw)
        return None
    staff = raw.get("staff_th")
    walk_up = raw.get("walk_up")
    if walk_up is not None and not walks_up_from(spots.get(walk_up) if isinstance(walk_up, str) else None, counter):
        logger.warning("a scene's tour: walk_up %r is not a spot that walks up to a guest from its counter -- the card "
                       "stays at the counter when a guest spot is not recorded", walk_up)
        walk_up = None
    out = {"counter": counter, "guests": list(guests), "staff_th": staff if isinstance(staff, str) else "",
           "walk_up": walk_up}
    end = raw.get("end")
    if end is not None:
        if standable(end) and end not in guests:
            out["end"] = end
        else:
            logger.warning("a scene's tour: end %r is not a spot to stand at -- it ends at the counter", end)
    via = _via(raw.get("via"))
    if via:
        out["via"] = via
    lines = _lines(raw.get("lines"), {counter, *guests, *([walk_up] if walk_up else [])})
    if lines:
        out["lines"] = lines
    glide = raw.get("glide")
    if glide is not None:
        if isinstance(glide, (int, float)) and not isinstance(glide, bool) and math.isfinite(glide):
            out["glide"] = min(GLIDE_MAX, max(GLIDE_MIN, float(glide)))
        else:
            logger.warning("a scene's tour: glide %r is not a speed -- the board's own walks", glide)
    return out


def _via(raw) -> list:
    """The tour's `via` on the way from the debut to the counter: [[x, y, heading deg], ...] in board coordinates. Anything
    else = none (logged): the walk to the counter as it was."""
    if raw is None:
        return []

    def num(v) -> bool:
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)

    if (not isinstance(raw, list) or not 1 <= len(raw) <= VIA_MAX
            or not all(isinstance(p, list) and len(p) == 3 and all(num(v) for v in p) for p in raw)):
        logger.warning("a scene's tour: via %r is not 1-%d points [x, y, heading] -- none", raw, VIA_MAX)
        return []
    return [[float(v) for v in p] for p in raw]


def _lines(raw, at: set) -> dict:
    """The tour's `lines`: {handshake spot: a recorded line pinned by its sha256 (event_host `_recorded_entry`)}. One that
    is not that, or is for no handshake of this tour, is left out (logged): that handshake is silent, as all were."""
    from app import event_host

    out = {}
    for sid, rec in (raw.items() if isinstance(raw, dict) else ()):
        entry = event_host._recorded_entry({"id": "tour " + str(sid), "recorded": rec}) if sid in at else None
        if entry is None:
            logger.warning("a scene's tour: the line at %r is not a recorded line of one of its handshakes -- nothing is "
                           "said there", sid)
        else:
            out[sid] = entry
    return out


def walks_up_from(spot: dict | None, counter) -> bool:
    """`spot` is card 3's kind (owner 22 Nov, guest_front): it walks on from `counter` (`from_spot`), turned round, and
    straight up to whoever stands there (a `forward` with `meet_m`) -- the tour's `walk_up` (WALK_UP_TH)."""
    return (isinstance(spot, dict) and spot.get("from_spot") == counter and not spot.get("dock")
            and any(isinstance(s, dict) and "forward" in s and s.get("meet_m") is not None
                    for s in spot.get("steps") or []))


def _hand(place: dict, lines: dict) -> dict:
    """A handshake step at `place`, with the recorded line the tour gives it (none = silent, as before 24 Nov)."""
    step = {"kind": "handshake", "spot": place["id"], "name_th": place["name_th"]}
    if lines.get(place["id"]):
        step["line"] = lines[place["id"]]
    return step


def planned(tour: dict | None, spots: dict) -> dict | None:
    """What one press does after the scene's own walk, from what is recorded NOW: {"counter": {id, name_th,
    walkable}, "guests": [...], "missing": [names of guest spots with no position], "counter_ok", "guests_ok",
    "walk_up": {id, name_th, walkable} | None, "walk_up_ok": a guest spot missing, so one guest card 3's way,
    "steps": [{"kind": "walk"|"handshake", "spot", "name_th", "back", "head_on", "walk_up", "if_met"}]}. The phone
    shows it before the press and `pressed` runs exactly these steps."""
    if not tour:
        return None
    from app import event_host

    def one(sid):
        sp = spots.get(sid) or {}
        # a tour spot is where she stands to give her hand: its heading too (review 23 Nov -- a pose with no heading
        # leaves her facing the way she came, the hand offered to nobody): no heading = not recorded
        return {"id": sid, "name_th": sp.get("name_th") or sid,
                "walkable": event_host.spot_steps(sp or None) is not None and spot_pose(sp) is not None,
                "pose": spot_pose(sp)}

    counter = one(tour["counter"])
    guests = [one(g) for g in tour["guests"]]
    missing = [g["name_th"] for g in guests if not g["walkable"]]
    walk_up = one(tour["walk_up"]) if tour.get("walk_up") else None
    walk_up_ok = bool(counter["walkable"] and missing and walk_up and walk_up["walkable"])
    # where the round ends: its own `end` spot once it is marked (owner 24 Nov), the counter until then
    end = one(tour["end"]) if tour.get("end") else None
    if end is None or not end["walkable"]:
        end = counter
    lines = tour.get("lines") or {}
    steps: list[dict] = []
    # `head_on`: a walk that ends in its handshake -- head-on where its spot asks for it (HEAD_ON_MAX_M: the counter's,
    # owner 23 Nov); the walk back to the counter after the guests has no handshake and comes from their side as it was
    via = tour.get("via") or []
    gl = {"glide": tour["glide"]} if tour.get("glide") else {}
    if counter["walkable"]:
        # `via` (owner 24 Nov): the way to the counter's hand is the corner, not the head-on point -- of THIS walk; the
        # by-hand button's own walk has no corner and keeps its head-on point (review 24 Nov: it had neither)
        steps += [dict({"kind": "walk", "spot": counter["id"], "name_th": counter["name_th"], "back": False,
                        "head_on": True}, **({"via": via} if via else {}), **gl),
                  _hand(counter, lines)]
        if not missing:
            for g in guests:
                steps += [{"kind": "walk", "spot": g["id"], "name_th": g["name_th"], "back": False, "head_on": True, **gl},
                          _hand(g, lines)]
            # its end has no hand after it: held near it (a wall, a stand) is there (review 24 Nov: 45 s and a stop at the
            # very end of the round, the marked spot 0.6 m off the wall)
            steps.append({"kind": "walk", "spot": end["id"], "name_th": end["name_th"], "back": True, **gl})
        elif walk_up_ok:
            # a guest spot not recorded: ONE guest card 3's way (owner 23 Nov: "ส่วนจุดแขกตอนนี้ให้หุ่นหันหลังแล้วเดินไป
            # ตรงๆก็เจอคนแล้วทักทายได้ไหม") -- `walk_up` (guest_front): turned round slowly, straight ahead up to whoever
            # stands there; the hand only to someone met (`if_met`); then back to the counter facing it, no hand
            steps += [{"kind": "walk", "spot": walk_up["id"], "name_th": walk_up["name_th"], "back": False,
                       "walk_up": True},
                      {**_hand(walk_up, lines), "walk_up": True, "if_met": True},
                      {"kind": "walk", "spot": end["id"], "name_th": end["name_th"], "back": True}]
    # where she will turn on the spot, slowly and watched, by the recorded poses (review 23 Nov: the card said "she
    # turns round at the counter and at the last guest" -- true of one assumed layout only). From the debut's end to
    # the counter is not known before the press (a metre ahead of wherever the dock is).
    turns = []
    walks = [s for s in steps if s["kind"] == "walk"]
    places = {p["id"]: p for p in [counter, *guests, *([walk_up] if walk_up else []), end]}
    for a, b in zip(walks, walks[1:]):
        here, there = places[a["spot"]], places[b["spot"]]
        legs = (leg_steps(spots.get(b["spot"]), tuple(here["pose"]), None, head_on=bool(b.get("head_on")),
                          via=b.get("via"))
                if here["pose"] else None)
        for s in legs or []:
            if s.get("do") == "turn":
                row = {"at_th": there["name_th"] if s.get("end") else here["name_th"], "to_th": there["name_th"],
                       "end": bool(s.get("end"))}
                if not turns or turns[-1] != row:        # a turn in two halves (TURN_SPLIT_DEG) is one on the card
                    turns.append(row)
    return {"counter": counter, "guests": guests, "missing": missing, "counter_ok": counter["walkable"],
            "guests_ok": counter["walkable"] and not missing, "steps": steps, "staff_th": tour.get("staff_th") or "",
            "turns": turns, "walk_up": walk_up, "walk_up_ok": walk_up_ok,
            # the phone's words for it (the Event page's card and the ceremony's step say the same: WALK_UP_*)
            "walk_up_th": WALK_UP_STEP_TH if walk_up_ok else None,
            "missing_th": WALK_UP_MISSING_TH % (", ".join(missing), counter["name_th"]) if walk_up_ok else None}


def spot_pose(spot: dict | None) -> tuple | None:
    """(x, y, heading deg) where a spot leaves her standing: its recorded/file pose, or its steps' last point and the
    last heading they give -- None without a heading (or without a place)."""
    if not spot:
        return None
    pose = spot.get("pose")
    if pose is not None:
        return (float(pose[0]), float(pose[1]), float(pose[2])) if pose[2] is not None else None
    x = y = face = None
    for s in spot.get("steps") or []:
        if s.get("to"):
            x, y = float(s["to"][0]), float(s["to"][1])
            face = float(s["face"]) if s.get("face") is not None else face
        elif s.get("do") == "turn" and s.get("face") is not None:
            face = float(s["face"])
    return None if x is None or face is None else (x, y, face)


def _wrap_deg(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def _turn(face: float, label: str, *, end: bool = False) -> dict:
    """A watched slow turn (board_walk `turn`); `end` = to the point's own heading after it (the card says which)."""
    out = {"do": "turn", "face": round(float(face), 1), "w_spin": TURN_W_SPIN, "label": label}
    if end:
        out["end"] = True
    return out


def _turns(heading: float, face: float, label: str, *, end: bool = False, step: dict | None = None) -> list[dict]:
    """The watched turn(s) from `heading` to `face`: one, or two halves the same way round past TURN_SPLIT_DEG.
    `step`: a turn the spot's own steps give (card 3's, 22 Nov) -- kept as it is but for its heading."""
    delta = _wrap_deg(float(face) - float(heading))

    def one(to: float) -> dict:
        return dict(step, face=round(float(to), 1)) if step is not None else _turn(to, label, end=end)

    if abs(delta) <= TURN_SPLIT_DEG:
        return [dict(step) if step is not None else _turn(face, label, end=end)]
    return [one(_wrap_deg(float(heading) + delta / 2.0)), one(float(face))]


def head_on_point(spot: dict | None) -> tuple | None:
    """(x, y, heading deg) where a walk that ends in the handshake at `spot` turns to come straight in (HEAD_ON_MAX_M):
    `head_on_m` back from the spot's last point along its heading, facing the same way. None = the spot asks for none
    (no `head_on_m` in (0, HEAD_ON_MAX_M]), or its walk does not end with a point and a heading to come in along."""
    from app import event_host

    d = (spot or {}).get("head_on_m")
    if isinstance(d, bool) or not isinstance(d, (int, float)) or not 0.0 < float(d) <= HEAD_ON_MAX_M:
        return None
    steps = event_host.spot_steps(spot)
    last = steps[-1] if steps else {}
    if not last.get("to") or last.get("face") is None:
        return None
    x, y, face = float(last["to"][0]), float(last["to"][1]), float(last["face"])
    return (round(x - float(d) * math.cos(math.radians(face)), 3), round(y - float(d) * math.sin(math.radians(face)), 3),
            face)


def head_on_steps(spot: dict | None, steps: list[dict] | None, here=None) -> list[dict] | None:
    """`steps` (event_host.spot_steps(spot)) with the head-on point (`head_on_point`) put before the spot's last point:
    there she faces the spot's heading, and the last point is straight ahead. Unchanged when the spot has none, or
    `here` (x, y, ...) already stands nearer the spot than `head_on_m` -- she would walk away from it to come back."""
    start = head_on_point(spot) if steps else None
    if start is None:
        return steps
    final = steps[-1]
    if here is not None and math.hypot(float(here[0]) - float(final["to"][0]),
                                       float(here[1]) - float(final["to"][1])) < float(spot["head_on_m"]):
        return steps
    from app import event_host

    step = {"to": [start[0], start[1]], "face": start[2], "speed": float(final.get("speed") or event_host.DEFAULT_SPEED),
            "label": "จุดตั้งต้นเดินตรงเข้า%s (ห่างออกมา %.1f ม.)" % (spot.get("name_th") or "", float(spot["head_on_m"]))}
    return steps[:-1] + [step] + steps[-1:]


def via_ahead(via: list, here) -> bool:
    """The first `via` point lies ahead of the way she stands (VIA_AHEAD_DEG): it is an absolute point worked out for where
    the box stood, so a box put down elsewhere on the day, or a walk out that ended short, must not send her off sideways
    (review 24 Nov)."""
    if here[2] is None:
        return True
    dx, dy = via[0][0] - float(here[0]), via[0][1] - float(here[1])
    if math.hypot(dx, dy) < TURN_MIN_M:
        return True
    return abs(_wrap_deg(math.degrees(math.atan2(dy, dx)) - float(here[2]))) <= VIA_AHEAD_DEG


def via_steps(steps: list[dict], via: list, name_th: str) -> list[dict]:
    """`steps` with the tour's `via` points first: at each she stops facing its heading (so the turn there is hers to see,
    not a curve the board cuts), at the spot's own speed."""
    from app import event_host

    first = next((i for i, s in enumerate(steps) if s.get("to")), None)
    if not via or first is None:
        return steps
    speed = float(steps[first].get("speed") or event_host.DEFAULT_SPEED)
    out = [{"to": [p[0], p[1]], "face": p[2], "speed": speed, "label": "เข้าแนวของ%s แล้วหัน" % name_th} for p in via]
    return steps[:first] + out + steps[first:]


def glide_steps(steps: list[dict], speed: float, name: str, end: bool = False) -> list[dict]:
    """A walk's steps as one `glide` (board_walk): the points in straight lines, the heading at the end turned to; the
    turns before a point are the glide's own. `end`: the round ends there, no hand after it (END_SHORT_OK_M). A walk with anything else in it (a dock, a walk ahead, a fallback) stays
    as it is."""
    pts: list[list[float]] = []
    face, before = None, []
    for s in steps:
        if s.get("to") and not s.get("fallback"):
            pts.append([float(s["to"][0]), float(s["to"][1])])
            face = s.get("face")
        elif s.get("do") == "turn" and s.get("face") is not None:
            if pts:
                face = s["face"]
        elif s.get("do") == "undock" and not pts:
            before.append(s)
        else:
            return steps
    if not pts:
        return steps
    return [*before, {"glide": pts, "speed": speed, "label": name,
                      **({"face": float(face)} if face is not None else {}),
                      **({"short_ok": END_SHORT_OK_M} if end else {})}]


def leg_steps(spot: dict | None, here, dock: dict | None, *, head_on: bool = False,
              via: list | None = None, glide: float | None = None, end: bool = False,
              via_heading: float | None = None) -> list[dict] | None:
    """board_walk steps to `spot` from `here` = (x, y, heading in degrees | None): the spot's own steps
    (event_host.spot_steps) as the event walks them from there (event_host.route_from: out of the dock by its
    corridor, round a floor hazard), with every turn of more than TURN_FIRST_DEG made slowly and watched by us
    (`watched_turns`) instead of by the board. None = the spot cannot be walked to. Every walk to a spot uses it
    (event_host.walk_steps: the scenes, the card's "ไป<จุด>", the ceremony order's walks, the roam, this tour).
    `head_on`: the walk ends in the handshake there -- straight in from the spot's head-on point (`head_on_steps`,
    owner 23 Nov), when it has one. Pure but for the event file's hazards: scripts/floor_sim.py walks the same."""
    from app import event_host

    steps = event_host.spot_steps(spot)
    # `via_heading`: the way she walked out of the box, when she has turned since to wave (scene `wave_face`) -- the
    # corner lies ahead of THAT (review 25 Nov 02:3x: judged by the 100 deg wave heading, the owner's corner to Khun
    # Miki at ~169 deg was dropped as "not ahead" and she glided across instead)
    look = here if via_heading is None or here is None else (here[0], here[1], via_heading)
    if via and here is not None and not via_ahead(via, look):
        logger.warning("event tour: the corner %s is not ahead of (%.2f, %.2f) %s -- the box was moved, or the walk out was "
                       "short: the walk as without it", via[0], float(here[0]), float(here[1]), here[2])
        via = None
    if head_on and not via:
        steps = head_on_steps(spot, steps, here)
    if steps is not None and via:
        steps = via_steps(steps, via, (spot or {}).get("name_th") or "จุดถัดไป")
    if steps is None or here is None:
        return steps
    x, y, heading = float(here[0]), float(here[1]), here[2]
    steps = event_host.route_from(steps, (x, y), dock)
    if heading is None:
        return steps
    if glide:
        return glide_steps(steps, glide, (spot or {}).get("name_th") or "จุดถัดไป", end)
    return watched_turns(steps, (x, y, float(heading)), (spot or {}).get("name_th") or "จุดถัดไป", dock)


def watched_turns(steps: list[dict], here, name: str = "จุดถัดไป", dock: dict | None = None) -> list[dict]:
    """The steps with the board's own big turns taken away (review 23 Nov): the lidar sees nothing behind her, and
    the board turns at its own speed, unwatched -- at the START of a MoveTo whose point lies behind her, and at its
    END when the point's heading is far from the way she arrives (board_walk passes it as MoveTo's yaw, and turns to
    it itself after a miss). Before such a MoveTo: a watched slow turn to face its point (card 3's, 22 Nov); such
    an end: the MoveTo without its heading, then a watched slow turn to it. A point nearer than TURN_MIN_M: she
    hardly drives -- a big heading change there is turned to first, then the short MoveTo."""
    x, y, heading = float(here[0]), float(here[1]), float(here[2])
    out: list[dict] = []
    for s in steps:
        if s.get("do") == "turn" and s.get("face") is not None:
            out += _turns(heading, float(s["face"]), "", step=s)  # a spot's own turn: in halves past TURN_SPLIT_DEG
            heading = float(s["face"])
            continue
        if s.get("do") == "dock":
            # board_walk's dock step sets out with a MoveTo to the dock's way in (dock_way_in): from the end spot (24 Nov)
            # that lies ~170 deg behind her, 1.1 m from Khun Miki -- the board's own turn, unwatched (review 24 Nov)
            from app import event_host

            way = event_host.dock_way_in(dock) if dock else None
            if way is not None and math.hypot(way[0] - x, way[1] - y) >= TURN_MIN_M:
                bearing = math.degrees(math.atan2(way[1] - y, way[0] - x))
                if abs(_wrap_deg(bearing - heading)) > TURN_FIRST_DEG:
                    out += _turns(heading, bearing, "หมุนช้าๆ หันไปทางแท่น")
                    heading = bearing
            out.append(s)
            continue
        if not s.get("to"):
            out.append(s)                                # undock / a walk ahead keep her heading
            continue
        step = dict(s)
        tx, ty = float(step["to"][0]), float(step["to"][1])
        face = float(step["face"]) if step.get("face") is not None else None
        if math.hypot(tx - x, ty - y) < TURN_MIN_M:
            if face is not None and abs(_wrap_deg(face - heading)) > TURN_FIRST_DEG:
                out += _turns(heading, face, "หมุนช้าๆ หันตาม%s" % name)
                heading = face
            out.append(step)
            heading = face if face is not None else heading
        else:
            bearing = math.degrees(math.atan2(ty - y, tx - x))
            if abs(_wrap_deg(bearing - heading)) > TURN_FIRST_DEG:
                out += _turns(heading, bearing, "หมุนช้าๆ หันไปทาง%s" % name)
            if face is not None and abs(_wrap_deg(face - bearing)) > TURN_FIRST_DEG:
                step.pop("face")
                out.append(step)
                out += _turns(bearing, face, "หมุนช้าๆ หันตาม%s" % name, end=True)
                heading = face
            else:
                out.append(step)
                heading = face if face is not None else bearing
        x, y = tx, ty
    return out


def rear_turns(steps: list[dict], here) -> list[str]:
    """Where `steps` from `here` (x, y, heading deg) would have the BOARD turn her more than TURN_FIRST_DEG: at the
    start of a MoveTo (its point behind her) or at its end (its heading against the way she arrives) -- after our
    own turns. [] = none. The simulator's gate on every event walk."""
    x, y, heading = float(here[0]), float(here[1]), here[2]
    out = []
    for s in steps:
        if s.get("do") == "turn" and s.get("face") is not None:
            heading = float(s["face"])
        elif s.get("to"):
            tx, ty = float(s["to"][0]), float(s["to"][1])
            face = float(s["face"]) if s.get("face") is not None else None
            if math.hypot(tx - x, ty - y) < TURN_MIN_M:
                arrive = heading
            else:
                arrive = math.degrees(math.atan2(ty - y, tx - x))
                if heading is not None and abs(_wrap_deg(arrive - float(heading))) > TURN_FIRST_DEG:
                    out.append("a MoveTo to (%.2f, %.2f) %.0f deg off her heading with no watched turn first"
                               % (tx, ty, abs(_wrap_deg(arrive - float(heading)))))
            if face is not None and arrive is not None and abs(_wrap_deg(face - float(arrive))) > TURN_FIRST_DEG:
                out.append("a MoveTo to (%.2f, %.2f) ends with the board's turn of %.0f deg to its heading"
                           % (tx, ty, abs(_wrap_deg(face - float(arrive)))))
            heading = face if face is not None else arrive
            x, y = tx, ty
    return out


# ---- the state the rest of the server asks ----------------------------------------------------------------------

def active() -> bool:
    """A tour is under way: from card 1's press to its end, within its hold (a dead task lets go by itself)."""
    return STATE["phase"] is not None and STATE["ended"] is None and time.monotonic() < float(STATE["until"])


def running(scene_id: str | None = None) -> bool:
    """A tour is under way (of `scene_id` when given): its card is not pressed again over it (review 23 Nov: a
    second press ended it and walked a fresh metre ahead from wherever she stood -- facing a guest 65 cm away, or the
    counter whose top the lidar does not see). Stop it first."""
    return active() and (scene_id is None or STATE["scene"] == scene_id)


RUNNING_TH = "การ์ดนี้กำลังทำต่อเนื่องอยู่ — กดหยุดสีแดงก่อน แล้วค่อยกดใหม่ (ไม่งั้นหุ่นจะเดินตรงไปข้างหน้าอีก 1 ม. จากตรงที่ยืน)"


def holds_head() -> bool:
    """head_follow's question: no look at all now (owner 23 Nov: "ไม่ต้องให้หุ่นขยับหัวนะ") -- the card, and a walk of
    the card's that a "by hand" button sent (`hand_run`): 23 Nov on the robot the card ended at the counter leg, its
    walk was pressed by hand, and the walk scan nodded the head (tilt 18) on the way."""
    return active() or _hand_walking()


class _HandPress:
    """`hand_run` while a "by hand" walk press is starting its walk (no run id yet): one object per press, so a
    second press's pending is never cleared by the first press ending (second review 23 Nov)."""


def _hand_walking() -> bool:
    run_id = STATE.get("hand_run")
    if run_id is None:
        return False
    if isinstance(run_id, _HandPress):
        return event_walk.walking()
    run = event_walk.current()
    return run is not None and getattr(run, "id", None) == run_id and event_walk.walking()


def holds_arm() -> bool:
    """For a gesture that is not the tour's own: her talking arm, a voice's gesture or handshake."""
    return active()


def _hold(seconds: float) -> None:
    STATE["until"] = time.monotonic() + seconds
    try:
        from app import greeter

        greeter.hold(GREETER_KEY, seconds)
    except Exception:                                    # noqa: BLE001 -- a greeting held is not worth a failure
        logger.exception("event tour: the camera's greetings could not be held")


def _let_go() -> None:
    STATE["until"] = 0.0
    try:
        from app import greeter

        greeter.release(GREETER_KEY)
    except Exception:                                    # noqa: BLE001
        pass


# ---- the press ------------------------------------------------------------------------------------------------

def pressed(sc: dict, seq: int, stops_seen: int) -> None:
    """event_host.start_scene, BEFORE the scene's walk is asked for (a walker that arrives at once must find its
    tour). Any press ends a tour under way ("replaced"); a scene with a tour (EVENT_TOUR on) plans its own from
    what is recorded now and holds the head from here."""
    if active():
        _end(STATE["seq"], "replaced")
    task = STATE.get("task")
    if task is not None and not task.done():
        task.cancel()
    if not (settings.event_tour and sc.get("tour")):
        return
    from app import event_host

    plan = planned(sc["tour"], (event_host.load() or {}).get("spots") or {})
    STATE.update(seq=seq, scene=sc["id"], plan=plan["steps"] if plan else [], at=None, phase="debut", ended=None,
                 detail_th=None, since=time.time(), ended_at=None, stops_seen=stops_seen, task=None, leg=None,
                 face_lost_at=None, missing=(plan or {}).get("missing") or [],
                 counter_ok=bool((plan or {}).get("counter_ok")), owner="event", order=None, reveal=None, opening=[],
                 hand_done=[], skipped=[], met=None, hand_run=None, call_lost_at=None, impact=None, box_press=bool(sc.get("walk_out_of_box")),
                 box_run=None, via_heading=None, at_corner=False, corner_walk=False)
    _hold(DEBUT_HOLD_S)
    turnlog.record("event_tour", phase="pressed", scene=sc["id"], steps=len(STATE["plan"]),
                   missing=len(STATE["missing"]), counter=STATE["counter_ok"])
    _note()


def walk_answer(seq: int, walk: dict | None) -> None:
    """The scene's own walk was asked for: refused or none = the scene is acted where she stands, no tour."""
    if STATE["seq"] != seq or STATE["phase"] != "debut" or STATE["ended"] is not None:
        return
    if walk is None or not walk.get("ok"):
        if event_walk.stops() != STATE["stops_seen"]:
            why = event_walk.STATE.get("last_stop") or "staff"    # a person's stop while the arm rested
        else:
            why = "walking_off" if (walk or {}).get("error") == "disabled" else "debut_refused"
        _end(seq, why, detail_th=(walk or {}).get("error_th"))


def debut_ended(seq: int, run) -> None:
    """The scene's own walk ended (event_host._callbacks, after its line and its wave): arrived = the tour goes
    on (a task of its own), anything else = it ends here."""
    if STATE["seq"] != seq or STATE["phase"] != "debut" or STATE["ended"] is not None:
        return
    from app import event_host

    if event_host.STATE["seq"] != seq:
        _end(seq, "replaced")                            # staff pressed something else meanwhile
        return
    if event_walk.stops() != STATE["stops_seen"]:
        _end(seq, event_walk.STATE.get("last_stop") or "staff")
        return
    if getattr(run, "state", None) != "arrived":
        if getattr(run, "reason", None) == "replaced":
            _end(seq, "replaced")                        # another walk took over (a spot, home)
            return
        detail = None
        with contextlib.suppress(Exception):
            view = run.view()
            # the wheels never turned (Run.still) on a stop board_walk calls "in the way" or names no kind of: the walk
            # card's E-stop line (23 Nov on the robot: the E-stop was in, the card read "มีคนหรือของขวางทาง" and the
            # walk card the E-stop). A stop of a known kind (the page went quiet, the face gone, lost) keeps its own
            # words -- second review: the E-stop line over "the control page went quiet" misleads the other way
            still_th = view.get("still_th") if view.get("kind") in (None, "blocked") else None
            detail = still_th or view.get("reason_th")
        _end(seq, "debut_cut", detail_th=detail)
        return
    if getattr(run, "out_of_box", None) is False:
        # out of the box, but not all of it (event_host._callbacks `_out_of_box`): not out -- no tour
        walked = event_walk.walked_m(run)
        _end(seq, "debut_short", detail_th=None if walked is None else "เดินได้ %.2f ม." % walked)
        return
    if getattr(run, "box_still", None) is False:
        _end(seq, "not_still")                           # out, the wheels not seen still: the arm stays locked
        return
    STATE["phase"] = "after_debut"                       # the debut itself is done (the phone's first step)
    if not STATE["counter_ok"]:
        _end(seq, "no_counter")
        return
    _hold(DEBUT_HOLD_S)
    _note()
    STATE["task"] = asyncio.get_running_loop().create_task(_run(seq))


def stop(why: str = "staff") -> bool:
    """A person's stop (event_host.stop, AFTER its walk stop -- the leg's own reason stays the person's): ended
    now, nothing more of it. True = one was under way."""
    if not active():
        return False
    _end(STATE["seq"], why)
    return True


# ---- the run ----------------------------------------------------------------------------------------------------

def _why_not(seq: int, *, leg=None) -> str | None:
    """None = go on. `leg`: our own walk now -- its dead man's switches (the page, the face, B) are its own, and
    its end says why; here only what the walk does not know."""
    from app import event_host, event_roam

    if STATE["seq"] != seq or STATE["ended"] is not None:
        return "replaced"
    if STATE["owner"] == "ceremony":
        # the ceremony's opening step owns it (`begin_ceremony`): its app on the robot, its press the order's
        # own -- not reset, no stop since, its step's mark still that press's
        why = _ceremony_lost()
        if why is not None:
            return why
    else:
        if event_host.STATE["seq"] != seq:
            return "replaced"
        if event_host.ceremony_now():
            return "ceremony_app"
    if event_walk.stops() != STATE["stops_seen"]:
        return event_walk.STATE.get("last_stop") or "staff"
    if not settings.event_tour and STATE["phase"] not in OPENING:
        return "switched_off"
    run = event_walk.current()
    if event_walk.walking() and run is not leg:
        return "replaced"                                # a walk this tour did not start (another press's, a walk-up)
    if leg is not None:
        return None
    if not event_roam.page_alive():
        return "control_lost"
    if event_walk._app_latched():
        return "app_stop"
    if event_walk._faces():
        STATE["face_lost_at"] = None
    else:
        lost = STATE["face_lost_at"] = STATE["face_lost_at"] or time.monotonic()
        if time.monotonic() - lost > event_walk.FACE_GRACE_S:
            return "face_gone"
    return None


def _tick(seq: int, *, leg=None) -> str | None:
    why = _why_not(seq, leg=leg)
    if why is None:
        _hold(HOLD_S)
    return why


async def _run(seq: int) -> None:
    try:
        if not await _debut_over(seq):
            return
        await _tour(seq)
    except asyncio.CancelledError:
        if STATE["seq"] == seq and STATE["ended"] is None:
            _end(seq, "replaced")
        raise
    except Exception:                                    # noqa: BLE001 -- a run that dies says so, and lets go
        logger.exception("event tour: the run failed")
        await _end_and_stop_leg(seq, "error")


async def _tour(seq: int) -> None:
    """The steps after the debut (`planned`): each walk and handshake in turn, to the end or the first that stops."""
    for i, step in enumerate(list(STATE["plan"])):
        why = _tick(seq)
        if why is not None:
            await _end_and_stop_leg(seq, why)
            return
        STATE["at"] = i
        if step["kind"] == "handshake" and step.get("if_met") and not STATE.get("met"):
            # the walk up to an unmarked guest met nobody within its metres (owner 23 Nov): no hand into the air --
            # on to the counter (board_walk's `@@ met`, event_walk Run.met; an old board_walk says none: no hand)
            STATE.setdefault("skipped", []).append(i)
            turnlog.record("event_tour", phase="no_guest_met", spot=step["spot"])
            _note()
            continue
        ok = await (_leg(seq, i, step) if step["kind"] == "walk" else _handshake(seq, i, step))
        if not ok:
            return
    _end(seq, "done" if any(s.get("back") for s in STATE["plan"]) else "done_counter",
         detail_th=NOBODY_MET_TH if STATE.get("skipped") else None)


async def _debut_over(seq: int) -> bool:
    """The debut's line and its hand are over, and her voice: DEBUT_DONE_WAIT_S at most (then the first leg's arm
    rest brings a hand down). False = it ended meanwhile."""
    from app import event_host, event_pageant, robot_body

    deadline = time.monotonic() + DEBUT_DONE_WAIT_S
    while True:
        why = _tick(seq)
        if why is not None:
            await _end_and_stop_leg(seq, why)
            return False
        p = event_host.STATE.get("perform") or {}
        # (the ceremony's opening waited for its own line: `_wave_and_line`)
        line = (STATE["owner"] == "event" and p.get("seq") == seq
                and p.get("phase") in ("walking", "raising", "saying", "settling"))
        arm = (robot_body.STATE.get("state") == "started"
               and time.time() - float(robot_body.STATE.get("at") or 0.0) < HANDSHAKE_DONE_S)
        if not (line or event_pageant.active(seq) or arm or _her_voice_on()):
            return True
        if time.monotonic() > deadline:
            turnlog.record("event_tour", phase="debut_wait", over=False, line=line, arm=arm)
            return True
        await asyncio.sleep(TICK_S)


async def _say_line(seq: int, i: int, step: dict) -> bool:
    """A handshake's recorded line, as the debut's is said (event_cues.play_line: the red stop cuts it). Not played
    (not this take, no call, another line) = the handshake goes on in silence, logged: never read by the model instead --
    in the ceremony nobody reads it, and the owner heard this take."""
    from app import event_cues, event_host

    rec = step["line"]
    said, error = False, None
    try:
        pcm, error = await asyncio.to_thread(event_host.recorded_pcm, {"recorded": rec})
        if pcm is not None and _why_not(seq) is None:
            music("duck")                                # the first hand's hit (its 2.6 s sting) under her hello: silence
            result = await event_cues.play_line("tour_" + step["spot"], rec["text"], pcm, lang=rec.get("lang") or "en",
                                                scene_th=step.get("name_th"),
                                                still=lambda: _why_not(seq) is None and STATE["at"] == i)
            said, error = bool(result.get("ok")), result.get("error")
    except Exception:                                    # noqa: BLE001 -- a line never ends the tour
        logger.exception("event tour: the line at %s", step.get("spot"))
        error = "error"
    turnlog.record("event_tour", phase="line", spot=step["spot"], said=said, error=None if said else error)
    return said


async def _line_over(seq: int, saying: "asyncio.Task | None") -> bool:
    """After the hand is down: the step's line is out and her voice is over (LINE_OVER_S at most) before the next walk.
    False = the tour ended meanwhile."""
    deadline = time.monotonic() + LINE_OVER_S
    while saying is not None:
        why = _tick(seq)
        if why is not None:
            saying.cancel()
            await _end_and_stop_leg(seq, why)
            return False
        if time.monotonic() > deadline or (saying.done() and not _her_voice_on()):
            break
        await asyncio.sleep(TICK_S)
    return True


def music(op: str, **kw) -> bool:
    """The catwalk music on the room's screen (app/face_mirror.catwalk; owner 24 Nov: "เพลง catwalk ... เปิดจนกว่าหุ่นจะเดิน
    ครบหมดนะ"). True = the screen's part took it (its switch, the ceremony's call, something to act on). Never a reason for
    anything to fail."""
    if not getattr(settings, "catwalk_music", False):
        return False
    try:
        from app import face_mirror

        return bool(face_mirror.catwalk(op, **kw))
    except Exception:                                    # noqa: BLE001 -- music never ends the tour
        logger.exception("event tour: the music (%s)", op)
        return False


def _her_voice_on() -> bool:
    from app import session as session_module

    session = session_module._active
    until = float(getattr(session, "_play_until", 0.0) or 0.0) if session is not None else 0.0
    return until > time.monotonic()


def _chassis() -> bool:
    try:
        from app import robot_chassis

        return bool(robot_chassis.configured())
    except Exception:                                    # noqa: BLE001
        return False


async def _where():
    """(x, y, heading deg) from the board, or None (no chassis here, or it did not answer)."""
    if not _chassis():
        return None
    try:
        from app import robot_chassis

        pose = await asyncio.wait_for(robot_chassis.pose(), 3.0)
        return float(pose["x"]), float(pose["y"]), math.degrees(float(pose["yaw"]))
    except Exception:                                    # noqa: BLE001
        logger.exception("event tour: no pose for the next leg")
        return None


async def _leg(seq: int, i: int, step: dict) -> bool:
    from app import event_host

    doc = event_host.load() or {}
    spot = (doc.get("spots") or {}).get(step["spot"])
    dock = event_host.dock_doc(doc)
    here = await _where()
    if here is None and _chassis():
        # the turn round needs the heading: the board's MoveTo would turn her round blind otherwise
        _end(seq, "no_pose", where=i)
        return False
    # the counter's walk ends in Khun Miki's handshake: straight in from its head-on point (owner 23 Nov); the walk back
    # after the guests does not (a plan noted before 23 Nov has no `head_on`: as it was)
    via_heading, STATE["via_heading"] = STATE.get("via_heading"), None    # the first leg after the wave only
    at_corner, STATE["at_corner"] = bool(STATE.get("at_corner")), False
    # waved at the corner (`wave_at_via`): she stands on it, facing the guests -- straight up to Khun Miki from there, no
    # corner again (a point under her feet: a turn to nowhere) and no head-on point (that is the way without the corner)
    steps = leg_steps(spot, here, dock, head_on=bool(step.get("head_on")) and not at_corner,
                      via=None if at_corner else step.get("via"), glide=step.get("glide"),
                      end=bool(step.get("back")), via_heading=via_heading)
    if not steps:
        _end(seq, "no_spot", where=i)
        return False
    STATE["phase"] = "walking"
    music("walk", track="guests" if any(s["kind"] == "handshake" for s in STATE["plan"][:i]) else "walk")
    music("unduck")
    hand = STATE.pop("hand_rested", None)
    return await _walk(seq, step["spot"], (spot or {}).get("name_th") or step["spot"], steps, dock, where=i,
                       back=bool(step.get("back")), rested_at=hand, rested_by="handshake" if hand else "rest")


async def _walk(seq: int, spot_id: str, name_th: str, steps: list[dict], dock, *, where: int | None,
                back: bool = False, rested_at: float | None = None, wheels: bool = True, cut: str = "leg_cut",
                box_of: float | None = None, rested_by: str = "rest", soft: bool = False, corner: bool = False) -> bool:
    """One walk of the card, watched to its end: a tour leg (`where` = its step), or the ceremony's walk out of the box
    (`where` None, `rested_at` = the arm rested in the countdown, `wheels` False: held at the box door is its own
    patience, not an E-stop, `cut` = its end short, `box_of` = its forward_m: OUT only with all of it walked, by the
    board's poses -- not out = how far it went kept by the latch, app/event_box.py). Card 1 of the Event page asks for
    room and says it did not get there, as every scene's walk; the ceremony's (cue-only) says nothing."""
    from app import event_host

    await _call_back(seq)                                # the ceremony's: no walk sets out while its call re-dials
    why = _tick(seq)
    if why is not None:
        await _end_and_stop_leg(seq, why)
        return False
    done = asyncio.Event()
    out: dict = {}
    speaks = STATE["owner"] == "event"

    async def ended(run) -> None:
        out.update(state=run.state, reason=run.reason, started=run.saw_started, met=getattr(run, "met", None))
        with contextlib.suppress(Exception):
            out["reason_th"] = run.view().get("reason_th")
        if box_of is not None:
            # the walker's real end (its closing `@@ forward_line`, or none): however it ended -- arrived, cut by a
            # tick here, a person's stop -- where she stands is the box's to know (app/event_box.py: no walker
            # started = the arm back; set out = used for good, how far kept or "not known"), whoever listens now
            from app import event_box

            out["box"] = event_box.walk_ended(run, box_of, tol_m=event_host.WALKED_OUT_TOL_M, first_step=corner)
            out["walked"] = event_walk.walked_m(run)
            out["out_of_box"] = out["box"] == "full"
        if run.state == "arrived" and run.spot != event_host.FORWARD_SPOT:
            event_host._count_arrival(run.spot)
        done.set()

    async def told(run, what: str) -> None:
        # someone in the way: she asks for room, as every scene's walk does (event_host._callbacks)
        if not speaks or what != "held" or STATE["seq"] != seq or STATE["ended"] is not None:
            return
        now = time.monotonic()
        if now - STATE["held_said"] < event_host.HELD_SAY_EVERY_S:
            return
        STATE["held_said"] = now
        await event_host._say(event_host.held_text(), "event_held",
                              lambda: STATE["leg"] is run and event_walk.current() is run and bool(run.held_at))

    _note()
    walk = await event_walk.start(spot_id, name_th, steps, dock=dock, on_end=ended, on_event=told,
                                  stops_seen=STATE["stops_seen"], beat=False, rested_at=rested_at, rested_by=rested_by)
    if not walk.get("ok"):
        if soft and _why_not(seq) is None and walk.get("error") != "stop_latched":
            # `soft` (the turn before the wave): refused before a wheel turned -- Q / the laser's fit just out of the box
            # (a turn-only route is a map route), the arm, the call -- she waves where she stands (review 25 Nov 02:3x:
            # this branch ended the whole opening). A latch from a person's stop (B, the robot's screen) still ends it.
            turnlog.record("event_tour", phase="soft_walk_refused", error=walk.get("error"))
            return False
        # refused before it set out (the arm not at rest, the latch, Q, a stop during the arm's rest ...)
        _end(seq, _why_not(seq) or ("leg_refused" if where is not None else "debut_refused"),
             detail_th=walk.get("error_th"), where=where)
        return False
    run = event_walk.current()
    if run is None or run.id != (walk.get("run") or {}).get("id", run.id):
        run = None
    STATE["leg"] = run
    if box_of is not None:
        STATE["box_run"] = (run.id if run is not None else "?")   # a walker of this press: its own end says the arm
        late = time.monotonic() - float(STATE.get("impact") or time.monotonic())
        if STATE.get("reveal") and late > LATE_NOTE_S:
            # measured once the walker is out (6c, review 23 Nov): the walk's own rest of the arm -- redone inside
            # `event_walk.start` when the build's did not hold -- is in it
            _add_note("walk_out", LATE_TH % late)
            turnlog.record("event_tour", phase="late_walk_out", late_s=round(late, 2))
    turnlog.record("event_tour", phase="leg", spot=spot_id, back=back,
                   turned=any(s.get("do") == "turn" for s in steps))
    first, read_at, moved = None, 0.0, not wheels
    while not done.is_set():
        why = _tick(seq, leg=run)
        if why is None and not moved and run is not None and run.saw_started and run.state == "walking":
            # the wheels since this walker set out (WHEELS_STILL_END_S): no movement at all = an E-stop left in
            now = time.monotonic()
            if now >= read_at:
                read_at = now + POSE_EVERY_S
                pose = await _where()
                if pose is not None:
                    if first is None:
                        first = (pose, now)
                    elif (math.hypot(pose[0] - first[0][0], pose[1] - first[0][1]) >= WHEELS_MOVED_M
                          or abs(_wrap_deg(pose[2] - first[0][2])) >= WHEELS_MOVED_DEG):
                        moved = True
                    elif now - first[1] >= WHEELS_STILL_END_S:
                        why = "wheels_still"
        if why is not None:
            await _end_and_stop_leg(seq, why, leg=run)
            return False
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(done.wait(), TICK_S)
    STATE["leg"] = None
    if out.get("state") == "arrived" and box_of is not None and not out.get("out_of_box"):
        # arrived short of its forward_m: board_walk's "near enough" (something ahead in its last 0.2 m) is not out --
        # nor an arrival with no closing line to measure it by (a pose jump: not known)
        walked = out.get("walked")
        _end(seq, "debut_short", detail_th="ไม่รู้ระยะ" if walked is None else "เดินได้ %.2f จาก %.2f ม." % (walked, box_of))
        return False
    if box_of is not None and corner and out.get("out_of_box") and out.get("state") != "arrived":
        # the walk out done (its forward's own closing line), then stopped on the way to the corner -- by anything, a
        # person's stop too: she is out of the box and the latch must not outlive it (final review 25 Nov 05:5x: every
        # later walk was refused in_box until staff cleared it). Seen still = out; not seen still = latched, as after
        # the walk out itself
        from app import event_box

        if await event_host._wheels_still(lambda: True) is not False:
            event_box.outcome("out")
    STATE["walk_arrived"] = out.get("state") == "arrived"
    if out.get("state") != "arrived" and corner and out.get("out_of_box") and _why_not(seq) is None \
            and out.get("reason") not in event_walk.REASONS_TH:
        # out of the box, all of it, then held on the way to the corner (someone in her way, the board): she waves
        # where she stands -- the walk out is what the card needed (a person's stop still ends it below)
        turnlog.record("event_tour", phase="corner_short", reason=out.get("reason"))
        return True
    if out.get("state") != "arrived" and soft and _why_not(seq) is None and out.get("reason") not in event_walk.REASONS_TH:
        # `soft` (the turn before the wave): a turn the walker itself gave up on (no room, its 20 s) ends nothing --
        # a stop by a person, the page, the face or another press still ends the card below
        turnlog.record("event_tour", phase="soft_walk_short", reason=out.get("reason"))
        return False
    if out.get("state") != "arrived":
        reason = out.get("reason")
        if speaks and out.get("started") and reason not in event_host.QUIET_STOPS:
            # the walk she set out on ended short: as every scene's walk (event_host._callbacks)
            event_host._spawn(event_host._say(event_host.trouble_text(name_th or ""),
                                              "event_walk_trouble", lambda: event_host.STATE["seq"] == seq))
        # a stop event_walk names (a person's, another press, the page, the face, B) is said as that; board_walk's
        # own (someone in the way, the board gave up, Q) as "did not get there" with the walk's Thai for it
        known = isinstance(reason, str) and reason in event_walk.REASONS_TH
        _end(seq, reason if known else cut, detail_th=None if known else out.get("reason_th"), where=where)
        return False
    STATE["met"] = bool(out.get("met"))                  # whom this walk walked up to: an `if_met` hand's question
    return True


async def _handshake(seq: int, i: int, step: dict) -> bool:
    """Staff's handshake (robot_body.staff_request: every arm gate but the heard guard), waited out: the app's own
    terminal row for it after the request (robot_body.STATE). Nothing said, unless the step has a recorded `line` (owner
    24 Nov, Khun Miki's): it plays once the app says the hand is going out, and is over before the next walk."""
    from app import event_host, robot_body
    from app import robot_contracts as rc

    STATE["phase"] = "settling"
    STATE.pop("hand_rested", None)
    _note()
    first = not any(s["kind"] == "handshake" for s in STATE["plan"][:i])
    if first and music("hit"):
        # Khun Miki's: the music is off before the hand goes up, and she says her line into the quiet (owner 24 Nov)
        quiet_till = time.monotonic() + HAND_AFTER_MUSIC_S
        while time.monotonic() < quiet_till:
            why = _tick(seq)
            if why is not None:
                await _end_and_stop_leg(seq, why)
                return False
            await asyncio.sleep(0.1)
    deadline = time.monotonic() + event_host.STILL_WAIT_S
    while event_host._robot_settling() and time.monotonic() < deadline:
        why = _tick(seq)
        if why is not None:
            await _end_and_stop_leg(seq, why)
            return False
        await asyncio.sleep(0.25)
    if robot_body._staff_session() is None:
        up = await event_walk.call_for(lambda: _why_not(seq) is None, reason="event_tour")
        if not up:
            why = _why_not(seq)
            _end(seq, why or "no_call", where=i)
            return False
    why = _tick(seq)
    if why is not None:
        await _end_and_stop_leg(seq, why)
        return False
    STATE["phase"] = "handshake"
    _note()
    asked = time.time()
    result = await event_host._gesture("handshake")
    turnlog.record("event_tour", phase="handshake", spot=step["spot"], ok=bool(result.get("ok")),
                   error=None if result.get("ok") else result.get("error"))
    if not result.get("ok"):
        _end(seq, _why_not(seq) or "handshake_refused", detail_th=result.get("error_th"), where=i)
        return False
    t0 = time.monotonic()
    started = False
    begun: float | None = None
    saying: asyncio.Task | None = None
    while True:
        why = _tick(seq)
        if why is not None:
            await _end_and_stop_leg(seq, why)
            return False
        st = robot_body.STATE
        if st.get("last_action") == "handshake" and float(st.get("at") or 0.0) >= asked:
            state = st.get("state")
            if state == "started" and begun is None:
                begun = float(st.get("at") or 0.0)
            if step.get("line") and saying is None and state in ("started", "done"):
                saying = asyncio.get_running_loop().create_task(_say_line(seq, i, step))
            if state == "done":
                at = float(st.get("at") or 0.0)
                if begun is not None and 0.0 < at - begun < HANDSHAKE_SURE_S:
                    STATE["hand_rested"] = at            # the next walk's arm rest: the app has just put it there
                return await _line_over(seq, saying)
            if state in ("failed", "stopped"):
                _end(seq, "handshake_" + state, detail_th=rc.body_reason_th(st.get("reason")) if st.get("reason") else None,
                     where=i)
                return False
            started = True
        waited = time.monotonic() - t0
        if not started and waited > HANDSHAKE_START_S:
            _end(seq, "handshake_silent", where=i)
            return False
        if waited > HANDSHAKE_DONE_S:
            _end(seq, "handshake_no_end", where=i)
            return False
        await asyncio.sleep(TICK_S)


async def _end_and_stop_leg(seq: int, why: str, *, leg=None) -> None:
    """Ended, and its own walk stopped if one is still on (a person's stop has stopped it already: its reason
    stays the person's -- event_walk._stop_run keeps the first)."""
    leg = leg if leg is not None else (STATE.get("leg") if STATE["seq"] == seq else None)
    _end(seq, why)
    if leg is not None and leg.state in ("starting", "walking"):
        with contextlib.suppress(Exception):
            await event_walk._stop_run(leg, "cancelled")


def _end(seq: int, why: str, *, detail_th: str | None = None, where: int | None = None) -> None:
    if STATE["seq"] != seq or STATE["ended"] is not None:
        return
    if where is not None:
        STATE["at"] = where
    STATE.update(ended=why, detail_th=detail_th, ended_at=time.time(), leg=None)
    music("hit" if why in GOOD else "off")
    for row in STATE.get("opening") or []:
        if row["state"] == "now":
            row["state"] = "cut"                         # the ceremony's opening step it stopped at
    if STATE.get("box_press") and STATE.get("box_run") is None and STATE["owner"] == "ceremony":
        # the opening ended before any walker of its walk out (the build cut, the screen refused, the walk refused at
        # its own gate): positively never moved -- the arm comes back (app/event_box.py)
        from app import event_box

        event_box.never_moved()
    if STATE["owner"] == "ceremony" and why not in GOOD:
        # every way the ceremony's opening ends short takes the screen's debut down too (owner 23 Nov, the red stop
        # during the countdown = no walk out): ceremony_reveal hears the server's own screen stops, not a socket
        # gone on the screen's side. Idempotent there; a run already over is no harm.
        _abort_reveal(why)
        # ...and its hand down, whatever ended it (review 23 Nov: only the red stop did -- an order reset, the call
        # gone, the page quiet left it waving to the app's 45 s cap)
        _hand_down(seq, why)
    _let_go()
    turnlog.record("event_tour", phase="end", why=why, at=STATE["at"], steps=len(STATE["plan"]))
    logger.info("event tour: ended (%s) at step %s of %d", why, STATE["at"], len(STATE["plan"]))
    _note()


# ---- the ceremony's opening: card 1 as the Emma Ceremony app's first step (owner 23 Nov) --------------------------
# Owner 23 Nov 2026: "ช่วงพิธีอยู่แอปพิธีทั้งหมด" -- on stage nothing switches apps: card 1's whole sequence is ONE step of
# the ceremony's running order (`{"do": "opening", "scene": "debut"}`, app/event_order.py), pressed in the Emma Ceremony
# app; the Event page's card 1 stays as it is for outside the ceremony. In order ("ดับไฟก่อน ปิดม่านจากนั้นก็เอาซาวน์
# นับถอยหลัง 10 9 8 ขึ้นจอด้วยโดยเมื่อถึง 0 ก็ให้ทุกอย่างมืด แล้วก็มีซาวน์ แล้วหุ่นก็เปิดตัว"):
#   1. the room screen's debut (app/ceremony_reveal.py: the build, black, the hit -- the owner took the countdown out on
#      23 Nov evening: black ~5.5 s, the hit ~7.0 s from the press, read from the screen's own answer, never assumed) --
#      meanwhile, in the box, her arm is rested (the walk's own rest, asked early: the wheels go on the hit,
#      `event_walk.start(rested_at=...)`; a rest slower than the build = the walk out late, said on its step);
#   2. on the hit -- only if the screen's run was not taken down before it -- the walk out of the box, arms down (the
#      scene's walk: event_host.debut_walk; the stage team opens the door on the hit, the walk's hold waits for it);
#   3. out and still: the standing pageant, the recorded line through event_cues.play_line after its lead, the hand down
#      after the line (`_wave_and_line`);
#   4. the tour: the counter's handshake, the guests', back to the counter (`_tour`, as card 1).
# The ceremony's rules hold: she says nothing but that recorded line (cue-only: no "excuse me", no "I did not get
# there" -- her mic is deaf but while she moves, app/ceremony.py), her head is held as on card 1, the step owns it
# through the order's own press (its gen, its mark, its stops: `_ceremony_lost`). Every way it ends short takes the
# screen's debut down (`_end` -> `_abort_reveal`). Nothing here has run on the robot.

#: The ceremony's presses are numbered from here: never one of event_host's (`seq` also names the pageant's run).
CEREMONY_SEQ_BASE = 1_000_000
#: The ceremony app's call gone this long (no other call up) is a re-dial, waited for (`_ceremony_lost`).
CALL_GRACE_S = 10.0
#: A step that needs the call (the pageant, the line) waits this long for it to be back.
CALL_WAIT_S = CALL_GRACE_S
_CEREMONY = {"n": 0}
#: The opening's first three steps on the phone.
OPENING_TH = {"reveal": "จอเปิดตัว: ดนตรีขึ้น → จอมืด → เสียงกระแทก (หุ่นรอในกล่อง แขนพัก)",
              "no_reveal": "ไม่มีจอเปิดตัว — เริ่มทันที",
              "walk_out": "เดินออกจากกล่อง (แขนลง)",
              "wave": "หยุดนิ่งแล้วยกมือโบก + พูดประโยคเปิดตัว จบแล้วเอามือลง"}
RUNNING_CEREMONY_TH = "การ์ดเปิดตัวกำลังทำต่อเนื่องอยู่ — กดหยุดสีแดงก่อน แล้วค่อยกดใหม่"
#: The screen cannot count down (no room screen, not in the ceremony's show mode): the step is not ready, and the page
#: offers starting without it on purpose (two taps) -- never quietly.
WITHOUT_SCREEN_TH = "กด 'เริ่มโดยไม่มีจอเปิดตัว' (แตะสองครั้ง) ถ้าจะเปิดตัวโดยไม่มีจอ"
#: The walk out this much after the hit is said on its step: the arm's rest asked in the build (~7 s since the countdown
#: went, 23 Nov) took longer, or failed and the walk rests the arm itself -- never shown as "on the hit".
LATE_NOTE_S = 0.5
LATE_TH = "ออกจากกล่องช้ากว่าเสียงกระแทก %.1f วิ (รอแขนพักเสร็จ)"
REST_AGAIN_TH = "การพักแขนระหว่างจอเปิดตัวไม่สำเร็จ — พักแขนอีกครั้งหลังเสียงกระแทก หุ่นออกช้ากว่าจังหวะ"
NO_SCREEN_CODES = ("no_screen", "not_ceremony")        # (only small screens is a warning: `small_screen_only`)
#: The recorded line's word (event_host.recorded_pcm): in the ceremony nobody reads it instead (cue-only).
LINE_WHY_TH = {"off": "เสียงอัดปิดอยู่บนเครื่องนี้ (EVENT_RECORDED_LINES)",
               "missing": "ไม่มีไฟล์เสียงอัดบนเครื่องนี้ (ยังไม่ deploy)",
               "not_the_take": "ไฟล์เสียงอัดไม่ใช่เทคที่ปักไว้",
               "format": "ไฟล์เสียงอัดผิดรูปแบบ",
               None: "ฉากนี้ไม่มีเสียงอัด"}
LINE_NOT_SAID_TH = "ประโยคเปิดตัวจะไม่ถูกพูด: %s (ในพิธี Emma ไม่พูดเอง)"
#: The readiness reads two files (the recorded line, the reveal's assets) in a thread, kept this long.
CONTEXT_TTL_S = 3.0
_CONTEXT: dict = {"at": None, "key": None, "value": None}


def _ceremony_lost() -> str | None:
    """The ceremony's opening is no longer its press's: its app off the robot, the order reset, a stop of the order
    since (the red stop, a cut, a person's stop: event_order._stop_gen), its step pressed or skipped over it."""
    try:
        from app import ceremony, event_order
    except Exception:                                    # noqa: BLE001 -- not knowing is not ours
        return "error"
    order = STATE.get("order") or {}
    if not ceremony.on():
        # a call dropped and dialling again (review 23 Nov: one network hiccup ended the opening mid-walk): no call at
        # all for CALL_GRACE_S is a re-dial, waited for -- the walk under way keeps its own switches (the page, the
        # robot face's link, B, every stop). Another app's call up, or longer, ends it.
        from app import session as session_module

        if session_module._active is not None:
            return "ceremony_app_off"
        lost = STATE["call_lost_at"] = STATE.get("call_lost_at") or time.monotonic()
        if time.monotonic() - lost > CALL_GRACE_S:
            return "ceremony_app_off"
    else:
        STATE["call_lost_at"] = None
    if event_order.STATE.get("gen") != order.get("gen"):
        return "order_reset"
    if order.get("stops") is not None and event_order._stop_gen() != order["stops"]:
        return event_walk.STATE.get("last_stop") or "staff"
    if event_order.STATE["marks"].get(order.get("key")) is not order.get("mark"):
        return "replaced"
    return None


#: The ends that take the screen's debut down even AFTER its hit: a person stopped it (the red stop, a voice "หยุด",
#: the desk, B on the robot), staff pressed something else or restarted the order, the ceremony's call is gone (the
#: page leaves show mode anyway), the server restarted. Every other end after the hit is the ROBOT's (a pose jump on the
#: walk out, a walk short, a handshake refused, ...) and the room's screen plays on to its held logo -- owner 23 Nov on
#: the robot: the walk out stopped on a pose jump 6 s after the hit, the wave was cut mid-screen and the logo came up,
#: "มันดูไม่สมูทเลย ปรับเลย". Before the hit every end still takes it down (no walk out without the screen's hit).
SCREEN_DOWN_AFTER_HIT = frozenset(event_walk.PERSON_STOPS | {"app_stop", "replaced", "order_reset", "ceremony_app_off",
                                                            "restarted"})


def _abort_reveal(why: str) -> None:
    """The screen's debut down, when this opening had one and it may still be on (`_end`) -- after its hit only for
    a person's / staff's end (SCREEN_DOWN_AFTER_HIT)."""
    if not STATE.get("reveal"):
        return
    try:
        from app import ceremony_reveal

        now = ceremony_reveal.where()
        if now is not None and now.get("phase") == "impact" and why not in SCREEN_DOWN_AFTER_HIT:
            turnlog.record("event_tour", phase="reveal_kept", why=why, ends_in=now.get("ends_in"))
            return
        if STATE.get("phase") in OPENING or ceremony_reveal.running():
            ceremony_reveal.abort(why)
    except Exception:                                    # noqa: BLE001 -- the stop of the rest goes on
        logger.exception("event tour: the screen's debut could not be taken down")


def _hand_down(seq, why: str) -> None:
    """The opening's pageant down, when it is this press's (any end but the round's own)."""
    try:
        from app import event_pageant

        if event_pageant.active(seq):
            _spawn(event_pageant.end("tour_" + str(why)[:16]))
    except Exception:                                    # noqa: BLE001 -- the stop of the rest goes on
        logger.exception("event tour: the opening's hand could not be brought down")


def _spawn(coro) -> None:
    try:
        asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()                                     # no loop (a script, a test's sync end): nothing to bring down


def _add_note(key: str, note: str) -> None:
    """A note on one of the opening's rows, kept beside any it has."""
    for row in STATE.get("opening") or []:
        if row["key"] == key:
            row["note_th"] = " · ".join(n for n in (row.get("note_th"), note) if n)


async def _call_back(seq: int) -> None:
    """Until the ceremony app's call is up again (a re-dial): `_ceremony_lost` ends the press once its grace is over
    (CALL_GRACE_S), so this waits that long at most. Nothing new starts without it -- a walk (`_walk`), the pageant
    and the line (`_wave_and_line`); what is under way when it drops goes on by its own switches."""
    if STATE.get("owner") != "ceremony":
        return
    from app import ceremony

    deadline = time.monotonic() + CALL_WAIT_S + 1.0
    while not ceremony.on() and time.monotonic() < deadline and _why_not(seq) is None:
        await asyncio.sleep(TICK_S)


def _opening(key: str, state: str, note_th: str | None = None) -> None:
    for row in STATE.get("opening") or []:
        if row["key"] == key:
            row["state"] = state
            if note_th is not None:
                row["note_th"] = note_th


def _context_now(scene_id: str | None) -> dict:
    """In a thread: the recorded line's word and the screen's readiness (files read, hashed)."""
    from app import ceremony_reveal, event_host

    sc = event_host.scene(scene_id) if scene_id else None
    line = None
    if sc is not None:
        pcm, why = event_host.recorded_pcm(sc)
        line = "ok" if pcm is not None else why
    try:
        reveal = ceremony_reveal.ready()
        warn = list(ceremony_reveal.warnings() or [])     # every warning, not ready()'s first (23 Nov)
    except Exception:                                    # noqa: BLE001 -- a screen we cannot ask is no screen
        logger.exception("event tour: the screen's debut readiness")
        reveal, warn = (False, "no_screen"), []
    return {"line": line, "reveal": reveal, "warnings": warn}


async def ceremony_context(scene_id: str | None) -> dict:
    """What `ceremony_view` reads that costs a file read (app/event_order.py `_context`, once per poll)."""
    now = time.monotonic()
    if _CONTEXT["value"] is None or _CONTEXT["key"] != scene_id or now - _CONTEXT["at"] >= CONTEXT_TTL_S:
        value = await asyncio.to_thread(_context_now, scene_id)
        _CONTEXT.update(at=now, key=scene_id, value=value)
    return _CONTEXT["value"]


def ceremony_view(scene_id: str | None, ctx: dict | None, *, without_screen: bool = False) -> dict:
    """The opening step on the ceremony page, before and after the press: {"ready", "why_th", "warn_th": [...],
    "no_screen": no hall screen to play the debut (the page's "เริ่มโดยไม่มีจอเปิดตัว"), "steps_th": what one press
    does, "turns", "missing", "tour": `view()` of this step's last press (None before)}."""
    from app import ceremony_reveal, event_host

    sc = event_host.scene(scene_id) if scene_id else None
    ctx = ctx or {}
    why: list[str] = []
    warn: list[str] = []
    no_screen = False
    if sc is None or not sc.get("walk_out_of_box"):
        why.append("ไม่พบฉากเปิดตัว '%s' ในไฟล์งาน" % scene_id)
    if not settings.event_walk_enabled:
        why.append(event_walk.REASONS_TH["disabled"] + " — การเปิดตัวคือเดินออกจากกล่อง")
    if sc is not None and not sc.get("forward_m") and event_host.spot_steps(
            ((event_host.load() or {}).get("spots") or {}).get(sc.get("spot"))) is None:
        why.append(event_walk.REASONS_TH["no_steps"])
    if active():
        why.append(RUNNING_CEREMONY_TH)
    from app import event_box

    if not active() and not event_box.armed():
        # single use (second review 23 Nov): only the order's in-box tick arms the walk out -- out, partly out or not
        # known, a press would walk another whole forward_m (resetting the order does not arm it)
        why.append(event_box.NOT_ARMED_CEREMONY_TH)
    ok, code = ctx.get("reveal") or (False, "no_screen")
    if code in NO_SCREEN_CODES:
        no_screen = True
        if without_screen:
            warn.append("ไม่มีจอเปิดตัว (%s)" % ceremony_reveal.why_th(code))   # staff chose it, two taps
        else:
            why.append(ceremony_reveal.why_th(code) + " — " + WITHOUT_SCREEN_TH)
    elif code:
        # it runs: silent, or the logo without the wave -- and ready()'s (True, code) warnings (only small screens, no
        # screen playing the sound aloud, two of them, the mix not listened to): said, never a refusal
        warn.append(ceremony_reveal.why_th(code))
    if code not in NO_SCREEN_CODES:
        for w in ctx.get("warnings") or []:
            if w != code:
                warn.append(ceremony_reveal.why_th(w))   # all of them, each once
    line = ctx.get("line")
    if sc is not None and line != "ok":
        warn.append(LINE_NOT_SAID_TH % LINE_WHY_TH.get(line, str(line)))
    plan = planned(sc.get("tour"), (event_host.load() or {}).get("spots") or {}) if (
        sc is not None and settings.event_tour and sc.get("tour")) else None
    steps_th = [OPENING_TH["reveal"], OPENING_TH["walk_out"], OPENING_TH["wave"]]
    if plan is None:
        if sc is not None:
            warn.append(WHY_TH["tour_off"] if sc.get("tour") else "ฉากนี้ไม่มีการ์ดต่อเนื่อง — เปิดตัวอย่างเดียว")
    elif not plan["counter_ok"]:
        warn.append("ยังไม่ได้บันทึกจุด%s — เปิดตัวอย่างเดียว ไม่ไปต่อ" % plan["counter"]["name_th"])
    else:
        steps_th.append("ไป%s ยื่นมือจับมือ%s" % (plan["counter"]["name_th"],
                                              " พร้อมคำทัก" if any(s.get("line") for s in plan["steps"]) else " (ไม่พูด)"))
        if plan["guests_ok"]:
            steps_th.append("ไปจับมือแขก %d จุด (%s)" % (len(plan["guests"]),
                                                       " → ".join(g["name_th"] for g in plan["guests"])))
            steps_th.append(_end_th(plan))
        elif plan["walk_up_ok"]:
            steps_th.append(WALK_UP_STEP_TH)
            steps_th.append(_end_th(plan))
            warn.append(WALK_UP_MISSING_TH % (", ".join(plan["missing"]), plan["counter"]["name_th"]))
        else:
            steps_th.append("ยืนรอที่%s (ไม่ไปหาแขก)" % plan["counter"]["name_th"])
            warn.append("ยังไม่ได้บันทึกจุด: %s — จับมือที่%s แล้วยืนรอตรงนั้น ไม่ไปหาแขกเลย"
                        % (", ".join(plan["missing"]), plan["counter"]["name_th"]))
    mine = STATE["owner"] == "ceremony" and STATE["seq"] is not None and STATE["scene"] == scene_id
    # pressable on purpose without the screen: the screen is its only reason (the page's own two-tap button)
    others = [w for w in why if not w.endswith(WITHOUT_SCREEN_TH)]
    return {"ready": not why, "why_th": " · ".join(why) or None, "warn_th": warn, "no_screen": no_screen,
            "ready_without_screen": no_screen and not others,
            "steps_th": steps_th, "turns": (plan or {}).get("turns") or [], "missing": (plan or {}).get("missing") or [],
            "tour": view() if mine else None}


def _end_th(plan: dict) -> str:
    """Where the round ends, for the page: back at the counter facing it (as before), or the tour's own end spot."""
    back = next((s for s in plan["steps"] if s.get("back")), None)
    if back is None or back["spot"] == plan["counter"]["id"]:
        return "กลับ%s หันเข้าเคาน์เตอร์" % plan["counter"]["name_th"]
    return "ไป%s" % back["name_th"]


def ceremony_running() -> bool:
    """The ceremony's opening is under way: every other press of the order but a tick waits for it (event_order)."""
    return active() and STATE["owner"] == "ceremony"


def ceremony_result(seq) -> tuple[bool, str | None] | None:
    """For the order's mark of that press: None = still going; (True, a note or None) = it went as planned (the
    whole round, or all the recorded spots allowed); (False, why) = it ended short."""
    if STATE["seq"] != seq or STATE["owner"] != "ceremony":
        return False, WHY_TH["replaced"]
    ended = STATE["ended"]
    if ended is None:
        if not active():
            return False, WHY_TH["error"]                # a run nothing holds any more
        return None
    note = _why_th(ended)
    if STATE.get("detail_th"):
        note = "%s (%s)" % (note, STATE["detail_th"])
    if ended in GOOD:
        return True, None if ended == "done" else note
    return False, note


def owns_walk(run) -> bool:
    """app/ceremony.py `_ceremony_walk`: a walk under way while the ceremony's opening runs is its own (it ends on any
    walk it did not start), and so is one its card's "by hand" sent (`hand_walk`) -- a call that re-dials mid-walk
    must not stop it."""
    if run is None:
        return False
    return ceremony_running() or (STATE.get("owner") == "ceremony" and getattr(run, "id", None) == STATE.get("hand_run"))


def hand_walk(run_id) -> None:
    """A walk the ceremony page's "by hand" button of this card started (app/event_order.py `opening_hand`)."""
    STATE["hand_run"] = run_id


def hand_pressed(k: int) -> None:
    """A step left of the card was pressed by hand and its path took it: the page shows it taken (kept: tour.json)."""
    STATE.setdefault("hand_done", []).append(int(k))
    _note()


async def begin_ceremony(scene_id: str | None, *, order: dict, without_screen: bool = False) -> dict:
    """The ceremony's opening step pressed (app/event_order.py `_dispatch`; `order` = that press: {"gen", "key",
    "mark", "stops"}). {"ok": True, "seq", "reveal": bool, "impact_in_s"} = under way -- the screen's countdown
    sent, or (`without_screen`, staff's deliberate choice) the walk out of the box about to go; else
    {"ok": False, "error", "error_th"} and nothing started. The screen not able to count down is refused unless
    `without_screen`: never a debut that quietly skips its countdown."""
    from app import ceremony_reveal, event_host

    sc = event_host.scene(scene_id) if scene_id else None
    if sc is None or not sc.get("walk_out_of_box"):
        return {"ok": False, "error": "bad_step", "error_th": "ไม่พบฉากเปิดตัว '%s' ในไฟล์งาน" % scene_id}
    if active():
        return {"ok": False, "error": "tour_running", "error_th": RUNNING_CEREMONY_TH}
    if not settings.event_walk_enabled:
        return {"ok": False, "error": "disabled", "error_th": event_walk.REASONS_TH["disabled"]}
    from app import event_box

    if not event_box.armed():
        # single use: only staff's word that she is in the box (the order's in-box tick) arms it (app/event_box.py)
        return {"ok": False, "error": "not_armed", "error_th": event_box.NOT_ARMED_CEREMONY_TH}
    if not without_screen:
        ok, code = ceremony_reveal.ready()
        if code in NO_SCREEN_CODES:
            return {"ok": False, "error": "no_screen",
                    "error_th": ceremony_reveal.why_th(code) + " — " + WITHOUT_SCREEN_TH}
    task = STATE.get("task")
    if task is not None and not task.done():
        task.cancel()
    _CEREMONY["n"] += 1
    seq = CEREMONY_SEQ_BASE + _CEREMONY["n"]
    plan = planned(sc.get("tour"), (event_host.load() or {}).get("spots") or {}) if (
        settings.event_tour and sc.get("tour")) else None
    stops = order.get("stops")
    STATE.update(owner="ceremony", order=order, seq=seq, scene=sc["id"], plan=(plan or {}).get("steps") or [], at=None,
                 phase="reveal", ended=None, detail_th=None, since=time.time(), ended_at=None,
                 stops_seen=stops[2] if isinstance(stops, tuple) and len(stops) == 3 else event_walk.stops(),
                 task=None, leg=None, face_lost_at=None, missing=(plan or {}).get("missing") or [],
                 counter_ok=bool((plan or {}).get("counter_ok")), reveal=None, tour_on=plan is not None,
                 hand_done=[], skipped=[], met=None, hand_run=None, call_lost_at=None, impact=None, box_press=True, box_run=None, via_heading=None, at_corner=False, corner_walk=False,
                 opening=[{"key": "no_reveal" if without_screen else "reveal", "state": "now", "note_th": None},
                          {"key": "walk_out", "state": "todo", "note_th": None},
                          {"key": "wave", "state": "todo", "note_th": None}])
    _hold(DEBUT_HOLD_S)
    # the arm used by this press (on disk: a restart finds it "not known"); the latch held since the tick
    event_box.press("ceremony_opening", sc.get("forward_m"))
    impact_in = 0.0
    if not without_screen:
        r = await ceremony_reveal.start()
        if not r.get("ok"):
            text = ceremony_reveal.why_th(r.get("why"))
            _end(seq, "reveal_refused", detail_th=text)
            return {"ok": False, "error": "reveal_refused", "error_th": "%s: %s" % (WHY_TH["reveal_refused"], text)}
        STATE["reveal"] = r.get("run") or None
        impact_in = float(r.get("impact_in_s") or 0.0)
        # its hit as the screen gave it (8e, review 23 Nov): `impact_at()` gone None or cancelled before then = no walk
        STATE["impact"] = time.monotonic() + impact_in
        said = [r["why"]] if r.get("why") else []
        said += [w for w in r.get("warnings") or [] if w not in said]
        if said:
            # silent, the logo without the wave, and every warning the screen gave (23 Nov: all, not the first)
            _opening("reveal", "now", " · ".join(ceremony_reveal.why_th(w) for w in said))
    turnlog.record("event_tour", phase="pressed", scene=sc["id"], owner="ceremony", steps=len(STATE["plan"]),
                   missing=len(STATE["missing"]), counter=STATE["counter_ok"], screen=not without_screen)
    _note()
    STATE["task"] = asyncio.get_running_loop().create_task(_ceremony_run(seq, sc))
    return {"ok": True, "seq": seq, "reveal": STATE["reveal"] is not None, "impact_in_s": round(impact_in, 2)}


async def _rest_ahead() -> float | None:
    """The walk's own arm rest, asked during the countdown: the time it was asked when the app said done."""
    if not settings.event_walk_arm_rest:
        return None
    asked = time.time()
    try:
        return asked if await event_walk.rest_arm() else None
    except Exception:                                    # noqa: BLE001 -- the walk asks again itself
        logger.exception("event tour: the arm's rest before the walk out")
        return None


async def _until_hit(seq: int, rest: asyncio.Task) -> bool:
    """The screen's countdown, to its hit (app/ceremony_reveal.py) -- and the arm's rest asked meanwhile, to its end.
    False = it ended (a stop, the run taken down before its hit: no walk out)."""
    from app import ceremony_reveal

    run = STATE.get("reveal")
    hit = run is None
    while True:
        why = _tick(seq)
        if why is not None:
            rest.cancel()
            await _end_and_stop_leg(seq, why)
            return False
        if not hit:
            at = ceremony_reveal.impact_at()
            due = STATE.get("impact") or 0.0
            if ceremony_reveal.cancelled_before_impact(run) or (at is None and time.monotonic() < due):
                # taken down before its hit -- the screen's own stop, the red stop, or the server no longer having
                # the run at all (a screen socket gone, review 23 Nov): the room never saw it, she stays in the box
                rest.cancel()
                _end(seq, "reveal_cut")
                return False
            hit = time.monotonic() >= (at if at is not None else due)
            if hit:
                music("walk")                            # on the opening's own hit; its ring dies away under it
                _opening("reveal", "done")
        if hit and rest.done():
            return True
        left = (STATE.get("impact") or 0.0) - time.monotonic()
        await asyncio.sleep(TICK_S if hit else max(0.01, min(TICK_S, left)))


async def _ceremony_run(seq: int, sc: dict) -> None:
    from app import event_host

    rest = asyncio.get_running_loop().create_task(_rest_ahead())
    # where the corner lies, read in the box during the countdown -- not after the hit (review 04:5x: up to the pose's 3 s
    # between the hit and the walk out)
    ahead = asyncio.get_running_loop().create_task(_corner_ahead(sc))
    try:
        if not await _until_hit(seq, rest):
            ahead.cancel()
            return
        rested_at = rest.result()
        if STATE["opening"][0]["key"] == "no_reveal":
            _opening("no_reveal", "done")
        STATE["phase"] = "walk_out"
        _opening("walk_out", "now")
        if rested_at is None and settings.event_walk_arm_rest:
            _add_note("walk_out", REST_AGAIN_TH)          # the walk rests the arm itself, after the hit (`_walk`)
        walk = await event_host.debut_walk(sc)
        if walk.get("ok") is False:
            _end(seq, "debut_refused", detail_th=walk.get("error_th"))
            return
        steps = list(walk["steps"])
        try:
            corner = await ahead
        except Exception:                                # noqa: BLE001 -- no corner = the walk out alone, as ever
            logger.exception("event tour: the corner")
            corner = None
        if corner is not None:
            # owner 25 Nov ~04:25: "เดินออกจากกล่อง → ไปมุม รวมกันเลยไม่ได้หรอ จะได้ไม่ต้องรอแล้วเสียเวลา" -- one walker: out of
            # the box, straight on to the corner, the turn to the guests at its end (the glide's own turn)
            steps.append({"glide": [[corner[0], corner[1]]], "face": corner[2], "speed": corner[3],
                          "label": "ไปมุมแล้วหันหาแขก", "after_box": True})
            STATE["corner_walk"] = True
        if not await _walk(seq, walk["spot"], walk["name_th"], steps, walk["dock"], where=None,
                           rested_at=rested_at, wheels=False, cut="debut_cut",
                           box_of=float(sc.get("forward_m") or 0.0), corner=corner is not None):
            return
        if corner is not None:
            STATE["at_corner"] = bool(STATE.get("walk_arrived"))
            turnlog.record("event_tour", phase="wave_spot", corner=True, turned=STATE["at_corner"], one_walk=True)
        _opening("walk_out", "done")
        if not await _wave_and_line(seq, sc):
            return
        if not STATE.get("tour_on"):
            _end(seq, "tour_off")
            return
        if not STATE["counter_ok"]:
            _end(seq, "no_counter")
            return
        STATE["phase"] = "after_debut"
        _note()
        if not await _debut_over(seq):
            return
        await _tour(seq)
        if STATE["seq"] == seq and STATE.get("ended") == "done":
            # owner 25 Nov 00:09: "ไปถึงตำแหน่งหุ่นจะการ์ดสองเองเลย" -- the whole round ended at card 2's spot: card 2 (the talk with
            # Khun Miki) starts by itself, through the order's own press and gates (another session's code; none = nothing)
            asyncio.get_running_loop().create_task(_card_two(seq))
    except asyncio.CancelledError:
        if STATE["seq"] == seq and STATE["ended"] is None:
            _end(seq, "replaced")
        raise
    except Exception:                                    # noqa: BLE001 -- a run that dies says so, and lets go
        logger.exception("event tour: the ceremony's opening failed")
        await _end_and_stop_leg(seq, "error")
    finally:
        if not rest.done():
            rest.cancel()


async def _card_two(seq: int) -> None:
    """Card 2 after a whole card 1 (`_ceremony_run`): app/event_order.follow_after_opening, when it is there. Never a
    reason for card 1 to have failed: logged, and staff press card 2 by hand."""
    try:
        from app import event_order

        start = getattr(event_order, "follow_after_opening", None)
        if start is None:
            return
        result = start()
        if asyncio.iscoroutine(result):
            result = await result
        turnlog.record("event_tour", phase="card_two", seq=seq,
                       ok=bool(result.get("ok")) if isinstance(result, dict) else bool(result))
    except Exception:                                    # noqa: BLE001 -- card 1 is over either way
        logger.exception("event tour: card 2 after card 1")


async def _wave_and_line(seq: int, sc: dict) -> bool:
    """Out of the box: once the wheels stand still, the standing pageant, the recorded line after its lead
    (event_pageant.hand_up), the hand down after the line (`end_after_line`, standing: one turn each way). Not seen
    still = the line with no arm (event_host's box rule). The pageant refused (an app without it): the scene's
    `gesture` once her first word is out, as card 1's fallback. The line that cannot play is NOT said by her
    instead (the ceremony is cue-only): the step says so."""
    from app import event_cues, event_host, event_pageant

    STATE["phase"] = "waving"
    _opening("wave", "now")
    _note()

    def still() -> bool:
        return _why_not(seq) is None

    settled = await event_host._wheels_still(still)
    why = _tick(seq)
    if why is not None:
        await _end_and_stop_leg(seq, why)
        return False
    from app import event_box

    notes: list[str] = []
    if settled is not False:
        event_box.outcome("out")                         # out of the box, all of it, and still: the arm is hers again
    else:
        notes.append(event_host.NOT_STILL_TH)            # the latch stays: no arm until staff look and clear it
    face = sc.get("wave_face")
    if STATE.get("corner_walk") and STATE.get("at_corner"):
        face = None                                      # the walk out went on to the corner and turned there itself
    elif STATE.get("corner_walk"):
        notes.append("ไม่ได้ไปมุมก่อนโบก")              # held short of it: the slow turn to the guests where she stands
    if settled is not False and isinstance(face, (int, float)) and not isinstance(face, bool):
        # owner 25 Nov 01:5x: "ถ้าโบกทักทายเปลี่ยนเป็นตอนหุ่นเดินตรงมาแล้วหันขวาไปทางแขกก่อนค่อยยกมือทักทาย" -- out of the box,
        # a slow watched turn to face the guests (the scene's `wave_face`), then the wave. A turn that does not happen
        # leaves her where she stands and the wave goes on: never a reason to end the opening.
        doc = event_host.load() or {}
        out_at = await _where()
        STATE["via_heading"] = out_at[2] if out_at is not None else None
        steps = [{"do": "turn", "face": float(face), "w_spin": WAVE_TURN_W, "label": "หันหาแขก"}]
        # owner 25 Nov ~03:10: "จุดจะให้หุ่นโยกมือทักทายท่านางงามคือให้หุ่นเดินมาที่มุมก่อนจะเลี้ยวหาคุณมิกิ" -- with the scene's
        # `wave_at_via`, on straight to the tour's corner first (it lies ahead of the walk out: the same line the first
        # leg used to take), then the turn there. Not ahead (the box moved) = the turn where she stands, as before.
        tour = parse(sc.get("tour"), doc.get("spots") or {}) if (
            sc.get("wave_at_via") and settings.event_tour and not STATE.get("corner_walk")) else None
        via = (tour or {}).get("via") if (tour or {}).get("counter") else None   # no tour after it: no corner either
        corner = via[0] if via and out_at is not None and via_ahead(via, out_at) else None
        if corner is not None:
            speed = float((tour or {}).get("glide") or event_host.DEFAULT_SPEED)
            steps = [{"glide": [[float(corner[0]), float(corner[1])]], "speed": speed,
                      "label": "ไปมุมก่อนเลี้ยวหาคุณมิกิ"}] + steps
        # `wheels=False`: someone in her way at its start is the walker's own patience (soft: she waves where she
        # stands), not 15 s of still wheels ending the whole opening (review 25 Nov 03:3x)
        turned = await _walk(seq, event_host.FORWARD_SPOT, "ไปมุมแล้วหันหาแขก" if corner is not None else "หันหาแขก",
                             steps, event_host.dock_doc(doc), where=None, cut="wave_turn_cut", soft=True, wheels=False)
        STATE["at_corner"] = bool(turned and corner is not None)
        turnlog.record("event_tour", phase="wave_spot", corner=corner is not None, turned=bool(turned))
        if not turned:
            if STATE["ended"] is not None:
                return False
            notes.append("ไม่ได้ไปมุม/หันหาแขกก่อนโบก" if corner is not None else "ไม่ได้หันหาแขกก่อนโบก")
        if await event_host._wheels_still(still) is False:
            # not seen still after the corner walk / the turn: no arm up (the walk out's rule) -- review 25 Nov 03:3x
            settled = False
            notes.append(event_host.NOT_STILL_TH)
    arm = settled is not False and bool(sc.get("pageant"))
    # the pageant and the line go over the call: back from a re-dial first, CALL_WAIT_S at most
    await _call_back(seq)
    pcm, line_why = await asyncio.to_thread(event_host.recorded_pcm, sc)
    hand = None
    if arm:
        hand = await event_pageant.start(seq, still=still)
        if event_pageant.active(seq) and not event_pageant.ending():
            await event_pageant.hand_up(seq, still)
        elif not hand.get("ok"):
            notes.append("ไม่ได้ยกมือท่านางงาม: %s" % (hand.get("error_th") or hand.get("error")))
    why = _tick(seq)
    if why is not None:
        await _end_and_stop_leg(seq, why)
        return False
    # the music down for her line once the hand is UP (owner 25 Nov ~04:25: "จังหวะโบกมือเหมือนจะปิดเสียงไวไป หุ่นยังไม่ทัน
    # ยกแขนเลย" -- it used to go silent at the pageant's start, its 4.5 s lead before the hand was up)
    if pcm is not None and music("duck"):            # no line to say: the music plays on (review 04:5x)
        await asyncio.sleep(DUCK_LEAD_S)
    said = False
    if pcm is not None:
        rec = sc["recorded"]
        result = await event_cues.play_line("scene_" + sc["id"], rec["text"], pcm, lang=rec.get("lang") or "en",
                                            scene_th=sc["title_th"], still=still)
        said = bool(result.get("ok"))
        if not said:
            notes.append("ประโยคเปิดตัวไม่ได้พูด: %s" % (event_cues.ERRORS_TH.get(result.get("error") or "")
                                                       or result.get("error") or "ผิดพลาด"))
    else:
        notes.append("ประโยคเปิดตัวไม่ได้พูด: %s" % LINE_WHY_TH.get(line_why, str(line_why)))
    turnlog.record("event_tour", phase="wave", pageant=bool(hand and hand.get("ok")), said=said,
                   still=settled is not False)
    if event_pageant.active(seq):
        await event_pageant.end_after_line(seq, standing=True)
    elif arm and said and sc.get("gesture") and hand is not None and not hand.get("ok") and still():
        # the pageant refused (an app without it, its own gate): the greeting wave, card 1's fallback
        fallback = await event_host._gesture(sc["gesture"])
        if not fallback.get("ok"):
            notes.append("ไม่ได้โบกมือทักทาย: %s" % (fallback.get("error_th") or fallback.get("error")))
    why = _tick(seq)
    if why is not None:
        _opening("wave", "cut", " · ".join(notes) or None)
        await _end_and_stop_leg(seq, why)
        return False
    _opening("wave", "done", " · ".join(notes) or None)
    if settled is False:
        # out, the wheels not seen still: the arm stays locked -- the tour's handshakes would each be refused; staff
        # look, clear the latch, and do the rest by hand
        _end(seq, "not_still")
        return False
    return True


async def event_hand(k: int, *, stops_seen: int) -> dict:
    """Card 1's step k by hand from the Event page (its `view()["hand"]`; the ceremony's twin is event_order
    `opening_hand`): out of the box, what the card did not get to -- the wave and the line, a walk to its spot, a
    handshake. Never while a card runs or she walks (a walk by hand is the walk's own: busy). Two taps on the page."""
    from app import event_host, robot_body

    v = view() or {}
    hand = None
    for h in v.get("hand") or [] if v.get("owner") == "event" else []:
        if h["k"] == k:
            hand = h
    if hand is None or hand.get("done"):
        return {"ok": False, "error": "no_hand", "error_th": "ไม่มีขั้นนี้ให้ทำเองแล้ว"}
    if active():
        return {"ok": False, "error": "tour_running", "error_th": RUNNING_TH}
    if event_walk._app_latched():
        return {"ok": False, "error": "stop_latched", "error_th": event_walk.REASONS_TH["stop_latched"]}
    try:
        if hand["kind"] == "walk":
            # the card's walk: the head stays still on it (holds_head) -- from the press, not from go_spot's answer
            # (the walk is under way ~3 s before it: 20:26:16 its first head row, 20:26:19 the answer)
            token = _HandPress()                         # this press's own: a second press's never clears it
            STATE["hand_run"] = token
            try:
                result = await event_host.go_spot(hand["spot"], stops_seen=stops_seen, head_on=bool(hand.get("head_on")))
            finally:
                if STATE.get("hand_run") is token:
                    STATE["hand_run"] = None
            ok_walk = isinstance(result, dict) and result.get("ok")
            run_id = (((result or {}).get("walk") or {}).get("run") or {}).get("id") if ok_walk else None
            if run_id is not None and STATE.get("hand_run") is None:
                hand_walk(run_id)                        # a later press holding its own pending walk keeps it
        elif event_walk.walking():
            result = {"ok": False, "error": "moving", "error_th": MOVING_TH}
        elif hand["kind"] == "wave":
            result = await wave_by_hand(still=lambda: event_walk.stops() == stops_seen and not event_host.ceremony_now())
        else:
            result = await robot_body.staff_request("handshake")
    except Exception:                                    # noqa: BLE001 -- the phone gets an answer
        logger.exception("event tour: card 1's step %s by hand", k)
        result = {"ok": False, "error": "error", "error_th": WHY_TH["error"]}
    ok = bool(isinstance(result, dict) and result.get("ok"))
    if ok:
        hand_pressed(k)
    turnlog.record("event_tour", phase="by_hand", k=k, kind=hand["kind"], ok=ok)
    out = {"ok": ok, "k": k, "result": result}
    if not ok:
        out.update(error=(result or {}).get("error") or "error", error_th=(result or {}).get("error_th") or WHY_TH["error"])
    return out


async def wave_by_hand(*, still) -> dict:
    """The opening's wave and its line by hand (its card's `HAND_WAVE`, app/event_order.py `opening_hand`): she is out
    of the box -- the latch let go -- and the card did not get to them. The standing pageant, the recorded line after
    its lead, the hand down after it, as `_wave_and_line`; the line is never said by her instead (cue-only). {"ok"} =
    the pageant accepted or the line under way; the rest goes on by itself, `still` asked before each part."""
    from app import event_box, event_cues, event_host, event_pageant
    from app import robot_contracts as rc

    sc = event_host.scene(STATE.get("scene")) if STATE.get("scene") else None
    if sc is None:
        return {"ok": False, "error": "no_hand", "error_th": "ไม่มีขั้นเปิดตัวให้ทำต่อ"}
    if event_box.held():
        return {"ok": False, "error": event_box.REASON, "error_th": rc.body_reason_th(event_box.REASON)}
    if event_walk.walking():
        # a walk by hand still under way (6b, review 23 Nov): the recorded line would play while she walks
        return {"ok": False, "error": "moving", "error_th": MOVING_TH}
    pcm, line_why = await asyncio.to_thread(event_host.recorded_pcm, sc)
    _CEREMONY["n"] += 1
    seq = CEREMONY_SEQ_BASE + _CEREMONY["n"]           # the pageant's own run, never the card's ended press
    hand = await event_pageant.start(seq, still=still) if sc.get("pageant") else None
    if pcm is None and not (hand and hand.get("ok")):
        why = (hand or {}).get("error_th") or LINE_NOT_SAID_TH % LINE_WHY_TH.get(line_why, str(line_why))
        return {"ok": False, "error": (hand or {}).get("error") or "no_line", "error_th": why}
    notes: list[str] = []
    if hand is not None and not hand.get("ok"):
        notes.append("ไม่ได้ยกมือท่านางงาม: %s" % (hand.get("error_th") or hand.get("error")))
    if pcm is None:
        notes.append("ประโยคเปิดตัวไม่ได้พูด: %s" % LINE_WHY_TH.get(line_why, str(line_why)))
    _opening("wave", "now", " · ".join(notes) or None)
    _note()

    async def rest() -> None:
        try:
            if event_pageant.active(seq) and not event_pageant.ending():
                await event_pageant.hand_up(seq, still)
            said = False
            if pcm is not None and still():
                rec = sc["recorded"]
                result = await event_cues.play_line("scene_" + sc["id"], rec["text"], pcm, lang=rec.get("lang") or "en",
                                                    scene_th=sc["title_th"], still=still)
                said = bool(result.get("ok"))
                if not said:
                    notes.append("ประโยคเปิดตัวไม่ได้พูด: %s" % (event_cues.ERRORS_TH.get(result.get("error") or "")
                                                               or result.get("error") or "ผิดพลาด"))
            turnlog.record("event_tour", phase="wave_by_hand", pageant=bool(hand and hand.get("ok")), said=said)
        finally:
            if event_pageant.active(seq):
                await event_pageant.end_after_line(seq, standing=True)
            _opening("wave", "done" if still() else "cut", " · ".join(notes) or None)
            _note()

    _spawn(rest())
    return {"ok": True, "pageant": bool(hand and hand.get("ok")), "line": pcm is not None}


# ---- the phone, and a restart ------------------------------------------------------------------------------------

#: The ceremony's opening cut before the walk out: the robot never left the box.
OPENING_AGAIN_TH = "กดขั้นเปิดตัวอีกครั้ง (เริ่มใหม่ตั้งแต่จอเปิดตัว — หุ่นยังอยู่ในกล่อง)"
#: She set out of the box and stopped short (app/event_box.py): the page's box panel has both ways on.
BOX_PANEL_TH = ("กด 'เดินออกให้ครบ' (เดินเฉพาะระยะที่เหลือ) หรือพาหุ่นออกเองแล้วกด 'หุ่นออกจากกล่องแล้ว — ปลดล็อกแขน' "
                "ในกล่องสถานะหุ่นด้านบน — ห้ามกดขั้นเปิดตัวซ้ำ")
#: The opening's own steps by hand on its card (`hand` items; the plan's own are 0 up).
HAND_WAVE = 41
MOVING_TH = "หุ่นกำลังเดินอยู่ — รอให้ถึงก่อน แล้วค่อยกดโบก + พูด"
#: Card 1 of the Event page ended with her still in the box, the arm given back (no walker set out).
CARD_AGAIN_TH = "กดเริ่มเดินเปิดตัวอีกครั้ง (หุ่นยังอยู่ในกล่อง ยังไม่ได้ออกเดิน)"
WAVE_BY_HAND_TH = "ยกมือโบก + พูดประโยคเปิดตัว (หุ่นต้องออกจากกล่องแล้ว)"


def by_hand_th(step: dict) -> str:
    """A step left of the card, as staff do it by hand."""
    if step.get("walk_up"):
        return WALK_UP_TH if step["kind"] == "walk" else "ยื่นมือจับมือ (แขกที่เดินไปเจอ)"
    return "ไป" + step["name_th"] if step["kind"] == "walk" else "ยื่นมือจับมือ (%s)" % step["name_th"]


#: The walk up to an unmarked guest on the card (`walk_up`, card 3's spot), and its hand -- only to someone met.
WALK_UP_TH = "หันหลังให้เคาน์เตอร์ เดินตรงไปหาแขก"
WALK_UP_HAND_TH = "ยื่นมือจับมือแขกที่เดินไปเจอ (ไม่เจอใคร = ไม่ยื่นมือ)"
NOBODY_MET_TH = "เดินไปไม่เจอแขกข้างหน้า — ไม่ได้ยื่นมือ แล้วกลับหน้าเคาน์เตอร์"
WALK_UP_STEP_TH = ("หันหลังให้เคาน์เตอร์ช้าๆ เดินตรงไปหาแขก 1 คน (แบบการ์ด 3) — เจอแขกตรงหน้า = ยื่นมือจับมือ "
                   "ไม่เจอใคร = ไม่ยื่นมือ")
#: The phone's warning when a guest spot is missing and the card walks up to one guest instead (%s: the missing spots,
#: the counter's name). Where the guest stands: inside card 3's walk (its 1.2 m, met ~0.65 m in front of her), and
#: clear of her turn at the counter's head-on point (HEAD_ON_MAX_M; the simulator: a guest 1.9 m out is 0.07 m from it).
WALK_UP_MISSING_TH = ("ยังไม่ได้บันทึกจุด: %s — จับมือที่%s แล้วหันหลังเดินตรงไปหาแขก 1 คนแบบการ์ด 3 (ไม่ไป 3 จุด): "
                      "ให้แขกยืนรอตรงหน้าเคาน์เตอร์ ห่างออกมาราว 2 ม. ค่อนไปทางมือขวาของหุ่น และดูข้างหลังหุ่นให้ว่างครึ่งเมตร"
                      " (หุ่นหมุนตัวมองไม่เห็นข้างหลัง)")


def _step_th(step: dict) -> str:
    if step.get("walk_up"):
        return WALK_UP_TH if step["kind"] == "walk" else WALK_UP_HAND_TH
    if step["kind"] == "handshake":
        return "ยื่นมือจับมือ (%s)" % step["name_th"]
    return ("กลับ" if step.get("back") else "ไป") + step["name_th"]


def _why_th(why: str | None) -> str | None:
    if why is None:
        return None
    return WHY_TH.get(why) or event_walk.REASONS_TH.get(why) or why


def view() -> dict | None:
    """For the staff phones: the steps of the last card-1 press, each done / now / todo / cut, why it ended and
    what is left to do by hand. None = no tour pressed."""
    if STATE["seq"] is None and STATE.get("ended") is None:
        return None
    plan = STATE["plan"] or []
    at = STATE["at"]
    ended = STATE["ended"]
    ceremony = STATE.get("owner") == "ceremony"
    # the ceremony's opening: the whole round, or all the recorded spots allowed (GOOD) -- card 1's view as before
    good = ended in (GOOD if ceremony else ("done", "done_counter"))
    if ceremony:
        # its first three steps, each as it went (`_opening`): the countdown, the walk out, the wave and the line
        steps = [{"text_th": OPENING_TH.get(row["key"], row["key"]), "state": row["state"],
                  **({"note_th": row["note_th"]} if row.get("note_th") else {})} for row in STATE.get("opening") or []]
    else:
        if STATE["phase"] != "debut":
            debut = "done"
        elif ended is None:
            debut = "now"
        elif ended in ("walking_off", "debut_refused"):
            debut = "done"                               # acted where she stands (event_host.scene_text)
        else:
            debut = "cut"
        steps = [{"text_th": "เปิดตัว", "state": debut}]
    for k, step in enumerate(plan):
        if good or (at is not None and k < at):
            state = "done"
        elif at is not None and k == at:
            state = "cut" if ended else "now"
        else:
            state = "todo"
        skipped = k in (STATE.get("skipped") or [])
        steps.append({"text_th": NOBODY_MET_TH if skipped and state == "done" else _step_th(step), "state": state})
    left = [] if good else [s for k, s in enumerate(plan) if at is None or k >= at]
    by_hand = []
    for s in left:
        by_hand.append(by_hand_th(s) if s.get("walk_up") else "ไป" + s["name_th"] if s["kind"] == "walk"
                       else "ยื่นมือจับมือ")
    now = None if ended else (PHASE_TH.get(STATE["phase"]) or STATE["phase"])
    if now and at is not None and at < len(plan):
        now += " — " + _step_th(plan[at])
    hand: list[dict] = []
    from app import event_box

    box = event_box.view()
    if ended and not good and box["in"] and not box["armed"]:
        # she set out of the box and did not get out, or nobody knows -- neither in nor out (reviews 23 Nov): nothing of
        # the card's by hand until she is out, and never the opening (or card 1) again: another whole forward_m
        left, by_hand = [], [box.get("text_th") or WHY_TH["debut_short"], BOX_PANEL_TH]
    elif ended and not good and box["in"] and box["armed"]:
        # no walker set out: still in the box, the arm given back -- the whole press again
        left, by_hand = [], [OPENING_AGAIN_TH if ceremony else CARD_AGAIN_TH]
    elif ended and not good:
        # out of the box, by the walk out or by staff (the latch cleared, no arm): the rest by hand, two taps each --
        # on both pages (the Event page's `/event/tour-hand`, the ceremony's `opening_hand`)
        # One button each for what the card did not get to -- its wave and line first, when it did not get to them
        # (card 1's: still at its debut) -- this app's own on each page: the ceremony's are refused outside it, the
        # Event page's during it.
        rows = {row["key"]: row["state"] for row in STATE.get("opening") or []}
        first = 0 if at is None else at
        done = set(STATE.get("hand_done") or [])
        if rows.get("wave") != "done" and (ceremony or STATE.get("phase") == "debut"):
            hand.append({"k": HAND_WAVE, "kind": "wave", "spot": None, "text_th": WAVE_BY_HAND_TH,
                         "done": HAND_WAVE in done})
        hand += [{"k": k, "kind": s["kind"], "spot": s["spot"], "text_th": by_hand_th(s), "done": k in done,
                  "head_on": bool(s.get("head_on"))}       # the card's walk to a handshake, by hand: the same way in
                 for k, s in enumerate(plan) if k >= first]
        by_hand = [h["text_th"] for h in hand]
    return {"scene": STATE["scene"], "ended": ended, "ok": good, "why_th": _why_th(ended), "detail_th": STATE["detail_th"],
            "now_th": now, "steps": steps, "missing": list(STATE.get("missing") or []), "owner": STATE.get("owner"),
            "by_hand_th": by_hand if ended and not good else [], "hand": hand,
            "ago_s": max(0, int(time.time() - float(STATE.get("ended_at") or STATE.get("since") or time.time())))}


def _path():
    return event_walk.RUN_DIR / "tour.json"


def _note() -> None:
    """What the phone shows survives the server (Android's resets, 4 Nov): read back by `recover` as cut."""
    try:
        event_walk.RUN_DIR.mkdir(parents=True, exist_ok=True)
        _path().write_text(json.dumps({k: STATE.get(k) for k in ("seq", "scene", "plan", "at", "phase", "ended",
                                                                 "detail_th", "since", "ended_at", "missing", "owner",
                                                                 "opening", "hand_done", "skipped")},
                                      ensure_ascii=False), encoding="utf-8")
    except (OSError, TypeError, ValueError):
        logger.exception("event tour: could not note its state")


def recover() -> None:
    """At the server's start: a tour that was under way was cut by the restart -- said, never carried on."""
    try:
        doc = json.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except (OSError, ValueError):
        logger.exception("event tour: unreadable note")
        return
    if not isinstance(doc, dict):
        return
    owner = "ceremony" if doc.get("owner") == "ceremony" else "event"
    opening = [dict(row, state="cut" if row.get("state") == "now" else row.get("state"))
               for row in doc.get("opening") or [] if isinstance(row, dict) and row.get("key")]
    STATE.update(seq=doc.get("seq"), scene=doc.get("scene"), plan=list(doc.get("plan") or []), at=doc.get("at"),
                 phase=doc.get("phase"), ended=doc.get("ended") or "restarted", detail_th=doc.get("detail_th"),
                 since=doc.get("since"), ended_at=doc.get("ended_at") or time.time(),
                 missing=list(doc.get("missing") or []), until=0.0, task=None, leg=None, owner=owner, order=None,
                 reveal=None, opening=opening, box_press=False, box_run=None,
                 hand_done=[int(k) for k in doc.get("hand_done") or [] if isinstance(k, int) and not isinstance(k, bool)],
                 skipped=[int(k) for k in doc.get("skipped") or [] if isinstance(k, int) and not isinstance(k, bool)],
                 met=None)
    if not doc.get("ended"):
        turnlog.record("event_tour", phase="end", why="restarted", at=STATE["at"], steps=len(STATE["plan"]))
        _note()
