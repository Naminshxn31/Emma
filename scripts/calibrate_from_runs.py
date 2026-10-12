"""What every run of scripts/drive_route.py measured about the doors and the
base's own odometry, put together across runs -- the numbers behind two of
the changes the owner approved on 9 ต.ค. 2026: (1) remember each door's
measured middle and move the lanes so the robot arrives on it (no
turn-drive-turn step sideways), (2) calibrate odometry (distance, and above
all the spot turns) so fewer checkpoint fixes are needed. Read-only over
data/robot/runs/; writes one JSON (data/robot/calibration/floor2-<date>.json,
gitignored like all of data/robot/) and prints a summary:

    python scripts/calibrate_from_runs.py
    python scripts/calibrate_from_runs.py --since 20261008 --no-write

What it trusts, and why (each rule is something the logs showed):

* A door's middle comes from both sides at once -- the jambs beside the robot
  in the frame (the door pass's side readings, every 0.1 s) or both edges
  ahead at the aim. "one edge + drawing" is never a middle: at D8 the edge it
  picks moved 6 cm with a 3 cm change of standpoint (robot_lanes, 7 ต.ค.).
  The two are kept apart: at D8 from the corridor they disagree by 2-3 cm,
  every run.
* Door numbers come from runs since DOOR_FRAME_SINCE: on 8 ต.ค. 09:2x the
  route's y near D11 moved 17 cm (its `_note_d11`) -- a middle measured in
  odometry's frame before that is in another frame.
* A lane is moved by what the aim itself decided -- the first step sideways
  of a door step, run after run -- since that is the step the move is meant
  to save (`analyze_run`'s "move the lane before it by that", with numbers).
* A spot turn's own error comes only from a fix that sees the same walls from
  the same spot and heading as the fix before the turn (out and back, a step
  sideways): between two different checkpoints the fix's dyaw is the turn
  plus how far the two wall sets disagree -- 8 ต.ค. "above D8" facing north
  read ea_east's bowed end +1.6..+4.7° off, and the dock check took it back
  every run; that is not the turns.
* Distance and lane drift come only from two fixes on the move in one lane
  (the second's correction is what accrued since the first, against the same
  walls). A lane's first fix carries where the rooms' walls disagree with
  each other (cor_end against the room, 10-15 cm, 8 ต.ค.).
* Too few samples or too much spread: no constant, and the reason said.
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

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "data" / "robot" / "runs"
ROUTE = ROOT / "data" / "robot" / "routes" / "floor2_bigroom_roundtrip.json"
OUT = ROOT / "data" / "robot" / "calibration"

#: the route's frame near the doors as it is now: D11, mr_north and the lane to
#: D11 moved 17 cm in y on 8 ต.ค. 09:2x; the 09:23 run already measured in the
#: new frame (its D11 middle agrees with every run after it, not with 7 ต.ค.)
DOOR_FRAME_SINCE = "20261008-0920"
#: the evidence a recommendation needs
MIN_N = 3                      # samples, after outliers are dropped
DOOR_MAX_MAD_M = 0.015         # spread (MAD, scaled to a std)
DOOR_MIN_SHIFT_M = 0.01        # below this a lane is left where it is
DOOR_SIGN_AGREE = 0.75         # share of steps that must go the same way
DOOR_STEP_SHARE = 0.5          # a lane is moved for a step that happens at least this often
OUTLIER_K = 3.0                # |x - median| > K * MAD -> dropped (floors below)
DOOR_OUTLIER_FLOOR_M = 0.02
DEG_OUTLIER_FLOOR = 1.0
#: what counts as which motion
SPOT_TURN_MIN_DEG = 1.0        # a "turn" that moved less is no turn (already inside the allowance)
ARC_MIN_DEG = 20.0             # a lane whose heading changed this much rounded a corner
NEAR_DOCK_M = 1.3              # a spot turn this close to the dock pose is "at the dock"
FRAME_WIDTH = (0.72, 0.98)     # both sides beside the robot add up to a doorway, not a corridor
SAME_VIEW_M = 0.25             # two fixes this close, facing within SAME_VIEW_DEG: the same walls
SAME_VIEW_DEG = 10.0
SAME_VIEW_MAX_DRIVE_M = 0.3
#: a fix on the move that matched (even if not applied): its dyaw is a measurement
ACCEPTED_NOT_TAKEN = ("beyond the caps", "more than one frame turns")


# --------------------------------------------------------------------------
# small statistics

def robust(values, floor: float = 0.0, k: float = OUTLIER_K) -> dict:
    """Median and MAD (scaled to a std) after dropping what lies more than
    `k` MADs (at least `floor`) from the median. Fewer than 4 values: nothing
    is dropped -- three points cannot outvote one."""
    v = [float(x) for x in values if x is not None and math.isfinite(x)]
    if not v:
        return {"n": 0, "n_all": 0, "dropped": [], "kept": []}
    med = statistics.median(v)
    mad = 1.4826 * statistics.median(abs(x - med) for x in v)
    kept, dropped = v, []
    if len(v) >= 4:
        lim = max(k * mad, floor)
        kept = [x for x in v if abs(x - med) <= lim]
        dropped = [x for x in v if abs(x - med) > lim]
    med = statistics.median(kept)
    return {"n": len(kept), "n_all": len(v), "median": med,
            "mad": 1.4826 * statistics.median(abs(x - med) for x in kept),
            "mean": statistics.fmean(kept), "min": min(kept), "max": max(kept),
            "dropped": sorted(dropped), "kept": kept}


def boot_ci(values, n: int = 2000, seed: int = 0) -> list[float] | None:
    """95% bootstrap interval of the median (fixed seed: the same logs, the
    same report)."""
    v = np.asarray([x for x in values if x is not None], dtype=float)
    if len(v) < MIN_N:
        return None
    rng = np.random.default_rng(seed)
    s = np.median(v[rng.integers(0, len(v), size=(n, len(v)))], axis=1)
    return [float(np.percentile(s, 2.5)), float(np.percentile(s, 97.5))]


def confidence(n: int, est: float | None, ci: list[float] | None) -> str:
    """insufficient: under MIN_N -- nothing is proposed. low: the 95% interval
    holds zero. medium: it does not. high: and it is no wider than the
    estimate itself, from 8 or more samples."""
    if n < MIN_N or est is None or ci is None:
        return "insufficient"
    if ci[0] <= 0.0 <= ci[1]:
        return "low"
    if n >= 8 and (ci[1] - ci[0]) / 2.0 <= abs(est) * 0.5:
        return "high"
    return "medium"


def ratio_fit(xs, ys, floor: float = 0.0) -> dict:
    """y = k x through the origin (least squares), outliers on y - k x
    dropped once (4+ pairs); bootstrap interval over the pairs."""
    x = np.asarray(xs, dtype=float)
    y = np.asarray(ys, dtype=float)
    if len(x) < MIN_N or not np.any(x):
        return {"n": int(len(x)), "confidence": "insufficient"}
    k = float(np.dot(x, y) / np.dot(x, x))
    res = y - k * x
    keep = np.ones(len(x), bool)
    if len(x) >= 4:
        mad = 1.4826 * float(np.median(np.abs(res - np.median(res))))
        keep = np.abs(res - np.median(res)) <= max(OUTLIER_K * mad, floor, 1e-12)
    x, y = x[keep], y[keep]
    if len(x) < MIN_N or not np.any(x):
        return {"n": int(len(x)), "confidence": "insufficient"}
    k = float(np.dot(x, y) / np.dot(x, x))
    rng = np.random.default_rng(0)
    ks = []
    for _ in range(2000):
        i = rng.integers(0, len(x), len(x))
        den = float(np.dot(x[i], x[i]))
        if den > 0:
            ks.append(float(np.dot(x[i], y[i]) / den))
    ci = [float(np.percentile(ks, 2.5)), float(np.percentile(ks, 97.5))]
    return {"n": int(len(x)), "dropped": int((~keep).sum()), "k": k, "ci95": ci,
            "resid_sd": float(np.std(y - k * x)), "sum_x": float(np.sum(x)),
            "confidence": confidence(int(len(x)), k, ci)}


def _r(v, nd=4):
    return None if v is None else round(float(v), nd)


def wrap_deg(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def stat_out(s: dict, ci: bool = False) -> dict:
    if not s.get("n"):
        return {"n": 0}
    out = {"n": s["n"], "median": _r(s["median"]), "mad": _r(s["mad"]), "min": _r(s["min"]), "max": _r(s["max"])}
    if s.get("dropped"):
        out["dropped"] = [_r(v) for v in s["dropped"]]
    if ci:
        c = boot_ci(s["kept"])
        out["ci95"] = None if c is None else [_r(v) for v in c]
        out["confidence"] = confidence(s["n"], s["median"], c)
    return out


# --------------------------------------------------------------------------
# reading the logs

def load_jsonl(path: Path) -> list[dict]:
    """As analyze_run.load (kept here: that file is in flux under another
    change, and a broken import there must not take this down)."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict):
            out.append(e)
    return out


