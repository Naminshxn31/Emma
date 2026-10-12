"""The room agent stands alone, and every gate on it is tested from outside.

It is deliberately NOT wired into Emma yet (owner, 2026-09-24: "อย่าเพิ่งเอาไป
ใส่ที่ emma นะ ทำทดสอบไปก่อน"), so these tests are the only thing holding its
shape. They drive the HTTP surface a room PC would expose, not the functions
behind it — a gate that is only tested through its own helper is a gate the
next caller can walk around.
"""

import dataclasses
import inspect
import json

import pytest
from fastapi.testclient import TestClient

from app import room_agent


@pytest.fixture
def launched(monkeypatch):
    """Nothing may actually start during a test — learned the hard way.

    The first version of this file called the real `open_program` with "cmd",
    and on this Windows machine that matched exactly one Start Menu entry
    ("git cmd") and **opened a shell window on the owner's desktop**. It is the
    same family as the day pytest put a fullscreen kiosk Chrome on screen:
    a test that reaches a real launcher is a test that uses the machine.
    """
    from app.tools import computer

    calls = []
    monkeypatch.setattr(computer, "on_loop", lambda fn, *a, **k: calls.append((fn, a, k)))
    return calls


@pytest.fixture
def agent(monkeypatch, tmp_path, launched):
    monkeypatch.setattr(room_agent, "ROOM_NAME", "ห้องทดสอบ")
    monkeypatch.setattr(room_agent, "ROOM_TOKEN", "test-token")
    monkeypatch.setattr(room_agent, "PROGRAMS", {"notepad"})
    # Everything is off by default; a test that wants a capability says so.
    monkeypatch.setattr(room_agent, "ALLOW", set(room_agent.CAPABILITIES) - {"type_text", "mouse_click"})
    monkeypatch.setattr(room_agent, "INPUT_ENABLED", False)
    monkeypatch.setattr(room_agent, "LOG_DIR", tmp_path / "room-agent")
    return TestClient(room_agent.app)


def _log_lines(tmp_path):
    files = sorted((tmp_path / "room-agent").glob("*.jsonl"))
    return [json.loads(line) for f in files for line in f.read_text(encoding="utf-8").splitlines()]


def test_health_says_which_room_and_whether_it_is_armed(agent):
    body = agent.get("/health").json()
    assert body["ok"] is True
    assert body["room"] == "ห้องทดสอบ"
    assert body["armed"] is True
    # The action list IS the compatibility contract: a server reading this knows
    # exactly what this machine will do. code_version is a separate thing -- just
    # enough for the console to see a self-update took (it changes with the code).
    assert isinstance(body["code_version"], str) and body["code_version"]
    # a MAC hint (or blank) so the console can fill rooms.json for Wake-on-LAN
    m = body["mac_hint"]
    assert m == "" or (len(m) == 17 and m.count(":") == 5)
    assert body["programs"] == ["notepad"]
    # The catalogue is published in full, the enabled list is what is live.
    assert set(body["catalogue"]) == set(room_agent.CAPABILITIES)
    assert "type_text" in body["catalogue"] and "type_text" not in body["enabled"]
    assert body["input_control"] is False


def test_an_agent_with_no_token_refuses_everything(monkeypatch, tmp_path):
    """Fail closed, and say why.

    `WS_TOKEN` shipped the other way round — HOST=0.0.0.0 next to an empty
    token — and that inversion is the whole reason this test exists. An
    unconfigured machine must be inert, not open.
    """
    monkeypatch.setattr(room_agent, "ROOM_TOKEN", "")
    monkeypatch.setattr(room_agent, "LOG_DIR", tmp_path / "room-agent")
    client = TestClient(room_agent.app)

    assert client.get("/health").json()["armed"] is False
    reply = client.post("/do", json={"action": "open_program", "args": {"name": "notepad"}})
    assert reply.status_code == 503
    assert reply.json()["ok"] is False
    # Refusals are logged too: "nothing happened" and "nobody asked" look the
    # same to whoever is holding the phone at the other end.
    assert [line["action"] for line in _log_lines(tmp_path)] == ["(refused)"]


def test_the_wrong_token_is_refused_and_recorded(agent, tmp_path):
    reply = agent.post("/do", json={"action": "open_program", "args": {"name": "notepad"}},
                       headers={"X-Room-Token": "not-it"})
    assert reply.status_code == 401
    assert _log_lines(tmp_path)[0]["detail"] == "bad token"


