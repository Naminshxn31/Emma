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
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

ROOT = Path(__file__).resolve().parent
PAGE = ROOT / "client" / "room-console.html"

# room_control.py is a script, not a package module; load it the way the tests do.
_spec = importlib.util.spec_from_file_location("room_control", ROOT / "scripts" / "room_control.py")
room_control = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(room_control)

HOST = os.getenv("ROOM_CONSOLE_HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = int(os.getenv("ROOM_CONSOLE_PORT", "8800"))

_NO_CACHE = {"Cache-Control": "no-store"}

app = FastAPI(title="Emma room console", docs_url=None, redoc_url=None)


@app.get("/")
async def page() -> Response:
    return FileResponse(PAGE, headers=_NO_CACHE)


@app.get("/rooms")
async def rooms() -> JSONResponse:
    try:
        table = room_control.rooms()
    except room_control.RoomError as exc:
        return JSONResponse({"rooms": [], "error": str(exc)})
    return JSONResponse({"rooms": sorted(table)})


@app.get("/health")
async def health(room: str = "") -> JSONResponse:
    try:
        return JSONResponse(room_control.health(room))
    except room_control.RoomError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@app.post("/do")
async def do(request: Request) -> JSONResponse:
    body = await request.json()
    room = str((body or {}).get("room", ""))
    action = str((body or {}).get("action", ""))
    args = (body or {}).get("args") or {}
    if not isinstance(args, dict):
        return JSONResponse({"ok": False, "error": "bad args"}, status_code=400)
    try:
        # `screen` carries a base64 image; it does not belong in a JSON reply that
        # the browser then base64-decodes again, so /screen is the way to view it.
        result = room_control.do(room, action, args)
    except room_control.RoomError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    if isinstance(result, dict) and result.get("jpeg_base64"):
        result = {**result, "jpeg_base64": "(use /screen)"}
    return JSONResponse(result)


@app.get("/screen")
async def screen(room: str = "") -> Response:
    """The room's screen as an image, so a plain <img src="/screen?room=.."> shows
    it and can be refreshed by changing a cache-buster query."""
    try:
        result = room_control.do(room, "screen")
    except room_control.RoomError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
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


def main() -> None:
    import uvicorn

    print(f"Room console on http://{HOST}:{PORT}  (rooms from data/rooms.json)")
    if HOST not in {"127.0.0.1", "localhost"}:
        print(f"  WARNING: bound to {HOST} -- this page drives PCs; keep it off the open network.")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
