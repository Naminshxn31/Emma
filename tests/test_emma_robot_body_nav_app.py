"""Emma's body and navigation on the robot app (docs/emma-body-nav-design-2026-10-08.md,
package P4): the native end of move_body and of the นำทาง slot.

Two kinds of check, because the APK cannot run here:

* Source contracts (the project's Android pattern, see test_emma_robot_app.py):
  the closed bridge grammar, the trust gate on every emma-* message, the stop
  paths, the thread rules and the launcher command line, read from the Java.
* `BodyGestures.java` is deliberately free of android.* and org.json, so it is
  COMPILED with the plain JDK and RUN: the clamps, the pose rules and the
  refusal order are exercised for real, not just found as text. Without a JDK
  that part skips and says what went unchecked.

Nothing here talks to a robot.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import robot_contracts as rc

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps/emma-robot-android/app"
SRC = APP / "src/main/java/com/emma/robot/control"
MAIN = SRC / "MainActivity.java"
FACE = SRC / "EmmaFace.java"
BODY = SRC / "BodyController.java"
GESTURES = SRC / "BodyGestures.java"
POSES = SRC / "BodyPoseStore.java"
NAV = SRC / "NavLauncher.java"
ARM = SRC / "ArmController.java"
DRAFT = SRC / "MotionDraft.java"
MANIFEST = APP / "src/main/AndroidManifest.xml"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def method(text: str, signature: str) -> str:
    """The body of one Java method, by brace matching from its signature."""
    start = text.index(signature)
    open_at = text.index("{", start)
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_at + 1:i]
    raise AssertionError(f"unbalanced braces after {signature}")


def code_only(java: str) -> str:
    """Drop comments so a guard cannot be satisfied by prose (a "//" inside a
    string literal stays)."""
    out, i, n = [], 0, len(java)
    while i < n:
        c = java[i]
        if c in "\"'":
            j = i + 1
            while j < n and java[j] != c:
                j += 2 if java[j] == "\\" else 1
            out.append(java[i:j + 1])
            i = j + 1
        elif java.startswith("//", i):
            i = java.find("\n", i)
            i = n if i < 0 else i
        elif java.startswith("/*", i):
            end = java.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def statement(text: str, start_marker: str) -> str:
    """The Java statement starting at `start_marker`, up to the `;` that ends
    it OUTSIDE string literals (shell scripts are full of `;`)."""
    i = text.index(start_marker)
    j, n = i, len(text)
    while j < n:
        c = text[j]
        if c == '"':
            j += 1
            while text[j] != '"':
                j += 2 if text[j] == "\\" else 1
        elif c == ";":
            return text[i:j]
        j += 1
    raise AssertionError(start_marker)


def java_string_constant(text: str, name: str) -> str:
    m = re.search(r'static final Pattern %s = Pattern\.compile\("((?:[^"\\]|\\.)*)"\)' % name, text)
    assert m, name
    return m.group(1).encode().decode("unicode_escape")


# ---- the bridge: a closed grammar behind one trust gate --------------------

BRIDGE_STRINGS = {
    '"emma-body:list"': "bridge.bodyList()",
    '"emma-body:status"': "bridge.bodyStatus()",
    '"emma-body:stop"': "bridge.bodyStop()",
    '"emma-nav:places"': "bridge.navPlaces()",
    '"emma-nav:status"': "bridge.navStatus()",
    '"emma-nav:stop"': "bridge.navStop()",
    '"emma-face:token"': "bridge.faceToken()",
}


def test_every_bridge_string_of_the_grammar_maps_to_its_bridge_call():
    answer = code_only(method(read(FACE), "private String answer(String message)"))
    for literal, call in BRIDGE_STRINGS.items():
        case = answer.index("case " + literal + ":")
        assert answer.startswith("return " + call + ";", answer.index("return", case)), literal
    # the two parametrised shapes, matched WHOLE (matches(), never find())
    assert "Matcher go = NAV_GO.matcher(message);" in answer
    assert "if (go.matches()) return bridge.navGo(go.group(1), go.group(2));" in answer
    assert "if (body.matches()) return bridge.bodyGesture(body.group(1));" in answer
    assert ".find()" not in code_only(read(FACE))
    # anything else in the emma-body:/emma-nav:/emma-face: space is refused
    assert answer.rstrip().endswith("return BAD_REQUEST;")
    assert 'BAD_REQUEST = "{\\"accepted\\":false,\\"reason\\":\\"bad_request\\"}"' in read(FACE)


def test_the_shape_regexes_admit_only_contract_ids():
    """ID_RE from robot_contracts, inside the bridge prefix. A trailing newline
    must fail: Java's `$` would have accepted it, and the id goes into su."""
    face = read(FACE)
    body = re.compile(java_string_constant(face, "BODY_ACTION"))
    go = re.compile(java_string_constant(face, "NAV_GO"))
    assert java_string_constant(face, "BODY_ACTION") == "emma-body:(" + rc.ID_RE.strip("^$") + ")"
    for action in rc.BODY_ACTION_IDS:
        assert body.fullmatch("emma-body:" + action), action
    for bad in ("emma-body:Nod", "emma-body:nod\n", "emma-body:n", "emma-body:../x", "emma-body:nod;reboot",
                "emma-body:" + "a" * 33, "emma-body:ก้ม", "emma-body: nod"):
        assert not body.fullmatch(bad), bad
    assert go.fullmatch("emma-nav:go:big_room:tap")
    assert go.fullmatch("emma-nav:go:dock:voice")
    for bad in ("emma-nav:go:big_room:pc", "emma-nav:go:big_room", "emma-nav:go:Big:tap",
                "emma-nav:go:big_room:tap\n", "emma-nav:go:big_room:tap --to-step 3",
                "emma-nav:go:big room:tap", "emma-nav:go:x:tap"):
        assert not go.fullmatch(bad), bad


def test_the_trust_gate_holds_for_every_emma_message():
    prompt = code_only(method(read(FACE), "public boolean onJsPrompt("))
    # legacy greet: through the same gate, answers "" as before
    assert "if (trustedPreview(url, view)) bridge.armGreeting();" in prompt
    # every emma-body:/emma-nav:/emma-face: answer goes through the gate
    assert "result.confirm(trustedPreview(url, view) ? answer(message) : BAD_REQUEST);" in prompt
    assert prompt.count("answer(message)") == 1
    for prefix in ('"emma-body:"', '"emma-nav:"', '"emma-face:"'):
        assert prefix in prompt, prefix
    # every other prompt keeps the browser's own behaviour
    assert "return false;           // unchanged browser behaviour" in method(read(FACE), "public boolean onJsPrompt(")
    gate = code_only(method(read(FACE), "private boolean trustedPreview(String url, WebView view)"))
    for part in ("trusted(originOf(source))", "trusted(originOf(current))",
                 '"/preview".equals(source.getPath())', '"/preview".equals(current.getPath())',
                 '"1".equals(current.getQueryParameter("app"))'):
        assert part in gate, part


def test_the_face_class_reaches_no_hardware_and_no_launcher():
    """EmmaFace parses shapes only. The forbidden list of test_emma_robot_app,
    extended for the body/nav pieces (§5 P4)."""
    text = code_only(read(FACE))
    for word in ("SlamwareCorePlatform", "ArmController", "moveBy", "192.168.11.1", "1445", "com.aobo",
                 "cancelNavigation", "BodyController", "NavLauncher", "/system/xbin/su", "emma_nav",
                 "BodyGestures", "ProcessBuilder", "addJavascriptInterface", "SafetyBar", "safetyBar"):
        assert word not in text, word


def test_native_to_page_uses_only_the_two_fixed_event_names():
    face = code_only(read(FACE))
    assert 'notifyPage("emma-body-changed")' in method(face, "void bodyChanged()")
    assert 'notifyPage("emma-nav-changed")' in method(face, "void navChanged()")
    events = set(re.findall(r'notifyPage\("([^"]+)"\)', face))
    assert events == {"emma-native-asking", "emma-native-asked", "emma-body-changed", "emma-nav-changed"}


def test_motion_needs_the_gates_and_stop_and_status_need_none():
    main = code_only(read(MAIN))
    body = code_only(read(BODY))
    nav = code_only(read(NAV))
    # foreground = resumed AND window focus, for both
    assert main.count("public boolean foreground() { return faceForeground && windowFocused; }") == 2
    request = method(body, "synchronized String request(String id)")
    assert "BodyGestures.refusal(enabled(), host.operatorVisible(), host.foreground()," in request
    refusal = method(nav, "private String goRefusal(String place, String origin)")
    for gate in ("host.operatorVisible()", "host.foreground()", "latched()", "host.bodyLatched()",
                 "host.bodyBusy()", '"leg_unverified"'):
        assert gate in refusal, gate
    # stops and reads are unconditional: no if, no gate, always "accepted"
    for sig in ("@Override public String bodyStop()", "@Override public String navStop()"):
        stop = method(main, sig)
        assert "if" not in stop and 'return "{\\"accepted\\":true}";' in stop, sig
    for sig, call in (("@Override public String bodyList()", "body.list()"),
                      ("@Override public String bodyStatus()", "body.status()"),
                      ("@Override public String navPlaces()", "nav.places()"),
                      ("@Override public String navStatus()", "nav.status()")):
        assert method(main, sig).strip() == "return " + call + ";", sig


