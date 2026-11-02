"""The room screen's own listener (owner 28 Oct, app/face_mirror.py).

The robot's server stays on 127.0.0.1 (scripts/robot_server.py: the call
socket opens programs, drives the base, hears the room). This is a SECOND,
separate ASGI app on MIRROR_HOST:MIRROR_PORT, plain http (a view-only page
needs no microphone, so no secure origin), and it serves only:

  /mirror               the robot face page (client/voice-preview.html), which
                        reads its path and runs view-only (no mic, no call,
                        every send a no-op)
  /assets/...           the page's static files (story engine, 3D Emma)
  /plan-display-protocol.js, /story-asset/<sha>   what the page loads
  /ws/mirror            app/face_mirror.serve -- read-only

Every request needs MIRROR_TOKEN: `/mirror?token=...` once, which sets an
HttpOnly cookie for the rest (the page's three.js imports and pictures carry
no token). Without MIRROR_TOKEN the listener does not start at all.

`/mirror?ticket=...` is the same door for the screens the robot opens itself
(app/room_screens.py): a one-time ticket, spent on first use and dead after
TICKET_TTL_S, then a redirect to /mirror without it. That URL sits in a
browser's command line on a room PC -- anyone at its Task Manager can read
it -- so it must not be the token.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import logging
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, RedirectResponse, Response
from starlette.staticfiles import StaticFiles

from app import face_mirror
from app.config import settings

logger = logging.getLogger("condo_voice.mirror")

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "client" / "voice-preview.html"
ASSETS = ROOT / "client" / "assets"
PLAN_PROTOCOL = ROOT / "client" / "plan-display-protocol.js"
COOKIE = "emma_mirror"
_NO_CACHE = {"Cache-Control": "no-cache, must-revalidate", "X-Frame-Options": "SAMEORIGIN",
             "X-Content-Type-Options": "nosniff"}


def _token_ok(token: object) -> bool:
    return (bool(settings.mirror_token) and isinstance(token, str)
            and hmac.compare_digest(token.encode("utf-8"), settings.mirror_token.encode("utf-8")))


def _cookie_value() -> str:
    # derived, so the token itself never sits in a cookie
    return hmac.new(settings.mirror_token.encode("utf-8"), b"emma-mirror-v1", hashlib.sha256).hexdigest()


def _cookie_ok(conn) -> bool:
    value = conn.cookies.get(COOKIE) if settings.mirror_token else None
    return bool(value) and hmac.compare_digest(value.encode("utf-8"), _cookie_value().encode("utf-8"))


#: How long a ticket the robot hands a room PC stays good: the agent there
#: starts a browser, which asks for the page within a few seconds.
TICKET_TTL_S = 120.0
_tickets: dict[str, float] = {}


def issue_ticket() -> str:
    """One entrance to /mirror, for one room screen, once."""
    now = time.monotonic()
    for old, at in list(_tickets.items()):
        if now - at > TICKET_TTL_S:
            del _tickets[old]
    ticket = secrets.token_urlsafe(24)
    _tickets[ticket] = now
    return ticket


def _spend_ticket(value: object) -> bool:
    if not settings.mirror_token or not isinstance(value, str):
        return False
    at = _tickets.pop(value, None)
    return at is not None and time.monotonic() - at <= TICKET_TTL_S


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def _gate(request: Request, call_next):
    if request.url.path == "/mirror" and _spend_ticket(request.query_params.get("ticket")):
        rest = [(k, v) for k, v in request.query_params.multi_items() if k not in ("ticket", "token")]
        response = RedirectResponse("/mirror" + ("?" + urlencode(rest) if rest else ""), status_code=303)
        response.set_cookie(COOKIE, _cookie_value(), httponly=True, samesite="strict",
                            max_age=30 * 24 * 3600)
        return response
    if request.url.path == "/mirror" and _token_ok(request.query_params.get("token")):
        response = await call_next(request)
        response.set_cookie(COOKIE, _cookie_value(), httponly=True, samesite="strict",
                            max_age=30 * 24 * 3600)
        return response
    if not _cookie_ok(request):
        return Response("ต้องเปิดด้วย /mirror?token=... (MIRROR_TOKEN)", status_code=403,
                        media_type="text/plain; charset=utf-8")
    return await call_next(request)


@app.get("/mirror")
async def mirror_page() -> FileResponse:
    return FileResponse(PAGE, headers=_NO_CACHE)


@app.get("/plan-display-protocol.js")
async def plan_protocol() -> FileResponse:
    return FileResponse(PLAN_PROTOCOL, media_type="text/javascript", headers=_NO_CACHE)


@app.get("/story-asset/{sha256}")
async def story_asset(sha256: str):
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        return Response(status_code=404)
    from app import stories

    found = await asyncio.to_thread(stories.asset_body, sha256)
    if found is None:
        return Response(status_code=404)
    body, media_type = found
    return Response(content=body, media_type=media_type,
                    headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


@app.get("/slides/{name}")
async def slide_image(name: str):
    """An answer picture the robot's screen shows (face_mirror forwards its
    address): the call server's /slides rule exactly (app/slide_assets.py) --
    an image of the active catalogue that the display policy allows, never
    index.json or a file dropped beside the deck -- behind this listener's
    cookie instead of WS_TOKEN (the room PC never holds the call's token)."""
    from app.tools import slides

    if (Path(name).name != name or Path(name).suffix.lower()
            not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}):
        return Response(status_code=404)
    source = next((item for item in slides.load_slides() if item.get("file") == name), None)
    if source is None or slides.display_payload({
            "id": source.get("id"), "source_id": "slide_catalog",
            "project_id": settings.project_id}) is None:
        return Response(status_code=404)
    folder = Path(settings.slides_dir).expanduser()
    if not folder.is_absolute():
        folder = ROOT / folder
    path = folder / name
    if not path.is_file():
        return Response(status_code=404)
    return FileResponse(path, headers={"Cache-Control": "private, no-store",
                                       "X-Content-Type-Options": "nosniff"})


