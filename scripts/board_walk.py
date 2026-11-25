"""Walk a remembered path on the navigation board, on the robot (floor 1, 28 ต.ค. 2026).

Owner: "ให้ทำการเดินจำการเข้าห้องนี้และส่วนต้อนรับทั้งหมด แล้วกลับแท่นให้ได้ด้วย".
Floor 2 runs on scripts/drive_route.py (our own lanes against measured walls,
because the board could not localize in the furnished rooms there). Floor 1 is
different: the board's own map is a lidar scan laid over the plan, it holds a
fix (Q 70-80) everywhere we went, and its MoveTo planner got through the
multimedia room's door and its ~0.9 m gap. So this is a thin runner:

* a path is a list of steps from `data/robot/waypoints/<floor>.json`:
  ``{"to": [x, y], "face": deg?, "speed": m/s?}`` = one MoveTo (``with_yaw``
  when ``face`` is given -- without the flag the board ignores the yaw),
  ``{"do": "undock"}``, ``{"do": "dock"}``;
* points are close together where the line matters (the gap between the
  table's chairs and the round chairs): the board plans its own way between
  two points, and close points are how the line is held;
* a leg with no progress for 8 s is cancelled and the run stops where it is
  -- no skipping ahead past a point it could not reach;
* docking is our own: to the pre-dock spot on the dock's line facing out,
  then reverse straight in under velocity control (the floor-2 way,
  drive_route.back_into_dock, owner 7 ต.ค. "เดินหน้าเข้ามาใกล้ๆ แล้วค่อยหมุนตัว
  ถอยง่ายกว่า"), stopping on on_dock, a bumper event, a stall, the heading
  leaving the line, or overshooting the dock pose. The lidar does not see
  behind, and on floor 1 it does not see the dock either (lower than the
  scan plane) -- the dock pose is the only guide, so the speeds are low.
  ``--register-dock`` registers the board's home dock once on it, when the
  map has none (GoHome needs one);
* ``{"back_to": [x, y, yaw_deg]}`` reverses straight along the line a `forward` came out on,
  onto the dock at its start (owner 11 Nov, "ขอปุ่มกลับแท่น"): velocity only, no map --
  `Walker.back_to`;
* ``{"glide": [[x, y], ...], "face": deg?, "speed": m/s?}`` (24 Nov): straight lines over OPEN floor and
  turns on the spot, ours end to end -- `Walker.glide`;
* ``{"creep": [[x, y, speed?], ...]}`` is a polyline we drive ourselves under
  velocity control where the board will not go (the multimedia room):
  `CreepLine` is its tick logic with no I/O, so scripts/walk_sim.py and the
  tests drive the same code against a simulated base and lidar. The board's
  pose jumps are eased in (`PoseFilter`), arbitrated by the lidar against
  the board's own map (`MapFit`); a base holding itself for something ahead
  is told apart from one caught on chair wheels below the lidar.

Run on the robot with the server's Python, from its code directory:
    cd /data/local/emma-server/code && ../runtime/py -s scripts/board_walk.py \\
        data/robot/waypoints/floor1.json multimedia_to_dock [--map] [--register-dock]
``--map`` keeps SLAM mapping on while walking (the lidar rewrites what it
sees -- "เดินจำ"), off again before a creep line (the multimedia room's door:
never mapped, CLAUDE.md), before docking and at the end. SIGTERM / SIGHUP
stop it the Ctrl-C way (`stop_on_signals`).
"""
from __future__ import annotations

import argparse
import json
import math
import re
import signal
import struct
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable

# app.slamware_sdk: the repo (or the robot's code directory) above scripts/,
# and the working directory when the script was pushed somewhere else.
sys.path[:0] = [str(Path(__file__).resolve().parent.parent), "."]

BOARD ="http://192.168.11.1:1448"
SDK_HOST, SDK_PORT = "192.168.11.1", 1445
MOVE_TO = "slamtec.agent.actions.MoveToAction"
STOP_EVENTS = {"BUMPER_TRIGGERED", "PATH_OCCUPIED", "ROBOT_BLOCKED", "DEVICE_ERROR"}

#: A leg that moves less than this for STUCK_S is given up (the board keeps
#: "replanning" in front of a closed door or a person for as long as it likes).
STUCK_S = 8.0
#: Where a point the robot rolls through hands over to the next one.
HANDOVER_M = 0.35
#: A leg the board ends > 0.6 m short of its own accord is asked again once.
LEG_TRIES = 2
LEG_RETRY_WAIT_S = 2.0
#: A leg with a heading that the board ends further off it than this is
#: turned the rest of the way on the spot.
FACE_FIX_DEG = 8.0
LEG_MAX_S = 120.0
MIN_QUALITY = 50
#: `{"patience_s": N}` in a route file (only app/event_walk.py writes it): a leg
#: that stands still waits this long before giving up, not STUCK_S -- and says so
#: on `@@` lines at HELD_NOTE_S. Owner 5 Nov: the launch event, where the thing
#: in the way is a guest who steps aside when asked. Every other route file
#: stops at STUCK_S as before.
HELD_NOTE_S = 3.0
#: `{"min_quality": N}` in a route file replaces MIN_QUALITY at the start of a walk.
#: Measured 5 Nov on the 2,305 frames of the 4 Nov lobby walks: the board's Q was
#: never under 41, 215 of the survey's 1,966 under 50, and 153 of 442 at the
#: event's own "entrance" spot -- MIN_QUALITY refused the walk back from there
#: about one time in three, with a fix good enough to have walked there. event_walk
#: writes 40, the bar `leg` already holds mid-walk (the stale pose of 4 Nov read Q32).
#: `{"min_fit": F}` in a route file: at the start of a walk the lidar must also lie on
#: the map at the board's pose (the median of START_FIT_FRAMES). Q alone does not know
#: the wrong map: 5 Nov, carried up to floor 2 with floor 1's map still loaded, the
#: board read Q41-45 at floor 1 coordinates and event_walk's bar of 40 would have
#: let it walk, on a floor it had no map of. The lidar said 0.15 (0.14 on 4 Nov, the
#: other way round). Standing at a right pose on floor 1, Q down to 42: 0.23 at the
#: lowest of 49 frames, 0.26 for a median of three. event_walk writes it.
START_FIT_FRAMES = 3
#: A dock pose is only written when the lidar agrees (`--set-dock-pose`): the dock
#: pose fits 0.68-0.76, the stale one 0.14 (4 Nov) -- MapFit.score.
DOCK_FIT_MIN = 0.45
#: `--find-dock-pose` (owner 19 Nov: "ต้องมาตั้งแท่นใหม่ทุกครั้งที่ทำ" -- the dock is put down again at every
#: setup; that night 11 cm deeper and 9 cm aside, the file's pose fitting 0.14 and a search round it
#: 0.71): the best pose for the lidar near the file's dock, searched -- never moved to. The best of a
#: search is the best of many tries, so its bar is above DOCK_FIT_MIN's single pose; a best on the
#: search's own edge is a dock further than it looks (not taken: the true one is outside).
DOCK_FIND_RADIUS_M = 0.30
DOCK_FIND_TURN_DEG = 8.0
DOCK_FIND_MIN = 0.55
#: drive_route's numbers for the floor-2 dock, the same charger model.
PREDOCK_M = 0.45
BACK_SPEED = 0.06
BACK_SLOW_M = 0.10
#: "ลื่นสมูทขึ้น" (owner 28 ต.ค.): the first 20 cm of the 45 cm back at 0.08;
#: the contacts are still met at drive_route's 0.06 and 0.036.
BACK_FAST_SPEED = 0.08
BACK_FAST_FROM_M = 0.25
#: How far past the dock pose a reverse may go without contact. 0.04 on
#: floor 2, where the dock pose was the board's own registration; here it is
#: an estimate until --register-dock has run, so a little more (the stall
#: check is what stops a push into something).
OVERSHOOT_M = 0.04
OVERSHOOT_ESTIMATED_M = 0.10
#: ... and past it without contact she goes ON, slowly, looking for the contacts (23 Nov, floor 1, owner: the
#: charger was moved deeper and may be pushed by the robot). Lidar fits with her ON the contacts that night lay
#: 9-25 cm deeper than the file's dock pose ((-7.10, -6.84) 168: (-7.01, -6.869), (-6.849, -6.841), (-6.987, -6.841),
#: (-6.997, -6.854), (-7.001, -6.875), (-6.866, -6.89)); five map docks and the re-learn's back_to stopped "0.04-0.07 m
#: past the dock pose and no contact" (one more "wheels stopped after 1.63 m"), while a back_to aimed 0.30-1.30 m
#: deeper met the contacts EVERY time in 7-23 s, the board reading 0.23-0.29 m past the file's pose there (walk
#: frames 20261123-2*-moving). And on every one of those reverses the board's pose moved 13-17 cm deeper in ONE
#: 0.6 s frame (~2 cm of driving) at 0.10-0.15 m out -- so "0.04 past" by the pose was not 0.04 past in truth.
#: The seek: DOCK_SEEK_SPEED, the heading held as the reverse held it, and it stops on on_dock, a stall (blocked: the
#: dock's body with no power, or something behind), the bumper, off its line (DOCK_SEEK_ASIDE_M / DOCK_SEEK_YAW_DEG),
#: a pose jump, or DOCK_SEEK_M ASKED (speed x time -- an upper bound on what the wheels did; the pose snaps deeper and
#: would end it short) -- a cap reached is not tried again. Only from a reverse ON its line, and never once this run
#: has stalled (`_dock_stalled`): the lidar does not see behind her. The price: a dock with no power (4 Nov) is
#: pushed up to DOCK_SEEK_M unless its body stops the wheels first. 0 = the old stop at the overshoot.
DOCK_SEEK_M = 0.30
DOCK_SEEK_SPEED = 0.6 * 0.06                       # BACK_SPEED x 0.6, the slow end's own speed
DOCK_SEEK_ASIDE_M = 0.05
DOCK_SEEK_YAW_DEG = 3.0
DOCK_SEEK_S = 1.5 * DOCK_SEEK_M / DOCK_SEEK_SPEED + 5.0
HEADING_GUARD_DEG = 5.0
STALL_FROM_M = 0.05
STALL_S = 1.2
STALL_MPS = 0.012
#: ... over STALL_S, as ever, the NET of the pose's steps -- but a step of STALL_JUMP_M or more in one read (a tenth of a
#: second at 0.08 m/s is under 1 cm) is the board's localization, not her: left out; and not while the pose went
#: STALL_PATH_M altogether (a correction spread over several reads). Over STALL_NET_S the net alone decides.
#: 24 Nov, 21:26 and 22:29, reversing at ~0.054 m/s, the board's pose jumped 4.5 cm outward at the same place (x -7.85)
#: and 1.2 s came to nothing -- "the wheels stopped", with the lidar in front showing her still backing (3.29 -> 3.36 m)
#: and nothing behind (the owner stood there).
STALL_JUMP_M, STALL_PATH_M = 0.03, 0.03
STALL_NET_S, STALL_NET_M = 2.5, 0.02
UNDOCK_SPEED = 0.15
#: Past the first contact, onto the springs (see Walker._seat), then how long
#: the robot has to stay on the dock before it counts.
SEAT_M = 0.015
SEAT_SPEED = 0.05
SETTLE_S = 2.0
DOCK_TRIES = 2
#: Further than this aside of the dock's line at the pre-dock spot, she steps onto the line
#: before reversing (owner 16 Nov, floor 1: "เดินวัดต่อจนไม่ให้จอดเยื้องหรือเอียง"). The third
#: dock on the moved dock: the board's MoveTo ended 9 cm aside of the line, the reverse (0.45 m,
#: a lean of 2° at most, in pulses) took none of it out, and she charged 9.5 cm aside and 1.7°
#: crooked -- the lidar on the map agreed. The two docks before came in within 2 cm of the line
#: and sat within 1.1 cm. A step = turn square to the line, straight ahead by the offset (the
#: lidar watching the strip ahead, as on the way out), and the turn back is dock()'s own.
SQUARE_TOL_M = 0.03
#: ... but not before the LONG reverse (`back_from_m`) within SQUARE_TOL_LONG_M (below): that
#: reverse leans onto the line itself (BACK_HOME_LEAN_DEG takes out 7 cm over 1.6 m), and the step's
#: two turns on the spot move her more than the step does. 20 Nov, floor 1 (owner "หุ่นจอดเอียง" fixed,
#: then the counter walk, n20j): 5.1 cm aside at 1.9 m out; the turn to step (-89 deg) moved the board's
#: pose 16.5 cm, the step 5.6 cm, the turn the other way (180 deg) 13 cm, the turn back 4 cm -- 11.9 cm
#: aside after two tries, and no reverse from there (off the charger). The four docks before that
#: evening came in within 3 cm and reversed with no step. scripts/walk_sim.py `turn_slide_m_per_rad`.
#: More than this aside is not a MoveTo's tolerance (a pose that jumped, the wrong spot): no step.
SQUARE_MAX_M = 0.30
SQUARE_SPEED = 0.06
SQUARE_TRIES = 2
#: The step's time is what its distance takes, twice over, plus this (review 16 Nov: one fixed
#: 12 s was 0.72 m of rolling on a pose that stopped advancing, and then a second try).
SQUARE_SLACK_S = 2.0
#: Closer than this to the pre-dock spot, the dock's approach point is skipped.
APPROACH_SKIP_M = 1.5
#: `dock.back_from_m` in the route file: home FACING OUT (16 Nov, floor 1, the moved dock). The
#: nook the dock stands in is not on the board's map, and facing it from nearer than ~1.5 m the
#: board's pose read 0.3-0.45 m further out along the south wall than she was (walk frames of ten
#: runs against the board's own map, `.tmp/floor1-20261116/slip_report.md`: 13 of 32 frames at
#: 1.25-1.5 m, 5 of 12 at 0.5-0.75 m; fit 0.13-0.40, Q49-54) -- the MoveTo to the pre-dock spot
#: then ended 0.8-0.9 m short twice (CANNOT_REACH_TARGET) and 9-18 cm aside twice, of five.
#: Facing out, every frame from 0.25 to 3 m lay on the map (176 frames, no slip, fit 0.56+).
#: So with `back_from_m` the board's MoveTo ends that far out on the dock's line, facing out
#: (where the pose is right), she steps onto the line there, and reverses the whole way under
#: velocity control with the board's pose watched for a jump -- `back_to`'s way. No value = the
#: pre-dock spot as it always was (floor 2, and floor 1 until its file says otherwise).
#: After the turn to face out: the board's pose settles (it jumped 0.38-0.46 m back onto the
#: truth as the turn ended, both times that night).
BACK_FROM_SETTLE_S = 2.0
BACK_FROM_MAX_M = 2.5              # `BACK_TO_MAX_M`: not further, blind
#: An approach point nearer than this to the spot she reverses from is that same place: not
#: a leg of its own (floor 1: 0.24 m apart -- a MoveTo that short ended after 11 cm, 28 ต.ค.).
APPROACH_SAME_M = 0.6
#: The reverse holds the dock's own heading over its last stretch (`back_in_command`): the lean
#: that brings her onto the line is still on when she meets the contacts otherwise. 16 Nov, five
#: docks: 0.4-2.7° off the dock's heading, every one the way the lean was. Only on the long
#: reverse: the 0.45 m one has no room to give up (the simulated one parks 1 cm further aside).
#: The pulses turned her 0.7-0.95°/s that night (three reverses, the first 1.2-2 s of each):
#: 0.30 m at the slow end's speeds is 5 s -- a lean of 3° is out before the contacts.
BACK_STRAIGHT_M = 0.30
#: The long reverse may lean this far to come onto the line (the 0.45 m one: 2°, as ever). 16 Nov:
#: at 2° it was at its limit for the whole 1.95 m and still went 1.5 cm the wrong way -- the
#: base rolls 2.3° off the heading read (below), more than the lean could give. Half the
#: heading guard's 5° (3.5° left 1.5°: a heading that reads 1° noisy tripped the guard on 2 of
#: 30 simulated reverses -- review 16 Nov), and it is gone over the last BACK_STRAIGHT_M.
BACK_HOME_LEAN_DEG = 2.5
#: Before the long reverse starts (review 16 Nov: "the 1.9 m blind reverse has no 'on the line'
#: precondition"). `_square_up`'s "backing in from here" exits were written for 0.45 m, where
#: charging aside beat a night off the charger; from 1.9 m a start 0.3-0.6 m aside meets the
#: nook that far aside, blind. So, read after the last turn has settled (BACK_HOME_SETTLE_S: the
#: board's pose snapped 0.38-0.46 m as a turn ended, that night): no further aside than the lean
#: takes out before the square end -- 2.5° over 1.6 m is 7 cm, and 6 cm leaves the 2-4 cm the
#: board's pose blipped by during that night's reverses under BACK_HOME_ASIDE_STOP_M -- the
#: heading within BACK_HOME_START_YAW_DEG (turned once more first when it is over 1.5°: the turns
#: of that night ended within 1°), and the distance out within BACK_HOME_OUT_BAND_M of the file's
#: (nearer is the nook's slip zone, further is more than was asked for). Else a Stop, said.
BACK_HOME_SETTLE_S = 1.0
BACK_HOME_START_ASIDE_M = 0.06
BACK_HOME_START_YAW_DEG = 4.0      # under the heading guard's 5°; the pulses take it out in the first 0.3 m
BACK_HOME_OUT_BAND_M = 0.35
#: ... and while it reverses: this far aside of the line is not a reverse that is converging
#: (a line turned the wrong way, a pose that walked) -- stopped, not tried again. Under the
#: contacts' own reach (she charged 9.5 cm aside), so what gets to the dock is on it.
BACK_HOME_ASIDE_STOP_M = 0.10
#: SQUARE_TOL_M's bar before the long reverse (see there). 1 cm under BACK_HOME_START_ASIDE_M: the
#: settled pose the reverse is judged on reads a few mm off the one this is judged on, and at the bar
#: itself 3 of 80 simulated ways home stopped there (walk_sim dock_trials, 80 each: floor1.json 80/80
#: at 0.03-0.05, 77 at 0.06; turns sliding 0.05 / 0.10 m/rad 53 / 37 docked at 0.03, 66 / 58 at 0.05;
#: a heading read 1 deg noisy 79 -> 74 -- the price, a longer lean on a noisy heading).
SQUARE_TOL_LONG_M = 0.05
#: ... and past SQUARE_TOL_LONG_M she first comes to the spot again: AGAIN_OUT_M further out along the dock's
#: line and the board's own leg back to the spot (`_come_again`), once a way home; only what is still past the
#: bar after that is stepped onto the line (`_square_up`), and then the start gate (`_ready_to_reverse`).
#: 20 Nov, floor 1, the new map: the step's turns on the spot moved the board's pose 4-16.5 cm a turn (n20j:
#: -89 deg 16.5 cm, 180 deg 13, -90 deg 4) and n20j and n20k ended 7-12 cm aside, not reversing, off the
#: charger. n20k's leg had started 0.14 m from the spot and ended 5.5 cm aside; n20j's came from the reception
#: and ended 5.1 cm aside after the board's own last turn (scratchpad n20j/n20j_guard.jsonl, 61-69 s); the
#: other legs from >= ~1 m ended <= 3 cm aside and reversed with no step (n20l: 0.1 cm aside on the contacts).
#: The way out and back is two legs of the board's own and none of our turns. scripts/walk_sim.py DockWorld
#: (`far_leg_20nov`, `near_leg_aside_m`, `pose_slide_m_per_rad`), dock_trials, 500 ways home a row (scratchpad
#: walk_sim_1121/abc_compare.txt), the step alone (read again after its turn) -> coming again first: a near start
#: with the board's pose sliding on our turns 197 -> 432 docked, far legs as 20 Nov with it 463 -> 490, the base
#: sliding 0.10 m/rad 460 -> 495, nothing sliding 499 -> 500; 16 Nov's legs (+-8 cm) with a heading read 1 deg
#: noisy 453 -> 437 (more ways home lean onto the line instead of stepping -- SQUARE_TOL_LONG_M's price again).
#: If the come-again's own 1.0 m leg ends like a near one (not known: the near model rests on n20k alone) it is
#: 197 -> 209 only. The step read again after its turn alone barely helps (177 -> 197): the turn back slides too.
#: Coming again and then leaving it to the gate (no step) docked fewer with 16 Nov's legs (999 -> 904 of 1000),
#: so the step stays last. 0 = the step at once, as before.
AGAIN_OUT_M = 1.0
#: A come-again leg that the board ended short of its point (board_walk `leg`'s own words) is carried on
#: from; any other Stop of it (Q under 40, held through the patience, a turn with no room) is the dock's.
LEG_NOT_THERE = re.compile(r"ended ([0-9.]+) m from the point")
#: ... and when the start gate then will not reverse from where it left her (that try's come-again only). Its
#: own words carry event_walk.kind_of's "blocked" marker ("something") and none of "dock"'s ("not on", "not
#: docked", "behind") or "lost"'s ("pose"): the phone says "in the way", whatever the leg's text was (re-review
#: 21 Nov: it said "blocked" only because the quoted leg happened to say "ended").
NOT_COME_AGAIN = ("dock: something in the way of coming to the spot again (the board gave that leg up %.2f m short "
                  "of its point) -- %.2f m aside, %.2f m out, no reverse from there")
