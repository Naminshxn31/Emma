"""The two modes of the launch event (owner 5 Nov 2026): "ช่วงเปิดพิธีจะฟังเสียงคนเดียว
และก็เปิดคลิป เปิดทีวี พูดออกมา อาจจะมีซีนก่อน ส่วนตอนปกติค่อยปรับโหมดตอนพิธีเปิดจบ ส่วนการ
ทักทายหลังจากเปิดอยากให้เวลาหุ่นเจอหน้าคนแล้วจำได้ หุ่นก็จะเดินเข้าไปหาแล้วทักทายเลย" + the
multimedia room's door: "เปิดค้าง แต่เราไม่เข้าทำเป็นปิดเลย".

What these hold down:
* the ceremony is the Emma Ceremony app being on the robot since 9 Nov (owner: "อันนี้ทำแยกกับ
  อันเดิมนะ ไม่งั้นจะปนกัน", app/ceremony.py) -- no scene of the event file is one any more,
  and the mode has no clock of its own;
* in a ceremony: the camera greets nobody, she asks nothing, the call stays up, and only
  her name earns a reply (or, cue-only, nothing but the pressed lines);
* out of one: the ordinary rules, and walking up is armed by the scene that says so;
* a clip is an id, she stays quiet over it, and nothing is claimed the screen did not say.
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from pathlib import Path

import pytest

from app import ceremony, event_clips, event_host, event_walk, face_mirror, heard
from app.config import settings
from app.providers.base import ProviderEvent
from app.providers.transcript_filter import NOISE_HOLD_BYTES, NoiseReplyGate
from tests.ceremony_app import ceremony_off, ceremony_on
from tests.test_reply_stall import _client_contents, _provider, _question, fast, rows  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
TRACKED = ROOT / "tests" / "data" / "floor1_event_rehearsed.json"
CEREMONY_FILE = ROOT / "data" / "ceremony" / "floor1_ceremony.json"
PAGE = (ROOT / "client" / "voice-preview.html").read_text(encoding="utf-8")
STAFF_PAGE = (ROOT / "client" / "event-control.html").read_text(encoding="utf-8")


@pytest.fixture
def event(tmp_path, monkeypatch):
    doc = json.loads(TRACKED.read_text(encoding="utf-8"))
    clips = tmp_path / "clips"
    clips.mkdir()
    (clips / "opening.mp4").write_bytes(b"not really a film")
    # The tracked file names films that are not on any machine yet: this one brings its own,
    # first, and an Event scene that rolls it and says a line (no scene is a ceremony since
    # 9 Nov -- the ceremony is the Emma Ceremony app, whose order rolls the same films).
    doc["clips"] = {"opening": {"title_th": "คลิปเปิดงาน", "file": str(clips / "opening.mp4"), "seconds": None,
                                "after_th": "ขอบคุณแขกที่รับชมหนึ่งประโยคสั้นๆ แล้วหยุดรอคนดำเนินพิธี"},
                    **doc["clips"]}
    doc["scenes"].insert(1, {"id": "film_scene", "title_th": "เล่นคลิปของงาน",
                             "tv": "open", "clip": "opening", "spot": None,
                             "brief_th": "เล่นคลิปของงานบนจอใหญ่ ระหว่างคลิปเล่นไม่ต้องพูด",
                             "on_start_th": "เชิญแขกชมคลิปบนจอใหญ่หนึ่งประโยคสั้นๆ"})
    route = tmp_path / "route.json"
    route.write_text(json.dumps({"dock": {"pose": [-7.3, -6.2, 168.8], "approach": [-9.0, -6.2]},
                                 "paths": {}}), encoding="utf-8")
    doc["route_file"] = str(route)
    path = tmp_path / "floor1_event.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "event_file", str(path))
    monkeypatch.setattr(event_walk, "RUN_DIR", tmp_path / ".run")
    monkeypatch.setattr(event_walk, "GATE_WAIT_S", 0.0)
    monkeypatch.setattr(event_clips, "CLIPS_DIR", clips)
    event_host.reset()
    return path


@pytest.fixture
def said(monkeypatch):
    out = []

    async def fake_say(text, source, still):
        out.append((source, text, still()))

    monkeypatch.setattr(event_host, "_say", fake_say)
    return out


@pytest.fixture
def screen(monkeypatch):
    """A room screen that is up, and what was sent to it. Never a real room PC."""
    face_mirror.reset()
    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    face_mirror._clients.add(queue)
    monkeypatch.setattr("app.room_screens.offered", lambda: True)
    yield queue
    face_mirror.reset()


def _sent(queue) -> list[dict]:
    out = []
    while not queue.empty():
        out.append(json.loads(queue.get_nowait()))
    return out


def _ceremony():
    """The named-only ceremony (answers when the host says her name): the ceremony app's call."""
    return ceremony_on(replies="named")


# ---- the file ---------------------------------------------------------------------------

def test_the_day_is_the_ceremony_app_then_the_event_app():
    """Owner 9 Nov: "อันนี้ทำแยกกับอันเดิมนะ ไม่งั้นจะปนกัน" -- the event file has the Event app's
    scenes only; the ceremony's own file opens the big screen first and ends handing over to
    the Event app, whose 'after the ceremony' scene takes it from there."""
    doc = event_host._normalise(json.loads(TRACKED.read_text(encoding="utf-8")), {})
    scenes = {s["id"]: s for s in doc["scenes"]}
    assert "ceremony_start" not in scenes and not [s for s in scenes.values() if "ceremony" in s]
    assert {"tech_presentation", "embassy_world_story"} <= set(doc["clips"])    # shared with the ceremony
    assert scenes["after_ceremony"]["approach"] and scenes["after_ceremony"]["tv"] == "close"
    # its brief says nothing of the ceremony app (the Event app's own words)
    assert "Ceremony" not in scenes["after_ceremony"]["brief_th"] and "แอป" not in scenes["after_ceremony"]["brief_th"]
    order = json.loads(CEREMONY_FILE.read_text(encoding="utf-8"))["order"]
    last = order[-1]["steps"]
    # the 12 Nov script: the LED, its films, lights, curtain and globe are the show team's
    # (Screen_Show_Guide) -- no step puts her face or a film on a screen; manual ticks instead
    assert not [st for sc in order for st in sc["steps"] if st["do"] in ("screen", "clip")]
    assert last[-1]["do"] == "handoff" and last[-1]["to"] == "event"
    assert "หน้าคุม Event" in last[-1]["note_th"] and "หน-า" not in json.dumps(last, ensure_ascii=False)
    assert not [st for sc in order for st in sc["steps"] if st["do"] == "scene"]


def test_a_scene_marked_ceremony_is_an_ordinary_event_scene(caplog):
    """A file from before 9 Nov: its `ceremony`/`replies` mean nothing now (logged) -- no scene
    can silence the camera or deafen her; only the ceremony app's call does."""
    doc = event_host._normalise({"scenes": [{"id": "x", "ceremony": True, "replies": "cues", "approach": True,
                                             "roam": {"stops": ["a", "b"]}}]}, {})
    sc = doc["scenes"][0]
    assert "ceremony" not in sc and "replies" not in sc
    assert sc["approach"] is True and sc["roam"] == {"stops": ["a", "b"]}
    assert "Emma Ceremony app" in caplog.text


def test_a_scene_marks_nothing_by_omission():
    doc = event_host._normalise({"scenes": [{"id": "x", "tv": "maybe", "clip": "nope"}]}, {})
    sc = doc["scenes"][0]
    assert (sc["approach"], sc["tv"], sc["clip"], sc["roam"]) == (False, None, None, None)


def test_no_spot_of_the_event_goes_through_the_multimedia_door():
    """"เปิดค้าง แต่เราไม่เข้าทำเป็นปิดเลย": the door stands open on the day and nothing
    in the event may lead past its line. The leaf is at y -7.78 (4 Nov, door closed)."""
    doc = json.loads(TRACKED.read_text(encoding="utf-8"))
    door_line = -7.78
    for sid, spot in doc["spots"].items():
        if spot.get("pose"):
            assert spot["pose"][1] > door_line, sid
        x = y = face = None
        for step in spot.get("steps") or []:
            if step.get("to"):
                (x, y), face = step["to"], step.get("face", face)
            elif step.get("do") == "turn":
                face = step["face"]
            elif "forward" in step:
                # a walk ahead (card 3's guest_front, 22 Nov: turned round at the counter, up to the guest):
                # its whole length along the heading it walks
                x += step["forward"] * math.cos(math.radians(face))
                y += step["forward"] * math.sin(math.radians(face))
            assert y is not None and y > door_line, (sid, step)
            assert "creep" not in step, sid
    words = next(s for s in doc["scenes"] if s["id"] == "escort_multimedia")
    assert "ไม่เข้า" in words["brief_th"] and "ด้านนอก" in words["on_arrive_th"]


