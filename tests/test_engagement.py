"""
Engagement — the robot behaving like an established reception robot.

Owner, 9 Oct 2026: "ลองปรับให้เหมือนหุ่นต้อนรับอื่นให้ดีที่สุด". The reference
behaviour and how each piece maps onto it is in docs/listening-focus-2026-10-09.md
("Engagement"). Five pieces, each behind a default-off switch:

1. FOCUS_WAKE — Look and Talk's "outside the zone, say the name": the wake
   word inside a camera-opened call opens a window that waives focus rule 2.
2. FOCUS_INVITE — Pepper's BasicAwareness in words: a caller nobody can see
   is invited to step in front, once, through events.announce.
3. FOCUS_ENGAGE_MODE=full — Pepper's FullyEngaged: the main person keeps the
   robot until they leave.
4. FOCUS_ATTENTION — Furhat's gaze: the mascot turns toward whoever speaks.
5. FOCUS_GOODBYE — a short goodbye when the guest walks off mid-conversation.

Every producer is a stub here (faces, mouths, DOA, the KWS); the detection
tests that need the real wake model skip with a reason that says so.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import struct
import subprocess
import time
import types
import wave
from pathlib import Path

import pytest

from app import focus
from app.config import settings

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "data" / "wake"
MODEL_READY = (Path(settings.wake_model_dir) / "tokens.txt").exists()
T = 1000.0

needs_model = pytest.mark.skipif(
    not MODEL_READY,
    reason=("wake model not downloaded (scripts/fetch_wake_model.py) — the "
            "in-call ear's real DETECTION is not being verified on this "
            "machine, only the window and the session around it"))


@pytest.fixture
def rows(monkeypatch):
    from app import turnlog

    out: list[dict] = []
    monkeypatch.setattr(turnlog, "record", lambda event, **f: out.append({"event": event, **f}))
    return out


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(settings, "focus_enabled", True)
    monkeypatch.setattr(settings, "vad_silence_ms", 500)


def face(track=1, t=T, bearing=0.0, px=150, yaw=0.0, name=None):
    return focus.FaceObs(track=track, t=t, bearing_deg=bearing, width_px=px,
                         yaw_deg=yaw, name=name)


def _pcm(name: str) -> bytes:
    with wave.open(str(FIXTURES / name)) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        return w.readframes(w.getnframes())


# ==================== 1. the name inside a call ====================

def test_the_name_opens_an_out_of_view_window_and_it_closes(on, rows):
    """Look and Talk: nobody in front of a camera-opened call is denied —
    until the name is called; then for FOCUS_WAKE_WINDOW_S only."""
    focus.observe_faces([], T)                              # camera alive, nobody
    assert focus.may_open(T + 0.1) == (False, focus.NO_FACE)
    info = focus.heard_name(T + 0.2)
    assert info["in_view"] is False and info["camera"] is True
    assert focus.may_open(T + 0.3) == (True, focus.OK)
    opened = [r for r in rows if r["event"] == "focus_open"][-1]
    assert opened["called"] is True
    end = T + 0.2 + settings.focus_wake_window_s
    focus.observe_faces([], end)
    assert focus.may_open(end - 0.1)[0], "still inside the window"
    assert focus.may_open(end + 0.1) == (False, focus.NO_FACE), "the window closed"


def test_the_window_waives_rule_two_only(on):
    """Faces in view are still judged: a caller behind a customer whose mouth
    is still is 'faces in front are silent'; a confident voice from elsewhere
    is still elsewhere."""
    focus.observe_faces([face(t=T)], T)
    focus.observe_mouth(1, T - 5, 0.9)                     # proven mouth
    focus.observe_mouth(1, T + 0.2, 0.05)                  # ...silent now
    focus.heard_name(T + 0.2)
    assert focus.may_open(T + 0.3) == (False, focus.SILENT_FACES)

    focus._forget_everything()
    focus.observe_faces([face(t=T, bearing=0.0)], T)
    focus.heard_name(T)
    focus.observe_doa(T + 0.1, 60.0, 0.9)
    assert focus.may_open(T + 0.2) == (False, focus.ELSEWHERE)


def test_the_window_lets_a_name_through_a_dead_camera(on):
    """The name is as deliberate as the button, which is heard from outside
    the lens; "no camera" is rule 2's own branch."""
    assert focus.may_open(T) == (False, focus.NO_CAMERA)
    info = focus.heard_name(T)
    assert info["camera"] is False
    assert focus.may_open(T + 0.1)[0]


def test_the_window_belongs_to_one_conversation(on):
    focus.observe_faces([], T)
    focus.heard_name(T)
    focus.reset()
    focus.observe_faces([], T + 0.1)
    assert focus.may_open(T + 0.2) == (False, focus.NO_FACE)


def test_heard_name_gives_the_side_and_logs_numbers_only(on, rows):
    focus.observe_faces([face(t=T, name="แก้ว")], T)
    for i in range(5):
        focus.observe_doa(T + 0.2 * i, 32.0 + i, 0.8)
    info = focus.heard_name(T + 1.0)
    assert info["in_view"] is True and info["bearing"] == pytest.approx(34.0)
    row = [r for r in rows if r["event"] == "focus_wake"][0]
    assert row["doa"] == 34.0 and row["in_view"] is True
    assert "แก้ว" not in repr(row) and "name" not in row and "text" not in row


def test_off_the_name_does_nothing(rows):
    assert focus.heard_name(T)["window_s"] == 0.0
    assert focus.may_open(T + 0.1) == (True, focus.OFF)
    assert rows == []


class Scripted:
    def __init__(self, answers):
        self.answers = list(answers)

    def accept_waveform(self, samples):
        pass

    def is_speech_detected(self):
        return self.answers.pop(0) if self.answers else False


LOUD = (int(0.1 * 32767)).to_bytes(2, "little", signed=True) * 160
QUIET = b"\x30\x00" * 160


def test_emma_and_the_question_in_one_breath_keeps_its_head(on):
    """The gate re-asks on every frame of a denied utterance; the name opens
    the window mid-utterance and the segment starts with the denied words
    still in its pre-roll."""
    from app.vad_gate import VadGate

    gate = VadGate(Scripted([False, True, True]), prefix_padding_ms=100)
    focus.observe_faces([], time.monotonic())
    assert gate.feed(QUIET) == []
    assert gate.feed(LOUD) == [], "nobody in front: denied"
    focus.heard_name(time.monotonic())
    actions = gate.feed(LOUD)
    assert [k for k, _ in actions] == ["start", "audio", "audio"]
    assert actions[1][1] == QUIET + LOUD


