"""The link to the Astronaut robot's Android app, and its mock.

The SDK (`aoborobotsdk-v2.0.aar`, `AoboRobotManager`) is Android-only, and it
reaches the robot's navigation board over the robot's own internal network —
`quickConnect()` goes to 192.168.11.1:1445. A FastAPI server on a PC cannot
call it. Something running on the robot has to.

So the split is: this server decides *what* the robot should do, and the app on
the robot's chest screen does it. The question is how the two talk.

**Down the WebSocket that is already open**, not through a new port on the
robot. The robot app is already connected to `/ws` — it is the voice client,
sending microphone audio up and receiving audio and `{"type": "slide"}` down.
Adding `{"type": "robot", ...}` to that stream costs nothing, needs no inbound
connection to the robot, and survives the robot being on Wi-Fi with a
changing address. A second channel would be a second thing to authenticate,
reconnect and debug, and it would be able to disagree with the first about
which robot it is talking to.

**Mock is the default, and says so.** Same rule as `broadlink_ir`: with no
robot connected, commands succeed logically and change nothing physically, and
every result carries `hardware: "mock"` so the model can tell a guest the truth
rather than cheerfully announcing a journey that never happened. That mistake
has already been made once in this project — `_worst()` in `smarthome.py`
exists because "mock" was being reported as "ok".
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("condo_voice.robot")

#: Points of interest the robot knows, mirrored from the robot's own map.
#:
#: The robot is the authority — POIs are created on it with
#: `addPointAtCurrentPose(name)` by walking it there once — so this is a cache,
#: refreshed by the app on connect. Empty until a real robot reports in, which
#: is why `go_to_place` can only fail politely rather than guess.
KNOWN_PLACES: list[str] = []

#: What the robot last told us it was doing. Like `smarthome.STATE`, this is
#: what we last *sent* plus whatever the app reported back, not measured truth.
STATE: dict[str, Any] = {
    "connected": False,
    "moving": False,
    "destination": None,
    "battery": None,
    "charging": None,
}


def snapshot() -> dict:
    return dict(STATE)


def available() -> bool:
    """True only when a real robot app is connected and has said it is ready.

    Deliberately not "is a websocket open". The voice client in a browser is
    also on that socket, and it has no arms.
    """
    return bool(STATE["connected"])


def status() -> dict:
    """Why the robot is or isn't usable — for /health and startup logging."""
    from app.config import settings

    return {
        "enabled": settings.robot_enabled,
        "app_connected": bool(STATE["connected"]),
        "places_known": len(KNOWN_PLACES),
        "usable": settings.robot_enabled and available(),
    }


def reset_state() -> None:
    """One definition, so tests and code cannot drift apart.

    `slides.reset_state()` exists for the same reason, after a hand-written
    reset in the test fixture fell behind the real state and left tests
    depending on which one ran first.
    """
    STATE.update({
        "connected": False, "moving": False, "destination": None,
        "battery": None, "charging": None,
    })
    KNOWN_PLACES.clear()


def app_connected(places: list[str] | None = None) -> None:
    """The robot app reported in, with the POIs its map actually contains."""
    STATE["connected"] = True
    KNOWN_PLACES.clear()
    KNOWN_PLACES.extend(places or [])
    logger.info("robot app connected — %d places on its map: %s",
                len(KNOWN_PLACES), ", ".join(KNOWN_PLACES) or "(none)")


def app_gone() -> None:
    """The socket closed. Everything below this is now a guess, so stop
    claiming otherwise — a robot that is still walking when the link drops
    will keep walking, and the honest state for us is "unknown"."""
    if STATE["connected"]:
        logger.info("robot app disconnected")
    reset_state()


def find_place(request: str) -> str | None:
    """Match what the guest asked for against the robot's own POI names.

    **Every word of the destination's name must appear in the request.** Not
    "the best overlap" — the whole name.

    The first version scored on shared tokens and picked the highest, which is
    how `documents.find` works, and it sent someone asking for ห้องน้ำชั้นสาม
    to ห้องตัวอย่าง: both contain ห้อง, that was one shared token, and one was
    more than zero. Worse, it used `robust_tokens`, which mixes in character
    n-grams to widen search recall — so the two strings also matched on `ห้อง`,
    `ห้อ` and `้อง` and scored 3. That is exactly the failure the deck search
    already learned from (`ราคา` matching `อาคาร` through the run `าคา`),
    reappearing because a function built for recall got reused where the cost
    of a wrong answer is completely different.

    A wrong document prints a page nobody wanted. A wrong destination walks a
    customer across the gallery, and by the time anyone notices, they are
    already there. So this errs the other way: no match is a question the
    robot asks out loud, which costs one sentence.
    """
    from app.tools.retrieval import tokenize

    wanted = set(tokenize(request or ""))
    if not wanted:
        return None

    matches = [
        place for place in KNOWN_PLACES
        if (words := set(tokenize(place))) and words <= wanted
    ]
    if not matches:
        return None
    # Two POIs can both be contained in one request when one name contains the
    # other ("ห้องตัวอย่าง" inside "ห้องตัวอย่าง 2 ห้องนอน"). The longer name
    # is the more specific request, so it wins.
    return max(matches, key=lambda place: len(set(tokenize(place))))


async def send(action: str, **args: Any) -> str:
    """Ask the robot app to do something. Returns "ok", "mock", or "failed".

    Fire-and-forget on purpose, and this is the important part.

    Navigation takes half a minute. Function calling is synchronous — the model
    produces nothing until the tool returns — so waiting here for the robot to
    arrive would gag it for the entire walk, with a guest alongside. That exact
    bug has already happened in this project: `start_presentation` waited on a
    browser window and the robot fell silent for four seconds in front of
    somebody who had just asked to see something.

    So the tool returns immediately with "on my way", and arrival comes back
    later through `arrived()` as its own turn — the same shape as the
    `resumed` event and `follow_canva`.
    """
    from app.config import settings

    if not settings.robot_enabled:
        return "mock"

    from app import session as session_module

    live = session_module._active
    if live is None or not available():
        logger.info("robot command %r ran in mock mode — no robot app connected", action)
        return "mock"

    try:
        await live._send_json({"type": "robot", "action": action, "args": args})
    except Exception:
        logger.exception("could not send robot command %r", action)
        return "failed"
    return "ok"


async def arrived(place: str | None, ok: bool = True) -> None:
    """The robot finished moving. Tell the model, so it can say so out loud.

    Injected as a turn rather than left in state, because nothing else would
    ever read it: the model is not polling, and after a silent tool result
    there is no turn boundary coming. `_resume_after_silence` in `session.py`
    exists for the same reason — a robot waiting for something that will never
    fire is the failure this project keeps rediscovering.
    """
    STATE["moving"] = False
    STATE["destination"] = None

    from app import session as session_module

    live = session_module._active
    if live is None or live.provider is None:
        return

    if ok:
        text = ("ถึง%s แล้ว ให้บอกลูกค้าสั้นๆ ว่าถึงแล้ว "
                "แล้วคุยต่อตามปกติ" % (place or "ที่หมาย"))
    else:
        # Naming the likely cause matters: "ไปไม่ได้" tells a guest nothing
        # and invites the model to invent a reason.
        text = ("ไป%s ไม่สำเร็จ อาจมีสิ่งกีดขวางหรือหาทางไม่เจอ "
                "ให้บอกลูกค้าตรงๆ แล้วเสนอให้เดินไปเองหรือเรียกเจ้าหน้าที่"
                % (place or "ที่หมาย"))
    try:
        await live.provider.send_text(text)
    except Exception:
        logger.exception("could not report arrival to the model")
