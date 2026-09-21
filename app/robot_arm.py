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

Nothing here is reachable by the model. The manual controls remain a desk
instrument. One server-owned path may request a stored group alongside Emma's
spoken greeting, but only when its dedicated opt-in and every existing arm
interlock are open. It cannot arm the module or loosen a gate.
`ROBOT_ARM_ENABLED` is false by default and gates every write.
"""
from __future__ import annotations

import asyncio
import logging
import re

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
#: here as a warning, not a feature: opening their app can move the arm on its
#: own, and this module must never imitate that. Nothing is sent on connect.
VENDOR_HOME_GROUP = 99

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
    global _armed
    _commanded.clear()
    _sent.clear()
    _armed = True


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
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise
    return proc.returncode or 0, (out or b"").decode("utf-8", "replace").strip()


def _adb(shell_command: str) -> list[str]:
    args = [settings.robot_arm_adb]
    if settings.robot_arm_adb_serial:
        args += ["-s", settings.robot_arm_adb_serial]
    return args + ["shell", shell_command]


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
        code, out = await _run(_adb(command), timeout=settings.robot_arm_timeout_s)
    except Exception as exc:
        logger.info("arm: could not resolve the CH340 port: %s", exc)
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
        code, out = await _run(_adb("ls " + port),
                               timeout=settings.robot_arm_timeout_s)
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
    port = await resolve_port()
    if not port:
        raise RuntimeError(f"ไม่พบ {port_description()}")
    code, out = await _run(
        _adb("stty -F %s %d raw -echo" % (port, settings.robot_arm_baud)),
        timeout=settings.robot_arm_timeout_s)
    return out if code else "ok"


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
    if not _armed and not ignore_latch:
        raise PermissionError("disarmed")
    port = await resolve_port()
    if not port:
        raise RuntimeError(f"ไม่พบ {port_description()}")
    command = "printf '%s\\r\\n' > %s" % (line, port)
    code, out = await _run(_adb(command), timeout=settings.robot_arm_timeout_s)
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


async def greet() -> bool:
    """Request the configured handshake/greeting group without risking speech.

    A greeting must still be heard if the robot is off, disconnected,
    disarmed, or motion-locked. `run_group()` remains the single movement
    path, so this helper inherits all four gates and never prepares or arms
    hardware on its own. Returning False is an observable skip, not an
    exception that can tear down the voice session.
    """
    if not settings.robot_greeting_gesture_enabled:
        return False
    group = settings.robot_greeting_arm_group
    try:
        await run_group(group)
    except Exception as exc:
        logger.info("arm: greeting group %s skipped: %s", group, exc)
        return False
    logger.info("arm: greeting group %s started", group)
    return True


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
    global _armed
    _armed = False
    logger.warning("arm: disarmed — further commands refused until re-armed")

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
