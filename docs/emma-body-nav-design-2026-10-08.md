# Emma สั่งร่างกายหุ่นด้วยเสียง + ฟังก์ชันนำทาง — แบบออกแบบสำหรับลงมือทำ (8 ต.ค. 2026)

> Status: design only. Nothing in this document has run on the robot; the robot is powered off.
> Every "verified" below cites the evidence it rests on. Everything else is a plan.
> Sources: `CLAUDE.md`, the six subsystem maps and the completeness critique made on 8 Oct, and the
> files named in each section (read on 8 Oct, working tree on `dev`).
> **Revision 2 (8 Oct, after a safety review and a build review).** Every point was checked against the source
> and folded in where it held. §8 lists what changed and the points that were rejected, with the reasons.

## 0. สรุปสั้น (Summary)

The owner asked for two things: voice control of the robot's body (head, arms, hands), and navigation as a
tap-to-go function on the robot's face, built like the existing face-scan function. He also asked for the voice
model to be tuned. This design does all three on top of what already works, with one path for each job.

- **Body:** a fixed vocabulary of named actions (`nod`, `head_down`, `raise_left_arm` …). The model picks
  one name. The server sends only that name to the robot's face page. The page passes it to the Android app
  through the existing `window.prompt` bridge. The app's own gesture table turns the name into servo moves
  through the Aobo SDK, which is the only path that has moved the arm (group 6, 30 Sep). Names that nobody
  has mapped on the bench are refused, never guessed.
- **Navigation:** the face page's hold menu (the six slots around Emma; slot 1 is the face scan) gets
  slot 2, **นำทาง**. It offers place cards, a confirm sheet, a progress view and a native red STOP bar. Tapping
  a place runs a new on-robot launcher (`scripts/emma_nav.py`) through `su`. The launcher runs one *leg* of the
  one verified route (`floor2_bigroom_roundtrip.json`) using the new `drive_route.py --to-step`. On the robot face
  session, voice `go_to_place` and `return_to_base` use the same launcher, so there is one navigation system.
- **Voice tuning:** changes only where there is evidence for them. Captions biased toward body and place words,
  which also feed a heard-word guard (the `heard.py` precedent). Thai tool descriptions that quote real
  phrasings. Prompt blocks that stop contradicting the tools. A fix for gemini-3.8 sending `turn_complete`
  before its tool call (13/15 in the 8 Oct eval). Robot-command scenarios in the live eval. A correction to
  the stale model section of CLAUDE.md.

Everything is **off by default**, and both the server and the robot have to switch it on (two keys). A
`git pull` on the sales-room machine changes nothing (§2h), and that includes runtime timing: the turn-idle
change is behind its own switch, which is off by default. Voice navigation has a third key (`ROBOT_NAV_VOICE`,
off), because a customer can talk to the robot face. A stop can never be refused as "busy" at any layer.

---

## 1. สถาปัตยกรรม (Architecture)

```
 ┌──────────────── PC: FastAPI server (app/) ─────────────────────────────────────────────────┐
 │  Gemini Live (3.8) ──tool call──► registry.dispatch                                         │
 │                                     │                                                       │
 │        move_body(action)  ──────────┤  app/tools/body.py ─► app/robot_body.py               │
 │        go_to_place(place) ──────────┤  app/tools/robot.py ─► app/robot_nav.py  (face+routes)│
 │        return_to_base / stop_moving │                     └► robot_link/robot_chassis (old) │
 │                                     ▼                                                       │
 │                     app/robot_face_link.py  request(session, kind, payload) ──► await ack   │
 │                          ▲   │  {type:"body_command"|"nav_command", command_id, …}          │
 │  events.announce ◄───────┘   │  (only ever to registry.robot_face_session — the face's /ws) │
 │  (nav arrival/stop/refusal,  │                                                              │
 │   voice-origin runs only)    │                                                              │
 └──────────────────────────────┼─────────────────────────────────────────────────────────────┘
                                │ the existing /ws voice socket (WS_TOKEN), robot face only
 ┌──────────────────────────────▼──── Robot: Android 15, EmmaRobot app ───────────────────────┐
 │  WebView: /preview?app=1&cam=1  (client/voice-preview.html)                                 │
 │    • relays body_command / nav_command → window.prompt("emma-body:nod")                     │
 │    • hold menu slot 2 "นำทาง" → overlay → window.prompt("emma-nav:go:big_room:tap")         │
 │    • sends body_ack / nav_ack (from the prompt's return string), body_status / nav_status   │
 │      (pulled with "emma-*:status" after the native 'emma-body-changed'/'emma-nav-changed')  │
 │                │ onJsPrompt (EmmaFace: trusted origin + /preview + app=1, fixed grammar)     │
 │                ▼                                                                            │
 │  EmmaFace.Bridge (interface)  ─────►  MainActivity                                          │
 │                                        ├─ BodyController ─► ArmController ─► Aobo SDK ──► CH340 servo board
 │                                        │    (BodyGestures table + BodyPoseStore, clamps,     (groups 3/6/99,
 │                                        │     one-at-a-time, cooldown, rest-return)            raw '#7P..T..')
 │                                        ├─ NavLauncher ─ su 0 sh -c "… emma_nav.py go … --tether"
 │                                        │     stdin: "ping"/"stop"   stdout: status JSON lines
 │                                        └─ native SafetyBar "■ หยุดหุ่น" (above the WebView)
 │                                                                                             │
 │  /data/local/tmp/emma-nav/  (installed + deployed from the PC by scripts/robot_python.py)   │
 │    code/scripts/emma_nav.py  ──spawns──►  code/scripts/drive_route.py --from-step/--to-step  │
 │       │  (leg lookup, where-am-I,          │ --status-file nav/drive_status.json            │
 │       │   tether, status, here.json)       │ --watch-parent --arms-in                       │
 │       ▼                                    ▼                                                │
 │    nav/status.json  nav/here.json  run.pid    board REST 192.168.11.1:1448 + SDK :1445      │
 └─────────────────────────────────────────────────────────────────────────────────────────────┘

 Return paths
   body : native accepts/refuses synchronously in the prompt  → page → {body_ack}  → tool result (≤2.5 s)
          native 'emma-body-changed' → page pulls status       → {body_status} → turnlog + BODY_STATE (no speech)
   nav  : native accepts/refuses synchronously (cached gates)  → page → {nav_ack}   → tool result (≤3 s)
          launcher status lines → native → 'emma-nav-changed' → page pulls → {nav_status} (each change + every 5 s)
          terminal state (arrived/stopped/refused/failed) of a voice-origin run → events.announce (one turn)
   stop : native SafetyBar / gamepad B / onPause / focus loss / operator screen open — no page, no server needed
          (stop work runs on NavLauncher's own threads, never on MainActivity's single `io` executor)
          app death → su pipe EOF → launcher SIGINTs drive_route; launcher death → drive_route --watch-parent
          app killed with its whole cgroup (SIGKILL, no cancel) → next app start runs `emma_nav.py reap`
```

Why each piece sits where it does:

- **The server never holds a pulse, channel, group number or step index.** The same rule as
  `show_unit(room)` and not `show_unit(room, price)`: the model chooses *which* named thing, and the machine
  that owns the hardware owns the numbers. The Android app is the source of truth for poses, and the launcher
  on the robot is the source of truth for legs.
- **The robot-side run logic is Python (`emma_nav.py`), not Java.** It is testable offline with pytest, reuses
  `robot_python`'s runtime, and keeps the Java to process plumbing. (D6)
- **No new port.** Body and nav messages ride the face page's existing `/ws`, the same reasoning as in
  `robot_link.py`'s docstring. The tap path needs no server at all: the bridge goes straight to native.
  **(9 Oct, after review 2)** One more socket on the same port: the status link `/ws/robot-face` (§10), because
  the call's `/ws` exists only during a call and a walk started between calls was invisible to the server.

---

## 2. สัญญา (Exact contracts)

### 2.0 Shared constants — `app/robot_contracts.py` (new, P0, stdlib only)

There is one Python source for ids and codes. The Java and HTML copies are checked against it by tests
(P4/P5), because two copies of a rule drift.

```python
BODY_ACTIONS = (            # id, Thai label (UI + instruction), kind
  ("head_up","เงยหน้า","head"), ("head_down","ก้มหน้า","head"), ("head_left","หันหน้าไปทางซ้าย","head"),
  ("head_right","หันหน้าไปทางขวา","head"), ("head_center","มองตรง","head"), ("nod","พยักหน้า","head"),
  ("shake_head","ส่ายหน้า","head"), ("raise_left_arm","ยกแขนซ้าย","arm"), ("raise_right_arm","ยกแขนขวา","arm"),
  ("greet_arm","ยกแขนทักทาย","arm"), ("wave","โบกมือ","arm"), ("handshake","ยื่นมือจับมือ","arm"),
  ("hands_left","ขยับมือไปทางซ้าย","arm"), ("hands_right","ขยับมือไปทางขวา","arm"),
  ("rest","เอาแขนลง/ท่าพัก","arm"), ("stop","หยุดท่าทาง","stop"),
)
# Heard guard (§2d): per action, the caption must contain one VERB token, and for a sided action also the
# SIDE token. Bare nouns (หน้า ท่า มือ ซ้าย ขวา ลด) never count on their own: "ลดราคา", "ข้างหน้า" and "ท่าน"
# must not authorise motion. Matched on retrieval.tokenize() words, never on substrings.
BODY_HEARD = {
  "head_up": (("เงย", "look up", "head up"), None),        "head_down": (("ก้ม", "look down", "head down"), None),
  "head_left": (("หัน", "มอง", "turn", "look"), ("ซ้าย", "left")),
  "head_right": (("หัน", "มอง", "turn", "look"), ("ขวา", "right")),
  "head_center": (("มองตรง", "หันตรง", "ตรง", "straight", "center", "centre"), None),
  "nod": (("พยัก", "nod"), None),                           "shake_head": (("ส่าย", "shake"), None),
  "raise_left_arm": (("ยก", "raise", "lift"), ("ซ้าย", "left")),
  "raise_right_arm": (("ยก", "raise", "lift"), ("ขวา", "right")),
  "greet_arm": (("ทักทาย", "โบก", "greet", "wave"), None),   "wave": (("โบก", "wave"), None),
  "handshake": (("จับมือ", "handshake", "shake hands"), None),
  "hands_left": (("ขยับ", "เลื่อน", "move"), ("ซ้าย", "left")),
  "hands_right": (("ขยับ", "เลื่อน", "move"), ("ขวา", "right")),
  "rest": (("ลง", "พัก", "เก็บ", "lower", "rest"), None),
}   # "stop" is never guarded
BODY_STATUSES = ("verified", "direction_known", "operator_mapped", "needs_mapping")
ID_RE = r"^[a-z][a-z0-9_]{1,31}$"          # every id that crosses a boundary (body action, place id)
COMMAND_ID_RE = r"^[0-9a-f]{32}$"
MAX_INBOUND_BYTES = 8192                    # any page→server body/nav message larger is dropped
BODY_REASONS = {   # code -> Thai (UI text and tool instruction stem)
  "disabled": "ปิดการสั่งท่าทางไว้บนหุ่น", "server_disabled": "ปิดการสั่งท่าทางไว้ที่เซิร์ฟเวอร์",
  "needs_mapping": "ท่านี้ยังไม่ได้ตั้งค่าบนตัวหุ่น", "direction_unconfirmed": "ท่านี้ยังไม่ได้เปิดให้ใช้ (ยังไม่ทดสอบทิศ)",
  "busy": "แขนกำลังทำท่าอื่นอยู่", "cooldown": "เพิ่งทำท่าไป รอสักครู่", "rate_limited": "สั่งท่าถี่เกินไป",
  "navigating": "หุ่นกำลังเดินอยู่ ขยับแขนไม่ได้", "latched": "เพิ่งกดหยุดแขน ล็อกไว้ชั่วคราว",
  "operator_screen": "เจ้าหน้าที่เปิดหน้าควบคุมอยู่", "not_foreground": "หน้าจอหุ่นไม่ได้อยู่ด้านหน้า",
  "arm_unavailable": "ต่อบอร์ดแขนไม่ได้", "not_robot_face": "ไม่ได้คุยผ่านหน้าหุ่น",
  "heard_missing": "ไม่ได้ยินคำสั่งท่าทางชัดเจน", "timeout": "หุ่นไม่ตอบรับคำสั่ง", "bad_request": "คำสั่งไม่ถูกต้อง",
  "nav_unknown": "ตรวจไม่ได้ว่าหุ่นกำลังเดินอยู่หรือไม่ จึงยังไม่ขยับแขน",
  # link level (robot_face_link), distinct from "busy" (= the ARM is busy, decided on the robot)
  "request_pending": "คำสั่งก่อนหน้ายังรอหุ่นตอบอยู่", "disconnected": "หน้าจอหุ่นหลุดการเชื่อมต่อ",
}
NAV_REASONS = {
  "disabled": "ฟังก์ชันนำทางปิดอยู่บนหุ่น", "not_installed": "ยังไม่ได้ติดตั้งระบบนำทางบนหุ่น (ติดตั้งจากคอมพิวเตอร์ก่อน)",
  "places_stale": "ข้อมูลจุดหมายไม่ตรงกับเส้นทางที่ติดตั้ง — ติดตั้งใหม่จากคอมพิวเตอร์",
  "unknown_place": "ไม่รู้จักจุดหมายนี้", "already_there": "อยู่ที่นี่แล้ว", "no_leg": "ยังไม่มีเส้นทางจากจุดนี้ไปที่นั่น",
  "location_unknown": "ไม่รู้ว่าหุ่นอยู่ตรงไหน — ให้เจ้าหน้าที่พาหุ่นกลับแท่นชาร์จก่อน",
  "low_battery": "แบตเตอรี่ไม่พอ", "run_active": "หุ่นกำลังเดินอยู่", "leg_unverified": "เส้นทางนี้ยังไม่เคยทดสอบแยก — ต้องให้เจ้าหน้าที่กดสั่งเอง",
  "body_busy": "แขนกำลังทำท่าอยู่ รอสักครู่", "body_latched": "แขนถูกล็อกหลังกดหยุด",
  "arms_not_rest": "เก็บแขนไม่สำเร็จ — ยังเดินไม่ได้", "board_unreachable": "ติดต่อฐานล้อไม่ได้",
  "operator_screen": "เจ้าหน้าที่เปิดหน้าควบคุมอยู่", "not_foreground": "หน้าจอหุ่นไม่ได้อยู่ด้านหน้า",
  "launcher_failed": "เริ่มระบบนำทางไม่สำเร็จ", "heard_missing": "ไม่ได้ยินชื่อจุดหมายชัดเจน",
  "not_robot_face": "ไม่ได้คุยผ่านหน้าหุ่น", "timeout": "หุ่นไม่ตอบรับคำสั่ง", "bad_request": "คำสั่งไม่ถูกต้อง",
  "request_pending": "คำสั่งก่อนหน้ายังรอหุ่นตอบอยู่", "disconnected": "หน้าจอหุ่นหลุดการเชื่อมต่อ",
  "nav_latched": "เพิ่งกดหยุดหุ่น ล็อกการเดินไว้ชั่วคราว", "voice_disabled": "ปิดการสั่งเดินด้วยเสียงไว้ — ให้เจ้าหน้าที่กดสั่งที่หน้าจอ",
  "relocalize": "ระบบระบุตำแหน่งของฐานล้อปิดอยู่หรืออ่านไม่ได้ — ให้เจ้าหน้าที่เปิด/ตั้งตำแหน่งใหม่ก่อน",
  # terminal reasons of a run (launcher/drive_route)
  "stop_pressed": "กดหยุดแล้ว", "door_blocked": "ประตูปิดหรือมีคนยืนขวางประตู", "obstacle": "มีสิ่งกีดขวางทาง",
  "link_lost": "ขาดการติดต่อกับฐานล้อ", "dock_blocked": "เข้าแท่นชาร์จไม่สำเร็จ — หุ่นจอดอยู่ข้างแท่น",
  "app_lost": "แอปหน้าจอหุ่นหยุดทำงาน หุ่นจึงหยุด", "launcher_lost": "ตัวควบคุมการเดินหยุดทำงาน หุ่นจึงหยุด",
  "refused": "ระบบนำทางไม่ยอมเริ่ม (ตรวจก่อนออกเดินไม่ผ่าน)", "stopped": "หุ่นหยุดกลางทาง", "error": "ระบบนำทางหยุดเพราะข้อผิดพลาด",
  "interrupted": "การเดินถูกขัดจังหวะ หุ่นหยุดแล้ว", "pc_run": "มีการสั่งเดินจากคอมพิวเตอร์อยู่",
}
NAV_STATES = ("idle", "preparing", "starting", "running", "arrived", "stopped", "refused", "failed")
NAV_TERMINAL = ("arrived", "stopped", "refused", "failed")
NAV_ACTIVITY = ("idle", "active", "stale", "unknown")   # robot_face_link.nav_activity(), §2a
RUN_ID_RE = COMMAND_ID_RE                               # run ids are 32 hex, generated by the app (§2b)
INBOUND_TYPES = ("body_ack", "body_status", "body_caps", "nav_ack", "nav_status", "nav_caps")
```

