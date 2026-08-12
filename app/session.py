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

from fastapi import WebSocket, WebSocketDisconnect

from app import heard, turnlog, voices
from app.config import settings
from app.prompts import GREETING, build_instructions
from app.providers import ProviderError, default_voice_for, get_provider

logger = logging.getLogger("condo_voice.session")


class VoiceSession:
    def __init__(self, ws: WebSocket, provider_name: str | None = None, voice: str | None = None) -> None:
        self.ws = ws
        self.provider_name = provider_name or settings.provider
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
        )
        provider = get_provider(self.provider_name, self.voice, instructions, greeting=GREETING)

        try:
            async with provider:
                self.provider = provider
                await self._send_json({
                    "type": "ready",
                    "provider": self.provider_name,
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
                watch = asyncio.create_task(self._follow_canva())
                unanswered = asyncio.create_task(self._resume_after_silence())
                _done, pending = await asyncio.wait(
                    {up, down, watch, unanswered}, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

        except ProviderError as exc:
            await self._send_json({"type": "error", "code": "provider_error", "message": str(exc)})
        except Exception as exc:
            logger.exception("voice session failed")
            await self._send_json({"type": "error", "code": "internal", "message": str(exc)})
        finally:
            try:
                await self.ws.close()
            except Exception:
                pass

    # ---- browser -> provider ----

    async def _browser_to_provider(self) -> None:
        try:
            while True:
                message = await self.ws.receive()
                if message.get("type") == "websocket.disconnect":
                    return

                if (data := message.get("bytes")) is not None:
                    await self.provider.send_audio(data)
                    continue

                if (text := message.get("text")) is not None:
                    try:
                        event = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") == "stop":
                        return
                    if event.get("type") == "audio_lead":
                        # How far the browser's audio queue runs ahead of the
                        # guest's ear. The other screens wait that long before
                        # switching, or they show the next slide while the
                        # robot is still talking about the last one.
                        from app import display

                        display.set_audio_lead(event.get("ms", 0))
                        continue
                    if event.get("type") == "robot_ready":
                        # The Android app on the robot identifying itself, and
                        # reporting which POIs its own map actually contains.
                        # The robot is the authority on that — the points were
                        # made by walking it there once — so this is the only
                        # place `KNOWN_PLACES` comes from. A browser never
                        # sends this, which is what keeps `available()` from
                        # meaning "some socket is open".
                        from app.tools import robot_link

                        robot_link.app_connected(event.get("places") or [])
                        turnlog.record("robot_ready",
                                       places=len(robot_link.KNOWN_PLACES))
                        continue
                    if event.get("type") == "robot_arrived":
                        # Sent when the SDK's navigation callback fires, long
                        # after the tool call that started the walk returned.
                        from app.tools import robot_link

                        place = event.get("place")
                        ok = bool(event.get("ok", True))
                        turnlog.record("robot_arrived", place=place, ok=ok)
                        await robot_link.arrived(place, ok)
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
                if event.kind == "audio" and event.audio:
                    self._sent_audio_ms += (
                        len(event.audio) / 2 / self.provider.output_sample_rate * 1000
                    )
                    await self.ws.send_bytes(event.audio)

                elif event.kind == "speech_started":
                    # Close the question the moment a voice starts, not when
                    # the transcript lands. A guest thinking out loud through
                    # "เอ่อ... เคยมาค่ะ ตอนเด็กๆ" takes several seconds, and
                    # the grace timer must not expire and start the next slide
                    # over the top of them mid-sentence.
                    from app.tools import slides

                    slides.answer_received()
                    await self._send_json({"type": "speech_started"})

                elif event.kind == "interrupted":
                    self._sent_audio_ms = 0.0
                    self._spoke_this_turn = False
                    # A barge-in mid-tour is a pause, not a skip: keep the deck
                    # on the current slide so the advance nudge resumes it
                    # rather than stepping over a slide nobody finished hearing.
                    from app.tools import slides

                    slides.pause_for_barge_in()
                    await self._send_json({"type": "interrupted"})

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
                    self._sent_audio_ms = 0.0
                    await self._send_json({"type": "turn_complete"})
                    await self._nudge_tour_if_stalled()
                    self._spoke_this_turn = False

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
                    await self._send_json({"type": "user_transcript", "text": event.text or ""})

                elif event.kind == "assistant_transcript":
                    if (event.text or "").strip():
                        self._spoke_this_turn = True
                        turnlog.record("said", text=event.text or "")
                    await self._send_json({"type": "assistant_transcript", "text": event.text or ""})

                elif event.kind == "tool_call":
                    turnlog.record("tool", name=event.text or "")
                    await self._send_json({"type": "tool_call", "name": event.text or ""})

                elif event.kind == "tool_result":
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
                        from app import display
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
                from app import display

                await display.show(order["slide"])
                await self._send_json({
                    "type": "tool_result",
                    "name": "follow_canva",
                    "result": {"ok": True, "page": page},
                    "slide": order["slide"],
                    "displays": display.client_count(),
                })
                await self.provider.send_text(order["text"])
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
                try:
                    await self.provider.send_text(slides.CONTINUE_NUDGE)
                except Exception:
                    logger.exception("could not resume the tour after silence")
        except asyncio.CancelledError:
            raise

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
        """Push the tour on, but only after the guest has caught up."""
        from app import display
        from app.tools import slides

        waited = await display.wait_until_heard(max_wait=45.0, then_pause=0.4)

        # It may have sorted itself out while we waited — the model usually
        # calls next_slide on its own, and nudging a tour that already moved
        # would skip a slide nobody heard.
        if not slides.should_continue_tour() or slides.STATE["index"] != index:
            logger.info("tour moved on by itself after %.1fs — no nudge needed", waited)
            return
        if slides.awaiting_answer():
            return          # a question is open; that silence is the guest's

        logger.info(
            "nudging model to advance the tour from slide %d (waited %.1fs for audio)",
            index + 1, waited,
        )
        try:
            await self.provider.send_text(slides.CONTINUE_NUDGE)
        except Exception:
            logger.exception("could not nudge the tour forward")

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


async def handle_connection(ws: WebSocket, provider: str | None = None, voice: str | None = None) -> None:
    """Run a voice session, taking the robot over from any previous one.

    Newest wins rather than newest rejected. A stale tab must not be able to
    lock the robot out — the person standing in front of it has no idea some
    other browser somewhere is still holding a socket open, and "the robot
    stopped working, restart the server" is a worse failure than "your old
    tab stopped working".
    """
    global _active

    previous = _active
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

    session = VoiceSession(ws, provider_name=provider, voice=voice)
    _active = session
    turnlog.record("session_start", provider=session.provider_name, voice=session.voice)
    try:
        await session.run()
    finally:
        if _active is session:
            _active = None
        turnlog.record("session_end")
