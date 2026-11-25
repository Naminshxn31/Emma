"""The event host (owner 30 Oct 2026): Emma hosting an event on floor 1, the
scenes switched by staff from a phone -- app/event_host.py, app/event_walk.py,
app/event_control_server.py, the `event` profile and the "Emma Event" app.

What these hold down:
* the event profile is the sales host, byte for byte, plus its own block;
* a voice never walks it (no walking tools), every stop path stops its walk;
* a walk needs the switch, a recorded spot, a robot face and a watching
  staff page -- and stops when either goes away;
* the staff page needs its own key; the sales room gains nothing by default.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

from app import event_host, event_walk, greeter
from app.config import settings

ROOT = Path(__file__).resolve().parents[1]
TRACKED = ROOT / "tests" / "data" / "floor1_event_rehearsed.json"


@pytest.fixture
def event(tmp_path, monkeypatch):
    """The tracked event file in a tmp folder (the spots file is written beside
    it), with a route file of its own: floor1.json is gitignored."""
    doc = json.loads(TRACKED.read_text(encoding="utf-8"))
    # The debut as it was before the box (owner 23 Nov: "หุ่นอยู่ในกล่อง ... เดินออกมาก่อน จึงค่อยยกมือ" -- the tracked
    # file's `walk_out_of_box`): the walk-and-wave of 10 Nov, the road every test here written before it holds, and
    # still the road of a scene without the field. The box's own road: tests/test_debut_box.py (`in_the_box`).
    for s in doc["scenes"]:
        if s["id"] == "debut":
            s["walk_out_of_box"] = False
    route = tmp_path / "route.json"
    route.write_text(json.dumps({"dock": {"pose": [-7.3, -6.2, 168.8], "approach": [-9.0, -6.2]},
                                 "paths": {}}), encoding="utf-8")
    doc["route_file"] = str(route)
    path = tmp_path / "floor1_event.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "event_file", str(path))
    monkeypatch.setattr(event_walk, "RUN_DIR", tmp_path / ".run")
    # the fake walkers below say nothing of a gate; the tests of the gate set this
    monkeypatch.setattr(event_walk, "GATE_WAIT_S", 0.0)
    event_host.reset()
    return path


def line_at_the_spot(path):
    """The arrival road -- a scene that keeps its line for the spot -- on the debut: without
    `say_on_walk` (owner 10 Nov moved the debut's line onto the walk: "เดินไปด้านหน้าพร้อมโบกมือ
    และพูด", tests/test_event_recorded_line.py). The road itself stays every other scene's."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    for s in doc["scenes"]:
        if s["id"] == "debut":
            s["say_on_walk"] = False
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def said(monkeypatch):
    out = []

    async def fake_say(text, source, still):
        out.append((source, text, still()))

    monkeypatch.setattr(event_host, "_say", fake_say)
    return out


def _walker(monkeypatch, script: str):
    """board_walk replaced by a small Python program (prints, sleeps, exits)."""
    async def spawn(route):
        assert json.loads(Path(route).read_text(encoding="utf-8"))["paths"]["event"]["steps"]
        return await asyncio.create_subprocess_exec(
            sys.executable, "-c", script, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT)

    monkeypatch.setattr(event_walk, "_spawn", spawn)
    monkeypatch.setattr(event_walk, "_faces", lambda: True)
    monkeypatch.setattr(event_walk, "_other_walker", lambda: False)
    monkeypatch.setattr(settings, "event_walk_enabled", True)


ARRIVES = "print('[ 0.1] step 1 done', flush=True); print('[ 0.2] path event done', flush=True)"
WALKS_ON = "import time; print('[ 0.1] step 1', flush=True); time.sleep(30)"


async def _settle(cond, timeout=10.0):
    for _ in range(int(timeout / 0.05)):
        if cond():
            return True
        await asyncio.sleep(0.05)
    return False


# ---- the file ----------------------------------------------------------------------

def test_the_tracked_event_file_is_whole():
    doc = event_host._normalise(json.loads(TRACKED.read_text(encoding="utf-8")), {})
    ids = [s["id"] for s in doc["scenes"]]
    # no ceremony scene since 9 Nov: the ceremony is the Emma Ceremony app (owner "ไม่งั้นจะปนกัน")
    assert ids == ["debut", "counter_handshake", "guest_handshake", "after_ceremony", "roam_lobby",
                   "welcome", "escort_multimedia", "tables", "introduce", "farewell", "rest"]
    # the page's big tile (owner 9 Nov "เอาแบบเดินเปิดตัว"): the one quick scene, a verified group
    debut = doc["scenes"][0]
    assert [s["id"] for s in doc["scenes"] if s["quick"]] == ["debut"]
    assert debut["gesture"] == "greet_arm" and debut["spot"] == "stage_centre"
    # card 2 (owner 21 Nov: "เดินขวามาที่หน้าเคาน์เตอร์ เป็นจุดที่มิกิจะพูด ... ยื่นมือท่าจับมือ" + "พูดทักทาย
    # เดี๋ยวเอาบทให้"): the counter, silent on the way (Khun Miki may be speaking), a greeting there, the handshake
    # after her first words -- the only other scene with a gesture
    assert {s["id"]: s["gesture"] for s in doc["scenes"] if s["gesture"]} == {"debut": "greet_arm",
                                                                              "counter_handshake": "handshake",
                                                                              "guest_handshake": "handshake"}
    counter = doc["scenes"][1]
    assert counter["spot"] == "beside_host" and not counter.get("on_start_th") and counter.get("on_arrive_th")
    assert not counter["pageant"] and not counter["quick"] and not counter["say_on_walk"]
    # ... and where she stands (a refused walk: red stop latched, arm not down, battery, walking off) she says
    # nothing and gives no hand -- the greeting is the counter's (review 21 Nov)
    assert counter["on_stand_th"] == "" and event_host.scene_text(counter, walking=False, spot_id="beside_host") is None
    assert doc["spots"]["beside_host"]["steps"][-1]["to"] == [-8.62, -4.2] and doc["spots"]["beside_host"]["steps"][-1]["face"] == 0
    # card 3 (owner 22 Nov: "ตอนจับมือกับคุณมิกิ ก็ให้หันหลังเดินไปจับมือกับแขก"): from the counter's spot (walked
    # to first, from wherever she is), turned round, straight up to the guest -- the walk ends in front of whoever
    # is there (meet_m), the whole 1.2 m when nobody is; silent on the way, a greeting there, the hand after it;
    # nothing where she stands when the walk is refused (as card 2)
    guest = doc["scenes"][2]
    assert guest["spot"] == "guest_front" and not guest.get("on_start_th") and guest.get("on_arrive_th")
    assert guest["on_stand_th"] == "" and event_host.scene_text(guest, walking=False, spot_id="guest_front") is None
    steps = doc["spots"]["guest_front"]["steps"]
    assert steps[0]["to"] == doc["spots"]["beside_host"]["steps"][-1]["to"]
    assert steps[1] == {"do": "turn", "face": 180, "w_spin": 0.3, "label": steps[1]["label"]}   # turned slowly: behind her is unseen
    assert {k: steps[2][k] for k in ("forward", "speed", "meet_m")} == {"forward": 1.2, "speed": 0.15, "meet_m": 0.7}
    assert len(steps) == 3
    raw = json.loads(TRACKED.read_text(encoding="utf-8"))
    assert not [sc["id"] for sc in raw["scenes"] if "ceremony" in sc or "replies" in sc]
    for sc in raw["scenes"]:
        for ref in [sc.get("spot")] + list(sc.get("stops") or []):
            assert ref is None or ref in raw["spots"], (sc["id"], ref)
    assert raw["event"]["dates"] == ["2026-11-25", "2026-11-26"]   # the owner's dates
    # no invented event facts: the owner has not given any yet
    assert raw["event"]["facts_th"] == [] and raw["event"]["name_th"] is None


def test_spot_steps_walk_from_anywhere(event):
    spots = event_host.load()["spots"]
    reception = event_host.spot_steps(spots["reception"])
    assert reception == [{"do": "undock"},
                         {"to": [-9.0, -5.2], "speed": 0.3, "label": "กลางส่วนต้อนรับ", "face": 90.0}]
    entrance = event_host.spot_steps(spots["entrance"])[1]
    assert "face" not in entrance                        # no heading recorded: the board picks
    assert event_host.spot_steps(spots["dock"]) == [{"do": "dock"}]
    assert event_host.spot_steps(spots["table_1"]) is None   # not recorded yet
    door = event_host.spot_steps(spots["multimedia_door"])
    assert door[0] == {"do": "undock"} and door[2]["to"] == [-11.0, -6.9] and "fallback" not in door[2]


def test_a_spot_recorded_at_the_robot_wins_over_the_file(event):
    spots_file = event_host.spots_path()
    spots_file.write_text(json.dumps({"spots": {
        "table_1": {"pose": [-8.5, -2.0, 45.0], "recorded_at": "2026-11-24 18:00", "arrivals": 2},
        "multimedia_door": {"pose": [-11.7, -7.3, -98]}}}), encoding="utf-8")
    spots = event_host.load()["spots"]
    assert spots["table_1"]["pose"] == [-8.5, -2.0, 45.0] and spots["table_1"]["arrivals"] == 2
    # a measured pose replaces the file's hand-made steps
    assert event_host.spot_steps(spots["multimedia_door"]) == [
        {"do": "undock"}, {"to": [-11.7, -7.3], "speed": 0.3, "label": "หน้าห้องมัลติมีเดีย", "face": -98.0}]


def test_no_event_file_is_no_event():
    assert settings.event_file == ""                     # conftest
    assert event_host.load() is None
    assert event_host.snapshot()["event"] is None
    assert event_host.scene_note() is None


# ---- the profile -------------------------------------------------------------------

def test_the_event_profile_is_the_sales_host_plus_its_block(event):
    from app.prompts import PROFILES, SALES_PROFILES, build_instructions

    assert "event" in PROFILES and SALES_PROFILES == ("condo", "event", "ceremony")
    condo = build_instructions("Embassy World", profile="condo", robot_face=True)
    ev = build_instructions("Embassy World", profile="event", robot_face=True)
    block = event_host.event_block()
    assert block in ev
    assert ev.replace("\n\n" + block, "") == condo
    assert "[งานอีเวนต์" not in condo


def test_the_event_block_invents_nothing_and_names_no_walking_tool(event):
    block = event_host.event_block()
    assert "ห้ามแต่ง" in block and "ทีมงาน" in block
    assert "ห้ามบอกว่ากำลังเดินไปเอง" in block
    for name in ("go_to_place", "return_to_base", "board_walk"):
        assert name not in block
    # the event's facts are the file's own lines, nothing else
    doc = json.loads(event.read_text(encoding="utf-8"))
    doc["event"]["facts_th"] = ["งานเริ่ม 10 โมงเช้า"]
    doc["event"]["name_th"] = "Embassy Day"
    event.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    block = event_host.event_block()
    assert "- งานเริ่ม 10 โมงเช้า" in block and "งาน Embassy Day" in block


def test_the_session_takes_the_event_profile_and_treats_it_as_the_sales_host():
    source = (ROOT / "app" / "session.py").read_text(encoding="utf-8")
    assert 'if self.profile not in {"condo", "emma", "joke", "translator", "event", "ceremony"}:' in source
    assert 'getattr(self, "profile", "condo") not in SALES_PROFILES' in source       # the follow-up
    assert 'getattr(self, "profile", "condo") in SALES_PROFILES and _ENDS_ON_QUESTION' in source
    from app import quick_asks

    assert quick_asks.TOPICS["event"] is quick_asks.TOPICS["condo"]


def _visible(profile, name, monkeypatch):
    from app.tools import registry

    token = registry.session_profile.set(profile)
    try:
        return registry.allowed(registry._REGISTRY[name])
    finally:
        registry.session_profile.reset(token)


def test_a_voice_never_walks_the_event_host(monkeypatch):
    import app.tools.robot  # noqa: F401 -- registers the walking tools

    monkeypatch.setattr(settings, "tools_enabled", True)
    monkeypatch.setattr(settings, "tool_groups", "robot")
    for name in ("go_to_place", "return_to_base"):
        assert _visible("condo", name, monkeypatch) is True, name
        assert _visible("event", name, monkeypatch) is False, name
    assert _visible("event", "stop_moving", monkeypatch) is True


def test_tool_results_carry_the_scene_in_an_event_session_only(event):
    from app.tools import registry

    result = {"ok": True}
    token = registry.session_profile.set("event")
    try:
        assert registry.model_view("search_condo_info", result) is result     # no scene yet
        event_host.STATE["scene"] = "welcome"
        out = registry.model_view("search_condo_info", result)
        assert out["event_scene"].startswith("ฉากตอนนี้ (ทีมงานเลือก): รับแขกหน้างาน")
        assert result == {"ok": True}                                         # not mutated
    finally:
        registry.session_profile.reset(token)
    token = registry.session_profile.set("condo")
    try:
        assert registry.model_view("search_condo_info", result) is result
    finally:
        registry.session_profile.reset(token)


# ---- scenes without walking ---------------------------------------------------------

def test_a_scene_with_walking_off_is_acted_where_she_stands(event, said):
    async def go():
        out = await event_host.start_scene("welcome")
        await asyncio.sleep(0)
        return out

    out = asyncio.run(go())
    assert out["ok"] and out["walk"]["error"] == "disabled"
    assert event_host.STATE["scene"] == "welcome"
    source, text, relevant = said[0]
    assert source == "event_scene" and relevant
    assert "ข้อความระบบ ไม่ใช่คำพูดแขก" in text
    assert "กล่าวต้อนรับแขกเข้างาน" in text                 # on_arrive: she is already there
    assert "ห้ามบอกว่ากำลังเดินไป" in text


def test_a_scene_with_no_spot_never_walks(event, said, monkeypatch):
    _walker(monkeypatch, ARRIVES)

    async def go():
        out = await event_host.start_scene("introduce")
        await asyncio.sleep(0)
        return out

    out = asyncio.run(go())
    assert out["walk"] is None and not event_walk.walking()
    assert "ห้ามบอกว่ากำลังเดินไป" not in said[0][1]


def test_an_unknown_scene_is_refused(event):
    assert asyncio.run(event_host.start_scene("party"))["error"] == "no_scene"


# ---- the debut: a line and a wave together (owner 9 Nov "เดินเปิดตัว หุ่นโบกมือทักทายพร้อมพูดประโยค") ----

@pytest.fixture
def stage(monkeypatch):
    """What reached the model and the arm, in order: ("say", source, text) / ("arm", action).
    `stage["spoke"]` = what announce answers (True: the text reached the model)."""
    log: list = []
    box = {"log": log, "spoke": True, "before_answer": None, "held": [], "voice": True, "arms": []}

    async def fake_say(text, source, still, held_language=True):
        log.append(("say", source, text))
        box["held"].append(held_language)
        if box["before_answer"] is not None:
            await box["before_answer"]()
        return box["spoke"] and still()

    async def staff_request(action):
        log.append(("arm", action))
        if box["arms"]:
            return box["arms"].pop(0)
        return box.get("arm") or {"ok": True, "action": action, "hardware": "requested",
                                  "error": None, "error_th": None}

    async def voice_begins(limit):
        return box["voice"]

    from app import robot_body

    monkeypatch.setattr(event_host, "_say", fake_say)
    monkeypatch.setattr(event_host, "_her_voice_begins", voice_begins)
    monkeypatch.setattr(event_host, "_robot_settling", lambda: False)
    monkeypatch.setattr(robot_body, "staff_request", staff_request)
    return box


async def _quiet_tasks():
    await asyncio.sleep(0)
    await _settle(lambda: not event_host._tasks)


def test_the_debut_waves_with_its_line_where_she_stands(event, stage):
    """Walking off (and the stage not recorded): the line where she stands, the arm after it."""
    async def go():
        out = await event_host.start_scene("debut")
        await _quiet_tasks()
        return out

    out = asyncio.run(go())
    assert out["ok"] and out["walk"]["ok"] is False
    assert [e[0] for e in stage["log"]] == ["say", "arm"]                 # the text first, then the arm
    _, source, text = stage["log"][0]
    assert source == "event_scene" and "«Hello everyone! I'm Emmaaa!" in text
    assert "เป็นภาษาอังกฤษเสมอ" in text                                  # owner 10 Nov: "อังกฤษอย่างเดียว"
    assert "ห้ามบอกว่ากำลังเดินไป" in text                                # she has not gone anywhere
    assert stage["log"][1] == ("arm", "greet_arm")
    p = event_host.perform_view()
    assert p["scene"] == "debut" and p["phase"] == "done" and p["said"] is True and p["gesture_ok"] is True
    assert p["gesture_th"] == "โบกมือทักทาย"
    # the quoted sentence fixes its language: no 🌐 "answer only in X" after it (review 9 Nov)
    assert stage["held"] == [False]


def test_a_scene_with_no_quoted_sentence_keeps_the_held_language(event, stage):
    async def go():
        await event_host.start_scene("introduce")
        await _quiet_tasks()

    asyncio.run(go())
    assert stage["held"] == [True]


def test_no_voice_no_arm(event, stage):
    """Her new reply never began (dropped, cut, a gate): the wave is not sent alone."""
    stage["voice"] = False

    async def go():
        await event_host.start_scene("debut")
        await _quiet_tasks()

    asyncio.run(go())
    assert [e[0] for e in stage["log"]] == ["say"]
    p = event_host.perform_view()
    assert p["said"] is True and p["gesture_ok"] is False and p["gesture_error_th"] == event_host.NO_VOICE_TH


def test_a_busy_arm_is_asked_again(event, stage, monkeypatch):
    """Her speech gestures' beat, or the rest before a refused walk: `busy`/`cooldown` for moments."""
    monkeypatch.setattr(event_host, "ARM_RETRY_EVERY_S", 0.01)
    busy = {"ok": False, "action": "greet_arm", "hardware": "refused", "error": "busy", "error_th": "แขนกำลังทำท่าอื่นอยู่"}
    stage["arms"] = [busy, dict(busy, error="cooldown"), dict(busy, error="request_pending")]

    async def go():
        await event_host.start_scene("debut")
        await _quiet_tasks()

    asyncio.run(go())
    assert [e for e in stage["log"] if e[0] == "arm"] == [("arm", "greet_arm")] * 4
    assert event_host.perform_view()["gesture_ok"] is True
    # any other refusal is the answer at once
    event_host.reset()
    stage["log"].clear()
    stage["arms"] = [dict(busy, error="latched", error_th="หุ่นล็อกแขนอยู่")]

    async def again():
        await event_host.start_scene("debut")
        await _quiet_tasks()

    asyncio.run(again())
    assert [e for e in stage["log"] if e[0] == "arm"] == [("arm", "greet_arm")]
    assert event_host.perform_view()["gesture_error_th"] == "หุ่นล็อกแขนอยู่"


def test_the_line_waits_for_the_robot_to_stand_still(event, stage, monkeypatch):
    """Pushed onto the stage a moment ago: WALK_QUIET would drop her whole reply (review 9 Nov)."""
    moving = {"n": 3}
    phases: list = []

    def settling():
        phases.append((event_host.perform_view() or {}).get("phase"))
        moving["n"] -= 1
        return moving["n"] >= 0

    monkeypatch.setattr(event_host, "_robot_settling", settling)

    async def go():
        await event_host.start_scene("debut")
        await _quiet_tasks()

    asyncio.run(go())
    assert moving["n"] < 0 and "settling" in phases               # it waited, and said so on the phone
    assert [e[0] for e in stage["log"]] == ["say", "arm"]
    assert event_host.perform_view()["phase"] == "done"
    # never for ever: STILL_WAIT_S and then the line goes
    monkeypatch.setattr(event_host, "_robot_settling", lambda: True)
    monkeypatch.setattr(event_host, "STILL_WAIT_S", 0.3)
    event_host.reset()
    stage["log"].clear()
    asyncio.run(go())
    assert [e[0] for e in stage["log"]] == ["say", "arm"]


def test_a_red_stop_while_the_arm_rests_before_the_walk_says_nothing(event, stage, monkeypatch):
    """Review 9 Nov, probed: the stop during the rest answered the walk "stopped", and the scene
    was acted where she stood -- the greeting and its wave -- with the phone saying so."""
    async def walk_stopped(sc, spot_id, seq, stops_seen=None):
        await event_host.stop()                          # the red button, during the rest
        return {"ok": False, "error": "stopped", "error_th": "หยุดโดยทีมงาน"}

    monkeypatch.setattr(event_host, "_walk_to", walk_stopped)

    async def go():
        await event_host.start_scene("debut")
        await _quiet_tasks()

    asyncio.run(go())
    assert stage["log"] == []
    p = event_host.perform_view()
    assert p["phase"] == "stopped" and p["said"] is False


def test_a_line_that_never_went_out_lifts_no_arm(event, stage):
    stage["spoke"] = False

    async def go():
        await event_host.start_scene("debut")
        await _quiet_tasks()

    asyncio.run(go())
    assert [e[0] for e in stage["log"]] == ["say"]
    p = event_host.perform_view()
    assert p["phase"] == "done" and p["said"] is False and p["gesture_ok"] is None


def test_the_arms_refusal_is_on_the_phone(event, stage):
    stage["arm"] = {"ok": False, "action": "greet_arm", "hardware": "refused", "error": "latched",
                    "error_th": "หุ่นล็อกแขนอยู่"}

    async def go():
        await event_host.start_scene("debut")
        await _quiet_tasks()

    asyncio.run(go())
    p = event_host.perform_view()
    assert p["said"] is True and p["gesture_ok"] is False and p["gesture_error_th"] == "หุ่นล็อกแขนอยู่"


def test_a_red_stop_while_she_waits_to_speak_sends_no_wave(event, stage):
    async def red_stop():
        await event_host.stop()                          # the page's red button, while announce waits
    stage["before_answer"] = red_stop

    async def go():
        await event_host.start_scene("debut")
        await _quiet_tasks()

    asyncio.run(go())
    assert [e[0] for e in stage["log"]] == ["say"]
    p = event_host.perform_view()
    # the stop also makes the line no longer relevant: announce drops it (the fake answers still())
    assert p["phase"] == "stopped" and p["said"] is False and p["gesture_ok"] is None


def test_a_red_stop_after_the_line_went_out_keeps_the_arm_down(event, stage, monkeypatch):
    async def voice_then_stop(limit):
        await event_host.stop()                          # pressed while her reply was about to play
        return True

    monkeypatch.setattr(event_host, "_her_voice_begins", voice_then_stop)

    async def go():
        await event_host.start_scene("debut")
        await _quiet_tasks()

    asyncio.run(go())
    assert [e[0] for e in stage["log"]] == ["say"]
    p = event_host.perform_view()
    assert p["said"] is True and p["gesture_ok"] is False and p["gesture_error_th"] == event_host.STOPPED_GESTURE_TH


def test_on_a_walk_the_wave_waits_for_the_arrival(event, stage, monkeypatch):
    event_host.spots_path().write_text(json.dumps({"spots": {
        "stage_centre": {"pose": [-9.0, -3.0, 0.0]}}}), encoding="utf-8")
    # a debut with words on the way too: those are said, and still no arm before the arrival
    line_at_the_spot(event)
    doc = json.loads(event.read_text(encoding="utf-8"))
    doc["scenes"][0]["on_start_th"] = "บอกแขกว่ากำลังออกมา"
    event.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    _walker(monkeypatch, "import time; time.sleep(0.6); print('[ 0.1] step 1 done', flush=True); "
                         "print('[ 0.2] path event done', flush=True)")
    seen: list = []

    async def go():
        out = await event_host.start_scene("debut")
        assert out["walk"]["ok"], out
        await asyncio.sleep(0.1)
        seen.append((list(stage["log"]), event_host.perform_view()["phase"]))  # on the way
        assert await _settle(lambda: ("arm", "greet_arm") in stage["log"])
        await _quiet_tasks()

    asyncio.run(go())
    on_the_way, phase = seen[0]
    assert [e[:2] for e in on_the_way] == [("say", "event_scene")] and phase == "walking"
    assert [e[0] for e in stage["log"]] == ["say", "say", "arm"]
    assert stage["log"][1][1] == "event_arrived" and "หุ่นเดินถึงกลางเวที" in stage["log"][1][2]
    assert event_host.perform_view()["phase"] == "done"


def test_card_2_walks_to_the_counter_quietly_then_greets_and_gives_the_hand(event, stage, monkeypatch):
    """Owner 21 Nov ("ทำการ์ดกดเป็นซีนวันจริง จะได้กดแล้วให้หุ่นทำตามที่เราสั่งไว้" + "พูดทักทาย เดี๋ยวเอาบทให้"),
    the tracked file's card 2: no words on the way (Khun Miki may be speaking), at the counter her greeting,
    then the handshake after her first words -- the arm never before the line."""
    spots = event_host.load()["spots"]
    assert event_host.spot_steps(spots["beside_host"])[-1]["to"] == [-8.62, -4.2]
    _walker(monkeypatch, "import time; time.sleep(0.6); print('[ 0.1] step 1 done', flush=True); "
                         "print('[ 0.2] path event done', flush=True)")
    seen: list = []

    async def go():
        out = await event_host.start_scene("counter_handshake")
        assert out["walk"]["ok"], out
        await asyncio.sleep(0.1)
        seen.append((list(stage["log"]), event_host.perform_view()["phase"]))  # on the way
        assert await _settle(lambda: ("arm", "handshake") in stage["log"])
        await _quiet_tasks()

    asyncio.run(go())
    on_the_way, phase = seen[0]
    assert on_the_way == [] and phase == "walking"
    assert [e[0] for e in stage["log"]] == ["say", "arm"]
    assert stage["log"][0][1] == "event_arrived" and "หุ่นเดินถึงข้างคุณมิกิแล้ว" in stage["log"][0][2]
    assert stage["log"][1] == ("arm", "handshake") and event_host.perform_view()["phase"] == "done"


def test_card_2_comes_in_head_on_and_a_plain_walk_to_the_spot_does_not(event, stage, monkeypatch):
    """Owner 23 Nov, after card 1 ran on the robot: "ตอนเดินปรับเป็นเดินให้ไกลกว่าโต๊ะเคาน์เตอร์แล้วค่อยเดินเข้ามาจับมือกับคุณมิกิ".
    Card 2 gives Khun Miki the hand there, so it walks in as card 1's counter leg does: to the head-on point 1 m back
    from the counter (beside_host's `head_on_m`), then straight in -- with the board's pose and without it. The staff
    page's plain walk to the spot (its spot list, "สั่งเอง") is a walk, not the handshake: as it was."""
    from app import event_tour

    routes = []

    async def spawn(route):
        routes.append(json.loads(Path(route).read_text(encoding="utf-8"))["paths"]["event"]["steps"])
        return await asyncio.create_subprocess_exec(sys.executable, "-c", ARRIVES, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT)

    _walker(monkeypatch, ARRIVES)
    monkeypatch.setattr(event_walk, "_spawn", spawn)
    where = {"pose": (-7.3, -6.2, 168.8)}                 # on the fixture's dock

    async def board_pose():
        return where["pose"]

    monkeypatch.setattr(event_tour, "_where", board_pose)

    def walked(press):
        async def go():
            out = await press()
            assert (out.get("walk") or {}).get("ok"), out
            assert await _settle(lambda: not event_walk.walking())
            await _quiet_tasks()
        asyncio.run(go())
        return [tuple(s["to"]) for s in routes[-1] if s.get("to")]

    assert walked(lambda: event_host.start_scene("counter_handshake")) == [(-9.62, -4.2), (-8.62, -4.2)]
    assert walked(lambda: event_host.go_spot("beside_host")) == [(-8.62, -4.2)]
    assert walked(lambda: event_host.go_spot("beside_host", head_on=True)) == [(-9.62, -4.2), (-8.62, -4.2)]
    where["pose"] = None                                  # no pose read: the point is still on the way
    assert walked(lambda: event_host.start_scene("counter_handshake")) == [(-9.62, -4.2), (-8.62, -4.2)]
    # card 3 gives the hand too, but its spot asks for no head-on point: as it was
    where["pose"] = (-8.62, -4.2, 0.0)
    assert walked(lambda: event_host.start_scene("guest_handshake")) == [(-8.62, -4.2)]


def test_a_walk_that_never_got_there_says_so_on_the_phone(event, stage, monkeypatch):
    line_at_the_spot(event)
    event_host.spots_path().write_text(json.dumps({"spots": {
        "stage_centre": {"pose": [-9.0, -3.0, 0.0]}}}), encoding="utf-8")
    _walker(monkeypatch, WALKS_ON)

    async def go():
        await event_host.start_scene("debut")
        assert event_walk.walking()
        await event_host.stop()
        assert await _settle(lambda: not event_walk.walking())
        await _quiet_tasks()

    asyncio.run(go())
    assert ("arm", "greet_arm") not in stage["log"]
    p = event_host.perform_view()
    assert p["phase"] == "cut" and p["said"] is False
    # "เดินต่อ": the line waits for the arrival again (the red "never got there" is not left up)
    _walker(monkeypatch, ARRIVES)

    async def resume():
        out = await event_host.resume()
        assert out["ok"], out
        phase = event_host.perform_view()["phase"]
        assert await _settle(lambda: ("arm", "greet_arm") in stage["log"])
        await _quiet_tasks()
        return phase

    assert asyncio.run(resume()) == "walking"
    assert event_host.perform_view()["phase"] == "done"


class _Call:
    """The session's playback bookkeeping as session.py keeps it: `_note_playback` queues a chunk
    behind what is queued; `turn_complete` and `interrupted` reset `_sent_audio_ms` (lines 2008,
    2059 -- second review: the first version read that counter, and the reply's own end hid it);
    an interruption pulls `_play_until` back to now."""

    def __init__(self, ahead_s: float):
        now = time.monotonic()
        self._play_until = now + ahead_s if ahead_s > 0 else now - 1.0
        self._sent_audio_ms = 0.0

    def chunk(self, seconds: float):
        self._play_until = max(self._play_until, time.monotonic()) + seconds
        self._sent_audio_ms += seconds * 1000

    def turn_complete(self):
        self._sent_audio_ms = 0.0

    def interrupted(self):
        self._play_until = min(self._play_until, time.monotonic())
        self._sent_audio_ms = 0.0


def _voice(monkeypatch, call, script, limit=5.0):
    """Run _her_voice_begins against `call` while `script(call)` plays the session's side."""
    from app import session as session_module

    monkeypatch.setattr(session_module, "_active", call)
    monkeypatch.setattr(event_host, "VOICE_LEAD_S", 0.0)

    async def go():
        task = asyncio.create_task(event_host._her_voice_begins(limit))
        await script(call, task)
        return await task, time.monotonic()

    return asyncio.run(go())


def test_her_voice_begins_after_what_is_ahead_of_it(monkeypatch):
    """The robot face sends no audio lead: the session's own clock says when her NEW reply plays."""
    async def behind_her_last_answer(call, task):
        await asyncio.sleep(0.25)
        assert not task.done()                           # nothing of the new reply yet
        call.chunk(1.5)                                  # the debut reply streams in, behind the old answer
        call.chunk(1.5)
        call.turn_complete()                             # ...and ends: the per-turn counter is 0 again
        await asyncio.sleep(0.3)
        assert not task.done()                           # the old answer is still playing

    call = _Call(ahead_s=1.5)                            # her last answer: 1.5 s still to go
    old_end = call._play_until
    ok, at = _voice(monkeypatch, call, behind_her_last_answer)
    assert ok is True and at >= old_end - 0.05           # with the new reply's first word, not before


def test_her_voice_begins_at_once_in_a_quiet_room(monkeypatch):
    async def quiet(call, task):
        await asyncio.sleep(0.2)
        call.chunk(2.0)
        call.turn_complete()

    ok, _ = _voice(monkeypatch, _Call(ahead_s=0), quiet)
    assert ok is True


def test_no_new_reply_or_an_interruption_lifts_no_arm(monkeypatch):
    async def nothing(call, task):
        await asyncio.sleep(0)

    assert _voice(monkeypatch, _Call(ahead_s=0), nothing, limit=0.3)[0] is False

    async def cut_in(call, task):
        await asyncio.sleep(0.2)
        call.interrupted()                               # a guest cut her last answer short
        await asyncio.sleep(0.2)
        call.chunk(3.0)                                  # her answer to the guest, not this line

    # the guest's answer reaches past where the old answer would have ended, inside the limit
    assert _voice(monkeypatch, _Call(ahead_s=1.0), cut_in, limit=2.0)[0] is False

    from app import session as session_module

    monkeypatch.setattr(session_module, "_active", None)
    assert asyncio.run(event_host._her_voice_begins(0.3)) is False


def test_a_scene_left_behind_reports_nothing_over_the_next(event, stage):
    gate = asyncio.Event

    async def go():
        hold = gate()

        async def wait():
            await hold.wait()
        stage["before_answer"] = wait
        await event_host.start_scene("debut")
        await asyncio.sleep(0)
        stage["before_answer"] = None
        await event_host.start_scene("introduce")        # staff moved on while the debut waited
        hold.set()
        await _quiet_tasks()

    asyncio.run(go())
    assert ("arm", "greet_arm") not in stage["log"]      # the debut's line was no longer relevant
    assert event_host.perform_view()["scene"] == "introduce"


def test_only_a_gesture_the_robot_has_and_never_a_stop():
    doc = {"scenes": [{"id": "a", "gesture": "stop"}, {"id": "b", "gesture": "fly"},
                      {"id": "c", "gesture": "greet_arm", "quick": "yes"}, {"id": "d"}]}
    scenes = {s["id"]: s for s in event_host._normalise(doc, {})["scenes"]}
    assert scenes["a"]["gesture"] is None and scenes["b"]["gesture"] is None
    assert scenes["c"]["gesture"] == "greet_arm" and scenes["c"]["quick"] is False   # only a real true
    assert scenes["d"]["gesture"] is None and scenes["d"]["quick"] is False


def test_the_phone_gets_the_tile_and_the_sentence(event):
    snap = event_host.snapshot()
    debut = next(s for s in snap["scenes"] if s["id"] == "debut")
    # ท่านางงาม (owner 9 Nov): the tile names the pageant; greet_arm stays the fallback gesture
    assert debut["quick"] is True and debut["gesture"] == "greet_arm" and debut["pageant"] is True
    assert debut["gesture_th"] == "ท่านางงาม (โบกมือ หันซ้าย-ขวา)"
    assert debut["says_th"] == "Hello everyone! I'm Emmaaa! Welcome, and thank you so much for being here with us today!"
    welcome = next(s for s in snap["scenes"] if s["id"] == "welcome")
    assert welcome["says_th"] is None                    # an instruction to her is not words anyone hears
    assert snap["perform"] is None


# ---- walking -----------------------------------------------------------------------

def test_a_walk_arrives_and_she_greets_then(event, said, monkeypatch):
    _walker(monkeypatch, ARRIVES)

    async def go():
        out = await event_host.start_scene("welcome")
        assert out["walk"]["ok"], out
        assert await _settle(lambda: len(said) >= 2)
        return out

    asyncio.run(go())
    start, arrive = said[0], said[1]
    assert "ห้ามบอกว่าถึงแล้ว" in start[1]                    # on_start while she walks
    assert arrive[0] == "event_arrived" and "หุ่นเดินถึงกลางส่วนต้อนรับแล้ว" in arrive[1]
    run = event_walk.current()
    assert run.state == "arrived" and run.tail[-1].endswith("path event done")
    saved = json.loads(event_host.spots_path().read_text(encoding="utf-8"))
    assert saved["spots"]["reception"]["arrivals"] == 1      # a clean arrival is counted


def test_staff_stop_stops_the_walker(event, said, monkeypatch):
    _walker(monkeypatch, WALKS_ON)

    async def go():
        await event_host.start_scene("welcome")
        assert event_walk.walking()
        out = await event_host.stop()
        assert out["stopped"] is True
        assert await _settle(lambda: not event_walk.walking())

    asyncio.run(go())
    run = event_walk.current()
    assert run.state == "stopped" and run.reason == "staff"
    assert [s for s in said if s[0] == "event_arrived"] == []       # no arrival line after a stop


def test_every_other_stop_path_stops_the_event_walk():
    """voice (stop_moving, move_body(stop), the face's nav stop) and the desk
    STOP: each calls event_walk.stop_from before anything else."""
    robot = (ROOT / "app" / "tools" / "robot.py").read_text(encoding="utf-8")
    stop_moving = robot.split("async def stop_moving()", 1)[1].split("face = robot_nav.routes_session()", 1)[0]
    assert 'await event_walk.stop_from("voice")' in stop_moving
    body = (ROOT / "app" / "tools" / "body.py").read_text(encoding="utf-8")
    assert 'if action == "stop":' in body and 'await event_walk.stop_from("voice")' in body
    nav = (ROOT / "app" / "robot_nav.py").read_text(encoding="utf-8")
    assert 'await event_walk.stop_from("voice")' in nav.split("async def stop(session)", 1)[1].split("nav_ack", 1)[0]
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    desk = main.split('if action == "stop":', 1)[1].split("plan, face = robot_nav.stop_plan()", 1)[0]
    assert 'await event_walk.stop_from("desk")' in desk


def test_a_walk_stops_when_the_staff_page_goes_quiet(event, said, monkeypatch):
    _walker(monkeypatch, WALKS_ON)
    monkeypatch.setattr(settings, "event_walk_heartbeat_s", 0.3)

    async def go():
        await event_host.start_scene("welcome")
        assert await _settle(lambda: not event_walk.walking(), 8)

    asyncio.run(go())
    assert event_walk.current().reason == "control_lost"


def test_a_walk_stops_when_the_robot_face_goes_away(event, said, monkeypatch):
    _walker(monkeypatch, WALKS_ON)
    faces = {"up": True}
    monkeypatch.setattr(event_walk, "_faces", lambda: faces["up"])
    monkeypatch.setattr(event_walk, "FACE_GRACE_S", 0.2)

    async def go():
        await event_host.start_scene("welcome")
        assert event_walk.walking()
        faces["up"] = False
        for _ in range(40):                               # the page keeps polling
            event_walk.heartbeat()
            await asyncio.sleep(0.05)
            if not event_walk.walking():
                break

    asyncio.run(go())
    assert event_walk.current().reason == "face_gone"


@pytest.mark.parametrize("why, setup", [
    ("disabled", lambda mp: mp.setattr(settings, "event_walk_enabled", False)),
    ("no_face", lambda mp: mp.setattr(event_walk, "_faces", lambda: False)),
    ("other_walker", lambda mp: mp.setattr(event_walk, "_other_walker", lambda: True)),
    # the app's own walks wait out its 60 s stop latch; a server walk did not (review 6 Nov)
    ("stop_latched", lambda mp: mp.setattr(greeter, "_app_latched", lambda link: True)),
])
def test_a_walk_is_refused_rather_than_queued(event, said, monkeypatch, why, setup):
    _walker(monkeypatch, ARRIVES)
    setup(monkeypatch)
    out = asyncio.run(event_host.go_spot("reception"))
    assert out["ok"] is False and out["error"] == why and out["error_th"]
    assert not event_walk.walking()


def test_a_spot_not_recorded_cannot_be_walked_to(event, monkeypatch):
    _walker(monkeypatch, ARRIVES)
    out = asyncio.run(event_host.go_spot("table_3"))
    assert out["error"] == "no_steps"


def test_a_new_walk_replaces_the_one_under_way(event, said, monkeypatch):
    _walker(monkeypatch, WALKS_ON)

    async def go():
        await event_host.go_spot("reception")
        first = event_walk.current()
        await event_host.go_spot("entrance")
        assert first.state == "stopped" or await _settle(lambda: first.state == "stopped")
        assert first.reason == "replaced"
        assert event_walk.current().spot == "entrance" and event_walk.walking()
        await event_walk.stop("staff")
        await _settle(lambda: not event_walk.walking())

    asyncio.run(go())


def test_the_tables_go_one_after_another(event, said, monkeypatch):
    event_host.spots_path().write_text(json.dumps({"spots": {
        "table_1": {"pose": [-8.5, -2.0, 45]}, "table_2": {"pose": [-9.5, -1.0, 90]}}}), encoding="utf-8")
    _walker(monkeypatch, ARRIVES)

    async def go():
        await event_host.start_scene("tables")
        await _settle(lambda: not event_walk.walking())
        assert event_walk.current().spot == "table_1"
        out = await event_host.next_stop()
        assert out["ok"] and out["stop"] == 1
        await _settle(lambda: len([s for s in said if s[0] == "event_arrived"]) >= 2)

    asyncio.run(go())
    assert event_walk.current().spot == "table_2"
    assert "ตอนนี้อยู่ที่ โต๊ะ 2" in event_host.scene_note()


def test_focus_and_the_wheel_fence_see_the_event_walk(monkeypatch):
    from app import focus, robot_chassis

    monkeypatch.setattr(event_walk, "walking", lambda: True)
    assert focus._robot_moving() is True
    with pytest.raises(robot_chassis.Fenced):
        robot_chassis.run_fence()


# ---- recording a spot ---------------------------------------------------------------

def _board(monkeypatch, quality=72, yaw=0.785398):
    from app import robot_chassis

    async def pose():
        return {"x": -8.512, "y": -2.004, "yaw": yaw}

    async def q():
        return quality

    monkeypatch.setattr(robot_chassis, "configured", lambda: True)
    monkeypatch.setattr(robot_chassis, "pose", pose)
    monkeypatch.setattr(robot_chassis, "localization_quality", q)


def test_staff_record_a_spot_where_the_robot_stands(event, monkeypatch):
    _board(monkeypatch)
    out = asyncio.run(event_host.save_spot("table_1"))
    assert out == {"ok": True, "spot": "table_1", "pose": [-8.512, -2.004, 45.0]}
    spot = event_host.load()["spots"]["table_1"]
    assert spot["pose"] == [-8.512, -2.004, 45.0] and spot["recorded_at"]
    assert event_host.spot_steps(spot)[1]["face"] == 45.0
    assert event_host.spots_path().parent == event.parent           # beside the event file, not the tracked one


def test_a_spot_is_not_recorded_from_a_lost_fix_nor_over_the_dock(event, monkeypatch):
    _board(monkeypatch, quality=30)
    assert asyncio.run(event_host.save_spot("table_1"))["error"] == "lost"
    assert asyncio.run(event_host.save_spot("dock"))["error"] == "dock"
    assert asyncio.run(event_host.save_spot("nowhere"))["error"] == "no_spot"


def test_card_3_starts_where_the_counter_was_recorded_and_is_not_recorded_itself(event, monkeypatch):
    """Review 22 Nov: guest_front's first point was a copy of beside_host's -- staff record beside_host at the
    rehearsal (its note says so), card 2 went to the recorded place and card 3 back to the file's. Now it names
    the spot it walks on from (`from_spot`): the recorded counter moves card 3's start; a pose recorded for
    guest_front itself is never used (it would drive at the place a guest stands, the turn and the meet gone)
    and the phone cannot record one; a from_spot that is no place to start from = not walked."""
    spots = event_host.load()["spots"]
    assert event_host.spot_steps(spots["guest_front"])[1]["to"] == [-8.62, -4.2]
    event_host.spots_path().write_text(json.dumps({"spots": {
        "beside_host": {"pose": [-8.9, -4.5, 5.0], "recorded_at": "2026-11-24 18:00"},
        "guest_front": {"pose": [-10.0, -4.4, 180.0], "recorded_at": "2026-11-24 18:01"}}}), encoding="utf-8")
    event_host._cache.update(key=None)
    steps = event_host.spot_steps(event_host.load()["spots"]["guest_front"])
    assert steps[1]["to"] == [-8.9, -4.5] and steps[2]["do"] == "turn" and steps[3]["meet_m"] == 0.7 and len(steps) == 4
    # ... and the turn round follows the counter's recorded heading (second review 22 Nov: an absolute 180 after a
    # counter recorded at 5 deg turned her 175 deg; at 15-20 deg she walked past the guest): 5 + 180 -> -175
    assert steps[2]["face"] == -175.0 and steps[2]["w_spin"] == 0.3
    assert event_host.follow_spot([{"do": "turn", "face": 180}], [0, 0, None], 0.0)[0]["face"] == 180   # no heading
    assert event_host.file_heading({"steps": [{"to": [0, 0], "face": 0}]}) == 0.0
    _board(monkeypatch)
    out = asyncio.run(event_host.save_spot("guest_front"))
    assert out["ok"] is False and out["error"] == "follows" and "ข้างคุณมิกิ" in out["error_th"]
    row = next(p for p in event_host.shared_snapshot()["spots"] if p["id"] == "guest_front")
    assert row["follows_th"] == "ข้างคุณมิกิ" and row["walkable"]
    # the card says to the people pressing it what the robot cannot see (behind her), and the model never gets it
    card = next(s for s in event_host.snapshot()["scenes"] if s["id"] == "guest_handshake")
    assert "ข้างหลังหุ่น" in card["staff_th"] and "ข้างหลังหุ่น" not in (event_host.scene_note() or "")
    doc = json.loads(event.read_text(encoding="utf-8"))
    for bad in ("dock", "nowhere", "guest_front", ["beside_host"]):
        doc["spots"]["guest_front"]["from_spot"] = bad
        event.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        event_host._cache.update(key=None)
        assert event_host.spot_steps(event_host.load()["spots"]["guest_front"]) is None, bad
        # a from_spot that is no id does not take the phone's state down (second review: TypeError on every poll)
        assert event_host.shared_snapshot()["spots"] and asyncio.run(event_host.save_spot("guest_front"))["error"] == "follows"


def test_no_chassis_no_spot(event):
    assert asyncio.run(event_host.save_spot("table_1"))["error"] == "no_pose"


# ---- the staff page's listener ------------------------------------------------------

def test_the_staff_page_needs_its_own_key(event, monkeypatch):
    from fastapi.testclient import TestClient

    from app import event_control_server as ecs

    monkeypatch.setattr(settings, "event_control_key", "k3y-for-staff")
    client = TestClient(ecs.app)
    assert client.get("/event/state").status_code == 403
    assert client.post("/event/stop", json={}).status_code == 403
    assert client.get("/event-control?key=wrong", follow_redirects=False).status_code == 403
    r = client.get("/event-control?key=k3y-for-staff", follow_redirects=False)
    # the key leaves the URL by a same-origin hop page (24 Nov: a 303 inside a cross-site navigation from a QR code
    # never carried the new Strict cookie -> the phone got 403)
    assert r.status_code == 200 and "location.replace('/event-control')" in r.text and "k3y-for-staff" not in r.text
    assert "k3y-for-staff" not in r.headers.get("set-cookie", "")
    assert "samesite=strict" in r.headers["set-cookie"].lower() and "httponly" in r.headers["set-cookie"].lower()
    state = client.get("/event/state").json()
    assert state["ok"] and "welcome" in [s["id"] for s in state["scenes"]]
    assert event_walk.STATE["heartbeat"] is not None                  # every poll is a heartbeat
    assert client.get("/event-control").status_code == 200
    # the red stop also stops the arm and her line (review 7 Nov): no robot face here, so the
    # arm's part says it did not reach one -- never a quiet "ok"
    stopped = client.post("/event/stop", json={}).json()
    assert (stopped["ok"], stopped["stopped"], stopped["line_cut"]) == (True, False, False)
    assert stopped["body"]["sent"] is False and "E-stop" in stopped["body"]["error_th"]


def test_no_key_no_listener(monkeypatch, caplog):
    from app import event_control_server as ecs

    monkeypatch.setattr(settings, "event_control_port", 8003)
    monkeypatch.setattr(settings, "event_control_key", "")
    asyncio.run(ecs.serve())                                          # returns at once
    assert ecs._server["obj"] is None
    assert "EVENT_CONTROL_KEY is not" in caplog.text


def test_the_sales_room_gains_nothing_by_default():
    import os

    src = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    assert 'int(os.getenv("EVENT_CONTROL_PORT", "0") or 0)' in src
    assert '_get_bool("EVENT_WALK_ENABLED", False)' in src
    assert os.environ.get("EVENT_WALK_ENABLED") in (None, "", "false", "False", "0")  # the suite's own env


# ---- the pages ---------------------------------------------------------------------

def test_the_face_page_dials_the_event_host_for_the_event_app():
    html = (ROOT / "client" / "voice-preview.html").read_text(encoding="utf-8")
    assert "var eventApp = appShell && !mirror && q.get('event') === '1';" in html
    assert "if (eventApp) q.set('profile', 'event');" in html
    # after the app shell's strip of profile/lang, or it would be stripped too
    assert html.index("q.delete('profile');") < html.index("if (eventApp) q.set('profile', 'event');")
    cycle = html.split("function cycleMode() {", 1)[1].split("\n    }\n", 1)[0]
    assert cycle.lstrip().startswith("if (eventApp) return;")


def test_the_staff_page_stands_alone_and_stops_first():
    html = (ROOT / "client" / "event-control.html").read_text(encoding="utf-8")
    assert "<title>Emma Event</title>" in html
    assert 'id="stopBtn"' in html and "position: fixed" in html.split(".stopbar {", 1)[1].split("}", 1)[0]
    assert "setInterval(poll, 1000);" in html                       # the heartbeat
    assert "fetch('/event/state'" in html
    # the office Wi-Fi is all it may have: nothing from outside
    assert "https://" not in html and "http://" not in html
    # STOP is not held back by another press in flight
    stop = html.split("$('stopBtn').onclick", 1)[1].split("};", 1)[0]
    assert "busy" not in stop


def test_deploy_ships_the_walker_and_floor_one(monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    import robot_server as rs

    assert "scripts/board_walk.py" in rs.CODE_FILES
    assert "data/robot/waypoints/*.json" in rs.EXTRA_GLOBS
    assert rs._deployable("data/robot/waypoints/floor1.json")
    assert not rs._deployable("data/robot/legacy.json")             # the rest stays home
    assert rs._deployable("data/events/floor1_event.json")


def test_the_live_event_suite_names_only_real_scenes():
    """scripts/eval_live_qa.py --suite event: profile event by default, and
    every server:scene/arrive line names a scene of the tracked event file."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import eval_live_qa as ev

    assert ev.resolve_profile("event", None) == "event"
    assert ev.resolve_profile("scenarios", None) == "condo"          # the sales room's default unchanged
    cases = ev.load_cases("event", ev.FAQ)
    assert len(cases) >= 7 and all(c["suite"] == "event" for c in cases)
    assert not [c for c in ev.load_cases("all", ev.FAQ) if c.get("suite") == "event"]
    scenes = {s["id"] for s in json.loads(TRACKED.read_text(encoding="utf-8"))["scenes"]}
    for case in cases:
        for turn in case["turns"]:
            if turn.startswith(("server:scene:", "server:arrive:", "server:walk:", "server:walk_line:")):
                assert turn.rsplit(":", 1)[1] in scenes, (case["id"], turn)


def test_scene_texts_say_where_she_stands():
    doc = event_host._normalise(json.loads(TRACKED.read_text(encoding="utf-8")), {})
    welcome = next(s for s in doc["scenes"] if s["id"] == "welcome")
    standing = event_host.scene_text(welcome, walking=False, spot_id="reception")
    walking = event_host.scene_text(welcome, walking=True, spot_id="reception")
    assert standing.startswith("[ทีมงานเปลี่ยนฉากเป็น 'รับแขกหน้างาน' — ข้อความระบบ ไม่ใช่คำพูดแขก]")
    assert event_host.STANDING in standing and "ห้ามบอกว่าถึงแล้ว" not in standing
    assert "ห้ามบอกว่าถึงแล้ว" in walking and event_host.STANDING not in walking
    rest = next(s for s in doc["scenes"] if s["id"] == "rest")
    assert event_host.arrival_text(rest, "แท่นชาร์จ") is None          # nothing to say on the dock


def test_where_she_stands_a_scene_does_not_claim_its_arrival():
    """10 Nov, every scene pressed through the server with walking off (the robot today): the
    arrival line was the line where she stood -- "พาแขกไปห้องมัลติมีเดีย" had her say she had reached
    the room from across the lobby, "กลับแท่นชาร์จ" that she was off to charge, and a table scene
    greeted "the table" she was not at. `on_stand_th` is the scene's line for there."""
    doc = event_host._normalise(json.loads(TRACKED.read_text(encoding="utf-8")), {})
    sc = {s["id"]: s for s in doc["scenes"]}
    escort = event_host.scene_text(sc["escort_multimedia"], walking=False, spot_id="multimedia_door")
    assert "ถึงห้องมัลติมีเดียแล้ว" not in escort and event_host.STANDING in escort
    assert event_host.scene_text(sc["rest"], walking=False, spot_id="dock") is None    # she is not going
    tables = event_host.scene_text(sc["tables"], walking=False, spot_id="table_1")
    assert "โต๊ะนี้" not in tables and event_host.STANDING in tables
    # getting there still says it, and walking there still says nothing of having arrived -- once
    assert "ถึงห้องมัลติมีเดียแล้ว" in event_host.arrival_text(sc["escort_multimedia"], "หน้าห้องมัลติมีเดีย")
    walk = event_host.scene_text(sc["escort_multimedia"], walking=True, spot_id="multimedia_door")
    assert walk.count(event_host.NOT_ARRIVED) == 1
    # a scene with no line of its own for there keeps its arrival line (a greeting fits anywhere)
    for sid in ("debut", "welcome", "farewell"):
        assert "on_stand_th" not in sc[sid], sid
    welcome = event_host.scene_text(sc["welcome"], walking=False, spot_id="reception")
    assert sc["welcome"]["on_arrive_th"] in welcome
    # "" = nothing where she stands, even with an arrival line to fall back on
    assert event_host.scene_text({"title_th": "x", "on_stand_th": "", "on_arrive_th": "ถึงแล้ว"},
                                 walking=False, spot_id="dock") is None
    # and a scene that never names a spot is untouched by it
    assert event_host.scene_text({"title_th": "x", "on_stand_th": "y", "on_start_th": "z"},
                                 walking=False, spot_id=None).endswith("ตอนนี้: z")


def test_where_she_stands_the_next_table_and_the_scene_note_know_it(event, stage, monkeypatch):
    """Review 10 Nov: (1) "ไปจุดถัดไป" with walking off sent "ทักทายแขกที่โต๊ะนี้" (stop_text read the
    arrival line); (2) every tool result said "ตอนนี้อยู่ที่ โต๊ะ 1"; (3) the rest scene's brief rode on
    every tool result and brought "I need to recharge" back; (4) the phone said "Emma พูดอยู่กับที่"
    for a scene with nothing to say there."""
    async def go(sid, *more):
        out = await event_host.start_scene(sid)
        for _ in more:
            await _quiet_tasks()                         # its line goes out before the next press
            out = await event_host.next_stop()
        await _quiet_tasks()
        return out

    out = asyncio.run(go("tables", "next"))
    said = [e[2] for e in stage["log"] if e[0] == "say"]
    assert len(said) == 2 and all("โต๊ะนี้" not in t for t in said), said
    assert "แต่หุ่นยืนอยู่ที่เดิม" in said[1] and event_host.STANDING in said[1]
    assert out["speaks"] is True
    note = event_host.scene_note()
    assert "ตอนนี้อยู่ที่" not in note and note.endswith(event_host.STANDING)
    # a stop that cannot be walked to after one that was: standing from that press on
    event_host.STATE["standing"] = False
    asyncio.run(event_host.go_stop(2))
    assert event_host.scene_note().endswith(event_host.STANDING)
    stage["log"].clear()
    asyncio.run(_quiet_tasks())
    stage["log"].clear()
    out = asyncio.run(go("rest"))
    assert out["speaks"] is False and not [e for e in stage["log"] if e[0] == "say"]
    assert event_host.scene_note() is None, "nothing to act where she stands: nothing beside tool results"
    # walking there, the note says where she is, as before
    event_host.STATE.update(scene="tables", stop=0, standing=False)
    assert "ตอนนี้อยู่ที่ โต๊ะ 1" in event_host.scene_note()


def test_a_refused_roam_is_acted_where_she_stands(event, stage, monkeypatch):
    """Review 10 Nov: "จะเดินทักทายแขกทั่วงาน" went out and then the roam refused (walking off)."""
    from app import event_roam

    monkeypatch.setattr(event_roam, "start", lambda sc: {"ok": False, "error": "no_walk", "error_th": "ปิด"})

    async def go():
        out = await event_host.start_scene("roam_lobby")
        await _quiet_tasks()
        return out

    out = asyncio.run(go())
    [line] = [e[2] for e in stage["log"] if e[0] == "say"]
    assert "เดินทักทายแขกทั่วงาน" not in line and event_host.STANDING in line
    assert out["roam"]["ok"] is False and event_host.STATE["standing"] is True
    stage["log"].clear()
    monkeypatch.setattr(event_roam, "start", lambda sc: {"ok": True, "stops": sc["roam"]["stops"]})
    asyncio.run(go())
    [line] = [e[2] for e in stage["log"] if e[0] == "say"]
    assert "เดินทักทายแขกทั่วงาน" in line and event_host.STANDING not in line
    assert event_host.STATE["standing"] is False


def test_the_walks_start_line_hears_the_red_stop(event, stage, monkeypatch):
    """Review 10 Nov (older): the start line asked only whether the press was current -- a red stop
    after the walk set out left "เชิญทางนี้เลยค่ะ" going out for a walk that had stopped."""
    event_host.spots_path().write_text(json.dumps({"spots": {"reception": {"pose": [-9.0, -5.2, 90.0]}}}),
                                       encoding="utf-8")
    _walker(monkeypatch, WALKS_ON)
    stills = []

    async def fake_say(text, source, still, held_language=True):
        stills.append(still)
        return still()

    monkeypatch.setattr(event_host, "_say", fake_say)

    async def go():
        await event_host.start_scene("welcome")
        await asyncio.sleep(0.05)
        assert stills and stills[0]() is True
        await event_host.stop()
        assert stills[0]() is False
        await _settle(lambda: not event_walk.walking())
        await _quiet_tasks()

    asyncio.run(go())


def test_the_walker_runs_on_the_robots_own_launcher(tmp_path, monkeypatch):
    """On the robot sys.executable is glibc's bare python, which Android cannot
    start: board_walk goes through the `py` launcher beside the code, the way
    emma_server.sh starts the server. A PC has no launcher: sys.executable."""
    code = tmp_path / "emma-server" / "code"
    code.mkdir(parents=True)
    monkeypatch.setattr(event_walk, "ROOT", code)
    assert event_walk._python() == sys.executable
    launcher = tmp_path / "emma-server" / "runtime" / "py"
    launcher.parent.mkdir()
    launcher.write_text("#!/system/bin/sh\n", encoding="utf-8")
    assert event_walk._python() == str(launcher)


# ---- what goes wrong at the event (review 5 Nov) -------------------------------------

STARTED = "print('@@ {\"started\": true, \"q\": 70}', flush=True); "


def _steps_written():
    return json.loads((event_walk.RUN_DIR / "walk.json").read_text(encoding="utf-8"))


def test_a_walk_the_server_died_under_is_reported_and_goes_on_from_its_step(event, said, monkeypatch):
    """Android power-cycled itself three times on 4 Nov, taking the server and
    board_walk with it: the page came back "ready, standing still" with the scene
    forgotten. The note of the walk is what is left to say otherwise."""
    _walker(monkeypatch, STARTED + "import time; print('@@ {\"step\": 2}', flush=True); time.sleep(30)")

    async def go():
        await event_host.start_scene("escort_multimedia")
        run = event_walk.current()
        assert await _settle(lambda: run.done == 2)
        note = (event_walk.RUN_DIR / "active.json").read_text(encoding="utf-8")
        assert json.loads(note)["done"] == 2
        await event_walk.stop("staff")
        # the server is gone and comes back: no clean end was written
        (event_walk.RUN_DIR / "active.json").write_text(note, encoding="utf-8")
        scene_before = event_host.STATE["scene"]
        event_host.STATE.update(scene=None)
        event_walk.reset()

        await event_host.recover_after_restart()
        back = event_walk.current()
        assert event_host.STATE["scene"] == scene_before
        view = event_walk.snapshot()["run"]
        assert view["state"] == "interrupted" and view["kind"] == "restart"
        assert view["can_resume"] and view["next_th"]
        assert not (event_walk.RUN_DIR / "active.json").exists()      # said once

        out = await event_host.resume()
        assert out["ok"], out
        steps = _steps_written()["paths"]["event"]["steps"]
        # undock + the two done are not walked again: door front, off the dock's way
        assert steps[0] == {"do": "undock"} and steps[1]["to"] == back.steps[2]["to"]
        await event_walk.stop("staff")

    asyncio.run(go())


def test_a_walk_that_ended_leaves_no_note(event, said, monkeypatch):
    _walker(monkeypatch, ARRIVES)

    async def go():
        await event_host.start_scene("welcome")
        run = event_walk.current()
        assert await _settle(lambda: run.state == "arrived")
        assert event_walk.recover() is None

    asyncio.run(go())


def test_someone_in_the_way_she_asks_once_and_the_walk_waits(event, said, monkeypatch):
    """A guest in the way used to be 8 s, then a silent stop."""
    _walker(monkeypatch, STARTED + "import time; print('@@ {\"held\": 2, \"at\": [-9.0, -6.0]}', flush=True); "
            "time.sleep(0.2); print('@@ {\"moving\": 2}', flush=True); time.sleep(0.2); "
            "print('@@ {\"held\": 2, \"at\": [-9.5, -6.5]}', flush=True); time.sleep(30)")

    async def go():
        await event_host.start_scene("escort_multimedia")
        run = event_walk.current()
        assert await _settle(lambda: run.done == 0 and sum(s == "event_held" for s, _, _ in said) >= 1)
        assert await _settle(lambda: run.view()["held"])
        await asyncio.sleep(0.6)
        assert sum(s == "event_held" for s, _, _ in said) == 1       # HELD_SAY_EVERY_S
        assert event_walk.walking()                                  # the walk is waiting, not over
        await event_walk.stop("staff")
        assert run.view()["held"] is False

    asyncio.run(go())


@pytest.mark.parametrize("line,kind", [
    ("step 2: stuck", "blocked"),
    ("not on the dock: the wheels stopped after 0.20 m (something behind, not the contacts)", "dock"),
    ("localization quality 30 < 40 -- set the pose first", "lost"),
    ("the board stopped answering (ConnectError)", "board"),
])
def test_a_walk_ending_short_is_said_to_guests_and_named_to_staff(event, said, monkeypatch, line, kind):
    _walker(monkeypatch, STARTED + "import sys; print('[ 1.0] STOP: %s', flush=True); sys.exit(2)" % line)

    async def go():
        await event_host.start_scene("welcome")
        run = event_walk.current()
        assert await _settle(lambda: run.state == "stopped")
        assert await _settle(lambda: any(s == "event_walk_trouble" for s, _, _ in said))
        view = run.view()
        assert view["kind"] == kind and view["next_th"] and view["can_resume"]
        text = next(t for s, t, _ in said if s == "event_walk_trouble")
        assert "ห้ามบอกว่าถึงแล้ว" in text and "กำลังเดินไป" in text

    asyncio.run(go())


def test_a_stop_someone_chose_is_not_apologised_for(event, said, monkeypatch):
    _walker(monkeypatch, STARTED + "import time; time.sleep(30)")

    async def go():
        await event_host.start_scene("welcome")
        await asyncio.sleep(0.5)
        await event_host.stop()
        await asyncio.sleep(0.1)
        assert not any(s == "event_walk_trouble" for s, _, _ in said)

    asyncio.run(go())


def test_a_walker_refused_at_its_gate_never_said_she_was_going(event, said, monkeypatch):
    """Q under the bar, the board busy: refused in the walker's first second, with
    the scene's "I'm on my way" already sent."""
    _walker(monkeypatch, "import sys; print('[ 0.1] STOP: localization quality 30 < 40', flush=True); sys.exit(2)")
    monkeypatch.setattr(event_walk, "GATE_WAIT_S", 5.0)

    async def go():
        out = await event_host.start_scene("welcome")
        assert out["walk"]["ok"] is False and out["walk"]["error"] == "gate"
        assert out["walk"]["run"]["kind"] == "lost"
        assert await _settle(lambda: any(s == "event_scene" for s, _, _ in said))
        text = next(t for s, t, _ in said if s == "event_scene")
        assert event_host.STANDING.strip() in text
        assert not any(s == "event_walk_trouble" for s, _, _ in said)

    asyncio.run(go())


def test_a_low_battery_goes_nowhere_but_the_dock(event, said, monkeypatch):
    import time as _time

    _walker(monkeypatch, ARRIVES)
    monkeypatch.setattr(settings, "event_walk_min_battery", 20)
    event_walk.STATE["health"] = {"at": _time.monotonic(), "board": True, "battery": 12, "q": 70}
    # health_view: a fresh reading only; an old one is "not known", never "all well"
    assert event_walk.snapshot()["health"]["low_battery"] is True

    async def go():
        out = await event_host.go_spot("reception")
        assert out["ok"] is False and out["error"] == "low_battery"
        out = await event_host.go_spot("dock")
        assert out["ok"] is True

    asyncio.run(go())
    event_walk.STATE["health"]["at"] = _time.monotonic() - 60
    assert event_walk.health_view() == {"known": False}


def test_an_event_walk_asks_for_patience_and_nothing_else_does(event, monkeypatch):
    _walker(monkeypatch, ARRIVES)

    async def go():
        await event_host.go_spot("reception")
        doc = _steps_written()
        assert doc["events"] is True and doc["patience_s"] == event_walk.PATIENCE_S
        assert doc["min_quality"] == event_walk.MIN_QUALITY
        assert doc["min_fit"] == event_walk.MIN_FIT

    asyncio.run(go())
    route = json.loads(TRACKED.read_text(encoding="utf-8"))
    assert "patience_s" not in route and "min_quality" not in route


def test_dock_pose_recovery_takes_the_lidars_word(event, tmp_path, monkeypatch):
    """4 Nov: carried to another floor the board kept the old pose, Q32. The staff
    button sets the dock's pose only if board_walk's lidar check says so."""
    script = tmp_path / "fake_walk.py"
    monkeypatch.setattr(event_walk, "SCRIPT", script)
    monkeypatch.setattr(event_walk, "_python", lambda: sys.executable)

    async def go():
        script.write_text("print('@@ {\"dock_pose\": false, \"why\": \"fit\", \"fit\": 0.14}')", encoding="utf-8")
        out = await event_host.dock_pose()
        assert out["ok"] is False and out["error"] == "fit" and out["fit"] == 0.14
        script.write_text("print('@@ {\"dock_pose\": true, \"fit\": 0.7, \"q\": 73}')", encoding="utf-8")
        out = await event_host.dock_pose()
        assert out == {"ok": True, "fit": 0.7, "q": 73}

    asyncio.run(go())


def test_the_staff_page_offers_the_way_back_and_is_honest_when_cut_off():
    page = (ROOT / "client" / "event-control.html").read_text(encoding="utf-8")
    for needle in ("/event/resume", "/event/dock-pose", "lampBattery", "lampFix", "E-stop"):
        assert needle in page
    # with Android down nothing is left to stop a MoveTo: "it stops by itself" was false
    assert "มันจะหยุดเอง" not in page
    server = (ROOT / "app" / "event_control_server.py").read_text(encoding="utf-8")
    assert '"/event/resume"' in server and '"/event/dock-pose"' in server


def test_the_event_app_opens_itself_after_a_boot_and_the_sales_app_does_not():
    """4 Nov: Android reset, "แอป Emma ไม่เปิดเอง" -- no app = no server = no staff
    page, and nobody at the event holds an adb cable. The event build only: a plain
    build is the sales room's app and must not start doing this after a pull."""
    app = ROOT / "apps" / "emma-robot-android" / "app"
    gradle = (app / "build.gradle").read_text(encoding="utf-8")
    main = (app / "src" / "main" / "AndroidManifest.xml").read_text(encoding="utf-8")
    overlay = (app / "src" / "event" / "AndroidManifest.xml").read_text(encoding="utf-8")
    assert "BootReceiver" not in main and "BOOT_COMPLETED" not in main
    assert "BootReceiver" in overlay and "android.intent.action.BOOT_COMPLETED" in overlay
    assert "if (emmaEvent)" in gradle and "src/event/AndroidManifest.xml" in gradle
    java = (app / "src" / "main" / "java" / "com" / "emma" / "robot" / "control" / "BootReceiver.java")
    text = java.read_text(encoding="utf-8")
    assert "RootShell.run" in text and "am start" in text and "goAsync" in text


def test_a_walk_is_noted_from_its_first_moment(event, said, monkeypatch):
    """Android can go in the first seconds of a walk, before any step has ended."""
    _walker(monkeypatch, WALKS_ON)

    async def go():
        await event_host.go_spot("reception")
        assert (event_walk.RUN_DIR / "active.json").exists()
        await event_walk.stop("staff")
        assert not (event_walk.RUN_DIR / "active.json").exists()

    asyncio.run(go())


def test_the_door_spot_stays_clear_of_the_closed_door_and_the_doorway():
    """4 Nov, door closed: (-11.78, -7.36) is 0.40 m from the leaf at y -7.78 (arms'
    gap 1 cm, a spot turn's -6 cm), the creep stopped "something 0.10 m ahead", and
    guests would have to walk round her to get in."""
    doc = json.loads(TRACKED.read_text(encoding="utf-8"))
    steps = doc["spots"]["multimedia_door"]["steps"]
    to, face = steps[-1]["to"], steps[-1]["face"]
    assert to[1] > -7.78 + 0.8 and to[0] > -11.4                 # the leaf; the door's lane
    assert not any("creep" in s or "fallback" in s for s in steps)
    assert -140 < face < -120                                    # she looks at the door


def test_a_recorded_table_is_reached_along_its_aisle(event):
    """The board cuts corners into table edges (28 Oct: an arm hit one)."""
    steps = event_host.spot_steps({"pose": [1.0, 2.0, 90.0], "via": [[0.0, 0.0], [0.5, 1.0]], "name_th": "x"})
    assert [s.get("to") for s in steps[1:]] == [[0.0, 0.0], [0.5, 1.0], [1.0, 2.0]]
    assert steps[-1]["face"] == 90.0 and "face" not in steps[1]


def test_b_on_the_robot_stops_a_scene_walk(event, said, monkeypatch):
    """Review 6 Nov, critical: B or the operator screen DELETEs the board's action, and board_walk took
    that for a leg ended short and sent MoveTo again ~2 s later -- she paused, then walked on. The
    app's latch is the one sign the server gets."""
    _walker(monkeypatch, WALKS_ON)
    latched = {"on": False}
    monkeypatch.setattr(greeter, "_app_latched", lambda link: latched["on"])

    async def go():
        await event_host.start_scene("welcome")
        assert event_walk.walking()
        latched["on"] = True
        for _ in range(40):                               # the page keeps polling
            event_walk.heartbeat()
            await asyncio.sleep(0.05)
            if not event_walk.walking():
                break

    asyncio.run(go())
    assert event_walk.current().reason == "app_stop"
    assert event_walk.kind_of("app_stop") == "person" and event_walk.REASONS_TH["app_stop"]



# ---- the arm at rest before a walk the server runs (review 6 Nov, all-round) --------------------------

def _arm(monkeypatch, *, accepted=True, finishes=True, reason=None):
    """The robot face answering the rest request; `finishes`: its body_status "rest done" follows."""
    import time as _time

    from app import robot_body, robot_face_link

    asked = []
    monkeypatch.setattr(settings, "event_walk_arm_rest", True)
    call = _CallFace()
    monkeypatch.setattr(event_walk, "_call_face", lambda: call)   # a call up: none is rung

    async def request(session, kind, payload, timeout):
        assert isinstance(session, _CallFace), "the rest goes over the call"
        asked.append((kind, payload))
        if accepted and finishes:
            robot_body.STATE.update(last_action="rest", state="done", reason=None, at=_time.time())
        return {"accepted": accepted, "reason": reason}

    monkeypatch.setattr(robot_face_link, "request", request)
    monkeypatch.setattr(event_walk, "ARM_REST_WAIT_S", 0.3)
    return asked


class _CallFace:
    """A call session on the robot face: its voice up, its idle clock held from outside."""

    def __init__(self, provider=True):
        self.provider = object() if provider else None
        self.kept = 0

    def keep_line(self):
        self.kept += 1


def test_a_scene_walk_rests_the_arm_first(event, said, monkeypatch):
    _walker(monkeypatch, ARRIVES)
    asked = _arm(monkeypatch)
    out = asyncio.run(event_host.go_spot("reception"))
    assert out["ok"] is True and asked == [("body", {"action": "rest"})]


@pytest.mark.parametrize("accepted, finishes, reason", [(False, False, "busy"), (True, False, None)])
def test_no_walk_while_the_arm_is_not_at_rest(event, said, monkeypatch, accepted, finishes, reason):
    """A handshake (or its slow lowering) still going answers "busy"; a rest that never says done."""
    _walker(monkeypatch, ARRIVES)
    _arm(monkeypatch, accepted=accepted, finishes=finishes, reason=reason)
    monkeypatch.setattr(event_walk, "REST_BUSY_S", 0.3)            # still busy after it: a handshake still going
    monkeypatch.setattr(event_walk, "REST_COOLDOWN_EVERY_S", 0.05)
    out = asyncio.run(event_host.go_spot("reception"))
    assert out["ok"] is False and out["error"] == "arm_not_rest" and out["error_th"]
    assert not event_walk.walking()


def test_a_rest_answered_busy_for_a_moment_is_asked_again(event, said, monkeypatch):
    """Rehearsal 5 (25 Nov 03:22:19): her talking arm's own rest after the tease answered the walk's rest `busy` in the
    same second, done a moment later -- card 3 was refused arm_not_rest. Asked again within REST_BUSY_S, she walks --
    only after her talking arm (a handshake's busy is asked once: the test below)."""
    import time as _time

    from app import robot_body, robot_face_link

    _walker(monkeypatch, ARRIVES)
    asked = _arm(monkeypatch)
    left = {"n": 2}

    async def request(session, kind, payload, timeout):
        asked.append((kind, payload))
        if left["n"] > 0:
            left["n"] -= 1
            return {"accepted": False, "reason": "busy"}
        robot_body.STATE.update(last_action="rest", state="done", reason=None, at=_time.time())
        return {"accepted": True, "reason": None}

    monkeypatch.setattr(robot_face_link, "request", request)
    monkeypatch.setattr(event_walk, "REST_COOLDOWN_EVERY_S", 0.01)
    robot_body.STATE.update(last_action="raise_left_arm", state="done", reason=None, at=_time.time())   # her talking arm
    out = asyncio.run(event_host.go_spot("reception"))
    assert out["ok"] is True, out
    assert asked[-3:] == [("body", {"action": "rest"})] * 3


@pytest.mark.parametrize("busy_s", [0.0, 0.1, 0.24, 0.4])
def test_a_talking_arm_still_busy_then_cooling_down_is_waited_out(event, said, monkeypatch, busy_s):
    """condo-voice-75's check (25 Nov): the app's cooldown runs from its group 99's END. Her talking arm's rest busy for
    a while after the first ask, then the app's cooldown: the window must start again from the last busy answer, or the
    walk is refused "cooldown". Scaled: REST_COOLDOWN_S 0.4 (4), the app's cooldown 0.3 (3), REST_BUSY_S 0.5 (5)."""
    import time as _time

    from app import robot_body, robot_face_link

    _walker(monkeypatch, ARRIVES)
    _arm(monkeypatch)
    monkeypatch.setattr(event_walk, "REST_COOLDOWN_S", 0.4)
    monkeypatch.setattr(event_walk, "REST_BUSY_S", 0.5)
    monkeypatch.setattr(event_walk, "REST_COOLDOWN_EVERY_S", 0.02)
    robot_body.STATE.update(last_action="raise_left_arm", state="done", reason=None, at=_time.time())
    t0 = {"t": None}

    async def request(session, kind, payload, timeout):
        now = _time.monotonic()
        t0["t"] = now if t0["t"] is None else t0["t"]
        dt = now - t0["t"]
        if dt < busy_s:
            return {"accepted": False, "reason": "busy"}
        if dt < busy_s + 0.3:
            return {"accepted": False, "reason": "cooldown"}
        robot_body.STATE.update(last_action="rest", state="done", reason=None, at=_time.time())
        return {"accepted": True, "reason": None}

    monkeypatch.setattr(robot_face_link, "request", request)
    out = asyncio.run(event_host.go_spot("reception"))
    assert out["ok"] is True, out


def _cooling_arm(monkeypatch, cooldowns, *, then="accept"):
    """The app answering the rest `cooldown` `cooldowns` times (None = always), then `then`."""
    import time as _time

    from app import robot_body, robot_face_link

    asked = _arm(monkeypatch)
    left = {"n": cooldowns}

    async def request(session, kind, payload, timeout):
        asked.append((kind, payload))
        if left["n"] is None or left["n"] > 0:
            if left["n"] is not None:
                left["n"] -= 1
            return {"accepted": False, "reason": "cooldown"}
        if then == "busy":
            return {"accepted": False, "reason": "busy"}
        robot_body.STATE.update(last_action="rest", state="done", reason=None, at=_time.time())
        return {"accepted": True, "reason": None}

    monkeypatch.setattr(robot_face_link, "request", request)
    monkeypatch.setattr(event_walk, "REST_COOLDOWN_EVERY_S", 0.01)
    return asked


def test_a_rest_refused_for_the_apps_cooldown_is_asked_again(event, said, monkeypatch):
    """23 Nov 20:18:39 on the robot: card 1's pageant said done, the walk to the counter asked for the rest in
    the same second, the app answered `cooldown` (BodyGestures.COOLDOWN_MS, 3 s from a gesture's end) and the
    card ended arm_not_rest 1 m out of the dock. The app's timer is asked out, then she walks."""
    _walker(monkeypatch, ARRIVES)
    asked = _cooling_arm(monkeypatch, 2)
    out = asyncio.run(event_host.go_spot("reception"))
    assert out["ok"] is True, out
    assert asked == [("body", {"action": "rest"})] * 3


def test_a_cooldown_that_never_ends_is_still_no_walk(event, said, monkeypatch):
    _walker(monkeypatch, ARRIVES)
    asked = _cooling_arm(monkeypatch, None)
    monkeypatch.setattr(event_walk, "REST_COOLDOWN_S", 0.05)
    out = asyncio.run(event_host.go_spot("reception"))
    assert out["ok"] is False and out["error"] == "arm_not_rest"
    assert not event_walk.walking() and len(asked) >= 2


def test_a_walk_stopped_while_the_cooldown_is_asked_out_asks_no_more(event, said, monkeypatch):
    """Review 23 Nov: a walk stopped (or replaced by another press) during the cooldown re-asks went on sending
    rests for ~4.5 s -- an arm move after the stop, and the replacing walk's own rest answered request_pending/busy."""
    from app import robot_face_link

    _walker(monkeypatch, ARRIVES)
    asked = _cooling_arm(monkeypatch, None)
    cooling = robot_face_link.request

    async def request(session, kind, payload, timeout):
        if not asked:                                    # the first ask: staff press stop as it is refused
            asyncio.ensure_future(event_walk.stop("replaced"))
        return await cooling(session, kind, payload, timeout)

    monkeypatch.setattr(robot_face_link, "request", request)
    out = asyncio.run(event_host.go_spot("reception"))
    assert out["ok"] is False and not event_walk.walking()
    assert asked == [("body", {"action": "rest"})], "no rest asked after the walk was stopped"


def test_a_walk_stopped_while_the_pageant_comes_down_asks_no_rest(event, said, monkeypatch):
    """Second review 23 Nov: stopped/replaced during event_pageant.end's wait = no rest at all (it would race the
    replacing walk's own rest for the call's one pending body request)."""
    from app import event_pageant

    _walker(monkeypatch, ARRIVES)
    asked = _arm(monkeypatch)

    async def end(why, wait=False):
        await event_walk.stop("replaced")                # staff pressed something else while the hand came down
        return True

    monkeypatch.setattr(event_pageant, "end", end)
    out = asyncio.run(event_host.go_spot("reception"))
    assert out["ok"] is False and not event_walk.walking()
    assert asked == [], asked


def test_busy_is_not_asked_out_like_the_cooldown(event, said, monkeypatch):
    """`busy` = a handshake or its slow lowering still going: no walk, asked once (only the app's timer waits)."""
    _walker(monkeypatch, ARRIVES)
    asked = _cooling_arm(monkeypatch, 0, then="busy")
    out = asyncio.run(event_host.go_spot("reception"))
    assert out["ok"] is False and out["error"] == "arm_not_rest"
    assert asked == [("body", {"action": "rest"})]


# ---- no call on the robot face: the walk rings one, muted (owner 10 Nov) ------------------------------
#
# 19:52 the mic was tapped "Muted", the call hung up idle at 19:54:17 (no standing line rings under a
# hold), and the debut pressed at 19:55:49 was refused "arm_not_rest": the rest goes over the call, and
# there was none -- then "เดินแบบไม่ต้องเปิดสายไม่ได้หรอ" -> "เปิดสายแต่ mute ไมค์ได้ไหม".

def _no_call_yet(monkeypatch, *, comes_after=None):
    """No call until the bell is rung; it comes up `comes_after` s later (None = never)."""
    import time as _time

    from app import wake

    state = {"call": None, "rang": [], "at": None}

    async def summon(reason="server"):
        state["rang"].append(reason)
        if comes_after is not None:
            state["at"] = _time.monotonic() + comes_after
        return 1

    def face():
        if state["call"] is None and state["at"] is not None and _time.monotonic() >= state["at"]:
            state["call"] = _CallFace()
        return state["call"]

    monkeypatch.setattr(wake, "summon", summon)
    monkeypatch.setattr(event_walk, "_call_face", face)
    return state


def test_with_no_call_the_walk_rings_one_then_rests_the_arm_over_it(event, said, monkeypatch):
    _walker(monkeypatch, ARRIVES)
    asked = _arm(monkeypatch)
    state = _no_call_yet(monkeypatch, comes_after=0.4)
    out = asyncio.run(event_host.go_spot("reception"))
    assert out["ok"] is True, out
    assert state["rang"] == ["event_walk"]           # a staff ring: the page answers it under a hold, muted
    assert asked == [("body", {"action": "rest"})]


def test_no_call_in_time_is_no_walk_and_the_phone_says_why(event, said, monkeypatch):
    _walker(monkeypatch, ARRIVES)
    asked = _arm(monkeypatch)
    _no_call_yet(monkeypatch)                         # the bell rings, nobody dials
    monkeypatch.setattr(event_walk, "CALL_WAIT_S", 0.5)
    out = asyncio.run(event_host.go_spot("reception"))
    assert out["ok"] is False and out["error"] == "no_call"
    assert "สาย" in out["error_th"] and "แขน" not in out["error_th"].split("(")[0]
    assert asked == [], "no rest asked for without a call"
    assert not event_walk.walking()


def test_a_stop_while_the_call_is_dialling_is_no_walk(event, said, monkeypatch):
    spawned = []

    async def spawn(route):
        spawned.append(route)
        raise AssertionError("no walker after a stop")

    _walker(monkeypatch, ARRIVES)
    monkeypatch.setattr(event_walk, "_spawn", spawn)
    asked = _arm(monkeypatch)
    _no_call_yet(monkeypatch, comes_after=0.6)

    async def go():
        task = asyncio.ensure_future(event_walk.start("reception", "x", [{"do": "goto", "to": [1, 1]}], dock=None))
        await asyncio.sleep(0.2)
        assert event_walk.walking()                   # taken before the dial
        await event_walk.stop("staff")
        return await task

    out = asyncio.run(go())
    assert out["ok"] is False and not spawned and not event_walk.walking()
    assert asked == [], "the dial ends at the stop: nothing is asked of the arm after it"


def test_a_walk_nobody_pressed_rings_nothing_under_a_held_mic(event, said, monkeypatch):
    """Review 10 Nov: a roaming leg (autonomous) under the hold would ring a muted call and walk --
    nobody pressed for it and nobody can stop it by voice (face_approach's "mic_held" rule)."""
    from app import wake

    _walker(monkeypatch, ARRIVES)
    asked = _arm(monkeypatch)
    state = _no_call_yet(monkeypatch, comes_after=0.1)
    monkeypatch.setattr(wake, "mic_held", lambda: True)
    out = asyncio.run(event_walk.start("reception", "x", [{"do": "goto", "to": [1, 1]}], dock=None, autonomous=True))
    assert out["ok"] is False and out["error"] == "no_call"
    assert state["rang"] == [] and asked == []
    # ...pressed by staff, the same hold rings (a muted line) and walks
    out = asyncio.run(event_host.go_spot("reception"))
    assert out["ok"] is True and state["rang"] == ["event_walk"]


def test_a_call_already_up_is_held_from_before_the_rest(event, said, monkeypatch):
    """Review 10 Nov: a summoned line near its 30 s, found up, ended during the arm's rest (~15 s)."""
    from app import robot_face_link

    _walker(monkeypatch, ARRIVES)
    _arm(monkeypatch)
    call = event_walk._call_face()
    kept_at_rest = []
    inner = robot_face_link.request

    async def request(session, kind, payload, timeout):
        kept_at_rest.append(session.kept)
        return await inner(session, kind, payload, timeout)

    monkeypatch.setattr(robot_face_link, "request", request)
    assert asyncio.run(event_host.go_spot("reception"))["ok"] is True
    assert kept_at_rest and kept_at_rest[0] >= 1


def test_a_held_mic_lets_the_staff_bell_through_not_the_standing_line(monkeypatch):
    """The server half of "เปิดสายแต่ mute ไมค์ได้ไหม": wake._refuses under a hold."""
    from app import wake

    ws = object()
    monkeypatch.setattr(wake, "_HELD", {ws})
    assert wake._refuses(ws, "event_walk") is False
    assert wake._refuses(ws, "event_scene") is False
    assert wake._refuses(ws, "standing_line") is True


def test_the_rest_goes_over_the_call_not_the_stop_only_status_link(monkeypatch):
    """Both are claimed faces; the status link claimed after the call (it reconnected) was
    `current_face()` -- and `_send` refuses it everything but a stop (not_robot_face)."""
    from app import robot_face_link

    call = _CallFace()
    link = robot_face_link.StatusLink.__new__(robot_face_link.StatusLink)
    monkeypatch.setattr(robot_face_link, "live_faces", lambda: [link, call])   # newest first
    assert event_walk._call_face() is call
    monkeypatch.setattr(robot_face_link, "live_faces", lambda: [link])
    assert event_walk._call_face() is None and event_walk._call_ready() is False
    monkeypatch.setattr(robot_face_link, "live_faces", lambda: [_CallFace(provider=False)])
    assert event_walk._call_ready() is False            # its socket, its voice not up yet


def test_the_call_is_held_while_she_walks(event, said, monkeypatch):
    """A call rung for a walk is a summoned line nobody greeted: 30 s (SUMMONED_ABANDON_S) and it ends."""
    _walker(monkeypatch, WALKS_ON)
    _arm(monkeypatch)
    call = event_walk._call_face()

    async def go():
        await event_host.go_spot("reception")
        for _ in range(30):
            event_walk.heartbeat()
            await asyncio.sleep(0.1)
        kept = call.kept
        await event_walk.stop("staff")
        return kept

    assert asyncio.run(go()) >= 2


# ---- review 7 Nov: the walk is taken before the arm's rest, not after it ------------------------------
# (the reviewer's race_two_walks.py / race_stop_prelaunch.py, as tests)

class _Stdout:
    def __init__(self):
        self.ev = asyncio.Event()

    async def readline(self):
        await self.ev.wait()
        return b""


class _Proc:
    """board_walk that walks until it is signalled."""

    def __init__(self):
        self.stdout = _Stdout()
        self.returncode = None
        self.signalled = False

    def terminate(self):
        self.signalled = True
        self.returncode = -15
        self.stdout.ev.set()

    def send_signal(self, sig):
        self.terminate()

    def kill(self):
        self.terminate()

    async def wait(self):
        await self.stdout.ev.wait()
        return self.returncode


@pytest.fixture
def slow_rest(event, monkeypatch):
    """Walking on, the arm's rest answering after 0.3 s (~1.5 s on the robot), every walker counted."""
    spawned: list[_Proc] = []

    async def spawn(route):
        spawned.append(_Proc())
        return spawned[-1]

    async def rested():
        await asyncio.sleep(0.3)
        return True

    monkeypatch.setattr(event_walk, "_spawn", spawn)
    monkeypatch.setattr(event_walk, "_faces", lambda: True)
    monkeypatch.setattr(event_walk, "_other_walker", lambda: False)
    monkeypatch.setattr(event_walk, "_arm_rested", rested)
    monkeypatch.setattr(settings, "event_walk_enabled", True)
    return spawned


STEPS = [{"do": "goto", "to": [1.0, 1.0]}]


def test_two_walks_started_at_once_spawn_one_walker(slow_rest):
    """Both used to pass walking() while the first awaited the rest: two walkers, and the red stop
    signalled only the second -- the first stopped by the E-stop alone."""
    async def go():
        a, b = await asyncio.gather(
            event_walk.start("stage_centre", "กลางเวที", STEPS, dock=None),
            event_walk.start("beside_host", "ข้างพิธีกร", STEPS, dock=None))
        assert a["ok"] is True and b["ok"] is False and b["error"] == "busy"
        assert len(slow_rest) == 1 and event_walk.current().spot == "stage_centre"
        assert await event_walk.stop("staff") is True

    asyncio.run(go())
    assert [p.signalled for p in slow_rest] == [True]


def test_the_red_stop_during_the_arms_rest_means_no_walk(event, slow_rest):
    """It answered stopped:False and the walk launched after it."""
    async def go():
        walk = asyncio.create_task(event_host.go_spot("reception"))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if event_walk.walking():
                break
        assert event_walk.walking() and event_walk.current().state == "starting"   # taken, still resting
        import time as _time

        t0 = _time.monotonic()
        stopped = await event_host.stop()                                         # the red button
        assert stopped["stopped"] is True and not event_walk.walking()
        assert _time.monotonic() - t0 < 0.15        # at once: not after the rest (0.3 s), nor SPAWN_WAIT_S
        return await walk

    result = asyncio.run(go())
    assert result["ok"] is False and result["walk"]["error"] == "stopped"
    assert slow_rest == []                                                         # no walker, ever
    run = event_walk.current()
    assert run.state == "stopped" and run.reason == "staff" and run.ended is not None


def test_no_gesture_while_she_is_about_to_walk_but_her_arms_rest_goes(slow_rest):
    """A handshake accepted during the rest was a walk with the arm out. The rest itself must pass
    the same gate (it is what the walk asks for)."""
    from app import robot_face_link as link
    from tests.test_robot_body import Face, accept

    face = Face(accept)

    async def go():
        walk = asyncio.create_task(event_walk.start("stage_centre", "กลางเวที", STEPS, dock=None))
        await asyncio.sleep(0.05)
        assert event_walk.walking() and link.server_walking()
        nod = await link.request(face, "body", {"action": "nod"}, 0.5)
        rest = await link.request(face, "body", {"action": "rest"}, 0.5)
        await walk
        await event_walk.stop("staff")
        return nod, rest

    nod, rest = asyncio.run(go())
    assert nod["accepted"] is False and nod["reason"] == "navigating"
    assert rest["accepted"] is True and face.ws.commands() == ["rest"]


def test_b_on_the_robot_during_the_rest_is_no_walk(slow_rest, monkeypatch):
    latched = {"on": False}
    monkeypatch.setattr(greeter, "_app_latched", lambda link: latched["on"])

    async def go():
        walk = asyncio.create_task(event_walk.start("stage_centre", "กลางเวที", STEPS, dock=None))
        await asyncio.sleep(0.05)
        latched["on"] = True
        return await walk

    result = asyncio.run(go())
    assert result["ok"] is False and result["error"] == "stop_latched" and slow_rest == []
    assert not event_walk.walking() and event_walk.current() is None    # refused: current() as before


def test_a_cancelled_start_during_the_rest_leaves_nothing_walking(slow_rest):
    async def go():
        walk = asyncio.create_task(event_walk.start("stage_centre", "กลางเวที", STEPS, dock=None))
        await asyncio.sleep(0.05)
        walk.cancel()
        with pytest.raises(asyncio.CancelledError):
            await walk

    asyncio.run(go())
    assert not event_walk.walking() and slow_rest == []


def test_a_walks_own_watch_stops_that_walk_and_no_other(slow_rest, monkeypatch):
    """_watch used to call stop(), which stops whatever STATE["run"] is by then."""
    monkeypatch.setattr(settings, "event_walk_heartbeat_s", 0.3)

    async def go():
        assert (await event_walk.start("stage_centre", "กลางเวที", STEPS, dock=None))["ok"]
        first = event_walk.current()
        other = event_walk.Run("beside_host", "ข้างพิธีกร")
        other.state, other.launching, other.proc = "walking", True, _Proc()
        event_walk.STATE["run"] = other                  # as if it had replaced the first
        event_walk.STATE["heartbeat"] = None             # the staff page went quiet
        for _ in range(60):
            await asyncio.sleep(0.05)
            if first.state != "walking":
                break
        return first, other

    first, other = asyncio.run(go())
    assert first.state == "stopped" and first.reason == "control_lost" and slow_rest[0].signalled
    assert other.state == "walking" and other.stop_requested is None and not other.proc.signalled


def test_a_persons_stop_before_start_is_counted_for_the_press_that_began_before_it(slow_rest):
    """go_spot awaits (the roam's stop, the replaced walk, a pose read) before `start`: a stop there
    found nothing to stop. `stops_seen` / `unless_stopped` carry it."""
    async def go():
        seen = event_walk.stops()
        assert await event_walk.stop("replaced") is False and event_walk.stops() == seen   # a scene's own
        assert await event_walk.stop("voice") is False and event_walk.stops() == seen + 1  # nothing walked
        refused = await event_walk.start("stage_centre", "กลางเวที", STEPS, dock=None, stops_seen=seen)
        assert refused["ok"] is False and refused["error"] == "voice" and slow_rest == []
        walked = await event_walk.start("stage_centre", "กลางเวที", STEPS, dock=None)
        out = await event_walk.unless_stopped(seen, {"ok": True, "walk": walked})
        assert out["ok"] is False and out["walk"]["error"] == "stopped"
        assert event_walk.current().stop_requested == "voice" and slow_rest[0].signalled
        # a scene switched all the same: only its walk is refused; nothing walked = untouched
        scene = await event_walk.unless_stopped(seen, {"ok": True, "scene": "x", "walk": {"ok": True, "run": None}})
        assert scene["ok"] is True and scene["walk"]["error"] == "stopped"
        assert await event_walk.unless_stopped(seen, {"ok": True, "scene": "x", "walk": None}) == \
            {"ok": True, "scene": "x", "walk": None}
        same = event_walk.stops()
        assert (await event_walk.unless_stopped(same, {"ok": True, "walk": {"ok": True}}))["ok"] is True

    asyncio.run(go())


def test_a_stop_inside_go_spots_own_awaits_means_no_walker_at_all(event, slow_rest, monkeypatch):
    """The red stop while go_spot was still reading the pose (before `start`): `unless_stopped`
    alone stopped the walk a moment AFTER its walker was spawned -- the wheels could turn. go_spot
    now passes `stops_seen` to `start`, which refuses before any walker exists."""
    async def pose_read_with_a_stop(steps, doc):
        assert await event_walk.stop("staff") is False      # nothing walking yet: only counted
        return steps

    monkeypatch.setattr(event_host, "steps_from_here", pose_read_with_a_stop)

    async def go():
        seen = event_walk.stops()
        return await event_walk.unless_stopped(seen, await event_host.go_spot("reception", stops_seen=seen))

    result = asyncio.run(go())
    assert result["ok"] is False and result["walk"]["error"] == "staff"
    assert slow_rest == []                                   # never spawned, not spawned-then-stopped


def test_the_arms_rest_passes_only_for_the_walk_resting_it(slow_rest, monkeypatch):
    """Round 3: the exemption let the model's move_body(rest) reach the arm (and the head --
    the app's resetHome) mid-walk, and on plain days through walks the app cannot see."""
    from app import robot_face_link as link
    from app import walk_frames
    from tests.test_robot_body import Face, accept

    face = Face(accept)

    async def mid_walk():
        assert (await event_walk.start("stage_centre", "กลางเวที", STEPS, dock=None))["ok"]
        assert event_walk.current().state == "walking"
        rest = await link.request(face, "body", {"action": "rest"}, 0.5)
        await event_walk.stop("staff")
        return rest

    rest = asyncio.run(mid_walk())
    assert rest["accepted"] is False and rest["reason"] == "navigating"
    # a base moving that no event walk explains (board_walk over adb, a joystick)
    monkeypatch.setattr(walk_frames, "base_moving", lambda: True)
    rest = asyncio.run(link.request(Face(accept), "body", {"action": "rest"}, 0.5))
    assert rest["accepted"] is False and rest["reason"] == "navigating"


def test_what_follows_the_wheels_waits_for_the_walker_not_the_arms_rest(slow_rest):
    """Round 3: walking() is true through the rest (up to ~14.5 s, and for a start refused or
    stopped in it) -- read by the lidar, every still person became background, and the walk
    pictures began for walks that never set out. Those read moving()."""
    from app import lidar_people, walk_frames

    async def go():
        walk = asyncio.create_task(event_walk.start("stage_centre", "กลางเวที", STEPS, dock=None))
        await asyncio.sleep(0.05)
        resting = (event_walk.walking(), event_walk.moving(), lidar_people._walking(), walk_frames._label())
        await walk
        out = (event_walk.walking(), event_walk.moving(), lidar_people._walking(), walk_frames._label())
        await event_walk.stop("staff")
        return resting, out

    resting, out = asyncio.run(go())
    assert resting == (True, False, False, None)
    assert out[:3] == (True, True, True) and out[3] == "event-stage_centre"
