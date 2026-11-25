"""The ceremony's film on the robot's own chest screen (owner 24 Nov 2026, CEREMONY_FACE_FILM, app/face_film.py).

What these hold down:
* off (the default) = nothing of it exists: no routes on the call server, no ticket in `ready`, the page's
  film code reachable only through a ticket that never comes;
* on = the room screen's page served from the call server's own origin behind a cookie of its own: a
  one-time ticket is the entrance (never MIRROR_TOKEN, not the listener's cookie), every door answers
  403 without it, the page comes without the 3D mascot, and the frame's socket is not a screen of the hall;
* the face page: the frame only in the ceremony app, hidden until the frame says its show mode is on,
  taking no touch, the mascot stopping under it, gone with the call.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import face_film, face_mirror, mirror_server
from app.config import settings

ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "client" / "voice-preview.html").read_text(encoding="utf-8")


@pytest.fixture
def film(monkeypatch):
    monkeypatch.setattr(settings, "ceremony_face_film", True)
    monkeypatch.setattr(settings, "mirror_token", "test-mirror-token-0123456789")
    face_mirror.reset()
    mirror_server._tickets.clear()
    app = FastAPI()
    app.include_router(face_film.router)
    # (https, as the robot's face page is: the cookie is Secure there)
    with TestClient(app, base_url="https://127.0.0.1:8001") as client:
        yield client
    face_mirror.reset()
    mirror_server._tickets.clear()


def _enter(client) -> None:
    """The face's frame: the ticket, then the cookie it earns."""
    ticket = face_film.ticket()
    assert ticket
    r = client.get("/mirror", params={"film": "1", "ticket": ticket}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/mirror?film=1"


# ---- off ---------------------------------------------------------------------------------------------

def test_the_scale_is_clamped_and_one_by_default():
    assert settings.ceremony_face_film_scale == 1.0                         # (conftest pins it; the code's own floor:)
    src = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    assert 'max(0.4, min(1.0, float(os.getenv("CEREMONY_FACE_FILM_SCALE", "1") or 1)))' in src


def test_off_by_default_and_without_a_mirror_token(monkeypatch):
    assert face_film.enabled() is False and face_film.ticket() is None     # (conftest pins the switch off)
    monkeypatch.setattr(settings, "ceremony_face_film", True)
    monkeypatch.setattr(settings, "mirror_token", "")
    assert face_film.enabled() is False and face_film.ticket() is None     # a ticket is made from the token
    monkeypatch.setattr(settings, "mirror_token", "x" * 24)
    assert face_film.enabled() is True and face_film.ticket()


def test_the_call_server_has_the_routes_only_when_the_switch_was_on_at_start():
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    block = main[main.index("if settings.ceremony_face_film:"):][:200]
    assert "app.include_router(face_film.router)" in block
    assert main.count("face_film.router") == 1
    from app import main as main_module
    paths = {getattr(r, "path", "") for r in main_module.app.routes}
    assert ("/ws/mirror" in paths) == bool(settings.ceremony_face_film is True or "/ws/mirror" in paths)
    if not any(p == "/ws/mirror" for p in paths):
        assert "/mirror" not in paths and "/ceremony-asset/{sha256}" not in paths


def test_ready_carries_a_ticket_only_for_the_ceremony_robot_face_with_the_switch_on():
    src = (ROOT / "app" / "session.py").read_text(encoding="utf-8")
    block = src[src.index('if robot_face and self.profile == "ceremony" and settings.ceremony_face_film:'):][:700]
    assert 'ready["film_ticket"] = film' in block and "face_film.ticket()" in block
    assert 'ready["film_scale"] = round(settings.ceremony_face_film_scale, 2)' in block   # only below 1
    # the room screens' copy of `ready` never carries it
    assert "film_ticket" not in json.dumps(face_mirror.view({"type": "ready", "profile": "ceremony",
                                                               "film_ticket": "T" * 24}))
    assert settings.ceremony_face_film is False


# ---- on: the doors ---------------------------------------------------------------------------------------

def test_every_door_is_403_without_the_films_cookie(film):
    for path in ("/mirror", "/ceremony-acts.json", "/event-clip/" + "a" * 64, "/ceremony-asset/" + "a" * 64):
        assert film.get(path).status_code == 403, path
    # MIRROR_TOKEN in the URL is not an entrance here (it sits in the listener's command lines)
    assert film.get("/mirror", params={"token": settings.mirror_token}).status_code == 403
    # nor is the room-screen listener's cookie
    film.cookies.set(mirror_server.COOKIE, mirror_server._cookie_value())
    assert film.get("/mirror").status_code == 403


def test_a_ticket_is_spent_once_and_earns_a_strict_httponly_cookie(film):
    ticket = face_film.ticket()
    first = film.get("/mirror", params={"film": "1", "ticket": ticket}, follow_redirects=False)
    assert first.status_code == 303 and first.headers["location"] == "/mirror?film=1"
    cookie = first.headers["set-cookie"]
    assert cookie.startswith(face_film.COOKIE + "=") and "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert "Secure" in cookie and settings.mirror_token not in cookie
    film.cookies.clear()
    again = film.get("/mirror", params={"ticket": ticket}, follow_redirects=False)
    assert again.status_code == 403                                         # one-time
    assert face_film.ticket() != face_film.ticket()


def test_the_page_comes_without_the_3d_mascot_and_not_from_any_cache(film):
    _enter(film)
    page = film.get("/mirror")
    assert page.status_code == 200 and "no-cache" in page.headers["cache-control"]
    html = page.text
    assert not re.search(r'<script type="module" src="/assets/emma-3d-lab/mascot\.js', html)
    # nothing else of the page changed: it is the room screen's page
    assert len(html) > 0.99 * len(PAGE) - 200
    assert "var mirrorFilm = mirror && q.get('film') === '1';" in html
    # the strip pattern still finds the tag in the real file (a reworded tag would load the model twice)
    assert face_film._MASCOT_TAG.search(PAGE)


def test_the_listeners_handlers_are_the_ones_reused(film):
    _enter(film)
    assert film.get("/ceremony-acts.json").status_code == 200
    assert film.get("/ceremony-asset/" + "a" * 64).status_code == 404       # a file the index does not hold
    assert film.get("/ceremony-asset/nothex").status_code == 404
    assert film.get("/event-clip/" + "b" * 64).status_code == 404


def test_the_frames_socket_is_view_only_and_not_a_screen_of_the_hall(film):
    with pytest.raises(Exception):
        with film.websocket_connect("/ws/mirror?sound=0&w=1080&h=779&coarse=1"):
            pass                                                            # no cookie: closed 4403
    _enter(film)
    face_mirror.call_started()
    face_mirror.publish_json({"type": "ready", "output_rate": 24000, "profile": "ceremony"})
    # (a browser sends the Secure cookie over wss; the test client's ws:// does not -- the header says it)
    cookie = {"cookie": "%s=%s" % (face_film.COOKIE, face_film._cookie_value())}
    with film.websocket_connect("/ws/mirror?sound=0&w=1080&h=779&coarse=1", headers=cookie) as ws:
        first = json.loads(ws.receive_text())
        assert first["type"] == "mirror_hello" and first["in_call"] is True
        kinds = [first["type"]]
        while kinds[-1] != "ready":
            kinds.append(json.loads(ws.receive_text())["type"])
        assert face_mirror.watching() is True                               # it gets the stream ...
        assert face_mirror.screens() == 0 and face_mirror.screen_kinds() == []   # ... and is not a hall screen
        face_mirror.publish_json({"type": "turn_complete"})
        assert json.loads(ws.receive_text())["type"] == "turn_complete"
    assert face_mirror.watching() is False


def test_a_hall_screen_beside_the_frame_is_still_counted_alone():
    face_mirror.reset()
    try:
        import asyncio

        async def run():
            class W:
                query_params: dict = {}

                async def send_text(self, t): pass

                async def send_bytes(self, b): pass

                async def receive(self):
                    await asyncio.sleep(10)

            hall = asyncio.create_task(face_mirror.serve(W(), {"sound": True, "w": 4096, "h": 2160, "coarse": False}))
            face = asyncio.create_task(face_mirror.serve(W(), {"sound": False, "w": 1080, "h": 779, "coarse": True},
                                                         film=True))
            await asyncio.sleep(0.2)
            assert face_mirror.screens() == 1
            assert face_mirror.screen_kinds() == [{"sound": True, "w": 4096, "h": 2160, "coarse": False}]
            from app import ceremony_reveal
            assert ceremony_reveal._screens()[0] == 1
            face.cancel(); hall.cancel()
            await asyncio.gather(face, hall, return_exceptions=True)
            assert face_mirror.screens() == 0 and face_mirror._film == set()
        asyncio.run(run())
    finally:
        face_mirror.reset()


def test_the_story_pictures_under_the_film_take_the_films_cookie_not_the_token():
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    block = main[main.index("async def story_asset("):][:900]
    assert "face_film.cookie_ok(request)" in block and "settings.ws_token" in block


# ---- the face page ------------------------------------------------------------------------------------------

def test_the_face_page_opens_the_frame_only_in_the_ceremony_app_and_only_with_a_ticket():
    body = PAGE[PAGE.index("var faceFilmFrame = null;"):PAGE.index("function releaseCallHardware() {")]
    assert "if (!ceremonyApp || faceFilmFrame" in body                      # not the Event app, not the sales robot
    assert "[A-Za-z0-9_-]{16,64}" in body                                   # a ticket's shape, nothing else in src
    assert "'/mirror?film=1&ticket=' + encodeURIComponent(evt.film_ticket)" in body
    assert "if (scale >= 0.4 && scale < 1) { f.style.setProperty('--ff-s', String(scale));" in body
    assert "html.app-shell .face-film[data-scaled] {" in PAGE and "will-change: transform" in PAGE
    assert "location.origin" in body and "e.source !== faceFilmFrame.contentWindow" in body
    assert PAGE.count(" faceFilmOpen(evt);") == 1
    ready = PAGE[PAGE.index("        case 'ready':"):][:3600]
    assert "if (evt.film_ticket && typeof faceFilmOpen === 'function') faceFilmOpen(evt);" in ready
    assert "MIRROR_TOKEN" not in body and "mirror_token" not in body.lower()


def test_the_frame_goes_with_the_call_and_the_mascot_stops_under_it():
    assert "if (typeof faceFilmClose === 'function') faceFilmClose();" in PAGE[
        PAGE.index("function releaseCallHardware() {"):][:400]
    # every way a call starts, ends or fails passes releaseCallHardware (startCall, onclose, endCall)
    for site in ("function startCall()", "ws.onclose = function () {", "function endCall() {"):
        assert "releaseCallHardware();" in PAGE[PAGE.index(site):][:2600], site
    mascot = (ROOT / "client" / "assets" / "emma-3d-lab" / "mascot.js").read_text(encoding="utf-8")
    loop = mascot[mascot.index("renderer.setAnimationLoop(() => {"):]
    rest = "if (document.documentElement.classList.contains('face-film-on')) { clock.getDelta(); return; }"
    assert rest in loop and loop.index(rest) < loop.index("renderStep(")
    assert "mascot.js?v=20261124-film" in PAGE


def test_the_frame_is_hidden_until_its_show_mode_and_takes_no_touch():
    css = PAGE[PAGE.index(".face-film { display: none; }"):][:1800]
    assert "pointer-events: none" in css and "visibility: hidden;" in css
    assert "html.face-film-on .face-film { visibility: visible; }" in css
    assert ".mascot-wrap { opacity: 0; }" in css                            # opacity, not visibility: her hold still works
    assert "html.face-film-on .reply, html.face-film-on .vp-stage { visibility: hidden !important; }" in css
    # 24 Nov evening ("จอ LED ที่หุ่นยังไม่เต็มนะ"): the frame is the whole screen; the face's controls are away under it
    # -- no touch either: an unseen End must not end the call -- until a hold on Emma (peekControls, as under any
    # display) opens the frame's bottom; and the frame's page lays the screen out as a poster round the film's
    # 16:9 stage (html.mirror-film, put on by ?film=1 only)
    assert "top: 0; width: 100vw; height: 100vh;" in css and "--ff-h" not in PAGE
    assert "html.face-film-on .controls { opacity: 0; visibility: hidden; pointer-events: none; }" in css
    assert "html.face-film-on body.controls-peek .controls { opacity: 1; visibility: visible; pointer-events: auto; }" in css
    assert "html.face-film-on body.controls-peek .face-film { clip-path: inset(0 0 15% 0); }" in css
    assert ("if (document.body.classList.contains('emma-staging') || "
            "document.documentElement.classList.contains('face-film-on')) peekControls();") in PAGE
    # the 3D module's own long-press is the one that fires on the robot (seen 24 Nov: the hold opened the menu, no controls)
    assert PAGE.count("document.documentElement.classList.contains('face-film-on')) peekControls();") == 2
    assert "if (new URLSearchParams(location.search).get('film') === '1') document.documentElement.classList.add('mirror-film');" in PAGE
    poster = PAGE[PAGE.index("html.mirror.mirror-film.mirror-show { --ff-y"):PAGE.index("/* the browser held her voice back")]
    assert "--mirror-foot: 22vh" in PAGE                                    # the hall's own band is as it was
    assert "top: calc(var(--ff-y) + 56.25vw" in poster                      # the Thai line sits under the 16:9 film
    # the stage runs from the top edge of the screen (it began at the film's, under a band of the page's own purple)
    assert ".cs-stage { top:" not in poster and ".cs-slide { top: var(--ff-y); }" in poster
    # the logo between lines is whole (16:9, its words are 14-87% of its width) and says the name itself
    assert "body:has(> .cs-stage.cs-idle-on:not(.cs-up)) .mirror-back" in poster
    assert "right: calc(-9 * var(--cu)); top: calc(50vh - 36 * var(--cu)); bottom: auto; height: calc(66.4 * var(--cu));" in poster
    assert "blur" not in poster and "box-shadow" not in poster and "animation" not in poster   # motion/perf rules
    assert "html.mirror.mirror-film" not in PAGE.replace(poster, "").replace("classList.add('mirror-film')", "")
    # the frame's page says when its show mode is on, and the hall's screen is the only one that reports clips
    assert "window.parent.postMessage({ emmaFilm: !!on }, location.origin)" in PAGE
    assert "typeof mirrorFilm !== 'undefined' && mirrorFilm && window.parent !== window" in PAGE
    assert "if (typeof mirrorFilm !== 'undefined' && mirrorFilm) return;      // only the hall's screen" in PAGE


def test_nothing_of_the_film_is_in_the_sales_or_event_pages_paths():
    # the frame opens from `ready.film_ticket` alone, and the ticket is made for the ceremony profile only
    src = (ROOT / "app" / "session.py").read_text(encoding="utf-8")
    assert src.count("film_ticket") == 1
    assert "ceremonyApp = appShell && !mirror && q.get('ceremony') === '1';" in PAGE
