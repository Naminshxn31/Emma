"""The ceremony staff page's running order, in a real browser (review 7 Nov; moved 9 Nov).

Owner 9 Nov 2026: "อันนี้ทำแยกกับอันเดิมนะ ไม่งั้นจะปนกัน อันนี้จะเป็นสำหรับพิธี" -- the ceremony is its
own robot app, Emma Ceremony, pressed from its own page: client/ceremony-control.html (GET
/ceremony/state, POST /ceremony/order/*, /ceremony/cue; the red stop stays POST /event/stop for
both pages). The running order and the cue list moved there from event-control.html whole, and
these tests moved with them.

The page is driven in headless Chromium with the network faked by Playwright's router, from
states built here (`_state`, `_order`, `_step`, `_mark`) in exactly the shape the real server
answers: test_the_states_the_page_is_driven_with_are_the_servers_own reads the REAL
/ceremony/state (app/event_control_server.py + app/event_order.py, over our own script) and
holds every key of those builders to it -- a renamed field goes red there first. What these
hold down:

* her live window: the step says she is listening, "ถัดไป" says it closes it ("จบช่องตอบสด ✓"),
  and its own end is counted down from the server's clock (`server_time`, `order.live.until`);
* a film is running until the big screen plays it, and a failed one says why on the step;
* a press that got no answer (Wi-Fi, a hung POST cut at POST_TIMEOUT_MS) says "ไม่รู้ผล", never
  "ไม่ได้", and pressing that step again takes a second, deliberate tap (B6);
* one press at a time: a double tap on "ถัดไป" is one press; a step out of its turn takes two
  taps; "ข้ามขั้นนี้" only on the step "ถัดไป" stands at;
* a warning on a step that can still be pressed is shown at full strength;
* a finished order says so with the reset one tap away, and the way to the event page (B8); a
  rehearsal's cleared progress is said; the red stop says what it reached;
* the ceremony app not on the robot (`mode.ceremony_app` false) is said once, at the top;
* the new kinds: the big screen (`screen`) and the hand-over to Emma Event (`handoff`).

Without Playwright/Chromium the browser tests skip and say what is NOT being checked; the
source-level checks at the end run everywhere.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

from app import event_clips, event_cues, event_host, event_order, event_script, event_walk
from app.config import settings

ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "client" / "ceremony-control.html").read_text(encoding="utf-8")
EVENT = ROOT / "tests" / "data" / "floor1_event_rehearsed.json"
BASE = "http://event.test"
NOT_CEREMONY_TH = "เปิดแอป Emma Ceremony บนหุ่นก่อน (ตอนนี้กดได้แค่ขั้นทำเอง)"


def _js(start: str, end: str = "\n  }\n") -> str:
    body = PAGE[PAGE.index(start):]
    return body[:body.index(end) + len(end)]


# ---- the states the page is driven with ---------------------------------------------------------

def _mark(state: str, *, error_th=None, at=None, live=False, film=False) -> dict:
    return {"state": state, "error_th": error_th, "at": time.time() - 60 if at is None else at,
            "live": live, "film": film}


def _step(i: int, do: str, label_th: str, *, ready=True, why_th=None, line_th=None, show_th=None,
          voice=None, note_th=None, mark=None) -> dict:
    return {"i": i, "do": do, "label_th": label_th, "note_th": note_th, "line_th": line_th,
            "show_th": show_th, "voice": voice, "ready": ready, "why_th": why_th, "mark": mark}


def _order(scenes: list, *, last=None, next=None, running=None, finished=False, live=None,  # noqa: A002
           cleared_th=None) -> dict:
    return {"scenes": scenes, "last": last, "next": next, "running": running, "finished": finished,
            "live": live, "cleared_th": cleared_th}


GREET = "สวัสดีค่ะคุณมิกิ ฉันชื่อเอ็มม่า ยินดีต้อนรับทุกท่านสู่พิธีเปิดโครงการของเราค่ะ"


def _scenes(**marks) -> list:
    """The ceremony's nine scenes, as the server lists them. `marks` = {"n_i": mark}."""
    def m(n, i):
        return marks.get("%d_%d" % (n, i))

    scenes = [
        {"n": 1, "title_th": "เปิดพิธี", "steps": [
            _step(0, "walk", "เดินออกมากลางเวที", ready=False, why_th="การเดินปิดอยู่ (EVENT_WALK_ENABLED)",
                  mark=m(1, 0)),
            _step(1, "screen", "เปิดจอใหญ่", mark=m(1, 1)),
            _step(2, "cue", "Emma: «สวัสดีค่ะคุณมิกิ ฉันชื่อเอ็มม่า…»", line_th=GREET, voice="recorded",
                  mark=m(1, 2)),
            _step(3, "body", "ท่า: โบกมือ", mark=m(1, 3)),
        ]},
        {"n": 2, "title_th": "ทำเล", "steps": [
            _step(0, "cue", "Emma: «นี่คือที่ตั้งของโครงการค่ะ»", line_th="ได้เลยค่ะ นี่คือที่ตั้งของโครงการค่ะ",
                  show_th="ภาพ: location_beach (แผนที่ทำเล)", voice="read", mark=m(2, 0)),
            _step(1, "clip", "คลิป: วิดีโอเทคโนโลยี (คุณเจน)", mark=m(2, 1)),
            _step(2, "manual", "เปิดไฟล็อบบี้", note_th="ยังไม่ได้ต่อกับระบบไฟ", mark=m(2, 2)),
            _step(3, "cue", "Emma ตอบสด: s4_recommend", voice="live", mark=m(2, 3)),
            _step(4, "cue", "Emma: «ขอขอบคุณทุกคนค่ะ»", line_th="ขอขอบคุณทุกคนค่ะ", voice="read",
                  ready=False, why_th="ยังไม่อนุมัติ", mark=m(2, 4)),
        ]},
    ]
    for n, title in ((3, "จำนวนยูนิต"), (4, "วิวทะเล"), (5, "ความสูงอาคาร"), (6, "เทคโนโลยี"),
                     (7, "ไฟและม่าน"), (8, "เรื่องเล่า")):
        scenes.append({"n": n, "title_th": title, "steps": [
            _step(0, "cue", "Emma: ฉาก %d" % n, line_th="บทของฉาก %d ค่ะ" % n, voice="read", mark=m(n, 0))]})
    scenes.append({"n": 9, "title_th": "ส่งต่อหลังพิธี", "steps": [
        _step(0, "screen", "ปิดจอใหญ่", mark=m(9, 0)),
        _step(1, "handoff", "เปิดแอป Emma Event", note_th="จบพิธี: หุ่นเปิดแอปงาน ฉากงานกดต่อที่หน้าคุมงาน",
              mark=m(9, 1)),
    ]})
    return scenes


def _state(order, *, ceremony_app=True, call=True, cues=None, walk=None, live=None, clips=None) -> dict:
    """/ceremony/state as the server builds it: the shared pieces, then the cues and the order."""
    now = time.time()
    return {
        "ok": True, "event": {"name_th": "เปิดตัว Embassy World", "dates": ["2026-11-25", "2026-11-26"]},
        "walk": walk or {"enabled": True, "run": None,
                         "health": {"known": True, "board": True, "battery": 82, "charging": False, "q": 78,
                                    "lost": False, "low_battery": False, "docking": "on_dock"}},
        "face": {"connected": True, "event_call": False},
        "mode": {"ceremony": ceremony_app, "ceremony_app": ceremony_app, "needs_name": False,
                 "cue_only": ceremony_app, "live": live, "tv": None, "warnings": [], "roam": None},
        "clips": clips, "screen": {"offered": True, "open": False, "name": "multimedia"}, "spots": [],
        "server_time": now,
        "cues": {"cues": cues or [], "call": call, "host_th": "คุณมิกิ"},
        "order": order,
    }


