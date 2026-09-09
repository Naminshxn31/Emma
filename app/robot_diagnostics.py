"""Bounded rehearsal observations, separate from visitor transcripts."""
from collections import OrderedDict, deque
import math
import re


EVENTS = {"session_start", "session_end", "voice_ready", "provider_error", "wake",
          "ears_open", "mic_report", "metric_turn_start", "metric_turn_end",
          "metric_first_audio", "metric_client", "metric_usage", "metric_tool"}
NUMBERS = {"ms", "elapsed_ms", "duration_ms", "reply_start_to_audio_ms",
           "end_signal_to_audio_ms", "avg", "peak", "frames", "quiet_s",
           "input_tokens", "output_tokens", "total_tokens", "cached_tokens",
           "input_audio_tokens", "output_audio_tokens", "prompt_token_count",
           "response_token_count", "total_token_count", "cached_content_token_count",
           "tool_use_prompt_token_count", "thoughts_token_count"}
WORDS = {"playback_schedule_ms", "interruption_clear_ms", "gemini", "openai",
         "go_to_place", "stop_moving", "return_to_base", "get_robot_status",
         "set_simulated_device", "get_simulated_home",
         "complete", "interrupted", "closed", "error", "permission", "quota",
         "auth", "network", "provider", "snapshot", "response", "client_reported",
         "gemini_report", "openai_response", "timeout", "ok", "failed"}


def failure_kind(exc) -> str:
    # Only this category is recorded, never exception URLs, tokens or messages.
    value = str(exc).lower()
    if "winerror 5" in value or "access is denied" in value or "permission" in value:
        return "permission"
    if any(word in value for word in ("quota", "429", "resource_exhausted")):
        return "quota"
    if any(word in value for word in ("api key", "api_key", "401", "403", "unauthorized")):
        return "auth"
    if any(word in value for word in ("connect", "timeout", "network")):
        return "network"
    return "provider"


class RobotDiagnostics:
    def __init__(self, clock):
        self.clock = clock
        self.started = clock()
        self.rows = deque(maxlen=800)
        self.seq = 0
        self.session_id = None
        self.provider = "idle"
        self.error = "none"
        self.client_times = OrderedDict()

    def append(self, event, **fields):
        self.seq += 1
        self.rows.append({"seq": self.seq, "t": round(self.clock()-self.started, 3),
                          "event": event, **fields})

    def record(self, event, fields, session_id=None, command_id=None):
        if event not in EVENTS:
            return
        if event == "session_start":
            self.session_id = session_id
            self.provider, self.error = "connecting", "none"
        elif session_id and self.session_id and session_id != self.session_id:
            return
        if event == "voice_ready":
            self.provider = "connected"
        elif event == "provider_error":
            self.provider = "error"
            category = fields.get("category")
            self.error = category if isinstance(category, str) and category in {"permission", "quota", "auth", "network", "provider"} else "provider"
        elif event == "session_end" and self.provider != "error":
            self.provider = "idle"
        clean = {}
        for key, value in fields.items():
            if key in NUMBERS and type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 10**12:
                clean[key] = value
            elif key in {"ok", "audio_observed"} and type(value) is bool:
                clean[key] = value
            elif key in {"turn_id", "observed_turn_id"} and isinstance(value, str) and re.fullmatch(r"[a-f0-9]{32}", value):
                clean[key] = value
            elif key in {"name", "reason", "provider", "source", "aggregation", "measurement", "category", "outcome"} and isinstance(value, str) and value in WORDS:
                clean[key] = value
        self.append(event, session_id=session_id, command_id=command_id, **clean)

    def client(self, fields):
        if fields["session_id"] and fields["session_id"] != self.session_id:
            return False
        client_id = fields["client_id"]
        now = self.clock()
        previous = self.client_times.get(client_id)
        if previous is not None and now-previous < .2:
            return True
        self.client_times[client_id] = now
        self.client_times.move_to_end(client_id)
        while len(self.client_times) > 32:
            self.client_times.popitem(last=False)
        self.append("browser_state", **fields)
        return True

    def summary(self):
        samples = [r["end_signal_to_audio_ms"] for r in self.rows
                   if r["event"] == "metric_first_audio" and "end_signal_to_audio_ms" in r]
        samples.sort()
        def percentile(fraction):
            return samples[max(0, math.ceil(len(samples)*fraction)-1)] if samples else None
        return {"provider": self.provider, "error": self.error, "session_id": self.session_id,
                "retained": len(self.rows), "sequence": self.seq, "limit": 800,
                "audio_samples": len(samples), "end_to_audio_p50_ms": percentile(.5),
                "end_to_audio_p95_ms": percentile(.95),
                "measurement": "provider audio received; not acoustic output"}

    def export(self):
        return {"summary": self.summary(), "events": list(self.rows)}
