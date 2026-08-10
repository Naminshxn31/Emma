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
        {"type": "user_transcript",      "text": …}
        {"type": "assistant_transcript", "text": …}
        {"type": "interrupted"}     stop playback immediately
        {"type": "turn_complete"}
        {"type": "error", "message": …}
        binary frames : PCM16 mono @ `output_rate` (reply audio)
"""
from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal


@dataclass
class ProviderEvent:
    """One normalized event on its way to the browser."""

    kind: Literal[
        "audio", "user_transcript", "assistant_transcript",
        # The guest began speaking. Distinct from "interrupted": whether it
        # counts as barge-in depends on whether a reply is currently playing,
        # and only the browser knows that.
        "speech_started",
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
    ]
    audio: bytes | None = None
    text: str | None = None
    data: dict | None = None


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

    @abc.abstractmethod
    def events(self) -> AsyncIterator[ProviderEvent]:
        """Yield normalized events until the session ends."""


class ProviderError(RuntimeError):
    """Raised when a provider can't start — missing key, bad model, no network."""
