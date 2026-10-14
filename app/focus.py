"""
Listening focus: talk to the person in front of the robot, and know who spoke.

Owner, 9 Oct 2026, after the robot answered two people chatting beside it:
"ให้หุ่นดูด้วยไหมว่ามีคนอยู่หน้ากล้องเท่านั้นถึงจะคุยด้วย … แยกเสียงด้วยได้ไหม
… รู้ด้วยว่าคุยกับใครอยู่ … พูดชื่อคนที่คุยกับหุ่นด้วย" — then "ทำทั้งหมดเลย".
The spec and the API every producer codes against is
docs/listening-focus-2026-10-09.md; this module is the one arbiter.

Why it is needed at all: the robot's profile (`joke`) listens far, so its
call has no loudness floor, and Silero answers "is somebody speaking", never
"is somebody speaking *to us*". Every voice in the room reached Gemini.

Two questions, one place:

1. **May a speech segment open now?** `VadGate.feed` asks `may_open` once per
   opening attempt, exactly where the loudness floor sits. "No" behaves like
   the floor: the audio stays in the pre-roll and nothing goes upstream. The
   gate asks again on the next frame, so a face that turns toward the robot
   mid-sentence opens the segment then, head intact.
2. **Who spoke this segment?** `segment_closed` attributes a closed segment
   (mouth -> face, DOA breaks ties, a voice cluster carries the person across
   moments the face is not visible) and returns a `SpeakerLabel`.

Four optional inputs (faces, mouth activity, voice clusters, direction of
arrival) each arrive through an `observe_*` call from the producer's own
thread. Each is append + prune under one lock — never more than that, the
producers are a camera loop and an audio path.

Fail open per input, closed per switch: an input that is off, crashed or
silent simply stops contributing, and the rules that need it are skipped.
The exception is the switch that makes a face *required*: with
FOCUS_REQUIRE_FACE on and no camera frames, the robot does not listen and
the reason says "no camera" — a robot that silently fell back to hearing the
whole room is the exact failure this exists to end.

Every decision is a turn-log row (`focus_open` / `focus_deny` / `focus_label`
/ `focus_voice`) with numbers only: counts, widths, angles, activity, offsets,
cluster ids, rms. No transcript text, and no names — a label row says
`named=true`, never the name.

State that is about the *room* (faces, mouths, DOA) survives `reset()`: the
person standing there when a new session opens is still standing there.
State that is about the *conversation* (ordinals, voice clusters, the main
speaker) does not — one visitor's voice print is never the next one's
business, and the spec forbids keeping prints past the session.

Engagement (owner, 9 Oct 2026: "ลองปรับให้เหมือนหุ่นต้อนรับอื่นให้ดีที่สุด"),
each behind its own default-off switch and all inside this one arbiter:

- `heard_name` — Google Look and Talk's other half: outside the zone, say
  the name. The in-call wake word (app/call_wake.py) opens a window in which
  rule 2 is waived (FOCUS_WAKE).
- `engage_mode` "full" — Pepper's FullyEngaged: once the session has a main
  person (`_main_person`, the one notion of "main"), other faces and voices
  are denied `NOT_MAIN` / `ANOTHER_VOICE` until that person leaves.
- `attention` — Furhat's gaze: the bearing the robot's face should turn to.
- `calling_out_of_view` — a voice denied "no face in front" again and again
  from one direction: somebody worth inviting in (FOCUS_INVITE_DENIED_N).
"""
from __future__ import annotations

import logging
import math
import threading
import time
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass

from app.config import settings

logger = logging.getLogger("condo_voice.focus")


@dataclass(frozen=True)
class FaceObs:
    track: int              # stable per person while in view (facewatch's track id)
    t: float                # time.monotonic() of the frame
    bearing_deg: float      # horizontal angle from the camera axis, + = robot's left
    width_px: int           # face box width in the frame (distance proxy)
    yaw_deg: float | None   # head yaw (0 = facing the robot), facewatch._yaw
    name: str | None        # ONLY when recognised AND consent allows naming (consent.py rules)


@dataclass(frozen=True)
class SpeakerLabel:
    ordinal: int            # 1, 2, ... in order of first appearance in THIS session
    track: int | None       # face track if attributed
    name: str | None        # consented name, else None
    confidence: float       # 0..1
    why: str                # short machine reason, logged to turnlog


# ---- reasons (turn-log values; scripts/analyze_log.py groups by these) ----
OK = "ok"
OFF = "focus off"
NO_CAMERA = "no camera"
NO_FACE = "no face in front"
SILENT_FACES = "faces in front are silent"
ELSEWHERE = "voice from elsewhere"
ANOTHER_VOICE = "another voice"
#: FOCUS_ENGAGE_MODE=full: a face in front spoke, but not the main person's.
NOT_MAIN = "not the main speaker"
#: The robot is walking or gesturing: every voice is let through (see
#: `_robot_moving`).
ROBOT_MOVING = "robot moving"
#: Rule 2 waived: a conversation is under way (FOCUS_CONVERSE_HOLD_S), or the
#: voice is loud enough to be standing at the robot (FOCUS_NEAR_RMS).
CONVERSING = "in conversation"
NEAR_VOICE = "near voice"
#: Rule 2 waived: the segment opened within FOCUS_REPLY_WINDOW_S of her voice
#: ending -- the guest answering what she just said (`her_turn_ended`).
REPLY = "reply to her"