# ---- the real server: the shapes above are its own -----------------------------------------------

def _signed(raw: dict) -> dict:
    c = event_script._normalise(raw)
    return dict(raw, approved_by="test", approved_at="2026-11-09 10:00", approved_sha256=event_script.digest(c))


#: Our own small script (never the tracked one): every kind the ceremony's order has.
SCRIPT = {"format": event_script.FORMAT, "host_th": "คุณมิกิ", "replies": "cues", "cues": [
    _signed({"id": "s1_greet", "scene": 1, "th": "สวัสดีค่ะคุณมิกิ ฉันชื่อเอ็มม่าค่ะ", "numbers": []}),
    _signed({"id": "s4_recommend", "scene": 4, "th": None, "live_th": "แนะนำห้องวิวทะเล", "numbers": []}),
], "order": [
    {"n": 1, "title_th": "เปิด", "steps": [
        {"do": "walk", "spot": "stage_centre", "label_th": "เดินออกมากลางเวที"},
        {"do": "screen", "screen": "open", "label_th": "เปิดจอใหญ่"},
        {"do": "cue", "cue": "s1_greet"},
        {"do": "manual", "label_th": "เปิดไฟเวที", "note_th": "ยังไม่ได้ต่อ"},
    ]},
    {"n": 2, "title_th": "ห้อง", "steps": [
        {"do": "clip", "clip": "tech_presentation"},
        {"do": "cue", "cue": "s4_recommend"},
        {"do": "handoff", "to": "event", "label_th": "เปิดแอป Emma Event", "note_th": "จบพิธี"},
    ]},
]}


@pytest.fixture
def real(tmp_path, monkeypatch):
    """The real staff server over SCRIPT, the ceremony app on, a live call, walking off."""
    from fastapi.testclient import TestClient

    from app import ceremony, event_control_server, room_screens

    target = tmp_path / "floor1_event.json"
    target.write_text(EVENT.read_text(encoding="utf-8"), encoding="utf-8")
    script = tmp_path / "floor1_ceremony.json"
    script.write_text(json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "event_file", str(target))
    monkeypatch.setattr(settings, "ceremony_file", str(script))
    monkeypatch.setattr(settings, "event_control_key", "staff-secret")
    monkeypatch.setattr(event_walk, "RUN_DIR", tmp_path / ".run")
    monkeypatch.setattr(event_cues, "_live_call", lambda: object())
    monkeypatch.setattr(event_clips, "table", lambda: {"tech_presentation": {
        "id": "tech_presentation", "title_th": "วิดีโอเทคโนโลยี (คุณเจน)", "has_file": True, "path": None,
        "seconds": None, "after_th": None}})
    monkeypatch.setattr(room_screens, "offered", lambda: True)
    monkeypatch.setattr(ceremony, "on", lambda: True)       # the Emma Ceremony app's call is the live one
    mods = (event_host, event_script, event_cues, event_order, ceremony)
    for mod in mods:
        mod.reset()
    client = TestClient(event_control_server.app)
    client.get("/ceremony-control?key=staff-secret")
    yield client
    client.close()
    for mod in mods:
        mod.reset()


def test_the_states_the_page_is_driven_with_are_the_servers_own(real, monkeypatch):
    """Every key the builders above give the page is a key the real /ceremony/state has --
    and no more: a field renamed on either side goes red here, not at the ceremony."""
    from app import ceremony, robot_face_link

    doc = real.get("/ceremony/state").json()
    assert isinstance(doc["server_time"], float) and doc["mode"]["ceremony_app"] is True
    built = _state(_order([]))
    assert set(built) <= set(doc), set(built) - set(doc)
    assert set(built["mode"]) <= set(doc["mode"]) and set(built["cues"]) <= set(doc["cues"])
    order = doc["order"]
    assert set(order) == set(_order([])), set(order) ^ set(_order([]))
    assert order["finished"] is False and order["live"] is None and order["cleared_th"] is None
    for sc in order["scenes"]:
        assert set(sc) == {"n", "title_th", "steps"}
        for st in sc["steps"]:
            # a screen step also says which way (`screen`); the builders above leave it out on
            # purpose to drive the page's fallback hint, and test it given further down
            extra = {"screen"} if st["do"] == "screen" else set()
            assert set(st) == set(_step(0, "manual", "x")) | extra, (sc["n"], st)
            if extra:
                assert st["screen"] in ("open", "close")
    kinds = [st["do"] for sc in order["scenes"] for st in sc["steps"]]
    assert kinds == ["walk", "screen", "cue", "manual", "clip", "cue", "handoff"]
    # a tick: its mark, in the mark's shape
    assert real.post("/ceremony/order/step", json={"n": 1, "i": 3}).json()["ok"]
    mark = real.get("/ceremony/state").json()["order"]["scenes"][0]["steps"][3]["mark"]
    assert set(mark) == set(_mark("done")) and mark["state"] == "done"
    # her live window: its step, `order.live`, and "ถัดไป" closes it
    event_order.STATE["last"] = (2, 0)
    opened = real.post("/ceremony/order/next", json={"n": 2, "i": 1}).json()
    assert opened["ok"] and opened["result"]["live"] == "open", opened
    live = real.get("/ceremony/state").json()
    assert set(live["order"]["live"]) == {"n", "i", "until"} and live["order"]["next"] == [2, 1]
    assert live["order"]["live"]["until"] > live["server_time"]
    step = live["order"]["scenes"][1]["steps"][1]
    assert step["voice"] == "live" and step["mark"]["state"] == "running" and step["mark"]["live"] is True

    async def request_stop(first, kind, timeout):
        return {"accepted": True, "reason": None, "via": "call"}

    monkeypatch.setattr(robot_face_link, "request_stop", request_stop)
    stop = real.post("/event/stop", json={}).json()           # one red stop for both pages
    assert {"ok", "stopped", "body", "line_cut", "live_closed"} <= set(stop) and stop["live_closed"] is True
    # the Event page's state carries neither: they are this page's alone
    assert not {"cues", "order"} & set(real.get("/event/state").json())
    # another app on the robot: said in `mode`, and only a tick can be pressed
    monkeypatch.setattr(ceremony, "on", lambda: False)
    off = real.get("/ceremony/state").json()
    assert off["mode"]["ceremony_app"] is False
    ready = [st["do"] for sc in off["order"]["scenes"] for st in sc["steps"] if st["ready"]]
    assert set(ready) <= {"manual"}, ready


# ---- the page in a browser -----------------------------------------------------------------------

@pytest.fixture(scope="module")
def chromium():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright is not installed -- the ceremony page's live window, film, unknown-result, "
                    "double tap, banners and finished states are NOT being checked in a browser")
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True)
    except Exception as exc:                             # noqa: BLE001 -- no Chromium for playwright
        pw.stop()
        pytest.skip("headless Chromium is not installed (%s) -- the ceremony page's states are NOT being "
                    "checked in a browser" % str(exc).splitlines()[0])
    yield browser
    browser.close()
    pw.stop()


