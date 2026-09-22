"""FastAPI entry point: serves the voice UI and bridges it to the chosen provider."""
from __future__ import annotations

import ipaddress
import json
import logging
import secrets
from pathlib import Path
from urllib.parse import quote, urlsplit

from collections import deque
from datetime import datetime

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from app.slide_assets import SlideAssets
from fastapi.staticfiles import StaticFiles

from app import display, turnlog, voices
from app.config import settings
from app.providers import default_voice_for
from app.session import handle_connection

logging.basicConfig(level=settings.log_level)

app = FastAPI(title="Condo Voice Assistant", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ROOT = Path(__file__).resolve().parent.parent
CLIENT_INDEX = ROOT / "client" / "index.html"
DISPLAY_INDEX = ROOT / "client" / "display.html"
HARDWARE_PAGE = ROOT / "client" / "hardware.html"
ROBOT_CONTROL_PAGE = ROOT / "client" / "robot-control.html"
ROBOT_ARM_PAGE = ROOT / "client" / "robot-arm.html"
ROBOT_HUD_PAGE = ROOT / "client" / "robot-hud.html"
ROBOT_DRIVE_PAGE = ROOT / "client" / "robot-joystick.html"
ROBOT_MAPVIEW_PAGE = ROOT / "client" / "robot-map.html"
ROBOT_CONSOLE_PAGE = ROOT / "client" / "robot-console.html"
#: The next-generation call screen (the glass-mascot design). Served at its
#: own URL, deliberately separate from `/` (index.html) so the production
#: gallery page is untouched while this one is finished and signed off.
PREVIEW_PAGE = ROOT / "client" / "voice-preview.html"
CLIENT_ASSETS = ROOT / "client" / "assets"
PLAN_DISPLAY_PROTOCOL = ROOT / "client" / "plan-display-protocol.js"

# Serve the slide images. Mounted only if the folder exists so the app still
# starts on a machine that hasn't imported a deck yet.
_slides_path = Path(settings.slides_dir).expanduser()
if not _slides_path.is_absolute():
    _slides_path = ROOT / _slides_path
if _slides_path.is_dir():
    app.mount("/slides", SlideAssets(directory=str(_slides_path)), name="slides")

# The mascot art the preview page (voice-preview.html) references. Images
# only — a read-only folder of PNGs — so a plain static mount is safe here,
# same as the slides mount above.
if CLIENT_ASSETS.is_dir():
    app.mount("/assets", StaticFiles(directory=str(CLIENT_ASSETS)), name="assets")

_MODEL_FOR = {"gemini": lambda: settings.gemini_model, "openai": lambda: settings.openai_model}

#: Hosts that mean "this machine only". Anything else is reachable from the
#: network the machine is on.
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

#: The startup deck warm-up, held so it isn't garbage-collected mid-walk.
_warm_task = None


def warn_if_open_to_the_network(log: logging.Logger) -> str:
    """Say out loud when the server is on the network with no WS_TOKEN.

    The gate defaults to off (`WS_TOKEN=""`) while the exposure defaults to
    **on** (`HOST=0.0.0.0`, in config.py and in .env.example both), so the
    shipped configuration is the unprotected one. That is the same shape as
    the `script_approved` bug — a safety mechanism whose default is the
    unsafe side — and it matters more here, because the tools on this server
    open programs and press keys on the machine running it.

    A warning rather than a refusal to start: a showroom that will not boot
    twenty minutes before opening is a worse outcome than one that boots
    loudly. Returned as a string so a test can read the decision instead of
    the log handler.
    """
    host = (settings.host or "").strip()
    if host in _LOOPBACK_HOSTS:
        return "localhost"
    if settings.ws_token:
        return "protected"
    log.warning(
        "SECURITY: HOST=%s makes this server reachable from the network and "
        "WS_TOKEN is empty, so anything on it can open a session — and the "
        "session can open programs, press keys and drive the screen on this "
        "machine. Set WS_TOKEN in .env to a long random value and open every "
        "page as ...?token=<value>, or set HOST=127.0.0.1.", host,
    )
    return "open"


@app.on_event("startup")
async def _log_effective_config() -> None:
    """Print the settings that actually decide whether this thing works.

    Voice bugs are mostly silent — a mis-set VAD sensitivity just means the
    robot never answers, with nothing in the log to explain it. Showing the
    effective values on boot turns that into a five-second diagnosis.
    """
    log = logging.getLogger("condo_voice")
    # First line of the banner on purpose: everything below is about whether
    # the assistant works, and this one is about who gets to use it.
    warn_if_open_to_the_network(log)
    provider = settings.provider
    log.info("provider=%s model=%s voice=%s key=%s",
             provider, _MODEL_FOR.get(provider, lambda: "?")(),
             settings.gemini_voice if provider == "gemini" else settings.openai_voice,
             "set" if settings.api_key_for(provider) else "MISSING")
    if provider == "gemini":
        if settings.vad_mode == "local":
            # Exercise the factory now rather than on the first call: a
            # missing model must be one line at boot, not a surprise at the
            # first hello. for_session() logs its own fallback warning.
            from app import vad_gate

            probe = vad_gate.for_session()
            log.info("vad: LOCAL (silero) silence=%dms prefix=%dms — silence "
                     "is not streamed upstream%s",
                     settings.vad_silence_ms, settings.vad_prefix_padding_ms,
                     "" if probe else " [FELL BACK TO GEMINI — see warning]")
        log.info("vad: start=%s end=%s silence=%dms prefix=%dms | thinking_budget=%s | half_duplex=%s",
                 settings.vad_start_sensitivity, settings.vad_end_sensitivity,
                 settings.vad_silence_ms, settings.vad_prefix_padding_ms,
                 settings.gemini_thinking_budget, settings.half_duplex)

    from app import tools
    from app.tools import broadlink_ir

    names = [t.name for t in tools.load_tools()]
    log.info("tools: %s", ", ".join(names) if names else "(none)")
    if settings.multi_session:
        # Said out loud, because from a tester's chair "the robot can't do X"
        # and "X is stripped in this mode" look identical. Names what was
        # asked for and refused, so the .env line explains itself at boot.
        from app.config import MULTI_SESSION_SAFE

        asked = {g.strip() for g in settings.tool_groups.split(",") if g.strip()}
        stripped = asked - MULTI_SESSION_SAFE
        log.info(
            "MULTI_SESSION: on — concurrent sessions do not supersede each "
            "other, machine screens are dark, tool groups limited to %s%s. "
            "Free-tier Gemini caps concurrent Live sessions — expect "
            "connection errors beyond ~3 callers without billing.",
            ",".join(sorted(settings.enabled_tool_groups() or set())) or "(none)",
            (" (stripped from TOOL_GROUPS: %s)" % ",".join(sorted(stripped)))
            if stripped else "",
        )
    # Two ways to end up without a tool you thought you had, both of which
    # look identical from the conversation ("เอมม่าทำสิ่งนั้นไม่ได้ค่ะ") and
    # neither of which said anything at boot until now.
    stray = tools.unknown_groups(settings.enabled_tool_groups())
    if stray:
        log.warning(
            "TOOL_GROUPS names %s, which are not tool groups — check the "
            "spelling. Valid: %s", ", ".join(sorted(stray)),
            ", ".join(sorted(set(tools._TOOL_MODULES.values()))),
        )
    if settings.units_sample and "show_unit" not in names:
        log.warning(
            "UNITS_SAMPLE is on but the `units` group is not loaded, so "
            "show_unit does not exist and asking for a room will be refused. "
            "Add `units` to TOOL_GROUPS in .env, e.g. TOOL_GROUPS=%s",
            ",".join(sorted((settings.enabled_tool_groups() or set()) | {"units"})),
        )
    ir = broadlink_ir.status()
    log.info("infrared: %s", ir)
    if settings.robot_enabled:
        # Said at boot because the movement tools fail *quietly* by design —
        # they succeed and report `hardware: "mock"`. Without this line, a
        # gallery would find out the robot never moves when a guest asks it to.
        from app.tools import robot_link

        from app import robot_chassis

        # Two different things to be waiting for, and saying the wrong one is
        # worse than saying nothing: an operator who reads "waiting for
        # robot_ready" on a machine driving the chassis directly would go
        # hunting for an Android app that is never going to report in.
        waiting = ("the chassis at %s to answer" % settings.robot_chassis_url
                   if robot_chassis.configured()
                   else "the app to send robot_ready")
        log.info("robot: enabled — waiting for %s; "
                 "until then every movement runs in mock mode (%s)",
                 waiting, robot_link.status())
    if settings.canva_url and not settings.multi_session:
        # Actually open it, rather than checking that the *package* imports.
        # The old check passed while Chromium itself was missing, so the
        # window silently never appeared and nothing said why. Opening it now
        # also means the first slide change doesn't pay the cold start.
        from app.tools import canva_display

        ok, detail = await canva_display.self_check()
        (log.info if ok else log.error)("canva display: %s", detail)
        if ok and settings.canva_warm_deck:
            # A background task, not an await: walking the deck takes about
            # half a minute and boot must not wait for it. `open_and_warm`
            # explains why it happens here instead of on the first slide.
            # Reference kept — a bare create_task can be collected mid-walk.
            import asyncio

            global _warm_task
            _warm_task = asyncio.create_task(canva_display.open_and_warm())

    # The live inventory link: same rule as everything below that fails
    # quietly — one line of truth at boot.
    from app.tools import units as _units

    import asyncio

    ok_inv, detail_inv = await asyncio.to_thread(_units.live_probe)
    (log.info if ok_inv else log.warning)("%s", detail_inv)

    # Semantic search: on or off, said out loud at boot.
    #
    # It fails *quietly* by design — a gallery that answers slightly worse
    # beats one that won't start — and the cost of that is nobody noticing it
    # has been off the whole time. It was, for the life of the project, and
    # the reason was a per-minute rate limit rather than anything in the code.
    # No API call here: checking would itself spend a request against the
    # limit that causes the problem.
    from pathlib import Path as _Path

    cache = _Path(settings.slides_dir).expanduser() / "embeddings.npz"
    if cache.exists():
        from app.tools.retrieval import choose_provider, embedding_signature

        log.info("semantic search: ready (%s, %s)",
                 embedding_signature(choose_provider()), cache)
    else:
        log.warning(
            "semantic search: OFF — no %s yet, so search is keyword-only and "
            "cross-language matching will not work (a Chinese or Russian "
            "question finds nothing). Build it once with: "
            "python scripts/build_embeddings.py  (works offline with "
            "EMBED_PROVIDER=local if the API is unavailable)", cache,
        )
    if settings.ir_enabled and names and not ir["usable"]:
        missing = []
        if not ir["package_installed"]:
            missing.append("`pip install broadlink`")
        if not ir["device_file"]:
            missing.append(f"{settings.broadlink_device_file} (hub MAC/IP)")
        if not ir["codes_file"]:
            missing.append(f"{settings.ir_codes_file} (learned IR codes)")
        log.warning(
            "Smart-home tools will run in MOCK mode — nothing will physically "
            "switch. Missing: %s", "; ".join(missing),
        )
    # Deliberately at function level, not inside the infrared block above.
    # It was indented one step too far, so the warning about "the assistant
    # may never respond" only ever printed on machines whose *infrared hub*
    # was also broken — the one setting whose symptom is total silence, hidden
    # behind a condition that has nothing to do with it.
    if settings.vad_start_sensitivity.upper() == "LOW":
        log.warning("VAD_START_SENSITIVITY=LOW detects speech LESS often — "
                    "the assistant may never respond. Use HIGH unless you know why.")


@app.on_event("startup")
async def _rearm_reminders() -> None:
    """A reminder must survive a restart, so the clock must too.

    Guarded by the enabled groups: importing the module registers its tools,
    and the condo profile must not gain reminder tools because the server
    happened to restart with something pending in the file.
    """
    from app import tools as tools_pkg

    if "app.tools.reminders" not in tools_pkg._modules_to_load(
        settings.enabled_tool_groups()
    ):
        return
    from app.tools import reminders

    if reminders.pending():
        reminders.ensure_watcher()
        logging.getLogger("condo_voice").info(
            "reminders: %d pending — watcher armed", len(reminders.pending()))


def _quiet_connection_resets(loop) -> None:
    """Stop Windows socket teardown from impersonating a crash.

    Every time a browser tab refreshes or closes, the proactor loop tries to
    shut down a TCP socket the other side has already abandoned, and Windows
    answers WinError 10054. asyncio logs that as ERROR with a full traceback
    — so the operator's console shows what looks like the robot failing, on
    every single page reload, forever. It has been mistaken for a real fault
    in this project more than once.

    Only ConnectionResetError is swallowed, and only from the loop's
    exception handler: a reset on a *live* session still surfaces through
    the read path, where the session code already treats it as a disconnect.
    Everything else goes to the default handler untouched.
    """
    def handler(loop, context):
        if isinstance(context.get("exception"), ConnectionResetError):
            return
        loop.default_exception_handler(context)

    loop.set_exception_handler(handler)


@app.on_event("startup")
async def _no_teardown_noise() -> None:
    import asyncio

    _quiet_connection_resets(asyncio.get_running_loop())


@app.on_event("startup")
async def _warm_search() -> None:
    """Load the slide/document search model at boot, not inside a customer's
    first question. Measured 2026-09-14: `search_condo_info` cold-starts the
    local MiniLM encoder for ~7.5s the first time, and on a server already
    busy with a live audio session that lands past a tool call's 15s ceiling
    and reads as "lookup — timeout" on the page. Warming it in the background
    moves that one-time cost to a moment nobody is waiting on."""
    import asyncio

    async def go() -> None:
        log = logging.getLogger("condo_voice")
        try:
            from app.tools.knowledge import search_condo_info

            await asyncio.to_thread(search_condo_info, "warm up")
            log.info("search: embedding model warmed")
        except Exception:
            log.debug("search warm-up skipped", exc_info=True)

    asyncio.create_task(go())


@app.on_event("startup")
async def _watch_the_door() -> None:
    """Open the entrance camera, if this machine has one and wants it.

    `FACE_ENABLED` is off by default and the showroom must stay that way:
    every face that gets through opens a session, which is the one thing
    the wake word exists to avoid. `greeter.start()` checks the switch
    itself so nothing here has to be kept in step with it.
    """
    from app import greeter

    greeter.start()


@app.on_event("startup")
async def _reach_the_chassis() -> None:
    """Start polling the robot's navigation board, if this machine has one.

    `ROBOT_CHASSIS_URL` is empty by default and every machine before
    2026-09-10 ran that way, so this is a no-op unless somebody has pointed
    it at a chassis. `robot_chassis.start()` checks the switch itself, the
    same as `greeter.start()`, so nothing here has to be kept in step.

    It has to be a background poller rather than something the tools call:
    the REST API has no callback, so an arrival is only noticed by asking,
    and nobody is asking while the model waits for a turn that never comes.
    """
    from app import robot_chassis

    robot_chassis.start()


@app.on_event("shutdown")
async def _let_go_of_the_chassis() -> None:
    """Stop polling and close the socket. Deliberately does not cancel a walk
    in progress — the server shutting down is not a reason to move a robot,
    and a robot that must stop has a physical button for it."""
    from app import robot_chassis

    await robot_chassis.stop()


@app.on_event("shutdown")
async def _stop_watching_the_door() -> None:
    """Let go of the camera. A held lens with nothing reading it keeps the
    light on and stops the enrolment station opening the same device."""
    from app import greeter

    await greeter.stop()


@app.on_event("shutdown")
async def _close_web_stage() -> None:
    """A kiosk window outliving the server it was driven by is a fullscreen
    page with no address bar and nothing left to close it."""
    from app.tools import webstage

    await webstage.shutdown()


@app.on_event("shutdown")
async def _close_canva_display() -> None:
    """No-op if the window was never opened (CANVA_URL unset)."""
    from app.tools import canva_display

    await canva_display.shutdown()


#: The kiosk problem: a tab that loaded yesterday's HTML keeps yesterday's
#: JS, and "reload the page" quietly serves it from cache. The pages are a
#: few KB; re-fetching them every load costs nothing and ends the class of
#: bug where a feature exists on the server and not in the open tab.
_NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}


