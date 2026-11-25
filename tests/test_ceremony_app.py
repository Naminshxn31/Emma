"""The Emma Ceremony app (owner 9 Nov 2026: "อันนี้ทำแยกกับอันเดิมนะ ไม่งั้นจะปนกัน อันนี้จะเป็น
สำหรับพิธี ที่สามารถใช้ข้อมูลอันเก่าได้") -- app/ceremony.py, its profile, its pages' routes, the
order's screen and handoff steps, and its own file.

What these hold down:
* the mode follows the ceremony app's face call: on at its claim (before the dial), off at its
  end or a takeover -- not for an Event face, not for a desk tab, nothing kept, no clock;
* the ceremony profile is the sales host with the ceremony's block; the event's has none of it;
* "ไม่ปน": while it is on, the Event page's presses that drive her are refused and nothing of the
  event host runs; without it, the ceremony's presses are refused -- the red stop always works;
* the screen step is a scene's `tv` code, the handoff asks the ceremony face to open Emma Event
  and waits for its answer;
* the script moved whole: its words, approvals and recordings, and deploy ships it.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import ceremony, event_host, event_order, event_script, event_walk
from app.config import settings
from tests.ceremony_app import ceremony_off, ceremony_on
from tests.test_event_order import SCRIPT, call, calls, event, rows  # noqa: F401 -- fixtures

ROOT = Path(__file__).resolve().parents[1]
REAL_SCRIPT = ROOT / "data" / "ceremony" / "floor1_ceremony.json"
EVENT = ROOT / "tests" / "data" / "floor1_event_rehearsed.json"


# ---- the mode follows the ceremony app's face call ----------------------------------------------

class _Stop(Exception):
    pass


class _Ws:
    """A robot face's socket that records the mode at accept (session.run claims before it) and
    then ends the call -- or, `hold`, waits there until told."""

    def __init__(self, ua="Mozilla/5.0 EmmaRobot/1.0 EmmaBridge/1", hold=None):
        self.headers = {"user-agent": ua}
        self.query_params = {}
        self.seen: list = []
        self.sent: list = []
        self.hold = hold
        self.closed = False

    async def accept(self):
        self.seen.append((ceremony.on(), ceremony.STATE["session"] is not None))
        if self.hold is not None:
            await self.hold.wait()
        raise _Stop

    async def send_text(self, text):
        self.sent.append(text)

    async def send_json(self, doc):
        self.sent.append(doc)

    async def close(self):
        self.closed = True


def _connect(ws, profile):
    from app import session as session_module

    return session_module.handle_connection(ws, provider="gemini", profile=profile)


def test_the_ceremony_apps_face_call_is_the_mode_from_its_claim_to_its_end():
    ws = _Ws()
    with pytest.raises(_Stop):
        asyncio.run(_connect(ws, "ceremony"))
    assert ws.seen == [(True, True)], "on at the claim, before the call is even accepted"
    assert not ceremony.on() and ceremony.STATE["session"] is None, "the call's end turns it off"


@pytest.mark.parametrize("ua, profile", [
    ("Mozilla/5.0 EmmaRobot/1.0 EmmaBridge/1", "event"),      # the Emma Event app's face
    ("Mozilla/5.0 EmmaRobot/1.0 EmmaBridge/1", "condo"),      # the sales app's face
    ("Mozilla/5.0 Chrome/150", "ceremony"),                     # a desk tab asking for the profile
])
def test_no_other_call_is_the_ceremony(ua, profile):
    ws = _Ws(ua=ua)
    with pytest.raises(_Stop):
        asyncio.run(_connect(ws, profile))
    assert ws.seen == [(False, False)]


def test_the_event_apps_face_taking_the_robot_ends_the_ceremony_at_once(rows):
    """The handoff's own end: the Event app's face connects while the ceremony call is still up.
    Off at the takeover -- before the old call's cleanup has run -- and that cleanup, late, does
    not end the new call's state."""
    async def go():
        hold = asyncio.Event()
        old = _Ws(hold=hold)
        first = asyncio.create_task(_connect(old, "ceremony"))
        for _ in range(50):
            await asyncio.sleep(0)
            if old.seen:
                break
        assert old.seen == [(True, True)] and ceremony.on()
        new = _Ws()
        with pytest.raises(_Stop):
            await _connect(new, "event")
        assert new.seen == [(False, False)], "the Event face's call is not the ceremony"
        assert old.closed is True
        hold.set()
        with pytest.raises(_Stop):
            await first
        return old

    asyncio.run(go())
    assert not ceremony.on()
    offs = [r["why"] for r in rows if r["event"] == "ceremony_app" and r["action"] == "off"]
    assert offs == ["superseded"]


