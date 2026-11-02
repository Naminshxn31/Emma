"""A bad network: what a dropped line, a failed dial and a slow one do (26 Oct).

The owner asked what Emma should do when the internet is bad. Before any
offline fallback, the audit of the call path (read against google-genai 2.24
and websockets 16.1.1, measured with blackholed resolvers and hosts) found
ways a bad link ended a call that could have gone on, or lied about it:

(a) `_reconnect` left `_session` on the dead socket: a guest talking while the
    line rejoined made the mic pump raise, and FIRST_COMPLETED ended the call;
(b) a tool result sent back on a dead socket ended the call, and the page
    never saw what the tool had done;
(c) a DNS timeout, the websocket handshake timeout and a TLS failure were all
    classified "provider", not "network";
(d) a failed dial sent `str(exc)` to the page, and the face then said "Ready";
(e) nothing of ours bounded the dial (CONNECT_TIMEOUT_S, off by default);
(f) the turn log had no row for a drop, a rejoin, the dial's time, or why a
    call ended.

Every socket here is a fake: nothing touches the network.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import socket
import ssl
import subprocess
import time
import types
from pathlib import Path

import pytest

from app.config import settings
from app.vad_gate import VadGate

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "client" / "voice-preview.html"

QUIET = b"\x30\x00" * 160
LOUD = (int(0.1 * 32767)).to_bytes(2, "little", signed=True) * 160


class Scripted:
    """A VAD detector that says what it is told, frame by frame."""

    def __init__(self, answers):
        self.answers = list(answers)

    def accept_waveform(self, samples):
        pass

    def is_speech_detected(self):
        return self.answers.pop(0) if self.answers else False


@pytest.fixture
def rows(monkeypatch):
    from app import turnlog

    out: list[dict] = []
    monkeypatch.setattr(turnlog, "record", lambda event, **f: out.append({"event": event, **f}))
    return out


def _closed():
    """What websockets raises on a send to a socket the server closed: no
    close frame received = 1006, the link."""
    from websockets.exceptions import ConnectionClosedError

    return ConnectionClosedError(None, None)


def _api_error(code: int, reason: str):
    """What the SDK raises from receive() when the server closes the socket."""
    from google.genai.errors import APIError

    return APIError(code, reason, None)


def _message(**content):
    base = dict(interrupted=False, input_transcription=None, output_transcription=None,
                model_turn=None, turn_complete=False)
    base.update(content)
    return types.SimpleNamespace(session_resumption_update=None, go_away=None, usage_metadata=None,
                                 tool_call_cancellation=None, tool_call=None,
                                 server_content=types.SimpleNamespace(**base))


class Live:
    """A socket that works. `passes`: what each receive() pass yields; past
    the last one it waits (the conversation goes on)."""

    def __init__(self, passes=()):
        self.sent: list = []
        self.passes = list(passes)

    async def send_realtime_input(self, **kw):
        self.sent.append(next(k for k, v in kw.items() if v is not None))

    async def send_client_content(self, **kw):
        self.sent.append("client_content")

    async def send_tool_response(self, **kw):
        self.sent.append("tool_response")

    async def receive(self):
        if not self.passes:
            await asyncio.Event().wait()
        for message in self.passes.pop(0):
            yield message


class Dead(Live):
    """The server closed it and we have not noticed yet: every send raises
    what websockets raises, and receive() raises what the SDK raises."""

    def __init__(self, error=None):
        super().__init__()
        self.error = error

    async def send_realtime_input(self, **kw):
        self.sent.append("written into a dead socket")
        raise _closed()

    async def send_tool_response(self, **kw):
        self.sent.append("tool_response")
        raise _closed()

    async def receive(self):
        if self.error is None:
            await asyncio.Event().wait()
        raise self.error
        yield                                               # pragma: no cover - a generator


class _Cm:
    def __init__(self):
        self.exited = False

    async def __aexit__(self, *exc):
        self.exited = True


def _provider(monkeypatch, answers=()):
    from app import vad_gate
    from app.providers.gemini import GeminiProvider

    monkeypatch.setattr(vad_gate, "for_session",
                        lambda **_: VadGate(Scripted(answers), prefix_padding_ms=100))
    provider = GeminiProvider("Kore", "x")
    provider.model = "gemini-3.8-live"
    provider._cm = _Cm()
    return provider


def _fc(name: str, call_id: str):
    return types.SimpleNamespace(id=call_id, name=name, args={})


async def _collect(agen):
    return [event async for event in agen]


# ---- (a) a guest talking while a dropped line rejoins ----------------------

def test_a_dropped_line_is_retired_before_it_rejoins_so_the_mic_never_writes_into_it(monkeypatch, rows):
    """The audit's mode 3c (i): with the dead socket still in `_session`
    during the rejoin, a guest's words raised inside the mic pump and the
    call ended even when the resume worked. `_cut_over` already drops the
    mic while it rejoins; the receive loop's own reconnect now does too."""
    provider = _provider(monkeypatch, [True, True, True])
    dead = Dead(_api_error(1011, "Internal error encountered."))
    new = Live(passes=[[_message(turn_complete=True)]])
    provider._session = dead
    provider._resume_handle = "handle"
    during = []

    async def connect():
        during.append(provider._session)
        await provider.send_audio(LOUD)          # the guest talks on while it rejoins
        provider._session = new
        provider._cm = _Cm()

    monkeypatch.setattr(provider, "_connect", connect)

    async def run():
        kinds = []
        stream = provider._receive_events()
        async for event in stream:
            kinds.append(event.kind)
            if event.kind == "turn_complete":
                break
        await stream.aclose()
        return kinds

    kinds = asyncio.run(asyncio.wait_for(run(), 5))
    assert during == [None], "the dead socket is retired before the new one opens"
    assert dead.sent == [], "nothing is written into the dead socket"
    assert "turn_complete" in kinds, "the call goes on, on the new socket"
    assert provider._session is new
    assert {"event": "upstream_closed", "code": 1011, "stage": "receive"} in rows
    rejoin = [r for r in rows if r["event"] == "reconnect"]
    assert len(rejoin) == 1 and rejoin[0]["road"] == "drop" and rejoin[0]["outcome"] == "resumed"
    assert isinstance(rejoin[0]["ms"], int)


