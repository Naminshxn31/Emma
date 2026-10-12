"""Walking, stopping, and gesturing — the Astronaut robot as callable tools.

Same rule as `smarthome`: handlers return **facts**, not sentences, so the
model phrases the confirmation in whatever language the guest is speaking.
Every result carries `hardware` ("ok" / "mock" / "failed") so it can tell
somebody the robot didn't actually move, instead of announcing a journey that
never happened.

Nothing here holds a tool call open while the robot walks. See
`robot_link.send`.

**Routes mode on the robot face** (docs/emma-body-nav-design-2026-10-08.md
§2d, P2). With `ROBOT_NAV_ROUTES_ENABLED=true` and the call coming from the
robot face's own session, the four navigation tools go to `app/robot_nav.py`
(one leg of the verified route, through the face page and the app) instead
of the planner. Decided inside each handler, never in the declaration: the
names, descriptions and parameters below are what the sales room's model
sees, and they must stay byte-identical (P0 snapshot test).
"""
from __future__ import annotations

import logging

# Imported at load, not at the first call: it subscribes to the face's
# nav_status frames, and an arrival that lands before anything imported it
# would have nobody to announce it.
from app import robot_nav
from app.tools import robot_link
from app.tools.registry import tool

logger = logging.getLogger("condo_voice.robot")

#: The reply a sent-but-unconfirmed stop gets, and what it must not become.
#: Quoted because the quote is what works: the bare "ห้ามพูดว่า 'หยุดแล้ว'"
#: let 'หยุดให้เรียบร้อยแล้วค่ะ' through (live eval 9 ต.ค., robot_nav.stop).
_STOP_REPLY = ("ให้ตอบว่า 'ส่งคำสั่งหยุดแล้วค่ะ' "
               "ห้ามพูดว่า 'หยุดแล้ว' 'หยุดให้เรียบร้อยแล้วค่ะ' หรือ 'เรียบร้อยค่ะ'")


@tool(
    name="request_handshake",
    description=(
        "ยื่นแขนทำท่าจับมือจริงหนึ่งครั้งบนหุ่น Emma เมื่อคนตรงหน้าขอจับมือชัดเจน "
        "เช่น 'ขอจับมือหน่อย' หรือ 'จับมือกันไหม' ให้เรียกทันทีโดยไม่ถามซ้ำ "
        "อย่าเรียกเพียงเพราะมีคนถามว่าหุ่นมีแขนหรือจับมือได้ไหม"
    ),
    parameters={"type": "object", "properties": {}},
    tags=["robot"],
)
async def request_handshake() -> dict:
    """Send only the owner's verified handshake group via the Android face."""
    from app import robot_arm
    from app.tools.registry import robot_face_session

    session = robot_face_session.get()
    if session is None:
        return {"ok": False, "hardware": "failed",
                "instruction": "ตอนนี้ไม่ได้คุยผ่านหน้าหุ่น จึงสั่งแขนจับมือไม่ได้"}
    requested = await robot_arm.greet(session=session)
    if not requested:
        return {"ok": False, "hardware": "failed",
                "instruction": "ยังสั่งท่าจับมือไม่ได้ในขณะนี้ ให้บอกตามจริง ห้ามบอกว่าแขนยื่นแล้ว"}
    # Finding #7: the app's greet() refuses SILENTLY (the 60 s stop latch,
    # a walk in progress or nav unknown, the arm busy) and this frame has no
    # ack, so "requested" is not "the hand is coming". Narrating motion
    # after somebody pressed the red bar is a robot that says it moves when
    # it does not. The fix with an ack lives in the app and the face page.
    return {"ok": True, "hardware": "requested",
            "instruction": ("ส่งคำขอท่าจับมือไปยังแอปบนหุ่นแล้ว แต่แอปอาจปฏิเสธเงียบๆ "
                            "(เช่นเพิ่งกดหยุด หรือหุ่นกำลังเดิน) ห้ามพูดว่ากำลังยื่นมือหรือยื่นมือแล้ว "
                            "ให้ชวนจับมือเฉยๆ")}


