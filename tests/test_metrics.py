import asyncio
import json
from types import SimpleNamespace

import pytest

from app import metrics, turnlog
from scripts.report_metrics import summarize


@pytest.fixture
def observed(monkeypatch):
    rows, now = [], [10.0]
    monkeypatch.setattr(turnlog, "record", lambda event, **fields: rows.append({"event": event, **fields}))
    return metrics.SessionMetrics("openai", "test", "vad", clock=lambda: now[0]), rows, now


def test_first_audio_one_clock_and_no_previous_turn_signal(observed):
    obs, rows, now = observed
    obs.speech_end("test_vad")
    now[0] += .3
    obs.ensure()
    now[0] += .2
    first = obs.audio()
    assert obs.audio() is None
    assert rows[-1]["end_signal_to_audio_ms"] == 500
    assert rows[-1]["reply_start_to_audio_ms"] == 200
    obs.finish()
    assert obs.audio() != first
    assert rows[-1]["end_signal_source"] == "unavailable"


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, "3", 120001, {}])
def test_invalid_client_metric_ignored(observed, value):
    obs, rows, _ = observed
    tid = obs.ensure()
    before = len(rows)
    obs.client({"turn_id": tid, "name": "playback_schedule_ms", "ms": value})
    assert len(rows) == before


def test_client_metrics_cannot_cross_sessions_or_grow_unbounded(observed):
    obs, rows, _ = observed
    tid = obs.ensure()
    msg = {"turn_id": tid, "name": "playback_schedule_ms", "ms": 32, "secret": "PRIVATE"}
    obs.client(msg)
    obs.client(msg)
    metrics.SessionMetrics("gemini").client(msg)
    assert len([r for r in rows if r["event"] == "metric_client"]) == 1
    assert "PRIVATE" not in json.dumps(rows)
    for _ in range(100):
        obs.finish()
        obs.ensure()
    assert len(obs.recent) == 32 and tid not in obs.recent


def test_usage_identity_and_snapshot_semantics(observed):
    obs, rows, _ = observed
    event = {"source": "openai_response", "response_id": "r1", "input_tokens": 10,
             "output_tokens": 2, "total_tokens": 12, "text": "PRIVATE"}
    obs.usage(event)
    obs.usage(event)
    obs.usage({**event, "response_id": "r2"})
    obs.usage({"source": "gemini_report", "total_token_count": 40})
    obs.usage({"source": "gemini_report", "total_token_count": 30})
    result = summarize(rows + [r for r in rows if r["event"] == "metric_usage"])[0]
    assert result["response_usage_tokens"]["total_tokens"] == 24
    assert result["gemini_latest_snapshot_not_total"]["total_token_count"] == 30
    assert "PRIVATE" not in json.dumps(rows)


def test_report_percentiles_and_missing_signals():
    rows = [{"event": "said", "text": "PRIVATE"}]
    rows += [{"event": "metric_first_audio", "turn_id": str(i), "provider": "gemini",
              "end_signal_source": "unavailable", "reply_start_to_audio_ms": value}
             for i, value in enumerate([10, 20, 30, 40])]
    out = summarize(rows)[0]
    assert out["latency_ms"]["reply_start_to_audio_ms"] == {"n": 4, "p50": 25, "p95": 38.5}
    assert out["missing_end_signal"] == 4
    assert "end_signal_to_audio_ms" not in out["latency_ms"]
    assert "PRIVATE" not in json.dumps(out)


def test_dispatch_records_timeout_and_preserves_outcome(observed, monkeypatch):
    from app.tools import registry
    obs, rows, now = observed
    async def fail(name, args):
        now[0] += .4
        return {"ok": False, "error": "timeout"}
    monkeypatch.setattr(registry, "_dispatch", fail)
    async def run():
        token = metrics.active.set(obs)
        try:
            return await registry.dispatch("unknown", {"phone": "PRIVATE"})
        finally:
            metrics.active.reset(token)
    assert asyncio.run(run())["error"] == "timeout"
    assert rows[-1]["elapsed_ms"] == 400 and rows[-1]["outcome"] == "failed"
    assert "PRIVATE" not in json.dumps(rows)


def test_openai_usage_and_end_signal():
    from app.providers.openai_realtime import OpenAIProvider
    class Socket:
        def __aiter__(self):
            async def stream():
                yield json.dumps({"type": "input_audio_buffer.speech_stopped"})
                yield json.dumps({"type": "response.done", "response": {"id": "r1", "output": [],
                    "usage": {"input_tokens": 12, "output_tokens": 4, "total_tokens": 16,
                              "input_token_details": {"audio_tokens": 8, "cached_tokens": 3},
                              "output_token_details": {"audio_tokens": 4}}}})
            return stream()
    provider = OpenAIProvider("marin", "synthetic")
    provider._ws = Socket()
    async def run():
        return [e async for e in provider.events()]
    events = asyncio.run(run())
    assert [e.kind for e in events] == ["speech_stopped", "usage", "turn_complete"]
    assert events[1].data["input_audio_tokens"] == 8 and events[1].data["cached_tokens"] == 3


def test_gemini_usage_without_content_is_not_dropped():
    from app.providers.gemini import GeminiProvider
    provider = GeminiProvider("Aoede", "synthetic")
    class Session:
        calls = 0
        def receive(self):
            self.calls += 1
            async def stream():
                if self.calls == 1:
                    yield SimpleNamespace(usage_metadata=SimpleNamespace(total_token_count=123))
            return stream()
    provider._session = Session()
    async def no_resume():
        return False
    provider._reconnect = no_resume
    async def run():
        return [e async for e in provider.events()]
    events = asyncio.run(run())
    assert events[0].data == {"source": "gemini_report", "total_token_count": 123}


def test_local_vad_reports_decision_without_changing_wire(observed):
    from app.providers.gemini import GeminiProvider
    obs, _, _ = observed
    provider = GeminiProvider("Aoede", "synthetic")
    sent = []
    async def send(**payload):
        sent.append(payload)
    provider._session = SimpleNamespace(send_realtime_input=send)
    provider._vad_gate = SimpleNamespace(feed=lambda _: [("end", b"")])
    async def run():
        token = metrics.active.set(obs)
        try:
            await provider.send_audio(b"\x00\x00")
        finally:
            metrics.active.reset(token)
    asyncio.run(run())
    assert list(sent[0]) == ["activity_end"]
    assert obs.pending_end[1] == "gemini_local_vad_decision"
