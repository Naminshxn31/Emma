"""The mic is held while the Live socket is down and being rejoined (app/providers/gemini.py `_hold_mic`, RECONNECT_KEEPS_MIC).
The robot's log of 24 Nov: Google closes a session nothing was sent to for two minutes (1008), 184 times that day, the rejoin
1.2 s at the median and 4.4 s at worst -- and everything said in those seconds was dropped, her name first of all."""
import asyncio

import pytest

from app.config import settings
from app.providers import gemini
from app.providers.gemini import GeminiProvider

LOUD, QUIET = b"\x10\x27" * 320, b"\x00\x00" * 320


class Gate:
    """Opens on a loud frame, closes on the third quiet one; says what it was fed."""

    def __init__(self):
        self.fed, self.open, self.quiet, self.open_rms, self.keep_audio = [], False, 0, 0.1, False

    @property
    def in_speech(self):
        return self.open

    def feed(self, frame):
        self.fed.append(frame)
        loud = any(frame)
        out = []
        if loud and not self.open:
            self.open = True
            out.append(("start", b""))
        if self.open:
            out.append(("audio", frame))
            self.quiet = 0 if loud else self.quiet + 1
            if self.quiet >= 3:
                self.open, self.quiet = False, 0
                out.append(("end", b""))
        return out

    def take_closed(self):
        return None


class Socket:
    def __init__(self):
        self.sent = []

    async def send_realtime_input(self, **kw):
        self.sent.append(next(iter(kw)))


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(settings, "reconnect_keeps_mic", True)
    monkeypatch.setattr(settings, "reply_stall_s", 0.0)
    monkeypatch.setattr(gemini, "_mic_deaf", lambda: False)
    p = GeminiProvider.__new__(GeminiProvider)
    p._session, p._vad_gate, p._closing = None, Gate(), False
    p._event_queue = asyncio.Queue()
    p._note_reopen = p._stall_restart = lambda: None
    p.input_sample_rate = 16000

    async def no_note():
        return False

    p._report_segment = no_note
    p._speech_ended_at, p._replied_since_end = None, False
    return p


def _say(p, *frames):
    async def go():
        for f in frames:
            await p.send_audio(f)
    asyncio.run(go())


def test_a_sentence_begun_while_the_socket_is_down_is_heard_whole(provider):
    _say(provider, QUIET, LOUD, LOUD)                          # down: "Emma, how ..."
    assert provider._vad_gate.fed == []
    provider._session = Socket()
    _say(provider, LOUD, QUIET, QUIET, QUIET)                  # back: "... many units?"
    assert provider._vad_gate.fed == [QUIET, LOUD, LOUD, LOUD, QUIET, QUIET, QUIET]          # in order, once
    assert provider._session.sent[0] == "activity_start" and provider._session.sent[-1] == "activity_end"
    assert provider._session.sent.count("activity_start") == 1 and provider._session.sent.count("audio") == 6
    assert provider._mic_down is None


def test_silence_in_the_gap_stays_unsent(provider):
    _say(provider, *[QUIET] * 50)
    provider._session = Socket()
    _say(provider, QUIET)
    assert provider._session.sent == []


def test_it_holds_so_much_and_no_more(provider, monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(gemini.time, "monotonic", lambda: clock["t"])
    for _ in range(1000):                                      # 20 s of an outage
        clock["t"] += 0.02
        _say(provider, LOUD)
    assert 395 <= len(provider._mic_down) <= 402               # the last 8 s
    provider._session = Socket()
    clock["t"] += 30.0                                         # (and back only much later: that is no sentence any more)
    _say(provider, QUIET)
    assert provider._vad_gate.fed == [QUIET] and provider._mic_down is None


def test_a_deaf_ceremony_holds_silence(provider, monkeypatch):
    monkeypatch.setattr(gemini, "_mic_deaf", lambda: True)
    _say(provider, LOUD)
    assert [f for _, f in provider._mic_down] == [QUIET]


def test_a_socket_lost_mid_sentence_the_new_one_hears_the_sentence_from_its_start(provider):
    provider._session = first = Socket()
    _say(provider, LOUD, LOUD)                                 # "Emma, how many" -- on the old socket
    provider._session = None                                   # (dropped)
    _say(provider, LOUD)                                       # "units" -- held
    provider._session = second = Socket()
    _say(provider, QUIET, QUIET, QUIET)                        # "?" and he stops
    assert first.sent == ["activity_start", "audio", "audio"]
    assert second.sent[0] == "activity_start" and second.sent[-1] == "activity_end"
    assert second.sent.count("activity_start") == 1 and second.sent.count("audio") == 6     # (2 again + 1 held + 3)


def test_the_ceremony_call_alone_gets_its_longer_pre_roll(monkeypatch):
    from app import vad_gate

    made = []
    monkeypatch.setattr(vad_gate, "_make_detector", lambda *a: object())
    monkeypatch.setattr(vad_gate, "VadGate", lambda det, **kw: made.append(kw["prefix_padding_ms"]))
    monkeypatch.setattr(settings, "vad_mode", "local")
    vad_gate.for_session(min_rms=0.02)
    vad_gate.for_session(min_rms=0.02, silence_ms=1500, prefix_padding_ms=1000)
    vad_gate.for_session(min_rms=0.02, silence_ms=1500, prefix_padding_ms=None)
    assert made == [settings.vad_prefix_padding_ms, 1000, settings.vad_prefix_padding_ms]


def test_turned_deaf_during_the_outage_none_of_it_is_heard(provider, monkeypatch):
    _say(provider, LOUD, LOUD)
    monkeypatch.setattr(gemini, "_mic_deaf", lambda: True)
    assert provider._take_mic_down() == [QUIET, QUIET]


def test_off_or_closing_or_without_the_local_gate_is_as_before(provider, monkeypatch):
    provider._closing = True
    _say(provider, LOUD)
    provider._closing, gate = False, provider._vad_gate
    provider._vad_gate = None
    _say(provider, LOUD)
    provider._vad_gate = gate
    monkeypatch.setattr(settings, "reconnect_keeps_mic", False)
    _say(provider, LOUD)
    assert provider._mic_down is None
    provider._session = Socket()
    _say(provider, LOUD)
    assert provider._vad_gate.fed == [LOUD]


def test_the_socket_gone_again_mid_flush_does_not_end_the_call(provider):
    _say(provider, LOUD, LOUD, LOUD)

    class Dead(Socket):
        async def send_realtime_input(self, **kw):
            provider._session = None                           # (the receive loop retired it meanwhile)

    provider._session = Dead()
    _say(provider, LOUD)                                       # nothing raised
    assert provider._mic_down is None and len(provider._vad_gate.fed) == 4


def test_robot_env_turns_it_on():
    from pathlib import Path

    env = (Path(__file__).resolve().parent.parent / "data" / "robot-server" / "robot.env").read_text(encoding="utf-8")
    assert "\nRECONNECT_KEEPS_MIC=true" in env and "\nCATWALK_MUSIC=true" in env and "\nCEREMONY_PREFIX_PADDING_MS=1000" in env
