"""The hand in one room: a tiny local service that runs on that room's PC.

Emma's brain stays in one place. This is what a room lends her — actions
performed on the machine that is standing in that room, reachable over the LAN
by the server holding the conversation. No cloud, no account, no paid service:
the owner's rule for this piece was "ทำแบบ Local ไม่ก็ฟรี".

The shape is a CATALOGUE that is **entirely switched off** until a machine says
otherwise (owner, 2026-09-24: "อยากให้ควบคุมคอมได้ ส่วนที่ว่าจะให้ใช้อะไรบ้าง
ค่อยมาจำกัด"). Writing the abilities now and narrowing later is only safe in
that order: a capability nobody enabled cannot be called, so the list can grow
without the risk growing with it. `ROOM_AGENT_ALLOW` is that switch, and it is
empty by default — a freshly installed agent does nothing at all.

Four gates, and each one is a bug this repo has already paid for once:

**It fails closed.** No token configured means every command is refused, and
the boot line says so. `WS_TOKEN` shipped the other way round — `HOST=0.0.0.0`
next to `WS_TOKEN=""`, so the value that went out of the box was the one with
no door on it. A service that can drive a showroom PC does not repeat that.

**It listens on localhost until told otherwise.** The room PC and the server
are usually different machines, so this will normally be opened to the LAN —
but that is a decision someone makes on purpose, not a default that arrives by
forgetting.

**Programs come from the room's own list.** `open_program` resolves names
against the Start Menu, and that is not narrow enough here: measured on the
owner's machine, the Start Menu holds 120 entries and the word "cmd" matches
exactly one of them — "git cmd" — so the first run of these tests opened a
shell on his desktop. A list of what is installed is not a list of what a
stranger at a microphone may start. `ROOM_AGENT_PROGRAMS` is the room's list.

**Typing and clicking are a separate switch again.** Everything else here is a
named action with a fixed shape; `type_text` and `mouse_click` are general
computer control, where one misheard word becomes keystrokes on a real
machine. They need `ROOM_AGENT_INPUT=true` on top of being enabled, so nobody
turns them on by copying a line.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("room_agent")

ROOT = Path(__file__).resolve().parent.parent

# Read this machine's .env BEFORE any of the settings below.
#
# Everywhere else in this repo the .env is read on the way through app.config,
# and that is enough because everything else imports app.config. This module
# deliberately does not: importing app.tools registers Emma's tools as a side
# effect, so the capabilities reach for it late. The result was a room agent
# that read its settings from an environment nothing had populated -- it came
# up on the default port, unnamed, tokenless and refusing everything, while a
# file two lines away said otherwise. The boot banner was honest about the
# state and silent about the cause, so it read as "configured wrong" rather
# than "the file was never opened". Caught 2026-09-25 by starting a built room
# package instead of exporting the variables by hand.
load_dotenv(ROOT / ".env")

#: Which room this machine is. Free text, but it is what the server's routing
#: table will match on, so it has to be the same string in both places.
ROOM_NAME = os.getenv("ROOM_AGENT_NAME", "").strip()
#: Shared secret, one per machine. Blank = refuse everything.
ROOM_TOKEN = os.getenv("ROOM_AGENT_TOKEN", "").strip()


def _list(var: str) -> set[str]:
    return {x.strip().lower() for x in os.getenv(var, "").split(",") if x.strip()}


#: Written on a list to mean "do not check this list". Owner, 2026-09-25:
#: "ทำได้ทั้งหมดก่อน ขอแค่เข้าไปควบคุมได้ก่อน" -- so it exists, but as a word
#: somebody typed on purpose, not as the value that arrives by forgetting.
#: Empty is still the default and the boot banner says out loud when a list is
#: open. It does NOT reach ROOM_AGENT_INPUT: keyboard and mouse stay their own
#: line, because the reason that switch is separate -- one misheard word
#: becomes keystrokes on a real machine -- is just as true in a room where
#: everything else is allowed.
OPEN_LIST = {"all", "*"}

#: Which capabilities this room turns on. Empty = none. `all` = the catalogue.
ALLOW: set[str] = _list("ROOM_AGENT_ALLOW")
#: What this room may open or close, by name, lower-case. `all` hands the
#: decision to the Start Menu, which is the wide setting the "git cmd" incident
#: in this file's header is about.
PROGRAMS: set[str] = _list("ROOM_AGENT_PROGRAMS")
#: The second switch for general keyboard/mouse control.
INPUT_ENABLED = os.getenv("ROOM_AGENT_INPUT", "").strip().lower() in {"1", "true", "yes", "on"}
HOST = os.getenv("ROOM_AGENT_HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = int(os.getenv("ROOM_AGENT_PORT", "8777"))

LOG_DIR = ROOT / "data" / "room-agent"
SHOT_DIR = LOG_DIR / "shots"

WINDOWS = sys.platform == "win32"


def _unsupported(what: str) -> dict:
    return {"ok": False, "error": "not supported on this machine",
            "detail": what,
            "instruction": "เครื่องนี้ทำสิ่งนี้ไม่ได้ ให้บอกตรงๆ ห้ามบอกว่าทำแล้ว"}


# ==================== the machine-level actions ====================
# Delegated to app.tools.computer where it already exists (programs, browser,
# media keys); written here where it does not. Nothing is duplicated: a second
# copy of "start a program" would be a second copy of its allow-list too.

#: Keys a room may be asked to press. A fixed map, not free text — this is how
#: a presentation is driven without handing over the keyboard. The names are
#: what the server would say; the numbers are Windows virtual key codes.
KEYS: dict[str, int] = {
    "next_slide": 0x22,      # PageDown, what a presenter remote sends
    "previous_slide": 0x21,  # PageUp
    "start_slideshow": 0x74,  # F5
    "escape": 0x1B,
    "space": 0x20,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "home": 0x24, "end": 0x23,
}


def press_key(key: str) -> dict:
    name = (key or "").strip().lower()
    vk = KEYS.get(name)
    if vk is None:
        return {"ok": False, "error": "unknown key", "known": sorted(KEYS)}
    if not WINDOWS:
        return _unsupported("press_key")
    import ctypes

    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
    # Fire and forget, like the media keys: which application answered is
    # unknowable from here, so the result never claims an outcome.
    return {"ok": True, "pressed": name,
            "note": "ส่งปุ่มแล้ว แต่ไม่รู้ว่าโปรแกรมไหนรับ ห้ามบอกว่าสไลด์เปลี่ยนแล้วถ้าไม่เห็นภาพยืนยัน"}


def list_windows() -> dict:
    """What is open on that screen — read-only, and the honest way to check.

    Emma cannot see the room's monitor. Without this the only way to report
    "the presentation is up" is to believe the command that was sent, which is
    exactly the mock-reported-as-ok mistake one machine over.
    """
    if not WINDOWS:
        return _unsupported("list_windows")
    import ctypes

    titles: list[str] = []
    user32 = ctypes.windll.user32
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def collect(handle, _param):
        if not user32.IsWindowVisible(handle):
            return True
        length = user32.GetWindowTextLengthW(handle)
        if length:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(handle, buf, length + 1)
            if buf.value.strip():
                titles.append(buf.value)
        return True

    user32.EnumWindows(proto(collect), None)
    return {"ok": True, "windows": titles[:40], "count": len(titles)}


def focus_window(title: str) -> dict:
    """Bring a window to the front by a piece of its title."""
    if not WINDOWS:
        return _unsupported("focus_window")
    import ctypes

    want = (title or "").strip().lower()
    listed = list_windows()
    matches = [t for t in listed.get("windows", []) if want in t.lower()]
    if not matches:
        return {"ok": False, "error": "no such window", "open": listed.get("windows", [])[:10]}
    if len(matches) > 1:
        # Same rule as open_program: ambiguity is a question, not a guess.
        return {"ok": False, "error": "ambiguous", "candidates": matches[:8]}
    user32 = ctypes.windll.user32
    handle = user32.FindWindowW(None, matches[0])
    if not handle:
        return {"ok": False, "error": "window disappeared"}
    user32.ShowWindow(handle, 9)          # SW_RESTORE
    user32.SetForegroundWindow(handle)
    return {"ok": True, "focused": matches[0]}


def screenshot() -> dict:
    """Save what the room screen shows, and return where it went — not the image.

    Read-only, and the only way Emma can verify anything she did here. The file
    stays on the room's own disk: a screen grab of a sales desk can hold a
    customer's name, so it is not sent anywhere by this call.
    """
    try:
        from PIL import ImageGrab
    except Exception:
        return _unsupported("screenshot (PIL not installed)")
    try:
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        image = ImageGrab.grab()
        path = SHOT_DIR / (datetime.now().strftime("%Y%m%d-%H%M%S") + ".png")
        image.save(path)
        return {"ok": True, "saved": str(path), "size": list(image.size)}
    except Exception:
        logger.exception("screenshot failed")
        return {"ok": False, "error": "screenshot failed"}


def screen() -> dict:
    """Return the current screen as a base64 JPEG, for the operator's console.

    Deliberately the opposite of `screenshot`: that one saves to disk and returns
    only a path, because a saved grab of a sales desk is a record nobody chose to
    keep. This one SENDS the image, so a person watching the control panel can see
    the room PC live. That is the owner looking at their own machine over the LAN,
    and the room token is the gate. Downscaled and JPEG so it is light enough to
    poll a few times a second; the real resolution is reported in `size`.
    """
    try:
        from PIL import ImageGrab
    except Exception:
        return _unsupported("screen (PIL not installed)")
    try:
        import base64
        import io as _io

        image = ImageGrab.grab()
        real = list(image.size)
        max_w = 1366
        if image.width > max_w:
            image = image.resize((max_w, round(image.height * max_w / image.width)))
        buf = _io.BytesIO()
        image.convert("RGB").save(buf, format="JPEG", quality=55)
        return {"ok": True, "jpeg_base64": base64.b64encode(buf.getvalue()).decode("ascii"),
                "size": real}
    except Exception:
        logger.exception("screen grab failed")
        return {"ok": False, "error": "screen grab failed"}


def lock_screen() -> dict:
    if not WINDOWS:
        return _unsupported("lock_screen")
    import ctypes

    ok = bool(ctypes.windll.user32.LockWorkStation())
    return {"ok": ok} if ok else {"ok": False, "error": "lock failed"}


def monitor(state: str) -> dict:
    """Turn the room's display off or on."""
    if not WINDOWS:
        return _unsupported("monitor")
    import ctypes

    want = (state or "").strip().lower()
    if want not in {"off", "on"}:
        return {"ok": False, "error": "unknown state", "known": ["off", "on"]}
    HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER = 0xFFFF, 0x0112, 0xF170
    ctypes.windll.user32.SendMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER,
                                      2 if want == "off" else -1)
    return {"ok": True, "monitor": want}


