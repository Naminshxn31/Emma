"""Staff press a cue on the event phone page, and Emma says that cue's approved line.

The owner's launch-ceremony script (6 Nov 2026: คุณมิกิ asks, Emma answers) is data in
app/event_script.py; this is the press. Two ways to say a line, chosen per cue:

* **recorded** -- `event_voice.pcm(cue)` has her own live voice saying it, rendered
  ahead and checked against the words, AND a person listened to that take
  (`approved_voice_sha256`, written by `approve_event_script.py --listened`):
  approval signs the words, not the audio -- a `--fresh` render after it would
  otherwise swap the stage audio unheard. The line goes out through the provider
  as if it were her reply (`provider.play_recorded`): nothing is left to a model,
  and the model's own output is held off the speaker while it plays.
* **read** -- no listened-to recording: the live model is asked to say it word for
  word (`event_script.read_text`, through `events.announce` like every server
  line), and what she actually said is scored against the line.

What it holds to:

* **Only an approved, clean cue is ever said** (`event_script.servable()`, the one
  gate). A draft, a line with a problem, a cue that has only a live brief: refused,
  nothing sent, the page says why.
* **Only on the robot face's live call.** No call = `no_call`, and nobody claims she
  spoke. A clip or a test walk keeping her quiet = `quiet`, the same.
* **Her current reply first.** A press waits (bounded) for the model to be idle and
  done streaming, no tool running (its result would reach the face mid-line), her
  voice played out; a newer press supersedes an older one still waiting; one cue
  pressed twice is one.
* **A cue the model asks for inside a tool call** (`by="model"`, the cue tool) is
  never announced -- the model is mid-turn. Recorded: the hold goes up at once, is
  kept up while the line waits, and the line follows the tool's result. No
  recording: the line goes back for the model to say itself, nothing else.
* **A cue line is her turn in the exchange with the host**: when it has played,
  the ceremony's "she was addressed" window runs from the end of its audio
  (`provider.mark_named`) -- otherwise the host's next unnamed line was refused.
* **The model learns of a recorded line beside tool results** (`last_said_note`,
  read by `event_host.scene_note`), not by a context note: that note is behind
  EVENT_CUE_CONTEXT_NOTE until measured live.
* **One turn-log row per press** (`event_cue`): what was pressed, how, how long to
  her first sound, how close she came. Never a guest's words.
* **A picture pressed with the line** (`show=<story id>`, owner 7 Nov: the location
  line with the map on the TV) goes up as THIS answer's screen, as if present_story
  had been called for it: the provider puts a `story_picture` into its own stream
  right before the line's first word (recorded: inside `play_recorded`, after the
  `interrupted` that stops an old reply; read: queued the instant the text is sent,
  behind everything already on its way), and the session draws it as a present_story
  result -- the face's rule "no picture = clear the old one at the answer's first
  sound" then finds the stage already this answer's, the story's clock hears the
  line from its first word, and the room screen gets its copy. The model never sees
  the payload (no tool ran); it learns the id beside later tool results
  (`last_said_note`). A picture that cannot go up never holds the line: the line
  plays and the answer's `show` says why. A live cue (`live_th`) gets no picture:
  its answer is the model's own reply to the host, at a time and in a turn the
  server does not open, so nothing can put a picture ahead of it as its own.

Nothing here has run on the robot yet.
"""
from __future__ import annotations

import asyncio
import logging
import time

from app import event_script, turnlog

logger = logging.getLogger("condo_voice.event_cues")

#: How long a press waits for her current reply to be over -- the model done
#: generating and her voice played out -- before it goes anyway (and stops her
#: on the page first, as a barge-in would). Staff pressed it: a long story
#: does not get to hold the ceremony up past this.
VOICE_WAIT_S = 15.0
#: ...and for the announcement lock (a scene's line being said, another cue):
#: past this the line has not started, and the log says so -- not "superseded".
LOCK_WAIT_S = 45.0
#: Read mode: her turn saying the line ends within this, or the log says so.
READ_TURN_S = 90.0
#: Read mode: her FIRST SOUND within this after the text went out, or the press
#: ends failed ("not_heard") and the line is free to press again. Review 7 Nov: a
#: reading that never came waited the whole READ_TURN_S, ended ok with no audio,
#: and the cue stayed "running" -- every press refused -- for 90 s. Her answers
#: start within 1.8 s in 95% of 801 robot turns (CLAUDE.md), and the robot's reply
#: watchdog nudges a silent turn at 8 s (REPLY_STALL_S): 12 s is past that nudge
#: and its answer, and soon enough for staff to press again while it matters.
READ_START_S = 12.0
#: After a recorded line has played out, the context note (EVENT_CUE_CONTEXT_NOTE)
#: waits at most this for the model's own (held) turn to end: client content sent
#: into a turn the model is still generating interrupts it.
NOTE_WAIT_S = 8.0
#: The staff page polls every second; the cue rows cost a read of every number
#: source (report chapters hashed) -- once per this, off the loop.
STATUS_TTL_S = 10.0
#: How long the "a recorded line was said" fact rides beside tool results.
SAID_NOTE_S = 180.0

#: EVENT_CUE_CONTEXT_NOTE only (off): what the model is told after a recorded line
#: went out whole, as context and not as a turn (`send_context_note`). The line is
#: quoted -- here that is the point: it is what she said, word for word.
NOTE_SAID = ("[คิวบทพิธี — ทีมงานกดเล่นเสียงที่อัดไว้ ไม่ใช่คำพูดของแขก ไม่ต้องตอบ] "
             "เมื่อสักครู่คุณพูดประโยคนี้ไปแล้วด้วยเสียงที่อัดไว้ ห้ามพูดซ้ำ: «%s»")
#: Beside tool results (event_host.scene_note): the cue's id and scene, never the
#: line -- a quoted sentence in an instruction is a sentence she says (CLAUDE.md).
SAID_FACT = "คิวบทพิธีที่ Emma เพิ่งพูดไปแล้วด้วยเสียงที่อัดไว้: %s (ฉาก %s)%s — ห้ามพูดซ้ำ"
#: ...and the story that went up with it, by id: never its lines (the model is not
#: to tell it -- it said the script line), so a later question knows what was shown.
WITH_PICTURE = " พร้อมภาพเรื่อง %s บนจอ"
#: A line the model read itself knows its own words; only the picture is news to it.
SHOWN_FACT = "คิวบทพิธีที่ Emma เพิ่งพูดไปเอง: %s" + WITH_PICTURE
#: Staff stopped a line the model was reading (`cut`): the model generated it whole
#: and believes it was said; the room heard only the start. By id, never the line.
STOPPED_FACT = "คิวบทพิธี %s ถูกทีมงานกดหยุดกลางคัน ส่วนที่เหลือไม่มีใครได้ยิน"

ERRORS_TH = {
    "no_cue": "ไม่พบคิวนี้ในบทพิธี",
    "live_only": "คิวนี้ไม่มีประโยคตายตัว — ให้คุณมิกิถาม Emma เอง",
    "problems": "บทนี้ผิดกฎเนื้อหา — ต้องแก้บทก่อน",
    "not_approved": "บทนี้ยังไม่อนุมัติ (ร่าง) — ยังพูดไม่ได้",
    "no_call": "ไม่มีสายเปิดที่หน้าหุ่น — Emma ยังไม่ได้พูด",
    "quiet": "Emma ถูกปิดเสียงอยู่ (คลิปกำลังเล่น หรือหุ่นเดินทดสอบ) — ยังไม่ได้พูด",
    "already": "คิวนี้กำลังรอหรือกำลังพูดอยู่แล้ว",
    "busy": "Emma กำลังพูดบทพิธีอื่นอยู่",
    "superseded": "มีคิวใหม่กดตามมา — คิวนี้ไม่ได้พูด",
    "offline": "หุ่นออฟไลน์อยู่และคิวนี้ไม่มีเสียงอัดที่ฟังอนุมัติแล้ว — Emma อ่านเองไม่ได้",
    "not_started": "Emma เริ่มบทนี้ไม่ได้ (รอคิวเสียงนานเกิน) — ยังไม่ได้พูด",
    "not_sent": "ส่งบทให้ Emma ไม่สำเร็จ (อาจได้ยินแค่บางส่วน)",
    "still_speaking": "Emma ยังพูดค้างอยู่ — รอให้จบก่อน",
    "not_heard": "ไม่ได้ยิน Emma อ่านบท",
    "stopped": "ทีมงานกดหยุด — Emma หยุดพูดบทนี้แล้ว",
    "no_player": "สายนี้เล่นเสียงอัดไม่ได้ — Emma พูดเอง",
    "error": "ผิดพลาด — Emma ยังไม่ได้พูด",
}

