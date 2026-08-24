"""
OpenAI Realtime API provider — speech-to-speech, paid per audio minute.

Raw WebSocket (the GA interface, so no `OpenAI-Beta` header). Audio is PCM16
**24 kHz** in both directions, base64 inside JSON events.

Interruption needs more work here than with Gemini: OpenAI cancels the
in-flight response but keeps the full reply in conversation history, so the
client must also report how much audio the guest actually heard via
`conversation.item.truncate`. Without that the model believes it was heard in
full and the conversation drifts. `app/session.py` tracks the played duration
and calls `truncate()` below.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import websockets

from app.config import settings
from app.providers.base import ProviderError, ProviderEvent, VoiceProvider

logger = logging.getLogger("condo_voice.openai")


def build_session_config(voice: str, instructions: str, use_tools: bool = True) -> dict[str, Any]:
    """The session.update payload that configures the whole conversation."""
    if settings.openai_turn_detection == "semantic_vad":
        turn_detection: dict[str, Any] = {
            "type": "semantic_vad",
            "eagerness": settings.openai_vad_eagerness,
            "create_response": True,
            "interrupt_response": True,
        }
    else:
        turn_detection = {
            "type": "server_vad",
            "threshold": 0.5,
            "prefix_padding_ms": settings.vad_prefix_padding_ms,
            "silence_duration_ms": settings.vad_silence_ms,
            "create_response": True,
            "interrupt_response": True,
        }

    from app import tools

    if use_tools:
        tools.load_tools()
        declared = tools.as_openai_tools()
    else:
        declared = []

    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "output_modalities": ["audio"],
            "instructions": instructions,
            **({"tools": declared, "tool_choice": "auto"} if declared else {}),
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "turn_detection": turn_detection,
                    "transcription": {"model": settings.openai_transcribe_model},
                },
                "output": {
                    # `rate` is required here too. Omitting it makes the API
                    # reject the whole session.update, which fails silently in
                    # the worst way: the socket stays open and the model still
                    # answers, but with none of these instructions applied — so
                    # it behaves like a generic assistant instead of the condo
                    # receptionist, and input transcription never turns on.
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "voice": voice,
                },
            },
        },
    }


class OpenAIProvider(VoiceProvider):
    input_sample_rate = 24000
    output_sample_rate = 24000
    session_limit_minutes = 60

    def __init__(self, voice: str, instructions: str, greeting: str | None = None,
                 use_tools: bool = True) -> None:
        super().__init__(voice, instructions)
        self.use_tools = use_tools
        self.greeting = greeting
        self._ws: websockets.WebSocketClientProtocol | None = None
        #: Item id of the reply currently playing — needed to truncate it.
        self.current_item_id: str | None = None
        #: True once the server confirms our session.update was accepted.
        self.configured = False

    async def __aenter__(self) -> OpenAIProvider:
        if not settings.openai_api_key:
            raise ProviderError("OPENAI_API_KEY is not set. Add it to .env")
        url = f"{settings.openai_url}?model={settings.openai_model}"
        try:
            self._ws = await websockets.connect(
                url,
                additional_headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                max_size=None,
            )
        except Exception as exc:
            raise ProviderError(f"Could not reach OpenAI Realtime: {exc}") from exc

        await self._ws.send(json.dumps(build_session_config(self.voice, self.instructions, use_tools=self.use_tools)))
        if self.greeting:
            await self._ws.send(json.dumps({
                "type": "response.create",
                "response": {"instructions": self.greeting},
            }))
        return self

    async def __aexit__(self, *exc) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass

    async def send_audio(self, pcm16: bytes) -> None:
        if self._ws is None:
            return
        await self._ws.send(json.dumps({
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(pcm16).decode("ascii"),
        }))

    async def truncate(self, heard_ms: int) -> None:
        """Tell OpenAI how much of the interrupted reply was actually heard."""
        if self._ws is None or self.current_item_id is None:
            return
        await self._ws.send(json.dumps({
            "type": "conversation.item.truncate",
            "item_id": self.current_item_id,
            "content_index": 0,
            "audio_end_ms": max(0, heard_ms),
        }))

    async def _run_tool_calls(self, fn_calls: list[dict]) -> AsyncIterator[ProviderEvent]:
        """Run the requested functions, return the outputs, ask for a reply."""
        from app import tools

        calls = []
        for call in fn_calls:
            try:
                args = json.loads(call.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append((call.get("call_id"), call.get("name", ""), args))

        for _cid, name, args in calls:
            logger.info("tool call: %s(%s)", name, args)
            yield ProviderEvent(kind="tool_call", text=name)

        results = await tools.dispatch_all(calls)

        for call_id, name, result in results:
            await self._ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(result, ensure_ascii=False),
                },
            }))
            yield ProviderEvent(kind="tool_result", text=name, data=result)

        # Unlike Gemini, OpenAI won't speak again on its own after a tool
        # result — it has to be asked.
        await self._ws.send(json.dumps({"type": "response.create"}))

    async def events(self) -> AsyncIterator[ProviderEvent]:
        if self._ws is None:
            return
        try:
            async for raw in self._ws:
                try:
                    evt = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                etype = evt.get("type")

                if etype == "response.output_audio.delta":
                    if evt.get("item_id"):
                        self.current_item_id = evt["item_id"]
                    yield ProviderEvent(kind="audio", audio=base64.b64decode(evt["delta"]))

                elif etype == "response.output_audio_transcript.delta":
                    yield ProviderEvent(kind="assistant_transcript", text=evt.get("delta", ""))

                elif etype == "conversation.item.input_audio_transcription.completed":
                    yield ProviderEvent(kind="user_transcript", text=evt.get("transcript", ""))

                elif etype == "input_audio_buffer.speech_started":
                    # Fires for *every* utterance, not just barge-in. Sending
                    # "interrupted" here tagged ordinary replies as cut off;
                    # let the browser decide, since it knows whether audio is
                    # actually playing.
                    yield ProviderEvent(kind="speech_started")

                elif etype == "session.updated":
                    self.configured = True

                elif etype == "response.done":
                    # Function calls arrive as items on the finished response;
                    # run them and ask for a follow-up reply.
                    outputs = ((evt.get("response") or {}).get("output") or [])
                    fn_calls = [o for o in outputs if o.get("type") == "function_call"]
                    if fn_calls:
                        async for tool_evt in self._run_tool_calls(fn_calls):
                            yield tool_evt
                        continue
                    yield ProviderEvent(kind="turn_complete")

                elif etype == "error":
                    message = (evt.get("error") or {}).get("message") or "unknown upstream error"
                    if not self.configured:
                        # The dangerous case: the socket is fine and the model
                        # will happily keep talking, but our instructions were
                        # never applied — including the rule against inventing
                        # prices. Say so plainly instead of letting it look
                        # like a working assistant.
                        message = (
                            "Session setup was REJECTED, so the assistant is running "
                            "without its condo instructions or guardrails — do not "
                            "trust anything it says. Cause: " + message
                        )
                        logger.error("session.update rejected: %s", message)
                    yield ProviderEvent(kind="error", text=message)
        except asyncio.CancelledError:
            raise
        except websockets.ConnectionClosed:
            pass
        except Exception as exc:
            logger.exception("OpenAI Realtime session ended with an error")
            yield ProviderEvent(kind="error", text=str(exc))
