"""The ceremony preview shows the day's jokes and the day's end (owner 23 Nov 2026, watching
scripts/preview_ceremony_show.py: "ฉากที่เล่นมุข ที่ตอนโดดลงสระ และแฟนพันธุ์แท้ให้พูดต่อเนื่องเลย ไม่ต้อง
เว้นช่วงมันดูเงียบ และช้า" and "ฉากสุดท้ายเอาออกให้มันจบตรงขอบคุณพอ").

* the preview reads the order's chains the way the day does (app/event_order.py `_chain_follower`);
* in the watcher's loop a punchline that comes on from its set-up has no stand-in host line, no --gap
  and no pause before its first word, and its set-up no hold after its voice; a button that lands on
  the punchline plays it as a staff press would; the other lines keep their host line and gap;
* a pause holds the punchline like any line; a set-up has no screenshot after its voice;
* (the run ending at the thank-you: tests/test_ceremony_slides.py, the preview's lines.)
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import event_script
from app.config import settings

ROOT = Path(__file__).resolve().parents[1]
REAL_SCRIPT = ROOT / "data" / "ceremony" / "floor1_ceremony.json"
#: The script under test (a module attribute: read when a test loads it).
PREVIEW = ROOT / "scripts" / "preview_ceremony_show.py"
RATE = 24000


def _preview():
    spec = importlib.util.spec_from_file_location("preview_ceremony_show_chain", PREVIEW)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["preview_ceremony_show_chain"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def real(monkeypatch):
    monkeypatch.setattr(settings, "ceremony_file", str(REAL_SCRIPT))
    event_script.reset()
    yield
    event_script.reset()


def test_the_preview_reads_the_days_chains(real):
    # (24 Nov, the owner: question 1, the swimming joke's end, questions 9 and 10 are one answer each)
    assert _preview().chained() == {"s02_l05": "s02_l03", "s04_l11": "s04_l09", "s04_l13": "s04_l11",
                                    "s05_l08": "s05_l06", "s05_l13": "s05_l11", "s06_l07": "s06_l05"}


def _watch(pv, monkeypatch, todo_ids, *, gap=0.5, press=None):
    """The watcher's loop over the real lines, `play` and Khun Miki's voice stood in (each line
    0.2 s): what was played, with what lead/tail, when; whose host line was voiced."""
    played, hosts_said = [], []

    async def play(cue, pcm, *, ctl=None, on_voice=None, lead_s=0.4, tail_s=0.3, chained=False, **kw):
        start = time.monotonic()
        await asyncio.sleep(lead_s + 0.2 + tail_s)
        played.append({"id": cue["id"], "lead_s": lead_s, "tail_s": tail_s, "start": start,
                       "end": time.monotonic()})

    async def say_host(text, pcm, *, ctl=None, on_voice=None, on_caption=None):
        hosts_said.append(text)
        await asyncio.sleep(0.01)

    monkeypatch.setattr(pv, "play", play)
    monkeypatch.setattr(pv, "say_host", say_host)
    monkeypatch.setattr(pv, "_host_takes", lambda: {cid: {"text": cid, "pcm": b""} for cid in todo_ids})
    todo = [event_script.cue(cid) for cid in todo_ids]
    clips = {cid: b"\x00\x00" * 2400 for cid in todo_ids}
    ctl = pv.Controls(todo)
    args = SimpleNamespace(gap=gap, no_host=False)

    async def go():
        task = asyncio.create_task(pv.watch(ctl, todo, clips, args))
        if press is not None:
            await press(ctl, played)
        deadline = time.monotonic() + 10
        while not ctl.done and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(go())
    return played, hosts_said


def test_the_watchers_loop_plays_a_joke_straight_through(real, monkeypatch):
    pv = _preview()
    ids = ["s04_l06", "s04_l09", "s04_l11", "s04_l13"]
    played, hosts = _watch(pv, monkeypatch, ids, gap=0.5)
    by = {p["id"]: p for p in played}
    assert [p["id"] for p in played] == ids
    # the punchline: no host line, no gap, no pause before its first word -- and its set-up holds
    # nothing after its voice
    assert by["s04_l11"]["lead_s"] == 0.0 and by["s04_l09"]["tail_s"] == 0.0
    assert by["s04_l11"]["start"] - by["s04_l09"]["end"] < 0.15
    assert hosts == ["s04_l06", "s04_l09"]                             # (s04_l13 comes on from the punchline too, 24 Nov)
    # the other lines as before: the press's pause, the hold, the gap (and the laughter after the punchline)
    assert by["s04_l06"]["lead_s"] == 0.4 and by["s04_l13"]["lead_s"] == 0.0
    assert by["s04_l09"]["start"] - by["s04_l06"]["end"] >= 0.5
    assert by["s04_l13"]["start"] - by["s04_l11"]["end"] < 0.15


def test_a_button_that_lands_on_the_punchline_plays_it_as_a_press_would(real, monkeypatch):
    pv = _preview()

    async def press(ctl, played):
        while ctl.i != 1:                            # the set-up has begun
            await asyncio.sleep(0.01)
        ctl.press("next")

    played, hosts = _watch(pv, monkeypatch, ["s06_l03", "s06_l05", "s06_l07"], press=press)
    punch = [p for p in played if p["id"] == "s06_l07"]
    assert punch and punch[0]["lead_s"] == 0.4               # staff's press of it: not "follows at once"
    assert "s06_l07" not in hosts                            # ...and still no host line before it


def test_a_pause_holds_the_punchline_too(monkeypatch):
    """"No gap" is `wait(seconds=0)`, and a pause must hold it. Without its own word on the pause,
    `wait` held 0 s only by luck of the clock -- `start` read a hair before `began` made the unpaused
    time come out a hair below zero; on a clock that reads both in the same tick (Windows' old 15.6 ms
    tick, coarser here so this is not timing luck) the punchline went on under a pause."""
    pv = _preview()
    real_monotonic = time.monotonic

    class Coarse:
        monotonic = staticmethod(lambda: int(real_monotonic() / 0.5) * 0.5)
        time = staticmethod(time.time)

    monkeypatch.setattr(pv, "time", Coarse)
    ctl = pv.Controls([{"id": "a", "en": "x"}])

    async def go():
        ctl.set_paused(True)
        waiting = asyncio.create_task(ctl.wait(seconds=0.0))      # the punchline's "no gap"
        await asyncio.sleep(0.3)
        held = not waiting.done()
        ctl.set_paused(False)
        await asyncio.wait_for(waiting, 2)
        return held

    assert asyncio.run(go()) is True


def test_a_backlog_of_shots_never_holds_the_punchline_back(real, monkeypatch):
    """A headless screenshot takes ~1.1 s and --shots-every may ask one each 0.5 s: the set-up's play
    used to work through the whole backlog before it returned (review run 23 Nov: 3.4 s between the
    two). Each "shot" here moves the preview's clock 1.1 s; the set-up's play is over by its voice's end."""
    from app import face_mirror

    pv = _preview()
    clock = {"skew": 0.0}
    real_monotonic = pv.time.monotonic
    monkeypatch.setattr(pv, "time", type("T", (), {"monotonic": staticmethod(lambda: real_monotonic() + clock["skew"]),
                                                    "time": staticmethod(time.time)}))
    monkeypatch.setattr(face_mirror, "publish_audio", lambda chunk: None)
    shots = []

    async def shot(name):
        shots.append(name)
        clock["skew"] += 1.1

    async def go():
        start = pv.time.monotonic()
        await pv.play(event_script.cue("s04_l09"), b"\x00\x00" * RATE * 6, on_mark=shot, every=0.5,
                      lead_s=0.0, tail_s=0.0, chained=True)
        return pv.time.monotonic() - start

    took = asyncio.run(go())
    assert shots and took < 6.0 + 0.5, (took, shots)         # her 6 s, not 6 s plus the backlog


def test_a_set_up_has_no_screenshot_after_its_voice():
    pv = _preview()
    assert pv.shot_marks(5.0, 1.0)[-1] == ("t06.5", 6.5)                   # a line: one after its end
    assert pv.shot_marks(5.0)[-1] == ("c", 5.5)
    # a set-up: its last shot (awaited, ~SHOT_S) over before its voice is -- not a pause before the punchline
    assert max(t for _n, t in pv.shot_marks(5.0, 1.0, chained=True)) <= 5.0 - pv.SHOT_S
    assert pv.shot_marks(5.0, chained=True) == [("a", 0.9), ("b", 2.5)]
