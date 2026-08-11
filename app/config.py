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

load_dotenv()


def _get_list(name: str, default: list[str]) -> list[str]:
    val = os.getenv(name)
    if not val:
        return default
    return [item.strip() for item in val.split(",") if item.strip()]


def _get_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # --- Tools (things the assistant can actually do) ---
    tools_enabled: bool = _get_bool("TOOLS_ENABLED", True)
    #: Blank = every group. Otherwise a comma list, e.g. "smarthome".
    tool_groups: str = os.getenv("TOOL_GROUPS", "")

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
    slides_dir: str = os.getenv("SLIDES_DIR", "data/slides")
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
    canva_open_at_start: bool = _get_bool("CANVA_OPEN_AT_START", False)

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


    # --- Provider selection ---
    provider: str = os.getenv("VOICE_PROVIDER", "gemini")  # gemini | openai

    # --- Gemini Live (free tier available) ---
    gemini_api_key: str | None = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-native-audio-preview-12-2025")
    gemini_voice: str = os.getenv("GEMINI_VOICE", "Kore")
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

    def api_key_for(self, provider: str | None = None) -> str | None:
        return self.gemini_api_key if (provider or self.provider) == "gemini" else self.openai_api_key

    def enabled_tool_groups(self) -> set[str] | None:
        """None means "all groups"; a set restricts to those named."""
        if not self.tools_enabled:
            return set()
        groups = {g.strip() for g in self.tool_groups.split(",") if g.strip()}
        return groups or None


settings = Settings()
