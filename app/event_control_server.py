"""The event staff's phone pages (owner 30 Oct: "ให้ทีมงานกดเปลี่ยนซีน"; the ceremony's own
page since 9 Nov: "อันนี้ทำแยกกับอันเดิมนะ ไม่งั้นจะปนกัน").

The robot's call server stays on 127.0.0.1 (scripts/robot_server.py) and the
room screen's listener is view-only by its own definition (app/mirror_server.py)
-- these pages start walks, so they are neither: a THIRD listener, plain http on
EVENT_CONTROL_HOST:EVENT_CONTROL_PORT, behind its own EVENT_CONTROL_KEY (the
staff phone never holds WS_TOKEN or MIRROR_TOKEN). Without the key it does not
start at all. Two pages on it, one key, one cookie: the Emma Event app's and the
Emma Ceremony app's.

  /event-control         the Event page (client/event-control.html)
  GET  /event/state      what it shows -- and the walk's dead man's switch:
                         every poll is a heartbeat (app/event_walk.py)
  POST /event/scene      {scene, stop?}   switch scene (walks if it has a spot)
  POST /event/next                       the scene's next stop (the next table)
  POST /event/stop-index {stop}          one stop of the scene (a table's chip)
  POST /event/go         {spot}           walk there, nothing said
  POST /event/say        {scene}          the scene's line where she stands, nothing else
                                         (the "สั่งเอง" card, owner 14 Nov)
  POST /event/body       {action}         one gesture (event_host.MANUAL_GESTURES, the
                                         pageant and its end) -- the same card
  POST /event/stop                       stop: the walk, the arm, the line being said
  POST /event/box-clear                  she is out of the box: the in-the-box arm latch lets go (both pages)
  POST /event/box-arm                    she is in the box: arms card 1's one walk out, the arm latch on (Event page)
  POST /event/tour-hand       {k}        a step card 1 did not get to, by hand (Event page)
  POST /event/box-finish                 the rest of a walk out of the box that stopped short (the Event page)
  POST /ceremony/box-finish              the same, the ceremony page (ceremony app on: the ceremony's own walk)
                                         (BOTH pages' red button)
  POST /event/approach {on}              walking up to scanned guests, on or off
  POST /event/approach/corner {clear?}   the robot's pose now = a corner of its area
  POST /event/resume                     the stopped walk, from the step it reached
  POST /event/dock-pose                  the robot is on the dock: set the pose there
  POST /event/dock-relearn               the dock was put down again: find it, out 0.8 m and back,
                                         write its pose (event_host.relearn_dock; a walk)
  GET  /event/ready      {fresh?}         the morning's GO / NO-GO, read on the robot (app/event_readiness.py)
  POST /event/spot       {spot}           the robot's pose now = this spot
  POST /event/clip       {clip | action}  a film on the big screen, or stop it

  /ceremony-control           the Ceremony page (client/ceremony-control.html)
  GET  /ceremony/state        the shared pieces + the cues + the running order (+ heartbeat)
  POST /ceremony/cue          {id}       say a ceremony line (app/event_cues.py)
  POST /ceremony/order/step   {n, i}     one step of the ceremony's running order ({without_screen} the opening)
  POST /ceremony/order/opening-hand {n, i, k}  a step the opening's card did not get to, by hand
  POST /ceremony/order/next   {n?, i?}   the step after the last one taken ({n, i} = the
                                         step the button showed: moved since = stale)
  POST /ceremony/order/skip   {n, i}     mark a step skipped, the order moves past it
  POST /ceremony/order/reset             back to the first step (app/event_order.py)
  POST /ceremony/device       {device, action}  one venue light / curtain command (app/venue_devices.py,
                                         the page's "ไฟ/ม่าน" card; rehearsal and recovery -- the
                                         order's own device steps go through /ceremony/order/*)
  POST /ceremony/adhoc        {open}     "ตอบสด": her live window for the host off-script, open (true) or
                                         "กลับเข้าบท" (false) -- the action the button showed, never a
                                         toggle, never words (app/ceremony.py `adhoc_open`). Every press
                                         of the script above but a skip closes it first (`_back_to_script`)

**Not mixed (9 Nov).** While the Emma Ceremony app is on the robot (app/ceremony.py), the
Event page's presses that drive her -- a scene, the next stop or a table, a walk, the
resume, a film, walking up on, the card's line and gestures -- answer `ceremony_app`; the
red stop (and the card's "เอามือลง") always works from
either page, and saving a spot, the dock pose and turning walking up OFF are not driving
her. The other way round, every ceremony press but a tick answers `not_ceremony_app` while
the ceremony app is not on (app/event_order.py, and /ceremony/cue here).

`/event-control?key=...` (or `/ceremony-control?key=...`) once sets an HttpOnly,
SameSite=Strict cookie (a derived value, never the key itself); every other request needs
the cookie.
SameSite=Strict is what keeps another site's page from POSTing here with it.
Plain http on the office Wi-Fi is the mirror's trade-off too: anyone on that
network who sniffs the cookie could drive the scenes -- and the E-stop, the
voice "หยุด" and the page's STOP all still work against them.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from app import event_host, event_walk
from app.config import settings

logger = logging.getLogger("condo_voice.event_control")

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "client" / "event-control.html"
#: The Emma Ceremony app's staff page (written beside the event one, 9 Nov).
CEREMONY_PAGE = ROOT / "client" / "ceremony-control.html"
#: The two page paths the key may be brought to (`?key=` once, then the cookie).
PAGES = ("/event-control", "/ceremony-control")
COOKIE = "emma_event"
MAX_BODY = 2048
_NO_CACHE = {"Cache-Control": "no-store", "X-Frame-Options": "DENY",
             "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}


def _key_ok(value: object) -> bool:
    key = settings.event_control_key
    return (bool(key) and isinstance(value, str)
            and hmac.compare_digest(value.encode("utf-8"), key.encode("utf-8")))


def _cookie_value() -> str:
    return hmac.new(settings.event_control_key.encode("utf-8"), b"emma-event-v1", hashlib.sha256).hexdigest()


def _cookie_ok(request: Request) -> bool:
    value = request.cookies.get(COOKIE) if settings.event_control_key else None
    return bool(value) and hmac.compare_digest(value.encode("utf-8"), _cookie_value().encode("utf-8"))


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def _gate(request: Request, call_next):
    if request.url.path in PAGES and _key_ok(request.query_params.get("key")):
        # the key leaves the address bar at once (history, a screenshot). NOT a 303 (24 Nov, the owner's phone from
        # the QR code: "ขึ้นให้ใส่คีย์"): a link opened from another app/site is a cross-site navigation, and the
        # browser does not send the new SameSite=Strict cookie on a redirect inside that navigation -> 403. A tiny
        # page of our own origin moves on by itself instead: that navigation is same-site, so the cookie goes.
        path = request.url.path
        body = ('<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=%s">'
                '<script>location.replace(%r)</script><a href="%s">เปิดหน้าคุม</a>' % (path, path, path))
        response = Response(body, status_code=200, media_type="text/html; charset=utf-8", headers=_NO_CACHE)
        response.set_cookie(COOKIE, _cookie_value(), httponly=True, samesite="strict",
                            max_age=14 * 24 * 3600)
        return response
    if not _cookie_ok(request):
        return Response("ต้องเปิดด้วย /event-control?key=... (EVENT_CONTROL_KEY)", status_code=403,
                        media_type="text/plain; charset=utf-8", headers=_NO_CACHE)
    return await call_next(request)


async def _body(request: Request) -> dict:
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return {}
    with contextlib.suppress(ValueError):
        doc = json.loads(raw or b"{}")
        return doc if isinstance(doc, dict) else {}
    return {}


def _json(doc: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(doc, status_code=status, headers=_NO_CACHE)


@app.get("/event-control")
async def page() -> FileResponse:
    return FileResponse(PAGE, headers=_NO_CACHE)


@app.get("/ceremony-control")
async def ceremony_page() -> Response:
    """The Ceremony page. Not on this machine (yet) = 404, never a crash of the listener."""
    if not CEREMONY_PAGE.is_file():
        return Response("ไม่พบหน้าคุมพิธี (client/ceremony-control.html)", status_code=404,
                        media_type="text/plain; charset=utf-8", headers=_NO_CACHE)
    return FileResponse(CEREMONY_PAGE, headers=_NO_CACHE)


@app.get("/event/state")
async def state() -> JSONResponse:
    event_walk.heartbeat()
    doc = event_host.snapshot()
    doc["server_time"] = time.time()
    if settings.face_approach:
        # Walking up to scanned guests (app/face_approach.py): its switch is
        # staff's, here. Off = the state this page always had.
        from app import face_approach

        doc["approach"] = face_approach.snapshot()
    # No cues, no running order since 9 Nov: they are the Ceremony page's (/ceremony/state).
    return _json(doc)


@app.get("/ceremony/state")
async def ceremony_state() -> JSONResponse:
    """What the Ceremony page shows: the heartbeat first (the walk's dead man's switch is the
    same for both pages), the pieces both pages share (the walk, the films, the big screen, the
    spots, the modes -- event_host.shared_snapshot), then the cues and the running order."""
    event_walk.heartbeat()
    doc = event_host.shared_snapshot()
    # the server's clock, beside the order: its live window's end (`order.live.until`) is
    # counted down on the phone without the order changing every second (it is rebuilt
    # only when it changes -- a row replaced under a finger misses its tap)
    doc["server_time"] = time.time()
    # The ceremony's cues (app/event_cues.py): approved script text and what the
    # last press did -- never a guest's words. After the heartbeat, and never in
    # its way: a script that cannot be read is an empty list, not a dead poll.
    try:
        from app import event_cues

        doc["cues"] = await event_cues.snapshot()
    except Exception:                                    # noqa: BLE001 -- the walk's poll comes first
        logger.exception("ceremony staff page: the cue list")
        doc["cues"] = {"cues": [], "call": False, "host_th": None}
    # The ceremony's running order (app/event_order.py): the script's scenes as steps
    # staff press one by one. None = the script has no order. Never in the poll's way.
    try:
        from app import event_order

        doc["order"] = await event_order.snapshot()
    except Exception:                                    # noqa: BLE001
        logger.exception("ceremony staff page: the running order")
        doc["order"] = None
    # "ตอบสด" (app/ceremony.py): open or not, until when, whether it may open now and why not, how the
    # last one ended. Never in the poll's way: unreadable = no key (the page then shows no button).
    try:
        from app import ceremony

        doc["adhoc"] = ceremony.adhoc_snapshot()
    except Exception:                                    # noqa: BLE001
        logger.exception("ceremony staff page: ตอบสด")
    # "ทั้งพิธีคุยสด" (owner 24 Nov, app/ceremony_follow.py): only with CEREMONY_FOLLOW_HOST on -- off (the default)
    # = no `follow` key, the page as before. Never in the poll's way.
    if settings.ceremony_follow_host:
        try:
            from app import ceremony_follow

            doc["follow"] = ceremony_follow.snapshot()
        except Exception:                                # noqa: BLE001
            logger.exception("ceremony staff page: follow mode")
    # The venue's lights and curtains the robot drives (app/venue_devices.py): only when this
    # machine drives one -- off (the default) = no `venue` key, the page as before.
    try:
        from app import venue_devices

        venue = venue_devices.snapshot()
        if venue is not None:
            doc["venue"] = venue
    except Exception:                                    # noqa: BLE001 -- never the poll's end
        logger.exception("ceremony staff page: the venue devices")
    return _json(doc)


def _back_to_script(why: str = "script"):
    """`async with _back_to_script() as closed:` around a press of the ceremony's script: "ตอบสด" closes FIRST
    (owner 23 Nov -- pressing the next line is going back to the script), before anything of the press
    is read or awaited, and stays shut until the press has answered (app/ceremony.py `script_press`: a
    tap from another phone in the press's awaits is refused "busy"). Left open, a line pressed under it
    is answered twice: her live answer to the host's "Emma ..." and then the line (its press waits for
    her answer to end, event_cues `_until_quiet`); a walk or a gesture under it has her answering while
    she moves. Closed whatever the press then does -- refused or not, the host is back on the script.
    Every POST /ceremony/* but "ตอบสด" itself, a skip and the venue card goes through it
    (tests/test_ceremony_adhoc.py walks the routes). `closed` True = a window was open (the answer says
    so)."""
    from app import ceremony

    return ceremony.script_press(why)


def _closed_first(doc: dict, closed: bool) -> dict:
    """The press's own answer, plus `adhoc_closed` when it closed "ตอบสด" (the phone says so)."""
    return {**doc, "adhoc_closed": True} if closed and isinstance(doc, dict) else doc


@app.post("/ceremony/adhoc")
async def adhoc(request: Request) -> JSONResponse:
    """"ตอบสด" (owner 23 Nov: the host may go off-script): {open: true} opens her live window with the
    server's own brief, {open: false} is "กลับเข้าบท". The phone sends the action its button showed --
    never a toggle (a double tap, a second phone, an answer that never came: nothing flips back) and
    never words. One tap on the page: it moves nothing and says nothing by itself, and closing must
    always be possible (the page's `whyTwoTaps` keeps two taps for a walk, the hand-over, a light).
    The answer carries `adhoc` (the page's state, as GET /ceremony/state has it)."""
    from app import ceremony

    body = await _body(request)
    want = body.get("open")
    if want is not True and want is not False:
        return _json({"ok": False, "error": "bad_request"}, 400)
    if want:
        doc = ceremony.adhoc_open()
    else:
        doc = {"ok": True, "open": False, "closed": ceremony.adhoc_close("staff")}
    return _json({**doc, "adhoc": ceremony.adhoc_snapshot()})


@app.post("/ceremony/follow")
async def follow(request: Request) -> JSONResponse:
    """"ทั้งพิธีคุยสด" (owner 24 Nov, app/ceremony_follow.py): {action: "on"} = card 2's button again (the same start),
    "resume" = after the red stop, "off" = "หยุดโหมดสด / กลับไปกดเอง". The phone sends the action its button showed,
    never words. Ending it must always be possible: "off" is never gated. The answer carries `follow`."""
    if not settings.ceremony_follow_host:
        return _json({"ok": False, "error": "follow_off"}, 404)
    from app import ceremony_follow

    body = await _body(request)
    action = body.get("action")
    if action == "on":
        doc = await ceremony_follow.start(by="page")
    elif action == "resume":
        doc = {"ok": ceremony_follow.resume("page")}
    elif action == "off":
        doc = {"ok": True, "stopped": ceremony_follow.stop("staff")}
    else:
        return _json({"ok": False, "error": "bad_request"}, 400)
    return _json({**doc, "follow": ceremony_follow.snapshot()})


@app.post("/ceremony/cue")
async def cue(request: Request) -> JSONResponse:
    """A line of the ceremony script, by its id in the script file -- never words:
    the `show_unit(room)` rule. Approved lines only (event_cues refuses the rest), and only
    with the Emma Ceremony app on the robot (the order's rule: a ceremony line said into the
    Event app's day is the mix the owner asked to keep apart)."""
    from app import ceremony, event_cues

    async with _back_to_script() as closed:
        body = await _body(request)
        if not ceremony.on():
            return _json(_closed_first(ceremony.refusal(), closed))
        return _json(_closed_first(await event_cues.play(str(body.get("id") or "")[:64], by="staff"), closed))


def _step_of(body: dict) -> tuple[int, int] | None:
    """(n, i) of a step, ints only: a step is named by its place, never by words."""
    n, i = body.get("n"), body.get("i")
    if (isinstance(n, int) and not isinstance(n, bool) and isinstance(i, int) and not isinstance(i, bool)
            and 0 < n < 1000 and 0 <= i < 1000):
        return n, i
    return None


@app.post("/ceremony/order/step")
async def order_step(request: Request) -> JSONResponse:
    """{n, i}; the opening step also {without_screen: true} -- started on purpose without the room screen's
    countdown (the page's own two-tap button, offered only while the screen cannot count down)."""
    from app import event_order

    async with _back_to_script() as closed:
        body = await _body(request)
        at = _step_of(body)
        if at is None or body.get("without_screen") not in (None, True, False):
            return _json(_closed_first({"ok": False, "error": "bad_request"}, closed), 400)
        return _json(_closed_first(await event_order.press(*at, without_screen=body.get("without_screen") is True),
                                   closed))


@app.post("/ceremony/order/opening-hand")
async def order_opening_hand(request: Request) -> JSONResponse:
    """{n, i, k}: step k of the ceremony's opening that its card did not get to, by hand (app/event_order.py
    `opening_hand`) -- a walk to its spot or a handshake, named by its place in the card, never words."""
    from app import event_order

    async with _back_to_script() as closed:
        body = await _body(request)
        at, k = _step_of(body), body.get("k")
        if at is None or not isinstance(k, int) or isinstance(k, bool) or not 0 <= k < 50:
            return _json(_closed_first({"ok": False, "error": "bad_request"}, closed), 400)
        return _json(_closed_first(await event_order.opening_hand(*at, k), closed))


@app.post("/ceremony/order/next")
async def order_next(request: Request) -> JSONResponse:
    """{} = the next step, whatever it is now; {n, i} = the step the phone's button
    showed -- if the order moved since (another phone, a skip, a restart), nothing runs
    and the answer is `stale` with the real next."""
    from app import event_order

    async with _back_to_script() as closed:
        body = await _body(request)
        expect = None
        if "n" in body or "i" in body:
            expect = _step_of(body)
            if expect is None:
                return _json(_closed_first({"ok": False, "error": "bad_request"}, closed), 400)
        return _json(_closed_first(await event_order.next(expect), closed))


@app.post("/ceremony/order/skip")
async def order_skip(request: Request) -> JSONResponse:
    """A step marked skipped: nothing of it runs, so "ตอบสด" stays open (staff may skip the lines the host
    ad-libbed past while he is still talking)."""
    from app import event_order

    at = _step_of(await _body(request))
    if at is None:
        return _json({"ok": False, "error": "bad_request"}, 400)
    return _json(await event_order.skip(*at))


@app.post("/ceremony/order/reset")
async def order_reset() -> JSONResponse:
    from app import event_order

    async with _back_to_script("reset") as closed:            # as the order's own live step closes with it
        return _json(_closed_first(event_order.reset_order(), closed))


@app.post("/ceremony/device")
async def venue_device(request: Request) -> JSONResponse:
    """The "ไฟ/ม่าน" card: one command to a venue device, by its id and an action id -- never words.
    Not the ceremony app's alone: rehearsing the lights, or bringing them back after the ceremony, is
    staff's at any time. The answer is app/venue_devices.run's own: `hardware` says how far it got
    ("sent" is not "the lights are off"). Not a press of the script: "ตอบสด" stays as it is."""
    from app import venue_devices

    body = await _body(request)
    device, action = body.get("device"), body.get("action")
    if not isinstance(device, str) or not isinstance(action, str):
        return _json({"ok": False, "error": "bad_request"}, 400)
    return _json(await venue_devices.run(device[:32], action[:16], by="staff_card"))


def _ceremony_on() -> bool:
    """The Emma Ceremony app is on the robot: the Event page's presses that drive her are
    refused (owner 9 Nov, "ไม่งั้นจะปนกัน")."""
    return bool(event_host.ceremony_now())


@app.post("/event/approach")
async def approach(request: Request) -> JSONResponse:
    from app import face_approach

    body = await _body(request)
    on = body.get("on") is True
    if on and _ceremony_on():
        return _json(event_host.ceremony_refusal())      # off is never refused
    from app import event_tour

    if on and event_tour.active():
        # card 1's tour walks and shakes hands by itself (owner 23 Nov): a walk up to somebody between its legs
        # would be a second driver, and turns the head the tour holds still
        return _json({"ok": False, "error": "tour",
                      "error_th": "การ์ด 1 กำลังทำต่อเนื่องอยู่ — เปิดเดินเข้าทักได้หลังการ์ดจบ หรือกดหยุดสีแดงก่อน"})
    return _json(face_approach.set_enabled(on))


@app.post("/event/approach/corner")
async def approach_corner(request: Request) -> JSONResponse:
    from app import face_approach

    body = await _body(request)
    return _json(await face_approach.add_corner(clear=body.get("clear") is True))


@app.post("/event/clip")
async def clip(request: Request) -> JSONResponse:
    """The event's film on the big screen, by its id in the event file, or off."""
    from app import event_clips

    body = await _body(request)
    if body.get("action") == "stop":
        return _json({"ok": True, "stopped": await event_clips.stop("staff")})
    if _ceremony_on():
        return _json(event_host.ceremony_refusal())      # the ceremony's films are its order's steps
    return _json(await event_host.play_clip(str(body.get("clip") or ""), by="staff"))


# Every press that walks awaits things before its walk's `start` (the roam's stop, the walk it
# replaces, a pose read): a red stop in that window found no walk to stop, and the walk set out
# after it (review 7 Nov). `event_walk.unless_stopped` stops the walk such a press started.

@app.post("/event/scene")
async def scene(request: Request) -> JSONResponse:
    if _ceremony_on():
        return _json(event_host.ceremony_refusal())
    body = await _body(request)
    stop = body.get("stop")
    seen = event_walk.stops()
    result = await event_host.start_scene(str(body.get("scene") or ""),
                                          stop_index=stop if isinstance(stop, int) else 0,
                                          stops_seen=seen)
    return _json(await event_walk.unless_stopped(seen, result))


@app.post("/event/next")
async def next_stop() -> JSONResponse:
    if _ceremony_on():
        return _json(event_host.ceremony_refusal())
    seen = event_walk.stops()
    return _json(await event_walk.unless_stopped(seen, await event_host.next_stop(stops_seen=seen)))


@app.post("/event/stop-index")
async def stop_index(request: Request) -> JSONResponse:
    body = await _body(request)
    index = body.get("stop")
    if not isinstance(index, int):
        return _json({"ok": False, "error": "bad_request"}, 400)
    if _ceremony_on():
        return _json(event_host.ceremony_refusal())
    seen = event_walk.stops()
    return _json(await event_walk.unless_stopped(seen, await event_host.go_stop(index, stops_seen=seen)))


@app.post("/event/go")
async def go(request: Request) -> JSONResponse:
    if _ceremony_on():
        # event_host.go_spot itself is the ceremony order's walk too: the refusal is the page's
        return _json(event_host.ceremony_refusal())
    body = await _body(request)
    seen = event_walk.stops()
    return _json(await event_walk.unless_stopped(seen, await event_host.go_spot(str(body.get("spot") or ""), stops_seen=seen)))


@app.post("/event/say")
async def say(request: Request) -> JSONResponse:
    """The "สั่งเอง" card (owner 14 Nov "ทำแบบ manual"): a scene's line where she stands -- by the
    scene's id, never words. No walk, no gesture, the scene staff chose stays."""
    if _ceremony_on():
        return _json(event_host.ceremony_refusal())
    body = await _body(request)
    return _json(await event_host.manual_say(str(body.get("scene") or "")[:64]))


@app.post("/event/body")
async def body_gesture(request: Request) -> JSONResponse:
    """The card's gestures, by id (event_host.MANUAL_GESTURES, "pageant", "pageant_end"). Bringing
    the hand down is never refused; the rest wait for Emma Event (the ceremony's arm is its order's)."""
    body = await _body(request)
    action = str(body.get("action") or "")[:32]
    if action != "pageant_end" and _ceremony_on():
        return _json(event_host.ceremony_refusal())
    return _json(await event_host.manual_gesture(action))


@app.post("/event/home")
async def home(request: Request) -> JSONResponse:
    """The walk card's "กลับแท่น": back along a walk ahead, else the map's dock walk
    (event_host.go_home) -- the way the button showed when it was confirmed (`way`), or nothing."""
    if _ceremony_on():
        return _json(event_host.ceremony_refusal())
    body = await _body(request)
    way = body.get("way")
    if way not in ("back", "map"):
        return _json({"ok": False, "error": "bad_request", "error_th": "รีเฟรชหน้าคุมแล้วกดใหม่"}, 400)
    seen = event_walk.stops()
    return _json(await event_walk.unless_stopped(seen, await event_host.go_home(stops_seen=seen, way=way)))


#: The walk stop failed: what the phone says (the arm and the line were still stopped).
STOP_FAILED_TH = "หยุดการเดินไม่สำเร็จ — ถ้าหุ่นยังเคลื่อนที่ กดปุ่มหยุดฉุกเฉิน (E-stop) บนตัวหุ่นทันที"


@app.post("/event/stop")
async def stop() -> JSONResponse:
    """The red button: everything that can move or speak, at once. The walk (and the
    roam and walking up to guests: event_host.stop), the ARM (review 7 Nov: a wave or
    one of the order's handshakes went on after this button -- robot_body.staff_stop,
    a body stop on every face socket; the app then latches arm and walking 60 s) and a
    ceremony line being said (event_cues.cut). Concurrently, and each whatever the
    others do: the arm stop goes out even when the walk stop raises.

    Her live window (the order's live step, or the ceremony page's "ตอบสด") closes too, and the
    order learns of the stop first and synchronously (`event_order.staff_stopped`): a press still
    reading its readiness runs nothing after this button (review 7 Nov).

    {"ok", "stopped", "body": {"sent", "error_th", "note_th"}, "line_cut": bool,
     "live_closed": bool}."""
    from app import ceremony, event_cues, event_order, robot_body

    try:
        live_closed = event_order.staff_stopped()
    except Exception:                                    # noqa: BLE001 -- the stops below still go
        logger.exception("event staff page: the order's stop")
        live_closed = False
    if settings.ceremony_follow_host:
        try:
            from app import ceremony_follow

            ceremony_follow.pause("stop")                # owner 24 Nov: the red stop pauses the live mode until staff resume it
        except Exception:                                # noqa: BLE001 -- the stops below still go
            logger.exception("event staff page: pausing follow mode")
    try:
        # "ตอบสด" is no step of the order (staff_stopped does not see it): closed here, as synchronously,
        # with the reason its page says ("กดหยุดแดง") -- event_host.stop and the cut would close it too,
        # later and as "stop"/"cut"
        live_closed = ceremony.adhoc_close("staff_stopped") or live_closed
    except Exception:                                    # noqa: BLE001 -- the stops below still go
        logger.exception("event staff page: closing ตอบสด")

    async def walk() -> dict:
        return await event_host.stop()

    async def line() -> bool:
        cut = getattr(event_cues, "cut", None)
        return bool(await cut("staff")) if callable(cut) else False

    body_task = asyncio.ensure_future(robot_body.staff_stop())       # first: it is the arm
    walk_r, body_r, line_r = await asyncio.gather(walk(), body_task, line(), return_exceptions=True)
    if isinstance(walk_r, BaseException):
        logger.error("event staff page: the walk stop raised", exc_info=walk_r)
        doc = {"ok": False, "stopped": False, "error": "error", "error_th": STOP_FAILED_TH}
    else:
        doc = dict(walk_r)
    if isinstance(body_r, BaseException):
        logger.error("event staff page: the arm stop raised", exc_info=body_r)
        body_r = {"sent": False, "error_th": robot_body.STAFF_STOP_FAILED_TH % "ผิดพลาด", "note_th": None}
    if isinstance(line_r, BaseException):
        logger.error("event staff page: cutting the line raised", exc_info=line_r)
        line_r = False
    doc.update(body=body_r, line_cut=bool(line_r), live_closed=bool(live_closed))
    return _json(doc)


@app.post("/event/box-clear")
async def box_clear() -> JSONResponse:
    """"หุ่นออกจากกล่องแล้ว — ปลดล็อกแขน" (two taps on either page; app/event_box.py): the in-the-box latch lets go. Both
    pages, the ceremony too -- it is what makes the arm hers again after somebody moved her out by hand."""
    return _json(event_host.box_clear())


@app.post("/event/box-arm")
async def box_arm() -> JSONResponse:
    """"หุ่นอยู่ในกล่องแล้ว" (two taps, the Event page): staff say she is in the box -- the one walk out of card 1 is armed
    and the arm latch holds from now (app/event_box.py). Not driving her: allowed from either page."""
    return _json(event_host.box_arm("event_page"))


@app.post("/event/tour-hand")
async def tour_hand(request: Request) -> JSONResponse:
    """{k}: step k of card 1 that its press did not get to, by hand from the Event page (app/event_tour.py
    `event_hand`: the wave and the line, a walk to its spot, a handshake) -- named by its place, never words."""
    from app import event_tour

    if event_host.ceremony_now():
        return _json(event_host.ceremony_refusal())
    body = await _body(request)
    k = body.get("k")
    if not isinstance(k, int) or isinstance(k, bool) or not 0 <= k < 50:
        return _json({"ok": False, "error": "bad_request"}, 400)
    seen = event_walk.stops()
    return _json(await event_walk.unless_stopped(seen, await event_tour.event_hand(k, stops_seen=seen)))


@app.post("/event/box-finish")
async def box_finish() -> JSONResponse:
    """"เดินออกให้ครบ" (two taps, the Event page): only what is left of a walk out of the box that stopped short. It
    drives her: refused while the ceremony app is on (its page has /ceremony/box-finish)."""
    if event_host.ceremony_now():
        return _json(event_host.ceremony_refusal())
    seen = event_walk.stops()
    return _json(await event_walk.unless_stopped(seen, await event_host.box_finish(stops_seen=seen)))


@app.post("/ceremony/box-finish")
async def ceremony_box_finish() -> JSONResponse:
    """The same from the ceremony page: only with the ceremony app on, and the walk is the ceremony's own (a re-dial
    of its call does not stop it: event_tour.hand_walk)."""
    from app import ceremony, event_tour

    async with _back_to_script() as closed:                   # a walk: not with her answering the host
        if not ceremony.on():
            return _json(_closed_first(ceremony.refusal(), closed))
        seen = event_walk.stops()
        result = await event_walk.unless_stopped(seen, await event_host.box_finish(stops_seen=seen))
        run = ((result.get("walk") or {}).get("run") or {}).get("id") if result.get("ok") else None
        if run is not None:
            event_tour.hand_walk(run)
        return _json(_closed_first(result, closed))


@app.post("/event/resume")
async def resume() -> JSONResponse:
    if _ceremony_on():
        return _json(event_host.ceremony_refusal())
    return _json(await event_host.resume())


@app.post("/event/dock-pose")
async def dock_pose() -> JSONResponse:
    return _json(await event_host.dock_pose())


@app.post("/event/dock-relearn")
async def dock_relearn() -> JSONResponse:
    """วัดแท่นใหม่ (owner 19 Nov: the dock is put down again at every setup). It walks: refused
    while the ceremony app is on (event_host.relearn_dock), and a red stop during its checks wins."""
    if _ceremony_on():
        return _json(event_host.ceremony_refusal())
    seen = event_walk.stops()
    return _json(await event_walk.unless_stopped(seen, await event_host.relearn_dock(stops_seen=seen)))


@app.get("/event/ready")
async def ready(request: Request) -> JSONResponse:
    """The morning's GO / NO-GO for the phone, read-only. `?fresh=1` re-reads the files."""
    from app import event_readiness

    try:
        return _json(await event_readiness.check(fresh=request.query_params.get("fresh") == "1"))
    except Exception:                                    # noqa: BLE001 -- a check, never the page's end
        logger.exception("event staff page: the morning check")
        return _json({"go": False, "rows": [{"ok": False, "th": "ตรวจความพร้อม", "detail_th": "ตรวจไม่ได้"}],
                      "at": time.time(), "not_here_th": ""})


@app.post("/event/spot")
async def spot(request: Request) -> JSONResponse:
    body = await _body(request)
    return _json(await event_host.save_spot(str(body.get("spot") or "")))


def _server_class():
    import uvicorn

    class EventServer(uvicorn.Server):
        # the main server owns SIGINT/SIGTERM (mirror_server's reason)
        def capture_signals(self):
            return contextlib.nullcontext()

    return EventServer


_server = {"obj": None}


async def serve() -> None:
    """The listener, until `stop_server()`. Off without the port or the key."""
    if not settings.event_control_port:
        return
    if not settings.event_control_key:
        logger.error("EVENT_CONTROL_PORT=%s is set but EVENT_CONTROL_KEY is not -- "
                     "the event staff page stays off", settings.event_control_port)
        return
    import uvicorn

    try:
        await event_host.recover_after_restart()
    except Exception:                                    # noqa: BLE001 -- the page matters more
        logger.exception("event staff page: could not look for an interrupted walk")
    config = uvicorn.Config(app, host=settings.event_control_host, port=settings.event_control_port,
                            lifespan="off", log_config=None, access_log=False)
    server = _server_class()(config)
    _server["obj"] = server
    logger.info("event staff pages: http://%s:%s/event-control?key=... and /ceremony-control?key=... (walking %s)",
                settings.event_control_host, settings.event_control_port,
                "on" if settings.event_walk_enabled else "off")
    try:
        await server.serve()
    except (OSError, SystemExit) as exc:
        logger.error("event staff page could not listen on %s:%s (%s)",
                     settings.event_control_host, settings.event_control_port, exc)


def stop_server() -> None:
    server = _server["obj"]
    if server is not None:
        server.should_exit = True
