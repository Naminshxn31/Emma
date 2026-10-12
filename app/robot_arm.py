"""Talking to the upper-body servo board over the robot's own serial port.

The board is a Torobot servo controller — the Astronaut manual says so in the
*Upper body joint control panel* section ("Controlled using Torobot programming
software"), which is what took this off the list of things only the vendor
could unlock. Its protocol is plain text on a serial line:

    #1P1500T3000\\r\\n     channel 1 to pulse 1500, taking 3000 ms
    #7GC1\\r\\n            run stored action group 7, once

Three facts about that board decide the entire shape of this module, and each
one of them is a thing the code has to refuse to pretend about:

**The robot's own red button reaches the arm through the vendor app.** Its
recovered code takes the E-stop as an IO signal and, with
`estopStopArmMovement` on (it is on, in this robot's saved settings), writes
`#STOP` to the arm board itself. Whether the button *also* cuts servo power
in hardware is unknown. The consequence for this module is uncomfortable and
has to be said on every page that uses it: the way we obtain the port is to
stop that app, so while this module holds the port the button's only *known*
path to the arm is gone. That is why finding the power cut is not optional.

**There is a stop frame, and nobody has watched it work.** The published
Torobot command table has only "go to a position" and "play a group", which is
why this file used to say the board had no stop at all. Recovering the vendor
app's own code on 2026-09-11 found `#STOP\\r\\n` in its stop helper and a
parser that clears `isArmStartAction` on `#STOP+OK`, so the frame exists and
the board is expected to acknowledge it. `stop()` therefore sends it — a stop
that exists and is unproven beats no stop at all — and *also* latches this
module disarmed, and the page still says the certain stop is the servo power
supply, which the manual puts on its own circuit separate from the control
board. What the page must never say is that pressing it stopped the arm.
Software that promises to halt a moving arm is making a promise it cannot keep
on evidence it does not have, and a button labelled "หยุด" that somebody trusts
instead of the switch is worse than no button.

**A PWM servo cannot be asked where it is.** Nothing comes back on the wire.
So the first line sent to any channel moves it from a position we do not know
to one we chose, and that is true no matter how careful the rest of the code
is. The only honest handling is to make that first move explicit and the
slowest the protocol allows (`centre()`), and to refuse every relative step
until it has happened — `_commanded` is not a cache of where the joint is, it
is a record of what we last told it, which is the only thing we actually know.

**Nobody has confirmed which channel is which joint.** The app's own servo
picker offers 1, 11, 7 and 8, so those four are the only ones this module will
address. Not because the board has four channels — the manual says twenty —
but because those four are the only numbers with any evidence behind them, and
a control surface that will happily address channel 14 on a robot whose wiring
map nobody has is a control surface for a joint nobody can predict.

The manual controls remain a desk instrument. A fixed model tool can request
only the verified handshake group from the Android robot face when a person
asks to shake hands. A separate server-owned path requests the same group
alongside a camera greeting. Both still pass the arm interlocks; neither can
arm the module or loosen a gate.
`ROBOT_ARM_ENABLED` is false by default and gates every write.

Since the voice body/nav work (docs/emma-body-nav-design-2026-10-08.md §4)
the serial path is the *second* writer: while ROBOT_BODY_ENABLED or
ROBOT_NAV_ROUTES_ENABLED is on, or the robot face reports a walk, every
serial mover refuses with `app_owns_arm_port` (`app_owns_port()`). `#STOP`
never does, and `stop()` also asks the app for its native stop.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from app import adb_target, robot_face_link
from app.config import settings

logger = logging.getLogger(__name__)

#: The absolute pulse range the Torobot protocol defines. Nothing may leave it.
PULSE_MIN = 500
PULSE_MAX = 2500
PULSE_CENTRE = 1500

#: One press. Server-side and not negotiable by the page, for the same reason
#: `_NUDGE_METRES` is: the client is a web page, and a client that names its
#: own step size is one typo away from driving a joint into its stop.
STEP = 40

#: Milliseconds for one step, and for the blind opening move. `T` is travel
#: time, so a larger number is slower. The opening move uses the slowest the
#: protocol defines — 9999, the top of the documented 100-9999 range — because
#: we do not know how far that first move is about to travel, and "the slowest
#: available" is the only defensible choice when the distance is unknown. It
#: was 3000 until somebody checked the range and found that 3000 is merely
#: slow, not slowest.
STEP_MS = 800
SLOW_MS = 9999

#: The only channels this module will address — the four the robot's own app
#: exposes in `servo_number_options`, and the same four its recovered code
#: holds as `ArmTestActivity.SERVO_NUMBERS = [1, 11, 7, 8]`. Note the order:
#: not sorted, and 1 and 11 are exactly ten apart on a twenty channel board,
#: which is where the untested left/right mirror hypothesis in
#: `docs/robot-command-research-2026-09-11.md` comes from. Four addressable
#: channels is not a claim that the arm has four motors — the vendor's own
#: "All" button just loops these four, and no wiring map exists for the rest.
CHANNELS = (1, 11, 7, 8)

#: The vendor app's stop helper, recovered from its code. Its receive parser
#: treats `#STOP+OK` as the acknowledgement. Nothing here waits for that ack:
#: this module never reads the port, so an ack would be unnoticed either way,
#: and a stop that waits for a reply is a stop that can hang.
STOP = "#STOP"
#: Gap between the two stop writes; the vendor helper uses 60 ms.
STOP_REPEAT_S = 0.06

#: The group the vendor's own `usbInit()` writes straight after opening the
#: port, and which its code uses as "return to the starting pose". Recorded
#: first as a warning: opening their app can move the arm on its own, and this
#: module must never imitate that — nothing is sent on connect. `greet()` does
#: send it, deliberately and *after* a greeting group it just played: a group
#: parks the arm at its final pose, and a board that cannot be asked where a
#: joint is cannot be asked to undo that either. The second greeting of
#: 2026-09-30 played group 6 from group 6's own end pose — the board answered
#: #AGF and nothing visibly moved ("มือไม่ขึ้น").
VENDOR_HOME_GROUP = 99

#: How long after the greeting group's write the serial path waits before the
#: rest-return. Group 6 was measured finishing within 13s (11× #CC then #AGF,
#: 2026-09-30) and the offered hand should stay out a few seconds for the
#: person to take it; this path cannot hear #AGF (nothing here reads the
#: port), so it waits the measured ceiling plus that hold.
GREETING_HOME_DELAY_S = 18.0

#: The pending rest-return, kept so a newer greeting replaces it instead of
#: racing it, and so the task is not garbage-collected mid-sleep.
_home_task: "asyncio.Task | None" = None

#: Stable identity of the upper-body controller. A tty number is not an
#: identity: CH340 has appeared as ttyUSB10 and ttyUSB11 after different USB
#: enumerations, while the neighbouring CP2102 took the other number.
ARM_USB_VID = "1a86"
ARM_USB_PID = "7523"
AUTO_PORT = "auto"
_TTY_PATH = re.compile(r"/dev/ttyUSB\d+")

#: What we last told each channel. Absent means "we have never spoken to this
#: channel in this session, so we know nothing about where it is".
_commanded: dict[int, int] = {}

#: Latched by `stop()`, cleared by `arm()`. Starts armed only in the sense
#: that `ROBOT_ARM_ENABLED` has to be on for any of this to run at all.
_armed = True

#: Every line actually written, newest last, for the page's log. Bounded so a
#: long session cannot grow it without limit.
_sent: list[str] = []
_SENT_KEEP = 40


def configured() -> bool:
    """Both switches on: the feature, and a port to write to."""
    return bool(settings.robot_arm_enabled and settings.robot_arm_port)


def port_description() -> str:
    """A human-readable configured target, without pretending auto resolved."""
    if settings.robot_arm_port.strip().lower() == AUTO_PORT:
        return f"CH340 ({ARM_USB_VID}:{ARM_USB_PID})"
    return settings.robot_arm_port


def armed() -> bool:
    return _armed


def reset_state() -> None:
    """Forget what we have said. Tests call this; so does `arm()`.

    Clearing `_commanded` is deliberately *not* the same as re-arming: after
    a restart we are back to knowing nothing about any joint, so the opening
    slow move has to be made again before any relative step is allowed.
    """
    global _armed, _home_task
    _commanded.clear()
    _sent.clear()
    _armed = True
    # A rest-return sleeping on a dead test loop is the module-level asyncio
    # leak conftest's _fresh_async_state exists for — never leave one behind.
    if _home_task is not None:
        _home_task.cancel()
        _home_task = None


def span() -> int:
    """How far from centre a channel may be driven, in microseconds.

    Small by default and widened only in `.env` by somebody who has watched
    the joint move. The point is that the total reachable travel is bounded
    before anybody presses anything, not after.
    """
    return max(0, min(int(settings.robot_arm_span), PULSE_MAX - PULSE_CENTRE))


def limits() -> tuple[int, int]:
    return PULSE_CENTRE - span(), PULSE_CENTRE + span()


async def _run(args: list[str], timeout: float) -> tuple[int, str]:
    """Run one adb command. Replaced wholesale in tests.

    Bytes in, decoded here: a device shell can answer in whatever encoding it
    likes, and on this owner's Windows a `text=True` would decode it as cp874
    and raise — which would read as "the arm is broken" rather than "a log
    line had a stray byte in it".
    """
    # A thread, not asyncio.create_subprocess_exec: under DEV_RELOAD uvicorn
    # runs a SelectorEventLoop on Windows, which cannot start a subprocess —
    # every wave failed with an empty NotImplementedError (app/adb_target.py).
    return await adb_target.run(args, timeout)


def _adb(shell_command: str) -> list[str]:
    args = [adb_target.executable(settings.robot_arm_adb)]
    if settings.robot_arm_adb_serial:
        args += ["-s", adb_target.current(settings.robot_arm_adb_serial)]
    return args + ["shell", shell_command]


async def _on_robot(shell_command: str) -> tuple[int, str]:
    """`_run(_adb(...))`, re-finding the robot once if adb no longer knows
    the name it was reached by (Wi-Fi adb names are IP:port, and the IP moves)."""
    timeout = settings.robot_arm_timeout_s
    code, out = await _run(_adb(shell_command), timeout=timeout)
    serial = settings.robot_arm_adb_serial
    if code and serial and adb_target.looks_missing(out):
        if await adb_target.find(adb_target.executable(settings.robot_arm_adb), serial, timeout):
            code, out = await _run(_adb(shell_command), timeout=timeout)
    return code, out


async def resolve_port() -> str:
    """Resolve `auto` to the one tty whose USB identity is the arm's CH340."""
    configured_port = settings.robot_arm_port.strip()
    if configured_port.lower() != AUTO_PORT:
        return configured_port

    command = (
        'for t in /sys/class/tty/ttyUSB*; do [ -e "$t" ] || continue; '
        'v=$(cat "$t/device/../../idVendor" 2>/dev/null); '
        'p=$(cat "$t/device/../../idProduct" 2>/dev/null); '
        f'if [ "$v:$p" = "{ARM_USB_VID}:{ARM_USB_PID}" ]; then '
        'echo /dev/${t##*/}; fi; done'
    )
    try:
        code, out = await _on_robot(command)
    except Exception as exc:
        # type name too: NotImplementedError's message is empty, and an empty
        # reason is how this line hid the real cause for a day.
        logger.info("arm: could not resolve the CH340 port: %s: %s", type(exc).__name__, exc)
        return ""
    matches = sorted(set(_TTY_PATH.findall(out))) if code == 0 else []
    if len(matches) != 1:
        logger.warning("arm: expected one %s:%s tty, found %s",
                       ARM_USB_VID, ARM_USB_PID, matches)
        return ""
    return matches[0]


