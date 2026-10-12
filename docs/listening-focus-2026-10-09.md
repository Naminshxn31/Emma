# Listening focus — talk to the person in front of the robot (spec, 9 Oct 2026)

Owner, 9 Oct 2026, after the robot answered two people chatting nearby: "ให้หุ่นดูด้วยไหมว่ามีคนอยู่หน้ากล้องเท่านั้นถึงจะคุยด้วย … แยกเสียงด้วยได้ไหม … รู้ด้วยว่าคุยกับใครอยู่ … พูดชื่อคนที่คุยกับหุ่นด้วย", then "ทำทั้งหมดเลย".

Why it happens today: the camera greeter opens a call when it sees a face, and from then on every voice in the room reaches Gemini. The robot's profile `joke` is in `LISTEN_FAR_PROFILES`, so its call has no loudness floor (`vad_min_rms_far=0`, log line "no loudness floor").

## Architecture

One arbiter, `app/focus.py`, decides two things:

1. **May a speech segment open now?** `VadGate.feed` asks once, at the moment it would open a segment, exactly where the loudness floor sits today. "No" is treated like the floor: the audio stays in the pre-roll, nothing goes upstream.
2. **Who spoke this segment?** After the segment closes, the session asks for a label. With `FOCUS_SPEAKER_LABELS` on, the label reaches the model as a context note that does not start a turn.

Four inputs feed it. Each is optional and off by default; focus works with whatever is on.

| Input | Producer (owner of the code) | Call into focus |
|---|---|---|
| Faces from the robot camera: bbox, bearing, size, yaw, consented name | `app/facewatch.py` / `app/greeter.py` (backbone agent) | `observe_faces` |
| Mouth activity per face (active speaker) | `app/active_speaker.py` (vision agent) | `observe_mouth` |
| Voice print per segment, clustered per session | `app/voiceprint.py` (voice agent) | `observe_voice` |
| Direction of arrival from the 8-channel mic array | Android app + `app/doa.py` (array agent) | `observe_doa` |

## The API (`app/focus.py`) — exact names; every producer codes against this

```python
from dataclasses import dataclass

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

def enabled() -> bool                                   # settings.focus_enabled
def observe_faces(faces: list[FaceObs], t: float) -> None   # every processed frame, [] = nobody
def observe_mouth(track: int, t: float, activity: float) -> None   # 0..1, ~ASD_FPS per track
def observe_doa(t: float, bearing_deg: float | None, confidence: float) -> None   # None = no single source
def observe_voice(segment: int, t0: float, t1: float, cluster: int, confidence: float) -> None
def may_open(t: float) -> tuple[bool, str]             # (allow, reason) — VadGate calls this
def segment_closed(segment: int, t0: float, t1: float) -> SpeakerLabel | None   # session calls this
def reset() -> None                                     # new session / session end
def snapshot() -> dict                                  # debug view (no names unless consented)
```

Rules that bind every producer:

- **Times are `time.monotonic()`** on the server. A producer that measures elsewhere (the app) converts at arrival and says so.
- **Bearing convention: + = the robot's left**, degrees from the camera axis. The DOA side maps mic geometry to the same frame (calibrated against face bearings, see below).
- **Names only through consent** (`app/consent.py`): a name the gallery gives but consent does not allow is `None`. Voice prints are never stored beyond the session and never matched to a name except through a face the camera saw speaking in the same session.
- **Never block**: every `observe_*` returns in under a millisecond (append + prune); heavy work stays in the producer's thread.
- **Fail open per input, closed per switch**: a producer that crashes or goes silent simply stops contributing; `may_open` then decides from what remains. With `FOCUS_REQUIRE_FACE` on and the camera dead, the robot does not listen; the reason says "no camera" so the page/log can show it.

## Decision rules (backbone agent implements, all tunable, logged)

`may_open(t)` allows a segment when:

