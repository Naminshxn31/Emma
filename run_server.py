"""Run the assistant using HOST/PORT/TLS settings from .env.

Unlike the bare ``uvicorn app.main:app`` command, uvicorn's CLI does not read
this project's HOST and PORT variables as bind options.  This entrypoint does.
"""
from __future__ import annotations

import socket
import sys
import threading
import time
import webbrowser

import uvicorn

from app.config import settings


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


if __name__ == "__main__":
    cert = settings.ssl_certfile.strip() or None
    key = settings.ssl_keyfile.strip() or None
    scheme = "https" if cert and key else "http"
    base = f"{scheme}://{_lan_ip()}:{settings.port}"
    # The token has to be on every page URL, and forgetting it looks exactly
    # like the server being broken: the page loads, the socket is accepted,
    # and then nothing happens. Printing the ready-made links is cheaper than
    # explaining that once per machine. The value is already in .env on this
    # machine, so echoing it to this machine's own console reveals nothing.
    q = f"?token={settings.ws_token}" if settings.ws_token else ""
    print(f"  talk to Emma : {base}/{q}")
    print(f"  robot kiosk  : {base}/{q}&kiosk=1" if q
          else f"  robot kiosk  : {base}/?kiosk=1")
    print(f"  robot screen : {base}/display{q}&chat=1" if q
          else f"  robot screen : {base}/display?chat=1")
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
        ssl_certfile=cert,
        ssl_keyfile=key,
    )
