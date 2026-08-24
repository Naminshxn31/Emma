"""
The first computer-control tool: open a page in the owner's browser.

เฟส 2ข proper — PowerShell, clicking, file operations — is gated behind a
real confirm mechanism and stays gated. This tool ships ahead of that gate
because it sits at the mild end of the spectrum the gate was built for, and
the difference is worth stating precisely:

- **Reversible.** The worst mishearing opens a wrong tab; closing it is the
  whole cleanup. "ลบไฟล์" misheard has no such undo, which is exactly why
  the rest of 2ข waits.
- **No interpreter.** The action is `os.startfile` on an http(s) URL — the
  scheme check is a hard wall, not a convention. `file://` would silently
  become "open any file with its default app" and `ms-settings:` and
  friends reach OS surfaces; both are refused before anything runs.
- **Local-only exposure.** The server binds 127.0.0.1, so the only thing
  that can ask for this is the machine's own browser.

YouTube needs no special case: the model composes ordinary URLs
(`youtube.com/results?search_query=...`), and the description says so —
one tool, every site.
"""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

from app import turnlog
from app.tools.registry import tool

logger = logging.getLogger("condo_voice.computer")


@tool(
    name="open_in_browser",
    description=(
        "เปิดหน้าเว็บบนจอคอมพิวเตอร์ของเจ้าของ ใช้เมื่อเขาขอให้เปิดเว็บ เปิดยูทูบ "
        "หรืออยากดูอะไรเต็มๆ บนจอ เปิดยูทูบพร้อมค้นได้ด้วย URL รูปแบบ "
        "https://www.youtube.com/results?search_query=คำค้น "
        "รับเฉพาะลิงก์ http/https เท่านั้น"
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "ลิงก์เต็ม ขึ้นต้น http:// หรือ https://"},
        },
        "required": ["url"],
    },
    tags=["computer"],
)
def open_in_browser(url: str) -> dict:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        # The wall. file:// opens arbitrary files with their default apps,
        # ms-settings:/shell: reach into the OS — none of that is "open a
        # webpage", so none of it gets to ride on this tool.
        return {"ok": False, "error": "not a web url",
                "instruction": "เปิดได้เฉพาะลิงก์เว็บ http/https ให้บอกเจ้าของตรงๆ"}
    try:
        import os

        os.startfile(url)  # Windows: default browser
    except Exception:
        logger.exception("could not open %r", url)
        turnlog.record("open_browser", url=url, ok=False)
        return {"ok": False, "error": "could not open",
                "instruction": "เปิดเบราว์เซอร์ไม่สำเร็จ ให้บอกเจ้าของตรงๆ ห้ามบอกว่าเปิดแล้ว"}
    turnlog.record("open_browser", url=url, ok=True)
    return {"ok": True, "opened": url}


#: Windows virtual-key codes for the media keys. These are the same events
#: the physical keyboard buttons send — fully reversible (a wrong press is
#: undone by pressing again), no window focus needed, no interpreter, which
#: is what lets this ship on the same argument as open_in_browser while
#: typing/clicking/closing stays behind the เฟส 2ข confirm gate.
_MEDIA_KEYS = {
    "play_pause": 0xB3,
    "next_track": 0xB0,
    "previous_track": 0xB1,
    "volume_up": 0xAF,
    "volume_down": 0xAE,
    "mute": 0xAD,
}

#: Windows moves volume 2% per press; ten presses is a firm audible step.
#: The cap is the "อะไรนะ 50" rule again — a misheard count must not become
#: five minutes of the volume key auto-repeating.
_MAX_PRESSES = 25


def _press_media_key(vk: int) -> None:
    import ctypes

    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)     # key down
    ctypes.windll.user32.keybd_event(vk, 0, 2, 0)     # key up