# ---- one press ----------------------------------------------------------------------------

def test_no_scene_switches_the_ceremony_the_app_does(event, said, monkeypatch):
    """Pressing every scene of the Event app leaves the ceremony off; the ceremony app's call
    turns it on, its end turns it off (app/ceremony.py)."""
    monkeypatch.setattr("app.room_screens.offered", lambda: False)

    async def go():
        for sc in event_host.load()["scenes"]:
            if sc["spot"] is None and not sc["stops"] and not sc["roam"]:
                await event_host.start_scene(sc["id"])
                assert not event_host.ceremony_now(), sc["id"]
        ceremony_on()
        assert event_host.ceremony_now() and event_host.mode_snapshot()["ceremony_app"] is True
        ceremony_off()
        assert not event_host.ceremony_now() and event_host.mode_snapshot()["ceremony_app"] is False

    asyncio.run(go())


def test_the_ceremony_has_no_clock_of_its_own(event, monkeypatch):
    """EVENT_CEREMONY_MAX_S ended a ceremony SCENE because scene.json outlived the event; the app
    being open is the mode, however long the speeches run, and nothing warns of a time-out."""
    ceremony_on()
    ceremony.STATE["since"] = time.time() - settings.event_ceremony_max_s - 3600
    assert event_host.ceremony_now() and event_host.cue_only()
    assert not any("เกินเวลา" in w for w in event_host.mode_snapshot()["warnings"])


def test_the_mode_does_not_come_back_from_scene_json_and_walking_up_does_not_either(event, said, monkeypatch):
    """After a restart the ceremony is back when its app's call is (a new claim), not from the
    Event app's scene file; walking up never comes back by itself."""
    monkeypatch.setattr(settings, "face_approach", True)
    monkeypatch.setattr(settings, "event_walk_enabled", True)
    from app import face_approach

    monkeypatch.setattr(face_approach, "area", lambda: [(0, 0), (1, 0), (1, 1)])

    async def recover():
        await event_host.recover_after_restart()

    asyncio.run(event_host.start_scene("introduce"))
    event_host.STATE.update(scene=None)
    asyncio.run(recover())
    assert event_host.STATE["scene"] == "introduce" and not event_host.ceremony_now()
    asyncio.run(event_host.start_scene("after_ceremony"))
    assert face_approach.STATE["staff_on"]
    face_approach.STATE["staff_on"] = False
    asyncio.run(recover())
    assert not face_approach.STATE["staff_on"], "a machine must not start walking by itself"


def test_the_scene_that_says_so_arms_walking_up_and_any_other_disarms(event, said, monkeypatch):
    from app import face_approach

    monkeypatch.setattr(settings, "face_approach", True)
    monkeypatch.setattr(settings, "event_walk_enabled", True)
    monkeypatch.setattr(face_approach, "area", lambda: [(0, 0), (1, 0), (1, 1)])
    event_walk.heartbeat()

    async def go():
        result = await event_host.start_scene("after_ceremony")
        assert result["approach"]["ok"] and face_approach.armed()
        await event_host.start_scene("introduce")
        assert not face_approach.armed()
        await event_host.start_scene("after_ceremony")
        assert face_approach.armed()
        # the ceremony app opening turns it off (app/ceremony.py `_quiet_the_event`)
        ceremony_on()
        for _ in range(5):
            await asyncio.sleep(0)
        assert not face_approach.armed()

    asyncio.run(go())


def test_the_scene_says_why_it_could_not_arm_walking_up(event, said, monkeypatch):
    from app import face_approach

    monkeypatch.setattr(settings, "face_approach", True)
    monkeypatch.setattr(settings, "event_walk_enabled", False)
    monkeypatch.setattr(face_approach, "area", lambda: [(0, 0), (1, 0), (1, 1)])
    result = asyncio.run(event_host.start_scene("after_ceremony"))
    assert result["approach"]["ok"] is False and result["approach"]["error"] == "walk_off"


def test_staff_cannot_arm_walking_up_inside_a_ceremony(event, monkeypatch):
    from app import face_approach

    monkeypatch.setattr(settings, "face_approach", True)
    monkeypatch.setattr(settings, "event_walk_enabled", True)
    monkeypatch.setattr(face_approach, "area", lambda: [(0, 0), (1, 0), (1, 1)])
    _ceremony()
    assert face_approach.set_enabled(True)["error"] == "ceremony"
    assert not face_approach.STATE["staff_on"]


def test_without_walk_up_on_this_machine_a_scene_does_not_touch_it(event, said):
    assert settings.face_approach is False
    assert asyncio.run(event_host.start_scene("after_ceremony"))["approach"] is None


# ---- what she is told ---------------------------------------------------------------------

def test_a_ceremony_line_says_it_outranks_her_questions(event):
    """CEREMONY_RULE rides with the ceremony (beside its tool results, and its film's end) and
    nowhere in the Event app's scenes (owner 9 Nov: "ไม่งั้นจะปนกัน")."""
    for sc in event_host.load()["scenes"]:
        text = event_host.scene_text(sc, walking=False, spot_id=None) or ""
        assert "ไม่ถามกลับ" not in text, sc["id"]
    assert "ไม่ถามกลับ" not in event_host.clip_end_text("คลิป", "ขอบคุณค่ะ")
    event_host.STATE["scene"] = "introduce"
    assert event_host.ceremony_note() is None and "ไม่ถามกลับ" not in event_host.scene_note()
    _ceremony()
    note = event_host.ceremony_note()
    assert "ไม่ถามกลับ" in note and "เรียกชื่อ" in note
    assert "ไม่ถามกลับ" not in event_host.scene_note(), "the event's scene note never carries it"
    assert "ไม่ถามกลับ" in event_host.clip_end_text("คลิป", "ขอบคุณค่ะ")


def test_a_mic_muted_at_the_robot_is_told_to_staff(event, monkeypatch):
    from app import wake

    assert not any("ไมค์" in w for w in event_host.mode_snapshot()["warnings"])
    monkeypatch.setattr(wake, "_HELD", {object()})
    assert any("ไมค์" in w for w in event_host.mode_snapshot()["warnings"])


def test_the_staff_page_is_not_promised_one_voice(event):
    """Listening to one enrolled voice left on 5 Nov: nothing on the phone says it is on,
    and a ceremony with nothing wrong has nothing to warn about."""
    _ceremony()
    snap = event_host.mode_snapshot()
    assert snap["warnings"] == [] and snap["ceremony"] is True and snap["needs_name"] is True
    assert "listen_one" not in snap


# ---- focus, the call, her questions, the camera -------------------------------------------

def test_the_camera_keeps_its_say_in_a_ceremony_and_her_name_still_gets_in(monkeypatch):
    """With no voice door a ceremony must not open the room to the whole crowd: focus's
    rules stand, and the host reaches her the way anybody out of view does: by name."""
    from app import focus

    monkeypatch.setattr(settings, "focus_enabled", True)
    monkeypatch.setattr(settings, "focus_require_face", True)
    monkeypatch.setattr(settings, "focus_wake", True)
    monkeypatch.setattr(settings, "focus_wake_window_s", 10.0)
    focus.reset()
    focus.begin_session(summoned=True)
    now = time.monotonic()
    assert focus.may_open(now, 0.02)[0] is False
    _ceremony()
    assert focus.may_open(now + 1, 0.02)[0] is False
    focus.heard_name(now + 2)
    assert focus.may_open(now + 3, 0.02)[0] is True
    ceremony_off()
    assert focus.may_open(now + 20, 0.02)[0] is False


def test_she_is_not_hung_up_on_through_a_three_minute_clip(monkeypatch):
    from app import session as session_module

    monkeypatch.setattr(settings, "idle_timeout_s", 120)
    sess = session_module.VoiceSession(None, provider_name="gemini", summoned=False)
    sess._ever_heard = True
    sess._last_heard_at = time.monotonic() - 3600
    sent = []

    async def send(msg):
        sent.append(msg)

    sess._send_json = send
    calls = {"n": 0}

    async def fake_sleep(_s):
        calls["n"] += 1
        if calls["n"] > 6:
            raise asyncio.CancelledError

    monkeypatch.setattr(session_module.asyncio, "sleep", fake_sleep)
    _ceremony()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(sess._close_when_nobody_is_there())
    assert sent == []
    # ...and the quiet is not counted against the next guest when it ends
    assert time.monotonic() - sess._ceremony_at < 5
    ceremony_off()
    calls["n"] = 0
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(sess._close_when_nobody_is_there())
    assert sent == []


