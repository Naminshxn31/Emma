"""
Tool registry — how the assistant does things, not just talks about them.

Register a function once here and it becomes callable by **both** providers:
the registry emits Gemini `FunctionDeclaration`s and OpenAI Realtime tool
definitions from the same source, and `dispatch()` runs it regardless of
which one asked.

Adding a tool is one decorator:

    @tool(
        name="set_lights",
        description="เปิดหรือปิดไฟในห้องขาย",
        parameters={
            "type": "object",
            "properties": {"on": {"type": "boolean", "description": "true=เปิด"}},
            "required": ["on"],
        },
    )
    def set_lights(on: bool) -> dict:
        return {"ok": True, "lights": on}

Two rules that matter for a *voice* assistant:

1. **Return facts, not sentences.** The model speaks the reply itself, in
   whatever language the guest is using. A handler that returns
   `"เปิดไฟให้แล้วค่ะ"` forces Thai and a fixed phrasing; returning
   `{"ok": True, "lights": True}` lets the model say it naturally in Thai,
   English or anything else.
2. **Never raise into a provider.** An exception mid-conversation leaves the
   guest in silence; its raw text can also contain a source path or draft
   content. `dispatch()` logs it locally and sends a generic error.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("condo_voice.tools")

#: Hard ceiling on any single tool call. Nothing here is worth going quiet
#: for — the slide tools drive a mirror, and the hardware tools answer in
#: milliseconds or not at all.
#:
#: Sized to sit above the slowest legitimate tool with room to spare, not
#: pinned to it. `next_slide` budgets its own time in three separate places
#: (SLIDE_TOOL_BUDGET_S + SLIDE_PAUSE_S + CANVA_ARRIVAL_TIMEOUT_S) and the
#: three add up to 11.8s against a 12.0s ceiling — a margin nobody chose,
#: where raising any one of them by a second in .env turns every advance
#: into {"error": "timeout"} halfway through a tour, for no visible reason.
#: `test_the_slide_budgets_fit_inside_the_tool_timeout` holds the gap open.
TOOL_TIMEOUT_S = 15.0

#: Ceiling for tools that are *supposed* to take a while.
#:
#: `next_slide` is the only one, and it is worth being precise about why: it
#: is not slow, it is *waiting on purpose*. It holds the call open until the
#: guest has actually heard the narration for the slide being left, because
#: the model generates a turn several times faster than it takes to speak
#: and will otherwise walk the whole 59-page deck in about a minute.
#:
#: Judging it by the same 15s ceiling as a light switch forced
#: SLIDE_TOOL_BUDGET_S down to 6s — shorter than the median deck script
#: (~10s spoken) and a fifth of the longest (~30s). The cap meant to protect
#: the conversation was quietly setting how far the picture could drift
#: ahead of the words.
SLOW_TOOL_TIMEOUT_S = 30.0


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any]
    #: Physical/irreversible actions the model shouldn't fire on a mishearing.
    #: Reserved for a confirmation step; currently informational.
    confirm: bool = False
    #: Long-running work (robot navigation). Gemini can keep talking while it
    #: runs; providers that can't just call it normally.
    long_running: bool = False
    #: Waits on the guest rather than on a machine — gets SLOW_TOOL_TIMEOUT_S
    #: instead of the default ceiling. Not a licence to be slow: a tool that
    #: sets this must bound its own wait and say why.
    paced: bool = False
    tags: list[str] = field(default_factory=list)
    # Permission group, independent of display-effect tags and handler mocks.
    group: str | None = None
    blocking: bool = False
    exclusive: bool = False


_REGISTRY: dict[str, Tool] = {}
_inflight: dict[object, asyncio.Task] = {}


def _has_foreign_project(value: object, expected: str) -> bool:
    if isinstance(value, dict):
        if "project_id" in value and value["project_id"] != expected:
            return True
        return any(_has_foreign_project(item, expected) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_foreign_project(item, expected) for item in value)
    return False


def tool(
    name: str,
    description: str,
    parameters: dict[str, Any] | None = None,
    *,
    confirm: bool = False,
    long_running: bool = False,
    paced: bool = False,
    tags: list[str] | None = None,
    blocking: bool = False,
    exclusive: bool = False,
) -> Callable:
    def decorate(fn: Callable) -> Callable:
        if name in _REGISTRY:
            raise ValueError(f"duplicate tool name: {name}")
        _REGISTRY[name] = Tool(
            name=name,
            description=description,
            parameters=parameters or {"type": "object", "properties": {}},
            handler=fn,
            confirm=confirm,
            long_running=long_running,
            paced=paced,
            tags=tags or [],
            group=fn.__module__.removeprefix("app.tools.")
            if fn.__module__.startswith("app.tools.") else None,
            blocking=blocking,
            exclusive=exclusive,
        )
        return fn

    return decorate


def allowed(entry: Tool) -> bool:
    from app.config import settings
    from app.tools import _OPT_IN

    if not settings.tools_enabled:
        return False
    # Optional personal/web/computer tools have no condo source-approval
    # contract. TOOL_GROUPS may not turn them into an alternate knowledge or
    # file/display path for a sales session.
    if settings.assistant_profile == "condo" and entry.group in {
            "memory", "mydocs", "websearch", "computer"}:
        return False
    groups = settings.enabled_tool_groups()
    if entry.group is None:
        return True
    return entry.group not in _OPT_IN if groups is None else entry.group in groups


def all_tools() -> list[Tool]:
    return [entry for entry in _REGISTRY.values() if allowed(entry)]


def get(name: str) -> Tool | None:
    return _REGISTRY.get(name)


def clear() -> None:
    """Test helper — drop every registration."""
    _REGISTRY.clear()


async def dispatch(name: str, args: dict[str, Any] | None) -> dict[str, Any]:
    from app.metrics import active

    observer = active.get()
    if observer is None:
        return await _dispatch(name, args)
    turn_id = observer.ensure()
    started = observer.clock()
    outcome = "cancelled_or_error"
    try:
        result = await _dispatch(name, args)
        outcome = "ok" if result.get("ok") else "failed"
        return result
    finally:
        # No arguments or error text: only a known tool name and elapsed wait.
        observer.record("metric_tool", turn_id=turn_id,
                        name=name if name in _REGISTRY else "unknown", outcome=outcome,
                        elapsed_ms=round((observer.clock()-started)*1000, 3))


async def _dispatch(name: str, args: dict[str, Any] | None) -> dict[str, Any]:
    """Run a tool by name. Always returns a dict, never raises."""
    entry = _REGISTRY.get(name)
    if entry is None:
        logger.warning("model called unknown tool %r", name)
        return {"ok": False, "error": f"unknown tool: {name}"}
    if not allowed(entry):
        return {"ok": False, "error": f"tool disabled: {name}"}
    from app.turnlog import session_id

    # Independent visitors may query the same read tool simultaneously;
    # physical exclusive jobs (printing) remain owned by the whole machine.
    scope = session_id.get()
    job_key = name if entry.exclusive or scope is None else (scope, name)
    if job_key in _inflight and not _inflight[job_key].done():
        return {"ok": False, "error": "busy",
                "instruction": "คำสั่งก่อนยังไม่จบ ห้ามสั่งซ้ำ ให้รอหรือติดต่อเจ้าหน้าที่"}

    args = dict(args or {})

    # Models occasionally invent argument names. Drop unknown ones rather
    # than letting a TypeError take the turn down; a missing *required*
    # argument still surfaces below, which is the case worth reporting.
    signature = inspect.signature(entry.handler)
    takes_kwargs = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()
    )
    if not takes_kwargs:
        unexpected = set(args) - set(signature.parameters)
        if unexpected:
            logger.warning("%s called with unknown args %s — ignoring", name, sorted(unexpected))
            args = {k: v for k, v in args.items() if k not in unexpected}

    from threading import Event

    abandoned = Event()
    try:
        if entry.blocking or entry.exclusive:
            from app.tool_io import run_blocking

            task = asyncio.create_task(
                run_blocking(entry.handler, _abandoned=abandoned, **args)
                if entry.blocking else entry.handler(**args))
            _inflight[job_key] = task

            def finished(job):
                if _inflight.get(job_key) is job:
                    _inflight.pop(job_key, None)
                # Consume an abandoned result, including its exception.
                if not job.cancelled():
                    error = job.exception()
                    if abandoned.is_set():
                        from app import turnlog

                        result = job.result() if error is None else {}
                        turnlog.record("tool_finished_after_wait", name=name,
                                       ok=error is None and isinstance(result, dict)
                                       and result.get("ok", True))

            task.add_done_callback(finished)
            # Keep the worker owned after timeout, preventing duplicate
            # physical actions. Its native I/O timeout still bounds the job.
            result = asyncio.shield(task)
        else:
            result = entry.handler(**args)
        if inspect.isawaitable(result):
            # Nothing gets to hold the conversation open indefinitely.
            #
            # `start_presentation` was seen never returning — it had begun
            # waiting on a browser window — and because function calling is
            # synchronous the model produced nothing at all from that moment
            # on. The robot stood silent with a guest in front of it and no
            # way to recover short of restarting the server.
            #
            # A tool that overruns is a bug worth fixing, but it must fail as
            # a bad answer, not as a dead robot. Whatever the tool was doing,
            # the model gets *something* back and can speak.
            ceiling = SLOW_TOOL_TIMEOUT_S if entry.paced else TOOL_TIMEOUT_S
            result = await asyncio.wait_for(result, timeout=ceiling)
    except asyncio.TimeoutError:
        abandoned.set()
        logger.error(
            "tool %s did not finish within %.0fs — returning an error so the "
            "conversation can continue", name,
            SLOW_TOOL_TIMEOUT_S if entry.paced else TOOL_TIMEOUT_S,
        )
        return {
            "ok": False,
            "error": "timeout",
            "instruction": "เครื่องมือไม่ตอบสนอง ยังยืนยันผลไม่ได้ ห้ามสั่งซ้ำเอง ให้ขอโทษสั้นๆ แล้วคุยต่อ ห้ามเงียบ",
        }
    except asyncio.CancelledError:
        abandoned.set()
        raise
    except TypeError:
        # Wrong/missing arguments from the model — recoverable, tell it so.
        logger.warning("bad arguments for %s", name)
        return {"ok": False, "error": "invalid arguments"}
    except Exception as exc:
        logger.warning("tool %s failed (%s)", name, type(exc).__name__)
        return {"ok": False, "error": "tool unavailable"}

    if not isinstance(result, dict):
        result = {"ok": True, "result": result}
    result.setdefault("ok", True)

    from app.config import settings

    # The final tool-result boundary applies even to an optional adapter or
    # a future tool whose author forgot the expected tags.
    if _has_foreign_project(result, settings.project_id):
        logger.error("tool %s returned a foreign-project result", name)
        return {"ok": False, "error": "project scope mismatch"}
    if any(tag in entry.tags for tag in ("units", "knowledge", "slides")):
        result.setdefault("project_id", settings.project_id)

    # The handler only prepares verified content. Provider success must wait
    # for a render acknowledgement from the same browser session.
    if name == "show_plan" and result.get("pending_display") is True:
        from app.plan_display import confirm_plan

        result = await confirm_plan(result)

    # A tool that changed what's on screen has to reach the display windows.
    # Doing it here rather than inside each slide tool keeps the handlers
    # synchronous and free of transport concerns.
    if "slides" in entry.tags:
        await _push_to_displays()

    return result


async def _push_to_displays() -> None:
    try:
        from app import display
        from app.tools import slides

        await display.show(slides.current_slide())
    except Exception:
        logger.exception("could not update the slide display")


async def dispatch_all(calls: list[tuple[str, str, dict]]) -> list[tuple[str, str, dict]]:
    """Run calls in the order the model emitted them.

    Tool calls are not independent computations.  Several of them mutate
    shared presentation state (and the physical gallery), so running a batch
    with ``gather`` let a late ``search_condo_info`` overwrite the slide chosen
    by ``start_presentation``.  The model's call order is the only meaningful
    order here; preserve it for deterministic state and screen updates.
    """
    results = []
    for _cid, name, args in calls:
        results.append(await dispatch(name, args))
    return [
        (cid, name, result)
        for (cid, name, _args), result in zip(calls, results)
    ]


# ---- provider-specific views of the same registry ----


def as_openai_tools() -> list[dict[str, Any]]:
    """OpenAI Realtime `session.tools` shape."""
    return [
        {
            "type": "function",
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
        }
        for t in all_tools()
    ]


def as_gemini_tool():
    """A single Gemini `Tool` holding every function declaration.

    Imported lazily so the registry stays usable (and testable) without the
    google-genai package installed.
    """
    from google.genai import types

    declarations = []
    for t in all_tools():
        kwargs: dict[str, Any] = {
            "name": t.name,
            "description": t.description,
            # Must be `parameters`, not `parameters_json_schema`. The former
            # is converted into a typed Schema the Live API actually sends to
            # the model; with the latter the model saw a function with no
            # declared arguments and invented its own names
            # (set_lights(lights_on=...) instead of set_lights(on=...)).
            "parameters": t.parameters,
        }
        if t.long_running:
            # Lets the conversation continue while the action runs, instead
            # of the guest standing in silence waiting for it to finish.
            #
            # Only Gemini 2.5 honours this. On 3.x Live the field is not
            # supported and the model blocks until the tool responds — which
            # is invisible today, because every tool here returns in
            # milliseconds, and becomes the worst bug in the product the day
            # a `navigate_to` is added and a guest waits half a minute in
            # front of a robot that has gone silent mid-sentence.
            from app.config import settings
            from app.providers.gemini import supports_non_blocking

            if supports_non_blocking():
                kwargs["behavior"] = types.Behavior.NON_BLOCKING
            else:
                logger.warning(
                    "tool %r is declared long-running, but %s does not support "
                    "asynchronous function calling — the model will STOP "
                    "SPEAKING until it returns. Use a gemini-2.5-* model, or "
                    "make this tool acknowledge immediately and report its "
                    "result separately.",
                    t.name, settings.gemini_model,
                )
        declarations.append(types.FunctionDeclaration(**kwargs))

    return types.Tool(function_declarations=declarations) if declarations else None
