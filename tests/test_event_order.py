"""The ceremony's running order (owner 7 Nov 2026: "ทำเป็นแบบกดสั่งด้วยนะ พวกซีนต่างๆ"):
the script's nine scenes as steps staff press one by one on the phone -- app/event_order.py,
`order` in data/ceremony/floor1_ceremony.json (app/event_script.py; the Emma Ceremony app's
own file since 9 Nov), the staff gesture path (app/robot_body.py `staff_request`) and the
/ceremony/order/* endpoints. Every press here but a tick needs the ceremony app on the robot
(app/ceremony.py), which the `event` fixture puts up.

What these hold down:
* the real order is whole and names only things that exist;
* each kind goes to its own path with its own id, and only one line is said at a time;
* "ถัดไป" never jumps a step that is not ready -- staff skip on purpose;
* `last` moves only on a step that was taken; a cue is `running` until event_cues ends it;
* a staff gesture skips the heard guard and NOTHING else, and is never a stop;
* the endpoints are behind the staff page's key.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from app import ceremony, event_cues, event_host, event_order, event_script, event_walk, robot_body
from app import robot_contracts as rc
from app import robot_face_link as link
from app.config import settings
from tests.ceremony_app import ceremony_off, ceremony_on
from tests.test_robot_body import Face, accept

ROOT = Path(__file__).resolve().parents[1]
EVENT = ROOT / "tests" / "data" / "floor1_event_rehearsed.json"
REAL_SCRIPT = ROOT / "data" / "ceremony" / "floor1_ceremony.json"
STORY_IDS = ("location_beach", "unit_count", "facilities_overview", "views_collections", "building_height")


def _signed(raw: dict) -> dict:
    c = event_script._normalise(raw)
    return dict(raw, approved_by="test", approved_at="2026-11-07 10:00", approved_sha256=event_script.digest(c))


#: Our own small script: signed lines with no numbers, and an order over the real event's ids.
SCRIPT = {"format": event_script.FORMAT, "host_th": "คุณมิกิ", "cues": [
    _signed({"id": "s1_greet", "scene": 1, "th": "สวัสดีค่ะคุณมิกิ ฉันชื่อเอ็มม่าค่ะ", "numbers": []}),
    _signed({"id": "s2_location", "scene": 2, "th": "ได้เลยค่ะ นี่คือที่ตั้งของโครงการค่ะ", "numbers": []}),
    _signed({"id": "s4_recommend", "scene": 4, "th": None, "live_th": "แนะนำห้องวิวทะเล", "numbers": []}),
    {"id": "s9_thanks", "scene": 9, "th": "ขอขอบคุณทุกคนค่ะ", "numbers": [],
     "approved_by": None, "approved_at": None, "approved_sha256": None},
], "order": [
    {"n": 1, "title_th": "เปิด", "steps": [
        {"do": "walk", "spot": "stage_centre", "label_th": "เดินออกมากลางเวที"},
        {"do": "screen", "screen": "open"},
        {"do": "body", "action": "nod"},
        {"do": "cue", "cue": "s1_greet"},
    ]},
    {"n": 2, "title_th": "ทำเล", "steps": [
        {"do": "cue", "cue": "s2_location", "show": "location_beach"},
        {"do": "clip", "clip": "tech_presentation"},
        {"do": "manual", "label_th": "เปิดไฟล็อบบี้", "note_th": "ยังไม่ได้ต่อ"},
        {"do": "cue", "cue": "s4_recommend"},
        {"do": "cue", "cue": "s9_thanks"},
    ]},
]}


@pytest.fixture
def rows(monkeypatch):
    out: list[dict] = []
    from app import turnlog

    monkeypatch.setattr(turnlog, "record", lambda event, **f: out.append({"event": event, **f}))
    return out


@pytest.fixture
def event(tmp_path, monkeypatch):
    """The tracked event file in a tmp folder (its spots and films, shared) and SCRIPT as the
    ceremony's own file beside it (tests never write real data) -- and the Emma Ceremony app's
    call up: every step but a tick is that app's."""
    target = tmp_path / "floor1_event.json"
    target.write_text(EVENT.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "floor1_ceremony.json").write_text(json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "event_file", str(target))
    monkeypatch.setattr(settings, "ceremony_file", str(tmp_path / "floor1_ceremony.json"))
    monkeypatch.setattr(event_walk, "RUN_DIR", tmp_path / ".run")
    event_host.reset()
    event_script.reset()
    event_cues.reset()
    event_order.reset()
    ceremony_on()
    yield target
    event_host.reset()
    event_script.reset()
    event_cues.reset()


@pytest.fixture
def call(monkeypatch):
    """A live call on the robot face, as event_cues sees it."""
    monkeypatch.setattr(event_cues, "_live_call", lambda: object())


@pytest.fixture
def calls(monkeypatch):
    """Every path a step can take, recorded; each answers ok."""
    out: list[tuple] = []

    async def big_screen(want, *, by="scene"):
        out.append(("screen", want))
        return {"ok": True}

    async def handoff(app="event", *, wait_s=None):
        out.append(("handoff", app))
        return {"ok": True, "app": app}

    async def go_spot(spot_id, *, stops_seen=None):
        out.append(("walk", spot_id))
        return {"ok": True, "walk": {"ok": True, "run": {"id": "run-1"}}}

    async def staff_request(action):
        out.append(("body", action))
        return {"ok": True, "action": action, "hardware": "requested", "error": None, "error_th": None}

    async def play(cue_id, *, by="staff", show=None):
        out.append(("cue", cue_id, by, show))
        return {"ok": True, "mode": "read"}

    async def play_clip(clip_id, *, by="staff", lead_in=False):
        out.append(("clip", clip_id, by))
        return {"ok": True, "clip": clip_id}

    monkeypatch.setattr(event_host, "big_screen", big_screen)
    monkeypatch.setattr(ceremony, "handoff", handoff)
    monkeypatch.setattr("app.room_screens.offered", lambda: True)    # a screen step is ready
    monkeypatch.setattr(event_host, "go_spot", go_spot)
    monkeypatch.setattr(robot_body, "staff_request", staff_request)
    monkeypatch.setattr(robot_body, "staff_ready", lambda action: None)
    monkeypatch.setattr(event_cues, "play", play)
    monkeypatch.setattr(event_host, "play_clip", play_clip)
    return out


def _walkable(monkeypatch, event):
    """Walking on and stage_centre recorded at the robot (the spots file beside the event)."""
    monkeypatch.setattr(settings, "event_walk_enabled", True)
    event_host.spots_path().write_text(json.dumps({"spots": {"stage_centre": {"pose": [-9.0, -3.0, 0.0]}}}),
                                       encoding="utf-8")


def _clip_on_disk(monkeypatch):
    from app import event_clips, room_screens

    monkeypatch.setattr(event_clips, "table", lambda: {"tech_presentation": {
        "id": "tech_presentation", "title_th": "วิดีโอเทคโนโลยี (คุณเจน)", "has_file": True}})
    monkeypatch.setattr(room_screens, "offered", lambda: True)


def _step(snap, n, i) -> dict:
    sc = next(s for s in snap["scenes"] if s["n"] == n)
    return next(st for st in sc["steps"] if st["i"] == i)


def _snap() -> dict:
    return asyncio.run(event_order.snapshot())


def _as_drafts(doc: dict) -> dict:
    """The script with every approval taken off: what a test of "a draft is never served" needs,
    whatever the real file's state (the owner approved 29 cues on 12 Nov)."""
    import copy

    doc = copy.deepcopy(doc)
    for cue in doc.get("cues") or []:
        cue.update(approved_by=None, approved_at=None, approved_sha256=None, approved_voice_sha256=None)
    return doc


# ---- the real file ---------------------------------------------------------------------------

def _real_event() -> dict:
    return event_host._normalise(json.loads(EVENT.read_text(encoding="utf-8")), {})