def test_on_reads_the_active_call_not_a_flag():
    """A claim whose call is no longer the robot's active one is no ceremony (the takeover is
    seen before any cleanup) -- and the probes read no file."""
    call_ = ceremony_on()
    assert event_host.ceremony_now() and event_host.cue_only()
    from app import session as session_module

    session_module._active = SimpleNamespace(profile="event")
    assert not event_host.ceremony_now() and not event_host.cue_only_closed()
    session_module._active = call_
    assert event_host.ceremony_now()


def test_the_probes_read_no_file(monkeypatch):
    ceremony_on()

    def boom(*a, **k):
        raise AssertionError("a file read on the audio path")

    monkeypatch.setattr(event_script, "load", boom)
    monkeypatch.setattr(event_host, "load", boom)
    monkeypatch.setattr(Path, "read_text", boom)
    monkeypatch.setattr(Path, "stat", boom)
    for _ in range(3):
        assert event_host.ceremony_now() and event_host.cue_only() and event_host.cue_only_closed()
        assert event_host.mic_deaf() in (True, False)


def test_a_claim_is_only_a_ceremony_profiles():
    assert ceremony.claim(SimpleNamespace(profile="event")) is False
    assert ceremony.claim(None) is False
    assert ceremony.STATE["session"] is None


def test_the_ceremony_app_never_greets_the_room():
    """A call that opens mid-ceremony (the network, a restart) must not welcome anyone: the
    session dials the ceremony profile with no greeting at all, named or cue-only."""
    source = (ROOT / "app" / "session.py").read_text(encoding="utf-8")
    assert ('greeting=None if (self.summoned or self.profile == "ceremony") else greeting_for(self.profile)'
            in source)


# ---- the profile ---------------------------------------------------------------------------------

@pytest.fixture
def files(tmp_path, monkeypatch):
    doc = json.loads(EVENT.read_text(encoding="utf-8"))
    doc["event"]["facts_th"] = ["งานเริ่ม 10 โมงเช้า"]
    doc["event"]["name_th"] = "Embassy Day"
    target = tmp_path / "floor1_event.json"
    target.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "event_file", str(target))
    monkeypatch.setattr(settings, "ceremony_file", str(REAL_SCRIPT))
    event_host.reset()
    event_script.reset()
    return target


def _prompt(profile):
    from app.prompts import build_instructions
    from app.tools import registry

    token = registry.session_profile.set(profile)
    try:
        return build_instructions("Embassy World", profile=profile, robot_face=True)
    finally:
        registry.session_profile.reset(token)


def test_the_ceremony_profile_is_the_sales_host_plus_the_ceremonys_block(files):
    from app.prompts import PROFILES, SALES_PROFILES, load_facts

    assert "ceremony" in PROFILES and "ceremony" in SALES_PROFILES
    condo, cer, ev = _prompt("condo"), _prompt("ceremony"), _prompt("event")
    block = event_host.ceremony_block()
    assert cer.replace("\n\n" + block, "") == condo, "condo byte for byte, plus its block"
    assert load_facts() in cer                                   # the approved facts, shared
    assert "[พิธีเปิดงาน — แอป Emma Ceremony]" in block and event_host.CEREMONY_RULE in block
    assert "- งานเริ่ม 10 โมงเช้า" in block and "งาน Embassy Day" in block       # the event's facts, shared
    assert event_host.STAFF_WALKS in block and "ห้ามแต่ง" in block
    for name in ("go_to_place", "return_to_base", "board_walk"):
        assert name not in block
    # the event's prompt has none of the ceremony, the ceremony's none of the scenes
    assert event_host.CEREMONY_RULE not in ev and "[พิธีเปิดงาน" not in ev and "[งานอีเวนต์" not in cer
    assert "ทีมงานเป็นคนเลือกฉาก" not in block and "event_scene" in block


