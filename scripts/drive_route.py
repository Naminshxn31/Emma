"""Drive a lane route on the Slamware chassis: straight legs at right angles,
a wall-measured position fix at every stop (app/robot_lanes.py).

    python scripts/drive_route.py data/robot/routes/floor2_out.json --dry-run
    python scripts/drive_route.py data/robot/routes/floor2_out.json

`--dry-run` moves nothing: it runs the first checkpoint where the robot stands
(no turning) and prints what it would correct. Use it on the dock before every
real run.

Needs the chassis relay from docs/robot-arrival-2026-09-09.md — `nc` on the
robot and `adb forward tcp:11448 tcp:11448` — so the board answers at
http://127.0.0.1:11448.

What this refuses to do, and why (each one happened on 6 ต.ค. 2026):

* run with the board's localization ON — it pulled the docked robot 0.33 m
  sideways twice; our fixes would be overwritten between stops.
* drive a diagonal leg — the board's own diagonal through ฝ่ายอาคาร hit a desk.
* keep going after a stop it could not measure — odometry drifted 0.2-0.3 m
  on one 90° turn; two unmeasured legs in a row is how it ends up in a jamb.
* push through a door it has not measured — D11 is 0.856 m for a 0.55 m robot.

Every run writes data/robot/runs/<time>_<route>.jsonl (gitignored) with each
stop's fix and lidar frame, so furniture can be added to the map later from
frames whose pose is known.
"""
from __future__ import annotations

import argparse
import contextlib
import concurrent.futures
import dataclasses
import hashlib
import json
import math
import os
import signal
import sys
import threading
import time
from pathlib import Path

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import robot_lanes as rl  # noqa: E402

MOVE_TO = "slamtec.agent.actions.MoveToAction"
ROTATE_TO = "slamtec.agent.actions.RotateToAction"
MOVE_BY = "slamtec.agent.actions.MoveByAction"
GO_HOME = "slamtec.agent.actions.GoHomeAction"
#: The board's turn-rate limit while ITS GoHome runs, put back afterwards. At
#: its own 1.0 rad/s, GoHome spun ~200° at 50-62°/s before backing in — the
#: loudest scraping the owner heard (6 ต.ค. 16:04) — and an arm 0.78 m across
#: swept that fast while the board believes the robot is a 0.55 m disc.
#: 0.35 is what our own spot turns cruised at until 7 ต.ค. (~16°/s measured;
#: 0.5 since).
DOCK_ANGULAR_SPEED = 0.35
#: Where GoHome starts backing in from: 1.04 m out from the dock marker along
#: its heading, 0 to the side — both recorded GoHomes turned round to reverse
#: at exactly that spot (1.043 / 1.041 m, 3-4 mm aside; 6 ต.ค. 16:04, 16:12).
#: Left short of it or past it, GoHome first drives there itself (0.28-0.30 m
#: forward with the turns that go with it); the owner asked for ≤ 0.2 m.
LANDING_M = 1.042
#: Our own way onto the charger (owner, 7 ต.ค.: "เดินหน้าเข้ามาใกล้ๆ แล้วค่อยหมุนตัว
#: ถอยง่ายกว่า"): drive forward to PREDOCK_M beyond where the robot sits when
#: docked (DOCKED_M out from the marker — the route's dock pose is 0.25 m out),
#: turn round there, back straight in at BACK_SPEED until the board says
#: on_dock. GoHome only if that does not make contact: from anywhere but its
#: landing spot GoHome first drives out to it.
DOCKED_M = 0.25
PREDOCK_M = 0.45
BACK_SPEED = 0.06
BACK_OVERSHOOT_M = 0.04
#: Backing in, the lidar keeps reading the dock check's wall ahead: the robot
#: is blocked when odometry has backed BACK_SLIP_M more than that wall has
#: moved away, BACK_SLIP_FRAMES frames running. 8 ต.ค. 16:10 (room test) the
#: robot came 4 cm off the dock's line and its tail met the column beside the
#: charger; the wheels turned on on the polished floor, odometry counted on to
#: BACK_OVERSHOOT_M, and the robot leant on the column -- the owner: "เกือบล้ม"
#: -- then the second go did the same. The wall said 5-6 cm of it never happened.
#: The slip is counted from the first frame after BACK_SLIP_FROM_M backed: a
#: frame's pose may be stamped a little after its points, which at a standstill
#: start reads as up to ~1.4 cm of slip once moving (reviewer). Simulated from
#: the real scan (desks occluding, ±2° heading, ±4 cm aside) a free back-in
#: reads at most 0.5 cm over two frames running.
BACK_SLIP_M = 0.010
BACK_SLIP_FRAMES = 2
BACK_SLIP_FROM_M = 0.02
#: Returns this close to a wall's line count as that wall (see wall_offset).
BACK_SLIP_BAND_M = 0.08
#: ...and the other way the wheels give it away: they stop. Four earlier
#: back-ins that ended at the overshoot limit with no contact had odometry
#: under 0.005 m/s for 0.4 s, 0.3-2.8 cm before the contacts; none of 13
#: dockings went under 0.015 m/s before on_dock (reviewer, logs 7-8 ต.ค.).
#: Counted after BACK_SLIP_FROM_M backed, the start's ramp is not a stall.
BACK_STALL_MPS = 0.008
BACK_STALL_S = 0.4
#: Over the last 12 cm the wall must have been read within this long, or the
#: robot holds still for the next frame: frames came 1.6 s apart at worst
#: that day, and backing on without them is backing on with the watch off.
BACK_FRAME_MAX_AGE_S = 0.6
#: Blocked: straight forward this far at BACK_EASE_SPEED, the way it came, at
#: once and in the same velocity session (no turning -- the lidar cannot see
#: the column behind), so it is not left leaning on what it met; not when
#: anything is within BACK_EASE_FRONT_M of the lidar ahead (front face 0.30 m
#: + the 6 cm + 10 cm). No second go, this run: the same line meets it again.
BACK_EASE_M = 0.06
BACK_EASE_SPEED = 0.04
BACK_EASE_FRONT_M = 0.46
#: ...given BACK_EASE_S: the wheels make ~60% of what is asked at these speeds
#: (8 ต.ค. 17:32: 4.8 cm in the 3 s it had then, and called that "not clear").
BACK_EASE_S = 5.0
#: The look at the charger's surroundings (route `dock_gap`): only within
#: DOCK_GAP_FACING_DEG of facing the dock and DOCK_GAP_RANGE_M (robot centre to
#: the wall behind it) -- from the end of the west lane (~1.8 m) and from the
#: pre-dock spot (~0.9 m), before the half turn.
DOCK_GAP_FACING_DEG = 25.0
DOCK_GAP_RANGE_M = (0.5, 2.4)
#: At the pre-dock spot the gap must put the robot within DOCK_GAP_REDO_M of the
#: line: backing in leans at most 2° in 0.08 rad/s pulses -- about 1 cm over the
#: 0.45 m (reviewer). Further off: straight back DOCK_GAP_BACK_M the way it came,
#: the approach again, one more look; still off -- not backing in.
DOCK_GAP_REDO_M = 0.015
DOCK_GAP_BACK_M = 0.40
#: Returns between the robot's front and the charger, inside the width it backs
#: in with, are in the way of backing in (they also shadow the faces): wait this
#: long for them to go, then do not back in.
DOCK_PATH_WAIT_S = 5.0
#: A lane gives up when it has drifted LANE_MAX_OFF_M off its line, measured
#: from where it started (a checkpoint may just have moved the robot's pose
#: sideways); it does not start further off than LANE_START_MAX_OFF_M.
LANE_MAX_OFF_M = 0.20
LANE_START_MAX_OFF_M = 0.35
#: Held by something in the lane's strip: stand still and wait this long for
#: it to move before giving up the route -- long enough for someone to walk
#: past or step aside (it was 8 s; people stand by the robot on test days).
LANE_HOLD_S = 20.0
#: Stop a lane when the lidar sees something this close beside the robot's
#: 0.78 m width. The ahead-strip does not cover a door leaf standing open
#: alongside the path — D8's is 12 cm from the arm on the lane in front of it.
#: The narrowest real pass logged was 0.136 m (docked 26° askew).
SIDE_STOP_M = 0.04
#: In a doorway under velocity control: stop when the lidar puts either side
#: this close (arm width 0.78 m -- the owner's figure, padded 3-5 cm), and do
#: not start unless the measured opening's middle is this near the robot's
#: line -- there is no stepping sideways inside a frame or next to a leaf.
DOOR_SIDE_STOP_M = 0.008
DOOR_LINEUP_M = 0.06
DOOR_MAX_OFF_AXIS_DEG = 8.0
#: Further off than DOOR_LINEUP_M, out in front of the door where it can
#: still turn, the robot steps sideways by itself (`step_across`): what I
#: did by hand on 7 ต.ค. at D11 (20.5 cm off -- odometry lost ~20 cm across
#: the 5 m corridor, both ways) and at D8 (7 cm). Past DOOR_SHIFT_MAX_M the
#: opening it sees is more likely not this door's.
DOOR_SHIFT_MAX_M = 0.30
DOOR_SHIFT_TRIES = 2
#: ...on a slant, not square across: turn DOOR_STEP_ANGLE_DEG toward the line,
#: drive the offset / sin of it, turn back -- while the door ahead leaves
#: DOOR_STEP_MIN_FACE_M after the step's forward part (offset / tan). Square
#: across was two 90° spot turns of ~6 s round a 5 cm drive: 16 s of 8 ต.ค.
#: 11:29's D8 out (owner: "ไม่จำเป็นต้องหันเพื่อไปปรับให้ตรงมุมไหม ไม่งั้นจะเสียเวลา").
#: The same lateral, measured the same way; the turn sweeps less, so a leaf
#: beside blocks it less often. Not after a block (`back_off` made room for
#: the quarter turn, and going forward again undoes the backing off).
DOOR_STEP_ANGLE_DEG = 35.0
DOOR_STEP_MIN_FACE_M = 0.60
#: ...and DOOR_LINEUP_M is only the most: going in, the near side must keep
#: DOOR_ENTRY_KEEP_M of what the measured opening leaves a side. 7 ต.ค. 13:55,
#: D8 in: 5.7 cm off (under 6) with 7.6 cm a side = 1.9 cm on the near side,
#: drifted 2.6 cm more on the way in and the side stop held it 5 mm from the
#: west jamb inside the frame -- where it can neither turn nor back out.
DOOR_ENTRY_KEEP_M = 0.04
DOOR_LINEUP_MIN_M = 0.02
DOOR_RESTEP_SLACK_M = 0.005
#: The link dropping (LinkLost): wait up to LINK_WAIT_S for it, polling every
#: LINK_POLL_S, then do the step again -- at most LINK_STEP_RETRIES times a
#: step. A lane within LINK_LANE_DONE_M of its end counts as done (a lane
#: needs 5 cm to have a direction). A door whose wall line is closer than
#: DOOR_RESUME_FACE_M goes on from the frame: from there the aim cannot see
#: the face to measure the opening, and stepping sideways is not possible.
LINK_WAIT_S = 180.0
LINK_POLL_S = 3.0
LINK_STEP_RETRIES = 3
LINK_LANE_DONE_M = 0.06
#: A lane held at the stop distance this close to its end has ended there.
LANE_HELD_DONE_M = 0.08
#: Backing into the dock: this many goes, out to the pre-dock spot between them.
DOCK_TRIES = 2
#: The dock check's wall search: heading guesses off odometry, and how long
#: to look again (someone in front of the wall) before not backing in.
DOCK_CHECK_YAW_TRIES_DEG = (0.0, -6.0, 6.0, -12.0, 12.0, -18.0, 18.0)
DOCK_CHECK_WAIT_S = 5.0
#: --dock-turn-test: each half turn asked this far, one way then the other
DOCK_TURN_TEST_DEG = 178.0
DOOR_RESUME_FACE_M = 0.60
#: Held in a doorway by something ahead on one side for DOOR_BLOCK_S: back off
#: up to DOOR_BACK_OFF_M (no further than the pass had come), step across by
#: what clears it, go in again -- DOOR_BLOCK_RETRIES times a door.
DOOR_BLOCK_S = 2.5
#: No opening seen from the aim: look again for this long before stopping.
#: 10 s first; 7 ต.ค. 16:03 D8 from the corridor still had no opening after
#: 10 s and a minute later three looks in a row found the 0.856 m passage --
#: the second time that day (15:32). The door had been shut (the owner: "จะมี
#: บางครั้งที่เห็นแบบนั้นเพราะประตูปิด"); one opened again, or someone who steps
#: out of the doorway, is waited out. Shut for longer: the route stops there.
DOOR_FIND_WAIT_S = 30.0
DOOR_BACK_OFF_M = 0.30
DOOR_BLOCK_RETRIES = 2
#: Sent at least the creep speed for DOOR_STALL_TICKS (~1 s) and moving under
#: DOOR_STALL_SPEED: add DOOR_KICK_STEP, once a second, up to DOOR_KICK_MAX --
#: only outside the frame's slow zone, never with something at a side or ahead.
#: 7 ต.ค. 15:50, past D8 into the corridor: 0.061 sent, 6 mm/s moved, 13 s for
#: the last 5 cm (34 s through the door had been 0.8 s for it).
DOOR_STALL_TICKS = 10
DOOR_STALL_SPEED = 0.015
DOOR_KICK_STEP = 0.01
DOOR_KICK_MAX = 0.03
DOOR_KICK_NEAR_CREEP = 0.03
#: A step sideways that cannot be made (`StepBlocked`) of no more than
#: DOOR_STEER_ONLY_M, with the opening at least DOOR_STEER_FACE_M ahead: the
#: pass steers it on the way in instead of the route ending there.
#: Steering in instead of a step (owner's sketch, 8 ต.ค.): up to
#: DOOR_STEER_IN_MAX_M off, with DOOR_STEER_IN_MIN_RUN_M or more of approach
#: before the frame's slow zone -- and only where what the approach is
#: predicted to leave at the frame is within the line-up a step would have
#: brought it to, so the near side keeps what a step keeps (DOOR_ENTRY_KEEP_M,
#: 13:55 at D8). The first gate (10 x the offset in run) never bound and let
#: 6 cm in with ~2 cm a side at the frame (reviewer). Predicted: |off| x
#: exp(-DOOR_STEER_IN_RATE x (run - DOOR_STEER_IN_SETTLE_M)) -- the first half
#: metre goes on leaning over. Fitted to the worst of 144 simulated D8 starts
#: (3-4 cm off, odometry ±1 cm / ±2°, glass leaf or not; 8 ต.ค.): 0.93 of the
#: offset left after 0.68 m of run, 0.67 after 1.28 (the model: 0.94, 0.69).
#: Leaning harder did not help: 4-5° and gain 0.9-1.2 left up to 1.2-1.5 of
#: it (swinging past) and came 1.2 cm from the leaf -- turning away from the
#: leaf beside swings the rear toward it. D8 from the room, 5 cm a side
#: (line-up 2): up to 2.9 cm off at 1.3 m of run, 3.9 at 1.9; the rest step.
DOOR_STEER_IN_MAX_M = 0.06
DOOR_STEER_IN_MIN_RUN_M = 0.6
DOOR_STEER_IN_RATE = 0.5
DOOR_STEER_IN_SETTLE_M = 0.55
#: ...and only where the opening leaves this much a side (D8 and D11 with the
#: arms in -- D11 out has ~0.73 m of run, so up to 2.2 cm there). 4.5, not 5:
#: D8's drawing gives 0.0500000000000002 (reviewer) -- not a float's edge.
DOOR_STEER_IN_MIN_MARGIN_M = 0.045
DOOR_STEER_ONLY_M = 0.06
DOOR_STEER_FACE_M = 0.90
DOOR_STEER_KEEP_M = 0.025
#: Walls measured while driving (a drive step's "use"): the stop-and-go of a
#: checkpoint between two lanes the same way (owner, 7 ต.ค.: "จังหวะทางตรงบางที
#: จะมีหยุดก่อนแล้วค่อยเดินต่อ") taken on the move. Every ROLL_FIX_EVERY_M from
#: ROLL_FIX_FIRST_M on, from a lidar frame under ROLL_FIX_AGE_S old, matched off
#: the control loop (73 ms a match; inline it would stall the 10 Hz loop the
#: way the 0.48 s stall overshot a lane at 17:31). Only what a moving frame
#: measures well is taken: the coordinate ACROSS the lane and the heading,
#: from walls along it; along the lane a 0.4 m/s frame is smeared. Up to
#: ROLL_FIX_MAX_M / ROLL_FIX_MAX_DEG a time -- larger is a mismatch, not
#: drift (stopped checkpoints in the corridor moved 10-19 cm per 2-2.5 m).
#: Kept as a correction on top of odometry and written to the board only once
#: the robot has stopped at the lane's end, as a checkpoint does.
#: Corners: two lanes at a right angle, one after the other, are driven as one
#: -- slowing to CORNER_V by the arc's start and round a quarter circle of
#: CORNER_R, without stopping (owner, 7 ต.ค.: "ทำให้เหลือ 1 นาที ... ทำครบรวม
#: เลี้ยวโค้ง"). A stop and a spot turn there was ~9 s: braking, 5.5 s of turn,
#: a checkpoint, starting again. CORNER_DECIDE_M before the arc the lidar frame
#: of the moment is swept with the body along it (CORNER_MARGIN_M clear) and
#: the forbidden areas are checked; anything in it and the lane ends at its
#: target as it always did, and the next one starts with a spot turn. On the
#: arc the strip ahead stops it at CORNER_FRONT_STOP_M (the arc was cleared;
#: this is for whoever steps into it). Corners checked against the 7 ต.ค. map
#: at 0.45 m: junction both ways 0.28 m clear, dock lane into D8's line 0.14;
#: north lane into the dock lane only 0.08 -- that one keeps its stop.
CORNER_R = 0.45
CORNER_V = 0.20
CORNER_LOOKAHEAD_M = 0.30
CORNER_DECIDE_M = 0.40
CORNER_MARGIN_M = 0.06
CORNER_FRONT_STOP_M = 0.40
CORNER_MIN_NEXT_M = 0.30
#: Off the arc by more than CORNER_MAX_OFF_M: the arc is given up on the spot
#: and the robot goes on by whichever lane it is nearer (`corner_fallback`):
#: past the arc's middle it stops and the next lane starts as any lane does,
#: with a spot turn (its room checked); before it, it goes on along the first
#: lane to the corner and turns there, as a corner not taken does. Not the end
#: of the route (owner, 8 ต.ค.: "ทำไมไม่ให้ระบบเราแก้ไขเฉพาะหน้าไปเลย ...
#: เลี้ยวไปอีกทางนิดหน่อยก็ปรับมุมได้"). 09:45: the loop ran at 0.6 s a tick into
#: the first arc, the turn came 0.23 m late, the robot ran 0.17 m wide toward
#: the furniture by the east wall and stood held 20 s until the route ended.
#: Stopping on the arc's first few degrees left the robot ~0.45 m off the next
#: lane -- past LANE_START_MAX_OFF_M, which ended the route anyway (reviewer).
#: Held by the strip ahead on the arc it waits as a lane does (LANE_HOLD_S): a
#: stale frame or someone stepping in front is not a reason to give up.
CORNER_MAX_OFF_M = 0.10
#: ...and the arc starts only from this close to the lane: a lane that comes to
#: the arc's start further off (allowed 0.20; a rolling fix just before the arc
#: can move it) would be off the arc from its first tick.
CORNER_START_MAX_OFF_M = 0.06
CORNER_BACK_MAX_DEG = 20.0
#: Out of a door into a lane at a right angle (D8 out -> the corridor east,
#: D11 out -> the corridor south), rounded the same way instead of a stop and a
#: 90° spot turn (~6 s; owner 8 ต.ค.: "ไม่จำเป็นต้องหันเพื่อไปปรับให้ตรงมุมไหม ไม่งั้นจะ
#: เสียเวลา"). The pass ends where its own slow zone does -- the back
#: DOOR_SLOW_AFTER_M past the wall line -- and the corner starts at least
#: DOOR_EXIT_RUNUP_M on, its radius whatever is left to the lane (at most
#: CORNER_R) and never under half the body + DOOR_EXIT_R_SPARE_M: turning
#: tighter than half its width, the inner rear corner moves BACK -- toward the
#: jamb just passed (at this radius it recedes 5-6 mm in simulation, 13 cm past
#: the wall face, as pursuit tightens the arc). D8 out leaves 0.38 m, D11 out
#: 0.44; anything less: the pass goes on to the lane as it always did. The
#: checkpoint between is not stopped at -- the lane's own walls measure on the
#: way (as at the junction) -- and so only where it measures nothing those do
#: not: across the lane, no door. Anything beside the robot where the pass
#: ends (a leaf along the way out) and the pass goes on to its own end: a
#: lane's side stop (4 cm) would hold it there, in the doorway (reviewer).
#: The run-up is a centimetre over the lane's 5 cm "room before the arc": at
#: exactly 5 the pass stopping a millimetre late lost the corner (reviewer).
DOOR_EXIT_RUNUP_M = 0.06
DOOR_EXIT_R_SPARE_M = 0.01
#: ...and where the pass stopped later than that (9 ต.ค., D8 out: 1.8 cm past
#: its end, the radius came to 0.3589 against 0.36 and the corner went for a
#: stop and a 90° spot turn, ~6 s), the radius stays at its floor and the
#: run-up takes the difference, down to this -- still over the lane's own 5 cm,
#: and the arc then starts further past the wall line than planned, not nearer.
DOOR_EXIT_MIN_RUNUP_M = 0.055
ROLL_FIX_EVERY_M = 0.6
ROLL_FIX_FIRST_M = 0.3
ROLL_FIX_AGE_S = 0.25
ROLL_FIX_MAX_M = 0.15
ROLL_FIX_MAX_DEG = 3.0
#: ...and up to ROLL_FIX_BIG_M / ROLL_FIX_BIG_DEG when walls on BOTH sides of
#: the lane agree (reviewer: the junction's stopped checkpoint moved y 18-19 cm
#: twice with cor_north and cor_south agreeing -- capped at 0.15, every fix on
#: the corridor lane after it would have been thrown away, and the fence and
#: off-lane checks judged from odometry). Beyond that, the lane stops: the
#: walls and odometry disagree by more than drift explains.
ROLL_FIX_BIG_M = 0.30
#: 12°, not 6: 8 ต.ค. 09:16, 0.1 m after the first corner, both walls put the
#: heading 6.6° off odometry's (the turn off the dock lane and the arc; spot
#: turns came out 6-12° off all of 7 ต.ค.) and the 6° cap ended the run there.
#: One fix beyond the caps is thrown away; the lane stops only when the next
#: one says the same (ROLL_FIX_AGREE_*).
ROLL_FIX_BIG_DEG = 12.0
ROLL_FIX_AGREE_M = 0.04
ROLL_FIX_AGREE_DEG = 2.0
#: ...and one frame alone turns the heading at most ROLL_FIX_ONE_DEG (the cap
#: before 8 ต.ค.); 6-12° is taken when the next frame agrees. Raising the cap
#: to 12° let a single mismatched frame steer the robot up to 12° to follow it,
#: with the stair-fence check judging from that pose (reviewer).
ROLL_FIX_ONE_DEG = 6.0
#: ...and ALONG the lane too, where the lane names a wall across its way (the
#: dock lane: ea_east ahead): the frame's own time put right -- it was seen
#: `age` before the pose it is matched from, the robot `v x age` further back
#: -- from a frame under ROLL_ALONG_AGE_S old, up to ROLL_ALONG_MAX_M a time.
#: 8 ต.ค., all five runs: 0.1 m round the corner off the dock lane the walls
#: put the robot 3.4-5.2 cm east of odometry -- the dock lane's length, which
#: no wall along it measures -- and the 0.76 m lane after it brought ~1 cm of
#: that back: D8 out stepped sideways every run, ~12 s.
ROLL_ALONG_MAX_M = 0.08
#: Short lanes (see _drive_velocity): under LANE_SHORT_M, starting within
#: LANE_SHORT_MAX_OFF_M of the line, pursue half their length ahead, at least
#: LANE_LOOKAHEAD_MIN_M.
LANE_SHORT_M = 1.0
LANE_SHORT_MAX_OFF_M = 0.10
LANE_LOOKAHEAD_MIN_M = 0.6
#: An along-lane fix over ROLL_ALONG_MAX_M (up to ROLL_ALONG_BIG_M) is taken
#: when the next one says the same within ROLL_ALONG_AGREE_M: the corridor's
#: x was 10-20 cm off already at its start, the same along the lane (reviewer,
#: 7-8 ต.ค. stops) -- refused outright at 8 cm, it was never put right.
ROLL_ALONG_BIG_M = 0.25
ROLL_ALONG_AGREE_M = 0.03
ROLL_ALONG_AGE_S = 0.15
#: Our own lidar map (app/scan_match.py; owner 19 ต.ค.: "เมื่อกี้เราเดินมาแบบนี้
#: หุ่นก็น่าจะอยู่ประมาณนี้ แล้วก็ค่าตรงกับเลเซอร์"). Matched at every checkpoint
#: from the wheels' guess, next to the walls' fix, and LOGGED ONLY
#: (`shadow_fix`): nothing it says moves the robot or the pose written to the
#: board. Offline it beat the walls' own reach (p90 2.1 / 6.0 cm, docked y the
#: walls never measure); whether it holds on the robot is what these rows are
#: for. 0.17 s a match on the robot's CPU (19 ต.ค.), done while it stands.
#: Built on the PC: `scripts/eval_scan_localization.py --save-field`.
#: RELATIVE to ROOT, joined in _main when the run starts, never here at import
#: (pre-commit review 26 ต.ค. #8/#18): `default=str(ROOT / ...)` was fixed from
#: the real ROOT, so every main()-driven test that patched ROOT still loaded
#: this PC's gitignored map (a test's run log said shadow_map loaded=true) and
#: took the "no map file" path on a clean checkout -- the suite read the
#: machine. On the robot ROOT is the deployed tree either way: same file.
SHADOW_FIELD = Path("data") / "robot" / "maps" / "floor2_lidar_field.npz"
#: ...and the lidar frame on the move, every SCAN_LOG_EVERY_M along a velocity
#: lane (`seen` in the drive record): until 19 ต.ค. only stopped checkpoints
#: logged a frame, so nothing said how a 0.4 m/s frame matches. 2 cm thinned,
#: millimetres -- ~7 KB a frame, ~10 a route.
SCAN_LOG_EVERY_M = 0.6
SCAN_LOG_VOXEL_M = 0.02
#: A step sideways creeps a few cm: its front strip is the door pass's (the
#: body + 1 cm a side), not a lane's (+ 5 cm) built for 0.3 m/s.
STEP_STRIP_MARGIN_M = 0.01
STEP_DONE_M = 0.02
STEP_STILL_M = 0.003
#: One edge or the lane, before a door? The lane, where a checkpoint measured
#: the coordinate across the door less than LATERAL_FRESH_M back (D8 from
#: the room: odometry 1.2 m from two long walls, the one edge seen past the
#: leaf wobbling 6.6 cm with the standpoint). The edge, where it was measured
#: further back and the edge disagrees by more than ONE_EDGE_OVERRULE_M --
#: more than any edge wobble seen: 7 ต.ค. 13:07 at D11 the south jamb said
#: 22 cm off, odometry's y was last measured 2.5 m and two turns back, the
#: lane was trusted and the robot crawled toward the north jamb until the
#: heading guard stopped it.
#: 8 ต.ค.: the code went by the edge whenever edge and lane agreed within 3 cm,
#: fresh or not -- D8 from the room at 1.94 m read -0.038, +0.044, -0.075 at
#: one spot, the lane (walls measured on the way 0.4 m back) -0.01, and the
#: robot stepped twice for nothing (~25 s); D11 from 1.5 m read +0.087 against
#: the frame's +0.027 and stepped twice into a stop. One far edge now only
#: overrules a STALE lane, by more than ONE_EDGE_OVERRULE_M (0.08 -> 0.15:
#: that noise reached 11 cm; the 13:07 case was 22).
LATERAL_FRESH_M = 1.5
ONE_EDGE_OVERRULE_M = 0.15
#: Each door's middle, remembered across runs (owner 9 ต.ค.: "ทำ 1-3 เลย"), one
#: door AND one way through at a time (`rl.door_key`). Every first aim at D8 and
#: D11 since 8 ต.ค. saw one edge + the drawing, so the aim trusted the lane and
#: the lane is the DRAWING's middle: D8 from the corridor is 5.9 cm east of it by
#: the jambs (-2.391, MAD 1.3 cm, n 5), and all five passes went in 6.8 cm west of
#: the jambs' middle -- 1.4-4.3 cm from the west jamb, 10.6-14.5 from the east.
#: A door step with `remember` aims at the median of the last DOOR_MEM_USE_N
#: middles: the route's `door_memory_seed` (measured before the runtime kept
#: one, oldest) and what its passes measured since (--door-memory). Used only
#: with DOOR_MEM_MIN_N or more, spread (MAD as a std) within DOOR_MEM_MAX_MAD_M,
#: and within DOOR_MEM_MAX_FROM_DRAWING_M of the drawing -- further is not this
#: door, or the route's frame moved under it (8 ต.ค.: D11 17 cm). A median of
#: five: one bad pass moves it by one rank, never by its own size.
DOOR_MEM_USE_N = 5
DOOR_MEM_MIN_N = 3
DOOR_MEM_MAX_MAD_M = 0.015
DOOR_MEM_MAX_FROM_DRAWING_M = 0.10
#: ...and a pass is remembered only from the jambs beside it, both at once (the
#: box's two sides adding up to a doorway, DOOR_MEM_FRAME_WIDTH with the body),
#: for DOOR_MEM_MIN_TICKS ticks or more -- one edge is never a middle (D8's moved
#: 6 cm with a 3 cm change of standpoint) -- and only where the coordinate across
#: the door had been measured within LATERAL_FRESH_M before the aim: what it
#: records is the door in the frame the walls just set, not a run's drift. The
#: same rule as scripts/calibrate_from_runs.py's `in_frame`, so its numbers are
#: the seed. Kept: the last DOOR_MEM_KEEP a way; used: under DOOR_MEM_MAX_AGE_S
#: old and measured in the route's present frame (`route_frame_id`).
DOOR_MEM_FRAME_WIDTH = (0.72, 0.98)
DOOR_MEM_MIN_TICKS = 5
DOOR_MEM_KEEP = 10
DOOR_MEM_MAX_AGE_S = 30 * 86400.0