@tool(
    name="go_to_place",
    description=(
        "พาลูกค้าเดินไปยังจุดที่ระบุในห้องขาย ใช้เมื่อลูกค้าขอให้พาไปดู เช่น "
        "'พาไปดูห้องตัวอย่าง' 'ขอไปดูโซนสระว่ายน้ำ' 'พาไปที่โต๊ะเซลส์' — "
        "หุ่นยนต์จะเริ่มเดินทันทีและใช้เวลาสักครู่ ระหว่างเดินยังคุยกับลูกค้าได้ตามปกติ "
        "เมื่อถึงแล้วจะมีข้อความแจ้งอีกที ห้ามบอกว่าถึงแล้วก่อนได้รับแจ้ง"
    ),
    parameters={
        "type": "object",
        "properties": {
            "place": {
                "type": "string",
                "description": "ชื่อจุดหมาย ตามที่ลูกค้าพูด เช่น ห้องตัวอย่าง สระว่ายน้ำ ฟิตเนส",
            }
        },
        "required": ["place"],
    },
    tags=["robot"],
)
async def go_to_place(place: str) -> dict:
    """Start walking. Returns as soon as the order is sent, never on arrival.

    The whole reason this returns early is in `robot_link.send`: a synchronous
    function call held open for a thirty-second walk is thirty seconds of a
    robot standing mute next to a customer it just offered to guide.
    """
    face = robot_nav.routes_session()
    if face is not None:
        return await robot_nav.go(face, place)
    target = robot_link.find_place(place)
    if target is None:
        # Do not guess. A wrong slide is corrected with a sentence; a robot
        # leading somebody to the wrong room is not.
        known = ", ".join(robot_link.places())
        return {
            "ok": False,
            "error": "unknown place",
            "known_places": robot_link.places(),
            "instruction": (
                "ไม่รู้จักจุดนี้ในแผนที่ ห้ามเดาและห้ามพาไป "
                + ("จุดที่ไปได้คือ %s ให้ถามลูกค้าว่าหมายถึงจุดไหน" % known
                   if known else
                   "ยังไม่มีจุดไหนในแผนที่เลย ให้บอกลูกค้าตรงๆ แล้วเสนอเรียกเจ้าหน้าที่")
            ),
        }

    hardware = await robot_link.send("move_to_point", place=target)
    if hardware == "ok":
        robot_link.STATE["moving"] = True
        robot_link.STATE["destination"] = target
        robot_link.STATE["status_source"] = "command_sent"
        from app.config import settings

        robot_link.start_arrival_watch(target, settings.robot_arrival_timeout_s)

    return {
        "ok": hardware != "failed",
        "place": target,
        "moving": robot_link.snapshot()["moving"],
        "hardware": hardware,
        "instruction": (
            "ส่งคำสั่งไปจุดหมายแล้ว ยังไม่ได้ยืนยันว่าหุ่นเริ่มเดิน ชวนคุยระหว่างรอได้ "
            "ห้ามบอกว่าถึงแล้ว รอจนกว่าจะมีข้อความแจ้งว่าถึง"
            if hardware == "ok" else
            "ยังไม่ได้เชื่อมต่อหุ่นยนต์จริง ให้บอกลูกค้าตรงๆ ว่ายังพาไปไม่ได้ "
            "ห้ามบอกว่ากำลังเดินไป"
        ),
    }


