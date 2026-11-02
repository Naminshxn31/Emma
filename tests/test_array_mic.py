"""ARRAY_MIC: the robot app as the mic array's ONLY owner (docs/emma-array-mic-2026-11-02.md).

Owner, 2 Nov 2026: "ต้องแก้แอปให้ถือไมค์เองและทำเส้นเสียงใหม่". The measured
blocker of 10 Oct -- the Bothlent array gives Android ONE capture stream; app
first = the page's mic reads exact zeros (Emma deaf), page first = the app
gets 2 of 8 channels -- is solved by never having both: the page runs a
silent source on its own clock and the server puts the array's audio in each
of its frames, and a dead array hands the mic back to the page (after the app
let go). These tests drive the real tap, the real session methods, the real
/ws/robot-doa endpoint and the page's real JavaScript (extracted, run in
node against fakes) -- nothing reaches a robot.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest

from app import array_audio, doa
from app.config import settings
from tests.test_head_follow import Face, Scene, on, tick  # noqa: F401  (`on` is a fixture)

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "client" / "voice-preview.html"
SRC = ROOT / "apps" / "emma-robot-android" / "app" / "src" / "main" / "java" / "com" / "emma" / "robot" / "control"
FACE_UA = "Mozilla/5.0 (Linux; Android 15) wv EmmaRobot/0.1 EmmaBridge/1 EmmaArrayMic/1"
OLD_FACE_UA = "Mozilla/5.0 (Linux; Android 15) wv EmmaRobot/0.1 EmmaBridge/1"
LINK_UA = "EmmaRobot/0.1 EmmaArray/1 EmmaArrayMic/1"
OLD_LINK_UA = "EmmaRobot/0.1 EmmaArray/1"
TOKEN = "array-mic-token"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    doa.reset_state()
    array_audio.reset_state()
    monkeypatch.setattr(doa, "_calibration_path", lambda: tmp_path / "doa_calibration.json")
    monkeypatch.setattr(array_audio, "_capture_dir", lambda: tmp_path / "array_capture8")
    yield
    array_audio.reset_state()
    doa.reset_state()


@pytest.fixture
def sole_on(monkeypatch):
    monkeypatch.setattr(settings, "doa_enabled", True)
    monkeypatch.setattr(settings, "array_audio", True)
    monkeypatch.setattr(settings, "array_mic", True)


class _Link:
    """What doa.serve_link registers with array_audio for an app link."""

    def __init__(self, sole=True, state="capturing"):
        self.status = {"state": state}
        self.device = "Bothlent UAC Dongle"
        self.sole = sole
        self.released = False
        self.sent: list[dict] = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


class _S:
    """A session as sole_offer sees it: a socket with headers and the call URL's query."""

    def __init__(self, agent, query=None):
        self.ws = type("WS", (), {"headers": {"user-agent": agent},
                                  "query_params": {"array_mic": "1"} if query is None else query})()


def _array(n=256, value=1000) -> bytes:
    return np.full(n, value, "<i2").tobytes()


SILENT = bytes(640)          # one 20 ms page frame of the silent source


# ==================== the switch, the marker, byte-identical when off ====================


def test_switch_off_everything_is_as_before(monkeypatch):
    """ARRAY_MIC off (the default), even with DOA + ARRAY_AUDIO on and the
    new APK on both sockets: doa_ready, the call's `ready` and the tap are
    exactly what they were."""
    monkeypatch.setattr(settings, "doa_enabled", True)
    monkeypatch.setattr(settings, "array_audio", True)
    assert json.dumps(doa.ready_frame(LINK_UA)) == json.dumps(doa.ready_frame())
    assert "array_mic" not in doa.ready_frame(LINK_UA)
    kw = dict(input_rate=16000, barge_in=False, hold_speech=False)
    assert array_audio.sole_offer(_S(FACE_UA), **kw) is None
    from app.session import VoiceSession

    session = VoiceSession.__new__(VoiceSession)
    session.ws = _S(FACE_UA).ws
    provider = type("P", (), {"input_sample_rate": 16000})()
    assert asyncio.run(session._array_mic_offer(provider, False, False)) is None
    session._open_array_and_enhancer(provider, False, False, mic_offer=None)
    assert isinstance(session._array_tap, array_audio.Tap) and not session._array_tap.sole
    # and everything off: the literal frame of tests/test_array_audio.py
    monkeypatch.setattr(settings, "doa_enabled", False)
    monkeypatch.setattr(settings, "array_audio", False)
    assert json.dumps(doa.ready_frame(LINK_UA)) == json.dumps(
        {"type": "doa_ready", "rate": 16000, "frame": 1024, "channels": [0, 1, 2, 3], "max_lag": 16.0,
         "gate_rms": 0.004, "max_fps": 16, "band": [200, 4000], "stats_s": 2.0})


def test_it_needs_every_switch_and_the_marker_on_both_sockets(monkeypatch, caplog):
    kw = dict(input_rate=16000, barge_in=False, hold_speech=False)
    monkeypatch.setattr(settings, "array_mic", True)
    assert not array_audio.sole_enabled() and "DOA_ENABLED and ARRAY_AUDIO" in caplog.text
    monkeypatch.setattr(settings, "doa_enabled", True)
    assert not array_audio.sole_enabled()                       # the stream switch too
    monkeypatch.setattr(settings, "array_audio", True)
    assert array_audio.sole_enabled()
    assert doa.ready_frame(LINK_UA)["array_mic"] == {"version": 1}
    assert "array_mic" not in doa.ready_frame(OLD_LINK_UA)      # an old app is never asked
    assert array_audio.sole_offer(_S(FACE_UA), **kw) == ""
    assert array_audio.sole_offer(_S(OLD_FACE_UA), **kw) is None        # its page keeps its mic
    assert array_audio.sole_offer(_S("Mozilla/5.0 Chrome/130 " + array_audio.MARKER), **kw) is None
    assert array_audio.sole_offer(_S(FACE_UA), input_rate=24000, barge_in=False, hold_speech=False) == "input_rate"
    assert array_audio.sole_offer(_S(FACE_UA), input_rate=16000, barge_in=True, hold_speech=False) == "barge_in"
    assert array_audio.sole_offer(_S(FACE_UA), input_rate=16000, barge_in=False, hold_speech=True) == "hold_speech"
    # a page whose JS predates ARRAY_MIC (no array_mic=1 in its call URL): keep its mic
    assert array_audio.sole_offer(_S(FACE_UA, query={}), **kw) == "old_page"
    monkeypatch.setattr(settings, "multi_session", True)
    assert not array_audio.sole_enabled() and "array_mic" not in doa.ready_frame(LINK_UA)


def test_the_marker_is_one_string_in_the_server_the_page_and_both_user_agents():
    """Four copies of one seam: the APK's WebView user agent, its link's, the
    page's regex and the server's constant. P7's lesson (EmmaBridge/1): every
    package green and the feature could never switch on."""
    import re

    face = (SRC / "EmmaFace.java").read_text(encoding="utf-8")
    suffix = re.findall(r'setUserAgentString\(settings\.getUserAgentString\(\)\s*\+\s*"([^"]*)"\)', face)
    assert len(suffix) == 1 and array_audio.MARKER in suffix[0].split()
    listener = (SRC / "ArrayListener.java").read_text(encoding="utf-8")
    link_ua = re.search(r'static final String USER_AGENT = "([^"]*)";', listener).group(1)
    assert array_audio.MARKER in link_ua.split() and "EmmaRobot/" in link_ua
    page = PAGE.read_text(encoding="utf-8")
    call = re.search(r"var ARRAY_MIC_APP = appShell && !mirror && (.*?);\n", page, re.S).group(1)
    ua = "Mozilla/5.0 (Linux; Android 15; ZC-3588A) AppleWebKit/537.36 wv" + suffix[0]
    for rx in re.findall(r"/((?:[^/\\]|\\.)+)/\.test\(UA_ARRAY\)", call):
        assert re.search(rx.replace("\\/", "/"), ua), rx
    assert re.search(r"EmmaArrayMic\\/1\\b", call)
    bridge = re.search(r"var ARRAY_MIC = ON_ROBOT && (.*);", page).group(1)
    assert "EmmaArrayMic\\/1\\b" in bridge


# ==================== the link ====================


