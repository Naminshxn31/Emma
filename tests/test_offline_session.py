"""The offline backup wired into a real call (OFFLINE_FALLBACK, 26 Oct 2026).

The owner: "what should Emma do when the internet is bad? ... build an
emergency backup." The pieces (net_health, the offline provider, the router,
approved lines and clips) are tested on their own in tests/test_offline_*.py;
this file is the seam between them and `VoiceSession`, through
`handle_connection` with a fake page socket:

* switch OFF: a failed dial is exactly the error frame + close it was, and a
  healthy call's `ready` and turn-log rows carry nothing new;
* switch ON, the Gemini dial fails for the network: the SAME page socket gets
  `ready` with `offline: true` -- no error, no close, no "Ready" on a robot
  that cannot talk (the audit's measured outage shape);
* a heard story question reaches the page as present_story's tool_result
  with the approved clip's audio frames; a draft line is never said;
* the link already known down: no Gemini dial at all;
* the link back: the offline call ends at a quiet moment, so the next dial
  is Gemini.

Every socket, recognizer and probe here is a fake: nothing touches the
network, no model is loaded, no API is called.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from app import net_health, stories
from app.config import settings
from app.offline import asr, router
from app.providers import ProviderError
from tests.test_offline_provider import STORY, FakeEngine, ScriptedDetector, _clip, _warm_tokenizer
from app.vad_gate import VadGate

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "client" / "voice-preview.html"
CHUNK = b"\x01\x02" * 160          # 20 ms at 16 kHz, above the dead-mic floor


def _network_failure() -> ProviderError:
    """What GeminiProvider.__aenter__ raises when robot_dns cannot resolve
    the Live host (ProviderError `from` the gaierror)."""
    try:
        raise socket.gaierror(-3, "robot_dns: generativelanguage.googleapis.com (timed out)")
    except socket.gaierror as cause:
        try:
            raise ProviderError("Could not open a Gemini Live session: %s" % cause) from cause
        except ProviderError as wrapped:
            return wrapped


class _Page:
    """The page: sends `frames` (bytes = mic audio), then holds the line
    for `hold_s` and hangs up -- or never, with hold_s None."""

    def __init__(self, frames=(), hold_s: float | None = 1.0):
        self.headers = {"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        self.query_params = {}
        self.frames = list(frames)
        self.hold_s = hold_s
        self.sent: list[str] = []
        self.audio: list[bytes] = []
        self.closed = False

    async def accept(self):
        pass

    async def receive(self):
        if self.frames:
            await asyncio.sleep(0.005)
            return {"type": "websocket.receive", "bytes": self.frames.pop(0)}
        if self.hold_s is None:
            await asyncio.Event().wait()
        await asyncio.sleep(self.hold_s)
        return {"type": "websocket.disconnect"}

    async def send_text(self, text):
        self.sent.append(text)

    async def send_bytes(self, data):
        self.audio.append(bytes(data))

    async def close(self, *a, **k):
        self.closed = True

    def frames_of(self, kind=None):
        out = [json.loads(text) for text in self.sent]
        return [f for f in out if kind is None or f.get("type") == kind]


class _Gemini:
    """A Gemini dial: fails with `fail`, or opens and plays `script`."""

    input_sample_rate = 16000
    output_sample_rate = 24000
    session_limit_minutes = 15
    auto_resumes = True
    model = "fake-live"

    def __init__(self, fail: BaseException | None = None, script=()):
        self.fail = fail
        self.script = list(script)
        self.entered = 0

    async def __aenter__(self):
        self.entered += 1
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


@pytest.fixture
def rows(monkeypatch):
    from app import turnlog

    out: list[dict] = []
    monkeypatch.setattr(turnlog, "record", lambda event, **f: out.append({"event": event, **f}))
    return out


@pytest.fixture
def no_probe(monkeypatch):
    """A probe that fails the test if anything asks the network."""
    async def probe(*a, **k):
        raise AssertionError("the network was probed")

    monkeypatch.setattr(net_health, "probe", probe)


def _probe_says(monkeypatch, ok: bool):
    calls = []

    async def probe(*a, **k):
        calls.append(ok)
        return (True, "probe_ok") if ok else (False, "dns")

    monkeypatch.setattr(net_health, "probe", probe)
    return calls


@pytest.fixture
def offline_on(monkeypatch):
    """The switch on, the offline provider's ears scripted (five frames of
    speech, then silence) and its catalog prepared without the slide index."""
    monkeypatch.setattr(settings, "offline_fallback", True)
    monkeypatch.setattr(router, "prepare", _warm_tokenizer)
    from app.providers import offline as module

    monkeypatch.setattr(module, "_own_gate", lambda vad_floor: VadGate(
        ScriptedDetector([True] * 5 + [False] * 3), prefix_padding_ms=0))
    return module


def _call(monkeypatch, gemini, page, *, provider_name=None, timeout=10.0):
    """One call through handle_connection. `get_provider` hands out `gemini`
    for a Gemini dial and the REAL registry's answer for "offline" (so with
    the switch off that name stays unknown, as it always was)."""
    from app import session as session_module
    from app.providers import get_provider as real_get_provider

    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(settings, "idle_timeout_s", 0)
    monkeypatch.setattr(settings, "canva_url", "")
    asked: list[str] = []

    def get_provider(name, *a, **k):
        asked.append(name)
        return gemini if name == "gemini" else real_get_provider(name, *a, **k)

    monkeypatch.setattr(session_module, "get_provider", get_provider)
    asyncio.run(asyncio.wait_for(session_module.handle_connection(page, provider=provider_name),
                                 timeout))
    return asked


# ---- switch off: nothing changes --------------------------------------------

def test_switch_off_a_network_dial_failure_is_the_old_error_and_close(monkeypatch, rows, no_probe):
    from app.session import DIAL_FAILED_TEXT

    page = _Page()
    asked = _call(monkeypatch, _Gemini(fail=_network_failure()), page)
    assert asked == ["gemini"], "no offline provider is ever built with the switch off"
    assert page.frames_of() == [{"type": "error", "code": "provider_error", "category": "network",
                                 "message": DIAL_FAILED_TEXT["network"]}]
    assert page.closed
    assert not [r for r in rows if r["event"] in ("offline_fallback", "net_state", "offline_end")]
    assert [r["reason"] for r in rows if r["event"] == "session_end"] == ["dial_failed"]
    assert net_health.health._fails == 0, "the hysteresis is the switch's, never fed with it off"


def test_switch_off_a_healthy_call_carries_nothing_new(monkeypatch, rows, no_probe):
    from app.providers.base import ProviderEvent

    page = _Page()
    gemini = _Gemini(script=[ProviderEvent(kind="assistant_transcript", text="Hello."),
                             ProviderEvent(kind="turn_complete")])
    _call(monkeypatch, gemini, page)
    ready = page.frames_of("ready")
    assert len(ready) == 1 and ready[0]["provider"] == "gemini" and "offline" not in ready[0]
    start = [r for r in rows if r["event"] == "session_start"]
    assert start == [{"event": "session_start", "provider": "gemini", "voice": settings.gemini_voice}]
    voice_ready = [r for r in rows if r["event"] == "voice_ready"]
    assert len(voice_ready) == 1 and set(voice_ready[0]) == {"event", "connect_ms"}
    assert not [r for r in rows if r["event"].startswith("offline")]


def test_switch_off_the_session_enters_the_provider_itself(monkeypatch):
    """No wrapper between run() and the provider: the dial is the one it was."""
    from app.session import VoiceSession

    session = VoiceSession(_Page())
    gemini = _Gemini()
    assert session._offline_first is None
    assert session._offline_dial(gemini, "x", {}) is gemini


def test_switch_off_provider_offline_in_the_url_is_still_unknown(monkeypatch, rows):
    """`?provider=offline` means nothing without the switch: the old missing
    key / unknown-provider path, never an offline call."""
    from app.session import VoiceSession

    monkeypatch.setattr(settings, "openai_api_key", "")
    session = VoiceSession(_Page(), provider_name="offline")
    assert session._offline_first is None
    page = _Page()
    _call(monkeypatch, _Gemini(), page, provider_name="offline")
    assert [f["code"] for f in page.frames_of("error")] == ["missing_api_key"]


# ---- switch on: the same socket falls back ----------------------------------

def test_a_network_dial_failure_opens_the_offline_voice_on_the_same_socket(
        monkeypatch, rows, offline_on, no_probe):
    monkeypatch.setattr(asr, "engine", lambda: FakeEngine())
    page = _Page(hold_s=0.3)
    gemini = _Gemini(fail=_network_failure())
    asked = _call(monkeypatch, gemini, page)
    assert asked == ["gemini", "offline"] and gemini.entered == 1
    assert not page.frames_of("error"), "a fallback is not an error on the screen"
    ready = page.frames_of("ready")
    assert len(ready) == 1
    assert ready[0]["offline"] is True and ready[0]["provider"] == "offline"
    assert (ready[0]["input_rate"], ready[0]["output_rate"]) == (16000, 24000)
    failed = [r for r in rows if r["event"] == "provider_error"]
    assert len(failed) == 1 and failed[0]["category"] == "network"
    assert failed[0]["fallback"] == "offline" and isinstance(failed[0]["ms"], int)
    assert [r for r in rows if r["event"] == "offline_fallback"] == [
        {"event": "offline_fallback", "why": "dial", "category": "network"}]
    voice_ready = [r for r in rows if r["event"] == "voice_ready"]
    assert len(voice_ready) == 1 and voice_ready[0]["provider"] == "offline"
    assert [r["reason"] for r in rows if r["event"] == "session_end"] == ["browser_closed"]
    # one network failure noted: the hysteresis needs a second to say OFFLINE
    assert net_health.health._fails == 1 and not net_health.health.offline()


def test_a_heard_story_question_reaches_the_page_with_its_approved_clip(
        monkeypatch, rows, offline_on, no_probe):
    """The full road: mic frames -> the offline gate -> (stub) recognizer ->
    router -> present_story's own tool_result on the page + the clip's PCM
    as binary frames, the words as the bubble."""
    engine = FakeEngine("ที่จอดรถมีกี่คันคะ", "th")
    monkeypatch.setattr(asr, "engine", lambda: engine)
    monkeypatch.setattr(router, "route",
                        lambda *a, **k: router.Route("story", topic="parking", why="story"))
    monkeypatch.setattr(stories, "tool_result", lambda topic: dict(STORY, topic=topic))
    # present_story is offered to this session (robot face + stories switch
    # in production); the session context is not what is under test here
    monkeypatch.setattr(offline_on.OfflineProvider, "_stories_on", lambda self: True)
    pcm = _clip(STORY["say"][0]["th"], "th")
    page = _Page([CHUNK] * 8, hold_s=0.8)
    _call(monkeypatch, _Gemini(fail=_network_failure()), page)

    assert page.frames_of("ready")[0]["offline"] is True
    heard = page.frames_of("user_transcript")
    assert [f["text"] for f in heard] == ["ที่จอดรถมีกี่คันคะ"]
    results = page.frames_of("tool_result")
    assert len(results) == 1 and results[0]["name"] == "present_story"
    assert results[0]["result"]["screen"] == "story" and results[0]["result"]["topic"] == "parking"
    assert [f["name"] for f in page.frames_of("tool_call")] == ["present_story"]
    said = "".join(f["text"] for f in page.frames_of("assistant_transcript"))
    assert said == STORY["say"][0]["th"] + " " + STORY["say"][1]["th"]
    assert b"".join(page.audio) == pcm, "exactly the approved clip, nothing synthesized"
    assert page.frames_of("turn_complete")
    assert "tool" in [r["event"] for r in rows]


def test_an_unapproved_line_is_never_spoken_or_shown(monkeypatch, rows, offline_on, no_probe):
    """The shipped lines.json is all drafts: a price question routes to the
    sales hand-off, and nothing reaches the bubble or the speaker -- even
    with a clip of the draft's words on disk."""
    from app.offline import lines

    engine = FakeEngine("ราคาเริ่มต้นเท่าไหร่คะ", "th")
    monkeypatch.setattr(asr, "engine", lambda: engine)
    shipped = json.loads((ROOT / "data" / "projects" / "embassy_world" / "offline"
                          / "lines.json").read_text(encoding="utf-8"))
    assert all(line.get("approved_by") is None for line in shipped["lines"])
    draft = next(line for line in shipped["lines"] if line["id"] == "handoff_commercial")
    _clip(lines.norm(draft["th"]), "th")
    page = _Page([CHUNK] * 8, hold_s=0.8)
    _call(monkeypatch, _Gemini(fail=_network_failure()), page)
    assert page.frames_of("ready")[0]["offline"] is True
    assert engine.calls, "the segment was heard and decoded"
    assert not page.frames_of("assistant_transcript")
    assert page.audio == []
    routed = [r for r in rows if r["event"] == "offline_route"]
    assert routed and routed[0]["line"] == "handoff_commercial"