def type_text(text: str) -> dict:
    """General keyboard control. Behind ROOM_AGENT_INPUT for a reason."""
    if not WINDOWS:
        return _unsupported("type_text")
    import ctypes

    body = str(text or "")
    if not body:
        return {"ok": False, "error": "nothing to type"}
    if len(body) > 200:
        # A bound, because this arrives from speech: a transcript that runs
        # away should not become two thousand keystrokes.
        return {"ok": False, "error": "too long", "limit": 200}
    user32 = ctypes.windll.user32
    for ch in body:
        code = ord(ch)
        user32.keybd_event(0, code, 0x4, 0)      # KEYEVENTF_UNICODE
        user32.keybd_event(0, code, 0x4 | 0x2, 0)
    return {"ok": True, "typed_chars": len(body)}


def mouse_click(x: int, y: int) -> dict:
    """General pointer control. Behind ROOM_AGENT_INPUT for a reason."""
    if not WINDOWS:
        return _unsupported("mouse_click")
    import ctypes

    try:
        px, py = int(x), int(y)
    except (TypeError, ValueError):
        return {"ok": False, "error": "bad coordinates"}
    user32 = ctypes.windll.user32
    user32.SetCursorPos(px, py)
    user32.mouse_event(0x0002, 0, 0, 0, 0)       # left down
    user32.mouse_event(0x0004, 0, 0, 0, 0)       # left up
    return {"ok": True, "clicked": [px, py]}


