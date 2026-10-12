"""The adb backend that lets the operator console drive a room's Android panel.

No real adb runs here: `_run` (the single adb subprocess call) and `_connect`
are stubbed, so the tests pin the CONTRACT, not a device. The two rules that
matter most, carried from the rest of the project:

  Unreachable is never 'ok'. If adb cannot reach the panel, the result says so
  and instructs "do not say it happened" -- the same mock-is-not-ok rule the
  robot and the Windows room caller follow.

  Input is its own switch. A real tap or keystroke (`mouse_click`, `type_text`)
  is refused unless the room enabled input, mirroring ROOM_AGENT_INPUT.
"""

import base64
import importlib.util
import io
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("android_control", ROOT / "scripts" / "android_control.py")
ac = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ac)


@pytest.fixture
def adb(monkeypatch):
    """Record adb calls; return canned output per command. No device involved."""
    calls = []

    def fake_run(serial, args, *, binary=False, timeout=ac.TIMEOUT):
        calls.append(args)
        if args[:2] == ["exec-out", "screencap"]:
            from PIL import Image
            buf = io.BytesIO()
            Image.new("RGB", (1920, 1080), (10, 20, 30)).save(buf, format="PNG")
            return buf.getvalue()
        if args[:3] == ["shell", "dumpsys", "activity"]:
            return ("  topResumedActivity=ActivityRecord{9d2 u0 com.android.launcher4/.Launcher} t8}\n")
        if args[:2] == ["shell", "getprop"]:
            return "M6APro\n"
        return ""

    monkeypatch.setattr(ac, "_run", fake_run)
    monkeypatch.setattr(ac, "_connect", lambda addr: None)
    return calls


ADDR = "192.168.1.99:33099"


def test_health_reports_an_android_room_the_console_can_read(adb):
    h = ac.health(ADDR, name="จอ Android 1")
    assert h["ok"] is True and h["kind"] == "android"
    assert h["room"] == "จอ Android 1"
    assert h["input_control"] is True
    assert "screen" in h["enabled"] and "mouse_click" in h["enabled"]
    assert set(h["enabled"]) <= set(h["catalogue"])   # every enabled cap is in the catalogue


def test_health_without_input_hides_the_tap_and_type_capabilities(adb):
    h = ac.health(ADDR, input_enabled=False)
    assert h["input_control"] is False
    assert "mouse_click" not in h["enabled"] and "type_text" not in h["enabled"]
    assert "mouse_drag" not in h["enabled"]
    assert "screen" in h["enabled"]                    # read/act still listed


def test_screen_returns_the_downscaled_jpeg_and_the_real_size(adb):
    r = ac.do(ADDR, "screen")
    assert r["ok"] is True
    assert r["size"] == [1920, 1080]                   # real px -> click mapping
    raw = base64.b64decode(r["jpeg_base64"])
    assert raw[:2] == b"\xff\xd8"                       # JPEG, not the raw PNG
    assert ["exec-out", "screencap", "-p"] in adb


def test_a_tap_maps_to_input_tap_with_real_coordinates(adb):
    r = ac.do(ADDR, "mouse_click", {"x": 500, "y": 640})
    assert r["ok"] is True
    assert ["shell", "input", "tap", "500", "640"] in adb


def test_mouse_drag_maps_to_input_swipe(adb):
    r = ac.do(ADDR, "mouse_drag", {"x1": 100, "y1": 200, "x2": 100, "y2": 900, "ms": 300})
    assert r["ok"] is True and r["dragged"] == [100, 200, 100, 900]
    assert ["shell", "input", "swipe", "100", "200", "100", "900", "300"] in adb
    assert ac.do(ADDR, "mouse_drag", {"x1": "a", "y1": 1, "x2": 2, "y2": 3})["ok"] is False
    assert ac.do(ADDR, "mouse_drag", {"x1": 1, "y1": 1, "x2": 2, "y2": 2},
                 input_enabled=False)["ok"] is False


