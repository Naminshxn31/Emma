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
from pathlib import Path

import pytest

from app import event_host, event_walk
from app.config import settings

ROOT = Path(__file__).resolve().parents[1]
TRACKED = ROOT / "data" / "events" / "floor1_event.json"


@pytest.fixture
def event(tmp_path, monkeypatch):
    """The tracked event file in a tmp folder (the spots file is written beside
    it), with a route file of its own: floor1.json is gitignored."""
    doc = json.loads(TRACKED.read_text(encoding="utf-8"))
    route = tmp_path / "route.json"
    route.write_text(json.dumps({"dock": {"pose": [-7.3, -6.2, 168.8], "approach": [-9.0, -6.2]},
                                 "paths": {}}), encoding="utf-8")
    doc["route_file"] = str(route)
    path = tmp_path / "floor1_event.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "event_file", str(path))
    monkeypatch.setattr(event_walk, "RUN_DIR", tmp_path / ".run")
    event_host.reset()
    return path


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
    assert ids == ["welcome", "escort_multimedia", "tables", "introduce", "farewell", "rest"]
    raw = json.loads(TRACKED.read_text(encoding="utf-8"))
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
    assert door[0] == {"do": "undock"} and door[2]["fallback"]


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

    assert "event" in PROFILES and SALES_PROFILES == ("condo", "event")
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
    assert 'if self.profile not in {"condo", "emma", "joke", "translator", "event"}:' in source
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
    assert r.status_code == 303 and r.headers["location"] == "/event-control"   # the key leaves the URL
    assert "k3y-for-staff" not in r.headers.get("set-cookie", "")
    assert "samesite=strict" in r.headers["set-cookie"].lower() and "httponly" in r.headers["set-cookie"].lower()
    state = client.get("/event/state").json()
    assert state["ok"] and [s["id"] for s in state["scenes"]][0] == "welcome"
    assert event_walk.STATE["heartbeat"] is not None                  # every poll is a heartbeat
    assert client.get("/event-control").status_code == 200
    assert client.post("/event/stop", json={}).json() == {"ok": True, "stopped": False}


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
            if turn.startswith(("server:scene:", "server:arrive:")):
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