def test_the_call_bar_is_its_own_knob_with_the_beam_asymmetry_kept(monkeypatch):
    """FOCUS_WAKE_THRESHOLD scales the spellings like WAKE_THRESHOLD does,
    and the standby's absolute WAKE_SPELLINGS bars do not leak into it."""
    from app import wake

    monkeypatch.setattr(settings, "wake_spellings", "EMMA:0.01,IMMA,A MA")
    lines = wake._encode_keyword("emma", base=0.2).splitlines()
    bars = {line.split("@")[0].split("#")[1].strip(): line for line in lines}
    assert set(bars) == {"0.10", "0.20", "0.16"}, lines      # 0.5x, 1x, 0.8x
    standby = wake._encode_keyword("emma")
    assert "#0.01" in standby, "the standby path is unchanged"


class _StubSpotter:
    """sherpa's KeywordSpotter shape: one hit after `after` decodes."""

    def __init__(self, after=None):
        self.after = after
        self.decodes = 0
        self.ready = 0

    def create_stream(self):
        return types.SimpleNamespace(accept_waveform=lambda rate, samples: None)

    def is_ready(self, stream):
        self.ready += 1
        return self.ready % 2 == 1

    def decode_stream(self, stream):
        self.decodes += 1

    def get_result(self, stream):
        return "EMMA" if self.after is not None and self.decodes == self.after else ""

    def reset_stream(self, stream):
        pass


def test_the_call_ear_reports_its_own_cost():
    from app import call_wake

    ears = call_wake.CallEars(_StubSpotter(after=2))
    second = QUIET * 100
    hits = [ears.feed(second) for _ in range(4)]
    assert hits == [None, "EMMA", None, None]
    cost = ears.report()
    assert cost["audio_s"] == 4.0
    assert cost["hits"] == 1 and cost["ms_per_s"] is not None
    assert set(cost) == {"audio_s", "ms_per_s", "hits"}, "numbers only"


@needs_model
def test_the_call_ear_hears_a_thai_mouth_and_not_the_imm_trap(monkeypatch):
    """The real detector, at the in-call bar, on 20 ms frames as the page
    sends them — and with WAKE_ENABLED off, as on the robot."""
    from app import call_wake

    assert settings.wake_enabled is False
    spotter = call_wake.load()
    assert spotter is not None

    def hits(name):
        ears = call_wake.CallEars(spotter)
        pcm = _pcm(name)
        return [h for i in range(0, len(pcm), 640) if (h := ears.feed(pcm[i:i + 640]))]

    assert hits("emma_thai_f.wav") == ["EMMA"]
    assert hits("emma_sentence.wav") == ["EMMA"]
    assert hits("other_imm_trap.wav") == []
    assert hits("other.wav") == []


@needs_model
def test_the_call_bar_moves_without_touching_the_standby_bar(monkeypatch):
    from app import call_wake

    monkeypatch.setattr(settings, "focus_wake_threshold", 0.95)
    ears = call_wake.CallEars(call_wake.load())
    pcm = _pcm("emma_thai_f.wav")
    assert not [i for i in range(0, len(pcm), 640) if ears.feed(pcm[i:i + 640])]
    assert settings.wake_threshold == 0.10
    text = (Path(settings.wake_model_dir) / call_wake.KEYWORDS_FILE).read_text(encoding="utf-8")
    assert "#0.47 @EMMA" in text or "#0.48 @EMMA" in text, text   # 0.95 x 0.5


# ---- the session's ear ----

class _Gate:
    in_speech = False


class _Provider:
    input_sample_rate = 16000

    def __init__(self):
        self._vad_gate = _Gate()
        self._speech_ended_at = None
        self._replied_since_end = True
        self.sent: list[str] = []
        self.audio_at_window: list[bool] = []

    async def send_text(self, text):
        self.sent.append(text)

    async def send_audio(self, pcm):
        self.audio_at_window.append(focus._called_at(time.monotonic()))


class _Ears:
    def __init__(self, hit_on=1):
        self.n = 0
        self.hit_on = hit_on

    def feed(self, pcm):
        self.n += 1
        return "EMMA" if self.n == self.hit_on else None


def _session(summoned=True, profile="joke"):
    from app import session as session_module

    sess = session_module.VoiceSession(None, provider_name="gemini", profile=profile,
                                       summoned=summoned)
    sess._ears_closed_until = None
    sess.provider = _Provider()
    return sess


class _WS:
    def __init__(self, frames):
        self.frames = list(frames)
        self.out: list[dict] = []

    async def receive(self):
        if self.frames:
            return {"type": "websocket.receive", "bytes": self.frames.pop(0)}
        return {"type": "websocket.disconnect"}

    async def send_text(self, text):
        self.out.append(json.loads(text))


def test_the_ear_hears_the_frame_before_the_gate_does(on, monkeypatch):
    """The hit must open the window before the gate is asked about the same
    frame — otherwise "Emma, ..." in one breath loses its question."""
    sess = _session()
    sess.ws = _WS([LOUD])
    sess._call_ears = _Ears(hit_on=1)
    focus.observe_faces([], time.monotonic())
    asyncio.run(sess._browser_to_provider())
    assert sess.provider.audio_at_window == [True]


def test_own_voice_saying_her_name_opens_nothing(on, monkeypatch, rows):
    from app import display

    monkeypatch.setattr(display, "remaining_lead", lambda: 2.0)
    sess = _session()
    sess._call_ears = _Ears(hit_on=1)
    sess._hear_name(LOUD)
    assert not focus._called_at(time.monotonic())
    assert [r for r in rows if r["event"] == "focus_wake"] == [
        {"event": "focus_wake", "ignored": "own_voice"}]


def test_the_ear_exists_only_where_it_can_change_anything(on, monkeypatch):
    from app import call_wake

    loads = []
    monkeypatch.setattr(call_wake, "load", lambda: loads.append(1) or _StubSpotter())

    def opened(summoned=True, gate=True):
        sess = _session(summoned=summoned)
        if not gate:
            sess.provider._vad_gate = None

        async def go():
            sess._open_call_ears(sess.provider)
            if sess._ears_task is not None:
                await sess._ears_task
        asyncio.run(go())
        return sess._call_ears is not None

    assert not opened(), "FOCUS_WAKE off (the default): no ear, no load"
    assert loads == []
    monkeypatch.setattr(settings, "focus_wake", True)
    assert opened()
    assert isinstance(_session()._call_ears, type(None)), "nothing until a call opens it"
    assert not opened(summoned=False), "a person-started call is heard out of view already"
    assert not opened(gate=False), "no local gate: focus is never asked"
    monkeypatch.setattr(settings, "focus_require_face", False)
    assert not opened(), "no face rule, nothing for the name to waive"
    monkeypatch.setattr(settings, "focus_require_face", True)
    monkeypatch.setattr(settings, "focus_enabled", False)
    assert not opened()
    assert len(loads) == 1


