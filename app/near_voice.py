"""
Near voices in, far voices out: the lidar says whether anybody stands at the robot.

Owner, 5 Nov 2026, after "listen to one voice" failed on the robot (0 refusals,
11 short phrases of the person on file dropped): "พยายามให้หุ่นฟังเสียงคนใกล้
คนไกลออก หรือมีวิธีอื่นไหม". `VOICE_NEAR_DOOR`:

    off     (default) nothing is read; focus's rules are byte-identical to before
    shadow  every door decision also says, on its focus_open / focus_deny row, what
            this door would have done (`near_state`, `near_m`, `near_would`).
            Nothing she hears changes. This is how the numbers below get real.
    on      a voice with no face in view on a call the camera opened (the only place
            focus's rule 2 applies) opens because somebody stands within VOICE_NEAR_M
            -- and is refused when nobody does, whatever the level or the reply window.

**Why not the level.** Level is what the door has today (FOCUS_NEAR_RMS, the 0.02
floor) and it does not measure distance on this robot: over 89 face-verified opens
(Oct 10 - Nov 5) face width against voice level has Spearman -0.09 (trigger frame) and
0.12 (5 s peak); a face at ~0.9 m and one at ~2 m read the same 0.046/0.044. The door re-asks
on every frame of a sentence, so FOCUS_NEAR_RMS is a one-20-ms-frame peak detector: of
196 voices it let in, 13% asked anything (reply window 36%, a pressed call 54%).

**Replayed on the robot's logs** (`scripts/replay_near_voice.py`; standing robot, past
VOICE_NEAR_WARM_S after a walk, 4-5 Nov, the only days with lidar rows; 94 voices that
the waivers let in with no face; "for her" and "not" from the offline router's gates,
never read by anyone, and noisy):

                                  for her kept    not for her refused
    today                         22/22 100%       0/39   0%
    level >= 0.10 alone           14/22  64%      19/39  49%
    level >= 0.12 alone           13/22  59%      22/39  56%
    level >= 0.20 alone           10/22  45%      28/39  72%
    lidar <= 1.5 m alone          14/22  64%      27/39  69%
    lidar <= 1.5 m or >= 0.20     19/22  86%      18/39  46%   <- this door
    lidar <= 1.0 m alone          11/22  50%      33/39  85%

The two days split the way they should. 5 Nov, the owner standing at the robot testing:
27 of 29 kept, 11 of 11 "for her". 4 Nov, the robot answering the office's talk ("พูดเอง
ด้วย"): 33 of 65 kept, 18 of 32 "not for her" refused. And 11 voices refused "no face
in front" that day had somebody within 1.5 m: the near half of the owner's ask. Rows are
logged every 10 s only when somebody is found, so all of this approximates the door.

**What it cannot do.** It says somebody is near, not who is speaking: with a guest
at the robot, a far voice still passes. In a crowd somebody is always near; only a mic
at the mouth or the array would help there. Nothing has been measured with a person at a
known distance, and one number is a worry: of 10 arrivals the camera recognised on 4-5 Nov,
the lidar counted nobody at 7 (too far for its 2.5 m, just moved, or really unseen: not
known). `docs/near-voice-bench.md` is that test, and it decides `on`.

**The lidar loses people, three ways, all measured on `lidar_people.Tracker`.**
* A still person joins the background at 96.5 s (BG_QUANTILE of BG_WINDOW_S; on the robot
  p90 of a continuous sighting is 65-89 s), and a call lasts minutes. So the door keeps
  an anchor: where somebody was last seen near, for as long as the newest scan
  still has a return at that spot. Reads a bin list the poller already made.
* Somebody already there when the robot stops is background from the first scan: 0 of
  120 scans, until a leg shifts ~10 cm. So for VOICE_NEAR_WARM_S after any move the door
  has no opinion. The tracker's window (120 s) is not this clock: it is shorter than
  a guest may stand still after Emma walked up to them.
* The tracker reads only while HEAD_FOLLOW and LIDAR_PEOPLE are on, and not on a walk.

**Every failure opens.** No fresh scan, warming up, a tracker that raises, an unknown
mode: `UNKNOWN`, and focus's rules decide exactly as they do with this door off. A deaf
robot is the worse failure. While she moves `focus._robot_moving` answers first and
this is never asked: a "หยุด" from anywhere reaches the only thing that can stop her.

**Same door as the floor and focus.** It lives inside `focus._decide`, so a "no" is
silence, VAD_HEAD_HOLD_MS keeps the head of the sentence, and the next frame asks again:
somebody who walks up mid-sentence is heard from its start. It decides only at
the opening, so there is no second hangover clock and an open sentence is never cut.

**Cost.** One asyncio wake-up a second (`_watch`, only with the switch on) and, per
door frame, a few list reads: nothing on the audio path that is not already there, and
no new scan. The poller it reads is the head's own. `SILENCE_RMS` comes first, in the gate.
"""
from __future__ import annotations

import asyncio
import logging
import time

from app.config import settings

logger = logging.getLogger("condo_voice.near_voice")

OFF, SHADOW, ON = "off", "shadow", "on"
#: What the door knows. UNKNOWN = no opinion: focus decides as if the door were off.
NEAR, NOBODY, UNKNOWN = "near", "nobody", "unknown"

