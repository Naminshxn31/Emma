"""The robot opens/closes a room's big screen itself (owner 28 Oct 2026:
"จอห้องนั้นต้องเปิดเว็บทิ้งไว้หรอ ไม่เอาๆ เอาแบบหุ่นไปอยู่ใกล้จอห้องนั้น หรือสั่งให้เปิดจอ
ก็ให้หุ่นเชื่อมจอเลย"): the room PC's two screen actions (app/room_agent.py),
the one-time ticket (app/mirror_server.py), the robot side
(app/room_screens.py) and the `room_screen` tool (app/tools/screens.py)."""
import asyncio
import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import heard, room_agent, room_screens
from app.config import settings

ROOT = Path(__file__).resolve().parent.parent


# ---- the room PC: only the face page, in its own kiosk profile ---------------------------

@pytest.fixture
def pc(monkeypatch, tmp_path):
    """The agent with both screen actions on, and nothing real started."""
    monkeypatch.setattr(room_agent, "ROOM_TOKEN", "pc-key")
    monkeypatch.setattr(room_agent, "ALLOW", {"emma_screen_open", "emma_screen_close"})
    monkeypatch.setattr(room_agent, "LOG_DIR", tmp_path / "room-agent")
    monkeypatch.setattr(room_agent, "EMMA_SCREEN_PROFILE", tmp_path / "room-agent" / "emma-screen")
    monkeypatch.setattr(room_agent, "WINDOWS", True)
    calls = []
    monkeypatch.setattr(room_agent, "_emma_browser", lambda: r"C:\Chrome\chrome.exe")
    monkeypatch.setattr(room_agent, "_emma_windows", lambda close: calls.append(("windows", close)) or 1)
    monkeypatch.setattr(room_agent, "monitor", lambda state: calls.append(("monitor", state)) or {"ok": True})
    monkeypatch.setattr(room_agent, "_keep_display_on", lambda on: calls.append(("awake", on)))
    # the screens of the machine running the tests are not the room's
    monkeypatch.setattr(room_agent, "displays", lambda: [
        {"device": "DISPLAY1", "primary": True, "x": 0, "y": 0, "width": 1920, "height": 1080, "mm": [0, 0], "hmon": 1}])

    import subprocess

    class Proc:
        pid = 4242

    def popen(args, **kw):
        calls.append(("popen", args))
        return Proc()

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(room_agent, "_bring_to_front",
                        lambda pid, wait_s=0, display=None: calls.append(("front", pid)))
    return TestClient(room_agent.app), calls, tmp_path


def _do(client, action, **args):
    return client.post("/do", json={"action": action, "args": args}, headers={"x-room-token": "pc-key"}).json()


def test_the_pc_opens_only_the_robot_face_page_in_a_kiosk_of_its_own(pc):
    client, calls, tmp = pc
    url = "http://192.168.1.197:8002/mirror?ticket=abc"
    out = _do(client, "emma_screen_open", url=url)
    assert out == {"ok": True, "opened": "emma_screen", "browser": "chrome", "display": "DISPLAY1"}
    # the old window goes first: one face, never a second one on top
    assert calls.index(("windows", True)) < next(i for i, c in enumerate(calls) if c[0] == "popen")
    args = next(c[1] for c in calls if c[0] == "popen")
    assert args[0] == r"C:\Chrome\chrome.exe" and args[-1] == url
    assert "--kiosk" in args and "--autoplay-policy=no-user-gesture-required" in args
    assert "--disable-background-networking" in args
    # its own profile: the everyday one hands the URL to the running browser and drops --kiosk
    assert args[1] == "--user-data-dir=%s" % (tmp / "room-agent" / "emma-screen")
    assert ("awake", True) in calls and ("monitor", "on") in calls
    # over whatever the room PC shows: 28 Oct it came up behind a PowerShell window
    import time as _time

    for _ in range(50):
        if ("front", 4242) in calls:
            break
        _time.sleep(0.02)
    assert ("front", 4242) in calls
    for bad in ("https://example.com/", "http://192.168.1.197:8002/", "file:///C:/x/mirror",
                "http://192.168.1.197:8002/mirror/../x", "http://u:p@192.168.1.197:8002/mirror",
                "http://192.168.1.197:8002/mirror#x", "javascript:alert(1)", "http://:99999/mirror"):
        assert _do(client, "emma_screen_open", url=bad)["ok"] is False, bad
    assert sum(1 for c in calls if c[0] == "popen") == 1


TWO_SCREENS = [  # the multimedia room's PC, 28 Oct (EDID: an MSI 22" and a screen with no size)
    {"device": "DISPLAY1", "primary": False, "x": -1920, "y": 0, "width": 1920, "height": 1080, "mm": [480, 260], "hmon": 1},
    {"device": "DISPLAY2", "primary": True, "x": 0, "y": 0, "width": 1920, "height": 1080, "mm": [0, 0], "hmon": 2},
]


def test_the_face_goes_on_the_screen_the_table_names(pc, monkeypatch):
    """Owner 28 Oct: "มันจะมี 2 จอ ให้ขึ้นจอใหญ่ได้ไหม"."""
    client, calls, _ = pc
    monkeypatch.setattr(room_agent, "displays", lambda: [dict(d) for d in TWO_SCREENS])
    fronts = []
    monkeypatch.setattr(room_agent, "_bring_to_front", lambda pid, wait_s=0, display=None: fronts.append(display))
    url = "http://192.168.1.197:8002/mirror?ticket=a"
    out = _do(client, "emma_screen_open", url=url)                       # no choice: the primary
    assert out["display"] == "DISPLAY2"
    assert next(c[1] for c in calls if c[0] == "popen")[-2] == "--window-position=480,270"
    calls.clear()
    out = _do(client, "emma_screen_open", url=url, display="DISPLAY1")
    assert out["display"] == "DISPLAY1"
    assert next(c[1] for c in calls if c[0] == "popen")[-2] == "--window-position=-1440,270"
    for _ in range(50):
        if len(fronts) == 2:
            break
        __import__("time").sleep(0.02)
    assert fronts[-1]["device"] == "DISPLAY1"          # moved there if Chrome put it elsewhere
    assert _do(client, "emma_screen_open", url=url, display="secondary")["display"] == "DISPLAY1"
    bad = _do(client, "emma_screen_open", url=url, display="DISPLAY7")
    assert bad["ok"] is False and bad["displays"] == ["DISPLAY1", "DISPLAY2"]


