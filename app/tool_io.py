"""Bounded worker I/O, with explicit handoff for machine state/UI effects."""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
from contextvars import ContextVar
from threading import Event

_owner: ContextVar[tuple | None] = ContextVar("tool_io_owner", default=None)


async def run_blocking(fn, *args, _abandoned=None, **kwargs):
    """Cancellation abandons the result and suppresses later UI effects.

    Python cannot kill an in-flight thread. The registry retains that job to
    refuse duplicate executions until it ends; network calls keep native
    timeouts too. Physical actions already submitted cannot be rolled back.
    """
    abandoned = _abandoned if _abandoned is not None else Event()
    token = _owner.set((asyncio.get_running_loop(), abandoned))
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except asyncio.CancelledError:
        abandoned.set()
        raise
    finally:
        _owner.reset(token)


def on_loop(fn, *args, **kwargs):
    """Run a short state/UI operation on the calling session's event loop."""
    owner = _owner.get()
    if owner is None:
        return fn(*args, **kwargs)
    loop, abandoned = owner
    if abandoned.is_set():
        raise RuntimeError("tool call was cancelled")
    result = Future()

    def invoke():
        if abandoned.is_set():
            result.set_exception(RuntimeError("tool call was cancelled"))
            return
        token = _owner.set(None)
        try:
            result.set_result(fn(*args, **kwargs))
        except BaseException as exc:
            result.set_exception(exc)
        finally:
            _owner.reset(token)

    loop.call_soon_threadsafe(invoke)
    return result.result(timeout=5)