#: Lane speed. Half the board's 0.5 default: every leg ends in a measured stop,
#: so speed buys little, and a slow robot that misjudges a jamb only nudges it.
#: (The board's base.max_moving_speed for MoveTo/GoHome and between lanes;
#: velocity lanes raise it for themselves to their cruise, `_board_speed`.)
LANE_SPEED = 0.25
NORMAL_SPEED = 0.5
#: Cruise for velocity lanes and the spot turns' fast rate, when nothing is
#: given on the command line -- which is every run the robot's launcher
#: (scripts/emma_nav.py) starts. 9 ต.ค., the first two walks from the panel ran
#: on the old defaults, 0.35 m/s and 0.5 rad/s, 116 + 139 s; the hand-started
#: runs of 8 ต.ค. (33-35) had gone at --vmax 0.45 --spin 0.8 with no stop and
#: no contact: 90° turns 3.8-4.0 s (5.5-6.1), 180° 6.1 s (9.5-9.9), at most
#: 0.47° past, one go each (owner 9 ต.ค.: "ทำให้ไวกว่านี้ได้ไหม เพิ่มสปีดเลย").
#: Anything ahead slows a lane by the same distance rule as at 0.35 (lane_limits).
LANE_VMAX = 0.45
SPIN_FAST = 0.8
#: The one knob to dial both back (--speed-scale, 0 < s <= 1): lanes at
#: LANE_VMAX x s, the spot turns' fast rate at SPIN_FAST x s (off at or under
#: the measured 0.5). 0.78 is 9 ต.ค.'s lane speed. Change it here for the
#: launcher's runs: its argv is fixed.
SPEED_SCALE = 1.0
#: Direct drives -- a step sideways onto a door's line, the way to the dock's
#: pre-dock spot and out of it, the landing spot -- keep the speeds they were
#: validated at: they end next to a jamb, a leaf or the charger.
DIRECT_V_MAX = 0.35
#: What the base takes to stop for something that appears in the strip ahead
#: (robot_lanes.stop_speed, DriveLimits.stop_*), from the 7-9 ต.ค. velocity
#: lanes: odometry answers a command ~0.3 s late (least squares over 173 lanes),
#: a frame is up to 0.2 s old when it is read, a tick is 0.1 s; braking asked at
#: 0.5-0.6 m/s^2 showed 0.4-0.8 (median 0.56) -- 0.5 is used.
STOP_REACT_S = 0.6
STOP_DECEL = 0.5
#: Doorways. Aim: how far off the measured middle the push may start (the
#: first version allowed 4 cm and hit D11 from 3.8 cm). Side: stop when the
#: lidar sees a jamb closer than this to the 0.55 m disc — 10 cm because the
#: contact frame showed the robot touching with 5 cm still "free" on the disc.
DOOR_AIM_M = 0.015
#: Side stop, measured from rl.ROBOT_WIDTH (0.78 m). The owner's tape numbers
#: carry some slack ("อันนี้เป็นตัวเลขเผื่อ") and he has driven it through D8
#: and D11 by hand when centred, so the stop is at 0.5 cm on the padded width.
DOOR_SIDE_M = 0.005
#: A door is pushed only if, centred, the gap the lidar measures *now* leaves
#: this much a side on the padded width. D8/D11 fully open leave 3.8 cm. A
#: leaf left half-open narrows the measured gap and moves its middle — the aim
#: follows the measured middle, and this check refuses what no longer fits.
DOOR_MIN_MARGIN = 0.03
# Spot turns under velocity control are not asked for less than this: the
# base does not start turning for a correction under ~2° before the stop
# arrives (0.10 rad/s turns it 0°, and it starts 0.4-0.7 s late), so a
# tighter ask only adds goes that cannot move it (6 ต.ค.).
SPOT_TURN_TOL_DEG = 2.0
#: Spot-turn room: the robot's rectangle swept through the turn must stay
#: this far from every lidar return, and the sweep includes this much coast
#: past the angle (measured 1.6-5.7°; the slow end of a turn coasts ~2°).
TURN_MARGIN_M = 0.04
#: A spot turn whose yaw has moved under TURN_STILL_DEG for TURN_STILL_S while
#: already within its allowance has done what was asked.
TURN_STILL_DEG = 0.3
TURN_STILL_S = 1.5
#: After a spot turn the angle is read once the base has stopped turning --
#: TURN_SETTLE_STILL_DEG between two reads TURN_SETTLE_DT_S apart, at most
#: TURN_SETTLE_MAX_S -- not once the board has dropped the action: that waited
#: for its ~1 s watchdog after every turn, 1.0-1.3 s x 13 turns a round (8 ต.ค.
#: 13:10; the coast itself is over in ~0.4 s). Docking keeps the board's wait:
#: GoHome may follow it.
TURN_SETTLE_STILL_DEG = 0.15
TURN_SETTLE_DT_S = 0.1
TURN_SETTLE_MAX_S = 1.2
#: ...and how long that action is still there (the board's watchdog, ~1 s)
LINGER_S = 1.5
TURN_COAST_DEG = 3.0
DOOR_SPEED = 0.12
#: Inside the frame we never steer; the lidar side reading is the real guard. This
#: only stops a robot whose heading has clearly gone (simulated: 3° stopped clean
#: passes in the doorway on drift alone).
DOOR_TURN_IN_FRAME_DEG = 6.0
#: Own straight driving ("drive" steps). Stop this far short of the target
#: (latency + braking at 0.25 m/s), steer toward a point this far ahead on the
#: lane, and refuse to drive into anything this close in the robot's own strip
#: (the lidar sits near the centre; the base reaches 0.30 m ahead of it).
DRIVE_STOP_LEAD = 0.08
DRIVE_LOOKAHEAD = 1.2
#: Steer only past 2.5° and at most every 0.75 s: one 0.12 s turn send moved the
#: heading ~2-3° on the robot (6 ต.ค. 14:10), and a 1.5° deadband made it swing
#: -3.6° → +4.4° and stall.
DRIVE_STEER_DEG = 2.5
DRIVE_STEER_GAP_S = 0.75
DRIVE_FRONT_STOP = 0.50
#: Board events that end a push at once (GET /api/platform/v1/events, from this
#: robot's /js/spec.js). The owner heard a warning at the D11 contact.
STOP_EVENTS = {"BUMPER_TRIGGERED", "PATH_OCCUPIED", "ROBOT_BLOCKED", "DEVICE_ERROR"}

#: --status-file: the log kinds that rewrite it, and the state each one means
#: (design 2026-10-08 §2g). Not `drive` or `door`: a status every lane
#: would only rewrite the same step, and the launcher polls it anyway.
STATUS_KINDS = {"run_start": "running", "step": "running", "stop": "stopped", "refused": "refused",
                "leg_done": "done", "route_done": "done", "interrupted": "interrupted", "error": "failed"}
#: --to-step: what a leg may end on -- a measured, stationary pose. A leg that
#: ended on a drive would hand the next one a pose nobody measured (the same
#: rule as app/robot_places.LEG_END_STEPS, which the places file is checked by).
LEG_END_DO = ("checkpoint", "dock")
#: --watch-parent: how often the parent is looked at. The launcher's own
#: tether is 4 s (scripts/emma_nav.py TETHER_S); this is well inside it.
WATCH_PARENT_S = 0.5
#: Exit codes. 3 used to be 1: a refusal before anything moved is not a stop
#: mid-route, and the launcher keeps the robot's known place only for the first.
EXIT_DONE, EXIT_STOP, EXIT_NO_BOARD, EXIT_REFUSED, EXIT_INTERRUPTED = 0, 1, 2, 3, 130
#: An exception nobody caught (an httpx error out of preflight, a bug). It used
#: to leave main as Python's own exit 1 = EXIT_STOP, and the launcher told the
#: guest "หุ่นหยุดกลางทาง" for a robot that never moved (review finding 27).
EXIT_ERROR = 4


class Stop(RuntimeError):
    """The run ends here, with the reason, and the robot standing still.

    `code` is the reason as a word the robot's launcher (scripts/emma_nav.py)
    can map to a Thai sentence, set at the raise site. It is never recovered
    from the message: the classes are rewrapped on the way out (`run_step`
    turns a DoorBlocked into a plain Stop "-- still, after 2 goes round it"),
    and English text matched by a regex is the substring bug of CLAUDE.md in
    another language (design 2026-10-08 §2g, build review #6)."""

    code = "stopped"

    def __init__(self, *args, code: str | None = None):
        super().__init__(*args)
        if code is not None:
            self.code = code


class DoorBlocked(Stop):
    """Held in a doorway by something ahead on ONE side -- a jamb or a leaf
    at the edge of the strip. `need` is how far across clears it (+ = the
    robot is too far left); `came` how far the pass had come, which is how
    far back is known to be clear. `Runner.run_step` backs off, steps
    across, and goes in again (the owner, 7 ต.ค.: "พยายามให้หุ่นมันหลบซ้ายขวา
    โดยดูจากเรดาห์ ... ทำให้ระบบสามารถวิเคราะห์เองแก้เองได้")."""

    code = "door_blocked"

    def __init__(self, msg: str, need: float, came: float):
        super().__init__(msg)
        self.need, self.came = need, came


class StepBlocked(Stop):
    """A step sideways onto a door's line that cannot be made from here -- no
    room for the quarter turn, or something that way within the doorway's
    stop distance plus the step. Found from the scan before anything moves.
    In front of a door, a small one is steered on the way in instead."""

    code = "obstacle"


class LinkLost(Stop):
    """The link to the board dropped -- Wi-Fi to the robot, adb's forward with
    it (7 ต.ค.: three times on one trip home, each ending the run). The board's
    own watchdog stops a velocity action within ~1 s, so the robot is standing;
    `Runner.run` waits for the link and does the step again from where it is.
    A safety stop is a plain `Stop` and still ends the run."""

    code = "link_lost"


class Board:
    def __init__(self, base: str, timeout: float = 6.0, reforward=None):
        self.http = httpx.Client(base_url=base.rstrip("/"), timeout=timeout)
        self.reforward = reforward

    def _req(self, method: str, path: str, body=None):
        # The relay rides adb over Wi-Fi, which drops for a second now and then
        # (6 ต.ค. 14:05: "offline", forward gone, run crashed mid-turn). Three
        # tries, then a clean Stop. A dropped link is safe for our own driving:
        # MoveBy only lasts while it is re-sent, so the robot halts by itself.
        for attempt in range(12):
            try:
                r = self.http.request(method, path, json=body)
                break
            except httpx.TransportError as e:
                if attempt == 11:
                    raise LinkLost("lost the board (%s %s): %s" % (method, path, e.__class__.__name__))
                # "Refused" = the adb forward is gone: the Wi-Fi link to the robot
                # dropped for a moment and adb removed the forward with it (the adb
                # server itself kept running, 6 ต.ค. 14:35). Reconnect and re-forward.
                if isinstance(e, httpx.ConnectError) and self.reforward and attempt % 2 == 0:
                    self.reforward()
                time.sleep(0.6)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        if not r.content:
            return None
        try:
            return r.json()
        except ValueError:
            return r.text

    def get(self, path): return self._req("GET", path)
    def put(self, path, body): return self._req("PUT", path, body)
    def post(self, path, body): return self._req("POST", path, body)
    def delete(self, path): return self._req("DELETE", path)

    def pose(self) -> tuple[float, float, float]:
        p = self.get("/api/core/slam/v1/localization/pose")
        return float(p["x"]), float(p["y"]), float(p["yaw"])

    def set_pose(self, pose) -> None:
        x, y, yaw = pose
        self.put("/api/core/slam/v1/localization/pose",
                 {"x": x, "y": y, "z": 0, "yaw": yaw, "pitch": 0, "roll": 0})

    def localization_on(self) -> bool:
        return self.get("/api/core/slam/v1/localization/:enable") is True

    def set_localization(self, on: bool) -> None:
        self.put("/api/core/slam/v1/localization/:enable", {"enable": on})

    def mapping_on(self) -> bool:
        return self.get("/api/core/slam/v1/mapping/:enable") is True

    def power(self) -> dict:
        return self.get("/api/core/system/v1/power/status") or {}

    def current(self):
        return self.get("/api/core/motion/v1/actions/:current")

    def state(self, action_id) -> dict:
        doc = self.get("/api/core/motion/v1/actions/%s" % action_id) or {}
        return doc.get("state") or {}

    def start(self, name: str, options: dict):
        hook = getattr(self, "before_action", None)
        if hook is not None:
            hook()                     # Runner._drop_lingering: no velocity action left current
        doc = self.post("/api/core/motion/v1/actions", {"action_name": name, "options": options}) or {}
        return doc.get("action_id")

    def cancel(self) -> None:
        try:
            self.delete("/api/core/motion/v1/actions/:current")
        except httpx.HTTPError:
            pass

    def set_speed(self, v: float) -> None:
        self.put("/api/core/system/v1/parameter", {"param": "base.max_moving_speed", "value": str(v)})

    def home_pose(self) -> tuple[float, float, float]:
        """The dock marker the board's GoHome aims at."""
        p = self.get("/api/core/slam/v1/homepose")
        return float(p["x"]), float(p["y"]), float(p["yaw"])

    def param(self, name: str) -> float:
        return float(str(self.get("/api/core/system/v1/parameter?param=" + name)).strip('"'))

    def set_param(self, name: str, value: float) -> None:
        self.put("/api/core/system/v1/parameter", {"param": name, "value": "%g" % value})

    def scan(self):
        return rl.scan_points(self.get("/api/core/system/v1/laserscan") or {})

    def events(self) -> list:
        try:
            return self.get("/api/platform/v1/events") or []
        except httpx.HTTPError:
            return []

    def raw_scan(self) -> dict:
        return self.get("/api/core/system/v1/laserscan") or {}

    def forbidden(self) -> list[tuple[float, float, float, float]]:
        """Forbidden rectangles as (x0, x1, y0, y1). The board stores them as a
        segment + half width; ours are all axis-aligned, so this is exact."""
        out = []
        for a in self.get("/api/core/artifact/v1/rectangle-areas/forbidden_area") or []:
            s, e, h = a["area"]["start"], a["area"]["end"], a["area"]["half_width"]
            if abs(s["y"] - e["y"]) < 1e-6:
                out.append((min(s["x"], e["x"]), max(s["x"], e["x"]), s["y"] - h, s["y"] + h))
            elif abs(s["x"] - e["x"]) < 1e-6:
                out.append((s["x"] - h, s["x"] + h, min(s["y"], e["y"]), max(s["y"], e["y"])))
        return out


def wall_offset(points: np.ndarray, pose: tuple[float, float, float],
                anchors: dict, band: float = BACK_SLIP_BAND_M) -> tuple[float | None, float | None]:
    """Where one frame's returns put the robot against `anchors`' walls, minus
    where `pose` puts it: (dx, dy), from the returns within `band` of a wall's
    line and inside its stretch, None with under 50 of them. One least-squares
    line per wall (no RANSAC) -- cheap enough for every frame while backing in
    -- read where it crosses the robot, so a heading that drifts under
    odometry does not read as the robot moving; the median of the returns only
    when the seen stretch is short or does not reach past the robot (a line
    read far beyond its returns is noise: ~1 cm a frame from 50 returns over
    0.3 m, 1.5 m off -- reviewer)."""
    p = np.asarray(points, dtype=float).reshape(-1, 2)
    x, y, yaw = pose
    c, s = math.cos(yaw), math.sin(yaw)
    wx, wy = x + c * p[:, 0] - s * p[:, 1], y + s * p[:, 0] + c * p[:, 1]
    found: dict[str, list[tuple[float, int]]] = {"x": [], "y": []}
    for a in anchors.values():
        across, along, here = (wx, wy, y) if a.axis == "x" else (wy, wx, x)
        sel = (np.abs(across - a.at) <= band) & (along >= a.lo) & (along <= a.hi)
        if sel.sum() < 50:
            continue
        # Where the wall's line crosses the robot's own along-coordinate, not the
        # median of its returns: a heading off by e tilts the returns about the
        # robot, and their median sat ~0.9 m along it -- 0.7° that odometry's
        # heading drifted while backing read as 1.1 cm of slip (17:32 the same
        # day, second back-in: it rose 0.4 -> 1.2 cm, slowly, and stopped).
        al = along[sel] - here
        if np.ptp(al) >= 1.0 and al.min() - 0.2 <= 0.0 <= al.max() + 0.2:
            seen = float(np.polyfit(al, across[sel], 1)[1])
        else:
            seen = float(np.median(across[sel]))
        found[a.axis].append((a.at - seen, int(sel.sum())))
    out = []
    for k in ("x", "y"):
        v = found[k]
        out.append(sum(o * n for o, n in v) / sum(n for _, n in v) if v else None)
    return out[0], out[1]


class ScanWatcher:
    """Lidar side clearance, refreshed in the background during a door push.

    A frame is ~120 KB through the relay; fetched inline it would stretch
    the gap between MoveBy re-sends past the board's own lifetime for them
    and the push would stutter (robot_chassis.py hit the same thing with
    its joystick). Own client: httpx.Client is not shared across threads."""

    def __init__(self, base_url, sdk_addr: tuple[str, int] | None = None):
        import threading
        self._http = httpx.Client(base_url=str(base_url), timeout=4.0)
        # Over the SDK port the same frame is 26 KB instead of 117 KB; on the one
        # Wi-Fi tunnel that carries setvelocity too, that is the difference
        # between a lane and a string of watchdog stops (6 ต.ค. 15:23). Its own
        # connection: SlamwareSdk is not shared across threads either.
        self._sdk_addr = sdk_addr
        self._sdk = None
        self.source = "sdk" if sdk_addr else "rest"
        self._lock = threading.Lock()
        self._value: tuple[float | None, float | None] = (None, None)
        self._front: float | None = None
        self._gap: dict | None = None
        self._points = np.zeros((0, 2))
        self._pose: tuple[float, float, float] | None = None
        self._at = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self._http.close()
        if self._sdk is not None:
            self._sdk.close()

    def clearance(self) -> tuple[float | None, float | None]:
        with self._lock:
            return self._value

    def front(self) -> float | None:
        with self._lock:
            return self._front

    def gap(self) -> dict | None:
        with self._lock:
            return self._gap

    def points(self) -> np.ndarray:
        with self._lock:
            return self._points

    def frame(self) -> tuple[np.ndarray, tuple[float, float, float] | None]:
        """The latest points and the board's pose they were taken at (the
        frame's own x/y/yaw), or None for the pose when the frame has none."""
        with self._lock:
            return self._points, self._pose

    def age(self) -> float:
        """Seconds since the last frame landed. The relay rides Wi-Fi to the
        robot and stalled for seconds at a time on 6 ต.ค. 14:35-14:37; driving
        on after a stall with a stale frame is driving blind."""
        with self._lock:
            return time.time() - self._at if self._at else 99.0

    def _frame(self) -> dict:
        if self._sdk_addr is not None:
            from app import slamware_sdk as sw
            try:
                if self._sdk is None:
                    self._sdk = sw.SlamwareSdk(*self._sdk_addr, timeout=2.0)
                return sw.laser_scan(self._sdk)
            except (sw.SdkError, OSError):
                if self._sdk is not None:
                    self._sdk.close()
                self._sdk, self._sdk_addr, self.source = None, None, "rest (sdk failed)"
        return self._http.get("/api/core/system/v1/laserscan").json()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                doc = self._frame()
                pts = rl.scan_points(doc)
                value, front, gap = rl.side_clearance(pts), rl.front_distance(pts), rl.gap_ahead(pts)
                p = doc.get("pose") if isinstance(doc, dict) else None
                pose = (tuple(float(p[k]) for k in ("x", "y", "yaw"))
                        if isinstance(p, dict) and all(isinstance(p.get(k), (int, float)) for k in ("x", "y", "yaw"))
                        else None)
                with self._lock:
                    self._value, self._front, self._gap, self._points = value, front, gap, pts
                    self._pose = pose
                    self._at = time.time()
            except (httpx.HTTPError, ValueError):
                pass
            self._stop.wait(0.15)


