"""Read turn logs without printing transcripts. Usage units are tokens, not money."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path


def percentile(values, fraction):
    values = sorted(values)
    if not values:
        return None
    pos = (len(values)-1)*fraction
    lo = int(pos)
    return round(values[lo] + (values[min(lo+1, len(values)-1)]-values[lo])*(pos-lo), 3)


def summarize(rows):
    groups = {}
    seen_usage = set()
    seen_measurements = set()
    for row in rows:
        event = row.get("event", "")
        if not event.startswith("metric_"):
            continue
        key = (row.get("provider", "unknown"), row.get("model", ""), row.get("vad", ""))
        group = groups.setdefault(key, {"samples": defaultdict(list), "usage": defaultdict(int),
                                        "gemini_latest_snapshot": None, "turns": 0,
                                        "missing_end_signal": 0})
        sid, tid = row.get("session_id"), row.get("turn_id")
        if event == "metric_turn_end":
            group["turns"] += 1
        fields = ("end_signal_to_audio_ms", "reply_start_to_audio_ms") if event == "metric_first_audio" else ()
        if event == "metric_first_audio" and row.get("end_signal_source") == "unavailable":
            group["missing_end_signal"] += 1
        values = {field: row.get(field) for field in fields}
        if event == "metric_client" and row.get("name") in {"playback_schedule_ms", "interruption_clear_ms"}:
            values[row["name"]] = row.get("ms")
        if event == "metric_tool":
            values["tool_wait_ms"] = row.get("elapsed_ms")
            values["tool_wait_ms:" + str(row.get("name", "unknown"))] = row.get("elapsed_ms")
        for name, value in values.items():
            identity = (key, sid, tid, event, name)
            if event != "metric_tool" and identity in seen_measurements:
                continue
            if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                group["samples"][name].append(value)
                seen_measurements.add(identity)
        if event == "metric_usage" and row.get("aggregation") == "response":
            rid = row.get("response_id")
            identity = (key, sid, rid)
            if not rid or identity in seen_usage:
                continue
            seen_usage.add(identity)
            for field in ("input_tokens", "output_tokens", "total_tokens", "cached_tokens",
                          "input_audio_tokens", "output_audio_tokens"):
                value = row.get(field)
                if type(value) is int and value >= 0:
                    group["usage"][field] += value
        elif event == "metric_usage" and row.get("aggregation") == "snapshot":
            group["gemini_latest_snapshot"] = {
                k: v for k, v in row.items() if k.endswith("_token_count") and type(v) is int and v >= 0}
    return [{"provider": key[0], "model": key[1], "vad": key[2],
             "turns": g["turns"], "missing_end_signal": g["missing_end_signal"],
             "latency_ms": {n: {"n": len(v), "p50": percentile(v, .5), "p95": percentile(v, .95)}
                            for n, v in g["samples"].items()},
             "response_usage_tokens": dict(g["usage"]),
             "gemini_latest_snapshot_not_total": g["gemini_latest_snapshot"]}
            for key, g in groups.items()]


def read_rows(paths):
    for path in paths:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except (ValueError, TypeError):
                    continue  # a crash may leave one partial line
                if isinstance(row, dict):
                    yield row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", type=Path, nargs="+")
    args = parser.parse_args()
    paths = sorted({p for item in args.logs for p in (item.glob("*.jsonl") if item.is_dir() else [item])})
    print(json.dumps(summarize(read_rows(paths)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
