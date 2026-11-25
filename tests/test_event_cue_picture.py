"""A ceremony cue pressed with a picture (app/event_cues.py `play(..., show=)`, owner
7 Nov: the location line with the map on the TV, the unit-count line with its count).

What these hold down:
* ownership: the picture reaches the robot face (and the room screen's copy) ahead of
  the line's first word, after any `interrupted` that stops an old reply, as a
  present_story result of that answer -- replayed through the page's own code
  (tests/client_cue_picture.cjs), her line's first sound does not clear it, nothing
  settles it, and the story's clock hears the whole line;
* both roads: a recorded line (the picture inside `play_recorded`) and a line the
  model reads (queued the instant its text went out, behind what is already queued;
  never when the text did not go out);
* the model never gets the payload or the story's lines -- only the id beside later
  tool results (`last_said_note`);
* the call's books: story_shown by="cue", the story "just told", its taps live;
* a picture that cannot go up never holds the line: the line plays and `show` says
  why; a live cue, the cue tool and a refused line carry no picture.

And from the review of 7 Nov, read mode and the red stop:
* a read never lands on a turn of the model's still in flight ("still_speaking"), and
  an old turn's `interrupted` reaching the reading's tap after the send is not her
  line being cut;
* no voice of a reading within READ_START_S = "not_heard", the line free at once,
  never "ok"; its picture comes down;
* `cut()`: a recorded line stops feeding, what is queued is dropped, the page gets
  `interrupted`, the hold is lowered, its picture comes down, and the call goes on; a
  waiting press never starts; a reading's rest is held off the speaker.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from app import event_cues, event_host, event_script, events, face_mirror, stories
from app.config import settings
from app.providers.base import ProviderEvent
from tests.ceremony_app import ceremony_on
from tests.test_event_cues import (  # noqa: F401 -- fixtures
    GREET, FakeProvider, _Socket, _cue_rows, _drain, _gemini, _msg, _no_reconnect, _pcm, _read_all, _session,
    _settle, face, rows, script, voice)
from tests.test_slides import _as_the_live_session
from tests.test_stories import _approve, _as, _built, _tool, approved, catalog_dir  # noqa: F401 -- fixtures

ROOT = Path(__file__).resolve().parent.parent
TOPIC = "fx_ground"                       # the fixture catalog's drill story (one tap, five pictures)


@pytest.fixture(autouse=True)
def _present_story_registered():
    """present_story in the registry (another test may have cleared it): a tap to the
    next story is drawn only where the call can see that tool (stories.picture)."""
    _tool()


class _Page:
    """The robot face's socket: every frame its call sends, in order."""

    def __init__(self):
        self.frames: list = []

    async def send_text(self, text):
        self.frames.append(json.loads(text))

    async def send_bytes(self, data):
        self.frames.append(bytes(data))


def _face_call(provider, monkeypatch):
    """The robot face's live call in the Emma Ceremony app (the ceremony's lines are its own
    since 9 Nov), its session reading the provider's stream the way GeminiProvider.events()
    hands it out (output taps included). The mode itself is claimed where a test needs it."""
    from app.session import VoiceSession

    page = _Page()
    sess = VoiceSession(page)
    sess.provider = provider
    sess.profile = "ceremony"
    sess._robot_face_claim = True
    _as_the_live_session(sess)

    async def stream():
        while True:
            event = await provider._event_queue.get()
            if event is None:
                return
            for tap in list(provider._output_taps):
                tap(event)
            yield event

    monkeypatch.setattr(provider, "events", stream)
    return sess, page


async def _finish(provider, pump):
    """Every queued event through the session, then the pump closed."""
    assert await _settle(lambda: provider._event_queue.empty())
    provider._event_queue.put_nowait(None)
    await asyncio.wait_for(pump, 5)


def _kind(frame) -> str:
    return "audio" if isinstance(frame, bytes) else frame["type"]


def _mirror_frames(q: asyncio.Queue) -> list:
    out = []
    while not q.empty():
        item = q.get_nowait()
        out.append(item if isinstance(item, bytes) else json.loads(item))
    return out


