"""A clip on the room's big screen, during the event's opening ceremony.

Owner 5 Nov 2026: "ช่วงเปิดพิธีจะฟังเสียงคนเดียว และก็เปิดคลิป เปิดทีวี พูดออกมา
อาจจะมีซีนก่อน". The TV is the robot's own page (app/room_screens.py opens it, the
room PC's agent opens nothing else), so a clip is a message to that page
(app/face_mirror.py) and a file behind its cookie (app/mirror_server.py):

* **Staff or the host pick a clip by its id in the event file** (`clips`), never a
  file or an address -- the `show_unit(room)` rule. A file outside data/events/clips/
  is not a clip.
* **The whole clip is on the room PC before it is needed.** Robot to room PC is the
  robot's Wi-Fi, and the multimedia room is a known dead zone (CLAUDE.md): the page
  fetches it into memory as soon as the screen is up in a ceremony, so a Wi-Fi hole
  mid-clip costs nothing. Cued before that finished, it streams rather than not
  playing, and the staff page said it was not ready.
* **Nothing is claimed the screen did not report.** The page says playing / ended /
  error (POST /event-clip-state, its own cookie); no "playing" in ACK_WAIT_S = failed
  on the staff page, her voice back, nothing said about it to the room. The socket to
  the screen stays one-way (face_mirror).
* **She says nothing over it** (`quiet`, read by the reply gate in providers/gemini.py):
  a narrator on the TV is a voice too, and the door may be failing open. Her tool calls
  still run, so the host can still stop it.
* **An ended clip is not left to the page's word alone**: ACK + duration (the page's,
  else the file's `seconds`, else MAX_CLIP_S) + GRACE_S and her voice comes back anyway.
  A browser that died mid-clip must not leave a mute robot.

Nothing here has run on the robot or on the room's TV yet (docs/emma-event-rehearsal.md).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import time
from pathlib import Path

from app import face_mirror, turnlog

logger = logging.getLogger("condo_voice.event_clips")

ROOT = Path(__file__).resolve().parent.parent
#: The only folder a clip may come from. gitignored; `robot_server.py deploy`
#: ships what the event file names.
CLIPS_DIR = ROOT / "data" / "events" / "clips"
TYPES = {".mp4": "video/mp4", ".webm": "video/webm"}

#: Opening a kiosk on the room PC and the page's socket: measured 28 Oct, ~2-3 s of
#: window + the page's three.js. Generous, because the alternative is no clip.
CONNECT_WAIT_S = 25.0
#: The cue to the page's "playing": a decode start, on a PC we have not timed.
ACK_WAIT_S = 12.0
#: With no duration from anywhere. Longer than any opening clip; her voice comes back.
MAX_CLIP_S = 1800.0
GRACE_S = 8.0
#: Her line before the clip: it starts within this of the text, or there is none...
LEAD_IN_START_S = 8.0
#: ...and the clip rolls by this long whatever she is doing.
LEAD_IN_MAX_S = 40.0

IDLE, OPENING, ASKED, PLAYING, ENDED, STOPPED, FAILED = (
    "idle", "opening", "asked", "playing", "ended", "stopped", "failed")
_ACTIVE = (OPENING, ASKED, PLAYING)
REPORTS = {"ready", "fetch_failed", "playing", "ended", "error"}

ERRORS_TH = {
    "no_clip": "ไม่พบคลิปนี้ในไฟล์งาน", "no_file": "ยังไม่มีไฟล์คลิปนี้ในเครื่องหุ่น",
    "no_screen": "ยังไม่ได้ตั้งจอใหญ่ (ROOM_SCREENS / MIRROR_PORT)",
    "screen_refused": "เครื่องของจอใหญ่ไม่ตอบ", "no_page": "จอใหญ่เปิดแล้วแต่หน้าเล่นคลิปไม่เชื่อม",
    "no_ack": "จอใหญ่ไม่เริ่มเล่นคลิป", "error": "จอใหญ่เล่นคลิปไม่ได้",
}

STATE: dict = {"seq": 0, "phase": IDLE, "clip": None, "by": None, "at": None, "duration": None,
               "error": None, "ready": {}, "task": None, "playing": None, "ended": None}
_hashes: dict = {}


def reset() -> None:
    task = STATE.get("task")
    if task is not None and not task.done():
        try:
            task.cancel()
        except RuntimeError:             # its loop has closed (a test before this one)
            pass
    STATE.update(seq=0, phase=IDLE, clip=None, by=None, at=None, duration=None, error=None,
                 ready={}, task=None, playing=None, ended=None)
    _hashes.clear()


# ---- the table ------------------------------------------------------------------------

def _doc() -> dict:
    from app import event_host

    return event_host.load() or {}


def _clip_path(name) -> Path | None:
    """data/events/clips/<file> and nothing else."""
    if not isinstance(name, str) or not name.strip():
        return None
    path = Path(name.strip())
    path = (path if path.is_absolute() else ROOT / path).resolve()
    if path.suffix.lower() not in TYPES:
        return None
    try:
        path.relative_to(Path(CLIPS_DIR).resolve())
    except ValueError:
        logger.warning("event clip %s is outside %s: not a clip", name, CLIPS_DIR)
        return None
    return path


def table() -> dict[str, dict]:
    out = {}
    for cid, raw in (_doc().get("clips") or {}).items():
        path = _clip_path(raw.get("file"))
        out[cid] = {"id": cid, "title_th": raw.get("title_th") or cid, "path": path,
                    "has_file": path is not None and path.is_file(),
                    "seconds": raw.get("seconds"), "after_th": raw.get("after_th")}
    return out


def _sha(path: Path) -> str:
    """Pinned to what is on disk now; cached on (mtime, size): a clip is ~100 MB and
    a screen asks for it once, the table on every staff poll."""
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    found = _hashes.get(key)
    if found is None:
        digest = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        found = digest.hexdigest()
        _hashes[key] = found
    return found


def path_for(sha: str) -> tuple[Path, str] | None:
    """(file, media type) of the clip with this hash, for the screen's fetch. Off the loop."""
    for clip in table().values():
        if clip["has_file"] and _sha(clip["path"]) == sha:
            return clip["path"], TYPES[clip["path"].suffix.lower()]
    return None


