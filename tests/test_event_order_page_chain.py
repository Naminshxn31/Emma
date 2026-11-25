"""The ceremony page's joke said straight through (owner 23 Nov 2026: "ฉากที่เล่นมุข ... ให้พูดต่อเนื่องเลย
ไม่ต้องเว้นช่วง"): a step of a chained pair carries `chain` (app/event_order.py `_chain_views`), and the
page says what the pair does by itself -- the punchline that is coming asks for no tap, in either way of
pressing; the one that did not follow says why at full strength, and is a press like any other.

Driven in headless Chromium with the network faked, from states in the server's own shape (held to the
real /ceremony/state below); the page and its harness are tests/test_event_order_page.py's.
"""
from __future__ import annotations

import json

from app import event_order, event_script
from app.config import settings
from tests import test_event_order_page as tep
from tests.test_event_order_page import chromium, real  # noqa: F401 -- fixtures, used by name

SETUP_TH = "ขอบคุณค่ะคุณมิกิ เอ็มม่าศึกษามาอย่างดีแล้วค่ะ..."
PUNCH_TH = "ลงไปว่ายน้ำในสระด้วยตัวเองค่ะ!"


def _chain(role: str, state, *, th=None, warn=False) -> dict:
    if th is None:
        th = event_order.CHAIN_LEAD_TH if role == "lead" else {
            "waiting": event_order.CHAIN_WAITING_TH, "pressed": event_order.CHAIN_PRESSED_TH}.get(
                state, event_order.CHAIN_FOLLOW_TH)
    return {"role": role, "state": state, "th": th, "warn": warn}


def _joke(state, *, lead_mark=None, follow_th=None, warn=False, running=None, lead_chain=None, nxt=None) -> dict:
    """One scene: the set-up (1.0, chained), its punchline (1.1), the line after the laughter (1.2);
    "ถัดไป" at the punchline once the set-up was pressed (unless `nxt` says where)."""
    lead = tep._step(0, "cue", "Emma: «ขอบคุณค่ะคุณมิกิ…»", line_th=SETUP_TH, voice="read", mark=lead_mark,
                     note_th="มุกเกริ่น — กดครั้งเดียว")
    lead["chain"] = lead_chain or _chain("lead", state)
    follow = tep._step(1, "cue", "Emma: «ลงไปว่ายน้ำ…»", line_th=PUNCH_TH, voice="read", note_th="เฉลยมุก")
    follow["chain"] = _chain("follow", state, th=follow_th, warn=warn)
    after = tep._step(2, "cue", "Emma: «ถ้าวันไหน…»", line_th="ถ้าวันไหนเอ็มม่ากันน้ำได้ค่ะ", voice="read")
    pressed = lead_mark is not None
    return tep._order([{"n": 1, "title_th": "มุก", "steps": [lead, follow, after]}],
                      last=[1, 0] if pressed else None, next=nxt or ([1, 1] if pressed else [1, 0]), running=running)


def _page(browser, order, mode):
    return tep.Page(browser, tep._state(order), html=tep.PAGE, mode=mode)


def test_the_punchline_that_is_coming_asks_for_no_tap(chromium):
    page = _page(chromium, _joke("waiting", lead_mark=tep._mark("running"), running=[1, 0]), "order")
    try:
        follow = page.text("#step-1-1")
        assert "ตามมาเอง" in page.text("#step-1-1 .shead") and "รอตามมาเอง…" in follow
        assert page.text("#step-1-1 .schain") == "↪ " + event_order.CHAIN_WAITING_TH
        assert "แตะ =" not in follow                                    # "ถัดไป" stands there: no tap asked
        assert page.text("#step-1-0 .schain") == "↪ " + event_order.CHAIN_LEAD_TH
        assert "ตามมาเองเมื่อ Emma พูดบทก่อนหน้าจบ — ไม่ต้องกด" in page.text("#nextTop")
        assert "is-live" in page.page.locator("#nextBtn").get_attribute("class")
        assert page.page.locator("#step-1-1 .swarn").count() == 0
    finally:
        page.close()


def test_the_punchline_that_did_not_follow_says_why_and_is_a_press_again(chromium):
    why = event_order.CHAIN_MISSED_TH % event_order.ERRORS_TH["moving"]
    page = _page(chromium, _joke("missed", lead_mark=tep._mark("done"), follow_th=why, warn=True), "order")
    try:
        assert page.text("#step-1-1 .swarn") == "⚠ " + why                 # full strength
        assert "รอตามมาเอง" not in page.text("#step-1-1")
        assert "แตะ = พูดบทนี้" in page.text("#step-1-1")
        assert page.text("#nextTop").startswith("ถัดไป ▶")
        page.posts["/ceremony/order/next"] = {"ok": True, "n": 1, "i": 1, "do": "cue",
                                              "result": {"ok": True, "mode": "read"}}
        page.page.click("#nextBtn")
        page.page.wait_for_timeout(300)
        assert page.sent == [("/ceremony/order/next", {"n": 1, "i": 1})]
    finally:
        page.close()


