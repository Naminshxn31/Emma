"""
Keep query-string credentials out of uvicorn's log lines.

Pages authenticate in the query (`/ws/camera?token=...`, `/ws?...&token=`),
and uvicorn writes the full path of every WebSocket it accepts. A token on a
console that is copied into a log file is a reusable credential on disk.

Why this is installed from `app.main` and not only from run_server.py: with
DEV_RELOAD (uvicorn reload), the app runs in a SPAWNED worker process, and a
filter added in the launcher's process never reaches it. Measured 2026-09-29:
the worker's log carried `WebSocket /ws/camera?token=<the real token>
[accepted]` in the clear while run_server.py believed it had redacted it.
Installing on import of the app puts the filter in whichever process serves
requests — reload or not — and uvicorn has already configured logging by
then, so nothing resets it afterwards.
"""
from __future__ import annotations

import logging
import re

#: The loggers uvicorn writes request/socket lines to.
LOGGERS = ("uvicorn.error", "uvicorn.access")


class TokenRedactionFilter(logging.Filter):
    """Remove reusable query credentials from a record's message and args."""

    _TOKEN = re.compile(r"([?&]token=)[^&\s\"]+")

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._TOKEN.sub(r"\1***", v) if isinstance(v, str) else v
                               for k, v in record.args.items()}
            else:
                record.args = tuple(
                    self._TOKEN.sub(r"\1***", value) if isinstance(value, str) else value
                    for value in record.args
                )
        if isinstance(record.msg, str):
            record.msg = self._TOKEN.sub(r"\1***", record.msg)
        return True


def install() -> None:
    """Add the filter to uvicorn's loggers once (idempotent)."""
    for name in LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(f, TokenRedactionFilter) for f in logger.filters):
            logger.addFilter(TokenRedactionFilter())