def test_input_actions_are_refused_when_input_is_off(adb):
    for action, args in (("mouse_click", {"x": 1, "y": 2}), ("type_text", {"text": "hi"})):
        r = ac.do(ADDR, action, args, input_enabled=False)
        assert r["ok"] is False and r["error"] == "input control is off"
    # and nothing was sent to the device
    assert not any(a[:3] == ["shell", "input", "tap"] for a in adb)


def test_type_text_is_escaped_so_it_cannot_inject_a_shell_command(adb):
    ac.do(ADDR, "type_text", {"text": "hi there'; rm -rf /"})
    sent = adb[-1]                                      # ["shell", "input text '...'"]
    assert sent[0] == "shell"
    cmd = sent[1]
    assert cmd.startswith("input text '") and cmd.endswith("'")
    assert "hi%sthere" in cmd                           # spaces -> %s
    assert "'\\''" in cmd                               # the single quote was escaped, not left open


def test_unreachable_panel_is_reported_not_pretended(monkeypatch):
    def boom(addr):
        raise ac.AdbError("could not reach the panel at " + addr)
    monkeypatch.setattr(ac, "_connect", boom)
    r = ac.do(ADDR, "list_windows")
    assert r["ok"] is False
    assert "could not reach" in r["error"]
    assert "ห้ามบอกว่าทำแล้ว" in r["instruction"]
    # health too
    assert ac.health(ADDR)["ok"] is False


def test_open_program_needs_a_package_name_not_arbitrary_text(adb):
    assert ac.do(ADDR, "open_program", {"name": "ยูทูป"})["ok"] is False
    assert ac.do(ADDR, "open_program", {"name": ""})["ok"] is False
    ok = ac.do(ADDR, "open_program", {"name": "com.google.android.youtube"})
    assert ok["ok"] is True and "monkey" in adb[-1]


def test_press_key_is_an_allow_list_plus_raw_keycode_only(adb):
    assert ac.do(ADDR, "press_key", {"key": "next_slide"})["ok"] is True
    assert ac.do(ADDR, "press_key", {"key": "KEYCODE_TAB"})["ok"] is True
    # not an arbitrary shell string
    bad = ac.do(ADDR, "press_key", {"key": "HOME; reboot"})
    assert bad["ok"] is False and "unknown key" in bad["error"]


def test_open_in_browser_cannot_inject_a_shell_command(adb):
    """adb shell reassembles the command remotely, so `-d http://x;reboot` would
    run reboot. The url must ride inside single quotes -- present but literal --
    while a real query string (?a=1&b=2) still works."""
    ac.do(ADDR, "open_in_browser", {"url": "http://x;reboot"})
    assert adb[-1] == ["shell", "am start -a android.intent.action.VIEW -d 'http://x;reboot'"]
    ac.do(ADDR, "open_in_browser", {"url": "https://x.com/?a=1&b=2"})
    assert "?a=1&b=2" in adb[-1][1] and adb[-1][1].endswith("'")
    assert ac.do(ADDR, "open_in_browser", {"url": "javascript:alert(1)"})["ok"] is False
    assert ac.do(ADDR, "open_in_browser", {"url": "http://x\nreboot"})["ok"] is False


def test_discover_lists_panels_with_their_stable_serial(monkeypatch):
    """For first-time setup: list visible panels + the serial to paste into
    rooms.json, deduped (a panel appears as ip:port AND its mDNS name)."""
    monkeypatch.setattr(ac, "_plain", lambda args, **k:
        "List of devices attached\n"
        "192.168.1.99:33099\tdevice product:M6APro model:M6APro\n"
        "adb-SER99-x._adb-tls-connect._tcp\tdevice product:M6APro model:M6APro\n"
        "192.168.1.97:5555\tdevice product:TB model:TB1100Pro\n")
    serials = {"192.168.1.99:33099": "SER99",
               "adb-SER99-x._adb-tls-connect._tcp": "SER99",
               "192.168.1.97:5555": "SER97"}
    names = {"192.168.1.99:33099": "ห้องขาย A", "adb-SER99-x._adb-tls-connect._tcp": "ห้องขาย A",
             "192.168.1.97:5555": "null"}   # unset device_name comes back "null"

    def fake_run(serial, args, **k):
        return names[serial] if args[:2] == ["shell", "settings"] else serials[serial]
    monkeypatch.setattr(ac, "_run", fake_run)
    devs = ac.discover()
    assert sorted(d["serial"] for d in devs) == ["SER97", "SER99"]   # .99 deduped
    assert any(d["model"] == "TB1100Pro" for d in devs)
    by_serial = {d["serial"]: d for d in devs}
    assert by_serial["SER99"]["name"] == "ห้องขาย A"   # user-set device name, for telling panels apart
    assert by_serial["SER97"]["name"] == ""            # "null" -> blank