def test_she_asks_nothing_into_the_ceremony():
    from app import session as session_module

    sess = session_module.VoiceSession(None, provider_name="gemini", summoned=True)
    assert sess._follow_up_veto(time.monotonic()) != "ceremony"
    _ceremony()
    assert sess._follow_up_veto(time.monotonic()) == "ceremony"


def test_the_camera_greets_nobody_in_a_ceremony_and_again_after(monkeypatch):
    from tests.test_greeter import _run_with, _sighting

    _ceremony()
    announced, _ = _run_with(monkeypatch, [_sighting(kind="known", name="Pete")])
    assert announced == []
    ceremony_off()
    announced, _ = _run_with(monkeypatch, [_sighting(kind="known", name="Pete")])
    assert len(announced) == 1


def test_the_base_does_not_turn_toward_a_face_in_a_ceremony(monkeypatch):
    from app import greeter

    monkeypatch.setattr(settings, "robot_chassis_motion_enabled", True)
    monkeypatch.setattr("app.robot_chassis.configured", lambda: True)
    _ceremony()
    assert asyncio.run(greeter._turn_refusal(30.0))[0] == "ceremony"


# ---- only her name earns a reply -----------------------------------------------------------

def _t(text):
    return ProviderEvent(kind="assistant_transcript", text=text)


