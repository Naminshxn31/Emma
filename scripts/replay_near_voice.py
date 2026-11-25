#!/usr/bin/env python3
"""VOICE_NEAR_DOOR against what really came through the listening door. Numbers only: the
transcripts are read to label a voice and never printed or stored.

    python scripts/replay_near_voice.py --logs <folder of jsonl> [--walks <file>]

The robot's logs are read-only to pull, and hold customers' words, so a scratch folder,
never the repo:

    adb exec-out "su 0 cat /data/local/emma-server/code/data/logs/2026-11-05.jsonl" > <folder>/2026-11-05.jsonl
    adb exec-out "su 0 sh -c 'cat /data/local/emma-server/code/data/robot/walk-frames/*/run.json'" > <walks file>

**Before VOICE_NEAR_DOOR=shadow** the lidar is known only from `lidar_people` rows: one
every 10 s at most, only when somebody is found. A voice counts as somebody-near when a row is
<= 10.5 s before it or <= 3 s after, so this approximates what the door would read.
**After shadow** the rows carry `near_state` / `near_would`, which is what the door read,
and the second table is the one to believe. docs/near-voice-bench.md says what to do with it.

Labels, from the offline router's gates (app/offline/router.py), and noisy: "for her" =
it asks something, or she called a tool on it; "not for her" = two people talking to each
other, filler, or nothing transcribed; the rest counted in neither column.
"""
from __future__ import annotations

import argparse
import bisect
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: The reasons today's waivers let a voice in with no face. The door decides only these.
WAIVERS = ("near voice", "reply to her", "in conversation")
#: How long after a walk the door has no opinion (VOICE_NEAR_WARM_S's default).
WARM_S = 180


def _seconds(clock: str) -> float:
    h, m, s = str(clock).split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def label(text: str | None) -> str:
    from app.offline import router

    if text is None:
        return "nothing"
    norm = router._norm(text)
    if not norm:
        return "nothing"
    if router._human_talk(norm):
        return "human"
    words = router.content_words(norm)
    if words and router.asks(norm):
        return "asks"
    return "words" if words else "filler"


def for_her(row: dict) -> bool:
    return row["label"] == "asks" or row["tool"]


def not_for_her(row: dict) -> bool:
    return not row["tool"] and row["label"] in ("human", "filler", "nothing")


def read_day(path: Path) -> tuple[list[dict], list[tuple[float, float]]]:
    """(the door's rows joined to what was heard next, the day's lidar rows)."""
    sessions: dict = defaultdict(list)
    lidar = []
    for line in path.open(encoding="utf-8", errors="replace"):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("event") == "lidar_people":
            lidar.append((_seconds(row["t"]), float(row["nearest_m"])))
        sessions[row.get("session_id")].append(row)
    out = []
    for seq in sessions.values():
        # the 5 s report windows hold the loudest 20 ms frame: the door re-asks on every frame
        reports = [(_seconds(r["t"]), r.get("peak") or 0.0) for r in seq if r.get("event") == "mic_report"]
        for i, row in enumerate(seq):
            if row.get("event") not in ("focus_open", "focus_deny"):
                continue
            t = _seconds(row["t"])
            heard = None
            tool = False
            if row["event"] == "focus_open":
                for later in seq[i + 1:]:
                    kind = later.get("event")
                    if kind == "focus_open":
                        break
                    if kind == "heard" and heard is None:
                        heard = later.get("text") or ""
                    if kind == "tool" and heard is not None:
                        tool = True
            window = [p for at, p in reports if t <= at <= t + 7]
            out.append({"t": t, "event": row["event"], "reason": row.get("reason"), "rms": row.get("rms"),
                        "peak": max(window) if window else None, "label": label(heard), "tool": tool,
                        "near_state": row.get("near_state"), "near_would": row.get("near_would")})
    return out, lidar


def read_walks(path: Path | None) -> dict:
    walks: dict = defaultdict(list)
    if path is None:
        return walks
    text = path.read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r'"started": "(\d{4}-\d\d-\d\d)T(\d\d:\d\d:\d\d)",\s*"ended": "\d{4}-\d\d-\d\dT(\d\d:\d\d:\d\d)"', text):
        walks[m.group(1)].append((_seconds(m.group(2)), _seconds(m.group(3))))
    return walks