def _replay(tmp_path, frames, line, topic=TOPIC, prelude=({"type": "speech_started"},), *, mirror=False):
    """The frames through the page's own onMessage and stage code (node). The
    prelude is the host's question: `speech_started` makes her answer the one that
    owns the screen -- the case where her first sound clears a stage not its own.
    `mirror`: as the room screen's page runs them (/mirror: its story frame)."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to replay the frames through voice-preview.html: "
                    "the page-side ownership of a cue's picture is NOT checked on this machine")
    spec = {"topic": topic, "line": line, "mirror": mirror,
            "frames": [{"json": f} for f in prelude]
            + [{"audio": len(f)} if isinstance(f, bytes) else {"json": f} for f in frames]}
    path = tmp_path / ("frames-%d.json" % len(list(tmp_path.glob("frames-*.json"))))
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    result = subprocess.run([node, "tests/client_cue_picture.cjs", str(path)], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def _story_lines(topic=TOPIC) -> list[str]:
    story = stories.available()[topic]
    return [line[k] for line in story["lines"] for k in ("th", "en")]


def _pictures(events_) -> list:
    return [e for e in events_ if e is not None and e.kind == "story_picture"]


# ---- ownership, end to end ------------------------------------------------------------

def test_a_recorded_line_puts_its_picture_up_ahead_of_its_first_word_on_the_face_and_the_tv(
        script, voice, rows, face, approved, monkeypatch, tmp_path):
    provider = _gemini(monkeypatch)
    sess, page = _face_call(provider, monkeypatch)
    voice["s1_greet"] = _pcm(1.0)
    monkeypatch.setattr(settings, "mirror_port", 18443)

    async def go():
        tv = asyncio.Queue()
        face_mirror._clients.add(tv)
        pump = asyncio.create_task(sess._provider_to_browser())
        result = await event_cues.play("s1_greet", show=TOPIC)
        assert await _settle(lambda: not event_cues.STATE["runs"])
        await _finish(provider, pump)
        return result, _mirror_frames(tv)

    with _as("ceremony", face=True):
        result, tv = asyncio.run(go())
    assert result == {"ok": True, "mode": "recorded", "show": {"ok": True, "topic": TOPIC}}

    for frames, on_tv in ((page.frames, False), (tv, True)):
        kinds = [_kind(f) for f in frames]
        pic = kinds.index("tool_result")
        assert kinds.count("tool_result") == 1
        assert pic < kinds.index("assistant_transcript") and pic < kinds.index("audio"), kinds
        shown = frames[pic]
        # exactly what a present_story result puts on the page -- and nothing for a model
        assert shown["result"]["screen"] == "story" and shown["result"]["topic"] == TOPIC
        assert set(shown["result"]) == {"ok", "screen", "topic", "story"}
        assert "".join(f["text"] for f in frames if _kind(f) == "assistant_transcript") == GREET
        # the TV's frames as the TV's page runs them (review 7 Nov, B9: replayed as the
        # face, its own staging -- showMirrorStory, answerStaged, mirror-story -- never ran)
        _replay(tmp_path, frames, GREET, mirror=on_tv)
    assert page.frames[[_kind(f) for f in page.frames].index("tool_result")]["name"] == "present_story"
    assert b"".join(f for f in page.frames if isinstance(f, bytes)) == voice["s1_greet"]

    # the call's books, as for present_story: the story "just told" -- and NO taps (review
    # 7 Nov, B2): a touch on the chest screen made her talk over the ceremony and changed
    # the TV's picture. Not on the page either: no chip, no bound row, no picture to open.
    assert sess._story_topic == TOPIC and sess._story_owns_answer and sess._screen_staged
    assert sess._story_taps == set()
    drawn = page.frames[[_kind(f) for f in page.frames].index("tool_result")]["result"]["story"]
    assert drawn["taps"] == [] and drawn["zoom"] == []
    assert not [e for key in ("items", "floors") for e in drawn["params"].get(key) or []
                if isinstance(e, dict) and "tap" in e]
    assert stories.last_shown() == TOPIC
    shown_rows = [r for r in rows if r["event"] == "story_shown"]
    assert shown_rows == [{"event": "story_shown", "topic": TOPIC, "taps": 0, "by": "cue", "cue": "s1_greet"}]
    row = _cue_rows(rows)[-1]
    assert row["ok"] and row["show"] == TOPIC and row["shown"] is True and "show_error" not in row

    # the model got nothing: no tool response, no text -- and later only the id
    assert provider._session.sent == []
    note = event_cues.last_said_note()
    assert "s1_greet" in note and (event_cues.WITH_PICTURE % TOPIC) in note
    assert GREET not in note and not any(line in note for line in _story_lines())
    ceremony_on(session=sess)                            # beside the ceremony app's tool results
    assert TOPIC in event_host.ceremony_note()
    assert event_host.scene_note() is None               # ...never the Event app's (no scene, no cue)


def test_her_old_reply_is_stopped_before_the_picture_goes_up(script, voice, rows, face, approved,
                                                             monkeypatch, tmp_path):
    """The press waited its VOICE_WAIT_S and her last answer is still playing: the
    line stops it first (`interrupted`), and on the page that settles whatever story is
    up -- the picture must come after it, or it would jump to its end at once."""
    provider = _gemini(monkeypatch)
    sess, page = _face_call(provider, monkeypatch)
    voice["s9_thanks"] = _pcm(0.6)
    monkeypatch.setattr(event_cues, "VOICE_WAIT_S", 0.05)
    sess._play_until = time.monotonic() + 60.0          # her old reply, still audible

    async def go():
        pump = asyncio.create_task(sess._provider_to_browser())
        result = await event_cues.play("s9_thanks", show=TOPIC)
        assert await _settle(lambda: not event_cues.STATE["runs"])
        await _finish(provider, pump)
        return result

    with _as("ceremony", face=True):
        result = asyncio.run(go())
    assert result["show"] == {"ok": True, "topic": TOPIC}
    kinds = [_kind(f) for f in page.frames]
    assert kinds.index("interrupted") < kinds.index("tool_result") < kinds.index("assistant_transcript")
    line = event_script.cue("s9_thanks")["th"]
    _replay(tmp_path, page.frames, line)


def _reading_socket(provider, monkeypatch, messages):
    """The Live socket for a read cue: once the cue's text has gone out, the model's
    messages -- through the provider's own `_receive_events` into its queue (the
    picture of a read line goes out THERE, ahead of her first word)."""
    sock = _Socket()

    async def model():
        while not sock.sent:
            await asyncio.sleep(0.01)
        for message in messages:
            yield message

    sock.script = model
    provider._session = sock
    _no_reconnect(provider, monkeypatch)

    async def receive():
        async for event in provider._receive_events():
            await provider._event_queue.put(event)

    return sock, receive


def test_a_line_the_model_reads_gets_its_picture_behind_what_is_queued_and_ahead_of_her_reply(
        script, voice, rows, face, approved, monkeypatch, tmp_path):
    """No listened-to recording: the model reads the line. The picture goes out right
    ahead of her reading's first word -- behind an old reply's `interrupted` still on
    its way (it would settle a story drawn before it), and behind Gemini's LATE
    `interrupted` for the turn the text landed on (review 7 Nov, A5: queued at the
    send, the picture went ahead of that one, and the page's stopPlayback ->
    storyHook('settle') jumped the map to its end before she said a word). And that
    late `interrupted`, reaching the reading's tap AFTER the send, is not her line
    being cut: review 7 Nov, the press ended at once "ok" with nothing measured, and
    the next line could go over hers."""
    provider = _gemini(monkeypatch)                    # no recording: read mode
    sess, page = _face_call(provider, monkeypatch)
    c = event_script.cue("s1_greet")
    provider._event_queue.put_nowait(ProviderEvent(kind="interrupted"))   # on its way, not yet drawn
    pieces = ["สวัสดีค่ะคุณมิกิ ", "ฉันชื่อเอ็มม่า ", "มีอะไรให้ฉันช่วยหรือไม่คะ"]
    sock, receive = _reading_socket(provider, monkeypatch, [
        _msg(interrupted=True),                        # the turn the text landed on, cut -- late
        *[_msg(text=text, audio=_pcm(0.2)) for text in pieces],
        _msg(turn_complete=True)])

    async def go():
        press = asyncio.create_task(event_cues.play("s1_greet", show=TOPIC))
        assert await _settle(lambda: sock.sent)                          # the text went out
        assert _pictures(list(provider._event_queue._queue)) == [], "not at the send: ahead of her words"
        receiver = asyncio.create_task(receive())
        pump = asyncio.create_task(sess._provider_to_browser())
        result = await asyncio.wait_for(press, 5)
        assert await _settle(lambda: not event_cues.STATE["runs"])
        await asyncio.wait_for(receiver, 5)
        await _finish(provider, pump)
        return result

    with _as("ceremony", face=True):
        result = asyncio.run(go())
    assert result == {"ok": True, "mode": "read", "show": {"ok": True, "topic": TOPIC}}
    kinds = [_kind(f) for f in page.frames]
    late = max(i for i, k in enumerate(kinds) if k == "interrupted")
    assert kinds.count("interrupted") == 2 and late < kinds.index("tool_result") < kinds.index("assistant_transcript")
    assert kinds.index("tool_result") < kinds.index("audio")
    _replay(tmp_path, page.frames, "".join(pieces))
    # the model was asked to say the line -- the line alone, nothing of the picture
    sent = [s["turns"].parts[0].text for s in provider._session.sent]
    assert sent == [event_script.read_text(c)]
    assert TOPIC not in sent[0] and not any(line in sent[0] for line in _story_lines())
    row = _cue_rows(rows)[-1]
    # the old turn's `interrupted` and the late one reached the tap first, and neither
    # was taken for her line
    assert row["mode"] == "read" and row["ok"] and row["shown"] is True
    assert row["fidelity"] == 1.0 and row["cut"] is False and row["stale_interrupts"] == 2
    assert row["seconds"] == 0.6
    note = event_cues.last_said_note()
    assert note == event_cues.SHOWN_FACT % ("s1_greet", TOPIC)
    assert [r["by"] for r in rows if r["event"] == "story_shown"] == ["cue"]


def test_a_read_text_that_never_went_out_leaves_no_picture(script, voice, rows, face, approved, monkeypatch):
    provider = _gemini(monkeypatch)
    sess = _session(provider)
    sess._robot_face_claim = True

    async def failing(text, *, still_relevant=None, **kw):
        assert still_relevant()              # her turn armed, as announce does right before sending
        return False                         # ...and the send failed

    monkeypatch.setattr(events, "announce", failing)
    with _as("ceremony", face=True):
        result = asyncio.run(event_cues.play("s1_greet", show=TOPIC))
    assert result["error"] == "not_sent"
    assert result["show"] == {"ok": False, "error": "not_said", "error_th": event_cues.SHOW_ERRORS_TH["not_said"]}
    assert _pictures(_drain(provider)) == []
    assert event_cues.last_said_note() is None


def test_a_reply_that_is_not_next_out_gets_no_picture_and_the_line_is_read_anyway(
        script, voice, rows, face, approved, monkeypatch):
    """The provider refuses (a recorded hold up = the text deferred behind it, a
    socket just found closed): `not_shown`, and she reads the line all the same."""
    provider = _gemini(monkeypatch)
    sess, page = _face_call(provider, monkeypatch)
    provider._closed_noted = provider._session         # a send just found this socket closed

    async def go():
        press = asyncio.create_task(event_cues.play("s1_greet", show=TOPIC))
        assert await _settle(lambda: provider._session.sent)
        assert _pictures(list(provider._event_queue._queue)) == []
        provider._event_queue.put_nowait(ProviderEvent(kind="audio", audio=_pcm(0.2)))
        provider._event_queue.put_nowait(ProviderEvent(kind="turn_complete"))
        pump = asyncio.create_task(sess._provider_to_browser())
        result = await asyncio.wait_for(press, 5)
        assert await _settle(lambda: not event_cues.STATE["runs"])
        await _finish(provider, pump)
        return result

    with _as("ceremony", face=True):
        result = asyncio.run(go())
    assert result["ok"] and result["mode"] == "read"
    assert result["show"]["ok"] is False and result["show"]["error"] == "not_shown"
    assert "tool_result" not in [_kind(f) for f in page.frames]


def test_offline_a_recorded_line_carries_its_picture_the_same_way(monkeypatch):
    """No network (OFFLINE_FALLBACK): the recorded line is the one cue mode left, and
    its picture goes the same way -- after the interruption, before the first word."""
    from app.providers import offline as module

    monkeypatch.setattr(module, "_own_gate", lambda vad_floor: None)
    p = module.OfflineProvider("Despina", "x")
    p._audio_until = time.monotonic() + 30            # her last line still playing
    pic = {"topic": TOPIC, "cue": "s1_greet", "by": "cue"}
    report: dict = {}
    assert asyncio.run(p.play_recorded(GREET, _pcm(0.3), interrupt_first=True, report=report, picture=pic))
    out = []
    while not p._events.empty():
        out.append(p._events.get_nowait())
    kinds = [e.kind for e in out]
    assert kinds.index("interrupted") < kinds.index("story_picture") < kinds.index("assistant_transcript")
    assert kinds.index("story_picture") < kinds.index("audio") and report["picture"] is True
    assert out[kinds.index("story_picture")].data == pic


# ---- a picture that cannot go up never holds the line --------------------------------------

@pytest.mark.parametrize("why", ["stories_off", "unknown_story", "not_approved", "not_face", "error"])
def test_a_picture_that_cannot_go_up_never_holds_the_line(script, voice, rows, face, catalog_dir,
                                                          monkeypatch, why):
    from tests.test_stories import _units_tools

    _units_tools()
    monkeypatch.setattr(settings, "tool_groups", "stories,units,knowledge")
    monkeypatch.setattr(settings, "stories_enabled", why != "stories_off")
    if why != "not_approved":
        _approve(catalog_dir)
    if why == "error":
        def broken(topic):
            raise RuntimeError("catalog unreadable")

        monkeypatch.setattr(stories, "picture", broken)
    provider = _gemini(monkeypatch)
    sess = _session(provider)
    sess._robot_face_claim = why != "not_face"
    voice["s9_thanks"] = _pcm(0.4)

    async def go():
        result = await event_cues.play("s9_thanks", show="no_such_story" if why == "unknown_story" else TOPIC)
        assert await _settle(lambda: not event_cues.STATE["runs"])
        return result

    with _as("ceremony", face=True):
        result = asyncio.run(go())
    assert result == {"ok": True, "mode": "recorded",
                      "show": {"ok": False, "error": why, "error_th": event_cues.SHOW_ERRORS_TH[why]}}
    out = _drain(provider)
    assert b"".join(e.audio for e in out if e.kind == "audio") == voice["s9_thanks"], "the line played whole"
    assert _pictures(out) == []
    row = _cue_rows(rows)[-1]
    assert row["ok"] is True and row["show_error"] == why and row["shown"] is False
    assert "พร้อมภาพ" not in event_cues.last_said_note()


def test_a_provider_that_cannot_carry_a_picture_still_reads_the_line(script, voice, rows, face, approved):
    provider = FakeProvider()                            # no shows_pictures, no queue
    sess = _session(provider)
    sess._robot_face_claim = True

    async def go():
        async def her():
            assert await _settle(lambda: provider.sent and provider.taps)
            provider.feed(ProviderEvent(kind="assistant_transcript", text=GREET))
            provider.feed(ProviderEvent(kind="audio", audio=_pcm(0.3)))
            provider.feed(ProviderEvent(kind="turn_complete"))

        reading = asyncio.create_task(her())
        result = await event_cues.play("s1_greet", show=TOPIC)
        await reading
        assert await _settle(lambda: not event_cues.STATE["runs"])
        return result

    with _as("ceremony", face=True):
        result = asyncio.run(go())
    assert result["ok"] and result["mode"] == "read" and result["show"]["error"] == "unsupported"
    assert provider.sent == [event_script.read_text(event_script.cue("s1_greet"))]


def test_a_live_cue_gets_no_picture_and_says_why(script, voice, rows, face, approved, monkeypatch):
    """A live cue's answer is the model's own reply to the host -- at a time and in a
    turn the server does not open -- so nothing can put a picture ahead of it."""
    provider = _gemini(monkeypatch)
    _session(provider)._robot_face_claim = True
    with _as("ceremony", face=True):
        result = asyncio.run(event_cues.play("s4_live", show=TOPIC))
    assert result["error"] == "live_only"
    assert result["show"]["error"] == "live_only" and "ตอบสด" in result["show"]["error_th"]
    assert _drain(provider) == [] and provider._session.sent == []


def test_a_refused_line_carries_no_picture(script, voice, rows, approved):
    voice["s1_greet"] = _pcm(0.3)
    result = asyncio.run(event_cues.play("s1_greet", show=TOPIC))          # no call at all
    assert result["error"] == "no_call" and result["show"]["error"] == "not_said"
    assert _cue_rows(rows)[-1]["show_error"] == "not_said"


def test_the_cue_tool_never_puts_a_picture_up(script, voice, rows, face, approved, monkeypatch):
    """Inside the model's own tool call the turn is the model's, not ours."""
    provider = _gemini(monkeypatch)
    sess = _session(provider)
    sess._robot_face_claim = True
    voice["s1_greet"] = _pcm(0.3)

    async def go():
        result = await event_cues.play("s1_greet", by="model", show=TOPIC)
        provider._tool_rounds += 1                       # the tool's result went back
        assert await _settle(lambda: not event_cues.STATE["runs"])
        return result

    with _as("ceremony", face=True):
        result = asyncio.run(go())
    assert result["mode"] == "recorded" and result["show"]["error"] == "model_turn"
    out = _drain(provider)
    assert _pictures(out) == [] and b"".join(e.audio for e in out if e.kind == "audio") == voice["s1_greet"]


def test_without_show_nothing_changes(script, voice, rows, face, approved, monkeypatch):
    provider = _gemini(monkeypatch)
    _session(provider)._robot_face_claim = True
    voice["s1_greet"] = _pcm(0.3)

    async def go():
        result = await event_cues.play("s1_greet")
        assert await _settle(lambda: not event_cues.STATE["runs"])
        return result

    with _as("ceremony", face=True):
        assert asyncio.run(go()) == {"ok": True, "mode": "recorded"}
    assert _pictures(_drain(provider)) == []
    assert "show" not in _cue_rows(rows)[-1] and "shown" not in _cue_rows(rows)[-1]


# ---- the picture alone ----------------------------------------------------------------------

def test_the_picture_is_present_storys_page_part_and_nothing_for_a_model(approved, rows):
    with _as("ceremony", face=True):
        alone = stories.picture(TOPIC)
        assert stories.last_shown() is None and not [r for r in rows if r["event"] == "story_shown"], \
            "the session writes those when it really goes up"
        told = stories.tool_result(TOPIC)
    assert alone == {"ok": True, "screen": "story", "topic": TOPIC, "story": told["story"]}
    # a tap to the next story asks the model for present_story: not drawn where it cannot
    with _as("event", face=False):
        assert stories.picture(TOPIC)["story"]["taps"] == []
    assert stories.picture("nope") == {"ok": False, "error": "unknown_story"}
    assert stories.picture(None) == {"ok": False, "error": "unknown_story"}


# ---- read mode: never onto a turn in flight, never "ok" without her voice (review 7 Nov) ----

@pytest.mark.parametrize("busy", ["streaming", "tool", "turn"])
def test_a_read_never_lands_on_a_turn_still_in_flight(script, voice, rows, face, approved, monkeypatch, busy):
    """VOICE_WAIT_S ran out with her old answer still being generated: the text would
    land on that turn, Gemini would cut it, and its `interrupted` and output would come
    after the send -- taken for her line. Refused instead, nothing sent, nothing shown,
    the line free again."""
    provider = _gemini(monkeypatch)
    sess = _session(provider)
    sess._robot_face_claim = True
    monkeypatch.setattr(event_cues, "VOICE_WAIT_S", 0.1)
    monkeypatch.setattr(events, "TURN_WAIT_S", 0.1)
    if busy == "streaming":
        provider._model_out_at = time.monotonic()        # her old answer's output is coming in
    elif busy == "tool":
        provider._pending_tool_ids.add("call-1")
    else:
        sess.turn_idle.clear()                           # an announcement's turn not over
    with _as("ceremony", face=True):
        result = asyncio.run(event_cues.play("s1_greet", show=TOPIC))
    assert result["ok"] is False and result["error"] == "still_speaking"
    assert result["error_th"] == "Emma ยังพูดค้างอยู่ — รอให้จบก่อน"
    assert result["show"]["error"] == "not_said"
    assert provider._session.sent == [] and _pictures(_drain(provider)) == []
    assert not event_cues.STATE["runs"] and not events._lock.locked()
    row = _cue_rows(rows)[-1]
    assert row["error"] == "still_speaking" and row["why"] == busy


def test_a_reading_that_never_comes_ends_failed_and_frees_the_line(script, voice, rows, face, approved,
                                                                   monkeypatch):
    """The text went out and no voice came: "not_heard" after READ_START_S, never ok,
    the line pressable again at once -- and the picture waiting for her first word
    never goes up."""
    provider = _gemini(monkeypatch)
    sess, page = _face_call(provider, monkeypatch)
    monkeypatch.setattr(event_cues, "READ_START_S", 0.3)

    async def go():
        pump = asyncio.create_task(sess._provider_to_browser())
        started = time.monotonic()
        result = await event_cues.play("s1_greet", show=TOPIC)
        took = time.monotonic() - started
        assert not event_cues.STATE["runs"], "the line is free again"
        await _finish(provider, pump)
        return result, took

    with _as("ceremony", face=True):
        result, took = asyncio.run(go())
    assert result["ok"] is False and result["error"] == "not_heard"
    assert result["error_th"] == "ไม่ได้ยิน Emma อ่านบท" and took < 3
    assert result["show"]["ok"] is False
    shown = [f["result"]["screen"] for f in page.frames if _kind(f) == "tool_result"]
    # it waits for her first word (A5): no word, never up -- and no clear either, which
    # would have taken down whatever WAS up
    assert shown == [], "a picture with no line under it never goes up"
    assert provider._picture_pending is None
    row = _cue_rows(rows)[-1]
    assert row["error"] == "not_heard" and row["why"] == "no_audio" and row["ok"] is False
    assert event_cues.STATE["last"]["s1_greet"]["ok"] is False
    assert event_cues.last_said_note() is None


def test_words_without_her_voice_are_not_heard(script, voice, rows, face):
    """Her turn ended with words and no audio: not "ok" -- and not after 12 s either."""
    provider = FakeProvider()
    _session(provider)

    async def go():
        async def her():
            assert await _settle(lambda: provider.sent and provider.taps)
            provider.feed(ProviderEvent(kind="assistant_transcript", text=GREET))
            provider.feed(ProviderEvent(kind="turn_complete"))

        reading = asyncio.create_task(her())
        started = time.monotonic()
        result = await event_cues.play("s1_greet")
        await reading
        return result, time.monotonic() - started

    result, took = asyncio.run(go())
    assert result["error"] == "not_heard" and took < 3
    assert not event_cues.STATE["runs"]


# ---- the red stop: cut() --------------------------------------------------------------------

def test_cut_stops_a_recorded_line_mid_way_and_the_call_goes_on(script, voice, rows, face, approved, monkeypatch):
    provider = _gemini(monkeypatch, lead=0.0)            # paced: 2 s of line, a chunk per 0.1 s
    sess, page = _face_call(provider, monkeypatch)
    voice["s1_greet"] = _pcm(2.0)
    voice["s9_thanks"] = _pcm(0.3)

    async def go():
        pump = asyncio.create_task(sess._provider_to_browser())
        result = await event_cues.play("s1_greet", show=TOPIC)
        await asyncio.sleep(0.3)
        cut = await event_cues.cut("staff")
        assert await _settle(lambda: not event_cues.STATE["runs"])
        assert await _settle(lambda: provider._event_queue.empty())
        stop_at = len(page.frames)
        after = await event_cues.play("s9_thanks")          # the call works normally afterwards
        assert await _settle(lambda: not event_cues.STATE["runs"])
        await _finish(provider, pump)
        return result, cut, stop_at, after

    with _as("ceremony", face=True):
        result, cut, stop_at, after = asyncio.run(go())
    assert cut is True and result["ok"] and result["show"]["ok"]
    line = page.frames[:stop_at]
    kinds = [_kind(f) for f in line]
    last_stop = max(i for i, k in enumerate(kinds) if k == "interrupted")
    assert "audio" not in kinds[last_stop:], "audio reached the page after the stop"
    assert sum(len(f) for f in line if isinstance(f, bytes)) < len(voice["s1_greet"])
    screens = [f["result"]["screen"] for f in line if _kind(f) == "tool_result"]
    assert screens == ["story", "clear"], "the stopped line's picture comes down"
    assert sess._story_taps == set()
    assert provider._recorded is None and sess.turn_idle.is_set() and not events._lock.locked()
    stopped = [r for r in _cue_rows(rows) if r["id"] == "s1_greet"][-1]
    assert stopped["error"] == "stopped" and stopped["cut"] is True
    cut_row = [r for r in rows if r["event"] == "event_cue_cut"][-1]
    assert cut_row["why"] == "staff" and cut_row["ran"] and cut_row["speaking"] == 1
    assert cut_row["recorded"] and cut_row["interrupted"] and cut_row["cleared"]
    # ...and the next line plays whole
    assert after == {"ok": True, "mode": "recorded"}
    assert b"".join(f for f in page.frames[stop_at:] if isinstance(f, bytes)) == voice["s9_thanks"]
    note = event_cues.last_said_note()
    assert "s9_thanks" in note and "พร้อมภาพ" not in note


def test_cut_stops_a_press_still_waiting_before_it_starts(script, voice, rows, face, monkeypatch):
    provider = _gemini(monkeypatch)
    sess = _session(provider)
    voice["s9_thanks"] = _pcm(0.3)

    async def go():
        await events._lock.acquire()                     # a scene's line is being said
        press = asyncio.create_task(event_cues.play("s9_thanks"))
        assert await _settle(lambda: any(r.get("task") for r in event_cues.STATE["runs"].values()))
        assert await event_cues.cut("arm")
        result = await asyncio.wait_for(press, 2)
        events._lock.release()
        return result

    result = asyncio.run(go())
    assert result["error"] == "stopped" and result["error_th"] == event_cues.ERRORS_TH["stopped"]
    assert not [e for e in _drain(provider) if e.kind == "audio"] and provider._recorded is None
    assert sess.turn_idle.is_set() and not event_cues.STATE["runs"]
    cut_row = [r for r in rows if r["event"] == "event_cue_cut"][-1]
    assert cut_row["why"] == "arm" and cut_row["waiting"] == 1 and cut_row["speaking"] == 0


def test_cut_drops_the_rest_of_a_reading_and_holds_what_the_model_still_makes(script, voice, rows, face,
                                                                              monkeypatch):
    provider = _gemini(monkeypatch)                      # no recording: read mode
    sess, page = _face_call(provider, monkeypatch)

    async def go():
        press = asyncio.create_task(event_cues.play("s1_greet"))
        assert await _settle(lambda: provider._session.sent)
        provider._event_queue.put_nowait(ProviderEvent(kind="assistant_transcript", text="สวัสดีค่ะ"))
        provider._event_queue.put_nowait(ProviderEvent(kind="audio", audio=_pcm(0.2)))
        pump = asyncio.create_task(sess._provider_to_browser())
        result = await asyncio.wait_for(press, 5)           # ok on her first sound
        assert await _settle(lambda: provider._event_queue.empty())
        for _ in range(3):                                   # the rest of her reading, not handed over yet
            provider._event_queue.put_nowait(ProviderEvent(kind="audio", audio=_pcm(0.2)))
        cut = await event_cues.cut()                         # (nothing awaited in between)
        assert await _settle(lambda: not event_cues.STATE["runs"])
        await _finish(provider, pump)
        return result, cut

    with _as("ceremony", face=True):
        result, cut = asyncio.run(go())
    assert result == {"ok": True, "mode": "read"} and cut is True
    kinds = [_kind(f) for f in page.frames]
    assert kinds.count("audio") == 1 and kinds.index("audio") < kinds.index("interrupted")
    assert provider._model_held_at is not None, "what the model still generates is held"
    row = _cue_rows(rows)[-1]
    assert row["error"] == "stopped" and row["cut"] is True
    cut_row = [r for r in rows if r["event"] == "event_cue_cut"][-1]
    assert cut_row["held"] and cut_row["purged_ms"] == 600
    assert event_cues.STOPPED_FACT % "s1_greet" in event_cues.last_said_note()


def test_a_stopped_readings_rest_never_reaches_the_speaker(monkeypatch):
    """stop_line(hold_model=True): the model goes on generating the turn it was reading
    (nothing short of a turn of ours stops it); none of it is played, its end passes
    empty, and the next turn plays as ever."""
    provider = _gemini(monkeypatch)

    async def model():
        yield _msg(text="ต่อจากนี้", audio=b"\x01\x00" * 2400)     # the rest of the stopped reading
        yield _msg(turn_complete=True)
        yield _msg(text="ใหม่", audio=b"\x02\x00" * 2400, turn_complete=True)

    provider._session = _Socket(model)
    _no_reconnect(provider, monkeypatch)
    out = provider.stop_line(hold_model=True)
    assert out["held"] and out["interrupted"] and "recorded" not in out
    received = []
    asyncio.run(_read_all(provider, received))
    assert b"".join(e.audio for e in received if e.kind == "audio") == b"\x02\x00" * 2400
    assert "".join(e.text for e in received if e.kind == "assistant_transcript") == "ใหม่"
    assert [e.kind for e in _drain(provider)] == ["interrupted"]


def test_cut_with_nothing_running_does_nothing(script, voice, rows, face, monkeypatch):
    provider = _gemini(monkeypatch)
    _session(provider)
    assert asyncio.run(event_cues.cut()) is False
    assert _drain(provider) == []
    assert [r for r in rows if r["event"] == "event_cue_cut"] == [{"event": "event_cue_cut", "why": "staff",
                                                                    "ran": False}]


def test_cut_reaches_the_last_seconds_of_a_line_whose_press_is_over(script, voice, rows, face, monkeypatch):
    """A recorded line is pushed up to RECORDED_LEAD_S ahead of her voice: its press
    can be over while its end still plays. The stop still reaches it."""
    provider = _gemini(monkeypatch)                      # no pacing: pushed at once, "playing" ~1 s
    _session(provider)
    voice["s9_thanks"] = _pcm(1.0)

    async def go():
        await event_cues.play("s9_thanks")
        assert await _settle(lambda: not event_cues.STATE["runs"])
        return await event_cues.cut()

    assert asyncio.run(go()) is True
    out = _drain(provider)
    assert out[-1].kind == "interrupted" and not [e for e in out if e.kind == "audio"], \
        "what was still queued is dropped and the page told to stop"
    assert [r for r in rows if r["event"] == "event_cue_cut"][-1]["tail"] is True


def test_offline_the_stop_drops_what_is_queued_and_stops_the_page(monkeypatch):
    from app.providers import offline as module

    monkeypatch.setattr(module, "_own_gate", lambda vad_floor: None)
    p = module.OfflineProvider("Despina", "x")
    for e in (ProviderEvent(kind="assistant_transcript", text="สวัสดี"), ProviderEvent(kind="audio", audio=_pcm(0.2)),
              ProviderEvent(kind="turn_complete")):
        p._events.put_nowait(e)
    out = p.stop_line(clear=True)
    left = []
    while not p._events.empty():
        left.append(p._events.get_nowait())
    assert [e.kind for e in left] == ["turn_complete", "interrupted", "story_picture"]
    assert left[-1].data == {"clear": True} and out["purged_ms"] == 200


# ---- review 7 Nov, second round: the red stop's windows and the line's tail -------------------

def test_cut_stops_a_press_still_looking_its_cue_up(script, voice, rows, face, monkeypatch):
    """A2: the press registers its run only after the cue is looked up (`_pick`, off the
    loop). A red stop in that window found nothing, said False, and the line played
    after it. The press reads the stop's generation at entry; the stop counts it."""
    import threading

    provider = _gemini(monkeypatch)
    _session(provider)
    voice["s9_thanks"] = _pcm(0.5)
    real, gate = event_cues._pick, threading.Event()

    def slow(cue_id):
        gate.wait(3)
        return real(cue_id)

    monkeypatch.setattr(event_cues, "_pick", slow)

    async def go():
        before = event_cues.cut_gen()
        press = asyncio.create_task(event_cues.play("s9_thanks"))
        assert await _settle(lambda: event_cues.STATE["entering"])
        assert not event_cues.STATE["runs"], "the window: nothing registered yet"
        stopped = await event_cues.cut("staff")
        gate.set()
        result = await asyncio.wait_for(press, 3)
        await asyncio.sleep(0.1)
        return before, stopped, result

    before, stopped, result = asyncio.run(go())
    assert stopped is True and event_cues.cut_gen() == before + 1
    assert result["ok"] is False and result["error"] == "stopped"
    assert not [e for e in _drain(provider) if e.kind == "audio"], "the line played after the stop"
    assert provider._recorded is None and not event_cues.STATE["runs"]
    assert [r for r in rows if r["event"] == "event_cue_cut"][-1]["entering"] == 1