def _client(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(settings, "ws_token", TOKEN)
    monkeypatch.setattr(settings, "doa_enabled", True)
    monkeypatch.setattr(settings, "array_audio", True)
    monkeypatch.setattr(settings, "array_mic", True)
    return TestClient(app)


def test_an_old_apk_gets_doa_off_so_it_never_takes_the_array_from_the_page(monkeypatch):
    client = _client(monkeypatch)
    with client.websocket_connect("/ws/robot-doa?token=" + TOKEN, headers={"user-agent": OLD_LINK_UA}) as ws:
        assert ws.receive_json() == {"type": "doa_off"}
    assert array_audio._STREAM.link is None
    with client.websocket_connect("/ws/robot-doa?token=" + TOKEN, headers={"user-agent": LINK_UA}) as ws:
        ready = ws.receive_json()
        assert ready["type"] == "doa_ready" and ready["array_mic"] == {"version": 1}
        link = array_audio._STREAM.link
        assert link is not None and link.sole is True and callable(link.send_text)
        # the app's "released" status is a state the server knows, not malformed
        ws.send_text(json.dumps({"type": "doa_status", "state": "released", "detail": "page"}))
        deadline = time.monotonic() + 3
        while link.status.get("state") != "released" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert link.status["state"] == "released" and link.counts["malformed"] == 0
        assert array_audio.sole_state(time.monotonic()) == (False, "released")


def test_release_goes_down_the_link_once_and_the_stream_stays_released(sole_on):
    link = _Link()
    array_audio.link_opened(link)
    assert asyncio.run(array_audio.request_release("stale")) is True
    assert asyncio.run(array_audio.request_release("stale")) is True
    assert link.sent == [{"type": "array_release", "why": "stale"}]
    assert array_audio.sole_state(time.monotonic()) == (False, "released")
    array_audio.link_closed(link)
    assert asyncio.run(array_audio.request_release("x")) is False      # no link: nothing to tell


def test_sole_state_wants_a_live_recovered_stream(monkeypatch, sole_on):
    now = time.monotonic()
    assert array_audio.sole_state(now) == (False, "no_link")
    array_audio.link_opened(_Link(sole=False))
    assert array_audio.sole_state(now) == (False, "old_app")
    link = _Link()
    array_audio.link_opened(link)
    assert array_audio.sole_state(now) == (False, "stale")
    array_audio._beam(link, 0, _array(), now)
    assert array_audio.sole_state(now) == (False, "recovering")
    monkeypatch.setattr(array_audio, "RECOVER_S", 0.0)
    assert array_audio.sole_state(now) == (True, "live")
    link.status = {"state": "stopped"}
    assert array_audio.sole_state(now) == (False, "not_capturing")


# ==================== the tap: the silent clock, the array, the hand-back ====================


def run_sole(*, seconds=3.0, array_down=(), array_zero=(), page_gaps=(), speaking=lambda t: False,
             audible_until=lambda t: None, verified=True, deaf_s=None, monkeypatch=None):
    """The page's silent frames every 20 ms, the array's chunks every 16 ms
    (each sample = -(its global index // 2) - 1: never zero, says the moment
    to 1/8000 s without wrapping for 4 s),
    through one sole Tap. -> (list of (t, page, out), tap, handbacks)."""
    if deaf_s is not None:
        monkeypatch.setattr(settings, "array_mic_deaf_s", deaf_s)
    link = _Link()
    array_audio.link_opened(link)
    tap = array_audio.Tap(sole=True)
    handbacks: list[str] = []
    tap.on_handback = handbacks.append
    events = []
    t = 0.0
    while t < seconds:
        if not any(a <= t < b for a, b in page_gaps):
            events.append((t + 0.001, "page", t))
        t += 0.02
    t = 0.0
    while t < seconds:
        if not any(a <= t < b for a, b in array_down):
            events.append((t, "array", t))
        t += 0.016
    events.sort(key=lambda e: e[0])
    base, seq, out = 100.0, 0, []
    for at, kind, t in events:
        now = base + at
        if kind == "array":
            idx = int(round(t * 16000))
            if any(a <= t < b for a, b in array_zero):
                chunk = bytes(512)
            else:
                chunk = (-(np.arange(idx, idx + 256) // 2) - 1).astype("<i2").tobytes()
            array_audio._beam(link, seq, chunk, now)
            seq += 1
        else:
            until = audible_until(t)
            got = tap.frame(SILENT, now, speaking=speaking(t), in_speech=True,
                            audible_until=None if until is None else base + until, verified=verified)
            out.append((t, got))
    return out, tap, handbacks


def _moments(chunk: bytes) -> np.ndarray:
    v = np.frombuffer(chunk, "<i2").astype(np.int64)
    return (-v - 1) * 2 / 16000.0


def test_every_silent_frame_carries_the_array_and_the_silence_never_goes_up(sole_on):
    out, tap, handbacks = run_sole()
    sent = [got for t, got in out if got]
    assert sent and all(np.frombuffer(g, "<i2").min() < 0 for g in sent), "only the array's audio"
    assert not any(got is SILENT for t, got in out)
    assert tap.mode == "array" and tap.why == "sole" and handbacks == []
    # continuous, never a moment twice
    moments = np.concatenate([_moments(g) for g in sent])
    assert (np.diff(moments) >= 0).all() and moments[-1] - moments[0] > 2.5


def test_nothing_from_the_array_while_emma_may_be_heard(sole_on):
    """The array has no echo canceller: while the server's clock says she may
    be audible, the silent frame goes up and what the array heard then is
    dropped, never forwarded later."""
    out, tap, _ = run_sole(speaking=lambda t: 1.0 <= t < 2.0)
    during = [got for t, got in out if 1.0 <= t < 2.0]
    assert during and all(got is SILENT for got in during)
    assert tap.counts["speaking"] == len(during)
    for t, got in out:
        if got and got is not SILENT:
            m = _moments(got)
            assert not ((m >= 1.0 + 0.02) & (m < 2.0 - 0.02)).any(), t


def test_half_duplex_the_echo_tail_counts_from_the_pages_resume(monkeypatch, sole_on):
    """The page sends nothing while she plays (half-duplex) and resumes after
    its own playback + 350 ms; the tail is counted again from that resume --
    silent frames for ARRAY_AUDIO_ECHO_TAIL_S, then the array."""
    monkeypatch.setattr(settings, "array_audio_echo_tail_s", 0.25)
    out, _, _ = run_sole(page_gaps=[(1.0, 1.6)], audible_until=lambda t: 1.45 if t >= 1.0 else None)
    window = [got for t, got in out if 1.6 <= t < 1.84]
    assert window and all(got is SILENT for got in window)
    later = [got for t, got in out if t >= 1.86 and got]
    assert later and all(got is not SILENT for got in later)
    for got in later:
        assert (_moments(got) >= 1.6 + 0.25 - 0.04).all()


def test_a_dead_array_is_a_hole_then_one_handback_and_never_the_array_again(monkeypatch, sole_on):
    """Dies at 1.0 s, comes back at 2.0 s. Up to ARRAY_MIC_DEAF_S the silent
    frames go up (a hole -- there is no second microphone); at the deaf mark
    one hand-back (callback + row), and from then on every frame is the
    page's own, even when the array returns: the app let go for its run."""
    rows = []
    monkeypatch.setattr(array_audio, "_record", lambda event, **f: rows.append((event, f)))
    out, tap, handbacks = run_sole(array_down=[(1.0, 2.0)], deaf_s=0.5, monkeypatch=monkeypatch)
    assert handbacks == ["stale"]
    [(_, row)] = [r for r in rows if r[0] == "array_mic_handback"]
    assert row["phase"] == "call" and row["why"] == "stale" and row["scope"] == "call"
    assert 500 <= row["dead_ms"] < 600
    hole = [got for t, got in out if 1.0 + 0.13 <= t < 1.5]
    assert hole and all(got is SILENT for got in hole)
    after = [got for t, got in out if t >= 1.55]
    assert after and all(got is SILENT for got in after), "the page's frame, whatever the array does"
    assert tap.handed_back and not tap.sole and tap.mode == "page"
    assert [r[1]["source"] for r in rows if r[0] == "array_source"] == ["array", "none", "page"]


def test_two_short_holes_with_her_turn_between_are_not_one_long_one(monkeypatch, sole_on):
    """Deaf is measured from the last chunk that carried sound, not from the
    first bad frame: a hole, her turn (no frames, the array fine), a second
    hole -- each under ARRAY_MIC_DEAF_S -- is no hand-back."""
    out, tap, handbacks = run_sole(array_down=[(1.0, 1.3), (2.0, 2.3)], page_gaps=[(1.3, 2.0)],
                                   deaf_s=0.5, monkeypatch=monkeypatch)
    assert handbacks == [] and not tap.handed_back
    assert any(got is not SILENT and got for t, got in out if t >= 2.5)


def test_exact_zeros_from_the_array_are_deafness_too(monkeypatch, sole_on):
    out, tap, handbacks = run_sole(array_zero=[(1.0, 9.0)], deaf_s=1.0, monkeypatch=monkeypatch)
    assert handbacks == ["zeros"]
    assert all(got is SILENT for t, got in out if t >= 2.05)


def test_an_unverified_page_gets_no_array_audio_and_hands_back_to_itself(monkeypatch, sole_on):
    out, tap, handbacks = run_sole(verified=False, deaf_s=0.5, monkeypatch=monkeypatch)
    assert handbacks == ["not_robot_face"]
    assert all(got is SILENT for t, got in out)


def test_the_sole_report_says_so_and_carries_the_arrays_levels(monkeypatch, sole_on):
    rows = []
    monkeypatch.setattr(array_audio, "_record", lambda event, **f: rows.append((event, f)))
    monkeypatch.setattr(settings, "array_audio_gain", 2.0)
    out, tap, _ = run_sole(seconds=1.0)
    tap.report()
    row = [r for r in rows if r[0] == "array_report"][-1][1]
    assert row["sole"] is True and row["handed_back"] is False and row["gain"] == 2.0
    assert row["array_rms"] and row["sent_rms"] and row["sent_rms"] > row["array_rms"]
    # an ARRAY_AUDIO tap's row has no new field
    rows.clear()
    array_audio.Tap().report()
    assert "sole" not in rows[-1][1]


# ==================== the session ====================


class _Provider:
    input_sample_rate = 16000
    output_sample_rate = 24000

    def __init__(self):
        self.sent: list = []

    async def send_audio(self, data):
        self.sent.append(data)


class _WS:
    def __init__(self, frames=(), agent=FACE_UA):
        self.headers = {"user-agent": agent}
        self.query_params = {"array_mic": "1"}
        self._frames = list(frames)
        self.texts: list[dict] = []

    async def receive(self):
        if self._frames:
            return {"type": "websocket.receive", "bytes": self._frames.pop(0)}
        await asyncio.sleep(0.05)       # let a hand-back's task send before the page goes
        return {"type": "websocket.disconnect"}

    async def send_text(self, text):
        self.texts.append(json.loads(text))


def _session(frames=(), agent=FACE_UA):
    from app.session import VoiceSession

    s = VoiceSession.__new__(VoiceSession)
    s.ws = _WS(frames, agent)
    s.provider = _Provider()
    s._ears_closed_until = None
    s._greeting_turn_seen = False
    return s


def test_ready_says_true_for_a_live_array_and_the_call_gets_a_sole_tap(monkeypatch, sole_on):
    monkeypatch.setattr(array_audio, "RECOVER_S", 0.0)
    link = _Link()
    array_audio.link_opened(link)
    array_audio._beam(link, 0, _array(), time.monotonic())
    s = _session()
    offer = asyncio.run(s._array_mic_offer(s.provider, False, False))
    assert offer == ""
    s._open_array_and_enhancer(s.provider, False, False, mic_offer=offer)
    assert s._array_tap.sole and s._array_tap.on_handback == s._array_handback
    assert link.sent == []


def test_ready_waits_for_the_array_then_the_page_holds_it_for_this_call_only(monkeypatch, sole_on):
    """Not live at `ready`: waited for ARRAY_MIC_START_WAIT_S (an app that
    resumed a moment ago is still opening it), then `ready` says false with
    the reason and the call has no tap. "stale" is a moment, not a verdict
    on the run (lead's review, 2 Nov): NO array_release -- the page holds the
    app off for this call through its bridge and frees it afterwards."""
    rows = []
    monkeypatch.setattr("app.turnlog.record", lambda event, **f: rows.append((event, f)))
    monkeypatch.setattr(settings, "array_mic_start_wait_s", 0.2)
    link = _Link()
    array_audio.link_opened(link)                                    # linked, nothing arriving
    s = _session()
    started = time.monotonic()
    offer = asyncio.run(s._array_mic_offer(s.provider, False, False))
    assert offer == "stale" and time.monotonic() - started >= 0.2
    assert link.sent == [], "a transient reason must not latch the app for its run"
    assert ("array_mic_handback", {"phase": "start", "why": "stale", "scope": "call"}) in rows
    s._open_array_and_enhancer(s.provider, False, False, mic_offer=offer)
    assert s._array_tap is None


@pytest.mark.parametrize("reason", ["no_link", "not_capturing", "stale", "zeros", "recovering"])
def test_a_transient_reason_holds_for_the_call(monkeypatch, sole_on, reason):
    """The robot booting or any server restart: the app's link still backs off
    (2/4/8/16/32 s) when the first standing-line call rings -- no_link. That
    used to latch the array off for the day, silently."""
    async def not_live(timeout):
        return False, reason
    monkeypatch.setattr(array_audio, "wait_sole", not_live)
    link = _Link()
    array_audio.link_opened(link)
    s = _session()
    assert asyncio.run(s._array_mic_offer(s.provider, False, False)) == reason
    assert array_audio.offer_scope(reason) == "call" and link.sent == [] and not link.released


@pytest.mark.parametrize("reason", sorted(array_audio.FINAL_REASONS))
def test_a_final_reason_lets_go_for_the_run(monkeypatch, sole_on, reason):
    async def not_live(timeout):
        return False, reason
    monkeypatch.setattr(array_audio, "wait_sole", not_live)
    link = _Link()
    array_audio.link_opened(link)
    s = _session()
    assert asyncio.run(s._array_mic_offer(s.provider, False, False)) == reason
    assert array_audio.offer_scope(reason) == "run"
    assert link.sent == [{"type": "array_release", "why": reason}]


def test_the_final_reasons_are_the_ones_the_lead_named():
    assert array_audio.FINAL_REASONS == {"old_app", "old_page", "released", "input_rate", "barge_in", "hold_speech"}


def test_a_released_or_old_app_is_not_waited_for(monkeypatch, sole_on):
    monkeypatch.setattr(settings, "array_mic_start_wait_s", 5.0)
    link = _Link()
    link.released = True
    array_audio.link_opened(link)
    s = _session()
    started = time.monotonic()
    assert asyncio.run(s._array_mic_offer(s.provider, False, False)) == "released"
    assert time.monotonic() - started < 0.5
    # barge-in / hold-speech: the page must still take its mic -- and the app let go first
    link.released = False
    assert asyncio.run(s._array_mic_offer(s.provider, True, False)) == "barge_in"
    assert link.sent == [{"type": "array_release", "why": "barge_in"}]


def test_the_pump_sends_the_array_in_place_of_silent_frames_then_hands_back(monkeypatch, sole_on):
    """End to end through _browser_to_provider: every silent frame becomes
    the array's audio (with ARRAY_AUDIO_GAIN); once the array has been dead
    for ARRAY_MIC_DEAF_S the page is told `array_mic_lost` and the silent
    frame itself goes up. Nothing goes to the app: the page holds it for the
    call through its bridge, and the app counts deaf holds per run."""
    from app import robot_face_link
    from app.session import VoiceSession

    monkeypatch.setattr(robot_face_link, "is_robot_face", lambda session: True)
    monkeypatch.setattr(settings, "array_audio_gain", 2.0)
    monkeypatch.setattr(settings, "array_audio_stale_ms", 5000)
    link = _Link()
    array_audio.link_opened(link)
    array_audio._beam(link, 0, np.full(320, 1000, "<i2").tobytes(), time.monotonic())
    s = _session([SILENT])
    s._open_array_and_enhancer(s.provider, False, False, mic_offer="")
    asyncio.run(VoiceSession._browser_to_provider(s))
    assert [np.frombuffer(x, "<i2").tolist() for x in s.provider.sent] == [[2000] * 320]
    # dead: the deaf mark has passed since the last chunk with sound
    monkeypatch.setattr(settings, "array_mic_deaf_s", 0.0)
    monkeypatch.setattr(array_audio, "live", lambda now: (False, "stale"))
    s2 = _session([SILENT, SILENT])
    s2._open_array_and_enhancer(s2.provider, False, False, mic_offer="")
    asyncio.run(VoiceSession._browser_to_provider(s2))
    assert s2.provider.sent == [SILENT, SILENT]
    assert s2.ws.texts == [{"type": "array_mic_lost", "why": "stale"}]
    assert link.sent == [], "a mid-call hand-back is the call's, not the run's"


def test_an_unverified_page_is_told_but_cannot_make_the_app_let_go(monkeypatch, sole_on):
    from app import robot_face_link
    from app.session import VoiceSession

    monkeypatch.setattr(robot_face_link, "is_robot_face", lambda session: False)
    monkeypatch.setattr(settings, "array_mic_deaf_s", 0.0)
    link = _Link()
    array_audio.link_opened(link)
    array_audio._beam(link, 0, _array(), time.monotonic())
    s = _session([SILENT])
    s._open_array_and_enhancer(s.provider, False, False, mic_offer="")
    asyncio.run(VoiceSession._browser_to_provider(s))
    assert s.provider.sent == [SILENT]                     # never the robot's array
    assert s.ws.texts == [{"type": "array_mic_lost", "why": "not_robot_face"}]
    assert link.sent == []


def test_mic_report_names_the_source_only_on_the_array(monkeypatch, sole_on):
    from app.session import VoiceSession

    rows = []
    monkeypatch.setattr("app.turnlog.record", lambda event, **f: rows.append((event, f)))
    monkeypatch.setattr(VoiceSession, "MIC_REPORT_S", 0.0)
    s = _session()
    s._array_tap = array_audio.Tap(sole=True)
    s._mic_report(_array(320))
    row = [f for e, f in rows if e == "mic_report"][-1]
    assert row["source"] == "array" and row["gain"] == 1.0
    s._mic_probe = None
    s._array_tap = None
    s._mic_report(_array(320))
    assert "source" not in [f for e, f in rows if e == "mic_report"][-1]


# ==================== direction: frames kept straight (the array does not turn) ====================


class _DoaFocus:
    MOUTH_MOVING = 0.35

    def __init__(self):
        self.observed = []
        self.snap = {}

    def observe_doa(self, t, bearing, conf):
        self.observed.append(bearing)

    def snapshot(self):
        return self.snap


def test_doa_learns_in_the_robots_frame_and_reports_in_the_cameras(monkeypatch):
    """A face 5 deg left in the head camera with the head turned 10 deg left
    is 15 deg left of the robot -- the array's frame, which does not turn.
    The pair learns 15; the bearing focus is handed (compared with faces in
    the camera) is the robot's minus the pan; no pair while the head moves;
    HEAD_FOLLOW off = every number unchanged."""
    from app import head_follow

    fake = _DoaFocus()
    monkeypatch.setattr(doa, "_focus", lambda: fake)
    monkeypatch.setattr(doa, "_truth_at", lambda t: 5.0)
    lags = [1.0, 2.0, 3.0, 1.0, 2.0, 1.0]
    strength = [0.9] * 6

    def learned(t):
        cal = doa.Calibration({"rate": 16000})
        doa._STATE.pair_last_t = doa._STATE.truth_try_t = -1e9
        _, _, info = doa.estimate(cal, t, lags, strength)
        return (cal.pairs[-1][1] if cal.pairs else None), info.get("pair")

    assert learned(100.0) == (5.0, "added")                        # HEAD_FOLLOW off
    monkeypatch.setattr(settings, "head_follow", True)
    head_follow._HEAD.update(pan_deg=10.0, tilt_deg=0.0, moved_at=50.0)
    assert learned(100.0) == (15.0, "added")
    head_follow._HEAD["moved_at"] = 99.5                           # turning right now
    assert learned(100.0) == (None, "head_moving")
    # focus gets the robot's bearing minus the pan
    head_follow._HEAD["moved_at"] = 50.0
    monkeypatch.setattr(doa, "estimate", lambda *a, **k: (20.0, 0.9, {"closure": 0.1}))
    link = doa._Link(LINK_UA)
    link.device = "Bothlent UAC Dongle"
    link._frame({"type": "doa", "t_app_ms": 1000, "lags": lags, "strength": strength, "rms": 0.01},
                time.monotonic())
    assert fake.observed == [pytest.approx(10.0)]
    monkeypatch.setattr(settings, "head_follow", False)
    link._frame({"type": "doa", "t_app_ms": 1100, "lags": lags, "strength": strength, "rms": 0.01},
                time.monotonic())
    assert fake.observed[-1] == pytest.approx(20.0)
    head_follow.reset()


def test_head_moved_at_changes_only_with_the_pose():
    from app import head_follow

    head_follow.reset()
    head_follow._set_head(0, 0)
    assert head_follow._HEAD["moved_at"] is None                    # centre -> centre: no move
    head_follow._set_head(40, 0)
    first = head_follow._HEAD["moved_at"]
    assert first is not None
    head_follow._set_head(40, 0)                                    # a hold re-send
    assert head_follow._HEAD["moved_at"] == first
    head_follow.reset()


def test_the_search_turns_toward_the_voice_only_with_head_follow_doa(on, monkeypatch):
    """HEAD_FOLLOW_DOA off (the default): a DOA bearing still turns nothing
    (tests/test_head_follow.py). On: the 'voice at the robot, no face' search
    turns the whole bearing toward it and looks down, once."""
    from app import head_follow

    scene = Scene(monkeypatch)
    face = Face()
    follow = head_follow.HeadFollow(face)
    scene.cue = {"bearing": 6.0, "source": "doa", "track": None}
    scene.tracks = []
    scene.voice = (102.0, 0.08)
    tick(follow, 102.5)
    assert face.ws.looks() == [(0, 30)], "off: the plain look-down search"
    monkeypatch.setattr(settings, "head_follow_doa", True)
    face2 = Face()
    follow2 = head_follow.HeadFollow(face2)
    tick(follow2, 102.5)
    # 6 deg over a 20 deg full span = 30 %, all of it (one shot), and down
    assert face2.ws.looks() == [(30, 30)]
    rows = [f for k, f in on if k == "head_look"]
    assert rows and rows[-1]["why"] == "doa"


# ==================== the page (its real JavaScript, in node) ====================


def _node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required: the page's ARRAY_MIC code was NOT run (the one-owner order, "
                    "the silent clock, the hand-back swap are unchecked beyond source text)")
    return node


def _between(text, start, end):
    a = text.index(start)
    return text[a:text.index(end, a)]


def _node_script(tmp_path, text):
    """A script FILE: Windows caps a command line at 32k characters."""
    path = tmp_path / "harness.cjs"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_the_silent_worklet_keeps_the_pages_20ms_clock_in_exact_zeros(tmp_path):
    node = _node()
    page = PAGE.read_text(encoding="utf-8")
    src = _between(page, "var SILENT_WORKLET_SRC = `", "registerProcessor('cap-silent', CapSilent);")
    src = src[len("var SILENT_WORKLET_SRC = `"):]
    script = """
const vm = require('vm');
const posted = [];
let Proc = null;
const ctx = vm.createContext({ sampleRate: 24000,
  AudioWorkletProcessor: class { constructor() { this.port = { postMessage: (m) => posted.push(m) }; } } });
vm.runInContext(%s + "\\nthis.P = CapSilent;", ctx);
const p = new ctx.P({ processorOptions: { targetRate: 16000 } });
// one second of render quanta: half with a 1-channel silent input, half with none
for (let k = 0; k < 376; k++) p.process(k %% 2 ? [[new Float32Array(128)]] : [[]]);
const frames = posted.map((b) => new Int16Array(b));
console.log(JSON.stringify({ n: frames.length, len: frames[0].length,
  zeros: frames.every((f) => f.every((x) => x === 0)) }));
""" % json.dumps(src)
    out = subprocess.run([node, _node_script(tmp_path, script)], capture_output=True, encoding="utf-8",
                         timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    # 376 quanta x 128 frames at 24 kHz = 2.005 s -> 100 frames of 320 samples (20 ms at 16 kHz;
    # at exactly 2 s float rounding leaves the 100th a hair short)
    assert got == {"n": 100, "len": 320, "zeros": True}


_HARNESS = r"""
const vm = require('vm');
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const code = html.slice(html.indexOf('    // The page\'s own call mic: one constraint object'),
                        html.indexOf('    // ---- server -> client'));
const scenario = JSON.parse(process.argv[3]);
const log = [];
let now = 0;
const timers = [];
class Node { constructor(kind, name) { this.kind = kind; this.name = name; this.port = {};
    // a worklet that ticks (scenario.ticks): one frame as soon as the page listens
    const port = this.port;
    Object.defineProperty(port, 'onmessage', { get() { return port._fn; }, set(fn) { port._fn = fn;
      if (fn && scenario.ticks) setImmediate(() => fn({ data: new ArrayBuffer(640) })); } }); }
  connect(n) { log.push('connect:' + this.kind + '->' + n.kind + (n.name ? ':' + n.name : '')); }
  disconnect() { log.push('disconnect:' + this.kind); } }
let states = scenario.states.slice();
const bridge = { arrayMic: {
  release() { log.push('release'); return scenario.releaseAnswer; },
  hold(kind) { log.push('hold:' + kind); return scenario.releaseAnswer; },
  free() { log.push('free'); return null; },
  state() { const s = states.length ? states.shift() : scenario.last; log.push('state:' + JSON.stringify(s)); return s; } } };
class Ctx { constructor() { this.state = 'running'; this.audioWorklet = { addModule: async (u) => { log.push('module'); } }; }
  resume() { return Promise.resolve(); }
  createConstantSource() { const n = new Node('constant'); n.offset = { value: 1 }; n.start = () => log.push('constant.start'); return n; }
  createMediaStreamSource(s) { return new Node('mic'); } }
const c = vm.createContext({
  window: { AudioContext: Ctx, EmmaRobotBridge: scenario.bridge ? bridge : undefined },
  navigator: { mediaDevices: { getUserMedia: async (c) => { log.push('getUserMedia'); return { getTracks: () => [] }; } } },
  URL: { createObjectURL: () => 'blob:x', revokeObjectURL() {} }, Blob: class {},
  AudioWorkletNode: class extends Node { constructor(ctx, name) { super('worklet', name); log.push('worklet:' + name); } },
  WebSocket: { OPEN: 1 }, ws: { readyState: 1, send: (m) => log.push('send:' + m) },
  performance: { now: () => now }, setTimeout: (fn, ms) => timers.push({ at: now + ms, fn }),
  WORKLET_SRC: 'W', SILENT_WORKLET_SRC: 'S', ARRAY_MIC_APP: scenario.app, ARRAY_RELEASE_WAIT_MS: 3000, ARRAY_CLOCK_CHECK_MS: 2000,
  arrayMicOffer: scenario.offer, arrayMicSilent: false, arrayMicHeld: false, mirror: false, audioCtx: null,
  micStream: null, micSource: null, worklet: null, playing: false, inputRate: 16000, outputRate: 24000,
  micNS: false, micCallBoost: 1, callAGC: () => false, thaiUi: () => false,
  buildMicChain: (ctx, src) => src, handleMicAudio() {}, armAudioUnlock() {}, startMirrorAudio() {},
  stopFailedCall() { log.push('stopFailedCall'); }, setMicLabel() {}, show() {}, status() {},
});
vm.runInContext(code, c);
(async () => {
  const flush = async () => { for (let i = 0; i < 20; i++) await new Promise((r) => setImmediate(r)); };
  const run = async (p) => {
    for (let guard = 0; guard < 200; guard++) {
      await flush();
      timers.sort((a, b) => a.at - b.at);
      const t = timers.shift();
      if (!t) break;
      now = t.at; t.fn();
    }
    await p;
  };
  await run(c.startMic());
  log.push('silent=' + c.arrayMicSilent);
  if (scenario.lose) { states = scenario.loseStates.slice(); await run(c.arrayMicLost()); log.push('silent=' + c.arrayMicSilent); }
  log.push('held=' + c.arrayMicHeld);
  console.log(JSON.stringify({ log, now }));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


_HARNESS_FILE: dict = {}


def _page_run(**scenario):
    node = _node()
    base = dict(app=True, offer=None, bridge=True, releaseAnswer=None, states=[], last=None,
                lose=False, loseStates=[], ticks=True)
    base.update(scenario)
    if "path" not in _HARNESS_FILE:
        import tempfile

        # one fixed file, rewritten per run (no temp folder left behind per run)
        path = Path(tempfile.gettempdir()) / "condo_voice_array_mic_page_harness.cjs"
        path.write_text(_HARNESS, encoding="utf-8")
        _HARNESS_FILE["path"] = str(path)
    out = subprocess.run([node, _HARNESS_FILE["path"], str(PAGE), json.dumps(base)],
                         capture_output=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _sends(log):
    return [json.loads(x[5:]) for x in log if x.startswith("send:")]


def test_page_on_the_array_opens_no_microphone():
    r = _page_run(offer=True)
    log = r["log"]
    assert "getUserMedia" not in log
    assert "worklet:cap-silent" in log and "connect:constant->worklet:cap-silent" in log
    assert log[-2:] == ["silent=true", "held=false"]
    assert _sends(log) == [{"type": "mic_state", "why": "array_start", "state": "array",
                            "label": "array", "muted": False, "handed": False}]


@pytest.mark.parametrize("scope, ask, said, held", [("call", "hold:start", "held", "held=true"),
                                                    ("run", "release", "released", "held=false")])
def test_page_takes_its_mic_only_after_the_apps_capture_ended(scope, ask, said, held):
    """`array_mic: false`: the app is asked to let go -- for this call (a
    transient reason: emma-face:mic:hold) or for its run (a final one:
    emma-face:mic:release) -- and getUserMedia waits for an explicit
    capturing=false: two captures of this array and the page reads zeros
    (measured 10 Oct). A call hold is marked so the call's teardown frees it."""
    still = {"capturing": True, "released": scope == "run"}
    gone = {"capturing": False, "released": scope == "run"}
    r = _page_run(offer=scope, releaseAnswer=still, states=[still, still, gone])
    log = r["log"]
    assert ask in log and ("release" in log) == (scope == "run") and ("hold:start" in log) == (scope == "call")
    assert log.index(ask) < log.index("state:" + json.dumps(gone, separators=(",", ":"))) < log.index("getUserMedia")
    assert "worklet:cap" in log and "worklet:cap-silent" not in log
    assert _sends(log)[0]["why"] == "array_start" and _sends(log)[0]["state"] == said
    assert log[-1] == held


def test_page_gives_up_waiting_after_the_bound_and_says_so():
    still = {"capturing": True, "released": False}
    r = _page_run(offer="call", releaseAnswer=still, states=[], last=still)
    log = r["log"]
    assert r["now"] >= 3000 and log.index("getUserMedia") > log.index("hold:start")
    assert _sends(log)[0]["state"] == "timeout"


def test_page_today_s_path_when_ready_says_nothing_or_the_apk_is_old():
    """No array_mic in `ready` -> today's path: the bridge is never touched.
    An APK without EmmaArrayMic/1 gets today's path even if a server says true."""
    for case in (dict(offer=None), dict(offer=True, app=False), dict(offer="call", app=False),
                 dict(offer="run", app=False)):
        log = _page_run(**case)["log"]
        assert log.index("getUserMedia") < log.index("module"), case
        assert not any(x == "release" or x.startswith(("state:", "hold:")) for x in log), case
        assert _sends(log) == [], case


def test_page_hand_back_swaps_the_silent_source_for_its_own_mic_after_the_release():
    """Mid-call: a DEAF hold for this call (the app counts those per run),
    then the page's mic -- after the app's capture ended."""
    gone = {"capturing": False, "released": False}
    still = {"capturing": True, "released": False}
    r = _page_run(offer=True, lose=True, releaseAnswer=still, loseStates=[still, gone])
    log = r["log"]
    lost = log[log.index("silent=true") + 1:]
    assert "release" not in lost
    assert lost.index("hold:deaf") < lost.index("getUserMedia")
    assert lost.index('state:{"capturing":false,"released":false}') < lost.index("getUserMedia")
    assert "disconnect:constant" in lost and "worklet:cap" in lost
    assert lost.index("getUserMedia") < lost.index("worklet:cap")
    assert log[-2:] == ["silent=false", "held=true"]
    assert [m["state"] for m in _sends(log)] == ["array", "held", "page"]


def test_page_wiring_ready_and_the_lost_message():
    page = PAGE.read_text(encoding="utf-8")
    # the call URL says this page's JS can go without its mic (sole_offer reads it)
    dial = page.split("function startCall() {", 1)[1].split("ws.binaryType", 1)[0]
    assert "+ (ARRAY_MIC_APP ? '&array_mic=1' : '')" in dial
    ready = page.split("        case 'ready':", 1)[1].split("break;", 1)[0]
    # one assignment before startMic (tests run this case alone, without the new names)
    assert ready.rstrip().endswith(
        "arrayMicOffer = evt.array_mic === true ? true\n"
        "            : (evt.array_mic === false ? (evt.array_mic_scope === 'run' ? 'run' : 'call') : null);\n"
        "          startMic();")
    lost = page.split("        case 'array_mic_lost':", 1)[1].split("break;", 1)[0]
    assert "arrayMicLost();" in lost
    # the harness's two bounds are the page's own
    assert "var ARRAY_RELEASE_WAIT_MS = 3000;" in page and "var ARRAY_CLOCK_CHECK_MS = 2000;" in page
    # the call's teardown forgets the silent source
    teardown = page.split("function releaseCallHardware() {", 1)[1].split("\n    }\n", 1)[0]
    assert "arrayMicSilent = false;" in teardown
    # never a mic from the array path: getUserMedia only in the two page-mic places
    engine = _between(page, "    // The page's own call mic: one constraint object", "    // ---- server -> client")
    assert engine.count("getUserMedia(pageMicConstraints())") == 2
    start = _between(engine, "async function startArrayMic(", "async function arrayMicLost(")
    assert "getUserMedia" not in start


def test_the_bridge_answer_is_whitelisted(tmp_path):
    node = _node()
    page = PAGE.read_text(encoding="utf-8")
    model = _between(page, "// <nav-model>", "// </nav-model>")
    script = """
const vm = require('vm');
const ctx = vm.createContext({});
vm.runInContext(%s + "\\nthis.f = micAnswer; this.FIXED = FIXED_MESSAGES;", ctx);
console.log(JSON.stringify([ctx.f({capturing: false, released: true, held: 'yes', extra: 'x'}), ctx.f({capturing: 'no'}),
  ctx.f(null), ctx.f({accepted: false, reason: 'bad_request'}), ctx.FIXED.filter((m) => m.indexOf('mic') >= 0)]));
""" % json.dumps(model)
    out = subprocess.run([node, _node_script(tmp_path, script)], capture_output=True, encoding="utf-8",
                         timeout=30)
    assert out.returncode == 0, out.stderr
    a, b, c, d, fixed = json.loads(out.stdout)
    assert a == {"capturing": False, "released": True, "held": False}
    assert b == {"capturing": None, "released": False, "held": False} and c is None
    assert d == {"capturing": None, "released": False, "held": False}, "a bad_request is never 'not capturing'"
    assert fixed == ["emma-face:mic", "emma-face:mic:release", "emma-face:mic:hold", "emma-face:mic:hold:deaf",
                     "emma-face:mic:free"]


# ==================== the app (source contracts; AudioRecord does not run on a JVM) ====================


def _java(name):
    from tests.test_emma_robot_body_nav_app import code_only

    return code_only((SRC / name).read_text(encoding="utf-8"))


def _method(src, signature):
    from tests.test_emma_robot_body_nav_app import method

    return method(src, signature)


def test_the_app_holds_the_array_alone_and_lets_go_for_a_call_or_its_run():
    src = _java("ArrayListener.java")
    start = _method(src, "private void startCapture(Run r)")
    # MicOwner decides, checked again UNDER its lock right where the thread starts
    locked = start.split("synchronized (micOwner) {", 1)[1]
    assert locked.index("if (!micOwner.mayStart())") < locked.index("captureWanted = true;") \
        < locked.index("capture.start();")
    take = _method(src, "private String micTake(String kind, String why)")
    held = take.split("synchronized (micOwner) {", 1)[1].split("\n        }", 1)[0]
    assert "micOwner.release(why)" in held and "micOwner.hold(kind)" in held and "stopCapture();" in held
    # "capturing" until the thread that holds the AudioRecord has ENDED (after r.release())
    running = _method(src, "private boolean captureRunning()")
    assert "captureWanted || (t != null && t.isAlive())" in running
    assert "captureRunning()" in _method(src, "String micState()")
    assert 'return micTake("release", why);' in _method(src, "String micRelease(String why)")
    assert 'micTake("deaf".equals(kind) ? "deaf" : "start", null)' in _method(src, "String micHold(String kind)")
    # free: only a lifted hold restarts, and only after the WebView's recordings ended
    free = _method(src, "String micFree()")
    assert free.index("if (micOwner.free())") < free.index("activeOthers(-1)") < free.index("now.retainAll(page);") \
        < free.index("if (now.isEmpty()) break;") < free.index("startCapture(r);")
    assert 'if (r != null && live(r) && "ready".equals(linkState) && soleMode) startCapture(r);' in free
    # a resume (the face back in front) touches neither: the page frees a hold, a restart a release
    assert "micOwner" not in _method(src, "void resume()")
    # sole mode only when the server asked, and only with the beam on
    assert 'soleMode = beamMode != null && m.optJSONObject("array_mic") != null;' in _method(
        src, "private void readArray(JSONObject m)")
    serve = _method(src, "private long serve(Run r, DoaLink opened)")
    assert '"array_release".equals(message.optString("type"))' in serve
    assert 'micTake("release", "server:" + DoaMath.codeOrEmpty(message.optString("why", "")));' in serve
    loop = _method(src, "private void captureLoop(Run owner)")
    finale = loop.split("} finally {", 1)[1]
    assert finale.index("if (micOwner.released() || micOwner.held())") < finale.index("else if (latched == null)")


def test_the_ownership_rules_run_on_a_jvm(tmp_path):
    """MicOwner.java, compiled and run: a start hold never latches, deaf holds
    are counted and the 3rd of a run latches, free lifts only a hold, a
    release is never undone, and nothing starts while held or released."""
    from tests.test_emma_robot_body_nav_app import _javac

    tools = _javac()
    if tools is None:
        pytest.skip("no JDK found: MicOwner was NOT compiled or run -- the call/run rules are unchecked "
                    "beyond source text")
    javac, java = tools
    work = tmp_path / "src"
    work.mkdir()
    (work / "M.java").write_text("""package com.emma.robot.control;
public class M { static void p(String k, Object v) { System.out.println(k + "=" + v); }
public static void main(String[] a) {
  MicOwner o = new MicOwner();
  p("fresh", o.mayStart());
  for (int k = 0; k < 10; k++) { o.hold("start"); if (o.mayStart()) p("bad", k); if (!o.free()) p("badfree", k); }
  p("start_x10_released", o.released()); p("start_x10_may", o.mayStart()); p("deaf0", o.deafHolds());
  p("free_unheld", o.free());
  p("deaf1", o.hold("deaf")); p("deaf1_free", o.free());
  p("deaf2", o.hold("deaf")); p("deaf2_free", o.free());
  p("deaf3", o.hold("deaf")); p("deaf3_why", o.why()); p("deaf3_free", o.free()); p("deaf3_may", o.mayStart());
  p("after_latch_hold", o.hold("start")); p("after_latch_may", o.mayStart());
  MicOwner r = new MicOwner();
  r.release("old_page"); p("rel_free", r.free()); p("rel_may", r.mayStart()); p("rel_why", r.why());
  r.release("again"); p("rel_why2", r.why());
  MicOwner h = new MicOwner(); h.hold("start"); h.release("barge_in"); p("held_then_rel_free", h.free());
} }""", encoding="utf-8")
    out = tmp_path / "out"
    subprocess.run([javac, "-encoding", "UTF-8", "-d", str(out), str(SRC / "MicOwner.java"), str(work / "M.java")],
                   check=True, capture_output=True, timeout=120)
    res = subprocess.run([java, "-cp", str(out), "com.emma.robot.control.M"], capture_output=True,
                         encoding="utf-8", timeout=60, check=True)
    got = dict(line.split("=", 1) for line in res.stdout.split())
    assert "bad" not in got and "badfree" not in got, got
    assert got["fresh"] == "true"
    assert got["start_x10_released"] == "false" and got["start_x10_may"] == "true" and got["deaf0"] == "0"
    assert got["free_unheld"] == "false"
    assert (got["deaf1"], got["deaf1_free"], got["deaf2"], got["deaf2_free"]) == ("false", "true", "false", "true")
    assert (got["deaf3"], got["deaf3_why"], got["deaf3_free"], got["deaf3_may"]) == ("true", "deaf_x3", "false", "false")
    assert got["after_latch_hold"] == "true" and got["after_latch_may"] == "false"
    assert (got["rel_free"], got["rel_may"], got["rel_why"], got["rel_why2"]) == ("false", "false", "old_page", "old_page")
    assert got["held_then_rel_free"] == "false"


def test_the_face_answers_the_mic_prompts_and_the_activity_implements_them():
    face = _java("EmmaFace.java")
    answer = _method(face, "private String answer(String message)")
    for literal, call in (('"emma-face:mic"', "bridge.micState()"),
                          ('"emma-face:mic:release"', "bridge.micRelease()"),
                          ('"emma-face:mic:hold"', 'bridge.micHold("start")'),
                          ('"emma-face:mic:hold:deaf"', 'bridge.micHold("deaf")'),
                          ('"emma-face:mic:free"', "bridge.micFree()")):
        case = answer.index("case " + literal + ":")
        assert answer.startswith("return " + call + ";", answer.index("return", case)), literal
    main = _java("MainActivity.java")
    assert "array.micRelease(\"page\")" in _method(main, "public String micRelease()")
    assert "array.micState()" in _method(main, "public String micState()")
    assert "array.micHold(kind)" in _method(main, "public String micHold(String kind)")
    assert "array.micFree()" in _method(main, "public String micFree()")


def test_the_servers_release_reason_is_shape_checked_in_the_app(tmp_path):
    from tests.test_emma_robot_body_nav_app import _javac

    tools = _javac()
    if tools is None:
        pytest.skip("no JDK found: DoaMath.codeOrEmpty was NOT compiled or run")
    javac, java = tools
    work = tmp_path / "src"
    work.mkdir()
    (work / "H.java").write_text("""package com.emma.robot.control;
public class H { public static void main(String[] a) {
  for (String s : new String[]{"stale", "not_robot_face", "Stale", "x y", "", "a23456789012345678901234567890123",
                               "zeros\\nINJECT"}) System.out.println("[" + DoaMath.codeOrEmpty(s) + "]");
} }""", encoding="utf-8")
    out = tmp_path / "out"
    subprocess.run([javac, "-encoding", "UTF-8", "-d", str(out), str(SRC / "DoaMath.java"), str(work / "H.java")],
                   check=True, capture_output=True, timeout=120)
    res = subprocess.run([java, "-cp", str(out), "com.emma.robot.control.H"], capture_output=True,
                         encoding="utf-8", timeout=60, check=True)
    assert res.stdout.split() == ["[stale]", "[not_robot_face]", "[]", "[]", "[]", "[]", "[]"]


# ==================== mutation-check gaps (2 Nov): each of these let a reverted guard pass ====================


def test_a_stream_that_dies_while_she_speaks_is_handed_back_on_time(monkeypatch, sole_on):
    """The health check runs BEFORE the echo hold: an array that dies while
    Emma is audible counts toward the hand-back from the moment it died --
    not from the end of her tail (which would leave the next turn deaf)."""
    rows = []
    monkeypatch.setattr(array_audio, "_record", lambda event, **f: rows.append((event, f)))
    run_sole(array_down=[(1.0, 9.0)], speaking=lambda t: 0.8 <= t < 2.5, deaf_s=0.5, monkeypatch=monkeypatch)
    [(_, row)] = [r for r in rows if r[0] == "array_mic_handback"]
    assert 500 <= row["dead_ms"] < 600, row


def test_page_never_takes_an_unknown_answer_for_a_released_array():
    """An answer without capturing=false (a bad_request, an APK mid-update)
    is NOT "the app let go": the page waits out the bound, then says so."""
    unknown = {"capturing": None, "released": False}
    r = _page_run(offer="call", releaseAnswer=unknown, states=[], last=unknown)
    log = r["log"]
    assert r["now"] >= 3000 and log.index("getUserMedia") > log.index("hold:start")
    assert _sends(log)[0]["state"] == "timeout"


def test_page_takes_its_mic_when_the_silent_clock_never_ticks():
    """The one deafness the server cannot see: a WebView that does not run the
    worklet on a silent input sends nothing -- not even the zeros the deaf
    check counts. No frame within ARRAY_CLOCK_CHECK_MS = the page lets the
    app go and opens its own mic."""
    gone = {"capturing": False, "released": False}
    r = _page_run(offer=True, ticks=False, releaseAnswer=gone)
    log = r["log"]
    assert r["now"] >= 2000
    assert log.index("worklet:cap-silent") < log.index("hold:deaf") < log.index("getUserMedia")
    assert [(m["why"], m["state"]) for m in _sends(log)] == [
        ("array_start", "array"), ("array_no_clock", "held"), ("array_no_clock", "page")]
    # a clock that ticks: no swap, no mic
    assert "getUserMedia" not in _page_run(offer=True)["log"]


def test_doa_focus_off_keeps_every_direction_away_from_focus(monkeypatch):
    """DOA_FOCUS=false: the link, the calibration and ARRAY_MIC's audio run,
    and focus never hears of a direction (stage 1 of the robot test)."""
    fake = _DoaFocus()
    monkeypatch.setattr(doa, "_focus", lambda: fake)
    doa._observe(1.0, 12.0, 0.9)
    assert fake.observed == [12.0]
    monkeypatch.setattr(settings, "doa_focus", False)
    doa._observe(2.0, 12.0, 0.9)
    assert fake.observed == [12.0]


def test_a_new_apk_never_captures_beside_the_page_so_rollback_is_one_step(monkeypatch):
    """The new APK is asked for the array only as its only owner: with
    ARRAY_MIC off (DOA_ENABLED still on -- a half rollback) its link gets
    doa_off, so it never opens the array under the page's mic. An APK from
    before keeps today's DOA_ENABLED behaviour."""
    client = _client(monkeypatch)
    monkeypatch.setattr(settings, "array_mic", False)
    with client.websocket_connect("/ws/robot-doa?token=" + TOKEN, headers={"user-agent": LINK_UA}) as ws:
        assert ws.receive_json() == {"type": "doa_off"}
    with client.websocket_connect("/ws/robot-doa?token=" + TOKEN, headers={"user-agent": OLD_LINK_UA}) as ws:
        ready = ws.receive_json()
        assert ready["type"] == "doa_ready" and "array_mic" not in ready


# ==================== the page in a REAL Chromium (playwright) ====================
# node runs the page's code against fakes; this runs the real page in a real
# browser, the only place the silent clock (ConstantSource -> AudioWorklet,
# never connected to the destination) can be shown to tick at all. PC
# Chromium, not the robot's WebView 124: the on-robot test still decides.

from tests.test_voice_preview_page import _chromium_face  # noqa: E402,F401  (a fixture)

_APP_INIT = r"""
(() => {
  // every frame the page sends: its largest sample (0 = exact silence)
  const send = window.WebSocket.prototype.send;
  window.__frames = [];
  window.WebSocket.prototype.send = function (data) {
    if (typeof data !== 'string') {
      const a = new Int16Array(data);
      let peak = 0;
      for (let i = 0; i < a.length; i++) peak = Math.max(peak, Math.abs(a[i]));
      window.__frames.push(peak);
    }
    return send.call(this, data);
  };
  // the app's bridge: emma-face:mic* answered from window.__app, the rest refused
  window.__app = { capturing: true, released: false, held: false, stopAfter: 2 };
  window.__asks = [];
  window.__gum = [];
  window.prompt = (message) => {
    window.__asks.push(message + '@' + window.__gum.length);
    if (message === 'emma-face:mic:release') { window.__app.released = true; }
    if (message === 'emma-face:mic:hold' || message === 'emma-face:mic:hold:deaf') { window.__app.held = true; }
    if (message === 'emma-face:mic:free') { window.__app.held = false; }
    if (message.indexOf('emma-face:mic') === 0) {
      if ((window.__app.released || window.__app.held) && window.__app.stopAfter-- <= 0) window.__app.capturing = false;
      return JSON.stringify({ sole: true, capturing: window.__app.capturing, released: window.__app.released,
                              held: window.__app.held });
    }
    return '';
  };
  // every getUserMedia that asks for audio
  const md = navigator.mediaDevices;
  const gum = md.getUserMedia.bind(md);
  md.getUserMedia = (c) => { if (c && c.audio) window.__gum.push(window.__asks.length); return gum(c); };
})();
"""


def _robot_call(chromium, ua):
    browser, port, fake_socket = chromium
    ctx = browser.new_context(viewport={"width": 1080, "height": 1920}, device_scale_factor=1,
                              permissions=["microphone"], user_agent=ua)
    ctx.add_init_script(fake_socket)
    ctx.add_init_script(_APP_INIT)
    page = ctx.new_page()
    page.goto("http://127.0.0.1:%d/preview?app=1&cue=0" % port)
    page.wait_for_function("() => document.querySelector('.control.mic') && window.EmmaStory")
    page.wait_for_timeout(1200)
    page.click(".control.mic")
    page.wait_for_function("() => !!window.__callWS && window.__callWS.readyState === 1")
    return ctx, page


_READY = """(offer) => window.__emit(Object.assign({type: 'ready', provider: 'gemini', profile: 'condo',
    voice: 'Kore', input_rate: 16000, output_rate: 24000, half_duplex: true, barge_in: false,
    hold_speech: false}, offer))"""

_CHROME_UA = ("Mozilla/5.0 (Linux; Android 15; ZC-3588A) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0.0.0 Mobile Safari/537.36 wv EmmaRobot/0.1 EmmaBridge/1")


def _mic_states(page):
    sent = page.evaluate("() => window.__sent || []")
    out = []
    for item in sent:
        if item.startswith("{") and '"mic_state"' in item:
            m = json.loads(item)
            out.append((m["why"], m["state"]))
    return out


@pytest.mark.timeout(120)
def test_real_chromium_the_silent_clock_ticks_and_the_hand_back_opens_the_mic_after_the_app(_chromium_face):
    ctx, page = _robot_call(_chromium_face, _CHROME_UA + " " + array_audio.MARKER)
    try:
        # a hold left by the last document is freed as the page's first act (before any call)
        first = page.evaluate("() => window.__asks.filter((a) => a.indexOf('emma-face:mic') === 0)")
        assert first == ["emma-face:mic:free@0"], first
        page.evaluate("() => { window.__asks = []; }")
        page.evaluate(_READY, {"array_mic": True})
        page.wait_for_timeout(1500)
        frames = page.evaluate("() => window.__frames.slice()")
        # ~50 frames a second of exact zeros, and no microphone opened
        assert len(frames) >= 40 and max(frames) == 0, (len(frames), max(frames or [0]))
        assert page.evaluate("() => window.__gum.length") == 0
        assert ("array_start", "array") in _mic_states(page)
        # the server's hand-back: the app is asked to let go, the page polls until
        # its capture ENDED, and only then opens its own mic
        page.evaluate("() => { window.__frames = []; window.__emit({type: 'array_mic_lost', why: 'stale'}); }")
        page.wait_for_function("() => window.__gum.length === 1", timeout=5000)
        asks = page.evaluate("() => window.__asks.filter((a) => a.indexOf('emma-face:mic') === 0)")
        gum_after = page.evaluate("() => window.__gum[0]")
        mic_asks = [a for a in asks]
        assert mic_asks[0].startswith("emma-face:mic:hold:deaf@0"), mic_asks
        # every ask before getUserMedia saw no mic open yet; the last one said "not capturing"
        assert all(a.endswith("@0") for a in mic_asks)
        assert page.evaluate("() => window.__app.capturing") is False and gum_after >= len(mic_asks)
        page.wait_for_timeout(1500)
        states = _mic_states(page)
        assert ("array_lost", "held") in states and ("array_lost", "page") in states
        frames = page.evaluate("() => window.__frames.slice()")
        assert len(frames) >= 40, len(frames)          # the page's own mic now feeds the same clock
        # the call ends: the hold is freed (after the tracks -- tests the order in node)
        page.evaluate("() => { window.__asks = []; window.__callWS.onclose({}); }")
        page.wait_for_timeout(300)
        assert page.evaluate("() => window.__asks.filter((a) => a.indexOf('emma-face:mic') === 0)") \
            == ["emma-face:mic:free@1"]
        assert page.evaluate("() => window.__app.held") is False
    finally:
        ctx.close()


@pytest.mark.timeout(120)
def test_real_chromium_an_old_apk_gets_today_s_mic_whatever_the_server_says(_chromium_face):
    ctx, page = _robot_call(_chromium_face, _CHROME_UA)
    try:
        page.evaluate(_READY, {"array_mic": True})
        page.wait_for_function("() => window.__gum.length === 1", timeout=5000)
        asks = page.evaluate("() => window.__asks.filter((a) => a.indexOf('emma-face:mic') === 0)")
        assert asks == [] and _mic_states(page) == []         # not even the load-time free
    finally:
        ctx.close()


# ==================== lead's review (2 Nov): call-scoped holds, the stall guard ====================


_TEARDOWN = r"""
const vm = require('vm');
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8').replace(/\r\n/g, '\n');   // a CRLF checkout too
const a = html.indexOf('    function releaseCallHardware() {');
const code = html.slice(a, html.indexOf('\n    }\n', a) + 7);
const held = process.argv[3] === '1';
const log = [];
const c = vm.createContext({
  worklet: null, micSource: null, sources: [], bargeGate: null, audioCtx: null, dropHeldSpeech() {},
  micStream: { getTracks: () => [{ stop() { log.push('track.stop'); } }, { stop() { log.push('track.stop'); } }] },
  arrayMicHeld: held, arrayMicSilent: true,
  window: { EmmaRobotBridge: { arrayMic: { free() { log.push('free'); } } } },
});
vm.runInContext(code + '\nreleaseCallHardware(); this.after = arrayMicHeld;', c);
console.log(JSON.stringify({ log, after: c.after }));
"""


@pytest.mark.parametrize("held", [True, False])
def test_the_call_frees_the_app_only_after_its_mic_tracks_are_stopped(tmp_path, held):
    node = _node()
    out = subprocess.run([node, _node_script(tmp_path, _TEARDOWN), str(PAGE), "1" if held else "0"],
                         capture_output=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    if held:
        assert got["log"] == ["track.stop", "track.stop", "free"] and got["after"] is False
    else:
        assert got["log"] == ["track.stop", "track.stop"], "no hold, nothing to free"


def test_every_call_exit_goes_through_the_teardown_that_frees():
    """The free lives in releaseCallHardware: the socket closing, a failed
    mic, the next call's start -- every way out of a call runs it."""
    page = PAGE.read_text(encoding="utf-8")
    assert page.count("window.EmmaRobotBridge.arrayMic.free()") == 1
    teardown = page.split("function releaseCallHardware() {", 1)[1].split("\n    }\n", 1)[0]
    assert teardown.index("getTracks().forEach(function (t) { t.stop(); })") < teardown.index("arrayMic.free()")
    for exit_ in ("ws.onclose = function () {", "function stopFailedCall() {", "function startCall() {"):
        body = page.split(exit_, 1)[1][:2500]
        assert "releaseCallHardware();" in body, exit_


def test_no_hand_back_is_decided_inside_or_right_after_an_event_loop_stall(monkeypatch, sole_on):
    """Lead's review: `last_good` is when a chunk with sound was PROCESSED;
    after a stall the page frame can be judged before the array's chunks
    queued on the other socket -- a false 'dead' that cost the array. While
    the loop watch has not beaten since the stall, and for STALL_GRACE_S
    after, the decision waits (counted: stall_held); the dead clock keeps
    running, so a truly dead array is still handed back right after."""
    rows = []
    monkeypatch.setattr(array_audio, "_record", lambda event, **f: rows.append((event, f)))
    monkeypatch.setattr(settings, "array_mic_deaf_s", 2.0)
    link = _Link()
    array_audio.link_opened(link)
    tap = array_audio.Tap(sole=True)
    backs = []
    tap.on_handback = backs.append
    array_audio._beam(link, 0, _array(), 100.0)
    tap.frame(SILENT, 100.0, speaking=False, in_speech=True)          # live
    # a 3 s stall: nothing ran from 100.0 to 103.0; the page frame comes first
    array_audio._LOOP.update(beat=100.0, stall_end=None)
    assert tap.frame(SILENT, 103.0, speaking=False, in_speech=True) is SILENT
    assert backs == [] and tap.counts["stall_held"] == 1
    # the watch beats (the stall seen), the array's queued chunks drain: live again
    array_audio._LOOP.update(beat=103.01, stall_end=103.01)
    array_audio._beam(link, 1, _array(), 103.02)
    out = tap.frame(SILENT, 103.03, speaking=False, in_speech=True)
    assert out and out is not SILENT and backs == []
    # a truly dead array after a stall: handed back once the grace has passed
    array_audio._LOOP.update(beat=106.0, stall_end=105.9)
    assert tap.frame(SILENT, 106.0, speaking=False, in_speech=True) is SILENT and backs == []
    array_audio._LOOP.update(beat=106.5, stall_end=105.9)
    tap.frame(SILENT, 106.5, speaking=False, in_speech=True)
    assert backs == ["stale"]


def test_the_loop_watch_measures_a_real_stall_and_stops_with_its_last_user():
    async def go():
        array_audio.loop_watch_start()
        await asyncio.sleep(0.2)
        healthy = array_audio._stalled(time.monotonic())
        time.sleep(0.4)                                   # a real stall of the loop
        stalled_now = array_audio._stalled(time.monotonic())
        await asyncio.sleep(0.15)                         # the watch beats and records it
        just_after = array_audio._stalled(time.monotonic())
        await asyncio.sleep(array_audio.STALL_GRACE_S + 0.1)
        later = array_audio._stalled(time.monotonic())
        seen = array_audio._LOOP["stall_s"]
        array_audio.loop_watch_stop()
        return healthy, stalled_now, just_after, later, seen, array_audio._LOOP["task"]
    # its own thread: playwright's sync API (the Chromium tests' module fixture)
    # keeps an event loop running on this one
    import threading

    box = []
    worker = threading.Thread(target=lambda: box.append(asyncio.run(go())))
    worker.start()
    worker.join(10)
    healthy, stalled_now, just_after, later, seen, task = box[0]
    assert (healthy, stalled_now, just_after, later) == (False, True, True, False)
    assert seen >= 0.3 and task is None