# ---- tunables (constants, not settings: tune from focus_* rows first) ----
#: Rule 3's window: mouth samples this recent count for "is anybody in front
#: moving their mouth". Silero opens ~0.25 s into speech, so the speaker's
#: mouth has moved inside it. A video path lagging the audio only delays the
#: opening: the gate re-asks on every frame and opens with its pre-roll.
MOUTH_WINDOW_S = 0.6
#: Activity (0..1, from app/active_speaker.py) at or above which a mouth
#: "moves". Not measured yet — set from focus_deny rows' `mouth` values.
MOUTH_MOVING = 0.35
#: segment_closed: the best mouth must lead the second by this much to win
#: alone; closer than that, DOA breaks the tie.
MOUTH_MARGIN = 0.15
#: Rule 4: DOA samples this recent, at least this confident, more than this
#: many degrees from every engaged face = the voice came from elsewhere.
DOA_WINDOW_S = 0.6
DOA_MIN_CONF = 0.5
DOA_TOLERANCE_DEG = 25.0
#: No frame from the camera loop for this long = no camera. The greeter
#: reads at FACE_FPS (6 on the robot) and a robot-camera read waits 1.5 s
#: before it calls a frame missing, so 3 s is two missed reads.
CAMERA_STALE_S = 3.0
#: Voice clusters below this confidence are not evidence for rule 5 or for
#: carrying a person across.
VOICE_MIN_CONF = 0.5
#: A cluster becomes the session's "main speaker" after this many segments
#: that a face in front was seen speaking.
MAIN_MIN_SEGMENTS = 2
#: Rule 5 holds for this long after a segment from another voice ended. It
#: must expire: once a segment is denied nothing new reaches the voice
#: prints, so a rule that waited for the "other voice" to stop would never
#: see it stop — and with no mouth input the robot would be deaf for the
#: rest of the session.
ANOTHER_VOICE_HOLD_S = 4.0
#: Labels below this confidence are not sent to the model (`context_note`).
LABEL_MIN_CONF = 0.5
#: Perception older than this is pruned on every append.
HISTORY_S = 15.0
#: Hard caps — memory is bounded however long the robot runs.
MAX_FRAMES = 128
MAX_TRACKS = 16
MAX_MOUTH_PER_TRACK = 64
MAX_DOA = 128
MAX_SEGMENTS = 64
#: A deny row is written once per burst: denied frames closer together than
#: this are one denied utterance (Silero says "speech" on every 20 ms frame
#: of it), and a long one still gets a row at most every DENY_LOG_EVERY_S.
DENY_BURST_GAP_S = 1.0
DENY_LOG_EVERY_S = 2.0
#: heard_name: the DOA samples over the name itself (it ends ~0.3 s before
#: the detector fires; "เอ็มม่า" is about a second) give the caller's side.
NAME_DOA_WINDOW_S = 1.5
#: calling_out_of_view: denied utterances this recent, every bearing within
#: this many degrees of their median, are one caller in one place.
CALLING_WINDOW_S = 20.0
CALLING_SPREAD_DEG = 20.0
#: focus_gap rows: nobody engaged for longer than FOCUS_HOLD_S and at most
#: this long, then somebody engaged again — the numbers FOCUS_GOODBYE_AFTER_S
#: is tuned from (a person reading a brochure vs a person who left).
GAP_LOG_MAX_S = 60.0

_lock = threading.Lock()

# -- the room (survives reset) --
_frames: deque = deque(maxlen=MAX_FRAMES)           # (t, tuple[FaceObs, ...])
_last_frame_t: float | None = None
_last_engaged_t: float | None = None
_mouth: "OrderedDict[int, deque]" = OrderedDict()   # track -> deque[(t, activity)]
#: Tracks whose mouth the active speaker has seen MOVE at least once (the
#: last time it did). Only these can be called "silent" — see `_proven`.
_moved: dict[int, float] = {}
_doa: deque = deque(maxlen=MAX_DOA)                 # (t, bearing | None, confidence)
#: The last moment a voice reached the door: Silero flagged speech above the
#: loudness floor and `may_open` was asked, allowed OR denied. Engagement
#: needs the denied half: a guest reading a brochure (face lost), a child
#: under the lens or a caller beside it speaks without any segment opening,
#: and "no open segment" read as "nobody talking" let the goodbye be said —
#: and the call hung up — over a question (review 10 Oct).
_voice_at: list = [None]
#: When the robot last invited somebody in (FOCUS_INVITE). Room state, on
#: purpose: the standing line rings a fresh line seconds after the last one
#: ends, so a per-line rate limit reset with every ring (review 10 Oct).
_invited: dict = {"t": None}

# -- the conversation (cleared by reset) --
_voice: "OrderedDict[int, tuple]" = OrderedDict()   # segment -> (cluster, conf, t0, t1)
_segments: "OrderedDict[int, dict]" = OrderedDict()  # segment -> attribution
_people: list[dict] = []                            # {ordinal, tracks, clusters, name, segs}
_ordinals = {"last": 0}                             # the last ordinal handed out
_session_t = [0.0]                                  # monotonic time of the last reset
_cluster_person: dict[int, dict] = {}
_track_person: dict[int, dict] = {}
_deny_burst: dict = {}                              # {reason, last, logged}
_stats: Counter = Counter()
_last_decision: dict = {}
#: The out-of-view window the in-call wake word opened (heard_name).
_called: dict = {"t": None, "until": float("-inf"), "bearing": None}
#: Full engagement: segments that ended before `epoch` no longer count
#: toward the main person — the one who left is released (`_present_main`).
_main: dict = {"epoch": float("-inf")}
#: "No face in front" denials, one per utterance: (t, DOA bearing | None).
_calls: deque = deque(maxlen=32)


def enabled() -> bool:
    """settings.focus_enabled — the master switch, read at call time — and
    never under MULTI_SESSION (review 10 Oct).

    Focus is one room's state: one camera, one main person, one set of
    voices. MULTI_SESSION has many testers' browsers and no robot — nobody
    becomes `_active`, so `focus.reset()` (which runs only for the single
    session) never ran, `_people` grew for the life of the process across
    every tester, and with FOCUS_REQUIRE_FACE a camera nobody stands in
    front of denied every tester's speech. Off there, said once."""
    global _multi_warned
    if not settings.focus_enabled:
        return False
    if settings.multi_session:
        if not _multi_warned:
            _multi_warned = True
            logger.warning("FOCUS_ENABLED ignored — MULTI_SESSION has many conversations "
                           "and no robot in front of them; listening focus is off")
        return False
    return True


_multi_warned = False


def labels_deliverable() -> bool:
    """FOCUS_SPEAKER_LABELS on AND a road for the note to travel.

    The prompt line that explains "[ผู้พูด: …]" and the greeting that lets a
    name back in "only on a labelled turn" are promises about notes. Review
    10 Oct: both were attached whenever the two switches were on, including
    where no note can ever be sent — Gemini 3.1 Flash Live (its client
    content is history only; `send_context_note` refuses), Gemini-side VAD
    (no local segments, nothing to label), OpenAI, and after the process
    latched notes off when one stalled a turn (`_NOTE_STATE`). The model was
    told labels exist, and the greeting dropped "never use the name again"
    for a speaker check that would never come. One predicate for both.
    """
    if not (enabled() and settings.focus_speaker_labels):
        return False
    if (settings.provider or "gemini") != "gemini" or settings.vad_mode != "local":
        return False
    from pathlib import Path

    if not Path(settings.vad_model).expanduser().is_file():
        return False                      # VAD_MODE=local falls back to Gemini's
    model = (settings.gemini_model or "").removeprefix("models/")
    if model.startswith("gemini-3.1-flash-live"):
        return False
    import sys

    gemini = sys.modules.get("app.providers.gemini")
    state = getattr(gemini, "_NOTE_STATE", None)
    return not (isinstance(state, dict) and state.get("stalled"))


def engage_mode() -> str:
    """FOCUS_ENGAGE_MODE: "full" or "semi". Anything else is semi — a typo
    lands on today's behaviour, never on a stricter robot."""
    return "full" if str(settings.focus_engage_mode or "").strip().lower() == "full" else "semi"


# ---- producers ----------------------------------------------------------

def _prune_left(dq: deque, horizon: float) -> None:
    while dq and dq[0][0] < horizon:
        dq.popleft()


