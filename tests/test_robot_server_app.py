"""The face app starts the Emma server that runs ON the robot (9 Oct 2026):
ServerLauncher + ServerRules in apps/emma-robot-android, and the face's wait
for a local server that is still booting (EmmaFace).

Two kinds of check, because the APK cannot run here (the NavRules pattern):
`ServerRules.java` has no android.* and no org.json, so it is COMPILED with the
plain JDK and RUN; the rest are source contracts read from the Java. Without
a JDK the compiled part skips and says what went unchecked.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tests.test_emma_robot_body_nav_app import _javac, code_only, method, read

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "apps/emma-robot-android/app/src/main/java/com/emma/robot/control"
RULES = SRC / "ServerRules.java"
LAUNCHER = SRC / "ServerLauncher.java"
NAV = SRC / "NavLauncher.java"
NAV_RULES = SRC / "NavRules.java"
MAIN = SRC / "MainActivity.java"
FACE = SRC / "EmmaFace.java"


HARNESS = r"""
package com.emma.robot.control;

import java.io.FileDescriptor;
import java.io.FileOutputStream;
import java.io.PrintStream;
import java.util.Arrays;

public final class ServerHarness {
    static PrintStream out;
    static void p(String k, Object v) { out.println(k + "=" + v); }
    static String script(String dns) {
        try { return ServerRules.startScript(dns); } catch (IllegalArgumentException e) { return "refused"; }
    }
    static void w(String key, ServerRules.Watch w) {
        p(key + ".state", w.state);
        p(key + ".detail", w.detail);
    }

