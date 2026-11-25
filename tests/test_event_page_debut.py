"""The Event page's scenes, in a real browser: one numbered box each, in the event file's order
(owner 10 Nov 2026: "ส่วนหน้าควบคุม อยากให้มันช่องๆ แยกแต่ละซีนเป็นลำดับเลย"), the debut (owner
9 Nov: "หน้าสั่งยังดูใช้ยาก ทำให้ใช้ง่ายหน่อย เอาแบบเดินเปิดตัว หุ่นโบกมือทักทายพร้อมพูดประโยค") box 1 of them.

Drawn from the server's OWN snapshot (event_host.snapshot over the tracked event file, its
`perform` from event_host._perform): a field the page reads by a name the server does not send
goes red here, not at the event. What these hold down:

* every scene is a box, numbered 1..N in the file's order, the scene now marked and the one after
  it marked "ถัดไป" (nothing pressed yet: box 1);
* each box says what happens in order, the sentence she will say, and why it will be done where she
  stands -- walking off, a spot not recorded, a scene with nothing to say there ("ไม่พูด");
* every box's own start button takes two taps (a scene can move the robot and its arm), and so does
  saving the debut's spot;
* what came of a press is said in that scene's box ("accepted", never "it waved"), a refusal in red;
* nothing is wider than a phone.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import event_host, event_walk
from app.config import settings
from tests.test_event_order_page import Page, chromium  # noqa: F401 -- the fixture, used by name

ROOT = Path(__file__).resolve().parents[1]
TRACKED = ROOT / "tests" / "data" / "floor1_event_rehearsed.json"
EVENT_PAGE = (ROOT / "client" / "event-control.html").read_text(encoding="utf-8")
SENTENCE = "Hello everyone! I'm Emmaaa! Welcome, and thank you so much for being here with us today!"
PAGEANT = "ท่านางงาม (โบกมือ หันซ้าย-ขวา)"


@pytest.fixture
def served(tmp_path, monkeypatch):
    path = tmp_path / "floor1_event.json"
    path.write_text(TRACKED.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(settings, "event_file", str(path))
    monkeypatch.setattr(event_walk, "RUN_DIR", tmp_path / ".run")
    event_host.reset()
    yield path
    event_host.reset()


def _record_stage():
    event_host.spots_path().write_text(json.dumps({"spots": {"stage_centre": {
        "pose": [-9.0, -3.0, 0.0], "recorded_at": "2026-11-24 18:00"}}}), encoding="utf-8")


def _state(walk_on: bool = True) -> dict:
    """The server's snapshot as /event/state sends it, with a robot that is up."""
    snap = json.loads(json.dumps(event_host.snapshot(), default=str))
    snap["walk"]["enabled"] = walk_on
    snap["walk"]["health"] = {"known": True, "board": True, "battery": 80, "charging": False, "q": 60,
                              "lost": False, "low_battery": False, "docking": "off_dock"}
    snap["face"] = {"connected": True, "event_call": True, "ceremony_call": False}
    snap["mode"]["ceremony_app"] = False
    snap["mode"]["warnings"] = []
    return snap


def _performed(scene_id: str, phase: str, **more) -> dict:
    event_host.STATE["seq"] += 1
    event_host._perform(event_host.scene(scene_id), event_host.STATE["seq"], phase, **more)
    return _state()


def _ids() -> list[str]:
    return [s["id"] for s in json.loads(TRACKED.read_text(encoding="utf-8"))["scenes"]]


