"""The robot face, copied to a big screen in the room (owner 28 Oct: "ให้จอ
ห้องนั้นโชว์ตัวหน้าเหมือนในจอหุ่นเลย" -- and the listener only to watch:
"หุ่นเปิดช่องดูอย่างเดียว").

The robot's call already sends its page everything the face draws: her
voice, her words, her state, the stories. This module keeps a copy of that
stream for the screens that ask (app/mirror_server.py, /ws/mirror) and
nothing more:

  * Only what the face DRAWS goes out, by type, rebuilt field by field --
    never the guest's words (`user_transcript`; a tool's raw `result` can
    carry `heard` too, tools/computer.py), never a command id the page would
    answer (plans, body and walk commands), never the quick-ask buttons.
    The face itself never shows the guest's words; a screen across the room
    must not be the first to.
  * A screen cannot slow the robot: every screen has its own queue, a full
    queue drops (counted, logged once), and publishing is a put_nowait on
    the robot's own loop -- no await on the reply path.
  * Nothing comes back: the socket's incoming frames are read and dropped.

The call socket (/ws) is never opened by a mirror: the newest connection
there takes the robot over (session.handle_connection).
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import re
import time

from app.config import settings

logger = logging.getLogger("condo_voice.mirror")

#: Frames queued per screen before it is dropping. Her voice is ~10 chunks a
#: second; a LAN screen that is 40 s behind is not watching this call.
QUEUE_MAX = 400
#: State changes the face shows, forwarded as their bare type.
_STATES = frozenset({"speech_started", "interrupted", "turn_complete", "heard_end"})

#: An answer picture's address as the session sends it (tools/slides._public:
#: "/slides/<file>"): one file name of the deck's own images, nothing else.
_SLIDE_URL = re.compile(r"^/slides/[A-Za-z0-9_.-]+\.(?:jpe?g|png|webp|gif|avif)$", re.I)

#: A recorded line's voice goes to the screens WHOLE, the moment the robot starts saying it (the hall's
#: sound over a bad Wi-Fi, 23-24 Nov 2026: the lobby notebook plays her voice aloud for the hall). The
#: robot's own push runs only RECORDED_LEAD_S = 2 s ahead of her voice (providers/gemini.py), so on the
#: room screen's Wi-Fi a radio gap longer than that was a gap in her voice in the hall -- measured with a
#: proxy that stalls the link 1-3 s (docs/emma-ceremony.md, "เสียงในห้องโถงเมื่อ Wi-Fi ไม่ดี"). The line is
#: a pinned recording, known whole at its first word: the screens get all of it then, in pieces this long,
#: and play it on their own clock from where it lands -- nothing is heard sooner than before, the rest is
#: simply already there when the radio stops. The robot's own chunks of it are not sent again; a cut
#: (`interrupted`, the red stop) drops what a page holds, as it always did.
AHEAD_PIECE_S = 0.5
#: A line registered (`ahead`) whose voice has not started within this long is not coming.
AHEAD_PENDING_S = 30.0
#: A cut marks the lines still sounding as cut when the SCREEN may still be playing them: the screen plays
#: behind the server's clock (that lag is the point of sending a line whole), so "the robot finished it" is
#: not "the hall finished it". A line this long (seconds) past its end is certainly over on the screen.
AHEAD_LAG_S = 30.0
#: A screen's `have` (bytes of a line it already holds) beyond any clip of the ceremony is not believed.
AHEAD_MAX_BYTES = 24000 * 2 * 600

_clients: set[asyncio.Queue] = set()
#: One number per registered play of a line (`ahead`): a line played twice has the same id, and a screen's
#: `have` counts the bytes of ONE play (review 24 Nov). Never reset: a token is not reused.
_ahead_seq = itertools.count(1)


class _VoiceText(str):
    """A `mirror_voice` header -- marked so a cut can take the unsent ones out of a screen's queue."""


class _VoiceBytes(bytes):
    """A piece of a recorded line's voice (not the robot's own chunk) -- same reason."""

_state: dict = {"in_call": False, "rate": None, "profile": None, "story": None,
                "clips": None, "clip": None, "subtitle": None, "subtitle_voice_at": None, "motion": None,
                # the recorded line being said (`ahead`) and the one before it, for a screen back from a blink
                "ahead": None, "ahead_prev": None,
                # the walk's music (`catwalk`): {"track", "duck"} while a loop should be playing in the hall
                "catwalk": None}
