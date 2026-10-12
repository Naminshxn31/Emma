"""The robot face page's body/nav bridge and its navigation panel
(docs/emma-body-nav-design-2026-10-08.md §2a, §2b, §3; package P5).

Nothing here has run on the robot (it was powered off). The behaviour tests run
the page's REAL script, extracted from client/voice-preview.html, through
tests/face_nav_model.cjs: a fake DOM, a fake app bridge (window.prompt), a fake
/robot-nav server and a virtual clock. The pure rules (navModel, the relay,
the message builders, the 1 s hold) sit between `// <nav-model>` markers in
the page so the test runs the page's own copy, not a second one.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import robot_contracts as rc


ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "client" / "voice-preview.html"
HARNESS = ROOT / "tests" / "face_nav_model.cjs"
RUN = "ab" * 16


def _html() -> str:
    return PAGE.read_text(encoding="utf-8")


def _without_comments(code: str) -> str:
    code = re.sub(r"<!--.*?-->", "", code, flags=re.S)
    return "\n".join(line for line in code.splitlines()
                     if not line.lstrip().startswith(("//", "*", "/*")))


def _bridge_block() -> str:
    html = _html()
    return html.split("// ---- robot bridge + navigation:", 1)[1].split("</script>", 1)[0]


def _nav_markup() -> str:
    html = _html()
    return html.split('<div class="emma-nav" id="emmaNav"', 1)[1].split("<!-- The 3D mascot", 1)[0]


def _css() -> str:
    return _html().split("<style>", 1)[1].split("</style>", 1)[0]


def _css_block(selector: str) -> str:
    return _css().split("\n  " + selector + " {", 1)[1].split("}", 1)[0]


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required: navModel not executed — disabled/phase mapping unchecked, and the "
                    "relay, ready/face_hello, hold-to-start, PIN and heartbeat scenarios are NOT being "
                    "checked on this machine (only the source anchors are)")
    return node


def _run(args, stdin=None):
    # encoding, not text=True: Thai comes back through stdout, and Windows'
    # default codepage (cp874) cannot decode node's UTF-8.
    result = subprocess.run([_node(), str(HARNESS), str(PAGE), *args], input=stdin,
                            capture_output=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _model(*cases):
    return _run(["model"], json.dumps(list(cases), ensure_ascii=False))


def _scenario(name):
    return _run(["scenario", name])


# ---- the copies of app/robot_contracts.py --------------------------------

def test_the_pages_constants_are_the_contracts_own():
    [c] = _model({"fn": "constants"})
    assert c["NAV_REASONS_TH"] == rc.NAV_REASONS
    assert tuple(c["BODY_IDS"]) == rc.BODY_ACTION_IDS
    assert tuple(c["BODY_STATUSES"]) == rc.BODY_STATUSES
    assert tuple(c["NAV_STATES"]) == rc.NAV_STATES
    assert tuple(c["BODY_STATUS_STATES"]) == rc.BODY_STATUS_STATES
    assert c["ID_RE"] == rc.ID_RE and c["COMMAND_ID_RE"] == rc.COMMAND_ID_RE
    assert c["MAX_INBOUND_BYTES"] == rc.MAX_INBOUND_BYTES
    assert c["START_HOLD_MS"] == 1000


def test_every_nav_reason_text_is_in_the_page_even_without_node():
    block = _bridge_block()
    for code, text in rc.NAV_REASONS.items():
        assert f"{code}: '{text}'" in block, code


# ---- slot 2 ----------------------------------------------------------------

def test_slot_two_is_navigation_only_in_the_app_and_only_when_the_mode_is_not_off():
    html = _html()
    menu = html.split("var ACTIONS = [", 1)[1].split("];", 1)[0]
    assert "{ label: 'นำทาง', icon: NAV_ICON, run: openNav, when: navOffered }" in menu
    hold = html.split("// ---- press-and-hold action buttons around Emma", 1)[1].split("</script>", 1)[0]
    # `when` is asked every time the menu is drawn AND when a slot is pressed.
    assert ("return typeof action.run !== 'function' || (typeof action.when === 'function' && !action.when());"
            in hold)
    assert "var empty = isEmpty(action);" in hold and "if (!isEmpty(action)) {" in hold
    # The overlay script runs first, as the face scan's does.
    assert html.index("window.EmmaNav = Object.freeze(") < html.index("var ACTIONS = [")
    code = _without_comments(_bridge_block())
    assert "function offered() { return ON_ROBOT && mode !== 'off'; }" in code
    assert "var ON_ROBOT = IN_APP && /EmmaRobot\\//.test(UA) && /EmmaBridge\\/1\\b/.test(UA);" in code
    assert "var IN_APP = query.get('app') === '1';" in code
    # The mode is the server's answer; no answer is off.
    load = code.split("function loadConfig() {", 1)[1].split("\n    }\n", 1)[0]
    assert "mode = has(MODES, j && j.mode) ? j.mode : 'off';" in load
    assert "mode = 'off';" in load.split("}, function () {", 1)[1]

    run = _scenario("not_robot")
    assert not run["desk"]["offered"] and not run["old_apk"]["offered"] and not run["ua_only"]["offered"]
    pin = _scenario("pin")
    assert pin["offeredBefore"] is False          # before the server has answered
    assert pin["offeredAfter"] is True            # mode "pin", inside the app


def test_the_overlay_is_a_sibling_of_the_face_scan_outside_main():
    html = _html()
    assert html.index('id="emmaNav"') > html.index("</main>")
    assert html.index('id="emmaNav"') > html.index('id="emmaScan"')
    assert html.index('id="emmaNav"') < html.index('<script type="importmap">')
    assert _css_block(".emma-nav").count("z-index: 60;") == 1
    code = _without_comments(_bridge_block())
    # Escape and Tab are caught on the document's capture phase, like the scan.
    assert "document.addEventListener('keydown', function (e) {\n      if (!open) return;" in code
    assert "e.stopImmediatePropagation();" in code


# ---- the closed bridge grammar ---------------------------------------------

def test_only_the_exact_prompt_strings_of_the_design_reach_window_prompt():
    code = _without_comments(_html())
    calls = re.findall(r"window\.prompt\(([^)]*)\)", code)
    # The legacy greeting (P4's frozen string) and the bridge's one call site.
    assert sorted(calls) == sorted(["'emma-arm:greet:6', ''", "message, ''"])
    block = _without_comments(_bridge_block())
    ask = block.split("function ask(message) {", 1)[1].split("\n    }\n", 1)[0]
    assert "if (!ON_ROBOT || !allowedMessage(message)) return null;" in ask
    # Every caller passes a fixed string or a builder's output.
    args = re.findall(r"\bask\(([^()]*(?:\([^()]*\))?)\)", block)
    # + the auto-return's fixed reads and reports (owner 10 Oct; AutoReturn.java)
    fixed = {"'emma-body:list'", "'emma-body:status'", "'emma-nav:places'", "'emma-nav:status'",
             "'emma-face:token'", "message", "navGoMessage(place.id, 'tap')",
             "'emma-nav:auto'", "'emma-nav:auto:shown'", "'emma-nav:auto:stay'", "activityMessage(kind)"}
    assert set(args) <= fixed, set(args) - fixed

    allowed = [m for m in ("emma-body:list", "emma-body:status", "emma-body:stop", "emma-body:nod",
                           "emma-body:handshake", "emma-nav:places", "emma-nav:status", "emma-nav:stop",
                           "emma-nav:go:big_room:tap", "emma-nav:go:dock:voice", "emma-face:token",
                           "emma-nav:auto", "emma-nav:auto:shown", "emma-nav:auto:stay",
                           "emma-nav:activity:call_start", "emma-nav:activity:call_end",
                           "emma-nav:activity:speech")]
    # the page can never start the app's own walk: no go with origin auto
    refused = ["emma-arm:greet:6", "emma-body:dance", "emma-body:Nod", "emma-body:nod\n",
               "emma-body:nod:x", "emma-nav:go:big_room:pc", "emma-nav:go:big_room", "emma-nav:go:../x:tap",
               "emma-nav:go:ห้อง:tap", "emma-nav:go:big_room:tap\n", "emma-nav:reap", "emma-face:secret", "", None,
               "emma-nav:go:dock:auto", "emma-nav:auto:go", "emma-nav:activity:touch", "emma-nav:activity:",
               "emma-nav:auto:stay\n"]
    out = _model(*[{"fn": "allowedMessage", "args": [m]} for m in allowed + refused])
    assert out[:len(allowed)] == [True] * len(allowed)
    assert out[len(allowed):] == [False] * len(refused)


def test_ids_pass_through_only_after_id_re():
    good = _model({"fn": "navGoMessage", "args": ["big_room", "tap"]},
                  {"fn": "navGoMessage", "args": ["dock", "voice"]},
                  {"fn": "bodyMessage", "args": ["raise_left_arm"]},
                  {"fn": "bodyMessage", "args": ["stop"]})
    assert good == ["emma-nav:go:big_room:tap", "emma-nav:go:dock:voice", "emma-body:raise_left_arm",
                    "emma-body:stop"]
    bad = _model(*[{"fn": "navGoMessage", "args": [p, "tap"]}
                   for p in ("../etc", "Big_room", "b", "big_room\n", "ห้องประชุม", "a" * 33, "1room", None)],
                 {"fn": "navGoMessage", "args": ["big_room", "pc"]},
                 *[{"fn": "bodyMessage", "args": [a]} for a in ("dance", "list", "status", "nod\n", None)])
    assert bad == [None] * len(bad)

    run = _scenario("relay")
    # Exactly these strings, in this order: two body commands, one voice go,
    # one stop -- and the status pulls after an accepted go. A server `go`
    # is always ":voice", whatever origin it claims; bad ids never prompt.
    assert run["prompts"][:4] == ["emma-body:nod", "emma-body:stop", "emma-nav:go:big_room:voice", "emma-nav:stop"]
    # + the auto-return state read at load (a read; with the switch off the app answers "off")
    assert set(run["prompts"][4:]) <= {"emma-nav:status", "emma-nav:places", "emma-nav:auto"}
    acks = run["acks"]
    assert len(acks) == 8                                   # the 3-letter command id got no answer
    assert acks[0] == {"type": "body_ack", "command_id": "cd" * 16, "action": "nod", "accepted": True,
                       "reason": None, "status": "direction_known", "est_ms": 4000}
    assert acks[1]["action"] == "stop" and acks[1]["accepted"] is True
    assert [a["reason"] for a in acks[2:4]] == ["bad_request", "bad_request"]
    assert acks[2]["action"] is None                        # garbage is never echoed
    assert acks[4] == {"type": "nav_ack", "command_id": "cd" * 16, "action": "go", "accepted": True,
                       "reason": None, "run_id": RUN}
    assert [a["reason"] for a in acks[5:7]] == ["bad_request", "bad_request"]
    assert acks[7]["action"] == "stop" and acks[7]["accepted"] is True


def test_no_transcript_or_user_text_ever_goes_to_a_prompt():
    code = _without_comments(_bridge_block())
    for word in ("curUser", "transcript", "userLine", "evt.text", "replyNode", "data-emma-reply"):
        assert word not in code, word
    relay = code.split("function relayCommand(evt, ask, onRobot) {", 1)[1].split("\n    }\n", 1)[0]
    # The relay reads the command's id, action and place -- nothing else.
    fields = set(re.findall(r"evt\.(\w+)", relay))
    assert fields == {"type", "command_id", "action", "place"}
    # The call script hands the bridge the server's command object only.
    html = _without_comments(_html())
    assert "window.EmmaRobotBridge.command(evt, robotSender(ws));" in html
    # A command with a free-text field changes nothing that is asked.
    [out] = _model({"fn": "relayCommand", "args": [
        {"type": "nav_command", "command_id": "cd" * 16, "action": "go", "place": "big_room",
         "text": "พาไปห้องประชุมใหญ่", "origin": "voice"}, {}, True]})
    assert out["asked"] == ["emma-nav:go:big_room:voice"]


def test_a_page_that_is_not_the_robot_face_answers_not_robot_face_and_asks_nothing():
    run = _scenario("not_robot")
    for name in ("desk", "old_apk", "ua_only"):
        assert run[name]["prompts"] == [], name
        assert run[name]["sent"] == [{"type": "body_ack", "command_id": "cd" * 16, "action": "nod",
                                      "accepted": False, "reason": "not_robot_face", "status": None,
                                      "est_ms": None}], name


def test_frames_to_the_server_carry_whitelisted_fields_and_fit_the_limit():
    caps, status, big = _model(
        {"fn": "navCapsFrame", "args": [{"enabled": True, "installed": True, "here": "dock", "battery": 80,
                                         "places_sha256": "f" * 64, "secret": 1,
                                         "places": [{"id": "big_room", "name_th": "ห้อง", "available": True,
                                                     "verified": False, "doors": ["D8"], "x": 1},
                                                    {"id": "../bad", "name_th": "x"}], "run": None}]},
        {"fn": "navStatusFrame", "args": [{"state": "running", "run_id": RUN, "launcher_pid": 4711,
                                           "place": "big_room", "step": 4, "evil": "<b>"}]},
        {"fn": "frameFits", "args": [{"type": "nav_caps", "places": [{"name_th": "ห" * 3000}]}]})
    assert set(caps) == {"type", "enabled", "installed", "places_sha256", "here", "battery", "places",
                         "reason", "run"}
    assert caps["reason"] is None
    assert caps["places"] == [{"id": "big_room", "name_th": "ห้อง", "available": True, "reason": None,
                               "verified": False}]
    assert "evil" not in status and "launcher_pid" not in status and status["type"] == "nav_status"
    assert big is False          # 9000 UTF-8 bytes > MAX_INBOUND_BYTES: not sent


# ---- ready, status, caps ---------------------------------------------------

def test_ready_sends_face_hello_first_and_only_with_a_token_then_the_caps():
    run = _scenario("ready")
    assert run["s3cret"]["types"][0] == "face_hello"
    assert run["s3cret"]["first"] == {"type": "face_hello", "token": "s3cret"}
    assert set(run["s3cret"]["types"][1:]) == {"body_caps", "nav_caps", "nav_status"}
    assert "face_hello" not in run["none"]["types"]
    # Unknown gestures never reach the server.
    assert run["s3cret"]["body"]["gestures"] == {"nod": "direction_known", "handshake": "verified"}
    code = _without_comments(_html())
    # The token comes from the app, never from the URL.
    assert "query.get('face_token')" not in code and "q.get('face_token')" not in code
    ready = code.split("case 'ready':", 1)[1].split("break;", 1)[0]
    assert "window.EmmaRobotBridge.ready(robotSender(ws));" in ready


def test_body_status_goes_once_per_new_seq():
    run = _scenario("body_status")
    assert run["statuses"] == [4, 5]
    assert run["caps"] == 0          # unchanged caps are not resent


def test_nav_caps_go_at_most_every_30_s_while_idle():
    run = _scenario("caps_rate")
    assert run["atReady"] == 1
    assert run["reads"] > 20                 # the open panel read them every second...
    assert run["after20"] == 0               # ...and none of those reads became a frame
    assert run["after31"] == 1


def test_native_change_events_are_the_ones_the_app_sends():
    code = _without_comments(_bridge_block())
    assert "addEventListener('emma-body-changed'," in code
    assert "addEventListener('emma-nav-changed'," in code


# ---- the panel -------------------------------------------------------------

DESIGN_TEXTS = [
    "นำทางหุ่น", "สำหรับพนักงาน — ใส่รหัส PIN เพื่อสั่งหุ่นเดิน", "รหัสไม่ถูกต้อง", "ใส่ผิดหลายครั้ง รอ 5 นาที",
    "ยกเลิก", "ตกลง", "หุ่นอยู่ที่: ", "ไม่ทราบ", " · แบต ", "ยังไม่เคยวิ่งช่วงนี้แยก — เดินตามดูด้วย",
    "ผ่านประตู ", " และ ", "ให้หุ่นเดินไป ", " ใช่ไหม?", "หุ่นจะเก็บแขนลงก่อนออกเดิน",
    " — ดูให้ประตูเปิดและไม่มีคนยืนขวาง", "เดินตามดูได้ กดปุ่มหยุดฉุกเฉิน (E-stop) บนตัวหุ่นได้ทุกเมื่อ",
    "กดค้าง 1 วิ เพื่อเริ่มเดิน", "กำลังไป", "กำลังเก็บแขน…", "กำลังตรวจก่อนออกเดิน…", "ตรวจตำแหน่ง",
    "กำลังเดิน", "กำลังถอยเข้าแท่นชาร์จ", "ขั้น ", " จาก ", "เวลา ", "ถึง ", " แล้ว", "หุ่นหยุดแล้ว — ",
    "หุ่นหยุดอยู่ที่ขั้น ", " — ให้เจ้าหน้าที่ดูแล", "ยังไปไม่ได้ — ", "ปิด",
    "แบตเตอรี่เหลือ ", "% — ต้องมีอย่างน้อย ",
]


def test_every_thai_string_of_the_ui_design_is_present():
    page = _html()
    for text in DESIGN_TEXTS:
        assert text in page, text


def test_the_panel_reserves_the_safety_bars_14vh_and_has_no_stop_button_of_its_own():
    def declarations(selector):
        return re.sub(r"/\*.*?\*/", "", _css_block(selector), flags=re.S)
    assert "padding: max(16px, env(safe-area-inset-top)) 16px calc(14vh + 12px);" in declarations(".emma-nav")
    assert "bottom: 14vh;" in declarations(".emma-nav-sheet")
    markup = _nav_markup()
    buttons = re.findall(r"<button[^>]*>(.*?)</button>", markup, flags=re.S)
    assert buttons and not any("หยุด" in b for b in buttons)
    code = _without_comments(_bridge_block())
    # The page asks the app to stop ONLY when the server relays a stop; no
    # control of this panel does.
    stop_sites = [m.start() for m in re.finditer(r"'emma-nav:stop'", code)]
    relay = code.index("function relayCommand(")
    relay_end = code.index("function bodyCapsFrame(")
    fixed = code.index("var FIXED_MESSAGES")
    assert all(s == code.index("'emma-nav:stop'", fixed) or relay < s < relay_end for s in stop_sites)
    assert len(stop_sites) == 2
    assert "emma-body:stop" not in _without_comments(_bridge_block().split("// </nav-model>", 1)[1])


def test_the_hold_to_start_is_1000_ms_and_an_early_release_does_nothing():
    code = _without_comments(_bridge_block())
    assert "var START_HOLD_MS = 1000;" in code
    assert "var hold = holdController(START_HOLD_MS," in code
    for t in ("'pointerup', 'pointerleave', 'pointercancel'",):
        assert t in code
    early, exact, cancelled, twice = _model(
        {"fn": "hold", "args": [["start", 0], ["wait", 999], ["cancel", 0], ["wait", 5000]]},
        {"fn": "hold", "args": [["start", 0], ["wait", 1000]]},
        {"fn": "hold", "args": [["start", 0], ["wait", 500], ["cancel", 0], ["start", 0], ["wait", 999]]},
        {"fn": "hold", "args": [["start", 0], ["start", 0], ["wait", 1000], ["wait", 5000]]})
    assert early == {"fired": 0} and exact == {"fired": 1}
    assert cancelled == {"fired": 0} and twice == {"fired": 1}

    run = _scenario("hold")
    assert run["placesView"] == ["places"]
    assert run["dockSheet"] is False                     # a disabled card opens nothing
    assert run["sheet"]["title"] == "ให้หุ่นเดินไป ห้องประชุมใหญ่ ใช่ไหม?"
    assert run["sheet"]["items"] == ["หุ่นจะเก็บแขนลงก่อนออกเดิน",
                                     "ทางผ่านประตู D8 และ D11 — ดูให้ประตูเปิดและไม่มีคนยืนขวาง",
                                     "เดินตามดูได้ กดปุ่มหยุดฉุกเฉิน (E-stop) บนตัวหุ่นได้ทุกเมื่อ"]
    assert run["sheet"]["badge"] == "⚠ ยังไม่เคยวิ่งช่วงนี้แยก — เดินตามดูด้วย"
    assert run["early"] == 0 and run["slid"] == 0
    assert run["goes"] == ["emma-nav:go:big_room:tap"]
    assert run["after"] == ["progress"] and run["kicker"] == "กำลังไป" and run["phase"] == "กำลังเก็บแขน…"


def test_the_pin_and_the_ws_token_go_in_the_body_never_in_a_url():
    run = _scenario("pin")
    assert run["first"] == ["pin"]
    assert run["wrong"] == {"error": "รหัสไม่ถูกต้อง", "view": ["pin"], "pinLeft": ""}
    assert run["right"]["view"] == ["places"]
    assert run["right"]["where"] == "หุ่นอยู่ที่: แท่นชาร์จ · แบต 82%"
    assert run["expired"] == ["pin"]                     # the 120 s unlock ran out
    assert run["fetches"]
    for f in run["fetches"]:
        assert f["method"] == "POST"
        assert f["url"] in ("/robot-nav/config", "/robot-nav/unlock")
        assert "token" not in f["url"] and "?" not in f["url"]
        assert f["body"]["token"] == "tok123"
    assert [f["body"]["pin"] for f in run["fetches"] if f["url"] == "/robot-nav/unlock"] == ["0000", "4321"]
    code = _without_comments(_bridge_block())
    assert "if (token) body.token = token;" in code


def test_a_walk_cannot_be_closed_by_a_stray_tap_and_the_heartbeat_keeps_going():
    run = _scenario("heartbeat")
    assert run["opened"] == {"view": ["progress"], "phase": "ผ่านประตู D8", "step": "ขั้น 4 จาก 10",
                             "going": "ห้องประชุมใหญ่", "x": False}
    # 16 s of an unchanged status, the panel reading it every second: the 5 s
    # heartbeat still went (a timer re-armed by every read would never fire).
    assert run["beats"] >= 3
    assert run["stillOpen"] is True
    assert run["after"] == {"view": ["result"], "result": "✓ ถึง ห้องประชุมใหญ่ แล้ว", "x": True}


# ---- navModel --------------------------------------------------------------

CAPS = {"enabled": True, "installed": True, "here": "dock", "battery": 82, "places": [
    {"id": "dock", "name_th": "แท่นชาร์จ", "available": False, "reason": "already_there", "verified": False},
    {"id": "big_room", "name_th": "ห้องประชุมใหญ่", "name_en": "Big meeting room", "available": True,
     "verified": False, "doors": ["D8", "D11"], "min_battery": 30},
], "run": None}


def _with_place(**changes):
    caps = json.loads(json.dumps(CAPS))
    caps["places"][1].update(changes)
    return caps


def test_each_reason_becomes_its_disabled_card_text():
    cases = [{"fn": "navModel", "args": [_with_place(available=False, reason=code), None]}
             for code in rc.NAV_REASONS]
    out = _model(*cases)
    for code, m in zip(rc.NAV_REASONS, out):
        if m["view"] == "message":
            # A refusal that leaves nothing to choose replaces the whole list.
            assert m["text"] == rc.NAV_REASONS[code], code
            continue
        card = m["cards"][1]
        assert card["disabled"] is True and card["badge"] == "", code
        if code == "low_battery":
            assert card["line"] == "แบตเตอรี่เหลือ 82% — ต้องมีอย่างน้อย 30%"
        else:
            assert card["line"] == rc.NAV_REASONS[code], code
    # Finding 33: the app's overlay rewrites only cards that were available,
    # so the `here` card keeps already_there; a whole-panel reason on the
    # rest still replaces the list. Per-place reasons stay on their card.
    whole_panel = {"run_active", "places_stale", "board_unreachable", "relocalize", "nav_latched",
                   "operator_screen", "not_foreground", "body_busy", "body_latched", "pc_run"}
    assert {c for c, m in zip(rc.NAV_REASONS, out) if m["view"] == "message"} == whole_panel
    # Every place refused for the same robot-wide reason: the whole panel
    # says it once instead of repeating it on every card.
    def all_refused(code):
        caps = json.loads(json.dumps(CAPS))
        for place in caps["places"]:
            place.update(available=False, reason=code)
        return caps
    out = _model(*[{"fn": "navModel", "args": [all_refused(code), None]} for code in rc.NAV_REASONS])
    whole = {c: m for c, m in zip(rc.NAV_REASONS, out) if m["view"] == "message"}
    assert set(whole) == {"run_active", "places_stale", "board_unreachable", "relocalize", "nav_latched",
                          "operator_screen", "not_foreground", "body_busy", "body_latched", "pc_run"}
    assert all(m["text"] == rc.NAV_REASONS[c] for c, m in whole.items())


def test_places_view_cards_badge_and_header():
    [m] = _model({"fn": "navModel", "args": [CAPS, {"state": "idle"}]})
    assert m["view"] == "places"
    assert m["header"] == "หุ่นอยู่ที่: แท่นชาร์จ · แบต 82%"
    dock, big = m["cards"]
    assert dock["disabled"] and dock["line"] == "อยู่ที่นี่แล้ว"
    assert not big["disabled"] and big["line"] == "ผ่านประตู D8 และ D11"
    assert big["badge"] == "ยังไม่เคยวิ่งช่วงนี้แยก — เดินตามดูด้วย" and big["en"] == "Big meeting room"
    assert big["est"] == ""                                    # est_s null: no number invented
    [verified] = _model({"fn": "navModel", "args": [_with_place(verified=True, est_s=150), None]})
    assert verified["cards"][1]["badge"] == "" and verified["cards"][1]["est"] == "ใช้เวลาประมาณ 3 นาที"
    [nobat] = _model({"fn": "navModel", "args": [dict(CAPS, battery=None), None]})
    assert nobat["header"] == "หุ่นอยู่ที่: แท่นชาร์จ"
    [bad_id] = _model({"fn": "navModel", "args": [_with_place(id="Big Room"), None]})
    assert [c["id"] for c in bad_id["cards"]] == ["dock"]


def test_whole_panel_states():
    out = _model(
        {"fn": "navModel", "args": [None, None]},
        {"fn": "navModel", "args": [{"loading": True}, None]},
        {"fn": "navModel", "args": [dict(CAPS, installed=False, enabled=False), None]},
        {"fn": "navModel", "args": [dict(CAPS, enabled=False), None]},
        {"fn": "navModel", "args": [dict(CAPS, here=None), None]},
    )
    assert [m["view"] for m in out] == ["message"] * 5
    assert out[0]["kind"] == "loading" and out[1]["kind"] == "loading"
    assert [m["text"] for m in out[2:]] == [rc.NAV_REASONS["not_installed"], rc.NAV_REASONS["disabled"],
                                             rc.NAV_REASONS["location_unknown"]]


def test_the_launchers_own_refusal_wins_over_location_unknown():
    # Findings 8/16: emma_nav.places_caps leaves here=None both when the
    # places file / route sha is stale (top-level reason, places=[]) and
    # when the board read fails (every card board_unreachable). Both used to
    # say "push the robot back to the dock".
    stale = {"enabled": True, "installed": True, "here": None, "reason": "places_stale", "places": []}
    board = json.loads(json.dumps(CAPS))
    board["here"] = None
    for place in board["places"]:
        place.update(available=False, reason="board_unreachable")
    frame, from_raw, from_board = _model(
        {"fn": "navCapsFrame", "args": [stale]},
        {"fn": "navModel", "args": [stale, None]},
        {"fn": "navModel", "args": [board, None]})
    assert frame["reason"] == "places_stale"
    assert from_raw == {"view": "message", "kind": "blocked", "text": rc.NAV_REASONS["places_stale"]}
    assert from_board == {"view": "message", "kind": "blocked", "text": rc.NAV_REASONS["board_unreachable"]}
    # A top-level reason that is not a code is not shown as one.
    [junk] = _model({"fn": "navModel", "args": [dict(stale, reason="<b>x</b>"), None]})
    assert junk["text"] == rc.NAV_REASONS["location_unknown"]


def test_a_latch_replaces_the_list_even_with_the_here_card_kept():
    # Finding 33: after a STOP the app overlays nav_latched on the available
    # cards only; the here card keeps already_there, a no_leg card its own.
    latched = _with_place(available=False, reason="nav_latched")
    mixed = json.loads(json.dumps(latched))
    mixed["places"].append({"id": "lobby", "name_th": "ล็อบบี้", "available": False, "reason": "no_leg"})
    two = json.loads(json.dumps(mixed))
    two["places"][2].update(reason="body_latched")
    a, b, c = _model({"fn": "navModel", "args": [latched, None]}, {"fn": "navModel", "args": [mixed, None]},
                     {"fn": "navModel", "args": [two, None]})
    assert a == {"view": "message", "kind": "blocked", "text": rc.NAV_REASONS["nav_latched"]}
    assert b == a
    assert c["view"] == "places"             # two different whole-panel reasons: each stays on its card


def test_sliding_off_the_hold_button_cancels_the_walk():
    # Finding 15: Chromium captures a touch pointer to the button it started
    # on, so pointerleave never fired while the finger slid onto ยกเลิก.
    run = _scenario("hold_slide_off")
    assert run["stillHolding"] is True       # a move inside the button keeps the hold
    assert run["slidOff"] == 0
    assert run["released"] == [7, 7]         # the implicit capture is released on every press
    assert run["stayed"] == 1


def test_the_page_no_longer_lifts_for_a_safety_bar():
    # Finding 34's lift existed for the native SafetyBar; the owner removed the
    # bar on 10 Oct 2026, so nothing may still reserve or toggle for it.
    run = _scenario("safety_bar")
    assert run == {"idle": False, "walking": False, "arrived": False, "gesture": False, "rest_return": False,
                   "cooldown": False}
    assert "safety-bar-on" not in re.sub(r"/\*.*?\*/", "", _css(), flags=re.S)


def test_phase_mapping_and_step_k_of_n():
    base = {"state": "running", "run_id": RUN, "place": "big_room", "step_from": 10, "step_to": 20, "step": 14}
    cases = [
        dict(base, state="preparing"), dict(base, state="starting"),
        dict(base, do="checkpoint"), dict(base, do="drive"), dict(base, do="door", door="D11"),
        dict(base, do="dock"), dict(base, do="something_new"),
    ]
    out = _model(*[{"fn": "navModel", "args": [CAPS, s]} for s in cases])
    assert [m["phase"] for m in out] == ["กำลังเก็บแขน…", "กำลังตรวจก่อนออกเดิน…", "ตรวจตำแหน่ง", "กำลังเดิน",
                                         "ผ่านประตู D11", "กำลังถอยเข้าแท่นชาร์จ", "กำลังเดิน"]
    assert {m["step"] for m in out} == {"ขั้น 5 จาก 11"}
    assert out[0]["title"] == "กำลังไป" and out[0]["place"] == "ห้องประชุมใหญ่" and not out[0]["readOnly"]
    first, last, outside, missing = _model(
        {"fn": "navModel", "args": [CAPS, dict(base, step=10)]},
        {"fn": "navModel", "args": [CAPS, dict(base, step=20, elapsed_s=61.2)]},
        {"fn": "navModel", "args": [CAPS, dict(base, step=21)]},
        {"fn": "navModel", "args": [CAPS, {"state": "running", "place": "big_room"}]})
    assert first["step"] == "ขั้น 1 จาก 11" and last["step"] == "ขั้น 11 จาก 11"
    assert last["elapsed"] == "เวลา 1:01" and last["done"] == 1
    assert outside["step"] == "" and outside["done"] is None
    assert missing["step"] == "" and missing["elapsed"] == ""


def test_a_pc_started_run_is_shown_read_only_with_the_pc_run_text():
    pc = {"state": "running", "origin": "pc", "reason": "pc_run", "step": 3, "step_from": 1, "step_to": 20}
    by_status, by_caps = _model({"fn": "navModel", "args": [CAPS, pc]},
                                {"fn": "navModel", "args": [dict(CAPS, run=pc), {"state": "idle"}]})
    for m in (by_status, by_caps):
        assert m["view"] == "progress" and m["readOnly"] is True
        assert m["title"] == "หุ่นกำลังเดิน (สั่งจากคอมพิวเตอร์)"
        assert rc.NAV_REASONS["pc_run"] in m["note"]


def test_result_texts():
    base = {"run_id": RUN, "place": "big_room", "step_from": 1, "step_to": 10}
    out = _model(
        {"fn": "navModel", "args": [CAPS, dict(base, state="arrived")]},
        {"fn": "navModel", "args": [CAPS, dict(base, state="stopped", reason="stop_pressed", step=4, do="door", door="D8")]},
        {"fn": "navModel", "args": [CAPS, dict(base, state="refused", reason="location_unknown")]},
        {"fn": "navModel", "args": [CAPS, dict(base, state="failed", reason=None)]},
        {"fn": "navModel", "args": [CAPS, dict(base, state="stopped", reason="made_up_code")]},
    )
    assert out[0] == {"view": "result", "kind": "arrived", "title": "ถึง ห้องประชุมใหญ่ แล้ว", "sub": ""}
    assert out[1]["title"] == "หุ่นหยุดแล้ว — กดหยุดแล้ว"
    assert out[1]["sub"] == "หุ่นหยุดอยู่ที่ขั้น 4: ผ่านประตู D8 — ให้เจ้าหน้าที่ดูแล"
    assert out[2]["kind"] == "refused"
    assert out[2]["title"] == "ยังไปไม่ได้ — " + rc.NAV_REASONS["location_unknown"]
    assert out[3]["title"] == "หุ่นหยุดแล้ว — " + rc.NAV_REASONS["error"]
    assert out[4]["title"] == "หุ่นหยุดแล้ว — ยังไปที่นี่ไม่ได้"     # an unknown code is never shown raw


# ---- P4's frozen strings ----------------------------------------------------

def test_the_strings_p4s_test_asserts_are_untouched():
    page = _html()
    assert "addEventListener('emma-native-asking', function () { nativeDialog = true; });" in page
    assert "addEventListener('emma-native-asked', function () { nativeDialog = false; });" in page
    assert "if (asking === mine && (nativeDialog || (pausedWhileAsking && document.hidden))) {" in page
    # the arm greeting handling is unchanged
    assert ("if (evt.group === 6 && /EmmaRobot\\//.test(navigator.userAgent)) {\n"
            "            window.prompt('emma-arm:greet:6', '');") in page
