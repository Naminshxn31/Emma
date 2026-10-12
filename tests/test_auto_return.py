"""The robot walks back to its charging dock by itself after it stood idle at
the place it walked to (owner, 10 Oct 2026: "เหมือนทำงานเสร็จก็ให้เข้าไปชาร์จ
ที่แท่น"), plus two findings of the same day's reviews (the app's debug DOA
receiver, the app's stop latch reaching the server).

Nothing here has run on the robot. Like the rest of the app's tests:

* NavRules -- the countdown, the place gates, the origins, the latch seconds --
  is plain Java and is COMPILED and RUN with the JDK (the toolchain's, or any
  on PATH);
* AutoReturn / MainActivity / EmmaFace / NavLauncher / ArrayListener are read
  as source (the APK cannot run here), comments stripped;
* the face page's banner and reports run the page's REAL script through
  tests/face_nav_model.cjs (fake DOM, fake bridge, virtual clock);
* the launcher (scripts/emma_nav.py) and the greeter run in Python.
"""
from __future__ import annotations

import asyncio
import re
import subprocess

import pytest

from app import greeter, robot_contracts as rc, robot_face_link
from tests.test_emma_nav_launcher import BIG_ROOM_POSE, FakeBoard, World, _places, nav as emma_nav
from tests.test_emma_robot_body_nav_app import (
    FACE, MAIN, NAV, ROOT, SRC, _javac, code_only, java_string_constant, method, read,
)
from tests.test_face_body_nav_page import _bridge_block, _html, _model, _scenario, _without_comments

RULES = SRC / "NavRules.java"
AUTO = SRC / "AutoReturn.java"
LISTENER = SRC / "ArrayListener.java"


# ---- the contract: who may start a leg ------------------------------------------

def test_auto_is_a_go_origin_and_pc_only_a_status_one():
    assert rc.NAV_ORIGINS == ("tap", "voice", "auto")
    assert rc.NAV_STATUS_ORIGINS == ("tap", "voice", "auto", "pc")
    # the server never sends one: its own command origins are unchanged
    assert robot_face_link.NAV_ORIGINS == ("voice",)


def test_the_launcher_and_the_page_use_the_contracts_copies():
    assert emma_nav.ORIGINS == rc.NAV_ORIGINS
    [consts] = _model({"fn": "constants"})
    assert consts["NAV_REASONS_TH"] == rc.NAV_REASONS     # the failure banner's words
    assert tuple(consts["NAV_ORIGINS"]) == rc.NAV_STATUS_ORIGINS
    # the app's phases and the page's reports, as AutoReturn.java names them
    auto = read(AUTO)
    assert "enum Phase { OFF, COUNTING, WARNING, GOING, FAILED }" in auto
    assert consts["AUTO_PHASES"] == ["off", "counting", "warning", "going", "failed"]
    face = code_only(read(FACE))
    assert sorted(re.findall(r'bridge\.navActivity\("([a-z_]+)"\)', face)) == sorted(consts["ACTIVITY_KINDS"])


def test_the_page_can_never_ask_for_an_auto_walk():
    """The bridge grammar is still emma-nav:go:<place>:tap|voice, in the page
    and in the app; the app's navGo refuses any other origin before go()."""
    good, bad = _model({"fn": "navGoMessage", "args": ["dock", "tap"]},
                       {"fn": "navGoMessage", "args": ["dock", "auto"]})
    assert good == "emma-nav:go:dock:tap" and bad is None
    assert _model({"fn": "allowedMessage", "args": ["emma-nav:go:dock:auto"]}) == [False]
    go = re.compile(java_string_constant(read(FACE), "NAV_GO"))
    assert go.fullmatch("emma-nav:go:dock:tap") and not go.fullmatch("emma-nav:go:dock:auto")
    main = code_only(read(MAIN))
    nav_go = method(main, "@Override public String navGo(String placeId, String origin)")
    assert nav_go.index("if (!NavRules.pageOrigin(origin)) return") < nav_go.index("return nav.go(placeId, origin);")
    # the app's own go(): the one place "auto" is taken, from AutoReturn only
    auto = code_only(read(AUTO))
    assert auto.count("nav.go(") == 1 and "nav.go(dock, NavRules.AUTO)" in auto
    for path in SRC.glob("*.java"):
        if path.name not in ("AutoReturn.java", "NavLauncher.java", "MainActivity.java", "NavRules.java"):
            assert "NavRules.AUTO" not in code_only(read(path)), path.name