if ASSETS.is_dir():
    app.mount("/assets", StaticFiles(directory=str(ASSETS)), name="assets")


@app.websocket("/ws/mirror")
async def ws_mirror(ws: WebSocket) -> None:
    if not (_cookie_ok(ws) or _token_ok(ws.query_params.get("token"))):
        await ws.close(code=4403)
        return
    await ws.accept()
    await face_mirror.serve(ws)


def _server_class():
    import uvicorn

    class MirrorServer(uvicorn.Server):
        # The main server owns SIGINT/SIGTERM: uvicorn 0.52's serve() would
        # take them over for the whole process (capture_signals) and stop
        # this one first. Stopped by `should_exit` from main's shutdown hook.
        def capture_signals(self):
            return contextlib.nullcontext()

    return MirrorServer


_server = {"obj": None}


async def serve() -> None:
    """The listener, until `stop()`. Off without MIRROR_PORT or MIRROR_TOKEN."""
    if not settings.mirror_port:
        return
    if not settings.mirror_token:
        logger.error("MIRROR_PORT=%s is set but MIRROR_TOKEN is not -- the room screen listener stays off",
                     settings.mirror_port)
        return
    import uvicorn

    config = uvicorn.Config(app, host=settings.mirror_host, port=settings.mirror_port,
                            lifespan="off", log_config=None, access_log=False)
    server = _server_class()(config)
    _server["obj"] = server
    logger.info("room screen mirror: http://%s:%s/mirror?token=... (view only)",
                settings.mirror_host, settings.mirror_port)
    try:
        await server.serve()
    except (OSError, SystemExit) as exc:          # the port taken: the call server goes on
        logger.error("room screen mirror could not listen on %s:%s (%s)",
                     settings.mirror_host, settings.mirror_port, exc)


def stop() -> None:
    server = _server["obj"]
    if server is not None:
        server.should_exit = True