def test_only_listed_actions_exist(agent):
    reply = agent.post("/do", json={"action": "run_anything", "args": {"cmd": "cmd.exe"}},
                       headers={"X-Room-Token": "test-token"})
    body = reply.json()
    assert body["ok"] is False
    assert body["error"] == "unknown action"
    assert "run_anything" not in body["known"]


def test_a_missing_argument_is_refused_before_anything_runs(agent):
    body = agent.post("/do", json={"action": "open_program", "args": {}},
                      headers={"X-Room-Token": "test-token"}).json()
    assert body == {"ok": False, "error": "missing argument", "missing": ["name"]}


def test_a_room_opens_nothing_it_was_not_given(agent, launched):
    """The room's own list is the narrow gate, and it is checked first.

    Measured on this machine: `open_program`'s Start Menu list holds 120
    entries and "cmd" matches exactly one of them, "git cmd" — which is how
    the first run of this file opened a shell on the owner's desktop. A list
    of what is installed is not a list of what a stranger at a microphone may
    start, so the room names its own programs and defaults to none.
    """
    for name in ("cmd", "git cmd", "regedit", "powershell", "diskpart", "chrome"):
        body = agent.post("/do", json={"action": "open_program", "args": {"name": name}},
                          headers={"X-Room-Token": "test-token"}).json()
        assert body["ok"] is False, name
        assert body["error"] == "program not allowed here", (name, body)
    # Refused before anything is resolved, let alone started.
    assert launched == []


def test_an_empty_list_means_this_room_opens_nothing_at_all(agent, monkeypatch, launched):
    monkeypatch.setattr(room_agent, "PROGRAMS", set())
    body = agent.post("/do", json={"action": "open_program", "args": {"name": "notepad"}},
                      headers={"X-Room-Token": "test-token"}).json()
    assert body["ok"] is False and body["allowed"] == []
    assert launched == []


def test_a_listed_program_goes_through_the_start_menu_lookup_as_well(agent, monkeypatch, launched):
    """Two gates, and the second one is the existing tool, not a copy of it."""
    from app.tools import computer

    monkeypatch.setattr(computer, "_installed_apps", lambda: {"notepad": "C:/fake/notepad.lnk"})
    body = agent.post("/do", json={"action": "open_program", "args": {"name": "notepad"}},
                      headers={"X-Room-Token": "test-token"}).json()
    assert body == {"ok": True, "opened": "notepad"}
    assert len(launched) == 1 and launched[0][1] == ("C:/fake/notepad.lnk",)


def test_a_path_is_still_not_a_program_name(agent, monkeypatch, launched):
    monkeypatch.setattr(room_agent, "PROGRAMS", {"c:/windows/system32/cmd.exe"})
    body = agent.post("/do", json={"action": "open_program",
                                   "args": {"name": "C:/Windows/system32/cmd.exe"}},
                      headers={"X-Room-Token": "test-token"}).json()
    # Even if someone puts a path in the room's list, the tool underneath
    # refuses it: "open a program" never becomes "execute this file".
    assert body == {"ok": False, "error": "not a program name",
                    "instruction": "บอกชื่อโปรแกรมเฉยๆ ไม่รับ path"}
    assert launched == []


def test_the_log_keeps_the_action_and_never_the_words_that_were_asked_for(agent, tmp_path):
    agent.post("/do", json={"action": "open_program", "args": {"name": "a-secret-looking-name"}},
               headers={"X-Room-Token": "test-token"})
    lines = _log_lines(tmp_path)
    assert lines and lines[-1]["action"] == "open_program"
    assert lines[-1]["room"] == "ห้องทดสอบ"
    # Same rule as the server's tool log: argument NAMES are in the contract,
    # their values are not written down. A log that quietly collects what
    # people asked for is a box of personal data nobody decided to keep.
    assert "a-secret-looking-name" not in json.dumps(lines, ensure_ascii=False)


