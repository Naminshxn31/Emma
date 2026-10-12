"""A local control panel for the room agents -- pick a room, watch its screen,
click to send commands.

Run it on the management machine:

    python room_console_server.py
    # then open http://127.0.0.1:8800

This is the OPERATOR's tool and is deliberately OUTSIDE app/. The tests that keep
the room agent and its caller out of Emma scan app/ for exactly this kind of
reference; the console proxies to the agents through scripts/room_control, so it
belongs on the same side of that line as the caller, not inside Emma. It inherits
room_control's rules unchanged: the explicit rooms.json, a token per room, and an
unknown room is an error, never a guess.

It binds 127.0.0.1 by default, because a page that drives sales-room PCs should be
reachable only from the machine running it. Set ROOM_CONSOLE_HOST to widen that on
purpose, never by default.
"""

from __future__ import annotations

import base64
import importlib.util
import os
import urllib.request
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

ROOT = Path(__file__).resolve().parent
PAGE = ROOT / "client" / "room-console.html"

# room_control.py is a script, not a package module; load it the way the tests do.
_spec = importlib.util.spec_from_file_location("room_control", ROOT / "scripts" / "room_control.py")
room_control = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(room_control)

HOST = os.getenv("ROOM_CONSOLE_HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = int(os.getenv("ROOM_CONSOLE_PORT", "8800"))
# Where THIS machine's agent-bundle server is, over loopback -- used only to read
# the bundle's sha256 so a self-update can be integrity-checked (see /do).
BUNDLE_URL = os.getenv("ROOM_AGENT_BUNDLE_URL", "http://127.0.0.1:8799/agent-bundle").strip()

_NO_CACHE = {"Cache-Control": "no-store"}

app = FastAPI(title="Emma room console", docs_url=None, redoc_url=None)

# This page drives sales-room PCs and Android panels. It binds loopback, but a
# browser is a shared surface: a page the operator has open on any OTHER site can
# still POST here (the response is opaque to it, but the action runs), and a DNS
# rebinding page can make itself same-origin. Two cheap gates close both:
#   - a Host allow-list (TrustedHost) refuses a rebinding page's forged Host;
#   - typed JSON bodies on the POST routes make FastAPI require application/json,
#     which a cross-origin page cannot send without a preflight it will fail
#     (a plain form / text-plain / no-type body is now 422, not an executed act).
_ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
if HOST not in _ALLOWED_HOSTS:
    _ALLOWED_HOSTS.add(HOST)                 # a deliberate ROOM_CONSOLE_HOST widening
for _extra in os.getenv("ROOM_CONSOLE_ALLOWED_HOSTS", "").split(","):
    if _extra.strip():
        _ALLOWED_HOSTS.add(_extra.strip())
app.add_middleware(TrustedHostMiddleware, allowed_hosts=sorted(_ALLOWED_HOSTS))


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    resp = await call_next(request)
    # Belt and braces against clickjacking the no-confirm buttons.
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    return resp


class DoBody(BaseModel):
    room: str = ""
    action: str = ""
    args: dict = {}


class ScrcpyBody(BaseModel):
    room: str = ""
    audio: str = "off"      # "off" | "playback" (panel output audio) | "mic" (room)


class WakeBody(BaseModel):
    room: str = ""


def _bundle_sha() -> str:
    """The sha256 the LOCAL bundle server would serve, read over loopback (a
    trusted hop on this machine). Passed to the room agent so it can check that
    the bytes it pulls over the LAN match what this machine built -- a rogue host
    answering the office PC's IP while it is off cannot match it. Best-effort: if
    the bundle server is unreachable the agent's own download fails too."""
    try:
        with urllib.request.urlopen(BUNDLE_URL + ".sha256", timeout=5) as r:
            return r.read(128).decode("ascii", "replace").strip()
    except Exception:
        return ""


@app.get("/")
async def page() -> Response:
    return FileResponse(PAGE, headers=_NO_CACHE)


@app.get("/rooms")
async def rooms() -> JSONResponse:
    try:
        table = await run_in_threadpool(room_control.rooms)
    except room_control.RoomError as exc:
        return JSONResponse({"rooms": [], "error": str(exc)})
    except Exception as exc:
        return JSONResponse({"rooms": [], "error": "could not read rooms (%s)" % type(exc).__name__},
                            status_code=502)
    return JSONResponse({"rooms": sorted(table)})


@app.get("/health")
async def health(room: str = "") -> JSONResponse:
    # room_control talks to the room over the network / adb (blocking). Run it in
    # a thread so one slow room never freezes the whole console's event loop --
    # otherwise a single hung request makes even /rooms time out.
    try:
        return JSONResponse(await run_in_threadpool(room_control.health, room))
    except room_control.RoomError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": "could not reach the room (%s)" % type(exc).__name__},
                            status_code=502)