def test_the_real_order_is_the_owners_ten_scenes_and_validates_clean(monkeypatch):
    """The owner's 12 Nov script (Embassy_World_Actor_Script_TH.docx): ten scenes. The LED, its
    films, the lights, the curtain and the globe are the show team's (Screen_Show_Guide) -- manual
    ticks, never a step that puts her face or a film on a screen."""
    doc = event_script.load(REAL_SCRIPT)
    assert event_script.order_problems(doc, _real_event()) == []
    # with the stories on (the robot), every `show` is an approved story
    monkeypatch.setattr(settings, "stories_enabled", True)
    assert event_script.order_problems(doc, _real_event()) == []
    order = event_script.order(doc)
    assert [sc["n"] for sc in order] == list(range(1, 11))
    steps = [st for sc in order for st in sc["steps"]]
    # every cue of the script is pressed somewhere, once -- but the quotation's backup line: in
    # the order after the real one, "ถัดไป" would play it on top; it is swapped in before the day
    pressed = sorted(st["cue"] for st in steps if st["do"] == "cue")
    # (the demo room's line, still empty, is out of the order since 24 Nov: question 9 is one answer)
    assert pressed == sorted(c["id"] for c in event_script.cues(doc) if c["id"] not in ("s05_backup_quote", "s05_l07"))
    # the room dark and the robot in its box (a tick), then card 1 as ONE step (owner 23 Nov: "ช่วงพิธีอยู่แอปพิธีทั้งหมด"):
    # the countdown, out of the box, the wave and the line, Khun Miki's and the guests' hands, back beside him --
    # scene 1's walk and wave and scene 2's three handshakes and walk back are its, no longer steps of their own
    assert [(st["do"], st["spot"] or st["action"] or st.get("scene")) for st in order[0]["steps"]] == [
        ("manual", None), ("opening", "debut"), ("follow", None)]      # + card 2 (24 Nov; hidden unless CEREMONY_FOLLOW_HOST)
    assert order[0]["steps"][0]["label_th"] == "ไฟห้องปิด ม่านปิด หุ่นพร้อมในกล่อง"
    assert [(st["do"], st["cue"]) for st in order[1]["steps"]][:1] == [("cue", "s02_l03")]
    assert not [st for st in steps if st["do"] == "walk" and st["spot"] in ("stage_centre", "beside_host")]
    assert not [st for st in steps if st["do"] == "body" and st["action"] in ("handshake", "greet_arm")]
    assert [st for st in steps if st["do"] == "opening"] == [order[0]["steps"][1]]
    # the globe is its own spot (one spot for two places sent a walk to the globe)
    assert order[-1]["steps"][0]["spot"] == "beside_globe"
    assert {st["show"] for st in steps if st["show"]} == set(STORY_IDS)
    assert not [st for st in steps if st["do"] in ("screen", "clip")]
    # a "done" line follows the tick that says it is done (lights, curtain, quotation)
    smart = [(st["do"], st["cue"]) for st in order[7]["steps"]]
    for done in ("s08_l08", "s08_l12", "s08_l16"):
        assert smart[smart.index(("cue", done)) - 1][0] == "manual"
    quote = [(st["do"], st["cue"]) for st in order[4]["steps"]]
    # (question 10 is one answer, owner 24 Nov: nothing in it says the quotation is ready, so nothing waits for a tick)
    assert quote[quote.index(("cue", "s05_l13")) - 1] == ("cue", "s05_l11")
    # the ceremony ends handing the robot to the Event app (9 Nov)
    assert [(st["do"], st["to"]) for st in order[-1]["steps"][-1:]] == [("handoff", "event")]
    assert not any(st["do"] is None for st in steps)                    # no scene step left behind
    assert not any(st["do"] == "body" and st["action"] == "stop" for st in steps)
    assert all(st["label_th"] for st in steps if st["do"] == "manual")


def test_the_real_order_on_this_machine_says_why_each_step_waits(event, monkeypatch):
    """The tracked files as they are on a PC, every line a draft (pinned here: the owner approved
    29 on 12 Nov), walking off -- the order shows all of it,
    and the manual ticks are ready."""
    drafts = _as_drafts(json.loads(REAL_SCRIPT.read_text(encoding="utf-8")))
    (event.parent / "floor1_ceremony.json").write_text(json.dumps(drafts, ensure_ascii=False), encoding="utf-8")
    event_script.reset()
    snap = _snap()
    assert [s["n"] for s in snap["scenes"]] == list(range(1, 11))
    assert snap["last"] is None and snap["next"] == [1, 0] and snap["running"] is None
    assert _step(snap, 1, 0)["do"] == "manual" and _step(snap, 1, 0)["ready"] is True   # the room dark, in the box
    opening = _step(snap, 1, 1)
    # card 1 as the ceremony's first step: walking off on a PC, no room screen -- both said; what it does, said
    assert opening["do"] == "opening" and opening["ready"] is False and "EVENT_WALK_ENABLED" in opening["why_th"]
    assert opening["opening"]["no_screen"] is True and "เริ่มโดยไม่มีจอเปิดตัว" in opening["why_th"]
    assert opening["opening"]["steps_th"][:2] == ["จอเปิดตัว: ดนตรีขึ้น → จอมืด → เสียงกระแทก (หุ่นรอในกล่อง แขนพัก)",
                                                  "เดินออกจากกล่อง (แขนลง)"]
    assert "ประตูกล่อง" in opening["note_th"]
    greet = _step(snap, 2, 0)
    assert greet["do"] == "cue" and greet["ready"] is False and "ร่าง" in greet["why_th"]
    assert greet["line_th"].startswith("สวัสดีค่ะคุณมิกิ") and greet["voice"] == "read"
    assert greet["label_th"].startswith("Emma: «สวัสดีค่ะคุณมิกิ") and greet["label_th"].endswith("…»")
    loc = _step(snap, 3, 0)
    assert loc["show_th"].startswith("ภาพ: location_beach") and "STORIES_ENABLED" in loc["why_th"]
    # (the demo room's empty line is out of the order since 24 Nov)
    assert "ใบเสนอราคา" in _step(snap, 5, 2)["line_th"]
    assert _step(snap, 5, 5)["do"] == "manual" and _step(snap, 5, 5)["ready"] is True
    assert _step(snap, 7, 1)["do"] == "manual" and _step(snap, 7, 1)["ready"] is True   # the LED's film
    end = _step(snap, 10, 3)                 # the last press: the Event app takes the after-part
    assert end["do"] == "handoff" and end["ready"] is True and end["label_th"] == "จบพิธี → เปิดแอป Emma Event"
    assert "กดฉาก 'หลังพิธี'" in end["note_th"] and "หน้าคุม Event" in end["note_th"]
    after = next(sc for sc in _real_event()["scenes"] if sc["id"] == "after_ceremony")
    assert after["title_th"].startswith("หลังพิธี")      # the scene the note sends staff to
    assert _step(snap, 4, 2)["note_th"].startswith("มุกเกริ่น")
    # the two jokes said straight through (owner 23 Nov) and questions 1 and 9 as one answer each (owner 24 Nov): `chain` on
    # their steps and nowhere else; the pool joke goes on into her laps (s04_l13, owner 24 Nov "ฉากตอนว่ายหายไป")
    chained = {(sc["n"], st["i"]): st["chain"]["role"] for sc in snap["scenes"] for st in sc["steps"] if "chain" in st}
    assert chained == {(2, 0): "lead", (2, 1): "follow", (4, 2): "lead", (4, 3): "lead", (4, 4): "follow",
                       (5, 1): "lead", (5, 2): "follow",
                       (5, 3): "lead", (5, 4): "follow", (6, 1): "lead", (6, 2): "follow"}
    assert _step(snap, 4, 4)["chain"] == {"role": "follow", "state": None, "th": event_order.CHAIN_FOLLOW_TH,
                                          "warn": False}
    for sc in snap["scenes"]:
        for st in sc["steps"]:
            # `line_en` since 17 Nov, on a line's step only: the real file is spoken in English
            # ("speak": "en") and line_th is the screen's subtitle
            assert set(st) - {"line_en", "chain", "opening", "in_box", "box_th"} == {
                "i", "do", "label_th", "note_th", "line_th", "show_th", "voice", "ready", "why_th", "mark"}
            assert ("line_en" in st) == bool(st["do"] == "cue" and st["line_th"])
            # the in-box tick, and only it, says what it arms (third review 23 Nov: ONE walk out of the box)
            assert ("in_box" in st) == ("box_th" in st) == (st["do"] == "manual" and (sc["n"], st["i"]) == (1, 0))
            assert st["do"] in event_script.ORDER_KINDS and st["mark"] is None