def face_min_px() -> int:
    """The engagement width bar: FOCUS_FACE_MIN_PX, never stricter than the
    greeter's own FACE_MIN_PX.

    A face the robot greets is a face it listens to. The greeter greets a
    colleague from FACE_MIN_PX and a stranger from 1.3x that; the owner's
    room runs FACE_MIN_PX=50 so the doorway wakes the robot. With the focus
    bar at 90, everybody greeted at 50-89 px was spoken to and then denied
    "no face in front" for answering from where they stood — the 2026-08-31
    อาซู่ bug (greeted at the door, never heard) wearing a face, and the
    reason `VadGate.stand_down` exists for the loudness floor.
    """
    bar = int(settings.focus_face_min_px)
    greet = int(getattr(settings, "face_min_px", 0) or 0)
    return min(bar, greet) if greet > 0 else bar


def _engaged(face: FaceObs) -> bool:
    """Close enough and facing the robot. Yaw unknown counts as facing:
    a missing landmark is a missing input, and inputs fail open.

    FOCUS_YAW_MAX_DEG <= 0 turns the facing rule off (owner, 9 Oct 2026:
    "การหันหน้าเข้ากล้องเอาออกดีกว่า") -- a customer reading the chest
    screen or talking to a companion beside the robot is still in front."""
    if face.width_px < face_min_px():
        return False
    limit = float(settings.focus_yaw_max_deg)
    if limit <= 0:
        return True
    return face.yaw_deg is None or abs(face.yaw_deg) <= limit


def observe_faces(faces: list[FaceObs], t: float) -> None:
    """Every processed camera frame; [] = nobody. Append + prune.

    A frame that brings an engaged face back after more than FOCUS_HOLD_S
    without one writes a `focus_gap` row (the gap, nothing else): how long
    the person in front goes missing while still there is the number the
    goodbye (FOCUS_GOODBYE_AFTER_S) must sit above, and it is only knowable
    from the robot's own camera."""
    global _last_frame_t, _last_engaged_t
    frame = tuple(faces)
    gap = None
    with _lock:
        _frames.append((t, frame))
        _prune_left(_frames, t - HISTORY_S)
        _last_frame_t = t if _last_frame_t is None else max(_last_frame_t, t)
        if any(_engaged(f) for f in frame):
            prev = _last_engaged_t
            if prev is not None and max(0.0, float(settings.focus_hold_s)) < t - prev <= GAP_LOG_MAX_S:
                gap = t - prev
            _last_engaged_t = t if _last_engaged_t is None else max(_last_engaged_t, t)
    if gap is not None and enabled():
        _log("focus_gap", gap_s=round(gap, 1))


def observe_mouth(track: int, t: float, activity: float) -> None:
    """Mouth activity 0..1 of one face track, ~ASD_FPS per track."""
    try:
        value = min(1.0, max(0.0, float(activity)))
    except (TypeError, ValueError):
        return
    with _lock:
        dq = _mouth.get(track)
        if dq is None:
            dq = _mouth[track] = deque(maxlen=MAX_MOUTH_PER_TRACK)
            while len(_mouth) > MAX_TRACKS:
                gone, _ = _mouth.popitem(last=False)
                _moved.pop(gone, None)
        else:
            _mouth.move_to_end(track)
        dq.append((t, value))
        _prune_left(dq, t - HISTORY_S)
        if value >= MOUTH_MOVING:
            _moved[track] = t if track not in _moved else max(_moved[track], t)


def observe_doa(t: float, bearing_deg: float | None, confidence: float) -> None:
    """Direction of arrival in the camera's frame (+ = robot's left);
    None = no single source."""
    try:
        conf = min(1.0, max(0.0, float(confidence)))
        bearing = None if bearing_deg is None else float(bearing_deg)
    except (TypeError, ValueError):
        return
    if bearing is not None and not math.isfinite(bearing):
        bearing = None
    with _lock:
        _doa.append((t, bearing, conf))
        _prune_left(_doa, t - HISTORY_S)


def observe_voice(segment: int, t0: float, t1: float, cluster: int, confidence: float) -> None:
    """The voice cluster of a segment (per-session clustering).

    Normally arrives after `segment_closed` — the voice print needs the
    whole segment — and links that segment's person to the cluster. One
    that arrives first is kept for `segment_closed` to use. A print of a
    segment that ended before this conversation began is dropped: it was
    computed for a session that has since ended, and its cluster ids mean
    nothing here (cluster ids are per session; segment ids per process).
    """
    try:
        conf = min(1.0, max(0.0, float(confidence)))
        cluster = int(cluster)
    except (TypeError, ValueError):
        return
    row = None
    with _lock:
        info = _segments.get(segment)
        if info is None and t1 < _session_t[0]:
            return
        _voice[segment] = (cluster, conf, t0, t1)
        while len(_voice) > MAX_SEGMENTS:
            _voice.popitem(last=False)
        if info is None:
            return                     # segment_closed will find it here
        info["cluster"] = cluster
        info["voice_conf"] = conf
        unattributed = info.get("person") is None
        person = None
        if conf >= VOICE_MIN_CONF:
            person = _link_cluster(info, cluster)
        row = {"segment": segment, "cluster": cluster, "conf": round(conf, 2),
               "ordinal": person["ordinal"] if person else None,
               # The voice named a person the camera could not: known only
               # now, after the segment's label (if any) was already sent.
               "late": unattributed and person is not None}
    _log("focus_voice", **row)


def heard_name(t: float) -> dict:
    """The wake word was heard inside a live call at `t` (app/call_wake.py).

    Google Look and Talk's rule, the half focus lacked: inside the zone, look
    and talk; outside it, say the name. For FOCUS_WAKE_WINDOW_S rule 2 (a face
    in front) is waived — "no camera" included, the name is as deliberate as
    the button a person-started call is heard through. Rules 3-5 keep judging
    every face in view: a caller behind a customer being served is still "a
    voice from elsewhere" when the array says so, and in full engagement mode
    still not the main person.

    Returns {"in_view", "camera", "bearing", "window_s"}: whether anybody was
    engaged when the name came, whether the camera is alive, and the median
    confident DOA over the name (None without one) — the invitation's side.
    """
    window = max(0.0, float(settings.focus_wake_window_s))
    if not enabled():
        return {"in_view": False, "camera": False, "bearing": None, "window_s": 0.0}
    with _lock:
        engaged = _engaged_faces(t)
        camera = _last_frame_t is not None and (t - _last_frame_t) <= CAMERA_STALE_S
        bearing = _doa_bearing(t - NAME_DOA_WINDOW_S, t + 0.1)
        _called["t"] = t
        _called["until"] = max(_called["until"], t + window)
        _called["bearing"] = bearing
    info = {"in_view": bool(engaged), "camera": camera, "bearing": bearing,
            "window_s": window}
    _log("focus_wake", in_view=info["in_view"], camera=camera,
         doa=None if bearing is None else round(bearing, 1), window_s=window)
    return info


