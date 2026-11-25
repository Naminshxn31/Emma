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


# ---- the opener stands for the first line and the script goes on (owner 25 Nov ~11:00: "ขอแนะนำตัวอีกครั้งดิฉัน emma
# แล้วก็ตามบทต่อเลย ไม่ต้องทักมิกิ เพราะว่าตอนการ์ดแรกมีไปจับมือทักทายแล้ว") ----------------------------------------------

THEN_SCENES = [{"n": 1, "steps": [{"i": 0, "do": "manual"}, {"i": 1, "do": "opening"}, {"i": 2, "do": "follow"}]},
               {"n": 2, "steps": [{"i": 0, "do": "cue", "cue": "s02_l03"}, {"i": 1, "do": "cue", "cue": "s02_l05"},
                                  {"i": 2, "do": "cue", "cue": "s02_l09"}]}]


@pytest.fixture
def after(monkeypatch):
    from app import ceremony_follow, event_cues

    state = {"played": [], "stops": (0,), "audible": [False], "talking": False}
    monkeypatch.setattr(event_order, "_scenes", lambda: THEN_SCENES)
    monkeypatch.setattr(event_order, "_stop_gen", lambda: state["stops"])
    monkeypatch.setattr(event_order, "OPENER_THEN_PAUSE_S", 0.0)
    monkeypatch.setitem(event_order.STATE, "marks", {})
    monkeypatch.setattr(event_cues, "line_audible", lambda now=None: state["audible"].pop(0) if len(state["audible"]) > 1
                        else state["audible"][0])
    monkeypatch.setattr(ceremony_follow, "_host_talking", lambda: state["talking"])
    monkeypatch.setitem(ceremony_follow.STATE, "on", True)
    monkeypatch.setitem(ceremony_follow.STATE, "paused", None)

    async def play(cand, res, by, skipped=None):
        state["played"].append((cand["n"], cand["i"], cand["cue"], by, [(s["n"], s["i"]) for s in skipped or []]))
        return {"ok": True}

    monkeypatch.setattr(ceremony_follow, "_play", play)
    return state


def test_after_the_opener_the_next_line_is_pressed_and_the_line_it_stands_for_skipped(after):
    after["audible"] = [True, True, False]                          # (her opener still sounding, then over)
    asyncio.run(event_order._after_opener("s02_l05", "s02_l03", (0,), 1.0))
    assert after["played"] == [(2, 1, "s02_l05", "card2_open", [(2, 0)])]


@pytest.mark.parametrize("why", ["stop", "follow_off", "paused", "taken", "no_step"])
def test_nothing_follows_the_opener_after_a_stop_with_card_2_off_or_the_line_taken(after, monkeypatch, why):
    from app import ceremony_follow

    then = "s02_l05"
    if why == "stop":
        after["stops"] = (1,)
    elif why == "follow_off":
        monkeypatch.setitem(ceremony_follow.STATE, "on", False)
    elif why == "paused":
        monkeypatch.setitem(ceremony_follow.STATE, "paused", "staff")
    elif why == "taken":
        event_order.STATE["marks"][event_order._key(2, 1)] = {"state": "done"}    # (he asked for it meanwhile)
    else:
        then = "s99_l99"
    asyncio.run(event_order._after_opener(then, "s02_l03", (0,), 1.0))
    assert after["played"] == []


def test_the_line_it_stands_for_already_said_is_not_skipped(after):
    event_order.STATE["marks"][event_order._key(2, 0)] = {"state": "done"}
    asyncio.run(event_order._after_opener("s02_l05", "s02_l03", (0,), 1.0))
    assert after["played"] == [(2, 1, "s02_l05", "card2_open", [])]


