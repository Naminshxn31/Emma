"""The ceremony's film on the robot's own chest screen (owner 24 Nov 2026, CEREMONY_FACE_FILM).

Owner: "อยากจะให้ตอนพูด จอหุ่น กับจอคอมนี้ขึ้นพรีเซ้นต์โมชั่นเหมือนกันเลย จะได้เหมือนซ้อมจริง" -- and, asked
whether the chest screen should play the film too: "ทำเลย เพราะไม่งั้นมันไม่ตรงกันไหม ยังไงหน้าหุ่นก็ไม่ได้อันนั้นอยู่แล้ว".

The film is the room screen's own page (`/mirror`, client/voice-preview.html in its show mode): the same acts,
the same Thai subtitles, the same clock (the voice the page plays). The robot's face page puts it in a silent
full-width frame while the ceremony app's call is up. Why it is served HERE, from the call server's own
origin, and not framed from the room-screen listener (app/mirror_server.py on MIRROR_PORT):

  * the face page is https://127.0.0.1:8001 and the listener is plain http on another port -- another ORIGIN:
    the WebView is set to MIXED_CONTENT_NEVER_ALLOW, the listener's pages say X-Frame-Options SAMEORIGIN, and its
    SameSite=Strict cookie is not sent to a frame of another scheme (schemeful same-site, Chrome 89+), so every
    request of the framed page after the first would be a 403;
  * same origin has none of that, and the frame inherits the page's autoplay (EmmaFace.java
    setMediaPlaybackRequiresUserGesture(false)) -- the page's clock follows the audio it plays.

The doors are the listener's own (mirror_server's handlers, reused as they are), behind a cookie of their own:
`/mirror`, `/ws/mirror`, `/ceremony-asset/<sha>`, `/ceremony-acts.json`, `/event-clip/<sha>`. The first entrance
is a one-time ticket (`mirror_server.issue_ticket`, 120 s) that the call's `ready` carries to the face page --
never MIRROR_TOKEN, which sits in the listener's command lines. Nothing here is reachable without that cookie,
and the frame's socket is view only like every mirror socket (app/face_mirror.serve).

What the frame is NOT: a screen of the hall. `face_mirror` counts it apart (`film=True`) so the debut's
"which screens are watching" (app/ceremony_reveal.py) still means the hall's. It does not answer a clip's
cue either (/event-clip-state is not served here: only the hall's screen reports what it played).
"""
from __future__ import annotations

import hashlib
import hmac
import re

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, RedirectResponse, Response

from app import face_mirror, mirror_server
from app.config import settings

COOKIE = "emma_film"
#: The listener's page, one file for every screen; only this route serves it without the 3D mascot.
PAGE = mirror_server.PAGE
_MASCOT_TAG = re.compile(r'<script type="module" src="/assets/emma-3d-lab/mascot\.js[^"]*"></script>')
_NO_CACHE = {"Cache-Control": "no-cache, must-revalidate", "X-Content-Type-Options": "nosniff"}

router = APIRouter()


def enabled() -> bool:
    """CEREMONY_FACE_FILM on, and the cookie it is entered with can be made (MIRROR_TOKEN set)."""
    return bool(settings.ceremony_face_film and settings.mirror_token)


def ticket() -> str | None:
    """A one-time entrance for the face page's frame, for the ceremony call's `ready`; None = off."""
    return mirror_server.issue_ticket() if enabled() else None


def _cookie_value() -> str:
    # derived from its own label, so it is not the room-screen listener's cookie (that one is not valid here
    # and this one is not valid there), and the token itself never sits in a cookie
    return hmac.new(settings.mirror_token.encode("utf-8"), b"emma-face-film-v1", hashlib.sha256).hexdigest()


def _cookie_ok(conn) -> bool:
    if not enabled():
        return False
    value = conn.cookies.get(COOKIE)
    return bool(value) and hmac.compare_digest(value.encode("utf-8"), _cookie_value().encode("utf-8"))


def cookie_ok(conn) -> bool:
    """Is this request the film's own frame (main.py's /story-asset takes it beside WS_TOKEN: a story's
    pictures under the film)."""
    return _cookie_ok(conn)


def _set_cookie(response: Response, request: Request) -> None:
    response.set_cookie(COOKIE, _cookie_value(), httponly=True, samesite="strict", path="/",
                        secure=request.url.scheme == "https", max_age=24 * 3600)


async def _gate(request: Request) -> None:
    if not _cookie_ok(request):
        raise HTTPException(status_code=403)


def page_for_film() -> bytes:
    """voice-preview.html as the frame gets it: the 3D mascot's module left out. The face page around the
    frame already draws her (and the frame's show mode hides her anyway); a second WebGL context and the
    162k-triangle model for nobody is memory and GPU the film's pictures need on the robot."""
    html = PAGE.read_text(encoding="utf-8")
    return _MASCOT_TAG.sub("", html, count=1).encode("utf-8")


@router.get("/mirror")
async def film_page(request: Request) -> Response:
    if not enabled():
        return Response(status_code=404)
    if mirror_server._spend_ticket(request.query_params.get("ticket")):
        response = RedirectResponse("/mirror?film=1", status_code=303)
        _set_cookie(response, request)
        return response
    if not _cookie_ok(request):
        return Response("forbidden", status_code=403, media_type="text/plain")
    return Response(page_for_film(), media_type="text/html; charset=utf-8", headers=_NO_CACHE)


@router.websocket("/ws/mirror")
async def film_socket(ws: WebSocket) -> None:
    if not _cookie_ok(ws):
        await ws.close(code=4403)
        return
    await ws.accept()
    await face_mirror.serve(ws, face_mirror.kind_from_query(ws.query_params), film=True)


# the listener's own handlers, as they are (their gate there is the listener's cookie; here, the film's)
router.add_api_route("/ceremony-asset/{sha256}", mirror_server.ceremony_asset, methods=["GET"],
                     dependencies=[Depends(_gate)])
router.add_api_route("/ceremony-acts.json", mirror_server.ceremony_acts, methods=["GET"],
                     dependencies=[Depends(_gate)])
router.add_api_route("/event-clip/{sha256}", mirror_server.event_clip_file, methods=["GET"],
                     dependencies=[Depends(_gate)])