_TS = re.compile(r"^(\d\d):(\d\d):(\d\d) (.*)$")
_NUM = r"[-+]?\d+(?:\.\d+)?"
_V = r"(?:[-+]?\d+(?:\.\d+)?|—)"


def _f(s: str | None) -> float | None:
    return None if s is None or s == "—" else float(s)


def parse_text(text: str) -> list[dict]:
    """drive_route's printed lines as the jsonl's events, for a leg whose log
    stayed on the robot (20261009 leg 2: only its run.out came back). What
    the page does not print is left out, not guessed: a lane whose walls were
    taken on the move has no heading on the page, so its interval is marked
    and kept out of the heading numbers."""
    ev: list[dict] = []
    pose = None                      # (x, y, yaw rad) as well as the page tells it
    t_day = 0.0
    width = None
    step = None
    for raw in text.splitlines():
        line = raw.strip()
        m = _TS.match(line)
        if m:
            t_day = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            line = m.group(4).strip()
        t = t_day + len(ev) * 1e-3   # strictly increasing within a second
        if (mm := re.search(r"counting (\d\.\d+) m in doorways", line)):
            width = float(mm.group(1))
        if (mm := re.search(r"log \S*/(\d{8}-\d{6}_\S+?)\.jsonl", line)):
            ev.append({"kind": "run_start", "text_log": mm.group(1), "door_width": width, "t": t})
            continue
        if (mm := re.search(r"resuming at step (\d+) from the board's pose \((%s), (%s), (%s)\)" % (_NUM, _NUM, _NUM),
                            line)):
            pose = (float(mm.group(2)), float(mm.group(3)), float(mm.group(4)))
            ev.append({"kind": "resume", "from_step": int(mm.group(1)), "pose": list(pose), "t": t})
            continue
        if (mm := re.match(r"step (\d+)/\d+ (\w+) (.*)$", line)):
            step = {"kind": "step", "i": int(mm.group(1)), "do": mm.group(2), "label": mm.group(3).strip(), "t": t}
            ev.append(step)
            continue
        if (mm := re.match(r"fix at \((%s), (%s), (%s)°\): (OK|REJECTED) dx (%s)(?: m)? dy (%s)(?: m)? dyaw (%s)°?"
                           % (_NUM, _NUM, _NUM, _V, _V, _V), line)):
            gx, gy, gyaw = float(mm.group(1)), float(mm.group(2)), math.radians(float(mm.group(3)))
            dx, dy, dyaw = _f(mm.group(5)), _f(mm.group(6)), _f(mm.group(7))
            ok = mm.group(4) == "OK"
            fixed = (gx + (dx or 0.0), gy + (dy or 0.0), gyaw + math.radians(dyaw or 0.0)) if ok else (gx, gy, gyaw)
            label = None
            if step is not None:
                label = step["label"] if step["do"] == "checkpoint" else "%s line-up" % step["label"]
            ev.append({"kind": "checkpoint", "label": label, "guess": [gx, gy, gyaw], "pose": list(fixed),
                       "accepted": ok, "dx": dx, "dy": dy, "dyaw": None if dyaw is None else math.radians(dyaw),
                       "matches": [], "t": t})
            pose = fixed
            continue
        if (mm := re.match(r"wall (\S+)\s+at (%s) seen (%s)" % (_NUM, _NUM), line)):
            for e in reversed(ev):
                if e["kind"] in ("checkpoint", "dock_check"):
                    e.setdefault("matches", []).append({"anchor": mm.group(1)})
                    break
            continue
        if (mm := re.match(r"dock check: (OK|REJECTED) dx (%s)(?: m)? dy (%s)(?: m)? dyaw (%s)°?" % (_V, _V, _V), line)):
            ok = mm.group(1) == "OK"
            dyaw, dx = _f(mm.group(4)), _f(mm.group(2))
            if ok and pose is not None:
                pose = (pose[0] + (dx or 0.0), pose[1], pose[2] + math.radians(dyaw or 0.0))
            ev.append({"kind": "dock_check", "accepted": ok, "dx": dx, "dyaw_total": dyaw,
                       "pose": None if pose is None else list(pose), "matches": [], "t": t})
            continue
        if (mm := re.match(r"turned to (%s)°, off by (%s)° \((\d+) go" % (_NUM, _NUM), line)):
            to, off = float(mm.group(1)), float(mm.group(2))
            ev.append({"kind": "turn", "mode": "text", "to_deg": to, "off_deg": off, "goes": int(mm.group(3)),
                       "start_deg": None if pose is None else math.degrees(pose[2]), "t": t,
                       "position": None if pose is None else [pose[0], pose[1]]})
            if pose is not None:
                pose = (pose[0], pose[1], math.radians(to - off))
            continue
        if (mm := re.match(r"drive (%s) m to \((%s), (%s)\)" % (_NUM, _NUM, _NUM), line)):
            to = [float(mm.group(2)), float(mm.group(3))]
            if pose is not None:
                # the lane's own direction: the heading the page does not print
                pose = (pose[0], pose[1], math.atan2(to[1] - pose[1], to[0] - pose[0]))
            lab = ""
            if step is not None and step["do"] == "drive":
                lab = re.sub(r"\s*\(on from the corner\)$", "", step["label"])
            ev.append({"kind": "drive", "mode": "text", "label": lab, "to": to,
                       "start": None if pose is None else list(pose), "t": t})
            continue
        if (mm := re.match(r"(%s) m off (D\d+)'s middle: stepping sideways" % _NUM, line)):
            ev.append({"kind": "door_step_across", "door": mm.group(2), "off": float(mm.group(1)), "t": t})
            ev.append({"kind": "_sideways_next", "t": t})
            continue
        if (mm := re.search(r"walls on the way: (\d+) of (\d+) taken", line)):
            for e in reversed(ev):
                if e["kind"] == "drive":
                    e["text_rolling"] = int(mm.group(1))
                    break
            continue
        mm = (re.match(r"ended at \((%s), (%s)\)" % (_NUM, _NUM), line)
              or re.match(r"round the corner into .* at \((%s), (%s)\) on the move" % (_NUM, _NUM), line))
        if mm:
            x, y = float(mm.group(1)), float(mm.group(2))
            for e in reversed(ev):
                if e["kind"] == "drive":
                    e["end"] = [x, y, None if pose is None else pose[2]]
                    if "round the corner" in line:
                        e["corner"] = True
                    break
            pose = (x, y, pose[2] if pose is not None else 0.0)
            continue
        if (mm := re.match(r"(D\d+) ahead at (%s) m: (%s) m wide \((both edges|one edge \+ drawing)\), middle (%s) m"
                           % (_NUM, _NUM, _NUM), line)):
            ev.append({"kind": "door_aim", "door": mm.group(1),
                       "gap": {"face": float(mm.group(2)), "width": float(mm.group(3)),
                               "edges": 2 if mm.group(4) == "both edges" else 1, "centre": float(mm.group(5))},
                       "pose": None if pose is None else list(pose), "t": t})
            continue
        if (mm := re.match(r"through (D\d+) in (%s) s, now at \((%s), (%s)\); closest sides L (%s) R (%s) m "
                           r"\((%s) m width\)" % (_NUM, _NUM, _NUM, _NUM, _NUM, _NUM), line)):
            x, y = float(mm.group(3)), float(mm.group(4))
            ev.append({"kind": "door", "mode": "text", "door": mm.group(1), "ok": True,
                       "seconds": float(mm.group(2)), "end": [x, y, None if pose is None else pose[2]],
                       "closest": [float(mm.group(5)), float(mm.group(6))], "door_width": float(mm.group(7)),
                       "t": t})
            pose = (x, y, pose[2] if pose is not None else 0.0)
            continue
    # the drive right after "stepping sideways" is that step, not a lane
    out, sideways = [], False
    for e in ev:
        if e["kind"] == "_sideways_next":
            sideways = True
            continue
        if e["kind"] == "drive" and sideways:
            e["label"] = "sideways onto the door's line"
            sideways = False
        out.append(e)
    return out


def load_runs(runs_dir: Path = RUNS, since: str | None = None) -> list[dict]:
    """Every *.jsonl, and every *.out page whose run's jsonl is not here (it
    stayed on the robot) -- the jsonl says more than the page."""
    runs, have = [], set()
    for p in sorted(runs_dir.glob("*.jsonl")):
        have.add(p.stem)
        runs.append({"name": p.stem, "source": "jsonl", "events": load_jsonl(p), "stamp": p.stem[:15]})
    for p in sorted(runs_dir.glob("*.out")):
        ev = parse_text(p.read_text(encoding="utf-8", errors="replace"))
        starts = [e for e in ev if e["kind"] == "run_start"]
        name = starts[0]["text_log"] if starts else p.stem
        if name in have:
            continue
        runs.append({"name": name, "source": "text", "events": ev, "stamp": name[:15]})
    if since:
        runs = [r for r in runs if r["stamp"][:len(since)] >= since]
    runs.sort(key=lambda r: r["stamp"])
    return runs