def test_the_snapshot_says_what_the_boxes_need(served):
    """The two fields the boxes read beside the old ones: the line as the wheels turn (the debut)
    and a scene with nothing to say where she stands (rest: `on_stand_th` "")."""
    scenes = {s["id"]: s for s in event_host.snapshot()["scenes"]}
    assert scenes["debut"]["say_on_walk"] is True and scenes["welcome"]["say_on_walk"] is False
    assert scenes["rest"]["quiet_standing"] is True
    # a scene with a line where she stands, or none of its own (the arrival line then): not quiet
    assert scenes["escort_multimedia"]["quiet_standing"] is False and scenes["welcome"]["quiet_standing"] is False
    # what start_scene says on a walk: rest's one line as it sets off, nothing at the dock (review 10 Nov)
    assert (scenes["rest"]["has_start"], scenes["rest"]["has_arrive"]) == (True, False)
    assert (scenes["welcome"]["has_start"], scenes["welcome"]["has_arrive"]) == (True, True)
    assert (scenes["debut"]["has_start"], scenes["debut"]["has_arrive"]) == (False, True)
    assert scenes["roam_lobby"]["roam_stops"] == ["reception", "lobby_east", "lobby_mid", "entrance", "lobby_west"]
    assert scenes["welcome"]["roam_stops"] == []


def test_quiet_standing_needs_somewhere_to_go(served):
    """scene_text reads `on_stand_th` only for a scene that goes somewhere (its `spot_id`): one that
    stays put says its start line however its `on_stand_th` is set (review 10 Nov, a made-up scene)."""
    doc = json.loads(served.read_text(encoding="utf-8"))
    doc["scenes"].append({"id": "stay", "title_th": "อยู่กับที่", "spot": None, "on_start_th": "ทักทายแขก",
                          "on_stand_th": ""})
    served.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    event_host.reset()
    stay = {s["id"]: s for s in event_host.snapshot()["scenes"]}["stay"]
    assert stay["quiet_standing"] is False
    assert event_host.scene_text(event_host.scene("stay"), walking=False, spot_id=None) is not None


def test_every_scene_is_a_numbered_box_in_the_files_order(chromium, served):  # noqa: F811
    ids = _ids()
    page = Page(chromium, _state(), html=EVENT_PAGE, path="/event-control")
    try:
        boxes = page.page.locator("#scenes > .scene")
        assert boxes.count() == len(ids) and page.page.locator("#acts").count() == 0
        for i, sid in enumerate(ids):
            box = "#scenes > .scene:nth-child(%d)" % (i + 1)
            assert page.page.locator(box).get_attribute("id") == "scene-" + sid
            assert page.text(box + " .num") == str(i + 1)
            assert page.page.locator(box + " button.go").count() == 1       # its own start button
        # nothing pressed yet: box 1 is next, nothing is "now"
        assert page.text("#scene-%s .tag" % ids[0]) == "ถัดไป"
        assert page.page.locator(".scene .tag").count() == 1 and page.page.locator(".scene.current").count() == 0

        # a box's start: the first tap sends nothing, the second sends that scene
        welcome = "#scene-welcome button.go"
        assert page.text(welcome) == "เริ่มรับแขกหน้างาน"
        page.page.click(welcome)
        assert page.sent == [] and page.text(welcome) == "แตะอีกครั้งเพื่อเริ่ม"
        assert page.text("#scene-farewell button.go") == "เริ่มส่งแขก"          # only that box armed
        page.page.wait_for_timeout(450)                    # a second, deliberate tap (TWO_TAP_MIN_MS)
        page.page.click(welcome)
        page.page.wait_for_function("document.getElementById('toast').classList.contains('show')")
        assert page.sent == [("/event/scene", {"scene": "welcome"})]

        # the scene now is marked, its button says "again", and the box after it is next
        event_host.STATE["scene"] = "welcome"
        page.redraw(_state())
        page.page.wait_for_function("document.querySelector('#scene-welcome.current') !== null")
        at = ids.index("welcome")
        assert page.text("#scene-welcome .tag") == "ฉากตอนนี้"
        assert page.text(welcome) == "เริ่มรับแขกหน้างาน อีกครั้ง"
        assert page.text("#scene-%s .tag" % ids[at + 1]) == "ถัดไป"
        assert page.page.locator(".scene .tag").count() == 2
        # the last scene now: nothing after it
        event_host.STATE["scene"] = ids[-1]
        page.redraw(_state())
        page.page.wait_for_function("document.querySelector('#scene-%s.current') !== null" % ids[-1])
        assert page.page.locator(".scene .tag.next").count() == 0
        assert page.page.evaluate("document.documentElement.scrollWidth") <= 400
    finally:
        page.close()