class Runner:
    def __init__(self, board: Board, route: rl.Route, log_path: Path, min_battery: int):
        self.b, self.route, self.min_battery = board, route, min_battery
        self.log_file = log_path.open("a", encoding="utf-8")
        self.fences: list[tuple[float, float, float, float]] = []
        self.door_min_margin = DOOR_MIN_MARGIN
        self.door_width = rl.ROBOT_WIDTH          # width counted in doorways (--door-width)
        self.turn_width = rl.ROBOT_WIDTH          # width swept by spot turns (--arms-in: the base's)
        self.sdk = None                 # app.slamware_sdk.SlamwareSdk when velocity control is available
        self.limits = rl.DriveLimits()
        self.next_heading: float | None = None
        self.sdk_addr: tuple[str, int] | None = None
        self.measured_at: dict[str, tuple[float, float]] = {}   # axis -> where a checkpoint last measured it
        self._lingering: float | None = None
        board.before_action = self._drop_lingering
        # -- legs (design 2026-10-08 §2g); all None = the whole route, as before
        self.to_step: int | None = None       # last step of this run, 1-based (--to-step)
        self.status_path: Path | None = None  # --status-file
        self.state_out: Path | None = None    # --state-out
        self.door_memory_path: Path | None = None   # --door-memory (DOOR_MEM_*)
        self.from_step = 1
        self.log_name = log_path.name
        self.shadow = None                    # app.scan_match.Field (SHADOW_FIELD), log-only
        self.shadow_why = "not loaded"

    def fast_pose(self) -> tuple[float, float, float]:
        """The pose over the SDK port when there is one: 8 ms there against
        37 ms for REST on the same tunnel (6 ต.ค., robot standing)."""
        if self.sdk is not None:
            from app import slamware_sdk as sw
            try:
                r = self.sdk.call("getpose")
                if all(isinstance(r.get(k), (int, float)) for k in ("x", "y", "yaw")):
                    return float(r["x"]), float(r["y"]), float(r["yaw"])
            except (sw.SdkError, OSError):
                pass
        return self.b.pose()

    def watcher(self) -> "ScanWatcher":
        return ScanWatcher(self.b.http.base_url, self.sdk_addr)

    # -- bookkeeping -----------------------------------------------------
    def log(self, **event) -> None:
        event["t"] = round(time.time(), 2)
        self.log_file.write(json.dumps(event, ensure_ascii=False) + "\n")
        self.log_file.flush()
        if getattr(self, "status_path", None) is not None and event.get("kind") in STATUS_KINDS:
            self.write_status(event)

    def last_step(self) -> int:
        """The step this run ends on: `--to-step`, else the route's last."""
        n = getattr(self, "to_step", None)
        return len(self.route.steps) if n is None else n

    def write_status(self, event: dict) -> None:
        """`--status-file`: what the robot's launcher reads while this runs
        (scripts/emma_nav.py merges it into what the face page shows). Written
        whole to PATH.tmp, then os.replace'd: a reader polling every 0.2 s must
        never parse half a file and call a moving robot "unknown". A status
        that cannot be written never ends a run -- the jsonl is the record,
        this is a window onto it."""
        kind = event.get("kind")
        st = dict(getattr(self, "_status", None) or {})
        if event.get("pose") is not None:
            st["pose"] = [round(float(v), 4) for v in event["pose"]]
        if kind == "step":
            i = event.get("i")
            st.update(step=i, do=event.get("do"), label=event.get("label"),
                      door=self.route.steps[i - 1].door if isinstance(i, int) else None)
        elif kind in ("stop", "refused", "interrupted", "error"):
            st.update(reason_code=event.get("code"), reason=event.get("reason") or None)
        st.update({"format": 1, "state": STATUS_KINDS[kind], "step_from": getattr(self, "from_step", 1),
                   "step_to": self.last_step(), "steps_total": len(self.route.steps),
                   "run_log": getattr(self, "log_name", None), "t": event["t"]})
        for k in ("step", "do", "label", "door", "reason_code", "reason", "pose"):
            st.setdefault(k, None)
        self._status = st
        path = Path(self.status_path)
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(st), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as e:
            print("(status file %s not written: %s)" % (path, e), flush=True)

    def say(self, msg: str) -> None:
        print(time.strftime("%H:%M:%S"), msg, flush=True)

    def fenced(self, x: float, y: float) -> bool:
        return any(x0 - 0.05 <= x <= x1 + 0.05 and y0 - 0.05 <= y <= y1 + 0.05 for x0, x1, y0, y1 in self.fences)

    # -- preflight -------------------------------------------------------
    def preflight(self, *, moving: bool, from_step: int = 1) -> None:
        pw = self.b.power()
        self.say("battery %s%%, %s" % (pw.get("batteryPercentage"), pw.get("dockingStatus")))
        if self.b.mapping_on():
            raise Stop("mapping is ON — it would overwrite the plan map; switch it off in RoboStudio first",
                       code="mapping_on")
        # GoHome stays "current" for a few seconds after the robot is on the dock
        # (seen 6 ต.ค. 15:09) — wait that out before calling it a running action.
        for _ in range(10):
            if self.b.current() is None:
                break
            time.sleep(0.5)
        else:
            raise Stop("an action is already running on the board", code="action_running")
        if self.b.localization_on():
            if not moving:
                # A dry run writes nothing; say what a real run would do.
                self.say("board localization is ON — a real run would switch it off first")
            else:
                self.say("board localization was ON — switching it off (it pulls the pose in ฝ่ายอาคาร)")
                self.b.set_localization(False)
                if self.b.localization_on():
                    raise Stop("could not switch the board's localization off", code="localization")
        if moving and (pw.get("batteryPercentage") or 0) < self.min_battery:
            raise Stop("battery %s%% < %d%%" % (pw.get("batteryPercentage"), self.min_battery),
                       code="battery_low")
        self.fences = self.b.forbidden()
        self.say("forbidden areas: %d" % len(self.fences))
        if self.route.start_from_dock and from_step > 1:
            # Resuming mid-route: the robot is wherever the last run left it,
            # and the board's pose is the last fix plus odometry. Keep it.
            self.say("resuming at step %d from the board's pose %s" % (
                from_step, tuple(round(v, 3) for v in self.b.pose())))
        elif self.route.start_from_dock:
            if pw.get("dockingStatus") != "on_dock":
                raise Stop("route starts from the dock but the robot is not on it", code="not_on_dock")
            if self.route.dock_pose and moving:
                self.b.set_pose(self.route.dock_pose)
                self.say("start pose = dock %s" % (self.route.dock_pose,))
            elif self.route.dock_pose:
                x, y, _ = self.b.pose()
                dx, dy = x - self.route.dock_pose[0], y - self.route.dock_pose[1]
                self.say("board pose is %.2f m from the route's dock pose" % math.hypot(dx, dy))

    # -- primitives --------------------------------------------------------
    def wait_idle(self, limit: float) -> bool:
        t0 = time.time()
        while time.time() - t0 < limit:
            if self.b.current() is None:
                return True
            time.sleep(0.3)
        self.b.cancel()
        return False

    def turn_space_clear(self, turn: float | None = None, wait_s: float = 6.0) -> None:
        """Room for the turn about to be made: the robot's own rectangle (0.60 m
        x 0.78 m across the arms) swept through `turn` radians in its direction,
        plus the coast (`rl.turn_sweep_gap`), kept `TURN_MARGIN_M` clear of every
        lidar return. None = a full turn. It was a 0.54 m circle whatever the
        turn: on 6 ต.ค. that refused a 13° turn at the dock's landing spot that
        moved every corner away from what stood beside it. A person stood 0.44 m
        in front of the robot during a test that morning, which is why this
        waits a few seconds and then stops rather than turning. The lidar sees
        ±103°; behind the robot is where it just came from."""
        sweep = 2 * math.pi if turn is None else turn + math.copysign(math.radians(TURN_COAST_DEG), turn)
        t0 = time.time()
        while True:
            pts = self.b.scan()
            gap = rl.turn_sweep_gap(pts, sweep, width=self.turn_width) if len(pts) >= 200 else -9.0
            if gap >= TURN_MARGIN_M:
                return
            if time.time() - t0 > wait_s:
                raise Stop("something %.2f m from the robot's corners on this turn -- not turning in place (needs %.2f)"
                           % (gap, TURN_MARGIN_M))
            self.say("  waiting: something %.2f m from where the robot's corners go on this %.0f° turn"
                     % (gap, math.degrees(sweep)))
            time.sleep(1.0)

    def rotate_to(self, heading: float, tol_deg: float = 5.0, tries: int = 1) -> float:
        """Turn in place. 5° is enough before a leg or a stop — the stop
        measures the true heading anyway — and every extra correction is one
        more in-place turn, which is where the owner heard the casters scrub
        (6 ต.ค.). Doors ask for 1° and three tries. The yaw is sampled during
        the turn and logged: the owner saw it start the wrong way first."""
        err = rl.wrap_pi(heading - self.b.pose()[2])
        if abs(err) <= math.radians(tol_deg):
            return err
        if self.sdk is not None:
            return self.turn_velocity(heading, tol_deg=max(tol_deg, SPOT_TURN_TOL_DEG))
        if abs(err) > math.radians(10):
            self.turn_space_clear(err)
        for _ in range(tries):
            start_yaw = self.b.pose()[2]
            self.b.start(ROTATE_TO, {"angle": heading})
            trace = []
            t0 = time.time()
            while time.time() - t0 < 20:
                trace.append(round(math.degrees(self.b.pose()[2]), 1))
                if self.b.current() is None:
                    break
                time.sleep(0.15)
            else:
                self.b.cancel()
            err = rl.wrap_pi(heading - self.b.pose()[2])
            first = rl.wrap_pi(math.radians(trace[1] if len(trace) > 1 else trace[0]) - start_yaw) if trace else 0.0
            wanted = rl.wrap_pi(heading - start_yaw)
            wrong_way = abs(first) > math.radians(1.0) and first * wanted < 0
            self.log(kind="turn", target=round(math.degrees(heading), 1), start=round(math.degrees(start_yaw), 1),
                     trace=trace, end_err=round(math.degrees(err), 2), wrong_way_first=wrong_way)
            if wrong_way:
                self.say("  (turn started the wrong way: %+.1f° before going %+.1f°)" % (
                    math.degrees(first), math.degrees(wanted)))
            if abs(err) <= math.radians(tol_deg):
                break
        self.say("  heading %+.0f°, off by %.1f°" % (math.degrees(heading), math.degrees(err)))
        return err

    def checkpoint(self, step: rl.Step) -> rl.Fix:
        if step.heading_deg is not None:
            self.rotate_to(math.radians(step.heading_deg))
        guess = self.b.pose()
        # One frame first: two frames 0.3 s apart were ~1 s at every stop
        # (owner, 7 ต.ค.: "ทำให้เหลือ 1 นาที"); the walls on the move match on one
        # frame all the way down the corridor. A second frame only when the first
        # is short or does not match.
        frames = [self.b.raw_scan()]
        pts = rl.scan_points(frames[0])
        fix = (rl.correct_pose(guess, pts, self.route.anchors_for(step), self.route.doors_for(step))
               if len(pts) >= 200 else None)
        # ...or leaves a coordinate unmeasured that this stop's walls measure: one
        # frame at the junction missed mr_north (0.6 m of it) twice that day,
        # and y went unmeasured where two frames had moved it 18-19 cm
        axes = {a.axis for a in self.route.anchors_for(step).values()}
        short = fix is not None and fix.accepted and (("x" in axes and fix.dx is None) or ("y" in axes and fix.dy is None))
        if fix is None or not fix.accepted or short:
            # An empty frame through the relay reads as "no named wall in view"
            # (seen once on the dock, 6 ต.ค. 13:58) — read again before judging.
            time.sleep(0.3)
            frames = [frames[0], self.b.raw_scan()]
            pts = np.vstack([rl.scan_points(f) for f in frames])
            if len(pts) < 200:
                time.sleep(0.5)
                frames = [self.b.raw_scan(), self.b.raw_scan()]
                pts = np.vstack([rl.scan_points(f) for f in frames])
            fix = rl.correct_pose(guess, pts, self.route.anchors_for(step), self.route.doors_for(step))
        if not fix.accepted and self.b.power().get("dockingStatus") == "on_dock":
            # GoHome docks at whatever angle the contacts allow: -5° by hand,
            # -10° and -26° by GoHome on 6 ต.ค. — outside a ±15° heading search.
            # On the dock the position is known, only the heading is not: try
            # headings ±35° and keep the fix with the most wall length behind it.
            best = None
            for deg in range(-35, 36, 5):
                g = (guess[0], guess[1], guess[2] + math.radians(deg))
                f = rl.correct_pose(g, pts, self.route.anchors_for(step), self.route.doors_for(step))
                length = sum(m["length"] for m in f.matches) if f.accepted else 0.0
                if f.accepted and (best is None or length > best[0]):
                    best = (length, f)
            if best is not None:
                fix = best[1]
                self.say("  (on the dock: heading found by a wide search)")
        self.say("  fix at (%.2f, %.2f, %.1f°): %s" % (guess[0], guess[1], math.degrees(guess[2]), fix.summary()))
        for m in fix.matches:
            self.say("    wall %-12s at %+.3f seen %+.3f (%.2f m of it)" % (m["anchor"], m["at"], m["seen"], m["length"]))
        for d in fix.doors:
            self.say("    door %-5s gap %.3f..%.3f (%.3f m wide)" % (d["door"], d["measured"]["lo"], d["measured"]["hi"], d["measured"]["width"]))
        for dd in fix.dropped:
            self.say("    ignored %s run (%.2f m, %+.1f° off the others)" % (dd["anchor"], dd["length"], dd["turn_deg"]))
        self.log(kind="checkpoint", label=step.label, guess=guess, accepted=fix.accepted, reason=fix.reason,
                 pose=fix.pose, dx=fix.dx, dy=fix.dy, dyaw=fix.dyaw, matches=fix.matches, doors=fix.doors, dropped=fix.dropped,
                 scan={"angle": [round(p["angle"], 4) for p in frames[0].get("laser_points", []) if p.get("valid")],
                       "distance": [round(p["distance"], 4) for p in frames[0].get("laser_points", []) if p.get("valid")]})
        self.shadow_fix(step.label, guess, frames[0], fix)
        if not fix.accepted:
            raise Stop("checkpoint %r could not measure its walls: %s" % (step.label or "?", fix.reason))
        self.b.set_pose(fix.pose)
        # where each coordinate was last measured -- a door decides by this
        # whether odometry or the edge it sees knows better where it is
        if getattr(self, "measured_at", None) is None:
            self.measured_at = {}
        for axis, d in (("x", fix.dx), ("y", fix.dy)):
            if d is not None:
                self.measured_at[axis] = (fix.pose[0], fix.pose[1])
        return fix

    def shadow_fix(self, label: str, guess, frame: dict, fix: rl.Fix | None) -> dict | None:
        """Our lidar map's answer at this stop (SHADOW_FIELD), from the same
        wheels' guess and frame as the walls' fix -- logged as `shadow_fix`
        beside it and NEVER used: the pose written to the board is the walls'
        alone. Anything it raises is logged and swallowed; a log row is never
        a reason to stop a route."""
        fd = getattr(self, "shadow", None)
        if fd is None:
            return None
        t0 = time.time()
        try:
            from app import scan_match as sm
            pts = rl.scan_points(frame)
            pts = pts[np.hypot(pts[:, 0], pts[:, 1]) < sm.MAX_RANGE] if len(pts) else pts
            m = sm.search(fd, sm.voxel(pts), guess)
            row = {"guess": [round(float(v), 4) for v in guess], "est": [round(float(v), 4) for v in m.pose],
                   "score": round(m.score, 3), "sharp": [round(s, 3) for s in m.sharp], "pinned": list(m.pinned()),
                   "applied": [round(float(v), 4) for v in m.apply()], "seconds": round(time.time() - t0, 3)}
            if fix is not None and fix.accepted:
                # against the walls, on the axes they measured
                vs = {}
                if fix.dx is not None:
                    vs["x_m"] = round(float(m.pose[0] - fix.pose[0]), 4)
                if fix.dy is not None:
                    vs["y_m"] = round(float(m.pose[1] - fix.pose[1]), 4)
                if fix.dyaw is not None:
                    vs["yaw_deg"] = round(math.degrees(rl.wrap_pi(m.pose[2] - fix.pose[2])), 2)
                row["vs_walls"] = vs
            self.log(kind="shadow_fix", label=label, **row)
            parts = [("%s %+.1f cm" % (k[0], v * 100)) if k.endswith("_m") else ("yaw %+.1f°" % v)
                     for k, v in (row.get("vs_walls") or {}).items()]
            self.say("  map (log only): (%.3f, %.3f, %.1f°) score %.2f%s, %.2f s"
                     % (m.pose[0], m.pose[1], math.degrees(m.pose[2]), m.score,
                        (" -- vs walls " + ", ".join(parts)) if parts else "", row["seconds"]))
            return row
        except Exception as e:                  # noqa: BLE001 -- see the docstring
            try:
                self.log(kind="shadow_fix", label=label, error=("%s: %s" % (type(e).__name__, e))[:200],
                         seconds=round(time.time() - t0, 3))
            except Exception:                   # noqa: BLE001
                pass
            return None

    def move(self, step: rl.Step) -> None:
        x0, y0, _ = self.b.pose()
        tx, ty = step.to
        heading = rl.lane_heading((x0, y0), (tx, ty))
        if heading is None:
            raise Stop("leg %r from (%.2f, %.2f) to (%.2f, %.2f) is not a straight lane" % (step.label, x0, y0, tx, ty))
        self.rotate_to(heading)
        dist = math.hypot(tx - x0, ty - y0)
        # Arrive already facing the way the next step goes. Without this the
        # board ended the east leg turned 7° LEFT (+6.3°) and the next stop
        # then turned 96° right — the owner saw "หมุนไปซ้ายก่อนแล้วจะหมุนไปทางขวา"
        # (6 ต.ค. 13:38; the logged turn traces themselves went straight).
        end_yaw = self.next_heading if self.next_heading is not None else heading
        aid = self.b.start(MOVE_TO, {"target": {"x": tx, "y": ty, "z": 0},
                                     "move_options": {"mode": 0, "flags": ["with_yaw"], "yaw": end_yaw,
                                                      "acceptable_precision": 0.1, "fail_retry_count": 0}})
        self.say("  move %.2f m to (%.2f, %.2f), arrive facing %+.0f°, action %s"
                 % (dist, tx, ty, math.degrees(end_yaw), aid))
        limit = dist / 0.10 + 45
        t0, last, still = time.time(), None, 0.0
        trace, worst_off = [], 0.0
        while True:
            time.sleep(0.5)
            x, y, yaw = self.b.pose()
            trace.append((round(x, 3), round(y, 3), round(math.degrees(yaw), 1)))
            if self.fenced(x, y):
                self.b.cancel()
                self.log(kind="move", label=step.label, to=[tx, ty], end=[x, y], cancelled="fence", trace=trace)
                raise Stop("entered a forbidden area at (%.2f, %.2f) — cancelled" % (x, y))
            # How far the robot has strayed from the lane line. The board's
            # planner may sidestep an obstacle; 0.6 m is a sidestep, more is
            # the diagonal we are here to prevent.
            ux, uy = math.cos(heading), math.sin(heading)
            off = abs(-(x - x0) * uy + (y - y0) * ux)
            worst_off = max(worst_off, off)
            if off > 0.6:
                self.b.cancel()
                self.log(kind="move", label=step.label, to=[tx, ty], end=[x, y], cancelled="lane", trace=trace)
                raise Stop("left the lane by %.2f m at (%.2f, %.2f) — cancelled" % (off, x, y))
            if self.b.current() is None:
                break
            if last and math.hypot(x - last[0], y - last[1]) < 0.02:
                still += 0.5
            else:
                still = 0.0
            last = (x, y)
            if still >= 20:
                self.b.cancel()
                self.log(kind="move", label=step.label, to=[tx, ty], end=[x, y], cancelled="stall", trace=trace)
                raise Stop("no progress for 20 s at (%.2f, %.2f) — cancelled" % (x, y))
            if time.time() - t0 > limit:
                self.b.cancel()
                self.log(kind="move", label=step.label, to=[tx, ty], end=[x, y], cancelled="timeout", trace=trace)
                raise Stop("leg took longer than %.0f s — cancelled" % limit)
        st = self.b.state(aid)
        x, y, _ = self.b.pose()
        miss = math.hypot(tx - x, ty - y)
        self.say("  ended at (%.2f, %.2f), %.2f m from target, strayed up to %.2f m off the lane, board says %s"
                 % (x, y, miss, worst_off, st or "?"))
        self.log(kind="move", label=step.label, to=[tx, ty], end=[x, y], miss=miss, state=st,
                 worst_off_lane=round(worst_off, 3), trace=trace)
        if miss > 0.25:
            raise Stop("leg %r stopped %.2f m short (%s)" % (step.label, miss, st.get("reason") or "no reason given"))

    # -- smooth driving through the SDK's velocity control -----------------
    @contextlib.contextmanager
    def _board_speed(self, v: float):
        """base.max_moving_speed caps velocity control too (7 ต.ค. 16:37: 0.40
        sent, 0.24 moved -- every velocity lane that day ran at LANE_SPEED).
        Raised to `v` for one velocity lane and put back after: MoveTo steps
        and GoHome plan with the same parameter around the board's 0.55 m
        disc, so they keep LANE_SPEED."""
        raised = v > LANE_SPEED + 1e-6
        if raised:
            self._drop_lingering()
            try:
                self.b.set_speed(v)
            except (httpx.HTTPError, Stop):
                raised = False
        try:
            yield
        finally:
            if raised:
                try:
                    self.b.set_speed(LANE_SPEED)
                except (httpx.HTTPError, Stop):
                    pass

    def limits_for(self, direct: bool = False) -> rl.DriveLimits:
        """The lane limits, or for a direct drive (a step sideways, the dock's
        pre-dock spot and the way out of it) the slower ones it was validated
        at (DIRECT_V_MAX) -- set by main as `direct_limits`; a Runner without
        them (tests) drives everything on `limits`, as before."""
        d = getattr(self, "direct_limits", None) if direct else None
        return d if d is not None else self.limits

    def drive_velocity(self, step: rl.Step, direct: bool = False, side_stop: float = SIDE_STOP_M,
                       limits: rl.DriveLimits | None = None, front_margin: float = 0.05) -> None:
        limits = limits or self.limits_for(direct)
        with self._board_speed(limits.v_max):
            self._drive_velocity(step, direct, side_stop, limits, front_margin)

    def run_chain(self, chain: list[int], lead: tuple[int, rl.Step, float] | None = None) -> int:
        """Lanes `chain` (1-based step numbers, at right angles one after the
        other) as one drive: one velocity action and one lidar watcher, each
        corner rounded on the move (`CORNER_*`). Returns the last step driven
        -- earlier than the chain's end when a corner was not clear and that
        lane stopped at its target; the run goes on from the next step as it
        always did. A dropped link raises LinkLost with `step_index` set to the
        lane it dropped on. `lead` = (step number, lane, corner radius): a lane
        not in the route ahead of the chain -- the way out of a door to its
        corner (DOOR_EXIT_*) -- numbered as that door."""
        from app import slamware_sdk as sw
        steps = [self.route.steps[k - 1] for k in chain]
        radii = [CORNER_R] * len(chain)
        if lead is not None:
            chain, steps, radii = [lead[0]] + list(chain), [lead[1]] + steps, [lead[2]] + radii
        if self.b.power().get("dockingStatus") == "on_dock":
            self.undock()
        # the first lane's spot turn on its own action, before the chain's opens
        # (reviewer: inside it, its exit cancelled the shared action)
        k = chain[0]
        with self._board_speed(self.limits.v_max):
            # inside the raised speed: the chain may take this turn's action back
            x, y, yaw = self.b.pose()
            h = rl.lane_heading((x, y), steps[0].to)
            if h is not None and abs(rl.wrap_pi(h - yaw)) > math.radians(10):
                self.turn_velocity(h)
            watcher = self.watcher()
            try:
                watcher.start()
                time.sleep(0.5)
                self.b.events()
                with sw.VelocitySession(self.sdk) as v:
                    carry = None
                    for n, (k, st) in enumerate(zip(chain, steps)):
                        if n > 0:
                            self.say("step %d/%d %s %s (on from the corner)"
                                     % (k, len(self.route.steps), st.do, st.label))
                            self.log(kind="step", i=k, do=st.do, label=st.label, chained=True)
                        then = steps[n + 1] if n + 1 < len(steps) else None
                        try:
                            carry = self._drive_velocity(st, then=then, session=v, watcher=watcher, carry=carry,
                                                         corner_r=radii[n])
                        except LinkLost as e:
                            e.step_index = k
                            raise
                        if carry is None:
                            return k
            except (sw.SdkError, OSError, httpx.TransportError) as e:
                err = LinkLost("chain at step %d: %s" % (k, e))
                err.step_index = k
                raise err
            finally:
                watcher.stop()
        return chain[-1]

    def _drive_velocity(self, step: rl.Step, direct: bool = False, side_stop: float = SIDE_STOP_M,
                        limits: rl.DriveLimits | None = None, front_margin: float = 0.05,
                        then: rl.Step | None = None, session=None, watcher=None, carry: dict | None = None,
                        corner_r: float = CORNER_R):
        """The lane under velocity control: speed and turn rate together, ramped,
        braking into the target, slowing for anything in the robot's strip.
        `robot_lanes.lane_command` decides; this loop feeds it the pose and the
        lidar ten times a second and sends what it says. A stale lidar frame
        counts as "blocked": with the relay on Wi-Fi a stall must not be
        driven through blind (6 ต.ค. 14:35-14:37)."""
        from app import slamware_sdk as sw
        corr0 = carry["corr"] if carry else (0.0, 0.0, 0.0)
        x0, y0, _ = _apply_corr(corr0, self.fast_pose()) if carry else self.b.pose()
        tx, ty = step.to
        if direct:
            # straight at the point, whatever the angle — only for the short
            # approach to the dock's landing spot, which sits on the dock's own
            # 5°-off line, not on a lane
            heading = math.atan2(ty - y0, tx - x0)
        else:
            heading = rl.lane_heading((x0, y0), (tx, ty))
        if heading is None:
            raise Stop("leg %r from (%.2f, %.2f) to (%.2f, %.2f) is not a straight lane" % (step.label, x0, y0, tx, ty))
        ux, uy = math.cos(heading), math.sin(heading)
        length = (tx - x0) * ux + (ty - y0) * uy
        start = (tx - length * ux, ty - length * uy)       # the lane is the axis line through the target
        # "Off the lane" is drift that grew, not where a checkpoint just put the
        # robot: 7 ต.ค. 11:43 a stop moved y by 0.164 m and the next lane peaked
        # at 0.179 of the 0.20 limit while it came back. A stop that moves it
        # past 0.20 would have ended the route on the first tick.
        lat0 = abs(-(x0 - tx) * uy + (y0 - ty) * ux)
        if lat0 > LANE_START_MAX_OFF_M:
            raise Stop("leg %r starts %.2f m off its lane -- more than a lane brings back (%.2f)"
                       % (step.label, lat0, LANE_START_MAX_OFF_M))
        off_limit = max(LANE_MAX_OFF_M, lat0 + 0.05)
        if carry is None and session is None and abs(rl.wrap_pi(heading - self.b.pose()[2])) > math.radians(10):
            self.turn_velocity(heading)
        own_watcher = watcher is None
        if own_watcher:
            watcher = self.watcher()
            try:
                watcher.start()
                time.sleep(0.5)
                self.b.events()
            except BaseException:
                watcher.stop()
                raise
        # a right-angle corner into the next lane, rounded on the move (CORNER_*)
        corner = (corner_geometry((tx, ty), heading, then.to, r=corner_r)
                  if then is not None and not direct else None)
        corner_note, arc = None, False
        if corner is not None:
            along_a = (corner["A"][0] - x0) * ux + (corner["A"][1] - y0) * uy
            if along_a < 0.05:
                corner, corner_note = None, "no room before the arc"
        decided = corner is None
        self.say("  drive %.2f m to (%.2f, %.2f), velocity control%s"
                 % (length, tx, ty, "" if corner is None else ", round the corner into %r" % then.label))
        t0, prev, trace, worst, reason = time.time(), (carry["prev"] if carry else (0.0, 0.0)), [], 0.0, None
        prog_t, prog_rem = t0, length
        still_t, still_rem = t0, length      # the last time it moved STEP_STILL_M
        dt = 0.1
        lost, held_short = None, None
        lim = limits or self.limits_for(direct)
        if abs(length) < LANE_SHORT_M and lat0 <= LANE_SHORT_MAX_OFF_M and not direct:
            # A lane under LANE_SHORT_M aims half its length ahead, not under
            # LANE_LOOKAHEAD_MIN_M: with 0.8 m the 0.76 m lane into D8 brought ~1
            # cm of the 4-5 cm the corner had left back -- every run of 8 ต.ค. --
            # and D8 out stepped sideways (~10 s). Simulated (casters, 0.3 s lag,
            # 2-8 cm and ±2° at the start): 0.6 ends within 1.7 cm (0.8: 2.7) for
            # ~1 more left/right reversal a lane; 0.4 got 0.9 cm but twice the
            # reversals -- each swings the casters round, the scraping the owner
            # heard -- and a lane starting 0.19 m off turned on the spot (no room
            # check there) (reviewer). So not for a lane starting further off
            # than LANE_SHORT_MAX_OFF_M, and nothing changes for longer lanes.
            lim = dataclasses.replace(lim, lookahead=max(LANE_LOOKAHEAD_MIN_M, abs(length) / 2.0))
        side_hold_t = None
        # walls measured on the move (ROLL_FIX_*): the correction so far, the
        # match in flight, what was taken and what was not
        roll = self.route.anchors_for(step) if step.use else {}
        # a lane after a corner is short (0.7 m into D8): first fix sooner, closer together
        every = min(ROLL_FIX_EVERY_M, max(0.25, abs(length) / 3.0))
        corr, pending, next_fix, rolled = corr0, None, (0.1 if carry else ROLL_FIX_FIRST_M), []
        seen, next_seen = [], 0.0           # frames on the move, for the log only (SCAN_LOG_*)
        big_seen = None                     # a fix beyond the caps, waiting for the next to agree
        along_seen = None                   # an along fix over ROLL_ALONG_MAX_M, likewise
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1) if roll else None
        nlx, nly = -uy, ux
        handed, last_tick = False, None
        try:
          try:
            with contextlib.ExitStack() as stack:
                v = session if session is not None else stack.enter_context(sw.VelocitySession(self.sdk))
                renewals0 = v.renewals
                while True:
                    tick = time.time()
                    x, y, yaw = _apply_corr(corr, self.fast_pose())
                    if pending is not None and pending[0].done():
                        fut, q, q_age, q_v = pending
                        pending = None
                        try:
                            f = fut.result()
                        except Exception as e:                  # noqa: BLE001 -- a bad match is no fix
                            f = rl.Fix(False, "match failed: %s" % e, q, q)
                        lat_c = (f.pose[0] - q[0]) * nlx + (f.pose[1] - q[1]) * nly
                        dyaw = rl.wrap_pi(f.pose[2] - q[2])
                        across = f.dy if abs(ux) >= abs(uy) else f.dx
                        lat_axis_ = "y" if abs(ux) >= abs(uy) else "x"
                        here = q[1] if lat_axis_ == "y" else q[0]
                        sides_ = {m["at"] > here for m in f.matches if m.get("axis") == lat_axis_}
                        both = len(sides_) == 2                 # walls on both sides of the lane agree
                        lim_m = ROLL_FIX_BIG_M if both else ROLL_FIX_MAX_M
                        lim_deg = ROLL_FIX_BIG_DEG if both else ROLL_FIX_MAX_DEG
                        row = {"along": round((q[0] - x0) * ux + (q[1] - y0) * uy, 2),
                               "lat": round(lat_c, 3), "dyaw_deg": round(math.degrees(dyaw), 2),
                               "walls": [m["anchor"] for m in f.matches], "both_sides": both}
                        # along the lane: only from a wall across the way, named by the lane
                        along_meas = f.dx if lat_axis_ == "y" else f.dy
                        along_c = ((f.pose[0] - q[0]) * ux + (f.pose[1] - q[1]) * uy) + q_v * q_age
                        along_seen_ok = (along_meas is not None and q_age < ROLL_ALONG_AGE_S
                                         and any(m.get("axis") != lat_axis_ for m in f.matches))
                        along_ok = along_seen_ok and abs(along_c) <= ROLL_ALONG_MAX_M
                        if along_seen_ok and ROLL_ALONG_MAX_M < abs(along_c) <= ROLL_ALONG_BIG_M:
                            if along_seen is not None and abs(along_seen - along_c) <= ROLL_ALONG_AGREE_M:
                                along_ok = True             # the second to say it
                            # pending until it is applied: a frame that agrees and then is
                            # refused for its across or heading keeps it for the next (reviewer)
                            along_seen = along_c
                        elif along_seen_ok:
                            along_seen = None
                        if along_meas is not None:
                            row["along_fix"] = round(along_c, 3)
                        agrees = (big_seen is not None and abs(big_seen[0] - lat_c) <= ROLL_FIX_AGREE_M
                                  and abs(rl.wrap_pi(big_seen[1] - dyaw)) <= math.radians(ROLL_FIX_AGREE_DEG))
                        if (f.accepted and across is not None and both
                                and (abs(lat_c) > ROLL_FIX_BIG_M or abs(dyaw) > math.radians(ROLL_FIX_BIG_DEG))):
                            row["taken"] = False
                            row["why"] = "beyond the caps"
                            rolled.append(row)
                            if agrees:
                                reason = ("walls on both sides put the robot %+.2f m / %+.1f° off where odometry "
                                          "has it, twice -- more than drift explains" % (lat_c, math.degrees(dyaw)))
                                break
                            big_seen = (lat_c, dyaw)
                        elif (f.accepted and across is not None and both
                              and abs(dyaw) > math.radians(ROLL_FIX_ONE_DEG)
                              and not (big_seen is not None and abs(rl.wrap_pi(big_seen[1] - dyaw))
                                       <= math.radians(ROLL_FIX_AGREE_DEG))):
                            # the heading has to agree; across it cannot: driven a
                            # heading 8° out, what the walls put across grows by
                            # sin 8° per metre between the two frames
                            row["taken"] = False
                            row["why"] = "more than one frame turns -- waiting for the next to agree"
                            rolled.append(row)
                            big_seen = (lat_c, dyaw)
                        else:
                            big_seen = None
                            lat_ok = across is not None and abs(lat_c) <= lim_m
                            lat_bad = across is not None and not lat_ok     # a mismatch: none of it
                            if (f.accepted and (lat_ok or along_ok) and not lat_bad
                                    and abs(dyaw) <= math.radians(lim_deg)):
                                t_lat = lat_c if lat_ok else 0.0
                                t_along = along_c if along_ok else 0.0
                                corr = _compose_corr(corr, dyaw, q[:2], (t_lat * nlx + t_along * ux,
                                                                         t_lat * nly + t_along * uy))
                                x, y, yaw = _apply_corr(corr, self.fast_pose())
                                # the lane is now judged from where the walls put it
                                off_limit = max(off_limit, abs(-(x - tx) * uy + (y - ty) * ux) + 0.05)
                                if getattr(self, "measured_at", None) is None:
                                    self.measured_at = {}
                                if lat_ok:
                                    self.measured_at[lat_axis_] = (x, y)
                                if along_ok:
                                    along_seen = None
                                    self.measured_at["x" if lat_axis_ == "y" else "y"] = (x, y)
                                    if corner is not None and decided and abs(t_along) > 0.02:
                                        # the arc's sweep was judged from where the robot was
                                        # thought to be (reviewer): judge it again from here
                                        decided = False
                                row["taken"] = True
                                row["along_taken"] = along_ok
                            else:
                                row["taken"] = False
                                row["why"] = f.reason if not f.accepted else "too big or not across the lane"
                            rolled.append(row)
                    fresh = watcher.age() < 0.6
                    # the body as it is now: the base's width with the arms in (7 ต.ค.: D11's
                    # leaf 3 cm from the base read as touching the arms' 0.78). The strip
                    # ahead too: 7 ต.ค. 11:39 the corridor's glass-wall posts 0.43 m beside
                    # the lane held the robot as "0.48 m ahead" through the arms' 0.78 strip,
                    # while with the arms where they hang it was clear for 1.35 m.
                    pts_now = watcher.points() if fresh else None
                    front = (rl.front_distance(pts_now, half_width=self.turn_width / 2, margin=front_margin)
                             if fresh else 0.0)
                    sides = [g for g in (rl.side_clearance(pts_now, half_width=self.turn_width / 2, shaped=True)
                                         if fresh else (None, None)) if g is not None]
                    along = (x - x0) * ux + (y - y0) * uy
                    if pts_now is not None and watcher.age() < ROLL_FIX_AGE_S and along >= next_seen:
                        next_seen = along + SCAN_LOG_EVERY_M
                        try:
                            fr = getattr(watcher, "frame", None)
                            pts_l, pose_l = fr() if fr is not None else (pts_now, None)
                            seen.append(scan_record(
                                pts_l, _apply_corr(corr, pose_l) if pose_l is not None else (x, y, yaw),
                                along, 0.0 if pose_l is not None else watcher.age(), prev[0]))
                        except Exception:       # noqa: BLE001 -- a frame for the log never stops a lane
                            pass
                    if (roll and not arc and pending is None and pts_now is not None
                            and watcher.age() < ROLL_FIX_AGE_S and along >= next_fix):
                        next_fix = along + every
                        # from the pose the board says the frame was taken at -- no timing
                        # model: the frame's age from landing missed the board's own
                        # latency, and the speed was the commanded one (reviewer)
                        fr = getattr(watcher, "frame", None)
                        pts_sub, pose_sub = fr() if fr is not None else (pts_now, None)
                        if pose_sub is not None:
                            q_sub, q_age, q_v = _apply_corr(corr, pose_sub), 0.0, 0.0
                        else:
                            pts_sub, q_sub, q_age, q_v = pts_now, (x, y, yaw), watcher.age(), prev[0]
                        pending = (pool.submit(rl.correct_pose, q_sub, np.array(pts_sub), roll, {}),
                                   q_sub, q_age, q_v)
                    d_a = None
                    if corner is not None:
                        d_a = (corner["A"][0] - x) * ux + (corner["A"][1] - y) * uy
                        age = watcher.age()
                        # a stale frame decides only at the arc's start: a lane setting off
                        # 6 cm before it (out of a door) gets the frames on the way (reviewer)
                        if not decided and d_a <= CORNER_DECIDE_M and (age < ROLL_FIX_AGE_S or d_a <= 0.0):
                            decided = True
                            # the frame was taken `age` ago, `prev[0] * age` further back
                            # along the lane (reviewer: up to 0.2 m at 0.6 s)
                            if age < ROLL_FIX_AGE_S and pts_now is not None:
                                gap = corner_clear(corner, (x, y, yaw), np.asarray(pts_now) - (prev[0] * age, 0.0),
                                                   self.turn_width)
                            else:
                                gap = -9.0
                            fenced = any(self.fenced(p[0], p[1]) for p in corner_poses(corner))
                            if gap < CORNER_MARGIN_M or fenced:
                                corner_note = ("forbidden area on the arc" if fenced else
                                               "no fresh lidar frame" if gap == -9.0 else
                                               "%.2f m clear along the arc (need %.2f)" % (gap, CORNER_MARGIN_M))
                                self.say("  corner into %r not taken: %s -- stopping to turn" % (then.label, corner_note))
                                corner = None
                            else:
                                corner_note = "%.2f m clear" % gap
                    if corner is not None and not arc and d_a <= 0.0:
                        # at the arc's start: where the lane has brought it, rolling fixes and all
                        lat_now = -(x - tx) * uy + (y - ty) * ux
                        if abs(lat_now) > CORNER_START_MAX_OFF_M:
                            corner_note = ("%.2f m off the lane before the arc (arc from %.2f)"
                                           % (lat_now, CORNER_START_MAX_OFF_M))
                            self.say("  corner into %r not taken: %s -- stopping to turn" % (then.label, corner_note))
                            corner = None
                    if corner is not None and (arc or d_a <= 0.0):
                        arc = True
                        # the ramps by the time that really passed: 0.6 s ticks at
                        # 09:45 turned the omega ramp into a 1.2 s straight run
                        dt_arc = min(0.5, max(dt, tick - last_tick)) if last_tick else dt
                        cmd = arc_command(corner, (x, y, yaw), prev, dt_arc, front, lim)
                        if cmd.done:
                            handed = True
                            break
                        if abs(cmd.lat) > CORNER_MAX_OFF_M:
                            if corner_fallback(corner, (x, y, yaw), then.to) == "next":
                                corner_note = ("arc given up: %.2f m off it -- stopping, the next lane turns "
                                               "on the spot" % cmd.lat)
                                self.say("  " + corner_note)
                                break
                            corner_note = ("arc given up: %.2f m off it -- on along the lane to the corner, "
                                           "turning there" % cmd.lat)
                            self.say("  " + corner_note)
                            corner, arc = None, False
                            cmd = rl.lane_command((x, y, yaw), start, (tx, ty), prev, dt, front, lim)
                    elif corner is not None:
                        # on to the corner, at CORNER_V by the arc's start
                        v_c = corner_speed(lim)
                        cap = math.sqrt(v_c ** 2 + 2.0 * lim.decel * max(0.0, d_a - prev[0] * lim.latency))
                        cmd = rl.lane_command((x, y, yaw), start, corner["C"], prev, dt, front,
                                              dataclasses.replace(lim, v_max=max(v_c, min(lim.v_max, cap))))
                    else:
                        cmd = rl.lane_command((x, y, yaw), start, (tx, ty), prev, dt, front, lim)
                    worst = max(worst, abs(cmd.lat))
                    # ...and, last, the free run ahead of the front face (m, what the
                    # speed is held to: robot_lanes.free_space at 0°; None = clear for
                    # 3 m, 0 = no fresh frame) and what held the speed ("front" the
                    # taper, "stop" the stop it can make, None the lane itself):
                    # where a lane slowed, and for what (scripts/analyze_run.py)
                    trace.append((round(x, 3), round(y, 3), round(math.degrees(yaw), 1), round(cmd.lat, 3),
                                  round(cmd.vx, 3), round(cmd.omega, 3), cmd.phase, round(tick - t0, 2),
                                  round(watcher.age(), 2),
                                  None if front is None else round(max(0.0, front - rl.ROBOT_BASE_LENGTH / 2.0), 2),
                                  getattr(cmd, "why", "") or None))
                    if cmd.done:
                        break
                    # Never for a direct step: one shorter than this is "done" before
                    # it has moved (15:14, the 3.6 cm step at D11).
                    if still_rem - cmd.rem >= STEP_STILL_M:
                        still_t, still_rem = tick, cmd.rem
                    if direct and cmd.phase == "drive" and cmd.rem <= STEP_DONE_M and tick - still_t > 1.5:
                        # A step sideways a centimetre or two short and not moving: done.
                        # 8 ต.ค. 09:36, a 10 cm step at D11 stood 2 cm short for 20 s --
                        # something 0.48 m ahead slowed it under what the base moves at
                        # -- and the route ended. Within the door's line-up. "Not moving"
                        # is under STEP_STILL_M in 1.5 s, not the lane's 1 cm of progress:
                        # a 2-3 cm step still creeping off the start was counted done
                        # with 1-9 mm made (reviewer).
                        held_short = cmd.rem
                        break
                    if cmd.phase == "drive" and front is not None and front <= lim.front_stop \
                            and cmd.rem <= LANE_HELD_DONE_M and not direct:
                        # 7 ต.ค. 14:33: 4 cm from the junction lane's end, the step in
                        # the corridor's end wall 0.49 m ahead (stop 0.50) -- it waited
                        # 20 s and ended the route. That close, what holds it at the
                        # stop distance is where the lane ends; the next stop measures.
                        held_short = cmd.rem
                        break
                    if sides and min(sides) < side_stop and not self.fenced(x, y):
                        # Something beside: stand still and let it go by. 15:28 in the
                        # corridor between the meeting rooms it read 0.00 m and a moment
                        # later both sides were 0.4 m clear -- someone at the robot's
                        # side, and the route ended for it.
                        if side_hold_t is None:
                            side_hold_t = tick
                            self.say("  something %.2f m beside the robot -- standing still for it" % min(sides))
                        if tick - side_hold_t <= LANE_HOLD_S:
                            prev = (0.0, 0.0) if v.set(0.0, 0.0) else (0.0, 0.0)
                            prog_t = still_t = tick
                            # or the first arc tick after the hold ramps over the whole
                            # hold (dt 0.5): 0.175 m/s and 0.39 rad/s from standstill,
                            # beside whoever held it (reviewer)
                            last_tick = tick
                            trace.append("held beside")
                            time.sleep(max(0.0, dt - (time.time() - tick)))
                            continue
                    else:
                        side_hold_t = None
                    if self.fenced(x, y):
                        reason = "entered a forbidden area"
                    elif sides and min(sides) < side_stop:
                        reason = "something %.2f m beside the robot for %.0f s" % (min(sides), LANE_HOLD_S)
                    elif self._drive_events([], round(tick - t0, 2)):
                        reason = "board event (bumper / path occupied)"
                    elif abs(cmd.lat) > off_limit:
                        reason = "%.2f m off the %s" % (cmd.lat, "arc" if arc else "lane")
                    elif tick - t0 > abs(length) / 0.08 + 30:
                        reason = "too slow"
                    elif v.renewals - renewals0 > 4:
                        reason = "the link stalled past the board's watchdog %d times" % (v.renewals - renewals0)
                    if prog_rem - cmd.rem > 0.01:
                        prog_t, prog_rem = tick, cmd.rem
                    elif tick - prog_t > LANE_HOLD_S and cmd.phase in ("drive", "corner"):
                        reason = reason or ("held for %.0f s (something %s ahead)"
                                            % (LANE_HOLD_S, "—" if front is None else "%.2f m" % front))
                    if reason:
                        break
                    if v.set(cmd.vx, cmd.omega):
                        # A stall outlived the board's watchdog: the robot is standing
                        # and the action was taken up again with a zero — ramp from
                        # there (6 ต.ค. 15:14 this was the operation_fail that ended
                        # the leg).
                        prev = (0.0, 0.0)
                        trace.append("renewed")
                    else:
                        prev = (cmd.vx, cmd.omega)
                    last_tick = tick
                    time.sleep(max(0.0, dt - (time.time() - tick)))
                if not handed:
                    # Stop now: in a chain the session belongs to run_chain and would
                    # send its zero only after this lane's logging (reviewer: a bumper
                    # or the stair fence at 0.35 m/s, ~0.5 s and 15-18 cm more).
                    v.set(0.0, 0.0)
          except (Stop, sw.SdkError, OSError) as e:
            # The link dropped mid-leg (6 ต.ค. 15:10, at 1.07 m of 1.59). The board
            # stops a velocity action ~1 s after the last command by itself;
            # keep the trace so the log says where and how it was going.
            lost = str(e)
        finally:
            if own_watcher:
                watcher.stop()
            if pool is not None:
                pool.shutdown(wait=False)
        if lost:
            self.log(kind="drive", mode="velocity", label=step.label, to=[tx, ty], lost_link=lost,
                     worst_off_lane=round(worst, 3), seconds=round(time.time() - t0, 1), corr=list(corr),
                     on_arc=arc, trace=trace)
            if arc:
                # Between two lanes, at an angle: neither lane can be driven again
                # from here as a straight leg (reviewer: lane_heading refuses it, or
                # it means a 150-180° turn and back). Stand and say so -- and which
                # lane it can start again on (in the arc's first degrees the next
                # one refuses: more than 0.35 m off it).
                again = then.label
                try:
                    x, y, yaw = _apply_corr(corr, self.fast_pose())
                    if corner is not None and corner_fallback(corner, (x, y, yaw), then.to) == "lane":
                        again = step.label
                except (Stop, OSError, httpx.HTTPError):
                    pass                        # the link is down: say the next lane
                raise Stop("drive %r: link lost on the corner into %r (%s) -- the board has stopped the robot "
                           "between the lanes; check it and resume from step %r"
                           % (step.label, then.label, lost, again), code="link_lost")
            err = LinkLost("drive %r: link lost mid-leg (%s) — the board stops itself within ~1 s" % (step.label, lost))
            err.corr = corr                 # still valid against the board's odometry
            raise err
        if handed:
            # round the corner and on: no stop, the correction goes on with it
            x, y, yaw = _apply_corr(corr, self.fast_pose())
            self.log(kind="drive", mode="velocity", label=step.label, to=[tx, ty], end=[x, y, yaw],
                     worst_off_lane=round(worst, 3), seconds=round(time.time() - t0, 1), handed_over=then.label,
                     corner=corner_note, rolling=rolled or None, trace=trace, seen=seen or None)
            self.say("  round the corner into %r at (%.2f, %.2f) on the move (%s), %.1f s"
                     % (then.label, x, y, corner_note, time.time() - t0))
            return {"prev": prev, "corr": corr}
        time.sleep(0.4)
        x, y, yaw = _apply_corr(corr, self.b.pose())
        if corr != (0.0, 0.0, 0.0):
            self.b.set_pose((x, y, yaw))            # stopped now: hand the walls' word to the board
        miss = math.hypot(tx - x, ty - y)
        self.log(kind="drive", mode="velocity", label=step.label, to=[tx, ty], end=[x, y, yaw], miss=miss,
                 worst_off_lane=round(worst, 3), seconds=round(time.time() - t0, 1), stopped=reason,
                 held_short=None if held_short is None else round(held_short, 3), rolling=rolled or None,
                 corner=corner_note, trace=trace, seen=seen or None)
        if rolled:
            taken = [r_ for r_ in rolled if r_["taken"]]
            along = [r_ for r_ in taken if r_.get("along_taken")]
            self.say("  walls on the way: %d of %d taken, across %s cm%s"
                     % (len(taken), len(rolled), ", ".join("%+.1f" % (r_["lat"] * 100) for r_ in taken
                                                          if not r_.get("along_taken") or r_["lat"]) or "-",
                        "; along %s cm" % ", ".join("%+.1f" % (r_["along_fix"] * 100) for r_ in along)
                        if along else ""))
        if held_short is not None:
            self.say("  (held %.2f m short of the end by something at the stop distance -- taken as the end)"
                     % held_short)
        self.say("  ended at (%.2f, %.2f), %.2f m from target, off the lane at most %.3f m, %.1f s"
                 % (x, y, miss, worst, time.time() - t0))
        if reason:
            raise Stop("drive %r: %s at (%.2f, %.2f)" % (step.label, reason, x, y))

    def turn_velocity(self, heading: float, tol_deg: float = SPOT_TURN_TOL_DEG, tries: int = 3,
                      board_idle: bool = False) -> float:
        """Turn on the spot to `heading` under velocity control
        (`robot_lanes.turn_command`: a cruise rate, then the slowest rate that
        turns this base, the zero sent early by the coast). After each go the
        angle is read again once the base has stopped turning (TURN_SETTLE_*)
        -- or, `board_idle`, once the board has dropped the action (docking:
        a REST GoHome may come next); up to `tries` goes."""
        from app import slamware_sdk as sw
        err = rl.wrap_pi(heading - self.fast_pose()[2])
        if abs(err) <= math.radians(tol_deg):
            return err
        # Docking's turns (board_idle) at the rate they were validated at: the
        # half turn before backing in decides how crooked it meets the charger.
        lim = dataclasses.replace(self.limits, w_spin_fast=0.0) if board_idle else self.limits
        if abs(err) <= lim.w_spin_min * lim.spin_coast_s:
            # Under the coast of the slowest rate that turns this base (2.2°):
            # turn_command sends one 0.1 s tick and calls it coasting in, and the
            # base -- 0.4-0.7 s from rest before it turns at all -- does not move.
            # 9 ต.ค., D8 out: 2.08° off, three goes, 0.7 s, still 2.08° off.
            self.log(kind="turn_skipped", mode="velocity", to_deg=round(math.degrees(heading), 1),
                     off_deg=round(math.degrees(err), 2), why="under what the base turns for")
            return err
        if abs(err) > math.radians(10):
            self.turn_space_clear(err)
        t0, trace, renewals, goes = time.time(), [], 0, 0
        for goes in range(1, tries + 1):
            prev, t_go = 0.0, time.time()
            mark = (t_go, None)                         # when the yaw last moved TURN_STILL_DEG
            with sw.VelocitySession(self.sdk, wait_idle_s=3.0 if board_idle else 0.0) as v:
                while time.time() - t_go < 25:
                    tick = time.time()
                    yaw = self.fast_pose()[2]
                    cmd = rl.turn_command(yaw, heading, prev, 0.1, lim, tol_deg=tol_deg)
                    trace.append((round(time.time() - t0, 2), round(math.degrees(yaw), 2), round(cmd.omega, 3), goes))
                    if cmd.done:
                        break
                    if mark[1] is None or abs(rl.wrap_pi(yaw - mark[1])) >= math.radians(TURN_STILL_DEG):
                        mark = (tick, yaw)
                    elif (tick - mark[0] > TURN_STILL_S
                          and abs(rl.wrap_pi(heading - yaw)) <= math.radians(tol_deg)):
                        # turning on to the angle within what was asked, and the base has
                        # stopped answering: what was asked is met (reviewer: since a turn
                        # under way no longer stops at the allowance, a stall there pushed
                        # 0.15 rad/s for the whole 25 s go)
                        trace.append("still inside the allowance")
                        break
                    prev = 0.0 if v.set(0.0, cmd.omega) else cmd.omega
                    time.sleep(max(0.0, 0.1 - (time.time() - tick)))
                renewals += v.renewals
            if not board_idle:
                self._lingering = time.time()
                self._turn_settle(trace, t0)
            err = rl.wrap_pi(heading - self.fast_pose()[2])
            if abs(err) <= math.radians(tol_deg):
                break
        sign = math.copysign(1.0, rl.wrap_pi(heading - math.radians(trace[0][1]))) if trace else 1.0
        past = max([0.0] + [sign * math.degrees(rl.wrap_pi(math.radians(t_[1]) - heading))
                            for t_ in trace if isinstance(t_, tuple) and len(t_) >= 2])
        self.log(kind="turn", mode="velocity", to_deg=round(math.degrees(heading), 1), off_deg=round(math.degrees(err), 2),
                 goes=goes, seconds=round(time.time() - t0, 1), renewals=renewals, past_deg=round(past, 2),
                 spin=lim.w_spin_fast or lim.w_spin, trace=trace)
        self.say("  turned to %+.0f°, off by %.1f° (%d go%s, %.1f s)"
                 % (math.degrees(heading), math.degrees(err), goes, "" if goes == 1 else "es", time.time() - t0))
        return err

    def _drop_lingering(self) -> None:
        """A spot turn that did not wait for the board leaves its velocity action
        current ~1 s (holding a zero). Before a REST action (MoveTo, MoveBy,
        GoHome) or a change of base.max_moving_speed -- which the board may
        take at an action's start: the next session would take the turn's
        action back, set up under the old cap (reviewer) -- end it and wait."""
        t = getattr(self, "_lingering", None)
        self._lingering = None
        if t is None or time.time() - t > LINGER_S or self.sdk is None:
            return
        from app import slamware_sdk as sw
        try:
            self.sdk.call("cancelaction", {})
            t_end = time.time() + LINGER_S
            while time.time() < t_end:
                cur = self.sdk.call("getcurrentaction")
                if not cur.get("action_type"):
                    return
                time.sleep(0.1)
        except (sw.SdkError, OSError):
            pass

    def _turn_settle(self, trace: list, t0: float) -> None:
        """Until the yaw stops changing (the coast after the zero is over)."""
        t_end = time.time() + TURN_SETTLE_MAX_S
        last = self.fast_pose()[2]
        while time.time() < t_end:
            time.sleep(TURN_SETTLE_DT_S)
            yaw = self.fast_pose()[2]
            if abs(rl.wrap_pi(yaw - last)) < math.radians(TURN_SETTLE_STILL_DEG):
                trace.append((round(time.time() - t0, 2), round(math.degrees(yaw), 2), "settled"))
                return
            last = yaw
        trace.append((round(time.time() - t0, 2), round(math.degrees(last), 2), "settle limit"))

    def drive(self, step: rl.Step) -> None:
        """A straight lane under our own control: one turn, then forward with
        MoveBy re-sent every 0.12 s, and when the heading needed to come back
        onto the lane differs by more than 1.5° one send is a short turn
        (direction 2/3) instead. No planner: the owner watched MoveTo weave
        round chair legs and swing at the end of a leg (6 ต.ค.)."""
        if self.sdk is not None:
            return self.drive_velocity(step)
        x0, y0, _ = self.b.pose()
        tx, ty = step.to
        heading = rl.lane_heading((x0, y0), (tx, ty))
        if heading is None:
            raise Stop("leg %r from (%.2f, %.2f) to (%.2f, %.2f) is not a straight lane" % (step.label, x0, y0, tx, ty))
        self.rotate_to(heading, tol_deg=2.0, tries=2)
        ux, uy = math.cos(heading), math.sin(heading)
        length = (tx - x0) * ux + (ty - y0) * uy
        watcher = ScanWatcher(self.b.http.base_url)
        watcher.start()
        time.sleep(0.5)
        self.b.events()
        self.say("  drive %.2f m to (%.2f, %.2f) on our own" % (length, tx, ty))
        t0 = last_send = last_check = time.time()
        prog_t, prog_rem = t0, length
        cmd, steers, worst, trace = 0, 0, 0.0, []
        reason, last_steer, seen_events = None, 0.0, []
        try:
            while True:
                now = time.time()
                if now - last_send >= 0.12 and watcher.age() < 0.6:
                    self.b.start(MOVE_BY, {"direction": cmd})
                    last_send, cmd = now, 0
                if now - last_check < 0.25:
                    time.sleep(0.03)
                    continue
                last_check = now
                x, y, yaw = self.b.pose()
                rem = (tx - x) * ux + (ty - y) * uy
                lat = -(x - tx) * uy + (y - ty) * ux          # + = left of the lane
                herr = rl.wrap_pi(heading - yaw)
                worst = max(worst, abs(lat))
                front = watcher.front()
                if rem <= DRIVE_STOP_LEAD:
                    break
                if self.fenced(x, y):
                    reason = "entered a forbidden area"
                elif front is not None and front < DRIVE_FRONT_STOP:
                    reason = "something %.2f m ahead in the robot's path" % front
                elif self._drive_events(seen_events, round(now - t0, 2)):
                    reason = "board event %s" % seen_events[-1]["type"]
                elif abs(lat) > 0.15:
                    reason = "%.2f m off the lane" % lat
                elif abs(herr) > math.radians(10):
                    reason = "heading %.1f° off" % math.degrees(herr)
                elif now - t0 > abs(length) / 0.10 + 30:
                    reason = "too slow"
                if prog_rem - rem > 0.01:
                    prog_t, prog_rem = now, rem
                elif now - prog_t > 5.0:
                    # Forward is being sent and our lidar strip is clear: the
                    # board's own sensors (ultrasonic) are holding it.
                    reason = reason or ("the board stopped moving on its own for 5 s (nothing in our lidar strip "
                                        "%s) — its ultrasonic/safety layer?" % ("" if front is None else "≥ %.2f m" % front))
                if reason:
                    trace.append((round(x, 3), round(y, 3), round(math.degrees(yaw), 1), round(lat, 3), "stop"))
                    break
                # Aim at a point DRIVE_LOOKAHEAD ahead on the lane.
                want = rl.wrap_pi(heading - math.atan2(lat, DRIVE_LOOKAHEAD) - yaw)
                if now - last_steer >= DRIVE_STEER_GAP_S and abs(want) > math.radians(DRIVE_STEER_DEG):
                    cmd = 3 if want > 0 else 2                 # 3 = turn left, 2 = turn right
                    steers, last_steer = steers + 1, now
                trace.append((round(x, 3), round(y, 3), round(math.degrees(yaw), 1), round(lat, 3), cmd))
        finally:
            self.b.cancel()
            watcher.stop()
        time.sleep(0.4)
        x, y, yaw = self.b.pose()
        miss = math.hypot(tx - x, ty - y)
        self.log(kind="drive", label=step.label, to=[tx, ty], end=[x, y, yaw], miss=miss,
                 worst_off_lane=round(worst, 3), steers=steers, seconds=round(time.time() - t0, 1),
                 stopped=reason, events=seen_events, trace=trace)
        self.say("  ended at (%.2f, %.2f), %.2f m from target, off the lane at most %.3f m, %d steer sends, %.1f s"
                 % (x, y, miss, worst, steers, time.time() - t0))
        if reason:
            raise Stop("drive %r: %s at (%.2f, %.2f)" % (step.label, reason, x, y))

    def _drive_events(self, seen: list, t: float) -> bool:
        """Read the board's event queue, keep every event for the log, and
        say whether one of them must stop the drive."""
        stop = False
        for e in self.b.events():
            if isinstance(e, dict):
                seen.append({"t": t, "type": e.get("type")})
                stop = stop or e.get("type") in STOP_EVENTS
        return stop

    def pulse(self, seconds: float = 0.8) -> None:
        """One short forward burst: MoveBy is a remote-control move that only
        lasts while it is re-sent. Timed, not counted — four sends from the
        robot's shell (each through a fresh `nc`) took ~0.8 s and moved ~3.5 cm;
        four from here returned in ~0.4 s and moved 7 mm (first undock, 6 ต.ค.)."""
        t0 = time.time()
        while time.time() - t0 < seconds:
            self.b.start(MOVE_BY, {"direction": 0})
            time.sleep(0.12)
        self.b.cancel()
        time.sleep(0.2)

    def undock(self, distance: float = 0.20) -> None:
        """Ease straight forward off the charger before any turn or planned
        move. On 5 ต.ค. a MoveTo from the dock stalled (the planner will not
        start from a cell against the wall), and turning while the contacts
        are still touching scrapes them; the morning of 6 ต.ค. got off the
        same dock with these bursts."""
        x0, y0, _ = self.b.pose()
        for n in range(1, 11):
            self.pulse()
            x, y, _ = self.b.pose()
            moved = math.hypot(x - x0, y - y0)
            if moved >= distance and self.b.power().get("dockingStatus") != "on_dock":
                self.say("  off the dock after %d bursts (%.2f m)" % (n, moved))
                self.log(kind="undock", bursts=n, moved=moved)
                return
        raise Stop("could not ease off the dock (moved %.2f m in 10 bursts)" % moved)

    def sidestep(self, axis: int, delta: float) -> None:
        """Move `delta` metres along world `axis` (0 = x, 1 = y) without the
        planner: turn to face that way, short straight bursts measured by
        odometry, stop within 1 cm. The caller turns back and re-measures."""
        if axis == 0:
            h = 0.0 if delta > 0 else math.pi
        else:
            h = math.pi / 2 if delta > 0 else -math.pi / 2
        self.rotate_to(h, tol_deg=2.0, tries=2)
        start = self.b.pose()
        for _ in range(25):
            moved = abs(self.b.pose()[axis] - start[axis])
            if moved >= abs(delta) - 0.01:
                break
            self.pulse(0.35 if abs(delta) - moved < 0.05 else 0.6)
        moved = self.b.pose()[axis] - start[axis]
        self.say("  sidestep asked %+.3f m, moved %+.3f m" % (delta, moved))
        self.log(kind="sidestep", axis=axis, asked=delta, moved=moved)

    def measure_gap(self) -> dict | None:
        time.sleep(0.6)                        # settle after a turn before reading the lidar
        pts = np.vstack([self.b.scan() for _ in range(2)])
        if len(pts) < 200:
            time.sleep(0.5)
            pts = np.vstack([self.b.scan() for _ in range(2)])
        return rl.gap_ahead(pts)

    def door(self, step: rl.Step) -> None:
        """Through a doorway by what the lidar sees in front, not by the map.

        History (6 ต.ค.): the board's MoveTo yawed to -78° inside D8 and hit
        it; a push aimed by the map's door position hit D11 (aim 3.8 cm off);
        a straight push from 0.9 cm off drifted 3.3 cm in 0.5 m and stopped
        at the jamb; what got through D8 without a touch was: measure the gap
        from the robot, line up, angle toward the middle *before* the frame,
        then straight. That is this: line up by the gap (sidestep if > 5 cm
        off), steer at the gap's middle on the approach, no steering once the
        front reaches the wall, stop on a side reading or the bumper."""
        if self.sdk is not None:
            return self.door_velocity(step)
        door = self.route.doors[step.door]
        heading = math.radians(step.heading_deg)
        across = 0 if door.axis == "x" else 1          # world coordinate we travel along
        lat_axis = 1 - across
        left = (-math.sin(heading), math.cos(heading))  # world direction of robot-frame +lateral
        sign = math.cos(heading) if across == 0 else math.sin(heading)
        for attempt in range(3):
            self.rotate_to(heading, tol_deg=1.0, tries=3)
            g = self.measure_gap()
            if g is None:
                raise Stop("door %s: no opening ahead (closed, or someone standing in it?)" % step.door)
            margin = rl.door_margin(g["width"])
            self.say("  %s ahead at %.2f m: %.3f m wide, middle %+.3f m (+ = left), %.1f cm a side"
                     % (step.door, g["face"], g["width"], g["centre"], margin * 100))
            self.log(kind="door_aim", door=step.door, gap=g, pose=list(self.b.pose()))
            if margin < self.door_min_margin:
                raise Stop("door %s: gap %.3f m leaves %.1f cm a side for a %.2f m robot (need %.1f) — not pushing"
                           % (step.door, g["width"], margin * 100, rl.ROBOT_WIDTH, self.door_min_margin * 100))
            if abs(g["centre"]) <= 0.05:
                break
            if g["face"] < rl.TURN_SWEEP_RADIUS + 0.10:
                raise Stop("door %s: %.2f m off the middle and too close to the wall (%.2f m) to step sideways"
                           % (step.door, g["centre"], g["face"]))
            self.sidestep(lat_axis, g["centre"] * left[lat_axis])
        else:
            raise Stop("door %s: could not line up within 5 cm" % step.door)

        watcher = ScanWatcher(self.b.http.base_url)
        watcher.start()
        time.sleep(0.5)
        self.b.events()
        self.b.set_speed(DOOR_SPEED)
        t0 = last_send = last_check = time.time()
        prog_t, prog_p = t0, self.b.pose()[across]
        cmd, last_steer, steers = 0, 0.0, 0
        entered_yaw, worst, trace, reason = None, [9.0, 9.0], [], None
        try:
            while True:
                now = time.time()
                if now - last_send >= 0.12 and watcher.age() < 0.6:
                    self.b.start(MOVE_BY, {"direction": cmd})
                    last_send, cmd = now, 0
                if now - last_check < 0.25:
                    time.sleep(0.03)
                    continue
                last_check = now
                x, y, yaw = self.b.pose()
                p = (x, y)
                if (p[across] - step.until) * sign >= 0:
                    break
                l, r = watcher.clearance()
                if l is not None:
                    worst[0] = min(worst[0], l)
                if r is not None:
                    worst[1] = min(worst[1], r)
                gap = watcher.gap()
                trace.append((round(x, 3), round(y, 3), round(math.degrees(yaw), 1),
                              None if gap is None else round(gap["centre"], 3),
                              None if gap is None else round(gap["face"], 2), l, r))
                if (l is not None and l < DOOR_SIDE_M) or (r is not None and r < DOOR_SIDE_M):
                    reason = "side too close (L %s, R %s m)" % (
                        "—" if l is None else "%.3f" % l, "—" if r is None else "%.3f" % r)
                elif any(e.get("type") in STOP_EVENTS for e in self.b.events() if isinstance(e, dict)):
                    reason = "board event (bumper / path occupied)"
                elif entered_yaw is not None and abs(rl.wrap_pi(yaw - entered_yaw)) > math.radians(DOOR_TURN_IN_FRAME_DEG):
                    reason = "heading changed %.1f° inside the frame" % math.degrees(rl.wrap_pi(yaw - entered_yaw))
                elif now - t0 > 60:
                    reason = "60 s and still not through"
                if abs(p[across] - prog_p) > 0.01:
                    prog_t, prog_p = now, p[across]
                elif now - prog_t > 4.0:
                    reason = reason or "no progress for 4 s"
                if reason:
                    break
                if entered_yaw is None and gap is not None and gap["face"] > 0.32:
                    # Still outside: point at the middle of the opening.
                    bearing = math.atan2(gap["centre"], gap["face"] + 0.10)
                    if abs(bearing) > math.radians(1.2) and now - last_steer >= 0.5:
                        cmd, last_steer, steers = (3 if bearing > 0 else 2), now, steers + 1
                elif entered_yaw is None:
                    entered_yaw = yaw                       # front at the wall: straight from here
        finally:
            self.b.cancel()
            watcher.stop()
            self.b.set_speed(LANE_SPEED)
        x, y, yaw = self.b.pose()
        self.log(kind="door", door=step.door, ok=reason is None, reason=reason, seconds=round(time.time() - t0, 1),
                 steers=steers, closest=worst, end=[x, y, yaw], trace=trace)
        if reason:
            raise Stop("door %s: stopped at (%.2f, %.2f) — %s" % (step.door, x, y, reason))
        self.say("  through %s in %.1f s at (%.2f, %.2f); %d steer sends; closest sides L %.3f R %.3f m (0.78 m width)"
                 % (step.door, time.time() - t0, x, y, steers, *worst))

    @staticmethod
    def _door_kick(hist: list, kick: float, wait: int, xy, start_xy, heading: float, sent: float,
                   vx: float, info: dict) -> tuple[float, int]:
        """The extra speed for a base that does not move at the speed sent:
        (`kick`, ticks before it may grow again). Grows by DOOR_KICK_STEP when
        the last DOOR_STALL_TICKS all sent at least the creep speed and the
        robot went slower than DOOR_STALL_SPEED; kept while it moves (taking it
        away would stall it again); dropped in the frame's slow zone, with
        anything near ahead, or when the pass asks for less than the creep."""
        along = (xy[0] - start_xy[0]) * math.cos(heading) + (xy[1] - start_xy[1]) * math.sin(heading)
        hist.append((along, sent))
        del hist[:-(DOOR_STALL_TICKS + 1)]
        creep = rl.DOOR_LIMITS.v_creep - 1e-3
        front = info.get("front")
        # only around the creep speed: held at the fast speed, something is
        # holding the base that the lidar may not see (glass, the board's own
        # sensors) -- pushing harder there is the wrong answer
        if (vx < creep or vx > rl.DOOR_LIMITS.v_creep + DOOR_KICK_NEAR_CREEP or info.get("slow")
                or (front is not None and front <= rl.DOOR_LIMITS.front_slow)):
            hist.clear()
            return 0.0, 0
        if len(hist) <= DOOR_STALL_TICKS or min(s for _, s in hist) < creep:
            return kick, wait - 1
        rate = (hist[-1][0] - hist[0][0]) / (DOOR_STALL_TICKS * 0.1)
        if rate < DOOR_STALL_SPEED and wait <= 0:
            return min(kick + DOOR_KICK_STEP, DOOR_KICK_MAX), DOOR_STALL_TICKS
        return kick, wait - 1

    def _step_blocked(self, q: np.ndarray, off: float, slant: float) -> str | None:
        """Why a step `off` across on a `slant` cannot be made from here, by the
        lidar frame `q` (robot frame), or None: the turn out, the way, and the
        turn back where the step ends -- the last was never checked, and a
        slanted step ends nearer what is beside the door (the leaf): the spot
        turn's own check then ended the route there instead of refusing the
        step before anything moved (simulation, 8 ต.ค.)."""
        if len(q) < 200:
            return "no full lidar frame to check the room"
        side = -math.copysign(slant, off)                   # right of the middle: turn left
        coast = math.radians(TURN_COAST_DEG)
        room = rl.turn_sweep_gap(q, side + math.copysign(coast, side), width=self.turn_width)
        if room < TURN_MARGIN_M:
            return "no room here to turn and step sideways (the corners would come %.2f m from something)" % room
        # The step's own way, as its front strip will see it once turned (the
        # doorway's strip and stop distance): 7 ต.ค. 15:53 at D11's turn spot it
        # turned, then stood 20 s before something 0.36 m that way and ended
        # the route. That was D11's leaf tip off the corner, 0.34-0.36 m to
        # the side -- inside a lane's 5 cm strip margin, outside the door's 1 cm.
        c, s = math.cos(-side), math.sin(-side)
        turned = np.c_[q[:, 0] * c - q[:, 1] * s, q[:, 0] * s + q[:, 1] * c]
        run = abs(off) / math.sin(slant)
        way = rl.front_distance(turned, half_width=self.turn_width / 2, margin=STEP_STRIP_MARGIN_M)
        if way is not None and way <= rl.DOOR_LIMITS.front_stop + run:
            return "something %.2f m the way a step would go" % way
        # where it ends, facing along the step: the turn back. A forecast from
        # here, a step away -- the turn itself checks again on a fresh frame
        # there (`turn_space_clear`); this only refuses what clearly will not
        # turn. D11's turn spot (7 ต.ค., real frame) forecasts 0.0399 for a
        # 3.7 cm step that turned back fine on the day.
        # A slant is a choice -- square is there if it will not do -- so it goes
        # only where the forecast clears the real check's margin: one at 3.7 cm
        # turned out, could not turn back, and stood 35° to the door (reviewer).
        back = rl.turn_sweep_gap(turned - (run, 0.0), -side - math.copysign(coast, side), width=self.turn_width)
        if back < (TURN_MARGIN_M if slant < math.pi / 2 else TURN_MARGIN_M - 0.01):
            return ("no room to turn back where the step ends (the corners would come %.2f m from something)"
                    % back)
        return None

    def step_across(self, step: rl.Step, heading: float, off: float, face: float | None = None) -> None:
        """Sideways onto a door's line, out in front of it: turn toward it,
        drive the measured offset (`off`, + = left of the middle), turn back,
        and take the heading again at the door's own checkpoint -- a spot turn
        on this base came out 6° and 12° from what odometry said after two left
        turns on 7 ต.ค. (the walls at the next stops agreed, three at a time).
        On a slant of DOOR_STEP_ANGLE_DEG when `face` (the door's wall line
        ahead, m) leaves room for its forward part; square across otherwise.
        Refused (`StepBlocked`), before anything moves, where the turn has no
        room or the step's way is not clear."""
        pts = self.b.scan()
        if len(pts) < 200:                                  # a short frame is not "no room": read again
            time.sleep(0.3)
            pts = np.vstack([np.asarray(pts, dtype=float).reshape(-1, 2), self.b.scan()])
        q = np.asarray(pts, dtype=float).reshape(-1, 2)
        why = None
        slants = [math.radians(DOOR_STEP_ANGLE_DEG), math.pi / 2]
        if face is None or face - abs(off) / math.tan(slants[0]) < DOOR_STEP_MIN_FACE_M:
            slants = slants[1:]
        for slant in slants:
            why = self._step_blocked(q, off, slant)
            if why is None:
                break
        if why is not None:
            raise StepBlocked("door %s: %+.3f m off the opening's middle, and %s" % (step.door, off, why))
        ahead = 0.0 if slant == math.pi / 2 else abs(off) / math.tan(slant)
        x, y, _ = self.fast_pose()
        nx, ny = -math.sin(heading), math.cos(heading)
        to = (x - off * nx + ahead * math.cos(heading), y - off * ny + ahead * math.sin(heading))
        self.say("  %+.3f m off %s's middle: stepping sideways%s" % (
            off, step.door, "" if ahead == 0.0 else " on a %.0f° slant" % math.degrees(slant)))
        # direct: a step can be 2-5 cm now (the near side must keep 4 cm), under
        # a lane's 5 cm minimum -- 7 ต.ค. 14:08 a 3.6 cm step was refused as
        # "not a straight lane". It is axis-aligned by construction. Stop
        # tolerance 1 cm, not a lane's 2: the step is the whole point.
        # The doorway's stop distances, not a lane's: 7 ต.ค. 15:14 inside the big
        # room a 3.6 cm step north had D11's open leaf 0.47 m ahead, under the
        # lane's 0.50 -- held where it stood, and it went on to the door as if
        # stepped. The base would have ended 13 cm from the leaf.
        base = self.limits_for(direct=True)
        self.drive_velocity(rl.Step(do="drive", to=to, label="sideways onto %s's line" % step.door), direct=True,
                            front_margin=STEP_STRIP_MARGIN_M, limits=dataclasses.replace(
                                base, stop_tol=0.01, v_creep=rl.DOOR_LIMITS.v_creep,
                                front_stop=min(base.front_stop, rl.DOOR_LIMITS.front_stop),
                                front_slow=min(base.front_slow, rl.DOOR_LIMITS.front_slow)))
        self.rotate_to(heading, tol_deg=SPOT_TURN_TOL_DEG)
        cp = getattr(self, "door_checkpoint", None)
        if cp is not None:
            self.checkpoint(cp)
        self.log(kind="door_step_across", door=step.door, off=round(off, 3), to=[round(to[0], 3), round(to[1], 3)],
                 slant_deg=round(math.degrees(slant), 1), checkpoint=None if cp is None else cp.label)

    def door_velocity(self, step: rl.Step, resume: bool = False, skip_lineup: bool = False) -> None:
        """Through a doorway under velocity control (`robot_lanes.DoorPass`):
        across-the-door position and heading from the lidar -- the opening
        ahead, the edge or edges beside, walls along and across the way -- not
        from odometry; steering in single pulses just past the casters'
        breakaway; 0.06 m/s; never turning on the spot inside. Simulated
        through D8 as measured (tests/test_robot_door.py) before it ran here.
        The robot must arrive facing the door; off its line by more than
        DOOR_LINEUP_M it steps sideways first (`step_across`), out here where
        it can still turn -- next to a leaf or inside a frame it cannot.
        `resume`: the link dropped with the robot already at the frame (14:11,
        D8: through it, 0.2 m past the wall line); no aim -- the face is behind
        or beside it -- the pass goes on from the lidar beside it."""
        from app import slamware_sdk as sw
        door = self.route.doors[step.door]
        heading = math.radians(step.heading_deg)
        mid = (door.lo + door.hi) / 2.0
        middle = (mid, door.at) if door.axis == "y" else (door.at, mid)
        lat_axis = "x" if door.axis == "y" else "y"     # the world coordinate across this door
        width_hint = door.hi - door.lo
        # Where odometry puts the door's middle, once both its edges have been
        # seen (lat_map - measured). After a step sideways odometry is off the
        # route's door by the step itself; without this a one-edge reading
        # that agrees with the door would be overruled by the lane and the
        # robot sent straight back where it came from.
        door_corr = self._door_remembered(step.door, heading, lat_axis)
        # Where this door's own passes found its middle, this way through (a
        # `remember` step, DOOR_MEM_*): the aim's lane is read against it, not
        # the drawing's. The run's own memory above, when it holds, knows better
        # (this run's odometry); a middle measured now, both edges, better still.
        prior, prior_info = None, None
        if door_corr is None and getattr(step, "remember", False):
            prior, prior_info = self._door_prior(step, door, heading, lat_axis)
            if prior is not None:
                self.say("  (%s's middle as its passes found it: %s %.3f, %+.1f cm from the drawing's -- %s)"
                         % (prior_info["key"], lat_axis, prior_info["middle"], (prior_info["middle"] - mid) * 100,
                            prior_info["why"]))
            else:
                self.say("  (%s: no remembered middle -- %s)" % (prior_info["key"], prior_info["why"]))
        aim_since = None
        shifts = 0
        g = None
        steer_in = False
        while not resume:
            turned = abs(rl.wrap_pi(heading - self.fast_pose()[2])) > math.radians(SPOT_TURN_TOL_DEG)
            self.rotate_to(heading, tol_deg=SPOT_TURN_TOL_DEG)
            # settle after a turn before reading the lidar; straight off a lane
            # that already faces the door (D8 out) there is nothing to settle
            time.sleep(0.6 if turned else 0.15)
            x, y, yaw_aim = self.fast_pose()
            face_near = (middle[0] - x) * math.cos(heading) + (middle[1] - y) * math.sin(heading)
            # The opening is read square to the door, not to the robot: the turn
            # before the aim stops within 2°, and at the frame's distance each
            # degree is 1.4 cm across at D11 (0.8 m), 3.3 at D8 from the room (1.9
            # m). 8 ต.ค., D11 in, both runs: the south jamb sat at y -1.119..-1.129
            # in the world through all three aims, but read -3.4 / -2.9 cm off,
            # then +2.3 / +3.5 the other way after a 3 cm step -- the heading had
            # gone from -1.0° / -0.7° to +0.2° / +1.4°. A second step back each time.
            rot = rl.wrap_pi(yaw_aim - heading)
            c_r, s_r = math.cos(rot), math.sin(rot)
            # Look again for a while before calling it closed: 15:32, D8 from the
            # corridor, three looks in a second found nothing and the route ended;
            # the next look, a minute later, found the 0.857 m passage -- someone
            # had been in the doorway.
            g, t_look, said = None, time.time(), False
            while True:
                pts = self.b.scan()
                q_ = np.asarray(pts, dtype=float).reshape(-1, 2)
                pts = np.c_[q_[:, 0] * c_r - q_[:, 1] * s_r, q_[:, 0] * s_r + q_[:, 1] * c_r]
                g = rl.opening_ahead(pts, face_near, width_hint) if len(pts) >= 200 else None
                if g is not None or time.time() - t_look > DOOR_FIND_WAIT_S:
                    break
                if not said:
                    self.say("  %s: no opening seen yet -- looking again (up to %.0f s)" % (step.door, DOOR_FIND_WAIT_S))
                    said = True
                time.sleep(0.4)
            if g is None:
                # what was there, for the next look at why (16:03: no record of it)
                q = np.asarray(pts, dtype=float).reshape(-1, 2)
                near = q[(q[:, 0] > 0.2) & (q[:, 0] < face_near + 0.6) & (np.abs(q[:, 1]) < 0.9)]
                self.log(kind="door_not_found", door=step.door, face_near=round(face_near, 3), pose=list(self.fast_pose()),
                         squared_deg=round(math.degrees(rot), 2), points=np.round(near, 3).tolist())
                raise Stop("door %s: no opening found %.2f m ahead in %.0f s (closed, or someone standing in it?)"
                           % (step.door, face_near, DOOR_FIND_WAIT_S))
            margin = rl.door_margin(g["width"], self.door_width)
            self.say("  %s ahead at %.2f m: %.3f m wide (%s), middle %+.3f m (+ = left), %.1f cm a side"
                     % (step.door, g["face"], g["width"], "both edges" if g["edges"] == 2 else "one edge + drawing",
                        g["centre"], margin * 100))
            # how far back the coordinate across the door was measured: which of
            # edge and lane knows better, and whether this pass may be remembered
            at = (getattr(self, "measured_at", None) or {}).get(lat_axis)
            since = math.hypot(x - at[0], y - at[1]) if at is not None else None
            aim_since = since
            self.log(kind="door_aim", door=step.door, gap=g, pose=list(self.fast_pose()),
                     squared_deg=round(math.degrees(rot), 2),
                     prior=None if prior is None else round(prior, 4))
            if margin < self.door_min_margin:
                raise Stop("door %s: gap %.3f m leaves %.1f cm a side for a %.2f m robot (need %.1f) -- not going in"
                           % (step.door, g["width"], margin * 100, self.door_width, self.door_min_margin * 100))
            # where the lane put us across the door, by the route's door and odometry
            lat_map = (x - middle[0]) * -math.sin(heading) + (y - middle[1]) * math.cos(heading)
            off = -g["centre"]
            if g["edges"] == 2:
                door_corr = lat_map - off
            else:
                # against the remembered middle when there is one (and no fresher word)
                lat_odo = lat_map - (door_corr if door_corr is not None else (prior or 0.0))
                stale = since is None or since > LATERAL_FRESH_M
                back = "never" if since is None else "%.1f m back" % since
                if stale and "frame_centre" in g:
                    # Both jambs seen, wider than the passage: which one bounds the
                    # passage is a guess that assumes the robot came lined up (7 ต.ค.
                    # 13:19, D11: the guess said 6.7 cm, the robot was ~19 cm off).
                    # The frame's middle is measured, consistent from stop to stop
                    # to 1-2 cm, and within a few cm of the passage's.
                    off = -g["frame_centre"]
                    by = "the frame's middle"
                    if door.leaf is not None:
                        # The leaf narrows one side past the frame; the other jamb is the
                        # passage's edge. Which of the robot's sides the leaf is on: the
                        # robot's left points along +lat when that normal is positive.
                        left_sign = math.cos(heading) if door.axis == "x" else -math.sin(heading)
                        leaf_left = (door.leaf == "hi") == (left_sign > 0)
                        off = -(g["frame_lo"] + width_hint / 2.0 if leaf_left else g["frame_hi"] - width_hint / 2.0)
                        by = "the %s jamb (the leaf is on the %s)" % ("right" if leaf_left else "left",
                                                                     "left" if leaf_left else "right")
                    self.say("  (both jambs seen, %.3f m apart: going by %s, %+.3f m off; the lane "
                             "said %+.3f, its %s measured %s)" % (g["frame_width"], by, off, lat_odo, lat_axis, back))
                    self.log(kind="door_frame_overrules_lane", door=step.door, frame_off=round(off, 3),
                             lane_off=round(lat_odo, 3), measured_back=None if since is None else round(since, 2))
                    door_corr = lat_map - off
                elif door_corr is None and stale and abs(off - lat_odo) > ONE_EDGE_OVERRULE_M:
                    self.say("  (one edge puts us %+.3f m off, the lane %+.3f; odometry's %s was last measured %s "
                             "-- going by the edge)" % (off, lat_odo, lat_axis, back))
                    self.log(kind="door_edge_overrules_lane", door=step.door, edge_off=round(off, 3),
                             lane_off=round(lat_odo, 3), measured_back=None if since is None else round(since, 2))
                    door_corr = lat_map - off
                else:
                    if abs(off - lat_odo) > 0.005:
                        self.say("  (one edge seen from here puts us %+.3f m off; the lane says %+.3f -- trusting the "
                                 "lane until the frame is beside us)" % (off, lat_odo))
                    off = lat_odo
            lineup = min(DOOR_LINEUP_M, max(DOOR_LINEUP_MIN_M, margin - DOOR_ENTRY_KEEP_M))
            if skip_lineup:
                # after a block: the step across was measured on what held it,
                # closer and more direct than this aim -- do not undo it
                self.say("  (in again after a block: %+.3f m by this aim, not stepping again)" % off)
                break
            # after a step the aim is a new reading of a new place: 8 ต.ค. 13:12 at
            # D11 a 3.4 cm step left 2.3 cm the other way by the jamb (the step
            # itself ended 1 cm on; the middle read 2.5 cm apart from 0.79 and 0.69
            # m) -- 3 mm over the line-up, 12 s for a second step back
            # ...only where the near side would still keep DOOR_STEER_KEEP_M: at D11
            # (3 cm a side) 2.6 cm off would leave 4 mm, under 13:55's 5 (reviewer)
            slack = (DOOR_RESTEP_SLACK_M if shifts and margin - (lineup + DOOR_RESTEP_SLACK_M) >= DOOR_STEER_KEEP_M
                     else 0.0)
            if abs(off) <= lineup + slack + 0.001:  # a millimetre: finer than anything here measures
                break
            # Room to curve onto the line on the way (the approach steers as a
            # lane does, `robot_lanes.DoorPass`): no stop and step sideways.
            run = g["face"] - (rl.ROBOT_BASE_LENGTH / 2.0 + rl.DOOR_SLOW_BEFORE_M)
            at_frame = abs(off) * math.exp(-DOOR_STEER_IN_RATE * max(run - DOOR_STEER_IN_SETTLE_M, 0.0))
            if (abs(off) <= DOOR_STEER_IN_MAX_M and margin >= DOOR_STEER_IN_MIN_MARGIN_M
                    and run >= DOOR_STEER_IN_MIN_RUN_M and at_frame <= lineup + 0.001):
                self.say("  %+.3f m off %s's middle, %.2f m to curve onto its line on the way in (~%.1f cm "
                         "left at the frame) -- no step" % (off, step.door, run, at_frame * 100))
                self.log(kind="door_steer_in", door=step.door, off=round(off, 3), run=round(run, 3),
                         at_frame=round(at_frame, 3))
                steer_in = True
                break
            if abs(off) > DOOR_SHIFT_MAX_M:
                raise Stop("door %s: %+.3f m off the opening's middle -- too far to be this door's opening; "
                           "fix the approach" % (step.door, off))
            if shifts >= DOOR_SHIFT_TRIES:
                raise Stop("door %s: %+.3f m off the opening's middle after %d steps sideways"
                           % (step.door, off, shifts))
            try:
                self.step_across(step, heading, off, face=g["face"])
            except StepBlocked as e:
                # 7 ต.ค. 15:53: a 3.7 cm step at D11's turn spot ended the route. That
                # much the pass steers on the way in -- it starts from the same
                # offset and goes by the jambs once they are beside it.
                # ...only where, uncorrected, the near side would still keep
                # DOOR_STEER_KEEP_M: 13:55 at D8 1.9 cm went to 5 mm in the frame.
                if (abs(off) > DOOR_STEER_ONLY_M or g["face"] < DOOR_STEER_FACE_M
                        or margin - abs(off) < DOOR_STEER_KEEP_M):
                    raise
                self.say("  (%s -- steering the %.1f cm on the way in instead)" % (e, abs(off) * 100))
                self.log(kind="door_step_blocked", door=step.door, off=round(off, 3), face=round(g["face"], 3),
                         why=str(e))
                break
            shifts += 1
        # Odometry well off the door (a step sideways, or a lane that drifted):
        # start the pass from where the two edges put the door, not from the
        # route's, or its first ticks lean toward the wrong middle.
        # ...and from the remembered middle when nothing fresher said where it is:
        # with the drawing's, D8 in leant toward the west jamb from the first tick
        ref = door_corr if door_corr is not None else prior
        seed = ref if ref is not None and abs(ref) > rl.ONE_EDGE_TRUST_M else None
        dp = rl.DoorPass(middle=middle, heading=heading, until=step.until,
                         width=g["width"] if g is not None and g["edges"] == 2 else None, width_hint=width_hint,
                         robot_width=self.door_width, corr=seed, steer_in=steer_in)
        watcher = self.watcher()
        watcher.start()
        time.sleep(0.6)
        self.b.events()
        t0, prev, trace, reason, lost = time.time(), (0.0, 0.0), [], None, None
        blocked = None
        start_xy = self.fast_pose()[:2]
        prog_t, prog_rem = t0, None
        closest = [9.0, 9.0]
        hist, kick, kick_wait = [], 0.0, 0          # (along, sent vx) the last ticks; the stall kick
        try:
            try:
                with sw.VelocitySession(self.sdk) as v:
                    while True:
                        tick = time.time()
                        if tick - t0 > 120:
                            reason = "120 s and still not through"
                            break
                        x, y, yaw = self.fast_pose()
                        if watcher.age() > 0.6:
                            # no fresh lidar: stand still (a zero keeps the action alive)
                            v.set(0.0, 0.0)
                            prev = (0.0, 0.0)
                            time.sleep(0.1)
                            continue
                        pts_now = watcher.points()
                        cmd, info = dp.update((x, y, yaw), pts_now, prev)
                        kick, kick_wait = self._door_kick(hist, kick, kick_wait, (x, y), start_xy, heading,
                                                          prev[0], cmd.vx, info)
                        left, right = info["sides"]          # the box: where the jambs are across
                        # the gap to the base's rounded outline: what the stop goes by
                        s_left, s_right = rl.side_clearance(pts_now, half_width=self.door_width / 2.0, shaped=True)
                        for i, side in enumerate((s_left, s_right)):
                            if side is not None:
                                closest[i] = min(closest[i], side)
                        trace.append((round(tick - t0, 2), round(x, 3), round(y, 3), round(math.degrees(yaw), 1),
                                      round(math.degrees(info["yaw_est"]), 1),
                                      None if info["lat_meas"] is None else round(info["lat_meas"], 3),
                                      None if info["corr"] is None else round(info["corr"], 3),
                                      None if left is None else round(left, 3),
                                      None if right is None else round(right, 3),
                                      round(cmd.vx, 3), round(cmd.omega, 3), int(info["slow"]),
                                      None if s_left is None else round(s_left, 3),
                                      None if s_right is None else round(s_right, 3), round(kick, 3)))
                        if cmd.done:
                            break
                        sides = [side for side in (s_left, s_right) if side is not None]
                        turned = (None if dp.entered_yaw is None
                                  else abs(rl.wrap_pi(info["yaw_est"] - dp.entered_yaw)))
                        off_axis = abs(rl.wrap_pi(yaw - heading))
                        if sides and min(sides) < DOOR_SIDE_STOP_M:
                            reason = "side %.3f m" % min(sides)
                        elif off_axis > math.radians(DOOR_MAX_OFF_AXIS_DEG):
                            # by odometry, whatever the walls say: 7 ต.ค. 11:02 it reached
                            # 22.8° on the approach, where no guard looked at the heading
                            reason = "heading %.1f° off the way through" % math.degrees(off_axis)
                        elif turned is not None and turned > rl.frame_turn_limit((left, right)):
                            reason = "heading changed %.1f deg inside the frame (sides L %s R %s)" % (
                                math.degrees(turned), "-" if left is None else "%.3f" % left,
                                "-" if right is None else "%.3f" % right)
                        elif self.fenced(x, y):
                            reason = "entered a forbidden area"
                        elif self._drive_events([], round(tick - t0, 2)):
                            reason = "board event (bumper / path occupied)"
                        elif v.renewals > 4:
                            reason = "the link stalled past the board's watchdog %d times" % v.renewals
                        if prog_rem is None or prog_rem - cmd.rem > 0.01:
                            prog_t, prog_rem = tick, cmd.rem
                        else:
                            if tick - prog_t > DOOR_BLOCK_S and blocked is None:
                                need = rl.strip_edge(pts_now, half_width=self.door_width / 2.0)
                                if need is not None:
                                    blocked = need
                                    reason = reason or ("held by something ahead on one side -- %+.3f m across "
                                                        "clears it" % need)
                            if tick - prog_t > 8.0:
                                reason = reason or "held for 8 s (something %s ahead)" % (
                                    "?" if info["front"] is None else "%.2f m" % info["front"])
                        if reason:
                            break
                        vx = cmd.vx + kick if cmd.vx > 0 else cmd.vx
                        prev = (0.0, 0.0) if v.set(vx, cmd.omega) else (vx, cmd.omega)
                        time.sleep(max(0.0, 0.1 - (time.time() - tick)))
            except (Stop, sw.SdkError, OSError) as e:
                lost = str(e)
        finally:
            watcher.stop()
        time.sleep(0.4)
        x, y, yaw = self.fast_pose()
        self.log(kind="door", mode="velocity", door=step.door, ok=reason is None and lost is None, reason=reason,
                 lost_link=lost, width=None if g is None else g["width"], resumed=resume, closest=closest,
                 end=[x, y, yaw],
                 seconds=round(time.time() - t0, 1), trace=trace)
        if lost:
            raise LinkLost("door %s: link lost in the doorway (%s) -- the board stops itself within ~1 s"
                       % (step.door, lost))
        if reason and blocked is not None:
            raise DoorBlocked("door %s: at (%.2f, %.2f) %s" % (step.door, x, y, reason), blocked,
                              math.hypot(x - start_xy[0], y - start_xy[1]))
        if reason:
            raise Stop("door %s: stopped at (%.2f, %.2f) -- %s" % (step.door, x, y, reason))
        self.say("  through %s in %.1f s, now at (%.2f, %.2f); closest sides L %.3f R %.3f m (%.2f m width)"
                 % (step.door, time.time() - t0, x, y, *closest, self.door_width))
        if dp.corr is not None:
            # where the jambs put this door's middle, in odometry's frame, for the
            # way back through it (`_door_remembered`)
            nx, ny = -math.sin(heading), math.cos(heading)
            if not hasattr(self, "door_seen"):
                self.door_seen = {}
            self.door_seen[step.door] = {"offset": (dp.corr * nx, dp.corr * ny), "lat_axis": lat_axis,
                                         "measured": (getattr(self, "measured_at", None) or {}).get(lat_axis)}
        if not resume:
            self._remember_middle(step, door, heading, lat_axis, trace, aim_since)

    def _door_remembered(self, door: str, heading: float, lat_axis: str) -> float | None:
        """Where the last pass through this door found its middle, across the
        way it is about to go (`door_corr`), while no checkpoint has measured
        that coordinate since -- odometry's frame is still the one the jambs
        were measured in. 7 ต.ค., D11 out, three runs: the way in had centred
        the robot on the jambs 4-4.5 cm south of the route's middle, the turn
        round left it there, and the lane -- the route's middle -- said "4.5 cm
        off, step sideways" every time (~17 s each)."""
        mem = (getattr(self, "door_seen", None) or {}).get(door)
        if mem is None or mem["lat_axis"] != lat_axis:
            return None
        if (getattr(self, "measured_at", None) or {}).get(lat_axis) != mem["measured"]:
            return None
        ox, oy = mem["offset"]
        return ox * -math.sin(heading) + oy * math.cos(heading)

    def _door_prior(self, step: rl.Step, door: rl.Door, heading: float,
                    lat_axis: str) -> tuple[float | None, dict]:
        """A `remember` door step's middle across the way (DOOR_MEM_*), as the
        aim's `door_corr` (+ = the robot's left of the drawing's), or None --
        with what it came from: the route's seed for this door and way, then
        the passes in --door-memory under DOOR_MEM_MAX_AGE_S old and measured
        in this route's frame, oldest first."""
        key = rl.door_key(step.door, step.heading_deg)
        drawn = (door.lo + door.hi) / 2.0
        seed = list(self.route.door_seed.get(key, ()))
        learned = []
        path = getattr(self, "door_memory_path", None)
        doc = load_door_memory(path) if path is not None else None
        if doc is not None:
            fid, now = route_frame_id(self.route), time.time()
            learned = [float(s["at"]) for s in doc["doors"].get(key, [])
                       if s.get("frame") == fid and 0.0 <= now - s["t"] <= DOOR_MEM_MAX_AGE_S]
        mid, why = door_middle_prior(seed + learned, drawn)
        info = {"key": key, "middle": None if mid is None else round(mid, 4), "why": why,
                "seed": len(seed), "learned": len(learned)}
        self.log(kind="door_prior", door=step.door, **info)
        if mid is None:
            return None, info
        return (mid - drawn) * _n_comp(heading, lat_axis), info

    def _remember_middle(self, step: rl.Step, door: rl.Door, heading: float, lat_axis: str,
                         trace: list, since: float | None) -> None:
        """After a pass through a `remember` door with --door-memory: where the
        jambs beside put its middle (`door_middle_sample`), added to this door
        and way's own memory -- only from both jambs at once, with the
        coordinate across fresh at the aim, near the drawing (DOOR_MEM_*). A
        memory file that does not parse is never written over."""
        path = getattr(self, "door_memory_path", None)
        if path is None or not getattr(step, "remember", False):
            return
        key = rl.door_key(step.door, step.heading_deg)
        drawn = (door.lo + door.hi) / 2.0
        at, ticks = door_middle_sample(trace, lat_axis, _n_comp(heading, lat_axis), self.door_width)
        why = None
        if at is None:
            why = "the jambs beside both at once for %d tick(s), %d needed" % (ticks, DOOR_MEM_MIN_TICKS)
        elif since is None or since > LATERAL_FRESH_M:
            why = "its %s was measured %s before the aim" % (lat_axis, "never" if since is None else "%.1f m" % since)
        elif abs(at - drawn) > DOOR_MEM_MAX_FROM_DRAWING_M:
            why = "%.1f cm from the drawing's middle -- not this door's" % (abs(at - drawn) * 100)
        stored = False
        if why is None:
            doc = load_door_memory(path)
            if doc is None:
                why = "the memory file does not parse -- left as it is"
            else:
                rows = doc["doors"].setdefault(key, [])
                rows.append({"at": round(at, 4), "t": round(time.time(), 1), "ticks": ticks,
                             "run": getattr(self, "log_name", None), "frame": route_frame_id(self.route)})
                del rows[:-DOOR_MEM_KEEP]
                stored = save_door_memory(path, doc)
                why = None if stored else "not written"
        self.log(kind="door_middle", door=step.door, key=key, at=None if at is None else round(at, 4),
                 ticks=ticks, stored=stored, why=why)
        self.say("  %s's middle by the jambs beside: %s%s" % (
            key, "-" if at is None else "%s %.3f" % (lat_axis, at),
            " (remembered)" if stored else " (not remembered: %s)" % why))

    def approach_landing(self) -> None:
        """Put the robot where GoHome starts backing in (`LANDING_M` out from
        the board's dock marker), back to the charger, so GoHome has nothing
        to do but reverse. Driven straight at the spot from wherever the last
        lane ended — which must be short of it: there is no reversing here."""
        try:
            hx, hy, hyaw = self.b.home_pose()
        except (httpx.HTTPError, KeyError, TypeError, ValueError, Stop) as e:
            self.say("  (no dock marker from the board: %s — GoHome finds its own way in)" % e)
            if self.route.dock_pose is not None:
                self.turn_velocity(self.route.dock_pose[2], board_idle=True)
            return
        lx, ly = hx + LANDING_M * math.cos(hyaw), hy + LANDING_M * math.sin(hyaw)
        x, y, yaw = self.fast_pose()
        inward = rl.wrap_pi(hyaw + math.pi)                       # facing the charger
        ahead = (lx - x) * math.cos(inward) + (ly - y) * math.sin(inward)
        off = math.hypot(lx - x, ly - y)
        if ahead > 0.03 and off > 0.03:
            self.drive_velocity(rl.Step(do="drive", to=(lx, ly), label="to the dock's landing spot"), direct=True)
        elif ahead <= -0.03:
            self.say("  %.2f m past the landing spot — GoHome will drive back to it" % -ahead)
        self.turn_velocity(hyaw, board_idle=True)
        x, y, _ = self.fast_pose()
        self.log(kind="landing", spot=[round(lx, 3), round(ly, 3)], at=[round(x, 3), round(y, 3)],
                 off=round(math.hypot(lx - x, ly - y), 3))
        self.say("  at the landing spot (%.2f, %.2f), %.2f m from it" % (lx, ly, math.hypot(lx - x, ly - y)))

    def back_into_dock(self) -> bool:
        """Forward to the pre-dock spot on the dock's own line, turn round, and
        reverse straight in under velocity control — heading held in single
        pulses (the door's way), leaning gently back onto the line — until the
        board reports on_dock. Stops short on a bumper event, a heading change
        past 4°, or `BACK_OVERSHOOT_M` past where a docked robot sits. The
        lidar does not see behind; this is the dock approach the robot itself
        just came along. Returns whether it is on the dock.
        DOCK_TRIES goes: one that makes no contact drives back out to the
        pre-dock spot and backs in again (7 ต.ค. 14:47: 5 cm from where it
        docks the tail met the charger off-centre and the robot swung 3.5°;
        the heading guard stopped it, and GoHome, then the fallback, spun
        254° and left it on the contacts at ~63° -- the owner docked it)."""
        reason = None
        if getattr(self, "dock_blocked", False):
            # A link drop after a block retries the dock step (run_step): not
            # into the same column again (reviewer: LINK_STEP_RETRIES of them).
            raise Stop("blocked backing in earlier this run -- not backing in again; standing by the charger",
                       code="dock_blocked")
        for attempt in range(1, DOCK_TRIES + 1):
            docked, reason = self._back_in_once(out_first=attempt > 1)
            if docked:
                return True
            if getattr(self, "dock_blocked", False):
                break                      # the same line would meet it again (BACK_EASE_M)
            if attempt < DOCK_TRIES:
                self.say("  not on the dock (%s) -- out to the pre-dock spot, once more" % reason)
        self.dock_failure = reason
        return False

    def _dock_gap(self, hx: float, hy: float, hyaw: float, where: str) -> dict | None:
        """Facing the dock: the pose from the charger's surroundings (route
        `dock_gap`, rl.dock_gap_fix) -- y across the dock's line, which nothing
        else in that room measures, and heading and x from the wall behind it.
        Written to the board's pose; None (logged) when not facing it, out of
        range, or the gap is not plainly there -- then odometry's word stands."""
        gap = self.route.dock_gap
        x, y, yaw = self.b.pose()
        off = rl.wrap_pi(yaw - (hyaw + math.pi))
        dist = x - gap.wall_x
        line_y = hy + (gap.wall_x + (gap.face_from + gap.face_to) / 2.0 - hx) * math.tan(hyaw)
        if abs(off) > math.radians(DOCK_GAP_FACING_DEG) or not DOCK_GAP_RANGE_M[0] <= dist <= DOCK_GAP_RANGE_M[1]:
            self.log(kind="dock_gap", where=where, pose=[x, y, yaw], fix=None,
                     skipped="%.0f° off facing the dock, %.2f m from its wall" % (math.degrees(off), dist))
            return None
        ux, uy = math.cos(hyaw), math.sin(hyaw)
        dock = (hx + DOCKED_M * ux, hy + DOCKED_M * uy)
        half = self.turn_width / 2.0 + 0.03
        fix, in_path, t0, why = None, 0, time.time(), []
        while True:
            pose0 = self.b.pose()
            pts = np.vstack([rl.scan_points(self.b.raw_scan()) for _ in range(2)])
            x, y, yaw = self.b.pose()
            if math.hypot(x - pose0[0], y - pose0[1]) > 0.003 or abs(rl.wrap_pi(yaw - pose0[2])) > math.radians(0.2):
                if time.time() - t0 > DOCK_PATH_WAIT_S:   # never still: no look (reviewer: this waited forever)
                    why = ["still moving"]
                    break
                time.sleep(0.3)                         # still moving: these frames are not one look
                continue
            why = []
            fix = rl.dock_gap_fix(pts, (x, y, yaw), gap, line_y, why=why)
            in_path = 0
            if where.startswith("pre-dock"):
                # what stands between the robot's front and the charger, inside
                # the width it backs in with (fix applied when there is one)
                fx, fy, fw = (x, y, yaw) if fix is None else (x + fix["dx"], y + fix["dy"], yaw + fix["dyaw"])
                c, s_ = math.cos(fw), math.sin(fw)
                wx, wy = fx + c * pts[:, 0] - s_ * pts[:, 1], fy + s_ * pts[:, 0] + c * pts[:, 1]
                along = (wx - dock[0]) * ux + (wy - dock[1]) * uy
                across = -(wx - dock[0]) * uy + (wy - dock[1]) * ux
                front = (fx - dock[0]) * ux + (fy - dock[1]) * uy - rl.ROBOT_BASE_LENGTH / 2.0
                in_path = int(np.sum((along > -0.25) & (along < front - 0.02) & (np.abs(across) <= half)))
            if in_path < 5 and fix is not None:
                break
            if time.time() - t0 > (DOCK_PATH_WAIT_S if in_path >= 5 else 0.6):
                break
            time.sleep(0.3)
        # The look itself, so a "no gap" can be replayed (rl.dock_gap_fix with this
        # pose): 9 ต.ค. both looks of the round trip said "no gap", nothing of what
        # they saw was kept, and 8 ต.ค.'s frames re-seen from those two poses find
        # the gap 11/11 -- the scene, not the code, and no way to see what. The
        # returns ahead within reach of the dock's wall (robot frame, mm): the
        # wall, the faces and anything in front of them, ~1-2k numbers a look.
        ahead = pts[(pts[:, 0] > 0.0) & (np.hypot(pts[:, 0], pts[:, 1]) <= DOCK_GAP_RANGE_M[1] + 0.6)]
        self.log(kind="dock_gap", where=where, pose=[x, y, yaw], line_y=round(line_y, 4), in_path=in_path,
                 fix=None if fix is None else {k: round(float(v), 4) for k, v in fix.items()},
                 why=why[-1] if fix is None and why else None,
                 points=np.round(ahead, 3).tolist() if len(pts) else None)
        if fix is None and where.startswith("pre-dock") and why and why[-1].startswith("far"):
            # the gap plainly there but further than odometry is ever out: backing
            # in on odometry here is backing in that far off (reviewer: 6 cm)
            raise Stop("%s -- odometry that far out is not something to back in on" % why[-1][5:],
                       code="dock_blocked")
        if in_path >= 5:
            raise Stop("%d returns between the robot and the charger, in the way it backs in -- not backing in"
                       % in_path, code="dock_blocked")
        if fix is None:
            # the reason on the console too: 9 ต.ค. both looks said only "not found",
            # and the why stayed in a log that was still on the robot
            self.say("  dock gap not found (%s: %s) -- across the line on odometry's word"
                     % (where, why[-1] if why else "no look"))
            return None
        # onto the pose as it is now (the robot is still; were it not, a pose
        # read before the frames would undo whatever it moved -- reviewer)
        cx, cy, cyaw = self.b.pose()
        self.b.set_pose((cx + fix["dx"], cy + fix["dy"], rl.wrap_pi(cyaw + fix["dyaw"])))
        self.say("  dock gap (%s): %+.1f cm across, %+.1f cm along, %+.1f°" % (
            where, fix["dy"] * 100, fix["dx"] * 100, math.degrees(fix["dyaw"])))
        return fix

    def _dock_check(self, hyaw: float) -> None:
        """At the pre-dock spot, turned round: the heading (and x) from the
        route's `dock_check` walls -- ฝ่ายอาคาร's 3 m east wall 2 m ahead --
        before backing in. A half turn on the spot came out several degrees
        off what odometry said all day (6° and 12° after left turns); backing
        0.45 m crooked by that meets the charger off-centre.
        The wall is looked for with the heading guessed up to
        ±DOCK_CHECK_YAW_TRIES_DEG off odometry (the matcher takes a wall within
        12° of where it is expected: 7 ต.ค. 16:08 it found none, backed in on
        odometry and met the charger crooked; the scan from the charger later
        that day matches with the guess up to 11-12° off the truth either way,
        always to the same -11.9°, and not at 13°), and looked for again for
        DOCK_CHECK_WAIT_S. None at all: no backing in blind -- that is how 14:47
        and 16:08 ended."""
        anchors = {n: self.route.anchors[n] for n in self.route.dock_check}
        guess = self.b.pose()
        t0, fix, tried, first = time.time(), None, None, None
        while True:
            frames = [self.b.raw_scan(), self.b.raw_scan()]
            pts = np.vstack([rl.scan_points(f) for f in frames])
            for d in DOCK_CHECK_YAW_TRIES_DEG:
                f = rl.correct_pose((guess[0], guess[1], guess[2] + math.radians(d)), pts, anchors, {})
                first = first or f
                if f.accepted:
                    fix, tried = f, d
                    break
            if fix is not None or time.time() - t0 > DOCK_CHECK_WAIT_S:
                break
            time.sleep(0.5)
        turned = None if fix is None else math.degrees(rl.wrap_pi(fix.pose[2] - guess[2]))
        self.log(kind="dock_check", guess=guess, accepted=fix is not None, reason=(fix or first).reason,
                 pose=None if fix is None else fix.pose, yaw_guess_deg=tried, dyaw_total=turned,
                 dx=None if fix is None else fix.dx, matches=None if fix is None else fix.matches,
                 points=None if fix is not None else np.round(pts, 3).tolist())
        if fix is None:
            raise Stop("dock check: %s, with the heading guessed up to %.0f° either way for %.0f s -- not backing "
                       "in blind (14:47 and 16:08 met the charger crooked that way); standing at the pre-dock spot"
                       % (first.reason, max(DOCK_CHECK_YAW_TRIES_DEG), DOCK_CHECK_WAIT_S), code="dock_blocked")
        self.b.set_pose(fix.pose)
        self.say("  dock check: %s; heading %+.1f° from odometry's%s" % (
            fix.summary(), turned, "" if not tried else " (wall found with the guess %+.0f°)" % tried))
        if abs(rl.wrap_pi(fix.pose[2] - hyaw)) > math.radians(SPOT_TURN_TOL_DEG):
            self.turn_velocity(hyaw, board_idle=True)

    def _back_in_once(self, out_first: bool) -> tuple[bool, str | None]:
        from app import slamware_sdk as sw
        hx, hy, hyaw = self.b.home_pose()
        self.dock_started = True                    # from here on a failure is not handed to GoHome
        ux, uy = math.cos(hyaw), math.sin(hyaw)
        nx, ny = -uy, ux
        dock = (hx + DOCKED_M * ux, hy + DOCKED_M * uy)
        pre = (dock[0] + PREDOCK_M * ux, dock[1] + PREDOCK_M * uy)
        x, y, yaw = self.fast_pose()
        if out_first:
            # Straight out first, the way it backed in, no turning: 7 ต.ค. 16:08 the
            # tail met the charger crooked, and the go again began with a 38° turn
            # on the spot right there (odometry's heading had gone with the bump).
            # As far as it backed, no further: past the pre-dock spot it would
            # need a half turn to come back to it.
            d = min(PREDOCK_M, getattr(self, "last_backed", PREDOCK_M))
            if d >= 0.05:
                self.drive_velocity(rl.Step(do="drive", to=(x + d * math.cos(yaw), y + d * math.sin(yaw)),
                                            label="straight out from the charger"), direct=True)
                x, y, _ = self.fast_pose()
        if self.route.dock_gap is not None and not out_first:
            self._dock_gap(hx, hy, hyaw, "approach")         # the approach then steers onto the line
            x, y, yaw = self.fast_pose()
        beyond = (x - pre[0]) * ux + (y - pre[1]) * uy > 0.03
        off_line = abs((x - dock[0]) * nx + (y - dock[1]) * ny) > 0.03
        came = 0.0
        if (out_first or beyond or off_line) and math.hypot(x - pre[0], y - pre[1]) > 0.03:
            came = math.hypot(x - pre[0], y - pre[1])
            self.drive_velocity(rl.Step(do="drive", to=pre, label="to the pre-dock spot"), direct=True)
        if self.route.dock_gap is not None and not out_first:
            look = self._dock_gap(hx, hy, hyaw, "pre-dock")  # what is left, backing in leans it out
            if look is not None and abs(look["dy"]) > DOCK_GAP_REDO_M:
                if came < DOCK_GAP_BACK_M + 0.05:
                    raise Stop("%.1f cm off the charger's line at the pre-dock spot, and no ground it just "
                               "crossed to back onto for another approach -- not backing in" % (look["dy"] * 100),
                               code="dock_blocked")
                self.say("  %.1f cm off the charger's line at the pre-dock spot: back %.2f m, the approach again"
                         % (look["dy"] * 100, DOCK_GAP_BACK_M))
                self.back_off(DOCK_GAP_BACK_M)
                self.drive_velocity(rl.Step(do="drive", to=pre, label="to the pre-dock spot, again"), direct=True)
                again = self._dock_gap(hx, hy, hyaw, "pre-dock, again")
                if again is None:
                    raise Stop("%.1f cm off the charger's line, and the second approach could not see the gap "
                               "to say it is on it now -- not backing in" % (look["dy"] * 100),
                               code="dock_blocked")
                if abs(again["dy"]) > DOCK_GAP_REDO_M:
                    raise Stop("still %.1f cm off the charger's line after a second approach -- not backing in"
                               % (again["dy"] * 100), code="dock_blocked")
        self.turn_velocity(hyaw, board_idle=True)
        if self.route.dock_check:
            self._dock_check(hyaw)
        self.b.events()
        t0, trace, reason, docked = time.time(), [], None, False
        x0, y0, _ = self.fast_pose()
        vx, since_pulse, last_power = 0.0, 9.0, 0.0
        # The wall ahead says how far the robot has really backed (BACK_SLIP_M),
        # the wheels whether they still turn (BACK_STALL_MPS).
        walls = {n: self.route.anchors[n] for n in self.route.dock_check}
        watcher = self.watcher() if walls else None
        seen, slip0, slip, slip_max, over, blocked = None, None, 0.0, 0.0, 0, False
        slips, recent, last_read, eased, ease_why = [], [], t0, 0.0, None
        if watcher is not None:
            watcher.start()
        try:
          with sw.VelocitySession(self.sdk) as v:
            while True:
                tick = time.time()
                x, y, yaw = self.fast_pose()
                out = (x - dock[0]) * ux + (y - dock[1]) * uy        # still to go, along the line
                lat = (x - dock[0]) * nx + (y - dock[1]) * ny        # + = left of the line, facing out
                herr = rl.wrap_pi(yaw - hyaw)
                # every tick over the last 12 cm: stop on contact, not up to 0.3 s after it
                if out < 0.12 or tick - last_power >= 0.3:
                    last_power = tick
                    if self.b.power().get("dockingStatus") == "on_dock":
                        docked = True
                        break
                backed = math.hypot(x - x0, y - y0)
                # speed since the newest pose at least BACK_STALL_S old (ticks jitter:
                # a window cut at exactly BACK_STALL_S lost its far end half the time)
                recent = [q for q in recent if tick - q[0] <= BACK_STALL_S + 0.3] + [(tick, x, y)]
                ref = [q for q in recent if tick - q[0] >= BACK_STALL_S]
                stalled = (backed >= BACK_SLIP_FROM_M and bool(ref)
                           and math.hypot(x - ref[-1][1], y - ref[-1][2]) / (tick - ref[-1][0]) < BACK_STALL_MPS)
                if watcher is not None:
                    pts, fpose = watcher.frame()
                    if pts is not seen and fpose is not None and len(pts) >= 200:
                        seen = pts
                        wdx, wdy = wall_offset(pts, fpose, walls)
                        if wdx is not None or wdy is not None:
                            last_read = tick
                            # + = the walls put the robot further out than odometry does
                            c_ = (wdx or 0.0) * ux + (wdy or 0.0) * uy
                            if slip0 is None and backed >= BACK_SLIP_FROM_M:
                                slip0 = c_
                            if slip0 is not None:
                                slip = c_ - slip0
                                slip_max = max(slip_max, slip)
                                over = over + 1 if slip > BACK_SLIP_M else 0
                                slips.append((round(tick - t0, 2), round(slip, 4)))
                if over >= BACK_SLIP_FRAMES or stalled:
                    blocked = True
                    reason = ("blocked: backed %.3f m by odometry, %s -- something behind, not the contacts"
                              % (backed, "%.3f m less by the wall ahead" % slip if over >= BACK_SLIP_FRAMES
                                 else "the wheels stopped (under %.3f m/s for %.1f s)" % (BACK_STALL_MPS, BACK_STALL_S)))
                elif out < -BACK_OVERSHOOT_M:
                    reason = "%.2f m past where the dock should be, no contact" % -out
                elif abs(herr) > math.radians(4):
                    reason = "heading %.1f° off the dock's line" % math.degrees(herr)
                elif self._drive_events([], round(tick - t0, 2)):
                    reason = "board event (bumper?)"
                elif tick - t0 > 40:
                    reason = "40 s and not on the dock"
                if reason:
                    break
                # Backing, the tail leads: to come back onto the line from the
                # left the nose turns LEFT (the opposite of driving forward).
                want = rl.wrap_pi(hyaw + max(-math.radians(2), min(math.radians(2), 3.0 * lat)) - yaw)
                target_v = -(BACK_SPEED if out > 0.10 else BACK_SPEED * 0.6)
                if watcher is not None and out < 0.12 and tick - last_read > BACK_FRAME_MAX_AGE_S:
                    target_v, vx, recent = 0.0, 0.0, []     # hold for a fresh frame (not a stall)
                vx = max(target_v, vx - 0.02)
                omega = 0.0
                since_pulse += 0.1
                if abs(want) > math.radians(0.7) and since_pulse >= 0.5:
                    omega, since_pulse = math.copysign(rl.DOOR_PULSE_W, want), 0.0
                trace.append((round(tick - t0, 2), round(x, 3), round(y, 3), round(math.degrees(yaw), 1),
                              round(out, 3), round(lat, 3), round(vx, 3), omega))
                if v.set(vx, omega):
                    vx = 0.0
                time.sleep(max(0.0, 0.1 - (time.time() - tick)))
            if blocked:
                v.set(0.0, 0.0)
                time.sleep(0.3)
                if self.b.power().get("dockingStatus") == "on_dock":
                    docked = True                   # on the contacts after all: stay
                else:
                    front = watcher.front() if watcher is not None else None
                    ex0, ey0, _ = self.fast_pose()
                    if front is not None and front < BACK_EASE_FRONT_M:
                        ease_why = "something %.2f m ahead" % front
                    else:
                        te = time.time()
                        while math.hypot(self.fast_pose()[0] - ex0, self.fast_pose()[1] - ey0) < BACK_EASE_M:
                            if time.time() - te > BACK_EASE_S:
                                ease_why = "short of %.2f m after %.1f s" % (BACK_EASE_M, time.time() - te)
                                break
                            v.set(BACK_EASE_SPEED, 0.0)
                            time.sleep(0.1)
                        v.set(0.0, 0.0)
                    ex, ey, _ = self.fast_pose()
                    eased = math.hypot(ex - ex0, ey - ey0)
        finally:
            if watcher is not None:
                watcher.stop()
        time.sleep(1.0)
        pw = self.b.power()
        docked = docked or pw.get("dockingStatus") == "on_dock"
        x, y, yaw = self.fast_pose()
        self.last_backed = math.hypot(x - x0, y - y0)
        self.log(kind="back_into_dock", docked=docked, charging=pw.get("isCharging"), reason=reason,
                 backed=round(math.hypot(x - x0, y - y0), 3), end=[x, y, yaw], seconds=round(time.time() - t0, 1),
                 slip_max=round(slip_max, 3) if watcher is not None else None, blocked=blocked,
                 eased=round(eased, 3) if blocked else None, ease_why=ease_why, slips=slips, trace=trace)
        if blocked and not docked:
            self.dock_blocked = True
        self.say("  backed %.2f m: %s%s" % (math.hypot(x - x0, y - y0),
                                           "on the dock" if docked else "not on the dock",
                                           "" if docked else " (%s)" % reason))
        return docked, reason

    def dock(self, step: rl.Step) -> None:
        """GoHome, the board's own docking — with two things done for it first,
        because left to itself it spun ~200° at up to 62°/s and then flipped
        its turn 15 times backing in (recorded 6 ต.ค. 16:04, the scraping the
        owner heard on every return): our controller drives the robot onto
        GoHome's own landing spot and turns it round there, one way at ~16°/s
        (`approach_landing`), so GoHome only has to back in; and the board's
        turn rate is capped while it runs and put back after.
        The pose is recorded throughout and summarised in the log."""
        if self.sdk is not None:
            try:
                if self.back_into_dock():
                    x, y, yaw = self.b.pose()
                    self.log(kind="dock", ok=True, how="own reverse", end=[x, y, yaw])
                    self.say("  docked at %+.1f° by backing straight in" % math.degrees(yaw))
                    return
                # Not GoHome: on 7 ต.ค. 14:47 it spun 254° with 11 reversals and left
                # the robot on the contacts at ~63°, reported as docked. Standing
                # still by the charger is the safe end; a person docks it.
                x, y, yaw = self.b.pose()
                self.log(kind="dock", ok=False, how="own reverse", end=[x, y, yaw],
                         reason=getattr(self, "dock_failure", None))
                if getattr(self, "dock_blocked", False):
                    raise Stop("blocked backing in (%s) -- eased off it, standing by the charger"
                               % getattr(self, "dock_failure", None), code="dock_blocked")
                raise Stop("not on the dock after %d tries backing in (%s) -- standing by the charger"
                           % (DOCK_TRIES, getattr(self, "dock_failure", None)), code="dock_blocked")
            except Stop:
                raise
            except Exception as e:
                # Not only the board's HTTP errors: a link drop (LinkLost, SdkError,
                # OSError) went up to run_step, which does the step again from where
                # the robot stands -- backing in again, after a block into the same
                # column (reviewer). Started is started: stand still, a person docks it.
                if getattr(self, "dock_started", False):
                    # GoHome after a crooked contact is the 14:47 spin (above)
                    raise Stop("own docking failed part way (%s) -- not handing over to GoHome; standing still" % e,
                               code="dock_blocked")
                self.say("  (own docking unavailable: %s -- GoHome)" % e)
                self.approach_landing()
        before = None
        try:
            before = self.b.param("base.max_angular_speed")
            self.b.set_param("base.max_angular_speed", DOCK_ANGULAR_SPEED)
        except (httpx.HTTPError, ValueError, Stop) as e:
            self.say("  (could not cap the board's turn rate for GoHome: %s)" % e)
        trace, ok, st = [], False, {}
        try:
            aid = self.b.start(GO_HOME, {"gohome_options": {"flags": "dock", "back_to_landing": True,
                                                            "charging_retry_count": 2}})
            self.say("  go home, action %s (turn rate capped at %s rad/s, was %s)"
                     % (aid, DOCK_ANGULAR_SPEED if before is not None else "—", before))
            t0, checked = time.time(), 0.0
            while time.time() - t0 < 120:
                tick = time.time()
                x, y, yaw = self.fast_pose()
                trace.append((round(tick - t0, 2), round(x, 4), round(y, 4), round(math.degrees(yaw), 2)))
                if tick - checked >= 1.0:
                    checked = tick
                    if self.b.power().get("dockingStatus") == "on_dock":
                        ok = True
                        break
                    if self.b.current() is None:
                        break
                time.sleep(max(0.0, 0.1 - (time.time() - tick)))
            if ok:
                # On the contacts for a moment is not docked: 14:47 it touched at
                # ~63° and called it done. Charging, still, and facing the dock's way.
                time.sleep(2.0)
                pw = self.b.power()
                _, _, hyaw_now = self.b.home_pose()
                twist = abs(rl.wrap_pi(self.b.pose()[2] - hyaw_now))
                if (pw.get("dockingStatus") != "on_dock" or not pw.get("isCharging")
                        or twist > math.radians(15)):
                    ok = False
                    st = {"reason": "on the contacts only for a moment: %s, charging %s, %.0f° off the dock's line"
                                    % (pw.get("dockingStatus"), pw.get("isCharging"), math.degrees(twist))}
            if not ok and not st:
                st = self.b.state(aid)
        finally:
            if before is not None:
                try:
                    self.b.set_param("base.max_angular_speed", before)
                    # The board applies a parameter a moment after the PUT: read
                    # straight back it still said 0.35 and a false warning went
                    # out, while a read a few seconds later said 1.0 (6 ต.ค. 16:13).
                    deadline = time.time() + 2.0
                    while True:
                        now = self.b.param("base.max_angular_speed")
                        if abs(now - before) <= 1e-3:
                            break
                        if time.time() > deadline:
                            self.say("  WARNING: board turn rate is %s, expected %s back" % (now, before))
                            break
                        time.sleep(0.25)
                except (httpx.HTTPError, ValueError, Stop) as e:
                    self.say("  WARNING: could not put the board's turn rate back to %s: %s" % (before, e))
        summary = rl.turn_summary(trace)
        x, y, yaw = self.b.pose()
        self.log(kind="dock", ok=ok, state=st or None, turn=summary, end=[x, y, yaw], trace=trace)
        if not ok:
            raise Stop("go home did not dock (%s) — use the D-pad" % (st.get("reason") or "?"), code="dock_blocked")
        self.say("  docked at %+.1f°; GoHome turned %.0f° in all, peak %.0f°/s, %d left-right reversals"
                 % (math.degrees(yaw), summary["turned_deg"], summary["peak_rate_deg"], summary["reversals"]))

    # -- the dock's half turn, measured (--dock-turn-test; supervised) --------
    def _half_turn_fix(self, facing: str) -> dict:
        """One stopped look at the pre-dock spot after a half turn, written to
        the board -- so the next half turn starts from a measured heading and
        this look's dyaw is the half turn's own error. Facing "out": the
        route's dock_check walls (ea_east 2 m ahead), the heading guessed up to
        DOCK_CHECK_YAW_TRIES_DEG off as `_dock_check` does. Facing the "dock":
        the bare wall behind the charger (`rl.dock_wall_fix`; its stretches are
        too short for `correct_pose`). No wall read: a Stop, standing there."""
        time.sleep(0.3)
        guess = self.b.pose()
        pts = np.vstack([rl.scan_points(self.b.raw_scan()) for _ in range(2)])
        if facing == "out":
            anchors = {n: self.route.anchors[n] for n in self.route.dock_check}
            fix = next((f for f in (rl.correct_pose((guess[0], guess[1], guess[2] + math.radians(d)), pts,
                                                    anchors, {}) for d in DOCK_CHECK_YAW_TRIES_DEG)
                        if f.accepted), None)
            if fix is None:
                raise Stop("dock turn test: %s not read facing out" % ", ".join(anchors), code="dock_blocked")
            pose, dx = fix.pose, fix.dx
        else:
            why: list = []
            wall = rl.dock_wall_fix(pts, guess, self.route.dock_gap, why=why)
            if wall is None:
                raise Stop("dock turn test: the wall behind the dock not read (%s)" % (why[-1] if why else "?"),
                           code="dock_blocked")
            pose, dx = (guess[0] + wall["dx"], guess[1], rl.wrap_pi(guess[2] + wall["dyaw"])), wall["dx"]
        self.b.set_pose(pose)
        return {"facing": facing, "dyaw_deg": round(math.degrees(rl.wrap_pi(pose[2] - guess[2])), 3),
                "dx": None if dx is None else round(dx, 4), "pose": [round(v, 4) for v in pose]}

    def dock_turn_test(self, n: int) -> list[dict]:
        """`--dock-turn-test N` (9 ต.ค., supervised; behaviour of a run is not
        changed by it): is the half turn at the dock off by more one way than
        the other? calibrate_from_runs had one of each -- clockwise 180° then
        back anticlockwise came out +6.05°, anticlockwise both ways +0.64° --
        too few to change anything on. From the dock: the dock's own checkpoint,
        off it, straight out to the pre-dock spot, facing out, a look; then N
        times a half turn clockwise (facing the dock) and a look, a half turn
        anticlockwise (facing out) and a look; then back onto the charger the
        way a run docks. DOCK_TURN_TEST_DEG, not 180: at exactly 180 the shorter
        way is a coin toss (wrap_pi) and the dock's own turn is ~175°. Each
        look is written to the board: its dyaw is that half turn's error."""
        if self.sdk is None or self.route.dock_gap is None or not self.route.dock_check:
            raise Stop("dock turn test: needs velocity control, and the route's dock_gap and dock_check")
        first = self.route.steps[0] if self.route.steps else None
        if first is not None and first.do == "checkpoint":
            self.checkpoint(first)                  # the dock's own: the heading it starts from
        hx, hy, hyaw = self.b.home_pose()
        pre = (hx + (DOCKED_M + PREDOCK_M) * math.cos(hyaw), hy + (DOCKED_M + PREDOCK_M) * math.sin(hyaw))
        self.undock()
        self.drive_velocity(rl.Step(do="drive", to=pre, label="out to the pre-dock spot"), direct=True)
        self.turn_velocity(hyaw, board_idle=True)
        rows = [dict(self._half_turn_fix("out"), go=0, turn=None)]
        self.log(kind="dock_turn", **rows[-1])
        for k in range(1, n + 1):
            for sense, facing in ((-1.0, "dock"), (1.0, "out")):
                yaw0 = self.fast_pose()[2]
                self.turn_velocity(rl.wrap_pi(yaw0 + sense * math.radians(DOCK_TURN_TEST_DEG)), board_idle=True)
                odo = math.degrees(rl.wrap_pi(self.fast_pose()[2] - yaw0))
                row = dict(self._half_turn_fix(facing), go=k, turn="cw" if sense < 0 else "ccw",
                           odometry_deg=round(odo, 2))
                rows.append(row)
                self.log(kind="dock_turn", **row)
                self.say("  half turn %d %s: odometry %+.1f°, the walls then put it %+.2f° from that"
                         % (k, row["turn"], odo, row["dyaw_deg"]))
        out = {}
        for turn in ("cw", "ccw"):
            errs = [r_["dyaw_deg"] for r_ in rows if r_.get("turn") == turn]
            out[turn] = {"n": len(errs), "mean_deg": round(float(np.mean(errs)), 3) if errs else None,
                         "each": errs}
        self.log(kind="dock_turn_summary", **out)
        self.say("half turns at the dock, error after each (+ = the truth more anticlockwise than odometry): "
                 "CW %s (mean %s), CCW %s (mean %s)" % (out["cw"]["each"], out["cw"]["mean_deg"],
                                                        out["ccw"]["each"], out["ccw"]["mean_deg"]))
        self.dock(rl.Step(do="dock", label="dock"))
        return rows

    # -- the run -----------------------------------------------------------
    def chain_from(self, i: int) -> list[int]:
        """Step `i` and the drive steps straight after it, when they are all
        drives under velocity control (their corners are judged on the way)."""
        steps = self.route.steps
        if self.sdk is None or steps[i - 1].do != "drive" or getattr(self, "no_corners", False):
            return [i]
        j = i
        while j < len(steps) and steps[j].do == "drive":
            j += 1
        # Not clamped to --to-step, on purpose: a chain is drives only, and a
        # leg ends on a checkpoint or dock (validate_to_step), so no chain can
        # reach past it. A clamp here was dead code whose test could not go
        # red (build review #11); this says the invariant instead.
        assert j <= self.last_step(), "a chain past --to-step"
        return list(range(i, j + 1))

    def run(self, from_step: int = 1, to_step: int | None = None) -> None:
        """Steps `from_step`..`to_step` (1-based, inclusive). `to_step` given =
        a leg (scripts/emma_nav.py): it ends with `leg_done` even when it is
        the route's last step -- leg 2 of the round trip ends on step 20 = the
        dock, and a `route_done` there was never counted as arrived (build
        review #3). Without it the whole route runs and ends `route_done`, as
        it always has."""
        handlers = {"checkpoint": self.checkpoint, "move": self.move, "drive": self.drive,
                    "door": self.door, "dock": self.dock}
        if to_step is not None:
            self.to_step = to_step
        leg = getattr(self, "to_step", None) is not None
        self.from_step = from_step
        i = from_step
        while i <= self.last_step():
            step = self.route.steps[i - 1]
            self.say("step %d/%d %s %s" % (i, len(self.route.steps), step.do, step.label))
            self.log(kind="step", i=i, do=step.do, label=step.label)
            nxt = self.route.steps[i] if i < len(self.route.steps) else None
            self.next_heading = (math.radians(nxt.heading_deg)
                                 if nxt is not None and nxt.heading_deg is not None else None)
            if step.do == "door":
                self.door_checkpoint = self.checkpoint_for(i)
                plan = self.door_exit_plan(i)
                if plan is not None:
                    i = self.door_then_corner(i, step, plan, handlers)
                    continue
            chain = self.chain_from(i)
            if len(chain) > 1:
                try:
                    i = self.run_chain(chain) + 1
                except LinkLost as e:
                    # a dropped link mid-chain: the rest one lane at a time, each
                    # with run_step's own waiting and going again
                    self.recover_link(str(e))
                    self.keep_corrections(e)
                    k = getattr(e, "step_index", i)
                    for kk in range(k, min(chain[-1], self.last_step()) + 1):
                        st_ = self.route.steps[kk - 1]
                        if kk != k:
                            self.say("step %d/%d %s %s" % (kk, len(self.route.steps), st_.do, st_.label))
                            self.log(kind="step", i=kk, do=st_.do, label=st_.label)
                        self.run_step(kk, st_, handlers, dropped=kk == k)
                    i = chain[-1] + 1
                continue
            self.run_step(i, step, handlers)
            i += 1
        pose = self.b.pose()
        self.log(kind="leg_done" if leg else "route_done", pose=list(pose))
        if getattr(self, "state_out", None) is not None:
            save_state(self, self.state_out)
        self.say("%s done at %s" % ("leg (steps %d-%d)" % (from_step, self.last_step()) if leg else "route",
                                    tuple(round(v, 3) for v in pose)))

    def door_exit_plan(self, i: int) -> dict | None:
        """Door step `i` (1-based) followed -- past at most one checkpoint -- by
        a lane at a right angle: where the pass ends instead (`until`), and the
        lane to round the corner into (DOOR_EXIT_*). None: the door goes on
        to its own `until` as before."""
        steps = self.route.steps
        if self.sdk is None or getattr(self, "no_corners", False):
            return None
        step = steps[i - 1]
        k, skipped = i + 1, None
        if k <= len(steps) and steps[k - 1].do == "checkpoint":
            k, skipped = k + 1, k
        if k > len(steps) or steps[k - 1].do != "drive" or step.until is None:
            return None
        # A leg's last step is where its next leg starts from a measured,
        # standing pose: rounded out on the move, that checkpoint would never
        # be stood at, and the lane after it belongs to the next leg. This one
        # test is both of the design's cases: `skipped == to_step` puts the
        # lane at to_step + 1, past the leg.
        if k > self.last_step():
            return None
        lane, door = steps[k - 1], self.route.doors[step.door]
        h1 = math.radians(step.heading_deg)
        a = 0 if abs(math.cos(h1)) >= abs(math.sin(h1)) else 1     # the world axis the pass runs along
        if "xy"[a] != door.axis:
            return None
        sgn = math.copysign(1.0, math.cos(h1) if a == 0 else math.sin(h1))
        mid = (door.lo + door.hi) / 2.0
        out = [mid, mid]
        out[a] = step.until                                        # where the pass used to end
        h2 = rl.lane_heading(tuple(out), lane.to)
        if h2 is None or abs(abs(rl.wrap_pi(h2 - h1)) - math.pi / 2) > math.radians(5):
            return None
        # the stop not made measures nothing the lane's own walls do not:
        # only walls across the lane, no door, and the lane has walls of that kind
        across = "y" if abs(math.cos(h2)) >= abs(math.sin(h2)) else "x"
        anchors = self.route.anchors
        if not any(w in anchors and anchors[w].axis == across for w in lane.use):
            return None
        if skipped is not None:
            cp = steps[skipped - 1]
            if cp.doors or any(w not in anchors or anchors[w].axis != across for w in cp.use):
                return None
        c_from_wall = (lane.to[a] - door.at) * sgn                 # the lane's line, past the wall line
        end_from_wall = rl.ROBOT_BASE_LENGTH / 2.0 + rl.DOOR_SLOW_AFTER_M
        r = min(CORNER_R, c_from_wall - end_from_wall - DOOR_EXIT_RUNUP_M)
        if r < self.turn_width / 2.0 + DOOR_EXIT_R_SPARE_M or (step.until - door.at) * sgn < c_from_wall - 0.10:
            return None
        return {"until": door.at + sgn * end_from_wall, "axis": a, "sgn": sgn, "lane_k": k, "skipped": skipped,
                "c_from_wall": c_from_wall, "end_from_wall": end_from_wall, "wall": door.at}

    def door_then_corner(self, i: int, step: rl.Step, plan: dict, handlers: dict) -> int:
        """The door, ended where its slow zone does, then on round the corner
        into the lane after it (`door_exit_plan`) and on along the lanes after
        that. Returns the next step to run: the checkpoint after the door, as
        before, when the corner was not taken (the lead lane stopped on the
        lane's line) or could not be planned from where the pass ended."""
        self.run_step(i, dataclasses.replace(step, until=plan["until"]), handlers)
        pts = self.b.scan()
        beside = [g for g in rl.side_clearance(pts, half_width=self.turn_width / 2.0, shaped=True) if g is not None]
        if beside and min(beside) < rl.DOOR_FAST_SIDE_M:
            # a leaf or a wall along the way out: the pass's own rules to its end
            self.say("  (something %.2f m beside the robot out of %s -- the pass goes on to its end)"
                     % (min(beside), step.door))
            self.log(kind="door_exit_corner", door=step.door, taken=False, beside=round(min(beside), 3))
            self.run_step(i, step, handlers, resume=True)
            return i + 1
        a, sgn, k = plan["axis"], plan["sgn"], plan["lane_k"]
        lane = self.route.steps[k - 1]
        x, y, _ = self.fast_pose()
        here = ((x, y)[a] - plan["wall"]) * sgn
        c = [x, y]
        c[a] = lane.to[a]                                          # straight on, to the lane's line
        # from where the pass really ended (it can stop a centimetre or two on)
        left = plan["c_from_wall"] - max(here, plan["end_from_wall"])
        r = min(CORNER_R, left - DOOR_EXIT_RUNUP_M)
        r_min = self.turn_width / 2.0 + DOOR_EXIT_R_SPARE_M
        if r < r_min <= left - DOOR_EXIT_MIN_RUNUP_M:
            r = r_min                       # a shorter run-up, never a tighter arc (DOOR_EXIT_MIN_RUNUP_M)
        lead = rl.Step(do="drive", to=(c[0], c[1]), label="out of %s on to %r" % (step.door, lane.label))
        if r < r_min:
            self.say("  (%.2f m left for the corner out of %s -- on to the lane's line and turning there)"
                     % (r, step.door))
            self.run_step(i, lead, handlers)
            return i + 1
        chain = self.chain_from(k)
        try:
            last = self.run_chain(chain, lead=(i, lead, r))
        except LinkLost as e:
            self.recover_link(str(e))
            self.keep_corrections(e)
            kk0 = getattr(e, "step_index", i)
            if kk0 == i:
                # dropped on the way out, before the corner: on to the lane's line as
                # before and the checkpoint from there (the arc's own drop is a Stop)
                self.run_step(i, lead, handlers, dropped=True)
                return i + 1
            self._log_skipped(plan, step, lane)
            for kk in range(kk0, min(chain[-1], self.last_step()) + 1):
                st_ = self.route.steps[kk - 1]
                if kk != kk0:                       # run_chain logged the one it dropped on
                    self.say("step %d/%d %s %s" % (kk, len(self.route.steps), st_.do, st_.label))
                    self.log(kind="step", i=kk, do=st_.do, label=st_.label)
                self.run_step(kk, st_, handlers, dropped=kk == kk0)
            return chain[-1] + 1
        if last == i:
            return i + 1
        self._log_skipped(plan, step, lane)
        return last + 1

    def _log_skipped(self, plan: dict, step: rl.Step, lane: rl.Step) -> None:
        if plan["skipped"] is not None:
            cp = self.route.steps[plan["skipped"] - 1]
            self.log(kind="step_skipped", i=plan["skipped"], label=cp.label,
                     why="rounded out of %s on the move; %r measures its walls on the way" % (step.door, lane.label))

    def run_step(self, i: int, step: rl.Step, handlers: dict, resume: bool = False, dropped: bool = False) -> None:
        """One step; if the link drops, wait for it and do the step again from
        where the robot stands (the board has stopped it). A lane is driven
        from here to its end -- or counted done if it is within a few cm of
        it; a door already at its frame goes on without the aim. `resume`: a
        door going on from where the robot stands, no aim; `dropped`: the link
        has already dropped once on this step (a chain's) -- a lane that near
        its end is done, as on any second go (reviewer: a fresh go 3 cm from the
        end read the 3 cm as "not a straight lane")."""
        from app import slamware_sdk as sw
        drops, resume_door, blocks, skip_lineup = (1 if dropped else 0), resume, 0, False
        while True:
            try:
                if step.do in ("move", "drive", "door") and self.b.power().get("dockingStatus") == "on_dock":
                    self.undock()
                if step.do == "drive" and drops:
                    x, y, _ = self.b.pose()
                    if math.hypot(step.to[0] - x, step.to[1] - y) < LINK_LANE_DONE_M:
                        self.say("  (%.2f m from the lane's end when the link dropped: done)"
                                 % math.hypot(step.to[0] - x, step.to[1] - y))
                        return
                if step.do == "door" and self.sdk is not None:
                    return self.door_velocity(step, resume=resume_door, skip_lineup=skip_lineup)
                return handlers[step.do](step)
            except DoorBlocked as e:
                blocks += 1
                if blocks > DOOR_BLOCK_RETRIES:
                    raise Stop("%s -- still, after %d goes round it" % (e, blocks - 1), code=e.code)
                most = max(e.came - 0.02, 0.0)
                back = min(DOOR_BACK_OFF_M, most)
                self.say("  %s -- backing off %.2f m (up to %.2f for room to turn), %+.3f m across, in again"
                         % (e, back, most, -e.need))
                self.log(kind="door_block", door=step.door, need=round(e.need, 3), came=round(e.came, 3),
                         back=round(back, 3), go=blocks)
                if most > 0.02:
                    self.back_off(back, most=most, turn=-math.copysign(math.pi / 2, e.need))
                self.step_across(step, math.radians(step.heading_deg), e.need)
                resume_door, skip_lineup = False, True
            except (LinkLost, sw.SdkError, OSError, httpx.TransportError) as e:
                drops += 1
                if drops > LINK_STEP_RETRIES:
                    raise Stop("step %d %r: the link dropped %d times -- giving up" % (i, step.label, drops),
                               code="link_lost")
                self.recover_link(str(e))
                self.keep_corrections(e)
                if step.do == "door":
                    door = self.route.doors[step.door]
                    h = math.radians(step.heading_deg)
                    mid = (door.lo + door.hi) / 2.0
                    middle = (mid, door.at) if door.axis == "y" else (door.at, mid)
                    x, y, _ = self.b.pose()
                    face_near = (middle[0] - x) * math.cos(h) + (middle[1] - y) * math.sin(h)
                    resume_door = face_near < DOOR_RESUME_FACE_M
                    self.say("  door %s again%s (%.2f m from its wall line)"
                             % (step.door, ", going on from the frame" if resume_door else " from the aim", face_near))
                else:
                    self.say("  step %d again from here" % i)

    def back_off(self, dist: float, most: float | None = None, turn: float | None = None) -> None:
        """Straight back the way the robot came (the lidar does not see behind;
        this is ground it has just crossed), no turning, 0.06 m/s: at least
        `dist` m, and on -- up to `most` -- until the `turn` (radians, the
        step across's quarter turn) has room by the lidar. 15:24 inside the big
        room 0.30 m back left D11's open leaf beside the robot and the quarter
        turn had no room. Stops early on a board event or after 25 s."""
        from app import slamware_sdk as sw
        most = dist if most is None else max(most, dist)
        x0, y0, yaw0 = self.fast_pose()
        hx, hy = math.cos(yaw0), math.sin(yaw0)
        t0, vx, why = time.time(), 0.0, None
        watcher = self.watcher() if turn is not None else None
        if watcher is not None:
            watcher.start()
        self.b.events()
        try:
          with sw.VelocitySession(self.sdk) as v:
            while True:
                x, y, _ = self.fast_pose()
                back = -((x - x0) * hx + (y - y0) * hy)
                if back >= most:
                    break
                if back >= dist:
                    if watcher is None:
                        break
                    pts = watcher.points()
                    sweep = turn + math.copysign(math.radians(TURN_COAST_DEG), turn)
                    if len(pts) >= 200 and rl.turn_sweep_gap(pts, sweep, width=self.turn_width) >= TURN_MARGIN_M:
                        break
                if self._drive_events([], round(time.time() - t0, 2)):
                    why = "board event"
                    break
                if time.time() - t0 > 25:
                    why = "25 s"
                    break
                vx = max(-0.06, vx - 0.02)
                if v.set(vx, 0.0):
                    vx = 0.0
                time.sleep(0.1)
        finally:
            if watcher is not None:
                watcher.stop()
        x, y, _ = self.fast_pose()
        moved = -((x - x0) * hx + (y - y0) * hy)
        self.log(kind="back_off", asked=round(dist, 3), most=round(most, 3), moved=round(moved, 3), stopped=why)
        self.say("  backed off %.2f m%s" % (moved, "" if why is None else " (stopped: %s)" % why))
        if why == "board event":
            raise Stop("backing off: board event (bumper?)")

    def keep_corrections(self, e: BaseException) -> None:
        """After a dropped link: write the walls' word that the lane had not
        written yet (`LinkLost.corr`) -- `measured_at` already says it was measured."""
        c = getattr(e, "corr", None)
        if c and tuple(c) != (0.0, 0.0, 0.0):
            try:
                self.b.set_pose(_apply_corr(tuple(c), self.b.pose()))
            except (httpx.HTTPError, Stop):
                pass

    def recover_link(self, why: str) -> None:
        """Wait for the board to answer again -- the adb forward put back, a
        fresh SDK connection -- up to LINK_WAIT_S, else a Stop."""
        from app import slamware_sdk as sw
        t0 = time.time()
        self.say("  link lost (%s) -- the board has stopped the robot; waiting for the link" % why[:160])
        self.log(kind="link_lost", why=why[:300])
        while time.time() - t0 < LINK_WAIT_S:
            reforward = getattr(self.b, "reforward", None)
            if reforward is not None:
                reforward()
            try:
                self.b.power()
                if getattr(self, "sdk_addr", None) is not None:
                    try:
                        if self.sdk is not None:
                            self.sdk.close()
                    except Exception:
                        pass
                    self.sdk = sw.SlamwareSdk(*self.sdk_addr)
                    self.sdk.call("getsdpversion")
                self.say("  link back after %.0f s" % (time.time() - t0))
                self.log(kind="link_back", seconds=round(time.time() - t0, 1))
                time.sleep(1.5)            # whatever velocity action was running has timed out by now
                return
            except (Stop, httpx.HTTPError, sw.SdkError, OSError):
                time.sleep(LINK_POLL_S)
        raise Stop("the link to the board did not come back in %.0f s" % LINK_WAIT_S, code="link_lost")

    def checkpoint_for(self, i: int) -> rl.Step | None:
        """The checkpoint that lines up door step `i` (1-based): the nearest one
        before it with only lanes in between, facing the way through. A step
        sideways in front of the door takes its heading again there."""
        door = self.route.steps[i - 1]
        for s in reversed(self.route.steps[:i - 1]):
            if s.do == "checkpoint":
                if s.heading_deg == door.heading_deg:
                    return s
                break
            if s.do not in ("drive", "move"):
                break
        if door.use:
            # The door's own walls (its "use"): a lane into it rounds a corner
            # instead of stopping at a checkpoint (CORNER_*), so after a step
            # sideways the heading is taken again from these, facing the door.
            return rl.Step(do="checkpoint", heading_deg=door.heading_deg, use=door.use,
                           label="%s line-up" % door.label)
        return None


