"""The navigation board's SDK port (1445): the one thing the REST API lacks — velocity.

REST gives MoveTo (the board plans and drives), RotateTo, and MoveBy: forward,
back, turn-left, turn-right, each lasting only while re-sent. Steering with that
means alternating "forward" with short "turn" sends, and the owner watched the
result wobble and stutter (6 ต.ค.: "ทำไมมันเคลื่อนที่แปลกๆ อยากให้แบบสมูท").
The SDK port has a velocity-control action — forward speed and turn rate
together — which is what a smooth curve is made of. It is the same protocol
RoboStudio speaks, and none of it is documented for this firmware; every
detail below was found on 6 ต.ค. 2026 by reading RoboStudio's own SDK library
(`librpsdk.so`) and then confirmed on the robot:

* framing: one JSON object per message, ``{"args": …, "command": …,
  "request_id": n}``, terminated by ``\\r\\n\\r\\n``; replies echo
  ``request_id`` and carry ``result``.
* ``velocitycontrol`` takes a bare STRING — ``"not_monitored"`` or
  ``"monitored_by_localization_quality"`` (the serialised
  ``VelocityControlFlag``). An object, a number or nothing all answer
  ``codestr: "value"``. Success returns ``{"id": n, "action_type": 12,
  "code": 1}``. We use ``not_monitored``: the board's localization is off on
  this robot (it pulled the pose 0.33 m in the furnished room), and a
  monitored action would stop on its quality.
* ``setvelocity`` takes ``{"id": n, "vx": m/s, "vy": 0, "omega": rad/s}``
  and answers ``codestr: "done"``. ``action_id``/``actionid`` →
  ``operation_fail``; numbers sent as strings → type errors.
* ``cancelaction`` ``{}`` answers ``done`` and does NOT end a velocity
  action (6 ต.ค. 15:20, zero speed: still current 0.3 s later). What ends it
  is the board's watchdog: ~1 s without a ``setvelocity`` the robot stops,
  ~1.5 s and the action is gone (``getcurrentaction`` id 0).
* ``setvelocity`` to the id of an action the watchdog just ended answers
  ``done`` and quietly starts a NEW action under a new id; the next send to
  the old id then answers ``operation_fail`` — any id that is not the current
  one does. That is how a Wi-Fi stall killed the first real lane mid-way
  (6 ต.ค. 15:14, at 0.43 of 0.53 m); `VelocitySession.set` now takes up the
  live action and ramps from zero instead.
* measured off the dock: vx 0.05 for 1 s → 3.5 cm (the ramp eats some),
  omega ±0.15 for 1 s → ±4.2°, vx 0.10 with omega ±0.10 for 1.5 s → a 14 cm
  arc turning ±6.4°.

* ``getlaserscan`` (no args) is the same frame REST's
  ``/system/v1/laserscan`` serves, plus the pose it was taken at, in 26 KB
  instead of 117 KB: ``laser_points`` is base64 of Slamware's RLE (see
  `rle_decode`) over a uint32 byte count and 12-byte points — float32
  distance, float32 angle, uint8 valid, 3 bytes padding. It matters because
  everything rides one adb tunnel over Wi-Fi: polling the 117 KB REST frame
  three times a second during a lane put ~350 KB/s ahead of every 100-byte
  setvelocity, and the first real lanes (6 ต.ค. 15:23) stalled 1.4-2.3 s
  again and again — each stall past the watchdog a full stop.

This module only talks. What speeds are safe, and when to stop, is decided by
the caller (scripts/drive_route.py) from the lidar.
"""
from __future__ import annotations

import base64
import json
import socket
import struct
import time
from typing import Any, Callable

TERMINATOR = b"\r\n\r\n"
VELOCITY_FLAG = "not_monitored"


class SdkError(RuntimeError):
    """The board answered, and the answer was not success."""


def rle_decode(data: bytes) -> bytes:
    """Slamware's RLE: ``b"RLE"``, the opening sentinel S, one more byte, then
    the stream. Bytes other than S are themselves; ``S n v`` (n > 0) is n
    copies of v; ``S 0 x`` makes x the sentinel from there on, which is how a
    literal S gets through (``81 00 7f 81`` = one 0x81). Nothing documents
    it; on a real frame (6 ต.ค.) this reading — and none of the sixteen
    escape-and-swap variants tried first — decodes to exactly the length the
    payload's own header states, 19308 bytes = 1609 points."""
    if len(data) < 5 or data[:3] != b"RLE":
        raise SdkError("not an RLE block")
    sentinel, out, i, n = data[3], bytearray(), 5, len(data)
    while i < n:
        c = data[i]
        if c != sentinel:
            out.append(c)
            i += 1
            continue
        if i + 2 >= n:
            raise SdkError("RLE block ends inside a run")
        count, value = data[i + 1], data[i + 2]
        if count:
            out += bytes((value,)) * count
        else:
            sentinel = value
        i += 3
    return bytes(out)


def parse_laser_points(blob: bytes) -> list[dict]:
    """The decoded ``getlaserscan`` payload as REST-shaped points, so
    `robot_lanes.scan_points` reads either source."""
    if len(blob) < 4:
        raise SdkError("laser scan payload too short")
    size = struct.unpack_from("<I", blob, 0)[0]
    if size % 12 or len(blob) < 4 + size:
        raise SdkError("laser scan payload is %d bytes, header says %d" % (len(blob) - 4, size))
    return [{"distance": d, "angle": a, "valid": bool(v)}
            for d, a, v in struct.iter_unpack("<ffB3x", blob[4:4 + size])]