def test_a_rejoin_that_fails_is_logged_and_ends_as_before(monkeypatch, rows):
    provider = _provider(monkeypatch)
    provider._session = Dead(_api_error(1006, "Abnormal closure."))
    provider._resume_handle = "handle"

    async def connect():
        raise OSError("connect call failed")

    monkeypatch.setattr(provider, "_connect", connect)
    kinds = [e.kind for e in asyncio.run(asyncio.wait_for(_collect(provider._receive_events()), 5))]
    assert kinds == ["error"], "a network really gone still ends the call"
    assert provider._session is None
    assert {"event": "upstream_closed", "code": 1006, "stage": "receive"} in rows
    assert [(r["road"], r["outcome"]) for r in rows if r["event"] == "reconnect"] == [("drop", "failed")]


def test_no_handle_to_rejoin_with_is_a_row_too(monkeypatch, rows):
    """20 Oct's two lost calls: heard -> session_end, nothing between."""
    provider = _provider(monkeypatch)
    provider._session = Dead(_api_error(1011, "Internal error encountered."))
    provider._resume_handle = None
    asyncio.run(asyncio.wait_for(_collect(provider._receive_events()), 5))
    assert [(r["road"], r["outcome"], r["ms"]) for r in rows if r["event"] == "reconnect"] == [
        ("drop", "no_handle", None)]


def test_go_away_still_rejoins_with_the_old_socket_taking_the_mic(monkeypatch, rows):
    """A healthy rotation is unchanged: the old socket is open and keeps the
    mic until the new one is up; only the row is new."""
    provider = _provider(monkeypatch)
    old = Live()
    provider._session = old
    provider._resume_handle = "handle"
    during = []

    async def connect():
        during.append(provider._session)
        provider._session = Live()

    monkeypatch.setattr(provider, "_connect", connect)
    assert asyncio.run(provider._reconnect()) is True
    assert during == [old]
    assert [(r["road"], r["outcome"]) for r in rows if r["event"] == "reconnect"] == [("go_away", "resumed")]
    assert not [r for r in rows if r["event"] == "upstream_closed"]