async def port_present() -> bool:
    """Is the serial node there right now?

    Worth its own call because on 2026-09-10 it was not: the kernel log has
    both USB-serial converters attaching a second after boot and both
    disconnecting sixty seconds later, three milliseconds apart. A page that
    reports "sent" into a device node that does not exist would be the
    mock-reported-as-ok bug wearing a serial cable.
    """
    port = await resolve_port()
    if not port:
        return False
    try:
        code, out = await _on_robot("ls " + port)
    except Exception as exc:
        logger.info("arm: could not check the port: %s", exc)
        _forget_positions("the port could not be checked")
        return False
    present = code == 0 and "No such file" not in out
    if not present:
        # A port that is gone is a port somebody else may have. On 2026-09-11
        # the vendor app was restarted between two of our sessions; it took
        # the board back, ran its home pose, and left every joint somewhere
        # other than what this module last commanded. Stepping +40 from that
        # stale number would have been a fast move from an unknown start.
        _forget_positions("the port went away")
    return present


def _forget_positions(why: str) -> None:
    if _commanded:
        logger.info("arm: forgetting commanded positions — %s", why)
        _commanded.clear()


async def prepare() -> str:
    """Set the line discipline before the first write.

    The board auto-detects its baud rate within the range the Astronaut manual
    lists (9600 to 128000, which is why the SDK's 115200 is not the
    contradiction it first looked like), but the *host* port still has to be
    put into raw mode or the shell's line handling will mangle the CRLF the
    protocol requires.
    """
    # `stty raw` at our baud on a port the app holds would change the line
    # settings under the app's own SDK — a write to the port in all but name,
    # and the first thing the serial greeting does.
    _check_port_owner()
    port = await resolve_port()
    if not port:
        raise RuntimeError(f"ไม่พบ {port_description()}")
    code, out = await _on_robot("stty -F %s %d raw -echo" % (port, settings.robot_arm_baud))
    if code:
        raise RuntimeError(out or "ตั้งค่าพอร์ตแขนไม่สำเร็จ (adb returned %d)" % code)
    return "ok"


