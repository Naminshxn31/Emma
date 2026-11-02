"""Would FOCUS_GOODBYE have said goodbye to someone still there? (2 Nov 2026)

Owner: "ให้คนแรกคุยจนเดินออกไปก่อน แล้วจึงเริ่มบทสนทนาใหม่กับคนถัดไป". The
goodbye (session._left_mid_conversation) fires when an engaged face has been
gone FOCUS_GOODBYE_AFTER_S with no voice since and the guest was heard within
30 s before. A `focus_gap` row is an engaged face that CAME BACK after gap_s:
a goodbye inside that gap was said to a guest still in front. Counts only --
no text from the logs is printed.

Approximate on purpose: it does not model the wait for her own answer to end
(may_say), and the face that came back may be someone else's.

    python scripts/replay_goodbye.py [glob]   (default .tmp/robot-logs/*/*.jsonl,
                                               a day's "-full" copy wins)
"""
import bisect, datetime as dt, glob, json, sys
files = sorted(set(glob.glob(sys.argv[1] if len(sys.argv) > 1 else ".tmp/robot-logs/*/*.jsonl")))
# prefer -full copies over partial ones of the same day
by_day = {}
for p in files:
    day = p.replace("\\", "/").split("/")[-1]
    if day not in by_day or "-full" in p:
        by_day[day] = p
rows = []
for day, p in sorted(by_day.items()):
    for line in open(p, encoding="utf-8"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        t = r.get("t")
        if not isinstance(t, str):
            continue
        try:
            r["_t"] = dt.datetime.fromisoformat(day[:10] + "T" + t if len(t) <= 12 else t).timestamp()
        except ValueError:
            continue
        rows.append(r)
rows.sort(key=lambda r: r["_t"])
voice = [r["_t"] for r in rows if r.get("event") in ("focus_open", "focus_deny")]
heard = [r["_t"] for r in rows if r.get("event") == "heard"]
starts = [(r["_t"], r.get("session_id")) for r in rows if r.get("event") == "session_start"]
ends = {r.get("session_id"): r["_t"] for r in rows if r.get("event") == "session_end"}
said = [r["_t"] for r in rows if r.get("event") == "said"]

def any_in(seq, a, b):
    i = bisect.bisect_right(seq, a)
    return i < len(seq) and seq[i] <= b

def in_session(t):
    for s, sid in starts:
        if s <= t <= ends.get(sid, s + 4 * 3600):
            return True
    return False

gaps = [(r["_t"], float(r["gap_s"])) for r in rows if r.get("event") == "focus_gap" and isinstance(r.get("gap_s"), (int, float))]
print("days", len(by_day), "focus_gap rows", len(gaps), "sessions", len(starts))
import collections
print("gap_s histogram:", sorted(collections.Counter(min(60, int(g // 2) * 2) for _, g in gaps).items()))
for after in (8, 12, 20, 30):
    n = sum(1 for _, g in gaps if g >= after)
    cand = 0
    for end, g in gaps:
        if g < after:
            continue
        left = end - g
        if not in_session(left):
            continue
        if any_in(voice, left, left + after) or any_in(heard, left, left + after):
            continue                  # a voice since the face went: no goodbye
        if not (any_in(heard, left - 30, left) or any_in(voice, left - 30, left)):
            continue                  # not heard just before: not a conversation walked out of
        cand += 1
    print("FOCUS_GOODBYE_AFTER_S=%2d: gaps >= that %3d, would-be goodbye to a guest still there %3d" % (after, n, cand))