def _computer(attr: str) -> Callable[..., Any]:
    """Late-bound call into app.tools.computer.

    Imported at call time, not module import: pulling that module in registers
    its functions as Emma's tools as a side effect, and this file is imported
    by tests that have nothing to do with the registry.
    """

    def call(**kwargs):
        try:
            from app.tools import computer
        except Exception as exc:                  # pragma: no cover - deployment shape
            # A room PC needs app/ AND two data files: app.tools.__init__ imports
            # app.config, which resolves paths through data/registry/source_registry.json
            # at import time and reaches data/projects/<id>/project.json from there.
            # Because THIS import is late, a machine missing them boots perfectly,
            # answers /health and runs list_windows, then fails the first time
            # somebody says "open the browser". Measured on a bare copy 2026-09-25.
            logger.error("delegated capability %s is unavailable: %s", attr, exc)
            return {"ok": False, "error": "this machine is not fully installed",
                    "detail": f"{attr}: {exc}",
                    "instruction": "เครื่องในห้องนี้ติดตั้งไม่ครบ ให้บอกตรงๆ ห้ามบอกว่าทำแล้ว"}
        return getattr(computer, attr)(**kwargs)

    return call


@dataclass(frozen=True)
class Capability:
    run: Callable[..., Any]
    #: Argument names that must be present and non-empty.
    args: tuple[str, ...] = ()
    #: Argument names passed through when given.
    optional: tuple[str, ...] = ()
    #: read = looks only · act = does something named and reversible ·
    #: input = general keyboard/mouse, the second switch.
    risk: str = "act"
    #: Checked against the room's own program list before anything resolves.
    program_arg: str = ""
    what: str = ""


