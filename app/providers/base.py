"""
Common interface for speech-to-speech providers.

The browser talks one normalized protocol to our server; each provider
translates it to whatever its own API wants. That matters because the two
providers genuinely differ in ways the UI would otherwise have to care about:

|                  | Gemini Live            | OpenAI Realtime          |
| ---------------- | ---------------------- | ------------------------ |
| Input audio      | PCM16 **16 kHz**       | PCM16 **24 kHz**         |
| Output audio     | PCM16 24 kHz           | PCM16 24 kHz             |
| Transport        | `google-genai` SDK     | raw WebSocket            |
| Interruption     | server sets `interrupted`; client just stops | client must also send `conversation.item.truncate` with how much was heard |
| Session cap      | 15 min (audio-only)    | 60 min                   |
| Cost             | free tier available    | paid per audio minute    |

Normalized protocol (see `app/session.py` for the server side and
`client/index.html` for the browser side):

    browser -> server
        binary frames : PCM16 mono @ `input_sample_rate`
        {"type": "stop"}

    server -> browser
        {"type": "ready", "input_rate": …, "output_rate": …,
         "provider": …, "voice": …, "session_limit_min": …}
        {"type": "user_transcript",      "text": …, "utterance_id": …}
        # utterance_id is optional; Gemini uses it to group input deltas
        # independently of output playback and local speech-start signals.
        {"type": "assistant_transcript", "text": …}
        {"type": "interrupted"}     stop playback immediately
        {"type": "turn_complete"}
        {"type": "error", "message": …}
        binary frames : PCM16 mono @ `output_rate` (reply audio)
"""
from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class ProviderEvent:
    """One normalized event on its way to the browser."""

    kind: Literal[
        "audio", "user_transcript", "assistant_transcript",
        # The guest began speaking. Distinct from "interrupted": whether it
        # counts as barge-in depends on whether a reply is currently playing,
        # and only the browser knows that.
        "speech_started", "speech_stopped", "usage",
        "interrupted", "turn_complete", "error",
        # The provider rejoined its own session past the duration cap. Nothing
        # for the browser, but the *server* needs it: the model comes back
        # with no reason to say anything, so a tour that was mid-flight has
        # nothing left to push it. See `_nudge_tour_if_stalled`.
        "resumed",
        # The model invoked a tool (lights, AC, …). Surfaced so the UI can
        # show that something physical happened — a guest who hears "ปิดไฟ
        # ให้แล้วค่ะ" while the room stays lit needs to see whether the
        # command actually reached the hardware.
        "tool_call", "tool_result",
        # A tool call that was announced but will never get a result (its
        # connection retired, or it was cancelled), and a silent reconnect
        # with no go_away. Both exist for the turn-idle tracker
        # (TURN_IDLE_TRACKS_TOOLS): without them an in-flight entry would
        # outlive its call and every announcement would wait out TURN_WAIT_S.
        # Emitted only while that switch is on.
        "tool_dropped", "reconnected",
        # A story's picture for a line the SERVER says (a ceremony cue pressed
        # with a picture, app/event_cues.py `show`): `data` = {topic, cue, by}.
        # The provider puts it in its stream right before the line's first
        # word; the session draws it as a present_story result (session.py
        # `_story_picture`). Nothing of it goes to the model.
        "story_picture",
    ]
    audio: bytes | None = None
    text: str | None = None
    data: dict | None = None
    #: Gemini's function-call id (or a local one) on tool_call / tool_result /
    #: tool_dropped. Not part of equality: two events that say the same thing
    #: to the browser are the same event, whichever call they belonged to.
    call_id: str | None = field(default=None, compare=False)


