"""Can the robot tell where it is from its lidar? Measured on the runs it logged.

    python scripts/eval_scan_localization.py
    python scripts/eval_scan_localization.py --plan-walls "C:/Users/Name/Downloads/Embassy_HeadOffice_RobotMap/Embassy_HeadOffice_Floor2_walls.csv"
    python scripts/eval_scan_localization.py --save-field data/robot/maps/floor2_lidar_field.npz [--no-eval]

Offline only: reads data/robot/runs/*.jsonl (every drive_route checkpoint =
one lidar frame + the wheels' guess + the anchor-fixed pose) and the route
files for today's anchor values. Nothing is sent to the robot.

How it is judged (app/scan_match.py says why the map is ours, not the plan):

* **Leave one day out.** Each day's frames are matched against a map built
  from the OTHER days only -- furniture moved between days stays in the test.
* **Truth = the route's own fix, re-run with TODAY's anchors**, on the axes an
  anchor measured. The logged fix is not used as-is: until 8 ต.ค. mr_north and
  D11 were 17 cm off in y (CHANGELOG 8 ต.ค.), and the first round of this
  evaluation "missed" by 14-19 cm exactly there -- the map was right and the
  reference wrong. Anchors still at plan values (cor_west, ea_south_d8, ...)
  can be a few cm off too; the misses that remain sit mostly on them.
* **The axis no anchor measured** has no truth in the log, so two indirect
  checks: (D) the docked robot's y, compared with what the first stop that
  DID measure y implies after the wheels carried it there; (E) every leg from
  a stop that did not measure an axis to one that did, carried by odometry.
* **Started off on purpose**: four guesses 0.25 m + 7° off, to see whether
  the answer comes back or lands somewhere else.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import robot_lanes as rl  # noqa: E402
from app import scan_match as sm  # noqa: E402

PERTURB = [(0.25, 0.25, 7), (-0.25, 0.25, -7), (0.25, -0.25, -7), (-0.25, -0.25, 7)]


@dataclass
class Frame:
    run: str
    i: int
    label: str
    guess: np.ndarray
    logged: np.ndarray
    pts: np.ndarray
    raw: dict
    pose: np.ndarray | None = None              # today's anchors
    meas: tuple[bool, bool, bool] = (False, False, False)

    @property
    def key(self):
        return (self.run, self.i)

    @property
    def day(self):
        return self.run[:8]


def load_frames(runs_dir: Path) -> list[Frame]:
    out = []
    for f in sorted(glob.glob(str(runs_dir / "*.jsonl"))):
        run = os.path.basename(f)[:-6]
        k = 0
        for line in open(f, encoding="utf-8"):
            try:
                d = json.loads(line)
            except ValueError:
                continue                 # a run killed mid-write leaves half a line
            if d.get("kind") != "checkpoint" or not d.get("scan") or not d.get("accepted"):
                continue
            out.append(Frame(run, k, d.get("label", ""), np.asarray(d["guess"], float),
                             np.asarray(d["pose"], float), sm.scan_points(d["scan"]["angle"], d["scan"]["distance"]), d))
            k += 1
    return out


def todays_anchors(routes_dir: Path):
    """Every route file's anchors and doors, the newest file's value winning."""
    anchors, doors = {}, {}
    for p in sorted(routes_dir.glob("*.json"), key=lambda p: p.stat().st_mtime):
        d = json.loads(p.read_text(encoding="utf-8"))
        for n, a in (d.get("anchors") or {}).items():
            try:
                anchors[n] = rl.Anchor(**{k: v for k, v in a.items() if not k.startswith("_")})
            except (TypeError, ValueError):
                pass
        for n, a in (d.get("doors") or {}).items():
            try:
                doors[n] = rl.Door(**{k: v for k, v in a.items() if not k.startswith("_")})
            except (TypeError, ValueError):
                pass
    return anchors, doors


def frame_meta(routes_dir: Path) -> dict:
    """The walls and doors a map is built on, as drive_route checks them
    (load_shadow_field): [axis, at, lo, hi] by name."""
    A, D = todays_anchors(routes_dir)
    return {"anchors": {n: [a.axis, a.at, a.lo, a.hi] for n, a in sorted(A.items())},
            "doors": {n: [d.axis, d.at, d.lo, d.hi] for n, d in sorted(D.items())}}


def retruth(frames: list[Frame], routes_dir: Path) -> list[Frame]:
    """Re-run each stop's fix with the walls and doors it matched then, at
    today's values. A stop today's anchors reject drops out."""
    A, D = todays_anchors(routes_dir)
    kept = []
    for f in frames:
        names = {m["anchor"] for m in f.raw.get("matches") or []}
        dnames = {x.get("door") for x in f.raw.get("doors") or []}
        fix = rl.correct_pose(tuple(f.guess), f.pts, {n: A[n] for n in names if n in A},
                              {n: D[n] for n in dnames if n in D})
        if not fix.accepted:
            continue
        f.pose = np.asarray(fix.pose, float)
        f.meas = (fix.dx is not None, fix.dy is not None, fix.dyaw is not None)
        kept.append(f)
    return kept


def area(p) -> str:
    """Floor-2 head office, for the tables only."""
    x, y = p[0], p[1]
    if y < -1.68:
        return "corridor_south"
    if x < -1.8:
        return "fayarkarn"
    if x > 3.5:
        return "big_room"
    return "corridor_west"


def plan_occupancy(grid: sm.Grid, csv_path: str) -> np.ndarray:
    W = np.loadtxt(csv_path, delimiter=",", skiprows=1, encoding="utf-8-sig").reshape(-1, 4)
    occ = np.zeros((grid.ny, grid.nx), bool)
    for x1, y1, x2, y2 in W:
        n = int(math.hypot(x2 - x1, y2 - y1) / (grid.res / 2)) + 2
        t = np.linspace(0, 1, n)
        r, c = grid.cells(np.c_[x1 + (x2 - x1) * t, y1 + (y2 - y1) * t])
        ok = (r >= 0) & (r < grid.ny) & (c >= 0) & (c < grid.nx)
        occ[r[ok], c[ok]] = True
    return occ


def lidar_field(grid: sm.Grid, train: list[Frame]):
    poses = {f.key: f.pose for f in train}
    meas = {f.key: f.meas for f in train}
    frames = {f.key: (f.run, f.pts) for f in train}
    refined = sm.refine(frames, poses, meas, grid)
    occ = sm.occupancy(grid, [(f.run, f.pts, refined[f.key]) for f in train])
    return sm.Field.from_occupancy(grid, occ), occ, refined


def wrapd(a: float) -> float:
    return math.degrees((a + math.pi) % (2 * math.pi) - math.pi)


def evaluate(frames: list[Frame], grid: sm.Grid, plan_field: sm.Field | None, log=print) -> list[dict]:
    rows = []
    t0 = time.time()
    for day in sorted({f.day for f in frames}):
        train = [f for f in frames if f.day != day]
        test = [f for f in frames if f.day == day]
        field, _, _ = lidar_field(grid, train)
        log("  %s: %3d frames against a map of %3d from other days (%.0f s)" % (day, len(test), len(train), time.time() - t0))
        for f in test:
            small = sm.voxel(f.pts)
            row = dict(run=f.run, i=f.i, label=f.label, area=area(f.pose), guess=f.guess.tolist(),
                       pose=f.pose.tolist(), meas=list(f.meas), maps={})
            for name, fd in (("lidar", field), ("plan", plan_field)):
                if fd is None:
                    continue
                m = sm.search(fd, small, f.guess)
                pert = [sm.search(fd, small, f.guess + np.array([dx, dy, math.radians(dt)])).pose.tolist()
                        for dx, dy, dt in PERTURB]
                row["maps"][name] = dict(est=m.pose.tolist(), score=m.score, sharp=list(m.sharp), pert=pert)
            rows.append(row)
    return rows


def _st(v, k=100.0):
    v = np.abs(np.asarray(v, float)) * k
    if not len(v):
        return "        -           "
    return "n%3d med %4.1f p90 %4.1f max %4.1f" % (len(v), np.median(v), np.percentile(v, 90), v.max())


def report(rows: list[dict], frames: list[Frame], runs_dir: Path, sharp_min: float = sm.SHARP_MIN) -> dict:
    srcs = ["odometry"] + [s for s in ("plan", "lidar") if rows and s in rows[0]["maps"]]
    areas = ("fayarkarn", "corridor_south", "corridor_west", "big_room")
    est = lambda r, s: np.array(r["guess"]) if s == "odometry" else np.array(r["maps"][s]["est"])
    summary: dict = {"frames": len(rows), "errors": {}, "sharp_min": sharp_min}

    print("\nA. against the fix with today's anchors, measured axes only (cm / deg)")
    for a in ("ALL",) + areas:
        R = [r for r in rows if a == "ALL" or r["area"] == a]
        print("== %s (%d)" % (a, len(R)))
        for s in srcs:
            ex = [est(r, s)[0] - r["pose"][0] for r in R if r["meas"][0]]
            ey = [est(r, s)[1] - r["pose"][1] for r in R if r["meas"][1]]
            et = [wrapd(est(r, s)[2] - r["pose"][2]) for r in R if r["meas"][2]]
            print("  %-8s x %s | y %s | yaw %s" % (s, _st(ex), _st(ey), _st(et, 1.0)))
            if a == "ALL":
                summary["errors"][s] = {ax: {"n": len(v), "p50": float(np.median(np.abs(v))) if v else None,
                                             "p90": float(np.percentile(np.abs(v), 90)) if v else None,
                                             "max": float(np.max(np.abs(v))) if v else None}
                                        for ax, v in (("x_m", ex), ("y_m", ey), ("yaw_deg", et))}

    print("\nB. axes the match claims (sharpness >= %.2f) and claimed but > 5 cm off" % sharp_min)
    for s in srcs[1:]:
        claim = bad = n = 0
        for r in rows:
            m = r["maps"][s]
            for ax in (0, 1):
                if r["meas"][ax]:
                    n += 1
                    if m["sharp"][ax] >= sharp_min:
                        claim += 1
                        bad += abs(m["est"][ax] - r["pose"][ax]) > 0.05
        print("  %-6s %d measured axes: claimed %d, claimed and > 5 cm off %d" % (s, n, claim, bad))
        summary.setdefault("claims", {})[s] = {"measured": n, "claimed": claim, "claimed_off_5cm": bad}

    print("\nC. started 0.25 m + 7 deg off: back within 3 cm / 1 deg of the answer from the wheels' guess")
    for s in srcs[1:]:
        for a in areas:
            ok = tot = far = 0
            for r in (r for r in rows if r["area"] == a):
                m = r["maps"][s]
                for p in m["pert"]:
                    for ax in (0, 1):
                        if m["sharp"][ax] >= sharp_min:
                            d = abs(p[ax] - m["est"][ax]); tot += 1; ok += d <= 0.03; far += d > 0.10
                    d = abs(wrapd(p[2] - m["est"][2])); tot += 1; ok += d <= 1.0; far += d > 3.0
            print("  %-6s %-15s %4.0f%% back, %4.1f%% landed > 10 cm / 3 deg away" % (s, a, 100 * ok / max(1, tot), 100 * far / max(1, tot)))

    # D. the dock: y never measured there (reset_dock sets it by hand)
    by = {(r["run"], r["i"]): r for r in rows}
    print("\nD. docked y (never measured; the logged value was set by hand) vs what the first stop that measured y implies")
    pairs = []
    for run in sorted({r["run"] for r in rows}):
        d0 = by.get((run, 0))
        if not d0 or d0["label"] != "on the dock":
            continue
        for k in range(1, 200):
            r = by.get((run, k))
            if r is None:
                break
            if r["meas"][1]:
                assumed = d0["pose"][1]
                implied = assumed + (r["pose"][1] - r["guess"][1])
                pairs.append((run, {s: d0["maps"][s]["est"][1] for s in srcs[1:]}, implied, assumed))
                break
    for run, ys, implied, assumed in pairs:
        print("  %s  logged %.3f  %s  implied %.3f" % (run[4:15], assumed, "  ".join("%s %.3f" % kv for kv in ys.items()), implied))
    if pairs:
        imp = np.array([p[2] for p in pairs]); asm = np.array([p[3] for p in pairs])
        line = "  |logged - implied| median %.1f cm" % (np.median(np.abs(asm - imp)) * 100)
        for s in srcs[1:]:
            v = np.array([p[1][s] for p in pairs])
            line += "   |%s - implied| median %.1f cm" % (s, np.median(np.abs(v - imp)) * 100)
        print(line)
        summary["dock"] = {"pairs": len(pairs), "logged_vs_implied_p50": float(np.median(np.abs(asm - imp))),
                           **{s + "_vs_implied_p50": float(np.median(np.abs(np.array([p[1][s] for p in pairs]) - imp)))
                              for s in srcs[1:]}}

    # E. carried forward: unmeasured at stop k, measured at k+1, nothing between
    # that moved the pose but the wheels (drive_route writes each fix to the board)
    print("\nE. an axis not measured at one stop, carried by the wheels to the next stop that measured it")
    seq: dict[str, list] = {}
    for f in sorted(glob.glob(str(runs_dir / "*.jsonl"))):
        run = os.path.basename(f)[:-6]
        k, between = -1, []
        for line in open(f, encoding="utf-8"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("kind") == "checkpoint" and d.get("scan") and d.get("accepted"):
                k += 1
                if k > 0:
                    seq.setdefault(run, []).append((k - 1, k, between))
                between = []
            else:
                between.append(d)
    errs = {s: [] for s in srcs}
    for run, legs in seq.items():
        for a, b, between in legs:
            ra, rb = by.get((run, a)), by.get((run, b))
            if not ra or not rb:
                continue
            rolled = any(x.get("kind") == "drive" and any(y.get("taken") for y in (x.get("rolling") or [])) for x in between)
            if rolled or {x.get("kind") for x in between} & {"door", "dock", "back_into_dock", "undock", "door_step_across", "sidestep"}:
                continue
            delta = np.array(rb["guess"]) - np.array(ra["pose"])
            for ax in (0, 1):
                if ra["meas"][ax] or not rb["meas"][ax]:
                    continue
                truth = rb["pose"][ax]
                errs["odometry"].append(rb["guess"][ax] - truth)
                for s in srcs[1:]:
                    errs[s].append(ra["maps"][s]["est"][ax] + delta[ax] - truth)
    for s, v in errs.items():
        print("  %-8s %s" % (s, _st(v)))
    summary["carried"] = {s: {"n": len(v), "p50": float(np.median(np.abs(v))) if v else None} for s, v in errs.items()}
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", default=str(ROOT / "data/robot/runs"))
    ap.add_argument("--routes", default=str(ROOT / "data/robot/routes"))
    ap.add_argument("--plan-walls", help="plan wall segments CSV (x1_m,y1_m,x2_m,y2_m) to compare against")
    ap.add_argument("--save-field", help="also build the map from ALL days and save its likelihood field (npz) "
                                         "-- what drive_route loads on the robot (SHADOW_FIELD)")
    ap.add_argument("--json", help="write the summary numbers here")
    ap.add_argument("--no-eval", action="store_true",
                    help="only build what --save-field asks for (after new runs; ~1 min instead of ~8)")
    args = ap.parse_args(argv)
    runs_dir, routes_dir = Path(args.runs), Path(args.routes)
    frames = load_frames(runs_dir)
    n0 = len(frames)
    frames = retruth(frames, routes_dir)
    print("%d checkpoint frames, %d kept with today's anchors (%d days)" % (n0, len(frames), len({f.day for f in frames})))
    if not frames:
        print("nothing to measure")
        return 1
    grid = sm.grid_for([(f.pts, f.pose) for f in frames])
    plan_field = sm.Field.from_occupancy(grid, plan_occupancy(grid, args.plan_walls)) if args.plan_walls else None
    summary = None
    if not args.no_eval:
        rows = evaluate(frames, grid, plan_field)
        summary = report(rows, frames, runs_dir)
    if args.json and summary is not None:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(summary, indent=1), encoding="utf-8")
    if args.save_field:
        _, occ, _ = lidar_field(grid, frames)
        runs = sorted({f.run for f in frames})
        meta = {"built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "runs": len(runs), "first_run": runs[0],
                "last_run": runs[-1], "frames": len(frames), "min_runs": sm.MIN_RUNS, "sigma": sm.SIGMA,
                **frame_meta(routes_dir)}
        field = sm.Field.from_occupancy(grid, occ, meta=meta)
        out = Path(args.save_field)
        out.parent.mkdir(parents=True, exist_ok=True)
        field.save(out)
        print("field saved: %s (%d x %d cells, %d runs, %d bytes)" % (out, grid.nx, grid.ny, len(runs), out.stat().st_size))
    return 0


if __name__ == "__main__":
    sys.exit(main())
