"""Card 2 starts by itself after a whole card 1 (owner 25 Nov 00:09: "...ไปถึงตำแหน่งหุ่นจะการ์ดสองเองเลย"):
app/event_order.follow_after_opening -- the `follow` step after the opening, pressed through the order's own path, once the
order's mark of the opening says done and her last line is over; never after a stop, a failed opening, or twice."""
import asyncio

import pytest

from app import event_order

SCENES = [{"n": 1, "steps": [{"i": 0, "do": "manual"}, {"i": 1, "do": "opening"}, {"i": 2, "do": "follow"}]},
          {"n": 2, "steps": [{"i": 0, "do": "cue", "cue": "s02_l03"}]}]


@pytest.fixture
def order(monkeypatch):
    state = {"runs": [], "answers": [], "stops": (0,)}
    monkeypatch.setattr(event_order, "_scenes", lambda: SCENES)
    for name in ("_restore_once", "_expire_old", "_settle"):
        monkeypatch.setattr(event_order, name, lambda *a, **k: None)
    monkeypatch.setattr(event_order, "_stop_gen", lambda: state["stops"])
    monkeypatch.setattr(event_order, "AFTER_OPENING_WAIT_S", 1.5)
    marks = {}
    monkeypatch.setitem(event_order.STATE, "marks", marks)

    async def run(scenes, n, i, *, via, stops=None, **kw):
        state["runs"].append((n, i, via, stops))
        out = state["answers"].pop(0) if state["answers"] else {"ok": True}
        if out.get("ok"):
            marks[event_order._key(n, i)] = {"state": "done"}
        return out

    monkeypatch.setattr(event_order, "_run", run)
    state["marks"] = marks
    return state


def test_after_the_whole_card_1_the_follow_step_is_pressed_once(order):
    order["marks"][event_order._key(1, 1)] = {"state": "done"}
    got = asyncio.run(event_order.follow_after_opening())
    assert got["ok"] and order["runs"] == [(1, 2, "after_opening", (0,))]
    assert asyncio.run(event_order.follow_after_opening())["already"] and len(order["runs"]) == 1


def test_it_waits_for_the_openings_mark_and_her_last_line(order):
    order["marks"][event_order._key(1, 1)] = {"state": "running"}
    order["answers"] = [{"ok": False, "error": "running"}]

    async def go():
        task = asyncio.create_task(event_order.follow_after_opening())
        await asyncio.sleep(0.3)
        assert order["runs"] == []                                  # (the opening's own mark not settled yet)
        order["marks"][event_order._key(1, 1)] = {"state": "done"}
        return await task

    got = asyncio.run(go())
    assert got["ok"] and len(order["runs"]) == 2                    # (refused once: her line still sounding)


@pytest.mark.parametrize("opening", ["failed", "skipped"])
def test_a_card_1_that_did_not_go_the_whole_way_starts_nothing(order, opening):
    order["marks"][event_order._key(1, 1)] = {"state": opening}
    assert asyncio.run(event_order.follow_after_opening())["ok"] is False and order["runs"] == []


def test_a_stop_while_it_waits_starts_nothing(order):
    order["marks"][event_order._key(1, 1)] = {"state": "running"}

    async def go():
        task = asyncio.create_task(event_order.follow_after_opening())
        await asyncio.sleep(0.2)
        order["stops"] = (1,)                                       # (the red stop)
        order["marks"][event_order._key(1, 1)] = {"state": "done"}
        return await task

    got = asyncio.run(go())
    assert got["error"] == "stopped" and order["runs"] == []


def test_her_first_words_to_him_are_the_take_the_owner_picked_and_none_without_one(order, monkeypatch, tmp_path):
    import hashlib
    import io
    import json
    import wave

    from app import event_cues, event_host
    from app.config import settings

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(event_host.RECORDED_RATE)
        w.writeframes(b"\x01\x00" * 2400)
    take = buf.getvalue()
    (tmp_path / "data" / "events" / "clips").mkdir(parents=True)
    (tmp_path / "data" / "events" / "clips" / "card2_open_A.wav").write_bytes(take)
    event = tmp_path / "event.json"
    monkeypatch.setattr(event_host, "ROOT", tmp_path)
    monkeypatch.setattr(event_host, "_path", lambda: event)
    monkeypatch.setattr(settings, "event_recorded_lines", True)
    played = []

    async def play_line(line_id, text, pcm, *, lang="en", **kw):
        played.append((line_id, text, len(pcm), lang))
        return {"ok": True}

    monkeypatch.setattr(event_cues, "play_line", play_line)
    order["marks"][event_order._key(1, 1)] = {"state": "done"}
    event.write_text(json.dumps({"scenes": []}), encoding="utf-8")
    assert asyncio.run(event_order.follow_after_opening())["opener"]["error"] == "none" and played == []
    order["marks"].pop(event_order._key(1, 2))
    rec = {"file": "data/events/clips/card2_open_A.wav", "sha256": hashlib.sha256(take).hexdigest(),
           "text": "Khun Miki, I'm all set!", "lang": "en"}
    event.write_text(json.dumps({"scenes": [], "card2_open": rec}), encoding="utf-8")
    assert asyncio.run(event_order.follow_after_opening())["opener"]["ok"]
    assert played == [("card2_open", "Khun Miki, I'm all set!", 4800, "en")]
    order["marks"].pop(event_order._key(1, 2))
    event.write_text(json.dumps({"scenes": [], "card2_open": {**rec, "sha256": "0" * 64}}), encoding="utf-8")
    assert asyncio.run(event_order.follow_after_opening())["opener"]["error"] == "not_the_take" and len(played) == 1


def test_a_refusal_that_is_not_a_wait_is_the_answer_and_a_wait_runs_out(order):
    order["marks"][event_order._key(1, 1)] = {"state": "done"}
    order["answers"] = [{"ok": False, "error": "not_ceremony_app"}]
    assert asyncio.run(event_order.follow_after_opening())["error"] == "not_ceremony_app" and len(order["runs"]) == 1
    order["runs"].clear()
    order["answers"] = [{"ok": False, "error": "running"}] * 10
    got = asyncio.run(event_order.follow_after_opening())
    assert got["ok"] is False and 2 <= len(order["runs"]) <= 4     # (1.5 s of retries, then given up)