def test_auto_goes_like_tap_on_an_unverified_leg_in_the_app_and_the_launcher():
    nav = code_only(read(NAV))
    refusal = method(nav, "private String goRefusal(String place, String origin)")
    assert 'if (NavRules.needsVerifiedLeg(origin) && !entry.optBoolean("verified", false)) return "leg_unverified";' \
        in refusal
    assert "if (place == null || !PLACE_ID.matcher(place).matches() || !NavRules.goOrigin(origin)) {" \
        in method(nav, "synchronized String go(String place, String origin)")
    assert "if (!NavRules.goOrigin(origin)) throw new IllegalArgumentException(\"origin\");" in method(
        nav, "static String[] goCommand(String place, String origin, String runId, boolean armsRested)")
    places = _places()
    leg, why = emma_nav.plan_leg(places, "big_room", "dock", "auto", 50)
    assert why is None and leg is not None and leg.verified is False
    assert emma_nav.plan_leg(places, "big_room", "dock", "voice", 50) == (None, "leg_unverified")
    assert emma_nav.plan_leg(places, "big_room", "dock", "auto", 5) == (None, "low_battery")


def test_the_launcher_runs_an_auto_leg_and_its_argparse_takes_the_word(tmp_path, monkeypatch):
    w = World(tmp_path, board=FakeBoard(docking="not_on_dock", pose=BIG_ROOM_POSE), child_kw={"exit_at": 0.6})
    w.here()
    w.go("dock", "auto")
    assert w.spawned and "--from-step 10 --to-step 20" in " ".join(w.spawned[0])
    assert w.lines[0]["origin"] == "auto"
    seen = {}
    monkeypatch.setattr(emma_nav, "go", lambda env, place, origin, run_id, **kw: seen.update(
        place=place, origin=origin) or 0)
    monkeypatch.setattr(emma_nav.signal, "signal", lambda *a: None)
    assert emma_nav.main(["go", "dock", "--origin", "auto", "--run-id", "a" * 32]) == 0
    assert seen == {"place": "dock", "origin": "auto"}
    with pytest.raises(SystemExit):
        emma_nav.main(["go", "dock", "--origin", "pc", "--run-id", "a" * 32])


def test_the_places_caps_name_the_dock_by_kind_and_carry_the_boards_dock_state(tmp_path):
    w = World(tmp_path, board=FakeBoard(docking="not_on_dock", pose=BIG_ROOM_POSE))
    w.here()
    caps = emma_nav.places_caps(w.env)
    kinds = {c["id"]: c["kind"] for c in caps["places"]}
    assert kinds == {"dock": "dock", "big_room": "room"}
    assert caps["here"] == "big_room" and caps["docking"] == "not_on_dock"
    assert caps["charging"] is None                   # this fake board sends no isCharging: unknown
    dock = next(c for c in caps["places"] if c["id"] == "dock")
    assert dock["available"] is True and dock["min_battery"] == 10
    w.board.isCharging = False
    orig = w.board.power
    w.board.power = lambda: dict(orig(), isCharging=False)
    assert emma_nav.places_caps(w.env)["charging"] is False
    w.board.power = lambda: dict(orig(), isCharging="yes", dockingStatus="on dock;x")
    junk = emma_nav.places_caps(w.env)
    assert junk["charging"] is None and junk["docking"] is None
    down = World(tmp_path / "down", board=FakeBoard(fail=True))
    caps = emma_nav.places_caps(down.env)
    assert caps["docking"] is None and caps["charging"] is None and caps["here"] is None


# ---- NavRules, compiled and run ------------------------------------------------------

