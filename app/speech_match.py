"""How close what Emma said is to the line she was given.

One scorer for the three places that ask: the voice render of the ceremony lines
(a take is kept only when its transcript matches the line), the cue log on the
robot (a cue read by the live model, `event_cue.fidelity`), and the ceremony eval
that answers the owner's 6 Nov question -- recorded lines or the live model.

The transcript is the live session's own output transcription, which writes Thai
without the spaces a script has and may write a number as digits ("1,066") or
leave out a comma. So both sides are folded before comparing: NFC, lower case,
no whitespace or punctuation, thousands separators dropped. What it does NOT
fold: a number spoken as Thai words ("หนึ่งพันหกสิบหก") -- that counts as a
difference, and `numbers` reports it separately so a reader can tell "said the
number in words" from "said another number".
"""
from __future__ import annotations

import difflib
import re
import unicodedata

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def fold(text: str | None) -> str:
    text = unicodedata.normalize("NFC", str(text or "")).lower()
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text)
    # \W also matches Thai vowel signs and tone marks in some builds -- keep every
    # letter and mark, drop only spaces and punctuation
    return "".join(ch for ch in text if unicodedata.category(ch)[0] in "LMN")


def fidelity(heard: str | None, line: str | None) -> float:
    """0..1, 1 = the same characters in the same order once folded."""
    a, b = fold(heard), fold(line)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def numbers(text: str | None) -> list[float]:
    out = []
    for m in _NUMBER.finditer(text or ""):
        try:
            out.append(float(m.group(0).replace(",", "")))
        except ValueError:
            pass
    return out


def number_check(heard: str | None, line: str | None) -> dict:
    """{"missing": [...], "extra": [...]} as numbers: in the line but not heard,
    heard but not in the line. An extra number is the costly one -- a figure the
    approval never covered."""
    said, want = numbers(heard), numbers(line)
    missing, extra = list(want), []
    for v in said:
        hit = next((w for w in missing if abs(w - v) < 1e-6), None)
        if hit is None:
            extra.append(v)
        else:
            missing.remove(hit)
    return {"missing": missing, "extra": extra}
