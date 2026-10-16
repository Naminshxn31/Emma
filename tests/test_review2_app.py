"""Review 2 (2026-10-09), package D: the robot app (apps/emma-robot-android)
and the robot face page's bridge/panel (client/voice-preview.html).

Nothing here has run on the robot. The Java is read as source (the project's
Android pattern -- the APK cannot run here) except NavRules, which is pure and
is COMPILED and RUN with the plain JDK; the page runs its REAL script through
tests/face_nav_model.cjs (fake DOM, fake bridge, virtual clock).

Findings covered: 5 (app + page halves), 7 (app half), 10, 17, 21, 22, 23,
24, 25, 26.
"""
from __future__ import annotations

import re
import subprocess

import pytest

from app import robot_contracts as rc
from tests.test_emma_robot_body_nav_app import (
    ARM, BODY, DRAFT, FACE, MAIN, NAV, ROOT, SRC, _javac, code_only, method, read,
)
from tests.test_face_body_nav_page import _bridge_block, _scenario, _without_comments

RULES = SRC / "NavRules.java"
EMMA_NAV = ROOT / "scripts" / "emma_nav.py"


# ---- 5: the forced nav_caps at the end of a walk carried the pre-walk cache --

def test_a_run_ending_drops_the_places_cache_before_the_page_hears_it():
    """The page forces a nav_caps the moment it sees the terminal status; that
    frame carried `here` from before the walk (or `run: running` from a
    mid-walk refresh, which put the server back to active for good)."""
    nav = code_only(read(NAV))
    status = method(nav, "private void setStatus(Run which, String state, String reason)")
    assert status.index("if (which.terminal()) invalidatePlaces();") < status.rindex("host.changed();")
    merge = method(nav, "private void merge(Run which, JSONObject status)")
    assert merge.index("if (ended) invalidatePlaces();") < merge.index("if (changed) host.changed();") \
        < merge.index("if (ended) refreshPlaces();")
    # a PC run seen only by the poll ends the same way
    poll = method(nav, "private void poll()")
    assert poll.index("if (wasActive && !nowActive) invalidatePlaces();") \
        < poll.index("host.changed();") < poll.index("if (wasActive && !nowActive) refreshPlaces();")
    drop = method(nav, "private void invalidatePlaces()")
    assert "placesGen.incrementAndGet();" in drop and "places = null;" in drop
    # with no cache, places() answers loading (the page retries every second)
    places = method(nav, "String places()")
    assert 'if (cached == null) return "{\\"loading\\":true}";' in places


def test_a_refresh_that_started_before_the_end_is_thrown_away_and_rerun():
    nav = code_only(read(NAV))
    refresh = method(nav, "void refreshPlaces()")
    assert "if (!placesRefreshing.compareAndSet(false, true)) return;" in refresh
    took = refresh.index("final int gen = placesGen.get();")
    shell = refresh.index("runShell(PLACES_SCRIPT,")
    locked = refresh.index("synchronized (placesLock) {")
    stale = refresh.index("if (gen != placesGen.get()) {")
    write = refresh.index("places = fresh;")
    assert took < shell < locked < stale < write
    assert "again = true;" in refresh[stale:write]
    # the check and the write cannot be split by an end: the drop takes the same lock
    assert "synchronized (placesLock) {" in method(nav, "private void invalidatePlaces()")
    tail = refresh[refresh.index("} finally {"):]
    assert tail.index("placesRefreshing.set(false);") < tail.index("if (again) refreshPlaces();") \
        < tail.index("host.changed();")


def test_the_page_owes_the_forced_caps_and_sends_a_change_at_once_after_a_run():
    run = _scenario("caps_after_run")
    assert run["atReady"] == 1
    # the forced frame survived two loading answers and a plain read between them
    assert run["owed"] == 1
    # the app's refresh after the end reached the server at once, not on the 30 s tick
    assert run["refreshed"] == ["dock", "big_room"]
    # outside the 30 s after a run, the old rate limit holds
    assert run["lateAtOnce"] == 0
    code = _without_comments(_bridge_block())
    assert "if (force) capsOwed = true;" in code
    assert "capsRetry = setTimeout(function () { pullCaps(false); }, 1000);" in code
    assert "capsAfterRunUntil = Date.now() + CAPS_MS;" in code