1. Focus is off → allow (today's behaviour, byte-identical).
2. `FOCUS_REQUIRE_FACE`: at least one face *engaged* within `FOCUS_HOLD_S`: `width_px ≥ FOCUS_FACE_MIN_PX`, `|yaw| ≤ FOCUS_YAW_MAX_DEG`. Otherwise deny "no face in front". (Review 9 Oct: any engaged observation inside the hold counts, not only the newest frame — one glance at a companion must not deny; and the width bar is capped at the greeter's `FACE_MIN_PX` — a face the robot greets is a face it listens to.)
3. If mouth activity is available for the engaged faces in the last ~0.6 s and **none** moves → deny "faces in front are silent" (someone off-camera spoke). If none is available → do not use this rule. (Review 9 Oct: only for faces whose mouth the active speaker has seen move at least once — a face mask gives valid landmarks and lips that never move, measured activity 0.00, and would otherwise be deaf for the whole visit.)
4. If DOA is available with confidence and points more than ~25° away from every engaged face → deny "voice from elsewhere". If unavailable → skip.
5. If voice clusters are available and this session already has a confirmed main speaker, a segment from another cluster is allowed only if its face is engaged and its mouth moves (a second customer joining) — otherwise deny "another voice". (Voice clusters arrive after a segment; this rule applies to the *next* opening.)

`segment_closed` attributes the segment: mouth activity during [t0, t1] picks the face; DOA breaks ties; the voice cluster carries the ordinal across moments when the face is not visible. Ordinals are per session.

## Speaker labels to the model

With `FOCUS_SPEAKER_LABELS`, after each attributed segment the session sends a non-turn context note (Gemini Live `send_client_content(turn_complete=False)` or the provider's existing equivalent) like `[ผู้พูด: คนที่ 2]` or `[ผู้พูด: คุณแก้ว]`. The system instruction gets one line explaining the note, attached only when the switch is on. Measure with `scripts/eval_live_qa.py` before turning it on for customers: the model must use the name naturally and never read the bracket aloud.

Review 9 Oct: `turn_complete=False` is documented by google-genai as "the model will wait … and will not return until you send turn_complete=True" — if the server honours that, every labelled turn gets no reply. Unmeasured, so bounded: `gemini._note_stall_guard` completes the turn after `NOTE_STALL_S` (6 s) with no reply-side message, latches notes off for the process and writes `focus_note_stalled` (analyze_log shouts it). Note that `eval_live_qa.py` sends text turns and never a labelled audio segment, so it cannot measure this path as it stands.

## Tuning from real logs

Every decision is a `turnlog` row: `focus_open` / `focus_deny` with the reason and the inputs (face count, best width/yaw, mouth max, doa offset, cluster). `scripts/analyze_log.py` gains a section. Thresholds come from the robot's own logs, not from theory (CLAUDE.md: "จูนจากบรรทัด log เท่านั้น").

## Engagement — behave like an established reception robot (10 Oct 2026)

Owner, 9 Oct: "ลองปรับให้เหมือนหุ่นต้อนรับอื่นให้ดีที่สุด". Five pieces, each behind its own switch, all OFF by default and all requiring `FOCUS_ENABLED`; off, every row and every decision above is unchanged (tests: `tests/test_engagement.py`).

| Reference behaviour | Here | Switch |
|---|---|---|
| Google Look and Talk: in the zone (close, facing), just talk; outside it, say the wake word | rule 2 already is the zone; the name heard **inside a camera-opened call** opens a window (`focus.heard_name`) that waives rule 2 for `FOCUS_WAKE_WINDOW_S`; rules 3-5 still judge every face in view | `FOCUS_WAKE`, `FOCUS_WAKE_THRESHOLD`, `FOCUS_WAKE_WINDOW_S` |
| Pepper BasicAwareness: turn toward a sound to bring the person into view | the robot cannot turn during a conversation, so it says it: one short invitation to step in front (+ the side from DOA), through `events.announce` | `FOCUS_INVITE`, `FOCUS_INVITE_EVERY_S`, `FOCUS_INVITE_DENIED_N` |
| Pepper engagement modes SemiEngaged / FullyEngaged | `semi` = today; `full` = once the session has a main person, other faces/voices are denied `not the main speaker` / `another voice` until that person leaves, then anyone may engage | `FOCUS_ENGAGE_MODE` |
| Furhat: attention follows whoever speaks | `focus.attention()` → `{"type":"attention","bearing"}` on the call socket → `EmmaMascot.attend()` turns the head (≤ 0.12 rad), back to centre on null | `FOCUS_ATTENTION` |
| Reception norm: the guest walks off mid-conversation | one goodbye sentence through `events.announce`, then the call ends (the standing line rings a fresh one) | `FOCUS_GOODBYE`, `FOCUS_GOODBYE_AFTER_S` |

Rules that bind these, each paid for elsewhere in this repo:

- **The in-call ear** (`app/call_wake.py`) is the standby ear's model, spellings and per-spelling scale with its own bar; it runs only for a summoned call with `FOCUS_REQUIRE_FACE` and a local VAD gate (nowhere else can the name change anything), is fed each frame *before* the gate sees it ("Emma, …" in one breath keeps its head), ignores hits while her own voice may still be playing (`_she_is_audible`: the page's audio lead, or the server's own clock of reply bytes played back to back — the robot face page, voice-preview.html, reports no lead), and writes `focus_wake_cpu` (ms per second of audio) at every call's end. Measured on this PC, 677 s of real call audio, 20 ms frames: 15.4 ms CPU per second with one thread (1.5% of a core); the library default of two threads cost 59 ms for the same wall time, so it uses one. Fires on that audio: 4 at bars 0.10-0.15, 3 at 0.20 (unlabelled — some may be real calls of the name); 0.15 halves the owner's real name clips caught, hence 0.10. Not measured on the robot's ARM board: read `focus_wake_cpu`.
- **Window**: `heard_name` waives rule 2 including "no camera" (the name is as deliberate as the button); it is conversation state (cleared by `reset`) and its rows carry `called=true`.
- **Invitation**: only when the camera is alive and nobody is engaged; waits until nobody is talking (an open segment OR a voice focus just denied — `focus.last_voice_at`), no reply is owed, and her own answer has played out by the server's clock (`_quiet_moment`), then `announce` (relevance re-checked: still nobody in front, not stale, nobody talking, her voice not still playing, and for the name: somebody was heard after it — a false fire of the ear invites nobody). At most one per `FOCUS_INVITE_EVERY_S` per ROBOT (`focus.claim_invite`; per line, the standing line's re-ring reset it). The denial trigger needs N denied utterances from one DOA direction, and only on a line that already holds a conversation (greeted or heard): on a virgin standing line it is the chat beside the robot; without a bearing it never fires (calling and chatting look the same).
- **Review 10 Oct — why the server clock**: the robot face page never sends `audio_lead`, so `announce`'s audio wait is a no-op there. Before the fix the invitation went in while her answer to the caller was still being generated (client content mid-reply interrupts it) and the goodbye's `idle_timeout` closed the page's AudioContext at turn_complete — before a word of it played.
- **Full mode**: the main person is `_main_person` — `_main_cluster`'s rule (most segments a face in front was seen speaking, ≥ `MAIN_MIN_SEGMENTS`) counted per person, so it exists without voice prints. "Left" = none of their tracks engaged within `FOCUS_HOLD_S`; then segments before that moment stop counting (`focus_release` row). Without mouth/DOA/voice data it cannot tell people apart and is semi (fail open).
- **Attention** goes to the robot face page only (`robot_face_link.is_robot_face`), at most 4 per second and only when the bearing moved ≥ 3°; the mascot eases toward it only in the `idle`-clip states (the `look` clip already turns the same bone). The sign is checked against the GLB (face on +z, identity bones). Judge the size by eye on the robot.
- **Goodbye**: greeted, heard, a face engaged during this call and none for `FOCUS_GOODBYE_AFTER_S` (never under the hold), nobody heard since and no voice at the door since (a denied one included: a guest asking with their face in a brochure, a child under the lens), and heard within `FOCUS_AWAY_S` before they left. The call ends only once the goodbye has played out (`_until_she_is_heard`), not when it was generated. If they come back while she says it, the call goes on. Off by default because a guest reading a brochure loses their face too: tune `FOCUS_GOODBYE_AFTER_S` above the `focus_gap` rows (written whenever focus is on: nobody engaged for longer than the hold, then somebody again).
