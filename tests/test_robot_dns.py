"""scripts/robot_dns.py -- name resolution for the glibc Python on the robot.

Android has no /etc/resolv.conf, so glibc's getaddrinfo failed every name
(measured 9 Oct: gaierror -3 for generativelanguage.googleapis.com while the
robot pinged 8.8.8.8). The shim asks A records over UDP itself. No real
network here: socket.socket is replaced by a scripted fake.
"""
from __future__ import annotations

import socket
import struct
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import robot_dns  # noqa: E402


def _name(name: str) -> bytes:
    return b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00"


def answer(qid: int, name: str, records: list[tuple[int, int, bytes]], rcode: int = 0,
           qr: bool = True) -> bytes:
    """A response: the question, then (type, ttl, rdata) records. Each record's
    owner name is a compression pointer -- to the question (offset 12) for the
    first, to the previous CNAME's rdata for the rest, the way servers write a
    CNAME chain."""
    flags = (0x8000 if qr else 0) | 0x0100 | 0x0080 | rcode
    msg = bytearray(struct.pack(">HHHHHH", qid, flags, 1, len(records), 0, 0))
    msg += _name(name) + struct.pack(">HH", 1, 1)
    owner = 12
    for rtype, ttl, rdata in records:
        msg += struct.pack(">H", 0xC000 | owner) + struct.pack(">HHIH", rtype, 1, ttl, len(rdata))
        rdata_at = len(msg)
        msg += rdata
        if rtype == 5:
            owner = rdata_at
    return bytes(msg)


def a(ip: str, ttl: int = 60) -> tuple[int, int, bytes]:
    return 1, ttl, socket.inet_aton(ip)


def cname(target: str, ttl: int = 300) -> tuple[int, int, bytes]:
    return 5, ttl, _name(target)


# ---- parse --------------------------------------------------------------------------

def test_parse_follows_compression_through_a_cname_to_the_a_records():
    msg = answer(0x1234, "generativelanguage.googleapis.com",
                 [cname("googleapis.l.google.com"), a("142.250.66.10", 120), a("142.250.66.42", 45)])
    assert robot_dns.parse(msg, 0x1234) == (["142.250.66.10", "142.250.66.42"], 45)


def test_parse_refuses_someone_elses_answer_a_query_and_a_runt():
    msg = answer(0x1234, "example.com", [a("1.2.3.4")])
    with pytest.raises(ValueError):
        robot_dns.parse(msg, 0x4321)                       # wrong id
    with pytest.raises(ValueError):
        robot_dns.parse(answer(0x1234, "example.com", [], qr=False), 0x1234)   # a query, not an answer
    with pytest.raises(ValueError):
        robot_dns.parse(b"\x12\x34\x81", 0x1234)          # a stray, not a broken answer


def test_parse_reports_a_server_error():
    with pytest.raises(LookupError):
        robot_dns.parse(answer(7, "nope.invalid", [], rcode=3), 7)


def test_the_query_is_one_a_question_with_recursion():
    q = robot_dns._query("Example.COM.")
    _qid, flags, qd, an, ns, ar = struct.unpack(">HHHHHH", q[:12])
    assert (flags, qd, an, ns, ar) == (0x0100, 1, 0, 0, 0)
    assert q[12:] == _name("Example.COM") + struct.pack(">HH", 1, 1)


# ---- resolve, with a scripted network ----------------------------------------------------

class Net:
    """socket.socket stand-in. `script[server]` turns the query packet into the
    datagrams that server sends back, in order; then the socket times out."""

    def __init__(self, script):
        self.script = script
        self.asked: list[str] = []

    def socket(self, family, kind):
        assert (family, kind) == (socket.AF_INET, socket.SOCK_DGRAM)
        return _Sock(self)


class _Sock:
    def __init__(self, net: Net):
        self.net, self.queue = net, iter(())

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def settimeout(self, t):
        pass

    def sendto(self, packet, addr):
        server, port = addr
        assert port == 53
        self.net.asked.append(server)
        qid = struct.unpack(">H", packet[:2])[0]
        # an iterator, not a list: a flooding server is an endless generator
        self.queue = iter(self.net.script.get(server, lambda q: [])(qid))

    def recvfrom(self, n):
        item = next(self.queue, None)
        if item is None:
            raise socket.timeout("timed out")
        return item


@pytest.fixture
def net(monkeypatch):
    def install(script, dns=""):
        n = Net(script)
        monkeypatch.setattr(robot_dns.socket, "socket", n.socket)
        monkeypatch.setenv("EMMA_DNS", dns)
        monkeypatch.setattr(robot_dns, "_cache", {})
        return n
    return install


def test_strays_are_skipped_and_the_real_answer_is_taken(net):
    n = net({"192.168.1.1": lambda qid: [
        (answer(qid, "x.example", [a("6.6.6.6")]), ("10.9.9.9", 53)),     # not who we asked
        (answer(qid ^ 1, "x.example", [a("6.6.6.6")]), ("192.168.1.1", 53)),   # wrong id
        (b"\x00\x01", ("192.168.1.1", 53)),                                 # a runt
        (answer(qid, "x.example", [a("93.184.216.34")]), ("192.168.1.1", 53)),
    ]}, dns="192.168.1.1")
    assert robot_dns.resolve("x.example") == ["93.184.216.34"]
    assert n.asked == ["192.168.1.1"]            # the runt did not make it give up on this server