def _called_at(t: float) -> bool:
    """Inside the out-of-view window the name opened (called under _lock)."""
    return _called["t"] is not None and t <= _called["until"]


# ---- the decision -------------------------------------------------------

def _latest_per_track(t_from: float, t_to: float) -> dict[int, FaceObs]:
    """The newest observation of each track seen in [t_from, t_to]."""
    seen: dict[int, FaceObs] = {}
    for ft, frame in _frames:
        if ft < t_from or ft > t_to:
            continue
        for face in frame:
            seen[face.track] = face
    return seen


def _engaged_in(t_from: float, t_to: float) -> dict[int, FaceObs]:
    """The newest *engaged* observation of each track seen in [t_from, t_to].

    Not "the newest observation, if engaged": the spec's rule is a face
    engaged *within* FOCUS_HOLD_S. Judging only the newest frame made one
    glance at a companion (yaw past FOCUS_YAW_MAX_DEG for a single frame at
    FACE_FPS, or one noisy keypoint) deny the customer who had been facing
    the robot a fifth of a second earlier — while the idle clock
    (`_last_engaged_t`) still counted them as engaged.
    """
    seen: dict[int, FaceObs] = {}
    for ft, frame in _frames:
        if ft < t_from or ft > t_to:
            continue
        for face in frame:
            if _engaged(face):
                seen[face.track] = face
    return seen


def _engaged_faces(t: float) -> dict[int, FaceObs]:
    hold = max(0.0, float(settings.focus_hold_s))
    # +0.5: a frame stamped a moment after the audio frame that asked (two
    # threads, two clocks of arrival) is still "now".
    return _engaged_in(t - hold, t + 0.5)


def _proven(tracks) -> list[int]:
    """The tracks whose mouth the active speaker has been seen to MOVE.

    Only those can be judged "silent". A face mask, a hand over the mouth or
    a heavy moustache gives 2d106det a face it is happy with and lips that
    never move: measured 9 Oct on insightface's t1.jpg with a painted mask
    over a jaw opening 4x a second, valid geometry on every frame and
    activity 0.00 (max 0.08) — so rule 3 denied that customer "faces in
    front are silent" on every word, for the whole visit. A mouth signal
    that has never shown it can see this mouth is a missing input, and
    inputs fail open (spec). The proof is per track, i.e. per person while
    in view, and it is room state: it survives `reset`.
    """
    return [k for k in tracks if k in _moved]


def _mouth_max(tracks, t_from: float, t_to: float) -> float | None:
    """Highest activity of these tracks in the window; None = no samples."""
    best = None
    for track in tracks:
        for mt, value in _mouth.get(track, ()):
            if t_from <= mt <= t_to:
                best = value if best is None else max(best, value)
    return best


def _mouth_mean(track: int, t_from: float, t_to: float) -> float | None:
    values = [v for mt, v in _mouth.get(track, ()) if t_from <= mt <= t_to]
    return sum(values) / len(values) if values else None


def _doa_bearing(t_from: float, t_to: float) -> float | None:
    """Median confident bearing in the window; None if there is none."""
    xs = sorted(b for dt, b, c in _doa
                if t_from <= dt <= t_to and b is not None and c >= DOA_MIN_CONF)
    if not xs:
        return None
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2


def _main_cluster() -> int | None:
    """The cluster a face in front was seen speaking most, once it has
    MAIN_MIN_SEGMENTS of them."""
    counts = Counter(info["cluster"] for info in _segments.values()
                     if info.get("cluster") is not None
                     and info.get("voice_conf", 0.0) >= VOICE_MIN_CONF
                     and info.get("track") is not None)
    if not counts:
        return None
    cluster, n = counts.most_common(1)[0]
    return cluster if n >= MAIN_MIN_SEGMENTS else None


def _last_voice() -> tuple[int, float] | None:
    """(cluster, t1) of the most recent confident voice print."""
    best = None
    for cluster, conf, _t0, t1 in _voice.values():
        if conf < VOICE_MIN_CONF:
            continue
        if best is None or t1 > best[1]:
            best = (cluster, t1)
    return best


def _main_person() -> dict | None:
    """The session's main person: the rule of `_main_cluster` — the one a face
    in front was seen speaking most, once MAIN_MIN_SEGMENTS — counted per
    person rather than per voice cluster.

    Not a second notion of "main": a person *is* their face tracks and voice
    clusters merged (`_merge`), so with voice prints on this is the main
    cluster's person, and with them off (the robot today) it still exists,
    from the face attributions alone. Segments that ended before the last
    release (`_present_main`) do not count: whoever engages after the main
    person left starts from zero, the old one included. Ties go to the
    earlier ordinal — the person who engaged first keeps the robot.
    """
    since = _main["epoch"]
    counts: Counter = Counter()
    people: dict[int, dict] = {}
    for info in _segments.values():
        person = info.get("person")
        if person is None or info.get("track") is None or info.get("t1", since) < since:
            continue
        counts[id(person)] += 1
        people[id(person)] = person
    if not counts:
        return None
    best = max(counts, key=lambda k: (counts[k], -people[k]["ordinal"]))
    return people[best] if counts[best] >= MAIN_MIN_SEGMENTS else None


def _present_main(t: float, engaged: dict[int, FaceObs]) -> tuple[dict | None, int | None]:
    """(the main person if one of their face tracks is engaged, the ordinal
    released at `t` or None). Called under _lock, full mode only.

    Pepper's FullyEngaged ends when the engaged human leaves; here "leaves" is
    none of the main person's tracks engaged within FOCUS_HOLD_S. A new track
    id for the same person (lost for a frame, re-detected) reads as a leave
    too: the robot falls back to semi — anybody in front — until they are
    main again, never to a robot that ignores the person it is talking to.
    """
    person = _main_person()
    if person is None:
        return None, None
    if any(_track_person.get(k) is person for k in engaged):
        return person, None
    _main["epoch"] = t
    return None, person["ordinal"]