def test_line_audible_is_a_press_or_the_tail_of_its_voice(script, voice, rows, face, monkeypatch):
    """A3: the running order keeps a step busy while her line is under way or still
    playing -- a line's press can be over with seconds of her voice still to play."""
    provider = _gemini(monkeypatch)                      # no pacing: pushed at once, ~1 s "playing"
    _session(provider)
    voice["s9_thanks"] = _pcm(1.0)
    assert event_cues.line_audible() is False

    async def go():
        press = asyncio.create_task(event_cues.play("s9_thanks"))
        assert await _settle(lambda: event_cues.STATE["runs"])
        during = event_cues.line_audible()
        await press
        assert await _settle(lambda: not event_cues.STATE["runs"])
        return during

    assert asyncio.run(go()) is True
    now = time.monotonic()
    assert event_cues.line_audible(now) is True, "the press is over, her voice is not"
    assert event_cues.line_audible(now + 1.0 + event_cues.AUDIBLE_SLACK_S + 0.2) is False
    _session(FakeProvider())                             # another call: not its line
    assert event_cues.line_audible(now) is False


def test_a_reading_never_goes_up_over_the_last_lines_voice(script, voice, rows, face, approved, monkeypatch):
    """A3: a read line ends at Gemini's turn_complete with her voice still to play. The
    next reading, and its picture, waits for that tail -- and is refused if it is still
    there, never sent over it."""
    provider = _gemini(monkeypatch)
    sess = _session(provider)
    sess._robot_face_claim = True
    monkeypatch.setattr(event_cues, "VOICE_WAIT_S", 0.1)
    event_cues.STATE["audible"] = (provider, time.monotonic() + 30.0, False)    # the last line, still playing
    with _as("ceremony", face=True):
        result = asyncio.run(event_cues.play("s1_greet", show=TOPIC))
    assert result["ok"] is False and result["error"] == "still_speaking"
    assert _cue_rows(rows)[-1]["why"] == "tail"
    assert provider._session.sent == [] and getattr(provider, "_picture_pending", None) is None


