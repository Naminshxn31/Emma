"""
The robot speaking without being asked.

Four callers grew this behaviour independently and only one of them got it
right, so these are tests of the shared path rather than of any one caller —
the point of `app/events.py` is that a camera added next month cannot get it
wrong in a fifth new way.
"""
from __future__ import annotations

import asyncio

import pytest

from app import display, events


class Provider:
    """Records what reached the model, and whether two sends overlapped."""

    def __init__(self, delay: float = 0.0) -> None:
        self.said: list[str] = []
        self.delay = delay
        self.inflight = 0
        self.most_at_once = 0

    async def send_text(self, text: str) -> None:
        self.inflight += 1
        self.most_at_once = max(self.most_at_once, self.inflight)
        if self.delay:
            await asyncio.sleep(self.delay)
        self.said.append(text)
        self.inflight -= 1


class Session:
    def __init__(self, delay: float = 0.0) -> None:
        self.provider = Provider(delay)


@pytest.fixture()
def live(monkeypatch):
    from app import session as session_module

    session = Session()
    monkeypatch.setattr(session_module, "_active", session)
    return session


def test_an_announcement_waits_until_the_guest_has_caught_up(live):
    """The bug, in the one place it can now be fixed for everybody.

    Text sent into the conversation while the browser still holds unplayed
    audio is a barge-in: Gemini cancels its own generation and throws away
    the rest of what it was saying. Measured on the tour before this rule
    reached it — 21 of 81 slides delivered under 60% of their script.
    """
    async def body():
        display.set_audio_lead(20_000)      # twenty seconds still unheard
        pending = asyncio.create_task(
            events.announce("ถึงแล้วค่ะ", source="test", max_wait=5, then_pause=0)
        )
        await asyncio.sleep(0.05)
        assert live.provider.said == [], (
            "spoke over twenty seconds of queued narration — the guest loses "
            "the rest of the sentence, not just this announcement"
        )

        display.set_audio_lead(0)           # the guest has caught up
        assert await pending is True
        assert live.provider.said == ["ถึงแล้วค่ะ"]

    asyncio.run(body())


def test_relevance_is_judged_after_the_wait_not_before_it(live):
    """Waiting is not free — it can take forty-five seconds, and the reason
    for speaking can evaporate inside that window. The tour hit this first:
    the model usually calls next_slide by itself, so a nudge that was fair
    when scheduled would skip a slide by the time it landed."""
    still_true = {"value": True}

    async def body():
        display.set_audio_lead(2_000)
        pending = asyncio.create_task(events.announce(
            "ไปต่อ", source="test",
            still_relevant=lambda: still_true["value"],
            max_wait=5, then_pause=0,
        ))
        await asyncio.sleep(0.05)

        # Whatever we were going to announce, someone else handled it.
        still_true["value"] = False
        display.set_audio_lead(0)

        assert await pending is False
        assert live.provider.said == [], "announced something no longer true"

    asyncio.run(body())


def test_the_next_guest_is_not_told_the_last_guests_news(monkeypatch):
    """Sessions are handed over mid-conversation — a reload, a second tab,
    somebody walking away. An announcement that was waiting through the
    handover belongs to a conversation that is over. Delivering it to whoever
    is standing there now is worse than dropping it: "ถึงห้องตัวอย่างแล้ว" to
    a person who never asked to go anywhere."""
    from app import session as session_module

    first, second = Session(), Session()
    monkeypatch.setattr(session_module, "_active", first)

    async def body():
        display.set_audio_lead(2_000)
        pending = asyncio.create_task(
            events.announce("ถึงแล้วค่ะ", source="test", max_wait=5, then_pause=0)
        )
        await asyncio.sleep(0.05)

        session_module._active = second     # a new tab takes the robot over
        display.set_audio_lead(0)

        assert await pending is False
        assert first.provider.said == []
        assert second.provider.said == [], "told the new guest about the old one"

    asyncio.run(body())


def test_two_events_at_once_do_not_talk_over_each_other(monkeypatch):
    """With one event source there was nothing to collide with. With a robot,
    a camera and a clock there will be — and two texts arriving together is
    the barge-in bug again, with the robot as its own interrupter."""
    from app import session as session_module

    session = Session(delay=0.05)
    monkeypatch.setattr(session_module, "_active", session)

    async def body():
        display.set_audio_lead(0)
        await asyncio.gather(
            events.announce("หนึ่ง", source="first", max_wait=5, then_pause=0),
            events.announce("สอง", source="second", max_wait=5, then_pause=0),
        )
        assert session.provider.most_at_once == 1, "two announcements overlapped"
        assert sorted(session.provider.said) == ["สอง", "หนึ่ง"], "one was lost"

    asyncio.run(body())


def test_nobody_listening_is_not_an_error(monkeypatch):
    """Events fire on their own clocks, so they will fire into an empty room.
    A robot finishing its walk after the guest left must not raise."""
    from app import session as session_module

    monkeypatch.setattr(session_module, "_active", None)
    assert asyncio.run(events.announce("ถึงแล้ว", source="test")) is False


def test_a_provider_that_fails_does_not_take_the_caller_down(live):
    """Announcements run in background tasks nobody awaits. An exception here
    is a task that dies unobserved, which is how a silent robot happens."""
    async def boom(text):
        raise RuntimeError("socket closed")

    live.provider.send_text = boom

    async def body():
        display.set_audio_lead(0)
        assert await events.announce(
            "ถึงแล้ว", source="test", max_wait=5, then_pause=0
        ) is False

    asyncio.run(body())