def test_a_quota_failure_on_a_link_that_answers_is_the_old_error(monkeypatch, rows, offline_on):
    """Not every failed dial is an outage: the network is asked, and a link
    that answers keeps the error a quota or a key deserves."""
    from app.session import DIAL_FAILED_TEXT

    calls = _probe_says(monkeypatch, True)
    page = _Page()
    asked = _call(monkeypatch, _Gemini(fail=ProviderError("Could not open: 429 RESOURCE_EXHAUSTED quota")),
                  page)
    assert calls == [True]
    assert asked == ["gemini"]
    assert page.frames_of() == [{"type": "error", "code": "provider_error", "category": "quota",
                                 "message": DIAL_FAILED_TEXT["quota"]}]


def test_an_unclassified_failure_on_a_dead_link_falls_back(monkeypatch, rows, offline_on):
    monkeypatch.setattr(asr, "engine", lambda: FakeEngine())
    calls = _probe_says(monkeypatch, False)
    page = _Page(hold_s=0.2)
    asked = _call(monkeypatch, _Gemini(fail=ProviderError("Could not open: 1011 None.")), page)
    assert calls == [False] and asked == ["gemini", "offline"]
    assert page.frames_of("ready")[0]["offline"] is True
    assert [r["category"] for r in rows if r["event"] == "offline_fallback"] == ["provider"]


