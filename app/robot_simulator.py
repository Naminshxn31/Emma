"""Loopback-only rehearsal server. Run with python -m app.robot_simulator."""
from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.robot_backend import use_backend
from app.robot_simulation import SimulatedRobot
from app.robot_home import HomeChange


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ToolInput(Input):
    tool: Literal["go_to_place", "stop_moving", "return_to_base", "get_robot_status"]
    place: str = Field(default="", max_length=200)


class CommandInput(Input):
    action: Literal["move_to_point", "cancel_navigation", "go_home"]
    args: dict[str, str] = Field(default_factory=dict)
    command_id: str = Field(min_length=1, max_length=80)


class FaultInput(Input):
    fault: Literal["none", "obstacle", "navigation_failed", "no_arrival", "ack_lost"]
    duration: float = Field(ge=1, le=30, allow_inf_nan=False)
    timeout: float = Field(ge=2, le=60, allow_inf_nan=False)


class ConnectionInput(Input):
    connected: bool


class VoiceObservation(Input):
    client_id: str = Field(pattern=r"^[a-f0-9-]{36}$")
    session_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    mic: Literal["live", "flowing", "muted", "denied", "unavailable", "idle"]
    playback: Literal["running", "suspended", "closed", "idle", "interrupted"]
    wake: Literal["busy", "listening", "idle", "disabled"]
    provider: Literal["idle", "connecting", "connected", "unavailable", "error"]
    error: Literal["none", "permission", "quota", "auth", "network", "provider", "missing_key", "busy"]