    public static void main(String[] a) throws Exception {
        out = new PrintStream(new FileOutputStream(FileDescriptor.out), true, "UTF-8");
        for (String h : new String[]{"127.0.0.1", "localhost", "LOCALHOST", " localhost ", "192.168.1.43",
                "127.0.0.1.evil.example", "emma-pc.local", ""}) {
            p("local." + h.trim(), ServerRules.localServer(h));
        }
        p("local.null", ServerRules.localServer(null));
        p("dns", ServerRules.dnsList(Arrays.asList("192.168.1.1", "192.168.1.1", "fe80::1", "1.2.3.4;reboot",
                "256.1.1.1", "8.8.8.8", null, " 9.9.9.9 ", "1.1.1.1")));
        p("dns.none", "[" + ServerRules.dnsList(null) + "]");
        p("script", script("192.168.1.1,8.8.8.8"));
        p("script.empty", script(""));
        p("script.inject", script("1.1.1.1'; reboot; '"));
        for (int i = 0; i <= 6; i++) p("retry." + i, ServerRules.pageRetryDelayMs(i));
        for (int i = 0; i <= 7; i++) p("backoff." + i, ServerRules.restartBackoffMs(i));

        ServerRules.Watch boot = new ServerRules.Watch();
        boot.observe(0, "started 4242\n", false, 0L);
        w("boot.1", boot);
        boot.observe(0, "running 4242\n", false, 30000L);
        w("boot.2", boot);
        boot.observe(0, "running 4242\n", false, 100000L);
        w("boot.3", boot);
        boot.observe(0, "running 4242\n", true, 130000L);
        w("boot.4", boot);
        p("boot.4.line", boot.label());

        ServerRules.Watch crash = new ServerRules.Watch();
        crash.observe(0, "started 1\n", false, 0L);
        crash.observe(0, "started 2\n", false, 30000L);
        w("crash.2", crash);
        p("crash.2.may_now", crash.mayStart(30001L));
        p("crash.2.may_later", crash.mayStart(60000L));
        crash.observe(0, "started 3\n", false, 60000L);
        p("crash.3.may", crash.mayStart(100000L) + "," + crash.mayStart(120000L));
        crash.observe(0, "running 3\n", true, 120000L);
        w("crash.ok", crash);
        p("crash.ok.may", crash.mayStart(120001L));

        ServerRules.Watch taken = new ServerRules.Watch();
        taken.observe(0, "started 9\n", true, 0L);
        w("taken", taken);

        ServerRules.Watch missing = new ServerRules.Watch();
        missing.observe(86, "", false, 0L);
        w("missing", missing);
        ServerRules.Watch partial = new ServerRules.Watch();
        partial.observe(2, "no .env: robot_server.py secrets\n", false, 0L);
        w("partial", partial);

        ServerRules.Watch su = new ServerRules.Watch();
        su.observe(-1, "", true, 0L);
        w("su.open", su);
        su.observe(-1, "", false, 1L);
        w("su.closed", su);
        p("su.closed.may", su.mayStart(2L));

        ServerRules.Watch other = new ServerRules.Watch();
        other.observe(0, "starting (another start is in progress)\n", false, 0L);
        w("other", other);
        ServerRules.Watch fail = new ServerRules.Watch();
        fail.observe(1, "failed to start (see /data/local/emma-server/code/data/server.log)\n", false, 0L);
        w("fail", fail);

        ServerRules.Watch off = new ServerRules.Watch();
        off.off("192.168.1.43");
        w("off", off);
        p("off.line", off.label());
    }
}
"""


@pytest.fixture(scope="module")
def rules(tmp_path_factory) -> dict[str, str]:
    tools = _javac()
    if tools is None:
        pytest.skip("no JDK found: ServerRules was NOT compiled or run -- which URL starts a server, the "
                    "EMMA_DNS string that reaches su, the page retry and the watchdog states are unchecked "
                    "beyond the source-text tests")
    javac, java = tools
    work = tmp_path_factory.mktemp("serverrules")
    src = work / "src"
    src.mkdir()
    (src / "ServerHarness.java").write_text(HARNESS, encoding="utf-8")
    out = work / "out"
    subprocess.run([javac, "-encoding", "UTF-8", "-d", str(out), str(RULES), str(src / "ServerHarness.java")],
                   check=True, capture_output=True, encoding="utf-8", timeout=120)
    run = subprocess.run([java, "-cp", str(out), "com.emma.robot.control.ServerHarness"],
                         check=True, capture_output=True, encoding="utf-8", timeout=60)
    values = {}
    for line in run.stdout.splitlines():
        key, _, value = line.partition("=")
        values[key] = value
    return values


# ---- compiled -------------------------------------------------------------------------

def test_only_a_face_pointed_at_this_robot_starts_anything(rules):
    """A robot provisioned to a PC URL must not start a second Emma."""
    for host in ("127.0.0.1", "localhost", "LOCALHOST"):
        assert rules["local." + host] == "true", host
    for host in ("192.168.1.43", "127.0.0.1.evil.example", "emma-pc.local", "", "null"):
        assert rules["local." + host] == "false", host


def test_emma_dns_reaching_su_is_dotted_quads_only(rules):
    assert rules["dns"] == "192.168.1.1,8.8.8.8,9.9.9.9"          # dedup, IPv4, at most three
    assert rules["dns.none"] == "[]"
    assert rules["script"] == ("[ -f /data/local/emma-server/code/scripts/emma_server.sh ] || exit 86; "
                               "export EMMA_DNS='192.168.1.1,8.8.8.8'; "
                               "exec sh /data/local/emma-server/code/scripts/emma_server.sh start")
    assert rules["script.empty"].startswith("[ -f ") and "EMMA_DNS=''" in rules["script.empty"]
    assert rules["script.inject"] == "refused"


def test_the_face_retries_a_booting_server_on_a_back_off(rules):
    assert [int(rules["retry.%d" % i]) for i in range(7)] == [1000, 1000, 2000, 4000, 8000, 8000, 8000]


def test_a_crash_looping_server_is_restarted_less_and_less_often(rules):
    assert [int(rules["backoff.%d" % i]) for i in range(8)] == [0, 0, 30000, 60000, 120000, 240000,
                                                                 300000, 300000]
    assert rules["crash.2.state"] == "FAILED" and "2 ครั้ง" in rules["crash.2.detail"]
    assert rules["crash.2.may_now"] == "false" and rules["crash.2.may_later"] == "true"
    assert rules["crash.3.may"] == "false,true"                    # 60 s after the third
    assert rules["crash.ok.state"] == "RUNNING" and rules["crash.ok.may"] == "true"


def test_a_slow_boot_is_starting_then_a_fault_then_running(rules):
    assert rules["boot.1.state"] == "STARTING"
    assert rules["boot.2.state"] == "STARTING"                     # 30 s: inside the boot grace
    assert rules["boot.3.state"] == "FAILED" and "พอร์ตไม่ตอบ" in rules["boot.3.detail"]
    assert rules["boot.4.state"] == "RUNNING" and rules["boot.4.detail"] == ""
    # the operator line keeps the last error after it recovers
    assert rules["boot.4.line"].startswith("เซิร์ฟเวอร์บนหุ่น: ทำงานอยู่ · ล่าสุด: ")


def test_what_the_operator_line_says_for_each_answer(rules):
    assert rules["taken.state"] == "FAILED" and "adb reverse" in rules["taken.detail"]
    assert rules["missing.state"] == "NOT_INSTALLED"
    assert rules["partial.state"] == "NOT_INSTALLED" and rules["partial.detail"] == "no .env: robot_server.py secrets"
    assert rules["su.open.state"] == "RUNNING"                     # a live face is never "failed"
    assert rules["su.closed.state"] == "FAILED" and rules["su.closed.may"] == "true"   # su, not a start
    assert rules["other.state"] == "STARTING"
    assert rules["fail.state"] == "FAILED" and rules["fail.detail"].startswith("failed to start")
    assert rules["off.state"] == "OFF" and "192.168.1.43" in rules["off.line"]


# ---- source contracts --------------------------------------------------------------------

def test_server_rules_compile_without_android():
    text = code_only(read(RULES))
    assert "import android" not in text and "org.json" not in text


def test_the_launcher_uses_the_apps_one_su_path_and_never_stops_the_server():
    launcher = code_only(read(LAUNCHER))
    assert "ProcessBuilder" not in launcher and "Runtime.getRuntime" not in launcher
    assert launcher.count("NavLauncher.runPeriodic(") == 1
    assert "NavLauncher.runPeriodic(ServerRules.startScript(dns()), START_TIMEOUT_MS)" in launcher
    # the wedge guard of review2 #25 is the one NavLauncher already has
    nav = code_only(read(NAV))
    assert "return runShell(script, timeoutMs, true);" in method(nav, "static Shell runPeriodic(String script, long timeoutMs)")
    for path in (LAUNCHER, RULES, MAIN, FACE):
        text = code_only(read(path))
        assert "emma_server.sh stop" not in text and '" stop"' not in text, path.name
        assert '" restart"' not in text and "sh restart" not in text, path.name
    assert code_only(read(RULES)).count('" start"') == 1


def test_nothing_starts_before_the_url_is_known_to_be_this_robot():
    check = method(code_only(read(LAUNCHER)), "private void check()")
    assert check.index("ServerRules.localServer(name)") < check.index("answers(port)") \
        < check.index("NavLauncher.runPeriodic(")
    assert "watch.off(name);" in check[:check.index("} else {")]


def test_the_app_never_carries_a_secret():
    """The .env and the TLS key are root:nobody 0640 on the robot; the app
    starts a script and passes Android's DNS servers, nothing else."""
    for path in (LAUNCHER, RULES):
        text = code_only(read(path))
        for word in (".env", "lan-key", "API_KEY", "GEMINI", "WS_TOKEN", "ws_token", "getSharedPreferences"):
            assert word not in text, (path.name, word)
    assert re.findall(r"export (\w+)=", code_only(read(RULES))) == ["EMMA_DNS"]


