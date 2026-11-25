"""The venue's lights and curtains the robot sends (app/venue_devices.py; owner 20 Nov 2026: "เตรียมด้วยนะ
หุ่นอาจจะต้องสั่งปิดไฟ ปิดม่านได้ ... เช็คและรีวิวทดสอบโดยละเอียด").

What these hold down -- every device here is a fake (a Broadlink module in sys.modules, a local http
server); nothing reaches a real light:

* default off: no device in use, a press sends nothing, no card, no morning row;
* sent is not done: a Broadlink frame the hub took is "sent", never "ok"; "ok" only when an http device
  reads its own state back as changed; no answer after sending is "unknown", a hub that never answered
  is "failed" (nothing went out); a dry run is "mock" and nothing leaves the machine;
* one command per device at a time, and a timeout leaves the device busy until its thread ends;
* the tracked file is refused a URL or a secret; the log row never carries the URL, the token or a code;
* the morning check reaches the hub from this machine, and says the subnet when it cannot;
* the PC preflight reads the ROBOT's composed settings and pings from the robot;
* scripts/venue_devices.py never writes the sales room's codes and learns RF in two steps.
"""
from __future__ import annotations

import asyncio
import importlib.machinery
import importlib.util
import json
import socket
import sys
import threading
import time
import types
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app import venue_devices as vd
from app.config import settings

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "ha-secret-token-xyz"


# ---- fakes ------------------------------------------------------------------------------------------

class FakeHub:
    """A Broadlink hub: `auth_fails` attempts fail first; `send_fails` send_data calls raise; `delay`
    on each send (a hub that is slow to ack)."""

    def __init__(self, world):
        self.world = world
        self.timeout = None

    def auth(self):
        w = self.world
        w["auths"] += 1
        if w["auth_fails"] > 0:
            w["auth_fails"] -= 1
            raise OSError("no answer")
        return True

    def send_data(self, payload):
        w = self.world
        if w["delay"]:
            time.sleep(w["delay"])
        if w["send_fails"] > 0:
            w["send_fails"] -= 1
            raise OSError("no ack")
        w["sent"].append(bytes(payload).hex())


@pytest.fixture
def hub(monkeypatch):
    """A fake `broadlink` module with one hub; the world dict says what it does and what it got."""
    world = {"auths": 0, "auth_fails": 0, "send_fails": 0, "delay": 0.0, "sent": [], "made": []}
    mod = types.ModuleType("broadlink")
    mod.__spec__ = importlib.machinery.ModuleSpec("broadlink", None)

    def gendevice(devtype, host, mac, name="", is_locked=False):
        world["made"].append((devtype, host, bytes(mac).hex()))
        return FakeHub(world)

    mod.gendevice = gendevice
    monkeypatch.setitem(sys.modules, "broadlink", mod)
    monkeypatch.setattr(vd, "_package", lambda: True)
    monkeypatch.setattr(vd, "_sleep", lambda s: None)
    return world


DEVICES = {"format": vd.FORMAT, "devices": {
    "lobby_lights": {"name_th": "ไฟล็อบบี้", "kind": "lights", "adapter": "broadlink",
                     "broadlink": {"codes": {"off": "lobby_lights_off", "on": "lobby_lights_on"}, "repeat": 2}},
    "lobby_curtains": {"name_th": "ผ้าม่านล็อบบี้", "kind": "curtains", "adapter": "manual",
                       "broadlink": {"codes": {"open": "lobby_curtains_open"}}},
}}
CODES = {"lobby_lights_off": "26001a00aabbcc", "lobby_lights_on": "26001a00ddeeff"}


@pytest.fixture
def venue(tmp_path, monkeypatch):
    """The switch on, a devices file and a codes file in tmp, a hub set in settings. Returns a writer
    for the devices file."""
    devices, codes = tmp_path / "venue_devices.json", tmp_path / "venue_codes.json"

    def write(doc=DEVICES, code_doc=CODES):
        devices.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        codes.write_text(json.dumps(code_doc), encoding="utf-8")
        vd.reset()

    write()
    monkeypatch.setattr(settings, "venue_devices", True)
    monkeypatch.setattr(settings, "venue_devices_file", str(devices))
    monkeypatch.setattr(settings, "venue_codes_file", str(codes))
    monkeypatch.setattr(settings, "venue_broadlink_host", "127.0.0.1")
    monkeypatch.setattr(settings, "venue_broadlink_mac", "aa:bb:cc:dd:ee:ff")
    monkeypatch.setattr(settings, "venue_broadlink_devtype", "0x520b")
    return write


