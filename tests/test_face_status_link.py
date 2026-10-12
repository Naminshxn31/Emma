"""The robot face page's status link, `/ws/robot-face` (2026-10-09).

Review 2's high finding #0 (and #3): the face page's bridge talked to the
server only through the call's `/ws`, so a walk started between calls — a
panel tap on the face page, or a PC `robot_python run` the app's poller sees
— left `robot_face_link.nav_activity()` at "unknown", and the desk STOP could
not reach the app. The page now holds a second socket from load that REPORTS
(the same frames the call carries) and carries back only STOPS.

Server tests drive the real endpoint (TestClient) for the gate, and the real
`serve_status_link` loop with a fake socket for the behaviour, so the order
of frames is deterministic. Page tests run the page's real bridge script
through tests/face_nav_model.cjs. The Java is NavRules (compiled and run) plus
source checks — nothing here has run on the robot.
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from app import robot_chassis, robot_face_link, robot_nav
from app.config import settings
from tests.test_emma_robot_body_nav_app import NAV, SRC, _javac, code_only, method, read
from tests.test_face_body_nav_page import PAGE, _bridge_block, _node, _scenario, _without_comments

RULES = SRC / "NavRules.java"
ROBOT_UA = "Mozilla/5.0 (Linux; Android 15) wv EmmaRobot/0.1 EmmaBridge/1"
DESK_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
TOKEN = "t-link"
RUN = "c" * 32
RUNNING_PC = {"type": "nav_status", "state": "running", "run_id": None, "origin": "pc",
              "reason": "pc_run", "place": "big_room"}
RUNNING_TAP = {"type": "nav_status", "state": "running", "run_id": RUN, "origin": "tap",
               "place": "big_room"}
IDLE = {"type": "nav_status", "state": "idle", "run_id": None}
#: What an APK that states its poll sends (NavLauncher.status, 9 Oct review):
#: only an explicit `poll_stale: false` lets a status link's idle count.
LINK_IDLE = dict(IDLE, poll_stale=False)


def run(coro):
    return asyncio.run(coro)


# ---- fakes -------------------------------------------------------------------


class FakeSocket:
    """The server end of /ws/robot-face as `serve_status_link` sees it.
    `feed` is the page sending a frame; `auto_ack` makes it answer every
    command the way the real page relays the app's answer, over this socket."""

    def __init__(self, ua: str = ROBOT_UA, auto_ack: bool = True):
        self.headers = {"user-agent": ua}
        self.sent: list[dict] = []
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.closed = False
        self.auto_ack = auto_ack

    async def accept(self):
        pass

    async def send_text(self, text):
        frame = json.loads(text)
        self.sent.append(frame)
        if self.auto_ack and frame.get("type", "").endswith("_command"):
            kind = frame["type"].split("_")[0]
            self.feed({"type": "%s_ack" % kind, "command_id": frame["command_id"],
                       "action": frame["action"], "accepted": True, "reason": None})

    async def receive(self):
        return await self.inbox.get()

    async def close(self):
        self.closed = True

    def feed(self, frame, raw: str | None = None):
        self.inbox.put_nowait({"type": "websocket.receive",
                               "text": raw if raw is not None else json.dumps(frame)})

    def feed_bytes(self, data: bytes):
        self.inbox.put_nowait({"type": "websocket.receive", "bytes": data})

    def hang_up(self):
        self.inbox.put_nowait({"type": "websocket.disconnect"})

    def commands(self):
        return [(f["type"], f["action"]) for f in self.sent if f["type"].endswith("_command")]


class CallPage:
    """The call session's /ws (a VoiceSession stand-in, like the other face
    tests use): acks every command unless told to fail or stay quiet."""

    def __init__(self, mode: str = "ack"):
        self.mode = mode
        self.sent: list[dict] = []

    async def send_text(self, text):
        if self.mode == "dead":
            raise RuntimeError("socket closed")
        frame = json.loads(text)
        self.sent.append(frame)
        if self.mode == "ack":
            kind = frame["type"].split("_")[0]
            asyncio.get_running_loop().call_soon(
                robot_face_link.receive, self.owner,
                {"type": "%s_ack" % kind, "command_id": frame["command_id"],
                 "action": frame["action"], "accepted": True})


class CallSession:
    def __init__(self, mode: str = "ack"):
        self.ws = CallPage(mode)
        self.ws.owner = self


async def settle(rounds: int = 10):
    for _ in range(rounds):
        await asyncio.sleep(0)


async def open_link(sock: FakeSocket):
    """Run the real endpoint loop on `sock`; return (task, the StatusLink)."""
    task = asyncio.ensure_future(robot_face_link.serve_status_link(sock))
    await settle()
    links = [f for f in robot_face_link._CLAIMED.values()
             if robot_face_link.is_status_link(f) and f.ws is sock]
    return task, (links[0] if links else None)


async def close_link(task, sock: FakeSocket):
    sock.hang_up()
    await asyncio.wait_for(task, 2)


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(robot_face_link, "_clock", lambda: now[0])
    return now


# ==================== the gate (the real endpoint) ====================


def test_every_websocket_route_demands_the_token(monkeypatch):
    """Closing one entrance is never enough (CLAUDE.md): every socket the app
    serves, the new one included, refuses a missing or wrong WS_TOKEN — found
    from the route table, so a fifth socket cannot be added without it."""
    from fastapi.testclient import TestClient
    from starlette.routing import WebSocketRoute

    from app.main import app

    monkeypatch.setattr(settings, "ws_token", "secret123")
    paths = [r.path for r in app.routes if isinstance(r, WebSocketRoute)]
    assert "/ws/robot-face" in paths
    client = TestClient(app)
    for path in paths:
        for suffix in ("", "?token=wrong"):
            with client.websocket_connect(path + suffix, headers={"user-agent": ROBOT_UA}) as ws:
                assert ws.receive_json()["code"] == "unauthorized", path + suffix
    assert robot_face_link.live_faces() == []


