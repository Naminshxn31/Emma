"""Provider registry — pick a speech-to-speech backend by name."""
from __future__ import annotations

from app.config import settings
from app.providers.base import ProviderError, ProviderEvent, VoiceProvider

__all__ = ["ProviderError", "ProviderEvent", "VoiceProvider", "get_provider", "default_voice_for"]


def default_voice_for(provider: str) -> str:
    # offline plays clips rendered in GEMINI_VOICE (app/offline/voice.py)
    offline = provider == "offline" and settings.offline_fallback
    return settings.gemini_voice if provider == "gemini" or offline else settings.openai_voice


def get_provider(provider: str, voice: str, instructions: str, greeting: str | None = None,
                 use_tools: bool = True, vad_floor: float | None = None) -> VoiceProvider:
    """Construct (but don't open) a session for `provider`.

    Imports are deferred so installing only one provider's SDK is enough to
    run the app with that provider. `vad_floor` only means something to the
    provider with a local VAD gate (Gemini); OpenAI's detection is its own.
    """
    if provider == "gemini":
        from app.providers.gemini import GeminiProvider

        return GeminiProvider(voice, instructions, greeting=greeting,
                              use_tools=use_tools, vad_floor=vad_floor)
    if provider == "openai":
        from app.providers.openai_realtime import OpenAIProvider

        return OpenAIProvider(voice, instructions, greeting=greeting, use_tools=use_tools)
    if provider == "offline" and settings.offline_fallback:
        # OFFLINE_FALLBACK (docs/emma-offline-mode.md): approved lines and
        # stories, local ASR, no network and no key. Chosen by the session
        # when app/net_health.py says the link is down. With the switch off
        # the name does not exist (a `?provider=offline` URL gets the same
        # "unknown provider" as before the switch was written).
        from app.providers.offline import OfflineProvider

        return OfflineProvider(voice, instructions, greeting=greeting,
                               use_tools=use_tools, vad_floor=vad_floor)
    raise ProviderError(f"Unknown VOICE_PROVIDER '{provider}'. Use 'gemini' or 'openai'.")
