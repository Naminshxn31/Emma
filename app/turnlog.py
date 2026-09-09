"""One line of JSON per turn, so a bad demo can be read instead of guessed at.

Every bug in this project so far was diagnosed from a screenshot the operator
happened to take and a console log they happened to copy. That worked, but it
only works for bugs someone is watching when they happen — and the ones that
matter in a sales gallery happen while everyone is busy with a customer.

What goes in is chosen to answer the questions that actually got asked:

    "ทำไมตอบสไลด์ไม่ตรง"      -> caption, tool_query, hits, displayed
    "ทำไมภาพมาก่อนเสียง"        -> audio_lead_ms at the moment of the change
    "ทำไมไม่ไปหน้าต่อไป"        -> tool calls and their results, in order
    "มันพูดอะไรที่ไม่มีในบท"     -> the assistant caption next to the slide id

**This records what customers say.** The transcripts are the point — they are
what makes a mis-heard "ice bath" traceable — but they are also strangers'
speech written to disk in a public showroom. So: no audio is ever written,
only text; the file is line-delimited and easy to inspect or delete; and
`TURN_LOG=false` turns the whole thing off without touching code.
"""
from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar
from threading import RLock
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger("condo_voice.turnlog")

_fh = None
_path: Path | None = None
_warned = False
session_id: ContextVar[str | None] = ContextVar("turnlog_session_id", default=None)
_write_lock = RLock()


def _forget_old_logs(folder: Path) -> None:
    """Delete logs past their keep-days. Never raises.

    The turn log exists to answer "what actually happened", and the honest
    version of that is "recently". Every line is something a visitor said in a
    sales gallery, recorded without them being asked — a debugging tool that
    quietly became an indefinite archive of members of the public.

    Dates come from the filename rather than the filesystem, because a
    mtime is changed by copying the folder, restoring a backup, or a sync
    client touching it, and none of those should reset a retention clock.

    `TURN_LOG_KEEP_DAYS=0` keeps everything, for anyone who has a reason to.
    """
    keep = settings.turn_log_keep_days
    if keep <= 0:
        return
    cutoff = time.time() - keep * 86400
    for old in folder.glob("*.jsonl"):
        try:
            when = time.mktime(time.strptime(old.stem, "%Y-%m-%d"))
        except ValueError:
            continue          # not one of ours; leave it alone
        if when >= cutoff:
            continue
        try:
            old.unlink()
            logger.info("removed turn log older than %d days: %s", keep, old.name)
        except Exception:
            logger.warning("could not remove the old turn log %s", old, exc_info=True)


def _handle():
    """Open the day's file lazily. Returns None when logging is off."""
    global _fh, _path, _warned

    if not settings.turn_log:
        return None

    day = time.strftime("%Y-%m-%d")
    want = Path(settings.turn_log_dir).expanduser() / f"{day}.jsonl"
    if _fh is not None and _path == want:
        return _fh

    # Rolled past midnight, or first write of the process.
    if _fh is not None:
        try:
            _fh.close()
        except Exception:
            pass
        _fh = None

    try:
        want.parent.mkdir(parents=True, exist_ok=True)
        _fh = want.open("a", encoding="utf-8")
        _path = want
        # Only here, which is once per process and once per midnight. These
        # files hold what visitors said out loud, so keeping them forever is a
        # decision — and it was being made by default, silently, because
        # nothing ever deleted one.
        _forget_old_logs(want.parent)
        return _fh
    except Exception:
        # A read-only disk must not take the robot down mid-conversation.
        # Warn once — a warning per turn would itself become the problem.
        if not _warned:
            logger.warning("could not open the turn log at %s — continuing without it", want)
            _warned = True
        return None


def record(event: str, **fields: Any) -> None:
    """Append one event. Never raises: this is instrumentation, not the job."""
    from app.robot_backend import active as simulation_backend

    if simulation_backend.get() is not None:
        # A strict allowlist keeps rehearsal metrics separate from transcripts.
        recorder = getattr(simulation_backend.get(), "record_diagnostic", None)
        if recorder is not None:
            recorder(event, fields, session_id.get())
        return
    # Worker tools may finish together. Protect opening/rotation and writing
    # as one operation so the first two events cannot overwrite the handle.
    with _write_lock:
        _record(event, fields)


def _record(event: str, fields: dict) -> None:
    fh = _handle()
    if fh is None:
        return
    row = {"t": time.strftime("%H:%M:%S"), "event": event}
    if (sid := session_id.get()) is not None:
        row["session_id"] = sid
    row.update({k: v for k, v in fields.items() if v is not None})
    try:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        fh.flush()      # a crash is exactly when the last line matters most
    except Exception:
        pass


def close() -> None:
    global _fh
    if _fh is not None:
        try:
            _fh.close()
        except Exception:
            pass
        _fh = None