### 2a. WebSocket messages (face page ⇄ server, on the existing `/ws`; since 9 Oct also on the status link, §10)

All of these are JSON text frames. The server sends commands **only** to the session in
`registry.robot_face_session` (UA contains `EmmaRobot/`). The server ignores inbound body/nav messages from any
other session, drops any frame over `MAX_INBOUND_BYTES`, and drops ids that fail `ID_RE` / `COMMAND_ID_RE`.

**Server → page**

| type | fields | notes |
|---|---|---|
| `body_command` | `command_id` (32 hex), `action` (∈ BODY_ACTIONS ids) | `stop` is also an action |
| `nav_command` | `command_id`, `action` ∈ `go`/`stop`, `place` (id, only with `go`), `origin`=`"voice"` | the `run_id` is **not** chosen by the server; the app generates it and returns it in `nav_ack` |

**Page → server**

| type | fields | when |
|---|---|---|
| `body_ack` | `command_id`, `action`, `accepted` (bool), `reason` (code\|null), `status` (BODY_STATUSES\|null), `est_ms` (int\|null) | immediately after the prompt returns. A desktop (non-app) page answers `accepted:false, reason:"not_robot_face"` |
| `body_status` | `seq` (int), `action`, `state` ∈ `started`/`done`/`stopped`/`failed`, `reason` (code\|null) | on every native `emma-body-changed` whose `seq` is new |
| `body_caps` | `enabled` (bool), `allow_direction_known` (bool), `gestures` {id: status} | on `ready` and whenever native reports a change |
| `nav_ack` | `command_id`, `action`, `accepted`, `reason`, `run_id` (str\|null) | immediately after the prompt returns |
| `nav_status` | the §2e status object | on every state/step change, and every 5 s while `state ∈ {preparing,starting,running}` |
| `nav_caps` | `enabled`, `installed`, `places_sha256`, `here` (id\|null), `battery` (int\|null), `places` [{`id`,`name_th`,`available`,`reason`,`verified`}] | on `ready`, after a run ends, and at most every 30 s while idle |

`app/robot_face_link.py` (P0) is the only thing that touches these frames on the server. Its API:

```python
async def request(session, kind: str, payload: dict, timeout: float) -> dict
    # kind ∈ {"body","nav"}; sends {"type": f"{kind}_command", "command_id": hex32, **payload}
    # via session.ws.send_text; returns the ack dict, or {"accepted": False, "reason": "timeout"} /
    # {"accepted": False, "reason": "not_robot_face"} / {"accepted": False, "reason": "disconnected"}.
    # One outstanding NON-STOP request per (session, kind); a second returns
    # {"accepted": False, "reason": "request_pending"} (renamed from "busy": "busy" is the ARM's code).
    # payload["action"] == "stop" is NEVER refused here: it is sent at once with its own command_id,
    # whatever is outstanding, and the pending request keeps waiting for its own ack.
def receive(session, event: dict) -> None       # called by session.py for INBOUND_TYPES; acks resolve
                                                # futures by (session, kind, command_id); others go to handlers
def on(message_type: str, handler) -> None      # P1 registers body_status/body_caps; P2 nav_status/nav_caps
def nav_activity(session=None) -> str           # NAV_ACTIVITY: from the latest nav_status/nav_caps this
    # module routed for the face session. "active" = a non-terminal state, or nav_caps.run non-terminal
    # (that includes a PC-started run, §2e `pc_run`); "stale" = active and no frame for
    # ROBOT_NAV_STATUS_STALE_S; "idle" = terminal/idle reported; "unknown" = no face frame ever seen.
    # Shared by P1 (body + serial arm refusal) and P2 (wheel fence), so neither owns the other's state.
def issued_run(session, run_id: str) -> bool    # True only for a run_id that came back in a nav_ack to a
                                                # nav_command THIS session's server sent (announce gate)
def session_closed(session) -> None             # resolve pending acks with "disconnected"; notify handlers
def reset_state() -> None                       # conftest
```

**All state is per session.** Caps, the latest nav status, issued run ids and pending acks are keyed by the
session object. A terminal `nav_status` is announced only when `issued_run(session, run_id)` is true. A second
page that merely claims the `EmmaRobot/` user agent can therefore neither fake an arrival into another session
nor answer another session's commands. It can still report itself "active" and so hold the wheel fence (§4).
That is a denial of service, not motion, and the fence fails safe.

**Optional face token (`ROBOT_FACE_TOKEN`, default empty).** When it is set, the face session counts as the
robot face only after a `{"type":"face_hello","token":…}` frame matches it (`secrets.compare_digest`). The
page gets the token from native (`emma-face:token`, trust-gated, §2b), never from the URL, so a desk browser
holding WS_TOKEN cannot become the robot face. This is the `ROBOT_TOKEN`/`robot_ready` fix from the 1 Sep
review, applied to the face. When it is empty, the user agent decides, as today, and the boot log says so.

### 2b. JS bridge (page → `EmmaFace.onJsPrompt` → MainActivity)

The grammar is closed. No message carries free text, pulse, channel, group or step. `EmmaFace` parses and
validates the *shape* of the message. `MainActivity` validates it against its tables (gesture ids, last
places list). `EmmaFace` keeps no reference to `ArmController`, `BodyController`, `NavLauncher`, `su`, `1445`
or `192.168.11.1`; the existing test that forbids this is extended.

| prompt string | returns (JSON string via `result.confirm`) | extra native gate |
|---|---|---|
| `emma-arm:greet:6` (legacy, unchanged) | `""` | foreground + focus + !operator (as today), now through `BodyController` (one lock) |
| `emma-body:list` | `{"enabled":b,"allow_direction_known":b,"gestures":{"nod":"direction_known",…}}` | none |
| `emma-body:status` | `{"seq":n,"state":"idle\|running\|rest_return\|cooldown\|latched","active":id\|null,"last":{"action":id,"state":…,"reason":…}\|null}` | none |
| `emma-body:stop` | `{"accepted":true}` (always) | **none, on purpose**: a stop must never be blocked by focus or state |
| `emma-body:<id>` (id ∈ table, ≠ list/status/stop) | `{"accepted":b,"action":id,"reason":code\|null,"status":…,"est_ms":n\|null}` | foreground + window focus + !operatorVisible, then §2c checks |
| `emma-nav:places` | the `nav_caps` body (cached; `{"loading":true}` before the first `places` call returns) | none |
| `emma-nav:status` | the §2e status object (`{"state":"idle",…}` when no run) | none |
| `emma-nav:go:<place_id>:<tap\|voice>` | `{"accepted":b,"reason":code\|null,"run_id":hex32\|null}` | foreground + focus + !operator + !nav latch + §2e start checks on cached state. On accept, native generates `run_id` (32 hex, `SecureRandom`) *before* answering and passes it to the launcher as `--run-id`; every status line echoes it |
| `emma-nav:stop` | `{"accepted":true}` (always) | none. Sets the nav cancel token and the nav latch synchronously, then escalates on NavLauncher's stop thread |
| `emma-face:token` | `{"token":str}` (empty when not set on the robot) | none beyond the trust gate; read once on `ready` for `face_hello` (§2a) |
| any other `emma-body:`/`emma-nav:` | `{"accepted":false,"reason":"bad_request"}` | — |
| anything else | `return false` (unchanged browser behaviour) | — |

The trust gate is the one from today, factored into `EmmaFace.trustedPreview(url, view)` and applied to
**every** `emma-*` message: `trusted(originOf(source)) && trusted(originOf(current)) &&
"/preview".equals(source.getPath()) && "/preview".equals(current.getPath()) &&
"1".equals(current.getQueryParameter("app"))`. If it fails, the result is `{"accepted":false,"reason":"bad_request"}`
and nothing runs.

**No I/O on the UI thread.** `onJsPrompt` runs on the UI thread. Every answer comes from cached state, and
the work is queued. "Accepted" therefore means *queued after the cheap gates passed*. The definitive outcome
arrives as status.