@tool(
    name="stop_moving",
    description=(
        "หยุดหุ่นยนต์ทันที ใช้เมื่อลูกค้าบอกว่า หยุด รอก่อน อย่าเพิ่งไป "
        "ไม่ไปแล้ว stop wait — หรือเมื่อรู้สึกว่ามีอะไรขวางทาง"
    ),
    parameters={"type": "object", "properties": {}},
    tags=["robot"],
    # A stop is never answered "busy" (safety review #1): not part of the
    # declaration, so the sales room's tool list is unchanged.
    preempt=lambda args: True,
)
async def stop_moving() -> dict:
    """Cancel navigation.

    Sent even when we believe the robot is standing still. Our idea of
    `moving` comes from what we last sent plus what the app reported, and if
    those have drifted the guest is looking at the disagreement — a robot that
    replies "I'm not moving" while rolling towards someone is the worst
    possible time to trust a cached flag.
    """
    face = robot_nav.routes_session()
    if face is not None:
        # Routes mode: the face page's nav stop and body stop, never the
        # board's cancel — a DELETE mid-leg makes drive_route redo the step.
        return await robot_nav.stop(face)
    # Review 2, finding #4: only the face's OWN session took the branch
    # above, so "หยุด" from a desk tab or the robot's kiosk Chrome during a
    # leg run sent the board DELETE (the #0 bug) and told the model the stop
    # was sent. The desk STOP's rule, from the same helper.
    plan, walking = robot_nav.stop_plan()
    if plan == "face":
        return await robot_nav.stop(walking)
    if plan == "face_gone":
        await robot_link.send("cancel_navigation")     # all that is left: a pause
        return {"ok": False, "hardware": "failed", "did": "stop_unconfirmed",
                "error": "no_robot_face",
                "instruction": robot_nav.STOP_SAY_NO_FACE}
    # Findings #6/#17: with ROBOT_BODY_ENABLED on and routes off, "หยุด"
    # during a gesture can land here, and the planner's cancel never
    # reached the arm. The arm stop follows the robot face session alone —
    # not the body switch: a switch must never be why something cannot
    # stop (robot_body skips its own gate for a stop). Started first, so
    # neither stop waits behind the other.
    import asyncio

    from app.tools.registry import robot_face_session

    body_task = None
    face_task = None
    if plan == "unknown" and walking is not None:
        # Review 10 Oct: a face is connected but cannot vouch for idle (its
        # status link silent, the app's poll stale). It may still carry the
        # run's own stop to the app, so the nav + body stop go through it
        # (and every other face socket) beside the board cancel below —
        # `robot_nav.stop` includes the arm stop the branch after this sends.
        face_task = asyncio.ensure_future(robot_nav.stop(walking))
    elif robot_face_session.get() is not None:
        from app import robot_body

        body_task = asyncio.ensure_future(robot_body.request("stop"))
    was_moving = robot_link.snapshot()["moving"]
    hardware = await robot_link.send("cancel_navigation")
    if hardware == "ok":
        robot_link.STATE["moving"] = False
        robot_link.STATE["destination"] = None
        robot_link.STATE["status_source"] = "stop_requested"
        robot_link._cancel_arrival_watch()
    # Review 2, finding #29: every branch quotes the reply and the words it
    # must not say — the bare "ตอบรับสั้นๆ" let 'เรียบร้อยค่ะ' through, the
    # way robot_nav.stop's text once did (live eval 9 ต.ค.) — and only a
    # real failure sends people for staff.
    if hardware == "ok":
        text = ("ส่งคำสั่งหยุดแล้ว แต่ยังไม่ได้ยืนยันว่าหุ่นหยุดจริง " + _STOP_REPLY)
    elif hardware == "mock":
        text = ("ยังไม่ได้เชื่อมต่อฐานล้อของหุ่น จึงไม่ได้ส่งคำสั่งหยุดไปที่ล้อ "
                "ห้ามพูดว่า 'หยุดแล้ว' หรือ 'เรียบร้อยค่ะ' ให้บอกตรงๆ ว่ายังไม่ได้เชื่อมต่อหุ่น "
                "ถ้าหุ่นขยับอยู่ให้บอกคนตรงนั้นกดปุ่มหยุดฉุกเฉิน (E-stop) บนตัวหุ่น")
    else:
        text = ("ส่งคำสั่งหยุดไม่สำเร็จ ยังยืนยันการหยุดไม่ได้ ห้ามพูดว่า 'หยุดแล้ว' "
                "'ส่งคำสั่งหยุดแล้วค่ะ' หรือ 'เรียบร้อยค่ะ' ให้บอกคนตรงนั้นกดปุ่มหยุดฉุกเฉิน (E-stop) "
                "บนตัวหุ่นทันที แล้วแจ้งเจ้าหน้าที่ตรวจสอบหุ่น")
    out = {
        "ok": hardware != "failed",
        "was_moving": was_moving,
        "hardware": hardware,
        "instruction": text,
    }
    if plan == "unknown":
        # Review 2, finding #0 (voice side): nothing seen, but a leg runner
        # can exist here, and no face can vouch right now (no face page, its
        # status link silent, the app's poll stale). The board DELETE above
        # is a pause to a leg run, so this is not "sent".
        out.update(ok=False, did="stop_unconfirmed", error="run_unknown")
        if hardware == "ok":    # mock/failed already say nothing was confirmed
            out["instruction"] = robot_nav.STOP_SAY_UNKNOWN
        if face_task is not None:
            nav = await face_task
            out["nav_stop"], out["body_stop"] = nav["nav_stop"], nav["body_stop"]
            if nav["nav_stop"]["accepted"]:
                # The face took the run's stop: "ส่งคำสั่งหยุดแล้วค่ะ" is true
                # whatever the board said; still unconfirmed (the face cannot
                # vouch for what is running), so the red-button line stays.
                out["instruction"] = robot_nav.STOP_SAY_UNKNOWN_FACE
    if body_task is not None:
        body = await body_task
        accepted = body.get("ok") is True
        out["body_stop"] = {"accepted": accepted, "reason": body.get("reason"),
                            "hardware": body.get("hardware")}
        if not accepted:
            out["instruction"] = (
                "ส่งคำสั่งหยุดแขนไม่สำเร็จ ห้ามบอกว่าหยุดแล้ว ห้ามพูดว่า 'หยุดแล้ว' หรือ 'เรียบร้อยค่ะ' "
                "ให้บอกคนตรงนั้นให้กดปุ่มหยุดฉุกเฉิน (E-stop) บนตัวหุ่นทันที แล้วแจ้งเจ้าหน้าที่")
        elif hardware == "mock" and plan != "unknown":
            # The gesture-only setup (body on, no wheels to stop): the arm
            # stop IS the stop. "ตรวจสอบหุ่น" over an arm stop that worked
            # sent the guest for staff (finding #29).
            out["instruction"] = (
                "ส่งคำสั่งหยุดแขนแล้ว แต่ยังยืนยันไม่ได้ว่าแขนหยุดจริง ให้ตอบว่า 'ส่งคำสั่งหยุดแล้วค่ะ' "
                "ห้ามพูดว่า 'หยุดแล้ว' 'แขนหยุดแล้ว' 'หยุดให้เรียบร้อยแล้วค่ะ' หรือ 'เรียบร้อยค่ะ' "
                "ถ้ามีอันตรายให้บอกคนให้กดปุ่มหยุดฉุกเฉิน (E-stop) บนตัวหุ่น")
        else:
            out["instruction"] += (" ส่งคำสั่งหยุดแขนแล้วด้วย แต่ยังยืนยันไม่ได้ว่าแขนหยุดจริง "
                                   "ห้ามพูดว่า 'หยุดแล้ว' 'หยุดให้เรียบร้อยแล้วค่ะ' หรือ 'เรียบร้อยค่ะ'")
    return out


