"""The link's health without an API call (app/net_health.py): hysteresis,
transition-only turn-log rows, reason categories, switch-off silence.
No test touches the network: the probe's DNS and connect are stubbed."""
from __future__ import annotations

import asyncio
import socket
import ssl

import pytest

from app import net_health
from app.config import settings


@pytest.fixture
def rows(monkeypatch):
    seen = []
    from app import turnlog

    monkeypatch.setattr(turnlog, "record", lambda event, **f: seen.append((event, f)))
    return seen


def test_one_failure_is_not_an_outage(rows):
    h = net_health.NetHealth()
    h.failure("connect")
    assert h.state == "online" and rows == []


def test_two_failures_in_a_row_go_offline_once(rows):
    h = net_health.NetHealth()
    h.failure("dns")
    h.failure("dns")
    h.failure("dns")
    assert h.offline()
    assert rows == [("net_state", {"state": "offline", "reason": "dns"})]


def test_a_success_between_failures_resets_the_count():
    h = net_health.NetHealth()
    h.failure("connect")
    h.probe_ok()
    h.failure("connect")
    assert not h.offline()
    h.dial_ok()                       # a Gemini session opened
    h.failure("connect")
    assert not h.offline()
    h.failure("connect")              # now two in a row
    assert h.offline()


def test_online_again_only_after_two_probe_successes(rows):
    h = net_health.NetHealth()
    h.failure("timeout")
    h.failure("timeout")
    h.probe_ok()
    assert h.offline()
    h.dial_ok()                       # a dial is not the link coming back
    h.probe_ok()
    assert not h.offline()
    assert [r[1]["state"] for r in rows] == ["offline", "online"]
    # categories only -- never an exception's text
    assert all(set(f) == {"state", "reason"} and f["reason"] in net_health.REASONS for _, f in rows)


def test_an_unknown_reason_is_folded_into_a_category():
    h = net_health.NetHealth()
    h.failure("socket.gaierror: robot_dns: generativelanguage.googleapis.com (timed out)")
    h.failure("x")
    assert h.last_reason == "connect"


def test_only_network_dial_failures_count(monkeypatch):
    monkeypatch.setattr(settings, "offline_fallback", True)
    for _ in range(3):
        net_health.note_dial_failure("quota")
        net_health.note_dial_failure("auth")
    assert not net_health.is_offline()
    net_health.note_dial_failure("network")
    net_health.note_dial_failure("network")
    assert net_health.is_offline()


def test_failed_dials_keep_a_streak_only_an_opened_dial_resets(rows):
    """Review 26 Oct: probe successes reset the one failure count, so a link
    that answers the probe and refuses every Live dial never left ONLINE."""
    h = net_health.NetHealth()
    assert h.dial_failure(now=0.0) == 0.0, "one failed dial is a glitch: no back-off"
    h.probe_ok()
    h.probe_ok()
    assert h.dial_failure(now=1.0) == net_health.DIAL_BACKOFF_S, "probes do not reset the dial streak"
    assert h.dial_backoff_left(now=1.0 + 30) == net_health.DIAL_BACKOFF_S - 30
    assert h.dial_backoff_left(now=1.0 + net_health.DIAL_BACKOFF_S) == 0.0
    assert h.dial_failure(now=100.0) == 2 * net_health.DIAL_BACKOFF_S
    for n in range(10):
        wait = h.dial_failure(now=200.0 + n)
    assert wait == net_health.DIAL_BACKOFF_MAX_S, "capped: one deaf dial per cap at worst"
    assert [f for e, f in rows if e == "dial_backoff"][0] == {"streak": 2, "wait_s": 60}
    h.dial_ok()
    assert h.dial_fails == 0 and h.dial_backoff_left(now=210.0) == 0.0


def test_the_back_off_is_the_switchs(monkeypatch):
    monkeypatch.setattr(settings, "offline_fallback", True)
    net_health.note_dial_failure("network")
    net_health.note_dial_failure("network")
    assert net_health.dial_backoff_left() > 0
    monkeypatch.setattr(settings, "offline_fallback", False)
    assert net_health.dial_backoff_left() == 0.0


def test_switch_off_never_says_offline(monkeypatch):
    net_health.health.failure("dns")
    net_health.health.failure("dns")
    assert net_health.health.offline()
    monkeypatch.setattr(settings, "offline_fallback", False)
    assert not net_health.is_offline()


def test_no_background_probe_with_the_switch_off():
    async def go():
        return net_health.start()

    assert asyncio.run(go()) is None


def _stub_dns(monkeypatch, result):
    async def fake(self, host, port, **kw):
        if isinstance(result, BaseException):
            raise result
        if callable(result):
            return await result()
        return result

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", fake)


def test_probe_reason_dns(monkeypatch):
    _stub_dns(monkeypatch, socket.gaierror(socket.EAI_NONAME, "robot_dns: x"))
    assert asyncio.run(net_health.probe(timeout=1)) == (False, "dns")


def test_probe_dns_that_never_answers_is_dns(monkeypatch):
    async def hang():
        await asyncio.sleep(5)

    _stub_dns(monkeypatch, hang)
    assert asyncio.run(net_health.probe(timeout=0.2)) == (False, "dns")


def _resolved(monkeypatch, connect):
    _stub_dns(monkeypatch, [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443))])
    monkeypatch.setattr(asyncio, "open_connection", connect)


def test_probe_reasons_connect_tls_timeout(monkeypatch):
    async def refused(*a, **k):
        raise ConnectionRefusedError("nope")

    _resolved(monkeypatch, refused)
    assert asyncio.run(net_health.probe(timeout=1)) == (False, "connect")

    async def bad_cert(*a, **k):
        raise ssl.SSLCertVerificationError("bad")

    _resolved(monkeypatch, bad_cert)
    assert asyncio.run(net_health.probe(timeout=1)) == (False, "tls")

    async def slow(*a, **k):
        await asyncio.sleep(5)

    _resolved(monkeypatch, slow)
    assert asyncio.run(net_health.probe(timeout=0.2)) == (False, "timeout")


def test_probe_ok_uses_the_resolved_address_and_the_name_for_sni(monkeypatch):
    seen = {}

    class Writer:
        def close(self):
            seen["closed"] = True

        async def wait_closed(self):
            return None

    async def ok(host, port, **kw):
        seen.update(host=host, port=port, sni=kw.get("server_hostname"), ssl=kw.get("ssl"))
        return object(), Writer()

    _resolved(monkeypatch, ok)
    assert asyncio.run(net_health.probe(timeout=1)) == (True, "probe_ok")
    assert seen["host"] == "192.0.2.1" and seen["port"] == 443
    assert seen["sni"] == net_health.HOST and isinstance(seen["ssl"], ssl.SSLContext)
    assert seen["closed"]


def test_probe_now_feeds_the_hysteresis(monkeypatch):
    _stub_dns(monkeypatch, socket.gaierror(socket.EAI_NONAME, "x"))
    asyncio.run(net_health.probe_now())
    asyncio.run(net_health.probe_now())
    assert net_health.health.offline()