def test_health_lists_the_screens_for_the_robots_table(pc, monkeypatch):
    client, _, _ = pc
    monkeypatch.setattr(room_agent, "displays", lambda: [dict(d) for d in TWO_SCREENS])
    shown = client.get("/health").json()["displays"]
    assert [d["device"] for d in shown] == ["DISPLAY1", "DISPLAY2"]
    assert all("hmon" not in d for d in shown)                         # a handle is no one's business


def test_the_robot_sends_the_display_its_table_names(tmp_path, monkeypatch):
    _table(tmp_path, monkeypatch, [dict(A, display="DISPLAY1"), dict(B, display="../../x"), FAR])
    by_name = {s["name"]: s for s in room_screens.screens()}
    assert by_name["ห้องขาย 2"]["display"] == "DISPLAY1"
    assert "display" not in by_name["ห้องประชุม"]                      # not a screen name: dropped
    monkeypatch.setattr(settings, "mirror_port", 8002)
    monkeypatch.setattr(settings, "mirror_token", "t")
    monkeypatch.setattr(room_screens, "_robot_ip_toward", lambda host: "192.168.1.197")
    monkeypatch.setattr(room_screens, "current_pose", lambda: None)
    sent = []

    async def ask(screen, action, args, timeout=0):
        sent.append(args)
        return {"ok": True}

    monkeypatch.setattr(room_screens, "_ask", ask)
    asyncio.run(room_screens.open_screen("ห้องขาย 2"))
    assert sent[-1]["display"] == "DISPLAY1"
    asyncio.run(room_screens.open_screen("ไม่มีตำแหน่ง"))
    assert "display" not in sent[-1]


def test_closing_takes_the_window_down_and_lets_the_display_sleep(pc):
    client, calls, _ = pc
    assert _do(client, "emma_screen_close") == {"ok": True, "closed": "emma_screen"}
    assert ("windows", True) in calls and ("awake", False) in calls


def test_the_pc_log_names_the_action_never_the_address_it_was_handed(pc):
    client, _, tmp = pc
    _do(client, "emma_screen_open", url="http://192.168.1.197:8002/mirror?ticket=SECRETTICKET")
    text = "".join(p.read_text(encoding="utf-8") for p in (tmp / "room-agent").glob("*.jsonl"))
    assert "emma_screen" in text and "SECRETTICKET" not in text and "8002" not in text


def test_both_screen_actions_are_off_until_the_room_turns_them_on(monkeypatch, tmp_path):
    monkeypatch.setattr(room_agent, "ROOM_TOKEN", "pc-key")
    monkeypatch.setattr(room_agent, "ALLOW", set())
    monkeypatch.setattr(room_agent, "LOG_DIR", tmp_path)
    out = _do(TestClient(room_agent.app), "emma_screen_open", url="http://10.0.0.1:8002/mirror")
    assert out["ok"] is False and out["error"] == "not enabled here"


def test_the_window_is_found_by_its_profile_path_with_no_shell_quoting(monkeypatch, tmp_path):
    """Found again by the profile in its command line, not a remembered pid
    (the agent restarts; pids get reused), closed politely before by force."""
    import subprocess

    profile = tmp_path / "it's here" / "emma-screen"
    monkeypatch.setattr(room_agent, "EMMA_SCREEN_PROFILE", profile)
    seen = {}

    def run(args, **kw):
        seen["args"] = args

        class Done:
            stdout = b"2\r\n"
        return Done()

    monkeypatch.setattr(subprocess, "run", run)
    assert room_agent._emma_windows(close=True) == 2
    args = seen["args"]
    assert args[:4] == ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand"]
    script = base64.b64decode(args[4]).decode("utf-16-le")
    assert str(profile).replace("'", "''") in script          # PS single-quote escape
    assert "CloseMainWindow" in script and script.index("CloseMainWindow") < script.index("Stop-Process")
    assert "if ($true)" in script
    room_agent._emma_windows(close=False)
    assert "if ($false)" in base64.b64decode(seen["args"][4]).decode("utf-16-le")


def test_the_kiosk_profile_offers_no_translation_and_no_restore(monkeypatch, tmp_path):
    """28 Oct, the first open on a real desktop: Google Translate's bubble over
    the kiosk (an English Chrome, a Thai page)."""
    monkeypatch.setattr(room_agent, "EMMA_SCREEN_PROFILE", tmp_path / "emma-screen")
    room_agent._prepare_profile()                                 # a profile that never ran
    prefs = tmp_path / "emma-screen" / "Default" / "Preferences"
    data = json.loads(prefs.read_text(encoding="utf-8"))
    assert data["translate"]["enabled"] is False
    assert data["profile"] == {"exit_type": "Normal", "exited_cleanly": True}
    data["profile"]["exit_type"] = "Crashed"                      # killed last time
    data["kept"] = 1
    prefs.write_text(json.dumps(data), encoding="utf-8")
    room_agent._prepare_profile()
    again = json.loads(prefs.read_text(encoding="utf-8"))
    assert again["profile"]["exit_type"] == "Normal" and again["kept"] == 1
    page = (ROOT / "client" / "voice-preview.html").read_text(encoding="utf-8")
    head = page[:page.index("</script>")]
    assert "setAttribute('translate', 'no')" in head and "location.pathname === '/mirror'" in head


# ---- the ticket: the URL on the room PC is not the token ---------------------------------

@pytest.fixture
def mirror(monkeypatch):
    from app import mirror_server

    monkeypatch.setattr(settings, "mirror_token", "the-mirror-token")
    mirror_server._tickets.clear()
    yield mirror_server, TestClient(mirror_server.app)
    mirror_server._tickets.clear()