#: The whole surface. A name that is not here does not exist, and the server
#: cannot invent one. Everything is off until ROOM_AGENT_ALLOW names it.
CAPABILITIES: dict[str, Capability] = {
    "list_windows": Capability(list_windows, risk="read", what="รายชื่อหน้าต่างที่เปิดอยู่"),
    "screenshot": Capability(screenshot, risk="read", what="เก็บภาพหน้าจอไว้ในเครื่องนั้น"),
    "screen": Capability(screen, risk="read", what="ภาพหน้าจอสดส่งกลับมาดูที่แผงคุม"),
    "open_program": Capability(_computer("open_program"), args=("name",), program_arg="name",
                               what="เปิดโปรแกรมในรายการของห้อง"),
    "close_program": Capability(_computer("close_program"), args=("name",), program_arg="name",
                                what="ปิดโปรแกรมในรายการของห้อง"),
    "open_in_browser": Capability(_computer("open_in_browser"), args=("url",),
                                  what="เปิดหน้าเว็บ (เฉพาะ http/https)"),
    "close_web_page": Capability(_computer("close_web_page"), what="ปิดหน้าเว็บที่เปิดไว้"),
    "media_control": Capability(_computer("media_control"), args=("action",), optional=("times",),
                                what="ปุ่มมีเดีย เล่น/หยุด/เสียง"),
    "press_key": Capability(press_key, args=("key",), what="กดปุ่มในรายการ เช่น เลื่อนสไลด์"),
    "focus_window": Capability(focus_window, args=("title",), what="ดึงหน้าต่างขึ้นมาข้างหน้า"),
    "lock_screen": Capability(lock_screen, what="ล็อกหน้าจอ"),
    "monitor": Capability(monitor, args=("state",), what="ปิด/เปิดจอ"),
    "type_text": Capability(type_text, args=("text",), risk="input", what="พิมพ์ข้อความ (คุมคีย์บอร์ดจริง)"),
    "mouse_click": Capability(mouse_click, args=("x", "y"), risk="input", what="คลิกเมาส์ (คุมเมาส์จริง)"),
}


def _record(action: str, ok: bool, detail: str = "", client: str = "") -> None:
    """One line per command, on the machine that ran it.

    Argument *names* are already in the capability's contract; their values are
    not written here. The same rule as the server's tool log, for the same
    reason — a log that quietly accumulates what people asked for is a box of
    personal data nobody decided to keep.
    """
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        line = {"at": datetime.now().isoformat(timespec="seconds"), "room": ROOM_NAME,
                "action": action, "ok": ok, "detail": detail, "from": client}
        path = LOG_DIR / (datetime.now().strftime("%Y-%m-%d") + ".jsonl")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:            # a log that cannot be written must not stop the hand
        logger.exception("could not write the room agent log")