@tool(
    name="media_control",
    description=(
        "กดปุ่มมีเดียของเครื่อง: เล่น/หยุดเพลงหรือวิดีโอ เพลงถัดไป เพลงก่อนหน้า "
        "เพิ่มเสียง ลดเสียง ปิดเสียง ใช้เมื่อเจ้าของสั่งเรื่องเพลง/วิดีโอ/เสียงของเครื่อง "
        "เพิ่มลดเสียงหนึ่งขั้นที่ได้ยินชัดใช้ times=5"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string",
                       "enum": sorted(_MEDIA_KEYS),
                       "description": "ปุ่มที่จะกด"},
            "times": {"type": "integer",
                      "description": "จำนวนครั้ง (ใช้กับเพิ่ม/ลดเสียง) ค่าเริ่มต้น 1"},
        },
        "required": ["action"],
    },
    tags=["computer"],
)
def media_control(action: str, times: int = 1) -> dict:
    vk = _MEDIA_KEYS.get(action)
    if vk is None:
        return {"ok": False, "error": "unknown action",
                "instruction": "ทำได้เฉพาะ: %s" % ", ".join(sorted(_MEDIA_KEYS))}
    try:
        times = max(1, min(int(times), _MAX_PRESSES))
    except (TypeError, ValueError):
        times = 1
    try:
        for _ in range(times):
            _press_media_key(vk)
    except Exception:
        logger.exception("media key %s failed", action)
        turnlog.record("media_control", action=action, ok=False)
        return {"ok": False, "error": "key press failed",
                "instruction": "กดปุ่มไม่สำเร็จ ให้บอกเจ้าของตรงๆ ห้ามบอกว่าทำแล้ว"}
    turnlog.record("media_control", action=action, times=times, ok=True)
    return {
        "ok": True, "pressed": action, "times": times,
        # The key is fire-and-forget: which app (if any) responded is
        # unknowable from here. The model must not narrate a music state it
        # cannot see — the mock-reported-as-ok lesson, one peripheral over.
        "note": ("ส่งปุ่มแล้ว แต่คุณไม่รู้ว่ามีแอปเพลงเปิดอยู่ไหมและแอปไหนรับ "
                 "ให้พูดว่า 'กดปุ่มให้แล้วค่ะ' ห้ามพูดว่า 'เปิดเพลงให้แล้ว' "
                 "ถ้าไม่มีเสียงเพลงดัง แปลว่าไม่มีแอปเพลงเปิดอยู่ ให้เสนอเปิดยูทูบแทน"),
    }


# ==================== opening and closing programs ====================

#: Where Windows keeps the shortcuts for everything installed. Resolving the
#: owner's spoken name against THIS list — instead of executing whatever
#: string arrived — is the whole security model of open_program: no paths,
#: no arguments, no shell, only launch targets the machine itself lists.
def _start_menu_dirs():
    import os

    return [
        Path(os.environ.get("ProgramData", r"C:\ProgramData"))
        / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
    ]


def _installed_apps() -> dict:
    """Friendly name (lowercased) -> shortcut path. Rebuilt per call: a
    glob over two folders is milliseconds, and a cache would miss the app
    the owner installed five minutes ago."""
    apps: dict = {}
    for d in _start_menu_dirs():
        if not d.is_dir():
            continue
        for lnk in d.rglob("*.lnk"):
            name = lnk.stem.lower()
            # The Start Menu lists uninstallers right next to the apps they
            # remove. "เปิด xxx" resolving to "Uninstall xxx" is the one way
            # this launcher reaches something irreversible, so those
            # shortcuts simply don't exist as far as this tool can see.
            if "uninstall" in name or "ถอนการติดตั้ง" in name or "remove " in name:
                continue
            apps.setdefault(name, lnk)
    return apps


_NAME_TOKEN = None  # set below; simple import-order convenience