def test_a_screen_and_a_film_step_say_why_they_wait_on_a_pc(event):
    """The 12 Nov order has no screen or film step (the LED is the show team's), so the real file no
    longer shows these two readiness messages: a scene with them added to it does (review 12 Nov --
    the rewrite of the snapshot test above had dropped the only check of both)."""
    doc = json.loads(REAL_SCRIPT.read_text(encoding="utf-8"))
    doc["order"].append({"n": 11, "title_th": "จอใหญ่", "steps": [
        {"do": "screen", "screen": "open", "label_th": "เปิดจอใหญ่ (หน้า Emma)"},
        {"do": "clip", "clip": "tech_presentation"},
        {"do": "screen", "screen": "close", "label_th": "ปิดจอใหญ่ (หน้า Emma)"}]})
    (event.parent / "floor1_ceremony.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    event_script.reset()
    assert event_script.order_problems(doc, _real_event()) == []
    snap = _snap()
    up, film, down = (_step(snap, 11, i) for i in range(3))
    assert up["ready"] is False and up["why_th"] == event_order.SCREEN_UNSET_TH   # no room screen on a PC
    assert up["screen"] == "open" and down["screen"] == "close" and "screen" not in film
    assert set(up) == {"i", "do", "label_th", "note_th", "line_th", "show_th", "voice",
                       "ready", "why_th", "mark", "screen"}
    from app import event_clips

    assert film["ready"] is False and film["why_th"] == event_clips.ERRORS_TH["no_file"]   # not on this disk


def test_no_order_in_the_script_is_no_order(event):
    doc = dict(SCRIPT)
    doc.pop("order")
    (event.parent / "floor1_ceremony.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    event_script.reset()
    assert _snap() is None
    assert asyncio.run(event_order.next())["error"] == "no_order"


@pytest.mark.parametrize("step,problem", [
    ({"do": "cue", "cue": "nope"}, "unknown_cue:nope"),
    ({"do": "scene", "scene": "after_ceremony"}, "scene_is_the_event_apps"),   # 9 Nov: never in the ceremony
    ({"do": "screen", "screen": "maybe"}, "bad_screen:maybe"),
    ({"do": "screen"}, "bad_screen:None"),
    ({"do": "handoff", "to": "joke"}, "bad_app:joke"),
    ({"do": "handoff", "to": "event", "app": "event"}, "unexpected_field:app"),
    ({"do": "walk", "spot": "moon"}, "unknown_spot:moon"),
    ({"do": "clip", "clip": "opening"}, "unknown_clip:opening"),
    ({"do": "body", "action": "dance"}, "unknown_body:dance"),
    ({"do": "body", "action": "stop"}, "stop_is_not_a_step"),
    ({"do": "dance"}, "bad_kind:dance"),
    ({"do": "walk", "spot": "stage_centre", "show": "unit_count"}, "show_not_on_cue"),
    ({"do": "cue", "cue": "s1_greet", "spot": "stage_centre"}, "unexpected_field:spot"),
    ({"do": "manual"}, "manual_without_label"),
    ({"do": "manual", "label_th": 5}, "label_th_not_text"),
    ("cue", "not_an_object"),
])
def test_order_problems_name_what_is_wrong(step, problem):
    doc = {**SCRIPT, "order": [{"n": 1, "title_th": "x", "steps": [step]}]}
    found = event_script.order_problems(doc, _real_event())
    assert any(p.endswith(problem) for p in found), found


def test_order_problems_on_the_scenes_themselves(monkeypatch):
    event = _real_event()
    ok = {"do": "manual", "label_th": "x"}
    assert "duplicate_n:1" in event_script.order_problems(
        {**SCRIPT, "order": [{"n": 1, "title_th": "a", "steps": [ok]}, {"n": 1, "title_th": "b", "steps": [ok]}]},
        event)
    assert "n1_no_steps" in event_script.order_problems({**SCRIPT, "order": [{"n": 1, "title_th": "a"}]}, event)
    assert "n1_no_title" in event_script.order_problems({**SCRIPT, "order": [{"n": 1, "steps": [ok]}]}, event)
    assert "scene_0_bad_n" in event_script.order_problems(
        {**SCRIPT, "order": [{"n": "1", "title_th": "a", "steps": [ok]}]}, event)
    assert event_script.order_problems({**SCRIPT, "order": {}}, event) == ["order_not_a_list"]
    # a story is checked only where stories are on; there an unknown one is named
    bad = {**SCRIPT, "order": [{"n": 1, "title_th": "a", "steps": [
        {"do": "cue", "cue": "s1_greet", "show": "no_such_story"}]}]}
    assert event_script.order_problems(bad, event) == []
    monkeypatch.setattr(settings, "stories_enabled", True)
    assert event_script.order_problems(bad, event) == ["n1.0_unknown_story:no_such_story"]


# ---- each kind goes to its own path --------------------------------------------------------------

def test_each_kind_runs_its_own_path_with_its_own_id(event, calls, call, rows, monkeypatch):
    _walkable(monkeypatch, event)
    _clip_on_disk(monkeypatch)

    async def go():
        for n, i in ((1, 0), (1, 1), (1, 2), (1, 3), (2, 0), (2, 1), (2, 2)):
            result = await event_order.press(n, i)
            assert result["ok"], (n, i, result)
            # a cue stays running until event_cues says it is over (nothing does here)
            for mark in event_order.STATE["marks"].values():
                if mark.get("cue"):
                    mark["state"] = "done"

    asyncio.run(go())
    assert calls == [("walk", "stage_centre"), ("screen", "open"), ("body", "nod"),
                     ("cue", "s1_greet", "staff", None),            # no show: not passed at all
                     ("cue", "s2_location", "staff", "location_beach"),
                     ("clip", "tech_presentation", "staff")]        # the manual tick ran nothing
    assert event_order.STATE["last"] == (2, 2)
    order_rows = [r for r in rows if r["event"] == "event_order"]
    assert [(r["n"], r["i"], r["do"], r["ok"], r["via"]) for r in order_rows][-1] == (2, 2, "manual", True, "press")
    assert all(set(r) == {"event", "n", "i", "do", "ok", "error", "via"} for r in order_rows)


def test_a_cue_without_show_is_played_without_the_keyword(event, call, monkeypatch):
    """`show=` only when the step has one -- an event_cues without the keyword still says the line."""
    seen = []

    async def play(cue_id, *, by="staff"):
        seen.append((cue_id, by))
        return {"ok": True, "mode": "read"}

    monkeypatch.setattr(event_cues, "play", play)
    assert asyncio.run(event_order.press(1, 3))["ok"]
    assert seen == [("s1_greet", "staff")]
    event_order.STATE["marks"]["1.3"]["state"] = "done"
    # a show it cannot take: the line goes alone and the step says the picture did not
    assert asyncio.run(event_order.press(2, 0))["ok"]
    assert seen[-1] == ("s2_location", "staff")
    assert "ภาพไม่ขึ้น" in event_order.STATE["marks"]["2.0"]["error_th"]


# ---- "ถัดไป" never jumps -------------------------------------------------------------------------

def test_next_stops_at_a_step_that_is_not_ready_and_skip_moves_past_it(event, calls, rows):
    assert settings.event_walk_enabled is False                    # conftest: walking off
    result = asyncio.run(event_order.next())
    assert result["ok"] is False and result["error"] == "not_ready" and (result["n"], result["i"]) == (1, 0)
    assert "EVENT_WALK_ENABLED" in result["error_th"]
    assert calls == [] and event_order.STATE["last"] is None        # nothing ran, nothing moved
    assert asyncio.run(event_order.next())["error"] == "not_ready"  # a second tap does not jump either
    assert asyncio.run(event_order.skip(1, 0))["ok"]
    snap = _snap()
    assert snap["last"] == [1, 0] and snap["next"] == [1, 1] and _step(snap, 1, 0)["mark"]["state"] == "skipped"
    assert asyncio.run(event_order.next())["ok"]
    assert calls == [("screen", "open")] and event_order.STATE["last"] == (1, 1)
    assert [r["via"] for r in rows if r["event"] == "event_order"] == ["next", "next", "skip", "next"]


def test_a_press_of_a_step_that_is_not_ready_runs_nothing(event, calls):
    result = asyncio.run(event_order.press(2, 4))                  # s9_thanks: a draft
    assert result["error"] == "not_ready" and "ร่าง" in result["error_th"] and calls == []
    assert asyncio.run(event_order.press(1, 3))["error"] == "not_ready"   # no call on the robot face
    assert event_order.STATE["last"] is None and event_order.STATE["marks"] == {}


def test_last_moves_only_on_a_step_its_path_took(event, calls, monkeypatch):
    async def refused(want, *, by="scene"):
        return {"ok": False, "error": "unreachable"}

    monkeypatch.setattr(event_host, "big_screen", refused)
    result = asyncio.run(event_order.press(1, 1))
    assert result["ok"] is False and result["error_th"] == "เปิดจอใหญ่ไม่สำเร็จ (unreachable)"
    assert event_order.STATE["last"] is None
    mark = _step(_snap(), 1, 1)["mark"]
    assert mark["state"] == "failed" and mark["error_th"] == "เปิดจอใหญ่ไม่สำเร็จ (unreachable)"


def test_the_end_of_the_order_is_the_end(event, calls):
    event_order.STATE["last"] = (2, 4)
    assert asyncio.run(event_order.next())["error"] == "end"
    assert _snap()["next"] is None


# ---- one line at a time --------------------------------------------------------------------------

def _speaking(cue_id: str) -> dict:
    run = {"seq": 99, "id": cue_id, "phase": "speaking"}
    event_cues.STATE["runs"][99] = run
    return run


def test_a_cue_is_running_until_event_cues_ends_it_then_done(event, call, monkeypatch):
    async def play(cue_id, *, by="staff", show=None):
        _speaking(cue_id)                     # as event_cues does: the run stays while she speaks
        return {"ok": True, "mode": "recorded"}

    monkeypatch.setattr(event_cues, "play", play)
    assert asyncio.run(event_order.press(1, 3))["ok"]
    snap = _snap()
    assert _step(snap, 1, 3)["mark"]["state"] == "running" and snap["running"] == [1, 3]
    assert snap["last"] == [1, 3]                                    # taken: the path accepted it
    event_cues.STATE["runs"].clear()
    event_cues.STATE["last"]["s1_greet"] = {"ok": True, "error": None, "at": time.time()}
    snap = _snap()
    assert _step(snap, 1, 3)["mark"]["state"] == "done" and snap["running"] is None


def test_a_cue_that_ended_badly_is_failed_with_its_reason(event, call, monkeypatch):
    async def play(cue_id, *, by="staff", show=None):
        _speaking(cue_id)
        return {"ok": True, "mode": "read"}

    monkeypatch.setattr(event_cues, "play", play)
    assert asyncio.run(event_order.press(1, 3))["ok"]
    event_cues.STATE["runs"].clear()
    event_cues.STATE["last"]["s1_greet"] = {"ok": False, "error": "not_sent", "at": time.time()}
    mark = _step(_snap(), 1, 3)["mark"]
    assert mark["state"] == "failed" and mark["error_th"] == event_cues.ERRORS_TH["not_sent"]


def test_a_cue_refused_by_event_cues_is_failed_and_not_taken(event, call, monkeypatch):
    async def play(cue_id, *, by="staff", show=None):
        return {"ok": False, "error": "no_call", "error_th": event_cues.ERRORS_TH["no_call"]}

    monkeypatch.setattr(event_cues, "play", play)
    result = asyncio.run(event_order.press(1, 3))
    assert result["ok"] is False and result["error"] == "no_call"
    assert event_order.STATE["last"] is None and _step(_snap(), 1, 3)["mark"]["state"] == "failed"


def test_while_she_says_a_line_nothing_else_is_pressed_but_a_manual_tick(event, calls, call):
    _speaking("s1_greet")                                            # a line under way (/event/cue too)
    for n, i in ((1, 1), (1, 3), (2, 0)):
        result = asyncio.run(event_order.press(n, i))
        assert result["error"] == "running" and result["error_th"] == "Emma กำลังพูดอยู่ — รอให้จบก่อน"
    assert asyncio.run(event_order.next())["error"] == "running"
    assert calls == []
    assert asyncio.run(event_order.press(2, 2))["ok"]               # the lights person ticks theirs
    event_cues.STATE["runs"].clear()
    assert asyncio.run(event_order.press(1, 1))["ok"]


def test_a_second_tap_on_the_same_line_is_refused_while_it_is_said(event, call, monkeypatch):
    played = []

    async def play(cue_id, *, by="staff", show=None):
        played.append(cue_id)
        _speaking(cue_id)
        return {"ok": True, "mode": "recorded"}

    monkeypatch.setattr(event_cues, "play", play)

    async def go():
        first = await event_order.press(1, 3)
        again = await event_order.press(1, 3)
        return first, again

    first, again = asyncio.run(go())
    assert first["ok"] and again["error"] == "running" and played == ["s1_greet"]
    assert asyncio.run(event_order.skip(1, 3))["error"] == "running"    # skipping it does not stop her


def test_reset_starts_the_order_again(event, calls, rows):
    asyncio.run(event_order.press(1, 1))
    assert event_order.STATE["last"] == (1, 1)
    assert event_order.reset_order() == {"ok": True}
    snap = _snap()
    assert snap["last"] is None and snap["next"] == [1, 0] and _step(snap, 1, 1)["mark"] is None
    assert rows[-1]["event"] == "event_order" and rows[-1]["via"] == "reset"


def test_a_walk_step_runs_until_its_walk_arrives(event, calls, monkeypatch):
    _walkable(monkeypatch, event)
    assert asyncio.run(event_order.press(1, 0))["ok"]
    run = type("Run", (), {"id": "run-1", "state": "running", "reason": None,
                           "view": lambda self: {"reason_th": "มีคนหรือของขวางทาง"}})()
    monkeypatch.setattr(event_walk, "current", lambda: run)
    assert _step(_snap(), 1, 0)["mark"]["state"] == "running"
    run.state = "stopped"
    mark = _step(_snap(), 1, 0)["mark"]
    assert mark["state"] == "failed" and mark["error_th"] == "มีคนหรือของขวางทาง"


# ---- the staff gesture: every gate but the heard guard --------------------------------------------

class StaffFace(Face):
    """The robot face's live call: a fake page that acks, and a provider (event_cues' test)."""
    provider = object()


@pytest.fixture
def body(monkeypatch):
    for flag in ("robot_body_enabled", "robot_arm_enabled", "robot_arm_motion_enabled",
                 "robot_arm_groups_enabled"):
        monkeypatch.setattr(settings, flag, True)
    monkeypatch.setattr(settings, "robot_body_ack_timeout_s", 0.5)
    assert settings.robot_body_require_heard is True                # conftest: the guard is on
    robot_body.reset_state()
    face = StaffFace(accept)
    link.receive(face, {"type": "body_caps", "enabled": True, "allow_direction_known": False,
                        "gestures": {a: "verified" for a in rc.BODY_ACTION_IDS if a != "stop"}})
    from app import session as session_module

    monkeypatch.setattr(session_module, "_active", face)
    return face


def _staff(action: str) -> dict:
    return asyncio.run(robot_body.staff_request(action))


def test_a_staff_gesture_needs_no_caption_and_the_voice_path_still_does(body, rows):
    from app.tools.registry import robot_face_session

    result = _staff("nod")
    assert result["ok"] is True and result["hardware"] == "requested"
    assert body.ws.commands() == ["nod"]
    assert {"event": "body_request", "action": "nod", "by": "staff"} in rows

    async def voice():
        robot_face_session.set(body)
        return await robot_body.request("nod")

    assert asyncio.run(voice())["reason"] == "heard_missing"        # nobody said "พยักหน้า"
    assert body.ws.commands() == ["nod"]


def test_a_staff_gesture_still_stops_at_the_arm_latch(body, rows, monkeypatch):
    from app import robot_arm

    monkeypatch.setattr(robot_arm, "_armed", False)
    result = _staff("greet_arm")
    assert result["ok"] is False and result["error"] == "latched" and body.ws.sent == []
    assert robot_body.staff_ready("greet_arm") == "latched"
    assert {"event": "body_refused", "action": "greet_arm", "reason": "latched",
            "hardware": "refused", "by": "staff"} in rows


def test_a_staff_gesture_still_needs_the_server_keys(body, monkeypatch):
    monkeypatch.setattr(settings, "robot_arm_motion_enabled", False)
    result = _staff("nod")
    assert result["error"] == "server_disabled" and body.ws.sent == []
    assert robot_body.staff_ready("nod") == "server_disabled"
    monkeypatch.setattr(settings, "robot_arm_motion_enabled", True)
    monkeypatch.setattr(settings, "robot_arm_groups_enabled", False)
    assert _staff("handshake")["error"] == "server_disabled"       # a stored group needs its own key
    assert body.ws.sent == []


def test_a_staff_gesture_is_never_a_stop(body, rows):
    result = _staff("stop")
    assert result["ok"] is False and result["error"] == "stop_not_here" and "E-stop" in result["error_th"]
    assert body.ws.sent == []
    assert robot_body.staff_ready("stop") == "stop_not_here"


def test_a_staff_gesture_needs_the_robot_faces_call(body, monkeypatch):
    from app import session as session_module

    monkeypatch.setattr(session_module, "_active", None)
    assert _staff("nod")["error"] == "no_call" and robot_body.staff_ready("nod") == "no_call"
    monkeypatch.setattr(session_module, "_active", object())       # a desk tab is not the robot face
    assert _staff("nod")["error"] == "no_call"
    assert body.ws.sent == []


def test_a_staff_gesture_is_refused_while_the_server_walks_her(body, monkeypatch):
    monkeypatch.setattr(link, "server_walking", lambda: True)
    assert _staff("nod")["error"] == "navigating" and body.ws.sent == []
    assert robot_body.staff_ready("nod") == "navigating"


def test_a_staff_gesture_takes_the_robots_cached_refusal(body):
    link.receive(body, {"type": "body_caps", "enabled": True, "allow_direction_known": False,
                        "gestures": {"nod": "needs_mapping"}})
    assert _staff("nod")["error"] == "needs_mapping" and body.ws.sent == []
    assert robot_body.staff_ready("nod") == "needs_mapping"


def test_a_staff_gesture_the_robot_refuses_says_why(body):
    body.ws.answer = lambda frame: {"accepted": False, "reason": "busy"}
    result = _staff("wave")
    assert result["ok"] is False and result["error"] == "busy" and result["error_th"] == rc.body_reason_th("busy")


def test_the_order_body_step_uses_the_staff_path(event, body, rows):
    """Through the order, with the real staff path: no caption, and it moves."""
    ceremony_on(session=body)                                    # the face's call is the ceremony app's
    result = asyncio.run(event_order.press(1, 2))
    assert result["ok"], result
    assert body.ws.commands() == ["nod"] and _step(_snap(), 1, 2)["mark"]["state"] == "done"


# ---- the owner's printout -------------------------------------------------------------------------

def test_the_status_printout_lists_the_order_against_the_event_it_shares(capsys):
    """`approve_event_script.py --status --file <script>`: the order checked against the event
    file whose spots and films it names (the tracked one when EVENT_FILE is blank, as in
    conftest; not a file beside the script since 9 Nov) -- and nothing written."""
    from scripts import approve_event_script

    before = REAL_SCRIPT.read_bytes()
    assert settings.event_file == ""
    assert approve_event_script.main(["--status", "--file", str(REAL_SCRIPT)]) == 0
    out = capsys.readouterr().out
    assert "running order:" in out and "ORDER PROBLEM" not in out
    assert "  10. เปิดพรีวิวอย่างเป็นทางการ" in out and "+ story location_beach" in out
    assert "s05_l07" in out and "BREAKS: empty" in out          # the demo room's line, still waiting
    assert REAL_SCRIPT.read_bytes() == before
    assert "handoff event" in out and "screen close" not in out
    # an order with an Event app's scene in it is said, not hidden
    lines = approve_event_script.order_lines({**SCRIPT, "order": [{"n": 1, "title_th": "x", "steps": [
        {"do": "scene", "scene": "after_ceremony"}]}]}, _real_event())
    assert "  ORDER PROBLEM: n1.0_scene_is_the_event_apps" in lines


# ---- the endpoints ---------------------------------------------------------------------------------

def test_the_order_endpoints_need_the_staff_key(event, calls, monkeypatch):
    from fastapi.testclient import TestClient

    from app import event_control_server

    monkeypatch.setattr(settings, "event_control_key", "staff-secret")
    with TestClient(event_control_server.app) as client:
        for path in ("/ceremony/order/next", "/ceremony/order/step", "/ceremony/order/skip",
                     "/ceremony/order/reset", "/ceremony/cue"):
            assert client.post(path, json={"n": 1, "i": 1}).status_code == 403, path
        assert client.get("/ceremony/state").status_code == 403
        assert calls == [] and event_order.STATE["last"] is None
        client.get("/event-control?key=staff-secret")             # the one key, the one cookie
        state = client.get("/ceremony/state").json()
        assert [s["n"] for s in state["order"]["scenes"]] == [1, 2] and state["order"]["next"] == [1, 0]
        assert "cues" in state and isinstance(state["server_time"], float)
        # the Event page carries neither any more, and the old routes are gone (9 Nov)
        event_state = client.get("/event/state").json()
        assert "order" not in event_state and "cues" not in event_state
        for path in ("/event/order/next", "/event/order/step", "/event/order/skip", "/event/order/reset",
                     "/event/cue"):
            assert client.post(path, json={"n": 1, "i": 1}).status_code in (404, 405), path
        assert client.post("/ceremony/order/step", json={"n": "1", "i": 1}).status_code == 400
        assert client.post("/ceremony/order/step", json={"n": True, "i": 1}).status_code == 400
        assert client.post("/ceremony/order/step", json={"n": 1, "i": 1}).json()["ok"] is True
        assert calls == [("screen", "open")]
        assert client.post("/ceremony/order/skip", json={"n": 1, "i": 2}).json()["ok"] is True
        assert client.get("/ceremony/state").json()["order"]["next"] == [1, 3]
        stale = client.post("/ceremony/order/next", json={"n": 1, "i": 1}).json()
        assert stale["ok"] is False and stale["error"] == "stale" and stale["next"] == [1, 3]
        assert stale["error_th"] == "ลำดับเปลี่ยนแล้ว — ดูหน้าจออีกครั้ง"
        assert client.post("/ceremony/order/next", json={"n": "1", "i": 3}).status_code == 400
        assert client.post("/ceremony/order/next", json={"n": 1, "i": 3}).json()["error"] == "not_ready"  # no call
        assert client.post("/ceremony/order/next", json={}).json()["error"] == "not_ready"
        assert calls == [("screen", "open")]                         # the stale press ran nothing
        assert client.post("/ceremony/order/reset", json={}).json() == {"ok": True}
        assert client.get("/ceremony/state").json()["order"]["last"] is None


# ---- review 7 Nov: nothing slow on the loop ----------------------------------------------------------

def test_the_snapshot_never_checks_a_line_on_the_loop_when_its_row_exists(event, call, monkeypatch):
    """A clean cue's row has problems == [] -- that is an answer, not a reason to check again
    (review: ~250 ms a poll, `event_script.problems` 11 times on the loop)."""
    _snap()                                          # event_cues' rows, made once in its thread
    assert event_cues.STATE["status"] is not None

    def boom(*a, **k):
        raise AssertionError("a content check on the loop")

    monkeypatch.setattr(event_script, "problems", boom)
    monkeypatch.setattr(event_script, "number_problems", boom)
    snap = _snap()
    greet = _step(snap, 1, 3)
    assert greet["ready"] is True and greet["why_th"] is None
    assert _step(snap, 2, 4)["ready"] is False and "ร่าง" in _step(snap, 2, 4)["why_th"]   # the draft, from its row


def test_a_cue_with_no_row_yet_is_being_checked(event, call, monkeypatch):
    async def no_rows():
        return {"host_th": None, "call": True, "cues": []}

    monkeypatch.setattr(event_cues, "snapshot", no_rows)
    monkeypatch.setattr(event_script, "problems", lambda *a, **k: (_ for _ in ()).throw(AssertionError("loop")))
    greet = _step(_snap(), 1, 3)
    assert greet["ready"] is False and greet["why_th"] == "กำลังตรวจบท…"


def test_the_stories_catalog_is_read_off_the_loop(event, monkeypatch):
    import threading

    from app import stories

    seen = []
    real = stories.load_catalog

    def load_catalog(*a, **k):
        seen.append(threading.current_thread() is threading.main_thread())
        return real(*a, **k)

    monkeypatch.setattr(stories, "load_catalog", load_catalog)
    monkeypatch.setattr(settings, "stories_enabled", True)
    _snap()
    _snap()                                           # cached: not read again within STATIC_TTL_S
    assert seen == [False]


# ---- review 7 Nov: the red stop stops the arm and the line too ------------------------------------------

def _client(monkeypatch):
    from fastapi.testclient import TestClient

    from app import event_control_server

    monkeypatch.setattr(settings, "event_control_key", "staff-secret")
    client = TestClient(event_control_server.app)
    client.get("/event-control?key=staff-secret")
    return client


def test_the_red_stop_stops_the_walk_the_arm_and_the_line_at_once(event, monkeypatch, rows):
    """Concurrently: the walk stop waits for the arm stop to have started -- a red button
    that stopped the arm only after the walk would time out here."""
    from app import robot_face_link

    arm_started = asyncio.Event()
    sent = []

    async def walk_stop():
        await asyncio.wait_for(arm_started.wait(), 2.0)
        return {"ok": True, "stopped": True}

    async def request_stop(first, kind, timeout):
        sent.append((kind, timeout))
        arm_started.set()
        return {"accepted": True, "reason": None, "via": "call"}

    async def cut(why):
        sent.append(("cut", why))
        return True

    monkeypatch.setattr(event_host, "stop", walk_stop)
    monkeypatch.setattr(robot_face_link, "request_stop", request_stop)
    monkeypatch.setattr(event_cues, "cut", cut, raising=False)
    with _client(monkeypatch) as client:
        doc = client.post("/event/stop", json={}).json()
    assert doc["ok"] is True and doc["stopped"] is True and doc["line_cut"] is True
    assert doc["body"]["sent"] is True and doc["body"]["error_th"] is None and "60 วินาที" in doc["body"]["note_th"]
    assert ("body", robot_body.STAFF_STOP_TIMEOUT_S) in sent and ("cut", "staff") in sent
    assert {"event": "body_stop", "by": "staff", "sent": True, "reason": None, "via": "call"} in rows


def test_the_arm_stop_goes_out_even_when_the_walk_stop_raises(event, monkeypatch):
    from app import robot_face_link

    sent = []

    async def walk_stop():
        raise RuntimeError("the walker would not die")

    async def request_stop(first, kind, timeout):
        sent.append(kind)
        return {"accepted": False, "reason": "timeout", "via": None}

    monkeypatch.setattr(event_host, "stop", walk_stop)
    monkeypatch.setattr(robot_face_link, "request_stop", request_stop)
    monkeypatch.delattr(event_cues, "cut", raising=False)             # not there: nothing to cut
    with _client(monkeypatch) as client:
        doc = client.post("/event/stop", json={}).json()
    assert sent == ["body"]
    assert doc["ok"] is False and "E-stop" in doc["error_th"] and doc["line_cut"] is False
    assert doc["body"]["sent"] is False and "E-stop" in doc["body"]["error_th"]


def test_the_staff_arm_stop_reaches_the_face_with_nothing_else_asked(body, monkeypatch, rows):
    """Not gated: a latched arm, the switches off -- a stop still goes."""
    from app import robot_arm

    monkeypatch.setattr(robot_arm, "_armed", False)
    monkeypatch.setattr(settings, "robot_body_enabled", False)
    result = asyncio.run(robot_body.staff_stop())
    assert result["sent"] is True and body.ws.commands() == ["stop"]
    from app import session as session_module

    monkeypatch.setattr(session_module, "_active", None)
    link.reset_state()
    gone = asyncio.run(robot_body.staff_stop())
    assert gone["sent"] is False and "ไม่มีหน้าหุ่นเชื่อมต่อ" in gone["error_th"]


def test_the_stop_texts_say_what_the_red_button_does():
    assert "แขน" in robot_body.STAFF_ERRORS_TH["stop_not_here"] and "บท" in robot_body.STAFF_ERRORS_TH["stop_not_here"]
    about = json.loads(REAL_SCRIPT.read_text(encoding="utf-8"))["about_order"]
    assert "หยุดทั้งการเดิน แขน และบท" in about


# ---- review 7 Nov: one press at a time, and the button's own step ------------------------------------

def test_two_presses_at_once_run_one(event, calls, monkeypatch):
    """The gate is set before the first await: two taps that land together run once."""
    async def slow_screen(want, *, by="scene"):
        calls.append(("screen", want))
        await asyncio.sleep(0.05)
        return {"ok": True}

    monkeypatch.setattr(event_host, "big_screen", slow_screen)

    async def go():
        return await asyncio.gather(event_order.press(1, 1), event_order.press(1, 1),
                                    event_order.press(1, 2), event_order.next())

    results = asyncio.run(go())
    assert [r["ok"] for r in results] == [True, False, False, False]
    assert {r["error"] for r in results[1:]} == {"busy"}
    assert calls == [("screen", "open")]


def test_next_with_the_step_the_button_showed(event, calls):
    stale = asyncio.run(event_order.next((1, 1)))
    assert stale["error"] == "stale" and stale["next"] == [1, 0] and calls == []
    assert asyncio.run(event_order.skip(1, 0))["ok"]
    assert asyncio.run(event_order.next((1, 1)))["ok"] and calls == [("screen", "open")]
    event_order.STATE["last"] = (2, 4)
    assert asyncio.run(event_order.next((2, 4)))["error"] == "stale"      # the end: nothing to match


# ---- review 7 Nov: `last` never rewinds ----------------------------------------------------------------

def test_skipping_an_earlier_step_does_not_rewind(event, calls):
    event_order.STATE["last"] = (2, 2)
    assert asyncio.run(event_order.skip(1, 1))["ok"]
    snap = _snap()
    assert snap["last"] == [2, 2] and snap["next"] == [2, 3]
    assert _step(snap, 1, 1)["mark"]["state"] == "skipped"
    assert asyncio.run(event_order.skip(2, 4))["ok"] and event_order.STATE["last"] == (2, 4)


def test_pressing_an_earlier_step_again_does_not_rewind(event, calls):
    """The third handshake pressed after the greeting: "ถัดไป" must not bring the greeting back."""
    event_order.STATE["last"] = (2, 2)
    assert asyncio.run(event_order.press(1, 2))["ok"]            # the gesture again
    assert calls == [("body", "nod")] and event_order.STATE["last"] == (2, 2)
    assert _snap()["next"] == [2, 3]


# ---- review 7 Nov: a restart mid-ceremony picks up where it was ----------------------------------------

def test_tests_never_reach_the_robots_copy():
    assert event_order.state_path() is None                       # conftest


@pytest.fixture
def kept(tmp_path, monkeypatch):
    path = tmp_path / "run" / "order.json"
    monkeypatch.setattr(event_order, "state_path", lambda: path)
    return path


def _restart():
    """What a server restart leaves: the files, and nothing in memory."""
    event_order.reset()
    event_cues.reset()


def test_the_order_comes_back_after_a_restart(event, calls, kept):
    assert _snap() and not kept.exists()                          # untouched: nothing written
    asyncio.run(event_order.skip(1, 0))
    assert asyncio.run(event_order.press(1, 1))["ok"]
    doc = json.loads(kept.read_text(encoding="utf-8"))
    assert doc["last"] == [1, 1] and doc["marks"]["1.1"]["state"] == "done" and doc["digest"]
    assert not kept.with_suffix(".tmp").exists()
    _restart()
    snap = _snap()
    assert snap["last"] == [1, 1] and snap["next"] == [1, 2]
    assert _step(snap, 1, 0)["mark"]["state"] == "skipped" and _step(snap, 1, 1)["mark"]["state"] == "done"


def test_a_step_running_when_the_server_died_comes_back_failed(event, call, kept, monkeypatch):
    async def play(cue_id, *, by="staff", show=None):
        _speaking(cue_id)
        return {"ok": True, "mode": "read"}

    monkeypatch.setattr(event_cues, "play", play)
    assert asyncio.run(event_order.press(1, 3))["ok"]
    assert json.loads(kept.read_text(encoding="utf-8"))["marks"]["1.3"]["state"] == "running"
    _restart()
    mark = _step(_snap(), 1, 3)["mark"]
    assert mark["state"] == "failed" and mark["error_th"] == "เซิร์ฟเวอร์เริ่มใหม่ระหว่างขั้นนี้"
    assert _snap()["last"] == [1, 3] and _snap()["running"] is None


def test_a_kept_order_for_another_order_or_another_day_is_ignored(event, calls, kept, monkeypatch):
    asyncio.run(event_order.press(1, 1))
    saved = kept.read_text(encoding="utf-8")
    # the order changed (a step's words): start again
    doc = json.loads((event.parent / "floor1_ceremony.json").read_text(encoding="utf-8"))
    doc["order"][0]["steps"][0]["label_th"] = "เดินออกมา"
    (event.parent / "floor1_ceremony.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    event_script.reset()
    _restart()
    assert _snap()["last"] is None
    # the same order, but older than a ceremony can be
    (event.parent / "floor1_ceremony.json").write_text(json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8")
    event_script.reset()
    old = json.loads(saved)
    old["saved_at"] = time.time() - settings.event_ceremony_max_s - 1
    kept.write_text(json.dumps(old), encoding="utf-8")
    _restart()
    assert _snap()["last"] is None
    kept.write_text(saved, encoding="utf-8")
    _restart()
    assert _snap()["last"] == [1, 1]                               # the same file, in time: back


def test_reset_is_kept_too(event, calls, kept):
    asyncio.run(event_order.press(1, 1))
    event_order.reset_order()
    assert json.loads(kept.read_text(encoding="utf-8"))["last"] is None
    _restart()
    assert _snap()["last"] is None


# ---- review 7 Nov: a film on the big screen, and the live answer ------------------------------------------

def test_a_line_is_not_ready_while_a_film_keeps_her_quiet(event, calls, call, monkeypatch):
    monkeypatch.setattr(event_cues, "_quiet", lambda: "clip")
    greet = _step(_snap(), 1, 3)
    assert greet["ready"] is False and greet["why_th"] == event_cues.ERRORS_TH["quiet"]
    assert asyncio.run(event_order.press(1, 3))["error"] == "not_ready" and calls == []


# ---- review 7 Nov: the live step opens her window (it was a tick, its brief reached nobody) --------------

def _ceremony():
    """The ceremony app's call (the `event` fixture has one up already; a test that took the
    robot's call elsewhere puts it back)."""
    return ceremony_on()


def test_the_live_step_opens_her_window_with_its_brief_and_next_closes_it(event, calls, call, rows):
    """Pressed BEFORE คุณมิกิ asks: her window opens with the cue's own brief and stays running;
    "ถัดไป" (the same step) closes it once she has answered -- only then is it taken."""
    _ceremony()
    event_order.STATE["last"] = (2, 2)
    snap = _snap()
    assert snap["next"] == [2, 3] and _step(snap, 2, 3)["ready"] is True
    opened = asyncio.run(event_order.next((2, 3)))
    assert opened["ok"] and opened["result"]["live"] == "open" and calls == []     # nothing said for her
    assert event_host.live_state()["cue"] == "s4_recommend"
    assert ceremony.STATE["live"]["brief"] == "แนะนำห้องวิวทะเล"                  # its brief, as written
    snap = _snap()
    mark = _step(snap, 2, 3)["mark"]
    assert mark["state"] == "running" and mark["live"] is True
    assert snap["next"] == [2, 3] and snap["live"]["n"] == 2 and snap["live"]["until"] > time.time() + 60
    assert snap["last"] == [2, 2]                              # taken when closed, not when opened
    # while it is open: a gesture, a walk, a line over her answer are refused; a manual tick is not
    for n, i in ((1, 2), (1, 0), (2, 0)):
        refused = asyncio.run(event_order.press(n, i))
        assert refused["error"] == "live" and "ถัดไป" in refused["error_th"]
    assert asyncio.run(event_order.press(2, 2))["ok"] and calls == []
    closed = asyncio.run(event_order.next((2, 3)))
    assert closed["ok"] and closed["result"]["live"] == "closed" and event_host.live_state() is None
    snap = _snap()
    assert _step(snap, 2, 3)["mark"]["state"] == "done" and snap["last"] == [2, 3] and snap["next"] == [2, 4]
    assert snap["live"] is None
    assert [(r["n"], r["i"], r["ok"]) for r in rows if r["event"] == "event_order"][-1] == (2, 3, True)


def test_the_live_step_pressed_again_closes_it_too(event, calls, call, monkeypatch):
    _ceremony()
    assert asyncio.run(event_order.press(2, 3))["ok"] and event_host.live_state() is not None
    # the call dropped mid-answer: the open window can still be closed (never "not ready")
    monkeypatch.setattr(event_cues, "_live_call", lambda: None)
    assert _step(_snap(), 2, 3)["ready"] is True and _step(_snap(), 2, 3)["why_th"] is None
    assert asyncio.run(event_order.press(2, 3))["result"]["live"] == "closed"
    assert event_host.live_state() is None and _step(_snap(), 2, 3)["mark"]["state"] == "done"


def test_her_window_closing_by_itself_ends_the_step_with_a_note(event, calls, call):
    _ceremony()
    assert asyncio.run(event_order.press(2, 3))["ok"]
    event_host.live_close("expired")                           # EVENT_LIVE_MAX_S, or the app closing
    snap = _snap()
    mark = _step(snap, 2, 3)["mark"]
    assert mark["state"] == "done" and mark["error_th"] == event_order.LIVE_EXPIRED_TH
    assert snap["last"] == [2, 3] and snap["next"] == [2, 4] and snap["live"] is None


def test_the_live_step_waits_for_a_ceremony_its_approval_and_a_call(event, calls, monkeypatch):
    """live_open answers False outside a ceremony (the ceremony app is not on); the brief reaches
    the model, so it is approved like a line; she answers on the robot face's call."""
    ceremony_off()
    live = _step(_snap(), 2, 3)
    assert live["ready"] is False and live["why_th"].startswith(event_order.ERRORS_TH["not_ceremony_app"] + " · ")
    assert asyncio.run(event_order.press(2, 3))["error"] == "not_ceremony_app" and event_host.live_state() is None
    _ceremony()
    assert _step(_snap(), 2, 3)["why_th"] == event_cues.ERRORS_TH["no_call"]
    monkeypatch.setattr(event_cues, "_live_call", lambda: object())
    assert _step(_snap(), 2, 3)["ready"] is True
    doc = json.loads((event.parent / "floor1_ceremony.json").read_text(encoding="utf-8"))
    doc["cues"][2]["live_th"] = "แนะนำห้องวิวภูเขา"            # edited after it was signed: a draft again
    (event.parent / "floor1_ceremony.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    event_script.reset()
    event_cues.STATE["status"] = None
    assert _step(_snap(), 2, 3)["why_th"] == event_cues.ERRORS_TH["not_approved"]


def test_the_red_stop_closes_her_window_and_fails_the_step(event, calls, call, monkeypatch, rows):
    from app import robot_face_link

    _ceremony()
    assert asyncio.run(event_order.press(2, 3))["ok"]

    async def walk_stop():
        return {"ok": True, "stopped": False}

    async def request_stop(first, kind, timeout):
        return {"accepted": True, "reason": None, "via": "call"}

    monkeypatch.setattr(event_host, "stop", walk_stop)
    monkeypatch.setattr(robot_face_link, "request_stop", request_stop)
    with _client(monkeypatch) as client:
        doc = client.post("/event/stop", json={}).json()
    assert doc["live_closed"] is True and event_host.live_state() is None
    mark = _step(_snap(), 2, 3)["mark"]
    assert mark["state"] == "failed" and mark["error_th"] == event_order.LIVE_STOPPED_TH


def test_reset_closes_her_window(event, calls, call):
    _ceremony()
    assert asyncio.run(event_order.press(2, 3))["ok"]
    event_order.reset_order()
    assert event_host.live_state() is None and _snap()["live"] is None


# ---- review 7 Nov (A3): a read line is busy until the room has heard it ----------------------------------

def test_a_read_line_holds_the_order_until_her_voice_has_played(event, calls, call, monkeypatch):
    """event_cues' run is over at Gemini's turn_complete with ~20 s of her voice still to play:
    "ถัดไป" sent the next step over her line."""
    audible = {"on": False}
    monkeypatch.setattr(event_cues, "line_audible", lambda now=None: audible["on"])
    assert asyncio.run(event_order.press(1, 3))["ok"]           # its run is over (calls' play leaves none)
    audible["on"] = True                                        # ...and her voice of it is still playing
    snap = _snap()
    assert _step(snap, 1, 3)["mark"]["state"] == "running" and snap["running"] == [1, 3]
    for n, i in ((1, 2), (1, 0)):
        assert asyncio.run(event_order.press(n, i))["error"] == "running"
    assert asyncio.run(event_order.next())["error"] == "running"
    assert calls == [("cue", "s1_greet", "staff", None)]
    audible["on"] = False
    assert _step(_snap(), 1, 3)["mark"]["state"] == "done"
    assert asyncio.run(event_order.press(1, 2))["ok"]
    # a line pressed on /event/cue (no mark of the order's) still playing holds it the same way
    audible["on"] = True
    assert asyncio.run(event_order.press(1, 2))["error"] == "running"


def test_while_her_window_is_open_next_closes_it_wherever_the_order_is(event, calls, call):
    """Opened out of its turn (a double tap): "ถัดไป" is its close, not the step after `last`."""
    _ceremony()
    event_order.STATE["last"] = (1, 0)
    assert asyncio.run(event_order.press(2, 3))["ok"]
    snap = _snap()
    assert snap["next"] == [2, 3] and snap["last"] == [1, 0]
    assert asyncio.run(event_order.next((1, 1)))["error"] == "stale"          # the old button is not it
    assert asyncio.run(event_order.next())["result"]["live"] == "closed" and calls == []
    assert event_host.live_state() is None and event_order.STATE["last"] == (2, 3)


# ---- review 7 Nov (A2): the red stop wins over a press in flight -----------------------------------------

@pytest.fixture
def held_context(monkeypatch):
    """_context() held open until released: a press that is reading its readiness."""
    real = event_order._context
    gate = {"entered": None, "release": None}

    async def slow():
        gate["entered"].set()
        await gate["release"].wait()
        return await real()

    monkeypatch.setattr(event_order, "_context", slow)
    return gate


@pytest.mark.parametrize("stop", ["order", "cut", "walk"])
def test_a_stop_while_a_press_reads_its_readiness_runs_nothing(event, calls, held_context, stop):
    async def go():
        held_context["entered"], held_context["release"] = asyncio.Event(), asyncio.Event()
        press = asyncio.create_task(event_order.press(1, 2))
        await held_context["entered"].wait()
        if stop == "order":
            event_order.staff_stopped()                   # the red stop's first, synchronous step
        elif stop == "cut":
            event_cues.STATE["cut_gen"] = event_cues.cut_gen() + 1      # event_cues.cut
        else:
            await event_walk.stop("voice")                # a voice "หยุด"
        held_context["release"].set()
        return await press

    result = asyncio.run(go())
    assert result["ok"] is False and result["error"] == "stopped" and calls == []
    assert result["error_th"] == event_order.ERRORS_TH["stopped"]


class _Proc:
    def __init__(self, run):
        self.run, self.signalled, self.returncode = run, False, None

    def terminate(self):
        self.signalled, self.returncode, self.run.state = True, -15, "stopped"

    send_signal = kill = lambda self, *a: self.terminate()

    async def wait(self):
        return self.returncode


def test_a_walk_a_press_set_out_after_a_stop_is_stopped_at_once(event, calls, monkeypatch):
    """go_spot awaits the roam's stop, the walk it replaces and a pose read before its `start`: a
    red stop then found no walk, and the walk set out after it."""
    _walkable(monkeypatch, event)
    run = event_walk.Run("stage_centre", "กลางเวที")
    run.id, run.state, run.launching = "run-1", "walking", True
    run.proc = _Proc(run)

    async def go_spot(spot_id, *, stops_seen=None):
        calls.append(("walk", spot_id))
        await event_walk.stop("staff")                    # the red stop, while nothing walked yet
        event_walk.STATE["run"] = run                     # ...and then its walk set out
        return {"ok": True, "walk": {"ok": True, "run": {"id": "run-1"}}}

    monkeypatch.setattr(event_host, "go_spot", go_spot)
    result = asyncio.run(event_order.press(1, 0))
    assert result["ok"] is False and result["error"] == "stopped" and result["result"]["walk"]["error"] == "stopped"
    assert run.proc.signalled and run.stop_requested == "staff"
    assert _step(_snap(), 1, 0)["mark"]["state"] == "failed" and event_order.STATE["last"] is None


@pytest.mark.parametrize("path, body", [("/event/go", {"spot": "stage_centre"}),
                                        ("/event/scene", {"scene": "welcome"})])
def test_the_pages_own_walk_buttons_stop_a_walk_set_out_after_a_stop(event, monkeypatch, path, body):
    ceremony_off()                                        # the Event app's page: its own app on the robot
    run = event_walk.Run("stage_centre", "กลางเวที")
    run.id, run.state, run.launching = "run-1", "walking", True
    run.proc = _Proc(run)

    async def walks(*a, **k):
        await event_walk.stop("staff")                    # the red stop before its walk's `start`
        event_walk.STATE["run"] = run
        return {"ok": True, **({"scene": "welcome"} if path == "/event/scene" else {}),
                "walk": {"ok": True, "run": {"id": "run-1"}}}

    monkeypatch.setattr(event_host, "go_spot", walks)
    monkeypatch.setattr(event_host, "start_scene", walks)
    with _client(monkeypatch) as client:
        doc = client.post(path, json=body).json()
    assert doc["walk"]["ok"] is False and doc["walk"]["error"] == "stopped" and run.proc.signalled
    assert doc["ok"] is (path == "/event/scene")              # a scene switched all the same


def test_a_reset_while_a_press_is_in_flight_keeps_its_gate(event, calls, monkeypatch):
    """reset() cleared `pressing`: a second press ran alongside the one still in flight."""
    async def go():
        gate = asyncio.Event()

        async def slow_screen(want, *, by="scene"):
            calls.append(("screen", want))
            await gate.wait()
            return {"ok": True}

        monkeypatch.setattr(event_host, "big_screen", slow_screen)
        first = asyncio.create_task(event_order.press(1, 1))
        await asyncio.sleep(0.05)
        event_order.reset_order()
        second = await event_order.press(1, 2)
        gate.set()
        return await first, second

    first, second = asyncio.run(go())
    assert second["error"] == "busy" and calls == [("screen", "open")]
    assert first["ok"] and event_order.STATE["last"] is None   # the old press does not move the reset order


# ---- review 7 Nov (A6): the robot's 60 s latch after a stop --------------------------------------------

def test_after_a_stop_a_gesture_or_a_walk_waits_out_the_robots_latch(event, calls, monkeypatch):
    """They showed ready and failed "latched" when pressed."""
    from app import greeter

    _walkable(monkeypatch, event)
    latched = {"on": True}
    monkeypatch.setattr(greeter, "_app_latched", lambda link: latched["on"])
    snap = _snap()
    for n, i in ((1, 0), (1, 2)):
        st = _step(snap, n, i)
        assert st["ready"] is False and st["why_th"] == event_order.LATCHED_TH, (n, i)
    assert _step(snap, 1, 1)["ready"] is True                  # the big screen is not the arm
    assert asyncio.run(event_order.press(1, 2))["error"] == "not_ready" and calls == []
    latched["on"] = False
    assert _step(_snap(), 1, 2)["ready"] is True and _step(_snap(), 1, 0)["ready"] is True


# ---- review 7 Nov (B4): a film is running until the big screen says it plays ----------------------------

def _clips(monkeypatch, *, screen=True):
    from app import event_clips, room_screens

    monkeypatch.setattr(event_clips, "table", lambda: {
        cid: {"id": cid, "title_th": cid, "has_file": True, "path": None, "seconds": None, "after_th": None}
        for cid in ("tech_presentation", "embassy_world_story")})
    monkeypatch.setattr(room_screens, "offered", lambda: screen)
    return event_clips


def test_a_clip_step_runs_until_the_screen_plays_it_and_says_when_it_did_not(event, calls, monkeypatch):
    clips = _clips(monkeypatch)

    async def play_clip(clip_id, *, by="staff", lead_in=False):
        calls.append(("clip", clip_id, by))
        clips.STATE.update(seq=clips.STATE["seq"] + 1, phase="opening", clip=clip_id, error=None)
        return {"ok": True, "clip": clip_id}

    monkeypatch.setattr(event_host, "play_clip", play_clip)
    assert asyncio.run(event_order.press(2, 1))["ok"]
    assert _step(_snap(), 2, 1)["mark"]["state"] == "running"   # cued is not playing
    clips.STATE["phase"] = "asked"
    assert _step(_snap(), 2, 1)["mark"]["state"] == "running"
    clips.STATE["phase"] = "playing"
    assert _step(_snap(), 2, 1)["mark"]["state"] == "done"
    # the screen never started it
    assert asyncio.run(event_order.press(2, 1))["ok"]
    clips.STATE.update(phase="failed", error="no_ack")
    mark = _step(_snap(), 2, 1)["mark"]
    assert mark["state"] == "failed" and mark["error_th"] == clips.ERRORS_TH["no_ack"]
    # stopped before it played
    assert asyncio.run(event_order.press(2, 1))["ok"]
    clips.STATE.update(seq=clips.STATE["seq"] + 1, phase="stopped")
    mark = _step(_snap(), 2, 1)["mark"]
    assert mark["state"] == "failed" and "หยุด" in mark["error_th"]


FILM_CUE = _signed({"id": "s8_story", "scene": 8, "th": "เชิญทุกท่านรับชมวิดีโอค่ะ", "numbers": [],
                    "clip": "embassy_world_story"})


def _with_film_cue(event):
    doc = {**SCRIPT, "cues": SCRIPT["cues"] + [FILM_CUE],
           "order": SCRIPT["order"] + [{"n": 3, "title_th": "วิดีโอ", "steps": [{"do": "cue", "cue": "s8_story"}]}]}
    (event.parent / "floor1_ceremony.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    event_script.reset()


def test_a_line_with_a_film_needs_the_big_screen(event, calls, call, monkeypatch):
    _with_film_cue(event)
    _clips(monkeypatch, screen=False)
    film = _step(_snap(), 3, 0)
    assert film["ready"] is False and "จอใหญ่" in film["why_th"] and film["why_th"].startswith("วิดีโอหลังบท")
    assert asyncio.run(event_order.press(3, 0))["error"] == "not_ready" and calls == []
    _clips(monkeypatch, screen=True)
    assert _step(_snap(), 3, 0)["ready"] is True


def test_a_lines_film_is_followed_from_the_cues_own_row(event, calls, call, monkeypatch):
    _with_film_cue(event)
    clips = _clips(monkeypatch)
    outcome = {"id": "embassy_world_story", "ok": False, "error": "no_screen",
               "error_th": clips.ERRORS_TH["no_screen"]}

    async def play(cue_id, *, by="staff", show=None):
        calls.append(("cue", cue_id, by, show))
        if outcome["ok"]:
            clips.STATE.update(seq=clips.STATE["seq"] + 1, phase="opening", clip=outcome["id"], error=None)
        event_cues.STATE["last"][cue_id] = {"ok": True, "error": None, "at": time.time(), "clip": dict(outcome)}
        return {"ok": True, "mode": "read"}

    monkeypatch.setattr(event_cues, "play", play)
    assert asyncio.run(event_order.press(3, 0))["ok"]
    mark = _step(_snap(), 3, 0)["mark"]
    assert mark["state"] == "done" and mark["error_th"] == "วิดีโอไม่ขึ้น: " + clips.ERRORS_TH["no_screen"]
    # cued: running until the screen plays it
    outcome.update(ok=True, error=None, error_th=None)
    assert asyncio.run(event_order.press(3, 0))["ok"]
    snap = _snap()
    assert _step(snap, 3, 0)["mark"]["state"] == "running"
    assert asyncio.run(event_order.press(1, 2))["ok"]          # her line is over: the film is not her voice
    clips.STATE["phase"] = "playing"
    mark = _step(_snap(), 3, 0)["mark"]
    assert mark["state"] == "done" and not mark["error_th"]


# ---- review 7 Nov (B8): the end is said, and a rehearsal's progress does not reach the ceremony -----------

def test_a_finished_order_says_so(event, calls):
    assert _snap()["finished"] is False
    event_order.STATE["last"] = (2, 4)
    snap = _snap()
    assert snap["finished"] is True and snap["next"] is None


def test_an_order_left_longer_than_a_ceremony_is_cleared_without_a_restart(event, calls, rows):
    assert asyncio.run(event_order.press(1, 1))["ok"]
    assert _snap()["cleared_th"] is None
    event_order.STATE["changed_at"] -= settings.event_ceremony_max_s + 1     # the morning's rehearsal
    snap = _snap()
    assert snap["last"] is None and snap["next"] == [1, 0] and _step(snap, 1, 1)["mark"] is None
    assert snap["cleared_th"].startswith("ล้างความคืบหน้าเก่าแล้ว") and "ชั่วโมง" in snap["cleared_th"]
    assert [r["via"] for r in rows if r["event"] == "event_order"][-1] == "expired"
    assert asyncio.run(event_order.skip(1, 0))["ok"] and _snap()["cleared_th"] is None   # read on
    # never while something runs
    _speaking("s1_greet")
    event_order.STATE["marks"]["1.0"]["state"] = "running"
    event_order.STATE["changed_at"] -= settings.event_ceremony_max_s + 1
    assert _snap()["last"] == [1, 0]


def test_a_kept_order_dated_ahead_of_this_clock_is_not_fresh(event, calls, kept):
    """A7: a copy stamped ahead of now read as fresh for as long as the gap."""
    asyncio.run(event_order.press(1, 1))
    doc = json.loads(kept.read_text(encoding="utf-8"))
    doc["saved_at"] = time.time() + 3600
    kept.write_text(json.dumps(doc), encoding="utf-8")
    _restart()
    assert _snap()["last"] is None
    doc["saved_at"] = time.time() - 10                          # the same copy, dated now: back
    kept.write_text(json.dumps(doc), encoding="utf-8")
    _restart()
    assert _snap()["last"] == [1, 1]


def test_a_kept_order_too_old_says_it_was_cleared(event, calls, kept):
    asyncio.run(event_order.press(1, 1))
    doc = json.loads(kept.read_text(encoding="utf-8"))
    doc["saved_at"] = time.time() - settings.event_ceremony_max_s - 60
    kept.write_text(json.dumps(doc), encoding="utf-8")
    _restart()
    snap = _snap()
    assert snap["last"] is None and snap["cleared_th"] and "ชั่วโมง" in snap["cleared_th"]


def test_the_live_step_opens_while_the_question_line_still_plays(event, calls, call, monkeypatch):
    """s4_ask_view ends "บอกฉันได้เลยค่ะ" and the host answers at once: the window must be open by
    then. The order refused it while her line was still heard (line_audible), the mic stayed deaf,
    and the start of the host's answer went up as silence (review 7 Nov, round 3)."""
    _ceremony()
    assert ceremony.STATE["replies"] == "cues"                   # the file's default: cue-only
    event_order.STATE["last"] = (2, 2)
    monkeypatch.setattr(event_cues, "line_audible", lambda now=None: True)   # her question's last seconds
    out = asyncio.run(event_order.next((2, 3)))
    assert out["ok"] is True, out
    assert event_host.live_state() is not None and event_host.mic_deaf() is False
    event_host.live_close("test")


def test_a_line_waits_for_the_robot_to_stop_moving(event, calls, call, monkeypatch):
    """The mic is open while she walks or gestures (a spoken stop must be heard): applause over
    the third handshake cut s1_greet pressed straight after, as a barge-in (review 7 Nov, round 3)."""
    moving = {"on": True}
    monkeypatch.setattr(event_host, "robot_moving", lambda: moving["on"])
    result = asyncio.run(event_order.press(1, 3))
    assert result["ok"] is False and result["error"] == "moving"
    assert "รอให้จบ" in result["error_th"] and calls == []
    assert asyncio.run(event_order.press(2, 2))["ok"]               # a manual tick is no line
    moving["on"] = False
    assert asyncio.run(event_order.press(1, 3))["ok"]
    assert calls == [("cue", "s1_greet", "staff", None)]


def test_a_walk_step_is_the_ceremonys_own_while_it_is_still_starting(event, calls, call, monkeypatch):
    """Its run id reaches the mark only when go_spot answers (~20 s with the arm's rest): until then
    `walk_pending` tells ceremony._ceremony_walk the walk is ours -- a ceremony call that re-dialled
    in between stopped it as the event's (review of the split, 9 Nov). Gone once answered."""
    seen = []

    async def go_spot(spot_id, *, stops_seen=None):
        seen.append([dict(m) for m in event_order.STATE["marks"].values()])
        return {"ok": True, "walk": {"ok": True, "run": {"id": "run-9"}}}

    _walkable(monkeypatch, event)
    monkeypatch.setattr(event_host, "go_spot", go_spot)
    assert asyncio.run(event_order.press(1, 0))["ok"]
    assert any(m.get("walk_pending") for m in seen[0])
    mark = event_order.STATE["marks"][event_order._key(1, 0)]
    assert "walk_pending" not in mark and mark["run"] == "run-9"