def load_route(path: Path = ROUTE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# doors

def _heading_key(yaw_rad: float) -> int:
    return int(round(wrap_deg(math.degrees(yaw_rad)) / 90.0)) * 90 % 360


def _lat_axis(door: dict | None, heading: int) -> str:
    if door is not None:
        return "x" if door["axis"] == "y" else "y"
    return "x" if heading in (90, 270) else "y"


def _n_comp(heading: int, lat_axis: str) -> int:
    """The robot's left, as a sign along the world axis across the door."""
    h = math.radians(heading)
    return int(round(-math.sin(h))) if lat_axis == "x" else int(round(math.cos(h)))


def _path_len(xy: list[tuple[float, float]]) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(xy, xy[1:]))


def in_frame(door_ev: dict, heading: int, lat_axis: str, robot_width: float | None) -> dict | None:
    """Both jambs beside the robot (the door pass's side readings, the box's
    left and right, every tick): where they put the door's middle in
    odometry's frame, and how wide the passage is. None when the pass logged
    fewer than 3 such ticks."""
    if robot_width is None:
        return None
    nc = _n_comp(heading, lat_axis)
    mids, widths, offs = [], [], []
    for row in door_ev.get("trace") or []:
        if not isinstance(row, (list, tuple)) or len(row) < 9:
            continue
        left, right = row[7], row[8]
        if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
            continue
        w = left + right + robot_width
        if not FRAME_WIDTH[0] <= w <= FRAME_WIDTH[1]:
            continue
        lat = (right - left) / 2.0                # + = the robot left of the middle
        robot_lat = row[1] if lat_axis == "x" else row[2]
        mids.append(robot_lat - lat * nc)
        widths.append(w)
        offs.append(lat)
    if len(mids) < 3:
        return None
    return {"ticks": len(mids), "mid": statistics.median(mids), "width": statistics.median(widths),
            "entry_off": offs[0]}


def door_passes(run: dict, route: dict) -> list[dict]:
    """One row per door pass of one run: where the robot stood at the first
    aim, what the aims and the jambs beside it said the middle was, what the
    aim did about it (steps sideways and their `off`), how close it came."""
    doors = route.get("doors", {})
    robot_width = None
    out: list[dict] = []
    cur: dict | None = None
    last_lane: dict | None = None          # the last drive that was a lane
    fixes: list[dict] = []
    step_i, aims_in_step = None, 0
    for e in run["events"]:
        k = e.get("kind")
        if k == "run_start":
            robot_width = e.get("door_width") or robot_width
            last_lane, fixes = None, []
        elif k == "step":
            step_i, aims_in_step = e.get("i"), 0
        elif k == "drive":
            lab = e.get("label") or ""
            if not lab.startswith(("sideways onto", "out of")) and e.get("to"):
                last_lane, fixes = e, []
        elif k == "checkpoint" and e.get("accepted"):
            fixes.append(e)
        elif k == "door_aim":
            if e.get("pose") is None or not e.get("gap"):
                continue
            pose, g = e["pose"], e["gap"]
            heading = _heading_key(pose[2])
            lat_axis = _lat_axis(doors.get(e["door"]), heading)
            nc = _n_comp(heading, lat_axis)
            robot_lat = pose[0] if lat_axis == "x" else pose[1]
            aim = {"edges": g.get("edges"), "centre": g.get("centre"), "width": g.get("width"), "face": g.get("face"),
                   "robot_lat": robot_lat,
                   "mid": robot_lat + g["centre"] * nc if g.get("edges") == 2 and g.get("centre") is not None else None,
                   "frame_mid": None if g.get("frame_centre") is None else robot_lat + g["frame_centre"] * nc,
                   "frame_width": g.get("frame_width")}
            if cur is None or cur["door"] != e["door"] or cur.get("closed"):
                first = aims_in_step == 0
                ax = 0 if lat_axis == "x" else 1
                lane_to = last_lane["to"][ax] if last_lane is not None and first else None
                lane_end = (last_lane["end"][ax] if lane_to is not None and last_lane.get("end") is not None
                            else None)
                fix_corr = (sum((f.get("dx") if lat_axis == "x" else f.get("dy")) or 0.0 for f in fixes)
                            if lane_to is not None else None)
                cur = {"run": run["name"], "stamp": run["stamp"], "source": run["source"], "door": e["door"],
                       "heading": heading, "lat_axis": lat_axis, "n_comp": nc, "step": step_i,
                       "first_in_step": first, "arrival": robot_lat, "lane_to": lane_to, "lane_end": lane_end,
                       "fix_corr": fix_corr, "aims": [], "steps_across": 0, "first_step_off": None,
                       "steer_in": False, "step_blocked": False, "robot_width": robot_width}
                out.append(cur)
            cur["aims"].append(aim)
            aims_in_step += 1
        elif cur is not None and not cur.get("closed") and k == "door_step_across":
            if cur["steps_across"] == 0:
                cur["first_step_off"] = e.get("off")
            cur["steps_across"] += 1
        elif cur is not None and not cur.get("closed") and k == "door_steer_in":
            cur["steer_in"] = True
        elif cur is not None and not cur.get("closed") and k == "door_step_blocked":
            cur["step_blocked"] = True
        elif k == "door":
            if cur is None or cur["door"] != e.get("door") or cur.get("closed"):
                continue                       # a pass resumed with no aim: nothing to compare
            rw = e.get("door_width") or robot_width
            cur.update(closed=True, ok=bool(e.get("ok")), reason=e.get("reason"), seconds=e.get("seconds"),
                       closest=e.get("closest"), robot_width=rw,
                       frame=in_frame(e, cur["heading"], cur["lat_axis"], rw) if e.get("mode") == "velocity" else None)
            last_lane, fixes = None, []
    return [p for p in out if p.get("closed")]


def route_lanes(route: dict) -> dict:
    """For each door step of the route: the lane whose end puts the robot in
    front of it (the last drive before it, past any checkpoints), or None when
    the robot comes there out of another door."""
    out = {}
    steps = route.get("steps", [])
    for i, st in enumerate(steps):
        if st.get("do") != "door":
            continue
        door = route["doors"].get(st["door"])
        heading = int(st.get("heading", 0)) % 360
        lat_axis = _lat_axis(door, heading)
        lane = None
        for j in range(i - 1, -1, -1):
            if steps[j].get("do") == "checkpoint":
                continue
            if steps[j].get("do") == "drive" and steps[j].get("to"):
                lane = {"step": j + 1, "label": steps[j].get("label"), "coord": lat_axis,
                        "current": steps[j]["to"][0 if lat_axis == "x" else 1]}
            break
        out[(st["door"], heading)] = {"label": st.get("label") or "%s %d" % (st["door"], heading), "lane": lane,
                                      "route_mid": None if door is None else (door["lo"] + door["hi"]) / 2.0,
                                      "lat_axis": lat_axis, "leaf": None if door is None else door.get("leaf")}
    return out


def _leaf_side(leaf: str | None, heading: int, lat_axis: str) -> str | None:
    """Which of the robot's sides the door's leaf is on ('L'/'R'): `leaf` is
    the end of the door (lo/hi along the axis across) it hangs from."""
    if leaf not in ("lo", "hi"):
        return None
    left_is_hi = _n_comp(heading, lat_axis) > 0
    return "L" if (leaf == "hi") == left_is_hi else "R"