#: The answer's `show` when the picture pressed with a line did not go up. The line
#: is never held for it (the owner's ceremony goes on); this says what staff saw.
SHOW_ERRORS_TH = {
    "stories_off": "ภาพเรื่องเล่าปิดอยู่บนเครื่องนี้ — Emma พูดบทโดยไม่มีภาพ",
    "not_approved": "แคตตาล็อกเรื่องเล่ายังไม่อนุมัติ (หรือถูกแก้หลังอนุมัติ) — Emma พูดบทโดยไม่มีภาพ",
    "unknown_story": "ไม่มีเรื่องนี้ในแคตตาล็อกที่อนุมัติ — Emma พูดบทโดยไม่มีภาพ",
    "not_face": "สายนี้ไม่ใช่หน้าหุ่น — ขึ้นภาพไม่ได้ Emma พูดบทโดยไม่มีภาพ",
    "unsupported": "สายนี้ขึ้นภาพพร้อมบทไม่ได้ — Emma พูดบทโดยไม่มีภาพ",
    "model_turn": "ภาพขึ้นพร้อมบทได้เฉพาะคิวที่ทีมงานกด",
    "live_only": ("คิวนี้ Emma ตอบสดเอง ไม่มีบทให้ระบบพูด — ระบบไม่รู้ว่าคำตอบไหนของเธอคือคำตอบของคิวนี้ "
                  "จึงขึ้นภาพพร้อมคำตอบไม่ได้ (ภาพจะถูกล้างตอนคุณมิกิพูดหรือตอนเธอเริ่มตอบ)"),
    "not_said": "Emma ไม่ได้พูดบทนี้ — ภาพจึงไม่ขึ้น",
    "not_shown": "ขึ้นภาพไม่ทันก่อน Emma เริ่มพูด — Emma พูดบทโดยไม่มีภาพ",
    "error": "ขึ้นภาพไม่ได้ (ผิดพลาด) — Emma พูดบทโดยไม่มีภาพ",
}

#: `audible`: (provider, monotonic end, shown) of the last line's audio on the page -- a
#: line whose press is over may still be playing its last seconds (`cut`, `line_audible`),
#: and `shown` = its picture is up with it (the stop takes it down: review 7 Nov, A4).
#: `stopped`: a read line staff stopped ({"id", "at"}), for the model's note.
#: `cut_gen`: bumped by every `cut()` -- a press still in its first awaits, not in `runs`
#: yet, reads it at entry (`play`). `entering`: presses in those awaits now.
STATE: dict = {"seq": 0, "runs": {}, "last": {}, "status": None, "said": None, "shown": None,
               "stopped": None, "audible": None, "cut_gen": 0, "entering": 0,
               # the last two lines said whole, newest last ({"id", "at"}): the ceremony's live window shows her
               # their approved text (app/ceremony.py `live_extras`, owner 24 Nov "เห็นบทที่เพิ่งพูด")
               "said_log": [],
               # the cue a joke's chain presses next (app/event_order.py `_chain_watch`): read sooner by CHAIN_READ_LEAD_S
               "chained_next": None}
#: A joke's punchline read by the live model (owner 24 Nov ~15:45, rehearsal: "พยายามให้มันเป็นบทเดียวกันเลย ไม่งั้นมันจะเปิดให้
#: พูด"): the read waited out the set-up's last voice (1.5 s, the A3 tail) and THEN the model took ~1 s to its first word
#: -- a 2.5-3 s hole in the middle of a joke. The punchline's text goes in this much before the set-up's voice ends:
#: her ~1 s to first audio covers it, and her voice queues behind the set-up's on the robot (the hall holds its line
#: and slide until the voice before it has played there, voice-preview.html mirrorSubNext). Chained punchlines only.
CHAIN_READ_LEAD_S = 1.0
_tasks: set = set()


def reset() -> None:
    for task in list(_tasks):
        if not task.done():
            try:
                task.cancel()
            except RuntimeError:             # its loop has closed (a test before this one)
                pass
    _tasks.clear()
    global _status_refresh
    _status_refresh = None                   # (a refresh of another loop -- a test before this one -- is not ours)
    STATE.update(seq=0, runs={}, last={}, status=None, said=None, shown=None, stopped=None, audible=None,
                 cut_gen=0, entering=0, said_log=[], chained_next=None)


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


def cut_gen() -> int:
    """How many red stops so far (`cut`): a caller that read it before an await and
    finds it changed after was stopped meanwhile (app/event_order.py)."""
    return STATE["cut_gen"]


def _tail_on(provider, now: float) -> bool:
    """The last line's audio is still ahead on the page, on this provider."""
    tail = STATE.get("audible")
    return (tail is not None and provider is not None and tail[0] is provider
            and tail[1] + AUDIBLE_SLACK_S > now)


def line_audible(now: float | None = None) -> bool:
    """A ceremony line of hers is under way or still playing: a press waiting or
    speaking, or the last line's audio still ahead on the live call's page. Review
    7 Nov (A3): a line the model reads ends at Gemini's turn_complete, with up to
    ~20 s of her voice still to play -- the running order keeps its step busy on
    this. Her film after a line is not her voice (event_clips has its own state)."""
    now = time.monotonic() if now is None else now
    if STATE["entering"] or any(r.get("phase") in ("waiting", "speaking") and not r.get("ended")
                                and not r.get("rolling") for r in STATE["runs"].values()):
        return True
    live = _live_call()
    return live is not None and _tail_on(getattr(live, "provider", None), now)


def last_said_note() -> str | None:
    """For event_host.scene_note: the recorded line said a moment ago, by id -- and
    the story pressed with a line (either mode), by id."""
    now = time.monotonic()
    notes = []
    said = STATE.get("said")
    if said and now - said["at"] <= SAID_NOTE_S:
        extra = (" ถูกพูดแทรกก่อนจบ" if said.get("cut") else "") + (
            WITH_PICTURE % said["show"] if said.get("show") else "")
        notes.append(SAID_FACT % (said["id"], said.get("scene_th") or "-", extra))
    shown = STATE.get("shown")
    if shown and now - shown["at"] <= SAID_NOTE_S:
        notes.append(SHOWN_FACT % (shown["id"], shown["topic"]))
    stopped = STATE.get("stopped")
    if stopped and now - stopped["at"] <= SAID_NOTE_S:
        notes.append(STOPPED_FACT % stopped["id"])
    return " · ".join(notes) or None


# ---- which cue, which call ------------------------------------------------------------

def _pick(cue_id: str) -> tuple[dict | None, str | None]:
    """(cue, None) when it may be said now, else (cue or None, why not).
    `servable()` decides; the rest only names the reason for the page."""
    doc = event_script.load()
    c = event_script.cue(cue_id, doc) if cue_id else None
    if c is None:
        if cue_id and cue_id.startswith("x_"):
            # a line of the ceremony's extras (app/ceremony_extra.py): its own file, the same signatures
            from app import ceremony_extra

            return ceremony_extra.pick(cue_id)
        return None, "no_cue"
    served = event_script.servable(doc).get(cue_id)
    if served is None:
        if not c.get("th") and c.get("live_th"):
            return c, "live_only"
        return c, ("problems" if event_script._problems_in(c, event_script.speak(doc)) else "not_approved")
    if not served.get("th"):
        return served, "live_only"           # approved, but a brief for the live model, not words
    # `speak`: "en" (owner 17 Nov): the English is the line, the Thai the screen's subtitle
    return event_script.spoken(served, doc), None


