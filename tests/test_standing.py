"""The standing line (STANDING_LINE): a summoned session held open so a face
greeting costs a turn, not a 2-3s dial.

The property worth defending on every side is the OFF side: the sales-room
default must not grow a loop that dials Gemini on its own, and a line that
has been used must still die — one visitor's words must not sit in the
model's context when the next visitor walks up.
"""
from __future__ import annotations

import asyncio
import json
import time
import types
from pathlib import Path

import pytest

from app import standing
from app.config import settings


def test_off_by_default_and_refused_under_multi_session(monkeypatch):
    standing.stop()
    assert settings.standing_line is False, "conftest pins the .env value off"
    standing.start()
    assert standing._task is None

    monkeypatch.setattr(settings, "standing_line", True)
    monkeypatch.setattr(settings, "multi_session", True)
    standing.start()
    assert standing._task is None, "no single line exists to hold under MULTI_SESSION"


def test_the_loop_rings_only_when_no_session_is_live(monkeypatch):
    from app import session as session_module
    from app import wake

    monkeypatch.setattr(session_module, "_active", None)
    rings = []

    async def fake_summon(reason=""):
        rings.append(reason)
        return 1

    monkeypatch.setattr(wake, "summon", fake_summon)
    # Two ticks with a live session (no ring), then the line is free.
    actives = [object(), object(), None, None]
    calls = {"n": 0}

    async def fake_sleep(_s):
        calls["n"] += 1
        if calls["n"] > 6:
            raise asyncio.CancelledError
        if actives:
            session_module._active = actives.pop(0)

    monkeypatch.setattr(standing.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(standing.run())
    assert rings == ["standing_line", "standing_line"]


def test_a_muted_page_is_not_rung_open_but_the_camera_still_rings_it():
    """Owner, 14 Oct: muted = the standing line does not ring the page back
    open (an idle line re-dialled with the mic on reached the room's chatter),
    but "ตอน Mute ถ้ากล้องจับหน้าคนได้ จะต้องทักนะ" -- the camera's bells
    still reach it. The page says so on its bell socket; any other text is
    ignored, and a page that goes away takes its hold with it."""
    from app import wake

    class Sock:
        def __init__(self):
            self.sent = []

        async def send_text(self, text):
            self.sent.append(json.loads(text)["reason"])

    wake.reset()
    muted, plain = Sock(), Sock()
    wake.register(muted)
    wake.register(plain)
    wake.note(muted, json.dumps({"type": "hold", "on": True}))
    wake.note(plain, "not json")
    wake.note(plain, json.dumps({"type": "something", "on": True}))
    assert asyncio.run(wake.summon(reason="standing_line")) == 1
    assert muted.sent == [] and plain.sent == ["standing_line"]
    for reason in ("face_approach", "face_known", "face_stranger"):
        asyncio.run(wake.summon(reason=reason))
    assert muted.sent == ["face_approach", "face_known", "face_stranger"]
    assert not wake.held_only()
    wake.unregister(plain)
    assert wake.held_only()
    wake.note(muted, json.dumps({"type": "hold", "on": False}))
    assert asyncio.run(wake.summon(reason="standing_line")) == 1 and muted.sent[-1] == "standing_line"
    wake.unregister(muted)
    assert not wake.held_only() and not wake._HELD
    wake.reset()


def test_the_camera_opening_a_line_through_a_hold_is_on_the_record(monkeypatch):
    """Owner, 5 Nov: "ปิดไมค์ ... อยู่หุ่นก็สามารถได้ยิน". The one way a muted robot hears on purpose,
    and until now it could only be inferred from gaps in server.log."""
    from app import turnlog, wake

    rows = []
    monkeypatch.setattr(turnlog, "record", lambda event, **f: rows.append((event, f)))

    class Sock:
        async def send_text(self, text):
            pass

    wake.reset()
    sock = Sock()
    wake.register(sock)
    asyncio.run(wake.summon(reason="face_known"))
    assert rows == [], "not muted: nothing to record"
    wake.note(sock, json.dumps({"type": "hold", "on": True}))
    asyncio.run(wake.summon(reason="standing_line"))
    assert rows == [], "the standing line does not get through, and is not the camera"
    asyncio.run(wake.summon(reason="face_known"))
    assert rows == [("mic_hold", {"on": True, "why": "camera_rang", "reason": "face_known"})]
    asyncio.run(wake.summon(reason="face_known"))
    assert len(rows) == 1, "one row for a face that keeps ringing"
    wake.reset()


def test_the_robot_keeps_a_mute_through_a_reboot():
    """Android rebooted 9 times on 5 Nov, and each one forgot the mute the WebView held."""
    env = (Path(__file__).resolve().parent.parent / "data" / "robot-server" / "robot.env").read_text(encoding="utf-8")
    assert "MIC_HOLD_PERSIST=true" in env.splitlines()


def test_a_virgin_standing_line_is_not_abandoned(monkeypatch):
    """Ungreeted and nobody ever heard: this IS the held-open line, and the
    30s abandon / idle timeout must both leave it alone — an hour of silence
    included."""
    from app import session as session_module

    monkeypatch.setattr(settings, "standing_line", True)
    monkeypatch.setattr(settings, "idle_timeout_s", 120)
    sess = session_module.VoiceSession(None, provider_name="gemini", summoned=True)
    sess._last_heard_at = time.monotonic() - 3600
    sent = []

    async def send(msg):
        sent.append(msg)

    sess._send_json = send
    calls = {"n": 0}

    async def fake_sleep(_s):
        calls["n"] += 1
        if calls["n"] > 8:
            raise asyncio.CancelledError

    monkeypatch.setattr(session_module.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(sess._close_when_nobody_is_there())
    assert sent == [], "a virgin standing line must never be hung up on"


def test_a_standing_line_that_was_used_still_dies(monkeypatch):
    """The moment anybody was heard, the ordinary rules end the quiet line —
    that is what flushes one visitor's context before the next."""
    from app import session as session_module

    monkeypatch.setattr(settings, "standing_line", True)
    monkeypatch.setattr(settings, "idle_timeout_s", 120)
    sess = session_module.VoiceSession(None, provider_name="gemini", summoned=True)
    sess._ever_heard = True
    sess._last_heard_at = time.monotonic() - 3600
    sent = []

    async def send(msg):
        sent.append(msg)

    sess._send_json = send

    async def fake_sleep(_s):
        pass

    monkeypatch.setattr(session_module.asyncio, "sleep", fake_sleep)
    asyncio.run(sess._close_when_nobody_is_there())
    assert sent and sent[-1]["type"] == "idle_timeout"


def test_a_line_already_spoken_on_counts_as_a_conversation():
    """ประโยคทักทายไม่ให้พูดแทรก covers the standing line too: somebody who
    started talking on the held-open line must not be greeted over."""
    from app import greeter

    class Line:
        summoned = True
        _greeting_turn_seen = False
        _ever_heard = False

    assert greeter._in_conversation(Line()) is False
    Line._ever_heard = True
    assert greeter._in_conversation(Line()) is True


# -- the greeting on a line held open for hours (review 10 Oct) ----------------

def _ticks(monkeypatch, sess, n=3):
    from app import session as session_module
    from app.tools import slides

    sent = []

    async def send(msg):
        sent.append(msg)

    async def shut():
        pass

    sess._send_json = send
    monkeypatch.setattr(slides, "shutdown_display", shut)
    calls = {"n": 0}

    async def fake_sleep(_s):
        calls["n"] += 1
        if calls["n"] > n:
            raise asyncio.CancelledError

    monkeypatch.setattr(session_module.asyncio, "sleep", fake_sleep)
    try:
        asyncio.run(sess._close_when_nobody_is_there())
    except asyncio.CancelledError:
        pass
    return [m["type"] for m in sent]


@pytest.mark.parametrize("frames_streamed", [True, False])
def test_a_greeted_standing_line_is_not_hung_up_on_before_the_guest_can_answer(
        monkeypatch, frames_streamed):
    """Dialled an hour ago, greeted just now, the guest has not spoken yet.

    Probe 10 Oct, both ways the robot gets there: with the page's mic
    streaming (the real robot) the 10 s hold ended at the first frame and the
    greeting was never registered — the greeted line stayed "virgin" and,
    once the guest spoke, died by the 30 s ABANDON rule; with no frame since
    the dial the greeting registered but `_last_heard_at` still held the
    dial time, and the first idle tick hung up on the greeted guest."""
    from app import display
    from app import session as session_module

    monkeypatch.setattr(settings, "standing_line", True)
    monkeypatch.setattr(settings, "idle_timeout_s", 120)
    monkeypatch.setattr(display, "remaining_lead", lambda: 0.0)
    sess = session_module.VoiceSession(None, provider_name="gemini", summoned=True)
    sess._last_heard_at = time.monotonic() - 3600
    sess._ears_closed_until = None if frames_streamed else time.monotonic() - 3590
    sess._greeting_turn_done()
    assert sess._greeting_turn_seen, "the greeting on the held line is the greeting"
    assert _ticks(monkeypatch, sess) == [], "hung up on the greeting it just gave"

    # The guest answers, then listens 35 s to a long reply: IDLE_TIMEOUT_S
    # (120) governs a greeted call, not the 30 s abandon of an ungreeted one.
    sess._ever_heard = True
    sess._last_heard_at = time.monotonic() - 35
    assert _ticks(monkeypatch, sess) == []


def test_a_greeted_standing_line_still_dies_when_nobody_answers(monkeypatch):
    """The idle clock starts at the greeting — and still runs out."""
    from app import display
    from app import session as session_module

    monkeypatch.setattr(settings, "standing_line", True)
    monkeypatch.setattr(settings, "idle_timeout_s", 120)
    monkeypatch.setattr(display, "remaining_lead", lambda: 0.0)
    sess = session_module.VoiceSession(None, provider_name="gemini", summoned=True)
    sess._last_heard_at = time.monotonic() - 3600
    sess._ears_closed_until = None
    sess._greeting_turn_done()
    sess._idle_from -= 121
    assert _ticks(monkeypatch, sess) == ["idle_timeout"]


def test_an_answer_on_a_line_somebody_spoke_on_is_not_its_greeting(monkeypatch):
    """Ears already open and a guest already heard: that turn answered them.
    It must not restart the idle clock (only guest speech does)."""
    from app import session as session_module

    sess = session_module.VoiceSession(None, provider_name="gemini", summoned=True)
    sess._ears_closed_until = None
    sess._ever_heard = True
    sess._greeting_turn_done()
    assert sess._greeting_turn_seen is False
    assert sess._idle_from == 0.0
    plain = session_module.VoiceSession(None, provider_name="gemini")
    plain._greeting_turn_done()
    assert plain._greeting_turn_seen is False and plain._idle_from == 0.0


def test_the_idle_start_is_set_only_by_the_greeting():
    """The `_last_heard_at` rule's twin (test_only_guest_speech_resets_the_
    idle_clock): any other writer — assistant audio, a tool, a tour nudge —
    would keep the empty-room session alive forever."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "app" / "session.py").read_text(
        encoding="utf-8")
    writes = [line.strip() for line in source.splitlines() if "_idle_from = " in line]
    assert len(writes) == 2, writes
    body = source.split("def _greeting_turn_done", 1)[1].split("\n    def ", 1)[0]
    assert "self._idle_from = " in body


# ---- the mute hold on the server's disk (MIC_HOLD_PERSIST, owner 5 Nov:
# "ปิดไมค์ แต่เหมือนรีแล้วก็กลับมาพูด") ----

class _Bell:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


def _press(ws, on=True):
    from app import wake

    wake.note(ws, json.dumps({"type": "hold", "on": on, "press": True}))


@pytest.fixture
def kept_hold(monkeypatch, tmp_path):
    from app import wake

    path = tmp_path / "robot" / "mic_hold.json"
    monkeypatch.setattr(settings, "mic_hold_persist", True)
    monkeypatch.setattr(settings, "mic_hold_file", str(path))
    monkeypatch.setattr(settings, "mic_hold_reset_hour", 6)
    wake.reset()
    yield path
    wake.reset()


def test_a_pressed_mute_survives_a_server_restart_and_a_fresh_page(kept_hold):
    """The robot's server.log of 26-27 Oct: muted, a server restart with a fresh
    page, and the standing line rang 6 s later with nobody having touched the
    mic. The server restarting (wake.reset: every socket and all memory gone)
    must leave the hold on disk, and a page that has never heard of it is still
    not rung open. The camera keeps the owner's 14 Oct rule by default."""
    from app import wake

    page = _Bell()
    wake.register(page)
    _press(page)
    assert kept_hold.exists()
    wake.reset()

    fresh = _Bell()
    wake.register(fresh)
    state = wake.hello()["hold"]
    assert state["on"] is True and state["until"] > state["since"]
    assert asyncio.run(wake.summon(reason="standing_line")) == 0 and fresh.sent == []
    assert wake.held_only()
    assert asyncio.run(wake.summon(reason="face_stranger")) == 1


def test_only_a_press_changes_what_the_server_keeps(kept_hold):
    """A page's sync on connect is not a person: a fresh or an old page saying
    "not muted" must not lift a hold, a tap must."""
    from app import wake

    page = _Bell()
    wake.register(page)
    _press(page)
    wake.note(page, json.dumps({"type": "hold", "on": False}))           # yesterday's JS
    wake.note(page, json.dumps({"type": "hold", "on": False, "press": False}))
    assert wake.hold_state()["on"] is True
    assert asyncio.run(wake.summon(reason="standing_line")) == 0
    _press(page, on=False)
    assert wake.hold_state() == {"on": False} and not kept_hold.exists()
    assert asyncio.run(wake.summon(reason="standing_line")) == 1


def test_the_hold_ends_at_the_next_reset_hour():
    from app import wake

    at = lambda *a: time.mktime((2026, *a, 0, 0, 0, -1))
    assert wake._next_reset(at(11, 4, 18, 5)) == at(11, 5, 6, 0)          # evening: tomorrow
    assert wake._next_reset(at(11, 5, 5, 59)) == at(11, 5, 6, 0)          # before: this morning
    assert wake._next_reset(at(11, 5, 6, 0)) == at(11, 6, 6, 0)           # exactly: tomorrow
    assert wake._next_reset(at(11, 30, 20, 0)) == at(12, 1, 6, 0)         # month end


def test_yesterdays_hold_does_not_leave_the_robot_deaf_at_the_event(kept_hold):
    """The stale hold: the morning has passed. It ends, the file goes, the open
    bell is told so the screen stops saying Muted, and the standing line rings
    again -- in that order, in the same look."""
    from app import wake

    page = _Bell()
    wake.register(page)
    _press(page)
    wake._hold["until"] = time.time() - 1
    assert asyncio.run(wake.summon(reason="standing_line")) == 1
    assert [f["type"] for f in page.sent] == ["hold_state", "wake"]
    assert page.sent[0]["on"] is False
    assert not kept_hold.exists() and wake.hold_state() == {"on": False}
    assert not wake._HELD


def test_an_expired_hold_on_disk_does_not_come_back_after_a_reboot(kept_hold):
    from app import wake

    kept_hold.parent.mkdir(parents=True)
    kept_hold.write_text(json.dumps({"since": time.time() - 90000, "until": time.time() - 3600}))
    assert wake.hold_state() == {"on": False} and not kept_hold.exists()


def test_a_clock_gone_backwards_does_not_hold_for_years(kept_hold):
    """A reset RTC: `until` is suddenly years away, the deaf robot again."""
    from app import wake

    kept_hold.parent.mkdir(parents=True)
    kept_hold.write_text(json.dumps({"since": time.time() + 3600, "until": time.time() + 7200}))
    assert wake.hold_state() == {"on": False}


@pytest.mark.parametrize("junk", ["{", "[]", '{"since": 1}', '{"since": "x", "until": 2}'])
def test_an_unreadable_hold_file_is_no_hold_not_a_crash(kept_hold, junk):
    from app import wake

    kept_hold.parent.mkdir(parents=True)
    kept_hold.write_text(junk)
    assert wake.hold_state() == {"on": False}


def test_mute_camera_greets_off_means_nothing_opens_and_she_greets_nobody(monkeypatch):
    """The owner's decision to take: a face under a hold rings nothing and gets
    no greeting, even on a line still open. Default keeps 14 Oct's rule (above)."""
    from app import events, wake

    monkeypatch.setattr(settings, "mute_camera_greets", False)
    wake.reset()
    page = _Bell()
    wake.register(page)
    assert not wake.camera_refused()                                     # nothing held yet
    wake.note(page, json.dumps({"type": "hold", "on": True}))
    assert wake.camera_refused()
    for reason in ("face_approach", "face_known", "face_stranger", "standing_line"):
        assert asyncio.run(wake.summon(reason=reason)) == 0
    assert page.sent == []

    told = []

    class Provider:
        async def send_text(self, text):
            told.append(text)

    class Sess:
        provider = Provider()

    from app import session as session_module

    monkeypatch.setattr(session_module, "_active", Sess())
    said = asyncio.run(events.announce("hello", source="face_stranger", summon=True))
    assert said is False and told == []
    # any other news still reaches her: this switch is about the camera only
    assert asyncio.run(events.announce("arrived", source="robot_arrived")) is True
    assert told and told[0].startswith("arrived")
    wake.reset()


def test_a_new_bell_is_told_the_hold_in_its_first_frame(kept_hold, monkeypatch):
    """The wire: the page adopts from the frame the socket opens with, before any
    ring can reach it. And the sync it sends back does not lift the hold."""
    from fastapi.testclient import TestClient

    from app import main, wake

    monkeypatch.setattr(settings, "wake_enabled", False)
    monkeypatch.setattr(settings, "face_enabled", True)
    monkeypatch.setattr(settings, "ws_token", "")
    page = _Bell()
    wake.register(page)
    _press(page)
    wake.unregister(page)
    with TestClient(main.app).websocket_connect("/ws/wake") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "standby_only" and hello["hold"]["on"] is True
        ws.send_text(json.dumps({"type": "hold", "on": True, "press": False}))
    assert wake.hold_state()["on"] is True


def test_with_the_switch_off_nothing_about_the_hold_changes(monkeypatch, tmp_path):
    """Every other machine: no file, no new field on the wire or the meter."""
    from app import wake

    path = tmp_path / "mic_hold.json"
    monkeypatch.setattr(settings, "mic_hold_file", str(path))
    assert settings.mic_hold_persist is False
    wake.reset()
    page = _Bell()
    wake.register(page)
    _press(page)
    assert not path.exists()
    assert wake.hello() == {} and wake.hold_state() is None and "hold" not in wake.mic_level()
    assert asyncio.run(wake.summon(reason="standing_line")) == 0          # the socket's hold, as before
    assert asyncio.run(wake.summon(reason="face_known")) == 1
    wake.reset()


def test_a_muted_virgin_line_ends_instead_of_waiting_for_a_greeting(monkeypatch):
    """A virgin line is held so a greeting lands fast; muted, she would greet
    somebody she cannot hear (14 Oct's log: a line open 23 min with no frames)."""
    from app import session as session_module

    monkeypatch.setattr(settings, "standing_line", True)
    monkeypatch.setattr(settings, "idle_timeout_s", 120)
    sess = session_module.VoiceSession(None, provider_name="gemini", summoned=True)
    sess._last_heard_at = time.monotonic() - 3600
    sess._page_muted = True
    sent = []

    async def send(msg):
        sent.append(msg)

    sess._send_json = send
    calls = {"n": 0}

    async def fake_sleep(_s):
        calls["n"] += 1
        if calls["n"] > 8:
            raise asyncio.CancelledError

    monkeypatch.setattr(session_module.asyncio, "sleep", fake_sleep)
    try:
        asyncio.run(sess._close_when_nobody_is_there())
    except asyncio.CancelledError:
        pass
    assert [m.get("type") for m in sent] == ["idle_timeout"]


def test_no_follow_up_into_the_silence_a_mute_makes():
    """FOLLOW_UP_AFTER_S counts silence, and a muted mic is silence by definition:
    4 s after she finished she asked a question nobody could answer."""
    from app import session as session_module

    sess = session_module.VoiceSession(None, provider_name="gemini", summoned=True)
    assert sess._follow_up_veto(time.monotonic()) != "muted"
    sess._page_muted = True
    assert sess._follow_up_veto(time.monotonic()) == "muted"
    source = (Path(session_module.__file__)).read_text(encoding="utf-8")
    handler = source[source.index('if event.get("type") == "mic_state":'):]
    handler = handler[:handler.index("continue")]
    assert 'event.get("why") in ("mute_tap", "hold_waiting", "hold_greeting", "hold_expired")' in handler
    assert 'self._page_muted = bool(event.get("muted"))' in handler


# -- review 6 Nov: the hold's ends and its witnesses ----------------------------

def test_a_press_holds_until_the_next_reset_hour_not_an_hour(kept_hold):
    """'ห้ามใช้อายุคงที่' (CLAUDE.md): a mutation to now + 3600 shipped green before this."""
    from app import wake

    page = _Bell()
    wake.register(page)
    _press(page)
    hold = wake._hold
    assert hold["until"] == wake._next_reset(hold["since"])


def test_the_morning_reaches_a_page_in_a_call(kept_hold, monkeypatch):
    """A camera's held call open at 06:00: summon was the only push of the expiry, and the
    standing line does not summon while a line is up -- the page read "Muted" past morning."""
    from app import session as session_module, standing, wake

    page = _Bell()
    wake.register(page)
    _press(page)
    wake._hold["until"] = time.time() - 1               # the morning came
    monkeypatch.setattr(session_module, "_active", object())
    ticks = {"n": 0}

    async def sleep(_s):
        ticks["n"] += 1
        if ticks["n"] > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(standing.asyncio, "sleep", sleep)
    try:
        asyncio.run(standing.run())
    except asyncio.CancelledError:
        pass
    frames = list(page.sent)
    assert {"type": "hold_state", "on": False, "why": "expired"} in frames
    assert not any(f.get("type") == "wake" for f in frames), "no ring with a line up"


def test_a_press_after_an_untold_expiry_is_not_undone(kept_hold):
    """Expired, nobody told yet, then a new press: the next summon used to send on: false while
    the server's hold stood, and the page un-muted (review 6 Nov)."""
    from app import wake

    page = _Bell()
    wake.register(page)
    _press(page)
    wake._hold["until"] = time.time() - 1
    assert wake.hold_state() == {"on": False}           # expiry seen, the news pending
    _press(page)
    asyncio.run(wake.summon(reason="face_known"))
    assert not any(m.get("type") == "hold_state" for m in page.sent)
    assert wake.hold_state()["on"] is True


def test_muted_camera_greetings_refused_without_a_bell_registered(monkeypatch, kept_hold):
    """MUTE_CAMERA_GREETS=false is the one switch only if it does not need a bell socket: between
    bell reconnects a held robot still greeted (review 6 Nov)."""
    from app import wake

    page = _Bell()
    wake.register(page)
    _press(page)
    wake.unregister(page)
    monkeypatch.setattr(settings, "mute_camera_greets", False)
    assert wake.camera_refused() is True
    monkeypatch.setattr(settings, "mute_camera_greets", True)
    assert wake.camera_refused() is False


def test_the_phone_reads_the_call_s_mic_not_the_hold(kept_hold, monkeypatch):
    """A greeted camera call under a hold has its mic open: the phone said "closed, tap it", and the
    tap closed it (review 6 Nov). With no call, the hold is what the next call opens with."""
    from app import event_host, session as session_module, wake

    page = _Bell()
    wake.register(page)
    _press(page)
    monkeypatch.setattr(session_module, "_active", None)
    assert event_host._mic_closed() is True
    call = types.SimpleNamespace(_page_muted=False)       # hold_greeting: open
    monkeypatch.setattr(session_module, "_active", call)
    assert event_host._mic_closed() is False
    call._page_muted = True                                # hold_waiting / a tap: shut
    assert event_host._mic_closed() is True


# ---- the Emma Ceremony app under a hold (robot, 23 Nov: a tap at 19:55 held the mic to 06:00; the
# ceremony app opened with no call -- the standing line skipped its held page -- and the opening refused
# "เปิดแอป Emma Ceremony บนหุ่นก่อน" until a tap at 22:25:19 lifted the hold; after the hand-back the Event
# call came up live and was re-muted by hand at 22:47:52) ----

def _ceremony_says(ws, on=False, press=False):
    from app import wake

    wake.note(ws, json.dumps({"type": "hold", "on": on, "press": press, "ceremony": True}))


def test_the_ceremony_app_s_line_comes_up_through_the_hold_and_leaves_it_standing(kept_hold, monkeypatch):
    from app import turnlog, wake

    rows = []
    monkeypatch.setattr(turnlog, "record", lambda event, **f: rows.append((event, f)))
    event_page = _Bell()
    wake.register(event_page)
    _press(event_page)                                       # 19:55
    since = wake.hold_state()["since"]
    ceremony_page = _Bell()
    wake.register(ceremony_page)
    _ceremony_says(ceremony_page)                            # its bell's first word, after hello
    assert asyncio.run(wake.summon(reason="standing_line")) == 1
    assert [m["reason"] for m in ceremony_page.sent] == ["standing_line"], "the ceremony's call comes up"
    assert event_page.sent == [], "the Event page under the hold: as before"
    assert not wake.held_only()
    assert ("mic_hold", {"on": True, "why": "ceremony_rang", "reason": "standing_line"}) in rows
    # taps in the ceremony app: its own line, never the stored hold
    _ceremony_says(ceremony_page, on=True, press=True)
    _ceremony_says(ceremony_page, on=False, press=True)
    state = wake.hold_state()
    assert state["on"] is True and state.get("since") == since, "a ceremony tap rewrote the stored hold"
    assert kept_hold.exists()
    # the hand-back: the ceremony app closes, the Event face comes back to the hold it left
    wake.unregister(ceremony_page)
    fresh = _Bell()
    wake.register(fresh)
    assert wake.hello()["hold"]["on"] is True and wake.hello()["hold"]["since"] == since
    wake.note(fresh, json.dumps({"type": "hold", "on": True, "press": False}))
    assert asyncio.run(wake.summon(reason="standing_line")) == 0 and fresh.sent == []
    assert wake.held_only()
    assert asyncio.run(wake.summon(reason="event_walk")) == 2, "a staff press still opens it, muted"
    # ...and a person's tap there still ends it
    _press(fresh, on=False)
    assert wake.hold_state() == {"on": False} and not kept_hold.exists()


def test_the_ceremony_app_s_own_tap_holds_its_own_line(kept_hold):
    """Its page's own hold (a tap in the ceremony app) is in `_HELD` like any page's: the standing line
    does not ring it open -- whether or not a stored hold stands."""
    from app import wake

    page = _Bell()
    wake.register(page)
    _ceremony_says(page, on=True)
    assert wake.hold_state() == {"on": False}, "its tap wrote nothing to disk"
    assert asyncio.run(wake.summon(reason="standing_line")) == 0
    assert wake.held_only()
    _ceremony_says(page, on=False)
    assert asyncio.run(wake.summon(reason="standing_line")) == 1
    wake.unregister(page)
    assert not wake._CEREMONY, "a page that goes away takes its name with it"
