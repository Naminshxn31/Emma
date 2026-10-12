"""
Reaching the robot over adb (app/adb_target.py) — the three stacked faults
behind "ทำไมมือไม่ขึ้นมาจับตอนทัก" (owner, 2026-09-29): the greeting reached
the arm, and the arm's lookup died before a single adb command ran.
Nothing here talks to a real device.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from app import adb_target, robot_arm, robot_android
from app.config import settings


@pytest.fixture(autouse=True)
def _fresh():
    adb_target.reset()
    yield
    adb_target.reset()


def test_the_runner_works_on_the_loop_uvicorn_reload_uses():
    """uvicorn on Windows runs a SelectorEventLoop under reload, and that loop
    raises an EMPTY NotImplementedError from asyncio subprocesses — the log
    line was "could not resolve the CH340 port: " and nothing after it."""
    args = [sys.executable, "-c", "print('hi')"]
    code, out = asyncio.run(adb_target.run(args, timeout=20),
                            loop_factory=asyncio.SelectorEventLoop)
    assert (code, out) == (0, "hi")


def test_a_hung_command_times_out_the_way_callers_expect():
    args = [sys.executable, "-c", "import time; time.sleep(5)"]
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(adb_target.run(args, timeout=0.3))


def test_a_repo_relative_adb_is_resolved_and_a_bare_name_is_left_to_path():
    exe = adb_target.executable("tools/android/platform-tools/adb.exe")
    assert Path(exe).is_absolute()
    assert Path(exe) == adb_target.ROOT / "tools" / "android" / "platform-tools" / "adb.exe"
    assert adb_target.executable("adb") == "adb"
    assert adb_target.executable("") == "adb"


def _fake_adb(monkeypatch, devices: dict[str, str]):
    """devices: adb name -> ro.serialno."""
    calls = []

    async def fake_run(args, timeout):
        calls.append(args[1:])
        if args[1:] == ["devices"]:
            return 0, "List of devices attached\n" + "\n".join(
                f"{name}\tdevice product:x" for name in devices) + "\n"
        if args[1:] == ["mdns", "services"]:
            return 0, "List of discovered mdns services\n"
        if args[1] == "-s" and args[3:] == ["shell", "getprop ro.serialno"]:
            return 0, devices.get(args[2], "")
        raise AssertionError(args)

    monkeypatch.setattr(adb_target, "run", fake_run)
    return calls


def test_the_robot_is_found_by_its_serial_whatever_ip_it_has(monkeypatch):
    # The sales room had a panel and a same-model signage box on the same adb.
    _fake_adb(monkeypatch, {"adb-panel-tls": "an40010992", "192.168.0.34:5555": "c7425db4",
                            "192.168.1.197:5555": "fde47c41"})
    assert adb_target.current("fde47c41") == "fde47c41"             # nothing learned yet
    assert asyncio.run(adb_target.find("adb", "fde47c41", 5)) is True
    assert adb_target.current("fde47c41") == "192.168.1.197:5555"


def test_a_serial_nobody_has_is_not_guessed(monkeypatch):
    _fake_adb(monkeypatch, {"192.168.0.34:5555": "c7425db4"})
    assert asyncio.run(adb_target.find("adb", "fde47c41", 5)) is False
    assert adb_target.current("fde47c41") == "fde47c41"


def test_disconnected_robot_is_reconnected_by_mdns_and_verified(monkeypatch):
    calls = []

    async def fake_run(args, timeout):
        command = args[1:]
        calls.append(command)
        if command == ["devices"]:
            return 0, "List of devices attached\nother\tdevice\n"
        if command == ["-s", "other", "shell", "getprop ro.serialno"]:
            return 0, "another-device"
        if command == ["mdns", "services"]:
            return 0, ("List of discovered mdns services\n"
                       "adb-another-device _adb._tcp 192.168.1.5:5555\n"
                       "adb-fde47c41 _adb._tcp 192.168.1.197:5555\n")
        if command == ["connect", "192.168.1.197:5555"]:
            return 0, "connected"
        if command == ["-s", "192.168.1.197:5555", "shell", "getprop ro.serialno"]:
            return 0, "fde47c41"
        raise AssertionError(command)

    monkeypatch.setattr(adb_target, "run", fake_run)
    assert asyncio.run(adb_target.find("adb", "fde47c41", 5)) is True
    assert adb_target.current("fde47c41") == "192.168.1.197:5555"
    assert [c for c in calls if c[0] == "connect"] == [["connect", "192.168.1.197:5555"]]


def test_mdns_name_without_matching_hardware_serial_is_refused(monkeypatch):
    async def fake_run(args, timeout):
        command = args[1:]
        if command == ["devices"]:
            return 0, "List of devices attached\n"
        if command == ["mdns", "services"]:
            return 0, "adb-fde47c41 _adb._tcp 192.168.1.197:5555"
        if command == ["connect", "192.168.1.197:5555"]:
            return 0, "connected"
        if command == ["-s", "192.168.1.197:5555", "shell", "getprop ro.serialno"]:
            return 0, "different-serial"
        raise AssertionError(command)

    monkeypatch.setattr(adb_target, "run", fake_run)
    assert asyncio.run(adb_target.find("adb", "fde47c41", 5)) is False
    assert adb_target.current("fde47c41") == "fde47c41"


def test_offline_wifi_device_triggers_recovery():
    assert adb_target.looks_missing("error: device offline") is True
    assert adb_target.looks_missing("ls: cannot access file") is False


def test_the_arm_re_finds_the_robot_when_adb_says_not_found(monkeypatch):
    monkeypatch.setattr(settings, "robot_arm_adb", "adb")
    monkeypatch.setattr(settings, "robot_arm_adb_serial", "fde47c41")
    seen = []

    async def fake_run(args, timeout):
        seen.append(args[2])                                          # the -s name used
        if args[2] == "fde47c41":
            return 1, "adb.exe: device 'fde47c41' not found"
        return 0, "/dev/ttyUSB11"

    async def fake_find(adb, serial, timeout):
        adb_target._names[serial] = "192.168.1.197:5555"
        return True

    monkeypatch.setattr(robot_arm, "_run", fake_run)
    monkeypatch.setattr(adb_target, "find", fake_find)
    code, out = asyncio.run(robot_arm._on_robot("echo"))
    assert (code, out) == (0, "/dev/ttyUSB11")
    assert seen == ["fde47c41", "192.168.1.197:5555"]


def test_an_ordinary_failure_is_not_mistaken_for_a_missing_device(monkeypatch):
    monkeypatch.setattr(settings, "robot_arm_adb_serial", "fde47c41")
    calls = []

    async def fake_run(args, timeout):
        calls.append(args)
        return 1, "ls: /dev/ttyUSB10: No such file or directory"

    async def never(*a):
        raise AssertionError("find() must not run for a normal failure")

    monkeypatch.setattr(robot_arm, "_run", fake_run)
    monkeypatch.setattr(adb_target, "find", never)
    assert asyncio.run(robot_arm._on_robot("ls /dev/ttyUSB10"))[0] == 1
    assert len(calls) == 1


def test_both_robot_modules_go_through_the_shared_runner_and_name():
    for module in (robot_arm, robot_android):
        src = Path(module.__file__).read_text(encoding="utf-8")
        assert "create_subprocess_exec(" not in src, module.__name__
        assert "adb_target.run(" in src and "adb_target.current(" in src
        assert "adb_target.executable(settings.robot_arm_adb)" in src