def test_a_link_known_down_skips_the_gemini_dial(monkeypatch, rows, offline_on, no_probe):
    monkeypatch.setattr(asr, "engine", lambda: FakeEngine())
    net_health.note_dial_failure("network")
    net_health.note_dial_failure("network")
    assert net_health.is_offline()
    gemini = _Gemini()
    page = _Page(hold_s=0.2)
    asked = _call(monkeypatch, gemini, page)
    assert asked == ["offline"] and gemini.entered == 0, "no 10 s dial into a dead link"
    assert [r["provider"] for r in rows if r["event"] == "session_start"] == ["offline"]
    assert [r for r in rows if r["event"] == "offline_fallback"] == [
        {"event": "offline_fallback", "why": "net_state", "category": None}]
    assert page.frames_of("ready")[0]["offline"] is True


def test_provider_offline_in_the_url_needs_no_key(monkeypatch, rows, offline_on, no_probe):
    """How the owner tries the backup without pulling the cable."""
    monkeypatch.setattr(asr, "engine", lambda: FakeEngine())
    page = _Page(hold_s=0.2)
    from app import session as session_module

    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "idle_timeout_s", 0)
    monkeypatch.setattr(settings, "canva_url", "")
    asyncio.run(asyncio.wait_for(session_module.handle_connection(page, provider="offline"), 10))
    assert not page.frames_of("error")
    assert page.frames_of("ready")[0]["offline"] is True