def test_next_on_a_set_up_that_was_not_said_plays_it_again_never_the_bare_punchline(chromium):
    """Review 23 Nov: the server puts "ถัดไป" back on a set-up that was not said whole
    (event_order._chain_redo): the bar says so and sends the set-up; a stray tap on the punchline's
    row is out of its turn -- two taps."""
    again = {"role": "lead", "state": "missed", "th": event_order.CHAIN_LEAD_AGAIN_TH, "warn": True}
    order = _joke("missed", lead_mark=tep._mark("done"), follow_th=event_order.CHAIN_LEAD_FAILED_TH, warn=True,
                  lead_chain=again, nxt=[1, 0])
    page = _page(chromium, order, "order")
    try:
        assert "มุกเกริ่นอีกครั้ง (เฉลยตามมาเอง)" in page.text("#nextTop")
        assert page.text("#nextLabel") == "Emma: «ขอบคุณค่ะคุณมิกิ…»"
        assert page.text("#step-1-0 .swarn") == "⚠ " + event_order.CHAIN_LEAD_AGAIN_TH
        assert page.text("#step-1-1 .swarn") == "⚠ " + event_order.CHAIN_LEAD_FAILED_TH
        page.page.click("#step-1-1 .slabel")                   # the punchline's row, once
        page.page.wait_for_timeout(300)
        assert page.sent == []
        page.page.wait_for_timeout(4200)                       # (its arming lapses)
        page.posts["/ceremony/order/next"] = {"ok": True, "n": 1, "i": 0, "do": "cue",
                                              "result": {"ok": True, "mode": "read"}}
        page.page.click("#nextBtn")
        page.page.wait_for_timeout(300)
        assert page.sent == [("/ceremony/order/next", {"n": 1, "i": 0})]
    finally:
        page.close()


def test_manual_the_coming_punchline_button_says_so_and_still_takes_a_tap(chromium):
    page = _page(chromium, _joke("waiting", lead_mark=tep._mark("running"), running=[1, 0]), "manual")
    try:
        button = page.page.locator("#step-1-1 .sgo")
        assert button.inner_text() == "ตามมาเอง — ไม่ต้องกด" and button.is_enabled()   # never a trap
        assert page.text("#step-1-2 .sgo") == "พูดบทนี้"
        why = event_order.CHAIN_MISSED_TH % event_order.CHAIN_WHY_TH["stopped"]
        page.redraw(tep._state(_joke("missed", lead_mark=tep._mark("done"), follow_th=why, warn=True)))
        assert page.text("#step-1-1 .sgo") == "พูดบทนี้"
        page.page.click("#step-1-1 .sgo")
        page.page.wait_for_timeout(300)
        assert page.sent == [("/ceremony/order/step", {"n": 1, "i": 1})]
    finally:
        page.close()


def test_pressing_the_set_up_says_the_punchline_follows(chromium):
    page = _page(chromium, _joke(None), "manual")
    try:
        page.posts["/ceremony/order/step"] = {"ok": True, "n": 1, "i": 0, "do": "cue",
                                              "result": {"ok": True, "mode": "read"}}
        page.page.click("#step-1-0 .sgo")
        assert "บทถัดไปตามมาเองเมื่อ Emma พูดจบ" in page.toast()
        assert page.text("#step-1-1 .schain") == "↪ " + event_order.CHAIN_FOLLOW_TH
    finally:
        page.close()


def test_the_chain_the_page_is_driven_with_is_the_servers_own(real):
    """The real /ceremony/state over a chained order: `chain` on the pair's two steps only, in the
    shape the builders above give the page."""
    doc = json.loads(json.dumps(tep.SCRIPT))
    doc["cues"].append(tep._signed({"id": "s1_punch", "scene": 1, "th": PUNCH_TH, "numbers": []}))
    steps = doc["order"][0]["steps"]
    at = next(k for k, st in enumerate(steps) if st.get("cue") == "s1_greet")
    steps[at]["chain"] = True
    steps.insert(at + 1, {"do": "cue", "cue": "s1_punch"})
    with open(settings.ceremony_file, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    event_script.reset()
    event_order.reset()
    got = real.get("/ceremony/state").json()["order"]
    chained = {(sc["n"], st["i"]): st["chain"] for sc in got["scenes"] for st in sc["steps"] if "chain" in st}
    assert chained == {(1, at): _chain("lead", None), (1, at + 1): _chain("follow", None)}
    for sc in got["scenes"]:
        for st in sc["steps"]:
            extra = {"screen"} if st["do"] == "screen" else set()
            extra |= {"chain"} if "chain" in st else set()
            assert set(st) == set(tep._step(0, "manual", "x")) | extra, (sc["n"], st)