def laser_scan(sdk: "SlamwareSdk", timeout: float | None = None) -> dict:
    """One lidar frame over the SDK port, shaped like REST's document:
    ``{"laser_points": [...], "pose": {"x", "y", "yaw"}}``."""
    r = sdk.call("getlaserscan", None, timeout)
    if r.get("code") != 1 or not isinstance(r.get("laser_points"), str):
        raise SdkError("getlaserscan refused: %s" % (r.get("codestr") or r))
    points = parse_laser_points(rle_decode(base64.b64decode(r["laser_points"])))
    return {"laser_points": points, "pose": {"x": r.get("x"), "y": r.get("y"), "yaw": r.get("yaw")}}


class SlamwareSdk:
    def __init__(self, host: str = "127.0.0.1", port: int = 11445, timeout: float = 3.0,
                 connect: Callable[..., Any] = socket.create_connection):
        self.timeout = timeout
        self.sock = connect((host, port), timeout=timeout)
        self.buf = b""
        self.rid = 0

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def _send(self, command: str, args: Any) -> int:
        self.rid += 1
        msg = {"args": {} if args is None else args, "command": command, "request_id": self.rid}
        self.sock.sendall(json.dumps(msg).encode() + TERMINATOR)
        return self.rid

    def call(self, command: str, args: Any = None, timeout: float | None = None) -> dict:
        """Send one command and return its ``result``. Replies to earlier
        requests that arrive late are skipped, not mistaken for this one."""
        rid = self._send(command, args)
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while time.monotonic() < deadline:
            while TERMINATOR in self.buf:
                raw, self.buf = self.buf.split(TERMINATOR, 1)
                try:
                    doc = json.loads(raw)
                except ValueError:
                    continue
                if doc.get("request_id") == rid:
                    result = doc.get("result")
                    return result if isinstance(result, dict) else {"value": result}
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                raise SdkError("board closed the SDK connection during %r" % command)
            self.buf += chunk
        raise SdkError("no answer to %r within %.1f s" % (command, self.timeout if timeout is None else timeout))


class VelocitySession:
    """One velocity-control action. ``with VelocitySession(sdk) as v: v.set(vx, w)``
    — leaving the block, normally or by an exception, sends zero and waits for
    the board to drop the action, because a robot left holding its last
    command is the failure that matters, and an action still current when the
    next REST MoveTo / GoHome arrives is the board's to resolve, not ours."""

    # Longer than this since the last accepted send and the watchdog may have
    # ended the action: take it up again (velocitycontrol hands back the live
    # one if there still is one) instead of sending into a dead id.
    RENEW_AFTER_S = 1.0

    def __init__(self, sdk: SlamwareSdk, flag: str = VELOCITY_FLAG, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, wait_idle_s: float = 3.0):
        self.sdk, self.flag, self.id = sdk, flag, None
        # 0: leave with the zero sent and nothing more -- the board drops the
        # action ~1 s later by its watchdog, or the next session takes it up
        # (velocitycontrol hands back a live one). For a spot turn going on
        # under velocity control; never before a REST action.
        self.wait_idle_s = wait_idle_s
        self.clock, self.sleep = clock, sleep
        self.last_ok = 0.0
        self.renewals = 0

    def open(self) -> int:
        result = self.sdk.call("velocitycontrol", self.flag)
        if result.get("code") != 1 or not result.get("id"):
            raise SdkError("velocitycontrol refused: %s" % (result.get("codestr") or result))
        self.id = int(result["id"])
        self.last_ok = self.clock()
        return self.id

    def _send(self, vx: float, omega: float) -> str:
        result = self.sdk.call("setvelocity", {"id": self.id, "vx": float(vx), "vy": 0.0, "omega": float(omega)})
        return result.get("codestr") or str(result)

    def _renew(self) -> None:
        """The robot is standing still at this point (the watchdog stopped it),
        so the action is taken up with a zero: the caller ramps from there
        rather than jumping back to the speed it had before the stall."""
        self.open()
        answer = self._send(0.0, 0.0)
        if answer != "done":
            raise SdkError("setvelocity refused after renewing the action: %s" % answer)
        self.last_ok = self.clock()
        self.renewals += 1

    def set(self, vx: float, omega: float) -> bool:
        """Send one command. Returns True when the action had to be taken up
        again (a gap longer than the watchdog) — this send was a zero and the
        caller should ramp from standstill. False = sent as asked."""
        if self.id is None:
            raise SdkError("velocity session is not open")
        if self.clock() - self.last_ok > self.RENEW_AFTER_S:
            self._renew()
            return True
        answer = self._send(vx, omega)
        if answer == "done":
            self.last_ok = self.clock()
            return False
        if answer == "operation_fail":
            self._renew()
            return True
        raise SdkError("setvelocity refused: %s" % answer)

    def stop(self, wait_idle_s: float | None = None) -> None:
        if self.id is None:
            return
        wait_idle_s = self.wait_idle_s if wait_idle_s is None else wait_idle_s
        try:
            if self._send(0.0, 0.0) != "done":
                # the id went stale (the watchdog ended it in a gap): the board
                # may have revived the action under a new one, still holding the
                # last command -- up to ~35° of turn at 0.8 rad/s (reviewer).
                # Take the live action up and send its zero.
                self._renew()
            if wait_idle_s <= 0:
                return
            self.sdk.call("cancelaction", {})          # harmless; the watchdog is what ends it
            deadline = self.clock() + wait_idle_s
            while self.clock() < deadline:
                current = self.sdk.call("getcurrentaction")
                if current.get("id") != self.id or not current.get("action_type"):
                    break
                self.sleep(0.25)
        finally:
            self.id = None

    def __enter__(self) -> "VelocitySession":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        try:
            self.stop()
        except (SdkError, OSError):
            pass
