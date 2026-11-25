"""Is the internet good enough to talk to Gemini right now? -- without asking it.

The owner, 26 Oct 2026: "what should Emma do when the internet is bad? ...
build an emergency backup." The backup (app/providers/offline.py) is only as
good as the decision to use it, and the decision has two ways to be wrong:

* **Too eager.** One dropped packet is not an outage. This project already
  learned the shape with GEMINI_MODEL_FALLBACK: falling back on any error at
  all turns a five-second hiccup into a silent downgrade nobody goes back to
  check -- here it would turn a guest's real Gemini conversation into fixed
  lines because Wi-Fi flinched once. So nothing flips on a single failure:
  OFFLINE after two in a row, ONLINE after two probe successes in a row.
  Failed Gemini dials also keep their own streak that only an opened dial
  resets (DIAL_BACKOFF_S): a link can answer the probe and still refuse
  every Live dial.

* **Too slow.** The robot's measured outage shape (understand.json, audit):
  a failed dial shows "Connecting..." for up to 10 s (websockets'
  open_timeout) -- up to ~40 s when the SDK's setup read stalls -- then
  "Ready" on a robot that cannot speak, and redials every ~15 s, all day.
  A probe that knows the link is down lets the next dial go straight to the
  offline voice.

**The probe is the provider's own first steps, and nothing more**: resolve the
Live API's host the way the provider will (on the robot `scripts/robot_dns.py`
replaces `socket.getaddrinfo` process-wide, and `loop.getaddrinfo` goes
through it), then TCP + TLS to :443 with the default SSL context (what
`websockets` uses; on the robot SSL_CERT_FILE points it at certifi). No HTTP
request, no API key, no quota. Wi-Fi up but no internet -- the robot's likely
failure -- is exactly what this sees and what navigator.onLine / Android's
ConnectivityManager do not.

Background probing runs with OFFLINE_FALLBACK on, or NET_STATUS on (10 Nov: the staff pages'
lamp -- shown only, every decision below still asks OFFLINE_FALLBACK); both off, this module is
never imported by the running server. The turn log gets one
`net_state` row per *transition* (state + a reason category, never an
exception's text: a resolver message can carry hostnames and addresses that
are nobody's business in a guest log).
"""
from __future__ import annotations

import asyncio
import logging
import socket
import ssl
import time

from app.config import settings

logger = logging.getLogger("condo_voice.net_health")

#: The host every Gemini Live dial starts with (google-genai's endpoint).
HOST = "generativelanguage.googleapis.com"
PORT = 443
#: Consecutive failures that make the link OFFLINE, consecutive probe
#: successes that make it ONLINE again. Two, not one: see the module doc.
FAILS_TO_OFFLINE = 2
OKS_TO_ONLINE = 2

#: Reason categories (the only words a net_state row carries).
REASONS = ("dns", "connect", "tls", "timeout", "dial_network", "probe_ok", "dial_ok")

#: A link that answers the probe and still fails every Gemini dial (review 26
#: Oct, reproduced: a proxy or venue Wi-Fi resetting the websocket upgrade, a
#: link too slow for the handshake, a setup stall caught by CONNECT_TIMEOUT_S).
#: Every probe success reset the failure count, so the state never left ONLINE
#: and the robot looped all day: a 10-15 s deaf "Connecting...", the backup
#: call, the watcher ending it ~10 s later because the probe answered, the
#: standing line redialling the same failure. "Back" has to be proved by the
#: thing that failed: dial failures are counted on their own, only a dial that
#: OPENS resets them, and from the second in a row (never on one glitch, the
#: module rule) the next dial waits DIAL_BACKOFF_S, doubling up to
#: DIAL_BACKOFF_MAX_S -- calls in between open the backup at once, and a
#: backup call is not ended for the network until the wait is over. The cap
#: is the cost of a check: one failed dial (~10 s deaf) per ten minutes at worst.
DIAL_FAILS_TO_BACKOFF = 2
DIAL_BACKOFF_S = 60.0
DIAL_BACKOFF_MAX_S = 600.0


