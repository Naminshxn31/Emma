"""The ceremony's light and curtain steps (owner 20 Nov 2026: "หุ่นอาจจะต้องสั่งปิดไฟ ปิดม่านได้ ... เช็คและรีวิว
ทดสอบโดยละเอียด"): `{"do": "device"}` in the running order (app/event_order.py) through app/venue_devices.py,
the staff page's "ไฟ/ม่าน" card and /ceremony/device (app/event_control_server.py, client/ceremony-control.html).

What these hold down:

* **Off = the ceremony exactly as before.** The real order with its device steps taken out is, byte for
  byte, the order as it was on 20 Nov (tests/data/ceremony_order_before_venue_devices.json); with
  VENUE_DEVICES off the staff page's order, "ถัดไป" and every press are the same as for the order that
  never had them; /ceremony/state has no `venue`.
* **On = the robot fires at the beat, staff still confirm.** The device step comes before the staff tick,
  the "are off" line after it; the tick's note says why it is still needed; a device press goes while her
  line is still heard (the lights go off on her "now"); a line press does not.
* **Sent is not done**: the step's mark says how far it got (`hardware`), a failure keeps the order where
  it was and says how to go on by hand, the red stop does not touch a light.
"""
from __future__ import annotations

import asyncio
import copy
import importlib.machinery
import json
import sys
import time
import types
from pathlib import Path

import pytest

from app import event_cues, event_host, event_order, event_script, event_walk, venue_devices
from app.config import settings
from tests.ceremony_app import ceremony_off, ceremony_on
from tests.test_event_order import calls  # noqa: F401 -- the fixture: every other path recorded, answering ok

ROOT = Path(__file__).resolve().parents[1]
EVENT = ROOT / "data" / "events" / "floor1_event.json"
REAL_SCRIPT = ROOT / "data" / "ceremony" / "floor1_ceremony.json"
BEFORE = ROOT / "tests" / "data" / "ceremony_order_before_venue_devices.json"


def _signed(raw: dict) -> dict:
    c = event_script._normalise(raw)
    return dict(raw, approved_by="test", approved_at="2026-11-20 10:00", approved_sha256=event_script.digest(c))


CUES = [
    _signed({"id": "s8_off_now", "scene": 1, "th": "ได้ค่ะ กำลังปิดไฟล็อบบี้ค่ะ", "numbers": []}),
    _signed({"id": "s8_off_done", "scene": 1, "th": "ปิดไฟล็อบบี้เรียบร้อยแล้วค่ะ", "numbers": []}),
    _signed({"id": "s8_open_now", "scene": 1, "th": "ได้เลยค่ะ กำลังเปิดผ้าม่านค่ะ", "numbers": []}),
    _signed({"id": "s8_open_done", "scene": 1, "th": "เปิดผ้าม่านเรียบร้อยแล้วค่ะ", "numbers": []}),
]
TICK_LIGHTS = {"do": "manual", "label_th": "ไฟล็อบบี้ดับแล้ว — ทีมไฟยืนยัน",
               "note_th": "หุ่นไม่ได้คุมไฟ: กดเมื่อไฟดับจริง แล้วค่อยกดบทถัดไป"}
TICK_CURTAIN = {"do": "manual", "label_th": "ผ้าม่านเปิดเสร็จ — ทีมงานยืนยัน", "note_th": "กดเมื่อม่านเปิดถึงจุดที่กำหนด"}
LIGHTS_OFF = {"do": "device", "device": "lobby_lights", "action": "off", "note_th": "กดตอน Emma พูดว่ากำลังปิด"}
CURTAIN_OPEN = {"do": "device", "device": "lobby_curtains", "action": "open"}

WITH = {"format": event_script.FORMAT, "host_th": "คุณมิกิ", "replies": "cues", "cues": CUES, "order": [
    {"n": 1, "title_th": "สาธิต", "steps": [
        {"do": "cue", "cue": "s8_off_now"}, LIGHTS_OFF, TICK_LIGHTS, {"do": "cue", "cue": "s8_off_done"},
        {"do": "cue", "cue": "s8_open_now"}, CURTAIN_OPEN, TICK_CURTAIN, {"do": "cue", "cue": "s8_open_done"}]},
    {"n": 2, "title_th": "จบ", "steps": [{"do": "manual", "label_th": "ปิดงาน"}]},
]}