def test_a_ticket_opens_the_page_once_then_the_cookie_carries_on(mirror):
    ms, client = mirror
    ticket = ms.issue_ticket()
    first = client.get("/mirror?ticket=%s&sound=1" % ticket, follow_redirects=False)
    assert first.status_code == 303
    assert first.headers["location"] == "/mirror?sound=1"      # the ticket is not kept in the address
    assert ms.COOKIE in first.cookies
    assert client.get("/mirror?sound=1").status_code == 200     # by cookie
    # spent: a copy of the command line is worth nothing
    other = TestClient(ms.app)
    assert other.get("/mirror?ticket=%s" % ticket, follow_redirects=False).status_code == 403
    assert other.get("/mirror?ticket=made-up", follow_redirects=False).status_code == 403


def test_a_ticket_dies_after_its_time(mirror, monkeypatch):
    ms, client = mirror
    ticket = ms.issue_ticket()
    ms._tickets[ticket] -= ms.TICKET_TTL_S + 1
    assert client.get("/mirror?ticket=%s" % ticket, follow_redirects=False).status_code == 403


def test_no_ticket_works_on_a_machine_with_no_mirror_token(mirror, monkeypatch):
    ms, client = mirror
    ticket = ms.issue_ticket()
    monkeypatch.setattr(settings, "mirror_token", "")
    assert client.get("/mirror?ticket=%s" % ticket, follow_redirects=False).status_code == 403


# ---- the robot side: the table ------------------------------------------------------------

def _table(tmp_path, monkeypatch, screens):
    path = tmp_path / "room-screens.json"
    path.write_text(json.dumps({"screens": screens}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "room_screens_file", str(path))
    room_screens.reset()
    return path


A = {"name": "ห้องขาย 2", "agent": "http://192.168.1.52:8777", "token": "k2",
     "near": {"x": 0.0, "y": 0.0, "radius": 2.0}}
B = {"name": "ห้องประชุม", "agent": "http://192.168.1.53:8777", "token": "k3",
     "near": {"x": 10.0, "y": 0.0, "radius": 2.0}}
FAR = {"name": "ไม่มีตำแหน่ง", "agent": "http://192.168.1.54:8777", "token": "k4"}


def test_the_table_skips_broken_entries_and_follows_the_file(tmp_path, monkeypatch):
    import os

    path = _table(tmp_path, monkeypatch, [A, {"name": "no key", "agent": "http://1.2.3.4:1"},
                                         {"name": "x", "agent": "ftp://1.2.3.4", "token": "t"}, dict(A)])
    assert room_screens.names() == ["ห้องขาย 2"]
    path.write_text(json.dumps({"screens": [A, FAR]}, ensure_ascii=False), encoding="utf-8")
    os.utime(path, ns=(10**18, 10**18))
    assert room_screens.names() == ["ห้องขาย 2", "ไม่มีตำแหน่ง"]      # no restart
    monkeypatch.setattr(settings, "room_screens_file", "")
    assert room_screens.screens() == []


def test_nothing_is_offered_without_every_piece(tmp_path, monkeypatch):
    _table(tmp_path, monkeypatch, [A])
    monkeypatch.setattr(settings, "mirror_port", 8002)
    monkeypatch.setattr(settings, "mirror_token", "t")
    assert room_screens.offered() is False                     # the switch
    monkeypatch.setattr(settings, "room_screens_enabled", True)
    assert room_screens.offered() is True
    monkeypatch.setattr(settings, "mirror_port", 0)
    assert room_screens.offered() is False                     # no page to show
    monkeypatch.setattr(settings, "mirror_port", 8002)
    monkeypatch.setattr(settings, "mirror_token", "")
    assert room_screens.offered() is False


def test_which_screen_is_never_a_guess(tmp_path, monkeypatch):
    _table(tmp_path, monkeypatch, [A, B, FAR])
    assert room_screens.pick("ห้องประชุม")["name"] == "ห้องประชุม"
    assert room_screens.pick("ห้องอื่น") is None
    assert room_screens.pick(None, {"x": 9.0, "y": 0.5})["name"] == "ห้องประชุม"
    assert room_screens.pick(None, {"x": 5.0, "y": 0.0}) is None       # between rooms: ask
    assert room_screens.pick(None, None) is None
    _table(tmp_path, monkeypatch, [FAR])
    assert room_screens.pick(None, None)["name"] == "ไม่มีตำแหน่ง"       # the only one


# ---- the robot side: near opens, leaving closes ---------------------------------------------

def test_coming_near_opens_after_two_readings_and_leaving_closes(tmp_path, monkeypatch):
    _table(tmp_path, monkeypatch, [A, B])
    at = lambda x: {"x": x, "y": 0.0, "yaw": 0.0}
    assert room_screens.decide(at(5.0)) is None
    assert room_screens.decide(at(1.5)) is None                 # one reading is not arriving
    assert room_screens.decide(at(1.0)) == ("open", "ห้องขาย 2")
    room_screens.STATE.update({"open": "ห้องขาย 2", "by": "near"})
    assert room_screens.decide(at(2.5)) is None                 # the edge band toggles nothing
    assert room_screens.decide(at(2.9)) is None
    assert room_screens.decide(at(3.5)) is None                 # beyond radius+margin: one reading
    assert room_screens.decide(at(4.0)) == ("close", "ห้องขาย 2")


def test_no_pose_decides_nothing_and_forgets_the_runs(tmp_path, monkeypatch):
    _table(tmp_path, monkeypatch, [A])
    assert room_screens.decide({"x": 0.0, "y": 0.0}) is None
    assert room_screens.decide(None) is None
    assert room_screens.decide({"x": 0.0, "y": 0.0}) is None    # starts again from one
    assert room_screens.decide({"x": float("nan"), "y": 0}) is None


def test_closed_by_hand_next_to_it_stays_closed_until_the_robot_leaves(tmp_path, monkeypatch):
    _table(tmp_path, monkeypatch, [A])
    monkeypatch.setattr(room_screens, "current_pose", lambda: {"x": 0.5, "y": 0.0})
    asked = []

    async def ask(screen, action, args, timeout=0):
        asked.append(action)
        return {"ok": True}

    monkeypatch.setattr(room_screens, "_ask", ask)
    asyncio.run(room_screens.close_screen())                 # "ปิดจอ"
    for _ in range(5):
        assert room_screens.decide({"x": 0.5, "y": 0.0}) is None
    for _ in range(2):
        room_screens.decide({"x": 9.0, "y": 0.0})            # it left
    room_screens.decide({"x": 0.5, "y": 0.0})
    assert room_screens.decide({"x": 0.5, "y": 0.0}) == ("open", "ห้องขาย 2")


def test_a_position_given_after_a_hand_close_still_opens_where_the_robot_stands(tmp_path, monkeypatch):
    """28 Oct on the robot: "ปิดจอ" while the screen had no position, then
    `here` with the robot standing at it -- nothing opened until the robot
    walked away and back."""
    _table(tmp_path, monkeypatch, [FAR])
    monkeypatch.setattr(room_screens, "current_pose", lambda: {"x": 0.5, "y": 0.0})

    async def ask(screen, action, args, timeout=0):
        return {"ok": True}

    monkeypatch.setattr(room_screens, "_ask", ask)
    path = Path(settings.room_screens_file)
    asyncio.run(room_screens.close_screen())
    # the file changes under a running server: no reset(), only a re-read
    path.write_text(json.dumps({"screens": [dict(FAR, near={"x": 0.0, "y": 0.0, "radius": 2.0})]},
                               ensure_ascii=False), encoding="utf-8")
    import os

    os.utime(path, ns=(10**18, 10**18))
    room_screens.decide({"x": 0.5, "y": 0.0})
    assert room_screens.decide({"x": 0.5, "y": 0.0}) == ("open", "ไม่มีตำแหน่ง")


def test_a_screen_opened_by_hand_is_not_swapped_and_closes_on_leaving_only_once_seen_near(tmp_path, monkeypatch):
    _table(tmp_path, monkeypatch, [A, B])
    room_screens.STATE.update({"open": "ห้องประชุม", "by": "asked", "seen_inside": False})
    for _ in range(3):
        assert room_screens.decide({"x": 0.0, "y": 0.0}) is None   # near A: B stays
    for _ in range(3):
        assert room_screens.decide({"x": 20.0, "y": 0.0}) is None  # never was near B
    room_screens.decide({"x": 10.0, "y": 0.0})                     # now it is
    assert room_screens.STATE["seen_inside"] is True
    room_screens.decide({"x": 20.0, "y": 0.0})
    assert room_screens.decide({"x": 20.0, "y": 0.0}) == ("close", "ห้องประชุม")


def test_a_pc_that_did_not_answer_is_not_asked_every_poll(tmp_path, monkeypatch):
    _table(tmp_path, monkeypatch, [A])
    room_screens._failed_at["ห้องขาย 2"] = 1000.0
    room_screens.decide({"x": 0, "y": 0}, now=1001.0)
    assert room_screens.decide({"x": 0, "y": 0}, now=1002.0) is None
    assert room_screens.decide({"x": 0, "y": 0}, now=1000.0 + room_screens.RETRY_AFTER_S) == ("open", "ห้องขาย 2")


# ---- the robot side: what is sent ---------------------------------------------------------

def test_opening_sends_the_face_page_with_a_ticket_never_the_token(tmp_path, monkeypatch):
    _table(tmp_path, monkeypatch, [A])
    monkeypatch.setattr(settings, "mirror_port", 8002)
    monkeypatch.setattr(settings, "mirror_token", "the-mirror-token")
    monkeypatch.setattr(room_screens, "_robot_ip_toward", lambda host: "192.168.1.197")
    monkeypatch.setattr(room_screens, "current_pose", lambda: {"x": 0.3, "y": 0.0})
    sent = []

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True, "opened": "emma_screen"}

    class Client:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json, headers):
            sent.append((url, json, headers))
            return Response()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    out = asyncio.run(room_screens.open_screen())
    assert out == {"ok": True, "screen": "ห้องขาย 2", "error": None}
    url, body, headers = sent[0]
    assert url == "http://192.168.1.52:8777/do" and headers == {"x-room-token": "k2"}
    assert body["action"] == "emma_screen_open"
    assert body["args"]["url"].startswith("http://192.168.1.197:8002/mirror?ticket=")
    assert "the-mirror-token" not in json.dumps(body)
    assert "sound" not in body["args"]["url"]                     # the robot speaks in that room
    assert room_screens.STATE == {"open": "ห้องขาย 2", "by": "asked", "seen_inside": True}
    asyncio.run(room_screens.close_screen())
    assert sent[1][1] == {"action": "emma_screen_close", "args": {}}
    assert room_screens.STATE["open"] is None