class NetHealth:
    """The hysteresis. Pure: fed outcomes, says the state; the probe and the
    clock live outside it so the tests drive it directly."""

    def __init__(self) -> None:
        #: "online" until proven otherwise: the switch-on machine starts the
        #: way it always did (a Gemini dial) and needs two failures to move.
        self.state = "online"
        self._fails = 0
        self._oks = 0
        self.changed_at = time.monotonic()
        self.last_reason: str | None = None
        #: Gemini dials that failed for the network in a row -- probes never
        #: touch it (see DIAL_BACKOFF_S) -- and until when the next dial waits.
        self.dial_fails = 0
        self.dial_backoff_until = 0.0
        #: The last probe, for the staff pages' lamp: when, how it went, how long the handshake took.
        self.last_probe_at: float | None = None
        self.last_probe_ok: bool | None = None
        self.last_probe_ms: int | None = None
        self.last_probe_reason: str | None = None

    def offline(self) -> bool:
        return self.state == "offline"

    def _flip(self, state: str, reason: str) -> None:
        self.state = state
        self.changed_at = time.monotonic()
        self.last_reason = reason
        logger.warning("network: %s (%s)", state.upper(), reason)
        from app import turnlog

        turnlog.record("net_state", state=state, reason=reason)

    def failure(self, reason: str) -> None:
        """A probe or a network-category dial failed."""
        reason = reason if reason in REASONS else "connect"
        self._oks = 0
        self._fails += 1
        if self.state != "offline" and self._fails >= FAILS_TO_OFFLINE:
            self._flip("offline", reason)

    def probe_ok(self) -> None:
        self._fails = 0
        self._oks += 1
        if self.state == "offline" and self._oks >= OKS_TO_ONLINE:
            self._flip("online", "probe_ok")

    def dial_ok(self) -> None:
        """A Gemini session opened: whatever failed before was not "in a row".
        It does not count toward ONLINE -- only probes do: a dial that got
        through once while the link flaps is not the link being back. It is
        the one thing that ends a dial back-off."""
        self._fails = 0
        self.dial_fails = 0
        self.dial_backoff_until = 0.0

    def dial_failure(self, now: float | None = None) -> float:
        """A Gemini dial failed for the network: one failure for the link's
        state, one for the dial streak. Returns the back-off it starts (0 =
        none: the first failure in a row is a glitch until a second agrees)."""
        now = time.monotonic() if now is None else now
        self.dial_fails += 1
        self.failure("dial_network")
        if self.dial_fails < DIAL_FAILS_TO_BACKOFF:
            return 0.0
        wait = min(DIAL_BACKOFF_MAX_S,
                   DIAL_BACKOFF_S * 2 ** (self.dial_fails - DIAL_FAILS_TO_BACKOFF))
        self.dial_backoff_until = now + wait
        from app import turnlog

        turnlog.record("dial_backoff", streak=self.dial_fails, wait_s=round(wait))
        logger.warning("network: %d Gemini dials failed in a row -- the next waits %.0f s "
                       "(calls meanwhile open the offline voice)", self.dial_fails, wait)
        return wait

    def dial_backoff_left(self, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        return max(0.0, self.dial_backoff_until - now)


#: The process's one view of the link (the robot runs one server).
health = NetHealth()
_task: asyncio.Task | None = None


def reset() -> None:
    """Tests: a fresh state and no prober (module state, like display._lock)."""
    global health, _task
    health = NetHealth()
    _task = None


def is_offline() -> bool:
    """What a dial asks first. False whenever the switch is off."""
    return bool(settings.offline_fallback) and health.offline()


def note_dial_failure(category: str) -> None:
    """A Gemini dial failed; `category` is session._provider_failure_kind's.
    Only network-category failures say anything about the link (a quota or
    auth failure is not an outage, and must not send guests to fixed lines)."""
    if category == "network":
        health.dial_failure()


def note_dial_success() -> None:
    health.dial_ok()


def dial_backoff_left() -> float:
    """Seconds the next Gemini dial still waits (DIAL_BACKOFF_S). 0 whenever
    the switch is off."""
    if not settings.offline_fallback:
        return 0.0
    return health.dial_backoff_left()


#: One TLS context for every probe (review 10 Nov: building it each time is ~5-7 ms on the event loop,
#: every NET_PROBE_S). The default context -- what `websockets` uses for the dial.
_SSL: dict = {"context": None}


def _ssl_context() -> ssl.SSLContext:
    if _SSL["context"] is None:
        _SSL["context"] = ssl.create_default_context()
    return _SSL["context"]


async def probe(host: str = HOST, port: int = PORT,
                timeout: float | None = None) -> tuple[bool, str]:
    """(ok, reason category): DNS + TCP + TLS handshake, whole thing bounded
    by NET_PROBE_TIMEOUT_S. Never raises."""
    budget = settings.net_probe_timeout_s if timeout is None else timeout
    stage = "dns"
    writer = None
    try:
        async with asyncio.timeout(max(0.1, float(budget))):
            loop = asyncio.get_running_loop()
            # loop.getaddrinfo runs socket.getaddrinfo in a thread -- on the
            # robot that is robot_dns's resolver, exactly what the dial uses.
            infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            if not infos:
                return False, "dns"
            address = infos[0][4][0]
            stage = "connect"
            context = _ssl_context()
            # The resolved address, the name for SNI and the certificate: one
            # resolution per probe, the way the dial does it.
            _reader, writer = await asyncio.open_connection(
                address, port, ssl=context, server_hostname=host,
                ssl_handshake_timeout=max(0.1, float(budget)))
            return True, "probe_ok"
    except TimeoutError:
        return False, "timeout" if stage != "dns" else "dns"
    except socket.gaierror:
        return False, "dns"
    except ssl.SSLError:
        return False, "tls"
    except OSError:
        return False, "connect"
    except Exception:                                # pragma: no cover - belt
        logger.debug("network probe failed oddly", exc_info=True)
        return False, "connect"
    finally:
        if writer is not None:
            writer.close()
            try:
                async with asyncio.timeout(1.0):
                    await writer.wait_closed()
            except Exception:
                pass


async def probe_now() -> bool:
    """One probe, recorded in the hysteresis; True = the link answered. For
    a dial that just failed with an unclassified error: "is it the network?"
    is asked of the network, not guessed from the exception's wording."""
    started = time.monotonic()
    ok, reason = await probe()
    health.last_probe_at = time.monotonic()
    health.last_probe_ok = ok
    health.last_probe_ms = round((health.last_probe_at - started) * 1000)
    health.last_probe_reason = None if ok else reason
    if ok:
        health.probe_ok()
    else:
        health.failure(reason)
    return ok


async def _run() -> None:
    every = float(settings.net_probe_s)
    while True:
        try:
            await probe_now()
        except asyncio.CancelledError:
            raise
        except Exception:                            # pragma: no cover - belt
            logger.debug("network probe loop", exc_info=True)
        await asyncio.sleep(max(1.0, every))


def watched() -> bool:
    """Something probes the link: OFFLINE_FALLBACK (it acts on it) or NET_STATUS (shown only)."""
    return bool(settings.offline_fallback or settings.net_status) and settings.net_probe_s > 0


def start() -> asyncio.Task | None:
    """The background prober, from the server's startup hook. Only with OFFLINE_FALLBACK or
    NET_STATUS on and NET_PROBE_S > 0; idempotent."""
    global _task
    if not watched():
        return None
    if _task is not None and not _task.done():
        return _task
    _task = asyncio.get_running_loop().create_task(_run(), name="net_health_probe")
    logger.info("%s: probing %s:%d every %.0f s",
                "offline fallback on" if settings.offline_fallback else "network lamp on (shown only)",
                HOST, PORT, settings.net_probe_s)
    return _task


def stop() -> None:
    global _task
    if _task is not None and not _task.done():
        _task.cancel()
    _task = None


#: A handshake slower than this reads "ช้า" on the lamp: DNS + TCP + TLS to Google is ~0.1-0.3 s
#: on a healthy link; past a second, a Live session's audio is late too. A guess to tune on site.
SLOW_MS = 1000


def staff_view() -> dict | None:
    """The staff pages' lamp: None when nothing probes. `level`: ok / slow / flaky (the last probe
    failed, not yet two in a row) / down (OFFLINE, the hysteresis's word) / unknown (no probe yet).
    Categories and numbers only, like every other view of it."""
    if not watched():
        return None
    h = health
    if h.last_probe_at is None:
        level = "unknown"
    elif h.offline():
        level = "down"
    elif h.last_probe_ok is False:
        level = "flaky"
    elif (h.last_probe_ms or 0) >= SLOW_MS:
        level = "slow"
    else:
        level = "ok"
    return {"level": level, "ms": h.last_probe_ms,
            "ago_s": None if h.last_probe_at is None else round(time.monotonic() - h.last_probe_at),
            "reason": h.last_reason if level == "down" else (h.last_probe_reason if level == "flaky" else None)}


def snapshot() -> dict:
    """For /health or the face: categories and seconds only."""
    return {"enabled": bool(settings.offline_fallback), "state": health.state,
            "since_s": round(time.monotonic() - health.changed_at, 1),
            "reason": health.last_reason,
            "dial_fails": health.dial_fails,
            "dial_backoff_s": round(health.dial_backoff_left())}
