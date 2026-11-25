"""Thai subtitle of Emma's LIVE answers on the ceremony hall screen (owner 24 Nov, "ถ้าเอาซับไทยที่หุ่นตอบพอ
ไม่มีซับคนพูด จะไวไหม"): app/live_subtitle.py, its hooks in session.py, face_mirror's live messages and the
page's side (client/voice-preview.html mirrorLive*) in a real Chromium."""
import asyncio
import inspect
import json
import time
from pathlib import Path

import pytest

from app import face_mirror, live_subtitle
from app.config import settings

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "client" / "voice-preview.html"


# ---- the sentence split --------------------------------------------------------------------

def test_split_cuts_at_stops_followed_by_a_space_and_keeps_numbers_whole():
    pieces, rest = live_subtitle.split_pieces(
        "The main pool is called Water Lagoon. It is 3.5 meters deep in the middle. And then")
    assert pieces == ["The main pool is called Water Lagoon.", "It is 3.5 meters deep in the middle."]
    assert rest == " And then"
    # a stop at the very end of what has come may be "3." of "3.5": it waits for the next chunk
    assert live_subtitle.split_pieces("It is about 600 meters from the beach.") == ([], "It is about 600 meters from the beach.")
    pieces, rest = live_subtitle.split_pieces("It is about 600 meters from the beach.", final=True)
    assert pieces == ["It is about 600 meters from the beach."] and rest == ""


def test_split_cuts_a_long_clause_at_its_last_comma_and_never_a_number():
    long = ("We have a sky pool on the roof level of building A, a main pool called Water Lagoon with a "
            "wide sun deck, and an underground pool with a quiet lounge for the residents of all buildings")
    pieces, rest = live_subtitle.split_pieces(long)
    assert pieces and all(len(p) <= live_subtitle.LONG_CLAUSE for p in pieces)
    assert (" ".join(pieces) + " " + rest.strip()).replace("  ", " ").strip() == long
    # "2,500" has a comma that is not followed by a space: never a cut
    pieces, rest = live_subtitle.split_pieces("x" * 40 + " about 2,500 square meters of facilities in all the project buildings here ok")
    assert pieces == []


def test_a_piece_too_short_to_stand_waits_for_the_next_one():
    assert live_subtitle.split_pieces("Yes. ") == ([], "Yes.")
    pieces, rest = live_subtitle.split_pieces("Yes. The main pool is called Water Lagoon. It is")
    assert pieces == ["Yes. The main pool is called Water Lagoon."]
    assert live_subtitle.split_pieces("Yes.", final=True) == (["Yes."], "")


# ---- her live answer reaches the hall as Thai ------------------------------------------------

@pytest.fixture()
def hall(monkeypatch):
    face_mirror.reset()
    live_subtitle._turn = None
    queue = asyncio.Queue()
    face_mirror._clients.add(queue)
    monkeypatch.setattr(settings, "ceremony_live_subtitle_th", True)
    monkeypatch.setattr(settings, "ceremony_follow_host", True)
    monkeypatch.setattr(settings, "ceremony_follow_cues", False)
    monkeypatch.setattr(settings, "ceremony_live_subtitle_timeout_s", 2.5)
    face_mirror.call_started()
    face_mirror.publish_json({"type": "ready", "output_rate": 24000, "profile": "ceremony"})
    while not queue.empty():
        queue.get_nowait()
    yield queue
    live_subtitle._turn = None
    face_mirror.reset()


def _drain(queue):
    return [json.loads(queue.get_nowait()) for _ in range(queue.qsize())]


def _thai(text):
    return "ไทย:" + text


async def _fake_translate(text):
    return _thai(text)


async def _settle(n=6):
    for _ in range(n):
        await asyncio.sleep(0.01)