_dropped = {"count": 0, "logged": False}
#: Who wants to know the screens were told to stop (app/ceremony_reveal: the debut's walk must not go out
#: on a hit the room never saw): called with "red_stop", "call_ended", "left_show_mode" or "slide" --
#: exactly what the page takes the debut down on (voice-preview.html mirrorRevealStop's callers).
_stop_hooks: list = []
#: What each screen said it is when it connected (/ws/mirror?sound=1&w=..&h=..&coarse=0): {"sound", "w",
#: "h" (device pixels), "coarse" (touch-first)}. A queue without one (a page from before 23 Nov, a test)
#: is a screen of unknown kind. Only ever warned on, never refused (app/ceremony_reveal).
_kinds: dict = {}
#: The queues of the robot's own chest-screen frame (app/face_film.py, CEREMONY_FACE_FILM): they get everything
#: a screen gets, and are not a screen of the hall -- `screens()` / `screen_kinds()` (the debut's "who is
#: watching", app/ceremony_reveal.py) leave them out.
_film: set = set()


def voice_on_robot() -> bool:
    """Her voice is on the robot's own speaker only: CEREMONY_VOICE_ON_ROBOT, in the ceremony's call. The room
    screen then plays it at its silent gain (voice-preview.html mirrorHerVoice) and nothing of it is replayed
    late in the hall (room_screens.echo_tail_s)."""
    return bool(settings.ceremony_voice_on_robot) and _state["in_call"] and _state["profile"] == "ceremony"


def watching() -> bool:
    """Any screen connected -- the session's hook costs nothing without one."""
    return bool(_clients)


def view(payload: object) -> dict | None:
    """The face's own message as a mirror may see it, or None (dropped)."""
    if not isinstance(payload, dict):
        return None
    kind = payload.get("type")
    if kind in _STATES:
        return {"type": kind}
    if kind == "assistant_transcript":
        text = payload.get("text")
        return {"type": kind, "text": text if isinstance(text, str) else ""}
    if kind == "ready":
        out = {"type": "ready"}
        if isinstance(payload.get("output_rate"), int):
            out["output_rate"] = payload["output_rate"]
        if isinstance(payload.get("profile"), str):
            out["profile"] = payload["profile"]
        if out.get("profile") == "ceremony" and settings.ceremony_voice_on_robot:
            out["voice_on_robot"] = True           # (the show mode's page plays her silent: the robot speaks)
        return out
    if kind == "tool_result":
        result = payload.get("result")
        if not isinstance(result, dict):
            return None
        screen = result.get("screen")
        if screen == "story" and isinstance(result.get("story"), dict):
            if _story_is_the_scripts(result.get("topic")):
                return None                        # live first: the script's own cue slide is the hall's answer
            # stories.page_payload: pinned /story-asset/<sha> pictures, the
            # catalog's lines and labels -- "no sources, notes or guest
            # questions" (stories.py); `say`/`instruction`/`details` stay home
            return {"type": "tool_result", "result": {
                "ok": True, "screen": "story", "topic": result.get("topic"), "story": result["story"]}}
        if screen == "clear":
            return {"type": "tool_result", "result": {"screen": "clear"}}
        # The answer picture (ANSWER_IMAGES: search_condo_info put the slide
        # that matches her answer on the robot's screen -- session.py adds it
        # as `slide`). Robot log 28 Oct, staff on the media-room TV: the robot
        # showed it and the room screen did not ("บางคำถามมีรูป แต่ทำไมอยู่ๆก็
        # ไม่มี"), because only stories passed here. Rebuilt from the address
        # and the titles; the slide's summaries and policy traces stay home.
        picture = _slide_view(payload.get("slide"))
        if picture is not None:
            return {"type": "tool_result", "result": {"screen": "slide"}, "slide": picture}
    return None


def _story_is_the_scripts(topic: object) -> bool:
    """CEREMONY_FOLLOW_CUES off, follow running (owner 24 Nov: "ไม่ต้องโชว์ ... จะได้ไม่ต้องชนกัน"): a story whose topic
    is a cue's `show` in the order is not a second motion on the hall screen. Off-script stories pass as before."""
    if not (settings.ceremony_follow_host and not settings.ceremony_follow_cues and _state["profile"] == "ceremony"):
        return False
    try:
        from app import ceremony_follow

        if settings.ceremony_free_talk and ceremony_follow.free_talk():
            return not ceremony_follow.story_wanted(topic)   # free talk (24 Nov ~12:40): her story shows unless a cue slide holds the exchange

        return ceremony_follow.hall_story_covered(topic)
    except Exception:                              # noqa: BLE001 -- the story goes out
        return False


def _slide_view(slide: object) -> dict | None:
    if not isinstance(slide, dict):
        return None
    url = slide.get("url")
    if not isinstance(url, str) or not _SLIDE_URL.match(url):
        return None
    out = {"url": url}
    for key in ("title_th", "title_en"):
        if isinstance(slide.get(key), str):
            out[key] = slide[key][:200]
    return out


def _put(item) -> None:
    for queue in list(_clients):
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            _dropped["count"] += 1
            if not _dropped["logged"]:
                _dropped["logged"] = True
                logger.warning("mirror: a room screen is not keeping up -- frames dropped (logged once)")