#: A history span under this is a tracker that has just been reset (the robot moved).
RESET_SPAN_S = 5.0
#: One sighting holds at most this long by its spot alone: a coat left on a chair keeps
#: the door open (fail-open), but not all day.
ANCHOR_MAX_S = 900.0
#: "Still there": a return in the newest scan within this many bins (2 deg each) of where
#: they were seen, and this far nearer / farther. Their centre sits between two legs.
ANCHOR_BINS = 3
ANCHOR_NEARER_M = 0.35
ANCHOR_FARTHER_M = 0.20
WATCH_EVERY_S = 1.0

STATE: dict = {"anchor": None, "still_since": None, "last_span": 0.0, "task": None, "warned": set()}


def mode() -> str:
    """A typo lands on today's behaviour, never on a stricter robot."""
    value = str(getattr(settings, "voice_near_door", OFF) or OFF).strip().lower()
    return value if value in (SHADOW, ON) else OFF


def enabled() -> bool:
    return mode() != OFF


def reset() -> None:
    """Tests only: the room is not per call, so no session resets this."""
    STATE.update(anchor=None, still_since=None, last_span=0.0)
    STATE["warned"] = set()


def _warn_once(key: str, msg: str, *args) -> None:
    if key not in STATE["warned"]:
        STATE["warned"].add(key)
        logger.warning(msg, *args)


def _occupied(tracker, anchor: tuple[float, float, float]) -> bool:
    """Is there still a return where they stood? The tracker's own list for its newest scan."""
    from app import lidar_people

    if not tracker.history:
        return False
    bins = tracker.history[-1][1]
    bearing, distance = anchor[0], anchor[1]
    centre = int(((bearing % 360.0) // lidar_people.BIN_DEG)) % lidar_people.BINS
    lo, hi = distance - ANCHOR_NEARER_M, distance + ANCHOR_FARTHER_M
    return any(lo <= bins[(centre + k) % lidar_people.BINS] <= hi
               for k in range(-ANCHOR_BINS, ANCHOR_BINS + 1))


def _observe(now: float) -> str | None:
    """Bring the anchor and the stillness clock up to `now`. Returns why there is no
    opinion, or None."""
    from app import lidar_people, robot_chassis

    if not lidar_people.enabled():
        _warn_once("lidar_off", "VOICE_NEAR_DOOR needs HEAD_FOLLOW and LIDAR_PEOPLE: "
                                "every voice is judged as if it were off")
        STATE.update(anchor=None, still_since=None, last_span=0.0)
        return "lidar_off"
    if not robot_chassis.configured():
        _warn_once("no_chassis", "VOICE_NEAR_DOOR needs ROBOT_CHASSIS_URL: "
                                 "every voice is judged as if it were off")
        STATE.update(anchor=None, still_since=None, last_span=0.0)
        return "no_chassis"
    tracker = lidar_people.STATE["tracker"]
    if not tracker.history:
        # A walk: the poller threw the background away and stops updating.
        STATE.update(anchor=None, still_since=None, last_span=0.0)
        return "stale"
    if now - tracker.at > lidar_people.FRESH_S:
        # A board slow to answer: no opinion now, and nothing forgotten. A guest the tracker
        # has already absorbed could not be found again. If she moved meanwhile, the next
        # scan resets the tracker and the span below says so.
        return "stale"
    span = tracker.history[-1][0] - tracker.history[0][0]
    if STATE["still_since"] is None or span < RESET_SPAN_S or span < STATE["last_span"] - 2.0:
        # The background was thrown away: whoever stands here now is in the new one.
        STATE["still_since"] = now - span
        if span < RESET_SPAN_S or span < STATE["last_span"] - 2.0:
            STATE["anchor"] = None
    STATE["last_span"] = span
    reach = float(settings.voice_near_m)
    seen = [p for p in lidar_people.people(now) if p.distance <= reach]
    if seen:
        nearest = min(seen, key=lambda p: p.distance)
        STATE["anchor"] = (nearest.bearing, nearest.distance, now)
    elif STATE["anchor"] is not None:
        if now - STATE["anchor"][2] > ANCHOR_MAX_S or not _occupied(tracker, STATE["anchor"]):
            STATE["anchor"] = None
    return None


def look(now: float) -> tuple[str, float | None, str]:
    """(NEAR | NOBODY | UNKNOWN, metres to the nearest near person or None, why).

    Never raises: this is asked from inside the listening door."""
    try:
        why = _observe(now)
        if why is not None:
            return UNKNOWN, None, why
        anchor = STATE["anchor"]
        if anchor is not None:
            return NEAR, anchor[1], "seen" if anchor[2] == now else "still"
        if now - STATE["still_since"] < float(settings.voice_near_warm_s):
            return UNKNOWN, None, "warming"
        return NOBODY, None, "nobody"
    except Exception:                                    # noqa: BLE001 -- see the docstring
        if "error" not in STATE["warned"]:
            STATE["warned"].add("error")
            logger.exception("near door: no opinion (every voice as if it were off)")
        return UNKNOWN, None, "error"


async def _watch() -> None:
    """A person who arrives and stands silent still needs seeing before the tracker
    absorbs them: nobody is asking at the door then."""
    while True:
        await asyncio.sleep(WATCH_EVERY_S)
        if enabled():
            look(time.monotonic())


def start() -> None:
    """From main's startup hook. No task with the switch off."""
    if not enabled():
        return
    task = STATE["task"]
    if task is None or task.done():
        STATE["task"] = asyncio.create_task(_watch())


async def stop() -> None:
    task, STATE["task"] = STATE["task"], None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):      # noqa: BLE001
            pass