def _apply_corr(t: tuple[float, float, float], pose: tuple[float, float, float]) -> tuple[float, float, float]:
    """`t` = (ox, oy, oth): corrected = R(oth)·board + (ox, oy), heading + oth."""
    ox, oy, oth = t
    c, s = math.cos(oth), math.sin(oth)
    return c * pose[0] - s * pose[1] + ox, s * pose[0] + c * pose[1] + oy, rl.wrap_pi(pose[2] + oth)


def _compose_corr(t: tuple[float, float, float], phi: float, q: tuple[float, float],
                  shift: tuple[float, float]) -> tuple[float, float, float]:
    """`t` followed by a turn of `phi` about `q` and a shift (corrected frame)."""
    ox, oy, oth = t
    c, s = math.cos(phi), math.sin(phi)
    dx, dy = ox - q[0], oy - q[1]
    return c * dx - s * dy + q[0] + shift[0], s * dx + c * dy + q[1] + shift[1], oth + phi


def corner_geometry(target: tuple[float, float], h1: float, next_to: tuple[float, float],
                    r: float = CORNER_R) -> dict | None:
    """The quarter circle joining the lane through `target` along `h1` to the
    axis lane through `next_to`: C the corner, A where the arc leaves the
    first lane, B where it joins the second, O its centre. None unless the
    two lanes meet at a right angle with room for the arc on the second."""
    u1 = (math.cos(h1), math.sin(h1))
    if abs(u1[0]) >= abs(u1[1]):
        c = (next_to[0], target[1])
        d = next_to[1] - c[1]
        h2 = math.copysign(math.pi / 2, d)
    else:
        c = (target[0], next_to[1])
        d = next_to[0] - c[0]
        h2 = 0.0 if d > 0 else math.pi
    if abs(d) < r + CORNER_MIN_NEXT_M:
        return None
    turn = rl.wrap_pi(h2 - h1)
    if abs(abs(turn) - math.pi / 2) > math.radians(5):
        return None
    sg = 1.0 if turn > 0 else -1.0
    u2 = (math.cos(h2), math.sin(h2))
    a = (c[0] - r * u1[0], c[1] - r * u1[1])
    b = (c[0] + r * u2[0], c[1] + r * u2[1])
    o = (a[0] - sg * r * u1[1], a[1] + sg * r * u1[0])
    return {"C": c, "A": a, "B": b, "O": o, "R": r, "sg": sg, "h1": h1, "h2": h2, "u1": u1, "u2": u2,
            "thA": math.atan2(a[1] - o[1], a[0] - o[0])}