def test_dns_comes_from_the_active_network_ipv4_only():
    dns = method(code_only(read(LAUNCHER)), "private String dns()")
    assert "cm.getActiveNetwork()" in dns and "cm.getLinkProperties(network)" in dns
    assert "link.getDnsServers()" in dns and "address instanceof Inet4Address" in dns
    assert "return ServerRules.dnsList(found);" in dns


def test_the_watchdog_has_its_own_thread_and_survives_a_bad_tick():
    launcher = code_only(read(LAUNCHER))
    assert 'new Thread(runnable, "server-watchdog")' in launcher
    assert "scheduleWithFixedDelay(this::tick, 0, WATCH_MS, TimeUnit.MILLISECONDS)" in method(launcher, "void resume()")
    assert "WATCH_MS = 30000L" in launcher
    tick = method(launcher, "private void tick()")
    assert "check();" in tick and "catch (RuntimeException e)" in tick
    # not io (arm work, the walk's launch) and not the nav poller (gesture freshness)
    assert re.search(r"\bio\b", launcher) is None


def test_main_activity_starts_on_resume_rests_on_pause_and_never_stops_the_server():
    main = code_only(read(MAIN))
    assert "server.resume();" in method(main, "protected void onResume()")
    pause = method(main, "protected void onPause()")
    assert "server.pause();" in pause
    assert pause.index("stopAndDisarm(") < pause.index("server.pause();")
    assert "server.shutdown();" in method(main, "protected void onDestroy()")
    shutdown = method(code_only(read(LAUNCHER)), "void shutdown()")
    assert "pause();" in shutdown and "watchdog.shutdownNow();" in shutdown and "runPeriodic" not in shutdown
    create = method(main, "protected void onCreate(Bundle savedInstanceState)")
    assert create.index("emmaFace = new EmmaFace(this, this);") < create.index("server = new ServerLauncher(") \
        < create.index("operatorRoot = buildUi();")
    assert "return emmaFace.serverUrl();" in create


