"""Task-local robot backend override used by the isolated simulation server.

Only the isolated simulator server sets this context, including its Emma voice
sessions. The production server cannot select it through a client message.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Protocol

SIMULATION_TOOLS = frozenset({"go_to_place", "stop_moving", "return_to_base", "get_robot_status",
                              "set_simulated_device", "get_simulated_home"})


class RobotBackend(Protocol):
    state: dict[str, Any]
    places: list[str]

    async def send(self, action: str, **args: Any) -> str: ...


active: ContextVar[RobotBackend | None] = ContextVar("robot_simulation", default=None)


@contextmanager
def use_backend(backend: RobotBackend):
    token = active.set(backend)
    try:
        yield backend
    finally:
        active.reset(token)