#: `back_to` and the long reverse count a stall from the first centimetre (BACK_TO_STALL_FROM_M)
#: -- but not from the first tick: a base that takes 0.8 s to answer has not made STALL_MPS over
#: its first STALL_S and read as "the wheels stopped after 0.01 m" (review 16 Nov, simulated; the
#: two reverses of that night started in time). Counted from this long after the wheels were
#: first asked to turn, and again after the watchdog's gap (vx starts from 0 there too).
BACK_STALL_GRACE_S = 2.0
#: `dock.axis_offset_deg` in the route file: the way she ROLLS, minus the heading the board
#: reads, on this floor's map (+ = anticlockwise). 16 Nov, floor 1: reversing 1.95 m with the
#: board's heading held 1.9° right of the dock's (the lean, at its limit) she did not come
#: 6 cm left as that heading says -- she went 1.5 cm RIGHT: the base travelled 2.3° left of the
#: heading read. The straight legs of that night's walks say the same (38 stretches of 0.35 m+,
#: median +2.2°; the lidar's mount, or the map). So a line drawn along the dock's HEADING is
#: 2.3° off the way she must roll to meet the dock square: every one of five docks that night
#: read 0.4-2.7° right of the dock's heading on the contacts, and the long one sat 4 cm aside.
#: With it the dock's line -- where the leg ends, what "aside" means, what the reverse follows
#: -- is turned by this much, and the heading held on it stays the dock's own (what the board
#: read with her square on the dock). No value = 0: the line is the heading, as ever.
AXIS_OFFSET_MAX_DEG = 5.0
#: Driving out of the dock: stop for anything this close ahead of the front.
OUT_FRONT_STOP_M = 0.20
#: `{"forward": m}`: straight ahead from where it stands, under our own velocity (owner 10 Nov,
#: the debut pressed on floor 2: "กดแล้วหุ่นไม่เห็นเดินไปด้านหน้าเลย") -- the debut's walk while
#: its spot is not recorded (app/event_host.py `forward_m`). Measured from its own start pose,
#: never against the map: a route of these alone passes no map gate (Q, the lidar's fit; `run`).
#: What it asks of the board is a pose that does not jump under it, and a jump stops it.
FORWARD_MAX_M = 3.0
FORWARD_SPEED = 0.15
FORWARD_SPEED_MAX = 0.25
#: Stand still for anything this close ahead of the base's front face in its strip -- a guest is
#: not walked into -- and set off again once it is this far. Closer than FORWARD_SHORT_OK_M to the
#: end, that is the end.
FORWARD_HOLD_M = 0.50
FORWARD_GO_M = 0.60
FORWARD_SHORT_OK_M = 0.20
#: `{"forward": m, "meet_m": d}`: walking UP TO a guest (owner 22 Nov: "ตอนจับมือกับคุณมิกิ ก็ให้หันหลังเดินไป
#: จับมือกับแขก"). Whoever stands in the strip ahead nearer than d to the front face is where the walk was going:
#: it ends there, done, wherever along its m metres -- no hold, no patience (the guest waits for her, not she for
#: the guest; a hold that lasts the patience is a walk that failed and a hand never given). Nobody = the whole m.
#: d is kept between FORWARD_HOLD_M (the base's own sonar hold, 0.5) and FORWARD_MEET_MAX_M, so a meet walk
#: always ends before it could be held. The stop lands a few cm inside d (the command's lag at 0.15 m/s).
FORWARD_MEET_MAX_M = 1.0
FORWARD_END_TOL_M = 0.01
#: A pose change between two reads past this, plus what the top speed covers in the time
#: between them (review 10 Nov: a 1 s HTTP hiccup at 0.15 m/s is 15 cm of real driving),
#: = the board relocalized under the line (the wrong map, a stale pose): stop where it is.
FORWARD_JUMP_M = 0.15
FORWARD_JUMP_DEG = 10.0
#: ... and since 24 Nov such a jump no longer stops a straight line (`forward`, `back_to`): it is
#: measured by the WHEELS (`LineMeter`, `_OdoGuard`; app.slamware_sdk.odo_pose). 23 Nov, floor 1:
#: the walk out of the box stopped at 0.24 of 1.0 m on "jumped 0.19 m / 1° in 0.1 s" (22:26, walk
#: frames 20261123-222610: along 0.19 -> 0.24 -> 0.48 m in 0.6 s ticks at 0.15 m/s) and the back
#: onto the dock on "jumped 0.16 m" (22:26:49: -0.19 m in 0.6 s at 0.08 m/s) -- the localization
#: flipping between two fits along the dock's line (0.83-0.90 each, 16-24 cm apart), not the base.
#: What still stops: with no odometry from the board, a jump past POSE_LOST_M (that is no flip
#: along a line); the base turned / aside of its own line by the wheels (below); a stall; the time.
#: Odometry that does not move with the base (a reply that is not the wheels) is dropped for the
#: pose once the pose has gone ODO_CHECK_M: ratio outside ODO_RATIO.
FORWARD_ASIDE_STOP_M = 0.15
ODO_TIMEOUT_S = 0.5
ODO_CHECK_M = 0.15
ODO_RATIO = (0.4, 2.5)
#: ... and odometry whose TURN disagrees with the pose's own (its jumps out) by more than this is
#: dropped the same way, at any distance. `getodopose` has not been seen on this board yet: a yaw
#: in degrees (or any other frame than the pose's radians) reads a 0.3° wobble as 17° -- "off its
#: own line" one tick out of the box. A real turn shows in both; the pose's own heading jitters ~1-2°.
ODO_TURN_DISAGREE_DEG = 3.0
#: OFF until `getodopose` has been seen answering on this board (24 Nov, the robot off when it was written:
#: the reply's shape and yaw unit are read from RoboStudio's library, not from the board). Off = every line
#: is measured by the board's pose with its relocalizations taken out (`LineMeter` / `_OdoGuard` "pose") --
#: that alone carries 22:26's 0.19 m flip (under POSE_LOST_M). On = the wheels, these checks as the guard.
USE_ODOMETRY = False
#: After the line: the board's pose this far from where the wheels put her is said (`@@ pose_disagree`),
#: not stopped for -- the way back reads it (`back_to`'s start check).
LINE_DISAGREE_M = 0.10
#: Asked to go at least FORWARD_STALL_MPS and moving under FORWARD_STALL_M a tick for this
#: long with nothing ahead: caught on something below the lidar's plane (a chair's foot).
#: 0.05, so the last 15 cm's 0.06 is watched too (review 10 Nov: the pose frozen there left
#: nothing but the time cap).
#: `{"forward": m, "right_clear_m": w}`: the pageant's arm is OUT TO THE ROBOT'S RIGHT since 10 Nov's
#: side pose (the owner's video), past the base's edge where the strip ahead never looks (review 10
#: Nov: a guest standing just right of the line was never "ahead"). With `right_clear_m` the walk also
#: stands still for returns on its right (y < 0: x forward, y left -- robot_lanes.scan_points) from
#: beside the shoulder to the hold distance ahead, up to `w` past the base's edge.
FORWARD_SIDE_FROM_X = -0.30
#: `{"back_to": [x, y, yaw_deg]}`: the robot must stand on that line -- this far aside of it at
#: most, its heading this close to it, and between 5 cm and BACK_TO_MAX_M out along it (the back-in's
#: own 40 s at 0.08 m/s) -- or it does not move: a robot pushed off the line backs into what the lidar
#: cannot see. A pose jump while it backs (FORWARD_JUMP_*) stops it, as on the way out.
BACK_TO_LINE_M = 0.05
#: ... and a stall counts from the first tick (the dock's own back-in: from 5 cm backed): it backs
#: further, blind, and something right behind it from the start was pushed until the bumper (review).
BACK_TO_STALL_FROM_M = 0.0
#: A reverse whose velocity action was taken away (B, the robot's own screen): a person stopped it.
TAKEN_AWAY = "the board's velocity action was taken away (B / the robot's screen) -- stopped"
BACK_TO_YAW_DEG = 3.0
BACK_TO_MAX_M = 2.5
FORWARD_SIDE_MIN_PTS = 3
FORWARD_STALL_S = 2.5
FORWARD_STALL_MPS = 0.05
FORWARD_STALL_M = 0.004
#: What was ASKED (speed x time) past the distance by this much = the pose is not telling the
#: truth (corrections under the jump bar slid it back -- review 10 Nov: 9-10 cm corrections on
#: these maps, 1 m could have been 1.3): stop. The lidar strip stays the guard for what is ahead.
FORWARD_ASKED_OVER_M = 0.25
#: ...or this share of the distance, if more: card 1's walk out of the box (1.30 m) keeps only ~82-85% of what is asked on
#: the board's pose on every rehearsal (25 Nov: 1.28 for 1.56 asked; the runs that passed were >= 0.84) -- a flat 0.25
#: put the trigger on that very ratio. A real slide (the review's pose keeping 60%) is still caught well short.
FORWARD_ASKED_OVER_FRAC = 0.30
#: ...but the pose this near the end when it does: the step ends as done, where it stands (25 Nov 02:18 on the robot:
#: card 1's walk out of the box, "asked for 1.56 m and the pose says 1.28 (1.28 of 1.30 m)" -- 2 cm short in the
#: last creep at 0.06 m/s, after leaving the dock's contacts -- ended the whole opening). It stops all the same:
#: only the outcome differs, and every later step goes by the board's pose anyway.
FORWARD_NEAR_DONE_M = 0.05
#: Our own line through the multimedia room (28 ต.ค.). The board plans the
#: middle of the free space and will not take a target within ~0.55 m of
#: anything (CANNOT_REACH_TARGET at 0.51 m) -- and between two targets its
#: path cut the table's corner at (-12.2, -11.1), ~0.25 m from the table-side
#: chairs, where the owner saw the robot touch a chair ("พื้นที่ขวาจะเยอะกว่า").
#: A "creep" step drives a polyline itself under velocity control, the
#: floor-2 way (drive_route lanes), with drive_route's lidar checks.
CREEP_SPEED = 0.15
#: The most a segment's own speed may ask (open stretches of a creep line).
CREEP_SPEED_MAX = 0.25
CREEP_START_TOL_M = 0.25
#: The next segment takes over this far before a vertex: robot_lanes'
#: lane_command brakes into a lane's end, which at every vertex is a stop.
CREEP_HANDOVER_M = 0.10
CREEP_MAX_OFF_M = 0.15
CREEP_HOLD_S = 20.0
#: Turn on the spot first when the line is more than this off the heading
#: (robot_lanes' 25° is for open lanes). 28 ต.ค. trace in the gap: heading
#: north while the line ran at 58°, the round chairs' legs came into the strip
#: ahead (0.28 m from the front), and the base -- which slows itself for what
#: is ahead -- made 0.009 m/s of the 0.06 asked and none of a 0.16 rad/s turn
#: for 9 s. Facing along the line first keeps the legs out of the strip.
CREEP_ALIGN_DEG = 12.0
#: A heading error up to this that has no room to be turned out on the spot
#: is steered out while rolling instead (the segment then stays steer-only).
STEER_INSTEAD_DEG = 30.0
CREEP_STALL_S = 3.0
CREEP_STALL_M = 0.03
#: The base holds itself for what is this close ahead of its front face in
#: the strip (its sonar hold): every held stretch of the 28 ต.ค. traces had
#: something 0.10-0.39 m ahead (14 stretches in 7 traces), moving freely from
#: ~0.5 m. Held like that is not "caught on chair wheels" -- tv1c and tv1d
#: stopped as caught with the round chair 0.34-0.38 m ahead -- and steering
#: does not get it out (go1: 0.13 rad/s asked, 6° in 3 s, 7 s standing).
HOLD_ZONE_M = 0.45
HELD_ASK_MPS = 0.05
#: Held with the heading this far off the line: turn on the spot onto it (the
#: line is where things are out of the strip). go1 / cyc1 at the gap's exit:
#: heading -110..-114° on a -99° line, the round chair's backrest tip in the
#: strip's edge; aligned, it is 0.41 m to the side.
HELD_TURN_MIN_DEG = 4.0
HELD_SPINS_MAX = 2
CREEP_END_TOL_M = 0.12
#: Held up by the base's own slow-down for something ahead this close to a
#: bend = take the next segment now (its heading turns away from it).
STALL_HANDOVER_M = 0.45
#: side_nudge: a side the lidar sees nearer than this to the base moves the
#: line away by the difference, at most NUDGE_MAX_M. 28 ต.ค.: the north end of
#: the gap leaves ~5 cm a side at the scan plane.
NUDGE_M = 0.08
NUDGE_MAX_M = 0.08
#: A pose change bigger than this (plus twice the distance the speed allows)
#: between two ticks of a creep line is the board's localization jumping.
POSE_JUMP_M = 0.06
#: A jump is eased into, not ignored (PoseFilter): the offset that keeps the
#: line continuous is worked off by this much per metre driven (0.2 m over
#: ~1.3 m, an approach of ~8.5°). 28-29 ต.ค. back1: the offset was kept for
#: good -- four corrections (0.10/0.21/0.17/0.11 m) on the way out of the
#: screen's alcove left the robot following a frame ~0.2 m east of the real
#: one, into the door leaf's tip, for 120 s; the lidar frame at the stop
#: matched the map at the board's pose, not at ours.
POSE_BLEND_PER_M = 0.15
#: One jump this big is not a correction: the board is lost or the wheels
#: slip (28 ต.ค.: 0.6 m north in one tick, the robot stuck on chair wheels).
POSE_LOST_M = 0.40
#: Lidar arbiter (MapFit): an offset is held, not eased off, only while the
#: scan fits the board's own map clearly better where we think the robot is
#: than where the board says it is -- the broken-door-map case of 28 ต.ค.
#: Held further than this from the board = stop, the two cannot both be right.
FIT_TOL_M = 0.06
FIT_HOLD_MARGIN = 0.08
FIT_EVERY_S = 0.5
POSE_HOLD_MAX_M = 0.30
#: The progress watchdog: less than this in CREEP_HOLD_S = held up, whatever
#: speed is being asked. back1: 83 s at 2-4 mm/s with 0.06-0.08 asked (the
#: base holds for what is within ~0.4 m ahead), caught by neither the stall
#: check (>= 0.08 asked) nor the hold timer (0 asked) -- only by CREEP_MAX_S.
#: 0.15 m: back1 made 0.05-0.07 m in its 20 s windows; the slowest stretch
#: that was not held made 0.31 m in 7 s (tv1g, segment 4).
PROGRESS_M = 0.15
#: Base half-width (0.35, robot_lanes) + 1 cm: at the scan plane the lidar
#: sees chair legs and the door frame, not the arms' height.
CREEP_HALF_WIDTH = 0.36
CREEP_SIDE_STOP_M = 0.02
TURN_SWEEP_MIN_M = 0.03            # room left round the arms' rectangle while turning in place
#: A route's `{"do": "turn", "w_spin": r}`: that turn's cruise rate (rad/s), kept within these (on the spot nothing
#: turns this base under 0.13 rad/s; DriveLimits.w_spin 0.5 is the validated cruise). Card 3 (22 Nov) turns round at
#: the counter where people stand BEHIND her -- the lidar does not see behind and the sweep check passes there
#: (second review: a guest 5-13 cm behind her back was touched in the simulator): slower, the arm's corner moves
#: at ~0.15 m/s instead of ~0.26.
TURN_W_SPIN_MIN, TURN_W_SPIN_MAX = 0.2, 0.5
#: A turn on the spot larger than this (degrees) is two of half, the same way round, from the pose at the time
#: (`_spot_turn`; app/event_tour.TURN_SPLIT_DEG splits the planned ones the same way).
TURN_SPLIT_DEG = 150.0
CREEP_MAX_S = 120.0

#: A "glide" step (owner 24 Nov, after the first whole card 1: "การเดินเข้าไปแต่ละจุดช้า เหมือนต้องวัดหรืออะไร ทำให้เนียบเลย").
#: That run's walk frames: the board's own arrival hunted at every stop -- at Khun Miki it swung 26 deg past and back, at
#: the third guest its pose jumped 0.4 m a metre from the point and it turned 280 deg the long way round on the spot,
#: 7 s, beside the guest. A glide asks the board for nothing but its pose: turn to face the point, one straight line
#: (robot_lanes.lane_command: ramps up, brakes into the point, no second look), turn to the heading. Pose jumps are
#: eased in (`PoseFilter`), not chased. OPEN floor only -- no planner: what is ahead is stopped for, not gone round.
GLIDE_SPEED, GLIDE_SPEED_MAX = 0.35, 0.50
#: Owner 24 Nov night ("เพิ่มความเร็วหุ่น"): the board's own top speed is 0.5. The ramps of a glide (m/s²) -- most of the
#: round's lines are 0.7-1.4 m and never reach the top speed: the ramps are the time. The base brakes ~0.56 (logs).
GLIDE_ACCEL, GLIDE_DECEL = 0.45, 0.45
#: Someone standing where she is going (owner 24 Nov night: "เช็คกรณีที่มีคนบังตำแหน่งนั้นจะทำยังไง"): held this near the point
#: for GLIDE_NEAR_WAIT_S = there (the person on the spot is whom the hand is for). Held further: she waits for them to step
#: aside (the walk's patience, the phone told), then the walk stops. NOT round them by the board's MoveTo: tried in the
#: simulator 24 Nov night, its way round passed 0 cm from the person under the planner margin it assumes, and the board
#: has cut a table's corner into the arm before (28 Oct) -- no way round a guest until that margin is measured.
GLIDE_NEAR_OK_M, GLIDE_NEAR_WAIT_S = 0.80, 3.0
#: The strip ahead that must be free: the arms' half-width (0.39) and 3 cm. Held from GLIDE_HOLD_M off the front face
#: (slowing from GLIDE_SLOW_M), on again at GLIDE_GO_M; held within GLIDE_SHORT_OK_M of the point = there (a guest
#: stands at the end of the walk that goes to them).
GLIDE_HALF_WIDTH = 0.42
GLIDE_HOLD_M, GLIDE_GO_M, GLIDE_SLOW_M = 0.40, 0.50, 1.10
#: -- held for GLIDE_SHORT_S, not one tick of somebody passing. A step's own `short_ok` (m) is up to GLIDE_SHORT_MAX_M.
GLIDE_SHORT_OK_M, GLIDE_SHORT_MAX_M, GLIDE_SHORT_S = 0.35, 0.60, 1.0
#: Nearer than this to a point she stands on it. Nearer than GLIDE_BEHIND_M and more than GLIDE_BEHIND_DEG off her
#: heading when the line must be laid again: there too -- never turned round and back for it beside a guest.
GLIDE_MIN_M = 0.08
GLIDE_BEHIND_M, GLIDE_BEHIND_DEG = 0.50, 60.0
#: The turns' cruise: DriveLimits' validated 0.5 rad/s -- as fast as card 3's turns may be beside people (TURN_W_SPIN_MAX:
#: nobody looks behind her). A turn that has been asked for this much more than its angle is not turning: it stops.
GLIDE_W_SPIN = 0.5
#: The slow end of a turn (0.15 rad/s, the slowest that turns this base) from this far off, not 15: ~0.6 s a turn, two a leg.
GLIDE_SPIN_SLOW_DEG = 10.0
GLIDE_OVERTURN_DEG = 60.0
GLIDE_FACE_TOL_DEG, GLIDE_ALIGN_DEG = 3.0, 6.0
#: A turn looks this far round ahead of itself for room, every tick; none = it waits (patience), then stops.
GLIDE_SWEEP_DEG = 70.0
#: A heading this far off the line is turned out on the spot; this far aside = the line laid again from where she is.
GLIDE_REALIGN_DEG, GLIDE_REJOIN_M, GLIDE_REJOINS_MAX = 25.0, 0.25, 4
#: The board's pose is lost (PoseFilter: one jump past POSE_LOST_M): she stands till it holds still this long, then
#: goes on from where it says -- this often at most.
GLIDE_STEADY_S, GLIDE_STEADY_M, GLIDE_STEADY_MAX_S, GLIDE_LOST_MAX = 0.8, 0.04, 6.0, 3
#: Further than this from the point by the board's steady pose when the line is done: once more, from there (not when
#: the lidar says the board is the one that is wrong: its jumps are held off up to GLIDE_HOLD_POSE_M, over open floor).
GLIDE_AGAIN_M = 0.35
GLIDE_HOLD_POSE_M = 0.60
#: A glide goes by the WHEELS once the step has begun (`_GlideBoard`): `getodopose` answers on this board (24 Nov 23:26, 2 ms,
#: radians), and over four legs it was within 1-6 cm and 2 deg of the lidar's fit at the stops, where the board's pose ran
#: ahead 0.3 m walking east past the guests -- 0.59 m by the board, 0.31 by the wheels AND the lidar: she stopped 0.35 m
#: short of the third guest, every round. The board's pose is the start of each step (GLIDE_ANCHOR_S standing), and the
#: check that she really moves.
GLIDE_ANCHOR_S = 0.5
#: The wheels read 0.982 of the way (0.974-0.989 over those four legs, against the lidar). Past GLIDE_FOLLOW_FROM_M of a
#: step their drift (2 deg a leg: 0.2 m over 6 m) is worked off towards the board, GLIDE_FOLLOW_PER_M of what is left a
#: metre driven -- a slide of the board's of 0.3 m over a metre moves her ~3 cm, a short hop not at all.
GLIDE_ODO_SCALE = 0.982
#: ... and 0.98 of a turn (24 Nov 23:35: after a turn of 156-171 deg the line was 3 deg out, 0.2-0.3 m aside at its end).
#: After a turn on the spot the heading is the board's again (`reyaw`): standing, it is the lidar's and right.
GLIDE_ODO_TURN = 0.98
GLIDE_FOLLOW_FROM_M, GLIDE_FOLLOW_PER_M = 1.0, 0.0
#: Across the line the BOARD is right and the wheels drift (25 Nov 00:00, two rounds checked by the lidar: by the wheels the
#: 2.2 m line to the first guest ended 13-17 cm west of its point every time, the board -- and the lidar -- saw it; along an
#: east-going line the board leads while she moves, 0.3 m, and the wheels are right). So: along the line by the wheels,
#: across it by the board, eased in with this time constant (s), a disagreement over GLIDE_ACROSS_MAX_M = a jump, not used.
GLIDE_ACROSS_TAU_S, GLIDE_ACROSS_MAX_M = 1.0, 0.35
GLIDE_REYAW_MAX_DEG = 15.0
#: At the point: the board standing this far from the wheels (and the lidar not against it) = on from the board, once.
GLIDE_SETTLE_M = 0.12
#: Across the line a board pose that moves more than this between two reads is a jump: left out, never followed.
GLIDE_ACROSS_STEP_M = 0.05
#: A lidar frame with fewer returns than this is not a look at the room: a
#: frame here has 1,200-1,600 (tests/data/robot), and drive_route met an empty
#: one on 6 ต.ค. 13:58 (its number, 200). Read as it was, an empty frame is
#: "nothing there" to every check -- no front, no sides, and 9 m of room for a
#: spot turn in the door frame (review 29 ต.ค.).
SCAN_MIN_PTS = 200


#: The robot's own Emma server (scripts/robot_server.py: 127.0.0.1 only) and
#: its .env beside the code, relative to the code directory this runs from.
SERVER = "https://127.0.0.1:8001"
ENV_PATH = ".env"


def ws_token(env_path: str | Path) -> str:
    """WS_TOKEN from a .env file -- read, never printed."""
    for line in Path(env_path).read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "WS_TOKEN":
            return value.strip().strip('"').strip("'")
    raise KeyError("WS_TOKEN")


