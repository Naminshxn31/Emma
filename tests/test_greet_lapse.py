"""The camera's greeting on a line somebody already used (FACE_GREET_LAPSE_S, owner 6 Nov:
"ไม่เห็นทักเลย").

The robot's log that morning: the standing line opened at boot, two staff talking to each other
near the robot were heard 20 s later ("อยากได้กล่อง", 0.93 m by lidar, before the camera had a
frame), she answered once, and from then on every face was "during a live session — not
interrupting": 5 strangers and 2 people she knew, nobody greeted, while the staff said out loud
that nobody had been greeted. The line ends after 120 s of nobody heard; with people talking in
front of it, that never came.

The 30 Sep rule stays: a greeting never cuts into a conversation with her (ประโยคทักทายไม่ให้พูด
แทรก). What changes is what a conversation is: her talking with somebody -- speaking, an answer of
hers on its way, her last words under FACE_GREET_LAPSE_S ago -- not "the line heard something".
Somebody mid-sentence: the greeting waits for them to finish (off the camera loop), and if she
answers them instead, it is dropped as before. 0 = the old rule, byte for byte in behaviour.
"""
from __future__ import annotations

import asyncio
import time
import types

import numpy as np
import pytest

from app import facewatch, greeter
from app.config import settings


def _sighting(kind="known", name="ต้า", group="staff", score=0.62):
    return facewatch.Sighting(kind=kind, name=name, score=score, meta={"group": group})


def _line(**attrs):
    """The robot's standing line, the real session object: staff were heard on it."""
    from app import session as session_module

    sess = session_module.VoiceSession(None, provider_name="gemini", summoned=True)
    sess.provider = types.SimpleNamespace(_vad_gate=types.SimpleNamespace(in_speech=False),
                                          _speech_ended_at=None, _replied_since_end=True)
    sess._ever_heard = True
    for key, value in attrs.items():
        setattr(sess, key, value)
    return sess


@pytest.fixture
def lapse(monkeypatch):
    from app import display, focus

    monkeypatch.setattr(settings, "face_greet_lapse_s", 20.0)
    monkeypatch.setattr(display, "remaining_lead", lambda: 0.0)
    monkeypatch.setattr(focus, "last_voice_at", lambda: None)


# -- what a conversation is -------------------------------------------------------------------

def test_at_zero_the_30_sep_rule_is_untouched(monkeypatch):
    monkeypatch.setattr(settings, "face_greet_lapse_s", 0.0)
    assert greeter._in_conversation(None) is False
    assert greeter._conversation_state(None) is None
    for summoned in (False, True):
        for greeted in (False, True):
            for heard in (False, True):
                line = types.SimpleNamespace(summoned=summoned, _greeting_turn_seen=greeted,
                                             _ever_heard=heard, _play_until=0.0)
                old = heard if (summoned and not greeted) else True
                assert greeter._in_conversation(line) is old, (summoned, greeted, heard)
                assert greeter._conversation_state(line) == ("exchange" if old else None)


def test_staff_talking_to_each_other_do_not_make_a_conversation(lapse):
    """09:04:40, ต้า: the line heard staff, her one answer ended half a minute ago."""
    now = time.monotonic()
    line = _line(_play_until=now - 32)
    assert greeter._conversation_state(line, now) is None
    assert greeter._in_conversation(line) is False


@pytest.mark.parametrize("ago, state", [(-3.0, "exchange"), (5.0, "exchange"), (19.0, "exchange"),
                                        (21.0, None)])
def test_her_own_words_keep_it_a_conversation_for_the_lapse(lapse, ago, state):
    """09:04:12, อาชู่: 4 s after her answer is her conversation, not a greeting's moment.
    A negative `ago` is her voice still playing."""
    now = time.monotonic()
    assert greeter._conversation_state(_line(_play_until=now - ago), now) == state


def test_a_tap_and_a_tool_are_her_answer_on_its_way(lapse):
    now = time.monotonic()
    assert greeter._conversation_state(_line(_quick_ask_at=now - 1.0), now) == "exchange"
    assert greeter._conversation_state(_line(_quick_ask_at=now - 4.0), now) is None
    assert greeter._conversation_state(_line(_tool_calls_open={"c1": now - 12.0}), now) == "exchange"
    assert greeter._conversation_state(_line(_tool_calls_open={"c1": now - 40.0}), now) is None, \
        "a call that never reported back holds the greeting for ever"
    assert greeter._conversation_state(_line(_tool_seen_at=now - 1.0), now) == "exchange"
    assert greeter._conversation_state(_line(_tool_seen_at=now - 4.0), now) is None