@tool(
    name="return_to_base",
    description=(
        "ให้หุ่นยนต์กลับไปยังจุดจอดหรือแท่นชาร์จ ใช้เมื่อลูกค้าบอกว่าดูเสร็จแล้ว "
        "หรือเจ้าหน้าที่สั่งให้กลับที่"
    ),
    parameters={"type": "object", "properties": {}},
    tags=["robot"],
)
async def return_to_base() -> dict:
    face = robot_nav.routes_session()
    if face is not None:
        return await robot_nav.go(face, None, dock=True)
    hardware = await robot_link.send("go_home")
    if hardware == "ok":
        robot_link.STATE["moving"] = True
        robot_link.STATE["destination"] = "base"
        robot_link.STATE["status_source"] = "command_sent"
        from app.config import settings

        robot_link.start_arrival_watch("base", settings.robot_arrival_timeout_s)
    return {
        "ok": hardware != "failed",
        "hardware": hardware,
        "instruction": (
            "ส่งคำสั่งกลับจุดจอดแล้ว รอหุ่นรายงานว่าถึงก่อนยืนยันการชาร์จ"
            if hardware == "ok" else
            "ยังสั่งกลับจุดจอดจริงไม่ได้ ให้บอกตรงๆ ห้ามบอกว่ากำลังกลับหรือชาร์จ"
        ),
    }