# ---- gesture table and clamps ----------------------------------------------

DESIGN_STATUSES = {
    "handshake": "verified", "greet_arm": "verified", "rest": "verified",
    "head_center": "direction_known", "head_down": "direction_known", "nod": "direction_known",
    "head_up": "needs_mapping", "shake_head": "needs_mapping", "head_left": "needs_mapping",
    "head_right": "needs_mapping", "raise_left_arm": "needs_mapping", "raise_right_arm": "needs_mapping",
    "wave": "needs_mapping", "hands_left": "needs_mapping", "hands_right": "needs_mapping",
}


def java_ids() -> list[str]:
    block = read(GESTURES)
    block = block[block.index("static final String[] IDS = {"):]
    block = block[:block.index("};")]
    return re.findall(r'"([a-z_]+)"', block)


def test_java_gesture_ids_are_the_contract_ids_in_order():
    assert tuple(java_ids()) == rc.BODY_ACTION_IDS
    assert set(DESIGN_STATUSES) == set(rc.BODY_ACTION_IDS) - {"stop"}
    assert set(rc.BODY_STATUSES) == {"verified", "direction_known", "operator_mapped", "needs_mapping"}
    for status in rc.BODY_STATUSES:
        assert f'"{status}"' in read(GESTURES), status


CLAMPS = {
    "COOLDOWN_MS": 3000, "MAX_PER_MINUTE": 6, "MIN_FRAME_MS": 800, "MAX_FRAME_MS": 5000,
    "MAX_FRAMES": 6, "MAX_TOTAL_MS": 12000, "STOP_LATCH_MS": 60000,
    "HEAD_LIMIT_MIN": 1460, "HEAD_LIMIT_MAX": 1540, "ARM_LIMIT_MIN": 1300, "ARM_LIMIT_MAX": 1700,
    "HEAD_PREFIX_MS": 5000, "HEAD_RETURN_MS": 1200, "HANDSHAKE_GROUP": 6, "GREET_GROUP": 3,
    "HANDSHAKE_HOLD_MS": 5000, "GREET_HOLD_MS": 1500, "GROUP_FALLBACK_MS": 30000,
    "REST_AGF_TIMEOUT_MS": 15000,
}


def test_clamp_constants_are_the_design_values():
    text = code_only(read(GESTURES))
    for name, value in CLAMPS.items():
        assert re.search(r"static final (?:long|int) %s = %d;" % (name, value), text), name
    assert "DEFAULT_VOICE_CHANNELS = {5, 7, 8};" in text
    nav = code_only(read(NAV))
    for name, value in {"NAV_STOP_LATCH_MS": 60000, "STOP_KILL_AFTER_MS": 2000, "PING_MS": 1000,
                        "POLL_MS": 5000, "PLACES_REFRESH_MS": 10000, "REST_TIMEOUT_MS": 15000}.items():
        assert re.search(r"static final long %s = %d;" % (name, value), nav), name


