"""
Broadlink IR sender — drives the sales-gallery lights and air conditioner.

Ported from the `emma` project, which already learned the IR codes for this
room; point `IR_CODES_FILE` / `BROADLINK_DEVICE_FILE` at those JSON files
rather than re-learning them.

Two properties worth knowing:

- **IR is one-way.** Nothing reports back whether the light actually
  switched, so the state this module tracks is what we *sent*, not what the
  room is doing. After a restart, or if someone uses the physical remote, it
  can be wrong.
- **The gallery Wi-Fi link to the hub is lossy** (emma's notes measured ~75%
  packet loss to a hub that was genuinely online), so a single send failing
  usually means a dropped packet, not broken hardware. Hence the retries.

With no hub configured this runs in mock mode: state still updates so the
conversation works end to end, and `hardware` reports `"mock"` so the caller
can be honest about it.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from app.config import settings

logger = logging.getLogger("condo_voice.ir")

_CONNECT_ATTEMPTS = 4
_CONNECT_RETRY_DELAY = 1.0
_SEND_ATTEMPTS = 3
_SEND_RETRY_DELAY = 0.4


def _codes_path() -> Path:
    return Path(settings.ir_codes_file).expanduser()


def _device_path() -> Path:
    return Path(settings.broadlink_device_file).expanduser()


def package_installed() -> bool:
    import importlib.util

    return importlib.util.find_spec("broadlink") is not None


def available() -> bool:
    """True only when we could genuinely drive the hardware.

    All three parts matter. Checking just the config files made the system
    claim it had sent commands while the `broadlink` package wasn't even
    installed.
    """
    return package_installed() and _device_path().is_file() and _codes_path().is_file()


def status() -> dict:
    """Why IR is or isn't usable — for /health and startup logging."""
    return {
        "enabled": settings.ir_enabled,
        "package_installed": package_installed(),
        "device_file": _device_path().is_file(),
        "codes_file": _codes_path().is_file(),
        "usable": settings.ir_enabled and available(),
    }


def load_codes() -> dict:
    try:
        return json.loads(_codes_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _rediscover(cfg: dict):
    # The router may hand the hub a new IP after a reboot; find it again by
    # its fixed MAC and heal the saved config in place.
    import broadlink

    try:
        for found in broadlink.discover(timeout=8):
            if found.mac.hex() == cfg.get("mac"):
                found.auth()
                cfg["host"] = found.host[0]
                cfg["devtype"] = found.devtype
                _device_path().write_text(json.dumps(cfg), encoding="utf-8")
                return found
    except Exception:
        logger.exception("broadlink rediscovery failed")
    return None


def _get_device():
    import broadlink

    try:
        cfg = json.loads(_device_path().read_text(encoding="utf-8"))
    except Exception:
        return None
    for attempt in range(_CONNECT_ATTEMPTS):
        try:
            device = broadlink.gendevice(
                cfg["devtype"], (cfg["host"], 80), bytes.fromhex(cfg["mac"])
            )
            device.auth()
            return device
        except Exception:
            if attempt < _CONNECT_ATTEMPTS - 1:
                time.sleep(_CONNECT_RETRY_DELAY)
    return _rediscover(cfg)


def send(code_name: str, repeat: int = 1, delay: float = 0.6) -> str:
    """Send one learned IR code.

    Returns "ok", "mock" (no hub configured), or "failed".
    """
    if not settings.ir_enabled or not available():
        return "mock"

    codes = load_codes()
    code = codes.get(code_name)
    if not code:
        logger.warning("no IR code named %r", code_name)
        return "failed"

    try:
        device = _get_device()
    except ImportError:
        logger.warning("broadlink package not installed; running IR in mock mode")
        return "mock"
    if device is None:
        return "failed"

    payload = bytes.fromhex(code)
    for i in range(repeat):
        sent = False
        for attempt in range(_SEND_ATTEMPTS):
            try:
                device.send_data(payload)
                sent = True
                break
            except Exception:
                if attempt < _SEND_ATTEMPTS - 1:
                    time.sleep(_SEND_RETRY_DELAY)
        if not sent:
            return "failed"
        if i < repeat - 1:
            time.sleep(delay)
    return "ok"