def test_the_debut_box_says_what_happens_and_takes_two_taps(chromium, served):  # noqa: F811
    page = Page(chromium, _state(), html=EVENT_PAGE, path="/event-control")
    try:
        box = page.text("#scene-debut")
        assert "quick" in page.page.locator("#scene-debut").get_attribute("class").split()
        # forward_m (owner 10 Nov "กดแล้วหุ่นไม่เห็นเดินไปด้านหน้าเลย"): the stage not recorded = a metre
        # ahead; out of the box (owner 23 Nov "หุ่นอยู่ในกล่อง ... เดินออกมาก่อน จึงค่อยยกมือ"): arms down on the way,
        # the wave and the line once out (the 10 Nov walk-and-wave is a scene without `walk_out_of_box`)
        assert "1. เดินออกจากกล่อง 1 ม. (แขนลง)" in box and PAGEANT not in box.split("Emma จะพูด:")[0]
        assert "2. แล้วค่อยยกมือโบก + พูด (ล้อหยุดก่อน) จบแล้วเอามือลง" in box
        assert "Emma จะพูด:" in box and SENTENCE in box
        # always straight ahead (review 23 Nov: a recorded stage was a MoveTo from inside the box) -- nobody is told
        # to record one for it
        assert ("เดินตรงออกจากกล่อง 1 ม. เสมอ (ไม่ใช้จุดที่บันทึก — ห้ามบันทึกจุดกลางเวที (ล็อบบี้)สำหรับฉากนี้) · ระยะเป็นค่าเดา: "
                "วัดกับความลึกกล่องจริงให้พาทั้งตัวและแขนพ้นขอบกล่อง · เดินไม่ครบระยะ = ไม่ออก (แขนยังล็อก)") in box
        assert "ยังไม่ได้บันทึกจุด" not in box
        assert "ออกจากกล่องไม่ได้ (ประตูกล่องเปิดไม่สุด มีของขวางหน้าหุ่น) = รอ 45 วิแล้วหยุด ไม่โบก ไม่พูดประโยค" in box
        assert "ตรงที่หุ่นยืน" not in box.split("Emma จะพูด:")[0]

        go = "#scene-debut button.go"
        page.page.click(go)
        assert page.sent == [] and page.text(go) == "แตะอีกครั้งเพื่อเริ่ม"
        page.page.wait_for_timeout(450)                    # a second, deliberate tap (TWO_TAP_MIN_MS)
        page.page.click(go)
        page.page.wait_for_function("document.querySelector('#scene-debut button.go').textContent === 'เริ่มเดินเปิดตัว'")
        page.page.wait_for_function("document.getElementById('toast').classList.contains('show')")   # answered
        assert page.sent == [("/event/scene", {"scene": "debut"})]
        page.redraw(_state(walk_on=False))                      # walking off
        page.page.wait_for_function("document.getElementById('scene-debut').innerText.indexOf('การเดินปิด') >= 0")
        box = page.text("#scene-debut")
        # still in the box: her line, no arm (the pageant's arm swings out sideways into it)
        assert "พูดตรงที่หุ่นยืน ไม่ยกมือ (ยังอยู่ในกล่อง)" in box and "ทำ%s" % PAGEANT not in box
        assert "การเดินปิด: เล่นฉากตรงที่หุ่นยืน (ปกติเดินตรงออกจากกล่อง 1 ม.)" in box
        page.redraw(_state())
        page.page.wait_for_function("document.getElementById('scene-debut').innerText.indexOf('เดินออกจากกล่อง 1 ม.') >= 0")

        # out of the box no spot is walked to: its box offers none to record (review 23 Nov)
        assert page.page.locator("#scene-debut button:not(.go)").count() == 0
        # the phone is no wider than a phone
        assert page.page.evaluate("document.documentElement.scrollWidth") <= 400
    finally:
        page.close()