def app_owns_port() -> str | None:
    """Why this module must not write the arm port right now, or None.

    The Android app's SDK path is the single production path to the arm
    (design §4, "two writers on the arm port"): once the app has played a
    gesture it holds the CH340, and an adb `printf` from here is a second
    writer on the same board. Three things say the app owns it:

    - ROBOT_BODY_ENABLED — voice gestures go through the app;
    - ROBOT_NAV_ROUTES_ENABLED — the app rests the arms before every walk;
    - the robot face reports a walk `active` or `stale`, *whatever the
      switches say*: the camera greeter reaches `greet()` through
      events.announce for a non-face session, and on this machine every
      ROBOT_ARM_* flag is on with an adb serial set, so a greeting could
      have swung the arm mid-run with the body switch off (safety review #4).
      "stale" counts: a walk that stopped reporting is not a walk that ended.
    """
    if settings.robot_body_enabled:
        return "robot_body_enabled"
    # Also covers a walk the server cannot see (review 2, finding #3): with
    # routes on, a walk no face can vouch for reads "unknown" below, and
    # this branch already owns the port for it. Routes off, "unknown" stays
    # open, as before; a walk between calls is now reported by the face's
    # status link (active -> the branch below).
    if settings.robot_nav_routes_enabled:
        return "robot_nav_routes_enabled"
    if robot_face_link.nav_activity() in ("active", "stale"):
        return "robot_walking"
    return None