# ==================== 2. the invitation ====================

@pytest.fixture
def live(on, monkeypatch):
    """A session that is `_active`, so the real events.announce delivers."""
    from app import session as session_module

    sess = _session()
    monkeypatch.setattr(session_module, "_active", sess)
    monkeypatch.setattr(settings, "focus_invite", True)
    return sess


def _run_invite(sess, bearing=30.0, why="name", before=None, heard=True):
    """`heard`: the caller's words after the name reached the model (a
    transcript after the hit). A name with nothing heard after it is a
    false fire or a caller who said no more — no invitation (review 10 Oct)."""
    async def go():
        sess._schedule_invite(bearing, why)
        if heard and getattr(sess, "_invite_at", None) is not None:
            sess._last_heard_at = sess._invite_at + 0.01
        if before is not None:
            before()
        if sess._invite_task is not None:
            await sess._invite_task
    asyncio.run(go())


def test_a_caller_out_of_view_is_invited_once_through_announce(live, rows):
    focus.observe_faces([], time.monotonic())
    _run_invite(live)
    (text,) = live.provider.sent
    assert "เชิญมายืนตรงหน้า" in text and "ทางซ้ายของหุ่น" in text
    assert "ห้ามพูดถึงกล้อง" in text and "ภาษาเดียวกับ" in text
    assert [r for r in rows if r["event"] == "announce"][-1]["source"] == "focus_invite"


def test_a_caller_straight_ahead_gets_no_side(live):
    focus.observe_faces([], time.monotonic())
    _run_invite(live, bearing=5.0)
    assert "ทางซ้าย" not in live.provider.sent[0] and "ทางขวา" not in live.provider.sent[0]
    focus._invited["t"] = None                             # the rate limit is the robot's
    live.turn_idle.set()                                   # the first one was answered
    _run_invite(live, bearing=-40.0, why="voice")
    assert "ทางขวาของหุ่น" in live.provider.sent[1] and "มีคนพูดกับหุ่น" in live.provider.sent[1]


def test_somebody_who_stepped_in_is_not_invited(live, rows):
    focus.observe_faces([], time.monotonic())
    _run_invite(live, before=lambda: focus.observe_faces([face(t=time.monotonic())],
                                                         time.monotonic()))
    assert live.provider.sent == []
    assert [r for r in rows if r["event"] == "announce"][-1]["reason"] == "no longer relevant"


def test_no_invitation_into_a_dead_camera(live):
    _run_invite(live)                                   # no frame ever
    assert live.provider.sent == []


def test_invitations_are_rate_limited_and_one_at_a_time(live, rows):
    focus.observe_faces([], time.monotonic())
    _run_invite(live)
    _run_invite(live)
    assert len(live.provider.sent) == 1
    assert [r for r in rows if r["event"] == "focus_invite"][-1]["reason"] == "rate"


def test_the_invitation_waits_for_the_guest_and_their_owed_answer(live, monkeypatch):
    """events.announce cannot see the gap between a guest's activity_end and
    the first chunk of the reply; `_quiet_moment` does."""
    from app import display

    async def nothing_queued(max_wait=0.0, then_pause=0.0):
        return 0.0                       # announce would otherwise pause 0.4 s

    monkeypatch.setattr(display, "wait_until_heard", nothing_queued)
    focus.observe_faces([], time.monotonic())
    gate, provider = live.provider._vad_gate, live.provider
    gate.in_speech = True
    seen = {}

    async def go():
        live._schedule_invite(30.0, "name")
        await asyncio.sleep(0.3)
        seen["while_speaking"] = list(provider.sent)
        gate.in_speech = False
        provider._speech_ended_at = time.monotonic()      # finished; reply owed
        provider._replied_since_end = False
        await asyncio.sleep(0.3)
        seen["while_owed"] = list(provider.sent)
        provider._replied_since_end = True                # the answer started
        live._last_heard_at = time.monotonic()            # ...to words it heard
        focus.observe_faces([], time.monotonic())
        await live._invite_task

    asyncio.run(go())
    assert seen == {"while_speaking": [], "while_owed": []}
    assert len(provider.sent) == 1


def _no_page_lead(monkeypatch):
    """The robot face (voice-preview.html) never sends `audio_lead`: the
    page's clock reads 0 there, whatever is queued."""
    from app import display

    monkeypatch.setattr(display, "remaining_lead", lambda: 0.0)

    async def nothing_queued(max_wait=0.0, then_pause=0.0):
        return 0.0

    monkeypatch.setattr(display, "wait_until_heard", nothing_queued)


def test_the_invitation_waits_for_her_answer_to_be_heard_on_the_robot_face(live, monkeypatch):
    """Review 10 Oct: the robot face reports no audio lead, so announce's wait
    saw nothing queued and the invitation went in while her answer to the
    caller was still being generated — client content mid-reply interrupts
    it. The server's own playback clock (`_play_until`) is what can see it."""
    _no_page_lead(monkeypatch)
    focus.observe_faces([], time.monotonic())
    seen = {}

    async def go():
        live._play_until = time.monotonic() + 0.6        # her answer, still playing
        live._schedule_invite(30.0, "name")
        live._last_heard_at = live._invite_at + 0.01
        await asyncio.sleep(0.5)
        seen["while_she_speaks"] = list(live.provider.sent)
        focus.observe_faces([], time.monotonic())
        await live._invite_task

    asyncio.run(go())
    assert seen == {"while_she_speaks": []}
    assert len(live.provider.sent) == 1


def test_an_invitation_still_blocked_by_her_voice_is_dropped_not_sent(live, monkeypatch, rows):
    _no_page_lead(monkeypatch)
    monkeypatch.setattr(live, "QUIET_WAIT_S", 0.2)
    focus.observe_faces([], time.monotonic())
    live._play_until = time.monotonic() + 5.0
    _run_invite(live)
    assert live.provider.sent == []
    assert [r for r in rows if r["event"] == "announce"][-1]["reason"] == "no longer relevant"


