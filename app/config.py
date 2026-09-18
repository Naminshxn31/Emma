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


@dataclass
class Settings:
    project_id: str = ACTIVE_PROJECT_ID
    # Which persona this server runs: "condo" (the gallery receptionist,
    # default) or "emma" (the owner's personal assistant). One repo, two
    # hats — forking was rejected on purpose, because twenty-plus fixed bugs
    # (Thai tokenisation, audio lead, mishearings) would need fixing twice.
    assistant_profile: str = os.getenv("ASSISTANT_PROFILE", "condo").strip().lower()

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

    @property
    def call_mic_boost(self) -> float:
        return float(self.call_mic_boost_raw) if self.call_mic_boost_raw else self.mic_boost

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
    # person would click through it. Only slides from the deck that was
    # exported to build this URL can be synced (id `ew-042` -> that deck's
    # page 42); anything else on screen just doesn't move this window.
    # Leave CANVA_URL blank to skip all of this — nothing below runs.
    canva_url: str = os.getenv("CANVA_URL", "")
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
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-native-audio-preview-12-2025")
    gemini_voice: str = os.getenv("GEMINI_VOICE", "Kore")
    #: Model to use if GEMINI_MODEL cannot be opened. Blank disables it.
    #:
    #: The gallery runs a `-preview` model, and preview means Google may
    #: withdraw it, rename it, or tighten its limits without much notice. On
    #: the day that happens the robot goes silent for the whole day and the
    #: only trace is a traceback nobody is watching. A slightly older voice is
    #: a much smaller problem than a receptionist that does not answer.
    #:
    #: Only used for errors that say the *model* is unavailable — see
    #: `_model_is_unavailable`. A dropped connection still fails loudly.
    gemini_model_fallback: str = os.getenv(
        "GEMINI_MODEL_FALLBACK", "gemini-2.5-flash-native-audio-preview-12-2025")
    # Adapts the reply's tone to the guest's tone. Native-audio 2.5 only, and
    # needs the v1beta endpoint — ignored automatically on models without it.
    gemini_affective_dialog: bool = _get_bool("GEMINI_AFFECTIVE_DIALOG", True)
    # Lets the model stay quiet when speech clearly isn't aimed at it — useful
    # for a robot standing in a busy gallery. Native-audio 2.5 only.
    gemini_proactive_audio: bool = _get_bool("GEMINI_PROACTIVE_AUDIO", False)

    # Gemini 2.5 native audio enables *dynamic thinking by default*, which
    # adds a noticeable pause before the robot starts talking. A receptionist
    # answering from a fixed fact sheet doesn't need it, so default to 0
    # (off). Raise it if answers start feeling shallow.
    gemini_thinking_budget: int = int(os.getenv("GEMINI_THINKING_BUDGET", "0"))
    # Gemini 3.x Live uses levels instead of a token budget.
    gemini_thinking_level: str = os.getenv("GEMINI_THINKING_LEVEL", "minimal")

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
    # speaking. Kills barge-in, but it's the reliable cure when speaker
    # output feeds back into the mic (laptop speakers, no headphones, no
    # hardware echo cancellation). Prefer headphones or a robot with
    # hardware AEC and leave this off.
    half_duplex: bool = _get_bool("HALF_DUPLEX", False)

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
        if self.assistant_profile != "condo":
            return
        identity = project_identity(self.project_id)
        if self.project_name != identity["display_name"]:
            raise ValueError("PROJECT_NAME does not match PROJECT_ID")
        if self.inventory_project != identity["inventory_slug"]:
            raise ValueError("INVENTORY_PROJECT does not match PROJECT_ID")

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