# ---- 7: --arms-rested crosses the boundary only after a real rest ------------

def test_arms_rested_is_passed_only_after_the_rest_returned_true():
    """With the switch on. Off = the operator's word (owner 9 Oct, see
    test_face_status_link.test_the_rest_switch_off_means_the_operator_vouches_for_the_arms)."""
    nav = code_only(read(NAV))
    launch = method(nav, "private void launch(Run next)")
    declared = launch.index("boolean armsRested = NavRules.operatorVouchesArms(requireArmRest());")
    gate = launch.index("if (requireArmRest()) {")
    rest = launch.index("if (!host.restArms(REST_TIMEOUT_MS)) {")
    refused = launch.index('finish(next, "refused", "arms_not_rest");')
    rested = launch.index("armsRested = true;")
    spawn = launch.index("new ProcessBuilder(goCommand(next.place, next.origin, next.id, armsRested))")
    assert declared < gate < rest < refused < rested < spawn
    assert launch.count("armsRested = true;") == 1
    # the flag is a constant word, only from goCommand's boolean
    cmd = method(nav, "static String[] goCommand(String place, String origin, String runId, boolean armsRested)")
    assert '(armsRested ? " --arms-rested" : "")' in cmd
    assert nav.count('" --arms-rested"') == 1


def test_the_launcher_takes_the_flag_and_refuses_with_a_code_the_page_can_read():
    """The other half lives in scripts/emma_nav.py (the launcher agent): the
    app's word must be one its argparse knows, or every walk ends launcher_lost."""
    launcher = read(EMMA_NAV)
    assert 'ap.add_argument("--arms-rested"' in launcher
    assert "arms_rested=a.arms_rested" in launcher
    refusals = set(re.findall(r'end\("refused", "([a-z_]+)"', launcher))
    assert "arms_not_rest" in refusals and "arms_not_rest" in rc.NAV_REASONS


# ---- 10: a real CH340 drop is noticed --------------------------------------

def test_a_failed_write_while_connected_is_a_link_loss():
    arm = code_only(read(ARM))
    on_error = method(arm, "public void onError(String detail)")
    assert on_error.index('if (armManager.isConnected()) linkLost("serial write failed");') \
        < on_error.index("listener.onArmError(detail);")


def test_the_usb_detach_broadcast_for_the_ch340_is_a_link_loss():
    arm = code_only(read(ARM))
    assert "USB_VENDOR_ID = 0x1a86;" in arm and "USB_PRODUCT_ID = 0x7523;" in arm
    receive = method(arm, "public void onReceive(Context context, Intent intent)")
    assert "UsbManager.ACTION_USB_DEVICE_DETACHED.equals(intent.getAction())" in receive
    assert "device.getVendorId() == USB_VENDOR_ID && device.getProductId() == USB_PRODUCT_ID" in receive
    assert 'linkLost("CH340 detached");' in receive
    assert "new IntentFilter(UsbManager.ACTION_USB_DEVICE_DETACHED)" in arm
    # unregistered when the Activity goes
    assert "appContext.unregisterReceiver(usbDetach);" in method(arm, "void release()")
    main = code_only(read(MAIN))
    destroy = method(main, "protected void onDestroy()")
    assert destroy.index("upperBody.disconnect();") < destroy.index("upperBody.release();")