def _live_call():
    """The robot face's live call, or None."""
    from app import robot_face_link
    from app import session as session_module

    session = session_module._active
    if session is None or getattr(session, "provider", None) is None:
        return None
    return session if robot_face_link.is_robot_face(session) else None


def _quiet() -> str | None:
    """Her voice is being kept in (a clip on the big screen, a test walk): the
    reply gate would drop the line as it drops a text turn's reply."""
    try:
        from app.providers.gemini import _walk_quiet

        why = _walk_quiet()
    except Exception:                                    # noqa: BLE001 -- a probe that fails is "not quiet"
        return None
    if not why:
        return None
    return why if isinstance(why, str) else "walking"


def _offline(provider) -> bool:
    return getattr(provider, "model", None) == "offline"


def _voice():
    """app/event_voice.py, written alongside; absent = no recordings at all."""
    try:
        from app import event_voice
    except ImportError:
        return None
    return event_voice


def _lang(c: dict) -> str:
    """The language this cue is said in: the picked cue's own (`event_script.spoken`), else
    the file's."""
    return c.get("lang") or event_script.speak()


#: Her reading pace in English, characters a second (the 29 preview takes of 17 Nov:
#: 3,560 characters in 253 s). Only the room screen's first guess for a line the live
#: model reads; the page corrects it from the voice it plays.
EN_CHARS_PER_S = 14.0


def _slide(c: dict) -> dict | None:
    """The cue's motion slide for the room screen, or None. Whatever goes wrong with a
    slide, the line's subtitle still goes out. A scene's own line (`play_line`) brings its slide
    built already (card 2's opener: `ceremony_slides.opener_slide`) -- never looked up by its id."""
    if "slide" in c:
        return c["slide"] if isinstance(c["slide"], dict) else None
    try:
        from app import ceremony_slides
        return ceremony_slides.for_cue(c.get("id"), lang=_lang(c))
    except Exception:
        logger.exception("event cue %s: its slide", c.get("id"))
        return None


def _subtitle(run: dict, seconds: float | None = None) -> None:
    """The room screen's Thai line under an English reading (owner 17 Nov). Only a cue
    that carries one (`event_script.spoken`: `sub_th`): a line said in Thai has none --
    the room hears those words."""
    c = run["cue"]
    sub = c.get("sub_th")
    if not isinstance(sub, str) or not sub.strip():
        return
    try:
        from app import face_mirror
        if seconds is None:
            seconds = len(c.get("th") or "") / EN_CHARS_PER_S
        line = {"id": c["id"], "text": sub, "scene": c.get("scene_th"), "seconds": seconds}
        # the line's motion slide rides with it (owner 17 Nov) -- only from a signed slides
        # file, only a slide that passes its checks; none = the plain backdrop, as before
        slide = _slide(c)
        if slide is not None:
            line["slide"] = slide
        face_mirror.subtitle(line)
        run["subtitled"] = True
    except Exception:
        logger.exception("event cue %s: its subtitle", run.get("id"))


def _voice_ahead(run: dict, pcm: bytes | None) -> None:
    """A recorded line about to play: the room screens get its voice whole at its first word
    (face_mirror.ahead -- the hall hears it through a Wi-Fi gap that the robot's 2 s lead does
    not cover, 24 Nov). Right before the provider's push, both roads; never in the way of the line."""
    if not pcm:
        return
    try:
        from app import face_mirror
        face_mirror.ahead(run["id"], pcm)
    except Exception:
        logger.exception("event cue %s: its voice for the room screens", run.get("id"))


def _voice_gone(run: dict) -> None:
    """The line did not play out on the robot: what the room screens hold of it goes too."""
    try:
        from app import face_mirror
        face_mirror.ahead_gone(run["id"])
    except Exception:
        logger.exception("event cue %s: its voice off the room screens", run.get("id"))


def _clip_sha(voice, c: dict) -> str | None:
    clip_sha = getattr(voice, "clip_sha", None)
    return clip_sha(c, _lang(c)) if callable(clip_sha) else None


