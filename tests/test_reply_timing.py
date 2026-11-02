"""How long the guest waited, measured on the turns that were slow.

Robot log 28 Oct (staff testing in the floor-1 media room, "บางจังหวะตัวโมเดล
ตอบช้า"): a spoken answer came 0.9-1.7 s after the guest's last word
(end_signal_to_audio_ms, p50 1.36 s) -- but every answer that went through a
tool (search_condo_info -> present_story -> speech) logged end_signal
"unavailable": gemini-3.8 completes a turn BEFORE its tool call and each
tool result opens another, so the turn that finally spoke never had the
guest's end signal. Those were exactly the slow ones (2-4 s, read off the
one-second timestamps), and the metric could not see them.
"""
from __future__ import annotations

import pytest

from app import metrics, turnlog


@pytest.fixture
def observed(monkeypatch):
    rows, now = [], [10.0]
    monkeypatch.setattr(turnlog, "record", lambda event, **fields: rows.append({"event": event, **fields}))
    return metrics.SessionMetrics("gemini", "test", "local", clock=lambda: now[0]), rows, now


def _first_audio(rows):
    return [r for r in rows if r["event"] == "metric_first_audio"][-1]


def test_a_tool_chain_answer_is_timed_from_the_guests_last_word(observed):
    """The 28 Oct shape: empty turn_complete, search, present_story, speech."""
    obs, rows, now = observed
    obs.speech_end("gemini_local_vad_decision")
    now[0] += 0.4
    obs.ensure()                     # 3.8's empty turn before the tool call
    obs.finish()
    now[0] += 0.6
    obs.ensure()                     # search_condo_info
    obs.finish()
    now[0] += 0.9
    obs.ensure()                     # present_story
    obs.finish()
    now[0] += 1.1
    obs.audio()                      # she speaks
    first = _first_audio(rows)
    assert first["end_signal_to_audio_ms"] == pytest.approx(3000)
    assert first["end_signal_source"] == "gemini_local_vad_decision"
    assert first["tool_hops"] == 3


def test_a_direct_answer_is_unchanged(observed):
    obs, rows, now = observed
    obs.speech_end("gemini_local_vad_decision")
    now[0] += 1.2
    obs.audio()
    first = _first_audio(rows)
    assert first["end_signal_to_audio_ms"] == pytest.approx(1200)
    assert "tool_hops" not in first


def test_an_answer_that_spoke_does_not_hand_the_signal_on(observed):
    """The turn after an answer with audio is not the answer again."""
    obs, rows, now = observed
    obs.speech_end("gemini_local_vad_decision")
    obs.audio()
    obs.finish()
    now[0] += 5
    obs.audio()
    assert _first_audio(rows)["end_signal_source"] == "unavailable"


def test_an_interruption_drops_the_carried_signal(observed):
    obs, rows, now = observed
    obs.speech_end("gemini_local_vad_decision")
    obs.ensure()
    obs.finish("interrupted")
    now[0] += 2
    obs.audio()
    assert _first_audio(rows)["end_signal_source"] == "unavailable"


def test_a_turn_of_the_servers_own_is_not_timed_from_the_guest(observed):
    """A dropped bare "ค่ะ" leaves the signal riding; the follow-up question
    the server then sends is not the answer to the guest's words."""
    obs, rows, now = observed
    obs.speech_end("gemini_local_vad_decision")
    obs.ensure()
    obs.finish()                     # nothing reached the speaker
    now[0] += 6
    obs.robot_turn()                 # events.announce sent its text
    obs.audio()
    assert _first_audio(rows)["end_signal_source"] == "unavailable"


def test_announce_tells_the_metrics_it_is_the_servers_turn(monkeypatch):
    """Wired, not only written: events.announce calls robot_turn after its
    text went up (revert the hook and this goes red)."""
    import asyncio
    from types import SimpleNamespace

    from app import display, events
    from app import session as session_module

    called = []

    class Provider:
        async def send_text(self, text):
            called.append(("text", text))

    class Metrics:
        def robot_turn(self):
            called.append(("robot_turn",))

    fake = SimpleNamespace(provider=Provider(), metrics=Metrics(), turn_idle=None)
    monkeypatch.setattr(session_module, "_active", fake)

    async def heard(**_kw):
        return 0.0

    monkeypatch.setattr(display, "wait_until_heard", heard)
    assert asyncio.run(events.announce("hello", source="test")) is True
    assert ("robot_turn",) in called and called.index(("robot_turn",)) > 0


def test_a_tap_is_timed_from_the_tap(monkeypatch):
    """The quick-ask path marks the moment of the tap as the end signal."""
    import inspect

    from app import session as session_module

    src = inspect.getsource(session_module.VoiceSession._quick_ask)
    assert 'speech_end("tap")' in src
    assert src.index('speech_end("tap")') < src.index("await self.provider.send_text(text)")


# -- the call's microphone report judges the window it reports ----------------

def test_speech_level_with_no_new_segment_says_so_not_look_at_gemini(monkeypatch, caplog):
    """28 Oct, 22:56:46-22:57:29: nine 5-s reports of speech-level audio
    (avg 0.012-0.021, frames up to 0.27 rms), not one segment opened, and
    every report said "speech level reached -- if nothing is heard, look at
    Gemini's side" because the call had opened 10 segments earlier. The
    tester then asked whether she could hear them at all."""
    import logging

    from tests.test_greeter import _pump, _session_with_ears

    loud = (int(0.3 * 32767)).to_bytes(2, "little", signed=True) * 320
    sess = _session_with_ears(monkeypatch, segments=10)
    with caplog.at_level(logging.INFO, logger="condo_voice.session"):
        _pump(sess, [loud, loud, loud])
    lines = [r.getMessage() for r in caplog.records if "call audio" in r.getMessage()]
    assert lines and "no segment opened in this window" in lines[-1], lines
    assert "look at Gemini" not in lines[-1]


def test_a_window_that_opened_a_segment_still_points_past_the_microphone(monkeypatch, caplog):
    import logging

    from tests.test_greeter import _pump, _session_with_ears

    loud = (int(0.3 * 32767)).to_bytes(2, "little", signed=True) * 320
    sess = _session_with_ears(monkeypatch, segments=10)
    gate = sess.provider._vad_gate
    original = sess.provider.send_audio

    async def opens(data):
        gate.segments += 1               # this window's audio opened one
        await original(data)

    sess.provider.send_audio = opens
    with caplog.at_level(logging.INFO, logger="condo_voice.session"):
        _pump(sess, [loud, loud, loud])
    lines = [r.getMessage() for r in caplog.records if "call audio" in r.getMessage()]
    assert lines and "look at Gemini" in lines[-1], lines
