"""Numbers for one card-1 / ceremony-opening run, read back from what the robot already logs.

Owner, 23 Nov 2026: "อยากให้เดินเยอะๆ แล้ววัดได้แม่น จะได้ไม่มีปัญหาในวันจริง" -- walk it many times and
measure it precisely. Tonight's runs are the BASELINE; a bench harness (scripts/card1_bench.py) presses the card
again and again and calls `extract_runs` on what each press left behind.

    python scripts/card1_metrics.py --turnlog .tmp/walk-frames/robot_turnlog_20261123.jsonl \
        --board "<scratch>/boardlog*.txt" --walkframes ".tmp/walk-frames/walk-frames/20261123-2*" \
        --state "<scratch>/state*.jsonl" --state "<scratch>/scene1*_watch.jsonl" \
        --out-dir .tmp/card1-bench --name baseline_20261123

    from scripts.card1_metrics import extract_runs
    runs = extract_runs(turnlog, boardlogs=[...], walkframes=[...], statelogs=[...], since=press_epoch)

Four sources, four clocks -- the part that is easy to get wrong:

* turnlog (`data/logs/*.jsonl` on the robot): `"t"` is `time.strftime("%H:%M:%S")` = Thai wall clock TRUNCATED to
  the second. Every turnlog-only interval is therefore +-1 s. The date comes from the file name (or --date).
  `debut_start.wall_ms` is the exception: the server's clock in ms.
* board pose log (`epoch|quality|{pose}` from busybox wget on the robot, ~3-7 Hz): `date +%s.%N` on the robot =
  the same clock as the server, in UTC seconds.
* walk-frames run folders (`data/robot/walk-frames/<run>/frames.jsonl`): Thai ISO time with ms, ~1.7 Hz.
* staff-page state polls (`{"t": PC epoch, "s": snapshot}`): **`t` is the PC's clock, 4-5.7 s BEHIND the robot**
  tonight (and not constant). The snapshot's own `server_time` is the robot's clock -- use that, never `t`.
  A run seen only here (the turnlog pulled before it) is still a run: press, walk, box, pageant, end.

Every moment is kept with its resolution and source; a metric takes the finest source available and says so
(`res`, `src` on each run). The board pose -- not a log line -- is the only source for when the wheels really
turned: `event_tour late_walk_out` (`hit_to_walker_s`) is the walker's start, which tonight came ~0.8-1.0 s
before the pose first moved.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import html
import json
import math
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THAI = _dt.timezone(_dt.timedelta(hours=7))

#: the counter spot of card 1 tonight (event file `beside_host`, staff page `tour.counter.pose`): x, y, yaw deg
COUNTER_POSE = (-8.62, -4.20, 0.0)
COUNTER_SPOT = "beside_host"
FORWARD_SPOT = "forward"

#: +- (half-width, s) of each source's moments, used to pick the finest and reported per metric. A turnlog row
#: "22:26:03" happened in [03.0, 04.0): it is kept as 03.5 +- 0.5 (keeping 03.0 biased every turnlog-vs-other
#: interval by half a second). A poll or pose sample: the midpoint between the sample that first showed it and
#: the one before, +- half that gap.
RES = {"turnlog": 0.5, "wall_ms": 0.001, "state": 0.25, "board": 0.15, "frames": 0.3}

#: a board pose sample "moved" from the one before by at least this much (the pose is bit-identical when still)
MOVE_M = 0.003
MOVE_DEG = 0.3
#: a pose jump: this far in one sample, faster than the robot drives (walk-out ~0.13 m/s, legs <= 0.5 m/s)
#: The board pose is noisy (3-6 cm zigzag while turning, two pollers interleaved = 0.1 s gaps): 22:02:53 tonight
#: gave 0.105 m in 0.13 s mid-turn, then nothing. A jump is >= 0.12 m in one sample, >= 3x the speed around it,
#: and the pose STAYS there (does not snap back on the next sample). 22:26:14.995: 0.208 m in 0.29 s, neighbours
#: 0.14 m/s, stayed -- the walker's own guard saw it as "0.19 m / 1° in 0.1 s".
JUMP_MIN_M = 0.12
JUMP_MIN_SPEED = 0.6
JUMP_NEIGHBOUR_X = 3.0
#: a stop inside a leg worth naming (someone in the way, the board holding)
STOP_NOTE_S = 2.0
#: how far a state-poll press and a turnlog press may sit apart and still be the same press
SAME_PRESS_S = 3.0
#: a run with no end row in any source is cut here (the log ended / was pulled mid-run)
MAX_RUN_S = 300.0

#: default gates for the bench harness: (metric, low, high) -- None = no bound. Tonight's spread plus a margin;
#: change them here, not in the harness.
DEFAULT_LIMITS = [
    ("walkout_box_m", 0.98, 1.03),
    ("walkout_lateral_m", -0.10, 0.10),
    ("hit_to_wheels_s", None, 2.8),
    ("hit_to_walker_s", None, 1.8),
    ("arrived_to_out_s", None, 3.0),
    ("pageant_to_line_s", 3.5, 6.5),
    ("rest_wait_s", None, 5.0),
    ("arrival_err_m", None, 0.15),
    ("pose_jumps", None, 0),
]


# ----------------------------------------------------------------------------------------------- small helpers
def _expand(items) -> list[Path]:
    """Paths or globs (str/Path, one or many) -> existing paths, sorted, no duplicates."""
    if items is None:
        return []
    if isinstance(items, (str, Path)):
        items = [items]
    out: list[Path] = []
    for it in items:
        s = str(it)
        hits = glob.glob(s) if any(c in s for c in "*?[") else [s]
        for h in sorted(hits):
            p = Path(h)
            if p.exists() and p not in out:
                out.append(p)
    return out


def _th(ts: float | None, ms: bool = True) -> str | None:
    if ts is None:
        return None
    s = _dt.datetime.fromtimestamp(ts, THAI).strftime("%H:%M:%S.%f")
    return s[:-5] if ms else s[:8]


def _wrap_deg(d: float) -> float:
    return (d + 180.0) % 360.0 - 180.0


def _r(v, n=2):
    return None if v is None else round(v, n)


def _date_from_name(path) -> _dt.date | None:
    m = re.search(r"(20\d{2})(\d{2})(\d{2})", Path(path).name)
    if not m:
        return None
    try:
        return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _hms_epoch(day: _dt.date, hms: str) -> float:
    h, m, s = (int(x) for x in hms.split(":"))
    return _dt.datetime(day.year, day.month, day.day, h, m, s, tzinfo=THAI).timestamp()


# --------------------------------------------------------------------------------------------------- loaders
def load_turnlog(path, date: _dt.date | str | None = None) -> list[dict]:
    """Rows in file order, each with `_ts` (Thai wall clock of its second, epoch) and `_i` (its index).
    Malformed lines are skipped. A time that falls back by more than 12 h = the next day (a run past midnight)."""
    p = Path(path)
    if isinstance(date, str):
        date = _dt.date.fromisoformat(date)
    day = date or _date_from_name(p)
    if day is None:
        raise ValueError("no date for %s: name it *YYYYMMDD* or pass date=" % p)
    rows, prev = [], None
    with open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            t = r.get("t")
            if not isinstance(r, dict) or not isinstance(t, str) or not re.fullmatch(r"\d{1,2}:\d{2}:\d{2}", t):
                continue
            ts = _hms_epoch(day, t)
            if prev is not None and ts < prev - 12 * 3600:
                day = day + _dt.timedelta(days=1)
                ts = _hms_epoch(day, t)
            prev = ts
            r["_ts"] = ts
            r["_i"] = len(rows)
            rows.append(r)
    return rows


def load_boardlogs(paths) -> list[tuple]:
    """`epoch|quality|{pose json}` lines from any number of files (pollers may overlap) -> sorted, de-duplicated
    samples (ts, q, x, y, yaw_rad). A line with no pose (wget timed out) is dropped; no quality -> q None."""
    seen, out = set(), []
    for p in _expand(paths):
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.strip().split("|", 2)
                if len(parts) != 3:
                    continue
                try:
                    ts = float(parts[0])
                    pose = json.loads(parts[2])
                    x, y, yaw = float(pose["x"]), float(pose["y"]), float(pose["yaw"])
                except (ValueError, KeyError, TypeError):
                    continue
                try:
                    q = int(float(parts[1]))
                except ValueError:
                    q = None
                key = round(ts, 3)
                if key in seen:
                    continue
                seen.add(key)
                out.append((ts, q, x, y, yaw))
    out.sort()
    return out


def load_walkframes(paths) -> list[dict]:
    """Walk-frames run folders (run.json + frames.jsonl) -> [{name, label, start, end, end_state, end_reason,
    frames: [(ts, q, x, y, yaw, state, head)]}]. Only `event-*` folders are card walks; others are kept too
    (the caller matches by label)."""
    out = []
    for p in _expand(paths):
        if not p.is_dir():
            continue
        fr = p / "frames.jsonl"
        meta = {}
        try:
            meta = json.loads((p / "run.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        frames = []
        line_deg = None
        if fr.exists():
            with open(fr, encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                        ts = _dt.datetime.fromisoformat(r["t"]).replace(tzinfo=THAI).timestamp()
                    except (ValueError, KeyError, TypeError):
                        continue
                    if line_deg is None:
                        # board_walk: "[  0.0] dock: its line is turned +2.3° from its heading" -- the walk-out
                        # follows the dock's line, not the heading it stood at
                        mm = re.search(r"line is turned ([+-]?[\d.]+)°", str((r.get("walk") or {}).get("step") or ""))
                        if mm:
                            line_deg = float(mm.group(1))
                    pose = r.get("pose") or {}
                    if not all(k in pose for k in ("x", "y", "yaw")):
                        continue
                    walk = r.get("walk") or {}
                    frames.append((ts, r.get("q"), float(pose["x"]), float(pose["y"]), float(pose["yaw"]),
                                   walk.get("state"), r.get("head")))
        frames.sort(key=lambda f: f[0])
        label = meta.get("label") or "-".join(p.name.split("-")[2:])
        end = meta.get("end") or {}

        def _iso(s):
            try:
                return _dt.datetime.fromisoformat(s).replace(tzinfo=THAI).timestamp()
            except (TypeError, ValueError):
                return None
        start = _iso(meta.get("started"))
        if start is None:
            m = re.match(r"(\d{8})-(\d{6})", p.name)
            if m:
                start = _dt.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=THAI).timestamp()
        out.append({"name": p.name, "label": label, "start": start, "end": _iso(meta.get("ended")),
                    "end_state": end.get("state"), "end_reason": end.get("reason"), "frames": frames,
                    "line_deg": line_deg})
    out.sort(key=lambda r: r["start"] or 0)
    return out


def _pick_state(s: dict) -> dict:
    box = s.get("box") or {}
    tour = s.get("tour") or {}
    walk = s.get("walk") or {}
    run = walk.get("run") or {}
    man = s.get("manual") or {}
    perf = s.get("perform") or {}
    steps = [st.get("state") for st in tour.get("steps") or [] if isinstance(st, dict)]
    notes = [st.get("note_th") for st in tour.get("steps") or [] if isinstance(st, dict) and st.get("note_th")]
    return {"left": box.get("left"), "armed": box.get("armed"), "walked_m": box.get("walked_m"),
            "box_in": box.get("in"), "owner": tour.get("owner"), "scene": tour.get("scene"),
            "ended": tour.get("ended"), "now": tour.get("now_th"), "steps": steps, "notes": notes,
            "run_id": run.get("id"), "run_spot": run.get("spot"), "run_state": run.get("state"),
            "run_reason": run.get("reason"), "run_held": run.get("held"),
            "pageant_up": man.get("pageant_up"), "saying": man.get("saying"),
            # the Event card's `perform` (null on the ceremony page): phase "done" + said = the line has started
            "perf_phase": perf.get("phase"), "perf_said": perf.get("said")}


_T_HEAD = re.compile(r'^\{"t":\s*([\d.]+)')


def load_statelogs(paths, t_min: float | None = None, t_max: float | None = None) -> list[dict]:
    """Staff-page polls -> picked snapshots sorted by the ROBOT's clock (`server_time`). A poll without
    server_time gets the PC time plus that file's median offset (the PC ran 4-5.7 s behind tonight).
    t_min/t_max (epoch, robot clock) skip polls far outside the window WITHOUT parsing them (a poll is ~20 kB,
    tonight's files were 400 MB): the PC `t` at the line's head is compared with a 120 s margin."""
    snaps = []
    for p in _expand(paths):
        rows, offs = [], []
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                if t_min is not None or t_max is not None:
                    mm = _T_HEAD.match(line)
                    if mm:
                        tt = float(mm.group(1))
                        if (t_min is not None and tt < t_min - 120) or (t_max is not None and tt > t_max + 120):
                            continue
                try:
                    r = json.loads(line)
                    s = r["s"]
                    t = float(r["t"])
                except (ValueError, KeyError, TypeError):
                    continue
                if not isinstance(s, dict):
                    continue
                st = s.get("server_time")
                if isinstance(st, (int, float)):
                    offs.append(st - t)
                rows.append((t, st, s))
        off = statistics.median(offs) if offs else 0.0
        for t, st, s in rows:
            d = _pick_state(s)
            d["ts"] = float(st) if isinstance(st, (int, float)) else t + off
            d["pc"] = t
            d["clock"] = "server" if isinstance(st, (int, float)) else "pc+offset"
            snaps.append(d)
    snaps.sort(key=lambda d: d["ts"])
    return snaps


# ------------------------------------------------------------------------------------------- pose analysis
def _window(samples, t0, t1):
    return [s for s in samples if t0 <= s[0] <= t1]


def motion(samples, t0: float, t1: float, pad_before: float = 1.0, pad_after: float = 2.0,
           stop_before: float | None = None) -> dict | None:
    """What the wheels did between t0 and t1 (turnlog seconds: padded, because a row's second is truncated).
    samples = [(ts, q, x, y, yaw_rad)]. None if there is no pose in the window."""
    hi = t1 + pad_after
    if stop_before is not None:
        hi = min(hi, stop_before)
    seg = _window(samples, t0 - pad_before, hi)
    if len(seg) < 2:
        return None
    moving = [False]
    for a, b in zip(seg, seg[1:]):
        d = math.hypot(b[2] - a[2], b[3] - a[3])
        dy = abs(_wrap_deg(math.degrees(b[4] - a[4])))
        moving.append(d >= MOVE_M or dy >= MOVE_DEG)
    idx = [i for i, m in enumerate(moving) if m]
    qs = [s[1] for s in seg if s[1] is not None]
    out = {"n": len(seg), "q_min": min(qs) if qs else None, "q_max": max(qs) if qs else None,
           "start_pose": seg[0][2:5], "end_pose": seg[-1][2:5], "moved": bool(idx)}
    if not idx:
        return out
    i0, i1 = idx[0], idx[-1]
    start = seg[i0 - 1]
    end = seg[i1]
    # the first still sample after the last motion is where it stopped; the pose there is the arrival.
    # The wheels started somewhere between the last still sample and the first moved one: the midpoint, +- half
    # the gap. Same for the stop (between the last two samples that still differ).
    after = seg[i1 + 1] if i1 + 1 < len(seg) else None
    out.update(first_move=seg[i0][0], last_still=seg[i0 - 1][0], last_move=end[0],
               first_still_after=after[0] if after else None,
               start_at=(seg[i0 - 1][0] + seg[i0][0]) / 2, start_res=(seg[i0][0] - seg[i0 - 1][0]) / 2,
               stop_at=(seg[i1 - 1][0] + seg[i1][0]) / 2, stop_res=(seg[i1][0] - seg[i1 - 1][0]) / 2,
               start_pose=start[2:5], end_pose=(after or end)[2:5])
    out["moving_s"] = sum(seg[i][0] - seg[i - 1][0] for i in idx)
    # the longest stand-still between the first and the last motion (someone in the way, the board holding)
    longest, at, run_from = 0.0, None, None
    for i in range(i0, i1 + 1):
        if not moving[i]:
            if run_from is None:
                run_from = seg[i - 1][0]
        elif run_from is not None:
            gap = seg[i - 1][0] - run_from
            if gap > longest:
                longest, at = gap, run_from
            run_from = None
    out["longest_stop_s"] = longest
    out["longest_stop_at"] = at
    out["jumps"] = find_jumps(seg)
    return out


def find_jumps(seg) -> list[dict]:
    """Pose jumps in a run of samples (see JUMP_MIN_M): far, fast against its neighbours, and it stays."""
    speeds = []
    for i in range(1, len(seg)):
        dt = max(seg[i][0] - seg[i - 1][0], 1e-3)
        speeds.append(math.hypot(seg[i][2] - seg[i - 1][2], seg[i][3] - seg[i - 1][3]) / dt)
    jumps = []
    for k, v in enumerate(speeds):
        i = k + 1
        a, b = seg[i - 1], seg[i]
        d = math.hypot(b[2] - a[2], b[3] - a[3])
        if d < JUMP_MIN_M:
            continue
        near = [speeds[j] for j in range(max(0, k - 3), min(len(speeds), k + 4)) if j != k]
        base = statistics.median(near) if near else 0.0
        if v < max(JUMP_MIN_SPEED, JUMP_NEIGHBOUR_X * base):
            continue
        if i + 1 < len(seg):
            c = seg[i + 1]
            if math.hypot(c[2] - a[2], c[3] - a[3]) < math.hypot(c[2] - b[2], c[3] - b[3]):
                continue                                 # snapped back: noise, not a jump
        jumps.append({"ts": b[0], "m": round(d, 3), "in_s": round(b[0] - a[0], 2), "speed": round(v, 2),
                      "dx": b[2] - a[2], "dy": b[3] - a[3], "src": "board"})
    return jumps


def geometry(start_pose, end_pose, line_deg: float | None = None, minus=(0.0, 0.0)) -> dict:
    """Straight line, along/lateral relative to the START heading (+ = to the robot's left), direction of
    travel against that heading, heading change. `line_deg` = the line the walker follows, turned from the
    heading (+ = left, board_walk's "its line is turned +2.3°"): lateral against that line too. `minus` = a
    displacement to take out first (the sum of the pose jumps: the board moved the robot, the wheels did not)."""
    x0, y0, a0 = start_pose
    x1, y1, a1 = end_pose
    dx, dy = x1 - x0 - minus[0], y1 - y0 - minus[1]
    along = dx * math.cos(a0) + dy * math.sin(a0)
    lateral = -dx * math.sin(a0) + dy * math.cos(a0)
    dist = math.hypot(dx, dy)
    out = {"dist": dist, "along": along, "lateral": lateral,
           "dir_deg": _wrap_deg(math.degrees(math.atan2(dy, dx) - a0)) if dist > 0.02 else None,
           "yaw_change_deg": _wrap_deg(math.degrees(a1 - a0)), "lateral_line": None}
    if line_deg is not None:
        al = a0 + math.radians(line_deg)
        out["lateral_line"] = -dx * math.sin(al) + dy * math.cos(al)
    return out


def max_lateral(samples, start_pose, t0, t1) -> float | None:
    x0, y0, a0 = start_pose
    vals = [-(s[2] - x0) * math.sin(a0) + (s[3] - y0) * math.cos(a0) for s in _window(samples, t0, t1)]
    return max(vals, key=abs) if vals else None


# -------------------------------------------------------------------------------------------- run finding
def _turnlog_runs(rows: list[dict]) -> list[dict]:
    presses = [r for r in rows if r.get("event") == "event_tour" and r.get("phase") == "pressed"]
    runs = []
    for k, p in enumerate(presses):
        nxt = presses[k + 1]["_i"] if k + 1 < len(presses) else len(rows)
        end = next((r for r in rows[p["_i"] + 1:nxt]
                    if r.get("event") == "event_tour" and r.get("phase") == "end"), None)
        last = end["_i"] if end else nxt - 1
        # rows of the press itself may come just before the `pressed` row (same second): look 6 rows back
        lo = max(0, p["_i"] - 6)
        if k > 0:
            lo = max(lo, presses[k - 1]["_i"] + 1)
        runs.append({"press_row": p, "end_row": end, "rows": rows[p["_i"]:last + 1],
                     "pre": [r for r in rows[lo:p["_i"]] if p["_ts"] - r["_ts"] <= 2],
                     "tail": [r for r in rows[last + 1:min(nxt, last + 40)] if r["_ts"] - rows[last]["_ts"] <= 3]})
    return runs


def _state_runs(snaps: list[dict], counter_spot: str = COUNTER_SPOT) -> list[dict]:
    """Presses seen on the staff page: box.left turning "pending" (the card pressed, the robot in its box)."""
    runs, prev = [], None
    starts = []
    for i, s in enumerate(snaps):
        if s.get("left") == "pending" and (prev is None or prev.get("left") != "pending"):
            starts.append(i)
        prev = s
    for k, i in enumerate(starts):
        hi = starts[k + 1] if k + 1 < len(starts) else len(snaps)
        t0 = snaps[i]["ts"]
        seg = [s for s in snaps[i:hi] if s["ts"] - t0 <= MAX_RUN_S]
        before = snaps[i - 1] if i > 0 else None
        m: dict = {}

        def first(pred, start=0):
            """(index, moment, +-) of the first poll where pred holds: it happened between that poll and the one
            before -- the midpoint, +- half the gap."""
            for j in range(start, len(seg)):
                if pred(seg[j]):
                    prev_ts = seg[j - 1]["ts"] if j > 0 else (before["ts"] if before else seg[j]["ts"] - 0.5)
                    gap = max(seg[j]["ts"] - prev_ts, 0.05)
                    return j, seg[j]["ts"] - gap / 2, gap / 2
            return None, None, None

        _, t_press, r_press = first(lambda s: True)
        m["press"] = (t_press, r_press)
        old_run = before.get("run_id") if before else None
        # "starting" covers the call and the arm's rest (the Event card shows its walk as starting from the
        # press); "walking" = the walker launched, the turnlog's `event_walk start`
        j, ts, res = first(lambda s: s.get("run_spot") == FORWARD_SPOT and s.get("run_id") != old_run
                           and s.get("run_state") == "walking")
        walk_id = seg[j]["run_id"] if j is not None else None
        if j is None:
            # stopped before it ever walked: still say how the walk-out ended
            j0, _, _ = first(lambda s: s.get("run_spot") == FORWARD_SPOT and s.get("run_id") != old_run)
            if j0 is not None:
                walk_id, j = seg[j0]["run_id"], j0
        elif True:
            m["walk_start"] = (ts, res)
        if j is not None:
            j2, ts2, res2 = first(lambda s: s.get("run_id") == walk_id
                                  and s.get("run_state") not in ("starting", "walking"), j)
            if j2 is not None:
                m["walk_end"] = (ts2, res2)
                m["walk_state"] = seg[j2].get("run_state")
                m["walk_reason"] = seg[j2].get("run_reason")
        for left in ("not_still", "out", "unknown", "moved"):
            jj, tt, rr = first(lambda s, left=left: s.get("left") == left)
            if jj is not None:
                m["box_" + left] = (tt, rr)
                m.setdefault("walked_m", seg[jj].get("walked_m"))
                if left == "out":
                    m["walked_m"] = seg[jj].get("walked_m")
        ju, tu, ru = first(lambda s: s.get("pageant_up") is True)
        if ju is not None:
            m["pageant_up"] = (tu, ru)
            jd, td, rd = first(lambda s: s.get("pageant_up") is False, ju)
            if jd is not None:
                m["pageant_down"] = (td, rd)
        js, tsay, rsay = first(lambda s: s.get("perf_phase") == "done" and s.get("perf_said") is True)
        if js is not None:
            m["line_start"] = (tsay, rsay)
        # the ceremony page (5 steps): step 3 "wave + line, arm down" done while the counter step waits = the
        # pageant's end was sent (the turnlog's `event_pageant end line_done`)
        jw, tw, rw = first(lambda s: len(s.get("steps") or []) >= 4 and s["steps"][2] == "done"
                           and s["steps"][3] in ("todo", "now") and s.get("ended") is None)
        if jw is not None:
            m["line_done"] = (tw, rw)
        jl, tl, rl = first(lambda s: s.get("run_spot") == counter_spot and s.get("run_id") != old_run)
        if jl is not None:
            leg_id = seg[jl]["run_id"]
            jw2, tw2, rw2 = first(lambda s: s.get("run_id") == leg_id and s.get("run_state") == "walking", jl)
            if jw2 is not None:
                m["leg_start"] = (tw2, rw2)
            jl2, tl2, rl2 = first(lambda s: s.get("run_id") == leg_id
                                  and s.get("run_state") not in ("starting", "walking"), jl)
            if jl2 is not None:
                if jw2 is not None:
                    m["leg_end"] = (tl2, rl2)
                m["leg_state"] = seg[jl2].get("run_state")
                m["leg_reason"] = seg[jl2].get("run_reason")
            held = [s["ts"] for s in seg[jl:] if s.get("run_id") == leg_id and s.get("run_held")]
            if held:
                m["leg_held_s"] = held[-1] - held[0]
        je, te, re_ = first(lambda s: s.get("ended") is not None)
        if je is not None:
            m["tour_end"] = (te, re_)
            m["ended"] = seg[je].get("ended")
        last = seg[je] if je is not None else seg[-1]
        for n in last.get("notes") or []:
            mm = re.search(r"ช้ากว่าเสียงกระแทก\s*([\d.]+)\s*วิ", n or "")
            if mm:
                m["late_note_s"] = float(mm.group(1))
        m["owner"] = next((s.get("owner") for s in seg if s.get("owner")), None)
        m["scene"] = next((s.get("scene") for s in seg if s.get("scene")), None)
        m["clock"] = seg[0].get("clock")
        m["n"] = len(seg)
        runs.append(m)
    return runs


# ------------------------------------------------------------------------------------------------ the run
class _Moments:
    """name -> (ts, res, src); `put` keeps the finest."""

    def __init__(self):
        self.m: dict[str, tuple] = {}

    def put(self, name, ts, res, src):
        if ts is None:
            return
        old = self.m.get(name)
        if old is None or res < old[1]:
            self.m[name] = (ts, res, src)

    def ts(self, name):
        v = self.m.get(name)
        return v[0] if v else None

    def span(self, a, b):
        """(seconds, +-res, "srcA>srcB") or (None, None, None)."""
        va, vb = self.m.get(a), self.m.get(b)
        if not va or not vb:
            return None, None, None
        return vb[0] - va[0], va[1] + vb[1], va[2] if va[2] == vb[2] else "%s>%s" % (va[2], vb[2])


def _rows(run, event, **match):
    return [r for r in run["rows"] if r.get("event") == event and all(r.get(k) == v for k, v in match.items())]


def _first(rows, event, after_i=-1, **match):
    for r in rows:
        if r["_i"] > after_i and r.get("event") == event and all(r.get(k) == v for k, v in match.items()):
            return r
    return None


def _short(reason: str | None, n: int = 90) -> str | None:
    if not reason:
        return reason
    reason = re.sub(r"^step \d+ [^:]*: ", "", reason)
    return reason if len(reason) <= n else reason[:n - 1] + "…"


def _build(tl: dict | None, st: dict | None, board, frames, counter, counter_spot) -> dict:
    M = _Moments()
    notes: list[str] = []
    out: dict = {"sources": []}
    rows = tl["rows"] if tl else []
    allrows = (tl["pre"] + tl["rows"] + tl["tail"]) if tl else []

    # ---- press and kind
    debut = None
    if tl:
        out["sources"].append("turnlog")
        p = tl["press_row"]
        M.put("press", p["_ts"] + 0.5, 0.5, "turnlog")
        debut = next((r for r in allrows if r.get("event") == "debut_start"), None)
        if debut and debut.get("wall_ms"):
            M.put("press", debut["wall_ms"] / 1000.0, RES["wall_ms"], "wall_ms")
            M.put("hit", debut["wall_ms"] / 1000.0 + float(debut.get("impact_in_s") or 0.0), RES["wall_ms"], "wall_ms")
        kind = "ceremony" if (p.get("owner") == "ceremony" or debut) else "event"
        out["scene"] = p.get("scene")
        out["counter_planned"] = p.get("counter")
    else:
        kind = "ceremony" if (st or {}).get("owner") == "ceremony" else "event"
        out["scene"] = (st or {}).get("scene")
    if st:
        out["sources"].append("state")
        for name in ("press", "walk_start", "walk_end", "box_not_still", "box_out", "box_unknown", "pageant_up",
                     "pageant_down", "line_start", "line_done", "leg_start", "leg_end", "tour_end"):
            if name in st:
                ts, res = st[name]
                M.put({"pageant_up": "pageant_start", "pageant_down": "pageant_done"}.get(name, name), ts,
                      max(res, 0.025), "state")
        if st.get("clock") != "server":
            notes.append("state polls had no server_time: PC clock + median offset used")
    out["kind"] = kind

    # ---- turnlog moments
    walk_out = leg = None
    if tl:
        call = _first(rows, "event_walk", phase="call")
        if call:
            out["press_to_call_s"] = call.get("waited_s")
            out.setdefault("_src", {})["press_to_call_s"] = "turnlog waited_s"
            if not call.get("up"):
                notes.append("call did not come up")
        elif kind == "ceremony":
            out.setdefault("_src", {})["press_to_call_s"] = "no call step: the ceremony app's call is already live"
        rest_s = _first(rows, "body", action="rest", state="started")
        if rest_s and not rest_s.get("origin"):
            M.put("rest_start", rest_s["_ts"] + 0.5, 0.5, "turnlog")
            rest_d = _first(rows, "body", rest_s["_i"], action="rest", state="done")
            if rest_d:
                M.put("rest_done", rest_d["_ts"] + 0.5, 0.5, "turnlog")
        starts = _rows(tl, "event_walk", phase="start")
        for s in starts:
            end = next((r for r in allrows if r.get("event") == "event_walk" and r.get("phase") == "end"
                        and r.get("run_id") == s.get("run_id")), None)
            w = {"start": s, "end": end}
            if s.get("spot") == FORWARD_SPOT and walk_out is None:
                walk_out = w
            elif s.get("spot") == counter_spot and leg is None:
                leg = w
        # a leg the server refused or a staff stop before it launched: logged as end/refused without a start
        if leg is None:
            r = next((r for r in rows if r.get("event") == "event_walk" and r.get("spot") == counter_spot
                      and r.get("phase") in ("refused", "end")), None)
            if r is not None:
                leg = {"start": None, "end": r}
        if walk_out:
            M.put("walk_start", walk_out["start"]["_ts"] + 0.5, 0.5, "turnlog")
            if walk_out["end"]:
                M.put("walk_end", walk_out["end"]["_ts"] + 0.5, 0.5, "turnlog")
        late = _first(rows, "event_tour", phase="late_walk_out")
        if late:
            out["hit_to_walker_s"] = late.get("late_s")
            out.setdefault("_src", {})["hit_to_walker_s"] = "turnlog late_walk_out (server monotonic)"
        for kind_ in ("out", "not_still", "unknown", "moved", "not_out"):
            r = _first(rows, "event_box", action=kind_)
            if r:
                M.put("box_" + kind_, r["_ts"] + 0.5, 0.5, "turnlog")
                if r.get("walked_m") is not None and (kind_ in ("out", "not_still") or "walkout_box_m" not in out):
                    out["walkout_box_m"] = r.get("walked_m")
                    out["walkout_target_m"] = r.get("of_m")
        pg = _first(rows, "event_pageant", phase="start")
        if pg:
            M.put("pageant_start", pg["_ts"] + 0.5, 0.5, "turnlog")
            if not pg.get("ok", True):
                notes.append("pageant start refused")
            fa = _first(rows, "metric_first_audio", pg["_i"])
            said = _first(rows, "said", pg["_i"])
            if fa:
                M.put("line_start", fa["_ts"] + 0.5, 0.5, "turnlog")
            elif said:
                M.put("line_start", said["_ts"] + 0.5, 0.5, "turnlog")
            ld = _first(rows, "event_pageant", pg["_i"], phase="end", why="line_done")
            if ld:
                M.put("line_done", ld["_ts"] + 0.5, 0.5, "turnlog")
            pd = _first(rows, "body", pg["_i"], action="pageant", state="done")
            if pd:
                M.put("pageant_done", pd["_ts"] + 0.5, 0.5, "turnlog")
        cue = _first(rows, "event_cue")
        if cue:
            out["line_s"] = cue.get("seconds")
            out["line_mode"] = cue.get("mode")
            out["line_ok"] = cue.get("ok")
            if cue.get("cut"):
                notes.append("line cut")
        p0, p1 = M.ts("pageant_start"), M.ts("pageant_done")
        saids = [r for r in rows if r.get("event") == "said" and p0 is not None and r["_ts"] + 1 >= p0
                 and (p1 is None or r["_ts"] <= p1)]          # not "Excuse me" on the leg
        if saids:
            out["line_said_span_s"] = saids[-1]["_ts"] - saids[0]["_ts"]
        # the arm's rest after the pageant: how many times the app said "cooldown" before it took it
        after = M.ts("pageant_done")
        pd_i = next((r["_i"] for r in rows if r.get("event") == "body" and r.get("action") == "pageant"
                     and r.get("state") == "done" and not r.get("origin")), None)
        if pd_i is not None:
            acks = [r for r in rows if r["_i"] > pd_i and r.get("event") == "body_ack" and r.get("action") == "rest"
                    and not r.get("origin")]
            refused = [r for r in acks if r.get("accepted") is False]
            out["rest_refusals"] = sum(1 for r in refused if r.get("reason") == "cooldown")
            ok = next((r for r in acks if r.get("accepted")), None)
            if refused:
                M.put("rest_asked", refused[0]["_ts"] + 0.5, 0.5, "turnlog")
            if ok:
                M.put("rest_accepted", ok["_ts"] + 0.5, 0.5, "turnlog")
                if not refused:
                    M.put("rest_asked", ok["_ts"] + 0.5, 0.5, "turnlog")
            other = sorted({r.get("reason") for r in refused if r.get("reason") != "cooldown"})
            if other:
                notes.append("rest refused: " + ", ".join(map(str, other)))
        elif after is None and kind:
            pass
        if leg and leg["start"]:
            M.put("leg_start", leg["start"]["_ts"] + 0.5, 0.5, "turnlog")
            if leg["end"]:
                M.put("leg_end", leg["end"]["_ts"] + 0.5, 0.5, "turnlog")
        held = _rows(tl, "event_walk", phase="held")
        if held:
            out["leg_held"] = len(held)
        hs = _first(rows, "body", action="handshake", state="started")
        if hs and not hs.get("origin"):
            M.put("hs_start", hs["_ts"] + 0.5, 0.5, "turnlog")
            hd = next((r for r in allrows if r["_i"] > hs["_i"] and r.get("event") == "body"
                       and r.get("action") == "handshake" and not r.get("origin")
                       and r.get("state") in ("done", "stopped", "failed")), None)
            if hd:
                M.put("hs_end", hd["_ts"] + 0.5, 0.5, "turnlog")
                out["handshake_state"] = hd.get("state")
        if tl["end_row"]:
            M.put("tour_end", tl["end_row"]["_ts"] + 0.5, 0.5, "turnlog")
            out["ended_why"] = tl["end_row"].get("why")
        # head commands while the card runs (the card's rule: the server does not turn the head)
        heads = [r for r in rows[1:-1] if r.get("event") == "head_look"] if tl["end_row"] else \
            [r for r in rows[1:] if r.get("event") == "head_look"]
        out["head_cmds"] = len(heads)
        whys: dict = {}
        for r in heads:
            whys[r.get("why")] = whys.get(r.get("why"), 0) + 1
        out["head_whys"] = whys
        out["head_after_end"] = next((r.get("why") for r in tl["tail"] if r.get("event") == "head_look"), None)
        # the screen's reveal (ceremony)
        down = next((r for r in allrows if r.get("event") == "debut_down"), None)
        kept = next((r for r in allrows if r.get("event") == "event_tour" and r.get("phase") == "reveal_kept"), None)
        if down:
            out["reveal"] = "down (%s%s)" % (down.get("why"), ", before the hit" if down.get("before_hit") else "")
        elif kept:
            out["reveal"] = "kept (%s)" % kept.get("why")
        elif debut:
            out["reveal"] = "played through"
        # the walk's own stop reasons: pose jumps and lost lines
        jumps = []
        for w in (walk_out, leg):
            if w and w["end"]:
                mm = re.search(r"pose jumped ([\d.]+) m / ([\d.]+)°", w["end"].get("reason") or "")
                if mm:
                    jumps.append({"ts": w["end"]["_ts"], "m": float(mm.group(1)), "deg": float(mm.group(2)),
                                  "src": "walker stop", "spot": w["end"].get("spot")})
        lost = [r for r in rows if r.get("event") == "event_walk" and r.get("phase") == "back_line"
                and r.get("why") == "lost"]
        if lost:
            notes.append("back line lost %dx (pose jump?)" % len(lost))
        out["_walker_jumps"] = jumps
        if walk_out and walk_out["end"]:
            out["walkout_state"] = walk_out["end"].get("state")
            out["walkout_reason"] = _short(walk_out["end"].get("reason"))
            out["walkout_walk_s"] = walk_out["end"].get("seconds")
            mm = re.search(r"\(([\d.]+) of ([\d.]+) m\)", walk_out["end"].get("reason") or "")
            if mm:
                out["walkout_walker_m"] = float(mm.group(1))
        if leg and leg["end"]:
            out["leg_state"] = leg["end"].get("state") or leg["end"].get("phase")
            out["leg_reason"] = _short(leg["end"].get("reason"))
            out["leg_walk_s"] = leg["end"].get("seconds")
    elif st:
        out["ended_why"] = st.get("ended")
        if st.get("walked_m") is not None:
            out["walkout_box_m"] = st.get("walked_m")
            out.setdefault("_src", {})["walkout_box_m"] = "staff page box.walked_m (2 decimals)"
            out.setdefault("prec", {})["walkout_box_m"] = 0.005
        out["walkout_state"] = st.get("walk_state")
        out["walkout_reason"] = _short(st.get("walk_reason"))
        out["leg_state"] = st.get("leg_state")
        out["leg_reason"] = _short(st.get("leg_reason"))
        if st.get("leg_held_s"):
            out["leg_held"] = 1
        notes.append("staff-page polls only (turnlog pulled before this run): no arm rest, line, head, "
                     "handshake or board pose")
    if st and st.get("late_note_s") is not None and out.get("hit_to_walker_s") is None:
        out["hit_to_walker_s"] = st["late_note_s"]
        out.setdefault("_src", {})["hit_to_walker_s"] = "staff page note (0.1 s)"
        out.setdefault("prec", {})["hit_to_walker_s"] = 0.05
    if st and tl and st.get("late_note_s") is not None and out.get("hit_to_walker_s") is not None:
        if abs(st["late_note_s"] - out["hit_to_walker_s"]) > 0.06:
            notes.append("staff page says late %.1f s, turnlog %.2f s" % (st["late_note_s"], out["hit_to_walker_s"]))

    # ---- board pose / walk frames: the wheels
    press = M.ts("press")
    out["press_ts"] = press

    def wheels(t0, t1, stop_before=None, label=""):
        mo = None
        if board:
            mo = motion(board, t0, t1, stop_before=stop_before)
            if mo is not None:
                mo["src"] = "board"
        if (mo is None or mo.get("n", 0) < 4) and frames:
            fs = [(f[0], f[1], f[2], f[3], f[4]) for fr in frames for f in fr["frames"]]
            fs.sort()
            mf = motion(fs, t0, t1, stop_before=stop_before)
            if mf is not None:
                mf["src"] = "frames"
                mo = mf
        return mo

    nxt = M.ts("pageant_start") or M.ts("leg_start") or M.ts("tour_end")
    wo = None
    if M.ts("walk_start") is not None:
        t_end = M.ts("walk_end") or (M.ts("walk_start") + 30)
        wo = wheels(M.ts("walk_start"), t_end, stop_before=(nxt + 0.5) if nxt and nxt > t_end else None)
    if wo is not None:
        out["sources"].append(wo["src"])
        out["walkout_q"] = [wo["q_min"], wo["q_max"]]
        if wo.get("moved"):
            M.put("wheels_start", wo["start_at"], wo["start_res"], wo["src"])
            M.put("wheels_stop", wo["stop_at"], wo["stop_res"], wo["src"])
            line_deg = next((fr.get("line_deg") for fr in frames if fr.get("line_deg") is not None
                             and fr["label"].endswith(FORWARD_SPOT)), None)
            g = geometry(wo["start_pose"], wo["end_pose"], line_deg)
            out["walkout_pose_m"] = _r(g["dist"], 3)
            out["walkout_along_m"] = _r(g["along"], 3)
            out["walkout_lateral_m"] = _r(g["lateral"], 3)
            out["walkout_line_deg"] = line_deg
            out["walkout_lateral_line_m"] = _r(g["lateral_line"], 3)
            out["walkout_dir_deg"] = _r(g["dir_deg"], 1)
            out["walkout_yaw_change_deg"] = _r(g["yaw_change_deg"], 1)
            if wo["jumps"]:
                # what the wheels did: the straight line with the board's jumps taken out
                jx = sum(j["dx"] for j in wo["jumps"])
                jy = sum(j["dy"] for j in wo["jumps"])
                out["walkout_pose_nojump_m"] = _r(geometry(wo["start_pose"], wo["end_pose"], None, (jx, jy))["dist"], 3)
            ml = max_lateral(board if wo["src"] == "board" else
                             sorted((f[0], f[1], f[2], f[3], f[4]) for fr in frames for f in fr["frames"]),
                             wo["start_pose"], wo["first_move"], wo["last_move"])
            out["walkout_lateral_max_m"] = _r(ml, 3)
            out["walkout_wheels_s"] = _r(wo["stop_at"] - wo["start_at"])
            out["_board_jumps"] = [dict(j, spot=FORWARD_SPOT) for j in wo["jumps"]]
        else:
            out["walkout_pose_m"] = 0.0
            notes.append("walk-out: the pose never moved (%d samples)" % wo["n"])
    lg = None
    if M.ts("leg_start") is not None and M.ts("leg_end") is not None:
        lg = wheels(M.ts("leg_start"), M.ts("leg_end"),
                    stop_before=(M.ts("hs_start") + 0.5) if M.ts("hs_start") and M.ts("hs_start") > M.ts("leg_end") + 2
                    else None)
    if lg is not None:
        if lg["src"] not in out["sources"]:
            out["sources"].append(lg["src"])
        out["leg_q"] = [lg["q_min"], lg["q_max"]]
        if lg.get("moved"):
            M.put("leg_wheels_start", lg["start_at"], lg["start_res"], lg["src"])
            M.put("leg_wheels_stop", lg["stop_at"], lg["stop_res"], lg["src"])
            out["leg_wheels_s"] = _r(lg["stop_at"] - lg["start_at"])
            out["leg_longest_stop_s"] = _r(lg["longest_stop_s"], 1)
            if lg["longest_stop_s"] >= STOP_NOTE_S:
                out["leg_longest_stop_at"] = _th(lg["longest_stop_at"])
            out["_board_jumps"] = out.get("_board_jumps", []) + [dict(j, spot=counter_spot) for j in lg["jumps"]]
            if out.get("leg_state") == "arrived" and counter:
                x, y, yaw = lg["end_pose"]
                dx, dy = x - counter[0], y - counter[1]
                out["arrival_err_m"] = _r(math.hypot(dx, dy), 3)
                out["arrival_dx_m"] = _r(dx, 3)
                out["arrival_dy_m"] = _r(dy, 3)
                out["arrival_yaw_err_deg"] = _r(_wrap_deg(math.degrees(yaw) - counter[2]), 1)
                out["arrival_pose"] = [_r(x, 3), _r(y, 3), _r(math.degrees(yaw), 1)]

    # ---- the frames' own head values during the card's walks
    if frames and press is not None:
        end_t = M.ts("tour_end") or (press + MAX_RUN_S)
        turned = [f for fr in frames for f in fr["frames"]
                  if press <= f[0] <= end_t and f[6] and any(abs(float(v or 0)) > 0.5 for v in f[6])]
        if any(press <= f[0] <= end_t for fr in frames for f in fr["frames"]):
            out["frames_head_turned"] = len(turned)
            if "frames" not in out["sources"]:
                out["sources"].append("frames")

    # ---- jumps, both kinds, de-duplicated by time
    jumps = out.pop("_walker_jumps", []) + out.pop("_board_jumps", [])
    kept_j: list = []
    for j in sorted(jumps, key=lambda j: j["ts"]):
        if kept_j and abs(j["ts"] - kept_j[-1]["ts"]) <= 2.0:
            kept_j[-1]["src"] = kept_j[-1]["src"] + "+" + j["src"]
            continue
        kept_j.append({k: v for k, v in j.items() if k not in ("dx", "dy")})
    for j in kept_j:
        j["at_s"] = _r(j.pop("ts") - press) if press is not None else None
    out["pose_jumps"] = len(kept_j)
    out["pose_jump_list"] = kept_j

    # ---- durations
    src, res = out.pop("_src", {}), {}

    def span(key, a, b, n=2):
        v, rr, s = M.span(a, b)
        if v is not None:
            out[key] = round(v, n)
            res[key] = round(rr, 3)
            src[key] = s

    span("rest_s", "rest_start", "rest_done")
    span("press_to_walker_s", "press", "walk_start")
    span("press_to_hit_s", "press", "hit")
    span("hit_to_launch_s", "hit", "walk_start")
    span("hit_to_wheels_s", "hit", "wheels_start")
    span("walker_to_wheels_s", "walk_start", "wheels_start")
    span("arrived_to_out_s", "walk_end", "box_out")
    span("stop_to_out_s", "wheels_stop", "box_out")
    span("out_to_pageant_s", "box_out", "pageant_start")
    span("pageant_to_line_s", "pageant_start", "line_start")
    span("pageant_to_line_done_s", "pageant_start", "line_done")
    span("pageant_total_s", "pageant_start", "pageant_done")
    span("rest_wait_s", "rest_asked", "rest_accepted")
    span("pageant_done_to_leg_s", "pageant_done", "leg_start")
    span("handshake_s", "hs_start", "hs_end")
    span("end_s", "press", "tour_end")
    if "leg_walk_s" not in out:
        span("leg_walk_s", "leg_start", "leg_end", 1)
    if out.get("hit_to_wheels_s") is not None and out.get("hit_to_walker_s") is not None:
        out["walker_late_vs_wheels_s"] = round(out["hit_to_wheels_s"] - out["hit_to_walker_s"], 2)
    out["src"] = src
    out["res"] = res
    out["moments"] = {k: {"th": _th(v[0]), "rel_s": _r(v[0] - press) if press else None, "res": round(v[1], 3),
                          "src": v[2]} for k, v in sorted(M.m.items(), key=lambda kv: kv[1][0])}
    out["notes"] = notes
    out["press_th"] = _th(press)
    out["date"] = _dt.datetime.fromtimestamp(press, THAI).date().isoformat() if press else None
    out["run"] = (_dt.datetime.fromtimestamp(press, THAI).strftime("%Y%m%d-%H%M%S") + "-" + kind) if press else None
    if "ended_why" not in out and st and st.get("ended"):
        out["ended_why"] = st.get("ended")
    if out.get("ended_why") is None:
        out["ended_why"] = None
        notes.append("no end row: the log ended inside the run")
    return out


def _match_frames(frames_runs, t0, t1):
    if t0 is None:
        return []
    return [fr for fr in frames_runs if fr["label"].startswith("event-") and fr["start"] is not None
            and t0 - 3 <= fr["start"] <= (t1 or t0 + MAX_RUN_S) + 3]


def extract_runs(turnlog_path=None, *, boardlogs=(), walkframes=(), statelogs=(), date=None,
                 counter=COUNTER_POSE, counter_spot: str = COUNTER_SPOT, since: float | None = None,
                 until: float | None = None, kinds=None) -> list[dict]:
    """Every card-1 / ceremony-opening press found in the sources, as one dict each (see the module docstring
    and `FIELDS`). Paths may be globs. `since`/`until` = epoch seconds of the press (robot clock); `kinds` =
    {"event", "ceremony"}. A press seen in both the turnlog and the staff-page polls is one run (<= 3 s apart);
    a press seen only in the polls is still returned (fewer numbers, a note says why)."""
    rows = load_turnlog(turnlog_path, date) if turnlog_path else []
    board = load_boardlogs(boardlogs)
    frames_runs = load_walkframes(walkframes)
    snaps = load_statelogs(statelogs, t_min=since,
                           t_max=(until + MAX_RUN_S) if until is not None else None)
    tls = _turnlog_runs(rows)
    sts = _state_runs(snaps, counter_spot)
    used = set()
    pairs = []
    for tl in tls:
        t = tl["press_row"]["_ts"]
        best = None
        for k, st in enumerate(sts):
            if k in used:
                continue
            d = abs(st["press"][0] - t - 0.5)          # a turnlog second covers t .. t+1
            if d <= SAME_PRESS_S and (best is None or d < best[0]):
                best = (d, k)
        st = None
        if best is not None:
            used.add(best[1])
            st = sts[best[1]]
        pairs.append((tl, st))
    for k, st in enumerate(sts):
        if k not in used:
            pairs.append((None, st))
    out = []
    for tl, st in pairs:
        t0 = tl["press_row"]["_ts"] if tl else st["press"][0]
        t1 = (tl["end_row"]["_ts"] if tl and tl["end_row"] else
              (st["tour_end"][0] if st and "tour_end" in st else t0 + MAX_RUN_S))
        if since is not None and t0 < since - 1:
            continue
        if until is not None and t0 > until:
            continue
        run = _build(tl, st, board, _match_frames(frames_runs, t0, t1), counter, counter_spot)
        if kinds and run["kind"] not in kinds:
            continue
        out.append(run)
    out.sort(key=lambda r: r.get("press_ts") or 0)
    return out


# --------------------------------------------------------------------------------- harness helpers / summary
def check_run(run: dict, limits=DEFAULT_LIMITS) -> list[str]:
    """Gate failures for one run ("metric=value outside [lo, hi]"). A metric the run has no value for is not
    a failure -- a run that never got that far is judged by `ended_why`."""
    bad = []
    for key, lo, hi in limits:
        v = run.get(key)
        if v is None:
            continue
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            bad.append("%s=%s outside [%s, %s]" % (key, v, lo, hi))
    return bad


def summarize(runs: list[dict], keys=None) -> dict:
    """metric -> {n, min, median, max} across runs (numbers only)."""
    keys = keys or ([k for k, _, _ in TABLE if k not in ("press_th", "kind", "ended_why", "reveal", "walkout_state",
                                                      "leg_state", "head_whys")] + SUMMARY_EXTRA)
    out = {}
    for k in keys:
        vals = [r[k] for r in runs if isinstance(r.get(k), (int, float)) and not isinstance(r.get(k), bool)]
        if vals:
            out[k] = {"n": len(vals), "min": min(vals), "median": round(statistics.median(vals), 3),
                      "max": max(vals)}
    return out


#: (field, column title, unit) -- the baseline table, in the card's own order
TABLE = [
    ("press_th", "press (Thai)", ""),
    ("kind", "kind", ""),
    ("ended_why", "ended", ""),
    ("press_to_call_s", "press→call", "s"),
    ("rest_s", "arm rest", "s"),
    ("hit_to_walker_s", "hit→walker", "s"),
    ("hit_to_wheels_s", "hit→wheels", "s"),
    ("walkout_box_m", "walk-out (box)", "m"),
    ("walkout_pose_m", "walk-out (pose)", "m"),
    ("walkout_lateral_m", "lateral", "m"),
    ("walkout_lateral_line_m", "lateral vs line", "m"),
    ("walkout_wheels_s", "wheels moving", "s"),
    ("arrived_to_out_s", "arrived→out", "s"),
    ("out_to_pageant_s", "out→pageant", "s"),
    ("pageant_to_line_s", "pageant→line", "s"),
    ("line_s", "line", "s"),
    ("pageant_to_line_done_s", "pageant→line done", "s"),
    ("pageant_total_s", "pageant→arm down", "s"),
    ("rest_refusals", "rest cooldowns", ""),
    ("rest_wait_s", "rest wait", "s"),
    ("leg_walk_s", "counter leg", "s"),
    ("leg_longest_stop_s", "longest stop", "s"),
    ("arrival_err_m", "arrival err", "m"),
    ("handshake_s", "handshake", "s"),
    ("pose_jumps", "pose jumps", ""),
    ("head_cmds", "head cmds", ""),
    ("reveal", "screen reveal", ""),
]
FIELDS = [k for k, _, _ in TABLE]
#: numbers kept on every run but not in the table (spread + harness comparisons)
SUMMARY_EXTRA = ["press_to_walker_s", "hit_to_launch_s", "walker_to_wheels_s", "walker_late_vs_wheels_s",
                 "walkout_along_m", "walkout_lateral_max_m", "walkout_dir_deg", "stop_to_out_s",
                 "pageant_done_to_leg_s", "leg_wheels_s", "arrival_dx_m", "arrival_dy_m", "arrival_yaw_err_deg",
                 "end_s"]


def _fmt(v, unit=""):
    if v is None:
        return "–"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return ("%.3f" % v) if unit == "m" else ("%.2f" % v)
    if isinstance(v, (list, tuple)):
        return "–".join(_fmt(x) for x in v)
    if isinstance(v, dict):
        return ", ".join("%s×%s" % (k, n) for k, n in v.items()) or "–"
    return str(v)


# ------------------------------------------------------------------------------------------------- writers
def write_jsonl(runs, path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in runs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def inconsistencies(runs: list[dict], hand: dict | None = None) -> list[str]:
    """Things that do not add up, run by run -- what the report's reader should look at first."""
    out = []
    for r in runs:
        tag = "%s %s" % (r.get("press_th"), r.get("kind"))
        if r.get("walkout_box_m") is not None and r.get("walkout_pose_m") is not None and r.get("pose_jumps", 0) == 0:
            d = r["walkout_pose_m"] - r["walkout_box_m"]
            if abs(d) > 0.03:
                out.append("%s: walk-out box %.3f m vs board pose %.3f m (%+.3f)" % (tag, r["walkout_box_m"],
                                                                                   r["walkout_pose_m"], d))
        if r.get("walker_late_vs_wheels_s") is not None and r["walker_late_vs_wheels_s"] > 0.4:
            out.append("%s: wheels moved %.2f s after the walker said it started (late_walk_out %.2f s, board "
                       "%.2f s after the hit)" % (tag, r["walker_late_vs_wheels_s"], r["hit_to_walker_s"],
                                                  r["hit_to_wheels_s"]))
        if r.get("pose_jumps"):
            js = "; ".join("%s m at %s s (%s)" % (j.get("m"), j.get("at_s"), j.get("src")) for j in r["pose_jump_list"])
            out.append("%s: pose jump %s" % (tag, js))
        if r.get("head_cmds"):
            out.append("%s: %d head command(s) during the card (%s) -- the card's rule is none" %
                       (tag, r["head_cmds"], _fmt(r.get("head_whys"))))
        if r.get("rest_refusals"):
            out.append("%s: arm rest refused %d× (cooldown) after the pageant%s" %
                       (tag, r["rest_refusals"], (", accepted %.1f s after the first ask" % r["rest_wait_s"])
                        if r.get("rest_wait_s") is not None else
                        " and never accepted inside the card (it ended: %s)" % r.get("ended_why")))
        if r.get("walkout_box_m") is not None and r.get("walkout_walker_m") is not None and r.get("walkout_state") != "arrived":
            if abs(r["walkout_box_m"] - r["walkout_walker_m"]) > 0.05:
                out.append("%s: walker stopped at %.2f m but the box counted %.3f m (%s)" %
                           (tag, r["walkout_walker_m"], r["walkout_box_m"], r.get("walkout_reason")))
        for n in r.get("notes") or []:
            if n.startswith("staff page says"):
                out.append("%s: %s" % (tag, n))
    if hand:
        for key, (lo, hi, what) in hand.items():
            for r in runs:
                v = r.get(key)
                if v is None:
                    continue
                if key.startswith("walkout_") and r.get("walkout_state") not in (None, "arrived"):
                    continue                             # a walk-out that never finished is not this measurement
                tol = (r.get("prec") or {}).get(key, 0.0) + 1e-9    # a 2-decimal source: 1.01 may be 1.009
                if not (lo - tol <= v <= hi + tol):
                    out.append("%s %s: %s = %s outside the hand-measured %s–%s (%s)" %
                               (r.get("press_th"), r.get("kind"), key, v, lo, hi, what))
    return out


def write_markdown(runs, path, title="Card 1 baseline", hand=None) -> str:
    lines = ["# %s" % title, "",
             "%d runs. Times are the robot's clock (Thai). `–` = the run never got there or no source covers it." %
             len(runs), ""]
    cols = TABLE
    lines.append("| " + " | ".join(t + (" (%s)" % u if u else "") for _, t, u in cols) + " |")
    lines.append("|" + "---|" * len(cols))
    for r in runs:
        lines.append("| " + " | ".join(_fmt(r.get(k), u).replace("|", "/") for k, _, u in cols) + " |")
    lines += ["", "## Per run", ""]
    for r in runs:
        lines.append("- **%s %s** — ended `%s`; sources %s. %s%s" % (
            r.get("press_th"), r.get("kind"), r.get("ended_why"), "+".join(r.get("sources") or []),
            "walk-out %s (%s). " % (r.get("walkout_state"), r.get("walkout_reason")) if r.get("walkout_reason") else "",
            " ".join(r.get("notes") or [])))
        if r.get("arrival_pose"):
            lines.append("  - counter arrival pose %s vs %s: dx %s dy %s yaw %s°, Q %s" % (
                r["arrival_pose"], list(COUNTER_POSE), r.get("arrival_dx_m"), r.get("arrival_dy_m"),
                r.get("arrival_yaw_err_deg"), _fmt(r.get("leg_q"))))
    s = summarize(runs)
    lines += ["", "## Spread", "", "| metric | n | min | median | max |", "|---|---|---|---|---|"]
    for k, v in s.items():
        lines.append("| %s | %d | %s | %s | %s |" % (k, v["n"], v["min"], v["median"], v["max"]))
    inc = inconsistencies(runs, hand)
    lines += ["", "## Inconsistent / worth a look", ""] + (["- " + x for x in inc] or ["- none"])
    text = "\n".join(lines) + "\n"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding="utf-8")
    return text


_CSS = """
:root{color-scheme:light;--bg:#fcfcfb;--panel:#ffffff;--ink:#0b0b0b;--ink2:#52514e;--ink3:#8a8984;--rule:#e4e3df;
--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--s5:#e87ba4;--s6:#008300;--s7:#6250d6;--good:#0ca30c;--bad:#d03b3b;--warn:#b77900}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#1a1a19;--panel:#222220;
--ink:#f0efec;--ink2:#c3c2b7;--ink3:#8f8e86;--rule:#383835;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;
--s5:#d55181;--s6:#0ca30c;--s7:#9085e9;--good:#0ca30c;--bad:#e66767;--warn:#fab219}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#1a1a19;--panel:#222220;--ink:#f0efec;--ink2:#c3c2b7;--ink3:#8f8e86;
--rule:#383835;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--s5:#d55181;--s6:#0ca30c;--s7:#9085e9;
--good:#0ca30c;--bad:#e66767;--warn:#fab219}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",
"Noto Sans Thai",sans-serif}main{max-width:1280px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:32px 0 8px}p.sub{color:var(--ink2);margin:0 0 16px}
.wrap{overflow-x:auto;border:1px solid var(--rule);border-radius:8px;background:var(--panel)}
table{border-collapse:collapse;font-variant-numeric:tabular-nums;width:100%}
th,td{padding:6px 8px;border-bottom:1px solid var(--rule);text-align:right;white-space:nowrap}
th{font-weight:600;color:var(--ink2);font-size:12px;position:sticky;top:0;background:var(--panel)}
td.l,th.l{text-align:left}tr:last-child td{border-bottom:0}td.na{color:var(--ink3)}
td.bad{color:var(--bad);font-weight:600}.tag{display:inline-block;padding:1px 6px;border-radius:9px;
border:1px solid var(--rule);font-size:12px}.tag.ok::before{content:"✓ ";color:var(--good)}.tag.no::before{content:"✕ ";color:var(--bad)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--rule);border-radius:8px;padding:12px}
.card h3{font-size:13px;margin:0 0 6px;color:var(--ink2);font-weight:600}
svg text{fill:var(--ink2);font-size:11px}svg .ax{stroke:var(--rule)}svg .band{fill:var(--s3);opacity:.14}
.legend{display:flex;flex-wrap:wrap;gap:12px;margin:6px 0 0;color:var(--ink2);font-size:12px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
ul.inc li{margin:4px 0}code{font-size:12px}details{margin-top:8px}details pre{white-space:pre-wrap;font-size:11px;color:var(--ink2)}
"""

#: phase bars on the timeline: (from moment, to moment, label, css var)
PHASES = [
    ("press", "walk_start", "press→walker (call, arm rest, hit)", "--s4"),
    ("walk_start", "walk_end", "walk-out", "--s1"),
    ("walk_end", "pageant_start", "arrived→out→pageant", "--s5"),
    ("pageant_start", "line_done", "pageant + line", "--s2"),
    ("line_done", "pageant_done", "arm coming down", "--s7"),
    ("pageant_done", "leg_start", "rest (cooldown)", "--s5"),
    ("leg_start", "leg_end", "counter leg", "--s3"),
    ("hs_start", "hs_end", "handshake", "--s6"),
]


def _timeline_svg(runs) -> str:
    w, lh, left, right = 1080, 30, 150, 20
    spans = [r.get("end_s") or max((m["rel_s"] or 0) for m in r["moments"].values()) for r in runs]
    tmax = max([s for s in spans if s] + [10.0])
    tmax = math.ceil(tmax / 10.0) * 10
    h = 30 + lh * len(runs) + 24
    sx = lambda t: left + (w - left - right) * (t / tmax)
    parts = ['<svg viewBox="0 0 %d %d" width="100%%" role="img" aria-label="phase timeline per run">' % (w, h)]
    for t in range(0, int(tmax) + 1, 10):
        parts.append('<line class="ax" x1="%.1f" x2="%.1f" y1="20" y2="%d"/>' % (sx(t), sx(t), h - 20))
        parts.append('<text x="%.1f" y="%d" text-anchor="middle">%ds</text>' % (sx(t), h - 6, t))
    for k, r in enumerate(runs):
        y = 26 + k * lh
        mo = r["moments"]
        parts.append('<text x="%d" y="%d" text-anchor="end">%s %s</text>' % (left - 8, y + 14, html.escape(
            (r.get("press_th") or "")[:8]), r.get("kind")))
        for a, b, label, var in PHASES:
            if a in mo and b in mo and mo[a]["rel_s"] is not None and mo[b]["rel_s"] is not None:
                x0, x1 = sx(max(mo[a]["rel_s"], 0)), sx(mo[b]["rel_s"])
                if x1 - x0 < 1:
                    continue
                parts.append('<rect x="%.1f" y="%d" width="%.1f" height="18" rx="3" fill="var(%s)">'
                             '<title>%s: %.1f s (%s → %s)</title></rect>' % (
                                 x0, y, max(x1 - x0 - 2, 1), var, label, mo[b]["rel_s"] - mo[a]["rel_s"],
                                 mo[a]["th"], mo[b]["th"]))
        if "hit" in mo:
            xh = sx(mo["hit"]["rel_s"])
            parts.append('<line x1="%.1f" x2="%.1f" y1="%d" y2="%d" stroke="var(--ink)" stroke-width="2">'
                         '<title>screen hit %s</title></line>' % (xh, xh, y - 3, y + 21, mo["hit"]["th"]))
        if "wheels_start" in mo:
            xw = sx(mo["wheels_start"]["rel_s"])
            parts.append('<circle cx="%.1f" cy="%d" r="4" fill="var(--panel)" stroke="var(--ink)" stroke-width="2">'
                         '<title>wheels start (board pose) %s</title></circle>' % (xw, y + 9, mo["wheels_start"]["th"]))
        end = r.get("end_s")
        if end is not None:
            xe = sx(end)
            parts.append('<text x="%.1f" y="%d">■ %s</text>' % (xe + 4, y + 14, html.escape(str(r.get("ended_why")))))
    parts.append("</svg>")
    legend = '<div class="legend">' + "".join('<span><i style="background:var(%s)"></i>%s</span>' % (v, l)
                                                for _, _, l, v in PHASES)
    legend += '<span>│ black line = screen hit</span><span>○ = wheels start (board pose)</span></div>'
    return "".join(parts) + legend


def _strip_svg(runs, key, unit, band=None, ref_key=None, ref_label=None, title="") -> str:
    vals = [(r, r.get(key)) for r in runs if isinstance(r.get(key), (int, float))]
    refs = [(r, r.get(ref_key)) for r in runs if ref_key and isinstance(r.get(ref_key), (int, float))]
    if not vals and not refs:
        return '<div class="card"><h3>%s</h3><p class="sub">no values</p></div>' % html.escape(title)
    allv = [v for _, v in vals + refs] + (list(band[:2]) if band else [])
    lo, hi = min(allv), max(allv)
    pad = (hi - lo) * 0.15 or abs(hi) * 0.1 or 0.1
    lo, hi = lo - pad, hi + pad
    w, h, l, rr = 340, 40 + 20 * len(runs), 92, 14
    sx = lambda v: l + (w - l - rr) * (v - lo) / (hi - lo)
    p = ['<svg viewBox="0 0 %d %d" width="100%%" role="img" aria-label="%s">' % (w, h, html.escape(title))]
    if band:
        p.append('<rect class="band" x="%.1f" y="8" width="%.1f" height="%d"><title>%s</title></rect>' % (
            sx(band[0]), max(sx(band[1]) - sx(band[0]), 2), h - 30, html.escape(band[2])))
    for t in (lo + pad, (lo + hi) / 2, hi - pad):
        p.append('<line class="ax" x1="%.1f" x2="%.1f" y1="8" y2="%d"/>' % (sx(t), sx(t), h - 22))
        p.append('<text x="%.1f" y="%d" text-anchor="middle">%s%s</text>' % (sx(t), h - 8, ("%.3f" if unit == "m" else "%.2f") % t, unit))
    for k, r in enumerate(runs):
        y = 18 + 20 * k
        p.append('<text x="%d" y="%d" text-anchor="end">%s</text>' % (l - 6, y + 4, (r.get("press_th") or "")[:8]))
        v = r.get(key)
        rv = r.get(ref_key) if ref_key else None
        if isinstance(rv, (int, float)):
            p.append('<rect x="%.1f" y="%d" width="9" height="9" rx="2" fill="var(--panel)" stroke="var(--s2)" '
                     'stroke-width="2"><title>%s %s: %s%s</title></rect>' % (sx(rv) - 4.5, y - 4, r.get("press_th"),
                                                                             ref_label, rv, unit))
        if isinstance(v, (int, float)):
            p.append('<circle cx="%.1f" cy="%d" r="5" fill="var(--s1)" stroke="var(--panel)" stroke-width="2">'
                     '<title>%s: %s%s</title></circle>' % (sx(v), y, r.get("press_th"), v, unit))
    p.append("</svg>")
    leg = '<div class="legend"><span><i style="background:var(--s1)"></i>%s</span>' % html.escape(key)
    if ref_key:
        leg += '<span><i style="background:var(--panel);border:2px solid var(--s2)"></i>%s</span>' % html.escape(ref_label)
    if band:
        leg += '<span><i style="background:var(--s3);opacity:.4"></i>%s</span>' % html.escape(band[2])
    return '<div class="card"><h3>%s</h3>%s%s</div></div>' % (html.escape(title), "".join(p), leg)


def write_html(runs, path, title="Card 1 baseline", hand=None, limits=DEFAULT_LIMITS, sources=None) -> None:
    hand = hand or {}
    lim = {k: (lo, hi) for k, lo, hi in limits}
    head = "".join('<th class="%s">%s%s</th>' % ("l" if k in ("press_th", "kind", "ended_why", "reveal") else "",
                                                html.escape(t), " <span style='font-weight:400'>(%s)</span>" % u if u else "")
                   for k, t, u in TABLE)
    body = []
    for r in runs:
        cells = []
        for k, t, u in TABLE:
            v = r.get(k)
            cls = []
            if k in ("press_th", "kind", "ended_why", "reveal"):
                cls.append("l")
            if v is None:
                cls.append("na")
            elif k in lim and isinstance(v, (int, float)):
                lo, hi = lim[k]
                if (lo is not None and v < lo) or (hi is not None and v > hi):
                    cls.append("bad")
            tip = ""
            if r.get("res", {}).get(k) is not None:
                tip = ' title="±%s s · %s"' % (r["res"][k], html.escape(str(r["src"].get(k))))
            elif r.get("src", {}).get(k):
                tip = ' title="%s"' % html.escape(str(r["src"][k]))
            cells.append('<td class="%s"%s>%s</td>' % (" ".join(cls), tip, html.escape(_fmt(v, u))))
        body.append("<tr>%s</tr>" % "".join(cells))
    inc = inconsistencies(runs, hand)
    gates = []
    for r in runs:
        bad = check_run(r, limits)
        gates.append('<li><b>%s %s</b> <span class="tag %s">%s</span> %s</li>' % (
            html.escape(r.get("press_th") or ""), r.get("kind"), "no" if bad else "ok",
            "outside gates" if bad else "inside gates", html.escape("; ".join(bad))))
    strips = [
        _strip_svg(runs, "walkout_box_m", "m", band=hand.get("walkout_box_m") and
                   (hand["walkout_box_m"][0], hand["walkout_box_m"][1], "hand-measured"),
                   ref_key="walkout_pose_m", ref_label="board pose straight line", title="Walk-out distance"),
        _strip_svg(runs, "hit_to_wheels_s", "s", ref_key="hit_to_walker_s", ref_label="hit→walker (late_walk_out)",
                   band=hand.get("hit_to_walker_s") and (hand["hit_to_walker_s"][0], hand["hit_to_walker_s"][1],
                                                         "hand-measured"),
                   title="Screen hit → wheels (board pose) vs → walker start"),
        _strip_svg(runs, "walkout_lateral_m", "m", title="Walk-out lateral drift at the stop (+ = robot's left)"),
        _strip_svg(runs, "arrival_err_m", "m", band=hand.get("arrival_err_m") and
                   (hand["arrival_err_m"][0], hand["arrival_err_m"][1], "hand-measured"),
                   title="Counter arrival error vs (−8.62, −4.20)"),
        _strip_svg(runs, "pageant_to_line_s", "s", title="Pageant start → first audio of the line (turnlog ±2 s)"),
        _strip_svg(runs, "rest_wait_s", "s", ref_key="rest_refusals", ref_label="cooldown refusals (count)",
                   title="Arm rest after the pageant: wait until accepted"),
    ]
    detail = []
    for r in runs:
        detail.append("<details><summary>%s %s — moments & notes</summary><pre>%s</pre></details>" % (
            html.escape(r.get("press_th") or ""), r.get("kind"),
            html.escape(json.dumps({k: r.get(k) for k in ("moments", "notes", "pose_jump_list", "walkout_reason",
                                                          "leg_reason", "arrival_pose", "leg_q", "walkout_q",
                                                          "sources", "src", "res")}, ensure_ascii=False, indent=1))))
    doc = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Card 1 baseline</title><style>%s</style></head><body><main>
<h1>%s</h1><p class="sub">%d runs · robot clock (Thai) · hover a cell for its ± and source · `–` = not reached / no source.%s</p>
<h2>Baseline — one row per run</h2><div class="wrap"><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>
<h2>Where each run's time went (from the press)</h2><div class="card">%s</div>
<h2>Key numbers</h2><div class="grid">%s</div>
<h2>Inconsistent / worth a look</h2><ul class="inc">%s</ul>
<h2>Bench gates (DEFAULT_LIMITS)</h2><ul class="inc">%s</ul>
<h2>Per-run detail</h2>%s
</main></body></html>""" % (_CSS, html.escape(title), len(runs),
                            (" Sources: " + html.escape(", ".join(sources))) if sources else "",
                            head, "".join(body), _timeline_svg(runs), "".join(strips),
                            "".join("<li>%s</li>" % html.escape(x) for x in inc) or "<li>none</li>",
                            "".join(gates), "".join(detail))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(doc, encoding="utf-8")


#: the hand measurements of 23 Nov the report draws as bands (metric -> (lo, hi, what))
HAND_20261123 = {
    "walkout_box_m": (1.004, 1.009, "walk-out measured by hand"),
    "hit_to_walker_s": (1.29, 1.39, "hit→wheels measured by hand"),
    "arrival_err_m": (0.07, 0.11, "counter arrival measured by hand"),
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--turnlog", help="robot turnlog jsonl (date from its name or --date)")
    ap.add_argument("--date", help="YYYY-MM-DD of the turnlog when its name has none")
    ap.add_argument("--board", action="append", default=[], help="board pose log(s), globs ok; repeatable")
    ap.add_argument("--walkframes", action="append", default=[], help="walk-frames run folder(s), globs ok")
    ap.add_argument("--state", action="append", default=[], help="staff-page state poll jsonl(s), globs ok")
    ap.add_argument("--since", help="only presses at/after HH:MM:SS (Thai, the turnlog's date) or an epoch")
    ap.add_argument("--until", help="only presses at/before HH:MM:SS or an epoch")
    ap.add_argument("--kind", choices=("event", "ceremony"), action="append")
    ap.add_argument("--out-dir", default=str(ROOT / ".tmp" / "card1-bench"))
    ap.add_argument("--name", default=None, help="output base name (default card1_<date>)")
    ap.add_argument("--hand", choices=("20261123", "none"), default="none",
                    help="draw these hand measurements as bands and flag runs outside them")
    ap.add_argument("--json", action="store_true", help="print the runs as JSON lines to stdout too")
    a = ap.parse_args(argv)

    day = _dt.date.fromisoformat(a.date) if a.date else (_date_from_name(a.turnlog) if a.turnlog else None)

    def when(s):
        if not s:
            return None
        if re.fullmatch(r"\d+(\.\d+)?", s):
            return float(s)
        if day is None:
            raise SystemExit("--since/--until HH:MM:SS needs a dated turnlog or --date")
        return _hms_epoch(day, s if s.count(":") == 2 else s + ":00")

    runs = extract_runs(a.turnlog, boardlogs=a.board, walkframes=a.walkframes, statelogs=a.state, date=day,
                        since=when(a.since), until=when(a.until), kinds=set(a.kind) if a.kind else None)
    name = a.name or "card1_%s" % (day.strftime("%Y%m%d") if day else "runs")
    out = Path(a.out_dir)
    hand = HAND_20261123 if a.hand == "20261123" else None
    srcs = [("turnlog %s" % Path(a.turnlog).name) if a.turnlog else None,
            "%d board log(s)" % len(_expand(a.board)), "%d walk-frames folder(s)" % len(_expand(a.walkframes)),
            "%d state log(s)" % len(_expand(a.state))]
    write_jsonl(runs, out / (name + ".jsonl"))
    write_markdown(runs, out / (name + ".md"), title="Card 1 runs — %s" % name, hand=hand)
    write_html(runs, out / (name + ".html"), title="Card 1 runs — %s" % name, hand=hand,
               sources=[s for s in srcs if s])
    if a.json:
        for r in runs:
            print(json.dumps(r, ensure_ascii=False))
    print("%d run(s) -> %s.{jsonl,md,html}" % (len(runs), out / name), file=sys.stderr)
    for r in runs:
        bad = check_run(r)
        print("  %s %-8s ended=%-12s walk-out %s m  hit->wheels %s s  arrival %s m%s" % (
            r.get("press_th"), r.get("kind"), r.get("ended_why"), _fmt(r.get("walkout_box_m"), "m"),
            _fmt(r.get("hit_to_wheels_s")), _fmt(r.get("arrival_err_m"), "m"),
            ("  GATES: " + "; ".join(bad)) if bad else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
