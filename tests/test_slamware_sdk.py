"""SDK port 1445 client (app/slamware_sdk.py) against a fake board — no network.

The fake answers the way the real board did on 6 ต.ค. 2026: velocitycontrol
only accepts the bare string flag, setvelocity only with "id"; the watchdog
ends an action 1.5 s after its last setvelocity; a send to the id it just
ended starts a new action under a new id, a send to any other non-current id
is operation_fail; cancelaction answers "done" and changes nothing.
"""
import json
import math
import socket
from pathlib import Path

import pytest

from app import slamware_sdk as sw

T = b"\r\n\r\n"


class FakeBoard:
    WATCHDOG_S = 1.5

    def __init__(self, late_reply=False):
        self.sent, self.out, self.closed = [], b"", False
        self.late_reply = late_reply
        self.actions, self.velocity = 0, []
        self.now = 0.0
        self.current, self.last_set, self.last_id = None, 0.0, None

    def clock(self):
        return self.now

    def sleep(self, t):
        self.now += t

    def _alive(self):
        if self.current is not None and self.now - self.last_set > self.WATCHDOG_S:
            self.current = None
        return self.current

    def _new_action(self):
        self.actions += 1
        self.current = self.last_id = 400 + self.actions
        self.last_set = self.now
        return self.current

    # socket API used by SlamwareSdk
    def sendall(self, data):
        for raw in data.split(T):
            if raw:
                self.sent.append(json.loads(raw))
                self._answer(self.sent[-1])

    def recv(self, n):
        if not self.out:
            raise socket.timeout()
        chunk, self.out = self.out[:7], self.out[7:]     # dribble: framing must cope
        return chunk

    def close(self):
        self.closed = True

    def _reply(self, rid, cmd, result):
        self.out += json.dumps({"command": cmd, "request_id": rid, "result": result}).encode() + T

    def _answer(self, msg):
        rid, cmd, args = msg["request_id"], msg["command"], msg["args"]
        if self.late_reply:
            self._reply(rid - 1, "getsdpversion", {"code": 1})    # a stale reply first
        if cmd == "velocitycontrol":
            if args == "not_monitored":
                aid = self._alive() or self._new_action()         # the live one is handed back
                self._reply(rid, cmd, {"action_type": 12, "code": 1, "id": aid})
            else:
                self._reply(rid, cmd, {"code": 0, "codestr": "value"})
        elif cmd == "setvelocity":
            ok = isinstance(args, dict) and "id" in args and all(isinstance(args.get(k), float) for k in ("vx", "vy", "omega"))
            live = self._alive()
            if ok and live is not None and args["id"] == live:
                pass
            elif ok and live is None and args["id"] == self.last_id:
                self._new_action()                                # revived under a NEW id
            else:
                ok = False
            if ok:
                self.last_set = self.now
                self.velocity.append((args["vx"], args["omega"]))
            self._reply(rid, cmd, {"code": 0, "codestr": "done" if ok else "operation_fail"})
        elif cmd == "cancelaction":
            self._reply(rid, cmd, {"code": 0, "codestr": "done"})  # and does nothing
        elif cmd == "getcurrentaction":
            live = self._alive()
            self._reply(rid, cmd, {"code": 1, "id": live or 0, "action_type": 12 if live else 0})
        else:
            self._reply(rid, cmd, {"code": 1})


def session(board, **kw):
    return sw.VelocitySession(sdk_with(board), clock=board.clock, sleep=board.sleep, **kw)


def sdk_with(board):
    return sw.SlamwareSdk(connect=lambda addr, timeout: board, timeout=0.5)


def test_frames_requests_and_matches_the_reply_by_request_id():
    board = FakeBoard(late_reply=True)
    sdk = sdk_with(board)
    assert sdk.call("getsdpversion") == {"code": 1}
    assert board.sent[0] == {"args": {}, "command": "getsdpversion", "request_id": 1}


def test_velocity_session_uses_the_shapes_the_board_accepts():
    board = FakeBoard()
    with session(board) as v:
        assert v.id == 401
        assert v.set(0.1, -0.2) is False
    assert board.sent[0]["args"] == "not_monitored"            # a bare string, not an object
    assert board.sent[1]["args"] == {"id": 401, "vx": 0.1, "vy": 0.0, "omega": -0.2}
    # leaving the block sends zero and stays until the board has dropped the action
    assert board.velocity[-1] == (0.0, 0.0)
    assert board.sent[-1]["command"] == "getcurrentaction"
    assert board.current is None


def test_leaving_on_an_exception_still_stops_the_robot():
    board = FakeBoard()
    with pytest.raises(ValueError):
        with session(board) as v:
            v.set(0.2, 0.0)
            raise ValueError("lidar said stop")
    assert board.velocity[-1] == (0.0, 0.0)
    assert board.current is None