def test_somebody_mid_sentence_or_just_finished_is_a_wait_not_a_no(lapse):
    now = time.monotonic()
    line = _line()
    line.provider._vad_gate.in_speech = True
    assert greeter._conversation_state(line, now) == "pending"
    assert greeter._in_conversation(line) is True, "the approach must not set off mid-sentence"
    line.provider._vad_gate.in_speech = False
    line.provider._speech_ended_at, line.provider._replied_since_end = now - 1.0, False
    assert greeter._conversation_state(line, now) == "pending", "her answer may be 1-3 s away"
    line.provider._replied_since_end = True          # a bare "ค่ะ" the server dropped is a reply
    assert greeter._conversation_state(line, now) is None
    line.provider._speech_ended_at, line.provider._replied_since_end = now - 4.0, False
    assert greeter._conversation_state(line, now) is None, "no voice of hers in 4 s: not answering"


def _call(call_id):
    return types.SimpleNamespace(call_id=call_id, data={})


def test_the_tool_calls_follow_the_events_and_a_reconnect_clears_them(lapse):
    line = _line()
    line._tool_mark(_call("a"), opened=True)
    line._tool_mark(_call("b"), opened=True)
    line._tool_mark(_call("a"), opened=False)
    assert list(line._tool_calls_open) == ["b"]
    assert greeter._conversation_state(line) == "exchange"
    line._clear_tools_in_flight("reconnect")
    assert line._tool_calls_open == {}
    line._tool_mark(_call("x"), opened=False)            # a result for nothing open
    assert line._tool_calls_open == {}
    import inspect

    from app import session as session_module

    src = inspect.getsource(session_module.VoiceSession)
    assert src.count("self._tool_mark(event, opened=True)") == 1
    assert src.count("self._tool_mark(event, opened=False)") == 2
    assert src.index('event.kind == "tool_call"') < src.index("self._tool_mark(event, opened=True)")


def test_a_cancelled_call_is_timed_from_its_own_start(lapse, monkeypatch):
    """With TURN_IDLE_TRACKS_TOOLS off (the robot) gemini.py yields no tool_dropped: the call stays
    open. Later calls and results must not keep it alive (review 6 Nov: a count timed from the
    last mark did)."""
    line = _line()
    line._tool_mark(_call("cancelled"), opened=True)
    line._tool_calls_open["cancelled"] -= 40.0            # it opened 40 s ago
    line._tool_mark(_call("later"), opened=True)
    line._tool_mark(_call("later"), opened=False)
    line._tool_seen_at -= 4.0                              # that result was 4 s ago
    assert greeter._conversation_state(line) is None


def test_her_answer_held_by_a_gate_is_still_coming(lapse):
    """A bare-"ค่ะ" or noise gate holds her first audio while it decides: `_replied_since_end` is
    set, her voice clock is not (review 6 Nov: the greeting went out into her answer)."""
    now = time.monotonic()
    line = _line()
    line.provider._speech_ended_at, line.provider._replied_since_end = now - 1.0, True
    line.provider._ack_gate = types.SimpleNamespace(_held=[object()])
    assert greeter._conversation_state(line, now) == "pending"
    line.provider._ack_gate._held = []
    line.provider._noise_gate = types.SimpleNamespace(_held=[object()])
    assert greeter._conversation_state(line, now) == "pending"
    line.provider._noise_gate._held = []
    assert greeter._conversation_state(line, now) is None


def test_a_question_still_waiting_for_her_holds_the_greeting(lapse):
    """REPLY_STALL_S's guard on a worded question: her slow answers (5 of 801 past 3 s)."""
    now = time.monotonic()
    line = _line()
    line.provider._stall_wait = now - 5.0
    assert greeter._conversation_state(line, now) == "pending"
    line.provider._stall_wait = now - greeter.QUIET_WAIT_S - 1.0
    assert greeter._conversation_state(line, now) is None, "a guard left set does not hold for ever"