def door_report(passes: list[dict], route: dict, since: str = DOOR_FRAME_SINCE) -> dict:
    """Per door and direction: the numbers, then what they support."""
    lanes = route_lanes(route)
    groups = defaultdict(list)
    for p in passes:
        groups[(p["door"], p["heading"])].append(p)
    rep = {}
    for (door, heading), ps_all in sorted(groups.items()):
        info = lanes.get((door, heading), {"label": "%s %d°" % (door, heading), "lane": None, "route_mid": None,
                                           "lat_axis": ps_all[0]["lat_axis"], "leaf": None})
        nc = ps_all[0]["n_comp"]
        ps = [p for p in ps_all if p["stamp"] >= since]
        first = [p for p in ps if p["first_in_step"]]
        beside = robust([p["frame"]["mid"] for p in ps if p.get("frame")], DOOR_OUTLIER_FLOOR_M)
        ahead = robust([p["aims"][0]["mid"] for p in first if p["aims"][0].get("mid") is not None], DOOR_OUTLIER_FLOOR_M)
        frame_ahead = robust([p["aims"][0]["frame_mid"] for p in first if p["aims"][0].get("frame_mid") is not None],
                             DOOR_OUTLIER_FLOOR_M)
        # the same pass, read both ways: ahead (both edges, else the frame's jambs) less beside
        paired = [((p["aims"][0]["mid"] if p["aims"][0].get("mid") is not None else p["aims"][0]["frame_mid"])
                   - p["frame"]["mid"]) for p in first if p.get("frame")
                  and (p["aims"][0].get("mid") is not None or p["aims"][0].get("frame_mid") is not None)]
        arrival = robust([p["arrival"] for p in first], DOOR_OUTLIER_FLOOR_M)
        lane_bias = robust([p["arrival"] - p["lane_to"] for p in first if p.get("lane_to") is not None],
                           DOOR_OUTLIER_FLOOR_M)
        tracking = robust([p["lane_end"] - p["lane_to"] for p in first
                           if p.get("lane_end") is not None and p.get("lane_to") is not None], DOOR_OUTLIER_FLOOR_M)
        fix_before = robust([p["fix_corr"] for p in first if p.get("fix_corr") is not None], DOOR_OUTLIER_FLOOR_M)
        offs = robust([p["first_step_off"] for p in first if p.get("first_step_off") is not None], DOOR_OUTLIER_FLOOR_M)
        entry = robust([p["frame"]["entry_off"] for p in ps if p.get("frame")], DOOR_OUTLIER_FLOOR_M)
        closest = defaultdict(lambda: {"L": [], "R": []})
        for p in ps_all:
            c = p.get("closest")
            if p.get("ok") and c and len(c) == 2 and max(c) < 5:
                w = "%.2f" % p["robot_width"] if p.get("robot_width") else "unknown"
                closest[w]["L"].append(c[0])
                closest[w]["R"].append(c[1])
        clear = {}
        for w, v in sorted(closest.items()):
            sl, sr = robust(v["L"]), robust(v["R"])
            clear[w] = {"n": len(v["L"]), "L": stat_out(sl), "R": stat_out(sr), "min_L": _r(min(v["L"])),
                        "min_R": _r(min(v["R"])),
                        # how far the robot's line would move left (+) for the two medians to
                        # meet: moving left takes from L what it gives R
                        "balance_left_m": _r((sl["median"] - sr["median"]) / 2.0)}
        g = {
            "door": door, "heading_deg": heading, "direction": info["label"], "lat_axis": info["lat_axis"],
            "left_is": "+%s" % info["lat_axis"] if nc > 0 else "-%s" % info["lat_axis"],
            "leaf_side": _leaf_side(info.get("leaf"), heading, info["lat_axis"]),
            "route_mid": info["route_mid"], "window_since": since,
            "passes_all": len(ps_all), "passes": len(ps), "passes_ok": sum(1 for p in ps if p.get("ok")),
            "first_aims": len(first),
            "first_aim_one_edge": sum(1 for p in first if p["aims"][0].get("edges") == 1),
            "stepped_sideways": sum(1 for p in first if p["steps_across"] > 0),
            "steered_in": sum(1 for p in first if p["steer_in"]),
            "step_blocked": sum(1 for p in first if p["step_blocked"]),
            "first_step_off": stat_out(offs),
            "middle_jambs_beside": stat_out(beside),
            "middle_both_edges_ahead": stat_out(ahead),
            "middle_frame_ahead": stat_out(frame_ahead),
            "ahead_minus_beside": stat_out(robust(paired, DOOR_OUTLIER_FLOOR_M)),
            "frame_entry_off_left": stat_out(entry),
            "arrival": stat_out(arrival),
            "arrival_minus_lane_target": stat_out(lane_bias),
            "lane_end_minus_target": stat_out(tracking),
            "fix_before_door": stat_out(fix_before),
            "width_jambs_beside": stat_out(robust([p["frame"]["width"] for p in ps if p.get("frame")], 0.01)),
            "width_both_edges_ahead": stat_out(robust([p["aims"][0]["width"] for p in first
                                                       if p["aims"][0].get("edges") == 2], 0.01)),
            "width_frame_ahead": stat_out(robust([p["aims"][0]["frame_width"] for p in first
                                                  if p["aims"][0].get("frame_width")], 0.01)),
            "closest": clear,
            "per_pass": [{"run": p["run"], "src": p["source"], "first": p["first_in_step"],
                          "in_window": p["stamp"] >= since, "arrival": _r(p["arrival"]),
                          "lane_to": _r(p.get("lane_to")), "first_step_off": _r(p.get("first_step_off")),
                          "steps": p["steps_across"], "aim_edges": p["aims"][0].get("edges"),
                          "mid_ahead": _r(p["aims"][0].get("mid")), "frame_mid_ahead": _r(p["aims"][0].get("frame_mid")),
                          "mid_beside": _r(p["frame"]["mid"]) if p.get("frame") else None,
                          "ok": p.get("ok"), "closest": [_r(c, 3) for c in (p.get("closest") or [])],
                          "robot_width": p.get("robot_width")} for p in ps_all],
        }
        g["recommendation"] = _door_recommendation(g, offs, beside, lane_bias, info, nc, entry, clear)
        rep[g["direction"]] = g
    return rep


def _spread_ok(s: dict) -> str | None:
    if s.get("n", 0) < MIN_N:
        return "%d sample(s), %d needed" % (s.get("n", 0), MIN_N)
    if s["mad"] > DOOR_MAX_MAD_M:
        return "spread %.1f cm (MAD) over %.1f cm allowed" % (s["mad"] * 100, DOOR_MAX_MAD_M * 100)
    return None


def _tight_side(clear: dict) -> tuple[str | None, float]:
    """The side the robot passes closer to (median closest, the robot width
    with the most passes), and by how much closer than the other."""
    if not clear:
        return None, 0.0
    c = max(clear.values(), key=lambda v: v["n"])
    if not c["L"].get("n") or not c["R"].get("n"):
        return None, 0.0
    d = c["R"]["median"] - c["L"]["median"]
    return ("L" if d > 0 else "R"), abs(d)


def _door_recommendation(g: dict, offs: dict, beside: dict, lane_bias: dict, info: dict, nc: int,
                         entry: dict | None = None, clear: dict | None = None) -> dict:
    """The lane: moved by -(the aim's first step) when steps happen in at
    least DOOR_STEP_SHARE of the door's first aims, MIN_N or more of them,
    within DOOR_MAX_MAD_M of each other, DOOR_SIGN_AGREE the same way, and
    DOOR_MIN_SHIFT_M or more -- what the aim keeps doing by hand, done by the
    lane. Cross-checked against the jambs: the middle beside less where the
    lane leaves the robot. The middle to remember: the jambs beside, by the
    same spread rule."""
    rec: dict = {}
    why = _spread_ok(beside)
    tight, by = _tight_side(clear or {})
    if why is None and entry and entry.get("n"):
        # centring on the jambs moves the robot right when it stood left of them (+)
        toward = "R" if entry["median"] > 0 else "L"
        if tight == toward and by >= 0.01:
            # D11: the frame's middle is not the passage's -- the leaf lies past the
            # frame on the side it would move to, and that side is already the close one
            why = ("centring on the jambs would move the robot %.1f cm toward its %s side, already the closer one "
                   "by %.1f cm (the leaf: the frame's middle is not the passage's)"
                   % (abs(entry["median"]) * 100, toward, by * 100))
    if why is None and g["route_mid"] is not None:
        rec["remember_middle"] = {"axis": g["lat_axis"], "at": _r(beside["median"], 3),
                                  "from_route_mid": _r(beside["median"] - g["route_mid"], 3),
                                  "n": beside["n"], "mad": _r(beside["mad"], 3)}
    else:
        rec["remember_middle"] = {"why_not": why or "the route has no such door"}
    lane = info.get("lane")
    if lane is None:
        rec["lane"] = {"why_not": "no lane leads to this door (the robot comes out of another door's pass)"}
        return rec
    base = {"step": lane["step"], "label": lane["label"], "coord": lane["coord"], "current": lane["current"]}
    if beside.get("n", 0) >= MIN_N and lane_bias.get("n", 0) >= MIN_N:
        # where the lane must end for the robot to stand on the jambs' middle
        base["by_jambs"] = _r(beside["median"] - lane_bias["median"], 3)
    share = g["stepped_sideways"] / g["first_aims"] if g["first_aims"] else 0.0
    why = _spread_ok(offs)
    if why is None and share < DOOR_STEP_SHARE:
        why = "stepped sideways at %d of %d first aims" % (g["stepped_sideways"], g["first_aims"])
    if why is None:
        same = sum(1 for v in offs["kept"] if v * offs["median"] > 0) / offs["n"]
        if abs(offs["median"]) < DOOR_MIN_SHIFT_M:
            why = "steps of %+.1f cm: under %.0f mm" % (offs["median"] * 100, DOOR_MIN_SHIFT_M * 1000)
        elif same < DOOR_SIGN_AGREE:
            why = "only %.0f%% of steps the same way" % (same * 100)
    if why:
        rec["lane"] = {**base, "why_not": why}
        return rec
    delta = -offs["median"] * nc               # `off` + = the robot left of the middle: go right by it
    rec["lane"] = {**base, "proposed": _r(lane["current"] + delta, 3), "delta": _r(delta, 3),
                   "from": "first steps sideways", "n": offs["n"], "mad": _r(offs["mad"], 3),
                   "stepped": "%d/%d" % (g["stepped_sideways"], g["first_aims"])}
    return rec