def test_a_room_can_be_addressed_by_stable_serial(monkeypatch):
    """rooms.json can hold the panel's hardware serial instead of the changing
    Wireless-debugging port: _target finds whatever transport currently embeds
    that serial. adb's mDNS auto-connect re-attaches a paired panel after a
    reboot at a NEW port, but the transport name is always adb-<serial>-..."""
    monkeypatch.setattr(ac, "_plain", lambda args, **k:
        "List of devices attached\n"
        "adb-an40010992a86711205-newsuffix._adb-tls-connect._tcp\tdevice product:M6APro\n"
        "192.168.1.5:41000\tdevice\n")
    assert ac._target("an40010992a86711205") == "adb-an40010992a86711205-newsuffix._adb-tls-connect._tcp"


def test_a_literal_ip_port_is_used_as_is(monkeypatch):
    monkeypatch.setattr(ac, "_connect", lambda a: None)
    assert ac._target("192.168.1.99:5555") == "192.168.1.99:5555"


def test_an_unfindable_serial_is_reported_not_guessed(monkeypatch):
    monkeypatch.setattr(ac, "_plain", lambda args, **k: "List of devices attached\n")
    with pytest.raises(ac.AdbError):
        ac._target("nosuchserial")


def test_launch_scrcpy_streams_with_our_adb(tmp_path, monkeypatch):
    """The real-time window uses scrcpy, and it must reuse OUR adb (via the ADB
    env var) so it does not spin up a second, version-clashing adb server."""
    fake = tmp_path / "scrcpy.exe"
    fake.write_text("x")
    monkeypatch.setattr(ac, "_SCRCPY", fake)
    monkeypatch.setattr(ac, "_connect", lambda addr: None)
    monkeypatch.setattr(ac, "adb_path", lambda: r"C:\adb.exe")
    monkeypatch.setattr(ac, "_SCRCPY_SETTLE", 0)          # don't sleep in the test
    seen = {}

    class _Live:                                          # a process that stays up
        def poll(self): return None
    monkeypatch.setattr(ac.subprocess, "Popen",
                        lambda cmd, **kw: seen.update(cmd=cmd, env=kw.get("env")) or _Live())
    r = ac.launch_scrcpy("10.0.0.5:5555", title="จอ A")
    assert r["ok"] is True
    assert str(fake) == seen["cmd"][0]
    assert "-s" in seen["cmd"] and "10.0.0.5:5555" in seen["cmd"]
    assert seen["env"]["ADB"] == r"C:\adb.exe"


def test_launch_scrcpy_says_when_it_is_not_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "_SCRCPY", tmp_path / "missing.exe")
    assert ac.launch_scrcpy("10.0.0.5:5555")["ok"] is False


def test_scrcpy_audio_modes_map_to_the_right_flags_and_never_record():
    """Three modes, and NONE records to disk (no --record anywhere -- listening
    is LIVE only): off = video only (--no-audio); playback = video + the panel's
    OUTPUT audio; mic = audio only from the room's mic, and --no-control so it
    cannot touch the panel."""
    off = ac._scrcpy_args("SER", "t", "off")
    pb = ac._scrcpy_args("SER", "t", "playback")
    mic = ac._scrcpy_args("SER", "t", "mic")
    assert "--no-audio" in off and "--no-video" not in off
    assert "--no-audio" not in pb and "--no-video" not in pb            # video + sound
    assert "--audio-source=mic" in mic and "--no-video" in mic and "--no-control" in mic
    # the audio modes use a bigger buffer so the sound does not stutter over Wi-Fi
    assert any(a.startswith("--audio-buffer=") for a in pb)
    assert any(a.startswith("--audio-buffer=") for a in mic)
    assert not any(a.startswith("--audio-buffer=") for a in off)        # no audio in off mode
    for args in (off, pb, mic):
        assert args[:3] == [str(ac._SCRCPY), "-s", "SER"]
        assert not any(a.startswith("--record") for a in args)         # never saved to disk