@pytest.fixture
def rows(monkeypatch):
    out: list[dict] = []
    from app import turnlog

    monkeypatch.setattr(turnlog, "record", lambda event, **f: out.append({"event": event, **f}))
    return out


def run(*a, **k):
    return asyncio.run(vd.run(*a, **k))


# ---- off by default ---------------------------------------------------------------------------------

def test_the_tracked_file_is_valid_and_both_devices_are_the_staffs_today():
    """The shipped file names the lobby's lights and curtains, both `manual` (nobody knows yet what drives
    them): on every machine, nothing is in use, whatever the switch says."""
    assert vd.problems() == [] and set(vd.devices()) == {"lobby_lights", "lobby_curtains"}
    assert all(d.adapter == "manual" for d in vd.devices().values())
    assert not vd.in_use("lobby_lights") and not vd.in_use("lobby_curtains")
    on = replace(vd.Config.from_settings(), enabled=True)
    assert not vd.in_use("lobby_lights", on)                       # manual: still the staff's tick
    raw = json.loads((ROOT / "data" / "events" / "venue_devices.json").read_text(encoding="utf-8"))
    assert "://" not in json.dumps(raw["devices"]) and "token" not in json.dumps(raw["devices"]).lower()


def test_off_is_off_nothing_sent_no_card_no_morning_row(hub, venue, monkeypatch, rows):
    monkeypatch.setattr(settings, "venue_devices", False)
    assert not vd.in_use("lobby_lights")
    out = run("lobby_lights", "off")
    assert out["ok"] is False and out["error"] == "off" and out["hardware"] == "failed"
    assert hub["sent"] == [] and hub["auths"] == 0
    assert vd.snapshot() is None and vd.readiness_rows() == [] and vd.review() == []
    assert vd.why_not("lobby_lights", "off") == vd.OFF_TH
    assert vd.STATE["last"] == {}                                    # nothing to show on a card


# ---- broadlink: sent is not done ----------------------------------------------------------------------

def test_a_frame_the_hub_took_is_sent_never_ok(hub, venue, rows):
    out = run("lobby_lights", "off", by="ceremony")
    assert out["ok"] is True and out["hardware"] == "sent" and out["error"] is None
    assert hub["sent"] == [CODES["lobby_lights_off"]] * 2           # repeat 2: the file's own
    assert hub["made"][0] == (0x520B, ("127.0.0.1", 80), "aabbccddeeff")
    assert "ยังไม่มีใครยืนยัน" in out["note_th"]
    assert vd.STATE["last"]["lobby_lights"]["hardware"] == "sent"
    row = rows[-1]
    assert row == {"event": "venue_device", "device": "lobby_lights", "action": "off", "by": "ceremony",
                   "hardware": "sent", "ok": True, "error": None, "ms": row["ms"]}
    assert CODES["lobby_lights_off"] not in json.dumps(rows)


def test_a_hub_that_never_answers_is_failed_and_says_why(hub, venue):
    hub["auth_fails"] = 99
    out = run("lobby_lights", "off")
    assert out["ok"] is False and out["hardware"] == "failed" and out["error"] == "hub_unreachable"
    assert hub["sent"] == [] and hub["auths"] == vd.HUB_ATTEMPTS
    assert "ไม่ได้ส่งอะไรออกไป" in out["error_th"]
    # the card / the order step warn of it until the next command goes
    assert "ครั้งล่าสุด" in vd.warning("lobby_lights")


def test_a_dropped_packet_on_auth_is_retried(hub, venue):
    hub["auth_fails"] = 1
    assert run("lobby_lights", "on")["hardware"] == "sent" and hub["auths"] == 2


def test_no_ack_after_sending_is_unknown_not_failed(hub, venue):
    """The frame may have gone out: "ดูห้องก่อนกดซ้ำ" -- a one-button curtain remote pressed twice stops."""
    hub["send_fails"] = 99
    out = run("lobby_lights", "off")
    assert out["ok"] is False and out["hardware"] == "unknown" and out["error"] == "no_ack"
    assert "ดูห้องก่อนกดซ้ำ" in out["error_th"]


