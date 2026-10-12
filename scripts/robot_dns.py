"""Name resolution for the glibc Python that runs on the robot.

The robot's Python (python-build-standalone + Debian glibc, see
scripts/robot_python.py) resolves names through glibc, and glibc reads
/etc/resolv.conf. Android has no such file -- its resolver lives in netd and
bionic talks to it over a socket glibc knows nothing about. Measured on the
robot 2026-10-09: `socket.getaddrinfo('generativelanguage.googleapis.com', 443)`
-> gaierror -3 "Temporary failure in name resolution", while the same robot
pings 8.8.8.8 fine. Every HTTPS call the server makes (Gemini, the sales
inventory) starts with a name, so without this nothing leaves the robot.

`install()` wraps `socket.getaddrinfo`: IP literals and localhost go to the
original (no DNS involved), any other name is asked over UDP to the servers in
EMMA_DNS (the face app passes Android's own DNS servers for the active
network) and then to public resolvers. A-records only: the robot's Wi-Fi has
no IPv6 route, and an AAAA answer first would make every connect wait for a
timeout. Standard library only -- it has to run before anything is installed.
"""
from __future__ import annotations

import ipaddress
import os
import secrets
import socket
import struct
import threading
import time

FALLBACK = ("1.1.1.1", "8.8.8.8")
TIMEOUT_S = 2.0
#: An answer is reused for its TTL, capped: a Live session reconnects every
#: ~10 minutes and must follow Google moving the endpoint.
MAX_TTL_S = 300
_cache: dict[str, tuple[float, list[str]]] = {}
_lock = threading.Lock()
_original = socket.getaddrinfo
_installed = False


def nameservers() -> list[str]:
    """EMMA_DNS first (comma separated, IPv4 only), then the public fallbacks."""
    out: list[str] = []
    for part in (os.environ.get("EMMA_DNS") or "").split(","):
        part = part.strip()
        try:
            if part and ipaddress.ip_address(part).version == 4 and part not in out:
                out.append(part)
        except ValueError:
            continue
    out += [s for s in FALLBACK if s not in out]
    return out


def _query(name: str) -> bytes:
    qid = secrets.randbits(16)
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)    # RD, one question
    labels = b"".join(bytes([len(p)]) + p for p in (x.encode("idna") for x in name.rstrip(".").split(".")))
    return header + labels + b"\x00" + struct.pack(">HH", 1, 1)  # A, IN


def _skip_name(msg: bytes, i: int) -> int:
    while True:
        n = msg[i]
        if n == 0:
            return i + 1
        if n & 0xC0 == 0xC0:                                     # compression pointer
            return i + 2
        i += 1 + n


def parse(msg: bytes, qid: int) -> tuple[list[str], int]:
    """(IPv4 addresses, ttl) from a response to `qid`; CNAMEs are followed by
    the server already (RD), so the A records in the answer are the result."""
    if len(msg) < 12:
        # ValueError, not struct.error: a runt datagram is a stray to skip (the
        # same as a wrong id), not a broken answer that gives up this server
        raise ValueError("short datagram")
    rid, flags, qd, an, _ns, _ar = struct.unpack(">HHHHHH", msg[:12])
    if rid != qid or not flags & 0x8000:
        raise ValueError("not our answer")
    if flags & 0x000F:
        raise LookupError("rcode %d" % (flags & 0x000F))
    i = 12
    for _ in range(qd):
        i = _skip_name(msg, i) + 4
    addrs: list[str] = []
    ttl = MAX_TTL_S
    for _ in range(an):
        i = _skip_name(msg, i)
        rtype, _cls, rttl, rdlen = struct.unpack(">HHIH", msg[i:i + 10])
        i += 10
        if rtype == 1 and rdlen == 4:
            addrs.append(socket.inet_ntoa(msg[i:i + 4]))
            ttl = min(ttl, rttl)
        i += rdlen
    return addrs, ttl


def resolve(name: str) -> list[str]:
    key = name.lower().rstrip(".")
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return list(hit[1])
    last: Exception | None = None
    for server in nameservers():
        packet = _query(key)
        qid = struct.unpack(">H", packet[:2])[0]
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(TIMEOUT_S)
                s.sendto(packet, (server, 53))
                deadline = time.monotonic() + TIMEOUT_S
                while True:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        # strays kept arriving: each one used to buy another
                        # 0.05 s wait, so the 2 s budget never ran out
                        raise socket.timeout("no answer from %s" % server)
                    s.settimeout(max(0.05, left))
                    data, peer = s.recvfrom(4096)
                    if peer[0] != server:
                        continue                                 # not from who we asked
                    try:
                        addrs, ttl = parse(data, qid)
                    except ValueError:
                        continue                                 # a stray datagram
                    break
        except (OSError, LookupError, struct.error, IndexError) as exc:
            last = exc
            continue
        if addrs:
            with _lock:
                _cache[key] = (time.monotonic() + max(5, min(ttl, MAX_TTL_S)), addrs)
            return addrs
        last = LookupError("no A record for %s" % key)
    raise socket.gaierror(socket.EAI_NONAME, "robot_dns: %s (%s)" % (key, last))


def _literal(host) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    if host in ("localhost", "localhost.localdomain", ""):
        return True
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        return True
    except ValueError:
        return False


def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if _literal(host) or family == socket.AF_INET6:
        return _original(host, port, family, type, proto, flags)
    name = host.decode("ascii") if isinstance(host, bytes) else str(host)
    out = []
    for addr in resolve(name):
        out += _original(addr, port, socket.AF_INET, type, proto, flags | socket.AI_NUMERICHOST)
    return out


def install() -> None:
    """Idempotent. Only on a robot runtime: a PC resolves names itself."""
    global _installed
    if not _installed:
        socket.getaddrinfo = getaddrinfo
        _installed = True
