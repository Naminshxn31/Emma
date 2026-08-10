"""
Collapse the word-spacing the recogniser inserts into Thai.

Thai is not written with spaces between words. The recogniser emits them
anyway, so a guest saying "เอาทุกคนเลย" shows up as:

    เอา ทุก คน เลย

which reads to a Thai speaker roughly the way "a l l  o f  t h e m" reads in
English — understandable, obviously wrong.

The rule is narrow on purpose: **drop a space only when the characters on
both sides of it are Thai.** Spaces that separate Thai from Latin are how you
tell "Embassy World" apart from the words around it, so those stay:

    โครงการ Embassy World พัฒนา โดยบริษัท   ->   โครงการ Embassy World พัฒนาโดยบริษัท

Known cost, and the reason this is **off by default** (THAI_SPACING=true to
enable): Thai also uses spaces at clause boundaries, and nothing here can
tell those apart from word-spacing, so they go too —

    ขอโทษค่ะ ยังไม่มีข้อมูลราคา   ->   ขอโทษค่ะยังไม่มีข้อมูลราคา

which is grammatical but not what was said. Distinguishing the two needs
statistics over the whole utterance (word-spacing separates *every* word,
clause spacing doesn't), and gathering those means buffering the utterance
instead of streaming it — the transcript would stop appearing live.

The root cause is more likely the language hint: an unbiased recogniser was
decoding Thai as English. Fix TRANSCRIBE_LANGUAGES first and only reach for
this if the word-spacing survives it.

Streaming: transcripts arrive as deltas, and a space can be the last
character of one delta with the deciding character in the next. So a
candidate space is held back until the character after it arrives.
"""
from __future__ import annotations

# Thai block, including the digits and punctuation that sit inside it.
_THAI_START, _THAI_END = "฀", "๿"


def is_thai(ch: str) -> bool:
    return bool(ch) and _THAI_START <= ch <= _THAI_END


class ThaiSpacing:
    """Streaming filter. Feed deltas in, get display text out."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._pending = ""  # spaces held pending the next character
        self._prev = ""     # last character emitted

    def feed(self, text: str) -> str:
        if not self.enabled:
            return text
        out: list[str] = []
        for ch in text:
            if ch == " ":
                # Only worth holding if what precedes it is Thai; otherwise
                # it can never be dropped, so emit it now.
                if is_thai(self._prev):
                    self._pending += ch
                else:
                    out.append(ch)
                    self._prev = ch
                continue

            if self._pending:
                # Thai on both sides -> word-spacing, drop it. Anything else
                # (Latin, a digit, punctuation, end of input) -> keep it.
                if not is_thai(ch):
                    out.append(self._pending)
                self._pending = ""

            out.append(ch)
            self._prev = ch
        return "".join(out)

    def flush(self) -> str:
        """End of turn: a held space had nothing after it, so it stays."""
        pending, self._pending, self._prev = self._pending, "", ""
        return pending

    def reset(self) -> None:
        self._pending = ""
        self._prev = ""


def collapse(text: str) -> str:
    """Non-streaming convenience wrapper."""
    f = ThaiSpacing()
    return f.feed(text) + f.flush()