def test_a_reading_goes_once_the_last_lines_voice_is_out(script, voice, rows, face, monkeypatch):
    provider = _gemini(monkeypatch)
    _session(provider)
    monkeypatch.setattr(event_cues, "READ_START_S", 0.1)
    tail_end = time.monotonic() + 0.3
    event_cues.STATE["audible"] = (provider, tail_end, False)
    sent_at = []
    send = provider.send_text

    async def timed(text, **kw):
        sent_at.append(time.monotonic())
        return await send(text, **kw)

    monkeypatch.setattr(provider, "send_text", timed)
    asyncio.run(event_cues.play("s1_greet"))             # no voice comes: not_heard -- the timing is the point
    assert sent_at and sent_at[0] >= tail_end + event_cues.AUDIBLE_SLACK_S - 0.05


def test_cut_in_a_lines_last_seconds_takes_its_picture_down(script, voice, rows, face, approved, monkeypatch):
    """A4 (a): a recorded line is pushed up to RECORDED_LEAD_S ahead of her voice: its
    press is over while its end -- and its picture -- still play. The stop reached the
    voice and left the picture standing."""
    provider = _gemini(monkeypatch)                      # no pacing: pushed at once, "playing" ~1 s
    sess, page = _face_call(provider, monkeypatch)
    voice["s9_thanks"] = _pcm(1.0)

    async def go():
        pump = asyncio.create_task(sess._provider_to_browser())
        result = await event_cues.play("s9_thanks", show=TOPIC)
        assert await _settle(lambda: not event_cues.STATE["runs"])
        stopped = await event_cues.cut()
        await _finish(provider, pump)
        return result, stopped

    with _as("ceremony", face=True):
        result, stopped = asyncio.run(go())
    assert result["show"]["ok"] and stopped is True
    screens = [f["result"]["screen"] for f in page.frames if _kind(f) == "tool_result"]
    assert screens == ["story", "clear"], "the stop in the tail left the picture up"
    cut_row = [r for r in rows if r["event"] == "event_cue_cut"][-1]
    assert cut_row["tail"] is True and cut_row["cleared"] is True