# --------------------------------------------------------------------------
# odometry: heading

def _turn_angle(e: dict) -> tuple[float | None, float | None]:
    """(odometry's angle turned, deg, + = counter-clockwise; start heading).
    From the sampled yaw, unwrapped -- a half turn's direction cannot be told
    from its two ends."""
    if e.get("mode") == "velocity":
        tr = [r for r in e.get("trace") or [] if isinstance(r, (list, tuple)) and len(r) >= 2
              and isinstance(r[1], (int, float))]
        if tr:
            yaws = np.unwrap(np.radians([r[1] for r in tr]))
            return float(np.degrees(yaws[-1] - yaws[0])), tr[0][1]
        return None, None
    if e.get("mode") == "text":
        if e.get("start_deg") is None:
            return None, None
        return wrap_deg(e["to_deg"] - e["off_deg"] - e["start_deg"]), e["start_deg"]
    tr = [r for r in e.get("trace") or [] if isinstance(r, (int, float))]
    if e.get("start") is not None and tr:
        yaws = np.unwrap(np.radians([e["start"]] + tr))
        return float(np.degrees(yaws[-1] - yaws[0])), e["start"]
    return None, None


def _xy_rows(trace, xi=0) -> list[tuple[float, float]]:
    return [(r[xi], r[xi + 1]) for r in trace or [] if isinstance(r, (list, tuple)) and len(r) > xi + 1
            and isinstance(r[xi], (int, float)) and isinstance(r[xi + 1], (int, float))]


def _walls(e: dict) -> frozenset:
    return frozenset(m.get("anchor") for m in e.get("matches") or [] if isinstance(m, dict) and m.get("anchor"))


def _place_turns(run: dict) -> None:
    """A turn's position: the pose last logged before it (a turn logs only
    its yaw). Needed for 'at the dock'."""
    pos = None
    for e in run["events"]:
        if e.get("kind") == "turn":
            if e.get("position") is None and pos is not None:
                e["position"] = list(pos)
            continue
        for key in ("pose", "end"):
            v = e.get(key)
            if isinstance(v, list) and len(v) >= 2 and all(isinstance(c, (int, float)) for c in v[:2]):
                pos = (v[0], v[1])
                break


class _Interval:
    def __init__(self, run: str, meas: dict):
        self.run, self.meas = run, meas
        self.turns: list[dict] = []
        self.dist = 0.0
        self.arc = 0.0
        self.dirty: list[str] = []

    def close(self, meas: dict) -> dict:
        a, b = self.meas, meas
        same = False
        if a.get("pose") is not None and b.get("pose") is not None:
            d = math.hypot(a["pose"][0] - b["pose"][0], a["pose"][1] - b["pose"][1])
            dh = abs(wrap_deg(math.degrees(a["pose"][2] - b["pose"][2])))
            walls_ok = not a["walls"] or not b["walls"] or bool(a["walls"] & b["walls"])
            same = (d <= SAME_VIEW_M and dh <= SAME_VIEW_DEG and walls_ok and self.dist <= SAME_VIEW_MAX_DRIVE_M
                    and a["kind"] != "wall on the move" and b["kind"] != "wall on the move")
        return {"run": self.run, "from": ("%s %s" % (a["kind"], a.get("label") or "")).strip(),
                "to": ("%s %s" % (b["kind"], b.get("label") or "")).strip(),
                "dyaw_deg": b["dyaw_deg"], "turns": self.turns, "dist_m": self.dist, "arc_deg": self.arc,
                "dirty": self.dirty, "same_view": same,
                "a_ccw": sum(tu["deg"] for tu in self.turns if tu["deg"] > 0),
                "a_cw": sum(tu["deg"] for tu in self.turns if tu["deg"] < 0)}


def heading_intervals(run: dict, dock_xy: tuple[float, float] | None) -> list[dict]:
    """The run cut at every heading measurement (a fix with a dyaw, a dock
    check, a wall taken on the move): what moved in between and what the
    measurement then put right. The first measurement of a run only opens
    the first interval (the start heading is the dock's assumed one, or
    wherever a resumed leg stood)."""
    _place_turns(run)
    out: list[dict] = []
    cur: _Interval | None = None

    def measure(dyaw_deg, kind, label, pose, walls):
        nonlocal cur
        m = {"dyaw_deg": dyaw_deg, "kind": kind, "label": label, "pose": pose, "walls": walls}
        if cur is not None and dyaw_deg is not None:
            out.append(cur.close(m))
        cur = _Interval(run["name"], m)

    for e in run["events"]:
        k = e.get("kind")
        if k in ("run_start", "resume"):
            cur = None
        elif k == "checkpoint":
            if e.get("accepted") and e.get("dyaw") is not None:
                measure(math.degrees(e["dyaw"]), "fix", e.get("label"), e.get("pose"), _walls(e))
        elif k == "dock_check":
            if e.get("accepted"):
                d = e.get("dyaw_total")
                if d is None and e.get("dyaw") is not None:
                    d = math.degrees(e["dyaw"])
                measure(d, "dock check", None, e.get("pose"), _walls(e))
        elif cur is None:
            continue
        elif k == "turn":
            deg, start = _turn_angle(e)
            if deg is None:
                cur.dirty.append("turn of unknown angle")
                continue
            if abs(deg) < SPOT_TURN_MIN_DEG:
                continue
            pos = e.get("position")
            near = (dock_xy is not None and pos is not None
                    and math.hypot(pos[0] - dock_xy[0], pos[1] - dock_xy[1]) <= NEAR_DOCK_M)
            cur.turns.append({"deg": deg, "start": start, "near_dock": bool(near)})
        elif k == "drive":
            if e.get("mode") == "text":
                if e.get("text_rolling"):
                    cur.dirty.append("walls taken on the move (not on the page)")
                if e.get("corner"):
                    cur.dirty.append("arc (its angle is not on the page)")
                st, en = e.get("start"), e.get("end")
                if st and en:
                    cur.dist += math.hypot(en[0] - st[0], en[1] - st[1])
                continue
            xy = _xy_rows(e.get("trace"))
            if e.get("end"):
                xy.append((e["end"][0], e["end"][1]))
            total = _path_len(xy)
            yaws = [r[2] for r in e.get("trace") or [] if isinstance(r, (list, tuple)) and len(r) > 2
                    and isinstance(r[2], (int, float))]
            if e.get("end") and len(e["end"]) > 2 and isinstance(e["end"][2], (int, float)):
                yaws.append(math.degrees(e["end"][2]))
            rows = [r for r in e.get("rolling") or [] if isinstance(r, dict)]
            applied = sum(r.get("dyaw_deg") or 0.0 for r in rows if r.get("taken"))
            turned = 0.0
            if len(yaws) >= 2:
                uw = np.degrees(np.unwrap(np.radians(yaws)))
                turned = float(uw[-1] - uw[0]) - applied
            done = 0.0
            for r in rows:
                if not r.get("taken") or r.get("dyaw_deg") is None:
                    continue
                along = max(done, min(float(r.get("along") or 0.0), total))
                cur.dist += along - done
                done = along
                measure(r["dyaw_deg"], "wall on the move", e.get("label"), None, frozenset(r.get("walls") or ()))
            cur.dist += max(0.0, total - done)
            if abs(turned) >= ARC_MIN_DEG:
                cur.arc += turned
        elif k == "door":
            if e.get("mode") == "text":
                cur.dist += 2.4                # aim to the pass's end on this route; the page has no trace
                continue
            cur.dist += _path_len(_xy_rows(e.get("trace"), 1 if e.get("mode") == "velocity" else 0))
        elif k == "move":
            cur.dist += _path_len(_xy_rows(e.get("trace")))
        elif k == "undock":
            cur.dist += float(e.get("moved") or 0.0)
        elif k in ("sidestep", "back_off"):
            cur.dist += abs(float(e.get("moved") or 0.0))
        elif k in ("back_into_dock", "dock"):
            cur.dirty.append("docking")
        elif k in ("link_lost", "stop", "interrupted", "error", "rescue2_turn", "rescue2_push", "rescue_gap"):
            cur.dirty.append(k)
    return out


