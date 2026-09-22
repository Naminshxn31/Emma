"""
Gemini Live API provider — speech-to-speech with a free tier.

Uses the `google-genai` SDK's `client.aio.live.connect()`. Audio in is PCM16
**16 kHz** (note: not 24 kHz like OpenAI), audio out is PCM16 24 kHz.

Interruption is simpler here than with OpenAI: when the guest talks over the
model, the server cancels generation itself and reports
`server_content.interrupted`. We just stop playback — there's no truncate
message to send back, because Gemini already discarded the unsent audio.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from app.config import settings
from app.providers.base import ProviderError, ProviderEvent, VoiceProvider
from app.providers.thai_spacing import ThaiSpacing
from app.providers.transcript_filter import TranscriptFilter

logger = logging.getLogger("condo_voice.gemini")


def _sensitivity(enum_cls, prefix: str, value: str):
    """Map 'LOW'/'HIGH' from .env onto the SDK's sensitivity enums."""
    name = f"{prefix}_SENSITIVITY_{(value or '').strip().upper()}"
    return getattr(enum_cls, name, getattr(enum_cls, f"{prefix}_SENSITIVITY_UNSPECIFIED"))


#: Words that are English but not names — they turn up inside slide titles
#: and marketing slogans and are no use as vocabulary hints.
_NOT_A_NAME = {
    "ALL", "BUILD", "BETTER", "THE", "AND", "FOR", "FLOOR", "PLAN", "AWARDS",
    "LIFE", "NEW", "QUESTION", "IS", "HOW", "WE", "DO", "MORE", "BUILDINGS",
    "GROUND", "BASEMENT", "ENTRANCE", "UNITS", "TOWER", "TYPE", "ROOM",
    "VIEW", "CONCEPT", "PRELIMINARY", "DREAM", "EVERYBODY", "HAS", "NOT",
    "JUST", "ONE", "PLACE", "THREE", "ZONES", "JUNIOR", "UNDER", "HERE",
}


def supports_native_audio_extras(model: str | None = None) -> bool:
    """Affective dialogue and proactive audio are native-audio 2.5 features."""
    return "native-audio" in (model or settings.gemini_model)


def supports_non_blocking(model: str | None = None) -> bool:
    """Can this model run a tool *while* it keeps talking?

    Gemini 2.5 and 3.8 Live accept `Behavior.NON_BLOCKING`. Gemini 3.1 Live
    predates asynchronous function calling and must wait for tool results.
    """
    name = (model or settings.gemini_model).removeprefix("models/")
    return not name.startswith("gemini-3.1-")


def facility_names() -> list[str]:
    """The project's own English facility names, read out of the deck.

    A guest asked for the **ice bath**. The recogniser — biased toward Thai,
    because that's the language most of them speak — heard "ไอ้บ้า", which is
    an insult, and the robot apologised for having offended them.

    These names are the project's vocabulary and they're already written down
    in the slide index, so the deck supplies them and stays current as slides
    are added. Same idea as the tokeniser's custom dictionary.
    """
    import re

    from app.tools.slides import load_slides

    found: dict[str, str] = {}
    for slide in load_slides():
        for field in ("title_en", "title_th"):
            text = slide.get(field) or ""
            for match in re.findall(r"\b[A-Z][A-Z0-9&]{2,}(?:\s+[A-Z][A-Z0-9&]{1,})*\b", text):
                words = [w for w in match.split() if w not in _NOT_A_NAME]
                if not words or len(words) > 3:
                    continue
                name = " ".join(words)
                if len(name) >= 4:
                    found.setdefault(name.lower(), name)
    return sorted(found.values())


