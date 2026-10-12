"""Per-session captions for the body/nav guards (design §2d step 4, safety
review #7).

`heard._LAST` was one global holding the latest chunk. A caption landing
*after* the tool call left the previous utterance in place, so "ยกแขนได้ไหม"
(a question) could authorise the arm the model raised a sentence later. The
store here is per session, timestamped, cut at every `activity_start`, aged,
and spendable once. `last()` is untouched for the slide tools —
tests/test_heard.py runs unmodified.
"""
from __future__ import annotations

import asyncio
import types

from app import heard
from app.config import settings


class S:
    """A session stand-in. Plain class (hashable by identity)."""


def test_fresh_caption_after_activity_start():
    s = S()
    heard.mark_activity(s, now=100.0)
    heard.record("ยกแขนซ้าย", s, utterance_id="1", now=100.5)
    cap = heard.caption_for(s, now=101.0)
    assert cap is not None and cap.text == "ยกแขนซ้าย" and cap.at == 100.5


def test_caption_before_the_latest_activity_start_is_stale():
    """The case that motivated all of this: the question was captioned, the
    guest started speaking again, the tool call came before the new caption."""
    s = S()
    heard.record("ยกแขนได้ไหม", s, utterance_id="1", now=100.0)
    heard.mark_activity(s, now=103.0)
    assert heard.caption_for(s, now=103.5) is None
    assert heard.latest_caption(s).text == "ยกแขนได้ไหม"     # still kept for logs


def test_age_bound(monkeypatch):
    s = S()
    heard.record("ก้มหน้าหน่อย", s, now=100.0)
    assert heard.caption_for(s, now=110.0) is not None                 # 10 s: ok
    assert heard.caption_for(s, now=110.01) is None                    # older
    monkeypatch.setattr(settings, "robot_heard_max_age_s", 30.0)
    assert heard.caption_for(s, now=120.0) is not None
    assert heard.caption_for(s, now=120.0, max_age_s=5) is None


def test_server_side_vad_has_only_the_age_bound():
    """No activity marks at all (VAD_MODE=gemini): the weaker case. A caption
    of the previous sentence still counts while it is young — the reason the
    guard is also single-use."""
    s = S()
    heard.record("ยกแขนได้ไหม", s, utterance_id="1", now=100.0)
    heard.record("โอเค", s, utterance_id="2", now=103.0)
    cap = heard.caption_for(s, now=104.0)
    assert cap.text == "โอเค"


def test_sessions_are_isolated():
    a, b = S(), S()
    heard.record("ยกแขนซ้าย", a, now=100.0)
    assert heard.caption_for(b, now=100.1) is None
    heard.mark_activity(b, now=101.0)
    assert heard.caption_for(a, now=101.5) is not None
    heard.forget(a)
    assert heard.latest_caption(a) is None


def test_chunks_of_one_utterance_join_and_keep_their_start_time():
    s = S()
    heard.mark_activity(s, now=99.0)
    heard.record("ก้ม", s, utterance_id="7", now=100.0)
    heard.record("หน้าหน่อย", s, utterance_id="7", now=100.4)
    cap = heard.caption_for(s, now=101.0)
    assert cap.text == "ก้มหน้าหน่อย" and cap.at == 100.0


def test_new_speech_splits_a_caption_even_under_the_same_utterance_id():
    """Gemini can keep one input turn id across two VAD segments. Joining
    them would date "เอาเลย" back to the question before it."""
    s = S()
    heard.record("ยกแขนได้ไหม", s, utterance_id="7", now=100.0)
    heard.mark_activity(s, now=102.0)
    heard.record("เอาเลย", s, utterance_id="7", now=102.5)
    cap = heard.caption_for(s, now=103.0)
    assert cap.text == "เอาเลย" and cap.at == 102.5


def test_a_caption_is_spent_once():
    s = S()
    heard.record("ยกแขนซ้าย", s, now=100.0)
    cap = heard.caption_for(s, now=100.5)
    assert heard.consume(s, cap) is True
    assert heard.consume(s, heard.caption_for(s, now=100.6)) is False
    heard.record("ยกแขนขวา", s, now=101.0)
    assert heard.consume(s, heard.caption_for(s, now=101.1)) is True


def test_last_is_unchanged_for_the_slide_tools():
    s = S()
    heard.record("ปิดสไลด์ด้วยค่ะ", s, utterance_id="1", now=1.0)
    heard.record(" หน่อย ", s, utterance_id="1", now=1.1)
    assert heard.last() == "หน่อย"          # the latest chunk, stripped, as before
    heard.record("bit like")                # no session: global only
    assert heard.last() == "bit like"
    assert heard.latest_caption(s).text == "ปิดสไลด์ด้วยค่ะ หน่อย"


def test_forget_without_a_session_forgets_everything():
    s = S()
    heard.record("ยกแขนซ้าย", s, now=1.0)
    heard.mark_activity(s, now=0.5)
    heard.forget()
    assert heard.last() == "" and heard.latest_caption(s) is None


def test_bound_session_is_what_the_provider_marks():
    """The provider has no reference to its session; `VoiceSession.run`
    binds it into the task context and `mark_activity()` finds it there."""
    s = S()

    async def scenario():
        heard.bind_session(s)
        heard.record("ยกแขนซ้าย", now=100.0)
        heard.mark_activity(now=101.0)
        return heard.caption_for(now=101.5)

    assert asyncio.run(scenario()) is None
    assert heard.latest_caption(s).text == "ยกแขนซ้าย"


def test_gemini_marks_activity_at_the_activity_start_send(monkeypatch):
    """design §2d: the boundary is the user's own activity_start (VAD local),
    marked where gemini.py sends it."""
    from app.providers import gemini

    s = S()
    sent = []

    class Gate:
        def feed(self, pcm):
            return [("start", b""), ("audio", b"\0\0")]

    class Live:
        async def send_realtime_input(self, **kw):
            sent.append(sorted(kw))

    provider = gemini.GeminiProvider.__new__(gemini.GeminiProvider)
    provider._session = Live()
    provider._vad_gate = Gate()
    provider._event_queue = asyncio.Queue()
    provider._speech_ended_at = None
    provider._replied_since_end = False
    provider.input_sample_rate = 16000

    async def scenario():
        heard.bind_session(s)
        heard.record("ยกแขนได้ไหม", now=0.0)
        await provider.send_audio(b"\0\0")

    asyncio.run(scenario())
    assert ["activity_start"] in sent
    assert heard.caption_for(s, max_age_s=1e9) is None


def test_session_records_per_session_captions_with_the_utterance_id():
    from app import session as session_module

    sess = session_module.VoiceSession.__new__(session_module.VoiceSession)
    sess.ws = types.SimpleNamespace()
    sent = []

    async def send_json(payload):
        sent.append(payload)

    sess._send_json = send_json

    class Provider:
        async def events(self):
            from app.providers.base import ProviderEvent

            yield ProviderEvent(kind="user_transcript", text="ยกแขน",
                                data={"utterance_id": "3"})
            yield ProviderEvent(kind="user_transcript", text="ซ้าย",
                                data={"utterance_id": "3"})

    sess.provider = Provider()
    asyncio.run(sess._provider_to_browser())
    cap = heard.latest_caption(sess)
    assert cap.text == "ยกแขนซ้าย" and cap.utterance == "3"
    assert heard.last() == "ซ้าย"