def test_a_good_dial_resets_the_failure_count(monkeypatch, rows, offline_on, no_probe):
    from app.providers.base import ProviderEvent

    net_health.note_dial_failure("network")
    page = _Page()
    _call(monkeypatch, _Gemini(script=[ProviderEvent(kind="turn_complete")]), page)
    assert net_health.health._fails == 0
    ready = page.frames_of("ready")
    assert ready[0]["provider"] == "gemini" and "offline" not in ready[0]


# ---- the link comes back ----------------------------------------------------

def _quick_watch(monkeypatch):
    from app.session import VoiceSession

    monkeypatch.setattr(VoiceSession, "OFFLINE_RETURN_QUIET_S", 0.2)
    monkeypatch.setattr(VoiceSession, "OFFLINE_WATCH_S", 0.05)
    monkeypatch.setattr(VoiceSession, "OFFLINE_RECHECK_S", 0.05)


def test_the_offline_call_ends_at_a_quiet_moment_once_the_link_answers(monkeypatch, rows, offline_on):
    monkeypatch.setattr(asr, "engine", lambda: FakeEngine())
    _quick_watch(monkeypatch)
    calls = []

    async def probe(*a, **k):
        calls.append(1)
        # the first probe is the dial's question ("is it the network?"): no
        return (len(calls) > 1, "probe_ok" if len(calls) > 1 else "dns")

    monkeypatch.setattr(net_health, "probe", probe)
    page = _Page(hold_s=None)                     # the page never hangs up itself
    _call(monkeypatch, _Gemini(fail=ProviderError("Could not open: 1011 None.")), page)
    assert page.frames_of("ready")[0]["offline"] is True
    assert page.frames_of("idle_timeout"), "ended the way an idle call ends"
    assert [r["reason"] for r in rows if r["event"] == "session_end"] == ["network_back"]
    ended = [r for r in rows if r["event"] == "offline_end"]
    assert len(ended) == 1 and ended[0]["reason"] == "network_back"


