"""M1 plan action: only a verified browser render may confirm ``show_plan``."""
from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app import turnlog
from app.config import settings

logger = logging.getLogger("condo_voice.plan_display")
ACK_TIMEOUT_S = 12.0
SEND_TIMEOUT_S = 2.0


@dataclass
class _Pending:
    command_id: str
    sha256: str
    future: asyncio.Future[dict]


_clients: dict[str, Callable[[dict], Awaitable[None]]] = {}
_pending: dict[str, _Pending] = {}


def register(session_id: str, send: Callable[[dict], Awaitable[None]]) -> None:
    _clients[session_id] = send


def unregister(session_id: str) -> None:
    _clients.pop(session_id, None)
    pending = _pending.pop(session_id, None)
    if pending and not pending.future.done():
        pending.future.set_result({"status": "disconnected"})


def receive_ack(session_id: str, event: dict) -> None:
    """Accept an ACK only for the outstanding command on this voice socket."""
    pending = _pending.get(session_id)
    if not pending or event.get("command_id") != pending.command_id:
        return
    if not pending.future.done():
        pending.future.set_result(event)


def _failed(reason: str) -> dict:
    state = ("TIMEOUT" if reason == "display timeout" else
             "HASH_MISMATCH" if reason == "display hash mismatch" else "FAILED")
    return {"ok": False, "error": reason, "action_state": state,
            "instruction": "ยังยืนยันไม่ได้ว่าผังแสดงบนจอ ห้ามพูดว่าเปิดแล้ว ให้ขอโทษและเสนอให้ฝ่ายขายช่วยเปิดผัง"}


async def confirm_plan(prepared: dict) -> dict:
    """Wait for the *same* session to verify bytes, decode and paint the plan."""
    from app.tools import units

    session_id = turnlog.session_id.get()
    if not session_id or session_id not in _clients:
        return _failed("display unavailable")
    if prepared.get("project_id") != settings.project_id:
        return _failed("project scope mismatch")
    if (not isinstance(prepared.get("floor"), int)
            or not isinstance(prepared.get("units"), list)
            or not isinstance(prepared.get("counts"), dict)
            or "building" not in prepared):
        return _failed("invalid prepared plan")
    try:
        verified = units.verified_plan_bytes(prepared.get("image"))
    except Exception:
        logger.warning("verified plan lookup failed")
        return _failed("verified asset unavailable")
    if verified is None:
        return _failed("verified asset unavailable")
    sha256, _body = verified
    if session_id in _pending:
        return _failed("display busy")

    command_id = secrets.token_hex(16)
    future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
    _pending[session_id] = _Pending(command_id, sha256, future)
    turnlog.record("plan_action", state="REQUESTED", command_id=command_id)
    command = {
        "type": "display_command", "action": "show_plan",
        "command_id": command_id, "project_id": settings.project_id,
        "asset_id": f"floor_{prepared['floor']}",
        "expected_sha256": sha256,
        "url": f"/verified-plan/{sha256}",
        "plan": {key: prepared[key] for key in ("floor", "building", "units", "counts")},
    }
    try:
        await asyncio.wait_for(_clients[session_id](command), SEND_TIMEOUT_S)
        turnlog.record("plan_action", state="DISPATCHED", command_id=command_id)
        ack = await asyncio.wait_for(future, ACK_TIMEOUT_S)
        turnlog.record("plan_action", state="ACKNOWLEDGED_BY_DISPLAY",
                       command_id=command_id, status=str(ack.get("status", ""))[:32])
        if ack.get("status") == "disconnected":
            return _failed("display disconnected")
        if ack.get("project_id") != settings.project_id or ack.get("asset_id") != command["asset_id"]:
            return _failed("display identity mismatch")
        if ack.get("status") == "hash_mismatch":
            return _failed("display hash mismatch")
        if ack.get("status") != "rendered":
            return _failed("display render failed")
        if ack.get("sha256") != sha256:
            return _failed("display hash mismatch")
        turnlog.record("plan_action", state="RENDERED", command_id=command_id)
    except asyncio.TimeoutError:
        try:
            await asyncio.wait_for(
                _clients[session_id]({"type": "display_cancel", "command_id": command_id}),
                1.0)
        except Exception:
            logger.warning("timed-out plan command could not be cancelled")
        return _failed("display timeout")
    except Exception:
        logger.warning("plan display command could not be delivered")
        return _failed("display unavailable")
    finally:
        if _pending.get(session_id, None) is not None and _pending[session_id].command_id == command_id:
            _pending.pop(session_id, None)

    return {"ok": True, "screen": "plan", "project_id": settings.project_id,
            "floor": prepared["floor"], "building": prepared["building"],
            "counts": prepared["counts"], "action_state": "RENDERED",
            "policy_trace": prepared.get("policy_trace", {}),
            "render_ack": {"command_id": command_id, "asset_id": command["asset_id"],
                           "sha256": sha256},
            "instruction": (f"จอยืนยันว่าแสดงผังชั้น {prepared['floor']} ที่ตรวจ hash แล้ว "
                            "จึงบอกลูกค้าได้ว่าเปิดผังให้แล้ว ห้ามพูดราคา")}