class _Page:
    """A page that sends `frames` and then hangs up."""

    def __init__(self, frames):
        self.frames = list(frames)
        self.sent: list[str] = []

    async def receive(self):
        if self.frames:
            return {"type": "websocket.receive", "bytes": self.frames.pop(0)}
        return {"type": "websocket.disconnect"}

    async def send_text(self, text):
        self.sent.append(text)


def test_a_socket_closed_under_the_mic_does_not_end_the_call(monkeypatch, rows):
    """Before the receive loop has noticed the close, the mic pump's send
    raised ConnectionClosed through `_browser_to_provider`, which returned --
    and FIRST_COMPLETED ended a call the loop's resume would have saved.
    The pump now runs until the page itself hangs up."""
    from app.session import VoiceSession

    provider = _provider(monkeypatch, [True] * 8)
    provider._session = Dead()
    session = VoiceSession(_Page([LOUD, LOUD, LOUD]))
    session.provider = provider
    asyncio.run(asyncio.wait_for(session._browser_to_provider(), 5))
    assert session._end_reason == "browser_closed", "not mic_pump_failed"
    assert [r for r in rows if r["event"] == "upstream_closed"] == [
        {"event": "upstream_closed", "code": 1006, "stage": "mic"}], "one row per socket, not per frame"


def test_a_send_that_was_wrong_still_raises(monkeypatch):
    """Only a closed socket is swallowed; a bug in what we sent is not."""
    provider = _provider(monkeypatch, [True])

    class Broken(Live):
        async def send_realtime_input(self, **kw):
            raise ValueError("not a closed socket")

    provider._session = Broken()
    with pytest.raises(ValueError):
        asyncio.run(provider.send_audio(LOUD))


# ---- (b) a tool result on a dead socket ------------------------------------

def test_a_tool_result_on_a_dead_socket_reaches_the_page_and_the_stream_goes_on(monkeypatch, rows):
    """The audit's mode 3c (ii): send_tool_response on the dead socket raised
    inside the tool runner, which put an error and ended the call -- and the
    result (a story's picture) never reached the page, because the yields
    come after the send."""
    from app import tools

    provider = _provider(monkeypatch)

    class HalfDead(Live):           # receive() waits; the send finds it closed
        async def send_tool_response(self, **kw):
            raise _closed()

    socket_ = HalfDead()
    provider._session = socket_
    result = {"ok": True, "screen": "story"}

    async def dispatch(calls):
        return [(cid, name, dict(result)) for cid, name, _ in calls]

    monkeypatch.setattr(tools, "dispatch_all", dispatch)

    async def scenario():
        kinds = []
        stream = provider.events()

        async def collect():
            async for event in stream:
                kinds.append((event.kind, event.data))
                if event.kind == "tool_result":
                    return

        reader = asyncio.create_task(collect())
        await asyncio.sleep(0)
        await provider._tool_batches.put((socket_, [_fc("present_story", "id-1")]))
        await asyncio.wait_for(reader, 5)
        await asyncio.sleep(0.05)
        alive = [not task.done() for task in provider._event_tasks]
        queued = provider._event_queue.qsize()
        await stream.aclose()
        return kinds, alive, queued

    kinds, alive, queued = asyncio.run(scenario())
    assert [k for k, _ in kinds] == ["tool_call", "tool_result"]
    assert kinds[1][1] == result, "the page gets what the tool did"
    assert alive == [True, True] and queued == 0, "no error, no end of stream: the call goes on"
    assert {"event": "upstream_closed", "code": 1006, "stage": "tool_response"} in rows


def test_a_drop_noticed_while_the_tool_ran_shows_the_result_and_sends_it_nowhere(monkeypatch, rows):
    from app import tools

    provider = _provider(monkeypatch)
    dead = Live()
    replacement = Live()
    provider._session = dead

    async def dispatch(calls):
        provider._retire(dead)                  # the receive loop saw the close
        provider._session = replacement         # ...and rejoined, before the tool finished
        return [(cid, name, {"ok": True}) for cid, name, _ in calls]

    monkeypatch.setattr(tools, "dispatch_all", dispatch)
    events = asyncio.run(_collect(provider._run_tool_calls([_fc("present_story", "id-1")])))
    assert [e.kind for e in events] == ["tool_call", "tool_result"]
    assert dead.sent == [] and replacement.sent == [], \
        "a result from a retired connection must not enter its replacement"


