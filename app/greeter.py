"""
The camera as a second way in: somebody walks up, the robot opens its mouth.

`facewatch.Watcher` answers "is there somebody there, and is it anybody we
know". This module owns the parts that decision cannot: a camera handle, a
clock, and the one road to the model. It is deliberately the only place the
two meet — `facewatch` says in its own docstring that it does not talk to
the model or hold a socket, and that stays true.

Why this is not just "wake on a face"
-------------------------------------
There is already a way in: the wake word. It has a measured ceiling — about
half the calls that miss never reach the detector at all, too quiet or too
far, and no threshold fixes a sound that is not there. A camera fails in
completely different conditions to a microphone, which is the whole reason
to add one. It is a second door, not a better lock.

What it costs, and why it is off by default
-------------------------------------------
Every sighting that gets through opens a Gemini session. That is the exact
thing the wake word exists to prevent — "เปิดหน้าเว็บทิ้งไว้ทั้งวันไม่เผา
quota" — so a camera pointed at a corridor with `FACE_ENABLED=true` is a
bill. Three gates stand in front of it and all three are in `facewatch`:
a face has to be close enough (`FACE_MIN_PX`), the same answer has to
arrive several frames running (`FACE_CONFIRM_FRAMES`), and the same person
cannot do it twice inside `FACE_COOLDOWN_S` (ten minutes). The switch
itself is off, and the showroom default must stay that way.

Why the greeting is a summon and not a `send_text`
--------------------------------------------------
When somebody walks up there is usually no session at all — the line is
asleep waiting for a name. `events.announce(summon=True)` is exactly this
case: it rings the standby browser, waits for it to dial, and delivers the
text on the fresh line. Everything the four earlier event sources learned
the hard way (wait for the audio queue, re-check afterwards, one at a time,
never deliver to the next guest) comes with it for free. Writing a second
path here would be the four-exits bug with a camera attached.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from app import turnlog
from app.config import settings

logger = logging.getLogger("condo_voice.greeter")

#: Held so the loop is not garbage collected, and so shutdown can stop it.
_task: asyncio.Task | None = None

#: How long to wait after a camera read comes back empty before giving up on
#: the camera entirely. A USB camera unplugged mid-day should not spin a
#: thread reading None forever, and it should say so once.
MAX_EMPTY_READS = 30


def greeting_for(sighting) -> str:
    """What to send to the model when somebody is standing there.

    Instructions, not words to repeat: the model chooses its own sentence,
    the same as every other announcement. Two things are named explicitly
    because the model reaches for both on its own and both are wrong in a
    showroom — narrating the camera ("ระบบจดจำใบหน้าตรวจพบ..."), which is
    unsettling to hear from a robot you have just walked up to, and telling
    a stranger that it does not know them, which is an accusation rather
    than a greeting.
    """
    if sighting.kind == "known":
        who = sighting.name
        role = sighting.group
        return (
            "%s เพิ่งเดินเข้ามาหน้าหุ่น%s — ทักทายด้วยชื่อสั้นๆ แล้วถามว่า"
            "ให้ช่วยอะไรไหม ห้ามพูดถึงกล้อง ห้ามพูดว่า "
            "'ระบบจดจำใบหน้าตรวจพบ' หรือ 'สแกนเจอ'"
            % (who, (" (%s)" % role) if role else "")
        )
    return (
        "มีคนเดินเข้ามายืนหน้าหุ่น ยังไม่รู้ว่าเป็นใคร — ทักทายสั้นๆ "
        "แล้วถามว่าให้ช่วยอะไรไหม ห้ามพูดถึงกล้อง และห้ามบอกว่าจำไม่ได้"
        "หรือไม่รู้จัก"
    )


def _say_who_is_missing(watcher) -> None:
    """Name anybody enrolled from the camera who is not in the gallery yet.

    Enrolling and rebuilding are deliberately two steps — the gallery is what
    the robot says names out of, and it should not change because somebody
    was practising at the station. But the gap between them is invisible from
    the doorway: standing in front of the camera and not being recognised
    looks exactly like a threshold that is too high, and the answer is often
    "nobody ran build_face_gallery.py". A name in the boot log costs nothing
    and answers it before it is asked.
    """
    from app import enrollment

    try:
        known = set(watcher.gallery.names) if watcher.gallery is not None else set()
        missing = [name for name, _ in enrollment.enrolled() if name not in known]
    except Exception:                              # pragma: no cover - belt
        return
    if missing:
        logger.warning(
            "enrolled but not in the gallery: %s — run "
            "build-face-gallery.cmd (or python scripts/build_face_gallery.py) "
            "or they will be greeted as strangers", ", ".join(missing))


async def _greet(sighting) -> None:
    turnlog.record("face_seen", kind=sighting.kind, name=sighting.name,
                   score=round(sighting.score, 3), group=sighting.group)
    from app import events

    await events.announce(
        greeting_for(sighting),
        source="face_known" if sighting.kind == "known" else "face_stranger",
        summon=True,
    )


async def _open_camera_patiently(camera):
    """Open the camera, retrying on FACE_CAMERA_RETRY_S until it works.

    Returns None only when retrying is switched off. Says something on the
    first failure and then about once a minute — a line every ten seconds
    for a camera that is unplugged all weekend is noise, silence is the bug
    this replaces.
    """
    attempt = 0
    while True:
        cap = await asyncio.to_thread(camera.open_camera)
        if cap is not None:
            if attempt:
                logger.info("camera is back after %d attempt(s)", attempt)
            return cap
        if settings.face_camera_retry_s <= 0:
            logger.warning("camera greeting off — no camera opened "
                           "(try: python scripts/watch_camera.py --list)")
            return None
        if attempt % 6 == 0:
            logger.warning("no camera opened — retrying every %.0fs "
                           "(try: python scripts/watch_camera.py --list)",
                           settings.face_camera_retry_s)
        attempt += 1
        await asyncio.sleep(settings.face_camera_retry_s)


async def run() -> None:
    """Watch until cancelled. Never raises into the server that started it."""
    import time

    from app import camera, facewatch, wake
    from app import session as session_module

    watcher = facewatch.Watcher.from_settings()
    if watcher is None:
        # from_settings has already said which piece is missing. No camera is
        # opened at all: a lens held open to feed a decision nobody can make
        # is a light on the front of the machine for no reason.
        logger.info("camera greeting off — nothing to recognise with")
        return

    _say_who_is_missing(watcher)

    cap = await _open_camera_patiently(camera)
    if cap is None:
        return

    interval = 1.0 / max(settings.face_fps, 0.5)
    empty = 0
    last_ring = 0.0
    logger.info("camera greeting on — %.1f fps, threshold %.2f, cooldown "
                "%.0fs. The enrolment station cannot open the same camera "
                "while this is running.", settings.face_fps, watcher.threshold,
                watcher.cooldown_s)
    try:
        while True:
            ok, frame = await asyncio.to_thread(cap.read)
            if not ok or frame is None:
                empty += 1
                if empty >= MAX_EMPTY_READS:
                    # Not the end. A camera that stops delivering is a
                    # USB hiccup or somebody else holding the device, and
                    # both end on their own — the loop used to end with
                    # them, silently, until the next restart. Release the
                    # dead handle and try again on a timer.
                    with contextlib.suppress(Exception):
                        cap.release()
                    if settings.face_camera_retry_s <= 0:
                        logger.warning("camera stopped returning frames — "
                                       "greeting off until the server restarts "
                                       "(FACE_CAMERA_RETRY_S=0)")
                        return
                    logger.warning("camera stopped returning frames — "
                                   "reopening every %.0fs", settings.face_camera_retry_s)
                    cap = await _open_camera_patiently(camera)
                    if cap is None:
                        return
                    empty = 0
                    continue
                await asyncio.sleep(interval)
                continue
            empty = 0

            # Recognition off the event loop: it is 100-200ms of ONNX per
            # frame with somebody in front of the lens, and this process is
            # also carrying a live audio session. The measured cost at
            # FACE_THREADS=2 is 18% of the machine with a face in frame,
            # 8% without.
            sighting = await asyncio.to_thread(watcher.see, frame)

            # Ring the standby browser the moment somebody is close enough,
            # not after they are confirmed: confirming takes ~1.2s and the
            # Gemini dial 2-3s, and run in sequence the robot greeted
            # people's backs. A ring with a session already up costs
            # nothing (no summon fires), and a face that leaves before
            # confirming costs one aborted dial — cheaper than a greeting
            # nobody stayed to hear.
            now_mono = time.monotonic()
            if (watcher.someone_at is not None
                    and now_mono - watcher.someone_at < 1.0
                    and session_module._active is None
                    and now_mono - last_ring > 15.0):
                last_ring = now_mono
                rang = await wake.summon(reason="face_approach")
                if rang:
                    logger.info("somebody close — pre-ringing %d standby "
                                "browser(s) while confirming", rang)

            if sighting is not None and sighting.kind == "stranger" \
                    and not settings.face_greet_strangers:
                # Read after `see`, not before: the cooldown it just set is
                # what stops the next thirty frames asking the same
                # question, and skipping the call would also skip that.
                logger.info("face: stranger (%.3f) — not greeting strangers",
                            sighting.score)
                sighting = None
            if sighting is not None and sighting.kind == "stranger" \
                    and session_module._active is not None:
                # A stranger wakes the line up; a stranger does not cut into
                # a conversation. Measured 2026-09-01, one session: three
                # "strangers" at 0.355 / 0.469 / 0.478 — every one of them
                # one of the two people already talking to Emma, caught
                # turning their head (the threshold is 0.50). Each landed
                # as a user turn mid-sentence and Emma broke off to say
                # "สวัสดีค่ะ มีอะไรให้ช่วยไหมคะ" to nobody. A known face is
                # different: "หวัดดี พีท" mid-conversation carries a name
                # somebody may want to hear. A stranger's greeting carries
                # nothing the conversation did not already have.
                logger.info("face: stranger (%.3f) during a live session — "
                            "not interrupting", sighting.score)
                turnlog.record("face_seen", kind="stranger",
                               score=round(sighting.score, 3), group="",
                               greeted=False, why="live_session")
                sighting = None
            if sighting is not None:
                logger.info("face: %s %s (%.3f)", sighting.kind,
                            sighting.name or "-", sighting.score)
                await _greet(sighting)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        raise
    except Exception:
        # A camera loop that dies must not take the showroom with it, and
        # must not die quietly either.
        logger.exception("camera greeting stopped")
    finally:
        with contextlib.suppress(Exception):
            cap.release()


def start() -> None:
    """Begin watching, if this machine is meant to.

    Called from the server's startup hook. Not from `Watcher.__init__` and
    not on the first frame: starting hardware from inside something that
    looks like a constructor is how the deck warm-up ended up inside the
    path a waiting conversation ran through.
    """
    global _task
    if not settings.face_enabled or _task is not None:
        return
    _task = asyncio.create_task(run())


async def stop() -> None:
    global _task
    if _task is None:
        return
    task, _task = _task, None
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