@app.post("/do")
async def do(body: DoBody) -> JSONResponse:
    args = dict(body.args or {})
    # A self-update is the one action whose payload is code that will be executed
    # on the room PC. Attach the sha256 this trusted machine built, so the agent
    # refuses a substituted bundle. Attached here, not trusted from the browser.
    if body.action == "update" and "sha256" not in args:
        sha = await run_in_threadpool(_bundle_sha)
        if sha:
            args["sha256"] = sha
    try:
        # `screen` carries a base64 image; it does not belong in a JSON reply that
        # the browser then base64-decodes again, so /screen is the way to view it.
        result = await run_in_threadpool(room_control.do, body.room, body.action, args)
    except room_control.RoomError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        # Any other failure (upstream timeout, a malformed reply) must still be
        # JSON the browser can read, never a bare 500 it decodes as an error.
        return JSONResponse({"ok": False, "error": "room agent did not respond (%s)" % type(exc).__name__},
                            status_code=502)
    if isinstance(result, dict) and result.get("jpeg_base64"):
        result = {**result, "jpeg_base64": "(use /screen)"}
    return JSONResponse(result)


@app.get("/screen")
async def screen(room: str = "") -> Response:
    """The room's screen as an image, so a plain <img src="/screen?room=.."> shows
    it and can be refreshed by changing a cache-buster query."""
    try:
        result = await run_in_threadpool(room_control.do, room, "screen")
    except room_control.RoomError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": "room agent did not respond (%s)" % type(exc).__name__},
                            status_code=502)
    if not (isinstance(result, dict) and result.get("ok") and result.get("jpeg_base64")):
        detail = result.get("error", "no image") if isinstance(result, dict) else "no image"
        return JSONResponse({"ok": False, "error": detail}, status_code=502)
    try:
        raw = base64.b64decode(result["jpeg_base64"])
    except Exception:
        return JSONResponse({"ok": False, "error": "bad image"}, status_code=502)
    headers = dict(_NO_CACHE)
    if result.get("size"):
        headers["X-Screen-Size"] = ",".join(str(n) for n in result["size"])
    return Response(content=raw, media_type="image/jpeg", headers=headers)


@app.get("/discover")
async def discover() -> JSONResponse:
    """Android panels adb can see right now, with the serial to add to rooms.json
    -- so setting up a new panel is copy-a-line, not hunt-for-a-number."""
    try:
        return JSONResponse({"devices": await run_in_threadpool(room_control.discover_android)})
    except Exception as exc:
        return JSONResponse({"devices": [], "error": "could not run discovery (%s)" % type(exc).__name__})


@app.post("/wake")
async def wake(body: WakeBody) -> JSONResponse:
    """Send a Wake-on-LAN packet to a powered-off Windows room PC. Fire-and-forget
    (UDP): it reports the signal was sent, never that the PC came on."""
    try:
        return JSONResponse(await run_in_threadpool(room_control.wake, body.room))
    except room_control.RoomError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": "could not send wake (%s)" % type(exc).__name__},
                            status_code=502)


@app.post("/scrcpy")
async def scrcpy_launch(body: ScrcpyBody) -> JSONResponse:
    """Open a real-time scrcpy window (Android rooms) on the machine running the
    console. Local-only, like the rest of this page, and only for a known room.
    `audio`: off (video only), playback (panel output audio), or mic (the room).
    Live only -- never recorded."""
    try:
        return JSONResponse(await run_in_threadpool(room_control.launch_scrcpy, body.room, body.audio))
    except room_control.RoomError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": "could not start scrcpy (%s)" % type(exc).__name__},
                            status_code=502)


def main() -> None:
    import uvicorn

    print(f"Room console on http://{HOST}:{PORT}  (rooms from data/rooms.json)")
    if HOST not in {"127.0.0.1", "localhost"}:
        print(f"  WARNING: bound to {HOST} -- this page drives PCs; keep it off the open network.")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