def test_nothing_is_enabled_until_the_room_says_so(monkeypatch, tmp_path, launched):
    """Owner: "ส่วนที่ว่าจะให้ใช้อะไรบ้างค่อยมาจำกัด".

    Writing the abilities first and narrowing later is only safe in that
    order — so a freshly installed agent has every capability switched off,
    including the read-only ones. Growing the catalogue then cannot grow what
    any room can do.
    """
    monkeypatch.setattr(room_agent, "ROOM_TOKEN", "test-token")
    monkeypatch.setattr(room_agent, "ALLOW", set())
    monkeypatch.setattr(room_agent, "LOG_DIR", tmp_path / "room-agent")
    client = TestClient(room_agent.app)

    assert client.get("/health").json()["enabled"] == []
    for action in room_agent.CAPABILITIES:
        body = client.post("/do", json={"action": action, "args": {}},
                           headers={"X-Room-Token": "test-token"}).json()
        assert body["ok"] is False, action
        assert body["error"] == "not enabled here", (action, body)
    assert launched == []


def test_typing_and_clicking_need_a_second_switch(agent, monkeypatch):
    """General keyboard and mouse control is not just another action.

    One misheard word becomes keystrokes on a real machine, so being listed in
    ROOM_AGENT_ALLOW is not enough — the machine has to say ROOM_AGENT_INPUT
    as well. Nobody turns this on by copying a line.
    """
    monkeypatch.setattr(room_agent, "ALLOW", set(room_agent.CAPABILITIES))
    for action, args in (("type_text", {"text": "hello"}), ("mouse_click", {"x": 10, "y": 10})):
        body = agent.post("/do", json={"action": action, "args": args},
                          headers={"X-Room-Token": "test-token"}).json()
        assert body == {"ok": False, "error": "input control is off",
                        "instruction": "เครื่องนี้ปิดการคุมคีย์บอร์ด/เมาส์ไว้ ให้บอกตรงๆ ห้ามบอกว่าทำแล้ว"}


def test_keys_come_from_a_fixed_map_not_from_free_text(agent):
    """A presentation is driven without handing over the keyboard."""
    body = agent.post("/do", json={"action": "press_key", "args": {"key": "ctrl+alt+del"}},
                      headers={"X-Room-Token": "test-token"}).json()
    assert body["ok"] is False and body["error"] == "unknown key"
    assert "next_slide" in body["known"] and "previous_slide" in body["known"]


def test_the_catalogue_and_its_risk_labels_stay_honest():
    """Every capability declares what it is, and only two are `input`.

    The risk label is what the second switch keys off, so a new action that
    types or clicks must be labelled or it would ride in on `act`.
    """
    for name, cap in room_agent.CAPABILITIES.items():
        assert cap.risk in {"read", "act", "input"}, name
        assert cap.what, name
    assert {n for n, c in room_agent.CAPABILITIES.items() if c.risk == "input"} == {"type_text", "mouse_click"}
    # The read-only ones must not be able to change anything by definition.
    assert {n for n, c in room_agent.CAPABILITIES.items() if c.risk == "read"} == {
        "list_windows", "screenshot", "screen"}


def test_reboot_and_shutdown_are_act_and_honest():
    """Power actions are `act` (not input), Windows-guarded, and must not run in
    a test -- so this only inspects them, never calls them. shutdown must be
    honest that the PC cannot be powered back on from here."""
    for name in ("reboot", "shutdown"):
        cap = room_agent.CAPABILITIES.get(name)
        assert cap is not None and cap.risk == "act", name
    src_r = inspect.getsource(room_agent.reboot)
    src_s = inspect.getsource(room_agent.shutdown)
    assert '"/r"' in src_r and "_unsupported" in src_r     # restart; no-op off Windows
    assert '"/s"' in src_s and "_unsupported" in src_s     # shutdown
    assert "Wake-on-LAN" in src_s                          # says it cannot power back on from here


def _agent_bundle(extra=None):
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("run_room_agent.py", "run = 1\n")
        zf.writestr("app/room_agent.py", "agent = 2\n")
        zf.writestr("app/tools/computer.py", "tools = 3\n")
        for name, body in (extra or {}).items():
            zf.writestr(name, body)
    return buf.getvalue()