AUTO_HARNESS = r"""
package com.emma.robot.control;

public final class AutoHarness {
    static void p(String k, Object v) { System.out.println(k + "=" + v); }
    public static void main(String[] a) {
        p("origins", String.join(",", NavRules.ORIGINS));
        for (String o : new String[]{"tap", "voice", "auto", "pc", "", null}) {
            p("go." + o, NavRules.goOrigin(o));
            p("page." + o, NavRules.pageOrigin(o));
            p("verified." + o, NavRules.needsVerifiedLeg(o));
        }
        p("latch.none", NavRules.latchSeconds(0L, 5000L));
        p("latch.past", NavRules.latchSeconds(4000L, 5000L));
        p("latch.full", NavRules.latchSeconds(65000L, 5000L));
        p("latch.tail", NavRules.latchSeconds(5001L, 5000L));
        p("idle.default", NavRules.AUTO_IDLE_DEFAULT_S);
        p("idle.low", NavRules.clampIdleS(5));
        p("idle.high", NavRules.clampIdleS(100000));
        p("idle.neg", NavRules.clampIdleS(-90));
        p("idle.up", NavRules.stepIdleS(90, 1));
        p("idle.down", NavRules.stepIdleS(90, -7));
        p("idle.floor", NavRules.stepIdleS(60, -1));
        p("idle.ceil", NavRules.stepIdleS(600, 1));
        p("arm.tap", NavRules.armsAutoReturn("arrived", "tap", "big_room", "dock"));
        p("arm.voice", NavRules.armsAutoReturn("arrived", "voice", "big_room", "dock"));
        p("arm.auto", NavRules.armsAutoReturn("arrived", "auto", "big_room", "dock"));
        p("arm.pc", NavRules.armsAutoReturn("arrived", "pc", "big_room", "dock"));
        p("arm.dock", NavRules.armsAutoReturn("arrived", "tap", "dock", "dock"));
        p("arm.stopped", NavRules.armsAutoReturn("stopped", "tap", "big_room", "dock"));
        p("arm.nodock", NavRules.armsAutoReturn("arrived", "tap", "big_room", null));
        // the countdown: due at 100 s, so the warning starts at 90 s
        long due = 100000L;
        p("step.early", NavRules.autoStep(89999L, due, 0L, 0L, 0L));
        p("step.warn", NavRules.autoStep(90000L, due, 0L, 0L, 0L));
        p("step.unconfirmed", NavRules.autoStep(93000L, due, 90000L, 0L, 0L));
        p("step.never_shown", NavRules.autoStep(93001L, due, 90000L, 0L, 0L));
        p("step.shown", NavRules.autoStep(95000L, due, 90000L, 91000L, 94500L));
        p("step.go_early", NavRules.autoStep(100999L, due, 90000L, 91000L, 100500L));
        p("step.go", NavRules.autoStep(101000L, due, 90000L, 91000L, 100500L));
        p("step.gap", NavRules.autoStep(101000L, due, 90000L, 91000L, 98400L));
        p("left.count", NavRules.autoLeftS(40000L, due, 0L, 0L));
        p("left.unconfirmed", NavRules.autoLeftS(90500L, due, 90000L, 0L));
        p("left.shown", NavRules.autoLeftS(95000L, due, 90000L, 91000L));
        p("left.over", NavRules.autoLeftS(200000L, due, 0L, 0L));
        // the places gates, in order
        p("block.ok", NavRules.autoBlock("big_room", "big_room", "dock", "not_on_dock", false, true, null, 50, 10));
        p("block.nodock", NavRules.autoBlock("big_room", "big_room", null, "not_on_dock", false, true, null, 50, 10));
        p("block.armed_dock", NavRules.autoBlock("dock", "dock", "dock", "not_on_dock", false, true, null, 50, 10));
        p("block.unarmed", NavRules.autoBlock(null, "big_room", "dock", "not_on_dock", false, true, null, 50, 10));
        p("block.unknown", NavRules.autoBlock("big_room", null, "dock", "not_on_dock", false, true, null, 50, 10));
        p("block.here_dock", NavRules.autoBlock("big_room", "dock", "dock", "not_on_dock", false, true, null, 50, 10));
        p("block.moved", NavRules.autoBlock("big_room", "lobby", "dock", "not_on_dock", false, true, null, 50, 10));
        p("block.on_dock", NavRules.autoBlock("big_room", "big_room", "dock", "on_dock", false, true, null, 50, 10));
        p("block.dock_null", NavRules.autoBlock("big_room", "big_room", "dock", null, false, true, null, 50, 10));
        p("block.dock_odd", NavRules.autoBlock("big_room", "big_room", "dock", "unknown", false, true, null, 50, 10));
        p("block.charging", NavRules.autoBlock("big_room", "big_room", "dock", "not_on_dock", true, true, null, 50, 10));
        p("block.charging_null", NavRules.autoBlock("big_room", "big_room", "dock", "not_on_dock", null, true, null, 50, 10));
        p("block.card", NavRules.autoBlock("big_room", "big_room", "dock", "not_on_dock", false, false, "low_battery", 50, 10));
        p("block.card_bare", NavRules.autoBlock("big_room", "big_room", "dock", "not_on_dock", false, false, null, 50, 10));
        p("block.battery", NavRules.autoBlock("big_room", "big_room", "dock", "not_on_dock", false, true, null, 9, 10));
        p("block.battery_null", NavRules.autoBlock("big_room", "big_room", "dock", "not_on_dock", false, true, null, null, 10));
        p("block.min_null", NavRules.autoBlock("big_room", "big_room", "dock", "not_on_dock", false, true, null, 50, null));
        for (String b : new String[]{"at_dock", "moved", "no_dock", "location_unknown", "nav_latched", "not_shown",
                "low_battery", "charging_unknown", "places_old"}) {
            p("disarms." + b, NavRules.autoBlockDisarms(b));
        }
    }
}
"""


