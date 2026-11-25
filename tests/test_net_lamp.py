"""The staff pages' "เน็ต" lamp (owner 10 Nov 2026: "วิธีทำเน็ตไม่หลุด หรือแก้ปัญหาเรื่องเน็ต เพราะตอนวันงาน
คนน่าจะต้องเยอะมาก" ... "อันไหนดีแล้วไม่ส่งผลก็ทำเลย").

What these hold down:
* NET_STATUS runs app/net_health.py's probe and SHOWS it -- nothing it decides: with OFFLINE_FALLBACK
  off a link the probe calls down still dials Gemini (no offline voice, no back-off);
* the lamp's levels: ok / slow / flaky (one failed probe) / down (the hysteresis's OFFLINE) / unknown;
* both phone pages draw it, and "down" says what still works.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app import event_host, event_walk, net_health
from app.config import settings
from tests.test_event_order_page import Page, chromium  # noqa: F401 -- the fixture, used by name

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def lamp_on(monkeypatch):
    monkeypatch.setattr(settings, "net_status", True)
    monkeypatch.setattr(settings, "net_probe_s", 15.0)
    net_health.reset()
    yield
    net_health.reset()


def _probe(monkeypatch, *results):
    answers = list(results)

    async def probe(host=net_health.HOST, port=net_health.PORT, timeout=None):
        return answers.pop(0)

    monkeypatch.setattr(net_health, "probe", probe)


def test_off_by_default_and_nothing_probes():
    source = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    assert 'net_status: bool = _get_bool("NET_STATUS", False)' in source
    assert net_health.watched() is False and net_health.staff_view() is None
    assert event_host._net_view() is None


def test_the_lamp_levels(lamp_on, monkeypatch):
    assert net_health.staff_view()["level"] == "unknown"
    _probe(monkeypatch, (True, "probe_ok"), (False, "dns"), (False, "connect"), (True, "probe_ok"),
           (True, "probe_ok"))
    asyncio.run(net_health.probe_now())
    view = net_health.staff_view()
    assert view["level"] == "ok" and isinstance(view["ms"], int) and view["reason"] is None
    asyncio.run(net_health.probe_now())
    assert net_health.staff_view() == {**net_health.staff_view(), "level": "flaky", "reason": "dns"}
    asyncio.run(net_health.probe_now())
    assert net_health.staff_view()["level"] == "down"               # two in a row: the hysteresis
    asyncio.run(net_health.probe_now())
    assert net_health.staff_view()["level"] == "down"               # one answer is not "back"
    asyncio.run(net_health.probe_now())
    assert net_health.staff_view()["level"] == "ok"
    net_health.health.last_probe_ms = net_health.SLOW_MS
    assert net_health.staff_view()["level"] == "slow"


def test_shown_only_nothing_decided(lamp_on, monkeypatch):
    """OFFLINE_FALLBACK off: a link the lamp calls down changes no dial."""
    assert settings.offline_fallback is False
    _probe(monkeypatch, (False, "dns"), (False, "dns"))
    asyncio.run(net_health.probe_now())
    asyncio.run(net_health.probe_now())
    assert net_health.health.offline() and net_health.staff_view()["level"] == "down"
    assert net_health.is_offline() is False and net_health.dial_backoff_left() == 0.0
    net_health.health.dial_failure()
    net_health.health.dial_failure()
    assert net_health.dial_backoff_left() == 0.0
    assert "network" not in json.dumps(net_health.staff_view())     # categories only, no hostnames
    # the server's startup hook starts the prober for the lamp too
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "if not (settings.offline_fallback or settings.net_status):\n        return" in main


def test_the_prober_starts_for_the_lamp(lamp_on, monkeypatch):
    _probe(monkeypatch, *([(True, "probe_ok")] * 5))

    async def go():
        task = net_health.start()
        assert task is not None
        await asyncio.sleep(0.05)
        net_health.stop()
        return net_health.staff_view()["level"]

    assert asyncio.run(go()) == "ok"
    monkeypatch.setattr(settings, "net_probe_s", 0.0)
    assert net_health.start() is None                              # NET_PROBE_S=0 = nothing probes


def test_both_staff_states_carry_it(lamp_on, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "event_file", str(ROOT / "data" / "events" / "floor1_event.json"))
    monkeypatch.setattr(event_walk, "RUN_DIR", tmp_path / ".run")
    event_host.reset()
    assert event_host.shared_snapshot()["net"]["level"] == "unknown"
    assert event_host.snapshot()["net"]["level"] == "unknown"       # the Event page's, built on it


@pytest.mark.parametrize("path,page,app_name", [
    ("/event-control", "event-control.html", "Emma Event"),
    ("/ceremony-control", "ceremony-control.html", "Emma Ceremony"),
])
def test_both_pages_draw_the_lamp(chromium, path, page, app_name, monkeypatch, tmp_path):  # noqa: F811
    from app import ceremony, event_control_server, event_cues, room_screens
    from fastapi.testclient import TestClient

    monkeypatch.setattr(settings, "event_file", str(ROOT / "data" / "events" / "floor1_event.json"))
    monkeypatch.setattr(settings, "ceremony_file", str(ROOT / "data" / "ceremony" / "floor1_ceremony.json"))
    monkeypatch.setattr(settings, "event_control_key", "staff-secret")
    monkeypatch.setattr(event_walk, "RUN_DIR", tmp_path / ".run")
    monkeypatch.setattr(event_cues, "_live_call", lambda: object())
    monkeypatch.setattr(room_screens, "offered", lambda: True)
    monkeypatch.setattr(ceremony, "on", lambda: path == "/ceremony-control")
    event_host.reset()
    client = TestClient(event_control_server.app)
    client.get(path + "?key=staff-secret")
    state = client.get("/event/state" if path == "/event-control" else "/ceremony/state").json()
    client.close()
    html = (ROOT / "client" / page).read_text(encoding="utf-8")
    shown = {}
    for level, ms in (("ok", 180), ("slow", 1400), ("flaky", 300), ("down", None), (None, None)):
        st = dict(state, net=None if level is None else {"level": level, "ms": ms, "ago_s": 3, "reason": None})
        p = Page(chromium, st, html=html, path=path)
        try:
            lamp = p.page.locator("#lampNet")
            shown[level] = (lamp.get_attribute("class"), p.text("#netText"), p.text("#banner"))
        finally:
            p.close()
    assert "on" in shown["ok"][0].split() and shown["ok"][1] == "เน็ต 0.2 วิ"
    assert "warn" in shown["slow"][0].split() and shown["slow"][1] == "เน็ตช้า 1.4 วิ"
    assert "warn" in shown["flaky"][0].split() and shown["flaky"][1].startswith("เน็ตสะดุด")
    assert "off" in shown["down"][0].split() and shown["down"][1] == "เน็ตหลุด"
    assert "อินเทอร์เน็ตของหุ่นหลุด — Emma ตอบไม่ได้" in shown["down"][2]
    assert "อินเทอร์เน็ตของหุ่นหลุด" not in shown["ok"][2]
    assert shown[None][1] == "เน็ต ?" and not {"on", "off", "warn"} & set(shown[None][0].split())


def test_the_lamp_goes_grey_when_this_phone_loses_the_robot(chromium, monkeypatch, tmp_path):  # noqa: F811
    """Review 10 Nov: the lamp kept the last poll's green "เน็ต 0.1 วิ" beside a red server lamp."""
    from tests.test_event_order_page import BASE

    monkeypatch.setattr(settings, "event_file", str(ROOT / "data" / "events" / "floor1_event.json"))
    monkeypatch.setattr(event_walk, "RUN_DIR", tmp_path / ".run")
    event_host.reset()
    state = json.loads(json.dumps(event_host.snapshot(), default=str))
    state.update(server_time=0, net={"level": "ok", "ms": 120, "ago_s": 2, "reason": None})
    html = (ROOT / "client" / "event-control.html").read_text(encoding="utf-8")
    p = Page(chromium, state, html=html, path="/event-control")
    try:
        assert "on" in p.page.locator("#lampNet").get_attribute("class").split()
        p.page.route(BASE + "/event/state", lambda r: r.abort("failed"))
        p.page.wait_for_function("!document.getElementById('lampServer').classList.contains('on')", timeout=8000)
        p.page.wait_for_function("document.getElementById('netText').textContent === 'เน็ต ?'", timeout=4000)
        assert not {"on", "off", "warn"} & set(p.page.locator("#lampNet").get_attribute("class").split())
    finally:
        p.close()