def test_the_server_clock_runs_without_the_array(on):
    """`_note_playback` used to run only with ARRAY_AUDIO — engagement had
    no clock of her voice on the robot face at all."""
    from app.providers.base import ProviderEvent

    sess = _session()
    assert getattr(sess, "_array_tap", None) is None
    chunk = b"\x00\x00" * 4800                             # 0.2 s at 24 kHz

    class Speaking:
        output_sample_rate = 24000

        async def events(self):
            yield ProviderEvent(kind="audio", audio=chunk)
            yield ProviderEvent(kind="audio", audio=chunk)

    class Out:
        async def send_bytes(self, data):
            pass

        async def send_text(self, text):
            pass

    sess.provider = Speaking()
    sess.ws = Out()
    start = time.monotonic()
    asyncio.run(sess._provider_to_browser())
    assert sess._play_until >= start + 0.4 - 0.01
    assert sess._she_is_audible(time.monotonic())


def test_her_spoken_turn_opens_the_reply_window_from_the_server_clock(on, monkeypatch):
    """FOCUS_REPLY_WINDOW_S counts from when her voice has PLAYED, read off
    the server's playback clock: the robot face reports no audio lead, so
    `remaining_lead` alone would start the window while she still speaks."""
    from app.providers.base import ProviderEvent

    _no_page_lead(monkeypatch)
    monkeypatch.setattr(settings, "focus_reply_window_s", 10.0)
    sess = _session()
    chunk = b"\x00\x00" * 24000                           # 1 s at 24 kHz

    class Speaking:
        output_sample_rate = 24000

        async def events(self):
            yield ProviderEvent(kind="audio", audio=chunk)
            yield ProviderEvent(kind="turn_complete")

    class Out:
        async def send_bytes(self, data):
            pass

        async def send_text(self, text):
            pass

    sess.provider = Speaking()
    sess.ws = Out()
    start = time.monotonic()
    asyncio.run(sess._provider_to_browser())
    assert focus._reply["until"] >= start + 1.0 + 10.0 - 0.05
    focus.observe_faces([], time.monotonic())
    assert focus.may_open(start + 10.5) == (True, focus.REPLY)


def test_her_own_name_on_the_server_clock_opens_nothing(on, monkeypatch, rows):
    """The own-voice guard read only the page's lead — 0 on the robot face,
    the one page this ear runs for."""
    _no_page_lead(monkeypatch)
    sess = _session()
    sess._play_until = time.monotonic() + 1.0
    sess._call_ears = _Ears(hit_on=1)
    sess._hear_name(LOUD)
    assert not focus._called_at(time.monotonic())
    assert {"event": "focus_wake", "ignored": "own_voice"} in rows


def test_a_denied_voice_is_somebody_talking(on):
    """A voice focus refused never opens a segment; `_guest_speaking` read
    only the gate's open segment, so the caller beside the lens — the one
    being invited — was "silent" and could be talked over."""
    sess = _session()
    focus.observe_faces([], time.monotonic())
    assert sess._guest_speaking() is False
    assert focus.may_open(time.monotonic())[0] is False   # no face in front
    assert sess._guest_speaking() is True
    focus._voice_at[0] = time.monotonic() - sess.VOICE_RECENT_S - 0.1
    assert sess._guest_speaking() is False, "a pause is a pause"


def test_a_name_with_nobody_heard_after_it_invites_nobody(live, rows):
    """A false fire of the in-call ear (or a caller who said nothing more):
    the window is all it gets, no sentence to an empty room."""
    focus.observe_faces([], time.monotonic())
    _run_invite(live, heard=False)
    assert live.provider.sent == []
    assert [r for r in rows if r["event"] == "announce"][-1]["reason"] == "no longer relevant"


def test_the_rate_limit_is_the_robots_not_the_lines(live, rows):
    """The standing line rings a fresh line seconds after the last one ends:
    a per-line limit restarted with every ring."""
    from app import session as session_module

    focus.observe_faces([], time.monotonic())
    _run_invite(live)
    assert len(live.provider.sent) == 1
    fresh = _session()
    session_module._active = fresh
    focus.observe_faces([], time.monotonic())
    _run_invite(fresh)
    assert fresh.provider.sent == []
    assert [r for r in rows if r["event"] == "focus_invite"][-1]["reason"] == "rate"


def test_no_voice_invitation_on_a_line_nobody_has_talked_on(on, monkeypatch):
    """A virgin standing line (all day, nobody greeted): voices denied again
    and again from one side are the chat beside the robot. The invitation
    un-virgins the line, the idle rule ends it, the standing line re-rings —
    an invitation into the same chat every half minute."""
    monkeypatch.setattr(settings, "focus_invite", True)
    monkeypatch.setattr(settings, "focus_invite_denied_n", 2)
    sess = _session()
    asked = []
    monkeypatch.setattr(focus, "calling_out_of_view", lambda t, n: asked.append(n) or 30.0)
    invited = []
    monkeypatch.setattr(sess, "_schedule_invite", lambda b, why: invited.append(why))
    monkeypatch.setattr(sess, "ENGAGE_TICK_S", 0.0)

    async def ticks(n=5):
        task = asyncio.create_task(sess._engagement_watch())
        for _ in range(n):
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    sess._greeting_turn_seen = sess._ever_heard = False
    asyncio.run(ticks())
    assert asked == [] and invited == []
    sess._greeting_turn_seen = True                       # a conversation now
    asyncio.run(ticks())
    assert invited and set(invited) == {"voice"}


def test_a_hit_with_nobody_in_front_asks_for_the_invitation(live, monkeypatch):
    calls = []
    monkeypatch.setattr(live, "_schedule_invite", lambda b, why: calls.append((b, why)))
    focus.observe_faces([], time.monotonic())
    live._call_ears = _Ears(hit_on=1)
    live._hear_name(LOUD)
    assert calls == [(None, "name")]
    focus.observe_faces([face(t=time.monotonic())], time.monotonic())
    live._call_ears = _Ears(hit_on=1)
    live._hear_name(LOUD)
    assert len(calls) == 1, "somebody in front: nobody to invite"
    monkeypatch.setattr(settings, "focus_invite", False)
    focus._forget_everything()
    focus.observe_faces([], time.monotonic())
    live._call_ears = _Ears(hit_on=1)
    live._hear_name(LOUD)
    assert len(calls) == 1, "FOCUS_INVITE off"


def _denied(t, bearing):
    if bearing is not None:
        focus.observe_doa(t, bearing, 0.9)
    focus.observe_faces([], t)
    focus.may_open(t)


def test_a_voice_denied_again_and_again_from_one_place_is_a_caller(on):
    for i, b in enumerate((28.0, 31.0, 35.0)):
        _denied(T + 2.0 * i, b)
    assert focus.calling_out_of_view(T + 4.1, 3) == pytest.approx(31.0)
    assert focus.calling_out_of_view(T + 4.2, 3) is None, "one caller, one invitation"