def test_launch_scrcpy_rejects_an_unknown_audio_mode(tmp_path, monkeypatch):
    fake = tmp_path / "scrcpy.exe"
    fake.write_text("x")
    monkeypatch.setattr(ac, "_SCRCPY", fake)
    assert ac.launch_scrcpy("10.0.0.5:5555", audio="record_the_room")["ok"] is False


def test_launch_scrcpy_reports_a_window_that_dies_at_once(tmp_path, monkeypatch):
    """scrcpy exiting immediately (device gone / adb clash) must not be logged as
    'real-time window opened'."""
    fake = tmp_path / "scrcpy.exe"
    fake.write_text("x")
    monkeypatch.setattr(ac, "_SCRCPY", fake)
    monkeypatch.setattr(ac, "_connect", lambda addr: None)
    monkeypatch.setattr(ac, "adb_path", lambda: r"C:\adb.exe")
    monkeypatch.setattr(ac, "_SCRCPY_SETTLE", 0)

    class _Dead:
        def poll(self): return 1                          # already exited
    monkeypatch.setattr(ac.subprocess, "Popen", lambda cmd, **kw: _Dead())
    assert ac.launch_scrcpy("10.0.0.5:5555")["ok"] is False


def test_type_text_refuses_non_ascii_honestly_instead_of_a_fake_reach_error(adb):
    """`input text` cannot type Thai; the device throws and it comes back as a
    non-zero exit. Rather than report that as 'could not reach the panel' (a lie),
    say ASCII-only and point at scrcpy -- and do not send anything."""
    r = ac.do(ADDR, "type_text", {"text": "สวัสดี"})
    assert r["ok"] is False and "ASCII" in r["error"]
    assert not any(a[:2] == ["shell", "input"] or (len(a) > 1 and "input text" in a[1]) for a in adb)
    # plain ASCII still types
    assert ac.do(ADDR, "type_text", {"text": "hello 123"})["ok"] is True


def test_a_serial_is_matched_exactly_not_as_a_substring(monkeypatch):
    """A short/placeholder serial must not latch onto a different device whose
    transport merely CONTAINS those characters -- that would drive the wrong
    panel. Only an exact serial or its adb-<serial>-… mDNS transport counts."""
    monkeypatch.setattr(ac, "_plain", lambda args, **k:
        "List of devices attached\n"
        "192.168.1.99:33099\tdevice product:M6APro\n"          # contains '99', '168', '33099'
        "adb-REALSERIAL-x._adb-tls-connect._tcp\tdevice product:M6APro\n")
    assert ac._find_by_serial("REALSERIAL") == "adb-REALSERIAL-x._adb-tls-connect._tcp"
    assert ac._find_by_serial("99") is None                    # not the ip:port
    assert ac._find_by_serial("168") is None


def test_a_blank_adb_address_is_refused_not_guessed(monkeypatch):
    """A whitespace/empty adb value must not fall through to 'the first device
    adb lists' -- the guess this project forbids."""
    monkeypatch.setattr(ac, "_plain", lambda args, **k:
        "List of devices attached\nsomeserial\tdevice\n")
    for bad in ("", "   ", None, 123):
        with pytest.raises(ac.AdbError):
            ac._target(bad)


def test_reboot_and_shutdown_map_to_adb(adb):
    assert ac.do(ADDR, "reboot")["power"] == "restart"
    assert ["reboot"] in adb
    r = ac.do(ADDR, "shutdown")
    assert r["power"] == "shutdown"
    assert ["reboot", "-p"] in adb
    assert "ปลุกจากดับสนิทไม่ได้" in r["instruction"]   # honest: no remote power-on


def test_unknown_action_is_a_clean_error_with_the_known_list(adb):
    r = ac.do(ADDR, "definitely_not_a_thing")
    assert r["ok"] is False and r["error"] == "unknown action"
    assert "screen" in r["known"]