def test_a_pc_that_is_off_is_an_answer_not_a_crash(tmp_path, monkeypatch):
    _table(tmp_path, monkeypatch, [A])
    monkeypatch.setattr(settings, "mirror_port", 8002)
    monkeypatch.setattr(settings, "mirror_token", "t")
    monkeypatch.setattr(room_screens, "_robot_ip_toward", lambda host: "192.168.1.197")
    monkeypatch.setattr(room_screens, "current_pose", lambda: None)
    import httpx

    class Client:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            raise httpx.ConnectTimeout("no")

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    out = asyncio.run(room_screens.open_screen())
    assert out["ok"] is False and out["error"] == "unreachable"
    assert room_screens.STATE["open"] is None and "ห้องขาย 2" in room_screens._failed_at


def test_only_the_two_screen_actions_can_ever_leave_for_a_room_pc():
    """The general room control stays off Emma (owner 24 Sep); this wire is
    the screen only. Said in the code twice, held here."""
    with pytest.raises(ValueError):
        asyncio.run(room_screens._ask(A, "open_program", {"name": "cmd"}))
    for path in sorted((ROOT / "app").rglob("*.py")):
        if path.name in ("room_agent.py", "room_screens.py"):
            continue
        text = path.read_text(encoding="utf-8")
        assert "emma_screen_" not in text and "/do\"" not in text, path