def test_apply_update_bundle_writes_code_and_rejects_junk(tmp_path):
    """The self-update writes only the agent code, from a validated bundle, into
    a staging-then-swap. Pure file work -- no network, no restart -- so it is
    unit-tested here rather than by running the real `update`."""
    target = tmp_path / "install"
    assert room_agent._apply_update_bundle(_agent_bundle(), target)["ok"] is True
    assert (target / "run_room_agent.py").read_text() == "run = 1\n"
    assert (target / "app" / "room_agent.py").read_text() == "agent = 2\n"
    assert room_agent._apply_update_bundle(b"not a zip", target)["ok"] is False
    # a zip that is not an agent is refused
    import io
    import zipfile
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as zf:
        zf.writestr("random.txt", "hi")
    assert room_agent._apply_update_bundle(b.getvalue(), target)["ok"] is False
    # a path that escapes the tree is refused
    r = room_agent._apply_update_bundle(_agent_bundle({"../evil.py": "boom"}), target)
    assert r["ok"] is False and "unsafe" in r["error"]
    # a drive-letter / colon path is refused too (C:evil, C:/…)
    r = room_agent._apply_update_bundle(_agent_bundle({"C:evil.py": "boom"}), target)
    assert r["ok"] is False and "unsafe" in r["error"]


def _bundle_with(app_body: str) -> bytes:
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("run_room_agent.py", "run = 1\n")
        zf.writestr("app/room_agent.py", app_body)
    return buf.getvalue()


def test_apply_update_refuses_code_that_does_not_import_and_touches_nothing(tmp_path):
    """The gate that makes self-update safe: a bundle whose agent will not even
    import must be rejected BEFORE it replaces the live code, not discovered
    after the swap when the agent is down and nobody is at the machine."""
    target = tmp_path / "install"
    (target / "app").mkdir(parents=True)
    (target / "app" / "room_agent.py").write_text("old = 1\n")
    r = room_agent._apply_update_bundle(_bundle_with("def broken(:\n"), target)
    assert r["ok"] is False and "does not run" in r["error"]
    assert (target / "app" / "room_agent.py").read_text() == "old = 1\n"     # untouched
    assert not (target / "app.prev").exists()


def test_apply_update_keeps_the_old_code_in_app_prev_for_rollback(tmp_path):
    """A good update swaps atomically and leaves the previous code in app.prev,
    which is what the supervisor restores if the new code fails to boot."""
    target = tmp_path / "install"
    (target / "app").mkdir(parents=True)
    (target / "app" / "room_agent.py").write_text("old = 1\n")
    (target / "run_room_agent.py").write_text("oldrun = 1\n")
    assert room_agent._apply_update_bundle(_agent_bundle(), target)["ok"] is True
    assert (target / "app" / "room_agent.py").read_text() == "agent = 2\n"        # new is live
    assert (target / "app.prev" / "room_agent.py").read_text() == "old = 1\n"     # old is kept


def test_update_refuses_a_bundle_whose_hash_does_not_match(monkeypatch):
    """The console attaches the sha256 it built locally; a rogue LAN host serving
    different bytes over plain HTTP fails the check and nothing is applied."""
    import urllib.request

    monkeypatch.setenv("ROOM_AGENT_UPDATE_URL", "http://x/agent-bundle")

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n=-1): return b"pretend-bundle-bytes"

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())

    def _must_not_apply(*a, **k):
        raise AssertionError("a hash mismatch must be refused before applying")
    monkeypatch.setattr(room_agent, "_apply_update_bundle", _must_not_apply)

    r = room_agent.update(sha256="deadbeefdeadbeef")    # not the hash of those bytes
    assert r["ok"] is False and "integrity" in r["error"]


def test_update_refuses_without_a_configured_source(monkeypatch):
    """No ROOM_AGENT_UPDATE_URL -> it does nothing (no network, no restart), so
    this is safe to call. The source is fixed in .env, never in the request."""
    monkeypatch.delenv("ROOM_AGENT_UPDATE_URL", raising=False)
    r = room_agent.update()
    assert r["ok"] is False and "no update source" in r["error"]


def test_update_is_a_listed_act_capability():
    cap = room_agent.CAPABILITIES.get("update")
    assert cap is not None and cap.risk == "act"


def test_every_listed_action_exists_on_the_machine_side(agent):
    """The map and the functions cannot drift apart.

    An action that names a function nobody wrote answers "unknown action" at
    the wrong layer — the server would see a live agent that refuses a listed
    action, which reads as a network fault rather than a typo.
    """
    for name, cap in room_agent.CAPABILITIES.items():
        assert callable(cap.run), name