def test_a_code_not_learned_is_not_ready_and_sends_nothing(hub, venue):
    venue(code_doc={"lobby_lights_off": CODES["lobby_lights_off"]})
    assert vd.why_not("lobby_lights", "off") is None
    assert "learn lobby_lights on" in vd.why_not("lobby_lights", "on")
    out = run("lobby_lights", "on")
    assert out["error"] == "not_configured" and out["hardware"] == "failed" and hub["auths"] == 0


def test_no_hub_in_env_is_not_ready(hub, venue, monkeypatch):
    monkeypatch.setattr(settings, "venue_broadlink_mac", "")
    assert vd.why_not("lobby_lights", "off") == vd.ERRORS_TH["no_hub"]
    assert run("lobby_lights", "off")["hardware"] == "failed" and hub["auths"] == 0


def test_a_manual_device_and_an_unknown_one(hub, venue):
    assert run("lobby_curtains", "open")["hardware"] == "manual"
    assert run("nope", "off")["error"] == "no_device" and run("lobby_lights", "open")["error"] == "no_device"
    assert hub["auths"] == 0


def test_a_dry_run_is_mock_and_nothing_leaves(hub, venue, monkeypatch):
    monkeypatch.setattr(settings, "venue_devices_dry_run", True)
    monkeypatch.setattr(settings, "venue_broadlink_host", "")      # not even a hub is needed
    out = run("lobby_lights", "off")
    assert out["ok"] is True and out["hardware"] == "mock" and vd.DRY_RUN_TH == out["note_th"]
    assert hub["auths"] == 0 and hub["sent"] == []
    assert vd.warning("lobby_lights") == vd.DRY_RUN_TH


def test_one_command_per_device_and_a_timeout_is_unknown_and_stays_busy(hub, venue, monkeypatch):
    monkeypatch.setattr(settings, "venue_device_timeout_s", 0.2)
    hub["delay"] = 0.6

    async def go():
        first = await vd.run("lobby_lights", "off")
        second = await vd.run("lobby_lights", "on")             # the first one's thread still sends
        busy_then = vd.busy("lobby_lights")
        for _ in range(60):
            if not vd.busy("lobby_lights"):
                break
            await asyncio.sleep(0.05)
        return first, second, busy_then, vd.busy("lobby_lights")

    first, second, busy_then, busy_after = asyncio.run(go())
    assert first["hardware"] == "unknown" and first["error"] == "timeout" and "ดูห้อง" in first["error_th"]
    assert second["error"] == "busy" and second["ok"] is False and busy_then is True
    assert busy_after is False
    assert vd.STATE["last"]["lobby_lights"]["action"] == "off"     # "busy" is not the device's last word


# ---- the file: no URL, no secret ----------------------------------------------------------------------

@pytest.mark.parametrize("extra", [{"token": "x"}, {"url": "http://10.0.0.5"}, {"note_th": "ดู https://x.example"},
                                   {"http": {"actions": {"off": {"path": "/a", "headers": {"Authorization": "Bearer x"}}}}}])
def test_a_secret_or_a_url_in_the_tracked_file_is_refused(extra):
    doc = {"format": vd.FORMAT, "devices": {"lobby_lights": {
        "name_th": "ไฟ", "kind": "lights", "adapter": "http",
        "http": {"actions": {"off": {"method": "POST", "path": "/api/services/light/turn_off"}}}, **extra}}}
    devices, problems = vd.parse(doc)
    assert "lobby_lights" not in devices and problems, problems


def test_bad_entries_are_left_out_never_guessed():
    doc = {"format": vd.FORMAT, "devices": {
        "Bad Id": {"name_th": "x", "kind": "lights", "adapter": "manual"},
        "a": {"name_th": "x", "kind": "fountain", "adapter": "manual"},
        "b": {"name_th": "x", "kind": "lights", "adapter": "zigbee"},
        "c": {"name_th": "x", "kind": "lights", "adapter": "broadlink", "broadlink": {"codes": {"open": "c_open"}}},
        "d": {"name_th": "x", "kind": "lights", "adapter": "broadlink", "broadlink": {"codes": {"off": "d_off"}, "repeat": 9}},
        "ok": {"name_th": "x", "kind": "lights", "adapter": "broadlink", "broadlink": {"codes": {"off": "ok_off"}}}}}
    devices, problems = vd.parse(doc)
    assert set(devices) == {"ok"} and len(problems) >= 5
    assert vd.parse({"devices": {}})[1] == ["bad_format"]


def test_an_order_step_is_checked_against_the_file():
    assert vd.step_problem("lobby_lights", "off") is None
    assert vd.step_problem("lobby_lights", "open").startswith("bad_action")
    assert vd.step_problem("stage_fog", "on").startswith("unknown_device")