async def run_action(action: str, args: dict[str, Any]) -> dict:
    """Execute one enabled capability on THIS machine."""
    name = (action or "").strip()
    cap = CAPABILITIES.get(name)
    if cap is None:
        return {"ok": False, "error": "unknown action", "known": sorted(CAPABILITIES)}
    # Enabled first: a capability this room did not turn on must not even
    # reveal whether its arguments were right.
    if name not in ALLOW and not (ALLOW & OPEN_LIST):
        return {"ok": False, "error": "not enabled here", "enabled": sorted(ALLOW),
                "instruction": "ห้องนี้ยังไม่ได้เปิดความสามารถนี้ ให้บอกตรงๆ ห้ามบอกว่าทำแล้ว"}
    if cap.risk == "input" and not INPUT_ENABLED:
        return {"ok": False, "error": "input control is off",
                "instruction": "เครื่องนี้ปิดการคุมคีย์บอร์ด/เมาส์ไว้ ให้บอกตรงๆ ห้ามบอกว่าทำแล้ว"}

    missing = [a for a in cap.args if not str(args.get(a, "")).strip()]
    if missing:
        return {"ok": False, "error": "missing argument", "missing": missing}

    if cap.program_arg:
        wanted = str(args.get(cap.program_arg, "")).strip().lower()
        if wanted not in PROGRAMS and not (PROGRAMS & OPEN_LIST):
            return {"ok": False, "error": "program not allowed here", "allowed": sorted(PROGRAMS),
                    "instruction": "ห้องนี้ยังไม่ได้อนุญาตโปรแกรมนี้ ให้บอกตรงๆ ห้ามบอกว่าเปิดแล้ว"}

    kwargs = {a: args[a] for a in cap.args}
    kwargs.update({a: args[a] for a in cap.optional if a in args})
    result = cap.run(**kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result if isinstance(result, dict) else {"ok": True, "result": result}


def create_app() -> FastAPI:
    app = FastAPI(title="Emma room agent", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health() -> dict:
        # The catalogue AND what is live. A server reading this can tell the
        # model what this room can actually do, instead of letting it promise
        # something and find out afterwards — and a person can tell "no agent
        # there" apart from "agent there, nothing enabled".
        return {
            "ok": True,
            "room": ROOM_NAME,
            "armed": bool(ROOM_TOKEN),
            "enabled": sorted(CAPABILITIES) if (ALLOW & OPEN_LIST) else sorted(ALLOW & set(CAPABILITIES)),
            "everything_enabled": bool(ALLOW & OPEN_LIST),
            "any_program": bool(PROGRAMS & OPEN_LIST),
            "catalogue": {n: {"risk": c.risk, "what": c.what} for n, c in sorted(CAPABILITIES.items())},
            "programs": sorted(PROGRAMS),
            "input_control": INPUT_ENABLED,
        }

    @app.post("/do")
    async def do(request: Request) -> JSONResponse:
        client = request.client.host if request.client else ""
        if not ROOM_TOKEN:
            _record("(refused)", False, "no token configured", client)
            return JSONResponse(status_code=503, content={
                "ok": False, "error": "agent not armed",
                "instruction": "ROOM_AGENT_TOKEN is not set on this machine"})
        if request.headers.get("x-room-token", "") != ROOM_TOKEN:
            _record("(refused)", False, "bad token", client)
            return JSONResponse(status_code=401, content={"ok": False, "error": "bad token"})
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"ok": False, "error": "bad request"})
        action = str((body or {}).get("action", "")).strip()
        args = (body or {}).get("args") or {}
        if not isinstance(args, dict):
            return JSONResponse(status_code=400, content={"ok": False, "error": "bad args"})

        result = await run_action(action, args)
        ok = bool(result.get("ok"))
        # Only fields the action itself resolved from its own lists are copied
        # into the log — never the raw text that was asked for.
        detail = str(result.get("opened") or result.get("pressed") or result.get("focused")
                     or result.get("error") or "")
        _record(action or "(none)", ok, detail, client)
        return JSONResponse(content=result)

    return app


app = create_app()


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    logger.info("room agent: room=%r", ROOM_NAME or "(unnamed)")
    if ALLOW & OPEN_LIST:
        logger.warning("EVERY capability in the catalogue is enabled here (%d of them).", len(CAPABILITIES))
    else:
        logger.info("room agent: enabled=%s", ",".join(sorted(ALLOW)) or "(none — every action will be refused)")
    if PROGRAMS & OPEN_LIST:
        logger.warning("ANY program the Start Menu can find may be started from the network.")
    else:
        logger.info("room agent: programs=%s", ",".join(sorted(PROGRAMS)) or "(none)")
    if not ROOM_TOKEN:
        logger.warning("ROOM_AGENT_TOKEN is not set — every command will be refused.")
    if INPUT_ENABLED:
        logger.warning("ROOM_AGENT_INPUT is on — this machine accepts typing and clicking from the network.")
    if HOST not in {"127.0.0.1", "localhost"}:
        logger.warning("listening on %s — keep it on the LAN and keep the token secret.", HOST)
    # Say at BOOT whether the delegated half of the catalogue can run here, for
    # the reason in `_computer`: those capabilities import late, so an
    # incomplete copy starts clean and the answer arrives in front of a customer.
    try:
        from app.tools import computer  # noqa: F401
    except Exception as exc:
        logger.error("NOT fully installed: %s", exc)
        logger.error("these will refuse until app/ and data/ are complete: %s",
                     ", ".join(n for n, c in sorted(CAPABILITIES.items())
                               if c.run.__qualname__.startswith("_computer")))
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
