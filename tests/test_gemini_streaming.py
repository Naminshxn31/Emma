"""Speech boundaries and slow tools must not hold up the live audio stream."""
import asyncio
from types import SimpleNamespace as N

from app import tools
from app.providers.gemini import GeminiProvider
from app.vad_gate import VadGate


class LiveSession:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.responses = []
        self.input = []

    async def receive(self):
        while True:
            message = await self.incoming.get()
            if message is None:
                return
            yield message
            if getattr(getattr(message, "server_content", None), "turn_complete", False):
                return

    async def send_realtime_input(self, **payload):
        self.input.append(payload)

    async def send_tool_response(self, **payload):
        self.responses.append(payload)


def content(**fields):
    return N(server_content=N(**fields))


def call(name):
    return N(tool_call=N(function_calls=[N(id=name, name=name, args={})]))


async def take_until(stream, kind):
    events = []
    async with asyncio.timeout(2):
        async for event in stream:
            events.append(event)
            if event.kind == kind:
                return events
    raise AssertionError(f"stream ended before {kind}")


def test_slow_tools_do_not_hide_audio_or_content_in_the_same_message(monkeypatch):
    async def scenario():
        provider = GeminiProvider("voice", "test")
        provider._session = session = LiveSession()
        started, release = asyncio.Event(), asyncio.Event()

        async def dispatch(calls):
            started.set()
            await release.wait()
            return [(cid, name, {"ok": True}) for cid, name, _ in calls]

        monkeypatch.setattr(tools, "dispatch_all", dispatch)
        message = call("slow_lookup")
        message.session_resumption_update = N(new_handle="test-handle")
        message.server_content = N(
            output_transcription=N(text="Still speaking."),
            model_turn=N(parts=[N(inline_data=N(data=b"\x01\x02"))]),
            turn_complete=True,
        )
        session.incoming.put_nowait(message)
        stream = provider.events()
        try:
            events = await take_until(stream, "turn_complete")
            assert any(e.audio == b"\x01\x02" for e in events)
            assert any(e.text == "Still speaking." for e in events)
            assert provider._resume_handle == "test-handle"
            assert not session.responses
            await asyncio.wait_for(started.wait(), 2)
            # Interruption and incoming text must also pass the waiting tool.
            session.incoming.put_nowait(content(
                interrupted=True, input_transcription=N(text="A question.")))
            events = await take_until(stream, "user_transcript")
            assert "interrupted" in [e.kind for e in events]
            release.set()
            await take_until(stream, "tool_result")
            assert session.responses[0]["function_responses"][0].id == "slow_lookup"
        finally:
            await stream.aclose()
        assert provider._event_tasks == []
    asyncio.run(scenario())


def test_tool_batches_keep_order_while_audio_continues(monkeypatch):
    async def scenario():
        provider = GeminiProvider("voice", "test")
        provider._session = session = LiveSession()
        release = asyncio.Event()
        order = []

        async def dispatch(calls):
            name = calls[0][1]
            order.append(name)
            if name == "first":
                await release.wait()
            return [(cid, name, {"ok": True}) for cid, name, _ in calls]

        monkeypatch.setattr(tools, "dispatch_all", dispatch)
        for name in ("first", "second"):
            session.incoming.put_nowait(call(name))
        session.incoming.put_nowait(content(model_turn=N(parts=[N(inline_data=N(data=b"audio"))])))
        stream = provider.events()
        try:
            await take_until(stream, "audio")
            assert "second" not in order
            release.set()
            await take_until(stream, "tool_result")
            await take_until(stream, "tool_result")
            assert order == ["first", "second"]
            assert [r["function_responses"][0].id for r in session.responses] == order
        finally:
            await stream.aclose()
    asyncio.run(scenario())


def test_closing_stream_cancels_waiting_work_and_discards_queued_tools(monkeypatch):
    async def scenario():
        provider = GeminiProvider("voice", "test")
        provider._session = session = LiveSession()
        started, cancelled = asyncio.Event(), asyncio.Event()
        calls_seen = []

        async def dispatch(calls):
            calls_seen.extend(name for _, name, _ in calls)
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        monkeypatch.setattr(tools, "dispatch_all", dispatch)
        session.incoming.put_nowait(call("first"))
        session.incoming.put_nowait(call("must_not_run"))
        stream = provider.events()
        await take_until(stream, "tool_call")
        await asyncio.wait_for(started.wait(), 2)
        tasks = list(provider._event_tasks)
        await stream.aclose()
        assert cancelled.is_set()
        assert calls_seen == ["first"]
        assert all(task.done() for task in tasks)
        assert provider._tool_batches.empty()
        assert not session.responses
    asyncio.run(scenario())


def test_tool_failure_is_reported_and_stops_background_tasks(monkeypatch):
    async def dispatch(_):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(tools, "dispatch_all", dispatch)

    async def scenario():
        provider = GeminiProvider("voice", "test")
        provider._session = session = LiveSession()
        session.incoming.put_nowait(call("failure"))
        async with asyncio.timeout(2):
            events = [e async for e in provider.events()]
        assert events[-1].kind == "error"
        assert provider._event_tasks == []
    asyncio.run(scenario())