#: 90 s is the real POST cut; the page under test gets it at 0.3 s (init script below).
SHORT_POST = """(() => { const st = window.setTimeout;
  window.setTimeout = (f, ms, ...a) => st(f, ms === 90000 ? 300 : ms, ...a); })();"""


#: The ceremony page's way of pressing, as this phone keeps it (owner 14 Nov "ทำแบบ manual": "manual"
#: is the page's default). The tests above the manual section were written for "order" -- the big
#: "ถัดไป" and a tap on the row -- and pin it; `mode=None` = the page's own default.
MODE_KEY = "emma.ceremony.press"


class Page:
    """The real page; /ceremony/state answers `state`; POSTs answer by `posts[path]`: a dict
    (JSON), "abort" (the network dropped), "hang" (no answer ever)."""

    def __init__(self, browser, state: dict, width: int = 400, html: str = PAGE, path: str = "/ceremony-control",
                 mode: str | None = "order"):
        self.state, self.posts, self.sent, self.gets = state, {}, [], []
        self.html, self.path = html, path
        self.ctx = browser.new_context(viewport={"width": width, "height": 860}, device_scale_factor=2)
        self.page = self.ctx.new_page()
        self.page.set_default_timeout(4000)                # a missing element fails here, not at pytest's 20 s
        self.page.add_init_script(SHORT_POST)
        if mode is not None:
            self.page.add_init_script("try { localStorage.setItem(%s, %s); } catch (e) {}"
                                      % (json.dumps(MODE_KEY), json.dumps(mode)))
        self.page.route(BASE + "/**", self._route)
        self.page.goto(BASE + path)
        self.page.wait_for_function("document.getElementById('lampServer').classList.contains('on')")
        self.page.wait_for_timeout(250)

    def _route(self, route):
        req = route.request
        path = req.url[len(BASE):].split("?")[0]
        if path == self.path:
            return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=self.html)
        if req.method == "GET":
            self.gets.append(path)
            if path in ("/ceremony/state", "/event/state"):
                return route.fulfill(status=200, content_type="application/json", body=json.dumps(self.state))
            return route.fulfill(status=404, body="")
        self.sent.append((path, json.loads(req.post_data or "{}")))
        answer = self.posts.get(path, {"ok": True})
        if answer == "abort":
            return route.abort("failed")
        if answer == "hang":
            return None                                  # never answered: the page's own cut must end it
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(answer))

    def text(self, selector: str) -> str:
        return self.page.locator(selector).first.inner_text()

    def visible(self, selector: str) -> bool:
        return self.page.locator(selector).first.is_visible()

    def toast(self) -> str:
        self.page.wait_for_function("document.getElementById('toast').classList.contains('show')")
        return self.text("#toast")

    def clear_toast(self):
        """The last press's toast stays up 3.2 s: gone before the next one is read."""
        self.page.evaluate("document.getElementById('toast').classList.remove('show')")

    def redraw(self, state: dict):
        self.state = state
        self.page.wait_for_timeout(1300)                   # the next poll (every second)

    def close(self):
        self.ctx.close()


def _live_state():
    """Her window open on 2.3 ("ถัดไป" stands there), closing by itself in 2:30."""
    now = time.time()
    order = _order(_scenes(**{"2_3": _mark("running", live=True)}), last=[2, 2], next=[2, 3],
                   running=[2, 3], live={"n": 2, "i": 3, "until": now + 150})
    doc = _state(order, live={"cue": "s4_recommend", "opened_at": now, "until": now + 150, "why": None})
    doc["server_time"] = now
    return doc


def test_her_live_window_on_the_page(chromium):
    page = Page(chromium, _live_state())
    try:
        top = page.text("#nextTop")
        assert page.text("#nextLabel") == "จบช่องตอบสด ✓" and "กดเมื่อ Emma ตอบจบ" in top
        assert re.search(r"ปิดเองใน 2:(29|30)", top), top
        assert "is-live" in page.page.locator("#nextBtn").get_attribute("class")
        row = page.text("#step-2-3")
        assert "Emma กำลังฟังคุณมิกิ" in row and "แตะ = ปิดช่องตอบสด" in row
        assert "ช่องตอบสดเปิดอยู่" in page.text("#modeSub")
        page.posts["/ceremony/order/next"] = {"ok": True, "n": 2, "i": 3, "do": "cue",
                                              "result": {"ok": True, "live": "closed"}}
        page.page.click("#nextBtn")
        assert "ปิดช่องตอบสดแล้ว" in page.toast()
        assert page.sent[-1] == ("/ceremony/order/next", {"n": 2, "i": 3})      # the step on its face
    finally:
        page.close()


def test_a_film_waiting_for_the_big_screen_and_one_that_failed(chromium):
    order = _order(_scenes(**{"2_1": _mark("running", film=True)}), last=[2, 0], next=[2, 2], running=[2, 1])
    page = Page(chromium, _state(order))
    try:
        assert "รอจอใหญ่เริ่มเล่น…" in page.text("#step-2-1")
        why = "จอใหญ่ไม่ตอบรับคำสั่งเล่น"
        page.redraw(_state(_order(_scenes(**{"2_1": _mark("failed", error_th=why)}), last=[2, 0], next=[2, 2])))
        page.page.wait_for_function("document.getElementById('step-2-1').innerText.indexOf('ไม่สำเร็จ') >= 0")
        assert "ไม่สำเร็จ: " + why in page.text("#step-2-1")
    finally:
        page.close()


def _at(n, i, **marks):
    """The order with "ถัดไป" at n.i, everything before it done."""
    done = {}
    for sc in _scenes():
        for st in sc["steps"]:
            if (sc["n"], st["i"]) < (n, i):
                done["%d_%d" % (sc["n"], st["i"])] = _mark("skipped" if not st["ready"] else "done")
    done.update(marks)
    flat = [(sc["n"], st["i"]) for sc in _scenes() for st in sc["steps"]]
    before = [p for p in flat if p < (n, i)]
    return _order(_scenes(**done), last=list(before[-1]) if before else None, next=[n, i])


@pytest.mark.parametrize("how", ["abort", "hang"])
def test_a_press_with_no_answer_is_unknown_and_its_step_takes_a_second_tap(chromium, how):
    """B6: a hung POST held every control until a reload, and a dropped one said "ไม่ได้: ติดต่อ
    เซิร์ฟเวอร์ไม่ได้" though the server may have run it -- inviting a second line."""
    page = Page(chromium, _state(_at(1, 2)))              # "ถัดไป" = 1.2, her greeting (ready)
    try:
        page.posts["/ceremony/order/next"] = how
        page.page.click("#nextBtn")
        toast = page.toast()
        assert "ไม่รู้ผล — ดูสถานะขั้นนี้ก่อนกดซ้ำ" in toast and "ไม่ได้" not in toast
        assert len(page.sent) == 1
        page.page.wait_for_timeout(1400)                   # past the order's gap after an answer
        assert "แตะสองครั้ง" in page.text("#nextTop") and "แตะสองครั้ง" in page.text("#step-1-2")
        assert page.page.locator("#nextBtn").is_enabled()  # the hung one is over: nothing is held
        page.page.click("#nextBtn")                        # the same step again: armed, not sent
        page.page.wait_for_timeout(100)
        assert len(page.sent) == 1 and "แตะอีกครั้งเพื่อยืนยัน" in page.text("#nextTop")
        assert "กดครั้งก่อนไม่รู้ผล" in page.text("#step-1-2")
        page.page.wait_for_timeout(500)
        page.posts["/ceremony/order/next"] = {"ok": True, "n": 1, "i": 2, "do": "cue",
                                              "result": {"ok": True, "mode": "recorded"}}
        page.page.click("#nextBtn")
        page.page.wait_for_timeout(300)
        assert len(page.sent) == 2 and page.sent[-1] == ("/ceremony/order/next", {"n": 1, "i": 2})
    finally:
        page.close()