def test_while_the_state_says_offline_the_prober_decides(monkeypatch, rows, offline_on):
    """OFFLINE in the hysteresis and a background prober running: the call
    does not ask on its own -- it ends only after the state is ONLINE (two
    probe answers in a row), and then at a quiet moment."""
    monkeypatch.setattr(asr, "engine", lambda: FakeEngine())
    monkeypatch.setattr(settings, "net_probe_s", 15.0)
    _quick_watch(monkeypatch)
    calls = _probe_says(monkeypatch, True)
    # the prober's own two failures (a real outage); two failed DIALS would
    # also start the dial back-off, which keeps the call whatever the probe
    # says (test_a_link_that_answers_probes_but_fails_every_dial_stops_dialling)
    net_health.health.failure("dns")
    net_health.health.failure("dns")
    page = _Page(hold_s=None)

    async def main():
        from app import session as session_module

        task = asyncio.create_task(session_module.handle_connection(page))
        await asyncio.sleep(0.6)
        assert not task.done(), "still offline: the call goes on"
        assert calls == [], "the call itself did not probe"
        net_health.health.probe_ok()
        net_health.health.probe_ok()              # the prober's two answers
        assert not net_health.health.offline()
        await asyncio.wait_for(task, 5)

    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(settings, "idle_timeout_s", 0)
    monkeypatch.setattr(settings, "canva_url", "")
    asyncio.run(main())
    assert [r["reason"] for r in rows if r["event"] == "session_end"] == ["network_back"]
    assert calls == [True], "one confirming probe at the quiet moment"


def _handshake_timeout() -> ProviderError:
    """websockets' own words for a dial that never got its upgrade answered."""
    try:
        raise TimeoutError("timed out during opening handshake")
    except TimeoutError as cause:
        try:
            raise ProviderError("Could not open a Gemini Live session: %s" % cause) from cause
        except ProviderError as wrapped:
            return wrapped