@pytest.fixture(scope="module")
def rules(tmp_path_factory) -> dict[str, str]:
    tools = _javac()
    if tools is None:
        pytest.skip("no JDK found: NavRules' auto-return countdown, place gates, origins and latch seconds "
                    "were NOT compiled or run -- they are unchecked beyond the source-text tests")
    javac, java = tools
    work = tmp_path_factory.mktemp("autoreturn")
    src = work / "src"
    src.mkdir()
    (src / "AutoHarness.java").write_text(AUTO_HARNESS, encoding="utf-8")
    out = work / "out"
    subprocess.run([javac, "-encoding", "UTF-8", "-d", str(out), str(RULES), str(src / "AutoHarness.java")],
                   check=True, capture_output=True, encoding="utf-8", timeout=120)
    run = subprocess.run([java, "-cp", str(out), "com.emma.robot.control.AutoHarness"],
                         check=True, capture_output=True, encoding="utf-8", timeout=60)
    return dict(line.partition("=")[::2] for line in run.stdout.splitlines())


def test_compiled_origins_are_the_contracts(rules):
    assert tuple(rules["origins"].split(",")) == rc.NAV_ORIGINS
    assert [rules["go." + o] for o in ("tap", "voice", "auto", "pc", "", "null")] == \
        ["true", "true", "true", "false", "false", "false"]
    # the page may ask for tap and voice only
    assert [rules["page." + o] for o in ("tap", "voice", "auto", "pc")] == ["true", "true", "false", "false"]
    # only a voice start needs a verified leg: auto goes like tap
    assert [rules["verified." + o] for o in ("tap", "voice", "auto")] == ["false", "true", "false"]


def test_compiled_latch_seconds(rules):
    assert (rules["latch.none"], rules["latch.past"], rules["latch.full"], rules["latch.tail"]) == ("0", "0", "60", "1")


def test_compiled_idle_time_stays_in_its_safe_range(rules):
    assert rules["idle.default"] == "90"
    assert (rules["idle.low"], rules["idle.high"], rules["idle.neg"]) == ("60", "600", "60")
    assert (rules["idle.up"], rules["idle.down"], rules["idle.floor"], rules["idle.ceil"]) == ("120", "60", "60", "600")


def test_compiled_only_an_arrival_of_somebodys_walk_away_from_the_dock_arms_it(rules):
    assert rules["arm.tap"] == rules["arm.voice"] == "true"
    for case in ("auto", "pc", "dock", "stopped"):
        assert rules["arm." + case] == "false", case
    assert rules["arm.nodock"] == "true"       # unknown dock: armed, then refused as no_dock (disarms)


def test_compiled_countdown_warns_then_waits_for_the_page(rules):
    assert rules["step.early"] == "count" and rules["step.warn"] == "warn"
    # 3 s for the page to put the warning on screen, then no walk
    assert rules["step.unconfirmed"] == "wait" and rules["step.never_shown"] == "not_shown"
    # 10 s counted from the page's FIRST confirmation, while it keeps confirming
    assert rules["step.shown"] == "wait" and rules["step.go_early"] == "wait" and rules["step.go"] == "go"
    assert rules["step.gap"] == "not_shown"
    assert (rules["left.count"], rules["left.unconfirmed"], rules["left.shown"], rules["left.over"]) == \
        ("60", "10", "6", "0")


def test_compiled_place_gates_say_no_to_anything_they_cannot_read(rules):
    assert rules["block.ok"] == "null"
    expect = {"nodock": "no_dock", "armed_dock": "at_dock", "unarmed": "at_dock", "unknown": "location_unknown",
              "here_dock": "at_dock", "moved": "moved", "on_dock": "at_dock", "dock_null": "dock_unknown",
              "dock_odd": "dock_unknown", "charging": "charging_unknown", "charging_null": "charging_unknown",
              "card": "low_battery", "card_bare": "no_leg", "battery": "low_battery",
              "battery_null": "low_battery", "min_null": "low_battery"}
    assert {k: rules["block." + k] for k in expect} == expect
    disarming = {b for b in ("at_dock", "moved", "no_dock", "location_unknown", "nav_latched", "not_shown",
                             "low_battery", "charging_unknown", "places_old") if rules["disarms." + b] == "true"}
    assert disarming == {"at_dock", "moved", "no_dock"}


