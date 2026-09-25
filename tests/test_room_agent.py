"""The room agent stands alone, and every gate on it is tested from outside.

It is deliberately NOT wired into Emma yet (owner, 2026-09-24: "อย่าเพิ่งเอาไป
ใส่ที่ emma นะ ทำทดสอบไปก่อน"), so these tests are the only thing holding its
shape. They drive the HTTP surface a room PC would expose, not the functions
behind it — a gate that is only tested through its own helper is a gate the
next caller can walk around.
"""

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
    # The action list IS the contract. A server reading this knows exactly what
    # this machine will do, without a version number to compare.
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
    assert {n for n, c in room_agent.CAPABILITIES.items() if c.risk == "read"} == {"list_windows", "screenshot"}


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