def test_a_link_that_answers_probes_but_fails_every_dial_stops_dialling(monkeypatch, rows, offline_on):
    """Review 26 Oct (reproduced): DNS + TLS answer but the Live upgrade
    fails every time (a proxy resetting the websocket, a link too slow for
    the handshake). Every probe success used to reset the failure count, so
    the state never left ONLINE: each call dialled for 10-15 s deaf, fell
    back, was ended by the watcher ~10 s later, and the standing line dialled
    again -- all day. Two failed DIALS in a row now hold the dial off (and
    keep the backup call up) whatever the probe says."""
    monkeypatch.setattr(asr, "engine", lambda: FakeEngine())
    _quick_watch(monkeypatch)
    probes = _probe_says(monkeypatch, True)
    gemini = _Gemini(fail=_handshake_timeout())
    for _ in range(3):
        page = _Page(hold_s=0.6)
        _call(monkeypatch, gemini, page)
        assert page.frames_of("ready")[0]["offline"] is True
    assert gemini.entered == 2, "the third call must not dial a link that failed two dials"
    assert [r["why"] for r in rows if r["event"] == "offline_fallback"] == ["dial", "dial", "dial_backoff"]
    ends = [r["reason"] for r in rows if r["event"] == "session_end"]
    assert ends[1:] == ["browser_closed", "browser_closed"], \
        "a backup call inside the back-off is not ended because a probe answered"
    assert [r for r in rows if r["event"] == "dial_backoff"][0]["streak"] == 2
    assert probes, "the probe still runs; it just does not prove the dial"


def test_a_good_dial_ends_the_back_off(monkeypatch, rows, offline_on, no_probe):
    from app.providers.base import ProviderEvent

    net_health.note_dial_failure("network")
    net_health.note_dial_failure("network")
    assert net_health.dial_backoff_left() > 0
    net_health.health.probe_ok()
    net_health.health.probe_ok()                   # the prober: ONLINE again ...
    assert net_health.dial_backoff_left() > 0, "... which is not a dial getting through"
    net_health.health.dial_backoff_until = 0.0     # the back-off ran out: the next call dials
    page = _Page()
    _call(monkeypatch, _Gemini(script=[ProviderEvent(kind="turn_complete")]), page)
    assert page.frames_of("ready")[0]["provider"] == "gemini"
    assert net_health.health.dial_fails == 0 and net_health.dial_backoff_left() == 0


def test_a_guest_mid_turn_keeps_the_offline_call(monkeypatch):
    """Quiet means nobody speaking, nothing owed, her voice done."""
    import time

    from app.session import VoiceSession

    session = VoiceSession(_Page())
    session._play_until = 0.0
    now = time.monotonic()
    session._last_heard_at = now - 30
    assert session._offline_quiet_s(now) >= 29
    session._play_until = now + 2                 # her clip still playing
    assert session._offline_quiet_s(now) is None
    session._play_until = 0.0
    session._last_heard_at = now - 1              # the guest just spoke
    assert session._offline_quiet_s(now) < VoiceSession.OFFLINE_RETURN_QUIET_S


def test_an_asked_for_offline_call_is_not_ended_by_the_link(monkeypatch, rows, offline_on, no_probe):
    """`?provider=offline` is somebody trying the backup on purpose: the link
    being fine is no reason to hang up on them (and nothing is probed)."""
    monkeypatch.setattr(asr, "engine", lambda: FakeEngine())
    _quick_watch(monkeypatch)
    page = _Page(hold_s=0.8)
    _call(monkeypatch, _Gemini(), page, provider_name="offline")
    assert page.frames_of("ready")[0]["offline"] is True
    assert not page.frames_of("idle_timeout")
    assert [r["reason"] for r in rows if r["event"] == "session_end"] == ["browser_closed"]
    assert [r["why"] for r in rows if r["event"] == "offline_fallback"] == ["asked"]


# ---- the face: an honest word in the pill -----------------------------------

def test_the_face_says_offline_mode_in_its_status_pill():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to run the face's status pill code -- the "
                    "'Offline mode' mark is NOT being checked on this machine")
    script = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const html = fs.readFileSync('client/voice-preview.html', 'utf8');
