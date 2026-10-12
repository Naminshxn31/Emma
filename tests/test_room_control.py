"""The caller side: which machine a command goes to, and what happens when it
cannot get there.

The agent's own gates are tested in test_room_agent.py. These are the two ways
this half goes wrong, and neither of them looks like a failure at the time:

  It sends the command to the wrong room. Nobody notices in the logs, because
  the command succeeded — on a screen in front of a different customer.

  It cannot reach the room at all and the answer reads like success. Same shape
  as the robot's mock-is-not-ok rule: a machine nobody spoke to must never be
  reported as a machine that did something.
"""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("room_control", ROOT / "scripts" / "room_control.py")
room_control = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(room_control)


@pytest.fixture
def table(tmp_path, monkeypatch):
    """A routing table on disk, because that is what the real one is."""
    def write(rooms: dict) -> Path:
        path = tmp_path / "rooms.json"
        path.write_text(json.dumps(rooms, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(room_control, "TABLE", path)
        return path
    return write


def test_an_android_room_is_driven_over_adb_not_http(table, monkeypatch):
    """A room marked kind:android (or just carrying an adb address) routes to the
    adb backend, and must never touch the HTTP path -- a stray urlopen would mean
    the panel was being addressed like a Windows agent."""
    table({"จอ A": {"kind": "android", "adb": "10.0.0.5:5555", "input": True}})
    seen = {}

    class FakeAdb:
        def health(self, addr, *, name="", input_enabled=True):
            seen["health"] = (addr, name, input_enabled)
            return {"ok": True, "kind": "android"}

        def do(self, addr, action, args=None, *, input_enabled=True):
            seen["do"] = (addr, action, args, input_enabled)
            return {"ok": True}

    monkeypatch.setattr(room_control, "_android_mod", lambda: FakeAdb())
    monkeypatch.setattr(room_control.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("android room hit HTTP")))

    assert room_control.health("จอ A")["kind"] == "android"
    assert seen["health"] == ("10.0.0.5:5555", "จอ A", True)
    room_control.do("จอ A", "screen", {"x": 1})
    assert seen["do"] == ("10.0.0.5:5555", "screen", {"x": 1}, True)


def test_android_input_is_off_unless_the_room_says_exactly_true(table, monkeypatch):
    """Fail closed like ROOM_AGENT_INPUT: input is real taps/keystrokes and the
    panel is reachable from a browser, so a missing or malformed flag must NOT
    hand it out. Only a JSON `true` enables it; `false`/absent disable it; a
    non-bool is a mistake in a hand-edited file and is refused, not guessed."""
    seen = {}

    class FakeAdb:
        def health(self, addr, *, name="", input_enabled=True):
            seen["ie"] = input_enabled
            return {"ok": True}

        def do(self, addr, action, args=None, *, input_enabled=True):
            return {"ok": True}

    monkeypatch.setattr(room_control, "_android_mod", lambda: FakeAdb())

    table({"จอ A": {"kind": "android", "adb": "10.0.0.5:5555", "input": False}})
    room_control.health("จอ A")
    assert seen["ie"] is False

    table({"จอ A": {"kind": "android", "adb": "10.0.0.5:5555"}})       # omitted
    room_control.health("จอ A")
    assert seen["ie"] is False                                        # fail closed

    table({"จอ A": {"kind": "android", "adb": "10.0.0.5:5555", "input": True}})
    room_control.health("จอ A")
    assert seen["ie"] is True

    # a non-bool ("true" as a string, 1, ...) is a config mistake, not "on"
    table({"จอ A": {"kind": "android", "adb": "10.0.0.5:5555", "input": "true"}})
    with pytest.raises(room_control.RoomError):
        room_control.health("จอ A")


def test_discover_android_routes_to_the_backend(monkeypatch):
    class FakeAdb:
        def discover(self):
            return [{"serial": "S", "model": "M6APro"}]
    monkeypatch.setattr(room_control, "_android_mod", lambda: FakeAdb())
    assert room_control.discover_android()[0]["serial"] == "S"


def test_launch_scrcpy_is_android_only(table, monkeypatch):
    table({"จอ A": {"kind": "android", "adb": "10.0.0.5:5555"},
           "pc": {"url": "http://x:8777", "token": "t"}})
    seen = {}

    class FakeAdb:
        def launch_scrcpy(self, addr, *, title="", audio="off"):
            seen["a"] = (addr, title, audio)
            return {"ok": True}

    monkeypatch.setattr(room_control, "_android_mod", lambda: FakeAdb())
    assert room_control.launch_scrcpy("จอ A")["ok"] is True
    assert seen["a"] == ("10.0.0.5:5555", "จอ A", "off")
    room_control.launch_scrcpy("จอ A", audio="mic")               # the room's live mic
    assert seen["a"] == ("10.0.0.5:5555", "จอ A", "mic")
    with pytest.raises(room_control.RoomError):        # a PC room has no scrcpy
        room_control.launch_scrcpy("pc")


def test_wake_sends_a_magic_packet_to_a_windows_rooms_mac(table, monkeypatch):
    """A powered-off PC can't be reached through its agent (nothing is running),
    so the management PC sends a Wake-on-LAN magic packet itself: 6x0xFF then the
    MAC repeated 16 times, broadcast on the WoL ports. It targets the /24
    subnet-directed broadcast of the PC's own IP so it leaves the right interface
    on this dual-homed management PC."""
    table({"ห้องขาย 1": {"url": "http://192.168.0.9:8777", "token": "t",
                          "mac": "AA:BB:CC:DD:EE:FF"}})
    sent = []

    class FakeSock:
        def setsockopt(self, *a): pass
        def sendto(self, data, addr): sent.append((data, addr))
        def close(self): pass

    monkeypatch.setattr(room_control.socket, "socket", lambda *a, **k: FakeSock())
    r = room_control.wake("ห้องขาย 1")
    assert r["ok"] is True
    data, _ = sent[0]
    assert data[:6] == b"\xff" * 6
    assert data[6:12] == bytes.fromhex("AABBCCDDEEFF")
    assert len(data) == 6 + 6 * 16
    assert ("192.168.0.255", 9) in {a for _, a in sent}     # subnet-directed, WoL port
    assert "เปิดแล้ว" not in r.get("instruction", "")        # honest: signal sent, not "PC is on"


def test_wake_refuses_an_android_room_no_wol_there(table):
    table({"จอ A": {"kind": "android", "adb": "10.0.0.5:5555"}})
    with pytest.raises(room_control.RoomError):
        room_control.wake("จอ A")


def test_wake_needs_a_mac_and_rejects_a_bad_one(table):
    table({"ห้องขาย 1": {"url": "http://192.168.0.9:8777", "token": "t"}})
    with pytest.raises(room_control.RoomError):                # no mac configured
        room_control.wake("ห้องขาย 1")
    table({"ห้องขาย 1": {"url": "http://192.168.0.9:8777", "token": "t", "mac": "not-a-mac"}})
    with pytest.raises(room_control.RoomError):                # malformed mac
        room_control.wake("ห้องขาย 1")


def test_resolve_accepts_an_android_room_without_a_url(table):
    table({"จอ A": {"kind": "android", "adb": "10.0.0.5:5555"}})
    assert room_control.resolve("จอ A")["adb"] == "10.0.0.5:5555"
    # a Windows room still must carry a url
    table({"pc": {"token": "t"}})
    with pytest.raises(room_control.RoomError):
        room_control.resolve("pc")


def test_one_room_in_the_table_is_still_not_an_answer(table):
    """The single-room case is the one that tempts a fallback, and it is the
    one where the fallback is most convincing: it is right every day until a
    second room is added, and then it is silently wrong."""
    table({"ห้องขาย 1": {"url": "http://10.0.0.1:8777", "token": "t"}})

    with pytest.raises(room_control.RoomError) as caught:
        room_control.resolve("")

    assert "which room" in str(caught.value)
    assert "ห้องขาย 1" in str(caught.value)      # it offers the list...
    # ...and it did not return it.


def test_a_room_that_is_almost_the_right_name_is_a_different_room(table):
    table({"ห้องขาย 1": {"url": "http://10.0.0.1:8777", "token": "t"},
           "ห้องขาย 2": {"url": "http://10.0.0.2:8777", "token": "t"}})

    for asked in ("ห้องขาย", "ห้องขาย 3", "ห้อง 1", "sales 1"):
        with pytest.raises(room_control.RoomError):
            room_control.resolve(asked)


def test_an_empty_table_says_so_instead_of_failing_somewhere_later(table, tmp_path, monkeypatch):
    monkeypatch.setattr(room_control, "TABLE", tmp_path / "nothing.json")

    with pytest.raises(room_control.RoomError) as caught:
        room_control.resolve("ห้องขาย 1")

    assert "no rooms configured" in str(caught.value)


def test_a_room_that_cannot_be_reached_is_never_reported_as_done(table, monkeypatch):
    """A sales-room PC that is off, asleep or on another subnet lands here.

    The failure has to name the connection, not the action: "I turned the
    screen off" about a machine nobody spoke to is the same bug as the robot
    saying it is walking while the mock is on.
    """
    import urllib.error

    table({"ห้องขาย 1": {"url": "http://10.255.255.1:8777", "token": "t"}})
    monkeypatch.setattr(room_control.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("unreachable")))

    result = room_control.do("ห้องขาย 1", "monitor", {"state": "off"})

    assert result["ok"] is False
    assert "reach" in result["error"]
    assert "ห้ามบอกว่าทำแล้ว" in result["instruction"]