def test_one_press_at_a_time_and_a_step_out_of_turn_takes_two_taps(chromium):
    page = Page(chromium, _state(_at(1, 2)))
    try:
        page.page.dblclick("#nextBtn")                     # a double tap is one line, never two
        page.page.wait_for_timeout(300)
        assert page.sent == [("/ceremony/order/next", {"n": 1, "i": 2})]
        page.page.wait_for_timeout(1400)                   # the gap after the answer
        page.page.click("#step-2-0 .slabel")               # ahead of its turn: armed, nothing sent
        page.page.wait_for_timeout(100)
        assert len(page.sent) == 1 and "แตะอีกครั้งเพื่อพูดบทนี้ (นอกลำดับ)" in page.text("#step-2-0")
        page.page.wait_for_timeout(500)
        page.page.click("#step-2-0 .slabel")
        page.page.wait_for_timeout(300)
        assert page.sent[-1] == ("/ceremony/order/step", {"n": 2, "i": 0})
    finally:
        page.close()


def test_skip_is_offered_on_the_next_step_only_and_a_ready_steps_warning_is_kept(chromium):
    scenes = _scenes()
    scenes[0]["steps"][2]["why_th"] = "ภาพไม่ขึ้น: เครื่องนี้ยังขึ้นภาพพร้อมบทไม่ได้"     # ready, with a warning
    order = _order(scenes, last=[1, 1], next=[1, 2])
    page = Page(chromium, _state(order))
    try:
        assert page.page.locator("#step-1-2 .skip").count() == 1
        assert page.page.locator(".skip").count() == 1                      # nowhere else
        warn = page.page.locator("#step-1-2 .swarn")
        assert warn.is_visible() and "ภาพไม่ขึ้น" in warn.inner_text()
        assert float(warn.evaluate("e => getComputedStyle(e).opacity")) == 1.0
        assert "unready" not in page.page.locator("#step-1-2").get_attribute("class")
        assert "มีคำเตือน" in page.text("#nextTop")
        page.page.click("#step-1-2 .skip")
        page.page.wait_for_timeout(300)
        assert page.sent == [("/ceremony/order/skip", {"n": 1, "i": 2})]
    finally:
        page.close()


def test_nine_scene_cards_and_next_above_the_red_stop(chromium):
    page = Page(chromium, _state(_at(1, 1)), width=360)
    try:
        titles = page.page.locator(".ocard .otitle").all_inner_texts()
        assert [t.split(" · ")[0] for t in titles] == ["ฉาก %d" % n for n in range(1, 10)]
        nxt = page.page.locator("#nextBtn").bounding_box()
        stop = page.page.locator("#stopBtn").bounding_box()
        assert nxt["y"] + nxt["height"] <= stop["y"]                         # "ถัดไป" above, STOP at the edge
        assert stop["height"] >= 72 and stop["width"] >= 360 - 2 * 16 - 1
        assert stop["y"] + stop["height"] >= 860 - 12
        assert page.page.evaluate("document.documentElement.scrollWidth") <= 360        # no sideways scroll
    finally:
        page.close()


def test_a_finished_order_says_so_with_the_reset_and_the_event_page_at_hand(chromium):
    """B8: "ถัดไป" just disappeared. And after the hand-over the robot is Emma Event's."""
    marks = {"9_0": _mark("done"), "9_1": _mark("done")}
    order = _at(9, 1, **marks)
    order.update(next=None, last=[9, 1], finished=True)
    page = Page(chromium, _state(order))
    try:
        assert page.page.locator("#nextBtn").is_hidden()
        assert page.visible("#orderEnd") and "ลำดับพิธีจบแล้ว ✓" in page.text("#orderEnd")
        assert page.page.locator("#orderEnd a.go").get_attribute("href") == "/event-control"
        assert page.page.locator("#orderTools").get_attribute("open") is None
        page.page.click("#orderEndReset")
        assert page.page.locator("#orderTools").get_attribute("open") is not None
        assert page.sent == []                             # it goes to the reset; the reset is two taps there
    finally:
        page.close()


def test_a_cleared_rehearsal_and_what_the_red_stop_reached_are_said(chromium):
    cleared = "ล้างความคืบหน้าเก่าแล้ว (กดครั้งล่าสุดเมื่อ 08:10 — เก่าเกิน 3 ชั่วโมง) เริ่มจากขั้นแรก"
    order = _order(_scenes(), next=[1, 0], cleared_th=cleared)
    page = Page(chromium, _state(order))
    try:
        assert page.visible("#orderCleared") and page.text("#orderCleared") == cleared
        page.posts["/event/stop"] = {"ok": True, "stopped": True, "line_cut": True, "live_closed": True,
                                     "body": {"sent": True, "error_th": None, "note_th": "สั่งหยุดแขนแล้ว (ล็อก 60 วิ)"}}
        page.page.click("#stopBtn")
        toast = page.toast()
        for said in ("ส่งคำสั่งหยุดเดินแล้ว", "สั่งหยุดแขนแล้ว (ล็อก 60 วิ)", "ตัดบทที่พูดอยู่", "ปิดช่องตอบสดแล้ว"):
            assert said in toast, said
        assert page.sent == [("/event/stop", {})]          # the one red stop both pages share
    finally:
        page.close()


def test_the_ceremony_app_not_on_the_robot_is_said_at_the_top(chromium):
    """Owner 9 Nov: the ceremony is its own app. Another app on the robot = every step but a tick
    is refused by the server; the page says so once, before anyone presses."""
    scenes = _scenes()
    for sc in scenes:
        for st in sc["steps"]:
            if st["do"] != "manual":
                st.update(ready=False, why_th="เปิดแอป Emma Ceremony บนหุ่นก่อน")
    page = Page(chromium, _state(_order(scenes, next=[1, 0]), ceremony_app=False))
    try:
        assert page.visible("#appBanner") and page.text("#appBanner") == NOT_CEREMONY_TH
        # the robot's call is another app's: not "Emma โหมดพิธี"
        assert "on" not in page.page.locator("#lampCall").get_attribute("class").split()
        page.redraw(_state(_order(_scenes(), next=[1, 0]), ceremony_app=True))
        assert not page.visible("#appBanner")
        assert "on" in page.page.locator("#lampCall").get_attribute("class").split()
        doc = _state(_order(_scenes(), next=[1, 0]))
        del doc["mode"]["ceremony_app"]                    # a server that does not say is not accused of it
        page.redraw(doc)
        assert not page.visible("#appBanner")
        # Emma Event's call is up: the hand-over worked -- the ceremony is over, never "open the
        # ceremony app" (following that stopped the after-party's walks; review of the split, 9 Nov)
        doc = _state(_order(_scenes(), next=None, finished=True), ceremony_app=False)
        doc["face"]["event_call"] = True
        page.redraw(doc)
        assert page.visible("#appBanner") and page.text("#appBanner").startswith("พิธีจบแล้ว")
        assert "เปิดแอป Emma Ceremony" not in page.text("#appBanner")
        page.redraw(_state(_order(scenes, next=[1, 0]), ceremony_app=False))   # nobody's call: as before
        assert page.text("#appBanner") == NOT_CEREMONY_TH
    finally:
        page.close()


