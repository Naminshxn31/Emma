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
import json
import logging
import re

logger = logging.getLogger("condo_voice.mirror")

#: Frames queued per screen before it is dropping. Her voice is ~10 chunks a
#: second; a LAN screen that is 40 s behind is not watching this call.
QUEUE_MAX = 400
#: State changes the face shows, forwarded as their bare type.
_STATES = frozenset({"speech_started", "interrupted", "turn_complete", "heard_end"})

#: An answer picture's address as the session sends it (tools/slides._public:
#: "/slides/<file>"): one file name of the deck's own images, nothing else.
_SLIDE_URL = re.compile(r"^/slides/[A-Za-z0-9_.-]+\.(?:jpe?g|png|webp|gif|avif)$", re.I)

_clients: set[asyncio.Queue] = set()
_state: dict = {"in_call": False, "rate": None, "profile": None, "story": None}
_dropped = {"count": 0, "logged": False}


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
        return out
    if kind == "tool_result":
        result = payload.get("result")
        if not isinstance(result, dict):
            return None
        screen = result.get("screen")
        if screen == "story" and isinstance(result.get("story"), dict):
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


def publish_json(payload: object) -> None:
    """A message the robot face's call just sent its page."""
    out = view(payload)
    if out is None:
        return
    if out["type"] == "ready":
        _state["rate"] = out.get("output_rate") or _state["rate"]
        _state["profile"] = out.get("profile") or _state["profile"]
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
    if _clients and chunk:
        _put(bytes(chunk))


def call_started() -> None:
    _state.update(in_call=True, story=None)
    if _clients:
        _put(json.dumps({"type": "mirror_start"}))


def call_ended() -> None:
    _state.update(in_call=False, story=None)
    if _clients:
        _put(json.dumps({"type": "mirror_end"}))


def snapshot() -> list[str]:
    """What a screen that joins now needs: is there a call, at what rate is
    her voice, and the story on the robot's screen (it lands on its last
    frame: no voice of it will come)."""
    out = [json.dumps({"type": "mirror_hello", "in_call": _state["in_call"]})]
    if _state["in_call"]:
        ready = {"type": "ready"}
        if _state["rate"]:
            ready["output_rate"] = _state["rate"]
        if _state["profile"]:
            ready["profile"] = _state["profile"]
        out.append(json.dumps({"type": "mirror_start"}))
        out.append(json.dumps(ready))
        if _state["story"]:
            out.append(json.dumps(_state["story"], ensure_ascii=False))
    return out


async def serve(ws) -> None:
    """One screen, after its gate (mirror_server): the snapshot, then the
    stream, until either side goes. Incoming frames are read and dropped."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
    for item in snapshot():
        await ws.send_text(item)
    _clients.add(queue)

    async def drain_incoming() -> None:
        while True:
            message = await ws.receive()
            if message.get("type") == "websocket.disconnect":
                return

    reader = asyncio.create_task(drain_incoming())
    try:
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
        reader.cancel()


def reset() -> None:
    """Tests: no screens, no call."""
    _clients.clear()
    _state.update(in_call=False, rate=None, profile=None, story=None)
    _dropped.update(count=0, logged=False)
