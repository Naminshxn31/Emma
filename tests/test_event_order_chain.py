"""A joke said straight through (owner 23 Nov 2026, watching the ceremony preview: "ฉากที่เล่นมุข ที่ตอน
โดดลงสระ และแฟนพันธุ์แท้ให้พูดต่อเนื่องเลย ไม่ต้องเว้นช่วงมันดูเงียบ และช้า").

`"chain": true` on a cue step of the running order (app/event_script.py, app/event_order.py): staff press
the set-up once, and the punchline -- the next step of the scene -- is pressed by the server the moment
the set-up has been said, as a press (`_run`, via "chain"). What these hold down:

* the punchline goes the moment the set-up's line is over on the server -- while its last seconds still
  play (event_cues waits those out before the punchline's first word), not after the cut's slack;
* a stop of any kind between the two (the red stop, event_cues' cut, a walk stopped) = no punchline,
  and its step says so; so does a set-up that failed or was cut;
* a gate that refuses the punchline (the robot moving, another line pressed) leaves it to staff, said
  on its step, and a press by hand then works -- as it does after the chain played it;
* staff who pressed or skipped the punchline first, a reset, a set-up pressed again: one punchline;
* the real order chains exactly the two jokes, and "ถัดไป" through it says every line once;
* the file's `chain` is checked like every other field of a step.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from app import event_cues, event_host, event_order, event_script, event_walk
from app.config import settings
from tests.test_event_order import _real_event, _signed, call, calls, event, rows  # noqa: F401 -- fixtures

ROOT = Path(__file__).resolve().parents[1]
REAL_SCRIPT = ROOT / "data" / "ceremony" / "floor1_ceremony.json"

#: Our own joke: a set-up, its punchline, the line after the laughter, a tick.
SCRIPT = {"format": event_script.FORMAT, "host_th": "คุณมิกิ", "replies": "cues", "cues": [
    _signed({"id": "j_setup", "scene": 1, "th": "ขอบคุณค่ะคุณมิกิ เอ็มม่าศึกษามาอย่างดีแล้วค่ะ...", "numbers": []}),
    _signed({"id": "j_punch", "scene": 1, "th": "ลงไปว่ายน้ำในสระด้วยตัวเองค่ะ!", "numbers": []}),
    _signed({"id": "j_after", "scene": 1, "th": "ถ้าวันไหนเอ็มม่ากันน้ำได้ จะขอไปว่ายโชว์บ้างนะคะ", "numbers": []}),
], "order": [
    {"n": 1, "title_th": "มุก", "steps": [
        {"do": "cue", "cue": "j_setup", "chain": True},
        {"do": "cue", "cue": "j_punch"},
        {"do": "cue", "cue": "j_after"},
        {"do": "manual", "label_th": "ติ๊ก"},
    ]},
]}


@pytest.fixture
def joke(event):
    """SCRIPT as the ceremony's file (the tracked event file beside it: `event`), the app on."""
    (event.parent / "floor1_ceremony.json").write_text(json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8")
    event_script.reset()
    event_order.reset()
    return event


class Voice:
    """event_cues as the order sees it: a pressed line is a run while she says it (`play`), then its
    row in `last` (`said`); `tail` = her voice of the last line still playing (`line_audible`)."""

    def __init__(self, monkeypatch):
        self.played: list[tuple[str, float]] = []
        self.tail = False
        self.seq = 0
        self.gate = None                 # an asyncio.Event: a press waits on it before her first word
        self.refuse = None               # an event_cues error code: the next press is refused with it
        monkeypatch.setattr(event_cues, "play", self.play)
        monkeypatch.setattr(event_cues, "line_audible",
                            lambda now=None: self.tail or bool(event_cues.STATE["runs"]))
        monkeypatch.setattr(event_cues, "_live_call", lambda: object())

    async def play(self, cue_id, *, by="staff", show=None):
        if self.gate is not None:
            await self.gate.wait()
        if self.refuse:
            why, self.refuse = self.refuse, None
            return {"ok": False, "error": why, "error_th": event_cues.ERRORS_TH[why]}
        self.played.append((cue_id, time.monotonic()))
        self.seq += 1
        event_cues.STATE["runs"][self.seq] = {"seq": self.seq, "id": cue_id, "phase": "speaking"}
        return {"ok": True, "mode": "recorded"}

    def said(self, cue_id: str, *, ok: bool = True, cut: bool = False, tail: bool = True) -> float:
        """Her line is over on the server (its run ended, its row written); `tail` = its last seconds
        still play on the page. Returns when."""
        for k, run in list(event_cues.STATE["runs"].items()):
            if run["id"] == cue_id:
                del event_cues.STATE["runs"][k]
        event_cues.STATE["last"][cue_id] = {"ok": ok, "error": None if ok else "not_sent", "cut": cut,
                                            "at": time.time()}
        self.tail = tail
        return time.monotonic()

    def ids(self) -> list[str]:
        return [c for c, _ in self.played]


@pytest.fixture
def voice(monkeypatch):
    return Voice(monkeypatch)


def _step(snap: dict, n: int, i: int) -> dict:
    sc = next(s for s in snap["scenes"] if s["n"] == n)
    return next(st for st in sc["steps"] if st["i"] == i)


async def _until(probe, seconds: float = 2.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if probe():
            return True
        await asyncio.sleep(0.01)
    return bool(probe())


def _order_rows(rows: list) -> list[tuple]:
    return [(r["n"], r["i"], r["via"], r["ok"], r["error"]) for r in rows if r["event"] == "event_order"]


# ---- the punchline at once ---------------------------------------------------------------------

def test_the_set_up_says_its_punchline_the_moment_it_is_said(joke, voice, rows):
    async def go():
        got = await event_order.press(1, 0)
        assert got["ok"], got
        snap = await event_order.snapshot()
        assert _step(snap, 1, 0)["chain"] == {"role": "lead", "state": "waiting", "th": event_order.CHAIN_LEAD_TH,
                                              "warn": False}
        assert _step(snap, 1, 1)["chain"] == {"role": "follow", "state": "waiting",
                                              "th": event_order.CHAIN_WAITING_TH, "warn": False}
        assert "chain" not in _step(snap, 1, 2) and "chain" not in _step(snap, 1, 3)
        await asyncio.sleep(0.4)
        assert voice.ids() == ["j_setup"]                       # nothing while she says the set-up
        ended = voice.said("j_setup", tail=True)                # over on the server; her last seconds play on
        assert await _until(lambda: len(voice.played) == 2)
        # pressed while the set-up's tail still plays (event_cues waits that out before the first word):
        # not after the cut's AUDIBLE_SLACK_S, not after the next poll of the page
        assert voice.tail is True and voice.played[1][1] - ended < event_cues.AUDIBLE_SLACK_S
        assert voice.ids() == ["j_setup", "j_punch"]
        snap = await event_order.snapshot()
        assert _step(snap, 1, 0)["mark"]["state"] == "done"
        assert _step(snap, 1, 1)["mark"]["state"] == "running" and snap["running"] == [1, 1]
        assert snap["last"] == [1, 1] and snap["next"] == [1, 2]
        assert _step(snap, 1, 1)["chain"]["state"] == "pressed"
        assert _step(snap, 1, 1)["chain"]["th"] == event_order.CHAIN_PRESSED_TH
        # her punchline is being said: a staff press now is refused as ever -- and only one punchline
        assert (await event_order.press(1, 2))["error"] == "running"
        voice.said("j_punch", tail=False)
        await asyncio.sleep(0.2)
        assert voice.ids() == ["j_setup", "j_punch"]            # the punchline has no chain of its own

    asyncio.run(go())
    assert _order_rows(rows) == [(1, 0, "press", True, None), (1, 1, "chain", True, None),
                                 (1, 2, "press", False, "running")]


def test_through_next_the_joke_is_one_press_too(joke, voice, rows):
    """"ตามลำดับ": "ถัดไป" on the set-up, and the next "ถัดไป" is the line after the punchline."""
    async def go():
        assert (await event_order.next(expect=(1, 0)))["ok"]
        voice.said("j_setup", tail=False)
        assert await _until(lambda: len(voice.played) == 2)
        snap = await event_order.snapshot()
        assert snap["next"] == [1, 2]
        voice.said("j_punch", tail=False)
        assert (await event_order.next(expect=(1, 2)))["ok"]

    asyncio.run(go())
    assert voice.ids() == ["j_setup", "j_punch", "j_after"]
    assert [r[2] for r in _order_rows(rows)] == ["next", "chain", "next"]


# ---- no punchline after a stop, or a set-up that was not said ------------------------------------

def _red_stop():
    event_order.staff_stopped()


def _cut():
    event_cues.STATE["cut_gen"] += 1                 # what event_cues.cut counts first, whatever it found


def _walk_stop():
    event_walk.STATE["stops"] = int(event_walk.STATE.get("stops") or 0) + 1


@pytest.mark.parametrize("stop", [_red_stop, _cut, _walk_stop], ids=["red_stop", "cue_cut", "walk_stop"])
def test_a_stop_between_the_two_is_no_punchline(joke, voice, rows, stop):
    async def go():
        assert (await event_order.press(1, 0))["ok"]
        await asyncio.sleep(0.1)
        stop()
        voice.said("j_setup", tail=False)
        await asyncio.sleep(0.4)
        assert voice.ids() == ["j_setup"]
        snap = await event_order.snapshot()
        follow = _step(snap, 1, 1)
        assert follow["mark"] is None and snap["next"] == [1, 1]
        assert follow["chain"]["state"] == "missed" and follow["chain"]["warn"] is True
        assert follow["chain"]["th"] == event_order.CHAIN_MISSED_TH % event_order.CHAIN_WHY_TH["stopped"]
        # staff still decide: pressed by hand, it is said
        assert (await event_order.press(1, 1))["ok"]

    asyncio.run(go())
    assert voice.ids() == ["j_setup", "j_punch"]
    assert (1, 1, "chain", False, "stopped") in _order_rows(rows)


@pytest.mark.parametrize("how", [{"ok": False}, {"cut": True}], ids=["failed", "cut_short"])
def test_a_set_up_that_was_not_said_whole_has_no_punchline(joke, voice, how):
    async def go():
        assert (await event_order.press(1, 0))["ok"]
        voice.said("j_setup", tail=False, **how)
        await asyncio.sleep(0.4)
        assert voice.ids() == ["j_setup"]
        chain = _step(await event_order.snapshot(), 1, 1)["chain"]
        # its own words (review 23 Nov): press the SET-UP again -- not "press this one", which would
        # be a punchline with no set-up
        assert chain == {"role": "follow", "state": "missed", "th": event_order.CHAIN_LEAD_FAILED_TH, "warn": True}

    asyncio.run(go())


def test_next_goes_back_to_a_set_up_that_was_not_said_and_the_punchline_follows_it(joke, voice):
    """Review 23 Nov: "ถัดไป" ("ตามลำดับ") stood on the punchline after its set-up failed -- one tap, a
    punchline with no set-up. The server's next is the set-up again (snapshot and /ceremony/order/next
    agree: the phone sends the step its button shows), and the joke goes as one press."""
    async def go():
        assert (await event_order.next(expect=(1, 0)))["ok"]
        voice.said("j_setup", cut=True, tail=False)
        await asyncio.sleep(0.3)
        snap = await event_order.snapshot()
        assert snap["last"] == [1, 0] and snap["next"] == [1, 0]
        assert _step(snap, 1, 0)["chain"] == {"role": "lead", "state": "missed",
                                              "th": event_order.CHAIN_LEAD_AGAIN_TH, "warn": True}
        stale = await event_order.next(expect=(1, 1))                 # a phone still showing the punchline
        assert stale["error"] == "stale" and stale["next"] == [1, 0] and voice.ids() == ["j_setup"]
        assert (await event_order.next(expect=(1, 0)))["ok"]          # the set-up again...
        voice.said("j_setup", tail=False)
        assert await _until(lambda: voice.ids() == ["j_setup", "j_setup", "j_punch"])   # ...and its punchline
        assert (await event_order.snapshot())["next"] == [1, 2]

    asyncio.run(go())


def test_a_set_up_skipped_after_it_failed_gives_next_back_to_the_punchline(joke, voice):
    async def go():
        assert (await event_order.press(1, 0))["ok"]
        voice.said("j_setup", ok=False, tail=False)
        await asyncio.sleep(0.3)
        assert (await event_order.snapshot())["next"] == [1, 0]
        assert (await event_order.skip(1, 0))["ok"]                  # staff drop the joke's set-up
        snap = await event_order.snapshot()
        assert snap["next"] == [1, 1] and _step(snap, 1, 1)["chain"]["th"] == event_order.CHAIN_FOLLOW_TH

    asyncio.run(go())


@pytest.mark.parametrize("first", [True, False], ids=["first_press", "pressed_again"])
def test_a_set_up_refused_on_its_way_never_says_the_punchline_follows(joke, voice, first):
    """Review 23 Nov: the set-up pressed again and refused inside its dispatch (Emma kept quiet: a clip)
    -- the old chain retired, no new one: the punchline said "ตามมาเอง" with nothing watching. A set-up
    that was not said says so, and "ถัดไป" is on it."""
    async def go():
        if not first:
            assert (await event_order.press(1, 0))["ok"]
            voice.said("j_setup", ok=False, tail=False)
            await asyncio.sleep(0.2)
        voice.refuse = "quiet"
        got = await event_order.press(1, 0)
        assert got["ok"] is False and got["error"] == "quiet"
        await asyncio.sleep(0.2)
        snap = await event_order.snapshot()
        follow = _step(snap, 1, 1)["chain"]
        assert follow == {"role": "follow", "state": "missed", "th": event_order.CHAIN_LEAD_FAILED_TH, "warn": True}
        assert snap["next"] == [1, 0]

    asyncio.run(go())
    assert voice.ids() == ([] if first else ["j_setup"])


@pytest.mark.parametrize("when", ["after_the_miss", "before_the_old_watcher_looked"])
def test_pressing_the_set_up_again_shows_no_passing_miss(joke, voice, when):
    """Review 23 Nov: the new press marks the set-up long before its own chain begins (her first
    word), and the old chain -- its watcher seeing the new mark, or its "missed" already up -- put
    "ไม่ได้ตามมาเอง" on the punchline meanwhile. The press retires it at once."""
    async def go():
        assert (await event_order.press(1, 0))["ok"]
        voice.said("j_setup", ok=False, tail=False)
        if when == "after_the_miss":
            await asyncio.sleep(0.2)
            assert _step(await event_order.snapshot(), 1, 1)["chain"]["state"] == "missed"
        voice.gate = asyncio.Event()                     # the press again: waiting for her first word
        again = asyncio.ensure_future(event_order.press(1, 0))
        seen = []
        for _ in range(6):
            await asyncio.sleep(0.05)
            seen.append(_step(await event_order.snapshot(), 1, 1)["chain"])
        assert not any(c["state"] == "missed" or c["warn"] for c in seen), seen
        assert seen[-1]["th"] == event_order.CHAIN_FOLLOW_TH
        voice.gate.set()
        assert (await again)["ok"]
        assert _step(await event_order.snapshot(), 1, 1)["chain"]["state"] == "waiting"
        voice.said("j_setup", tail=False)
        assert await _until(lambda: voice.ids()[-1:] == ["j_punch"])

    asyncio.run(go())
    assert voice.ids() == ["j_setup", "j_setup", "j_punch"]


# ---- a gate that refuses: staff press it -------------------------------------------------------

def test_a_gate_that_refuses_leaves_the_punchline_to_staff_and_says_why(joke, voice, rows, monkeypatch):
    moving = {"now": False}
    monkeypatch.setattr(event_order, "_robot_moving", lambda: moving["now"])

    async def go():
        assert (await event_order.press(1, 0))["ok"]
        moving["now"] = True                         # a gesture under way when the set-up ends
        voice.said("j_setup", tail=False)
        await asyncio.sleep(0.4)
        assert voice.ids() == ["j_setup"]
        snap = await event_order.snapshot()
        follow = _step(snap, 1, 1)
        assert follow["mark"] is None and snap["next"] == [1, 1]          # still staff's to press
        assert follow["chain"]["warn"] is True
        assert follow["chain"]["th"] == event_order.CHAIN_MISSED_TH % event_order.ERRORS_TH["moving"]
        moving["now"] = False
        assert (await event_order.press(1, 1))["ok"]                      # by hand: the retry
        voice.said("j_punch", tail=False)
        await asyncio.sleep(0.1)
        assert (await event_order.press(1, 1))["ok"]                      # ...and again: a punchline by hand always works

    asyncio.run(go())
    assert voice.ids() == ["j_setup", "j_punch", "j_punch"]
    assert (1, 1, "chain", False, "moving") in _order_rows(rows)


def test_another_line_pressed_meanwhile_is_not_talked_over(joke, voice, rows):
    """The chain skips the set-up's own tail only: any other line under way still refuses it (a line
    pressed by hand on /ceremony/cue, say), and so does a press still in its first awaits."""
    async def go():
        assert (await event_order.press(1, 0))["ok"]
        event_cues.STATE["entering"] = 1             # a /ceremony/cue press looking its cue up
        voice.said("j_setup", tail=True)
        await asyncio.sleep(0.4)
        assert voice.ids() == ["j_setup"]
        event_cues.STATE["entering"] = 0
        chain = _step(await event_order.snapshot(), 1, 1)["chain"]
        assert chain["state"] == "missed" and event_order.ERRORS_TH["running"] in chain["th"]

    asyncio.run(go())
    assert (1, 1, "chain", False, "running") in _order_rows(rows)


# ---- one punchline: staff first, a reset, the set-up again --------------------------------------

def test_staff_skipping_the_punchline_first_is_no_punchline(joke, voice):
    async def go():
        assert (await event_order.press(1, 0))["ok"]
        assert (await event_order.skip(1, 1))["ok"]           # "ข้ามขั้นนี้" on it while she says the set-up
        voice.said("j_setup", tail=False)
        await asyncio.sleep(0.4)
        snap = await event_order.snapshot()
        assert voice.ids() == ["j_setup"] and snap["next"] == [1, 2]
        assert _step(snap, 1, 1)["chain"]["state"] == "taken"

    asyncio.run(go())


def test_staff_pressing_the_punchline_as_the_set_up_ends_is_one_punchline(joke, voice, monkeypatch):
    async def go():
        assert (await event_order.press(1, 0))["ok"]
        voice.said("j_setup", tail=False)
        by_hand = asyncio.ensure_future(event_order.press(1, 1))   # in the same instant as the chain's turn
        await asyncio.sleep(0.4)
        assert (await by_hand)["ok"]
        assert voice.ids() == ["j_setup", "j_punch"]
        assert _step(await event_order.snapshot(), 1, 1)["chain"]["state"] == "taken"

    asyncio.run(go())


@pytest.mark.parametrize("how", ["reset", "pressed_again"])
def test_a_reset_or_the_set_up_again_ends_the_old_chain(joke, voice, how):
    async def go():
        assert (await event_order.press(1, 0))["ok"]
        if how == "reset":
            event_order.reset_order()
        else:
            # the set-up failed (its chain gives up), then pressed again: that press has its own chain --
            # one punchline, after the set-up that was said
            voice.said("j_setup", ok=False, tail=False)
            await asyncio.sleep(0.2)
            assert (await event_order.press(1, 0))["ok"]
        voice.said("j_setup", tail=False)
        await asyncio.sleep(0.4)
        return voice.ids()

    ids = asyncio.run(go())
    assert ids == (["j_setup"] if how == "reset" else ["j_setup", "j_setup", "j_punch"])


# ---- the file ------------------------------------------------------------------------------------

def _order(*steps) -> dict:
    cues = SCRIPT["cues"] + [
        _signed({"id": "j_live", "scene": 1, "th": None, "live_th": "ตอบสด", "numbers": []}),
        _signed({"id": "j_film", "scene": 1, "th": "ดูวิดีโอกันค่ะ", "numbers": [], "clip": "tech_presentation"})]
    return {**SCRIPT, "cues": cues, "order": [{"n": 1, "title_th": "x", "steps": list(steps)}]}


SETUP, PUNCH = {"do": "cue", "cue": "j_setup", "chain": True}, {"do": "cue", "cue": "j_punch"}


@pytest.mark.parametrize("steps,problem", [
    ((SETUP,), "n1.0_chain_to_nothing"),                             # a chain never crosses a scene
    ((SETUP, {"do": "manual", "label_th": "x"}), "n1.0_chain_to_a_manual"),
    (({**SETUP, "chain": "yes"}, PUNCH), "n1.0_chain_not_a_bool"),
    ((SETUP, {"do": "cue", "cue": "j_live"}), "n1.0_chain_to_a_line_without_words"),
    (({"do": "cue", "cue": "j_live", "chain": True}, PUNCH), "n1.0_chain_from_a_line_without_words"),
    (({"do": "cue", "cue": "j_film", "chain": True}, PUNCH), "n1.0_chain_after_a_film"),
    (({"do": "manual", "label_th": "x", "chain": True}, PUNCH), "n1.0_unexpected_field:chain"),
])
def test_a_chain_the_file_got_wrong_is_named(steps, problem):
    found = event_script.order_problems(_order(*steps), _real_event())
    assert problem in found, found


def test_a_chain_written_right_or_false_is_clean_and_normalised_only_where_true():
    assert event_script.order_problems(_order(SETUP, PUNCH), _real_event()) == []
    assert event_script.order_problems(_order({**SETUP, "chain": False}, PUNCH), _real_event()) == []
    rows_ = event_script.order(_order(SETUP, PUNCH, {**SETUP, "chain": False}, PUNCH))[0]["steps"]
    assert rows_[0]["chain"] is True and "chain" not in rows_[1] and "chain" not in rows_[2]


def test_a_chain_the_server_cannot_follow_is_not_followed(joke, voice, monkeypatch):
    """Written wrong anyway (a punchline with no words yet): no chain at all -- the set-up is a line."""
    doc = _order(SETUP, {"do": "cue", "cue": "j_live"})
    (joke.parent / "floor1_ceremony.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    event_script.reset()

    async def go():
        assert (await event_order.press(1, 0))["ok"]
        assert event_order.STATE["chain"] is None
        assert "chain" not in _step(await event_order.snapshot(), 1, 0)

    asyncio.run(go())


# ---- the real order ----------------------------------------------------------------------------

def test_the_real_order_chains_the_two_jokes_questions_1_9_and_10_and_nothing_else(monkeypatch):
    monkeypatch.setattr(settings, "ceremony_file", str(REAL_SCRIPT))
    event_script.reset()
    order = event_script.order()
    pairs = {(sc["n"], st["i"]): event_order._chain_follower(order, sc["n"], st["i"])
             for sc in order for st in sc["steps"] if st.get("chain")}
    assert pairs == {(2, 0): (2, 1), (4, 2): (4, 3), (4, 3): (4, 4), (5, 1): (5, 2), (5, 3): (5, 4),
                     (6, 1): (6, 2)}
    cue = {(sc["n"], st["i"]): st["cue"] for sc in order for st in sc["steps"] if st["do"] == "cue"}
    # (question 1's answer is one line to the room, owner 24 Nov "มันควรรวมของ 2 บทไหม": no window to talk in between)
    assert [(cue[a], cue[b]) for a, b in pairs.items()] == [("s02_l03", "s02_l05"), ("s04_l09", "s04_l11"),
                                                            ("s04_l11", "s04_l13"), ("s05_l06", "s05_l08"), ("s05_l11", "s05_l13"),
                                                            ("s06_l05", "s06_l07")]
    # (23 Nov the laughter after the pool joke was the room's and s04_l13 waited for staff; 24 Nov, with nobody pressing,
    # her swim was never seen -- owner: "ฉากตอนว่ายหายไปต้องเอามารวมกันไหม")
    assert not next(st for st in order[3]["steps"] if st["cue"] == "s04_l13").get("chain")


def test_next_through_the_real_order_says_every_line_once_and_the_punchlines_by_themselves(
        event, calls, call, rows, monkeypatch):
    """The 17 Nov desk rehearsal (tests/test_ceremony_english.py), in ONE event loop as on the day:
    "ถัดไป" from the first step to the hand-over, every path stubbed. The two punchlines are said by
    the chain -- "ถัดไป" never lands on them -- and every line once, in file order."""
    doc = json.loads(REAL_SCRIPT.read_text(encoding="utf-8"))
    for raw in doc["cues"]:                               # signed for this tmp copy only
        c = event_script._normalise(raw)
        if c["th"]:
            raw.update(approved_by="test", approved_at="2026-11-23 10:00", approved_sha256=event_script.digest(c))
    (event.parent / "floor1_ceremony.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    event_script.reset()
    event_order.reset()
    monkeypatch.setattr(event_script, "number_problems", lambda c, **kw: [])
    monkeypatch.setattr(settings, "event_walk_enabled", True)
    event_host.spots_path().write_text(json.dumps({"spots": {
        k: {"pose": [-9.0, -3.0, 0.0]} for k in ("stage_centre", "beside_host", "beside_globe")}}), encoding="utf-8")
    event_host.reset()

    async def go():
        for _ in range(200):
            got = await event_order.next()
            if got.get("error") == "end":
                return
            if not got.get("ok"):
                assert (await event_order.skip(got["n"], got["i"]))["ok"], got    # the demo room's empty line
            await asyncio.sleep(0.15)                     # the chain's turn, as the room's seconds would give it
        raise AssertionError("never reached the end")

    asyncio.run(go())
    want = [st["cue"] for sc in event_script.order(doc) for st in sc["steps"]
            if st["do"] == "cue" and st["cue"] != "s05_l07"]
    assert [c[1] for c in calls if c[0] == "cue"] == want and len(want) == 28
    chained = [(r["n"], r["i"]) for r in rows if r["event"] == "event_order" and r["via"] == "chain"]
    assert chained == [(2, 1), (4, 3), (4, 4), (5, 2), (5, 4), (6, 2)]
    assert calls[-1] == ("handoff", "event")