def test_a_link_loss_aborts_the_frames_then_tears_the_sdk_link_down():
    arm = code_only(read(ARM))
    lost = method(arm, "void linkLost(String why)")
    once = lost.index("if (linkDown || !armManager.isConnected()) return;")
    order = [lost.index("linkDown = true;"), lost.index("abort.run();"),
             lost.index("listener.onArmLinkLost(why);"), lost.index("armManager.disconnect();")]
    assert once < order[0] and order == sorted(order), order
    # the SDK's flag alone is not "connected" any more; connect() rebuilds a lost link
    assert "return armManager.isConnected() && !linkDown;" in method(arm, "boolean isConnected()")
    connect = method(arm, "boolean connect()")
    assert "if (!linkDown) return true;" in connect
    assert connect.index("port = prepareInternalArmPort();") < connect.index("linkDown = false;") \
        < connect.index("robotManager.connectArm(port, BAUD_RATE);")
    # the frame loop asks linkUp() (which also notices a vanished ttyUSB node)
    up = method(arm, "private boolean linkUp()")
    assert "!new File(node).exists()" in up and 'linkLost("ttyUSB node gone");' in up
    loop = method(arm, "void playFrames(List<PoseFrame> source, BooleanSupplier stillCurrent, Runnable onDone, "
                       "Runnable onAbort)")
    assert loop.count("if (!linkUp()) {") == 2            # before each frame, and before onDone
    assert loop.rindex("if (!linkUp()) {") < loop.index("onDone.run();")
    assert "frameAbort.set(abort);" in loop and "frameAbort.compareAndSet(abort, null);" in loop
    assert "&& !linkDown" in loop                          # a frame's wait ends on the loss


def test_a_link_loss_ends_any_gesture_as_failed_without_a_latch():
    main = code_only(read(MAIN))
    lost = method(main, "@Override public void onArmLinkLost(String why)")
    assert "upperBodyArmed = false;" in lost and "body.armLost();" in lost
    body = code_only(read(BODY))
    arm_lost = method(body, "void armLost()")
    assert arm_lost.index('finish(generation.get(), "failed", "arm_unavailable");') < arm_lost.index("halt();")
    assert "latchedUntil" not in arm_lost


# ---- 17: no places yet is not "unknown place" -------------------------------

def test_a_voice_go_before_the_places_load_is_not_called_an_unknown_place():
    refusal = method(code_only(read(NAV)), "private String goRefusal(String place, String origin)")
    block = refusal[refusal.index("if (list == null) {"):]
    block = block[:block.index("}") + 1]
    assert "refreshPlaces();" in block and 'return "launcher_failed";' in block
    assert "unknown_place" not in block
    assert "launcher_failed" in rc.NAV_REASONS


# ---- 21: the dead WebView is never touched by the stop it triggers ----------

def test_the_dead_webview_is_dropped_before_page_lost_and_stops_notify_last():
    face = code_only(read(FACE))
    rec = method(face, "private void recoverFromRendererDeath(WebView dead, boolean crashed)")
    assert rec.index("boolean wasFace = webView == dead;") < rec.index("if (wasFace) webView = null;") \
        < rec.index("bridge.pageLost();")
    assert "if (!wasFace || root == null) return;" in rec
    notify = method(face, "private void notifyPage(String event)")
    assert notify.index("try {") < notify.index("webView.evaluateJavascript(") < notify.index("catch (RuntimeException e)")
    body = code_only(read(BODY))
    for sig in ("void stop(String reason)", "void halt()"):
        m = method(body, sig)
        assert m.index("voidEverything(") < m.index("stopArm();") < m.index("host.changed();"), sig
    assert "host.changed();" not in method(body, "private void voidEverything(String reason)")


# ---- 22: only a playback that ran to the end counts as watched -------------

def test_the_draft_counts_as_watched_only_from_the_playbacks_on_done():
    main = code_only(read(MAIN))
    play = method(main, "private void playDraftMotion()")
    start = play.index("final int editsAtStart = motionDraft.startPlayback();")
    call = play.index("upperBody.playFrames(motionDraft.frames(), null,")
    done = play.index("motionDraft.markPlayed(editsAtStart);")
    cut = play.index("motionDraft.playbackCut();")
    assert start < call < done < cut
    assert main.count("motionDraft.markPlayed(") == 1
    draft = code_only(read(DRAFT))
    assert "playedSinceEdit = false;" in method(draft, "int startPlayback()")
    assert "playedSinceEdit = editsAtStart == edits && !frames.isEmpty();" in method(draft, "void markPlayed(int editsAtStart)")
    assert "edits++;" in method(draft, "ArmController.PoseFrame captureFrame(int durationMs)")
    assert "edits++;" in method(draft, "void clear()")
    assert "void markPlayed()" not in draft