def _a(n=4800):
    return ProviderEvent(kind="audio", audio=b"\x01\x00" * (n // 2))


def _gate(on=True):
    return NoiseReplyGate(0.06, named_only=lambda: on)


def test_a_host_speaking_to_the_room_gets_no_reply_from_her():
    """Whatever lets this voice in, she must not answer its whole speech."""
    gate = _gate()
    gate.segment(0.2)
    gate.heard("ขอต้อนรับแขกผู้มีเกียรติทุกท่านครับ")
    assert gate.push(_t("ยินดีต้อนรับค่ะ")) == [] and gate.push(_a(NOISE_HOLD_BYTES)) == []
    owed, dropped = gate.close()
    assert owed == [] and dropped["why"] == "not_named"


@pytest.mark.parametrize("words", ["Emma เปิดคลิปหน่อย", "เอ็มม่า แนะนำตัวหน่อยครับ", "คุณเอมม่าครับ", "emma, hello"])
def test_her_name_earns_the_reply(words):
    gate = _gate()
    gate.segment(0.2)
    gate.heard(words)
    first = _t("ค่ะ")
    assert gate.push(first) == [first]
    assert gate.close() == ([], None)


def test_what_is_not_her_name():
    """"อะไร" and "what" pass the noise gate; to the seats they are no cue."""
    gate = _gate()
    gate.segment(0.2)
    gate.heard("มีอะไรให้ดูบ้างครับ what a day")
    assert gate.push(_t("ค่ะ")) == [] and gate.push(_a(NOISE_HOLD_BYTES)) == []
    assert gate.close()[1]["why"] == "not_named"


def test_the_same_person_goes_on_without_her_name_for_a_moment(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr("app.providers.transcript_filter.time.monotonic", lambda: clock["t"])
    gate = _gate()
    gate.segment(0.2)
    gate.heard("Emma มีกี่อาคาร")
    gate.push(_t("สามอาคารค่ะ"))
    gate.close()
    clock["t"] += 20.0
    gate.segment(0.2)
    gate.heard("แล้วสูงกี่ชั้น")
    first = _t("ค่ะ")
    assert gate.push(first) == [first]
    gate.close()
    clock["t"] += 31.0
    gate.segment(0.2)
    gate.heard("ขอบคุณแขกทุกท่านครับ")
    assert gate.push(_t("ค่ะ")) == [] and gate.push(_a(NOISE_HOLD_BYTES)) == []
    assert gate.close()[1]["why"] == "not_named"


def test_staffs_cue_and_a_tool_after_it_are_never_judged():
    gate = _gate()
    gate.text_turn()
    gate.tool_call()
    first = _t("ขอต้อนรับแขกทุกท่านค่ะ")
    assert gate.push(first) == [first]


def test_a_cue_opens_no_window_for_the_speech_that_follows(monkeypatch):
    gate = _gate()
    gate.text_turn()
    gate.push(_t("ขอต้อนรับค่ะ"))
    gate.close()
    gate.segment(0.2)
    gate.heard("ขอบคุณเอ็มที่มาร่วมงานครับ")
    assert gate.push(_t("ค่ะ")) == [] and gate.push(_a(NOISE_HOLD_BYTES)) == []


def test_a_tool_she_reached_for_on_nobody_s_call_says_nothing():
    gate = _gate()
    gate.segment(0.2)
    gate.heard("โครงการของเรามีสามอาคารครับ")
    gate.tool_call()
    assert gate.push(_t("สามอาคารค่ะ")) == [] and gate.push(_a(NOISE_HOLD_BYTES)) == []
    assert gate.close()[1]["why"] == "not_named"


def test_the_ordinary_day_is_the_ordinary_gate():
    gate = _gate(on=False)
    gate.segment(0.2)
    gate.heard("ขอบคุณแขกทุกท่านครับ")
    first = _t("ค่ะ")
    assert gate.push(first) == [first]


def test_the_quiet_of_a_clip_says_why(monkeypatch):
    gate = NoiseReplyGate(0.06, quiet=lambda: "clip")
    gate.segment(0.2)
    gate.heard("Emma")
    assert gate.push(_t("ค่ะ")) == []
    assert gate.close()[1]["why"] == "clip"
    walking = NoiseReplyGate(0.06, quiet=lambda: True)
    walking.segment(0.2)
    assert walking.push(_t("ค่ะ")) == []
    assert walking.close()[1]["why"] == "walking"


# ---- the clip ----------------------------------------------------------------------------------

async def _settle(cond, timeout=5.0):
    for _ in range(int(timeout / 0.02)):
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


def test_a_clip_rolls_she_is_quiet_and_comes_back_when_the_screen_says_so(event, said, screen):
    async def go():
        result = await event_host.play_clip("opening", by="staff")
        assert result["ok"]
        assert await _settle(lambda: event_clips.quiet())
        sent = _sent(screen)
        assert [m["type"] for m in sent][-1] == "mirror_clip_play"
        cue = sent[-1]
        assert cue["id"] == "opening" and cue["url"].startswith("/event-clip/")
        assert "file" not in json.dumps(cue) and "clips" not in cue["url"]
        assert any(m["type"] == "mirror_clips" for m in sent), "fetched ahead of the cue"
        assert event_clips.report(cue["seq"], "opening", "playing", 180.0)
        assert await _settle(lambda: event_clips.STATE["phase"] == event_clips.PLAYING)
        assert event_clips.quiet() and event_clips.STATE["duration"] == 180.0
        assert said == [], "nothing said over the film"
        assert event_clips.report(cue["seq"], "opening", "ended")
        assert await _settle(lambda: event_clips.STATE["phase"] == event_clips.ENDED and said)
        assert not event_clips.quiet()
        assert said[0][0] == "event_clip_end" and "คลิป" in said[0][1]

    asyncio.run(go())


def test_a_screen_that_never_says_playing_gives_her_voice_back(event, said, screen, monkeypatch):
    monkeypatch.setattr(event_clips, "ACK_WAIT_S", 0.05)

    async def go():
        await event_host.play_clip("opening")
        assert await _settle(lambda: event_clips.STATE["phase"] == event_clips.FAILED)
        assert not event_clips.quiet()
        snap = event_clips.snapshot()
        assert snap["error_th"] == event_clips.ERRORS_TH["no_ack"]
        assert said == [], "she does not announce a film that is not there"
        assert _sent(screen)[-1]["type"] == "mirror_clip_stop"

    asyncio.run(go())


def test_a_browser_that_died_mid_film_does_not_leave_a_mute_robot(event, said, screen, monkeypatch):
    monkeypatch.setattr(event_clips, "GRACE_S", 0.05)

    async def go():
        await event_host.play_clip("opening")
        assert await _settle(event_clips.quiet)
        cue = [m for m in _sent(screen) if m["type"] == "mirror_clip_play"][-1]
        event_clips.report(cue["seq"], "opening", "playing", 0.05)
        assert await _settle(lambda: event_clips.STATE["phase"] == event_clips.ENDED)
        assert not event_clips.quiet()

    asyncio.run(go())


def test_the_screen_reporting_an_error_is_a_failure_not_an_ending(event, said, screen):
    async def go():
        await event_host.play_clip("opening")
        assert await _settle(event_clips.quiet)
        cue = [m for m in _sent(screen) if m["type"] == "mirror_clip_play"][-1]
        event_clips.report(cue["seq"], "opening", "error")
        assert await _settle(lambda: event_clips.STATE["phase"] == event_clips.FAILED)
        assert said == []

    asyncio.run(go())


def test_a_report_about_another_cue_is_nothing(event, said, screen):
    async def go():
        await event_host.play_clip("opening")
        assert await _settle(event_clips.quiet)
        seq = event_clips.STATE["seq"]
        assert not event_clips.report(seq - 1, "opening", "ended")
        assert not event_clips.report(seq, "other", "ended")
        assert not event_clips.report(seq, "opening", "whatever")
        assert event_clips.quiet()
        await event_clips.stop()

    asyncio.run(go())


def test_a_nonsense_duration_is_not_believed(event, said, screen):
    async def go():
        await event_host.play_clip("opening")
        assert await _settle(event_clips.quiet)
        seq = event_clips.STATE["seq"]
        event_clips.report(seq, "opening", "playing", float("inf"))
        assert event_clips.STATE["duration"] is None
        await event_clips.stop()

    asyncio.run(go())


def test_staff_stop_takes_it_down_and_says_nothing(event, said, screen):
    async def go():
        await event_host.play_clip("opening")
        assert await _settle(event_clips.quiet)
        assert await event_clips.stop("staff") is True
        assert not event_clips.quiet()
        assert _sent(screen)[-1]["type"] == "mirror_clip_stop"
        await asyncio.sleep(0.05)
        assert said == []
        assert await event_clips.stop("staff") is False

    asyncio.run(go())


def test_the_next_scene_takes_the_film_down(event, said, screen, monkeypatch):
    async def go():
        await event_host.play_clip("opening")
        assert await _settle(event_clips.quiet)
        await event_host.start_scene("introduce")
        assert not event_clips.quiet()

    asyncio.run(go())


def test_the_screen_is_opened_for_the_clip_when_it_is_not_up(event, said, monkeypatch):
    face_mirror.reset()
    monkeypatch.setattr("app.room_screens.offered", lambda: True)
    opened = []
    queue: asyncio.Queue = asyncio.Queue(maxsize=50)

    async def fake_open(name=None, *, by="asked"):
        opened.append((name, by))
        face_mirror._clients.add(queue)
        return {"ok": True, "screen": "x"}

    monkeypatch.setattr("app.room_screens.open_screen", fake_open)

    async def go():
        await event_host.play_clip("opening")
        assert await _settle(event_clips.quiet)
        assert opened == [(None, "scene")]
        await event_clips.stop()

    asyncio.run(go())
    face_mirror.reset()


def test_a_room_pc_that_does_not_answer_is_said_to_staff(event, said, monkeypatch):
    face_mirror.reset()
    monkeypatch.setattr("app.room_screens.offered", lambda: True)

    async def fake_open(name=None, *, by="asked"):
        return {"ok": False, "error": "unreachable"}

    monkeypatch.setattr("app.room_screens.open_screen", fake_open)

    async def go():
        await event_host.play_clip("opening")
        assert await _settle(lambda: event_clips.STATE["phase"] == event_clips.FAILED)
        assert event_clips.snapshot()["error_th"] == event_clips.ERRORS_TH["screen_refused"]
        assert not event_clips.quiet()

    asyncio.run(go())


def test_she_finishes_her_line_before_the_film_rolls(event, said, screen, monkeypatch):
    from app import session as session_module

    now = {"until": time.monotonic() + 0.2}

    class _Session:
        def _array_audible_until(self, _now):
            return now["until"]

    monkeypatch.setattr(session_module, "_active", _Session())

    async def go():
        await event_host.play_clip("opening", lead_in=True)
        await asyncio.sleep(0.05)
        assert not event_clips.quiet() and event_clips.STATE["phase"] == event_clips.OPENING
        assert await _settle(event_clips.quiet)
        await event_clips.stop()

    asyncio.run(go())


@pytest.mark.parametrize("clip_id,why", [("nope", "no_clip")])
def test_an_unknown_clip_is_refused(event, screen, clip_id, why):
    assert asyncio.run(event_host.play_clip(clip_id))["error"] == why


def test_a_clip_with_no_file_yet_is_refused_and_named_to_staff(event, screen):
    (event_clips.CLIPS_DIR / "opening.mp4").unlink()
    assert asyncio.run(event_host.play_clip("opening"))["error"] == "no_file"
    assert event_clips.snapshot()["clips"][0]["has_file"] is False
    assert not event_clips.offered()


def test_only_the_clips_folder_holds_clips(event, tmp_path):
    outside = tmp_path / "secret.mp4"
    outside.write_bytes(b"x")
    assert event_clips._clip_path(str(outside)) is None
    assert event_clips._clip_path(str(event_clips.CLIPS_DIR / ".." / "secret.mp4")) is None
    assert event_clips._clip_path(str(event_clips.CLIPS_DIR / "notes.txt")) is None
    assert event_clips._clip_path(None) is None
    assert event_clips._clip_path(str(event_clips.CLIPS_DIR / "opening.mp4")) is not None


def test_a_scene_with_a_clip_starts_it_and_lets_her_line_go_first(event, said, screen, monkeypatch):
    from app import session as session_module

    monkeypatch.setattr(session_module, "_active", None)

    async def go():
        result = await event_host.start_scene("film_scene")
        assert result["clip"]["ok"]
        assert not event_host.ceremony_now(), "a scene's film is the Event app's, not a ceremony"
        assert await _settle(event_clips.quiet)
        await asyncio.sleep(0.05)
        assert said and said[0][0] == "event_scene"
        await event_clips.stop()

    asyncio.run(go())


def test_the_tv_opens_only_if_it_is_not_up_a_reopen_would_drop_the_film(event, said, monkeypatch):
    from app import room_screens

    opened = []

    async def fake_open(name=None, *, by="asked"):
        opened.append(by)
        return {"ok": True}

    monkeypatch.setattr(room_screens, "open_screen", fake_open)
    monkeypatch.setattr(room_screens, "state", lambda: {"open": False, "screen": None})

    async def go():
        await event_host.start_scene("film_scene")
        assert await _settle(lambda: event_host.STATE["tv"] is not None)
        assert opened == ["scene"] and event_host.STATE["tv"]["ok"]
        await event_clips.stop()
        monkeypatch.setattr(room_screens, "state", lambda: {"open": True, "screen": "x"})
        event_host.STATE["tv"] = None
        await event_host.start_scene("film_scene")
        assert await _settle(lambda: event_host.STATE["tv"] is not None)
        assert opened == ["scene"]
        await event_clips.stop()
        # the ceremony order's screen step is the same code (event_host.big_screen)
        event_host.STATE["tv"] = None
        assert await event_host.big_screen("open", by="staff") == {"ok": True}
        assert opened == ["scene"] and event_host.STATE["tv"] == {"want": "open", "ok": True, "error": None}

    asyncio.run(go())


def test_a_tv_that_did_not_open_is_on_the_staff_page(event, said, monkeypatch):
    from app import room_screens

    async def fake_open(name=None, *, by="asked"):
        return {"ok": False, "error": "unreachable"}

    monkeypatch.setattr(room_screens, "open_screen", fake_open)
    monkeypatch.setattr(room_screens, "state", lambda: {"open": False, "screen": None})

    async def go():
        assert (await event_host.big_screen("open", by="staff"))["ok"] is False
        assert event_host.STATE["tv"] == {"want": "open", "ok": False, "error": "unreachable"}

    asyncio.run(go())
    assert any("เปิดจอใหญ่ไม่สำเร็จ" in w for w in event_host.mode_snapshot()["warnings"])


def test_the_scene_after_the_ceremony_takes_the_tv_down(event, said, monkeypatch):
    from app import room_screens

    closed = []

    async def fake_close(name=None, *, by="asked"):
        closed.append(by)
        return {"ok": True}

    monkeypatch.setattr(room_screens, "close_screen", fake_close)

    async def go():
        await event_host.start_scene("after_ceremony")
        assert await _settle(lambda: closed)

    asyncio.run(go())
    assert closed == ["scene"]


# ---- the screen's stream ------------------------------------------------------------------------

def test_a_screen_that_joins_late_is_told_the_cue():
    """The window takes seconds to open: a cue into that gap would be no clip at all."""
    face_mirror.reset()
    face_mirror.clip_prepare([{"id": "opening", "url": "/event-clip/abc"}])
    face_mirror.clip_play({"seq": 3, "id": "opening", "url": "/event-clip/abc"})
    types = [json.loads(m)["type"] for m in face_mirror.snapshot()]
    assert types == ["mirror_hello", "mirror_clips", "mirror_clip_play"]
    face_mirror.clip_stop()
    assert [json.loads(m)["type"] for m in face_mirror.snapshot()] == ["mirror_hello", "mirror_clips"]
    face_mirror.reset()


def test_the_faces_own_stream_cannot_carry_a_cue():
    """`view` still rebuilds the call's messages by type: nothing a tool result holds
    can put a film on the room's screen."""
    assert face_mirror.view({"type": "mirror_clip_play", "url": "http://x/y"}) is None
    assert face_mirror.view({"type": "mirror_clips", "clips": []}) is None


@pytest.fixture
def mirror_client(event, monkeypatch):
    from fastapi.testclient import TestClient

    from app import mirror_server

    monkeypatch.setattr(settings, "mirror_token", "room-secret")
    face_mirror.reset()
    with TestClient(mirror_server.app) as client:
        yield client
    face_mirror.reset()


def test_the_film_is_behind_the_screens_cookie_and_its_hash(event, mirror_client):
    sha = event_clips._sha(event_clips.CLIPS_DIR / "opening.mp4")
    assert mirror_client.get("/event-clip/" + sha).status_code == 403
    assert mirror_client.post("/event-clip-state", json={}).status_code == 403
    mirror_client.get("/mirror?token=room-secret")
    got = mirror_client.get("/event-clip/" + sha)
    assert got.status_code == 200 and got.content == b"not really a film"
    assert got.headers["content-type"].startswith("video/mp4")
    assert mirror_client.get("/event-clip/" + "0" * 64).status_code == 404
    assert mirror_client.get("/event-clip/../../.env").status_code == 404
    # a file beside it the event does not name is not served by any hash
    other = event_clips.CLIPS_DIR / "other.mp4"
    other.write_bytes(b"other")
    assert mirror_client.get("/event-clip/" + event_clips._sha(other)).status_code == 404


def test_the_screens_one_word_back_reaches_the_clip_and_nothing_else(event, mirror_client):
    mirror_client.get("/mirror?token=room-secret")
    assert mirror_client.post("/event-clip-state", json={"clip": "opening", "state": "ready"}).status_code == 204
    assert event_clips.STATE["ready"]["opening"] is True
    assert mirror_client.post("/event-clip-state", content=b"not json").status_code == 204
    assert mirror_client.post("/event-clip-state", content=b"x" * 3000).status_code == 413
    assert mirror_client.post("/event-clip-state", json={"clip": "other", "state": "ready"}).status_code == 204
    assert "other" not in event_clips.STATE["ready"]


# ---- the host's words ----------------------------------------------------------------------------

def _visible(profile, name, monkeypatch, *, face=True):
    from app.tools import registry

    token = registry.session_profile.set(profile)
    who = registry.robot_face_session.set(object() if face else None)
    try:
        return registry.allowed(registry._REGISTRY[name])
    finally:
        registry.robot_face_session.reset(who)
        registry.session_profile.reset(token)


def test_only_the_ceremony_app_sees_the_film_by_voice_and_only_with_a_film(event, screen, monkeypatch):
    """"Emma เปิดคลิป" is the host's, in the ceremony: the ceremony app's profile since 9 Nov. At
    the Event app's party it only ever refused (a guest must never take the room's screen)."""
    import app.tools.screens  # noqa: F401 -- registers it

    monkeypatch.setattr(settings, "tools_enabled", True)
    monkeypatch.setattr(settings, "tool_groups", "screens")
    assert _visible("ceremony", "event_clip", monkeypatch) is True
    for profile in ("condo", "emma", "joke", "event"):
        assert _visible(profile, "event_clip", monkeypatch) is False, profile
    assert _visible("ceremony", "event_clip", monkeypatch, face=False) is False
    (event_clips.CLIPS_DIR / "opening.mp4").unlink()
    assert _visible("ceremony", "event_clip", monkeypatch) is False


class _Sess:
    pass


def _said_by_host(words):
    session = _Sess()
    heard.bind_session(session)
    heard.mark_activity(session)
    heard.record(words, session)
    return session


def test_the_host_calls_for_the_film_by_her_name(event, said, screen):
    from app.tools.screens import event_clip

    async def go():
        _ceremony()
        session = _said_by_host("Emma เปิดคลิปให้แขกชมหน่อยครับ")
        assert session is not None
        result = await event_clip("play")
        assert result["ok"] and result["clip"] == "opening" and result["hardware"] == "requested"
        assert "ห้ามบอกว่าเห็นคลิปเล่นแล้ว" in result["instruction"]
        assert await _settle(lambda: event_clips.STATE["by"] == "voice")
        await event_clips.stop()

    asyncio.run(go())


@pytest.mark.parametrize("words", [
    "เดี๋ยวเราจะชมคลิปกันครับ",           # to the seats: no name
    "Emma แนะนำตัวหน่อยครับ",             # her name: no film
    "Emma ไม่ต้องเปิดคลิปนะครับ",          # no
])
def test_words_that_are_no_cue(event, said, screen, words):
    from app.tools.screens import event_clip

    async def go():
        _ceremony()
        _said_by_host(words)
        result = await event_clip("play")
        assert result["ok"] is False and result["reason"] == "not_heard"
        assert event_clips.STATE["phase"] == event_clips.IDLE

    asyncio.run(go())


def test_at_the_party_no_guest_takes_the_screen(event, said, screen):
    from app.tools.screens import event_clip

    async def go():
        _said_by_host("Emma เปิดคลิปหน่อย")
        result = await event_clip("play")
        assert result["ok"] is False and result["reason"] == "not_ceremony"
        assert "ห้ามบอกว่าเปิดแล้ว" in result["instruction"]

    asyncio.run(go())


def test_a_stop_needs_nobodys_words(event, said, screen):
    from app.tools.screens import event_clip

    async def go():
        await event_host.play_clip("opening")
        assert await _settle(event_clips.quiet)
        result = await event_clip("stop")
        assert result["ok"] and result["stopped"] is True
        assert not event_clips.quiet()

    asyncio.run(go())


def test_a_speech_she_stays_silent_through_is_not_chased(monkeypatch, fast, rows):
    """REPLY_STALL_S reads her silence as a server gone quiet: a nudge, then the speech
    asked back to her as a question, then the call moved to the fallback model."""
    provider, rejoined = _provider(monkeypatch, [True, False])
    old = provider._session
    _ceremony()
    _question(provider, "ขอต้อนรับแขกผู้มีเกียรติทุกท่านครับ")
    assert _client_contents(old) == [] and rejoined == []
    assert not [r for r in rows if r["event"] == "reply_stall"]


def test_her_name_left_unanswered_is_still_chased(monkeypatch, fast, rows):
    """The server going silent on the one sentence she was asked is the same 146 s."""
    provider, rejoined = _provider(monkeypatch, [True, False])
    old = provider._session
    _ceremony()
    _question(provider, "Emma แนะนำตัวหน่อยครับ")
    assert _client_contents(old) == [{"turn_complete": True}]
    assert [r["stage"] for r in rows if r["event"] == "reply_stall"][:1] == ["nudge"]


def test_she_is_kept_quiet_by_the_provider_over_a_clip(event):
    from app.providers import gemini

    assert not gemini._walk_quiet()
    event_clips.STATE["phase"] = event_clips.PLAYING
    assert gemini._walk_quiet() == "clip"
    assert gemini._ceremony_named_only() is False
    _ceremony()
    assert gemini._ceremony_named_only() is True


# ---- the pages -------------------------------------------------------------------------------------

def test_the_tv_page_plays_it_over_the_face_and_its_socket_stays_one_way():
    body = PAGE[PAGE.index("function mirrorClipSay"):PAGE.index("function startMirrorAudio")]
    assert "mirrorClipSay" in body and "/event-clip-state" in body
    assert "ws.send" not in body and "sock.send" not in body
    # fetched whole ahead of the cue, streamed only if the cue beat it
    assert "r.blob()" in body and "file.blob) || cue.url" in body
    # the handling stays in the mirror's own socket: the shared handler is untouched
    handler = PAGE[PAGE.index("function onMessage"):]
    handler = handler[:handler.index("function startMirror")]
    assert "mirror_clip" not in handler


def test_a_card_the_staff_page_hides_is_hidden():
    """`.walk { display: grid }` outranks the browser's [hidden]: FACE_APPROACH off still
    showed that card, empty, with a blank green button."""
    style = STAFF_PAGE[STAFF_PAGE.index("<style>"):STAFF_PAGE.index("</style>")]
    assert "[hidden] { display: none !important; }" in style


def test_the_staff_page_says_the_mode_and_runs_the_film():
    for piece in ("modeCard", "clipCard", "/event/clip", "โหมดปกติ", "หยุดคลิป",
                  "เจอคนที่จำได้แล้วเดินเข้าไปทัก", "แอป Emma Ceremony เปิดอยู่"):
        assert piece in STAFF_PAGE, piece
    # the ceremony's own words live on the Emma Ceremony app's page (owner 9 Nov: "แยก ... ไม่งั้นจะปนกัน")
    ceremony_page = (ROOT / "client" / "ceremony-control.html").read_text(encoding="utf-8")
    for piece in ("โหมดพิธีเปิด", "ตอบเมื่อเรียกชื่อ Emma", "พูดเฉพาะบทที่กด", "หยุดคลิป"):
        assert piece in ceremony_page, piece
        if piece != "หยุดคลิป":
            assert piece not in STAFF_PAGE, piece


def test_the_staff_page_routes_the_film_and_never_a_file(event, said, screen, monkeypatch):
    from fastapi.testclient import TestClient

    from app import event_control_server

    monkeypatch.setattr(settings, "event_control_key", "staff-secret")
    with TestClient(event_control_server.app) as client:
        client.get("/event-control?key=staff-secret")
        state = client.get("/event/state").json()
        assert state["mode"]["ceremony"] is False
        assert state["clips"]["clips"][0]["id"] == "opening"
        assert "path" not in json.dumps(state["clips"]) and ".mp4" not in json.dumps(state["clips"])
        refused = client.post("/event/clip", json={"clip": "../../.env"}).json()
        assert refused["ok"] is False and refused["error"] == "no_clip"
        assert client.post("/event/clip", json={"action": "stop"}).json() == {"ok": True, "stopped": False}


# ---- deploy ----------------------------------------------------------------------------------------

def test_deploy_ships_what_the_event_names_from_its_one_folder(tmp_path):
    from scripts import robot_server

    (tmp_path / "data" / "events" / "clips").mkdir(parents=True)
    (tmp_path / "data" / "events" / "clips" / "opening.mp4").write_bytes(b"x")
    (tmp_path / "data" / "events" / "clips" / "not_named.mp4").write_bytes(b"x")
    (tmp_path / "secret.mp4").write_bytes(b"x")
    (tmp_path / "data" / "events" / "floor1_event.json").write_text(json.dumps({"clips": {
        "a": {"file": "data/events/clips/opening.mp4"},
        "b": {"file": "secret.mp4"},
        "c": {"file": "data/events/clips/../../../secret.mp4"},
        "d": {"file": "data/events/clips/missing.mp4"}}}), encoding="utf-8")
    assert robot_server.event_clip_files(tmp_path) == {"data/events/clips/opening.mp4"}


def test_no_event_file_no_clips_to_ship(tmp_path):
    from scripts import robot_server

    assert robot_server.event_clip_files(tmp_path) == set()


def test_the_films_stay_out_of_git():
    assert "data/events/clips/" in (ROOT / ".gitignore").read_text(encoding="utf-8")


# ---- a cue-only ceremony (review 7 Nov: she answered the host live AND said the pressed line) ----
#
# In a ceremony she answered whenever the host said "เอ็มม่า" -- and nearly every line of
# the host's names her: a live answer, then the pressed line; tools run by that answer
# (the film rolled, a story, a gesture); applause cutting her line as a barge-in. The
# ceremony scene says `"replies": "cues"`: the mic goes up as silence, only a server
# turn is hers to say, only a stop runs -- except while the robot moves (a spoken stop
# must be heard), and inside the one live window staff open (s4_recommend).

def _cue_only(session=None):
    """The cue-only ceremony (the file's "replies": "cues"): the ceremony app's call."""
    return ceremony_on(replies="cues", session=session)


class _Upstream:
    """The Live socket: what the mic sent up, with the audio's bytes."""

    def __init__(self):
        self.sent: list = []

    async def send_realtime_input(self, **kw):
        for key, value in kw.items():
            if value is not None:
                self.sent.append((key, getattr(value, "data", None)))

    async def send_client_content(self, **kw):
        self.sent.append(("client_content", None))


def _mic(monkeypatch, answers, *, vad=True):
    """A real GeminiProvider over a real VadGate whose detector says what `answers` say."""
    from app import vad_gate
    from app.providers.gemini import GeminiProvider
    from tests.test_reply_stall import Scripted

    monkeypatch.setattr(vad_gate, "for_session",
                        lambda **_: vad_gate.VadGate(Scripted(answers), prefix_padding_ms=100) if vad else None)
    provider = GeminiProvider("Kore", "x")
    provider.model = "gemini-3.8-live"
    provider._session = _Upstream()
    return provider


def _still(monkeypatch, moving=False):
    from app import focus

    monkeypatch.setattr(focus, "_robot_moving", lambda: moving)


def test_the_ceremony_on_the_day_is_cue_only_and_the_word_is_the_ceremony_files(tmp_path, monkeypatch):
    """CEREMONY_FILE says "replies": "cues" (the 7 Nov review's decision, now the file's own word);
    "named" is the older ceremony; absent, odd, or no file at all = cues (a ceremony app that
    cannot read its script is a quiet one, not one that answers the room)."""
    assert json.loads(CEREMONY_FILE.read_text(encoding="utf-8"))["replies"] == "cues"
    assert ceremony.file_replies({"replies": "named"}) == "named"
    for doc in ({}, {"replies": "all"}, {"replies": None}):
        assert ceremony.file_replies(doc) == "cues", doc
    monkeypatch.setattr(settings, "ceremony_file", "")
    assert ceremony.file_replies() == "cues"
    named = tmp_path / "c.json"
    named.write_text(json.dumps({"format": "emma-event-script-v1", "replies": "named", "cues": []}), encoding="utf-8")
    monkeypatch.setattr(settings, "ceremony_file", str(named))
    from app import event_script

    event_script.reset()
    ceremony_on(replies=None)                            # the claim reads the file
    assert event_host.ceremony_now() and not event_host.cue_only()


def test_the_ceremony_apps_call_is_cue_only_until_it_ends(event, said, monkeypatch):
    monkeypatch.setattr("app.room_screens.offered", lambda: False)
    assert not event_host.cue_only()
    ceremony_on()
    assert event_host.cue_only() and event_host.cue_only_closed()
    mode = event_host.mode_snapshot()
    assert mode["cue_only"] is True and mode["needs_name"] is False and mode["live"] is None
    ceremony_off()
    assert not event_host.cue_only() and not event_host.cue_only_closed()
    _cue_only()
    ceremony.STATE["replies"] = "named"
    assert event_host.ceremony_now() and not event_host.cue_only(), "the named-only ceremony is not cue-only"


def test_after_a_restart_cue_only_is_back_with_the_apps_new_call(event, said, monkeypatch):
    """The server restarted under the ceremony (Android reset): nothing comes back from a file;
    the ceremony app's face dials again, and that call's claim is the ceremony again."""
    monkeypatch.setattr("app.room_screens.offered", lambda: False)
    _cue_only()
    ceremony.reset()                                     # the process died
    asyncio.run(event_host.recover_after_restart())
    assert not event_host.cue_only()
    _cue_only()                                          # the app's face dialled again
    assert event_host.cue_only()


def test_the_live_window_opens_only_in_a_ceremony_and_shuts_every_way(event, said, rows, monkeypatch):
    from app import event_cues

    monkeypatch.setattr("app.room_screens.offered", lambda: False)
    assert event_host.live_open(cue_id="s4_recommend", brief_th="แนะนำห้อง") is False
    assert event_host.live_state() is None
    _cue_only()
    assert event_host.live_open(cue_id="s4_recommend", brief_th="แนะนำห้องวิวทะเล", max_s=60)
    st = event_host.live_state()
    assert set(st) == {"cue", "opened_at", "until", "why"} and st["cue"] == "s4_recommend" and st["why"] is None
    assert 59.9 <= st["until"] - st["opened_at"] <= 60.1
    assert event_host.cue_only() and not event_host.cue_only_closed() and not event_host.mic_deaf()
    mode = event_host.mode_snapshot()
    assert mode["live"]["cue"] == "s4_recommend" and mode["needs_name"] is True
    event_host.live_close("next")                        # staff pressed "ถัดไป"
    assert event_host.live_state() is None and event_host.cue_only_closed()
    # its own end
    assert event_host.live_open(cue_id="s4_recommend", brief_th=None, max_s=0.05)
    time.sleep(0.1)
    assert event_host.live_state() is None and event_host.cue_only_closed()
    # the default bound
    assert event_host.live_open(cue_id="s4_recommend", brief_th=None)
    st = event_host.live_state()
    assert abs(st["until"] - st["opened_at"] - settings.event_live_max_s) < 0.1
    # a newer window replaces it; the red stop of the cues, the staff stop, a new scene
    assert event_host.live_open(cue_id="s4_recommend", brief_th=None)
    asyncio.run(event_cues.cut("staff"))
    assert event_host.live_state() is None
    assert event_host.live_open(cue_id="s4_recommend", brief_th=None)
    asyncio.run(event_host.stop())
    assert event_host.live_state() is None
    assert event_host.live_open(cue_id="s4_recommend", brief_th=None)
    # an Event page's scene is refused in the ceremony and leaves the window alone (no "scene")
    assert asyncio.run(event_host.start_scene("introduce"))["error"] == "ceremony_app"
    assert event_host.live_state() is not None
    ceremony_off()                                       # the ceremony app's call ended
    assert event_host.live_state() is None
    closes = [r["why"] for r in rows if r["event"] == "event_live" and r["action"] == "close"]
    assert closes == ["next", "expired", "replaced", "cut", "stop", "app_closed"]
    opens = [r for r in rows if r["event"] == "event_live" and r["action"] == "open"]
    assert opens[0] == {"event": "event_live", "action": "open", "cue": "s4_recommend", "max_s": 60.0, "brief": True}
    assert [r["why"] for r in rows if r["event"] == "event_live" and r["action"] == "refused"] == ["no_ceremony"]


def test_the_live_brief_rides_beside_tool_results_while_the_window_is_open(event):
    from app.tools import registry

    result = {"ok": True}
    brief = "แนะนำห้องวิวทะเลจากห้องว่างจริง"
    token = registry.session_profile.set("ceremony")
    try:
        _cue_only()
        out = registry.model_view("search_condo_info", result)
        assert out["event_scene"] == event_host.CEREMONY_RULE and "event_live" not in out
        assert event_host.live_open(cue_id="s4_recommend", brief_th=brief)
        out = registry.model_view("search_condo_info", result)
        assert out["event_live"] == event_host.LIVE_NOTE % ("s4_recommend", brief)
        assert out["event_scene"] and result == {"ok": True}, "beside the ceremony's note, the result not mutated"
        event_host.live_close("next")
        assert "event_live" not in registry.model_view("search_condo_info", result)
    finally:
        registry.session_profile.reset(token)
    # any other profile: the very same object (the sales room, byte for byte) -- and the Event
    # app's own profile never carries the ceremony's window (owner 9 Nov: "ไม่งั้นจะปนกัน")
    assert event_host.live_open(cue_id="s4_recommend", brief_th=brief)
    for profile in ("condo", "event"):
        token = registry.session_profile.set(profile)
        try:
            assert registry.model_view("search_condo_info", result) is result, profile
        finally:
            registry.session_profile.reset(token)


def test_the_live_window_is_context_only_with_the_note_switch(rows, monkeypatch):
    import types as pytypes

    from app import session as session_module

    sent = []

    class Provider:
        async def send_context_note(self, text):
            sent.append(text)
            return True

    _cue_only(pytypes.SimpleNamespace(provider=Provider()))
    assert session_module._active.provider is not None

    async def go(on):
        monkeypatch.setattr(settings, "event_cue_context_note", on)
        assert event_host.live_open(cue_id="s4_recommend", brief_th="แนะนำห้อง")
        await asyncio.sleep(0.05)

    asyncio.run(go(False))
    assert sent == []
    asyncio.run(go(True))
    assert len(sent) == 1 and "s4_recommend" in sent[0] and "ไม่ต้องตอบ" in sent[0]
    assert [r for r in rows if r["event"] == "event_live_note"] == [
        {"event": "event_live_note", "cue": "s4_recommend", "sent": True}]


# -- the reply gate

def _cue_gate(on=True):
    return NoiseReplyGate(0.06, named_only=lambda: True, cue_only=lambda: on)


@pytest.mark.parametrize("words", ["เอ็มม่า เปิดวิดีโอหน่อยครับ", "Emma, hello", "ขอบคุณแขกทุกท่านครับ"])
def test_no_answer_to_the_room_is_played_named_or_not(words):
    gate = _cue_gate()
    gate.segment(0.2)
    gate.heard(words)
    assert gate.push(_t("ได้เลยค่ะ")) == [] and gate.push(_a(NOISE_HOLD_BYTES)) == []
    owed, dropped = gate.close()
    assert owed == [] and dropped["why"] == "cue_only"


def test_a_server_turn_is_hers_to_say_and_nothing_she_starts_herself():
    gate = _cue_gate()
    gate.text_turn()                                     # a cue staff pressed, a scene's line
    first = _t("ขอต้อนรับแขกทุกท่านค่ะ")
    assert gate.push(first) == [first]
    gate.close()
    # a turn of hers with nothing before it (spontaneous): not a server turn
    assert gate.push(_t("ค่ะ")) == [] and gate.close()[1]["why"] == "cue_only"


def test_without_cue_only_the_ceremony_gate_is_as_it_was():
    gate = _cue_gate(on=False)
    gate.segment(0.2)
    gate.heard("Emma เปิดคลิปหน่อย")
    first = _t("ค่ะ")
    assert gate.push(first) == [first]

    def broken():
        raise RuntimeError("event_host unreadable")

    gate = NoiseReplyGate(0.06, named_only=lambda: True, cue_only=broken)
    gate.segment(0.2)
    gate.heard("Emma เปิดคลิปหน่อย")
    assert gate.push(first) == [first], "a probe that fails is the ordinary gate"


def test_the_live_provider_drops_her_named_answer_and_the_window_lets_it_through(monkeypatch, rows):
    from app.providers.gemini import GeminiProvider

    provider = GeminiProvider("Kore", "x")
    _cue_only()
    gate = provider._noise_gate
    gate.segment(0.2)
    gate.heard("เอ็มม่า เปิดวิดีโอครับ")
    assert provider._gated(_t("ได้เลยค่ะ เชิญชมวิดีโอค่ะ")) == []
    assert provider._noise_close() == []
    assert [r["why"] for r in rows if r["event"] == "noise_reply_dropped"] == ["cue_only"]
    assert event_host.live_open(cue_id="s4_recommend", brief_th=None)
    gate.segment(0.2)
    gate.heard("เอ็มม่า ห้องไหนวิวสวยที่สุดครับ")
    first = _t("ห้องฝั่งทะเลชั้นสูงวิวสวยที่สุดค่ะ")
    assert provider._gated(first) == [first], "the live window: the named-only ceremony of before"


# -- the mic

@pytest.mark.parametrize("case", ["still", "moving", "live", "named_only", "ordinary"])
def test_the_mic_is_deaf_unless_the_robot_moves_or_the_window_is_open(monkeypatch, case):
    from tests.test_reply_stall import LOUD

    provider = _mic(monkeypatch, [True] * 6)
    if case != "ordinary":
        _cue_only()
    if case == "named_only":
        ceremony.STATE["replies"] = "named"
    if case == "live":
        assert event_host.live_open(cue_id="s4_recommend", brief_th=None)
    _still(monkeypatch, moving=case == "moving")

    async def go():
        for _ in range(3):
            await provider.send_audio(LOUD)

    asyncio.run(go())
    kinds = [k for k, _ in provider._session.sent]
    if case == "still":
        assert kinds == [], "something of the room went up"
    else:
        assert kinds[0] == "activity_start"
        assert [d for k, d in provider._session.sent if k == "audio"] == [LOUD] * 3


def test_a_sentence_the_window_shuts_on_closes_on_silence(monkeypatch):
    """Staff press "ถัดไป" while the host is still talking: the segment is not cut off
    mid-air (no activity_end would leave the model waiting) -- it is fed silence until
    the detector's own hangover ends it."""
    from tests.test_reply_stall import LOUD

    provider = _mic(monkeypatch, [True, True, True, False])
    _cue_only()
    assert event_host.live_open(cue_id="s4_recommend", brief_th=None)
    _still(monkeypatch)

    async def go():
        await provider.send_audio(LOUD)                  # the host, inside the window
        event_host.live_close("next")
        for _ in range(3):
            await provider.send_audio(LOUD)

    asyncio.run(go())
    kinds = [k for k, _ in provider._session.sent]
    assert kinds == ["activity_start", "audio", "audio", "audio", "audio", "activity_end"]
    audio = [d for k, d in provider._session.sent if k == "audio"]
    assert audio[0] == LOUD and audio[1:] == [bytes(len(LOUD))] * 3


def test_without_the_local_gate_a_deaf_mic_sends_nothing(monkeypatch):
    from tests.test_reply_stall import LOUD

    provider = _mic(monkeypatch, [], vad=False)
    assert provider._vad_gate is None
    _still(monkeypatch)
    _cue_only()
    asyncio.run(provider.send_audio(LOUD))
    assert provider._session.sent == []
    ceremony.STATE["replies"] = "named"
    asyncio.run(provider.send_audio(LOUD))
    assert provider._session.sent == [("audio", LOUD)]


def test_her_silence_to_the_room_is_not_chased_and_a_stop_still_is(monkeypatch):
    provider = _mic(monkeypatch, [])
    _cue_only()
    _still(monkeypatch)
    assert provider._ceremony_silence_is_right("เอ็มม่า แนะนำตัวหน่อยครับ") is True
    _still(monkeypatch, moving=True)
    assert provider._ceremony_silence_is_right("เอ็มม่า หยุด") is False


def test_offline_the_mic_is_deaf_too(monkeypatch):
    from app.providers import offline as module
    from tests.test_reply_stall import LOUD

    fed = []

    class Gate:
        keep_audio = True

        def feed(self, pcm):
            fed.append(pcm)
            return []

    monkeypatch.setattr(module, "_own_gate", lambda vad_floor: Gate())
    p = module.OfflineProvider("Despina", "x")
    _still(monkeypatch)
    _cue_only()
    asyncio.run(p.send_audio(LOUD))
    ceremony.STATE["replies"] = "named"
    asyncio.run(p.send_audio(LOUD))
    assert fed == [bytes(len(LOUD)), LOUD]


@pytest.mark.parametrize("words, action", [("เอ็มม่า ห้องน้ำอยู่ไหนคะ", "silent"), ("หยุด", "stop")])
def test_offline_answers_nothing_but_a_stop(monkeypatch, rows, words, action):
    import types as pytypes

    from app.providers import offline as module

    monkeypatch.setattr(module, "_own_gate", lambda vad_floor: None)
    p = module.OfflineProvider("Despina", "x")
    performed = []

    async def perform(route, lang):
        performed.append(route.action)

    monkeypatch.setattr(p, "_perform", perform)

    class Engine:
        ready = True

        @staticmethod
        def transcribe(pcm, hint):
            return pytypes.SimpleNamespace(text=words, lang="th", decode_ms=1, lid_ms=0)

    _cue_only()
    asyncio.run(p._heard_segment((1, 0.0, 1.0, b"\x00\x00" * 1600), Engine))
    assert performed == [action]
    if action == "silent":
        assert [r["why"] for r in rows if r["event"] == "offline_route"] == ["cue_only"]


# -- the tools

def test_no_tool_runs_but_a_stop(monkeypatch, rows):
    from app.tools import registry

    ran = []
    names = ("zz_cue_only_any", "zz_cue_only_stop")
    saved = dict(registry._REGISTRY)

    def make(name, preempt=None):
        async def handler(**kw):
            ran.append(name)
            return {"ok": True}

        registry.tool(name=name, description="test", preempt=preempt)(handler)

    make(names[0])
    make(names[1], preempt=lambda args: True)

    async def both():
        return [await registry.dispatch(n, {}) for n in names]

    try:
        _cue_only()
        refused, stopped = asyncio.run(both())
        assert refused == {"ok": False, "error": "cue_only", "instruction": registry.CUE_ONLY_REFUSAL}
        assert stopped["ok"] and ran == ["zz_cue_only_stop"]
        assert {"event": "tool_refused", "name": "zz_cue_only_any", "why": "cue_only"} in rows
        # the live window: tools as in the named-only ceremony
        assert event_host.live_open(cue_id="s4_recommend", brief_th=None)
        asyncio.run(both())
        assert ran[-2:] == list(names)
        # no cue-only ceremony: every tool as before
        event_host.live_close("next")
        ceremony.STATE["replies"] = "named"
        asyncio.run(both())
        assert ran[-2:] == list(names)
    finally:
        registry._REGISTRY.clear()
        registry._REGISTRY.update(saved)


def test_the_stops_that_get_through_are_the_real_stops():
    import app.tools.body  # noqa: F401 -- registered on import
    import app.tools.robot  # noqa: F401
    from app.tools import registry

    stop, body = registry.get("stop_moving"), registry.get("move_body")
    if stop is None or body is None:
        pytest.skip("another test cleared the registry: WHICH tools pass a cue-only ceremony is NOT checked here")
    assert registry._preempts(stop, {})
    assert registry._preempts(body, {"action": "stop"}) and not registry._preempts(body, {"action": "wave"})
    for name in ("event_clip", "present_story", "say_script_line", "search_condo_info", "go_to_place"):
        entry = registry.get(name)
        assert entry is None or not registry._preempts(entry, {}), name


# -- the chest screen

def test_a_touch_on_the_chest_screen_is_ignored(rows):
    from tests.test_language_menu import _session

    sess = _session()
    _cue_only()
    for ask in ({"type": "quick_ask", "id": "units", "lang": "en"}, {"type": "quick_ask", "id": "lang_en"},
                {"type": "quick_ask", "id": "story_fx_ground_back"}, {"type": "quick_ask", "id": "../x y"}):
        asyncio.run(sess._quick_ask(ask))
        sess._quick_ask_at = -1e9
    # (the live window too: a touch there would take the host's question)
    assert event_host.live_open(cue_id="s4_recommend", brief_th=None)
    asyncio.run(sess._quick_ask({"type": "quick_ask", "id": "units", "lang": "en"}))
    assert sess.provider.sent == [] and sess.page == []
    ignored = [r["id"] for r in rows if r["event"] == "quick_ask_ignored"]
    assert ignored == ["units", "lang_en", "story_fx_ground_back", None, "units"]
    assert not [r for r in rows if r["event"] == "quick_ask"]
    # out of it: the tap goes as ever
    event_host.live_close("next")
    ceremony.STATE["replies"] = "named"
    sess._quick_ask_at = -1e9
    asyncio.run(sess._quick_ask({"type": "quick_ask", "id": "units", "lang": "en"}))
    assert len(sess.provider.sent) == 1


def test_a_call_reopened_mid_ceremony_does_not_greet_the_room(monkeypatch):
    """The server or the face restarted under a cue-only ceremony: a greeting is a server turn,
    so the cue-only gate would play "Hello everyone" over the host. Review 7 Nov."""
    provider = _mic(monkeypatch, [])
    provider.greeting = "Greet the guest."
    sent: list[str] = []

    async def send_text(text, **_):
        sent.append(text)

    monkeypatch.setattr(provider, "send_text", send_text)
    _cue_only()
    asyncio.run(provider._send_greeting())
    assert sent == []
    ceremony.STATE["replies"] = "named"                    # the named-only ceremony / a plain day
    asyncio.run(provider._send_greeting())
    assert sent == ["Greet the guest."]


def test_the_stall_chase_never_asks_the_host_back_while_the_robot_moves(monkeypatch):
    """Robot moving (a walk, a handshake): the mic is open for a spoken stop, and her silence
    to the host was chased -- nudge, then a rejoin that asked the host's line back as TEXT, a
    server turn the cue-only gate PLAYS: her live answer over the ceremony (review 7 Nov,
    round 3). One empty "your turn" at most; no text, no rejoin, no fallback."""
    from app import focus
    from tests.test_reply_stall import _provider, _question
    from app import turnlog

    rows: list[dict] = []
    monkeypatch.setattr(turnlog, "record", lambda event, **kw: rows.append({"event": event, **kw}))
    monkeypatch.setattr(settings, "reply_stall_s", 0.05)
    monkeypatch.setattr(settings, "reply_stall_reconnect_s", 0.05)
    monkeypatch.setattr(settings, "gemini_model_fallback", "gemini-2.5-flash-native-audio-preview")
    provider, rejoined = _provider(monkeypatch, [True, False])
    _cue_only()
    monkeypatch.setattr(focus, "_robot_moving", lambda: True)
    assert event_host.cue_only_closed() and not event_host.mic_deaf()
    _question(provider, "เอ็มม่า ยินดีต้อนรับครับ")
    stages = [r["stage"] for r in rows if r["event"] == "reply_stall"]
    assert stages == ["nudge"] and rejoined == []          # the empty "your turn" only