def test_a_muted_line_is_greeted_and_the_greeting_marks_its_mic(monkeypatch, lapse):
    """A muted page is no block: the camera's own waiting line reports itself muted
    (hold_waiting), and dropping the greeting there would drop the one it rang for. The greeting
    opens that call's mic -- the 14 Oct rule -- through the frame `announce` sends before it, the
    only sound the page opens a held mic on (review 6 Nov)."""
    from app import events
    from app import session as session_module

    line = _line(_play_until=time.monotonic() - 32, _page_muted=True)
    assert greeter._conversation_state(line) is None
    line.provider.send_text = None
    sent, order = [], []

    async def send_json(msg):
        order.append(msg.get("type"))

    async def send_text(text):
        order.append("text")

    line._send_json = send_json
    line.provider.send_text = send_text
    line.turn_idle = None
    monkeypatch.setattr(session_module, "_active", line)
    from app import display

    async def heard(**kw):
        return 0.0

    monkeypatch.setattr(display, "wait_until_heard", heard)
    assert asyncio.run(events.announce("hello", source="face_known")) is True
    assert order == ["greeting_mic", "text"]
    order.clear()
    assert asyncio.run(events.announce("a scene", source="event_scene")) is True
    assert order == ["text"], "a scene's line is not a greeting: it opens no muted mic"


def test_at_zero_a_greeting_marks_nothing(monkeypatch):
    line = _line()
    monkeypatch.setattr(settings, "face_greet_lapse_s", 0.0)
    greeter.mark_greeted(line)
    assert not hasattr(line, "_greeted_at")
    monkeypatch.setattr(settings, "face_greet_lapse_s", 20.0)
    greeter.mark_greeted(line)
    assert time.monotonic() - line._greeted_at < 1.0
    greeter.mark_greeted(None)                            # no line: nothing to do, no error


# -- the camera loop --------------------------------------------------------------------------

class _Cap:
    def __init__(self, frames):
        self.frames = list(frames)

    def read(self):
        if not self.frames:
            return False, None
        return True, self.frames.pop(0)

    def release(self):
        pass


def _run(monkeypatch, sightings, someone_at=None, frames=3, after_run=None):
    from app import camera, events

    announced, rows = [], []

    async def fake_announce(text, *, source, still_relevant=None, **kw):
        announced.append((source, still_relevant))
        return True

    seen = list(sightings)

    class _Watcher:
        threshold, cooldown_s = 0.45, 600.0

        def __init__(self):
            self.someone_at = someone_at

        def see(self, frame, now=None):
            return seen.pop(0) if seen else None

    monkeypatch.setattr(facewatch.Watcher, "from_settings", classmethod(lambda cls: _Watcher()))
    monkeypatch.setattr(camera, "open_camera", lambda *a, **k: _Cap([np.zeros((8, 8, 3), np.uint8)] * frames))
    monkeypatch.setattr(events, "announce", fake_announce)
    monkeypatch.setattr(greeter.turnlog, "record", lambda event, **f: rows.append((event, f)))
    monkeypatch.setattr(settings, "face_fps", 100.0)
    async def main():
        await greeter.run()
        if after_run is not None:
            await asyncio.sleep(0)       # what run() cancelled has run its cancellation
            after_run()                  # still inside the loop: asyncio.run's own cleanup is later

    asyncio.run(main())
    return announced, rows


def test_a_known_face_on_a_used_but_quiet_line_is_greeted(monkeypatch, lapse):
    from app import session as session_module

    monkeypatch.setattr(session_module, "_active", _line(_play_until=time.monotonic() - 32))
    announced, rows = _run(monkeypatch, [_sighting()])
    assert [a[0] for a in announced] == ["face_known"]
    assert not any(f.get("why") == "live_session" for e, f in rows if e == "face_seen")


def test_mid_conversation_it_is_still_not_greeted(monkeypatch, lapse):
    from app import session as session_module

    monkeypatch.setattr(session_module, "_active", _line(_play_until=time.monotonic() - 4))
    announced, rows = _run(monkeypatch, [_sighting()])
    assert announced == []
    assert ("live_session", False) in [(f.get("why"), f.get("greeted")) for e, f in rows if e == "face_seen"]


def test_the_same_line_at_zero_is_not_greeted(monkeypatch):
    """The sales room's default: the used line is a conversation until it ends."""
    from app import session as session_module

    monkeypatch.setattr(settings, "face_greet_lapse_s", 0.0)
    monkeypatch.setattr(session_module, "_active", _line(_play_until=time.monotonic() - 32))
    announced, _ = _run(monkeypatch, [_sighting()])
    assert announced == []


