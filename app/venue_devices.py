"""The event venue's lights and curtains, sent by the robot's own server -- ready for the day the owner
says what drives them (owner 20 Nov 2026: "เตรียมด้วยนะ หุ่นอาจจะต้องสั่งปิดไฟ ปิดม่านได้ ... อาจจะต้อง
ทำระบบเผื่อไว้ เช็คและรีวิวทดสอบโดยละเอียด").

Nobody knows yet what switches the floor-1 lobby's lights and curtains: an IR remote, an RF 433/315 MHz
remote, a smart switch or relay, Tuya or Home Assistant, the building's own system. So the devices are
named in a data file (VENUE_DEVICES_FILE, tracked, no secrets) and each has an ADAPTER:

  manual     today's staff tick: nothing is sent -- the ceremony's "ทำเอง" step is the whole of it
  broadlink  a learned IR or RF frame through a Broadlink hub (an RM4 Pro does both): the hub's host,
             MAC and type in .env (VENUE_BROADLINK_*), the frames in VENUE_CODES_FILE
             (`scripts/venue_devices.py learn <device> <action> [--rf]`)
  http       a webhook / a Home Assistant service call: the base URL and a bearer token in .env only
             (VENUE_HTTP_BASE_URL, VENUE_HTTP_TOKEN); the method, path and JSON body in the file,
             optionally the entity's `state` read back -- the only way a result here is "ok"

Who presses: the ceremony's running order (`{"do": "device", "device": ..., "action": ...}`, placed
BEFORE the staff tick that confirms the room really changed -- app/event_order.py), and the ceremony
page's "ไฟ/ม่าน" card (rehearsal, recovery). Never the voice: see docs/emma-ceremony.md ("ทำไมไม่สั่ง
ด้วยเสียง") -- during the ceremony her microphone hears nothing but a move, and after it a guest's
"ปิดไฟ" would darken the lobby for everyone (the heard guard checks words, not who said them).

What it holds to (each is a bug this project already paid for once):

* **Sent is not done.** `hardware`: "ok" only when the device itself says the state changed (an http
  adapter with `state` read back); "sent" = the hub / endpoint took it (IR and RF are one-way: the
  2026-08-26 lamp that never reacted to an accepted frame); "mock" = VENUE_DEVICES_DRY_RUN, nothing
  left the machine; "failed" = nothing went out; "unknown" = it may have (no answer after sending --
  look at the room before pressing again: a one-button RF curtain remote turns "open" pressed twice
  into "stop"); "manual" = staff do it. `ok` = sent, confirmed or mocked (the smarthome tools'
  convention: `hardware` says what really happened, `ok` only that it was not a failure).
* **Default off, everywhere.** VENUE_DEVICES=false: no device is "in use", the ceremony's device steps
  are not part of its order (app/event_order.py hides them), /ceremony/state has no `venue`, the morning
  card no row -- the ceremony runs exactly as before (tests/test_venue_ceremony.py).
* **Nothing slow on the event loop.** A send or a probe runs in a thread, one at a time per device; past
  VENUE_DEVICE_TIMEOUT_S the press answers "unknown" and the device stays busy until its thread ends.
* **No secrets in the file, none in the log.** The file is refused a token/URL field; one turn-log row
  per command: device, action, by, hardware, error, ms -- never the URL, the token or a code.
* **A red stop does not undo a light.** Nothing here listens to it: a light that went off stays off.

Nothing here has run against a real venue device yet.
"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import re
import socket
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from app.config import settings

logger = logging.getLogger("condo_voice.venue_devices")

ROOT = Path(__file__).resolve().parent.parent
FORMAT = "emma-venue-devices-v1"
ADAPTERS = ("manual", "broadlink", "http")
#: kind -> what it may be asked, in the order the staff card shows them.
KIND_ACTIONS = {"lights": ("off", "on"), "curtains": ("open", "close", "stop"), "switch": ("off", "on")}
ACTION_TH = {"on": "เปิด", "off": "ปิด", "open": "เปิด", "close": "ปิด", "stop": "หยุด"}
#: What the room shows when the action took -- the words of the tick that confirms it.
SEEN_TH = {("lights", "off"): "ไฟดับ", ("lights", "on"): "ไฟสว่าง", ("curtains", "open"): "ม่านเปิด",
           ("curtains", "close"): "ม่านปิด", ("curtains", "stop"): "ม่านหยุด",
           ("switch", "off"): "อุปกรณ์ปิด", ("switch", "on"): "อุปกรณ์เปิด"}
HARDWARE = ("ok", "sent", "mock", "failed", "unknown", "manual")

#: An IR/RF frame is sent this many times at most (a learned remote that needs a double press).
MAX_REPEAT = 3
#: One http request's own timeout, and how long its state may take to be read back as changed.
HTTP_TIMEOUT_S = 3.0
STATE_WAIT_S = 3.0
#: The hub: auth attempts (a lossy Wi-Fi link drops UDP -- broadlink_ir.py measured ~75 %).
HUB_ATTEMPTS = 3
HUB_SOCKET_S = 1.0
SEND_ATTEMPTS = 2
#: A device busy longer than its timeout plus this is a thread that hung: no longer "busy".
BUSY_GRACE_S = 30.0

_ID = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_ENV_NAME = re.compile(r"^VENUE_[A-Z0-9_]{1,48}$")
#: A key that holds a secret, or a URL: these belong in .env, never in the tracked file.
_SECRET_KEY = re.compile(r"(?i)token|passw|secret|authori|api[_-]?key|bearer|cookie|^url$|_url$|^host$|^mac$")
_METHODS = ("POST", "PUT", "GET")

OFF_TH = "ระบบสั่งไฟ/ม่านจากหุ่นปิดอยู่บนเครื่องนี้ (VENUE_DEVICES) — ให้ทีมทำเอง"
MANUAL_TH = "อุปกรณ์นี้ยังไม่ได้ต่อกับหุ่น (ทีมงานทำเอง)"
DRY_RUN_TH = "จำลอง — กดแล้วไม่ได้ส่งสัญญาณจริง (VENUE_DEVICES_DRY_RUN)"
ERRORS_TH = {
    "off": OFF_TH,
    "no_device": "ไม่พบอุปกรณ์หรือคำสั่งนี้ในไฟล์อุปกรณ์งาน",
    "manual": MANUAL_TH + " — ให้ทีมทำเอง",
    "busy": "กำลังส่งคำสั่งก่อนหน้าของอุปกรณ์นี้อยู่ — รอสักครู่",
    "no_code": "ยังไม่ได้สอนรหัสรีโมตของคำสั่งนี้ — ไม่ได้ส่งอะไรออกไป",
    "no_package": "เครื่องนี้ไม่มีแพ็กเกจ broadlink — ไม่ได้ส่งอะไรออกไป",
    "no_hub": "ยังไม่ได้ตั้งกล่อง Broadlink (VENUE_BROADLINK_HOST / _MAC / _DEVTYPE ใน .env) — ไม่ได้ส่งอะไรออกไป",
    "hub_unreachable": "ติดต่อกล่อง Broadlink ไม่ได้ — ไม่ได้ส่งอะไรออกไป",
    "no_ack": "ส่งสัญญาณออกไปแล้วแต่กล่องไม่ตอบ — ไม่รู้ว่าถึงอุปกรณ์ไหม: ดูห้องก่อนกดซ้ำ",
    "no_base": "VENUE_HTTP_BASE_URL ใน .env ยังไม่ได้ตั้ง หรือไม่ได้ขึ้นต้นด้วย http:// หรือ https:// — ไม่ได้ส่งอะไรออกไป",
    "timeout_before": "หมดเวลาก่อนส่ง — ไม่ได้ส่งอะไรออกไป",
    "no_path": "ยังไม่ได้ตั้งที่อยู่คำสั่ง (path_env) ใน .env — ไม่ได้ส่งอะไรออกไป",
    "unreachable": "ติดต่อระบบควบคุมไม่ได้ — ไม่ได้ส่งอะไรออกไป",
    "auth": "ระบบควบคุมไม่ยอมรับสิทธิ์ (VENUE_HTTP_TOKEN ผิดหรือหมดอายุ) — ไม่ได้ทำ",
    "refused": "ระบบควบคุมปฏิเสธคำสั่ง — ไม่ได้ทำ",
    "timeout": "ส่งแล้วแต่ไม่ได้คำตอบ — ไม่รู้ผล: ดูห้องก่อนกดซ้ำ",
    "error": "ผิดพลาด — ไม่รู้ผล: ดูห้องก่อนกดซ้ำ",
}
HARDWARE_TH = {"ok": "อุปกรณ์ยืนยันแล้ว", "sent": "ส่งคำสั่งแล้ว (ยังไม่มีใครยืนยันว่าเปลี่ยนจริง)",
               "mock": DRY_RUN_TH, "failed": "ส่งไม่สำเร็จ", "unknown": "ไม่รู้ผล", "manual": "ทีมงานทำเอง"}

#: `last`: device -> the last command's outcome (the card, the order step's warning). `probe`: target ->
#: the morning check's word on reaching it.
STATE: dict = {"last": {}, "probe": {}}
_busy: dict[str, float] = {}
#: The waits between retries (tests make them instant; the fakes keep their own clock).
_sleep = time.sleep
_cache: dict = {"devices": (None, None), "codes": (None, None)}


def reset() -> None:
    STATE.update(last={}, probe={})
    _busy.clear()
    _cache.update(devices=(None, None), codes=(None, None))


# ---- configuration -------------------------------------------------------------------------------

@dataclass(frozen=True)
class Config:
    """What a machine drives with -- this server's settings, or (scripts/event_preflight.py) the
    robot's .env as `robot_server.py secrets` composes it."""
    enabled: bool = False
    dry_run: bool = False
    devices_file: str = ""
    codes_file: str = ""
    hub_host: str = ""
    hub_mac: str = field(default="", repr=False)
    hub_devtype: str = ""
    http_base: str = ""
    #: repr=False (review 20 Nov): a Config printed or logged must not carry the token, nor the env
    http_token: str = field(default="", repr=False)
    timeout_s: float = 8.0
    #: where a `path_env` is looked up: None = this process's environment (.env is loaded into it)
    env: dict | None = field(default=None, compare=False, hash=False, repr=False)

    def getenv(self, name: str) -> str:
        return str((self.env if self.env is not None else os.environ).get(name) or "").strip()

    @classmethod
    def from_settings(cls) -> "Config":
        return cls(enabled=bool(settings.venue_devices), dry_run=bool(settings.venue_devices_dry_run),
                   devices_file=settings.venue_devices_file or "", codes_file=settings.venue_codes_file or "",
                   hub_host=settings.venue_broadlink_host or "", hub_mac=settings.venue_broadlink_mac or "",
                   hub_devtype=settings.venue_broadlink_devtype or "",
                   http_base=settings.venue_http_base_url or "", http_token=settings.venue_http_token or "",
                   timeout_s=float(settings.venue_device_timeout_s or 8.0))

    @classmethod
    def from_env(cls, env: dict) -> "Config":
        """The robot's composed .env (KEY -> raw value), the way app/config.py would read it."""
        def first(name: str, default: str = "") -> str:
            parts = str(env.get(name, default) or "").split()
            return parts[0] if parts else ""

        def on(name: str) -> bool:
            return str(env.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}

        try:
            timeout = float(env.get("VENUE_DEVICE_TIMEOUT_S") or 8)
        except ValueError:
            timeout = 8.0
        return cls(enabled=on("VENUE_DEVICES"), dry_run=on("VENUE_DEVICES_DRY_RUN"),
                   devices_file=str(env.get("VENUE_DEVICES_FILE") or "data/events/venue_devices.json").strip(),
                   codes_file=str(env.get("VENUE_CODES_FILE") or "data/events/venue_codes.json").strip(),
                   hub_host=first("VENUE_BROADLINK_HOST"), hub_mac=first("VENUE_BROADLINK_MAC"),
                   hub_devtype=first("VENUE_BROADLINK_DEVTYPE"), http_base=first("VENUE_HTTP_BASE_URL"),
                   http_token=first("VENUE_HTTP_TOKEN"), timeout_s=timeout, env=dict(env))


