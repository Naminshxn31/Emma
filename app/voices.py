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


# --- Gemini Live: 30 native-audio voices. Descriptions follow Google's own
# one-word characterisations where they publish them. ---
GEMINI_VOICES: list[Voice] = [
    Voice("Kore", "Kore", "Firm", "หนักแน่น", ("#8ED0F5", "#1B6FE0"), recommended=True),
    Voice("Sulafat", "Sulafat", "Warm", "อบอุ่น", ("#FFC2A8", "#E0603A"), recommended=True),
    Voice("Leda", "Leda", "Youthful", "สดใส วัยรุ่น", ("#F3C6D8", "#B04A79")),
    Voice("Aoede", "Aoede", "Breezy", "สบายๆ", ("#A8D5BA", "#2E7D57")),
    Voice("Puck", "Puck", "Upbeat", "กระฉับกระเฉง", ("#FFE3A3", "#D9A02B")),
    Voice("Charon", "Charon", "Informative", "ให้ข้อมูล", ("#BFD4DE", "#4C7A91")),
    Voice("Fenrir", "Fenrir", "Excitable", "ตื่นเต้น", ("#FFB3B3", "#C23A3A")),
    Voice("Orus", "Orus", "Firm", "หนักแน่น", ("#C9C2E8", "#5A4FA3")),
    Voice("Zephyr", "Zephyr", "Bright", "สดใส", ("#CBE0A8", "#5E8B32")),
    Voice("Autonoe", "Autonoe", "Bright", "สดใส", ("#D8E9F5", "#3E7CA8")),
    Voice("Callirrhoe", "Callirrhoe", "Easy-going", "สบายๆ ไม่เกร็ง", ("#E8D5C4", "#8B6B4A")),
    Voice("Despina", "Despina", "Smooth", "นุ่มนวล", ("#E3C9E8", "#8A4FA3")),
    Voice("Erinome", "Erinome", "Clear", "ชัดเจน", ("#D4D4D4", "#6B6B6B")),
    Voice("Laomedeia", "Laomedeia", "Upbeat", "กระฉับกระเฉง", ("#FFD9A3", "#D98A2B")),
    Voice("Achernar", "Achernar", "Soft", "นุ่ม", ("#F5D8E4", "#B0708A")),
    Voice("Algieba", "Algieba", "Smooth", "นุ่มนวล", ("#C4DCE8", "#4A7E96")),
    Voice("Alnilam", "Alnilam", "Firm", "หนักแน่น", ("#B8C4E8", "#3F4FA3")),
    Voice("Enceladus", "Enceladus", "Breathy", "เสียงลม", ("#E0E8F0", "#7A8FA3")),
    Voice("Iapetus", "Iapetus", "Clear", "ชัดเจน", ("#CFE3D4", "#4E8B62")),
    Voice("Gacrux", "Gacrux", "Mature", "ผู้ใหญ่", ("#D4C4B0", "#7A6248")),
    Voice("Rasalgethi", "Rasalgethi", "Informative", "ให้ข้อมูล", ("#C4D4E8", "#46689B")),
    Voice("Schedar", "Schedar", "Even", "เรียบ นิ่ง", ("#D0D8DC", "#5F7078")),
    Voice("Umbriel", "Umbriel", "Easy-going", "สบายๆ ไม่เกร็ง", ("#CCE0DC", "#4A8078")),
    Voice("Vindemiatrix", "Vindemiatrix", "Gentle", "อ่อนโยน", ("#E8DCE8", "#8A6B96")),
    Voice("Achird", "Achird", "Friendly", "เป็นมิตร", ("#FFD4C4", "#C2704A")),
    Voice("Algenib", "Algenib", "Gravelly", "เสียงห้าว", ("#BFB5A8", "#6B5D4A")),
    Voice("Pulcherrima", "Pulcherrima", "Forward", "ตรงไปตรงมา", ("#F5C4D0", "#B04A64")),
    Voice("Sadachbia", "Sadachbia", "Lively", "มีชีวิตชีวา", ("#D8F0C4", "#6B9B32")),
    Voice("Sadaltager", "Sadaltager", "Knowledgeable", "รอบรู้", ("#C4CCE8", "#464F9B")),
    Voice("Zubenelgenubi", "Zubenelgenubi", "Casual", "เป็นกันเอง", ("#DCDCC4", "#7A7A4A")),
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