def test_chatter_without_a_bearing_or_from_two_places_is_not(on):
    for i in range(3):
        _denied(T + 2.0 * i, None)
    assert focus.calling_out_of_view(T + 4.1, 3) is None, "no bearing: chatting looks the same"
    focus._forget_everything()
    for i, b in enumerate((-40.0, 30.0, -38.0)):
        _denied(T + 2.0 * i, b)
    assert focus.calling_out_of_view(T + 4.1, 3) is None
    assert focus.calling_out_of_view(T + 4.1, 0) is None, "0 = off"


def test_one_long_denied_utterance_is_one_not_three(on):
    focus.observe_doa(T, 30.0, 0.9)
    for i in range(300):                               # 6 s of 20 ms frames
        focus.observe_doa(T + i * 0.02, 30.0, 0.9)
        focus.observe_faces([], T + i * 0.02)
        focus.may_open(T + i * 0.02)
    assert focus.calling_out_of_view(T + 6.0, 3) is None


# ==================== 3. full engagement ====================

def _main_is_track_one(t0=T, voice=False):
    """Track 1 speaks two segments (the main person); track 2 stands beside."""
    for seg, start in ((1, t0), (2, t0 + 3)):
        focus.observe_faces([face(1, start, 0.0), face(2, start, 30.0)], start)
        focus.observe_mouth(1, start + 0.5, 0.9)
        focus.observe_mouth(2, start + 0.5, 0.05)
        focus.segment_closed(seg, start, start + 2.0)
        if voice:
            focus.observe_voice(seg, start, start + 2.0, cluster=1, confidence=0.9)
    focus.observe_mouth(2, t0 - 10, 0.9)                 # track 2's mouth is proven


def _companion_speaks(t):
    focus.observe_faces([face(1, t, 0.0), face(2, t, 30.0)], t)
    focus.observe_mouth(1, t, 0.05)
    focus.observe_mouth(2, t, 0.9)


def test_full_mode_ignores_the_companion_while_the_main_person_is_there(on, monkeypatch, rows):
    _main_is_track_one()
    t = T + 7
    _companion_speaks(t)
    assert focus.may_open(t + 0.1) == (True, focus.OK), "semi: a second customer joins"
    monkeypatch.setattr(settings, "focus_engage_mode", "full")
    assert focus.may_open(t + 0.1) == (False, focus.NOT_MAIN)
    deny = [r for r in rows if r["event"] == "focus_deny"][-1]
    assert deny["main_person"] == 1 and deny["main_mouth"] == 0.05
    focus.observe_mouth(1, t + 0.2, 0.9)                  # the main person speaks
    assert focus.may_open(t + 0.3)[0]


def test_full_mode_hears_the_main_persons_direction_only(on, monkeypatch):
    _main_is_track_one()
    t = T + 7
    focus.observe_faces([face(1, t, 0.0), face(2, t, 30.0)], t)
    focus._mouth.clear()
    focus._moved.clear()                                   # no mouth input at all
    focus.observe_doa(t, 30.0, 0.9)                        # the companion's side
    assert focus.may_open(t + 0.1)[0], "semi: near a face"
    monkeypatch.setattr(settings, "focus_engage_mode", "full")
    assert focus.may_open(t + 0.1) == (False, focus.NOT_MAIN)


def test_full_mode_has_no_second_customer_joining_by_voice(on, monkeypatch):
    _main_is_track_one(voice=True)
    t = T + 7
    focus.observe_faces([face(1, t, 0.0), face(2, t, 30.0)], t)
    focus.segment_closed(3, t, t + 2.0)
    focus.observe_voice(3, t, t + 2.0, cluster=2, confidence=0.9)
    t2 = t + 2.5
    focus.observe_faces([face(1, t2, 0.0), face(2, t2, 30.0)], t2)
    focus._mouth.clear()
    focus._moved.clear()
    focus.observe_mouth(2, t2, 0.9)
    focus.observe_mouth(2, t2 - 9, 0.9)
    assert focus.may_open(t2 + 0.1)[0], "semi: a moving mouth in front reopens"
    monkeypatch.setattr(settings, "focus_engage_mode", "full")
    assert focus.may_open(t2 + 0.1) == (False, focus.ANOTHER_VOICE)


def test_full_mode_releases_when_the_main_person_leaves(on, monkeypatch, rows):
    monkeypatch.setattr(settings, "focus_engage_mode", "full")
    _main_is_track_one()
    t = T + 7
    focus.observe_faces([face(2, t, 30.0)], t)            # track 1 is gone
    focus.observe_mouth(2, t, 0.9)
    t_left = t + settings.focus_hold_s + 0.5
    focus.observe_faces([face(2, t_left, 30.0)], t_left)
    focus.observe_mouth(2, t_left, 0.9)
    assert focus.may_open(t_left + 0.1)[0], "anybody in the zone may engage now"
    assert [r for r in rows if r["event"] == "focus_release"] == [
        {"event": "focus_release", "ordinal": 1}]
    # The companion becomes main after two segments of their own; the old
    # main person, back in view, is now the one ignored.
    for seg, start in ((10, t_left + 1), (11, t_left + 4)):
        focus.observe_faces([face(2, start, 30.0)], start)
        focus.observe_mouth(2, start + 0.5, 0.9)
        focus.segment_closed(seg, start, start + 2.0)
    t3 = t_left + 7
    focus.observe_faces([face(1, t3, 0.0), face(2, t3, 30.0)], t3)
    focus.observe_mouth(1, t3, 0.9)
    focus.observe_mouth(1, t3 - 9, 0.9)
    focus.observe_mouth(2, t3, 0.05)
    assert focus.may_open(t3 + 0.1) == (False, focus.NOT_MAIN)
    assert focus.snapshot()["main_person"] == 2


def test_full_mode_without_a_main_person_is_semi(on, monkeypatch):
    monkeypatch.setattr(settings, "focus_engage_mode", "full")
    t = T
    _companion_speaks(t)
    focus.observe_mouth(2, t - 9, 0.9)
    assert focus.may_open(t + 0.1)[0], "nobody is main yet: anybody in front"


def test_an_unknown_mode_is_semi_never_stricter(monkeypatch):
    for value in ("", "FULL ", "fully", "Semi", None):
        monkeypatch.setattr(settings, "focus_engage_mode", value)
        assert focus.engage_mode() == ("full" if value == "FULL " else "semi"), value