def test_the_ceremony_profile_sees_the_sales_tools_and_never_the_walking_ones(monkeypatch):
    import app.tools.robot  # noqa: F401 -- registers the walking tools
    from app.tools import registry

    monkeypatch.setattr(settings, "tools_enabled", True)
    monkeypatch.setattr(settings, "tool_groups", "robot")
    token = registry.session_profile.set("ceremony")
    try:
        for name in ("go_to_place", "return_to_base"):
            assert registry.allowed(registry._REGISTRY[name]) is False, name
        assert registry.allowed(registry._REGISTRY["stop_moving"]) is True
    finally:
        registry.session_profile.reset(token)


# ---- "ไม่ปน": the Event page in the ceremony --------------------------------------------------------

def _client(monkeypatch):
    from fastapi.testclient import TestClient

    from app import event_control_server

    monkeypatch.setattr(settings, "event_control_key", "staff-secret")
    client = TestClient(event_control_server.app)
    client.get("/event-control?key=staff-secret")
    return client


REFUSED = [("/event/scene", {"scene": "welcome"}), ("/event/next", {}), ("/event/stop-index", {"stop": 0}),
           ("/event/go", {"spot": "reception"}), ("/event/resume", {}), ("/event/clip", {"clip": "tech_presentation"}),
           ("/event/approach", {"on": True})]


def test_the_event_pages_presses_that_drive_her_are_refused_in_the_ceremony(event, monkeypatch):
    ran = []

    async def records(*a, **k):
        ran.append((a, k))
        return {"ok": True}

    for name in ("start_scene", "next_stop", "go_stop", "go_spot", "resume", "play_clip"):
        monkeypatch.setattr(event_host, name, records)
    from app import face_approach

    monkeypatch.setattr(face_approach, "set_enabled", lambda on: ran.append(("approach", on)) or {"ok": True})
    assert ceremony.on()                                         # the `event` fixture: the app is up
    with _client(monkeypatch) as client:
        for path, body in REFUSED:
            doc = client.post(path, json=body).json()
            assert doc == {"ok": False, "error": "ceremony_app",
                           "error_th": "หุ่นเปิดแอป Emma Ceremony อยู่ — ฉากงานกดได้เมื่อเปิดแอป Emma Event"}, path
        assert ran == []
        # what is not driving her: a stop of the film, walking up OFF, saving a spot
        assert client.post("/event/clip", json={"action": "stop"}).json() == {"ok": True, "stopped": False}
        assert client.post("/event/approach", json={"on": False}).json() == {"ok": True}
        assert ran == [("approach", False)]
        assert client.post("/event/spot", json={"spot": "nope"}).json()["error"] == "no_spot"
        state = client.get("/event/state").json()
        assert state["mode"]["ceremony_app"] is True
        # out of the ceremony: the same presses go through
        ceremony_off()
        for path, body in REFUSED[:-1]:
            assert client.post(path, json=body).json().get("error") != "ceremony_app", path
        assert len(ran) == 1 + len(REFUSED) - 1


def test_the_red_stop_works_from_either_page_in_the_ceremony(event, monkeypatch):
    from app import event_cues, robot_face_link

    async def request_stop(first, kind, timeout):
        return {"accepted": True, "reason": None, "via": "call"}

    monkeypatch.setattr(robot_face_link, "request_stop", request_stop)
    stopped = []
    real_stop = event_host.stop

    async def stop():
        stopped.append("walk")
        return await real_stop()

    monkeypatch.setattr(event_host, "stop", stop)

    async def cut(why):
        stopped.append("line")
        return False

    monkeypatch.setattr(event_cues, "cut", cut)
    assert event_host.live_open(cue_id="s4_recommend", brief_th=None)
    with _client(monkeypatch) as client:
        client.get("/ceremony-control?key=staff-secret")
        doc = client.post("/event/stop", json={}).json()
    assert doc["ok"] is True and stopped == ["walk", "line"] and event_host.live_state() is None


