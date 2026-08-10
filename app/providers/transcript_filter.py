"""
Strip affective-dialogue annotations out of Gemini's output transcript.

With `enable_affective_dialog` on, the transcript stream carries emotion
labels alongside the words the model actually speaks:

    emotion_user
    calmness
    emotion_model
    joy
    สวัสดีค่ะ ยินดีต้อนรับ...

Those are internal signals, not speech, so they must not reach the on-screen
conversation. Dropping the feature would fix it too, but that's what makes
the reply's tone follow the guest's, so filter instead.

The awkward part is that transcripts arrive as **deltas** — a marker can be
split across two messages (`"emo"` then `"tion_model\njoy\n"`). So this holds
back any trailing text that could still turn out to be the beginning of a
marker, and releases it once it's clear it isn't.
"""
from __future__ import annotations

import re

MARKERS = ("emotion_user", "emotion_model")

# A marker is followed by whitespace and one label word (calmness, joy, …).
# The trailing \s ensures the label is complete before we drop it.
_MARKER_RE = re.compile(
    r"(?:%s)\s*[A-Za-z_]+\s" % "|".join(re.escape(m) for m in MARKERS)
)
# Same thing but allowing an unterminated label at the very end of the buffer.
_PARTIAL_MARKER_RE = re.compile(
    r"(?:%s)\s*[A-Za-z_]*\s*\Z" % "|".join(re.escape(m) for m in MARKERS)
)


def _held_suffix_len(buf: str) -> int:
    """How many trailing chars could still become a marker, so must wait."""
    for marker in MARKERS:
        # Longest suffix of buf that is a proper prefix of this marker.
        limit = min(len(buf), len(marker) - 1)
        for size in range(limit, 0, -1):
            if marker.startswith(buf[-size:]):
                return size
    return 0


class TranscriptFilter:
    """Streaming filter: feed deltas in, get display-safe text out."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, delta: str) -> str:
        self._buf += delta
        out_parts: list[str] = []

        while True:
            match = _MARKER_RE.search(self._buf)
            if match:
                out_parts.append(self._buf[: match.start()])
                self._buf = self._buf[match.end():]
                continue
            break

        # A marker whose label hasn't finished arriving yet: keep waiting.
        partial = _PARTIAL_MARKER_RE.search(self._buf)
        if partial:
            out_parts.append(self._buf[: partial.start()])
            self._buf = self._buf[partial.start():]
            return "".join(out_parts)

        hold = _held_suffix_len(self._buf)
        if hold:
            out_parts.append(self._buf[:-hold])
            self._buf = self._buf[-hold:]
        else:
            out_parts.append(self._buf)
            self._buf = ""

        return "".join(out_parts)

    #: A held-back fragment this long that still prefixes a marker is almost
    #: certainly a marker, not speech — no real reply ends in "emot". Shorter
    #: fragments ("e", "em") are far more likely to be the end of a word, so
    #: they get released rather than silently swallowed.
    _FRAGMENT_IS_MARKER_AT = 4

    def flush(self) -> str:
        """End of turn: release anything held back, minus dangling markers."""
        remaining = _PARTIAL_MARKER_RE.sub("", self._buf)
        if len(remaining) >= self._FRAGMENT_IS_MARKER_AT:
            for marker in MARKERS:
                if marker.startswith(remaining):
                    remaining = ""  # a marker that never finished arriving
                    break
        self._buf = ""
        return remaining

    def reset(self) -> None:
        self._buf = ""
