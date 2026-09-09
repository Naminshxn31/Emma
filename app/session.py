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
import json
import logging
import time

from fastapi import WebSocket, WebSocketDisconnect

from app import display, heard, turnlog, voices
from app.config import settings
from app.prompts import build_instructions, greeting_for
from app.providers import ProviderError, default_voice_for, get_provider
from app.robot_backend import active as simulation_backend

logger = logging.getLogger("condo_voice.session")


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
            time.monotonic() + self.SUMMONED_HOLD_MAX_S if summoned else None
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
        #: Per-connection persona. The URL can ask for one (?profile=
        #: translator) so the sales room flips into interpreter mode with a
        #: bookmark instead of an .env edit and a restart. Falls back to the
        #: machine's configured profile, and unknown names land on condo
        #: inside build_instructions — same safety as everywhere else.
        self.profile = (profile or settings.assistant_profile).strip().lower()
        # A public mode URL cannot unlock the machine owner's private persona.
        if self.profile not in {"condo", "emma", "translator"}:
            self.profile = "condo"
        if (self.profile == "emma" and settings.assistant_profile != "emma"
                and simulation_backend.get() is None):
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
        #: A transcript chunk shaped like a formatted verse block arrived —
        #: many newlines, one atomic chunk — which is the signature of text
        #: Gemini wrote but never voiced. See `_respeak_silent_block`.
        self._silent_block_seen = False
        self._respeak_fired = False

    @property
    def metrics(self):
        # Lazy also supports protocol tests that construct sessions via __new__.
        if not hasattr(self, "_metrics"):
            from app.metrics import SessionMetrics

            self._metrics = SessionMetrics(getattr(self, "provider_name", "unknown"))
        return self._metrics

    async def run(self) -> None:
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
        )
        provider = get_provider(
            self.provider_name, self.voice, instructions,
            greeting=None if self.summoned else greeting_for(self.profile),
            # Toolless by config for the translator, not by prompt: a
            # declared tool is a callable tool whatever the prompt says.
            use_tools=(self.profile != "translator"),
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
                if self.summoned:
                    # The machine opened this session to talk to somebody
                    # at a distance; the near-field floor would filter out
                    # exactly that person's reply. See VadGate.stand_down.
                    provider.stand_down_floor()
                turnlog.record("voice_ready")
                await self._send_json({
                    "type": "ready",
                    "diagnostic_session_id": turnlog.session_id.get() if simulation_backend.get() is not None else None,
                    "provider": self.provider_name,
                    # Which persona this session actually opened with — shown
                    # in the header, because "ไม่เห็นแปลภาษาเลย" turned out to
                    # mean a translator URL served by a pre-translator server,
                    # and nothing on screen said which mode was really running.
                    "profile": self.profile,
                    "robot_simulator": simulation_backend.get() is not None,
                    "voice": self.voice,
                    "input_rate": provider.input_sample_rate,
                    "output_rate": provider.output_sample_rate,
                    "session_limit_min": provider.session_limit_minutes,
                    "auto_resumes": provider.auto_resumes,
                    # The browser knows exactly when playback is happening,
                    # so it gates the mic rather than the server guessing.
                    "half_duplex": settings.half_duplex,
                })

                up = asyncio.create_task(self._browser_to_provider())
                down = asyncio.create_task(self._provider_to_browser())
                unanswered = asyncio.create_task(self._resume_after_silence())
                jobs = {up, down, unanswered}
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
                    if simulation_backend.get() is None:
                        jobs.add(asyncio.create_task(self._follow_canva()))
                if backend := simulation_backend.get():
                    from app.robot_voice import watch_robot

                    jobs.add(asyncio.create_task(watch_robot(self, backend)))
                if settings.idle_timeout_s or self.summoned:
                    jobs.add(asyncio.create_task(self._close_when_nobody_is_there()))
                _done, pending = await asyncio.wait(
                    jobs, return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

        except ProviderError as exc:
            from app.robot_diagnostics import failure_kind
            turnlog.record("provider_error", category=failure_kind(exc))
            await self._send_json({"type": "error", "code": "provider_error", "message": str(exc)})
        except Exception as exc:
            logger.exception("voice session failed")
            await self._send_json({"type": "error", "code": "internal", "message": str(exc)})
        finally:
            self.metrics.finish("session_closed")
            active.reset(metrics_token)
            for name in ("_nudge_task", "_respeak_task"):
                task = getattr(self, name, None)
                if task is not None and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
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
        if (getattr(self, "_ears_closed_until", None) is None
                or getattr(self, "_greeting_turn_seen", False)):
            return
        self._greeting_turn_seen = True
        self._ears_closed_until = min(self._ears_closed_until,
                                      time.monotonic() + display.remaining_lead())

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
                    self._mic_report(data)
                    await self.provider.send_audio(data)
                    continue

                if (text := message.get("text")) is not None:
                    try:
                        event = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                        continue
                    if (simulation_backend.get() is not None
                            and event.get("type") in {"robot_ready", "robot_arrived"}):
                        # Only the simulator engine can report its navigation.
                        # Even a valid hardware token cannot attach a real robot.
                        continue
                    if event.get("type") == "stop":
                        return
                    if event.get("type") == "client_metric":
                        self.metrics.client(event)
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
                        # played before the guest cut in. Only OpenAI needs it.
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
                if event.kind == "speech_stopped":
                    self.metrics.speech_end(event.text or "provider_vad_event")
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
                    await self.ws.send_bytes(event.audio)

                elif event.kind == "speech_started":
                    self.metrics.finish("new_speech")
                    self.metrics.pending_end = None
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
                    self._spoke_this_turn = False
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
                    self._sent_audio_ms = 0.0
                    display.end_turn()
                    self._greeting_turn_done()
                    idle = getattr(self, "turn_idle", None)
                    if idle is not None:
                        idle.set()
                    await self._send_json({"type": "turn_complete"})
                    await self._nudge_tour_if_stalled()
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
                    # What the robot *heard*, which is the field that has
                    # explained the most confusing bugs: "ao rummy" for Thai,
                    # "ไอ้บ้า" for "ice bath". Without it, a wrong answer is
                    # indistinguishable from a wrong question.
                    turnlog.record("heard", text=event.text or "")
                    # Kept so the destructive slide tools can check whether
                    # anybody asked for what they are about to do. See
                    # `app/heard.py` — this is the field that decides.
                    heard.record(event.text or "")
                    # Somebody is in the room. Only guest speech resets the
                    # idle clock — the robot narrating to nobody is the case
                    # the timer exists to end, and it is busy throughout.
                    self._last_heard_at = time.monotonic()
                    self._respeak_fired = False
                    await self._send_json({"type": "user_transcript", "text": event.text or ""})

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
                    await self._send_json({"type": "assistant_transcript", "text": event.text or ""})
                    # ...and to the robot's own screen, which unlike this tab
                    # is being read by the guest — so it goes through the
                    # audio clock instead of straight out. See app/display.py.
                    await display.say(event.text or "")
                    await display.set_phase("speaking")

                elif event.kind == "tool_call":
                    turnlog.record("tool", name=event.text or "")
                    await self._send_json({"type": "tool_call", "name": event.text or ""})

                elif event.kind == "tool_result":
                    if backend := simulation_backend.get():
                        backend._event("tool_result", tool=event.text or "", result=event.data or {})
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
                    await self._send_json(payload)

                elif event.kind == "error":
                    await self._send_json({
                        "type": "error", "code": "upstream", "message": event.text or "",
                    })
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("provider->browser pump failed")

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
                    # Rung by the camera and never greeted: the face that
                    # rang did not confirm (a colleague in cooldown whose
                    # score dipped for two frames). Nothing to say and
                    # nobody talking is not a conversation worth two
                    # minutes of an open Gemini line.
                    limit = min(limit, self.SUMMONED_ABANDON_S) if limit else self.SUMMONED_ABANDON_S
                if not limit:
                    continue
                quiet = time.monotonic() - self._last_heard_at
                if quiet < limit:
                    continue
                logger.info("no guest speech for %.0fs — ending the session", quiet)
                turnlog.record("idle_timeout", quiet_s=round(quiet))
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
        if simulation_backend.get() is not None:
            return
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


def _reset_conversation_state() -> None:
    import sys

    for name in ("app.tools.slides", "app.tools.calc"):
        module = sys.modules.get(name)
        if module is not None:
            (module.reset_state if name.endswith("slides") else module.reset)()
    heard.forget()
    display.set_audio_lead(0)
    display.cancel_pending_reveal()


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
    single_session = not settings.multi_session or simulation_backend.get() is not None
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
    import uuid

    log_token = turnlog.session_id.set(uuid.uuid4().hex)
    turnlog.record("session_start", provider=session.provider_name, voice=session.voice)
    try:
        await session.run()
    finally:
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
            turnlog.session_id.reset(log_token)