def turn_groups(intervals: list[dict]) -> dict:
    """Same-view intervals (the fix after the turns sees the walls the fix
    before them saw, from the same spot and heading): the turns' own error,
    no wall bias in it. Out and back (CW then CCW, or the other way) by size;
    one way only (a full turn) apart."""
    groups = defaultdict(list)
    for iv in intervals:
        if not iv["same_view"] or iv["dirty"] or not iv["turns"]:
            continue
        net = sum(t["deg"] for t in iv["turns"])
        gross = sum(abs(t["deg"]) for t in iv["turns"])
        dock = any(t["near_dock"] for t in iv["turns"])
        if abs(net) <= 10.0 and gross >= 20.0:
            half = gross / 2.0
            size = "~35°" if half < 60 else ("~90°" if half < 135 else "~180°")
            key = "out and back %s%s" % (size, " at the dock" if dock else "")
            groups[key].append({"run": iv["run"], "half": half, "dyaw": iv["dyaw_deg"],
                                "order": "CW first" if iv["turns"][0]["deg"] < 0 else "CCW first"})
        elif abs(net) >= 20.0 and all(t["deg"] * net > 0 for t in iv["turns"] if abs(t["deg"]) >= 5):
            key = "one way %s%s" % ("CCW" if net > 0 else "CW", " at the dock" if dock else "")
            groups[key].append({"run": iv["run"], "net": net, "dyaw": iv["dyaw_deg"]})
        else:
            groups["mixed"].append({"run": iv["run"], "net": net, "gross": gross, "dyaw": iv["dyaw_deg"]})
    out = {}
    for key, rows in sorted(groups.items()):
        s = robust([r["dyaw"] for r in rows], DEG_OUTLIER_FLOOR)
        o = stat_out(s, ci=True)
        if key.startswith("out and back"):
            # scale model: dyaw = (k_ccw - k_cw) * half
            f = ratio_fit([r["half"] for r in rows], [r["dyaw"] for r in rows], DEG_OUTLIER_FLOOR)
            o["ccw_minus_cw_per_deg"] = _fit_out(f)
            o["cw_first"] = stat_out(robust([r["dyaw"] for r in rows if r["order"] == "CW first"], DEG_OUTLIER_FLOOR))
            o["ccw_first"] = stat_out(robust([r["dyaw"] for r in rows if r["order"] == "CCW first"],
                                             DEG_OUTLIER_FLOOR))
        elif key.startswith("one way"):
            f = ratio_fit([r["net"] for r in rows], [r["dyaw"] for r in rows], DEG_OUTLIER_FLOOR)
            o["per_deg"] = _fit_out(f)
        o["values"] = [_r(r["dyaw"], 2) for r in rows]
        out[key] = o
    return out


def leg_table(intervals: list[dict], min_n: int = MIN_N) -> list[dict]:
    """What each fix keeps putting right after the same way there, run after
    run (from fix -> to fix): turn + lane + how far the two wall sets
    disagree, together -- the numbers a per-leg allowance would use."""
    legs = defaultdict(list)
    for iv in intervals:
        if iv["dirty"] or iv["same_view"] or not iv["from"].startswith(("fix", "dock")) \
                or not iv["to"].startswith(("fix", "dock")):
            continue
        legs[(iv["from"], iv["to"])].append(iv)
    out = []
    for (a, b), ivs in legs.items():
        if len(ivs) < min_n:
            continue
        s = robust([iv["dyaw_deg"] for iv in ivs], DEG_OUTLIER_FLOOR)
        o = stat_out(s, ci=True)
        o.update({"from": a, "to": b, "turns": [_r(t["deg"], 0) for t in ivs[-1]["turns"]],
                  "dist_m": _r(statistics.median(iv["dist_m"] for iv in ivs), 2),
                  "runs": [iv["run"][:15] for iv in ivs]})
        out.append(o)
    out.sort(key=lambda o: -abs(o["median"]) if o.get("n") else 0)
    return out


def rolling_pairs(run: dict) -> tuple[list[dict], list[dict]]:
    """Two fixes on the move in one lane: what the second found accrued since
    the first (heading per metre; along the lane per metre). A lane's first
    fix is not used: it carries everything since the axis was last measured,
    other rooms' walls included."""
    heads, alongs = [], []
    for e in run["events"]:
        if e.get("kind") != "drive":
            continue
        rows = [r for r in e.get("rolling") or [] if isinstance(r, dict) and r.get("along") is not None]
        to, tr = e.get("to"), _xy_rows(e.get("trace"))
        lane_deg = math.degrees(math.atan2(to[1] - tr[0][1], to[0] - tr[0][0])) if to and tr else None
        prev_h = prev_a = None                    # (along, what of the error was left standing)
        for r in rows:
            matched = r.get("taken") or r.get("why") in ACCEPTED_NOT_TAKEN
            if matched and r.get("dyaw_deg") is not None:
                if prev_h is not None and r["along"] > prev_h[0]:
                    heads.append({"run": run["name"], "lane": e.get("label"), "lane_deg": lane_deg,
                                  "dist": r["along"] - prev_h[0], "dyaw_deg": r["dyaw_deg"] - prev_h[1],
                                  "walls": r.get("walls")})
                prev_h = (r["along"], 0.0 if r.get("taken") else r["dyaw_deg"])
            # an along fix matched but not applied (over ROLL_ALONG_MAX_M, waiting for the
            # next to agree) still says where the robot was: the error stands on
            if matched and r.get("along_fix") is not None:
                if prev_a is not None and r["along"] > prev_a[0]:
                    alongs.append({"run": run["name"], "lane": e.get("label"), "lane_deg": lane_deg,
                                   "dist": r["along"] - prev_a[0], "err": r["along_fix"] - prev_a[1],
                                   "walls": r.get("walls")})
                prev_a = (r["along"], 0.0 if r.get("along_taken") else r["along_fix"])
    return heads, alongs


def along_at_fixes(run: dict) -> list[dict]:
    """The along-the-lane correction a fix made right after a long lane (the
    lane's direction, + = the robot went further than odometry said). Not
    the distance error alone: since that axis was last measured, the walls
    measuring it changed rooms."""
    out, last = [], None
    for e in run["events"]:
        k = e.get("kind")
        if k == "drive" and e.get("to") and e.get("end") and not (e.get("label") or "").startswith("sideways"):
            last = e
        elif k == "checkpoint" and e.get("accepted") and last is not None:
            tr = _xy_rows(last.get("trace"))
            start = tr[0] if tr else last.get("start")
            if start is None:
                last = None
                continue
            dx, dy = last["to"][0] - start[0], last["to"][1] - start[1]
            length = math.hypot(dx, dy)
            if length >= 1.5:
                ax = "x" if abs(dx) >= abs(dy) else "y"
                corr = e.get("dx") if ax == "x" else e.get("dy")
                if corr is not None:
                    sign = math.copysign(1.0, dx if ax == "x" else dy)
                    out.append({"run": run["name"], "lane": last.get("label"), "to": e.get("label"),
                                "length": length, "along_fix": corr * sign})
            last = None
        elif k == "door":
            last = None
    return out


def lanes_agree(fit: dict, pairs: list[dict], key: str, floor: float = 0.0) -> dict:
    """A per-metre constant holds for the base only if lanes say it alike: at
    least two lanes with MIN_N pairs each, the same sign, within a factor
    two. Otherwise the confidence is capped at low -- 9 Oct the dock lane
    (ea_east ahead) said -0.84 %/m and the corridor (cor_end ahead) -0.16 %/m
    with 8 of 9 pairs from one run: a wall ahead's own shape, not the
    wheels."""
    by = defaultdict(list)
    for p in pairs:
        by[p["lane"]].append(p)
    lanes = {}
    for lane, ps in by.items():
        f = ratio_fit([p["dist"] for p in ps], [p[key] for p in ps], floor)
        lanes[lane] = {"n": len(ps), "k": None if "k" not in f else _r(f["k"], 5),
                       "runs": len({p["run"] for p in ps})}
    ks = [v["k"] for v in lanes.values() if v["k"] is not None and v["n"] >= MIN_N]
    out = dict(fit)
    out["by_lane"] = lanes
    if out.get("confidence") in ("medium", "high"):
        why = None
        if len(ks) < 2:
            why = "%d lane(s) with %d+ pairs, 2 needed" % (len(ks), MIN_N)
        elif not (all(k > 0 for k in ks) or all(k < 0 for k in ks)):
            why = "lanes disagree in sign (%s)" % ", ".join("%+.4f" % k for k in ks)
        elif max(abs(k) for k in ks) > 2.0 * min(abs(k) for k in ks):
            why = "lanes disagree more than 2x (%s)" % ", ".join("%+.4f" % k for k in ks)
        if why:
            out["confidence"] = "low"
            out["capped"] = why
    return out


def _fit_out(f: dict) -> dict:
    if "k" not in f:
        return {"n": f.get("n", 0), "confidence": "insufficient"}
    out = {"n": f["n"], "dropped": f["dropped"], "k": _r(f["k"], 5), "ci95": [_r(c, 5) for c in f["ci95"]],
           "resid_sd": _r(f["resid_sd"], 4), "confidence": f["confidence"]}
    for key in ("capped", "by_lane"):
        if key in f:
            out[key] = f[key]
    return out


# --------------------------------------------------------------------------
# the whole thing