def table(rows: list[dict], rules: list[tuple[str, object]]) -> None:
    total_for = sum(for_her(r) for r in rows)
    total_not = sum(not_for_her(r) for r in rows)
    print("  voices %d · for her %d · not for her %d · neither %d" % (
        len(rows), total_for, total_not, len(rows) - total_for - total_not))
    for name, keep in rules:
        kept_for = sum(for_her(r) and keep(r) for r in rows)
        kept_not = sum(not_for_her(r) and keep(r) for r in rows)
        print("  %-30s kept %3d  for her kept %3d/%d (%3.0f%%)  not for her refused %3d/%d (%3.0f%%)" % (
            name, sum(bool(keep(r)) for r in rows), kept_for, total_for, 100 * kept_for / max(total_for, 1),
            total_not - kept_not, total_not, 100 * (total_not - kept_not) / max(total_not, 1)))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--logs", default=str(ROOT / "data" / "logs"), help="folder of YYYY-MM-DD.jsonl")
    ap.add_argument("--walks", default=None, help="walk-frames run.json files, joined (optional)")
    args = ap.parse_args()
    from app.offline import router

    router.prepare()
    walks = read_walks(Path(args.walks) if args.walks else None)
    replay: list[dict] = []
    moved_in = kept_out = 0
    admitted = Counter()
    shadow: list[dict] = []
    for path in sorted(Path(args.logs).glob("*.jsonl")):
        day = path.stem
        rows, lidar = read_day(path)
        shadow.extend(r for r in rows if r["near_state"])
        if not lidar:
            continue
        at = [x[0] for x in lidar]
        for row in rows:
            if row["reason"] not in WAIVERS and row["reason"] != "no face in front":
                continue
            t = row["t"]
            if any(a - 15 <= t <= b + WARM_S for a, b in walks[day]):
                moved_in += 1
                continue
            i = bisect.bisect_right(at, t)
            before = at[i - 1] if i else -1e12
            after = at[i] if i < len(at) else 1e12
            row["m"] = lidar[i - 1][1] if t - before <= 10.5 else (lidar[i][1] if after - t <= 3 else None)
            if row["event"] == "focus_deny":
                if row["m"] is not None and row["m"] <= 1.5:
                    admitted[day] += 1
                else:
                    kept_out += 1
            else:
                row["day"] = day
                replay.append(row)
    if replay:
        print("Lidar rows against voices the waivers let in with no face (standing, past the warm-up):")
        near = lambda r, reach: r["m"] is not None and r["m"] <= reach
        table(replay, [("today", lambda r: True)]
              + [("level >= %.2f alone" % x, lambda r, x=x: (r["peak"] or 0) >= x) for x in (0.08, 0.10, 0.12, 0.20)]
              + [("lidar <= %.1f m alone" % m, lambda r, m=m: near(r, m)) for m in (1.0, 1.5, 2.5)]
              + [("lidar <= 1.5 m or >= 0.20", lambda r: near(r, 1.5) or (r["peak"] or 0) >= 0.20)])
        print("  by day, lidar <= 1.5 m or >= 0.20:")
        for day in sorted({r["day"] for r in replay}):
            mine = [r for r in replay if r["day"] == day]
            keep = [r for r in mine if near(r, 1.5) or (r["peak"] or 0) >= 0.20]
            print("    %s voices %d kept %d · for her %d/%d · not for her %d/%d" % (
                day, len(mine), len(keep), sum(for_her(r) for r in keep), sum(for_her(r) for r in mine),
                sum(not_for_her(r) for r in keep), sum(not_for_her(r) for r in mine)))
        print("  refused 'no face' today with somebody within 1.5 m: %s · still refused %d · in a walk or warming %d" % (
            dict(admitted), kept_out, moved_in))
    else:
        print("No lidar rows in %s" % args.logs)
    if shadow:
        print("\nVOICE_NEAR_DOOR rows (what the door itself read):")
        print("  near_state, opens: %s" % dict(Counter(r["near_state"] for r in shadow if r["event"] == "focus_open")))
        print("  near_state, refusals: %s" % dict(Counter(r["near_state"] for r in shadow if r["event"] == "focus_deny")))
        faces = [r for r in shadow if r["event"] == "focus_open" and r["reason"] == "ok"]
        print("  voices with a face in front %d: lidar near %d, nobody %d, no opinion %d  <- its coverage of real people" % (
            len(faces), sum(r["near_state"] == "near" for r in faces), sum(r["near_state"] == "nobody" for r in faces),
            sum(r["near_state"] == "unknown" for r in faces)))
        opens = [r for r in shadow if r["event"] == "focus_open" and r["near_would"]]
        if opens:
            table(opens, [("today", lambda r: True), ("the door", lambda r: r["near_would"] != "refuse")])
        print("  refused today, the door would open: %d" % sum(
            r["event"] == "focus_deny" and r["near_would"] == "open" for r in shadow))


if __name__ == "__main__":
    main()