def test_the_event_hosts_own_functions_refuse_in_the_ceremony_too(event):
    """Below the page, for whatever else might call them: a scene, its stops, the resume."""
    ceremony_on()
    for coro in (event_host.start_scene("introduce"), event_host.next_stop(), event_host.go_stop(0),
                 event_host.resume()):
        assert asyncio.run(coro) == event_host.ceremony_refusal()
    assert event_host.STATE["scene"] is None


def test_nothing_of_the_event_host_is_said_into_the_ceremony_but_a_films_end(monkeypatch, rows):
    from app import events

    said = []

    async def announce(text, *, source, still_relevant, summon):
        said.append((source, still_relevant()))

    monkeypatch.setattr(events, "announce", announce)
    asyncio.run(event_host._say("x", "event_arrived", lambda: True))
    assert said == [("event_arrived", True)]
    ceremony_on()
    for source in ("event_scene", "event_arrived", "event_held", "event_walk_trouble", "event_roam_home"):
        asyncio.run(event_host._say("x", source, lambda: True))
    assert said == [("event_arrived", True)]
    assert {r["source"] for r in rows if r["event"] == "event_say_dropped"} == {
        "event_scene", "event_arrived", "event_held", "event_walk_trouble", "event_roam_home"}
    asyncio.run(event_host._say("x", "event_clip_end", lambda: True))
    assert said[-1] == ("event_clip_end", True)
    # an announcement that waited out her voice and found the ceremony begun is dropped too
    ceremony_off()
    got = {}

    async def late(text, *, source, still_relevant, summon):
        ceremony_on()
        got["still"] = still_relevant()

    monkeypatch.setattr(events, "announce", late)
    asyncio.run(event_host._say("x", "event_scene", lambda: True))
    assert got["still"] is False


def test_the_roam_neither_starts_nor_walks_on_in_the_ceremony(event, monkeypatch):
    from app import event_roam

    monkeypatch.setattr(settings, "event_walk_enabled", True)
    sc = event_host.scene("roam_lobby")
    monkeypatch.setattr(event_host, "spot_steps", lambda spot: [{"to": [0, 0]}])
    assert event_roam.start(sc)["error"] == "ceremony_app"
    ceremony_off()

    async def nothing():
        return None

    async def go():
        # the claim's own stop is not what this measures: the loop's word, every tick
        monkeypatch.setattr(ceremony, "_quiet_the_event", nothing)
        monkeypatch.setattr(event_roam, "TICK_S", 0.01)
        monkeypatch.setattr(event_roam, "page_alive", lambda: True)
        monkeypatch.setattr(event_roam, "_someone_about", lambda now: False)
        monkeypatch.setattr(event_roam, "_battery", lambda: None)
        result = event_roam.start(sc)
        assert result["ok"]
        ceremony_on()                                        # the ceremony app opened meanwhile
        for _ in range(100):
            await asyncio.sleep(0.01)
            if not event_roam.STATE["on"]:
                break
        return event_roam.STATE["why"]

    assert asyncio.run(go()) == "ceremony_app"


@pytest.mark.parametrize("ours", [False, True, "pending"])
def test_the_claim_stops_the_event_hosts_roam_walking_up_and_a_scenes_walk(monkeypatch, ours):
    """...but never a walk the ceremony's own order set out on (a call that re-dials mid-walk) --
    nor one still resting its arm before its run id reached the order's mark ("pending": the
    step's go_spot takes up to ~20 s to answer; review of the split, 9 Nov)."""
    from app import event_roam, face_approach

    did = []

    async def roam_stop(why):
        did.append(("roam", why))

    async def walk_stop(why):
        did.append(("walk", why))
        return True

    run = SimpleNamespace(id="run-7", state="starting" if ours == "pending" else "walking")
    monkeypatch.setattr(event_roam, "stop", roam_stop)
    monkeypatch.setattr(face_approach, "_turn_off", lambda why: did.append(("approach", why)))
    monkeypatch.setattr(event_walk, "current", lambda: run)
    monkeypatch.setattr(event_walk, "stop", walk_stop)
    if ours == "pending":
        event_order.STATE["marks"]["1.1"] = {"state": "running", "walk_pending": True}
    elif ours:
        event_order.STATE["marks"]["1.1"] = {"state": "running", "run": "run-7"}

    async def go():
        ceremony_on()
        for _ in range(5):
            await asyncio.sleep(0)

    asyncio.run(go())
    assert ("roam", "ceremony_app") in did and ("approach", "ceremony_app") in did
    assert (("walk", "replaced") in did) is (not ours)


