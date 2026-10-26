"""What a run of scripts/drive_route.py did, read back from its log
(data/robot/runs/*.jsonl): where the time went, what held or stopped it,
how close it came in each doorway, how far odometry had drifted at each
checkpoint -- and what keeps coming back across runs, said as a change to
make. drive_route prints this at the end of every run (and writes it next to
the log as .summary.json); by hand:

    python scripts/analyze_run.py               # the latest run
    python scripts/analyze_run.py --last 4      # the latest four, together
    python scripts/analyze_run.py data/robot/runs/20261007-154849_floor2_bigroom_roundtrip.jsonl

Why it exists (owner, 7 ต.ค.: "ทำให้ระบบวิเคราะห์ทำงานให้ดีที่สุด"): every
fix that day came from reading these logs by hand -- the 15 s crawl past D8
(0.061 sent, 6 mm/s moved), the 6 cm step at D8 from the corridor on every
run (the lane 5 cm off the measured passage), the dock check that found no
wall. Each was in the numbers before anyone looked.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "data" / "robot" / "runs"

#: what counts as worth saying
DRIFT_FLAG_M = 0.10            # a checkpoint moved the pose this much
DRIFT_FLAG_DEG = 3.0
CLOSE_FLAG_M = 0.02            # a side this close in a doorway
SLOW_DOOR_RATIO = 1.3          # a door this much slower than its usual
CRAWL_SPEED = 0.015            # m/s moved while sending at least the creep speed
CREEP = 0.059
REPEAT_MIN = 2                 # a step across this many times at one door, the same way
DONE_KINDS = ("route_done", "leg_done")


def load(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def _door_crawl(e: dict) -> float:
    """Seconds a door pass sent at least the creep speed and moved under
    CRAWL_SPEED, over 1 s windows (the 15:50 crawl past D8)."""
    tr = [t for t in e.get("trace") or [] if isinstance(t, (list, tuple)) and len(t) > 9]
    if len(tr) < 12:
        return 0.0
    yaw = math.radians(tr[0][3])
    along = [(t[0], t[1] * math.cos(yaw) + t[2] * math.sin(yaw), t[9]) for t in tr]
    crawl, i = 0.0, 0
    for j in range(len(along)):
        while along[j][0] - along[i][0] > 1.0:
            i += 1
        span = along[j][0] - along[i][0]
        if span >= 0.9 and min(a[2] for a in along[i:j + 1]) >= CREEP:
            if (along[j][1] - along[i][1]) / span < CRAWL_SPEED:
                crawl += along[j][0] - along[j - 1][0]
    return crawl


def _held(e: dict) -> float:
    return sum(0.1 for t in e.get("trace") or [] if t == "held beside")


def _slowed(e: dict) -> tuple[float, float | None]:
    """A lane's seconds held under its own speed by something in the strip
    ahead (trace column 10: "front" the taper, "stop" the stop it can make --
    drive_route since 9 ต.ค.), and the least free run ahead of the front face
    then (column 9). Older logs have neither: (0, None)."""
    rows = [t for t in e.get("trace") or [] if isinstance(t, (list, tuple)) and len(t) > 10]
    secs, least = 0.0, None
    for a, b in zip(rows, rows[1:]):
        if a[10] in ("front", "stop"):
            secs += max(0.0, b[7] - a[7])
            if a[9] is not None:
                least = a[9] if least is None else min(least, a[9])
    return secs, least


def _stop_kind(reason: str) -> str:
    r = reason.lower()
    if "no opening" in r:
        return "door shut / someone in the doorway"
    if "dock" in r or "charger" in r:
        return "docking"
    if "link" in r:
        return "link to the robot"
    if "held" in r or "something" in r or "beside" in r:
        return "something in the way"
    if "not going in" in r:
        # "gap 0.760 m leaves 3.0 cm a side" read as a side stop (8 ต.ค. 11:30)
        return "doorway too narrow to go in"
    if re.search(r"\bside -?\d", r):
        # "-- side 0.008 m"; not "steps sideways" / "sideways onto D8's line" (reviewer)
        return "doorway side stop"
    if "forbidden" in r:
        return "forbidden area"
    return "other"


def summarize(events: list[dict], history: list[list[dict]] | None = None) -> dict:
    """The numbers of one run (or several logs of one round, concatenated)."""
    s: dict = {"steps": [], "doors": [], "stops": [], "checkpoints": [], "notes": []}
    ts = [e["t"] for e in events if "t" in e]
    s["seconds"] = round(ts[-1] - ts[0], 1) if len(ts) > 1 else 0.0
    marks = [e for e in events if e.get("kind") == "step"]
    for k, m in enumerate(marks):
        end = marks[k + 1]["t"] if k + 1 < len(marks) else ts[-1]
        s["steps"].append({"i": m["i"], "do": m["do"], "label": m["label"], "seconds": round(end - m["t"], 1)})
    cat = defaultdict(float)
    for e in events:
        k = e.get("kind")
        if k == "turn":
            cat["spot turns"] += e.get("seconds") or 0.0
        elif k == "drive":
            cat["lanes"] += e.get("seconds") or 0.0
        elif k == "door":
            cat["door passes"] += e.get("seconds") or 0.0
    s["by_kind"] = {k: round(v, 1) for k, v in cat.items()}
    turns = [e for e in events if e.get("kind") == "turn"]
    s["turns"] = {"n": len(turns), "seconds": round(sum(e.get("seconds") or 0 for e in turns), 1),
                  "more_than_one_go": sum(1 for e in turns if (e.get("goes") or 1) > 1)}
    # steps across: from the aim that asked for it to the step's end
    across, last_aim = [], None
    for e in events:
        if e.get("kind") == "door_aim":
            last_aim = e
        elif e.get("kind") in ("door", "door_block"):
            last_aim = None                 # a step after a block was not asked for by an aim
        elif e.get("kind") == "door_step_across":
            shift = None
            if last_aim is not None and e.get("to"):
                shift = [round(e["to"][0] - last_aim["pose"][0], 3), round(e["to"][1] - last_aim["pose"][1], 3)]
            across.append({"door": e["door"], "off": e["off"], "shift": shift,
                           "heading": None if last_aim is None else round(math.degrees(last_aim["pose"][2])) % 360,
                           "seconds": None if last_aim is None else round(e["t"] - last_aim["t"], 1)})
    s["steps_across"] = across
    for e in events:
        k = e.get("kind")
        if k == "door":
            s["doors"].append({"door": e["door"], "ok": e.get("ok"), "seconds": e.get("seconds"),
                               "link_lost": bool(e.get("lost_link")),
                               "closest": [round(c, 3) for c in e.get("closest") or []],
                               "crawl_s": round(_door_crawl(e), 1),
                               "kick": max((t[14] for t in e.get("trace") or []
                                            if isinstance(t, (list, tuple)) and len(t) > 14), default=0.0),
                               "reason": e.get("reason")})
        elif k == "checkpoint":
            s["checkpoints"].append({"label": e.get("label"), "ok": e.get("accepted"),
                                     "dx": e.get("dx"), "dy": e.get("dy"),
                                     "dyaw_deg": None if e.get("dyaw") is None else round(math.degrees(e["dyaw"]), 1),
                                     "reason": e.get("reason") or None})
        elif k == "stop":
            s["stops"].append({"reason": e["reason"], "kind": _stop_kind(e["reason"])})
        elif k in ("dock_check", "back_into_dock", "dock"):
            s.setdefault("dock", []).append({kk: v for kk, v in e.items()
                                             if kk not in ("trace", "points", "matches", "t", "state", "turn")})
        elif k == "drive" and (e.get("held_short") or _held(e) or e.get("stopped")):
            s.setdefault("lane_holds", []).append({"label": e.get("label"), "held_beside_s": round(_held(e), 1),
                                                   "held_short": e.get("held_short"), "stopped": e.get("stopped")})
        elif k in ("door_step_blocked", "door_not_found", "door_block", "back_off"):
            s.setdefault("recoveries", []).append({kk: v for kk, v in e.items() if kk not in ("points", "t")})
        elif k == "dock_gap":
            s.setdefault("dock_gap", []).append({"where": e.get("where"), "found": e.get("fix") is not None,
                                                 "why": e.get("why") or e.get("skipped"), "in_path": e.get("in_path")})
        elif k == "turn_skipped":
            s["turns_skipped"] = s.get("turns_skipped", 0) + 1
        if k == "drive":
            secs, least = _slowed(e)
            if secs > 0:
                s.setdefault("slowed_ahead", []).append({"label": e.get("label"), "seconds": round(secs, 1),
                                                         "least_free_m": least})
    # our lidar map, matched at every checkpoint for the log only (drive_route
    # shadow_fix, 19 ต.ค.): how far from the walls on the axes they measured,
    # what it says on the axes they did not, and what it costs
    shadow = [e for e in events if e.get("kind") == "shadow_fix"]
    if shadow:
        ok = [e for e in shadow if not e.get("error")]
        vs = defaultdict(list)
        for e in ok:
            for ax, v in (e.get("vs_walls") or {}).items():
                vs[ax].append(abs(v))
        s["shadow"] = {"n": len(shadow), "errors": len(shadow) - len(ok),
                       "vs_walls_max": {ax: round(max(v) * (100 if ax.endswith("_m") else 1), 1)
                                        for ax, v in vs.items()},
                       "vs_walls_n": {ax: len(v) for ax, v in vs.items()},
                       "only_map": sum(1 for e in ok for ax, key in ((0, "x_m"), (1, "y_m"))
                                       if (e.get("pinned") or [False, False])[ax] and key not in (e.get("vs_walls") or {})),
                       "seconds_max": max((e.get("seconds") or 0.0 for e in shadow), default=0.0),
                       "score_min": min((e.get("score") or 0.0 for e in ok), default=None)}
    seen = [f for e in events if e.get("kind") == "drive" for f in (e.get("seen") or [])]
    if seen:
        s["frames_on_the_move"] = len(seen)
    rolled = [f for e in events if e.get("kind") == "drive" for f in (e.get("rolling") or [])]
    if rolled:
        taken = [f for f in rolled if f.get("taken")]
        s["rolling"] = {"taken": len(taken), "not_taken": len(rolled) - len(taken),
                        "max_across_cm": round(max((abs(f["lat"]) for f in taken), default=0.0) * 100, 1),
                        "max_turn_deg": max((abs(f["dyaw_deg"]) for f in taken), default=0.0)}
    # A leg (drive_route --to-step, the robot's launcher) ends `leg_done`, even on the
    # route's last step: counted only as `route_done`, every leg read "not finished"
    # (design 2026-10-08 build review #3).
    s["done"] = any(e.get("kind") in DONE_KINDS for e in events)
    s["leg"] = any(e.get("kind") == "leg_done" for e in events)
    s["refused"] = [{"code": e.get("code"), "reason": e.get("reason")} for e in events
                    if e.get("kind") == "refused"]
    # drive_route's own uncaught exception (exit 4, review finding 27): neither a
    # refusal nor a stop on the way -- said as what it is
    s["errors"] = [e.get("reason") for e in events if e.get("kind") == "error"]
    s["notes"] = _notes(s, history or [])
    return s


def _notes(s: dict, history: list[list[dict]]) -> list[str]:
    notes = []
    for rf in s.get("refused") or []:
        # exit 3: preflight said no and nothing moved -- not a stop on the way
        notes.append("REFUSED before moving (%s): %s" % (rf["code"], rf["reason"]))
    for err in s.get("errors") or []:
        notes.append("ERROR (drive_route crashed, exit 4 -- see run.out for the traceback): %s" % err)
    for st in s["stops"]:
        notes.append("STOPPED (%s): %s" % (st["kind"], st["reason"]))
    for d in s["doors"]:
        if d["crawl_s"] >= 2.0:
            notes.append("%s: %.1f s crawling (sent the creep speed, moved < %.1f cm/s)"
                         % (d["door"], d["crawl_s"], CRAWL_SPEED * 100))
        if d["closest"] and min(d["closest"]) < CLOSE_FLAG_M:
            notes.append("%s: a side %.1f cm from the robot" % (d["door"], min(d["closest"]) * 100))
    for c in s["checkpoints"]:
        if not c["ok"]:
            notes.append("checkpoint %r not taken: %s" % (c["label"], c["reason"]))
            continue
        moved = max(abs(c["dx"] or 0.0), abs(c["dy"] or 0.0))
        if moved >= DRIFT_FLAG_M or abs(c["dyaw_deg"] or 0.0) >= DRIFT_FLAG_DEG:
            notes.append("checkpoint %r moved the pose %.0f cm / %.1f° (odometry had drifted that far)"
                         % (c["label"], moved * 100, c["dyaw_deg"] or 0.0))
    ro = s.get("rolling")
    if ro and ro["not_taken"]:
        notes.append("walls on the move: %d of %d not taken (too big, or not matched)"
                     % (ro["not_taken"], ro["taken"] + ro["not_taken"]))
    if s["turns"]["more_than_one_go"]:
        notes.append("%d spot turn(s) needed more than one go" % s["turns"]["more_than_one_go"])
    for g in s.get("dock_gap") or []:
        if not g["found"]:
            notes.append("dock gap not used (%s): %s" % (g["where"], g["why"] or "?"))
    # against earlier runs: doors slower than usual
    usual = defaultdict(list)
    for ev in history:
        for e in ev:
            if e.get("kind") == "door" and e.get("ok") and e.get("seconds"):
                usual[e["door"]].append(e["seconds"])
    for d in s["doors"]:
        past = usual.get(d["door"], [])
        if d["ok"] and len(past) >= 3 and d["seconds"] > SLOW_DOOR_RATIO * statistics.median(past):
            notes.append("%s took %.0f s, usually %.0f s" % (d["door"], d["seconds"], statistics.median(past)))
    # the same step across at the same door, run after run: the lane is off
    same = defaultdict(list)
    for ev in history + [None]:
        rows = s["steps_across"] if ev is None else summarize(ev)["steps_across"]
        seen = set()
        for a in rows:
            key = (a["door"], a["heading"])
            if key in seen:
                continue                    # the first step at a door per run: the second only trims it
            seen.add(key)
            if a["shift"] is not None:
                same[key].append(a["shift"])
    now = {(a["door"], a["heading"]) for a in s["steps_across"]}
    for (door, heading), shifts in same.items():
        if len(shifts) < REPEAT_MIN or (door, heading) not in now:
            continue                        # not this run: already fixed, or not reached
        mx = statistics.median(v[0] for v in shifts)
        my = statistics.median(v[1] for v in shifts)
        agree = all(abs(v[0] - mx) < 0.03 and abs(v[1] - my) < 0.03 for v in shifts)
        if agree and math.hypot(mx, my) >= 0.025:
            notes.append("%s (heading %s°): stepped sideways (%+.2f, %+.2f) m in %d runs -- move the lane "
                         "before it by that, and the step goes" % (door, heading, mx, my, len(shifts)))
    return notes


def report(s: dict, name: str = "") -> str:
    out = ["", "== run summary %s ==" % name,
           "%s, %.0f s (%.1f min)" % (("leg done" if s.get("leg") else "route done") if s["done"]
                                     else "not finished", s["seconds"], s["seconds"] / 60)]
    if s["by_kind"]:
        out.append("time: " + ", ".join("%s %.0f s" % kv for kv in sorted(s["by_kind"].items(), key=lambda kv: -kv[1]))
                   + " | steps across %d (%.0f s)" % (len(s["steps_across"]),
                                                      sum(a["seconds"] or 0 for a in s["steps_across"])))
    t = s["turns"]
    if t["n"]:
        out.append("spot turns: %d, %.0f s (%.1f s each)" % (t["n"], t["seconds"], t["seconds"] / t["n"]))
    for d in s["doors"]:
        out.append("  %-4s %s %5.1f s  closest %s%s%s" % (
            d["door"], "ok  " if d["ok"] else ("LINK" if d.get("link_lost") else "STOP"), d["seconds"] or 0.0,
            "/".join("%.1f" % (c * 100) for c in d["closest"]) + " cm" if d["closest"] else "-",
            "" if not d["crawl_s"] else "  crawl %.1f s" % d["crawl_s"],
            "" if not d["kick"] else "  kick +%.2f" % d["kick"]))
    if s.get("turns_skipped"):
        out.append("spot turns skipped (under what the base turns for): %d" % s["turns_skipped"])
    if s.get("rolling"):
        ro = s["rolling"]
        out.append("walls on the move: %d taken, up to %.1f cm / %.1f deg" % (ro["taken"], ro["max_across_cm"],
                                                                          ro["max_turn_deg"]))
    if s.get("shadow"):
        sh = s["shadow"]
        out.append("lidar map (log only): %d stops%s, vs walls max %s | axes only the map measured: %d | "
                   "up to %.2f s a match%s" % (
                       sh["n"], "" if not sh["errors"] else " (%d errors)" % sh["errors"],
                       ", ".join("%s %.1f%s (n %d)" % (ax[0] if ax.endswith("_m") else "yaw", v,
                                                        " cm" if ax.endswith("_m") else " deg", sh["vs_walls_n"][ax])
                                 for ax, v in sorted(sh["vs_walls_max"].items())) or "-",
                       sh["only_map"], sh["seconds_max"],
                       "" if sh["score_min"] is None else ", lowest score %.2f" % sh["score_min"]))
    if s.get("frames_on_the_move"):
        out.append("lidar frames logged on the move: %d" % s["frames_on_the_move"])
    if s.get("slowed_ahead"):
        sl = s["slowed_ahead"]
        out.append("slowed for something ahead: %.1f s on %d lane(s): %s" % (
            sum(x["seconds"] for x in sl), len(sl),
            "; ".join("%s %.1f s%s" % (x["label"], x["seconds"], "" if x["least_free_m"] is None
                                       else " (%.2f m free at least)" % x["least_free_m"]) for x in sl)))
    slow = sorted(s["steps"], key=lambda st: -st["seconds"])[:5]
    if slow:
        out.append("longest steps: " + "; ".join("%d %s %.0f s" % (st["i"], st["label"], st["seconds"]) for st in slow))
    if s["notes"]:
        out.append("worth a look:")
        out.extend("  - " + n for n in s["notes"])
    else:
        out.append("nothing stands out")
    return "\n".join(out)


def history_before(path: Path, route_stem: str, n: int = 20) -> list[list[dict]]:
    logs = sorted(p for p in RUNS.glob("*_%s.jsonl" % route_stem) if p.name < path.name)
    return [load(p) for p in logs[-n:]]


def analyze(path: Path, write: bool = True) -> str:
    events = load(path)
    stem = path.stem.split("_", 1)[1] if "_" in path.stem else path.stem
    s = summarize(events, history_before(path, stem))
    if write:
        path.with_suffix(".summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    return report(s, path.stem)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("logs", nargs="*", type=Path)
    ap.add_argument("--last", type=int, default=1, help="the latest N run logs, as one")
    args = ap.parse_args()
    paths = args.logs or sorted(RUNS.glob("*.jsonl"))[-args.last:]
    if not paths:
        print("no run logs in %s" % RUNS)
        return 1
    if len(paths) == 1:
        print(analyze(paths[0]))
        return 0
    events = [e for p in paths for e in load(p)]
    stem = paths[-1].stem.split("_", 1)[1] if "_" in paths[-1].stem else paths[-1].stem
    print(report(summarize(events, history_before(paths[0], stem)), "%d logs to %s" % (len(paths), paths[-1].stem)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
