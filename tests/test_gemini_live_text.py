"""Gemini 3.1 realtime text must trigger speech, not seed silent history."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from app.providers.gemini import GeminiProvider


@pytest.mark.parametrize("model", ["gemini-3.1-flash-live-preview", "models/gemini-3.1-flash-live-preview"])
def test_31_greeting_and_arrival_use_realtime_text(model):
    provider = GeminiProvider.__new__(GeminiProvider)
    provider.model = model
    provider.greeting = "ทักทายสั้นๆ"
    provider._session = AsyncMock()

    async def go():
        await provider._send_greeting()
        await provider.send_text("ถึง Lobby แล้ว")

    asyncio.run(go())
    assert provider._session.send_realtime_input.await_count == 2
    provider._session.send_realtime_input.assert_any_await(text="ทักทายสั้นๆ")
    provider._session.send_realtime_input.assert_any_await(text="ถึง Lobby แล้ว")
    provider._session.send_client_content.assert_not_awaited()


def test_25_keeps_complete_user_turn():
    provider = GeminiProvider.__new__(GeminiProvider)
    provider.model = "gemini-2.5-flash-native-audio-preview-12-2025"
    provider._session = AsyncMock()
    asyncio.run(provider.send_text("ทักทายสั้นๆ"))
    call = provider._session.send_client_content.await_args.kwargs
    assert call["turn_complete"] is True
    assert call["turns"].parts[0].text == "ทักทายสั้นๆ"
    provider._session.send_realtime_input.assert_not_awaited()