def offered() -> bool:
    """The host's tool is shown and staff's buttons work: a file, and a screen to put it on."""
    from app import room_screens

    return room_screens.offered() and any(c["has_file"] for c in table().values())


async def _rows() -> list[dict]:
    rows = []
    for clip in table().values():
        if clip["has_file"]:
            sha = await asyncio.to_thread(_sha, clip["path"])
            rows.append({"id": clip["id"], "url": "/event-clip/" + sha})
    return rows


async def prepare() -> None:
    """The screen starts fetching every clip of the event, ahead of its cue. A screen
    that connects later is told too (face_mirror.snapshot)."""
    rows = await _rows()
    if rows:
        face_mirror.clip_prepare(rows)


# ---- the clip --------------------------------------------------------------------------

def quiet() -> bool:
    """Her voice is kept in: from the cue until the screen says it ended."""
    return STATE["phase"] in (ASKED, PLAYING)


def active() -> bool:
    return STATE["phase"] in _ACTIVE


class _Failed(Exception):
    pass


async def _until(cond, wait_s: float) -> bool:
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if cond():
            return True
        await asyncio.sleep(0.2)
    return bool(cond())


async def _after_her_voice() -> None:
    """A scene's "please watch" and the host's "ค่ะ" first: the clip must not cut her off."""
    from app import session as session_module

    started = time.monotonic()
    spoke = False
    while time.monotonic() - started < LEAD_IN_MAX_S:
        session = session_module._active
        if session is None:
            return
        now = time.monotonic()
        if session._array_audible_until(now) > now:
            spoke = True
        elif spoke or now - started >= LEAD_IN_START_S:
            return
        await asyncio.sleep(0.2)


async def play(clip_id: str, *, by: str = "staff", lead_in: bool = False, on_end=None) -> dict:
    """Start a clip; returns at once (a tool call and a phone's POST are both waiting).
    `on_end(clip, phase)`: after it ended or failed, unless something replaced it."""
    from app import room_screens

    clip = table().get(clip_id)
    why = ("no_clip" if clip is None else "no_file" if not clip["has_file"]
           else "no_screen" if not room_screens.offered() else None)
    if why is not None:
        return {"ok": False, "error": why, "error_th": ERRORS_TH[why]}
    await stop("replaced")
    STATE["seq"] += 1
    seq = STATE["seq"]
    STATE.update(phase=OPENING, clip=clip_id, by=by, at=None, duration=None, error=None,
                 playing=asyncio.Event(), ended=asyncio.Event())
    turnlog.record("event_clip", phase="cued", clip=clip_id, by=by)
    STATE["task"] = asyncio.create_task(_run(seq, clip, lead_in, on_end))
    return {"ok": True, "clip": clip_id, "title_th": clip["title_th"]}