@app.get("/")
async def serve_client() -> FileResponse:
    return FileResponse(CLIENT_INDEX, headers=_NO_CACHE)


@app.get("/preview")
async def serve_preview() -> FileResponse:
    #: The glass-mascot call screen, a work in progress. Same `/ws`, `/voices`
    #: and `/health` as `/`, so it is a real call — just wearing the new
    #: design. Kept off `/` until the owner signs it off.
    return FileResponse(PREVIEW_PAGE, headers=_NO_CACHE)


@app.get("/plan-display-protocol.js")
async def serve_plan_display_protocol() -> FileResponse:
    # Both call pages must receive the same verifier/ACK implementation.
    return FileResponse(PLAN_DISPLAY_PROTOCOL, media_type="text/javascript", headers=_NO_CACHE)


def _same_origin_loopback_websocket(websocket: WebSocket) -> bool:
    """Allow the local Emma page to dial without exposing WS_TOKEN in its URL.

    The TCP peer, page Origin and request Host must all be the same loopback
    origin. An internet page can ask a browser to reach localhost, but its
    Origin will not match this WebSocket Host and is rejected. LAN clients and
    non-browser clients still need the configured token.
    """
    client = websocket.client
    if client is None:
        return False
    try:
        if not ipaddress.ip_address(client.host).is_loopback:
            return False
    except ValueError:
        return False

    origin = websocket.headers.get("origin", "")
    host = websocket.headers.get("host", "")
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    try:
        origin_is_loopback = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        origin_is_loopback = parsed.hostname.casefold() == "localhost"
    return origin_is_loopback and parsed.netloc.casefold() == host.casefold()