def test_mid_sentence_the_loop_hands_the_greeting_to_a_waiter(monkeypatch, lapse):
    from app import session as session_module

    line = _line()
    line.provider._vad_gate.in_speech = True
    monkeypatch.setattr(session_module, "_active", line)
    waited = []

    async def waiter(watcher, sighting, session):
        waited.append((sighting.name, session))
        await asyncio.sleep(30)                      # still waiting when the next face comes

    monkeypatch.setattr(greeter, "_greet_when_quiet", waiter)
    announced, _ = _run(monkeypatch, [_sighting(), _sighting(name="อาชู่")], frames=4)
    assert announced == [], "not into the middle of their sentence"
    assert waited == [("ต้า", line)], "one waiter; the second face does not start another"


# -- the wait ---------------------------------------------------------------------------------

BOX = (100, 100, 300, 300)


def _wait(monkeypatch, line, *, someone_at, after=None, quiet_wait=1.0, gap_wait=0.4, box=BOX):
    """`_greet_when_quiet` on `line`; `after(line)` runs 0.3 s in. `someone_at`: whether a face is
    in front (None = nobody since), `box`: where it is then."""
    from app import events
    from app import session as session_module

    monkeypatch.setattr(session_module, "_active", line)
    monkeypatch.setattr(greeter, "QUIET_WAIT_S", quiet_wait)
    monkeypatch.setattr(greeter, "GAP_WAIT_S", gap_wait)
    announced, rows = [], []

    async def fake_announce(text, *, source, still_relevant=None, **kw):
        announced.append((source, still_relevant() if still_relevant else None))
        return True

    monkeypatch.setattr(events, "announce", fake_announce)
    monkeypatch.setattr(greeter.turnlog, "record", lambda event, **f: rows.append((event, f)))
    watcher = types.SimpleNamespace(present_at=someone_at, present_box=BOX, present_head=(0.0, 0.0),
                                    present_shape=(480, 640, 3))

    async def go():
        task = asyncio.create_task(greeter._greet_when_quiet(watcher, _sighting(), line))
        await asyncio.sleep(0.3)
        if after is not None:
            after(line)
        watcher.present_at = time.monotonic() if someone_at is not None else None
        watcher.present_box = box
        await task

    asyncio.run(go())
    return announced, [f.get("why") for e, f in rows if e == "face_seen" and f.get("greeted") is False]


def _talking():
    line = _line()
    line.provider._vad_gate.in_speech = True
    return line


def test_they_finish_and_she_does_not_answer_them_so_she_greets(monkeypatch, lapse):
    def finish(line):
        line.provider._vad_gate.in_speech = False      # and her bare "ค่ะ" was dropped

    announced, dropped = _wait(monkeypatch, _talking(), someone_at=time.monotonic(), after=finish)
    assert [a[0] for a in announced] == ["face_known"] and dropped == []
    assert announced[0][1] is True, "the greeting's own last look still lets it through"


def test_they_finish_and_she_answers_them_so_no_greeting(monkeypatch, lapse):
    def she_answers(line):
        line.provider._vad_gate.in_speech = False
        line._play_until = time.monotonic() + 3.0

    announced, dropped = _wait(monkeypatch, _talking(), someone_at=time.monotonic(), after=she_answers)
    assert announced == [] and dropped == ["live_session"]


def test_one_unbroken_voice_is_never_talked_over(monkeypatch, lapse):
    announced, dropped = _wait(monkeypatch, _talking(), someone_at=time.monotonic(), quiet_wait=0.6)
    assert announced == [] and dropped == ["talking"]


def test_people_talking_to_each_other_are_greeted_at_their_next_breath(monkeypatch, lapse):
    """6 Nov: sentences 1-3 s apart, none answered by her, so the 3 s answer window after each
    never ran out. After QUIET_WAIT_S of that, a breath is enough."""
    line = _line()
    line.provider._speech_ended_at = time.monotonic() + 100.0   # a sentence always just ended
    line.provider._replied_since_end = False
    assert greeter._conversation_state(line) == "pending"
    announced, dropped = _wait(monkeypatch, line, someone_at=time.monotonic(), quiet_wait=0.6)
    assert [a[0] for a in announced] == ["face_known"] and dropped == []