def adaptation_phrases() -> list[str]:
    """Words the transcriber should expect, so it stops inventing homophones.

    Untuned, Gemini rendered the Thai for "turn off the light" as "bit fire"
    and the project name as "Ambassador World". These are the terms that
    actually come up at a sales-gallery desk.
    """
    phrases = [settings.project_name]
    if settings.robot_name:
        phrases.append(settings.robot_name)
    try:
        phrases += facility_names()
    except Exception:
        logger.warning("could not read facility names from the deck", exc_info=True)
    phrases += [
        # Commands wired to real hardware — worth getting right.
        "เปิดไฟ", "ปิดไฟ", "เปิดแอร์", "ปิดแอร์",
        "ปรับอุณหภูมิ", "ลดอุณหภูมิ", "เพิ่มอุณหภูมิ", "องศา",
        "ความแรงลม", "พัดลม",
        # Sales vocabulary.
        "โครงการ", "คอนโด", "ห้องตัวอย่าง", "ห้องขาย",
        "ราคา", "โปรโมชั่น", "ผังห้อง", "แบบห้อง",
        "สิ่งอำนวยความสะดวก", "ทำเลที่ตั้ง", "พาชม", "นัดหมาย",
        "ห้องนอน", "ตารางเมตร", "ชั้น",
        # Unit-conversation phrases, added after a measured mishearing:
        # "แต่ละห้องต่างกันยังไง" came back as "เตารีดผ้ากันยังไง" and the
        # robot went off researching how to iron. Biasing the recogniser
        # toward the sentences people actually say at this desk is the same
        # fix that turned "bit fire" back into ปิดไฟ.
        "แต่ละห้อง", "ต่างกันยังไง", "ห้องว่าง", "ผังโครงการ", "ผังชั้น",
        "ตึกไหน", "ห้องมุม", "วิวทะเล", "วิวสระ", "งบเท่าไหร่", "กี่ล้าน",
    ]
    return [p for p in phrases if p and not p.startswith("[")]


def language_codes() -> list[str]:
    """BCP-47 hints for the recogniser, from TRANSCRIBE_LANGUAGES.

    Empty list == omit the field == full auto-detection.
    """
    value = (settings.transcribe_languages or "").strip().lower()
    if value in {"auto", "any", "all", ""}:
        return []
    return [c.strip() for c in settings.transcribe_languages.split(",") if c.strip()]


def _transcription_config():
    """Input transcription, tuned for readable intent-level captions.

    Two things to know about this config, both learned the hard way:

    1. **It only produces captions.** The model is speech-to-speech; it
       understands the guest's audio directly and picks its reply language
       from that. Nothing here can restrict what languages the robot speaks.
       So constraining the recogniser is cheap — it costs caption quality for
       unlisted languages and nothing else.

    2. **Codes are hints, not a whitelist.** Per the SDK: "BCP-47 language
       codes providing hints about the languages present in the audio."
       Detection still runs; it's just biased. Which is exactly what was
       missing — with no hints at all, Thai speech was being decoded by an
       English recogniser and came back romanised ("ao rummy"), so the panel
       showed the guest saying words in a language they never spoke.

    `language_codes`, `custom_vocabulary`, and `mode` are the current fields.
    `language_hints`, `language_auto` and `adaptation_phrases` all still
    exist but are marked Deprecated — they're used only
    as a fallback for older SDKs, and a config the server rejects would kill
    the whole session over a caption, so the last resort is no hints at all.
    """
    from google.genai import types

    codes = language_codes()
    phrases = adaptation_phrases()
    mode = (settings.transcribe_mode or "SMART").strip().upper()
    if mode not in {"SMART", "VERBATIM"}:
        logger.warning("unknown TRANSCRIBE_MODE=%r; using SMART", mode)
        mode = "SMART"

    # Current field names.
    try:
        kwargs: dict = {"mode": mode}
        if codes:
            kwargs["language_codes"] = codes
        if phrases:
            kwargs["custom_vocabulary"] = phrases
        cfg = types.AudioTranscriptionConfig(**kwargs)
        logger.info(
            "input transcription: mode=%s languages=%s vocabulary=%d phrase(s)",
            mode, ",".join(codes) if codes else "auto-detect", len(phrases),
        )
        return cfg
    except Exception:
        logger.warning("current transcription fields rejected; trying legacy", exc_info=True)

    # Pre-2.16 SDKs.
    try:
        kwargs = {}
        if codes:
            kwargs["language_hints"] = types.LanguageHints(language_codes=codes)
        if phrases:
            kwargs["adaptation_phrases"] = phrases
        return types.AudioTranscriptionConfig(**kwargs)
    except Exception:
        logger.warning(
            "transcription hints not accepted by this SDK version — "
            "falling back to plain transcription", exc_info=True,
        )
        return types.AudioTranscriptionConfig()