# ---- 23: a head move cut short leaves the head "unknown" --------------------

def test_head_known_is_false_from_the_start_of_a_head_sequence_until_its_on_done():
    body = code_only(read(BODY))
    play = method(body, "private void play(String id, int gen, List<BodyGestures.Frame> frames, boolean speech)")
    reset = play.index("if (head) headKnown = false;")
    call = play.index("arm.playFrames(poseFrames,")
    known = play.index("if (head) headKnown = true;")
    assert play.index("if (head && !headKnown) sequence.add(BodyGestures.headPrefix());") < reset < call < known
    assert body.count("headKnown = true;") == 1


# ---- 24: a voice gesture's connect cannot arm the operator's manual keys ----

def test_only_the_x_press_arms_the_manual_arm_keys():
    main = code_only(read(MAIN))
    assert "upperBodyArmed = true;" not in method(main, "@Override public void onArmConnected(String port)")
    assert main.count("upperBodyArmed = true;") == 1
    assert "upperBodyArmed = true;" in method(main, "private void connectUpperBody()")
    show = method(main, "private void showOperator()")
    assert show.index("upperBodyArmed = false;") < show.index("stopBodyAndNav(")


# ---- 25: a wedged su does not pile up stuck root children -------------------

def test_periodic_su_calls_back_off_while_children_are_stuck_and_stops_never_do():
    nav = code_only(read(NAV))
    shell = method(nav, "private static Shell runShell(String script, long timeoutMs, boolean periodic)")
    gate = shell.index("if (periodic && NavRules.suWedgeBlocks(liveWedged(), lastWedgeAt, SystemClock.elapsedRealtime()) != null) {")
    assert gate < shell.index("new ProcessBuilder(")
    timeout = shell[shell.index("if (!process.waitFor(timeoutMs, TimeUnit.MILLISECONDS)) {"):]
    assert timeout.index("process.destroyForcibly();") < timeout.index("noteWedged(process);")
    assert "return runShell(script, timeoutMs, false);" in method(nav, "private static Shell runShell(String script, long timeoutMs)")
    periodic = set(re.findall(r"runShell\(([A-Z_]+), [A-Z_ *0-9]+, true\)", nav))
    assert periodic == {"POLL_SCRIPT", "PLACES_SCRIPT", "CHECK_SCRIPT", "REAP_SCRIPT"}, periodic
    # the stop path is never gated
    escalate = method(nav, "private void escalate(Run current, boolean full)")
    assert "runShell(STOP_SCRIPT, SHELL_TIMEOUT_MS);" in escalate and "runShell(KILL_SCRIPT, SHELL_TIMEOUT_MS);" in escalate
    assert ", true)" not in escalate + method(nav, "private void endLauncher(Run current, Process tether)")
    # the operator screen says so
    main = code_only(read(MAIN))
    assert "nav.suWedged()" in method(main, "private void publishBodyNavStatus()")


WEDGE_HARNESS = r"""
package com.emma.robot.control;

public final class WedgeHarness {
    public static void main(String[] a) {
        long now = 1_000_000L;
        System.out.println("none=" + NavRules.suWedgeBlocks(0, 0L, now));
        System.out.println("one_fresh=" + NavRules.suWedgeBlocks(1, now - 1000L, now));
        System.out.println("one_retry=" + NavRules.suWedgeBlocks(1, now - NavRules.WEDGE_RETRY_MS, now));
        System.out.println("two_retry=" + NavRules.suWedgeBlocks(2, now - NavRules.WEDGE_RETRY_MS - 1, now));
        System.out.println("cap=" + NavRules.suWedgeBlocks(NavRules.MAX_WEDGED_SU, 0L, now));
        System.out.println("max=" + NavRules.MAX_WEDGED_SU);
    }
}
"""