def test_the_agent_is_not_wired_into_emma_yet():
    """Owner: "อย่าเพิ่งเอาไปใส่ที่ emma นะ".

    Nothing in the server may reach for the agent until that changes. This is
    the test that turns a verbal "not yet" into something that fails out loud.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for path in sorted((root / "app").rglob("*.py")):
        if path.name == "room_agent.py":
            continue
        assert "room_agent" not in path.read_text(encoding="utf-8"), path


def test_what_the_agent_writes_on_a_room_pc_never_reaches_the_repo():
    """`screenshot` saves a full-desktop capture under data/room-agent/shots/.

    That is whatever happened to be on a sales-room monitor: a customer's
    details, an inventory sheet, somebody's mail. It is taken by a service
    driven from ANOTHER machine, so unlike every other file in data/ there is
    nobody standing there deciding to keep it. Found by running the agent for
    real on 2026-09-25 -- the unit tests patch the capture away, so the path it
    writes to had never once existed while a test was looking.

    Same rule as data/logs/: local evidence, not history.
    """
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for written in ("data/room-agent/2026-01-01.jsonl",
                    "data/room-agent/shots/20260101-000000.png"):
        result = subprocess.run(["git", "check-ignore", "-q", written],
                                cwd=root, capture_output=True, text=True, encoding="utf-8")
        # exit 0 == ignored. Anything else and a screenshot of a sales desk is
        # one `git add -A` away from the history.
        assert result.returncode == 0, written


def test_the_agent_reads_the_env_file_sitting_next_to_it():
    """Every other module here gets load_dotenv() for free by importing
    app.config. This one does not import it -- app.tools registers Emma's tools
    as a side effect, so the capabilities reach for it late -- which meant the
    settings were read from an environment nothing had populated.

    A room PC then started on the default port, unnamed, with no token and
    every action refused, while its own .env said otherwise two lines away. The
    boot banner was honest about the state and said nothing about the cause.
    Caught by running a built room package rather than exporting the variables
    by hand (2026-09-25).
    """
    from pathlib import Path

    import re

    source = Path(room_agent.__file__).read_text(encoding="utf-8")
    # Both sides of this comparison have to be the CODE, never a mention of it.
    # The first draft searched for the plain text "load_dotenv(" and matched the
    # comment above the call explaining why it is there, which sits above
    # everything -- so the test passed with the call moved to the bottom, which
    # is exactly the broken version. Proven by moving it and watching it stay
    # green. A module-level call starts its own line at column 0.
    load = re.search(r"^load_dotenv\(", source, re.M)
    assert load, "nothing loads this machine's .env any more"
    for setting in ("ROOM_AGENT_TOKEN", "ROOM_AGENT_ALLOW", "ROOM_AGENT_PORT"):
        read = re.search(r'(?:os\.getenv|_list)\(\s*"%s"' % setting, source)
        assert read, f"nothing reads {setting} any more"
        assert load.start() < read.start(), f"{setting} is read before .env is loaded"


def _load_supervisor():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "run_room_agent.py"
    spec = importlib.util.spec_from_file_location("run_room_agent_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_supervisor_runs_the_agent_as_a_child_and_logs_headless():
    """run_room_agent is a supervisor: it runs the agent as a CHILD process so a
    self-update can restart it (and roll a bad one back) without anyone at the
    room PC. Under pythonw a detached process has no console, so it still points
    a logfile at the child (the 2026-09-25 headless-start fix)."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "run_room_agent.py").read_text(encoding="utf-8")
    assert "_log_stream" in src                        # headless logging kept
    assert "pythonw" in src and "agent.log" in src
    assert "app.room_agent" in src and "Popen" in src  # the agent runs as a child
    assert "UPDATE_EXIT_CODE" in src and "app.prev" in src   # restart + rollback