def test_a_tunnelled_room_pc_still_fetches_the_page_over_the_lan(monkeypatch):
    """28 Oct on the robot: the command through `adb reverse` (agent on
    127.0.0.1) put the page on 127.0.0.1 too, i.e. through `adb forward` --
    up to 2.6 s behind the robot's voice where the LAN copy kept time."""
    import socket as real

    monkeypatch.setattr(settings, "mirror_host", "0.0.0.0")
    asked = []

    class Probe:
        def __init__(self, *a):
            pass

        def connect(self, addr):
            asked.append(addr[0])

        def getsockname(self):
            return ("192.168.1.197" if asked[-1] != "127.0.0.1" else "127.0.0.1", 5)

        def close(self):
            pass

    monkeypatch.setattr(real, "socket", Probe)
    assert room_screens._robot_ip_toward("127.0.0.1") == "192.168.1.197"
    assert asked == [room_screens._ROUTE_PROBE]
    asked.clear()
    assert room_screens._robot_ip_toward("192.168.1.52") == "192.168.1.197"
    assert asked == ["192.168.1.52"]                     # a LAN agent: its own route
    monkeypatch.setattr(settings, "mirror_host", "10.0.0.5")
    assert room_screens._robot_ip_toward("127.0.0.1") == "10.0.0.5"   # bound: that one


def test_toggle_decides_from_the_servers_own_state(tmp_path, monkeypatch):
    _table(tmp_path, monkeypatch, [A])
    monkeypatch.setattr(settings, "mirror_port", 8002)
    monkeypatch.setattr(settings, "mirror_token", "t")
    monkeypatch.setattr(room_screens, "_robot_ip_toward", lambda host: "192.168.1.197")
    monkeypatch.setattr(room_screens, "current_pose", lambda: None)
    sent = []

    async def ask(screen, action, args, timeout=0):
        sent.append(action)
        return {"ok": True}

    monkeypatch.setattr(room_screens, "_ask", ask)
    out = asyncio.run(room_screens.toggle())
    assert out["ok"] is True and out["open"] is True and sent == ["emma_screen_open"]
    room_screens.STATE.update({"open": None, "by": None})       # the robot walked away meanwhile
    out = asyncio.run(room_screens.toggle())
    assert out["open"] is True and sent[-1] == "emma_screen_open"   # up again, not "down"
    out = asyncio.run(room_screens.toggle())
    assert out["open"] is False and sent[-1] == "emma_screen_close"


def test_the_hold_menu_has_the_big_screen_beside_the_scan_and_the_navigation():
    """Owner 28 Oct: "ตัวฟังก์ชั่น big screen เอาไปไว้กับพวกแสกนหน้า กับนำทาง" --
    slot 3 of the hold menu, not a button in the call bar."""
    page = (ROOT / "client" / "voice-preview.html").read_text(encoding="utf-8")
    menu = page[page.index("var ACTIONS = ["):]
    menu = menu[:menu.index("];")]
    slots = [line.strip() for line in menu.splitlines()[1:] if line.strip()]
    assert slots[0].startswith("{ label: 'สแกนใบหน้า'") and slots[1].startswith("{ label: 'นำทาง'")
    assert slots[2] == "{ label: 'จอใหญ่', icon: SCREEN_ICON, run: toggleScreen, when: screenOffered },"
    # only where the server says a screen is set up; nothing in the call bar
    assert "post('status').then(function (j) { offered = !!(j && j.ok && j.offered); });" in page
    bar = page.split('<nav class="controls"', 1)[1].split("</nav>", 1)[0]
    assert "screen" not in bar.lower()
    # the token in the body, never the URL; the server decides open or closed
    assert "fetch('/room-screen', {" in page and "body: JSON.stringify(body)" in page
    assert "'/room-screen?" not in page
    # a press says how it went, after the menu has closed
    assert "said.then(function (text) { if (text) toast(text, document.body); });" in page


@pytest.fixture
def desk(tmp_path, monkeypatch):
    from app import main

    _table(tmp_path, monkeypatch, [A])
    monkeypatch.setattr(settings, "room_screens_enabled", True)
    monkeypatch.setattr(settings, "mirror_port", 8002)
    monkeypatch.setattr(settings, "mirror_token", "t")
    monkeypatch.setattr(settings, "ws_token", "desk")
    calls = []

    async def toggle(by="tap"):
        calls.append(by)
        return {"ok": True, "screen": "ห้องขาย 2", "error": None}

    monkeypatch.setattr(room_screens, "toggle", toggle)
    return TestClient(main.app), calls


def test_the_endpoint_takes_the_token_in_the_body_or_the_robots_own_page(desk):
    client, calls = desk
    assert client.post("/room-screen", json={"action": "toggle"}).status_code == 403
    assert client.post("/room-screen?token=desk", json={"action": "toggle"}).status_code == 403  # URL: no
    out = client.post("/room-screen", json={"action": "toggle", "token": "desk", "source": "face"}).json()
    assert out["ok"] is True and calls == ["tap"]
    client.post("/room-screen", json={"action": "toggle", "token": "desk"})
    assert calls == ["tap", "staff"]
    status = client.post("/room-screen", json={"action": "status", "token": "desk"}).json()
    assert status == {"open": False, "screen": None, "ok": True, "offered": True, "screens": ["ห้องขาย 2"]}
    assert client.post("/room-screen", json={"action": "reboot", "token": "desk"}).json()["ok"] is False


def test_a_robot_without_a_screen_offers_no_slot(desk, monkeypatch):
    client, calls = desk
    monkeypatch.setattr(settings, "room_screens_enabled", False)
    status = client.post("/room-screen", json={"action": "status", "token": "desk"}).json()
    assert status["offered"] is False and status["screens"] == []
    assert client.post("/room-screen", json={"action": "toggle", "token": "desk"}).json()["ok"] is False
    assert calls == []


# ---- the tool -----------------------------------------------------------------------------

def _tool():
    import importlib

    import app.tools.registry as reg
    import app.tools.screens as module

    entry = reg.get("room_screen")
    if entry is None or entry.handler is not module.room_screen:
        reg._REGISTRY.pop("room_screen", None)
        importlib.reload(module)
        entry = reg.get("room_screen")
    return entry


