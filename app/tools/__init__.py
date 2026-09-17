"""Tool registry and the built-in tool modules.

Importing a module is what registers its tools, so every tool module must be
imported here. To add a category later (robot navigation, printing a
brochure, booking a viewing), create `app/tools/<name>.py`, decorate the
handlers with `@tool`, and add it to `_TOOL_MODULES`.
"""
from __future__ import annotations

import importlib
import logging

from app.config import settings
from app.tools.registry import (  # noqa: F401  (re-exported)
    Tool,
    all_tools,
    as_gemini_tool,
    as_openai_tools,
    clear,
    dispatch,
    dispatch_all,
    get,
    tool,
)

logger = logging.getLogger("condo_voice.tools")

#: Module path -> the tag used to enable/disable it via TOOLS_ENABLED.
_TOOL_MODULES = {
    "app.tools.smarthome": "smarthome",
    "app.tools.slides": "slides",
    # Its own group, not part of "slides". The deck tools present a
    # 65-page sales presentation; the unit card answers "ขอดูห้อง A801".
    # Filing them together means anyone who wants the card gets
    # start_presentation too — and a tool the model can see is a tool it
    # will eventually call, which is how Emma would end up narrating a condo
    # deck in the owner's living room.
    "app.tools.units": "units",
    # Same family as `units`: a screen template driven by a tool. Its own
    # group for the same reason — a machine should be able to name exactly
    # which screens its assistant can put up.
    "app.tools.calc": "calc",
    "app.tools.knowledge": "knowledge",
    "app.tools.documents": "documents",
    "app.tools.robot": "robot",
    "app.tools.reminders": "reminders",
    "app.tools.memory": "memory",
    "app.tools.mydocs": "mydocs",
    "app.tools.websearch": "websearch",
    "app.tools.computer": "computer",
}

#: Groups that load only when *named* in the enabled set. "Everything"
#: (TOOL_GROUPS blank on the condo profile) predates these groups, and the
#: gallery must not sprout new tools on a git pull — a guest setting the
#: showroom an alarm is nobody's feature. Emma names them in her default.
_OPT_IN = {"reminders", "memory", "mydocs", "websearch", "computer"}


def unknown_groups(enabled: set[str] | None) -> set[str]:
    """Names in TOOL_GROUPS that match no group. Almost always a typo.

    Silent until now, and silence is the wrong answer: `TOOL_GROUPS=...,unit`
    loads exactly as much as leaving it out, so the assistant simply says it
    cannot do the thing and nothing anywhere says why. Cost a round trip to
    find once already.
    """
    if enabled is None:
        return set()
    return enabled - set(_TOOL_MODULES.values())


def _modules_to_load(enabled: set[str] | None) -> list[str]:
    """Pure so it can be tested without touching the import machinery."""
    chosen = []
    for module_path, group in _TOOL_MODULES.items():
        if enabled is None:
            if group in _OPT_IN:
                continue
            chosen.append(module_path)
        elif group in enabled:
            chosen.append(module_path)
    return chosen

_loaded = False


def load_tools() -> list[Tool]:
    """Import the enabled tool modules once, and return what got registered."""
    global _loaded
    # Imports are cached by Python. Re-evaluate the selection so a changed
    # configuration cannot leave the newly permitted groups unregistered.
    for module_path in _modules_to_load(settings.enabled_tool_groups()):
        try:
            importlib.import_module(module_path)
        except Exception:
            # A broken tool module must not take the whole assistant down —
            # it should still be able to hold a conversation.
            logger.exception("could not load tool module %s", module_path)

    _loaded = True
    names = [t.name for t in all_tools()]
    logger.info("tools loaded: %s", ", ".join(names) if names else "(none)")
    return all_tools()


def reset_for_tests() -> None:
    global _loaded
    _loaded = False
    clear()