# ---- the ceremony's presses without the ceremony app --------------------------------------------------

def test_the_ceremonys_steps_are_refused_without_its_app_but_a_tick(event, calls, call, rows):
    ceremony_off()
    for n, i in ((1, 0), (1, 1), (1, 2), (1, 3), (2, 0), (2, 1), (2, 3)):
        out = asyncio.run(event_order.press(n, i))
        assert out == {"ok": False, "error": "not_ceremony_app", "error_th": "เปิดแอป Emma Ceremony บนหุ่นก่อน",
                       "n": n, "i": i}, (n, i)
    assert asyncio.run(event_order.next())["error"] == "not_ceremony_app"
    assert calls == [] and event_order.STATE["last"] is None
    assert asyncio.run(event_order.press(2, 2))["ok"]            # the lights person's tick
    assert asyncio.run(event_order.skip(1, 0))["ok"]             # marking, not driving her
    snap = asyncio.run(event_order.snapshot())
    step = next(st for sc in snap["scenes"] if sc["n"] == 1 for st in sc["steps"] if st["i"] == 1)
    assert step["ready"] is False and step["why_th"].startswith("เปิดแอป Emma Ceremony บนหุ่นก่อน")
    ceremony_on()
    assert asyncio.run(event_order.press(1, 1))["ok"] and calls == [("screen", "open")]


def test_a_ceremony_line_pressed_on_its_page_needs_its_app(event, monkeypatch):
    from app import event_cues

    played = []

    async def play(cue_id, *, by="staff", show=None):
        played.append(cue_id)
        return {"ok": True, "mode": "read"}

    monkeypatch.setattr(event_cues, "play", play)
    with _client(monkeypatch) as client:
        ceremony_off()
        assert client.post("/ceremony/cue", json={"id": "s1_greet"}).json() == ceremony.refusal()
        assert played == []
        ceremony_on()
        assert client.post("/ceremony/cue", json={"id": "s1_greet"}).json()["ok"] is True
    assert played == ["s1_greet"]