def test_a_hung_action_reads_as_no_response_not_as_a_crash(table, monkeypatch):
    """A read timeout (the agent accepted the connection but a slow/hung action
    never answered -- `monitor`'s window broadcast did exactly this on
    2026-09-25) is raised bare as TimeoutError, NOT wrapped in URLError, so it
    would otherwise escape as a 500. It must read as 'did not respond', never as
    the action having happened."""
    table({"ห้องขาย 1": {"url": "http://10.0.0.1:8777", "token": "t"}})
    monkeypatch.setattr(room_control.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(TimeoutError("timed out")))

    result = room_control.do("ห้องขาย 1", "monitor", {"state": "off"})

    assert result["ok"] is False
    assert "did not respond" in result["error"]
    assert "ห้ามบอกว่าทำแล้ว" in result["instruction"]


def test_the_command_carries_that_room_s_own_token(table, monkeypatch):
    """One token per machine. Sending room 1's token to room 2 would be refused
    by room 2, which is the point -- but only if the token is read per room and
    not held once in a module."""
    table({"ห้องขาย 1": {"url": "http://10.0.0.1:8777", "token": "token-one"},
           "ห้องขาย 2": {"url": "http://10.0.0.2:8777", "token": "token-two"}})
    seen = []

    class _Response:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"ok": true}'

    def fake_urlopen(req, data=None, timeout=None):
        seen.append((req.full_url, req.get_header("X-room-token"), json.loads(data)))
        return _Response()

    monkeypatch.setattr(room_control.urllib.request, "urlopen", fake_urlopen)

    room_control.do("ห้องขาย 2", "list_windows")

    url, token, body = seen[0]
    assert url == "http://10.0.0.2:8777/do"
    assert token == "token-two"
    assert body == {"action": "list_windows", "args": {}}


