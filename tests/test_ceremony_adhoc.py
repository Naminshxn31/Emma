"""Emma Ceremony's "ตอบสด": the host goes off-script (owner 23 Nov 2026).

"วันจริงมิกิอาจจะด้นสดบ้าง ... เขาอาจจะนอกบทแล้วสลับไปมาได้" -> "ตอบสดทำเลย ยังไงก็ทำไปก่อนไม่เวิร์ค
ค่อยไปใช่แบบกด". The scripted lines stay pressed (measured 7 Nov: pressed cues 33/33, free live chat
76% on-script, the model picking the cue 68% -- staff decide when it is live, never the model). One
button on the ceremony page opens her live window for the host off-script (app/ceremony.py
`adhoc_open`), the same one closes it, and any press of the script closes it first
(app/event_control_server.py `_back_to_script`). What these hold down:

* the window's brief is the server's (ADHOC_BRIEF_TH), never words from the phone; it is not a step
  of the running order (no mark, no order change);
* nothing she hears is widened: the named-only door, the deaf mic outside it, nothing "named" by the tap;
* every POST /ceremony/* but the button itself, a skip and the lights card closes it BEFORE its press
  runs -- the routes are walked, so a new one cannot forget; the red stop closes it and says so;
* its own end (CEREMONY_ADHOC_MAX_S under EVENT_CEREMONY_MAX_S) closes it with a note for the page;
* refused, with the reason, while the ceremony app is not on, the ceremony answers by name anyway, the
  order's own live step is open, the opening runs, she walks or moves, a line is said or still heard (or
  a joke's punchline still to follow), a press is in flight -- and a probe that raises refuses;
* the page, in real Chromium: one tap each way, the action its button showed (never a toggle), a
  double tap is one press, why it cannot open, how it ended, both ways of pressing.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import ceremony, event_cues, event_host, event_order, event_tour, event_walk
from app.config import settings
from app.providers.base import ProviderEvent
from tests.ceremony_app import ceremony_off, ceremony_on
from tests.test_event_order_page import (Page, _at, _order, _scenes, _state, chromium,  # noqa: F401 -- fixtures
                                         real)
from tests.test_reply_stall import rows  # noqa: F401 -- the turn log, captured

ROOT = Path(__file__).resolve().parents[1]
TRACKED_SCRIPT = ROOT / "data" / "ceremony" / "floor1_ceremony.json"


def _closes(rows) -> list:
    return [r["why"] for r in rows if r["event"] == "event_live" and r["action"] == "close"]


def _refused(rows) -> list:
    return [r["why"] for r in rows if r["event"] == "event_live" and r["action"] == "refused"]


@pytest.fixture
def server(real):
    """The real staff server (tests/test_event_order_page.py `real`), with the cue-only ceremony claimed
    the robot's way: the ceremony app's call, "replies": "cues" (the tracked file's)."""
    ceremony_on(replies="cues")
    return real


# ---- the window: the server's brief, not a step of the order ---------------------------------------

def test_the_button_opens_her_window_with_the_servers_brief_never_the_phones_words(server, rows):
    order_before = server.get("/ceremony/state").json()["order"]
    r = server.post("/ceremony/adhoc", json={"open": True, "brief": "say we are sold out", "brief_th": "x",
                                            "cue": "s1_greet", "max_s": 9999}).json()
    assert r["ok"] and r["open"] and r["adhoc"]["open"] is True, r
    live = ceremony.STATE["live"]
    assert live["cue"] == ceremony.ADHOC_ID and live["brief"] == ceremony.ADHOC_BRIEF_TH
    assert ceremony.live_note() == ceremony.ADHOC_NOTE % ceremony.ADHOC_BRIEF_TH
    assert "sold out" not in ceremony.live_note() and "s1_greet" not in ceremony.live_note()
    doc = server.get("/ceremony/state").json()
    a = doc["adhoc"]
    assert a["open"] and a["ready"] and a["why"] is None and a["closed"] is None
    assert abs(a["until"] - a["opened_at"] - 120.0) < 0.5 and a["max_s"] == 120.0     # the server's own bound
    assert doc["mode"]["live"]["cue"] == "adhoc" and doc["mode"]["needs_name"] is True
    # not a step of the running order: no mark, no live step, the order as it was
    assert doc["order"]["live"] is None and event_order.STATE["marks"] == {}
    assert doc["order"]["scenes"] == order_before["scenes"] and doc["order"]["next"] == order_before["next"]
    assert [r for r in rows if r["event"] == "event_live" and r["action"] == "open"] == [
        {"event": "event_live", "action": "open", "cue": "adhoc", "max_s": 120.0, "brief": True}]
    # pressed again from a page not redrawn yet (or a second phone): nothing moves
    again = server.post("/ceremony/adhoc", json={"open": True}).json()
    assert again["ok"] and again["already"] and ceremony.STATE["live"]["opened_at"] == live["opened_at"]
    assert len([r for r in rows if r["event"] == "event_live" and r["action"] == "open"]) == 1
    # "กลับเข้าบท"
    back = server.post("/ceremony/adhoc", json={"open": False}).json()
    assert back["ok"] and back["closed"] is True and back["adhoc"]["open"] is False
    assert back["adhoc"]["closed"]["why"] == "staff" and "กลับเข้าบท" in back["adhoc"]["closed"]["why_th"]
    assert ceremony.live_state() is None and _closes(rows) == ["staff"]
    # closed already: nothing to close, no row
    assert server.post("/ceremony/adhoc", json={"open": False}).json()["closed"] is False
    assert _closes(rows) == ["staff"]


@pytest.mark.parametrize("body", [{}, {"open": "true"}, {"open": 1}, {"open": None}, {"toggle": True}])
def test_the_button_says_which_way_never_toggle(server, body):
    assert server.post("/ceremony/adhoc", json=body).status_code == 400
    assert ceremony.live_state() is None


def test_the_brief_is_instructions_only_and_its_id_no_cue_of_the_script():
    brief = ceremony.ADHOC_BRIEF_TH
    assert len(brief) < 600                               # live_open keeps 600
    # no sentence of hers to say word for word (CLAUDE.md 27 Oct), no number of its own
    for mark in ("«", "»", '"', "'", "“", "”"):
        assert mark not in brief, mark
    assert not any(ch.isdigit() for ch in brief)
    for rule in ("ภาษาที่เขาพูด", "ภาษาอังกฤษ", "ข้อมูลที่อนุมัติ", "ค้น", "ห้ามแนะนำตัวใหม่",
                 "ห้ามพูดถึงบทพิธี ทีมงาน ปุ่ม", "กฎราคา", "สองประโยค", "ไม่ถามกลับ",
                 # review 23 Nov: "ห้ามทักทาย" fought "answer what he asks" ("Emma, say hello to our guests")
                 "ทักทายแขกให้ทักสั้นๆ ได้", "ตอบรับสั้นๆ"):
        assert rule in brief, rule
    assert "ห้ามทักทาย" not in brief
    cues = json.loads(TRACKED_SCRIPT.read_text(encoding="utf-8"))["cues"]
    assert ceremony.ADHOC_ID not in {c.get("id") for c in cues}


def test_her_live_answers_rule_is_in_the_ceremony_prompt_and_nowhere_else():
    """Owner 23 Nov, after the live check: banter got the sales room's refusal and a question back, and
    answers ran 18-27 s -- the brief reaches the model only beside a tool result. The rule is the ceremony
    block's (the system prompt); every other profile's prompt is what it was, byte for byte."""
    from app.prompts import build_instructions

    rule = ceremony.ADHOC_PROMPT_TH
    for mark in ("«", "»", '"', "“", "”"):
        assert mark not in rule, mark
    assert not any(ch.isdigit() for ch in rule)
    for words in ("หนึ่งหรือสองประโยคสั้นๆ", "คำทักทาย คำชม หรือคำถามเล่นๆ", "ไม่ต้องบอกว่าตอบได้แค่เรื่องโครงการ",
                  "โดยไม่แต่งข้อมูล", "ไม่ถามกลับ", "ไม่แนะนำตัวใหม่",
                  # the lines staff press for her to read are said whole (read mode re-measured, 23 Nov)
                  "ไม่ใช้กับบทพิธีที่ทีมงานกดให้อ่าน",
                  # review round 2: which rule wins over the sales room's, said; and the window unmentioned
                  "ใช้แทนกฎความยาวและคำถามท้ายคำตอบของห้องขาย", "ไม่พูดถึงบทพิธี ทีมงานที่คุมพิธี ปุ่ม หรือช่วงตอบสด"):
        assert words in rule, words
    assert rule in event_host.ceremony_block()
    for profile in ("condo", "event", "emma", "joke", "translator"):
        for face in (False, True):
            text = build_instructions(settings.project_name, languages=settings.reply_languages,
                                      robot_name=settings.robot_name, profile=profile, robot_face=face)
            assert rule not in text and "ช่วงตอบสด" not in text, (profile, face)
    assert rule in build_instructions(settings.project_name, languages=settings.reply_languages,
                                      robot_name=settings.robot_name, profile="ceremony", robot_face=True)


# ---- nothing she hears is widened ---------------------------------------------------------------------

def _t(text):
    return ProviderEvent(kind="assistant_transcript", text=text)


def test_she_answers_only_when_named_and_nothing_before_the_tap(rows):
    from app.providers.gemini import GeminiProvider

    provider = GeminiProvider("Kore", "x")
    ceremony_on(replies="cues")
    assert event_host.mic_deaf()                          # the robot still, no window: silence goes up
    assert ceremony.adhoc_open()["ok"]
    assert not event_host.mic_deaf() and not event_host.cue_only_closed()
    gate = provider._noise_gate
    assert not gate.named_recently(), "the tap is not her name: nothing is let in by it"
    gate.segment(0.2)
    gate.heard("So, how many units are there in total?")
    assert provider._gated(_t("There are many units.")) == []
    provider._noise_close()
    assert [r["why"] for r in rows if r["event"] == "noise_reply_dropped"] == ["not_named"]
    gate.segment(0.2)
    gate.heard("Emma, how many units are there?")
    first = _t("There are many units.")
    assert provider._gated(first) == [first], "named: the live window's answer"
    provider._noise_close()
    ceremony.adhoc_close("staff")
    assert event_host.mic_deaf() and event_host.cue_only_closed()


def test_after_her_line_the_door_is_the_named_exchange_it_always_was(rows):
    """Said as it is (review 23 Nov), not as "only her name": a line of hers opens the named exchange for
    30 s from its audio's end (event_cues `_named` -> mark_named) -- the host's unnamed follow-up gets in,
    in this window as in the order's own. The page says so ("หรือภายใน 30 วิหลังเธอพูด"); nothing here
    widens it, and past it only her name does."""
    from app.providers.gemini import GeminiProvider
    from app.providers.transcript_filter import NAMED_WINDOW_S

    assert NAMED_WINDOW_S == 30.0
    provider = GeminiProvider("Kore", "x")
    ceremony_on(replies="cues")
    gate = provider._noise_gate
    gate.mark_named(time.monotonic())                     # her scripted line just ended
    assert ceremony.adhoc_open()["ok"]
    gate.segment(0.2)
    gate.heard("Wow, that is impressive. How tall is the building?")
    first = _t("It rises twenty-three metres.")
    assert provider._gated(first) == [first]
    provider._noise_close()
    gate._named_at = time.monotonic() - NAMED_WINDOW_S - 1    # her last word is long past
    gate.segment(0.2)
    gate.heard("Ladies and gentlemen, a big round of applause please.")
    assert provider._gated(_t("Thank you.")) == []
    provider._noise_close()
    assert [r["why"] for r in rows if r["event"] == "noise_reply_dropped"] == ["not_named"]


class _Upstream:
    def __init__(self):
        self.nudges = 0

    async def send_client_content(self, **kw):
        self.nudges += 1


@pytest.mark.parametrize("closed_before", ["rejoin", "after_rejoin", "never"])
def test_the_reply_watchdog_stops_once_the_window_has_closed(monkeypatch, rows, closed_before):
    """Review 23 Nov: the host named her inside the window, Gemini sat on it, step 1 nudged -- then staff
    pressed "กลับเข้าบท" (or a line, a walk, the red stop). Steps 2-3 used to rejoin and ask his words
    back as TEXT, a server turn the cue-only gate plays: her live answer to an old question in the middle
    of the script, then the call moved to the fallback model.

    24 Nov (owner's decision the day before): the same for a window that stays OPEN -- a cue-only
    ceremony's chase ends at step 1 (one empty "your turn") whatever the window, so the old "never"
    case (the chase goes on while it is open) is now "it still stops". The three cases only differ in
    when staff close the window; none may rejoin or ask text. See tests/test_ceremony_stall.py."""
    import asyncio

    from app.providers.gemini import GeminiProvider

    monkeypatch.setattr(settings, "reply_stall_s", 0.05)
    monkeypatch.setattr(settings, "reply_stall_reconnect_s", 0.15)
    monkeypatch.setattr(settings, "gemini_model_fallback", "")
    provider = GeminiProvider("Kore", "x")
    ceremony_on(replies="cues")
    assert ceremony.adhoc_open()["ok"]
    up = _Upstream()
    provider._session, provider._closing, provider._reconnecting = up, False, False
    provider._stall_heard = "Emma, how deep is the pool?"
    sent, rejoined = [], []

    async def send_text(text, **kw):
        sent.append(text)

    async def cut_over():
        rejoined.append(True)
        if closed_before == "after_rejoin":
            ceremony.adhoc_close("staff")                 # pressed while the rejoin was dialling
        return True

    provider.send_text = send_text
    provider._cut_over = cut_over

    async def main():
        since = time.monotonic()
        provider._stall_wait = since
        task = asyncio.create_task(provider._reply_stall_guard(up, since))
        await asyncio.sleep(0.1)                          # step 1 went: the window was open
        if closed_before == "rejoin":
            ceremony.adhoc_close("staff")                 # "กลับเข้าบท" before step 2
        await task

    asyncio.run(main())
    stages = [r["stage"] for r in rows if r["event"] == "reply_stall"]
    assert sent == [], "his words were asked back as text in a cue-only ceremony"
    assert rejoined == [], "the ceremony call was rejoined by the watchdog"
    assert up.nudges == 1 and stages == ["nudge"], "one empty 'your turn', nothing after it"


# ---- refused, with the reason --------------------------------------------------------------------------

class _NotDone:
    def done(self):
        return False


def _boom():
    raise RuntimeError("probe broke")


#: (why, how): the state that refuses it, made the module's own way.
REFUSALS = {
    "not_ceremony_app": lambda mp: ceremony_off(),
    "named": lambda mp: ceremony_on(replies="named"),
    "order_live": lambda mp: ceremony.live_open(cue_id="s4_recommend", brief_th="แนะนำห้อง"),
    "opening": lambda mp: mp.setattr(event_tour, "ceremony_running", lambda: True),
    "moving:walk": lambda mp: mp.setattr(event_walk, "walking", lambda: True),
    "moving:gesture": lambda mp: mp.setattr(event_host, "robot_moving", lambda: True),
    "line:audible": lambda mp: mp.setattr(event_cues, "line_audible", lambda now=None: True),
    "line:order": lambda mp: event_order.STATE["marks"].update(
        {"1.2": {"state": "running", "cue": "s1_greet", "at": time.time(), "since": time.monotonic(),
                 "at_step": (1, 2), "error_th": None}}),
    "line:chain": lambda mp: event_order.STATE.update(chain={"state": "waiting", "task": _NotDone()}),
    "busy": lambda mp: event_order.STATE.update(pressing=object()),
    "unknown": lambda mp: mp.setattr(event_tour, "ceremony_running", _boom),
}


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_refused_with_its_reason_and_a_row(case, rows, monkeypatch):
    ceremony_on(replies="cues")
    REFUSALS[case](monkeypatch)
    why = case.split(":")[0]
    before = ceremony.STATE["live"]
    out = ceremony.adhoc_open()
    assert out == {"ok": False, "error": why, "error_th": ceremony.ADHOC_REFUSED_TH[why]}, out
    assert ceremony.STATE["live"] is before               # nothing opened (the order's own window stays)
    assert _refused(rows) == [why]
    snap = ceremony.adhoc_snapshot()
    assert snap["ready"] is False and snap["open"] is False and snap["why"] == why
    assert snap["why_th"] == ceremony.ADHOC_REFUSED_TH[why]


def test_ready_when_nothing_refuses(monkeypatch):
    ceremony_on(replies="cues")
    snap = ceremony.adhoc_snapshot()
    assert snap == {"open": False, "opened_at": None, "until": None, "max_s": 120.0, "ready": True, "why": None,
                    "why_th": None, "closed": None, "warn_th": None}


def test_a_mic_muted_at_the_robot_is_said_not_refused(monkeypatch):
    ceremony_on(replies="cues")
    monkeypatch.setattr(event_host, "_mic_closed", lambda: True)
    snap = ceremony.adhoc_snapshot()
    assert snap["ready"] is True and snap["warn_th"] == ceremony.ADHOC_MIC_TH
    out = ceremony.adhoc_open()
    assert out["ok"] and out["warn_th"] == ceremony.ADHOC_MIC_TH
    ceremony_off()
    assert ceremony.adhoc_snapshot()["warn_th"] is None    # not the ceremony's call: not its word


def test_a_press_holds_it_shut_until_the_press_has_answered(server, rows, monkeypatch):
    """Review 23 Nov: closed first, then the press awaited (a line waiting for her voice, a walk's pose
    read) -- and a tap from a second phone in that await opened the window again under the press."""
    during = []

    async def play(*a, **k):
        during.append(ceremony.adhoc_open())             # the second phone's tap, mid-press
        return {"ok": True, "mode": "recorded"}

    monkeypatch.setattr(event_cues, "play", play)
    assert server.post("/ceremony/adhoc", json={"open": True}).json()["open"]
    r = server.post("/ceremony/cue", json={"id": "s1_greet"}).json()
    assert r["adhoc_closed"] is True and during[0]["error"] == "busy", during
    assert ceremony.live_state() is None and ceremony.STATE["script_presses"] == 0
    assert server.post("/ceremony/adhoc", json={"open": True}).json()["open"], "answered: free again"

    async def broken(*a, **k):
        raise RuntimeError("the press blew up")

    monkeypatch.setattr(event_cues, "play", broken)
    with pytest.raises(RuntimeError):
        server.post("/ceremony/cue", json={"id": "s1_greet"})
    assert ceremony.STATE["script_presses"] == 0, "a press that raised holds nothing"


def test_the_window_does_not_drive_the_big_screen(rows, monkeypatch):
    """Review 23 Nov: a window staff open at any moment must not let "Emma, play the video" roll a film or
    switch the screen under the screen team's show. Her words, a search, a picture and the stops stay;
    the order's own live step keeps every tool it had."""
    import asyncio

    from app.tools import registry
    from app.tools import screens  # noqa: F401 -- registers event_clip and room_screen

    ceremony_on(replies="cues")
    assert ceremony.adhoc_open()["ok"]
    monkeypatch.setattr(registry, "allowed", lambda entry: True)
    ran = []

    async def tool(**k):
        ran.append(k)
        return {"ok": True}

    for name in sorted(ceremony.ADHOC_NO_TOOLS):
        monkeypatch.setattr(registry._REGISTRY[name], "handler", tool)
        out = asyncio.run(registry._dispatch(name, {}))
        assert out == {"ok": False, "error": "adhoc", "instruction": ceremony.ADHOC_TOOL_REFUSAL}, name
    assert ran == [] and [r["why"] for r in rows if r["event"] == "tool_refused"] == ["adhoc", "adhoc"]
    assert not ceremony.adhoc_refuses("search_condo_info") and not ceremony.adhoc_refuses("present_story")
    assert not ceremony.adhoc_refuses("move_body") and not ceremony.adhoc_refuses("stop_moving")
    ceremony.adhoc_close("staff")
    assert ceremony.live_open(cue_id="s4_recommend", brief_th="แนะนำห้อง")
    assert not ceremony.adhoc_refuses("event_clip") and not registry._adhoc_refuses("event_clip")
    for quoted in ("«", '"', "“"):
        assert quoted not in ceremony.ADHOC_TOOL_REFUSAL


def test_the_orders_own_live_step_is_not_this_buttons(server, rows):
    assert ceremony.live_open(cue_id="s4_recommend", brief_th="แนะนำห้อง")
    assert ceremony.adhoc_close("staff") is False
    r = server.post("/ceremony/adhoc", json={"open": False}).json()
    assert r["closed"] is False and ceremony.live_state()["cue"] == "s4_recommend"
    # a press of the order closes "ตอบสด" only: the order's own window is its step's to close
    server.post("/ceremony/order/step", json={"n": 1, "i": 3})
    assert ceremony.live_state()["cue"] == "s4_recommend" and _closes(rows) == []


# ---- back to the script: every press of it closes the window FIRST ----------------------------------

#: route -> (its body, the module and function its press runs, the close's why)
PRESSES = {
    "/ceremony/cue": ({"id": "s1_greet"}, event_cues, "play", "script"),
    "/ceremony/order/step": ({"n": 1, "i": 3}, event_order, "press", "script"),
    "/ceremony/order/next": ({"n": 1, "i": 0}, event_order, "next", "script"),
    "/ceremony/order/opening-hand": ({"n": 1, "i": 0, "k": 0}, event_order, "opening_hand", "script"),
    "/ceremony/order/reset": ({}, event_order, "reset_order", "reset"),
    "/ceremony/box-finish": ({}, event_host, "box_finish", "script"),
}


@pytest.mark.parametrize("path", sorted(PRESSES))
def test_a_press_of_the_script_closes_it_before_the_press_runs(server, rows, monkeypatch, path):
    body, module, name, why = PRESSES[path]
    seen = []

    def recorder(*a, **k):
        seen.append(ceremony.live_state())                # what the press would run under
        return {"ok": True}

    async def arecorder(*a, **k):
        return recorder()

    monkeypatch.setattr(module, name, recorder if name == "reset_order" else arecorder)
    assert server.post("/ceremony/adhoc", json={"open": True}).json()["open"]
    r = server.post(path, json=body).json()
    assert seen == [None], "the window was still open when the press ran"
    assert r["ok"] and r["adhoc_closed"] is True, r
    assert _closes(rows) == [why]
    assert server.get("/ceremony/state").json()["adhoc"]["closed"]["why"] == why
    # with no window open the answer says nothing of it
    r = server.post(path, json=body).json()
    assert "adhoc_closed" not in r and seen == [None, None]


#: The POST /ceremony/* routes that are NOT a press of the script: the button itself, a skip (nothing
#: of the step runs -- staff skip what the host ad-libbed past while he is still talking), the lights card.
NOT_THE_SCRIPT = {"/ceremony/adhoc": {"open": True}, "/ceremony/order/skip": {"n": 1, "i": 3},
                  "/ceremony/device": {"device": "lobby_lights", "action": "off"},
                  # "ทั้งพิธีคุยสด" (24 Nov): the mode's own control -- it holds the window open, never a press of the script
                  "/ceremony/follow": {"action": "off"}}


def test_every_ceremony_press_closes_it_but_the_button_a_skip_and_the_lights(server):
    """The routes are walked: a POST /ceremony/* added later that forgets `_back_to_script` goes red
    here, not on the stage (the conftest browser-registry test's way)."""
    from app import event_control_server

    routes = {r.path for r in event_control_server.app.routes
              if getattr(r, "path", "").startswith("/ceremony/") and "POST" in (getattr(r, "methods", None) or ())}
    assert routes == set(PRESSES) | set(NOT_THE_SCRIPT), routes ^ (set(PRESSES) | set(NOT_THE_SCRIPT))
    for path in sorted(set(PRESSES)):
        assert server.post("/ceremony/adhoc", json={"open": True}).json()["open"], path
        server.post(path, json={})                        # well-formed or not: the host is back on the script
        assert ceremony.live_state() is None, path
    for path, body in NOT_THE_SCRIPT.items():
        assert server.post("/ceremony/adhoc", json={"open": True}).json()["open"]
        server.post(path, json=body)
        assert ceremony.live_state() is not None and ceremony.live_state()["cue"] == "adhoc", path


def test_the_red_stop_closes_it_and_says_so(server, rows, monkeypatch):
    from app import robot_face_link

    async def request_stop(first, kind, timeout):
        return {"accepted": True, "reason": None, "via": "call"}

    monkeypatch.setattr(robot_face_link, "request_stop", request_stop)
    assert server.post("/event/stop", json={}).json()["live_closed"] is False      # nothing open: nothing said
    assert server.post("/ceremony/adhoc", json={"open": True}).json()["open"]
    stop = server.post("/event/stop", json={}).json()
    assert stop["live_closed"] is True and ceremony.live_state() is None
    assert _closes(rows) == ["staff_stopped"]           # its own reason, first: the page says "กดหยุดแดง"
    assert "หยุดแดง" in ceremony.adhoc_snapshot()["closed"]["why_th"]


# ---- its own end, the app closing -------------------------------------------------------------------------

def test_its_own_end_closes_it_with_a_note_for_the_page(rows, monkeypatch):
    ceremony_on(replies="cues")
    monkeypatch.setattr(settings, "ceremony_adhoc_max_s", 0.05)
    assert ceremony.adhoc_open()["ok"]
    time.sleep(0.1)
    snap = ceremony.adhoc_snapshot()
    assert snap["open"] is False and snap["closed"]["why"] == "expired"
    assert "ปิดเองแล้ว (ครบ 0:00)" in snap["closed"]["why_th"] and "กดตอบสดอีกครั้ง" in snap["closed"]["why_th"]
    assert snap["ready"] is True                         # and may open again at once
    assert _closes(rows) == ["expired"]
    monkeypatch.setattr(ceremony, "ADHOC_CLOSED_SHOW_S", 0.0)
    time.sleep(0.01)
    assert ceremony.adhoc_snapshot()["closed"] is None    # said for a while, not for ever
    # opening again clears the note at once
    monkeypatch.setattr(ceremony, "ADHOC_CLOSED_SHOW_S", 30.0)
    monkeypatch.setattr(settings, "ceremony_adhoc_max_s", 120.0)
    assert ceremony.adhoc_snapshot()["closed"]["why"] == "expired"
    assert ceremony.adhoc_open()["ok"] and ceremony.adhoc_snapshot()["closed"] is None


def test_its_bound_and_the_ceremonys_cap(monkeypatch):
    assert ceremony.adhoc_max_s() == 120.0
    monkeypatch.setattr(settings, "ceremony_adhoc_max_s", 600.0)
    monkeypatch.setattr(settings, "event_ceremony_max_s", 30.0)
    assert ceremony.adhoc_max_s() == 30.0
    ceremony_on(replies="cues")
    assert ceremony.adhoc_open()["ok"]
    live = ceremony.STATE["live"]
    assert abs(live["until"] - live["opened_at"] - 30.0) < 0.1
    monkeypatch.setattr(settings, "event_ceremony_max_s", 10800.0)
    for broken in (float("nan"), float("inf"), -5.0, 0.0, "abc"):
        monkeypatch.setattr(settings, "ceremony_adhoc_max_s", broken)
        assert ceremony.adhoc_max_s() == 120.0, broken


def test_the_app_closing_closes_it(rows):
    ceremony_on(replies="cues")
    assert ceremony.adhoc_open()["ok"]
    ceremony_off()
    assert ceremony.live_state() is None and _closes(rows) == ["app_closed"]
    snap = ceremony.adhoc_snapshot()
    assert snap["closed"]["why"] == "app_closed" and snap["why"] == "not_ceremony_app" and not snap["ready"]


def test_the_state_poll_never_breaks_on_it(server, monkeypatch):
    def broken():
        raise RuntimeError("ceremony unreadable")

    monkeypatch.setattr(ceremony, "adhoc_snapshot", broken)
    r = server.get("/ceremony/state")
    assert r.status_code == 200 and "adhoc" not in r.json() and "order" in r.json()


# ---- her live answer's picture comes off at the next press (owner 23 Nov) ----------------------------
# "the picture stays until the next line is pressed": a picture of her ตอบสด answer (present_story, a unit
# card, an answer picture) is taken off the robot face and the event screen by the next press of the
# script, through the clear a stopped cue's picture takes -- never a cue's own picture (`show`).
# `own` = her own tool's result (session's receive loop); False = the server's own (a cue's `show`, a clear).

class _FaceCall:
    """The ceremony app's call as the press reaches it: the robot face's own, with the session's clear
    (VoiceSession._story_picture) recorded."""

    def __init__(self, fail=False):
        self.profile, self.provider, self._robot_face_claim, self._page_muted = "ceremony", None, True, False
        self.cleared, self.fail = [], fail

    async def _story_picture(self, data):
        if self.fail:
            raise RuntimeError("the socket broke")
        self.cleared.append(dict(data))


STORY = {"type": "tool_result", "name": "present_story", "result": {"ok": True, "screen": "story", "story": {}}}
ANSWER_PICTURE = {"type": "tool_result", "name": "search_condo_info", "result": {"found": True},
                  "slide": {"url": "/slides/x.png"}}
UNIT_CARD = {"type": "tool_result", "name": "show_unit", "result": {"screen": "unit", "unit": {}}}
PLAN_PENDING = {"type": "tool_result", "name": "show_plan",           # units.py: drawn only after a render ACK
                "result": {"ok": False, "pending_display": True, "screen": "plan"}}
CLEAR = {"type": "tool_result", "name": "event_cue", "result": {"screen": "clear"}}
WORDS_ONLY = {"type": "tool_result", "name": "search_condo_info", "result": {"found": True}}


@pytest.fixture
def call(server):
    face_call = _FaceCall()
    ceremony_on(replies="cues", session=face_call)
    return face_call


@pytest.mark.parametrize("picture", [STORY, ANSWER_PICTURE, UNIT_CARD], ids=["story", "answer_picture", "unit"])
def test_her_live_answers_picture_comes_off_at_the_next_press(server, call, rows, picture):
    assert server.post("/ceremony/adhoc", json={"open": True}).json()["open"]
    ceremony.note_face_message(call, picture, own=True)  # her answer to the host, with its picture
    ceremony.note_face_message(call, WORDS_ONLY, own=True)   # (a later answer with words only keeps it)
    r = server.post("/ceremony/order/step", json={"n": 1, "i": 3}).json()     # the next line of the script
    assert r["ok"] and r["adhoc_closed"] is True
    assert call.cleared == [{"clear": True, "cue": "adhoc"}]
    assert [x for x in rows if x["event"] == "event_live" and x["action"] == "picture_cleared"] == [
        {"event": "event_live", "action": "picture_cleared", "cue": "adhoc"}]
    server.post("/ceremony/order/step", json={"n": 1, "i": 3})
    assert len(call.cleared) == 1, "taken off once: the press after it has nothing of hers to take"


def test_back_to_the_script_first_the_picture_at_the_next_line(server, call):
    """"กลับเข้าบท" (or the window's own end) shuts the window; the picture stays until the next line is
    pressed -- what the owner was told."""
    assert server.post("/ceremony/adhoc", json={"open": True}).json()["open"]
    ceremony.note_face_message(call, STORY, own=True)
    server.post("/ceremony/adhoc", json={"open": False})
    assert call.cleared == []
    server.post("/ceremony/order/reset", json={})        # not a line: the reset leaves it
    server.post("/ceremony/order/skip", json={"n": 1, "i": 3})
    assert call.cleared == []
    server.post("/ceremony/cue", json={"id": "nope"})    # a line pressed (refused or not): off
    assert call.cleared == [{"clear": True, "cue": "adhoc"}]


def test_a_picture_of_hers_that_lands_after_the_press_comes_down_too(server, call):
    """Review round 2 (must-fix): her search (with its answer picture) or story still running when staff
    pressed the next line lands 0.5-2 s later. It used to wipe the mark and stay under every later line."""
    async def late(*a, **k):
        # inside the press (a line waiting for her voice): her late result arrives now
        ceremony.note_face_message(call, ANSWER_PICTURE, own=True)
        await asyncio.sleep(0.05)                        # the clear it spawned goes out
        return {"ok": True, "mode": "recorded"}

    import asyncio

    from app import event_cues

    monkey = pytest.MonkeyPatch()
    monkey.setattr(event_cues, "play", late)
    try:
        assert server.post("/ceremony/adhoc", json={"open": True}).json()["open"]
        server.post("/ceremony/cue", json={"id": "s1_greet"})
    finally:
        monkey.undo()
    assert call.cleared == [{"clear": True, "cue": "adhoc"}], "not under the line it was pressed for"
    assert ceremony.STATE["adhoc_picture"] is False


def test_a_late_picture_after_the_window_ended_by_itself_waits_for_the_next_press(server, call, monkeypatch):
    monkeypatch.setattr(settings, "ceremony_adhoc_max_s", 0.05)
    assert server.post("/ceremony/adhoc", json={"open": True}).json()["open"]
    time.sleep(0.1)
    assert ceremony.live_state() is None                 # its own end
    ceremony.note_face_message(call, STORY, own=True)    # her answer's story, late
    assert call.cleared == [] and ceremony.STATE["adhoc_picture"] is True
    server.post("/ceremony/order/step", json={"n": 1, "i": 3})
    assert call.cleared == [{"clear": True, "cue": "adhoc"}]


@pytest.mark.parametrize("case", ["no_picture", "cue_picture_before", "cue_picture_after", "cleared_already",
                                  "plan_not_drawn", "order_live_step", "order_live_step_late"])
def test_nothing_but_her_live_answers_picture_is_taken(server, call, case):
    if case == "cue_picture_before":
        ceremony.note_face_message(call, STORY, own=False)   # a cue's own `show`, up before ตอบสด opened
    if case == "order_live_step_late":
        # ...and its answer's picture landing after that step closed: still not this button's (`adhoc_recent`)
        assert ceremony.live_open(cue_id="s4_recommend", brief_th="แนะนำห้อง")
        ceremony.live_close("done")
        ceremony.note_face_message(call, STORY, own=True)
        server.post("/ceremony/order/step", json={"n": 1, "i": 3})
        assert call.cleared == []
        return
    if case == "order_live_step":
        # the order's own live step answers with a picture: not this button's
        assert ceremony.live_open(cue_id="s4_recommend", brief_th="แนะนำห้อง")
        ceremony.note_face_message(call, STORY, own=True)
        ceremony.live_close("done")
        server.post("/ceremony/order/step", json={"n": 1, "i": 3})
        assert call.cleared == []
        return
    assert server.post("/ceremony/adhoc", json={"open": True}).json()["open"]
    if case in ("no_picture", "cue_picture_before"):
        ceremony.note_face_message(call, WORDS_ONLY, own=True)
    elif case == "plan_not_drawn":
        ceremony.note_face_message(call, PLAN_PENDING, own=True)   # review round 2: never drawn
    else:
        ceremony.note_face_message(call, ANSWER_PICTURE, own=True)
    server.post("/ceremony/adhoc", json={"open": False})
    if case == "cue_picture_after":
        ceremony.note_face_message(call, STORY, own=False)   # a cue's own `show` replaced it
    if case == "cleared_already":
        ceremony.note_face_message(call, CLEAR, own=False)   # the red stop's clear, a stopped cue's
    server.post("/ceremony/order/step", json={"n": 1, "i": 3})
    assert call.cleared == [], case


def test_another_call_marks_nothing(server, call):
    other = _FaceCall()
    assert server.post("/ceremony/adhoc", json={"open": True}).json()["open"]
    ceremony.note_face_message(other, STORY, own=True)   # not the ceremony's call
    ceremony.note_face_message(call, "not a dict", own=True)
    server.post("/ceremony/order/step", json={"n": 1, "i": 3})
    assert call.cleared == [] and other.cleared == []


def test_a_clear_that_did_not_go_out_keeps_the_mark(server):
    """Review round 2: the mark went before the clear was known to go out (a press during a re-dial, a
    socket that raised) and the picture stayed up unmarked."""
    broken = _FaceCall(fail=True)
    ceremony_on(replies="cues", session=broken)
    assert server.post("/ceremony/adhoc", json={"open": True}).json()["open"]
    ceremony.note_face_message(broken, STORY, own=True)
    server.post("/ceremony/order/step", json={"n": 1, "i": 3})
    assert ceremony.STATE["adhoc_picture"] is True, "still hers to take down"
    good = _FaceCall()
    ceremony_on(replies="cues", session=good)            # a new call: its face starts clean
    assert ceremony.STATE["adhoc_picture"] is False


def test_the_sessions_own_paths_mark_it_and_its_clear_reaches_the_face_and_the_event_screen(monkeypatch):
    """The real VoiceSession._note_ceremony_picture and _story_picture (the existing clear path), on a
    stand-in session: her own result marks, the press sends the clear to the face's socket and to the event
    screen (face_mirror), and a cue's own picture sent through _story_picture never counts as hers."""
    import asyncio
    import inspect
    import types

    from app import face_mirror
    from app.session import VoiceSession

    monkeypatch.setattr(settings, "mirror_port", 8002)
    monkeypatch.setattr(settings, "multi_session", False)
    sent = []

    async def send_text(text):
        sent.append(json.loads(text))

    fake = types.SimpleNamespace(profile="ceremony", provider=None, _robot_face_claim=True, _page_muted=False,
                                 ws=types.SimpleNamespace(send_text=send_text))
    for name in ("_mirrored", "_send_json", "_note_screen", "_story_picture", "_note_ceremony_picture"):
        setattr(fake, name, types.MethodType(getattr(VoiceSession, name), fake))
    ceremony_on(replies="cues", session=fake)
    face_mirror.reset()
    screen: asyncio.Queue = asyncio.Queue()
    face_mirror._clients.add(screen)
    try:
        async def main():
            assert ceremony.adhoc_open()["ok"]
            fake._note_ceremony_picture(STORY, own=True)     # the receive loop, before sending her result
            assert ceremony.STATE["adhoc_picture"] is True
            async with ceremony.script_press() as closed:
                assert closed is True
            assert sent[-1] == {"type": "tool_result", "name": "event_cue", "result": {"screen": "clear"}}
            assert ceremony.STATE["adhoc_picture"] is False

        asyncio.run(main())
        room = []
        while not screen.empty():
            item = screen.get_nowait()
            if isinstance(item, str):
                room.append(json.loads(item))
        assert room[-1] == {"type": "tool_result", "result": {"screen": "clear"}}, room   # the event screen
    finally:
        face_mirror._clients.discard(screen)
        face_mirror.reset()
    # where the session calls it: her own result right before it is sent; the server's own pictures as not hers
    loop = inspect.getsource(VoiceSession)
    assert ("self._note_ceremony_picture(payload, own=True)      # her own tool's picture\n"
            "                    await self._send_json(payload)") in loop
    story = inspect.getsource(VoiceSession._story_picture)
    assert story.count("self._note_ceremony_picture(") == 2 and "own=True" not in story


# ---- the page, in a real browser -----------------------------------------------------------------------

def _adhoc(open_=False, *, ready=True, why=None, why_th=None, left=103.0, closed=None, warn_th=None) -> dict:
    now = time.time()
    return {"open": open_, "opened_at": now - 17 if open_ else None, "until": now + left if open_ else None,
            "max_s": 120.0, "ready": bool(open_ or ready), "why": why, "why_th": why_th, "closed": closed,
            "warn_th": warn_th}


def _with(adhoc, order=None) -> dict:
    doc = _state(order or _at(1, 2))
    doc["adhoc"] = adhoc
    if adhoc and adhoc["open"]:
        doc["mode"]["live"] = {"cue": "adhoc", "opened_at": adhoc["opened_at"], "until": adhoc["until"], "why": None}
    return doc


def test_the_adhoc_states_the_page_is_driven_with_are_the_servers_own(server):
    doc = server.get("/ceremony/state").json()
    assert set(doc["adhoc"]) == set(_adhoc()), set(doc["adhoc"]) ^ set(_adhoc())
    server.post("/ceremony/adhoc", json={"open": True})
    server.post("/ceremony/adhoc", json={"open": False})
    closed = server.get("/ceremony/state").json()["adhoc"]["closed"]
    assert set(closed) == {"why", "why_th", "at"}


def _adhoc_posts(page) -> list:
    return [body for path, body in page.sent if path == "/ceremony/adhoc"]


class _Server(Page):
    """The fake server's state changes when the press reaches it (`after[path]`), as the real one's
    does -- set before the click, a poll in between drew the new state and the tap was the other way."""

    def __init__(self, *a, **k):
        self.after = {}
        super().__init__(*a, **k)

    def _route(self, route):
        req = route.request
        path = req.url.split("://", 1)[1].split("/", 1)[1].split("?")[0]
        if req.method == "POST" and "/" + path in self.after:
            self.state = self.after.pop("/" + path)
        return super()._route(route)


@pytest.mark.parametrize("mode", ["manual", "order"])
def test_one_tap_opens_one_tap_closes_in_both_ways_of_pressing(chromium, mode):
    page = _Server(chromium, _with(_adhoc()), mode=mode)
    try:
        assert page.visible("#adhocBtn") and page.text("#adhocLabel") == "ตอบสด"
        assert not page.visible("#adhocNow")
        assert page.page.locator("#nextBtn").is_visible() == (mode == "order")
        opened = _adhoc(True, left=120.0)
        page.posts["/ceremony/adhoc"] = {"ok": True, "open": True, "adhoc": opened}
        page.after["/ceremony/adhoc"] = _with(opened)      # the server's state once it opened
        page.page.click("#adhocBtn")
        assert "Emma กำลังฟังคุณมิกิ" in page.toast()
        page.page.click("#adhocBtn")                       # a double tap: still one press
        page.page.wait_for_timeout(200)
        assert _adhoc_posts(page) == [{"open": True}]
        # redrawn from the answer at once, not at the next poll
        assert page.text("#adhocLabel") == "กลับเข้าบท"
        assert page.page.locator("#adhocBtn").get_attribute("aria-pressed") == "true"
        assert "Emma กำลังฟังคุณมิกิ (ตอบสด)" in page.text("#adhocNowTop")
        page.page.wait_for_timeout(1300)                   # a poll, and past the gap after the answer
        assert page.text("#adhocLabel") == "กลับเข้าบท"
        page.clear_toast()
        back = _adhoc(closed={"why": "staff", "why_th": "ปิดแล้ว — กลับเข้าบท", "at": time.time()})
        page.posts["/ceremony/adhoc"] = {"ok": True, "open": False, "closed": True, "adhoc": back}
        page.after["/ceremony/adhoc"] = _with(back)
        page.page.click("#adhocBtn")
        assert "กลับเข้าบทแล้ว" in page.toast()
        assert _adhoc_posts(page) == [{"open": True}, {"open": False}]   # the action it showed, each time
        assert page.text("#adhocLabel") == "ตอบสด"
    finally:
        page.close()


def test_open_says_she_listens_counts_down_and_keeps_stop_at_the_edge(chromium):
    page = Page(chromium, _with(_adhoc(True, left=103.0)), width=360, mode="order")
    try:
        top = page.text("#adhocNowTop")
        assert top.startswith("Emma กำลังฟังคุณมิกิ (ตอบสด)") and ("ปิดเองใน 1:43" in top or "ปิดเองใน 1:42" in top), top
        assert 'เรียกชื่อ "Emma"' in page.text("#adhocNowSub") and "กลับเข้าบทเอง" in page.text("#adhocNowSub")
        assert "is-open" in page.page.locator("#adhocBtn").get_attribute("class")
        assert "คุณมิกิพูดนอกบท" in page.text("#modeSub")
        stop = page.page.locator("#stopBtn").bounding_box()
        for sel in ("#adhocBtn", "#nextBtn", "#adhocNow"):
            box = page.page.locator(sel).bounding_box()
            assert box["y"] + box["height"] <= stop["y"], sel                     # STOP keeps the bottom edge
        assert stop["height"] >= 72 and stop["y"] + stop["height"] >= 860 - 12
        assert page.page.evaluate("document.documentElement.scrollWidth") <= 360   # no sideways scroll
        # the last step of the page is not hidden under the taller bar
        pad = page.page.evaluate("parseFloat(getComputedStyle(document.body).paddingBottom)")
        bar = page.page.locator(".stopbar").bounding_box()
        assert pad >= bar["height"], (pad, bar)
    finally:
        page.close()


def test_not_ready_says_why_and_sends_nothing(chromium):
    why_th = ceremony.ADHOC_REFUSED_TH["line"]
    page = Page(chromium, _with(_adhoc(ready=False, why="line", why_th=why_th)), mode="manual")
    try:
        assert "is-unready" in page.page.locator("#adhocBtn").get_attribute("class")
        assert page.text("#adhocTop") == "ยังเปิดไม่ได้"
        page.page.click("#adhocBtn")
        assert page.toast() == "ตอบสดยังเปิดไม่ได้: " + why_th
        assert _adhoc_posts(page) == []
    finally:
        page.close()


def test_closed_by_itself_is_said_out_loud(chromium):
    page = Page(chromium, _with(_adhoc(True, left=2.0)), mode="manual")
    try:
        why_th = "ปิดเองแล้ว (ครบ 2:00) — ถ้าคุณมิกิยังด้นสดอยู่ กดตอบสดอีกครั้ง"
        page.redraw(_with(_adhoc(closed={"why": "expired", "why_th": why_th, "at": time.time()})))
        assert page.toast() == "ตอบสด: " + why_th
        assert "is-closed" in page.page.locator("#adhocNow").get_attribute("class")
        assert page.text("#adhocNowTop") == "ตอบสด: " + why_th and page.text("#adhocLabel") == "ตอบสด"
    finally:
        page.close()


def test_a_press_of_the_script_that_closed_it_says_so(chromium):
    page = Page(chromium, _with(_adhoc(True)), mode="order")
    try:
        page.posts["/ceremony/order/next"] = {"ok": True, "n": 1, "i": 2, "do": "cue", "adhoc_closed": True,
                                              "result": {"ok": True, "mode": "recorded"}}
        page.page.click("#nextBtn")
        assert page.toast().startswith("กลับเข้าบท (ปิดตอบสดแล้ว) · ")
        assert [p for p, _ in page.sent] == ["/ceremony/order/next"]     # one press, nothing of the button's
    finally:
        page.close()


def test_an_older_server_shows_no_button(chromium):
    page = Page(chromium, _state(_at(1, 2)), mode="manual")      # no `adhoc` in its state
    try:
        assert not page.visible("#adhocBtn") and not page.visible("#adhocNow")
        assert page.page.locator("#barRow").is_hidden()          # no empty row over STOP
        page.redraw(_with(_adhoc(True)))
        assert page.visible("#adhocBtn") and page.visible("#adhocNow")
        page.redraw(_state(_at(1, 2)))                            # unreadable on the server: gone again
        assert not page.visible("#adhocBtn") and not page.visible("#adhocNow")
    finally:
        page.close()


def test_a_muted_robot_mic_is_on_the_bar(chromium):
    from app import ceremony as cer

    page = Page(chromium, _with(_adhoc(True, warn_th=cer.ADHOC_MIC_TH)), mode="manual")
    try:
        assert page.text("#adhocNowSub").startswith("⚠ " + cer.ADHOC_MIC_TH)
    finally:
        page.close()


def test_a_window_gone_with_no_word_of_how_is_still_said(chromium):
    """A server restart (or this phone offline past the note): open, then closed with no `closed` record."""
    page = Page(chromium, _with(_adhoc(True)), mode="manual")
    try:
        page.redraw(_with(_adhoc()))
        assert page.toast() == "ตอบสด: ปิดแล้ว — ถ้าคุณมิกิยังด้นสดอยู่ กดตอบสดอีกครั้ง"
    finally:
        page.close()


@pytest.mark.parametrize("width", [320, 360])
def test_its_words_fit_beside_next_on_a_narrow_phone(chromium, width):
    """Review 23 Nov: at 320 px "กลับเข้าบท" was cut to an ellipsis, at 360 px the not-ready line."""
    for adhoc in (_adhoc(True), _adhoc(ready=False, why="line", why_th="x"), _adhoc()):
        page = Page(chromium, _with(adhoc), width=width, mode="order")
        try:
            for sel in ("#adhocLabel", "#adhocTop"):
                fits = page.page.evaluate(
                    "(s) => { const e = document.querySelector(s); return e.scrollWidth <= e.clientWidth; }", sel)
                assert fits, (width, sel, page.text(sel))
            assert page.page.evaluate("document.documentElement.scrollWidth") <= width
        finally:
            page.close()

