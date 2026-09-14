"""Voice catalogues for the picker UI, per provider.

Voice IDs are fixed by each provider's API — a typo means a failed session,
so there's a test asserting these match the documented lists. Descriptions
and gradient colours are ours, purely for the picker screen.

A session's voice locks once the model has spoken, which is why the guest
picks before the conversation starts.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    description_en: str
    description_th: str
    gradient: tuple[str, str]
    recommended: bool = False


# --- Gemini Live voices. Trimmed to two on 2026-09-14 by the owner's call
# ("เหลือสองเสียงนี้พอ เอา Despina ตั้ง") — Despina (default, other languages)
# and Zephyr (kept for Thai). Gemini Live sets one voice per session, so the
# per-language split is a per-link choice (?voice=), not a mid-call switch.
# The full 30-voice catalogue lives in git history if more are wanted again.
# Descriptions follow Google's one-word characterisations. ---
GEMINI_VOICES: list[Voice] = [
    Voice("Despina", "Despina", "Smooth", "นุ่มนวล", ("#E3C9E8", "#8A4FA3"), recommended=True),
    Voice("Zephyr", "Zephyr", "Bright", "สดใส", ("#CBE0A8", "#5E8B32")),
]

# --- OpenAI Realtime. marin/cedar are OpenAI's recommended pair. ---
OPENAI_VOICES: list[Voice] = [
    Voice("marin", "Marin", "Warm and grounded", "อบอุ่น หนักแน่น", ("#8ED0F5", "#1B6FE0"), recommended=True),
    Voice("cedar", "Cedar", "Calm and steady", "สงบ นิ่ง", ("#A8D5BA", "#2E7D57"), recommended=True),
    Voice("alloy", "Alloy", "Neutral and clear", "เป็นกลาง ชัดเจน", ("#D8D8D8", "#6B6B6B")),
    Voice("ash", "Ash", "Soft and thoughtful", "นุ่ม ครุ่นคิด", ("#C9C2E8", "#5A4FA3")),
    Voice("ballad", "Ballad", "Gentle and expressive", "อ่อนโยน มีอารมณ์", ("#F3C6D8", "#B04A79")),
    Voice("coral", "Coral", "Bright and friendly", "สดใส เป็นมิตร", ("#FFC2A8", "#E0603A")),
    Voice("echo", "Echo", "Even and composed", "เรียบ นิ่ง", ("#BFD4DE", "#4C7A91")),
    Voice("sage", "Sage", "Measured and wise", "สุขุม น่าเชื่อถือ", ("#CBE0A8", "#5E8B32")),
    Voice("shimmer", "Shimmer", "Light and airy", "เบา โปร่ง", ("#FFE3A3", "#D9A02B")),
    Voice("verse", "Verse", "Lively and warm", "มีชีวิตชีวา อบอุ่น", ("#F5B8C4", "#C24A64")),
]

CATALOGUES: dict[str, list[Voice]] = {
    "gemini": GEMINI_VOICES,
    "openai": OPENAI_VOICES,
}


def for_provider(provider: str) -> list[Voice]:
    return CATALOGUES.get(provider, GEMINI_VOICES)


def ids_for(provider: str) -> set[str]:
    return {v.id for v in for_provider(provider)}


def is_valid(provider: str, voice_id: str) -> bool:
    return voice_id in ids_for(provider)


def as_dicts(provider: str) -> list[dict]:
    return [asdict(v) for v in for_provider(provider)]