async def _recording(c: dict) -> tuple[bytes | None, str | None]:
    """(PCM of the take a person listened to, None) or (None, why not -- None when
    there is simply no recording)."""
    voice = _voice()
    if voice is None:
        return None, None
    try:
        sha = await asyncio.to_thread(_clip_sha, voice, c)
        if sha is None:
            return None, None
        if sha != c.get("approved_voice_sha256"):
            # the words are signed, this take is not: read live, never an unheard take
            return None, "voice_not_approved"
        pcm = await asyncio.to_thread(lambda: voice.pcm(c, _lang(c), sha=sha))
    except Exception:                                    # noqa: BLE001 -- no recording, then
        logger.exception("event cue %s: its recording could not be read", c.get("id"))
        return None, "voice_error"
    if not isinstance(pcm, (bytes, bytearray)) or len(pcm) < 2:
        return None, "voice_changed"                     # swapped between the look and the read
    return bytes(pcm[:len(pcm) // 2 * 2]), None


def _voice_status(c: dict) -> dict:
    voice = _voice()
    if voice is None or not c.get("th"):
        return {"recorded": False}
    try:
        got = voice.status(c, _lang(c)) if _lang(c) != "th" else voice.status(c)
        got = dict(got) if isinstance(got, dict) else {"recorded": False}
        sha = _clip_sha(voice, c) if got.get("recorded") else None
    except Exception:                                    # noqa: BLE001
        logger.exception("event cue %s: recording status", c.get("id"))
        return {"recorded": False}
    got["approved"] = bool(sha) and sha == c.get("approved_voice_sha256")
    return got


async def _picture_for(run: dict, session, provider) -> str | None:
    """The picture pressed with this line: why it cannot go up, or None (then
    `run["picture"]` is what the provider puts ahead of the line's first word --
    the id only: the session builds the payload in the call's own context)."""
    if run["by"] == "model":
        return "model_turn"                  # inside the model's own tool call: its turn, not ours
    if not getattr(session, "_robot_face_claim", False):
        return "not_face"                    # nothing draws a story but the robot face (and its copy)
    if not getattr(provider, "shows_pictures", False):
        return "unsupported"
    from app import stories

    try:
        got = await asyncio.to_thread(lambda: stories.picture(run["show"], taps=False))
    except Exception:                                    # noqa: BLE001 -- the line plays bare
        logger.exception("event cue %s: its picture %s", run["id"], run["show"])
        return "error"
    if not got.get("ok"):
        why = str(got.get("error") or "error")
        return why if why in SHOW_ERRORS_TH else "error"
    run["picture"] = {"topic": got["topic"], "cue": run["cue"]["id"], "by": "cue"}
    return None


def _with_show(run: dict | None, result: dict) -> dict:
    """The answer, with what became of the picture when one was pressed with it."""
    if not run or run.get("show") is None or "show" in result:
        return result
    if run.get("shown"):
        show = {"ok": True, "topic": run["picture"]["topic"]}
    else:
        code = run.get("show_error") or "not_said"
        show = {"ok": False, "error": code, "error_th": SHOW_ERRORS_TH.get(code, SHOW_ERRORS_TH["error"])}
    return {**result, "show": show}


# ---- the press ------------------------------------------------------------------------

def _running(cue_id: str) -> bool:
    return any(r["id"] == cue_id for r in STATE["runs"].values())


def _speaking() -> bool:
    return any(r.get("phase") == "speaking" for r in STATE["runs"].values())


async def play(cue_id: str, *, by: str = "staff", show: str | None = None) -> dict:
    """Say the cue's approved line. `by="staff"` (the phone page): answers once she
    has started the line or it was refused/superseded. `by="model"` (the cue tool,
    inside a tool call): answers at once and never announces anything.

    `show` = an approved story's id to put up on the robot face and the room screen
    with the line, as that answer's picture (see the module's note). Given, the
    answer carries `show`: {"ok": True, "topic": id} once it is on its way ahead of
    her first word, else {"ok": False, "error": code, "error_th": ...} -- and the
    line plays all the same."""
    pressed = time.monotonic()
    # Read before the first await (review 7 Nov, A2): a red stop while `_pick` runs found
    # no run to stop, answered False, and the line played after it. A stop since = stopped.
    gen = STATE["cut_gen"]
    by = by if by in ("staff", "model", "scene") else "staff"
    cue_id = cue_id.strip() if isinstance(cue_id, str) else ""
    STATE["entering"] += 1
    try:
        c, why = await asyncio.to_thread(_pick, cue_id)
    finally:
        STATE["entering"] -= 1
    run = {"seq": None, "id": (c or {}).get("id") or cue_id[:48], "by": by, "mode": None,
           "pressed": pressed, "phase": None, "known": c is not None, "cut_gen": gen,
           "chained": bool(STATE.get("chained_next")) and STATE.get("chained_next") == cue_id}
    if run["chained"]:
        STATE["chained_next"] = None
    if show is not None and show != "":
        # requested: the answer says what became of it, whatever happens below
        run.update(show=show.strip()[:64] if isinstance(show, str) else "?", shown=False)
        if why == "live_only":
            run["show_error"] = "live_only"
    if why:
        return _end(run, error=why)
    if STATE["cut_gen"] != gen:
        run["stopped"] = "staff"
        return _end(run, error="stopped")       # the red stop came while the cue was looked up
    session = _live_call()
    if session is None:
        return _end(run, error="no_call")
    quiet = _quiet()
    if quiet:
        run["quiet"] = quiet
        return _end(run, error="quiet")
    if _running(c["id"]):
        return _end(run, error="already")       # a double tap is one line, not two
    if by == "model" and _speaking():
        # the hold a recorded line raises would cut the one being said now
        return _end(run, error="busy")
    provider = session.provider
    # Registered before the first await: a double tap landing while the recording
    # is read must find this press, or both would pass the check above.
    STATE["seq"] += 1
    run.update(seq=STATE["seq"], session=session, provider=provider, cue=c, phase="waiting", report={})
    STATE["runs"][run["seq"]] = run
    pcm, voice_why = await _recording(c)
    if voice_why:
        run["voice_why"] = voice_why
    recorded = bool(pcm) and bool(getattr(provider, "plays_recorded", False))
    if run.get("show") is not None:
        run["show_error"] = await _picture_for(run, session, provider)
    if run.get("stopped") or STATE["cut_gen"] != gen:
        run["stopped"] = run.get("stopped") or "staff"
        return _end(run, error="stopped")       # the red stop came while its recording was read
    if by == "model":
        if not recorded:
            run["mode"] = "say_yourself"
            _end(run, ok=True)
            return _with_show(run, {"ok": True, "mode": "say_yourself", "line": c["th"]})
        run["mode"] = "recorded"
        run["pcm"] = pcm                     # (its subtitle's true length, its voice whole to the room screens)
        # called HERE, inside the tool call: the hold on the model's own answer to
        # this very call is up before its result can go back (providers/gemini.py)
        coro = provider.play_recorded(c["th"], pcm, lang=c.get("lang") or "th", wait_tools=True,
                                      report=run["report"])
        run["task"] = _spawn(_guarded(run, _recorded_line(run, coro=coro)))
        return _with_show(run, {"ok": True, "mode": "recorded"})
    if not recorded and _offline(provider):
        return _end(run, error="offline")
    run.update(mode="recorded" if recorded else "read",
               started=asyncio.get_running_loop().create_future())
    run["task"] = _spawn(_guarded(run, _recorded_line(run, pcm=pcm) if recorded else _read_line(run)))
    return await asyncio.shield(run["started"])


async def play_line(line_id: str, text: str, pcm: bytes, *, lang: str = "en",
                    scene_th: str | None = None, still=None, sub_th: str | None = None,
                    slide: dict | None = None) -> dict:
    """A scene's own line recorded ahead (app/event_host.py `recorded`; owner 10 Nov 2026: the
    debut's English pageant line in the voice the owner picked by ear -- "Jessica v3 รอบแรก (C)
    ชอบเสียงนี้"). Not a cue of the ceremony script: the scene pins the file's sha256 and
    event_host reads and checks it. From here on the road of a staff press of a recorded cue
    (`_recorded_line`): her current reply first, the announcement lock, the red stop (`cut`),
    the named window, `said` beside later tool results (`last_said_note`, by id). Answers once
    her first word is out, or why not -- `play`'s answers, `by="scene"`; `no_player` = this
    call cannot play a file (offline, or a provider without recorded lines).

    `still` (the scene press's own): while the line waits its turn it is asked again, as
    `events.announce` asks a model's line -- another scene pressed meanwhile makes it
    `superseded` (review 10 Nov: the welcome played around the next scene's line). The same
    line pressed again replaces one still waiting for a press that is gone, instead of
    answering `already` (both presses lost it); once it is speaking it plays out."""
    pressed = time.monotonic()
    gen = STATE["cut_gen"]
    c = {"id": str(line_id or "")[:48], "th": " ".join(str(text or "").split()), "lang": lang,
         "scene_th": scene_th}
    if isinstance(sub_th, str) and sub_th.strip():
        # the hall screen's Thai line under an English recording (card 2's opener, owner 25 Nov): `_subtitle`
        c["sub_th"] = " ".join(sub_th.split())
    # its hall-screen film, built by the caller from a signed slide (card 2's opener); none = no slide, never the
    # slide some cue of the script that happens to share the id would have
    c["slide"] = slide if isinstance(slide, dict) else None
    run = {"seq": None, "id": c["id"], "by": "scene", "mode": None, "pressed": pressed, "phase": None,
           "known": True, "cut_gen": gen}
    if not c["id"] or not c["th"] or not pcm:
        return _end(run, error="error")
    session = _live_call()
    if session is None:
        return _end(run, error="no_call")
    quiet = _quiet()
    if quiet:
        run["quiet"] = quiet
        return _end(run, error="quiet")
    for old in list(STATE["runs"].values()):
        if (old["id"] == c["id"] and old.get("phase") == "waiting" and callable(old.get("still"))
                and not _asks_still(old)):
            task = old.get("task")
            _end(old, error="superseded")
            if task is not None:
                task.cancel()
    if _running(c["id"]):
        return _end(run, error="already")
    provider = session.provider
    if not getattr(provider, "plays_recorded", False) or _offline(provider):
        return _end(run, error="no_player")
    run["still"] = still
    STATE["seq"] += 1
    run.update(seq=STATE["seq"], session=session, provider=provider, cue=c, phase="waiting", report={},
               mode="recorded", started=asyncio.get_running_loop().create_future())
    STATE["runs"][run["seq"]] = run
    run["task"] = _spawn(_guarded(run, _recorded_line(run, pcm=bytes(pcm))))
    return await asyncio.shield(run["started"])


async def _guarded(run: dict, coro) -> None:
    """Every road out of a press ends it: the page gets an answer, the log a row."""
    try:
        await coro
    except asyncio.CancelledError:
        _end(run, error="stopped" if run.get("stopped") else "error")    # `cut` cancels a waiting press
        raise
    except Exception:                                    # noqa: BLE001 -- the page gets an answer
        logger.exception("event cue %s", run["id"])
        _end(run, error="error")


def _still_ours(run: dict) -> str | None:
    from app import session as session_module

    if run.get("stopped") or run.get("cut_gen", STATE["cut_gen"]) != STATE["cut_gen"]:
        return "stopped"
    if STATE["seq"] != run["seq"]:
        return "superseded"
    if callable(run.get("still")) and not _asks_still(run):
        return "superseded"                 # a scene's line whose press is gone (play_line)
    session = run["session"]
    if session_module._active is not session or getattr(session, "provider", None) is not run["provider"]:
        return "no_call"
    return None


def _asks_still(run: dict) -> bool:
    try:
        return bool(run["still"]())
    except Exception:                                    # noqa: BLE001 -- a probe that fails: not ours any more
        return False


def _audible(session, now: float) -> bool:
    until = getattr(session, "_array_audible_until", None)
    return bool(callable(until) and until(now) > now)


def _asks(provider, name: str) -> bool:
    probe = getattr(provider, name, None)
    try:
        return bool(callable(probe) and probe())
    except Exception:                                    # noqa: BLE001 -- a probe that fails is "no"
        return False


async def _until_quiet(run: dict, *, wait_model: bool, owed: bool = False) -> str | None:
    """Until her reply is over -- the model idle and done streaming, no tool of its
    running (a tool_result would reach the face mid-line), her voice played out --
    bounded by VOICE_WAIT_S. `wait_model=False` for the cue tool: the model's turn
    IS that tool call, and its answer is held. `owed` (read mode): also an answer
    the model is still working out with nothing out yet (`_model_busy`). A reason to
    give up, or None."""
    session, provider = run["session"], run["provider"]
    deadline = time.monotonic() + VOICE_WAIT_S
    while True:
        why = _still_ours(run)
        if why:
            return why
        now = time.monotonic()
        idle = getattr(session, "turn_idle", None)
        if now < STATE.get("preempt_live_until", 0.0) and not _asks(provider, "tools_pending"):
            # follow mode matched the host's scripted line (app/ceremony_follow.py, owner 24 Nov): a live reply of hers
            # that is still out is what this line replaces -- the recording cuts it (play_recorded purges the model's
            # queued output, `interrupt_first` stops what the page plays) instead of waiting behind it, up to VOICE_WAIT_S
            return None
        busy = (_asks(provider, "tools_pending") or _audible(session, now)
                or (wait_model and ((idle is not None and not idle.is_set())
                                    or _asks(provider, "model_streaming")))
                or (owed and (_model_busy(session, provider, now) is not None
                              # the last line's voice still ahead (A3): a reading sent
                              # now would put its picture up over that tail
                              or _tail_on(provider, now + (CHAIN_READ_LEAD_S if run.get("chained") else 0.0)))))
        if not busy or now >= deadline:
            return None
        await asyncio.sleep(0.1)


def _model_busy(session, provider, now: float) -> str | None:
    """The model has a turn in flight, so a text sent now lands ON it (read mode).
    Gemini then interrupts that turn, and its `interrupted` -- and any of its output
    already on the way -- arrives AFTER the text: the reading's tap took it for her
    line being cut (review 7 Nov: the press ended at once "ok", the next line could
    go over hers, and the picture queued at the send was drawn under the old
    answer's audio). Streaming output, a tool of its running, the turn an
    announcement opened not over, or the guest's (host's) words just ended with no
    answer out yet -- the ~1.2 s the model works before its first sound, which
    "streaming" cannot see."""
    if _asks(provider, "tools_pending"):
        return "tool"
    if _asks(provider, "model_streaming"):
        return "streaming"
    idle = getattr(session, "turn_idle", None)
    if idle is not None and not idle.is_set():
        return "turn"
    owed = getattr(session, "_reply_owed", None)
    try:
        if callable(owed) and owed(now):
            return "reply_owed"
    except Exception:                                    # noqa: BLE001 -- a probe that fails is "no"
        pass
    return None


def _speaks_for_her(session) -> None:
    """What `events.announce` does for a server line: the pending answer is not
    hers to follow up any more (FOLLOW_UP_AFTER_S), and the guest's last end
    signal does not time this turn (metrics)."""
    void = getattr(session, "_void_follow_up", None)
    if callable(void):
        void()
    try:
        observer = session.metrics
    except Exception:                                    # noqa: BLE001
        observer = None
    if observer is not None and hasattr(observer, "robot_turn"):
        observer.robot_turn()


def _why_not(report: dict) -> str:
    why = str(report.get("why") or "")
    if why.startswith("quiet"):
        return "quiet"
    if why == "closing":
        return "no_call"
    if why in ("replaced", "ended"):
        return "superseded"
    if why == "hold_lapsed":
        return "not_started"
    return "not_sent"


async def _keep_hold(provider, report: dict) -> None:
    """The cue tool's hold, kept up while its line waits (her voice, the lock):
    bounded from each refresh, so a press that died lets it lapse."""
    while True:
        await asyncio.sleep(1.0)
        refresh = getattr(provider, "refresh_recorded", None)
        if not callable(refresh) or not refresh(report):
            return


def _named(run: dict, audio_end: float) -> None:
    """Her line was her turn in the exchange: the ceremony's "addressed" window
    runs from the end of its audio (review 6 Nov: the host's "ว้าว คุณฉลาดมาก"
    after a cue was refused as somebody speaking to the room)."""
    now = time.monotonic()
    until = getattr(run["session"], "_array_audible_until", None)
    at = max(audio_end, until(now) if callable(until) else now, now)
    mark = getattr(run["provider"], "mark_named", None)
    if callable(mark):
        mark(at)


async def _recorded_line(run: dict, *, pcm: bytes | None = None, coro=None) -> None:
    """Both roads of a recorded line: a staff press (`pcm`) and the cue tool (`coro`,
    its hold already up)."""
    from app import events

    session, provider, c = run["session"], run["provider"], run["cue"]
    report = run["report"]
    idle = getattr(session, "turn_idle", None)
    keep = _spawn(_keep_hold(provider, report)) if coro is not None else None
    cleared = locked = False
    task = None
    whole = False
    try:
        why = await _until_quiet(run, wait_model=coro is None)
        if why:
            _end(run, error=why)
            return
        # The announcement lock for the whole line: a scene's line or a follow-up
        # sent while it plays would be answered into the hold. Bounded.
        try:
            await asyncio.wait_for(events._lock.acquire(), LOCK_WAIT_S)
            locked = True
        except asyncio.TimeoutError:
            run["why"] = "lock_timeout"
            _end(run, error="not_started")
            return
        why = _still_ours(run) or ("quiet" if _quiet() else None)
        if why:
            _end(run, error=why)
            return
        if coro is not None and not getattr(provider, "refresh_recorded", lambda r: True)(report):
            run["why"] = report.get("why") or "hold_lapsed"
            _end(run, error="superseded" if run["why"] == "replaced" else "not_started")
            return
        if idle is not None:
            idle.clear()                     # a line of hers is under way
            cleared = True
        _speaks_for_her(session)
        run["phase"] = "speaking"
        run["waited_s"] = round(time.monotonic() - run["pressed"], 1)
        # both roads of a recording (a press's `pcm`, the cue tool's `coro` -- its pcm on the run)
        voice = pcm if pcm is not None else run.get("pcm")
        _subtitle(run, len(voice) / 2 / 24000 if voice else None)
        if coro is None:
            # the picture rides INSIDE the line (shows_pictures): put out by the
            # provider after any `interrupted` and before the first word
            extra = {"picture": run["picture"]} if run.get("picture") is not None else {}
            coro = provider.play_recorded(c["th"], pcm, lang=c.get("lang") or "th", report=report,
                                          interrupt_first=_audible(session, time.monotonic()), **extra)
        _voice_ahead(run, voice)
        task = asyncio.ensure_future(coro)
        if keep is not None:
            keep.cancel()
        while not task.done() and "first_at" not in report:
            if report.get("picture"):
                run["shown"] = True          # up on the page already: a stop takes it down (A4)
            await asyncio.sleep(0.02)
        if report.get("picture"):
            run["shown"] = True
        if "first_at" in report:
            _resolve(run, {"ok": True, "mode": "recorded"})
        whole = await task
        if "first_at" not in report:
            run["why"] = report.get("why")
            _end(run, error="stopped" if run.get("stopped") else _why_not(report))
            return
        _heard_recorded(run, report)
        # its last seconds may still be playing after the press is over (`cut`) -- and
        # its picture with them: the stop in that window takes it down too (A4)
        STATE["audible"] = (provider, report["first_at"] + float(report.get("sent_s") or 0.0),
                            bool(run.get("shown")) and not run.get("stopped"))
        _named(run, report["first_at"] + float(report.get("sent_s") or 0.0))
        if not whole and report.get("why") in ("put_timeout", "closing"):
            # the page stopped taking it: a fragment went out -- said so, never "ok"
            run.update(partial=True, why=report["why"])
            _end(run, error="no_call" if report["why"] == "closing" else "not_sent")
            return
        stopped = bool(run.get("stopped"))
        # stopped by staff: the room did not hear the rest, and `cut` took its picture down
        STATE["said"] = {"id": c["id"], "scene_th": c.get("scene_th"), "cut": not whole or stopped,
                         "show": run["picture"]["topic"] if run.get("shown") and not stopped else None,
                         "at": time.monotonic()}
        if whole and not stopped:
            STATE["said_log"] = (STATE.get("said_log") or [])[-1:] + [{"id": c["id"], "at": time.monotonic()}]
            await _note(run)
    finally:
        if keep is not None:
            keep.cancel()
        if task is None and coro is not None:
            coro.close()
            # the hold went up at the press: a line that will not play lowers it
            ender = getattr(provider, "end_recorded", None)
            if callable(ender):
                ender(report)
        elif task is not None and not task.done():
            task.cancel()                    # cancelled while waiting for its first chunk
        if task is not None and not whole:
            _voice_gone(run)                 # the room screens hold all of it: what the robot never said stops there
        if cleared and idle is not None:
            idle.set()                       # announcements may go again, whatever happened
        if locked:
            events._lock.release()
    if run.get("stopped"):
        run["cut"] = True
        _end(run, error="stopped")              # no film after a line staff stopped
        return
    if whole and c.get("clip"):
        await _clip(run)
    _end(run, ok=True)


def _heard_recorded(run: dict, report: dict) -> None:
    run["ms"] = round((report["first_at"] - run["pressed"]) * 1000)
    run["seconds"] = report.get("sent_s")
    run["cut"] = bool(report.get("cut"))
    for key in ("model_held_ms", "purged_ms"):
        if report.get(key):
            run[key] = report[key]


async def _note(run: dict) -> None:
    """EVENT_CUE_CONTEXT_NOTE (off): once the line has played out and the model's
    own turn is over, tell the model what she said (a context note, not a turn).
    Off by default: never measured live, and on 3.8 a turn_complete=False note may
    hold the host's next reply -- nothing releases it on a PC, on the robot only
    the watchdog's nudge (in a ceremony only when she was named). The fact rides
    beside tool results instead (`last_said_note`)."""
    from app import session as session_module
    from app.config import settings

    if not settings.event_cue_context_note:
        return
    session, provider = run["session"], run["provider"]
    deadline = time.monotonic() + NOTE_WAIT_S + float(run.get("seconds") or 0.0)
    while time.monotonic() < deadline and (_audible(session, time.monotonic())
                                           or _asks(provider, "model_streaming")):
        await asyncio.sleep(0.1)
    if (session_module._active is not session or session.provider is not provider
            or _asks(provider, "model_streaming")):
        run["noted"] = False
        return
    try:
        run["noted"] = bool(await provider.send_context_note(NOTE_SAID % run["cue"]["th"]))
    except Exception:                                    # noqa: BLE001 -- context, not the line
        logger.exception("event cue %s: the note after the line", run["id"])
        run["noted"] = False


async def _read_line(run: dict) -> None:
    """No listened-to recording: the live model says it. What she says in that turn
    is collected for the log's `fidelity` -- the owner's question (recorded lines or
    the live model) is answered by these rows.

    Two rules from the review of 7 Nov. The text never lands on a turn of the
    model's still in flight (`_model_busy`, asked at the send): refused
    "still_speaking" -- its late `interrupted` and its output would be taken for her
    line. And the press is "ok" only once her VOICE of it is out: no first sound
    within READ_START_S = "not_heard", the line free again at once (a reading that
    never came used to end ok, after 90 s of "running")."""
    from app import events, speech_match
    from app import session as session_module

    why = await _until_quiet(run, wait_model=True, owed=True)
    if why:
        _end(run, error=why)
        return
    session, provider, c = run["session"], run["provider"], run["cue"]
    heard = {"text": "", "audio": 0, "first_at": None, "cut": False, "stale": 0}
    started = asyncio.Event()                # her first sound -- or her turn over without one
    done = asyncio.Event()
    untap = [None]
    unquote = getattr(provider, "unquote_next_reply", None)

    def wake() -> None:
        started.set()
        done.set()

    def on_event(event) -> None:
        if event.kind == "audio" and event.audio:
            if heard["first_at"] is None:
                heard["first_at"] = time.monotonic()
                started.set()
            heard["audio"] += len(event.audio)
        elif event.kind == "assistant_transcript":
            heard["text"] += event.text or ""
        elif event.kind == "interrupted":
            if heard["first_at"] is None and not heard["text"].strip():
                # Nothing of her reading yet: the turn our text landed on, ending
                # (Gemini cuts a turn in flight when client content arrives, and
                # says so after the text). Not her line being cut. `_model_busy`
                # keeps the text off a turn with output out; this is for one that
                # began, silent, in between.
                heard["stale"] += 1
                return
            heard["cut"] = True
            wake()
        elif event.kind == "turn_complete" and (heard["audio"] or heard["text"].strip()):
            wake()                       # (3.8's empty turn end before a tool call is not it)

    def still() -> bool:
        # asked by announce AFTER its waits, right before it sends: the moment
        # her turn for this line starts, and the last word on superseding it
        if run.get("stopped") or run["cut_gen"] != STATE["cut_gen"]:
            run["stopped"] = run.get("stopped") or "staff"
            return False
        if STATE["seq"] != run["seq"]:
            run["superseded"] = True
            return False
        now = time.monotonic()
        if _tail_on(provider, now + (CHAIN_READ_LEAD_S if run.get("chained") else 0.0)):
            # Review 7 Nov (A3): the line before ended at Gemini's turn_complete with her
            # voice of it still to play; this reading and its picture would go up over
            # that tail. `_until_quiet` waited for it, bounded; still there = not now.
            run["busy"] = "tail"
            return False
        busy = _model_busy(session, provider, now)
        if busy is not None and busy != "reply_owed":
            # (an answer still owed with nothing out was waited for above, bounded;
            # what is left of it is the silent case the tap's stale rule covers --
            # refusing on it would refuse every press while the host talks)
            run["busy"] = busy
            return False
        tap = getattr(provider, "tap_output", None)
        untap[0] = tap(on_event) if callable(tap) else None
        # READ_RULE quotes the line in « », and the transcript of her reading may
        # carry the marks; on the robot that text is the caption bubble
        if callable(unquote):
            unquote()
        run["phase"] = "speaking"
        run["waited_s"] = round(time.monotonic() - run["pressed"], 1)
        return True

    try:
        # The line's own language by definition (Thai, or English in a file whose `speak`
        # is "en"): the read rule names it and says "never translate", so the menu's held
        # language ("answer only in X") on top would be two orders in one turn
        sent = await events.announce(event_script.read_text(c, c.get("lang") or "th"), source="event_cue",
                                     still_relevant=still, held_language=False)
    except BaseException:
        if untap[0] is not None:
            untap[0]()
        if callable(unquote):
            unquote(False)
        raise
    if not sent:
        if untap[0] is not None:
            untap[0]()
        if callable(unquote):
            unquote(False)                   # armed for a turn that never went out
        gone = session_module._active is not session
        if run.get("busy"):
            run["why"] = run["busy"]
        _end(run, error="stopped" if run.get("stopped") else "still_speaking" if run.get("busy") else
             "superseded" if run.get("superseded") else ("no_call" if gone else "not_sent"))
        return
    if not run.get("stopped"):
        _subtitle(run)
    if run.get("picture") is not None and not run.get("stopped"):
        # The text is out and NOTHING has yielded since announce sent it: her reply
        # cannot be in the provider's queue yet, and whatever is (an old reply's
        # tail, the `interrupted` that cuts it) goes to the page before the picture.
        # Not before the send: a text that never went out must not leave a picture
        # up with no line.
        queue = getattr(provider, "queue_picture", None)
        run["shown"] = bool(callable(queue) and queue(run["picture"]))
        if run["shown"]:
            STATE["shown"] = {"id": c["id"], "topic": run["picture"]["topic"], "at": time.monotonic()}
        else:
            run["show_error"] = "not_shown"
    if untap[0] is None:
        # this provider cannot show her voice: said, not measured (no provider the
        # robot runs is like this -- Gemini taps)
        _resolve(run, {"ok": True, "mode": "read"})
        run.update(fidelity=None, why="no_tap")
        _end(run, ok=True)
        return
    run["wake"] = wake                       # `cut`: stop waiting for her
    sent_at = time.monotonic()
    try:
        if not run.get("stopped"):
            try:
                await asyncio.wait_for(started.wait(), min(READ_START_S, READ_TURN_S))
            except asyncio.TimeoutError:
                pass
        if heard["first_at"] is not None and not run.get("stopped"):
            _resolve(run, {"ok": True, "mode": "read"})        # her voice of it is out
            try:
                await asyncio.wait_for(done.wait(), max(0.0, READ_TURN_S - (time.monotonic() - sent_at)))
            except asyncio.TimeoutError:
                run["timeout"] = True
    finally:
        run.pop("wake", None)
        untap[0]()
        if callable(unquote):
            unquote(False)
    if heard["stale"]:
        run["stale_interrupts"] = heard["stale"]
    rate = getattr(provider, "output_sample_rate", 24000) or 24000
    run["seconds"] = round(heard["audio"] / 2 / rate, 2)
    run["cut"] = heard["cut"] or bool(run.get("stopped"))
    if heard["first_at"] is not None:
        run["ms"] = round((heard["first_at"] - run["pressed"]) * 1000)
        STATE["audible"] = (provider, heard["first_at"] + run["seconds"],
                            bool(run.get("shown")) and not run.get("stopped"))
    if heard["audio"]:
        _named(run, heard["first_at"] + run["seconds"])
    if run.get("stopped"):
        # `cut` took the picture down and held the rest of her turn
        STATE["shown"] = None
        STATE["stopped"] = {"id": c["id"], "at": time.monotonic()}
        _end(run, error="stopped")
        return
    if not heard["audio"]:
        # no voice of it: never "ok" (review 7 Nov), and a picture that went up for
        # it comes down -- it would stand there with no line
        run["why"] = "no_audio"
        if run.get("shown"):
            queue = getattr(provider, "queue_picture", None)
            if callable(queue):
                queue({"clear": True, "cue": c["id"]})
            STATE["shown"] = None
            run.update(shown=False, show_error="not_said")     # up, and down again: not shown with her line
        _end(run, error="not_heard")
        return
    said = heard["text"].strip()
    if not said:
        # 3.8 leaves the output transcript out of ~40% of turns (measured 6 Nov):
        # no words is "not measured", never a score of 0 for a line she may have
        # said perfectly
        run["fidelity"] = None
        run["why"] = "no_transcript"
    else:
        voice = _voice()
        # event_voice's scorer: a number she said in Thai words ("ร้อยห้าสิบห้า")
        # is the line said right -- the same bar the recordings were kept by
        score = getattr(voice, "line_fidelity", None) or speech_match.fidelity
        unspoken = getattr(voice, "unspoken_numbers", None)
        numbers = speech_match.number_check(said, c["th"])
        run["fidelity"] = round(float(score(said, c["th"])), 3)
        run["numbers_missing"] = len(unspoken(said, c["th"]) if unspoken else numbers["missing"])
        run["numbers_extra"] = len(numbers["extra"])
    if not heard["cut"] and heard["audio"] and c.get("clip") and not run.get("stopped"):
        await _clip(run)
    if run.get("stopped"):
        _end(run, error="stopped")
    else:
        _end(run, ok=True)


async def _clip(run: dict) -> None:
    """The cue's film, after her line: event_host's entry, so it ends the way
    every clip ends (its `after_th`), and `lead_in` lets her last words out first.
    (A cue whose film is not on disk is not servable -- event_script `clip_missing` --
    but a film that went missing since keeps this error road.)"""
    from app import event_host

    clip = run["cue"]["clip"]
    run["clip"] = clip
    run["rolling"] = True                    # her line is over: `cut` leaves the film to the scene's stop
    try:
        result = await event_host.play_clip(clip, by=run["by"], lead_in=True)
    except Exception:                                    # noqa: BLE001
        logger.exception("event cue %s: its clip", run["id"])
        result = {"ok": False, "error": "error"}
    # Review 7 Nov (B4): a film that did not start ended the cue "ok" with the reason
    # in the row alone -- the running order could not tell staff. Now the cue's answer
    # in STATE["last"] carries it (`ok` = cued: the screen's own later failure is
    # event_clips' state, on the same page).
    ok = bool(result.get("ok"))
    run["clip_ok"] = ok
    if not ok:
        run["clip_error"] = str(result.get("error") or "error")
    run["clip_result"] = {"id": clip, "ok": ok, "error": None if ok else run["clip_error"],
                          "error_th": None if ok else str(result.get("error_th") or CLIP_ERROR_TH)}


def _resolve(run: dict, result: dict) -> None:
    started = run.get("started")
    if started is not None and not started.done():
        started.set_result(_with_show(run, result))


#: The press's facts in its row: numbers and words of OURS only (`show` is a story
#: id from the catalog, `show_error` a code).
_ROW = ("ms", "waited_s", "seconds", "cut", "partial", "numbers_missing", "numbers_extra",
        "model_held_ms", "purged_ms", "noted", "clip", "clip_ok", "clip_error", "quiet", "timeout", "why",
        "voice_why", "show", "shown", "show_error", "stale_interrupts")
#: A film that did not start, when event_clips gave no words of its own.
CLIP_ERROR_TH = "คลิปไม่เริ่มเล่น — Emma พูดบทแล้ว"


def _end(run: dict, *, ok: bool = False, error: str | None = None) -> dict:
    """The press is over (or refused): its answer, its row, its line on the page."""
    result = _with_show(run, {"ok": True, "mode": run.get("mode")} if ok else
                        {"ok": False, "error": error, "error_th": ERRORS_TH.get(error or "", ERRORS_TH["error"])})
    if run.get("clip_result") is not None:
        result = {**result, "clip": dict(run["clip_result"])}
    if run.get("ended"):
        return result
    run["ended"] = True
    if run.get("subtitled"):
        try:
            from app import face_mirror
            face_mirror.subtitle_end(run["id"], cut=bool(run.get("stopped") or not ok))
        except Exception:
            logger.exception("event cue %s: its subtitle's end", run.get("id"))
    _resolve(run, result)
    if run.get("seq") is not None:
        STATE["runs"].pop(run["seq"], None)
    if "show" in result and not result["show"]["ok"]:
        run["show_error"] = result["show"]["error"]   # the row says why, as the answer does
    fields = {k: run[k] for k in _ROW if run.get(k) is not None}
    if "fidelity" in run:
        fields["fidelity"] = run["fidelity"]        # a read turn: null is "not measured", kept as such
    turnlog.record("event_cue", id=run["id"], by=run["by"], mode=run.get("mode"), ok=ok, error=error, **fields)
    if run.get("known"):
        STATE["last"][run["id"]] = {"mode": run.get("mode"), "ok": ok, "error": error, "by": run["by"],
                                    "cut": bool(run.get("cut")), "fidelity": run.get("fidelity"),
                                    "at": time.time()}
        if "show" in result:
            STATE["last"][run["id"]]["show"] = result["show"]
        if run.get("clip_result") is not None:
            # the film after her line: {"id", "ok", "error", "error_th"} (B4)
            STATE["last"][run["id"]]["clip"] = dict(run["clip_result"])
    return result


# ---- the red stop ----------------------------------------------------------------------

#: How long past a line's last audio `cut` still treats it as playing: the page's own
#: lead over the server's clock (RECORDED_LEAD_S of a recorded line is already in it).
AUDIBLE_SLACK_S = 1.0


async def cut(why: str = "staff") -> bool:
    """Staff pressed the red stop: the ceremony line being said stops NOW (a wrong
    line, or the arm/handshake emergency -- app/event_control_server.py /event/stop).

    What it stops:
    * a press still waiting (her voice, the lock): it never starts ("stopped");
    * a recorded line: no more of it is fed, what is queued for the page is dropped,
      its hold is lowered (the call goes on as normal);
    * a line the model reads: what is queued is dropped and the rest of that turn is
      held off the speaker to its end (`provider.stop_line(hold_model=True)`);
    * the page: an `interrupted` -- the barge-in road, voice-preview.html
      `stopPlayback()` stops every source it scheduled, the captions and the
      story's clock (the TV's copy too);
    * the picture pressed with that line comes down (`clear`).
    What it cannot: audio already past the page, in the robot's output buffer
    (outputLatency, tens of ms); the model's own generation of a line it reads --
    Gemini goes on, held here, and its context has the line whole (the note beside
    tool results says it was stopped); a film the line started (`rolling`: the
    scene's stop is event_host's).
    Review 7 Nov: a press still in its first awaits (looking the cue up) is stopped
    too, by the generation it read at entry (A2: it used to play after a stop that
    said False); a line's picture comes down in the last seconds after its press
    ended and before its first word (A4); with no line of ours at all, her own
    answer streaming or still playing (the live step, an announcement) is stopped
    the same way (B3: the red stop used to silence nothing but a cue line). The
    ceremony's live window closes (event_host.live_close).
    True = something was stopped. One `event_cue_cut` row, with `why`."""
    why = why.strip()[:24] if isinstance(why, str) and why.strip() else "staff"
    now = time.monotonic()
    STATE["cut_gen"] += 1                    # a press not registered yet reads it (`play`)
    entering = STATE["entering"]
    try:
        # the room screen's line and slide come down whatever is found to stop below: a
        # line "over" here may still be playing there (face_mirror.subtitle_cut)
        from app import face_mirror

        face_mirror.subtitle_cut()
    except Exception:                                    # noqa: BLE001 -- the stop goes on
        logger.exception("event cues: the room screen's line")
    try:
        from app import event_host

        event_host.live_close("cut")
    except Exception:                                    # noqa: BLE001 -- the stop goes on
        logger.exception("event cues: closing the live window")
    runs = [r for r in list(STATE["runs"].values())
            if r.get("phase") in ("waiting", "speaking") and not r.get("ended") and not r.get("rolling")]
    tail = STATE.get("audible")
    live = _live_call()
    tail_on = live is not None and _tail_on(getattr(live, "provider", None), now)
    answer = None
    if not runs and not tail_on and live is not None:
        # nothing of ours: her own answer, if one is under way (B3)
        live_provider = getattr(live, "provider", None)
        answer = (_model_busy(live, live_provider, now)
                  or ("audible" if _audible(live, now) else None)) if live_provider is not None else None
    if not runs and not tail_on and answer is None:
        if entering:
            turnlog.record("event_cue_cut", why=why, ran=True, entering=entering)
            return True
        turnlog.record("event_cue_cut", why=why, ran=False)
        return False
    speaking = [r for r in runs if r.get("phase") == "speaking"]
    waiting = [r for r in runs if r.get("phase") != "speaking"]
    for r in runs:
        r["stopped"] = why
    for r in waiting:
        task = r.get("task")
        if task is not None and not task.done():
            task.cancel()                    # its waits end now; `_guarded` says "stopped"
    providers: dict = {}
    for r in speaking:
        entry = providers.setdefault(id(r["provider"]), [r["provider"], False, False])
        entry[1] = entry[1] or r.get("mode") == "read"
        # the picture is on its way the moment the provider says so (`report`), not
        # only once her first word is out (A4: stopped in between, it stayed up)
        entry[2] = entry[2] or bool(r.get("shown")) or bool((r.get("report") or {}).get("picture"))
    if tail_on:
        entry = providers.setdefault(id(tail[0]), [tail[0], False, False])
        # its picture stands with its last seconds (A4: they were cut and it stayed up)
        entry[2] = entry[2] or (len(tail) > 2 and bool(tail[2]))
    if answer is not None:
        # held to its end as a reading is: the server cannot be made to stop the turn --
        # but only a turn still under way. An answer whose words are all out and only
        # still PLAYING has no turn left to hold: the hold would land on her NEXT reply
        # instead (a model turn within RECORDED_HELD_GAP_S is kept off the speaker) --
        # the guest's next question answered in silence (review 7 Nov, round 3).
        entry = providers.setdefault(id(live.provider), [live.provider, False, False])
        entry[1] = entry[1] or answer != "audible"
    done: dict = {}
    for provider, reading, shown in providers.values():
        stop = getattr(provider, "stop_line", None)
        if not callable(stop):
            continue
        try:
            done.update(stop(hold_model=reading, clear=shown) or {})
        except Exception:                                # noqa: BLE001 -- the stop still ends the presses
            logger.exception("event cues: stopping the line")
    for r in speaking:
        wake = r.get("wake")
        if callable(wake):
            wake()                           # a reading stops waiting for her
    STATE["audible"] = None
    extra = {"entering": entering} if entering else {}
    if answer is not None:
        extra["answer"] = answer
    turnlog.record("event_cue_cut", why=why, ran=True, ids=[r["id"] for r in runs][:4],
                   waiting=len(waiting), speaking=len(speaking), tail=bool(tail_on and not runs), **extra,
                   **{k: v for k, v in done.items() if k in ("recorded", "held", "purged_ms", "interrupted",
                                                             "cleared", "picture_dropped")})
    return True


# ---- the staff page --------------------------------------------------------------------

def _status_now() -> dict:
    doc = event_script.load()
    rows = []
    for row in event_script.status(doc):
        c = event_script.cue(row["id"], doc) or {}
        voice = _voice_status(c)
        rows.append({**row, "recorded": bool(voice.get("recorded")), "voice_approved": bool(voice.get("approved")),
                     "voice_why": voice.get("why"), "voice_seconds": voice.get("seconds"),
                     "voice_fidelity": voice.get("fidelity")})
    return {"host_th": (doc or {}).get("host_th"), "rows": rows}


#: The one status refresh in flight (rehearsal 24 Nov 11:00, a live stack dump by the walking session: all 12
#: executor threads were inside _status_now -- every poll after the 10 s TTL started its OWN refresh, one refresh
#: took longer than 10 s on the robot once the cues were signed, so the cache was never fresh and the loop starved:
#: /ceremony/state > 30 s, "chassis unreachable", "OFFLINE (dns)"). One refresh at a time; a stale status is served
#: at once while it runs (a phone page a few seconds old is fine); only the very first call waits.
_status_refresh: "asyncio.Future | None" = None


async def _status() -> dict:
    global _status_refresh
    now = time.monotonic()
    cached = STATE["status"]
    if cached is not None and now - cached[0] < STATUS_TTL_S:
        return cached[1]
    loop = asyncio.get_running_loop()
    if (_status_refresh is None or _status_refresh.done()
            or getattr(_status_refresh, "get_loop", lambda: loop)() is not loop):
        async def run() -> None:
            try:
                st = await asyncio.to_thread(_status_now)
            except Exception:                            # noqa: BLE001 -- the page still loads
                logger.exception("event cues: status")
                old = STATE["status"]
                # (review 24 Nov) a failed refresh keeps the good list the staff page already had
                st = old[1] if old is not None else {"host_th": None, "rows": []}
            STATE["status"] = (time.monotonic(), st)

        _status_refresh = asyncio.ensure_future(run())
    if cached is not None:
        return cached[1]                                 # stale while the one refresh runs
    await asyncio.shield(_status_refresh)
    return STATE["status"][1]


async def snapshot() -> dict:
    """The cue list for the phone page: what each is, whether it can be pressed,
    what the last press did. Approved script text only -- never a guest's words."""
    status = await _status()
    phases = {r["id"]: r.get("phase") for r in STATE["runs"].values()}
    wall = time.time()
    cues = []
    for row in status["rows"]:
        last = STATE["last"].get(row["id"])
        cues.append({**row, "phase": phases.get(row["id"]),
                     "last": None if last is None else {**{k: v for k, v in last.items() if k != "at"},
                                                         "ago_s": round(wall - last["at"])}})
    return {"host_th": status["host_th"], "call": _live_call() is not None, "cues": cues}