def may_open(t: float, rms: float | None = None) -> tuple[bool, str]:
    """(allow, reason) for a speech segment opening at monotonic time `t`.

    `rms` is the trigger chunk's level — not a rule, a number for the log:
    it is what the far-field floor gets tuned from.
    """
    if not enabled():
        return True, OFF                                    # rule 1
    with _lock:
        _voice_at[0] = t if _voice_at[0] is None else max(_voice_at[0], t)
        allow, reason, inputs = _decide(t, rms)
        # Renewed only by a segment the rules themselves let through (a face
        # in front, or the name called) -- never by the waivers: chained on
        # its own opens, one loud word kept the room open for the rest of the
        # call (simulated on 12-13 Oct: 555 room segments through).
        if allow and reason == OK and settings.focus_converse_hold_s > 0:
            _conversing["until"] = t + float(settings.focus_converse_hold_s)
        if allow:
            _reply["since"].add(reason)      # her next turn judges the chain
        released = inputs.pop("released", None)
        _stats["open" if allow else "deny"] += 1
        _last_decision.clear()
        _last_decision.update(allow=allow, reason=reason, t=t)
        new_utterance = not allow and (
            not _deny_burst or _deny_burst.get("reason") != reason
            or (t - _deny_burst.get("last", t)) > DENY_BURST_GAP_S)
        row = _row_for(allow, reason, t)
        if new_utterance and reason == NO_FACE:
            # One entry per denied utterance, with where it came from:
            # `calling_out_of_view` reads these.
            _calls.append((t, _doa_bearing(t - DOA_WINDOW_S, t + 0.1)))
    if released is not None:
        _log("focus_release", ordinal=released)
    if row is not None:
        inputs["rms"] = None if rms is None else round(float(rms), 4)
        _log(row, reason=reason, **inputs)
    return allow, reason


#: A body_status "started" with no later frame stops counting after this
#: long, so a lost "done" cannot hold every door open for the rest of the day.
BODY_MOVING_MAX_S = 30.0


def _robot_moving() -> bool:
    """Walking (the face link's nav state, most cautious face) or a gesture
    under way. Review 12 Oct: rules 2-3 dropped a guest's "หยุด" spoken from
    beside or behind a walking robot -- out of the forward camera -- and the
    model, the only one that can call stop_moving / move_body(stop), never
    heard it. While the robot moves, hearing a stop beats ignoring a
    bystander. Never raises: focus must not fail on the motion bookkeeping."""
    try:
        from app import robot_body, robot_face_link

        if robot_face_link.nav_activity() in ("active", "stale"):
            return True
        state = robot_body.STATE
        at = state.get("at")
        return (state.get("state") == "started" and isinstance(at, (int, float))
                and time.time() - at < BODY_MOVING_MAX_S)
    except Exception:
        return False


#: Until when this session counts as "in conversation" (FOCUS_CONVERSE_HOLD_S
#: after the last segment that opened). Conversation state: `reset` clears it.
_conversing = {"until": float("-inf")}

#: Until when a voice is "the reply" (her voice's end + FOCUS_REPLY_WINDOW_S),
#: how many of her turns in a row answered only window-admitted voices
#: (`chain`), and the reasons the segments since her last turn opened on
#: (`since`). Conversation state: `reset` clears it.
_reply = {"until": float("-inf"), "chain": 0, "since": set()}

#: Her turns in a row that answered only voices the reply window let in (no
#: face, no near voice, no name) before the window stops renewing. Review
#: 13 Oct, replayed on 12 Oct's denials: renewed by every answer, chatter
#: within 10 s of her voice chained up to 5 answers (~80 s), and each
#: admitted transcript reset the idle clock so the call never ended. Three
#: exchanges serve a guest who stays off camera; a face, a voice at the
#: robot or her name starts the count again.
MAX_REPLY_CHAIN = 3


def her_turn_ended(voice_end: float, epoch: int | None = None) -> None:
    """Her turn is complete and her voice plays out at `voice_end`
    (monotonic, the same clock `may_open` is asked on).

    13 Oct, robot logs: on a camera-opened call the guest at the chest
    screen is out of the head camera -- 19 of 19 "probably known" arrivals
    on 12 Oct were 209-577 px faces lost within seconds, and every one of
    the 983 "no face in front" denials had no face at all. The converse
    hold never started there (it is renewed only by a face-verified open),
    so a guest who never shows a face was deaf the whole call. Turn-taking
    is the evidence that needs no camera: speech that starts within the
    window after she stopped is the answer to her, the way people take
    turns. Room chatter only lands if it falls inside that window; the
    loudness floor (VAD_MIN_RMS_FAR) still applies. 0 = off."""
    window = float(getattr(settings, "focus_reply_window_s", 0) or 0)
    if window <= 0:
        return
    with _lock:
        # a superseded call's late turn_complete must not open the new call's
        # window before its greeting (the `segment_closed` epoch rule)
        if epoch is not None and epoch != _epoch[0]:
            return
        since, _reply["since"] = _reply["since"], set()
        if since and since <= {REPLY}:
            _reply["chain"] += 1
        elif since & {OK, NEAR_VOICE, CONVERSING}:
            _reply["chain"] = 0
        if _reply["chain"] >= MAX_REPLY_CHAIN:
            _reply["until"] = float("-inf")
            return
        _reply["until"] = max(_reply["until"], float(voice_end) + window)