def test_the_big_screen_and_the_hand_over_are_steps_of_their_own(chromium):
    page = Page(chromium, _state(_at(1, 1)))              # "ถัดไป" = 1.1, the big screen
    try:
        row = page.text("#step-1-1")
        assert "จอใหญ่" in row and "เปิดจอใหญ่" in row and "แตะ = สั่งจอใหญ่" in row
        assert "ฉาก 1 · จอใหญ่" in page.text("#nextTop")
        page.posts["/ceremony/order/next"] = {"ok": True, "n": 1, "i": 1, "do": "screen", "result": {"ok": True}}
        page.page.click("#nextBtn")
        assert "สั่งแล้ว: เปิดจอใหญ่" in page.toast()
        # the order's own `screen`, when a server gives it, names the way the screen goes
        doc = _state(_at(1, 1))
        doc["order"]["scenes"][0]["steps"][1]["screen"] = "open"
        page.redraw(doc)
        assert "แตะ = เปิดจอใหญ่" in page.text("#step-1-1")
        # the hand-over: the last step, said for what it is, and its answer either way
        page.page.wait_for_timeout(1300)
        page.redraw(_state(_at(9, 1, **{"9_0": _mark("done")})))
        handoff = page.text("#step-9-1")
        assert "ส่งต่อ" in handoff and "แตะ = จบพิธี เปิดแอป Emma Event" in handoff
        assert "จบพิธี เปิดแอป Emma Event" in page.text("#nextTop")
        page.posts["/ceremony/order/next"] = {"ok": True, "n": 9, "i": 1, "do": "handoff",
                                              "result": {"ok": True, "app": "event"}}
        page.page.click("#nextBtn")
        assert "เปิดแอป Emma Event บนหุ่นแล้ว" in page.toast()
        page.page.wait_for_timeout(1400)
        failed = "เปิดแอป Emma Event ไม่ได้ — เปิดเองบนหุ่น (หุ่นยังไม่ได้ลงแอป Emma Event)"
        page.posts["/ceremony/order/next"] = {"ok": False, "n": 9, "i": 1, "do": "handoff",
                                              "error": "handoff_failed", "error_th": failed,
                                              "result": {"ok": False, "error": "handoff_failed", "error_th": failed}}
        page.clear_toast()
        page.page.click("#nextBtn")
        assert failed in page.toast()                      # the server's words, verbatim
    finally:
        page.close()


def test_without_an_order_the_cue_list_stays_and_a_press_goes_to_the_ceremony(chromium):
    cues = [{"id": "s1_greet", "scene": 1, "scene_th": "เปิดพิธี", "host_th": "เชิญ Emma ค่ะ", "kind": "line",
             "th": GREET, "live_th": None, "ready": True, "approved": True, "problems": [], "recorded": True,
             "voice_approved": True, "voice_why": None, "phase": None, "clip": None, "last": None}]
    page = Page(chromium, _state(None, cues=cues))
    try:
        assert page.page.locator("#order").is_hidden() and page.page.locator("#nextBtn").is_hidden()
        assert page.visible("#cuesHead") and GREET in page.text("#cues")
        page.posts["/ceremony/cue"] = {"ok": True, "mode": "recorded"}
        page.page.click("#cues button.primary")
        assert "พูดด้วยเสียงอัด" in page.toast()
        assert page.sent == [("/ceremony/cue", {"id": "s1_greet"})]
    finally:
        page.close()


def test_the_poll_is_the_ceremony_state_and_keeps_coming(chromium):
    """Every poll is the walk's heartbeat (app/event_walk.py): it must keep coming while the
    page is open -- and only /ceremony/state, never the Event page's."""
    page = Page(chromium, _state(_at(1, 2)))
    try:
        before = len(page.gets)
        page.page.wait_for_timeout(2300)
        assert len(page.gets) - before >= 2
        assert set(page.gets) == {"/ceremony/state"}
    finally:
        page.close()


# ---- the source: what the browser tests stand on --------------------------------------------------

def test_a_post_is_cut_no_sooner_than_the_servers_longest_press():
    """POST_TIMEOUT_MS against the real waits: a walk (the replaced walk's stop, a pose read, the
    arm's rest, the walker's gate) and a line (her voice, the lock, her first sound)."""
    post = _js("async function post(")
    assert "new AbortController()" in post and "signal: ctl ? ctl.signal : undefined" in post
    assert "ms || POST_TIMEOUT_MS" in post and "clearTimeout(cut)" in post[post.index("finally"):]
    assert "unknown: true" in post and "UNKNOWN_TH" in post and "ติดต่อเซิร์ฟเวอร์ไม่ได้" not in post
    ms = int(re.search(r"POST_TIMEOUT_MS = (\d+)", PAGE).group(1))
    walk = (event_walk.SPAWN_WAIT_S + event_walk.KILL_AFTER_S + 3.0 + 2.0      # the walk it replaces
            + 3.0                                                              # steps_from_here's pose
            + settings.robot_body_ack_timeout_s + event_walk.ARM_REST_WAIT_S + event_walk.GATE_WAIT_S)
    line = event_cues.VOICE_WAIT_S + event_cues.LOCK_WAIT_S + event_cues.READ_START_S
    assert ms / 1000.0 >= max(walk, line) + 5, (ms, walk, line)
    assert "post('/event/stop', {}, STOP_TIMEOUT_MS)" in _js("$('stopBtn').onclick", "};\n")


def test_an_unknown_answer_is_never_called_a_refusal():
    outcome = _js("function orderOutcome(")
    assert outcome.index("if (r && r.unknown)") < outcome.index("if (r && r.ok)") < outcome.index("' ไม่ได้: '")
    act = _js("async function act(")
    assert act.index("if (r.unknown)") < act.index("ไม่ได้: ")
    send = _js("async function orderPost(")
    assert "if (r && r.unknown) { if (sent) unknownAt[sent] = Date.now(); }" in send
    assert "else if (r && r.n != null && r.i != null) delete unknownAt[r.n + ':' + r.i];" in send
    assert "if (whyTwoTaps(o, n, i, st) && !secondTap(k)) return;" in _js("function pressStep(")
    why = _js("function whyTwoTaps(")
    # the unknown result first: in either way of pressing, before the mode decides
    # (+ a light / curtain command whose result was "unknown": review 20 Nov, tests/test_venue_ceremony.py)
    assert why.splitlines()[1].strip() == "if (unknownAt[n + ':' + i] || deviceUnsure(st)) return 'unknown';"
    nxt = _js("$('nextBtn').onclick", "};\n")
    assert nxt.index("if ((unknownAt[k] || deviceUnsure(at.st)) && !secondTap(k))") < nxt.index("orderPost(")
    assert "unknownAt]);" in _js("function renderOrder(")         # rows rebuilt when it changes