def test_she_answering_during_the_breath_wait_still_wins(monkeypatch, lapse):
    line = _line()
    line.provider._speech_ended_at = time.monotonic() + 100.0
    line.provider._replied_since_end = False
    line.provider._vad_gate.in_speech = True

    def she_answers(line):
        line._play_until = time.monotonic() + 3.0

    announced, dropped = _wait(monkeypatch, line, someone_at=time.monotonic(), after=she_answers,
                               quiet_wait=0.2, gap_wait=1.0)
    assert announced == [] and dropped == ["live_session"]


def test_nobody_left_in_front_is_not_greeted(monkeypatch, lapse):
    def finish(line):
        line.provider._vad_gate.in_speech = False

    announced, dropped = _wait(monkeypatch, _talking(), someone_at=None, after=finish)
    assert announced == [] and dropped == ["left"]


def test_a_new_line_meanwhile_is_not_greeted_on(monkeypatch, lapse):
    from app import session as session_module

    def replaced(line):
        session_module._active = _line()

    announced, dropped = _wait(monkeypatch, _talking(), someone_at=time.monotonic(), after=replaced)
    assert announced == [] and dropped == ["line_changed"]


# -- after the decision -----------------------------------------------------------------------

def test_she_started_answering_somebody_while_the_greeting_queued(monkeypatch, lapse):
    """The robot face reports no audio lead: `announce` sends at her turn_complete, with her
    answer still playing. The greeting's own check catches it."""
    from app import events
    from app import session as session_module

    line = _line(_play_until=time.monotonic() - 32)
    monkeypatch.setattr(session_module, "_active", line)
    looked = []

    async def fake_announce(text, *, source, still_relevant=None, **kw):
        line._play_until = time.monotonic() + 5.0      # her answer to somebody, playing
        looked.append(still_relevant())
        return False

    monkeypatch.setattr(events, "announce", fake_announce)
    asyncio.run(greeter._greet(_sighting()))
    assert looked == [False]
    assert not hasattr(line, "_greeted_at"), "a greeting not sent does not hold the idle clock"