async def _first_run() -> dict:
    assert await _settle(lambda: event_cues.STATE["runs"])
    return next(iter(event_cues.STATE["runs"].values()))


def test_cut_between_the_picture_and_the_first_word_takes_it_down(script, voice, rows, face, approved,
                                                                   monkeypatch):
    """A4 (b): the picture is on the page before her first word is; a stop in between
    found the press "not shown yet" and left it up."""
    provider = _gemini(monkeypatch)
    sess, page = _face_call(provider, monkeypatch)
    voice["s9_thanks"] = _pcm(0.5)
    held: dict = {}
    push = provider._push_own

    async def first_word_late(event):
        await held["gate"].wait()                        # her first word, not out yet
        return await push(event)

    monkeypatch.setattr(provider, "_push_own", first_word_late)

    async def go():
        held["gate"] = asyncio.Event()
        pump = asyncio.create_task(sess._provider_to_browser())
        press = asyncio.create_task(event_cues.play("s9_thanks", show=TOPIC))
        run = await _first_run()
        assert await _settle(lambda: run["report"].get("picture"))
        assert "first_at" not in run["report"]
        stopped = await event_cues.cut()
        held["gate"].set()
        await asyncio.wait_for(press, 5)
        assert await _settle(lambda: not event_cues.STATE["runs"])
        await _finish(provider, pump)
        return stopped

    with _as("ceremony", face=True):
        stopped = asyncio.run(go())
    assert stopped is True
    screens = [f["result"]["screen"] for f in page.frames if _kind(f) == "tool_result"]
    assert screens == ["story", "clear"], "the stop before her first word left the picture up"