def test_her_answer_goes_out_sentence_by_sentence_in_thai_with_marks_and_an_end(hall, monkeypatch):
    monkeypatch.setattr(live_subtitle, "translate", _fake_translate)

    async def run():
        for chunk in ("The main pool is called", " Water Lagoon.", " It is 1.30 meters", " deep in the middle of it. ",
                      "Come and see it."):
            live_subtitle.feed(chunk)
            await asyncio.sleep(0)
        live_subtitle.end_turn()
        await _settle()

    asyncio.run(run())
    got = _drain(hall)
    subs = [m for m in got if m["type"] == "mirror_subtitle"]
    assert [m["text"] for m in subs] == [_thai("The main pool is called Water Lagoon."),
                                         _thai("It is 1.30 meters deep in the middle of it."),
                                         _thai("Come and see it.")]
    assert all(m["live"] is True and m["id"].startswith("live-") for m in subs)
    assert [m["n"] for m in subs] == list(range(len(subs)))
    # the page's clock: a mark where the answer begins, one after every piece -- in the stream, before the Thai
    marks = [m for m in got if m["type"] == "mirror_live_mark"]
    assert marks[0]["n"] == 0 and [m["n"] for m in marks] == list(range(len(marks)))
    assert got.index(marks[0]) < got.index(subs[0])
    assert got[-1]["type"] == "mirror_subtitle_end" and got[-1]["id"] == subs[0]["id"] and got[-1]["cut"] is False
    assert not any("English" in json.dumps(m) for m in got)


def test_nothing_goes_out_for_the_host_or_outside_the_ceremony_or_with_the_switch_off(hall, monkeypatch):
    calls = []

    async def spy(text):
        calls.append(text)
        return _thai(text)

    monkeypatch.setattr(live_subtitle, "translate", spy)

    async def say(text="The main pool is called Water Lagoon and it is big."):
        live_subtitle.feed(text)
        live_subtitle.end_turn()
        await _settle()

    # the host's words never reach `feed`: the session's only call is in the assistant_transcript branch
    from app import session as session_module
    src = inspect.getsource(session_module.VoiceSession)
    user_branch = src.split('elif event.kind == "assistant_transcript":', 1)[0].split('if event.kind == "user_transcript"', 1)[-1]
    assert "live_subtitle" not in user_branch
    assistant_branch = src.split('elif event.kind == "assistant_transcript":', 1)[1].split('elif event.kind == "reconnected":', 1)[0]
    assert "live_subtitle.feed(event.text or \"\")" in assistant_branch
    assert "live_subtitle.cut()" in src.split('elif event.kind == "interrupted":', 1)[1].split('elif event.kind == "resumed":', 1)[0]
    assert "live_subtitle.end_turn()" in src.split('elif event.kind == "turn_complete":', 1)[1][:1800]

    def quiet(label):
        asyncio.run(say())
        assert _drain(hall) == [], label
        assert calls == [], label

    monkeypatch.setattr(settings, "ceremony_live_subtitle_th", False)
    quiet("switch off")
    monkeypatch.setattr(settings, "ceremony_live_subtitle_th", True)
    monkeypatch.setattr(settings, "ceremony_follow_cues", True)
    quiet("scripted cues: not live first")
    monkeypatch.setattr(settings, "ceremony_follow_cues", False)
    face_mirror.publish_json({"type": "ready", "output_rate": 24000, "profile": "condo"})
    _drain(hall)
    quiet("a call that is not the ceremony's")
    face_mirror.publish_json({"type": "ready", "output_rate": 24000, "profile": "ceremony"})
    _drain(hall)
    face_mirror.subtitle({"id": "s01_c1", "text": "บทของพิธี", "seconds": 5})
    _drain(hall)
    quiet("a cue's own line is up")
    face_mirror.subtitle_end("s01_c1")
    _drain(hall)
    face_mirror._clients.clear()
    quiet("no hall screen connected")


def test_a_failed_or_late_translation_shows_nothing_for_that_sentence_and_never_holds_her_audio(hall, monkeypatch):
    async def flaky(text):
        if "second" in text:
            raise RuntimeError("quota")
        if "third" in text:
            return None
        return _thai(text)

    monkeypatch.setattr(live_subtitle, "translate", flaky)

    async def run():
        t0 = time.perf_counter()
        live_subtitle.feed("This is the first sentence of the answer. This is the second sentence of the answer. "
                           "This is the third sentence of the answer. This is the fourth sentence here.")
        took = time.perf_counter() - t0
        live_subtitle.end_turn()
        await _settle(10)
        return took

    took = asyncio.run(run())
    assert took < 0.05                              # `feed` is a plain function: it cannot wait for a translation
    subs = [m for m in _drain(hall) if m["type"] == "mirror_subtitle"]
    assert [m["text"] for m in subs] == [_thai("This is the first sentence of the answer."),
                                         _thai("This is the fourth sentence here.")]