def test_a_greeting_sent_holds_the_idle_clock(monkeypatch, lapse):
    """A used line: `_idle_from` is the greeting turn's only on a virgin line. 110 s after the
    last staff words, the greeted person would have had 10 s to answer."""
    from app import display, events
    from app import session as session_module
    from app.tools import slides

    line = _line()
    monkeypatch.setattr(session_module, "_active", line)

    async def fake_announce(text, **kw):
        return True

    monkeypatch.setattr(events, "announce", fake_announce)
    asyncio.run(greeter._greet(_sighting()))
    assert time.monotonic() - line._greeted_at < 1.0

    monkeypatch.setattr(settings, "idle_timeout_s", 120)
    monkeypatch.setattr(settings, "standing_line", True)
    monkeypatch.setattr(display, "remaining_lead", lambda: 0.0)
    line._last_heard_at = time.monotonic() - 125
    sent = []

    async def send(msg):
        sent.append(msg["type"])

    async def shut():
        pass

    line._send_json = send
    monkeypatch.setattr(slides, "shutdown_display", shut)
    ticks = {"n": 0}

    async def fake_sleep(_s):
        ticks["n"] += 1
        if ticks["n"] > 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(session_module.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(line._close_when_nobody_is_there())
    assert sent == [], "hung up on the person she just greeted"
    line._greeted_at = time.monotonic() - 125
    ticks["n"] = 0
    asyncio.run(line._close_when_nobody_is_there())   # returns: it hung up
    assert sent == ["idle_timeout"], "the mark must not hold a line for ever"


# -- the wait, more ways it must end (review 6 Nov, mutations that survived) ---------------------

def test_somebody_else_in_another_spot_does_not_get_this_name(monkeypatch, lapse):
    """ต้า walked off while staff talked; a stranger stands elsewhere in the picture. The waiter
    used to see a fresh `someone_at` and say ต้า's name to them."""
    def finish(line):
        line.provider._vad_gate.in_speech = False

    announced, dropped = _wait(monkeypatch, _talking(), someone_at=time.monotonic(), after=finish,
                               box=(420, 100, 620, 300))
    assert announced == [] and dropped == ["left"]


def test_the_greeting_follows_the_quiet_at_once(monkeypatch, lapse):
    """Not at the end of QUIET_WAIT_S: within a poll of the sentence ending (her bare "ค่ะ")."""
    from app import events
    from app import session as session_module

    line = _talking()
    monkeypatch.setattr(session_module, "_active", line)
    monkeypatch.setattr(greeter, "QUIET_WAIT_S", 5.0)
    when = []

    async def fake_announce(text, *, source, **kw):
        when.append(time.monotonic())
        return True

    monkeypatch.setattr(events, "announce", fake_announce)
    watcher = types.SimpleNamespace(present_at=time.monotonic(), present_box=BOX, present_head=(0.0, 0.0),
                                    present_shape=(480, 640, 3))

    async def go():
        task = asyncio.create_task(greeter._greet_when_quiet(watcher, _sighting(), line))
        await asyncio.sleep(0.5)
        line.provider._vad_gate.in_speech = False
        quiet = time.monotonic()
        watcher.present_at = quiet
        await task
        return quiet

    quiet = asyncio.run(go())
    assert len(when) == 1 and when[0] - quiet < 0.5


def test_a_new_line_after_they_went_quiet_is_not_greeted_on(monkeypatch, lapse):
    from app import session as session_module

    def quiet_and_replaced(line):
        line.provider._vad_gate.in_speech = False
        session_module._active = _line()

    announced, dropped = _wait(monkeypatch, _talking(), someone_at=time.monotonic(), after=quiet_and_replaced)
    assert announced == [] and dropped == ["line_changed"]


@pytest.mark.parametrize("what", ["scan", "ceremony", "approach"])
def test_a_scan_a_ceremony_or_an_approach_starting_meanwhile_ends_it(monkeypatch, lapse, what):
    from app import face_approach

    def finish_and(line):
        line.provider._vad_gate.in_speech = False
        if what == "scan":
            monkeypatch.setattr(greeter, "paused", lambda: True)
        elif what == "ceremony":
            monkeypatch.setattr(greeter, "_ceremony_on", lambda: True)
        else:
            monkeypatch.setattr(settings, "face_approach", True)
            monkeypatch.setattr(face_approach, "holds_greeting", lambda s: True)

    announced, dropped = _wait(monkeypatch, _talking(), someone_at=time.monotonic(), after=finish_and)
    assert announced == [] and dropped == [what]


def test_the_camera_loop_ending_cancels_a_waiting_greeting(monkeypatch, lapse):
    from app import session as session_module

    line = _talking()
    monkeypatch.setattr(session_module, "_active", line)
    seen = {}

    async def waiter(watcher, sighting, session):
        seen["task"] = asyncio.current_task()
        await asyncio.sleep(30)

    monkeypatch.setattr(greeter, "_greet_when_quiet", waiter)
    states = []
    _run(monkeypatch, [_sighting()], after_run=lambda: states.append(seen["task"].cancelled()))
    assert states == [True], "a greeting left waiting after the camera loop stopped"


# -- the real watcher: is the person still there ----------------------------------------------

def _unit(*values):
    v = np.zeros(512, np.float32)
    for i, x in enumerate(values):
        v[i] = x
    return v / np.linalg.norm(v)


def test_the_watcher_keeps_seeing_the_person_it_just_decided(monkeypatch, lapse):
    """`see()` puts the decided person in cooldown at once, so `someone_at` stops for them; the
    presence stamp must not (review 6 Nov: every wait over 2 s ended "left")."""
    from app import faces

    frame = np.zeros((480, 640, 3), np.uint8)
    face = {"box": (100, 100, 300, 300), "vec": _unit(1)}
    monkeypatch.setattr(faces, "locate", lambda frame, det_size=640: [faces.Face(face["box"], 0.9, face["vec"])])
    monkeypatch.setattr(settings, "face_fast_confirm", True)
    gallery = faces.Gallery(["ต้า", "mix"], np.stack([_unit(1), _unit(0, 1)]),
                            [{"group": "staff"}, {"group": "staff"}])
    watcher = facewatch.Watcher(gallery=gallery, threshold=0.45, confirm_frames=2, cooldown_s=300,
                                min_face_px=50, track_wait_s=0, probable_wait_s=0)
    sighting = None
    for _ in range(20):
        sighting = watcher.see(frame)
        if sighting is not None:
            break
        time.sleep(0.05)
    assert sighting is not None and sighting.name == "ต้า"
    box, head = watcher.present_box, watcher.present_head
    decided_someone = watcher.someone_at
    time.sleep(0.05)
    watcher.see(frame)                                   # she stays, in cooldown now
    assert watcher.someone_at == decided_someone, "the premise: someone_at stops for her"
    assert greeter._still_there(watcher, box, head)
    face["box"] = (420, 100, 620, 300)                   # somebody else, elsewhere
    face["vec"] = _unit(0, 0, 1)
    watcher.see(frame)
    assert not greeter._still_there(watcher, box, head)


def test_the_breath_wait_does_not_cut_a_reply_a_gate_holds(monkeypatch, lapse):
    """After QUIET_WAIT_S of their talk, a breath is enough -- unless her reply has begun and a gate
    holds its first audio (final review 6 Nov): the greeting would cut her answer."""
    line = _line()
    line.provider._speech_ended_at = time.monotonic() + 100.0
    line.provider._replied_since_end = False
    line.provider._ack_gate = types.SimpleNamespace(_held=[object()])
    announced, dropped = _wait(monkeypatch, line, someone_at=time.monotonic(), quiet_wait=0.3, gap_wait=0.5)
    assert announced == [] and dropped == ["talking"]


def test_an_approach_holds_a_summoned_line_nobody_greeted(monkeypatch):
    """face_approach's `keep_line` (review 6 Nov): with the standing line off, a line rung for a walk
    up to somebody was abandoned after SUMMONED_ABANDON_S -- the dial, a look and a walk can be longer.
    Held only while the approach keeps calling; then the ordinary rule ends it."""
    from app import display
    from app import session as session_module
    from app.tools import slides

    line = _line(_ever_heard=False)
    line._greeting_turn_seen = False
    monkeypatch.setattr(settings, "standing_line", False)
    monkeypatch.setattr(display, "remaining_lead", lambda: 0.0)
    line._last_heard_at = time.monotonic() - (line.SUMMONED_ABANDON_S + 10)
    line._idle_from = 0.0
    line.keep_line()
    sent = []

    async def send(msg):
        sent.append(msg["type"])

    async def shut():
        pass

    line._send_json = send
    monkeypatch.setattr(slides, "shutdown_display", shut)
    ticks = {"n": 0}

    async def fake_sleep(_s):
        ticks["n"] += 1
        if ticks["n"] > 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(session_module.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(line._close_when_nobody_is_there())
    assert sent == [], "abandoned while an approach was using it"
    line._kept_at = time.monotonic() - (line.SUMMONED_ABANDON_S + 10)
    ticks["n"] = 0
    asyncio.run(line._close_when_nobody_is_there())   # returns: it hung up
    assert sent, "the hold must not keep a line for ever"



def test_a_mute_somebody_tapped_in_this_call_is_not_greeted_over(monkeypatch, lapse):
    """Review 6 Nov: before the lapse a used line was never greeted, so a mute pressed mid-call stayed
    shut; with it, a greeting 20 s later reopened the mic (greeting_mic). The camera's own waiting
    line reports hold_waiting, muted: that one is still greeted (the 14 Oct rule)."""
    line = _line(_page_muted=True, _page_muted_why="mute_tap")
    assert greeter._conversation_state(line) == "exchange"
    waiting = _line(_page_muted=True, _page_muted_why="hold_waiting", _ever_heard=False)
    assert greeter._conversation_state(waiting) != "exchange"


def test_a_line_whose_ears_the_walk_opened_still_counts_its_greeting(monkeypatch):
    """face_approach opens the ears before the stop; chatter on the way made the line "used" and the
    greeting was never counted -- the line then lived by the 30 s abandon rule (review 6 Nov)."""
    from app import display
    from app import session as session_module

    monkeypatch.setattr(display, "remaining_lead", lambda: 0.0)
    rows = []
    monkeypatch.setattr(session_module.turnlog, "record", lambda e, **f: rows.append((e, f)))
    line = _line(_ever_heard=False)
    line._greeting_turn_seen = False
    line._ears_closed_until = time.monotonic() + 4.0
    line.open_ears("face_approach")
    assert line._ears_closed_until is None and ("ears_open", {"greeted": False, "why": "face_approach"}) in rows
    line._ever_heard = True                              # somebody said something on the way
    line._greeting_turn_done()
    assert line._greeting_turn_seen is True