def _check_port_owner() -> None:
    why = app_owns_port()
    if why is not None:
        logger.info("arm: serial write refused — the robot app owns the arm port (%s)", why)
        raise PermissionError("app_owns_arm_port")


async def _write(line: str, ignore_latch: bool = False) -> None:
    """Put one protocol line on the wire.

    `printf` rather than `echo` because the trailing CRLF is mandatory and
    `echo` will not emit it portably; single quotes because an unquoted `#`
    or `&` in a device shell has bitten this project before — an unquoted `&`
    in an `am start` URL silently dropped a token and opened an unauthenticated
    page on the robot.
    """
    if not configured():
        raise RuntimeError("ROBOT_ARM_ENABLED / ROBOT_ARM_PORT ยังไม่ได้ตั้ง")
    # Every mover — centre, step, run_group, the serial greeting and its
    # rest-return — writes through here, so the fence sits in the one
    # primitive rather than in each caller (the wheel-fence lesson, safety
    # review #3: a guard in one entry point missed most of the paths).
    # `#STOP` is the exception, keyed on the frame itself rather than on a
    # flag: a lock must never disable the brake.
    if line != STOP:
        _check_port_owner()
    if not _armed and not ignore_latch:
        raise PermissionError("disarmed")
    port = await resolve_port()
    if not port:
        raise RuntimeError(f"ไม่พบ {port_description()}")
    command = "printf '%s\\r\\n' > %s" % (line, port)
    code, out = await _on_robot(command)
    if code:
        raise RuntimeError(out or "adb returned %d" % code)
    _sent.append(line)
    del _sent[:-_SENT_KEEP]
    logger.info("arm: %s", line)