const start = html.indexOf('    var appStatus = {');
const end = html.indexOf('    // The reply line shows for every answer');
assert.ok(start > 0 && end > start, 'status block found');
const c = vm.createContext({ appShell: true, uiLang: 'en', window: {}, statusNode: { textContent: '' } });
vm.runInContext(html.slice(start, end), c);
c.status('Listening...');
assert.equal(c.statusNode.textContent, 'Listening...', 'an online call: unchanged');
c.offlineCall = true;
c.status('Listening...');
assert.equal(c.statusNode.textContent, 'Listening... · Offline mode');
c.status(c.statusNode.textContent);
assert.equal(c.statusNode.textContent, 'Listening... · Offline mode', 'never twice');
assert.equal(c.lastStatusKey, 'Listening... · Offline mode');
c.status('Speaking...');
assert.equal(c.lastStatusKey, 'Speaking...', 'the key stays the state, the mark is paint');
c.uiLang = 'th';
c.status('Listening...');
assert.equal(c.statusNode.textContent, 'กำลังฟัง… · โหมดออฟไลน์');
c.offlineCall = false;
c.status('Ready');
assert.equal(c.statusNode.textContent, 'พร้อมคุยแล้ว');

// the ready frame sets it
const handler = html.slice(html.indexOf('    function onMessage(msg)'), html.indexOf('    // ---- dial / hang up ----'));
const said = [];
const d = vm.createContext({
  offlineCall: false, lastStatusKey: 'Connecting...', appShell: true, window: {},
  inputRate: 16000, outputRate: 24000, providerName: 'gemini', halfDuplex: false, bargeIn: false,
  bargeGate: null, holdSpeech: false, holdFloor: 0.04, holdMaxBytes: 0, HOLD_MAX_S: 8,
  modeId: 'condo', choiceSet: null, quickAsks: null, ArrayBuffer, performance: { now: () => 1 },
  paintMode() {}, dropHeldSpeech() {}, renderQuick() {}, startMic() {}, show() {},
  status(text) { said.push(text); },
});
vm.runInContext(handler, d);
d.onMessage({ data: JSON.stringify({ type: 'ready', provider: 'offline', offline: true, input_rate: 16000, output_rate: 24000 }) });
assert.equal(d.offlineCall, true);
assert.deepEqual(said, ['Connecting...'], 'repainted with the mark at once');
d.onMessage({ data: JSON.stringify({ type: 'ready', provider: 'gemini', input_rate: 16000, output_rate: 24000 }) });
assert.equal(d.offlineCall, false, 'an online ready clears it');
console.log('PASS');
"""
    result = subprocess.run([node, "-e", script], cwd=ROOT, capture_output=True,
                            encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr or result.stdout
    html = PAGE.read_text(encoding="utf-8")
    # cleared with the call: a dial, the close, a hang-up
    assert "manualEnd = false; inCall = true; callError = false; offlineCall = false;" in html
    onclose = html[html.index("ws.onclose = function () {"):]
    onclose = onclose[:onclose.index("function endCall()")]
    assert "inCall = false; offlineCall = false;" in onclose
    end_call = html[html.index("function endCall() {"):]
    assert "inCall = false; offlineCall = false;" in end_call[:200]
    for key, th, zh in (("Offline mode", "โหมดออฟไลน์", "离线模式"),):
        assert "'%s': '%s'" % (key, th) in html and "'%s': '%s'" % (key, zh) in html


# ---- the server: the prober and /health -------------------------------------

def test_health_shows_the_link_only_with_the_switch_on(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    assert "network" not in client.get("/health").json(), "the sales room's /health is unchanged"
    monkeypatch.setattr(settings, "offline_fallback", True)
    net_health.note_dial_failure("network")
    net_health.note_dial_failure("network")
    network = client.get("/health").json()["network"]
    assert network["enabled"] is True and network["state"] == "offline"
    assert network["reason"] == "dial_network", "a category, never an exception's text"


def test_the_startup_hook_starts_the_prober_only_with_the_switch_on(monkeypatch):
    from app import main

    _probe_says(monkeypatch, True)

    async def boot():
        await main._watch_the_network()
        started = net_health._task
        await main._stop_watching_the_network()
        return started

    assert asyncio.run(boot()) is None, "off: no probe, no task"
    monkeypatch.setattr(settings, "offline_fallback", True)
    monkeypatch.setattr(settings, "net_probe_s", 15.0)
    started = asyncio.run(boot())
    assert started is not None and net_health._task is None, "started, then stopped at shutdown"