def test_with_the_stage_recorded_the_walk_out_still_goes_straight_ahead(chromium, served):  # noqa: F811
    """Review 23 Nov: a recorded stage_centre made the walk out of the box the board's MoveTo from inside it -- the box
    says (and the server walks: event_host._walk_to) straight ahead, recorded or not."""
    _record_stage()
    page = Page(chromium, _state(), html=EVENT_PAGE, path="/event-control")
    try:
        box = page.text("#scene-debut")
        assert "1. เดินออกจากกล่อง 1 ม. (แขนลง)" in box and "2. แล้วค่อยยกมือโบก + พูด" in box
        assert "ไปกลางเวที" not in box.split("Emma จะพูด:")[0] and page.page.locator("#scene-debut button").count() == 1
        page.redraw(_state(walk_on=False))
        page.page.wait_for_function("document.getElementById('scene-debut').innerText.indexOf('การเดินปิด') >= 0")
        box = page.text("#scene-debut")
        assert "พูดตรงที่หุ่นยืน ไม่ยกมือ (ยังอยู่ในกล่อง)" in box and "(ปกติเดินตรงออกจากกล่อง 1 ม.)" in box
    finally:
        page.close()


def test_a_box_says_when_walking_is_off_and_when_she_will_not_speak(chromium, served):  # noqa: F811
    """10 Nov: with the walk switched off every card said "เดินไปที่ ..." while the scene was acted where
    she stood; and the rest scene, which says nothing where she stands, must not read "พูด"."""
    scenes = _state()["scenes"]
    page = Page(chromium, _state(walk_on=False), html=EVENT_PAGE, path="/event-control")
    try:
        cards = page.text("#scenes")
        assert cards.count("การเดินปิด: เล่นฉากตรงที่หุ่นยืน") == len(
            [s for s in scenes if s["spot"] or s["stops"] or s["roam"]])
        assert "(ปกติเดินไปกลางส่วนต้อนรับ)" in page.text("#scene-welcome")
        assert "(ปกติเดินไปทีละจุด 6 จุด)" in page.text("#scene-tables")
        rest = page.text("#scene-rest .plan")
        assert "อยู่กับที่ ไม่พูด" in rest and "พูด ตรงที่หุ่นยืน" not in rest
        assert "พูด ตรงที่หุ่นยืน" in page.text("#scene-escort_multimedia .plan")
        assert "พูด ตรงที่หุ่นยืน" in page.text("#scene-introduce .plan")        # no spot at all
        page.redraw(_state(walk_on=True))
        page.page.wait_for_function("document.getElementById('scenes').innerText.indexOf('การเดินปิด') < 0")
        # what start_scene says on the way (review 10 Nov): the start's line as she sets off, the
        # spot's at the spot -- and rest, silent at the dock, has no "ถึงแล้วพูด"
        welcome = page.text("#scene-welcome .plan")
        assert "1. พูดแล้วเดินไปกลางส่วนต้อนรับ" in welcome and "2. ถึงแล้วพูด" in welcome
        rest = page.text("#scene-rest .plan")
        assert "พูดแล้วเดินไปแท่นชาร์จ" in rest and "ถึงแล้ว" not in rest and "1." not in rest
        # the roam: its five stops are in the file, so it walks
        assert "1. พูดแล้วเดินวนเองทีละจุด (5 จุด)" in page.text("#scene-roam_lobby .plan")
        # the tables not recorded: acted where she stands, and the box says why
        assert "พูด ตรงที่หุ่นยืน" in page.text("#scene-tables .plan")
        assert "ยังไม่ได้บันทึกจุดแรก (โต๊ะ 1) — เล่นฉากตรงที่หุ่นยืน · บันทึกแล้ว 0 จาก 6 จุด" in page.text("#scene-tables")
        # what else a press does: on its own chip, not a step -- the screen, here
        assert "ปิดจอใหญ่" in page.text("#scene-after_ceremony .plan .also")
    finally:
        page.close()