def _check_motion_allowed() -> None:
    """The interlock every mover passes through.

    Separate from the disarm latch: the latch is a session-scoped "stop
    sending" pressed by a human; this is a `.env` switch that says the
    hardware has no proven stop yet. Both have to be open for a joint to
    move, and neither touches `stop()`.
    """
    if not settings.robot_arm_motion_enabled:
        raise PermissionError("motion_locked")


def _check_channel(channel: int) -> None:
    if channel not in CHANNELS:
        raise ValueError("ช่อง %r ไม่อยู่ในรายการที่ยืนยันแล้ว %s"
                         % (channel, list(CHANNELS)))


async def centre(channel: int) -> int:
    """Drive the channel to pulse 1500, as slowly as the protocol allows.

    This *commands* a position. It does not read, find, or home anything —
    there is nothing to read — and the page must not call it "finding the
    start", because a name that implies a measurement would hide the one fact
    that matters here: the joint may swing a long way, and which way is
    unknown until somebody watches it.

    1500 is the centre of the protocol's 500-2500, which is not the same as
    the centre of any particular joint's travel. Nothing has established that
    it is a safe or even reachable position for all four channels; it is
    simply the least-committed number available before any measurement exists.
    The first press on each channel is the moment to have a hand on the servo
    power supply.

    The vendor's own code sends exactly this shape — `#<servo>P1500T3000` in
    its `sendSingleServoPowerOn` — which is mild corroboration that 1500 is a
    sane target for these four channels, and none at all that the joint will
    not swing to reach it. Their T is 3000; this uses 9999, the slowest the
    protocol defines, because they know which joint they are moving and we
    do not.
    """
    _check_motion_allowed()
    _check_channel(channel)
    await _write("#%dP%dT%d" % (channel, PULSE_CENTRE, SLOW_MS))
    _commanded[channel] = PULSE_CENTRE
    return PULSE_CENTRE


async def step(channel: int, direction: int) -> int:
    """One bounded step from the last position we commanded.

    Refuses outright if we have never commanded this channel. "Step from the
    last known position" is only meaningful when there is one, and quietly
    assuming centre would be inventing a number about a physical joint — the
    same class of mistake as a default for a missing field in `calc_roi`.
    """
    _check_motion_allowed()
    _check_channel(channel)
    if channel not in _commanded:
        raise LookupError("ยังไม่รู้ตำแหน่งของช่อง %d — กดสั่งไป 1500 ก่อน"
                          % channel)
    low, high = limits()
    target = _commanded[channel] + (STEP if direction >= 0 else -STEP)
    target = max(low, min(high, target))
    target = max(PULSE_MIN, min(PULSE_MAX, target))
    if target == _commanded[channel]:
        raise ValueError("ถึงขอบระยะที่อนุญาตแล้ว (%d-%d) กว้างขึ้นได้ที่ "
                         "ROBOT_ARM_SPAN" % (low, high))
    await _write("#%dP%dT%d" % (channel, target, STEP_MS))
    _commanded[channel] = target
    return target


async def run_group(group: int) -> None:
    """Play one stored action group, exactly once. Unbounded by design.

    **None of the limits in this module apply to this call.** `CHANNELS`
    bounds which joint a step may address and `span()` bounds how far it may
    travel; a stored group is a recording made by somebody else and may drive
    any of the board's twenty channels to anywhere in their range, at whatever
    speed was recorded. Pinning the cycle count to 1 bounds how many times
    that happens and nothing else — reading it as a safety limit was the
    mistake this docstring exists to prevent.

    So it has its own switch, `ROBOT_ARM_GROUPS_ENABLED`, off by default and
    separate from `ROBOT_ARM_ENABLED`. Turning on the stepping controls must
    not silently turn on a control whose extent nobody can state.

    Kept rather than deleted because a group recorded by whoever built this
    robot was, presumably, recorded within the real mechanics — which is a
    better bet than any number this module could compose, and the only route
    to the pose table that does not start with guessing. "Presumably" is
    doing real work in that sentence, which is why the switch exists.

    Afterwards every channel's last-commanded position is forgotten: the group
    moved things, and what it moved them to is not something we can ask.

    Two numbers recovered from the vendor's code belong here. Their own test
    screen sends `GC5` — five cycles — which is theirs to choose and not a
    reason to loosen the one pinned here. And group `VENDOR_HOME_GROUP` is
    what their `usbInit()` fires immediately after opening the port, so on
    their app the arm can move simply because something connected. Passing 99
    here is allowed, because it appears to be a return-to-rest pose and that
    is a reasonable thing to ask for deliberately — but it is never sent on
    connect, and this module writes nothing at all until a button is pressed.
    """
    _check_motion_allowed()
    if not settings.robot_arm_groups_enabled:
        raise PermissionError("groups_locked")
    if not 0 <= int(group) <= 255:
        raise ValueError("เลขกลุ่มท่าต้องอยู่ระหว่าง 0-255")
    await _write("#%dGC1" % int(group))
    # Not `stale = True` next to a kept value: a number that must not be used
    # is better gone than flagged, because the flag is the thing somebody
    # forgets to check. `step()` already refuses a channel it knows nothing
    # about, so clearing this reuses a guard that is already tested.
    _commanded.clear()