@tool(
    name="open_program",
    description=(
        "เปิดโปรแกรมบนคอมพิวเตอร์ของเจ้าของ ระบุชื่อโปรแกรม เช่น notepad, spotify, "
        "line, word, chrome ถ้าเจอหลายตัวจะได้รายชื่อกลับไปให้ถามเจ้าของ"
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "ชื่อโปรแกรม (ภาษาอังกฤษตามชื่อจริงของแอป)"},
        },
        "required": ["name"],
    },
    tags=["computer"],
)
def open_program(name: str) -> dict:
    import os
    import re

    q = (name or "").strip().lower()
    if not q or re.search(r"[\/:]", q):
        # A path is not a program name. Accepting one turns this into
        # "execute any file", which is the 2ข gate's job to guard, not ours.
        return {"ok": False, "error": "not a program name",
                "instruction": "บอกชื่อโปรแกรมเฉยๆ ไม่รับ path"}

    apps = _installed_apps()
    matches = sorted(n for n in apps if q in n)
    exact = [n for n in matches if n == q]
    if exact:
        matches = exact
    if len(matches) == 1:
        target = matches[0]
        try:
            os.startfile(str(apps[target]))
        except Exception:
            logger.exception("could not launch %r", target)
            turnlog.record("open_program", name=target, ok=False)
            return {"ok": False, "error": "launch failed",
                    "instruction": "เปิดไม่สำเร็จ ให้บอกเจ้าของตรงๆ ห้ามบอกว่าเปิดแล้ว"}
        turnlog.record("open_program", name=target, ok=True)
        return {"ok": True, "opened": target}
    if len(matches) > 1:
        return {"ok": False, "error": "ambiguous",
                "candidates": matches[:8],
                "instruction": "เจอหลายโปรแกรม ให้ถามเจ้าของว่าหมายถึงตัวไหน"}

    # Nothing in the Start Menu. A bare well-known token (notepad, calc)
    # still launches through ShellExecute's own lookup — one word, no
    # arguments possible, so there is nothing to inject into.
    if re.fullmatch(r"[a-z0-9.+_-]{2,40}", q):
        try:
            os.startfile(q)
            turnlog.record("open_program", name=q, ok=True)
            return {"ok": True, "opened": q}
        except OSError:
            pass
    near = sorted(n for n in apps if any(w in n for w in q.split()))[:8]
    turnlog.record("open_program", name=q, ok=False)
    return {"ok": False, "error": "not found",
            "similar": near,
            "instruction": "ไม่พบโปรแกรมชื่อนี้ ให้บอกเจ้าของตรงๆ และอ่านชื่อใกล้เคียงให้ฟังถ้ามี"}


@tool(
    name="close_program",
    description=(
        "ปิดโปรแกรมแบบสุภาพ (เหมือนกดปุ่มกากบาท ถ้ามีงานไม่เซฟ โปรแกรมจะถามเอง) "
        "ระบุชื่อโปรเซส เช่น notepad, spotify, chrome"
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "ชื่อโปรแกรม/โปรเซส"},
        },
        "required": ["name"],
    },
    tags=["computer"],
)
def close_program(name: str) -> dict:
    import re
    import subprocess

    q = (name or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9.+_-]{2,40}", q):
        return {"ok": False, "error": "not a process name",
                "instruction": "บอกชื่อโปรแกรมเฉยๆ ไม่รับ path หรืออักขระพิเศษ"}
    image = q if q.endswith(".exe") else q + ".exe"
    # taskkill WITHOUT /F: a WM_CLOSE, the same event the X button sends,
    # so an app holding unsaved work gets to raise its own save dialog.
    # /F is the line this tool must never cross — force-killing is exactly
    # the unrecoverable action the เฟส 2ข confirm gate exists for.
    result = subprocess.run(
        ["taskkill", "/IM", image],
        capture_output=True, encoding="utf-8", errors="replace", timeout=10,
    )
    if result.returncode == 0:
        turnlog.record("close_program", name=image, ok=True)
        return {"ok": True, "close_requested": image,
                "note": ("ส่งคำสั่งปิดแล้ว ถ้าโปรแกรมมีงานไม่เซฟ จะมีกล่องถามเซฟเด้งบนจอ "
                         "ให้บอกเจ้าของว่าอาจต้องไปกดเอง")}
    if result.returncode == 128:
        turnlog.record("close_program", name=image, ok=False)
        return {"ok": False, "error": "not running",
                "instruction": "โปรแกรมนี้ไม่ได้เปิดอยู่ ให้บอกเจ้าของตรงๆ"}
    turnlog.record("close_program", name=image, ok=False)
    return {"ok": False, "error": "close failed",
            "instruction": "ปิดไม่สำเร็จ (อาจต้องสิทธิ์สูงกว่า) ให้บอกเจ้าของตรงๆ ห้ามบอกว่าปิดแล้ว"}