**Threads (build review #4).** MainActivity's `io` is a `newSingleThreadExecutor` (line 93) that also runs
`driveLoop`, the arm connect (≈5 s) and the rest wait (≤15 s). A tether ping or a STOP queued behind those
would miss `TETHER_S` (false `app_lost`) or wait seconds to stop. So:

- arm work (connect, gestures, rest-before-run) stays on `io`;
- NavLauncher owns three dedicated threads: `nav-reader` (launcher stdout), `nav-pinger` (a ping every
  1000 ms) and `nav-stopper` (the §4 stop escalation), plus `nav-poller` (every 5 s, `emma_nav.py status`, for
  PC-started runs, §2c);
- a body stop sets the latch and voids every timer **synchronously on the calling thread**, then calls
  `stopAll()` on a dedicated `body-stopper` thread. Every gesture task queued on `io` re-checks the latch and
  the nav state before it moves anything, so work that was queued before the stop never runs after it.

A source test asserts that none of `nav-reader`/`nav-pinger`/`nav-stopper`/`nav-poller`/`body-stopper` submit to `io`.

**Cancel token for a launch in progress (safety review #2).** "Accepted" for `go` means queued, and the
launcher writes `run.pid` only when drive_route spawns. A STOP during `preparing` (arms resting) or `starting`
(launcher preflight) would otherwise be a no-op and the leg would still start after `#AGF`. So NavLauncher holds
an `AtomicReference<String> cancelled` for the current `run_id`:

1. `emma-nav:stop` (and every other stop path) sets it, and sets the **nav latch** (`NAV_STOP_LATCH_MS = 60000`,
   or until "ล้างการล็อกการเดิน" on the operator screen, the same rule as the body latch).
2. It is checked after the rest returns and again immediately before `su` is executed. If it is set, the run
   ends `stopped/stop_pressed` without spawning anything.
3. If the launcher is already running, `stop` goes down its stdin. `emma_nav.py go` reads stdin from its first
   line of code (§2e), so a stop during its preflight aborts before drive_route is spawned.

**Native → page** uses only fixed event names through `notifyPage`: `emma-body-changed` and
`emma-nav-changed`. They carry no payload, and the page pulls the data with `emma-*:status`.

### 2c. Gesture table on the app, and its hard clamps

**Built-in table** (new `BodyGestures.java`, compiled in; the server never sees these numbers).

| id | built-in status | how it moves | after |
|---|---|---|---|
| `handshake` | verified (group 6, logcat 30 Sep + owner) | `playGroup(6)` | rest (group 99) on `#AGF` + 5000 ms; fallback 30 s |
| `greet_arm` | verified (group 3, 11 Sep camera test: image-left arm up, elbow bent, head turns) | `playGroup(3)` | rest on `#AGF` + 1500 ms; head marked unknown |
| `rest` | verified (group 99, 30 Sep) | `resetHome()` | head marked unknown |
| `head_center` | direction_known | frame `{7:1500, 8:1500} T3000` | — |
| `head_down` | direction_known (ch8 `#8P1540T3000` measured by the owner, 30 Sep: +40 = camera looks down) | `{8:1540}T3000`, hold `{8:1540}T2500`, `{8:1500}T3000` | — |
| `nod` | direction_known (same +40, nothing further) | `{8:1540}T1500, {8:1500}T1500, {8:1540}T1500, {8:1500}T1500` | — |
| `head_up` | **needs_mapping** (the ch8 minus side has never been driven; ch8 is the servo that stalled and burned on 12 Sep, CHANGELOG) | — | — |
| `shake_head` | **needs_mapping** (needs the ch7 minus side, never driven) | — | — |
| `head_left`, `head_right` | needs_mapping (ch7 + is *image*-right; the robot's own left is unconfirmed; the minus side is untested) | — | — |

**Only measured pulses are in the code.** The one owner-verified head pulse is `#8P1540` (memory note
"robot-zc3588a-state": "reuse 1540, never guess further numbers"), and the design uses nothing else on ch8. The
first revision used ±80 and the ch7/ch8 minus sides. The build review rejected that, correctly, and it is gone.
The owner's measurement used T3000, so the first move of each head gesture is T3000. Note from the same
memory: group 99 may knock the head back up after a rest-return. That is recorded at bench B1 and is not
corrected in code until it is seen.
| `raise_left_arm`, `raise_right_arm`, `wave`, `hands_left`, `hands_right` | needs_mapping (no channel evidence; group 3 is not a clean "raise") | — | — |
| `stop` | — | `stopAll()` (SDK `stopArmAll`) + void rest timers + latch | — |

"Left" and "right" always mean **the robot's own** left and right. The operator screen labels them as
"แขนซ้ายของหุ่น (ขวามือเราเมื่อยืนหน้าหุ่น)".

**Head position is not known after a group or at app start.** Groups move joints we cannot read back.
`BodyController` keeps `headKnown`. When it is false, the head gesture gets a prefix frame
`{7:1500, 8:1500} T5000`, which is the slowest a `PoseFrame` allows (the rule from `robot_arm.centre`: the
blind first move is the slowest one available). The prefix runs once, after which `headKnown = true`. Any
group gesture or `rest` sets it back to false.

**Operator-recorded poses** (`BodyPoseStore.java`, SharedPreferences `emma_body_poses`, key `poses_json`):

```json
{"format": 1,
 "poses": {
   "raise_left_arm": {
     "frames": [{"duration_ms": 2000, "targets": {"5": 1620}},
                {"duration_ms": 2500, "targets": {"5": 1620}}],
     "rest_after": true,
     "recorded_at": "2026-10-12T10:31:00+07:00",
     "note": "แขนซ้ายของหุ่น ยกด้านข้าง ดูจากกล้องหน้า",
     "played_ok": true
   }}}
```

A recorded pose gives that id the effective status `operator_mapped`. **"บันทึกร่างนี้เป็นท่า…"** (new
button in the pose studio, P4) saves only if all of these hold, and otherwise names the rule that failed in
Thai:

1. The id is in the vocabulary, and its built-in status is `needs_mapping` or `direction_known`. Verified
   gestures cannot be overwritten.
2. There are 1–6 frames, each frame is 800–5000 ms, and the total is ≤ 12000 ms.
3. Every channel is in the **voice channel allow-list**. That list is on the robot, editable only on the operator
   screen, and defaults to `{5, 7, 8}`, the only channels with camera evidence (`servo-channel-map.json`).
4. Every pulse is inside `LIMITS[ch]`, a code constant: `7` and `8` → **1460–1540** (±40, the measured range
   and nothing more); every other channel → 1300–1700, which is the same ±200 as `ROBOT_ARM_SPAN`. Widening a
   limit takes an APK build **and** a recorded measurement cited in the constant's comment
   (`docs/robot-servo-limits-*.md`). Both are deliberate. So `head_up`, `shake_head` and `head_left/right` can be
   recorded at bench B2 only inside ±40 until someone measures more travel.
5. The draft has been played with "เล่นร่างท่า" since it was last edited (`played_ok`): somebody watched it move.
6. If the pose touches channel 7 or 8 and its last frame does not return them to 1500, the controller adds
   `{7:1500, 8:1500} T1200`. `rest_after` is set automatically when any channel other than 7 and 8 is used.

**Clamps the app enforces on every voice or tap gesture** (`BodyController`; the constants are named and read by tests):

| rule | value |
|---|---|
| one gesture at a time (including the camera greeting and `rest` before a run) | `AtomicBoolean busy` |
| cooldown after a gesture ends (after its rest-return) | `COOLDOWN_MS = 3000` |
| rate | `MAX_PER_MINUTE = 6` |
| frame duration | `MIN_FRAME_MS = 800`, `MAX_FRAME_MS = 5000`, `MAX_FRAMES = 6`, `MAX_TOTAL_MS = 12000` |
| channels / pulses | the allow-list and `LIMITS` as above, re-checked at play time and not only at save |
| `direction_known` gestures | refused (`direction_unconfirmed`) until the operator turns on "เปิดท่าที่ยังไม่ทดสอบทิศ" (`allow_direction_known`, default off) |
| `needs_mapping` | refused (`needs_mapping`) |
| rest-return | arm gestures always end in group 99 (on `#AGF` + hold, or the last frame + 1000 ms, or a 30 s fallback) |
| no gestures while navigating | refused (`navigating`) while NavLauncher is in `preparing`/`starting`/`running`, **or** the `nav-poller` sees a live `run.pid` from any origin (a PC `robot_python run` shares it), **or** the poll result is unknown while the nav runtime is installed (`nav_unknown`: we cannot tell, so we do not move). The SafetyBar shows for a PC-started run too, labelled "■ หยุดหุ่น (สั่งจากคอมพิวเตอร์)" |
| explicit stop | latch set and timers voided synchronously, then `stopAll()` on `body-stopper` (§2b threads); all rest/hold timers voided ("after an explicit stop NOTHING may move on a timer"), state `latched` for `STOP_LATCH_MS = 60000` or until the operator presses "ล้างการล็อกท่าทาง" |
| loss of control | `onPause`, window focus lost, operator screen opened, `onDestroy` → the same as an explicit stop |
| robot-side master switch | `body_enabled`, default **off** (operator screen) |

Check order, which is also the order of the reasons a refusal reports: `disabled → operator_screen →
not_foreground → navigating / nav_unknown → latched → needs_mapping / direction_unconfirmed → busy → cooldown →
rate_limited`.
After that the action is queued, and an arm-connect failure arrives later as `body_status failed arm_unavailable`.

### 2d. Voice tools

**New group `body`** (`app/tools/body.py`, P1). It is opt-in (`_OPT_IN`) and is never loaded unless
`TOOL_GROUPS` names it. It is visible only when `robot_face_session` is set **and** `ROBOT_BODY_ENABLED=true`
(`registry.allowed`, P0). It is never in `MULTI_SESSION_SAFE`. While `move_body` is visible, `request_handshake`
is hidden, so the handshake has a single path.

```python
@tool(name="move_body", exclusive=True, preempt=lambda args: args.get("action") == "stop",
      tags=["robot", "body"],
  description=(
    "ขยับหัว แขน หรือมือของตัวหุ่น Emma หนึ่งท่า เมื่อคนตรงหน้าสั่งชัดเจน เรียกทันทีไม่ต้องถามซ้ำ "
    "เช่น 'ก้มหน้าหน่อย'→head_down 'เงยหน้า'→head_up 'พยักหน้า'→nod 'ส่ายหน้า'→shake_head "
    "'หันซ้าย'→head_left 'หันขวา'→head_right 'มองตรง'→head_center 'ยกแขนซ้าย'→raise_left_arm "
    "'ยกแขนขวา'→raise_right_arm 'โบกมือ'→wave 'ยกมือทักทาย'→greet_arm 'จับมือกันไหม'→handshake "
    "'ขยับมือไปทางซ้าย/ขวา'→hands_left/hands_right 'เอาแขนลง' 'ท่าพัก'→rest "
    "'หยุด' ระหว่างหุ่นกำลังทำท่า→stop · ซ้าย/ขวาคือของตัวหุ่นเอง · "
    "อย่าเรียกเพียงเพราะมีคนถามว่าหุ่นทำท่าอะไรได้บ้างหรือมีแขนไหม · ผลไม่สำเร็จห้ามเรียกซ้ำเอง"),
  parameters={"type": "object",
              "properties": {"action": {"type": "string", "enum": [<BODY_ACTIONS ids>],
                                        "description": "ชื่อท่าจากรายการเท่านั้น"}},
              "required": ["action"]})
async def move_body(action: str) -> dict
```

The parameter is the enum only (a test asserts that `properties` has exactly `action` and that it has an
`enum`).

**A stop is never "busy" (safety review #1, build review #9).** `registry._dispatch` keys an exclusive tool by
name alone and answers `busy` while a call is in flight (registry.py:236-239), so `move_body(stop)` during a
pending `nod` ack would have been refused. P0 adds one registry keyword, `preempt: Callable[[dict], bool] |
None`. When it returns true for the call's arguments, the in-flight check is skipped for that call; the call is
not recorded in `_inflight` either, so it cannot block anything. It is not part of the declaration sent to the
model, so declarations stay byte-identical. `move_body` uses it for `action == "stop"`, and the existing
`stop_moving` gets `preempt=lambda a: True`. `robot_face_link.request` has the matching exemption (§2a). The
revert-red test: with a `nod` ack pending (a fake page that never answers), `move_body(stop)` must send a
`body_command stop` within 100 ms, and `stop_moving` must send both stops while a `go` ack is pending.

The flow inside `robot_body.request(action)` runs these steps in order, and the first one that fails
decides the result:

1. If `action` is not in the vocabulary, return `bad_request`.
2. Check the server gate: `ROBOT_BODY_ENABLED`, `ROBOT_ARM_ENABLED` and `ROBOT_ARM_MOTION_ENABLED` must all
   be true (the existing interlock, D2), and `greet_arm`, `handshake` and `rest` also need
   `ROBOT_ARM_GROUPS_ENABLED`. If any is off, return `hardware:"mock"`, reason `server_disabled`. `stop` skips
   this gate and is always sent.
3. If `robot_face_session` is unset, return `not_robot_face`.
4. **Heard guard** (`ROBOT_BODY_REQUIRE_HEARD=true`). Return `heard_missing` and tell the model to ask unless
   **all** of these hold. This asks a question; it never moves the robot. The precedent is `heard.py` ("bit
   like" closed the deck). `stop` is never guarded.
   - **Fresh.** `heard.caption_for(session)` (P0) returns the latest caption *of this session* with its
     monotonic time. It counts only if it was recorded **after the latest `activity_start`** this session sent
     (VAD local, gemini.py:690, which P0 marks with `heard.mark_activity(session)`), and is at most
     `ROBOT_HEARD_MAX_AGE_S=10` old. `heard._LAST` was one global holding the latest caption. A caption that
     lands *after* the tool call used to leave the previous utterance in place, e.g. "ยกแขนได้ไหม", to authorise
     the new call. Now that case is stale and the model asks. With server-side VAD (no activity_start), only the
     age bound applies, and a test names that weaker case.
   - **Unused.** A caption authorises at most one motion. `robot_body` remembers the caption's time it last
     consumed, so one sentence cannot authorise a second gesture on a retry.
   - **The right words.** `retrieval.tokenize` of the caption contains a VERB token from `BODY_HEARD[action]`
     and, for a sided action, the SIDE token (§2.0). Bare nouns never count: "ลดราคา", "ข้างหน้า", "ท่าน" and
     "มือถือ" do not pass. A Thai substring match would let `ยก` match inside `ยกเลิก` (CLAUDE.md), so this uses
     words only.

   Tests: each of "ลดราคา", "ข้างหน้า", "ยกเลิก", "มือถือ", "ไปไหนมา" fails. "ก้มหน้าหน่อย" passes for
   `head_down` and fails for `raise_left_arm`. "ยกแขนซ้าย" fails for `raise_right_arm`. A caption recorded
   before the latest `activity_start` fails. A caption used once fails the second time.
5. If the cached `body_caps` for this session says the id is `needs_mapping` (or `direction_known` while
   that is off), return that refusal without a round trip, together with the list of available gestures.
6. Call `robot_face_link.request(session, "body", {"action": id}, ROBOT_BODY_ACK_TIMEOUT_S=2.5)`.
7. Map the ack to the result below.

Result fields: `ok` (true only when the gesture was accepted), `hardware`
(`requested`|`refused`|`failed`|`mock`), `action`, `reason` (code\|null), `status`, `est_s`, `available` (the
list of ids currently playable), and `instruction` (Thai). Each instruction quotes the wrong phrase it rules
out, and each ends with "ตอบเป็นภาษาเดียวกับที่ผู้ใช้พูด" (the 9.2% English-reply drift, 8 Oct eval):

- `requested`: "หุ่นรับคำสั่งท่า{th}แล้วและกำลังเริ่มทำ ยังไม่ได้ยืนยันว่าทำจบ ตอบรับสั้นๆ เช่น 'ได้ค่ะ' ห้ามพูดว่า 'ทำเสร็จแล้ว' หรือ 'ยกแขนแล้วค่ะ'"
- `needs_mapping` / `direction_unconfirmed`: "ท่า{th}ยังไม่ได้ตั้งค่าบนตัวหุ่น ห้ามบอกว่าทำแล้ว ให้บอกตรงๆ ว่ายังทำท่านี้ไม่ได้ ท่าที่ทำได้ตอนนี้คือ {available_th}"
- `busy` / `cooldown` / `rate_limited`: "หุ่นยังทำท่าก่อนหน้าไม่จบ ห้ามสั่งซ้ำเอง ให้บอกให้รอสักครู่"
- `navigating`: "หุ่นกำลังเดินอยู่ จึงขยับแขนไม่ได้เพื่อความปลอดภัย ให้บอกตรงๆ"
- `heard_missing`: "ไม่ได้ยินคำสั่งท่าทางชัดเจน ห้ามขยับเอง ให้ถามสั้นๆ ว่าต้องการให้ทำท่าอะไร"
- `mock` / `server_disabled`: "ตอนนี้ปิดการสั่งท่าทางไว้ ห้ามบอกว่าทำแล้ว ให้บอกตรงๆ ว่ายังสั่งท่าไม่ได้"
- `timeout` / `disconnected` / `request_pending`: "หุ่นไม่ตอบรับคำสั่ง ยังไม่รู้ว่าขยับหรือไม่ ห้ามสั่งซ้ำเอง ให้บอกว่าตอนนี้สั่งท่าไม่ได้"
- `nav_unknown`: same as `navigating`.
- `stop` accepted: "ส่งคำสั่งหยุดแขนแล้ว แต่ยังยืนยันไม่ได้ว่าแขนหยุดจริง ตอบรับสั้นๆ ห้ามพูดว่า 'แขนหยุดแล้ว' ถ้ามีอันตรายให้บอกคนให้กดปุ่มหยุดฉุกเฉินหรือปิดสวิตช์หลัก"

Completion (`body_status`) is recorded in turnlog as `body` with fields `action`, `state` and `reason` (names
and codes only, no speech). It updates `robot_body.STATE` (`last_action`, `state`, `at`) so that
`get_robot_status` can report it. **There is no spoken announcement** for a gesture ending, because that would
be a chatty robot. The tool already told the truth: requested, not done.

**Navigation on the robot face** (P2, `app/tools/robot.py` → new `app/robot_nav.py`). The tool names stay
as they are, so the descriptions sent to the sales room stay byte-identical. When
`ROBOT_NAV_ROUTES_ENABLED=true` **and** `robot_face_session` is set, the existing tools are routed as follows:

- `go_to_place(place)`:
  0. **Voice key (safety review #6).** `ROBOT_NAV_VOICE` ∈ `off` (default) | `unlocked` | `on`. `off` →
     refuse `voice_disabled`; the instruction tells the model to say that staff start walks from the screen.
     `unlocked` → allowed only while a staff PIN unlock (`/robot-nav/unlock`) is inside its TTL for this
     robot face. `on` → allowed. The robot face talks to the public (the joke profile), and "ไป" is in almost
     every sentence, so a verified leg plus a heard word was not enough to let a passer-by send the robot down
     the corridor. The PIN covered taps only.
  1. Match the request with `robot_places.match(request, places)`: every word of `name_th`, `name_en` or
     one alias must appear in the request, the longest unique match wins, and an ambiguous request returns
     None (the `find_place` rule).
  2. Refuse a leg that is not verified for voice (`ROBOT_NAV_VOICE_UNVERIFIED=false` → `leg_unverified`). The
     server works out "from" from the last `nav_caps.here`; when that is unknown, the native side decides.
  3. Apply the heard guard (`ROBOT_NAV_REQUIRE_HEARD=true`), with the same freshness and single-use rules as
     the body guard. The fresh caption must itself match **the same place** through `robot_places.match`
     (its name or one alias, whole words). A motion word alone (ไป พา เดิน กลับ) no longer counts: "ไปไหนมา"
     must not send the robot anywhere. `return_to_base` requires a dock name or alias (แท่นชาร์จ ที่ชาร์จ dock
     charger), or กลับ together with แท่น/ฐาน. Not บ้าน: "กลับบ้าน" is a guest saying goodbye.
  4. Call `robot_face_link.request(session, "nav", {"action":"go","place":id,"origin":"voice"}, 3.0)`.
  5. When accepted, return `hardware:"requested"`. The instruction is "ส่งคำสั่งให้หุ่นไป{name}แล้ว หุ่นจะเก็บแขนก่อนออกเดิน ยังไม่ได้ยืนยันว่าออกเดิน ห้ามบอกว่าถึงแล้ว ระหว่างเดินพูดสั้นๆ รอข้อความแจ้งว่าถึง".
- `return_to_base()` is the same as `go_to_place` with the place `dock`.
- `stop_moving()` sends `nav_command stop` **and** `body_command stop`. It sends both even if the server
  believes nothing is moving (the existing rule).
- `get_robot_status()` adds `nav` = {`here`, `run` (the last status), `places` [{`id`,`name_th`,`available`,`reason`}]}
  and `body` = `robot_body.STATE`.

**Terminal states** of a run whose `origin` is `voice` and for which `robot_face_link.issued_run(session,
run_id)` is true (the app generated the id and returned it in the `nav_ack` to *this* session's command) go
through `events.announce(source="robot_nav")` with `still_relevant` (the run is still the latest one):

- `arrived`: "ถึง{name}แล้ว บอกสั้นๆ ว่าถึงแล้ว"
- `stopped`, `refused` or `failed`: "ไป{name}ไม่สำเร็จ: {reason_th} หุ่นหยุดอยู่ที่ {step_label_th} บอกตรงๆ แล้วเสนอเรียกเจ้าหน้าที่"

Runs started by a tap are recorded but not announced; staff are driving those. If a running run sends no
`nav_status` for `ROBOT_NAV_STATUS_STALE_S=20` s, the server makes one announcement ("ขาดการติดต่อกับหุ่นระหว่างเดิน
ห้ามบอกว่าถึงแล้ว") and sends **no** stop, because the run's own tether and the native bar are the stops.

**How "started/refused" arrives quickly.** The ack is the synchronous return value of `window.prompt`, relayed
by the page, so it takes ~100–300 ms on a LAN. The `ROBOT_*_ACK_TIMEOUT_S` values bound it well inside
`TOOL_TIMEOUT_S` (15 s). A `nav` accept means "queued; the arms are being rested". The real start (`running`), or
a preflight refusal (`refused`), arrives as `nav_status`. Nothing holds a tool call open while anything moves.
`long_running` / NON_BLOCKING are **not** used: every tool returns within seconds, so the 3.1/3.8 difference
does not matter.

### 2e. On-robot launcher — `scripts/emma_nav.py` (P3)

It runs on the robot under `/data/local/tmp/emma-nav/py` (python-build-standalone). It imports only the
stdlib plus `app.robot_places` and `app.robot_contracts`, both stdlib-only and both added to
`robot_python.CODE`. It never imports numpy or `drive_route`, so it starts fast.

```
emma_nav.py check                      read-only: runtime ok, code MANIFEST hashes match, places file + route sha ok,
                                       board reachable, battery, docking, localization on/off, pose, here
emma_nav.py places                     one JSON object = nav_caps body (+ "run": status|null); no motion
emma_nav.py status                     one JSON line, the status object below
emma_nav.py go <place_id> --origin tap|voice --run-id <hex32> [--tether]
                                       start one leg; with --tether stay in the foreground (the app's su pipe):
                                       print a status JSON line every 1 s and on every change; read stdin lines
                                       "ping" / "stop" FROM ITS FIRST LINE OF CODE (a reader thread starts
                                       before any check), so a "stop" during steps 1-6 ends the run
                                       stopped/stop_pressed before anything is spawned; no ping for TETHER_S=4 s,
                                       or EOF → SIGINT the run (reason app_lost); "stop" → SIGINT (stop_pressed)
emma_nav.py stop                       SIGINT the pid in run.pid (works for a robot_python run too); exit 0
emma_nav.py reap                       run by the app on every start (§4): if run.pid is alive, its status
                                       origin is tap|voice and the launcher pid recorded in nav/status.json is
                                       dead → SIGINT it (an orphan of a dead app); if run.pid is dead and the
                                       board has a current action → DELETE /actions/:current; a PC run
                                       (origin "pc" or no launcher record) is left alone
```

Every line the launcher prints uses `print(..., flush=True)`, and the app starts it with `python -u`. Its
stdout is a pipe, so it is block-buffered otherwise, and the app would see status lines seconds late
(build review #13).

Files (all under `REMOTE=/data/local/tmp/emma-nav`):

- `run.pid` and `run.out`: **shared** with `robot_python run`, so there is one lock for both entry points.
- `nav/status.json`: the launcher's status.
- `nav/drive_status.json`: drive_route's status (§2g).
- `nav/here.json`: `{"place":"big_room","pose":[x,y,yaw],"at":epoch,"run_log":"20261012-103100_floor2_bigroom_roundtrip.jsonl"}`.
- `nav/route_state.json`: drive_route's `--state-out` at the end of a leg (`measured_at`, `door_seen`, the
  run log name). The next leg gets it as `--state-in` only when `here.json` is accepted (same `run_log`, pose
  within tolerance), because both are odometry-frame coordinates and are meaningless once the frame changes.
- `code/MANIFEST.json`: written by `robot_python deploy` as `{"deployed_at":…, "files":{path: sha256}}`.

`go` steps. The first failure ends the run with `state:"refused"` and that reason:

1. If `run.pid` names a live process, refuse `run_active`.
2. Load the places file. If it is invalid, or its `route_sha256` ≠ the sha256 of the deployed route, refuse
   `places_stale`. If the place id is unknown, refuse `unknown_place`.
3. Read board power and pose over REST (`http://192.168.11.1:1448`, 2 s timeout). If that fails, refuse
   `board_unreachable`.
4. Work out **where the robot is** (`here`):
   - If `dockingStatus == "on_dock"`, it is at `dock` (the board's reading wins over any record).
   - Otherwise, use `here.json`'s place only if all of these hold: its `run_log` is the **newest** `*.jsonl`
     in `code/data/robot/runs` (so a later PC run makes it stale); the board pose is within
     `arrive_tolerance_m` (0.25) and `arrive_tolerance_deg` (15) of the recorded pose (pushing the robot or
     driving it with the joystick moves odometry); and the board is not on the dock.
   - Otherwise the location is unknown. Refuse `location_unknown`.
5. Find the leg `here → place`. If here is the place, refuse `already_there`; if there is no such leg, refuse
   `no_leg`. A `voice` origin on a leg with `verified:false` is refused with `leg_unverified` (the server
   checks this too).
6. If the battery is below the leg's `min_battery`, refuse `low_battery`.
7. If a stop arrived on stdin, end `stopped/stop_pressed` now. Otherwise write the status `starting` and spawn
   `../py -u scripts/drive_route.py <route> --relay http://192.168.11.1:1448 --sdk 192.168.11.1:1445 --adb-serial ""
   --from-step F --to-step T [--arms-in] --min-battery B --status-file ../nav/drive_status.json --watch-parent
   --state-out ../nav/route_state.json [--state-in ../nav/route_state.json]`
   with stdout and stderr going to `../run.out`. Write the child's pid to `run.pid`, and the launcher's own pid,
   `origin` and `run_id` to `nav/status.json` (what `reap` reads). **`here.json` is not deleted here.**
8. While the child runs, merge `drive_status.json` into the status (`running`, `step`, `do`, `label`, `door`,
   `pose`). On the **first `step` status** (the robot is about to move), delete `here.json` and the old
   `route_state.json`. Deleting at spawn (revision 1) lost a perfectly known location whenever preflight
   refused (build review #7).
9. On exit, map the exit code. `0` with `leg_done` **or** `route_done` → `arrived` (write `here.json` with the
   end pose and this run's log name). `3` → `refused` (reason from drive_route's code): if `here.json` still
   exists, **rewrite its `run_log` to this run's log name**, because drive_route's Runner creates a new jsonl
   even when it refuses, and the "newest log" rule in step 4 would otherwise make the robot's location unknown.
   `1` → `stopped` (drive_route's code). `130` → `stopped` with `stop_pressed`, `app_lost` or `launcher_lost`,
   whichever the launcher recorded (`interrupted` when none). `2` → `failed` with `board_unreachable`. Anything
   else → `failed` with `error`. Delete `run.pid`. Print the final line.

**PC runs.** `status` reports `{"state":"running","origin":"pc","reason":"pc_run",…}` when `run.pid` is alive and
no launcher record owns it (a `robot_python run`). The app's `nav-poller` uses that for the body gate and the
SafetyBar (§2c), and the cards show `run_active`.

**Status object** (`status`, the tether lines, and `nav/status.json`; also the payload of `nav_status`):

```json
{"format":1, "run_id":"3f9c0d2a…(32 hex, from the app)", "run_log":"20261012-103100_floor2_bigroom_roundtrip.jsonl",
 "origin":"tap", "launcher_pid":4711, "state":"running",
 "place":"big_room", "from":"dock", "step":4, "step_from":1, "step_to":10,
 "do":"door", "door":"D8", "label":"D8 out", "elapsed_s":61.2, "battery":81,
 "reason":null, "detail":null, "pose":[-2.44,0.25,-1.571], "here":null,
 "exit_code":null, "t":1791459060.4}
```

`reason` is a NAV_REASONS code. `detail` is drive_route's English message, at most 160 characters, kept for
the log and the operator, never shown to customers or spoken. `state` takes values from `NAV_STATES`;
`preparing` is added only by the app (while the arms rest), never by the launcher.

**Testability on Windows.** `os.kill(pid, SIGINT)` does not exist for child processes on Windows. The tether
logic therefore talks to a `Child` interface (`poll()`, `interrupt()`, `kill()`). Tests drive it with a fake
child and a fake clock, and only the real implementation sends signals. The where-am-I decision, leg lookup,
exit-code mapping and status merge are pure functions.

### 2f. Places table — `data/robot-places/floor2.json` (P3, **tracked**; `data/robot/` stays gitignored)

```json
{"format": 1,
 "route": "data/robot/routes/floor2_bigroom_roundtrip.json",
 "route_sha256": "<sha256 of that file as deployed — refuse everything when it differs>",
 "places": [
   {"id": "dock", "name_th": "แท่นชาร์จ", "name_en": "charging dock",
    "aliases_th": ["ที่ชาร์จ", "แท่น", "จุดจอด"], "aliases_en": ["dock", "charger", "base"], "kind": "dock"},
   {"id": "big_room", "name_th": "ห้องประชุมใหญ่", "name_en": "big meeting room",
    "aliases_th": ["ห้องประชุม"], "aliases_en": ["meeting room"], "kind": "room",
    "arrive_tolerance_m": 0.25, "arrive_tolerance_deg": 15}
 ],
 "legs": [
   {"from": "dock", "to": "big_room", "from_step": 1, "to_step": 10, "min_battery": 30,
    "verified": false, "est_s": null,
    "doors": ["D8", "D11"], "note": "steps 1-10 of the round trip, ending at checkpoint 'inside the big room'"},
   {"from": "big_room", "to": "dock", "from_step": 10, "to_step": 20, "min_battery": 10,
    "verified": false, "est_s": null,
    "doors": ["D11", "D8"], "note": "steps 10-20: re-measures 'inside the big room', then D11 out ... own back-in dock"}
 ]}
```

- Step numbers are **1-based**, as drive_route numbers them (`run(from_step=1)`, `steps[i-1]`). In the
  current file, step 10 is `checkpoint "inside the big room"`, step 11 is `door D11 out` and step 20 is `dock`.
- **Leg 2 starts on the checkpoint where leg 1 ends (build review #2).** Revision 1 started leg 2 at step 11,
  a door, in a fresh process. drive_route's door pass reads `measured_at` (line 2086) and the door memory
  `door_seen` (`_door_remembered`, line 2292, the 7 Oct fix for the D11-out sidesteps). Both live only in the
  Runner's memory, so a cold start took the "never measured" frame-centre guess branch and had no door
  memory. That is not the behaviour the verified round trip has. Now:
  1. leg 2 re-runs step 10, a stationary measurement at the pose where leg 1 stopped, so the D11 pass starts
     from a measured pose;
  2. leg 1 writes `--state-out` (`measured_at`, `door_seen`) and leg 2 reads it with `--state-in`. Whether step
     10 re-measures D11's lateral axis or not, the door memory is then valid or invalid exactly as it would be
     inside the full round trip (`_door_remembered` compares `measured_at[lat_axis]` with the value stored
     with the memory).
- `validate` allows legs to share **exactly one** boundary step, and only when that step is a `checkpoint`
  that ends one leg and starts the other. Any other overlap is still rejected.
- `verified` turns true only after two clean supervised runs of that leg on the robot (§6c). Until then the
  leg can be started only by a tap, and the card says so.
- `est_s` stays `null` until it is filled from the per-step seconds in
  `data/robot/runs/*bigroom_roundtrip.summary.json`. The UI hides the estimate while it is null. **No number
  is invented here.**
- `min_battery` is per leg: you must not be stranded on the way out, and going home must still be allowed at
  low battery.
- `app/robot_places.py` (P0, stdlib only) provides `load(path) -> Places` (raises `ValueError` naming the bad
  field), `validate(places, route_steps)` (every leg must end on a `checkpoint` or `dock` step, `from_step ≤
  to_step ≤ len(steps)`, legs must not overlap except for the shared-checkpoint rule above, every id must match
  `ID_RE`), `match(request, places)`,
  `leg(places, frm, to)` and `sha256_file(path)`.

### 2g. `drive_route.py` changes (P3)

- `--to-step N` (1-based, inclusive; the default is `len(steps)`).
  - Validated before anything moves: `from_step ≤ N ≤ len(steps)`, and step N must be `checkpoint` or `dock`.
    A leg must end where a measured, stationary pose exists. If the check fails, print the reason and **exit 3**.
  - The run loop becomes `while i <= self.to_step`.
  - `chain_from(i)` is **not** clamped. It chains only consecutive `drive` steps, and `to_step` is always a
    `checkpoint` or `dock` (validated above), so a chain can never cross it. Revision 1's clamp was dead code
    and its test could not go red (build review #11). An `assert end <= self.to_step` documents the invariant.
  - `door_exit_plan(i)` returns `None` when `lane_k > to_step` **or** when `skipped == to_step`: the final
    checkpoint of a leg must never be "rounded out" on the move.
  - The LinkLost re-run ranges in `run` and `door_then_corner` are bounded by `to_step`.
  - **Whenever `--to-step` is given**, the end logs `kind="leg_done"` with the pose, even when `to_step ==
    len(steps)` (leg 2 ends on step 20 = the last step; revision 1 would have logged `route_done` there, and the
    launcher would never have called it arrived). Without `--to-step`, a full route still logs `route_done`.
    `scripts/analyze_run.py` (line 164 counts only `route_done`) treats `leg_done` as done too.
- `--state-out PATH` / `--state-in PATH`: JSON `{"format":1,"run_log":…,"measured_at":{axis:[x,y]},
  "door_seen":{door:{"offset":[ox,oy],"lat_axis":…,"measured":[x,y]|null}}}`, written atomically at
  `leg_done`/`route_done` and loaded into the Runner before `run()` when given (§2f). A file that fails to parse
  is ignored with a printed line, never trusted in part.
- `--status-file PATH`: `Runner.log` also writes PATH (written to `PATH.tmp`, then `os.replace`) on these kinds:
  `run_start`, `step`, `stop`, `refused`, `leg_done`, `route_done`, `interrupted`. The content is
  `{"format":1,"state":"running|done|stopped|interrupted|refused","step","step_from","step_to","steps_total",
  "do","label","door","reason_code","reason","pose","t"}`.
- `Stop` gains `code` (default `"stopped"`), **set at every raise site that can end a run**, because the
  exception classes are rewrapped on the way out (build review #6): `run_step` re-raises a `LinkLost` as
  `Stop("step %d %r: the link dropped %d times -- giving up")` (line 2948) and a `DoorBlocked`/`StepBlocked` as
  `Stop("%s -- still, after %d goes round it")` (line 2934), and `recover_link` raises a plain `Stop` (line 3051).
  So:
  - those three sites set `code="link_lost"`, `code=e.code` (propagated from the original) and
    `code="link_lost"`;
  - `DoorBlocked` → `door_blocked`, `StepBlocked` → `obstacle`, `LinkLost` → `link_lost` as class defaults;
  - every dock Stop that ends "standing by the charger" / "not backing in" → `dock_blocked`;
  - each preflight raise gets its own code: `mapping_on`, `action_running`, `localization`, `battery_low`,
    `not_on_dock`. All of them map to the launcher reason `refused`, with `detail` kept;
  - a test lists every `raise Stop(` in the file (AST scan) and fails on any site inside `run`/`run_step`/
    `recover_link`/`preflight` without an explicit `code=` or a documented default. No reason is ever recovered
    by matching message text.
- **`main()` splits preflight from the run.** Preflight gets its own `try`: a `Stop` there logs
  `kind="refused"` with its code and returns **3**. The run's `except Stop` logs `kind="stop"` as today and
  returns 1. `except KeyboardInterrupt` now also logs `kind="interrupted"` with the reason the watch-parent thread
  recorded (`launcher_lost`) or none (it used to only `say` it).
- **Exit codes:** 0 done (leg or route) · 1 Stop during the run · 2 board unreachable · **3 refused before any
  motion** (preflight Stop or an invalid `--to-step`; this used to be 1) · 130 interrupted.
- `--watch-parent`: record `os.getppid()` at start. A daemon thread checks every 0.5 s, and when the parent
  changes (the launcher died) it records `launcher_lost` and raises SIGINT in its own process. That goes down the
  existing `KeyboardInterrupt` path (`board.cancel()`, log, summary). The check is injectable for tests.
- `preflight` still switches board localization **off** and nothing switches it back. This is deliberate: the
  second leg starts mid-route on the odometry pose, and localization "pulls the pose in ฝ่ายอาคาร". The old
  planner path is fenced off instead (§4).
- Unchanged: every existing gate (mapping, battery, forbidden areas, `start_from_dock`/`on_dock`, door
  margins, docking rules), the defaults, and `robot_python run` (no `--to-step` → the full route as today).

`scripts/robot_python.py` (P3):

- `CODE` += `app/robot_contracts.py`, `app/robot_places.py`, `scripts/emma_nav.py` (`analyze_run.py` is already
  deployed: drive_route imports it for the summary).
- `deploy` also pushes `data/robot-places/*.json` and writes `code/MANIFEST.json`.
- `stop` becomes `emma_nav.py stop` behaviour, with the same effect.
- New read-only `nav-status`, which prints `emma_nav.py status`.
- The test that checks `CODE` against the imports is extended to cover `emma_nav.py`.

### 2h. Settings (`app/config.py`, P0) and conftest pins

| setting | default | meaning |
|---|---|---|
| `ROBOT_BODY_ENABLED` | `false` | server key for `move_body` (the group must also be named in `TOOL_GROUPS`; the robot must also have `body_enabled`) |
| `ROBOT_BODY_ACK_TIMEOUT_S` | `2.5` | wait for `body_ack` |
| `ROBOT_BODY_REQUIRE_HEARD` | `true` | heard-word guard for body actions |
| `ROBOT_NAV_ROUTES_ENABLED` | `false` | robot face voice nav → launcher; the chassis planner refuses moves (§4) |
| `ROBOT_NAV_ACK_TIMEOUT_S` | `3.0` | wait for `nav_ack` |
| `ROBOT_NAV_PLACES_FILE` | `data/robot-places/floor2.json` | server copy of the places table (matching, names) |
| `ROBOT_NAV_VOICE_UNVERIFIED` | `false` | allow voice on legs not yet verified |
| `ROBOT_NAV_REQUIRE_HEARD` | `true` | heard-word guard for voice nav |
| `ROBOT_NAV_VOICE` | `off` | `off` \| `unlocked` \| `on` — may voice start a walk at all (§2d step 0) |
| `ROBOT_HEARD_MAX_AGE_S` | `10` | oldest caption the body/nav heard guard accepts |
| `ROBOT_NAV_PANEL` | `off` | `off` \| `pin` \| `open` — the face page's นำทาง slot. **At boot, a value ≠ `off` while `ROBOT_NAV_ROUTES_ENABLED=false` is forced to `off` with a loud log line** (taps with the old planner still live = two navigation systems at once). It warns rather than refusing to boot, the `warn_if_open_to_the_network` precedent |
| `ROBOT_NAV_PIN` | `""` | staff PIN for `pin` mode (empty + `pin` = the slot says it is not set up) |
| `ROBOT_NAV_STATUS_STALE_S` | `20` | silence during a voice run before one announcement; also the "stale" bound of `nav_activity()` |
| `ROBOT_FACE_TOKEN` | `""` | when set, the face session needs a matching `face_hello` (§2a) |
| `TURN_IDLE_TRACKS_TOOLS` | `false` | §5 P0: `turn_complete` sets `turn_idle` only when no tool call is in flight. **Off by default**, so the sales room's announce and nudge timing does not change on pull |
| `ANNOUNCE_SETTLE_S` | `0.0` | wait after `turn_idle` before announcing. **0 by default** (revision 1 had 0.8, which would have changed condo timing). P6 measures the gap and proposes a value; the owner sets it |

`ROBOT_ARM_ENABLED`, `ROBOT_ARM_MOTION_ENABLED` and `ROBOT_ARM_GROUPS_ENABLED` stay the hardware interlock and
are unchanged. Robot-side keys live in SharedPreferences `emma_body_nav` and are set only on the operator screen:

- `body_enabled` (false)
- `allow_direction_known` (false)
- `voice_channels` ({5,7,8})
- `nav_enabled` (false)
- `nav_require_arm_rest` (true)
- `face_token` ("") — typed on the operator screen, matched against the server's `ROBOT_FACE_TOKEN`

**conftest pins** (in `_fresh_async_state`):

- `robot_body_enabled=False`, `robot_nav_routes_enabled=False`, `robot_nav_panel="off"`, `robot_nav_pin=""`,
  `robot_nav_voice="off"`, `robot_face_token=""`, `turn_idle_tracks_tools=False`,
  `robot_nav_places_file=""` (tests pass fixtures explicitly), `announce_settle_s=0.0`.
- Reset `heard`'s per-session caption store and activity marks.
- Reset `robot_face_link`, and also `robot_body` and `robot_nav` through `sys.modules.get(...)` (importing a
  tool module registers its tools; the same reason calc is reset that way).

**Byte-identical default:** with these defaults, the tool list, tool descriptions, prompts and adaptation
phrases are unchanged for every profile. P0 and P6 each add a snapshot test of that. **Runtime too (safety
review #8):** P0 adds a condo runtime test. A fake provider emits `turn_complete` before `tool_call` before
`tool_result`, and the test asserts that `turn_idle` and `events.announce` timing are exactly today's with the
defaults (no settle, no tool tracking).

---

## 3. UI (หน้าจอ 1080×1920 แนวตั้ง)

**Entry: the existing hold menu, slot 2.** The existing gesture stays as it is: hold Emma for 0.5 s
(`HOLD_MS` in `mascot.js`, shared with the face scan) and the six slots appear. Slot 1 is
`สแกนใบหน้า`. Slot 2 becomes `นำทาง` (a map-pin icon) when `IN_APP && ROBOT_NAV_PANEL !== "off"`, and stays
the honest empty slot otherwise. The page learns the mode from `POST /robot-nav/config {token}` → `{"mode"}`
(P2), where the token goes in the body, never in a URL, which is the face-scan rule.

This deviates from D4's "long-press ~1 s". The menu hold is shared with the face scan and is not the gate. The
gates are the **PIN**, and a **1-second hold on "เริ่มเดิน"** at the moment motion is committed.

```
┌────────────────── 1080 ──────────────────┐   ① PIN (mode "pin")
│  ✕                         นำทางหุ่น       │   "สำหรับพนักงาน — ใส่รหัส PIN เพื่อสั่งหุ่นเดิน"
│                                          │   POST /robot-nav/unlock {token, pin} → {ok, ttl_s:120}
│   สำหรับพนักงาน — ใส่รหัส PIN            │   wrong: "รหัสไม่ถูกต้อง" · locked: "ใส่ผิดหลายครั้ง รอ 5 นาที"
│   เพื่อสั่งหุ่นเดิน                         │   (lockout reuses face_scan's per-client counter pattern)
│   ┌──────────────────────────────┐       │   The PIN is a customer deterrent, NOT a security
│   │  ● ● ● ●                     │       │   boundary: the boundary is WS_TOKEN + the trusted-origin
│   └──────────────────────────────┘       │   bridge + the robot's own nav_enabled key.
│        [ ยกเลิก ]      [ ตกลง ]           │
└──────────────────────────────────────────┘

┌──────────────────────────────────────────┐   ② places
│  ✕                         นำทางหุ่น       │   header line from emma-nav:places:
│  หุ่นอยู่ที่: แท่นชาร์จ · แบต 82%           │   "หุ่นอยู่ที่: {here_th|ไม่ทราบ} · แบต {n}%"
│                                          │
│ ┌──────────────────────────────────────┐ │   card (full width, min-height 160 CSS px,
│ │  ห้องประชุมใหญ่                        │ │   name 2.2rem, whole card is the target)
│ │  Big meeting room                    │ │
│ │  ผ่านประตู D8 และ D11                 │ │   badge when verified=false:
│ │  ⚠ ยังไม่เคยวิ่งช่วงนี้แยก — เดินตามดูด้วย │ │   "ยังไม่เคยวิ่งช่วงนี้แยก — เดินตามดูด้วย"
│ └──────────────────────────────────────┘ │
│ ┌──────────────────────────────────────┐ │   disabled card: 45% opacity, reason in place
│ │  แท่นชาร์จ                    (จาง)    │ │   of the doors line, e.g. "อยู่ที่นี่แล้ว"
│ │  อยู่ที่นี่แล้ว                          │ │
│ └──────────────────────────────────────┘ │   whole-panel states (replace the card list):
│                                          │   not installed / disabled / location unknown /
└──────────────────────────────────────────┘   run active → its NAV_REASONS text + [ปิด]

┌──────────────────────────────────────────┐   ③ confirm sheet (bottom sheet over ②)
│        ให้หุ่นเดินไป                       │   title: "ให้หุ่นเดินไป {name_th} ใช่ไหม?"
│        ห้องประชุมใหญ่ ใช่ไหม?               │   • "หุ่นจะเก็บแขนลงก่อนออกเดิน"
│  • หุ่นจะเก็บแขนลงก่อนออกเดิน               │   • "ทางผ่านประตู {doors} — ดูให้ประตูเปิดและไม่มีคนยืนขวาง"
│  • ทางผ่านประตู D8 และ D11 —              │   • "เดินตามดูได้ กดปุ่มแดง ■ หยุดหุ่น ได้ทุกเมื่อ"
│    ดูให้ประตูเปิดและไม่มีคนยืนขวาง          │   [ยกเลิก]   [กดค้าง 1 วิ เพื่อเริ่มเดิน]  ← fill ring;
│  • เดินตามดูได้ กดปุ่มแดงหยุดได้ทุกเมื่อ      │   release early = nothing. Then
│  [ ยกเลิก ]  [ กดค้าง 1 วิ เพื่อเริ่มเดิน ]   │   prompt("emma-nav:go:big_room:tap")
└──────────────────────────────────────────┘

┌──────────────────────────────────────────┐   ④ progress (replaces ②; the panel cannot be closed
│  กำลังไป                                  │      by a stray tap while a run is active)
│  ห้องประชุมใหญ่                            │   phase from state/do:
│                                          │    preparing  → "กำลังเก็บแขน…"
│  ███████████░░░░░░░░░  ขั้น 4 จาก 10       │    starting   → "กำลังตรวจก่อนออกเดิน…"
│  ผ่านประตู D8                             │    checkpoint → "ตรวจตำแหน่ง"
│  เวลา 1:01                               │    drive      → "กำลังเดิน"
│                                          │    door       → "ผ่านประตู {door}"
│                                          │    dock       → "กำลังถอยเข้าแท่นชาร์จ"
│ ─ ─ ─ ─ (page reserves bottom 14vh) ─ ─ ─│   step = step-step_from+1 of step_to-step_from+1
╞══════════════════════════════════════════╡
│█████████  ■ หยุดหุ่น  █████████████████████│   NATIVE SafetyBar (MainActivity view above the
│█████████  แตะเพื่อหยุดทันที █████████████████│   WebView; 12% of screen height, #B3263E, 34sp)
└──────────────────────────────────────────┘   shown whenever nav ∈ {preparing,starting,running},
                                                 a PC-started run is live (nav-poller), or a body
                                                 gesture is running/rest-returning. A STOP in
                                                 preparing/starting cancels the queued launch (§2b)
┌──────────────────────────────────────────┐   ⑤ result
│  ✓ ถึง ห้องประชุมใหญ่ แล้ว                  │   arrived : "ถึง {name_th} แล้ว"
│                                          │   stopped : "หุ่นหยุดแล้ว — {reason_th}"
│  [ ปิด ]                                  │             "หุ่นหยุดอยู่ที่ขั้น {k}: {phase} — ให้เจ้าหน้าที่ดูแล"
└──────────────────────────────────────────┘   refused : "ยังไปไม่ได้ — {reason_th}"
```

- The page has **no STOP button of its own**. The only STOP on screen is native, so it still works when the
  page, the WebView or the server is dead. The progress view reserves the bottom 14vh so that it never sits
  under the bar.
- Disabled-card reasons are the exact `NAV_REASONS` texts, with values filled in. For example, `low_battery`
  shows as "แบตเตอรี่เหลือ {n}% — ต้องมีอย่างน้อย {min}%".
- The overlay is `#emmaNav`, outside `<main>`, a sibling of `#emmaScan`, with the same Escape/focus rules. A
  desk browser (`?app=1` absent) never sees the slot.
- Body gestures have no UI of their own. The native SafetyBar appears during a gesture with the label
  "■ หยุดแขน" and the subtext "ทางหยุดที่แน่นอนคือสวิตช์หลัก".

---

## 4. ความปลอดภัย (Safety)

| hazard (from the critique) | what this design does |
|---|---|
| **No proven software stop for the arms** (`#STOP+OK` while ch5 kept moving, 11 Sep) | Gestures are short, small and slow: head +40 µs only (the one owner-measured pulse, `LIMITS` 1460–1540), first head move T3000, frames ≥ 800 ms, total ≤ 12 s. Unmapped ids are refused. `direction_known` gestures need an on-robot opt-in. The two-key default is off. Every stop path says the stop is *requested*, never achieved ("ห้ามพูดว่า 'แขนหยุดแล้ว'"). The bar's subtext names the main switch. The bench checklist (§6c) puts a person on the main switch and measures `stopAll` mid-move before `direction_known` is turned on. |
| **Two writers on the arm port** (the app holds CH340 after the first greeting; the server's adb `printf` is a second writer) | The app's SDK path is the single production path. `robot_arm`'s serial writes (`centre`, `step`, `run_group`, the serial greeting) raise `PermissionError("app_owns_arm_port")` when `ROBOT_BODY_ENABLED` **or** `ROBOT_NAV_ROUTES_ENABLED` is on, **or** `robot_face_link.nav_activity()` is `active`/`stale` (P1). This matters because the camera greeter goes `events.announce` → `robot_arm.greet` (events.py:203-206), which for a non-face session is the adb `run_group` (robot_arm.py:494), and this machine has every `ROBOT_ARM_*` flag on and an adb serial set: revision 1 would have let a greeting swing the arm mid-run whenever the body switch was off. `stop()` still writes `#STOP`, because a lock must never disable the brake, **and** now also sends `body_command stop` to the face session when there is one (the app's native `stopAll`, which `#STOP` over adb is not). The /arm stop endpoint calls `robot_arm.stop()`, so it gets both with no endpoint change. The /arm desk page shows the refusal reason. |
| **Two drivers on the wheels** (board MoveTo via `robot_chassis`, drive_route velocity, MainActivity joystick, the vendor app) | The fence is **in the primitives**, not in `send()`: `move_to`, `go_home`, `drive`, `nudge` and `undock` in `robot_chassis` each call one guard first (P2). `/robot/command` calls `drive`/`nudge`/`undock`/`go_home` directly (main.py:1063-1076), so a guard inside `send()` alone (robot_chassis.py:1214) missed most of the paths (safety review #3). The guard refuses with `routes_own_the_base` when `ROBOT_NAV_ROUTES_ENABLED=true` (`move_to`/`go_home`), and with `run_active` for **all five** whenever `nav_activity()` is `active` or `stale`, **whatever the switches say**. `drive_stop` and `cancel_navigation` are never fenced. A boot rule forces `ROBOT_NAV_PANEL` off unless routes are on (§2h). Before a launch, NavLauncher force-stops `com.aobo.robot.ai3` and calls `disconnectSdk()`. Opening the operator screen during a run **stops the run first** (reason `operator_screen`) before it connects the SDK. One `run.pid` covers both the app and `robot_python run`. Not covered: a PC `robot_python run` while no face page is connected (the server cannot see it). That is the operator at the PC, as today. **(review 2, 9 Oct)** The face bridge existed only during a call, so a panel-tap walk between calls also read `unknown`. With `ROBOT_NAV_ROUTES_ENABLED` on, `run_fence` treats `unknown` as running (`run_unknown`: open the face app so it reports, or use the red bar). The desk STOP and voice `stop_moving` still send the board DELETE, but when `leg_runner_possible()` (routes on, or a face has ever reported the launcher installed) they answer `ok:false`/`stop_unconfirmed` with the red-bar hint instead of "สั่งหยุดแล้ว". **Fixed later the same day by the status link (§10):** the face page reports between calls, so a tap walk or a PC run the app's poller sees is `active` (fences closed, routes on or off) and the desk STOP reaches the app through the link. `unknown` is what is left: no face page connected, its link silent, or the app's run.pid poll stale (`poll_stale`). A PC `robot_python run` with the robot's app closed is still invisible. |
| **Localization left OFF after a run** | drive_route keeps it off on purpose (the second leg needs the odometry pose). `robot_chassis` now also refuses `move_to`/`go_home` with `relocalize` when `localization_enabled()` is False **or None** (unreadable is not "on"), independently of the routes switch, because that board state is unsafe for MoveTo whoever caused it. **This changes this machine's default behaviour**: after any drive_route run, the robot-face `go_to_place` and `/mapview` goto stop working until someone relocalizes. The CHANGELOG entry says so in its first line, and `/mapview` (`client/robot-map.html`) shows "ระบบระบุตำแหน่งปิดอยู่ — กดตั้งตำแหน่งใหม่" with a button that posts the existing `/robot/command` `action=relocalize` (main.py:1030; the page has no relocalize control today), instead of a silent failure (P2). The sales room has no chassis motion enabled (`ROBOT_CHASSIS_MOTION_ENABLED` is checked first), so it is unaffected. |
| **Arms out before doors** (doors need `--arms-in`) | **(review 2, 9 Oct)** The launcher passes `--arms-in` only when the app's `go` argv carries `--arms-rested`, which the app appends only after `restArms` returned true. Without it a leg with doors is refused `arms_not_rest` before anything spawns (0.78 m does not fit D8/D11), and a leg without doors runs at the 0.78 m width. An old app that never sends the flag gets the safe answer. Before every launch the app plays `rest` (group 99) and waits for `#AGF` (≤ 15 s). No `#AGF` or no arm board means `arms_not_rest` and no run (`nav_require_arm_rest`, default true). Gestures are refused for the whole run (`navigating`). A body `latched` state refuses nav (`body_latched`). |
| **Customers reaching the menu** | The nav slot is `off` by default. In `pin` mode it needs the staff PIN, verified server-side with a lockout. Motion needs a 1 s hold on "เริ่มเดิน". The robot's `nav_enabled` is off by default and settable only on the 3-s-hold operator screen. |
| **Customers starting a walk by voice** (safety review #6: joke is the public showroom profile, registry.py:157-161, and "ไป" passes almost any sentence) | `ROBOT_NAV_VOICE=off` by default: voice can stop a walk but never start one. `unlocked` limits voice starts to the PIN-unlock TTL; `on` is the owner's explicit choice. Even then: `verified` legs only, and the fresh, single-use heard guard must name the place itself (§2d). |
| **A stop refused as busy** (safety review #1) | `preempt` in the registry and the stop exemption in `robot_face_link.request` (§2a, §2d), so a stop is never `busy`/`request_pending`. Natively, stops skip every gate and run off the `io` executor (§2b). |
| **A stop pressed while a launch is still queued** (safety review #2) | The nav cancel token and nav latch (§2b), checked after the rest and before `su`; the launcher reads stdin from its first line and aborts before spawning (§2e). Revert-red tests on both sides: a stop during `preparing` must prevent the `su` command; a `stop` line during preflight must prevent the spawn. |
| **A run the app did not start** (safety review #5: `robot_python run` shares `run.pid`) | `nav-poller` reads `emma_nav.py status` every 5 s. A live run of any origin refuses gestures and shows the SafetyBar; an unreadable status while installed refuses gestures (`nav_unknown`). |
| **A page pretending to be the robot face** (safety review #9) | Per-session state, and an arrival is announced only for a `run_id` issued to that session (§2a). An optional `ROBOT_FACE_TOKEN` hello closes the user-agent spoof; it is off until the owner sets it on both ends (§7). |
| **Stop/tether starved by the single `io` thread** (build review #4) | Dedicated NavLauncher and body-stop threads (§2b), with a source test. |
| **The model claiming motion that did not happen** | Results carry `hardware` = `requested`/`refused`/`failed`/`mock`, never "done". Instructions quote the wrong sentences. Arrival is a separate `events.announce` turn from a real `nav_status`. A gesture end is never announced. The P6 eval measures false "done" claims with hardware stubbed. |
| **Bridge privilege creep** (a remote page driving the body) | The grammar is closed (§2b), with ids validated twice (shape in EmmaFace, membership in MainActivity). The trust gate is the same as today's. Motion messages need foreground + focus + !operator. Native allow-lists and clamps apply whatever the page says. No `addJavascriptInterface`. |
| **App or launcher dies mid-run** | The su pipe hits EOF or pings stop for 4 s → the launcher SIGINTs drive_route → `board.cancel()`. If the launcher dies, `--watch-parent` → SIGINT. If the controller hangs, the native STOP escalates (below). If a force-stop kills the su children with the app's cgroup (SIGKILL, so no `board.cancel()`), the velocity watchdog stops the wheels in ~1 s, but a board action (GoHome, MoveBy) would continue. So every app start runs `emma_nav.py reap` (§2e), which DELETEs `/actions/:current` when `run.pid` is dead. Whether su children survive a force-stop at all is measured at bench C4. |
| **STOP itself causing a retry** | `DELETE /actions/:current` alone looks like a link drop to drive_route, which then **redoes the step** (`recover_link`, up to 180 s). So the native STOP order is: (0) cancel token + nav latch, synchronously (a launch still queued never starts); (1) tether `stop`; (2) `su … emma_nav.py stop` (SIGINT via `run.pid`); (3) after 2 s, if still alive, `kill -9` the controller (the board's velocity watchdog stops the wheels in ~1 s); (4) then `DELETE /actions/:current`. B, `onPause`, focus loss and `onDestroy` take the same path, all on `nav-stopper`, never on `io`. |
| **A robot stopped in a doorway** | Nothing moves it automatically. `here.json` is deleted when the first step starts (not at spawn: a refused preflight keeps the location, §2e), so the location is unknown and every card is disabled with "ให้เจ้าหน้าที่พาหุ่นกลับแท่นชาร์จก่อน". Recovery is a staff job (the `*_to_dock` fragments from the PC), the same as today. |
| **Docking fix only simulated** (8 Oct tail strike) | The leg `big_room → dock` ships `verified:false` and cannot be started by voice. The first runs are supervised (§6c), and only after a redeploy of reviewed code: `emma_nav check` compares `MANIFEST.json` with the files, and the operator screen shows `deployed_at`. |
| **Hallucinated or echoed commands** | The heard guard: a body or nav tool call asks instead of acting unless a fresh (after the latest `activity_start`, ≤ 10 s), unused caption of this session holds the action's verb (and side) or the place's own name. Local VAD, the silence floors and `HALF_DUPLEX` are not weakened (CLAUDE.md, measured). |
| **Voice "หยุด" not heard while Emma talks** (half-duplex) | Voice stop is best-effort by design. The stops that matter are the native bar, gamepad B, the physical E-stop and the main switch. The nav instruction asks for short speech during a run. That shortens the deaf windows, which is reasoning and not a measurement, and P6 measures it in the eval. |
| **Sales-room drift** | New groups are opt-in, the routes switch is off, nothing appears in prompts or ASR phrases when off, and snapshot tests cover all of it (§2h). Runtime timing too: `TURN_IDLE_TRACKS_TOOLS=false` and `ANNOUNCE_SETTLE_S=0` by default, with a condo runtime test (safety review #8). |
| **The turn-idle gate sticking** (safety review #8, build review #8: gemini.py:773-794 yields no `tool_result` for cancelled ids or after `_closing`/a stale session, so a counter would stay above 0 and every announcement and nudge would wait out `TURN_WAIT_S` for good — CLAUDE.md's "stop nudging, nothing wakes" bug) | When the switch is on: in-flight calls are tracked **by call id** with their start time, not as a counter. gemini.py (P0) puts the id in `tool_call`/`tool_result` events and yields a `tool_dropped` event for every id it skips. The set is cleared on `resumed`, on reconnect and on session end, and any entry older than `TOOL_TIMEOUT_S + 2` is discarded (ceiling). Tests cover each clearing path with a revert-red. |

---

## 5. Work packages and file ownership

The rule is **disjoint file sets**. P0 runs first and alone. P1–P6 run in parallel after P0 merges. P7
integrates. If a package needs a change in a file it does not own, it writes the request into its report and
the owner of that file makes the change in P7. No package edits `CHANGELOG.md`. Each one returns its entry text
(Thai: what changed, why, real test results, "ยังไม่ได้ทดสอบบนหุ่น") and P7 writes them, so that there is one
writer.

**Mutation rule (every package).** For each new guard, revert the guard once and show that the test goes red,
then restore it. Record the revert-red evidence in the report. Assert the condition and not a side effect. A
test asserting "nothing was sent" registers `session._active` (`_lone_session()`). A `pytest.skip` says what is
then unchecked. Subprocesses carrying Thai use `encoding="utf-8"`.

### P0 — contracts and shared plumbing (runs first)

- **Files:**
  - new: `app/robot_contracts.py`, `app/robot_face_link.py`, `app/robot_places.py`
  - edits: `app/config.py`, `.env.example`, `app/tools/__init__.py` (add `"app.tools.body": "body"`, add `body` to
    `_OPT_IN`), `app/tools/registry.py` (`allowed`, `preempt`), `app/session.py`, `app/events.py`,
    `app/heard.py` (per-session captions with time, `mark_activity`, `caption_for`; the existing `record`/`last`
    API keeps working for the slide tools), `app/providers/gemini.py` (**event plumbing only**: call ids on
    `tool_call`/`tool_result`, `tool_dropped`, `heard.mark_activity` at the `activity_start` send, line 690),
    `tests/conftest.py`
  - P0 runs alone and merges before P6 starts, so P6's later `adaptation_phrases` edit in gemini.py is
    sequential, not parallel. That is the one file two packages touch, and they do not touch it at the same time.
  - new tests: `tests/test_robot_contracts.py`, `tests/test_robot_face_link.py`, `tests/test_robot_places.py`,
    `tests/test_body_nav_gating.py`, `tests/test_turn_idle_tools.py`, `tests/test_heard_fresh.py`
- **registry.allowed():**
  - group `body` → requires `robot_face_session.get() is not None and settings.robot_body_enabled`.
  - `request_handshake` → hidden when a `move_body` entry exists and is allowed.
- **registry `preempt`** (§2d): the kwarg, its skip of the in-flight check, and no `_inflight` entry for a
  preempting call. The `stop_moving` decorator change is P2's (robot.py); P0 adds only the mechanism.
- **session.py:**
  1. Route `INBOUND_TYPES` and `face_hello` to `robot_face_link.receive(self, event)`. This is one branch, placed
     before `robot_ready`. When `ROBOT_FACE_TOKEN` is set, `robot_face_session` is set only after a matching
     hello.
  2. Only when `TURN_IDLE_TRACKS_TOOLS=true`: `tool_call` (with id) → add to `self._tools_in_flight` (id →
     start time) and `turn_idle.clear()`; `tool_result`/`tool_dropped` → remove; `resumed`, reconnect and
     session end → clear; entries older than `TOOL_TIMEOUT_S + 2` → discarded. `turn_complete` sets `turn_idle`
     only when the set is empty (gemini-3.8 sends `turn_complete` before the tool call in 13/15 cases:
     `docs/emma-live-qa-eval-2026-10-08.md`). With the switch off, today's code path runs unchanged.
  3. The `finally` of `handle_connection` calls `robot_face_link.session_closed(session)`.
- **events.announce:** after `turn_idle` is set, sleep `ANNOUNCE_SETTLE_S` (default 0 = no sleep, no extra
  check) and re-check. If idle was cleared again (a late tool call), wait again, still inside the existing
  `TURN_WAIT_S` bound.
- **Tests:**
  - ack routing by `command_id`; a wrong session or a wrong id is ignored; timeout; `session_closed` →
    `disconnected`; oversize and bad-id drops; one outstanding non-stop request per kind (`request_pending`);
    **a stop is sent while a request is pending** (revert-red); `nav_activity` for each state, stale by clock;
    `issued_run` false for another session's run id; `face_hello` gating.
  - registry: `preempt` lets a stop through while the same exclusive tool is in flight (revert-red), and a
    preempting call does not block a later call.
  - heard: fresh/stale around `mark_activity`; age bound; per-session isolation; `last()` unchanged for slides
    (`tests/test_heard.py` passes untouched).
  - turn idle: the id set clears on `tool_dropped`, `resumed`, reconnect and the ceiling (each revert-red); with
    the switch off, the condo runtime ordering test shows today's timing.
  - config: `ROBOT_NAV_PANEL=pin` with routes off → forced `off` and logged (the rule lives in `app/config.py`).
  - visibility matrix (face × switch × `TOOL_GROUPS` × profile × `MULTI_SESSION`).
  - handshake hidden only when `move_body` is visible.
  - **condo default tool list, descriptions and prompt are byte-identical** to a snapshot taken before P0.
  - turn ordering A/B/C (`turn_complete` before, during and after the tool) leaves `turn_idle` correct, and
    announce does not fire inside the gap.
  - places parser: valid file; each invalid field named; a leg ending on a drive step rejected; overlapping legs
    rejected; `match` uses whole words (`ห้องประชุมใหญ่` vs `ห้องประชุม`, and `ราคา` must not match `อาคาร`).
- **Must NOT change:** existing tool descriptions, prompts, `robot_link`, `robot_arm`, the greeting flow,
  `handle_connection` takeover semantics, gemini.py's config, `adaptation_phrases` and reconnect logic.
- **Accept when:** the P0 tests pass, the existing robot/tools/profile/voice/session/events files are green
  except the already-red prompt-length test, and revert-red is shown for each gate.

### P1 — server body control

- **Files:**
  - new: `app/robot_body.py`, `app/tools/body.py`, `tests/test_robot_body.py`
  - edit: `app/robot_arm.py` (serial refusal when `ROBOT_BODY_ENABLED` or `ROBOT_NAV_ROUTES_ENABLED` or
    `nav_activity()` ∈ {active, stale}; `stop()` also sends `body_command stop` to the face session),
    new `tests/test_robot_arm_lock.py` (no `tests/test_robot_arm.py` exists; `test_robot_arm_page.py` and
    `test_events.py` must pass untouched)
- **Contents:** `robot_body.request(action)` (§2d steps), the heard guard (`BODY_HEARD`, fresh + single-use),
  caps cache per session, handlers for `body_status`/`body_caps` via `robot_face_link.on`, `STATE`,
  `reset_state`, turnlog lines `body`/`body_refused`.
- **Tests:**
  - parameters are exactly `{action: enum}`, and the enum equals `BODY_ACTIONS` ids.
  - the description has the phrasing examples and the "อย่าเรียกเพียงเพราะมีคนถาม" guard.
  - hidden without the face or the switch; `server_disabled` → `mock` for each interlock flag; groups flag only
    for group gestures; `stop` bypasses the gate and the heard guard.
  - heard guard on and off: the phrase table of §2d step 4 (ลดราคา, ข้างหน้า, ยกเลิก, มือถือ fail; verb/side
    mismatch fails; stale and reused captions fail).
  - cached `needs_mapping` refuses without sending; `head_up`/`shake_head`/`head_left`/`head_right` are
    `needs_mapping` in the contract fixture.
  - accepted → `requested` + an instruction containing "ห้ามพูดว่า 'ทำเสร็จแล้ว'"; refused → the Thai reason +
    `available`; timeout → `failed` + "ห้ามสั่งซ้ำ".
  - `exclusive` busy for a second gesture; **`stop` never busy** (revert-red against `preempt`).
  - `body_status` updates `STATE` and announces nothing (patch `events.announce` and assert it was not called, with `_active` registered).
  - `robot_arm`: under each of the three conditions, `run_group`/`centre`/`step`/the serial greeting raise
    `app_owns_arm_port`, and the camera-greeter path (`events.announce(arm_greeting=True)` for a non-face
    session) does not write serial; `stop()` still writes `#STOP` and also sends `body_command stop`.
- **Must NOT change:** `request_handshake`'s code, `robot_arm.greet` native path semantics, the /arm endpoints' shape.

### P2 — server navigation routing

- **Files:**
  - new: `app/robot_nav.py`, `tests/test_robot_nav_voice.py`, `tests/test_robot_chassis_guards.py`
  - edits: `app/tools/robot.py` (`stop_moving` gets `preempt=lambda a: True`; declarations otherwise
    byte-identical), `app/robot_chassis.py`, `app/main.py` (`POST /robot-nav/config`, `POST /robot-nav/unlock`;
    token in the body; lockout), `client/robot-map.html` (relocalize
    notice + button), `tests/test_robot_map_page.py` (add cases only)
  - may edit `app/tools/robot_link.py` if needed (owned by P2)
- **Contents:** routes-mode routing for `go_to_place`/`return_to_base`/`stop_moving`/`get_robot_status` (§2d),
  including `ROBOT_NAV_VOICE` (off/unlocked/on, the unlock TTL shared with the PIN endpoint) and the place-name
  heard guard; `NAV_STATE` from `nav_status`/`nav_caps` per session; announce for voice-origin terminal states
  only when `issued_run`; the stale watchdog; the PIN check with `secrets.compare_digest` and a per-client
  lockout; the chassis guards **in the five primitives** (routes switch, `nav_activity`, localization False or
  None).
- **Tests:**
  - routes mode only on the face with the switch; the condo/desk path is unchanged (existing `tests/test_robot.py`
    passes untouched).
  - whole-word place match; `leg_unverified` for voice; `voice_disabled` with the default; `unlocked` inside
    and outside the TTL; the heard guard needs the place name ("ไปไหนมา" refused, "พาไปห้องประชุมใหญ่" passes,
    "กลับบ้าน" refused for the dock).
  - ack accepted/refused/timeout.
  - arrival announced once through `events.announce` (with `_active`), not for tap-origin runs, and **not for a
    run id issued to another session** (revert-red against `issued_run`).
  - a stale run → one announcement and no stop sent.
  - `stop_moving` sends both a nav stop and a body stop, **also while a `go` ack is pending** (revert-red).
  - chassis: each of `move_to`, `go_home`, `drive`, `nudge`, `undock` refuses while `nav_activity()` is
    active/stale with **both switches off** (revert-red per primitive); `move_to`/`go_home` refuse under the routes
    switch and on localization False and None; `drive_stop` and `cancel_navigation` always pass; a
    `/robot/command drive` during an active run is refused.
  - PIN: wrong, lockout, empty-PIN-in-pin-mode, token required.
  - `/robot-nav/config` never echoes the PIN.
- **Must NOT change:** tool names, tool descriptions and parameters of the existing robot tools (byte-identical
  declarations); `robot_link.find_place` semantics.

### P3 — on-robot launcher, drive_route legs, deploy

- **Files:**
  - new: `scripts/emma_nav.py`, `data/robot-places/floor2.json` (`route_sha256` computed from the current
    route file), `tests/test_emma_nav_launcher.py`, `tests/test_drive_route_legs.py`
  - edits: `scripts/drive_route.py`, `scripts/robot_python.py`, `scripts/analyze_run.py` (`leg_done` counts as
    done), `tests/test_robot_python.py`, `tests/test_analyze_run.py` (add cases only)
- **Tests:**
  - where-am-I truth table (on_dock; a valid `here.json`; stale by newer log; pose moved; heading off).
  - leg lookup and every refusal code; `run_id` must match `RUN_ID_RE` or the launcher refuses `bad_request`.
  - exit-code → state map: `0`+`leg_done` and `0`+`route_done` → `arrived`; `3` keeps `here.json` and rewrites
    its `run_log` (revert-red: without the rewrite the next `go` reports `location_unknown`); `here.json` survives
    until the first `step` status.
  - status merge; every printed line is flushed (a pipe-reading test sees each line before the process exits).
  - tether: ping keeps it alive, 4 s of silence → interrupt (`app_lost`), EOF → interrupt, `stop` →
    `stop_pressed`; **a `stop` line during steps 1–6 → no spawn at all** (revert-red); uses the fake `Child`
    and the fake clock.
  - `reap`: orphan of a dead launcher → interrupt; PC run left alone; dead `run.pid` + a board action → DELETE.
  - `--to-step`:
    - the loop ends at N and logs `leg_done`, **including N == len(steps)**.
    - `door_exit_plan`, on a **synthetic** door → checkpoint → drive fixture: first assert the plan is
      non-`None` with no `--to-step`, then assert it is `None` when `to_step` is that checkpoint (on the real
      route, `door_exit_plan(9)` is already `None` because step 11 is a door, so a test there cannot go red).
    - **on the real `floor2_bigroom_roundtrip.json` step list (copied into a fixture), legs 1–10 and 10–20 never
      run a step outside their range**: a fake Runner records the visited indices.
    - `--to-step` on a drive step → exit 3.
    - `--state-out` then `--state-in` round-trips `measured_at` and `door_seen`, and `_door_remembered` returns
      the same value after the round trip as in one process (revert-red: without `--state-in` it returns `None`).
  - status file is atomic (tmp + replace) and contains the right states.
  - preflight Stop → `kind="refused"` and exit 3 with its code; KeyboardInterrupt → `kind="interrupted"`.
  - the AST scan for `raise Stop(` sites without a `code` (§2g); the rewrapped sites carry `link_lost` /
    the original code.
  - `--watch-parent` triggers SIGINT through an injected `getppid`.
  - `CODE` vs imports includes `emma_nav`; `deploy` writes `MANIFEST.json`.
- **Must NOT change:** any existing drive_route gate, default, constant or behaviour when `--to-step`,
  `--status-file` and `--watch-parent` are absent (existing `test_robot_drive.py`, `test_robot_lanes.py`,
  `test_robot_door.py` pass untouched); route JSON files; `robot_python install`.

### P4 — Android app

- **Files:**
  - all of `apps/emma-robot-android/**`: `EmmaFace.java`, `MainActivity.java`, `ArmController.java`
    (`playFrames(frames, Runnable onDone)` overload), `MotionDraft.java`; new `BodyGestures.java`,
    `BodyController.java`, `BodyPoseStore.java`, `NavLauncher.java`; `README.md`
  - tests: `tests/test_emma_robot_app.py`, `tests/test_emma_robot_android_app.py`, new
    `tests/test_emma_robot_body_nav_app.py`
  - `tests/test_emma_robot_app.py` also asserts strings in `client/voice-preview.html` (around line 619,
    the native-dialog listeners). P4 must not edit those asserts, and P5 must keep those page strings
    byte-for-byte. If either side needs them changed, P7 does it.
- **Contents:**
  - §2b bridge: `EmmaFace.Bridge` interface implemented by MainActivity; `trustedPreview` helper.
  - §2c table and clamps.
  - `requestArmGreeting` routed through `BodyController` (keeping its gates).
  - NavLauncher: su tether (`python -u … go <id> --origin … --run-id <hex32> --tether`), ping every 1000 ms,
    status parsing, the stop escalation, the cancel token + nav latch, `places` cache refreshed in the background
    at most every 10 s, `check`, `reap` on app start, `nav-poller` every 5 s; all on its own threads (§2b).
  - `run_id` generation (`SecureRandom`, 32 hex) before the `go` prompt returns.
  - `emma-face:token` and the `face_token` operator field.
  - SafetyBar.
  - B / `onPause` / focus loss / `onDestroy` / operator-screen open → nav stop + body stop.
  - Rest before a run.
  - Operator screen additions: switches (`body_enabled`, `allow_direction_known`, `nav_enabled`,
    `nav_require_arm_rest`), voice channel allow-list toggles with `channelEvidence`, "บันทึกร่างนี้เป็นท่า…",
    "ล้างการล็อกท่าทาง", "ตรวจระบบนำทาง (ไม่ขยับ)" (runs `emma_nav.py check`, shows its JSON and `deployed_at`).
- **Tests (source text):**
  - every bridge string; the shape regex; the trust gate on all `emma-*`; motion gates present, and stop/status
    without them.
  - EmmaFace forbidden words extended (`BodyController`, `NavLauncher`, `/system/xbin/su`, `emma_nav`).
  - Java gesture ids == `robot_contracts.BODY_ACTIONS` ids, and the statuses equal §2c.
  - clamp constants equal §2c.
  - NavLauncher builds its command only from constants plus a validated id, origin and `run_id` (hex32), and
    never composes `--from-step`/`--to-step` (the launcher owns legs).
  - STOP escalation order (cancel token + latch → tether stop → `emma_nav stop` → `kill -9` after 2000 ms →
    DELETE); the cancel token is checked after the rest and immediately before the `su` exec.
  - the dedicated threads exist and never submit to `io`; gesture tasks re-check the latch and nav state.
  - `head_down`/`nod` use only 1540/1500 on ch8; `LIMITS` 7/8 are 1460–1540; `head_up`, `shake_head`,
    `head_left`, `head_right` are `needs_mapping` (a test scans `BodyGestures.java` for any ch7/ch8 pulse
    outside 1460–1540).
  - every stop path calls both nav and body stop.
  - SafetyBar is a MainActivity view and not in EmmaFace.
  - rest-before-run waits for `AGF`.
  - Existing tests updated only where the greeting now goes through `BodyController`; their intent is preserved.
- **Plus:** an **APK compile** (§6b).
- **Must NOT change:** the operator-screen staff gestures, token/origin handling, the camera and mic grant rules,
  the release HTTPS rules, the manifest permissions (no new permission is needed).

### P5 — face page (`client/voice-preview.html`)

- **Files:** `client/voice-preview.html`, `tests/test_voice_preview_page.py` (the existing page test; edit only
  where an assert names text that legitimately changes), new `tests/test_face_body_nav_page.py`, new
  `tests/face_nav_model.cjs` (node harness for a pure `navModel(caps, status)` → cards/disabled/phase function
  extracted in the page).
- **Contents:**
  - slot 2 `นำทาง` + `#emmaNav` overlay (§3), PIN via `/robot-nav/unlock`.
  - relays: `body_command` → prompt → `body_ack`; `nav_command` → prompt → `nav_ack`.
  - on `ready` (in-app): `emma-body:list` → `body_caps`, `emma-nav:places` → `nav_caps`.
  - native `emma-*-changed` → pull status → `body_status`/`nav_status` (dedupe by `seq`/state+step, plus every 5 s while running).
  - on `ready` (in-app): `emma-face:token` → `face_hello` when non-empty.
  - progress view also for a PC-started run (`origin:"pc"`), read-only, with the `pc_run` text.
  - a non-app page answers `not_robot_face`.
- **Tests:**
  - slot 2 only when `IN_APP` and mode ≠ off.
  - only the exact prompt strings of §2b are used; ids pass through only after `ID_RE`.
  - no transcript or user text ever goes to a prompt.
  - every Thai string of §3 present.
  - the progress view reserves 14vh and has no stop button of its own.
  - the hold-to-start is 1000 ms.
  - the token goes in the body and never in a URL.
  - node model cases (each reason → disabled text; phase mapping; step k of n). If node is missing, the skip
    reason says "navModel not executed — disabled/phase mapping unchecked".
- **Must NOT change:** the face scan, slot 1, the mascot hold (`HOLD_MS`), the half-duplex/hold-speech logic,
  the `arm_greeting` handling, and the page strings that `tests/test_emma_robot_app.py` asserts (P4's file).

### P6 — voice tuning (evidence-backed only)

- **Files:** `app/prompts.py`, `app/providers/gemini.py` (`adaptation_phrases` only), `scripts/eval_live_qa.py`,
  new `data/eval/qa_live_robot.json`, `CLAUDE.md`, new `tests/test_robot_voice_tuning.py`, and edits to
  `tests/test_voice.py` / `tests/test_profiles.py` only if an existing assert names text that legitimately changes.
- **Contents:**
  1. `_robot_face_block()`: when `move_body` is visible, one line listing what the body can do *now*, taken
     from the tool, not from the prompt ("ท่าที่ทำได้ดูจากผลเครื่องมือ move_body"). It replaces the handshake-only
     line, so nothing contradicts. When routes are on, a line saying navigation goes only to places in
     `get_robot_status`. When the switches are off, the text is unchanged.
  2. Emma rule 8 "เดินไม่ได้" and the condo robot-face scope line are rewritten **only** when the robot face +
     routes are on.
  3. `adaptation_phrases()`: when `ROBOT_BODY_ENABLED` is on, add the body words of §2d; when
     `ROBOT_NAV_ROUTES_ENABLED` is on, add place names and aliases from the places file plus "หยุด", "พาไป",
     "กลับแท่นชาร์จ". When either is on and the profile is `joke`, **drop the deck's facility names** (a known
     issue, CHANGELOG ~309). The total stays ≤ 100, enforced by a test. **Honest scope:** these bias the
     *captions* only, since the model hears raw audio. Their value is the heard guard and the logs.
  4. eval: a `--suite robot` and `--profile joke|emma` in the harness; `make_safe` stubs
     `robot_face_link.request` to scripted acks (accepted / refused needs_mapping / timeout) and records the
     calls; scenarios for each action phrasing, the question-not-command cases ("ยกแขนได้ไหม", "หุ่นมีแขนไหม"),
     left/right, ambiguous place, unknown place, stop while gesturing/walking, and "claimed done" checks. The
     harness also writes the **gap from `turn_complete` to the tool call** per turn. Its p95 is *proposed* as
     `ANNOUNCE_SETTLE_S`, and the report says whether `TURN_IDLE_TRACKS_TOOLS=true` should be turned on for the
     robot face and, separately, for the sales room. Both stay at their off defaults until the owner sets them.
     Robot scenarios also cover the heard-guard phrases of §2d ("ลดราคา", "ไปไหนมา", "กลับบ้าน") and
     `voice_disabled` (the model must say that staff start walks from the screen, not claim it is going).
  5. CLAUDE.md:
     - fix "Gemini 3.1 ไม่รองรับ…" → the truth on `dev`: `gemini-3.8-live` default (code + CHANGELOG 21 Sep),
       NON_BLOCKING supported but unused, `thinking_config` rejected by 3.8 (only `-extended-thinking` takes a
       level), early `turn_complete` 13/15.
     - add a short "ร่างกาย/นำทาง" section of rules from this design.
- **Not done, on purpose (measured and rejected in CLAUDE.md):** more wake spellings, lowering `SILENCE_RMS`,
  raising `VAD_FOCUS_RATIO`, `ECHO_BARGE`, switching to 3.1, the greeting on reconnect. Switching to
  `-extended-thinking` is not done either: there is no latency measurement. P6 *measures* it with the eval
  harness (`--model`, n ≥ 30 robot cases, at most 3 parallel, not while the sales room is open) and reports;
  changing the model is the owner's decision.
- **Tests:**
  - prompts byte-identical with the switches off (all profiles).
  - the robot-face block lines appear only when the tools are visible.
  - no prompt names an invisible tool.
  - phrases unchanged when off, added when on, ≤ 100, facility names absent for joke when on.
  - the eval scenarios file validates and the stub records calls.
  - `test_system_instruction_stays_short` is not made worse (it measures condo without the face).

### P7 — integration (after P1–P6)

- **Files:** `CHANGELOG.md` (one entry gathering every package's text), plus any file whose owner P7 coordinates
  a cross-package fix with.
- **Runs:**
  - full `pytest`, file by file (`.venv\Scripts\python.exe -m pytest tests\<file>.py -q -p no:cacheprovider`).
  - the cross-contract tests (Java ids = Python ids = HTML ids; launcher reasons ⊂ `NAV_REASONS`).
  - the APK build.
  - a `git diff --stat` review for files outside the declared sets.
- **Accept when:** all green except the known prompt-length test, the APK builds, and the defaults are byte-identical.

---

## 6. แผนทดสอบ (Test plan)

### 6a. Offline (every package, before P7)

Per §5. In summary:

- unit tests per module; source-text tests for Java/HTML with cross-language id checks
- byte-identical snapshot tests for the defaults
- drive_route leg tests on the real step list
- tether and where-am-I tests with fakes
- revert-red evidence for every guard

### 6b. Android compile (no install — the robot is off)

```powershell
$env:JAVA_HOME = "$PWD\.tmp\android-toolchain\jdk\jdk-17.0.20.1+1"
cd apps\emma-robot-android
.\gradlew.bat --no-daemon assembleDebug
```

The build must succeed with no new warnings in the touched classes. Record the APK path and size. **Do not
run `adb install`.** `local.properties` already points `sdk.dir` at the repo-local SDK.

### 6c. On the robot later — bench checklist for the owner

Every motion step needs a person with a hand on the **main switch**, and the physical E-stop within reach.

**A. Setup (no motion)**

1. `python scripts/robot_python.py deploy`. This deploys the *reviewed* code: CHANGELOG says the robot still
   runs the pre-review controller.
2. `adb install -r -g` the new APK, then open the operator screen and press "ตรวจระบบนำทาง (ไม่ขยับ)".
   - Expected: JSON with `installed:true`, matching MANIFEST hashes, `places_sha256`, battery, docking, `here`.
   - **This is the first proof that the app's uid can run the glibc Python through `su`** (critique b4). If it
     fails, record the error. Nothing else here can proceed.
3. Reboot the robot, then repeat step 2. This proves that `/data/local/tmp/emma-nav` survives a reboot.

**B. Body mapping session (camera on the robot, slow first moves)**

1. Turn on `body_enabled` on the robot only. Leave `allow_direction_known` off. By voice or by the page: `rest`,
   then `handshake`, then `greet_arm`.
   - Expected: `#AGF` each time, a rest-return after, `body_status done`.
   - Record whether group 99 itself answers `#AGF`, because §4's rest-before-run depends on it.
2. Turn on `allow_direction_known`. Run `head_center`; the first one is the T5000 prefix. Then `head_down` and
   `nod` (+40 only, the owner's 30 Sep value). Then, **in the pose studio, not by voice**, step the untested
   minus sides by −20 then −40 at T5000 (ch8 for `head_up`, ch7 for `shake_head`/`head_left`/`head_right`),
   with a hand on the main switch: ch8 is the servo that stalled and burned on 12 Sep. Record direction, any
   strain or noise, and the pulse. A pose that passes is saved with "บันทึกร่างนี้เป็นท่า…" (it cannot leave
   1460–1540). Going beyond ±40 needs a written measurement and an APK with wider `LIMITS` (§2c); it is not a
   bench-day toggle.
3. Stop test: start `head_down` with the prefix at T5000, then press the SafetyBar mid-move and record whether
   the head stops (`stopAll`). The result goes to `docs/robot-estop-evidence-*.md`. Until it is proven, keep
   §4's "requested, not stopped" wording.
4. Head left/right: centre the head, step ch7 + in the pose studio, and decide which way is **the robot's own**
   left. Record `head_left`/`head_right` with "บันทึกร่างนี้เป็นท่า…".
5. Arms and hands: with the studio, look for the channels that lift each arm and move each hand. First move
   T5000, ±40 steps. Add each confirmed channel to the voice allow-list *only after* camera evidence (update
   `servo-channel-map.json`). Record `raise_left_arm`, `raise_right_arm`, `wave`, `hands_left`, `hands_right`.
   Anything not found stays `needs_mapping`.

**C. First navigation from the app** (D8 and D11 open, a person at each door, arms checked visibly at rest)

1. With the robot on the dock, turn on `nav_enabled` (robot) and `ROBOT_NAV_PANEL=pin` + `ROBOT_NAV_PIN`
   (server). Open นำทาง and check that the cards and the reasons are right. Then **ห้องประชุมใหญ่ → hold 1 s**.
   Watch the arms rest → starting → running, and the progress steps 1–10, which must end at "inside the big
   room" without starting D11 out. The result should be `arrived` and `here = big_room`.
2. STOP test on a straight lane (east along the corridor, on a separate run): press the SafetyBar. Expected:
   the robot stops within ~1 s, `stopped/stop_pressed`, `here` unknown, and all cards disabled with
   "ให้เจ้าหน้าที่พาหุ่นกลับแท่นชาร์จก่อน". Recover from the PC as today.
3. From the big room: **แท่นชาร์จ** (steps 10–20: it re-measures inside the big room, then D11 out with the
   door memory carried over from leg 1, then the own back-in). Check in `run.out` that D11 out used the
   remembered offset (no "never measured" guess line). Watch the column beside the charger, which is
   the 8 Oct tail-strike spot. Expected: `arrived` with `on_dock` charging.
4. Kill test: during a run, force-stop the app with `adb shell am force-stop`. The robot must stop within ~5 s
   (tether, `app_lost`). Record whether drive_route survived the force-stop (`ps` for its pid). If it was killed
   with the app, check that the next app start's `reap` found no board action left running.
4a. STOP while preparing: tap a place and press the SafetyBar while the arms are resting. Expected:
   `stopped/stop_pressed`, **no** `su` launch in logcat, the robot never moves, the nav latch shown.
4b. PC run: start `robot_python run` from the PC with the app open. Expected: SafetyBar "(สั่งจากคอมพิวเตอร์)",
   gestures refused `navigating`, and pressing the bar stops the PC run.
5. After **two clean runs per leg**, set `verified:true` in `data/robot-places/floor2.json`, fill `est_s` from
   the summaries, then `deploy`.

**D. Voice** (server switches on, robot face session, joke profile as configured)

1. Say each body phrasing of §2d. Check the turnlog `body` lines and that Emma never says "เสร็จแล้ว" before
   anything moves. Check "ยกแขนได้ไหม" does **not** move the arm.
2. Set `ROBOT_NAV_VOICE=unlocked` (or `on`, the owner's choice). Say "พาไปห้องประชุมใหญ่" (verified leg) and
   "กลับแท่นชาร์จ". With the default `off`, check that Emma says staff start walks from the screen. Check that the arrival is announced once and that
   "หยุด" during a walk stops it (best-effort; record whether it was heard while she was talking).
3. Run `python scripts/eval_live_qa.py --suite robot --profile joke --concurrency 1` and keep the report.

---

## 7. คำถามที่เจ้าของต้องตัดสิน (Open decisions — the default taken)

| # | question | default in this design |
|---|---|---|
| 1 | Which gestures first? | handshake, greet_arm (group 3) and rest work now. `head_down`, `nod` and `head_center` use only the measured +40 and stay off until `allow_direction_known` (bench B2). `head_up`, `shake_head`, `head_left`/`head_right` are `needs_mapping` (minus sides never driven). The arms and hands wait for bench B5. |
| 2 | Who may command the body by voice? | Only the robot's own face session, with group `body` named in `TOOL_GROUPS`, `ROBOT_BODY_ENABLED`, the `ROBOT_ARM_*` interlock, and the robot's `body_enabled`. Every profile that is given the group (joke on this machine). |
| 3 | Spoken confirmation before moving? | None (rule 8 / owner paused confirmations). The heard guard asks only when no command word was heard. |
| 4 | Who sees the nav menu? | `off` by default; when on, `pin` (staff PIN) plus a 1 s hold to start. `open` (no PIN) is available if the owner wants it. The panel cannot be on while routes are off. |
| 4a | May voice start a walk? | `ROBOT_NAV_VOICE=off`: voice can stop a walk, never start one. `unlocked` = only inside a staff PIN unlock; `on` = anyone the robot face hears, on verified legs. |
| 5 | One navigation system? | Yes, while routes are on: the planner path refuses moves on that machine. The sales room (routes off) is unchanged. |
| 6 | The app starts the controller with no PC? | Yes, for runs. Install and deploy stay a PC step. |
| 7 | Arms during navigation? | Always rest (group 99) and confirm `#AGF` before starting; no gestures during a run. |
| 8 | Restore board localization after a run that ends on the dock? | No (keep the controller's behaviour). The planner path is fenced instead. |
| 9 | Unverified legs | Tap only, with a warning badge. Voice only after `verified:true`. |
| 10 | Body stop latch | 60 s, or until cleared on the operator screen. |
| 11 | Switch the voice model (3.8 → 3.8-extended-thinking)? | No. Measure first (P6), then the owner decides. |
| 12 | Still open from the dock work | Does "left" mean facing the dock? Is the dock square to the wall? Not needed for this design; they affect the dock leg's verification. |
| 13 | Usage/cost | P6 reports Gemini Live usage per robot eval run (token counts from the `usage` events). No price is quoted for 3.8 because none was found. |
| 14 | Require a face token (`ROBOT_FACE_TOKEN` + the robot's `face_token`)? | Off (empty), as today: the user agent decides. Recommended before `ROBOT_NAV_VOICE=on` or any use outside the office LAN. |
| 15 | Turn on `TURN_IDLE_TRACKS_TOOLS` / a non-zero `ANNOUNCE_SETTLE_S`? | Off / 0 everywhere. P6 measures the gemini-3.8 gap and recommends values for the robot face and, separately, for the sales room. |
| 16 | Localization guard on MoveTo/GoHome (refuses after any drive_route run until relocalized) | On, on machines with chassis motion enabled, because MoveTo with localization off is unsafe whoever caused it. This changes this machine's `/mapview` goto and robot-face planner `go_to_place` after a scripted run; the alternative is to have drive_route restore localization at the end of a leg that ends on the dock (decision 8). |
| 17 | Widen head limits beyond ±40? | No. Only with a written measurement and an APK build. |

**Nothing above has run on the robot.** The first real evidence is bench step A2.

---

## 8. Review round 2 — what changed, and what was rejected

Two reviews (safety: 10 points; build: 14 points) were each checked against the source before anything
changed: `registry.py:236-239`, `main.py:1030/1063-1076`, `events.py:203-206`, `heard.py:25-31`,
`robot_chassis.py:1214`, `robot_arm.py:488-496`, `gemini.py:690/760-807`, `drive_route.py` (lines 2086, 2292,
2934, 2948, 3051, `main()` 3324-3345), `MainActivity.java:93/1088`, `analyze_run.py:164`,
`test_emma_robot_app.py:~619`, and the route's step list (10 = checkpoint inside the big room, 11 = door D11
out, 20 = dock). Every point below held.

**Folded in**

| review | point | where |
|---|---|---|
| S1, B9 | a stop could come back `busy` (registry and face link) | §2a `request`, §2d `preempt`, P0/P1/P2 tests |
| S2 | STOP during `preparing`/`starting` did not cancel the queued launch | §2b cancel token + nav latch, §2e stdin from line one |
| S3 | the wheel fence missed `/robot/command` and keyed on one switch | §4 fence in the five primitives, keyed on `nav_activity`; panel forced off without routes |
| S4 | arms could move mid-run through the camera greeter's serial path; /arm stop was `#STOP` only | §4, P1 |
| S5 | no body gate / SafetyBar for a PC-started run | §2c `nav-poller`, `nav_unknown`, §2e `pc_run` |
| S6 | customers could start a walk by voice | `ROBOT_NAV_VOICE` (default `off`), §2d step 0, §7 4a |
| S7 | heard guard stale (global `_LAST`) and too broad | §2.0 `BODY_HEARD`, §2d step 4 (fresh, single-use, verb+side, place name) |
| S8, B8 | P0 changed sales-room timing; the counter could stick | `TURN_IDLE_TRACKS_TOOLS=false`, `ANNOUNCE_SETTLE_S=0`, id-tracked set with `tool_dropped`/resume/ceiling |
| S9 | any WS_TOKEN holder could pose as the face | per-session state, `issued_run`, optional `ROBOT_FACE_TOKEN` |
| S10 | localization guard changed this machine by default; `None` unhandled | refuse on `None`, CHANGELOG first line, `/mapview` notice, §7 #16 |
| B1 | head pulses beyond the one measured value | only `#8P1540`; `LIMITS` 7/8 = 1460–1540; four head ids → `needs_mapping` |
| B2 | leg 2 started cold on a door | leg 2 = steps 10–20, shared-checkpoint rule, `--state-out/--state-in` |
| B3 | leg 2 would never count as arrived | `leg_done` whenever `--to-step` is given; `analyze_run.py` |
| B4 | tether/stop queued on the single `io` thread | dedicated threads + source test |
| B5 | `run_id` had no workable origin | native `SecureRandom` hex32 → `--run-id`, echoed in status |
| B6 | stop codes lost to rewrapping; no `refused`/`interrupted` kinds | codes at raise sites, AST test, split preflight `try` |
| B7 | a refusal lost the robot's location | `here.json` deleted at the first `step`; exit 3 rewrites `run_log` |
| B10 | `busy` meant two things; `disconnected` missing | `request_pending`, both added to both dicts |
| B11 | P3 tests could not go red | synthetic door→checkpoint→drive fixture; clamp and its test dropped |
| B13 | launcher stdout block-buffered | `flush=True` + `python -u` |

**Rejected or changed from the review's fix**

- *B12 "assign both page-facing tests to P5."* Partly rejected. `tests/test_voice_preview_page.py` goes to P5,
  as proposed. `tests/test_emma_robot_app.py` stays with P4 because almost all of it is Java source checks.
  Moving it would make P5 the owner of P4's tests. Instead, the few page strings it asserts are frozen for P5,
  and P7 changes them if needed.
- *B14 "on app start, run `emma_nav.py stop`."* Rejected as written, because it would SIGINT a legitimate
  `robot_python run` from the PC every time the app restarts (both share `run.pid`). Replaced with
  `emma_nav.py reap`. It stops only an orphan of a dead app launcher, and it DELETEs a leftover board action
  only when `run.pid` is dead.
- *S7 "a caption newer than the last model turn."* The intent is adopted, but the boundary is moved. gemini-3.8
  sends `turn_complete` *before* the tool call in 13/15 turns, so a model-turn boundary would mark the
  utterance that caused the call as stale. The boundary used is the user's own `activity_start` (VAD local),
  plus an age bound and single use.
- *S9 "better: require a `ROBOT_TOKEN` hello."* Built, but **not required by default**. Requiring it means a
  token typed on both machines, and an unconfigured pull would silently stop the robot face from being the
  robot face. That is a behaviour change on pull, which CLAUDE.md forbids. The default is off, and it is
  owner decision #14. Without it, the remaining exposure is denial of service (a fake "active" that holds the
  fence), not motion or fake arrivals.
- *S3 "refuse when stale, whatever the switch says."* Adopted. One case is out of reach and recorded: a PC
  `robot_python run` while no face page is connected is invisible to the server. That is the operator at the
  PC, as today.
- *S10.* The guard stays independent of the routes switch (the review's fix allowed that). Restoring
  localization in drive_route is left as owner decision #8/#16 rather than done silently.
- *D4 "long-press ~1 s on a functions button".* Unchanged from revision 1. The existing 0.5 s hold menu opens
  the functions, and the 1 s hold sits on "เริ่มเดิน", where motion is committed.

## 9. Code review round 3 (9 Oct 2026) — what the implementation now does differently

A code review of the built packages found 43 points; five fixers and an integrator changed the code (CHANGELOG
2026-10-09). The sections above describe the plan; where the code now differs, this section wins. Nothing here
has run on the robot (it was off).

- **§2a `request` / `nav_activity`.** `nav_activity()` is the *most cautious* face (`_FACE_NAV`, per face), not the
  latest frame from any face; a `nav_caps` frame never ends a run (only that face's `nav_status` does). A NON-stop
  request whose ack times out puts a stop of the same kind on the wire before the tool answers; a late *accepted*
  ack sends another stop and joins `issued_run` — unless this session already sent a newer command of that kind,
  because the timeout's stop already sits ahead of it on the wire (a second stop would stop the newer walk).
  Side effect to know: the app's `bodyStop()` and `navStop()` both stop body AND nav and set `stop_pressed`, so
  these extra stops also stop a walk and start the app's stop latch.
- **§2d `stop_moving`.** Sends a body stop whenever there is a robot-face session, not only in routes mode, and
  whatever `ROBOT_BODY_ENABLED` says. `request_handshake` no longer lets the model narrate the arm moving (the app
  can refuse silently; a real ack is still open). The place guard refuses a caption that declines the walk or
  only asks about the place (`heard.declines_or_asks_where`: อย่า/ไม่ต้อง/ไม่อยาก…, ไหน/ยังไง/ไกล/กี่/where/how/far
  without พา/take/bring). The gesture guard needs a body noun for ambiguous verbs, refuses a negated verb, refuses
  a sided gesture in a caption that names the building or says "จะเห็น/จะเจอ" (`BODY_HEARD_SCENE`), and reads
  ซ้ายมือ/ขวามือ/"left hand side" as a side only.
- **§2d unlock.** `ROBOT_NAV_VOICE=unlocked` is keyed per face session (`nav_unlock` frame on the face's own `/ws`);
  an HTTP unlock never opens a loopback face (adb reverse makes the robot and every PC tab 127.0.0.1). Until
  `voice-preview.html` sends `nav_unlock`, `unlocked` cannot be used on a face behind adb reverse — fails safe.
- **§4 desk STOP.** `/robot/command stop` (the `/robot` panel, `/mapview`, robot-console, the joystick page) during
  an active or stale run goes through the walking face as nav stop + body stop, never the board DELETE, and answers
  `did: "stop_requested"` (sent, not confirmed). Face gone → `ok: false` and "press the robot's own stop".
- **§2e launcher.** `drive_route` installs `default_int_handler` for every run (`nohup … &` left SIGINT ignored);
  `--watch-parent <launcher pid>` and a refusal before the board when the launcher is already gone; exit 4 / log
  kind `error` for an uncaught exception; a run that never stepped keeps `here.json`. `emma_nav stop --wait S`
  escalates to kill -9 and then the board cancel; the app still calls `stop` without `--wait`. `robot_python`
  runs stop/status/busy through `/system/xbin/su 0 sh -c`, and every branch prints an answer, so empty output means
  "could not ask" (NOT STOPPED / do not start a second run) rather than "nothing running". **First powered-on
  session:** check `adb shell /system/xbin/su 0 sh -c id`.
- **§2b/§2c app.** A STOP cannot slip between the generation check and the arm command (`armGate`); frame gestures
  end on a board drop; a dead WebView renderer is rebuilt; B/STOP leave a live walk's DELETE to the escalation;
  a stale places file answers `places_stale` to a voice go; a Run given up on is not revived by a late line; the
  operator screen waits `STOP_DRAIN_MS` (the whole escalation) before connecting the SDK.

**Still open after round 3** (with the reason) is listed in CHANGELOG 2026-10-09.

## 10. The status link `/ws/robot-face` (9 Oct, after review 2 — the real fix for finding #0/#3)

The call's `/ws` is the bridge only while a call is open. A walk tapped on the panel, or a PC `robot_python run`
the app's `nav-poller` sees (it runs whenever the app is in front, whatever `nav_enabled` says), left
`nav_activity()` at `unknown` between calls, and the desk STOP could not reach the app. Nothing here has run on
the robot.

- **Server** (`app/robot_face_link.py` `serve_status_link`, `StatusLink`; route in `app/main.py`). Gated by
  `_reject_unauthorized` like every socket; only a user agent with `EmmaRobot/` (otherwise
  `{"type":"error","code":"not_robot_face"}` and close); with `ROBOT_FACE_TOKEN` set, frames count only after a
  matching `face_hello` (the call's rule, same code). In: `face_hello` + `INBOUND_TYPES` only, size-checked before
  parsing, then `receive`. Out: `{"type":"face_link_ready"}` once, then **stops only** — `_send` refuses any
  non-stop body/nav command addressed to a link (`not_robot_face`), before it is registered or sent. The link is
  never a tool's session (`registry.robot_face_session` is set by `VoiceSession.run` only), so it cannot get a go,
  an unlock or an announcement (`issued_run` stays false). Closed = `session_closed`: an active run stays
  active → stale, an idle entry goes. Turn-log rows it causes carry `origin: face_status_link`.
- **Liveness.** The page beats a `nav_status` every 10 s on the link while idle (`LINK_BEAT_S`); the 5 s beat of
  a walk is unchanged. A link silent for `LINK_SILENT_S` = 30 s, or any face whose latest `nav_status` says
  `poll_stale: true`, is **blind**: it reads `unknown` but ranks above `idle` in the most-cautious global answer
  (`active > stale > blind > idle > unknown`), so another face's `idle` cannot hide it and the review-2
  cannot-tell rules apply. An active frame keeps its active → stale clock; silence never lowers anything.
- **App** (`NavLauncher.status`, `NavRules.statusPollStale`). The app's idle used to come back as plain idle
  even when its run.pid poll was stale (su wedged, poller paused, the first second after start) — a PC run
  invisible, the fence open. It now carries `poll_stale: true` then (not for its own live run). The page passes
  the flag through only when true and sends a change of it at once.
- **Stops.** `stop_plan()` is unchanged in shape: `face` = the face that reported the run (`walking_face`, often
  the link now) or the newest live face. `robot_nav.stop` and the `/arm` stop go through
  `robot_face_link.request_stop`, which tries that face first, then the page's other socket
  (`stop_route`: first, walking face, every live face newest first). The first accepted ack is enough; a refusal,
  a dead socket or a timeout falls back. Answers stay "stop_requested" (sent, not confirmed) or unconfirmed +
  the red-bar hint.
- **Page** (`client/voice-preview.html`, bridge script). Only when `ON_ROBOT` (`?app=1` + `EmmaRobot/` +
  `EmmaBridge/1`): the sales room, desk tabs and the robot's kiosk Chrome never open it. Opened at load with the
  page's existing `token` query parameter (no new place for the WS token), backoff 2 s → 30 s reset only by
  `face_link_ready` (never `onopen` — the token gate accepts-then-closes), stopped for good on
  `unauthorized`/`not_robot_face` with a console line and nothing on screen. `emit` sends every frame to every
  live channel (call and link: each is its own server session; a duplicate state report is harmless); a channel
  that opens gets the snapshot, hello first. From the link only `body_command`/`nav_command` with
  `action: "stop"` are relayed (through `relayCommand`, the closed grammar); anything else is dropped.
- **Also (app):** with "เก็บแขนก่อนเดิน" off, `places()` marks every available leg with doors
  `available:false, reason:"arms_not_rest"` and `goRefusal` refuses it before anything is queued — the launcher
  refuses it anyway without `--arms-rested`, and the card used to look tappable.

Still open: a PC run with the robot's app closed or in the background (the poller pauses — blind, not idle);
a half-open link the page does not notice (the server reads it silent → unknown after 30 s; the page only
redials when the socket closes); `unknown` with a live but blind face still sends only the board DELETE (a face
stop is not attempted there).