def _without(doc: dict) -> dict:
    """The same script as it would have been written with no device step."""
    out = copy.deepcopy(doc)
    for sc in out["order"]:
        sc["steps"] = [st for st in sc["steps"] if st.get("do") != "device"]
    return out


DEVICES = {"format": venue_devices.FORMAT, "devices": {
    "lobby_lights": {"name_th": "ไฟล็อบบี้", "kind": "lights", "adapter": "broadlink",
                     "broadlink": {"codes": {"off": "lobby_lights_off", "on": "lobby_lights_on"}}},
    "lobby_curtains": {"name_th": "ผ้าม่านล็อบบี้", "kind": "curtains", "adapter": "broadlink",
                       "broadlink": {"codes": {"open": "lobby_curtains_open", "close": "lobby_curtains_close",
                                               "stop": "lobby_curtains_stop"}}}}}
CODES = {"lobby_lights_off": "2600aa01", "lobby_lights_on": "2600aa02", "lobby_curtains_open": "b20001",
         "lobby_curtains_close": "b20002", "lobby_curtains_stop": "b20003"}


@pytest.fixture
def script(tmp_path, monkeypatch):
    """The tracked event file (spots, clips) and a ceremony script of our own in tmp; the ceremony app on.
    Returns a writer: script(doc) puts that script in place."""
    target = tmp_path / "floor1_event.json"
    target.write_text(EVENT.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(settings, "event_file", str(target))
    monkeypatch.setattr(settings, "ceremony_file", str(tmp_path / "floor1_ceremony.json"))
    monkeypatch.setattr(event_walk, "RUN_DIR", tmp_path / ".run")
    monkeypatch.setattr(event_cues, "_live_call", lambda: object())

    def write(doc: dict) -> None:
        (tmp_path / "floor1_ceremony.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        for mod in (event_host, event_script, event_cues, event_order):
            mod.reset()
        event_order.STATE["pressing"] = None

    write(WITH)
    ceremony_on()
    yield write
    for mod in (event_host, event_script, event_cues, event_order):
        mod.reset()


@pytest.fixture
def hub(tmp_path, monkeypatch):
    """VENUE_DEVICES on, both devices on a fake Broadlink hub; `world["sent"]` = every frame it took."""
    world = {"sent": [], "auth_fails": 0}

    class Hub:
        timeout = None

        def auth(self):
            if world["auth_fails"] > 0:
                raise OSError("no answer")

        def send_data(self, payload):
            world["sent"].append(bytes(payload).hex())

    mod = types.ModuleType("broadlink")
    mod.__spec__ = importlib.machinery.ModuleSpec("broadlink", None)
    mod.gendevice = lambda devtype, host, mac, **k: Hub()
    monkeypatch.setitem(sys.modules, "broadlink", mod)
    monkeypatch.setattr(venue_devices, "_package", lambda: True)
    monkeypatch.setattr(venue_devices, "_sleep", lambda s: None)
    devices, codes = tmp_path / "venue_devices.json", tmp_path / "venue_codes.json"
    devices.write_text(json.dumps(DEVICES, ensure_ascii=False), encoding="utf-8")
    codes.write_text(json.dumps(CODES), encoding="utf-8")
    for name, value in (("venue_devices", True), ("venue_devices_file", str(devices)),
                        ("venue_codes_file", str(codes)), ("venue_broadlink_host", "127.0.0.1"),
                        ("venue_broadlink_mac", "aabbccddeeff"), ("venue_broadlink_devtype", "0x520b")):
        monkeypatch.setattr(settings, name, value)
    venue_devices.reset()
    return world


def _snap() -> dict:
    return asyncio.run(event_order.snapshot())


def _plain(snap: dict) -> list:
    """The order as staff see it: every step's content, its file index left out (a device step taken out
    of the file moves the indices after it -- the page never shows them)."""
    def step(st):
        out = {k: v for k, v in st.items() if k != "i"}
        if out.get("mark"):
            out["mark"] = {k: v for k, v in out["mark"].items() if k != "at"}       # the clock of the press
        return out

    return [(sc["n"], sc["title_th"], [step(st) for st in sc["steps"]]) for sc in snap["scenes"]]


def _label_at(snap: dict, at) -> str | None:
    if at is None:
        return None
    sc = next(s for s in snap["scenes"] if s["n"] == at[0])
    return next(st["label_th"] for st in sc["steps"] if st["i"] == at[1])


def _step(snap: dict, n: int, label_or_do: str) -> dict:
    sc = next(s for s in snap["scenes"] if s["n"] == n)
    return next(st for st in sc["steps"] if st["do"] == label_or_do or st["label_th"] == label_or_do)


# ---- off: exactly as before --------------------------------------------------------------------------

def test_the_real_order_without_its_device_steps_is_the_order_of_20_nov_byte_for_byte():
    real = json.loads(REAL_SCRIPT.read_text(encoding="utf-8"))["order"]
    before = json.loads(BEFORE.read_text(encoding="utf-8"))["order"]
    # (card 2, 24 Nov: `follow` is in the file but hidden from the order while CEREMONY_FOLLOW_HOST is off)
    stripped = [dict(sc, steps=[st for st in sc["steps"] if st.get("do") not in ("device", "follow")]) for sc in real]
    assert json.dumps(stripped, ensure_ascii=False, sort_keys=True) == json.dumps(before, ensure_ascii=False, sort_keys=True)
    assert sum(st.get("do") == "device" for sc in real for st in sc["steps"]) == 3


def test_off_the_real_order_on_the_page_is_the_one_before(monkeypatch):
    """The real file, VENUE_DEVICES off: what event_order serves is the 20 Nov order, step for step."""
    monkeypatch.setattr(settings, "ceremony_file", str(REAL_SCRIPT))
    event_script.reset()
    served = event_order._scenes()
    before = event_script.order(json.loads(BEFORE.read_text(encoding="utf-8")))

    def plain(scenes):
        return [(sc["n"], [{k: v for k, v in st.items() if k != "i"} for st in sc["steps"]]) for sc in scenes]

    assert plain(served) == plain(before)
    assert not any(st["do"] == "device" for sc in served for st in sc["steps"])


def test_off_the_staff_page_and_every_press_are_the_same_as_without_device_steps(script, calls):  # noqa: F811
    """Two ceremonies -- one written with device steps, one without -- run with "ถัดไป" to the end: the
    same steps shown, the same paths pressed in the same order, the same notes."""
    def walk() -> tuple:
        shown = [_plain(_snap())]
        pressed = []
        while True:
            before = _snap()
            got = asyncio.run(event_order.next())
            if got.get("error") == "end":
                break
            assert got["ok"], got
            pressed.append((got["do"], _label_at(before, before["next"])))
            shown.append(_plain(_snap()))
        return shown, pressed

    with_device = walk()
    calls_with = list(calls)
    calls.clear()
    script(_without(WITH))
    without = walk()
    assert with_device == without and calls_with == list(calls)
    assert [p[0] for p in with_device[1]] == ["cue", "manual", "cue", "cue", "manual", "cue", "manual"]
    # the tick's own note, as the file says it
    assert _step(_snap(), 1, TICK_LIGHTS["label_th"])["note_th"] == TICK_LIGHTS["note_th"]


def test_off_the_state_has_no_venue_card(script):
    from fastapi.testclient import TestClient

    from app import event_control_server

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "event_control_key", "staff-secret")
        client = TestClient(event_control_server.app)
        client.get("/ceremony-control?key=staff-secret")
        doc = client.get("/ceremony/state").json()
        assert "venue" not in doc
        r = client.post("/ceremony/device", json={"device": "lobby_lights", "action": "off"}).json()
        assert r["ok"] is False and r["error"] == "off" and r["hardware"] == "failed"
        client.close()


# ---- on: the beat, and the tick after it ---------------------------------------------------------------

def test_on_the_device_step_comes_before_the_tick_and_its_note_says_why(script, hub):
    snap = _snap()
    kinds = [st["do"] for st in snap["scenes"][0]["steps"]]
    assert kinds == ["cue", "device", "manual", "cue", "cue", "device", "manual", "cue"]
    dev = _step(snap, 1, "device")
    assert dev["label_th"] == "หุ่นสั่งปิดไฟล็อบบี้" and dev["ready"] is True and dev["why_th"] is None
    assert dev["note_th"] == LIGHTS_OFF["note_th"]
    tick = _step(snap, 1, TICK_LIGHTS["label_th"])
    assert tick["note_th"] != TICK_LIGHTS["note_th"] and "หุ่นสั่งปิดไฟล็อบบี้" in tick["note_th"]
    assert "ไฟดับ" in tick["note_th"] and "ส่งแล้วไม่ได้แปลว่าเปลี่ยนแล้ว" in tick["note_th"]
    curtain_tick = _step(snap, 1, TICK_CURTAIN["label_th"])
    assert "ม่านเปิด" in curtain_tick["note_th"]


def test_on_next_fires_the_light_at_its_beat_and_the_order_waits_for_the_tick(script, hub, calls):  # noqa: F811
    assert asyncio.run(event_order.next())["do"] == "cue"                     # "Turning off the lobby lights now"
    got = asyncio.run(event_order.next())
    assert got["ok"] and got["do"] == "device" and got["result"]["hardware"] == "sent"
    assert hub["sent"] == [CODES["lobby_lights_off"]]
    snap = _snap()
    mark = _step(snap, 1, "device")["mark"]
    assert mark["state"] == "done" and mark["hardware"] == "sent" and mark["error_th"] is None
    assert _label_at(snap, snap["next"]) == TICK_LIGHTS["label_th"]           # the room's word, still to come
    assert [c[0] for c in calls] == ["cue"]                                   # nothing else ran


def test_a_device_press_goes_while_her_line_is_still_heard_a_line_does_not(script, hub, calls, monkeypatch):  # noqa: F811
    monkeypatch.setattr(event_order, "_audible", lambda: True)
    snap = _snap()
    dev = _step(snap, 1, "device")
    assert asyncio.run(event_order.press(1, dev["i"]))["ok"]
    line = asyncio.run(event_order.press(1, 3))
    assert line["ok"] is False and line["error"] == "running"
    monkeypatch.setattr(event_order, "_robot_moving", lambda: True)
    assert asyncio.run(event_order.press(1, 5))["ok"]                         # the curtain, the robot moving
    assert hub["sent"] == [CODES["lobby_lights_off"], CODES["lobby_curtains_open"]]


def test_a_failed_command_stays_put_and_says_how_to_go_on_by_hand(script, hub, calls):  # noqa: F811
    hub["auth_fails"] = 99
    asyncio.run(event_order.next())                                           # the line
    got = asyncio.run(event_order.next())
    assert got["ok"] is False and got["result"]["hardware"] == "failed"
    assert "ทีมทำเอง" in got["error_th"] and "ข้ามขั้นนี้" in got["error_th"] and hub["sent"] == []
    snap = _snap()
    dev = _step(snap, 1, "device")
    assert dev["mark"]["state"] == "failed" and dev["mark"]["hardware"] == "failed"
    assert snap["next"] == [1, dev["i"]]                                      # "ถัดไป" = try again
    assert "ครั้งล่าสุด" in dev["why_th"] and dev["ready"] is True
    assert asyncio.run(event_order.skip(1, dev["i"]))["ok"]
    assert _label_at(_snap(), _snap()["next"]) == TICK_LIGHTS["label_th"]    # the tick still waits


def test_a_dry_run_is_done_with_its_warning_never_silently(script, hub, calls, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "venue_devices_dry_run", True)
    snap = _snap()
    dev = _step(snap, 1, "device")
    assert dev["ready"] is True and dev["why_th"] == venue_devices.DRY_RUN_TH
    assert asyncio.run(event_order.press(1, dev["i"]))["ok"]
    mark = _step(_snap(), 1, "device")["mark"]
    assert mark["state"] == "done" and mark["hardware"] == "mock" and mark["error_th"] == venue_devices.DRY_RUN_TH
    assert hub["sent"] == []


def test_a_device_step_is_the_ceremony_apps_like_every_press_but_a_tick(script, hub):
    ceremony_off()
    snap = _snap()
    dev = _step(snap, 1, "device")
    assert dev["ready"] is False and "Emma Ceremony" in dev["why_th"]
    assert asyncio.run(event_order.press(1, dev["i"]))["error"] == "not_ceremony_app" and hub["sent"] == []


def test_a_device_not_driven_here_vanishes_from_the_order(script, hub, monkeypatch, tmp_path):
    doc = copy.deepcopy(DEVICES)
    doc["devices"]["lobby_curtains"]["adapter"] = "manual"
    (tmp_path / "venue_devices.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    venue_devices.reset()
    kinds = [st["do"] for st in _snap()["scenes"][0]["steps"]]
    assert kinds == ["cue", "device", "manual", "cue", "cue", "manual", "cue"]
    tick = _step(_snap(), 1, TICK_CURTAIN["label_th"])
    assert tick["note_th"] == TICK_CURTAIN["note_th"]                         # no device before it: the file's


def test_the_red_stop_does_not_touch_a_light(script, hub, calls, monkeypatch):  # noqa: F811
    from fastapi.testclient import TestClient

    from app import event_control_server, robot_body

    async def staff_stop():
        return {"sent": True, "error_th": None, "note_th": None}

    async def walk_stop(*a, **k):
        return {"ok": True, "stopped": False}

    monkeypatch.setattr(robot_body, "staff_stop", staff_stop)
    monkeypatch.setattr(event_host, "stop", walk_stop)
    monkeypatch.setattr(settings, "event_control_key", "staff-secret")
    dev = _step(_snap(), 1, "device")
    assert asyncio.run(event_order.press(1, dev["i"]))["ok"]
    sent = list(hub["sent"])
    client = TestClient(event_control_server.app)
    client.get("/ceremony-control?key=staff-secret")
    assert client.post("/event/stop", json={}).status_code == 200
    client.close()
    assert hub["sent"] == sent                                                # nothing turned back on
    assert _step(_snap(), 1, "device")["mark"]["state"] == "done"


def test_a_device_marks_word_survives_a_restart(script, hub, calls, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(event_order, "state_path", lambda: tmp_path / "run" / "order.json")
    dev = _step(_snap(), 1, "device")
    asyncio.run(event_order.press(1, dev["i"]))
    event_order.reset()                                                       # the server starts again
    mark = _step(_snap(), 1, "device")["mark"]
    assert mark["state"] == "done" and mark["hardware"] == "sent"


# ---- the real file and its checks ---------------------------------------------------------------------

def test_every_real_device_step_sits_before_its_tick_and_the_done_line_after_it():
    order = event_script.order(event_script.load(REAL_SCRIPT))
    steps = [st for sc in order for st in sc["steps"]]
    found = []
    for k, st in enumerate(steps):
        if st["do"] != "device":
            continue
        assert steps[k - 1]["do"] == "cue" and steps[k + 1]["do"] == "manual" and steps[k + 2]["do"] == "cue"
        found.append((steps[k - 1]["cue"], st["device"], st["action"], steps[k + 2]["cue"]))
    assert found == [("s08_l06", "lobby_lights", "off", "s08_l08"), ("s08_l10", "lobby_lights", "on", "s08_l12"),
                     ("s08_l14", "lobby_curtains", "open", "s08_l16")]
    event = event_host._normalise(json.loads(EVENT.read_text(encoding="utf-8")), {})
    assert event_script.order_problems(event_script.load(REAL_SCRIPT), event) == []


def test_order_problems_name_an_unknown_device_or_action(monkeypatch):
    doc = copy.deepcopy(WITH)
    doc["order"][0]["steps"][1] = {"do": "device", "device": "stage_fog", "action": "on"}
    doc["order"][0]["steps"][5] = {"do": "device", "device": "lobby_curtains", "action": "off"}
    doc["order"][1]["steps"].append({"do": "device", "device": "lobby_lights", "action": "off", "url": "x"})
    # off (this machine drives nothing): the steps are hidden, their ids are not this machine's business --
    # a half-edited venue file must not turn the morning "ลำดับพิธี" row red (review 20 Nov); the shape is
    probs = event_script.order_problems(doc, {"spots": {}, "clips": {}})
    assert not any("unknown_device" in p or "bad_action" in p for p in probs)
    assert any("unexpected_field:url" in p for p in probs)
    monkeypatch.setattr(settings, "venue_devices", True)
    probs = event_script.order_problems(doc, {"spots": {}, "clips": {}})
    assert any("unknown_device:stage_fog" in p for p in probs)
    assert any("bad_action:off" in p for p in probs)
    assert any("unexpected_field:url" in p for p in probs)


def test_a_half_edited_venue_file_leaves_the_morning_order_row_alone_while_off(tmp_path, monkeypatch):
    """The reviewer's proof turned round: VENUE_DEVICES off, a venue file whose devices are half filled in."""
    bad = tmp_path / "venue.json"
    bad.write_text(json.dumps({"format": venue_devices.FORMAT, "devices": {
        "lobby_lights": {"name_th": "ไฟ", "kind": "lights", "adapter": "http"}}}), encoding="utf-8")
    monkeypatch.setattr(settings, "venue_devices_file", str(bad))
    venue_devices.reset()
    event = event_host._normalise(json.loads(EVENT.read_text(encoding="utf-8")), {})
    assert event_script.order_problems(event_script.load(REAL_SCRIPT), event) == []
    monkeypatch.setattr(settings, "venue_devices", True)
    assert any("unknown_device" in p for p in event_script.order_problems(event_script.load(REAL_SCRIPT), event))


def test_after_an_unknown_command_the_order_says_look_first(script, hub, calls, monkeypatch):  # noqa: F811
    async def unknown(device_id, action, *, by="staff", cfg=None):
        return {"ok": False, "device": device_id, "action": action, "hardware": "unknown", "error": "no_ack",
                "error_th": venue_devices.ERRORS_TH["no_ack"]}

    monkeypatch.setattr(venue_devices, "run", unknown)
    dev = _step(_snap(), 1, "device")
    got = asyncio.run(event_order.press(1, dev["i"]))
    assert got["ok"] is False and "ดูห้องก่อน" in got["error_th"] and "แตะสองครั้ง" in got["error_th"]
    assert "ลองอีกครั้ง" not in got["error_th"]
    assert _step(_snap(), 1, "device")["mark"]["hardware"] == "unknown"


def test_a_device_press_holds_no_other_step_and_its_own_step_once(script, hub, calls, monkeypatch):  # noqa: F811
    """Review 20 Nov: a hub that does not answer held every other step for the whole timeout."""
    async def go():
        gate = asyncio.Event()

        async def slow(device_id, action, *, by="staff", cfg=None):
            await gate.wait()
            return {"ok": True, "device": device_id, "action": action, "hardware": "sent", "error": None,
                    "error_th": None}

        monkeypatch.setattr(venue_devices, "run", slow)
        dev = _step(await event_order.snapshot(), 1, "device")
        first = asyncio.ensure_future(event_order.press(1, dev["i"]))
        await asyncio.sleep(0.05)
        again = await event_order.press(1, dev["i"])               # the same step, still sending
        line = await event_order.press(1, 3)                        # an unrelated step goes
        tick = await event_order.press(1, 2)
        gate.set()
        return await first, again, line, tick

    first, again, line, tick = asyncio.run(go())
    assert first["ok"] and again["error"] == "busy"
    assert line["ok"] and tick["ok"]
    assert ("cue", "s8_off_done", "staff", None) in calls
    assert not event_order._DEVICE_PRESSING


def test_two_taps_landing_together_on_one_device_step_send_once(script, hub, monkeypatch):
    """Its gate is set before the first await (the order's rule): both read their readiness first."""
    sent = []

    async def go():
        gate = asyncio.Event()

        async def slow(device_id, action, *, by="staff", cfg=None):
            sent.append(action)
            await gate.wait()
            return {"ok": True, "device": device_id, "action": action, "hardware": "sent", "error": None,
                    "error_th": None}

        monkeypatch.setattr(venue_devices, "run", slow)
        dev = _step(await event_order.snapshot(), 1, "device")
        real_context = event_order._context

        async def reading():
            await asyncio.sleep(0.01)                     # reading readiness takes a moment: both are in it
            return await real_context()

        monkeypatch.setattr(event_order, "_context", reading)
        both = asyncio.ensure_future(asyncio.gather(event_order.press(1, dev["i"]), event_order.press(1, dev["i"])))
        await asyncio.sleep(0.05)
        gate.set()
        return await both

    a, b = asyncio.run(go())
    assert sorted([a["ok"], b["ok"]]) == [False, True] and (a.get("error") or b.get("error")) == "busy"
    assert sent == ["off"] and _step(_snap(), 1, "device")["mark"]["state"] == "done"


# ---- the staff server and page ------------------------------------------------------------------------

def test_the_card_endpoint_is_behind_the_key_and_answers_how_far_it_got(script, hub, monkeypatch):
    from fastapi.testclient import TestClient

    from app import event_control_server

    monkeypatch.setattr(settings, "event_control_key", "staff-secret")
    client = TestClient(event_control_server.app)
    assert client.post("/ceremony/device", json={"device": "lobby_lights", "action": "off"}).status_code == 403
    assert hub["sent"] == []
    client.get("/ceremony-control?key=staff-secret")
    assert client.post("/ceremony/device", json={"device": ["x"], "action": "off"}).status_code == 400
    r = client.post("/ceremony/device", json={"device": "lobby_lights", "action": "off"}).json()
    assert r["ok"] is True and r["hardware"] == "sent" and hub["sent"] == [CODES["lobby_lights_off"]]
    venue = client.get("/ceremony/state").json()["venue"]
    assert [d["id"] for d in venue["devices"]] == ["lobby_lights", "lobby_curtains"]
    assert venue["devices"][0]["last"]["hardware"] == "sent"
    client.close()


PAGE = (ROOT / "client" / "ceremony-control.html").read_text(encoding="utf-8")


def test_the_page_card_and_the_device_step_in_the_source():
    script = PAGE[PAGE.index("<script>"):]
    assert "post('/ceremony/device', { device: d.id, action: a.id })" in script
    assert "renderVenue(state.venue);" in script
    assert "var TWO_TAP_KINDS = { walk: true, handoff: true, device: true, opening: true, follow: true };" in script
    assert "if (st && st.do === 'device') return venueOutcome(r.result, label);" in script
    assert 'id="venueCard" hidden' in PAGE


@pytest.fixture(scope="module")
def chromium():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright is not installed -- the ceremony page's lights/curtains card and device step "
                    "are NOT being checked in a browser")
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True)
    except Exception as exc:                             # noqa: BLE001
        pw.stop()
        pytest.skip("headless Chromium is not installed (%s) -- the lights/curtains card is NOT being checked "
                    "in a browser" % str(exc).splitlines()[0])
    yield browser
    browser.close()
    pw.stop()


def _page_state(venue=None, *, mark=None, ready=True, why=None):
    from tests.test_event_order_page import _mark, _order, _state, _step as _pstep

    steps = [_pstep(0, "cue", "Emma: «ได้ค่ะ กำลังปิดไฟล็อบบี้ค่ะ»", line_th="ได้ค่ะ กำลังปิดไฟล็อบบี้ค่ะ", voice="read",
                    mark=_mark("done")),
             _pstep(1, "device", "หุ่นสั่งปิดไฟล็อบบี้", ready=ready, why_th=why, mark=mark),
             _pstep(2, "manual", "ไฟล็อบบี้ดับแล้ว — ทีมไฟยืนยัน", note_th="หุ่นสั่งปิดไฟล็อบบี้ที่ขั้นก่อนหน้าแล้ว")]
    doc = _state(_order([{"n": 8, "title_th": "สาธิตระบบ Smart Home", "steps": steps}], last=[8, 0], next=[8, 1]))
    if venue is not None:
        doc["venue"] = venue
    return doc


VENUE = {"dry_run": False, "devices": [{
    "id": "lobby_lights", "name_th": "ไฟล็อบบี้", "kind": "lights", "adapter": "broadlink", "busy": False,
    "warn_th": None, "last": None,
    "actions": [{"id": "off", "th": "ปิด", "ready": True, "why_th": None},
                {"id": "on", "th": "เปิด", "ready": False, "why_th": "ยังไม่ได้สอนรหัส lobby_lights_on"}]}]}


def test_the_card_takes_two_taps_and_says_sent_not_done(chromium):
    from tests.test_event_order_page import Page

    p = Page(chromium, _page_state(VENUE), mode=None)
    try:
        assert p.visible("#venueCard") and "ส่งแล้วไม่ได้แปลว่าเปลี่ยนแล้ว" in p.text("#venueSub")
        buttons = p.page.locator("#venueList button")
        assert buttons.count() == 2 and buttons.nth(1).is_disabled()
        assert "ยังไม่ได้สอนรหัส" in p.text("#venueList")
        p.posts["/ceremony/device"] = {"ok": True, "hardware": "sent", "device": "lobby_lights", "action": "off"}
        buttons.nth(0).click()
        p.page.wait_for_timeout(450)
        assert not [s for s in p.sent if s[0] == "/ceremony/device"]              # one tap only arms it
        assert "แตะอีกครั้ง" in p.text("#venueList")
        p.page.locator("#venueList button").nth(0).click()
        toast = p.toast()
        assert [s for s in p.sent if s[0] == "/ceremony/device"] == [("/ceremony/device",
                                                                       {"device": "lobby_lights", "action": "off"})]
        assert "ส่งคำสั่งแล้ว" in toast and "ดูห้อง" in toast
    finally:
        p.close()


def test_without_a_venue_the_card_is_hidden(chromium):
    from tests.test_event_order_page import Page

    p = Page(chromium, _page_state(None), mode=None)
    try:
        assert not p.visible("#venueCard")
    finally:
        p.close()


@pytest.mark.parametrize("how", ["order-next", "order-row", "manual"])
def test_after_an_unknown_command_a_resend_takes_two_taps(chromium, how):
    """Review 20 Nov: "ถัดไป" resent a light command whose last result was "unknown" with one tap -- a
    toggle remote would have put the lights back on."""
    from tests.test_event_order_page import Page, _mark

    mark = dict(_mark("failed", error_th="ไม่รู้ผล — ดูห้องก่อนกดซ้ำ"), hardware="unknown")
    mode = "manual" if how == "manual" else "order"
    p = Page(chromium, _page_state(VENUE, mark=mark), mode=mode)
    try:
        p.posts["/ceremony/order/next"] = {"ok": True, "n": 8, "i": 1, "do": "device",
                                           "result": {"ok": True, "hardware": "sent"}}
        p.posts["/ceremony/order/step"] = p.posts["/ceremony/order/next"]
        # "ตามลำดับ": its big "ถัดไป", or a tap on the row of the step it stands at; "กดเอง": the step's button
        button = p.page.locator({"order-next": "#nextBtn", "order-row": "#step-8-1 .spress",
                                 "manual": "#step-8-1 .sgo"}[how])
        if mode == "manual":
            assert "แตะสองครั้ง" in button.inner_text()
        button.click()
        p.page.wait_for_timeout(450)
        assert not [s for s in p.sent if s[0].startswith("/ceremony/order/")]      # one tap only arms it
        button.click()
        p.page.wait_for_timeout(300)
        assert [s[0] for s in p.sent if s[0].startswith("/ceremony/order/")] == [
            "/ceremony/order/next" if how == "order-next" else "/ceremony/order/step"]
    finally:
        p.close()


def test_a_device_step_done_reads_sent_not_done(chromium):
    from tests.test_event_order_page import Page, _mark

    mark = dict(_mark("done"), hardware="sent")
    p = Page(chromium, _page_state(VENUE, mark=mark), mode=None)
    try:
        row = p.text("#step-8-1")
        assert "ส่งแล้ว (ยังไม่ยืนยัน)" in row and "ทำแล้ว ✓" not in row and "ดูห้อง" in row
        assert "ไฟ/ม่าน" in row
    finally:
        p.close()