async def _reject_unauthorized(websocket: WebSocket, token: str | None) -> bool:
    """True = this socket was rejected and closed.

    Applied to every WebSocket the moment WS_TOKEN is set, except a tokenless
    connection from the same loopback page. The same server that answers
    questions also opens programs and presses keys, so "some socket on the
    LAN" must never be enough to reach it. Accept-then-close because a
    handshake-level refusal shows browsers nothing actionable — this way the
    page can display *why* and stand down instead of redialing.
    """
    if not settings.ws_token or (not token and _same_origin_loopback_websocket(websocket)):
        return False
    # Encoded, not compared as str: `compare_digest` refuses non-ASCII text,
    # and a token someone pastes out of a password manager can contain
    # anything at all — a TypeError here would read as "the gate is broken".
    if token is not None and secrets.compare_digest(
            token.encode("utf-8"), settings.ws_token.encode("utf-8")):
        return False
    await websocket.accept()
    await websocket.send_text(json.dumps({
        "type": "error", "code": "unauthorized",
        "message": "ต้องใส่ token — เปิดหน้าด้วย ?token=... ให้ตรงกับ WS_TOKEN ใน .env",
    }))
    await websocket.close()
    return True


def _wake_ready() -> bool:
    from app import wake

    return settings.wake_enabled and wake.available()