def test_the_operator_screen_shows_one_server_line():
    main = code_only(read(MAIN))
    assert "root.addView(serverText," in method(main, "private View buildUi()")
    publish = method(main, "private void publishServerStatus()")
    assert "ui.post(" in publish and "serverText.setText(server.statusLine());" in publish


def test_a_booting_local_server_is_waited_for_not_shown_as_an_error():
    face = code_only(read(FACE))
    error = method(face, "public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error)")
    assert error.index("if (!request.isForMainFrame()) return;") < error.index("mainFrameFailed = true;") \
        < error.index("if (localServer()) {")
    local = error[error.index("if (localServer()) {"):]
    assert local.index("waitForLocalServer(view);") < local.index("return;") \
        < local.index("errorText.setVisibility(View.VISIBLE);")      # a PC server keeps its message
    wait = method(face, "private void waitForLocalServer(WebView view)")
    assert "view.setAlpha(0f);" in wait and "INVISIBLE" not in wait and "errorText.setVisibility(View.GONE);" in wait
    assert "root.postDelayed(retryLoad, ServerRules.pageRetryDelayMs(loadAttempts));" in wait
    # no error dialog: the calm line is not clickable and nothing toasts
    assert "bootText.setOnClickListener" not in face and "Toast" not in wait
    retry = method(face, "private void retryLocalLoad()")
    assert retry.index("pagePaused || root.getVisibility() != View.VISIBLE") < retry.index("reloadOnResume = true;") \
        < retry.index("loadEmma();")


def test_chromiums_error_page_finishing_is_not_read_as_emma_loading():
    face = code_only(read(FACE))
    finished = method(face, "public void onPageFinished(WebView view, String url)")
    assert finished.index("if (mainFrameFailed) return;") < finished.index("view.setAlpha(1f);")
    assert "loadAttempts = 0;" in finished
    load = method(face, "private void loadEmma()")
    assert load.index("mainFrameFailed = false;") < load.index("webView.loadUrl(page.build().toString());")
    assert "root.removeCallbacks(retryLoad);" in method(face, "private void retirePage()")
    assert "root.removeCallbacks(retryLoad);" in method(face, "void onActivityDestroy()")


def test_local_means_the_same_thing_to_the_face_and_the_watchdog():
    face = code_only(read(FACE))
    assert "return ServerRules.localServer(Uri.parse(serverUrl()).getHost());" in method(face, "private boolean localServer()")
    assert "return normalizeServer(prefs.getString(PREF_SERVER, DEFAULT_SERVER));" in method(face, "String serverUrl()")
