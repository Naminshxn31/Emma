"""FastAPI entry point: serves the voice UI and bridges it to the chosen provider."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
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

# Serve the slide images. Mounted only if the folder exists so the app still
# starts on a machine that hasn't imported a deck yet.
_slides_path = Path(settings.slides_dir).expanduser()
if not _slides_path.is_absolute():
    _slides_path = ROOT / _slides_path
if _slides_path.is_dir():
    app.mount("/slides", StaticFiles(directory=str(_slides_path)), name="slides")

_MODEL_FOR = {"gemini": lambda: settings.gemini_model, "openai": lambda: settings.openai_model}


@app.on_event("startup")
async def _log_effective_config() -> None:
    """Print the settings that actually decide whether this thing works.

    Voice bugs are mostly silent — a mis-set VAD sensitivity just means the
    robot never answers, with nothing in the log to explain it. Showing the
    effective values on boot turns that into a five-second diagnosis.
    """
    log = logging.getLogger("condo_voice")
    provider = settings.provider
    log.info("provider=%s model=%s voice=%s key=%s",
             provider, _MODEL_FOR.get(provider, lambda: "?")(),
             settings.gemini_voice if provider == "gemini" else settings.openai_voice,
             "set" if settings.api_key_for(provider) else "MISSING")
    if provider == "gemini":
        log.info("vad: start=%s end=%s silence=%dms prefix=%dms | thinking_budget=%s | half_duplex=%s",
                 settings.vad_start_sensitivity, settings.vad_end_sensitivity,
                 settings.vad_silence_ms, settings.vad_prefix_padding_ms,
                 settings.gemini_thinking_budget, settings.half_duplex)

    from app import tools
    from app.tools import broadlink_ir

    names = [t.name for t in tools.load_tools()]
    log.info("tools: %s", ", ".join(names) if names else "(none)")
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
    if settings.canva_url:
        # Actually open it, rather than checking that the *package* imports.
        # The old check passed while Chromium itself was missing, so the
        # window silently never appeared and nothing said why. Opening it now
        # also means the first slide change doesn't pay the cold start.
        from app.tools import canva_display

        ok, detail = await canva_display.self_check()
        (log.info if ok else log.error)("canva display: %s", detail)

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
        if settings.vad_start_sensitivity.upper() == "LOW":
            log.warning("VAD_START_SENSITIVITY=LOW detects speech LESS often — "
                        "the assistant may never respond. Use HIGH unless you know why.")


@app.on_event("shutdown")
async def _close_canva_display() -> None:
    """No-op if the window was never opened (CANVA_URL unset)."""
    from app.tools import canva_display

    await canva_display.shutdown()


@app.get("/")
async def serve_client() -> FileResponse:
    return FileResponse(CLIENT_INDEX)


def _wake_ready() -> bool:
    from app import wake

    return settings.wake_enabled and wake.available()


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
        "wake": {"enabled": settings.wake_enabled, "ready": _wake_ready()},
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
    return FileResponse(DISPLAY_INDEX)


@app.websocket("/ws/wake")
async def ws_wake(websocket: WebSocket) -> None:
    """Standby ears. The browser streams PCM16 mono 16kHz here while no
    conversation is running; on hearing the name, the server answers with a
    single {"type": "wake"} and the browser opens the real session on /ws.

    Detection runs server-side rather than in the page because the models
    are Python-only — and the audio still never leaves this machine: this
    process *is* the machine, and nothing upstream is connected until the
    name has been heard. That ordering is the whole point.
    """
    from app import wake

    await websocket.accept()
    if not settings.wake_enabled or not wake.available():
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


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket, voice: str | None = None, provider: str | None = None) -> None:
    await handle_connection(websocket, provider=provider, voice=voice)


@app.websocket("/ws/display")
async def ws_display(websocket: WebSocket) -> None:
    """Passive feed for slide displays — receives only, never sends."""
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
