"""Emma Ceremony, the third robot app (owner 9 Nov 2026), on the pages.

"อันนี้ทำแยกกับอันเดิมนะ ไม่งั้นจะปนกัน อันนี้จะเป็นสำหรับพิธี ที่สามารถใช้ข้อมูลอันเก่าได้" -- asked, the
owner chose a third app with its own APK, its own staff page and its own script, opening it = the
ceremony's mode, and Emma Event back to its own scenes with nothing of the ceremony mixed in.
What these hold down (the ceremony page's own running order: tests/test_event_order_page.py;
the APK: tests/test_emma_robot_android_app.py):

* the face page in the ceremony app (ceremony=1) dials profile ceremony and is one mode;
* the server's `open_app` (the ceremony's last step) reaches the app's bridge only from the
  ceremony app's own page, only for Emma Event, only on an APK that says EmmaOpenApp/1 -- every
  other page answers why not (`open_app_result`), asks the app nothing, and is not "her answer";
* client/event-control.html has no running order, no cue list and no ceremony scene any more,
  and says so -- with the way to the ceremony page -- while the ceremony app holds the robot.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from tests.test_event_order_page import Page, chromium, real  # noqa: F401 -- fixtures, used by name
from tests.test_face_body_nav_page import _model, _scenario

ROOT = Path(__file__).resolve().parents[1]
FACE = ROOT / "client" / "voice-preview.html"
EVENT_PAGE = (ROOT / "client" / "event-control.html").read_text(encoding="utf-8")
CEREMONY_BANNER = ("หุ่นเปิดแอป Emma Ceremony อยู่ (พิธีเปิด) — ฉากงานกดได้เมื่อเปิดแอป Emma Event"
                   " · ปุ่มหยุดยังใช้ได้")


def _face() -> str:
    return FACE.read_text(encoding="utf-8")


# ---- the face page in the ceremony app ------------------------------------------------------------

def test_the_ceremony_app_dials_the_ceremony_and_is_one_mode():
    html = _face()
    assert "var ceremonyApp = appShell && !mirror && q.get('ceremony') === '1';" in html
    assert "if (ceremonyApp) q.set('profile', 'ceremony');" in html
    # after the app shell's strip of profile/lang (or it would be stripped too), and after the
    # event app's line: only the ceremony APK adds ceremony=1, so it wins
    at = html.index("if (ceremonyApp) q.set('profile', 'ceremony');")
    assert html.index("q.delete('profile');") < at
    assert html.index("if (eventApp) q.set('profile', 'event');") < at
    cycle = html.split("function cycleMode() {", 1)[1].split("\n    }\n", 1)[0]
    first = [ln.strip() for ln in cycle.strip().splitlines()[:2]]
    assert first[1].startswith("if (ceremonyApp) return;")      # before any mode is changed


def _on_message(events: list, bridge: bool) -> dict:
    """The page's REAL onMessage (extracted) fed `events`; what reached the bridge and the socket."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required: the face page's open_app routing is NOT being checked")
    html = _face()
    handler = html[html.index("    function onMessage(msg)"):html.index("    // ---- dial / hang up ----")]
    script = """
const vm = require('vm');
const sent = [], opened = [];
// the call's socket: the page's own robotSender (in the slice) checks it is this one and open
const ws = { readyState: 1, send: (text) => { sent.push(JSON.parse(text)); } };
const ctx = { serverSeq: 0, ws, WebSocket: { OPEN: 1 }, JSON, console, window: {} };
if (%(bridge)s) ctx.window.EmmaRobotBridge = { openApp: (evt, sender) => {
  opened.push(evt); sender({ type: 'open_app_result', app: 'event', ok: true, reason: null }); } };
vm.createContext(ctx);
vm.runInContext(%(handler)s, ctx);
const seq = [];
for (const evt of %(events)s) { ctx.onMessage({ data: JSON.stringify(evt) }); seq.push(ctx.serverSeq); }
process.stdout.write(JSON.stringify({ sent, opened, seq }));
""" % {"bridge": "true" if bridge else "false", "handler": json.dumps(handler), "events": json.dumps(events)}
    run = subprocess.run([node, "-e", script], capture_output=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_open_app_goes_to_the_bridge_and_is_never_counted_as_her_answer():
    out = _on_message([{"type": "open_app", "app": "event"}, {"type": "zzz_not_a_type"}], bridge=True)
    assert out["opened"] == [{"type": "open_app", "app": "event"}]
    assert out["sent"] == [{"type": "open_app_result", "app": "event", "ok": True, "reason": None}]
    # a request to the app is not her answer: a tap's "Thinking..." fallback still counts on it
    assert out["seq"] == [0, 1]


def test_a_page_without_the_bridge_answers_no_bridge_never_silence():
    out = _on_message([{"type": "open_app", "app": "event"}, {"type": "open_app", "app": "../x"}], bridge=False)
    assert out["opened"] == []
    # on the call's own socket (the page's robotSender), whatever the app id was
    assert out["sent"] == [
        {"type": "open_app_result", "app": "event", "ok": False, "reason": "no_bridge"},
        {"type": "open_app_result", "app": None, "ok": False, "reason": "no_bridge"}]


def test_open_app_reaches_the_app_only_from_the_ceremony_app_on_an_apk_that_says_so():
    """The page's REAL bridge script (tests/face_nav_model.cjs): one prompt, the app's own literal,
    from the ceremony app's page alone; an APK without EmmaOpenApp/1 is never asked (one with
    EmmaBridge/1 alone answers bad_request, an older one shows the prompt in front of a guest)."""
    run = _scenario("open_app")

    def result(name):
        assert len(run[name]["sent"]) == 1, (name, run[name])
        frame = run[name]["sent"][0]
        assert frame["type"] == "open_app_result", frame
        return frame

    assert result("opened") == {"type": "open_app_result", "app": "event", "ok": True, "reason": None}
    assert run["opened"]["prompts"] == ["emma-face:open:event"]
    assert result("not_installed")["reason"] == "not_installed"
    assert result("bad_reason")["reason"] == "bad_request"           # the app's word, whitelisted
    assert result("no_answer") == {"type": "open_app_result", "app": "event", "ok": False, "reason": "bad_request"}
    for name, why in (("old_apk", "no_bridge"), ("event_app", "not_ceremony_app"), ("sales_app", "not_ceremony_app"),
                      ("desk", "not_ceremony_app"), ("mirror", "not_ceremony_app")):
        assert result(name) == {"type": "open_app_result", "app": "event", "ok": False, "reason": why}, name
        assert run[name]["prompts"] == [], name                     # nothing asked of the app
    # on the ceremony app itself: only Emma Event, by its id; nothing else is an open_app; no sender, no open
    assert run["wrong"]["sent"] == [{"type": "open_app_result", "app": None, "ok": False, "reason": "unknown_app"}] * 2
    assert run["wrong"]["prompts"] == []


def test_the_open_message_is_one_closed_shape():
    built = _model({"fn": "openAppMessage", "args": ["event"]}, {"fn": "openAppMessage", "args": ["control"]},
                   {"fn": "openAppMessage", "args": ["com.emma.robot.event"]}, {"fn": "openAppMessage", "args": [None]},
                   {"fn": "openAppMessage", "args": ["Event"]})
    assert built == ["emma-face:open:event", None, None, None, None]


# ---- the event page: Emma Event's scenes alone -------------------------------------------------------

def test_the_event_page_has_nothing_of_the_ceremony():
    """Owner 9 Nov: "ไม่งั้นจะปนกัน". The running order and the cue list moved to
    client/ceremony-control.html whole; no ceremony scene is drawn as one any more."""
    script = EVENT_PAGE[EVENT_PAGE.index("<script>"):]
    for gone in ("/event/order/", "/event/cue", "/ceremony/", "renderOrder", "renderCues", "pressCue",
                 "orderPost", 'id="nextBtn"', 'id="orderEnd"', 'id="order"', 'id="cues"', ">ลำดับพิธี<",
                 "ลำดับพิธีจบแล้ว", ">บทพิธี<", "cue_only", "sc.ceremony", "sc.replies", "'พิธีเปิด'", "with-next", ".ocard", ".cuegroup"):
        assert gone not in script and gone not in EVENT_PAGE.split("<script>", 1)[0], gone
    # everything else stays: scenes, stops, spots, walks, the film, walking up, the stop
    for kept in ("/event/scene", "/event/next", "/event/stop-index", "/event/go", "/event/spot", "/event/resume",
                 "/event/dock-pose", "/event/clip", "/event/approach", "/event/approach/corner",
                 "post('/event/stop', {}, STOP_TIMEOUT_MS)", "fetch('/event/state'", "setInterval(poll, 1000);"):
        assert kept in EVENT_PAGE, kept
    style = EVENT_PAGE[EVENT_PAGE.index("<style>"):EVENT_PAGE.index("</style>")]
    assert "[hidden] { display: none !important; }" in style


def test_the_event_page_says_when_the_ceremony_app_holds_the_robot():
    assert CEREMONY_BANNER in EVENT_PAGE
    banner = EVENT_PAGE.split('id="appBanner"', 1)[1].split("</div>", 1)[0]
    assert 'href="/ceremony-control"' in banner and 'id="appBanner" hidden>' in EVENT_PAGE
    render = EVENT_PAGE.split("function render(", 1)[1].split("\n  }\n", 1)[0]
    # only an explicit true: a server that does not say is not taken to mean it is
    assert "$('appBanner').hidden = !(s.mode && s.mode.ceremony_app === true);" in render


def _event_state(ceremony_app: bool | None) -> dict:
    now = time.time()
    scene = {"brief_th": "", "spot": None, "stops": [], "approach": False, "tv": None,
             "clip": None, "roam": False}
    mode = {"ceremony": bool(ceremony_app), "ceremony_app": ceremony_app, "needs_name": False, "cue_only": False,
            "live": None, "tv": None, "warnings": [], "roam": None}
    if ceremony_app is None:
        del mode["ceremony_app"]
    return {
        "ok": True, "event": {"name_th": "เปิดตัว Embassy World", "dates": ["2026-11-25", "2026-11-26"]},
        "scenes": [dict(scene, id="welcome", title_th="ต้อนรับแขก", brief_th="ยืนที่ส่วนต้อนรับ", spot="reception"),
                   dict(scene, id="tables", title_th="ทักทายที่โต๊ะ", stops=["t1", "t2"]),
                   dict(scene, id="after", title_th="หลังพิธี", approach=True)],
        "spots": [{"id": "reception", "name_th": "ส่วนต้อนรับ", "dock": False, "walkable": True,
                   "pose": [-9.0, -3.0, 0.0], "recorded_at": "2026-11-05 10:00", "arrivals": 2},
                  {"id": "t1", "name_th": "โต๊ะ 1", "dock": False, "walkable": False, "pose": None,
                   "recorded_at": None, "arrivals": 0},
                  {"id": "t2", "name_th": "โต๊ะ 2", "dock": False, "walkable": False, "pose": None,
                   "recorded_at": None, "arrivals": 0},
                  {"id": "dock", "name_th": "แท่นชาร์จ", "dock": True, "walkable": True, "pose": [-4.1, 1.5, 0.0],
                   "recorded_at": None, "arrivals": 0}],
        "current": {"scene": "welcome", "stop": 0, "since": now},
        "walk": {"enabled": True, "run": None, "health": {"known": True, "board": True, "battery": 76,
                                                          "charging": False, "q": 71, "lost": False,
                                                          "low_battery": False, "docking": "on_dock"}},
        "face": {"connected": True, "event_call": not ceremony_app},
        "mode": mode,
        "clips": {"clips": [{"id": "tech", "title_th": "วิดีโอเทคโนโลยี", "has_file": True, "ready": True}],
                  "phase": None, "title_th": None, "screen_ready": True, "screen_up": False},
        "screen": {"offered": True, "open": False, "name": "multimedia"}, "server_time": now,
    }


def test_the_event_state_the_page_is_drawn_from_is_the_servers_own(real):
    doc = real.get("/event/state").json()
    built = _event_state(True)
    assert set(built) - {"server_time"} <= set(doc), set(built) - set(doc)
    assert set(built["mode"]) <= set(doc["mode"]) and isinstance(doc["mode"]["ceremony_app"], bool)
    assert set(built["scenes"][0]) <= set(doc["scenes"][0]) and set(built["spots"][0]) <= set(doc["spots"][0])


def test_the_event_page_in_a_browser(chromium):  # noqa: F811 -- the imported fixture
    page = Page(chromium, _event_state(True), html=EVENT_PAGE, path="/event-control")
    try:
        assert page.visible("#appBanner") and CEREMONY_BANNER in page.text("#appBanner")
        assert page.page.locator("#appBanner a.go").get_attribute("href") == "/ceremony-control"
        assert "แอป Emma Ceremony เปิดอยู่" in page.text("#modeNow")
        assert page.page.locator(".scene").count() == 3 and "พิธีเปิด" not in page.text("#scenes")
        page.posts["/event/stop"] = {"ok": True, "stopped": False, "line_cut": False, "live_closed": False,
                                     "body": {"sent": True, "error_th": None, "note_th": None}}
        page.page.click("#stopBtn")                        # the red stop still works with the ceremony app on
        assert "ไม่มีการเดินอยู่" in page.toast() and page.sent == [("/event/stop", {})]
        page.redraw(_event_state(False))
        assert not page.visible("#appBanner") and page.text("#modeNow") == "โหมดปกติ"
        page.redraw(_event_state(None))
        assert not page.visible("#appBanner")
        # its own state only -- never the ceremony's (the morning card's GET /event/ready since 20 Nov)
        assert set(page.gets) <= {"/event/state", "/event/ready"} and "/event/state" in page.gets
        assert page.page.locator("#nextBtn").count() == 0 and page.page.locator("#order").count() == 0
    finally:
        page.close()