def test_semi_rows_carry_none_of_the_new_fields(on, rows):
    """The robot today (semi, no wake): the turn-log rows are what they were."""
    _main_is_track_one()
    _companion_speaks(T + 7)
    focus.may_open(T + 7.1)
    for row in rows:
        for key in ("main_person", "main_mouth", "doa_off_main", "called", "released"):
            assert key not in row, row


# ==================== 4. attention ====================

def test_attention_follows_whoever_speaks(on):
    t = T
    focus.observe_faces([face(1, t, -10.0, px=200), face(2, t, 25.0, px=120)], t)
    assert focus.attention(t) == {"bearing": -10.0, "source": "face"}, "the nearest face"
    focus.observe_mouth(2, t - 5, 0.9)
    focus.observe_mouth(2, t, 0.8)
    assert focus.attention(t + 0.1) == {"bearing": 25.0, "source": "mouth"}


def test_attention_turns_to_a_voice_outside_the_lens(on):
    focus.observe_faces([], T)
    focus.observe_doa(T, -40.0, 0.9)
    assert focus.attention(T + 0.1) == {"bearing": -40.0, "source": "doa"}


def test_attention_stays_on_the_last_speaker_then_returns_to_centre(on):
    focus.observe_faces([face(1, T, -10.0, px=200), face(2, T, 25.0, px=120)], T)
    focus.observe_mouth(2, T - 5, 0.9)
    focus.observe_mouth(2, T + 0.5, 0.9)
    focus.segment_closed(1, T, T + 2.0)
    t = T + 3.0
    focus.observe_faces([face(1, t, -10.0, px=200), face(2, t, 25.0, px=120)], t)
    assert focus.attention(t) == {"bearing": 25.0, "source": "label"}
    later = t + 5.0
    focus.observe_faces([], later)
    assert focus.attention(later) == {"bearing": None, "source": None}


def test_attention_in_full_mode_stays_with_the_main_person(on, monkeypatch):
    monkeypatch.setattr(settings, "focus_engage_mode", "full")
    _main_is_track_one()
    _companion_speaks(T + 7)
    assert focus.attention(T + 7.1) == {"bearing": 0.0, "source": "main"}


def test_attention_off_is_centre(rows):
    focus.observe_faces([face(t=T)], T)
    assert focus.attention(T) == {"bearing": None, "source": None}


def test_the_cue_goes_to_the_robot_face_only_and_only_when_it_moved(on, monkeypatch):
    from app import robot_face_link

    sess = _session()
    sess.ws = _WS([])
    sent = sess.ws.out
    is_face = {"v": False}
    monkeypatch.setattr(robot_face_link, "is_robot_face", lambda s: is_face["v"])
    t = time.monotonic()
    focus.observe_faces([face(1, t, 10.0)], t)
    asyncio.run(sess._send_attention(t))
    assert sent == [], "a desktop page never gets it"
    is_face["v"] = True
    for bearing in (10.0, 11.5, 14.0):
        focus.observe_faces([face(1, t, bearing)], t)
        asyncio.run(sess._send_attention(t))
    focus.observe_faces([], t + 5)
    asyncio.run(sess._send_attention(t + 5))
    asyncio.run(sess._send_attention(t + 5.25))
    assert sent == [{"type": "attention", "bearing": 10.0, "source": "face"},
                    {"type": "attention", "bearing": 14.0, "source": "face"},
                    {"type": "attention", "bearing": None, "source": None}]


def test_no_watcher_task_unless_a_switch_asks_for_one(on, monkeypatch):
    sess = _session()
    assert sess._wants_engagement_watch() is False
    monkeypatch.setattr(settings, "focus_attention", True)
    assert sess._wants_engagement_watch() is True
    monkeypatch.setattr(settings, "focus_enabled", False)
    assert sess._wants_engagement_watch() is False, "every engagement switch needs focus"
    monkeypatch.setattr(settings, "focus_enabled", True)
    monkeypatch.setattr(settings, "focus_attention", False)
    monkeypatch.setattr(settings, "focus_goodbye", True)
    assert _session(summoned=False)._wants_engagement_watch() is False
    assert _session()._wants_engagement_watch() is True
    # ...and the race only gets the task through that predicate (the
    # FIRST_COMPLETED lesson: a task that exists is a task that can end it).
    source = (ROOT / "app" / "session.py").read_text(encoding="utf-8")
    creation = source.split("jobs = {up, down, unanswered}", 1)[1].split("asyncio.wait", 1)[0]
    guarded = creation.split("if self._wants_engagement_watch():", 1)
    assert len(guarded) == 2 and "self._engagement_watch()" in guarded[1]
    assert "self._engagement_watch()" not in guarded[0]