def test_the_page_talks_to_the_ceremony_endpoints_and_the_shared_stop_only():
    """Owner 9 Nov ("ไม่งั้นจะปนกัน"): nothing of the Event page's scenes is pressed from here. The
    walk's resume and the walk home are refused while the ceremony app is on, so they are not
    offered; the dock pose and the film's stop are allowed and stay."""
    script = PAGE[PAGE.index("<script>"):]
    posts = set(re.findall(r"post\('(/[a-z/-]+)'", script))
    order = set(re.findall(r"orderPost\('(/[a-z/-]+)'", script))
    # + the opening's steps by hand (23 Nov, card 1 as the ceremony's first step: app/event_order.py `opening_hand`)
    assert order == {"/ceremony/order/step", "/ceremony/order/next", "/ceremony/order/skip",
                     "/ceremony/order/reset", "/ceremony/order/opening-hand"}
    # + the venue's lights / curtains card (20 Nov, app/venue_devices.py): the ceremony page's own endpoint
    # + "ตอบสด" (23 Nov, the host off-script: app/ceremony.py adhoc_open; tests/test_ceremony_adhoc.py)
    # + "ทั้งพิธีคุยสด" (24 Nov, the server follows the host: app/ceremony_follow.py): its own control
    assert posts == {"/ceremony/cue", "/event/stop", "/ceremony/device", "/ceremony/adhoc", "/ceremony/follow"}, posts
    acts = set(re.findall(r"act\('(/[a-z/-]+)'", script))
    # + the in-the-box panel (23 Nov, app/event_box.py): the rest of a walk out (the ceremony's own walk), the latch
    assert acts == {"/event/dock-pose", "/event/clip", "/ceremony/box-finish", "/event/box-clear"}, acts
    assert "act('/event/clip', { action: 'stop' }" in script and "{ clip:" not in script
    assert set(re.findall(r"fetch\('(/[a-z/-]+)'", script)) == {"/ceremony/state"}
    for gone in ("/event/order/", "/event/cue", "/event/scene", "/event/resume", "/event/go", "/event/state",
                 "resumeBtn", "homeBtn"):
        assert gone not in PAGE, gone
    assert "https://" not in PAGE and "http://" not in PAGE        # the office Wi-Fi is all it may have
    assert "setInterval(poll, 1000);" in PAGE                        # the heartbeat


def test_the_page_stands_alone_and_stop_keeps_the_bottom_edge():
    assert "<title>Emma Ceremony</title>" in PAGE
    style = PAGE[PAGE.index("<style>"):PAGE.index("</style>")]
    assert "[hidden] { display: none !important; }" in style
    bar = PAGE[PAGE.index('<div class="stopbar">'):]
    bar = bar[:bar.index("</div></div>")]
    assert bar.index('id="nextBtn"') < bar.index('id="orderEnd"') < bar.index('id="stopBtn"')
    stop = style[style.index(".stopbar #stopBtn {"):]
    stop = stop[:stop.index("}")]
    assert "width: 100%" in stop and "min-height: 72px" in stop and "var(--bad)" in stop
    assert "position: fixed" in style.split(".stopbar {", 1)[1].split("}", 1)[0]
    assert NOT_CEREMONY_TH in PAGE
    render = _js("function render(")
    assert "var off = !!(s.mode && s.mode.ceremony_app === false);" in render


# ---- moved with the order from event-control.html (test_event_cues.py's page section, 9 Nov) -------

def _order_section() -> str:
    return PAGE[PAGE.index("// ---- the running order"):PAGE.index("// ---- the poll")]


def test_the_page_runs_the_ceremony_as_a_running_order():
    """Owner 7 Nov: "ทำเป็นแบบกดสั่งด้วยนะ พวกซีนต่างๆ" -- every press of the ceremony, scene by
    scene, from `order`. A press sends the step's place, never words. No "scene" step any more
    (owner 9 Nov: opening the app is the ceremony's mode)."""
    for piece in ("ลำดับพิธี", "ข้ามขั้นนี้", "เริ่มลำดับใหม่", "อ่านทั้งหมด", 'id="nextBtn"',
                  "ทำแล้ว ✓", "กำลังทำ…", "ไม่สำเร็จ", "ข้ามแล้ว", "ยังไม่พร้อม"):
        assert piece in PAGE, piece
    kinds = _js("var KIND_TH = {", "};\n")
    for do, th in (("walk", "เดิน"), ("body", "ท่า"), ("cue", "พูด"), ("clip", "คลิป"), ("screen", "จอใหญ่"),
                   ("handoff", "ส่งต่อ"), ("manual", "ทำเอง")):
        assert f"{do}: '{th}'" in kinds, do
    assert "scene:" not in kinds and "scene:" not in _js("var DO_TH = {", "};\n")
    voices = _js("var VOICE_TH = {", "};\n")
    for voice, th in (("recorded", "เสียงอัด"), ("read", "Gemini อ่าน"), ("live", "ตอบสด")):
        assert f"{voice}: '{th}'" in voices, voice
    row = _js("function stepRow(")
    for field in ("st.label_th", "st.line_th", "st.show_th", "st.note_th", "st.why_th", "mark.error_th",
                  "VOICE_TH[st.voice]", "'ภาพ'"):
        assert field in row, field
    assert "var why = (r && (r.error_th || CUE_ERR[r.error] || r.error))" in _js("function orderOutcome(")
    assert "orderPost('/ceremony/order/step', { n: n, i: i }" in _js("function pressStep(")
    assert "orderPost('/ceremony/order/next', { n: at.sc.n, i: at.st.i }" in _js("$('nextBtn').onclick", "};\n")
    assert "orderPost('/ceremony/order/skip', { n: sc.n, i: st.i }" in row
    assert "manual ? 'is-manual'" in _js("function renderNextBtn(")
    style = PAGE[PAGE.index("<style>"):PAGE.index("</style>")]
    assert ".step.k-manual {" in style and "#nextBtn.is-manual {" in style


def test_the_flat_cue_list_stays_until_the_server_has_an_order():
    poll = _js("async function poll(")
    assert "if (hasOrder(state)) renderCues(null);" in poll
    assert "else renderCues(state.cues);" in poll and "renderOrder(state);" in poll
    order = _js("function renderOrder(")
    gone = order[:order.index("if (!o)")]
    assert "$('orderHead').hidden = $('orderNote').hidden = $('order').hidden = $('orderTools').hidden = !o;" in gone
    assert "renderNextBtn(o);" in gone
    assert "b.hidden = !at;" in _js("function renderNextBtn(")
    for piece in ("บทพิธี", "พูดบทนี้", "พูดด้วยเสียงอัด", "ให้ Gemini อ่าน", "ไม่มีสายเปิด", "ยังไม่อนุมัติ",
                  "มีเสียงอัด", "เสียงอัดยังไม่ได้ฟังอนุมัติ", "voice_approved"):
        assert piece in PAGE, piece
    # a line waiting for her voice must not hold every other button (act's one `busy`)
    press = PAGE[PAGE.index("async function pressCue"):PAGE.index("// ---- the running order")]
    assert "act(" not in press and "busy = true" not in press


def test_a_double_tap_never_says_two_lines():
    gap = int(re.search(r"ORDER_GAP_MS = (\d+)", PAGE).group(1))
    assert gap >= 1200
    held = _js("function orderHeld(", "}\n")
    assert "orderBusy !== null" in held and "ORDER_GAP_MS" in held
    send = _js("async function orderPost(")
    assert send.splitlines()[1].strip() == "if (orderHeld()) return;"     # before anything goes out
    assert send.index("orderBusy = what;") < send.index("await post(")
    assert "orderAt = Date.now();" in send[send.index("finally"):]        # the gap again from the answer
    section = _order_section()
    assert len(re.findall(r"(?<![A-Za-z])post\(", section)) == 1          # orderPost is the order's only way out
    assert "fetch(" not in section and "act(" not in section
    assert "b.disabled = orderHeld();" in _js("function renderNextBtn(")
    stop = _js("$('stopBtn').onclick", "};\n")
    assert "orderHeld" not in stop and "orderBusy" not in stop and "busy" not in stop