def _decide(t: float, rms: float | None = None) -> tuple[bool, str, dict]:
    engaged = _engaged_faces(t)
    camera = _last_frame_t is not None and (t - _last_frame_t) <= CAMERA_STALE_S
    seen = _latest_per_track(t - max(0.0, float(settings.focus_hold_s)), t + 0.5)
    inputs: dict = {
        "faces": len(engaged),
        "px": max((f.width_px for f in (engaged or seen).values()), default=None),
        "yaw": (round(min(abs(f.yaw_deg) for f in engaged.values() if f.yaw_deg is not None), 1)
                if any(f.yaw_deg is not None for f in engaged.values()) else None),
        "camera": camera,
    }

    if _robot_moving():
        return True, ROBOT_MOVING, inputs

    # Rule 2 — a face in front. Only for calls the CAMERA opened (summoned):
    # a call somebody started on purpose (the button, the wake word) is
    # heard even from outside the lens -- owner, 9 Oct 2026, asked that a
    # customer who calls Emma or presses "เริ่มคุย" out of view is not
    # ignored. Rules 3-5 still apply whenever a face IS in view.
    inputs["origin"] = "camera" if _origin["summoned"] else "user"
    # ...unless the name was just called (FOCUS_WAKE, `heard_name`): Look and
    # Talk's "outside the zone, say the name" — for the window only.
    called = _called_at(t)
    if called:
        inputs["called"] = True
    if settings.focus_require_face and _origin["summoned"] and not called:
        if not camera or not engaged:
            # 13 Oct, robot logs of 12-13 Oct: 1,065 "no face in front" to 86
            # opens. Most were room voices (rms 0.02-0.04, the chatter this
            # rule exists for), but a guest standing right at the chest
            # screen is under the head camera or fills its frame (a 540 px
            # face seen for one frame, then lost): mid-conversation they were
            # cut off, and "ขอเชคแฮน" never reached the model. Two waivers,
            # each off unless set: a conversation that just had a segment
            # open stays open, and a voice loud enough to be at the robot is
            # heard. Rules 3-5 still judge whenever a face IS in view.
            if t <= _conversing["until"]:
                inputs["conversing"] = True
                return True, CONVERSING, inputs
            if t <= _reply["until"]:
                inputs["reply"] = True
                return True, REPLY, inputs
            near = float(settings.focus_near_rms or 0.0)
            if near > 0 and rms is not None and rms >= near:
                inputs["near"] = True
                return True, NEAR_VOICE, inputs
            return False, (NO_CAMERA if not camera else NO_FACE), inputs

    # Full engagement (FOCUS_ENGAGE_MODE=full): the person the conversation
    # is with. None = semi, which is also full mode before anybody is main
    # and after they left — every rule below is then today's rule exactly.
    lead = None
    if engage_mode() == "full":
        lead, released = _present_main(t, engaged)
        if released is not None:
            inputs["released"] = released
        if lead is not None:
            inputs["main_person"] = lead["ordinal"]
    lead_tracks = [k for k in engaged if lead is not None and _track_person.get(k) is lead]

    # Rule 3 — a mouth in front moves. Only when mouth data exists for them,
    # and only for mouths the active speaker has proven it can see (`_proven`).
    mouth = _mouth_max(_proven(engaged), t - MOUTH_WINDOW_S, t + 0.1) if engaged else None
    inputs["mouth"] = None if mouth is None else round(mouth, 2)
    if mouth is not None and mouth < MOUTH_MOVING:
        return False, SILENT_FACES, inputs
    # ...and in full mode it must be the main person's mouth: another face
    # in front speaking while theirs is still is not the conversation. Only
    # when their mouth is proven (a masked main person is a missing input).
    lead_proven = _proven(lead_tracks)
    lead_mouth = (_mouth_max(lead_proven, t - MOUTH_WINDOW_S, t + 0.1)
                  if lead_proven else None)
    if (lead_mouth is not None and lead_mouth < MOUTH_MOVING
            and mouth is not None and mouth >= MOUTH_MOVING):
        inputs["main_mouth"] = round(lead_mouth, 2)
        return False, NOT_MAIN, inputs

    # Rule 4 — the voice comes from where a face is. Only with a confident
    # bearing and a face to compare it with.
    doa = _doa_bearing(t - DOA_WINDOW_S, t + 0.1)
    if doa is not None and engaged:
        off = min(abs(doa - f.bearing_deg) for f in engaged.values())
        inputs["doa_off"] = round(off, 1)
        if off > DOA_TOLERANCE_DEG:
            return False, ELSEWHERE, inputs
    # ...in full mode, from where the main person is.
    if doa is not None and lead_tracks:
        off_lead = min(abs(doa - engaged[k].bearing_deg) for k in lead_tracks)
        if off_lead > DOA_TOLERANCE_DEG:
            inputs["doa_off_main"] = round(off_lead, 1)
            return False, NOT_MAIN, inputs

    # Rule 5, full mode: after any voice that is not the main person's, only
    # the main person's own moving mouth reopens — no "second customer
    # joining" while they are still in front.
    if lead is not None:
        last = _last_voice()
        if last is not None:
            inputs["cluster"] = last[0]
        if (lead["clusters"] and last is not None and last[0] not in lead["clusters"]
                and (t - last[1]) <= ANOTHER_VOICE_HOLD_S
                and not (lead_mouth is not None and lead_mouth >= MOUTH_MOVING)):
            return False, ANOTHER_VOICE, inputs
        return True, OK, inputs

    # Rule 5 — after another voice, only a moving mouth in front reopens.
    main = _main_cluster()
    last = _last_voice()
    if main is not None:
        inputs["main"] = main
    if last is not None:
        inputs["cluster"] = last[0]
    if (main is not None and last is not None and last[0] != main
            and (t - last[1]) <= ANOTHER_VOICE_HOLD_S):
        if not (mouth is not None and mouth >= MOUTH_MOVING):
            return False, ANOTHER_VOICE, inputs
    return True, OK, inputs


def _row_for(allow: bool, reason: str, t: float) -> str | None:
    """Which turn-log row this decision gets, if any (called under _lock).

    Every open is one row — it is once per segment. A deny is asked on
    every 20 ms frame of a denied utterance, so it is one row per burst.
    """
    if allow:
        _deny_burst.clear()
        return "focus_open"
    burst = _deny_burst
    fresh = (not burst or burst.get("reason") != reason
             or (t - burst.get("last", t)) > DENY_BURST_GAP_S
             or (t - burst.get("logged", t)) >= DENY_LOG_EVERY_S)
    burst["reason"], burst["last"] = reason, t
    if fresh:
        burst["logged"] = t
        return "focus_deny"
    return None


# ---- attribution --------------------------------------------------------

def _new_person() -> dict:
    _ordinals["last"] += 1
    person = {"ordinal": _ordinals["last"], "tracks": set(), "clusters": set(),
              "name": None, "segs": 0}
    _people.append(person)
    return person


def _merge(keep: dict, drop: dict) -> None:
    """One person seen as two (a new face track, then the voice says it was
    the same voice). The earlier ordinal is kept."""
    if keep is drop:
        return
    keep["tracks"] |= drop["tracks"]
    keep["clusters"] |= drop["clusters"]
    keep["segs"] += drop["segs"]
    keep["name"] = keep["name"] or drop["name"]
    for track in drop["tracks"]:
        _track_person[track] = keep
    for cluster in drop["clusters"]:
        _cluster_person[cluster] = keep
    for info in _segments.values():
        if info.get("person") is drop:
            info["person"] = keep
            info["ordinal"] = keep["ordinal"]
    if drop in _people:
        _people.remove(drop)
    # Its number stays retired, never handed to somebody new: the model may
    # already have been told "คนที่ 2", and a different person arriving as
    # คนที่ 2 later would be the wrong-name bug with a number.


def _link_cluster(info: dict, cluster: int) -> dict | None:
    """Tie this segment's voice to its person (called under _lock)."""
    person = info.get("person")
    owner = _cluster_person.get(cluster)
    if person is None:
        if owner is not None:
            info["person"] = owner
            info["ordinal"] = owner["ordinal"]
            owner["segs"] += 1
        return owner
    if owner is None:
        _cluster_person[cluster] = person
        person["clusters"].add(cluster)
        return person
    if owner is not person:
        # The same voice under a new face track: the person returned to
        # view. Keep the earlier one.
        keep, drop = (owner, person) if owner["ordinal"] < person["ordinal"] else (person, owner)
        _merge(keep, drop)
        return keep
    return person


def segment_closed(segment: int, t0: float, t1: float,
                   epoch: int | None = None) -> SpeakerLabel | None:
    """Who spoke the segment that was forwarded over [t0, t1].

    t0..t1 is the span of what went upstream: the pre-roll before the
    opening through the frame that closed it. The closing hangover
    (VAD_SILENCE_MS of quiet) is trimmed here, so a mouth that moved in the
    silence after the sentence does not vote.

    `epoch` is the conversation the segment belongs to (`epoch()` when its
    session began). A takeover resets focus for the new session while the
    old one is still unwinding, and a segment the OLD gate closes after
    that reset used to be attributed into the NEW conversation — a
    stranger's last words made "คนที่ 1" of the next visitor (review 10 Oct).
    Checked under the same lock `reset` takes, so the two cannot interleave.
    """
    if not enabled():
        return None
    hangover = max(0.0, settings.vad_silence_ms / 1000.0)
    start, end = t0, max(t0 + 0.2, t1 - hangover)
    with _lock:
        if epoch is not None and epoch != _epoch[0]:
            return None
        label, row = _attribute(segment, start, end)
    _log("focus_label", **row)
    return label