# ---- AutoReturn / MainActivity / EmmaFace, as source ----------------------------------

def test_the_switch_is_off_by_default_and_lives_on_the_operator_screen_only():
    auto = code_only(read(AUTO))
    assert 'PREF_ENABLED = "nav_auto_return";' in auto
    assert "prefs.getBoolean(PREF_ENABLED, false)" in auto
    assert "prefs.getInt(PREF_IDLE_S, NavRules.AUTO_IDLE_DEFAULT_S)" in auto
    assert "NavRules.clampIdleS(" in method(auto, "int idleS()")
    tick = method(auto, "private void step(long now)")
    # off: nothing is observed, gated or asked
    off = tick.index("if (!enabled()) {")
    assert off < tick.index("observeRun(now);") and off < tick.index("countdown(now);")
    main = read(MAIN)
    assert "กลับแท่นเองเมื่อว่าง: " in main
    code = code_only(main)
    assert "autoReturn.setEnabled(!autoReturn.enabled())" in method(
        code, "private void buildBodyNavControls(LinearLayout root)")
    face = code_only(read(FACE))
    assert "setEnabled" not in face.split("private String answer(String message)", 1)[1].split("\n    }\n", 1)[0]


def test_the_walk_is_decided_on_fresh_places_after_every_gate_of_a_go():
    auto = code_only(read(AUTO))
    count = method(auto, "private void countdown(long now)")
    go_branch = count[count.index("NavRules.STEP_GO.equals(step)"):]
    assert go_branch.index("String why = block(true);") < go_branch.index("nav.go(dock, NavRules.AUTO)")
    warn_branch = count[count.index("NavRules.STEP_WARN.equals(step)"):count.index("NavRules.STEP_NOT_SHOWN")]
    assert warn_branch.index("String why = block(false);") < warn_branch.index("setPhase(Phase.WARNING);")
    assert "host.changed();" in warn_branch                  # the page is told to draw it
    block = method(auto, "private String block(boolean fresh)")
    assert 'if (fresh && (age < 0 || age > NavRules.AUTO_CAPS_FRESH_MS)) return "places_old";' in block
    assert block.index("NavRules.autoBlock(") < block.index("return nav.autoRefusal(dock);")
    nav = code_only(read(NAV))
    assert "return goRefusal(place, NavRules.AUTO);" in method(nav, "synchronized String autoRefusal(String place)")
    # a failed READ never makes an old answer look fresh
    assert "placesReadAt = SystemClock.elapsedRealtime();" in method(nav, "void refreshPlaces()")
    assert "long at = placesReadAt;" in method(nav, "long placesAgeMs()")


def test_a_walk_back_that_does_not_arrive_is_shown_and_never_retried():
    auto = code_only(read(AUTO))
    observe = method(auto, "private void observeRun(long now)")
    going = observe[observe.index("if (phase == Phase.GOING) {"):observe.index("if (run.active()) {")]
    assert "setPhase(Phase.FAILED);" in going and "failReason = why;" in going
    assert "nav.go(" not in observe
    # FAILED is left only by somebody at the screen (not by the app coming back
    # to the front) or a new arrival; never by the clock
    activity = method(auto, "void activity(String kind)")
    assert activity.index('if ("resume".equals(kind)) return;') < activity.index("setPhase(Phase.OFF);")
    assert "Phase.FAILED" not in method(auto, "private void countdown(long now)")
    tick = method(auto, "private void step(long now)")
    assert "if (phase == Phase.COUNTING || phase == Phase.WARNING) countdown(now);" in tick
    # a refusal before anything moved: count again, or disarm -- never a walk
    not_going = method(auto, "private void notGoing(String why, long now)")
    assert "NavRules.autoBlockDisarms(why)" in not_going and "nav." not in not_going


