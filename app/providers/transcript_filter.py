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
import time

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

def _ceremony_answers_all() -> bool:
    """The ceremony's follow window with answer-all on (app/ceremony_follow.py `answers_all`): her reply to the
    host is never dropped for being quiet. Never raises; anything else = False (every other call unchanged)."""
    try:
        from app import ceremony_follow

        return bool(ceremony_follow.answers_all())
    except Exception:  # noqa: BLE001
        return False


def _ceremony_thai_ignored() -> bool:
    """CEREMONY_ENGLISH_ONLY inside the ceremony's answer-all window (owner 24 Nov ~15:06: "วันจริงเองห้ามพูดไทย ไม่ต้อง
    ฟังไทยด้วย"). Never raises; anything else = False."""
    try:
        from app.config import settings

        return bool(settings.ceremony_english_only) and _ceremony_answers_all()
    except Exception:  # noqa: BLE001
        return False


def _ceremony_answer_named() -> bool:
    """CEREMONY_ANSWER_NAMED (owner 24 Nov ~15:38). Never raises."""
    try:
        from app.config import settings

        return bool(settings.ceremony_answer_named)
    except Exception:  # noqa: BLE001
        return False


def _ceremony_name_first() -> bool:
    """CEREMONY_NAME_FIRST inside the ceremony's answer-all window. Never raises."""
    try:
        from app import ceremony_follow

        return ceremony_follow.name_first() and _ceremony_answers_all()
    except Exception:  # noqa: BLE001
        return False


def _ceremony_may_answer(text: str) -> bool:
    """His words may be answered live: they name her -- or, her name first, the decision said so. Never raises; a broken
    probe = the name rule."""
    try:
        from app import ceremony_follow

        return ceremony_follow.may_answer(text)
    except Exception:  # noqa: BLE001
        return bool(HER_NAME_ONLY.search(text or ""))


def _thai_only(text: str) -> bool:
    """A caption in Thai script with no Latin letter in it (staff talking in the room, 24 Nov rehearsal)."""
    t = text or ""
    return (sum(1 for c in t if "฀" <= c <= "๿") >= 2
            and not any(("a" <= c <= "z") or ("A" <= c <= "Z") for c in t))


def _letters(text: str) -> int:
    """Letters of a caption, Thai vowel and tone marks included (they are not
    `isalpha`: "ครับ" is 4, the way the logs were measured)."""
    return sum(1 for c in text or "" if c.isalpha() or "฀" <= c <= "๿")

#: A server's own text turn (a cue the model reads) may go on this long with no heard words of its own.
SERVER_TURN_S = 30.0
#: Letters of caption a guest's words make at the least ("ครับ" is 4).
NOISE_MAX_LETTERS = 4
#: The ceremony host's collar mic (GCK, rehearsal 24 Nov ~0.02-0.03): an EMPTY transcript at this level or more in
#: follow's answer-all window is a sentence the recogniser missed, not a room sound (NoiseReplyGate._verdict).
CEREMONY_HOST_MIN_RMS = 0.015
#: ...but her name is never noise, however short and quiet ("Emma" is 4), nor
#: a guest saying they missed what SHE said (replay 15 Oct: "What?" at 0.053;
#: 28 Oct: "ห้ะ" at 0.047).
_HER_NAME = re.compile(r"emma|เอ็มม่า|เอมม่า|เอ็มมา|อิมม่า|เอ็มอา"
                       r"|\b(what|pardon|sorry|huh|again)\b|อะไร|ห้ะ|หะ|什么|что", re.IGNORECASE)
