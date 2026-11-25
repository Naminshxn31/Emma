"""The robot's door for the scan booth (docs/emma-face-approach.md).

The robot's call server stays on 127.0.0.1, the room screen's listener is
view-only and the staff page's starts walks: none of them is where a person's
face vectors should arrive. A FOURTH listener with its own key (GUEST_SYNC_KEY:
the booth holds neither WS_TOKEN, MIRROR_TOKEN nor EVENT_CONTROL_KEY), and HTTPS
only: the two listeners before it are plain http on the office Wi-Fi by a trade
they wrote down, and a face vector is not a scene switch. No key or no
certificate = it does not start, and says so.

  POST /guests/add     one guest: name, vectors, model tag, the booth's consent record
  POST /guests/revoke  {name}: the record says withdrawn, the rows are gone
  GET  /guests/status  how many, which model, the longest consent kept: never who

Sent with `Authorization: Bearer <key>`, never in a URL (a URL is what logs and
histories keep). Five wrong keys lock that client out for a minute, as the
scan PIN does. No page and no list: nothing here can tell anybody who is on it.
"""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import consent, faces, guests
from app.config import settings

logger = logging.getLogger("condo_voice.guest_sync")

#: Eight rows of 512 float32 in base64 are about 22 KB.
MAX_BODY = 64 * 1024
KEY_TRIES = 5
LOCKOUT_S = 60.0
MAX_CLIENTS = 64

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_wrong: dict[str, list[float]] = {}
_server: dict = {"obj": None, "task": None}

_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def reset() -> None:
    _wrong.clear()


def _json(doc: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(doc, status_code=status, headers=_HEADERS)


def _key_ok(header: object) -> bool:
    key = settings.guest_sync_key
    if not key or not isinstance(header, str) or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[7:].strip().encode("utf-8"), key.encode("utf-8"))


@app.middleware("http")
async def _gate(request: Request, call_next):
    client = request.client.host if request.client else ""
    now = time.monotonic()
    for host, (_count, until) in list(_wrong.items()):
        if until and now >= until:
            del _wrong[host]
    entry = _wrong.get(client)
    if entry is not None and entry[1]:
        return _json({"ok": False, "error": "locked"}, 429)
    if not _key_ok(request.headers.get("authorization")):
        if entry is None and len(_wrong) >= MAX_CLIENTS:
            return _json({"ok": False, "error": "locked"}, 429)
        count = (entry[0] if entry else 0) + 1
        _wrong[client] = [count, now + LOCKOUT_S if count >= KEY_TRIES else 0.0]
        return _json({"ok": False, "error": "key"}, 401)
    _wrong.pop(client, None)
    return await call_next(request)


async def _body(request: Request) -> dict | None:
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return None
    with contextlib.suppress(ValueError):
        doc = json.loads(raw or b"{}")
        return doc if isinstance(doc, dict) else None
    return None


def _status_of(result: dict) -> int:
    if result.get("ok"):
        return 200
    return 503 if result.get("error") in ("busy", "write_failed", "consent_unreadable") else 409


@app.post("/guests/add")
async def add(request: Request) -> JSONResponse:
    doc = await _body(request)
    if doc is None:
        return _json({"ok": False, "error": "bad_request"}, 400)
    result = await asyncio.to_thread(guests.add, doc)
    return _json(result, _status_of(result))


@app.post("/guests/revoke")
async def revoke(request: Request) -> JSONResponse:
    doc = await _body(request)
    if doc is None:
        return _json({"ok": False, "error": "bad_request"}, 400)
    result = await asyncio.to_thread(guests.revoke, doc.get("name"))
    return _json(result, _status_of(result))


@app.get("/guests/status")
async def status() -> JSONResponse:
    count = await asyncio.to_thread(guests.count)
    # max_days: the booth's --check tells a robot still on the old 30-day rule
    # before a guest scans, instead of every record coming back refused.
    return _json({"ok": True, "guests": count, "model_tag": faces.MODEL_TAG,
                  "max_days": consent.MAX_GUEST_DAYS})


def _refusal() -> str | None:
    if not settings.guest_sync_port:
        return "off"
    if not settings.guest_sync_key:
        return "GUEST_SYNC_KEY is not set"
    if not (settings.ssl_certfile and settings.ssl_keyfile):
        return "no SSL_CERTFILE / SSL_KEYFILE: face vectors do not cross the Wi-Fi in clear"
    if not settings.face_guests_file:
        return "FACE_GUESTS_FILE is not set"
    return None


async def serve() -> None:
    why = _refusal()
    if why:
        if settings.guest_sync_port:
            logger.error("GUEST_SYNC_PORT=%s is set but the listener stays off: %s",
                         settings.guest_sync_port, why)
        return
    import uvicorn

    class SyncServer(uvicorn.Server):
        # the main server owns SIGINT/SIGTERM (mirror_server's reason)
        def capture_signals(self):
            return contextlib.nullcontext()

    config = uvicorn.Config(app, host=settings.guest_sync_host, port=settings.guest_sync_port,
                            ssl_certfile=settings.ssl_certfile, ssl_keyfile=settings.ssl_keyfile,
                            lifespan="off", log_config=None, access_log=False)
    server = SyncServer(config)
    _server["obj"] = server
    logger.info("guest sync: https://%s:%s", settings.guest_sync_host, settings.guest_sync_port)
    try:
        await server.serve()
    except (OSError, SystemExit) as exc:
        logger.error("guest sync could not listen on %s:%s (%s)",
                     settings.guest_sync_host, settings.guest_sync_port, exc)


def start() -> None:
    task = _server["task"]
    if task is None or task.done():
        _server["task"] = asyncio.create_task(serve())


async def stop() -> None:
    server, task = _server["obj"], _server["task"]
    if server is not None:
        server.should_exit = True
    if task is not None and not task.done():
        with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError, Exception):
            await asyncio.wait_for(task, 3.0)
    _server.update(obj=None, task=None)