def test_health_asks_without_a_token_because_it_does_nothing(table, monkeypatch):
    """/health reports what a room CAN do and performs nothing, so it stays
    reachable when the token is wrong -- that is how "no agent there" is told
    apart from "agent there, token wrong"."""
    table({"ห้องขาย 1": {"url": "http://10.0.0.1:8777", "token": "t"}})
    seen = []

    class _Response:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"ok": true}'

    monkeypatch.setattr(room_control.urllib.request, "urlopen",
                        lambda req, data=None, timeout=None: (
                            seen.append((req.full_url, req.get_header("X-room-token"))) or _Response()))

    room_control.health("ห้องขาย 1")

    assert seen[0] == ("http://10.0.0.1:8777/health", None)


def test_the_routing_table_never_reaches_the_repo():
    """It holds one shared secret per sales-room PC."""
    result = subprocess.run(["git", "check-ignore", "-q", "data/rooms.json"],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0


def test_the_caller_is_not_wired_into_emma_either():
    """Owner: "อย่าเพิ่งเอาไปใส่ที่ emma นะ ทำทดสอบไปก่อน".

    test_room_agent.py guards the agent's name; this guards the caller's. The
    caller is the half that would actually be imported by a tool, so leaving it
    out of that check would leave the door it is about wide open.
    """
    for path in sorted((ROOT / "app").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "room_control" not in text, path
        assert "android_control" not in text, path   # the adb backend is caller-side too


def test_a_table_saved_by_notepad_still_loads(tmp_path, monkeypatch):
    """Windows Notepad writes UTF-8 with a BOM, and this file is edited by hand.

    Read as plain utf-8 the BOM raises "Expecting value: line 1 column 1",
    which reads as bad JSON rather than as an invisible first character, and
    the room names are Thai so there is no chance of noticing by eye.
    """
    path = tmp_path / "rooms.json"
    path.write_text('{"ห้องขาย 1": {"url": "http://10.0.0.1:8777", "token": "t"}}',
                    encoding="utf-8-sig")
    monkeypatch.setattr(room_control, "TABLE", path)

    assert room_control.resolve("ห้องขาย 1")["url"] == "http://10.0.0.1:8777"


def test_a_mangled_room_name_matches_nothing_instead_of_something(tmp_path, monkeypatch):
    """Thai that has been through the wrong encoding once (PowerShell writing
    JSON, a copy through a cp874 console) comes out as a different string.

    It must not near-match: a room whose name survived badly is not evidence
    about which machine is in which room, and the command belongs nowhere until
    a person fixes the file.
    """
    mangled = "ห้องขาย 1".encode("utf-8").decode("latin-1")
    path = tmp_path / "rooms.json"
    path.write_text(json.dumps({mangled: {"url": "http://10.0.0.1:8777", "token": "t"}},
                               ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(room_control, "TABLE", path)

    with pytest.raises(room_control.RoomError):
        room_control.resolve("ห้องขาย 1")