def _purge_voice() -> None:
    """A cut: what of a recorded line's voice has not gone out yet is not sent. Review 24 Nov: a stop
    queued behind the whole line (1.4 MB for 30 s) reached a screen on a weak Wi-Fi seconds late. Only the
    line's own header and pieces go; every other queued message keeps its place."""
    for queue in list(_clients):
        q = getattr(queue, "_queue", None)
        if q is None:
            continue
        keep = [x for x in q if not isinstance(x, (_VoiceText, _VoiceBytes))]
        if len(keep) != len(q):
            q.clear()
            q.extend(keep)


def publish_json(payload: object) -> None:
    """A message the robot face's call just sent its page."""
    out = view(payload)
    if out is None:
        return
    if out["type"] in ("interrupted", "speech_started"):
        _ahead_cut()                           # her line cut short: what follows of it goes as it comes
    if out["type"] == "ready":
        _state["rate"] = out.get("output_rate") or _state["rate"]
        _state["profile"] = out.get("profile") or _state["profile"]
        if out.get("profile") and out["profile"] != "ceremony":
            _stopped("left_show_mode")         # (the page leaves show mode on it: the debut goes with it)
    elif out["type"] == "speech_started":
        _state["story"] = None                 # a new question owns the screen
    elif out["type"] == "tool_result":
        # what is on the robot's screen now: a story or an answer picture
        _state["story"] = out if out["result"].get("screen") in ("story", "slide") else None
    if _clients:
        _put(json.dumps(out, ensure_ascii=False))


def publish_audio(chunk: bytes) -> None:
    """Her voice, as the face receives it (PCM16 mono at the ready's rate):
    the mirror plays it muted, so its captions and stories keep her time."""
    if chunk and _state["subtitle"] is not None and _state["subtitle_voice_at"] is None:
        _state["subtitle_voice_at"] = time.monotonic()        # her voice of this line began
    if chunk and _ahead_took(bytes(chunk)):
        return                                 # a recorded line's: the screens have it whole already
    if _clients and chunk:
        _put(bytes(chunk))


# ---- a recorded line's voice, whole (see AHEAD_PIECE_S) -------------------------------------
# Server-built like the subtitle: the pinned recording event_cues is about to play, never anything of
# the face's stream but the bytes the robot itself pushes -- matched byte for byte, so the screens hear
# exactly what the robot says and nothing it does not (a line refused before its first word never goes).

def _rate() -> int:
    return int(_state["rate"] or 24000)