def _cfg(cfg: Config | None = None) -> Config:
    return cfg if cfg is not None else Config.from_settings()


def _resolve(name: str) -> Path | None:
    name = (name or "").strip()
    if not name:
        return None
    path = Path(name)
    return path if path.is_absolute() else ROOT / path


def _stat(path: Path | None):
    try:
        st = path.stat() if path is not None else None
    except OSError:
        return None
    return None if st is None else (st.st_mtime_ns, st.st_size)


# ---- the devices file ----------------------------------------------------------------------------

@dataclass(frozen=True)
class Device:
    id: str
    name_th: str
    kind: str
    adapter: str
    codes: dict = field(default_factory=dict)       # broadlink: action -> code name in the codes file
    repeat: int = 1
    http: dict | None = None                        # http: {"actions": {action: spec}, "state": spec|None, "probe_path"}
    note_th: str | None = None

    def defined(self) -> tuple[str, ...]:
        """The actions this device can be sent under its adapter, in the kind's order (manual: all)."""
        kinds = KIND_ACTIONS.get(self.kind, ())
        if self.adapter == "broadlink":
            return tuple(a for a in kinds if a in self.codes)
        if self.adapter == "http":
            return tuple(a for a in kinds if a in ((self.http or {}).get("actions") or {}))
        return kinds