def wrap_pi(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


class Stop(Exception):
    pass


# ---- `--wait-go S`: ready in the build, wheels on the hit ------------------------------------------------------------
# 23 Nov on the robot, all five ceremony openings left the box late on the screen's hit: turnlog `event_tour
# late_walk_out` 1.29, 1.39, 1.29, 1.37, ~1.3 s (e.g. 22:41:43 debut_start impact_in 7.0, event_walk start 22:41:50,
# late 22:41:51). The arm was rested during the music, so that lag is this script's own start on the robot (the
# runtime's loader, the imports, Board(), the gates) before `@@ started`. With --wait-go the walker can be spawned
# a few seconds early: it does all of that, says `@@ {"ready": ...}`, and waits for ONE line on stdin -- "go" =
# drive; any other word, EOF (the server that holds the pipe is gone), or S seconds = out with no wheel turned
# (`NoGo`, exit NO_GO_EXIT, `@@ {"no_go": why}`, and never a `started` line). SIGTERM is the stop it always was
# (KeyboardInterrupt in the wait). Opt-in: without the flag nothing here runs.
#: The exit code of a walk that never had its go.
NO_GO_EXIT = 4
#: While it waits, this often: something else driving the base = no walk (the start gate's question, asked again),
#: and the SDK's connection asked once (one dropped while idle is re-opened here, not at the first tick after the go).
GO_KEEP_WARM_S = 1.0


class NoGo(Exception):
    """--wait-go: no go came (another word, EOF, the time) -- out without a wheel turned."""


class GoLine:
    """One line on stdin, read in a daemon thread: `wait(s)` -> "go", another word, "eof", "error", or None (nothing
    yet). os.read on the raw descriptor, NEVER sys.stdin's buffered object: a daemon thread blocked inside
    BufferedReader.readline holds that object's lock, and the interpreter's shutdown can abort on it ("could not
    acquire lock for <_io.BufferedReader name='<stdin>'> at interpreter shutdown, possibly due to daemon threads") --
    exit 134 for a walk that arrived, "failed" on the phone. A thread, not select(): select does not take a pipe on
    Windows (the tests), and the main thread's Event.wait stays where SIGTERM can reach it."""

    def __init__(self, fd: int = 0, read: Callable | None = None):
        import os
        import threading

        self._fd, self._read = fd, read or os.read
        self._got = threading.Event()
        self._thread: threading.Thread | None = None
        self.word: str | None = None

    def _run(self) -> None:
        buf = b""
        try:
            while b"\n" not in buf:
                chunk = self._read(self._fd, 64)
                if not chunk:
                    self.word = "eof"                    # nobody holds the pipe any more: no go is coming
                    return
                buf += chunk
            self.word = buf.split(b"\n", 1)[0].decode("ascii", "replace").strip().lower() or "empty"
        except OSError:
            self.word = "error"
        finally:
            self._got.set()

    def wait(self, timeout: float) -> str | None:
        if self._thread is None:
            import threading

            self._thread = threading.Thread(target=self._run, name="go-line", daemon=True)
            self._thread.start()
        return self.word if self._got.wait(max(0.0, timeout)) else None


def stop_on_signals() -> None:
    """SIGTERM (`pkill`'s default) and SIGHUP (the adb shell this was started
    from going away -- the multimedia room is a Wi-Fi dead zone, CLAUDE.md)
    end the run the Ctrl-C way, as KeyboardInterrupt: every open velocity
    session sends its zero, main's finally cancels the board's MoveTo. Python's
    own default for both is to die on the spot -- no finally, no __exit__ --
    and the MoveTo went on to its target with nobody watching (review 29 ต.ค.).
    SIGINT too: started in the background (`nohup ... &`) it is inherited
    ignored, and CPython only puts its handler over a default (drive_route's
    review finding 1: "stop" reported sent, the robot kept driving). A SIGHUP
    that is already ignored (nohup) stays ignored -- the run was meant to
    outlive the shell. The first signal ignores the rest, so a second cannot
    cut the stop short (drive_route's except KeyboardInterrupt); a stop that
    hangs is kill -9's."""
    sigs = [signal.SIGINT, signal.SIGTERM]
    hup = getattr(signal, "SIGHUP", None)                # not on Windows
    if hup is not None and signal.getsignal(hup) is not signal.SIG_IGN:
        sigs.append(hup)

    def _stop(signum, frame):
        for s in sigs:
            signal.signal(s, signal.SIG_IGN)
        raise KeyboardInterrupt(signal.Signals(signum).name)

    for s in sigs:
        signal.signal(s, _stop)


def load_path(route_file: str | Path, name: str) -> tuple[dict, list[dict]]:
    doc = json.loads(Path(route_file).read_text(encoding="utf-8"))
    paths = doc.get("paths") or {}
    if name not in paths:
        raise SystemExit("no path %r in %s (have: %s)" % (name, route_file, ", ".join(sorted(paths))))
    return doc, list(paths[name]["steps"])


def dock_pose(doc: dict) -> tuple[float, float, float]:
    x, y, deg = doc["dock"]["pose"]
    return float(x), float(y), math.radians(float(deg))


def back_from_m(doc: dict) -> float:
    """`dock.back_from_m`: how far out on the dock's line the board's MoveTo ends and our own
    reverse begins (BACK_FROM_* above). 0.0 = not set: the pre-dock spot. A value that is not
    a distance to reverse blind (under the pre-dock spot's, over BACK_FROM_MAX_M) is not one."""
    try:
        m = float((doc.get("dock") or {}).get("back_from_m") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return m if PREDOCK_M < m <= BACK_FROM_MAX_M else 0.0


def dock_line(doc: dict) -> float:
    """The direction of the dock's line in the map: its heading, turned by `axis_offset_deg`
    (above). The heading itself when the file gives none, or one that is no small angle."""
    yaw = dock_pose(doc)[2]
    try:
        off = float((doc.get("dock") or {}).get("axis_offset_deg") or 0.0)
    except (TypeError, ValueError):
        return yaw
    return wrap_pi(yaw + math.radians(off)) if 0.0 < abs(off) <= AXIS_OFFSET_MAX_DEG else yaw


def dock_key_notes(doc: dict) -> list[str]:
    """What the run says first about the dock's two optional keys: a value the file gives and
    this code will NOT use is the old way home under a file that reads like the new one."""
    dock = doc.get("dock") if isinstance(doc.get("dock"), dict) else {}
    notes = []
    if dock.get("back_from_m") is not None:
        far = back_from_m(doc)
        notes.append("dock: home facing out, reversing from %.2f m out on its line" % far if far else
                     "dock: back_from_m %r IS NOT USED (a distance over %.2f and up to %.1f m) -- home by the "
                     "pre-dock spot, the way that gave up in the nook" % (dock.get("back_from_m"), PREDOCK_M, BACK_FROM_MAX_M))
    if dock.get("axis_offset_deg") is not None:
        turned = dock_line(doc) != dock_pose(doc)[2]
        notes.append("dock: its line is turned %+.1f° from its heading" % float(dock["axis_offset_deg"]) if turned else
                     "dock: axis_offset_deg %r IS NOT USED (an angle within %.0f°, not 0) -- the line is the heading"
                     % (dock.get("axis_offset_deg"), AXIS_OFFSET_MAX_DEG))
    return notes


def pre_dock(doc: dict) -> tuple[float, float, float]:
    """On the dock's line, PREDOCK_M out, facing out (the way it backs in)."""
    x, y, yaw = dock_pose(doc)
    return x + PREDOCK_M * math.cos(yaw), y + PREDOCK_M * math.sin(yaw), yaw


def wheels_stopped(recent: list[tuple[float, float, float]], now: float) -> bool:
    """The reverse's wheels have stopped, by the board's pose (t, x, y) over the last seconds (STALL_* above)."""
    def over(secs: float) -> tuple[float, float, float] | None:
        ref = [k for k, q in enumerate(recent) if now - q[0] >= secs]
        if not ref:
            return None
        sx = sy = path = 0.0
        for a, b in zip(recent[ref[-1]:], recent[ref[-1] + 1:]):
            dx, dy = b[1] - a[1], b[2] - a[2]
            if math.hypot(dx, dy) < STALL_JUMP_M:
                sx, sy, path = sx + dx, sy + dy, path + math.hypot(dx, dy)
        return math.hypot(sx, sy), path, now - recent[ref[-1]][0]

    near = over(STALL_S)
    if near is not None and near[0] / near[2] < STALL_MPS and near[1] < STALL_PATH_M:
        return True
    far = over(STALL_NET_S)
    return far is not None and far[0] < STALL_NET_M


def back_in_command(out: float, lat: float, herr: float, straight_m: float = 0.0,
                    lean_deg: float = 2.0) -> tuple[float, float]:
    """(vx, heading wanted minus heading now) for one tick of reversing onto
    the dock. ``out`` = still to go along the dock's line, ``lat`` = + left of
    the line facing out, ``herr`` = heading minus the dock's. Backing, the
    tail leads: to come back onto the line from the left the nose turns LEFT
    (drive_route, 7 ต.ค.). The turn itself goes out as short pulses
    (`BACK_PULSE_W`): a steady small rate does nothing on this base."""
    lean = max(-math.radians(lean_deg), min(math.radians(lean_deg), 3.0 * lat))
    if straight_m > 0.0 and out < straight_m:
        lean = 0.0                                       # the last stretch: square to the dock
    want = wrap_pi(lean - herr)
    if out > BACK_FAST_FROM_M:
        vx = -BACK_FAST_SPEED
    elif out > BACK_SLOW_M:
        vx = -BACK_SPEED
    else:
        vx = -BACK_SPEED * 0.6
    return vx, want


def rolls_through(step: dict, nxt: dict | None) -> bool:
    """A plain point followed by another point is handed over before it is
    reached (Walker.leg). Not when it sets a heading (that is a place to face
    something), nor before a creep line (which starts from where the robot
    stands) or any other kind of step."""
    plain = "to" in step and step.get("face") is None and not step.get("fallback")
    if plain and nxt is not None and "glide" in nxt:
        # 25 Nov (card 3, the globe): the board's MoveTo hunted 6 s at its point -- handed over to our own glide before
        # it, its action cancelled there (velocity control does not take over a running MoveTo)
        return "cancel"
    return plain and nxt is not None and "to" in nxt


def right_side_hit(points, clear_m: float, ahead_m: float) -> bool:
    """FORWARD_SIDE_MIN_PTS lidar returns in the strip on the robot's right between the base's edge
    (CREEP_HALF_WIDTH) and `clear_m` past it, from FORWARD_SIDE_FROM_X to `ahead_m` (robot frame)."""
    import numpy as np

    p = np.asarray(points, dtype=float).reshape(-1, 2)
    if clear_m <= 0.0 or not len(p):
        return False
    sel = ((p[:, 1] < -CREEP_HALF_WIDTH) & (p[:, 1] >= -(CREEP_HALF_WIDTH + clear_m))
           & (p[:, 0] >= FORWARD_SIDE_FROM_X) & (p[:, 0] <= ahead_m))
    return int(sel.sum()) >= FORWARD_SIDE_MIN_PTS


def _off_map(step: dict) -> bool:
    """A step that never goes by the board's map: a `forward` (from its own start pose) and a
    `back_to` (back along that same line, 11 Nov) -- velocity along a line in the pose's own
    frame, wherever that frame sits on whatever map. NOT a MoveTo, an undock or a dock."""
    return "forward" in step or "back_to" in step


def needs_map(steps: list[dict]) -> bool:
    """Does any step go by the board's map? A `forward`, a `back_to` and a `wait` do not: a path
    of those alone is not stopped at the map gates (Q, the lidar's fit), which are about MoveTo
    to a point on a map -- 10 Nov on floor 2, the board on floor 1's map at Q48 and a fit nobody
    could pass, a metre ahead was what was asked (and 11 Nov, the way back onto the dock).
    No such step at all = the gates as ever."""
    if not any(_off_map(s) for s in steps):
        return True
    # a `fallback` is other steps (MoveTo, review 10 Nov): the gates as ever
    return any(s.get("fallback") or not (_off_map(s) or s.get("do") == "wait") for s in steps)


def side_nudge(left: float | None, right: float | None) -> float:
    """How far (m, + = left) to move a creep line this tick, away from a side
    the lidar sees within NUDGE_M of the base -- and only then. Both sides
    close = the difference (pushed both ways, the nearer wins)."""
    shift = 0.0
    if left is not None and left < NUDGE_M:
        shift -= NUDGE_M - left
    if right is not None and right < NUDGE_M:
        shift += NUDGE_M - right
    return max(-NUDGE_MAX_M, min(NUDGE_MAX_M, shift))


def join_line(pts: list[tuple[float, float]], at: tuple[float, float]) -> tuple[int, float]:
    """The segment of a creep line to start on from `at`, and how far `at` is
    from it: the nearest one, the later one when two are as near (at a vertex,
    the segment ahead). A run stopped part way picks up where it is."""
    best, best_d = 0, float("inf")
    for k, (a, b) in enumerate(zip(pts, pts[1:])):
        dx, dy = b[0] - a[0], b[1] - a[1]
        t = max(0.0, min(1.0, ((at[0] - a[0]) * dx + (at[1] - a[1]) * dy) / max(dx * dx + dy * dy, 1e-9)))
        d = math.hypot(at[0] - (a[0] + t * dx), at[1] - (a[1] + t * dy))
        if d <= best_d + 0.02:
            best, best_d = k, min(d, best_d)
    return best, best_d


#: drive_route's doorway pulse (robot_lanes.DOOR_PULSE_W / _WAIT_S): one tick
#: at 0.08 rad/s, at most every 0.5 s, when the heading is off by > 0.7°.
BACK_PULSE_W = 0.08
BACK_PULSE_WAIT_S = 0.5
BACK_PULSE_MIN_DEG = 0.7


class MapFit:
    """How well a lidar frame lies on the board's own map at a given pose:
    the fraction of returns within ~FIT_TOL_M of an occupied cell. The map is
    the board's explore grid (`GET /api/core/slam/v1/maps/explore`, header
    <ffIIf origin x/y, width, height, resolution; u32 size at 32; int8 cells
    from 36, < -10 = occupied). numpy only -- the robot has no scipy.

    Used to arbitrate a pose jump (PoseFilter): measured on the 28 ต.ค. stops,
    10-13 cm off moved the fit 0.83 -> 0.56 at the screen, so two poses 0.1 m
    apart are told apart; a constant bias of the map moves both alike."""

    def __init__(self, blob: bytes, tol_m: float = FIT_TOL_M):
        import numpy as np

        ox, oy, w, h, res = struct.unpack_from("<ffIIf", blob, 0)
        (size,) = struct.unpack_from("<I", blob, 32)
        if size != w * h or len(blob) < 36 + size or not res > 0:
            raise ValueError("explore map: size %d for %d x %d, %d bytes" % (size, w, h, len(blob)))
        occ = np.frombuffer(blob[36:36 + size], dtype=np.int8).reshape(h, w) < -10
        r = max(1, int(round(tol_m / res)))
        near = occ.copy()
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if dx * dx + dy * dy > r * r or (dx == 0 and dy == 0):
                    continue
                # near[y, x] |= occ[y + dy, x + dx], without wrapping round the edges
                ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
                xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
                near[ys, xs] |= occ[yd, xd]
        self.ox, self.oy, self.w, self.h, self.res = float(ox), float(oy), int(w), int(h), float(res)
        self.near = near

    def score(self, pose: tuple[float, float, float], points, max_pts: int = 400) -> float:
        import numpy as np

        p = np.asarray(points, dtype=float).reshape(-1, 2)
        if not len(p):
            return 0.0
        p = p[:: max(1, len(p) // max_pts)]
        x, y, yaw = pose
        c, s = math.cos(yaw), math.sin(yaw)
        col = np.floor((x + p[:, 0] * c - p[:, 1] * s - self.ox) / self.res).astype(int)
        row = np.floor((y + p[:, 0] * s + p[:, 1] * c - self.oy) / self.res).astype(int)
        inside = (col >= 0) & (col < self.w) & (row >= 0) & (row < self.h)
        hits = self.near[row[inside], col[inside]].sum()
        return float(hits) / len(p)


class PoseFilter:
    """The pose a creep line steers by: the board's, with its jumps eased in.

    History. The doorway, 28 ต.ค.: the board's pose jumped 0.2 m sideways in
    half a second (its saved map of the door was broken by a --map run), the
    line seemed 21° away and the turn back was refused at the jamb -- so a
    jump the base could not have made was not followed, an offset kept the
    line going on from where the robot was. With the map restored the jumps
    are the board CORRECTING itself: at the screen it reads 9-10 cm and 3-4°
    off (lidar fits of three stops), and corrects as the robot leaves the
    alcove. Kept for good, the offset carried that error -- back1, four jumps:
    ~0.2 m east of the line, into the door leaf, 120 s.

    Now a jump is eased in (POSE_BLEND_PER_M of the offset worked off per
    metre driven -- the robot comes back to the line at a few degrees, no
    spin in the gap), unless the lidar says the board jumped the wrong way
    (MapFit: the scan fits clearly better where we think the robot is) --
    then it is held, and stopped for beyond POSE_HOLD_MAX_M. One jump beyond
    POSE_LOST_M is a stop: no correction is that big."""

    def __init__(self, fit: MapFit | None = None, hold_max: float | None = None):
        self.fit = fit
        self.hold_max = POSE_HOLD_MAX_M if hold_max is None else hold_max
        self.corr = (0.0, 0.0)
        #: the jumps alone, never eased off: raw + this is continuous -- how far the
        #: robot has really moved (the stall and progress checks), not where to steer
        self.jumped = (0.0, 0.0)
        self.raw_prev: tuple[float, float] | None = None
        self.jumps = 0
        self.holding = False
        self.checked_at: float | None = None

    def update(self, raw: tuple[float, float, float], now: float, vx: float, dt: float,
               points=None) -> tuple[tuple[float, float, float], list[str], str | None]:
        """(pose to steer by, things to say, why to stop or None). `vx` is the
        speed asked on the last tick, `dt` the time since."""
        x, y, yaw = raw
        msgs: list[str] = []
        allowed = abs(vx) * dt
        travelled = 0.0
        if self.raw_prev is not None:
            moved = math.hypot(x - self.raw_prev[0], y - self.raw_prev[1])
            if moved > POSE_LOST_M:
                return (x + self.corr[0], y + self.corr[1], yaw), msgs, (
                    "the board's pose jumped %.2f m in one tick -- not a correction (lost, or the wheels slipping?)"
                    % moved)
            if moved > POSE_JUMP_M + 2 * allowed:
                d = (self.raw_prev[0] - x, self.raw_prev[1] - y)
                self.corr = (self.corr[0] + d[0], self.corr[1] + d[1])
                self.jumped = (self.jumped[0] + d[0], self.jumped[1] + d[1])
                self.jumps += 1
                self.checked_at = None                    # look at the lidar now
                msgs.append("the board's pose jumped %.2f m -- easing onto it as the robot drives" % moved)
            else:
                # eased per metre really driven -- not while held (asked, not moving)
                travelled = min(moved, 1.5 * allowed + 0.002)
        self.raw_prev = (x, y)
        c = math.hypot(*self.corr)
        if c > 1e-4:
            if self.fit is not None and points is not None and (
                    self.checked_at is None or now - self.checked_at >= FIT_EVERY_S):
                self.checked_at = now
                at_board = self.fit.score((x, y, yaw), points)
                at_ours = self.fit.score((x + self.corr[0], y + self.corr[1], yaw), points)
                hold = at_ours > at_board + FIT_HOLD_MARGIN
                if hold != self.holding:
                    msgs.append(("the lidar fits the map where the robot was (%.2f), not where the board jumped "
                                 "(%.2f) -- keeping to it" if hold else
                                 "the lidar no longer contradicts the board (%.2f vs %.2f) -- easing onto it")
                                % ((at_ours, at_board) if hold else (at_board, at_ours)))
                self.holding = hold
            if self.holding:
                if c > self.hold_max:
                    return (x + self.corr[0], y + self.corr[1], yaw), msgs, (
                        "the board's pose is %.2f m from where the lidar puts the robot" % c)
            else:
                k = max(0.0, c - POSE_BLEND_PER_M * travelled) / c
                self.corr = (self.corr[0] * k, self.corr[1] * k)
        return (x + self.corr[0], y + self.corr[1], yaw), msgs, None

    def odometry(self, raw: tuple[float, float, float]) -> tuple[float, float]:
        """The board's pose with the jumps taken out (continuous): what the
        stall and progress checks measure motion by. The steered pose moves
        while an offset is eased off, also with the robot standing still."""
        return raw[0] + self.jumped[0], raw[1] + self.jumped[1]


class CreepAction:
    """What one tick of a creep line asks for: "drive" (vx, w), "spin" (turn
    on the spot to `heading`, after checking the arms have room, then tick
    again), "again" (tick again now: a new segment), "done", "stop" (reason).
    (A plain class: the tests load this script by path, where a dataclass
    cannot find its own module.)"""

    def __init__(self, kind: str, vx: float = 0.0, w: float = 0.0, heading: float = 0.0, reason: str = ""):
        self.kind, self.vx, self.w, self.heading, self.reason = kind, vx, w, heading, reason

    def __repr__(self) -> str:
        return "CreepAction(%s, vx=%.3f, w=%.3f, heading=%.3f, %r)" % (self.kind, self.vx, self.w, self.heading,
                                                                      self.reason)


class CreepLine:
    """One creep step, tick by tick, with no I/O of its own -- Walker.creep
    reads the board and the lidar and sends what this returns; the tests and
    the replay of the recorded runs drive it with a simulated base. Owner,
    28 ต.ค.: "ทำเช็คทดสอบรีวิวให้ละเอียด" -- what is not driven offline is not
    tested before it meets the chairs."""

    def __init__(self, step: dict, step_no: int, raw_pose: tuple[float, float, float], now: float,
                 fit: MapFit | None = None):
        from app import robot_lanes as rl

        self.rl = rl
        self.step_no, self.label = step_no, step.get("label") or ""
        # the step's own speed is capped like a point's: the route file check lets a
        # step ask up to 0.5 (the board's legs), a doorway line must not take it
        vmax = min(float(step.get("speed") or CREEP_SPEED), CREEP_SPEED_MAX)
        pts = [(float(p[0]), float(p[1])) for p in step["creep"]]
        # [x, y, speed]: the segment starting there goes at that speed (28 ต.ค.: the open
        # middle of the room and the reception side of the door ran at the doorway's
        # 0.15 throughout -- owner: "เร็ว แม่นยำมากขึ้น")
        speeds = [min(float(p[2]), CREEP_SPEED_MAX) if len(p) > 2 else vmax for p in step["creep"]]
        first, off = join_line(pts, (raw_pose[0], raw_pose[1]))
        if off > CREEP_START_TOL_M:
            raise Stop("step %d: %.2f m from its line" % (step_no, off))
        self.messages: list[str] = []
        if first:
            self.messages.append("step %d: joining the line at segment %d (%.2f m from it)" % (step_no, first + 1, off))
        self.n_points = len(pts)
        self.segs = list(zip(pts, pts[1:]))[first:]
        self.seg_speeds = speeds[first:]
        self.pose = PoseFilter(fit)
        self.t0 = now
        self.prev = (0.0, 0.0)
        self.moving: list[tuple[float, float, float, float]] = []
        self.track: list[tuple[float, float, float]] = []
        self.worst_side, self.worst_lat, self.slowest_front = 9.0, 0.0, None
        self.last_row: dict | None = None
        self.k = -1
        self._next_segment(now)

    def _next_segment(self, now: float) -> None:
        rl = self.rl
        self.k += 1
        a, b = self.segs[self.k]
        self.hdg = math.atan2(b[1] - a[1], b[0] - a[0])
        # front_v with v_max (as DOOR_FAST_LIMITS has it): DOOR_LIMITS' 0.15 capped every
        # faster segment whenever anything was in the strip within 1.5 m -- 2.7 of go1's
        # 12.4 s and 9.8 of res1's 37.7 s on its 0.2-0.25 segments went at 0.15 for
        # something 0.5-1.2 m ahead. The taper below 0.5 m and the stop at 0.1 m stay.
        v = self.seg_speeds[self.k]
        self.limits = replace(rl.DOOR_LIMITS, v_max=v, front_v=max(v, rl.DOOR_LIMITS.front_v),
                              align_deg=CREEP_ALIGN_DEG)
        self.wide = replace(self.limits, align_deg=STEER_INSTEAD_DEG + 5)     # steer, don't spin (see tick)
        self.turn_checked, self.steer_only, self.gap = False, False, 9.0
        self.held_spins = 0
        self.tick_prev = now

    def pop_messages(self) -> list[str]:
        out, self.messages = self.messages, []
        return out

    def after_spin(self, now: float) -> None:
        """A spot turn (CreepAction "spin") took its seconds outside the ticks:
        the next tick ramps from standstill, its dt is not the turn's."""
        self.tick_prev, self.moving, self.prev = now, [], (0.0, 0.0)

    def sent(self, act: CreepAction, renewed: bool) -> None:
        """The command went out (`renewed`: the velocity action had to be taken
        up again and a zero went instead -- ramp from standstill)."""
        self.prev = (0.0, 0.0) if renewed else (act.vx, act.w)
        if self.last_row is not None:
            self.last_row["renewed"] = renewed

    def tick(self, now: float, raw: tuple[float, float, float], pts,
             board_event: Callable[[], bool] = lambda: False) -> CreepAction:
        rl = self.rl
        i, k = self.step_no, self.k
        last = k == len(self.segs) - 1
        a, b = self.segs[k]
        hdg = self.hdg
        dt = max(0.05, min(0.3, now - self.tick_prev))
        self.tick_prev = now
        (x, y, yaw), msgs, lost = self.pose.update(raw, now, self.prev[0], dt, pts)
        self.messages += ["step %d: %s" % (i, m) for m in msgs]
        if lost:
            return CreepAction("stop", reason="step %d %s, segment %d: %s at (%.2f, %.2f)" % (
                i, self.label, k + 1, lost, raw[0], raw[1]))
        front = rl.front_distance(pts, half_width=CREEP_HALF_WIDTH, margin=0.0, max_range=1.5)
        left, right = rl.side_clearance(pts, half_width=CREEP_HALF_WIDTH, shaped=True)
        # step away from a side the lidar sees closing in -- the line stays where it
        # was chosen otherwise (the owner's "ชิดซ้าย" is about wheels the lidar
        # cannot see; centring between legs would undo it)
        shift = side_nudge(left, right)
        sa = (a[0] - math.sin(hdg) * shift, a[1] + math.cos(hdg) * shift)
        sb = (b[0] - math.sin(hdg) * shift, b[1] + math.cos(hdg) * shift)
        cmd = rl.lane_command((x, y, yaw), sa, sb, self.prev, dt, front, self.limits)
        # the next segment takes over before this one brakes into its end
        if cmd.done or (not last and cmd.rem < CREEP_HANDOVER_M):
            if last:
                return CreepAction("done")
            self._next_segment(now)
            return CreepAction("again")
        sides = [s_ for s_ in (left, right) if s_ is not None]
        if sides:
            self.worst_side = min(self.worst_side, min(sides))
        self.worst_lat = max(self.worst_lat, abs(cmd.lat))
        if front is not None:
            front_gap = front - rl.ROBOT_BASE_LENGTH / 2
            self.slowest_front = front_gap if self.slowest_front is None else min(self.slowest_front, front_gap)
        reason = None
        if sides and min(sides) < CREEP_SIDE_STOP_M:
            reason = "something %.2f m from the %s side" % (
                min(sides), "left" if left is not None and left == min(sides) else "right")
        elif abs(cmd.lat) > CREEP_MAX_OFF_M:
            reason = "%.2f m off the line" % cmd.lat
        elif board_event():
            reason = "board event (bumper?)"
        elif now - self.t0 > CREEP_MAX_S:
            reason = "%.0f s on the line" % CREEP_MAX_S
        if cmd.phase == "align" and (self.steer_only or not self.turn_checked):
            if not self.steer_only:
                self.gap = rl.turn_sweep_gap(pts, cmd.err)
                self.turn_checked = True
            if self.steer_only or self.gap < TURN_SWEEP_MIN_M:
                if abs(cmd.err) <= math.radians(STEER_INSTEAD_DEG) and not reason:
                    # 28 ต.ค. in the doorway: 8° off and no room to turn on the
                    # spot -- steer it out while rolling, as an open lane would
                    self.steer_only = True
                    cmd = rl.lane_command((x, y, yaw), sa, sb, self.prev, dt, front, self.wide)
                    if cmd.phase == "align":       # beyond what steering takes, and no room to spin
                        reason = ("%.0f° off segment %d in a place too tight to turn on the spot"
                                  % (math.degrees(cmd.err), k + 1))
                else:
                    reason = ("no room to turn %.0f° onto segment %d: the arms would pass %.2f m "
                              "from something" % (math.degrees(cmd.err), k + 1, self.gap))
        # The progress watchdog (back1, 28-29 ต.ค.): whatever is asked, a robot that has
        # not got PROGRESS_M anywhere in CREEP_HOLD_S is held up -- by a person, a chair,
        # the door leaf, the base's own hold for what is ahead. It used to take 0.00 m/s
        # asked to start the clock; 0.06 asked and 0.005 m/s made went on for 120 s.
        ox_, oy_ = self.pose.odometry(raw)
        self.track = [p for p in self.track if now - p[0] <= CREEP_HOLD_S] + [(now, ox_, oy_)]
        if not reason and now - self.track[0][0] >= CREEP_HOLD_S * 0.95:
            got = max(math.hypot(ox_ - p[1], oy_ - p[2]) for p in self.track)
            if got < PROGRESS_M:
                reason = "held up: %.2f m in %.0f s%s" % (
                    got, CREEP_HOLD_S,
                    "" if front is None else ", something %.2f m ahead" % (front - rl.ROBOT_BASE_LENGTH / 2))
        # Driving but not going anywhere, two kinds. Held: something in the strip
        # within HOLD_ZONE_M of the front face, the base holding itself for it.
        # Stalled: nothing there, 0.08+ asked -- 28 ต.ค. the robot sat 23 s at
        # (-12.21, -10.70) with 0.15 m/s asked and nothing ahead, the owner:
        # caught on a chair's wheels (below the lidar). Never push on that.
        self.moving.append((now, ox_, oy_, cmd.vx))
        self.moving = [m for m in self.moving if now - m[0] <= CREEP_STALL_S]
        moving = self.moving
        still = (now - moving[0][0] >= CREEP_STALL_S * 0.9
                 and math.hypot(ox_ - moving[0][1], oy_ - moving[0][2]) < CREEP_STALL_M)
        asked = min(m[3] for m in moving)
        ahead = None if front is None else front - rl.ROBOT_BASE_LENGTH / 2
        held = still and asked >= HELD_ASK_MPS and ahead is not None and ahead < HOLD_ZONE_M
        stalled = still and asked >= 0.08 and not held
        if (held or stalled) and last and cmd.rem < CREEP_END_TOL_M and not reason:
            # 28 ต.ค. at the multimedia door: a wall 0.7 m ahead and the
            # base crept at ~1 cm/s for the last 9 cm -- that is there
            self.messages.append("step %d: %.2f m short of the end, the base will not creep closer -- stopping "
                                 "here" % (i, cmd.rem))
            return CreepAction("done")
        if held and not reason:
            # Near a bend (28 ต.ค. at the gap: the round chair in the strip 0.2-0.36 m
            # before the line bends away from it): turn onto the next segment and take
            # the bend now. Elsewhere, crooked on the line (go1/cyc1: -110..-114° on a
            # -99° line): turn onto the line. Only with room for the arms' turn
            # (robot_lanes.turn_sweep_gap); else wait -- the watchdog decides.
            bend = not last and cmd.rem < STALL_HANDOVER_M
            if bend:
                a2, b2 = self.segs[k + 1]
                target = math.atan2(b2[1] - a2[1], b2[0] - a2[0])
            else:
                target = hdg
            turn = wrap_pi(target - yaw)
            if abs(turn) > math.radians(HELD_TURN_MIN_DEG if not bend else 3.0) and \
                    self.held_spins < HELD_SPINS_MAX and rl.turn_sweep_gap(pts, turn) >= TURN_SWEEP_MIN_M:
                self.messages.append("step %d: held up by something %.2f m ahead%s -- turning %.0f° onto the %s" % (
                    i, ahead, ", %.2f m before the bend" % cmd.rem if bend else "", math.degrees(turn),
                    "next segment" if bend else "line"))
                self.moving, self.prev = [], (0.0, 0.0)
                if bend:
                    self._next_segment(now)
                self.held_spins += 1
                return CreepAction("spin", heading=target)
            if bend and abs(turn) <= math.radians(3.0):
                self.moving = []
                self._next_segment(now)
                return CreepAction("again")
        if stalled and not reason:
            reason = ("not moving: %.2f m in %.1f s with %.2f m/s asked -- caught on something "
                      "below the lidar (a chair's wheels?)" % (
                          math.hypot(ox_ - moving[0][1], oy_ - moving[0][2]), now - moving[0][0], asked))
        if reason:
            off = math.hypot(*self.pose.corr)
            return CreepAction("stop", reason="step %d %s, segment %d: %s at (%.2f, %.2f)%s" % (
                i, self.label, k + 1, reason, x, y,
                "" if off < 0.02 else " (the board says (%.2f, %.2f))" % (raw[0], raw[1])))
        self.last_row = {
            "t": round(now - self.t0, 2), "seg": k + 1, "pose": [round(x, 3), round(y, 3), round(yaw, 3)],
            "raw": [round(raw[0], 3), round(raw[1], 3)], "corr": [round(c_, 3) for c_ in self.pose.corr],
            "phase": cmd.phase, "vx": round(cmd.vx, 3), "w": round(cmd.omega, 3),
            "rem": round(cmd.rem, 3), "lat": round(cmd.lat, 3), "err": round(math.degrees(cmd.err), 1),
            "front": None if front is None else round(front, 2), "why": cmd.why,
            "sides": [None if s_ is None else round(s_, 3) for s_ in (left, right)],
            "shift": round(shift, 3), "steer_only": self.steer_only}
        return CreepAction("drive", vx=cmd.vx, w=cmd.omega)


class Board:
    def __init__(self, base: str = BOARD):
        import httpx

        self.c = httpx.Client(base_url=base, timeout=15)

    def get(self, path: str):
        r = self.c.get(path)
        r.raise_for_status()
        return r.json() if r.content else None

    def pose(self) -> tuple[float, float, float]:
        p = self.get("/api/core/slam/v1/localization/pose")
        return float(p["x"]), float(p["y"]), float(p["yaw"])

    def quality(self) -> int:
        return int(self.get("/api/core/slam/v1/localization/quality"))

    def power(self) -> dict:
        return self.get("/api/core/system/v1/power/status") or {}

    def current(self):
        r = self.c.get("/api/core/motion/v1/actions/:current")
        return r.json() if r.status_code == 200 and r.content else None

    def cancel(self) -> None:
        self.c.delete("/api/core/motion/v1/actions/:current")

    def events(self) -> list:
        try:
            return self.get("/api/platform/v1/events") or []
        except Exception:                                # noqa: BLE001
            return []

    def mapping(self, on: bool) -> None:
        self.c.put("/api/core/slam/v1/mapping/:enable", json={"enable": bool(on)}).raise_for_status()

    def speed(self) -> str | None:
        r = self.c.get("/api/core/system/v1/parameter", params={"param": "base.max_moving_speed"})
        return str(r.json()).strip('"') if r.status_code == 200 else None

    def set_speed(self, v) -> None:
        self.c.put("/api/core/system/v1/parameter", json={"param": "base.max_moving_speed", "value": str(v)})

    def move_to(self, x: float, y: float, yaw: float | None) -> int:
        r = self.c.post("/api/core/motion/v1/actions", json={
            "action_name": MOVE_TO,
            "options": {"target": {"x": x, "y": y, "z": 0},
                        "move_options": {"mode": 0, "flags": ["with_yaw"] if yaw is not None else [],
                                         "yaw": 0.0 if yaw is None else yaw,
                                         "acceptable_precision": 0, "fail_retry_count": 0}}})
        if r.status_code != 200:
            raise Stop("MoveTo refused: %s %s" % (r.status_code, r.text[:200]))
        return int(r.json().get("action_id") or 0)

    def action_reason(self, aid: int) -> str:
        """The board's own word on how an action ended (`:current` loses it)."""
        try:
            st = (self.get("/api/core/motion/v1/actions/%d" % aid) or {}).get("state") or {}
            return st.get("reason") or "result %s" % st.get("result")
        except Exception:                                # noqa: BLE001
            return "no reason given"

    def explore_map(self) -> bytes:
        """The board's occupancy grid, raw (MapFit parses it)."""
        r = self.c.get("/api/core/slam/v1/maps/explore")
        r.raise_for_status()
        return r.content

    def homedocks(self) -> list:
        return self.get("/api/core/slam/v1/homedocks") or []

    def register_dock(self) -> None:
        self.c.post("/api/core/slam/v1/homedocks/:register",
                    json={"metadata": {"display_name": "home_dock"}}).raise_for_status()


def _taken(v) -> bool:
    """`v.set` took the action up again because a PERSON took it (B / the robot's own screen:
    operation_fail), not for a gap past the watchdog (review 11 Nov: a 1.1 s pose read ended a
    floor-1 dock as "a person stopped it", and its second try never came). A session that does not
    say (a test's) counts as taken: the safe side."""
    return getattr(v, "last_renew", "taken") != "gap"


class _Jumped(Stop):
    """A pose jump under a line (forward, back_to): the line's two ends are in two frames now."""


def _rel(o0: tuple[float, float, float], o: tuple[float, float, float]) -> tuple[float, float, float]:
    """`o` from `o0`, in `o0`'s heading: (ahead, left, turned)."""
    dx, dy = o[0] - o0[0], o[1] - o0[1]
    c, s = math.cos(o0[2]), math.sin(o0[2])
    return dx * c + dy * s, -dx * s + dy * c, wrap_pi(o[2] - o0[2])


def _compose(a: tuple[float, float, float], r: tuple[float, float, float]) -> tuple[float, float, float]:
    """`r` (ahead, left, turned) from pose `a`, in `a`'s frame."""
    c, s = math.cos(a[2]), math.sin(a[2])
    return a[0] + r[0] * c - r[1] * s, a[1] + r[0] * s + r[1] * c, wrap_pi(a[2] + r[2])


class LineMeter:
    """How far a straight line (`forward`) has gone from where it set out -- ahead, aside, turned, in
    the start's heading -- from the wheels' odometry when the board gives it (`app.slamware_sdk.odo_pose`),
    else from the board's pose with its relocalizations taken out: a tick whose pose moved past
    FORWARD_JUMP_M + vmax x dt (or turned past FORWARD_JUMP_DEG) counts what was ASKED that tick (vx x dt)
    instead. No I/O. See FORWARD_ASIDE_STOP_M above for why (23 Nov)."""

    def __init__(self, pose: tuple[float, float, float], odo: tuple[float, float, float] | None = None):
        self.p0, self.o0 = pose, odo
        self.by = "odometry" if odo is not None else "pose"
        self.ahead = self.aside = self.turned = 0.0
        self.pose_way = [0.0, 0.0, 0.0]                  # the pose's own way, jumps out (always kept)
        self.yaw_off = 0.0                               # the heading jumps taken out
        self.last = pose
        self.jumps, self.jump_max = 0, 0.0
        self.notes: list[str] = []

    def update(self, pose, odo, vx: float, dt: float, vmax: float) -> tuple[float, float | None]:
        """One read: (how far it moved this tick -- the stall check's --, the jump in metres or None)."""
        lx, ly, lh = self.last
        moved = math.hypot(pose[0] - lx, pose[1] - ly)
        turn = wrap_pi(pose[2] - lh)
        jump = None
        t = self.pose_way[2]
        if moved > FORWARD_JUMP_M + vmax * dt or abs(turn) > math.radians(FORWARD_JUMP_DEG):
            jump = moved
            self.jumps += 1
            self.jump_max = max(self.jump_max, moved)
            self.yaw_off += turn
            step = (vx * dt * math.cos(t), vx * dt * math.sin(t), 0.0)
        else:
            h = self.p0[2] + self.yaw_off
            dx, dy = pose[0] - lx, pose[1] - ly
            step = (dx * math.cos(h) + dy * math.sin(h), -dx * math.sin(h) + dy * math.cos(h), turn)
        self.last = pose
        self.pose_way = [a + b for a, b in zip(self.pose_way, step)]
        before = (self.ahead, self.aside)
        if self.by == "odometry" and odo is None:
            self.by = "pose"
            self.notes.append("the wheels' odometry stopped answering -- on by the pose from %.2f m" % self.ahead)
        if self.by == "odometry":
            self.ahead, self.aside, self.turned = _rel(self.o0, odo)
            went = math.hypot(self.pose_way[0], self.pose_way[1])
            off = abs(wrap_pi(self.turned - self.pose_way[2]))
            if off > math.radians(ODO_TURN_DISAGREE_DEG):
                self.by = "pose"
                self.notes.append("the odometry says %.0f° turned where the pose (its jumps out) says %.0f -- not "
                                  "the wheels' frame: measuring by the pose" % (
                                      math.degrees(self.turned), math.degrees(self.pose_way[2])))
                self.ahead, self.aside, self.turned = self.pose_way
            elif went >= ODO_CHECK_M:
                said = math.hypot(self.ahead, self.aside)
                if not ODO_RATIO[0] * went <= said <= ODO_RATIO[1] * went:
                    # not the wheels (a constant or scaled reply): the pose's own way from the start
                    self.by = "pose"
                    self.notes.append("the odometry says %.2f m where the pose (its jumps out) says %.2f -- not "
                                      "the wheels: measuring by the pose" % (said, went))
                    self.ahead, self.aside, self.turned = self.pose_way
        else:
            self.ahead += step[0]
            self.aside += step[1]
            self.turned = wrap_pi(self.turned + step[2])
        return math.hypot(self.ahead - before[0], self.aside - before[1]), jump

    def pop_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    def end_pose(self) -> tuple[float, float, float]:
        """Where the wheels put her, in the START's frame (the frame `from` -- the dock -- was read in)."""
        return _compose(self.p0, (self.ahead, self.aside, self.turned))


class _OdoGuard:
    """The board for `back_to` (straight back onto the dock, blind): its pose() is where the WHEELS
    put her since the first read (`anchor`: from a `forward` of this same run that set out on this
    dock -- the dock itself plus the odometry since --, else the board's own pose at the first read),
    so a relocalization under the line no longer stops it (23 Nov 22:26:49, "jumped 0.16 m while
    backing": the localization flipping along the dock's line). With no odometry, or when it stops
    answering, the board's pose with its jumps taken out (continuous from the last pose given), and a
    jump past POSE_LOST_M is a Stop as before (`_Jumped`). The board's own jumps are counted for the
    report after (`jumps`, `board_last`). `_back_home` (the long reverse, map-frame alignment)
    keeps `_JumpGuard`."""

    def __init__(self, board: "Board", vmax: float, odo: Callable[[], tuple[float, float, float] | None],
                 anchor: tuple[tuple[float, float, float], tuple[float, float, float]] | None = None):
        self._b, self._vmax, self._odo = board, vmax, odo
        self.anchor = anchor
        self.by = "odometry"
        self._a: tuple[float, float, float] | None = None
        self._o0: tuple[float, float, float] | None = None
        self._last: tuple[float, float, float, float] | None = None   # the board's last read, and when
        self._off = (0.0, 0.0, 0.0)                      # pose way: added to the board's pose
        self._given: tuple[float, float, float] | None = None
        self._ot0: float | None = None                   # the odometry's yaw at its first read (ODO_TURN_DISAGREE_DEG)
        self._pturn = 0.0                                # the board's turn since then, its jumps out
        self._plast = 0.0
        self.jumps, self.jump_max = 0, 0.0
        self.board_last: tuple[float, float, float] | None = None
        self.notes: list[str] = []

    def pose(self) -> tuple[float, float, float]:
        p = self._b.pose()
        now = time.monotonic()
        o = self._odo() if self.by == "odometry" else None
        if self._a is None:                              # the first read
            if self.anchor is not None and o is not None:
                self._a, self._o0 = self.anchor
            else:
                self._a, self._o0 = p, o
                if self.anchor is not None:
                    self.notes.append("no odometry for the way back along this run's own line -- from the board's pose")
        jumped = None
        if self._last is not None:
            lx, ly, lh, lt = self._last
            moved = math.hypot(p[0] - lx, p[1] - ly)
            turned = wrap_pi(p[2] - lh)
            if moved > FORWARD_JUMP_M + self._vmax * (now - lt) or abs(turned) > math.radians(FORWARD_JUMP_DEG):
                jumped = (moved, turned, lx, ly, lh)
                self.jumps += 1
                self.jump_max = max(self.jump_max, moved)
        self._last = (p[0], p[1], p[2], now)
        self.board_last = p
        # the odometry's turn since the first read against the board's own (its jumps out): not the same
        # frame (a yaw in degrees -- ODO_TURN_DISAGREE_DEG) = on by the board's pose, as if it stopped answering
        if o is not None:
            if self._ot0 is None:
                self._ot0 = o[2]
            elif jumped is None:
                self._pturn = wrap_pi(self._pturn + wrap_pi(p[2] - self._plast))
            if (self.by == "odometry"
                    and abs(wrap_pi(wrap_pi(o[2] - self._ot0) - self._pturn)) > math.radians(ODO_TURN_DISAGREE_DEG)):
                self.notes.append("the odometry's turn (%.0f°) is not the board's (%.0f°) -- not the wheels' frame"
                                  % (math.degrees(wrap_pi(o[2] - self._ot0)), math.degrees(self._pturn)))
                o = None
        self._plast = p[2]
        if self.by == "odometry" and self._o0 is not None and o is not None:
            if jumped:
                self.notes.append("the board's pose jumped %.2f m / %.0f° while backing -- a relocalization: on by "
                                  "the wheels" % (jumped[0], abs(math.degrees(jumped[1]))))
            q = _compose(self._a, _rel(self._o0, o))
        else:
            if self.by == "odometry":
                self.by = "pose"
                if self._given is not None:              # on from the last pose given, not from the board's
                    self._off = (self._given[0] - p[0], self._given[1] - p[1], wrap_pi(self._given[2] - p[2]))
                    self.notes.append("the wheels' odometry stopped answering -- on by the board's pose, its "
                                      "jumps taken out")
                    jumped = None
            if jumped:
                if jumped[0] > POSE_LOST_M:
                    raise _Jumped("the board's pose jumped %.2f m / %.0f° while backing -- stopped where it is"
                                  % (jumped[0], abs(math.degrees(jumped[1]))))
                self._off = (self._off[0] + jumped[2] - p[0], self._off[1] + jumped[3] - p[1],
                             wrap_pi(self._off[2] + jumped[4] - p[2]))
                self.notes.append("the board's pose jumped %.2f m / %.0f° while backing -- a relocalization, "
                                  "taken out" % (jumped[0], abs(math.degrees(jumped[1]))))
            q = (p[0] + self._off[0], p[1] + self._off[1], wrap_pi(p[2] + self._off[2]))
        self._given = q
        return q

    def __getattr__(self, name: str):
        return getattr(self._b, name)


class _JumpGuard:
    """The board, with each pose read checked against the last (`back_to`): moved more than
    FORWARD_JUMP_M plus what `vmax` covers since, or turned past FORWARD_JUMP_DEG = the board
    relocalized under the line (the wrong floor's map, a stale pose) -- Stop where it is (the
    velocity session's exit sends its zero). Everything else is the board's own."""

    def __init__(self, board: "Board", vmax: float):
        self._b, self._vmax = board, vmax
        self._last: tuple[float, float, float, float] | None = None

    def pose(self) -> tuple[float, float, float]:
        p = self._b.pose()
        now = time.monotonic()
        if self._last is not None:
            lx, ly, lh, lt = self._last
            moved = math.hypot(p[0] - lx, p[1] - ly)
            turned = abs(wrap_pi(p[2] - lh))
            if moved > FORWARD_JUMP_M + self._vmax * (now - lt) or turned > math.radians(FORWARD_JUMP_DEG):
                raise _Jumped("the board's pose jumped %.2f m / %.0f° while backing -- stopped where it is"
                              % (moved, math.degrees(turned)))
        self._last = (p[0], p[1], p[2], now)
        return p

    def __getattr__(self, name: str):
        return getattr(self._b, name)


class _GlideBoard:
    """The board, with `pose` by the wheels from where it stood when this was made (`anchor`: the board's pose then).
    `raw` is the board's own. A reading that fails gives the last one with `failed` counting: the glide stands for it
    (zero sent, review 25 Nov: driving on the last pose put her 0.35 m past the point after three), and more than five in a
    row is a Stop."""

    def __init__(self, board, anchor: tuple[float, float, float], odo0: tuple[float, float, float], read):
        self._b, self._anchor, self._odo0, self._read, self._last = board, anchor, odo0, read, anchor
        self.failed, self._prev, self._way, self._corr = 0, (0.0, 0.0), 0.0, (0.0, 0.0, 0.0)

    def pose(self) -> tuple[float, float, float]:
        o = self._read()
        if o is None:
            self.failed += 1
            if self.failed > 5:
                raise Stop("the wheels' odometry stopped answering -- stopped where it is")
            return self._last
        self.failed = 0
        ahead, left, turned = _rel(self._odo0, o)
        rel = (ahead / GLIDE_ODO_SCALE, left / GLIDE_ODO_SCALE, turned / GLIDE_ODO_TURN)
        d = math.hypot(rel[0] - self._prev[0], rel[1] - self._prev[1])
        self._prev, self._way = rel[:2], self._way + d
        x, y, yaw = _compose(self._anchor, rel)
        cx, cy, cw = self._corr
        if GLIDE_FOLLOW_PER_M > 0.0 and self._way > GLIDE_FOLLOW_FROM_M and d > 0.0:
            bx, by, bw = self._b.pose()
            k = min(1.0, GLIDE_FOLLOW_PER_M * min(d, 0.2))
            cx, cy = cx + k * (bx - x - cx), cy + k * (by - y - cy)
            cw = cw + k * wrap_pi(bw - yaw - cw)
            self._corr = (cx, cy, cw)
        self._last = (x + cx, y + cy, wrap_pi(yaw + cw))
        return self._last

    def raw(self) -> tuple[float, float, float]:
        return self._b.pose()

    def reanchor(self, pose: tuple[float, float, float]) -> bool:
        """From here on by the wheels again, starting at `pose` (the board's, standing)."""
        o = self._read()
        if o is None:
            return False
        self._anchor, self._odo0, self._last = pose, o, pose
        self._prev, self._corr, self._way = (0.0, 0.0), (0.0, 0.0, 0.0), 0.0
        return True

    def reyaw(self) -> None:
        """Standing after a turn: on from here, the place by the wheels, the heading by the board."""
        x, y, yaw = self.pose()
        o = self._read()
        if o is None:
            return
        bw = [wrap_pi(self._b.pose()[2] - yaw) for _ in range(3)]
        turn = sorted(bw)[1]
        if abs(turn) > math.radians(GLIDE_REYAW_MAX_DEG):
            return                                       # not a turn's error: the board has lost itself
        self._anchor, self._odo0 = (x, y, wrap_pi(yaw + turn)), o
        self._prev, self._corr, self._last = (0.0, 0.0), (0.0, 0.0, 0.0), (x, y, wrap_pi(yaw + turn))

    def __getattr__(self, name: str):
        return getattr(self._b, name)


class Walker:
    def __init__(self, board: Board, doc: dict, mapping: bool, register: bool):
        self.b, self.doc, self.mapping_on, self.register = board, doc, mapping, register
        self.sdk = None
        self.trace = None                                # --trace: one JSON line per creep tick
        self.env_path = ENV_PATH
        self.record_dir = None                           # --record DIR
        self.patience_s = float(doc.get("patience_s") or 0.0)
        self.min_quality = int(doc.get("min_quality") or MIN_QUALITY)
        self.min_fit = float(doc.get("min_fit") or 0.0)
        self.events_on = bool(doc.get("events"))         # `@@` lines for app/event_walk.py
        self._map_fit: MapFit | None | bool = False      # False = not read yet (map_fit)
        self.go_line: GoLine | None = None               # --wait-go: drive only on its "go" (`hold_for_go`)
        self.go_wait_s = 0.0
        self.t0 = time.monotonic()

    def say(self, text: str) -> None:
        print("[%5.1f] %s" % (time.monotonic() - self.t0, text), flush=True)

    def event(self, **fields) -> None:
        """One machine line for app/event_walk.py (off unless the route file
        asks): what staff and Emma must know while the walk is still going."""
        if self.events_on:
            print("@@ " + json.dumps(fields), flush=True)

    def _sdk(self):
        if self.sdk is None:
            from app import slamware_sdk as sw

            self.sdk = sw.SlamwareSdk(SDK_HOST, SDK_PORT)
            self.sdk.call("getsdpversion")
        return self.sdk

    def odo(self) -> tuple[float, float, float] | None:
        """The wheels' odometry pose (app.slamware_sdk.odo_pose), or None. One failure and it is not
        asked again this run (said once): a board that does not know the command must not cost every
        tick a timeout -- the lines measure by the pose then, its jumps taken out (`LineMeter`)."""
        if not USE_ODOMETRY or getattr(self, "_odo_off", None):
            return None
        try:
            from app.slamware_sdk import odo_pose

            return odo_pose(self._sdk(), timeout=ODO_TIMEOUT_S)
        except Exception as e:                           # noqa: BLE001 -- the pose is the fallback
            self._odo_off = "%s: %s" % (type(e).__name__, str(e)[:100])
            self.say("(no wheel odometry from the board -- %s; lines measured by its pose, relocalizations "
                     "taken out)" % self._odo_off)
            return None

    # -- legs ---------------------------------------------------------------
    def leg(self, step: dict, i: int, through: bool = False) -> None:
        """One MoveTo. `through`: the next step is another plain point, so this
        one hands over HANDOVER_M before it -- the next MoveTo replaces this one
        while the robot is still rolling (28 ต.ค., owner: "ปรับให้ลื่นสมูทขึ้น";
        every point of the first tours was a full stop and a start)."""
        tx, ty = (float(v) for v in step["to"])
        yaw = math.radians(float(step["face"])) if step.get("face") is not None else None
        if step.get("speed"):
            self.b.set_speed(step["speed"])
        start = time.monotonic()
        stuck_after = max(STUCK_S, self.patience_s)
        # with patience the board's own "ended short" (someone in the way) is asked
        # again until the patience is spent, not once
        tries = LEG_TRIES if not self.patience_s else max(LEG_TRIES, int(self.patience_s / LEG_RETRY_WAIT_S))
        for attempt in range(1, tries + 1):
            aid = self.b.move_to(tx, ty, yaw)
            last, still = self.b.pose(), time.monotonic()
            result = None
            held = False
            while result is None:
                time.sleep(0.2)
                p = self.b.pose()
                if math.hypot(p[0] - last[0], p[1] - last[1]) > 0.04 or abs(wrap_pi(p[2] - last[2])) > 0.08:
                    last, still = p, time.monotonic()
                    if held:
                        held = False
                        self.event(moving=i)
                elif self.patience_s and not held and time.monotonic() - still > HELD_NOTE_S:
                    held = True
                    self.event(held=i, at=[round(p[0], 2), round(p[1], 2)])
                if through and math.hypot(p[0] - tx, p[1] - ty) < HANDOVER_M:
                    result = "through"                   # not cancelled: the next MoveTo takes over
                    if through == "cancel":
                        self.b.cancel()                  # ... unless the next is a glide (rolls_through)
                elif self.b.current() is None:
                    result = "done"
                elif time.monotonic() - still > stuck_after:
                    self.b.cancel()
                    result = "stuck"
                elif time.monotonic() - start > LEG_MAX_S + stuck_after - STUCK_S:
                    self.b.cancel()
                    result = "too long"
            x, y, h = self.b.pose()
            spent = self.patience_s and time.monotonic() - start > self.patience_s
            if result != "done" or math.hypot(x - tx, y - ty) <= 0.6 or attempt == tries or spent:
                break
            if self.patience_s:
                self.event(held=i, at=[round(x, 2), round(y, 2)], ended_short=round(math.hypot(x - tx, y - ty), 2))
            # 28 ต.ค. smooth run 4: the board ended a leg 0.92 m short on its own
            # (someone in the way?) -- say why, give it a moment, ask again
            self.say("step %d: the board ended %.2f m short (%s) -- once more in %.0f s" % (
                i, math.hypot(x - tx, y - ty), self.b.action_reason(aid), LEG_RETRY_WAIT_S))
            time.sleep(LEG_RETRY_WAIT_S)
        q = self.b.quality()
        miss = math.hypot(x - tx, y - ty)
        self.say("step %d %s -> (%.2f, %.2f)%s: %s in %.0f s, at (%.2f, %.2f) %.0f°, %.2f m off, Q%d%s" % (
            i, step.get("label") or "", tx, ty, "" if yaw is None else " facing %.0f°" % math.degrees(yaw),
            result, time.monotonic() - start, x, y, math.degrees(h), miss, q,
            "" if attempt == 1 else ", try %d" % attempt))
        if result not in ("done", "through"):
            raise Stop("step %d: %s" % (i, result))
        if miss > 0.6:
            raise Stop("step %d: ended %.2f m from the point (the board gave up on it)" % (i, miss))
        if yaw is not None and abs(wrap_pi(h - yaw)) > math.radians(FACE_FIX_DEG):
            # 28 ต.ค. at the door front: MoveTo with_yaw ended facing -176° for -98°
            self.say("step %d: the board left it facing %.0f° -- turning to %.0f°" % (
                i, math.degrees(h), math.degrees(yaw)))
            self.turn_to(yaw, tol_deg=3.0)
        if q < 40:
            raise Stop("step %d: localization quality %d" % (i, q))

    # -- our own line -------------------------------------------------------
    def creep(self, step: dict, i: int) -> None:
        """Drive a creep line (CreepLine): read the board's pose and a lidar
        frame, send what the line asks, until it is done or stops."""
        from app.slamware_sdk import VelocitySession

        if self.mapping_on:
            # CLAUDE.md, 28 ต.ค.: a --map run in the multimedia room wrote people and the
            # door leaf into the board's map of the door, and the pose jumped 0.2 m in the
            # doorway on every run after it. A creep line is that door and that room, and
            # --map left mapping on until docking (room_tv_go has no dock: mapped end to end).
            self.b.mapping(False)
            self.mapping_on = False
            self.say("step %d: mapping off for the rest -- never --map through the room's door" % i)
        line = CreepLine(step, i, self.b.pose(), time.monotonic(), fit=self.map_fit())
        for m in line.pop_messages():
            self.say(m)
        self._drop_events("step %d" % i)
        t0 = time.monotonic()
        with VelocitySession(self._sdk()) as v:
            while True:
                pts_now = self.scan()
                act = line.tick(time.monotonic(), self.b.pose(), pts_now, self._board_event)
                for m in line.pop_messages():
                    self.say(m)
                if act.kind == "again":
                    continue
                if act.kind == "done":
                    break
                if act.kind == "stop":
                    v.set(0.0, 0.0)
                    raise Stop(act.reason)
                if act.kind == "spin":
                    self._spot_turn(v, act.heading, tol_deg=3.0)
                    line.after_spin(time.monotonic())
                    continue
                renewed = v.set(act.vx, act.w)
                line.sent(act, renewed)
                if self.trace is not None and line.last_row is not None:
                    self.trace.write(json.dumps(line.last_row) + "\n")
                time.sleep(0.05)
            v.set(0.0, 0.0)
        x, y, h = self.b.pose()
        self.say("step %d %s: line of %d points done in %.0f s, at (%.2f, %.2f) %.0f°, off the line at most %.2f m, "
                 "closest side %.2f m, closest ahead %s, %d pose jump(s), Q%d" % (
                     i, step.get("label") or "", line.n_points, time.monotonic() - t0, x, y, math.degrees(h),
                     line.worst_lat, line.worst_side,
                     "-" if line.slowest_front is None else "%.2f m" % line.slowest_front, line.pose.jumps,
                     self.b.quality()))

    # -- open floor, our own ---------------------------------------------------
    def _glide_odo(self) -> tuple[float, float, float] | None:
        try:
            from app.slamware_sdk import odo_pose

            o = odo_pose(self._sdk(), timeout=ODO_TIMEOUT_S)
        except Exception:                                # noqa: BLE001 -- None: the board's pose, or the last reading
            return None
        return o if all(math.isfinite(v) for v in o) and abs(o[2]) <= math.pi + 0.01 else None

    def glide(self, step: dict, i: int) -> None:
        """`_glide`, by the wheels when they answer (GLIDE_ANCHOR_S): the board's pose, standing, is where it begins."""
        board, first = self.b, self._glide_odo()
        if first is None:
            return self._glide(step, i, False)
        t0 = since = time.monotonic()
        at = steady = board.pose()
        while time.monotonic() - t0 < GLIDE_STEADY_MAX_S:
            time.sleep(0.1)
            p = board.pose()
            if math.hypot(p[0] - steady[0], p[1] - steady[1]) > GLIDE_STEADY_M:
                steady, since = p, time.monotonic()      # moved (or jumped): held still GLIDE_ANCHOR_S from here
            at = p
            if time.monotonic() - since >= GLIDE_ANCHOR_S:
                break
        odo0 = self._glide_odo()
        if odo0 is None or math.hypot(odo0[0] - first[0], odo0[1] - first[1]) > 0.02:
            return self._glide(step, i, False)               # moving, or gone: not something to walk by
        self.b = _GlideBoard(board, at, odo0, self._glide_odo)
        try:
            self._glide(step, i, True)
        finally:
            self.b = board

    def _glide(self, step: dict, i: int, wheels: bool) -> None:
        """Straight lines over open floor and turns on the spot, velocity control end to end (GLIDE_* above)."""
        from app import robot_lanes as rl
        from app.slamware_sdk import VelocitySession

        label = step.get("label") or ""
        speed = min(max(float(step.get("speed") or GLIDE_SPEED), 0.1), GLIDE_SPEED_MAX)
        pts = [(float(p[0]), float(p[1])) for p in step["glide"]]
        short_ok = min(max(float(step.get("short_ok") or 0.0), GLIDE_SHORT_OK_M), GLIDE_SHORT_MAX_M)
        self.map_fit()                                   # read before the wheels are taken: it is a pause
        half = rl.ROBOT_BASE_LENGTH / 2.0
        limits = replace(rl.DriveLimits(), v_max=speed, front_v=speed, front_stop=half + GLIDE_HOLD_M,
                         front_slow=half + GLIDE_SLOW_M, align_deg=GLIDE_REALIGN_DEG, accel=GLIDE_ACCEL,
                         decel=GLIDE_DECEL)
        if self.mapping_on:
            self.b.mapping(False)
            self.mapping_on = False
        self._drop_events("step %d" % i)
        t0 = time.monotonic()
        note = {"jumps": 0, "short": None, "rejoins": 0, "lost": 0, "held_off": None}
        with VelocitySession(self._sdk()) as v:
            for k, (tx, ty) in enumerate(pts):
                self._glide_to(v, i, label, (tx, ty), limits, note, short_ok if k == len(pts) - 1 else GLIDE_SHORT_OK_M)
                if k == len(pts) - 1 and note["short"] is None and note["held_off"] is None and not wheels:
                    x, y, _ = self._glide_steady(v)
                    if math.hypot(x - tx, y - ty) > GLIDE_AGAIN_M:
                        self.say("step %d %s: %.2f m from the point by the board -- once more" % (
                            i, label, math.hypot(x - tx, y - ty)))
                        self._glide_to(v, i, label, (tx, ty), limits, note, short_ok, again=True)
            # `settle: false` (card 3's glide to the globe, 25 Nov ~05:10): the board's pose there jumps (Q66-73) and its
            # settle moved her on again 0.16-0.40 m after she was there, 13 s more -- the owner: "ไปไว ไม่สะดุด ไม่รอนาน"
            if wheels and note["short"] is None and hasattr(self.b, "reanchor") and step.get("settle", True) is not False:
                self._glide_settle(v, i, label, pts[-1], limits, note, short_ok)
            if step.get("face") is not None:
                self._glide_turn(v, i, label, math.radians(float(step["face"])), GLIDE_FACE_TOL_DEG)
            v.set(0.0, 0.0)
        x, y, h = self.b.pose()
        q = self.b.quality()
        if wheels:
            rx, ry, _h = self.b.raw()
            note["board_off"] = "%.2f m" % math.hypot(rx - x, ry - y)
        cx, cy = note["held_off"] or (0.0, 0.0)
        miss = math.hypot(x + cx - pts[-1][0], y + cy - pts[-1][1])
        self.say("step %d %s -> (%.2f, %.2f)%s: glided in %.0f s, at (%.2f, %.2f) %.0f°, %.2f m off, Q%d%s" % (
            i, label, pts[-1][0], pts[-1][1], "" if step.get("face") is None else " facing %.0f°" % float(step["face"]),
            time.monotonic() - t0, x, y, math.degrees(h), miss, q,
            "".join(", %s %s" % (k, n) for k, n in note.items() if n)))
        if miss > 0.6:
            raise Stop("step %d: ended %.2f m from the point" % (i, miss))
        if q < 40:
            raise Stop("step %d: localization quality %d" % (i, q))

    def _glide_settle(self, v, i: int, label: str, to: tuple[float, float], limits, note: dict, short_ok: float) -> None:
        """At the point by the wheels: the board, standing (the lidar agrees with it there, 24 Nov), further than
        GLIDE_SETTLE_M from the wheels and the lidar not against it = on from the board's pose, once (review 25 Nov: nothing
        bounded how far the two could disagree)."""
        self._glide_set(v, 0.0, 0.0, "glide")
        time.sleep(0.3)
        reads = [self.b.raw() for _ in range(3)]
        bx = sorted(r[0] for r in reads)[1]
        by = sorted(r[1] for r in reads)[1]
        bw = sorted((wrap_pi(r[2] - reads[0][2]) for r in reads))[1] + reads[0][2]
        x, y, yaw = self.b.pose()
        off = math.hypot(bx - x, by - y)
        note["board_off"] = "%.2f m" % off
        if off <= GLIDE_SETTLE_M:
            return
        fit = self.map_fit()
        if fit is not None:
            pts = self.scan()
            if fit.score((x, y, yaw), pts) > fit.score((bx, by, bw), pts) + FIT_HOLD_MARGIN:
                note["board_off"] += " (the lidar fits the wheels)"
                return
        if not self.b.reanchor((bx, by, wrap_pi(bw))):
            return
        self.say("step %d %s: the board, standing, puts her %.2f m from the wheels -- on from it" % (i, label, off))
        note["settled"] = "%.2f m" % off
        self._glide_to(v, i, label, to, limits, note, short_ok, again=True)

    def _glide_set(self, v, vx: float, w: float, what: str) -> bool:
        """One command. True = the action had to be taken up again after a gap (ramp from standstill); a person's
        taking it away (B / the robot's own screen) is a Stop."""
        if v.set(vx, w):
            if _taken(v):
                v.set(0.0, 0.0)
                raise Stop("%s: the board's velocity action was taken away (B / the robot's screen) -- stopped" % what)
            return True
        return False

    def _glide_steady(self, v) -> tuple[float, float, float]:
        """Stand until the board's pose holds still (GLIDE_STEADY_*) and give it."""
        self._glide_set(v, 0.0, 0.0, "glide")
        t0 = time.monotonic()
        seen = [(t0, *self.b.pose())]
        while True:
            time.sleep(0.1)
            self._glide_set(v, 0.0, 0.0, "glide")
            now = time.monotonic()
            seen.append((now, *self.b.pose()))
            recent = [s for s in seen if now - s[0] <= GLIDE_STEADY_S]
            x, y = seen[-1][1], seen[-1][2]
            if (now - t0 >= GLIDE_STEADY_S and all(math.hypot(s[1] - x, s[2] - y) <= GLIDE_STEADY_M for s in recent)) \
                    or now - t0 > GLIDE_STEADY_MAX_S:
                return seen[-1][1:]

    def _glide_held(self, held: dict, i: int, p, what: str) -> None:
        """Something in the way, this tick: the phone told once it lasts, a Stop when the patience is spent."""
        now = time.monotonic()
        if held.get("since") is None:
            held.update(since=now, said=False)
        if not held["said"] and now - held["since"] > HELD_NOTE_S:
            held["said"] = True
            self.event(held=i, at=[round(p[0], 2), round(p[1], 2)])
        if now - held["since"] > max(STUCK_S, self.patience_s):
            raise Stop("step %d: %s for %.0f s" % (i, what, now - held["since"]))

    def _glide_free(self, held: dict, i: int) -> None:
        if held.get("since") is not None:
            if held["said"]:
                self.event(moving=i)
            held["since"] = None

    def _glide_to(self, v, i: int, label: str, to: tuple[float, float], limits, note: dict,
                  short_ok: float = GLIDE_SHORT_OK_M, again: bool = False) -> None:
        """Face the point, then one straight line to it. `again`: she has driven this line already."""
        from app import robot_lanes as rl

        half = rl.ROBOT_BASE_LENGTH / 2.0
        what = "step %d %s" % (i, label)
        x, y, _ = self.b.pose()
        if math.hypot(to[0] - x, to[1] - y) < GLIDE_MIN_M:
            return
        held: dict = {}

        def lay(once: bool) -> tuple[float, float] | None:
            """Face the point from where she stands; the line starts there. None = this is the point (`once`: the line
            was driven before, and what is left of it lies beside or behind her)."""
            px, py, ph = self.b.pose()
            d, bearing = math.hypot(to[0] - px, to[1] - py), math.atan2(to[1] - py, to[0] - px)
            if d < GLIDE_MIN_M or (once and d < GLIDE_BEHIND_M
                                   and abs(wrap_pi(bearing - ph)) > math.radians(GLIDE_BEHIND_DEG)):
                return None
            self._glide_turn(v, i, label, bearing, GLIDE_ALIGN_DEG)
            if hasattr(self.b, "reyaw"):
                self.b.reyaw()
            px, py, _h = self.b.pose()
            return (px, py) if math.hypot(to[0] - px, to[1] - py) >= GLIDE_MIN_M else None

        a = lay(again)
        if a is None:
            return
        filt = PoseFilter(self.map_fit(), GLIDE_HOLD_POSE_M)
        last_good: tuple[float, float, float] | None = None
        across = 0.0                                     # metres added across the line: where the board puts her
        short_since: float | None = None
        prev, t_prev = (0.0, 0.0), time.monotonic()
        moving: list[tuple[float, float, float, float, float, float]] = []
        drove = 0.0
        cap = 15.0 + 4.0 * math.hypot(to[0] - a[0], to[1] - a[1]) / limits.v_max
        last_off: float | None = None
        jumped_off = 0.0                                 # the board's jumps across, left out
        while True:
            raw, pts = self.b.pose(), self.scan()
            now = time.monotonic()
            el = now - t_prev
            dt = max(0.05, min(0.3, el))
            t_prev = now
            if getattr(self.b, "failed", 0):
                # no reading from the wheels this tick: stand (pose() stops the walk after five in a row)
                self._glide_set(v, 0.0, 0.0, what)
                prev, moving = (0.0, 0.0), []
                time.sleep(0.05)
                continue
            (x, y, yaw), msgs, bad = filt.update(raw, now, prev[0], max(dt, el), pts)
            for m in msgs:
                self.say("%s: %s" % (what, m))
            relay = False
            if bad:
                self._glide_set(v, 0.0, 0.0, what)
                note["lost"] += 1
                if note["lost"] > GLIDE_LOST_MAX:
                    raise Stop("%s: %s" % (what, bad))
                at = self._glide_steady(v)
                fit, pts2 = self.map_fit(), self.scan()
                if fit is not None and last_good is not None and \
                        fit.score(last_good, pts2) > fit.score(at, pts2) + FIT_HOLD_MARGIN:
                    # it held still where the lidar says she is not: the board is the wrong one, not where she drove --
                    # on by where she was, no further than that off the board
                    off = (last_good[0] - at[0], last_good[1] - at[1])
                    if math.hypot(*off) > GLIDE_HOLD_POSE_M:
                        raise Stop("%s: %s -- and the lidar fits where she was (%.2f), not there (%.2f)" % (
                            what, bad, fit.score(last_good, pts2), fit.score(at, pts2)))
                    self.say("%s: %s -- the lidar fits where she was (%.2f), not there (%.2f): on by it" % (
                        what, bad, fit.score(last_good, pts2), fit.score(at, pts2)))
                    note["jumps"] += filt.jumps + 1
                    filt = PoseFilter(fit, GLIDE_HOLD_POSE_M)
                    filt.corr = filt.jumped = off
                    filt.holding, filt.raw_prev = True, (at[0], at[1])
                    prev, t_prev, moving, last_good = (0.0, 0.0), time.monotonic(), [], None
                    continue
                self.say("%s: %s -- stood till it held still, on from (%.2f, %.2f)" % (what, bad, at[0], at[1]))
                relay = True
            else:
                last_good = (x, y, yaw)
            board_raw = self.b.raw() if hasattr(self.b, "raw") else None
            if board_raw is not None and not relay:
                ln = math.hypot(to[0] - a[0], to[1] - a[1])
                if ln > 1e-6:
                    nx, ny = -(to[1] - a[1]) / ln, (to[0] - a[0]) / ln
                    bx, by, _bw = board_raw
                    off = ((bx - a[0]) * nx + (by - a[1]) * ny) - ((x - a[0]) * nx + (y - a[1]) * ny)
                    if last_off is not None and abs(off - last_off) > GLIDE_ACROSS_STEP_M:
                        jumped_off += off - last_off     # a jump of the board's: never followed (review 25 Nov)
                    last_off = off
                    off -= jumped_off
                    if abs(off) < GLIDE_ACROSS_MAX_M:
                        across += min(1.0, dt / GLIDE_ACROSS_TAU_S) * (off - across)
                    x, y = x + across * nx, y + across * ny
            front = rl.front_distance(pts, half_width=GLIDE_HALF_WIDTH, margin=0.0, max_range=2.5)
            cmd = rl.lane_command((x, y, yaw), a, to, prev, dt, front, limits)
            if not relay and cmd.done:
                break
            if not relay and (abs(cmd.lat) > GLIDE_REJOIN_M or cmd.phase == "align"):
                self._glide_set(v, 0.0, 0.0, what)
                note["rejoins"] += 1
                if note["rejoins"] > GLIDE_REJOINS_MAX:
                    raise Stop("%s: %.2f m aside of its line, %.0f° off it" % (what, cmd.lat, math.degrees(cmd.err)))
                relay = True
            if relay:
                note["jumps"] += filt.jumps
                laid = lay(True)
                if laid is None:
                    filt = PoseFilter(None)
                    break
                a, filt = laid, PoseFilter(self.map_fit(), GLIDE_HOLD_POSE_M)
                prev, t_prev, moving, last_good, across = (0.0, 0.0), time.monotonic(), [], None, 0.0
                last_off, jumped_off = None, 0.0
                continue
            if self._board_event():
                self._glide_set(v, 0.0, 0.0, what)
                raise Stop("%s: board event (bumper?)" % what)
            gap = None if front is None else front - half
            if gap is not None and gap < (GLIDE_GO_M if held.get("since") is not None else GLIDE_HOLD_M):
                self._glide_set(v, 0.0, 0.0, what)
                prev, moving = (0.0, 0.0), []
                short_since = now if short_since is None else short_since
                waited = now - short_since
                if (cmd.rem <= short_ok and waited >= GLIDE_SHORT_S) or \
                        (cmd.rem <= max(short_ok, GLIDE_NEAR_OK_M) and waited >= GLIDE_NEAR_WAIT_S):
                    note["short"] = "%.2f m (something %.2f m ahead)" % (cmd.rem, gap)
                    break
                self._glide_held(held, i, raw, "something %.2f m ahead" % gap)
                time.sleep(0.1)
                continue
            self._glide_free(held, i)
            short_since = None
            drove += dt
            if drove > cap:
                self._glide_set(v, 0.0, 0.0, what)
                raise Stop("%s: %.0f s of driving and %.2f m to go" % (what, drove, cmd.rem))
            ox, oy = filt.odometry(raw)
            # by the wheels the board's own pose is watched too: wheels that go round against something move nothing there
            bx, by = board_raw[:2] if board_raw is not None else (ox, oy)
            moving = [m for m in moving if now - m[0] <= CREEP_STALL_S] + [(now, ox, oy, cmd.vx, bx, by)]
            if now - moving[0][0] >= CREEP_STALL_S * 0.9 and min(m[3] for m in moving) >= 0.08 \
                    and (math.hypot(ox - moving[0][1], oy - moving[0][2]) < CREEP_STALL_M
                         or math.hypot(bx - moving[0][4], by - moving[0][5]) < CREEP_STALL_M):
                self._glide_set(v, 0.0, 0.0, what)
                raise Stop("%s: asked to go and not moving (caught on something below the lidar?)" % what)
            prev = (0.0, 0.0) if self._glide_set(v, cmd.vx, cmd.omega, what) else (cmd.vx, cmd.omega)
            time.sleep(0.05)
        self._glide_set(v, 0.0, 0.0, what)
        note["jumps"] += filt.jumps
        note["held_off"] = tuple(round(c, 3) for c in filt.corr) if filt.holding else None

    def _glide_turn(self, v, i: int, label: str, yaw: float, tol_deg: float) -> None:
        """On the spot to `yaw`, the shorter way, one move: looked ahead of every tick (the arms' rectangle round the
        turn still to come), waited for when someone stands in it. Behind her nobody looks: the bumper stops it."""
        from app import robot_lanes as rl

        limits = replace(rl.DriveLimits(), w_spin=GLIDE_W_SPIN, spin_slow_deg=GLIDE_SPIN_SLOW_DEG)
        what = "step %d %s turn" % (i, label)
        # by the board's heading even when the line goes by the wheels: they under-read a turn (GLIDE_ODO_TURN)
        here = getattr(self.b, "raw", self.b.pose)
        last = here()[2]
        turn = wrap_pi(yaw - last)
        if abs(turn) <= math.radians(tol_deg):
            return
        # at ~180 the shorter way flips with a tenth of a degree of noise: chosen once, kept (what is left is counted)
        sign, left = math.copysign(1.0, turn), abs(turn)
        held: dict = {}
        prev_w, settled, t_prev, spun, asked = 0.0, 0, time.monotonic(), 0.0, 0.0
        while True:
            p, pts = here(), self.scan()
            now = time.monotonic()
            dt = max(0.05, min(0.3, now - t_prev))
            t_prev = now
            left -= sign * wrap_pi(p[2] - last)
            last = p[2]
            asked += abs(prev_w) * dt
            if asked > abs(turn) + math.radians(GLIDE_OVERTURN_DEG):
                # 0.5 rad/s asked is made ~0.8 of by the base: a turn of 90 deg asks for ~110 (6 Oct, 24 Nov)
                if asked > 1.6 * abs(turn) + math.radians(GLIDE_OVERTURN_DEG):
                    self._glide_set(v, 0.0, 0.0, what)
                    raise Stop("%s: asked for %.0f° and the board's heading says %.0f° of %.0f° -- stopped" % (
                        what, math.degrees(asked), math.degrees(abs(turn) - left), math.degrees(abs(turn))))
            if self._board_event():
                self._glide_set(v, 0.0, 0.0, what)
                raise Stop("%s: board event (bumper?)" % what)
            # the target as turn_command sees it: never more than 140 deg off, so its "shorter way" is ours
            aim = wrap_pi(p[2] + sign * max(math.radians(-140.0), min(left, math.radians(140.0))))
            cmd = rl.turn_command(p[2], aim, prev_w, dt, limits=limits, tol_deg=tol_deg)
            if cmd.done:
                self._glide_set(v, 0.0, 0.0, what)
                if prev_w == 0.0:
                    return
                prev_w = 0.0
                time.sleep(0.3)                          # let it coast, then look again
                settled += 1
                if settled > 2:
                    return
                continue
            ahead = math.copysign(min(abs(left), math.radians(GLIDE_SWEEP_DEG)), cmd.err)
            gap = rl.turn_sweep_gap(pts, ahead)
            if gap < TURN_SWEEP_MIN_M:
                self._glide_set(v, 0.0, 0.0, what)
                prev_w = 0.0
                self._glide_held(held, i, p, "no room to turn %.0f° (the arms would pass %.2f m from something)" % (
                    math.degrees(cmd.err), gap))
                time.sleep(0.1)
                continue
            self._glide_free(held, i)
            spun += dt
            if spun > 25.0:
                self._glide_set(v, 0.0, 0.0, what)
                raise Stop("%s: 25 s and still %.0f° off" % (what, math.degrees(cmd.err)))
            prev_w = 0.0 if self._glide_set(v, 0.0, cmd.omega, what) else cmd.omega
            time.sleep(0.05)

    def _board_event(self) -> bool:
        return any(isinstance(e, dict) and e.get("type") in STOP_EVENTS for e in self.b.events())

    def _drop_events(self, why: str) -> None:
        """Read off what the board said before a drive of our own starts. Its
        events queue up until read (_back_in has always dropped them first --
        "drop what came before" -- and drive_route before every push), and
        nothing reads them during a MoveTo leg: a PATH_OCCUPIED from someone
        crossing the reception on the way to the door was the creep's first
        tick's "board event (bumper?)" (review 29 ต.ค.). Said, not lost."""
        old = sorted({e.get("type") for e in self.b.events() if isinstance(e, dict) and e.get("type") in STOP_EVENTS})
        if old:
            self.say("%s: the board had said %s before this drive -- not about it" % (why, ", ".join(old)))

    def map_fit(self) -> "MapFit | None":
        """The board's own map for the lidar arbiter of pose jumps, read once a
        run. None when it cannot be read: jumps are then eased in on the
        board's word (with the map fixed, every jump since was a correction)."""
        if self._map_fit is False:
            try:
                self._map_fit = MapFit(self.b.explore_map())
            except Exception as e:                       # noqa: BLE001
                self.say("(no map for the lidar check of pose jumps: %s -- trusting the board)" % type(e).__name__)
                self._map_fit = None
        return self._map_fit

    # -- dock ---------------------------------------------------------------
    def undock(self) -> None:
        if self.b.power().get("dockingStatus") != "on_dock":
            self.say("undock: not on the dock -- nothing to do")
            return
        self.drive_out("undock")

    def drive_out(self, why: str, out_m: float = PREDOCK_M) -> None:
        """Straight out along the dock's line to the pre-dock spot, our own way
        (velocity). Off the dock, and between two tries at docking: 28 ต.ค. the
        board's MoveTo from beside the dock ended after 11 cm. The lidar watches
        the strip ahead (the owner moved the dock once to where a table stood
        in front of it)."""
        from app import robot_lanes as rl
        from app.slamware_sdk import VelocitySession

        dx, dy, dyaw = dock_pose(self.doc)
        ux, uy = math.cos(dyaw), math.sin(dyaw)
        self._drop_events(why)
        with VelocitySession(self._sdk()) as v:
            vx, t0 = 0.0, time.monotonic()
            while True:
                x, y, _ = self.b.pose()
                out = (x - dx) * ux + (y - dy) * uy
                if out >= out_m:
                    break
                front = rl.front_distance(self.scan(), half_width=CREEP_HALF_WIDTH, margin=0.0, max_range=1.5)
                if front is not None and front - rl.ROBOT_BASE_LENGTH / 2 < OUT_FRONT_STOP_M:
                    v.set(0.0, 0.0)
                    raise Stop("%s: something %.2f m ahead" % (why, front - rl.ROBOT_BASE_LENGTH / 2))
                if any(isinstance(e, dict) and e.get("type") in STOP_EVENTS for e in self.b.events()):
                    v.set(0.0, 0.0)
                    raise Stop("%s: board event" % why)
                # 20 s for the pre-dock spot's 0.45 m; further out (`back_from_m`), its distance more
                limit = 20.0 + 1.5 * (out_m - PREDOCK_M) / UNDOCK_SPEED
                if time.monotonic() - t0 > limit:
                    v.set(0.0, 0.0)
                    raise Stop("%s: %.0f s and not out" % (why, limit))
                # ease off over the last 10 cm: the pre-dock spot is where it turns
                target = UNDOCK_SPEED if out_m - out > 0.10 else 0.06
                vx = min(target, vx + 0.03) if target > vx else target
                if v.set(vx, 0.0) and _taken(v):
                    # B / the robot's own screen: the session took the action up again and she
                    # drove on (review 16 Nov -- on a second try that is 1.9 m out and 1.9 m back)
                    v.set(0.0, 0.0)
                    raise Stop("%s: %s" % (why, TAKEN_AWAY))
                time.sleep(0.1)
            v.set(0.0, 0.0)
        x, y, h = self.b.pose()
        self.say("%s: out at (%.2f, %.2f) %.0f°, %s" % (why, x, y, math.degrees(h), self.b.power().get("dockingStatus")))

    def forward(self, step: dict, i: int) -> None:
        """`{"forward": m}`: straight ahead `m` metres from where it stands (FORWARD_* above),
        off the dock too (the drive out is this same straight line). Distance is the pose's
        progress along the heading it started on; the lidar strip ahead holds it for whoever
        stands there (for the route's `patience_s`, else STUCK_S), and a pose jump, a board
        event, a stall or the time it may take stop it where it is."""
        from app import robot_lanes as rl
        from app.slamware_sdk import VelocitySession

        want = min(float(step["forward"]), FORWARD_MAX_M)
        label = step.get("label") or "forward"
        if want <= 0.0:
            return
        vmax = min(float(step.get("speed") or FORWARD_SPEED), FORWARD_SPEED_MAX)
        right_clear = max(0.0, float(step.get("right_clear_m") or 0.0))
        # `"short_ok": false` (a walk out of the box, owner 23 Nov): no "near enough" -- something ahead within
        # FORWARD_SHORT_OK_M of the end holds it as anywhere else (review 23 Nov: 0.2 m short counted as out)
        short_ok = step.get("short_ok") is not False
        meet = (None if step.get("meet_m") is None
                else min(FORWARD_MEET_MAX_M, max(FORWARD_HOLD_M, float(step["meet_m"]))))
        patience = max(STUCK_S, self.patience_s)
        self._drop_events("forward")
        x0, y0, h0 = self.b.pose()
        # where it set out from, for the way back (`back_to`, the staff page's "กลับแท่น"): off the
        # dock = that line leads onto it
        try:
            from_dock = self.b.power().get("dockingStatus") == "on_dock"
        except Exception:                                # noqa: BLE001 -- unknown: no new way back from here
            from_dock = None

        def line_note(end: bool = False) -> None:
            # `end`: the step's closing line -- the one a walk out of the box is measured by (event_walk.walked_m;
            # second review 23 Nov: the line before the wheels, from == to, read as "walked 0.0 m")
            # 24 Nov: `to` is where the WHEELS put her, in the start's frame (`LineMeter.end_pose`), so walked_m =
            # (to - from) along from's heading is the odometry's distance, whatever the board's pose did on the way
            # (23 Nov 22:26: a 0.19 m relocalization at 0.24 m); the board's own end pose rides along as `board`
            try:
                xb, yb, hb = self.b.pose()
            except Exception:                            # noqa: BLE001 -- the stop matters more
                return
            xe, ye, he = meter.end_pose() if end else (xb, yb, hb)
            extra = {"end": True, "by": meter.by, "jumps": meter.jumps,
                     "board": [round(xb, 4), round(yb, 4), round(math.degrees(hb), 2)]} if end else {}
            self.event(forward_line=dict({"from": [round(x0, 4), round(y0, 4), round(math.degrees(h0), 2)],
                                          "to": [round(xe, 4), round(ye, 4), round(math.degrees(he), 2)],
                                          "on_dock": from_dock}, **extra))
            if end:
                off = math.hypot(xb - xe, yb - ye)
                if off > LINE_DISAGREE_M:
                    self.say("step %d %s: the board's pose ends %.2f m from where the wheels put her (%d jump(s))"
                             % (i, label, off, meter.jumps))
                    self.event(pose_disagree=i, m=round(off, 3), jumps=meter.jumps)

        meter = LineMeter((x0, y0, h0), self.odo())
        jumped = False
        line_note()                                      # the dock pose, before a wheel turns
        done, vx = 0.0, 0.0
        asked = 0.0                                      # metres asked for (speed x time), not the pose's
        moving_s = 0.0                                   # the time cap's clock: held time is patience's
        t0 = t_last = time.monotonic()
        held_since: float | None = None
        held_said = False
        still_since: float | None = None
        ended = "done"
        try:
            with VelocitySession(self._sdk()) as v:
                def halt(why: str, kind: type = Stop) -> Stop:
                    v.set(0.0, 0.0)
                    return kind("step %d %s: %s (%.2f of %.2f m)" % (i, label, why, done, want))

                while True:
                    p = self.b.pose()
                    now = time.monotonic()
                    dt, t_last = now - t_last, now
                    asked += vx * dt                         # what was being asked since the last read
                    if held_since is None:
                        moving_s += dt
                    # the distance is the wheels' (LineMeter; FORWARD_ASIDE_STOP_M above): a relocalization is said,
                    # not stopped for -- unless there is no odometry and it is past POSE_LOST_M (the wrong map)
                    moved, jump = meter.update(p, self.odo() if meter.by == "odometry" else None, vx, dt, vmax)
                    for note in meter.pop_notes():
                        self.say("step %d %s: %s" % (i, label, note))
                    if jump is not None:
                        if meter.by == "pose" and jump > POSE_LOST_M:
                            raise halt("the board's pose jumped %.2f m in %.1f s -- stopped where it is"
                                       % (jump, dt), _Jumped)
                        self.say("step %d %s: the board's pose jumped %.2f m in %.1f s -- a relocalization, on by "
                                 "the %s" % (i, label, jump, dt, "wheels" if meter.by == "odometry" else "speed asked"))
                        self.event(pose_jump=i, m=round(jump, 3), by=meter.by)
                    done = meter.ahead
                    if abs(meter.turned) > math.radians(FORWARD_JUMP_DEG) or abs(meter.aside) > FORWARD_ASIDE_STOP_M:
                        raise halt("off its own line by the %s: %.2f m aside, %.0f° turned" % (
                            meter.by, meter.aside, math.degrees(meter.turned)))
                    left = want - done
                    if left <= FORWARD_END_TOL_M:
                        break
                    if asked > want + max(FORWARD_ASKED_OVER_M, FORWARD_ASKED_OVER_FRAC * want):
                        if left <= FORWARD_NEAR_DONE_M:
                            vx = 0.0
                            v.set(0.0, 0.0)
                            ended = "%.2f m short, asked for %.2f m: the last creep, near enough" % (left, asked)
                            self.event(near_done=i, left_m=round(left, 3), asked_m=round(asked, 3))
                            break
                        raise halt("asked for %.2f m and the pose says %.2f -- not driving on a pose that slides"
                                   % (asked, done))
                    if any(isinstance(e, dict) and e.get("type") in STOP_EVENTS for e in self.b.events()):
                        raise halt("board event")
                    if moving_s > want / vmax * 2.0 + 10.0:
                        raise halt("%.0f s of driving and not there" % moving_s)
                    pts = self.scan()
                    front = rl.front_distance(pts, half_width=CREEP_HALF_WIDTH, max_range=2.5)
                    gap = None if front is None else front - rl.ROBOT_BASE_LENGTH / 2
                    if meet is not None and gap is not None and gap < meet:
                        vx = 0.0
                        v.set(0.0, 0.0)
                        ended = "met someone %.2f m ahead after %.2f m" % (gap, done)
                        # for app/event_walk.py: card 1's walk up to a guest gives the hand only to someone it met
                        # (owner 23 Nov: guest spots not marked = card 3's walk-up; nobody there = no hand)
                        self.event(met=i, gap_m=round(float(gap), 3), after_m=round(float(done), 3))
                        break
                    ahead = gap is not None and gap < (FORWARD_GO_M if held_since is not None else FORWARD_HOLD_M)
                    # the arm's side: a held walk waits for a little more room than it stopped for
                    side = right_side_hit(pts, right_clear + (0.05 if held_since is not None else 0.0),
                                          rl.ROBOT_BASE_LENGTH / 2 + FORWARD_HOLD_M)
                    if ahead or side:
                        vx, still_since = 0.0, None
                        v.set(0.0, 0.0)
                        if left <= FORWARD_SHORT_OK_M and not side and short_ok:
                            ended = "%.2f m short, something %.2f m ahead" % (left, gap)
                            break
                        if held_since is None:
                            held_since = now
                        if not held_said and now - held_since > HELD_NOTE_S:
                            held_said = True
                            self.event(held=i, at=[round(p[0], 2), round(p[1], 2)])
                        if now - held_since > patience:
                            raise halt("something %s for %.0f s" % (
                                "%.2f m ahead" % gap if ahead else "on the right, where the arm is", now - held_since))
                        time.sleep(0.1)
                        continue
                    if held_since is not None:
                        if held_said:
                            self.event(moving=i)
                        held_since, held_said = None, False
                    if vx >= FORWARD_STALL_MPS and moved < FORWARD_STALL_M:
                        still_since = now if still_since is None else still_since
                        if now - still_since > FORWARD_STALL_S:
                            raise halt("asked to go and not moving for %.1f s (caught on something below the lidar?)"
                                       % (now - still_since))
                    else:
                        still_since = None
                    # ease off over the last 15 cm: a stop where it was asked, not past it
                    target = vmax if left > 0.15 else 0.06
                    vx = min(target, vx + 0.03) if target > vx else target
                    if v.set(vx, 0.0):
                        # The action had to be taken up again: the board answered operation_fail (B or
                        # the robot's own screen took it away -- they DELETE the board's action, no
                        # signal reaches us) or a gap past the watchdog. Either way a person or the
                        # board stopped it; driving on is what VelocitySession would do (review 10 Nov).
                        vx = 0.0
                        raise halt("the board's velocity action was taken away (B / the robot's screen, "
                                   "or a gap past its watchdog) -- stopped")
                    time.sleep(0.1)
                v.set(0.0, 0.0)
        except _Jumped:
            jumped = True
            raise
        finally:
            # however the step ends -- done, a halt, the red stop (SIGTERM = KeyboardInterrupt), a
            # scan that failed, a lost board: the line it is on now (review 11 Nov: only done and the
            # halts said it). After a pose jump its two ends are in two frames: no way back along it.
            if jumped:
                self.event(line_lost=i)
            else:
                line_note(end=True)
        x, y, h = self.b.pose()
        self.say("step %d %s: forward %.2f m: %s in %.0f s, at (%.2f, %.2f) %.0f°; by the %s %.3f m, %d jump(s)" % (
            i, label, want, ended, time.monotonic() - t0, x, y, math.degrees(h), meter.by, meter.ahead, meter.jumps))
        # a `back_to` later in THIS run (the dock relearn: forward then back_to) backs by the wheels from the dock
        # itself, not from a board pose that may have flipped along the line since (`_OdoGuard` anchor)
        self._out_line = ((x0, y0, h0), meter.o0) if from_dock and meter.by == "odometry" else None

    def back_to(self, step: dict, i: int) -> None:
        """`{"back_to": [x, y, yaw_deg]}`: back onto the dock along the line a `forward` came out
        on (owner 11 Nov, the debut's metre off the floor-2 dock, the board holding floor 1's map:
        "ขอปุ่มกลับแท่น"). [x, y, yaw] is where that forward set out, on the dock, facing out. No
        map, no MoveTo, no turn: the dock's own back-in (`_back_in`: velocity, the heading held,
        on_dock / a stall / a bumper / 5° off the line / past the dock / 40 s) and `_stays_docked`,
        in the pose's own frame -- the frame the forward measured itself in. So it moves only from
        ON that line (BACK_TO_*), and a pose jump on the way stops it (`_JumpGuard`)."""
        dx, dy, dyaw_deg = (float(v) for v in step["back_to"])
        dyaw = math.radians(dyaw_deg)
        label = step.get("label") or "back to the dock"
        if self.b.power().get("dockingStatus") == "on_dock":
            self.say("step %d %s: already on the dock" % (i, label))
            self.event(docked=i)
            return
        # 24 Nov: by the wheels from the first read (`_OdoGuard`; 23 Nov 22:26:49 "jumped 0.16 m while backing"). A
        # forward of this run that set out on THIS dock (the relearn: forward then back_to) anchors it at the dock
        # itself -- the start check then reads how far the wheels took her, not a board pose that may have flipped
        out_line = getattr(self, "_out_line", None)
        anchor = None
        if out_line is not None:
            (fx, fy, fh), o0 = out_line
            if math.hypot(fx - dx, fy - dy) <= BACK_TO_LINE_M * 2 and abs(math.degrees(wrap_pi(fh - dyaw))) <= BACK_TO_YAW_DEG:
                anchor = ((dx, dy, dyaw), o0)
        real, self.b = self.b, _OdoGuard(self.b, BACK_FAST_SPEED, self.odo, anchor)
        guard = self.b
        try:
            x, y, yaw = self.b.pose()
            ux, uy = math.cos(dyaw), math.sin(dyaw)
            along = (x - dx) * ux + (y - dy) * uy
            aside = (x - dx) * -uy + (y - dy) * ux
            turned = abs(math.degrees(wrap_pi(yaw - dyaw)))
            if abs(aside) > BACK_TO_LINE_M or turned > BACK_TO_YAW_DEG or not 0.05 <= along <= BACK_TO_MAX_M:
                self.event(line_lost=i)                  # it does not describe where she is: gone
                raise Stop("step %d %s: not on the line it came out on (%.2f m out, %.2f m aside, %.1f° off) -- "
                           "not backing" % (i, label, along, aside, turned))
            self.say("step %d %s: backing %.2f m along %.1f° (by the %s%s)" % (
                i, label, along, dyaw_deg, guard.by, ", from this run's own line" if guard._o0 is not None
                and guard.anchor is not None and guard._a == guard.anchor[0] else ""))
            # the dock pose is where it sat on the contacts (measured, not estimated): 4 cm past it
            docked, reason, blocked = self._back_in(dx, dy, dyaw, OVERSHOOT_M, BACK_TO_STALL_FROM_M,
                                                    grace_s=BACK_STALL_GRACE_S)
            if docked:
                docked, reason = self._stays_docked(dx, dy, ux, uy, OVERSHOOT_M)
        except _Jumped:
            self.event(line_lost=i)                      # the frame moved under it: no way back along it
            raise
        finally:
            self.b = real
            for note in guard.notes:                     # what the board's pose did meanwhile: said, after
                self.say("step %d %s: %s" % (i, label, note))
            if guard.jumps:
                self.event(pose_jump=i, m=round(guard.jump_max, 3), by=guard.by, n=guard.jumps)
        x, y, yaw = self.b.pose()
        power = self.b.power()
        if not docked:
            raise Stop("step %d %s: not on the dock (%s%s), at (%.3f, %.3f) %.1f°" % (
                i, label, reason, ", blocked" if blocked else "", x, y, math.degrees(yaw)))
        self.say("step %d %s: ON THE DOCK at (%.3f, %.3f) %.1f°, charging=%s" % (
            i, label, x, y, math.degrees(yaw), power.get("isCharging")))
        self.event(docked=i)

    def scan(self):
        """Robot-frame lidar points (x forward, y left) over the SDK port. A
        frame under SCAN_MIN_PTS is read again (drive_route: "a short frame is
        not 'no room': read again"), and a second one is a Stop -- not driven,
        and not turned, on "nothing there"."""
        from app import robot_lanes as rl
        from app.slamware_sdk import laser_scan

        pts = rl.scan_points(laser_scan(self._sdk()))
        if len(pts) < SCAN_MIN_PTS:
            time.sleep(0.1)
            again = rl.scan_points(laser_scan(self._sdk()))
            if len(again) < SCAN_MIN_PTS:
                raise Stop("the lidar sent frames of %d and %d points (1,200-1,600 in one) -- not moving on that"
                           % (len(pts), len(again)))
            pts = again
        return pts

    def turn_to(self, yaw: float, tol_deg: float = 1.0, *, watch: bool = False, w_spin: float | None = None) -> None:
        from app.slamware_sdk import VelocitySession

        with VelocitySession(self._sdk()) as v:
            self._spot_turn(v, yaw, tol_deg, watch=watch, w_spin=w_spin)

    def _spot_turn(self, v, yaw: float, tol_deg: float, *, watch: bool = False, w_spin: float | None = None) -> None:
        """Turn in place inside an open velocity session -- after checking the
        arms' rectangle has room for this turn (robot_lanes.turn_sweep_gap).
        `watch` (a route's own turn step): a board event (the bumper) or the action taken away
        (B / the robot's screen) stops it there -- the check before it cannot see behind her
        (second review 22 Nov). `w_spin`: this turn's cruise rate (TURN_W_SPIN_*)."""
        import dataclasses

        from app import robot_lanes as rl

        limits = rl.DriveLimits()
        if w_spin is not None:
            limits = dataclasses.replace(limits, w_spin=min(TURN_W_SPIN_MAX, max(TURN_W_SPIN_MIN, float(w_spin))))
        if watch:
            self._drop_events("turn")
        turn = wrap_pi(yaw - self.b.pose()[2])
        if abs(turn) > math.radians(TURN_SPLIT_DEG):
            # From the pose as it IS (review 23 Nov: a turn planned from a modelled heading, after a MoveTo that
            # curved, can come out at ~180): past TURN_SPLIT_DEG two halves the same way round -- at ~180 the
            # shorter way flips with a tenth of a degree of noise and turn_command stops at every flip.
            self._spot_turn(v, wrap_pi(self.b.pose()[2] + turn / 2.0), tol_deg, watch=watch, w_spin=w_spin)
            turn = wrap_pi(yaw - self.b.pose()[2])
        gap = rl.turn_sweep_gap(self.scan(), turn)
        if gap < TURN_SWEEP_MIN_M:
            v.set(0.0, 0.0)
            raise Stop("no room to turn %.0f° here: the arms would pass %.2f m from something"
                       % (math.degrees(turn), gap))
        t0, prev_w, settled = time.monotonic(), 0.0, 0
        while True:
            if watch and any(isinstance(e, dict) and e.get("type") in STOP_EVENTS for e in self.b.events()):
                v.set(0.0, 0.0)
                raise Stop("turn: board event (bumper?) -- stopped where it is")
            cmd = rl.turn_command(self.b.pose()[2], yaw, prev_w, 0.1, limits=limits, tol_deg=tol_deg)
            if cmd.done:
                v.set(0.0, 0.0)
                if prev_w == 0.0:
                    break                                  # within tol from a standstill
                prev_w = 0.0
                time.sleep(0.5)                            # let it coast, then look again
                settled += 1
                if settled > 4:
                    break
                continue
            if time.monotonic() - t0 > 20:
                v.set(0.0, 0.0)
                raise Stop("turn: 20 s and still %.1f° off" % math.degrees(cmd.err))
            if v.set(0.0, cmd.omega) and watch:
                v.set(0.0, 0.0)
                raise Stop("turn: the board's velocity action was taken away (B / the robot's screen) -- stopped")
            prev_w = cmd.omega
            time.sleep(0.1)

    def dock(self) -> None:
        if self.events_on and self.b.power().get("dockingStatus") == "on_dock":
            # the staff page's "กลับแท่น" scene pressed with her already home: drove out
            # to the pre-dock spot and back in, in front of the guests (review 5 Nov)
            self.say("dock: already on the dock")
            return
        dx, dy, dyaw = dock_pose(self.doc)
        px, py, _ = pre_dock(self.doc)
        far = back_from_m(self.doc)                      # 0.0 = the pre-dock spot, as ever
        line = dock_line(self.doc)                       # == dyaw unless the file turns it
        if far:
            px, py = dx + far * math.cos(line), dy + far * math.sin(line)
        estimated = bool(self.doc["dock"].get("estimated"))
        overshoot = OVERSHOOT_ESTIMATED_M if estimated else OVERSHOOT_M
        if self.mapping_on:
            self.b.mapping(False)
            self.say("mapping off before docking")
        approach = self.doc["dock"].get("approach")
        if far and approach and math.hypot(float(approach[0]) - px, float(approach[1]) - py) < APPROACH_SAME_M:
            approach = None                              # the spot she reverses from is that place
        x, y, _ = self.b.pose()
        if approach and math.hypot(x - px, y - py) > APPROACH_SKIP_M:
            # 28 ต.ค.: sent to the dock from the middle of the reception, the board
            # planned its own way past the table at (-7.5, -4.2) and an arm hit it
            # (owner: "ชิดโต๊ะมากเกินไปแขนชน ... อย่าให้เกิดแบบนี้อีก") -- every way
            # in comes along the corridor every good dock came along
            self.b.set_speed(0.35)
            self.leg({"to": approach, "label": "the way to the dock"}, 0, through=True)
        self.b.set_speed(0.25)
        self.leg({"to": [px, py], "face": math.degrees(dyaw),
                  "label": "%.2f m out on the dock's line, facing out" % far if far else "pre-dock spot, facing out"}, 0)
        ux, uy = math.cos(line), math.sin(line)
        nx, ny = -uy, ux
        if far:
            self._face_out(dyaw)
        square_tol = SQUARE_TOL_LONG_M if far else SQUARE_TOL_M         # SQUARE_TOL_M above
        self._came_again = False                         # AGAIN_OUT_M: once a way home
        self._not_come_again = None                      # ... and a leg of it the board gave up on
        self._line_up(dx, dy, dyaw, line, far, (px, py), square_tol)
        docked, reason = False, None
        for attempt in range(1, DOCK_TRIES + 1):
            if attempt > 1:
                # 28 ต.ค. run 4: met the dock 1.9° crooked, wheels stopped 1.3 cm short,
                # no contact -- out along the line, square up, back in again
                self.say("dock: not on it (%s) -- out and in once more" % reason)
                if far:
                    self.drive_out("dock again", far)
                else:
                    self.drive_out("dock again")
                # a try's own come-again only: try 1's leg that ended short is not why try 2's drive out ended
                # aside (re-review 21 Nov: the phone said "in the way" for a dock that was met crooked)
                self._not_come_again = None
                self._line_up(dx, dy, dyaw, line, far, (px, py), square_tol)
            x, y, yaw = self.b.pose()
            if abs(wrap_pi(yaw - dyaw)) > math.radians(1.0):
                self.turn_to(dyaw)
            if far:
                self._ready_to_reverse(dx, dy, dyaw, line, far)
                docked, reason, blocked = self._back_home(dx, dy, dyaw, overshoot, line)
            elif line != dyaw:
                docked, reason, blocked = self._back_in(dx, dy, dyaw, overshoot, line=line)
            else:
                docked, reason, blocked = self._back_in(dx, dy, dyaw, overshoot)
            if docked:
                docked, reason = self._stays_docked(dx, dy, ux, uy, overshoot)
                if reason == TAKEN_AWAY:
                    blocked = True                       # a person's stop while seating: no second try
            if docked:
                break
            if blocked:
                # something behind it (or the bumper), not the dock: the next try
                # drove out and reversed into the same thing again (review 29 ต.ค.)
                reason += " -- not tried again"
                break
        x, y, yaw = self.b.pose()
        power = self.b.power()
        out = (x - dx) * ux + (y - dy) * uy
        lat = (x - dx) * nx + (y - dy) * ny
        self.say("dock: %s at (%.3f, %.3f) %.1f°, %.3f m along / %.3f m aside of the dock pose; %s, charging=%s%s" % (
            "ON THE DOCK" if docked else "NOT docked (%s)" % reason, x, y, math.degrees(yaw), out, lat,
            power.get("dockingStatus"), power.get("isCharging"), "" if attempt == 1 else ", try %d" % attempt))
        if not docked:
            raise Stop("not on the dock: %s" % reason)
        if self.register:
            docks = self.b.homedocks()
            if docks:
                self.say("home dock already registered: %s" % docks)
            elif self.b.quality() < MIN_QUALITY:
                self.say("not registering the dock: localization quality %d" % self.b.quality())
            else:
                self.b.register_dock()
                time.sleep(1.5)
                self.say("home dock registered: %s" % self.b.homedocks())

    def _face_out(self, dyaw: float) -> None:
        """`back_from_m`: square to the dock's heading where the board's leg ended, and a moment
        for the board's pose (BACK_FROM_SETTLE_S) -- what she steps and reverses on is read after."""
        if abs(wrap_pi(self.b.pose()[2] - dyaw)) > math.radians(1.0):
            self.turn_to(dyaw)
        time.sleep(BACK_FROM_SETTLE_S)
        x, y, yaw = self.b.pose()
        self.say("dock: facing out at (%.2f, %.2f) %.1f°, Q%d" % (x, y, math.degrees(yaw), self.b.quality()))

    def _ready_to_reverse(self, dx: float, dy: float, dyaw: float, line: float, far: float) -> None:
        """`back_from_m`, before every long reverse (the first, and a second try's): the pose
        read after the last turn has settled must be ON the dock's line, facing out, about as
        far out as the file says (BACK_HOME_* above) -- or she does not reverse blind from
        there. A Stop: the short way's "backing in from here" is not for 1.9 m."""
        time.sleep(BACK_HOME_SETTLE_S)
        x, y, yaw = self.b.pose()
        if abs(wrap_pi(yaw - dyaw)) > math.radians(1.5):
            # the turn coasted past (a base slow to answer: 3-4° in simulation) or the pose
            # settled elsewhere: once more, and what is read after THAT is what counts
            self.turn_to(dyaw)
            time.sleep(BACK_HOME_SETTLE_S)
            x, y, yaw = self.b.pose()
        q = self.b.quality()
        if q < self.min_quality:
            # review 21 Nov: the gate read the pose and never asked how sure the board was of it -- a
            # leg that ended at the spot with Q38 would have been reversed from, 1.9 m blind
            raise Stop("dock: localization quality %d < %d at %.2f m out -- not backing that far blind on that "
                       "pose" % (q, self.min_quality, far))
        ux, uy = math.cos(line), math.sin(line)
        out, lat = (x - dx) * ux + (y - dy) * uy, (x - dx) * -uy + (y - dy) * ux
        herr = wrap_pi(yaw - dyaw)
        if abs(lat) > BACK_HOME_START_ASIDE_M or abs(herr) > math.radians(BACK_HOME_START_YAW_DEG) \
                or abs(out - far) > BACK_HOME_OUT_BAND_M:
            if getattr(self, "_not_come_again", None):
                # the way out and back to the spot was not walked (something in it): that is the stop,
                # and the phone must say "in the way" -- not "look behind the robot" (NOT_COME_AGAIN above)
                raise Stop(NOT_COME_AGAIN % (self._not_come_again, lat, out))
            raise Stop("dock: not on the dock's line to reverse from (%.2f m out for %.2f, %.2f m aside, %.1f° off) "
                       "-- not backing that far blind from there" % (out, far, lat, math.degrees(herr)))

    def _back_home(self, dx: float, dy: float, dyaw: float, overshoot: float,
                   line: float | None = None) -> tuple[bool, str | None, bool]:
        """`back_from_m`: the long reverse onto the dock. `_back_in` with `back_to`'s two rules,
        for the same reason -- it backs further, blind: a stall counts from the first metre
        (`BACK_TO_STALL_FROM_M`: the wheels stopping anywhere out there is something behind her,
        "blocked", and not tried again), and a jump of the board's pose under the line is a Stop
        where she is (`_JumpGuard`). Square to the dock over the last BACK_STRAIGHT_M, and
        stopped when it is not coming onto the line (BACK_HOME_ASIDE_STOP_M)."""
        real, self.b = self.b, _JumpGuard(self.b, BACK_FAST_SPEED)
        try:
            return self._back_in(dx, dy, dyaw, overshoot, BACK_TO_STALL_FROM_M, BACK_STRAIGHT_M, line,
                                 BACK_HOME_LEAN_DEG, grace_s=BACK_STALL_GRACE_S, aside_stop_m=BACK_HOME_ASIDE_STOP_M)
        finally:
            self.b = real

    def _line_up(self, dx: float, dy: float, dyaw: float, line: float, far: float, spot: tuple[float, float],
                 tol: float) -> None:
        """At the spot she reverses from, before every reverse (the first and a second try's): the long
        reverse comes to the spot again once a way home (`_come_again`, AGAIN_OUT_M); then whatever is
        still past `tol` is stepped onto the line (`_square_up`) -- the short reverse's only way, as ever."""
        if far and AGAIN_OUT_M > 0.0 and not getattr(self, "_came_again", False):
            self._come_again(dx, dy, dyaw, line, far, spot, tol)
        self._square_up(dx, dy, dyaw, line, tol)

    def _come_again(self, dx: float, dy: float, dyaw: float, line: float, far: float,
                    spot: tuple[float, float], tol: float) -> None:
        """Past `tol` aside of the dock's line before the long reverse: out AGAIN_OUT_M further along the
        line and the board's leg to `spot` once more (AGAIN_OUT_M above). Says what it did; a leg that
        does not get there is said and left where it left her (the step and the start gate read that)."""
        ux, uy = math.cos(line), math.sin(line)
        x, y, _h = self.b.pose()
        lat = (x - dx) * -uy + (y - dy) * ux             # + = left of the line, facing out
        if abs(lat) <= tol:
            return
        self._came_again = True
        out = far + AGAIN_OUT_M
        self.say("dock: %.3f m aside of the dock's line -- out to %.2f m on it and coming to it again" % (lat, out))
        try:
            self.b.set_speed(0.25)
            self.leg({"to": [dx + out * ux, dy + out * uy], "label": "%.2f m out on the dock's line, to come again" % out}, 0)
            self.leg({"to": [float(spot[0]), float(spot[1])], "face": math.degrees(dyaw),
                      "label": "%.2f m out on the dock's line, facing out (again)" % far}, 0)
        except Stop as e:
            # Only the board ending a leg short of its point (CANNOT_REACH_TARGET, somebody stood there
            # and it gave up) is carried on from: she is where it left her and the step and the start
            # gate read that -- and if the gate will not reverse from there it says the way was not
            # walked (NOT_COME_AGAIN). Everything else is the dock's stop, as the first leg's is (review
            # 21 Nov: a "localization quality 38" at the spot was swallowed here, and the gate, which
            # read no quality, let her back 1.9 m blind on that pose; a guest holding the leg through
            # its patience was swallowed too).
            short = LEG_NOT_THERE.search(str(e))
            if not short:
                raise
            self._not_come_again = float(short.group(1))
            self.say("dock: did not come again (%s)" % e)
            return
        self._face_out(dyaw)
        x, y, _h = self.b.pose()
        self.say("dock: now %.3f m aside of the dock's line" % ((x - dx) * -uy + (y - dy) * ux))

    def _square_up(self, dx: float, dy: float, dyaw: float, line: float | None = None,
                   tol: float = SQUARE_TOL_M) -> None:
        """At the pre-dock spot: onto the dock's line when the leg ended aside of it
        (SQUARE_TOL_M above). Says what it did; a Stop is something in the way or a person.
        `line`: the line's direction when it is not the dock's heading (`dock_line`) -- "aside"
        is of that line; the heading she turns to is still read off the dock's own. `tol`: within
        this aside, no step (SQUARE_TOL_M; the long reverse's own start bar for that reverse)."""
        ux, uy = (math.cos(dyaw), math.sin(dyaw)) if line is None else (math.cos(line), math.sin(line))
        nx, ny = -uy, ux
        for _ in range(SQUARE_TRIES):
            x, y, _h = self.b.pose()
            lat = (x - dx) * nx + (y - dy) * ny          # + = left of the line, facing out
            if abs(lat) <= tol:
                return
            if abs(lat) > SQUARE_MAX_M:
                self.say("dock: %.2f m aside of the dock's line -- too far for a step, backing in from here" % lat)
                return
            face = wrap_pi(dyaw - math.copysign(math.pi / 2, lat))
            self.say("dock: %.3f m aside of the dock's line -- stepping onto it" % lat)
            try:
                self.turn_to(face, tol_deg=2.0)
            except Stop as e:
                # no room for the arms to turn (or the turn did not finish): before this step
                # existed she reversed from here and charged, aside -- still better than a night
                # off the charger (review 16 Nov). dock() turns her back onto the dock's heading.
                self.say("dock: no step onto the line (%s) -- backing in from here" % e)
                return
            # the offset AFTER the turn is the one to step (review 21 Nov, the n20j trace: +5.1 cm, -7.1
            # after the -89 deg turn, a 5.1 cm step made it -12.8; the 180 deg turn left -2.4 -- inside the
            # bar -- and a 12.8 cm step made it +10.5: the turns move the board's pose, and the step
            # was the distance read before them)
            x, y, _h = self.b.pose()
            lat = (x - dx) * nx + (y - dy) * ny
            if abs(lat) <= tol:
                self.say("dock: %.3f m aside after the turn -- inside the bar, no step" % lat)
                return
            gain = math.cos(face) * nx + math.sin(face) * ny     # aside per metre ahead (about -sign(lat))
            dist = -lat / gain if abs(gain) > 0.5 else 0.0
            if dist <= 0.01:
                continue                                 # the turn put her past the line: the next try turns back
            if dist > SQUARE_MAX_M:
                self.say("dock: %.2f m aside after the turn -- too far for a step, backing in from here" % lat)
                return
            if not self._step_ahead(dist, face):
                return
        x, y, _h = self.b.pose()
        self.say("dock: now %.3f m aside of the dock's line" % ((x - dx) * nx + (y - dy) * ny))

    def _step_ahead(self, dist: float, heading: float) -> bool:
        """Straight ahead `dist` along `heading`, slowly (drive_out's checks: the strip ahead,
        the board's events, a person taking the base). False = not made (something ahead, or
        the pose did not get there in the time the distance takes): no second step, the
        reverse goes from where she is. A board event or a person's stop is a Stop."""
        from app import robot_lanes as rl
        from app.slamware_sdk import VelocitySession

        ux, uy = math.cos(heading), math.sin(heading)
        self._drop_events("dock step")
        x0, y0, _h = self.b.pose()
        t0 = time.monotonic()
        max_s = 2.0 * dist / SQUARE_SPEED + SQUARE_SLACK_S
        made = True
        with VelocitySession(self._sdk()) as v:
            while True:
                x, y, _h = self.b.pose()
                done = (x - x0) * ux + (y - y0) * uy
                if done >= dist - 0.01:
                    break
                front = rl.front_distance(self.scan(), half_width=CREEP_HALF_WIDTH, margin=0.0, max_range=1.5)
                if front is not None and front - rl.ROBOT_BASE_LENGTH / 2 < OUT_FRONT_STOP_M + (dist - done):
                    # it would end closer than OUT_FRONT_STOP_M to it: not a reason to leave
                    # her off the charger -- no step, the reverse goes from here
                    self.say("dock step: something %.2f m ahead -- backing in from here"
                             % (front - rl.ROBOT_BASE_LENGTH / 2))
                    made = False
                    break
                if any(isinstance(e, dict) and e.get("type") in STOP_EVENTS for e in self.b.events()):
                    v.set(0.0, 0.0)
                    raise Stop("dock step: board event")
                if time.monotonic() - t0 > max_s:
                    self.say("dock step: %.0f s and the pose is not there -- backing in from here" % max_s)
                    made = False
                    break
                if v.set(SQUARE_SPEED, 0.0) and _taken(v):
                    v.set(0.0, 0.0)
                    raise Stop("dock step: " + TAKEN_AWAY)
                time.sleep(0.1)
            v.set(0.0, 0.0)
        return made

    def _back_in(self, dx: float, dy: float, dyaw: float, overshoot: float,
                 stall_from: float = STALL_FROM_M, straight_m: float = 0.0,
                 line: float | None = None, lean_deg: float = 2.0, grace_s: float = 0.0,
                 aside_stop_m: float = 0.0, seek_m: float | None = None) -> tuple[bool, str | None, bool]:
        """Reverse straight onto the dock from the pre-dock spot (see the module
        docstring for the stops). Returns (on the contacts, why not, blocked):
        blocked = the bumper, or the wheels stopped further out than the
        dock's last BACK_SLOW_M -- something the lidar cannot see behind, not
        the dock, and no reason for another try (dock()) -- or the velocity
        action taken away (B / the robot's own screen: a person stopped it;
        review 11 Nov -- VelocitySession took it up again and the reverse went on).
        `stall_from`: backed this far before a stall counts (`back_to`: almost
        at once -- it backs further, blind)."""
        from app.slamware_sdk import VelocitySession

        # `line` (dock_line): along and aside are of the line she rolls on; the heading held
        # (herr) is the dock's own, as the board reads it
        ux, uy = (math.cos(dyaw), math.sin(dyaw)) if line is None else (math.cos(line), math.sin(line))
        nx, ny = -uy, ux
        self.b.events()                                    # drop what came before
        self._taken = False                                # `_seat` sets it: no reverse after a person's stop
        x0, y0, _ = self.b.pose()
        # 40 s is the pre-dock spot's 0.45 m several times over. From further out (`back_to`,
        # `back_from_m`) the distance at BACK_FAST_SPEED, half as much again, and the slow end:
        # 16 Nov, 1.95 m took ~35 s of the 40 -- 2.5 m (BACK_TO_MAX_M) could not have made it.
        max_s = max(40.0, 15.0 + 1.5 * ((x0 - dx) * ux + (y0 - dy) * uy) / BACK_FAST_SPEED)
        recent: list[tuple[float, float, float]] = []
        docked, reason, blocked, vx, since_pulse = False, None, False, 0.0, 0.0
        asked_since: float | None = None                   # the wheels first asked to turn (BACK_STALL_GRACE_S)
        seeking: float | None = None                       # past the overshoot, on for the contacts (DOCK_SEEK_*)
        sought, prev = 0.0, None                           # metres ASKED since; the last tick (t, x, y, yaw)
        self._contact_past = 0.0                           # how far past the dock pose the contacts were met
        t0 = last_power = time.monotonic()
        with VelocitySession(self._sdk()) as v:
            while True:
                tick = time.monotonic()
                x, y, yaw = self.b.pose()
                out = (x - dx) * ux + (y - dy) * uy
                lat = (x - dx) * nx + (y - dy) * ny
                herr = wrap_pi(yaw - dyaw)
                if seeking is not None and prev is not None:
                    sought += abs(vx) * (tick - prev[0])
                if out < 0.15 or tick - last_power >= 0.3:
                    last_power = tick
                    if self.b.power().get("dockingStatus") == "on_dock":
                        docked = True
                        self._contact_past = max(0.0, -out)     # `_stays_docked` may back on to there
                        self._seat(v, x, y)
                        break
                backed = math.hypot(x - x0, y - y0)
                recent = [q for q in recent if tick - q[0] <= STALL_NET_S + 0.3] + [(tick, x, y)]
                stopped = wheels_stopped(recent, tick)
                started = grace_s <= 0.0 or (asked_since is not None and tick - asked_since >= grace_s)
                jump = None if seeking is None or prev is None else (
                    math.hypot(x - prev[1], y - prev[2]) - BACK_FAST_SPEED * (tick - prev[0]),
                    abs(wrap_pi(yaw - prev[3])))
                prev = (tick, x, y, yaw)
                no_seek = None                                 # why she does not go on past the overshoot
                if out < -overshoot and seeking is None:
                    if seek_m is None:
                        seek_m = DOCK_SEEK_M
                    if seek_m <= 0.0:
                        no_seek = ""
                    elif getattr(self, "_dock_stalled", False):
                        no_seek = " (the wheels stopped once this run: not on blind)"
                    elif abs(lat) > DOCK_SEEK_ASIDE_M or abs(herr) > math.radians(DOCK_SEEK_YAW_DEG):
                        no_seek = " (%.2f m aside / %.1f° off its line: not on blind)" % (lat, math.degrees(herr))
                if backed >= stall_from and started and stopped:
                    self._dock_stalled = True                  # no seek after this, this run (DOCK_SEEK_M)
                    if seeking is not None:
                        # past the dock pose: the dock's body with no power, or something behind -- not pushed on
                        reason, blocked = ("the wheels stopped %.2f m past the dock pose, %.2f m into looking for the "
                                           "contacts (no contact: a dock with no power, or something behind)"
                                           % (-out, sought)), True
                    else:
                        reason = "the wheels stopped after %.2f m (something behind, not the contacts)" % backed
                        # 28 ต.ค. run 4 (crooked, 1.3 cm short) is what DOCK_TRIES is for;
                        # stopped further out, the next try backs into the same thing
                        blocked = out > BACK_SLOW_M
                elif no_seek is not None:
                    reason = "%.2f m past the dock pose and no contact%s" % (-out, no_seek)
                elif seeking is not None and sought >= seek_m:
                    # further than a second try could go, and blind: not tried again
                    reason, blocked = ("%.2f m past the dock pose and no contact -- went on %.2f m looking for the "
                                       "contacts, no further blind" % (-out, sought)), True
                elif seeking is not None and (abs(lat) > DOCK_SEEK_ASIDE_M or abs(herr) > math.radians(DOCK_SEEK_YAW_DEG)):
                    reason = ("%.2f m past the dock pose and no contact, %.2f m aside / %.1f° off its line -- not on "
                              "blind" % (-out, lat, math.degrees(herr)))
                elif jump is not None and (jump[0] > FORWARD_JUMP_M or jump[1] > math.radians(FORWARD_JUMP_DEG)):
                    reason, blocked = ("the board's pose jumped %.2f m / %.0f° while looking for the contacts -- "
                                       "stopped where it is" % (jump[0], math.degrees(jump[1]))), True
                elif abs(herr) > math.radians(HEADING_GUARD_DEG):
                    reason = "heading %.1f° off the dock's line" % math.degrees(herr)
                elif aside_stop_m > 0.0 and abs(lat) > aside_stop_m:
                    # the long reverse is not coming onto its line: not the dock she is backing at
                    reason, blocked = "%.2f m aside of the dock's line %.2f m out, backing blind" % (lat, out), True
                elif any(isinstance(e, dict) and e.get("type") in STOP_EVENTS for e in self.b.events()):
                    reason, blocked = "board event (bumper?)", True
                elif tick - t0 > max_s:
                    reason = "%.0f s and not on the dock" % max_s
                if reason:
                    break
                if out < -overshoot and seeking is None:
                    # past the overshoot, on its line, never stalled: on for the contacts (DOCK_SEEK_M above)
                    seeking = tick
                    max_s = max(max_s, tick - t0 + DOCK_SEEK_S)
                    self.say("dock: %.2f m past the dock pose and no contact -- on, slowly, up to %.2f m more for "
                             "the contacts" % (-out, seek_m))
                target, want = back_in_command(out, lat, herr, straight_m, lean_deg)
                if seeking is not None:
                    target = max(target, -DOCK_SEEK_SPEED)
                vx = max(target, vx - 0.02)
                omega = 0.0
                since_pulse += 0.1
                if abs(want) > math.radians(BACK_PULSE_MIN_DEG) and since_pulse >= BACK_PULSE_WAIT_S:
                    omega, since_pulse = math.copysign(BACK_PULSE_W, want), 0.0
                renewed = v.set(vx, omega)
                if asked_since is None and vx != 0.0:
                    asked_since = tick
                if renewed:
                    if _taken(v):
                        v.set(0.0, 0.0)
                        reason, blocked = TAKEN_AWAY, True
                        break
                    vx = 0.0                             # a gap past the watchdog: ramp from standstill
                    asked_since = tick                   # ... and a start again for the stall's grace
                time.sleep(0.1)
        return docked, reason, blocked

    def _seat(self, v, xs: float, ys: float, extra: float = SEAT_M) -> None:
        """Keep reversing a little past the first contact. 28 ต.ค.: stopped the
        moment the board said on_dock, the robot read not_on_dock 1.5 s later
        and never charged -- the contacts touch before their springs are
        pressed, and the springs push a robot that stops there back off."""
        t0 = time.monotonic()
        while time.monotonic() - t0 < 1.5:
            x, y, _ = self.b.pose()
            if math.hypot(x - xs, y - ys) >= extra:
                break
            if v.set(-SEAT_SPEED, 0.0) and _taken(v):
                self._taken = True                       # a person stopped it: `_stays_docked` backs no more
                break
            time.sleep(0.1)
        v.set(0.0, 0.0)

    def _stays_docked(self, dx: float, dy: float, ux: float, uy: float, overshoot: float) -> tuple[bool, str | None]:
        """On the dock SETTLE_S after stopping, and charging -- or one more
        short reverse onto it (not past `overshoot` beyond the dock pose)."""
        from app.slamware_sdk import VelocitySession

        for attempt in (1, 2):
            time.sleep(SETTLE_S)
            power = self.b.power()
            if power.get("dockingStatus") == "on_dock":
                return True, None
            if attempt == 2:
                return False, "touched the contacts but did not stay on them (%s, charging %s)" % (
                    power.get("dockingStatus"), power.get("isCharging"))
            if getattr(self, "_taken", False):
                return False, TAKEN_AWAY                 # B while seating (review 11 Nov): no more reversing
            self.say("dock: off the contacts again after stopping -- reversing onto them once more")
            # contacts met further in by the seek (DOCK_SEEK_M): back to there and the overshoot past it, no further
            past = max(overshoot, getattr(self, "_contact_past", 0.0) + OVERSHOOT_M)
            with VelocitySession(self._sdk()) as v:
                t0 = time.monotonic()
                while time.monotonic() - t0 < 3.0:
                    x, y, _ = self.b.pose()
                    if (x - dx) * ux + (y - dy) * uy < -past:
                        break
                    if self.b.power().get("dockingStatus") == "on_dock":
                        self._seat(v, x, y)
                        break
                    if v.set(-SEAT_SPEED, 0.0) and _taken(v):
                        v.set(0.0, 0.0)
                        return False, TAKEN_AWAY
                    time.sleep(0.1)
                v.set(0.0, 0.0)
        return False, "not on the dock"

    # -- the room's big screen ----------------------------------------------
    def screen(self, action: str) -> None:
        """Open/close the room screen through the robot's own server (POST
        /room-screen, the hold menu's way; app/room_screens.py) -- owner 28 ต.ค.:
        "พอไปถึงหน้าห้องก็เปิดจอ แล้วก็เดินกลับแท่นแล้วก็ปิดจอ". A screen that
        does not answer is said and walked past: it is no reason to leave a
        robot standing in the reception."""
        import httpx

        try:
            token = ws_token(self.env_path)
            # the robot's own server on loopback, its LAN certificate (mkcert) is
            # not in this Python's store
            r = httpx.post(SERVER + "/room-screen", json={"action": action, "token": token},
                           verify=False, timeout=30)
            doc = r.json()
            self.say("screen %s: %s%s" % (action, "ok" if doc.get("ok") else "NOT ok",
                                          "" if doc.get("ok") else " (%s)" % (doc.get("error") or doc.get("reason"))))
        except Exception as e:                           # noqa: BLE001
            self.say("screen %s: could not ask the server (%s)" % (action, type(e).__name__))

    # -- run ----------------------------------------------------------------
    def warm_up(self) -> None:
        """--wait-go, before `ready`: what the first velocity step meets first, paid now -- the SDK's connection and
        its version call (`_sdk`), the imports of a walk ahead's first tick (robot_lanes, numpy, the velocity
        session's module), one lidar frame through the same code (too short = Stop here, before ready, as it would
        be at that first tick). Reads only: no velocity action is opened -- the board's watchdog ends one that is not
        fed (~1 s), and a session held through the wait would be a session driving nothing."""
        from app import robot_lanes as rl
        from app.slamware_sdk import VelocitySession  # noqa: F401 -- imported now, not on the first tick

        self._sdk()
        pts = self.scan()
        rl.front_distance(pts, half_width=CREEP_HALF_WIDTH, max_range=2.5)
        right_side_hit(pts, 0.05, 0.5)

    def keep_warm(self) -> None:
        """Once a GO_KEEP_WARM_S of the wait (`hold_for_go`): another action on the board = no walk (Stop, as the
        start gate); the SDK asked once -- a connection the board dropped is opened again now. A read that fails
        is said, not a stop: the walk's own first reads after the go are the judge."""
        try:
            busy = self.b.current() is not None
        except Exception as e:                           # noqa: BLE001
            self.say("(waiting for the go: the board's action could not be read: %s)" % type(e).__name__)
            busy = False
        if busy:
            raise Stop("the board is running another action")
        try:
            self._sdk().call("getsdpversion")
        except Exception as e:                           # noqa: BLE001
            self.say("(waiting for the go: the SDK did not answer (%s) -- connecting again)" % type(e).__name__)
            if self.sdk is not None:
                self.sdk.close()
            self.sdk = None
            self._sdk()

    def hold_for_go(self, q: int) -> None:
        """--wait-go: warm up, `@@ ready`, then wait for the go line (`GoLine`) for go_wait_s at most, keeping warm.
        Raises NoGo for any answer but "go" and for the time; SIGTERM ends it as KeyboardInterrupt (main)."""
        t0 = time.monotonic()
        self.warm_up()
        self.event(ready=True, q=q, warm_s=round(time.monotonic() - t0, 2))
        self.say("ready: waiting for the go (%.0f s at most)" % self.go_wait_s)
        deadline = time.monotonic() + self.go_wait_s
        while True:
            left = deadline - time.monotonic()
            if left <= 0.0:
                raise NoGo("no go in %.0f s" % self.go_wait_s)
            word = self.go_line.wait(min(GO_KEEP_WARM_S, left))
            if word is not None:
                break
            self.keep_warm()
        if word != "go":
            raise NoGo(word)
        self.say("go after %.1f s ready" % (time.monotonic() - t0))

    def start_fit(self) -> float:
        """The lidar on the board's map at the board's pose, median of a few frames:
        one frame with somebody across the laser is not a lost robot."""
        fit = MapFit(self.b.explore_map())
        scores = []
        for _ in range(START_FIT_FRAMES):
            scores.append(fit.score(self.b.pose(), self.scan()))
            time.sleep(0.15)
        return sorted(scores)[len(scores) // 2]

    def gate_on_map(self, where: str) -> None:
        """The map gates (Q, the lidar's fit) now, `where` said in the Stop."""
        q = self.b.quality()
        if q < self.min_quality:
            raise Stop("localization quality %d < %d %s -- set the pose first" % (q, self.min_quality, where))
        if self.min_fit > 0.0 and not self.mapping_on:
            fit = self.start_fit()
            if fit < self.min_fit:
                raise Stop("the lidar does not lie on this map at the board's pose (fit %.2f < %.2f, Q%d) %s"
                           % (fit, self.min_fit, q, where))

    def run(self, steps: list[dict], from_step: int) -> None:
        q = self.b.quality()
        # `after_box` steps (card 1's walk out and on to the corner in one walker, 25 Nov): not gated here, with her
        # still in the box (not on the board's map; the walk out itself never had these gates) -- at their first step
        on_map = needs_map([s for s in steps[max(0, from_step - 1):] if not s.get("after_box")]) or self.mapping_on
        if on_map and q < self.min_quality:
            raise Stop("localization quality %d < %d -- set the pose first" % (q, self.min_quality))
        if self.b.current() is not None:
            raise Stop("the board is running another action")
        if on_map and self.min_fit > 0.0 and not self.mapping_on:
            fit = self.start_fit()
            if fit < self.min_fit:
                raise Stop("the lidar does not lie on this map at the board's pose (fit %.2f < %.2f, Q%d): "
                           "the wrong map, or a stale pose -- set the pose first" % (fit, self.min_fit, q))
        try:
            x0, y0, _ = self.b.pose()
            at = [round(x0, 3), round(y0, 3)]
        except Exception:                                # noqa: BLE001 -- a staff hint, never a gate
            at = None
        if self.go_line is not None:
            # --wait-go: every gate above passed, now ready and waiting -- `started` only after the go, so a walker
            # that never had it never said it set out (event_box: the walk-out's arm comes back)
            self.hold_for_go(q)
        # `at`: where she set out. event_walk compares it with where she is held or stopped -- the
        # same spot = the wheels never turned (19 Nov: an E-stop left pressed, which the board does
        # not report; the phone read "someone in the way" for 45 s)
        self.event(started=True, q=q, at=at)
        for note in dock_key_notes(self.doc):
            self.say(note)
        if self.mapping_on:
            self.b.mapping(True)
            self.say("mapping on (the lidar rewrites what it sees on the way)")
        boxed_gate = False
        for i, step in enumerate(steps, 1):
            if i < from_step:
                continue
            if step.get("after_box") and not boxed_gate:
                boxed_gate = True
                if not self.mapping_on and needs_map([step]):
                    self.gate_on_map("after the walk out (step %d)" % i)
            nxt = steps[i] if i < len(steps) else None
            through = rolls_through(step, nxt)
            try:
                try:
                    self.step(step, i, through)
                except Stop as e:
                    if not step.get("fallback"):
                        raise
                    # 28 ต.ค.: the door front as one MoveTo, if the board takes the target;
                    # the three-part way that worked five times if it does not
                    self.say("step %d: %s -- the other way (%d steps)" % (i, e, len(step["fallback"])))
                    if self.b.current() is not None:
                        self.b.cancel()
                    for k, sub in enumerate(step["fallback"]):
                        self.step(sub, i, rolls_through(
                            sub, step["fallback"][k + 1] if k + 1 < len(step["fallback"]) else None))
            except Stop:
                # back1 (28-29 ต.ค.) stopped with no frame on record: where the robot
                # really was had to be read off a frame grabbed by hand afterwards
                if self.record_dir is not None:
                    try:
                        self.record(i, step, "stop")
                    except Exception as e2:              # noqa: BLE001 -- never hide the stop
                        self.say("(could not record the stop: %s)" % type(e2).__name__)
                raise
            self.event(step=i)
            if self.record_dir is not None and step.get("do") not in ("screen", "wait") and not through:
                self.record(i, step)

    def step(self, step: dict, i: int, through: bool = False) -> None:
        if step.get("do") == "undock":
            self.undock()
        elif step.get("do") == "dock":
            self.dock()
        elif step.get("do") == "screen":
            self.screen(step["action"])
        elif step.get("do") == "turn":
            # on the spot, after the lidar says the arms have room (_spot_turn) -- in FRONT and to the sides:
            # behind her nobody looks, so it is watched while it turns (a bumper, B) and may be slower
            self.turn_to(math.radians(float(step["face"])), tol_deg=2.0, watch=True,
                         w_spin=step.get("w_spin"))
            x, y, h = self.b.pose()
            self.say("step %d %s: facing %.0f° at (%.2f, %.2f)" % (i, step.get("label") or "turn", math.degrees(h), x, y))
        elif step.get("do") == "wait":
            self.say("wait %.0f s" % float(step["s"]))
            time.sleep(float(step["s"]))
        elif "glide" in step:
            self.glide(step, i)
        elif "creep" in step:
            self.creep(step, i)
        elif "forward" in step:
            self.forward(step, i)
        elif "back_to" in step:
            self.back_to(step, i)
        else:
            self.leg(step, i, through)

    def record(self, i: int, step: dict, tag: str = "") -> None:
        """--record: where the robot stopped, for measuring after the run -- the
        board's pose and quality, a lidar frame (REST document) and the power
        state. The board's own 'off by' is from its own pose; checking the frame
        against the map later says where the robot really was (28 ต.ค., owner:
        "ทำซ้ำสักห้ารอบดูเพื่อวัดความถูกต้องแม่นยำ")."""
        time.sleep(0.6)                                  # let the base settle
        doc = {"step": i, "spec": step, "t": round(time.monotonic() - self.t0, 1), "pose": list(self.b.pose()),
               "quality": self.b.quality(), "power": self.b.power(),
               "scan": self.b.get("/api/core/system/v1/laserscan")}
        name = "step_%02d%s.json" % (i, "_" + tag if tag else "")
        (Path(self.record_dir) / name).write_text(json.dumps(doc), encoding="utf-8")

def set_dock_pose(w: Walker) -> int:
    """`--set-dock-pose`: the robot stands on the dock (staff say so; the dock's
    contacts are not asked -- 4 Nov the dock had no power and read not_on_dock with
    the robot on it) and the board's pose is stale (4 Nov, carried to another
    floor: the old floor's pose, Q32). The lidar is the judge: written only when
    the scan lies on the map AT the dock pose, and read back."""
    if w.b.current() is not None:
        print("@@ " + json.dumps({"dock_pose": False, "why": "busy"}), flush=True)
        return 2
    x, y, yaw = dock_pose(w.doc)
    fit = MapFit(w.b.explore_map()).score((x, y, yaw), w.scan())
    if fit < DOCK_FIT_MIN:
        print("@@ " + json.dumps({"dock_pose": False, "why": "fit", "fit": round(fit, 2)}), flush=True)
        return 2
    if w.b.current() is not None:                        # again, right before the write (review 20 Nov)
        print("@@ " + json.dumps({"dock_pose": False, "why": "busy"}), flush=True)
        return 2
    w.b.c.put("/api/core/slam/v1/localization/pose",
              json={"x": x, "y": y, "z": 0, "yaw": yaw, "pitch": 0, "roll": 0}).raise_for_status()
    time.sleep(2.0)
    print("@@ " + json.dumps({"dock_pose": True, "fit": round(fit, 2), "q": w.b.quality()}), flush=True)
    return 0


def search_dock(fit: MapFit, frames, centre: tuple[float, float, float],
                radius_m: float = DOCK_FIND_RADIUS_M, turn_deg: float = DOCK_FIND_TURN_DEG) -> dict:
    """The pose near `centre` where the lidar frames lie best on the map: a coarse grid (2 cm, 1°)
    over the whole square, then a fine one (0.5 cm, 0.25°) round its best. numpy only, every
    (x, y) of one heading at once. {"pose": (x, y, yaw), "edge": bool} -- `edge`: the coarse best
    lies on the search's own border, so the true best may be outside it (not to be taken)."""
    import numpy as np

    pts = [np.asarray(f, dtype=float).reshape(-1, 2) for f in frames]
    pts = np.concatenate([p for p in pts if len(p)] or [np.zeros((0, 2))])
    if not len(pts):
        return {"pose": centre, "edge": True}
    pts = pts[:: max(1, len(pts) // 600)]

    def best_in(cx, cy, cyaw, rad, rdeg, step_m, step_deg):
        """The middle of the top plateau (the fit counts returns within FIT_TOL_M of a wall, so
        the best is a plateau a few cm and ~1° wide -- 16 Nov's on the dock: 10 cm, 1.25° -- and
        its first cell is one edge of it), and whether that plateau reaches the grid's border."""
        offs = np.arange(-rad, rad + step_m / 2, step_m)
        dyaws = np.arange(-rdeg, rdeg + step_deg / 2, step_deg)
        grid = np.zeros((len(dyaws), len(offs), len(offs)))
        for k, dyaw in enumerate(dyaws):
            yaw = cyaw + math.radians(dyaw)
            c, s = math.cos(yaw), math.sin(yaw)
            wx, wy = pts[:, 0] * c - pts[:, 1] * s, pts[:, 0] * s + pts[:, 1] * c
            cols = np.floor((cx + offs[:, None] + wx[None, :] - fit.ox) / fit.res).astype(int)   # (G, N)
            rows = np.floor((cy + offs[:, None] + wy[None, :] - fit.oy) / fit.res).astype(int)
            okc, okr = (cols >= 0) & (cols < fit.w), (rows >= 0) & (rows < fit.h)
            cc, rr = np.clip(cols, 0, fit.w - 1), np.clip(rows, 0, fit.h - 1)
            hit = fit.near[rr[:, None, :], cc[None, :, :]] & okr[:, None, :] & okc[None, :, :]   # (Gy, Gx, N)
            grid[k] = hit.sum(axis=2) / float(len(pts))
        top = grid >= grid.max() - 0.005
        ks, iys, ixs = np.nonzero(top)
        last = len(offs) - 1
        edge = bool(np.any((ixs == 0) | (ixs == last) | (iys == 0) | (iys == last)
                           | (ks == 0) | (ks == len(dyaws) - 1)))
        return (cx + float(offs[ixs].mean()), cy + float(offs[iys].mean()),
                cyaw + math.radians(float(dyaws[ks].mean()))), edge

    (x, y, yaw), edge = best_in(centre[0], centre[1], centre[2], radius_m, turn_deg, 0.02, 1.0)
    (x, y, yaw), _ = best_in(x, y, yaw, 0.04, 1.5, 0.005, 0.25)
    return {"pose": (float(x), float(y), float(yaw)), "edge": bool(edge)}


def find_dock_pose(w: Walker, set_it: bool) -> int:
    """`--find-dock-pose [--set]`: no walk. Where the dock really is, by the lidar, near the route
    file's dock pose (the dock is put down again at every setup -- DOCK_FIND_* above); with --set
    the board's pose is written there when the search is good (the robot stands on the dock: staff
    say so, the server checks the board's docking status). One `@@ {"dock_find": ...}` line."""
    if w.b.current() is not None:
        print("@@ " + json.dumps({"dock_find": False, "why": "busy"}), flush=True)
        return 2
    x0, y0, yaw0 = dock_pose(w.doc)
    fit = MapFit(w.b.explore_map())
    frames = []
    for _ in range(START_FIT_FRAMES):
        frames.append(w.scan())
        time.sleep(0.15)

    def median_fit(pose):
        scores = sorted(fit.score(pose, f) for f in frames)
        return scores[len(scores) // 2]

    found = search_dock(fit, frames, (x0, y0, yaw0))
    x, y, yaw = found["pose"]
    best = median_fit((x, y, yaw))
    ux, uy = math.cos(yaw0), math.sin(yaw0)
    word = {"dock_find": True, "file_fit": round(median_fit((x0, y0, yaw0)), 2), "fit": round(best, 2),
            "pose": [round(x, 3), round(y, 3), round(math.degrees(yaw), 1)],
            "shift_m": round(math.hypot(x - x0, y - y0), 3),
            "along_m": round((x - x0) * ux + (y - y0) * uy, 3), "aside_m": round((x - x0) * -uy + (y - y0) * ux, 3),
            "turn_deg": round(math.degrees(wrap_pi(yaw - yaw0)), 1), "edge": found["edge"],
            "on_dock": w.b.power().get("dockingStatus") == "on_dock"}
    # the lidar at the board's OWN pose: a stale pose after a power cycle reads low here while Q
    # can still say 50+ (the staff page's morning check, review 20 Nov)
    word["board_fit"] = round(median_fit(tuple(float(v) for v in w.b.pose()[:3])), 2)
    word["ok"] = best >= DOCK_FIND_MIN and not found["edge"]
    if set_it and word["ok"]:
        # read again right before the write (review 20 Nov): the search took seconds, and a pose
        # PUT under moving wheels -- or off the dock -- is a wrong pose for the whole walk
        on_dock_now = w.b.power().get("dockingStatus") == "on_dock"
        idle_now = w.b.current() is None
        if word["on_dock"] and on_dock_now and idle_now:
            w.b.c.put("/api/core/slam/v1/localization/pose",
                      json={"x": x, "y": y, "z": 0, "yaw": yaw, "pitch": 0, "roll": 0}).raise_for_status()
            time.sleep(2.0)
            word.update(set=True, q=w.b.quality())
        else:
            word["not_set"] = "busy" if not idle_now else "not_on_dock"
    print("@@ " + json.dumps(word), flush=True)
    return 0 if word["ok"] else 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("route_file")
    ap.add_argument("path")
    ap.add_argument("--from-step", type=int, default=1)
    ap.add_argument("--map", action="store_true", help="SLAM mapping on while walking")
    ap.add_argument("--register-dock", action="store_true")
    ap.add_argument("--trace", help="write every creep tick to this JSON-lines file")
    ap.add_argument("--max-s", type=float, help="stop a creep line after this many seconds")
    ap.add_argument("--record", help="write the pose, a lidar frame and the power state at every stop to this folder")
    ap.add_argument("--set-dock-pose", action="store_true",
                    help="no walk: set the board's pose to the route file's dock pose, if the lidar agrees")
    ap.add_argument("--find-dock-pose", action="store_true",
                    help="no walk: search the lidar's best pose near the route file's dock pose")
    ap.add_argument("--set", action="store_true", help="with --find-dock-pose: write the board's pose there when good")
    ap.add_argument("--wait-go", type=float, metavar="S",
                    help="after the gates: @@ ready, then drive only on a 'go' line on stdin within S s (NO_GO_EXIT)")
    args = ap.parse_args(argv)
    stop_on_signals()                                    # before anything can move
    doc, steps = load_path(args.route_file, args.path)
    board = Board()
    w = Walker(board, doc, args.map, args.register_dock)
    if args.set_dock_pose or args.find_dock_pose:
        key = "dock_find" if args.find_dock_pose else "dock_pose"
        try:
            return find_dock_pose(w, args.set) if args.find_dock_pose else set_dock_pose(w)
        except Exception as e:                           # noqa: BLE001
            print("@@ " + json.dumps({key: False, "why": type(e).__name__}), flush=True)
            return 3
        finally:
            if w.sdk is not None:
                w.sdk.close()
    if args.trace:
        w.trace = open(args.trace, "w", encoding="utf-8")
    if args.max_s:
        global CREEP_MAX_S
        CREEP_MAX_S = args.max_s
    if args.record:
        Path(args.record).mkdir(parents=True, exist_ok=True)
        w.record_dir = args.record
    if args.wait_go is not None and args.wait_go > 0:
        w.go_line, w.go_wait_s = GoLine(), float(args.wait_go)
    speed_before = board.speed()
    w.say("path %s: %d steps, speed was %s, Q%d, %s" % (
        args.path, len(steps), speed_before, board.quality(), board.power().get("dockingStatus")))
    code = 0
    try:
        w.run(steps, args.from_step)
        w.say("path %s done" % args.path)
    except Stop as e:
        try:
            x, y, _ = board.pose()
            w.event(stopped_at=[round(x, 3), round(y, 3)])   # beside `started`'s `at` (event_walk)
        except Exception:                                # noqa: BLE001 -- never hide the stop
            pass
        w.say("STOP: %s" % e)
        code = 2
    except KeyboardInterrupt as e:
        w.say("interrupted%s" % (" (%s)" % e if str(e) else ""))
        code = 130
    except NoGo as e:
        # --wait-go: no go came -- no `started` was said and no wheel turned (event_walk reads `no_go`)
        w.event(no_go=str(e)[:60])
        w.say("no go (%s): not moved" % e)
        code = NO_GO_EXIT
    except Exception as e:                               # noqa: BLE001
        # a board or SDK that stopped answering is a stop with a name: it used to be
        # a traceback on stdout and "exit 1" on the staff page (review 5 Nov)
        w.say("STOP: the board stopped answering (%s)" % type(e).__name__)
        code = 3
    finally:
        try:
            if board.current() is not None:
                board.cancel()
        except Exception:                                # noqa: BLE001
            pass
        try:
            if args.map:
                board.mapping(False)
            if speed_before:
                board.set_speed(speed_before)
        except Exception as e:                           # noqa: BLE001
            w.say("(could not put the speed/mapping back: %s)" % e)
        if w.sdk is not None:
            w.sdk.close()
        if w.trace is not None:
            w.trace.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