def test_the_tables_walk_to_the_first_and_staff_press_the_rest(chromium, served):  # noqa: F811
    """Review 10 Nov: "เดินไปทีละจุด 6 จุด" read as if she toured them by herself -- the press walks to
    the first stop; each next one is "ไปจุดถัดไป". The first not recorded = acted where she stands."""
    event_host.spots_path().write_text(json.dumps({"spots": {"table_1": {
        "pose": [-9.0, -3.0, 0.0], "recorded_at": "2026-11-24 18:00"}}}), encoding="utf-8")
    page = Page(chromium, _state(), html=EVENT_PAGE, path="/event-control")
    try:
        plan = page.text("#scene-tables .plan")
        assert "1. พูดแล้วเดินไปโต๊ะ 1" in plan and "2. ถึงแล้วพูด" in plan
        assert '3. กด "ไปจุดถัดไป" ทีละจุด (ทั้งหมด 6 จุด)' in plan
        assert "บันทึกตำแหน่งแล้ว 1 จาก 6 จุด — จุดที่ยังไม่บันทึก เล่นตรงที่หุ่นยืน" in page.text("#scene-tables")
        # another table recorded but not the first: the press stands at table 1
        event_host.spots_path().write_text(json.dumps({"spots": {"table_2": {
            "pose": [-9.0, -3.0, 0.0], "recorded_at": "2026-11-24 18:00"}}}), encoding="utf-8")
        page.redraw(_state())
        page.page.wait_for_function("document.getElementById('scene-tables').innerText.indexOf('จุดแรก') >= 0")
        assert "พูด ตรงที่หุ่นยืน" in page.text("#scene-tables .plan")
        assert "ยังไม่ได้บันทึกจุดแรก (โต๊ะ 1) — เล่นฉากตรงที่หุ่นยืน · บันทึกแล้ว 1 จาก 6 จุด" in page.text("#scene-tables")
    finally:
        page.close()


def test_a_roam_over_unrecorded_stops_says_it_will_stand(chromium, served):  # noqa: F811
    """event_roam.start refuses under two recorded stops (`no_stops`): the box must not promise a
    roam (review 10 Nov, a made-up roam over the unrecorded tables)."""
    doc = json.loads(served.read_text(encoding="utf-8"))
    roam = next(s for s in doc["scenes"] if s["id"] == "roam_lobby")
    roam["roam"]["stops"] = ["table_1", "table_2", "table_3"]
    served.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    event_host.reset()
    page = Page(chromium, _state(), html=EVENT_PAGE, path="/event-control")
    try:
        box = page.text("#scene-roam_lobby")
        assert "เดินวนเองทีละจุด" not in box and "พูด ตรงที่หุ่นยืน" in page.text("#scene-roam_lobby .plan")
        assert "บันทึกจุดเดินวนแล้ว 0 จาก 3 จุด — ต้องมีอย่างน้อย 2 จุด ไม่งั้นเล่นฉากตรงที่หุ่นยืน" in box
    finally:
        page.close()