def test_signs_of_somebody_restart_the_count():
    main = code_only(read(MAIN))
    touch = method(main, "public boolean dispatchTouchEvent(MotionEvent event)")
    assert 'autoReturn.activity("touch");' in touch and touch.rstrip().endswith("return super.dispatchTouchEvent(event);")
    assert touch.index("trackCornerHold(event);") < touch.index('autoReturn.activity("touch");')
    keys = method(main, "public boolean dispatchKeyEvent(KeyEvent event)")
    assert keys.index('autoReturn.activity("key");') < keys.index("KEYCODE_BUTTON_START")
    assert 'autoReturn.activity("resume");' in method(main, "protected void onResume()")
    auto = code_only(read(AUTO))
    assert "if (host.operatorVisible()) activityAt(now);" in method(auto, "private void step(long now)")
    face = code_only(method(read(FACE), "private String answer(String message)"))
    for literal, call in (('"emma-nav:auto"', "bridge.navAuto()"), ('"emma-nav:auto:shown"', "bridge.navAutoShown()"),
                          ('"emma-nav:auto:stay"', "bridge.navAutoStay()"),
                          ('"emma-nav:activity:call_start"', 'bridge.navActivity("call_start")'),
                          ('"emma-nav:activity:call_end"', 'bridge.navActivity("call_end")'),
                          ('"emma-nav:activity:speech"', 'bridge.navActivity("speech")')):
        case = face.index("case " + literal + ":")
        assert face.startswith("return " + call + ";", face.index("return", case)), literal
    # the warning counts only while the page confirms it is on screen
    assert "autoReturn.shown();" in method(main, "@Override public String navAutoShown()")
    shown = method(auto, "void shown()")
    assert "if (phase != Phase.WARNING) return;" in shown and "if (shownAt <= 0) shownAt = now;" in shown


def test_a_failing_tick_disarms_instead_of_crashing_the_app():
    """The tick runs on the UI thread: an exception would take the app, and
    with it the native STOP bar, down."""
    tick = method(code_only(read(AUTO)), "private void tick()")
    assert tick.index("ui.postDelayed(tick, TICK_MS);") < tick.index("try {")
    assert "step(SystemClock.elapsedRealtime());" in tick
    catch = tick[tick.index("catch (RuntimeException e) {"):]
    assert "setPhase(Phase.OFF);" in catch and "nav." not in catch


def test_the_auto_return_never_speaks_or_reaches_the_server():
    auto = code_only(read(AUTO))
    for word in ("announce", "WebSocket", "ws", "send(", "http", "Toast", "speak"):
        assert word not in auto, word
    # its only voice is the page's banner, through the fixed emma-nav-changed event
    assert "host.changed();" in auto


# ---- the face page, through the node harness -----------------------------------------

def test_off_by_default_the_page_reports_nothing_and_draws_nothing():
    run = _scenario("auto_off")
    assert set(run["asked"]) == {"emma-nav:auto"}        # reads only; no activity report
    assert run["banner"]["hidden"] is True
    assert run["frameTypes"] == ["body_caps", "nav_caps", "nav_status"] and run["autoFrames"] == 0


def test_a_call_and_speech_in_it_hold_the_walk_back():
    run = _scenario("auto_activity")
    # the call re-reads the state first: the switch was turned on while the
    # operator screen hid the page, and no change event reached it
    assert run["asked"] == ["emma-nav:auto", "emma-nav:activity:call_start", "emma-nav:activity:speech",
                            "emma-nav:activity:speech", "emma-nav:activity:call_end"]
    assert run["banner"]["hidden"] is True                # counting draws nothing


def test_the_warning_is_confirmed_only_while_it_is_on_screen_and_stay_takes_it_down():
    run = _scenario("auto_warning")
    first = run["first"]
    # the read that FINDS the warning is not a confirmation: it was not drawn yet
    assert first["asked"] == ["emma-nav:auto"]
    assert first["banner"] == {"hidden": False, "kind": "warning", "title": "หุ่นจะกลับแท่นชาร์จใน 10 วินาที",
                               "sub": "ยังคุยหรือใช้งานอยู่ แตะ “อยู่ต่อ”", "button": "อยู่ต่อ"}
    assert run["confirms"] == 3                           # every second after it is drawn
    assert run["later"]["title"] == "หุ่นจะกลับแท่นชาร์จใน 7 วินาที"
    assert run["whileHidden"] and set(run["whileHidden"]) == {"emma-nav:auto"}   # hidden: no "shown"
    assert run["afterStay"]["last"] == "emma-nav:auto:stay" and run["afterStay"]["banner"]["hidden"] is True
    assert run["readsAfterStay"] == 0                     # its clock stopped with it