def test_the_real_translate_gives_up_at_the_timeout_and_on_an_answer_with_no_thai(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "k-not-real")
    monkeypatch.setattr(settings, "ceremony_live_subtitle_timeout_s", 0.05)

    class _Models:
        async def generate_content(self, **kw):
            await asyncio.sleep(5)

    class _Client:
        aio = type("A", (), {"models": _Models()})()

    monkeypatch.setattr(live_subtitle, "_the_client", lambda: _Client())

    async def run():
        t0 = time.perf_counter()
        out = await live_subtitle.translate("Hello there everyone.")
        return out, time.perf_counter() - t0

    out, took = asyncio.run(run())
    assert out is None and took < 1.0

    class _Reply:
        text = "Hello there everyone"

    class _Models2:
        async def generate_content(self, **kw):
            return _Reply()

    monkeypatch.setattr(settings, "ceremony_live_subtitle_timeout_s", 2.5)
    monkeypatch.setattr(live_subtitle, "_the_client", lambda: type("C", (), {"aio": type("A", (), {"models": _Models2()})()})())
    assert asyncio.run(live_subtitle.translate("Hello there everyone.")) is None        # never English on the band
    _Reply.text = "สวัสดีค่ะทุกท่าน"
    assert asyncio.run(live_subtitle.translate("Hello there everyone.")) == "สวัสดีค่ะทุกท่าน"
    monkeypatch.setattr(settings, "gemini_api_key", None)
    assert asyncio.run(live_subtitle.translate("Hello there everyone.")) is None


def test_sentences_go_out_in_the_order_she_said_them_whatever_the_translation_times(hall, monkeypatch):
    async def slow_first(text):
        await asyncio.sleep(0.08 if "first" in text else 0.0)
        return _thai(text)

    monkeypatch.setattr(live_subtitle, "translate", slow_first)

    async def run():
        live_subtitle.feed("This is the first sentence of the answer. This is the second sentence of the answer. ")
        live_subtitle.end_turn()
        await asyncio.sleep(0.3)

    asyncio.run(run())
    got = _drain(hall)
    subs = [m for m in got if m["type"] == "mirror_subtitle"]
    assert [m["n"] for m in subs] == [0, 1] and "first" in subs[0]["text"]
    assert got[-1]["type"] == "mirror_subtitle_end"


def test_interrupted_takes_the_line_down_and_drops_translations_still_out(hall, monkeypatch):
    async def slow(text):
        await asyncio.sleep(0.1)
        return _thai(text)

    monkeypatch.setattr(live_subtitle, "translate", slow)

    async def run():
        live_subtitle.feed("This is the first sentence of the answer. This is the second sentence of the answer. ")
        await asyncio.sleep(0.02)
        live_subtitle.cut()                         # session: `interrupted`
        live_subtitle.end_turn()                    # a turn_complete after it finds nothing
        await asyncio.sleep(0.3)

    asyncio.run(run())
    got = _drain(hall)
    assert [m for m in got if m["type"] == "mirror_subtitle"] == []                 # nothing came late
    ends = [m for m in got if m["type"] == "mirror_subtitle_end"]
    assert len(ends) == 1 and ends[0]["cut"] is True and ends[0]["id"].startswith("live-")


def test_the_red_stop_cuts_her_live_line_too(hall, monkeypatch):
    monkeypatch.setattr(live_subtitle, "translate", _fake_translate)

    async def run():
        live_subtitle.feed("This is the first sentence of the answer. This is")
        await _settle()
        _drain(hall)
        face_mirror.subtitle_cut()
        await _settle()

    asyncio.run(run())
    got = _drain(hall)
    assert any(m["type"] == "mirror_subtitle_end" and m.get("cut") and m.get("id", "").startswith("live-") for m in got)
    assert live_subtitle._turn is None


def test_the_page_and_the_server_agree_on_the_message_names():
    html = PAGE.read_text(encoding="utf-8")
    for name in ("mirror_live_mark", "mirrorLiveMark", "mirrorLiveShow", "mirrorLivePump", "mirrorLiveEndHeld"):
        assert name in html, name
    # the mirror rules (CLAUDE.md): the live code is in the mirror's own branch, no new name in onMessage / storyHook
    on_message = html.split("function onMessage(", 1)[1].split("\n    function ", 1)[0]
    assert "mirrorLive" not in on_message and "live_subtitle" not in on_message
    assert "function storyHook" not in html or "mirrorLive" not in html.split("function storyHook", 1)[1].split("\n    function ", 1)[0]
    mirror_branch = html.split("sock.onmessage = function (m) {", 1)[1].split("sock.onclose", 1)[0]
    assert "mirrorLiveMark(said)" in mirror_branch and "mirrorLiveShow(said)" in mirror_branch