# ---- http: "ok" only from the device's own state --------------------------------------------------------

class Controller:
    """A local stand-in for Home Assistant: records requests, answers `status`, its entity's state."""

    def __init__(self):
        self.requests: list[dict] = []
        self.status, self.state, self.delay = 200, "on", 0.0
        self.server = None

    def __enter__(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _answer(self, code, doc):
                body = json.dumps(doc).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                outer.requests.append({"method": "POST", "path": self.path, "auth": self.headers.get("Authorization"),
                                       "body": json.loads(self.rfile.read(n) or b"null")})
                if outer.delay:
                    time.sleep(outer.delay)
                if outer.status >= 400:
                    return self._answer(outer.status, {"message": "no"})
                if self.path.endswith("turn_off"):
                    outer.state = "off"
                self._answer(200, [])

            def do_GET(self):
                outer.requests.append({"method": "GET", "path": self.path, "auth": self.headers.get("Authorization")})
                if self.headers.get("Authorization") != "Bearer " + TOKEN:
                    return self._answer(401, {"message": "401: Unauthorized"})
                self._answer(200, {"entity_id": "light.lobby", "state": outer.state})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    @property
    def base(self):
        return "http://127.0.0.1:%d" % self.server.server_address[1]

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


HTTP_DEVICES = {"format": vd.FORMAT, "devices": {"lobby_lights": {
    "name_th": "ไฟล็อบบี้", "kind": "lights", "adapter": "http",
    "http": {"actions": {"off": {"method": "POST", "path": "/api/services/light/turn_off",
                                 "body": {"entity_id": "light.lobby"}},
                         "on": {"method": "POST", "path": "/api/services/light/turn_on",
                                "body": {"entity_id": "light.lobby"}}},
             "state": {"path": "/api/states/light.lobby", "field": "state", "expect": {"off": ["off"], "on": "on"}},
             "probe_path": "/api/"}}}}


@pytest.fixture
def controller(venue, monkeypatch):
    with Controller() as c:
        venue(HTTP_DEVICES, {})
        monkeypatch.setattr(settings, "venue_http_base_url", c.base)
        monkeypatch.setattr(settings, "venue_http_token", TOKEN)
        monkeypatch.setattr(vd, "STATE_WAIT_S", 0.6)
        yield c


def test_http_ok_only_when_the_entity_reads_back_changed(controller, rows):
    out = run("lobby_lights", "off")
    assert out["ok"] is True and out["hardware"] == "ok" and out["state"] == "off"
    post = controller.requests[0]
    assert post == {"method": "POST", "path": "/api/services/light/turn_off", "auth": "Bearer " + TOKEN,
                    "body": {"entity_id": "light.lobby"}}
    assert TOKEN not in json.dumps(rows) and controller.base not in json.dumps(rows) and TOKEN not in json.dumps(out)


def test_http_taken_but_the_state_never_changed_is_sent(controller):
    out = run("lobby_lights", "on")          # the fake never turns on: the state stays "off" after an off
    controller.state = "off"
    out = run("lobby_lights", "on")
    assert out["ok"] is True and out["hardware"] == "sent" and out["state"] == "off"


def test_http_refused_unauthorised_unreachable_and_no_answer(controller, monkeypatch):
    controller.status = 500
    assert run("lobby_lights", "off")["error"] == "refused"
    controller.status = 401
    out = run("lobby_lights", "off")
    assert out["error"] == "auth" and out["hardware"] == "failed"
    controller.status = 200
    controller.delay = 1.0
    monkeypatch.setattr(vd, "HTTP_TIMEOUT_S", 0.3)
    out = run("lobby_lights", "off")
    assert out["hardware"] == "unknown" and out["error"] == "timeout"          # it arrived; no answer
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed = s.getsockname()[1]
    monkeypatch.setattr(settings, "venue_http_base_url", "http://127.0.0.1:%d" % closed)
    out = run("lobby_lights", "off")
    assert out["hardware"] == "failed" and out["error"] == "unreachable" and "ไม่ได้ส่งอะไรออกไป" in out["error_th"]


def test_http_needs_its_base_url(controller, monkeypatch):
    monkeypatch.setattr(settings, "venue_http_base_url", "")
    assert vd.why_not("lobby_lights", "off") == vd.ERRORS_TH["no_base"]
    assert run("lobby_lights", "off")["error"] == "not_configured" and controller.requests == []


SECRET_PATH = "/api/webhook/SECRET-HOOK-ID"


@pytest.mark.parametrize("base", ["192.168.0.5", "192.168.0.5:8123", "ha.local/api", "ftp://192.168.0.5"])
def test_a_base_url_without_a_scheme_is_not_ready_sends_nothing_and_logs_no_secret(venue, monkeypatch, caplog, base):
    """Review 20 Nov: "192.168.0.5" read READY, then the press raised ValueError("unknown url type: <the
    whole URL>") -- "unknown" for a request that never left, the webhook's secret path in server.log."""
    doc = {"format": vd.FORMAT, "devices": {"lobby_lights": {
        "name_th": "ไฟ", "kind": "lights", "adapter": "http",
        "http": {"actions": {"off": {"method": "POST", "path_env": "VENUE_LIGHTS_OFF_HOOK"}}}}}}
    venue(doc, {})
    monkeypatch.setenv("VENUE_LIGHTS_OFF_HOOK", SECRET_PATH)
    monkeypatch.setattr(settings, "venue_http_base_url", base)
    caplog.set_level("DEBUG")
    assert vd.why_not("lobby_lights", "off") == vd.ERRORS_TH["no_base"]
    out = run("lobby_lights", "off")
    assert out["hardware"] == "failed" and out["error"] == "not_configured"
    assert vd.probe(vd.devices()["lobby_lights"]) == (False, vd.ERRORS_TH["no_base"])
    rows = vd.readiness_rows()
    assert rows[0]["ok"] is False and "http://" in rows[0]["detail_th"]
    if "://" not in base:
        # the inner guards too: past the check, urllib's ValueError is "failed", nothing sent
        monkeypatch.setattr(vd, "base_ok", lambda url: True)
        got = vd._send_http(vd.devices()["lobby_lights"], "off", vd.Config.from_settings())
        assert got == {"hardware": "failed", "error": "no_base"}
        assert vd.probe(vd.devices()["lobby_lights"]) == (False, vd.ERRORS_TH["no_base"])
    assert "SECRET-HOOK-ID" not in caplog.text and "SECRET-HOOK-ID" not in json.dumps(out)


def test_an_exception_in_the_send_is_logged_by_its_type_only(hub, venue, monkeypatch, caplog):
    def boom(dev, action, cfg):
        raise RuntimeError("cannot reach http://10.0.0.9" + SECRET_PATH)

    monkeypatch.setattr(vd, "_send", boom)
    caplog.set_level("DEBUG")
    out = run("lobby_lights", "off")
    assert out["hardware"] == "unknown" and "RuntimeError" in caplog.text
    assert "SECRET-HOOK-ID" not in caplog.text and "10.0.0.9" not in caplog.text


def test_a_probe_that_raises_is_a_row_by_its_type_only(hub, venue, monkeypatch, caplog):
    def boom(dev, cfg=None):
        raise RuntimeError("http://10.0.0.9" + SECRET_PATH)

    monkeypatch.setattr(vd, "probe", boom)
    caplog.set_level("DEBUG")
    rows = vd.readiness_rows()
    assert rows[0]["ok"] is False and "ตรวจไม่ได้ (RuntimeError)" in rows[0]["detail_th"]
    assert "SECRET-HOOK-ID" not in json.dumps(rows, ensure_ascii=False) + caplog.text


def test_a_config_never_prints_its_token_mac_or_env():
    cfg = vd.Config(http_token="tok-SECRET", hub_mac="aa:bb:cc:dd:ee:ff", env={"VENUE_X": "/hook/SECRET2"})
    text = repr(cfg) + str(cfg)
    assert "SECRET" not in text and "aa:bb" not in text


def test_an_http_command_answers_within_its_budget(controller, monkeypatch):
    """Review 20 Nov: 3 s POST + 3 s of reads + one more GET went past the 8 s timeout. Now the reads stop
    in time: taken and never read back as changed = "sent" (not confirmed), never "unknown"."""
    monkeypatch.setattr(settings, "venue_device_timeout_s", 2.0)
    monkeypatch.setattr(vd, "STATE_WAIT_S", 3.0)
    controller.delay = 0.6
    controller.state = "off"                      # an "on" that never reads back as on
    started = time.monotonic()
    out = run("lobby_lights", "on")
    took = time.monotonic() - started
    assert out["hardware"] == "sent" and out["ok"] is True, out
    assert took < 2.0, took


def test_a_slow_hub_keeps_the_first_frame_within_the_budget(hub, venue, monkeypatch):
    doc = json.loads(json.dumps(DEVICES))
    doc["devices"]["lobby_lights"]["broadlink"]["repeat"] = 3
    venue(doc)
    monkeypatch.setattr(settings, "venue_device_timeout_s", 1.6)
    hub["delay"] = 0.5
    out = run("lobby_lights", "off")
    assert out["hardware"] == "sent" and len(hub["sent"]) == 1                 # the repeats that did not fit: cut


def test_the_preflight_names_an_order_step_the_robot_has_no_device_for(tmp_path, monkeypatch):
    pf = _pf()
    devices = tmp_path / "d.json"
    devices.write_text(json.dumps(DEVICES, ensure_ascii=False), encoding="utf-8")
    script = tmp_path / "ceremony.json"
    script.write_text(json.dumps({"format": "emma-event-script-v1", "cues": [], "order": [
        {"n": 8, "title_th": "ไฟ", "steps": [{"do": "device", "device": "stage_fog", "action": "on"},
                                             {"do": "device", "device": "lobby_lights", "action": "off"}]}]},
                                 ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "ceremony_file", str(script))
    from app import event_script

    event_script.reset()
    monkeypatch.setattr(pf, "_robot_env", lambda: {"VENUE_DEVICES": "true", "VENUE_DEVICES_FILE": str(devices)})
    rep = pf.Report()
    pf.check_venue(rep)
    row = next(r for r in rep.rows if "device steps" in r[1])
    assert row[0] == "NO " and "unknown_device:stage_fog" in row[2] and "lobby_lights" not in row[2]
    event_script.reset()


def test_a_webhook_path_lives_in_env(venue, monkeypatch):
    doc = {"format": vd.FORMAT, "devices": {"lobby_lights": {
        "name_th": "ไฟ", "kind": "lights", "adapter": "http",
        "http": {"actions": {"off": {"method": "POST", "path_env": "VENUE_LIGHTS_OFF_HOOK"}}}}}}
    with Controller() as c:
        venue(doc, {})
        monkeypatch.setattr(settings, "venue_http_base_url", c.base)
        monkeypatch.delenv("VENUE_LIGHTS_OFF_HOOK", raising=False)
        assert "VENUE_LIGHTS_OFF_HOOK" in vd.why_not("lobby_lights", "off")
        monkeypatch.setenv("VENUE_LIGHTS_OFF_HOOK", "/api/webhook/secret-id-1")
        out = run("lobby_lights", "off")
        assert out["hardware"] == "sent" and c.requests[0]["path"] == "/api/webhook/secret-id-1"


# ---- the morning check, the card -------------------------------------------------------------------------

def test_the_morning_rows_reach_the_hub_from_here(hub, venue):
    rows = vd.readiness_rows()
    assert [r["ok"] for r in rows] == [True] and "ไฟล็อบบี้" in rows[0]["th"]
    assert "ติดต่อกล่อง Broadlink ที่ 127.0.0.1 ได้" in rows[0]["detail_th"] and hub["sent"] == []


def test_the_morning_rows_say_the_subnet_and_the_way_on_by_hand(hub, venue, monkeypatch):
    hub["auth_fails"] = 99
    monkeypatch.setattr(vd, "route_hint", lambda host: "เครื่องนี้อยู่วง 192.168.79.x แต่อุปกรณ์อยู่วง 192.168.0.x — คนละวง")
    rows = vd.readiness_rows()
    assert rows[0]["ok"] is False and "คนละวง" in rows[0]["detail_th"] and vd.FALLBACK_TH in rows[0]["detail_th"]
    assert "ตรวจเช้า" in vd.warning("lobby_lights")                   # the order step says it too
    hub["auth_fails"] = 0
    assert run("lobby_lights", "off")["hardware"] == "sent"
    assert vd.warning("lobby_lights") is None                          # a command since is the newer word


def test_a_dry_run_is_a_no_go_in_the_morning(hub, venue, monkeypatch):
    monkeypatch.setattr(settings, "venue_devices_dry_run", True)
    rows = vd.readiness_rows()
    assert rows[0]["ok"] is False and "VENUE_DEVICES_DRY_RUN" in rows[0]["detail_th"] and hub["auths"] == 0


def test_the_route_hint_names_two_subnets():
    assert vd._net24("192.168.0.34") == "192.168.0" and vd._net24("hub.local") is None
    assert vd.route_hint("") == ""
    assert "วงเดียวกัน" in vd.route_hint("127.0.0.1")


def test_the_card_lists_devices_in_use_only(hub, venue):
    run("lobby_lights", "off")
    snap = vd.snapshot()
    assert [d["id"] for d in snap["devices"]] == ["lobby_lights"]                # the manual one is not there
    dev = snap["devices"][0]
    assert [a["id"] for a in dev["actions"]] == ["off", "on"] and all(a["ready"] for a in dev["actions"])
    assert dev["last"]["hardware"] == "sent" and dev["busy"] is False and snap["dry_run"] is False


def test_the_readiness_card_has_no_venue_row_while_off(monkeypatch):
    from app import event_readiness

    assert event_readiness._venue_rows() == []
    monkeypatch.setattr(event_readiness, "_ceremony_rows", lambda: [])
    monkeypatch.setattr(event_readiness, "_spot_rows", lambda: [])
    assert event_readiness._files() == []


def test_the_readiness_card_carries_the_venue_rows(hub, venue, monkeypatch):
    from app import event_readiness

    monkeypatch.setattr(event_readiness, "_ceremony_rows", lambda: [])
    monkeypatch.setattr(event_readiness, "_spot_rows", lambda: [])
    hub["auth_fails"] = 99
    files = event_readiness._files()
    assert len(files) == 1 and files[0]["ok"] is False and "ไฟล็อบบี้" in files[0]["th"]


# ---- the PC preflight: the robot's settings, the robot's Wi-Fi -------------------------------------------

def _pf():
    spec = importlib.util.spec_from_file_location("event_preflight", ROOT / "scripts" / "event_preflight.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_preflight_reads_the_robots_switch(monkeypatch):
    pf = _pf()
    monkeypatch.setattr(pf, "_robot_env", lambda: {"VENUE_DEVICES": "false"})
    rep = pf.Report()
    pf.check_venue(rep)
    assert rep.go and rep.rows[0][0] == "-- " and "off on the robot" in rep.rows[0][2]


def test_the_robot_env_is_composed_like_secrets_does(monkeypatch):
    """The PC's .env + robot.env on top -- no secret leaves the function's result into a row."""
    pf = _pf()
    env = pf._robot_env()
    assert isinstance(env, dict)
    cfg = vd.Config.from_env({"VENUE_DEVICES": "true", "VENUE_BROADLINK_HOST": "192.168.0.40 # the lobby hub",
                              "VENUE_DEVICE_TIMEOUT_S": "x"})
    assert cfg.enabled and cfg.hub_host == "192.168.0.40" and cfg.timeout_s == 8.0
    assert cfg.devices_file == "data/events/venue_devices.json"


def test_the_preflight_on_a_robot_that_drives_the_lights(tmp_path, monkeypatch):
    pf = _pf()
    devices = tmp_path / "d.json"
    devices.write_text(json.dumps(DEVICES, ensure_ascii=False), encoding="utf-8")
    codes = tmp_path / "c.json"
    codes.write_text(json.dumps({"lobby_lights_off": CODES["lobby_lights_off"]}), encoding="utf-8")
    env = {"VENUE_DEVICES": "true", "VENUE_DEVICES_FILE": str(devices), "VENUE_CODES_FILE": str(codes),
           "VENUE_BROADLINK_HOST": "192.168.0.40", "VENUE_BROADLINK_MAC": "aabbccddeeff",
           "VENUE_BROADLINK_DEVTYPE": "0x520b"}
    monkeypatch.setattr(pf, "_robot_env", lambda: env)
    monkeypatch.setattr(vd, "_package", lambda: True)
    rep = pf.Report()
    pf.check_venue(rep)
    # the ceremony's order (no order here: every action the file defines) needs "on" learned too
    assert not rep.go and any("learn lobby_lights on" in d for v, w, d in rep.rows if v == "NO ")
    assert any("not checked" in d for v, w, d in rep.rows)          # no --serial: said, not passed
    asked = []
    monkeypatch.setattr(pf, "_robot_reaches", lambda serial, adb, host: asked.append(host) or (False, "x does not answer"))
    rep = pf.Report()
    pf.check_venue(rep, "10.0.0.9:5555", "adb", robot_up=True)
    assert asked == ["192.168.0.40"] and rep.rows[-1][0] == "NO "


def test_the_robots_ping_names_the_other_subnet(monkeypatch):
    pf = _pf()

    class Done:
        def __init__(self, out):
            self.stdout = out

    monkeypatch.setattr(pf.subprocess, "run", lambda *a, **k: Done(
        "REACH-DOWN\n3: wlan0    inet 192.168.79.11/24 brd 192.168.79.255 scope global wlan0\n"))
    ok, detail = pf._robot_reaches("s", "adb", "192.168.0.40")
    assert ok is False and "ANOTHER SUBNET (192.168.0.x)" in detail and "robot on 192.168.79.x" in detail
    monkeypatch.setattr(pf.subprocess, "run", lambda *a, **k: Done("REACH-UP\n inet 192.168.0.2/24 "))
    assert pf._robot_reaches("s", "adb", "192.168.0.40")[0] is True
    monkeypatch.setattr(pf.subprocess, "run", lambda *a, **k: Done("error: device offline"))
    assert pf._robot_reaches("s", "adb", "192.168.0.40")[0] is None


# ---- scripts/venue_devices.py -----------------------------------------------------------------------------

def _cli():
    spec = importlib.util.spec_from_file_location("venue_devices_cli", ROOT / "scripts" / "venue_devices.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_learning_never_writes_the_sales_rooms_codes(tmp_path, monkeypatch):
    cli = _cli()
    sales = tmp_path / "ir_codes.json"
    sales.write_text('{"light_on": "aa"}', encoding="utf-8")
    monkeypatch.setattr(settings, "ir_codes_file", str(sales))
    with pytest.raises(ValueError):
        cli.save_code(sales, "lobby_lights_off", "2600aa", allowed={"lobby_lights_off"}, make_backup=True)
    assert sales.read_text(encoding="utf-8") == '{"light_on": "aa"}'
    venue = tmp_path / "venue_codes.json"
    with pytest.raises(ValueError):
        cli.save_code(venue, "light_on", "2600aa", allowed={"lobby_lights_off"}, make_backup=True)
    got = cli.save_code(venue, "lobby_lights_off", "2600AA", allowed={"lobby_lights_off"}, make_backup=True)
    assert got == {"lobby_lights_off": "2600aa"} and json.loads(venue.read_text(encoding="utf-8")) == got
    cli.save_code(venue, "lobby_lights_off", "2600bb", allowed={"lobby_lights_off"}, make_backup=True)
    assert json.loads((tmp_path / "venue_codes.json.bak").read_text(encoding="utf-8")) == got     # the undo


def test_rf_is_learned_in_two_steps():
    cli = _cli()
    calls = []

    class RF:
        def __init__(self):
            self.polls = 0

        def sweep_frequency(self):
            calls.append("sweep")

        def check_frequency(self):
            self.polls += 1
            return (self.polls >= 2, 433.92)

        def cancel_sweep_frequency(self):
            calls.append("cancel")

        def find_rf_packet(self, frequency=None):
            calls.append(("find", frequency))

        def check_data(self):
            if ("find", 433.92) not in calls:
                raise OSError("not yet")
            return b"\xb2\x00\x10\x00frame"

    said = []
    t = [0.0]
    data = cli.capture_rf(RF(), say=said.append, ask=lambda p: "", wait_s=5, poll_s=1,
                          clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s))
    assert data == b"\xb2\x00\x10\x00frame" and calls == ["sweep", ("find", 433.92)]
    assert any("433.92" in s for s in said)

    class Silent(RF):
        def check_frequency(self):
            return (False, 0.0)

    calls.clear()
    t[0] = 0.0
    assert cli.capture_rf(Silent(), say=said.append, ask=lambda p: "", wait_s=3, poll_s=1,
                          clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s)) is None
    assert calls == ["sweep", "cancel"]


def test_send_from_the_script_asks_first_and_uses_the_servers_path(hub, venue, monkeypatch):
    cli = _cli()
    monkeypatch.setattr(settings, "venue_devices", False)            # a PC with the switch off
    said = []
    cfg = vd.Config.from_settings()
    assert cli.cmd_send(cfg, "lobby_lights", "off", yes=False, ask=lambda p: "n", say=said.append) == 1
    assert hub["sent"] == []
    assert cli.cmd_send(cfg, "lobby_lights", "off", yes=False, ask=lambda p: "y", say=said.append) == 0
    assert hub["sent"] and any("hardware=sent" in s for s in said) and any("ไม่ได้แปลว่าไฟดับ" in s for s in said)
    assert settings.venue_devices is False                           # forced on for that run only