def test_the_whole_road_the_opener_with_its_thai_line_then_the_script(order, after, monkeypatch, tmp_path):
    import hashlib
    import io
    import json
    import wave

    from app import event_cues, event_host, event_script
    from app.config import settings

    monkeypatch.setattr(event_order, "_scenes", lambda: THEN_SCENES)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(event_host.RECORDED_RATE)
        w.writeframes(b"\x01\x00" * 2400)
    take = buf.getvalue()
    (tmp_path / "data" / "events" / "clips").mkdir(parents=True)
    (tmp_path / "data" / "events" / "clips" / "card2_open.wav").write_bytes(take)
    event = tmp_path / "event.json"
    monkeypatch.setattr(event_host, "ROOT", tmp_path)
    monkeypatch.setattr(event_host, "_path", lambda: event)
    monkeypatch.setattr(settings, "event_recorded_lines", True)
    monkeypatch.setattr(event_script, "cue", lambda cue_id, doc=None: {"scene_th": "ฉากสอง"} if cue_id == "s02_l05" else None)
    lines = []

    async def play_line(line_id, text, pcm, *, lang="en", scene_th=None, still=None, sub_th=None, slide=None):
        lines.append((line_id, text, lang, sub_th, scene_th))
        films.append(slide)
        return {"ok": True}

    films, asked = [], []
    from app import ceremony_slides

    # its film: the signed film of the line it stands in for, retitled (tests/test_card2_opener_film.py builds it)
    monkeypatch.setattr(ceremony_slides, "opener_slide", lambda base, en, th, lang="en": (
        asked.append((base, en, th, lang)) or {"template": "act", "film_of": base}))
    monkeypatch.setattr(event_cues, "play_line", play_line)
    event_order.STATE["marks"][event_order._key(1, 1)] = {"state": "done"}       # (`after` gave the order its own marks)
    rec = {"file": "data/events/clips/card2_open.wav", "sha256": hashlib.sha256(take).hexdigest(),
           "text": "Allow me to introduce myself once again.", "lang": "en", "sub_th": "ขออนุญาตแนะนำตัวอีกครั้งนะคะ",
           "then": "s02_l05", "instead_of": "s02_l03"}
    event.write_text(json.dumps({"scenes": [], "card2_open": rec}), encoding="utf-8")

    async def go():
        got = await event_order.follow_after_opening()
        for _ in range(40):                                         # (the line after it runs on its own)
            if after["played"]:
                break
            await asyncio.sleep(0.05)
        return got

    got = asyncio.run(go())
    assert got["opener"]["ok"]
    assert lines == [("card2_open", "Allow me to introduce myself once again.", "en", "ขออนุญาตแนะนำตัวอีกครั้งนะคะ", "ฉากสอง")]
    assert asked == [("s02_l03", "Allow me to introduce myself once again.", "ขออนุญาตแนะนำตัวอีกครั้งนะคะ", "en")]
    assert films == [{"template": "act", "film_of": "s02_l03"}]
    assert after["played"] == [(2, 1, "s02_l05", "card2_open", [(2, 0)])]


@pytest.mark.filterwarnings("ignore:coroutine '_recorded_line' was never awaited:RuntimeWarning")
def test_a_thai_line_given_to_a_recording_rides_to_the_hall_screen(monkeypatch):
    """event_cues.play_line's `sub_th`: the cue it builds carries it, and `_subtitle` sends it with the scene's name."""
    from types import SimpleNamespace

    from app import event_cues, face_mirror

    provider = SimpleNamespace(plays_recorded=True)
    monkeypatch.setattr(event_cues, "_live_call", lambda: SimpleNamespace(provider=provider))
    monkeypatch.setattr(event_cues, "_quiet", lambda: None)
    monkeypatch.setattr(event_cues, "_offline", lambda p: False)
    monkeypatch.setattr(event_cues, "_running", lambda cue_id: False)
    monkeypatch.setattr(event_cues, "_slide", lambda c: None)
    monkeypatch.setitem(event_cues.STATE, "runs", {})
    runs = []

    def spawn(coro):
        coro.close()                                                # (the player itself is not run here)
        run = max(event_cues.STATE["runs"].values(), key=lambda r: r["seq"])
        runs.append(run)
        run["started"].set_result({"ok": True})

    monkeypatch.setattr(event_cues, "_spawn", spawn)
    sent = []
    monkeypatch.setattr(face_mirror, "subtitle", lambda line: sent.append(line))
    got = asyncio.run(event_cues.play_line("card2_open", "Allow me to introduce myself once again.", b"\x00\x00" * 24000,
                                           lang="en", scene_th="ฉากสอง", sub_th="  ขออนุญาต   แนะนำตัวอีกครั้งนะคะ "))
    assert got == {"ok": True}
    assert runs[0]["cue"]["sub_th"] == "ขออนุญาต แนะนำตัวอีกครั้งนะคะ"
    event_cues._subtitle(runs[0], 1.0)
    assert sent and sent[0]["text"] == "ขออนุญาต แนะนำตัวอีกครั้งนะคะ" and sent[0]["scene"] == "ฉากสอง"
    # without it, as before: no Thai line goes up
    runs.clear()
    sent.clear()
    asyncio.run(event_cues.play_line("debut", "Hello, I'm Emma!", b"\x00\x00" * 24000, lang="en"))
    assert "sub_th" not in runs[0]["cue"]
    event_cues._subtitle(runs[0], 1.0)
    assert sent == []