def test_no_head_pulse_outside_the_one_measured_range():
    """`#8P1540` is the only head pulse anybody measured (30 Sep). The first
    revision used ±80 and the minus sides; this scans every frame(...) in the
    table for a ch7/ch8 value outside 1460-1540."""
    text = code_only(read(GESTURES))
    calls = re.findall(r"\bframe\((\d+|HEAD_PREFIX_MS)((?:,\s*\d+,\s*\d+)+)\)", text)
    assert len(calls) >= 9, calls
    for _, pairs in calls:
        nums = [int(n) for n in re.findall(r"\d+", pairs)]
        for channel, pulse in zip(nums[::2], nums[1::2]):
            if channel in (7, 8):
                assert 1460 <= pulse <= 1540, (channel, pulse)
    # head_down and nod use ch8 only, and only 1540 / 1500
    for gid in ("head_down", "nod"):
        body = text[text.index('if ("%s".equals(id))' % gid):]
        body = body[:body.index("}")]
        pairs = re.findall(r"frame\(\d+((?:,\s*\d+,\s*\d+)+)\)", body)
        assert pairs, gid
        for p in pairs:
            nums = [int(n) for n in re.findall(r"\d+", p)]
            assert nums[::2] == [8] * (len(nums) // 2), gid
            assert set(nums[1::2]) <= {1540, 1500}, gid


# ---- the JDK harness: run BodyGestures for real ----------------------------

HARNESS = r"""
package com.emma.robot.control;

import java.io.FileDescriptor;
import java.io.FileOutputStream;
import java.io.PrintStream;
import java.util.*;

public final class Harness {
    static PrintStream out;
    static Set<Integer> chans(int... c) { Set<Integer> s = new HashSet<>(); for (int x : c) s.add(x); return s; }
    static void p(String k, Object v) { out.println(k + "=" + v); }
    static List<BodyGestures.Frame> fs(BodyGestures.Frame... f) { return Arrays.asList(f); }
    static String ok(String r) { return r == null ? "ok" : "refused"; }

    public static void main(String[] a) throws Exception {
        out = new PrintStream(new FileOutputStream(FileDescriptor.out), true, "UTF-8");
        p("ids", String.join(",", BodyGestures.IDS));
        for (String id : BodyGestures.IDS) {
            p("status." + id, BodyGestures.builtInStatus(id));
            p("mapped." + id, BodyGestures.effectiveStatus(id, true));
        }
        Set<Integer> def = chans(BodyGestures.DEFAULT_VOICE_CHANNELS);
        for (String id : new String[]{"head_center", "head_down", "nod"}) {
            p("builtin." + id, ok(BodyGestures.checkFrames(BodyGestures.builtInFrames(id), def)));
            p("builtin_total." + id, BodyGestures.totalMs(BodyGestures.builtInFrames(id)));
            p("builtin_first." + id, BodyGestures.builtInFrames(id).get(0).durationMs);
        }
        p("builtin_none.head_up", BodyGestures.builtInFrames("head_up"));
        p("prefix", BodyGestures.headPrefix().durationMs + " " + BodyGestures.headPrefix().targets);
        // refusal order: all conditions bad, then fix them one by one
        boolean[] bad = new boolean[10];
        Arrays.fill(bad, true);
        for (int i = 0; i <= 10; i++) {
            if (i > 0) bad[i - 1] = false;
            p("refusal." + i, BodyGestures.refusal(!bad[0], bad[1], !bad[2], bad[3] ? "navigating" : null,
                    bad[4], bad[5] ? "needs_mapping" : (bad[6] ? "direction_known" : "verified"),
                    !bad[6], bad[7], bad[8], bad[9]));
        }
        p("refusal.nav_unknown", BodyGestures.refusal(true, false, true, "nav_unknown", false, "verified", false, false, false, false));
        p("refusal.direction_known_allowed", BodyGestures.refusal(true, false, true, null, false, "direction_known", true, false, false, false));
        p("refusal.operator_mapped", BodyGestures.refusal(true, false, true, null, false, "operator_mapped", false, false, false, false));
        p("refusal.null_status", BodyGestures.refusal(true, false, true, null, false, null, true, false, false, false));
        // clamps
        Set<Integer> c578 = chans(5, 7, 8);
        p("frames.ok", ok(BodyGestures.checkFrames(fs(BodyGestures.frame(800, 5, 1700), BodyGestures.frame(5000, 8, 1460)), c578)));
        p("frames.short", ok(BodyGestures.checkFrames(fs(BodyGestures.frame(799, 5, 1500)), c578)));
        p("frames.long", ok(BodyGestures.checkFrames(fs(BodyGestures.frame(5001, 5, 1500)), c578)));
        BodyGestures.Frame f = BodyGestures.frame(1000, 5, 1500);
        p("frames.six", ok(BodyGestures.checkFrames(fs(f, f, f, f, f, f), c578)));
        p("frames.seven", ok(BodyGestures.checkFrames(fs(f, f, f, f, f, f, f), c578)));
        BodyGestures.Frame g = BodyGestures.frame(4000, 5, 1500);
        p("frames.total12000", ok(BodyGestures.checkFrames(fs(g, g, g), c578)));
        p("frames.total12001", ok(BodyGestures.checkFrames(fs(g, g, BodyGestures.frame(4001, 5, 1500)), c578)));
        p("frames.channel", ok(BodyGestures.checkFrames(fs(BodyGestures.frame(1000, 6, 1500)), c578)));
        p("frames.narrowed", ok(BodyGestures.checkFrames(fs(BodyGestures.frame(1000, 8, 1540)), chans(5, 7))));
        p("frames.ch8_1541", ok(BodyGestures.checkFrames(fs(BodyGestures.frame(1000, 8, 1541)), c578)));
        p("frames.ch8_1459", ok(BodyGestures.checkFrames(fs(BodyGestures.frame(1000, 8, 1459)), c578)));
        p("frames.ch7_1580", ok(BodyGestures.checkFrames(fs(BodyGestures.frame(1000, 7, 1580)), c578)));
        p("frames.ch5_1701", ok(BodyGestures.checkFrames(fs(BodyGestures.frame(1000, 5, 1701)), c578)));
        p("frames.ch5_1299", ok(BodyGestures.checkFrames(fs(BodyGestures.frame(1000, 5, 1299)), c578)));
        p("frames.empty", ok(BodyGestures.checkFrames(new ArrayList<BodyGestures.Frame>(), c578)));
        // saving a pose
        List<BodyGestures.Frame> pose = fs(BodyGestures.frame(2000, 5, 1620), BodyGestures.frame(2500, 5, 1620));
        p("save.raise_left_arm", ok(BodyGestures.checkSave("raise_left_arm", pose, c578, true)));
        p("save.not_played", ok(BodyGestures.checkSave("raise_left_arm", pose, c578, false)));
        p("save.verified", ok(BodyGestures.checkSave("handshake", pose, c578, true)));
        p("save.rest", ok(BodyGestures.checkSave("rest", pose, c578, true)));
        p("save.direction_known", ok(BodyGestures.checkSave("head_down", fs(BodyGestures.frame(3000, 8, 1520)), c578, true)));
        p("save.unknown", ok(BodyGestures.checkSave("dance", pose, c578, true)));
        p("save.stop", ok(BodyGestures.checkSave("stop", pose, c578, true)));
        // rule 6
        List<BodyGestures.Frame> tilt = BodyGestures.withHeadReturn(fs(BodyGestures.frame(3000, 8, 1520)));
        p("return.tilt", tilt.size() + " " + tilt.get(tilt.size() - 1).durationMs + " " + tilt.get(tilt.size() - 1).targets);
        List<BodyGestures.Frame> back = BodyGestures.withHeadReturn(fs(BodyGestures.frame(3000, 8, 1520), BodyGestures.frame(3000, 8, 1500)));
        p("return.already", back.size());
        p("return.arm", BodyGestures.withHeadReturn(pose).size());
        p("rest_after.arm", BodyGestures.restAfter(pose));
        p("rest_after.head", BodyGestures.restAfter(fs(BodyGestures.frame(3000, 7, 1500, 8, 1540))));
        p("head.nod", BodyGestures.isHead("nod") + " " + BodyGestures.isHead("wave"));
    }
}
"""


def _javac() -> tuple[str, str] | None:
    candidates = []
    home = os.environ.get("JAVA_HOME")
    if home:
        candidates.append(Path(home) / "bin")
    candidates += [Path(p) for p in glob.glob(str(ROOT / ".tmp/android-toolchain/jdk/*/bin"))]
    for bindir in candidates:
        for exe in ("javac.exe", "javac"):
            javac = bindir / exe
            java = bindir / exe.replace("javac", "java")
            if javac.is_file() and java.is_file():
                return str(javac), str(java)
    javac, java = shutil.which("javac"), shutil.which("java")
    return (javac, java) if javac and java else None


@pytest.fixture(scope="module")
def harness(tmp_path_factory) -> dict[str, str]:
    tools = _javac()
    if tools is None:
        pytest.skip("no JDK found: BodyGestures was NOT compiled or run -- the clamps, the pose rules "
                    "and the refusal order are unchecked beyond the source-text tests")
    javac, java = tools
    work = tmp_path_factory.mktemp("bodygestures")
    src = work / "src"
    src.mkdir()
    (src / "Harness.java").write_text(HARNESS, encoding="utf-8")
    out = work / "out"
    subprocess.run([javac, "-encoding", "UTF-8", "-d", str(out), str(GESTURES), str(src / "Harness.java")],
                   check=True, capture_output=True, encoding="utf-8", timeout=120)
    run = subprocess.run([java, "-cp", str(out), "com.emma.robot.control.Harness"],
                         check=True, capture_output=True, encoding="utf-8", timeout=60)
    values = {}
    for line in run.stdout.splitlines():
        key, _, value = line.partition("=")
        values[key] = value
    return values


def test_compiled_ids_and_statuses_are_the_design_table(harness):
    assert tuple(harness["ids"].split(",")) == rc.BODY_ACTION_IDS
    for gid, status in DESIGN_STATUSES.items():
        assert harness["status." + gid] == status, gid
    assert harness["status.stop"] == "null"
    # a recorded pose never overrides a verified gesture
    for gid, status in DESIGN_STATUSES.items():
        assert harness["mapped." + gid] == ("verified" if status == "verified" else "operator_mapped"), gid


def test_compiled_refusal_order_is_the_contract_order(harness):
    """§2c: disabled -> operator_screen -> not_foreground -> navigating/nav_unknown
    -> latched -> needs_mapping/direction_unconfirmed -> busy -> cooldown -> rate_limited."""
    order = ["disabled", "operator_screen", "not_foreground", "navigating", "latched", "needs_mapping",
             "direction_unconfirmed", "busy", "cooldown", "rate_limited", "null"]
    assert [harness[f"refusal.{i}"] for i in range(11)] == order
    assert harness["refusal.nav_unknown"] == "nav_unknown"
    assert harness["refusal.direction_known_allowed"] == "null"
    assert harness["refusal.operator_mapped"] == "null"
    assert harness["refusal.null_status"] == "needs_mapping"
    # every code the app can answer is a contract code
    for code in order[:-1] + ["nav_unknown", "bad_request", "arm_unavailable"]:
        assert code in rc.BODY_REASONS, code


def test_compiled_built_in_gestures_obey_their_own_clamps(harness):
    for gid in ("head_center", "head_down", "nod"):
        assert harness["builtin." + gid] == "ok", gid
        # the first move of every head gesture is T3000, the owner's timing
        assert harness["builtin_first." + gid] == "3000", gid
    assert harness["builtin_none.head_up"] == "null"
    assert harness["prefix"] == "5000 {7=1500, 8=1500}"


@pytest.mark.parametrize("case, expected", [
    ("ok", "ok"), ("short", "refused"), ("long", "refused"), ("six", "ok"), ("seven", "refused"),
    ("total12000", "ok"), ("total12001", "refused"), ("channel", "refused"), ("narrowed", "refused"),
    ("ch8_1541", "refused"), ("ch8_1459", "refused"), ("ch7_1580", "refused"),
    ("ch5_1701", "refused"), ("ch5_1299", "refused"), ("empty", "refused"),
])
def test_compiled_frame_clamps(harness, case, expected):
    assert harness["frames." + case] == expected


def test_compiled_pose_save_rules(harness):
    assert harness["save.raise_left_arm"] == "ok"
    assert harness["save.not_played"] == "refused"       # rule 5: somebody watched it move
    assert harness["save.verified"] == "refused"         # rule 1: verified cannot be overwritten
    assert harness["save.rest"] == "refused"
    assert harness["save.direction_known"] == "ok"
    assert harness["save.unknown"] == "refused"
    assert harness["save.stop"] == "refused"
    # rule 6: a pose that leaves ch8 off-centre gets a T1200 return of what it touched
    assert harness["return.tilt"] == "2 1200 {8=1500}"
    assert harness["return.already"] == "2"
    assert harness["return.arm"] == "2"
    assert harness["rest_after.arm"] == "true"
    assert harness["rest_after.head"] == "false"
    assert harness["head.nod"] == "true false"


def test_poses_are_stored_where_the_design_says_and_a_bad_entry_is_dropped():
    store = code_only(read(POSES))
    assert 'PREFS = "emma_body_poses";' in store and 'KEY = "poses_json";' in store
    save = method(store, "synchronized String save(")
    assert save.index("BodyGestures.checkSave(") < save.index("prefs.edit()")
    get = method(store, "synchronized Pose get(String id)")
    assert "BodyGestures.VERIFIED.equals(builtIn)) return null;" in get
    assert "if (BodyGestures.checkFrames(frames, everyChannel(frames)) != null) return null;" in get
    assert 'if (!entry.optBoolean("played_ok", false)) return null;' in get


# ---- BodyController: one lock, latches, threads -----------------------------

def test_a_body_stop_latches_and_voids_on_the_calling_thread_then_stops_off_io():
    body = code_only(read(BODY))
    stop = method(body, "void stop(String reason)")
    assert stop.index("latchedUntil = SystemClock.elapsedRealtime() + BodyGestures.STOP_LATCH_MS;") \
        < stop.index("voidEverything(reason);") < stop.index("stopArm();")
    assert "io." not in stop
    stop_arm = method(body, "private void stopArm()")
    assert stop_arm.index("bodyStopper.execute(() -> {") < stop_arm.index("arm.stopAll();")
    assert 'new Thread(runnable, "body-stopper")' in body
    void = method(body, "private void voidEverything(String reason)")
    assert "generation.incrementAndGet();" in void and "ui.removeCallbacksAndMessages(TIMERS);" in void
    # a rest-before-run waiting on #AGF is woken by the stop and refuses
    assert "if (waiting != null) waiting.countDown();" in void
    clear = method(body, "void clearLatch()")
    assert "latchedUntil = 0L;" in clear


def test_gesture_tasks_re_check_the_latch_and_the_walk_before_moving():
    body = code_only(read(BODY))
    play = method(body, "private void play(String id, int gen, List<BodyGestures.Frame> frames)")
    assert play.lstrip().startswith("if (stale(gen)) return;")
    assert play.index("host.ensureArm();") < play.index("if (stale(gen)) return;", 30)
    stale = method(body, "private boolean stale(int gen)")
    assert "gen != generation.get() || latched()" in stale
    assert "host.navBlock()" in stale
    # every timer carries the generation it was set under
    assert "if (gen == generation.get()) work.run();" in method(body, "private void later(int gen, Runnable work, long delayMs)")


def test_one_lock_on_the_arm_for_voice_greeting_and_rest_before_a_walk():
    body = code_only(read(BODY))
    assert "busy.compareAndSet(false, true)" in method(body, "synchronized String request(String id)")
    assert "busy.compareAndSet(false, true)" in method(body, "synchronized void greet()")
    assert "busy.compareAndSet(false, true)" in method(body, "boolean restBeforeRun(long timeoutMs)")
    greet = method(body, "synchronized void greet()")
    assert "latched(now) || host.navBlock() != null" in greet    # never while walking or latched
    assert "greetingPending" not in code_only(read(MAIN))          # the old second lock is gone


def test_the_rest_before_a_walk_waits_for_agf():
    body = code_only(read(BODY))
    rest = method(body, "boolean restBeforeRun(long timeoutMs)")
    assert rest.index("arm.resetHome();") < rest.index("agf.await(timeoutMs, TimeUnit.MILLISECONDS)")
    assert "rested = agf.await(timeoutMs, TimeUnit.MILLISECONDS) && gen == generation.get();" in rest
    assert "restAgf = agf;" in rest
    assert "if (waiting != null) waiting.countDown();" in method(body, "void onArmFinished()")
    nav = code_only(read(NAV))
    launch = method(nav, "private void launch(Run next)")
    assert "if (requireArmRest()) {" in launch
    assert launch.index("if (requireArmRest()) {") < launch.index("if (!host.restArms(REST_TIMEOUT_MS)) {")
    assert 'finish(next, "refused", "arms_not_rest");' in launch
    assert "return body.restBeforeRun(timeoutMs);" in code_only(read(MAIN))
    assert 'PREF_REQUIRE_ARM_REST, true' in nav           # default on


PLAY_FRAMES = "void playFrames(List<PoseFrame> source, BooleanSupplier stillCurrent, Runnable onDone, Runnable onAbort)"


def test_play_frames_has_a_completion_callback_only_for_a_full_run():
    arm = code_only(read(ARM))
    assert PLAY_FRAMES in arm
    assert "playFrames(source, null, null, null);" in method(arm, "void playFrames(List<PoseFrame> source)")
    body = method(arm, PLAY_FRAMES)
    assert ("if (onDone != null && run == poseGeneration.get() && ownerCurrent(stillCurrent)\n"
            "                        && ended.compareAndSet(false, true)) onDone.run();") in body


# ---- NavLauncher -------------------------------------------------------------

def test_the_go_command_is_constants_plus_three_validated_values():
    nav = code_only(read(NAV))
    cmd = method(nav, "static String[] goCommand(String place, String origin, String runId, boolean armsRested)")
    assert 'if (place == null || !PLACE_ID.matcher(place).matches()) throw' in cmd
    # tap|voice|auto = robot_contracts.NAV_ORIGINS (test_auto_return compares the copies)
    assert 'if (!NavRules.goOrigin(origin)) throw' in cmd
    assert "if (runId == null || !RUN_ID.matcher(runId).matches()) throw" in cmd
    assert ('return new String[]{SU_BINARY, "0", "sh", "-c",\n                GUARD + "exec ../py -u " + LAUNCHER '
            '+ " go " + place + " --origin " + origin\n                        + " --run-id " + runId + " --tether"'
            ' + (armsRested ? " --arms-rested" : "")};') in cmd
    assert java_string_constant(read(NAV), "PLACE_ID") == rc.ID_RE.strip("^$")
    assert java_string_constant(read(NAV), "RUN_ID") == rc.RUN_ID_RE.strip("^$")
    # the launcher owns the legs: no step numbers ever leave this app
    for word in ("--from-step", "--to-step", "drive_route.py", "from_step", "to_step"):
        assert word not in nav, word
    # every su process is a constant script, or goCommand
    builders = re.findall(r"new ProcessBuilder\(([^;]*?)\)\s*\.", nav)
    assert sorted(builders) == sorted(['goCommand(next.place, next.origin, next.id, armsRested)',
                                       'SU_BINARY, "0", "sh", "-c", script']), builders
    for name in ("POLL_SCRIPT", "PLACES_SCRIPT", "CHECK_SCRIPT", "REAP_SCRIPT", "STOP_SCRIPT", "KILL_SCRIPT"):
        assert re.search(r"private static final String %s =" % name, nav), name
    assert set(re.findall(r"runShell\(([A-Z_]+),", nav)) == {
        "POLL_SCRIPT", "PLACES_SCRIPT", "CHECK_SCRIPT", "REAP_SCRIPT", "STOP_SCRIPT", "KILL_SCRIPT"}


def test_the_run_id_is_secure_random_hex32_made_before_the_prompt_returns():
    nav = code_only(read(NAV))
    assert "private final SecureRandom random = new SecureRandom();" in nav
    rid = method(nav, "private String newRunId()")
    assert "new byte[16]" in rid and 'String.format("%02x", b & 0xff)' in rid
    go = method(nav, "synchronized String go(String place, String origin)")
    assert go.index("newRunId()") < go.index("host.runArmWork(() -> launch(next));") \
        < go.index("return ack(true, null, next.id);")


def test_the_cancel_token_is_checked_after_the_rest_and_right_before_su():
    nav = code_only(read(NAV))
    launch = method(nav, "private void launch(Run next)")
    rest = launch.index("host.restArms(REST_TIMEOUT_MS)")
    exec_at = launch.index("new ProcessBuilder(goCommand(")
    after_rest = launch.index("if (isCancelled(next)) {", rest)
    lock = launch.index("synchronized (launchLock) {")
    # straight after the rest, before the vendor app is force-stopped or the
    # status says "starting": a STOP during preparing changes nothing else
    assert after_rest < launch.index("host.releaseBase();") < lock < exec_at
    last_check = launch.rindex("if (isCancelled(next)) {", 0, exec_at)
    assert lock < last_check < exec_at                     # inside the lock the stopper takes
    between = launch[last_check:exec_at]
    assert "finish(next, \"stopped\", cancelReason);" in between and "return;" in between
    # and the stopper reads the tether under the same lock
    escalate = method(nav, "private void escalate(Run current, boolean full)")
    assert escalate.index("synchronized (launchLock) {") < escalate.index('"stop\\n"')


def test_stop_sets_token_and_latch_synchronously_then_escalates_in_order():
    """§4: (0) cancel token + nav latch on the calling thread; (1) tether stop;
    (2) emma_nav.py stop (SIGINT via run.pid); (3) after 2000 ms kill -9 the
    controller; (4) only then DELETE /actions/:current -- a DELETE while
    drive_route lives reads as a link drop and it REDOES the step."""
    nav = code_only(read(NAV))
    stop = method(nav, "boolean stop(String reason, boolean explicit)")
    token = stop.index("cancelled.set(")
    latch = stop.index("latchedUntil = SystemClock.elapsedRealtime() + NAV_STOP_LATCH_MS;")
    queued = stop.index("navStopper.execute(() -> escalate(current, explicit || wasActive));")
    assert token < queued and latch < queued
    assert "runArmWork" not in stop and "io." not in stop
    esc = method(nav, "private void escalate(Run current, boolean full)")
    order = [esc.index('"stop\\n"'), esc.index("runShell(STOP_SCRIPT, SHELL_TIMEOUT_MS);"),
             esc.index("SystemClock.sleep(STOP_KILL_AFTER_MS);"), esc.index("runShell(KILL_SCRIPT, SHELL_TIMEOUT_MS);"),
             esc.index("endLauncher(current, tether);"), esc.index("host.deleteBoardAction();")]
    assert order == sorted(order), order
    assert "kill -9" in statement(nav, "KILL_SCRIPT =")
    assert " stop; else " in statement(nav, "STOP_SCRIPT =")
    # a reused pid is never signalled: only a live drive_route
    assert "grep -q drive_route" in statement(nav, "RUN_PID_IS_DRIVE_ROUTE =")


def test_reap_runs_on_start_and_never_stops_a_pc_run():
    nav = code_only(read(NAV))
    assert '" reap"' in statement(nav, "REAP_SCRIPT =")
    assert "runShell(REAP_SCRIPT, SHELL_TIMEOUT_MS, true)" in method(nav, "void reapOnStart()")
    assert "nav.reapOnStart();" in method(code_only(read(MAIN)), "protected void onCreate(Bundle savedInstanceState)")


def test_dedicated_threads_exist_and_never_submit_to_io():
    nav = code_only(read(NAV))
    for name in ("nav-reader", "nav-pinger", "nav-stopper", "nav-poller"):
        assert f'"{name}"' in nav, name
    # io is reachable only through Host.runArmWork, and only go() uses it
    assert nav.count("runArmWork(") == 2          # the interface method and the one call in go()
    assert "host.runArmWork(() -> launch(next));" in method(nav, "synchronized String go(String place, String origin)")
    for sig in ("private void escalate(Run current, boolean full)", "private void ping(Run which)",
                "private void readTether(Run which)", "private void poll()", "void refreshPlaces()",
                "void check(Runnable done)", "void reapOnStart()", "boolean stop(String reason, boolean explicit)"):
        assert "runArmWork" not in method(nav, sig), sig
    assert "next.ping = navPinger.scheduleWithFixedDelay(() -> ping(next), 0, PING_MS" in nav
    # per run: an old run's reader must not cancel a new run's pings
    assert "stopPinging(which);" in method(nav, "private void readTether(Run which)")
    assert "ScheduledFuture<?> task = which.ping;" in method(nav, "private static void stopPinging(Run which)")
    assert "new Thread(() -> readTether(next), \"nav-reader\")" in nav
    body = code_only(read(BODY))
    assert "io." not in method(body, "void stop(String reason)")
    assert "io." not in method(body, "void halt()")


def test_a_walk_and_a_gesture_exclude_each_other():
    nav = code_only(read(NAV))
    block = method(nav, "String bodyBlock()")
    assert 'if (ownRunActive() || polledActive()) return "navigating";' in block
    assert 'if (!pollFresh()) return "nav_unknown";' in block
    # a PC robot_python run (shares run.pid) is seen even before emma_nav is deployed
    assert '\\"origin\\":\\"pc\\"' in statement(nav, "POLL_SCRIPT =")
    refusal = method(nav, "private String goRefusal(String place, String origin)")
    order = ["disabled", "not_installed", "operator_screen", "not_foreground", "nav_latched",
             "run_active", "body_latched", "body_busy"]
    at = [refusal.index('"%s"' % code) for code in order]
    assert at == sorted(at)
    for code in order + ["unknown_place", "leg_unverified", "no_leg", "arms_not_rest", "launcher_failed",
                         "launcher_lost", "stop_pressed", "bad_request"]:
        assert code in rc.NAV_REASONS, code


def test_a_stale_places_file_is_refused_as_stale_not_as_an_unknown_place():
    """Review #8 (Java half): the launcher sends places=[] plus a top-level
    reason when its places file is stale. The voice go must answer with that
    reason before it scans the empty list for the place id."""
    refusal = method(code_only(read(NAV)), "private String goRefusal(String place, String origin)")
    stale = refusal.index('"places_stale".equals(cached.optString("reason", ""))')
    assert refusal.index('return "places_stale";') > stale
    assert stale < refusal.index("for (int i = 0; i < list.length(); i++)")
    assert stale < refusal.index('return "unknown_place";')
    assert "places_stale" in rc.NAV_REASONS


def test_a_run_given_up_on_is_not_revived_by_its_launchers_late_line():
    """Review #29, integrator's check: endLauncher finishes a hung launcher's
    Run as stopped; a late tether line must not set it back to running."""
    nav = code_only(read(NAV))
    merge = method(nav, "private void merge(Run which, JSONObject status)")
    assert "synchronized (which)" in merge
    assert merge.index("synchronized (which)") < merge.index("if (which.terminal()) return;")
    assert merge.index("if (which.terminal()) return;") < merge.index("which.status = status;")


def test_the_operator_screen_waits_out_the_whole_escalation():
    """Review #29 lengthened escalate; the operator screen's wait before it
    connects the SDK must cover every step of it, not the old 6 s."""
    nav = code_only(read(NAV))
    budget = statement(nav, "static final long STOP_DRAIN_MS =")
    for part in ("STOP_KILL_AFTER_MS", "LAUNCHER_EXIT_GRACE_MS", "LAUNCHER_GIVE_UP_MS",
                 "3 * SHELL_TIMEOUT_MS"):
        assert part in budget, part
    escalate = method(nav, "private void escalate(Run current, boolean full)")
    end = method(nav, "private void endLauncher(Run current, Process tether)")
    assert (escalate + end).count("runShell(") == 3     # the 3 shells the budget counts
    refresh = method(code_only(read(MAIN)), "private void refreshBaseStatus()")
    assert "nav.awaitStopped(NavLauncher.STOP_DRAIN_MS);" in refresh


def test_every_nav_state_and_reason_the_app_writes_is_a_contract_code():
    nav = code_only(read(NAV))
    states = set(re.findall(r'(?:setStatus|finish)\(\w+, "([a-z_]+)"', nav))
    assert states and states <= set(rc.NAV_STATES), states
    reasons = set(re.findall(r'(?:finish)\(\w+, "[a-z_]+", "([a-z_]+)"\)', nav))
    reasons |= set(re.findall(r'return "([a-z_]+)";', method(nav, "private String goRefusal(String place, String origin)")))
    assert reasons <= set(rc.NAV_REASONS), reasons - set(rc.NAV_REASONS)
    main = code_only(read(MAIN))
    for reason in set(re.findall(r'stopBodyAndNav\((?:null|"[a-z_]+"), "([a-z_]+)"', main)):
        assert reason in rc.NAV_REASONS, reason
    for reason in set(re.findall(r'stopBodyAndNav\("([a-z_]+)"', main)):
        assert reason in rc.BODY_REASONS, reason


# ---- MainActivity: stop paths, SafetyBar, operator screen --------------------

def test_every_stop_path_stops_both_the_walk_and_the_arm():
    main = code_only(read(MAIN))
    both = method(main, "private boolean stopBodyAndNav(String bodyReason, String navReason, boolean explicit)")
    assert both.index("nav.stop(navReason, explicit);") < both.index("body.stop(bodyReason);")
    dispatch = main[main.index("public boolean dispatchKeyEvent"):main.index("return super.dispatchKeyEvent")]
    b = dispatch[dispatch.index("KEYCODE_BUTTON_B"):]
    assert b.index('stopBodyAndNav(null, "stop_pressed", true);') < b.index("return true;")
    assert dispatch.index("KEYCODE_BUTTON_B") < dispatch.index("if (operatorVisible) {")
    paths = {
        "protected void onPause()": 'stopBodyAndNav("not_foreground", "not_foreground", false);',
        "public void onWindowFocusChanged(boolean hasFocus)": 'stopBodyAndNav("not_foreground", "not_foreground", false);',
        "protected void onDestroy()": 'stopBodyAndNav("not_foreground", "app_lost", false);',
        "private void showOperator()": 'stopBodyAndNav("operator_screen", "operator_screen", false);',
        "@Override public String bodyStop()": 'stopBodyAndNav(null, "stop_pressed", true);',
        "@Override public String navStop()": 'stopBodyAndNav(null, "stop_pressed", true);',
        # (the SafetyBar's tap was a stop path too, until the owner removed the bar, 10 Oct 2026)
        "@Override public void pageLost()": 'stopBodyAndNav("not_foreground", "app_lost", false);',
    }
    for sig, call in paths.items():
        assert call in method(main, sig), sig
    stop_button = main[main.index('Button stop = button("■ หยุดและล็อกทันที (B)"'):]
    stop_button = stop_button[:stop_button.index("root.addView(stop")]
    assert 'stopBodyAndNav(null, "stop_pressed", true);' in stop_button
    # the operator screen connects the SDK only after the walk it interrupted was stopped
    show = method(main, "private void showOperator()")
    assert show.index("stopBodyAndNav(") < show.index("refreshBaseStatus();")
    refresh = method(main, "private void refreshBaseStatus()")
    assert refresh.index("nav.awaitStopped(") < refresh.index("ensureSdkConnected();")


def test_there_is_no_safety_bar_any_more():
    """Owner, 10 Oct 2026: the arm bar first ("เอาปุ่มหยุดแขนออก ไม่จำเป็น"), then
    the walk bar too ("ปุ่มแดงๆ ตรงนี้เอาออกได้ไหม" -> "เอาออกทั้งหมด", told the
    other ways to stop). No red view over the face, ever; the stops it used to
    share remain: the operator STOP, gamepad B, the app pausing."""
    main = code_only(read(MAIN))
    assert "buildSafetyBar" not in main and "safetyBar" not in main and "SAFETY_BAR_HEIGHT" not in main
    soon = method(main, "private void publishBodyNavStatusSoon()")
    assert "publishBodyNavStatus" in soon
    focus = method(main, "public void onWindowFocusChanged(boolean hasFocus)")
    assert 'stopBodyAndNav("not_foreground", "not_foreground", false);' in focus


def test_operator_screen_additions():
    main = read(MAIN)
    for text in ("สั่งท่าทางด้วยเสียง: ", "เปิดท่าที่ยังไม่ทดสอบทิศ: ", "ฟังก์ชันนำทาง: ", "เก็บแขนก่อนเดิน: ",
                 "ใช้กับท่าเสียง: ", "บันทึกร่างนี้เป็นท่า…", "ล้างการล็อกท่าทาง", "ล้างการล็อกการเดิน",
                 "ตรวจระบบนำทาง (ไม่ขยับ)", "บันทึก face token",
                 "แขนซ้ายของหุ่น (ขวามือเราเมื่อยืนหน้าหุ่น)"):
        assert text in main, text
    code = code_only(main)
    # the pose is saved only through BodyController -> BodyPoseStore -> checkSave,
    # with "played since the last edit" from the draft itself
    assert "body.savePose(id, motionDraft.frames(), motionDraft.playedSinceEdit());" in code
    draft = code_only(read(DRAFT))
    assert "playedSinceEdit = false;" in method(draft, "ArmController.PoseFrame captureFrame()")
    assert "playedSinceEdit = false;" in method(draft, "void clear()")
    # review2 #22: only a playback that ran to the end counts (its onDone)
    play = method(code, "private void playDraftMotion()")
    assert play.index("final int editsAtStart = motionDraft.startPlayback();") \
        < play.index("motionDraft.markPlayed(editsAtStart);")
    # robot-side switches: walking OFF by default, the arm rest ON; gestures by
    # voice ON unless the operator turned them off (owner, 13 Oct)
    body = code_only(read(BODY))
    nav = code_only(read(NAV))
    assert "prefs.getBoolean(PREF_ENABLED, true)" in body and "prefs.getBoolean(PREF_ENABLED, false)" in nav
    assert "prefs.getBoolean(PREF_ALLOW_DIRECTION_KNOWN, false)" in body
    assert 'PREFS = "emma_body_nav";' in body and "PREFS = BodyController.PREFS;" in nav
    for key in ('"body_enabled"', '"allow_direction_known"', '"voice_channels"', '"nav_enabled"',
                '"nav_require_arm_rest"'):
        assert key in body + nav, key
    assert 'FACE_TOKEN = "face_token";' in code
    # the check runs the read-only launcher command and shows deployed_at
    check = method(nav, "void check(Runnable done)")
    assert "runShell(CHECK_SCRIPT" in check and '"deployed_at"' in check
    assert '" check"' in statement(nav, "CHECK_SCRIPT =")


def test_the_face_token_comes_from_native_never_from_a_url():
    main = code_only(read(MAIN))
    token = method(main, "@Override public String faceToken()")
    assert "getString(FACE_TOKEN, \"\")" in token
    save = main[main.index('Button saveToken = button("บันทึก face token"'):]
    save = save[:save.index("root.addView(saveToken")]
    assert 'faceTokenInput.setText("");' in save          # never shown back
    face = code_only(read(FACE))
    assert "face_token" not in face and "faceToken" not in face.replace("bridge.faceToken()", "") \
        .replace("String faceToken();", "")


def test_no_new_permission_and_no_javascript_interface():
    """§5 P4 must-not-change: the manifest permissions; the bridge stays on
    window.prompt (no addJavascriptInterface anywhere in the app)."""
    manifest = read(MANIFEST)
    assert set(re.findall(r'<uses-permission android:name="([^"]+)"', manifest)) == {
        "android.permission.INTERNET", "android.permission.ACCESS_NETWORK_STATE",
        "android.permission.RECORD_AUDIO", "android.permission.MODIFY_AUDIO_SETTINGS",
        "android.permission.CAMERA"}
    for path in SRC.glob("*.java"):
        assert "addJavascriptInterface" not in code_only(read(path)), path.name


# ---- review fixes 2026-10-09 (android) ---------------------------------------

AGF = SRC / "AgfFramer.java"
RULES = SRC / "NavRules.java"
ROOTSHELL = SRC / "RootShell.java"


def blocks(text: str, opener: str) -> list[tuple[int, int]]:
    """(start, end) of every `opener {...}` block, by brace matching."""
    out, at = [], 0
    while True:
        at = text.find(opener, at)
        if at < 0:
            return out
        open_at = text.index("{", at)
        depth = 0
        for i in range(open_at, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    out.append((open_at + 1, i))
                    break
        at = open_at + 1


def gate_of(text: str, pos: int) -> str | None:
    """The body of the `synchronized (armGate)` block around `pos`, if any."""
    for start, end in blocks(text, "synchronized (armGate)"):
        if start <= pos < end:
            return text[start:end]
    return None


ARM_MOTION = re.compile(r"\barm\.(playGroup|resetHome|playFrames)\(")


def test_a_stop_cannot_land_between_the_gate_check_and_the_arm_command():
    """Review #3: play() checked stale(gen) on io and only then sent the arm
    command, so a STOP in between was acknowledged (idle, SafetyBar gone) and
    the whole gesture still played. Every arm motion call in BodyController is
    now inside `synchronized (armGate)`, right behind its generation check, and
    every stop bumps the generation under the same lock."""
    body = code_only(read(BODY))
    calls = [m.start() for m in ARM_MOTION.finditer(body)]
    assert len(calls) >= 5, calls          # group, rest, frames, rest-return, rest-before-run
    for pos in calls:
        gate = gate_of(body, pos)
        assert gate is not None, body[pos - 200:pos + 40]
        first = gate.strip().splitlines()[0].strip()
        assert first in ("if (stale(gen)) return;",
                         "if (gen != generation.get() || latched()) return;",
                         "if (gen != generation.get() || latched()) return false;"), first
    void = method(body, "private void voidEverything(String reason)")
    bump = void.index("generation.incrementAndGet();")
    assert gate_of(void, bump) is not None
    # the rest branch moves phase only inside the gate, after the check
    play = method(body, "private void play(String id, int gen, List<BodyGestures.Frame> frames)")
    rest_phase = play.index('phase = "rest_return";')
    assert gate_of(play, rest_phase) is not None
    # a frame run also stops on the OWNER's generation, not only poseGeneration
    assert "arm.playFrames(poseFrames, () -> gen == generation.get() && !latched()," in play
    arm = code_only(read(ARM))
    loop = method(arm, PLAY_FRAMES)
    assert "if (run != poseGeneration.get() || !ownerCurrent(stillCurrent)) return;" in loop
    # the SDK's unordered write threads: a second #STOP, skipped once the arm has a new owner
    stopper = method(body, "private void stopArm()")
    lam = stopper[stopper.index("bodyStopper.execute(() -> {"):]
    assert lam.index("arm.stopAll();") < lam.index("SystemClock.sleep(STOP_REPEAT_MS);") \
        < lam.index("if (generation.get() == stoppedAt && !busy.get()) arm.stopAll();")
    # and every command is followed by the overtaken check
    assert play.count("stopIfOvertaken(gen);") == 3
    assert "stopIfOvertaken(gen);" in method(body, "private void restReturn(int gen, String why)")
    assert "if (gen != generation.get() || latched()) stopArm();" in method(body, "private void stopIfOvertaken(int gen)")


def test_a_frame_gesture_whose_board_drops_off_still_ends():
    """Review #13: playFrames returned silently when the board disconnected,
    so busy, the SafetyBar and every gate stayed stuck until a STOP (which
    then latched body AND nav for 60 s)."""
    arm = code_only(read(ARM))
    loop = method(arm, PLAY_FRAMES)
    # review2 #10: "dropped off" is linkUp(), not the SDK flag a dead CH340 never clears
    lost = loop.index("if (!linkUp()) {")
    assert loop.index("abort.run();", lost) < loop.index("return;", lost)
    assert "if (onAbort != null && run == poseGeneration.get() && ended.compareAndSet(false, true)) onAbort.run();" \
        in loop
    assert "|| !armManager.isConnected()) return;" not in loop     # the silent exit is gone
    body = code_only(read(BODY))
    play = method(body, "private void play(String id, int gen, List<BodyGestures.Frame> frames)")
    call = play[play.index("arm.playFrames(poseFrames,"):]
    call = call[:call.index("});\n") + 3]
    abort = call[call.rindex("}, () -> {"):]
    assert 'finish(gen, "failed", "arm_unavailable");' in abort
    # the backstop timer, scheduled before the run starts, ends only a gesture still "running"
    watchdog = play.index("later(gen, () -> {")
    assert watchdog < play.index("arm.playFrames(poseFrames,")
    timer = play[watchdog:play.index("FRAMES_WATCHDOG_SLACK_MS);", watchdog)]
    assert 'if (attempt == attempts.get() && "running".equals(phase)) finish(gen, "failed", "arm_unavailable");' in timer
    assert "BodyGestures.totalMs(sequence) + BodyGestures.REST_AFTER_FRAMES_MS + " in timer
    # a finished gesture's leftover timers never fire into the next one
    assert "ui.removeCallbacksAndMessages(TIMERS);" in method(body, "private synchronized void finish(int gen, String state, String reason)")
    # and a real loss of the arm link ends a gesture without a latch
    main = code_only(read(MAIN))
    gone = method(main, "@Override public void onArmDisconnected()")
    assert "if (wasUp && body != null) body.halt();" in gone
    assert "armLinkUp = true;" in method(main, "@Override public void onArmConnected(String port)")


def test_a_dead_webview_renderer_does_not_take_the_app_down():
    """Review #14: no onRenderProcessGone = the default false = a renderer
    crash or OOM kill took the Activity (and the native SafetyBar) with it."""
    face = code_only(read(FACE))
    gone = method(face, "public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail)")
    assert "recoverFromRendererDeath(view, detail != null && detail.didCrash());" in gone
    assert gone.strip().endswith("return true;")
    rec = method(face, "private void recoverFromRendererDeath(WebView dead, boolean crashed)")
    order = [rec.index("bridge.pageLost();"), rec.index("parent.removeView(dead);"), rec.index("dead.destroy();"),
             rec.index("root.addView(newWebView(), index,"), rec.index("openOrSetup();")]
    assert order == sorted(order), order
    # repeated deaths stop the loop and say so; a hidden/paused face loads on resume
    assert "if (rendererDeaths.size() >= RENDERER_DEATHS_MAX) {" in rec
    assert "RENDERER_DEATHS_MAX = 3;" in face and "RENDERER_DEATH_WINDOW_MS = 60000L;" in face
    assert "if (pagePaused || root.getVisibility() != View.VISIBLE) {" in rec
    assert "openOrSetup();" in method(face, "void onActivityResume()")
    # one place makes a face WebView, so the rebuilt one keeps the gate and the hold
    assert face.count("new WebView(") == 1
    made = method(face, "private WebView newWebView()")
    assert "configureWebView();" in made and "trackSetupHold(v, event);" in made
    # the page that died owned whatever moved: stopped as loss of control, not as a pressed STOP
    main = code_only(read(MAIN))
    assert method(main, "@Override public void pageLost()").strip() == \
        'stopBodyAndNav("not_foreground", "app_lost", false);'


def test_b_and_the_stop_button_leave_a_live_walks_delete_to_the_escalation():
    """Review #21: B and the operator STOP queued DELETE /actions/:current at
    once, racing the tether stop; drive_route renewed the deleted action. With
    a walk live the DELETE is NavLauncher.escalate's step 4 only."""
    main = code_only(read(MAIN))
    dispatch = main[main.index("public boolean dispatchKeyEvent"):main.index("return super.dispatchKeyEvent")]
    b = dispatch[dispatch.index("KEYCODE_BUTTON_B"):]
    assert b.index('boolean navLive = stopBodyAndNav(null, "stop_pressed", true);') \
        < b.index('stopAndDisarm("ปุ่ม B — หยุดและล็อกแล้ว", true, !navLive);')
    button = main[main.index('Button stop = button("■ หยุดและล็อกทันที (B)"'):]
    button = button[:button.index("root.addView(stop")]
    assert button.index('boolean navLive = stopBodyAndNav(null, "stop_pressed", true);') \
        < button.index('stopAndDisarm("หยุดและล็อกแล้ว", true, !navLive);')
    disarm = method(main, "private void stopAndDisarm(String reason, boolean explicit, boolean deleteNow)")
    queued = disarm[disarm.index("io.execute(() -> {"):]
    assert "if (deleteNow) cancelCurrent(explicit);" in queued and "else cancelSdkMove();" in queued
    assert disarm.count("cancelCurrent(") == 1
    assert "stopAndDisarm(reason, explicit, true);" in method(main, "private void stopAndDisarm(String reason, boolean explicit)")
    both = method(main, "private boolean stopBodyAndNav(String bodyReason, String navReason, boolean explicit)")
    assert "boolean navWasActive = nav != null && nav.stop(navReason, explicit);" in both
    assert "return navWasActive;" in both
    nav = code_only(read(NAV))
    stop = method(nav, "boolean stop(String reason, boolean explicit)")
    assert "final boolean wasActive = (current != null && current.active()) || polledActive();" in stop
    assert stop.rstrip().endswith("return wasActive;")
    # a live walk always gets a full escalation, which DELETEs last
    assert "escalate(current, explicit || wasActive)" in stop


def test_the_launcher_is_waited_for_then_killed_through_su_then_given_up_on():
    """Reviews #28/#29: destroyForcibly() on the su'd root launcher gets EPERM
    and is swallowed, so a launcher that ignored "stop" was never ended and its
    Run stayed "running" (SafetyBar pinned, gestures and go refused)."""
    nav = code_only(read(NAV))
    esc = method(nav, "private void escalate(Run current, boolean full)")
    assert esc.index("runShell(KILL_SCRIPT, SHELL_TIMEOUT_MS);") < esc.index("endLauncher(current, tether);") \
        < esc.index("host.deleteBoardAction();")
    end = method(nav, "private void endLauncher(Run current, Process tether)")
    order = [end.index("if (waitExit(tether, LAUNCHER_EXIT_GRACE_MS)) return;"),
             end.index("runShell(NavRules.killLauncherScript(pid), SHELL_TIMEOUT_MS);"),
             end.index("tether.destroyForcibly();"),
             end.index("if (waitExit(tether, LAUNCHER_GIVE_UP_MS)) return;"),
             end.index("stopPinging(current);"),
             end.index('finish(current, "stopped", cancelReason);')]
    assert order == sorted(order), order
    # the pid comes from the launcher's own status lines
    merge = method(nav, "private void merge(Run which, JSONObject status)")
    assert 'int pid = status.optInt("launcher_pid", 0);' in merge and "if (pid > 1) which.launcherPid = pid;" in merge


def test_the_ping_is_registered_before_the_reader_can_cancel_it():
    """Review #31: a launcher that ends at once let the reader reach
    stopPinging before `next.ping` existed; the ping then ran for ever."""
    nav = code_only(read(NAV))
    launch = method(nav, "private void launch(Run next)")
    assert launch.index("next.ping = navPinger.scheduleWithFixedDelay(") < launch.index("reader.start();")


def test_su_calls_on_io_are_bounded_and_stop_clears_a_run_with_no_tether():
    """Review #32: an unbounded waitFor() on io left an accepted walk in
    "preparing" for ever, and STOP did not clear it."""
    shell = code_only(read(ROOTSHELL))
    run = method(shell, "static Result run(String command, long timeoutMs)")
    assert "exited = process.waitFor(timeoutMs, TimeUnit.MILLISECONDS);" in run
    assert run.index("if (!exited) {") < run.index("throw new IllegalStateException(")
    assert run.index("process.getOutputStream().close();") < run.index("process.waitFor(")
    for path in (MAIN, ARM, ROOTSHELL):
        assert ".waitFor()" not in code_only(read(path)), path.name
    main = code_only(read(MAIN))
    assert "RootShell.run(\"am force-stop \" + VENDOR_PACKAGE, RootShell.TIMEOUT_MS);" in \
        method(main, "private void stopConflictingVendorApp()")
    assert "RootShell.run(command, RootShell.TIMEOUT_MS);" in method(code_only(read(ARM)), "private static void runRoot(String command)")
    nav = code_only(read(NAV))
    stop = method(nav, "boolean stop(String reason, boolean explicit)")
    locked = stop[stop.index("synchronized (launchLock) {"):]
    assert locked.index("if (current.stdin == null && current.tether == null) {") \
        < locked.index('finish(current, "stopped", cancelReason);')
    assert stop.index("synchronized (launchLock) {") < stop.index("navStopper.execute(")
    # ...and a launch that was blocked meanwhile cannot revive the stopped run
    launch = method(nav, "private void launch(Run next)")
    starting = launch[launch.index("synchronized (next) {"):]
    assert starting.index("if (next.terminal()) return;") < starting.index('setStatus(next, "starting", null);')


def test_the_agf_framer_and_the_nav_rules_are_wired_in():
    """The pure pieces the harness below runs are the ones the app calls."""
    arm = code_only(read(ARM))
    rx = method(arm, "public void onDataReceived(byte[] data, int length)")
    assert "boolean finished = agfIn(chunk);" in rx and "if (finished) listener.onArmFinished();" in rx
    assert "return agf.feed(chunk);" in method(arm, "private boolean agfIn(String chunk)")
    assert "clearRxTail();" in method(arm, "public void onDisconnected()")
    assert "clearRxTail();" in method(arm, "void playGroup(int group)")
    assert "clearRxTail();" in method(arm, "void resetHome()")
    nav = code_only(read(NAV))
    assert "NavRules.ownStatusWins(" in method(nav, "String status()")
    assert "NavRules.stopReason(" in method(nav, "private void merge(Run which, JSONObject status)")
    for path in (AGF, RULES):
        text = code_only(read(path))
        assert "import android" not in text and "org.json" not in text, path.name


NAV_HARNESS = r"""
package com.emma.robot.control;

import java.io.FileDescriptor;
import java.io.FileOutputStream;
import java.io.PrintStream;

public final class NavHarness {
    static PrintStream out;
    static void p(String k, Object v) { out.println(k + "=" + v); }
    static String feed(AgfFramer f, String... chunks) {
        StringBuilder s = new StringBuilder();
        for (String c : chunks) s.append(f.feed(c) ? "T" : "f");
        return s.toString();
    }
    static String kill(int pid) {
        try { return NavRules.killLauncherScript(pid); } catch (IllegalArgumentException e) { return "refused"; }
    }

    public static void main(String[] a) throws Exception {
        out = new PrintStream(new FileOutputStream(FileDescriptor.out), true, "UTF-8");
        p("agf.whole", feed(new AgfFramer(), "#CC#AGF\r\n"));
        p("agf.split2", feed(new AgfFramer(), "#CC#A", "GF\r\n"));
        p("agf.split3", feed(new AgfFramer(), "#", "AG", "F"));
        p("agf.split_each", feed(new AgfFramer(), "#", "A", "G", "F", "#", "A", "G", "F"));
        p("agf.noise", feed(new AgfFramer(), "#CC", "#CC", "\r\n"));
        AgfFramer r = new AgfFramer();
        r.feed("#A");
        r.reset();
        p("agf.reset", feed(r, "GF"));
        // ownStatusWins(ownHasStatus, ownActive, pollActive, ownRunId, polledRunId)
        p("own.terminal_poll_same_running", NavRules.ownStatusWins(true, false, true, "aa", "aa"));
        p("own.terminal_poll_pc", NavRules.ownStatusWins(true, false, true, "aa", null));
        p("own.terminal_poll_other", NavRules.ownStatusWins(true, false, true, "aa", "bb"));
        p("own.terminal_poll_idle", NavRules.ownStatusWins(true, false, false, "aa", "bb"));
        p("own.active_poll_other", NavRules.ownStatusWins(true, true, true, "aa", "bb"));
        p("own.none", NavRules.ownStatusWins(false, false, true, null, "bb"));
        // stopReason(state, reason, cancelledByApp, appReason)
        for (String why : new String[]{"not_foreground", "operator_screen", "app_lost", "stop_pressed", "bogus"}) {
            p("reason." + why, NavRules.stopReason("stopped", "stop_pressed", true, why));
        }
        p("reason.not_cancelled", NavRules.stopReason("stopped", "stop_pressed", false, "app_lost"));
        p("reason.failed", NavRules.stopReason("failed", "error", true, "app_lost"));
        p("reason.arrived", NavRules.stopReason("arrived", null, true, "app_lost"));
        p("reason.other_stop", NavRules.stopReason("stopped", "obstacle", true, "app_lost"));
        p("kill.1234", kill(1234));
        p("kill.1", kill(1));
        p("kill.0", kill(0));
        p("kill.neg", kill(-7));
    }
}
"""


@pytest.fixture(scope="module")
def nav_harness(tmp_path_factory) -> dict[str, str]:
    tools = _javac()
    if tools is None:
        pytest.skip("no JDK found: AgfFramer and NavRules were NOT compiled or run -- #AGF framing, the "
                    "status choice, the stop reason and the launcher kill script are unchecked beyond "
                    "the source-text tests")
    javac, java = tools
    work = tmp_path_factory.mktemp("navrules")
    src = work / "src"
    src.mkdir()
    (src / "NavHarness.java").write_text(NAV_HARNESS, encoding="utf-8")
    out = work / "out"
    subprocess.run([javac, "-encoding", "UTF-8", "-d", str(out), str(AGF), str(RULES), str(src / "NavHarness.java")],
                   check=True, capture_output=True, encoding="utf-8", timeout=120)
    run = subprocess.run([java, "-cp", str(out), "com.emma.robot.control.NavHarness"],
                         check=True, capture_output=True, encoding="utf-8", timeout=60)
    values = {}
    for line in run.stdout.splitlines():
        key, _, value = line.partition("=")
        values[key] = value
    return values


def test_compiled_agf_is_found_however_the_serial_reads_split_it(nav_harness):
    """Review #30: '#A' | 'GF' in two reads was missed; the handshake then
    waited the 30 s fallback and a walk's rest refused (arms_not_rest)."""
    assert nav_harness["agf.whole"] == "T"
    assert nav_harness["agf.split2"] == "fT"
    assert nav_harness["agf.split3"] == "ffT"
    assert nav_harness["agf.split_each"] == "fffTfffT"     # once per frame, never twice
    assert nav_harness["agf.noise"] == "fff"
    assert nav_harness["agf.reset"] == "f"                 # a stale prefix never joins a later read


def test_compiled_status_prefers_the_apps_own_finished_run_over_a_stale_poll_of_it(nav_harness):
    """Review #24: a poll of the SAME run, up to 5 s old, used to win while it
    still said running -- arrival reached the page/server 5-12 s late."""
    assert nav_harness["own.terminal_poll_same_running"] == "true"
    assert nav_harness["own.terminal_poll_pc"] == "false"        # a PC run is still seen
    assert nav_harness["own.terminal_poll_other"] == "false"
    assert nav_harness["own.terminal_poll_idle"] == "true"
    assert nav_harness["own.active_poll_other"] == "true"
    assert nav_harness["own.none"] == "false"


def test_compiled_stop_reason_keeps_the_apps_own_reason(nav_harness):
    """Review #26: background / operator screen / app closing were all
    reported as stop_pressed ("กดหยุดแล้ว") once the launcher ran."""
    for why in ("not_foreground", "operator_screen", "app_lost"):
        assert nav_harness["reason." + why] == why
        assert why in rc.NAV_REASONS
    assert nav_harness["reason.stop_pressed"] == "stop_pressed"
    assert nav_harness["reason.bogus"] == "stop_pressed"            # only the whitelist
    assert nav_harness["reason.not_cancelled"] == "stop_pressed"
    assert nav_harness["reason.failed"] == "error"
    assert nav_harness["reason.arrived"] == "null"
    assert nav_harness["reason.other_stop"] == "obstacle"


def test_compiled_launcher_kill_script_takes_only_a_real_pid_and_checks_it(nav_harness):
    """Review #29: the kill goes through su (the app uid cannot signal root),
    built from constants plus a validated int, and only for an emma_nav pid."""
    assert nav_harness["kill.1234"] == ("p=1234; kill -0 \"$p\" 2>/dev/null && tr '\\0' ' ' < /proc/$p/cmdline"
                                        " | grep -q emma_nav && kill -9 \"$p\"; exit 0")
    for bad in ("kill.1", "kill.0", "kill.neg"):
        assert nav_harness[bad] == "refused", bad


def test_the_face_is_full_screen_and_staff_can_still_swipe_the_bars_in():
    """Owner, 10 Oct 2026 ("uxui ของแอพให้ดูดีที่สุด"): the Android bars sat over
    the face's brand and mic. Hidden on every resume and every regained focus
    (a dialog or the shade brings them back), transient on a swipe -- and the
    focus-loss stop is untouched."""
    main = code_only(read(MAIN))
    bars = method(main, "private void hideSystemBars()")
    assert "bars.hide(WindowInsets.Type.systemBars());" in bars
    assert "BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE" in bars
    assert "SYSTEM_UI_FLAG_IMMERSIVE_STICKY" in bars          # minSdk 26 path
    assert "hideSystemBars();" in method(main, "protected void onResume()")
    focus = method(main, "public void onWindowFocusChanged(boolean hasFocus)")
    assert "if (hasFocus) hideSystemBars();" in focus
    assert 'stopBodyAndNav("not_foreground", "not_foreground", false);' in focus