def calibrate(runs: list[dict], route: dict, doors_since: str = DOOR_FRAME_SINCE) -> dict:
    dock = route.get("dock_pose")
    dock_xy = (dock[0], dock[1]) if dock else None
    route_name = route.get("name")
    passes, others, intervals, heads, alongs, at_fixes = [], 0, [], [], [], []
    for run in runs:
        rs = [e for e in run["events"] if e.get("kind") == "run_start"]
        this_route = (run["source"] == "text" or any(e.get("route") == route_name for e in rs)
                      or (not rs and "bigroom" in run["name"]))
        ps = door_passes(run, route)
        if this_route:
            passes.extend(ps)
        else:
            others += len(ps)
        intervals.extend(heading_intervals(run, dock_xy))
        h, a = rolling_pairs(run)
        heads.extend(h)
        alongs.extend(a)
        at_fixes.extend(along_at_fixes(run))
    doors = door_report(passes, route, doors_since)

    drift = lanes_agree(ratio_fit([p["dist"] for p in heads], [p["dyaw_deg"] for p in heads], DEG_OUTLIER_FLOOR),
                        heads, "dyaw_deg", DEG_OUTLIER_FLOOR)
    by_dir = {}
    for name, test in (("east", lambda d: -45 <= d < 45), ("north", lambda d: 45 <= d < 135),
                       ("west", lambda d: abs(d) >= 135), ("south", lambda d: -135 <= d < -45)):
        sel = [p for p in heads if p["lane_deg"] is not None and test(p["lane_deg"])]
        by_dir[name] = _fit_out(ratio_fit([p["dist"] for p in sel], [p["dyaw_deg"] for p in sel], DEG_OUTLIER_FLOOR))
        walls = defaultdict(int)
        for p in sel:
            walls["+".join(sorted(set(p.get("walls") or [])))] += 1
        by_dir[name]["walls"] = dict(sorted(walls.items(), key=lambda kv: -kv[1])[:3])
    along = lanes_agree(ratio_fit([p["dist"] for p in alongs], [p["err"] for p in alongs], 0.005), alongs, "err",
                        0.005)
    groups = turn_groups(intervals)
    legs = leg_table(intervals)
    fix_legs = defaultdict(list)
    for f in at_fixes:
        fix_legs["%s -> %s" % (f["lane"], f["to"])].append(f)

    return {
        "format": 1,
        "runs": len(runs), "runs_text": sum(1 for r in runs if r["source"] == "text"),
        "first_run": runs[0]["name"] if runs else None, "last_run": runs[-1]["name"] if runs else None,
        "route": route_name,
        "rules": {
            "min_n": MIN_N, "door_max_mad_m": DOOR_MAX_MAD_M, "door_min_shift_m": DOOR_MIN_SHIFT_M,
            "door_sign_agree": DOOR_SIGN_AGREE, "door_step_share": DOOR_STEP_SHARE, "doors_since": doors_since,
            "outlier": "|x - median| > %.0f MAD (floors 2 cm / 1 deg), with 4+ samples" % OUTLIER_K,
            "middle": "jambs beside (door pass, both sides) and both edges ahead (aim) apart; one edge never",
            "lane": "moved by -(first step sideways) when steps happen at >= half the first aims, n >= 3, MAD <= 1.5 "
                    "cm, 75% the same way, >= 1 cm",
            "turns": "only fixes that see the same walls from the same spot and heading as the fix before the turns",
            "confidence": "insufficient < %d; low: 95%% interval holds 0; medium: it does not; high: and half-width <= "
                          "|estimate|/2 with n >= 8" % MIN_N},
        "doors": doors,
        "door_passes_on_other_routes": others,
        "odometry": {
            "sign": "dyaw + = the truth is more counter-clockwise than odometry; along + = the robot went further "
                    "than odometry says",
            "heading_drift_on_lanes_deg_per_m": {"all": _fit_out(drift), "by_lane_direction": by_dir,
                                                 "pairs": len(heads), "metres": _r(sum(p["dist"] for p in heads), 1)},
            "distance_per_m": {**_fit_out(along), "pairs": len(alongs), "metres": _r(sum(p["dist"] for p in alongs), 2),
                               "per_pair": [{"lane": p["lane"], "dist": _r(p["dist"], 2), "err": _r(p["err"], 3)}
                                            for p in alongs]},
            "along_fix_after_long_lanes": {k: stat_out(robust([f["along_fix"] for f in v], DOOR_OUTLIER_FLOOR_M))
                                           | {"length_m": _r(statistics.median(f["length"] for f in v), 2)}
                                           for k, v in sorted(fix_legs.items()) if len(v) >= 2},
            "spot_turns_same_view": groups,
            "legs": legs,
            "intervals": len(intervals), "clean": sum(1 for iv in intervals if not iv["dirty"]),
            "same_view": sum(1 for iv in intervals if iv["same_view"] and not iv["dirty"]),
        },
        "proposal": _proposal(doors, drift, along, groups, legs, route),
    }


def _proposal(doors: dict, drift: dict, along: dict, groups: dict, legs: list[dict], route: dict | None = None) -> dict:
    """Constants for drive_route, each only from evidence of medium
    confidence or better -- otherwise the neutral value and the reason."""
    out: dict = {"lanes": [], "remembered_middles": []}
    for name, g in doors.items():
        rec = g["recommendation"]
        if "proposed" in rec.get("lane", {}):
            out["lanes"].append({"door": name, **rec["lane"]})
        if "at" in rec.get("remember_middle", {}):
            out["remembered_middles"].append({"door": name, **rec["remember_middle"]})
    for d, key in (("CCW", "TURN_SCALE_CCW"), ("CW", "TURN_SCALE_CW")):
        g = groups.get("one way %s" % d)
        f = (g or {}).get("per_deg") or {"n": 0, "confidence": "insufficient"}
        if f.get("confidence") in ("medium", "high"):
            out[key] = {"value": _r(1.0 / (1.0 + f["k"]), 4), "k": f["k"], "n": f["n"], "confidence": f["confidence"],
                        "means": "true turn = odometry's x (1 %+.4f): ask odometry for wanted x this" % f["k"]}
        else:
            out[key] = {"value": 1.0, "why_not": "%d same-view one-way %s turn(s) away from the dock (%s)"
                        % (f.get("n", 0), d, f.get("confidence"))}
    ob = groups.get("out and back ~90°", {}).get("ccw_minus_cw_per_deg") or {}
    out["turn_asymmetry"] = {"ccw_minus_cw_per_deg": ob.get("k"), "ci95": ob.get("ci95"), "n": ob.get("n", 0),
                             "confidence": ob.get("confidence", "insufficient"),
                             "means": "a CW 90 and a CCW 90 at one spot come back this many deg/deg apart"}
    if along.get("confidence") in ("medium", "high"):
        out["LINEAR_SCALE"] = {"value": _r(1.0 + along["k"], 4), "n": along["n"], "confidence": along["confidence"]}
    else:
        out["LINEAR_SCALE"] = {"value": 1.0, "why_not": "%s (n %s, %s per metre, 95%% %s)%s" % (
            along.get("confidence"), along.get("n", 0), None if along.get("k") is None else "%+.4f" % along["k"],
            None if along.get("ci95") is None else [round(c, 4) for c in along["ci95"]],
            "" if not along.get("capped") else "; " + along["capped"])}
    if drift.get("confidence") in ("medium", "high"):
        out["HEADING_DRIFT_DEG_PER_M"] = {"value": _r(drift["k"], 3), "n": drift["n"], "confidence": drift["confidence"]}
    else:
        out["HEADING_DRIFT_DEG_PER_M"] = {"value": 0.0, "why_not": "%s (n %s, %s deg/m, 95%% %s)%s" % (
            drift.get("confidence"), drift.get("n", 0), None if drift.get("k") is None else "%+.3f" % drift["k"],
            None if drift.get("ci95") is None else [round(c, 3) for c in drift["ci95"]],
            "" if not drift.get("capped") else "; " + drift["capped"])}
    dock = {k: v for k, v in groups.items() if "at the dock" in k}
    out["dock_half_turns"] = {k: {"n": v["n"], "values": v["values"]} for k, v in dock.items()}
    # what a per-leg allowance would leave: the legs that are steady
    here = {"fix %s" % st.get("label") for st in (route or {}).get("steps", []) if st.get("do") == "checkpoint"}
    here.add("dock check")
    steady = [lg for lg in legs if lg.get("n", 0) >= MIN_N and lg["mad"] <= 0.75 and abs(lg["median"]) >= 1.0
              and lg["from"] in here and lg["to"] in here]
    out["steady_legs"] = [{"from": lg["from"], "to": lg["to"], "median_deg": lg["median"], "mad": lg["mad"],
                           "n": lg["n"]} for lg in steady]
    return out


# --------------------------------------------------------------------------
# printing

def _cm(v):
    return "—" if v is None else "%+.1f" % (v * 100)


def _st(s: dict, scale: float = 100.0, unit: str = "cm") -> str:
    if not s.get("n"):
        return "—"
    return "%+.1f %s (MAD %.1f, n %d)" % (s["median"] * scale, unit, s["mad"] * scale, s["n"])