class VoiceProvider(abc.ABC):
    """A live speech-to-speech session with one provider."""

    #: Rate the browser must send microphone audio at.
    input_sample_rate: int = 16000
    #: Rate the provider's reply audio comes back at.
    output_sample_rate: int = 24000
    #: Provider's hard cap on a single audio session, for the UI to warn about.
    session_limit_minutes: int = 15
    #: Whether the provider reconnects itself at that cap to continue the same
    #: conversation. When True the cap is a seam the guest never notices, not
    #: an ending — the UI says so rather than counting down to a stop.
    auto_resumes: bool = False

    def __init__(self, voice: str, instructions: str) -> None:
        self.voice = voice
        self.instructions = instructions

    @abc.abstractmethod
    async def __aenter__(self) -> VoiceProvider:
        """Open the upstream session."""

    @abc.abstractmethod
    async def __aexit__(self, *exc) -> None:
        """Close it."""

    @abc.abstractmethod
    async def send_audio(self, pcm16: bytes) -> None:
        """Forward one chunk of microphone audio upstream."""

    async def send_text(self, text: str) -> None:
        """Inject a text turn from the server side, not the guest.

        Used to keep a slide tour moving: when the model narrates a slide and
        then stops instead of calling next_slide, the server nudges it with
        one of these. Default no-op so a provider that doesn't support it (or
        doesn't need it) simply never advances by this route — nothing breaks,
        the tour just falls back to being wholly model-driven.
        """
        return None

    def stand_down_floor(self) -> None:
        """Stop distance-filtering the microphone for this session.

        Called for sessions the machine opened itself (a face at the door):
        the person it greeted is far away by construction, and the
        near-field floor would filter out the very reply the greeting asked
        for. Default no-op — only the local VAD gate has a floor.
        """
        return None

    def watch_segments(self, hook, *, keep_audio: bool = False) -> bool:
        """Call `hook(segment, t0, t1, pcm16) -> str | None` whenever a
        speech segment closes, *before* the end-of-turn signal goes out; a
        returned string is sent as a context note (`send_context_note`).

        Listening focus (docs/listening-focus-2026-10-09.md). The hook runs
        on the audio path and must only hand work off. False = this
        provider has no local segmenter (Gemini without VAD_MODE=local,
        OpenAI), and nothing will ever be reported.
        """
        return False

    async def send_context_note(self, text: str) -> bool:
        """Add `text` to the model's context WITHOUT starting a turn.

        For speaker labels: the model must read it, never answer it as a
        question and never say it aloud. False = not sent (a provider or a
        model with no way to add context mid-conversation without a turn —
        and a turn is exactly what a label must not be).
        """
        return False

    #: Can `play_recorded` put a recorded line out as her reply? The ceremony
    #: cues (app/event_cues.py) ask before they promise "recorded" to anyone.
    plays_recorded: bool = False
    #: Can a ceremony line carry a story's picture (`play_recorded(picture=)`,
    #: `queue_picture`)? event_cues asks before it passes one: a provider
    #: without the keyword must never be handed it (the line would not play).
    shows_pictures: bool = False

    async def play_recorded(self, text: str, pcm: bytes, *, lang: str = "th",
                            wait_tools: bool = False, interrupt_first: bool = False,
                            report: dict | None = None, picture: dict | None = None) -> bool:
        """Play `pcm` (24 kHz mono PCM16, her own voice recorded ahead) with
        `text` as its transcript, as if it were her reply. True = the whole
        line went out. Default: not supported, nothing sent.

        A provider that supports it raises its hold on the model's own output
        the moment this is CALLED (before the returned coroutine runs), so a
        caller inside a tool call is covered from the instant it asks.

        `picture` (`shows_pictures` only): a `story_picture` event's data, put
        out right before the line's first word -- after the `interrupted` that
        stops an old reply (it would settle a story already drawn) -- so the
        page stages it as THIS answer's screen before her first sound."""
        return False

    def queue_picture(self, picture: dict) -> bool:
        """A `story_picture` for the reply to the text just sent (a ceremony cue
        the model reads, app/event_cues.py): queued behind everything already on
        its way to the page, ahead of that reply. False = not queued, because
        that reply is not the next thing out or there is no stream to put it in."""
        return False

    def stop_line(self, *, hold_model: bool = False, clear: bool = False) -> dict:
        """Staff stopped the ceremony line being said (app/event_cues.py `cut`):
        stop feeding a recorded line and lower its hold, drop what is queued for
        the page, hold the rest of the model's turn when it is reading one
        (`hold_model`), stop the page's audio (`interrupted`), and take the
        line's picture down (`clear`). What it did, by key; {} = nothing it can."""
        return {}

    def end_recorded(self, report: dict | None = None) -> None:
        """Drop a hold `play_recorded` raised whose line will not be played --
        only the one whose `report` this is, when given (a newer line's stays)."""
        return None

    def unquote_next_reply(self, on: bool = True) -> None:
        """Her next reply reads a line quoted in « » (a ceremony cue, read mode):
        keep the marks out of its transcript -- on the robot that is the caption.
        `on=False`: that turn is over, or its text never went out."""
        return None

    def refresh_recorded(self, report: dict) -> bool:
        """Keep the hold of a recorded line that has not started yet (its caller is
        still waiting). False = no such hold."""
        return False

    def tools_pending(self) -> bool:
        """A tool call of the model's is running right now."""
        return False

    def mark_named(self, at: float) -> None:
        """She has just spoken a line of the exchange (a ceremony cue); its audio
        ends at `at`. The reply gate's "she was addressed" window runs from then."""
        return None

    def tap_output(self, fn):
        """Call `fn(event)` with every event this provider hands the session,
        until the returned callable is called. None = this provider cannot
        (the cue log then has no fidelity for a line the model read)."""
        return None

    @abc.abstractmethod
    def events(self) -> AsyncIterator[ProviderEvent]:
        """Yield normalized events until the session ends."""


class ProviderError(RuntimeError):
    """Raised when a provider can't start — missing key, bad model, no network."""
