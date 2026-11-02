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


#: Non-speech markup in the transcript of her speech: robot 27 Oct 17:21,
#: after a story she "said" "<no speech>{pause}" (split "<no " / "speech>
#: {pause}") over a moment of silent audio -- shown on the screen as her
#: line. Eval outputs carried "</em>". Speech transcribed has no angle or
#: curly brackets in any language this robot speaks; a tag is letters only.
#: At most three words, the first right after the bracket: a stray "<" or "{"
#: before a sentence ("The lobby <" + "is on the ground floor") is no tag,
#: and was held to the turn's end and dropped with the words after it
#: (review 27 Oct).
_TAG_WORDS = r"[A-Za-z][A-Za-z_-]*(?: [A-Za-z_-]+){0,2}"
_TAG_RE = re.compile(r"</?%s>|\{%s\}" % (_TAG_WORDS, _TAG_WORDS))
#: The opening of a tag still arriving at the end of the buffer.
_PARTIAL_TAG_RE = re.compile(r"(?:</?(?:%s ?)?|\{(?:%s ?)?)\Z" % (_TAG_WORDS, _TAG_WORDS))


def _held_suffix_len(buf: str) -> int:
    """How many trailing chars could still become a marker, so must wait."""
    for marker in MARKERS:
        # Longest suffix of buf that is a proper prefix of this marker.
        limit = min(len(buf), len(marker) - 1)
        for size in range(limit, 0, -1):
            if marker.startswith(buf[-size:]):
                return size
    return 0


class TranscriptBudget:
    """Never let the output transcript run far ahead of the output audio.

    The output transcript is the server's transcription of what the model
    SAYS, so it cannot honestly be much longer than the audio behind it.
    2026-10-11 (FAQ eval, gemini-3.8-live): one turn's transcript carried
    2,241 characters of the model's own planning ("1. **Analyze User
    Request**: The user is asking about ... Let's call `search_condo_info`")
    over 7.5 s of audio — 300 characters a second where speech is 12-15.
    On the robot that text is the speech bubble a guest reads. One in 2,143
    eval turns, none in 8,172 logged gallery replies: rare, and exactly the
    kind of thing nobody would notice until a customer photographed it.

    The allowance is generous (40 characters per second of audio, plus 120
    for the opening words that can land before their audio does), so real
    speech in any language never meets it; what it cuts is text with no
    audio behind it. Per turn: reset on turn_complete and on an interrupt.
    """

    CHARS_PER_SECOND = 40
    SLACK_CHARS = 120
    #: A transcription of speech has no markdown; the leaked planning did
    #: ("1.  **Analyze User Request**", "`search_condo_info`"). The ratio alone
    #: still let ~400 of its characters through, so text carrying these marks
    #: ends the turn's transcript — from that delta on, nothing more is shown
    #: until the next turn.
    #: NOT a paragraph break, though the leak had those too (review 12 Oct): a
    #: verse the model formats as lines arrives as one atomic "\n\n..." chunk
    #: with no audio, and that chunk is the ONLY signal session.py has to ask
    #: for it out loud (`_respeak_silent_block`). Tainting on "\n\n" silenced
    #: the respeak too — the guest got nothing at all. The rate cap still
    #: bounds such a block.
    _NOT_SPEECH = ("**", "`")

    def __init__(self, bytes_per_second: int) -> None:
        self._bps = bytes_per_second
        self.reset()

    def reset(self) -> None:
        self._audio_bytes = 0
        self._chars = 0
        self._tainted = False
        self.dropped = 0

    def add_audio(self, nbytes: int) -> None:
        self._audio_bytes += nbytes

    def allow(self, text: str) -> str:
        """The part of `text` that may be shown; the rest is counted as dropped."""
        if self._tainted or any(mark in text for mark in self._NOT_SPEECH):
            self._tainted = True
            self.dropped += len(text)
            return ""
        budget = self.SLACK_CHARS + self.CHARS_PER_SECOND * self._audio_bytes / self._bps
        room = max(0, int(budget) - self._chars)
        kept = text[:room]
        self._chars += len(kept)
        self.dropped += len(text) - len(kept)
        return kept