@app.get("/unit_price")
async def unit_price(room: str, token: str | None = None):
    """Tap-to-reveal prices for the unit card — both nationalities.

    The numbers go straight from the sales DB to the browser on a human's
    tap; they never enter a tool result, so the model still cannot read a
    price aloud (the standing "ห้ามโชว์ราคา" order is about the model and
    the idle screen, and both stay priceless). Guarded by WS_TOKEN exactly
    like the sockets: this endpoint hands out money figures, and "some
    browser on the LAN" must not be enough.
    """
    import asyncio
    import hmac

    from fastapi.responses import JSONResponse

    if settings.ws_token and not hmac.compare_digest(
            (token or "").encode(), settings.ws_token.encode()):
        return JSONResponse({"error": "unauthorized"}, status_code=403)
    from app.tools import units as units_mod

    pair = await asyncio.to_thread(units_mod.price_pair, room)
    if pair is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return pair


@app.get("/verified-plan/{sha256}")
async def verified_plan(sha256: str, token: str | None = None):
    """Serve the exact, short-lived image bytes checked by the plan policy."""
    import hmac
    import re
    from fastapi import Response

    if settings.ws_token and not hmac.compare_digest(
            (token or "").encode(), settings.ws_token.encode()):
        return Response(status_code=403)
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        return Response(status_code=404)
    from app.tools import units

    body = units.verified_plan_body(sha256)
    if body is None:
        return Response(status_code=404)
    return Response(content=body, media_type="image/webp",
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@app.get("/health")
async def health() -> dict:
    from app.tools import robot_link

    provider = settings.provider
    return {
        "ok": True,
        "project": settings.project_name,
        "provider": provider,
        "model": _MODEL_FOR.get(provider, lambda: "unknown")(),
        "free_tier": provider == "gemini",
        "default_voice": default_voice_for(provider),
        # Never return the key itself — just whether one is configured, so the
        # UI can show a setup message instead of failing mid-conversation.
        "api_key_configured": bool(settings.api_key_for(provider)),
        # Whether the page should open standby ears instead of waiting for
        # the Start button. `ready` is the honest half: enabled-but-missing-
        # model must not put the UI in a mode the server cannot serve.
        # `ready` means "a keyword will be listened for"; `standby` means
        # "hold the socket open anyway" — true when the camera is the thing
        # that will ring it. Two flags because the page does two different
        # things: the second one must not open a microphone.
        "wake": {"enabled": settings.wake_enabled, "ready": _wake_ready(),
                 "standby": _wake_ready() or settings.face_enabled},
        "auto_connect": settings.auto_connect,
        # How long the page keeps the finished conversation readable after
        # the line sleeps. Served rather than hard-coded in the page so the
        # showroom and the owner's desk can hold different answers to "who
        # else can walk up to this screen".
        "transcript_keep_min": settings.transcript_keep_min,
        "mic": {"boost": settings.mic_boost,
                # The call's own boost — standby keeps far-field reach, the
                # call listens to the person at the desk. Same value unless
                # CALL_MIC_BOOST is set.
                "boost_call": settings.call_mic_boost,
                "noise_suppression": settings.mic_noise_suppression,
                # The two noise amplifiers, separately switchable — see
                # config.py for the measured reason each earned a switch.
                "compressor": settings.mic_compressor,
                "agc": settings.mic_agc},
        "robot": robot_link.status(),
        "providers_configured": {
            "gemini": bool(settings.gemini_api_key),
            "openai": bool(settings.openai_api_key),
        },
    }


@app.get("/voices")
async def list_voices(provider: str | None = None) -> dict:
    name = provider or settings.provider
    return {
        "provider": name,
        "voices": voices.as_dicts(name),
        "default": default_voice_for(name),
    }


@app.get("/display")
async def serve_display() -> FileResponse:
    """Fullscreen slide view — the robot's chest screen, or a second window."""
    return FileResponse(DISPLAY_INDEX, headers=_NO_CACHE)


@app.get("/hardware")
async def serve_hardware() -> FileResponse:
    """A bench for the microphone and camera of whatever device opens it.

    No token: it carries no controls and reaches nothing. Every other page
    here can drive this machine — open programs, move slides, move a robot —
    which is what `WS_TOKEN` guards. This one only looks at the microphone
    and camera of the browser it is running in, sends nothing anywhere, and
    is most needed exactly when the token is the thing that is wrong.
    """
    return FileResponse(HARDWARE_PAGE, headers=_NO_CACHE)


#: The last few hardware summaries any device sent in. Memory only, on
#: purpose. The page these come from is holding a live microphone, and a file
#: of diagnostics collected in a showroom is exactly the shape of thing that
#: becomes a retention question nobody ever decided — the mistake `data/logs/`
#: had to be given `TURN_LOG_KEEP_DAYS` to undo. These die with the process,
#: which is the whole useful life of one debugging session anyway.
_HARDWARE_REPORTS: deque[dict] = deque(maxlen=8)

#: A summary is a few hundred characters of device names and levels. The cap
#: is there so an endpoint that takes a POST without a token cannot be used
#: to park a megabyte in this process's memory.
_HARDWARE_REPORT_LIMIT = 4000


@app.post("/hardware/report")
async def take_hardware_report(request: Request) -> dict:
    """Accept one device's own summary of its microphone and camera.

    **Text only, and that is the whole design.** A browser can only measure
    the devices of the machine it runs on, so the robot's own readings are
    unreachable from the sales desk — which is where the person reading them
    is standing. This carries the verdicts across: device names, levels, frame
    rates. It never carries audio or video, and `tests/test_hardware_page.py`
    holds the page to that.
    """
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": "expected JSON"}
    if not isinstance(body, dict):
        return {"ok": False, "error": "expected an object"}
    report = str(body.get("report") or "")[:_HARDWARE_REPORT_LIMIT]
    if not report.strip():
        return {"ok": False, "error": "empty report"}
    _HARDWARE_REPORTS.appendleft({
        "at": datetime.now().strftime("%H:%M:%S"),
        "label": str(body.get("label") or "")[:80],
        "from": request.client.host if request.client else "?",
        "report": report,
    })
    return {"ok": True, "kept": len(_HARDWARE_REPORTS)}


@app.get("/hardware/reports")
async def read_hardware_reports() -> dict:
    """What other devices have sent, newest first."""
    return {"reports": list(_HARDWARE_REPORTS)}


def _authorized(token: str | None) -> bool:
    """The same gate `_reject_unauthorized` applies to every socket.

    Written out again rather than shared, because that one's job is to accept
    a WebSocket and explain itself before closing. The comparison is the part
    that matters and it is identical: encoded before `compare_digest`, which
    refuses non-ASCII and would otherwise raise on a token pasted out of a
    password manager — an exception here reads as "the gate is broken".
    """
    if not settings.ws_token:
        return True
    return token is not None and secrets.compare_digest(
        token.encode("utf-8"), settings.ws_token.encode("utf-8"))


#: How far one press of the manual control may move the robot. Server-side,
#: and not negotiable by the page: a control surface that lets the client name
#: the distance is one typo away from sending a robot across a gallery, and
#: the client is a web page anyone on the LAN can open.
_NUDGE_METRES = 0.3
_NUDGE_RADIANS = 0.2618          # 15 degrees


@app.get("/robot")
async def serve_robot_control() -> FileResponse:
    """Manual control, for proving the robot answers at all.

    This one *does* need the token: everything on it moves a real machine.
    """
    return FileResponse(ROBOT_CONTROL_PAGE, headers=_NO_CACHE)


@app.get("/robot/state")
async def robot_state(token: str = "") -> dict:
    """Read the chassis right now, not from the poll cache.

    Somebody standing next to a moving robot with a hand on a stop button
    needs this second's answer, not the one from up to `ROBOT_CHASSIS_POLL_S`
    ago.
    """
    from app import robot_chassis

    if not _authorized(token):
        return {"ok": False, "error": "unauthorized"}
    if not robot_chassis.configured():
        return {"ok": False, "error": "ROBOT_CHASSIS_URL ยังไม่ได้ตั้ง"}
    try:
        return {"ok": True, "state": await robot_chassis.live_state()}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@app.get("/robot/map")
async def robot_map(token: str = "") -> dict:
    """The occupancy grid, for a page that lets somebody click where to go.

    Cells go out as the board's own bytes, base64, one per cell, row-major
    from the origin (row 0 is the *lowest* y, so a canvas has to flip).
    world_x = origin_x + (col + 0.5) * resolution, likewise y. What a byte
    means is not decided here — see `robot_chassis.parse_explore_map`.
    """
    import base64

    from app import robot_chassis

    if not _authorized(token):
        return {"ok": False, "error": "unauthorized"}
    if not robot_chassis.configured():
        return {"ok": False, "error": "ROBOT_CHASSIS_URL ยังไม่ได้ตั้ง"}
    try:
        grid = await robot_chassis.explore_map()
    except ValueError as exc:
        return {"ok": False, "error": f"แมพอ่านไม่ออก: {exc}"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if grid is None:
        return {"ok": False, "error": "หุ่นยังไม่มีแมพ"}
    return {"ok": True, "map": {
        "origin_x": grid["origin_x"], "origin_y": grid["origin_y"],
        "width": grid["width"], "height": grid["height"],
        "resolution": grid["resolution"],
        "cells_b64": base64.b64encode(grid["cells"]).decode("ascii"),
    }}


#: Most lidar points a page gets per fetch unless it asks for fewer. The full
#: frame is ~1,600 points; a radar redrawn a few times a second does not need
#: them all, and the tunnel to the chassis is a `nc` relay on the robot.
_LASERSCAN_MAX_POINTS = 400


@app.get("/robot/laserscan")
async def robot_laserscan(token: str = "", max: int = _LASERSCAN_MAX_POINTS) -> dict:
    """The current lidar frame, for a radar view on the driving page.

    `angle` is radians from the robot's front (counter-clockwise positive),
    `distance` metres, `valid` the board's own verdict on the return. `pose`
    is where the robot was when the frame was taken. Read-only.
    """
    from app import robot_chassis

    if not _authorized(token):
        return {"ok": False, "error": "unauthorized"}
    if not robot_chassis.configured():
        return {"ok": False, "error": "ROBOT_CHASSIS_URL ยังไม่ได้ตั้ง"}
    limit = max if 0 < max <= _LASERSCAN_MAX_POINTS else _LASERSCAN_MAX_POINTS
    try:
        scan = await robot_chassis.laserscan(max_points=limit)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "points": scan["points"], "pose": scan["pose"],
            "total": scan["total"], "valid": scan["valid"],
            "clearance": scan["clearance"],
            "min_clearance": settings.robot_drive_min_clearance_m}


def _coordinate(body: dict, key: str) -> float | None:
    """A number from the request, or None. Strings of digits count; anything
    else is None rather than an exception, so the caller can say *which*."""
    value = body.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


@app.post("/robot/command")
async def robot_command(request: Request) -> dict:
    """One bounded step, or stop.

    Stop is first in the dispatch and is the only command that runs while
    motion is locked — a lock that also disabled the brake would be a worse
    thing than no lock. Everything else is a fixed increment computed from
    the pose at the moment of the press, so the failure mode of a dropped
    connection is a robot that finishes one short step and waits.
    """
    from app import robot_chassis

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if not robot_chassis.configured():
        return {"ok": False, "error": "ROBOT_CHASSIS_URL ยังไม่ได้ตั้ง"}

    if not _authorized(body.get("token") or request.query_params.get("token")):
        return {"ok": False, "error": "unauthorized"}
    action = str(body.get("action") or "")
    try:
        if action == "stop":
            # Disarms a held joystick too; a plain cancel would leave its
            # deadman to cancel again, which is harmless but says the wrong
            # thing in the log.
            await robot_chassis.drive_stop(reason="stop button")
            return {"ok": True, "did": "stop"}
        if action == "relocalize":
            # Before the lock: `dock` and `static` do not move the robot,
            # and a robot that does not know where it is needs this most
            # while everything else is still locked. `rotate` moves, and
            # `relocalize` itself refuses it while locked.
            mode = str(body.get("mode") or "dock")
            report = await robot_chassis.relocalize(mode)
            turnlog.record("robot_manual_command", action="relocalize")
            return {"ok": True, "did": "relocalize", **report}
        if action == "save_poi":
            # Also before the lock: writing a map point moves nothing. The
            # name is data the owner typed, logged by count not content.
            saved = await robot_chassis.save_poi(str(body.get("name") or ""))
            turnlog.record("robot_manual_command", action="save_poi")
            return {"ok": True, "did": "save_poi", **saved}
        if action == "save_map":
            report = await robot_chassis.save_map()
            turnlog.record("robot_manual_command", action="save_map")
            return {"ok": True, "did": "save_map", **report}
        if action == "dock_check":
            return {"ok": True, "did": "dock_check", **(await robot_chassis.dock_check())}
        if action == "register_dock":
            # Moves nothing; fixes where `home` will go. Before the lock for
            # the same reason relocalize is.
            report = await robot_chassis.register_dock()
            turnlog.record("robot_manual_command", action="register_dock")
            return {"ok": True, "did": "register_dock", **report}
        if not settings.robot_chassis_motion_enabled:
            return {"ok": False, "error": "motion_locked",
                    "hint": "ตั้ง ROBOT_CHASSIS_MOTION_ENABLED=true ใน .env แล้วรีสตาร์ต"}
        if action == "drive":
            # Hold-to-drive: the page repeats this every ~150 ms; only the
            # first of a burst is worth a log line.
            started = await robot_chassis.drive(str(body.get("direction") or ""))
            if started:
                turnlog.record("robot_manual_command", action="drive")
            return {"ok": True, "did": "drive"}
        if action == "forward":
            await robot_chassis.nudge(forward_m=_NUDGE_METRES)
        elif action == "back":
            await robot_chassis.nudge(forward_m=-_NUDGE_METRES)
        elif action == "left":
            await robot_chassis.nudge(turn_rad=_NUDGE_RADIANS)
        elif action == "right":
            await robot_chassis.nudge(turn_rad=-_NUDGE_RADIANS)
        elif action == "home":
            await robot_chassis.go_home()
        elif action == "undock":
            report = await robot_chassis.undock()
            if report["sent"] != "ok":
                return {"ok": False, "error": "ออกจากแท่นไม่ได้ — " + report["sent"]}
            turnlog.record("robot_manual_command", action="undock")
            return {"ok": True, "did": "undock", **report}
        elif action == "goto":
            place = str(body.get("place") or "")
            x, y = _coordinate(body, "x"), _coordinate(body, "y")
            if place:
                # A named point from the robot's own map — the only kind the
                # voice path can ask for, and one the client cannot invent.
                sent = await robot_chassis.send("move_to_point", place=place)
            elif x is not None and y is not None:
                # A clicked coordinate. The client chose it, so the server
                # checks it is on the map before the motors hear about it.
                try:
                    sent = await robot_chassis.goto_xy(x, y)
                except ValueError as exc:
                    return {"ok": False, "error": str(exc)}
            else:
                return {"ok": False, "error": "goto ต้องมี place หรือ x,y"}
            if sent != "ok":
                return {"ok": False, "error": "ไปจุดนี้ไม่ได้ — " + sent}
        else:
            return {"ok": False, "error": f"ไม่รู้จักคำสั่ง {action!r}"}
    except PermissionError:
        return {"ok": False, "error": "motion_locked"}
    except robot_chassis.ObstacleError as exc:
        # A machine-readable shape, because the page shows this one as a
        # warning in the radar rather than as a line of text.
        return {"ok": False, "error": "obstacle", "hint": str(exc),
                "blocked": {"direction": exc.direction,
                            "distance": round(exc.distance, 3),
                            "limit": exc.limit}}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    turnlog.record("robot_manual_command", action=action)
    return {"ok": True, "did": action}


@app.get("/arm")
async def serve_arm_control() -> FileResponse:
    """Manual control of the upper body. A desk instrument, not a feature.

    Deliberately a separate page from `/robot`. The chassis answers questions
    about itself and can be told to stop; this board does neither, and putting
    both on one screen would put a button that cannot stop an arm next to one
    that really does stop the wheels.
    """
    return FileResponse(ROBOT_ARM_PAGE, headers=_NO_CACHE)


@app.get("/hud")
async def serve_robot_hud() -> FileResponse:
    """One screen showing what is known about the robot, in its own shape.

    Read-only. The controls stay at `/robot` and `/arm` and this links to
    them, because two copies of a stop button drift apart and the copy that
    drifts is found by whoever needed it most.
    """
    return FileResponse(ROBOT_HUD_PAGE, headers=_NO_CACHE)


@app.get("/drive")
async def serve_robot_drive() -> FileResponse:
    """Hold-to-step driving. Pages only; every step still goes through
    `/robot/command`, so the distance stays the server's decision."""
    return FileResponse(ROBOT_DRIVE_PAGE, headers=_NO_CACHE)


@app.get("/mapview")
async def serve_robot_mapview() -> FileResponse:
    """The chassis map with click-to-go. Reads `/robot/map`, sends `goto`
    with a coordinate, and the server decides whether that point is on the
    map before the motors hear about it."""
    return FileResponse(ROBOT_MAPVIEW_PAGE, headers=_NO_CACHE)


@app.get("/cam.jpg")
async def robot_camera_frame(token: str = "") -> Response:
    """The robot's latest camera frame, for the cockpit's live view.

    The frames come from the robot's own kiosk page (?cam=1) into
    `robot_camera.feed` — the same feed the greeter reads. A frame older than
    two seconds is refused with 503, never served: a stale JPEG repeated while
    the link is down reads as a still person to anyone watching, which is the
    exact thing the greeter's "a frozen feed is a stopped camera" rule exists
    to prevent. The polling <img> on the page shows its placeholder on the 503.
    """
    import time

    from app import robot_camera

    if not _authorized(token):
        return Response(status_code=401)
    _seq, jpeg, at = robot_camera.feed.latest()
    if not jpeg or (time.monotonic() - at) > 2.0:
        return Response(status_code=503)
    return Response(content=jpeg, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@app.get("/mic/level")
async def mic_level_endpoint(token: str = "") -> dict:
    """The robot kiosk's most recent mic level, for the cockpit meter.

    Calls, wake mode, and the bounded mic test all report only RMS telemetry.
    Fall back to the older standby meter for clients that predate kiosk
    telemetry.  `streaming` is false when neither path sent a recent frame.
    """
    if not _authorized(token):
        return {"ok": False, "error": "unauthorized"}
    from app import robot_camera, wake

    calibration = {
        "near_field_floor": settings.vad_min_rms if settings.vad_mode == "local" else 0.0,
        "capture_boost": settings.call_mic_boost,
        "raw_floor_estimate": (
            round(settings.vad_min_rms / settings.call_mic_boost, 5)
            if (settings.vad_mode == "local" and settings.vad_min_rms > 0
                and settings.call_mic_boost > 0 and not settings.mic_compressor)
            else None
        ),
    }
    robot = robot_camera.feed.mic_level()
    if robot["streaming"]:
        return {"ok": True, **robot,
                "speech_floor": wake.WakeStream.SPEECH,
                "quiet_floor": wake.WakeStream.QUIET, **calibration}
    return {"ok": True, **wake.mic_level(), "source": "standby",
            "device_label": None, **calibration}


@app.post("/mic/control")
async def mic_control(request: Request) -> dict:
    """Ask the robot kiosk for one five-second level-only microphone test."""
    from app import robot_camera

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if not _authorized(body.get("token") or request.query_params.get("token")):
        return {"ok": False, "error": "unauthorized"}
    if body.get("action") != "probe":
        return {"ok": False, "error": "ไม่รู้จักคำสั่งไมค์"}
    if robot_camera.feed.senders < 1:
        return {"ok": False, "error": "หน้า Emma บนหุ่นยังไม่เชื่อมต่อ"}
    seq = robot_camera.feed.set_control(mic_probe_s=5)
    return {"ok": True, "did": "probe", "seconds": 5, "seq": seq}


@app.post("/cam/control")
async def cam_control(request: Request) -> dict:
    """Adjust the robot camera from the cockpit (currently zoom).

    The camera track lives on the robot's own page, not here, so this only
    records the wish; `/ws/camera` relays it to the page, which applies it
    with `applyConstraints`. Zoom is the one setting this camera actually
    exposes (1..4x, measured) — the FOV cannot be widened past the lens.
    """
    from app import robot_camera

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if not _authorized(body.get("token") or request.query_params.get("token")):
        return {"ok": False, "error": "unauthorized"}
    ctl: dict = {}
    if "zoom" in body:
        try:
            ctl["zoom"] = max(1.0, min(8.0, float(body["zoom"])))
        except (TypeError, ValueError):
            pass
    if not ctl:
        return {"ok": False, "error": "no control"}
    seq = robot_camera.feed.set_control(**ctl)
    return {"ok": True, "control": ctl, "seq": seq, "senders": robot_camera.feed.senders}


@app.get("/console")
async def serve_robot_console(request: Request, token: str = "") -> Response:
    """Verified robot controls and live readings on one test-session screen.

    Composes the existing chassis, arm, camera and microphone endpoints with
    a narrow Android display controller. The two stop buttons stay distinct
    on purpose — one is a chassis cancel that has been seen to work, the
    other writes a frame nobody has watched stop a joint — and merging them
    would let the proven one lend its credibility to the unproven one.
    A direct navigation from this same computer may bootstrap the token.  A
    cross-site fetch may not: without the Sec-Fetch-Site check, any webpage
    open on the PC could read the redirect target and steal the LAN control
    token through the permissive CORS middleware.
    """
    client_host = request.client.host if request.client else ""
    fetch_site = request.headers.get("sec-fetch-site", "")
    local_navigation = (
        client_host in _LOOPBACK_HOSTS
        and fetch_site in {"", "none", "same-origin"}
    )
    if not token and settings.ws_token and local_navigation:
        return RedirectResponse(
            "/console?token=" + quote(settings.ws_token, safe=""),
            status_code=302,
            headers=_NO_CACHE,
        )
    return FileResponse(ROBOT_CONSOLE_PAGE, headers=_NO_CACHE)


@app.get("/android/state")
async def robot_android_state(token: str = "") -> dict:
    """Android hardware state used by the all-controls test console."""
    from app import robot_android

    if not _authorized(token):
        return {"ok": False, "error": "unauthorized"}
    try:
        return {"ok": True, "state": await robot_android.state()}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@app.post("/android/command")
async def robot_android_command(request: Request) -> dict:
    """Allow-listed screen controls; never accepts a shell command or path."""
    from app import robot_android

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if not _authorized(body.get("token") or request.query_params.get("token")):
        return {"ok": False, "error": "unauthorized"}
    action = str(body.get("action") or "")
    try:
        if action == "brightness":
            result = await robot_android.set_brightness(
                str(body.get("display") or ""), int(body.get("percent")))
        elif action == "rotation":
            return {"ok": False, "error": "rotation_not_effective",
                    "hint": "ทดสอบกับหุ่นแล้ว Android รับค่า แต่จอจริงไม่หมุน"}
        else:
            return {"ok": False, "error": "ไม่รู้จักคำสั่ง Android"}
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    turnlog.record("robot_android_command", action=action)
    return {"ok": True, "did": action, "result": result}


@app.get("/arm/state")
async def arm_state(token: str = "") -> dict:
    from app import robot_arm

    if not _authorized(token):
        return {"ok": False, "error": "unauthorized"}
    try:
        return {"ok": True, "state": await robot_arm.state()}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@app.post("/arm/command")
async def arm_command(request: Request) -> dict:
    """One line on the servo board's wire, or a refusal.

    `stop` and `arm` are dispatched before the configuration check for the
    same reason stop comes first on the chassis page: the state of a switch
    must never be the reason somebody cannot disarm a machine. They are also
    the only two that do not touch the port at all.
    """
    from app import robot_arm

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if not _authorized(body.get("token") or request.query_params.get("token")):
        return {"ok": False, "error": "unauthorized"}

    action = str(body.get("action") or "")
    if action == "stop":
        out = await robot_arm.stop()
        note = ("ล็อกแล้ว และเชลล์บนหุ่นรายงานว่าเขียน #STOP สำเร็จ — "
                if out["stop_write_ok"] else
                "ล็อกแล้ว แต่การเขียน #STOP ล้มเหลว — ")
        return {"ok": True, "did": "stop",
                "stop_write_ok": out["stop_write_ok"], "error": out["error"],
                "note": note + "ไม่ยืนยันว่าเฟรมถึงบอร์ดหรือแขนหยุด "
                               "ตัวหยุดที่แน่นอนคือไฟเลี้ยงเซอร์โว"}
    if action == "group" and not settings.robot_arm_groups_enabled:
        # Ahead of the configuration and port checks so the reason reported is
        # the one that will still be true after the port comes back.
        return {"ok": False, "error": "groups_locked",
                "hint": "กลุ่มท่าขยับช่องไหนไกลแค่ไหนยังไม่มีใครรู้ — "
                        "ตั้ง ROBOT_ARM_GROUPS_ENABLED=true ใน .env แล้วรีสตาร์ต"}
    if action == "arm":
        robot_arm.arm()
        return {"ok": True, "did": "arm"}

    if not robot_arm.configured():
        return {"ok": False, "error": "arm_not_configured",
                "hint": "ตั้ง ROBOT_ARM_ENABLED=true และ ROBOT_ARM_PORT ใน .env แล้วรีสตาร์ต"}
    if not await robot_arm.port_present():
        return {"ok": False, "error": "port_missing",
                "hint": f"ไม่พบ {robot_arm.port_description()} บนหุ่นตอนนี้"}

    try:
        if action == "prepare":
            out = await robot_arm.prepare()
            return {"ok": out == "ok", "did": "prepare", "note": out}
        channel = int(body.get("channel") or 0)
        if action == "centre":
            at = await robot_arm.centre(channel)
        elif action in ("up", "down"):
            at = await robot_arm.step(channel, 1 if action == "up" else -1)
        elif action == "group":
            await robot_arm.run_group(int(body.get("group") or 0))
            at = None
        else:
            return {"ok": False, "error": f"ไม่รู้จักคำสั่ง {action!r}"}
    except PermissionError as exc:
        if str(exc) == "motion_locked":
            return {"ok": False, "error": "motion_locked",
                    "hint": "ยังไม่มีวิธีหยุดแขนที่พิสูจน์แล้ว (#STOP ได้ ACK แต่ข้อต่อเคลื่อนต่อ) — "
                            "ตั้ง ROBOT_ARM_MOTION_ENABLED=true ใน .env เมื่อมีคนตัดสินใจแล้ว"}
        return {"ok": False, "error": "disarmed",
                "hint": "กดปุ่มพร้อมสั่งงานก่อน"}
    except (ValueError, LookupError) as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    turnlog.record("robot_arm_command", action=action)
    return {"ok": True, "did": action, "at": at}


async def _hold_standby(websocket: WebSocket, wake) -> None:
    """Keep a browser reachable without listening to it.

    The microphone half of standby is off here: nothing is fed, so nothing
    is heard, and the page is told so rather than left painting "ไมค์กำลัง
    ฟังอยู่" over a socket that will never answer. That exact pairing — a
    reassuring screen over a dead ear — is the bug `WAKE_DEBUG` was built
    to find, and it is not worth repeating in a new place.
    """
    wake.register(websocket)
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return
    except WebSocketDisconnect:
        return
    finally:
        wake.unregister(websocket)


@app.websocket("/ws/wake")
async def ws_wake(websocket: WebSocket, token: str | None = None) -> None:
    """Standby ears. The browser streams PCM16 mono 16kHz here while no
    conversation is running; on hearing the name, the server answers with a
    single {"type": "wake"} and the browser opens the real session on /ws.

    Detection runs server-side rather than in the page because the models
    are Python-only — and the audio still never leaves this machine: this
    process *is* the machine, and nothing upstream is connected until the
    name has been heard. That ordering is the whole point.
    """
    from app import wake

    if await _reject_unauthorized(websocket, token):
        return
    await websocket.accept()
    if not settings.wake_enabled or not wake.available():
        if settings.face_enabled:
            # No keyword, but there is a camera, and the camera needs
            # somewhere to ring. This socket is two things wearing one name
            # — a detector fed by the browser, and the bell the server pulls
            # — and only the first half needs a model. Hanging up here left
            # `events.announce(summon=True)` with nobody to call, so a face
            # at the door could be recognised and then reach nothing.
            await websocket.send_text(json.dumps({"type": "standby_only"}))
            await _hold_standby(websocket, wake)
            return
        # Tell the browser why, then hang up. The Start button still works;
        # wake mode is an upgrade, never a gate.
        await websocket.send_text(json.dumps({
            "type": "wake_unavailable",
            "reason": ("disabled" if not settings.wake_enabled else "no model"),
        }))
        await websocket.close()
        return

    stream = wake.WakeStream()
    await websocket.send_text(json.dumps(
        {"type": "wake_listening", "word": settings.wake_word}
    ))
    # Registered so the server can ring the room itself — a reminder falling
    # due while the line is parked summons a session through this socket.
    wake.register(websocket)
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return
            data = message.get("bytes")
            if not data:
                continue
            hit = stream.feed(data)
            if hit:
                turnlog.record("wake", word=hit)
                await websocket.send_text(json.dumps({"type": "wake", "word": hit}))
                # One detection, one session. The browser drops this socket
                # and takes the mic to /ws; a lingering detector here would
                # hear the whole conversation for no reason.
                await websocket.close()
                return
    except WebSocketDisconnect:
        return
    finally:
        wake.unregister(websocket)


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket, voice: str | None = None, provider: str | None = None,
                      profile: str | None = None, lang: str | None = None,
                      token: str | None = None, summoned: str | None = None) -> None:
    if await _reject_unauthorized(websocket, token):
        return
    await handle_connection(websocket, provider=provider, voice=voice, profile=profile, lang=lang,
                            summoned=summoned == "1")


@app.websocket("/ws/camera")
async def ws_camera(websocket: WebSocket, token: str | None = None) -> None:
    """The robot's camera, one JPEG per binary message (kiosk `?cam=1`).

    Receives only. Frames land in `robot_camera.feed`, which the greeter
    reads when FACE_CAMERA_SOURCE=robot - see app/robot_camera.py for why
    the robot's own page is the sender. Token-gated like every socket: a
    picture of the showroom door is not for "some socket on the LAN".
    """
    import asyncio
    import json as _json

    from app import robot_camera

    if await _reject_unauthorized(websocket, token):
        return
    await websocket.accept()
    robot_camera.feed.attach()

    async def _recv_frames() -> None:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                return
            data = msg.get("bytes")
            if data:
                robot_camera.feed.push(data)
                continue
            text = msg.get("text")
            if text:
                try:
                    event = _json.loads(text)
                    if (event.get("type") == "mic_level"
                            and isinstance(event.get("level"), (int, float))
                            and not isinstance(event.get("level"), bool)):
                        robot_camera.feed.push_mic_level(
                            event["level"], str(event.get("label") or ""))
                except (TypeError, ValueError, _json.JSONDecodeError):
                    continue

    async def _push_control() -> None:
        # The one thing the server sends the robot's page: camera controls the
        # cockpit asked for (zoom). Latest-wins, and sent on connect too so a
        # page that reconnected picks up the current setting.
        last = -1
        while True:
            seq, ctl = robot_camera.feed.control()
            if seq != last and ctl:
                last = seq
                await websocket.send_text(_json.dumps({"type": "camctl", **ctl}))
                if "mic_probe_s" in ctl:
                    robot_camera.feed.clear_control("mic_probe_s")
            await asyncio.sleep(0.3)

    tasks = [asyncio.create_task(_recv_frames()), asyncio.create_task(_push_control())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        for t in tasks:
            t.cancel()
        robot_camera.feed.detach()


@app.websocket("/ws/display")
async def ws_display(websocket: WebSocket, token: str | None = None) -> None:
    """Passive feed for slide displays — receives only, never sends."""
    if await _reject_unauthorized(websocket, token):
        return
    from app.tools import slides

    await websocket.accept()
    await register_and_sync(websocket, slides)
    try:
        while True:
            # Nothing is expected from a display; this just parks the
            # connection until the screen goes away.
            await websocket.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        await display.unregister(websocket)


async def register_and_sync(websocket: WebSocket, slides) -> None:
    """Register the display and immediately send whatever is on screen, so a
    screen that connects mid-presentation isn't left blank."""
    await display.register(websocket)
    try:
        import json

        await websocket.send_text(json.dumps(
            {"type": "slide", "slide": slides.current_slide()}, ensure_ascii=False
        ))
    except Exception:
        pass