async def greet(session=None) -> bool:
    """Request the configured handshake/greeting group without risking speech.

    A greeting must still be heard if the robot is off, disconnected,
    disarmed, or motion-locked. Every path checks the same motion gates. The
    robot face asks its Android SDK to play the verified group; other clients
    use the direct serial path. Returning False is an observable skip, not an
    exception that can tear down the voice session.
    """
    if not settings.robot_greeting_gesture_enabled:
        return False
    group = settings.robot_greeting_arm_group
    try:
        # A freshly enumerated tty starts at 9600 baud in canonical mode.
        # The manual page prepares it explicitly, but automatic greetings
        # never went through that page. Do this only after the motion gates.
        _check_motion_allowed()
        if not configured():
            raise RuntimeError("ROBOT_ARM_ENABLED / ROBOT_ARM_PORT ยังไม่ได้ตั้ง")
        if not _armed:
            raise PermissionError("disarmed")
        if not settings.robot_arm_groups_enabled:
            raise PermissionError("groups_locked")
        if not 0 <= int(group) <= 255:
            raise ValueError("invalid greeting group")
        # The robot face uses the same Android SDK path as the operator's
        # confirmed handshake. A short adb write can be acknowledged by the
        # board without producing visible motion on this robot.
        headers = getattr(getattr(session, "ws", None), "headers", {})
        native_face = "EmmaRobot/" in headers.get("user-agent", "")
        if native_face and settings.robot_face_token and not robot_face_link.is_robot_face(session):
            # With ROBOT_FACE_TOKEN set, a user agent is a claim, not proof
            # (design §2a, the 1 Sep ROBOT_TOKEN fix applied to the face): a
            # desk browser holding WS_TOKEN must not reach the arm by
            # pretending to be EmmaRobot/. Unset, the user agent decides, as
            # before.
            raise PermissionError("not_robot_face")
        if native_face:
            if group != 6:
                raise ValueError("Android face handshake is verified only for group 6")
            await session.ws.send_text(json.dumps({"type": "arm_greeting", "group": 6}))
            logger.info("arm: greeting group 6 requested from Android face SDK")
            return True
        await prepare()
        await run_group(group)
    except Exception as exc:
        logger.info("arm: greeting group %s skipped: %s", group, exc)
        return False
    logger.info("arm: greeting group %s write accepted by adb (board motion unverified)", group)
    _schedule_home_after_greeting()
    return True


def _schedule_home_after_greeting() -> None:
    """Undo the greeting group's end pose once the gesture has played out.

    Only the serial path needs this — the Android face schedules its own
    rest-return on the board's #AGF (`MainActivity.scheduleGreetingHome`).
    A newer greeting cancels the older return so the two cannot interleave.
    `run_group` re-walks every gate when the timer fires, so a stop pressed
    during the wait leaves this as a logged skip, never a move.
    """
    global _home_task
    if _home_task is not None and not _home_task.done():
        _home_task.cancel()
    _home_task = asyncio.get_running_loop().create_task(_home_after_greeting())


async def _home_after_greeting() -> None:
    await asyncio.sleep(GREETING_HOME_DELAY_S)
    try:
        await run_group(VENDOR_HOME_GROUP)
        logger.info("arm: greeting rest-return group %d written", VENDOR_HOME_GROUP)
    except Exception as exc:
        logger.info("arm: greeting rest-return skipped: %s", exc)


