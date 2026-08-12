"""
Actions need a word that asked for them.

Emma's own voice returns through the speakers, the VAD reads it as a guest
starting to talk, and whatever it transcribes arrives as a request. In one
recorded run that closed the presentation:

    ผู้ช่วย: ...Embassy World พร้อมเป็นส่วนหนึ่งของความฝันนั้นค่ะ
    ลูกค้า: bit like
    ผู้ช่วย: ได้ค่ะ ปิดสไลด์เรียบร้อยแล้วค่ะ

Nobody said "bit like". These tests are about the guard on the consequence:
we cannot tell echo from speech, but we can tell "ปิดสไลด์" from "bit like".
"""
from __future__ import annotations

import asyncio

import pytest

from app import heard


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _forget():
    heard.forget()
    yield
    heard.forget()


def _fake_canva(monkeypatch, fake):
    """Patch the *package attribute*, not `sys.modules`.

    `close_presentation` does `from app.tools import canva_display`, which
    reads `app.tools.canva_display` as an attribute of the already-imported
    package. Replacing the `sys.modules` entry leaves that attribute alone,
    so the real browser shutdown runs and the test passes for the wrong
    reason. Exactly the trap that made
    `test_connect_opens_the_session_on_this_provider_s_model` pass alone and
    fail in the suite.
    """
    import app.tools
    monkeypatch.setattr(app.tools, "canva_display", fake, raising=False)


def test_the_transcript_that_closed_the_deck_does_not_ask_for_anything():
    """The actual string, from the actual run."""
    assert heard.asks_to_stop("bit like") is False


def test_silence_is_not_a_request():
    """An interruption with nothing transcribed is the commonest echo, and
    the least defensible thing to act on."""
    assert heard.asks_to_stop("") is False
    assert heard.asks_to_stop(None) is False


@pytest.mark.parametrize("said", [
    "ปิดสไลด์ด้วยค่ะ", "พอแล้วค่ะ", "หยุดก่อนนะ", "ไม่ต้องพรีเซนต์แล้ว",
    "stop", "that's enough", "关闭", "그만",
])
def test_a_real_request_still_works(said):
    assert heard.asks_to_stop(said) is True


@pytest.mark.parametrize("said", [
    "เปิดสไลด์หน่อย",      # ปิด inside เปิด — the opposite instruction
    "พอดีเลยค่ะ",          # พอ inside พอดี
    "เพราะอะไรคะ",         # พอ must not collide inside เพราะ
    "ขอดูห้องนอนหน่อย",
    "มีฟิตเนสไหม",
])
def test_thai_is_matched_as_words_not_substrings(said):
    """`term in text` finds a word inside an unrelated one constantly, and
    Thai has no spaces to stop it. `knowledge._is_commercial` shipped that
    bug once — `งบ` inside `ยังไงบ้าง` refused ordinary questions. `ปิด`
    inside `เปิด` would be worse: it means the opposite."""
    assert heard.asks_to_stop(said) is False


def test_close_presentation_refuses_when_nobody_asked(monkeypatch):
    from app.tools import slides

    heard.record("bit like")
    closed = []

    class Fake:
        @staticmethod
        async def shutdown():
            closed.append(True)

    _fake_canva(monkeypatch, Fake)
    out = run(slides.close_presentation())

    assert out["ok"] is False
    assert out["closed"] is False
    assert out["needs_confirmation"] is True
    assert closed == [], "the window must still be open"
    assert "ถาม" in out["instruction"]


def test_close_presentation_still_closes_when_asked(monkeypatch):
    """The guard must not cost the feature. A guest standing in front of a
    wall-mounted fullscreen deck has no keyboard."""
    from app.tools import slides

    heard.record("ปิดสไลด์ด้วยค่ะ")
    closed = []

    class Fake:
        @staticmethod
        async def shutdown():
            closed.append(True)

    _fake_canva(monkeypatch, Fake)
    out = run(slides.close_presentation())

    assert out["ok"] is True and out["closed"] is True
    assert closed == [True]


# ============ คำถามที่หาไม่เจอ ต้องถูกบันทึก ============


def test_a_question_that_found_nothing_is_written_to_the_log(monkeypatch, tmp_path):
    """The one line in the log that says what the robot *couldn't* do.

    Keywords on the slides are the only change that would measurably improve
    search, and the words worth adding are the ones real guests missed with.
    They exist for the length of one sentence unless something writes them
    down. `scripts/what_guests_ask.py` reads exactly this event.
    """
    from app import turnlog
    from app.tools import knowledge

    written = []
    monkeypatch.setattr(turnlog, "record",
                        lambda event, **f: written.append((event, f)))
    monkeypatch.setattr(knowledge, "turnlog", turnlog, raising=False)

    out = knowledge.search_condo_info("มีสนามกอล์ฟไหม")
    assert out["found"] is False

    misses = [f for event, f in written if event == "lookup" and not f.get("showed")]
    assert misses, "a question nobody could answer left no trace"
    assert misses[0]["query"] == "มีสนามกอล์ฟไหม"
    assert misses[0]["found"] is False


@pytest.mark.parametrize("said", [
    "ปิดไฟหน่อย", "ปิดไฟ", "ปิดแอร์", "ปิดเครื่องปรับอากาศ", "turn off the lights",
])
def test_switching_the_room_off_is_not_closing_the_deck(said):
    """From the real logs: ไฟ 27x, เปิด 19x, ปิด 15x. Switching the lights is
    the commonest thing anybody says to this robot, and it uses the same verb.

    `ปิดไฟ` survives on its own only because the tokenizer keeps it as one
    word. `ปิดแอร์` splits into `ปิด` + `แอร์`, and without the room-word
    check the guard read "turn the air conditioning off" as permission to
    close the presentation. Depending on which compounds a dictionary
    happens to contain is not a design.
    """
    from app import heard
    assert heard.asks_to_stop(said) is False


def test_asking_for_both_still_closes_the_deck():
    """The room check must not swallow a real request that mentions both."""
    from app import heard
    assert heard.asks_to_stop("ปิดไฟกับปิดสไลด์ด้วยค่ะ") is True
