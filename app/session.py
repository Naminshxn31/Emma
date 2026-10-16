"""
Browser-facing WebSocket session.

    browser  <--normalized WS-->  this server  <--provider SDK/WS-->  Gemini | OpenAI

The server is a proxy rather than a passthrough so that:

- the API key never reaches the browser,
- the condo instructions are applied server-side (a guest can't rewrite the
  assistant's rules from devtools),
- the browser speaks one protocol regardless of which provider is active.

Audio crosses the browser link as **binary frames** rather than base64 JSON —
roughly a third less bytes and no encode/decode per chunk on either end.

See `app/providers/base.py` for the protocol and the provider differences it
papers over.
"""
from __future__ import annotations

import asyncio
import contextvars
import itertools
import json
import logging
import queue
import math
import re
import threading
import time
from pathlib import Path

from fastapi import WebSocket, WebSocketDisconnect

from app import display, heard, robot_contracts, robot_face_link, speech_gestures, turnlog, voices
from app.config import settings
from app.prompts import build_instructions, greeting_for
from app.providers import ProviderError, default_voice_for, get_provider

logger = logging.getLogger("condo_voice.session")


def _provider_failure_kind(exc: Exception) -> str:
    """Classify provider failures without logging URLs, tokens, or messages."""
    value = str(exc).lower()
    if "winerror 5" in value or "access is denied" in value or "permission" in value:
        return "permission"
    if any(word in value for word in ("quota", "429", "resource_exhausted")):
        return "quota"
    if any(word in value for word in ("api key", "api_key", "401", "403", "unauthorized")):
        return "auth"
    if any(word in value for word in ("connect", "timeout", "network")):
        return "network"
    return "provider"

#: Call-mic debug recorder (CALL_DEBUG). Customer audio on disk, off by
#: default and gitignored — the WAKE_DEBUG equivalent for a live call, for
#: tuning the near-field floor by ear. The directory is a module constant so
#: tests redirect it to a tmp path instead of touching the repo's data/.
CALL_DEBUG_DIR = Path(__file__).resolve().parent.parent / "data" / "call_debug"

#: "Nothing sent yet" for the attention cue — distinct from None (centre).
_UNSENT = object()


def _array_held_by_app() -> bool:
    """The robot app reports its native array capture as running.
    DOA off (every PC, the default) never imports app.doa."""
    if not settings.doa_enabled:
        return False
    try:
        from app import doa

        link = doa.snapshot().get("link") or {}
        return (link.get("status") or {}).get("state") == "capturing"
    except Exception:
        return False


def _invite_text(bearing: float | None, why: str) -> str:
    """What the model is asked to say to a caller outside the lens.

    An instruction, not words to read: the model picks the sentence and the
    language, the way every announcement works (greeter.greeting_for). The
    side is the robot's own (+ bearing = its left); named only when the array
    was sure and the caller is clearly off to one side. Quoted wrong phrases,
    because that is what this model obeys: narrating the machinery ("ระบบ
    ตรวจพบเสียง") is the greeter's "ระบบจดจำใบหน้าตรวจพบ" one sense over.
    """
    side = ""
    if bearing is not None and abs(bearing) >= VoiceSession.INVITE_SIDE_MIN_DEG:
        side = " (เสียงมาทาง%sของหุ่น)" % ("ซ้าย" if bearing > 0 else "ขวา")
    who = ("มีคนเรียกชื่อคุณอยู่นอกระยะหน้าหุ่น" if why == "name"
           else "มีคนพูดกับหุ่นอยู่นอกระยะหน้าหุ่น")
    return (who + side + " — เชิญเขามายืนตรงหน้าหุ่นสั้นๆ ประโยคเดียว เช่น "
            "'เชิญมายืนตรงหน้าได้เลยค่ะ' ใช้ภาษาเดียวกับที่คุยกันอยู่ "
            "ห้ามพูดถึงกล้องหรือไมค์ ห้ามพูดว่า 'ระบบตรวจพบเสียง' "
            "และห้ามตอบเรื่องที่เพิ่งตอบไปแล้วซ้ำ")


#: The goodbye (FOCUS_GOODBYE). Same shape as the invitation: an instruction,
#: the model's own words and language, the machinery never narrated, and no
#: question — a question to somebody walking away is a sentence to nobody.
GOODBYE_TEXT = ("คนที่คุยอยู่เพิ่งเดินออกไปจากหน้าหุ่น — กล่าวลาสั้นๆ ประโยคเดียว "
                "เช่น 'ขอบคุณที่แวะมานะคะ' ใช้ภาษาเดียวกับที่คุยกันอยู่ "
                "ห้ามถามคำถาม ห้ามเล่าเรื่องที่ค้างอยู่ต่อ ห้ามพูดถึงกล้อง "
                "และห้ามพูดว่า 'ระบบตรวจพบ'")
#: The follow-up after silence (FOLLOW_UP_AFTER_S). An instruction, the
#: model's own words: one question that carries the topic on, never a
#: "anything else?" -- and explicitly the "ไม่ถามต่อ" rule's exception.
FOLLOW_UP_TEXT = ("ระบบแจ้ง: ลูกค้าเงียบไปหลังคำตอบล่าสุด ให้ถามต่อหนึ่งประโยคสั้นๆ "
                  "ที่ต่อจากเรื่องที่เพิ่งคุย ชวนไปขั้นถัดไปที่ทำได้จริงด้วยเครื่องมือที่มี "
                  "หรือชวนให้เราแนะนำส่วนอื่นของโครงการ (นี่คือข้อยกเว้นของกฎไม่ถามต่อ) ใช้ภาษาเดียวกับลูกค้า "
                  "ห้ามทวนคำตอบเดิม ห้ามถามว่า 'มีอะไรให้ช่วยอีกไหมคะ' ห้ามเปิดเรื่องราคาเอง "
                  # owner 15 Oct: "ทำไมชอบถามว่าลงทุนหรืออยู่เอง ควรจะถามเกี่ยวกับโครงการ"
                  "ห้ามถามว่าซื้อไว้อยู่เองหรือลงทุน "
                  "และห้ามเอ่ยถึงข้อความแจ้งนี้")
#: Her answer already ends on a question: the guest's silence is them
#: thinking about it, not an opening for a second one.
_ENDS_ON_QUESTION = re.compile(
    # คะ (high tone) is the question particle; ค่ะ (falling, with ่) the
    # statement one, so it never matches -- nor does นะคะ ("...ได้เลยนะคะ").
    # Review 12 Oct: the first version listed question words and matched
    # "เกี่ยว" through "กี่" (Thai has no spaces) while missing "...แบบไหนดีคะ".
    r"(?<!นะ)คะ\s*[?？.!]*\s*$"
    r"|(ไหม|มั้ย|หรือเปล่า|หรือยัง)\s*[?？.!]*\s*$"
    r"|[?？]\s*$")
#: The guest closed the topic or the visit: a follow-up is selling at them.
#: Thai is matched as WORDS (retrieval.tokenize), never as substrings --
#: บาย sits inside สบาย ("อยู่สบายไหม" is a question, not a goodbye), the same
#: trap as ราคา inside อาคาร (CLAUDE.md). Latin has spaces, so a regex.
_CLOSING_TH = ("ขอบคุณ", "ไม่เป็นไร", "แค่นี้", "พอแล้ว", "ไว้ก่อน", "ไปก่อน", "บาย", "ลาก่อน",
               "ไม่สนใจ")
_CLOSING_EN = re.compile(r"\bthank|that'?s all|that is all|\bbye\b|no thanks|not interested",
                         re.IGNORECASE)


def _guest_closed(text: str) -> bool:
    if _CLOSING_EN.search(text or ""):
        return True
    from app.tools.retrieval import tokenize

    hay = " %s " % " ".join(tokenize(text or ""))
    return any(" %s " % " ".join(tokenize(term)) in hay for term in _CLOSING_TH)
CALL_DEBUG_KEEP = 12
CALL_DEBUG_RATE = 16000


