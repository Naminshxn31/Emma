"""Deterministic command simulator, not an emulator of Aobo firmware or SLAM.

No sockets, vendor SDK, provider calls, or background tasks live in this engine.
The HTTP app ticks it; tests supply a clock. Distances/timing/battery are fiction.
"""
from __future__ import annotations

import copy
import math
import time
import uuid
from collections import OrderedDict, deque
from typing import Any, Callable


POINTS = {
    "base": (1.5, 1.5),
    "ห้องตัวอย่าง": (8.5, 6.5),
    "โต๊ะเซลส์": (3.0, 6.5),
    "สระว่ายน้ำ": (9.0, 2.0),
    "ฟิตเนส": (6.0, 4.0),
}
FAULTS = {"none", "obstacle", "navigation_failed", "no_arrival", "ack_lost"}


def corridor_route(start, target) -> list[list[float]]:
    """Toy showroom lanes shared with the game scene, not a SLAM planner.

    Every POI has a vertical access lane to the main corridor at y=3.1.
    Stops stay on those lanes, so a subsequent trip can use the same junction.
    """
    route = []
    for point in (start, (start[0], 3.1), (target[0], 3.1), target):
        pair = list(point)
        if not route or pair != route[-1]:
            route.append(pair)
    if list(start) == list(target):
        return [list(start)]
    return route


def route_position(route, fraction: float) -> list[float]:
    if fraction <= 0:
        return list(route[0])
    if fraction >= 1:
        return list(route[-1])
    lengths = [math.dist(a, b) for a, b in zip(route, route[1:])]
    remaining = sum(lengths) * min(1.0, max(0.0, fraction))
    for a, b, length in zip(route, route[1:], lengths):
        if length and remaining <= length:
            return [x + (y - x) * remaining / length for x, y in zip(a, b)]
        remaining -= length
    return list(route[-1])