def corner_fallback(g: dict, pose: tuple[float, float, float], next_to: tuple[float, float]) -> str:
    """An arc given up at `pose`: "lane" -- on along the first lane to the
    corner and turn there; "next" -- stop, the second lane starts from here.
    On the arc the offsets from the two lanes are R(1 - cos θ) and R(1 - sin θ):
    stopped in its first ~13° the robot is past LANE_START_MAX_OFF_M from the
    second lane, which ended the route (reviewer). Up to CORNER_BACK_MAX_DEG
    round, the first lane is driven on without a spot turn (its pursuit point
    is 0.8 m ahead, align at 25°); further round the second lane takes it when
    its own start checks will (`lane_heading` reads the way to `next_to` as
    that lane's axis, within LANE_START_MAX_OFF_M less 5 cm)."""
    x, y, yaw = pose
    (cx, cy), (u2x, u2y) = g["C"], g["u2"]
    if abs(rl.wrap_pi(yaw - g["h1"])) <= math.radians(CORNER_BACK_MAX_DEG):
        return "lane"
    lat2 = abs(-(x - cx) * u2y + (y - cy) * u2x)
    h = rl.lane_heading((x, y), next_to)
    if h is not None and abs(rl.wrap_pi(h - g["h2"])) < 0.01 and lat2 <= LANE_START_MAX_OFF_M - 0.05:
        return "next"
    return "lane"