def test_the_walking_up_chip_says_what_this_robot_does(chromium, served):  # noqa: F811
    """The approach as this robot has it (robot.env: FACE_APPROACH_ANYONE, DRY_RUN) -- and nothing
    where it is off (FACE_APPROACH unset: the scene's `approach` does nothing)."""
    state = _state()
    state["approach"] = dict(state.get("approach") or {}, available=False)
    page = Page(chromium, state, html=EVENT_PAGE, path="/event-control")
    try:
        assert "เดินเข้าทัก" not in page.text("#scene-welcome .plan")
        state = _state()
        state["approach"] = dict(state.get("approach") or {}, available=True, anyone=True, dry_run=True)
        page.redraw(state)
        page.page.wait_for_function("document.getElementById('scene-welcome').innerText.indexOf('เดินเข้าทัก') >= 0")
        assert "เปิดเดินเข้าทักคนที่เห็น (ซ้อม: ไม่เดินจริง)" in page.text("#scene-welcome .plan .also")
        state = _state()
        state["approach"] = dict(state.get("approach") or {}, available=True, anyone=False, dry_run=False)
        page.redraw(state)
        page.page.wait_for_function("document.getElementById('scene-welcome').innerText.indexOf('ซ้อม') < 0")
        assert "เปิดเดินเข้าทักแขกที่จำได้" in page.text("#scene-welcome .plan .also")
        # the press's refusal of it is said, not swallowed
        page.posts["/event/scene"] = {"ok": True, "scene": "welcome", "walk": None, "roam": None, "speaks": True,
                                      "approach": {"ok": False, "error": "no_area", "error_th": "ยังไม่ได้บันทึกมุมพื้นที่"}}
        page.page.click("#scene-welcome button.go")
        page.page.wait_for_timeout(450)                    # a second, deliberate tap (TWO_TAP_MIN_MS)
        page.page.click("#scene-welcome button.go")
        assert "ไม่ได้เปิดเดินเข้าทัก: ยังไม่ได้บันทึกมุมพื้นที่" in page.toast()
    finally:
        page.close()


def test_a_re_armed_start_keeps_its_four_seconds(chromium, served):  # noqa: F811
    """Review 10 Nov: the first tap's timer dropped any later arming of the same button."""
    page = Page(chromium, _state(), html=EVENT_PAGE, path="/event-control")
    try:
        go = "#scene-welcome button.go"
        page.page.click(go)                                   # armed at t0: its timer fires at t0+4
        page.page.wait_for_timeout(2000)
        page.page.click(go)                                   # sent at ~t0+2; that timer still runs
        page.page.wait_for_function("document.getElementById('toast').classList.contains('show')")
        page.page.click(go)                                   # armed again at t3 >= t0+2
        assert page.text(go) == "แตะอีกครั้งเพื่อเริ่ม"
        page.page.wait_for_timeout(2500)                       # t3+2.5: past t0+4, before t3+4
        assert page.text(go) == "แตะอีกครั้งเพื่อเริ่ม"
        assert page.sent == [("/event/scene", {"scene": "welcome"})]
    finally:
        page.close()


def test_the_debut_save_and_the_setup_list_are_two_buttons(chromium, served):  # noqa: F811
    """The debut walks out of the box: its box has no save of its own any more (review 23 Nov) -- the setup list's row
    of the stage is the only one, and arming it arms nothing in the debut's box."""
    page = Page(chromium, _state(), html=EVENT_PAGE, path="/event-control")
    try:
        assert page.page.locator("#scene-debut button:not(.go)").count() == 0
        row = page.page.locator("#spots .spot", has_text="กลางเวที").locator("button", has_text="บันทึกตำแหน่งตอนนี้")
        row.dispatch_event("click")                        # (in the folded setup list)
        assert "แตะอีกครั้ง" in page.page.evaluate("document.getElementById('spots').textContent")
        assert "แตะอีกครั้ง" not in page.text("#scene-debut")
        assert page.sent == []
    finally:
        page.close()


def test_the_scene_now_with_stops_has_its_stops_in_its_box(chromium, served):  # noqa: F811
    event_host.STATE["scene"] = "tables"
    page = Page(chromium, _state(), html=EVENT_PAGE, path="/event-control")
    try:
        assert page.page.locator("#scene-tables .stops button").count() == 6
        assert page.page.locator(".stops").count() == 1                        # only the scene now's
        nxt = page.page.locator("#scene-tables button.primary", has_text="ไปจุดถัดไป")
        assert nxt.count() == 1
        nxt.click()                                                            # one tap, as it always was
        page.page.wait_for_function("document.getElementById('toast').classList.contains('show')")
        assert page.sent == [("/event/next", {})]
    finally:
        page.close()