def test_the_supervisor_relaunches_on_update_and_rolls_back_a_bad_one(monkeypatch):
    """The loop that makes self-update safe: exit 97 relaunches into new code; a
    crash right after an update restores app.prev instead of leaving the agent
    down. Driven with a fake child runner so nothing actually starts."""
    sup = _load_supervisor()
    monkeypatch.setattr(sup, "_log_stream", lambda: None)
    monkeypatch.setattr(sup.time, "sleep", lambda s: None)
    rolled = []
    monkeypatch.setattr(sup, "_roll_back", lambda say: rolled.append(True) or False)

    seq = [sup.UPDATE_EXIT_CODE, 1]                    # update asked; new code crashes fast
    calls = []

    class _Stop(Exception):
        pass

    def fake_run(stream):
        if not seq:
            raise _Stop
        code = seq.pop(0)
        calls.append(code)
        return code

    with pytest.raises(_Stop):
        sup.main(run_agent=fake_run)
    assert calls == [sup.UPDATE_EXIT_CODE, 1]          # it DID relaunch after the update
    assert rolled == [True]                            # and rolled back the crash


def test_the_supervisor_can_actually_restore_app_prev(tmp_path, monkeypatch):
    """_roll_back swaps app.prev back into app (keeping the broken code as
    app.bad), on real directories."""
    sup = _load_supervisor()
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "room_agent.py").write_text("broken = 1\n")
    (tmp_path / "app.prev").mkdir()
    (tmp_path / "app.prev" / "room_agent.py").write_text("good = 1\n")
    monkeypatch.setattr(sup, "ROOT", tmp_path)
    monkeypatch.setattr(sup, "_APP", tmp_path / "app")
    monkeypatch.setattr(sup, "_PREV", tmp_path / "app.prev")
    assert sup._roll_back(lambda m: None) is True
    assert (tmp_path / "app" / "room_agent.py").read_text() == "good = 1\n"
    assert (tmp_path / "app.bad" / "room_agent.py").read_text() == "broken = 1\n"
    assert not (tmp_path / "app.prev").exists()


def test_the_screen_capability_returns_an_image_for_the_console():
    """`screen` is the console's live view: unlike `screenshot` (saves to disk,
    returns a path) it returns the frame as base64 JPEG so the operator can watch
    the room PC. Read-risk, and gated like everything else."""
    cap = room_agent.CAPABILITIES.get("screen")
    assert cap is not None and cap.risk == "read"
    result = room_agent.screen()
    # On a machine with a display + Pillow it returns an image; on a headless CI
    # box it returns a clean "unsupported" -- never a crash, and never a path.
    assert "saved" not in result
    if result.get("ok"):
        assert result["jpeg_base64"] and isinstance(result["size"], list)
    else:
        assert result.get("error") or result.get("detail")


def test_a_crashing_capability_comes_back_as_json_not_a_500(agent, monkeypatch):
    """A capability that raises must return a JSON error the console (and Emma
    later) can read, never a bare 500. Found 2026-09-25: `monitor` blocked/failed
    and the console got plain 'Internal Server Error' it could not JSON-parse."""
    def boom(**kwargs):
        raise RuntimeError("kaboom")
    original = room_agent.CAPABILITIES["list_windows"]
    monkeypatch.setitem(room_agent.CAPABILITIES, "list_windows",
                        dataclasses.replace(original, run=boom))
    r = agent.post("/do", json={"action": "list_windows"}, headers={"x-room-token": "test-token"})
    assert r.status_code == 200                       # JSON, not a 500
    body = r.json()
    assert body["ok"] is False
    assert "action failed" in body["error"]
    assert "RuntimeError" in body.get("detail", "")   # labelled, not the whole body


def test_monitor_is_time_boxed_so_a_hung_window_cannot_freeze_the_agent():
    """`monitor` broadcasts to every top-level window; a plain SendMessageW is
    synchronous and one hung window froze the whole agent (2026-09-25). It must
    use the time-boxed, abort-if-hung call instead."""
    src = inspect.getsource(room_agent.monitor)
    assert "SendMessageTimeoutW" in src
    assert "SMTO_ABORTIFHUNG" in src
    assert "SendMessageW(" not in src                 # the unbounded blocking broadcast is gone
    # The prototype is spelled out so the out-buffer is pointer-sized and the "on"
    # value (-1) sign-extends on Win64 -- not marshalled as a 32-bit int.
    assert "argtypes" in src and "c_ssize_t" in src


def test_capabilities_run_off_the_event_loop():
    """A blocking ctypes call must not freeze the agent for every other request,
    so the capability runs in a worker thread."""
    src = inspect.getsource(room_agent.run_action)
    assert "asyncio.to_thread" in src