class SimulatedRobot:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.reset()

    def reset(self) -> None:
        from app.robot_diagnostics import RobotDiagnostics
        self.diagnostics = RobotDiagnostics(self.clock)
        self.places = [name for name in POINTS if name != "base"]
        self.state: dict[str, Any] = {
            "connected": True, "moving": False, "destination": None,
            "battery": 85, "charging": False,
        }
        self.position = list(POINTS["base"])
        from app.robot_home import initial_home
        self.smart_home = initial_home()
        self.progress = 0.0
        self.phase = "idle"
        self.fault = "none"
        self.duration = 6.0
        self.timeout = 12.0
        self.pending: dict | None = None
        self.commands: OrderedDict[str, dict] = OrderedDict()
        self.events: deque[dict] = deque(maxlen=500)
        self.sequence = 0
        self.epoch = uuid.uuid4().hex[:12]
        self.started = self.clock()
        self._event("reset", detail="เริ่มรอบจำลองใหม่ ไม่ได้เชื่อมต่อหุ่นจริง")

    def _event(self, kind: str, **data: Any) -> dict:
        self.sequence += 1
        event = {"seq": self.sequence, "t": round(self.clock() - self.started, 3),
                 "type": kind, "hardware": "simulated", **data}
        self.events.append(event)
        return event

    def snapshot(self) -> dict:
        return copy.deepcopy({
            **self.state, "hardware": "simulated", "epoch": self.epoch,
            "phase": self.phase, "position": self.position, "fault": self.fault,
            "duration": self.duration, "timeout": self.timeout,
            "route": self.pending["route"] if self.pending else [],
            "progress": self.progress,
            "smart_home": self.smart_home,
            "diagnostics": self.diagnostics.summary(),
            "points": [{"name": name, "x": xy[0], "y": xy[1]}
                       for name, xy in POINTS.items()],
            "active_command_id": self.pending["id"] if self.pending else None,
            "events": list(self.events), "commands": list(self.commands.values()),
        })

    def set_home(self, command):
        from app.robot_home import apply_change
        apply_change(self.smart_home, command)
        self._event("smart_home", device=command.device,
                    detail=f"{command.device} → {self.smart_home[command.device]}")
        return {"ok": True, "hardware": "simulated", "smart_home": copy.deepcopy(self.smart_home),
                "instruction": "เปลี่ยนอุปกรณ์ในฉากจำลองแล้ว ไม่ได้ส่งคำสั่งไปอุปกรณ์จริง"}

    def record_diagnostic(self, event, fields, session_id=None):
        self.diagnostics.record(event, fields, session_id,
                                self.pending["id"] if self.pending else None)

    def configure(self, *, fault: str, duration: float, timeout: float) -> None:
        if fault not in FAULTS:
            raise ValueError("unknown fault")
        if not (math.isfinite(duration) and math.isfinite(timeout)
                and 1 <= duration <= 30 and 2 <= timeout <= 60):
            raise ValueError("duration must be 1..30 and timeout 2..60 seconds")
        self.fault, self.duration, self.timeout = fault, duration, timeout
        self._event("configuration", fault=fault, duration=duration, timeout=timeout,
                    detail="ใช้กับคำสั่งถัดไป")

    async def send(self, action: str, **args: Any) -> str:
        result = self.submit(action, args)
        return "simulated" if result["accepted"] else "failed"

    def submit(self, action: str, args: dict, command_id: str | None = None) -> dict:
        self.tick()
        ident = command_id or str(uuid.uuid4())
        if not isinstance(ident, str) or not 1 <= len(ident) <= 80:
            raise ValueError("invalid command_id")
        if action not in {"move_to_point", "cancel_navigation", "go_home"}:
            raise ValueError("unsupported action")
        expected = {"place"} if action == "move_to_point" else set()
        if set(args) != expected or (expected and not isinstance(args["place"], str)):
            raise ValueError("invalid args")
        if previous := self.commands.get(ident):
            if previous["action"] != action or previous["args"] != args:
                self._event("rejected", command_id=ident, reason="id_conflict")
                return {"accepted": False, "command_id": ident, "reason": "id_conflict"}
            self._event("duplicate", command_id=ident, detail="ไม่เริ่มเคลื่อนที่ซ้ำ")
            return {**previous["reply"], "duplicate": True}

        command = {"type": "robot", "action": action, "args": dict(args),
                   "command_id": ident}
        record = {**command, "status": "sent"}
        self.commands[ident] = record
        # Retain at most 256 commands, including the active one. This is a
        # rehearsal replay window, not durable exactly-once hardware delivery.
        if len(self.commands) > 256:
            victim = next(k for k in self.commands
                          if not self.pending or k != self.pending["id"])
            del self.commands[victim]
        self._event("command", command_id=ident, action=action, args=dict(args))
        target = args.get("place") if action == "move_to_point" else "base"
        reason = ("disconnected" if not self.state["connected"] else
                  "unknown_place" if action == "move_to_point" and target not in self.places else
                  "busy" if self.pending and action != "cancel_navigation" else None)
        if reason:
            record["status"] = "rejected"
            record["reply"] = {"accepted": False, "command_id": ident, "reason": reason}
            self._event("robot_ack", command_id=ident, accepted=False, reason=reason)
            return dict(record["reply"])

        record["reply"] = {"accepted": True, "command_id": ident}
        if action == "cancel_navigation":
            self._event("robot_ack", command_id=ident, accepted=True)
            if self.pending:
                self._finish("cancelled", ok=False)
            self.state.update(moving=False, destination=None)
            self.phase = record["status"] = "stopped"
            self._event("robot_stopped", command_id=ident)
        else:
            lost = self.fault == "ack_lost"
            record["status"] = "unknown" if lost else "accepted"
            record["reply"]["accepted"] = not lost
            if lost:
                record["reply"]["reason"] = "ack_lost"
            self._event("ack_missing" if lost else "robot_ack",
                        command_id=ident, accepted=not lost)
            self.pending = {
                "id": ident, "target": target, "start": tuple(self.position),
                "route": corridor_route(self.position, POINTS[target]),
                "started": self.clock(), "duration": self.duration,
                "timeout": self.timeout, "fault": self.fault,
            }
            self.progress = 0.0
            self.state.update(moving=None if lost else True, destination=target, charging=False)
            self.phase = "unknown" if lost else "moving"
        return dict(record["reply"])

    def _finish(self, status: str, *, ok: bool) -> None:
        pending, self.pending = self.pending, None
        if not pending:
            return
        self.commands[pending["id"]]["status"] = status
        self.state.update(moving=False, destination=None)
        self.phase = status
        if ok:
            self.progress = 1.0
            self.position = list(POINTS[pending["target"]])
            self.state["charging"] = pending["target"] == "base"
            self.state["battery"] = max(0, self.state["battery"] - 1)
        self._event("robot_arrived", command_id=pending["id"],
                    place=pending["target"], ok=ok, reason=status)

    def complete(self, command_id: str, *, ok: bool) -> bool:
        """Adapter callback boundary; late or unrelated results cannot finish a trip."""
        if (not self.state["connected"] or not self.pending
                or self.pending["id"] != command_id):
            self._event("stale_callback", command_id=command_id)
            return False
        self._finish("arrived" if ok else "navigation_failed", ok=ok)
        return True

    def tick(self) -> None:
        job = self.pending
        if not job or not self.state["connected"]:
            return
        elapsed = self.clock() - job["started"]
        if elapsed >= job["timeout"]:
            self._event("navigation_timeout", command_id=job["id"],
                        detail="หมดเวลาและหยุดเฉพาะตัวจำลอง ไม่ยืนยันการหยุดฮาร์ดแวร์")
            self._finish("timeout", ok=False)
            return
        fraction = min(1.0, max(0.0, elapsed / job["duration"]))
        if job["fault"] == "obstacle" and fraction >= 0.45:
            fraction = 0.45
            if self.phase != "blocked":
                self._event("obstacle", command_id=job["id"])
            self.phase = "blocked"
        self.progress = fraction
        self.position = route_position(job["route"], fraction)
        if job["fault"] == "navigation_failed" and fraction >= 0.4:
            self._finish("navigation_failed", ok=False)
        elif fraction >= 1 and job["fault"] not in {"no_arrival", "ack_lost"}:
            self.complete(job["id"], ok=True)

    def set_connected(self, connected: bool) -> None:
        self.tick()
        if connected == self.state["connected"]:
            return
        self.state["connected"] = connected
        if self.pending:
            self.commands[self.pending["id"]]["status"] = "unknown"
            self.pending = None
        # Lost telemetry is not proof that physical motion stopped. Reconnect
        # in our fake world reconciles to idle and never replays an old order.
        self.state.update(moving=False if connected else None, destination=None,
                          charging=False if connected else None)
        self.phase = "idle" if connected else "disconnected"
        self._event("robot_ready" if connected else "connection_lost",
                    detail="ซิงก์สถานะจำลองใหม่ ไม่ส่งคำสั่งเก่าซ้ำ" if connected
                    else "สถานะการเคลื่อนที่ไม่ทราบ ภาพหยุดอัปเดต ไม่ใช่หลักฐานว่าหุ่นหยุด")