def test_the_ceremony_page_and_state(event, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app import event_control_server

    monkeypatch.setattr(settings, "event_control_key", "staff-secret")
    with TestClient(event_control_server.app) as client:
        assert client.get("/ceremony-control?key=wrong").status_code == 403
        assert client.get("/ceremony/state").status_code == 403
        first = client.get("/ceremony-control?key=staff-secret", follow_redirects=False)
        assert first.status_code == 200 and "location.replace('/ceremony-control')" in first.text   # same-origin hop
        assert "staff-secret" not in first.text and "samesite=strict" in first.headers["set-cookie"].lower()
        monkeypatch.setattr(event_control_server, "CEREMONY_PAGE", tmp_path / "missing.html")
        assert client.get("/ceremony-control").status_code == 404       # not a crash
        page = tmp_path / "page.html"
        page.write_text("<html>ceremony</html>", encoding="utf-8")
        monkeypatch.setattr(event_control_server, "CEREMONY_PAGE", page)
        assert client.get("/ceremony-control").text == "<html>ceremony</html>"
        before = event_walk.STATE.get("heartbeat")
        state = client.get("/ceremony/state").json()
        assert event_walk.STATE.get("heartbeat") != before          # the walk's dead man's switch
    for key in ("walk", "clips", "screen", "spots", "mode", "cues", "order", "server_time", "face"):
        assert key in state, key
    assert "scenes" not in state and "current" not in state      # the Event app's
    assert state["mode"]["ceremony_app"] is True and state["face"]["ceremony_call"] is True
    assert set(state["screen"]) == {"offered", "open", "name"}
    assert [s["n"] for s in state["order"]["scenes"]] == [1, 2]


# ---- the screen step and the handoff -------------------------------------------------------------

def test_the_screen_step_is_a_scenes_tv_code(event, call, monkeypatch):
    """The ceremony uses the old big screen (owner 9 Nov): event_host.big_screen, the code a
    scene's `tv` runs -- awaited, and the step is what the room PC answered."""
    from app import room_screens

    asked = []

    async def open_screen(name=None, *, by="asked"):
        asked.append(("open", by))
        return {"ok": True}

    async def close_screen(name=None, *, by="asked"):
        asked.append(("close", by))
        return {"ok": False, "error": "unreachable"}

    monkeypatch.setattr(room_screens, "offered", lambda: True)
    monkeypatch.setattr(room_screens, "open_screen", open_screen)
    monkeypatch.setattr(room_screens, "close_screen", close_screen)
    monkeypatch.setattr(room_screens, "state", lambda: {"open": False, "screen": None})
    out = asyncio.run(event_order.press(1, 1))
    assert out["ok"] and asked == [("open", "scene")] and event_host.STATE["tv"]["ok"] is True
    doc = {**SCRIPT, "order": SCRIPT["order"] + [{"n": 3, "title_th": "จบ", "steps": [
        {"do": "screen", "screen": "close"}, {"do": "handoff", "to": "event"}]}]}
    (event.parent / "floor1_ceremony.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    event_script.reset()
    shut = asyncio.run(event_order.press(3, 0))
    assert shut["ok"] is False and shut["error_th"] == "ปิดจอใหญ่ไม่สำเร็จ (unreachable)"
    assert asked[-1] == ("close", "scene")
    assert "ปิดจอใหญ่ไม่สำเร็จ" in event_host.mode_snapshot()["warnings"]
    # no room screen on this machine: not ready, nothing asked
    monkeypatch.setattr(room_screens, "offered", lambda: False)
    assert asyncio.run(event_order.press(1, 1))["error"] == "not_ready" and len(asked) == 2


class _Face:
    """The ceremony app's face call: what it was sent."""

    def __init__(self):
        self.sent: list = []
        self.provider = None
        self._robot_face_claim = True

    async def _send_json(self, doc):
        self.sent.append(doc)


def test_the_handoff_asks_the_ceremony_face_and_waits_for_its_answer(rows):
    face = ceremony_on(session=_Face())

    async def answered(ok, reason=None, *, who=None, app="event"):
        task = asyncio.create_task(ceremony.handoff("event", wait_s=1.0))
        await asyncio.sleep(0)
        assert face.sent[-1] == {"type": "open_app", "app": "event"}
        # an answer from another call, or about another app, is nothing
        assert ceremony.open_app_result(SimpleNamespace(), {"app": "event", "ok": True}) is False
        assert ceremony.open_app_result(face, {"app": "joke", "ok": True}) is False
        assert ceremony.open_app_result(who or face, {"type": "open_app_result", "app": app, "ok": ok,
                                                       "reason": reason}) is True
        return await task

    assert asyncio.run(answered(True)) == {"ok": True, "app": "event"}
    refused = asyncio.run(answered(False, "not_installed"))
    assert refused == {"ok": False, "error": "handoff_failed", "reason": "not_installed",
                       "error_th": "เปิดแอป Emma Event ไม่ได้ — เปิดเองบนหุ่น (not_installed)"}
    assert [(r["ok"], r["reason"]) for r in rows if r["event"] == "ceremony_handoff"] == [
        (True, None), (False, "not_installed")]


def test_a_handoff_nobody_answers_fails_and_says_to_open_it_by_hand():
    face = ceremony_on(session=_Face())
    out = asyncio.run(ceremony.handoff("event", wait_s=0.05))
    assert out["ok"] is False and out["reason"] == "no_answer"
    assert out["error_th"] == "เปิดแอป Emma Event ไม่ได้ — เปิดเองบนหุ่น (หน้าหุ่นไม่ตอบใน 5 วินาที)"
    assert face.sent == [{"type": "open_app", "app": "event"}] and ceremony.STATE["handoff"] is None


def test_a_handoff_goes_only_to_the_ceremony_face_and_only_to_event():
    assert asyncio.run(ceremony.handoff("event")) == ceremony.refusal()          # no ceremony app
    face = ceremony_on(session=_Face())
    assert asyncio.run(ceremony.handoff("joke"))["error"] == "bad_app" and face.sent == []


def test_the_ceremony_call_closing_mid_handoff_ends_the_wait_at_once():
    face = ceremony_on(session=_Face())

    async def go():
        task = asyncio.create_task(ceremony.handoff("event", wait_s=5.0))
        await asyncio.sleep(0)
        ceremony_off()
        return await asyncio.wait_for(task, 1.0)

    out = asyncio.run(go())
    assert out["ok"] is False and out["reason"] == "closed" and "Emma Event" in out["error_th"]
    assert face.sent == [{"type": "open_app", "app": "event"}]


def test_the_session_hands_the_pages_answer_to_the_handoff():
    """session's receive loop: `open_app_result` from the ceremony face goes to the waiter."""
    from app import session as session_module

    class WS:
        def __init__(self, messages):
            self.messages = iter(messages)

        async def receive(self):
            try:
                return {"text": json.dumps(next(self.messages))}
            except StopIteration:
                return {"type": "websocket.disconnect"}

    async def go():
        voice = session_module.VoiceSession(None, provider_name="gemini")
        voice.ws = WS([{"type": "open_app_result", "app": "event", "ok": True, "reason": None}])
        sent = []

        async def send(doc):
            sent.append(doc)

        voice._send_json = send
        ceremony_on(session=voice)
        task = asyncio.create_task(ceremony.handoff("event", wait_s=2.0))
        await asyncio.sleep(0)
        await voice._browser_to_provider()
        return await task, sent

    out, sent = asyncio.run(go())
    assert out == {"ok": True, "app": "event"} and sent == [{"type": "open_app", "app": "event"}]


def test_the_order_runs_the_handoff_step(event, call, monkeypatch):
    face = ceremony_on(session=_Face())
    doc = {**SCRIPT, "order": [{"n": 1, "title_th": "จบ", "steps": [{"do": "handoff", "to": "event"}]}]}
    (event.parent / "floor1_ceremony.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    event_script.reset()
    event_order.reset()

    async def go():
        task = asyncio.create_task(event_order.press(1, 0))
        for _ in range(200):
            await asyncio.sleep(0.01)
            if face.sent:
                break
        assert ceremony.open_app_result(face, {"app": "event", "ok": True}) is True
        return await task

    out = asyncio.run(go())
    assert out["ok"] and out["result"] == {"ok": True, "app": "event"} and out["do"] == "handoff"
    monkeypatch.setattr(ceremony, "HANDOFF_WAIT_S", 0.05)
    failed = asyncio.run(event_order.press(1, 0))                # nobody answers this time
    assert failed["ok"] is False and failed["error"] == "handoff_failed"
    assert failed["error_th"].startswith("เปิดแอป Emma Event ไม่ได้ — เปิดเองบนหุ่น")


# ---- the file moved whole ----------------------------------------------------------------------------

def test_the_ceremony_file_is_the_script_whole_with_its_approvals():
    """data/events/floor1_event.script.json -> data/ceremony/floor1_ceremony.json (9 Nov): the same
    format and cues, every approval still matching its words (the digest is over the cue, not its
    path), and an order that checks clean against the event it shares."""
    config = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    assert 'os.getenv("CEREMONY_FILE", "data/ceremony/floor1_ceremony.json")' in config      # the default
    raw = json.loads(REAL_SCRIPT.read_text(encoding="utf-8"))
    assert raw["format"] == event_script.FORMAT and raw["replies"] == "cues" and raw["host_th"] == "คุณมิกิ"
    cues = event_script.cues(raw)
    # the owner's 12 Nov script (10 scenes, 30 cues) replaced the 6 Nov one (13 cues)
    assert len(cues) == 30 and len({c["id"] for c in cues}) == 30
    for c in cues:
        # ...or the digest of the cue before its English was added (17 Nov, "speak": "en"): a
        # draft again until the owner signs both texts -- tests/test_event_script.py holds that
        thai_ok = [w for w in c.get("words_ok") or [] if any("\u0e00" <= ch <= "\u0e7f" for ch in w)]
        assert c["approved_sha256"] in (None, event_script.digest(c),
                                        event_script.digest(dict(c, en=None, words_ok=thai_ok))), c["id"]
    assert event_script.order_problems(raw, event_host._normalise(json.loads(EVENT.read_text(encoding="utf-8")), {})) == []
    assert not (ROOT / "data" / "events" / "floor1_event.script.json").exists()


@pytest.mark.skipif(not (ROOT / "data" / "ceremony" / "voice" / "index.json").is_file(),
                    reason="NOT CHECKED: the recorded lines (gitignored) are not on this machine -- whether "
                           "they moved with the script is not checked here")
def test_the_recorded_lines_moved_with_it(monkeypatch):
    """The clips are keyed by the words, and the words did not change: every recorded line is
    still found for its cue, beside the ceremony file (app/event_voice.py `folder`)."""
    from app import event_voice

    monkeypatch.setattr(settings, "ceremony_file", str(REAL_SCRIPT))
    index = json.loads((ROOT / "data" / "ceremony" / "voice" / "index.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(settings, "gemini_model", index["model"])
    monkeypatch.setattr(settings, "gemini_voice", index["voice"])
    event_voice.reset()
    event_script.reset()
    assert event_voice.folder() == REAL_SCRIPT.parent / "voice"
    lang = event_script.speak()                      # the clips of the language the file is said in
    recorded = {c["id"] for c in event_script.cues() if c.get(lang) and event_voice.status(c, lang)["recorded"]}
    # (+ the extra lines' clips, 24 Nov: the same folder, their own file -- every clip is a line of one of the two)
    extras = event_script.load(REAL_SCRIPT.with_suffix(".extra.json"))
    recorded |= {c["id"] for c in event_script.cues(extras) if event_voice.status(c, lang)["recorded"]}
    assert recorded == {e["cue"] for e in index["clips"].values()}


def test_a_camera_greeting_decided_before_the_ceremony_call_never_lands_in_it(monkeypatch):
    """Review of the split (9 Nov): the camera decided with no call up (mode off) -- the call
    dropped and was redialling, the app had just opened, a muted standing line -- and its bell
    dialled the ceremony app's call: claimed, the mode on, and the greeting (a server turn the
    cue-only gate plays) went out with a handshake. Dropped after the wait, arm and all."""
    import types

    from app import ceremony, display, events, facewatch, greeter, robot_arm, wake
    from app import session as session_module

    sent, arms = [], []

    class Prov:
        _vad_gate = types.SimpleNamespace(in_speech=False)
        _speech_ended_at = None
        _replied_since_end = True

        async def send_text(self, text):
            sent.append(text)

    async def summon(reason="server"):
        # the ceremony app's face hears the bell and dials: session.run claims at once
        call = session_module.VoiceSession(None, provider_name="gemini", summoned=True, profile="ceremony")
        call.provider = Prov()

        async def _send_json(msg):
            pass

        call._send_json = _send_json
        session_module._active = call
        assert ceremony.claim(call, replies="cues")
        return 1

    async def handshake(session=None):
        arms.append("handshake")
        return True

    async def no_wait(**kw):
        return 0.0

    monkeypatch.setattr(wake, "summon", summon)
    monkeypatch.setattr(wake, "camera_refused", lambda: False)
    monkeypatch.setattr(display, "wait_until_heard", no_wait)
    monkeypatch.setattr(robot_arm, "greet", handshake)
    monkeypatch.setattr(settings, "face_greet_lapse_s", 20.0)
    session_module._active = None
    assert not ceremony.on()                          # the decision: no call, no ceremony
    asyncio.run(greeter._greet(facewatch.Sighting(kind="stranger", name=None, score=0.3, meta={})))
    assert ceremony.on()
    assert sent == [] and arms == []
    # events.announce holds it by itself too, whatever a camera source's own check says
    assert asyncio.run(events.announce("hello", source="face_known")) is False
    assert sent == []