@tool(
    name="play_youtube",
    description=(
        "เปิดวิดีโอ/เพลงจากยูทูบให้เล่นเลย โดยหาอันดับแรกของผลค้นให้อัตโนมัติ "
        "ใช้เมื่อเจ้าของขอเพลงหรือวิดีโอ เช่น 'เปิดเพลงรัก' 'ขอ lofi' "
        "ถ้าอยากเห็นหน้าผลค้นทั้งหมดค่อยใช้ open_in_browser"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "ชื่อเพลง วิดีโอ หรือคำค้น"},
        },
        "required": ["query"],
    },
    tags=["computer"],
)
def play_youtube(query: str) -> dict:
    """First hit, played directly. Born from a real exchange: "เอาอันแรกเลย"
    answered with "จัดไปค่ะ เปิดอันแรกให้แล้ว" — a claim, not an action,
    because the model has no way to click a result it cannot see. Resolving
    the first result server-side removes the situation where that lie is
    even tempting: the video the search page would list first simply opens.
    """
    import os
    import re as _re

    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "empty query"}
    video_id = title = None
    try:
        import httpx

        r = httpx.get(
            "https://www.youtube.com/results", params={"search_query": q},
            headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "th-TH,th"},
            timeout=10, follow_redirects=True,
        )
        m = _re.search(
            r'"videoId":"([\w-]{11})".*?"title":\{"runs":\[\{"text":"(.*?)"',
            r.text, _re.S,
        )
        if m:
            video_id, title = m.group(1), m.group(2)
    except Exception:
        logger.warning("youtube lookup failed for %r", q, exc_info=True)

    if video_id:
        url = "https://www.youtube.com/watch?v=" + video_id
        try:
            os.startfile(url)
        except Exception:
            logger.exception("could not open %r", url)
            return {"ok": False, "error": "could not open",
                    "instruction": "เปิดเบราว์เซอร์ไม่สำเร็จ ให้บอกเจ้าของตรงๆ"}
        turnlog.record("play_youtube", query=q, video=video_id)
        return {"ok": True, "playing": title or q, "url": url,
                "note": "เปิดวิดีโอตัวแรกของผลค้นแล้ว บอกชื่อคลิปให้เจ้าของฟังสั้นๆ"}

    # Couldn't resolve a video (network, layout change): fall back to the
    # search page and SAY it is the search page — never claim a video plays.
    fallback = "https://www.youtube.com/results?search_query=" + q.replace(" ", "+")
    try:
        os.startfile(fallback)
    except Exception:
        return {"ok": False, "error": "could not open",
                "instruction": "เปิดเบราว์เซอร์ไม่สำเร็จ ให้บอกเจ้าของตรงๆ"}
    turnlog.record("play_youtube", query=q, video=None)
    return {"ok": True, "playing": None, "opened_search_page": True,
            "instruction": ("หาอันแรกให้อัตโนมัติไม่ได้ เลยเปิดหน้าผลค้นแทน "
                            "บอกเจ้าของตรงๆ ว่าเปิดหน้าค้นให้ ให้เขาเลือกเอง "
                            "ห้ามบอกว่าเปิดวิดีโอแล้ว")}


@tool(
    name="end_conversation",
    description=(
        "จบบทสนทนาและวางสาย ใช้เมื่อเจ้าของบอกลา เช่น 'พอแล้ว' 'บาย' 'แค่นี้แหละ' "
        "'ไปได้แล้ว' หลังเรียกแล้วให้กล่าวลาสั้นๆ หนึ่งประโยค ระบบจะรอให้พูดจบก่อนวาย "
        "แล้วกลับไปรอเรียกชื่อ คำถามธรรมดาไม่ใช่คำบอกลา"
    ),
    parameters={"type": "object", "properties": {}},
    tags=["computer"],
)
async def end_conversation() -> dict:
    """Voice hang-up: what makes the End button a fire escape instead of a
    daily control. The close is deferred until the goodbye has actually been
    heard — closing on tool-return would cut the farewell mid-word, the
    audio-lead rule in its smallest form. The browser is told "farewell"
    first so auto-connect parks in standby instead of redialing a session
    the owner just ended.
    """
    import asyncio

    from app import display
    from app import session as session_module

    live = session_module._active
    if live is None:
        return {"ok": True, "note": "ไม่มีสายให้วาง"}

    async def _hang_up_after_goodbye():
        await asyncio.sleep(1.0)               # let the goodbye start flowing
        await display.wait_until_heard(max_wait=20.0, then_pause=0.5)
        if session_module._active is not live:
            return                              # someone else took over
        try:
            await live._send_json({"type": "farewell"})
        except Exception:
            pass
        try:
            await live.ws.close()
        except Exception:
            pass

    asyncio.create_task(_hang_up_after_goodbye())
    from app import turnlog as _tl

    _tl.record("end_conversation")
    return {"ok": True, "instruction": "กล่าวลาสั้นๆ หนึ่งประโยค แล้วหยุด"}