class VoiceSession:
    #: A summoned session that never got its greeting and heard nobody
    #: ends this soon, whatever IDLE_TIMEOUT_S says.
    SUMMONED_ABANDON_S = 30.0
    #: Longest a summoned session stays deaf waiting for its greeting.
    #: Dial + announce wait (0.4s) + generation + a spoken greeting is well
    #: under this; past it, listening anyway is the only safe choice.
    SUMMONED_HOLD_MAX_S = 10.0

    def __init__(self, ws: WebSocket, provider_name: str | None = None, voice: str | None = None,
                 profile: str | None = None, lang: str | None = None,
                 summoned: bool = False) -> None:
        self.ws = ws
        #: Opened because the server rang the page (a face at the door, a
        #: reminder), not because somebody said the name or pressed Start.
        #: Such a session gets its greeting from the event that rang it —
        #: `events.announce` delivers one the moment the line is up — so the
        #: provider's own opening line is dropped. With both, the second
        #: arrived while the first was still being generated and Gemini
        #: treated it as a barge-in: "หวัดดีค่ะ" cut off mid-word, then
        #: "สวัสดีค่ะ มีอะไรให้ช่วยไหมคะ" — the robot interrupting itself.
        self.summoned = summoned
        #: While set, microphone audio from the browser is dropped here and
        #: never reaches the model. A summoned session opens with the mic
        #: live 2-3 seconds before its greeting has even been generated, and
        #: whatever the room says in that window — the person at the door
        #: starting to talk, the owner at the desk, a passer-by — lands as
        #: the *first* turn, ahead of the greeting Emma was rung to give.
        #: The owner's words: "ตื่นแล้วทักทาย ให้ emma พูดก่อนค่อยฟังคนพูด".
        #: So a summoned session is deaf until the greeting has been heard
        #: (`_greeting_turn_done` sets the moment from the browser's audio
        #: lead), or until SUMMONED_HOLD_MAX_S if no greeting ever comes —
        #: the announcement can be dropped (`still_relevant` false) and a
        #: session that stays deaf forever is this bug with the sign
        #: flipped. One clock, read where the audio arrives; no task.
        self._ears_closed_until: float | None = (
            time.monotonic() + float(getattr(settings, "summoned_hold_max_s",
                                             self.SUMMONED_HOLD_MAX_S))
            if summoned else None
        )
        self._greeting_turn_seen = False
        #: Set while the model is not in the middle of answering a
        #: server-side announcement. `events.announce` clears it when it
        #: injects a turn and waits for it before injecting the next one.
        #: The audio queue alone could not keep two announcements apart:
        #: between "text sent" and "first audio chunk arrives" the queue
        #: is empty, `wait_until_heard` returns at once, and the second
        #: text lands while the model is still generating the first —
        #: which Gemini treats as a barge-in. Seen on screen 2026-09-01:
        #: "สวัสดีค่ะ มีอะไรให้" cut off, marked ถูกพูดแทรก, then the
        #: second greeting. The robot interrupting itself, again.
        self.turn_idle = asyncio.Event()
        self.turn_idle.set()
        self.provider_name = provider_name or settings.provider
        # Fixed by this server's configuration, never supplied by the model
        # or a visitor URL. All project data loaders use the same ID.
        self.project_id = settings.project_id
        #: Per-connection persona. The URL can ask for one (?profile=
        #: translator) so the sales room flips into interpreter mode with a
        #: bookmark instead of an .env edit and a restart. Falls back to the
        #: machine's configured profile, and unknown names land on condo
        #: inside build_instructions — same safety as everywhere else.
        self.profile = (profile or settings.assistant_profile).strip().lower()
        # A public mode URL cannot unlock the machine owner's private persona.
        if self.profile not in {"condo", "emma", "joke", "translator"}:
            self.profile = "condo"
        if self.profile == "emma" and settings.assistant_profile != "emma":
            self.profile = "condo"
        #: Translator mode's Thai->X target (?lang=es on the page URL).
        self.lang = (lang or "en").strip().lower()
        chosen = voice or default_voice_for(self.provider_name)
        if not voices.is_valid(self.provider_name, chosen):
            chosen = default_voice_for(self.provider_name)
        self.voice = chosen
        self.provider = None
        #: Milliseconds of reply audio handed to the browser for the current
        #: turn — OpenAI needs this to truncate an interrupted reply.
        self._sent_audio_ms = 0.0
        #: Which slide the tour was nudged at, and how many times, so a model
        #: that keeps ignoring the nudge isn't pestered forever. Reset the
        #: moment the position actually moves.
        self._nudge_index: int | None = None
        self._nudge_count = 0
        #: Did the model actually say anything this turn? The nudge exists for
        #: a model that narrates and then stops, and it has to be able to tell
        #: that apart from a model that said nothing at all — see
        #: `_nudge_tour_if_stalled`, where confusing the two ran a 59-page
        #: deck out in silence.
        self._spoke_this_turn = False
        #: The pending deferred nudge, if any. One at a time: a second would
        #: land on top of the first and push the deck two slides.
        self._nudge_task: asyncio.Task | None = None
        #: When a guest was last heard. Starts now rather than at zero, or a
        #: session would time out before anybody had a chance to speak.
        self._last_heard_at = time.monotonic()
        #: Where the idle clock may start besides guest speech: the moment a
        #: summoned line's greeting has been heard (`_greeting_turn_done`).
        #: The idle quiet is measured from the later of the two. Set nowhere
        #: else — the robot narrating to nobody must never reset it.
        self._idle_from = 0.0
        #: Whether a guest has EVER been heard on this line. `_last_heard_at`
        #: cannot say that (it starts at "now"), and two rules need the
        #: distinction: a virgin standing line is held open forever, and the
        #: greeter must not deliver a greeting into a line somebody has
        #: already started talking on (that is a conversation, ungreeted or
        #: not — ประโยคทักทายไม่ให้พูดแทรก).
        self._ever_heard = False
        #: A transcript chunk shaped like a formatted verse block arrived —
        #: many newlines, one atomic chunk — which is the signature of text
        #: Gemini wrote but never voiced. See `_respeak_silent_block`.
        self._silent_block_seen = False
        self._respeak_fired = False
        #: Follow-up after silence (FOLLOW_UP_AFTER_S): a guest question is
        #: awaiting its follow-up decision; the generation every guest voice
        #: bumps (a pending follow-up whose generation moved is void); what
        #: she said since the guest last spoke, and what the guest said.
        self._answer_pending = False
        self._follow_up_gen = 0
        self._follow_up_task: asyncio.Task | None = None
        self._said_since_guest = ""
        self._guest_text = ""
        # The choice buttons under her own question (_offer_choices): what she
        # said since the last turn_complete, and which set is on screen now.
        self._said_this_turn = ""
        self._choices_offered: str | None = None
        #: Engagement (FOCUS_WAKE / FOCUS_INVITE / FOCUS_GOODBYE /
        #: FOCUS_ATTENTION): all None/False unless a switch is on.
        self._began_at = time.monotonic()
        self._call_ears = None
        self._ears_task: asyncio.Task | None = None
        self._invite_task: asyncio.Task | None = None
        self._invite_at: float | None = None
        self._goodbye_done = False
        self._attention_sent = _UNSENT

    @property
    def metrics(self):
        # Lazy also supports protocol tests that construct sessions via __new__.
        if not hasattr(self, "_metrics"):
            from app.metrics import SessionMetrics

            self._metrics = SessionMetrics(getattr(self, "provider_name", "unknown"))
        return self._metrics

    async def run(self) -> None:
        # Tool visibility follows the persona of THIS conversation, and the set
        # belongs HERE rather than in __init__ — measured: a ContextVar set in
        # __init__ lands in the *caller's* context, so every test that merely
        # built a session repainted the tool list for every test after it in the
        # same process (90 red, all green again when run alone). Inside the
        # coroutine the write stays in the task asyncio copies for it, which is
        # also the context the provider and the tool calls inherit.
        from app.tools import registry as _registry

        _registry.session_profile.set(self.profile)
        headers = getattr(self.ws, "headers", {})
        robot_face = "EmmaRobot/" in headers.get("user-agent", "")
        # The ContextVar is the user agent's *claim*, and it has to be: tool
        # declarations are fixed when the provider connects below, before any
        # frame from the page can arrive, so a face_hello can never widen
        # them afterwards. With ROBOT_FACE_TOKEN set, the proof is checked
        # where it can be — every command and every inbound body/nav frame
        # goes through `robot_face_link.is_robot_face`, which needs the hello.
        _registry.robot_face_session.set(self if robot_face else None)
        if robot_face:
            robot_face_link.claim(self)
        # Captions and activity marks are kept per conversation (heard.py);
        # the provider has no reference to its session, so it finds it here.
        heard.bind_session(self)
        query = getattr(self.ws, "query_params", {})
        # An already-open /preview page may still run yesterday's JS after a
        # server reload. Only a page that loaded the acoustic gate advertises
        # this capability; otherwise the old half-duplex guard stays on.
        # ECHO_BARGE gates the whole exception: the owner ran it for one
        # morning (2026-09-30) and ordered it out — the duck-and-check made
        # Emma's voice stutter, and "she finishes her sentence" won.
        echo_barge = (settings.echo_barge and self.profile == "joke"
                      and query.get("echo_barge") == "1")
        await self.ws.accept()

        if not settings.api_key_for(self.provider_name):
            key = "GEMINI_API_KEY" if self.provider_name == "gemini" else "OPENAI_API_KEY"
            await self._send_json({
                "type": "error",
                "code": "missing_api_key",
                "message": f"{key} is not set on the server. Add it to .env and restart.",
            })
            await self.ws.close()
            return

        instructions = build_instructions(
            settings.project_name,
            languages=settings.reply_languages,
            robot_name=settings.robot_name,
            profile=self.profile,
            translator_target=self.lang,
            robot_face=robot_face,
        )
        provider = get_provider(
            self.provider_name, self.voice, instructions,
            greeting=None if self.summoned else greeting_for(self.profile),
            # Toolless by config for the translator, not by prompt: a
            # declared tool is a callable tool whatever the prompt says.
            use_tools=(self.profile != "translator"),
            # Near or far is the mode's (LISTEN_FAR_PROFILES): the sales host
            # hears the person in front, EmmaJoke/interpreter hear the room.
            vad_floor=settings.call_vad_floor(self.profile),
        )

        from app.metrics import active

        metrics_token = active.set(self.metrics)
        self.metrics.labels.update(model=getattr(provider, "model", settings.openai_model
                                                if self.provider_name == "openai" else ""),
                                   vad=settings.vad_mode if self.provider_name == "gemini"
                                   else settings.openai_turn_detection)
        try:
            async with provider:
                if hasattr(provider, "model"):
                    self.metrics.labels["model"] = provider.model
                self.provider = provider
                if self.summoned and settings.vad_summoned_bypass:
                    # The machine opened this session to talk to somebody
                    # at a distance; the near-field floor would filter out
                    # exactly that person's reply. See VadGate.stand_down.
                    provider.stand_down_floor()
                self._watch_segments(provider)
                self._open_call_ears(provider)
                turnlog.record("voice_ready")
                hold_speech = bool(settings.hold_speech
                                   and settings.half_duplex and not echo_barge
                                   and not settings.listens_far(self.profile))
                ready = {
                    "type": "ready",
                    "provider": self.provider_name,
                    # Which persona this session actually opened with — shown
                    # in the header, because "ไม่เห็นแปลภาษาเลย" turned out to
                    # mean a translator URL served by a pre-translator server,
                    # and nothing on screen said which mode was really running.
                    "profile": self.profile,
                    "voice": self.voice,
                    "input_rate": provider.input_sample_rate,
                    "output_rate": provider.output_sample_rate,
                    "session_limit_min": provider.session_limit_minutes,
                    "auto_resumes": provider.auto_resumes,
                    # The browser knows exactly when playback is happening,
                    # so it gates the mic rather than the server guessing.
                    "half_duplex": settings.half_duplex and not echo_barge,
                    "barge_in": echo_barge,
                    # Hold-speech rides ON half-duplex: she still cannot be
                    # interrupted; the page just queues what people say over
                    # her (above the floor) and sends it when her audio ends.
                    #
                    # NOT on a far-listening profile. Hold-speech tells her own
                    # echo from a person by one rule: the echo sits UNDER
                    # hold_floor. That holds for the near-field sales host, but
                    # the robot's omnidirectional array on `joke` hears its own
                    # speaker at peak 0.13–0.28 (measured 2026-10-01, well over
                    # the 0.04 floor) — so every sentence she says is queued as
                    # "someone talked over me" and flushed back as the next
                    # turn. She answered herself for whole paragraphs (owner:
                    # "emma ได้ยินเสียงตัวเองทำให้เพี้ยนพูดเองยาวๆ"). A far mic
                    # cannot separate loud echo from a person, so hold is off
                    # there; half-duplex still drops the mic while she speaks.
                    "hold_speech": hold_speech,
                    "hold_floor": settings.hold_speech_floor,
                }
                # Tap-to-ask topics (app/quick_asks.py), the robot face only:
                # the sales-room desk page's `ready` stays exactly as it was.
                self._robot_face_claim = robot_face
                if robot_face:
                    from app import quick_asks

                    asks = quick_asks.for_page(self.profile, settings.project_name)
                    if asks is not None:
                        ready["quick_asks"] = asks
                await self._send_json(ready)
                self._open_array_and_enhancer(provider, echo_barge, hold_speech)

                up = asyncio.create_task(self._browser_to_provider())
                down = asyncio.create_task(self._provider_to_browser())
                unanswered = asyncio.create_task(self._resume_after_silence())
                jobs = {up, down, unanswered}
                # A normal voice call does not imply a handshake. The camera
                # greeting requests it in events.announce; an explicit spoken
                # request reaches the fixed request_handshake tool instead.
                # Only when it is switched on. These tasks race under
                # FIRST_COMPLETED, so a task that returns immediately ends
                # the session immediately — with IDLE_TIMEOUT_S unset (the
                # default) an "off" watcher hung up on every guest the
                # instant they connected. A disabled feature must not be
                # present as a task at all.
                #
                # The canva follower had this exact bug five lines below the
                # comment describing it: `_follow_canva` returns immediately
                # when CANVA_URL is blank, and it sat in the race
                # unconditionally. The gallery machine never saw it — its
                # CANVA_URL is always set — so it waited for the first
                # profile with no presentation screen, where every session
                # died at "ready". Found by simulating exactly that.
                # ...and never under MULTI_SESSION: the Canva window is the
                # machine's, and N testers' sessions each polling and
                # narrating one shared window is the two-clocks bug times N.
                if settings.canva_url and settings.canva_poll_s > 0 and not settings.multi_session:
                    jobs.add(asyncio.create_task(self._follow_canva()))
                if settings.idle_timeout_s or self.summoned or settings.focus_enabled:
                    jobs.add(asyncio.create_task(self._close_when_nobody_is_there()))
                # Engagement (attention cue, invitation from denials, the
                # goodbye): in the race because the goodbye ends the call,
                # and only when one of them is on — it never returns
                # otherwise (the FIRST_COMPLETED lesson two blocks up).
                if self._wants_engagement_watch():
                    jobs.add(asyncio.create_task(self._engagement_watch()))
                _done, pending = await asyncio.wait(
                    jobs, return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

        except ProviderError as exc:
            turnlog.record("provider_error", category=_provider_failure_kind(exc))
            await self._send_json({"type": "error", "code": "provider_error", "message": str(exc)})
        except Exception as exc:
            logger.exception("voice session failed")
            await self._send_json({"type": "error", "code": "internal", "message": str(exc)})
        finally:
            # Nothing is in flight on a closed line: a set left non-empty
            # here would park announcements on this session's ghosts.
            self._clear_tools_in_flight("session_end")
            self._flush_call_debug()
            if getattr(self, "_array_tap", None) is not None:
                self._array_tap.report()
            self.metrics.finish("session_closed")
            active.reset(metrics_token)
            for name in ("_nudge_task", "_respeak_task", "_ears_task", "_invite_task",
                         "_follow_up_task"):
                task = getattr(self, name, None)
                if task is not None and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            speech_gestures.on_closed(self)
            ears = getattr(self, "_call_ears", None)
            if ears is not None:
                # What the in-call ear cost on THIS machine's event loop —
                # the robot's own number, not the PC's (app/call_wake.py).
                cost = ears.report()
                logger.info("in-call wake word: %.0fs of audio, %s ms per second, %d hit(s)",
                            cost["audio_s"], cost["ms_per_s"], cost["hits"])
                turnlog.record("focus_wake_cpu", **cost)
            if getattr(self, "_is_robot", False):
                # The robot's socket is gone. `app_gone` existed for this
                # and nothing called it (found 2026-09-01): the state said
                # "connected" until the next restart, every walk command
                # went down a socket that no longer existed and came back
                # "ok". The robot itself, if mid-walk, keeps walking —
                # that is the app's job to stop, and the reason the app
                # must cancel navigation on its own when the link drops.
                from app.tools import robot_link

                robot_link.app_gone()
            try:
                await self.ws.close()
            except Exception:
                pass

    #: Seconds between microphone reports during a call.
    MIC_REPORT_S = 5.0
    #: Peak below this is room noise, not a voice (same bar as the standby
    #: ears' SPEECH — one number for "is anybody talking" on both paths).
    MIC_SPEECH = 0.02

    def _mic_report(self, pcm16: bytes) -> None:
        """Say what the call's microphone is sending, every few seconds.

        The standby ears have had this since the day "เรียกแล้วไม่เกิดอะไรขึ้น"
        could not be diagnosed; the call never did, and on 2026-08-31 the
        same blindness came back one door over: the camera greeted, the
        browser's bytes reached this pump (`ears_open` logged), and then
        nothing — no `vad floor` line, no `heard`, the owner "พูดไม่ได้
        สั่งอะไรไม่ได้เลย". A page sending all-zero PCM (another stream
        still holding the device — the wake ears, in a handover), a
        microphone too far to register, and a Silero that never opened
        are three different repairs that leave the same empty log. This
        line separates them. Three verdicts, the standby's three, plus
        one the standby cannot need: a real microphone never reads an
        exact zero, so peak=0.0000 is not "quiet" — it is a second
        stream, and the fix is a reload, not a louder voice.
        """
        import math

        import numpy as np

        samples = np.frombuffer(pcm16, dtype="<i2").astype("float32") / 32768.0
        if not len(samples):
            return
        rms = math.sqrt(float((samples * samples).sum()) / len(samples))
        peak = float(np.abs(samples).max())
        st = getattr(self, "_mic_probe", None)
        if st is None:
            st = self._mic_probe = {"at": time.monotonic(), "peak": 0.0,
                                    "sum": 0.0, "n": 0, "zero": 0}
        st["peak"] = max(st["peak"], rms)
        st["sum"] += rms
        st["n"] += 1
        if peak == 0.0:
            st["zero"] += 1
        now = time.monotonic()
        if now - st["at"] < self.MIC_REPORT_S:
            return
        avg = st["sum"] / st["n"]
        gate = getattr(self.provider, "_vad_gate", None)
        segs = getattr(gate, "segments", None)
        if st["zero"] == st["n"]:
            # Measured 2026-09-01: the browser's track read live and
            # unmuted, the stream was the standby's own handed over, and
            # the device was delivering nothing to ANY program (WASAPI,
            # MME, WDM-KS all frameless). The fix was below the browser —
            # hence check-mic.cmd, which asks the device directly.
            verdict = ("DIGITAL SILENCE — every sample is exactly zero. A real "
                       "microphone never reads that. Run check-mic.cmd: if the "
                       "device shows NO FRAMES there too, it is the microphone "
                       "itself (mute button, replug the USB, reboot) — no page "
                       "reload or setting can help")
            if _array_held_by_app():
                # Measured on the robot 2026-10-10: the Bothlent array gives
                # Android one capture stream; with the app's 8-channel DOA
                # capture open first, the WebView's call mic read exact zeros
                # for the whole call — and the line above sent the reader to
                # a .cmd that does not exist on a robot.
                verdict = ("DIGITAL SILENCE — the robot app is holding the mic "
                           "array (DOA_ENABLED): this array gives Android one "
                           "capture stream, so the page's mic reads zeros. Set "
                           "DOA_ENABLED=false and restart")
        elif st["peak"] < self.MIC_SPEECH:
            verdict = ("too quiet to be speech — room noise only. Move closer, "
                       "or raise CALL_MIC_BOOST in .env")
        elif segs == 0:
            verdict = ("speech level reached but Silero opened no segment — "
                       "the detector, not the microphone")
        else:
            verdict = "speech level reached — if nothing is heard, look at Gemini's side"
        logger.info("call audio: avg=%.4f peak=%.4f (speech >= %.2f)%s — %s",
                    avg, st["peak"], self.MIC_SPEECH,
                    "" if segs is None else f" | vad segments={segs}", verdict)
        # Numbers only, into the turn log: the console is the one thing a
        # screenshot never contains, and the first report of this line
        # (2026-09-01) arrived as exactly that — a screenshot.
        turnlog.record("mic_report", avg=round(avg, 4), peak=round(st["peak"], 4),
                       zero_frames=st["zero"], frames=st["n"], vad_segments=segs,
                       verdict=verdict.split(" — ")[0][:40])
        st.update(at=now, peak=0.0, sum=0.0, n=0, zero=0)

    # ---- the robot's array as the call's ears (ARRAY_AUDIO, app/array_audio.py) ----

    def _open_array_and_enhancer(self, provider, echo_barge: bool, hold_speech: bool) -> None:
        """Both off (the default): neither attribute is set to anything but
        None and the page's bytes go up exactly as before."""
        from app import array_audio

        self._play_until = 0.0
        self._array_tap = array_audio.tap_for(
            self, input_rate=getattr(provider, "input_sample_rate", 16000),
            barge_in=echo_barge, hold_speech=hold_speech)
        self._enhancer = None
        if settings.enhance_enabled:
            from app import enhance

            self._enhancer = enhance.for_session(getattr(provider, "input_sample_rate", 16000))

    def _note_playback(self, seconds: float) -> None:
        """Reply audio handed to the page plays back to back from the moment
        it arrives (the page's playHead): the server's own clock of when
        Emma may still be audible, for the array, which has no AEC."""
        now = time.monotonic()
        self._play_until = max(getattr(self, "_play_until", 0.0), now) + seconds

    def _array_audible_until(self, now: float) -> float:
        """When Emma stops being audible by the server's clock: her reply
        bytes playing back to back, or the page's reported audio lead. 0 =
        she has not spoken on this call."""
        lead = display.remaining_lead()
        return max(getattr(self, "_play_until", 0.0), now + lead if lead > 0 else 0.0)

    def _array_speaking(self, now: float) -> bool:
        """Emma may be audible: her reply bytes are still playing by the
        server's clock, or the page reports an audio lead, plus the tail."""
        return now < self._array_audible_until(now) + settings.array_audio_echo_tail_s

    def _array_frame(self, page: bytes) -> bytes:
        """The page frame, or the array's audio in its place (never both)."""
        if not robot_face_link.is_robot_face(self):
            return page                 # ROBOT_FACE_TOKEN set and no hello yet
        now = time.monotonic()
        gate = getattr(self.provider, "_vad_gate", None)
        in_speech = getattr(gate, "in_speech", None) if gate is not None else None
        audible = self._array_audible_until(now)
        return self._array_tap.frame(page, now, speaking=self._array_speaking(now),
                                     in_speech=in_speech, audible_until=audible or None)

    def _call_debug_feed(self, pcm16: bytes) -> None:
        """Roll the last CALL_DEBUG_MAX_S of call-mic audio into a buffer.

        Off unless CALL_DEBUG=true. The call path writes no audio otherwise
        (turnlog is numbers only) — this is the WAKE_DEBUG equivalent for a
        live call, so the near-field floor (VAD_MIN_RMS) can be tuned by ear
        against front-vs-side recordings. It is customer speech on disk;
        data/call_debug is gitignored and this stays off outside a mic-chase.
        What is captured is exactly what the provider receives (post the
        browser mic chain) — i.e. what Gemini actually hears.
        """
        if not settings.call_debug:
            return
        buf = getattr(self, "_call_cap", None)
        if buf is None:
            buf = self._call_cap = bytearray()
        buf.extend(pcm16)
        cap = max(1, settings.call_debug_max_s) * CALL_DEBUG_RATE * 2
        if len(buf) > cap:
            del buf[:len(buf) - cap]

    def _flush_call_debug(self) -> None:
        """Write the buffered call audio at session end; newest few kept."""
        buf = getattr(self, "_call_cap", None)
        self._call_cap = None
        if not buf:
            return
        import wave

        try:
            CALL_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            path = CALL_DEBUG_DIR / time.strftime("call-%Y%m%d-%H%M%S.wav")
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(CALL_DEBUG_RATE)
                w.writeframes(bytes(buf))
            for old in sorted(CALL_DEBUG_DIR.glob("call-*.wav"))[:-CALL_DEBUG_KEEP]:
                old.unlink(missing_ok=True)
            logger.info("call debug: wrote %s (%.1fs of call audio)",
                        path.name, len(buf) / 2 / CALL_DEBUG_RATE)
        except Exception:
            logger.exception("call debug: could not write clip")

    # ---- listening focus (docs/listening-focus-2026-10-09.md) ----

    def _watch_segments(self, provider) -> None:
        """Ask the provider to report every closed speech segment, when focus
        or voice prints want them. Both off (the default): not even asked.

        Loud when it cannot: FOCUS_ENABLED with no local segmenter means
        `may_open` is never asked — the switch is on and nothing changes,
        the misconfiguration shape this project keeps paying for.
        """
        from app import focus

        want_focus = focus.enabled()
        want_voice = bool(settings.voiceprint_enabled)
        if not (want_focus or want_voice):
            return
        watch = getattr(provider, "watch_segments", None)
        ok = bool(watch(self._segment_closed, keep_audio=want_voice)) if watch else False
        if not ok:
            logger.warning("FOCUS_ENABLED / VOICEPRINT_ENABLED need VAD_MODE=local "
                           "(and its model): this session has no local speech "
                           "segments, so neither can see anything")
            turnlog.record("focus_unavailable", why="no_local_vad")

    def _segment_closed(self, gate_segment: int, t0: float, t1: float,
                        pcm16: bytes) -> str | None:
        """A speech segment just closed; called on the audio path by the
        provider, before the end-of-turn signal. Returns the speaker note to
        send ahead of it, or None.

        Attribution first, the voice print second: `focus.observe_voice`
        for this segment must arrive after `focus.segment_closed` made the
        segment known. The print itself runs on the voice-print thread —
        nothing here may wait on a model.
        """
        from app import focus

        segment = next(_SEGMENT_IDS)       # unique per process, see _SEGMENT_IDS
        note = None
        mine = getattr(self, "_focus_epoch", None)
        if mine is not None and mine != focus.epoch():
            # Superseded: a newer session reset focus while this one is still
            # unwinding. Its last segment belongs to no conversation — not the
            # new one's attribution, not the new one's voice clusters.
            return None
        if focus.enabled():
            label = focus.segment_closed(segment, t0, t1, epoch=mine)
            if (label is not None and settings.focus_speaker_labels
                    and getattr(self, "profile", "") != "translator"):
                note = focus.context_note(label)
        if settings.voiceprint_enabled and pcm16:
            _voiceprints.submit(segment, t0, t1, pcm16)
        return note

    #: With focus on, a conversation nobody faces any more ends after this
    #: long without a guest heard — instead of IDLE_TIMEOUT_S (120 s on the
    #: robot) of hearing whoever walks past. Only when the camera is
    #: delivering: a dead camera is not an empty room. Cheap to be early:
    #: on the robot the standing line rings a fresh line at once, so the
    #: person who comes back is heard straight away — only the previous
    #: visitor's words leave the model's context, which is the point.
    FOCUS_AWAY_S = 30.0

    # ---- engagement: behave like an established reception robot ----
    #
    # Owner, 9 Oct 2026: "ลองปรับให้เหมือนหุ่นต้อนรับอื่นให้ดีที่สุด". Focus
    # (app/focus.py) decides what is heard; this is the session's half — what
    # the robot DOES about it: hear its name from outside the lens, invite the
    # caller in, look at whoever speaks, and say goodbye to somebody who walked
    # off mid-conversation. Every switch is off by default and needs
    # FOCUS_ENABLED; off, none of these methods is reached and no task exists.
    # Anything the robot says by itself goes through `events.announce` (waits
    # for the ear, re-checks relevance after the wait, one at a time).

    #: The invitation names a side only past this many degrees off-axis.
    INVITE_SIDE_MIN_DEG = 15.0
    #: An invitation whose cause is older than this by the time everything
    #: queued before it has been heard is not said: "please step in" thirty
    #: seconds after the call is a sentence to whoever is there now.
    INVITE_STALE_S = 30.0
    #: `_quiet_moment`: a guest who just finished is owed a reply for at most
    #: this long (the model may choose silence), and the whole wait is
    #: bounded by QUIET_WAIT_S.
    REPLY_OWED_S = 8.0
    QUIET_WAIT_S = 20.0
    #: The watcher's tick — also the attention cue's top rate (4 a second).
    ENGAGE_TICK_S = 0.25
    #: The attention cue is resent only when the bearing moved this much:
    #: the page smooths, so finer steps are traffic and nothing else.
    ATTEND_STEP_DEG = 3.0
    #: Her voice may still be in the room this long after the server's clock
    #: says the last byte played: Android's output latency (~0.1-0.3 s, see
    #: `_array_frame`) plus a margin, so a goodbye's last syllable is not cut.
    AUDIBLE_TAIL_S = 0.5
    #: A voice at the door (opened or denied, `focus.last_voice_at`) this
    #: recent = somebody is talking now. Denied frames are asked about every
    #: 20 ms while they are loud, so a pause longer than this is a pause.
    VOICE_RECENT_S = 0.6
    #: Longest the goodbye waits for itself to be heard before ending the call.
    GOODBYE_HEARD_MAX_S = 15.0

    def _she_is_audible(self, now: float) -> bool:
        """Emma's reply may still be playing: the page's reported audio lead
        (index.html sends one) or, where the page sends none — the robot face,
        voice-preview.html — the server's own clock of reply bytes played back
        to back from arrival (`_note_playback`), plus AUDIBLE_TAIL_S.

        Review 10 Oct: engagement leaned on `events.announce`'s audio wait,
        which reads `display.remaining_lead()` — always 0 on the robot face. The
        invitation then went in while her answer to the caller was still being
        generated (client content mid-reply interrupts it — the Live API's own
        rule), and the goodbye's `idle_timeout` closed the page's AudioContext
        the moment the goodbye was GENERATED: nobody ever heard it.
        """
        until = self._array_audible_until(now)
        return bool(until) and now < until + self.AUDIBLE_TAIL_S

    async def _until_she_is_heard(self, max_wait: float) -> None:
        """Until her queued voice has played out (`_she_is_audible`), bounded."""
        deadline = time.monotonic() + max(0.0, max_wait)
        while time.monotonic() < deadline and self._she_is_audible(time.monotonic()):
            await asyncio.sleep(0.1)

    def _open_call_ears(self, provider) -> None:
        """FOCUS_WAKE: the name inside a camera-opened call (app/call_wake.py).

        Only where it can change anything: rule 2 (a face in front) binds only
        a summoned call with FOCUS_REQUIRE_FACE, and only a local VAD gate
        ever asks focus at all. The model loads off the event loop; until it
        has, the call simply has no ear for the name (fail open per input).
        """
        from app import focus

        if not (settings.focus_wake and focus.enabled() and self.summoned
                and settings.focus_require_face):
            return
        if getattr(provider, "_vad_gate", None) is None:
            return                       # _watch_segments has already said why
        self._ears_task = asyncio.create_task(
            self._load_call_ears(getattr(provider, "input_sample_rate", 16000)))

    async def _load_call_ears(self, rate: int) -> None:
        from app import call_wake

        try:
            spotter = await asyncio.to_thread(call_wake.load)
        except Exception:
            logger.exception("in-call wake word could not load")
            return
        if spotter is not None:
            self._call_ears = call_wake.CallEars(spotter, rate)

    def _hear_name(self, pcm16: bytes) -> None:
        """Feed the call's ear; on the name, open focus's out-of-view window
        and, for a caller nobody can see, ask for the invitation."""
        try:
            hit = self._call_ears.feed(pcm16)
        except Exception:
            logger.exception("in-call wake word failed — off for this call")
            self._call_ears = None
            return
        if not hit:
            return
        from app import focus

        if self._she_is_audible(time.monotonic()):
            # Her own voice saying her own name ("เอ็มม่าค่ะ") through a mic
            # with no echo cancelling. Half-duplex pages send nothing while
            # she plays; a page that does must not open the room to itself.
            # Not `display.remaining_lead()` alone: the robot face page — the
            # only one this ear runs for — never reports a lead (review 10 Oct).
            turnlog.record("focus_wake", ignored="own_voice")
            return
        info = focus.heard_name(time.monotonic())
        if settings.focus_invite and info["camera"] and not info["in_view"]:
            self._schedule_invite(info["bearing"], "name")

    def _guest_speaking(self) -> bool:
        """Somebody is talking right now: a segment is open, or focus was just
        asked about a voice — allowed or DENIED. A voice focus refused never
        opens a segment, and reading "no segment" as "silence" said the goodbye
        over a guest asking with their face in a brochure (review 10 Oct)."""
        gate = getattr(getattr(self, "provider", None), "_vad_gate", None)
        if getattr(gate, "in_speech", False):
            return True
        from app import focus

        voice = focus.last_voice_at()
        return voice is not None and time.monotonic() - voice < self.VOICE_RECENT_S

    def _reply_owed(self, now: float) -> bool:
        """The guest finished a segment and no reply audio has come yet.

        The window `events.announce` cannot see: between a guest's
        activity_end and the first chunk of the answer (~1.2 s measured) the
        audio queue is empty and turn_idle is set, so an announcement sent
        then lands while the model is generating — the robot talking over
        its own answer, the barge-in bug again."""
        provider = getattr(self, "provider", None)
        ended = getattr(provider, "_speech_ended_at", None)
        if ended is None or getattr(provider, "_replied_since_end", True):
            return False
        return now - ended < self.REPLY_OWED_S

    async def _quiet_moment(self) -> None:
        """Until nobody is mid-sentence, no answer is owed and her own answer
        has been heard (bounded). The last one is announce's job everywhere
        but the robot face, whose page reports no audio lead — and an owed
        answer that has only STARTED is still being generated there."""
        deadline = time.monotonic() + self.QUIET_WAIT_S
        while time.monotonic() < deadline:
            now = time.monotonic()
            if not (self._guest_speaking() or self._reply_owed(now)
                    or self._she_is_audible(now)):
                return
            await asyncio.sleep(0.2)

    def _schedule_invite(self, bearing: float | None, why: str) -> None:
        """One invitation at a time, at most one per FOCUS_INVITE_EVERY_S —
        per ROBOT, not per line (`focus.claim_invite`): the standing line
        rings a fresh line seconds after the last one ends, and a per-line
        limit restarted with every ring (review 10 Oct)."""
        from app import focus

        task = getattr(self, "_invite_task", None)
        if task is not None and not task.done():
            return
        now = time.monotonic()
        if not focus.claim_invite(now, settings.focus_invite_every_s):
            turnlog.record("focus_invite", sent=False, why=why, reason="rate")
            return
        self._invite_at = now
        self._invite_task = asyncio.create_task(self._invite(bearing, why, now))

    async def _invite(self, bearing: float | None, why: str, since: float) -> None:
        """Pepper's BasicAwareness in words: the caller is outside the zone,
        so tell them where the zone is. Never over her own sentence or the
        guest's (`_quiet_moment`, then announce's own waits)."""
        from app import events, focus

        await self._quiet_moment()

        def still_relevant() -> bool:
            now = time.monotonic()
            if now - since > self.INVITE_STALE_S:
                return False
            away = focus.engaged_age()
            # Camera dead: stepping in front would not make them heard.
            # Somebody engaged now: they came in (or are being served).
            if away is None or away < max(0.5, float(settings.focus_hold_s)):
                return False
            # The name was heard but nobody was, after it: a detector false
            # fire, or a caller who said nothing more — no sentence to an
            # empty room for it (the window it opened is all it gets).
            if why == "name" and self._last_heard_at <= since:
                return False
            # Still talking (the caller, the guest), or her answer still
            # playing on a page that reports no lead: not over either.
            return not (self._guest_speaking() or self._she_is_audible(now))

        await events.announce(_invite_text(bearing, why), source="focus_invite",
                              still_relevant=still_relevant)

    def _wants_engagement_watch(self) -> bool:
        from app import focus

        if not focus.enabled():
            return False
        return bool(settings.focus_attention
                    or (settings.focus_goodbye and self.summoned)
                    or (settings.focus_invite and settings.focus_invite_denied_n > 0
                        and self.summoned and settings.focus_require_face))

    async def _engagement_watch(self) -> None:
        """The attention cue, the invitation from denials, the goodbye.

        Races under FIRST_COMPLETED, so it returns only when the goodbye has
        ended the call; one failing tick is logged and the next one runs.
        """
        from app import focus

        failed_at = float("-inf")
        while True:
            await asyncio.sleep(self.ENGAGE_TICK_S)
            now = time.monotonic()
            try:
                if settings.focus_attention:
                    await self._send_attention(now)
                if (settings.focus_invite and settings.focus_invite_denied_n > 0
                        and self.summoned and settings.focus_require_face
                        and (getattr(self, "_greeting_turn_seen", False)
                             or getattr(self, "_ever_heard", False))):
                    # Only once this line holds a conversation. On a virgin
                    # standing line (all day, nobody greeted) voices denied
                    # again and again from one side are the staff chatting
                    # beside the robot that focus exists to ignore — and the
                    # invitation itself un-virgins the line, which the idle
                    # rule then ends and the standing line re-rings: an
                    # invitation into the same chat every half minute
                    # (review 10 Oct). A caller by NAME is not gated here.
                    bearing = focus.calling_out_of_view(now, settings.focus_invite_denied_n)
                    if bearing is not None:
                        self._schedule_invite(bearing, "voice")
                if (settings.focus_goodbye and self.summoned
                        and not self._goodbye_done and self._left_mid_conversation(now)):
                    self._goodbye_done = True              # once per call
                    if await self._say_goodbye():
                        return
            except asyncio.CancelledError:
                raise
            except Exception:
                if now - failed_at > 60.0:
                    failed_at = now
                    logger.exception("engagement watcher tick failed")

    async def _send_attention(self, now: float) -> None:
        """Furhat's gaze: the bearing the face should turn to, to the robot's
        own face page only (the one with the mascot), only when it moved."""
        if not robot_face_link.is_robot_face(self):
            return
        from app import focus

        cue = focus.attention(now)
        bearing = None if cue["bearing"] is None else round(float(cue["bearing"]), 1)
        last = self._attention_sent
        if last is not _UNSENT:
            if bearing is None and last is None:
                return
            if (bearing is not None and last is not None
                    and abs(bearing - last) < self.ATTEND_STEP_DEG):
                return
        self._attention_sent = bearing
        await self._send_json({"type": "attention", "bearing": bearing,
                               "source": cue["source"]})

    def _left_mid_conversation(self, now: float) -> bool:
        """The person this camera-opened call was with walked away from it.

        All of: greeted and heard (a conversation happened); a face was
        engaged during THIS call and none has been for FOCUS_GOODBYE_AFTER_S
        (never under FOCUS_HOLD_S — a glance is not a leave); nobody heard
        since they went (a caller by name out of view is still talking to
        her); and they were heard within FOCUS_AWAY_S before going — after a
        long silence it was not a conversation they walked out of."""
        from app import focus

        if not (getattr(self, "_greeting_turn_seen", False)
                and getattr(self, "_ever_heard", False)):
            return False
        after = max(float(settings.focus_hold_s), float(settings.focus_goodbye_after_s))
        away = focus.engaged_age(now)
        if away is None or away < after:
            return False                    # camera dead, or still here
        if away >= now - getattr(self, "_began_at", now):
            return False                    # nobody engaged during this call
        left_at = now - away
        heard_at = self._last_heard_at
        if heard_at > left_at:
            return False
        voice = focus.last_voice_at()
        if voice is not None and voice > left_at:
            # A voice at the door since the face went, even one focus
            # refused: the guest reading a brochure (face lost) asking a
            # question, a child under the lens, the guest a step to the side.
            # Not a walk-away to say goodbye to and hang up on (review 10 Oct).
            return False
        return left_at - heard_at <= self.FOCUS_AWAY_S

    async def _say_goodbye(self) -> bool:
        """Say a brief goodbye, then end the call. True = the call ended.

        The reception norm (a robot does not go on answering the room after
        its guest left): one sentence through announce, and then the line
        closes — the standing line rings a fresh one, so the next visitor
        meets a model with no previous visitor in its context. If they come
        back while she says it, the conversation simply goes on.
        """
        from app import events, focus

        heard_at = self._last_heard_at

        def still_gone() -> bool:
            away = focus.engaged_age()
            return (away is not None and away >= max(0.5, float(settings.focus_hold_s))
                    and self._last_heard_at == heard_at and not self._guest_speaking())

        def may_say() -> bool:
            # Not over her own answer still playing (the robot face reports
            # no audio lead, so announce's own wait cannot see it).
            return still_gone() and not self._she_is_audible(time.monotonic())

        await self._quiet_moment()
        if not await events.announce(GOODBYE_TEXT, source="focus_goodbye",
                                     still_relevant=may_say):
            return False
        idle = getattr(self, "turn_idle", None)
        if idle is not None:
            try:
                await asyncio.wait_for(idle.wait(), timeout=events.TURN_WAIT_S)
            except asyncio.TimeoutError:
                pass
        # Heard, not generated: `idle_timeout` makes the page end the call and
        # close its AudioContext, so ending at turn_complete cut the goodbye
        # before a word of it played on the robot face (review 10 Oct).
        await self._until_she_is_heard(self.GOODBYE_HEARD_MAX_S)
        if not still_gone() or self._she_is_audible(time.monotonic()):
            turnlog.record("focus_goodbye", ended=False)
            return False
        quiet = round(time.monotonic() - self._last_heard_at)
        logger.info("the guest walked away mid-conversation — said goodbye, ending the call")
        turnlog.record("focus_goodbye", ended=True)
        turnlog.record("idle_timeout", quiet_s=quiet, why="left")
        await self._send_json({"type": "idle_timeout", "quiet_s": quiet})
        from app.tools import slides

        try:
            await slides.shutdown_display()
        except Exception:
            logger.exception("could not close the display after the goodbye")
        return True

    def _greeting_turn_done(self) -> None:
        """The first completed turn of a summoned session is its greeting.

        `turn_complete` means the model stopped *generating*; the browser
        still has the whole greeting queued (the model writes 4-6x faster
        than it speaks). The ears open when that queue runs dry — the same
        clock every screen already waits on — and not one moment earlier,
        because with HALF_DUPLEX the browser is muting its mic until then
        anyway; the server hold just closes the window *before* playback
        that the browser cannot see.
        """
        # getattr, like `_silent_block_seen` below: tests build sessions
        # with __new__ and drive this pump without __init__ ever running.
        if getattr(self, "_greeting_turn_seen", False):
            return
        closed = getattr(self, "_ears_closed_until", None)
        if closed is None:
            # The ears are already open. Only one case is still a greeting:
            # a summoned line nobody has spoken on — the standing line,
            # whose 10 s hold a mic frame ended long ago, greeted by the
            # camera minutes or hours later. Returning here (as this did
            # until review 10 Oct) left the greeted line "virgin": never
            # idled, re-greetable, and once the guest spoke ended by the
            # 30 s ABANDON rule instead of IDLE_TIMEOUT_S. A turn on a line
            # somebody already spoke on is an answer, not a greeting.
            if not (getattr(self, "summoned", False)
                    and not getattr(self, "_ever_heard", False)):
                return
        self._greeting_turn_seen = True
        now = time.monotonic()
        # The idle clock starts when the greeting has been HEARD, not at the
        # dial: a standing line is dialled hours before its greeting, and
        # `_last_heard_at` (guest speech only) still held the dial time —
        # the first idle tick after the greeting hung up on a guest who had
        # not had a breath to answer (probe 10 Oct). Not `_last_heard_at`:
        # that clock is guest speech and nothing else.
        self._idle_from = max(now + display.remaining_lead(),
                              self._array_audible_until(now))
        if closed is not None:
            self._ears_closed_until = min(closed, now + display.remaining_lead())

    async def _nav_unlock(self, event: dict) -> None:
        """`{type: "nav_unlock", pin}` from the robot face's นำทาง slot.

        Finding #23: the unlock that lets ROBOT_NAV_VOICE=unlocked start a
        walk by voice is bound to THIS session, not to an IP. Only the robot
        face may send it (robot_face_link.is_robot_face — the user agent, and
        the face token when one is set); the wrong-PIN lockout is still keyed
        by the TCP peer, inside robot_nav.unlock.
        """
        from app import robot_nav

        if not robot_face_link.is_robot_face(self):
            logger.info("nav_unlock from a page that is not the robot face — ignored")
            await self._send_json({"type": "nav_unlock_result", "ok": False,
                                   "reason": "หน้านี้ไม่ใช่หน้าจอหุ่น"})
            return
        client = getattr(getattr(self.ws, "client", None), "host", None) or ""
        try:
            result = robot_nav.unlock(event.get("pin"), client, session=self)
        except robot_nav.PinError as exc:
            await self._send_json({"type": "nav_unlock_result", "ok": False,
                                   "reason": exc.reason, "status": exc.status})
            return
        await self._send_json({"type": "nav_unlock_result", **result})

    # ---- browser -> provider ----

    async def _browser_to_provider(self) -> None:
        try:
            while True:
                message = await self.ws.receive()
                if message.get("type") == "websocket.disconnect":
                    return

                if (data := message.get("bytes")) is not None:
                    if self._ears_closed_until is not None:
                        if time.monotonic() < self._ears_closed_until:
                            continue
                        self._ears_closed_until = None
                        logger.info("summoned session: greeting %s — listening now",
                                    "heard" if self._greeting_turn_seen
                                    else "never came (hold expired)")
                        turnlog.record("ears_open",
                                       greeted=self._greeting_turn_seen)
                    if getattr(self, "_array_tap", None) is not None:
                        data = self._array_frame(data)
                        if not data:
                            continue
                    if getattr(self, "_enhancer", None) is not None:
                        data = self._enhancer.process(data)
                        if not data:
                            continue
                    self._mic_report(data)
                    self._call_debug_feed(data)
                    if getattr(self, "_call_ears", None) is not None:
                        # Before the gate sees this frame: a hit here opens
                        # focus's window in time for the gate's question on
                        # this very frame, so "Emma, ..." in one breath is
                        # heard with its pre-roll (FOCUS_WAKE).
                        self._hear_name(data)
                    await self.provider.send_audio(data)
                    continue

                if (text := message.get("text")) is not None:
                    try:
                        event = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                        continue
                    if event.get("type") == "stop":
                        return
                    if event.get("type") == "barge_in":
                        # A camera-summoned call normally holds its ears until
                        # the greeting is heard. EmmaJoke's browser first
                        # ducks the speaker and confirms a person is still
                        # speaking; only then may that person interrupt the
                        # greeting. The later mic frames must not be dropped
                        # by the original summoned hold.
                        if (self.summoned and self.profile == "joke"
                                and self._ears_closed_until is not None):
                            self._ears_closed_until = None
                            turnlog.record("ears_open", greeted=False, why="barge_in")
                        continue
                    if event.get("type") == "display.render_ack":
                        from app import plan_display

                        session_id = turnlog.session_id.get()
                        if session_id:
                            plan_display.receive_ack(session_id, event)
                        continue
                    if event.get("type") == "client_metric":
                        self.metrics.client(event)
                        continue
                    if event.get("type") == "quick_ask":
                        await self._quick_ask(event)
                        continue
                    if event.get("type") == "story_report":
                        self._story_report(event.get("report"))
                        continue
                    if event.get("type") == "mic_state":
                        # The browser half of the `call audio:` report:
                        # which device, whether the OS muted it, whether
                        # the stream was handed over from standby or
                        # opened fresh. Numbers and a device name only.
                        logger.info("call mic (%s): %r muted=%s state=%s handed=%s",
                                    event.get("why"), event.get("label"),
                                    event.get("muted"), event.get("state"),
                                    event.get("handed"))
                        turnlog.record("mic_state", why=event.get("why"),
                                       muted=bool(event.get("muted")),
                                       state=event.get("state"),
                                       handed=bool(event.get("handed")))
                        continue
                    if event.get("type") == "audio_lead":
                        # How far the browser's audio queue runs ahead of the
                        # guest's ear. The other screens wait that long before
                        # switching, or they show the next slide while the
                        # robot is still talking about the last one.
                        display.set_audio_lead(event.get("ms", 0))
                        continue
                    if event.get("type") == "nav_unlock":
                        # The staff PIN typed on the robot face, over the
                        # face's own socket (finding #23): the HTTP unlock
                        # was keyed by client IP, and under adb reverse the
                        # robot and every browser on the server PC are all
                        # 127.0.0.1. Never logged with the PIN.
                        await self._nav_unlock(event)
                        continue
                    if (event.get("type") in robot_contracts.INBOUND_TYPES
                            or event.get("type") == "face_hello"):
                        # The face page relaying the Android app: acks,
                        # status, caps, and its token hello. One module owns
                        # these frames (design §2a); it checks that this
                        # socket is the robot face, the frame size and every
                        # id, and drops what fails. The raw size goes along
                        # because the limit is on what arrived, not on what
                        # survived parsing.
                        robot_face_link.receive(self, event,
                                                raw_size=len(text.encode("utf-8")))
                        continue
                    if event.get("type") == "robot_ready":
                        # The Android app on the robot identifying itself, and
                        # reporting which POIs its own map actually contains.
                        # The robot is the authority on that — the points were
                        # made by walking it there once — so this is the only
                        # place `KNOWN_PLACES` comes from.
                        #
                        # "A browser never sends this" was the whole guard
                        # until 2026-09-01. It is true of the page this
                        # project ships and of nothing else: any client on
                        # the LAN holding WS_TOKEN (it is in every browser's
                        # URL) could send it, become "the robot", and take
                        # every walk command. So the robot carries its own
                        # credential, and no ROBOT_TOKEN configured means no
                        # socket is ever the robot.
                        from app.tools import robot_link

                        if (not settings.robot_token
                                or event.get("token") != settings.robot_token):
                            logger.warning("robot_ready ignored — %s",
                                           "ROBOT_TOKEN is not set on this server"
                                           if not settings.robot_token
                                           else "wrong token")
                            turnlog.record("robot_rejected",
                                           why="no_server_token" if not settings.robot_token
                                           else "bad_token")
                            continue
                        if not robot_link.app_connected(event.get("places")):
                            logger.warning("robot_ready ignored — invalid places")
                            continue
                        self._is_robot = True
                        turnlog.record("robot_ready",
                                       places=len(robot_link.KNOWN_PLACES))
                        continue
                    if event.get("type") == "robot_arrived":
                        # Sent when the SDK's navigation callback fires, long
                        # after the tool call that started the walk returned.
                        # Only from the socket that proved it is the robot,
                        # and only while a walk is pending (`arrived` checks
                        # that half) — an arrival from anywhere else is a
                        # turn injected into the conversation by a stranger.
                        from app.tools import robot_link

                        if not getattr(self, "_is_robot", False):
                            logger.warning("robot_arrived ignored — this socket "
                                           "never sent an accepted robot_ready")
                            continue
                        place = event.get("place")
                        ok = event.get("ok")
                        if await robot_link.arrived(place, ok):
                            turnlog.record("robot_arrived", place=place, ok=ok)
                        continue
                    if event.get("type") == "played":
                        # Browser reporting how much reply audio it actually
                        # played before the guest cut in. Only OpenAI needs it
                        # for truncation -- but the page has STOPPED her voice
                        # (a tap over her, a barge-in): the server's playback
                        # clock ends now too. Gemini sends no `interrupted`
                        # once generation is done, so without this the next
                        # answer was queued behind audio nobody heard and the
                        # reply window, follow-up and idle hold all ran late
                        # (review 13 Oct).
                        self._play_until = min(getattr(self, "_play_until", 0.0), time.monotonic())
                        truncate = getattr(self.provider, "truncate", None)
                        if truncate is not None:
                            await truncate(int(event.get("heard_ms", 0)))
        except (WebSocketDisconnect, RuntimeError):
            return
        except Exception:
            logger.exception("browser->provider pump failed")

    # ---- provider -> browser ----

    async def _provider_to_browser(self) -> None:
        try:
            async for event in self.provider.events():
                if settings.turn_idle_tracks_tools:
                    self.prune_tools_in_flight()
                if event.kind == "speech_stopped":
                    self.metrics.speech_end(event.text or "provider_vad_event")
                    if getattr(self, "_robot_face_claim", False):
                        # The robot face shows "thinking" the moment the guest
                        # is heard to have finished (owner, 10 Oct: "ความเร็ว
                        # การตอบ"): measured, ~1.1 s pass from here to her first
                        # audio, and a face frozen in "listening" for that long
                        # reads as not having heard. A cue, not a turn: the desk
                        # page never gets it and its frames are unchanged.
                        await self._send_json({"type": "heard_end"})
                    continue
                if event.kind == "usage":
                    self.metrics.usage(event.data or {})
                    continue
                if event.kind in {"assistant_transcript", "tool_call"}:
                    self.metrics.ensure()
                if event.kind == "audio" and event.audio:
                    if (turn_id := self.metrics.audio()) is not None:
                        await self._send_json({"type": "metric_turn", "turn_id": turn_id})
                    self._sent_audio_ms += (
                        len(event.audio) / 2 / self.provider.output_sample_rate * 1000
                    )
                    # Every session, not only with the array tap: the robot
                    # face page (voice-preview.html) never reports an audio
                    # lead, so `display.remaining_lead()` is 0 there and this
                    # is the only clock of her voice that engagement can read
                    # (`_she_is_audible`). Arithmetic only; with no array tap
                    # and engagement off nothing reads it.
                    self._note_playback(len(event.audio) / 2 / self.provider.output_sample_rate)
                    # Her arms while she talks (SPEECH_GESTURES, robot face
                    # only, off by default): paced from the clock just moved.
                    speech_gestures.on_audio(self)
                    await self.ws.send_bytes(event.audio)

                elif event.kind == "speech_started":
                    self.metrics.finish("new_speech")
                    self.metrics.pending_end = None
                    self._guest_started_turn()
                    # Close the question the moment a voice starts, not when
                    # the transcript lands. A guest thinking out loud through
                    # "เอ่อ... เคยมาค่ะ ตอนเด็กๆ" takes several seconds, and
                    # the grace timer must not expire and start the next slide
                    # over the top of them mid-sentence.
                    from app.tools import slides

                    slides.answer_received()
                    await self._send_json({"type": "speech_started"})
                    # The guest's own words never reach the robot's screen —
                    # a public misreading of "ปิดไฟ" as "bit fire" makes a
                    # working robot look broken, and Emma reads phone numbers
                    # back out loud. An indicator says the same thing safely.
                    await display.set_phase("listening")

                elif event.kind == "interrupted":
                    self.metrics.finish("interrupted")
                    self._sent_audio_ms = 0.0
                    # The page stops her audio on this message: so does the
                    # array's playback clock (its tail still runs).
                    self._play_until = min(getattr(self, "_play_until", 0.0), time.monotonic())
                    speech_gestures.on_interrupted(self)
                    self._spoke_this_turn = False
                    self._said_this_turn = ""
                    # A barge-in mid-tour is a pause, not a skip: keep the deck
                    # on the current slide so the advance nudge resumes it
                    # rather than stepping over a slide nobody finished hearing.
                    from app.tools import slides

                    slides.pause_for_barge_in()
                    # Same reason `pause_for_barge_in` exists, one screen
                    # over: the queue holds words nobody heard, and the
                    # chest screen must not finish the sentence for her.
                    display.drop_unheard()
                    await self._send_json({"type": "interrupted"})
                    await display.set_phase("listening")

                elif event.kind == "resumed":
                    # Picked the session back up past the duration cap. The
                    # model is connected and idle; if a tour was running, it
                    # needs telling to carry on. `_nudge_tour_if_stalled`
                    # already checks everything that matters, except that it
                    # normally requires the model to have spoken this turn —
                    # and across a reconnect there was no turn at all.
                    logger.info("provider resumed — checking whether a tour is owed")
                    # A resumed model is idle with no turn to finish; calls
                    # of the old connection will never report back.
                    self._clear_tools_in_flight("resumed")
                    self._spoke_this_turn = True
                    await self._nudge_tour_if_stalled()
                    self._spoke_this_turn = False

                elif event.kind == "turn_complete":
                    self.metrics.finish()
                    # Evidence line for "ทำงานแต่ไม่มีเสียง" mornings
                    # (2026-08-27, page parked overnight): a transcript with
                    # (almost) no audio behind it. When a silent morning
                    # shows NO silent_turn lines, the audio did reach the
                    # browser and the fault is the machine's output device —
                    # identical on screen, opposite fixes.
                    if self._spoke_this_turn and self._sent_audio_ms < 200:
                        turnlog.record("silent_turn",
                                       audio_ms=round(self._sent_audio_ms))
                    audio_ms, self._sent_audio_ms = self._sent_audio_ms, 0.0
                    display.end_turn()
                    self._greeting_turn_done()
                    if self._spoke_this_turn or audio_ms >= 200:
                        # The guest's reply window (FOCUS_REPLY_WINDOW_S)
                        # starts when her voice has played out -- the server's
                        # own playback clock, since the robot face reports no
                        # audio lead (`remaining_lead` reads 0 there).
                        from app import focus

                        now = time.monotonic()
                        focus.her_turn_ended(max(now + display.remaining_lead(),
                                                 getattr(self, "_play_until", 0.0) or 0.0),
                                             epoch=getattr(self, "_focus_epoch", None))
                    idle = getattr(self, "turn_idle", None)
                    if idle is not None:
                        if settings.turn_idle_tracks_tools and getattr(
                                self, "_tools_in_flight", None):
                            # gemini-3.8 sends turn_complete before (13/15)
                            # or during its tool call; the turn is not over
                            # until the tool's own reply completes, so an
                            # announcement here would land inside that gap.
                            self._idle_held_by_tools = True
                        else:
                            idle.set()
                            self._idle_held_by_tools = False
                    await self._send_json({"type": "turn_complete"})
                    if getattr(self, "_screen_owed", False) and not getattr(self, "_screen_staged", False):
                        # The page clears its stage on this same event; the
                        # story's taps go with it.
                        self._story_taps = set()
                    await self._offer_choices()
                    await self._nudge_tour_if_stalled()
                    self._maybe_schedule_follow_up()
                    self._spoke_this_turn = False
                    if getattr(self, "_silent_block_seen", False):
                        self._silent_block_seen = False
                        if not getattr(self, "_respeak_fired", False):
                            # Once per user turn: if the retry also comes
                            # back as a block, asking a third time is a loop,
                            # not a fix. Re-armed when the user next speaks.
                            self._respeak_fired = True
                            self._respeak_task = asyncio.create_task(self._respeak_silent_block())

                elif event.kind == "user_transcript":
                    # A separate ASR caption of the guest audio. Gemini's
                    # conversational model consumes the audio directly, so
                    # this text can be wrong even when Emma understood and
                    # answered the spoken request correctly. Keep it useful
                    # for diagnostics without treating it as model truth.
                    turnlog.record("heard", text=event.text or "")
                    self._guest_text = (getattr(self, "_guest_text", "") + " " + (event.text or ""))[-300:]
                    # Kept so the destructive slide tools can check whether
                    # anybody asked for what they are about to do. See
                    # `app/heard.py` — this is the field that decides. Per
                    # session too, with its time, for the body/nav guards.
                    _utt = (event.data or {}).get("utterance_id")
                    heard.record(event.text or "", session=self,
                                 utterance_id=_utt if isinstance(_utt, str) else None)
                    # Somebody is in the room. Only guest speech resets the
                    # idle clock — the robot narrating to nobody is the case
                    # the timer exists to end, and it is busy throughout.
                    self._last_heard_at = time.monotonic()
                    self._ever_heard = True
                    self._respeak_fired = False
                    payload = {"type": "user_transcript", "text": event.text or ""}
                    utterance_id = (event.data or {}).get("utterance_id")
                    if isinstance(utterance_id, str):
                        payload["utterance_id"] = utterance_id
                    await self._send_json(payload)

                elif event.kind == "assistant_transcript":
                    if (event.text or "").strip():
                        self._spoke_this_turn = True
                    # A big atomic chunk full of newlines is text the model
                    # wrote but never voiced — spoken words stream in tiny
                    # word-sized pieces (measured: ≤13 chars). Three rap
                    # verses on 2026-08-26 arrived exactly this shape, on
                    # screen and silent.
                    _chunk = event.text or ""
                    if _chunk.count("\n") >= 2 and len(_chunk) >= 40:
                        self._silent_block_seen = True
                        turnlog.record("silent_block", chars=len(_chunk))
                    turnlog.record("said", text=event.text or "")
                    self._said_since_guest = (getattr(self, "_said_since_guest", "") + (event.text or ""))[-400:]
                    self._said_this_turn = (getattr(self, "_said_this_turn", "") + (event.text or ""))[-400:]
                    await self._send_json({"type": "assistant_transcript", "text": event.text or ""})
                    # ...and to the robot's own screen, which unlike this tab
                    # is being read by the guest — so it goes through the
                    # audio clock instead of straight out. See app/display.py.
                    await display.say(event.text or "")
                    await display.set_phase("speaking")

                elif event.kind == "reconnected":
                    # The transport rotated without a go_away (gemini.py's
                    # silent rejoin). Nothing for the browser; calls of the
                    # old connection will never report back.
                    self._clear_tools_in_flight("reconnect")

                elif event.kind == "tool_dropped":
                    self._tool_finished(event, dropped=True)

                elif event.kind == "tool_call":
                    self._tool_started(event)
                    # Review 12 Oct: a follow-up must not land while a tool runs
                    # (gemini-3.8 completes a turn BEFORE its call; with
                    # TURN_IDLE_TRACKS_TOOLS off nothing else holds it). The
                    # answer stays pending: the tool's spoken reply re-arms it.
                    self._follow_up_gen = getattr(self, "_follow_up_gen", 0) + 1
                    turnlog.record("tool", name=event.text or "")
                    await self._send_json({"type": "tool_call", "name": event.text or ""})

                elif event.kind == "tool_result":
                    self._tool_finished(event, dropped=False)
                    # Failures get a line of their own. 2026-08-26 16:26:
                    # play_youtube failed live ("มีข้อผิดพลาดนิดหน่อยค่ะ"),
                    # the same call worked from a fresh process minutes
                    # later, and the log held only the tool's *name* — the
                    # transient error had no evidence anywhere, which is the
                    # WAKE_DEBUG lesson again. Error text is tool-authored,
                    # never guest speech, so it is safe to keep.
                    _res = event.data or {}
                    if isinstance(_res, dict) and (
                        _res.get("ok") is False or "error" in _res
                    ):
                        turnlog.record(
                            "tool_error", name=event.text or "",
                            error=str(_res.get("error")
                                      or _res.get("message") or _res)[:200])
                    payload = {
                        "type": "tool_result",
                        "name": event.text or "",
                        "result": event.data or {},
                    }
                    # Mirror the slide into the conversation window too. With
                    # only /display showing it, running a presentation with no
                    # second window open looked like nothing happened at all.
                    data = event.data or {}
                    changed_screen = isinstance(data, dict) and (
                        "slide" in data or "now_showing" in data or data.get("cleared")
                    )
                    if changed_screen:
                        from app.tools import slides

                        payload["slide"] = slides.current_slide()
                        payload["displays"] = display.client_count()
                        # The audio lead at the instant the picture changed is
                        # the whole answer to "ทำไมภาพมาก่อนเสียง" — reading it
                        # afterwards is too late, it has already drained.
                        shown = payload["slide"] or {}
                        turnlog.record(
                            "screen",
                            tool=event.text or "",
                            slide=shown.get("id"),
                            position=shown.get("position"),
                            audio_lead_s=round(display.remaining_lead(), 1),
                        )
                    self._note_screen(event.text or "", data, bool(changed_screen))
                    await self._send_json(payload)

                elif event.kind == "error":
                    # A rejected session is safety-critical: preserve that
                    # warning, but never echo the provider's raw error body
                    # (which may contain request contents or source text).
                    rejected = "REJECTED" in (event.text or "")
                    await self._send_json({
                        "type": "error", "code": "upstream",
                        "message": (
                            "Session setup REJECTED; guardrails unavailable. Stop this customer session."
                            if rejected else "การเชื่อมต่อผู้ช่วยขัดข้องชั่วคราว"
                        ),
                    })
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("provider->browser pump failed")

    # ---- turn_idle vs tool calls (TURN_IDLE_TRACKS_TOOLS) ----
    #
    # Off by default, and then none of this runs: the sales room's announce
    # and nudge timing is exactly what it was. On, a tool call in flight keeps
    # `turn_idle` clear, tracked BY CALL ID with its start time — never a
    # counter. gemini.py yields no tool_result for a cancelled id or after a
    # stale session, so a counter stuck above zero would have parked every
    # announcement and nudge for good: CLAUDE.md's "stop nudging, nothing
    # wakes" bug (safety review #8). Every way out is covered: the result,
    # `tool_dropped`, `resumed`, a reconnect, the session's end, and a
    # ceiling per entry.

    def _tool_started(self, event) -> None:
        if not settings.turn_idle_tracks_tools:
            return
        call_id = getattr(event, "call_id", None) or (event.data or {}).get("call_id")
        if not isinstance(call_id, str) or not call_id:
            # A provider without call ids (OpenAI) cannot be tracked; it
            # keeps today's timing rather than a guess.
            return
        from app.tools.registry import SLOW_TOOL_TIMEOUT_S, TOOL_TIMEOUT_S, get

        entry = get(event.text or "")
        # The dispatcher's own ceiling for this tool, plus slack. A paced
        # tool (next_slide) legitimately runs past TOOL_TIMEOUT_S, and
        # discarding it early would let an announcement into its gap.
        ceiling = (SLOW_TOOL_TIMEOUT_S if entry is not None and entry.paced
                   else TOOL_TIMEOUT_S) + 2.0
        flight = self.__dict__.setdefault("_tools_in_flight", {})
        flight[call_id] = (time.monotonic(), ceiling)
        idle = getattr(self, "turn_idle", None)
        if idle is not None:
            idle.clear()
            self._idle_held_by_tools = True

    def _tool_finished(self, event, *, dropped: bool) -> None:
        if not settings.turn_idle_tracks_tools:
            return
        flight = getattr(self, "_tools_in_flight", None)
        call_id = getattr(event, "call_id", None)
        if call_id is None and dropped:
            call_id = (event.data or {}).get("call_id")
        if not flight or call_id not in flight:
            return
        del flight[call_id]
        # A result is followed by the model's reply to it, whose own
        # turn_complete sets idle. A dropped call has no reply coming, so
        # nothing else would: release here.
        if dropped and not flight:
            self._release_idle()

    def prune_tools_in_flight(self, now: float | None = None) -> None:
        """Discard entries past their ceiling. Called on every provider event
        and by `events.announce` before it waits."""
        flight = getattr(self, "_tools_in_flight", None)
        if not flight:
            return
        clock = time.monotonic() if now is None else now
        for call_id, (started, ceiling) in list(flight.items()):
            if clock - started > ceiling:
                del flight[call_id]
                logger.info("tool call %s never reported back in %.0fs — no longer "
                            "holding turn_idle for it", call_id, ceiling)
        if not flight:
            self._release_idle()

    def _clear_tools_in_flight(self, why: str) -> None:
        flight = getattr(self, "_tools_in_flight", None)
        if flight:
            logger.info("forgetting %d tool call(s) in flight (%s)", len(flight), why)
            flight.clear()
        self._release_idle()

    def _release_idle(self) -> None:
        """Set turn_idle again, but only if the tool tracking is what cleared
        it — an announcement's own pending turn is not ours to end."""
        if not getattr(self, "_idle_held_by_tools", False):
            return
        self._idle_held_by_tools = False
        idle = getattr(self, "turn_idle", None)
        if idle is not None:
            idle.set()

    async def _follow_canva(self) -> None:
        """Narrate whatever page a person puts on the Canva window.

        The robot drives the deck, but a salesperson standing at the screen
        will click it too — and the robot had no idea, so it kept talking
        about a slide nobody was looking at. Every page already has its
        script; this just makes the *screen* able to choose which one, so
        either side can lead and the other follows.

        Runs for the life of the call. Everything expensive is inside
        `poll_external`, which returns None unless the page really moved
        without us, so the common case is one cheap `location.hash` read a
        second.
        """
        from app.config import settings

        if not settings.canva_url or settings.canva_poll_s <= 0:
            return

        from app.tools import canva_display, slides

        while True:
            await asyncio.sleep(settings.canva_poll_s)
            try:
                page = await canva_display.poll_external()
                if page is None:
                    continue
                order = slides.follow_external_page(page)
                if order is None:
                    continue
                logger.info("following canva to page %d", page)
                await display.show(order["slide"])
                await self._send_json({
                    "type": "tool_result",
                    "name": "follow_canva",
                    "result": {"ok": True, "page": page},
                    "slide": order["slide"],
                    "displays": display.client_count(),
                })
                # Somebody moved the Canva window by hand, which they can do
                # at any moment — including while the robot is still talking
                # about the page they just left.
                from app import events

                await events.announce(order["text"], source="follow_canva")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("could not follow the canva window")

    async def _resume_after_silence(self) -> None:
        """Pick the tour back up when a slide's question goes unanswered.

        Every other route into `_nudge_tour_if_stalled` hangs off
        turn_complete, and that is exactly the event a waiting robot will
        never produce again: it asked its question, the turn ended *during*
        the grace period while `should_continue_tour()` was deliberately
        False, and then nothing happens. No speech, no tool call, no turn —
        nothing left to trigger on. The tour would stand there indefinitely
        in front of a guest who simply didn't feel like answering.

        So the expiry needs a clock of its own. One cheap comparison a second
        against a field that is almost always None.
        """
        from app.tools import slides

        try:
            while True:
                await asyncio.sleep(1.0)
                if not slides.STATE["awaiting"]:
                    continue
                # awaiting_answer() clears the flag itself once the grace has
                # run out, so this both tests and expires it.
                #
                # Strictly redundant today: should_continue_tour() below calls
                # awaiting_answer() too, so deleting this line changes no
                # behaviour, and a revert test proved as much. Kept anyway,
                # because the alternative is that this loop's correctness
                # depends on a detail inside a function whose job is a
                # different question ("does the tour owe a slide?"). Someone
                # optimising that guard out of should_continue_tour would
                # silently turn this watcher into a thing that interrupts
                # guests mid-answer.
                if slides.awaiting_answer():
                    continue
                if not slides.should_continue_tour():
                    continue
                logger.info("question went unanswered — resuming the tour")
                from app import events

                # The queue is usually empty by now — the grace period has
                # just run out — but "usually" is what the other three
                # callers assumed too, and it is free to be sure.
                await events.announce(
                    slides.CONTINUE_NUDGE,
                    source="tour_resume",
                    still_relevant=slides.should_continue_tour,
                )
        except asyncio.CancelledError:
            raise

    #: How long past the guest's last words her voice may hold the idle
    #: clock (`_close_when_nobody_is_there`): longer than any one answer
    #: (stories run 20-35 s), short of a tour narrated to an empty room.
    ANSWER_HOLDS_IDLE_S = 90.0

    async def _close_when_nobody_is_there(self) -> None:
        """Hang up on an empty room.

        A gallery session ends when a guest walks away, which produces no
        event at all: the socket stays open, the Live API connection stays
        billed, and the browser goes on holding a fullscreen window. On a
        free tier that is quota; on a paid one it is money, at $0.005 a
        minute of audio in, for a room with nobody in it.

        "Nobody is there" is the absence of *guest* speech. Deliberately not
        the absence of all activity — the robot narrating a 65-page deck to
        an empty room is the exact case this exists to end, and it is busy
        the whole time.

        The timer resets on `heard`, so a guest who listens quietly through
        a long slide and then speaks is fine; the window is minutes, not
        seconds. Off by default, because a demo that hangs up mid-sentence
        because somebody set it to thirty seconds is worse than the bill.
        """
        try:
            while True:
                await asyncio.sleep(5.0)
                limit = settings.idle_timeout_s
                if self.summoned and not getattr(self, "_greeting_turn_seen", False):
                    if settings.standing_line and not self._ever_heard:
                        # The standing line (owner 2026-09-30): a virgin
                        # summoned session IS the held-open line the next
                        # face greeting lands on in under a second, and it
                        # costs ~nothing while idle — local VAD never
                        # streams silence. It stops being virgin the moment
                        # it is greeted or hears anybody, and then every
                        # ordinary rule below ends it: one visitor's words
                        # must not still be in the model's context when the
                        # next visitor walks up. app/standing.py rings a
                        # fresh line after that.
                        continue
                    # Rung by the camera and never greeted: the face that
                    # rang did not confirm (a colleague in cooldown whose
                    # score dipped for two frames). Nothing to say and
                    # nobody talking is not a conversation worth two
                    # minutes of an open Gemini line.
                    limit = min(limit, self.SUMMONED_ABANDON_S) if limit else self.SUMMONED_ABANDON_S
                why = None
                from app import focus as _focus

                if _focus.enabled() and self.summoned:
                    # Listening focus: nobody has faced the robot for a
                    # while and nobody has been heard either — the visitor
                    # left. Placed after the standing-line hold above, so a
                    # virgin held-open line is never ended by it.
                    #
                    # Camera-opened calls only, the same line as focus rule
                    # 2 (begin_session): a call somebody started with the
                    # button is heard from outside the lens (owner, 9 Oct),
                    # and ending it 30 s after their last words — which is
                    # mid-answer when Emma's reply runs longer — is ignoring
                    # them the other way. Their call keeps IDLE_TIMEOUT_S.
                    from app import focus

                    away = focus.engaged_age()
                    if away is not None and away >= self.FOCUS_AWAY_S:
                        limit = min(limit, self.FOCUS_AWAY_S) if limit else self.FOCUS_AWAY_S
                        why = "no_face"
                if not limit:
                    continue
                now = time.monotonic()
                # Her voice holds the clock: a guest listening to a long
                # answer is not an empty room. 13 Oct, live on the robot: a
                # tapped facilities question, her 31 s answer with its story,
                # hung up "no_face" at 30 s mid-sentence (the guest stood under
                # the head camera), story and all. Held only so far past the
                # guest's last words, so narration to nobody still ends (the
                # deck case in the docstring).
                held = min(self._array_audible_until(now),
                           self._last_heard_at + self.ANSWER_HOLDS_IDLE_S)
                quiet = now - max(self._last_heard_at, getattr(self, "_idle_from", 0.0), held)
                if quiet < limit:
                    continue
                logger.info("no guest speech for %.0fs%s — ending the session", quiet,
                            " and nobody in front of the camera" if why else "")
                turnlog.record("idle_timeout", quiet_s=round(quiet), why=why)
                await self._send_json({"type": "idle_timeout",
                                       "quiet_s": round(quiet)})
                from app.tools import slides
                try:
                    await slides.shutdown_display()
                except Exception:
                    logger.exception("could not close the display on idle")
                return
        except asyncio.CancelledError:
            raise

    # ---- follow-up after silence (FOLLOW_UP_AFTER_S, owner 12 Oct) ----
    #: How long the watcher may wait in all (her answer playing, a tool
    #: running, then the silence itself) before it gives the answer up.
    FOLLOW_UP_MAX_WAIT_S = 90.0
    #: With a live camera, nobody engaged in front for this long = nobody to ask.
    FOLLOW_UP_FACE_GONE_S = 3.0

    def _guest_started_turn(self) -> None:
        """The guest spoke (or tapped a topic): whatever follow-up was
        pending is void -- they are talking; the next answer gets its own."""
        task = getattr(self, "_follow_up_task", None)
        if task is not None and not task.done() and getattr(self, "_answer_pending", False):
            turnlog.record("follow_up", sent=False, why="guest_spoke")
        self._follow_up_gen = getattr(self, "_follow_up_gen", 0) + 1
        self._answer_pending = True
        self._said_since_guest = ""
        self._guest_text = ""
        self._said_this_turn = ""
        self._choices_offered = None     # the page drops its buttons on the same events
        # Each answer owns the screen (voice-preview.html answerOwed/answerStaged):
        # mirrored here so a story's taps live exactly as long as its overview.
        self._screen_owed = True
        self._screen_staged = False
        self._story_owns_answer = False

    def _void_follow_up(self) -> None:
        """Something else spoke for her (an announcement): the pending answer
        is not hers to follow up any more, and that turn must not arm one
        (review 12 Oct: robot_arrived / follow_canva / an invite completing
        while an answer was pending re-armed it 4 s after her own news)."""
        self._answer_pending = False
        self._follow_up_gen = getattr(self, "_follow_up_gen", 0) + 1

    def _maybe_schedule_follow_up(self) -> None:
        """At a turn_complete that carried her answer to the guest: (re)start
        the silence watcher for it. Re-armed by a later turn_complete of the
        same answer (gemini-3.8 completes a turn before its tool call, then
        again after the tool's reply), and never armed by her own follow-up,
        greeting or announcements -- only a guest turn sets `_answer_pending`."""
        if settings.follow_up_after_s <= 0 or getattr(self, "profile", "condo") != "condo":
            return
        if not (getattr(self, "_answer_pending", False) and self._spoke_this_turn):
            return
        if getattr(getattr(self, "provider", None), "_vad_gate", None) is None:
            # VAD_MODE=gemini: no local gate, so neither speech_started nor
            # `_guest_speaking` can see the guest -- it would talk over them.
            if not getattr(self, "_follow_up_blind_logged", False):
                self._follow_up_blind_logged = True
                logger.info("follow-up after silence: off for this call -- needs VAD_MODE=local")
            return
        self._follow_up_gen = getattr(self, "_follow_up_gen", 0) + 1
        old = getattr(self, "_follow_up_task", None)
        if old is not None and not old.done():
            old.cancel()
        self._follow_up_task = asyncio.create_task(self._follow_up_after_silence(self._follow_up_gen))

    def _follow_up_veto(self, now: float) -> str | None:
        """Why this silence must not be followed up, or None."""
        from app import focus
        from app.tools import slides

        if _ENDS_ON_QUESTION.search((getattr(self, "_said_since_guest", "") or "").strip()):
            return "she_asked"
        if _guest_closed(getattr(self, "_guest_text", "") or ""):
            return "guest_closed"
        if slides.STATE.get("deck") or slides.STATE.get("awaiting"):
            return "tour"
        if focus._robot_moving():
            return "robot_moving"
        if focus.enabled():
            away = focus.engaged_age(now)
            if away is not None and away >= self.FOLLOW_UP_FACE_GONE_S:
                return "nobody_in_front"     # the goodbye is the walk-away's answer
        return None

    async def _follow_up_after_silence(self, gen: int) -> None:
        """Owner 12 Oct: answer completely; if then nothing is heard for
        FOLLOW_UP_AFTER_S, ask one short follow-up; if a voice is heard,
        listen and answer instead -- no follow-up for that answer at all.

        The clock starts when her voice has PLAYED OUT (`_she_is_audible`),
        not at turn_complete: the model generates 4-6x faster than she speaks.
        A tool still running or an answer still owed is not silence. Any
        voice, even one focus refused (`_guest_speaking`), cancels: the guest
        talking to a friend beside her is not a pause to fill."""
        from app import events

        wait_s = float(settings.follow_up_after_s)
        start = time.monotonic()
        quiet_since: float | None = None
        while True:
            await asyncio.sleep(0.2)
            if gen != getattr(self, "_follow_up_gen", 0):
                return
            now = time.monotonic()
            if now - start > self.FOLLOW_UP_MAX_WAIT_S:
                return
            if self._guest_speaking():
                self._answer_pending = False
                turnlog.record("follow_up", sent=False, why="voice_heard")
                return
            if (getattr(self, "_tools_in_flight", None) or self._she_is_audible(now)
                    or self._reply_owed(now)):
                quiet_since = None
                continue
            if quiet_since is None:
                quiet_since = now
                continue
            if now - quiet_since >= wait_s:
                break
        why = self._follow_up_veto(now)
        if why is None and _active is not self:
            why = "superseded"          # a newer call owns the robot now
        self._answer_pending = False
        if why:
            turnlog.record("follow_up", sent=False, why=why)
            return

        def still_quiet() -> bool:
            return (gen == getattr(self, "_follow_up_gen", 0) and _active is self
                    and not self._guest_speaking()
                    and not self._she_is_audible(time.monotonic()))

        sent = await events.announce(FOLLOW_UP_TEXT, source="follow_up", still_relevant=still_quiet)
        turnlog.record("follow_up", sent=bool(sent), after_s=round(wait_s, 1))

    async def _respeak_silent_block(self) -> None:
        """Ask the model to say out loud what it only wrote.

        Measured 2026-08-26, four rap deliveries: the model formats a verse
        as a quoted multi-line block, the block reaches output_transcription
        as one atomic chunk, and no audio comes with it — the guest watches
        text appear in silence. The same words as flowing speech carry full
        audio every time (six probe sessions, profanity included, Emma's
        own prompt included). A prompt rule alone did not hold: with the
        rule live the very next session block-formatted again — so the net
        is mechanical. Goes through events.announce because this is the
        robot speaking without being spoken to, and announce is the one
        pipe that waits for the ear, re-checks relevance, and cannot talk
        over another announcement."""
        from app import events

        await events.announce(
            "ระบบแจ้ง: ท่อนที่จัดรูปแบบเป็นบรรทัดเมื่อกี้ไม่มีเสียงออกลำโพง "
            "ผู้ใช้ไม่ได้ยินเลย ให้พูดเนื้อหาเดิมทั้งหมดอีกครั้งเป็นประโยคพูดต่อเนื่องปกติ "
            "ห้ามขึ้นบรรทัดใหม่ ห้ามใส่ชื่อท่อนในวงเล็บ และห้ามเอ่ยถึงข้อความแจ้งนี้",
            source="respeak_silent_block",
        )

    async def _nudge_tour_if_stalled(self) -> None:
        """Keep a slide tour moving when the model narrates and then stops.

        The tour is meant to advance itself: narrate a slide, call next_slide,
        repeat. The live model does not do this reliably — it narrates the
        cover, ends its turn, and waits, so the deck never leaves slide one.
        KEEP_GOING can't reach it there because that instruction rides on a
        tool *result*, and with no tool call there is no result to carry it.

        turn_complete is the one moment we know the model has stopped. If a
        tour still owes a slide, push next_slide as its own turn. In Gemini
        Live a real next_slide arrives as a tool call *before* turn_complete,
        so seeing turn_complete with the deck still behind means the model
        genuinely stalled — no race with an advance already in flight.

        Only for a model that *narrated* and then stopped. That qualifier is
        the whole safety of this function, and leaving it out turned the nudge
        into a machine for skipping the presentation:

            nudging model to advance the tour from slide 19
            next_slide -> ew-020 (20/59): waited 0.8s for audio
            nudging model to advance the tour from slide 20
            next_slide -> ew-021 (21/59): waited 0.8s for audio
            ... 26 slides, ~1s each

        CONTINUE_NUDGE ends "อย่าพูดอย่างอื่น" — say nothing else. The model
        obeyed: it called next_slide, said nothing, and completed the turn.
        That empty turn looked exactly like a stall, so we nudged again, and
        the deck walked itself to slide 45 in silence. (0.8s is SLIDE_PAUSE_S
        alone — the audio queue was empty every time, which is the log saying
        outright that nothing was ever spoken.)

        MAX_TOUR_NUDGES did not save it: the cap only counts repeats at the
        *same* index, and each nudge moved the index, so the counter reset
        every round and the limit was never once reached. A bound that the
        thing it's bounding can reset is not a bound.

        So: no speech this turn, no nudge. A model that has gone quiet without
        narrating is not stalled mid-tour, and pushing it harder is what
        caused the damage. KEEP_GOING rides on the next tool result and is the
        right instrument there.

        Bounded three ways now: the model must have spoken, the position has
        to move for the nudge to repeat freely, and MAX_TOUR_NUDGES caps how
        long we lean on a model that keeps ignoring it.
        """
        from app.tools import slides

        if not slides.should_continue_tour():
            self._nudge_index = None
            self._nudge_count = 0
            return

        if not self._spoke_this_turn:
            logger.info(
                "not nudging the tour: the model said nothing this turn, so it "
                "is not a model that narrated and stopped"
            )
            return

        index = slides.STATE["index"]
        if index == self._nudge_index:
            self._nudge_count += 1
        else:
            self._nudge_index = index
            self._nudge_count = 1

        if self._nudge_count > slides.MAX_TOUR_NUDGES:
            logger.info(
                "tour stalled at slide %d — nudged %d times, giving up",
                index + 1, self._nudge_count - 1,
            )
            return

        # Wait for the guest to finish hearing the slide before pushing.
        #
        # `turn_complete` means the model stopped *generating*, not that anyone
        # has heard it — it generates several times faster than it speaks, so
        # at this moment there can still be twenty seconds of narration sitting
        # in the browser's queue. The nudge goes in as a user turn, and a user
        # turn during playback is a barge-in: Gemini cancels its own generation
        # and drops the audio that hadn't been sent yet.
        #
        # So the mechanism built to keep the tour moving was cutting the tour
        # off. Measured over one real session: of 81 slides with a script
        # longer than 40 characters, 21 delivered under 60% of it, some as
        # little as 16% — the guest heard one sentence of six and the deck
        # moved on. Every "waited 0.8s for audio" in the server log after a
        # nudge is the same event: the queue was empty because the nudge had
        # just emptied it.
        #
        # This is the audio-lead rule again, one path further out than it was
        # ever applied: anything that changes what the guest *experiences* has
        # to be timed against their ear, not the model's clock. It was applied
        # to the picture and not to this.
        if self._nudge_task is not None and not self._nudge_task.done():
            return          # one pending nudge is enough
        self._nudge_task = asyncio.create_task(self._nudge_once_heard(index))

    async def _nudge_once_heard(self, index: int) -> None:
        """Push the tour on, but only after the guest has caught up.

        The waiting, the re-check afterwards and the delivery now live in
        `events.announce`. That sequence is the same for every event that
        makes the robot speak unprompted, and this was the only one of four
        callers that had all three parts. What stays here is the piece only
        the tour can answer: whether nudging is still the right thing by the
        time the guest has finished listening.
        """
        from app import events
        from app.tools import slides

        def still_worth_nudging() -> bool:
            # The model usually calls next_slide on its own, and nudging a
            # tour that already moved would skip a slide nobody heard.
            if not slides.should_continue_tour() or slides.STATE["index"] != index:
                return False
            # A question is open. That silence belongs to the guest.
            return not slides.awaiting_answer()

        await events.announce(
            slides.CONTINUE_NUDGE,
            source="tour_nudge",
            still_relevant=still_worth_nudging,
        )

    #: Two taps closer than this are one finger, not two questions.
    QUICK_ASK_MIN_GAP_S = 1.2

    def _note_screen(self, name: str, data: object, changed_screen: bool) -> None:
        """What a tool result put on the robot face's stage, as the page will
        see it (voice-preview.html tool_result): a story brings its taps; any
        other picture replaces it -- except an answer picture (ANSWER_IMAGES)
        inside an answer a story already owns, which the page ignores."""
        if not isinstance(data, dict) or not getattr(self, "_robot_face_claim", False):
            return
        if name == "present_story":
            if data.get("ok") and data.get("screen") == "story":
                from app import stories

                self._story_topic = data.get("topic")
                self._story_taps = stories.tap_ids(data)
                self._story_owns_answer = True
                self._screen_staged = True
            return
        if changed_screen and getattr(self, "_story_owns_answer", False):
            return                       # the story wins this answer's screen
        # Only what the robot face actually draws over a story: the unit card,
        # a floor plan, an embed, a clear (the text tables are not ported there).
        if changed_screen or data.get("embed") or data.get("screen") in ("unit", "plan", "clear"):
            self._screen_staged = True
            self._story_taps = set()

    async def _offer_choices(self) -> None:
        """Owner 12 Oct: "ลูกค้ากำลังมองหาห้องแบบสตูดิโอ หนึ่งห้องนอน หรือสองห้องนอนคะ
        คำพวกนี้อยากให้ขึ้นเป็นตัวเลือกบนจอด้วยให้ลูกค้ากดเข้าไปดูได้".

        Decided at every turn_complete that carried her voice, from what she
        said in that stretch (gemini-3.8 completes a turn before its tool call
        too -- a stretch with no words changes nothing). Her question gets its
        buttons; her next spoken line without one takes them down, so the
        buttons always belong to the last thing she asked. Robot face, condo,
        and only when the tool a tap leads to is visible here."""
        from app import quick_asks
        from app.prompts import _tool_visible

        said, self._said_this_turn = getattr(self, "_said_this_turn", ""), ""
        if not said.strip() or not getattr(self, "_robot_face_claim", False):
            return
        name = None
        if getattr(self, "profile", "condo") == "condo" and _ENDS_ON_QUESTION.search(said.strip()):
            name = quick_asks.choices_asked(said)
            if name and not _tool_visible(quick_asks.CHOICES[name]["tool"]):
                name = None
            if name and getattr(self, "_story_owns_answer", False) and getattr(self, "_story_taps", None):
                # The story's overview already holds this choice as taps; a
                # second row of the same buttons under it is noise.
                name = None
        if name == getattr(self, "_choices_offered", None):
            return
        self._choices_offered = name
        if name:
            turnlog.record("choices", set=name)
            await self._send_json(quick_asks.choices_for_page(name))
        else:
            await self._send_json({"type": "choices", "set": None, "items": []})

    #: A story id as the catalog writes them (app/stories.py ids).
    _STORY_ID = re.compile(r"[a-z0-9_]{1,64}")

    def _story_report(self, report) -> None:
        """The story engine's own report from the robot face (emma-story.js
        `Story.report`), one per story: per beat when it went up and where
        its cue word fell in her voice (seconds of her voice played), and the
        frame times while it played with her voice and the 3D Emma running.
        The reviewer (13 Oct) could not judge picture-to-voice timing or
        smoothness from silent screen recordings; this is that evidence from
        real conversations. Numbers and the story id only -- anything else in
        the message is dropped, and a malformed report writes nothing."""
        if not isinstance(report, dict):
            return
        sid = report.get("id")
        if not isinstance(sid, str) or not self._STORY_ID.fullmatch(sid):
            return
        beats = report.get("beats")
        if not isinstance(beats, list):
            return

        def number(value, high):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            try:
                value = float(value)      # an int past 1e308 raises: the call must not end
            except (OverflowError, ValueError):
                return None
            return round(value, 2) if math.isfinite(value) and 0 <= value <= high else None

        at_s, cue_s, lag_ms = [], [], []
        for beat in beats[:24]:
            beat = beat if isinstance(beat, dict) else {}
            at, cue = number(beat.get("at"), 600), number(beat.get("cue"), 600)
            at_s.append(at)
            cue_s.append(cue)
            lag_ms.append(None if at is None or cue is None else round((at - cue) * 1000))
        frames = number(report.get("frames"), 100000)
        long_frames = number(report.get("long_frames"), 100000)
        turnlog.record("story_report", topic=sid, beats=len(at_s), at_s=at_s, cue_s=cue_s,
                       lag_ms=lag_ms, interrupted=report.get("interrupted") is True,
                       audio_s=number(report.get("audio_s"), 600),
                       frames=None if frames is None else int(frames),
                       fps=number(report.get("fps"), 240), p95_ms=number(report.get("p95_ms"), 10000),
                       long_frames=None if long_frames is None else int(long_frames))

    async def _quick_ask(self, event: dict) -> None:
        """A topic tapped on the robot face (app/quick_asks.py): an id and a
        language code in, the server's own words out as a user turn. An id
        this profile does not have, a page that is not the robot face, or a
        second tap within QUICK_ASK_MIN_GAP_S goes nowhere."""
        if not getattr(self, "_robot_face_claim", False) or getattr(self, "provider", None) is None:
            return
        from app import quick_asks

        # The language the talk is in (owner 15 Oct: talking English, a tap
        # was answered in Thai): her own latest words, else the guest's.
        talk = (quick_asks.script_lang(getattr(self, "_said_since_guest", ""))
                or quick_asks.script_lang(getattr(self, "_guest_text", "")))
        ask_id = event.get("id")
        tap = None
        if isinstance(ask_id, str) and ask_id.startswith("story_"):
            # A tap on a story's own overview (app/stories.py): only an id of
            # the story this session put on screen last, and the words are the
            # catalog's -- the same rule as a choice under her question.
            text = None
            if ask_id in getattr(self, "_story_taps", ()):
                from app import stories

                tap = stories.find_tap(getattr(self, "_story_topic", None), ask_id)
                text = stories.tap_text(tap, event.get("lang"), talk) if tap is not None else None
            if text is None:
                return
        else:
            text = quick_asks.text_for(self.profile, ask_id, event.get("lang"), talk)
            if text is None:
                return
            choice_set = quick_asks.choice_set_of(ask_id)
            if choice_set is not None and choice_set != getattr(self, "_choices_offered", None):
                # A choice is an answer to her question -- only while that
                # question's buttons are on screen (a stale page, or a crafted
                # frame, does not get to make her call the tool).
                return
        now = time.monotonic()
        if now - getattr(self, "_quick_ask_at", -1e9) < self.QUICK_ASK_MIN_GAP_S:
            return
        self._quick_ask_at = now
        # Somebody touched the screen: the person the summoned call was
        # holding its ears for is here (the same opening barge_in makes),
        # and the idle clock counts a tap as being served.
        self._last_heard_at = now
        # ...and a tap is a guest in conversation, as speech is (review 12 Oct):
        # without this the camera greeter's `_in_conversation` still read the
        # summoned line as an ungreeted wait and announced "สวัสดีค่ะ" over the
        # answer to the topic the guest had just tapped.
        self._ever_heard = True
        if getattr(self, "_ears_closed_until", None) is not None:
            self._ears_closed_until = None
            turnlog.record("ears_open", greeted=False, why="quick_ask")
        lang = event.get("lang")
        turnlog.record("quick_ask", id=ask_id, lang=lang if lang in quick_asks.LANGS else None)
        self._guest_started_turn()        # a tapped question is a question
        if tap is not None and tap.get("more"):
            # A tapped picture (stories.tap_more_text): the page keeps the story
            # with the picture enlarged as this answer's screen (onTap keep), so
            # the story's taps live on and an answer picture does not replace it.
            self._screen_staged = True
            self._story_owns_answer = True
        await self.provider.send_text(text)

    async def _send_json(self, payload: dict) -> None:
        try:
            await self.ws.send_text(json.dumps(payload, ensure_ascii=False))
        except Exception:
            pass


#: The voice session that owns the robot right now.
#:
#: There is exactly one of everything downstream: one Canva window, one
#: screen, one set of lights, one `slides.STATE` holding one tour position.
#: That is not a limitation to be engineered away — it is a description of a
#: robot standing in a room. The bug was that nothing said so. A second
#: connection got its own VoiceSession and then quietly shared all the global
#: state with the first: two tours writing one position, two models steering
#: one Canva window, and the tell would be a deck that "randomly" jumped.
#:
#: Easy to hit by accident, too. Reload the page without the old socket
#: closing cleanly and you have two.
_active: VoiceSession | None = None


#: Segment ids handed to focus and the voice prints. Per process, not per
#: gate: a voice print still being computed when its session ends would
#: otherwise report "segment 3" into the next session, whose own segment 3
#: is somebody else — focus drops ids it never saw closed.
_SEGMENT_IDS = itertools.count(1)


class _VoiceprintWorker:
    """The one thread voice prints run on (VOICEPRINT_ENABLED).

    Off the audio path by construction: `submit` is a non-blocking put into
    a small bounded queue, and a full queue drops the segment (with a log
    row) rather than wait — a late voice print is worth less than a guest's
    audio arriving on time. Started on first use, so a machine with the
    switch off never has the thread at all.

    The session boundary goes through the same queue (`forget`): pending
    prints of the old session are discarded, and `voiceprint.reset()` runs
    on this thread, after any print already in flight — never beside it.
    Voice prints are never kept past the session (spec).
    """

    MAX_PENDING = 4

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue(maxsize=self.MAX_PENDING)
        self._thread: threading.Thread | None = None
        self._gen = 0
        self._missing = False
        self._failed_at = 0.0

    def submit(self, segment: int, t0: float, t1: float, pcm16: bytes) -> None:
        if self._missing:
            return
        self._start()
        try:
            self._q.put_nowait(("segment", self._gen, contextvars.copy_context(),
                                (segment, t0, t1, pcm16)))
        except queue.Full:
            turnlog.record("voiceprint_dropped", segment=segment)

    def forget(self) -> None:
        """Session boundary. A no-op until the thread exists."""
        if self._thread is None:
            return
        self._gen += 1
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        try:
            self._q.put_nowait(("reset", self._gen, None, None))
        except queue.Full:            # pragma: no cover - drained just above
            pass

    def _start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="voiceprint",
                                            daemon=True)
            self._thread.start()

    def _run(self) -> None:
        import importlib
        import sys

        while True:
            kind, gen, ctx, args = self._q.get()
            if kind == "reset":
                module = sys.modules.get("app.voiceprint")
                reset = getattr(module, "reset", None) if module is not None else None
                if reset is not None:
                    try:
                        reset()
                    except Exception:
                        logger.exception("voiceprint.reset failed")
                continue
            if gen != self._gen:
                continue                  # the session it belonged to has ended
            try:
                module = importlib.import_module("app.voiceprint")
            except ImportError:
                logger.warning("VOICEPRINT_ENABLED but app/voiceprint.py is not "
                               "importable — voice prints off until restart")
                self._missing = True
                continue
            try:
                ctx.run(module.segment, *args)
            except Exception:
                now = time.monotonic()
                if now - self._failed_at > 60.0:
                    self._failed_at = now
                    logger.exception("voiceprint.segment failed")