@pytest.fixture
def ready(tmp_path, monkeypatch):
    _table(tmp_path, monkeypatch, [A])
    monkeypatch.setattr(settings, "room_screens_enabled", True)
    monkeypatch.setattr(settings, "mirror_port", 8002)
    monkeypatch.setattr(settings, "mirror_token", "t")
    monkeypatch.setattr(settings, "tool_groups", "knowledge,screens")


def test_the_tool_is_the_robot_faces_and_only_where_everything_is_set(ready, monkeypatch):
    import app.tools.registry as reg

    entry = _tool()
    face = reg.robot_face_session.set(object())
    try:
        assert reg.allowed(entry) is True
        monkeypatch.setattr(settings, "tool_groups", "knowledge")
        assert reg.allowed(entry) is False                     # opt-in group
        monkeypatch.setattr(settings, "tool_groups", "")
        assert reg.allowed(entry) is False                     # blank = sales room default: not loaded
        monkeypatch.setattr(settings, "tool_groups", "knowledge,screens")
        monkeypatch.setattr(settings, "room_screens_enabled", False)
        assert reg.allowed(entry) is False
    finally:
        reg.robot_face_session.reset(face)
    monkeypatch.setattr(settings, "room_screens_enabled", True)
    assert reg.allowed(entry) is False                         # a desktop tab is not the robot


def test_the_tool_names_no_address_and_lists_screens_only_when_there_is_a_choice(ready, tmp_path, monkeypatch):
    from app.tools import screens as module

    description, params = module._declare()
    assert list(params["properties"]) == ["action"]
    _table(tmp_path, monkeypatch, [A, B])
    description, params = module._declare()
    assert params["properties"]["screen"]["enum"] == ["ห้องขาย 2", "ห้องประชุม"]
    assert "192.168" not in json.dumps(params) + description


def test_opening_needs_the_guests_own_word_for_a_screen(ready, monkeypatch):
    from app.tools import screens as module

    opened = []

    async def open_screen(name=None, by="asked"):
        opened.append(name)
        return {"ok": True, "screen": "ห้องขาย 2"}

    monkeypatch.setattr(room_screens, "open_screen", open_screen)
    session = object()
    heard.bind_session(session)
    # nothing heard: ask, do not open
    out = asyncio.run(module.room_screen("open"))
    assert out["reason"] == "not_heard" and opened == []
    # a yes to that question opens it
    heard.record("ใช่ค่ะ", session)
    assert asyncio.run(module.room_screen("open"))["ok"] is True and opened == [None]
    # the words themselves
    heard.mark_activity(session)
    heard.record("เปิดจอใหญ่ให้หน่อยค่ะ", session)
    assert asyncio.run(module.room_screen("open"))["ok"] is True
    for said in ("ไม่ต้องเปิดจอ", "ปิดจอ", "เอาลงจากจอ", "ราคาเท่าไหร่", "จอดรถที่ไหน"):
        heard.mark_activity(session)
        heard.record(said, session)
        assert asyncio.run(module.room_screen("open"))["ok"] is False, said
    # a bare yes with no question asked before it is not a request
    module._asked["at"] = None
    heard.mark_activity(session)
    heard.record("ใช่ค่ะ", session)
    assert asyncio.run(module.room_screen("open"))["ok"] is False


def test_closing_needs_no_words_it_is_the_way_back(ready, monkeypatch):
    from app.tools import screens as module

    async def close_screen(name=None, by="asked"):
        return {"ok": True, "screens": ["ห้องขาย 2"], "error": None}

    monkeypatch.setattr(room_screens, "close_screen", close_screen)
    out = asyncio.run(module.room_screen("close"))
    assert out["ok"] is True and "ห้ามบอก" not in out["instruction"]


def test_a_failed_open_says_so_and_never_that_it_is_up(ready, monkeypatch):
    from app.tools import screens as module

    async def open_screen(name=None, by="asked"):
        return {"ok": False, "screen": "ห้องขาย 2", "error": "unreachable"}

    monkeypatch.setattr(room_screens, "open_screen", open_screen)
    monkeypatch.setattr(module, "heard_open", lambda now=None: True)
    out = asyncio.run(module.room_screen("open"))
    assert out["ok"] is False and "ห้ามบอกว่าเปิดแล้ว" in out["instruction"]


# ---- shipping it ---------------------------------------------------------------------------

def test_the_table_of_pc_keys_never_ships_as_code_or_reaches_the_repo():
    import importlib.util
    import subprocess

    spec = importlib.util.spec_from_file_location("robot_server_t", ROOT / "scripts" / "robot_server.py")
    rs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rs)
    assert not rs._deployable("data/room-screens.json") and not rs._deployable("room-screens.json")
    assert rs.OPTIONAL_SECRET_FILES == {"data/room-screens.json": "room-screens.json"}
    for name in ("data/room-screens.json", "room-screens.json"):
        r = subprocess.run(["git", "check-ignore", "-q", name], cwd=ROOT, capture_output=True)
        assert r.returncode == 0, name
    env = (ROOT / "data" / "robot-server" / "robot.env").read_text(encoding="utf-8")
    assert "ROOM_SCREENS_FILE=room-screens.json" in env     # the root-owned copy, not data/