def create_app(engine: SimulatedRobot | None = None) -> FastAPI:
    robot = engine or SimulatedRobot()
    voice_cookie = secrets.token_urlsafe(32)
    voice_in_use = False

    @asynccontextmanager
    async def lifespan(app):
        async def advance():
            while True:
                robot.tick()
                await asyncio.sleep(0.1)

        task = asyncio.create_task(advance())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="Condo Robot Simulator", lifespan=lifespan)
    app.state.robot = robot
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def local_ui(request: Request, call_next):
        # JSON-only mutations and same-origin fetches prevent a remote web
        # page from silently controlling/resetting the local demo.
        if request.method == "POST":
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                return JSONResponse({"detail": "same-origin requests only"}, status_code=403)
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"detail": "JSON required"}, status_code=415)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        if request.url.path == "/voice":
            # The existing Emma client has inline code/styles and a blob PCM
            # AudioWorklet. Keep this exception on the voice document only.
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self' 'unsafe-inline' blob:; "
                "style-src 'self' 'unsafe-inline'; worker-src 'self' blob:; "
                "connect-src 'self' ws://127.0.0.1:* ws://localhost:*; "
                "img-src 'self' data:; frame-ancestors 'self'")
        return response

    client = Path(__file__).resolve().parents[1] / "client"

    @app.get("/")
    async def index():
        return FileResponse(client / "robot-simulator.html")

    @app.get("/voice")
    async def voice_page():
        response = FileResponse(client / "index.html")
        response.set_cookie("robot_sim_voice", voice_cookie, httponly=True,
                            samesite="strict", path="/ws")
        return response

    @app.get("/voices")
    async def voices():
        from app import voices as catalogue
        from app.config import settings
        from app.providers import default_voice_for

        return {"provider": settings.provider, "voices": catalogue.as_dicts(settings.provider),
                "default": default_voice_for(settings.provider)}

    @app.get("/health")
    async def voice_health():
        from app.config import settings
        from app import wake

        wake_ready = settings.wake_enabled and wake.available()

        return {
            "ok": True, "provider": settings.provider, "robot_simulator": True,
            "model": settings.gemini_model if settings.provider == "gemini" else settings.openai_model,
            "free_tier": False,  # Do not promise that the owner's API billing is free.
            "api_key_configured": bool(settings.api_key_for(settings.provider)),
            "auto_connect": False,
            "diagnostics": robot.diagnostics.summary(),
            "wake": {"enabled": settings.wake_enabled, "ready": wake_ready, "standby": wake_ready},
            "transcript_keep_min": settings.transcript_keep_min,
            "mic": {"boost": settings.mic_boost, "boost_call": settings.call_mic_boost,
                    "noise_suppression": settings.mic_noise_suppression,
                    "compressor": settings.mic_compressor, "agc": settings.mic_agc},
        }

    def authorized_voice(ws: WebSocket) -> bool:
        origin = ("https" if ws.url.scheme == "wss" else "http") + "://" + ws.headers.get("host", "")
        return (ws.headers.get("origin") == origin
                and secrets.compare_digest(ws.cookies.get("robot_sim_voice", ""), voice_cookie))

    @app.websocket("/ws/wake")
    async def wake_socket(ws: WebSocket):
        from app import wake
        from app.config import settings

        if not authorized_voice(ws):
            await ws.accept()
            await ws.send_json({"type": "error", "code": "unauthorized",
                                "message": "กรุณารีเฟรชหน้า Emma ของตัวจำลองเพื่อเชื่อมต่อใหม่"})
            await ws.close(code=1008)
            return
        await ws.accept()
        if not settings.wake_enabled or not wake.available():
            await ws.send_json({"type": "wake_unavailable",
                                "reason": "disabled" if not settings.wake_enabled else "no model"})
            await ws.close()
            return
        # Reuse the local detector; do not register for real reminders or
        # capture rehearsal audio into the owner's debug/enrollment folders.
        with use_backend(robot):
            stream = wake.WakeStream(diagnostics=False)
            await ws.send_json({"type": "wake_listening", "word": settings.wake_word})
            try:
                while True:
                    message = await ws.receive()
                    if message.get("type") == "websocket.disconnect":
                        return
                    data = message.get("bytes")
                    if not data:
                        continue
                    if len(data) % 2 or len(data) > 64000:
                        await ws.close(code=1009)
                        return
                    hit = stream.feed(data)
                    if hit and not voice_in_use:
                        robot._event("wake", detail="ได้ยินชื่อ Emma ในโหมดจำลอง")
                        robot.record_diagnostic("wake", {})
                        await ws.send_json({"type": "wake", "word": hit})
                        await ws.close()
                        return
            except WebSocketDisconnect:
                return

    @app.websocket("/ws")
    async def voice_socket(ws: WebSocket, voice: str | None = None):
        nonlocal voice_in_use
        if not authorized_voice(ws):
            await ws.close(code=1008)
            return
        if voice_in_use:
            await ws.accept()
            await ws.send_json({"type": "error", "code": "simulator_busy",
                                "message": "มีหน้า Emma คุยกับตัวจำลองอยู่แล้ว ให้จบการคุยในหน้านั้นก่อน"})
            await ws.close(code=1008)
            return
        voice_in_use = True
        try:
            from app.session import handle_connection

            with use_backend(robot):
                robot._event("voice_started", detail="Emma โหมดซ้อม ใช้ provider เดิม")
                # Profile/backend are fixed here, never selected by incoming JSON
                # or URL parameters. The real app's /ws is unchanged.
                await handle_connection(ws, voice=voice, profile="emma")
        finally:
            if robot.pending:
                robot.submit("cancel_navigation", {})
            robot._event("voice_ended", detail="จบเสียง Emma และยกเลิกงานจำลองที่ค้าง")
            voice_in_use = False

    @app.get("/simulator.{extension}")
    async def asset(extension: str):
        if extension not in {"js", "css"}:
            raise HTTPException(404)
        return FileResponse(client / f"robot-simulator.{extension}")

    @app.get("/robot-scene.js")
    async def scene_script():
        return FileResponse(client / "robot-scene.js")

    @app.get("/robot-scene-3d.js")
    async def scene_3d_script():
        return FileResponse(client / "robot-scene-3d.js", media_type="text/javascript")

    @app.get("/robot-{module}.js")
    async def scene_module(module: str):
        if module not in {"explorer", "interior"}:
            raise HTTPException(404)
        return FileResponse(client / f"robot-{module}.js", media_type="text/javascript")

    @app.get("/vendor/three/{filename}")
    async def three_asset(filename: str):
        if filename not in {"three.module.min.js", "three.core.min.js", "OrbitControls.js",
                            "LICENSE", "manifest.json"}:
            raise HTTPException(404)
        media = "text/javascript" if filename.endswith(".js") else "application/json" if filename.endswith(".json") else "text/plain"
        return FileResponse(client / "vendor" / "three" / filename, media_type=media)

    @app.get("/simulator-voice.{extension}")
    async def voice_asset(extension: str):
        if extension not in {"js", "css"}:
            raise HTTPException(404)
        return FileResponse(client / f"simulator-voice.{extension}")

    @app.get("/api/state")
    async def state():
        robot.tick()
        return robot.snapshot()

    @app.get("/api/diagnostics")
    async def diagnostics():
        return {"epoch": robot.epoch, **robot.diagnostics.export()}

    @app.post("/ws/diagnostics")
    async def voice_observation(body: VoiceObservation, request: Request):
        if (request.headers.get("origin") != str(request.base_url).rstrip("/")
                or not secrets.compare_digest(request.cookies.get("robot_sim_voice", ""), voice_cookie)):
            raise HTTPException(403, "reload the simulator voice page")
        if not robot.diagnostics.client(body.model_dump()):
            raise HTTPException(409, "stale voice session")
        return {"ok": True}

    @app.post("/api/tool")
    async def tool(body: ToolInput):
        # Exercise the actual production tool functions, but only these four.
        # No VoiceSession or paid voice provider is constructed.
        from app.tools import robot as handlers

        with use_backend(robot):
            if body.tool == "get_robot_status":
                result = handlers.get_robot_status()
            elif body.tool == "go_to_place":
                result = await handlers.go_to_place(body.place)
            else:
                result = await getattr(handlers, body.tool)()
        robot._event("tool_result", tool=body.tool, result=result)
        return {"result": result, "state": robot.snapshot()}

    @app.post("/api/home")
    async def home_command(body: HomeChange):
        from app.tools.simulation_home import set_simulated_device
        with use_backend(robot):
            result = set_simulated_device(**body.model_dump())
        return {"result": result, "state": robot.snapshot()}

    @app.post("/api/command")
    async def command(body: CommandInput):
        try:
            return robot.submit(body.action, body.args, body.command_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/config")
    async def configure(body: FaultInput):
        robot.configure(**body.model_dump())
        return robot.snapshot()

    @app.post("/api/connection")
    async def connection(body: ConnectionInput):
        robot.set_connected(body.connected)
        return robot.snapshot()

    @app.post("/api/reset")
    async def reset(body: Input):
        if voice_in_use:
            raise HTTPException(409, "จบสาย Emma ก่อนเริ่มรอบใหม่ เพื่อเก็บผลซ้อมให้ตรงรอบ")
        robot.reset()
        return robot.snapshot()

    return app


if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port)
