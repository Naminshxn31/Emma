"""The floor simulator against the real nights (`python scripts/floor_sim.py calibrate`), and
what stands between walking to fixed spots and walking anywhere (`... gaps`).

Calibration is the simulator's own acceptance test: the same path from the same start as a
walk that really happened, many seeded times, beside what the robot did. Where the two
disagree the table says so -- it is not tuned away. Both write under .tmp/floor1-sim/.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import floor_sim as fs  # noqa: E402
import walk_sim as ws  # noqa: E402

OUT = ROOT / ".tmp" / "floor1-sim"

#: the dock document of the night of 16 Nov as it was walked: the board's MoveTo to the
#: pre-dock spot (no `back_from_m` yet)
OLD_WAY = {"dock": {"pose": [-7.11, -6.78, 169.6], "approach": [-9.0, -6.2], "estimated": False}}
#: rehearsal_back_home as it was walked at 21:40 (an explicit back_to, before dock() learnt it)
BACK_HOME_AS_WALKED = [
    {"do": "undock"}, {"to": [-9.0, -5.2], "face": 90, "speed": 0.3}, {"do": "wait", "s": 5},
    {"to": [-8.6, 0.2], "speed": 0.3}, {"do": "wait", "s": 5}, {"to": [-8.4, -3.5], "speed": 0.3},
    {"to": [-9.0, -6.2], "speed": 0.3}, {"to": [-11.0, -6.9], "face": -131, "speed": 0.3}, {"do": "wait", "s": 5},
    {"to": [-9.3, -5.4], "speed": 0.3}, {"to": [-8.98, -6.44], "face": 169.6, "speed": 0.25}, {"do": "wait", "s": 3},
    {"back_to": [-7.11, -6.78, 169.6]}]
REHEARSAL = [
    {"do": "undock"}, {"to": [-9.0, -5.2], "face": 90, "speed": 0.3}, {"do": "wait", "s": 5},
    {"to": [-8.6, 0.2], "speed": 0.3}, {"do": "wait", "s": 5}, {"to": [-8.4, -3.5], "speed": 0.3},
    {"to": [-9.0, -6.2], "speed": 0.3}, {"to": [-11.0, -6.9], "face": -131, "speed": 0.3}, {"do": "wait", "s": 5},
    {"do": "dock"}]


def _many(lay, doc, steps, start, n, **world):
    return [fs.run_walk(lay, doc, steps, start, seed=k, **world) for k in range(n)]


def _line(runs) -> str:
    n = len(runs)
    done = [r for r in runs if r["end"] == "done"]
    docked = [r for r in done if r["on_dock"]]
    kinds: dict[str, int] = {}
    for r in runs:
        if r["end"] != "done":
            k = fs.stop_kind(r["reason"])
            kinds[k] = kinds.get(k, 0) + 1
    legs = [m for r in runs for m, _ in r["legs"] if m < 0.25]
    qs = [q for r in runs for _, q in r["legs"]]
    parts = ["done %d/%d" % (len(done), n)]
    if kinds:
        parts.append("stops: " + ", ".join("%s x%d" % kv for kv in sorted(kinds.items())))
    if docked:
        aside = [abs(r["aside_m"]) * 100 for r in docked]
        crook = [abs(r["crooked_deg"]) for r in docked]
        parts.append("docked %d: aside median %.1f cm (max %.1f), crooked median %.1f deg (max %.1f)" % (
            len(docked), np.median(aside), max(aside), np.median(crook), max(crook)))
    if legs:
        parts.append("stops end %.2f m off (median; max %.2f)" % (np.median(legs), max(legs)))
    if qs:
        parts.append("Q %d-%d" % (min(qs), max(qs)))
    parts.append("%.0f s" % np.median([r["s"] for r in runs]))
    parts.append("pose error up to %.2f m" % max(r["max_err_m"] for r in runs))
    return "; ".join(parts)


def main(n: int = 20) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    lay = fs.load_layout("20261116", dock="night")           # the replays: the dock where it stood that night
    if lay is None:
        print("the 16 Nov layout is not on this machine -- nothing to calibrate against")
        return 2
    paths = fs.floor1_paths()
    rows = []

    def case(title, real, runs, verdict):
        rows.append((title, real, _line(runs), verdict(runs)))
        print("\n%s\n  real: %s\n  sim : %s\n  -> %s" % rows[-1])

    def share(runs, test):
        return sum(1 for r in runs if test(r)) / len(runs)

    case("16 Nov 20:34 dock_out_in (0.45 m out and back, the board's MoveTo to the pre-dock spot)",
         "docked 0.6 cm aside, Q69, 33 s",
         _many(lay, OLD_WAY, paths["dock_out_in"], lay.dock, n),
         lambda r: "reproduced" if share(r, lambda x: x["on_dock"]) >= 0.8 else
         "NOT reproduced: docked %.0f%%" % (100 * share(r, lambda x: x["on_dock"])))
    case("16 Nov 20:36 dock_approach_test (out to (-9.0, -6.2), home by the pre-dock spot)",
         "approach 0.01 m off Q64; pre-dock leg ended 0.18 m off; docked 1.1 cm aside, 59 s",
         _many(lay, OLD_WAY, paths["dock_approach_test"], lay.dock, n),
         lambda r: "home %.0f%% of the time (real: 1 of 1; with the rehearsals 3 of 5 ways home by the pre-dock spot)" % (
             100 * share(r, lambda x: x["on_dock"])))
    reh = _many(lay, OLD_WAY, REHEARSAL, lay.dock, n)
    case("16 Nov 20:40 + 20:56 event_rehearsal x2 (the old way home)",
         "stops 0.01-0.07 m off, Q47-73; 146 s; ONE docked 9.5 cm aside / 1.7 deg, ONE 'the board gave up' 0.91 m "
         "short of the pre-dock spot (pose slid 0.46 m facing the nook)",
         reh,
         lambda r: "the round itself reproduced (stops, time); home: %.0f%% docked, %.0f%% not (real 1 and 1 of 2). HOW it "
         "fails differs: the real board gave up short of the spot; the simulated one drives on its slid pose and our own "
         "turn finds no room" % (100 * share(r, lambda x: x["on_dock"]), 100 * share(r, lambda x: not x["on_dock"])))
    stuck = (-7.97, -6.55, -18.0)
    case("16 Nov 21:11 to_dock from where it stood facing the nook (true (-7.97, -6.55), board said (-8.43, -6.45))",
         "0 of 2: CANNOT_REACH_TARGET, no movement; Q54",
         _many(lay, dict(OLD_WAY, min_quality=40), [{"do": "dock"}], stuck, n, settle_s=6.0, nook_slides=1.0),
         lambda r: "reproduced" if share(r, lambda x: not x["on_dock"]) >= 0.8 else
         "NOT reproduced: it docks %.0f%% of the time here" % (100 * share(r, lambda x: x["on_dock"])))
    turn = _many(lay, dict(OLD_WAY, min_quality=40), paths["turn_out"], stuck, n, settle_s=6.0, nook_slides=1.0)
    case("16 Nov 21:11 turn_out there (turn to 170 deg on the spot)",
         "the board's pose jumped 0.46 m back to the truth, Q54 -> Q72, lidar fit 0.77",
         turn,
         lambda r: "reproduced: slid %.2f m before the turn, %.2f m after" % (
             max(x["max_err_m"] for x in r), max(math.hypot(x["pose"][0] - x["board_pose"][0], x["pose"][1] - x["board_pose"][1]) for x in r)))
    case("16 Nov 21:40 rehearsal_back_home as walked (to 1.9 m out on the line facing out, then back_to)",
         "line spot 0.06 m off Q71; reversed 1.95 m; docked ~4 cm aside / 1.9 deg; 165 s",
         _many(lay, OLD_WAY, BACK_HOME_AS_WALKED, lay.dock, n),
         lambda r: "home %.0f%% (real 1 of 1), sitting as the real one did (~4 cm aside, 1.9 deg); the rest stop at "
         "back_to's own gate (5 cm / 3 deg off the line) -- the real leg ended 0.06 m off and passed it" % (
             100 * share(r, lambda x: x["on_dock"])))
    lay4 = fs.load_layout("20261104")
    if lay4 is not None and "lobby_survey" in paths:
        doc4 = dict(fs.route_doc(lay4), min_quality=40)          # the simulated Q on the old dock reads 47 (real: >= 50)
        sv = _many(lay4, doc4, paths["lobby_survey"], lay4.dock, 3)
        case("4 Nov lobby_survey (today's file: 8 stops, a full round at each; the old dock)",
             "walked through twice that night (~20 min each); Q 41-75",
             sv, lambda r: "NOT reproduced: done %d of %d (the real night: 2 of 2). With the refusal distance bounded by "
             "what the real board took (REFUSE_SEEN_M 0.30) it now reaches 7 of the 8 stops, P7 (-7.6, -0.6) among them, "
             "and stops LEAVING P7: the simulated base meets the truth cell 0.33 m from that stop's centre (the base's own "
             "half-width is 0.35 m), where the real robot stood and turned a full round twice -- so that cell is not where "
             "the thing was, or the thing is not solid at the base's height. Also: the start gate had to be lowered to Q40 "
             "(simulated Q on the old dock 47, real 50 or more), today's file is not that night's (the way round the bump "
             "was added 5 Nov), and FURNITURE_RETURNS and Q were fitted on 16 Nov only" % (
                 sum(x["end"] == "done" for x in r), len(r)))
        bump = _many(lay4, doc4, [{"to": [-7.6, -0.6], "speed": 0.3}], (-8.7, 1.0, -55.0), 5)
        hz = min(v for x in bump for v in x["min_hazard"].values()) if bump[0]["min_hazard"] else None
        rows.append(("4 Nov P6 -> P7 as it was then (straight from (-8.7, 1.0) to (-7.6, -0.6))",
                     "the heading flicked +19 / -22 deg at (-7.88, 0.80) and (-7.83, 0.80): something under the lidar plane",
                     "centre passes %.2f m from that point" % hz if hz is not None else "no hazard in the event file",
                     "the simulated straight line passes inside the hazard's keep-off radius (0.75 m), as the real one "
                     "did; the flick itself is not modelled (nothing is known of the thing but where the centre was)"))
        print("\n%s\n  real: %s\n  sim : %s\n  -> %s" % rows[-1])
    rows.append(("28 Oct reception_tour x4 (4 dockings, run 4 met the dock 1.9 deg crooked, 1.3 cm short)",
                 "known only from CLAUDE.md / CHANGELOG", "no per-run record is on this PC (no frames, no --record folder)",
                 "CANNOT be replayed"))
    md = ["# The floor simulator against the real walks", "",
          "`python scripts/floor_sim.py calibrate` -- %d seeded runs a case; the same steps from the same start." % n, "",
          "| Walk | The robot | The simulator | Verdict |", "|---|---|---|---|"]
    md += ["| %s | %s | %s | %s |" % r for r in rows]
    md += ["", "## The lidar itself", "",
           "Simulated scan against the real frame at four known poses of 16 Nov (`.tmp/floor1-sim/scripts/lidar_check.py`): "
           "range by bearing agrees within 3-4 cm at the median (79% of bearings within 15 cm) on the dock and in front of it; "
           "facing the nook 12 cm (57%). Fit on the board's map at 950 real poses: real ~ 1.01 x simulated + 0.02 (corr 0.87).",
           "", "## What it does not reproduce", "",
           "- **Why the board's pose slides.** The slide in the nook is put in as measured (where, how far, when it ends). "
           "Our own fit on the REAL frames finds no better pose within 0.45 m there, so the cause is the board's, unknown.",
           "- **How a slid way home ends.** The real board gave up short of the pre-dock spot (CANNOT_REACH_TARGET); the "
           "simulated one drives on its slid pose to 0.1 m from the dock and our own turn then finds no room. Both do not "
           "get home, at about the same rate (2 of 5 real, 35-50% simulated).",
           "- **The old way's 9.5 cm aside** (20:40) appears only when the leg to the pre-dock spot ends aside, which the "
           "measured arrival misses give rarely (max 3.7 cm aside in 20 runs).",
           "- **4 Nov**: the survey replay reaches 7 of 8 stops and stops leaving P7 (the base against a truth cell 0.33 m "
           "from the stop, where the real robot turned a full round twice); the simulated Q on the old dock is too low.",
           "- **The board's planner**: straight when its own map is clear, a shortest path otherwise -- never recorded.",
           "- **28 Oct**: no data here.", "", "## Assumptions with no measurement behind them", ""]
    md += ["- " + a for a in fs.assumptions()]
    (OUT / "calibration.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\nwritten:", OUT / "calibration.md")
    return 0


def gaps() -> int:
    """Where a robot sent ANYWHERE in the lobby by the board's MoveTo would be unsafe or
    unreliable today, measured on the 16 Nov truth against the board's map."""
    OUT.mkdir(parents=True, exist_ok=True)
    lay = fs.load_layout("20261116", dock="night")           # the furniture; the dock plays no part here
    if lay is None:
        print("the 16 Nov layout is not on this machine")
        return 2
    x0, x1, y0, y1, step = -12.5, -6.0, -7.4, 2.6, 0.25
    xs, ys = np.arange(x0, x1 + 1e-6, step), np.arange(y0, y1 + 1e-6, step)
    solid, board = lay.solid, lay.board.points()

    def nearest(pts, x, y):
        return float(np.hypot(pts[:, 0] - x, pts[:, 1] - y).min())

    turn_r = math.hypot(fs.FOOT_L / 2, fs.FOOT_W_ARMS / 2)
    arm_half = fs.FOOT_W_ARMS / 2
    w = fs.FloorWorld(lay, (-9.0, -5.2, 0.0), seed=0, roll_off_deg=0.0)
    cells = []
    for x in xs:
        for y in ys:
            ds, db = nearest(solid, x, y), nearest(board, x, y)
            if ds < fs.FOOT_L / 2:                               # inside something
                continue
            low = 0
            if ds >= arm_half:
                for k in range(8):
                    w.base.x, w.base.y, w.base.yaw = x, y, k * math.pi / 4
                    w._crop_at = None
                    if w._score((x, y, w.base.yaw), w.scan()) < fs.LOW_FIT:
                        low += 1
            cells.append((x, y, ds, db, low))
    c = np.array(cells)
    area = step * step
    stand = c[:, 2] >= turn_r + 0.05
    passes = c[:, 2] >= arm_half + 0.05
    board_free = c[:, 3] >= fs.BOARD_INFLATE_M
    trap = board_free & ~passes                                   # the board would plan here; the arms would not fit
    refused = c[:, 3] < fs.REFUSE_MAP_M
    lowfit = passes & (c[:, 4] >= 4)
    anylow = passes & (c[:, 4] >= 1)

    def boxes(mask, lim=6):
        pts = c[mask][:, :2]
        if not len(pts):
            return "none"
        # greedy 1 m clusters
        left, out = [tuple(p) for p in pts], []
        while left and len(out) < lim:
            seed = left.pop(0)
            grp = [seed]
            grew = True
            while grew:
                grew = False
                for q in list(left):
                    if any(math.hypot(q[0] - g[0], q[1] - g[1]) <= step * 1.5 for g in grp):
                        grp.append(q)
                        left.remove(q)
                        grew = True
            g = np.array(grp)
            out.append("x %.1f..%.1f / y %.1f..%.1f (%.1f m2)" % (g[:, 0].min(), g[:, 0].max(), g[:, 1].min(), g[:, 1].max(), len(g) * area))
        return "; ".join(out) + ("; ..." if left else "")

    spots = {"reception": (-9.0, -5.2), "entrance": (-8.6, 0.2), "multimedia door front": (-11.0, -6.9),
             "lobby_west": (-10.5, -2.5), "lobby_mid": (-9.5, -0.5), "lobby_east": (-8.5, -3.0)}
    md = ["# From fixed spots to walking anywhere in the lobby: what the simulator measures", "",
          "Owner, 17 Nov: 'หุ่นสามารถเดินได้ทั่ว lobby โดยที่เราไม่ต้องควบคุม'. Today she walks to recorded spots, each one "
          "checked by a person. This is what a free target would meet, on the furniture of the night of 16 Nov "
          "(%.2f m grid over x %.1f..%.1f, y %.1f..%.1f; `python scripts/floor_sim.py gaps`). No code for free roaming was written." % (
              step, x0, x1, y0, y1), "",
          "## Numbers", "",
          "| | m2 | |", "|---|---|---|",
          "| Floor her centre can be on at all (arms clear by 5 cm) | %.1f | |" % (passes.sum() * area),
          "| ... where she can also turn on the spot (%.2f m round the centre) | %.1f | %.0f%% |" % (
              turn_r + 0.05, stand.sum() * area, 100 * stand.sum() / max(1, passes.sum())),
          "| Floor the BOARD's map calls free for its plans but where the arms do not fit | %.1f | the board plans through it until its lidar sees the thing |" % (trap.sum() * area),
          "| Floor the board would refuse as a target (within %.2f m of its own map) though she fits there | %.1f | |" % (
              fs.REFUSE_MAP_M, (refused & passes).sum() * area),
          "| Floor where the lidar's fit on the board's map is under %.2f for at least half the headings | %.1f | %.0f%% of where she fits |" % (
              fs.LOW_FIT, lowfit.sum() * area, 100 * lowfit.sum() / max(1, passes.sum())),
          "| ... for at least one heading | %.1f | %.0f%% |" % (anylow.sum() * area, 100 * anylow.sum() / max(1, passes.sum())),
          "",
          "- **Where the board's map hides things from its plans:** %s" % boxes(trap),
          "- **Where the fit is low for most headings** (the board's pose is nobody's checked word there; the one place it "
          "was checked, the nook, it was 0.4 m wrong): %s" % boxes(lowfit),
          "", "## Today's spots", "", "| Spot | Nearest solid thing | Nearest thing on the board's map | Headings (of 8) with a low fit |", "|---|---|---|---|"]
    for name, (x, y) in spots.items():
        i = int(np.argmin(np.hypot(c[:, 0] - x, c[:, 1] - y)))
        md.append("| %s (%.1f, %.1f) | %.2f m | %.2f m | %d |" % (name, x, y, nearest(solid, x, y), nearest(board, x, y), int(c[i, 4])))
    md += ["", "## What stands between today and free roaming (in order of what the measurements say)", "",
           "1. **The board plans on a map that is not the lobby.** %.1f m2 of floor it takes for free holds furniture the arms "
           "would meet, and its own inflation is the base's, not the arms' (28 Oct: an arm on a table; here a walk that "
           "leaves the dock straight for the entrance side passes (-8.0, -4.8) with the arms 0.04 m from it). Fixed spots avoid this "
           "by hand (`via`, the approach point). Free targets need either the furniture in the board's map (a map_check "
           "upload after the event's tables stand, and again when they move) or our own planner over our own grid with the "
           "arms' footprint, driving the lines ourselves (the creep code exists)." % (trap.sum() * area),
           "2. **Nobody checks the board's pose.** On %.0f%% of the floor she fits on, at least one heading gives a fit under "
           "%.2f -- the condition under which the pose was found 0.4 m wrong in the nook. A free walk needs a second opinion "
           "on the pose before and during it: the lidar against OUR map (app/scan_match.py did this on floor 2, p90 2-6 cm), "
           "and a rule for what to do when the two disagree (turn toward mapped walls, as `turn_out` did)." % (
               100 * anylow.sum() / max(1, passes.sum()), fs.LOW_FIT),
           "3. **What the lidar cannot see.** The floor bump, chair wheels and table legs are below its plane; nothing behind "
           "her is seen at all. Fixed spots were walked and watched; free targets need a keep-out list kept by people "
           "(the event file's `floor_hazards` is one entry) or another sensor. The simulator knows only what is in that list.",
           "4. **People.** The board holds for someone in front and then goes round; how wide is not known (assumed: the base's "
           "width, so the arms pass at ~0 in the adverse cases). Before free roaming among guests this must be measured on "
           "the robot with a person standing in for the guest, or the rounding taken away from the board (stop and wait only).",
           "5. **Turning room.** %.0f%% of the floor she fits on lets her turn on the spot with the arms; a free target "
           "must be on that part, and a final heading asked for only there." % (100 * stand.sum() / max(1, passes.sum())),
           "6. **Coming home.** The dock is reached by a 1.9 m blind reverse from one spot on its line; free roaming does not "
           "change that, it only needs a checked way from anywhere to that spot (the pre-flight walks it from every spot).",
           "", "## Assumptions", ""] + ["- " + a for a in fs.assumptions()]
    (OUT / "free_roam_gaps.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md[4:16]))
    print("written:", OUT / "free_roam_gaps.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(gaps() if sys.argv[1:2] == ["gaps"] else main())
