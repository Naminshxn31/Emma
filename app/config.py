"""Configuration for the condo voice assistant.

Two speech-to-speech providers are supported and selected with
`VOICE_PROVIDER`:

- `gemini` (default) — Google Gemini Live API. Has a **free tier**, so this
  is the one to start with.
- `openai`  — OpenAI Realtime API. Paid, billed per minute of audio.

Both are genuine speech-to-speech (audio in, audio out, no text round-trip);
they differ mainly in audio rates and how interruption is reported, and
`app/providers/` hides those differences from the rest of the app.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

from app.data_sources import project_identity, source_path

load_dotenv()

# One gallery server serves one project. A second project needs its own
# registered sources and server process before it can be selected here.
ACTIVE_PROJECT_ID = os.getenv("PROJECT_ID", "embassy_world").strip() or "embassy_world"


def _get_list(name: str, default: list[str]) -> list[str]:
    val = os.getenv(name)
    if not val:
        return default
    return [item.strip() for item in val.split(",") if item.strip()]


def _get_token(name: str) -> str:
    """One whitespace-free value: everything from the first space on is junk.

    Neither a URL nor a key may contain whitespace, and the first live outage
    of the inventory link was a hand-paste that carried the setup note's
    annotation arrow into .env. Secrets are pasted by hand on purpose, so
    paste accidents are part of the design and get tolerated here.
    """
    parts = os.getenv(name, "").split()
    return parts[0] if parts else ""


def _get_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


#: Tool groups a MULTI_SESSION server may load — everything else drives the
#: one physical machine and cannot be shared by concurrent conversations:
#:
#:   smarthome/computer/robot/documents  the host's lights, keyboard, legs
#:                                       and printer, shared by definition
#:   slides                              one Canva window, one STATE, one
#:                                       tour position — the exact collision
#:                                       the supersede rule was built to stop
#:   reminders                           rings *this* room
#:   memory                              one persistent store; testers would
#:                                       write into each other's (and the
#:                                       owner's) memory
#:   calc                                module-level STATE merged per call —
#:                                       two concurrent testers would merge
#:                                       budgets into one sheet, and one
#:                                       person's numbers on another's screen
#:                                       is the transcript-wipe bug with money
#:
#: The survivors are stateless reads: the unit card, search, documents-as-
#: knowledge, web search. An allow-list rather than a deny-list so a new
#: group is excluded until someone decides otherwise — same direction as
#: _OPT_IN.
MULTI_SESSION_SAFE = frozenset({"units", "knowledge", "mydocs", "websearch"})

#: What a blank TOOL_GROUPS means under MULTI_SESSION. mydocs/websearch stay
#: opt-in (named in TOOL_GROUPS to appear), mirroring app.tools._OPT_IN —
#: a test asserts the two lists cannot drift apart.
MULTI_SESSION_DEFAULT = frozenset({"units", "knowledge"})

#: ROBOT_NAV_PANEL and ROBOT_NAV_VOICE values (design §2h). The first is the
#: default and the fallback for anything else.
NAV_PANEL_MODES = ("off", "pin", "open")
NAV_VOICE_MODES = ("off", "unlocked", "on")


@dataclass
class Settings:
    project_id: str = ACTIVE_PROJECT_ID
    # Which persona this server runs: "condo" (the gallery receptionist,
    # default) or "emma" (the owner's personal assistant). One repo, two
    # hats — forking was rejected on purpose, because twenty-plus fixed bugs
    # (Thai tokenisation, audio lead, mishearings) would need fixing twice.
    assistant_profile: str = os.getenv("ASSISTANT_PROFILE", "condo").strip().lower()
    #: The condo prompt's layout (10 Oct 2026 experiment, owner: "เอามาปรับใช้
    #: วัดค่าทดสอบ ว่ามันดีขึ้นไหม"). v1 = the gallery's prompt, byte for byte;
    #: v2 = the same tuned sentences regrouped the way Gemini 3 / Realtime
    #: prompting guides recommend (core rules first, labelled sections, data
    #: in a tagged block last) -- see prompts.build_condo_v2. Measured with
    #: scripts/eval_live_qa.py before it may become the default.
    prompt_layout: str = os.getenv("PROMPT_LAYOUT", "v1").strip().lower()

    # Open the voice session the moment the page loads, no button and no
    # wake word first. The owner asked for this twice, knowing the trade:
    # an open session meters quota the whole time the tab is up, which is
    # exactly what the wake word was built to avoid — their machine, their
    # call. Default off so the gallery keeps its staff-starts-it morning
    # routine.
    auto_connect: bool = _get_bool("AUTO_CONNECT", False)

    #: Minutes the finished conversation stays readable on screen after the
    #: line goes to sleep, before the page wipes it.
    #:
    #: Two opposite mistakes to avoid, which is why this is a number and not
    #: a boolean. Wiping the instant the call ends throws away the thing the
    #: owner most often wants right afterwards — what was just said, the
    #: number he asked her to repeat, the Copy button. Never wiping leaves
    #: one visitor's conversation on a screen the next visitor walks up to,
    #: which is the same mistake `data/logs/` made by keeping everything
    #: forever: a retention policy that was never actually decided.
    #:
    #: Zero disables the wipe (kiosk on a desk nobody else reaches). The
    #: countdown starts when the line sleeps, not when the call ends, so a
    #: reconnect inside the window keeps the thread on screen.
    transcript_keep_min: float = float(os.getenv("TRANSCRIPT_KEEP_MIN", "5"))

    # Where Emma's timers and reminders live. Deliberately not the condo
    # data tree's log area: this is the owner's own data, kept until done,
    # while data/logs/ is strangers' speech on a 30-day clock. The two must
    # never share a deletion policy.
    reminders_file: str = os.getenv("REMINDERS_FILE", "data/reminders.json")
    # Permanent facts about the owner, told to Emma on purpose. Same rule as
    # reminders: the owner's data, never the gallery's, never on the log's
    # 30-day clock.
    memory_file: str = os.getenv("MEMORY_FILE", "data/memory.json")
    # Folder of the owner's own files (.txt .md .pdf) that Emma may search.
    personal_docs_dir: str = os.getenv("PERSONAL_DOCS_DIR", "data/personal-docs")
    # Scope the library to one project. Comma-separated path fragments; a file
    # is searchable only if its path (relative to personal_docs_dir) contains
    # one of them. Empty = every file, the old behaviour. Set to "embassy-world"
    # so the sales host answers about Embassy World alone and cannot pull an
    # Embassy Life brochure sitting in the same corpus into a "what do we have
    # here" answer (measured 2026-09-14: it did, and invented specifics too).
    mydocs_include: str = os.getenv("MYDOCS_INCLUDE", "")
    # Gemini's native google_search grounding for the live session.
    # Measured 2026-08-22: a bare session connects fine while the same
    # session with this tool gets 1011 — grounding has its own quota and
    # the free tier has none of it. So this is a billing-tier feature:
    # flipping it on a free key turns every session into an instant 1011,
    # which the browser then redials — a robot that dies the moment it
    # answers. Turn on only after billing is enabled AND
    # scripts/probe_web_search.py prints OK for both probes.
    web_search: bool = _get_bool("WEB_SEARCH", False)
    #: A self-hosted SearXNG instance for `search_web`. When set, queries go
    #: there first and only fall back to the ddgs scraper if it fails.
    #:
    #: Why it earns a slot: ddgs is a scraper pretending to be a browser,
    #: and on 2026-08-25 it was measured failing outright on DuckDuckGo and
    #: hanging 15.5s in its Yahoo fallback. SearXNG runs on this machine,
    #: fans out to several engines, and answers JSON built for exactly this
    #: use. Still a fallback tier, not the fast path — Gemini's native
    #: grounding (WEB_SEARCH, needs billing) stays the real answer.
    searxng_url: str = _get_token("SEARXNG_URL").rstrip("/")

    # --- Microphone shaping (browser-side) ---
    # Far-field help in software: the page runs mic -> compressor -> gain
    # before anything hears it. The compressor squeezes loud-near and
    # quiet-far speech closer together, then the gain lifts the result —
    # the standard recipe for pulling distant speech up without clipping.
    # 1.0 = chain off (gallery default). 1.5-2.5 is the useful range;
    # beyond that the noise floor comes up with the voice.
    mic_boost: float = float(os.getenv("MIC_BOOST", "1.0"))
    #: Boost during a *call*, when different from the standby one. The two
    #: modes want opposite microphones: standby must hear the name from
    #: across the room (far-field: compressor + gain), but a call is one
    #: person at the desk, and the same compressor lifts everyone else's
    #: conversation to their level — Emma then answers words never aimed at
    #: her. Blank = same as MIC_BOOST, so machines that never asked for the
    #: split keep exactly one knob. 1.0 = the chain is bypassed on calls.
    call_mic_boost_raw: str = os.getenv("CALL_MIC_BOOST", "").strip()
    # Browser noise suppression eats quiet distant voices along with the
    # noise. Turn it off when chasing range in a quiet room; keep it on in
    # a noisy one. Measure by ear, not by theory.
    mic_noise_suppression: bool = _get_bool("MIC_NOISE_SUPPRESSION", True)
    #: The compressor half of MIC_BOOST's far-field recipe, separately
    #: switchable. Ratio 8 above -45dB lifts *everything* quiet — including
    #: the room's own noise floor, measured on this machine sitting right at
    #: the "speech" bar (frame RMS 0.02-0.03 in windows with nobody talking,
    #: captured 2026-08-26). A level bar that never rests and a keyword
    #: spotter listening through amplified hiss are both this. Off = plain
    #: gain: speech and noise scale together instead of noise catching up.
    mic_compressor: bool = _get_bool("MIC_COMPRESSOR", True)
    #: Browser automatic gain control. In a *quiet* room AGC hunts upward
    #: until something reaches its target level — and the only thing there
    #: is the noise floor. Same failure as the compressor, different agent.
    mic_agc: bool = _get_bool("MIC_AGC", True)
    #: Record the *call* microphone to data/call_debug/ for a listen-back,
    #: off by default. The call path otherwise writes no audio (turnlog is
    #: numbers only, by design); this is the WAKE_DEBUG equivalent for a live
    #: call, so near-field / VAD_MIN_RMS can be tuned by ear against
    #: front-vs-side recordings. Sensitive — customer speech on disk
    #: (data/call_debug is gitignored). On only while chasing a mic problem.
    call_debug: bool = _get_bool("CALL_DEBUG", False)
    #: Seconds of the most recent call audio to keep (rolling buffer, so a
    #: short controlled test call is captured whole).
    call_debug_max_s: int = int(os.getenv("CALL_DEBUG_MAX_S", "180"))

    @property
    def call_mic_boost(self) -> float:
        return float(self.call_mic_boost_raw) if self.call_mic_boost_raw else self.mic_boost

    def listens_far(self, profile: str | None) -> bool:
        """Whether a call on this profile listens far (LISTEN_FAR_PROFILES).
        A blank profile is the machine's default one, as the session reads it."""
        return (profile or self.assistant_profile).strip().lower() in self.listen_far_profiles

    def call_vad_floor(self, profile: str | None) -> float:
        """The near-field floor a call on this profile opens with."""
        return self.vad_min_rms_far if self.listens_far(profile) else self.vad_min_rms

    # --- Recognising faces at the door ---
    # Off by default, and for the usual reason: the showroom machine pulls
    # this repo and its behaviour must not change. A camera that starts
    # naming people the morning after a git pull is the loudest possible
    # version of that mistake.
    # Who renders the enrolment station's spoken lines (app/voice.py).
    # "gemini" = the robot's own voice (Kore), ten free renders a day and no
    # names; "local" = Thai MMS-VITS on this machine, unlimited and offline,
    # in its own voice. Switching = a different voice = delete
    # data/faces/voice/ first; state() warns if two voices share the cache.
    tts_provider: str = os.getenv("TTS_PROVIDER", "gemini")
    face_enabled: bool = _get_bool("FACE_ENABLED", False)
    # Which model pack recognises faces. "auraface" (Apache-2.0, commercial
    # use allowed) is the default; "buffalo_l" is the original insightface
    # pack whose weights are licensed for non-commercial research only —
    # kept loadable strictly for comparison runs. See _PACKS in faces.py.
    face_model_pack: str = os.getenv("FACE_MODEL_PACK", "auraface")
    face_gallery: str = os.getenv("FACE_GALLERY", "data/faces/gallery.npz")
    # Cosine threshold — a property of the model pack, re-measured with
    # `scripts/eval_faces.py` whenever FACE_MODEL_PACK changes.
    #
    # auraface (default pack), measured 2026-08-31, same protocol (149
    # enrolled, 156 held-out probes, 41 strangers): rank-1 152/156, genuine
    # p5 0.365, but a stranger's best match reaches 0.477 — three pairs in
    # 0.454-0.477 were reviewed by eye and could NOT be confirmed as the
    # same person, so they stand as false accepts. 0.50 greets 136/156
    # (87.2%), never wrongly, never a stranger. The licence bought this:
    # buffalo_l measured 156/156 with impostors under 0.342 (threshold 0.45
    # on that pack), but its weights are non-commercial-research-only.
    # A silence costs a greeting; a wrong name is said out loud to the
    # person it is wrong about — the default protects the second.
    # And every photograph behind these numbers is a studio portrait: the
    # entrance camera is a different instrument (check-face-range.cmd).
    face_threshold: float = float(os.getenv("FACE_THRESHOLD", "0.50"))
    # How many frames in a row must agree before anybody is greeted. One
    # frame is a bad witness — blur, a turning head, someone crossing
    # behind. Same reasoning as the VAD's `min_silence_duration`.
    face_confirm_frames: int = int(os.getenv("FACE_CONFIRM_FRAMES", "3"))
    #: For this many seconds after a face arrives, a frame that cannot
    #: tell a colleague from a stranger — blurred, turned sideways, or
    #: scoring in the band just under the naming bar — is not counted as
    #: evidence of a stranger; the arrival gets that long to show a clear
    #: frame first. Past it every frame counts as before, so a stranger is
    #: greeted at most this much later. Measured 2026-10-08: blur that
    #: drops a sharp-frame score of 0.73 to 0.46 reads as "a stranger" in
    #: two frames, and the logs show colleagues greeted anonymously and
    #: then recognised 20-90 s later. 0 = off (the old per-frame rule) —
    #: the code default, so a machine that pulls this keeps its behaviour.
    face_track_wait_s: float = float(os.getenv("FACE_TRACK_WAIT_S", "0"))
    #: How far the best match must lead the best match *of a different
    #: person* before the robot says a name. Two colleagues who resemble
    #: each other score close together, and "close second" is exactly the
    #: frame where the nearest is wrong. Measured on AuraFace (31 Aug): the
    #: right name at the desk scores >= 0.545 and the wrong-name flicker
    #: tops out at 0.141 — a 0.10 margin never fires on that data, and it
    #: is there for the day two look-alikes are enrolled.
    face_margin: float = float(os.getenv("FACE_MARGIN", "0.10"))
    #: Say a name only when that face is the only usable one in the frame.
    #: With two people walking in together the model would be told one
    #: name and address both of them by it; a greeting without a name is
    #: never wrong. Off = the nearest face is named regardless (old behaviour).
    face_name_when_alone: bool = _get_bool("FACE_NAME_WHEN_ALONE", True)
    #: Require a consent record (data/faces/people.json) before naming
    #: anybody. Off by default because the staff portraits in staff.csv
    #: have no such record — the people in them never enrolled themselves
    #: — and flipping this on before their consent is actually collected
    #: would silently unname the whole sales team. Revoked and expired
    #: records are honoured regardless of this switch: see app/consent.py.
    face_require_consent: bool = _get_bool("FACE_REQUIRE_CONSENT", False)
    #: How long a consent given at the enrolment station lasts.
    face_consent_days: float = float(os.getenv("FACE_CONSENT_DAYS", "365"))
    # Somebody standing at the desk is one arrival, not one per frame.
    face_cooldown_s: float = float(os.getenv("FACE_COOLDOWN_S", "600"))
    # Whether a face nobody knows is also worth waking up for. On is the
    # point of a receptionist — most visitors are strangers — but it is the
    # setting that decides whether a camera aimed at a corridor opens a
    # Gemini session for every passer-by, so it is reachable without editing
    # code. The cooldown applies to strangers as one group, so the worst
    # case is one session per FACE_COOLDOWN_S, not one per person.
    face_greet_strangers: bool = _get_bool("FACE_GREET_STRANGERS", True)
    # Narrower than this and they are across the room, not at the door —
    # which is also where recognition is least reliable, so both reasons
    # point the same way.
    #
    # The default is desk range. Measured on the owner's room (29 Aug,
    # Facecam at 1280x720): standing in the doorway the face is 53-59px and
    # the right name still scores 0.45-0.58, while walking blurs it to 44px
    # with garbage scores (0.10-0.13) — so that machine runs FACE_MIN_PX=50
    # in .env to make the doorway wake the robot. Wrong-name flickers at
    # that size measured 0.08-0.13, nowhere near FACE_THRESHOLD.
    face_min_px: int = int(os.getenv("FACE_MIN_PX", "110"))
    # Which capture index the entrance camera is on. -1 searches for the
    # first one showing a *moving* picture, which is the only reliable way
    # to tell a lens from a virtual camera: on the owner's machine index 0
    # is the Elgato Virtual Camera, permanently displaying a flawless still
    # of its own logo, and index 1 is the real Facecam. Set the number here
    # once it is known so nothing has to search at startup.
    face_camera: int = int(os.getenv("FACE_CAMERA", "-1"))
    #: Where frames come from: `local` = a lens on this machine via OpenCV
    #: (FACE_CAMERA picks which); `robot` = JPEG frames the robot's kiosk
    #: page sends to /ws/camera (`?cam=1`). The robot's cameras are on the
    #: robot, an Android machine the server cannot open a device on - the
    #: page it already runs is the only thing there that can. Anything
    #: else is `local`: a typo must not silently turn the camera off.
    face_camera_source: str = (
        "robot" if os.getenv("FACE_CAMERA_SOURCE", "").strip().lower() == "robot" else "local")
    # How many CPU cores onnxruntime may use. Left to itself it takes every
    # one of them, and a 16-core showroom PC sat at 100% the moment the
    # camera came on — while also running a live voice session. Measured
    # end to end through `facewatch.Watcher.see`, at FACE_FPS=5:
    #
    #   threads   idle    someone there   time to decide (3 confirms)
    #   default   18.5%   79.3%           0.6 s
    #   4         10.1%   43.4%           0.7 s
    #   2          7.8%   18.5%           1.2 s   <- default
    #   1          6.1%    6.4%           2.1 s
    #
    # Two, because this job does not need to be fast. Somebody walking up
    # to a desk is in frame for seconds, and deciding in 1.2s instead of
    # 0.6s costs nothing anybody can perceive while it hands three quarters
    # of the machine back to the thing that does have to answer instantly.
    # Drop to 1 on a weaker machine; 0 means "let onnxruntime decide",
    # which is the setting that caused the complaint.
    face_threads: int = int(os.getenv("FACE_THREADS", "2"))
    # How often frames are actually analysed. The camera hands over 30 a
    # second and a decision needs nowhere near that: somebody walking up to
    # a desk is in frame for seconds, and `FACE_CONFIRM_FRAMES` counts
    # agreeing looks, not video frames.
    face_fps: float = float(os.getenv("FACE_FPS", "5"))
    #: Seconds between attempts to reopen the camera after it stops
    #: delivering frames (USB hiccup, cable, the enrolment station holding
    #: it). 0 = give up, the pre-2026-09-01 behaviour: greeting off until
    #: the server restarts, with nothing on the machine saying so.
    face_camera_retry_s: float = float(os.getenv("FACE_CAMERA_RETRY_S", "10"))
    #: The face scan on the call screen (`/preview` hold menu, slot 1;
    #: app/face_scan.py): staff PIN, the scanned person's own "ยินยอม", the
    #: station's five poses, a name, straight into the live gallery. Off by
    #: default for the reason every switch in this file is: the showroom
    #: pulls this repo, and a pull must not give it a LAN endpoint that files
    #: faces under typed names.
    face_scan_enabled: bool = _get_bool("FACE_SCAN_ENABLED", False)
    #: The staff PIN that opens a scan. Empty = the scan refuses to start
    #: even when switched on: WS_TOKEN is blank on a localhost machine, and
    #: then this PIN is the only thing between a passer-by and the gallery.
    face_scan_pin: str = os.getenv("FACE_SCAN_PIN", "").strip()
    #: A scan's whole lifetime from start — a hard deadline no step moves —
    #: after which it is dropped, crops and all, on the clock with no request
    #: needed; also how long the door camera's greeting stays off for it.
    #: Capped at greeter.MAX_PAUSE_S (900). It used to be an idle timeout
    #: each step refreshed, and a preview left open refreshed it forever
    #: (review, 2026-09-29): the greeter stayed deaf for the rest of the day.
    face_scan_session_s: float = float(os.getenv("FACE_SCAN_SESSION_S", "600"))
    #: "Probably somebody we know" (owner 9 Oct 2026: far away, the robot
    #: may recognise a face but not be sure). When the nearest face's best
    #: match is a consented, enrolled person in the band under the naming
    #: bar (facewatch.PROBABLE_LOW..FACE_THRESHOLD) — or far, blurred,
    #: turned, or a close second — frames that would count toward "a
    #: stranger" are held for up to this many seconds while the person
    #: comes closer; the name is still said only on the usual rules. Past
    #: it, or once they walk away, a stranger is greeted without a name as
    #: before. 0 = off: the code default, so a pull changes nothing.
    face_probable_wait_s: float = float(os.getenv("FACE_PROBABLE_WAIT_S", "0"))
    #: The smallest face (px) the hold describes. Far faces below
    #: FACE_MIN_PX are otherwise never described; with the hold on they are,
    #: only to keep a probable arrival alive (they never greet). Measured
    #: on the synthetic 1024x768 frames (scripts/eval_face_distance.py):
    #: 33 of 35 faces at 30 px are not even detected, 2 of 35 at 40 px.
    face_probable_min_px: int = int(os.getenv("FACE_PROBABLE_MIN_PX", "36"))
    #: A closer look: ask the robot's face page for a q95 crop of the
    #: tracked face at the camera's native resolution (camctl `crop`, the
    #: answer rides /ws/camera as a tagged binary). Measured on synthetic
    #: frames: a 40 px colleague's p10 score 0.459 -> 0.552 against the
    #: q70 frame. Only with FACE_CAMERA_SOURCE=robot; off = nothing asked.
    face_zoom_enabled: bool = _get_bool("FACE_ZOOM_ENABLED", False)
    #: Let the page answer from `ImageCapture.takePhoto()` (only without a
    #: flash, only when its photo is bigger than the video) instead of the
    #: video frame. Unmeasured on the robot: turn on after its log line
    #: "robot camera caps" shows a photo bigger than the video.
    face_zoom_photo: bool = _get_bool("FACE_ZOOM_PHOTO", False)
    #: The owner's "ขยับเพื่อเช็ค": turn the base in place toward a probable
    #: far face (never toward it in a straight line). Refused on the dock,
    #: while any walk runs or cannot be ruled out, with chassis motion off,
    #: without lidar room, and while a conversation is live. Uses
    #: robot_chassis.nudge (its own fences apply on top).
    face_turn_to_check: bool = _get_bool("FACE_TURN_TO_CHECK", False)
    #: Largest turn, degrees; never more than 30 whatever this says.
    face_turn_max_deg: float = float(os.getenv("FACE_TURN_MAX_DEG", "30"))
    #: The greeting gesture (group 6 = the verified handshake) only for a
    #: named greeting with one face in frame — never a stranger, never two
    #: people. Off = the gesture rides every camera greeting as before
    #: (still behind ROBOT_GREETING_GESTURE_ENABLED and every arm gate).
    face_handshake_named_only: bool = _get_bool("FACE_HANDSHAKE_NAMED_ONLY", False)

    # --- Wake word ("Emma") ---
    # Off by default: the gallery robot is started by staff each morning and
    # must not grow a hot mic by surprise. The owner's machine turns it on.
    wake_enabled: bool = _get_bool("WAKE_ENABLED", False)
    # The name, as text. Encoded against the KWS model's BPE at startup, so
    # changing it is an .env edit — but test any new name out loud before
    # trusting it: short names collide with more of ordinary speech.
    wake_word: str = os.getenv("WAKE_WORD", "emma")
    wake_model_dir: str = os.getenv(
        "WAKE_MODEL_DIR",
        "data/wake/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01",
    )
    # Score shaping for the keyword path. Raise the threshold if the name
    # fires on speech that merely resembles it; raise the boost if a clear
    # call of the name is being missed. Defaults are the values measured
    # best on 2026-08-25 against Thai TTS renderings of the name (7/8
    # caught, zero false positives on the negative set) — see the table in
    # app/wake.py. Boost past ~5 measured strictly worse.
    wake_boost: float = float(os.getenv("WAKE_BOOST", "3.0"))
    wake_threshold: float = float(os.getenv("WAKE_THRESHOLD", "0.10"))
    #: Log what the standby microphone is actually sending, every couple of
    #: seconds, while waiting for the name.
    #:
    #: Written because "I said Emma and nothing happened" had no evidence
    #: behind it anywhere. The page showed "ไมค์กำลังฟังอยู่" the moment one
    #: frame left the browser — a frame of digital silence counts — and the
    #: server logged nothing at all unless the word actually fired. So a
    #: muted input device, a microphone across the room, and a name the model
    #: cannot match all looked identical, and the only way to tell them apart
    #: was to guess and change something.
    #:
    #: Off by default: it is two lines a second in a log the gallery reads
    #: for other reasons. Turn it on while chasing the microphone, off after.
    wake_debug: bool = _get_bool("WAKE_DEBUG", False)
    #: Where WAKE_DEBUG writes clips of speech that fired nothing — the
    #: owner's real voice through the real chain, which is the one input all
    #: TTS-based tuning could never test. Debug only, newest 20 kept.
    wake_debug_dir: str = os.getenv("WAKE_DEBUG_DIR", "data/wake_debug")
    #: Enrollment collection: save EVERY speech window on the standby
    #: socket (hits included) so a matcher can learn the owner's own voice —
    #: the one input no TTS proxy renders. Deliberate sessions only: turn
    #: on, say the name 10-15 times, turn off.
    wake_enroll: bool = _get_bool("WAKE_ENROLL", False)
    wake_enroll_dir: str = os.getenv("WAKE_ENROLL_DIR", "data/wake_enroll")
    #: Comma list of spellings to listen for, overriding the built-ins.
    #: Which spellings catch a real call of the name depends on the mouth,
    #: the room and the microphone, so it has to be tunable where those are
    #: — not in a commit. Blank uses the list in app/wake.py.
    wake_spellings: str = os.getenv("WAKE_SPELLINGS", "")

    # --- Tools (things the assistant can actually do) ---
    tools_enabled: bool = _get_bool("TOOLS_ENABLED", True)
    #: Blank = every group. Otherwise a comma list, e.g. "smarthome".
    tool_groups: str = os.getenv("TOOL_GROUPS", "")
    #: Shared test server: many browsers, one server, no machine. Off by
    #: default — the showroom default must stay byte-identical after a pull,
    #: which is the one thing the profile seam exists to protect. When on,
    #: concurrent sessions stop superseding each other and the tool set is
    #: forced down to groups that are safe to share (see
    #: `enabled_tool_groups`), because "everyone in the company can try it"
    #: must not mean "everyone in the company can print PDFs and press keys
    #: on the host".
    multi_session: bool = _get_bool("MULTI_SESSION", False)

    # Broadlink IR hub for the gallery lights/AC. Point these at the JSON
    # files the `emma` project already learned the codes into, or leave them
    # and everything runs in mock mode.
    ir_enabled: bool = _get_bool("IR_ENABLED", True)
    ir_codes_file: str = os.getenv("IR_CODES_FILE", "data/ir_codes.json")
    broadlink_device_file: str = os.getenv(
        "BROADLINK_DEVICE_FILE", "data/broadlink_device.json"
    )
    #: Names of the air conditioners in the room (owner, 2026-10-09: "เพิ่ม
    #: แอร์ ... samsung", "เพิ่มเป็นตัวที่สอง"). Lists the extra units and the
    #: code prefix each was learned under in IR_CODES_FILE. A unit takes part
    #: only once its power codes are learned on THIS machine, so listing it
    #: changes nothing until then; blank = the original AC only.
    ir_devices_file: str = os.getenv("IR_DEVICES_FILE", "data/ir_devices.json")

    # Slide deck shown on the robot's screen. `index.json` alongside the
    # images maps each file to titles/summaries/keywords in both languages.
    slides_dir: str = os.getenv(
        "SLIDES_DIR", str(source_path("slide_catalog", ACTIVE_PROJECT_ID).parent)
    )
    #: The unit table the sales team owns: room, size, view, price, status,
    #: plus approved_by and effective_from. Item 8 on their list, and the
    #: one that unblocks five others.
    units_file: str = os.getenv(
        "UNITS_FILE", str(source_path("local_unit_inventory", ACTIVE_PROJECT_ID))
    )
    #: Show the sample table when the real one is missing.
    #:
    #: Off by default and it must stay that way on any machine a customer can
    #: see. The sample exists so the card can be designed and shown to the
    #: sales team before their spreadsheet arrives; a showroom quoting made-up
    #: prices because nobody remembered a setting is the exact failure
    #: "ห้ามแต่งข้อมูลโครงการเองเด็ดขาด" was written to prevent. The card
    #: watermarks itself and the model is told to say so out loud, but the
    #: default is the real defence.
    units_sample: bool = _get_bool("UNITS_SAMPLE", False)
    # Live link to the sales team's own inventory system (condo-inventory:
    # Supabase/PostgreSQL, ~1,082 units, statuses flipped by the sales admin
    # UI with every change logged to a person). When set, show_unit answers
    # from here instead of any file — which retires the biggest worry item 8
    # carried: a static "available" that stopped being true yesterday.
    #
    # The key is the service-role secret: it bypasses RLS, so it stays in
    # this server's .env, is used for reads only, and is copied in by a
    # person — not by tooling. Blank = the file/sample chain as before.
    # `.split()[0]`: neither a URL nor a key may contain whitespace, and the
    # first live outage of this link was exactly that — the setup note's
    # annotation arrow ("← ค่าจาก ...") pasted into .env along with the
    # value, and show_unit reported the sales system unreachable while it
    # was fine. Values are copied by hand on purpose (secrets stay out of
    # tooling), so hand-paste accidents are part of the design and get
    # tolerated here rather than diagnosed at the first customer question.
    inventory_url: str = _get_token("INVENTORY_SUPABASE_URL").rstrip("/")
    inventory_key: str = _get_token("INVENTORY_SUPABASE_KEY")
    #: Seconds a fetched unit stays fresh. Short on purpose: the whole point
    #: of the live link is that "ว่าง" means now, not yesterday.
    inventory_cache_s: float = float(os.getenv("INVENTORY_CACHE_S", "30"))
    #: Which project's units this gallery may show. The sales team's
    #: inventory holds three Empire projects since 2026-09-03 (Embassy
    #: World, Embassy Life, Embassy One) and all three have buildings
    #: A/B/C — the same `unit_no` exists in more than one. Measured
    #: 2026-09-11: find_units put "A-1405" on the gallery screen as one of
    #: ours; it is an Embassy Life unit. Blank falls back to ours rather
    #: than "every project" — an unset knob must not widen what a customer
    #: sees (the WS_TOKEN="" lesson).
    inventory_project: str = (os.getenv("INVENTORY_PROJECT", "").strip()
                              or "embassy-world")
    #: Whether exact prices may appear on screen / in the model's hands.
    #:
    #: Off by default at the owner's instruction: the pricelist is the sales
    #: team's negotiation material, and a robot putting exact baht on a
    #: screen a customer can photograph gives that control away. Budget
    #: questions still work — the price stays a *filter* on the server —
    #: but the number itself never leaves: it is stripped from the tool
    #: result, so neither the card nor Emma ever has it to show or say.
    units_show_price: bool = _get_bool("UNITS_SHOW_PRICE", False)
    #: Whether Emma reads `pricing_unit_promotions` at all (list_promotions,
    #: the card's promo badge, the tap-to-reveal promo figure).
    #:
    #: Off by default because the table cannot tell a test row from a real
    #: offer: on 2026-10-06 it held 20 active "NET PRICE: 2,990,000 THB"
    #: rows that the owner says are tests — "ของจริงไม่มี" — while the
    #: approved facts say there is no promotion. With this on, Emma would
    #: answer the same question two ways depending on which source she
    #: picked, and the screen would list nineteen offers that do not exist.
    #: Turn it on only once the rows in the sales system are real.
    units_promotions: bool = _get_bool("UNITS_PROMOTIONS", False)
    #: Where the floor-plan images live. The sales app serves them as public
    #: static files on its Vercel deployment; floor->path comes from
    #: project sales/floor-plan-assets.json (copied from condo-inventory — recopy when
    #: their plans change). Not a secret, just an address.
    inventory_plan_base: str = _get_token("INVENTORY_PLAN_BASE") or "https://condo-inventory.vercel.app"
    #: One JSON line per turn — see app/turnlog.py. Records what guests say,
    #: so it is a privacy decision as much as a debugging one; off with
    #: TURN_LOG=false. No audio is ever written, only text.
    #: Documents the sales team prepared for the robot to print. It never
    #: creates one — it picks from data/documents/catalogue.json.
    documents_dir: str = os.getenv("DOCUMENTS_DIR", "data/documents")
    print_enabled: bool = _get_bool("PRINT_ENABLED", True)
    printer_name: str = os.getenv("PRINTER_NAME", "")
    #: Paper does not come back, and mishearings become quantities. A real
    #: transcript contained "อะไรนะ 50".
    print_max_copies: int = int(os.getenv("PRINT_MAX_COPIES", "3"))
    #: A PDF printing helper (SumatraPDF.exe). Set this and Windows file
    #: associations stop mattering — which is what a kiosk PC needs, because
    #: an association is a setting nobody owns after the machine is reimaged.
    pdf_print_exe: str = os.getenv("PDF_PRINT_EXE", "")
    #: Which embedding backend builds the slide index: auto | gemini | local | off.
    #: `auto` prefers the API and falls back to a local model if there is no
    #: key. Switching this rebuilds the index — vectors from two models are
    #: not comparable, see retrieval.embedding_signature.
    embed_provider: str = os.getenv("EMBED_PROVIDER", "auto")
    turn_log: bool = os.getenv("TURN_LOG", "true").strip().lower() not in {"false", "0", "no"}
    turn_log_dir: str = os.getenv("TURN_LOG_DIR", "data/logs")
    #: How long the raw transcripts are kept, in days. 0 keeps them forever.
    #:
    #: These are recordings of what members of the public said in a sales
    #: gallery, written down without anyone being asked, and the robot is told
    #: to read phone numbers back to confirm them — so numbers end up in here.
    #: A debugging tool becomes an indefinite archive of strangers the moment
    #: nothing deletes it, which is what was happening.
    #:
    #: The analysis value does not need the raw text. `scripts/analyze_log.py
    #: --save` writes counts and topics to data/log-summaries/, which survive
    #: this deletion and contain nothing anybody said.
    turn_log_keep_days: int = int(os.getenv("TURN_LOG_KEEP_DAYS", "30"))

    # Optional: mirror the presentation on the *actual* Canva design instead
    # of (alongside) the exported images. Canva's view links refuse to be
    # embedded in an iframe (X-Frame-Options), so this isn't an embed — it's
    # a real, separate Chromium window driven by Playwright, the same way a
    # person would click through it. Only slides in the measured page map
    # (presentations/slides/canva_pages.json, whose deck_url must name the
    # same design as this URL) can be synced; anything else on screen just
    # doesn't move this window. Leave CANVA_URL blank to skip all of this.
    canva_url: str = os.getenv("CANVA_URL", "")
    # Names the ids a new Canva import gets (scripts/import_canva_export.py).
    # No longer a sync gate: the 2026-10-06 library is intro-NNN and the
    # measured map, not the id spelling, says which page shows which slide.
    canva_deck_prefix: str = os.getenv("CANVA_DECK_PREFIX", "ew")
    # Headless has no window to put on a screen — false is what you want for
    # an actual gallery display; true is only useful for a smoke test.
    canva_headless: bool = _get_bool("CANVA_HEADLESS", False)
    # Launch the window fullscreen with no browser chrome, for the display.
    canva_kiosk: bool = _get_bool("CANVA_KIOSK", True)

    # Which browser the mirror drives. Canva refuses to load *some* slide
    # assets in Playwright's bundled Chromium — it flags the automated build
    # and shows "Image not found" for those images, while a normal browser on
    # the very same link renders them. Driving the real installed Chrome
    # instead (plus hiding the automation signal, below) makes Canva treat it
    # like the browser that already works. Empty falls back to bundled
    # Chromium; "chrome"/"msedge" use the installed one. If the channel isn't
    # present the launch degrades to bundled rather than failing.
    canva_browser_channel: str = os.getenv("CANVA_BROWSER_CHANNEL", "chrome")

    # Open the Canva window as soon as the server starts, rather than when a
    # presentation does. Off by default: a fullscreen browser sitting over
    # the desktop from boot is in the way while you're working. Startup still
    # verifies Chromium can run (headlessly, invisibly), so a broken install
    # is reported immediately either way.
    #
    # Also the switch CANVA_WARM_DECK depends on. Walking the deck needs a
    # window and is only free while nobody is watching, so "warm at boot"
    # and "no window at boot" cannot both be true; with this off the warm-up
    # is skipped and says so in the log rather than opening a window anyway.
    canva_open_at_start: bool = _get_bool("CANVA_OPEN_AT_START", False)

    # --- Web stage: "put that on the screen" ---
    # Open pages in a Chromium window this server drives, instead of firing
    # `os.startfile` at whatever browser the desktop happens to own.
    #
    # A window rather than a panel in /display because most of the web
    # refuses to be embedded — Canva's own view links do, which is why
    # canva_display exists at all — and a blocked frame renders as a white
    # rectangle with no error anywhere. See app/tools/webstage.py.
    #
    # Off by default: the gallery's `open_in_browser` opens the staff's own
    # browser, and a receptionist that puts visitor-requested web pages on
    # the presentation screen is a different product.
    web_stage: bool = _get_bool("WEB_STAGE", False)
    web_stage_kiosk: bool = _get_bool("WEB_STAGE_KIOSK", True)
    #: "chrome"/"msedge" drive the installed browser; blank uses bundled
    #: Chromium. Real Chrome plays more video formats — bundled Chromium
    #: ships without the proprietary codecs, so some YouTube videos are
    #: audio-only or refuse outright on it.
    web_stage_channel: str = os.getenv("WEB_STAGE_CHANNEL", "chrome")

    # Walk the whole deck once when the window opens, so every page is drawn
    # before a guest is standing in front of it.
    #
    # Canva's viewer fetches pages as you reach them, and its own progress bar
    # shows how far it has got. Open the deck and jump to page 35 and you get
    # the page template — a pale empty gradient with nothing on it. That is
    # the "หน้าหาย" a guest reported, and it is not a timing bug on our side:
    # nothing had asked for that page. A tour that answers questions cannot
    # promise to only ever move one page at a time, so the deck gets walked
    # up front instead. About half a minute, once. See `warm_deck`.
    canva_warm_deck: bool = _get_bool("CANVA_WARM_DECK", True)

    # End a session after this many seconds with no guest speech. 0 = never.
    #
    # A gallery session ends when somebody walks away, which produces no
    # event: the socket stays open, the Live API connection stays billed,
    # and a fullscreen window stays on the wall. Measured against guest
    # speech only, not activity — a robot narrating 65 slides to an empty
    # room is exactly what this ends, and it is busy the whole time.
    #
    # Off by default. A demo that hangs up mid-sentence because somebody set
    # this to thirty seconds is worse than the bill it saves.
    idle_timeout_s: int = int(os.getenv("IDLE_TIMEOUT_S", "0") or 0)

    # How often to check whether somebody moved the Canva deck by hand, so
    # the robot can narrate the page they went to. One `location.hash` read
    # per interval — cheap, but it is polling, so don't drop it far below a
    # second. Set 0 to leave the deck entirely robot-driven.
    canva_poll_s: float = float(os.getenv("CANVA_POLL_S", "1.0"))

    # Beat between slides during a narrated tour, after the previous slide's
    # narration has finished playing. A presenter pauses before moving on;
    # zero makes the deck feel like it is being flipped through.
    slide_pause_s: float = float(os.getenv("SLIDE_PAUSE_S", "0.8"))
    #: Show the one approved picture that matches an answer (search_condo_info)
    #: on a machine that does NOT load the `slides` group — the picture, not
    #: the presentation. The owner took the deck off twice (11 Sep, 8 Oct) and
    #: on 2026-10-11 asked for pictures back: "ให้บริการ / ให้ชม อาจจะเป็น
    #: รูปภาพให้เขาดู ไม่ได้หมายถึงพาไปดู" -> "ทุกเรื่องที่มีภาพตรง". Off by
    #: default so a pull leaves the sales room's screen exactly as it was;
    #: the robot turns it on in data/robot-server/robot.env, and
    #: scripts/robot_server.py then ships the 48 manifest-pinned pictures.
    answer_images: bool = _get_bool("ANSWER_IMAGES", False)
    #: The Interactive Presentation Engine on the robot face (owner 12 Oct
    #: 2026: a question answered by voice AND a motion story on the portrait
    #: screen, beat by beat with her voice, ending on an overview the guest can
    #: tap). `present_story(topic)` -- the model picks WHICH story, never a
    #: number (app/stories.py, docs/emma-story-engine.md). Off by default so a
    #: pull leaves every machine byte-identical; also needs `stories` named in
    #: TOOL_GROUPS (opt-in, like `body`), the robot face's own session, the
    #: condo profile and an approved catalog. `robot_server.py deploy` ships the
    #: story pictures only while this is on in robot.env.
    stories_enabled: bool = _get_bool("STORIES_ENABLED", False)
    #: How long a slide's question waits for an answer before the tour moves
    #: on by itself. Long enough that a guest who is thinking isn't cut off,
    #: short enough that a room full of people who won't talk to a robot
    #: doesn't strand the presentation. Ends the wait early the moment anyone
    #: actually speaks.
    tour_ask_grace_s: float = float(os.getenv("TOUR_ASK_GRACE_S", "9.0"))

    # Longest `next_slide` may hold the function call open while it waits for
    # the guest to finish hearing the previous narration.
    #
    # Holding it is what keeps the picture and the words together, but on a
    # realtime session it also means the model is parked mid-turn for the
    # length of a paragraph. A tour that narrates a few slides and then goes
    # quiet points straight at this. Lower it if that happens: a slide that
    # turns slightly early is a much smaller problem than one that never
    # turns at all.
    slide_tool_budget_s: float = float(os.getenv("SLIDE_TOOL_BUDGET_S", "20.0"))

    # Longest a slide change waits for the Canva window to actually be on the
    # page before the model is allowed to start narrating it. This is the
    # first slide's problem: Chromium is cold-starting (measured around four
    # seconds), and without the wait the robot talks over a window still
    # loading the cover. A slow-but-working launch and a wedged one look the
    # same until the timeout fires, so this has to cover a real cold start
    # (hence 5s, above the ~4s seen) yet stay well under the dispatch cap so a
    # genuinely stuck window still lets the tour begin. Warm slides arrive by
    # arrow key in about a tenth of a second and never approach it.
    canva_arrival_timeout_s: float = float(os.getenv("CANVA_ARRIVAL_TIMEOUT_S", "5.0"))

    # --- Astronaut robot (Aobo SDK, via the app on its chest screen) ---
    #
    # Off by default. With this false, or with no robot app connected, the
    # movement tools run in mock mode: they succeed logically, change nothing
    # physically, and report `hardware: "mock"` so the model tells the guest
    # the truth. Same contract as the IR tools — see `_worst()` in
    # smarthome.py, which exists because "mock" was once reported as "ok" and
    # the assistant announced it had switched off an air conditioner that
    # never received anything.
    robot_enabled: bool = _get_bool("ROBOT_ENABLED", False)
    #: The robot app's own credential, carried inside `robot_ready`. Distinct
    #: from WS_TOKEN on purpose: WS_TOKEN is in every browser's URL on the
    #: LAN, and until 2026-09-01 any page that had it could send
    #: `robot_ready` and become "the robot" — `available()` would flip and
    #: every walk command would go to it. Empty = no socket is ever
    #: accepted as the robot (robot stays mock), which is the safe default.
    robot_token: str = os.getenv("ROBOT_TOKEN", "")
    #: How long a walk may take before the server stops believing the robot
    #: is still on its way. Without it, an app that crashed after taking
    #: the order left `moving=True` forever — no arrival, no error, and a
    #: model told to "wait for the arrival message" that never came.
    robot_arrival_timeout_s: float = float(os.getenv("ROBOT_ARRIVAL_TIMEOUT_S", "120"))
    #: Pretend destinations, so the guiding conversation can be rehearsed
    #: before the robot exists. Ignored the moment a real robot reports its
    #: own map. Never makes anything move — see `robot_link.places`.
    robot_mock_places: list[str] = field(default_factory=lambda: _get_list(
        "ROBOT_MOCK_PLACES", []))

    # --- The chassis underneath the Aobo badge (SLAMTEC Slamware REST) ---
    #
    # Empty by default, which is every machine that has ever run this code:
    # movement then takes the Android-app path `robot_link` was written for.
    # Set it and the server drives the chassis itself and the app is only a
    # voice client again — see `app/robot_chassis.py` for what that costs.
    #
    # The address is *not* the robot's Wi-Fi address. The navigation board
    # lives on the robot's own internal network (192.168.11.1), reachable
    # from the robot's tablet but not from the gallery LAN, so this is
    # normally the near end of a tunnel rather than the board itself.
    robot_chassis_url: str = os.getenv("ROBOT_CHASSIS_URL", "")
    # Connection permits inspection; motion needs a separate deliberate opt-in
    # after the physical stop and local operating area have been verified.
    robot_chassis_motion_enabled: bool = _get_bool("ROBOT_CHASSIS_MOTION_ENABLED", False)
    #: How often to ask the chassis what it is doing. This is the only clock
    #: that ends a walk: REST has no callback, so nothing reports an arrival
    #: unless somebody asks. Two seconds is well inside the guest's patience
    #: and nowhere near the board's rate limits.
    robot_chassis_poll_s: float = float(os.getenv("ROBOT_CHASSIS_POLL_S", "2.0"))
    #: Per-request timeout. Short on purpose: a hung read must become
    #: "unreachable" quickly, because the honest state while we cannot see
    #: the robot is "unknown", and `snapshot()` reports that rather than
    #: leaving a stale `moving` on screen.
    robot_chassis_timeout_s: float = float(os.getenv("ROBOT_CHASSIS_TIMEOUT_S", "5.0"))
    #: How close counts as arrived when the action ends without a status.
    #: The vendor documentation says a finished action simply disappears, so
    #: the fallback is the robot's own reported pose against where it was
    #: sent — a measurement, not an assumption that HTTP 200 meant arrival.
    robot_chassis_arrival_tolerance_m: float = float(
        os.getenv("ROBOT_CHASSIS_ARRIVAL_TOLERANCE_M", "0.6"))
    #: Hold-to-drive deadman. The joystick page re-sends its direction every
    #: ~150 ms while a button is held; if the server hears nothing for this
    #: long it cancels the chassis action itself. The board's own MoveByAction
    #: is documented to stop when not re-sent, but how long *that* takes has
    #: not been measured, and a robot must not depend on an unmeasured timer.
    robot_drive_timeout_ms: int = int(os.getenv("ROBOT_DRIVE_TIMEOUT_MS", "400"))
    #: Hold-to-drive will not push the robot toward anything the lidar sees
    #: closer than this, in the direction of travel (metres from the lidar,
    #: not from the shell). Owner's number, 2026-09-12. Turning in place and
    #: driving *away* from the obstacle are never blocked — a robot that
    #: cannot back out of a corner is stuck, not safe.
    robot_drive_min_clearance_m: float = float(os.getenv("ROBOT_DRIVE_MIN_CLEARANCE_M", "0.3"))
    #: "Save this spot" needs the robot to know where the spot is. Below this
    #: `localization_quality` (0..100) the pose is odometry drift — on
    #: 2026-09-12 that was (0, 0) for a robot really at (3.0, -4.1) — and a
    #: destination saved from it would send every future guest to nowhere.
    robot_poi_min_quality: int = int(os.getenv("ROBOT_POI_MIN_QUALITY", "50"))

    # --- The upper body: a Torobot servo board on a serial port ---
    #
    # Separate from the chassis in every way that matters. The chassis is a
    # navigation computer that answers questions about itself over HTTP; this
    # is a servo controller that takes text on a wire, says nothing back, and
    # has no stop command. Sharing one switch between them would mean turning
    # on the half nobody has stood next to yet.
    #
    # Off by default and staying that way until somebody has found the servo
    # power supply the manual says is on its own circuit, because that supply
    # is the only real stop this hardware has.
    robot_arm_enabled: bool = _get_bool("ROBOT_ARM_ENABLED", False)
    #: The device node on the *robot*, not on this machine. On 2026-09-10 the
    #: two USB-serial converters came up as ttyUSB10 and ttyUSB11 rather than
    #: the SDK's documented default of ttyUSB1, so this is left empty: a
    #: guessed path is a write into whatever else is on that port.
    robot_arm_port: str = os.getenv("ROBOT_ARM_PORT", "")
    #: Within the 9600-128000 the Astronaut manual lists for this board, and
    #: matching the SDK default. The board detects the rate itself; this sets
    #: the host end.
    robot_arm_baud: int = int(os.getenv("ROBOT_ARM_BAUD", "115200"))
    #: How far from centre a joint may be driven, in microseconds of pulse
    #: width. 200 of a possible 1000 — deliberately a fraction of the travel,
    #: to be widened by somebody who has watched the joint move and knows
    #: which way it goes.
    robot_arm_span: int = int(os.getenv("ROBOT_ARM_SPAN", "200"))
    #: Playing a stored action group is the one control here whose extent
    #: cannot be stated: the recording may drive any of the board's twenty
    #: channels anywhere in their range. `ROBOT_ARM_SPAN` and the four-channel
    #: list bound the stepping controls and do not touch it, so it gets its
    #: own switch rather than riding in on `ROBOT_ARM_ENABLED`.
    robot_arm_groups_enabled: bool = _get_bool("ROBOT_ARM_GROUPS_ENABLED", False)
    #: Let Emma accompany a spoken greeting with one stored arm gesture.
    #: This opt-in never bypasses the arm, motion, group, or disarm gates;
    #: it only asks `robot_arm.run_group()` after the greeting has reached
    #: the voice provider. Group 6 is the vendor face-recognition binding
    #: (`faceregroupaction=6`, labelled handshake/greeting), but it has not
    #: yet been physically confirmed on this robot.
    robot_greeting_gesture_enabled: bool = _get_bool(
        "ROBOT_GREETING_GESTURE_ENABLED", False
    )
    robot_greeting_arm_group: int = int(os.getenv("ROBOT_GREETING_ARM_GROUP", "6"))
    #: The arm's motion interlock, mirroring `robot_chassis_motion_enabled`.
    #: Off by default and switched off again on 2026-09-11 after the owner's
    #: probe showed `#STOP` acknowledged (`#STOP+OK`) while channel 5 kept
    #: moving to its target. Until a stop that has been *seen* to halt a
    #: joint exists, nothing here may start one. Stop, prepare and state stay
    #: available with this off — a lock that also disabled the brake would
    #: be worse than no lock.
    robot_arm_motion_enabled: bool = _get_bool("ROBOT_ARM_MOTION_ENABLED", False)
    #: The bridge to the robot's shell. There is no route from this server to
    #: that serial port except through the tablet the port is plugged into.
    robot_arm_adb: str = os.getenv("ROBOT_ARM_ADB", "adb")
    robot_arm_adb_serial: str = os.getenv("ROBOT_ARM_ADB_SERIAL", "")
    robot_arm_timeout_s: float = float(os.getenv("ROBOT_ARM_TIMEOUT_S", "8.0"))

    # --- Body by voice + navigation from the robot face ---
    # docs/emma-body-nav-design-2026-10-08.md §2h. Every one of these is off
    # (or inert) by default and the robot has its own key on top, because the
    # sales room pulls this repo and must not change on a pull. None of it has
    # run on the robot yet.
    #
    #: Server key for `move_body`. The `body` group must also be named in
    #: TOOL_GROUPS (it is opt-in), the robot must have `body_enabled`, and the
    #: ROBOT_ARM_* interlock above still applies — this adds a key, it never
    #: replaces one.
    # Follow-up after silence (owner 12 Oct: answer completely, then if the
    # guest says nothing for a while, ask ONE short follow-up; any voice in
    # that window cancels it and she listens). Seconds of real silence after
    # her voice has played out. Research: peers leave ~0.1-0.3 s between turns
    # and ~1 s already feels long (Stivers 2009, Jefferson 1989), but a guest
    # who has just been told something needs time to think -- 3 s+ of wait
    # time multiplies answers (Rowe), and voice assistants keep listening 5 s
    # (Alexa) to 8 s (Google). 0 = off, the default: the sales room's PC
    # behaves exactly as before; the robot turns it on in robot.env.
    follow_up_after_s: float = float(os.getenv("FOLLOW_UP_AFTER_S", "0"))
    # A heard question Gemini never answers (robot, 14 Oct 13:19: three
    # questions transcribed, then 146 s of nothing from the server -- no
    # reply, no usage, no error -- before it answered one; once in 104
    # replies over three days, the rest started 1-2 s after the guest's last
    # word, slowest 6 s; eval 361 turns, slowest 3.6 s). Seconds after
    # activity_end, with words transcribed and no sign of a reply, before the
    # provider completes the turn again (an empty turn_complete); then
    # REPLY_STALL_RECONNECT_S more before it rejoins the conversation on a new
    # socket (the resumption handle) and asks the question again as text.
    # Never for a segment with no words: silence is the right answer to room
    # noise. 0 = off, the default (the sales room's PC is unchanged); the
    # robot turns it on in robot.env. Evidence: `reply_stall` rows.
    reply_stall_s: float = float(os.getenv("REPLY_STALL_S", "0"))
    reply_stall_reconnect_s: float = float(os.getenv("REPLY_STALL_RECONNECT_S", "8"))
    # A reply that is only "ค่ะ" is not played or shown (owner 15 Oct: talk not
    # meant for Emma gets silence -- 24 of 142 robot turns 12-14 Oct were a bare
    # "ค่ะ"; the prompt cannot ask for silence, 3.8 speaks the stage direction).
    # The prompt keeps "ตอบแค่ ค่ะ" -- what the model does reliably -- and the
    # provider drops that turn (transcript_filter.BareAckGate). Off by default:
    # the sales room's PC unchanged; the robot turns it on in robot.env.
    silence_bare_ack: bool = _get_bool("SILENCE_BARE_ACK", False)
    robot_body_enabled: bool = _get_bool("ROBOT_BODY_ENABLED", False)
    robot_body_ack_timeout_s: float = float(os.getenv("ROBOT_BODY_ACK_TIMEOUT_S", "2.5"))
    #: The heard-word guard (the `heard.py` precedent: "bit like" closed the
    #: deck). A body tool call asks instead of moving unless a fresh caption
    #: of this session holds the action's verb.
    robot_body_require_heard: bool = _get_bool("ROBOT_BODY_REQUIRE_HEARD", True)
    #: Voice nav on the robot face goes to the on-robot launcher, and the
    #: chassis planner refuses moves — one navigation system at a time.
    robot_nav_routes_enabled: bool = _get_bool("ROBOT_NAV_ROUTES_ENABLED", False)
    robot_nav_ack_timeout_s: float = float(os.getenv("ROBOT_NAV_ACK_TIMEOUT_S", "3.0"))
    robot_nav_places_file: str = os.getenv("ROBOT_NAV_PLACES_FILE", "data/robot-places/floor2.json")
    #: Server-side pre-check only (review 2, finding #16): the app and the
    #: launcher refuse a voice go on an unverified leg whatever this says,
    #: so on a real robot true only moves `leg_unverified` from the server
    #: to the robot. Kept for scripts/eval_live_qa.py's scripted robot.
    robot_nav_voice_unverified: bool = _get_bool("ROBOT_NAV_VOICE_UNVERIFIED", False)
    robot_nav_require_heard: bool = _get_bool("ROBOT_NAV_REQUIRE_HEARD", True)
    #: off | unlocked | on. The robot face talks to the public and "ไป" is in
    #: almost every sentence, so a verified leg plus a heard word was not
    #: enough to let a passer-by send the robot down the corridor (safety
    #: review #6). Anything unrecognised reads as `off`.
    robot_nav_voice: str = os.getenv("ROBOT_NAV_VOICE", "off").strip().lower()
    #: Oldest caption the body/nav heard guard accepts, in seconds.
    robot_heard_max_age_s: float = float(os.getenv("ROBOT_HEARD_MAX_AGE_S", "10"))
    #: off | pin | open — the face page's นำทาง slot. Forced to `off` at boot
    #: while routes are off (see `__post_init__`); read it through
    #: `nav_panel_mode()`, which re-applies that rule at runtime.
    robot_nav_panel: str = os.getenv("ROBOT_NAV_PANEL", "off").strip().lower()
    #: What .env asked for before the boot rule, so the warning can be
    #: repeated by whoever logs the boot (the import-time line may go to a
    #: stderr nobody reads).
    nav_boot_warnings: list[str] = field(default_factory=list, init=False, repr=False)
    robot_nav_pin: str = os.getenv("ROBOT_NAV_PIN", "").strip()
    #: Silence during a voice run before one announcement; also the "stale"
    #: bound of `robot_face_link.nav_activity()`.
    robot_nav_status_stale_s: float = float(os.getenv("ROBOT_NAV_STATUS_STALE_S", "20"))
    #: When set, the face session counts as the robot face only after a
    #: matching `face_hello`. Empty = the user agent decides, as before. The
    #: ROBOT_TOKEN/robot_ready fix of 1 Sep, applied to the face.
    robot_face_token: str = _get_token("ROBOT_FACE_TOKEN")
    #: `turn_complete` sets `turn_idle` only when no tool call is in flight
    #: (gemini-3.8 sends turn_complete before its tool call in 13/15 turns,
    #: docs/emma-live-qa-eval-2026-10-08.md). Off: the sales room's announce
    #: and nudge timing must not change on a pull.
    turn_idle_tracks_tools: bool = _get_bool("TURN_IDLE_TRACKS_TOOLS", False)
    #: Wait after `turn_idle` before an announcement. 0 = no sleep and no
    #: extra check. Revision 1 of the design had 0.8, which would have
    #: changed condo timing; P6 measures the gap, the owner sets it.
    announce_settle_s: float = float(os.getenv("ANNOUNCE_SETTLE_S", "0.0"))

    # --- Server ---
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    cors_origins: list[str] = field(default_factory=lambda: _get_list("CORS_ORIGINS", ["*"]))
    ssl_certfile: str = os.getenv("SSL_CERTFILE", "")
    ssl_keyfile: str = os.getenv("SSL_KEYFILE", "")
    # Shared secret for every WebSocket, required the moment HOST leaves
    # 127.0.0.1: this server carries tools that open programs and press keys
    # on the machine, and an open LAN socket is an invitation. Empty = no
    # check (localhost-only development). The roadmap gated LAN exposure on
    # exactly this, and the gallery/home machines crossed that line today.
    ws_token: str = os.getenv("WS_TOKEN", "")


    # --- Provider selection ---
    provider: str = os.getenv("VOICE_PROVIDER", "gemini")  # gemini | openai

    # --- Gemini Live (free tier available) ---
    gemini_api_key: str | None = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.8-live")
    gemini_voice: str = os.getenv("GEMINI_VOICE", "Despina")
    #: Model to use if GEMINI_MODEL cannot be opened. Blank disables it.
    #:
    #: Even a stable model can be unavailable for a key, region, or quota. On
    #: the day that happens the assistant goes silent and the only trace may
    #: be a traceback nobody is watching. A slightly older voice is a much
    #: smaller problem than an assistant that does not answer.
    #:
    #: Only used for errors that say the *model* is unavailable — see
    #: `_model_is_unavailable`. A dropped connection still fails loudly.
    gemini_model_fallback: str = os.getenv(
        "GEMINI_MODEL_FALLBACK", "gemini-2.5-flash-native-audio-preview-12-2025")
    # Adapts the reply's tone to the guest's tone. Native-audio 2.5 only, and
    # needs the v1beta endpoint — ignored automatically on models without it.
    gemini_affective_dialog: bool = _get_bool("GEMINI_AFFECTIVE_DIALOG", False)
    # Lets the model stay quiet when speech clearly isn't aimed at it — useful
    # for a robot standing in a busy gallery. Native-audio 2.5 only.
    gemini_proactive_audio: bool = _get_bool("GEMINI_PROACTIVE_AUDIO", False)

    # Gemini 2.5 native audio enables *dynamic thinking by default*, which
    # adds a noticeable pause before the robot starts talking. A receptionist
    # answering from a fixed fact sheet doesn't need it, so default to 0
    # (off). Raise it if answers start feeling shallow.
    gemini_thinking_budget: int = int(os.getenv("GEMINI_THINKING_BUDGET", "0"))
    # Gemini 3.1 and Gemini 3.8 Extended Thinking use levels. The standard
    # Gemini 3.8 Live model rejects thinking_config, so the provider omits it.
    gemini_thinking_level: str = os.getenv("GEMINI_THINKING_LEVEL", "low")

    # --- OpenAI Realtime (paid) ---
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-realtime-2.1")
    openai_url: str = os.getenv("OPENAI_REALTIME_URL", "wss://api.openai.com/v1/realtime")
    openai_voice: str = os.getenv("OPENAI_VOICE", "marin")
    openai_transcribe_model: str = os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-realtime-whisper")

    # --- Turn taking (applies to both, mapped per provider) ---
    # Docs recommend 500-800ms: shorter fragments an utterance at natural
    # pauses and hurts quality; longer just feels sluggish.
    vad_silence_ms: int = int(os.getenv("VAD_SILENCE_MS", "500"))
    vad_prefix_padding_ms: int = int(os.getenv("VAD_PREFIX_PADDING_MS", "300"))
    # Local Silero only: reject clicks/bumps that resemble speech for one or
    # two frames. Pre-roll preserves the beginning while the detector waits.
    vad_min_speech_ms: int = int(os.getenv("VAD_MIN_SPEECH_MS", "250"))
    # Local Silero only: "speaker focus" for the END of a turn. Once a
    # segment is open, audio quieter than this fraction of the speaker's own
    # recent level counts as silence for the end-of-turn clock (it is still
    # forwarded). Measured 2026-10-08: background talk 14 dB under the
    # speaker kept the turn open until the room went quiet (8 s+ in the
    # test mix; up to the 60 s valve in a busy gallery); 0.30 ends it ~1.5 s
    # after the speaker stops, and split none of 51 real call segments
    # (0.35 split one: the same speaker, 4 dB softer). 0 = off (old chain).
    vad_focus_ratio: float = float(os.getenv("VAD_FOCUS_RATIO", "0.30"))

    # Gemini only. Read these carefully — the names are the opposite of what
    # they sound like:
    #   START LOW = detects the start of speech *less often* (misses quiet
    #               or short utterances entirely — the robot just sits there)
    #   END   LOW = decides speech has ended *less often* (slower replies)
    # HIGH is the API's own default for both and what you almost always want.
    # Do not reach for LOW to fix the robot hearing itself; use HALF_DUPLEX.
    vad_start_sensitivity: str = os.getenv("VAD_START_SENSITIVITY", "HIGH")  # HIGH | LOW
    vad_end_sensitivity: str = os.getenv("VAD_END_SENSITIVITY", "HIGH")      # HIGH | LOW

    # Half-duplex: stop sending microphone audio while the assistant is
    # speaking. Kills barge-in in ordinary modes; the EmmaJoke preview *can*
    # use an acoustic duck-and-check gate to admit a person who talks over
    # her — see ECHO_BARGE below. This is the reliable cure when speaker
    # output feeds back into the mic (laptop speakers, no headphones, no
    # hardware echo cancellation). Prefer headphones or a robot with
    # hardware AEC and leave this off.
    half_duplex: bool = _get_bool("HALF_DUPLEX", False)

    # The EmmaJoke barge-in exception (a page that loaded the acoustic gate
    # may interrupt her mid-sentence). Off by default since 2026-09-30 —
    # the owner tried it for a morning and ordered it out ("เอาพูดแทรกออก"):
    # the duck-and-check made her voice stutter, and being uninterruptible
    # was the lesser evil. The gate code stays; flip this to try it again.
    # Only ever applies to profile=joke + a page advertising echo_barge=1,
    # so the sales-room default is unchanged either way.
    echo_barge: bool = _get_bool("ECHO_BARGE", False)

    # The standing line (owner, 2026-09-30 "ทำเลย" on: ต่อสาย Gemini ค้างไว้
    # เวลาทักจะได้ไว). app/standing.py rings the standby browser whenever no
    # session is live, and session.py holds that summoned line open while it
    # is still virgin (ungreeted, nobody heard) instead of abandoning it at
    # 30s — so a face greeting costs a turn, not a 2-3s dial. A line that
    # HAS been used still dies by the ordinary idle rules: one visitor's
    # words must not sit in the model's context when the next one walks up,
    # and the loop then rings a fresh line. Costs ~nothing while idle only
    # because local VAD never streams silence; do not enable with
    # VAD_MODE=gemini. Off by default — the sales machine keeps wake/button.
    standing_line: bool = _get_bool("STANDING_LINE", False)

    # Hold-speech (owner, 2026-09-30 "ทำเลย"): the third road between
    # barge-in (any noise cuts her sentence — rejected the same morning) and
    # plain half-duplex (words spoken while she talks are DROPPED, and
    # people talk over her constantly). With this on, a half-duplex page
    # keeps capturing during her speech, queues what clears the floor below,
    # and sends it the moment her audio finishes — she always finishes her
    # sentence, and the person never has to repeat theirs. The floor is what
    # makes it safe on the robot WITHOUT the duck-and-check that stuttered:
    # her own speaker echo measured ~0.025 peak in the robot's mic
    # (2026-09-14) while real speech lands 0.06+, so 0.04 passes the person
    # and starves the echo. Only meaningful with HALF_DUPLEX on and barge-in
    # off; old pages ignore the flag and keep dropping (safe).
    hold_speech: bool = _get_bool("HOLD_SPEECH", False)
    hold_speech_floor: float = float(os.getenv("HOLD_SPEECH_FLOOR", "0.04"))

    # Who decides when speech starts and ends.
    #   gemini (default) — Google's server-side detection; the mic streams
    #                      upstream continuously, silence included.
    #   local            — Silero VAD on this machine (sherpa-onnx, the wake
    #                      word's own runtime). Gemini's detection is turned
    #                      off and only speech is forwarded, wrapped in
    #                      explicit activity signals. Kills the silence-
    #                      hallucination class (我们走吧 from a quiet room)
    #                      at the source and stops metering silence.
    # Default stays gemini: the gallery must not change hearing behaviour on
    # a pull, and local needs a one-time model fetch.
    vad_mode: str = os.getenv("VAD_MODE", "gemini").strip().lower()
    vad_model: str = os.getenv("VAD_MODEL", "data/wake/silero_vad.onnx")
    #: VAD_MODE=local only — near-field floor. A speech segment opens only
    #: if its trigger chunk reaches this RMS; quieter speech (someone else's
    #: conversation across the room) is treated as silence and never sent.
    #: 0 = off. NOTE the scale depends on the mic chain in front of it:
    #: WakeStream's 0.05-0.2 reference was measured *behind* the MIC_BOOST
    #: compressor; with CALL_MIC_BOOST=1.0 (the pairing this floor wants —
    #: a compressor squeezing far voices up to near level defeats any
    #: loudness floor behind it) the same speech lands several times lower.
    #: Tune from the "vad floor: ... rms=X" log lines, not from theory —
    #: the first guess here was wrong by 3x for exactly this reason.
    vad_min_rms: float = float(os.getenv("VAD_MIN_RMS", "0"))
    #: Which assistant profiles listen FAR instead of near (owner, 2026-09-29:
    #: "แต่ละโหมดฟังเสียงต่างกัน บางโหมดจะต้องฟังเสียงคนด้านหน้า บางโหมด
    #: สามารถฟังได้ไกล" — EmmaJoke and the interpreter far, the sales host near).
    #: Blank = no split: every profile keeps exactly today's chain (MIC_AGC,
    #: VAD_MIN_RMS), so a machine that never asked is unchanged. Set = the
    #: split is real on BOTH ends: a far profile gets browser AGC on and the
    #: VAD_MIN_RMS_FAR floor; every other profile gets AGC OFF and the
    #: VAD_MIN_RMS floor. AGC off is not a side effect — AGC is what let a
    #: near-field call hear the whole room (the robot, 7/15 turns from
    #: somebody else), so a "near" mode that kept it would not be near.
    listen_far_profiles: frozenset = frozenset(
        p.strip().lower() for p in os.getenv("LISTEN_FAR_PROFILES", "").split(",") if p.strip())
    #: The loudness floor for a far-listening call. 0 = none: Silero alone
    #: decides what is speech, and SILENCE_RMS still stands underneath.
    vad_min_rms_far: float = float(os.getenv("VAD_MIN_RMS_FAR", "0"))

    # ---- listening focus: talk to the person in front of the camera ----
    # Owner, 9 Oct 2026: the robot answered people chatting nearby ("เป็นเสียง
    # คนคุยกันปกติ") and asked to talk only with whom it sees, separate
    # voices, and know who is speaking. app/focus.py arbitrates; spec in
    # docs/listening-focus-2026-10-09.md. Every switch is OFF by default:
    # the sales room (no robot camera) must behave byte-identically.
    #: Master switch. Off = none of the focus inputs change what is heard.
    focus_enabled: bool = _get_bool("FOCUS_ENABLED", False)
    #: A speech segment may open only while a face is "engaged": seen within
    #: FOCUS_HOLD_S, at least FOCUS_FACE_MIN_PX wide (a distance proxy), and
    #: turned toward the robot within FOCUS_YAW_MAX_DEG.
    focus_require_face: bool = _get_bool("FOCUS_REQUIRE_FACE", True)
    focus_face_min_px: int = int(os.getenv("FOCUS_FACE_MIN_PX", "90"))
    focus_yaw_max_deg: float = float(os.getenv("FOCUS_YAW_MAX_DEG", "35"))
    focus_hold_s: float = float(os.getenv("FOCUS_HOLD_S", "1.5"))
    #: Rule 2 waivers (13 Oct: on the robot 1,065 segments were denied "no face
    #: in front" to 86 opened; the guest at the chest screen is under the head
    #: camera, or so close the face is lost after a frame). Both 0 = off =
    #: today's rule. CONVERSE_HOLD_S: after a segment opens, a face is not
    #: required for this long (each open renews it). NEAR_RMS: a segment
    #: this loud opens without a face -- room chatter measured 0.02-0.04,
    #: opened segments p50 0.057; tune from focus_* rows, never by guess.
    focus_converse_hold_s: float = float(os.getenv("FOCUS_CONVERSE_HOLD_S", "0"))
    focus_near_rms: float = float(os.getenv("FOCUS_NEAR_RMS", "0"))
    #: Third waiver: a segment opening within this many seconds after her
    #: voice ended (greeting or answer) is the guest's reply -- turn-taking
    #: needs no camera. 0 = off. focus.her_turn_ended has the measurement.
    focus_reply_window_s: float = float(os.getenv("FOCUS_REPLY_WINDOW_S", "0"))
    #: A camera-summoned call is deaf until its greeting is heard, or this
    #: long if no greeting comes (the face was lost before it was judged).
    #: 13 Oct robot logs: ears opened 9-10 s after the dial on call after
    #: call -- a guest who spoke first was not heard at all in that window.
    summoned_hold_max_s: float = float(os.getenv("SUMMONED_HOLD_MAX_S", "10"))
    #: Tell the model who is speaking each turn ("ผู้พูด: คนที่ 1 / คุณ…").
    focus_speaker_labels: bool = _get_bool("FOCUS_SPEAKER_LABELS", False)
    # ---- engagement: behave like an established reception robot ----
    # Owner, 9 Oct 2026: "ลองปรับให้เหมือนหุ่นต้อนรับอื่นให้ดีที่สุด" (Google
    # Look and Talk, Pepper's engagement modes, Furhat's attention). Every
    # switch is OFF by default and every one needs FOCUS_ENABLED: off, the
    # call path is the focus path above, byte for byte. Spec section
    # "Engagement" in docs/listening-focus-2026-10-09.md.
    #: Look and Talk's "out of the zone, say the name": the wake-word
    #: detector (app/wake.py's model and spellings, its OWN bar below) runs on
    #: the audio of a camera-opened call. Hearing "Emma" opens a window in
    #: which focus rule 2 (a face in front) is waived; rules 3-5 still judge
    #: every face in view. app/call_wake.py has the CPU measurement.
    focus_wake: bool = _get_bool("FOCUS_WAKE", False)
    #: The in-call bar, scaled per spelling exactly like WAKE_THRESHOLD.
    #: Measured 9 Oct on 677 s of real call audio (data/call_debug): 0.10,
    #: 0.12 and 0.15 fire 4 times, 0.20 three -- a stricter bar barely buys
    #: anything there -- while 0.15 already halves the owner's real name
    #: clips caught (4 -> 2 of 60) and 0.25 loses the Thai female fixture.
    focus_wake_threshold: float = float(os.getenv("FOCUS_WAKE_THRESHOLD", "0.10"))
    #: How long after the name an out-of-view voice is heard.
    focus_wake_window_s: float = float(os.getenv("FOCUS_WAKE_WINDOW_S", "10"))
    #: Pepper's BasicAwareness (turn toward a voice), in words: an out-of-view
    #: caller is invited to step in front, once, through events.announce.
    focus_invite: bool = _get_bool("FOCUS_INVITE", False)
    #: At most one invitation per this many seconds per call.
    focus_invite_every_s: float = float(os.getenv("FOCUS_INVITE_EVERY_S", "60"))
    #: Also invite when this many "no face in front" denials in 20 s came
    #: from ONE direction (the array's DOA) -- somebody clearly talking to the
    #: robot from outside the lens without the name. 0 = off: without a
    #: bearing it cannot tell calling from chatting, and with one it still
    #: invites two people chatting side by side. Measure before turning on.
    focus_invite_denied_n: int = int(os.getenv("FOCUS_INVITE_DENIED_N", "0"))
    #: Pepper's engagement modes. semi = today (anybody engaged in front can
    #: speak; rule 5 lets a second customer join). full = once a main person
    #: is established (the session's main speaker, focus._main_person) every
    #: other face or voice is ignored until that person leaves (none of their
    #: face tracks engaged for FOCUS_HOLD_S), then anyone may engage.
    focus_engage_mode: str = os.getenv("FOCUS_ENGAGE_MODE", "semi").strip().lower()
    #: Furhat's attention cue: the robot face's mascot turns its head toward
    #: whoever is speaking (focus.attention), over the call's own socket.
    focus_attention: bool = _get_bool("FOCUS_ATTENTION", False)
    #: A brief goodbye when the person a camera-opened conversation was with
    #: walks away mid-conversation, then the call ends (the standing line
    #: rings a fresh one). Off: a person reading a brochure loses their face
    #: for seconds too -- tune FOCUS_GOODBYE_AFTER_S from `focus_gap` rows
    #: (written whenever focus is on) before turning it on.
    focus_goodbye: bool = _get_bool("FOCUS_GOODBYE", False)
    focus_goodbye_after_s: float = float(os.getenv("FOCUS_GOODBYE_AFTER_S", "8"))
    #: Active speaker: mouth movement of each face, from facial landmarks on
    #: face crops at ASD_FPS (the robot camera page sends faster while on).
    asd_enabled: bool = _get_bool("ASD_ENABLED", False)
    asd_fps: float = float(os.getenv("ASD_FPS", "15"))
    #: Voice prints per speech segment (sherpa-onnx speaker embedding),
    #: clustered per session only -- never stored across sessions.
    voiceprint_enabled: bool = _get_bool("VOICEPRINT_ENABLED", False)
    voiceprint_model: str = os.getenv("VOICEPRINT_MODEL", "")
    #: Direction of arrival from the robot's mic array (the face app reads
    #: the 8-channel Bothlent array natively and reports TDOA features).
    doa_enabled: bool = _get_bool("DOA_ENABLED", False)
    # ---- the array as the conversation's ears (app/array_audio.py) ----
    # Owner, 9 Oct 2026: "คุยได้แบบไม่ติดขัดแม้จะมีคนเยอะ". Every switch below
    # is OFF by default and needs DOA_ENABLED (the link it rides on, and the
    # calibration it steers with): the sales room has no array and must stay
    # byte-identical, doa_ready included.
    #: The robot app beamforms channels 0-3 toward the focus target (the
    #: engaged face that last spoke, else the nearest engaged face) and sends
    #: the mono 16 kHz result up /ws/robot-doa; the robot face's session uses
    #: it instead of the page's mic while it is live, and falls back to the
    #: page the moment it is not (app/array_audio.py). The array has no AEC:
    #: nothing it hears is forwarded while Emma's audio may be playing.
    array_audio: bool = _get_bool("ARRAY_AUDIO", False)
    #: ds = delay-and-sum: cannot hurt the target, but buys little on a ~6 cm
    #: array (synthetic rooms: +0.1..+0.4 dB SIR, +1.0..+1.4 dB SNR).
    #: mvdr = noise covariance learned on hops the target is not talking in:
    #: +5.7..+9.0 dB SIR on a 4-mic circle, +1.2..+1.8 on a line -- and on a
    #: line steered 8-15 degrees wrong the target loses 1-2.6 dB. The real
    #: geometry is unknown: A/B on the robot (app/array_audio.py has steps).
    array_audio_beam: str = os.getenv("ARRAY_AUDIO_BEAM", "ds").strip().lower()
    #: What the array sends with no target (no calibration yet, nobody in
    #: front): mic0 (one raw microphone) or average (the plain mean of four).
    #: mic0 by default: four UNALIGNED mics averaged comb-filter the voice --
    #: log-spectral distance 12-19 dB on the synthetic circle vs 0.2 steered.
    array_audio_unsteered: str = os.getenv("ARRAY_AUDIO_UNSTEERED", "mic0").strip().lower()
    #: Gain on the array stream before it reaches the VAD (its level is not
    #: the page's: no browser chain). Tune from `array_report` rows
    #: (page_rms vs array_rms of the same moments), never from theory.
    array_audio_gain: float = float(os.getenv("ARRAY_AUDIO_GAIN", "1.0"))
    #: Array audio older than this (no chunk arrived) = the stream is not
    #: live: the next page frame goes up instead. 120, not the first 60
    #: (review 10 Oct): "older" is measured when the event loop gets to the
    #: page frame, so ANY loop stall longer than this reads as a dead array
    #: -- the page frames and beam chunks queued during the stall are
    #: processed in socket order, and the page frame first sees a beam clock
    #: that stopped when the loop did. 60 ms is under four 16 ms hops and
    #: well under asyncio's own 100 ms "slow callback" bar; on the robot the
    #: server shares an RK3588 with face, ASD and DOA threads under one GIL.
    #: A false fallback is not lost audio (the page frame goes up at once)
    #: but it splices microphones mid-utterance, holds the page mic until the
    #: next boundary, and puts `fallback` in array_report -- the very count
    #: the robot A/B reads as "the array is flaky". 120 = PAGE_GAP_S, the
    #: tap's bar for "the PAGE stream stopped" on the same loop; the price is
    #: a real death's hole of up to ~140 ms instead of ~80. Not measured on
    #: the robot yet: tune from array_report `max_gap_ms` (the longest chunk
    #: gap the server saw since the last row), never from theory.
    array_audio_stale_ms: int = int(os.getenv("ARRAY_AUDIO_STALE_MS", "120"))
    #: After Emma's audio (the server's own playback clock), the array stays
    #: closed this long. Shorter than the page's own 350 ms echo guard on
    #: purpose: the page reopening is then the moment the array comes back.
    array_audio_echo_tail_s: float = float(os.getenv("ARRAY_AUDIO_ECHO_TAIL_S", "0.25"))
    #: Debug only (channels 4-7 question): with this on, an adb broadcast on
    #: the robot uploads N seconds of the RAW 8-channel capture to
    #: data/array_capture8/ARRAY8-TEST-*.s16le, deleted ARRAY_CAPTURE8_KEEP_MIN
    #: minutes after it was made. Room audio on disk: on only for the test.
    array_capture8: bool = _get_bool("ARRAY_CAPTURE8", False)
    array_capture8_keep_min: float = float(os.getenv("ARRAY_CAPTURE8_KEEP_MIN", "10"))
    #: Light speech enhancement (sherpa-onnx GTCRN, 16 kHz, streaming) on the
    #: call stream before the VAD. Off: measured (scripts/eval_enhance.py) it
    #: rescues Silero in fan/HVAC-type noise but not against other people's
    #: speech -- the robot's problem -- and made babble+noise at 0 dB worse
    #: (app/enhance.py has the numbers). Never together with the page's NS.
    enhance_enabled: bool = _get_bool("ENHANCE_ENABLED", False)
    enhance_model: str = os.getenv("ENHANCE_MODEL", "data/enhance/gtcrn_simple.onnx")
    #: Near-field floor exceptions. Grace preserves a wake word tail while a
    #: session opens; summoned bypass lets a camera-opened conversation hear
    #: a reply from the doorway. A robot intended to hear only nearby people
    #: sets both to 0/false after measuring its own mic.
    vad_floor_grace_s: float = float(os.getenv("VAD_FLOOR_GRACE_S", "3"))
    vad_summoned_bypass: bool = _get_bool("VAD_SUMMONED_BYPASS", True)
    # OpenAI only: semantic_vad waits on whether the sentence sounds finished.
    openai_turn_detection: str = os.getenv("OPENAI_TURN_DETECTION", "semantic_vad")
    openai_vad_eagerness: str = os.getenv("OPENAI_VAD_EAGERNESS", "medium")

    # --- Project identity ---
    # Write this exactly as it should be spoken. The model repeats it aloud,
    # and a vague or lowercased name is one it will happily "correct" into
    # something that sounds similar.
    project_name: str = os.getenv("PROJECT_NAME", "[ชื่อโครงการ]")
    # Optional name for the robot itself, so it can introduce itself the way
    # a receptionist would. Leave blank for a generic introduction.
    robot_name: str = os.getenv("ROBOT_NAME", "")

    # Which languages the assistant may reply in. "auto" (default) lets it
    # answer in whatever language the guest used — Gemini's native-audio
    # models cover ~97 and switch mid-conversation on their own. Restrict
    # with a comma list (e.g. "th,en") if sales staff can only follow up in
    # certain languages; the first entry is the one it opens with.
    reply_languages: str = os.getenv("REPLY_LANGUAGES", "auto")

    # How the *on-screen transcript* picks a language (Gemini only). Separate
    # from what the assistant can speak — the model is speech-to-speech and
    # understands the guest's audio directly, so this only affects captions
    # and can never stop the robot replying in some language.
    #
    # These are hints, not a whitelist: detection still runs, biased toward
    # what's listed. Left on "auto" the recogniser guessed English for Thai
    # speech and romanised it — the guest's own words came back as "ao rummy".
    # Thai leads because the gallery is in Thailand; en/zh follow because
    # those are the walk-ins. Add codes for others (ru-RU, ja-JP, ko-KR) if
    # their captions come out wrong; the reply language is unaffected either
    # way. "auto" restores full detection.
    transcribe_languages: str = os.getenv("TRANSCRIBE_LANGUAGES", "th-TH,en-US,zh-CN")

    # `SMART` removes filler/repeated false starts, applies light cleanup and
    # resolves an inline self-correction before the automatic transcript is
    # shown. It remains a separate ASR result, not the native-audio model's
    # private interpretation of the sound. `VERBATIM` keeps every hesitation
    # when an operator needs the raw recogniser output for diagnosis.
    transcribe_mode: str = os.getenv("TRANSCRIBE_MODE", "SMART").strip().upper()

    # Opt-in. The recogniser puts spaces between Thai words ("เอา ทุก คน
    # เลย"); Thai isn't written that way. Turning this on collapses a space
    # whenever both sides are Thai, so "โครงการ Embassy World" is safe.
    #
    # Off by default because it cannot tell word-spacing from the spaces Thai
    # legitimately uses at clause boundaries, and removes those too:
    # "ขอโทษค่ะ ยังไม่มีข้อมูลราคา" -> "ขอโทษค่ะยังไม่มีข้อมูลราคา". Fixing
    # the language hints (TRANSCRIBE_LANGUAGES) addresses the root cause;
    # reach for this only if word-spacing survives that.
    thai_spacing: bool = os.getenv("THAI_SPACING", "false").lower() == "true"

    # --- slide search ---
    # Semantic (embedding) search over the slide library, alongside BM25.
    # This is what lets a question find a slide that doesn't share its words:
    # สระว่ายน้ำ -> "SKY POOL", or a Chinese guest's 游泳池 -> anything at all.
    # Uses the same GEMINI_API_KEY; the 144 slides are embedded once and
    # cached, so the running cost is one short embedding per lookup. Turn off
    # to run purely lexically.
    search_semantic: bool = os.getenv("SEARCH_SEMANTIC", "true").lower() != "false"
    # Cosine similarity thresholds. Unlike BM25 these are absolute (0–1), so
    # a fixed number means something. Defaults are conservative starting
    # points — run scripts/eval_search.py against your own deck to see where
    # the gap between related and unrelated actually falls, then set these.
    search_min_similarity: float = float(os.getenv("SEARCH_MIN_SIMILARITY", "0.653"))
    search_show_similarity: float = float(os.getenv("SEARCH_SHOW_SIMILARITY", "0.68"))
    # Local sentence-transformer cosine scores live on a different scale from
    # Gemini embeddings.  Keeping separate defaults prevents switching to the
    # offline backend from silently rejecting otherwise good multilingual hits.
    search_local_min_similarity: float = float(
        os.getenv("SEARCH_LOCAL_MIN_SIMILARITY", "0.389")
    )
    search_local_show_similarity: float = float(
        os.getenv("SEARCH_LOCAL_SHOW_SIMILARITY", "0.46")
    )
    search_local_min_coverage: float = float(
        os.getenv("SEARCH_LOCAL_MIN_COVERAGE", "0.34")
    )
    project_knowledge_file: str = os.getenv(
        "PROJECT_KNOWLEDGE_FILE", str(source_path("project_vocabulary", ACTIVE_PROJECT_ID))
    )
    # Optional cross-encoder second stage. Empty keeps startup light; set this
    # after benchmarking the target robot's CPU/RAM.
    search_reranker_model: str = os.getenv("SEARCH_RERANKER_MODEL", "")
    search_reranker_candidates: int = int(os.getenv("SEARCH_RERANKER_CANDIDATES", "8"))

    def __post_init__(self) -> None:
        # Before the condo-only checks below, which return early for every
        # other profile — the robot face runs `joke`, and the boot rule must
        # hold there most of all.
        self._apply_body_nav_boot_rules()
        if self.assistant_profile != "condo":
            return
        identity = project_identity(self.project_id)
        if self.project_name != identity["display_name"]:
            raise ValueError("PROJECT_NAME does not match PROJECT_ID")
        if self.inventory_project != identity["inventory_slug"]:
            raise ValueError("INVENTORY_PROJECT does not match PROJECT_ID")

    def _apply_body_nav_boot_rules(self) -> None:
        """Normalise the two mode switches and force the nav panel off
        while routes are off, loudly.

        The panel with the old chassis planner still live is two navigation
        systems on one set of wheels (design §2h, safety review #3). A
        warning and a forced `off`, not a refusal to boot: the
        `warn_if_open_to_the_network` precedent — a showroom that will not
        start twenty minutes before opening is worse than one that starts
        loudly with the risky part switched off. A typo reads as `off`, the
        same rule as an unknown profile reading as condo.
        """
        import logging

        log = logging.getLogger("condo_voice.config")
        notes: list[str] = []
        voice = (self.robot_nav_voice or "").strip().lower()
        if voice not in NAV_VOICE_MODES:
            notes.append("ROBOT_NAV_VOICE=%r is not off/unlocked/on — using off" % voice)
            voice = "off"
        self.robot_nav_voice = voice
        panel = (self.robot_nav_panel or "").strip().lower()
        if panel not in NAV_PANEL_MODES:
            notes.append("ROBOT_NAV_PANEL=%r is not off/pin/open — using off" % panel)
            panel = "off"
        if panel != "off" and not self.robot_nav_routes_enabled:
            notes.append(
                "SAFETY: ROBOT_NAV_PANEL=%s FORCED OFF because "
                "ROBOT_NAV_ROUTES_ENABLED=false — taps through the panel while "
                "the chassis planner still takes moves would be two navigation "
                "systems on one set of wheels. Turn routes on to use the panel."
                % panel)
            panel = "off"
        self.robot_nav_panel = panel
        self.nav_boot_warnings = notes
        for line in notes:
            log.warning(line)

    def nav_panel_mode(self) -> str:
        """The นำทาง slot's mode as it may be used *now*.

        The boot rule above runs once, on .env values; a value set later (a
        test, a console) would skip it. Every reader goes through here, so
        "panel on while routes off" cannot happen at runtime either.
        """
        panel = (self.robot_nav_panel or "").strip().lower()
        if panel not in NAV_PANEL_MODES or not self.robot_nav_routes_enabled:
            return "off"
        return panel

    def nav_voice_mode(self) -> str:
        """`ROBOT_NAV_VOICE` as it may be used now; anything else is `off`."""
        voice = (self.robot_nav_voice or "").strip().lower()
        return voice if voice in NAV_VOICE_MODES else "off"

    def api_key_for(self, provider: str | None = None) -> str | None:
        return self.gemini_api_key if (provider or self.provider) == "gemini" else self.openai_api_key

    def enabled_tool_groups(self) -> set[str] | None:
        """None means "all groups"; a set restricts to those named.

        With TOOL_GROUPS blank, the profile decides the default. Emma gets
        `smarthome` only: slides, documents, knowledge and the Astronaut
        robot are gallery equipment, and a tool the model can see is a tool
        it will eventually call — Emma with `start_presentation` would
        narrate a condo deck in the owner's living room. An explicit
        TOOL_GROUPS still wins, so trying tools out on either profile stays
        a one-line .env change.
        """
        if not self.tools_enabled:
            return set()
        groups = {g.strip() for g in self.tool_groups.split(",") if g.strip()}
        base: set[str] | None
        if groups:
            base = groups
        elif self.assistant_profile == "emma":
            base = {"smarthome", "reminders", "memory", "mydocs",
                    "websearch", "computer"}
        elif self.assistant_profile == "translator":
            # An interpreter has one job. A tool the model can see is a tool
            # it will eventually call — mid-translation.
            base = set()
        else:
            base = None
        if self.multi_session:
            # Decided here and nowhere else, because everything reads this
            # method — load_tools, the boot warnings, the prompt suffixes,
            # the reminder rearm hook. A second decision point is how the
            # config-vs-gate drift bug happens.
            if base is None:
                return set(MULTI_SESSION_DEFAULT)
            return base & MULTI_SESSION_SAFE
        return base


settings = Settings()