def test_the_page_and_the_mascot_turn_toward_the_bearing():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to run the face page's attention handler and "
                    "the mascot's bearing-to-yaw map — neither is being verified")
    result = subprocess.run([node, "tests/client_attention.cjs"], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_page_centres_her_on_every_way_out_of_a_call():
    html = (ROOT / "client" / "voice-preview.html").read_text(encoding="utf-8")
    end_call = html.split("function endCall() {", 1)[1].split("\n    }\n", 1)[0]
    on_close = html.split("ws.onclose = function () {", 1)[1].split("\n      };\n", 1)[0]
    assert "attendFromServer(null);" in end_call
    assert "attendFromServer(null);" in on_close


def test_the_sign_premise_holds_in_the_model_itself():
    """attendYaw turns the Neck by +yaw for a bearing on the robot's left.
    That is "toward the person" only while the GLB faces +z with identity
    bones (her own left is then +x). A rebuilt model that breaks either
    would turn her away from everybody — checked against the file."""
    glb = (ROOT / "client" / "assets" / "emma-3d-lab" / "emma.glb").read_bytes()
    size = struct.unpack("<I", glb[12:16])[0]
    doc = json.loads(glb[20:20 + size])
    nodes = {n.get("name"): n for n in doc["nodes"]}
    for bone in ("Neck", "Head"):
        rotation = nodes[bone].get("rotation")
        assert rotation in (None, [0, 0, 0, 1]), (bone, rotation)
    for name, node in nodes.items():
        if name and name.startswith("Eye") and "mesh" in node:
            accessor = doc["accessors"][doc["meshes"][node["mesh"]]["primitives"][0]
                                        ["attributes"]["POSITION"]]
            assert accessor["min"][2] > 0, "the eyes are on the +z side"
    mascot = (ROOT / "client" / "assets" / "emma-3d-lab" / "mascot.js").read_text(encoding="utf-8")
    assert "neck.rotateY(attendSmooth)" in mascot


def test_the_pointer_gaze_and_the_attention_turn_her_the_same_way():
    """Review 10 Oct: two paths turned the Neck in opposite directions — the
    attention cue +yaw for a person on her left, the pointer gaze -yaw for a
    pointer on the screen's right, which (camera on +z, model facing +z) is
    the same side. The pointer path was the mirrored one (the SVG face that
    moved translate(+--gaze-x) toward the pointer agreed with the attention
    cue; it left with the SVG puppet, 14 Oct). Executes the shipped expression."""
    import re

    mascot = (ROOT / "client" / "assets" / "emma-3d-lab" / "mascot.js").read_text(encoding="utf-8")
    cam = re.search(r"camera\.position\.set\(([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)\)", mascot)
    x, _y, z = (float(v) for v in cam.groups())
    assert z > 0 and abs(x) < z, "the home camera looks at her face from +z"
    assert "gazeX = (event.clientX / innerWidth - .5) * 2;" in mascot   # + = screen right
    turn = re.search(r"neck\.rotateY\(([^()]*smoothX[^()]*)\)", mascot).group(1)
    pointer_right = eval(turn.replace(".45", "0.45"), {}, {"smoothX": 1.0})  # noqa: S307
    assert pointer_right > 0, ("a pointer on the screen's right (her left, +x) must turn "
                               "her toward it — the attention cue's sign: " + turn)


# ==================== 5. the goodbye ====================

def _conversation(sess, t_left, heard_before=5.0):
    """A greeted, heard call whose guest was engaged until `t_left`."""
    sess._greeting_turn_seen = True
    sess._ever_heard = True
    sess._began_at = t_left - 60.0
    sess._last_heard_at = t_left - heard_before
    focus.observe_faces([face(t=t_left)], t_left)


def test_the_guest_walked_away_mid_conversation(on, monkeypatch):
    sess = _session()
    now = time.monotonic()
    after = settings.focus_goodbye_after_s
    _conversation(sess, now - after - 1)
    focus.observe_faces([], now)                           # camera alive, nobody
    assert sess._left_mid_conversation(now) is True


@pytest.mark.parametrize("breaks", ["not_greeted", "never_heard", "still_here", "camera_dead",
                                    "glance", "before_this_call", "heard_since",
                                    "long_silence"])
def test_not_a_walk_away(on, monkeypatch, breaks):
    sess = _session()
    now = time.monotonic()
    after = settings.focus_goodbye_after_s
    t_left = now - after - 1
    _conversation(sess, t_left, heard_before=sess.FOCUS_AWAY_S + 5 if breaks == "long_silence"
                  else 5.0)
    if breaks != "camera_dead":
        focus.observe_faces([], now)
    if breaks == "not_greeted":
        sess._greeting_turn_seen = False
    elif breaks == "never_heard":
        sess._ever_heard = False
    elif breaks == "still_here":
        focus.observe_faces([face(t=now)], now)
    elif breaks == "camera_dead":
        now += focus.CAMERA_STALE_S + 1
    elif breaks == "glance":
        monkeypatch.setattr(settings, "focus_goodbye_after_s", 0.5)   # never under the hold
        focus._forget_everything()
        focus.observe_faces([face(t=now - 1.0)], now - 1.0)
        focus.observe_faces([], now)
    elif breaks == "before_this_call":
        sess._began_at = t_left + 1.0
    elif breaks == "heard_since":
        sess._last_heard_at = now - 1.0
    assert sess._left_mid_conversation(now) is False


def test_the_goodbye_is_said_through_announce_then_the_call_ends(live, rows):
    sess = live
    sess.ws = _WS([])
    now = time.monotonic()
    _conversation(sess, now - 20)
    focus.observe_faces([], now)
    waited = {}

    async def run():
        async def goodbye_turn_completes():
            while not sess.provider.sent:
                await asyncio.sleep(0.01)
            waited["idle_clear_after_send"] = not sess.turn_idle.is_set()
            await asyncio.sleep(0.05)
            sess.turn_idle.set()
        asyncio.get_running_loop().create_task(goodbye_turn_completes())
        return await sess._say_goodbye()

    assert asyncio.run(run()) is True
    assert waited == {"idle_clear_after_send": True}, "announce owns the turn it started"
    assert sess.provider.sent and sess.provider.sent[0].startswith("คนที่คุยอยู่เพิ่งเดินออกไป")
    assert "ห้ามถามคำถาม" in sess.provider.sent[0]
    (out,) = sess.ws.out
    assert out["type"] == "idle_timeout" and 25 <= out["quiet_s"] <= 26
    assert {"event": "focus_goodbye", "ended": True} in rows
    assert [r for r in rows if r["event"] == "idle_timeout"][-1]["why"] == "left"


def _goodbye_plays(sess, seconds):
    """The goodbye's audio, by the server's clock, as the provider streams it."""
    real = sess.provider.send_text

    async def send_text(text):
        await real(text)
        sess._play_until = time.monotonic() + seconds

    sess.provider.send_text = send_text


def test_the_call_ends_after_the_goodbye_is_heard_not_generated(live, rows, monkeypatch):
    """Review 10 Oct: on the robot face (no audio lead) the goodbye's
    `idle_timeout` went out at turn_complete — the page ends the call and
    closes its AudioContext on it, so nobody heard a word."""
    _no_page_lead(monkeypatch)
    sess = live
    sess.ws = _WS([])
    now = time.monotonic()
    _conversation(sess, now - 20)
    focus.observe_faces([], now)
    _goodbye_plays(sess, 0.5)
    sent_at = {}

    async def run():
        async def goodbye_turn_completes():
            while not sess.provider.sent:
                await asyncio.sleep(0.01)
            sent_at["t"] = time.monotonic()
            sess.turn_idle.set()                            # generated at once
        asyncio.get_running_loop().create_task(goodbye_turn_completes())
        ended = await sess._say_goodbye()
        sent_at["end"] = time.monotonic()
        return ended

    assert asyncio.run(run()) is True
    assert sess.ws.out and sess.ws.out[-1]["type"] == "idle_timeout"
    assert sent_at["end"] - sent_at["t"] >= 0.5 + sess.AUDIBLE_TAIL_S - 0.05


def test_coming_back_during_the_goodbye_keeps_the_call(live, rows, monkeypatch):
    _no_page_lead(monkeypatch)
    sess = live
    sess.ws = _WS([])
    now = time.monotonic()
    _conversation(sess, now - 20)
    focus.observe_faces([], now)
    _goodbye_plays(sess, 0.4)

    async def run():
        async def goodbye_turn_completes():
            while not sess.provider.sent:
                await asyncio.sleep(0.01)
            sess.turn_idle.set()
            await asyncio.sleep(0.2)                         # while it plays...
            focus.observe_faces([face(t=time.monotonic())], time.monotonic())
        asyncio.get_running_loop().create_task(goodbye_turn_completes())
        return await sess._say_goodbye()

    assert asyncio.run(run()) is False
    assert sess.provider.sent, "she said it"
    assert sess.ws.out == [], "but the call goes on"
    assert {"event": "focus_goodbye", "ended": False} in rows


def test_no_goodbye_while_a_voice_is_still_at_the_door(on):
    """Their face went (a brochure, a child under the lens, a step aside)
    but a voice reached the robot since — focus denied it, so nothing was
    "heard", and the goodbye was said and the call hung up over it."""
    sess = _session()
    now = time.monotonic()
    after = settings.focus_goodbye_after_s
    t_left = now - after - 1
    _conversation(sess, t_left)
    focus.observe_faces([], now)
    assert sess._left_mid_conversation(now) is True
    focus._voice_at[0] = t_left + 2.0                      # denied, after they went
    assert sess._left_mid_conversation(now) is False
    focus._voice_at[0] = t_left - 2.0                      # before: their last words
    assert sess._left_mid_conversation(now) is True


def test_nobody_gets_a_goodbye_after_coming_back_before_it(live):
    sess = live
    sess.ws = _WS([])
    now = time.monotonic()
    _conversation(sess, now - 20)
    focus.observe_faces([face(t=now)], now)                # back before it was said
    assert asyncio.run(sess._say_goodbye()) is False
    assert sess.provider.sent == [] and sess.ws.out == []


def test_the_watcher_ends_the_call_once_after_the_goodbye(on, monkeypatch):
    monkeypatch.setattr(settings, "focus_goodbye", True)
    sess = _session()
    said = []

    async def goodbye():
        said.append(1)
        return True

    monkeypatch.setattr(sess, "_say_goodbye", goodbye)
    monkeypatch.setattr(sess, "_left_mid_conversation", lambda now: True)
    monkeypatch.setattr(sess, "ENGAGE_TICK_S", 0.0)
    asyncio.run(asyncio.wait_for(sess._engagement_watch(), 2.0))
    assert said == [1] and sess._goodbye_done is True


def test_a_failing_tick_never_ends_the_call(on, monkeypatch):
    """The watcher races under FIRST_COMPLETED: an exception escaping it
    would hang up on the guest."""
    monkeypatch.setattr(settings, "focus_attention", True)
    sess = _session()
    ticks = {"n": 0}

    async def boom(now):
        ticks["n"] += 1
        raise RuntimeError("tick")

    monkeypatch.setattr(sess, "_send_attention", boom)
    monkeypatch.setattr(sess, "ENGAGE_TICK_S", 0.0)

    async def go():
        task = asyncio.create_task(sess._engagement_watch())
        for _ in range(200):
            if ticks["n"] >= 3 or task.done():
                break
            await asyncio.sleep(0.01)
        assert not task.done(), "the watcher ended — the call would have hung up"
        assert ticks["n"] >= 3
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(go())


def test_gaps_in_front_are_measured_for_the_goodbye_bar(on, rows):
    hold = settings.focus_hold_s
    focus.observe_faces([face(t=T)], T)
    focus.observe_faces([face(t=T + hold * 0.5)], T + hold * 0.5)   # a glance: no row
    focus.observe_faces([], T + 2)
    focus.observe_faces([face(t=T + 6.0)], T + 6.0)                 # back after 5.25 s
    assert [r for r in rows if r["event"] == "focus_gap"] == [
        {"event": "focus_gap", "gap_s": round(6.0 - hold * 0.5, 1)}]


def test_gaps_are_not_written_with_focus_off(rows):
    focus.observe_faces([face(t=T)], T)
    focus.observe_faces([face(t=T + 6.0)], T + 6.0)
    assert rows == []


# ==================== the robot's own numbers ====================

def test_the_log_report_shows_the_engagement_numbers(tmp_path, monkeypatch, capsys):
    """The two numbers only the robot can supply — the in-call ear's CPU and
    how long a guest goes missing from the camera while still there — plus
    what the robot said by itself. Numbers only, like every focus line."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "analyze_log_engagement", ROOT / "scripts" / "analyze_log.py")
    al = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(al)
    rows = [
        {"t": "10:00:00", "event": "focus_gap", "gap_s": 2.0},
        {"t": "10:00:10", "event": "focus_gap", "gap_s": 4.0},
        {"t": "10:01:00", "event": "focus_wake", "in_view": False, "camera": True,
         "doa": 30.0, "window_s": 10.0},
        {"t": "10:01:00", "event": "focus_wake", "ignored": "own_voice"},
        {"t": "10:01:05", "event": "announce", "source": "focus_invite", "sent": True},
        {"t": "10:02:00", "event": "focus_release", "ordinal": 1},
        {"t": "10:03:00", "event": "focus_goodbye", "ended": True},
        {"t": "10:03:00", "event": "focus_wake_cpu", "audio_s": 60.0, "ms_per_s": 31.5,
         "hits": 1},
    ]
    path = tmp_path / "2026-01-01.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["analyze_log.py", str(path)])
    assert al.main() == 0
    out = capsys.readouterr().out
    assert "ได้ยินชื่อระหว่างสาย 2 ครั้ง (นอกกล้อง 1, เสียงตัวเอง 1)" in out
    assert "p50 31.5" in out
    assert "เชิญมายืนหน้าหุ่น: พูด 1 / ไม่พูด (สถานการณ์เปลี่ยน) 0" in out
    assert "ปล่อยให้คนอื่นคุย 1 ครั้ง" in out
    assert "กล่าวลาแล้ววางสาย 1 / กลับมาคุยต่อ 0" in out
    assert "n=2  p50 3.0  p90 3.8  สูงสุด 4.0" in out


def test_a_tap_that_stops_her_voice_ends_the_server_playback_clock(on):
    """Review 13 Oct: a tap over her stops playback on the page, which sends
    `played`; Gemini sends no `interrupted` once generation is done, so the
    server's clock kept her "speaking" and queued the next answer behind
    audio nobody heard. `played` ends the clock now."""
    sess = _session()
    sess._play_until = time.monotonic() + 20.0

    class Frames:
        def __init__(self):
            self.items = [{"type": "websocket.receive", "text": json.dumps({"type": "played", "heard_ms": 900})}]

        async def receive(self):
            return self.items.pop(0) if self.items else {"type": "websocket.disconnect"}

        async def send_text(self, text):
            pass

    sess.ws = Frames()
    asyncio.run(sess._browser_to_provider())
    assert sess._play_until <= time.monotonic()
