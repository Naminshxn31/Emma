"""The event staff's phone page (owner 30 Oct: "ให้ทีมงานกดเปลี่ยนซีน").

The robot's call server stays on 127.0.0.1 (scripts/robot_server.py) and the
room screen's listener is view-only by its own definition (app/mirror_server.py)
-- this page starts walks, so it is neither: a THIRD listener, plain http on
EVENT_CONTROL_HOST:EVENT_CONTROL_PORT, behind its own EVENT_CONTROL_KEY (the
staff phone never holds WS_TOKEN or MIRROR_TOKEN). Without the key it does not
start at all.

  /event-control         the page (client/event-control.html)
  GET  /event/state      what the page shows -- and the walk's dead man's
                         switch: every poll is a heartbeat (app/event_walk.py)
  POST /event/scene      {scene, stop?}   switch scene (walks if it has a spot)
  POST /event/next                       the scene's next stop (the next table)
  POST /event/stop-index {stop}          one stop of the scene (a table's chip)
  POST /event/go         {spot}           walk there, nothing said
  POST /event/stop                       stop the walk
  POST /event/spot       {spot}           the robot's pose now = this spot

`/event-control?key=...` once sets an HttpOnly, SameSite=Strict cookie (a
derived value, never the key itself); every other request needs the cookie.
SameSite=Strict is what keeps another site's page from POSTing here with it.
Plain http on the office Wi-Fi is the mirror's trade-off too: anyone on that
network who sniffs the cookie could drive the scenes -- and the E-stop, the
voice "หยุด" and the page's STOP all still work against them.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from app import event_host, event_walk
from app.config import settings

logger = logging.getLogger("condo_voice.event_control")

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "client" / "event-control.html"
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
    if request.url.path == "/event-control" and _key_ok(request.query_params.get("key")):
        # the key leaves the address bar at once (history, a screenshot)
        response = Response(status_code=303, headers={"Location": "/event-control", **_NO_CACHE})
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


@app.get("/event/state")
async def state() -> JSONResponse:
    event_walk.heartbeat()
    return _json(event_host.snapshot())


@app.post("/event/scene")
async def scene(request: Request) -> JSONResponse:
    body = await _body(request)
    stop = body.get("stop")
    result = await event_host.start_scene(str(body.get("scene") or ""),
                                          stop_index=stop if isinstance(stop, int) else 0)
    return _json(result)


@app.post("/event/next")
async def next_stop() -> JSONResponse:
    return _json(await event_host.next_stop())


@app.post("/event/stop-index")
async def stop_index(request: Request) -> JSONResponse:
    body = await _body(request)
    index = body.get("stop")
    if not isinstance(index, int):
        return _json({"ok": False, "error": "bad_request"}, 400)
    return _json(await event_host.go_stop(index))


@app.post("/event/go")
async def go(request: Request) -> JSONResponse:
    body = await _body(request)
    return _json(await event_host.go_spot(str(body.get("spot") or "")))


@app.post("/event/stop")
async def stop() -> JSONResponse:
    return _json(await event_host.stop())


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

    config = uvicorn.Config(app, host=settings.event_control_host, port=settings.event_control_port,
                            lifespan="off", log_config=None, access_log=False)
    server = _server_class()(config)
    _server["obj"] = server
    logger.info("event staff page: http://%s:%s/event-control?key=... (walking %s)",
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