def test_retired_connections_do_not_send_tool_results_to_new_session(monkeypatch):
    async def scenario():
        provider = GeminiProvider("voice", "test")
        provider._session = old = LiveSession()
        replacement = LiveSession()

        async def dispatch(calls):
            provider._session = replacement
            return [(cid, name, {"ok": True}) for cid, name, _ in calls]

        monkeypatch.setattr(tools, "dispatch_all", dispatch)
        events = [e async for e in provider._run_tool_calls(call("lookup").tool_call.function_calls)]
        assert [e.kind for e in events] == ["tool_call"]
        assert not old.responses and not replacement.responses
    asyncio.run(scenario())


def test_local_vad_emits_one_start_per_accepted_segment_without_relaxing_floor():
    class Detector:
        def __init__(self):
            self.speaking = iter([False, True, True, True, False, True])

        def accept_waveform(self, _):
            pass

        def is_speech_detected(self):
            return next(self.speaking)

    async def scenario():
        provider = GeminiProvider("voice", "test")
        provider._session = session = LiveSession()
        provider._vad_gate = VadGate(Detector(), prefix_padding_ms=300,
                                     min_rms=0.04, floor_grace_s=0)
        quiet = b"\x10\x00" * 160
        loud = b"\x00\x10" * 160
        # Below-floor speech is still rejected. An accepted phrase can trail
        # off quietly; only the next accepted phrase opens a new boundary.
        for pcm in (quiet, quiet, loud, quiet, quiet, loud):
            await provider.send_audio(pcm)
        stream = provider.events()
        try:
            assert (await take_until(stream, "speech_started"))[-1].kind == "speech_started"
            assert (await take_until(stream, "speech_started"))[-1].kind == "speech_started"
            assert provider._event_queue.empty()
            assert sum("activity_start" in p for p in session.input) == 2
            assert sum("activity_end" in p for p in session.input) == 1
        finally:
            await stream.aclose()
    asyncio.run(scenario())


def test_input_ids_follow_transcription_boundaries_and_keep_interleaved_chunks():
    async def scenario():
        provider = GeminiProvider("voice", "test")
        provider._session = session = LiveSession()
        for message in (
            content(input_transcription=N(text="First ")),
            content(output_transcription=N(text="Reply before the last input chunk.")),
            content(input_transcription=N(text="question.")),
            content(turn_complete=True),
            content(input_transcription=N(text="Second question.", finished=True)),
            content(input_transcription=N(text="Third ")),
            content(interrupted=True, input_transcription=N(text="question.", finished=True)),
            content(turn_complete=True),
        ):
            session.incoming.put_nowait(message)
        stream = provider.events()
        try:
            events = await take_until(stream, "turn_complete")
            events += await take_until(stream, "turn_complete")
        finally:
            await stream.aclose()
        rows = {}
        for e in events:
            if e.kind == "user_transcript":
                key = e.data["utterance_id"]
                rows[key] = rows.get(key, "") + e.text
        assert list(rows.values()) == ["First question.", "Second question.", "Third question."]
    asyncio.run(scenario())


def test_unfinished_input_keeps_its_id_when_output_finishes_first():
    async def scenario():
        provider = GeminiProvider("voice", "test")
        provider._session = session = LiveSession()
        for message in (
            content(input_transcription=N(text="One ", finished=False)),
            content(turn_complete=True),
            content(input_transcription=N(text="question.", finished=True)),
            content(input_transcription=N(text="Another question.", finished=True)),
            content(turn_complete=True),
        ):
            session.incoming.put_nowait(message)
        stream = provider.events()
        try:
            events = await take_until(stream, "turn_complete")
            events += await take_until(stream, "turn_complete")
        finally:
            await stream.aclose()
        inputs = [e for e in events if e.kind == "user_transcript"]
        assert inputs[0].data == inputs[1].data
        assert inputs[1].data != inputs[2].data
    asyncio.run(scenario())


def test_cancelled_queued_tools_never_execute_and_running_result_is_not_sent(monkeypatch):
    async def scenario():
        provider = GeminiProvider("voice", "test")
        provider._session = session = LiveSession()
        started, release = asyncio.Event(), asyncio.Event()
        executed = []

        async def dispatch(calls):
            executed.extend(name for _, name, _ in calls)
            if calls[0][1] == "running":
                started.set()
                await release.wait()
            return [(cid, name, {"ok": True}) for cid, name, _ in calls]

        monkeypatch.setattr(tools, "dispatch_all", dispatch)
        batch = call("running")
        batch.tool_call.function_calls.extend(call("queued").tool_call.function_calls)
        session.incoming.put_nowait(batch)
        stream = provider.events()
        try:
            await take_until(stream, "tool_call")
            await asyncio.wait_for(started.wait(), 2)
            session.incoming.put_nowait(N(tool_call_cancellation=N(ids=["running", "queued"]),
                                         server_content=N(interrupted=True)))
            await take_until(stream, "interrupted")
            session.incoming.put_nowait(call("next"))
            release.set()
            await take_until(stream, "tool_result")
            assert executed == ["running", "next"]
            assert [r["function_responses"][0].id for r in session.responses] == ["next"]
        finally:
            await stream.aclose()
    asyncio.run(scenario())