def _text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _secrets_in(value, where: str, out: list[str]) -> None:
    """A secret-looking key or a URL anywhere under a device: the file is tracked, these are .env's."""
    if isinstance(value, dict):
        for key, sub in value.items():
            if isinstance(key, str) and _SECRET_KEY.search(key):
                out.append("%s_secret_field:%s" % (where, key[:24]))
            _secrets_in(sub, where, out)
    elif isinstance(value, list):
        for sub in value:
            _secrets_in(sub, where, out)
    elif isinstance(value, str) and re.search(r"(?i)\b[a-z][a-z0-9+.-]*://", value):
        out.append("%s_url_in_file" % where)


def _http_spec(raw, did: str, kinds: tuple, out: list[str]) -> dict | None:
    if not isinstance(raw, dict):
        out.append("%s_http_not_an_object" % did)
        return None
    actions = {}
    for action, spec in (raw.get("actions") or {}).items() if isinstance(raw.get("actions"), dict) else ():
        if action not in kinds:
            out.append("%s_http_bad_action:%s" % (did, str(action)[:16]))
            continue
        if not isinstance(spec, dict):
            out.append("%s_http_%s_not_an_object" % (did, action))
            continue
        method = str(spec.get("method") or "POST").upper()
        path, path_env = spec.get("path"), spec.get("path_env")
        if method not in _METHODS:
            out.append("%s_http_%s_bad_method" % (did, action))
            continue
        if path_env is not None:
            if not isinstance(path_env, str) or not _ENV_NAME.match(path_env):
                out.append("%s_http_%s_bad_path_env" % (did, action))
                continue
        elif not (isinstance(path, str) and path.startswith("/") and not re.search(r"\s", path)):
            out.append("%s_http_%s_bad_path" % (did, action))
            continue
        body = spec.get("body")
        if body is not None and not isinstance(body, (dict, list)):
            out.append("%s_http_%s_bad_body" % (did, action))
            continue
        actions[action] = {"method": method, "path": path if path_env is None else None,
                           "path_env": path_env, "body": body}
    state = raw.get("state")
    if state is not None:
        ok = (isinstance(state, dict) and isinstance(state.get("path"), str) and state["path"].startswith("/")
              and isinstance(state.get("field") or "state", str) and isinstance(state.get("expect"), dict))
        if not ok:
            out.append("%s_http_bad_state" % did)
            state = None
        else:
            expect = {}
            for action, want in state["expect"].items():
                values = [want] if isinstance(want, str) else want
                if action in kinds and isinstance(values, list) and all(isinstance(v, str) for v in values):
                    expect[action] = tuple(values)
            state = {"path": state["path"], "field": state.get("field") or "state", "expect": expect}
    probe = raw.get("probe_path")
    if probe is not None and not (isinstance(probe, str) and probe.startswith("/")):
        out.append("%s_http_bad_probe_path" % did)
        probe = None
    return {"actions": actions, "state": state, "probe_path": probe}


