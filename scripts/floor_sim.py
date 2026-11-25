"""Floor 1 on a desk: the robot's own walking code against a simulated lobby.

Owner, 17 Nov 2026: "เอาข้อมูลที่เคยเก็บทั้งหมดทำเป็น simulator ... ให้ระบบของเราลองเดินอยู่ตรงนั้น
ทดสอบจนกว่าระบบเราจะดี" -- and what it is for: "หุ่นสามารถเดินได้ทั่ว lobby โดยที่เราไม่ต้องควบคุม".

What runs here is the REAL `scripts/board_walk.py` Walker (legs, creep, forward, back_to,
undock/dock) and the event layer's real route builders (`event_host.spot_steps`,
`keep_off_hazards`, `event_walk`'s route document). What is simulated is everything under
them, and every part of that is either measured or named as an assumption below:

* **two maps, kept apart.** `truth` = what the lidar plane really holds (the board's map plus
  everything >= 2 walks' lidar agreed on -- scripts/map_check.py's output); `board` = the map
  the board localises and plans on. The night of 16 Nov both real failures came from the
  difference: the dock's nook is furnished in the truth and empty (with two stale wall
  fragments) on the board's map.
* **the lidar** is ray-cast over the truth (-101..+108 deg, nothing behind), glass the
  walks saw through ("phantom" cells) lets the beam pass but is still solid.
* **the board's pose** = the true pose plus the one error that was measured: facing the
  dock's nook from nearer than ~1.5 m it reads ~0.4 m further out along the dock's line and
  comes back as she turns away (16 Nov, 19 of 50 frames; `.tmp/floor1-20261116/slip_report.md`).
  A first version let the pose climb the board's own map with our `MapFit` (the scan scored
  round the pose it holds): on the REAL frames taken facing the nook that climb finds no
  better place within 0.45 m either (0.41 against 0.40), so it is not how the board slides
  and it is not used. Elsewhere the board is taken at its word, and the lidar's fit on the
  board's map is reported per walk as what nobody has checked (`low_fit_s`); `loc="pessimistic"`
  is an adverse case that slides the pose wherever that fit is low. Q follows the fit
  (950 real frames at the same poses: Q ~ 40 * fit + 39.5, sd 3.8).
* **the board's MoveTo** plans on what the BOARD knows (its map + what its lidar has seen
  lately), in the board's frame; refuses a target too near something (CANNOT_REACH_TARGET);
  ends near the point by the measured misses; inflates things by its base, not by the arms
  (28 Oct: an arm on the table at (-7.5, -4.2)); holds for what steps in front and gives up.
* **the base** is scripts/walk_sim.py's (lag, pulses, the dock) and rolls ~2.3 deg off the
  heading the board reads (and the lidar looks along).

It is a pre-flight gate, not the lobby: the board's planner and localiser are black boxes we
have samples of; legs of chairs under the lidar plane and people are only here when a case
puts them in. `calibrate` says how far it reproduces the real nights; `preflight` walks
every path and every event walk many seeded times.

    python scripts/floor_sim.py calibrate
    python scripts/floor_sim.py preflight [--runs N] [--seed S] [--only NAME]
    python scripts/floor_sim.py gaps            # what stands between spots and free roaming
    python scripts/floor_sim.py event-day [--runs N]   # the owner's event day (20 Nov) in one world + a picture
                                                       # a way home: .tmp/floor1-sim/event_sequence_20261121_*.png
    python scripts/floor_sim.py profile [--profile serving_example|emma|FILE.json] [--package DIR] [--spots F]

The dock (20 Nov) is where TODAY's files put it -- floor1.json's, or the pose re-learnt at the
robot ("วัดแท่นใหม่", the event's spots file; `--spots` a copy pulled from the robot) -- not
the night's; a replay of a night asks for that night's (`load_layout(dock="night")`).

The robot (20 Nov, owner: our maps may serve a serving robot too) is a profile
(app/robot_profile.py): body, lidar arc, motion, planner margins, dock tolerance. Emma is the
default and gives the same bytes as before. `profile` walks our spots and the ways between
them for ANOTHER robot as plain planner legs (its own navigation is its vendor's), on the 16 Nov
furniture or on a portable map package (scripts/map_package.py).
"""
from __future__ import annotations

import heapq
import json
import math
import re
import struct
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

from app import robot_lanes as rl  # noqa: E402
from app import robot_profile as rp  # noqa: E402
import walk_sim as ws  # noqa: E402

# ---- measured ----------------------------------------------------------------------
#: The lidar's arc on this robot (16 Nov frames: -101..+108 deg), nothing behind.
LIDAR_FROM, LIDAR_TO = math.radians(-101.0), math.radians(108.0)
LIDAR_BIN = math.radians(0.25)
LIDAR_RANGE_M = 10.0
LIDAR_NOISE_M = 0.008
#: Of the cells the truth holds and the board's map does not (furniture, the nook), the real
#: beam returns from this share on one scan: chair and table legs, glass, things lower than
#: the plane at some bearings. Set so the simulated scan's fit on the board's map equals the
#: real scan's at the same 950 poses (fit_vs_real.py): with 1.0 the simulated fit read 0.13
#: low, with 0.55 0.08 low, with 0.35 real ~ 1.01 * sim + 0.02 (corr 0.87, sd 0.09).
FURNITURE_RETURNS = 0.35
#: Q against the SIMULATED scan's fit on the board's map at the same pose: 950 real frames of
#: 16 Nov with the pose trusted (.tmp/floor1-sim/scripts/fit_vs_real.py): Q ~ 40 * fit + 39.5
#: (corr 0.85, sd 3.8). On real frames alone (2,745 of 4 and 16 Nov) Q ~ 43 * fit + 33 and
#: never sits under ~41.
Q_SLOPE, Q_AT_ZERO, Q_FLOOR = 40.0, 39.5, 41.0
#: The board on a MoveTo: rolls at the speed asked (median 0.27 m/s on 0.3 legs), turns on
#: the spot at ~0.38 rad/s (same frames).
SPOT_RAD_S = 0.38
BOARD_ACCEL = 0.4
#: Where its legs ended, metres off the point (the "... m off" lines of 16 Nov's five walks:
#: stops with a heading, the dock's approach and pre-dock spots).
ARRIVE_M = (0.01, 0.01, 0.01, 0.02, 0.02, 0.02, 0.03, 0.04, 0.04, 0.04, 0.04, 0.06, 0.07, 0.08, 0.08, 0.12, 0.18)
ARRIVE_DEG = 2.5
#: The pose slid 0.38-0.46 m facing the nook and came back as a turn ended.
#: The roll offset (walk_sim.ROLL_OFF_DEG) is the base's.

# ---- assumed (no measurement: named, conservative, listed by `assumptions()`) ------------
#: The board will not take a target nearer than this to something it knows. Bounded from what
#: the real board TOOK (.tmp/floor1-sim/scripts/refuse_bound.py, the 17 targets of 4 and
#: 16 Nov against each night's truth): beside its own map it took the old pre-dock spot at
#: 0.48 m three times; beside a thing only its lidar saw it took (-7.6, -0.6) at 0.33 m twice
#: on 4 Nov. So REFUSE_MAP_M <= 0.48 and REFUSE_SEEN_M <= 0.33. From below there is one
#: sentence and no record (28 Oct: CANNOT_REACH_TARGET "0.51 m from something" -- which is
#: more than both bounds, so it was not this rule alone). The values are just under the
#: nearest target taken: an upper bound from data, still an assumption below it. (0.55 for
#: what the lidar saw, the first version's guess, made lobby_survey and reception_tour stop
#: at points the real board had taken or that stand 0.43 m from a thing.)
REFUSE_MAP_M = 0.45
REFUSE_SEEN_M = 0.30
#: What the board keeps clear of round its centre when it plans: its base, not the arms.
BOARD_INFLATE_M = 0.36
#: How long a thing its lidar saw stays in its plan after it was last seen.
SEEN_KEEP_S = 25.0
#: Held this long by something in front: it plans again; this long: it ends the leg short.
BOARD_REPLAN_S, BOARD_GIVE_UP_S = 3.0, 10.0
#: The board's pose is looked at once a LOC_PERIOD (the slip's coming and going, Q, what its
#: lidar has seen). The slide builds at SLIP_RATE (the frames show it there within 2-3 s of
#: facing the nook) and is gone within a second of turning away (a jump of 0.38-0.46 m).
LOC_STEPS = (-0.45, -0.30, -0.15, -0.05, 0.0, 0.05, 0.15, 0.30, 0.45)
LOC_PERIOD_S, LOC_PTS = 1.0, 240
SLIP_RATE_MPS = 0.2
#: Facing the nook inside the zone the pose slid on 19 of 50 real frames, and 2 of the night's
#: 5 ways home by the pre-dock spot failed: each time she comes to face the nook the board
#: slides with this chance (`nook_slides` 1.0 = it has, as where she stood at 21:11).
NOOK_SLIDES = 0.4
#: Under this fit of the scan on the board's map nobody has checked the board's pose (the
#: slipped real frames: 0.13-0.40; facing out, never under 0.50). `loc="pessimistic"` slides there.
LOW_FIT = 0.45
#: A person: a round thing this wide at the lidar plane.
PERSON_R = 0.22
#: Things above the lidar's plane that the arms reach and the lidar never sees: in the truth for the
#: arms (every arm gap, the pageant arm, the handshake), never in a scan, never for the base. The
#: reception's counter (owner 20 Nov: "โต๊ะก็คือที่แขนชนวันนั้นเลย ไม่มีย้ายเลย" -- the table an arm hit on
#: 28 Oct, never moved): the lidar sees only its legs/base at x ~ -7.45 (y -4.47..-4.07 on 20 Nov; the
#: 16 Nov truth's leg line runs -3.85..-4.55), its corner cells are x -7.62..-7.52, y -4.82..-4.62, and its
#: top sticks out 10-15 cm west of the legs. Taken at its widest: the west edge at -7.62 (the corner's and
#: beside_host's note "ขอบหน้าโต๊ะ x ≈ −7.62"), from the corner (-4.82) to the north end of the legs
#: (-3.85), back to the legs (-7.42; what is east of them no arm reaches). (x0, x1, y0, y1).
ABOVE_LIDAR = ({"id": "counter_top", "box": (-7.62, -7.42, -4.82, -3.85),
                "source": "the counter's top: legs at x -7.45 + 10-15 cm overhang (owner 20 Nov), corner cells of 16 Nov"},)
#: What the lidar SAW at today's dock: on 20 Nov, docked at (-7.10, -6.84) 168.0 deg (the pose floor1.json was
#: given that night), five scans (scratchpad dock_scan_read.py, CHANGELOG 20 Nov: "ไม่มีอะไรในรัศมี 1.2 ม.
#: (เลเซอร์ไม่เห็นข้างกล่องแท่น)") -- nothing within 1.2 m in its arc. The 16 Nov truth there (a wall fragment of
#: that night's board map at (-6.85, -6.07..-6.27), furniture from y -5.97 north) is not today's: it held the
#: debut's walk ahead on its arm's side for 45 s in the first run of this check, where the real one walked its
#: metre in 10 s with the same side watch (n20j). Taken out of TODAY's layout only (the replays of 16 Nov keep it),
#: and only in the arc: behind her nobody has looked.
SEEN_EMPTY = ({"at": (-7.10, -6.84, 168.0), "r": 1.2, "source": "20 Nov: five scans on the dock, nothing within 1.2 m"},
              # map_check of 20 Nov (.tmp/map-check/20261120-floor1/pass2/findings.json) phantom #26: the lidar saw
              # THROUGH x -6.87..-6.82, y -6.67..-6.57 in 5 runs -- the south end of an old (9 Nov board map) line at
              # x -6.83 behind-right of the dock. Its other part, y -6.55..-6.07, nobody has ever seen either way: it
              # stays in the truth, and the pageant arm on the dock comes 0.03 m from it -- for the owner to look at
              {"box": (-6.87, -6.82, -6.67, -6.57), "source": "map_check 20 Nov phantom #26, seen through in 5 runs"})
#: (25 Nov ~09:50, owner: the glass-framed sign between guest 3 and the globe is gone, but a cabinet stands there for
#: the event -- "ตู้จะต้องวางตรงนั้นอยู่": the 16 Nov truth there (x -7.67..-6.72, y -1.97..-0.67) is kept as the
#: cabinet's stand-in; card 3's line to the globe goes west of it.)
#: The pageant's arm (10 Nov's side pose, `docs/emma-event-host.md`: shoulder 5=1420 6=1220, the elbow out to
#: the side and the forearm up) as a box in the robot frame (x ahead, y left), at shoulder height. NOT
#: MEASURED: app/event_host.py PAGEANT_RIGHT_CLEAR_M's own estimate ("the elbow and hand reach ~0.5-0.6 m
#: from the centre at shoulder height") -- out to 0.60 m from the centre, 0.25 m past the base's 0.35 m
#: edge, and +-0.15 m along it (the elbow beside the shoulder; docs/robot-arm-command-map line 105: "มือยื่น
#: พ้นฐานเท่าไหร่ -- ดูที่แท่นก่อนเดิน" was never done).
PAGEANT_ARM_BOX = (-0.15, 0.15, -0.60, -0.35)
#: The handshake (group 6, the right hand -- BodyGestures HANDSHAKE_GROUP; its joints were never recorded,
#: the arm map's "มือยื่นพ้นฐานเท่าไหร่" never measured): ASSUMED to reach HANDSHAKE_PAST_FRONT_M past the base's
#: front face, in front of the right shoulder -- the pageant estimate's 0.6 m from the centre, forward.
HANDSHAKE_PAST_FRONT_M = 0.30
HANDSHAKE_BOX = (0.30, 0.30 + HANDSHAKE_PAST_FRONT_M, -0.39, -0.05)
#: Places where the arms come nearer than this are kept per walk (run_walk's "tight").
TIGHT_M = 0.50

# ---- the pre-flight's bounds ---------------------------------------------------------
#: The arms' rectangle may not come nearer than this to anything solid (a touch is 0).
MIN_ARM_GAP_M = 0.05
#: The centre keeps the event file's own radius off a floor hazard (its `radius_m`).
#: A walk that is meant to arrive must arrive in this share of plain runs.
MIN_REACHED = 0.95
#: On the dock: within this of its centre line and heading (the contacts are 12 cm a side;
#: 16 Nov she charged 9.5 cm aside and the owner asked for better).
MAX_DOCK_ASIDE_M, MAX_DOCK_CROOKED_DEG = 0.05, 2.0
#: With a person or a thing put in the way the walk is not asked to arrive. A TOUCH there
#: (arms at 0 or less from the thing, the person, or anything solid) is counted and printed in
#: the summary under its own totals every run -- a table that is not on the board's map is the
#: event's own situation. It does not set the exit code, for one reason only: how wide the
#: board rounds a thing it has just seen is BOARD_INFLATE_M (0.36 m, never measured), 3 cm
#: less than the arms' half-width, so with it EVERY rounded thing is touched and the count
#: says "this has never been measured", not which walk is at fault. A touch while OUR code
#: drives (the dock's lines) is a broken bound and does set it.
ADVERSE_TOUCH_SETS_EXIT = False

#: Walks known to be outside the bounds, and why -- reported every run, not failing it; any
#: OTHER walk outside the bounds fails the pre-flight, and a walk listed here that is inside
#: them again is said so (take it off the list).
KNOWN_OUTSIDE = {
    "event dock -> beside_globe": (
        "since 25 Nov ~05:20: card 3's walk to the globe is our own glide from where she stands (owner: \"ไปไว ไม่สะดุด ไม่รอนาน\"); "
        "from the dock its straight line meets something ahead and she stops there (a stop, not a touch). The ceremony walks "
        "it from card 1's end point (20/20); a fourth point west of the counter fixed the dock but sent every walk from the "
        "guests back south first.",
        (r"^arrived \d+ of \d+ \(something ahead x\d+\)$",),
        {"min_arrived": 0.0}),                              # it stops, it does not touch: no count is worse
    "path lobby_survey": (
        "since 17 Nov (first run): its stop P7 (-7.6, -0.6) is 0.33 m from a cell of the board's own "
        "map of 9 Nov (the thing the 4 Nov survey itself wrote in) -- under REFUSE_MAP_M; and with a full "
        "turn there the arms' corner (0.49 m) reaches it. The real board took that target twice on 4 Nov, "
        "when its map did not have the thing. Not walked since: a person must look at that point. And (21 Nov) "
        "when the nook's slide is drawn (err 0.40 m) she passes 0.70-0.74 m from the floor bump (keeps 0.75): the "
        "world before 21 Nov does the same with seeds 1000+ (0.70 m, scratchpad walk_sim_1121/attribute.txt).",
        (r"^arrived \d+ of \d+ \(board gave up x\d+\)$",
         r"^centre 0\.7\d m from floor hazard lobby_floor_bump \(keeps 0\.75\)$"),
        {"min_arrived": 0.0}),                              # it arrives at none: no count is worse
    "path reception_tour": (
        "since 17 Nov: a path of 28 Oct's furniture. Its point (-10.5, -0.6) stands 0.43 m from a thing of "
        "16 Nov's (not on the board's map): inside the arms' corner reach (0.49 m) when she turns there "
        "(arms at 0.00..-0.05 m), and close enough that the board's plan sometimes finds no way to it: 6 of 20 on "
        "17 Nov; 21 Nov 0 of 20 before the world changed, 7 of 20 after -- not the counter's top (the top alone: "
        "0 of 20, the arms' least gap still at (-10.51, -0.75)) but the draws: the dock area seen empty changes "
        "every later random draw, and the old world gives up 5 of 20 with seeds 1000+ (scratchpad "
        "walk_sim_1121/attribute.txt). Not walked in this furniture: a person must look at that point before it is.",
        (r"^arms -?0\.0\d m from something solid at \(-10\.[3-7]\d, -0\.[4-9]\d\)$",
         r"^arrived \d+ of \d+ \(board gave up x\d+\)$"),
        {"min_arrived": 0.65}),                             # 13 of 20 the fewest seen (21 Nov); fewer is NEW
    "path room_front_and_back": (
        "since 17 Nov: it stops at the old door-front spot (-11.78, -7.36), where the arms have "
        "+1 cm with the door closed (CLAUDE.md, 5 Nov: the event's spot is (-11.0, -6.9) for that "
        "reason). The simulator reads -0.04..+0.07 m there. A path of 28 Oct, not the event's.",
        (r"^arms -?0\.0\d m from something solid at \(-11\.[6-9]\d, -7\.[2-5]\d\)$",)),
    # Until 23 Nov one finding here for every walk that starts with the debut: the pageant arm 0.03 m from something
    # at (-7.10, -6.84), raised ON the dock 4.5 s before the wheels (the dock's right side, the counter table the owner
    # names there, 21 Nov). Since 23 Nov (owner: "หุ่นอยู่ในกล่อง ... เดินออกมาก่อน จึงค่อยยกมือ") the debut walks out of
    # the box with the arms down and raises it only where it arrived (event_host `walk_out_of_box`, `_wave_standing`):
    # the arm never rises on the dock, so the entry is gone -- the debut's walk with the arm out all the way (a scene
    # without `walk_out_of_box`) finds it again, as NEW.
}


def _why(entry) -> str:
    return entry[0] if isinstance(entry, (tuple, list)) else str(entry)

#: Printed at the top of every pre-flight: what a clean report does NOT say.
NOT_SHOWN = (
    "the truth is the furniture of the night of 16 Nov at the lidar's plane, plus the reception counter's top at arm "
    "height (ABOVE_LIDAR, its overhang from the owner's 10-15 cm): any OTHER table top that overhangs is not in it -- "
    "every other arm gap here is an upper bound, and the event's own tables are not in it at all",
    "the pageant arm (0.25 m past the base's right edge) and the handshake's reach (0.30 m past the front) are "
    "ASSUMED sizes -- nobody has measured either; since 23 Nov the debut raises it only where its walk out of the box "
    "arrived (standing WAVE_STAND_S) -- the box itself is in no truth here: forward_m against its real depth is the "
    "venue's to measure",
    "the board's pose is taken as exact everywhere but the dock's nook (the one place it was checked, and wrong by 0.4 m)",
    "MoveTo's misses, Q and how much of the furniture returns were fitted on 16 Nov and checked on 16 Nov (circular); "
    "4 Nov is not reproduced; 28 Oct has no record here",
    "arm gaps on a MoveTo follow from an ASSUMED planner margin (BOARD_INFLATE_M 0.36 m; the gap rounding a thing is "
    "0.05 m with it, 0.14 at 0.45, 0.19 at 0.55): only the ORDER of routes is robust, not the centimetres",
    "Q here cannot go under the event walk's gate (floor 41 against min_quality 40) and the simulated fit reads ~0.10 "
    "high at the entrance side: the start gates are checked per spot from the REAL frames where there are any",
    "card 3's guest (22 Nov) stands where nobody has said -- ASSUMED HANDSHAKE_GUEST_AT_M from the counter's spot, "
    "straight ahead of her after the turn, still all day: a guest beside her line or one who walks up is not tried",
    "a turn on the spot never sees BEHIND her (the lidar's arc): card 3 turns round at the counter where people stand "
    "behind her -- someone within ~0.2 m of her back is touched (tests/test_floor_sim.py); only the staff's look and "
    "the turn's watch (a bumper, B) stand between",
    "card 1's tour (23 Nov): its three guest spots have NO position yet -- where they are and where each guest stands "
    "is ASSUMED (TOUR_GUESTS_*: a row 1.9 m out from the counter, 0.8 m apart, each guest 0.65 m in front of her spot) "
    "until --spots or the robot's recorded spots give them; every event walk's turns of more than 100 deg (at a "
    "MoveTo's start or end) are the same blind turn as card 3's -- checked only that one of ours (watched, slow) makes "
    "them (event_tour.rear_turns), not who stands behind her",
    "not simulated: go_home's reverse along a back line, walks up to guests (face_approach), the pageant's arm WAVING "
    "(it is a fixed box here), the multimedia room's door and everything behind it; within 1.2 m of the dock, in the "
    "lidar's arc, the 16 Nov truth is taken out (SEEN_EMPTY: the 20 Nov scans on the dock saw nothing there) -- the box "
    "she comes out of has sides below the lidar's plane or none, nobody knows which; behind her it is 16 Nov's",
    "simulated docking (0.2-0.4 cm aside) rests on ONE real 1.95 m reverse that sat ~4 cm aside and 1.9 deg crooked",
)

FOOT_L, FOOT_W_BASE, FOOT_W_ARMS = rl.ROBOT_BASE_LENGTH, rl.ROBOT_BASE_WIDTH, rl.ROBOT_WIDTH


def assumptions() -> list[str]:
    return [
        "REFUSE_MAP_M %.2f / REFUSE_SEEN_M %.2f: how near a thing a MoveTo target may be -- upper bounds from the nearest "
        "targets the real board took (0.48 m beside its map, 0.33 m beside a thing its lidar saw); nothing bounds them from below"
        % (REFUSE_MAP_M, REFUSE_SEEN_M),
        "BOARD_INFLATE_M %.2f: what the board's planner keeps clear of (never measured; set so an arm can touch, as on 28 Oct)"
        % BOARD_INFLATE_M,
        "SEEN_KEEP_S %.0f, BOARD_REPLAN_S %.0f, BOARD_GIVE_UP_S %.0f: the board's memory of what it saw and its patience"
        % (SEEN_KEEP_S, BOARD_REPLAN_S, BOARD_GIVE_UP_S),
        "the board's pose is wrong only where it was measured wrong (the dock's nook); everywhere else it is taken at "
        "its word -- low_fit_s says for how long a walk went where the board's map explains under %.0f%% of the scan"
        % (LOW_FIT * 100),
        "the board's path between two points: straight when its own map is clear, else a shortest path -- its real "
        "planner's shapes were never recorded",
        "people and low things exist only in the adverse cases; chair and table legs under the lidar plane are not in the truth",
        "the truth is the furniture of the night of 16 Nov (4 Nov for the survey replays), not the event's layout",
        "the counter's top (ABOVE_LIDAR %s): its west edge at its widest (the legs + 15 cm); the arms meet it, the lidar "
        "and the base do not" % (ABOVE_LIDAR[0]["box"],),
        "the pageant arm: a box %.2f..%.2f m along, %.2f..%.2f m to the right of the centre at shoulder height (never "
        "measured: event_host's own 0.5-0.6 m estimate)" % (PAGEANT_ARM_BOX[0], PAGEANT_ARM_BOX[1], -PAGEANT_ARM_BOX[3],
                                                             -PAGEANT_ARM_BOX[2]),
        "the handshake's hand: %.2f m past the base's front face, in front of the right shoulder (never measured)"
        % HANDSHAKE_PAST_FRONT_M,
        "card 1's tour guests (no position given): a row %.1f m from the counter's spot, %.1f m apart, each guest %.2f m "
        "in front of its spot, standing still all day" % (HANDSHAKE_GUEST_AT_M, TOUR_GUESTS_APART_M, TOUR_FRONT_TO_GUEST_M),
    ]


# ---- grids ---------------------------------------------------------------------------
class Grid:
    """An explore-map blob (board_walk.MapFit's format): `occ[row, col]`."""

    def __init__(self, blob: bytes):
        ox, oy, w, h, res = struct.unpack_from("<ffIIf", blob, 0)
        (size,) = struct.unpack_from("<I", blob, 32)
        if size != w * h:
            raise ValueError("explore map: %d cells for %d x %d" % (size, w, h))
        self.blob = blob
        self.ox, self.oy, self.w, self.h, self.res = float(ox), float(oy), int(w), int(h), float(res)
        self.occ = np.frombuffer(blob[36:36 + size], dtype=np.int8).reshape(h, w) < -10

    def rc(self, x, y):
        return (np.floor((np.asarray(y) - self.oy) / self.res).astype(int),
                np.floor((np.asarray(x) - self.ox) / self.res).astype(int))

    def points(self, mask=None) -> np.ndarray:
        rows, cols = np.nonzero(self.occ if mask is None else mask)
        return np.c_[self.ox + (cols + 0.5) * self.res, self.oy + (rows + 0.5) * self.res]