def corner_poses(g: dict, step_deg: float = 5.0, after_m: float = 0.3) -> list[tuple[float, float, float]]:
    """Poses along the arc (and `after_m` on along the second lane)."""
    out, n = [], int(90 / step_deg)
    for k in range(n + 1):
        t = g["thA"] + g["sg"] * math.radians(90) * k / n
        out.append((g["O"][0] + g["R"] * math.cos(t), g["O"][1] + g["R"] * math.sin(t),
                    g["h1"] + g["sg"] * math.radians(90) * k / n))
    for k in range(1, 4):
        d = after_m * k / 3
        out.append((g["B"][0] + d * g["u2"][0], g["B"][1] + d * g["u2"][1], g["h2"]))
    return out


def corner_clear(g: dict, pose: tuple[float, float, float], pts: np.ndarray, width: float,
                 margin: float = CORNER_MARGIN_M) -> float:
    """Least gap between the body (0.60 x `width`) swept along the corner and
    the lidar points of `pose` (robot frame); 9.0 with no points near."""
    if pts is None or len(pts) == 0:
        return 9.0
    x, y, yaw = pose
    c, s = math.cos(yaw), math.sin(yaw)
    w = np.c_[x + pts[:, 0] * c - pts[:, 1] * s, y + pts[:, 0] * s + pts[:, 1] * c]
    best = 9.0
    for px, py, ph in corner_poses(g):
        d = w - (px, py)
        cc, ss = math.cos(ph), math.sin(ph)
        lx, ly = d[:, 0] * cc + d[:, 1] * ss, -d[:, 0] * ss + d[:, 1] * cc
        qx, qy = np.abs(lx) - rl.ROBOT_BASE_LENGTH / 2, np.abs(ly) - width / 2
        gap = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0)
        best = min(best, float(gap.min()))
    return best