async def stop() -> dict:
    """Send the board's stop frame, and stop sending anything else.

    Two things, in this order, and the order is the point. The latch is set
    *first*, before any I/O, so that a write which hangs or fails still leaves
    this module refusing everything afterwards — the half that cannot fail
    must not be waiting behind the half that can.

    Then `#STOP` goes out, past the latch we just set, because the latch is
    about refusing new movement and this is the opposite of new movement.

    What comes back is narrower than it is tempting to name it.
    `stop_write_ok` means one thing only: the shell on the robot ran the
    write and exited zero. It does not mean the bytes reached the board, that
    the board understood them, or that a joint stopped moving — three claims
    this module has no way to make. Nothing here reads the board's
    `#STOP+OK`, and even receiving that would only say the board heard us.
    An earlier version of this called the field `stop_sent`, which quietly
    asserted the first of those three.
    """
    global _armed, _home_task
    _armed = False
    # The latch would refuse the write anyway; cancelling as well means an
    # explicit stop leaves nothing at all waiting to move on a timer.
    if _home_task is not None:
        _home_task.cancel()
        _home_task = None
    logger.warning("arm: disarmed — further commands refused until re-armed")

    # The app's native stopAll (SDK stopArmAll) is a different stop from
    # `#STOP` over adb, and once the app holds the port it is the one that
    # reaches the board. Started before the serial writes and awaited after,
    # so neither waits behind the other — and sent whatever the switches or
    # the port configuration say.
    body_stop = _start_body_stop()
    out = await _serial_stop()
    out["body_stop"] = await _finish_body_stop(body_stop)
    return out


def _start_body_stop() -> "asyncio.Task | None":
    face = robot_face_link.current_face()
    if face is None:
        return None
    # request_stop, not request: the face page holds two sockets (its call
    # and its status link) and current_face() is just the newest claim —
    # after a reconnect that can be another EmmaRobot/ device, whose ack
    # used to end the stop before the walking robot heard it. request_stop
    # sends on every live face socket at once.
    return asyncio.get_running_loop().create_task(robot_face_link.request_stop(
        face, "body", settings.robot_body_ack_timeout_s))


async def _finish_body_stop(task: "asyncio.Task | None") -> dict | None:
    """What the face page answered: {"accepted", "reason"}, or None when no
    robot face was connected. "accepted" means the app queued its stop — not
    that any joint stopped, the same narrowness as `stop_write_ok`."""
    if task is None:
        return None
    try:
        ack = await task
    except Exception as exc:
        logger.warning("arm: body_command stop failed: %s", exc)
        return {"accepted": False, "reason": "disconnected"}
    return {"accepted": ack.get("accepted") is True, "reason": ack.get("reason")}


async def _serial_stop() -> dict:
    if not configured():
        return {"stop_write_ok": False, "error": "ไม่ได้ตั้งค่าพอร์ต"}
    # Twice, STOP_REPEAT_S apart, because that is what the vendor's own
    # `sendArmToRobotStop()` does (a helper re-sends after 60 ms). Their code
    # is the only evidence we have about this board's habits, and a stop
    # helper that repeats itself is a strong hint that a single frame is
    # sometimes missed. Same frame both times; nothing new is asked for.
    wrote = 0
    for attempt in range(2):
        if attempt:
            await asyncio.sleep(STOP_REPEAT_S)
        try:
            await _write(STOP, ignore_latch=True)
            wrote += 1
        except Exception as exc:
            logger.warning("arm: #STOP write %d failed: %s", attempt + 1, exc)
            if not wrote:
                return {"stop_write_ok": False,
                        "error": f"{type(exc).__name__}: {exc}"}
    return {"stop_write_ok": True, "error": None}


def arm() -> None:
    global _armed
    _armed = True


async def state() -> dict:
    """Everything the page needs, measured rather than assumed."""
    low, high = limits()
    resolved = await resolve_port() if configured() else ""
    return {
        "configured": configured(),
        "enabled": bool(settings.robot_arm_enabled),
        "port": resolved or port_description(),
        "port_config": settings.robot_arm_port,
        "port_present": await port_present() if configured() else False,
        "armed": _armed,
        "groups_enabled": bool(settings.robot_arm_groups_enabled),
        "motion_enabled": bool(settings.robot_arm_motion_enabled),
        "channels": list(CHANNELS),
        "commanded": dict(_commanded),
        "limits": {"low": low, "high": high,
                   "centre": PULSE_CENTRE, "step": STEP},
        "sent": list(_sent[-12:]),
    }