def parse(doc) -> tuple[dict[str, Device], list[str]]:
    """{id: Device} and the problems found. A device that is not valid is left out with a problem --
    never guessed into shape (an "off" sent to the wrong thing is worse than no button)."""
    problems: list[str] = []
    if not isinstance(doc, dict):
        return {}, ["not_an_object"]
    if doc.get("format") != FORMAT:
        problems.append("bad_format")
    raw = doc.get("devices")
    if not isinstance(raw, dict):
        return {}, problems + ["no_devices"]
    out: dict[str, Device] = {}
    for did, entry in raw.items():
        if not isinstance(did, str) or not _ID.match(did):
            problems.append("bad_id:%s" % str(did)[:32])
            continue
        if not isinstance(entry, dict):
            problems.append("%s_not_an_object" % did)
            continue
        found: list[str] = []
        _secrets_in(entry, did, found)
        kind, adapter, name = entry.get("kind"), entry.get("adapter"), _text(entry.get("name_th"))
        if kind not in KIND_ACTIONS:
            found.append("%s_bad_kind:%s" % (did, str(kind)[:16]))
        if adapter not in ADAPTERS:
            found.append("%s_bad_adapter:%s" % (did, str(adapter)[:16]))
        if not name:
            found.append("%s_no_name" % did)
        if found:
            problems.extend(found)
            continue
        kinds = KIND_ACTIONS[kind]
        codes, repeat, http = {}, 1, None
        bl = entry.get("broadlink")
        if bl is not None:
            if not isinstance(bl, dict) or not isinstance(bl.get("codes"), dict):
                found.append("%s_broadlink_bad" % did)
            else:
                for action, code in bl["codes"].items():
                    if action not in kinds:
                        found.append("%s_broadlink_bad_action:%s" % (did, str(action)[:16]))
                    elif not isinstance(code, str) or not _CODE.match(code):
                        found.append("%s_broadlink_bad_code:%s" % (did, action))
                    else:
                        codes[action] = code
                rep = bl.get("repeat", 1)
                if isinstance(rep, int) and not isinstance(rep, bool) and 1 <= rep <= MAX_REPEAT:
                    repeat = rep
                else:
                    found.append("%s_broadlink_bad_repeat" % did)
        if entry.get("http") is not None:
            http = _http_spec(entry["http"], did, kinds, found)
        if adapter == "broadlink" and not codes:
            found.append("%s_broadlink_without_codes" % did)
        if adapter == "http" and not (http and http["actions"]):
            found.append("%s_http_without_actions" % did)
        if found:
            problems.extend(found)
            continue
        out[did] = Device(did, name, kind, adapter, codes, repeat, http, _text(entry.get("note_th")))
    return out, problems