def corner_speed(lim: rl.DriveLimits, r: float = CORNER_R) -> float:
    """CORNER_V, or slower if the turn-rate cap cannot hold the arc at it."""
    return min(CORNER_V, 0.9 * lim.w_max * r)


def arc_command(g: dict, pose: tuple[float, float, float], prev: tuple[float, float], dt: float,
                front: float | None, lim: rl.DriveLimits, v_c: float | None = None) -> rl.LaneCommand:
    """Round the corner by pure pursuit on the arc (then the second lane):
    `done` once the robot is CORNER_DONE_DEG short of the arc's end."""
    ox, oy = g["O"]
    px, py, yaw = pose
    th = math.atan2(py - oy, px - ox)
    s = rl.wrap_pi(th - g["thA"]) * g["sg"]
    span = math.pi / 2
    rerr = math.hypot(px - ox, py - oy) - g["R"]
    if s >= span - math.radians(4):
        return rl.LaneCommand(prev[0], prev[1], True, "corner done", 0.0, rerr, 0.0)
    sl = max(s, 0.0) + CORNER_LOOKAHEAD_M / g["R"]
    if sl <= span:
        t = g["thA"] + g["sg"] * sl
        tgt = (ox + g["R"] * math.cos(t), oy + g["R"] * math.sin(t))
    else:
        d = (sl - span) * g["R"]
        tgt = (g["B"][0] + d * g["u2"][0], g["B"][1] + d * g["u2"][1])
    alpha = rl.wrap_pi(math.atan2(tgt[1] - py, tgt[0] - px) - yaw)
    ld = max(math.hypot(tgt[0] - px, tgt[1] - py), 0.15)
    v_c = corner_speed(lim, g["R"]) if v_c is None else v_c
    v_t = 0.0 if (front is not None and front <= CORNER_FRONT_STOP_M) else v_c
    pv, pw = prev
    vx = max(0.0, min(v_t, pv + lim.accel * dt) if v_t >= pv else max(v_t, pv - lim.brake * dt))
    w_t = 0.0 if vx < 0.05 else max(-lim.w_max, min(lim.w_max, vx * 2.0 * math.sin(alpha) / ld))
    omega = max(pw - lim.w_accel * dt, min(pw + lim.w_accel * dt, w_t))
    return rl.LaneCommand(vx, omega, False, "corner", (span - s) * g["R"], rerr, alpha)