class TranscriptFilter:
    """Streaming filter: feed deltas in, get display-safe text out."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, delta: str) -> str:
        self._buf = _TAG_RE.sub("", self._buf + delta)
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

        # A tag whose closing bracket has not arrived yet: keep waiting.
        tag = _PARTIAL_TAG_RE.search(self._buf)
        if tag:
            out_parts.append(self._buf[: tag.start()])
            self._buf = self._buf[tag.start():]
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
        # a tag that never closed is markup too ("<no speech" at the turn's end)
        remaining = _PARTIAL_TAG_RE.sub("", _PARTIAL_MARKER_RE.sub("", self._buf))
        if len(remaining) >= self._FRAGMENT_IS_MARKER_AT:
            for marker in MARKERS:
                if marker.startswith(remaining):
                    remaining = ""  # a marker that never finished arriving
                    break
        self._buf = ""
        return remaining

    def reset(self) -> None:
        self._buf = ""


#: What a reply that is ONLY an acknowledgement reads as, once spaces and
#: punctuation are gone.
BARE_ACKS = ("ค่ะ", "คะ", "ค่า")
_ACK_NOISE = re.compile(r"[\s.,!?…ๆ\-–—\"'()\[\]{}]+")
#: A bare "ค่ะ" is about half a second of audio; past this much held audio
#: the reply is something else whatever its transcript says (24 kHz s16le).
ACK_MAX_HELD_BYTES = int(24000 * 2 * 1.2)


def _ack_norm(text: str) -> str:
    return _ACK_NOISE.sub("", text or "")


class BareAckGate:
    """Keep a reply that is only "ค่ะ" off the speaker and the screen.

    Owner, 15 Oct: talk not meant for Emma (staff among themselves) must get
    silence, not "ค่ะ" -- 24 of 142 robot turns 12-14 Oct were exactly that.
    The prompt cannot ask for silence (3.8 then speaks its stage direction:
    "Silently observe without responding."), but it reliably says a bare
    "ค่ะ", so the server drops that turn.

    A turn's first events are held until its transcript can no longer be a
    bare acknowledgement -- usually the first transcript piece ("ที่จอดรถ...",
    "Our parking..."), which in gemini-3.8 arrives with or before the first
    audio, so an answer is not delayed. A reply that opens "ค่ะ ..." waits for
    its next piece. At turn_complete a turn whose whole transcript is a bare
    acknowledgement is dropped; anything else held is released in order.
    Held audio past ACK_MAX_HELD_BYTES is released whatever the transcript.
    """

    def __init__(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self.reset()

    def reset(self) -> None:
        self._held: list = []
        self._text = ""
        self._bytes = 0
        self._passing = False

    def _could_be_bare(self) -> bool:
        n = _ack_norm(self._text)
        return any(ack.startswith(n) for ack in BARE_ACKS)

    def push(self, event) -> list:
        """An output event (assistant_transcript or audio) -> what to send now."""
        if not self.enabled or self._passing:
            return [event]
        self._held.append(event)
        if event.kind == "assistant_transcript":
            self._text += event.text or ""
        elif event.kind == "audio":
            self._bytes += len(event.audio or b"")
        if self._could_be_bare() and self._bytes <= ACK_MAX_HELD_BYTES:
            return []
        out, self._held, self._passing = self._held, [], True
        return out

    def close(self) -> tuple[list, bool]:
        """turn_complete: (events still owed, True if a bare acknowledgement
        was dropped). The next turn starts deciding again."""
        held, text = self._held, _ack_norm(self._text)
        self.reset()
        if not self.enabled or not held:
            return [], False
        if text in BARE_ACKS:
            return [], True
        return held, False


# ---- a reply to noise (owner 29 Oct) -------------------------------------------
#
# "บางทีหุ่นพูดเองเหมือนจะได้ยินเสียงใกล้แล้วมาตีความเอง อะไรที่ฟังไม่ชัดก็ไม่ต้องตอบ
# ให้คิดว่าเป็น noise แต่ถ้าเสียงดังอยู่ใกล้ แต่ฟังไม่ทันก็บอกหรือถามใหม่ ... ส่วนเสียงที่ไกล
# หรือฟังไม่ชัดก็ข้ามไป". Prompt rule 3 asks her to have ANY sound she could not
# make out said again, so a cough or a far word through the gate got a sentence.
# Robot logs 9-27 Oct: 610 segments let through, 184 answered; 18 answers to a
# segment the recogniser made 0-4 letters of. Those without a tool call and
# quieter than a voice at the robot (open rms < 0.06) were noise answered --
# "ดิฉันพร้อมให้ข้อมูล...", a Chinese refusal, "เช้า in English is morning"
# for "อ่ะ"; the loud ones (0.08: a show_unit, a story) and the ones that
# called a tool ("by?" -> hospitals) were questions whose caption came out
# short. So the server decides, by what it knows and the model does not:
# quiet + nothing heard + no tool = not played; loud and unclear still gets
# rule 3's "please say it again".

def _letters(text: str) -> int:
    """Letters of a caption, Thai vowel and tone marks included (they are not
    `isalpha`: "ครับ" is 4, the way the logs were measured)."""
    return sum(1 for c in text or "" if c.isalpha() or "฀" <= c <= "๿")

#: Letters of caption a guest's words make at the least ("ครับ" is 4).
NOISE_MAX_LETTERS = 4
#: ...but her name is never noise, however short and quiet ("Emma" is 4), nor
#: a guest saying they missed what SHE said (replay 15 Oct: "What?" at 0.053;
#: 28 Oct: "ห้ะ" at 0.047).
_HER_NAME = re.compile(r"emma|เอ็มม่า|เอมม่า|เอ็มมา|อิมม่า|เอ็มอา"
                       r"|\b(what|pardon|sorry|huh|again)\b|อะไร|ห้ะ|หะ|什么|что", re.IGNORECASE)
#: The transcript of the guest's words lands before her first audio in 429 of
#: 437 replies (robot logs 9-27 Oct); for the rest she waits this long for it.
NOISE_HOLD_BYTES = int(24000 * 2 * 0.8)


class NoiseReplyGate:
    """Keep her answer to a quiet sound nobody could make out off the speaker
    and the screen (module comment above). Off when `near_rms` is 0.

    The guest's side since her last reply is one "heard" (a question in two
    segments is one question): the loudest segment's opening rms, the
    caption so far, and whether a tool was called for it. Her reply is
    decided at its first event -- usually with the caption already in --
    held at most NOISE_HOLD_BYTES of audio while the caption is still empty.
    A reply to a server text or a tap (`text_turn`) is never judged."""

    def __init__(self, near_rms: float) -> None:
        self.near = float(near_rms or 0.0)
        self._heard: dict | None = None
        self.reset()

    @property
    def enabled(self) -> bool:
        return self.near > 0.0

    def reset(self) -> None:
        """A turn's output state (a new turn, or one cut off)."""
        self._held: list = []
        self._bytes = 0
        self._state: str | None = None        # None: undecided, "pass", "drop"
        self._output = False

    def segment(self, rms) -> None:
        """A voice segment opened (VAD start) at this rms. Over her reply as
        it streams, it starts a new "heard" (what she was saying is answered);
        before her reply, it joins the one waiting -- a tap's or a tool's
        reply stays theirs (replay 15 Oct: a tapped story's first words came
        just after a 0.023 sound, which used to own the story)."""
        rms = float(rms) if isinstance(rms, (int, float)) else None
        if self._heard is None or self._output:
            self._heard = {"rms": rms, "text": "", "tool": False, "server": False}
        elif rms is not None:
            prior = self._heard["rms"]
            self._heard["rms"] = rms if prior is None else max(prior, rms)

    def heard(self, text: str) -> None:
        if self._heard is not None and text:
            self._heard["text"] += text

    def tool_call(self) -> None:
        """She called a tool: her reply answers its result, never noise."""
        if self._heard is None:
            self._heard = {"rms": None, "text": "", "tool": True, "server": False}
        else:
            self._heard["tool"] = True

    def text_turn(self) -> None:
        """The server or a tap spoke last: her next reply answers that."""
        self._heard = {"rms": None, "text": "", "tool": False, "server": True}

    def _verdict(self, final: bool) -> str | None:
        h = self._heard
        if (not self.enabled or h is None or h["server"] or h["tool"] or h["rms"] is None
                or h["rms"] >= self.near):
            return "pass"
        if _letters(h["text"]) > NOISE_MAX_LETTERS or _HER_NAME.search(h["text"]):
            return "pass"           # words enough, or somebody calling her by name
        if final or self._bytes >= NOISE_HOLD_BYTES:
            return "drop"
        return None

    def push(self, event) -> list:
        """An output event (assistant_transcript or audio) -> what to send now."""
        self._output = True
        if self._state == "pass":
            return [event]
        if self._state == "drop":
            return []
        self._held.append(event)
        if event.kind == "audio":
            self._bytes += len(event.audio or b"")
        verdict = self._verdict(False)
        if verdict is None:
            return []
        self._state = verdict
        out, self._held = self._held, []
        return out if verdict == "pass" else []

    def close(self) -> tuple[list, dict | None]:
        """turn_complete: (events still owed, the dropped reply's facts or
        None). A turn that said something uses up what was heard; an empty
        one (3.8 completes before its tool call) leaves it for the reply."""
        state = self._state or (self._verdict(True) if self._output else "pass")
        held, heard, output = self._held, self._heard, self._output
        self.reset()
        if output:
            self._heard = None
        if state == "drop" and output:
            return [], {"rms": None if heard is None else heard["rms"],
                        "letters": 0 if heard is None else _letters(heard["text"])}
        return held, None