def test_a_step_that_is_not_ready_is_only_skipped_and_one_out_of_turn_takes_two_taps():
    press = _js("function pressStep(")
    send = press.index("orderPost(")
    assert press.index("if (!st.ready) { notReadyToast(st, isAt(o.next, n, i)); return; }") < send
    assert press.index("if (whyTwoTaps(o, n, i, st) && !secondTap(k)) return;") < send
    # "ตามลำดับ": out of its turn = two taps; a double tap is not a second thought
    assert "return isAt(o.next, n, i) ? null : 'out_of_turn';" in _js("function whyTwoTaps(")
    assert "return now - stepArmed.at >= ARM_MIN_MS;" in _js("function secondTap(")
    nxt = _js("$('nextBtn').onclick", "};\n")
    assert nxt.index("if (!at.st.ready)") < nxt.index("orderPost(")
    row = _js("function stepRow(")
    assert "press.setAttribute('aria-disabled', 'true')" in row and "row.setAttribute('aria-disabled'" not in row
    assert "acts.appendChild(skip)" in row and "press.appendChild(skip)" not in row
    assert "e.target.closest('button')" in row


def test_the_order_is_rebuilt_only_when_it_changed_and_scrolls_only_after_a_press():
    order = _js("function renderOrder(")
    assert "var key = JSON.stringify([o, orderBusy" in order
    assert "buildOrder(o);" in order[order.index("if (key !== orderKey) {"):]
    assert "if (orderScroll) { orderScroll = false; scrollToStep(o.next); }" in order
    # set once, after a press, and only in "ตามลำดับ" (in "กดเอง" staff stay where they pressed)
    section = _order_section()
    assert section.count("orderScroll = !manualMode();") == 1 and "orderScroll = true" not in section
    assert "orderScroll = !manualMode();" in _js("async function orderPost(")


def test_polls_never_overlap_and_a_press_still_gets_a_fresh_one():
    poll = _js("async function poll(")
    assert poll.splitlines()[1].strip() == "if (polling) {"
    guard = poll[:poll.index("polling = true;")]
    assert "if (asked === true) pollAgain = true;" in guard and "return;" in guard
    tail = poll[poll.rindex("} finally {"):]
    assert "polling = false;" in tail and "if (pollAgain) { pollAgain = false; poll(); }" in tail
    assert "ctl.abort()" in poll and "POLL_TIMEOUT_MS" in poll and "signal:" in poll
    for fn in ("async function orderPost(", "async function act(", "async function pressCue("):
        assert "poll(true);" in _js(fn), fn
    assert "poll(true);" in _js("$('stopBtn').onclick", "};\n")


def test_the_page_says_the_ceremonys_mode_and_what_the_stop_reached():
    for piece in ("modeCard", "clipCard", "โหมดพิธีเปิด", "ตอบเมื่อเรียกชื่อ Emma", "Emma พูดเฉพาะบทที่กด ไม่ตอบใคร",
                  "กล้องไม่ทักใคร", "หยุดคลิป"):
        assert piece in PAGE, piece
    msg = _js("function stopMessage(")
    for path in ("r.stopped", "var body = r && r.body;", "body.sent", "body.note_th", "body.error_th",
                 "r.line_cut", "r.live_closed", "'E-stop'"):
        assert path in msg, path
    assert "return" not in msg[:msg.index("var body = r && r.body;")].replace("function stopMessage(", "")
    assert "toast(stopMessage(r), 8000);" in _js("$('stopBtn').onclick", "};\n")


def test_a_step_that_is_not_ready_is_not_pressed_from_next(chromium):
    page = Page(chromium, _state(_order(_scenes(), next=[1, 0])))   # 1.0, the walk: walking is off
    try:
        assert "is-unready" in page.page.locator("#nextBtn").get_attribute("class")
        page.page.click("#nextBtn")
        assert "ยังไม่พร้อม: การเดินปิดอยู่" in page.toast() and page.sent == []
    finally:
        page.close()


# ---- "กดเอง" (manual): owner 14 Nov 2026, "ทำหน้าปุ่มกดยัง ทำแบบ manual" ------------------------------
# The page's default: every step its own button, any step at any time, one tap -- the button, never
# the row; no big "ถัดไป". A walk and the hand-over still take two taps (they move the robot / end the
# ceremony), and so does a step whose last press got no answer. "ตามลำดับ" is one tap away, kept per
# phone (localStorage), and is the page the tests above drive.

def _walk_ready(order: dict) -> dict:
    """1.0, the walk, ready (walking on) -- a walk to press."""
    order["scenes"][0]["steps"][0].update(ready=True, why_th=None)
    return order


def test_manual_is_the_default_and_every_step_is_one_tap_on_its_own_button(chromium):
    page = Page(chromium, _state(_at(1, 2)), mode=None)             # nobody chose: the page's default
    try:
        assert page.visible("#orderMode")
        assert page.page.locator('#orderMode [data-mode="manual"]').get_attribute("aria-pressed") == "true"
        assert page.page.locator("#nextBtn").is_hidden()          # no "ถัดไป" in this way of pressing
        assert "กดปุ่มของขั้นไหนก็ได้" in page.text("#orderNote")
        # every step has its button, saying what it does
        assert page.page.locator("#order .step").count() == page.page.locator("#order .step .sgo").count()
        assert page.text("#step-2-0 .sgo") == "พูดบทนี้" and page.text("#step-1-3 .sgo") == "ทำท่า"
        assert page.text("#step-2-2 .sgo") == "บอกว่าทำแล้ว (ไม่สั่งอะไร)"
        # a tap on the row is no press (a scroll is a touch); the button is, once, out of its turn
        page.page.click("#step-2-0 .slabel")
        page.page.wait_for_timeout(200)
        assert page.sent == []
        page.page.click("#step-2-0 .sgo")                          # ahead of "ถัดไป": one tap all the same
        page.page.wait_for_timeout(300)
        assert page.sent == [("/ceremony/order/step", {"n": 2, "i": 0})]
        page.page.wait_for_timeout(1400)                           # the order's gap after an answer
        page.page.click("#step-1-3 .sgo")                          # a gesture, back before "ถัดไป"
        page.page.wait_for_timeout(300)
        assert page.sent[-1] == ("/ceremony/order/step", {"n": 1, "i": 3}) and len(page.sent) == 2
        # a step that is not ready: its button cannot be pressed, and says nothing was sent
        assert page.page.locator("#step-2-4 .sgo").is_disabled()
    finally:
        page.close()


@pytest.mark.parametrize("at, armed", [((1, 0), "แตะอีกครั้งเพื่อเริ่มเดิน (หุ่นจะเดิน)"),
                                       ((9, 1), "แตะอีกครั้งเพื่อจบพิธี เปิดแอป Emma Event (จบพิธี)")])