def test_compiled_su_wedge_gate(tmp_path):
    tools = _javac()
    if tools is None:
        pytest.skip("no JDK found: NavRules.suWedgeBlocks was NOT compiled or run -- the su back-off "
                    "(review2 #25) is unchecked beyond the source-text test")
    javac, java = tools
    src = tmp_path / "src"
    src.mkdir()
    (src / "WedgeHarness.java").write_text(WEDGE_HARNESS, encoding="utf-8")
    out = tmp_path / "out"
    subprocess.run([javac, "-encoding", "UTF-8", "-d", str(out), str(RULES), str(src / "WedgeHarness.java")],
                   check=True, capture_output=True, encoding="utf-8", timeout=120)
    run = subprocess.run([java, "-cp", str(out), "com.emma.robot.control.WedgeHarness"],
                         check=True, capture_output=True, encoding="utf-8", timeout=60)
    values = dict(line.partition("=")[::2] for line in run.stdout.splitlines())
    assert values["none"] == "null"
    assert values["one_fresh"] == "su_wedged"        # backing off: no new child for a minute
    assert values["one_retry"] == "null"             # one retry a minute, so a recovered su is found
    assert values["two_retry"] == "null"
    assert values["cap"] == "su_wedged"              # never more than MAX_WEDGED_SU stuck children
    assert values["max"] == "3"


# ---- 26: watching a walk does not lock the face page ------------------------

def test_a_pc_run_or_a_voice_walk_can_be_watched_and_closed():
    run = _scenario("watch_walks")
    for name in ("pc", "voice"):
        w = run[name]
        assert w["opened"]["view"] == ["progress"], name
        assert w["opened"]["x"] is True and w["opened"]["inert"] is True, name
        assert w["viaX"] is True and w["inertAfter"] is False, name     # mic / End usable again
    tap = run["tap"]
    # the panel's own kind of walk still cannot be closed by a stray tap (§3)
    assert tap["opened"]["x"] is False and tap["viaX"] is False and tap["viaEscape"] is False
    assert tap["afterEnd"] == {"view": ["result"], "x": True} and tap["closedAfterEnd"] is True
    code = _without_comments(_bridge_block())
    assert "function canClose() { return !walking() || !ownWalk(); }" in code


# ---- review 12 Oct: a stop wins over queued operator arm commands; no walk on a stale poll ----

def test_an_operator_arm_command_queued_before_a_stop_never_runs():
    """B sent #STOP at once, then the group/servo/home command that was
    already waiting on `io` (behind a stick drive, a base poll, the arm
    connect) ran anyway. Every such command now re-checks, on `io`, that no
    stop came after it was queued -- and a connect that a stop overtook does
    not arm."""
    main = code_only(read(MAIN))
    stop = method(main, "private void stopAndDisarm(String reason, boolean explicit, boolean deleteNow)")
    assert stop.index("armStops.incrementAndGet();") < stop.index("upperBodyArmed = false;")
    for name in ("private void playSelectedArmGroup()", "private void sendSelectedServo()",
                 "private void resetUpperBodyHome()"):
        body = method(main, name)
        queued = body.index("final int stopsAtQueue = armStops.get();")
        assert queued < body.index("io.execute(") < body.index("if (!armCommandStillWanted(stopsAtQueue)) return;"), name
    wanted = method(main, "private boolean armCommandStillWanted(int stopsAtQueue)")
    assert "upperBodyArmed && armStops.get() == stopsAtQueue" in wanted
    connect = method(main, "private void connectUpperBody()")
    assert connect.index("if (armStops.get() != stopsAtQueue) {") < connect.index("upperBodyArmed = true;")


def test_a_walk_is_refused_while_the_run_poll_is_stale():
    """Gestures already refused a stale run.pid poll (nav_unknown); a walk
    did not, and launch() rests the arms before the launcher's own check."""
    nav = code_only(read(NAV))
    refusal = method(nav, "private String goRefusal(String place, String origin)")
    assert refusal.index('return "run_active";') < refusal.index('if (!pollFresh()) return "launcher_failed";') \
        < refusal.index('return "body_latched";')
    assert "launcher_failed" in rc.NAV_REASONS
