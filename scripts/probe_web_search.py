#!/usr/bin/env python3
"""
Does OUR live model accept google_search grounding? Measure, then flip.

    python scripts/probe_web_search.py

First run (2026-08-22) answered the question: a bare AUDIO session
connects fine, the same session plus google_search gets 1011 — grounding
has its own quota and the free tier has none. So this is a billing
question, not a model-compatibility one. Re-run after enabling billing;
until both probes print OK, WEB_SEARCH stays false. (Both matter:
grounding alone can work while the mix with function declarations is
rejected, and Emma always carries function tools.)

Note the modality: this model rejects response_modalities=["TEXT"] with
1007 — probes must ask for AUDIO, which the first version of this script
got wrong and briefly mistook for an exhausted daily quota.
"""
from __future__ import annotations

import asyncio
import sys


async def probe(client, model, label, tools) -> bool:
    config = {"response_modalities": ["AUDIO"], "tools": tools}
    try:
        async with client.aio.live.connect(model=model, config=config) as sess:
            await sess.send_client_content(turns={"role": "user", "parts": [
                {"text": "ตอบสั้นๆ หนึ่งประโยค: ข่าวใหญ่ของวันนี้คืออะไร"}]})
            audio = 0
            async for msg in sess.receive():
                if msg.data:
                    audio += len(msg.data)
                sc = getattr(msg, "server_content", None)
                if sc is not None and getattr(sc, "turn_complete", False):
                    break
        print(f"[{label}] OK — audio {audio} bytes")
        return True
    except Exception as e:
        print(f"[{label}] FAILED: {type(e).__name__}: {str(e)[:200]}")
        return False


async def main() -> int:
    from google import genai
    from google.genai import types

    from app.config import settings

    if not settings.gemini_api_key:
        print("GEMINI_API_KEY is not set")
        return 1
    client = genai.Client(api_key=settings.gemini_api_key,
                          http_options={"api_version": "v1beta"})
    gs = types.Tool(google_search=types.GoogleSearch())
    fn = types.Tool(function_declarations=[types.FunctionDeclaration(
        name="set_lights", description="เปิดปิดไฟ",
        parameters={"type": "OBJECT",
                    "properties": {"on": {"type": "BOOLEAN"}}})])

    alone = await probe(client, settings.gemini_model, "google_search alone", [gs])
    mixed = await probe(client, settings.gemini_model,
                        "google_search + function tools", [gs, fn])

    if alone and mixed:
        print("\nทั้งคู่ผ่าน → ตั้ง WEB_SEARCH=true ใน .env ได้")
        return 0
    if alone and not mixed:
        print("\ngrounding เดี่ยวผ่านแต่ผสม function ไม่ผ่าน — Emma ใช้ไม่ได้"
              " (เธอมีเครื่องมืออื่นเสมอ) อย่าเปิด WEB_SEARCH")
    else:
        print("\nยังใช้ไม่ได้กับโมเดลนี้ อย่าเปิด WEB_SEARCH"
              " (ถ้า error คือ quota ให้ลองใหม่หลังโควตารีเซ็ต)")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