async def _run(seq: int, clip: dict, lead_in: bool, on_end) -> None:
    from app import room_screens

    end = ENDED
    try:
        if not face_mirror.watching():
            opened = await room_screens.open_screen(_doc().get("screen"), by="scene")
            if not opened.get("ok"):
                raise _Failed("screen_refused")
        if not await _until(face_mirror.watching, CONNECT_WAIT_S):
            raise _Failed("no_page")
        await prepare()
        if lead_in:
            await _after_her_voice()
        if STATE["seq"] != seq:
            return
        STATE["phase"] = ASKED
        sha = await asyncio.to_thread(_sha, clip["path"])
        face_mirror.clip_play({"seq": seq, "id": clip["id"], "url": "/event-clip/" + sha})
        try:
            await asyncio.wait_for(STATE["playing"].wait(), ACK_WAIT_S)
        except asyncio.TimeoutError:
            raise _Failed("no_ack") from None
        if STATE["error"]:
            raise _Failed(STATE["error"])
        STATE["phase"] = PLAYING
        STATE["at"] = time.monotonic()
        turnlog.record("event_clip", phase="playing", clip=clip["id"], duration_s=STATE["duration"])
        limit = STATE["duration"] or clip.get("seconds") or MAX_CLIP_S
        try:
            await asyncio.wait_for(STATE["ended"].wait(), float(limit) + GRACE_S)
        except asyncio.TimeoutError:
            logger.warning("event clip %s: no word from the screen after %.0f s", clip["id"], limit)
            face_mirror.clip_stop()
        if STATE["error"]:
            raise _Failed(STATE["error"])
    except _Failed as failed:
        why = str(failed)
        end = FAILED
        STATE["error"] = why
        face_mirror.clip_stop()
        logger.warning("event clip %s failed: %s", clip["id"], why)
    except asyncio.CancelledError:
        raise
    except Exception:                                    # noqa: BLE001 -- her voice must come back
        logger.exception("event clip %s", clip["id"])
        end = FAILED
        STATE["error"] = "error"
        face_mirror.clip_stop()
    if STATE["seq"] != seq:
        return
    STATE["phase"] = end
    turnlog.record("event_clip", phase=end, clip=clip["id"], why=STATE["error"])
    if on_end is not None:
        try:
            await on_end(clip, end)
        except Exception:                                # noqa: BLE001
            logger.exception("event clip %s: its ending", clip["id"])


async def stop(why: str = "staff") -> bool:
    """Off the screen, her voice back. A clip not running is not an error."""
    if not active():
        return False
    STATE["seq"] += 1
    clip = STATE["clip"]
    task = STATE.get("task")
    STATE.update(phase=STOPPED, task=None)
    face_mirror.clip_stop()
    if task is not None and not task.done() and task is not asyncio.current_task():
        task.cancel()
    turnlog.record("event_clip", phase="stopped", clip=clip, why=why)
    return True


def report(seq, clip_id, state, duration=None) -> bool:
    """What the screen says (POST /event-clip-state). Only these words, only about this cue."""
    if state not in REPORTS or not isinstance(clip_id, str):
        return False
    if state in ("ready", "fetch_failed"):
        if clip_id in table():
            STATE["ready"][clip_id] = state == "ready"
            turnlog.record("event_clip", phase=state, clip=clip_id)
            return True
        return False
    if seq != STATE["seq"] or clip_id != STATE["clip"] or not active():
        return False
    if state == "playing":
        if isinstance(duration, (int, float)) and not isinstance(duration, bool) and (
                math.isfinite(duration) and 0 < duration <= MAX_CLIP_S):
            STATE["duration"] = float(duration)
        STATE["playing"].set()
    elif state == "ended":
        STATE["ended"].set()
    else:
        STATE["error"] = "error"
        STATE["playing"].set()
        STATE["ended"].set()
    return True


def snapshot() -> dict:
    """The staff page's card."""
    from app import room_screens

    clips = table()
    current = clips.get(STATE["clip"]) if STATE["clip"] else None
    elapsed = None if STATE["at"] is None or STATE["phase"] != PLAYING else round(time.monotonic() - STATE["at"])
    return {"phase": STATE["phase"], "clip": STATE["clip"], "by": STATE["by"],
            "title_th": current["title_th"] if current else None,
            "elapsed_s": elapsed, "duration_s": STATE["duration"],
            "error_th": ERRORS_TH.get(STATE["error"]) if STATE["phase"] == FAILED else None,
            "screen_ready": room_screens.offered(), "screen_up": face_mirror.watching(),
            "clips": [{"id": c["id"], "title_th": c["title_th"], "has_file": c["has_file"],
                       "ready": STATE["ready"].get(c["id"])} for c in clips.values()]}