#: Substrings that mean "this model can't be used", as opposed to "the
#: network hiccuped". Matched case-insensitively against the exception text.
#:
#: Deliberately narrow. Falling back on any error at all would turn a five
#: second outage into a silent, permanent downgrade that nobody investigates,
#: and the gallery would find out months later that it had been running the
#: older voice the whole time.
_UNAVAILABLE_SIGNS = (
    "not found",
    "not_found",
    "404",
    "is not supported",
    "does not exist",
    "not available",
    "unsupported model",
    "invalid model",
    # Quota counts: per-model limits are separate, so a different model may
    # well answer. A rate-limited robot is as silent as a missing one.
    "resource_exhausted",
    "429",
    "quota",
)


def _model_is_unavailable(exc: Exception) -> bool:
    """Is this the model's fault, or the network's?

    The distinction is the whole safety of the fallback. `-preview` models get
    withdrawn, renamed, and have their limits tightened with little notice, and
    when that happens the robot goes quiet for a whole day with a traceback
    nobody reads. That is worth switching models for. A dropped socket is not.
    """
    text = str(exc).lower()
    return any(sign in text for sign in _UNAVAILABLE_SIGNS)


class GeminiProvider(VoiceProvider):
    input_sample_rate = 16000
    output_sample_rate = 24000
    session_limit_minutes = 15  # audio-only cap for Live API sessions
    auto_resumes = True          # ...but we rejoin with a handle past it

    def __init__(self, voice: str, instructions: str, greeting: str | None = None,
                 use_tools: bool = True) -> None:
        super().__init__(voice, instructions)
        #: Spoken once, on the *initial* connect only. This parameter rode in
        #: from session.py since the beginning and was silently dropped here
        #: — only the OpenAI provider ever used it — so on Gemini the robot
        #: never announced itself. What looked like a wake greeting all along
        #: was the model reacting to the wake word's tail audio, and the day
        #: the VAD near-field floor ate that quiet tail (2026-08-26), waking
        #: Emma produced pure silence: chime, then nothing, and the owner
        #: concluded she wasn't awake. A knob wired to nothing is this
        #: project's signature misconfiguration; this one held a first
        #: impression.
        self.greeting = greeting
        #: The translator profile runs toolless BY CONFIG, not by asking the
        #: prompt nicely — a declared tool is a callable tool, whatever the
        #: instructions say, and an interpreter calling set_lights
        #: mid-sentence is not a theoretical failure in this codebase.
        self.use_tools = use_tools
        #: Which model this session actually opened with.
        #:
        #: An instance attribute rather than `settings.gemini_model` read at
        #: six call sites, because the session can end up on a *different*
        #: model than the one configured — see `__aenter__`. With the setting
        #: read directly, a fallback would connect to one model and then
        #: configure itself for another: the v1beta endpoint, the thinking
        #: field and the affective-dialogue warning are all chosen by model
        #: name, so they would all describe the model that wasn't running.
        self.model = settings.gemini_model
        self._session = None
        self._cm = None
        # Affective dialogue mixes emotion labels into the output transcript;
        # they're internal signals, not speech, so keep them off the screen.
        self._out_filter = TranscriptFilter()
        #: Local VAD gate, or None for Gemini-side detection. Created here —
        #: before _build_config runs — because the config's
        #: automatic_activity_detection block has to describe the arrangement
        #: this session actually uses. Deciding it in two places is how the
        #: two ends would drift: a gate sending activity signals into a
        #: session whose server-side detection is still on double-detects
        #: every utterance.
        from app import vad_gate

        self._vad_gate = vad_gate.for_session()
        # The recogniser word-spaces Thai; undo that for display. Separate
        # instances because the two transcript streams interleave and each
        # needs its own held-back character.
        self._in_spacing = ThaiSpacing(enabled=settings.thai_spacing)
        self._out_spacing = ThaiSpacing(enabled=settings.thai_spacing)
        # Live audio sessions are capped (~15 min). The server hands out a
        # resumption handle during the session and a `go_away` shortly before
        # cutting the socket; reconnecting with the latest handle continues
        # the same conversation past the cap. None until the server sends one
        # — an endpoint that doesn't support resumption simply never does, and
        # the reconnect below stays dormant. `_closing` suppresses the
        # reconnect when *we* are the ones shutting down.
        self._resume_handle: str | None = None
        self._closing = False
        # One event stream merges upstream audio, local speech boundaries and
        # tool results. Tools keep their order without holding up socket reads.
        self._event_queue = asyncio.Queue(maxsize=64)
        self._tool_batches = asyncio.Queue(maxsize=32)
        self._event_tasks: list[asyncio.Task] = []
        self._input_serial = 0
        self._input_turn_id: str | None = None
        self._input_finished: bool | None = None
        self._pending_tool_ids: set[str] = set()
        self._cancelled_tool_ids: set[str] = set()

    def _build_config(self):
        from google.genai import types

        config: dict = {
            "response_modalities": [types.Modality.AUDIO],
            "system_instruction": self.instructions,
            "speech_config": types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.voice)
                )
            ),
            # Both transcripts are for the on-screen captions only — the model
            # itself works directly on audio, which is why it still switched
            # the lights off correctly while the caption read "bit fire".
            "input_audio_transcription": _transcription_config(),
            "output_audio_transcription": types.AudioTranscriptionConfig(),
            "realtime_input_config": types.RealtimeInputConfig(
                # Two shapes, chosen by who is detecting speech, and they must
                # not mix: with `disabled=True` the Live API *rejects the
                # session* (1007) if any tuning field rides along —
                # "disabled is true, but the following fields were also set".
                # Seen live the first time local VAD ran. When the local gate
                # detects, the .env knobs drive the gate instead (vad_gate
                # reuses VAD_SILENCE_MS / PREFIX_PADDING_MS), so the robot's
                # patience survives the mode switch.
                automatic_activity_detection=(
                    types.AutomaticActivityDetection(disabled=True)
                    if self._vad_gate is not None
                    else types.AutomaticActivityDetection(
                        disabled=False,
                        prefix_padding_ms=settings.vad_prefix_padding_ms,
                        silence_duration_ms=settings.vad_silence_ms,
                        # HIGH = detect start of speech more often (the API's
                        # own default). LOW detects it *less* often — quiet or
                        # short utterances never picked up. See config.py.
                        start_of_speech_sensitivity=_sensitivity(
                            types.StartSensitivity, "START", settings.vad_start_sensitivity
                        ),
                        end_of_speech_sensitivity=_sensitivity(
                            types.EndSensitivity, "END", settings.vad_end_sensitivity
                        ),
                    )
                )
            ),
            # Keeps a long conversation inside the context window instead of
            # dying when it fills up.
            "context_window_compression": types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow()
            ),
            # Ask the server for a resumption handle so the session can be
            # rejoined after the audio-duration cap. On a fresh connect the
            # handle is None ("start me, and give me a handle"); on a reconnect
            # `_build_config` carries the last handle we were given, which is
            # what continues the conversation rather than starting over.
            "session_resumption": types.SessionResumptionConfig(
                handle=self._resume_handle
            ),
        }

        from app import tools

        if self.use_tools:
            tools.load_tools()
            gemini_tool = tools.as_gemini_tool()
            if gemini_tool is not None:
                config["tools"] = [gemini_tool]
        if settings.web_search:
            # Native grounding: Google runs the search, the model reads the
            # results. Behind a default-off flag because it has never been
            # verified against this live model — see WEB_SEARCH in config.
            config.setdefault("tools", []).append(
                types.Tool(google_search=types.GoogleSearch())
            )

        # Thinking configuration differs between the Live model families.
        # Gemini 3.8 Live performs interleaved reasoning internally and
        # rejects *any* thinking_config in session setup. The Extended
        # Thinking variant accepts levels (but not "minimal"), Gemini 3.1
        # accepts levels including "minimal", and 2.5 accepts a token budget.
        # Normalize the optional models/ prefix before choosing the shape.
        model_name = self.model.removeprefix("models/")
        if model_name == "gemini-3.8-live":
            pass
        elif model_name == "gemini-3.8-live-extended-thinking":
            level = (settings.gemini_thinking_level or "low").strip().lower()
            if level == "minimal":
                logger.warning(
                    "GEMINI_THINKING_LEVEL=minimal is not supported by %s; "
                    "using low instead.", self.model,
                )
                level = "low"
            config["thinking_config"] = types.ThinkingConfig(thinking_level=level)
        elif model_name.startswith("gemini-3"):
            config["thinking_config"] = types.ThinkingConfig(
                thinking_level=settings.gemini_thinking_level
            )
        else:
            config["thinking_config"] = types.ThinkingConfig(
                thinking_budget=settings.gemini_thinking_budget
            )

        # Native-audio 2.5 extras. Guarded because other Live models reject
        # them — but a guard that drops a setting in silence is its own bug.
        # .env asking for affective dialog and simply not getting it is the
        # same failure as the OpenAI session config being rejected quietly:
        # the system behaves differently from its own configuration and
        # nothing says so. Ask for something this model can't do and you get
        # told, at startup, once.
        if supports_native_audio_extras(self.model):
            if settings.gemini_affective_dialog:
                config["enable_affective_dialog"] = True
            if settings.gemini_proactive_audio:
                config["proactivity"] = types.ProactivityConfig(proactive_audio=True)
        else:
            for name, wanted in (
                ("GEMINI_AFFECTIVE_DIALOG", settings.gemini_affective_dialog),
                ("GEMINI_PROACTIVE_AUDIO", settings.gemini_proactive_audio),
            ):
                if wanted:
                    logger.warning(
                        "%s=true is set but %s is not a native-audio model, so "
                        "it is being IGNORED. Either switch to a "
                        "*-native-audio-* model or set %s=false so the config "
                        "matches what actually runs.",
                        name, self.model, name,
                    )

        return config

    async def _connect(self) -> None:
        """Open one Live session with the current config (handle included)."""
        from google import genai

        # Affective dialog / proactive audio are v1beta-only features.
        http_options = {"api_version": "v1beta"} if "native-audio" in self.model else None
        client = genai.Client(api_key=settings.gemini_api_key, http_options=http_options)
        self._cm = client.aio.live.connect(
            model=self.model, config=self._build_config()
        )
        self._session = await self._cm.__aenter__()

    async def __aenter__(self) -> GeminiProvider:
        if not settings.gemini_api_key:
            raise ProviderError(
                "GEMINI_API_KEY is not set. Get a free key at "
                "https://aistudio.google.com/apikey and put it in .env"
            )
        try:
            await self._connect()
        except Exception as exc:
            fallback = (settings.gemini_model_fallback or "").strip()
            if fallback and fallback != self.model and _model_is_unavailable(exc):
                # The robot standing silent all day is a worse outcome than the
                # robot sounding slightly different for an afternoon.
                #
                # This only fires for errors that say *this model* can't be
                # used — not for a dropped connection. A network blip must
                # fail loudly and be retried on the model that was chosen;
                # falling back on it would quietly downgrade the gallery and
                # leave nothing to notice.
                logger.error(
                    "MODEL FALLBACK: %s is unavailable (%s) — switching to %s "
                    "for this session. The robot will still work; check whether "
                    "the configured model is unavailable for this key or region.",
                    self.model, exc, fallback,
                )
                from app import turnlog

                turnlog.record("model_fallback", wanted=self.model,
                               using=fallback, reason=str(exc)[:200])
                self.model = fallback
                try:
                    await self._connect()
                    await self._send_greeting()
                    return self
                except Exception as second:
                    raise ProviderError(
                        "Could not open a Gemini Live session on %s or the "
                        "fallback %s: %s" % (settings.gemini_model, fallback, second)
                    ) from second
            raise ProviderError(f"Could not open a Gemini Live session: {exc}") from exc
        await self._send_greeting()
        return self

    async def _send_greeting(self) -> None:
        """Ask the model to speak its opening line — initial connect only.

        Deliberately not inside `_connect`: `_reconnect` reuses that to
        resume past the duration cap, and a robot re-introducing itself
        mid-conversation because the transport rotated would read as a
        reset to the person standing in front of it.
        """
        if not self.greeting or self._session is None:
            return
        try:
            await self.send_text(self.greeting)
        except Exception:
            logger.exception("could not deliver the greeting")

    async def _reconnect(self) -> bool:
        """Rejoin the conversation after the duration cap, using the handle.

        Returns False — and leaves the session ended — when there is nothing
        to resume with (no handle, or we're deliberately closing), so the
        caller falls back to ending cleanly rather than looping.
        """
        if self._closing or not self._resume_handle:
            return False
        old = self._cm
        try:
            await self._connect()   # _build_config now carries the handle
        except Exception:
            logger.exception("session resumption failed — ending the session")
            return False
        if old is not None:
            try:
                await old.__aexit__(None, None, None)
            except Exception:
                pass
        logger.info("resumed the Gemini Live session past the duration cap")
        return True

    async def __aexit__(self, *exc) -> None:
        self._closing = True
        await self._stop_event_tasks()
        if self._cm is not None:
            try:
                await self._cm.__aexit__(*(exc or (None, None, None)))
            except Exception:
                pass

    async def send_audio(self, pcm16: bytes) -> None:
        from google.genai import types

        if self._session is None:
            return
        if self._vad_gate is None:
            await self._session.send_realtime_input(
                audio=types.Blob(data=pcm16, mime_type=f"audio/pcm;rate={self.input_sample_rate}")
            )
            return
        # Local mode: the gate decides what upstream ever hears. Silence
        # produces no actions at all — no bytes, no signals — which is the
        # entire point: what is never sent cannot be hallucinated into
        # `我们走吧` or metered.
        for kind, data in self._vad_gate.feed(pcm16):
            if kind == "start":
                await self._event_queue.put(ProviderEvent(kind="speech_started"))
                await self._session.send_realtime_input(
                    activity_start=types.ActivityStart())
            elif kind == "end":
                from app.metrics import active

                if active.get() is not None:
                    active.get().speech_end("gemini_local_vad_decision")
                await self._session.send_realtime_input(
                    activity_end=types.ActivityEnd())
            else:
                await self._session.send_realtime_input(
                    audio=types.Blob(data=data,
                                     mime_type=f"audio/pcm;rate={self.input_sample_rate}")
                )

    def stand_down_floor(self) -> None:
        if self._vad_gate is not None:
            self._vad_gate.stand_down()

    async def send_text(self, text: str) -> None:
        """Push a server-side instruction in as its own turn.

        Gemini 3.1 uses realtime text; client content only seeds history and
        can complete silently without producing greeting/arrival audio.
        Keep the explicit user turn on older models. These are instructions
        to act, not text to read aloud; events.announce handles turn timing.
        """
        from google.genai import types

        if self._session is None:
            return
        if self.model.removeprefix("models/").startswith("gemini-3.1-flash-live"):
            await self._session.send_realtime_input(text=text)
            return
        await self._session.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=text)]),
            turn_complete=True,
        )

    async def _run_tool_calls(self, function_calls) -> AsyncIterator[ProviderEvent]:
        """Execute what the model asked for and hand the results back."""
        from google.genai import types

        from app import tools

        session = self._session

        calls = [
            (getattr(fc, "id", None), fc.name, dict(fc.args or {}))
            for fc in function_calls
        ]
        for _cid, name, args in calls:
            if _cid in self._cancelled_tool_ids:
                continue
            # Names of the arguments, never their values: the values are
            # phone numbers (Emma reads them back to confirm), budgets,
            # room numbers, what somebody asked her to remember. The
            # console is the least-governed store on the machine — no
            # retention, no redaction, copied into chat windows whole.
            # Each tool's turnlog line records the specific fields that
            # are worth keeping, by name, under TURN_LOG_KEEP_DAYS.
            logger.info("tool call: %s(%s)", name, ", ".join(sorted(args)))
            yield ProviderEvent(kind="tool_call", text=name)

        results = []
        for cid, name, args in calls:
            if (self._closing or self._session is not session
                    or cid in self._cancelled_tool_ids):
                continue
            # Check cancellation between actions as well as between batches.
            # An action already executing cannot necessarily be undone.
            results.extend(await tools.dispatch_all([(cid, name, args)]) if self.use_tools else
                           [(cid, name, {"ok": False, "error": "tools disabled for this session"})])
        results = [r for r in results if r[0] not in self._cancelled_tool_ids]

        responses = [
            types.FunctionResponse(id=cid, name=name, response=result)
            for cid, name, result in results
        ]
        # A result from a retired connection must not enter its replacement.
        if self._closing or self._session is not session:
            return
        if session is not None and responses:
            await session.send_tool_response(function_responses=responses)

        for _cid, name, result in results:
            yield ProviderEvent(kind="tool_result", text=name, data=result)

    def _input_event(self, text: str) -> ProviderEvent:
        if self._input_turn_id is None:
            self._input_serial += 1
            self._input_turn_id = str(self._input_serial)
        return ProviderEvent(kind="user_transcript", text=text,
                             data={"utterance_id": self._input_turn_id})

    async def _stop_event_tasks(self) -> None:
        tasks, self._event_tasks = self._event_tasks, []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        while not self._tool_batches.empty():
            self._tool_batches.get_nowait()
        self._pending_tool_ids.clear()
        self._cancelled_tool_ids.clear()

    async def events(self) -> AsyncIterator[ProviderEvent]:
        if self._session is None:
            return

        async def receive():
            try:
                async for event in self._receive_events():
                    await self._event_queue.put(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._event_queue.put(ProviderEvent(kind="error", text=str(exc)))
            await self._event_queue.put(None)

        async def run_tools():
            while True:
                session, calls = await self._tool_batches.get()
                ids = {fc.id for fc in calls if getattr(fc, "id", None)}
                try:
                    if self._closing or session is not self._session:
                        continue
                    async for event in self._run_tool_calls(calls):
                        await self._event_queue.put(event)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("Gemini tool response failed")
                    await self._event_queue.put(ProviderEvent(kind="error", text=str(exc)))
                    await self._event_queue.put(None)
                    return
                finally:
                    self._pending_tool_ids.difference_update(ids)
                    self._cancelled_tool_ids.difference_update(ids)

        self._event_tasks = [asyncio.create_task(receive()),
                             asyncio.create_task(run_tools())]
        try:
            while True:
                event = await self._event_queue.get()
                if event is None:
                    return
                yield event
        finally:
            await self._stop_event_tasks()

    async def _receive_events(self) -> AsyncIterator[ProviderEvent]:
        """Yield events for the whole conversation, not just one turn.

        `session.receive()` deliberately stops iterating once it sees
        `turn_complete` (see the SDK: it `break`s on that message). Iterating
        it a single time therefore ends the stream after the assistant's
        first reply, which tears the whole session down. So call it again for
        each new turn until the socket itself closes.
        """
        if self._session is None:
            return
        # True only for the span between reconnecting and receiving the first
        # message of the resumed session. If a reconnect yields nothing right
        # back, we must not reconnect again on top of it — that is the tight
        # loop this guards against. Any real message clears it, so a *later*
        # duration cap still gets its own resume.
        just_reconnected = False
        while True:
            try:
                received_anything = False
                async for message in self._session.receive():
                    received_anything = True
                    just_reconnected = False

                    # Resumption bookkeeping. The server hands out a fresh
                    # handle through the session and warns with go_away shortly
                    # before it closes the socket; neither is anything the
                    # browser needs to see.
                    update = getattr(message, "session_resumption_update", None)
                    if update is not None and getattr(update, "new_handle", None):
                        self._resume_handle = update.new_handle
                    if getattr(message, "go_away", None) is not None:
                        # `go_away` is not a warning to note and carry on from.
                        # It is an instruction: close the socket yourself. The
                        # server said so in the abort we earned by ignoring it —
                        #
                        #   1008 (policy violation) Connection aborted because
                        #   the client failed to close the connection after
                        #   receiving a GoAway signal
                        #
                        # and the cost was the whole conversation. The old code
                        # logged "will resume" and waited to be disconnected;
                        # meanwhile `_browser_to_provider` kept pushing mic
                        # audio into the dying socket, `send_realtime_input`
                        # raised ConnectionClosedError, and that exception took
                        # down the pump — which ends the session, because
                        # `run()` waits on FIRST_COMPLETED. A recoverable
                        # fifteen-minute rollover became a dead robot with a
                        # guest in front of it.
                        #
                        # So cut over now, while the socket is still healthy
                        # enough to close politely.
                        left = getattr(message.go_away, "time_left", "?")
                        logger.info("Gemini go_away (%s left) — resuming now", left)
                        if await self._reconnect():
                            just_reconnected = True
                            # Tell the session it happened. A resumed model is
                            # awake but idle: it has no turn to finish and no
                            # reason to speak, so a tour that was mid-flight
                            # simply stops. Every route that restarts a tour
                            # hangs off turn_complete, and there will not be
                            # another one. Fixing the crash without this just
                            # traded a dead robot for a silent one.
                            yield ProviderEvent(kind="resumed")
                            break        # re-enter receive() on the new session
                        logger.warning(
                            "go_away arrived with nothing to resume from — "
                            "the session will end when the socket closes"
                        )
                        continue

                    usage = getattr(message, "usage_metadata", None)
                    if usage is not None:
                        from app.metrics import counts

                        keys = ("prompt_token_count", "response_token_count", "total_token_count",
                                "cached_content_token_count", "tool_use_prompt_token_count",
                                "thoughts_token_count")
                        data = {key: getattr(usage, key, None) for key in keys}
                        yield ProviderEvent(kind="usage", data={
                            "source": "gemini_report", **counts(data, keys)})

                    cancellation = getattr(message, "tool_call_cancellation", None)
                    if cancellation is not None:
                        self._cancelled_tool_ids.update(
                            set(getattr(cancellation, "ids", None) or [])
                            & self._pending_tool_ids)
                    tool_call = getattr(message, "tool_call", None)
                    if tool_call and getattr(tool_call, "function_calls", None):
                        self._pending_tool_ids.update(
                            fc.id for fc in tool_call.function_calls if getattr(fc, "id", None))
                        await self._tool_batches.put(
                            (self._session, list(tool_call.function_calls)))

                    content = getattr(message, "server_content", None)
                    if content is None:
                        continue

                    # Barge-in: Gemini already cancelled its own generation
                    # and dropped the unsent audio, so the client only stops.
                    if getattr(content, "interrupted", False):
                        self._out_filter.reset()
                        self._out_spacing.reset()
                        yield ProviderEvent(kind="interrupted")

                    if getattr(content, "input_transcription", None):
                        finished = getattr(content.input_transcription, "finished", None)
                        if isinstance(finished, bool):
                            self._input_finished = finished
                        text = content.input_transcription.text
                        if text:
                            text = self._in_spacing.feed(text)
                            if text:
                                yield self._input_event(text)
                        if finished is True:
                            tail = self._in_spacing.flush()
                            if tail:
                                yield self._input_event(tail)
                            self._input_turn_id = None
                            self._input_finished = None

                    if getattr(content, "output_transcription", None):
                        text = content.output_transcription.text
                        if text:
                            clean = self._out_spacing.feed(self._out_filter.feed(text))
                            if clean:
                                yield ProviderEvent(kind="assistant_transcript", text=clean)

                    model_turn = getattr(content, "model_turn", None)
                    if model_turn and model_turn.parts:
                        for part in model_turn.parts:
                            blob = getattr(part, "inline_data", None)
                            if blob is not None and blob.data:
                                yield ProviderEvent(kind="audio", audio=blob.data)

                    if getattr(content, "turn_complete", False):
                        # Release any text the filters were holding back —
                        # a possible marker prefix, and a possible word space.
                        tail = self._out_spacing.feed(self._out_filter.flush())
                        tail += self._out_spacing.flush()
                        if tail:
                            yield ProviderEvent(kind="assistant_transcript", text=tail)
                        # Output completion must not split an input transcript
                        # explicitly marked unfinished by the provider. Older
                        # models omit finished, so retain the turn fallback.
                        if self._input_finished is not False:
                            user_tail = self._in_spacing.flush()
                            if user_tail:
                                yield self._input_event(user_tail)
                            self._input_turn_id = None
                        yield ProviderEvent(kind="turn_complete")

                if not received_anything:
                    # The stream ended. If the server had given us a handle,
                    # this is almost always the duration cap rather than a real
                    # goodbye — rejoin and carry the conversation across it.
                    if not just_reconnected and await self._reconnect():
                        just_reconnected = True
                        continue
                    logger.info("Gemini Live session closed upstream")
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # An upstream drop mid-stream. A resumption handle turns it
                # from fatal into the same cutover as a clean cap.
                if not just_reconnected and await self._reconnect():
                    just_reconnected = True
                    continue
                logger.exception("Gemini Live session ended with an error")
                yield ProviderEvent(kind="error", text=str(exc))
                return