def ahead(line_id: str, pcm: bytes) -> None:
    """The recorded line the robot is about to say (app/event_cues.py, right before the provider's push):
    its voice goes to the screens whole at its first word. Replaces any line registered before."""
    old = _state["ahead"]
    if old is not None and old["at"] is not None:
        _state["ahead_prev"] = {k: v for k, v in old.items() if k != "pcm"}
    if not isinstance(pcm, (bytes, bytearray)) or len(pcm) < 2:
        _state["ahead"] = None
        return
    data = bytes(pcm[:len(pcm) // 2 * 2])
    _state["ahead"] = {"id": str(line_id or "")[:64], "pcm": data, "n": len(data), "off": 0, "at": None,
                       "state": "pending", "set": time.monotonic(), "tok": next(_ahead_seq)}


def ahead_gone(line_id: str) -> None:
    """The line registered did not play out on the robot. Not started (refused, stopped before its first
    word): nothing of it goes. Started and still being pushed when it ended (the robot's page stopped
    taking it, the call closing -- no `interrupted` came through the call): the screens hold the rest of
    it, so they are told to stop it -- the road every cut takes. A cut already seen does nothing more."""
    a = _state["ahead"]
    if a is None or a["id"] != str(line_id or "")[:64]:
        return
    if a["at"] is None:
        _state["ahead"] = None
    elif a["state"] == "live":
        a["state"] = "cut"
        _purge_voice()
        if _clients:
            _put(json.dumps({"type": "interrupted"}))


def _drop_live_voice() -> None:
    """A new call (or none): a recorded line of the old one still sounding on the screens stops there --
    they hold all of it, where they used to hold its last 2 s."""
    a = _state["ahead"]
    state = _voice_state(a)
    if state is not None and state["state"] == "live" and _clients:
        _purge_voice()
        _put(json.dumps({"type": "interrupted"}))


def _ahead_took(chunk: bytes) -> bool:
    """The robot's own chunk of the registered line: True = the screens have it already (sent whole at
    the line's first chunk). Anything else is not the line's and goes as it always did."""
    a = _state["ahead"]
    if a is None or a["state"] not in ("pending", "live"):
        return False
    now = time.monotonic()
    if a["state"] == "pending" and now - a["set"] > AHEAD_PENDING_S:
        _state["ahead"] = None
        return False
    off = a["off"]
    if a["pcm"][off:off + len(chunk)] != chunk:
        if a["state"] == "live":
            # (never seen: the robot's voice left the line without a cut) -- the rest goes as it comes
            a["state"] = "cut"
            logger.warning("mirror: the robot's voice left recorded line %s mid-way", a["id"])
        return False
    if a["state"] == "pending":
        a.update(state="live", at=now)
        if _clients:
            for item in _voice_items(a, 0):
                _put(item)
    a["off"] = off + len(chunk)
    if a["off"] >= a["n"]:
        a["state"] = "said"                    # all of it pushed: her voice of it plays on its own clock
    return True


def _ahead_cut() -> None:
    """Her voice of the line was cut short (`interrupted`, a barge-in, the red stop): the page drops what it
    holds of it, and any later chunk of the robot's goes as it comes."""
    # (the line before too: a press over its tail sends its `interrupted` after the new line is registered)
    # Not only a line the server clock still calls live: the screen plays BEHIND that clock, so a line the
    # robot finished may still be sounding there (blink + red stop: it must not be resumed and played out).
    now = time.monotonic()
    for a in (_state["ahead"], _state["ahead_prev"]):
        if a is not None and a.get("at") is not None and a["state"] in ("live", "said") \
                and now < a["at"] + a["n"] / 2 / _rate() + AHEAD_LAG_S:
            a["state"] = "cut"
    _purge_voice()                             # what is not sent yet is not sent: the cut goes first


def _voice_state(a: dict | None, now: float | None = None) -> dict | None:
    """{"id", "state"} of a line whose voice started: "live" (the robot is still saying it), "over"
    (said to its end), "cut". For the hello: a screen back from a blink decides what it plays on."""
    if a is None or a.get("at") is None:
        return None
    now = time.monotonic() if now is None else now
    if a["state"] == "cut":
        state = "cut"
    else:
        state = "live" if now < a["at"] + a["n"] / 2 / _rate() + 0.5 else "over"
    return {"id": a["id"], "tok": a.get("tok"), "state": state}


def _voice_items(a: dict, start: int) -> list:
    """`mirror_voice` (which line, from which byte of it, how many bytes follow) and then that voice in
    pieces -- the page counts the bytes after it as this line's."""
    pcm = a.get("pcm")
    if not pcm:
        return []
    start = max(0, min(len(pcm), int(start)))
    start -= start % 2
    if start >= len(pcm):
        return []
    size = max(2, int(_rate() * AHEAD_PIECE_S) * 2)
    items: list = [_VoiceText(json.dumps({"type": "mirror_voice", "id": a["id"], "tok": a.get("tok"),
                                          "from": start, "bytes": len(pcm) - start}))]
    items.extend(_VoiceBytes(pcm[i:i + size]) for i in range(start, len(pcm), size))
    return items


# ---- a clip on the screen (app/event_clips.py) --------------------------------------
# Not the face's stream: the server's own messages, built here field by field from the
# event file's ids and pinned hashes. Kept for a screen that connects after them -- the
# window takes seconds to open, and a cue sent into that gap would be no clip at all.

def clip_prepare(rows: list[dict]) -> None:
    _state["clips"] = [{"id": str(r["id"]), "url": str(r["url"])} for r in rows]
    if _clients:
        _put(json.dumps({"type": "mirror_clips", "clips": _state["clips"]}))


def clip_play(cue: dict) -> None:
    """Replayed to a screen joining only until the next cue or stop: a browser that
    restarted mid-film starts it again, which beats a dark TV with a silent robot."""
    _state["clip"] = {"type": "mirror_clip_play", "seq": int(cue["seq"]),
                      "id": str(cue["id"]), "url": str(cue["url"])}
    if _clients:
        _put(json.dumps(_state["clip"]))


def clip_stop() -> None:
    _state["clip"] = None
    if _clients:
        _put(json.dumps({"type": "mirror_clip_stop"}))


# ---- the ceremony's subtitle (app/event_cues.py) -------------------------------------
# Owner 17 Nov: her voice on stage is English, "ซับไทย ... ด้านล่าง", and the room screen
# shows the pictures and that line with no Emma on it ("เอาหุ่นออกแล้วเอาพวกนี้ไปใส่แทน").
# The server's own message, as a clip's is: the cue's approved Thai line and its scene's
# title, both from the ceremony file -- never the face's stream, never a guest's word.

#: A line longer than this is not a cue of the file (the longest is under 700).
SUBTITLE_MAX = 1200


def subtitle(cue: dict) -> None:
    """The line going out now: {"id", "text", "scene", "seconds"} (`seconds` = how long
    her voice of it is expected to take; the page corrects it from the voice it plays).
    `slide` = the line's motion slide (app/ceremony_slides.py `for_cue`: checked against
    the cue and rebuilt there, from the slides file -- the cue code's own, like the words)."""
    text = cue.get("text")
    if not isinstance(text, str) or not text.strip():
        return
    out = {"type": "mirror_subtitle", "id": str(cue.get("id") or "")[:64], "text": text.strip()[:SUBTITLE_MAX]}
    if isinstance(cue.get("scene"), str) and cue["scene"].strip():
        out["scene"] = cue["scene"].strip()[:120]
    seconds = cue.get("seconds")
    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and 0 < seconds < 600:
        out["seconds"] = round(float(seconds), 1)
    if isinstance(cue.get("slide"), dict) and isinstance(cue["slide"].get("template"), str):
        out["slide"] = cue["slide"]
        _stopped("slide")                      # (a slide coming up takes the debut's place on the screen)
    _state["subtitle"] = out
    _state["motion"] = None                    # a line of the script: its own slide (or none) from here
    _state["subtitle_voice_at"] = None
    if _clients:
        _put(json.dumps(out, ensure_ascii=False))


def motion(slide: dict | None, cue: str | None) -> None:
    """"ตอบสด" (app/ceremony_motion.py, owner 23 Nov): the host off-script, a slide of ours on his topic in
    its settled state -- or, `slide` None, the show's own logo loop (any slide down). Server-built like the
    subtitle: a cue id and the slide ceremony_slides.for_cue checked, never the face's stream, never a word
    of anybody's. Kept for a screen that joins until a line's subtitle or the call's end replaces it."""
    out = {"type": "mirror_motion"}
    if isinstance(slide, dict) and isinstance(slide.get("template"), str) and isinstance(cue, str) and cue:
        out.update(id=cue[:64], slide=slide)
        _stopped("slide")                      # (a slide coming up takes the debut's place: as subtitle())
    _state["motion"] = out if "slide" in out else None
    if _clients:
        _put(json.dumps(out, ensure_ascii=False))


def motion_now() -> dict | None:
    """The topic slide of "ตอบสด" held for a joining screen now (None: none, or a line/the cut replaced it) --
    app/ceremony_motion.py asks after a press whether the pressed line's subtitle took its place."""
    return _state["motion"]


def subtitle_end(cue_id: str | None = None, *, cut: bool = False) -> None:
    """The line is over (`cut`: stopped by staff -- down at once, not after her voice).
    With an id, only that line's: a late end of the line before must not take the next
    one's words down."""
    held = _state["subtitle"]
    if held is None or (cue_id is not None and held.get("id") != str(cue_id)[:64]):
        return
    _state["subtitle"] = None
    if _clients:
        _put(json.dumps({"type": "mirror_subtitle_end", "id": held.get("id"), "cut": bool(cut)}))


def subtitle_cut() -> None:
    """The red stop (event_cues.cut): whatever line or slide is on the screen comes down
    now -- sent with no id and even when no line is held any more. Review 17 Nov: a line
    the model reads is "over" here at its turn_complete with most of its voice still
    queued on the screen, so a stop in that tail found nothing to end and the slide
    jumped to its end state and stayed (a stopped joke showing its punchline). Only in a
    ceremony's call or with a line held: any other call's messages are as they were."""
    _stopped("red_stop")                       # (the debut stops on it too -- the page and the walk)
    try:
        from app import live_subtitle

        live_subtitle.cut()                    # (her live answer's Thai line, if one is on: CEREMONY_LIVE_SUBTITLE_TH)
    except Exception:                          # noqa: BLE001 -- the stop goes out
        logger.exception("mirror: live subtitle cut")
    _state["motion"] = None                    # (the page takes a ตอบสด slide down on this cut too)
    _ahead_cut()                               # (a screen back from a blink must not play the rest of it on)
    if _state["subtitle"] is None and _state["profile"] != "ceremony":
        return
    _state["subtitle"] = None
    if _clients:
        _put(json.dumps({"type": "mirror_subtitle_end", "cut": True}))


# ---- her LIVE answer's Thai subtitle (app/live_subtitle.py, owner 24 Nov) ------------------------------
# Server-built like a cue's: the id of her turn, an index, the Thai the server translated -- never the face's
# stream, never a word of the host's. Not kept for a joining screen (a live sentence is gone with its voice).


def live_subtitle_ok() -> bool:
    """A hall screen is connected in the ceremony's call and no cue's line is up (a line of the script has
    its own subtitle): the only time her live words are translated at all."""
    return bool(_clients) and _state["in_call"] and _state["profile"] == "ceremony" and _state["subtitle"] is None


def _live_id(turn) -> str:
    return "live-" + str(int(turn))[:12]


def live_mark(turn: int, n: int) -> None:
    """Sentence `n` of her turn begins about here in her voice: sent in step with her audio frames, the page
    keeps where its own audio clock is when it arrives (voice-preview.html mirrorLiveMark)."""
    if _clients:
        _put(json.dumps({"type": "mirror_live_mark", "id": _live_id(turn), "n": int(n)}))


def live_subtitle(turn: int, n: int, text: str, seconds: float) -> None:
    if not isinstance(text, str) or not text.strip() or not _clients:
        return
    out = {"type": "mirror_subtitle", "live": True, "id": _live_id(turn), "n": int(n), "text": text.strip()[:SUBTITLE_MAX]}
    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and 0 < seconds < 600:
        out["seconds"] = round(float(seconds), 1)
    _put(json.dumps(out, ensure_ascii=False))


def live_subtitle_end(turn: int, *, cut: bool = False) -> None:
    """Her answer is over (`cut`: interrupted or stopped -- down at once). Carries the turn's id: a late end of
    the answer before cannot take the next answer's words down."""
    if _clients:
        _put(json.dumps({"type": "mirror_subtitle_end", "id": _live_id(turn), "cut": bool(cut)}))


# ---- the debut (app/ceremony_reveal.py) ----------------------------------------------------------
# The server's own message, like a clip's: built here field by field -- the numbers and the times, the
# pinned sound's and video's addresses, nothing else. Never replayed to a screen that joins later: a
# countdown that started without it is over.

_ASSET = re.compile(r"/ceremony-asset/[0-9a-f]{64}")


def on_stop(fn) -> None:
    if fn not in _stop_hooks:
        _stop_hooks.append(fn)


def _stopped(why: str) -> None:
    _state["reveal_until"] = None
    if why in _CATWALK_STOPS:
        _state["catwalk_hit_at"] = None               # (a stop: no pictures lingering for a screen that comes back)
    if why in _CATWALK_STOPS and _state["catwalk"] is not None:
        _state["catwalk"] = None
        _put(json.dumps({"type": "catwalk", "op": "off"}))
    for fn in list(_stop_hooks):
        try:
            fn(why)
        except Exception:
            logger.exception("mirror: a stop hook failed")


def screens() -> int:
    return len([q for q in _clients if q not in _film])


def screen_kinds() -> list:
    """What each connected screen said it is ({"sound", "w", "h"}), None for one that did not say."""
    return [_kinds.get(q) for q in list(_clients) if q not in _film]


def kind_from_query(params) -> dict | None:
    """/ws/mirror's query as a screen's kind -- None when the page did not say (no `sound`)."""
    if params.get("sound") not in ("0", "1"):
        return None

    def size(key: str) -> int:
        try:
            return max(0, min(20000, int(params.get(key) or 0)))
        except ValueError:
            return 0
    return {"sound": params.get("sound") == "1", "w": size("w"), "h": size("h"), "coarse": params.get("coarse") == "1"}


def show_mode() -> bool:
    """The screens are in the ceremony's show mode: a call is up and it is the ceremony app's
    (the page turns show mode on from that call's `ready`)."""
    return bool(_state["in_call"]) and _state["profile"] == "ceremony"


def reveal(spec: dict) -> None:
    out: dict = {"type": "ceremony_reveal", "seq": int(spec["seq"]),
                 "numbers": [[int(n), round(float(t), 3)] for n, t in spec["numbers"][:20]],
                 "beats": [round(float(t), 3) for t in (spec.get("beats") or [])[:64]]}
    for key in ("zero_s", "impact_s", "clip_at_s", "settle_s", "length_s"):
        out[key] = round(float(spec[key]), 3)
    if spec.get("dark") in (True, "build"):          # (black, or black until the hit: app/ceremony_reveal.py dark())
        out["dark"] = spec["dark"]
    if isinstance(spec.get("src"), str) and _ASSET.fullmatch(spec["src"]):
        out["src"] = spec["src"]
    # (live until its end: a screen back from a blink in that time plays on, after it is told to stop)
    _state["reveal_until"] = time.monotonic() + float(spec["length_s"]) + 1.0
    # The debut has the stage (review 25 Nov): a walk's music left on (its hit/off never came, or a press within the
    # pictures' linger) is over, and a line or a ตอบสด slide still held is not replayed to a screen that blinks during the
    # build -- it ended the debut on that screen while the robot still walked out on the hit. The tour's own `walk`
    # comes on the hit, after this.
    if _state["catwalk"] is not None:
        _state["catwalk"] = None
        if _clients:
            _put(json.dumps({"type": "catwalk", "op": "off"}))
    _state.update(subtitle=None, motion=None, subtitle_voice_at=None, catwalk_hit_at=None)
    _state["reveal_sent"] = True
    video = spec.get("video")
    if isinstance(video, dict) and isinstance(video.get("src"), str) and _ASSET.fullmatch(video["src"]):
        out["video"] = {"src": video["src"], "at_s": round(float(video["at_s"]), 3),
                        "length_s": round(float(video["length_s"]), 3)}
    if _clients:
        _put(json.dumps(out))


def reveal_stop() -> None:
    _state["reveal_until"] = None
    if _clients:
        _put(json.dumps({"type": "ceremony_reveal_stop"}))


# ---- the walk's music (card 1; owner 24 Nov, CATWALK_MUSIC) ------------------------------------------
# The page plays it on its own clock (client/assets/catwalk-music.js), aloud only where it plays sound: a
# Wi-Fi blink is not heard, and a slide or a line going up does not end it (her own line ducks it, the
# tour says when). What ends it: `hit`, `off`, the red stop, the call over, the show mode left.

_CATWALK_OPS = ("walk", "duck", "unduck", "hit", "off")
#: the page's own LINGER_MS (client/assets/catwalk-music.js): after a hit the pictures stay this long
CATWALK_LINGER_S = 45.0
_CATWALK_TRACKS = ("walk", "guests")
_CATWALK_STOPS = ("red_stop", "call_ended", "left_show_mode")


def catwalk(op: str, track: str = "walk") -> bool:
    """Said by the tour (app/event_tour.py). False = nothing was sent: the switch is off, no such word, or a
    `walk` outside the ceremony's show mode (the page would not play it)."""
    if not settings.catwalk_music or op not in _CATWALK_OPS:
        return False
    now = _state["catwalk"]
    if op == "walk":
        if not show_mode():
            return False
        now = _state["catwalk"] = {"track": track if track in _CATWALK_TRACKS else "walk", "duck": False}
        out = {"type": "catwalk", "op": "walk", **now}
    else:
        if op in ("duck", "unduck"):
            # (no walk on, e.g. right after a handshake's `hit`: still said -- the hit's sting rings ~2.6 s and her line
            # comes over it; the page silences what plays. Owner 25 Nov ~03:20: "ตอนหุ่นพูด เพลงไม่ดับ")
            if now is not None:
                now["duck"] = op == "duck"
        else:
            _state["catwalk"] = None
            # (a screen that blinks during the handshake keeps the pictures the hit lets linger -- review 25 Nov)
            _state["catwalk_hit_at"] = time.monotonic() if op == "hit" else None
        out = {"type": "catwalk", "op": op}
    if _clients:
        _put(json.dumps(out))
    return True


def _catwalk_join() -> list[str]:
    """For a screen that joins: the files to have ready, and what should be playing -- `off` too, for a screen
    back from a blink that still plays what was stopped meanwhile."""
    if not settings.catwalk_music:
        return []
    from app import catwalk_music

    files = catwalk_music.addresses()
    if not files:
        return []
    now = _state["catwalk"] if show_mode() else None
    out = {"type": "catwalk", "op": "walk", "back": True, **now} if now else {"type": "catwalk", "op": "off"}
    hit_at = _state.get("catwalk_hit_at")
    if not now and show_mode() and hit_at is not None and time.monotonic() - hit_at < CATWALK_LINGER_S:
        out["linger"] = True
    return [json.dumps({"type": "catwalk_files", "files": files, "gallery": catwalk_music.gallery()}), json.dumps(out)]


def call_started() -> None:
    # (no line of the call before: a call replaced mid-reading never says call_ended)
    _drop_live_voice()
    _purge_voice()
    _state.update(in_call=True, story=None, subtitle=None, motion=None, ahead=None, ahead_prev=None)
    if _clients:
        _put(json.dumps({"type": "mirror_start"}))


def call_ended() -> None:
    _stopped("call_ended")
    _purge_voice()
    _state.update(in_call=False, story=None, subtitle=None, motion=None, ahead=None, ahead_prev=None)
    if _clients:
        _put(json.dumps({"type": "mirror_end"}))


def snapshot() -> list[str]:
    """What a screen that joins now needs: is there a call, at what rate is
    her voice, and the story on the robot's screen (it lands on its last
    frame: no voice of it will come)."""
    return _join(time.monotonic(), None)[0]


def resume_asked(params) -> tuple[str, int, int] | None:
    """/ws/mirror's `line` + `have`: a screen back from a Wi-Fi blink still playing that recorded line,
    holding its first `have` bytes of the play numbered `tok` (voice-preview.html startMirror). None = an
    ordinary join."""
    try:
        line, have, tok = params.get("line"), int(params.get("have") or -1), int(params.get("tok"))
    except (AttributeError, TypeError, ValueError):
        return None
    if not isinstance(line, str) or not line or len(line) > 64 or not 0 <= have <= AHEAD_MAX_BYTES or tok < 1:
        return None
    return line, have, tok


def _join(now: float, resume: tuple[str, int, int] | None) -> tuple[list[str], list]:
    """`snapshot`'s messages, and her voice of the recorded line being said for the screen to queue right
    behind them: from where the robot is in it (its subtitle's `late`) -- or, for a screen back from a blink
    that still plays it (`resume`), from where its own copy of it ends, its subtitle not sent again."""
    hello: dict = {"type": "mirror_hello", "in_call": _state["in_call"]}
    voice: list = []
    a = _state["ahead"]
    resumed = None
    if _state["in_call"]:
        # what a screen back from a blink needs to decide what it plays on (only in a call, only when known)
        for key, rec in (("voice", a), ("voice_prev", _state["ahead_prev"])):
            state = _voice_state(rec, now)
            if state is not None:
                hello[key] = state
        if _state["subtitle"]:
            hello["line"] = _state["subtitle"].get("id")
        if (resume is not None and a is not None and a["at"] is not None and a["state"] != "cut"
                and resume[0] == a["id"] and resume[2] == a.get("tok")):
            resumed = a["id"]
            voice = _voice_items(a, resume[1])
    out = [json.dumps(hello)]
    if _state["in_call"]:
        ready = {"type": "ready"}
        if _state["rate"]:
            ready["output_rate"] = _state["rate"]
        if _state["profile"]:
            ready["profile"] = _state["profile"]
        if _state["profile"] == "ceremony" and settings.ceremony_voice_on_robot:
            ready["voice_on_robot"] = True
        out.append(json.dumps({"type": "mirror_start"}))
        out.append(json.dumps(ready))
        if _state["story"]:
            out.append(json.dumps(_state["story"], ensure_ascii=False))
        held = _state["subtitle"]
        late = None
        if held and _state["subtitle_voice_at"] is not None:
            late = round(max(0.1, now - _state["subtitle_voice_at"]), 1)
        if held and resumed is not None and held.get("id") == resumed:
            pass                               # still on that screen, its clock running: not again
        elif held:
            if late is not None:
                # a screen joining mid-line: how many seconds of her voice of this line
                # are already gone, so its slide picks the beats up from there -- never
                # from the start (late), never at the end state (review 17 Nov: a joke's
                # punchline or a "done" tick ahead of her words). A line with no slide too
                # (24 Nov): her voice of it now comes from there (`voice` below), and its
                # words must not start over from their first piece under it.
                held = {**held, "late": late}
            out.append(json.dumps(held, ensure_ascii=False))
        elif _state["motion"]:
            out.append(json.dumps(_state["motion"], ensure_ascii=False))     # "ตอบสด"'s topic slide
        live = _voice_state(a, now)
        if resumed is None and live is not None and live["state"] == "live":
            # joining while a recorded line is being said: her voice of it from where the robot is -- the
            # very seconds its subtitle's `late` says are gone (a line without a subtitle: from its start time)
            gone = late if late is not None and held and held.get("id") == a["id"] else now - a["at"]
            voice = _voice_items(a, int(gone * _rate()) * 2)
    if _state["clips"]:
        out.append(json.dumps({"type": "mirror_clips", "clips": _state["clips"]}))
    if _state["clip"]:
        out.append(json.dumps(_state["clip"]))
    # a screen back from a Wi-Fi blink plays its debut on through it (the page does not stop on a closed
    # socket): told to stop when the server's debut is over or was taken down meanwhile
    until = _state.get("reveal_until")
    if until is not None and time.monotonic() >= until:
        _state["reveal_until"] = None
        until = None
    if until is None and _state.get("reveal_sent"):
        out.append(json.dumps({"type": "ceremony_reveal_stop"}))
    out += _catwalk_join()
    return out, voice


async def serve(ws, kind: dict | None = None, *, film: bool = False) -> None:
    """One screen, after its gate (mirror_server): the snapshot, then the
    stream, until either side goes. Incoming frames are read and dropped.
    `kind`: what the screen said it is (kind_from_query). `film`: the robot's own chest-screen frame
    (app/face_film.py) -- it gets the stream, and is not counted as a screen of the hall."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
    texts, voice = _join(time.monotonic(), resume_asked(getattr(ws, "query_params", None) or {}))
    # Registered BEFORE the snapshot goes out, with no await between the two: whatever the robot
    # publishes from this moment waits in the queue behind the snapshot. Sent first and registered
    # after, a frame published while the snapshot was on its way was lost -- with a recorded line's
    # voice sent whole at its first word, that was the whole line.
    _clients.add(queue)
    if film:
        _film.add(queue)
    if kind is not None:
        _kinds[queue] = kind
    for item in voice:                         # her voice of the line being said, right behind the snapshot
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            break

    async def drain_incoming() -> None:
        while True:
            message = await ws.receive()
            if message.get("type") == "websocket.disconnect":
                return

    reader = None
    try:
        for item in texts:
            await ws.send_text(item)
        reader = asyncio.create_task(drain_incoming())
        while not reader.done():
            getter = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait({getter, reader}, return_when=asyncio.FIRST_COMPLETED)
            if getter not in done:
                getter.cancel()
                break
            item = getter.result()
            if isinstance(item, bytes):
                await ws.send_bytes(item)
            else:
                await ws.send_text(item)
    except Exception:
        pass                                   # the screen went: nothing to report
    finally:
        _clients.discard(queue)
        _kinds.pop(queue, None)
        _film.discard(queue)
        if reader is not None:
            reader.cancel()


def reset() -> None:
    """Tests: no screens, no call."""
    _clients.clear()
    _kinds.clear()
    _film.clear()
    _state.update(in_call=False, rate=None, profile=None, story=None, clips=None, clip=None, subtitle=None,
                  reveal_until=None, reveal_sent=False, motion=None, ahead=None, ahead_prev=None, catwalk=None)
    _dropped.update(count=0, logged=False)