def test_a_read_lines_picture_not_up_yet_is_dropped_by_the_stop_not_cleared(
        script, voice, rows, face, approved, monkeypatch):
    """A stop before her reading's first word: its picture never goes up -- and no clear
    goes out, which would take down whatever WAS up."""
    provider = _gemini(monkeypatch)
    sess, page = _face_call(provider, monkeypatch)

    async def go():
        press = asyncio.create_task(event_cues.play("s1_greet", show=TOPIC))
        assert await _settle(lambda: provider._session.sent)
        assert provider._picture_pending is not None
        stopped = await event_cues.cut()
        result = await asyncio.wait_for(press, 5)
        return stopped, result

    with _as("ceremony", face=True):
        stopped, result = asyncio.run(go())
    assert stopped is True and result["error"] == "stopped"
    assert provider._picture_pending is None
    assert _pictures(_drain(provider)) == [], "a clear for a picture that never went up"
    cut_row = [r for r in rows if r["event"] == "event_cue_cut"][-1]
    assert cut_row.get("picture_dropped") is True and "cleared" not in cut_row


@pytest.mark.parametrize("busy", ["streaming", "audible", "reply_owed"])
def test_the_red_stop_silences_her_own_answer_when_no_line_of_ours_runs(script, voice, rows, face,
                                                                        monkeypatch, busy):
    """B3: with no cue line at all -- the live step's answer, an announcement -- the red
    stop returned False and stopped nothing. Now her answer is held off the speaker to
    its end and the page told to stop, as a reading is; the live window closes."""
    provider = _gemini(monkeypatch)
    sess = _session(provider)
    now = time.monotonic()
    if busy == "streaming":
        provider._model_out_at = now
    elif busy == "audible":
        sess._play_until = now + 5.0
    else:
        provider._speech_ended_at, provider._replied_since_end = now, False
    ceremony_on(replies="cues", session=sess)
    assert event_host.live_open(cue_id="s4_recommend", brief_th=None)
    assert asyncio.run(event_cues.cut("staff")) is True
    # only a turn still under way is held. An answer whose words are all out and that only
    # still PLAYS has no turn left to hold: the hold landed on her NEXT reply (a model turn
    # within RECORDED_HELD_GAP_S stays off the speaker) -- review 7 Nov, round 3
    held = busy != "audible"
    assert (provider._model_held_at is not None) is held
    assert [e.kind for e in _drain(provider)] == ["interrupted"]      # the page stops her either way
    assert event_host.live_state() is None
    cut_row = [r for r in rows if r["event"] == "event_cue_cut"][-1]
    assert cut_row["ran"] is True and cut_row["answer"] == busy and bool(cut_row.get("held")) is held
    assert [r["why"] for r in rows if r["event"] == "event_live" and r["action"] == "close"] == ["cut"]