# ---- the page, in a real Chromium ---------------------------------------------------------------

from tests.test_hall_voice_page import RATE, _open, _pcm, _state, browser, hall as hall_server  # noqa: E402,F401


def _band(page):
    return page.evaluate("() => { var b = document.querySelector('.mirror-sub'); "
                         "return b && b.classList.contains('on') ? b.textContent : null; }")


def _start(on_loop):
    on_loop(face_mirror.call_started)
    on_loop(face_mirror.publish_json, {"type": "ready", "output_rate": RATE, "profile": "ceremony"})
    on_loop(face_mirror.publish_audio, _pcm(0.1))


def test_the_page_shows_each_sentence_when_its_voice_gets_there_and_drops_the_rest_on_interrupted(browser, hall_server):
    port, on_loop = hall_server
    page, errors = _open(browser, port)
    page.wait_for_function("() => window.EmmaMirror.state().socket === 1", timeout=5000)
    _start(on_loop)
    page.wait_for_function("() => window.EmmaMirror.state().queued > 0.05", timeout=15000)
    # her voice is queued 5.6 s ahead: sentence 0 begins now, sentence 1 begins 1.6 s on, sentence 2 at 3.6 s
    on_loop(face_mirror.live_mark, 7, 0)
    on_loop(face_mirror.publish_audio, _pcm(1.5))
    on_loop(face_mirror.live_mark, 7, 1)
    on_loop(face_mirror.publish_audio, _pcm(2.0))
    on_loop(face_mirror.live_mark, 7, 2)
    on_loop(face_mirror.publish_audio, _pcm(2.0))
    time.sleep(0.2)
    assert _band(page) is None                      # nothing yet: no translation has landed
    on_loop(face_mirror.live_subtitle, 7, 0, "ประโยคแรก", 2.0)             # 0's mark is at the clock: up now
    page.wait_for_function("() => { var b = document.querySelector('.mirror-sub'); return b && b.classList.contains('on'); }", timeout=3000)
    assert "ประโยคแรก" in _band(page)
    on_loop(face_mirror.live_subtitle, 7, 1, "ประโยคที่สอง", 2.0)         # ...1's translation lands early: her voice is not there yet
    assert "ประโยคแรก" in _band(page)
    page.wait_for_function("() => document.querySelector('.mirror-sub').textContent.indexOf('ประโยคที่สอง') >= 0", timeout=6000)
    # sentence 2 is still waiting for its place when her voice is cut: it never goes up
    on_loop(face_mirror.live_subtitle, 7, 2, "ประโยคที่สาม", 2.0)
    on_loop(face_mirror.publish_json, {"type": "interrupted"})
    page.wait_for_function("() => !document.querySelector('.mirror-sub').classList.contains('on')", timeout=3000)
    time.sleep(0.5)
    assert _band(page) is None
    assert [e for e in errors if "WebGL" not in e] == []
    page.close()


def test_the_page_takes_her_line_down_when_the_answer_ends(browser, hall_server):
    port, on_loop = hall_server
    page, errors = _open(browser, port)
    page.wait_for_function("() => window.EmmaMirror.state().socket === 1", timeout=5000)
    _start(on_loop)
    page.wait_for_function("() => window.EmmaMirror.state().queued > 0.05", timeout=15000)
    on_loop(face_mirror.live_mark, 9, 0)
    on_loop(face_mirror.live_subtitle, 9, 0, "คำตอบสั้นๆค่ะ", 1.0)
    page.wait_for_function("() => document.querySelector('.mirror-sub') && document.querySelector('.mirror-sub').classList.contains('on')", timeout=3000)
    on_loop(face_mirror.live_subtitle_end, 9)
    page.wait_for_function("() => !document.querySelector('.mirror-sub').classList.contains('on')", timeout=9000)
    assert [e for e in errors if "WebGL" not in e] == []
    page.close()
