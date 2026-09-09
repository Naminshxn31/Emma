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
- **Only an authenticated caller.** This argument used to read "the server
  binds 127.0.0.1, so the only thing that can ask for this is the machine's
  own browser" — and it stopped being true the day `HOST=0.0.0.0` became the
  default and the machines were opened to the LAN. Two of the three reasons
  above survive that move untouched; this one did not, and a stale safety
  argument is worse than none, because the next person reads it as a check
  that is still being made. What holds the line now is `WS_TOKEN`
  (`_reject_unauthorized` in app/main.py), which every socket passes through
  — so keep it set on any machine that is not bound to loopback.

YouTube needs no special case: the model composes ordinary URLs
(`youtube.com/results?search_query=...`), and the description says so —
one tool, every site.
"""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

from app import turnlog
from app.tool_io import on_loop
from app.tools.registry import tool

logger = logging.getLogger("condo_voice.computer")


#: Sites with a documented embed form. Everything else sets X-Frame-Options
#: and renders inside a page as a white rectangle with no error anywhere —
#: which is why this is a short allow-list rather than "try it and see".
def embeddable(url: str) -> str | None:
    """The URL to put in a frame, or None if this one cannot be framed.

    The point of the whole feature is that the answer appears in the page
    the owner is already looking at, instead of a second window arriving on
    top of it. That works for exactly as much of the web as agrees to be
    framed, so the honest shape is a list of what does, plus a fallback that
    says out loud when something did not.
    """
    from urllib.parse import parse_qs, quote, urlparse

    u = urlparse((url or "").strip())
    host = u.netloc.lower().removeprefix("www.")

    if host in ("youtube.com", "m.youtube.com"):
        if u.path == "/watch":
            vid = (parse_qs(u.query).get("v") or [""])[0]
            # `playsinline` matters on the robot's Android panel: without it
            # the video takes over the whole screen and the rest of the page
            # — including the way back — is gone.
            return f"https://www.youtube.com/embed/{vid}?autoplay=1&playsinline=1" if vid else None
        if u.path.startswith("/embed/"):
            return url
        return None
    if host == "youtu.be" and len(u.path) > 1:
        return f"https://www.youtube.com/embed/{u.path[1:]}?autoplay=1&playsinline=1"
    if host in ("google.com", "maps.google.com") and u.path.startswith("/maps"):
        # Rewritten, not just suffixed. `?api=1` is Google's documented form
        # for *opening* Maps and it refuses to be framed — a grey rectangle
        # with a broken-page icon and nothing in the console that names the
        # cause. The old `maps?q=...&output=embed` form still frames without
        # an API key, so anything /maps-shaped is normalised onto it rather
        # than trusting whatever the model happened to compose.
        query = parse_qs(u.query)
        place = (query.get("query") or query.get("q") or [""])[0]
        if not place:
            # A place inside the path, e.g. /maps/place/Pattaya
            parts = [p for p in u.path.split("/") if p and p not in ("maps", "place", "search")]
            place = parts[0] if parts else ""
        if not place:
            return None
        return "https://maps.google.com/maps?q=" + quote(place) + "&output=embed"
    return None


def _put_on_screen(url: str) -> str | None:
    """Show `url` on the assistant's own window, if it has one.

    Returns the place it went ("screen"), or None when the caller should fall
    back to `os.startfile`. Which one happened has to reach the model, not
    just the log: "เปิดให้แล้วค่ะ" is a different sentence depending on which
    screen it landed on, and the owner is looking at one of them. Same rule
    as the mock/hardware split on the IR and robot tools — the reply must
    never describe something that did not happen.
    """
    from app.tools import webstage

    if not webstage.enabled():
        return None
    webstage.request(url)
    return "screen"


@tool(
    name="open_in_browser",
    description=(
        "เปิดหน้าเว็บบนจอ ใช้เมื่อเจ้าของบอก URL หรือชื่อเว็บที่ต้องการชัดเจน "
        "รับเฉพาะลิงก์ http/https เท่านั้น "
        # The rule below is here rather than in the prompt because a rule at
        # the end of a system prompt gets ignored and one attached to a tool
        # gets followed — the same finding that put KEEP_GOING on the slide
        # tool results. And it names the case that actually happened: "เปิด
        # YouTube" opened youtube.com, which cannot be embedded (Google sets
        # X-Frame-Options), so it arrived as a separate window the owner did
        # not want — on a robot panel, a front page with no keyboard to
        # search from is worse than useless.
        "ห้ามเปิดหน้าแรกของเว็บวิดีโอ เช่น youtube.com เปล่าๆ เด็ดขาด — "
        "ถ้าเจ้าของบอกแค่ว่า 'เปิดยูทูบ' โดยไม่บอกว่าจะดูอะไร ให้ถามกลับสั้นๆ "
        "ว่าอยากดูอะไร แล้วใช้ play_youtube แทน เพราะ play_youtube เล่นบนจอ "
        "ในหน้าเดิมได้ ส่วนหน้าแรกยูทูบต้องเปิดหน้าต่างแยกและกดอะไรไม่ได้ "
        # Maps was already handled by `embeddable()` and unreachable by
        # voice, because nothing told the model the capability existed. A
        # tool that can do something the model is never told about is the
        # same as one that cannot.
        "แผนที่ขึ้นบนจอในหน้าเดิมได้ ใช้ URL รูปแบบ "
        "https://maps.google.com/maps?q=ชื่อสถานที่ "
        "เมื่อเจ้าของถามว่าที่ไหน ไปยังไง หรือขอดูแผนที่"
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "ลิงก์เต็ม ขึ้นต้น http:// หรือ https://"},
        },
        "required": ["url"],
    },
    tags=["computer"],
    blocking=True,
)
def open_in_browser(url: str) -> dict:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        # The wall. file:// opens arbitrary files with their default apps,
        # ms-settings:/shell: reach into the OS — none of that is "open a
        # webpage", so none of it gets to ride on this tool.
        return {"ok": False, "error": "not a web url",
                "instruction": "เปิดได้เฉพาะลิงก์เว็บ http/https ให้บอกเจ้าของตรงๆ"}
    frame = embeddable(url)
    if frame:
        # In the page the owner is already looking at. A second window
        # arriving on top of it is the thing this exists to avoid.
        turnlog.record("open_browser", url=url, ok=True, where="stage")
        return {"ok": True, "opened": url, "where": "stage", "embed": frame,
                "instruction": "ขึ้นบนจอในหน้าเดิมแล้ว บอกเจ้าของสั้นๆ ห้ามบอกว่าเปิดหน้าต่างใหม่"}
    if _put_on_screen(url):
        turnlog.record("open_browser", url=url, ok=True, where="screen")
        return {"ok": True, "opened": url, "where": "screen",
                "instruction": ("เว็บนี้ฝังในหน้าไม่ได้ เลยเปิดเป็นหน้าต่างแยกให้ "
                                "บอกเจ้าของตรงๆ ว่าเปิดหน้าต่างใหม่ให้")}
    try:
        import os

        on_loop(os.startfile, url)  # Windows: default browser
    except Exception:
        logger.exception("could not open %r", url)
        turnlog.record("open_browser", url=url, ok=False)
        return {"ok": False, "error": "could not open",
                "instruction": "เปิดเบราว์เซอร์ไม่สำเร็จ ให้บอกเจ้าของตรงๆ ห้ามบอกว่าเปิดแล้ว"}
    turnlog.record("open_browser", url=url, ok=True, where="browser")
    return {"ok": True, "opened": url, "where": "browser"}


@tool(
    name="close_web_page",
    description=(
        "ปิดทุกอย่างที่กำลังแสดงบนจอ ทั้งการ์ดข้อมูลห้อง วิดีโอ แผนที่ สไลด์ "
        "และหน้าต่างเว็บที่เปิดแยก ใช้เมื่อเจ้าของบอกว่าพอแล้ว ปิดได้ ปิดจอ "
        "ปิดวิดีโอ เอาออก หรือไม่อยากดูแล้ว "
        "ห้ามเรียกเมื่อเขาแค่ถามคำถามอื่นหรือขอเปลี่ยนเรื่อง — ถามอย่างอื่นไม่ได้แปลว่าให้ปิด"
    ),
    parameters={"type": "object", "properties": {}},
    tags=["computer"],
)
async def close_web_page() -> dict:
    """Closing has to be sayable, or it cannot be asked for.

    `stop_presentation` exists because it once did not: a guest said "หยุด
    พรีเซนต์", the model answered "ได้ค่ะ หยุดแล้ว" and carried straight on,
    because talking was the only thing it could do. A window the assistant
    can open and cannot close is that same hole.

    The description's last line is the other half of that lesson: a *question*
    is not an instruction to stop. Code cannot enforce it — the model picks
    the tool — so the guard lives in the words it reads, naming the case that
    actually happened.
    """
    from app.tools import webstage

    # Two screens, one instruction. This tool used to know only about the
    # separate Chromium window, so "ปิดจอให้หน่อย" with a unit card up
    # answered "ตอนนี้ไม่มีหน้าเว็บเปิดอยู่บนจอค่ะ" — technically about the
    # window it was looking at, and plainly wrong to the person looking at
    # the card. Whatever is on screen is what "the screen" means.
    if webstage.enabled() and webstage.is_open():
        await webstage.close()
    # `screen: clear` reaches the page through the same tool-result channel
    # that puts things on the stage, so closing travels the route opening
    # already uses rather than growing a second one.
    return {"ok": True, "closed": True, "screen": "clear",
            "instruction": "ปิดจอให้แล้ว บอกสั้นๆ"}


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
    blocking=True,
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
    blocking=True,
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
            on_loop(os.startfile, str(apps[target]))
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

    # Nothing in the Start Menu means nothing to open. There used to be a
    # fallback here that handed a bare token to ShellExecute's own lookup
    # ("one word, no arguments, nothing to inject") — which is true, and
    # beside the point: "cmd", "powershell", "regedit", "diskpart" are all
    # one word, none of them is in the Start Menu, and this server sits on
    # 0.0.0.0 taking commands from a microphone. Notepad and Calculator
    # are Start Menu entries and still open through the list above; the
    # list is the allow-list, and the allow-list is the whole point.
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
    blocking=True,
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


def _youtube_allows_embedding(video_id: str) -> bool:
    """Ask YouTube itself, before the iframe finds out the hard way.

    oEmbed returns 200 only for a video that exists and permits embedding;
    401/403 is an uploader who forbade it, 404 is an id that never existed.
    Fail-open on network trouble: the check must never make things worse
    than the old behaviour, and the search request that produced the id
    just succeeded over the same network.
    """
    try:
        import httpx

        r = httpx.get(
            "https://www.youtube.com/oembed",
            params={"url": "https://www.youtube.com/watch?v=" + video_id,
                    "format": "json"},
            timeout=4, follow_redirects=True,
        )
        return r.status_code == 200
    except Exception:
        return True


@tool(
    name="play_youtube",
    description=(
        "เปิดวิดีโอ/เพลงจากยูทูบให้เล่นบนจอเลย โดยหาอันดับแรกของผลค้นให้อัตโนมัติ "
        "ใช้เมื่อเจ้าของขอเพลงหรือวิดีโอ เช่น 'เปิดเพลงรัก' 'ขอ lofi' "
        # Seen live: "สุ่มเพลง" produced no tool call at all, and the reply
        # was "จัดไปค่ะ! เอ็มม่าเปิดเพลงฮิตยุค 2000s แบบสุ่มให้ฟังบนจอแล้วนะคะ"
        # over a screen with nothing on it. The model had no *query* to pass,
        # so it narrated the outcome instead of producing one. Saying "invent
        # the search term yourself" is what turns that into a call.
        "ถ้าเจ้าของไม่ได้ระบุเพลง เช่นบอกว่า 'สุ่มเพลง' 'เปิดเพลงอะไรก็ได้' "
        "ให้คุณคิดคำค้นเองแล้วเรียกเครื่องมือนี้ทันที ห้ามถามกลับ ห้ามข้ามการเรียก "
        # The exact sentence it said, quoted back at it. Documented in this
        # project as the only phrasing that works: "ห้ามเกริ่นล่วงหน้า" did
        # nothing; quoting the words about to be typed does.
        "ห้ามพูดว่า 'เปิดให้ฟังแล้ว' หรือ 'จัดไปค่ะ เปิดให้แล้ว' ถ้ายังไม่ได้เรียก "
        "เครื่องมือนี้และยังไม่ได้ผลลัพธ์กลับมา — เพลงจะไม่ขึ้นบนจอ แล้วเจ้าของจะบอกว่า "
        "ไม่เห็นขึ้นเลย "
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
    blocking=True,
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
    candidates: list[tuple[str, str]] = []
    try:
        import httpx

        r = httpx.get(
            "https://www.youtube.com/results", params={"search_query": q},
            headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "th-TH,th"},
            timeout=10, follow_redirects=True,
        )
        for m in _re.finditer(
            r'"videoId":"([\w-]{11})".*?"title":\{"runs":\[\{"text":"(.*?)"',
            r.text, _re.S,
        ):
            if m.group(1) not in (c[0] for c in candidates):
                candidates.append((m.group(1), m.group(2)))
            if len(candidates) >= 3:
                break
    except Exception:
        logger.warning("youtube lookup failed for %r", q, exc_info=True)

    # The first *playable* hit, not the first hit. Uploaders can forbid
    # embedding per video, and the iframe then renders "Video unavailable"
    # over a black screen — seen live 2026-08-26, repeatedly, because music
    # labels forbid it often. YouTube's own oEmbed endpoint answers 200 only
    # for a video that exists AND allows embedding, so each candidate is
    # asked before it is put on screen.
    video_id = title = None
    can_embed = False
    for vid, ti in candidates:
        if _youtube_allows_embedding(vid):
            video_id, title, can_embed = vid, ti, True
            break
    if video_id is None and candidates:
        # Real videos, all embed-forbidden: play the first in its own
        # window instead (a normal browser page plays anything).
        video_id, title = candidates[0]

    if video_id:
        url = "https://www.youtube.com/watch?v=" + video_id
        frame = embeddable(url) if can_embed else None
        if frame:
            turnlog.record("play_youtube", query=q, video=video_id, where="stage")
            return {"ok": True, "playing": title or q, "url": url,
                    "where": "stage", "embed": frame,
                    "note": "เล่นบนจอในหน้าเดิมแล้ว บอกชื่อคลิปให้เจ้าของฟังสั้นๆ"}
        if _put_on_screen(url):
            turnlog.record("play_youtube", query=q, video=video_id, where="screen")
            return {"ok": True, "playing": title or q, "url": url, "where": "screen",
                    "note": "เล่นบนจอแล้ว บอกชื่อคลิปให้เจ้าของฟังสั้นๆ"}
        try:
            on_loop(os.startfile, url)
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
    if _put_on_screen(fallback):
        turnlog.record("play_youtube", query=q, video=None, where="screen")
        return {"ok": True, "playing": None, "opened_search_page": True,
                "where": "screen",
                "instruction": ("หาอันแรกให้อัตโนมัติไม่ได้ เลยขึ้นหน้าผลค้นบนจอแทน "
                                "บอกเจ้าของตรงๆ ว่าขึ้นหน้าค้นให้ ให้เขาเลือกเอง "
                                "ห้ามบอกว่าเล่นวิดีโอแล้ว")}
    try:
        on_loop(os.startfile, fallback)
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

    from app import display, heard
    from app import session as session_module

    # A guard on the consequence, not a guess about the audio. Measured on
    # 2026-08-24: four of the day's seven hangups followed `我们走吧。` — the
    # same canned sentence, byte-identical, that a recogniser produces from
    # silence when zh-CN is in its hint list. Nobody said it, and the line
    # went anyway, mid-conversation, with nothing to correct afterwards.
    #
    # Dropping the language hint removes today's phrase; this removes the
    # class. The destructive slide tools already work this way — "we cannot
    # tell echo from speech, but we can tell ปิดสไลด์ from bit like".
    if not heard.asks_to_end():
        return {"ok": False, "error": "no goodbye heard",
                "heard": heard.last(), "instruction": heard.ASK_BEFORE_ENDING}

    live = session_module._active
    if live is None:
        return {"ok": True, "note": "ไม่มีสายให้วาง"}

    async def _hang_up_after_goodbye():
        # Wait for the goodbye to *begin*, not a fixed beat. The old
        # `sleep(1.0)` was a guess-clock: when the model took longer than a
        # second to start speaking (it usually does), the audio queue was
        # still empty at the check, wait_until_heard returned instantly, and
        # the line closed under the farewell — "ตอนไล่จะพูดไม่จบแล้วตัดไป",
        # reported 2026-08-26. So: watch for audio to actually start flowing
        # (bounded, in case the model says nothing at all), then wait for it
        # to be heard.
        for _ in range(32):                    # up to ~8s for speech to start
            await asyncio.sleep(0.25)
            if display.remaining_lead() > 0:
                break
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