def test_a_recorded_line_takes_over_a_read_lines_waiting_picture(monkeypatch):
    """The recorded line is the next answer now and the model's output under it is held:
    a read line's picture still waiting for the model's words must not go up later."""
    provider = _gemini(monkeypatch)
    assert provider.queue_picture({"topic": TOPIC, "cue": "s1_greet", "by": "cue"})
    assert provider._picture_pending is not None
    provider.play_recorded(GREET, _pcm(0.2)).close()
    assert provider._picture_pending is None


def test_no_picture_goes_up_behind_a_stop_that_came_while_the_old_reply_was_stopped(monkeypatch):
    """A4: the red stop between the `interrupted` that stops her old reply and the
    picture: its clear is already queued, and a picture behind it would stand there
    with no line."""
    provider = _gemini(monkeypatch)
    put = provider._put_own
    stops = []

    async def stopped_meanwhile(event):
        ok = await put(event)
        if event.kind == "interrupted" and not stops:
            stops.append(provider.stop_line(clear=True))     # staff, once
        return ok

    monkeypatch.setattr(provider, "_put_own", stopped_meanwhile)
    report: dict = {}
    pic = {"topic": TOPIC, "cue": "s9_thanks", "by": "cue"}
    whole = asyncio.run(provider.play_recorded(GREET, _pcm(0.3), interrupt_first=True, report=report, picture=pic))
    assert whole is False
    pictures = [e.data for e in _drain(provider) if e.kind == "story_picture"]
    assert pictures == [{"clear": True}], pictures
    assert not report.get("picture")