def grid_from_points(points, res: float = 0.05, margin: float = 1.5) -> Grid:
    return Grid(ws.explore_blob(points, res=res, margin=margin))


def dilate(mask: np.ndarray, r: int) -> np.ndarray:
    out = mask.copy()
    h, w = mask.shape
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx * dx + dy * dy > r * r or (dx == 0 and dy == 0):
                continue
            ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
            xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
            out[ys, xs] |= mask[yd, xd]
    return out


class Layout:
    """One night's lobby: the truth (solid), what the lidar returns from (solid minus glass
    it saw through), the board's map, the dock, floor hazards."""

    def __init__(self, truth: Grid, board: Grid, dock, *, glass=None, hazards=(), name: str = ""):
        self.truth, self.board, self.dock, self.name = truth, board, tuple(dock), name
        # where `dock` came from (load_layout / apply_todays_dock), and the pose re-learnt at the
        # robot that the EVENT walks dock to (None: they dock to floor1.json's, as the paths do)
        self.dock_from, self.relearnt = "that night (%s)" % (name or "given"), None
        self.solid = truth.points()
        seen = truth.occ.copy()
        if glass is not None:
            seen &= ~glass
        rows, cols = np.nonzero(seen)
        x, y = truth.ox + (cols + 0.5) * truth.res, truth.oy + (rows + 0.5) * truth.res
        br, bc = board.rc(x, y)                                   # the same place on the board's grid
        inside = (br >= 0) & (br < board.h) & (bc >= 0) & (bc < board.w)
        on_board = np.zeros(len(rows), bool)
        on_board[inside] = board.occ[br[inside], bc[inside]]
        walls = np.zeros_like(seen)
        walls[rows[on_board], cols[on_board]] = True
        self.returns = truth.points(walls)                        # what every scan returns from
        self.furniture = truth.points(seen & ~walls)              # what a scan returns from in part
        self.hazards = [dict(h) for h in hazards]
        # above the lidar's plane (ABOVE_LIDAR): the arms meet these, the lidar and the base do not
        self.above_parts: list[dict] = []
        self.above = np.zeros((0, 2))

    def put_above(self, parts) -> "Layout":
        """Things above the lidar's plane, as boxes {"id", "box": (x0, x1, y0, y1)} -> points every 2.5 cm."""
        self.above_parts = [dict(p) for p in parts]
        pts = []
        for p in self.above_parts:
            x0, x1, y0, y1 = p["box"]
            xs, ys = np.arange(x0, x1 + 1e-6, 0.025), np.arange(y0, y1 + 1e-6, 0.025)
            gx, gy = np.meshgrid(xs, ys)
            pts.append(np.c_[gx.ravel(), gy.ravel()])
        self.above = np.vstack(pts) if pts else np.zeros((0, 2))
        return self