@tool(
    name="get_robot_status",
    description=(
        "ดูสถานะหุ่นยนต์ตอนนี้ — แบตเตอรี่ กำลังเดินอยู่ไหม ไปไหนอยู่ "
        "และจุดไหนบ้างที่พาไปได้ ใช้เมื่อลูกค้าถามว่าพาไปไหนได้บ้าง"
    ),
    parameters={"type": "object", "properties": {}},
    tags=["robot"],
)
def get_robot_status() -> dict:
    face = robot_nav.routes_session()
    if face is not None:
        # The leg system's view replaces the planner's: its `places` would
        # name chassis POIs that routes mode refuses to drive to.
        state = robot_nav.status(face)
        state["places"] = [row["name_th"] for row in state["nav"]["places"]]
        state["hardware"] = "ok" if state["nav"]["activity"] != "unknown" else "mock"
        state["instruction"] = robot_nav.status_instruction(state["nav"])
        return state
    state = robot_link.snapshot()
    state["places"] = list(robot_link.places())
    state["hardware"] = "ok" if robot_link.available() else "mock"
    if not robot_link.available():
        state["instruction"] = (
            "ยังไม่ได้เชื่อมต่อหุ่นยนต์จริง ถ้าลูกค้าถามเรื่องการพาไป "
            "ให้บอกตรงๆ ว่ายังพาไปไม่ได้"
        )
    elif state.get("status_source") == "chassis":
        # The one path where these numbers are readings rather than echoes of
        # the last command. Telling the model "there is no telemetry" here
        # would have it hedge about a battery level it was just handed, and
        # the guest hears a robot that does not know its own state. The
        # hedge that stays is about the *map*: an empty `places` is why
        # `go_to_place` refuses, and that is worth saying out loud.
        state["instruction"] = (
            "แบตเตอรี่และการชาร์จมาจากการอ่านค่าหุ่นจริง ส่วน moving อ้างอิงสถานะงานนำทาง "
            "ไม่ใช่การวัดความเร็วล้อ และ null คือยังไม่ทราบ ห้ามรับรองการหยุดทางกายภาพจากค่านี้ "
            + ("แต่แผนที่ยังไม่มีจุดหมายสักจุด ถ้าลูกค้าขอให้พาไป ให้บอกตรงๆ ว่ายังพาไปไม่ได้"
               if not state["places"] else
               "จุดที่พาไปได้คือรายการใน places เท่านั้น ห้ามเสนอจุดนอกรายการ")
        )
        if state.get("motion_enabled") is False:
            state["instruction"] += " ขณะนี้ล็อกการเริ่มเดินไว้เพื่อรอตรวจระบบหยุด ห้ามบอกว่าพาเดินได้"
    else:
        state["instruction"] = (
            "สถานะอ้างอิงคำสั่งล่าสุดหรือรายงานถึงจุดหมายเท่านั้น ยังไม่มี telemetry "
            "moving=null คือไม่ทราบ ห้ามยืนยันว่าเดิน หยุด หรือชาร์จจากการส่งคำสั่งสำเร็จ"
        )
    return state