def test_the_keep_awake_tone_rides_the_screen_url_only_with_sound_and_only_a_quiet_one(monkeypatch):
    """Owner 16 Nov: the lobby's amplifier goes to standby when nothing plays ("พอไม่มีเสียงก็จะพัก
    เสียงไปเองเลย"). ROOM_SCREEN_KEEP_TONE "<hz>,<dB>" asks the screen page for one quiet tone --
    never without the screen's sound, and never a level a typo could make loud."""
    from urllib.parse import parse_qs, urlparse

    from app import mirror_server, room_screens
    from app.config import settings

    monkeypatch.setattr(mirror_server, "issue_ticket", lambda: "t")
    monkeypatch.setattr(room_screens, "_robot_ip_toward", lambda host: "10.0.0.2")
    screen = {"name": "lobby", "agent": "http://10.0.0.9:8777"}

    def query():
        return parse_qs(urlparse(room_screens.mirror_url(screen)).query)

    monkeypatch.setattr(settings, "room_screen_keep_tone", "25,-40")
    monkeypatch.setattr(settings, "room_screen_sound", False)
    assert "keep" not in query() and "sound" not in query()
    monkeypatch.setattr(settings, "room_screen_sound", True)
    assert query()["keep"] == ["25,-40"] and query()["sound"] == ["1"]
    for bad in ("", "25", "25,-10", "25,0", "5,-40", "30000,-40", "x,y", "25,-40,1", "nan,-40", "25,-inf"):
        monkeypatch.setattr(settings, "room_screen_keep_tone", bad)
        assert "keep" not in query(), bad
    # what goes out is what the page's own reader takes: plain decimals, the bounds themselves
    for raw, sent in (("19500,-36.5", "19500,-36.5"), ("22000,-20", "22000,-20"), ("10,-80", "10,-80"),
                      ("1e4,-40", "10000,-40"), (" 25 , -40 ", "25,-40")):
        monkeypatch.setattr(settings, "room_screen_keep_tone", raw)
        assert query()["keep"] == [sent], raw


