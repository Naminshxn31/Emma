"""Run the assistant using HOST/PORT/TLS settings from .env.

Unlike the bare ``uvicorn app.main:app`` command, uvicorn's CLI does not read
this project's HOST and PORT variables as bind options.  This entrypoint does.
"""
from __future__ import annotations

import os
import logging
import re
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

# Run from the repo root no matter what the launcher's working directory is.
# Relative paths — in .env and in the code (certs/, data/wake/silero_vad.onnx,
# data/logs, data/call_debug, …) — resolve against the process CWD. Started
# from elsewhere, the SSL cert crashed the boot (FileNotFoundError at
# load_cert_chain) and the Silero VAD model went "missing" and silently fell
# back to Gemini, at which point VAD_MIN_RMS does nothing. Both were the same
# wrong-CWD bug. Done before importing app.config so .env loads from here too.
os.chdir(Path(__file__).resolve().parent)

import uvicorn

from app.config import settings


class _TokenRedactionFilter(logging.Filter):
    """Remove reusable query credentials from Uvicorn's WebSocket lines."""

    _TOKEN = re.compile(r"([?&]token=)[^&\s\"]+")

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.args = tuple(
                self._TOKEN.sub(r"\1***", value) if isinstance(value, str) else value
                for value in record.args
            )
        if isinstance(record.msg, str):
            record.msg = self._TOKEN.sub(r"\1***", record.msg)
        return True


def _install_token_redaction() -> None:
    redact = _TokenRedactionFilter()
    for name in ("uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).addFilter(redact)


def _open_when_up(url: str, delay: float = 2.5) -> None:
    """Open the page once the server has had a moment to bind.

    Not the LAN address printed above it: browsers only grant microphone
    access on a secure origin, and `http://192.168.x.x` is not one. A
    launcher that opened the LAN link would hand somebody a page that looks
    right and cannot hear.

    And `localhost`, not `127.0.0.1`, though both are secure origins and the
    same machine. Permissions are stored *per origin* and those two are
    different origins, so a page opened on one has none of the grants given
    to the other. Read out of Chrome's own settings on the showroom machine
    after the first camera greeting was lost:

        http://localhost:8001   microphone allowed
        http://127.0.0.1:8001   (not listed)

    The page came up on the address with no grant, the standby ears were
    refused, and the only visible symptom was a line about the microphone on
    a screen nobody was reading yet.
    """
    def go() -> None:
        time.sleep(delay)
        webbrowser.open(url)

    threading.Thread(target=go, daemon=True).start()


def _lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No packet is sent; connect only asks Windows which interface it would use.
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "<computer-ip>"
    finally:
        sock.close()


def _resolve_repo_path(p: str) -> str | None:
    """A path from .env, resolved against this script's directory.

    SSL paths in .env are written relative to the repo (``certs/lan-cert.pem``).
    uvicorn hands a relative certfile to the OS, which looks it up under the
    *terminal's* working directory — so starting the server from anywhere but
    the project root failed with ``FileNotFoundError`` at ``load_cert_chain``.
    Resolving here against ``run_server.py``'s own location makes the start
    working-directory-independent.
    """
    from pathlib import Path

    p = p.strip()
    if not p:
        return None
    path = Path(p)
    return str(path if path.is_absolute() else (Path(__file__).resolve().parent / path))


if __name__ == "__main__":
    _install_token_redaction()
    cert = _resolve_repo_path(settings.ssl_certfile)
    key = _resolve_repo_path(settings.ssl_keyfile)
    scheme = "https" if cert and key else "http"
    base = f"{scheme}://{_lan_ip()}:{settings.port}"
    # Keep the token for the local --open URL, but never print it. Console
    # output is commonly redirected to a log and URL query strings then turn
    # into a reusable credential sitting on disk.
    q = f"?token={settings.ws_token}" if settings.ws_token else ""
    print(f"  talk to Emma : {base}/ (token configured: {bool(settings.ws_token)})")
    if not settings.ws_token:
        print("  (WS_TOKEN is empty - anyone on this network can open a session)")
    if not (cert and key):
        print(
            "NOTE: display/robot connections work over LAN HTTP, but browsers "
            "require trusted HTTPS to grant microphone access on a LAN IP."
        )
    if "--open" in sys.argv:
        # For the double-click launchers. A camera that wakes the robot needs
        # a page open to wake *into* — the summon rings standby browsers, and
        # with none of them there the greeting is a line in the log.
        _open_when_up(f"{scheme}://localhost:{settings.port}/{q}")
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        # Uvicorn includes the full query string in access lines. Control and
        # kiosk pages authenticate in that query, so request logging would
        # persist the credential on disk/stdout.
        access_log=False,
        ssl_certfile=cert,
        ssl_keyfile=key,
    )
