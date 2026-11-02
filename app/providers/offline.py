"""The offline provider: Emma when Gemini cannot be reached (OFFLINE_FALLBACK).

Same interface as GeminiProvider, so the session, the face, focus and the turn
log see an ordinary provider -- `ready` with 16 kHz in / 24 kHz out, then
speech_started / user_transcript / assistant_transcript / audio / tool_call /
tool_result / turn_complete. What is different is that **nothing here writes a
word**:

    mic -> VadGate (the same gate, floors and focus Gemini's sessions use)
        -> the closed segment -> app/offline/asr.py (on this machine)
        -> app/offline/router.py (rules) -> an approved story or approved line
        -> its pre-rendered clip (app/offline/voice.py) -> the page

A heard stop ("หยุด", "stop": router.stop_route) cuts her voice and calls the
stop tool the session offers (stop_moving / move_body(stop)) -- the one tool
this provider ever calls; it never moves anything.

A story goes out exactly as present_story's does online: tool_call, then the
tool_result with `stories.tool_result(topic)` (the face draws it, story_shown
is logged), then each approved `say` line in the talk language with its
audio, then turn_complete. A fixed line is one approved line with its clip.
No clip = the words and the pictures without the voice (emma-story.js runs
on its own clock); never a different voice, never a synthesized one.

`send_text` receives what the server meant for a model (events.announce,
taps, the follow-up nudge). Offline the few that can be honoured with
approved words are recognised by the markers the server's own builders put
in them (router.classify_injected); everything else is dropped with a debug
line -- the follow-up nudge, the goodbye, a robot's arrival: a fixed line
answering an instruction it cannot read would be a guess.

The attributes the session reads by getattr are kept (`_vad_gate`,
`_speech_ended_at`, `_replied_since_end`), so the mic report, call ears,
focus, the reply-owed check and the follow-up gate keep working.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

from app import heard
from app.config import settings
from app.providers.base import ProviderEvent, VoiceProvider

logger = logging.getLogger("condo_voice.offline")

#: A guest who answers within this long after her question ended is
#: answering it (FOCUS_REPLY_WINDOW_S's robot value): a one-word reply counts.
EXPECT_S = 10.0
#: A segment this long that decodes to nothing, right after her question,
#: gets "sorry, could you say it again" -- shorter is a cough or a knock.
NOT_HEARD_MIN_S = 0.8
_ASKED = re.compile(r"(\?|？|คะ)\s*$")


def _own_gate(vad_floor: float | None):
    """The session's VAD gate, built like GeminiProvider's (vad_gate.for_session:
    the same floor, grace, speaker focus and per-session detector). Offline
    needs one even where VAD_MODE is gemini -- there is no upstream detector
    to lean on -- so with that mode it is built here from the same knobs."""
    from app import vad_gate

    gate = vad_gate.for_session(min_rms=vad_floor)
    if gate is not None or settings.vad_mode == "local":
        return gate                     # local without a model has already said why
    detector = vad_gate._make_detector()
    if detector is None:
        return None
    floor = settings.vad_min_rms if vad_floor is None else float(vad_floor)
    return vad_gate.VadGate(detector, prefix_padding_ms=settings.vad_prefix_padding_ms,
                            min_rms=floor, floor_grace_s=settings.vad_floor_grace_s,
                            focus_ratio=settings.vad_focus_ratio)


class OfflineProvider(VoiceProvider):
    input_sample_rate = 16000
    output_sample_rate = 24000          # the clips' rate == the Live API's: `ready` is unchanged
    #: No upstream cap; the idle timeout ends an offline call like any other.
    session_limit_minutes = 60
    auto_resumes = False

    def __init__(self, voice: str, instructions: str, greeting: str | None = None,
                 use_tools: bool = True, vad_floor: float | None = None) -> None:
        super().__init__(voice, instructions)
        from app.offline import router
        from app.talk_lang import TalkLanguage

        #: The model label the session's metrics and turn log carry.
        self.model = "offline"
        #: Set (to an instruction for a model) when the session wants an
        #: opening greeting: offline that is the approved offline greeting.
        self.greeting = greeting
        self.use_tools = use_tools
        self._vad_gate = _own_gate(vad_floor)
        if self._vad_gate is not None:
            # The segment's audio is what the recognizer decodes.
            self._vad_gate.keep_audio = True
        else:
            logger.warning("offline provider: no local VAD (data/wake/silero_vad.onnx) -- "
                           "it cannot hear; taps and greetings still work")
        #: When the gate last closed a segment, and whether a reply (or a
        #: decision to stay silent) has been made since -- session._reply_owed.
        self._speech_ended_at: float | None = None
        self._replied_since_end = False
        self._segment_hook = None
        self._talk = TalkLanguage()
        self._route_state = router.RouterState()
        self._events: asyncio.Queue = asyncio.Queue()
        self._work: asyncio.Queue = asyncio.Queue()
        self._worker: asyncio.Task | None = None
        self._closing = False
        self._serial = 0
        self._call_serial = 0
        #: When the audio already handed to the page ends (her voice's clock),
        #: and when her last QUESTION ends -- a reply inside EXPECT_S is an answer.
        self._audio_until = 0.0
        self._asked_until: float | None = None
        self._more_tap_said = False
        self._stories_visible: bool | None = None
        self._no_ears_logged = False

    # ---- lifecycle ----

    async def __aenter__(self) -> OfflineProvider:
        from app.offline import asr, lines, router

        # Nothing here can fail for want of a network: an offline session
        # always opens. What it cannot do is said once, in the log.
        status = lines.status()
        if status["approved"] < len(lines.IDS):
            logger.warning("offline lines: %d of %d approved (drafts are never said) -- "
                           "python scripts/approve_offline_lines.py --by NAME",
                           status["approved"], len(lines.IDS))
        asr.engine()              # created now; loaded in the worker's thread
        await asyncio.to_thread(router.prepare)
        from app import turnlog

        turnlog.record("offline_session", lines=status["approved"], drafts=status["draft"])
        if self.greeting is not None:
            await self._work.put(("greeting", None))
        return self

    async def __aexit__(self, *exc) -> None:
        self._closing = True
        if self._worker is not None and not self._worker.done():
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):
                pass
        await self._events.put(None)

    # ---- in ----

    async def send_audio(self, pcm16: bytes) -> None:
        gate = self._vad_gate
        if gate is None or self._closing:
            return
        for kind, _data in gate.feed(pcm16):
            if kind == "start":
                # New speech: earlier captions no longer authorise motion
                # (the guard GeminiProvider keeps, design §2d step 4).
                heard.mark_activity()
                await self._events.put(ProviderEvent(kind="speech_started"))
            elif kind == "end":
                from app.metrics import active

                if active.get() is not None:
                    active.get().speech_end("offline_local_vad_decision")
                self._speech_ended_at = time.monotonic()
                self._replied_since_end = False
                closed = gate.take_closed()
                if closed is None:
                    continue
                hook = self._segment_hook
                if hook is not None:
                    try:
                        # Focus attribution / voice prints. The speaker note
                        # it may return is for a model; there is none.
                        hook(*closed)
                    except Exception:
                        logger.exception("segment hook failed")
                await self._work.put(("segment", closed))

    def stand_down_floor(self) -> None:
        if self._vad_gate is not None:
            self._vad_gate.stand_down()

    def watch_segments(self, hook, *, keep_audio: bool = False) -> bool:
        if self._vad_gate is None:
            return False
        self._segment_hook = hook
        # keep_audio stays on whatever the session asks: the recognizer needs it.
        self._vad_gate.keep_audio = True
        return True

    def note_guest(self, text: str) -> None:
        """Words that did not come through the recognizer (eval harnesses)."""
        self._talk.guest(text)

    async def send_text(self, text: str) -> None:
        """An instruction meant for a model -- see the module doc."""
        from app.offline import router

        injected = router.classify_injected(text)
        if injected.kind == "ignore":
            logger.debug("offline: server text with no offline meaning -- dropped (%d chars)",
                         len(text or ""))
            return
        await self._work.put(("inject", injected))

    async def send_context_note(self, text: str) -> bool:
        return False

    # ---- out ----

    async def events(self):
        if self._worker is None:
            self._worker = asyncio.create_task(self._run(), name="offline-provider")
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event

    async def _run(self) -> None:
        from app.offline import asr

        engine = asr.engine()
        # The models load once per process, in a thread, alongside the first
        # turns: a greeting or a tap does not wait for 0.9 GB of models; a
        # guest's first words wait at most for the rest of the load.
        loading = asyncio.ensure_future(asyncio.to_thread(engine.load))
        while not self._closing:
            kind, item = await self._work.get()
            try:
                if kind == "segment":
                    if not loading.done():
                        await loading
                    await self._heard_segment(item, engine)
                elif kind == "inject":
                    await self._injected(item)
                elif kind == "greeting":
                    await self._say_line("offline_greeting", self._reply_lang("en"), "greeting")
                    self._route_state.greeted = True
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("offline provider: one turn failed -- the call goes on")

    # ---- deciding ----

    def _reply_lang(self, fallback: str | None) -> str:
        """th / en: the conversation's language (talk_lang), any other known
        language answered in English (the stories have no third language),
        before the guest has said anything: `fallback`, else English -- the
        owner's first greeting is English (8 Oct)."""
        lang = self._talk.language()
        if lang in ("th", "en"):
            return lang
        if lang:
            return "en"
        return fallback if fallback in ("th", "en") else "en"

    def _stories_on(self) -> bool:
        """present_story is offered on THIS session (robot face, condo,
        STORIES_ENABLED, opt-in group, approved catalog): asked once, in the
        session's own context. Elsewhere offline tells no story either."""
        if self._stories_visible is None:
            try:
                from app.prompts import _tool_visible

                self._stories_visible = bool(settings.stories_enabled and _tool_visible("present_story"))
            except Exception:
                self._stories_visible = False
        return self._stories_visible

    def _expecting(self, now: float) -> bool:
        return self._asked_until is not None and now <= self._asked_until + EXPECT_S

    async def _heard_segment(self, closed, engine) -> None:
        from app import turnlog
        from app.offline import router
        from app.talk_lang import asked_for

        _n, t0, t1, pcm16 = closed
        if not engine.ready:
            self._replied_since_end = True
            if not self._no_ears_logged:
                self._no_ears_logged = True
                logger.warning("offline: speech heard but no recognizer is loaded -- not answered")
            return
        known = self._talk.language()
        hint = known if known else None
        # an answer to her question is judged by when the guest stopped,
        # not by when the decoder finished
        expecting = self._expecting(t1)
        result = await asyncio.to_thread(engine.transcribe, pcm16, hint)
        now = time.monotonic()
        if result is None:
            self._replied_since_end = True
            return
        text = result.text.strip()
        turnlog.record("offline_asr", lang=result.lang, decode_ms=result.decode_ms,
                       lid_ms=result.lid_ms, seconds=round(max(0.0, t1 - t0), 2),
                       chars=len(text))
        if text:
            self._serial += 1
            self._talk.guest(text)
            await self._events.put(ProviderEvent(kind="user_transcript", text=text,
                                                 data={"utterance_id": "offline-%d" % self._serial}))
        lang = self._reply_lang(result.lang)
        asked, question = asked_for(text) if text else (None, False)
        # A stop first, before the "say it again" branch: "พอแล้ว" may carry no
        # content word at all, and "Emma stop" after her question was the menu.
        stop = router.stop_route(text, self._robot_moving) if text else None
        if stop is not None:
            route = stop
        elif asked and not question:
            route = router.route_language_request("th" if asked == "th" else "en", self._route_state,
                                                  stories_on=self._stories_on())
            lang = "th" if asked == "th" else "en"
        elif not text or not router.content_words(text):
            if expecting and (t1 - t0) >= NOT_HEARD_MIN_S:
                route = router.line_route(self._route_state, "not_heard", "not_heard", now)
            else:
                route = router.Route("silent", why="empty")
        else:
            route = router.route(text, lang, self._route_state, now=now, expecting=expecting,
                                 stories_on=self._stories_on())
        turnlog.record("offline_route", action=route.action, line=route.line, topic=route.topic,
                       why=route.why, lang=lang)
        await self._perform(route, lang)

    async def _injected(self, injected) -> None:
        from app.offline import router

        lang = self._reply_lang(None)
        if injected.kind == "greeting":
            if self._route_state.greeted:
                return
            self._route_state.greeted = True
            await self._say_line("offline_greeting", self._reply_lang("en"), "greeting")
        elif injected.kind == "story":
            await self._perform(router.Route("story", topic=injected.topic, why="tap"), lang)
        elif injected.kind == "choice":
            topic = injected.topic if injected.topic and self._story_offered(injected.topic) else None
            if topic:
                await self._perform(router.Route("story", topic=topic, why="choice"), lang)
            else:
                await self._perform(router.line_route(self._route_state, "handoff_commercial", "choice",
                                                 time.monotonic()), lang)
        elif injected.kind == "tool_tap":
            await self._perform(router.line_route(self._route_state, "handoff_commercial", "tool_tap",
                                             time.monotonic()), lang)
        elif injected.kind == "more_tap":
            # The page has enlarged the picture: that is the answer. What the
            # report says about it is a model's job; offline she hands over
            # once and then lets the pictures speak.
            if not self._more_tap_said:
                self._more_tap_said = True
                await self._perform(router.line_route(self._route_state, "handoff_other", "more_tap",
                                                 time.monotonic()), lang)
            else:
                await self._events.put(ProviderEvent(kind="turn_complete"))
        elif injected.kind == "topic":
            text = injected.text or ""
            route = router.route(text, lang, self._route_state, expecting=True,
                                 stories_on=self._stories_on())
            await self._perform(route, lang)
        elif injected.kind == "language":
            target = "th" if injected.lang == "th" else "en"
            # A language button is the guest naming the language: talk_lang
            # locks on a request, so it is handed one in its own grammar --
            # unless the session already pinned the pick (the language menu,
            # 29 Oct): a made-up "speak English" after a Chinese pick is a
            # request after the pin, and it moved the held language to English
            # and the screen's button with it (review 29 Oct).
            if getattr(self._talk, "pinned", None) is None:
                self._talk.guest("พูดภาษาไทย" if target == "th" else "speak English")
                self._talk.emma(".")
            await self._perform(router.route_language_request(target, self._route_state,
                                                              stories_on=self._stories_on()), target)

    def _story_offered(self, topic: str) -> bool:
        if not self._stories_on():
            return False
        from app import stories

        return topic in stories.available()

    # ---- speaking ----

    async def _perform(self, route, lang: str) -> None:
        self._replied_since_end = True
        if route.action == "stop":
            await self._stop()
        elif route.action == "story" and route.topic and self._stories_on():
            await self._say_story(route.topic, lang)
        elif route.action == "line" and route.line:
            await self._say_line(route.line, lang, route.why)

    # ---- stopping (review 26 Oct: offline had no voice stop at all) ----

    @staticmethod
    def _robot_moving() -> bool:
        """focus's own test (walking or a gesture under way). Never raises."""
        try:
            from app import focus

            return bool(focus._robot_moving())
        except Exception:
            return False

    @staticmethod
    def _stop_tool() -> tuple[str, dict] | None:
        """The stop the model would call online, if this session offers it:
        stop_moving (the walk, and the arm through robot_nav) before
        move_body(stop). Visibility is the registry's, as for the model --
        a tool the session does not offer is not reached around."""
        from app.tools import registry

        for name, args in (("stop_moving", {}), ("move_body", {"action": "stop"})):
            entry = registry.get(name)
            if entry is not None and registry.allowed(entry):
                return name, args
        return None

    async def _stop(self) -> None:
        """Her voice stops now; the robot gets the stop tool. Sent whether or
        not we believe it is moving -- stop_moving's own rule: a cached
        `moving` flag is the worst thing to trust while it rolls. The tool's
        reply is an instruction for a model; offline nothing is said (no
        line says "stopped": a sent stop is not a confirmed one). The E-stop
        button on the robot stays the stop that does not depend on hearing."""
        if time.monotonic() < self._audio_until - 0.2:
            await self._events.put(ProviderEvent(kind="interrupted"))
            self._audio_until = time.monotonic()
        self._asked_until = None
        tool = self._stop_tool()
        if tool is None:
            logger.info("offline: a stop was heard; no stop tool is offered on this session")
            return
        from app.tools import registry

        name, args = tool
        self._call_serial += 1
        call_id = "offline-%d" % self._call_serial
        await self._events.put(ProviderEvent(kind="tool_call", text=name, call_id=call_id))
        result = await registry.dispatch(name, args)
        await self._events.put(ProviderEvent(kind="tool_result", text=name, data=result, call_id=call_id))
        await self._events.put(ProviderEvent(kind="turn_complete"))

    async def _barge_if_playing(self) -> None:
        """A new answer while the last one is still playing replaces it, the
        way a new turn interrupts Gemini: the page drops what is queued."""
        if time.monotonic() < self._audio_until - 0.2:
            await self._events.put(ProviderEvent(kind="interrupted"))
            self._audio_until = time.monotonic()

    async def _say_line(self, line_id: str, lang: str, why: str = "") -> None:
        from app.offline import lines

        text = lines.text(line_id, lang)
        if text is None:
            # A draft (or a missing id): never said, never shown.
            logger.info("offline line %s is not approved -- silent", line_id)
            return
        await self._barge_if_playing()
        await self._speak([text], lang)
        await self._events.put(ProviderEvent(kind="turn_complete"))

    async def _say_story(self, topic: str, lang: str) -> None:
        from app import stories

        result = await asyncio.to_thread(stories.tool_result, topic)
        if not isinstance(result, dict) or not result.get("ok"):
            logger.info("offline: story %s not shown (%s)", topic,
                        result.get("error") if isinstance(result, dict) else "no result")
            return
        await self._barge_if_playing()
        self._call_serial += 1
        call_id = "offline-%d" % self._call_serial
        await self._events.put(ProviderEvent(kind="tool_call", text="present_story", call_id=call_id))
        await self._events.put(ProviderEvent(kind="tool_result", text="present_story", data=result,
                                             call_id=call_id))
        said = [line.get(lang) or line.get("en") or "" for line in result.get("say") or []
                if isinstance(line, dict)]
        await self._speak([s for s in said if s.strip()], lang)
        await self._events.put(ProviderEvent(kind="turn_complete"))

    async def _speak(self, texts: list[str], lang: str) -> None:
        """Each line's words interleaved with its clip, word-sized pieces the
        way a live reply streams them (the bubble and the story's cue words
        follow the voice, not run ahead of it by a whole line)."""
        from app.offline import voice

        for n, text in enumerate(texts):
            pcm = await asyncio.to_thread(voice.pcm_for, text, lang)
            pieces = re.findall(r"\S+\s*", text)
            if n and pieces:
                pieces[0] = " " + pieces[0]
            self._talk.emma(text)
            if not pcm:
                if pieces:
                    await self._events.put(ProviderEvent(kind="assistant_transcript", text="".join(pieces)))
                continue
            chunks = voice.chunks(pcm)
            k = 0
            for i, chunk in enumerate(chunks):
                while k < len(pieces) and k * len(chunks) // len(pieces) <= i:
                    await self._events.put(ProviderEvent(kind="assistant_transcript", text=pieces[k]))
                    k += 1
                await self._events.put(ProviderEvent(kind="audio", audio=chunk))
            while k < len(pieces):
                await self._events.put(ProviderEvent(kind="assistant_transcript", text=pieces[k]))
                k += 1
            now = time.monotonic()
            self._audio_until = max(self._audio_until, now) + len(pcm) / 2 / voice.RATE
        if texts and _ASKED.search(texts[-1]):
            self._asked_until = max(self._audio_until, time.monotonic())
        else:
            self._asked_until = None