def _mirror_keep(cases: dict) -> dict:
    """The page's keep-awake tone code, cut out of voice-preview.html and run in node against a
    fake AudioContext (tests/client_mirror_keep.cjs)."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required: NOT checked here -- the room-screen page's own bounds on the "
                    "keep-awake tone (never loud, never without sound=1) and its one-tone guard")
    result = subprocess.run([node, "tests/client_mirror_keep.cjs", json.dumps(cases)], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_the_screen_page_plays_one_quiet_tone_only_with_sound_and_refuses_a_loud_one():
    """The page reads ?keep= itself (a URL is typed by hand too): only on the room screen with
    sound=1, only 10-22000 Hz at -80..-20 dB, plain decimals. One context and one oscillator
    however often a call starts (startMirrorAudio calls it every call)."""
    got = _mirror_keep({
        "quiet": {"query": "sound=1&keep=25,-40", "calls": 3},
        "high": {"query": "sound=1&keep=19500,-36.5"},
        "edge_loud": {"query": "sound=1&keep=22000,-20"},
        "edge_soft": {"query": "sound=1&keep=10,-80"},
        "no_sound": {"query": "keep=25,-40"},
        "sound_0": {"query": "sound=0&keep=25,-40"},
        "not_the_screen": {"query": "sound=1&keep=25,-40", "mirror": False},
        "loud": {"query": "sound=1&keep=1000,-5"},
        "just_too_loud": {"query": "sound=1&keep=1000,-19.9"},
        "exponent": {"query": "sound=1&keep=1e3,-40"},
        "plus_db": {"query": "sound=1&keep=25,40"},
        "too_low": {"query": "sound=1&keep=5,-40"},
        "too_high": {"query": "sound=1&keep=30000,-40"},
        "three": {"query": "sound=1&keep=25,-40,1"},
    })
    quiet = got["quiet"]
    assert quiet["keep"] == {"hz": 25, "gain": pytest.approx(0.01)}
    assert quiet["contexts"] == 1 and quiet["sounding"] == 1, quiet           # three calls, one tone
    assert quiet["gains"] == [[pytest.approx(0.01)]] and quiet["hz"] == [[25]]
    assert got["high"]["hz"] == [[19500]] and got["high"]["gains"][0][0] == pytest.approx(10 ** (-36.5 / 20))
    assert got["edge_loud"]["gains"] == [[pytest.approx(0.1)]]                # the loudest there is
    assert got["edge_soft"]["sounding"] == 1
    for name in ("no_sound", "sound_0", "not_the_screen", "loud", "just_too_loud", "exponent", "plus_db",
                 "too_low", "too_high", "three"):
        assert got[name]["keep"] is None and got[name]["contexts"] == 0, (name, got[name])
    for case in got.values():
        assert all(g <= 0.1 + 1e-9 for row in case.get("gains", []) for g in row), case


def test_the_tone_never_stacks_and_survives_a_browser_that_holds_audio_back():
    """Review 16 Nov: a throw after the oscillator started left that context sounding with the
    guard cleared -- the next call put a second tone on top. Now what was opened is closed. And a
    context the browser has not let start is resumed on a tap, whatever resume() returns."""
    got = _mirror_keep({
        "start_throws_once": {"query": "sound=1&keep=25,-40", "startThrows": True, "calls": 3},
        "held_back": {"query": "sound=1&keep=25,-40", "suspended": True},
        "old_resume": {"query": "sound=1&keep=25,-40", "suspended": True, "resumeNoPromise": True, "calls": 3},
    })
    first = got["start_throws_once"]
    assert first["contexts"] == 2 and first["open"] == 1 and first["sounding"] == 1, first
    assert got["held_back"]["listeners"] == ["pointerdown"] and got["held_back"]["sounding"] == 1
    old = got["old_resume"]
    assert "threw" not in old and old["contexts"] == 1 and old["sounding"] == 1 and old["guard"], old


def test_the_tone_starts_with_the_page_not_with_her_first_call():
    """The amplifier sleeps between calls too: the boot line starts the tone (a source pin -- the
    boot block needs the whole page to run; test_face_mirror pins the same line)."""
    src = (ROOT / "client" / "voice-preview.html").read_text(encoding="utf-8")
    assert "startMirror(); startMirrorKeep(); return; }" in src
    audio = src.split("function startMirrorAudio() {", 1)[1][:80]
    assert "startMirrorKeep();" in audio


def test_the_room_echo_tail_exists_only_while_a_screen_is_up_with_its_sound(monkeypatch):
    """Owner 17 Nov: the host wears the mic, her voice comes out of the room's speakers. The
    tail after her voice is for THAT room copy: no screen up, or a silent screen, = no guard
    (every other machine as it was), and a typo cannot make her deaf for a minute."""
    from app import room_screens
    from app.config import settings

    monkeypatch.setattr(settings, "room_screens_enabled", True)
    monkeypatch.setattr(settings, "room_screen_sound", True)
    monkeypatch.setattr(settings, "room_screen_echo_tail_s", 0.8)
    assert room_screens.echo_tail_s() == 0.0                       # nothing open
    monkeypatch.setitem(room_screens.STATE, "open", "lobby")
    assert room_screens.echo_tail_s() == 0.8
    monkeypatch.setattr(settings, "room_screen_sound", False)
    assert room_screens.echo_tail_s() == 0.0
    monkeypatch.setattr(settings, "room_screen_sound", True)
    monkeypatch.setattr(settings, "room_screens_enabled", False)
    assert room_screens.echo_tail_s() == 0.0
    monkeypatch.setattr(settings, "room_screens_enabled", True)
    monkeypatch.setattr(settings, "room_screen_echo_tail_s", 60.0)
    assert room_screens.echo_tail_s() == 3.0
    monkeypatch.setattr(settings, "room_screen_echo_tail_s", -1.0)
    assert room_screens.echo_tail_s() == 0.0


def _echo_rig(monkeypatch, gate=True):
    import struct
    import types

    from app import room_screens, session, turnlog
    from app.config import settings

    monkeypatch.setattr(settings, "room_screens_enabled", True)
    monkeypatch.setattr(settings, "room_screen_sound", True)
    monkeypatch.setattr(settings, "room_screen_echo_tail_s", 0.8)
    monkeypatch.setitem(room_screens.STATE, "open", "lobby")
    now = [100.0]
    monkeypatch.setattr(session.time, "monotonic", lambda: now[0])
    rows = []
    monkeypatch.setattr(turnlog, "record", lambda event, **kw: rows.append((event, kw)))
    me = types.SimpleNamespace(_play_until=0.0, provider=types.SimpleNamespace(_vad_gate=object() if gate else None),
                               ROOM_ECHO_WATCH_S=session.VoiceSession.ROOM_ECHO_WATCH_S)
    me._room_echo_row = lambda held: session.VoiceSession._room_echo_row(me, held)
    guard = lambda frame: session.VoiceSession._room_echo_guard(me, frame)   # noqa: E731
    frame = lambda amp: struct.pack("<320h", *([amp, -amp] * 160))           # noqa: E731
    return me, guard, frame, now, rows


def test_mic_frames_go_up_as_silence_until_the_rooms_copy_of_her_voice_is_over(monkeypatch):
    """While she is audible by the server's clock and for the tail after, the frame is zeros of
    the same length (the VAD gate still gets a frame); after it, the frame as it came. One row
    of numbers a stretch: the loudest it kept out, the loudest inside the tail, and the
    loudest in the 0.6 s AFTER the tail (the number the tail is set from)."""
    me, guard, frame, now, rows = _echo_rig(monkeypatch)
    loud, mid, soft = frame(8000), frame(4000), frame(1000)
    assert guard(loud) is loud                                       # she has not spoken on this call
    me._play_until = 101.0
    assert guard(loud) == bytes(len(loud))                           # her voice is playing
    now[0] = 101.5
    assert guard(mid) == bytes(len(mid))                             # the room's copy, inside the tail
    now[0] = 101.81
    assert guard(soft) is soft and rows == []                        # past the tail: heard, still watched
    now[0] = 102.3
    assert guard(soft) is soft and rows == []
    now[0] = 102.45
    assert guard(loud) is loud                                       # the watch is over: not counted
    assert rows == [("room_echo_guard", {"frames": 2, "peak": 0.2441, "after": 0.1221, "post": 0.0305, "tail_s": 0.8})]
    assert guard(loud) is loud and len(rows) == 1                    # one row a stretch


def test_the_room_echo_guard_follows_her_voice_being_cut_and_the_screen_coming_down(monkeypatch):
    """An interruption cuts `_play_until` back to now: the guard ends the tail after THAT, not
    after the reply's old end. The screen taken down mid-stretch ends it at once and the row
    still says which tail it was. With the setting at 0 the frame object itself comes back."""
    from app import room_screens
    from app.config import settings

    me, guard, frame, now, rows = _echo_rig(monkeypatch)
    loud = frame(8000)
    me._play_until = 130.0                                           # a long answer queued
    assert guard(loud) == bytes(len(loud))
    now[0], me._play_until = 105.0, 105.0                            # barged in: cut back
    assert guard(loud) == bytes(len(loud))
    now[0] = 105.9
    assert guard(loud) is loud                                       # 0.8 s after the cut, not at 130.8
    me._play_until = 200.0
    now[0] = 107.0                                                   # (the watch of the first stretch is over)
    guard(loud)
    rows.clear()
    assert guard(loud) == bytes(len(loud))                           # a new reply: guarded again
    monkeypatch.setitem(room_screens.STATE, "open", None)            # staff took the screen down
    assert guard(loud) is loud
    now[0] = 108.0
    assert guard(loud) is loud
    assert rows and rows[-1][1]["tail_s"] == 0.8
    monkeypatch.setitem(room_screens.STATE, "open", "lobby")
    monkeypatch.setattr(settings, "room_screen_echo_tail_s", 0.0)
    assert guard(loud) is loud


def test_with_server_side_detection_the_guarded_frame_is_not_sent_at_all(monkeypatch):
    """gemini.send_audio's own rule for a deaf mic without the local gate: silence sent upstream
    is still a stream to judge (review 17 Nov) -- nothing goes."""
    me, guard, frame, now, rows = _echo_rig(monkeypatch, gate=False)
    me._play_until = 101.0
    assert guard(frame(8000)) is None
    now[0] = 102.0
    f = frame(8000)
    assert guard(f) is f


def test_the_room_echo_guard_sits_on_the_mic_path_before_the_name_ears_and_the_provider():
    """After the mic report (it judges the real frame), before the name ears (her own "Emma" out
    of the room's speakers is not a call -- review 17 Nov) and the provider; a None is not sent."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "app" / "session.py").read_text(encoding="utf-8")
    pump = src.split("async def _browser_to_provider(self)", 1)[1].split("if (text := message.get(\"text\"))", 1)[0]
    order = [pump.index(part) for part in ("self._mic_report(data)", "data = self._room_echo_guard(data)",
                                           "if data is None:\n                        continue",
                                           "self._hear_name(data)", "await self.provider.send_audio(data)")]
    assert order == sorted(order)
