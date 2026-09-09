"""Bounded, content-free observations. All server durations use one clock.

A turn is a provider reply, including its tool follow-ups. Playback values
are client observations, not acoustic measurements or cross-clock latency.
"""
from __future__ import annotations

from collections import OrderedDict
from contextvars import ContextVar
import math
import time
import uuid

from app import turnlog

active: ContextVar["SessionMetrics | None"] = ContextVar("voice_metrics", default=None)


def counts(value, keys):
    """Copy only finite nonnegative integer counters, never arbitrary payloads."""
    return {key: value[key] for key in keys
            if isinstance(value, dict) and type(value.get(key)) is int
            and 0 <= value[key] <= 10**12}


class SessionMetrics:
    def __init__(self, provider, model="", vad="", *, clock=time.monotonic):
        self.labels = {"provider": provider, "model": model, "vad": vad}
        self.clock = clock
        self.current = None
        self.pending_end = None
        self.recent = OrderedDict()
        self.responses = OrderedDict()

    def record(self, event, **fields):
        turnlog.record(event, **self.labels, **fields)

    def speech_end(self, source):
        self.pending_end = (self.clock(), source)

    def ensure(self):
        if self.current is None:
            now = self.clock()
            self.current = {"id": uuid.uuid4().hex, "start": now,
                            "end_signal": self.pending_end, "audio": None}
            self.pending_end = None
            self.recent[self.current["id"]] = set()
            while len(self.recent) > 32:
                self.recent.popitem(last=False)
            self.record("metric_turn_start", turn_id=self.current["id"])
        return self.current["id"]

    def audio(self):
        turn_id = self.ensure()
        if self.current["audio"] is not None:
            return None
        now = self.clock()
        self.current["audio"] = now
        signal = self.current["end_signal"]
        self.record("metric_first_audio", turn_id=turn_id,
                    reply_start_to_audio_ms=round((now-self.current["start"])*1000, 3),
                    end_signal_to_audio_ms=round((now-signal[0])*1000, 3) if signal else None,
                    end_signal_source=signal[1] if signal else "unavailable")
        return turn_id

    def finish(self, reason="complete"):
        if self.current is None:
            return
        self.record("metric_turn_end", turn_id=self.current["id"], reason=reason,
                    elapsed_ms=round((self.clock()-self.current["start"])*1000, 3),
                    audio_observed=self.current["audio"] is not None)
        self.current = None

    def usage(self, payload):
        source = payload.get("source")
        if source == "openai_response":
            rid = payload.get("response_id")
            if not isinstance(rid, str) or not rid or len(rid) > 160:
                return  # no response identity: cannot safely aggregate
            if rid in self.responses:
                return
            self.responses[rid] = None
            while len(self.responses) > 2048:
                self.responses.popitem(last=False)
            fields = counts(payload, ("input_tokens", "output_tokens", "total_tokens",
                                      "cached_tokens", "input_audio_tokens", "output_audio_tokens"))
            self.record("metric_usage", turn_id=self.ensure(), source=source,
                        response_id=rid, aggregation="response", **fields)
        elif source == "gemini_report":
            # Live reports have no response identity. Preserve observations;
            # never sum them or infer billing deltas from context-window size.
            fields = counts(payload, ("prompt_token_count", "response_token_count",
                                      "total_token_count", "cached_content_token_count",
                                      "tool_use_prompt_token_count", "thoughts_token_count"))
            self.record("metric_usage", source=source, aggregation="snapshot",
                        observed_turn_id=self.current["id"] if self.current else None, **fields)

    def client(self, event):
        turn_id = event.get("turn_id")
        name = event.get("name")
        if not isinstance(turn_id, str) or turn_id not in self.recent:
            return
        if name not in {"playback_schedule_ms", "interruption_clear_ms"}:
            return
        value = event.get("ms")
        if type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= 120000:
            return
        if name in self.recent[turn_id]:
            return
        self.recent[turn_id].add(name)
        self.record("metric_client", turn_id=turn_id, name=name, ms=round(value, 3),
                    measurement="client_reported")