def lane_limits(vmax: float, wmax: float, spin: float = 0.5) -> rl.DriveLimits:
    """Lane limits for a run. Above 0.3 m/s braking anticipates 0.45 s of
    lag, not 0.25: 7 ต.ค. 17:31 the corridor lane at 0.40 went 13 cm past its
    end -- the control loop stalled 0.48 s just as braking was due (Wi-Fi to
    the relay). Simulated at 0.4 with 0.5 s of lag: 7.8 cm over with 0.25,
    2.7 cm with 0.45, for ~0.2 s more a lane."""
    # `spin` over the measured 0.5 is used only while more than 45° is left
    # (robot_lanes.turn_command): the approach to the angle stays as validated.
    # Something in the strip ahead: the taper (front_v at front_slow down to 0 at
    # front_stop) keeps its slope and is only made longer for a faster cruise, so
    # at any distance under the 0.35 m/s one's 1.20 m the speed allowed is
    # exactly what it was -- a faster lane starts slowing earlier (1.40 m at
    # 0.45), never later -- and the stop it can really make (STOP_*) caps it on
    # top, whatever --vmax says.
    # Braking into a lane's end anticipates `latency` s at the speed it is going;
    # over 0.35 m/s cruise that grows with the cruise (0.58 s at 0.45). With 0.45 s
    # kept, the 0.45 lane came into its last 5-15 cm up to 6 cm/s faster than the
    # 0.35 one with the loop stalled 0.5 s, and 2.9 cm past it; scaled, no faster
    # on any lag (0.2-0.5 s) and 0.1 cm past. A checkpoint, a door's aim, a
    # corner's arc start are reached no faster than at 0.35. Scaled by the speed
    # of the moment instead (so that a short lane brakes as before), the last
    # 10-15 cm came 1-1.5 cm/s faster: the two shortest lanes give 0.1-0.3 s.
    latency = 0.45 * vmax / 0.35 if vmax > 0.35 else (0.45 if vmax > 0.3 else 0.25)
    base = rl.DriveLimits()
    front_v = max(base.front_v, vmax)
    return rl.DriveLimits(v_max=vmax, w_max=wmax, latency=latency,
                          w_spin_fast=spin if spin > rl.DriveLimits.w_spin else 0.0,
                          front_v=front_v,
                          front_slow=base.front_stop + (base.front_slow - base.front_stop) * front_v / base.front_v,
                          stop_latency=STOP_REACT_S, stop_decel=STOP_DECEL)


def run_speeds(args: argparse.Namespace) -> tuple[float, float]:
    """(lane cruise m/s, spot turns' fast rate rad/s) for a run: --vmax and
    --spin, both dialled back by --speed-scale (SPEED_SCALE when not given)."""
    s = getattr(args, "speed_scale", SPEED_SCALE)
    return args.vmax * s, args.spin * s


def utf8_output(stream=None) -> None:
    """Print Thai whatever stdout is: redirected to a file on Windows it is
    cp1252, and a Thai word in a message is a crash. 8 ต.ค. 09:14, the first
    run after the robot rebooted: preflight's "board localization was ON" line
    (it names ฝ่ายอาคาร) ended the run before it started -- that branch only
    runs after a reboot, so no run the day before had printed it."""
    stream = sys.stdout if stream is None else stream
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def validate_to_step(route: rl.Route, from_step: int, to_step: int | None) -> str | None:
    """Why `--from-step`/`--to-step` cannot run, or None. Checked before the
    board is touched: a leg must end where a measured, stationary pose exists
    (a checkpoint, or the dock), because the next leg starts from it."""
    n = len(route.steps)
    if not 1 <= from_step <= n:
        return "--from-step %d is outside the route's %d steps" % (from_step, n)
    if to_step is None:
        return None
    if not from_step <= to_step <= n:
        return "--to-step %d must be in %d..%d" % (to_step, from_step, n)
    do = route.steps[to_step - 1].do
    if do not in LEG_END_DO:
        return ("--to-step %d is a %r step; a leg ends on a checkpoint or the dock, where the pose is "
                "measured standing still" % (to_step, do))
    return None


def save_state(r: "Runner", path: str | Path) -> None:
    """`--state-out`: what one leg knew that the next needs and only lived in
    this process -- where each axis was last measured (`measured_at`, read by
    the door pass) and where each door's middle was found (`door_seen`, read by
    `_door_remembered`: the 7 Oct D11-out sidesteps). Leg 2 of the round trip
    is a new process; without this its D11 pass took the never-measured
    branch the verified round trip never takes (build review #2). Written
    whole, then replaced: a torn file is worse than none."""
    seen = {}
    for door, mem in (getattr(r, "door_seen", None) or {}).items():
        meas = mem.get("measured")
        seen[door] = {"offset": [float(v) for v in mem["offset"]], "lat_axis": mem["lat_axis"],
                      "measured": None if meas is None else [float(v) for v in meas]}
    doc = {"format": 1, "run_log": getattr(r, "log_name", None),
           "measured_at": {ax: [float(v) for v in xy] for ax, xy in (getattr(r, "measured_at", None) or {}).items()},
           "door_seen": seen}
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        print("(state %s not written: %s)" % (path, e), flush=True)


def _pair(v) -> tuple[float, float]:
    if (not isinstance(v, list) or len(v) != 2
            or not all(isinstance(c, (int, float)) and not isinstance(c, bool) and math.isfinite(c) for c in v)):
        raise ValueError("not an [x, y] pair: %r" % (v,))
    return float(v[0]), float(v[1])


def load_state(r: "Runner", path: str | Path) -> bool:
    """`--state-in`: put a previous leg's `measured_at`/`door_seen` back, as
    the same tuples one process would hold (`_door_remembered` compares them
    with `==`). All or nothing: a file that does not parse whole is ignored
    with a printed line and never trusted in part -- half a memory is a door
    "remembered" against an axis that was measured somewhere else."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or doc.get("format") != 1:
            raise ValueError("format is not 1")
        raw_at, raw_seen = doc.get("measured_at"), doc.get("door_seen")
        if not isinstance(raw_at, dict) or not isinstance(raw_seen, dict):
            raise ValueError("measured_at/door_seen missing")
        at = {}
        for ax, xy in raw_at.items():
            if ax not in ("x", "y"):
                raise ValueError("axis %r" % (ax,))
            at[ax] = _pair(xy)
        seen = {}
        for door, mem in raw_seen.items():
            if not isinstance(door, str) or door not in r.route.doors or not isinstance(mem, dict):
                raise ValueError("door %r is not this route's" % (door,))
            if mem.get("lat_axis") not in ("x", "y"):
                raise ValueError("door %s lat_axis" % door)
            seen[door] = {"offset": _pair(mem.get("offset")), "lat_axis": mem["lat_axis"],
                          "measured": None if mem.get("measured") is None else _pair(mem.get("measured"))}
    except (OSError, ValueError) as e:
        print("state %s ignored (%s) -- this leg starts with no memory of the last" % (path, e), flush=True)
        return False
    r.measured_at, r.door_seen = at, seen
    print("state from %s: measured %s, doors %s" % (doc.get("run_log"), sorted(at), sorted(seen)), flush=True)
    return True


# -- each door's middle, across runs (DOOR_MEM_*) ----------------------------------
# Not in --state-out's file: that one is odometry's frame of one leg, and the
# launcher deletes it whenever the robot's place is unknown (scripts/emma_nav.py:
# on the first step of every run, after any stop). A middle is a place in the
# route's frame -- every checkpoint puts odometry back on it -- so it outlives
# the runs; it is kept the same way (written whole, then replaced; a file that
# does not parse whole is not trusted in part).

def _n_comp(heading: float, lat_axis: str) -> float:
    """The robot's left (-sin h, cos h) along the world axis across the door: ±1."""
    return float(round(-math.sin(heading))) if lat_axis == "x" else float(round(math.cos(heading)))


def scan_record(pts, pose, along: float, age: float, v: float) -> dict:
    """One lidar frame on the move for the drive record (SCAN_LOG_*): the
    robot-frame points thinned to one per SCAN_LOG_VOXEL_M square, in scan
    order, as whole millimetres x0, y0, x1, y1, ...; `pose` = where the
    controller had the robot when the frame was taken (odometry + the walls'
    corrections so far), `age` = how old the frame was at that pose."""
    p = np.asarray(pts, dtype=float).reshape(-1, 2)
    if len(p):
        k = np.floor(p / SCAN_LOG_VOXEL_M).astype(np.int64)
        _, idx = np.unique(k[:, 0] * 1_000_003 + k[:, 1], return_index=True)
        p = p[np.sort(idx)]
    return {"along": round(float(along), 2), "pose": [round(float(q), 4) for q in pose],
            "age": round(float(age), 3), "v": round(float(v), 3),
            "xy_mm": np.round(p * 1000.0).astype(np.int64).ravel().tolist()}


def load_shadow_field(route: rl.Route, path) -> tuple[object | None, str]:
    """The lidar map for `Runner.shadow_fix`, or (None, why not). Refused when
    a wall or door of this route is not where the map was built with it: the
    map is a frame, and a frame moved under it (8 ต.ค.: mr_north and D11,
    17 cm) would log every comparison that far off and call it the map's
    error -- which is exactly how the first offline round misread itself."""
    if path is None:
        return None, "off (--no-shadow-map)"
    p = Path(path)
    if not p.exists():
        return None, "no map file %s" % p
    try:
        from app import scan_match as sm
        fd = sm.Field.load(p)
    except Exception as e:                      # noqa: BLE001 -- a map that cannot load is no map
        return None, "map not loaded: %s" % e
    built_a, built_d = fd.meta.get("anchors") or {}, fd.meta.get("doors") or {}
    if not built_a:
        return None, "the map does not say which walls it was built on -- its frame is unknown"
    def moved(b, axis: str, vals: tuple) -> bool:
        return b[0] != axis or any(abs(float(u) - float(w)) > 0.001 for u, w in zip(b[1:], vals))

    off = []
    # a wall: where its face is (its lo/hi only say over what stretch it counts);
    # a door: where its gap is too -- D11's moved with mr_north on 8 ต.ค.
    for n, a in route.anchors.items():
        if n in built_a and moved(built_a[n], a.axis, (a.at,)):
            off.append("wall %s: route %s, map %s" % (n, a.at, built_a[n][1]))
    for n, d in route.doors.items():
        if n in built_d and moved(built_d[n], d.axis, (d.at, d.lo, d.hi)):
            off.append("door %s: route %s..%s, map %s..%s" % (n, d.lo, d.hi, built_d[n][2], built_d[n][3]))
    if off:
        return None, "map built on other walls (%s)" % "; ".join(off[:3])
    return fd, "%s: %s runs to %s, built %s" % (p.name, fd.meta.get("runs", "?"), fd.meta.get("last_run", "?"),
                                               fd.meta.get("built_at", "?"))


def route_frame_id(route: rl.Route) -> str:
    """The route's walls and doors, as numbers: a middle is remembered in the
    frame they set, and an edit to any of them may move that frame (8 ต.ค.:
    D11, mr_north and a lane, 17 cm). A middle measured under another id is
    not used."""
    doc = {"anchors": {n: [a.axis, a.at, a.lo, a.hi] for n, a in route.anchors.items()},
           "doors": {n: [d.axis, d.at, d.lo, d.hi] for n, d in route.doors.items()}}
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def door_middle_sample(trace: list, lat_axis: str, nc: float, width: float) -> tuple[float | None, int]:
    """Where the jambs beside the robot put a door's middle over one pass (the
    coordinate across the door, in odometry's frame): the median over the
    ticks whose two box sides (the trace's 8th and 9th) add up to a doorway
    with the body's `width`. (None, ticks) under DOOR_MEM_MIN_TICKS of them.
    scripts/calibrate_from_runs.py's `in_frame` reads the logs the same way."""
    mids = []
    for row in trace:
        if not isinstance(row, (list, tuple)) or len(row) < 9:
            continue
        left, right = row[7], row[8]
        if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
            continue
        if not DOOR_MEM_FRAME_WIDTH[0] <= left + right + width <= DOOR_MEM_FRAME_WIDTH[1]:
            continue
        robot = row[1] if lat_axis == "x" else row[2]
        mids.append(robot - (right - left) / 2.0 * nc)
    if len(mids) < DOOR_MEM_MIN_TICKS:
        return None, len(mids)
    return float(np.median(mids)), len(mids)


def door_middle_prior(values: list[float], drawn: float) -> tuple[float | None, str]:
    """The middle to aim at from what is remembered (`values`, oldest first):
    the median of the last DOOR_MEM_USE_N -- or None, and why not."""
    last = [float(v) for v in values][-DOOR_MEM_USE_N:]
    if len(last) < DOOR_MEM_MIN_N:
        return None, "%d middle(s) remembered, %d needed" % (len(last), DOOR_MEM_MIN_N)
    med = float(np.median(last))
    mad = 1.4826 * float(np.median(np.abs(np.asarray(last) - med)))
    if mad > DOOR_MEM_MAX_MAD_M:
        return None, "the last %d spread %.1f cm (MAD) -- over %.1f" % (len(last), mad * 100, DOOR_MEM_MAX_MAD_M * 100)
    if abs(med - drawn) > DOOR_MEM_MAX_FROM_DRAWING_M:
        return None, ("%.1f cm from the drawing's middle -- over %.0f: not this door, or the route's frame moved"
                      % (abs(med - drawn) * 100, DOOR_MEM_MAX_FROM_DRAWING_M * 100))
    return med, "median of the last %d, MAD %.1f cm" % (len(last), mad * 100)


def load_door_memory(path: str | Path) -> dict | None:
    """--door-memory: {"format": 1, "doors": {"D8@90": [{"at", "t", "frame", ...}, ...]}}.
    No file yet: an empty memory. One that does not parse whole: None -- not
    used, and never written over (a person looks at it; the seed still holds)."""
    path = Path(path)
    if not path.exists():
        return {"format": 1, "doors": {}}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or doc.get("format") != 1 or not isinstance(doc.get("doors"), dict):
            raise ValueError("format is not 1")
        for key, rows in doc["doors"].items():
            if not isinstance(rows, list):
                raise ValueError("%s is not a list" % key)
            for s in rows:
                ok = (isinstance(s, dict) and isinstance(s.get("frame"), str)
                      and all(isinstance(s.get(k), (int, float)) and not isinstance(s.get(k), bool)
                              and math.isfinite(s[k]) for k in ("at", "t")))
                if not ok:
                    raise ValueError("%s: a middle without at/t/frame" % key)
    except (OSError, ValueError) as e:
        print("door memory %s ignored (%s) -- not used, not written over" % (path, e), flush=True)
        return None
    return doc


def save_door_memory(path: str | Path, doc: dict) -> bool:
    """Written whole to PATH.tmp, then replaced (as save_state)."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)      # a PC run before the launcher made nav/
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError as e:
        print("(door memory %s not written: %s)" % (path, e), flush=True)
        return False


class ParentWatch:
    """`--watch-parent`: when the process that started this one dies (the
    robot's launcher, scripts/emma_nav.py), stop as if Ctrl-C was pressed --
    the KeyboardInterrupt path cancels the board's action, logs and
    summarises. Without it a launcher killed with the app left drive_route
    driving with nobody holding the tether (design 2026-10-08 §4). On Android
    an orphan is re-parented to init, so the parent id changes; that is the
    whole check. `getppid` and `interrupt` are injectable for tests (a SIGINT
    to the test process would end pytest).

    `parent` is the launcher's own pid, passed on the command line (review
    finding 10): a baseline read here, after numpy/httpx imports and argparse,
    is init's 1 when the launcher died in that second -- and then the watch
    never fires and the leg drives untethered. main() refuses to start when
    the parent is already not that pid; 0/None = the old getppid() baseline."""

    def __init__(self, getppid=os.getppid, interrupt=None, every: float = WATCH_PARENT_S,
                 parent: int | None = None):
        self.getppid, self.every = getppid, every
        self.interrupt = interrupt or (lambda: signal.raise_signal(signal.SIGINT))
        self.parent = parent if parent else getppid()
        self.reason: str | None = None
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self.loop, name="watch-parent", daemon=True)

    def start(self) -> "ParentWatch":
        self.thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def check(self) -> bool:
        """True (once) when the parent has gone: the reason is recorded first,
        so the interrupted log line can say why."""
        if self.reason is None and self.getppid() != self.parent:
            self.reason = "launcher_lost"
            print("the launcher (pid %d) has gone -- stopping" % self.parent, flush=True)
            self.interrupt()
            return True
        return False

    def loop(self) -> None:
        while not self._stop.wait(self.every):
            if self.check():
                return


def main(argv: list[str] | None = None) -> int:
    utf8_output()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("route")
    ap.add_argument("--relay", default="http://127.0.0.1:11448")
    ap.add_argument("--dry-run", action="store_true", help="no motion: one checkpoint where the robot stands")
    ap.add_argument("--from-step", type=int, default=1)
    ap.add_argument("--min-battery", type=int, default=25)
    ap.add_argument("--vmax", type=float, default=LANE_VMAX, help="cruise speed for velocity-controlled lanes, m/s")
    ap.add_argument("--wmax", type=float, default=0.6, help="max turn rate for velocity control, rad/s")
    ap.add_argument("--spin", type=float, default=SPIN_FAST,
                    help="spot turns' rate while more than 45° is left, rad/s (owner 8 ต.ค.: \"ลองเพิ่มความเร็ว\"); "
                         "the last 45° stay at the measured 0.5; 0.5 or under = off")
    ap.add_argument("--speed-scale", type=float, default=SPEED_SCALE,
                    help="dial --vmax and --spin back together, 0 < s <= 1 (0.78 = the lane speed of 9 ต.ค.); "
                         "direct steps, doorways and docking keep their own speeds")
    ap.add_argument("--sdk", default="127.0.0.1:11445",
                    help="SDK port relay (velocity control); 'off' to drive with the old MoveBy bursts")
    ap.add_argument("--adb-serial", default="192.168.1.197:5555",
                    help="robot's adb serial, used to put the relay's port forward back if it drops")
    ap.add_argument("--door-width", type=float, default=rl.ROBOT_WIDTH,
                    help="robot width counted in doorways, m (default the owner's padded 0.78; he said the "
                         "figures carry 3-5 cm of padding)")
    ap.add_argument("--arms-in", action="store_true",
                    help="the arms hang straight down beside the body: count the base's 0.70 m, not the padded "
                         "0.78 across the arms, in doorways, lanes and spot turns (7 ต.ค.: D8 and D11 passed both "
                         "ways so, arms not folded -- the owner)")
    ap.add_argument("--door-margin", type=float, default=DOOR_MIN_MARGIN,
                    help="least free space a side (m) for a door push; D8/D11 leave 0.038")
    # -- legs, for the robot's launcher (scripts/emma_nav.py, design 2026-10-08 §2g).
    # None of them given = the run is exactly what it was before they existed.
    ap.add_argument("--to-step", type=int, default=None,
                    help="last step (1-based, inclusive) -- a checkpoint or the dock; ends with leg_done")
    ap.add_argument("--status-file", default=None, help="keep this file at the run's current state (JSON)")
    ap.add_argument("--state-out", default=None, help="at the end, write what the next leg needs to know")
    ap.add_argument("--state-in", default=None, help="what the previous leg wrote with --state-out")
    ap.add_argument("--dock-turn-test", type=int, default=None, metavar="N",
                    help="supervised, from the dock: out to the pre-dock spot, N x (half turn clockwise + a look "
                         "at the dock's wall, half turn back + a look at ea_east), then back onto the dock -- "
                         "the half turn's error each way (Runner.dock_turn_test); nothing else of the route runs")
    ap.add_argument("--door-memory", default=None, metavar="PATH",
                    help="where each door's middle is remembered across runs (read before a `remember` door, "
                         "added to after it); the robot's nav dir. Without it only the route's seed is used")
    # PID: the launcher's own pid (scripts/emma_nav.py passes os.getpid()). Bare
    # = the old getppid() baseline, for a caller that does not pass one.
    ap.add_argument("--watch-parent", nargs="?", type=int, const=0, default=None, metavar="PID",
                    help="stop (as Ctrl-C) when the process that started this one (PID) dies")
    ap.add_argument("--pid-file", default=None,
                    help="a run.pid that names this process: removed at the end if it still does "
                         "(scripts/robot_python.py run)")
    # None = ROOT/SHADOW_FIELD, resolved in _main (not str(...) here: see SHADOW_FIELD)
    ap.add_argument("--shadow-map", default=None,
                    help="our lidar map (scripts/eval_scan_localization.py --save-field; default <repo>/"
                         + SHADOW_FIELD.as_posix() + "): matched at every "
                         "checkpoint and LOGGED only (shadow_fix); a missing file just means no rows")
    ap.add_argument("--no-shadow-map", action="store_true", help="do not load or match the lidar map at all")
    args = ap.parse_args(argv)

    # Before anything else, and for EVERY run, not only --watch-parent (review
    # finding 1): SIGINT has to raise KeyboardInterrupt. A process started in
    # the background by a non-interactive shell -- `robot_python run`'s
    # `(nohup ... &)` over adb -- inherits SIGINT *ignored*, and CPython only
    # installs its handler over an inherited default. `robot_python stop` and
    # `emma_nav.py stop` then reported "sent" while the robot kept driving.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    watch = None
    if args.watch_parent is not None:
        # The parent has to be the one that started us, not init after it
        # already died (review finding 10): with the launcher's pid given, a
        # launcher that is gone before we got here refuses the leg before the
        # board is touched -- nobody would hold its tether.
        if args.watch_parent and os.getppid() != args.watch_parent:
            print("REFUSED: the launcher (pid %d) is already gone (parent is %d)"
                  % (args.watch_parent, os.getppid()), flush=True)
            return EXIT_REFUSED
        watch = ParentWatch(parent=args.watch_parent or None).start()
    try:
        return _main(args, watch)
    finally:
        if watch is not None:
            watch.stop()
        if args.pid_file:
            release_pid_file(Path(args.pid_file))


def release_pid_file(path: Path, pid: int | None = None) -> bool:
    """Remove a run.pid that still names this process (review finding 12). A
    PC `robot_python run` writes run.pid and nothing used to remove it: the
    dead pid outlived the run, and once Android reused it, STOP signalled (and
    the app's kill -9'd) an unrelated process, `status` showed a PC run that
    locked every card, and the next app start's reap cancelled a board action
    somebody else started. Compare-then-unlink: a run.pid that names another
    process is someone else's lock and stays."""
    pid = os.getpid() if pid is None else pid
    try:
        if path.read_text(encoding="utf-8").strip() == str(pid):
            path.unlink()
            return True
    except OSError:
        pass
    return False


def _main(args: argparse.Namespace, watch: "ParentWatch | None") -> int:
    if not 0.0 < args.speed_scale <= 1.0:
        # a knob to dial back, not up: faster than LANE_VMAX is --vmax, said on purpose
        print("REFUSED: --speed-scale %g is outside 0 < s <= 1" % args.speed_scale, flush=True)
        return EXIT_REFUSED
    route = rl.load_route(args.route)
    if args.to_step is not None:
        why = validate_to_step(route, args.from_step, args.to_step)
        if why is not None:
            print("REFUSED: %s" % why, flush=True)
            return EXIT_REFUSED
    if args.dock_turn_test is not None:
        why = (None if 1 <= args.dock_turn_test <= 5 else "--dock-turn-test %d: 1-5 goes" % args.dock_turn_test)
        if why is None and (args.from_step != 1 or args.to_step is not None or args.dry_run):
            why = "--dock-turn-test starts on the dock and is all the run does (no --from-step/--to-step/--dry-run)"
        if why is None and (not route.start_from_dock or route.dock_gap is None or not route.dock_check):
            why = "--dock-turn-test needs a route from the dock with dock_gap and dock_check"
        if why is not None:
            print("REFUSED: %s" % why, flush=True)
            return EXIT_REFUSED
    runs = ROOT / "data" / "robot" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    log_path = runs / ("%s_%s.jsonl" % (time.strftime("%Y%m%d-%H%M%S"), Path(args.route).stem))
    def reforward() -> None:
        import subprocess
        adb = ROOT / "tools" / "android" / "platform-tools" / "adb.exe"
        port = args.relay.rsplit(":", 1)[-1].strip("/")
        if adb.exists() and args.adb_serial:
            try:
                subprocess.run([str(adb), "connect", args.adb_serial], capture_output=True, timeout=10)
                for p in {port, args.sdk.rsplit(":", 1)[-1]} - {"off"}:
                    subprocess.run([str(adb), "-s", args.adb_serial, "forward", "tcp:%s" % p, "tcp:%s" % p],
                                   capture_output=True, timeout=10)
            except subprocess.TimeoutExpired:
                pass            # Wi-Fi still down; the caller's retries decide when to give up
    board = Board(args.relay, reforward=reforward)
    try:
        board.power()
    except (httpx.HTTPError, Stop) as e:
        print("board not reachable at %s (%s) — start the relay (docs/robot-arrival-2026-09-09.md)" % (args.relay, e))
        return 2
    r = Runner(board, route, log_path, args.min_battery)
    r.from_step, r.to_step = args.from_step, args.to_step
    if args.status_file:
        r.status_path = Path(args.status_file)
    if args.state_out:
        r.state_out = Path(args.state_out)
    if args.state_in:
        load_state(r, args.state_in)
    if args.door_memory:
        r.door_memory_path = Path(args.door_memory)
    r.door_min_margin = args.door_margin
    r.door_width = args.door_width
    if args.arms_in:
        r.door_width = min(r.door_width, rl.ROBOT_BASE_WIDTH)
        r.turn_width = rl.ROBOT_BASE_WIDTH
        print("arms in: counting %.2f m in doorways and for spot turns" % rl.ROBOT_BASE_WIDTH)
    vmax, spin = run_speeds(args)
    r.limits = lane_limits(vmax, args.wmax, spin)
    r.direct_limits = lane_limits(min(vmax, DIRECT_V_MAX), args.wmax, spin)
    if args.sdk != "off":
        from app import slamware_sdk as sw
        host, port = args.sdk.rsplit(":", 1)
        try:
            r.sdk = sw.SlamwareSdk(host, int(port))
            r.sdk.call("getsdpversion")
            r.sdk_addr = (host, int(port))
            print("velocity control via SDK %s" % args.sdk)
        except (OSError, sw.SdkError) as e:
            r.sdk = None
            print("SDK %s not reachable (%s) — driving with MoveBy bursts" % (args.sdk, e))
    print("route %r, %d steps, log %s" % (route.name, len(route.steps), log_path))
    r.log(kind="run_start", route=route.name, steps=len(route.steps), from_step=args.from_step, vmax=round(vmax, 3),
          wmax=args.wmax, spin=round(r.limits.w_spin_fast or r.limits.w_spin, 3), speed_scale=args.speed_scale,
          arms_in=args.arms_in, door_width=r.door_width)
    # the default map under ROOT as it is NOW, not as it was at import (SHADOW_FIELD)
    shadow_map = (None if args.no_shadow_map
                  else Path(args.shadow_map) if args.shadow_map is not None else ROOT / SHADOW_FIELD)
    r.shadow, r.shadow_why = load_shadow_field(route, shadow_map)
    print("lidar map (log only): %s" % r.shadow_why)
    r.log(kind="shadow_map", loaded=r.shadow is not None, why=r.shadow_why)
    if args.dry_run:
        try:
            r.preflight(moving=False)
            first = next(s for s in route.steps if s.do == "checkpoint")
            probe = rl.Step(do="checkpoint", use=first.use, doors=first.doors, label="dry run: " + first.label)
            guess = board.pose()
            pts = np.vstack([board.scan() for _ in range(2)])
            if len(pts) < 200:
                time.sleep(0.5)
                pts = np.vstack([board.scan() for _ in range(2)])
            fix = rl.correct_pose(guess, pts, route.anchors_for(probe), route.doors_for(probe))
            print("pose on the board : (%.3f, %.3f, %.1f°)" % (guess[0], guess[1], math.degrees(guess[2])))
            print("fix               :", fix.summary())
            for m in fix.matches:
                print("  wall %-12s at %+.3f seen %+.3f (%.2f m)" % (m["anchor"], m["at"], m["seen"], m["length"]))
            if r.shadow is not None:
                r.shadow_fix(probe.label, guess, board.raw_scan(), fix)
            print("(dry run: pose NOT changed)")
            return 0
        except Stop as e:
            print("STOP:", e)
            return 1
    try:
        # Preflight on its own: a Stop here is a refusal before anything moved
        # (exit 3, `refused`), not a stop mid-route (exit 1). The launcher keeps
        # the robot's known place only for the first (build review #6/#7).
        try:
            r.preflight(moving=True, from_step=args.from_step)
        except Stop as e:
            r.say("REFUSED: %s" % e)
            r.log(kind="refused", code=e.code, reason=str(e))
            return EXIT_REFUSED
        board.set_speed(LANE_SPEED)                 # velocity lanes raise it for themselves (_board_speed)
        if args.dock_turn_test is not None:
            r.dock_turn_test(args.dock_turn_test)
            return 0
        r.run(args.from_step, args.to_step)
        return 0
    except Stop as e:
        r.say("STOP: %s" % e)
        try:
            pose = board.pose()
        except (httpx.HTTPError, Stop):
            pose = None                             # the link is what ended it
        r.log(kind="stop", reason=str(e), code=e.code, pose=pose)
        return 1
    except KeyboardInterrupt:
        # A STOP can arrive twice: under the launcher by design (its tether,
        # then `emma_nav.py stop`, design §4), and from the PC an operator who
        # presses stop again (review finding 1: every run, not only a watched
        # one). The second must not land in here and skip the cancel. A hang
        # is the app's kill -9's job, and `robot_python stop`'s escalation.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            board.cancel()
        except (httpx.HTTPError, Stop):
            pass
        r.say("interrupted — cancelled")
        # it used to be said only; the launcher reads why from the log/status
        r.log(kind="interrupted", code=None if watch is None else watch.reason)
        return 130
    except Exception as e:                          # noqa: BLE001
        # Review finding 27: an uncaught exception (httpx.HTTPStatusError out
        # of preflight's raise_for_status, a bug) left as Python's exit 1,
        # which is EXIT_STOP -- read as "stopped mid-way" for a robot that may
        # never have moved. Its own exit code and log kind; the finally below
        # still cancels the board's action and puts the speed back.
        import traceback
        traceback.print_exc()
        r.say("ERROR: %r" % (e,))
        r.log(kind="error", code="error", reason=repr(e)[:300])
        return EXIT_ERROR
    finally:
        try:
            board.cancel() if board.current() is not None else None
            board.set_speed(NORMAL_SPEED)
        except (httpx.HTTPError, Stop):
            pass                                    # link down: the exit code and the summary still stand
        if not args.dry_run:
            # what the run did, read back from its own log (scripts/analyze_run.py);
            # never the reason a run reports failure
            try:
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                import analyze_run
                r.log_file.flush()
                print(analyze_run.analyze(log_path), flush=True)
            except Exception as e:                      # noqa: BLE001
                print("(run summary failed: %s)" % e)


if __name__ == "__main__":
    sys.exit(main())