def test_what_came_of_the_press_is_in_its_box(chromium, served):  # noqa: F811
    page = Page(chromium, _performed("debut", "walking", said=None, gesture_ok=None, gesture_error_th=None),
                html=EVENT_PAGE, path="/event-control")
    try:
        result = "#scene-result-debut"
        assert page.text(result) == "กำลังเดินไป — ถึงแล้วจะโบกมือทักทายและพูด"
        # in its box, not twice (`hidden` itself: an empty line has no size and "is not visible")
        assert page.page.evaluate("document.getElementById('performLine').hidden") is True
        page.redraw(_performed("debut", "done", said=True, gesture_ok=True, gesture_error_th=None))
        page.page.wait_for_function("document.getElementById('scene-result-debut').innerText.indexOf('รับคำสั่ง') >= 0")
        assert page.text(result).startswith("ส่งให้ Emma พูดแล้ว · หุ่นรับคำสั่งโบกมือทักทายแล้ว")
        assert "bad" not in page.page.locator(result).get_attribute("class")
        page.redraw(_performed("debut", "done", said=True, gesture_ok=False, gesture_error_th="หุ่นล็อกแขนอยู่"))
        page.page.wait_for_function("document.getElementById('scene-result-debut').innerText.indexOf('ไม่ได้') >= 0")
        assert "โบกมือทักทายไม่ได้: หุ่นล็อกแขนอยู่" in page.text(result)
        assert "bad" in page.page.locator(result).get_attribute("class")
        page.redraw(_performed("debut", "done", said=False))
        page.page.wait_for_function("document.getElementById('scene-result-debut').innerText.indexOf('ไม่ได้พูด') >= 0")
        assert "Emma ไม่ได้พูด" in page.text(result)
        page.redraw(_performed("debut", "cut", said=False))
        page.page.wait_for_function("document.getElementById('scene-result-debut').innerText.indexOf('เดินไม่ถึง') >= 0")
        page.redraw(_performed("debut", "stopped", said=False))
        page.page.wait_for_function("document.getElementById('scene-result-debut').innerText.indexOf('กดหยุดแล้ว') >= 0")
        assert "bad" in page.page.locator(result).get_attribute("class")
        page.redraw(_performed("debut", "settling", said=None))
        page.page.wait_for_function("document.getElementById('scene-result-debut').innerText.indexOf('รอหุ่นนิ่ง') >= 0")
        # the debut's hand before her line (owner 11 Nov "มือจะขึ้นช้ากว่าเสียง"): the phone says what it waits for
        page.redraw(_performed("debut", "raising", said=None, gesture="pageant"))
        page.page.wait_for_function("document.getElementById('scene-result-debut').innerText.indexOf('ยกมือ') >= 0")
        assert page.text(result) == "สั่งยกมือ (ท่านางงาม (โบกมือ หันซ้าย-ขวา)) — Emma จะพูดหลังมือขึ้น…"
        page.redraw(_performed("debut", "saying", said=None, gesture="pageant"))
        page.page.wait_for_function("document.getElementById('scene-result-debut').innerText.indexOf('รับคำสั่ง') >= 0")
        assert page.text(result) == "กำลังส่งประโยคให้ Emma — หุ่นรับคำสั่งท่านางงาม (โบกมือ หันซ้าย-ขวา)แล้ว…"
        assert "bad" not in page.page.locator(result).get_attribute("class")
        # another scene's answer: in that scene's box, and the debut's goes
        page.redraw(_performed("welcome", "done", said=True))
        page.page.wait_for_function("!document.getElementById('scene-result-welcome').hidden")
        assert page.text("#scene-result-welcome").startswith("ส่งให้ Emma พูดแล้ว")
        assert not page.visible(result)
        assert page.page.evaluate("document.getElementById('performLine').hidden") is True
        # a scene the list does not have (the file changed under it): on the status card
        gone = _state()
        gone["perform"] = dict(gone["perform"], scene="old_scene", title_th="ฉากเก่า")
        page.redraw(gone)
        page.page.wait_for_function("!document.getElementById('performLine').hidden")
        assert page.text("#performLine").startswith("ฉากเก่า: ส่งให้ Emma พูดแล้ว")
    finally:
        page.close()