def test_a_flood_of_strays_cannot_hold_resolve_past_its_timeout(net, monkeypatch):
    """Each stray used to buy another 0.05 s wait: a peer that kept sending
    wrong-id datagrams held the lookup (and the Live connect behind it) for ever."""
    monkeypatch.setattr(robot_dns, "TIMEOUT_S", 0.2)
    monkeypatch.setattr(robot_dns, "FALLBACK", ())

    stop = threading.Event()

    def flood(qid):
        while not stop.is_set():          # ends only so a failing run can be cleaned up
            yield answer(qid ^ 0xFFFF, "x.example", [a("6.6.6.6")]), ("192.168.1.1", 53)

    net({"192.168.1.1": flood}, dns="192.168.1.1")
    box = {}

    def run():
        try:
            robot_dns.resolve("x.example")
        except Exception as exc:          # noqa: BLE001 -- the outcome is the point
            box["exc"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(5)
    still_waiting = t.is_alive()
    stop.set()
    t.join(5)
    assert not still_waiting, "resolve kept waiting while strays arrived"
    assert isinstance(box.get("exc"), socket.gaierror)


def test_a_server_error_moves_on_to_the_next_server(net):
    n = net({
        "192.168.1.1": lambda qid: [(answer(qid, "x.example", [], rcode=2), ("192.168.1.1", 53))],
        "1.1.1.1": lambda qid: [(answer(qid, "x.example", [a("1.2.3.4")]), ("1.1.1.1", 53))],
    }, dns="192.168.1.1")
    assert robot_dns.resolve("x.example") == ["1.2.3.4"]
    assert n.asked == ["192.168.1.1", "1.1.1.1"]


def test_no_server_answering_is_a_gaierror(net, monkeypatch):
    monkeypatch.setattr(robot_dns, "TIMEOUT_S", 0.05)
    n = net({}, dns="")
    with pytest.raises(socket.gaierror):
        robot_dns.resolve("x.example")
    assert n.asked == list(robot_dns.FALLBACK)


def test_answers_are_cached_for_their_ttl_capped(net, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(robot_dns.time, "monotonic", lambda: clock[0])
    n = net({"192.168.1.1": lambda qid: [(answer(qid, "x.example", [a("1.2.3.4", 86400)]),
                                          ("192.168.1.1", 53))]}, dns="192.168.1.1")
    assert robot_dns.resolve("X.example.") == ["1.2.3.4"]
    expires, _ = robot_dns._cache["x.example"]
    assert expires == 1000.0 + robot_dns.MAX_TTL_S       # a day's TTL, held 5 minutes
    clock[0] += robot_dns.MAX_TTL_S - 1
    assert robot_dns.resolve("x.example") == ["1.2.3.4"] and len(n.asked) == 1
    clock[0] += 2
    robot_dns.resolve("x.example")
    assert len(n.asked) == 2


def test_a_zero_ttl_is_still_held_a_few_seconds(net, monkeypatch):
    monkeypatch.setattr(robot_dns.time, "monotonic", lambda: 50.0)
    net({"192.168.1.1": lambda qid: [(answer(qid, "x.example", [a("1.2.3.4", 0)]), ("192.168.1.1", 53))]},
        dns="192.168.1.1")
    robot_dns.resolve("x.example")
    assert robot_dns._cache["x.example"][0] == 55.0


# ---- EMMA_DNS ------------------------------------------------------------------------

def test_emma_dns_takes_ipv4_only_in_order_then_the_public_fallbacks(monkeypatch):
    monkeypatch.setenv("EMMA_DNS", " 192.168.1.1,10.0.0.1 ,junk,::1,fe80::1%wlan0,999.1.1.1,;rm -rf /,"
                                   "192.168.1.1,1.1.1.1")
    assert robot_dns.nameservers() == ["192.168.1.1", "10.0.0.1", "1.1.1.1", "8.8.8.8"]
    monkeypatch.delenv("EMMA_DNS")
    assert robot_dns.nameservers() == ["1.1.1.1", "8.8.8.8"]


# ---- getaddrinfo ----------------------------------------------------------------------

@pytest.fixture
def calls(monkeypatch):
    seen = []

    def original(host, port, family=0, type=0, proto=0, flags=0):
        seen.append((host, port, family, type, proto, flags))
        return [("fake", host)]

    def no_dns(name):
        raise AssertionError("asked DNS for %r" % name)

    monkeypatch.setattr(robot_dns, "_original", original)
    monkeypatch.setattr(robot_dns, "resolve", no_dns)
    return seen


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "fe80::1%wlan0", b"10.0.0.2", None, ""])
def test_literals_and_localhost_never_ask_dns(calls, host):
    assert robot_dns.getaddrinfo(host, 8001) == [("fake", host)]
    assert calls == [(host, 8001, 0, 0, 0, 0)]


def test_an_ipv6_lookup_is_passed_through_untouched(calls):
    robot_dns.getaddrinfo("example.com", 443, socket.AF_INET6, socket.SOCK_STREAM)
    assert calls == [("example.com", 443, socket.AF_INET6, socket.SOCK_STREAM, 0, 0)]


def test_a_name_becomes_numeric_ipv4_lookups(calls, monkeypatch):
    monkeypatch.setattr(robot_dns, "resolve", lambda name: ["1.2.3.4", "5.6.7.8"])
    out = robot_dns.getaddrinfo(b"generativelanguage.googleapis.com", 443, 0, socket.SOCK_STREAM)
    assert out == [("fake", "1.2.3.4"), ("fake", "5.6.7.8")]
    assert calls == [(ip, 443, socket.AF_INET, socket.SOCK_STREAM, 0, socket.AI_NUMERICHOST)
                     for ip in ("1.2.3.4", "5.6.7.8")]


def test_install_is_idempotent_and_never_wraps_itself(monkeypatch):
    real = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo", real)        # restored after the test
    monkeypatch.setattr(robot_dns, "_installed", False)
    robot_dns.install()
    robot_dns.install()
    assert socket.getaddrinfo is robot_dns.getaddrinfo
    assert robot_dns._original is not robot_dns.getaddrinfo
