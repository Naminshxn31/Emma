"""FastAPI entry point: serves the voice UI and bridges it to the chosen provider."""
from __future__ import annotations

import json
import logging
import secrets
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from app.slide_assets import SlideAssets

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

# Serve the slide images. Mounted only if the folder exists so the app still
# starts on a machine that hasn't imported a deck yet.
_slides_path = Path(settings.slides_dir).expanduser()
if not _slides_path.is_absolute():
    _slides_path = ROOT / _slides_path
if _slides_path.is_dir():
    app.mount("/slides", SlideAssets(directory=str(_slides_path)), name="slides")

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

        log.info("robot: enabled — waiting for the app to send robot_ready; "
                 "until then every movement runs in mock mode (%s)",
                 robot_link.status())
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
async def _watch_the_door() -> None:
    """Open the entrance camera, if this machine has one and wants it.

    `FACE_ENABLED` is off by default and the showroom must stay that way:
    every face that gets through opens a session, which is the one thing
    the wake word exists to avoid. `greeter.start()` checks the switch
    itself so nothing here has to be kept in step with it.
    """
    from app import greeter

    greeter.start()


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


async def _reject_unauthorized(websocket: WebSocket, token: str | None) -> bool:
    """True = this socket was rejected and closed.

    Applied to every WebSocket the moment WS_TOKEN is set: the same server
    that answers questions also opens programs and presses keys, so "some
    socket on the LAN" must never be enough to reach it. Accept-then-close
    because a handshake-level refusal shows browsers nothing actionable —
    this way the page can display *why* and stand down instead of redialing.
    """
    if not settings.ws_token:
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