def _load(cfg: Config) -> tuple[dict[str, Device], list[str]]:
    path = _resolve(cfg.devices_file)
    key = (str(path), _stat(path))
    cached_key, cached = _cache["devices"]
    if cached_key == key and cached is not None:
        return cached
    if path is None or key[1] is None:
        value = ({}, ["no_file"])
    else:
        try:
            value = parse(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            logger.exception("venue devices: %s could not be read", path)
            value = ({}, ["unreadable"])
    _cache["devices"] = (key, value)
    return value


def devices(cfg: Config | None = None) -> dict[str, Device]:
    """Every valid device in the file, whatever the switch says (the order's ids are checked
    against these on every machine)."""
    return _load(_cfg(cfg))[0]


def problems(cfg: Config | None = None) -> list[str]:
    return list(_load(_cfg(cfg))[1])


def device(device_id, cfg: Config | None = None) -> Device | None:
    return devices(cfg).get(device_id) if isinstance(device_id, str) else None


def load_codes(cfg: Config | None = None) -> dict:
    cfg = _cfg(cfg)
    path = _resolve(cfg.codes_file)
    key = (str(path), _stat(path))
    cached_key, cached = _cache["codes"]
    if cached_key == key and cached is not None:
        return cached
    codes: dict = {}
    if path is not None and key[1] is not None:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            codes = {k: v for k, v in doc.items() if isinstance(k, str) and isinstance(v, str)} \
                if isinstance(doc, dict) else {}
        except (OSError, ValueError):
            logger.exception("venue devices: the codes file %s could not be read", path)
    _cache["codes"] = (key, codes)
    return codes


def step_problem(device_id, action, cfg: Config | None = None) -> str | None:
    """An order step's ids (app/event_script.py order_problems; the preflight with the robot's config):
    None = both exist."""
    dev = device(device_id, cfg)
    if dev is None:
        return "unknown_device:%s" % str(device_id)[:32]
    if action not in KIND_ACTIONS[dev.kind]:
        return "bad_action:%s" % str(action)[:16]
    return None


# ---- what can be pressed ----------------------------------------------------------------------------

def in_use(device_id, cfg: Config | None = None) -> bool:
    """This machine drives the device: the switch is on and the device is not a staff one. The ceremony's
    device steps of a device not in use are not part of its order."""
    cfg = _cfg(cfg)
    dev = device(device_id, cfg) if cfg.enabled else None
    return dev is not None and dev.adapter != "manual"


@functools.lru_cache(maxsize=1)
def _package() -> bool:
    """`broadlink` installed (asked once: the order reads readiness every poll, a find_spec walks sys.path)."""
    import importlib.util

    return importlib.util.find_spec("broadlink") is not None


def _hub_set(cfg: Config) -> bool:
    return bool(cfg.hub_host and _mac(cfg.hub_mac) and _devtype(cfg.hub_devtype) is not None)


def _mac(text: str) -> bytes | None:
    raw = re.sub(r"[^0-9a-fA-F]", "", text or "")
    try:
        return bytes.fromhex(raw) if len(raw) == 12 else None
    except ValueError:
        return None


def _devtype(text: str) -> int | None:
    try:
        return int(str(text).strip(), 0) if str(text or "").strip() else None
    except ValueError:
        return None


def why_not(device_id, action, cfg: Config | None = None) -> str | None:
    """Why (device, action) cannot be sent from this machine now, in Thai -- None = it can. Files and
    settings only: never the network (the order reads this every poll)."""
    cfg = _cfg(cfg)
    if not cfg.enabled:
        return OFF_TH
    dev = device(device_id, cfg)
    if dev is None or action not in KIND_ACTIONS[dev.kind]:
        return ERRORS_TH["no_device"]
    if dev.adapter == "manual":
        return MANUAL_TH
    if action not in dev.defined():
        return "ยังไม่ได้กำหนดคำสั่ง '%s' ของ%sในไฟล์อุปกรณ์งาน" % (ACTION_TH[action], dev.name_th)
    if cfg.dry_run:
        return None
    if dev.adapter == "broadlink":
        code = dev.codes[action]
        if not load_codes(cfg).get(code):
            return "ยังไม่ได้สอนรหัส %s — python scripts/venue_devices.py learn %s %s" % (code, dev.id, action)
        if not _hub_set(cfg):
            return ERRORS_TH["no_hub"]
        if not _package():
            return ERRORS_TH["no_package"]
        return None
    spec = dev.http["actions"][action]
    if not base_ok(cfg.http_base):
        return ERRORS_TH["no_base"]
    if spec["path_env"] and not cfg.getenv(spec["path_env"]):
        return "ยังไม่ได้ตั้ง %s ใน .env" % spec["path_env"]
    return None


def step_label(device_id, action) -> str:
    dev = device(device_id)
    name = dev.name_th if dev else str(device_id)
    return "หุ่นสั่ง%s%s" % (ACTION_TH.get(action, str(action)), name)


def seen_th(device_id, action) -> str:
    dev = device(device_id)
    return SEEN_TH.get((dev.kind if dev else "", action), "อุปกรณ์เปลี่ยน")


def confirm_note_th(device_id, action) -> str:
    """The note of the staff tick right after a device step that is in use: the tick's own file note
    ("หุ่นไม่ได้คุมไฟ") is no longer true, the reason for the tick still is."""
    return ("%sที่ขั้นก่อนหน้าแล้ว แต่ส่งแล้วไม่ได้แปลว่าเปลี่ยนแล้ว — กดขั้นนี้เมื่อเห็นจริงว่า%sแล้วเท่านั้น · "
            "ไม่เปลี่ยน = ให้ทีมทำเอง แล้วค่อยกด" % (step_label(device_id, action), seen_th(device_id, action)))


def warning(device_id, cfg: Config | None = None) -> str | None:
    """What a device step that CAN be pressed still warns of: a rehearsal's dry run, the last command
    that failed or is unknown, the morning check that could not reach it."""
    cfg = _cfg(cfg)
    if cfg.dry_run:
        return DRY_RUN_TH
    last = STATE["last"].get(device_id)
    if last and last.get("hardware") in ("failed", "unknown"):
        return "ครั้งล่าสุด %s %s: %s" % (time.strftime("%H:%M", time.localtime(last["at"])),
                                          ACTION_TH.get(last.get("action"), ""), last.get("error_th") or "")
    dev = device(device_id, cfg)
    probe = STATE["probe"].get(_target(dev, cfg)) if dev is not None else None
    if probe and probe.get("ok") is False and not (last and last.get("ok") and last["at"] >= probe["at"]):
        # a command that went out since the morning check is the newer word
        return "ตรวจเช้า (%s): %s" % (time.strftime("%H:%M", time.localtime(probe["at"])), probe.get("detail_th"))
    return None


# ---- sending ----------------------------------------------------------------------------------------

def _left(deadline: float | None) -> float:
    return float("inf") if deadline is None else deadline - time.monotonic()


def _hub(cfg: Config, deadline: float | None = None):
    """The venue's Broadlink, authed -- None when it never answered (nothing was sent then). Never
    starts an attempt it has no time left to finish (`deadline`, monotonic)."""
    import broadlink

    mac, devtype = _mac(cfg.hub_mac), _devtype(cfg.hub_devtype)
    for attempt in range(HUB_ATTEMPTS):
        if _left(deadline) < 0.3:
            return None
        try:
            hub = broadlink.gendevice(devtype, (cfg.hub_host, 80), mac)
            # broadlink waits 10 s per UDP attempt by default: a voice turn, a press, frozen for a minute
            hub.timeout = max(0.2, min(HUB_SOCKET_S, _left(deadline)))
            hub.auth()
            return hub
        except Exception:                                # noqa: BLE001 -- a dropped packet, or no hub
            if attempt < HUB_ATTEMPTS - 1 and _left(deadline) > 0.5 + HUB_SOCKET_S:
                _sleep(0.5)
    return None


def _send_broadlink(dev: Device, action: str, cfg: Config, deadline: float | None = None) -> dict:
    code = load_codes(cfg).get(dev.codes.get(action) or "")
    if not code:
        return {"hardware": "failed", "error": "no_code"}
    try:
        payload = bytes.fromhex(code)
    except ValueError:
        return {"hardware": "failed", "error": "no_code"}
    try:
        hub = _hub(cfg, deadline)
    except ImportError:
        return {"hardware": "failed", "error": "no_package"}
    if hub is None:
        return {"hardware": "failed", "error": "hub_unreachable", "hint_th": route_hint(cfg.hub_host)}
    for k in range(dev.repeat):
        if k and _left(deadline) < 0.6 + HUB_SOCKET_S:
            break                                        # one frame went out already: "sent" in the time we had
        for attempt in range(SEND_ATTEMPTS):
            try:
                hub.send_data(payload)
                break
            except Exception:                            # noqa: BLE001
                if attempt == SEND_ATTEMPTS - 1 or _left(deadline) < 0.4 + HUB_SOCKET_S:
                    # the frame may well have gone out: the hub's answer is what went missing
                    return {"hardware": "unknown", "error": "no_ack"}
                _sleep(0.4)
        if k < dev.repeat - 1:
            _sleep(0.6)
    return {"hardware": "sent", "error": None}


#: http(s)://host[:port][/prefix] -- review 20 Nov: "192.168.0.5" (the scheme forgotten) read ready, then
#: urllib raised ValueError("unknown url type: <the whole URL>") on the press: "unknown" for a request
#: that never left, and the URL -- a webhook's secret path with it -- in the server log.
_BASE = re.compile(r"^https?://[^/\s?#@]+(/[^\s?#]*)?$", re.IGNORECASE)


def base_ok(url: str) -> bool:
    return bool(url) and bool(_BASE.match(url))


def _bad_scheme(exc) -> bool:
    """urllib's URLError("unknown url type: ...") -- a base like "192.168.0.5:8123" read as a scheme:
    raised before anything was sent. Its text holds the URL: matched here, never logged."""
    reason = getattr(exc, "reason", None)
    return isinstance(reason, str) and "unknown url type" in reason


def _url(cfg: Config, path: str) -> str:
    return cfg.http_base.rstrip("/") + path


def _request(cfg: Config, method: str, url: str, body=None, timeout: float | None = None):
    """(status, parsed JSON or None). Raises what urllib raises -- whose messages can hold the URL:
    never log them."""
    import urllib.request

    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if cfg.http_token:
        headers["Authorization"] = "Bearer " + cfg.http_token
    data = None
    if method != "GET":
        data = json.dumps(body if body is not None else {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    wait = HTTP_TIMEOUT_S if timeout is None else max(0.2, min(HTTP_TIMEOUT_S, timeout))
    with urllib.request.urlopen(req, timeout=wait) as resp:     # noqa: S310 -- .env's own URL
        raw = resp.read(65536)
        status = resp.status
    try:
        return status, json.loads(raw.decode("utf-8")) if raw else None
    except ValueError:
        return status, None


def _field(doc, dotted: str):
    for part in dotted.split("."):
        if not isinstance(doc, dict):
            return None
        doc = doc.get(part)
    return doc


def _send_http(dev: Device, action: str, cfg: Config, deadline: float | None = None) -> dict:
    import urllib.error

    spec = dev.http["actions"][action]
    path = spec["path"] or cfg.getenv(spec["path_env"])
    if not base_ok(cfg.http_base):
        return {"hardware": "failed", "error": "no_base"}
    if not path.startswith("/"):
        return {"hardware": "failed", "error": "no_path"}
    if _left(deadline) < 0.5:
        return {"hardware": "failed", "error": "timeout_before"}
    try:
        _request(cfg, spec["method"], _url(cfg, path), spec["body"], timeout=_left(deadline))
    except ValueError:
        # urllib's "unknown url type" / http.client's InvalidURL: raised before anything was sent
        return {"hardware": "failed", "error": "no_base"}
    except urllib.error.HTTPError as exc:
        return {"hardware": "failed", "error": "auth" if exc.code in (401, 403) else "refused", "status": exc.code}
    except urllib.error.URLError as exc:
        if _bad_scheme(exc):
            return {"hardware": "failed", "error": "no_base"}          # "192.168.0.5:8123" read as a scheme
        # connecting or sending (urllib wraps those): the request never got a hearing
        return {"hardware": "failed", "error": "unreachable", "hint_th": route_hint(_host_of(cfg.http_base))}
    except (TimeoutError, socket.timeout):
        # sent, then no answer: it may have run
        return {"hardware": "unknown", "error": "timeout"}
    except OSError:
        return {"hardware": "unknown", "error": "error"}
    state = (dev.http or {}).get("state")
    want = state["expect"].get(action) if state else None
    if not want:
        return {"hardware": "sent", "error": None}
    # Read back within what is left of the press's budget (review 20 Nov: 3 s POST + 3 s of reads + one
    # more GET of up to 3 s went past the 8 s timeout -- an accepted command reported "unknown"): out of
    # time = "sent", not confirmed.
    seen = None
    until = min(time.monotonic() + STATE_WAIT_S, deadline if deadline is not None else float("inf"))
    while time.monotonic() < until - 0.2:
        try:
            _status, doc = _request(cfg, "GET", _url(cfg, state["path"]), timeout=until - time.monotonic())
            seen = _field(doc, state["field"])
        except (OSError, ValueError):
            seen = None
        if isinstance(seen, str) and seen in want:
            return {"hardware": "ok", "error": None, "state": seen}
        if time.monotonic() + 0.5 >= until:
            break
        _sleep(0.5)
    out = {"hardware": "sent", "error": None}
    if isinstance(seen, str):
        out["state"] = seen[:32]
    return out


#: The press's own budget is VENUE_DEVICE_TIMEOUT_S; the thread stops starting new work this much before
#: it, so a command the far end took is answered as such, not as the timeout's "unknown".
BUDGET_MARGIN_S = 0.5


def _send(dev: Device, action: str, cfg: Config) -> dict:
    """In a thread: the frame or the request, and what the far end said -- within the press's budget."""
    deadline = time.monotonic() + max(0.5, cfg.timeout_s - BUDGET_MARGIN_S)
    if dev.adapter == "broadlink":
        return _send_broadlink(dev, action, cfg, deadline)
    return _send_http(dev, action, cfg, deadline)


def _result(dev_id: str, action: str, hardware: str, error: str | None, *, name_th: str | None = None,
            error_th: str | None = None, **extra) -> dict:
    ok = hardware in ("ok", "sent", "mock")
    out = {"ok": ok, "device": dev_id, "action": action, "name_th": name_th, "hardware": hardware,
           "error": None if ok else (error or "error"),
           "error_th": None if ok else (error_th or ERRORS_TH.get(error or "", ERRORS_TH["error"])),
           "note_th": HARDWARE_TH.get(hardware) if ok else None}
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def _log(dev_id, action, by, out: dict, started: float) -> None:
    from app import turnlog

    turnlog.record("venue_device", device=dev_id, action=action, by=by, hardware=out.get("hardware"),
                   ok=bool(out.get("ok")), error=out.get("error"), ms=int((time.monotonic() - started) * 1000))


def busy(device_id, cfg: Config | None = None) -> bool:
    since = _busy.get(device_id)
    return since is not None and time.monotonic() - since < _cfg(cfg).timeout_s + BUSY_GRACE_S


async def run(device_id, action, *, by: str = "staff", cfg: Config | None = None) -> dict:
    """Send one command. {"ok", "device", "action", "name_th", "hardware", "error", "error_th", "note_th"}
    (+ "state" when an http device said its state, + "hint_th" on a network failure). Never raises."""
    cfg = _cfg(cfg)
    started = time.monotonic()
    did = str(device_id or "")[:32]
    act = str(action or "")[:16]
    dev = device(did, cfg)
    if dev is None or act not in KIND_ACTIONS.get(dev.kind, ()):
        out = _result(did, act, "failed", "no_device")
    elif not cfg.enabled:
        out = _result(did, act, "failed", "off", name_th=dev.name_th)
    elif dev.adapter == "manual":
        out = _result(did, act, "manual", "manual", name_th=dev.name_th)
    elif (why := why_not(did, act, cfg)) is not None:
        out = _result(did, act, "failed", "not_configured", name_th=dev.name_th, error_th=why)
    elif cfg.dry_run:
        out = _result(did, act, "mock", None, name_th=dev.name_th)
    elif busy(did, cfg):
        out = _result(did, act, "failed", "busy", name_th=dev.name_th)
        _log(did, act, by, out, started)
        return out                                        # not the device's last word: the first press is
    else:
        _busy[did] = time.monotonic()
        loop_task = asyncio.ensure_future(asyncio.to_thread(_send, dev, act, cfg))
        mine = _busy[did]
        loop_task.add_done_callback(lambda _t: _busy.pop(did, None) if _busy.get(did) == mine else None)
        try:
            got = await asyncio.wait_for(asyncio.shield(loop_task), timeout=cfg.timeout_s)
        except asyncio.TimeoutError:
            got = {"hardware": "unknown", "error": "timeout"}
        except Exception as exc:                         # noqa: BLE001 -- a press always gets an answer
            # the type only: an exception's text can hold the endpoint's URL, a webhook's secret path
            logger.error("venue devices: %s %s raised %s", did, act, type(exc).__name__)
            got = {"hardware": "unknown", "error": "error"}
        hint = got.get("hint_th")
        out = _result(did, act, got.get("hardware") or "unknown", got.get("error"), name_th=dev.name_th,
                      error_th=(ERRORS_TH.get(got.get("error") or "", ERRORS_TH["error"])
                                + ((" (" + hint + ")") if hint else "")) if got.get("error") else None,
                      state=got.get("state"), status=got.get("status"))
    if out["hardware"] != "failed" or out["error"] not in ("no_device", "off"):
        STATE["last"][did] = {"action": act, "hardware": out["hardware"], "ok": out["ok"], "at": time.time(),
                              "by": by, "error_th": out["error_th"]}
    _log(did, act, by, out, started)
    return out


# ---- reaching it: the morning check (never on the event loop) -----------------------------------------

def _host_of(url: str) -> str:
    from urllib.parse import urlsplit

    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def _net24(ip: str) -> str | None:
    parts = ip.split(".")
    return ".".join(parts[:3]) if len(parts) == 4 and all(p.isdigit() for p in parts) else None


def route_hint(host: str) -> str:
    """Which network this machine leaves on for `host` (a UDP connect sends nothing) -- the subnet the
    robot is on against the one the hub is on is the commonest reason it cannot be reached."""
    if not host:
        return ""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, 9))
            mine = s.getsockname()[0]
    except OSError:
        return "เครื่องนี้ไม่มีเส้นทางไป %s" % host
    a, b = _net24(mine), _net24(host)
    if a and b and a != b:
        return ("เครื่องนี้อยู่วง %s.x แต่อุปกรณ์อยู่วง %s.x — คนละวง: ให้อยู่ Wi-Fi เดียวกับหุ่น "
                "(Broadlink ใช้ 2.4 GHz)" % (a, b))
    if a and b:
        return "อยู่วงเดียวกัน (%s.x) แต่อุปกรณ์ไม่ตอบ — เสียบไฟไหม · IP เปลี่ยนไหม (scripts/venue_devices.py find)" % a
    return ""


def _target(dev: Device | None, cfg: Config) -> str | None:
    if dev is None:
        return None
    if dev.adapter == "broadlink":
        return "broadlink:%s" % cfg.hub_host
    if dev.adapter == "http":
        return "http:%s" % _host_of(cfg.http_base)
    return None


def targets(cfg: Config | None = None) -> list[tuple[str, str]]:
    """(adapter, host) of every device in use -- what the preflight asks the robot to reach."""
    cfg = _cfg(cfg)
    out = []
    for dev in devices(cfg).values():
        if dev.adapter == "broadlink" and cfg.hub_host:
            pair = ("broadlink", cfg.hub_host)
        elif dev.adapter == "http" and _host_of(cfg.http_base):
            pair = ("http", _host_of(cfg.http_base))
        else:
            continue
        if pair not in out:
            out.append(pair)
    return out


def probe(dev: Device, cfg: Config | None = None) -> tuple[bool, str]:
    """Can this machine reach what drives `dev`? Sends no command: a Broadlink auth, an http GET of
    `probe_path` (any HTTP answer = reached; 401/403 = the token). Blocking: call in a thread."""
    cfg = _cfg(cfg)
    if dev.adapter == "broadlink":
        if not _hub_set(cfg):
            return False, ERRORS_TH["no_hub"]
        try:
            hub = _hub(cfg)
        except ImportError:
            return False, ERRORS_TH["no_package"]
        if hub is None:
            return False, "ติดต่อกล่อง Broadlink ที่ %s ไม่ได้ · %s" % (cfg.hub_host, route_hint(cfg.hub_host))
        return True, "ติดต่อกล่อง Broadlink ที่ %s ได้" % cfg.hub_host
    if dev.adapter == "http":
        import urllib.error

        if not base_ok(cfg.http_base):
            return False, ERRORS_TH["no_base"]
        path = ((dev.http or {}).get("probe_path")) or "/"
        try:
            _request(cfg, "GET", _url(cfg, path))
        except ValueError:
            return False, ERRORS_TH["no_base"]
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return False, ERRORS_TH["auth"]
            return True, "ติดต่อระบบควบคุมได้ (HTTP %d)" % exc.code
        except (urllib.error.URLError, OSError) as exc:
            if _bad_scheme(exc):
                return False, ERRORS_TH["no_base"]          # "192.168.0.5:8123" read as a scheme
            host = _host_of(cfg.http_base)
            return False, "ติดต่อระบบควบคุมที่ %s ไม่ได้ · %s" % (host, route_hint(host))
        return True, "ติดต่อระบบควบคุมได้"
    return True, MANUAL_TH


def _ceremony_actions() -> dict[str, set]:
    """device -> the actions the ceremony's running order sends it (the file as written)."""
    try:
        from app import event_script

        out: dict[str, set] = {}
        for sc in event_script.order() or []:
            for st in sc["steps"]:
                if st.get("do") == "device" and st.get("device"):
                    out.setdefault(st["device"], set()).add(st.get("action"))
        return out
    except Exception:                                    # noqa: BLE001 -- the check still runs
        logger.exception("venue devices: the ceremony's device steps")
        return {}


def review(cfg: Config | None = None) -> list[tuple]:
    """The file-and-settings half of the morning check, no network: [(ok, th, detail_th, device|None)].
    ok None = a note. [] when the switch is off (the card unchanged)."""
    cfg = _cfg(cfg)
    if not cfg.enabled:
        return []
    rows: list[tuple] = []
    devs, probs = _load(cfg)
    if probs:
        rows.append((False, "ไฟล์อุปกรณ์ไฟ/ม่าน", "; ".join(probs)[:300], None))
    if cfg.dry_run:
        rows.append((False, "ไฟ/ม่าน: หุ่นส่งสัญญาณจริง",
                     "VENUE_DEVICES_DRY_RUN เปิดอยู่ = กดแล้วไม่มีอะไรออกไป (จำลอง) — ผู้ดูแลปิดก่อนวันงาน", None))
    used = [d for d in devs.values() if d.adapter != "manual"]
    if not used:
        rows.append((None, "ไฟ/ม่าน", "ทุกอุปกรณ์เป็นแบบทีมทำเอง (manual) — ขั้นหุ่นสั่งไฟ/ม่านไม่อยู่ในลำดับพิธี", None))
        return rows
    wanted = _ceremony_actions()
    for dev in used:
        need = sorted(wanted.get(dev.id) or dev.defined())
        whys = [w for w in (why_not(dev.id, a, cfg) for a in need) if w]
        rows.append((False if whys else True, "หุ่นสั่ง%s ได้ (%s: %s)" % (dev.name_th, dev.adapter,
                                                                         ", ".join(ACTION_TH[a] for a in need)),
                     " · ".join(dict.fromkeys(whys)) if whys else "", dev))
        others = [a for a in dev.defined() if a not in need and why_not(dev.id, a, cfg)]
        if others:
            rows.append((None, "%s: คำสั่งที่พิธีไม่ใช้ยังไม่พร้อม" % dev.name_th,
                         ", ".join(ACTION_TH[a] for a in others), None))
    return rows


#: Said with every row about reaching a device that failed: the ceremony goes on without it.
FALLBACK_TH = ("ถ้าแก้ไม่ทัน: ตอนพิธีกด 'ข้ามขั้นนี้' ที่ขั้นหุ่นสั่งไฟ/ม่าน แล้วให้ทีมทำเอง — "
               "ขั้นติ๊กยืนยันยังอยู่เหมือนเดิม")


def readiness_rows(cfg: Config | None = None) -> list[dict]:
    """The morning GO / NO-GO's rows for the venue devices (app/event_readiness.py), checked FROM THIS
    MACHINE -- on the robot, from the robot's own Wi-Fi. Blocking (an auth, an http GET): call it in a
    thread. [] when the switch is off."""
    cfg = _cfg(cfg)
    out: list[dict] = []
    reached: dict[str, tuple[bool, str]] = {}
    for ok, th, detail, dev in review(cfg):
        if dev is not None and ok and not cfg.dry_run:
            key = _target(dev, cfg)
            if key not in reached:
                try:
                    reached[key] = probe(dev, cfg)
                except Exception as exc:                 # noqa: BLE001 -- a row, never the card's end;
                    # the type only (its text can hold the endpoint)
                    reached[key] = (False, "ตรวจไม่ได้ (%s)" % type(exc).__name__)
                STATE["probe"][key] = {"ok": reached[key][0], "at": time.time(), "detail_th": reached[key][1]}
            ok, detail = reached[key]
            if not ok:
                detail = detail + " — " + FALLBACK_TH
        elif ok is False and dev is not None:
            detail = detail + " — " + FALLBACK_TH
        out.append({"ok": ok, "th": th, "detail_th": detail})
    return out


# ---- the staff card ---------------------------------------------------------------------------------

def snapshot(cfg: Config | None = None) -> dict | None:
    """The ceremony page's "ไฟ/ม่าน" card: None when nothing is in use (then /ceremony/state has no
    `venue` key at all -- the page as before). Files and memory only."""
    cfg = _cfg(cfg)
    if not cfg.enabled:
        return None
    used = [d for d in devices(cfg).values() if d.adapter != "manual"]
    if not used:
        return None
    rows = []
    for dev in used:
        acts = []
        for a in dev.defined():
            why = why_not(dev.id, a, cfg)
            acts.append({"id": a, "th": ACTION_TH[a], "ready": why is None, "why_th": why})
        last = STATE["last"].get(dev.id)
        rows.append({"id": dev.id, "name_th": dev.name_th, "kind": dev.kind, "adapter": dev.adapter,
                     "busy": busy(dev.id, cfg), "actions": acts, "warn_th": warning(dev.id, cfg),
                     "last": None if last is None else {
                         "action": last["action"], "th": ACTION_TH.get(last["action"]), "hardware": last["hardware"],
                         "ok": last["ok"], "at": last["at"], "error_th": last["error_th"]}})
    return {"dry_run": cfg.dry_run, "devices": rows}


def with_config(**changes) -> Config:
    """This machine's config with some fields changed (scripts/venue_devices.py: `send` on a PC whose
    .env has the switch off)."""
    return replace(Config.from_settings(), **changes)