def _phantoms(findings_path: Path, grid: Grid):
    try:
        doc = json.loads(findings_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    mask = np.zeros_like(grid.occ)
    for f in doc.get("findings") or []:
        if f.get("kind") == "phantom" and f.get("rc"):
            r, c = np.asarray(f["rc"][0]), np.asarray(f["rc"][1])
            ok = (r >= 0) & (r < grid.h) & (c >= 0) & (c < grid.w)
            mask[r[ok], c[ok]] = True
    return mask


LAYOUTS = {
    # the night of 16 Nov: the moved dock, the nook's contents written in, the two wall
    # fragments under the dock cleared (a robot stood there). `dock` is where it stood THAT
    # night -- the replays of that night use it (load_layout(dock="night")); the pre-flight
    # puts the dock where it is today (todays_dock).
    "20261116": {"truth": ".tmp/map-check/20261116-floor1-dock/clean_plus_footprint.bin",
                 "board": ".tmp/floor1-20261116/map_explore.bin",
                 "findings": ".tmp/map-check/20261116-floor1-dock/findings.json",
                 "dock": (-7.11, -6.78, 169.6)},
    # 4 Nov: the survey nights, the dock where it stood until 16 Nov
    "20261104": {"truth": ".tmp/map-check/20261104-floor1-b/clean_explore.bin",
                 "board": ".tmp/walk-frames/survey1/map.bin",
                 "findings": ".tmp/map-check/20261104-floor1-b/findings.json",
                 "dock": (-7.324, -6.191, 168.8)},
}
#: The night whose furniture the pre-flight walks today's files in (the newest one measured).
CURRENT = "20261116"


def event_hazards() -> list[dict]:
    try:
        doc = json.loads((ROOT / "data/events/floor1_event.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [h for h in doc.get("floor_hazards") or [] if isinstance(h, dict) and "x" in h]


def spots_file_default() -> Path:
    """The robot's own spots file beside the event file (event_host.spots_path)."""
    return EVENT_FILE.with_name(EVENT_FILE.stem + ".spots.json")


def relearnt_dock(spots_file=None) -> dict | None:
    """The dock pose the staff page re-learnt AT THE ROBOT ("วัดแท่นใหม่", owner 19 Nov: the dock
    is put down again at every setup) -- the `dock` of the robot's spots file, read the way the
    robot reads it (event_host._relearnt). None: no file, nothing re-learnt, or a file the robot
    itself could not read either (it then walks by floor1.json's dock, and so does the sim).
    -> {"pose": (x, y, deg), "recorded_at", "source"}"""
    path = Path(spots_file) if spots_file else spots_file_default()
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(saved, dict):
        return None
    from app import event_host as eh

    learnt = eh._relearnt(saved.get("dock"))
    if not learnt:
        return None
    return {"pose": tuple(float(v) for v in learnt["pose"][:3]), "recorded_at": learnt.get("recorded_at"),
            "source": str(path)}


def todays_dock(spots_file=None) -> dict:
    """Where the dock is today, as the robot walks to it: the re-learnt pose when there is one
    (every EVENT walk docks there: event_host.dock_doc lays it over floor1.json's), else
    floor1.json's dock pose. 20 Nov: the sim kept 16 Nov's dock (-7.11, -6.78, 169.6) after the
    real dock was re-learnt at (-7.04, -6.89, 171.0) on 19 Nov -- every way home then "docked"
    10 cm aside in the sim while the robot, walking by the file, sat on its contacts.
    -> {"pose", "from", "file_pose", "relearnt"}. Raises InputError when floor1.json has no dock."""
    dock = _read(ROUTE_FILE, "the floor-1 route file").get("dock")
    if not isinstance(dock, dict) or not isinstance(dock.get("pose"), (list, tuple)) or len(dock["pose"]) < 3:
        raise InputError("the floor-1 route file has no dock pose (%s)" % ROUTE_FILE)
    file_pose = tuple(float(v) for v in dock["pose"][:3])
    # a spots file given (pulled from the robot) first; one with no dock (the marking page's)
    # leaves the robot's own beside the event file to say
    learnt = (relearnt_dock(spots_file) if spots_file else None) or relearnt_dock()
    if learnt:
        return {"pose": learnt["pose"], "from": "re-learnt at the robot %s (%s)" % (learnt.get("recorded_at") or "?", learnt["source"]),
                "file_pose": file_pose, "relearnt": learnt["pose"]}
    return {"pose": file_pose, "from": "floor1.json (%s)" % ROUTE_FILE.name, "file_pose": file_pose, "relearnt": None}


def apply_todays_dock(layout: "Layout", spots_file=None) -> "Layout":
    """Put the layout's (physical) dock where today's files say, and remember the re-learnt pose
    for the event walks' route documents (route_doc). Returns the layout."""
    today = todays_dock(spots_file)
    layout.dock, layout.dock_from, layout.relearnt = tuple(today["pose"]), today["from"], today["relearnt"]
    return layout


def clear_seen_empty(truth: Grid, parts) -> int:
    """Take out of the truth what a later scan saw empty (SEEN_EMPTY): cells within `r` of the pose `at`
    inside the lidar's arc from there, or inside a `box` a map check saw through. -> how many cells."""
    n = 0
    for p in parts:
        rows, cols = np.nonzero(truth.occ)
        px_, py_ = truth.ox + (cols + 0.5) * truth.res, truth.oy + (rows + 0.5) * truth.res
        if "box" in p:                                            # a finding's box the lidar saw through
            x0, x1, y0, y1 = p["box"]
            m = (px_ >= x0) & (px_ <= x1) & (py_ >= y0) & (py_ <= y1)
        else:
            x, y, deg = p["at"]
            a = np.degrees(rl.wrap_pi(np.arctan2(py_ - y, px_ - x) - math.radians(deg)))
            m = (np.hypot(px_ - x, py_ - y) < float(p["r"])) & (a > math.degrees(LIDAR_FROM)) & (a < math.degrees(LIDAR_TO))
        truth.occ[rows[m], cols[m]] = False
        n += int(m.sum())
    return n


def load_layout(name: str = CURRENT, *, dock=None, spots_file=None) -> Layout | None:
    """None when the night's files are not on this machine (they are gitignored).

    `dock`: None = today's dock for the CURRENT night (todays_dock: floor1.json, or the pose
    re-learnt at the robot in `spots_file` / the event's spots file) and that night's own dock
    for any other night; "night" = where the dock stood that night (the replays of a night);
    (x, y, deg) = given."""
    spec = LAYOUTS[name]
    try:
        truth = Grid((ROOT / spec["truth"]).read_bytes())
        board = Grid((ROOT / spec["board"]).read_bytes())
    except OSError:
        return None
    today = dock is None and name == CURRENT
    cleared = clear_seen_empty(truth, SEEN_EMPTY) if today else 0
    lay = Layout(truth, board, spec["dock"], glass=_phantoms(ROOT / spec["findings"], truth),
                 hazards=event_hazards(), name=name)
    lay.put_above(ABOVE_LIDAR)                                    # floor 1's counter: there every night (never moved)
    lay.cleared = cleared
    if today:
        apply_todays_dock(lay, spots_file)
    elif isinstance(dock, (tuple, list)):
        lay.dock, lay.dock_from = tuple(float(v) for v in dock[:3]), "given"
    return lay


def box_gap(points: np.ndarray, pose, box) -> float:
    """Smallest gap between a box (x0, x1, y0, y1) of the robot frame (x ahead, y left) at `pose`
    and the points (negative = overlap). walk_sim.rect_gap is the centred box."""
    if not len(points):
        return 9.0
    x, y, yaw = pose
    c, s = math.cos(yaw), math.sin(yaw)
    dx, dy = points[:, 0] - x, points[:, 1] - y
    lx, ly = dx * c + dy * s, -dx * s + dy * c
    x0, x1, y0, y1 = box
    qx, qy = np.abs(lx - (x0 + x1) / 2) - (x1 - x0) / 2, np.abs(ly - (y0 + y1) / 2) - (y1 - y0) / 2
    d = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0)
    return float(d.min())


# ---- the world -----------------------------------------------------------------------
class FloorWorld:
    """The board, the base, the lidar and the clock for one walk. `start` is a BOARD pose
    (x, y, yaw rad); she starts where the board says (no error), on the dock if that is the
    dock's pose. Adverse cases: `people` [{"at": (x, y), "from_s", "to_s"}], `things`
    [(x, y, r)] left where the board's map has nothing, `lag_s`, `loc` False = a board that
    always knows where it is (to tell a route's fault from the localiser's)."""

    def __init__(self, layout: Layout, start, *, seed: int = 0, roll_off_deg: float = ws.ROLL_OFF_DEG,
                 people=(), things=(), lag_s: float = ws.LAG_S, loc: bool = True, arrive_scale: float = 1.0,
                 nook_slides: float = NOOK_SLIDES,
                 pose_noise_m: float = 0.004, pose_noise_deg: float = 0.15, profile: rp.RobotProfile | None = None):
        self.lay = layout
        # the robot's body, lidar, motion and planner (app/robot_profile.py; Emma = the constants
        # above, the same floats -- tests/test_robot_profile.py)
        p = self.p = profile or rp.EMMA
        self.foot_l, self.foot_w_base, self.foot_w_arms = p.body.length_m, p.body.width_m, p.body.reach_width_m
        self.reach_l = p.body.length_m + 2 * p.body.tray_overhang_m
        self.lidar_from, self.lidar_to = math.radians(p.lidar.from_deg), math.radians(p.lidar.to_deg)
        self.lidar_bin, self.lidar_range, self.lidar_noise = math.radians(p.lidar.bin_deg), p.lidar.sim_range_m, p.lidar.noise_m
        self.spot_rad_s, self.accel = p.motion.spot_turn_rad_s, p.motion.accel_mps2
        self.contact_half = p.docking.contact_half_m if p.docking.contact_half_m is not None else ws.DOCK_CONTACT_HALF_M
        self.rng = np.random.default_rng(seed)
        self.off = math.radians(roll_off_deg)
        dx, dy, ddeg = layout.dock
        self.dx, self.dy = dx, dy
        self.dyaw = math.radians(ddeg) + self.off                 # the dock's TRUE axis
        self.ux, self.uy = math.cos(self.dyaw), math.sin(self.dyaw)
        self.base = ws.SimBase((start[0], start[1], rl.wrap_pi(start[2] + self.off)), np.zeros((0, 2)),
                               seed=seed, noise_m=0.0)
        self.base.queue = [(0.0, 0.0)] * max(1, int(round(lag_s / self.base.dt)))
        self.fit = ws.load_board_walk().MapFit(layout.board.blob)
        self.e = np.zeros(2)                                      # board pose minus true pose
        self.loc, self.arrive_scale = loc, arrive_scale
        self.nook_slides, self._in_nook_view = nook_slides, None
        self.noise_m, self.noise_rad = pose_noise_m, math.radians(pose_noise_deg)
        self.people = [dict(p) for p in people]
        self.things = np.asarray([(x, y) for x, y, _ in things], dtype=float).reshape(-1, 2)
        self.thing_r = np.asarray([r for _, _, r in things], dtype=float)
        self.seen_t = np.full(layout.board.occ.shape, -1e9)      # when the board's lidar last saw a cell (board frame)
        self.cmd = (0.0, 0.0)
        self.action = None
        self.reason = "ok"
        self.moves: list[tuple] = []
        self.speed = 0.3
        self.bumped = False
        self._crop_at = None
        self._loc_at = -1e9
        self._fit_now = self._score(self._board_exact(), self.scan())
        self._q_fit = self._fit_now
        self.low_fit_s = 0.0
        self.was_out, self.arrivals = True, 0
        # what the walk is judged by
        self.min_arm_gap = self.min_base_gap = self.min_person_gap = 9.0
        self.min_hazard = {h.get("id", str(i)): 9.0 for i, h in enumerate(layout.hazards)}
        self.max_err = 0.0
        self.min_q = 99
        self.touched: list[str] = []
        self.arm_gap_at = None
        # the tight places of the walk: the arms' least gap per 0.25 m square she passed, where
        # it was under TIGHT_M (20 Nov: one walk's least gap can sit by the dock's nook while
        # the question was the table half-way -- `min_arm_gap_beyond` asks by place)
        self.tight: dict[tuple[float, float], float] = {}
        # the pageant's arm out to the right (PAGEANT_ARM_BOX) while it is up: its least gap, ON the dock
        # too -- the real arm rises there, EVENT_PAGEANT_LEAD_S (4.5 s) before the line and the wheels (review
        # 21 Nov: a first version judged it only 0.3 m out, as the resting arms, so a thing beside the dock
        # could never fail the gate)
        self.side_arm = None
        self.min_side_arm_gap = 9.0
        self.side_arm_at = None
        self.trace = None                                         # [(t, x, y, yaw)] of the TRUE base, when asked

    def reset_judge(self) -> None:
        """What a walk is judged by, from zero (a sequence of walks in one world: event_day)."""
        self.min_arm_gap = self.min_base_gap = self.min_person_gap = 9.0
        self.min_hazard = {h.get("id", str(i)): 9.0 for i, h in enumerate(self.lay.hazards)}
        self.max_err, self.min_q, self.low_fit_s = 0.0, 99, 0.0
        self.touched, self.arm_gap_at, self.tight = [], None, {}
        self.min_side_arm_gap = 9.0
        self.side_arm_at = None

    def arm_points(self) -> np.ndarray:
        """What the arms meet near her: the truth at the lidar's plane and what stands above it."""
        _, sol = self._crop()
        return np.vstack([sol, self._top]) if len(self._top) else sol

    # -- geometry of the truth --
    def _crop(self):
        b = self.base
        if self._crop_at is None or math.hypot(b.x - self._crop_at[0], b.y - self._crop_at[1]) > 1.5:
            self._crop_at = (b.x, b.y)
            for name, src in (("_ret", self.lay.returns), ("_sol", self.lay.solid), ("_fur", self.lay.furniture),
                              ("_top", self.lay.above)):
                d = np.hypot(src[:, 0] - b.x, src[:, 1] - b.y)
                setattr(self, name, src[d < self.lidar_range + 2.0])
        return self._ret, self._sol

    def _people_now(self) -> np.ndarray:
        t = self.base.t
        out = []
        for p in self.people:
            if p.get("from_s", 0.0) <= t <= p.get("to_s", 1e9):
                at = p["at"]
                if p.get("to"):                                   # walking across: from `at` to `to`
                    f = min(1.0, max(0.0, (t - p.get("from_s", 0.0)) / max(1e-6, p.get("walk_s", 6.0))))
                    at = (at[0] + f * (p["to"][0] - at[0]), at[1] + f * (p["to"][1] - at[1]))
                out.append(at)
        return np.asarray(out, dtype=float).reshape(-1, 2)

    def _extra_returns(self) -> np.ndarray:
        """People and left things, as rings of points the lidar and the base both meet."""
        rings = []
        ang = np.linspace(0, 2 * math.pi, 24, endpoint=False)
        for c in self._people_now():
            rings.append(np.c_[c[0] + PERSON_R * np.cos(ang), c[1] + PERSON_R * np.sin(ang)])
        for c, r in zip(self.things, self.thing_r):
            rings.append(np.c_[c[0] + r * np.cos(ang), c[1] + r * np.sin(ang)])
        return np.vstack(rings) if rings else np.zeros((0, 2))

    def scan(self) -> np.ndarray:
        """Robot-frame lidar returns (x ahead, y left), ray-cast over the truth."""
        ret, _ = self._crop()
        extra = self._extra_returns()
        fur = self._fur
        if len(fur) and FURNITURE_RETURNS < 1.0:
            fur = fur[self.rng.random(len(fur)) < FURNITURE_RETURNS]
        src = np.vstack([ret, fur, extra])
        if not len(src):
            return np.zeros((0, 2))
        b = self.base
        d = src - (b.x, b.y)
        r = np.hypot(d[:, 0], d[:, 1])
        # the lidar looks along the heading the board READS; the base rolls `off` away from it
        # (were the lidar turned with the rolling, no scan would lie on the map: it does)
        a = rl.wrap_pi(np.arctan2(d[:, 1], d[:, 0]) - (b.yaw - self.off))
        m = (r > 0.12) & (r < self.lidar_range) & (a > self.lidar_from) & (a < self.lidar_to)
        r, a = r[m], a[m]
        if not len(r):
            return np.zeros((0, 2))
        nb = int((self.lidar_to - self.lidar_from) / self.lidar_bin)
        idx = ((a - self.lidar_from) / self.lidar_bin).astype(int)
        hw = np.minimum(60, np.ceil(np.arctan2(self.lay.truth.res * 0.6, r) / self.lidar_bin).astype(int))
        depth = np.full(nb, np.inf)
        for k in range(-int(hw.max()), int(hw.max()) + 1):
            sel = hw >= abs(k)
            np.minimum.at(depth, np.clip(idx[sel] + k, 0, nb - 1), r[sel])
        hit = np.isfinite(depth)
        rr = depth[hit] + self.rng.normal(0.0, self.lidar_noise, int(hit.sum()))
        aa = self.lidar_from + (np.nonzero(hit)[0] + 0.5) * self.lidar_bin
        return np.c_[rr * np.cos(aa), rr * np.sin(aa)]

    # -- the board's pose --
    def _board_exact(self):
        b = self.base
        return (b.x + self.e[0], b.y + self.e[1], rl.wrap_pi(b.yaw - self.off))

    def _score(self, pose, pts) -> float:
        return self.fit.score(pose, pts, max_pts=LOC_PTS) if len(pts) >= 20 else 0.0

    def _slip_target(self, fit_true: float) -> np.ndarray:
        """Where the board's pose error is heading: the measured slide facing the nook; with
        `loc="pessimistic"`, the same slide (back along her heading) wherever the fit is low."""
        b = self.base
        if not self.loc:
            return np.zeros(2)
        out, _ = self.out_lat()
        facing_nook = abs(rl.wrap_pi(b.yaw - self.dyaw - math.pi)) < ws.SLIP_ARC
        if self.lay.name == "20261116" and facing_nook and 0.2 < out < ws.SLIP_WITHIN_M:
            if self._in_nook_view is None:                        # one answer for one time facing it
                self._in_nook_view = bool(self.rng.random() < self.nook_slides)
            if self._in_nook_view:
                return ws.SLIP_M * np.array([self.ux, self.uy])
        else:
            self._in_nook_view = None
        if self.loc == "pessimistic" and fit_true < LOW_FIT:
            return -ws.SLIP_M * np.array([math.cos(b.yaw), math.sin(b.yaw)])
        return np.zeros(2)

    def _localise(self) -> None:
        pts = self.scan()
        b = self.base
        fit_true = self._score((b.x, b.y, rl.wrap_pi(b.yaw - self.off)), pts)
        if fit_true < LOW_FIT:
            self.low_fit_s += LOC_PERIOD_S
        target = self._slip_target(fit_true)
        if not target.any():
            self.e = np.zeros(2)                                  # the view is back: it jumps home
        else:
            d = target - self.e
            n = float(np.hypot(*d))
            self.e = target if n <= SLIP_RATE_MPS * LOC_PERIOD_S else self.e + d / n * SLIP_RATE_MPS * LOC_PERIOD_S
        self._note_seen(pts)
        cur = self._score(self._board_exact(), pts)
        self._fit_now = cur
        self._q_fit += 0.4 * (cur - self._q_fit)                  # Q follows, it does not jump
        if self.action is not None and self.action.get("path"):
            # what its lidar has seen since may stand on the way it chose: it plans again
            path = self._plan(self._board_exact(), self.action["goal"])
            if path is not None:
                self.action["path"] = path
            else:
                self.action["held"] += LOC_PERIOD_S               # no way round for now: it stands
                self.action["blocked"] = True
        self.max_err = max(self.max_err, float(np.hypot(*self.e)))
        self.min_q = min(self.min_q, self.quality())

    def _note_seen(self, pts: np.ndarray) -> None:
        if not len(pts):
            return
        x, y, yaw = self._board_exact()
        c, s = math.cos(yaw), math.sin(yaw)
        g = self.lay.board
        row, col = g.rc(x + pts[:, 0] * c - pts[:, 1] * s, y + pts[:, 0] * s + pts[:, 1] * c)
        ok = (row >= 0) & (row < g.h) & (col >= 0) & (col < g.w) & (np.hypot(pts[:, 0], pts[:, 1]) < 5.0)
        self.seen_t[row[ok], col[ok]] = self.base.t

    def pose(self):
        x, y, yaw = self._board_exact()
        n = self.rng.normal(0.0, self.noise_m, 2)
        return (x + n[0], y + n[1], rl.wrap_pi(yaw + self.rng.normal(0.0, self.noise_rad)))

    def quality(self) -> int:
        q = max(Q_FLOOR, Q_SLOPE * self._q_fit + Q_AT_ZERO)
        return int(round(min(78.0, q)))

    # -- the dock --
    def out_lat(self):
        x, y = self.base.x - self.dx, self.base.y - self.dy
        return x * self.ux + y * self.uy, x * -self.uy + y * self.ux

    def power(self) -> dict:
        out, lat = self.out_lat()
        square = abs(rl.wrap_pi(self.base.yaw - self.dyaw)) < math.radians(12)
        on = out <= 0.0 and abs(lat) <= self.contact_half and out > -0.15 and square
        return {"dockingStatus": "on_dock" if on else "not_on_dock", "isCharging": on}

    # -- the rest of the board's REST --
    def events(self) -> list:
        if self.bumped:
            self.bumped = False
            return [{"type": "BUMPER_TRIGGERED"}]
        return []

    def set_speed(self, v) -> None:
        self.speed = min(float(v), self.p.motion.max_speed_mps)

    def current(self):
        return None if self.action is None else {"action_id": len(self.moves)}

    def cancel(self) -> None:
        self.action = None
        self.cmd = (0.0, 0.0)

    def action_reason(self, aid: int) -> str:
        return self.reason

    def explore_map(self) -> bytes:
        return self.lay.board.blob

    def homedocks(self) -> list:
        return []

    def mapping(self, on: bool) -> None:
        pass

    # -- what the board knows, for its plans (board frame) --
    def _known(self, r0: int, r1: int, c0: int, c1: int) -> np.ndarray:
        g = self.lay.board
        return g.occ[r0:r1, c0:c1] | (self.base.t - self.seen_t[r0:r1, c0:c1] < SEEN_KEEP_S)

    def _nearest_known(self, x: float, y: float, reach: float = 1.0) -> tuple[float, float]:
        """(distance to the board's own map, distance to what its lidar saw lately)."""
        g = self.lay.board
        row, col = (int(v) for v in g.rc(x, y))
        n = int(reach / g.res)
        r0, r1, c0, c1 = max(0, row - n), min(g.h, row + n + 1), max(0, col - n), min(g.w, col + n + 1)
        out = []
        for mask in (g.occ[r0:r1, c0:c1],
                     (self.base.t - self.seen_t[r0:r1, c0:c1] < SEEN_KEEP_S) & ~g.occ[r0:r1, c0:c1]):
            rr, cc = np.nonzero(mask)
            if not len(rr):
                out.append(9.0)
                continue
            px, py = g.ox + (cc + c0 + 0.5) * g.res, g.oy + (rr + r0 + 0.5) * g.res
            out.append(float(np.hypot(px - x, py - y).min()))
        return out[0], out[1]

    def _plan(self, start, goal) -> list[tuple[float, float]] | None:
        """The board's way from `start` to `goal` (board frame) round what it knows."""
        g = self.lay.board
        pad = 3.0
        x0, x1 = min(start[0], goal[0]) - pad, max(start[0], goal[0]) + pad
        y0, y1 = min(start[1], goal[1]) - pad, max(start[1], goal[1]) + pad
        (r0, r1), (c0, c1) = g.rc([x0, x1], [y0, y1])[0], g.rc([x0, x1], [y0, y1])[1]
        r0, r1, c0, c1 = max(0, int(r0)), min(g.h, int(r1)), max(0, int(c0)), min(g.w, int(c1))
        blocked = dilate(self._known(r0, r1, c0, c1), int(round(self.p.planner.inflate_m / g.res)))
        # where it stands is where it stands: a planner clears its own footprint
        sr, sc = (int(v) for v in g.rc(start[0], start[1]))
        yy, xx = np.ogrid[r0:r1, c0:c1]
        blocked &= ~((yy - sr) ** 2 + (xx - sc) ** 2 <= int(0.55 / g.res) ** 2)

        def free(ax, ay, bx, by) -> bool:
            n = max(2, int(math.hypot(bx - ax, by - ay) / (g.res * 0.7)))
            t = np.linspace(0.0, 1.0, n)
            rr, cc = g.rc(ax + t * (bx - ax), ay + t * (by - ay))
            rr, cc = rr - r0, cc - c0
            ok = (rr >= 0) & (rr < blocked.shape[0]) & (cc >= 0) & (cc < blocked.shape[1])
            return bool(ok.all()) and not bool(blocked[rr, cc].any())

        if free(start[0], start[1], goal[0], goal[1]):
            return [tuple(goal)]
        # a shortest way on a 10 cm grid
        coarse = blocked[: blocked.shape[0] // 2 * 2, : blocked.shape[1] // 2 * 2]
        coarse = coarse.reshape(coarse.shape[0] // 2, 2, coarse.shape[1] // 2, 2).any(axis=(1, 3))
        res = g.res * 2

        def cell(x, y):
            return int((y - (g.oy + r0 * g.res)) / res), int((x - (g.ox + c0 * g.res)) / res)

        def centre(rc):
            return g.ox + c0 * g.res + (rc[1] + 0.5) * res, g.oy + r0 * g.res + (rc[0] + 0.5) * res

        s, t = cell(*start[:2]), cell(*goal[:2])
        H, W = coarse.shape
        if not (0 <= t[0] < H and 0 <= t[1] < W) or coarse[t]:
            return None
        open_, came, cost = [(0.0, s)], {s: None}, {s: 0.0}
        while open_:
            _, cur = heapq.heappop(open_)
            if cur == t:
                break
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                nx = (cur[0] + dr, cur[1] + dc)
                if not (0 <= nx[0] < H and 0 <= nx[1] < W) or coarse[nx]:
                    continue
                c = cost[cur] + (1.4142 if dr and dc else 1.0)
                if c < cost.get(nx, 1e18):
                    cost[nx], came[nx] = c, cur
                    heapq.heappush(open_, (c + math.hypot(nx[0] - t[0], nx[1] - t[1]), nx))
        if t not in came:
            return None
        cells = []
        cur = t
        while cur is not None:
            cells.append(cur)
            cur = came[cur]
        pts = [centre(c) for c in reversed(cells)][1:-1] + [tuple(goal)]
        # pull the string: skip what a straight line reaches
        out, at = [], (start[0], start[1])
        i = 0
        while i < len(pts):
            j = len(pts) - 1
            while j > i and not free(at[0], at[1], pts[j][0], pts[j][1]):
                j -= 1
            out.append(pts[j])
            at, i = pts[j], j + 1
        return out

    def move_to(self, x: float, y: float, yaw) -> int:
        self.moves.append((round(x, 2), round(y, 2), None if yaw is None else round(math.degrees(yaw), 1)))
        bp = self._board_exact()
        d_map, d_seen = self._nearest_known(x, y)
        refuse = d_map < self.p.planner.refuse_map_m or d_seen < self.p.planner.refuse_seen_m
        miss = float(self.rng.choice(ARRIVE_M)) * self.arrive_scale
        ang = self.rng.uniform(-math.pi, math.pi)
        goal = (x + miss * math.cos(ang), y + miss * math.sin(ang))
        if math.hypot(x - bp[0], y - bp[1]) < 0.20:
            # already there by its own tolerance (16 Nov: the legs to the pre-dock spot from
            # the pre-dock spot ended 0.04-0.18 m off without a turn): it only takes the heading
            goal = (bp[0], bp[1])
        path = None if refuse else self._plan(bp, goal)
        v = self.action["v"] if self.action else 0.0
        self.action = {"goal": goal, "yaw": yaw, "path": path, "v": v, "held": 0.0, "t0": self.base.t,
                       "face_err": self.rng.uniform(-math.radians(ARRIVE_DEG), math.radians(ARRIVE_DEG)),
                       "fail": "CANNOT_REACH_TARGET" if path is None else None, "aim": (x, y)}
        self.reason = "ok"
        return len(self.moves)

    def _strip_blocked(self) -> bool:
        """Something NEW in the strip ahead that the board's plan did not know: it holds."""
        b = self.base
        extra = self._extra_returns()
        if not len(extra):
            return False
        d = extra - (b.x, b.y)
        c, s = math.cos(b.yaw), math.sin(b.yaw)
        lx, ly = d[:, 0] * c + d[:, 1] * s, -d[:, 0] * s + d[:, 1] * c
        return bool(((lx > self.foot_l / 2) & (lx < self.foot_l / 2 + 0.55) & (np.abs(ly) < self.foot_w_base / 2 + 0.05)).any())

    def _advance(self, dt: float) -> None:
        a, b = self.action, self.base
        if a["fail"]:
            # it turns toward the point, stands, and says it cannot (16 Nov: 5-10 s)
            bx, by, byaw = self._board_exact()
            err = rl.wrap_pi(math.atan2(a["aim"][1] - by, a["aim"][0] - bx) - byaw)
            if abs(err) > 0.05 and b.t - a["t0"] < 4.0:
                b.yaw = rl.wrap_pi(b.yaw + math.copysign(min(abs(err), self.spot_rad_s * dt), err))
            if b.t - a["t0"] >= 5.0:
                self.reason, self.action = a["fail"], None
            return
        bx, by, byaw = self._board_exact()
        path = a["path"]
        wp = path[0]
        d = math.hypot(wp[0] - bx, wp[1] - by)
        if len(path) > 1 and d < 0.25:
            path.pop(0)
            return
        if len(path) == 1 and d < 0.03:
            a["v"] = 0.0
            if a["yaw"] is None:
                self.action = None
                return
            err = rl.wrap_pi(a["yaw"] + a["face_err"] - byaw)
            if abs(err) < 0.01:
                self.action = None
                return
            b.yaw = rl.wrap_pi(b.yaw + math.copysign(min(abs(err), self.spot_rad_s * dt), err))
            return
        err = rl.wrap_pi(math.atan2(wp[1] - by, wp[0] - bx) - byaw)
        held = self._strip_blocked() or bool(a.pop("blocked", False))
        if held:
            a["held"] += dt
            if a["held"] > BOARD_GIVE_UP_S:
                self.reason, self.action = "PATH_OCCUPIED", None
                return
        else:
            a["held"] = 0.0
        if abs(err) > 0.35 and d > 0.10:
            target, w = 0.0, math.copysign(self.spot_rad_s, err)
        else:
            target = 0.0 if held else min(self.speed, math.sqrt(2 * 0.25 * d) + 0.03)
            w = max(-0.6, min(0.6, 2.0 * err))
        a["v"] += max(-self.accel * dt, min(self.accel * dt, target - a["v"]))
        nyaw = rl.wrap_pi(b.yaw + w * dt)
        nx, ny = b.x + a["v"] * math.cos(nyaw) * dt, b.y + a["v"] * math.sin(nyaw) * dt
        b.yaw = nyaw
        if self._base_gap((nx, ny, nyaw)) <= 0.0:                 # the base itself against something solid
            a["v"] = 0.0
            self._touch("base")
            self.bumped = True
            self.reason, self.action = "BUMPER", None
            return
        b.x, b.y = nx, ny

    def _base_gap(self, pose) -> float:
        _, sol = self._crop()
        extra = self._extra_returns()
        pts = np.vstack([sol, extra]) if len(extra) else sol
        return ws.rect_gap(pts, pose, self.foot_l, self.foot_w_base)

    def _touch(self, what: str) -> None:
        b = self.base
        self.touched.append("%s at (%.2f, %.2f) t=%.0fs" % (what, b.x, b.y, b.t))

    def _judge(self) -> None:
        b = self.base
        _, sol = self._crop()
        arms = self.arm_points()                                  # + the counter's top: the arms meet it
        gap_arms = ws.rect_gap(arms, b.pose, self.reach_l, self.foot_w_arms)
        gap_base = ws.rect_gap(sol, b.pose, self.foot_l, self.foot_w_base)
        out, _ = self.out_lat()
        if self.side_arm is not None:
            g = box_gap(arms, b.pose, self.side_arm)
            if g < self.min_side_arm_gap:                         # on the dock too (above)
                self.min_side_arm_gap, self.side_arm_at = g, (round(b.x, 2), round(b.y, 2))
        if out > 0.30:                                            # on the dock she is against it by design
            if gap_arms <= 0.0 and self.min_arm_gap > 0.0:
                self._touch("arms")
            if gap_arms < self.min_arm_gap:
                self.arm_gap_at = (round(b.x, 2), round(b.y, 2))
            self.min_arm_gap = min(self.min_arm_gap, gap_arms)
            if gap_arms < TIGHT_M:
                key = (round(b.x * 4) / 4, round(b.y * 4) / 4)
                self.tight[key] = min(self.tight.get(key, 9.0), gap_arms)
            self.min_base_gap = min(self.min_base_gap, gap_base)
        extra = self._extra_returns()
        if len(extra):
            g = ws.rect_gap(extra, b.pose, self.reach_l, self.foot_w_arms)
            if g <= 0.0 and self.min_person_gap > 0.0:
                self._touch("a person or a thing left there" + ("" if self.action is not None else " (ours: a line we drive ourselves)"))
            self.min_person_gap = min(self.min_person_gap, g)
        for i, h in enumerate(self.lay.hazards):
            k = h.get("id", str(i))
            self.min_hazard[k] = min(self.min_hazard[k], math.hypot(b.x - h["x"], b.y - h["y"]))

    # -- the clock (board_walk's `time`) --
    def monotonic(self) -> float:
        return self.base.t

    def sleep(self, s: float) -> None:
        b = self.base
        for _ in range(max(1, int(round(s / b.dt)))):
            if self.action is not None:
                self._advance(b.dt)
                b.t += b.dt
                b.v = b.w = 0.0
            else:
                before = (b.x, b.y)
                ret, _ = self._crop()
                near = ret[np.hypot(ret[:, 0] - b.x, ret[:, 1] - b.y) < 2.0]
                extra = self._extra_returns()
                b.env = np.vstack([near, extra]) if len(extra) else near
                b.step(*self.cmd)
                if abs(b.v) > 1e-3 and self._base_gap(b.pose) <= 0.0 and self.out_lat()[0] > 0.30:
                    b.x, b.y = before                             # against something: the wheels stop
                    b.v = 0.0
                    if not self.touched or not self.touched[-1].startswith("base"):
                        self._touch("base (ours: a line we drive ourselves)")
                    self.bumped = True
                out, lat = self.out_lat()
                if out < -ws.DOCK_BODY_M and abs(lat) < 0.45:     # the dock's body
                    b.x += (-ws.DOCK_BODY_M - out) * self.ux
                    b.y += (-ws.DOCK_BODY_M - out) * self.uy
                    b.v = 0.0
            if b.t - self._loc_at >= LOC_PERIOD_S:
                self._loc_at = b.t
                self._localise()
            self._judge()
            if self.trace is not None:
                self.trace.append((b.t, b.x, b.y, b.yaw))


# ---- one walk --------------------------------------------------------------------------
#: Out of the box (event_host `walk_out_of_box`, owner 23 Nov "หุ่นอยู่ในกล่อง ... เดินออกมาก่อน จึงค่อยยกมือ"): the walk
#: goes with the arms down and the pageant's arm is out only where she ARRIVED, standing -- from its start to its end:
#: event_pageant.MIN_STANDING_S (one turn each way; longer than the recorded line's 7.1 s after its 4.5 s lead) and
#: the slow lowering (3 s).
WAVE_STAND_S = 18.7 + 3.0


def _wave_standing(w: "FloorWorld") -> dict:
    """The standing pageant where the walk left her (`walk_out_of_box`): its arm box's least gap."""
    w.min_side_arm_gap, w.side_arm_at = 9.0, None
    w.side_arm = PAGEANT_ARM_BOX
    w.sleep(WAVE_STAND_S)
    w.side_arm = None
    return {"min_side_arm_gap": round(w.min_side_arm_gap, 3), "side_arm_at": w.side_arm_at}


def run_walk(layout: Layout, doc: dict, steps: list[dict], start, *, seed: int = 0, max_s: float = 1500.0,
             settle_s: float = 2.0, pageant: bool = False, wave_after: bool = False, **world) -> dict:
    """The real Walker over `steps` (a route file's document `doc`) from board pose `start`
    (x, y, yaw deg), after standing there `settle_s` (the board's lidar has looked round, Q
    has settled). `pageant`: the pageant's arm is out to the right the whole walk (the debut's
    walk ahead before 23 Nov, PAGEANT_ARM_BOX); `wave_after`: out only once she arrived, standing
    (`_wave_standing`: the walk out of the box). Returns how it ended and what it came near."""
    w = FloorWorld(layout, (start[0], start[1], math.radians(start[2])), seed=seed, **world)
    w.sleep(settle_s)
    if pageant:
        w.side_arm = PAGEANT_ARM_BOX
    t_start = w.base.t
    end, reason, said, events = _drive(w, doc, steps, max_s)
    w.cmd = (0.0, 0.0)
    w.action = None
    w.sleep(1.0)
    res = _walk_result(w, end, reason, said, events, w.base.t - t_start - 1.0)
    if pageant:
        res.update(min_side_arm_gap=round(w.min_side_arm_gap, 3), side_arm_at=w.side_arm_at)
    elif wave_after and end == "done":
        res.update(_wave_standing(w))
    return res


WHEEL_SCALE, WHEEL_TURN, WHEEL_FRAME_RAD = 0.98, 0.975, 2.1


def wheel_odo(w: "FloorWorld") -> tuple[float, float, float]:
    """getodopose of the simulated base: its own frame, its own scale -- from where the base really is, never the board's
    pose (a relocalization does not move it)."""
    b = w.base
    st = getattr(w, "_wheels", None)
    if st is None:
        st = w._wheels = {"true": (b.x, b.y, b.yaw), "odo": (3.0, -1.0, WHEEL_FRAME_RAD)}
    tx, ty, tyaw = st["true"]
    dx, dy = b.x - tx, b.y - ty
    c, s = math.cos(tyaw), math.sin(tyaw)
    ahead, left = dx * c + dy * s, -dx * s + dy * c            # the true step in the base's own frame
    dyaw = (b.yaw - tyaw + math.pi) % (2 * math.pi) - math.pi
    ox, oy, oyaw = st["odo"]
    co, so = math.cos(oyaw), math.sin(oyaw)
    ox += WHEEL_SCALE * (ahead * co - left * so)
    oy += WHEEL_SCALE * (ahead * so + left * co)
    oyaw = (oyaw + WHEEL_TURN * dyaw + math.pi) % (2 * math.pi) - math.pi
    st["true"], st["odo"] = (b.x, b.y, b.yaw), (ox, oy, oyaw)
    return ox, oy, oyaw


def _drive(w: "FloorWorld", doc: dict, steps: list[dict], max_s: float) -> tuple[str, str, list, list]:
    """One board_walk run of `steps` in the world as it stands -- a fresh Walker from a freshly read
    board_walk.py, as event_walk spawns one per walk. -> (end, reason, said, events)."""
    import app.slamware_sdk as sw

    bw = ws.load_board_walk()
    t_start = w.base.t
    bw.time = w

    class Session:
        def __init__(self, sdk, *a, **k):
            self.last_renew = None

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            w.cmd = (0.0, 0.0)

        def set(self, vx, om):
            w.cmd = (vx, om)
            return False

    walker = bw.Walker(w, doc, False, False)
    walker.sdk = object()
    said: list[str] = []
    events: list[dict] = []
    walker.say = said.append
    walker.event = lambda **f: events.append(f)
    walker.scan = w.scan
    walker.screen = lambda action: said.append("screen %s (not simulated)" % action)
    # the wheels' odometry the glides go by (24 Nov, on the robot: scale 0.974-0.989 over four legs, 1-2 deg of turn a leg)
    walker._glide_odo = lambda: wheel_odo(w)

    class TooLong(Exception):
        pass

    real_sleep = w.sleep

    def sleep(s):
        if w.base.t - t_start > max_s:
            raise TooLong()
        real_sleep(s)

    w.sleep = sleep
    real = sw.VelocitySession
    sw.VelocitySession = Session
    end, reason = "done", ""
    try:
        walker.run(steps, 1)
    except bw.Stop as e:
        end, reason = "stop", str(e)
    except TooLong:
        end, reason = "stop", "simulated time ran out (%.0f s)" % max_s
    finally:
        sw.VelocitySession = real
    w.sleep = real_sleep
    return end, reason, said, events


def _walk_result(w: "FloorWorld", end: str, reason: str, said: list, events: list, s: float) -> dict:
    """run_walk's result for the world as the walk left it."""
    out, lat = w.out_lat()
    on = w.power()["dockingStatus"] == "on_dock"
    legs = []
    for line in said:
        if " m off, Q" in line and "->" in line:
            try:
                legs.append((float(line.split(" m off, Q")[0].rsplit(" ", 1)[1]), int(line.split(" m off, Q")[1].split(",")[0].split()[0])))
            except (ValueError, IndexError):
                pass
    return {"end": end, "reason": reason, "s": round(s, 1), "on_dock": on,
            "aside_m": round(lat, 3) if on else None,
            "crooked_deg": round(math.degrees(rl.wrap_pi(w.base.yaw - w.dyaw)), 2) if on else None,
            "pose": (round(w.base.x, 2), round(w.base.y, 2), round(math.degrees(w.base.yaw), 1)),
            "board_pose": tuple(round(v, 2) for v in w._board_exact()[:2]),
            "min_arm_gap": round(w.min_arm_gap, 3), "min_base_gap": round(w.min_base_gap, 3), "arm_gap_at": w.arm_gap_at,
            "min_person_gap": round(w.min_person_gap, 3), "min_hazard": {k: round(v, 2) for k, v in w.min_hazard.items()},
            "max_err_m": round(w.max_err, 2), "min_q": w.min_q, "low_fit_s": round(w.low_fit_s, 0),
            "touched": w.touched, "legs": legs,
            "tight": {"%.2f,%.2f" % k: round(v, 3) for k, v in sorted(w.tight.items())},
            "moves": w.moves, "said": said, "events": events}


def min_arm_gap_beyond(res: dict, at, m: float) -> float:
    """The arms' least gap in a walk (run_walk's result) counting only the places more than `m`
    from `at` (x, y): the tight places away from, say, the dock. 9.0 = none under TIGHT_M."""
    gaps = [g for k, g in res.get("tight", {}).items()
            if math.hypot(float(k.split(",")[0]) - at[0], float(k.split(",")[1]) - at[1]) > m]
    return min(gaps) if gaps else 9.0


def stop_kind(reason: str) -> str:
    r = reason.lower()
    for key, kind in (("gave up", "board gave up"), ("cannot", "board gave up"), ("ended", "board gave up"),
                      ("not on the line", "not on the dock's line"), ("before the reverse", "not on the dock's line"),
                      ("aside", "off the dock's line"), ("quality", "lost (Q)"), ("does not lie", "lost (fit)"),
                      ("jumped", "pose jumped"), ("something", "something ahead"), ("no room", "no room to turn"),
                      ("stuck", "held (stuck)"), ("too long", "held (too long)"), ("wheels", "wheels stopped"),
                      ("board event", "bumper"), ("not on the dock", "not on the dock"), ("time ran out", "sim time")):
        if key in r:
            return kind
    return "other"


# ---- what to walk ----------------------------------------------------------------------
ROUTE_FILE = ROOT / "data" / "robot" / "waypoints" / "floor1.json"
EVENT_FILE = ROOT / "data" / "events" / "floor1_event.json"
CEREMONY_FILE = ROOT / "data" / "ceremony" / "floor1_ceremony.json"
#: The dock's keys board_walk reads (a package's dock carries notes and sources beside them).
DOCK_KEYS = ("pose", "approach", "estimated", "back_from_m", "axis_offset_deg", "leave_by_approach")


class InputError(Exception):
    """Something the pre-flight needs cannot be read, or nothing was asked of it: an error,
    never a pass (review 17 Nov: `--only nosuchwalk` and an unreadable floor1.json both ended
    "0 walk(s), 0 outside the bounds", exit 0)."""


def _read(path: Path, what: str) -> dict:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as e:
        raise InputError("%s cannot be read (%s): %s" % (what, path, type(e).__name__)) from None
    except ValueError as e:
        raise InputError("%s is not JSON (%s): %s" % (what, path, e)) from None
    if not isinstance(doc, dict):
        raise InputError("%s is not a JSON object (%s)" % (what, path))
    return doc


def route_doc(layout: Layout, *, event: bool = False) -> dict:
    """The document a walk runs under, as the robot writes it: floor1.json's dock (pose,
    approach, the way home) -- a floor1.json path run by board_walk reads the file as it is --
    and for an event walk what app/event_walk.py writes round it, with the dock pose re-learnt
    at the robot laid over the file's (event_host.dock_doc) when there is one."""
    given = getattr(layout, "route_dock", None)
    if given:
        # a layout from a map package carries its own dock (pose, approach, the way home)
        dock = {k: v for k, v in given.items() if k in DOCK_KEYS}
        doc = {"dock": dock}
        if event:
            from app import event_walk

            doc.update(events=True, patience_s=event_walk.PATIENCE_S, min_quality=event_walk.MIN_QUALITY,
                       min_fit=event_walk.MIN_FIT)
        return doc
    dock = _read(ROUTE_FILE, "the floor-1 route file").get("dock")
    if not isinstance(dock, dict) or "pose" not in dock:
        raise InputError("the floor-1 route file has no dock (%s)" % ROUTE_FILE)
    dock = {k: v for k, v in dock.items() if k != "note"}
    if layout.name != CURRENT:                                   # another night's dock: the old way home
        dock = {"pose": list(layout.dock), "approach": dock.get("approach"), "estimated": False}
    elif event and getattr(layout, "relearnt", None):
        dock["pose"] = list(layout.relearnt)
    doc = {"dock": dock}
    if event:
        from app import event_walk

        doc.update(events=True, patience_s=event_walk.PATIENCE_S, min_quality=event_walk.MIN_QUALITY,
                   min_fit=event_walk.MIN_FIT)
    return doc


def _in_the_room(steps: list[dict]) -> bool:
    """A path that goes through the multimedia room's door: the truth has the door closed
    (16 Nov) and the room was never walked for it -- not simulated, and said so."""
    for s in steps:
        pts = [s["to"]] if "to" in s else [q[:2] for q in s.get("creep") or s.get("glide") or []]
        if any(y < -7.9 for _, y in pts):
            return True
    return False


def floor1_paths() -> dict[str, list[dict]]:
    paths = _read(ROUTE_FILE, "the floor-1 route file").get("paths")
    if not isinstance(paths, dict) or not paths:
        raise InputError("the floor-1 route file has no paths (%s)" % ROUTE_FILE)
    return {name: list(p["steps"]) for name, p in paths.items()}


def load_spots(path) -> dict[str, dict]:
    """Positions for spots, from a file a person made: the marking page's
    {"marked": [{"id", "name", "x", "y", "face_deg", "nearest_thing_m"}, ...], "not_marked": [...]}
    or the robot's own recorded spots (`<event>.spots.json`, event_host._save_recorded:
    {"spots": {id: {"pose": [x, y, deg]}}}). -> {id: {"x", "y", "face_deg", "name", "source"}}."""
    doc = _read(Path(path), "the spots file")
    out: dict[str, dict] = {}
    if isinstance(doc.get("marked"), list):
        for m in doc["marked"]:
            try:
                sid = str(m["id"])
                face = m.get("face_deg")
                out[sid] = {"x": float(m["x"]), "y": float(m["y"]), "face_deg": None if face is None else float(face),
                            "name": str(m.get("name") or sid), "source": "marked"}
            except (KeyError, TypeError, ValueError):
                raise InputError("the spots file has a mark that is not {id, x, y}: %r" % (m,)) from None
    elif isinstance(doc.get("spots"), dict):
        for sid, sp in doc["spots"].items():
            pose = sp.get("pose") if isinstance(sp, dict) else None
            if not isinstance(pose, (list, tuple)) or len(pose) < 2:
                continue
            face = pose[2] if len(pose) > 2 else None
            out[str(sid)] = {"x": float(pose[0]), "y": float(pose[1]), "face_deg": None if face is None else float(face),
                             "name": str(sid), "source": "recorded on the robot"}
    else:
        raise InputError("the spots file has neither `marked` (the marking page) nor `spots` (the robot's own file): %s" % path)
    if not out and not relearnt_dock(path):
        # the robot's file right after "วัดแท่นใหม่" holds the dock alone: that is a position too
        raise InputError("the spots file holds no position (%s)" % path)
    return out


def event_plan(layout: Layout, spots: dict[str, dict] | None = None) -> dict:
    """Everything the event and the ceremony can ask her to walk, through the event layer's
    own route builders -- and, said out loud, everything they name that has no position.

    -> {"walks": {name: (steps, start)}, "where": {spot id: (x, y, face|None, name)},
        "standing": [ids given a position that no file walks to (custom_N)],
        "not_checked": [str]}"""
    from app import event_host as eh
    from app import event_tour as et
    from app.config import settings

    if not EVENT_FILE.exists():
        raise InputError("the event file cannot be read (%s)" % EVENT_FILE)
    old = settings.event_file
    settings.event_file = str(EVENT_FILE)
    eh._cache.update(key=None)
    try:
        doc = eh.load()
        if not doc or not doc.get("spots"):
            raise InputError("the event file has no spots (%s)" % EVENT_FILE)
        dock = eh.dock_doc(doc) or {"pose": list(layout.dock)}
        if getattr(layout, "relearnt", None):
            # the re-learnt pose this layout was given (it may come from a spots file pulled
            # from the robot, not the one beside the event file that dock_doc reads)
            dock = dict(dock, pose=list(layout.relearnt))
        all_spots = {sid: dict(sp) for sid, sp in doc["spots"].items()}
        standing = []
        for sid, m in (spots or {}).items():
            if sid in all_spots:
                all_spots[sid]["pose"] = [m["x"], m["y"], m["face_deg"]]
                all_spots[sid]["steps"] = None                    # a position given by a person wins (as a recorded one does)
                all_spots[sid]["given"] = m["source"]
            else:
                standing.append(sid)
        # a spot that walks on from one given here (card 3's guest_front from beside_host) starts where it was given
        # and turns by its heading (event_host.follow_spot; second review 22 Nov: --spots moved beside_host only)
        raw_spots = json.loads(EVENT_FILE.read_text(encoding="utf-8")).get("spots") or {}
        for sid, sp in all_spots.items():
            base = sp.get("from_spot")
            if isinstance(base, str) and base in (spots or {}) and (raw_spots.get(sid) or {}).get("steps"):
                m = spots[base]
                sp["steps"] = eh.follow_spot(raw_spots[sid]["steps"], [m["x"], m["y"], m["face_deg"]],
                                             eh.file_heading(raw_spots.get(base)))
        walkable = {sid: sp for sid, sp in all_spots.items() if sp.get("dock") or sp.get("pose") or sp.get("steps")}
        not_checked = ["spot %s (%s): no position" % (sid, sp.get("name_th") or sid)
                       for sid, sp in all_spots.items() if sid not in walkable]

        def at(sid):
            sp = walkable[sid]
            if sp.get("dock"):
                return tuple(dock["pose"])
            if sp.get("pose"):
                q = sp["pose"]
                return (q[0], q[1], 90.0 if q[2] is None else q[2])
            x, y, face = steps_end(sp["steps"])
            return (x, y, 0.0 if face is None else face)

        where = {}
        for sid, sp in walkable.items():
            x, y, _ = at(sid)
            face = None if sp.get("dock") else (sp["pose"][2] if sp.get("pose") else steps_end(sp["steps"])[2])
            where[sid] = (x, y, face, sp.get("name_th") or sid)
        walks, pageant, wave_after = {}, set(), set()
        for a in walkable:
            for b in walkable:
                if a == b:
                    continue
                steps = eh.spot_steps(walkable[b])
                if steps is None:
                    continue
                start = at(a)
                # the server's own builder for every walk to a spot (event_host.walk_steps, 23 Nov): its big turns
                # watched by us, from where she starts
                walks["event %s -> %s" % (a, b)] = (et.leg_steps(walkable[b], start, dock), start)
                if et.head_on_point(walkable[b]) is not None:
                    # a walk that ends in the handshake there comes straight in from its head-on point (owner 23 Nov:
                    # card 1's counter leg and its by-hand twin, card 2 -- pressed from wherever she is)
                    walks["event %s -> %s head-on" % (a, b)] = (et.leg_steps(walkable[b], start, dock, head_on=True), start)
        for sc in doc.get("scenes") or []:
            raw = next((r for r in json.loads(EVENT_FILE.read_text(encoding="utf-8")).get("scenes", []) if r.get("id") == sc["id"]), {})
            want = raw.get("spot")
            box = bool(sc.get("walk_out_of_box"))              # out of the box: ALWAYS ahead (event_host._walk_to)
            if want and want not in walkable and not box:
                not_checked.append("scene %s walks to %s: no position" % (sc["id"], want))
            if sc.get("forward_m") and (box or not want or want not in walkable):
                # event_host._walk_ahead (its spot has no position): straight ahead from where she stands --
                # on the event day out of the "box", the dock (owner 20 Nov). Simulated since 21 Nov; before,
                # a "not simulated" line here
                words, keys = debut_arm(sc) if sc.get("pageant") else ("", {})
                name = "event %s: %.1f m ahead out of the dock%s" % (
                    sc["id"], float(sc["forward_m"]), ", " + words if words else "")
                walks[name] = (debut_steps(sc), tuple(dock["pose"]))
                if keys.get("pageant"):
                    pageant.add(name)
                if keys.get("wave_after"):
                    wave_after.add(name)
            for stop in (raw.get("roam") or {}).get("stops") or []:
                if stop not in walkable:
                    not_checked.append("scene %s roams to %s: no position" % (sc["id"], stop))
        # the ceremony's own walk steps, in its order (data/ceremony/floor1_ceremony.json)
        try:
            cer = _read(CEREMONY_FILE, "the ceremony file")
        except InputError:
            cer = None
            not_checked.append("the ceremony file is not on this machine: its walk steps are not looked at")
        prev = None
        for part in (cer or {}).get("order") or []:
            for st in part.get("steps") or []:
                if st.get("do") == "opening":
                    # card 1 as the ceremony's first step (owner 23 Nov): its walks are card 1's tour, simulated as
                    # TOUR_NAME -- it leaves her at its counter, where the ceremony's next walk sets out from
                    sc = next((s for s in doc.get("scenes") or [] if s.get("id") == st.get("scene")), None)
                    counter = ((sc or {}).get("tour") or {}).get("counter")
                    if counter in walkable:
                        prev = counter
                    continue
                if st.get("do") != "walk" or not st.get("spot"):
                    continue
                sid = st["spot"]
                if sid not in walkable:
                    not_checked.append("ceremony part %s walks to %s: no position" % (part.get("n"), sid))
                    continue
                if prev is None:
                    # she comes out of the stage box, which no file gives a position: from the dock here
                    start, frm = tuple(dock["pose"]), "dock (the box she comes out of has no position)"
                else:
                    start, frm = at(prev), prev
                steps = eh.spot_steps(walkable[sid])
                walks["ceremony %s: %s -> %s" % (part.get("n"), frm, sid)] = (et.leg_steps(walkable[sid], start, dock), start)
                prev = sid
        if prev is not None:
            walks["ceremony end: %s -> dock" % prev] = (eh.route_from([{"do": "dock"}], at(prev)[:2], dock), at(prev))
        return {"walks": walks, "where": where, "standing": standing, "not_checked": not_checked, "pageant": pageant,
                "wave_after": wave_after}
    finally:
        settings.event_file = old
        eh._cache.update(key=None)


def steps_end(steps: list[dict]) -> tuple[float, float, float | None]:
    """Where a spot's `steps` leave her with nobody in the way: the last point's place, its heading or the last
    turn's, and a `forward` straight on along that heading for its whole length (card 3's spot since 22 Nov ends
    turned round and a straight walk up to the guest -- its `meet_m` ends it sooner when someone is there)."""
    x = y = face = None
    for st in steps:
        if st.get("to"):
            x, y = float(st["to"][0]), float(st["to"][1])
            face = float(st["face"]) if st.get("face") is not None else face
        elif st.get("do") == "turn":
            face = float(st["face"])
        elif "forward" in st and x is not None and face is not None:
            x += float(st["forward"]) * math.cos(math.radians(face))
            y += float(st["forward"]) * math.sin(math.radians(face))
    if x is None:
        raise InputError("a spot's steps have no point to go to: %r" % (steps,))
    return (x, y, face)


def debut_steps(scene: dict) -> list[dict]:
    """The walk event_host._walk_ahead writes for a scene with `forward_m` (a parsed scene, eh.load's):
    tests/test_floor_sim.py holds the two to the same steps."""
    from app import event_host as eh

    step = {"forward": float(scene["forward_m"]), "speed": eh.FORWARD_SPEED, "label": "เดินออกไปข้างหน้า"}
    if scene.get("pageant") and not scene.get("walk_out_of_box"):
        step["right_clear_m"] = eh.PAGEANT_RIGHT_CLEAR_M
    if scene.get("walk_out_of_box"):
        step["short_ok"] = False                          # all of it, or not out (event_host.ahead_steps)
    return [step]


def debut_arm(scene: dict) -> tuple[str, dict]:
    """How the debut's pageant arm goes with its walk: (the label's words, the leg's keys). Out of the box (owner 23
    Nov, `walk_out_of_box`): arms down on the way, the arm out once arrived (`wave_after`); before it, out all the way."""
    if scene.get("walk_out_of_box"):
        return "arms down, the pageant arm out at the end", {"wave_after": True}
    return "the pageant arm out", {"pageant": True}


def event_walks(layout: Layout, spots: dict[str, dict] | None = None) -> dict[str, tuple[list[dict], tuple]]:
    return {name[len("event "):] if name.startswith("event ") else name: v
            for name, v in event_plan(layout, spots)["walks"].items()}


_REAL_FRAMES: list | None = None


def _real_frames() -> list:
    """(x, y, yaw, robot-frame points) of the 16 Nov walk frames, when they are on this machine."""
    global _REAL_FRAMES
    if _REAL_FRAMES is None:
        _REAL_FRAMES = []
        for run in sorted((ROOT / ".tmp/floor1-20261116/walk-frames").glob("20261116-2*")):
            try:
                lines = (run / "frames.jsonl").read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                q = r.get("pose")
                a = np.asarray(r.get("scan") or [], dtype=float).reshape(-1, 2)
                if q and len(a) >= 50:
                    _REAL_FRAMES.append((q["x"], q["y"], q["yaw"], np.c_[a[:, 1] * np.cos(a[:, 0]), a[:, 1] * np.sin(a[:, 0])]))
    return _REAL_FRAMES


def spot_report(layout: Layout, where: dict[str, tuple], *, min_fit: float = 0.20, min_quality: int = 40,
                keep_out_below_y: float | None = None, profile: rp.RobotProfile | None = None) -> list[dict]:
    """Each spot on its own, as a place to stand and to start a walk from: room for the body
    and for a turn, whether the board would take it as a target, the start gates by heading
    (simulated scan on the BOARD's map; real frames taken there when there are any), the
    dock's slide zone, floor hazards, the keep-out line."""
    p = profile or rp.EMMA
    reach_l, reach_w = p.body.length_m + 2 * p.body.tray_overhang_m, p.body.reach_width_m
    board, only = layout.board.points(), layout.furniture
    corner = p.body.turn_radius_m
    ddeg = layout.dock[2]
    ux, uy = math.cos(math.radians(ddeg)), math.sin(math.radians(ddeg))
    # what the arms meet: the lidar plane's truth and what stands above it (the counter's top, ABOVE_LIDAR)
    above = getattr(layout, "above", np.zeros((0, 2)))
    arm_solid = np.vstack([layout.solid, above]) if len(above) else layout.solid
    rows = []
    for sid, (x, y, face, name) in where.items():
        solid = float(np.hypot(arm_solid[:, 0] - x, arm_solid[:, 1] - y).min())
        d_map = float(np.hypot(board[:, 0] - x, board[:, 1] - y).min())
        d_seen = float(np.hypot(only[:, 0] - x, only[:, 1] - y).min()) if len(only) else 9.0
        headings = [face] if face is not None else [k * 45.0 for k in range(8)]
        body = min(ws.rect_gap(arm_solid, (x, y, math.radians(h)), reach_l, reach_w) for h in headings)
        fits, refused_at = {}, []
        w = FloorWorld(layout, (x, y, 0.0), seed=0, roll_off_deg=0.0, profile=p)
        for k in range(8):
            h = k * 45.0 - 180.0
            w.base.yaw = math.radians(h)
            f = sorted(w._score((x, y, w.base.yaw), w.scan()) for _ in range(3))[1]
            q = int(round(max(Q_FLOOR, Q_SLOPE * f + Q_AT_ZERO)))
            fits[int(h)] = round(f, 2)
            if f < min_fit or q < min_quality:
                refused_at.append(int(h))
        real = [f for f in _real_frames() if math.hypot(f[0] - x, f[1] - y) < 0.45]
        real_row = None
        if real:
            fr = np.array([w.fit.score((f[0], f[1], f[2]), f[3], 400) for f in real])
            hd = np.array([math.degrees(f[2]) for f in real])
            under = sorted({int(round(v / 45.0)) * 45 for v in hd[fr < min_fit]})
            real_row = {"n": len(real), "median": round(float(np.median(fr)), 2), "under": round(float((fr < min_fit).mean()), 2),
                        "headings_under": under}
        out = (x - layout.dock[0]) * ux + (y - layout.dock[1]) * uy
        lat = (x - layout.dock[0]) * -uy + (y - layout.dock[1]) * ux
        in_arc = out > 0.2 and abs(math.atan2(lat, out)) < ws.SLIP_ARC
        hz = {h.get("id", str(i)): (round(math.hypot(x - h["x"], y - h["y"]), 2), float(h.get("radius_m", 0.0)))
              for i, h in enumerate(layout.hazards)}
        rows.append({
            "id": sid, "name": name, "x": x, "y": y, "face": face, "nearest_solid_m": round(solid, 2),
            "arm_gap_m": round(body, 2), "turn_gap_m": round(solid - corner, 2),
            "to_board_map_m": round(d_map, 2), "to_seen_thing_m": round(d_seen, 2),
            "board_refuses": d_map < p.planner.refuse_map_m or d_seen < p.planner.refuse_seen_m,
            "fit_by_heading": fits, "sim_refused_at": refused_at, "real": real_row,
            "out_on_dock_line_m": round(out, 2),
            "slide_zone": "inside (the leg into it from further out faces the nook where the pose slid)"
            if in_arc and out < ws.SLIP_WITHIN_M else
            ("near (one real frame slid at 2.02 m out)" if in_arc and out < 2.1 else "outside"),
            "hazards": hz, "below_keep_out": None if keep_out_below_y is None else round(y - keep_out_below_y, 2)})
    return rows


def spot_lines(rows: list[dict], profile: rp.RobotProfile | None = None, *, gates: bool = True) -> list[str]:
    """`gates`: the start gates (lidar fit / Q on the board's map) are Emma's board's; a robot
    with another localiser is not judged by them."""
    p = profile or rp.EMMA
    out = []
    for r in rows:
        bad = []
        if r["id"] == "dock" and not gates:
            out.append("  %-18s (%6.2f, %6.2f)  Emma's dock (this robot docks on its own: its walks home end at the "
                       "dock's approach point)" % (r["id"], r["x"], r["y"]))
            continue
        if r["id"] == "dock":
            # she sits against it: no body / turn / target question -- but what the board's map
            # holds right there is the 16 Nov finding (two wall fragments under the dock)
            out.append("  %-18s (%6.2f, %6.2f)  the dock itself (she sits against it; walks leave from the spot in front "
                       "of it). The board's own map has a cell %.2f m from the dock pose%s" % (
                           r["id"], r["x"], r["y"], r["to_board_map_m"],
                           " -- a stale wall fragment where she stands" if r["to_board_map_m"] < 0.3 else ""))
            continue
        if r["arm_gap_m"] < p.min_gap_m:
            bad.append("the body does not fit (arms %.2f m from something)" % r["arm_gap_m"])
        if r["turn_gap_m"] < 0.03:
            bad.append("no room for a full turn (the arms' corner reaches %.2f m, the nearest thing is %.2f m away)" % (
                p.body.turn_radius_m, r["nearest_solid_m"]))
        if r["board_refuses"]:
            bad.append("the board would refuse it as a target (%.2f m from its map, %.2f m from a thing it sees)" % (
                r["to_board_map_m"], r["to_seen_thing_m"]))
        if not gates:
            pass
        elif r["real"] and r["real"]["under"] > 0.05:
            bad.append("REAL frames here: %.0f%% under the start gate (fit %.2f median, n=%d; headings %s) -- a walk started "
                       "here facing that way is refused 'set the pose first'" % (
                           100 * r["real"]["under"], r["real"]["median"], r["real"]["n"], r["real"]["headings_under"]))
        elif r["sim_refused_at"]:
            bad.append("simulated start gate under its bar facing %s deg" % r["sim_refused_at"])
        if r["slide_zone"] != "outside" and gates:                  # Emma's board's slide
            bad.append("the dock's slide zone: " + r["slide_zone"])
        for k, (d, rad) in r["hazards"].items():
            if d < rad:
                bad.append("%.2f m from floor hazard %s (keeps %.2f)" % (d, k, rad))
        if r["below_keep_out"] is not None and r["below_keep_out"] < 0:
            bad.append("%.2f m past the keep-out line (no_walk_up_below_y)" % -r["below_keep_out"])
        if not gates:
            # another robot: its own body; the board's map/seen distances against ITS planner's
            # refusal; no fit or Q (Emma's board's localiser)
            out.append("  %-18s (%6.2f, %6.2f)%s  solid %.2f m  widest %.2f  turn %+.2f  map %.2f / seen %.2f%s" % (
                r["id"][:18], r["x"], r["y"], "" if r["face"] is None else " %4.0f deg" % r["face"], r["nearest_solid_m"],
                r["arm_gap_m"], r["turn_gap_m"], r["to_board_map_m"], r["to_seen_thing_m"],
                "" if not bad else "\n      <-- " + "\n      <-- ".join(bad)))
            continue
        low = min(r["fit_by_heading"].values())
        out.append("  %-18s (%6.2f, %6.2f)%s  solid %.2f m  arms %.2f  turn %+.2f  board map %.2f / seen %.2f  fit %.2f..%.2f%s%s" % (
            r["id"][:18], r["x"], r["y"], "" if r["face"] is None else " %4.0f deg" % r["face"], r["nearest_solid_m"],
            r["arm_gap_m"], r["turn_gap_m"], r["to_board_map_m"], r["to_seen_thing_m"], low, max(r["fit_by_heading"].values()),
            "" if not r["real"] else "  real fit %.2f (n=%d)" % (r["real"]["median"], r["real"]["n"]),
            "" if not bad else "\n      <-- " + "\n      <-- ".join(bad)))
    return out


#: The reception counter's spot (event file `beside_host`, owner 20 Nov: "เดินขวามาที่หน้าเคาน์เตอร์ เป็นจุดที่
#: มิกิจะพูด"), facing the counter: where the ways home of the event day start.
COUNTER_SPOT = (-8.62, -4.2, 0.0)


def dock_line_pose(layout: Layout, out_m: float) -> tuple[float, float, float]:
    """The board pose `out_m` out on the dock's line (the file's `axis_offset_deg` turns it), facing out."""
    dock = _read(ROUTE_FILE, "the floor-1 route file").get("dock") or {}
    line = math.radians(layout.dock[2] + float(dock.get("axis_offset_deg") or 0.0))
    return (layout.dock[0] + out_m * math.cos(line), layout.dock[1] + out_m * math.sin(line), layout.dock[2])


def starts_for(name: str, steps: list[dict], layout: Layout) -> list[tuple]:
    """Board poses a named floor1.json path is started from."""
    if steps and steps[0].get("do") == "undock":
        return [layout.dock]
    if steps and "forward" in steps[0]:
        # 20 Nov: a path that begins with a walk ahead begins on the dock -- floor1.json writes one only for
        # the debut's walk out of the box (event_counter_test, n20j); before, it was started at the reception
        return [layout.dock]
    if name == "dock_via_reception":
        # 20 Nov: home from in front of the dock (n20k: 0.14 m from the spot she reverses from) and from the
        # counter (the event day's way home, n20l came this way)
        far = float((_read(ROUTE_FILE, "the floor-1 route file").get("dock") or {}).get("back_from_m") or 1.9)
        return [dock_line_pose(layout, far - 0.14), COUNTER_SPOT]
    if name == "to_dock":
        # + 20 Nov: from 0.14 m in front of the spot she reverses from (n20k pressed it there) and from the
        # counter (the event's "กลับแท่น" from beside_host is this same dock walk)
        far = float((_read(ROUTE_FILE, "the floor-1 route file").get("dock") or {}).get("back_from_m") or 1.9)
        return [(-9.0, -5.2, 90.0), (-8.6, 0.2, 100.0), (-11.0, -6.9, -131.0), (-8.5, -3.0, -90.0),
                dock_line_pose(layout, far - 0.14), COUNTER_SPOT]
    if name == "multimedia_to_dock":
        return [(-9.0, -5.2, 90.0), (-8.6, 0.2, 100.0), (-11.0, -6.9, -131.0), (-8.5, -3.0, -90.0)]
    if name == "turn_out":
        return [(-7.97, -6.55, 4.0), (-8.4, -6.5, -18.0)]
    return [(-9.0, -5.2, 90.0)]


def adverse_cases(steps: list[dict], start, rng) -> dict[str, dict]:
    """The same walk with something wrong: a person standing on a leg for a while, one
    walking across, a thing left on a leg (not on the board's map), a slow base."""
    pts = [(start[0], start[1])] + [tuple(p) for s in steps for p in ([s["to"]] if "to" in s else s.get("glide") or [])]
    legs = [(a, b) for a, b in zip(pts, pts[1:]) if math.hypot(b[0] - a[0], b[1] - a[1]) > 2.4]
    out = {"late base": {"lag_s": 0.8}}
    if legs:
        a, b = legs[int(rng.integers(len(legs)))]
        f = rng.uniform(0.45, 0.6)
        mid = (a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1]))
        nx, ny = -(b[1] - a[1]), b[0] - a[0]
        n = math.hypot(nx, ny)
        nx, ny = nx / n, ny / n
        out["person stands on a leg (20 s)"] = {"people": [{"at": mid, "from_s": 0.0, "to_s": 1e9, "leave_after_seen_s": 20.0}]}
        out["person walks across"] = {"people": [{"at": (mid[0] - 2 * nx, mid[1] - 2 * ny),
                                                   "to": (mid[0] + 2 * nx, mid[1] + 2 * ny),
                                                   "from_s": 0.0, "to_s": 1e9, "walk_s": 1e9, "cross_when_near_m": 1.6}]}
        out["thing left on a leg"] = {"things": [(mid[0] + 0.1 * nx, mid[1] + 0.1 * ny, 0.2)]}
    return out


# ---- people who react to her (the adverse cases) ---------------------------------------
def _arm_people(w: FloorWorld) -> None:
    """A person "on a leg" leaves 20 s after she first comes within 2 m; one "walking across"
    sets off when she is near. Called once a tick through FloorWorld._people_now."""
    b = w.base
    for p in w.people:
        d = math.hypot(p["at"][0] - b.x, p["at"][1] - b.y)
        # a guest of one card (`leg`) starts counting only while that card walks (review 21 Nov: card 2's guest
        # started 4 s into card 1 and stood in card 2's way 8 s, not 20)
        mine = p.get("leg") is None or p.get("leg") == getattr(w, "leg_index", None)
        if p.get("leave_after_seen_s") and "seen_at" not in p and d < 2.0 and mine:
            p["seen_at"] = b.t
            p["to_s"] = b.t + p["leave_after_seen_s"]
        if p.get("cross_when_near_m") and "set_off" not in p:
            mid = ((p["at"][0] + p["to"][0]) / 2, (p["at"][1] + p["to"][1]) / 2)
            if math.hypot(mid[0] - b.x, mid[1] - b.y) < p["cross_when_near_m"] + 1.0:
                p["set_off"] = True
                p["from_s"], p["walk_s"], p["to_s"] = b.t, 5.0, b.t + 5.0


_people_now_plain = FloorWorld._people_now


def _people_now(self):
    _arm_people(self)
    return _people_now_plain(self)


FloorWorld._people_now = _people_now


# ---- the event day in one world (owner 20 Nov) -----------------------------------------
# The owner's order for the opening (20 Nov evening, CHANGELOG): (1) the debut -- out of the "box" (the dock)
# straight ahead in the pageant pose; (2) right, to the counter where Khun Miki speaks, 60-70 cm from the table,
# facing it, the handshake raised; (3) turning to shake guests' hands -- deferred, not here. Then home: the
# event's own "กลับแท่น" (event_host.go_home: after a walk on the map the line out of the dock is forgotten,
# so it is the map's dock walk, board_walk `dock`) or floor1.json's `dock_via_reception` (n20l).
#: How long she stands at the counter before the way home: the handshake's own 13 s (BodyGestures
#: HANDSHAKE_EST_MS) and a little.
COUNTER_STAND_S = 15.0
#: Within this of the counter's spot the trace's poses are her turns there (the board's last turn to face the
#: table, and the first of the way home).
COUNTER_TURN_R_M = 0.35
#: Card 3 (owner 22 Nov: "ตอนจับมือกับคุณมิกิ ก็ให้หันหลังเดินไปจับมือกับแขก"): from the counter she turns round
#: and walks straight up to the guest (board_walk `forward` with `meet_m`). WHERE the guest stands nobody has said:
#: ASSUMED this far from the counter's spot, straight ahead of her after the turn -- a guest invited forward, ~1.3 m
#: in front of her. The guest stands there all day here (seen by the board on the cards before: it is the room).
HANDSHAKE_GUEST_AT_M = 1.9
#: How long she stands with the guest before the way home: the handshake's 13 s and a little (as the counter).
GUEST_STAND_S = 15.0
#: In front of card 3's guest: the guest within the walk's meet distance and this (the stop's lag lands inside it,
#: a guest past it was never met); her heading within this of the turn's.
GUEST_MET_SLACK_M = 0.05
GUEST_FACE_TOL_DEG = 10.0


def _event_scenes_and_spots() -> tuple[list[dict], dict, dict | None]:
    """The event file as event_host parses it: (scenes, spots, the route file's dock)."""
    from app import event_host as eh
    from app.config import settings

    if not EVENT_FILE.exists():
        raise InputError("the event file cannot be read (%s)" % EVENT_FILE)
    old = settings.event_file
    settings.event_file = str(EVENT_FILE)
    eh._cache.update(key=None)
    try:
        doc = eh.load()
        if not doc or not doc.get("spots"):
            raise InputError("the event file has no spots (%s)" % EVENT_FILE)
        return list(doc.get("scenes") or []), dict(doc["spots"]), eh.dock_doc(doc)
    finally:
        settings.event_file = old
        eh._cache.update(key=None)


def event_day_sequences(layout: Layout) -> dict[str, list[dict]]:
    """{name: the walks of the event day, in order}: the debut's walk ahead out of the dock (the pageant arm
    out), the walk to the counter (`beside_host`, built where she stands as event_walk builds it: out by the
    dock's corridor), the handshake standing there, and home -- two ways. {} when the event file has no
    debut walk ahead or no counter spot (said by the caller)."""
    from app import event_host as eh
    from app import event_tour as et

    scenes, spots, dock = _event_scenes_and_spots()
    # The staff page's CARDS, as they are pressed on the day (owner 21 Nov: "ลองซีนในจำลองตามฉากที่เดินให้หน่อย
    # ... ทำการ์ดกดเป็นซีนวันจริง"): the debut's card, the counter's card (the scene that walks to beside_host
    # and gives the handshake), the card that goes home (the scene whose spot is the dock). A card changed in the
    # event file = this sequence changed; no card = no sequence (said by the caller), not a spot taken behind it.
    card = {s["id"]: i + 1 for i, s in enumerate(scenes)}
    debut = next((s for s in scenes if s.get("forward_m") and s.get("pageant")), None)
    counter_sc = next((s for s in scenes if s.get("spot") == "beside_host" and s.get("gesture") == "handshake"), None)
    home_sc = next((s for s in scenes if s.get("spot") and (spots.get(s["spot"]) or {}).get("dock")), None)
    counter = spots.get(counter_sc["spot"]) if counter_sc else None
    if debut is None or counter_sc is None or home_sc is None or eh.spot_steps(counter) is None:
        return {}
    # card 3 (owner 22 Nov): the handshake scene whose spot walks up to someone (a `forward` with `meet_m`)
    guest_sc = next((s for s in scenes if s.get("gesture") == "handshake" and s is not counter_sc
                     and any("meet_m" in st for st in (eh.spot_steps(spots.get(s.get("spot"))) or []))), None)
    ev = route_doc(layout, event=True)
    dock = dict(dock or {}, **ev["dock"])                         # the pose the event walks dock to
    start = tuple(ev["dock"]["pose"])

    def built(spot, head_on=False):
        # the server's builder for a walk to a spot (event_host.walk_steps -> event_tour.leg_steps), from her pose then
        return lambda pose: et.leg_steps(spot, (pose[0], pose[1], math.degrees(pose[2])), dock, head_on=head_on)

    words, keys = debut_arm(debut)
    # out of the box: she stands where she arrived while the hand is up (WAVE_STAND_S, `_wave_standing`), then on
    head = [dict({"what": "card %d %s: %.1f m ahead, %s" % (card[debut["id"]], debut["id"], debut["forward_m"], words),
                  "doc": ev, "steps": debut_steps(debut), "start": start, "after_s": 3.0}, **keys)]
    head += [   # card 2 gives the hand there: head-on, as event_host._walk_to builds it (owner 23 Nov)
            {"what": "card %d %s: to the counter (%s), the handshake there" % (card[counter_sc["id"]], counter_sc["id"],
                                                                                counter_sc["spot"]),
             "doc": ev, "steps": built(counter, head_on=True), "handshake": True, "after_s": COUNTER_STAND_S}]
    tag = "counter"
    if guest_sc is not None:
        steps = eh.spot_steps(spots[guest_sc["spot"]])
        turn = next((st for st in steps if st.get("do") == "turn"), None)
        cx, cy, cdeg = counter_spot() or COUNTER_SPOT
        h = math.radians(float(turn["face"]) if turn else cdeg + 180.0)
        at = (cx + HANDSHAKE_GUEST_AT_M * math.cos(h), cy + HANDSHAKE_GUEST_AT_M * math.sin(h))
        head.append({"what": "card %d %s: to the guest (%s), the handshake there" % (card[guest_sc["id"]], guest_sc["id"],
                                                                                    guest_sc["spot"]),
                     "doc": ev, "steps": built(spots[guest_sc["spot"]]), "guest_handshake": True, "after_s": GUEST_STAND_S,
                     "guests": [{"at": at, "from_s": 0.0, "to_s": 1e9, "handshake_guest": True}]})
        tag = "counter > guest"
    home_event = {"what": "card %d %s: home, the event's dock walk" % (card[home_sc["id"]], home_sc["id"]), "doc": ev,
                  "steps": built(spots[home_sc["spot"]])}
    seqs = {"event day: dock > ahead > %s > home by the event's dock walk" % tag: head + [home_event]}
    try:
        via = floor1_paths().get("dock_via_reception")
    except InputError:
        via = None
    if via:
        seqs["event day: dock > ahead > %s > home by dock_via_reception" % tag] = head + [
            {"what": "home: floor1.json dock_via_reception", "doc": route_doc(layout), "steps": [dict(s) for s in via]}]
    return seqs


# ---- card 1's tour in one world (owner 23 Nov) ---------------------------------------------------------------
# "การ์ดแรก ให้หุ่นเปิดตัวตามที่ซ้อมไว้ ... จากนั้นไปหน้าเคาน์เตอร์แล้วจับมือมิกิ แล้วหันหลัง ไปจับมือกับแขกสักสามจุดใกล้ๆตรงนั้น
# แล้วก็กลับมา ... หน้าเคาน์เตอร์": ONE press -- the debut, the counter's handshake, each guest spot's handshake, back to
# the counter facing it (app/event_tour.py), then the day's way home. Its legs are built where she stands as the server
# builds them (event_tour.leg_steps: the event's route + a watched slow turn first when the point is behind her).
#: WHERE the guests stand nobody has said yet (their spots are to be marked: guest_1..3 have no position). ASSUMED,
#: when no position is given: a row TOUR_GUESTS_AT_M from the counter's spot along her heading turned round (card 3's
#: HANDSHAKE_GUEST_AT_M), TOUR_GUESTS_APART_M apart across it; each guest spot = the robot facing that guest, its front
#: TOUR_FRONT_TO_GUEST_M from the guest's body (the owner's 60-70 cm at the counter). The guests stand there all day.
TOUR_GUESTS_AT_M = HANDSHAKE_GUEST_AT_M
TOUR_GUESTS_APART_M = 0.8
TOUR_FRONT_TO_GUEST_M = 0.65
#: In front of a tour guest after the walk: the base's front face this near to the guest at most (a guest further off
#: was never reached; the spot puts it at TOUR_FRONT_TO_GUEST_M, the board's MoveTo misses by up to 0.18 m -- ARRIVE_M),
#: her heading within GUEST_FACE_TOL_DEG of the spot's. At the counter again: the same heading tolerance, and the front
#: face where card 2's own MoveTo to that spot puts it -- the owner's 60-70 cm and the board's miss (card 2's leg read
#: 0.53-0.83 m over the 20 runs of 23 Nov; the gate never judged it): a wrong spot or a leg that stopped short is what
#: this catches, not the board's spread.
TOUR_FRONT_MAX_M = 0.90
TOUR_BACK_FRONT_M = (0.45, 0.95)
#: The tour's own end spot (owner 24 Nov): she stands this near it. A judged number, not a measured one.
TOUR_END_TOL_M = 0.30
TOUR_NAME = "card 1 tour: dock > ahead > counter > guests > counter > home by the event's dock walk"
#: The same card with a guest spot not marked (23 Nov: one guest card 3's way, event_tour.planned `walk_up`).
TOUR_WALK_UP_NAME = "card 1 tour, guest spots not marked: dock > ahead > counter > one guest ahead > counter > home"


def tour_guest_places(counter, n: int) -> list[tuple[tuple[float, float, float], tuple[float, float]]]:
    """ASSUMED: [((robot x, y, heading deg), (guest x, y))] for `n` guests in a row across her turned-round heading
    from the counter's spot -- the first on her right as she turns round (north of the counter here)."""
    cx, cy, cdeg = counter
    h = math.radians(cdeg + 180.0)
    ux, uy = math.cos(h), math.sin(h)
    nx, ny = -uy, ux                                             # across: her left when facing h
    mid = (cx + TOUR_GUESTS_AT_M * ux, cy + TOUR_GUESTS_AT_M * uy)
    back = FOOT_L / 2 + TOUR_FRONT_TO_GUEST_M + PERSON_R
    out = []
    for k in range(n):
        off = ((n - 1) / 2.0 - k) * TOUR_GUESTS_APART_M * -1.0   # k = 0 on her right, then across to her left
        g = (mid[0] + off * nx, mid[1] + off * ny)
        out.append(((round(g[0] - back * ux, 3), round(g[1] - back * uy, 3), round(math.degrees(h), 1)), g))
    return out


def tour_sequences(layout: Layout, spots: dict[str, dict] | None = None) -> dict[str, list[dict]]:
    """{TOUR_NAME: legs} for card 1's tour, or {} (no scene with a `tour`, no debut walk, no counter card home).
    Guest spots: a position given (`spots`, the --spots file) or recorded/in the event file wins; else ASSUMED
    (tour_guest_places) -- said in every leg's label. A guest spot with no position also gives TOUR_WALK_UP_NAME:
    what the card does then (its `walk_up`, 23 Nov). Every leg is checked for a turn round the board would make
    with no watched turn first (`rear_check`, event_tour.rear_turns)."""
    from app import event_host as eh
    from app import event_tour

    scenes, all_spots, dock = _event_scenes_and_spots()
    card = {s["id"]: i + 1 for i, s in enumerate(scenes)}
    debut = next((s for s in scenes if s.get("tour") and s.get("forward_m") and s.get("pageant")), None)
    home_sc = next((s for s in scenes if s.get("spot") and (all_spots.get(s["spot"]) or {}).get("dock")), None)
    if debut is None or home_sc is None:
        return {}
    tour = debut["tour"]
    spots = spots or {}

    def given(sid):
        if sid in spots:
            m = spots[sid]
            return [float(m["x"]), float(m["y"]), float(m["face_deg"]) if m.get("face_deg") is not None else None], \
                "given (%s)" % m.get("source", "--spots")
        sp = all_spots.get(sid) or {}
        if sp.get("pose") is not None:
            return list(sp["pose"]), "recorded" if sp.get("recorded_at") else "the event file's"
        return None, None

    counter = dict(all_spots[tour["counter"]])
    cpose, csrc = given(tour["counter"])
    if cpose is not None:
        counter.update(pose=cpose, steps=None)
    if eh.spot_steps(counter) is None:
        return {}
    cx, cy, cdeg = (cpose[0], cpose[1], cpose[2] or 0.0) if cpose is not None else (counter_spot() or COUNTER_SPOT)
    assumed = tour_guest_places((cx, cy, cdeg), len(tour["guests"]))
    ev = route_doc(layout, event=True)
    dock = dict(dock or {}, **ev["dock"])
    start = tuple(ev["dock"]["pose"])

    def built(spot, head_on=False, via=None, end=False):
        return lambda pose: event_tour.leg_steps(spot, (pose[0], pose[1], math.degrees(pose[2])), dock, head_on=head_on,
                                                 via=via, glide=tour.get("glide"), end=end)

    words, keys = debut_arm(debut)
    # the counter's walk ends in Khun Miki's handshake: straight in from its head-on point (owner 23 Nov, the plan's
    # `head_on`); the guests' walks too where a spot asks for it; the walk back after the guests does not
    legs = [dict({"what": "card %d debut: %.1f m ahead, %s" % (card[debut["id"]], debut["forward_m"], words),
                  "doc": ev, "steps": debut_steps(debut), "start": start, "after_s": 3.0}, **keys),
            {"what": "card %d (tour): to the counter (%s), the handshake there" % (card[debut["id"]], tour["counter"]),
             "doc": ev, "steps": built(counter, head_on=not tour.get("via"), via=tour.get("via")), "handshake": True,
             "after_s": COUNTER_STAND_S,
             "rear_check": True}]
    for k, gid in enumerate(tour["guests"]):
        pose, src = given(gid)
        guest_at = None
        if pose is None or pose[2] is None:
            (rx, ry, rdeg), guest_at = assumed[k]
            pose, src = [rx, ry, rdeg], "ASSUMED place"
        if guest_at is None:
            # a given spot: the guest stands in front of it at the same distance
            h = math.radians(pose[2])
            back = FOOT_L / 2 + TOUR_FRONT_TO_GUEST_M + PERSON_R
            guest_at = (pose[0] + back * math.cos(h), pose[1] + back * math.sin(h))
        spot = {"name_th": (all_spots.get(gid) or {}).get("name_th") or gid, "pose": pose}
        legs.append({"what": "card %d (tour): to the guest %s (%s), the handshake there" % (card[debut["id"]], gid, src),
                     "doc": ev, "steps": built(spot, head_on=True), "tour_guest": True, "spot_pose": tuple(pose),
                     "guest_at": guest_at,
                     "after_s": GUEST_STAND_S, "rear_check": True,
                     "guests": [{"at": guest_at, "from_s": 0.0, "to_s": 1e9, "tour_guest": gid}]})
    back = {"what": "card %d (tour): back to the counter (%s), facing it" % (card[debut["id"]], tour["counter"]),
            "doc": ev, "steps": built(counter), "counter_back": True, "spot_pose": (cx, cy, cdeg),
            "after_s": 3.0, "rear_check": True}
    end = dict(all_spots.get(tour.get("end")) or {})
    epose, _esrc = given(tour["end"]) if tour.get("end") else (None, None)
    if epose is not None and epose[2] is not None:
        # the tour's own end spot (owner 24 Nov): where the round ends, not the handshake's place at the counter
        end.update(pose=epose, steps=None)
        back.update(what="card %d (tour): to the end spot (%s)" % (card[debut["id"]], tour["end"]), steps=built(end, end=True),
                    spot_pose=tuple(epose), end_spot=True)
    home = {"what": "card %d %s: home, the event's dock walk" % (card[home_sc["id"]], home_sc["id"]), "doc": ev,
            "steps": built(all_spots[home_sc["spot"]]) if end else built_plain(eh.spot_steps(all_spots[home_sc["spot"]]), dock)}
    if tour.get("via") or tour.get("end"):
        # Khun Miki, standing in front of her spot all the while she is there (review 24 Nov: the walk home and the guests'
        # walks pass her and the simulator had nobody there); the marked round only -- the older tours are as they were
        hm = math.radians(cdeg)
        back_m = FOOT_L / 2 + TOUR_FRONT_TO_GUEST_M + PERSON_R
        miki = {"at": (cx + back_m * math.cos(hm), cy + back_m * math.sin(hm)), "from_s": 0.0, "to_s": 1e9, "tour_guest": "miki"}
        for leg in legs[1:] + [back, home]:
            leg["guests"] = list(leg.get("guests") or []) + [dict(miki)]
    legs += [back, home]
    seqs = {TOUR_NAME: legs}
    # a guest spot not marked: what the card does TODAY (owner 23 Nov: "ส่วนจุดแขกตอนนี้ให้หุ่นหันหลังแล้วเดินไปตรงๆก็เจอคน
    # แล้วทักทายได้ไหม") -- one guest card 3's way, its `walk_up` spot (guest_front: turned round at the counter, straight up
    # to whoever stands there, event_tour.planned), the guest where card 3's is ASSUMED (HANDSHAKE_GUEST_AT_M ahead)
    wid = tour.get("walk_up")
    unmarked = any(given(g)[0] is None or given(g)[0][2] is None for g in tour["guests"])
    if unmarked and wid in all_spots:
        wu = dict(all_spots[wid])
        raw = json.loads(EVENT_FILE.read_text(encoding="utf-8")).get("spots") or {}
        if tour["counter"] in spots and (raw.get(wid) or {}).get("steps"):
            # the counter given here: the walk-up starts there and turns by its heading (event_host.follow_spot)
            wu["steps"] = eh.follow_spot(raw[wid]["steps"], cpose, eh.file_heading(raw.get(tour["counter"])))
        turn = next((st for st in reversed(eh.spot_steps(wu) or []) if st.get("do") == "turn"), None)
        h = math.radians(float(turn["face"]) if turn else cdeg + 180.0)
        at = (cx + HANDSHAKE_GUEST_AT_M * math.cos(h), cy + HANDSHAKE_GUEST_AT_M * math.sin(h))
        seqs[TOUR_WALK_UP_NAME] = [dict(legs[0]), dict(legs[1]), {
            "what": "card %d (tour): to the guest ahead (%s: guest spots not marked), the handshake there" % (
                card[debut["id"]], wid),
            "doc": ev, "steps": built(wu), "guest_handshake": True, "after_s": GUEST_STAND_S, "rear_check": True,
            "guests": [{"at": at, "from_s": 0.0, "to_s": 1e9, "handshake_guest": True}]}, dict(back), dict(home)]
    return seqs


def built_plain(steps: list[dict], dock: dict):
    from app import event_host as eh

    return lambda pose: eh.route_from([dict(s) for s in steps], (pose[0], pose[1]), dock)


def _at_the_tour_guest(w: "FloorWorld", spot_pose, guest_at) -> dict:
    """Standing at a tour guest's spot: the base's front face and the handshake's hand to that guest, her heading
    against the spot's."""
    b = w.base
    pose = b.pose
    base_box = (-w.foot_l / 2, w.foot_l / 2, -w.foot_w_base / 2, w.foot_w_base / 2)
    ang = np.linspace(0, 2 * math.pi, 24, endpoint=False)
    ring = np.c_[guest_at[0] + PERSON_R * np.cos(ang), guest_at[1] + PERSON_R * np.sin(ang)]
    face = spot_pose[2]
    return {"front_to_guest": round(box_gap(ring, pose, base_box), 3),
            "hand_to_guest": round(box_gap(ring, pose, HANDSHAKE_BOX), 3),
            "face_err_deg": None if face is None else round(abs((math.degrees(b.yaw) - face + 180.0) % 360.0 - 180.0), 1),
            "at": (round(b.x, 3), round(b.y, 3), round(math.degrees(b.yaw), 1)), "guest_at": tuple(guest_at)}


def tour_summary(seqs: list[dict]) -> dict:
    """card 1's tour over a set of runs: in front of each guest, at the counter again, the turns round."""
    guests, back, rear = [], [], []
    for s in seqs:
        for p in s["parts"]:
            if p.get("tour_guest"):
                guests.append(dict(p["tour_guest"], what=p["what"]))
            if p.get("counter_back"):
                back.append(p["counter_back"])
            rear += ["%s: %s" % (p["what"], r) for r in p.get("rear_turns") or []]
    return {"guests": guests, "back": back, "rear": sorted(set(rear))}


def tour_faults(sm: dict) -> list[str]:
    """What is wrong with card 1's tour over a set of runs (tour_summary)."""
    bad = []
    g = sm["guests"]
    if g and min(r["hand_to_guest"] for r in g) <= 0.0:
        bad.append("the handshake's hand reaches into a tour guest (%.2f m)" % min(r["hand_to_guest"] for r in g))
    if g and max(r["front_to_guest"] for r in g) > TOUR_FRONT_MAX_M:
        bad.append("ended %.2f m from a tour guest: never reached the guest" % max(r["front_to_guest"] for r in g))
    off = [r["face_err_deg"] for r in g if r.get("face_err_deg") is not None and r["face_err_deg"] > GUEST_FACE_TOL_DEG]
    if off:
        bad.append("ended facing %.0f deg away from a tour guest" % max(off))
    b = sm["back"]
    off = [r["face_err_deg"] for r in b if r.get("face_err_deg") is not None and r["face_err_deg"] > GUEST_FACE_TOL_DEG]
    if off:
        bad.append("back at the counter facing %.0f deg away from it" % max(off))
    ends = [r["end_err_m"] for r in b if r.get("end_err_m") is not None]
    if ends and max(ends) > TOUR_END_TOL_M:
        bad.append("ended %.2f m from the tour's end spot" % max(ends))
    tops = [r["front_to_top"] for r in b if r.get("front_to_top") is not None]
    if tops and not (TOUR_BACK_FRONT_M[0] <= min(tops) and max(tops) <= TOUR_BACK_FRONT_M[1]):
        bad.append("back at the counter %.2f-%.2f m from the table's top (owner: 60-70 cm)" % (min(tops), max(tops)))
    bad += ["a turn round with no watched turn first -- %s" % r for r in sm["rear"]]
    return bad


def tour_lines(sm: dict, legs: list[dict]) -> list[str]:
    """The tour's own report lines: where its guests were (ASSUMED or given), in front of them, back at the counter."""
    out = []
    for leg in legs:
        if leg.get("tour_guest"):
            x, y, deg = leg["spot_pose"]
            out.append("%s: her spot (%.2f, %.2f) %.0f deg, the guest at (%.2f, %.2f)" % (
                leg["what"].split(": ", 1)[1].split(",")[0], x, y, deg, leg["guest_at"][0], leg["guest_at"][1]))
    g, b = sm["guests"], sm["back"]
    if g:
        out.append("in front of the tour's guests: front face to the guest %.2f-%.2f m, the handshake's hand %.2f-%.2f m, "
                   "facing %.0f deg off at most" % (
                       min(r["front_to_guest"] for r in g), max(r["front_to_guest"] for r in g),
                       min(r["hand_to_guest"] for r in g), max(r["hand_to_guest"] for r in g),
                       max((r["face_err_deg"] or 0.0) for r in g)))
    if b and any(r.get("end_err_m") is not None for r in b):
        out.append("at the end spot: %.2f m off it, facing %.0f deg off at most" % (
            max(r["end_err_m"] for r in b), max(r["face_err_deg"] for r in b)))
    elif b:
        out.append("back at the counter: front face to the table's top %.2f-%.2f m (owner 60-70 cm), facing %.0f deg off "
                   "at most" % (min(r["front_to_top"] for r in b), max(r["front_to_top"] for r in b),
                                max(r["face_err_deg"] for r in b)))
    out.append("turns round with no watched turn of ours first: %d" % len(sm["rear"]))
    return out


def counter_spot() -> tuple[float, float, float] | None:
    """beside_host's last point and heading, from the event file."""
    try:
        _, spots, _ = _event_scenes_and_spots()
    except InputError:
        return None
    sp = spots.get("beside_host") or {}
    if sp.get("steps"):
        last = sp["steps"][-1]
        return (float(last["to"][0]), float(last["to"][1]), float(last.get("face") or 0.0))
    if sp.get("pose"):
        return tuple(float(v) for v in sp["pose"][:3])
    return None


def counter_approach() -> tuple[float, float, float] | None:
    """beside_host's head-on point (event_tour.head_on_point, owner 23 Nov), from the event file -- None without one."""
    from app import event_tour

    try:
        _, spots, _ = _event_scenes_and_spots()
    except InputError:
        return None
    return event_tour.head_on_point(spots.get("beside_host"))


def _at_the_counter(w: "FloorWorld") -> dict:
    """Standing at the counter: the handshake's hand (HANDSHAKE_BOX) to the counter's top and to anything,
    the base's front face to the top (the owner's 60-70 cm), room for a full turn there."""
    b = w.base
    pose = b.pose
    arms = w.arm_points()
    top = w.lay.above
    base_box = (-w.foot_l / 2, w.foot_l / 2, -w.foot_w_base / 2, w.foot_w_base / 2)
    full = min(ws.rect_gap(arms, (b.x, b.y, math.radians(h)), w.reach_l, w.foot_w_arms) for h in range(0, 360, 5))
    return {"handshake_gap": round(box_gap(arms, pose, HANDSHAKE_BOX), 3),
            "hand_to_top": round(box_gap(top, pose, HANDSHAKE_BOX), 3) if len(top) else None,
            "front_to_top": round(box_gap(top, pose, base_box), 3) if len(top) else None,
            "full_turn_gap": round(full, 3), "at_counter": (round(b.x, 3), round(b.y, 3), round(math.degrees(b.yaw), 1))}


def _at_the_guest(w: "FloorWorld", steps: list[dict], guests: list) -> dict:
    """Standing in front of card 3's guest: the base's front face to the guest (the walk ends at its `meet_m`;
    the owner's distance at the counter was 60-70 cm), the handshake's hand to the guest (> 0 = the hand stops
    short of the body, the guest's own arm closes the rest), how far she walked from where she turned. None for
    the guest's numbers when card 3's guest is not there (she walked the whole way)."""
    b = w.base
    pose = b.pose
    base_box = (-w.foot_l / 2, w.foot_l / 2, -w.foot_w_base / 2, w.foot_w_base / 2)
    ang = np.linspace(0, 2 * math.pi, 24, endpoint=False)
    ring = (np.vstack([np.c_[g[0] + PERSON_R * np.cos(ang), g[1] + PERSON_R * np.sin(ang)] for g in guests])
            if guests else np.zeros((0, 2)))
    turned_at = next((st["to"] for st in steps if st.get("to")), None)
    # where the turn LEAVES her (its last: a turn round is two halves since 23 Nov, event_tour.TURN_SPLIT_DEG)
    face = next((float(st["face"]) for st in reversed(steps) if st.get("do") == "turn"), None)
    meet = next((float(st["meet_m"]) for st in steps if "meet_m" in st), None)
    return {"front_to_guest": round(box_gap(ring, pose, base_box), 3) if len(ring) else None,
            "meet_m": meet,
            "face_err_deg": None if face is None else round(abs((math.degrees(b.yaw) - face + 180.0) % 360.0 - 180.0), 1),
            "hand_to_guest": round(box_gap(ring, pose, HANDSHAKE_BOX), 3) if len(ring) else None,
            "walked_to_guest_m": None if turned_at is None else round(math.hypot(b.x - turned_at[0], b.y - turned_at[1]), 3),
            "at_guest": (round(b.x, 3), round(b.y, 3), round(math.degrees(b.yaw), 1))}


def guest_faults(rows: list[dict]) -> list[str]:
    """What is wrong in front of card 3's guest over a set of runs (sequence_summary's "guest" rows): the hand
    must stop short of the body, and the walk must end before the base's own sonar hold would hold it (it ends at
    `meet_m`: nearer than FORWARD_HOLD_M = the meet never ended it)."""
    bw = ws.load_board_walk()
    bad = []
    near = [r for r in rows if r["front_to_guest"] is not None]
    if near and min(r["hand_to_guest"] for r in near) <= 0.0:
        bad.append("the handshake's hand reaches into card 3's guest (%.2f m)" % min(r["hand_to_guest"] for r in near))
    if near and min(r["front_to_guest"] for r in near) < bw.FORWARD_HOLD_M:
        bad.append("stopped %.2f m from card 3's guest, nearer than the hold (%.2f): the meet did not end the walk" % (
            min(r["front_to_guest"] for r in near), bw.FORWARD_HOLD_M))
    # second review 22 Nov: a card 3 that ended "done" without meeting the guest passed (stopped at once on
    # something, turned the wrong way, walked past): the guest must be within its meet distance, and she must face
    # where the turn pointed
    far = [r["front_to_guest"] for r in near if r.get("meet_m") is not None and r["front_to_guest"] > r["meet_m"] + GUEST_MET_SLACK_M]
    if far:
        bad.append("ended %.2f m from card 3's guest, past the meet distance: never met the guest" % max(far))
    off = [r["face_err_deg"] for r in rows if r.get("face_err_deg") is not None and r["face_err_deg"] > GUEST_FACE_TOL_DEG]
    if off:
        bad.append("ended facing %.0f deg away from where card 3 turns her" % max(off))
    return bad


def run_sequence(layout: Layout, legs: list[dict], *, seed: int = 0, max_s: float = 1500.0, settle_s: float = 2.0,
                 **world) -> dict:
    """The walks of `legs` one after another in ONE world (the board's pose error, what its lidar has seen and
    the clock go on between them), each a fresh board_walk as event_walk spawns one. A walk whose steps are a
    function gets them from the board's pose where it starts (event_walk's steps_from_here). Stops at the
    first walk that does not end done. -> {"parts": [run_walk-like result + "what"], "trace": [(t, x, y, yaw)]}"""
    x, y, deg = legs[0]["start"]
    # a card's own guest (card 3's, `guests`) stands there from the start, beside whoever the caller put there
    world = dict(world, people=list(world.get("people") or ()) + [dict(g) for leg in legs for g in leg.get("guests", ())])
    w = FloorWorld(layout, (x, y, math.radians(deg)), seed=seed, **world)
    w.trace = []
    w.sleep(settle_s)
    parts = []
    for k, leg in enumerate(legs):
        w.leg_index = k                                          # whose turn it is (a card's guest: _arm_people)
        # read only when the steps are built from it: every read is a draw of the world's noise, and one more draw
        # moves every later draw of the day (23 Nov: an extra read here changed card 2's guest-in-the-way result)
        here = w.pose() if callable(leg["steps"]) else None
        steps = leg["steps"](here) if callable(leg["steps"]) else [dict(s) for s in leg["steps"]]
        w.reset_judge()
        w.side_arm = PAGEANT_ARM_BOX if leg.get("pageant") else None
        t0, i0 = w.base.t, len(w.trace)
        end, reason, said, events = _drive(w, leg["doc"], steps, max_s)
        w.cmd = (0.0, 0.0)
        w.action = None
        w.sleep(1.0)
        res = _walk_result(w, end, reason, said, events, w.base.t - t0 - 1.0)
        res.update(what=leg["what"], steps=steps, trace=(i0, len(w.trace)))
        if leg.get("pageant"):
            res.update(min_side_arm_gap=round(w.min_side_arm_gap, 3), side_arm_at=w.side_arm_at)
        w.side_arm = None                                         # the arm comes down after the walk (event_pageant)
        if leg.get("wave_after") and end == "done":
            res.update(_wave_standing(w))                         # out of the box: the hand up only now, standing
        if leg.get("handshake") and end == "done":
            res.update(_at_the_counter(w))
        if leg.get("guest_handshake") and end == "done":
            res.update(_at_the_guest(w, steps, [g["at"] for g in leg.get("guests", ())]))
        # card 1's tour (23 Nov): in front of its guest, at the counter again, and any turn round the board would make
        # with no watched turn of ours first (from where this leg was built: the server's own builder)
        if leg.get("tour_guest") and end == "done":
            res["tour_guest"] = _at_the_tour_guest(w, leg["spot_pose"], leg["guest_at"])
        if leg.get("counter_back") and end == "done":
            res["counter_back"] = dict(_at_the_counter(w), face_err_deg=round(abs(
                (math.degrees(w.base.yaw) - float(leg["spot_pose"][2]) + 180.0) % 360.0 - 180.0), 1))
            if leg.get("end_spot"):
                # not the counter's place: how far from the table is not its business, where she stands is
                res["counter_back"].update(front_to_top=None, end_err_m=round(math.hypot(
                    w.base.x - float(leg["spot_pose"][0]), w.base.y - float(leg["spot_pose"][1])), 3))
        if leg.get("rear_check") and here is not None:
            from app import event_tour

            res["rear_turns"] = event_tour.rear_turns(steps, (here[0], here[1], math.degrees(here[2])))
        parts.append(res)
        if end != "done":
            break
        if leg.get("after_s"):
            w.sleep(float(leg["after_s"]))
    return {"parts": parts, "trace": w.trace, "seed": seed, "legs": len(legs)}


def counter_turn_gap(layout: Layout, trace: list, spot) -> float:
    """The arms' least gap (the counter's top counted) over the poses within COUNTER_TURN_R_M of the counter's
    spot -- her turns there. 9.0 = never there."""
    pts = np.vstack([layout.solid, layout.above]) if len(layout.above) else layout.solid
    near = pts[np.hypot(pts[:, 0] - spot[0], pts[:, 1] - spot[1]) < 2.5]
    reach_l = rp.EMMA.body.length_m + 2 * rp.EMMA.body.tray_overhang_m
    gaps = [ws.rect_gap(near, (x, y, yaw), reach_l, rp.EMMA.body.reach_width_m)
            for _, x, y, yaw in trace if math.hypot(x - spot[0], y - spot[1]) < COUNTER_TURN_R_M]
    return min(gaps) if gaps else 9.0


def sequence_as_walk(seq: dict) -> dict:
    """One run of a sequence as one walk for judge()/summarise(): done when every walk was; the dock and the
    time from the last; the least of every gap."""
    parts = seq["parts"]
    last = parts[-1]
    done = len(parts) == seq["legs"] and all(p["end"] == "done" for p in parts)
    bad = next((p for p in parts if p["end"] != "done"), None)
    hz: dict = {}
    for p in parts:
        for k, v in p["min_hazard"].items():
            hz[k] = min(hz.get(k, 9.0), v)
    out = dict(last, end="done" if done else "stop",
               reason="" if bad is None else "%s: %s" % (bad["what"], bad["reason"]),
               s=round(sum(p["s"] for p in parts), 1), min_hazard=hz,
               min_arm_gap=min(p["min_arm_gap"] for p in parts), min_base_gap=min(p["min_base_gap"] for p in parts),
               arm_gap_at=min(parts, key=lambda p: p["min_arm_gap"])["arm_gap_at"],
               min_person_gap=min(p["min_person_gap"] for p in parts), max_err_m=max(p["max_err_m"] for p in parts),
               min_q=min(p["min_q"] for p in parts), low_fit_s=sum(p["low_fit_s"] for p in parts),
               touched=[t for p in parts for t in p["touched"]])
    for p in parts:
        for k in ("min_side_arm_gap", "side_arm_at", "handshake_gap",
                  "hand_to_top", "front_to_top", "full_turn_gap", "at_counter",
                  "front_to_guest", "hand_to_guest", "walked_to_guest_m", "at_guest"):
            if k in p:
                out[k] = p[k]
    return out


def sequence_summary(seqs: list[dict], layout: Layout | None = None) -> dict:
    """What the event-day report says of a set of runs: by walk, the least gaps and where; at the counter."""
    by: dict[str, dict] = {}
    for s in seqs:
        for p in s["parts"]:
            row = by.setdefault(p["what"], {"n": 0, "done": 0, "arm": 9.0, "arm_at": None, "base": 9.0, "stops": {}})
            row["n"] += 1
            row["done"] += p["end"] == "done"
            if p["end"] != "done":
                k = stop_kind(p["reason"])
                row["stops"][k] = row["stops"].get(k, 0) + 1
            if p["min_arm_gap"] < row["arm"]:
                row["arm"], row["arm_at"] = p["min_arm_gap"], p["arm_gap_at"]
            row["base"] = min(row["base"], p["min_base_gap"])
            if "min_side_arm_gap" in p and p["min_side_arm_gap"] <= row.get("min_side_arm_gap", 9.0):
                row["min_side_arm_gap"], row["side_arm_at"] = p["min_side_arm_gap"], p["side_arm_at"]
            row["come_again"] = row.get("come_again", 0) + any("coming to it again" in line for line in p["said"])
            row["steps_onto"] = row.get("steps_onto", 0) + any("stepping onto it" in line for line in p["said"])
    at = [p for s in seqs for p in s["parts"] if p.get("handshake_gap") is not None]
    spot = counter_spot()
    turn = min((counter_turn_gap(layout, s["trace"], spot) for s in seqs), default=9.0) if layout is not None and spot else None
    last = [s["parts"][-1] for s in seqs if len(s["parts"]) == s["legs"]]
    docked = [p for p in last if p["end"] == "done" and p["on_dock"]]
    return {"by_walk": by,
            "handshake_gap": min((p["handshake_gap"] for p in at), default=9.0),
            "hand_to_top": [min((p["hand_to_top"] for p in at), default=None), max((p["hand_to_top"] for p in at), default=None)],
            "front_to_top": [min((p["front_to_top"] for p in at), default=9.0), max((p["front_to_top"] for p in at), default=9.0)],
            "full_turn_gap": min((p["full_turn_gap"] for p in at), default=9.0),
            "counter_turn_gap": None if turn is None else round(turn, 3),
            "at_counter": [p["at_counter"] for p in at],
            "docked": len(docked), "n": len(seqs),
            "aside_cm": [round(abs(p["aside_m"]) * 100, 1) for p in docked],
            # card 3 (22 Nov): in front of the guest -- [] when the day has no card 3
            "guest": [{k: p.get(k) for k in ("front_to_guest", "hand_to_guest", "walked_to_guest_m", "at_guest",
                                              "meet_m", "face_err_deg")}
                      for s in seqs for p in s["parts"] if "at_guest" in p]}


def draw_event_day(layout: Layout, seqs: list[dict], path: Path, title: str, summary: dict) -> Path:
    """A top-down picture for the owner: walls, furniture, the counter's top, every run's path (the first
    bold), her footprint at the key points, the arm's reach at the counter and the gap to the table."""
    from PIL import Image, ImageDraw, ImageFont

    x0, x1, y0, y1 = -11.2, -6.2, -7.7, -3.3
    # card 1's tour: its guests in the picture (a row west of the counter, the first north of it)
    ga = [p["tour_guest"]["guest_at"] for s in seqs[:1] for p in s["parts"] if p.get("tour_guest")]
    if ga:
        x0 = min(x0, min(g[0] for g in ga) - 0.6)
        y1 = max(y1, max(g[1] for g in ga) + 0.7)
        y0 = min(y0, min(g[1] for g in ga) - 0.6)
    S = 170
    W, H, PANEL = int((x1 - x0) * S), int((y1 - y0) * S), 560
    im = Image.new("RGB", (W + PANEL, H), (255, 255, 255))
    d = ImageDraw.Draw(im, "RGBA")

    def font(size, bold=False):
        for name in (("tahomabd.ttf" if bold else "tahoma.ttf"), "LeelawUI.ttf", "arial.ttf"):
            try:
                return ImageFont.truetype("C:/Windows/Fonts/" + name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    f_s, f_m, f_b = font(15), font(18), font(22, True)

    def px(x, y):
        return ((x - x0) * S, (y1 - y) * S)

    def cells(pts, fill):
        for x, y in pts:
            if x0 - 0.05 <= x <= x1 and y0 - 0.05 <= y <= y1:
                a, b = px(x - 0.025, y + 0.025), px(x + 0.025, y - 0.025)
                d.rectangle([a, b], fill=fill)

    for gx in np.arange(math.ceil(x0 * 2) / 2, x1, 0.5):
        d.line([px(gx, y0), px(gx, y1)], fill=(232, 236, 245))
        d.text((px(gx, y1)[0] + 2, 2), "%.1f" % gx, fill=(120, 130, 160), font=f_s)
    for gy in np.arange(math.ceil(y0 * 2) / 2, y1, 0.5):
        d.line([px(x0, gy), px(x1, gy)], fill=(232, 236, 245))
        d.text((2, px(x0, gy)[1] + 2), "%.1f" % gy, fill=(120, 130, 160), font=f_s)
    cells(layout.board.points(), (205, 205, 205))
    cells(layout.furniture, (236, 160, 70))
    cells(layout.returns, (40, 40, 40))
    for part in layout.above_parts:
        bx0, bx1, by0, by1 = part["box"]
        d.rectangle([px(bx0, by1), px(bx1, by0)], fill=(150, 60, 200, 110), outline=(110, 30, 160), width=2)

    def foot(pose, l, w, colour, width=2, box=None):
        x, y, yaw = pose
        c, s = math.cos(yaw), math.sin(yaw)
        bx = box or (-l / 2, l / 2, -w / 2, w / 2)
        corners = [(bx[0], bx[2]), (bx[1], bx[2]), (bx[1], bx[3]), (bx[0], bx[3])]
        poly = [px(x + a * c - b * s, y + a * s + b * c) for a, b in corners]
        d.polygon(poly, outline=colour, width=width)
        return poly

    colours = [(200, 40, 160), (30, 90, 220), (20, 150, 70)]
    for k, s in enumerate(seqs):
        for j, p in enumerate(s["parts"]):
            i0, i1 = p["trace"]
            pts = [px(x, y) for _, x, y, _ in s["trace"][i0:i1:3]]
            if len(pts) > 1:
                # by what the walk is, not its place (22 Nov: card 3 was drawn in the way home's colour)
                col = colours[{"debut": 0, "counter": 1, "guest": 1, "back": 1}.get(_walk_role(p["what"]), 2)]
                d.line(pts, fill=col + ((255,) if k == 0 else (45,)), width=3 if k == 0 else 1)
    first = seqs[0]
    body_l, body_w, arms_w = rp.EMMA.body.length_m, rp.EMMA.body.width_m, rp.EMMA.body.reach_width_m
    tr = first["trace"]
    if tr:
        start = tr[0][1:]
        foot(start, body_l, body_w, (90, 90, 90), width=3)
        foot(start, 0, 0, (90, 90, 90), width=2, box=PAGEANT_ARM_BOX)
        d.text(px(start[0] - 0.35, start[1] - 0.62), "แท่น (กล่อง): ออกจากตรงนี้", fill=(60, 60, 60), font=f_m)
    p0 = first["parts"][0]
    if p0["trace"][1] > p0["trace"][0]:
        e = tr[p0["trace"][1] - 1][1:]
        foot(e, body_l, body_w, colours[0], width=2)
        foot(e, 0, 0, colours[0], width=3, box=PAGEANT_ARM_BOX)
        d.text(px(e[0] - 0.75, e[1] - 0.42), "เปิดตัวเดินตรง 1 ม. จบตรงนี้", fill=colours[0], font=f_m)
        d.text(px(e[0] - 0.75, e[1] - 0.58), "กรอบเล็กทางขวาหุ่น = แขนนางงาม (สมมติ)", fill=colours[0], font=f_s)
    try:
        dk = route_doc(layout, event=True)["dock"]
        ap = dk.get("approach")
    except InputError:
        ap = None
    if ap:
        d.ellipse([px(ap[0] - 0.05, ap[1] + 0.05), px(ap[0] + 0.05, ap[1] - 0.05)], outline=(30, 90, 220), width=2)
        d.text(px(ap[0] - 1.45, ap[1] + 0.06), "approach (ทางออกจากแท่น)", fill=(30, 90, 220), font=f_s)
    at = next((p for p in first["parts"] if p.get("at_counter")), None)
    if at is not None:
        cx, cy, cdeg = at["at_counter"]
        pose = (cx, cy, math.radians(cdeg))
        foot(pose, body_l, body_w, colours[1], width=3)
        foot(pose, body_l, arms_w, colours[1], width=1)
        foot(pose, 0, 0, (220, 30, 30), width=3, box=HANDSHAKE_BOX)
        r = rp.EMMA.body.turn_radius_m
        d.ellipse([px(cx - r, cy + r), px(cx + r, cy - r)], outline=(30, 90, 220, 160), width=1)
        top_x = min(part["box"][0] for part in layout.above_parts) if layout.above_parts else None
        if top_x is not None:
            def dim(xa, xb, yv, colour, text, above):
                d.line([px(xa, yv), px(xb, yv)], fill=colour, width=2)
                for xv in (xa, xb):
                    d.line([px(xv, yv + 0.05), px(xv, yv - 0.05)], fill=colour, width=2)
                w_ = d.textlength(text, font=f_m)
                mx = (px(xa, yv)[0] + px(xb, yv)[0]) / 2 - w_ / 2
                d.text((mx, px(xa, yv)[1] + (-28 if above else 6)), text, fill=colour, font=f_m)
            fx = cx + body_l / 2 * math.cos(pose[2])
            dim(fx, top_x, cy + 0.36, (30, 90, 220), "%.2f ม." % at["front_to_top"], True)
            hx = cx + HANDSHAKE_BOX[1] * math.cos(pose[2])
            dim(hx, top_x, cy - 0.36, (220, 30, 30), "%.2f ม." % at["hand_to_top"], False)
        d.text(px(cx - 1.75, cy + 0.12), "จุดคุณมิกิ: หันเข้าโต๊ะ", fill=colours[1], font=f_m)
        d.text(px(cx - 1.75, cy - 0.05), "ยกมือท่าจับมือ", fill=colours[1], font=f_m)
        d.text(px(top_x + 0.04 if top_x is not None else cx, cy + 0.65), "ขอบหน้าโต๊ะ (สูงกว่าเลเซอร์)", fill=(110, 30, 160), font=f_s)
    # card 1's tour (23 Nov): each guest (a person, orange) and where she stood for the handshake
    tour_guests = [p for p in first["parts"] if p.get("tour_guest")]
    for p in tour_guests:
        tg = p["tour_guest"]
        gx, gy = tg["guest_at"]
        d.ellipse([px(gx - PERSON_R, gy + PERSON_R), px(gx + PERSON_R, gy - PERSON_R)], outline=(230, 110, 0), width=3)
        ax, ay, adeg = tg["at"]
        foot((ax, ay, math.radians(adeg)), body_l, body_w, colours[1], width=2)
        foot((ax, ay, math.radians(adeg)), 0, 0, (220, 30, 30), width=2, box=HANDSHAKE_BOX)
        name = re.search(r"\b(guest_\d+)\b", p["what"])
        d.text(px(gx - 0.45, gy + 0.42), "%s%s" % (name.group(1) if name else "แขก", " (สมมติ)" if "ASSUMED" in p["what"] else ""),
               fill=(230, 110, 0), font=f_s)
    if tr:
        e = tr[-1][1:]
        foot(e, body_l, body_w, colours[2], width=2)
    # the dock's line and the spot she reverses from
    try:
        far = float((_read(ROUTE_FILE, "the floor-1 route file").get("dock") or {}).get("back_from_m") or 0.0)
    except InputError:
        far = 0.0
    if far:
        spot = dock_line_pose(layout, far)
        d.ellipse([px(spot[0] - 0.05, spot[1] + 0.05), px(spot[0] + 0.05, spot[1] - 0.05)], outline=(20, 150, 70), width=2)
        d.text(px(spot[0] - 0.95, spot[1] - 0.08), "จุดเริ่มถอยเข้าแท่น %.1f ม." % far, fill=(20, 150, 70), font=f_s)
        again = [line for s in seqs for p in s["parts"] for line in p["said"] if "coming to it again" in line]
        aout = getattr(ws.load_board_walk(), "AGAIN_OUT_M", 0.0)
        if again and aout:
            ag = dock_line_pose(layout, far + aout)
            d.ellipse([px(ag[0] - 0.05, ag[1] + 0.05), px(ag[0] + 0.05, ag[1] - 0.05)], outline=(20, 150, 70), width=2)
            d.text(px(ag[0] - 0.35, ag[1] - 0.12), "ออกไปแล้วเข้าใหม่ %.1f ม. (%d รอบ)" % (far + aout, len(again)),
                   fill=(20, 150, 70), font=f_s)
    # the panel
    X = W + 16
    yy = 14
    for part in title.split("\n"):
        d.text((X, yy), part, fill=(0, 0, 0), font=f_b)
        yy += 30
    yy += 10
    lines = [
        ("ดำ = ผนัง (อยู่ในแผนที่บอร์ด), ส้ม = ของ 16 พ.ย. ที่บอร์ดไม่รู้, เทา = แผนที่บอร์ด", (60, 60, 60)),
        ("ม่วงโปร่ง = หน้าโต๊ะเคาน์เตอร์ (สูงกว่าเลเซอร์ แขนชนได้)", (110, 30, 160)),
        ("ชมพู = เปิดตัว (เดินตรง), น้ำเงิน = ไปหน้าเคาน์เตอร์, เขียว = กลับแท่น", (60, 60, 60)),
        ("เส้นหนา = รอบแรก, เส้นจาง = อีก %d รอบ" % (len(seqs) - 1), (60, 60, 60)),
        ("กรอบแดง = มือท่าจับมือ (สมมติ ยื่นพ้นหน้าฐาน %.2f ม. ไม่เคยวัด)" % HANDSHAKE_PAST_FRONT_M, (220, 30, 30)),
        ("วงกลม = รัศมีแขนตอนหมุนตัว %.2f ม." % rp.EMMA.body.turn_radius_m, (30, 90, 220)),
        ("เส้นมีขีดน้ำเงิน = หน้าหุ่นถึงขอบหน้าโต๊ะ, เส้นมีขีดแดง = มือถึงขอบหน้าโต๊ะ", (60, 60, 60)),
        ] + ([("วงส้ม = แขกการ์ด 1%s ข้างๆ = ที่หุ่นยืนจับมือ" % (
            " (สมมติ ยังไม่มาร์ก)" if any("ASSUMED" in p["what"] for p in tour_guests) else ""), (230, 110, 0))]
             if tour_guests else []) + [
        ("", (0, 0, 0)),
        ("ผล (%d รอบ, ตัวจำลอง ไม่ใช่ผลวัดบนหุ่น):" % summary["n"], (0, 0, 0)),
    ]
    for what, row in summary["by_walk"].items():
        lines.append(("%s: ถึง %d/%d, แขนใกล้สุด %.2f ม., ฐาน %.2f ม." % (
            _thai_walk(what), row["done"], row["n"], row["arm"], row["base"]), (0, 0, 0)))
        if "min_side_arm_gap" in row:
            lines.append(("   แขนนางงามใกล้ของสุด %.2f ม. ที่ (%.2f, %.2f)" % (
                row["min_side_arm_gap"], row["side_arm_at"][0], row["side_arm_at"][1]), (200, 40, 160)))
    lines += [
        ("หน้าหุ่น→ขอบโต๊ะ %.2f–%.2f ม. (เจ้าของ 60–70 ซม.)" % tuple(summary["front_to_top"]), (30, 90, 220)),
        ("มือ→ขอบโต๊ะ %s ม." % "–".join("%.2f" % v for v in summary["hand_to_top"] if v is not None), (220, 30, 30)),
        ("หมุนตัวที่เคาน์เตอร์: แขนห่างของ ≥ %.2f ม." % (summary["counter_turn_gap"] or 9.0), (30, 90, 220)),
        ("กลับแท่น: ออกไปแล้วเข้าใหม่ %d รอบ, ก้าวเข้าแนว %d รอบ" % (
            sum(r.get("come_again", 0) for w_, r in summary["by_walk"].items() if _walk_role(w_) == "home"),
            sum(r.get("steps_onto", 0) for w_, r in summary["by_walk"].items() if _walk_role(w_) == "home")), (20, 150, 70)),
        ("เข้าแท่น %d/%d, เยื้อง %s ซม." % (summary["docked"], summary["n"],
                                          "–".join("%.1f" % v for v in (min(summary["aside_cm"]), max(summary["aside_cm"])))
                                          if summary["aside_cm"] else "-"), (20, 150, 70)),
    ]
    for text, col in lines:
        d.text((X, yy), text, fill=col, font=f_s)
        yy += 24
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
    return path


def _walk_role(what: str) -> str | None:
    """"debut" / "counter" / "guest" / "home" for an event-day walk's label, card or not ("card 2 counter_handshake: to the
    counter ..." -- review 21 Nov: the labels gained their card and the picture's sums matched none of them)."""
    text = what.split(": ", 1)[1] if what.startswith("card ") and ": " in what else what
    if text.startswith("debut") or "ahead, the pageant arm out" in text or "ahead, arms down" in text:
        return "debut"
    if text.startswith("to the counter"):
        return "counter"
    if text.startswith("to the guest"):
        return "guest"
    if text.startswith("back to the counter"):
        return "back"                                    # card 1's tour, its last walk (23 Nov)
    if text.startswith("home"):
        return "home"
    return None


def _thai_walk(what: str) -> str:
    role = _walk_role(what)
    th = {"debut": "เปิดตัว", "counter": "ไปเคาน์เตอร์", "guest": "ไปหาแขก", "back": "กลับเคาน์เตอร์",
          "home": "กลับแท่น"}.get(role)
    spot = re.search(r"\b(guest_\d+)\b", what) if role == "guest" else None
    if th and spot:
        th += " " + spot.group(1)                        # card 1's tour has three of them
    return (th + (" (" + what.split(":", 1)[0] + ")" if what.startswith("card ") else "")) if th else what


#: A guest standing in the way of the day's first two cards (owner 21 Nov: "อยากรู้ตอนวันงานถ้ามีคนยืนบัง
#: ระหว่างทางที่เตรียมไว้หุ่นจะทำยังไง" + "เพิ่มเลย ทดสอบด้วย"). The pre-flight's own in-the-way cases only take legs
#: over 2.4 m, and the cards' legs are 1-2 m: none of them was ever tried with someone there.
#:   card 1, the debut: 1.2 m ahead of the docked centre, in the strip the walk ahead must cross (its front ends
#:     1.35 m out) -- OUR code drives it (board_walk `forward`): it must stand still for the guest (FORWARD_HOLD_M)
#:     and never touch, and a guest who stays must stop it (event_walk's patience, 45 s).
#:   card 2, to the counter: halfway along the line from the dock's approach point to the counter's spot, where
#:     the board's MoveTo goes. The BOARD drives it: it may go round (room enough) or wait; how wide it keeps
#:     round a person it has just seen is BOARD_INFLATE_M (assumed, never measured) -- so a touch there is printed,
#:     as ADVERSE_TOUCH_SETS_EXIT says, and does not set the exit code.
#: "steps away" = the guest leaves GUEST_LEAVES_AFTER_S after she first comes within 2 m (`_arm_people`).
GUEST_LEAVES_AFTER_S = 20.0
IN_THE_WAY_RUNS = 10
#: The guest was MET on the card: the arms came this near (review 21 Nov: a route that no longer passes the
#: guest passed every case with a gap of 9 m).
GUEST_MET_M = 1.0
#: A guest who stays stops the card at the walk's patience (event_walk PATIENCE_S 45 s) -- plus the board's own
#: give-up and the leg's start: card 1 stopped at ~47 s, card 2 at ~51 s in the simulator. Later than this is
#: not the patience (review 21 Nov: patience x10 passed and only pytest's timeout caught it).
GUEST_STOP_SLACK_S = 10.0
GUEST_STOP_KINDS = ("something ahead", "board gave up", "held (stuck)", "held (too long)")
#: The one fault of card 2 that is the BOARD's choice, not judged (the rest of card 2's are: re-check 21 Nov -- a
#: filter on message prefixes dropped "never got to the guest's card" and nothing noticed).
BOARD_ONLY_FAULT = "did not get to the counter after the guest left"


def guest_stop_by_s(board: bool = False) -> float:
    """event_walk's patience + GUEST_STOP_SLACK_S (re-check 21 Nov: a copy of 45 let a patience of ~58 s pass).
    `board`: a leg the BOARD drives (card 2's MoveTo) -- + BOARD_GIVE_UP_S: board_walk asks whether the patience
    is spent only after the MoveTo under way ends (its `spent`), and the board holds that one up to its own give-up
    (23 Nov: 7 of 9 runs stopped at 50.5-51.1 s, two at 55.5 and 56.7 -- an attempt that began just before the
    patience ran out; which runs depends only on the world's random draws: the debut's 22 s wave standing moved
    them, as one extra pose read did the same morning). Ours (card 1's walk ahead) stops at the patience itself."""
    from app import event_walk

    return float(event_walk.PATIENCE_S) + GUEST_STOP_SLACK_S + (BOARD_GIVE_UP_S if board else 0.0)


def in_the_way_cases(legs: list[dict], layout: Layout) -> dict[str, dict]:
    """{case: {"people": [...], "card_leg": index of the leg the guest stands on, "leaves": bool, "ours": bool}}
    for the event day's `legs` (event_day_sequences): a guest on card 1's walk ahead and on card 2's walk."""
    x, y, deg = legs[0]["start"]
    h = math.radians(deg)
    on_debut = (x + 1.2 * math.cos(h), y + 1.2 * math.sin(h))
    dock = route_doc(layout, event=True)["dock"]
    ax, ay = (float(v) for v in (dock.get("approach") or (x, y)))
    # card 2's MoveTo from the dock's approach goes to the counter's head-on point first since 23 Nov (owner: "ไกลกว่า
    # โต๊ะเคาน์เตอร์แล้วค่อยเดินเข้ามา"), then straight in: its guest stands halfway along THAT
    spot = counter_approach() or counter_spot() or COUNTER_SPOT
    on_counter = ((ax + spot[0]) / 2, (ay + spot[1]) / 2)
    cases = {}
    for leg, at, ours in ((0, on_debut, True), (1, on_counter, False)):
        card = legs[leg]["what"].split(":", 1)[0]
        for leaves in (True, False):
            person = {"at": at, "from_s": 0.0, "to_s": 1e9, "leg": leg}
            if leaves:
                person["leave_after_seen_s"] = GUEST_LEAVES_AFTER_S
            cases["%s: a guest stands in the way at (%.2f, %.2f), %s" % (
                card, at[0], at[1], "steps away after %.0f s" % GUEST_LEAVES_AFTER_S if leaves else "and stays")] = {
                "people": [person], "card_leg": leg, "leaves": leaves, "ours": ours}
    return cases


def _held(part: dict) -> bool:
    return any(isinstance(e, dict) and "held" in e for e in part["events"])


def in_the_way_verdict(case: dict, run: dict) -> list[str]:
    """What is wrong with one run of an in-the-way case ([] = as it should be). Ours (card 1): never a touch of
    the guest, held while the guest stands, through once the guest leaves, stopped when the guest stays. The
    board's (card 2): it gets to the counter once the guest has left; a touch is not judged (in_the_way_touch).
    Either: the guest was met on the card (GUEST_MET_M), and a stop is the patience's (GUEST_STOP_*). (After a
    card that stops nothing walks on: run_sequence ends the day there.)"""
    parts = run["parts"]
    leg = case["card_leg"]
    if len(parts) <= leg:
        return ["never got to the guest's card (%s)" % (parts[-1]["reason"] if parts else "no walk")]
    p = parts[leg]
    bad = []
    # the cards before the guest's: the guest stands there too (card 2's beside card 1's walk, 0.93 m) -- a touch
    # there is a touch (re-check 21 Nov: never judged)
    if any(q["min_person_gap"] <= 0.0 for q in parts[:leg]):
        bad.append("touched the guest on an earlier card")
    if p["min_person_gap"] > GUEST_MET_M:
        bad.append("never met the guest (the card's route does not pass where the guest stands)")
    if p["end"] != "done" and (stop_kind(p["reason"]) not in GUEST_STOP_KINDS or p["s"] > guest_stop_by_s(not case["ours"])):
        bad.append("stopped, but not at the patience (%s after %.0f s)" % (stop_kind(p["reason"]), p["s"]))
    if case["ours"]:
        if p["min_person_gap"] <= 0.0:
            bad.append("touched the guest")
        if not _held(p):
            bad.append("did not stand still for the guest")
        if case["leaves"] and p["end"] != "done":
            bad.append("did not go on after the guest left (%s)" % stop_kind(p["reason"]))
        if not case["leaves"] and p["end"] == "done":
            bad.append("got there through a guest who stayed")
    elif case["leaves"] and p["end"] != "done":
        bad.append("%s (%s)" % (BOARD_ONLY_FAULT, stop_kind(p["reason"])))
    return bad


def in_the_way_touch(case: dict, run: dict) -> float:
    """The arms' least gap to the guest over EVERY walk of the run (<= 0 = a touch). 21 Nov, drawing the runs for
    the owner: the board went round card 2's guest who stayed, and the way home after it passed that guest with
    the arms 0.008 m INTO them -- the guest's card alone said 0.024."""
    return min((p["min_person_gap"] for p in run["parts"]), default=9.0)


def _in_the_way_report(layout: Layout, legs: list[dict], runs: int, seed: int, out) -> dict:
    """The in-the-way cases of the event day's cards, `runs` seeded runs each (in_the_way_cases).
    -> {case: row}; a row's "new" = what is wrong with OUR code's case (card 1) -- the exit code's part."""
    # the cards of the guest in the way and the way home after them -- not card 3 (22 Nov): its own guest stands
    # where card 3 walks, and walking it in every case doubled these runs (pytest's timeout) for nothing they judge
    legs = [leg for leg in legs if not leg.get("guest_handshake")]
    out("")
    out("A GUEST IN THE WAY of cards 1-2 (owner 21 Nov) -- %d runs a case. Card 1 is our code (judged); card 2 is the "
        "board's MoveTo, whose margin round a person is assumed (a touch there is printed, not judged):" % runs)
    rep = {}
    for case, spec in in_the_way_cases(legs, layout).items():
        got = [run_sequence(layout, legs, seed=seed + 100 + j, people=spec["people"]) for j in range(runs)]
        leg = spec["card_leg"]
        bad = sorted({b for r in got for b in in_the_way_verdict(spec, r)})
        parts = [r["parts"][leg] for r in got if len(r["parts"]) > leg]
        row = {"n": len(got), "done": sum(p["end"] == "done" for p in parts), "held": sum(_held(p) for p in parts),
               "stopped_s": [round(p["s"], 1) for p in parts if p["end"] != "done"],
               "stops": sorted({stop_kind(p["reason"]) for p in parts if p["end"] != "done"}),
               "touch_gap": round(min((in_the_way_touch(spec, r) for r in got), default=9.0), 3),
               "counter": sum(1 for r in got if len(r["parts"]) > 1 and r["parts"][1].get("front_to_top") is not None),
               "bad": bad, "ours": spec["ours"],
               "new": [b for b in bad if spec["ours"] or not b.startswith(BOARD_ONLY_FAULT)]}
        rep[case] = row
        out("  %s" % case)
        out("    %d/%d got there, %d stood still for the guest%s; arms to the guest >= %.2f m%s; greeting + hand at the "
            "counter %d/%d%s" % (
                row["done"], row["n"], row["held"],
                "" if not row["stopped_s"] else ", stopped after %s s (%s)" % (row["stopped_s"], ", ".join(row["stops"])),
                row["touch_gap"], "  <-- A TOUCH" if row["touch_gap"] <= 0.0 else "", row["counter"], row["n"],
                "" if not bad else ("   <-- " if spec["ours"] else "   (the board's, not judged) ") + "; ".join(bad)))
    return rep


def event_day(runs: int = 20, seed: int = 0, *, layout: Layout | None = None, out=print, png_dir: Path | None = None,
              spots_file=None, in_the_way: int = 0) -> dict:
    """The event-day sequence's report (and, with `png_dir`, a picture a way home) -> {name: summary}."""
    layout = layout or load_layout(spots_file=spots_file)
    if layout is None:
        raise InputError("the 16 Nov layout is not on this machine: nothing to walk in")
    seqs = event_day_sequences(layout)
    if not seqs:
        raise InputError("the event file has no debut card (walk ahead with the pageant), no counter card (a scene to beside_host with the handshake), or no card that goes home (a scene whose spot is the dock)")
    try:
        given = load_spots(spots_file) if spots_file else None
    except InputError:
        given = None                                              # a spots file with the dock alone
    tours = tour_sequences(layout, given)                         # card 1's tour (23 Nov), after the day's cards
    out("THE EVENT DAY, as the owner set it out on 20 Nov -- %d seeded runs a way home; a gate before the robot, NOT "
        "a measurement (16 Nov's furniture at the lidar's plane + the counter's top; the arm sizes are assumed):" % runs)
    for line in dock_lines(layout):
        out("  " + line)
    res = {}
    for k, (name, legs) in enumerate({**seqs, **tours}.items()):
        got = [run_sequence(layout, legs, seed=seed + j) for j in range(runs)]
        sm = sequence_summary(got, layout)
        flat = [sequence_as_walk(r) for r in got]
        bad = judge(name, flat, layout.hazards, plain=True, ends_docked=True) + guest_faults(sm["guest"])
        if name in tours:
            sm["tour"] = tour_summary(got)
            bad += tour_faults(sm["tour"])
        sm["bad"] = bad
        sm["new"] = known_reasons(name, bad, KNOWN_OUTSIDE)        # the exit code: what KNOWN_OUTSIDE does not name
        res[name] = sm
        out("")
        out(name + ("" if not bad else "   <-- " + "; ".join(bad) + (
            "  (KNOWN: %s)" % _why(KNOWN_OUTSIDE[name]) if not sm["new"] else "  NEW")))
        for what, row in sm["by_walk"].items():
            out("  %-58s %2d/%-2d done  arms >= %.2f m%s  base >= %.2f m%s%s%s" % (
                what[:58], row["done"], row["n"], row["arm"], "" if row["arm_at"] is None else " at %s" % (row["arm_at"],),
                row["base"],
                "" if "min_side_arm_gap" not in row else "  pageant arm >= %.2f m (at %s)" % (
                    row["min_side_arm_gap"], row.get("side_arm_at")),
                "" if not row["stops"] else "  stops %s" % row["stops"],
                "" if not (row.get("come_again") or row.get("steps_onto")) else "  came again %d, stepped %d" % (
                    row.get("come_again", 0), row.get("steps_onto", 0))))
        out("  at the counter: front face to the table's top %.2f-%.2f m (owner: 60-70 cm); the handshake's hand to the "
            "top %s-%s m, to anything >= %.2f m; arms while turning there >= %s m; a full turn there >= %.2f m" % (
                sm["front_to_top"][0], sm["front_to_top"][1], sm["hand_to_top"][0], sm["hand_to_top"][1], sm["handshake_gap"],
                sm["counter_turn_gap"], sm["full_turn_gap"]))
        if sm["guest"]:
            g = sm["guest"]
            out("  in front of card 3's guest (where the guest stands is ASSUMED, %.1f m from the counter's spot): front "
                "face to the guest %.2f-%.2f m (stops at meet_m); the handshake's hand to the guest %.2f-%.2f m; walked "
                "%.2f-%.2f m from the counter" % (
                    HANDSHAKE_GUEST_AT_M, min(r["front_to_guest"] for r in g), max(r["front_to_guest"] for r in g),
                    min(r["hand_to_guest"] for r in g), max(r["hand_to_guest"] for r in g),
                    min(r["walked_to_guest_m"] for r in g), max(r["walked_to_guest_m"] for r in g)))
        if name in tours:
            for line in tour_lines(sm["tour"], legs):
                out("  " + line)
        out("  on the dock %d of %d, aside %s cm" % (sm["docked"], sm["n"], sm["aside_cm"]))
        if png_dir is not None:
            tag = ("card1_tour_walk_up" if name == TOUR_WALK_UP_NAME else "card1_tour" if name in tours
                   else "event_dock_walk" if "event's dock walk" in name else "dock_via_reception")
            title = {"card1_tour": "การ์ด 1 กดครั้งเดียว (ตัวจำลอง%s):\nเปิดตัว → เคาน์เตอร์ → แขก %d จุด → เคาน์เตอร์ → แท่น" % (
                         ", แขกสมมติ" if any("ASSUMED" in leg["what"] for leg in legs) else "",
                         sum(1 for leg in legs if leg.get("tour_guest"))),
                     "card1_tour_walk_up": "การ์ด 1 กดครั้งเดียว จุดแขกยังไม่บันทึก (ตัวจำลอง, แขกสมมติ):\n"
                                           "เปิดตัว → เคาน์เตอร์ → หันหลังเดินไปหาแขก 1 คน → เคาน์เตอร์ → แท่น",
                     "event_dock_walk": "วันงาน (ตัวจำลอง): เปิดตัว → เคาน์เตอร์ →\nกลับแท่นด้วยปุ่มกลับแท่นของงาน",
                     "dock_via_reception": "วันงาน (ตัวจำลอง): เปิดตัว → เคาน์เตอร์ →\nกลับแท่นผ่านส่วนต้อนรับ (dock_via_reception)"}[tag]
            p = draw_event_day(layout, got, Path(png_dir) / ("event_sequence_20261121_%s.png" % tag), title, sm)
            sm["png"] = str(p)
            out("  picture: %s" % p)
        if k == 0 and in_the_way:
            # one way home is enough: the guest stands on cards 1-2
            sm["in_the_way"] = _in_the_way_report(layout, legs, in_the_way, seed, out)
            sm["new"] = sm["new"] + [b for row in sm["in_the_way"].values() for b in row["new"]]
    return res


# ---- the pre-flight --------------------------------------------------------------------
def _at(xy) -> str:
    return "(?)" if not xy else "(%.2f, %.2f)" % (xy[0], xy[1])


def known_reasons(name: str, bad: list[str], known: dict) -> list[str]:
    """The reasons of a walk outside the bounds that its KNOWN_OUTSIDE entry does not name -- [] = known.
    A walk not on the list: all of them."""
    entry = known.get(name)
    if entry is None:
        return list(bad)
    pats = entry[1] if isinstance(entry, (tuple, list)) else ()
    least = float((entry[2] if isinstance(entry, (tuple, list)) and len(entry) > 2 else {}).get("min_arrived", 1.0))

    def named(b: str) -> bool:
        if not any(re.search(pt, b) for pt in pats):
            return False
        # "arrived N of M": known down to the entry's own share only (re-review 21 Nov: "arrived \d+" took 0 of 2
        # as known for a walk that arrives 13 of 20)
        m = re.match(r"arrived (\d+) of (\d+)", b)
        return m is None or int(m.group(1)) >= least * int(m.group(2)) - 1e-9

    return [b for b in bad if not named(b)]


def judge(name: str, runs: list[dict], hazards: list[dict], *, plain: bool, ends_docked: bool,
          profile: rp.RobotProfile | None = None) -> list[str]:
    """What is wrong with these runs of one walk, against the bounds at the top (the robot's
    own: its gap, its dock's tolerance -- Emma's are MIN_ARM_GAP_M and MAX_DOCK_*)."""
    p = profile or rp.EMMA
    bad = []
    n = len(runs)
    done = [r for r in runs if r["end"] == "done"]
    if plain and len(done) < MIN_REACHED * n:
        kinds = {}
        for r in runs:
            if r["end"] != "done":
                kinds[stop_kind(r["reason"])] = kinds.get(stop_kind(r["reason"]), 0) + 1
        bad.append("arrived %d of %d (%s)" % (len(done), n, ", ".join("%s x%d" % kv for kv in sorted(kinds.items()))))
    gap = min(r["min_arm_gap"] for r in runs)
    if gap < p.min_gap_m:
        # where: KNOWN_OUTSIDE matches a reason by its place (review 21 Nov)
        bad.append("arms %.2f m from something solid at %s" % (gap, _at(min(runs, key=lambda r: r["min_arm_gap"]).get("arm_gap_at"))))
    side = [r for r in runs if "min_side_arm_gap" in r]
    if side and min(r["min_side_arm_gap"] for r in side) < p.min_gap_m:
        worst = min(side, key=lambda r: r["min_side_arm_gap"])
        bad.append("the pageant arm %.2f m from something at %s" % (worst["min_side_arm_gap"], _at(worst.get("side_arm_at"))))
    hands = [r["handshake_gap"] for r in runs if r.get("handshake_gap") is not None]
    if hands and min(hands) < p.min_gap_m:
        bad.append("the handshake's hand %.2f m from the counter's top or something" % min(hands))
    if any(r["min_person_gap"] <= 0.0 for r in runs):
        bad.append("touched a person or a thing left on the way")
    for i, h in enumerate(hazards):
        k = h.get("id", str(i))
        d = min(r["min_hazard"].get(k, 9.0) for r in runs)
        if d < float(h.get("radius_m", 0.0)):
            bad.append("centre %.2f m from floor hazard %s (keeps %.2f)" % (d, k, h["radius_m"]))
    if plain and ends_docked and p.docking.max_aside_m is not None:
        docked = [r for r in done if r["on_dock"]]
        if len(docked) < len(done):
            bad.append("%d of %d ended off the dock" % (len(done) - len(docked), len(done)))
        if docked:
            aside = max(abs(r["aside_m"]) for r in docked)
            crooked = max(abs(r["crooked_deg"]) for r in docked)
            if aside > p.docking.max_aside_m or crooked > p.docking.max_crooked_deg:
                bad.append("on the dock %.1f cm aside / %.1f deg crooked at worst" % (aside * 100, crooked))
    return bad


def summarise(runs: list[dict]) -> dict:
    done = [r for r in runs if r["end"] == "done"]
    docked = [r for r in done if r["on_dock"]]
    kinds: dict[str, int] = {}
    for r in runs:
        if r["end"] != "done":
            k = stop_kind(r["reason"])
            kinds[k] = kinds.get(k, 0) + 1
    hz = {}
    for r in runs:
        for k, v in r["min_hazard"].items():
            hz[k] = min(hz.get(k, 9.0), v)
    return {"n": len(runs), "arrived": len(done), "stops": kinds,
            "min_arm_gap": min(r["min_arm_gap"] for r in runs),
            "arm_gap_at": min(runs, key=lambda r: r["min_arm_gap"])["arm_gap_at"],
            "min_person_gap": min(r["min_person_gap"] for r in runs),
            "hazard": hz, "max_err_m": max(r["max_err_m"] for r in runs), "min_q": min(r["min_q"] for r in runs),
            "low_fit_s": round(float(np.median([r["low_fit_s"] for r in runs])), 0),
            "s": round(float(np.median([r["s"] for r in runs])), 0),
            "docked": len(docked),
            "aside_cm": round(max(abs(r["aside_m"]) for r in docked) * 100, 1) if docked else None,
            "crooked_deg": round(max(abs(r["crooked_deg"]) for r in docked), 2) if docked else None,
            **({} if not any("min_side_arm_gap" in r for r in runs) else {
                "min_side_arm_gap": min(r["min_side_arm_gap"] for r in runs if "min_side_arm_gap" in r),
                "side_arm_at": min((r for r in runs if "min_side_arm_gap" in r), key=lambda r: r["min_side_arm_gap"])["side_arm_at"]})}


def dock_lines(layout: Layout) -> list[str]:
    """The dock the walks are judged on, where it came from, and -- said, not hidden -- when
    the walks themselves dock elsewhere (floor1.json's paths read the file's pose; a re-learnt
    pose moves only the event walks)."""
    x, y, d = layout.dock
    out = ["THE DOCK in this layout: (%.3f, %.3f) %.1f deg -- %s" % (x, y, d, layout.dock_from)]
    if getattr(layout, "cleared", 0):
        out.append("  taken out of the truth, %d cells (SEEN_EMPTY): within %.1f m of the 20 Nov dock pose in the lidar's arc "
                   "(the scans on the dock saw nothing there) and map_check 20 Nov phantom #26 (seen through); the rest of "
                   "the old line at x -6.83 behind-right of the dock (y -6.55..-6.07) nobody has seen: it stays"
                   % (layout.cleared, SEEN_EMPTY[0]["r"]))
    try:
        file_pose = tuple(float(v) for v in _read(ROUTE_FILE, "the floor-1 route file")["dock"]["pose"][:3])
    except (InputError, KeyError, TypeError, ValueError):
        return out
    if layout.name != CURRENT:
        return out
    for who, pose in (("floor1.json's paths (board_walk reads the file)", file_pose),
                      ("the event walks", tuple(layout.relearnt) if layout.relearnt else file_pose)):
        apart = math.hypot(pose[0] - x, pose[1] - y)
        turn = abs((pose[2] - d + 180.0) % 360.0 - 180.0)
        if apart > 0.01 or turn > 0.5:
            out.append("  !! %s dock to (%.3f, %.3f) %.1f deg: %.1f cm / %.1f deg from it -- they will sit off the "
                       "contacts here, as on the robot" % (who, pose[0], pose[1], pose[2], apart * 100, turn))
    return out


def preflight(runs: int = 10, seed: int = 0, only: str | None = None, layout: Layout | None = None,
              adverse: bool = True, out=print, spots: dict[str, dict] | None = None, known: dict | None = None,
              spots_file=None) -> dict:
    """Every floor1.json path, every event walk and the ceremony's walk steps, `runs` seeded
    times plain and a few times per adverse case; each spot on its own. `spots`: positions a
    person gave (load_spots). Raises InputError when an input cannot be read or nothing
    matches `only`. -> {"walks", "broken" (every walk outside the bounds), "unexpected" (those
    not in `known`), "known_outside", "known_now_inside", "not_checked", "skipped",
    "touches", "spots"}."""
    known = KNOWN_OUTSIDE if known is None else known
    layout = layout or load_layout(spots_file=spots_file)
    if layout is None:
        raise InputError("the 16 Nov layout is not on this machine (.tmp/map-check/20261116-floor1-dock, "
                         ".tmp/floor1-20261116): nothing to walk in")
    out("WHAT THIS DOES NOT SHOW (the same every run):")
    for line in NOT_SHOWN:
        out("  - " + line)
    out("")
    for line in dock_lines(layout):
        out(line)
    out("")
    walks: dict[str, tuple] = {}
    skipped = []
    for name, steps in floor1_paths().items():
        if _in_the_room(steps):
            skipped.append(name)
            continue
        walks["path " + name] = (route_doc(layout), steps, starts_for(name, steps, layout))
    plan = event_plan(layout, spots)
    for name, (steps, start) in plan["walks"].items():
        walks[name] = (route_doc(layout, event=True), steps, [start])
    sequences = event_day_sequences(layout) if layout.name == CURRENT and not getattr(layout, "package", None) else {}
    if layout.name == CURRENT and not getattr(layout, "package", None) and not sequences:
        # review 21 Nov: a card renamed or its gesture changed made the event day vanish from the pre-flight with
        # exit 0 -- said where the unchecked things are said
        plan["not_checked"].append("the event day: the event file has no debut card, no counter card (a scene to "
                                   "beside_host with the handshake) or no card home (a scene at the dock)")
    # card 1's tour (23 Nov): its own sequence, its guests where --spots / the recorded spots put them, else ASSUMED
    tours = tour_sequences(layout, spots) if layout.name == CURRENT and not getattr(layout, "package", None) else {}
    if layout.name == CURRENT and not getattr(layout, "package", None) and not tours:
        plan["not_checked"].append("card 1's tour: the event file has no debut card with a `tour`, no counter for it, or "
                                   "no card home")
    chosen = {n: v for n, v in walks.items() if not only or only in n}
    chosen_seq = {n: v for n, v in {**sequences, **tours}.items() if not only or only in n}
    guest_only = bool(only) and "in the way" in only
    if guest_only:
        if not adverse:
            raise InputError("--only %r with --no-adverse checks nothing (the guest cases are adverse cases)" % only)
        chosen, chosen_seq = {}, {}
    if not chosen and not chosen_seq and not (guest_only and sequences):
        raise InputError("no walk matches --only %r (there are %d: %s ...)" % (only, len(walks), ", ".join(list(walks)[:4])))
    result, broken = {}, []
    touches: dict[str, list[str]] = {}
    for name, (doc, steps, starts) in chosen.items():
        ends_docked = bool(steps) and steps[-1].get("do") == "dock" or \
            (len(steps) > 1 and steps[-1].get("do") == "screen" and steps[-2].get("do") == "dock")
        arm_out = name in plan.get("pageant", ())
        wave = name in plan.get("wave_after", ())                # out of the box: the arm out once arrived (23 Nov)
        plain = [run_walk(layout, doc, steps, starts[k % len(starts)], seed=seed + k, pageant=arm_out, wave_after=wave)
                 for k in range(runs)]
        row = summarise(plain)
        bad = judge(name, plain, layout.hazards, plain=True, ends_docked=ends_docked)
        if name.startswith(("event ", "ceremony ")) and starts[0][2] is not None:
            # the event's own walks: no turn of more than event_tour.TURN_FIRST_DEG left to the board, at a MoveTo's
            # start or at its end (review 23 Nov) -- board_walk's own paths are the board's
            from app import event_tour

            bad += ["a turn the board makes blind -- %s" % r for r in event_tour.rear_turns(steps, starts[0])]
        row["adverse"] = {}
        notes: list[str] = []
        if adverse:
            rng = np.random.default_rng(seed + 991)
            for case, world in adverse_cases(steps, starts[0], rng).items():
                r = [run_walk(layout, doc, steps, starts[0], seed=seed + 500 + k, pageant=arm_out, wave_after=wave,
                              **json.loads(json.dumps(world))) for k in range(max(1, runs // 5))]
                row["adverse"][case] = summarise(r)
                found = judge(name, r, layout.hazards, plain=False, ends_docked=False)
                ours = [t for x in r for t in x["touched"] if "ours" in t]
                if ours:
                    bad.append("[%s] touched while our own code drove: %s" % (case, ours[0]))
                if min(x["min_person_gap"] for x in r) <= 0.0 or min(x["min_arm_gap"] for x in r) <= 0.0:
                    touches.setdefault(case, []).append(name)
                    if ADVERSE_TOUCH_SETS_EXIT:
                        bad.append("[%s] touched" % case)
                notes += ["[%s] %s" % (case, b) for b in found]
        result[name] = row
        row["notes"] = notes
        if bad:
            broken.append((name, bad))
        out("%-52s %2d/%-2d  arms %5.2f m  err %.2f m  Q>=%d  %4.0f s  %s%s%s%s" % (
            name[:52], row["arrived"], row["n"], row["min_arm_gap"], row["max_err_m"], row["min_q"], row["s"],
            "" if row["docked"] == 0 else "dock %s cm / %s deg  " % (row["aside_cm"], row["crooked_deg"]),
            "" if row.get("min_side_arm_gap") is None else "pageant arm %.2f m at %s  " % (
                row["min_side_arm_gap"], row["side_arm_at"]),
            "" if not bad else "  <-- " + "; ".join(bad),
            "" if not notes else "  (in the way: " + "; ".join(notes) + ")"))
    for name, legs in chosen_seq.items():
        # the event day in one world, walk after walk (event_day_sequences): plain runs only -- each of its
        # walks is a walk of its own above, with its adverse cases
        seq_runs = [run_sequence(layout, legs, seed=seed + k) for k in range(runs)]
        flat = [sequence_as_walk(r) for r in seq_runs]
        row = summarise(flat)
        row["sequence"] = sequence_summary(seq_runs)
        # card 3's guest is judged here too (review 22 Nov: only event_day judged it, the gate did not)
        bad = judge(name, flat, layout.hazards, plain=True, ends_docked=True) + guest_faults(row["sequence"]["guest"])
        if name in tours:
            row["tour"] = tour_summary(seq_runs)
            bad += tour_faults(row["tour"])                       # its guests, the counter again, its turns round
        result[name] = row
        if bad:
            broken.append((name, bad))
        sq = row["sequence"]
        out("%-52s %2d/%-2d  arms %5.2f m  hand to the counter's top %.2f m  front %.2f-%.2f m  %s%s" % (
            name[:52], row["arrived"], row["n"], row["min_arm_gap"], sq["handshake_gap"], sq["front_to_top"][0],
            sq["front_to_top"][1], "" if row["docked"] == 0 else "dock %s cm / %s deg" % (row["aside_cm"], row["crooked_deg"]),
            "" if not bad else "  <-- " + "; ".join(bad)))
        if name in tours:
            for line in tour_lines(row["tour"], legs):
                out("    " + line)
    # the event day's cards 1-2 (not card 1's tour: its first two walks are the same, and walking all of it in every
    # guest case doubled these runs for nothing they judge)
    first_seq = next(iter((sequences if guest_only else {n: v for n, v in chosen_seq.items() if n in sequences}).items()),
                     None)
    way: dict = {}
    if first_seq is not None and adverse:
        # a guest in the way of cards 1-2 (owner 21 Nov): the cards' legs are under the 2.4 m the walks' own
        # adverse cases take, so this is the only place they meet one. Not walks (review 21 Nov: they were
        # counted as 4 more walks); a NEW of theirs goes straight to `unexpected` below.
        way = _in_the_way_report(layout, first_seq[1], max(1, min(runs, IN_THE_WAY_RUNS)), seed, out)
    # a known walk is known only for the reasons its entry names: any other reason is NEW (review 21 Nov --
    # the list matched names, and reception_tour going 20/20 -> 13/20, lobby_survey crossing a hazard, passed as known)
    unexpected = [(n, known_reasons(n, b, known)) for n, b in broken if known_reasons(n, b, known)]
    unexpected += [("in the way: " + c, r["new"]) for c, r in way.items() if r["new"]]
    known_outside = [(n, b) for n, b in broken if n in known and not known_reasons(n, b, known)]
    known_now_inside = [n for n in known if n in result and n not in dict(broken)]
    out("")
    out("EACH SPOT ON ITS OWN (16 Nov's furniture; arms = the 0.60 x 0.78 m rectangle; fit = simulated scan on the board's map, 8 headings):")
    try:
        keep = _read(EVENT_FILE, "the event file").get("no_walk_up_below_y")
    except InputError:
        keep = None
    where = dict(plan["where"])
    for sid in plan["standing"]:
        m = spots[sid]
        where[sid] = (m["x"], m["y"], m["face_deg"], m["name"] + " (a standing spot: no file walks to it)")
    spot_rows = spot_report(layout, where, keep_out_below_y=keep)
    for line in spot_lines(spot_rows):
        out(line)
    out("")
    if skipped:
        out("NOT SIMULATED (through the multimedia room's door, closed in this truth): " + ", ".join(skipped))
    out("NOT CHECKED (%d):" % len(plan["not_checked"]))
    for line in plan["not_checked"]:
        out("  - " + line)
    if not plan["not_checked"]:
        out("  (every spot the event and the ceremony name has a position)")
    out("")
    out("THINGS IN THE WAY (not in the exit code: the board's margin round a thing it has just seen is assumed, 0.36 m,")
    out("3 cm less than the arms' half-width -- see ADVERSE_TOUCH_SETS_EXIT):")
    if not adverse:
        out("  not run (--no-adverse)")
    for case in sorted(touches):
        out("  %d of %d walks TOUCH with '%s' under the assumed planner margin" % (len(touches[case]), len(result), case))
    guest_touch = [c for c, r in way.items() if r["touch_gap"] <= 0.0]
    if guest_touch:
        out("  %d of %d guest-in-the-way cases TOUCH the guest under the assumed planner margin: %s" % (
            len(guest_touch), len(way), "; ".join(guest_touch)))
    if adverse and not touches and not guest_touch:
        out("  no walk touches anything in any case")
    out("")
    for n, b in known_outside:
        out("KNOWN outside the bounds: %s -- %s\n    known: %s" % (n, "; ".join(b), _why(known[n])))
    for n in known_now_inside:
        out("KNOWN-OUTSIDE walk is INSIDE the bounds now: %s -- take it off KNOWN_OUTSIDE" % n)
    for n, b in unexpected:
        out("NEW outside the bounds: %s -- %s%s" % (n, "; ".join(b), "" if n not in known else
                                                     "  (a KNOWN walk, a reason its entry does not name)"))
    return {"walks": result, "broken": broken, "unexpected": unexpected, "known_outside": known_outside,
            "known_now_inside": known_now_inside, "not_checked": plan["not_checked"], "skipped": skipped,
            "touches": touches, "spots": spot_rows, "in_the_way": way}


# ---- a map package (app/map_package.py) and another robot (app/robot_profile.py) -----------
def layout_from_package(path) -> Layout:
    """A portable map package as a layout: the board's map = the package's `map` layer, the
    truth = its `lidar_truth` layer (the map itself when it has none: then nothing is
    furniture the board does not know), glass = its `glass` layer, the dock and the floor
    hazards from its places. Raises InputError for a package that cannot be read."""
    from app import map_package as mp

    try:
        pkg = mp.read(path)
    except mp.PackageError as e:
        raise InputError(str(e)) from None
    dock = (pkg["places"].get("dock") or {}).get("pose")
    if not dock or len(dock) < 3 or dock[2] is None:
        raise InputError("the package %s has no dock pose (x, y, deg)" % path)
    board = Grid(mp.explore_blob(pkg["grid"]))
    truth = Grid(mp.explore_blob(pkg["truth"] if pkg["truth"] is not None else pkg["grid"]))
    hazards = [h for h in pkg["places"].get("hazards") or [] if isinstance(h, dict) and "x" in h]
    lay = Layout(truth, board, tuple(float(v) for v in dock[:3]), glass=pkg["glass"], hazards=hazards,
                 name="package %s" % pkg["manifest"].get("name"))
    lay.dock_from = "the package's dock (%s)" % (pkg["places"]["dock"].get("source") or "no source said")
    lay.route_dock = dict(pkg["places"]["dock"])
    lay.package = pkg
    return lay


def package_plan(layout: Layout) -> dict:
    """event_plan's shape for a package's own places: every placed spot to every other, by its
    `via` points (the dock is a spot: walks to it end with a dock step)."""
    places = layout.package["places"]
    where: dict[str, tuple] = {"dock": (layout.dock[0], layout.dock[1], None, "dock")}
    via: dict[str, list] = {"dock": []}
    not_checked = []
    for sp in places.get("spots") or []:
        pose = sp.get("pose")
        if not pose:
            not_checked.append("spot %s (%s): no position" % (sp.get("id"), sp.get("name") or sp.get("id")))
            continue
        where[sp["id"]] = (float(pose[0]), float(pose[1]), None if len(pose) < 3 else pose[2], sp.get("name") or sp["id"])
        via[sp["id"]] = [list(p) for p in sp.get("via") or []]
    for sp in places.get("unplaced") or []:
        not_checked.append("spot %s (%s): no position" % (sp.get("id"), sp.get("name") or sp.get("id")))
    walks = {}
    for a, (ax, ay, af, _) in where.items():
        for b, (bx, by, bf, _) in where.items():
            if a == b:
                continue
            start = tuple(layout.dock) if a == "dock" else (ax, ay, 90.0 if af is None else af)
            if b == "dock":
                steps = [{"do": "dock"}]
            else:
                steps = [{"do": "undock"}] + [{"to": p[:2], "speed": 0.3, "label": "via"} for p in via[b]] + \
                        [dict({"to": [bx, by], "speed": 0.3, "label": b}, **({} if bf is None else {"face": bf}))]
            walks["%s -> %s" % (a, b)] = (steps, start)
    return {"walks": walks, "where": where, "standing": [], "not_checked": not_checked}


#: Spots where the widest point at the lidar plane comes nearer than this to a thing are
#: listed to look at at the TRAY's height: a table top overhangs its legs, a counter its base,
#: and a map made at another robot's lidar plane has neither top.
TRAY_LOOK_M = 0.30

#: What a profile check does not show, printed at its top every run.
PROFILE_NOT_SHOWN = (
    "the map and the furniture are what EMMA's lidar saw at ITS plane (knee height, never measured): a robot whose "
    "lidar sits higher or lower sees other things -- every gap here is at Emma's lidar plane",
    "table tops and counters at tray height are in no map: spots near a thing are listed to look at, nothing is judged there",
    "its own navigation is not simulated: the walks are the board-planner model of the floor simulator (straight when its "
    "map is clear, else a shortest path, kept `planner.inflate_m` off what it knows) with this robot's body, lidar arc "
    "and speed -- the vendor's planner, localiser and docking are its own",
    "its localiser is taken as exact (Emma's measured slide in the dock's nook is her board's, not this robot's)",
    "Emma's own lines (creep, forward, back_to), turns and her dock are left out of its walks; a walk home ends at the "
    "dock's approach point",
)


def plain_legs(steps: list[dict], dock: dict | None) -> list[dict]:
    """A walk of ours as another robot would be sent along it: its plain points (planner legs).
    Emma's own lines, turns, waits, screens and dock are hers; a robot that docks itself is
    sent to the dock's approach point instead."""
    out = []
    for s in steps:
        if s.get("do") == "dock":
            ap = (dock or {}).get("approach")
            if ap:
                out.append({"to": [float(ap[0]), float(ap[1])], "speed": 0.3, "label": "the dock's approach (it docks itself)"})
        elif "to" in s and not any(k in s for k in ("creep", "forward", "back_to")) and s.get("do") is None:
            out.append({k: v for k, v in s.items() if k in ("to", "face", "speed", "label")})
    return out


def fit_area(layout: Layout, profile: rp.RobotProfile, *, box=(-12.5, -6.0, -7.4, 2.6), step: float = 0.1) -> dict:
    """Floor (m2) in `box` (the lobby) where the robot's centre can be with its widest point
    clear by its gap, and where it can also turn on the spot -- at the lidar plane."""
    x0, x1, y0, y1 = box
    sol = layout.solid
    sol = sol[(sol[:, 0] > x0 - 1.5) & (sol[:, 0] < x1 + 1.5) & (sol[:, 1] > y0 - 1.5) & (sol[:, 1] < y1 + 1.5)]
    xs, ys = np.arange(x0, x1 + 1e-6, step), np.arange(y0, y1 + 1e-6, step)
    gx, gy = np.meshgrid(xs, ys)
    pts = np.c_[gx.ravel(), gy.ravel()]
    near = np.full(len(pts), 9.0)
    for k in range(0, len(sol), 2000):
        chunk = sol[k:k + 2000]
        near = np.minimum(near, np.hypot(pts[:, None, 0] - chunk[None, :, 0], pts[:, None, 1] - chunk[None, :, 1]).min(axis=1))
    half = profile.body.reach_width_m / 2 + profile.min_gap_m
    turn = profile.body.turn_radius_m + profile.min_gap_m
    return {"pass_m2": round(float((near >= half).sum()) * step * step, 1),
            "turn_m2": round(float((near >= turn).sum()) * step * step, 1), "box": list(box), "step_m": step}


def profile_check(profile: rp.RobotProfile, *, layout: Layout | None = None, spots: dict[str, dict] | None = None,
                  runs: int = 3, seed: int = 0, only: str | None = None, out=print, spots_file=None) -> dict:
    """`spots_file`: a robot spots file -- its re-learnt dock is the dock, as in `preflight`
    (review 20 Nov: it was dropped here). Our places and the ways between them for ANOTHER robot (owner 20 Nov: a serving robot):
    each spot with its body (fit, turn, the planner's refusal, a tray's height to look at), each
    walk as plain planner legs with its body, lidar arc and speed, and how much of the lobby it
    fits in against Emma. -> {"walks", "broken", "spots", "spot_problems", "tray", "area",
    "not_checked"}. Raises InputError when nothing can be read or matched."""
    layout = layout or load_layout(spots_file=spots_file)
    if layout is None:
        raise InputError("the 16 Nov layout is not on this machine: nothing to walk in (or give --package)")
    for line in rp.describe(profile):
        out(line)
    out("WHAT THIS DOES NOT SHOW (the same every run):")
    for line in PROFILE_NOT_SHOWN:
        out("  - " + line)
    out("")
    for line in dock_lines(layout):
        out(line)
    out("")
    plan = package_plan(layout) if getattr(layout, "package", None) else event_plan(layout, spots)
    dock = route_doc(layout)["dock"] if getattr(layout, "route_dock", None) else route_doc(layout, event=True)["dock"]
    approach = dock.get("approach")
    doc = {"dock": dock, "min_quality": 1, "patience_s": 45, "events": True}
    chosen = {n: v for n, v in plan["walks"].items() if not only or only in n}
    if not chosen:
        raise InputError("no walk matches --only %r (there are %d)" % (only, len(plan["walks"])))
    result, broken = {}, []
    for name, (steps, start) in chosen.items():
        legs = plain_legs(steps, dock)
        if not legs:
            continue
        if math.hypot(start[0] - layout.dock[0], start[1] - layout.dock[1]) < 0.05 and approach:
            # it does not live on Emma's dock: it sets out from the dock's approach point, facing the first leg
            first = legs[0]["to"]
            start = (float(approach[0]), float(approach[1]), math.degrees(math.atan2(first[1] - approach[1], first[0] - approach[0])))
            if math.hypot(first[0] - approach[0], first[1] - approach[1]) < 0.3:
                legs = legs[1:]
                if not legs:
                    continue
        rs = [run_walk(layout, doc, legs, start, seed=seed + k, loc=False, profile=profile) for k in range(runs)]
        row = summarise(rs)
        bad = judge(name, rs, layout.hazards, plain=True, ends_docked=False, profile=profile)
        result[name] = row
        if bad:
            broken.append((name, bad))
        out("%-52s %2d/%-2d  widest %5.2f m  %4.0f s%s" % (name[:52], row["arrived"], row["n"], row["min_arm_gap"], row["s"],
                                                         "" if not bad else "  <-- " + "; ".join(bad)))
    out("")
    try:
        keep = _read(EVENT_FILE, "the event file").get("no_walk_up_below_y") if not getattr(layout, "package", None) else None
    except InputError:
        keep = None
    where = dict(plan["where"])
    for sid in plan.get("standing") or []:
        m = spots[sid]
        where[sid] = (m["x"], m["y"], m["face_deg"], m["name"] + " (a standing spot)")
    out("EACH SPOT ON ITS OWN (%s: widest %.2f m, %.2f long; turns in %.2f m):" % (
        profile.id, profile.body.reach_width_m, profile.body.length_m + 2 * profile.body.tray_overhang_m, profile.body.turn_radius_m))
    rows = spot_report(layout, where, keep_out_below_y=keep, profile=profile)
    for line in spot_lines(rows, profile, gates=profile.board_api == "slamware"):
        out(line)
    problems = [r["id"] for r in rows if r["id"] != "dock" and (
        r["arm_gap_m"] < profile.min_gap_m or r["turn_gap_m"] < 0.03 or r["board_refuses"])]
    tray = []
    if profile.body.tray_height_m is not None:
        tray = [{"id": r["id"], "name": r["name"], "gap_m": r["arm_gap_m"]} for r in rows
                if r["id"] != "dock" and r["arm_gap_m"] < TRAY_LOOK_M]
        out("")
        out("LOOK AT THESE AT TRAY HEIGHT (%.2f m): the widest point comes within %.2f m of a thing at the lidar plane" % (
            profile.body.tray_height_m, TRAY_LOOK_M))
        for t in tray:
            out("  %-18s %s: %.2f m -- if that thing is a table or a counter, its top reaches further than its legs" % (
                t["id"][:18], t["name"], t["gap_m"]))
        if not tray:
            out("  none")
    area = {"this": fit_area(layout, profile), "emma": fit_area(layout, rp.EMMA)}
    out("")
    out("THE LOBBY (x %.1f..%.1f, y %.1f..%.1f, at the lidar plane): its centre can be on %.1f m2 (Emma %.1f), it can turn "
        "on the spot on %.1f m2 (Emma %.1f)" % (tuple(area["this"]["box"]) + (area["this"]["pass_m2"], area["emma"]["pass_m2"],
                                                area["this"]["turn_m2"], area["emma"]["turn_m2"])))
    out("")
    out("NOT CHECKED (%d):" % len(plan["not_checked"]))
    for line in plan["not_checked"]:
        out("  - " + line)
    for n, b in broken:
        out("OUTSIDE the bounds: %s -- %s" % (n, "; ".join(b)))
    return {"profile": profile.to_dict(), "walks": result, "broken": broken, "spots": rows, "spot_problems": problems,
            "tray": tray, "area": area, "not_checked": plan["not_checked"]}


def main(argv=None) -> int:
    import argparse

    # the report names Thai spots: redirected to a file on Windows, print() is cp1252 and the run died at
    # "NOT CHECKED" with exit 1 -- the gate's own failure code (review 21 Nov)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("what", choices=["preflight", "calibrate", "gaps", "assumptions", "profile", "event-day"])
    ap.add_argument("--runs", type=int, default=None, help="seeded runs a walk (preflight 10, profile 3)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--only")
    ap.add_argument("--no-adverse", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--spots", help="positions for spots: the marking page's JSON, or the robot's <event>.spots.json "
                                    "(its `dock`, re-learnt at the robot, is then the dock too)")
    ap.add_argument("--profile", default="serving_example",
                    help="`profile`: the robot -- serving_example (default), emma, or a JSON file (app/robot_profile.py)")
    ap.add_argument("--package", help="`profile`: walk a portable map package (scripts/map_package.py export) "
                                      "instead of the 16 Nov layout and the event file")
    args = ap.parse_args(argv)
    if args.what == "profile":
        try:
            prof = rp.load(args.profile)
        except (KeyError, ValueError, OSError) as e:
            print("ERROR -- nothing was checked: %s" % e)
            return 2
        try:
            lay = layout_from_package(args.package) if args.package else None
            res = profile_check(prof, layout=lay, spots=load_spots(args.spots) if args.spots else None,
                                runs=max(1, args.runs or 3), seed=args.seed, only=args.only, spots_file=args.spots)
        except InputError as e:
            print("ERROR -- nothing was checked: %s" % e)
            return 2
        if args.json:
            Path(args.json).write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print("\n%s: %d walk(s), %d outside the bounds; %d spot(s) where it does not fit / turn / is refused; %d to look at "
              "at tray height; %d not checked for want of a position" % (
                  prof.id, len(res["walks"]), len(res["broken"]), len(res["spot_problems"]), len(res["tray"]),
                  len(res["not_checked"])))
        return 1 if res["broken"] or res["spot_problems"] else 0
    if args.what == "assumptions":
        print("\n".join("- " + a for a in assumptions()))
        return 0
    if args.what == "event-day":
        # the owner's event day in one world (20 Nov): the debut out of the dock, the counter, home two ways;
        # a picture a way home for the owner (.tmp/floor1-sim/event_sequence_20261121_*.png)
        try:
            # + a guest in the way of cards 1-2 (owner 21 Nov), unless --no-adverse
            res = event_day(args.runs or 20, args.seed, spots_file=args.spots, png_dir=ROOT / ".tmp" / "floor1-sim",
                            in_the_way=0 if args.no_adverse else max(1, min(args.runs or 20, IN_THE_WAY_RUNS)))
        except InputError as e:
            print("ERROR -- nothing was checked: %s" % e)
            return 2
        if args.json:
            Path(args.json).write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        return 1 if any(r["new"] for r in res.values()) else 0
    if args.what == "calibrate":
        import floor_sim_calibrate

        return floor_sim_calibrate.main()
    if args.what == "gaps":
        import floor_sim_calibrate

        return floor_sim_calibrate.gaps()
    try:
        res = preflight(args.runs or 10, args.seed, args.only, adverse=not args.no_adverse,
                        spots=load_spots(args.spots) if args.spots else None, spots_file=args.spots)
    except InputError as e:
        print("ERROR -- nothing was checked: %s" % e)
        return 2
    if args.json:
        Path(args.json).write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    n = len(res["walks"])
    print("\n%d walk(s): %d inside the bounds, %d known outside (listed above), %d NEW outside; %d not checked for want of a "
          "position; touches with a thing in the way: %s" % (
              n, n - len(res["broken"]), len(res["known_outside"]), len(res["unexpected"]), len(res["not_checked"]),
              ", ".join("%s %d" % (c, len(v)) for c, v in sorted(res["touches"].items())) or "none"))
    if res.get("in_the_way"):
        print("+ %d guest-in-the-way case(s) of cards 1-2, %d NEW" % (
            len(res["in_the_way"]), sum(1 for r in res["in_the_way"].values() if r["new"])))
    return 1 if res["unexpected"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