def test_a_tool_response_that_was_wrong_still_fails_closed(monkeypatch):
    from app import tools

    provider = _provider(monkeypatch)

    class Refuses(Live):
        async def send_tool_response(self, **kw):
            raise TypeError("not serialisable")

    provider._session = Refuses()

    async def dispatch(calls):
        return [(cid, name, {"ok": True}) for cid, name, _ in calls]

    monkeypatch.setattr(tools, "dispatch_all", dispatch)
    with pytest.raises(TypeError):
        asyncio.run(_collect(provider._run_tool_calls([_fc("present_story", "id-1")])))


# ---- (c) what counts as the network's fault ---------------------------------

def _wrapped(cause: BaseException, prefix="Could not open a Gemini Live session: "):
    from app.providers import ProviderError

    try:
        raise cause
    except BaseException as exc:
        try:
            raise ProviderError(prefix + str(exc)) from exc
        except ProviderError as wrapped:
            return wrapped


def _ssl_failure():
    return ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")


@pytest.mark.parametrize("make, kind", [
    # the three outage shapes that read "provider" before 26 Oct
    (lambda: _wrapped(socket.gaierror(-3, "robot_dns: generativelanguage.googleapis.com (timed out)")),
     "network"),
    (lambda: _wrapped(TimeoutError("timed out during opening handshake")), "network"),
    (lambda: _wrapped(_ssl_failure()), "network"),
    # by words alone, no cause attached
    (lambda: Exception("[Errno -3] robot_dns: generativelanguage.googleapis.com (no answer from 1.1.1.1)"),
     "network"),
    (lambda: Exception("timed out during opening handshake"), "network"),
    (lambda: Exception("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"), "network"),
    (lambda: Exception("1006 None. Abnormal closure."), "network"),
    # our own ceiling, and a close with no close frame
    (lambda: _wrapped(__import__("app.providers.gemini", fromlist=["x"]).ConnectTimeout("dial")),
     "network"),
    (lambda: _wrapped(_api_error(1006, "Abnormal closure.")), "network"),
    (lambda: _wrapped(_closed()), "network"),
    # what it was before, still
    (lambda: Exception("[Errno 111] Connect call failed"), "network"),
    (lambda: _wrapped(_api_error(1011, "Internal error encountered.")), "provider"),
    (lambda: Exception("1008 None. BidiGenerateContent session not found or expired"), "provider"),
    (lambda: Exception("1011 None. RESOURCE_EXHAUSTED: quota"), "quota"),
    (lambda: Exception("1007 None. API key not valid. Please pass a valid API key."), "auth"),
    (lambda: Exception("[WinError 5] Access is denied"), "permission"),
])
def test_failures_are_classified_by_type_and_by_words(make, kind):
    from app.session import _provider_failure_kind

    assert _provider_failure_kind(make()) == kind


# ---- (d) + (f) a failed dial, an upstream error, and why a call ended -------