def test_manual_a_walk_and_the_hand_over_still_take_two_taps(chromium, at, armed):
    page = Page(chromium, _state(_walk_ready(_at(1, 2))), mode="manual")
    try:
        sel = "#step-%d-%d .sgo" % at
        assert "(แตะสองครั้ง)" in page.text(sel)
        page.page.click(sel)
        page.page.wait_for_timeout(150)
        assert page.sent == [] and page.text(sel) == armed
        page.page.click(sel)                                       # a double tap is not a second thought
        page.page.wait_for_timeout(150)
        assert page.sent == []
        page.page.wait_for_timeout(400)
        page.page.click(sel)
        page.page.wait_for_timeout(300)
        assert page.sent == [("/ceremony/order/step", {"n": at[0], "i": at[1]})]
    finally:
        page.close()


def test_manual_a_press_with_no_answer_takes_a_second_tap_after(chromium):
    page = Page(chromium, _state(_at(1, 2)), mode="manual")
    try:
        page.posts["/ceremony/order/step"] = "abort"
        page.page.click("#step-2-0 .sgo")
        toast = page.toast()
        assert "ไม่รู้ผล — ดูสถานะขั้นนี้ก่อนกดซ้ำ" in toast and "ไม่ได้" not in toast
        page.page.wait_for_timeout(1400)
        assert "(แตะสองครั้ง)" in page.text("#step-2-0 .sgo") and "กดครั้งก่อนไม่รู้ผล" in page.text("#step-2-0")
        page.page.click("#step-2-0 .sgo")                          # armed, nothing sent
        page.page.wait_for_timeout(150)
        assert len(page.sent) == 1 and "(ผลครั้งก่อนไม่รู้)" in page.text("#step-2-0 .sgo")
    finally:
        page.close()


def test_manual_her_live_window_closes_from_its_own_button(chromium):
    doc = _live_state()
    page = Page(chromium, doc, mode="manual")
    try:
        assert page.page.locator("#nextBtn").is_hidden()
        live = page.text("#step-2-3 .sgo")                         # and its countdown, on its own button
        assert live.startswith("ปิดช่องตอบสด (Emma ตอบจบแล้ว)") and re.search(r"ปิดเองใน 2:(2\d|30)", live), live
        # another step refused while it is open: said with the button "กดเอง" has, not "ถัดไป"
        page.posts["/ceremony/order/step"] = {"ok": False, "n": 2, "i": 0, "error": "live",
                                              "error_th": event_order.ERRORS_TH["live"]}
        page.page.click("#step-2-0 .sgo")
        toast = page.toast()
        assert 'กด "ปิดช่องตอบสด" ที่ฉาก 2' in toast and "ถัดไป" not in toast, toast
        page.clear_toast()
        page.page.wait_for_timeout(1400)
        page.posts["/ceremony/order/step"] = {"ok": True, "n": 2, "i": 3, "do": "cue",
                                              "result": {"ok": True, "live": "closed"}}
        page.page.click("#step-2-3 .sgo")
        assert "ปิดช่องตอบสดแล้ว" in page.toast()
        assert page.sent[-1] == ("/ceremony/order/step", {"n": 2, "i": 3}) and len(page.sent) == 2
    finally:
        page.close()


def test_manual_a_step_already_done_takes_two_taps_again(chromium):
    """Review 14 Nov round 2: "ตามลำดับ" made a step out of its turn two taps for exactly this -- a
    stray tap must not say a line, or hold out a hand, a second time. A "ทำเอง" tick moves nothing."""
    page = Page(chromium, _state(_at(2, 3)), mode="manual")
    try:
        assert page.text("#step-2-0 .sgo") == "พูดบทนี้ อีกครั้ง (แตะสองครั้ง)"
        page.page.click("#step-2-0 .sgo")
        page.page.wait_for_timeout(150)
        assert page.sent == [] and page.text("#step-2-0 .sgo").endswith("(ทำไปแล้ว)")
        page.page.wait_for_timeout(400)
        page.page.click("#step-2-0 .sgo")
        page.page.wait_for_timeout(300)
        assert page.sent == [("/ceremony/order/step", {"n": 2, "i": 0})]
        page.page.wait_for_timeout(1400)                           # the order's gap after an answer
        assert "(แตะสองครั้ง)" not in page.text("#step-2-2 .sgo")      # the tick: one tap, again or not
        page.page.click("#step-2-2 .sgo")
        page.page.wait_for_timeout(300)
        assert page.sent[-1] == ("/ceremony/order/step", {"n": 2, "i": 2}) and len(page.sent) == 2
    finally:
        page.close()


def test_manual_the_live_countdown_keeps_its_second_tap_word(chromium):
    """The countdown is written onto the live step's button every poll: after a press with no answer
    it wrote over "(แตะสองครั้ง)" (round 2)."""
    page = Page(chromium, _live_state(), mode="manual")
    try:
        page.posts["/ceremony/order/step"] = "abort"
        page.page.click("#step-2-3 .sgo")
        page.toast()
        page.page.wait_for_timeout(1400)                           # polls after the press
        live = page.text("#step-2-3 .sgo")
        assert "ปิดเองใน" in live and live.endswith("(แตะสองครั้ง)"), live
    finally:
        page.close()


def test_the_way_of_pressing_is_switched_and_kept_on_the_phone(chromium):
    page = Page(chromium, _state(_at(1, 2)), mode=None)
    try:
        page.page.click('#orderMode [data-mode="order"]')
        page.page.wait_for_timeout(150)
        assert page.visible("#nextBtn") and page.page.locator("#order .sgo").count() == 0
        assert "กด \"ถัดไป\"" in page.text("#orderNote")
        assert page.page.evaluate("localStorage.getItem('%s')" % MODE_KEY) == "order"
        page.page.reload()                                         # the next load keeps it
        page.page.wait_for_function("document.getElementById('lampServer').classList.contains('on')")
        page.page.wait_for_timeout(250)
        assert page.visible("#nextBtn")
        page.page.click('#orderMode [data-mode="manual"]')
        page.page.wait_for_timeout(150)
        assert page.page.locator("#nextBtn").is_hidden() and page.page.locator("#order .sgo").count() > 0
        assert page.sent == []                                     # switching presses nothing
    finally:
        page.close()


def test_the_manual_way_in_the_source():
    """What the browser tests above stand on: the default, the one place two taps are decided, and
    the row that is no press in "กดเอง"."""
    assert "var MODE_KEY = 'emma.ceremony.press', pressMode = 'manual';" in PAGE
    assert "try { if (localStorage.getItem(MODE_KEY) === 'order') pressMode = 'order'; } catch (e)" in PAGE
    # a venue light / curtain step too (20 Nov): a stray tap must not darken the lobby; and the opening (23 Nov): she
    # walks out of the box
    # ...and card 2, the live mode (24 Nov): the server then plays the cues by the host's words
    assert "var TWO_TAP_KINDS = { walk: true, handoff: true, device: true, opening: true, follow: true };" in PAGE
    why = _js("function whyTwoTaps(")
    assert why.index("unknownAt") < why.index("manualMode()") < why.index("out_of_turn")
    row = _js("function stepRow(")
    assert row.index("if (manual) return row;") < row.index("row.onclick = function (e) {")
    assert "go.onclick = function () { pressStep(sc.n, st.i); };" in row
    assert "at = o && !manualMode() ? stepAt(o, o.next) : null;" in _js("function renderNextBtn(")
    assert "pressMode, unknownAt]);" in _js("function renderOrder(")     # rebuilt when the way changes