def test_the_link_is_only_for_the_robots_app(monkeypatch):
    """Right token, but not the EmmaRobot/ user agent: refused with its own
    code (the page stops for good on it) and never claimed as a face."""
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(settings, "ws_token", TOKEN)
    client = TestClient(app)
    with client.websocket_connect("/ws/robot-face?token=" + TOKEN,
                                  headers={"user-agent": DESK_UA}) as ws:
        assert ws.receive_json() == {"type": "error", "code": "not_robot_face"}
    assert robot_face_link.live_faces() == []
    assert robot_face_link.nav_activity() == "unknown"


def _wait_for(predicate, seconds: float = 3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_the_robot_link_reports_a_pc_run_through_the_real_endpoint(monkeypatch):
    """End to end over the real socket: ready first (the page's backoff
    resets on it, not on onopen), then a PC run reported between calls is
    "active" for every fence, and the closed link's run stays active."""
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(settings, "ws_token", TOKEN)
    client = TestClient(app)
    with client.websocket_connect("/ws/robot-face?token=" + TOKEN,
                                  headers={"user-agent": ROBOT_UA}) as ws:
        assert ws.receive_json() == {"type": "face_link_ready"}
        ws.send_text(json.dumps(RUNNING_PC))
        assert _wait_for(lambda: robot_face_link.nav_activity() == "active")
        face = robot_face_link.walking_face()
        assert robot_face_link.is_status_link(face)
    assert _wait_for(lambda: robot_face_link.live_faces() == [])
    assert robot_face_link.nav_activity() == "active", "a closed link's walk is still a walk"


# ==================== what the link accepts ====================


def test_only_report_frames_from_a_robot_page_count():
    """Not a call: nav_unlock, robot_ready, audio, oversize, malformed and
    binary frames are dropped before anything reads them."""

    async def scenario():
        sock = FakeSocket()
        task, link = await open_link(sock)
        assert link is not None and sock.sent[0] == {"type": "face_link_ready"}
        sock.feed({"type": "nav_unlock", "pin": "1234"})
        sock.feed({"type": "robot_ready", "token": "x", "places": []})
        sock.feed({"type": "mic_level", "level": 0.2})
        sock.feed(None, raw="{not json")
        sock.feed_bytes(b"\x00\x01")
        sock.feed(dict(RUNNING_PC, detail="x" * 9000))          # > MAX_INBOUND_BYTES
        await settle()
        before = robot_face_link.nav_activity()
        sock.feed(RUNNING_PC)
        await settle()
        after = robot_face_link.nav_activity()
        await close_link(task, sock)
        return before, after, link

    before, after, link = run(scenario())
    assert before == "unknown" and after == "active"
    assert link.seen_at is not None


def test_with_a_face_token_the_link_counts_only_after_the_hello(monkeypatch):
    """ROBOT_FACE_TOKEN set: the user agent is a claim; frames count only
    after a matching face_hello — the call session's rule, same code."""
    monkeypatch.setattr(settings, "robot_face_token", "s3cret")

    async def scenario():
        sock = FakeSocket()
        task, link = await open_link(sock)
        seen = []
        sock.feed(RUNNING_PC)
        await settle()
        seen.append(robot_face_link.nav_activity())
        sock.feed({"type": "face_hello", "token": "wrong"})
        sock.feed(RUNNING_PC)
        await settle()
        seen.append(robot_face_link.nav_activity())
        # an unproven link is not a face a stop can be sent to either
        seen.append((await robot_face_link.request(link, "nav", {"action": "stop"}, 0.2))["reason"])
        sock.feed({"type": "face_hello", "token": "s3cret"})
        sock.feed(RUNNING_PC)
        await settle()
        seen.append(robot_face_link.nav_activity())
        await close_link(task, sock)
        return seen, link

    seen, link = run(scenario())
    assert seen == ["unknown", "unknown", "not_robot_face", "active"]


# ==================== the link never starts motion ====================


def test_the_link_never_carries_anything_but_a_stop():
    """A go or a gesture addressed to the status link is refused before it is
    registered or sent; a stop goes and its ack comes back over the link."""

    async def scenario():
        sock = FakeSocket()
        task, link = await open_link(sock)
        sock.feed(LINK_IDLE)
        await settle()
        refused = [
            await robot_face_link.request(link, "nav", {"action": "go", "place": "big_room"}, 0.2),
            await robot_face_link.request(link, "body", {"action": "nod"}, 0.2),
            await robot_face_link.request(link, "body", {"action": "handshake"}, 0.2),
        ]
        stops = [await robot_face_link.request(link, "nav", {"action": "stop"}, 1.0),
                 await robot_face_link.request(link, "body", {"action": "stop"}, 1.0)]
        await close_link(task, sock)
        return refused, stops, sock

    refused, stops, sock = run(scenario())
    assert [r["reason"] for r in refused] == ["not_robot_face"] * 3
    assert sock.commands() == [("nav_command", "stop"), ("body_command", "stop")]
    assert [s["accepted"] for s in stops] == [True, True]
    assert robot_face_link._PENDING == {} and robot_face_link._SLOT == {}


def test_a_report_on_the_link_never_announces_or_issues_a_run(monkeypatch):
    """The announce gate (issued_run) stays the call's: the link never sends
    a go, so a terminal status on it — even origin voice — is logged, never
    spoken into anyone's call."""
    said = []

    async def fake_announce(*args, **kwargs):
        said.append(args)

    monkeypatch.setattr(robot_nav, "_announce", fake_announce)

    async def scenario():
        sock = FakeSocket()
        task, link = await open_link(sock)
        sock.feed(dict(RUNNING_TAP, origin="voice"))
        sock.feed(dict(RUNNING_TAP, origin="voice", state="arrived"))
        await settle(20)
        issued = robot_face_link.issued_run(link, RUN)
        await close_link(task, sock)
        return issued

    assert run(scenario()) is False
    assert said == []


# ==================== liveness ====================


def test_an_idle_link_that_goes_quiet_reads_unknown(clock, monkeypatch):
    """Three missed beats: the page stopped talking, which is not a quiet
    robot. Idle -> unknown (and outranks another face's idle); an active
    frame keeps its own active -> stale clock, never unknown."""

    async def scenario():
        sock = FakeSocket()
        task, link = await open_link(sock)
        out = {}
        sock.feed(LINK_IDLE)
        await settle()
        out["fresh"] = robot_face_link.nav_activity()
        clock[0] += robot_face_link.LINK_SILENT_S - 1
        out["two_beats"] = robot_face_link.nav_activity()
        clock[0] += 2
        out["silent"] = robot_face_link.nav_activity()
        out["silent_self"] = robot_face_link.nav_activity(link)
        # a call face that says idle does not hide the silent link
        call = CallSession()
        robot_face_link.claim(call)
        robot_face_link.receive(call, IDLE)
        out["with_idle_call"] = robot_face_link.nav_activity()
        monkeypatch.setattr(settings, "robot_nav_routes_enabled", True)
        try:
            robot_chassis.run_fence()
            out["fence"] = None
        except robot_chassis.Fenced as exc:
            out["fence"] = exc.code
        out["plan"] = robot_nav.stop_plan()[0]
        monkeypatch.setattr(settings, "robot_nav_routes_enabled", False)
        sock.feed(LINK_IDLE)                             # the next beat
        await settle()
        out["beat"] = robot_face_link.nav_activity()
        sock.feed(RUNNING_TAP)
        await settle()
        clock[0] += robot_face_link.LINK_SILENT_S + 1
        out["walk_silent"] = robot_face_link.nav_activity()
        await close_link(task, sock)
        return out

    out = run(scenario())
    assert out["fresh"] == "idle" and out["two_beats"] == "idle"
    assert out["silent"] == "unknown" and out["silent_self"] == "unknown"
    assert out["with_idle_call"] == "unknown"
    assert out["fence"] == "run_unknown"
    assert out["plan"] == "unknown"
    assert out["beat"] == "idle"
    assert out["walk_silent"] == "stale"


def test_a_stale_app_poll_is_not_idle(clock):
    """The app's run.pid poll went stale (su wedged, paused): its idle is "no
    run of mine". Either socket carries the flag; nav_caps afterwards (run
    null) does not wash it out; a fresh status does."""
    face = CallSession()
    robot_face_link.claim(face)
    robot_face_link.receive(face, dict(IDLE, poll_stale=True))
    assert robot_face_link.nav_activity() == "unknown"
    assert robot_face_link.nav_activity(face) == "unknown"
    robot_face_link.receive(face, {"type": "nav_caps", "enabled": True, "installed": True,
                                   "places": [], "run": None})
    assert robot_face_link.nav_activity() == "unknown"
    assert robot_face_link.leg_runner_possible() is True
    robot_face_link.receive(face, IDLE)
    assert robot_face_link.nav_activity() == "idle"
    # a walk is a walk whatever the poll says
    robot_face_link.receive(face, dict(RUNNING_TAP, poll_stale=True))
    assert robot_face_link.nav_activity() == "active"


def test_closing_the_link_keeps_a_walk_and_drops_an_idle(clock):
    async def scenario(frame):
        sock = FakeSocket()
        task, link = await open_link(sock)
        sock.feed(frame)
        await settle()
        await close_link(task, sock)
        return link

    run(scenario(LINK_IDLE))
    assert robot_face_link.nav_activity() == "unknown"
    assert robot_face_link.live_faces() == []
    run(scenario(RUNNING_TAP))
    assert robot_face_link.nav_activity() == "active"
    assert robot_face_link.walking_face() is None, "nobody left to send a stop to"
    clock[0] += settings.robot_nav_status_stale_s + 1
    assert robot_face_link.nav_activity() == "stale"


# ==================== stops reach the walk ====================


def _http(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    calls = []

    async def board(method, path, payload=None):
        calls.append((method, path))
        return None

    monkeypatch.setattr(settings, "ws_token", TOKEN)
    monkeypatch.setattr(settings, "robot_enabled", True)
    monkeypatch.setattr(settings, "robot_chassis_url", "http://chassis.test")
    monkeypatch.setattr(robot_chassis, "request", board)
    robot_chassis.reset_state()
    return TestClient(app), calls


class LinkPage:
    """A status link's socket in the HTTP request's own loop: acks every
    stop like the page does (no serve loop — the TestClient runs each
    request on its own event loop)."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, text):
        frame = json.loads(text)
        self.sent.append(frame)
        kind = frame["type"].split("_")[0]
        asyncio.get_running_loop().call_soon(
            robot_face_link.receive, self.link,
            {"type": "%s_ack" % kind, "command_id": frame["command_id"],
             "action": frame["action"], "accepted": True})


def _claimed_link(*frames):
    page = LinkPage()
    link = robot_face_link.StatusLink(page)
    page.link = link
    robot_face_link.claim(link)
    for frame in frames:
        robot_face_link.receive(link, frame)
    return link, page


@pytest.mark.parametrize("frame", [RUNNING_TAP, RUNNING_PC], ids=["panel_tap", "pc_run"])
def test_the_desk_stop_reaches_a_walk_started_between_calls(monkeypatch, frame):
    """The finding itself: no call open, the walk reported only by the link.
    /robot/command stop goes through the link as the run's own stop (nav +
    body) — no board DELETE — and says requested, not stopped."""
    client, board = _http(monkeypatch)
    link, page = _claimed_link(frame)
    out = client.post("/robot/command", json={"token": TOKEN, "action": "stop"}).json()
    assert out["ok"] is True and out["did"] == "stop_requested"
    assert sorted((f["type"], f["action"]) for f in page.sent) == [
        ("body_command", "stop"), ("nav_command", "stop")]
    assert [c for c in board if c[0] == "DELETE"] == []


def test_routes_off_a_pc_run_on_the_link_closes_the_fences(monkeypatch):
    """Routes off (this robot's .env): before the link a PC run between calls
    was invisible — joystick, nudge and the serial arm were all open over a
    walking robot. Reported by the link it is "active", whatever the switch."""
    from app import robot_arm

    assert settings.robot_nav_routes_enabled is False
    _claimed_link(RUNNING_PC)
    assert robot_face_link.nav_activity() == "active"
    with pytest.raises(robot_chassis.Fenced) as exc:
        robot_chassis.run_fence()
    assert exc.value.code == "run_active"
    assert robot_arm.app_owns_port() == "robot_walking"


def test_a_stop_falls_back_to_the_pages_other_socket(monkeypatch):
    """The page holds two sockets. One that is dead, refuses or stays quiet
    is not the end of a stop. Both hear it, whichever acks: the stop goes on
    every socket at once (9 Oct review — one after another made a quiet
    socket cost a whole ack timeout, and let the wrong page's ack end it);
    the app's second stop only renews its 60 s latch."""
    monkeypatch.setattr(settings, "robot_nav_ack_timeout_s", 0.1)
    monkeypatch.setattr(settings, "robot_body_ack_timeout_s", 0.1)

    async def scenario(call_mode):
        robot_face_link.reset_state()
        call = CallSession(call_mode)
        robot_face_link.claim(call)
        robot_face_link.receive(call, RUNNING_TAP)
        sock = FakeSocket()
        task, link = await open_link(sock)
        sock.feed(RUNNING_TAP)
        await settle()
        out = await robot_nav.stop(call)
        await close_link(task, sock)
        return out, call.ws.sent, sock.commands()

    for mode in ("dead", "quiet"):
        out, on_call, on_link = run(scenario(mode))
        assert out["ok"] is True and out["nav_stop"]["accepted"] is True, mode
        assert out["body_stop"]["accepted"] is True, mode
        assert sorted(on_link) == [("body_command", "stop"), ("nav_command", "stop")], mode
    out, on_call, on_link = run(scenario("ack"))
    assert out["ok"] is True
    assert sorted((f["type"], f["action"]) for f in on_call) == [
        ("body_command", "stop"), ("nav_command", "stop")]
    assert sorted(on_link) == [("body_command", "stop"), ("nav_command", "stop")]


class QuietPage:
    """A socket that takes the frame and never answers (a frozen page, a
    half-open TCP connection)."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


class StuckPage(QuietPage):
    """A socket whose send never returns (flow control: the call's socket
    also streams the reply's audio). The ack timeout starts only after it."""

    async def send_text(self, text):
        self.sent.append(json.loads(text))
        await asyncio.sleep(3600)


def _face(page, *, link=False, frame=None):
    face = robot_face_link.StatusLink(page) if link else CallSession()
    if not link:
        face.ws = page
    page.owner = page.link = face
    robot_face_link.claim(face)
    if frame is not None:
        robot_face_link.receive(face, frame)
    return face


def test_a_quiet_socket_does_not_hold_the_stop_back_by_its_timeout(monkeypatch):
    """Three quiet faces ahead of the live one (half-open sockets after a
    Wi-Fi blip, imposter pages): one after another, each cost a whole ack
    timeout — 1.5 s here at 0.5 s, 9 s at the real 3 s, past a voice tool's
    15 s ceiling with five. All at once, the live one answers at once."""
    monkeypatch.setattr(settings, "robot_nav_ack_timeout_s", 0.5)
    monkeypatch.setattr(settings, "robot_body_ack_timeout_s", 0.5)

    async def scenario():
        live = CallSession("ack")
        robot_face_link.claim(live)
        quiet = [_face(QuietPage(), frame=RUNNING_TAP) for _ in range(3)]
        started = time.monotonic()
        out = await robot_nav.stop(quiet[0])
        return out, time.monotonic() - started, live, quiet

    out, took, live, quiet = run(scenario())
    assert out["ok"] is True and out["body_stop"]["accepted"] is True
    assert took < 0.4, "a stop waited %.2f s behind sockets that never answer" % took
    assert sorted((f["type"], f["action"]) for f in live.ws.sent) == [
        ("body_command", "stop"), ("nav_command", "stop")]
    assert all(len(face.ws.sent) == 2 for face in quiet), "the quiet ones still got it"


def test_another_pages_ack_does_not_keep_the_stop_from_the_walking_robot(monkeypatch):
    """The /arm stop asks `current_face()` — the newest claim. With the
    status link the other ZC-3588A (same APK) is a face all day, and after a
    reconnect it is the newest: its app accepted the body stop and that was
    the answer — the robot that was walking never heard it."""
    from app import robot_arm

    async def scenario():
        real = _face(LinkPage(), link=True, frame=RUNNING_TAP)
        other = _face(LinkPage(), link=True)            # claimed later: current_face()
        assert robot_face_link.current_face() is other
        answer = await robot_arm._finish_body_stop(robot_arm._start_body_stop())
        return answer, real, other

    answer, real, other = run(scenario())
    assert answer == {"accepted": True, "reason": None}
    assert [f["type"] for f in real.ws.sent] == ["body_command"], "the walking robot must get the stop"
    assert [f["type"] for f in other.ws.sent] == ["body_command"]


def test_a_socket_stuck_in_send_does_not_hold_the_stop(monkeypatch):
    """A send that never returns had no timeout at all (the ack timeout
    starts after it), so the page's other socket was never tried and the
    desk STOP hung. Now the link carries it, and the stuck send is left to
    finish on its own — never cancelled."""
    monkeypatch.setattr(settings, "robot_nav_ack_timeout_s", 0.2)
    monkeypatch.setattr(settings, "robot_body_ack_timeout_s", 0.2)

    async def scenario():
        link = _face(LinkPage(), link=True, frame=RUNNING_TAP)
        call = _face(StuckPage())
        out = await asyncio.wait_for(robot_nav.stop(call), 3)
        kept = len(robot_face_link._TASKS)
        # nothing answers at all: the wait ends at timeout + grace, as "timeout"
        robot_face_link.reset_state()
        stuck = _face(StuckPage(), frame=RUNNING_TAP)
        started = time.monotonic()
        alone = await asyncio.wait_for(robot_face_link.request_stop(stuck, "nav", 0.2), 3)
        return out, link, kept, alone, time.monotonic() - started

    out, link, kept, alone, took = run(scenario())
    assert out["ok"] is True and out["nav_stop"]["accepted"] is True
    assert sorted((f["type"], f["action"]) for f in link.ws.sent) == [
        ("body_command", "stop"), ("nav_command", "stop")]
    assert kept >= 2, "the stuck sends are kept running, not dropped"
    assert alone["accepted"] is False and alone["reason"] == "timeout" and alone["via"] is None
    assert took < 0.2 + robot_face_link.STOP_SEND_GRACE_S + 0.5


def test_a_link_that_cannot_take_the_stop_falls_back_to_the_call(monkeypatch):
    """The other direction: the walk was reported by the link (walking_face),
    the link's socket is dead, the call is live."""
    monkeypatch.setattr(settings, "robot_nav_ack_timeout_s", 0.1)
    monkeypatch.setattr(settings, "robot_body_ack_timeout_s", 0.1)

    class DeadPage:
        async def send_text(self, text):
            raise RuntimeError("gone")

    async def scenario():
        dead = robot_face_link.StatusLink(DeadPage())
        robot_face_link.claim(dead)
        robot_face_link.receive(dead, RUNNING_PC)
        call = CallSession("ack")
        robot_face_link.claim(call)
        assert robot_face_link.walking_face() is dead
        plan, face = robot_nav.stop_plan()
        assert plan == "face" and face is dead
        out = await robot_nav.stop(face)
        return out, call

    out, call = run(scenario())
    assert out["ok"] is True
    assert sorted((f["type"], f["action"]) for f in call.ws.sent) == [
        ("body_command", "stop"), ("nav_command", "stop")]


def test_with_no_socket_left_the_stop_is_not_called_sent(monkeypatch):
    monkeypatch.setattr(settings, "robot_nav_ack_timeout_s", 0.05)
    monkeypatch.setattr(settings, "robot_body_ack_timeout_s", 0.05)
    out = run(robot_nav.stop(None))
    assert out["ok"] is False and out["hardware"] == "failed"
    assert out["nav_stop"]["reason"] == "not_robot_face"
    assert "ห้ามบอกว่าหยุดแล้ว" in out["instruction"]


def test_voice_stop_from_a_desk_tab_reaches_a_walk_the_link_reported(monkeypatch):
    """`stop_moving` spoken at a desk tab (no robot face session of its own)
    during a walk only the link reported: the face path, not the board."""
    from app.tools import load_tools, registry, robot_link

    load_tools()
    robot_link.reset_state()
    monkeypatch.setattr(settings, "turn_log", False)
    monkeypatch.setattr(settings, "robot_enabled", True)
    board = []

    async def board_send(action, **args):
        board.append(action)
        return "ok"

    monkeypatch.setattr(robot_link, "send", board_send)

    async def scenario():
        link, page = _claimed_link(RUNNING_TAP)
        assert registry.robot_face_session.get() is None
        return await registry.dispatch("stop_moving", {}), page

    out, page = run(scenario())
    assert out["ok"] is True and out["hardware"] == "requested"
    assert sorted((f["type"], f["action"]) for f in page.sent) == [
        ("body_command", "stop"), ("nav_command", "stop")]
    assert board == []
    robot_link.reset_state()


# ==================== the page ====================


def _link_block() -> str:
    code = _bridge_block()
    return code.split("// ---- the status link (/ws/robot-face)", 1)[1].split("// ---- the panel ----", 1)[0]


def test_the_page_opens_the_link_only_in_the_robots_app():
    run_ = _scenario("link_not_on_robot")
    for name in ("desk", "old_apk", "ua_only", "kiosk_chrome"):
        assert run_[name] == {"sockets": 0, "prompts": []}, name
    code = _without_comments(_link_block())
    open_link_fn = code.split("function openLink() {", 1)[1]
    assert open_link_fn.index("if (!ON_ROBOT || linkStopped || link") < open_link_fn.index("new WebSocket(")


def test_the_link_says_nothing_before_ready_then_hello_first_and_beats_while_idle():
    run_ = _scenario("link_open")
    # the page's own `token` parameter, the same one /ws uses -- no new place for it
    assert run_["opened"] == {"count": 1, "url": "wss://robot.test:8001/ws/robot-face?token=tok123"}
    assert run_["beforeReady"] == 0, "onopen fires for a refusal too: wait for the server's ready"
    assert run_["snapshot"] == ["face_hello", "body_caps", "nav_caps", "nav_status"]
    assert run_["hello"] == {"type": "face_hello", "token": "s3cret"}
    assert run_["beats"] == 3                       # 31 s idle, one nav_status every 10 s
    assert run_["goPrompts"] == 0
    code = _without_comments(_link_block())
    assert "+ (token ? '?token=' + encodeURIComponent(token) : '')" in code
    assert "query.get('face_token')" not in code


def test_the_page_relays_only_stops_from_the_link():
    run_ = _scenario("link_stop_only")
    assert run_["ignored"] == {"prompts": [], "sent": []}
    assert run_["stopPrompts"] == ["emma-nav:stop", "emma-body:stop"]
    assert [(a["type"], a["action"], a["accepted"]) for a in run_["acks"]] == [
        ("nav_ack", "stop", True), ("body_ack", "stop", True)]


def test_a_refused_link_stops_for_good_and_a_dropped_one_backs_off():
    run_ = _scenario("link_backoff")
    for code in ("unauthorized", "not_robot_face"):
        assert run_[code] == {"sockets": 1, "sent": 0, "warned": 1}, code
    assert run_["redial"]["gaps"] == [2000, 4000, 8000, 16000, 30000, 30000, 30000]
    assert run_["redial"]["afterReady"] == 1 and run_["redial"]["afterReadyGap"] == 2000
    # nothing for customers: the link block never writes to the page
    code = _without_comments(_link_block())
    for forbidden in ("textContent", "innerHTML", "status(", "show(", "alert("):
        assert forbidden not in code, forbidden


def test_the_call_and_the_link_both_hear_every_frame_and_the_link_outlives_the_call():
    run_ = _scenario("link_and_call")
    assert run_["callTypes"] == ["face_hello", "body_caps", "nav_caps", "nav_status"]
    assert run_["callNow"] == run_["callAfterDetach"], "nothing for a call that ended"
    assert run_["pcFrame"]["state"] == "running" and run_["pcFrame"]["origin"] == "pc"
    assert run_["walkBeats"] >= 3                   # the 5 s beat of a walk, between calls
    assert run_["laterCaps"] >= 1                   # the 30 s caps clock survives the call


def test_the_apps_stale_poll_flag_reaches_the_server_at_once():
    run_ = _scenario("link_poll_stale")
    assert len(run_["stale"]) == 1 and run_["stale"][0]["poll_stale"] is True
    # false is passed on too: on the link only an explicit false lets an idle count
    assert len(run_["fresh"]) == 1 and run_["fresh"][0]["poll_stale"] is False
    assert run_["firstHasKey"] is True


def test_an_apk_that_does_not_state_its_poll_gets_no_link():
    """An APK from before poll_stale has EmmaBridge/1, so ON_ROBOT let it
    open the link — and its bare idle (sent whether or not its poll could
    see a PC run) counted between calls, where before the link the server
    said "cannot tell". No link for it at all, for good; an app that just
    has not answered yet is asked again on the backoff."""
    run_ = _scenario("link_old_apk")
    assert run_["old"]["sockets"] == 0
    assert run_["old"]["statusAsks"] == 1, "asked once, then stopped -- no polling loop"
    assert run_["old"]["warned"] == 1
    assert run_["late"]["before"] == 0 and run_["late"]["after"] == 1
    code = _without_comments(_link_block())
    open_link_fn = code.split("function openLink() {", 1)[1]
    assert open_link_fn.index("appStatesPoll()") < open_link_fn.index("new WebSocket(")


def test_an_idle_link_counts_only_when_the_app_says_its_poll_is_fresh():
    """The server's half of the same rule: a status link whose latest status
    does not SAY `poll_stale: false` cannot vouch for idle — the wheel fence
    stays run_unknown with routes on, the desk STOP stays "unconfirmed". A
    call keeps its old reading (an old APK must not change what a call sees)."""
    link = _face(QuietPage(), link=True)
    link.seen_at = robot_face_link._now()
    robot_face_link.receive(link, IDLE)
    assert robot_face_link.nav_activity() == "unknown"
    assert robot_face_link.nav_activity(link) == "unknown"
    robot_face_link.receive(link, LINK_IDLE)
    assert robot_face_link.nav_activity() == "idle"
    # caps alone (no status yet) vouch for nothing either
    robot_face_link.reset_state()
    caps_only = _face(QuietPage(), link=True, frame={"type": "nav_caps", "enabled": True,
                                                     "places": [], "run": None})
    assert robot_face_link.is_status_link(caps_only)
    assert robot_face_link.nav_activity() == "unknown"
    robot_face_link.reset_state()
    call = CallSession()
    robot_face_link.claim(call)
    robot_face_link.receive(call, IDLE)
    assert robot_face_link.nav_activity() == "idle"


def test_every_plain_script_block_of_the_face_page_parses():
    node = _node()
    html = PAGE.read_text(encoding="utf-8")
    blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert blocks
    for i, block in enumerate(blocks):
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
            handle.write(block)
            path = handle.name
        try:
            done = subprocess.run([node, "--check", path], capture_output=True,
                                  encoding="utf-8", timeout=30)
        finally:
            Path(path).unlink(missing_ok=True)
        assert done.returncode == 0, f"voice-preview.html script #{i}:\n{done.stderr}"


# ==================== the app (Java) ====================


LINK_RULES_HARNESS = r"""
package com.emma.robot.control;

public final class LinkHarness {
    public static void main(String[] a) {
        System.out.println("stale.idle_stale=" + NavRules.statusPollStale(false, false));
        System.out.println("stale.idle_fresh=" + NavRules.statusPollStale(false, true));
        System.out.println("stale.own_stale=" + NavRules.statusPollStale(true, false));
        System.out.println("stale.own_fresh=" + NavRules.statusPollStale(true, true));
        System.out.println("vouch.off=" + NavRules.operatorVouchesArms(false));
        System.out.println("vouch.on=" + NavRules.operatorVouchesArms(true));
    }
}
"""


def test_compiled_link_rules(tmp_path):
    tools = _javac()
    if tools is None:
        pytest.skip("no JDK found: NavRules.statusPollStale / operatorVouchesArms were NOT compiled or run "
                    "-- poll_stale and the arm-rest switch are unchecked beyond the source-text test")
    javac, java = tools
    src = tmp_path / "src"
    src.mkdir()
    (src / "LinkHarness.java").write_text(LINK_RULES_HARNESS, encoding="utf-8")
    out = tmp_path / "out"
    subprocess.run([javac, "-encoding", "UTF-8", "-d", str(out), str(RULES), str(src / "LinkHarness.java")],
                   check=True, capture_output=True, encoding="utf-8", timeout=120)
    done = subprocess.run([java, "-cp", str(out), "com.emma.robot.control.LinkHarness"],
                          check=True, capture_output=True, encoding="utf-8", timeout=60)
    values = dict(line.partition("=")[::2] for line in done.stdout.splitlines())
    assert values == {"stale.idle_stale": "true", "stale.idle_fresh": "false",
                      "stale.own_stale": "false", "stale.own_fresh": "false",
                      "vouch.off": "true", "vouch.on": "false"}


def test_the_apps_status_says_when_its_poll_cannot_vouch_for_idle():
    nav = code_only(read(NAV))
    status = method(nav, "String status()")
    assert "NavRules.ownStatusWins(" in status
    assert "NavRules.statusPollStale(current != null && current.active(), pollFresh())" in status
    # on every status, false included: a status without it is "cannot tell"
    assert '.put("poll_stale", stale)' in status
    assert "return out;" not in status.split("boolean stale =", 1)[1].split("try {", 1)[0]
    # the poller runs whenever the app is in front, nav switch on or off and
    # with or without a call -- so the link can report a PC run between calls
    resume = method(nav, "void resume()")
    assert resume.index("pollTask = navPoller.scheduleWithFixedDelay(this::poll, 0, POLL_MS") \
        < resume.index("if (enabled()) refreshPlaces();")


def test_the_rest_switch_off_means_the_operator_vouches_for_the_arms():
    """Owner, 9 Oct 2026, at the robot: group 99 gave no #AGF, the first
    panel walk was refused arms_not_rest, "ไม่ต้องเก็บแขน เอาแบบนี้ไปเลย".
    Switch off = the operator's word: door legs stay offered and the launch
    passes --arms-rested; switch on = still only after a rest that finished."""
    nav = code_only(read(NAV))
    places = method(nav, "String places()")
    assert "arms_not_rest" not in places
    refusal = method(nav, "private String goRefusal(String place, String origin)")
    assert "arms_not_rest" not in refusal
    launch = method(nav, "private void launch(Run next)")
    assert "boolean armsRested = NavRules.operatorVouchesArms(requireArmRest());" in launch
    # with the switch on, a rest that did not finish still refuses
    on = launch.split("if (requireArmRest()) {", 1)[1]
    assert on.index('finish(next, "refused", "arms_not_rest");') < on.index("armsRested = true;")

# ==================== review 10 Oct: the server-side lows ====================


class _BrokenSendSocket(FakeSocket):
    """A page that vanished: the first send raises what the transport raises."""

    def __init__(self, exc):
        super().__init__()
        self.exc = exc

    async def send_text(self, text):
        raise self.exc


@pytest.mark.parametrize("exc_name", ["reset", "client_disconnected", "ws_closed"])
def test_a_link_that_dies_under_a_send_closes_quietly(caplog, exc_name):
    """Only WebSocketDisconnect/RuntimeError were caught: a reset socket, a
    uvicorn ClientDisconnected (an OSError) or websockets' ConnectionClosed
    under a send printed a full ASGI traceback for every reconnect of the
    page's backoff. Now one INFO line, and the link is still closed."""
    import logging

    from websockets.exceptions import ConnectionClosedError

    exc = {"reset": ConnectionResetError("reset by peer"),
           "client_disconnected": OSError("client disconnected"),
           "ws_closed": ConnectionClosedError(None, None)}[exc_name]
    sock = _BrokenSendSocket(exc)

    async def scenario():
        await asyncio.wait_for(robot_face_link.serve_status_link(sock), 2)

    with caplog.at_level(logging.INFO, logger=robot_face_link.logger.name):
        run(scenario())
    assert robot_face_link.live_faces() == []
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR or r.exc_info]
    assert any("status link dropped" in r.getMessage() for r in caplog.records)


def test_a_real_bug_in_the_link_loop_still_raises():
    """Quiet is for a closed socket only -- anything else is a bug to see."""
    sock = _BrokenSendSocket(ValueError("not a socket problem"))
    with pytest.raises(ValueError):
        run(robot_face_link.serve_status_link(sock))
    assert robot_face_link.live_faces() == [], "closed all the same"


def _hello_face(host: str):
    from types import SimpleNamespace

    face = CallSession()
    face.ws.client = SimpleNamespace(host=host)
    robot_face_link.claim(face)
    return face


def test_wrong_face_hellos_lock_the_host_out_for_a_while(monkeypatch, clock):
    """No limit existed: every socket, and every frame on it, could guess
    the ROBOT_FACE_TOKEN again. Per host, so a fresh socket is not a fresh
    counter; a right token clears the count; the lock ends by itself."""
    monkeypatch.setattr(settings, "robot_face_token", TOKEN)
    for _ in range(robot_face_link.HELLO_MAX_WRONG):
        robot_face_link.receive(_hello_face("10.0.0.9"), {"type": "face_hello", "token": "guess"})
    late = _hello_face("10.0.0.9")                      # a fresh socket, same host
    robot_face_link.receive(late, {"type": "face_hello", "token": TOKEN})
    assert not robot_face_link.is_robot_face(late), "locked: not even compared"
    other = _hello_face("127.0.0.1")                    # the robot itself is not locked
    robot_face_link.receive(other, {"type": "face_hello", "token": TOKEN})
    assert robot_face_link.is_robot_face(other)
    clock[0] += robot_face_link.HELLO_LOCK_S + 1
    robot_face_link.receive(late, {"type": "face_hello", "token": TOKEN})
    assert robot_face_link.is_robot_face(late)


def test_a_right_hello_clears_the_wrong_count(monkeypatch, clock):
    monkeypatch.setattr(settings, "robot_face_token", TOKEN)
    face = _hello_face("10.0.0.7")
    for _ in range(robot_face_link.HELLO_MAX_WRONG - 1):
        robot_face_link.receive(face, {"type": "face_hello", "token": "typo"})
    robot_face_link.receive(face, {"type": "face_hello", "token": TOKEN})
    again = _hello_face("10.0.0.7")
    for _ in range(robot_face_link.HELLO_MAX_WRONG - 1):
        robot_face_link.receive(again, {"type": "face_hello", "token": "typo"})
    robot_face_link.receive(again, {"type": "face_hello", "token": TOKEN})
    assert robot_face_link.is_robot_face(again)


def test_the_voice_arm_stop_goes_out_on_every_face_socket(monkeypatch):
    """`move_body(stop)` / the arm half of stop_moving was the one stop still
    sent on a single socket: a call socket that is quiet (busy streaming her
    reply, half-open) held it for a whole ack timeout and answered failed,
    while the page's status link would have taken it at once."""
    from app import robot_body
    from app.tools import registry

    monkeypatch.setattr(settings, "robot_body_ack_timeout_s", 0.3)

    async def scenario():
        call = _face(QuietPage())
        link, page = _claimed_link(LINK_IDLE)
        token = registry.robot_face_session.set(call)
        try:
            out = await robot_body.request("stop")
        finally:
            registry.robot_face_session.reset(token)
        return out, page, call

    out, page, call = run(scenario())
    assert out["ok"] is True and out["hardware"] == "requested"
    assert ("body_command", "stop") in [(f["type"], f["action"]) for f in page.sent]
    assert ("body_command", "stop") in [(f["type"], f["action"]) for f in call.ws.sent]


def test_a_desk_stop_with_a_blind_face_also_stops_through_the_face(monkeypatch):
    """Walk "unknown" (the face's link cannot vouch for idle: no
    `poll_stale: false`) used to send the board's pause alone -- the run's own
    stop never left, although a face was connected to carry it. Now both, at
    once; still not called a confirmed stop."""
    client, board = _http(monkeypatch)
    monkeypatch.setattr(settings, "robot_nav_routes_enabled", True)
    link, page = _claimed_link(IDLE)                  # connected, blind
    assert robot_face_link.nav_activity() == "unknown"
    assert robot_nav.stop_plan() == ("unknown", link)
    out = client.post("/robot/command", json={"token": TOKEN, "action": "stop"}).json()
    assert out["ok"] is False and out["did"] == "stop_unconfirmed"
    assert out["error"] == "run_unknown" and out["nav_stop"]["accepted"] is True
    assert out["hint"] == robot_nav.STOP_HINT_UNKNOWN_FACE
    assert sorted((f["type"], f["action"]) for f in page.sent) == [
        ("body_command", "stop"), ("nav_command", "stop")]
    assert len([c for c in board if c[0] == "DELETE"]) == 1


def test_no_face_at_all_keeps_the_unknown_desk_stop_as_it_was(monkeypatch):
    client, board = _http(monkeypatch)
    monkeypatch.setattr(settings, "robot_nav_routes_enabled", True)
    assert robot_nav.stop_plan() == ("unknown", None)
    out = client.post("/robot/command", json={"token": TOKEN, "action": "stop"}).json()
    assert out == {"ok": False, "did": "stop_unconfirmed", "error": "run_unknown",
                   "hint": robot_nav.STOP_HINT_UNKNOWN}


def test_a_voice_stop_with_a_blind_face_also_stops_through_the_face(monkeypatch):
    from app.tools import load_tools, registry, robot_link

    load_tools()
    robot_link.reset_state()
    monkeypatch.setattr(settings, "turn_log", False)
    monkeypatch.setattr(settings, "robot_enabled", True)
    monkeypatch.setattr(settings, "robot_nav_routes_enabled", True)
    board = []

    async def board_send(action, **args):
        board.append(action)
        return "ok"

    monkeypatch.setattr(robot_link, "send", board_send)

    async def scenario():
        link, page = _claimed_link(IDLE)              # connected, blind
        assert registry.robot_face_session.get() is None
        return await registry.dispatch("stop_moving", {}), page

    out, page = run(scenario())
    assert out["did"] == "stop_unconfirmed" and out["error"] == "run_unknown"
    assert out["nav_stop"]["accepted"] is True
    assert out["instruction"] == robot_nav.STOP_SAY_UNKNOWN_FACE
    assert sorted((f["type"], f["action"]) for f in page.sent) == [
        ("body_command", "stop"), ("nav_command", "stop")]
    assert board == ["cancel_navigation"]
    robot_link.reset_state()