class _DeskSocket:
    """The page: says nothing (the browser pump waits), records what it gets."""

    def __init__(self):
        self.headers = {"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        self.query_params = {}
        self.sent: list[str] = []
        self.closed = False

    async def accept(self):
        pass

    async def receive(self):
        await asyncio.Event().wait()

    async def send_text(self, text):
        self.sent.append(text)

    async def send_bytes(self, data):
        pass

    async def close(self, *a, **k):
        self.closed = True


class _FakeProvider:
    input_sample_rate = 16000
    output_sample_rate = 24000
    session_limit_minutes = 15
    auto_resumes = True
    model = "fake"

    def __init__(self, script=(), fail: BaseException | None = None):
        self.script = list(script)
        self.fail = fail

    async def __aenter__(self):
        if self.fail is not None:
            raise self.fail
        return self

    async def __aexit__(self, *exc):
        return False

    def stand_down_floor(self):
        pass

    async def send_text(self, text):
        pass

    async def send_audio(self, data):
        pass

    async def events(self):
        for event in self.script:
            yield event


def _call(monkeypatch, provider) -> _DeskSocket:
    from app import session as session_module

    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(settings, "idle_timeout_s", 0)
    monkeypatch.setattr(settings, "canva_url", "")
    monkeypatch.setattr(session_module, "get_provider", lambda *a, **k: provider)
    ws = _DeskSocket()
    asyncio.run(asyncio.wait_for(session_module.handle_connection(ws), 10))
    return ws


def test_a_failed_dial_tells_the_page_a_category_never_the_exception(monkeypatch, rows):
    """Audit 26 Oct: `str(exc)` went to the page -- the robot's public screen
    painted resolver detail, then the close made the pill say "Ready"."""
    from app.session import DIAL_FAILED_TEXT

    failure = _wrapped(socket.gaierror(-3, "robot_dns: generativelanguage.googleapis.com (timed out)"))
    ws = _call(monkeypatch, _FakeProvider(fail=failure))
    frames = [json.loads(text) for text in ws.sent]
    assert frames == [{"type": "error", "code": "provider_error", "category": "network",
                       "message": DIAL_FAILED_TEXT["network"]}]
    assert "robot_dns" not in "".join(ws.sent) and "googleapis" not in "".join(ws.sent)
    error = [r for r in rows if r["event"] == "provider_error"]
    assert len(error) == 1 and error[0]["category"] == "network" and isinstance(error[0]["ms"], int)
    assert [r["reason"] for r in rows if r["event"] == "session_end"] == ["dial_failed"]


@pytest.mark.parametrize("provider_name,key,other", [("gemini", "GEMINI_API_KEY", "OPENAI_API_KEY"),
                                                     ("openai", "OPENAI_API_KEY", "GEMINI_API_KEY")])
def test_a_refused_key_names_the_key_of_the_provider_that_dialled(monkeypatch, rows, provider_name, key, other):
    """Review 26 Oct: the category table hard-coded GEMINI_API_KEY, so a wrong
    OpenAI key sent the staff to check the wrong line of .env."""
    from app.providers import ProviderError
    from app.session import VoiceSession

    ws = _DeskSocket()
    session = VoiceSession(ws, provider_name=provider_name)
    failure = ProviderError("Could not open a session: Error code: 401 - Incorrect API key provided")
    asyncio.run(session._report_failed_dial(failure, 0.2))
    frames = [json.loads(text) for text in ws.sent]
    assert [f["category"] for f in frames] == ["auth"]
    assert key in frames[0]["message"] and other not in frames[0]["message"]


def test_an_internal_failure_never_sends_its_text_either(monkeypatch, rows):
    ws = _call(monkeypatch, _FakeProvider(fail=RuntimeError("the guest said 0812345678")))
    frames = [json.loads(text) for text in ws.sent]
    assert [f["code"] for f in frames] == ["internal"]
    assert "0812345678" not in "".join(ws.sent)
    assert [r["reason"] for r in rows if r["event"] == "session_end"] == ["internal"]


def test_an_upstream_error_carries_a_category_and_the_call_says_why_it_ended(monkeypatch, rows):
    from app.providers.base import ProviderEvent

    ws = _call(monkeypatch, _FakeProvider(script=[
        ProviderEvent(kind="error", text="1006 None. Abnormal closure.")]))
    errors = [f for f in map(json.loads, ws.sent) if f["type"] == "error"]
    assert errors == [{"type": "error", "code": "upstream", "category": "network",
                       "message": "การเชื่อมต่อผู้ช่วยขัดข้องชั่วคราว"}]
    assert [r["reason"] for r in rows if r["event"] == "session_end"] == ["upstream_error"]
    ready = [r for r in rows if r["event"] == "voice_ready"]
    assert len(ready) == 1 and isinstance(ready[0]["connect_ms"], int)


def test_a_stream_that_just_ends_is_upstream_closed(monkeypatch, rows):
    _call(monkeypatch, _FakeProvider(script=[]))
    assert [r["reason"] for r in rows if r["event"] == "session_end"] == ["upstream_closed"]


def test_a_superseded_call_says_so(monkeypatch, rows):
    from app import session as session_module

    old = session_module.VoiceSession(_DeskSocket())
    session_module._active = old
    _call(monkeypatch, _FakeProvider(script=[]))
    assert old._end_reason == "superseded"


# ---- (d) the robot face's own words for it ----------------------------------

def test_the_face_draws_its_own_english_line_and_keeps_it_through_the_close():
    """FIXED_UI_LANG is 'en' on the robot: the line comes from the category,
    never from the server's message (Thai, and once the exception's text),
    and the close that follows every error must not repaint it "Ready"."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to run the face's error handler -- the face's "
                    "error lines are NOT being checked on this machine")
    script = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const html = fs.readFileSync('client/voice-preview.html', 'utf8');
const handler = html.slice(html.indexOf('    function onMessage(msg)'), html.indexOf('    // ---- dial / hang up ----'));
const said = [];
const c = vm.createContext({
  callError: false, manualEnd: false, ArrayBuffer, performance: { now: () => 1 },
  show() {}, status(text) { said.push(text); },
});
vm.runInContext(handler, c);
const error = (evt) => { said.length = 0; c.onMessage({ data: JSON.stringify(Object.assign({ type: 'error' }, evt)) }); return said[said.length - 1]; };
assert.equal(error({ code: 'provider_error', category: 'network', message: 'robot_dns: generativelanguage.googleapis.com (timed out)' }),
             "No internet — Emma can't talk right now");
assert.equal(error({ code: 'provider_error', category: 'provider', message: 'Could not open a Gemini Live session: 1011' }),
             "Emma can't connect right now");
assert.equal(error({ code: 'upstream', category: 'provider', message: 'การเชื่อมต่อผู้ช่วยขัดข้องชั่วคราว' }),
             'Disconnected — tap the mic to try again');
assert.equal(error({ code: 'upstream', category: 'network', message: 'การเชื่อมต่อผู้ช่วยขัดข้องชั่วคราว' }),
             "No internet — Emma can't talk right now");
const rejected = 'Session setup REJECTED; guardrails unavailable. Stop this customer session.';
assert.equal(error({ code: 'upstream', category: 'rejected', message: rejected }), rejected);
assert.equal(error({ code: 'superseded', message: 'เปิดหน้าใหม่แล้ว' }), 'Another tab is using Emma');
assert.equal(c.callError, true);
// the close repaints the idle pose ("Ready"); the line comes back after it
said.length = 0; c.keepErrorStatus();
assert.deepEqual(said, ['Another tab is using Emma']);
c.callError = false; said.length = 0; c.keepErrorStatus();
assert.deepEqual(said, [], 'a call that ended well rests as before');
console.log('PASS');
"""
    result = subprocess.run([node, "-e", script], cwd=ROOT, capture_output=True,
                            encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr or result.stdout
    html = PAGE.read_text(encoding="utf-8")
    onclose = html[html.index("ws.onclose = function () {"):]
    onclose = onclose[:onclose.index("function endCall()")]
    assert "show('idle'); setMicLabel('Muted'); keepErrorStatus();" in onclose
    assert "else { show('idle'); keepErrorStatus(); }" in onclose
    for key in ("No internet — Emma can't talk right now", "Emma can't connect right now"):
        assert html.count('"%s"' % key) >= 3, "%s: the screen's line and its th/zh rows" % key


# ---- (e) CONNECT_TIMEOUT_S --------------------------------------------------

class _SlowDial:
    """A dial that stalls (the SDK's setup reply that never comes), and whose
    cancel takes `cleanup` seconds -- websockets' close on a dead link."""

    def __init__(self, delay=30.0, cleanup=0.0, ignore_cancel=False):
        self.delay, self.cleanup, self.ignore_cancel = delay, cleanup, ignore_cancel
        self.exited = False

    async def __aenter__(self):
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            if self.ignore_cancel:
                return "opened late"
            await asyncio.sleep(self.cleanup)
            raise
        return "session"

    async def __aexit__(self, *exc):
        self.exited = True


def _dialing(monkeypatch, dial):
    from google import genai

    from app.providers.gemini import GeminiProvider

    live = types.SimpleNamespace(connect=lambda model, config: dial)
    monkeypatch.setattr(genai, "Client", lambda **kw: types.SimpleNamespace(
        aio=types.SimpleNamespace(live=live)))
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(settings, "gemini_model_fallback", "")
    provider = GeminiProvider("Kore", "x")
    provider._build_config = lambda: {}
    return provider


def test_a_stalled_dial_fails_at_the_ceiling_as_a_network_fault(monkeypatch):
    from app.providers import ProviderError
    from app.session import _provider_failure_kind

    monkeypatch.setattr(settings, "connect_timeout_s", 0.1)
    provider = _dialing(monkeypatch, _SlowDial(cleanup=3.0))

    async def dial():
        started = time.monotonic()
        with pytest.raises(ProviderError) as caught:
            await asyncio.wait_for(provider.__aenter__(), 5)
        return time.monotonic() - started, caught.value

    took, error = asyncio.run(dial())
    assert took < 1.0, "the ceiling holds even while the abandoned dial cleans up (%.1fs)" % took
    assert _provider_failure_kind(error) == "network"


def test_no_ceiling_by_default(monkeypatch):
    assert settings.connect_timeout_s == 0, "conftest/.env must not switch it on for the suite"
    provider = _dialing(monkeypatch, _SlowDial(delay=0.2))
    asyncio.run(asyncio.wait_for(provider.__aenter__(), 5))
    assert provider._session == "session"


def test_a_dial_that_opens_after_the_ceiling_is_closed(monkeypatch):
    from app.providers.gemini import ConnectTimeout, _enter_within

    dial = _SlowDial(delay=0.3, ignore_cancel=True)

    async def scenario():
        with pytest.raises(ConnectTimeout):
            await _enter_within(dial, 0.05)
        await asyncio.sleep(0.1)
        return dial.exited

    assert asyncio.run(scenario()) is True, "nobody will ever read that session"


def test_a_dial_cancelled_from_outside_that_opens_anyway_is_closed(monkeypatch):
    """Review 27 Oct, 2nd pass: the call closing (or a newer watch) cancels a
    dial; one that opened anyway was read by nobody and stayed open."""
    from app.providers.gemini import _enter_within

    dial = _SlowDial(delay=0.3, ignore_cancel=True)

    async def scenario():
        task = asyncio.ensure_future(_enter_within(dial, 5.0))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.1)
        return dial.exited

    assert asyncio.run(scenario()) is True, "nobody will ever read that session"


def test_the_stage3_dial_leaves_the_call_as_it_was(monkeypatch):
    """`_dial` (stage 3's fallback dial, review 27 Oct 2nd pass) hands the new
    session back: while it dials and after, the call's model, handle, socket
    and context manager are still the stalled session's. Dialing through
    `_connect` set them to the fallback's for the whole dial -- the receive
    loop then found no handle and ended the call."""
    from google import genai

    fallback = "gemini-2.5-flash-native-audio-preview-12-2025"
    dial = _SlowDial(delay=0.05)
    provider = _dialing(monkeypatch, dial)
    seen, clients = {}, []

    def client(**kw):
        def connect(model, config):
            seen["model"], seen["config"] = model, config
            return dial
        clients.append(kw)
        return types.SimpleNamespace(aio=types.SimpleNamespace(live=types.SimpleNamespace(connect=connect)))

    monkeypatch.setattr(genai, "Client", client)
    provider._build_config = lambda: {"handle": provider._resume_handle, "model": provider.model}
    provider.model, provider._resume_handle = "gemini-3.8-live", "h-38"
    old_cm, old_session = object(), object()
    provider._cm, provider._session = old_cm, old_session

    async def run():
        task = asyncio.ensure_future(provider._dial(fallback, None))
        await asyncio.sleep(0.01)                    # mid-dial
        seen["mid"] = (provider.model, provider._resume_handle, provider._cm, provider._session)
        return await task

    cm, session = asyncio.run(run())
    assert (cm, session) == (dial, "session")
    assert seen["model"] == fallback and seen["config"] == {"handle": None, "model": fallback}
    assert clients[0]["http_options"] == {"api_version": "v1beta"}
    call = ("gemini-3.8-live", "h-38", old_cm, old_session)
    assert seen["mid"] == call, "the dial touched the call's state"
    assert (provider.model, provider._resume_handle, provider._cm, provider._session) == call