#: Bumped by every `reset()`: which conversation focus is attributing for.
_epoch = [0]


def epoch() -> int:
    """The current conversation's number (see `segment_closed`)."""
    with _lock:
        return _epoch[0]


def _attribute(segment: int, start: float, end: float):
    hold = max(0.0, float(settings.focus_hold_s))
    candidates = _engaged_in(start - hold, end + 0.3)
    track, conf, why = None, 0.0, "unknown"

    # Same proof as rule 3: a mouth never seen moving (a mask) is no vote
    # for "off camera" — the lone masked face keeps its weak label.
    scores = {k: _mouth_mean(k, start, end) for k in _proven(candidates)}
    scored = {k: v for k, v in scores.items() if v is not None}
    moving = sorted(((v, k) for k, v in scored.items() if v >= MOUTH_MOVING), reverse=True)
    doa = _doa_bearing(start, end)

    def by_doa(pool):
        if doa is None or not pool:
            return None
        offs = sorted((abs(doa - candidates[k].bearing_deg), k) for k in pool)
        if offs[0][0] > DOA_TOLERANCE_DEG:
            return None
        if len(offs) > 1 and offs[1][0] - offs[0][0] < 10.0:
            return None                              # two faces in the same direction
        return offs[0][1]

    if len(moving) == 1 or (len(moving) > 1 and moving[0][0] - moving[1][0] >= MOUTH_MARGIN):
        track, why = moving[0][1], "mouth"
        runner = moving[1][0] if len(moving) > 1 else 0.0
        conf = min(1.0, 0.6 + (moving[0][0] - runner))
    elif len(moving) > 1:
        pick = by_doa([k for _, k in moving])
        if pick is not None:
            track, why, conf = pick, "mouth+doa", 0.7
        else:
            why = "mouth_tie"
    elif scored:
        # Mouth data for the faces in front and none of them moved: the
        # voice came from somebody the camera does not see as speaking.
        why = "off_camera"
    elif doa is not None and candidates:
        pick = by_doa(list(candidates))
        if pick is not None:
            track, why, conf = pick, "doa", 0.6
        else:
            # A confident direction that matches no face (or two at once):
            # not the lone face's words just because it is the lone face.
            why = "doa_elsewhere"
    elif len(candidates) == 1:
        track, why, conf = next(iter(candidates)), "only_face", 0.5

    # The voice print of this segment, if it is already here (usually it is
    # not — it is computed after the close — and arrives via observe_voice).
    cluster = None
    voice = _voice.get(segment)
    if voice is not None and voice[1] >= VOICE_MIN_CONF:
        cluster = voice[0]

    person = None
    # The name a label may carry: only the one the camera gave THIS face in
    # THIS segment's window. The publisher (facewatch._publish) asks consent
    # on every frame, so that name was allowed a frame ago — a revocation
    # mid-conversation drops it from the next label without any file read
    # here, on the audio path. A label attributed by voice alone carries no
    # name: the face that earned it is not in view to re-ask.
    label_name = None
    if track is not None:
        person = _track_person.get(track)
        if person is None:
            person = _new_person()
            person["tracks"].add(track)
            _track_person[track] = person
        label_name = candidates[track].name
        person["name"] = label_name or person["name"]
    elif cluster is not None and cluster in _cluster_person:
        person = _cluster_person[cluster]
        why, conf = "voice", 0.5

    info = {"t0": start, "t1": end, "track": track, "why": why,
            "person": person, "ordinal": person["ordinal"] if person else None}
    _segments[segment] = info
    while len(_segments) > MAX_SEGMENTS:
        _segments.popitem(last=False)
    if person is not None:
        person["segs"] += 1
        if cluster is not None:
            info["cluster"], info["voice_conf"] = cluster, voice[1]
            person = _link_cluster(info, cluster) or person

    row = {"segment": segment, "ordinal": info["ordinal"], "track": track,
           "why": why, "conf": round(conf, 2), "faces": len(candidates),
           "mouth": (round(max(scored.values()), 2) if scored else None),
           "doa": None if doa is None else round(doa, 1),
           "named": bool(label_name)}
    if person is None:
        return None, row
    return SpeakerLabel(ordinal=person["ordinal"], track=track, name=label_name,
                        confidence=round(conf, 2), why=why), row


def context_note(label: SpeakerLabel | None) -> str | None:
    """The text the model gets for a label, or None when it is not worth
    saying. Square brackets on purpose: the system-instruction line
    (prompts.SPEAKER_LABEL_LINE) tells the model these are system notes it
    must never read aloud."""
    if label is None or label.confidence < LABEL_MIN_CONF:
        return None
    if label.name:
        return "[ผู้พูด: คุณ%s]" % label.name
    return "[ผู้พูด: คนที่ %d]" % label.ordinal


# ---- engagement helpers (the robot face, the invitation) ----------------

def attention(t: float | None = None) -> dict:
    """Where the robot's face should look (FOCUS_ATTENTION): {"bearing": deg
    or None, "source": why}. None = back to centre: nobody engaged and nobody
    speaking.

    Furhat's rule — attention follows whoever speaks — in the order of what
    this arbiter is surest of: a face in front whose mouth moves; else the
    array's confident bearing (Pepper's BasicAwareness, a voice outside the
    lens); else the face the last segment was attributed to; else the nearest
    engaged face (eye contact with somebody waiting). In full engagement mode
    the main person wins outright while they are in front: Pepper's
    FullyEngaged robot does not glance at every other voice.
    """
    t = time.monotonic() if t is None else t
    if not enabled():
        return {"bearing": None, "source": None}
    with _lock:
        out, released = _attend(t)
    if released is not None:
        _log("focus_release", ordinal=released)
    return out


def _attend(t: float) -> tuple[dict, int | None]:
    engaged = _engaged_faces(t)
    released = None
    if engage_mode() == "full":
        lead, released = _present_main(t, engaged)
        if lead is not None:
            mine = [f for k, f in engaged.items() if _track_person.get(k) is lead]
            face = max(mine, key=lambda f: f.width_px)
            return {"bearing": face.bearing_deg, "source": "main"}, released
    moving = []
    for track in _proven(engaged):
        value = _mouth_max([track], t - MOUTH_WINDOW_S, t + 0.1)
        if value is not None and value >= MOUTH_MOVING:
            moving.append((value, track))
    if moving:
        return {"bearing": engaged[max(moving)[1]].bearing_deg, "source": "mouth"}, released
    doa = _doa_bearing(t - DOA_WINDOW_S, t + 0.1)
    if doa is not None:
        return {"bearing": doa, "source": "doa"}, released
    for info in reversed(_segments.values()):
        track = info.get("track")
        if track is not None and track in engaged:
            return {"bearing": engaged[track].bearing_deg, "source": "label"}, released
        break                                   # only the latest segment
    if engaged:
        face = max(engaged.values(), key=lambda f: f.width_px)
        return {"bearing": face.bearing_deg, "source": "face"}, released
    return {"bearing": None, "source": None}, released


