"""A scene's line recorded ahead in a voice the owner picked by ear (app/event_host.py `recorded`).

Owner 10 Nov 2026, after a blind listening test of Despina beside ElevenLabs voices: "พอเป็น
เสียงภาษาไทยก็อันเดิมดี แต่พอเป็นอังกฤษ อีกตัวดีกว่า" -> "Jessica v3 รอบแรก (C) ชอบเสียงนี้" ->
"เราใช้โชว์แค่วันงาน". The debut's English pageant line plays from a file; every other word is
still the live model's. What these hold: only the take the owner heard plays (the pinned
sha256), only in the format the face plays, only with EVENT_RECORDED_LINES; the recorded-cue
road plays it (her reply first, the red stop); the pageant comes up before it and it waits for the hand (11 Nov); and
whatever cannot play the file leaves the line to her own voice, as before.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import time
import wave

import pytest

from app import event_cues, event_host, event_pageant, turnlog
from app.config import settings
from tests.test_event_cues import _drain, _gemini, _session, _settle as _settle_cues
from tests.test_event_host import TRACKED, _settle, _walker, event, stage  # noqa: F401 -- fixtures, used by name
from tests.test_event_pageant import STARTED_THEN_ARRIVES, _stage_recorded, robot  # noqa: F401 -- fixture

LINE = "Hello everyone! I'm Emmaaa! Welcome, and thank you so much for being here with us today!"


@pytest.fixture(autouse=True)
def _clean():
    event_pageant.reset()
    yield
    event_pageant.reset()


def _wav(seconds=0.4, rate=24000, channels=1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x10\x27" * int(rate * seconds) * channels)
    return buf.getvalue()


def _scene(tmp_path, monkeypatch, data: bytes, *, pin: bytes | None = None) -> dict:
    """A scene whose recorded line is `data` on disk, pinned to `pin` (default: the same bytes)."""
    monkeypatch.setattr(event_host, "ROOT", tmp_path)
    (tmp_path / "data" / "events" / "clips").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "events" / "clips" / "line.wav").write_bytes(data)
    raw = {"id": "debut", "recorded": {"file": "data/events/clips/line.wav",
                                        "sha256": hashlib.sha256(pin if pin is not None else data).hexdigest(),
                                        "text": LINE, "lang": "en"}}
    return {"id": "debut", "title_th": "เดินเปิดตัว", "recorded": event_host._recorded_entry(raw)}


# ---- the file ------------------------------------------------------------------------------

def test_the_tracked_debut_pins_one_take_in_the_events_folder():
    doc = json.loads(TRACKED.read_text(encoding="utf-8"))
    debut = next(s for s in doc["scenes"] if s["id"] == "debut")
    entry = event_host._recorded_entry(debut)
    assert entry is not None and entry["file"].startswith(event_host.RECORDED_DIR)
    assert entry["text"] == LINE and entry["lang"] == "en"
    # the quoted line the model says when the file cannot play is the same sentence
    assert "«%s»" % LINE in debut["on_arrive_th"]


@pytest.mark.parametrize("bad", [
    {"file": "secret.wav"},                                         # outside the folder
    {"file": "data/events/clips/../../secret.wav"},
    {"file": "data/events/clips/sub/line.wav"},
    {"file": "data/events/clips/line.mp3"},                         # the face plays PCM
    {"sha256": "nope"},
    {"sha256": None},
    {"text": "  "},
    "data/events/clips/line.wav",
])
def test_anything_but_a_pinned_wav_in_the_folder_is_ignored(bad):
    rec = {"file": "data/events/clips/line.wav", "sha256": "0" * 64, "text": LINE}
    rec = {**rec, **bad} if isinstance(bad, dict) else bad
    assert event_host._recorded_entry({"id": "x", "recorded": rec}) is None
    assert event_host._recorded_entry({"id": "x"}) is None


def test_only_the_take_the_owner_heard_plays_and_only_in_the_faces_format(tmp_path, monkeypatch):
    good = _wav()
    sc = _scene(tmp_path, monkeypatch, good)
    assert event_host.recorded_pcm(sc) == (None, "off")                 # EVENT_RECORDED_LINES off (conftest)
    monkeypatch.setattr(settings, "event_recorded_lines", True)
    pcm, why = event_host.recorded_pcm(sc)
    assert why is None and pcm == b"\x10\x27" * int(24000 * 0.4)
    # a re-render is another take, nobody heard it: never played until pinned
    assert event_host.recorded_pcm(_scene(tmp_path, monkeypatch, _wav(0.5), pin=good)) == (None, "not_the_take")
    # the face plays 16-bit mono at the Live rate: anything else would play at the wrong speed
    assert event_host.recorded_pcm(_scene(tmp_path, monkeypatch, _wav(rate=16000)))[1] == "format"
    assert event_host.recorded_pcm(_scene(tmp_path, monkeypatch, _wav(channels=2)))[1] == "format"
    assert event_host.recorded_pcm(_scene(tmp_path, monkeypatch, b"not a wav"))[1] == "format"
    (tmp_path / "data" / "events" / "clips" / "line.wav").unlink()
    assert event_host.recorded_pcm(sc) == (None, "missing")             # not deployed
    assert event_host.recorded_pcm({"id": "x", "recorded": None}) == (None, None)


def test_deploy_ships_a_scenes_recorded_line_and_never_the_elevenlabs_key(tmp_path):
    from scripts import robot_server

    clips = tmp_path / "data" / "events" / "clips"
    clips.mkdir(parents=True)
    (clips / "line.wav").write_bytes(b"x")
    (tmp_path / "data" / "events" / "floor1_event.json").write_text(json.dumps({"scenes": [
        {"id": "debut", "recorded": {"file": "data/events/clips/line.wav"}},
        {"id": "sneaky", "recorded": {"file": "data/events/clips/../../../.env"}},
        {"id": "gone", "recorded": {"file": "data/events/clips/missing.wav"}}]}), encoding="utf-8")
    assert robot_server.event_clip_files(tmp_path) == {"data/events/clips/line.wav"}
    # the key renders on this PC (scripts/voice_line.py); the robot only plays the file
    out = robot_server.compose_env("GEMINI_API_KEY=g\nELEVENLABS_API_KEY=el-secret\n", "")
    assert "ELEVENLABS_API_KEY" not in out and "el-secret" not in out


# ---- the debut --------------------------------------------------------------------------------

def _recorded(monkeypatch, answer):
    """The file and the player stubbed: `answer` is what event_cues.play_line says."""
    played = []
    monkeypatch.setattr(settings, "event_recorded_lines", True)
    monkeypatch.setattr(event_host, "recorded_pcm", lambda sc: (b"\x00\x00" * 2400, None))

    async def play_line(line_id, text, pcm, *, lang="en", scene_th=None, still=None):
        assert callable(still), "the line is the press's own: it gives play_line its `still`"
        played.append((line_id, text, lang, scene_th))
        return answer

    monkeypatch.setattr(event_cues, "play_line", play_line)
    return played


def test_the_debut_plays_its_recorded_line_once_the_pageant_is_up(
        event, stage, robot, monkeypatch):
    played = _recorded(monkeypatch, {"ok": True, "mode": "recorded"})
    stage["voice"] = False            # her own voice never comes: the file needs no voice cue

    async def go():
        out = await event_host.start_scene("debut")
        await _settle(lambda: not event_host._tasks)
        return out

    out = asyncio.run(go())
    assert out["ok"]
    assert played == [("scene_debut", LINE, "en", "เดินเปิดตัว")]
    assert not [e for e in stage["log"] if e[0] == "say"], "the model is not asked to say it as well"
    assert [e[:2] for e in stage["log"]] == [("pageant", "pageant"), ("pageant", "pageant_end")]
    p = event_host.perform_view()
    assert p["phase"] == "done" and p["said"] is True and p["gesture_ok"] is True and p["recorded"] is True


@pytest.mark.parametrize("error", ["no_call", "quiet", "no_player", "not_started"])
def test_a_call_that_cannot_play_it_leaves_the_line_to_her_own_voice(event, stage, robot, monkeypatch, error):
    played = _recorded(monkeypatch, {"ok": False, "error": error})
    rows = []
    monkeypatch.setattr(turnlog, "record", lambda event_, **f: rows.append({"event": event_, **f}))

    async def go():
        await event_host.start_scene("debut")
        await _settle(lambda: not event_host._tasks)

    asyncio.run(go())
    says = [e for e in stage["log"] if e[0] == "say"]
    assert played and [e[:2] for e in says] == [("say", "event_scene")]
    assert "«%s»" % LINE in says[0][2]
    # the hand came up before either try of the line (owner 11 Nov "มือจะขึ้นช้ากว่าเสียง")
    assert [e[:2] for e in stage["log"]][0] == ("pageant", "pageant")
    assert {"event": "event_recorded", "scene": "debut", "ok": False, "error": error} in rows


@pytest.mark.parametrize("error", ["stopped", "superseded", "already", "not_sent"])
def test_a_line_stopped_or_half_out_is_not_said_again(event, stage, robot, monkeypatch, error):
    _recorded(monkeypatch, {"ok": False, "error": error})

    async def go():
        await event_host.start_scene("debut")
        await _settle(lambda: not event_host._tasks)

    asyncio.run(go())
    assert not [e for e in stage["log"] if e[0] == "say"]
    # the hand leads the line now (11 Nov): a line that then does not go out brings it straight down,
    # and no greeting group waves for it
    assert [e[:2] for e in stage["log"]] == [("pageant", "pageant"), ("pageant", "pageant_end")]
    assert event_host.perform_view()["said"] is False


def test_pressed_again_while_its_line_plays_the_kept_hand_waves_through_it(event, stage, robot, monkeypatch):
    """The debut pressed twice within its line (owner 11 Nov's two presses were 9 s apart): the
    file plays out (play_line answers the second press `already`), and the hand kept for the second
    press stays up through it -- not brought down because "her line was not said"."""
    from app import event_pageant

    monkeypatch.setattr(event_pageant, "MIN_STANDING_S", 1.0)
    answers = [{"ok": True, "mode": "recorded"}, {"ok": False, "error": "already"}]
    played = _recorded(monkeypatch, None)

    async def play_line(line_id, text, pcm, *, lang="en", scene_th=None, still=None):
        played.append(line_id)
        return answers.pop(0)

    monkeypatch.setattr(event_cues, "play_line", play_line)
    monkeypatch.setattr(event_host, "_line_playing", lambda sc: True)

    async def go():
        await event_host.start_scene("debut")
        assert await _settle(lambda: (event_host.perform_view() or {}).get("phase") == "done")
        await event_host.start_scene("debut")
        p = event_host.perform_view()
        assert await _settle(lambda: (event_host.perform_view() or {}).get("phase") == "done")
        p = event_host.perform_view()
        assert p["said"] is True and p["gesture"] == "pageant" and p["gesture_ok"] is True
        assert event_pageant.active()                    # still up through the playing line
        await _settle(lambda: not event_host._tasks)

    asyncio.run(go())
    assert played == ["scene_debut", "scene_debut"]
    assert [e[:2] for e in stage["log"]] == [("pageant", "pageant"), ("pageant", "pageant_end")]
    up, down = robot["at"]
    assert down - up >= 1.0                              # down after the line, one turn each way


def test_no_file_on_the_robot_is_her_own_voice_as_before(event, stage, robot, monkeypatch):
    monkeypatch.setattr(settings, "event_recorded_lines", True)
    monkeypatch.setattr(event_host, "recorded_pcm", lambda sc: (None, "missing"))

    async def go():
        await event_host.start_scene("debut")
        await _settle(lambda: not event_host._tasks)

    asyncio.run(go())
    assert [e[:2] for e in stage["log"]] == [("pageant", "pageant"), ("say", "event_scene"), ("pageant", "pageant_end")]


# ---- the player ---------------------------------------------------------------------------------

def test_play_line_plays_the_file_as_her_reply_on_the_recorded_cue_road(monkeypatch):
    from app import robot_face_link

    monkeypatch.setattr(robot_face_link, "is_robot_face", lambda session: True)
    rows = []
    monkeypatch.setattr(turnlog, "record", lambda event_, **f: rows.append({"event": event_, **f}))
    provider = _gemini(monkeypatch)
    _session(provider)
    pcm = b"\x10\x27" * 24000

    async def go():
        result = await event_cues.play_line("scene_debut", LINE, pcm, lang="en", scene_th="เดินเปิดตัว")
        assert result == {"ok": True, "mode": "recorded"}
        assert await _settle_cues(lambda: not event_cues.STATE["runs"])

    asyncio.run(go())
    out = _drain(provider)
    assert "".join(e.text for e in out if e.kind == "assistant_transcript") == LINE
    assert b"".join(e.audio for e in out if e.kind == "audio") == pcm
    assert out[-1].kind == "turn_complete"
    # the model learns of it beside later tool results, by id -- never the line
    note = event_cues.last_said_note()
    assert "scene_debut" in note and LINE not in note
    row = [r for r in rows if r["event"] == "event_cue"][-1]
    assert row["by"] == "scene" and row["mode"] == "recorded" and row["ok"]


def test_play_line_answers_why_not_without_a_call_or_a_player(monkeypatch):
    from app import robot_face_link
    from tests.test_event_cues import FakeProvider

    async def go():
        return await event_cues.play_line("scene_debut", LINE, b"\x00\x00" * 100)

    assert asyncio.run(go())["error"] == "no_call"
    monkeypatch.setattr(robot_face_link, "is_robot_face", lambda session: True)
    _session(FakeProvider())                                             # read mode only: no recorded lines
    result = asyncio.run(go())
    assert result["error"] == "no_player" and result["error_th"] == event_cues.ERRORS_TH["no_player"]


# ---- the debut button: walk ahead, wave and speak together (owner 10 Nov) --------------------
#
# "ถ้าทำเสร็จก็อย่าพึ่งเดินให้รวมทั้งหมดเป็นปุ่มเปิดตัว โดยให้หุ่นเดินไปด้านหน้าพร้อมโปกมือและพูด":
# the line as the wheels turn, the wave from the wheels to after the line, nothing said again at the
# spot. These run a fake walker; nothing here moves a robot.

def test_the_tracked_debut_speaks_on_the_walk():
    doc = json.loads(TRACKED.read_text(encoding="utf-8"))
    debut = next(s for s in doc["scenes"] if s["id"] == "debut")
    assert debut["say_on_walk"] is True and debut["pageant"] is True and debut["forward_m"] == 1.0


def test_walking_she_speaks_as_the_wheels_turn_and_says_nothing_again_at_the_spot(event, stage, robot, monkeypatch):
    _stage_recorded()
    _walker(monkeypatch, STARTED_THEN_ARRIVES)
    seen = []

    async def before():
        from app import event_walk

        seen.append((event_walk.walking(), event_pageant.active()))     # when the line goes out
    stage["before_answer"] = before

    async def go():
        out = await event_host.start_scene("debut")
        assert out["walk"]["ok"], out
        assert await _settle(lambda: ("pageant", "pageant_end") in stage["log"], timeout=10)
        await _settle(lambda: not event_host._tasks)

    asyncio.run(go())
    kinds = [e[:2] for e in stage["log"]]
    # up with the wheels, the line on the way, down after the walk and the line -- once each
    assert kinds == [("pageant", "pageant"), ("say", "event_walk_line"), ("pageant", "pageant_end")]
    assert seen == [(True, True)], "the line went out while walking, the hand already up"
    line = stage["log"][1][2]
    assert "«%s»" % LINE in line and event_host.NOT_ARRIVED in line and "หุ่นกำลังเดินออกไปข้างหน้า" in line
    assert ("arm", "greet_arm") not in stage["log"]
    p = event_host.perform_view()
    assert p["phase"] == "done" and p["said"] is True and p["gesture"] == "pageant" and p["gesture_ok"] is True


def test_walking_with_the_lead_the_hand_stays_up_until_her_line_after_arriving(event, stage, robot, monkeypatch):
    """Review 11 Nov: the line waits for the hand on the walk too (4.5 s on the robot then, 2.4 s since 23 Nov's
    faster raise; a 1 m walk is ~7 s): the walk arrived while it waited, and the arrival brought the hand down
    before her line."""
    monkeypatch.setattr(settings, "event_pageant_lead_s", 1.5)
    _stage_recorded()
    _walker(monkeypatch, STARTED_THEN_ARRIVES)          # out at 0.4 s, there 0.8 s later
    at_line = []

    async def before():
        from app import event_walk

        at_line.append((event_walk.walking(), event_pageant.active(), time.time()))
    stage["before_answer"] = before

    async def go():
        out = await event_host.start_scene("debut")
        assert out["walk"]["ok"], out
        assert await _settle(lambda: ("pageant", "pageant_end") in stage["log"], timeout=10)
        await _settle(lambda: not event_host._tasks)

    asyncio.run(go())
    kinds = [e[:2] for e in stage["log"]]
    assert kinds == [("pageant", "pageant"), ("say", "event_walk_line"), ("pageant", "pageant_end")], kinds
    walking, up, _ = at_line[0]
    assert not walking and up, "arrived before the line, the hand still up for it"


def test_walking_the_recorded_line_plays_on_the_way(event, stage, robot, monkeypatch):
    _stage_recorded()
    _walker(monkeypatch, STARTED_THEN_ARRIVES)
    played = _recorded(monkeypatch, {"ok": True, "mode": "recorded"})

    async def go():
        await event_host.start_scene("debut")
        assert await _settle(lambda: ("pageant", "pageant_end") in stage["log"], timeout=10)
        await _settle(lambda: not event_host._tasks)

    asyncio.run(go())
    assert played == [("scene_debut", LINE, "en", "เดินเปิดตัว")]
    assert not [e for e in stage["log"] if e[0] == "say"]
    assert [e[:2] for e in stage["log"]] == [("pageant", "pageant"), ("pageant", "pageant_end")]
    assert event_host.perform_view()["recorded"] is True


def test_walking_the_hand_comes_down_only_after_a_line_still_playing(event, stage, robot, monkeypatch):
    """The walk can arrive before the line is over: the wave waits for her voice (end_after_line)."""
    from app import session as session_module

    _stage_recorded()
    _walker(monkeypatch, STARTED_THEN_ARRIVES)
    playing = {"until": 0.0}

    class Session:
        @property
        def _play_until(self):
            return playing["until"]

    monkeypatch.setattr(session_module, "_active", Session())
    _recorded(monkeypatch, {"ok": True, "mode": "recorded"})
    import time as _time

    async def go():
        await event_host.start_scene("debut")
        assert await _settle(lambda: ("pageant", "pageant") in stage["log"], timeout=5)
        playing["until"] = _time.monotonic() + 3.0                       # her line, 3 s more to go
        assert await _settle(lambda: ("pageant", "pageant_end") in stage["log"], timeout=10)
        return _time.monotonic()

    down = asyncio.run(go())
    assert down >= playing["until"], "the hand came down while she was still speaking"


def test_walking_a_refused_wave_is_not_tried_again_standing_on_the_way(event, stage, robot, monkeypatch):
    _stage_recorded()
    _walker(monkeypatch, STARTED_THEN_ARRIVES)
    robot["refuse"] = ["not_event_app"]

    async def go():
        await event_host.start_scene("debut")
        assert await _settle(lambda: any(e[0] == "say" for e in stage["log"]), timeout=5)
        await _settle(lambda: not event_host._tasks, timeout=5)

    asyncio.run(go())
    assert robot["asked"] == ["pageant"], "no second pageant and no greeting group while the wheels turn"
    assert ("arm", "greet_arm") not in stage["log"]
    p = event_host.perform_view()
    assert p["said"] is True and p["gesture_ok"] is False


# ---- review 10 Nov: the line is never lost, and never outlives its press -------------------------

ARRIVES_FAST = ("import time; print('@@ {\"started\": true}', flush=True); time.sleep(0.3); "
                "print('[ 0.1] step 1 done', flush=True); print('[ 0.2] path event done', flush=True)")


def test_a_walk_that_arrives_before_the_wave_was_answered_still_gets_its_line(event, stage, robot, monkeypatch):
    """1 m is ~7 s and a pageant start may retry for 6 s: when the walk is over first, the line had
    not set out on the way -- the arrival says it (and waves where she stands), as every scene."""
    monkeypatch.setattr(event_pageant, "RETRY_EVERY_S", 0.4)
    robot["refuse"] = ["busy", "busy"]
    _stage_recorded()
    _walker(monkeypatch, ARRIVES_FAST)

    async def go():
        await event_host.start_scene("debut")
        assert await _settle(lambda: any(e[:2] == ("say", "event_arrived") for e in stage["log"]), timeout=10)
        # the arrival runs in the walk's own task, not event_host's: wait for the phone's "done"
        assert await _settle(lambda: (event_host.perform_view() or {}).get("phase") == "done", timeout=10)

    asyncio.run(go())
    says = [e[1] for e in stage["log"] if e[0] == "say"]
    assert says == ["event_arrived"], "said once, at the spot -- never lost, never twice"
    assert "«%s»" % LINE in next(e[2] for e in stage["log"] if e[0] == "say")
    assert event_host.perform_view()["said"] is True


def test_a_recorded_line_waiting_its_turn_is_dropped_when_its_press_is_gone(monkeypatch):
    """Review 10 Nov: staff pressed the next scene while the debut's line waited for her reply --
    the English welcome played around the new scene's line (announce re-checks; this did not)."""
    from app import robot_face_link

    monkeypatch.setattr(robot_face_link, "is_robot_face", lambda session: True)
    audible = {"on": True}
    monkeypatch.setattr(event_cues, "_audible", lambda session, now: audible["on"])
    provider = _gemini(monkeypatch)
    _session(provider)
    pressed = {"current": True}

    async def go():
        task = asyncio.ensure_future(event_cues.play_line("scene_debut", LINE, b"\x10\x27" * 2400,
                                                          still=lambda: pressed["current"]))
        await asyncio.sleep(0.3)                          # waiting for her voice to be over
        pressed["current"] = False                        # ...and staff press another scene
        result = await asyncio.wait_for(task, 5)
        return result

    result = asyncio.run(go())
    assert result["ok"] is False and result["error"] == "superseded"
    assert not [e for e in _drain(provider) if e.kind == "audio"], "nothing of the line played"


def test_the_same_line_pressed_again_replaces_one_waiting_for_a_gone_press(monkeypatch):
    """Review 10 Nov: the debut pressed again while its line waited -- the second press answered
    `already`, the first saw its press gone: neither said it."""
    from app import robot_face_link

    monkeypatch.setattr(robot_face_link, "is_robot_face", lambda session: True)
    audible = {"on": True}
    monkeypatch.setattr(event_cues, "_audible", lambda session, now: audible["on"])
    provider = _gemini(monkeypatch)
    _session(provider)
    first = {"current": True}

    async def go():
        one = asyncio.ensure_future(event_cues.play_line("scene_debut", LINE, b"\x10\x27" * 2400,
                                                         still=lambda: first["current"]))
        await asyncio.sleep(0.2)
        first["current"] = False                          # the first press is gone (a new seq)
        two = asyncio.ensure_future(event_cues.play_line("scene_debut", LINE, b"\x10\x27" * 2400,
                                                         still=lambda: True))
        await asyncio.sleep(0.2)
        audible["on"] = False                             # her voice is over: the line may go
        return await asyncio.wait_for(one, 5), await asyncio.wait_for(two, 5)

    one, two = asyncio.run(go())
    assert one["error"] == "superseded" and two == {"ok": True, "mode": "recorded"}