def summary(cal: dict) -> str:
    L = ["", "== calibration from %d runs (%s .. %s; %d read from printed text) ==" % (
        cal["runs"], cal["first_run"], cal["last_run"], cal["runs_text"])]
    L.append("doors (runs since %s; lateral in world metres; 'left' is the robot's left):" % cal["rules"]["doors_since"])
    for name, g in cal["doors"].items():
        L.append("  %s (%s, left = %s%s): %d passes (%d ok; %d before the window), first aims %d (%d one-edge), "
                 "stepped sideways %d" % (
                     name, g["lat_axis"], g["left_is"], "" if not g["leaf_side"] else ", leaf on the %s"
                     % g["leaf_side"], g["passes"], g["passes_ok"], g["passes_all"] - g["passes"], g["first_aims"],
                     g["first_aim_one_edge"], g["stepped_sideways"]))
        for key, what in (("middle_jambs_beside", "jambs beside"), ("middle_both_edges_ahead", "both edges ahead"),
                          ("middle_frame_ahead", "frame jambs ahead")):
            s = g[key]
            if s.get("n"):
                L.append("      middle by %-17s %.3f (MAD %.1f cm, n %d) = %s cm from the route's %.3f" % (
                    what, s["median"], s["mad"] * 100, s["n"], _cm(s["median"] - g["route_mid"])
                    if g["route_mid"] is not None else "—", g["route_mid"] or float("nan")))
        if g["ahead_minus_beside"].get("n"):
            L.append("      the same pass, ahead less beside: %s" % _st(g["ahead_minus_beside"]))
        L.append("      arrival - lane target %s (lane end - target %s; fix before the door %s); first step off %s"
                 % (_st(g["arrival_minus_lane_target"]), _st(g["lane_end_minus_target"]), _st(g["fix_before_door"]),
                    _st(g["first_step_off"])))
        if g["frame_entry_off_left"].get("n"):
            L.append("      at the frame the robot was %s left of the jambs' middle" % _st(g["frame_entry_off_left"]))
        widths = ["%s %.3f" % (w, g[k]["median"]) for k, w in (("width_jambs_beside", "beside"),
                                                                ("width_both_edges_ahead", "edges ahead"),
                                                                ("width_frame_ahead", "frame ahead"))
                  if g[k].get("n")]
        if widths:
            L.append("      width m: " + ", ".join(widths))
        for w, c in g["closest"].items():
            L.append("      closest (%s m robot, n %d): L %.1f (min %.1f)  R %.1f (min %.1f) cm; to even them move "
                     "%s cm left" % (w, c["n"], c["L"]["median"] * 100, c["min_L"] * 100, c["R"]["median"] * 100,
                                     c["min_R"] * 100, _cm(c["balance_left_m"])))
        rec = g["recommendation"]
        ln = rec["lane"]
        if "proposed" in ln:
            L.append("   -> lane step %d %r: %s %.3f -> %.3f (%s cm; steps %s, n %d, MAD %.1f cm)%s" % (
                ln["step"], ln["label"], ln["coord"], ln["current"], ln["proposed"], _cm(ln["delta"]), ln["stepped"],
                ln["n"], ln["mad"] * 100, "" if ln.get("by_jambs") is None else "; by the jambs %.3f" % ln["by_jambs"]))
        else:
            L.append("   -> lane: no change -- %s%s" % (ln["why_not"], "" if ln.get("by_jambs") is None
                                                       else " (by the jambs it would end at %.3f)" % ln["by_jambs"]))
        rm = rec["remember_middle"]
        L.append("   -> middle to remember: %s" % ("%s %.3f (%s cm from the route's door, n %d, MAD %.1f cm)" % (
            rm["axis"], rm["at"], _cm(rm["from_route_mid"]), rm["n"], rm["mad"] * 100) if "at" in rm
            else "none -- %s" % rm["why_not"]))
    o = cal["odometry"]
    L.append("odometry (%s):" % o["sign"])
    d = o["distance_per_m"]
    L.append("  distance, two along-the-lane fixes on one wall: %s per m, 95%% %s, n %d over %.2f m (%s%s)" % (
        "—" if d.get("k") is None else "%+.4f" % d["k"], d.get("ci95"), d["n"], d["metres"] or 0, d["confidence"],
        "" if not d.get("capped") else ": " + d["capped"]))
    for lane, v in (d.get("by_lane") or {}).items():
        L.append("      lane %-28s %s per m, %d pairs from %d run(s)" % (
            lane[:28], "—" if v["k"] is None else "%+.4f" % v["k"], v["n"], v["runs"]))
    for k, v in o["along_fix_after_long_lanes"].items():
        L.append("      a fix after %-46s %s (lane %.1f m) -- walls of another room, not the scale" % (
            k[:46], _st(v), v["length_m"]))
    hd = o["heading_drift_on_lanes_deg_per_m"]
    a = hd["all"]
    L.append("  heading drift on lanes, two fixes on the move: %s deg/m, 95%% %s, n %d over %.1f m (%s%s)" % (
        "—" if a.get("k") is None else "%+.3f" % a["k"], a.get("ci95"), a["n"], hd["metres"] or 0, a["confidence"],
        "" if not a.get("capped") else ": " + a["capped"]))
    for k, v in hd["by_lane_direction"].items():
        if v.get("k") is not None:
            L.append("      %-5s %+.3f deg/m 95%% %s n %d (%s; walls %s)" % (k, v["k"], v["ci95"], v["n"],
                                                                     v["confidence"], v.get("walls")))
    L.append("  spot turns, the same walls seen before and after (%d intervals):" % o["same_view"])
    for k, v in o["spot_turns_same_view"].items():
        if not v.get("n"):
            continue
        extra = ""
        if "ccw_minus_cw_per_deg" in v and v["ccw_minus_cw_per_deg"].get("k") is not None:
            extra = "; CCW-CW %+.4f/deg" % v["ccw_minus_cw_per_deg"]["k"]
        L.append("      %-26s n %2d  dyaw median %+.2f  MAD %.2f deg  95%% %s (%s)%s" % (
            k, v["n"], v["median"], v["mad"], v.get("ci95"), v.get("confidence"), extra))
    L.append("  legs, fix to fix (turn + lane + the two wall sets' disagreement), the largest steady ones:")
    for lg in o["legs"][:8]:
        L.append("      %-34s -> %-34s %+.2f deg (MAD %.2f, n %d) turns %s, %.1f m" % (
            lg["from"][:34], lg["to"][:34], lg["median"], lg["mad"], lg["n"], lg["turns"], lg["dist_m"]))
    p = cal["proposal"]
    L.append("proposal:")
    for key in ("TURN_SCALE_CCW", "TURN_SCALE_CW", "LINEAR_SCALE", "HEADING_DRIFT_DEG_PER_M"):
        v = p[key]
        L.append("  %-24s %s%s" % (key, v["value"], "" if "why_not" not in v else "  (no change: %s)" % v["why_not"]))
    ta = p["turn_asymmetry"]
    L.append("  %-24s %s (95%% %s, n %d, %s)" % ("turn asymmetry CCW-CW", ta["ccw_minus_cw_per_deg"], ta["ci95"], ta["n"],
                                                  ta["confidence"]))
    for k, v in p["dock_half_turns"].items():
        L.append("  dock %-22s n %d: %s deg" % (k, v["n"], v["values"]))
    for ln in p["lanes"]:
        L.append("  lane step %d (%s) %s: %.3f -> %.3f" % (ln["step"], ln["door"], ln["coord"], ln["current"],
                                                           ln["proposed"]))
    for rm in p["remembered_middles"]:
        L.append("  remember %s middle: %s %.3f" % (rm["door"], rm["axis"], rm["at"]))
    for lg in p["steady_legs"]:
        L.append("  steady leg %s -> %s: %+.2f deg (n %d)" % (lg["from"], lg["to"], lg["median_deg"], lg["n"]))
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", type=Path, default=RUNS)
    ap.add_argument("--route", type=Path, default=ROUTE)
    ap.add_argument("--since", help="only runs from this stamp on (e.g. 20261008 or 20261008-0930)")
    ap.add_argument("--doors-since", default=DOOR_FRAME_SINCE,
                    help="door numbers from runs since this stamp (the route's frame; default %(default)s)")
    ap.add_argument("--out", type=Path, help="the JSON to write (default data/robot/calibration/<route>-<date>.json)")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)
    runs = load_runs(args.runs, args.since)
    if not runs:
        print("no run logs in %s" % args.runs)
        return 1
    cal = calibrate(runs, load_route(args.route), args.doors_since)
    print(summary(cal))
    if not args.no_write:
        out = args.out or OUT / ("%s-%s.json" % (args.route.stem.split("_")[0], runs[-1]["stamp"][:8]))
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".tmp")
        tmp.write_text(json.dumps(cal, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(out)
        print("\nwritten %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