_voiceprints = _VoiceprintWorker()


def _reset_conversation_state() -> None:
    import sys

    for name in ("app.tools.slides", "app.tools.calc"):
        module = sys.modules.get(name)
        if module is not None:
            (module.reset_state if name.endswith("slides") else module.reset)()
    heard.forget()
    display.set_audio_lead(0)
    display.cancel_pending_reveal()
    # Listening focus: who spoke, the voice clusters and the main speaker
    # belong to one conversation (the room — faces in view — stays).
    from app import focus

    focus.reset()
    _voiceprints.forget()


async def handle_connection(ws: WebSocket, provider: str | None = None, voice: str | None = None,
                            profile: str | None = None, lang: str | None = None,
                            summoned: bool = False) -> None:
    """Run a voice session, taking the robot over from any previous one.

    Newest wins rather than newest rejected. A stale tab must not be able to
    lock the robot out — the person standing in front of it has no idea some
    other browser somewhere is still holding a socket open, and "the robot
    stopped working, restart the server" is a worse failure than "your old
    tab stopped working".
    """
    global _active

    # MULTI_SESSION: a shared test server has no robot to take over — many
    # browsers, no machine. Nobody supersedes anybody, and nobody becomes
    # `_active`: leaving the slot empty is what keeps `events.announce`
    # structurally unable to deliver one tester's event into another
    # tester's conversation, rather than relying on those events never
    # firing. The machine-bound tool groups are already stripped in
    # `enabled_tool_groups`, so the shared state the takeover exists to
    # clear (slides.STATE, the calc sheet) can never be written here.
    single_session = not settings.multi_session
    previous = _active if single_session else None
    if previous is not None:
        logger.info("a new voice session took over — closing the previous one")
        turnlog.record("session_takeover")
        try:
            await previous._send_json({
                "type": "error",
                "code": "superseded",
                "message": "เปิดหน้าใหม่แล้ว หน้านี้จึงหยุดทำงาน",
            })
            await previous.ws.close()
        except Exception:
            pass          # it was probably already dead; that is why we're here

        # The robot is being handed over, so the tour it was mid-way through
        # belongs to nobody. Leaving it running would have the new session
        # inherit a position from a conversation it never had.
        from app.tools import slides

        slides.reset_state()
        # The ROI sheet holds the previous guest's budget and assumptions —
        # numbers, which are more personal than slides. Same rule as the
        # transcript wipe: one visitor's figures are not the next one's
        # business. sys.modules, not an import: importing would register the
        # calc tools on machines whose TOOL_GROUPS excludes them.
        import sys as _sys

        _calc = _sys.modules.get("app.tools.calc")
        if _calc is not None:
            _calc.reset()

    if single_session:
        _reset_conversation_state()

    session = VoiceSession(ws, provider_name=provider, voice=voice, profile=profile, lang=lang,
                           summoned=summoned)
    if single_session:
        _active = session
        # Listening focus: only a camera-opened (summoned) call needs a face
        # in front; a call a person started is heard from outside the lens.
        from app import focus

        focus.begin_session(summoned)
        # The conversation this session's segments belong to — taken after
        # `_reset_conversation_state` above bumped it (see `_segment_closed`).
        session._focus_epoch = focus.epoch()
    import uuid

    log_token = turnlog.session_id.set(uuid.uuid4().hex)
    from app import plan_display

    plan_display.register(turnlog.session_id.get(),
                          lambda command: ws.send_text(json.dumps(command, ensure_ascii=False)))
    turnlog.record("session_start", provider=session.provider_name, voice=session.voice)
    try:
        await session.run()
    finally:
        # Acks this session was waiting for resolve `disconnected` now, not
        # at their timeout; its caps, nav state and issued run ids go with it.
        robot_face_link.session_closed(session)
        heard.forget(session)
        try:
            if _active is session:
                _reset_conversation_state()
                _active = None
            # A superseded visitor cannot wipe the new owner's subtitles.
            if _active is None:
                await display.clear_subtitle()
                await display.set_phase("idle")
            turnlog.record("session_end")
        finally:
            plan_display.unregister(turnlog.session_id.get())
            turnlog.session_id.reset(log_token)