def test_going_and_a_failure_in_the_contracts_words():
    run = _scenario("auto_going_failed")
    assert run["going"] == {"hidden": False, "kind": "going", "title": "กำลังกลับแท่นชาร์จ",
                            "sub": "กดปุ่มหยุดฉุกเฉิน (E-stop) บนตัวหุ่นได้ทุกเมื่อ", "button": None}
    assert run["failed"]["title"] == "กลับแท่นชาร์จเองไม่สำเร็จ — " + rc.NAV_REASONS["obstacle"]
    assert run["failed"]["button"] == "รับทราบ" and "ไม่ลองเดินเองอีก" in run["failed"]["sub"]
    assert run["junk"]["title"] == "กลับแท่นชาร์จเองไม่สำเร็จ — " + rc.NAV_REASONS["error"]
    assert run["dismissed"]["hidden"] is True and run["asked"][-1] == "emma-nav:auto:stay"


def test_the_page_model_whitelists_the_apps_answer():
    states = _model({"fn": "autoState", "args": [{"enabled": True, "phase": "warning", "left_s": 7,
                                                  "place": "big_room", "reason": None, "extra": "x"}]},
                    {"fn": "autoState", "args": [{"enabled": True, "phase": "walking"}]},
                    {"fn": "autoState", "args": [{"accepted": False, "reason": "bad_request"}]},
                    {"fn": "autoState", "args": [{"enabled": True, "phase": "failed", "left_s": -1,
                                                  "place": "../x", "reason": "<b>"}]})
    assert states[0] == {"enabled": True, "phase": "warning", "left_s": 7, "place": "big_room", "reason": None}
    assert states[1] is None and states[2] is None        # an old APK's bad_request draws nothing
    assert states[3] == {"enabled": True, "phase": "failed", "left_s": None, "place": None, "reason": None}
    assert _model({"fn": "autoBanner", "args": [{"phase": "counting"}]},
                  {"fn": "autoBanner", "args": [{"phase": "off"}]}) == [None, None]
    assert _model(*[{"fn": "activityMessage", "args": [k]} for k in ("call_start", "speech", "touch", None)]) == \
        ["emma-nav:activity:call_start", "emma-nav:activity:speech", None, None]


def test_the_panel_names_an_auto_walk_and_does_not_lock_on_it():
    caps = {"enabled": True, "installed": True, "here": None, "battery": 50,
            "places": [{"id": "dock", "name_th": "แท่นชาร์จ", "available": False, "reason": "run_active"}]}
    walk = {"state": "running", "origin": "auto", "place": "dock", "run_id": "ab" * 16}
    [m] = _model({"fn": "navModel", "args": [caps, walk]})
    assert m["view"] == "progress" and m["title"] == "กลับแท่นชาร์จเองเมื่อว่าง" and m["place"] == "แท่นชาร์จ"
    # the panel's lock is for its OWN (tap) walk: an auto walk can be watched and closed
    code = _without_comments(_bridge_block())
    assert "return !!s && s.origin === 'tap' && s.reason !== 'pc_run';" in code


def test_the_page_never_writes_the_apps_text_as_markup():
    code = _without_comments(_bridge_block())
    render = code.split("function renderAuto() {", 1)[1].split("\n    }\n", 1)[0]
    assert "innerHTML" not in render and "textContent" in render
    markup = _html().split('<div class="emma-auto" id="emmaAutoReturn"', 1)[1].split("<!-- Navigation", 1)[0]
    buttons = re.findall(r"<button[^>]*>(.*?)</button>", markup, flags=re.S)
    assert buttons == ["อยู่ต่อ"] and "หยุด" not in "".join(buttons)


def test_the_page_re_reads_the_state_when_it_comes_back_in_front():
    code = _without_comments(_bridge_block())
    assert "document.addEventListener('visibilitychange', function () { if (ON_ROBOT && !document.hidden) pullAuto(); });" \
        in code


def test_the_call_reports_speech_through_one_guarded_helper():
    html = _html()
    handler = html.split("    function onMessage(msg) {", 1)[1].split("    // ---- dial / hang up ----", 1)[0]
    for case in ("case 'speech_started':", "case 'user_transcript':", "case 'assistant_transcript':"):
        body = handler.split(case, 1)[1].split("break;", 1)[0]
        assert "robotHeardSpeech();" in body, case
    helper = handler.split("function robotHeardSpeech() {", 1)[1].split("\n    }\n", 1)[0]
    assert "typeof bridge.speech === 'function'" in helper


# ---- the stop latch reaches the server (review, 10 Oct) ---------------------------------