def test_a_stall_past_the_watchdog_is_taken_up_again_from_standstill():
    # Wi-Fi stalled 2 s mid-lane: the board stopped the robot and ended the
    # action. The next send must not resume at 0.2 m/s out of nowhere.
    board = FakeBoard()
    with session(board) as v:
        v.set(0.2, 0.0)
        board.sleep(2.0)
        assert v.set(0.2, 0.0) is True
        assert board.velocity[-1] == (0.0, 0.0)
        assert v.set(0.05, 0.0) is False
        assert board.velocity[-1] == (0.05, 0.0)
        assert v.renewals == 1


def test_operation_fail_after_the_board_revived_the_action_under_a_new_id():
    # The real sequence of 6 ต.ค. 15:14: the action ended between two sends that
    # each looked on time from here; the first send to the dead id was "done"
    # (a new action, 402) and the second, still to 401, was operation_fail.
    board = FakeBoard()
    with session(board) as v:
        v.set(0.1, 0.0)
        board.current = None                                     # watchdog fired unseen
        assert v.set(0.1, 0.0) is False                          # revived as 402, id here still 401
        assert v.set(0.1, 0.0) is True                           # operation_fail -> take up 402
        assert v.id == 402
        assert board.velocity[-1] == (0.0, 0.0)
        assert v.set(0.1, 0.0) is False


def test_a_refused_velocity_control_is_an_error_not_a_silent_no_op():
    board = FakeBoard()
    v = sw.VelocitySession(sdk_with(board), flag="wrong")
    with pytest.raises(sw.SdkError, match="value"):
        v.open()


def test_no_answer_is_an_error():
    class Silent(FakeBoard):
        def _answer(self, msg):
            pass
    with pytest.raises(sw.SdkError, match="no answer"):
        sdk_with(Silent()).call("getsdpversion")


FRAME = Path(__file__).parent / "data" / "robot" / "sdk_laserscan_rle.bin"   # getlaserscan, 6 ต.ค. 15:27


def test_a_real_sdk_scan_decodes_to_the_length_its_header_states():
    blob = sw.rle_decode(FRAME.read_bytes())
    points = sw.parse_laser_points(blob)
    assert len(points) == 1609 and len(blob) == 4 + 1609 * 12
    assert all(0 <= p["distance"] < 40 and -math.pi - 0.01 <= p["angle"] <= math.pi + 0.01 for p in points)
    # the robot was standing west-facing on the dock lane; REST's frame then
    # started 3.24 m away at 1.918 rad — the same wall
    assert points[0]["distance"] == pytest.approx(3.243, abs=0.01)
    assert points[0]["angle"] == pytest.approx(1.918, abs=0.01)


def test_rle_runs_and_sentinel_switches():
    # "ab", run of 4 x 0x00, a literal 0x81 via switching the sentinel to 0x7f, "c"
    data = b"RLE\x81\x7f" + b"ab" + b"\x81\x04\x00" + b"\x81\x00\x7f" + b"\x81" + b"\x7f\x00\x81" + b"c"
    assert sw.rle_decode(data) == b"ab\x00\x00\x00\x00\x81c"


def test_a_payload_shorter_than_its_header_is_an_error_not_half_a_scan():
    blob = sw.rle_decode(FRAME.read_bytes())
    with pytest.raises(sw.SdkError, match="header"):
        sw.parse_laser_points(blob[:-12])


def test_a_session_that_does_not_wait_sends_its_zero_and_the_next_takes_the_action_up():
    # 8 ต.ค.: every spot turn waited ~1 s for the board's watchdog to drop the
    # action. A turn going on under velocity control leaves with the zero sent;
    # the next session gets the same, still live, action back.
    board = FakeBoard()
    with session(board, wait_idle_s=0.0) as v:
        v.set(0.0, 0.5)
        first = v.id
    assert board.velocity[-1] == (0.0, 0.0)
    assert board.sent[-1]["command"] == "setvelocity"            # no cancel, no waiting
    with session(board) as v2:
        assert v2.id == first
        assert v2.set(0.1, 0.0) is False


def test_a_zero_the_board_refuses_on_leaving_goes_to_the_live_action():
    # reviewer: the action ended unseen and the board revived it under a new id,
    # holding the last command; the zero to the old id was operation_fail and was
    # ignored -- the robot went on turning until the next watchdog (~35° at 0.8)
    board = FakeBoard()
    v = session(board, wait_idle_s=0.0)
    v.open()
    v.set(0.0, 0.5)
    board.current = None                                         # the watchdog fired unseen
    assert v.set(0.0, 0.5) is False                              # revived as 402, holding 0.5
    v.stop()
    assert board.velocity[-1] == (0.0, 0.0)
    assert board.sent[-1]["args"]["id"] == board.current         # the zero went to the live one
