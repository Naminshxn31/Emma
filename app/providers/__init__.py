"""Provider registry — pick a speech-to-speech backend by name."""
from __future__ import annotations

from app.config import settings
from app.providers.base import ProviderError, ProviderEvent, VoiceProvider

__all__ = ["ProviderError", "ProviderEvent", "VoiceProvider", "get_provider", "default_voice_for"]


def default_voice_for(provider: str) -> str:
    return settings.gemini_voice if provider == "gemini" else settings.openai_voice


def get_provider(provider: str, voice: str, instructions: str, greeting: str | None = None,
                 use_tools: bool = True) -> VoiceProvider:
    """Construct (but don't open) a session for `provider`.

    Imports are deferred so installing only one provider's SDK is enough to
    run the app with that provider.
    """
    if provider == "gemini":
        from app.providers.gemini import GeminiProvider

        return GeminiProvider(voice, instructions, greeting=greeting,
                              use_tools=use_tools)
    if provider == "openai":
        from app.providers.openai_realtime import OpenAIProvider

        return OpenAIProvider(voice, instructions, greeting=greeting, use_tools=use_tools)
    raise ProviderError(f"Unknown VOICE_PROVIDER '{provider}'. Use 'gemini' or 'openai'.")