def test_the_status_carries_the_stop_latch_and_its_end_is_announced():
    nav = code_only(read(NAV))
    status = method(nav, "String status()")
    assert "int latch = NavRules.latchSeconds(latchedUntil, SystemClock.elapsedRealtime());" in status
    assert '.put("poll_stale", stale).put("stop_latch_s", latch)' in status
    stop = method(nav, "boolean stop(String reason, boolean explicit)")
    assert stop.index("latchedUntil = SystemClock.elapsedRealtime() + NAV_STOP_LATCH_MS;") \
        < stop.index("announceLatchEnd();")
    end = method(nav, "private void announceLatchEnd()")
    assert "if (!latched()) host.changed();" in end and "NAV_STOP_LATCH_MS + 250" in end


def test_the_page_passes_the_latch_on_and_a_change_goes_at_once():
    run = _scenario("latch_frame")
    assert len(run["latched"]) == 1 and run["latched"][0]["stop_latch_s"] == 60
    assert len(run["cleared"]) == 1 and run["cleared"][0]["stop_latch_s"] == 0
    bad = _model({"fn": "navStatusFrame", "args": [{"state": "idle", "stop_latch_s": "60"}]},
                 {"fn": "navStatusFrame", "args": [{"state": "idle", "stop_latch_s": -3}]})
    assert all("stop_latch_s" not in f for f in bad)


def _greeter_robot(monkeypatch, latch):
    from tests.test_face_probable import _robot

    sent, rows = _robot(monkeypatch)
    monkeypatch.setattr(greeter, "_last_turn_at", 0.0)       # no cooldown from another test's turn
    monkeypatch.setattr(greeter, "_turn_home", None)
    face = object()
    monkeypatch.setattr(robot_face_link, "live_faces", lambda: [face])
    monkeypatch.setattr(robot_face_link, "nav_status",
                        lambda session: {"type": "nav_status", "state": "idle", "stop_latch_s": latch}
                        if session is face else None)
    return sent, rows


def test_the_greeters_base_turn_holds_still_through_the_apps_stop_latch(monkeypatch):
    from tests.test_face_probable import _turn

    sent, rows = _greeter_robot(monkeypatch, 42)
    turned, still = _turn()
    assert turned is False and sent == [] and rows[-1][1]["why"] == "app_latched"


def test_the_greeters_base_turn_holds_still_after_a_desk_stop(monkeypatch):
    """Review 12 Oct: on the "board" plan a desk STOP (main.py's stop button)
    only cancels the chassis -- it never reaches the app, so the app latch
    above cannot see it. The chassis remembers it; the turn waits it out."""
    import asyncio

    from app import robot_chassis
    from tests.test_face_probable import _turn

    sent, rows = _greeter_robot(monkeypatch, 0)
    async def _nothing(*a, **k):
        return None
    monkeypatch.setattr(robot_chassis, "cancel", _nothing)
    monkeypatch.setattr(robot_chassis, "_last_desk_stop_at", None)
    asyncio.run(robot_chassis.drive_stop(reason="release"))          # a joystick let go: not a stop
    assert not robot_chassis.desk_stopped_within(greeter.TURN_DESK_STOP_HOLD_S)
    asyncio.run(robot_chassis.drive_stop(reason="stop button"))
    assert robot_chassis.desk_stopped_within(greeter.TURN_DESK_STOP_HOLD_S)
    turned, still = _turn()
    assert turned is False and sent == [] and rows[-1][1]["why"] == "desk_stopped"


@pytest.mark.parametrize("latch", [0, None, True, "60"])
def test_no_latch_or_an_older_apk_turns_as_before(monkeypatch, latch):
    from tests.test_face_probable import _turn

    sent, rows = _greeter_robot(monkeypatch, latch)
    turned, _ = _turn()
    assert turned is True and len(sent) == 1


# ---- the debug DOA receiver (review, 10 Oct) ------------------------------------------

def test_the_adb_doa_hook_needs_dump_so_no_installed_app_can_send_it():
    src = code_only(read(LISTENER))
    assert "static final String DEBUG_SENDER_PERMISSION = android.Manifest.permission.DUMP;" in src
    ctor = method(src, "ArrayListener(Activity activity, Host host)")
    calls = re.findall(r"registerReceiver\(([^;]*)\);", ctor)
    assert calls == ["debugReceiver, filter, DEBUG_SENDER_PERMISSION, null, Context.RECEIVER_EXPORTED",
                     "debugReceiver, filter, DEBUG_SENDER_PERMISSION, null"]
    assert ctor.index("FLAG_DEBUGGABLE") < ctor.index("registerReceiver(")
    # still no new <uses-permission>: DUMP is asked of the SENDER, not held by the app
    manifest = read(ROOT / "apps/emma-robot-android/app/src/main/AndroidManifest.xml")
    assert "DUMP" not in manifest
