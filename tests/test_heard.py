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