#: Just her name (not "what?"): during an event's opening ceremony a reply has to
#: be asked for by it. The owner's own mouth: "เอ็มอา/อิหม่า" (26 Aug, wake word).
HER_NAME_ONLY = re.compile(r"emma|เอ็มม่า|เอมม่า|เอ็มมา|เอมมา|อิมม่า|อิหม่า|เอ็มอา", re.IGNORECASE)
#: ...and for this long after a reply her name earned, the same person goes on
#: without it. From her last output event, which is generated well ahead of her
#: voice (4-6x): a short answer leaves about this long after she stops talking.
NAMED_WINDOW_S = 30.0
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

    def __init__(self, near_rms: float, quiet=None, named_only=None, cue_only=None, follow_hold=None) -> None:
        self.near = float(near_rms or 0.0)
        #: () -> bool: a cue-only ceremony with no live window (app/event_host.py
        #: cue_only_closed): she speaks only the lines staff press, so every reply that
        #: is not a server turn is dropped, named or not. The mic is deaf then too --
        #: this is for the audio that still goes up while the robot moves (a spoken
        #: stop must reach the stop tools) and for a segment the window's end cut.
        self.cue_only = cue_only
        #: () -> bool: only a reply asked for by her name is played. An opening
        #: ceremony: the one voice she listens to is speaking to the ROOM, and a
        #: door that lets only that voice in still answers every sentence of its
        #: speech. A server text or a tap is never judged; tool calls still run.
        self.named_only = named_only
        #: () -> "wait" | "drop" | None (app/ceremony_follow.py `reply_hold`, owner 24 Nov: she answers the host
        #: without her name while follow mode's window is open): "wait" = the server has not yet decided whether
        #: the host's words are his scripted line -- her reply is held; "drop" = they were, the recording plays
        #: alone and this reply never does; None = hers to say, judged by the ordinary rules below.
        self.follow_hold = follow_hold
        self._named_at = float("-inf")
        #: () -> bool: everything she says now is kept off the speaker and the
        #: screen (WALK_QUIET -- a test walk, robot_face_link.walk_quiet), tool
        #: calls untouched. Asked at a reply's first event, like the rest.
        self.quiet = quiet
        self._heard: dict | None = None
        #: The words the reply going out now was judged on (`_heard` at its first event): a voice that opens while it
        #: streams starts the NEXT reply's words, and this one's turn_complete must not take them (robot 24 Nov 18:56).
        self._answers: dict | None = None
        self._server_at = float("-inf")
        #: The picture of a story she is about to tell in the ceremony (app/ceremony_stories.py): it goes out right before
        #: the first of her reply that does -- never for a reply that is dropped.
        self._picture = None
        self.reset()

    def hold_picture(self, event) -> None:
        self._picture = event

    def _with_picture(self, out: list) -> list:
        if self._picture is None or not out:
            return out
        picture, self._picture = self._picture, None
        return [picture, *out]

    @property
    def enabled(self) -> bool:
        return self.near > 0.0

    def reset(self) -> None:
        """A turn's output state (a new turn, or one cut off)."""
        self._held: list = []
        self._bytes = 0
        self._state: str | None = None        # None: undecided, "pass", "drop"
        self._output = False
        self._why = "noise"
        self._named_turn = False

    def segment(self, rms) -> None:
        """A voice segment opened (VAD start) at this rms. Over her reply as
        it streams, it starts a new "heard" (what she was saying is answered);
        before her reply, it joins the one waiting -- a tap's or a tool's
        reply stays theirs (replay 15 Oct: a tapped story's first words came
        just after a 0.023 sound, which used to own the story)."""
        rms = float(rms) if isinstance(rms, (int, float)) else None
        if self._heard is None or self._output:
            self._heard = {"rms": rms, "text": "", "tool": False, "server": False}
            self._picture = None                 # (new words of the guest: the old answer's picture is nobody's)
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
        self._server_at = time.monotonic()
        self._picture = None

    def _quiet_now(self) -> str | bool:
        try:
            return self.quiet()
        except Exception:  # noqa: BLE001 -- a probe that fails is "not quiet"
            return False

    def _named_only_now(self) -> bool:
        try:
            return bool(self.named_only())
        except Exception:  # noqa: BLE001 -- a probe that fails is the ordinary gate
            return False

    def _cue_only_now(self) -> bool:
        try:
            return bool(self.cue_only())
        except Exception:  # noqa: BLE001 -- a probe that fails is the ordinary gate
            return False

    def follow_waiting(self) -> bool:
        """A reply is held for follow's decision about the host's words (turn_complete waits for it, bounded)."""
        if self.follow_hold is None:
            return False
        h = self._heard
        if h is None or h["server"] or self._state is not None:
            return False
        try:
            return self.follow_hold() == "wait"
        except Exception:  # noqa: BLE001 -- a probe that fails is "no wait"
            return False

    def named_recently(self, now: float | None = None) -> bool:
        """Her name earned a reply a moment ago: this person is talking to her."""
        clock = time.monotonic() if now is None else now
        return clock - self._named_at <= NAMED_WINDOW_S

    def mark_named(self, at: float) -> None:
        """She just spoke a line of the exchange (a ceremony cue, app/event_cues.py):
        the window runs from `at` -- when that line's AUDIO ends, which may still be
        ahead. A cue goes out as a server turn, so nothing else opens it, and the
        host's next unnamed line ("ว้าว คุณฉลาดมาก...") was the speech of a stranger."""
        self._named_at = max(self._named_at, float(at))

    def _verdict(self, final: bool) -> str | None:
        if self.quiet is not None:
            why = self._quiet_now()
            if why:
                self._why = why if isinstance(why, str) else "walking"
                return "drop"
        h = self._heard
        if self.cue_only is not None and (h is None or not h["server"]) and self._cue_only_now():
            # a cue-only ceremony: whatever she was asked, by whatever name -- only a
            # server turn (a cue staff pressed, a scene's line) is hers to say
            self._why = "cue_only"
            return "drop"
        if self.follow_hold is not None and h is not None and not h["server"]:
            try:
                held = self.follow_hold()
            except Exception:  # noqa: BLE001 -- a probe that fails is the ordinary rules
                held = None
            if held == "drop":
                self._why = "cue_matched"
                return "drop"
            if held == "wait" and not final:
                return None
        if self.named_only is not None and h is not None and not h["server"] and self._named_only_now():
            if HER_NAME_ONLY.search(h["text"]) or self.named_recently():
                self._named_turn = True
                return "pass"
            if final or self._bytes >= NOISE_HOLD_BYTES:
                self._why = "not_named"
                return "drop"
            return None
        if (self.follow_hold is not None and (h is None or not h["server"]) and _ceremony_thai_ignored()
                and any(getattr(e, "kind", None) == "assistant_transcript" and _thai_only(getattr(e, "text", "") or "")
                        for e in self._held) and time.monotonic() - self._server_at > SERVER_TURN_S):
            # The belt: whatever let it through, a reply of hers that comes out in Thai is not played in the English-only
            # ceremony (robot 24 Nov 18:56: "ขออภัยค่ะ ดิฉันดูแลเฉพาะโครงการ ..." to somebody asking for a song).
            self._why = "thai_reply"
            return "drop"
        if (self.follow_hold is not None and h is None and _ceremony_name_first()
                and time.monotonic() - self._server_at > SERVER_TURN_S and not _ceremony_may_answer("")):
            # a reply to nothing anybody is known to have said: not his call
            self._why = "not_named"
            return "drop"
        if (self.follow_hold is not None and h is not None and not h["server"] and _thai_only(h["text"])
                and _ceremony_thai_ignored()):
            # The ceremony is English only, both ways (owner 24 Nov ~15:06): Thai words in the room -- the staff, a
            # guest -- are not hers to answer. A caption with any Latin letter is his and goes on as before.
            self._why = "thai_ignored"
            return "drop"
        if (self.follow_hold is not None and h is not None and not h["server"] and _ceremony_answers_all()
                and _ceremony_answer_named() and not _ceremony_may_answer(h["text"])):
            # (owner 24 Nov ~15:38: however long he talks, she answers off the script only when he calls her by name;
            # a matched question of the script was decided above -- its line plays, this reply is dropped anyway)
            if final or self._bytes >= NOISE_HOLD_BYTES:
                self._why = "not_named"
                return "drop"
            return None
        if (self.follow_hold is not None and h is not None and not h["server"]
                and (h["rms"] is None or h["rms"] >= CEREMONY_HOST_MIN_RMS) and _ceremony_answers_all()):
            # The host's collar mic in the ceremony's live window (reviews 24 Nov, both confirmed by repro): his
            # level is ~0.02-0.03, under NOISE_REPLY_NEAR_RMS 0.06, so a sentence the recogniser MISSED (an empty
            # transcript) and a short real question ("Why?", "Yes?", or a long one whose caption had only reached
            # "So," when her audio began) had her answer dropped as "noise" -- silence on stage. At his level her
            # reply passes; a sound under CEREMONY_HOST_MIN_RMS still meets the ordinary noise rule below.
            return "pass"
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
        if not self._output:
            self._answers = self._heard
        self._output = True
        if self._state == "pass":
            if self._named_turn:
                self._named_at = time.monotonic()
            return self._with_picture([event])
        if self._state == "drop":
            return []
        self._held.append(event)
        if event.kind == "audio":
            self._bytes += len(event.audio or b"")
        verdict = self._verdict(False)
        if verdict is None:
            return []
        self._state = verdict
        if verdict == "pass" and self._named_turn:
            self._named_at = time.monotonic()
        out, self._held = self._held, []
        if verdict != "pass":
            self._picture = None
            return []
        return self._with_picture(out)

    def close(self) -> tuple[list, dict | None]:
        """turn_complete: (events still owed, the dropped reply's facts or
        None). A turn that said something uses up what was heard; an empty
        one (3.8 completes before its tool call) leaves it for the reply."""
        state = self._state or (self._verdict(True) if self._output else "pass")
        held, output, why = self._held, self._output, self._why
        heard = self._answers if output else self._heard
        self.reset()
        # (only the words this reply answered are used up: somebody who spoke while it streamed is the next one's)
        if output and self._heard is self._answers:
            self._heard = None
        self._answers = None
        if state == "drop" and output:
            self._picture = None
            return [], {"rms": None if heard is None else heard["rms"],
                        "letters": 0 if heard is None else _letters(heard["text"]), "why": why}
        return self._with_picture(held), None