def calling_out_of_view(t: float, n: int) -> float | None:
    """Somebody keeps talking to the robot from outside the lens: the last `n`
    utterances denied "no face in front" within CALLING_WINDOW_S all carry a
    confident bearing within CALLING_SPREAD_DEG of their median. Returns that
    bearing and forgets them (one caller, one invitation), else None.

    No bearing, no answer: without the array, "denied again and again" is
    exactly what two people chatting beside the robot look like — the case
    focus exists to ignore. n <= 0 = off.
    """
    if n <= 0 or not enabled():
        return None
    with _lock:
        _prune_left(_calls, t - CALLING_WINDOW_S)
        recent = [b for ct, b in _calls if ct <= t + 0.5][-n:]
        if len(recent) < n or any(b is None for b in recent):
            return None
        ordered = sorted(recent)
        median = ordered[len(ordered) // 2]
        if any(abs(b - median) > CALLING_SPREAD_DEG for b in ordered):
            return None
        _calls.clear()
        return median


# ---- session helpers ----------------------------------------------------

def last_voice_at() -> float | None:
    """Monotonic time of the last voice at the door (opened or denied), or
    None. See `_voice_at`: the session's "is somebody talking" must count
    the voices focus refused, not only the segments it let through."""
    with _lock:
        return _voice_at[0]


def claim_invite(t: float, every_s: float) -> bool:
    """Take the invitation slot at `t` unless one was taken less than
    `every_s` ago (FOCUS_INVITE_EVERY_S) — process-wide, see `_invited`."""
    with _lock:
        last = _invited["t"]
        if last is not None and t - last < max(0.0, float(every_s)):
            return False
        _invited["t"] = t
        return True


def engaged_age(t: float | None = None) -> float | None:
    """Seconds since a face was last engaged, for the idle rule.

    None when it cannot be said: the camera is not delivering frames. A
    dead camera is not evidence that the room is empty, so the ordinary
    idle clock decides then (fail open per input).
    """
    t = time.monotonic() if t is None else t
    with _lock:
        if _last_frame_t is None or (t - _last_frame_t) > CAMERA_STALE_S:
            return None
        if _last_engaged_t is None:
            return float("inf")
        return max(0.0, t - _last_engaged_t)


#: Who opened the current call: the camera (a summoned standby line) or a
#: person (button / wake word). Set by session at connect; summoned until told
#: otherwise, so a caller that never says keeps today's strict rule.
_origin = {"summoned": True}


def begin_session(summoned: bool) -> None:
    """The session that just connected was opened by the camera (summoned)
    or by a person. Only camera-opened calls require a face (rule 2)."""
    with _lock:
        _origin["summoned"] = bool(summoned)


def reset() -> None:
    """A new session, or the end of one: forget the conversation.

    The room stays (faces, mouths, DOA): somebody standing in front of the
    lens when a session opens is still standing there, and forgetting them
    would deny the first words of every summoned call until the next frame.
    """
    with _lock:
        _epoch[0] += 1
        _voice.clear()
        _segments.clear()
        _people.clear()
        _ordinals["last"] = 0
        _session_t[0] = time.monotonic()
        _cluster_person.clear()
        _track_person.clear()
        _deny_burst.clear()
        _stats.clear()
        _last_decision.clear()
        # Engagement is conversation state too: a name called to the last
        # visitor's line opens nothing on the next one, and nobody is the
        # main person of a conversation that has not happened yet.
        _called.update(t=None, until=float("-inf"), bearing=None)
        _main["epoch"] = float("-inf")
        _calls.clear()
        _conversing["until"] = float("-inf")
        _reply["until"] = float("-inf")
        _reply["chain"] = 0
        _reply["since"] = set()


def _forget_everything() -> None:
    """Tests only: the room too."""
    global _last_frame_t, _last_engaged_t
    reset()
    with _lock:
        _frames.clear()
        _mouth.clear()
        _moved.clear()
        _doa.clear()
        _voice_at[0] = None
        _invited["t"] = None
        _last_frame_t = None
        _last_engaged_t = None
        _session_t[0] = float("-inf")
        _origin["summoned"] = True


def snapshot() -> dict:
    """A debug view. Names only as the producers gave them — which is only
    ever a consented name (FaceObs.name / SpeakerLabel.name rules)."""
    now = time.monotonic()
    with _lock:
        engaged = _engaged_faces(now)
        return {
            "enabled": enabled(),
            "require_face": bool(settings.focus_require_face),
            "camera_age_s": None if _last_frame_t is None else round(now - _last_frame_t, 2),
            "engaged": [{"track": f.track, "t": f.t, "bearing": round(f.bearing_deg, 1),
                         "px": f.width_px,
                         "yaw": None if f.yaw_deg is None else round(f.yaw_deg, 1),
                         "name": f.name} for f in engaged.values()],
            # With its time: app/doa.py calibrates against "the one engaged
            # face whose mouth moves" and must be able to tell a fresh value
            # from the last one a departed track left behind.
            "mouth": {track: {"t": dq[-1][0], "activity": round(dq[-1][1], 2)}
                      for track, dq in _mouth.items() if dq},
            "doa": (None if not _doa else
                    {"bearing": None if _doa[-1][1] is None else round(_doa[-1][1], 1),
                     "conf": round(_doa[-1][2], 2), "age_s": round(now - _doa[-1][0], 2)}),
            "people": [{"ordinal": p["ordinal"], "tracks": sorted(p["tracks"]),
                        "clusters": sorted(p["clusters"]), "name": p["name"],
                        "segments": p["segs"]} for p in _people],
            "main_cluster": _main_cluster(),
            "engage_mode": engage_mode(),
            # Read-only: the person who would be main, without judging
            # whether they left (that releases them, a decision's job).
            "main_person": (lambda p: p["ordinal"] if p else None)(_main_person()),
            "called_s": (round(_called["until"] - now, 1)
                         if _called_at(now) else None),
            "last": dict(_last_decision),
            "opens": _stats.get("open", 0),
            "denies": _stats.get("deny", 0),
        }


def _log(event: str, **fields) -> None:
    """Into the turn log, outside the lock. Never raises."""
    try:
        from app import turnlog

        turnlog.record(event, **fields)
    except Exception:                                  # pragma: no cover - belt
        logger.debug("focus: could not write %s", event, exc_info=True)
