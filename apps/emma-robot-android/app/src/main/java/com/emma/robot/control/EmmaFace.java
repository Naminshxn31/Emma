package com.emma.robot.control;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.SystemClock;
import android.text.InputType;
import android.util.Log;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.webkit.JsPromptResult;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageButton;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Emma's face on the robot: a self-contained WebView shell of the server's
 * voice page ({@code /preview?app=1}). Lifted almost verbatim from the proven
 * {@code com.emma.ai.voice} app so the audio path is the same one the browser
 * uses -- mic up, Emma's voice down -- and NOT reinvented here. The robot's
 * camera goes up the same way (the page's own getUserMedia -> /ws/camera);
 * this class only decides who may have it.
 *
 * It is a plain View surface, not an Activity, because the robot app already
 * has one Activity (the operator/chassis screen). {@link MainActivity} owns
 * this face as the DEFAULT screen and keeps the operator UI hidden behind a
 * staff gesture. It cannot drive the chassis or the arm: it parses the page's
 * window.prompt messages against a CLOSED grammar (docs/emma-body-nav-design
 * section 2b) and hands only a shape-checked id to {@link Bridge}, which
 * MainActivity implements and checks against its own tables. No message
 * carries free text, a pulse, a channel, a group or a step.
 *
 * Debug vs release is read from FLAG_DEBUGGABLE (not BuildConfig) so this
 * compiles without turning on the buildConfig feature: release accepts only
 * HTTPS and ignores Intent-supplied origins; debug also allows http to
 * loopback/emulator for development against a PC, honours the adb launch
 * extras, and turns on WebContents debugging.
 *
 * What does NOT depend on the build type (2026-09-29 review): the robot runs
 * the debug APK -- the repo has no release signing and the owner provisions it
 * with `am start --es server_url --es ws_token` -- so every guard that was
 * "release only" was simply off on the robot. The two that matter on the
 * customer face now hold in every build: the setup gear is gone once Emma is
 * configured, and the stored WS_TOKEN only ever goes to the origin it was
 * stored for (a changed server needs its own token, typed or sent with it).
 */
final class EmmaFace {
    /** What the trusted page may ask native for. Every method runs on the UI
     *  thread inside onJsPrompt, so each answers from cached state and queues
     *  any work: "accepted" means queued after the cheap gates passed. */
    interface Bridge {
        /** Legacy `emma-arm:greet:6`: the camera greeter's handshake. */
        void armGreeting();
        String bodyList();
        String bodyStatus();
        /** Always accepted: a stop is never blocked by focus or state. */
        String bodyStop();
        String bodyGesture(String id);
        /** A speech gesture, timed by the server to her voice. */
        String bodySpeech(String id);
        /** The head toward a person: percent of the app's head span (HeadLook). */
        String bodyLook(int pan, int tilt);
        String navPlaces();
        String navStatus();
        String navGo(String placeId, String origin);
        /** Always accepted, like bodyStop. */
        String navStop();
        /** The walk back to the dock after idle (owner 10 Oct): its state for
         *  the face's banner; "shown" = the warning is on screen now; "stay" =
         *  อยู่ต่อ. None of the four can start anything -- the walk is the
         *  app's own decision, made only after 10 s of a confirmed warning. */
        String navAuto();
        String navAutoShown();
        String navAutoStay();
        /** A call started/ended, somebody spoke in it: the count restarts.
         *  `kind` is one of this class's own literals, never page text. */
        String navActivity(String kind);
        /** The robot's face_token for the page's face_hello. */
        String faceToken();
        /** ARRAY_MIC (docs/emma-array-mic-2026-11-02.md): is the app's capture
         *  of the mic array still running -- the page opens its own mic only
         *  after it has ENDED (one capture stream on this array). */
        String micState();
        /** ARRAY_MIC: let go of the array for the rest of this app run (the
         *  page takes its mic). Idempotent; answers like micState. */
        String micRelease();
        /** ARRAY_MIC: let go of the array for the page's CALL -- `kind` is one of
         *  this class's literals: "start" (not live at the call's ready) or
         *  "deaf" (it went quiet mid-call; counted per app run). */
        String micHold(String kind);
        /** ARRAY_MIC: the page's call mic is stopped, or a page just loaded. */
        String micFree();
        /** The page died with its renderer (review #14): whatever it asked to
         *  move has nobody answering for it now. */
        void pageLost();
    }

    /** The grammar's shapes. matches() on a pattern with no anchors: Java's
     *  `$` also matches before a final line terminator, and an id with a
     *  trailing newline is two shell lines by the time it reaches a shell. */
    private static final Pattern BODY_ACTION = Pattern.compile("emma-body:([a-z][a-z0-9_]{1,31})");
    /** Her arm while she talks (owner 15 Oct): the same id shape, one fixed
     *  suffix. An APK without it answers bad_request (BODY_ACTION needs the
     *  whole message), so a newer page/server never moves an older app. */
    private static final Pattern BODY_SPEECH = Pattern.compile("emma-body:([a-z][a-z0-9_]{1,31}):speech");
    /** The head toward a person (owner 31 Oct): two whole numbers, each up to
     *  three digits with an optional minus -- PERCENT of the app's own head
     *  span, range-checked again by BodyController/HeadLook. An older APK
     *  answers bad_request (no pattern of its own matches it). */
    private static final Pattern BODY_LOOK = Pattern.compile("emma-body:look:(-?[0-9]{1,3}):(-?[0-9]{1,3})");
    private static final Pattern NAV_GO = Pattern.compile("emma-nav:go:([a-z][a-z0-9_]{1,31}):(tap|voice)");
    private static final String BAD_REQUEST = "{\"accepted\":false,\"reason\":\"bad_request\"}";

    private static final String PREFS = "emma_robot_face";
    private static final String PREF_SERVER = "server_url";
    private static final String PREF_TOKEN = "ws_token";
    /** The canonical origin the stored token belongs to. WS_TOKEN also guards
     *  /ws, which drives the robot: before this key, the setup panel refilled
     *  the stored token and loadEmma appended it to whatever server was typed,
     *  so repointing the app handed the token to that server. */
    private static final String PREF_TOKEN_ORIGIN = "ws_token_origin";
    private static final String DEFAULT_SERVER = "https://127.0.0.1:8001";
    private static final String TOKEN_FOR_THIS_SERVER =
            "ใส่ WS_TOKEN ของเซิร์ฟเวอร์นี้ — token ที่เก็บไว้ใช้ได้กับเซิร์ฟเวอร์ที่บันทึกคู่กันเท่านั้น";
    /** The staff way back into setup once the gear has left the face: a
     *  DELIBERATE hold in the top-right corner (where the gear used to be).
     *  Longer than the operator screen's 3 s top-left hold and in the other
     *  corner, so neither a finger resting on the glass nor the operator
     *  gesture opens it. */
    private static final long SETUP_HOLD_MS = 5000;
    private static final int SETUP_CORNER_DP = 90;
    private static final int MEDIA_PERMISSION_REQUEST = 901;
    /** Asked for together in ONE system dialog, so first-run setup at the robot
     *  is a single sitting -- the staff member granting the mic grants the
     *  camera in the same breath instead of meeting a second dialog later on
     *  the customer face. Each is still checked on its own afterwards. */
    private static final String[] MEDIA_PERMISSIONS = {
            android.Manifest.permission.RECORD_AUDIO,
            android.Manifest.permission.CAMERA,
    };

    private static final String TAG = "EmmaFace";
    /** Review #14: renderer deaths this close together stop the reload loop. */
    private static final int RENDERER_DEATHS_MAX = 3;
    private static final long RENDERER_DEATH_WINDOW_MS = 60000L;

    private final Activity activity;
    private final Bridge bridge;
    private final ArrayDeque<Long> rendererDeaths = new ArrayDeque<>();
    /** The page was told to pause (pausePage) and not resumed since. */
    private boolean pagePaused = false;
    /** A renderer died while the face was hidden or paused: load on resume. */
    private boolean reloadOnResume = false;
    private final SharedPreferences prefs;

    private FrameLayout root;
    private WebView webView;
    private ImageButton settingsButton;
    private LinearLayout setupPanel;
    private EditText serverInput;
    private EditText tokenInput;
    private TextView errorText;
    private ProgressBar progress;
    /** The calm line under the spinner while this robot's own server boots. */
    private TextView bootText;
    /** The last main-frame load failed. Chromium calls onPageFinished for its
     *  own error page too, right after onReceivedError -- read as success, it
     *  hid the "เชื่อมต่อ Emma ไม่ได้" message the moment it was shown and left
     *  customers looking at "Webpage not available". */
    private boolean mainFrameFailed = false;
    /** Loads of a local server that failed in a row, and since when. */
    private int loadAttempts = 0;
    private long waitingSince = -1L;
    private final Runnable retryLoad = this::retryLocalLoad;
    /** Page requests waiting on the system dialog. A list, not one slot: with
     *  the camera the page makes TWO requests (the mic, and separately the
     *  camera), so on first run the second can arrive while the dialog for the
     *  first is still up. One slot would overwrite the mic request and leave
     *  it unanswered forever -- Emma deaf, with nothing on screen saying why. */
    private final List<PermissionRequest> pendingWebPermissions = new ArrayList<>();
    private boolean mediaDialogOpen = false;
    /** The system permission dialog is an Activity on top of ours, so raising
     *  it PAUSES MainActivity -- and every pause used to reach the page as
     *  'emma-native-pause', which closes the face scan and ends the call. The
     *  request that raised the dialog tore down its own caller: staff tapped
     *  Allow and the scan was already gone (the late stream stopped as stale),
     *  or the camera was refused and the overlay vanished with no reason. So
     *  the pause our own dialog causes is swallowed, with the resume that
     *  answers it. */
    private boolean dialogPauseExpected = false;
    private boolean pauseSwallowed = false;
    /** Android permissions refused in THIS process. The page's ?cam=1 sender
     *  retries a failed camera every 10 s (six tries per load), and each retry
     *  used to call requestPermissions again: the dialog came back over the
     *  customer face, and once refusal was permanent Android still ran the
     *  permission activity -- a pause each time, hanging up any live call.
     *  A CAMERA refusal now stands until the app restarts (or staff grant it
     *  in Settings, which holds() sees). The mic's is never remembered: it is
     *  asked for only when a person taps to talk. */
    private final Set<String> refusedThisProcess = new HashSet<>();
    private String[] askedPermissions = new String[0];
    private String allowedOrigin = "";
    private boolean setupHeld = false;
    private final Runnable setupReveal = () -> {
        if (setupHeld) { setupHeld = false; showSetup(); }
    };

    EmmaFace(Activity activity, Bridge bridge) {
        this.activity = activity;
        this.bridge = bridge;
        this.prefs = activity.getSharedPreferences(PREFS, Activity.MODE_PRIVATE);
        bindLegacyToken();
    }

    /** The face surface; add it to the Activity's view tree. */
    View view() {
        if (root == null) root = buildUi();
        return root;
    }

    /** Load Emma, or show the server/token setup panel if nothing is stored. */
    void start(Intent launchIntent) {
        consumeLaunchConfiguration(launchIntent);
        openOrSetup();
    }

    void onActivityResume() {
        if (pauseSwallowed) {       // back from OUR dialog: the page never heard us leave
            pauseSwallowed = false;
            return;
        }
        pagePaused = false;
        if (webView != null) webView.onResume();
        if (reloadOnResume && webView != null && root != null && root.getVisibility() == View.VISIBLE) {
            reloadOnResume = false;
            openOrSetup();
        }
    }

    void onActivityPause() {
        if (dialogPauseExpected) {  // the pause our own permission dialog causes
            dialogPauseExpected = false;
            pauseSwallowed = true;
            return;
        }
        pausePage();
    }

    /** From MainActivity.onStop. A translucent permission dialog pauses but
     *  never stops the Activity, so a stop while a pause is swallowed is a real
     *  departure (Home while our dialog was up): the page must hear it now, or
     *  the mic and the lens stay open behind a screen nobody sees. */
    void onActivityStop() {
        if (pauseSwallowed) {
            pauseSwallowed = false;
            pausePage();
        }
    }

    private void pausePage() {
        pagePaused = true;
        if (webView != null) {
            webView.evaluateJavascript("window.dispatchEvent(new Event('emma-native-pause'))", null);
            webView.onPause();
        }
    }

    /** One of our own fixed event names to the page; never page-supplied text. */
    private void notifyPage(String event) {
        if (webView != null) {
            try {
                webView.evaluateJavascript("window.dispatchEvent(new Event('" + event + "'))", null);
            } catch (RuntimeException e) {
                // review2 #21: a notification is never worth a stop path --
                // the callers (body/nav stops) run this before they finish
                Log.w(TAG, "page notification " + event + " not delivered", e);
            }
        }
    }

    /** Native -> page carries no payload: the page pulls the data with
     *  emma-body:status / emma-nav:status. Callable from any thread. */
    void bodyChanged() {
        activity.runOnUiThread(() -> notifyPage("emma-body-changed"));
    }

    void navChanged() {
        activity.runOnUiThread(() -> notifyPage("emma-nav-changed"));
    }

    void onActivityDestroy() {
        for (PermissionRequest request : pendingWebPermissions) request.deny();
        pendingWebPermissions.clear();
        mediaDialogOpen = false;
        dialogPauseExpected = false;
        pauseSwallowed = false;
        if (root != null) root.removeCallbacks(retryLoad);
        if (webView != null) {
            webView.removeCallbacks(setupReveal);
            webView.loadUrl("about:blank");
            webView.destroy();
            webView = null;
        }
    }

    void onNewIntent(Intent intent) {
        if (consumeLaunchConfiguration(intent) && webView != null) openOrSetup();
    }

    /** Emma if a token is stored, the setup panel if not. An intent that moved
     *  the server without a token lands here unconfigured: the page of the old
     *  server is retired, not left listening behind the panel. */
    private void openOrSetup() {
        refreshGear();
        if (isConfigured()) {
            loadEmma();
            return;
        }
        retirePage();
        showSetup();
        if (prefs.contains(PREF_SERVER)) tokenInput.setError(TOKEN_FOR_THIS_SERVER);
    }

    private boolean debugBuild() {
        return (activity.getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0;
    }

    /** The "Emma Event" build (build.gradle -PemmaApp=event): told apart by its
     *  own package, like debug by its flag -- no BuildConfig needed. */
    static boolean isEventApp(Context context) {
        return EVENT_PACKAGE.equals(context.getPackageName());
    }

    static final String EVENT_PACKAGE = "com.emma.robot.event";
    static final String ROBOT_PACKAGE = "com.emma.robot.control";

    private FrameLayout buildUi() {
        FrameLayout container = new FrameLayout(activity);
        container.setBackgroundColor(Color.rgb(5, 11, 18));

        container.addView(newWebView(), new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        progress = new ProgressBar(activity);
        container.addView(progress, new FrameLayout.LayoutParams(dp(38), dp(38), Gravity.CENTER));

        errorText = text("", 16, 0xFFFFA7B3);
        errorText.setGravity(Gravity.CENTER);
        errorText.setPadding(dp(24), dp(18), dp(24), dp(18));
        errorText.setBackgroundColor(Color.argb(225, 23, 18, 28));
        errorText.setVisibility(View.GONE);
        errorText.setOnClickListener(v -> loadEmma());
        // not clickable: a touch on it still reaches the WebView (the setup hold)
        bootText = text("", 15, 0xFFB9C7D2);
        bootText.setGravity(Gravity.CENTER);
        bootText.setVisibility(View.GONE);
        FrameLayout.LayoutParams bootParams = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER);
        bootParams.setMargins(dp(28), dp(84), dp(28), 0);   // under the spinner
        container.addView(bootText, bootParams);
        FrameLayout.LayoutParams errorParams = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER);
        errorParams.setMargins(dp(28), 0, dp(28), 0);
        container.addView(errorText, errorParams);

        ImageButton settings = new ImageButton(activity);
        settings.setImageResource(R.drawable.ic_settings);
        settings.setBackgroundColor(Color.argb(94, 8, 18, 31));
        settings.setContentDescription("ตั้งค่าการเชื่อมต่อ Emma");
        settings.setPadding(dp(12), dp(12), dp(12), dp(12));
        settings.setOnClickListener(v -> showSetup());
        settingsButton = settings;
        FrameLayout.LayoutParams settingsParams = new FrameLayout.LayoutParams(dp(48), dp(48), Gravity.TOP | Gravity.END);
        settingsParams.setMargins(0, dp(12), dp(12), 0);
        container.addView(settings, settingsParams);
        refreshGear();     // no gear on the customer face once Emma is configured

        setupPanel = buildSetupPanel();
        setupPanel.setVisibility(View.GONE);
        container.addView(setupPanel, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        return container;
    }

    /** The ONE place a face WebView is made: buildUi and the renderer-death
     *  rebuild (review #14) both come here, so the two cannot drift apart --
     *  a rebuilt page without the setup hold, or without the trust gate,
     *  would be a different app. */
    private WebView newWebView() {
        webView = new WebView(activity);
        configureWebView();
        webView.setOnTouchListener((v, event) -> {
            trackSetupHold(v, event);
            return false;           // observes only: every touch still reaches the page
        });
        return webView;
    }

    /** Review #14: without onRenderProcessGone returning true, a renderer
     *  crash or an OOM kill of the renderer (camera + WebGL mascot all day)
     *  took the whole app down -- the native SafetyBar with it, and a crash
     *  skips onDestroy, so a gesture mid-flight was never stopped. The dead
     *  view must never be touched again: it is removed and destroyed, a new
     *  one takes its slot, and the page loads again. Repeated deaths in a
     *  short window stop reloading and say so, instead of looping. */
    private void recoverFromRendererDeath(WebView dead, boolean crashed) {
        Log.w(TAG, "WebView renderer gone (crashed=" + crashed + "), rebuilding the face");
        // Review2 #21: the dead view is dropped BEFORE pageLost. pageLost
        // stops the walk and the arm, and each stop notifies the page on this
        // thread (runOnUiThread runs inline) -- notifyPage then called
        // evaluateJavascript on the crashed WebView, which Android says can
        // only be removed and destroyed, on the very path #14 added to keep
        // the app and the SafetyBar alive.
        boolean wasFace = webView == dead;
        if (wasFace) webView = null;
        bridge.pageLost();
        for (PermissionRequest request : pendingWebPermissions) {
            try {
                request.deny();
            } catch (RuntimeException ignored) {
                // its renderer is gone; nothing is waiting for the answer
            }
        }
        pendingWebPermissions.clear();
        setupHeld = false;
        dead.removeCallbacks(setupReveal);
        ViewGroup parent = (ViewGroup) dead.getParent();
        int index = parent == null ? 0 : Math.max(0, parent.indexOfChild(dead));
        if (parent != null) parent.removeView(dead);
        dead.destroy();
        if (!wasFace || root == null) return;   // not the face's view: nothing to rebuild
        root.addView(newWebView(), index, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        long now = SystemClock.elapsedRealtime();
        rendererDeaths.addLast(now);
        while (!rendererDeaths.isEmpty() && now - rendererDeaths.peekFirst() > RENDERER_DEATH_WINDOW_MS) {
            rendererDeaths.pollFirst();
        }
        if (rendererDeaths.size() >= RENDERER_DEATHS_MAX) {
            progress.setVisibility(View.GONE);
            errorText.setText("หน้าจอ Emma ปิดตัวซ้ำหลายครั้ง\nแตะเพื่อลองใหม่ หรือเปิดแอปใหม่");
            errorText.setVisibility(View.VISIBLE);
            return;
        }
        // hidden (operator screen) or paused: load when the face comes back,
        // never a live page with the mic and the lens behind another screen
        if (pagePaused || root.getVisibility() != View.VISIBLE) {
            reloadOnResume = true;
            return;
        }
        openOrSetup();
    }

    private void configureWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);   // kiosk: audio/mic start with no tap
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        // EmmaBridge/1 says "this APK answers the emma-body/emma-nav/emma-face
        // prompts". The page asks them only when it sees this marker: an older
        // APK answers an unknown prompt with `return false`, and the WebView
        // then shows its own prompt dialog ("emma-face:token") in front of a
        // customer on every call until the APK is reinstalled.
        // EmmaArrayMic/1 (ARRAY_MIC, 2 Nov 2026): this APK can hold the mic
        // array alone and answers emma-face:mic / emma-face:mic:release -- a
        // page goes without its own mic only for an app that says so.
        settings.setUserAgentString(settings.getUserAgentString() + " EmmaRobot/0.1 EmmaBridge/1 EmmaArrayMic/1");
        WebView.setWebContentsDebuggingEnabled(debugBuild());

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                if (!request.isForMainFrame()) return false;
                Uri target = request.getUrl();
                String origin = originOf(target);
                if (trusted(origin)) return false;   // stay in-app only for the trusted origin
                if ("http".equals(target.getScheme()) || "https".equals(target.getScheme())) {
                    activity.startActivity(new Intent(Intent.ACTION_VIEW, target));
                }
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                if (mainFrameFailed) return;     // Chromium's error page "finished", not Emma
                loadAttempts = 0;
                waitingSince = -1L;
                view.setAlpha(1f);
                progress.setVisibility(View.GONE);
                errorText.setVisibility(View.GONE);
                bootText.setVisibility(View.GONE);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (!request.isForMainFrame()) return;
                mainFrameFailed = true;
                if (localServer()) {
                    waitForLocalServer(view);
                    return;
                }
                progress.setVisibility(View.GONE);
                errorText.setText("เชื่อมต่อ Emma ไม่ได้\nตรวจว่าเซิร์ฟเวอร์ทำงานและที่อยู่ถูกต้อง แล้วกดลองใหม่");
                errorText.setVisibility(View.VISIBLE);
            }

            @Override
            public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
                recoverFromRendererDeath(view, detail != null && detail.didCrash());
                return true;        // the app lives: SafetyBar, tether and arm owner are native
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onJsPrompt(WebView view, String url, String message,
                                      String defaultValue, JsPromptResult result) {
                if (message == null) return false;
                if ("emma-arm:greet:6".equals(message)) {
                    if (trustedPreview(url, view)) bridge.armGreeting();
                    result.confirm("");
                    return true;
                }
                if (!message.startsWith("emma-body:") && !message.startsWith("emma-nav:")
                        && !message.startsWith("emma-face:")) {
                    return false;           // unchanged browser behaviour for every other prompt
                }
                // The trust gate holds for EVERY emma-* message: a page that
                // is not our /preview?app=1 gets bad_request and nothing runs.
                result.confirm(trustedPreview(url, view) ? answer(message) : BAD_REQUEST);
                return true;
            }

            @Override
            public void onPermissionRequest(PermissionRequest request) {
                activity.runOnUiThread(() -> handleWebPermission(request));
            }

            @Override
            public void onPermissionRequestCanceled(PermissionRequest request) {
                // the page gave up (navigated, reloaded): nothing left to answer
                activity.runOnUiThread(() -> pendingWebPermissions.remove(request));
            }
        });
    }

    /** The configured server, normalized. ServerLauncher reads it from its
     *  own thread (SharedPreferences reads are thread-safe): its watchdog runs
     *  only while this points at the robot itself. */
    String serverUrl() {
        return normalizeServer(prefs.getString(PREF_SERVER, DEFAULT_SERVER));
    }

    private boolean localServer() {
        return ServerRules.localServer(Uri.parse(serverUrl()).getHost());
    }

    /** The mic array's socket (ArrayListener): /ws/robot-doa on the
     *  configured server, with the stored WS_TOKEN -- and ONLY when that token
     *  was stored for this server's origin, the rule loadEmma enforces (a
     *  token never follows a server it was not typed with). wss for https;
     *  ws only where validServer allows http (debug, loopback). null = not
     *  configured: the listener waits, it never asks anybody. Any thread
     *  (SharedPreferences reads are thread-safe). */
    String doaSocketUrl() {
        String server = serverUrl();
        if (!validServer(server)) return null;
        String token = prefs.getString(PREF_TOKEN, "");
        Uri base = Uri.parse(server);
        if (token == null || token.isEmpty()
                || !originOf(base).equals(prefs.getString(PREF_TOKEN_ORIGIN, ""))) {
            return null;
        }
        String path = base.getPath() == null ? "" : base.getPath();
        if (path.endsWith("/preview")) path = path.substring(0, path.length() - "/preview".length());
        while (path.endsWith("/")) path = path.substring(0, path.length() - 1);
        return new Uri.Builder()
                .scheme("https".equals(base.getScheme()) ? "wss" : "ws")
                .encodedAuthority(base.getEncodedAuthority())
                .encodedPath(path + "/ws/robot-doa")
                .appendQueryParameter("token", token)
                .build().toString();
    }

    /** The robot's own server (scripts/robot_server.py) is booting -- ~20 s
     *  after the app's ServerLauncher started it -- or restarting. The face
     *  waits for it instead of showing a customer an error: Chromium's error
     *  page is hidden (alpha 0, which keeps the setup-corner hold working;
     *  INVISIBLE would not), the spinner and a calm line stay, and the page is
     *  asked for again on a back-off until it answers. A PC server keeps the
     *  tap-to-retry message: nothing on this robot can start that one. */
    private void waitForLocalServer(WebView view) {
        loadAttempts++;
        long now = SystemClock.elapsedRealtime();
        if (waitingSince < 0) waitingSince = now;
        view.setAlpha(0f);
        errorText.setVisibility(View.GONE);
        progress.setVisibility(View.VISIBLE);
        bootText.setText(now - waitingSince < 60000L ? "Emma กำลังเตรียมตัว…" : "Emma กำลังเตรียมตัว อีกสักครู่นะคะ");
        bootText.setVisibility(View.VISIBLE);
        if (root == null) return;
        root.removeCallbacks(retryLoad);
        root.postDelayed(retryLoad, ServerRules.pageRetryDelayMs(loadAttempts));
    }

    private void retryLocalLoad() {
        if (webView == null || root == null) return;
        // never a live page (mic, lens) behind another screen: load on resume
        if (pagePaused || root.getVisibility() != View.VISIBLE) {
            reloadOnResume = true;
            return;
        }
        if (setupPanel != null && setupPanel.getVisibility() == View.VISIBLE) return;   // staff are choosing a server
        loadEmma();
    }

    /** The trust gate from the first handshake, now for every emma-* message:
     *  the prompt's own frame and the page on screen are both the trusted
     *  origin's /preview, and the page says app=1. */
    private boolean trustedPreview(String url, WebView view) {
        Uri source = Uri.parse(url == null ? "" : url);
        Uri current = Uri.parse(view.getUrl() == null ? "" : view.getUrl());
        return trusted(originOf(source)) && trusted(originOf(current))
                && "/preview".equals(source.getPath())
                && "/preview".equals(current.getPath())
                && "1".equals(current.getQueryParameter("app"));
    }

    /** The closed grammar. Only the SHAPE is checked here; whether an id is a
     *  real gesture or a place native knows is MainActivity's call against its
     *  own tables (ids are validated twice). */
    private String answer(String message) {
        switch (message) {
            case "emma-body:list":
                return bridge.bodyList();
            case "emma-body:status":
                return bridge.bodyStatus();
            case "emma-body:stop":
                return bridge.bodyStop();
            case "emma-nav:places":
                return bridge.navPlaces();
            case "emma-nav:status":
                return bridge.navStatus();
            case "emma-nav:stop":
                return bridge.navStop();
            case "emma-nav:auto":
                return bridge.navAuto();
            case "emma-nav:auto:shown":
                return bridge.navAutoShown();
            case "emma-nav:auto:stay":
                return bridge.navAutoStay();
            case "emma-nav:activity:call_start":
                return bridge.navActivity("call_start");
            case "emma-nav:activity:call_end":
                return bridge.navActivity("call_end");
            case "emma-nav:activity:speech":
                return bridge.navActivity("speech");
            case "emma-face:token":
                return bridge.faceToken();
            case "emma-face:mic":
                return bridge.micState();
            case "emma-face:mic:release":
                return bridge.micRelease();
            case "emma-face:mic:hold":
                return bridge.micHold("start");
            case "emma-face:mic:hold:deaf":
                return bridge.micHold("deaf");
            case "emma-face:mic:free":
                return bridge.micFree();
            default:
                break;
        }
        Matcher go = NAV_GO.matcher(message);
        if (go.matches()) return bridge.navGo(go.group(1), go.group(2));
        Matcher speech = BODY_SPEECH.matcher(message);
        if (speech.matches()) return bridge.bodySpeech(speech.group(1));
        Matcher look = BODY_LOOK.matcher(message);
        if (look.matches()) return bridge.bodyLook(Integer.parseInt(look.group(1)), Integer.parseInt(look.group(2)));
        Matcher body = BODY_ACTION.matcher(message);
        if (body.matches()) return bridge.bodyGesture(body.group(1));
        return BAD_REQUEST;
    }

    /** The ONLY page resources this face ever grants, each paired with the
     *  Android permission it rides on; anything else a page may ask for (MIDI
     *  sysex, protected media ID) maps to null and is never granted.
     *
     *  It used to be audio only. The chest screen was Chrome on
     *  /?kiosk=1&cam=1 then, and that page streamed the robot camera to
     *  /ws/camera for the greeter (FACE_CAMERA_SOURCE=robot). With /preview in
     *  this WebView holding the robot's only USB camera, nothing fed the
     *  greeter -- so video joins audio, for the trusted origin alone. */
    private static String androidPermissionFor(String resource) {
        if (PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(resource)) return android.Manifest.permission.RECORD_AUDIO;
        if (PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(resource)) return android.Manifest.permission.CAMERA;
        return null;
    }

    private boolean holds(String permission) {
        return activity.checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED;
    }

    /** What of this request may be granted right now: known resources whose
     *  Android permission is held. Judged per resource so a refused camera
     *  never takes the mic down with it -- the voice is the product, the
     *  camera an extra. (A page that asks for mic AND camera in ONE
     *  getUserMedia call still fails whole in Chromium when either is
     *  missing; the page must ask for them separately.) */
    private List<String> grantableNow(PermissionRequest request) {
        List<String> allowed = new ArrayList<>();
        for (String resource : request.getResources()) {
            String permission = androidPermissionFor(resource);
            if (permission != null && holds(permission)) allowed.add(resource);
        }
        return allowed;
    }

    /** Worth raising the system dialog for: not held, and not already refused
     *  in this process. */
    private boolean askable(String permission) {
        return !holds(permission) && !refusedThisProcess.contains(permission);
    }

    private void handleWebPermission(PermissionRequest request) {
        if (!trusted(originOf(request.getOrigin()))) {   // only the trusted origin
            request.deny();
            return;
        }
        boolean wantsMedia = false;
        boolean ask = false;
        for (String resource : request.getResources()) {
            String permission = androidPermissionFor(resource);        // audio + video; nothing else
            if (permission == null) continue;
            wantsMedia = true;
            if (askable(permission)) ask = true;
        }
        if (!wantsMedia) {
            request.deny();
            return;
        }
        if (!ask) {
            // Everything is held, or refused already in this process: answer
            // NOW from what Android holds. Asking again is what paused the
            // Activity on every one of the page's camera retries.
            List<String> allowed = grantableNow(request);
            if (allowed.isEmpty()) request.deny();
            else request.grant(allowed.toArray(new String[0]));
            return;
        }
        pendingWebPermissions.add(request);
        if (mediaDialogOpen) return;        // rides the dialog already up (it asks for every missing one)
        List<String> missing = new ArrayList<>();
        for (String permission : MEDIA_PERMISSIONS) {
            if (askable(permission)) missing.add(permission);
        }
        askedPermissions = missing.toArray(new String[0]);
        mediaDialogOpen = true;
        dialogPauseExpected = true;         // the dialog pauses us: that pause is ours, not the app leaving
        // The pause is swallowed, so the page would not know a person is
        // reading a dialog -- and its 8 s "camera request hung" timer ended
        // the face scan under them. Said in words instead (voice-preview.html).
        notifyPage("emma-native-asking");
        activity.requestPermissions(askedPermissions, MEDIA_PERMISSION_REQUEST);
    }

    /** Called from MainActivity.onRequestPermissionsResult. Returns true if handled. */
    boolean onRequestPermissionsResult(int requestCode, int[] results) {
        if (requestCode != MEDIA_PERMISSION_REQUEST) return false;
        mediaDialogOpen = false;
        dialogPauseExpected = false;        // answered: whatever pauses us next is not this dialog
        notifyPage("emma-native-asked");
        // Empty results = the dialog was interrupted, not answered; only an
        // answer counts as a refusal (then the page's next try asks again).
        // And only the CAMERA's refusal is remembered: it is the one the page
        // retries on its own (the ?cam=1 sender, every 10 s). The mic is
        // asked for only when a person taps to talk, so asking again is an
        // answer to that person -- remembering its refusal (review, round 2)
        // left Emma deaf until an app restart after one mis-tapped dialog.
        if (results != null && results.length > 0) {
            for (String permission : askedPermissions) {
                if (!holds(permission) && android.Manifest.permission.CAMERA.equals(permission)) {
                    refusedThisProcess.add(permission);
                }
            }
        }
        askedPermissions = new String[0];
        List<PermissionRequest> waiting = new ArrayList<>(pendingWebPermissions);
        pendingWebPermissions.clear();
        String currentOrigin = webView == null || webView.getUrl() == null
                ? "" : originOf(Uri.parse(webView.getUrl()));
        boolean micRefused = false;
        for (PermissionRequest request : waiting) {
            // read what Android now holds rather than results[i]: the dialog may
            // have covered only part of what this request needs, and the answer
            // is per permission -- camera refused, mic granted is a working Emma
            List<String> allowed = grantableNow(request);
            if (!allowed.isEmpty()
                    && trusted(originOf(request.getOrigin()))   // re-check origin at grant time
                    && trusted(currentOrigin)) {                // and the page has not navigated away
                request.grant(allowed.toArray(new String[0]));
            } else {
                request.deny();
            }
            for (String resource : request.getResources()) {
                if (PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(resource)
                        && !holds(android.Manifest.permission.RECORD_AUDIO)) micRefused = true;
            }
        }
        // Only the mic gets a toast: without it Emma is deaf. A refused camera
        // leaves a working voice, and the operator console already says there
        // is no live picture -- a toast per page load on the customer face
        // would be noise for the person standing in front of it.
        if (micRefused) {
            Toast.makeText(activity, "Emma ต้องใช้ไมโครโฟนเพื่อรับเสียงพูด", Toast.LENGTH_LONG).show();
        }
        return true;
    }

    private LinearLayout buildSetupPanel() {
        LinearLayout outer = new LinearLayout(activity);
        outer.setOrientation(LinearLayout.VERTICAL);
        outer.setGravity(Gravity.CENTER);
        outer.setPadding(dp(28), dp(32), dp(28), dp(32));
        outer.setBackgroundColor(Color.rgb(5, 11, 18));

        LinearLayout card = new LinearLayout(activity);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(24), dp(24), dp(24), dp(24));
        card.setBackgroundColor(Color.rgb(13, 27, 41));
        outer.addView(card, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        TextView title = text("เชื่อมต่อ Emma", 24, Color.WHITE);
        card.addView(title, matchWrap(0, 0, 0, 8));
        TextView detail = text("ใส่ที่อยู่เซิร์ฟเวอร์ condo-voice และ WS_TOKEN ข้อมูลนี้เก็บในพื้นที่ส่วนตัวของแอป"
                + " · token ผูกกับเซิร์ฟเวอร์ที่ใส่คู่กัน เปลี่ยนเซิร์ฟเวอร์ต้องใส่ token ของเซิร์ฟเวอร์ใหม่", 15, 0xFFB9C7D2);
        card.addView(detail, matchWrap(0, 0, 0, 18));

        serverInput = new EditText(activity);
        serverInput.setHint("https://127.0.0.1:8001");
        serverInput.setTextColor(Color.WHITE);
        serverInput.setHintTextColor(0xFF728392);
        serverInput.setSingleLine(true);
        serverInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        card.addView(serverInput, matchWrap(0, 0, 0, 12));

        tokenInput = new EditText(activity);
        tokenInput.setHint("WS_TOKEN");
        tokenInput.setTextColor(Color.WHITE);
        tokenInput.setHintTextColor(0xFF728392);
        tokenInput.setSingleLine(true);
        tokenInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        card.addView(tokenInput, matchWrap(0, 0, 0, 18));

        Button connect = new Button(activity);
        connect.setText("เชื่อมต่อและเปิด Emma");
        connect.setTextColor(0xFF05110C);
        connect.setBackgroundColor(0xFF67E6B3);
        connect.setOnClickListener(v -> saveAndLoad());
        card.addView(connect, matchWrap(0, 0, 0, 10));

        Button cancel = new Button(activity);
        cancel.setText("ยกเลิก");
        cancel.setTextColor(0xFFD8E2EA);
        cancel.setBackgroundColor(0xFF1B3042);
        cancel.setOnClickListener(v -> {
            if (webView.getUrl() != null) setupPanel.setVisibility(View.GONE);
        });
        card.addView(cancel, matchWrap(0, 0, 0, 0));
        return outer;
    }

    private boolean isConfigured() {
        String token = prefs.getString(PREF_TOKEN, "");
        return token != null && !token.trim().isEmpty();
    }

    /** The setup gear controls the server URL and token, so it must NOT sit on
     *  the face a customer sees once Emma is configured (blueprint: no controls
     *  in customer mode -- anyone at the kiosk could otherwise repoint it). It
     *  shows only while nothing is configured -- in EVERY build: it used to stay
     *  up in debug, and debug is the APK the robot runs, so a passer-by could
     *  tap it, type a server and get the stored token plus the mic and camera.
     *  Staff reach setup with the top-right hold instead (trackSetupHold). */
    private void refreshGear() {
        if (settingsButton != null) {
            settingsButton.setVisibility(isConfigured() ? View.GONE : View.VISIBLE);
        }
    }

    /** A deliberate SETUP_HOLD_MS hold in the top-right corner opens setup.
     *  Observed from the WebView's touch listener, which never consumes, so the
     *  page still gets every tap; moving out of the corner, lifting, or a second
     *  finger cancels it -- the same rules as the operator screen's hold. */
    private void trackSetupHold(View v, MotionEvent e) {
        int corner = dp(SETUP_CORNER_DP);
        switch (e.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                if (e.getX() > v.getWidth() - corner && e.getY() < corner) {
                    setupHeld = true;
                    v.postDelayed(setupReveal, SETUP_HOLD_MS);
                }
                break;
            case MotionEvent.ACTION_MOVE:
                if (setupHeld && (e.getX() < v.getWidth() - dp(120) || e.getY() > dp(120))) {
                    setupHeld = false;
                    v.removeCallbacks(setupReveal);
                }
                break;
            case MotionEvent.ACTION_POINTER_DOWN:
            case MotionEvent.ACTION_UP:
            case MotionEvent.ACTION_CANCEL:
                setupHeld = false;
                v.removeCallbacks(setupReveal);
                break;
            default:
                break;
        }
    }

    private void showSetup() {
        serverInput.setError(null);
        tokenInput.setError(null);
        serverInput.setText(prefs.getString(PREF_SERVER, DEFAULT_SERVER));
        // The stored token is NEVER put back into the field. Pre-filled (even
        // masked) it counted as "typed with" whatever server went in above it,
        // and followed that server out of the building. Blank = keep the stored
        // one, and only for the server it was stored with (saveAndLoad).
        tokenInput.setText("");
        tokenInput.setHint(isConfigured() ? "WS_TOKEN (เว้นว่าง = ใช้ตัวเดิม กับเซิร์ฟเวอร์เดิมเท่านั้น)" : "WS_TOKEN");
        setupPanel.setVisibility(View.VISIBLE);
    }

    private void saveAndLoad() {
        String server = serverInput.getText().toString().trim();
        if (!validServer(server)) {
            serverInput.setError(debugBuild()
                    ? "ใช้ HTTPS หรือ HTTP เฉพาะ localhost/Android emulator"
                    : "ต้องใช้ HTTPS เท่านั้น");
            return;
        }
        server = normalizeServer(server);
        String origin = serverOrigin(server);
        String typed = tokenInput.getText().toString().trim();
        String token;
        if (!typed.isEmpty()) {
            token = typed;                                   // a token typed now belongs to the server typed with it
        } else if (!isConfigured()) {
            token = "";                                      // nothing stored: nothing that could follow the server
        } else if (origin.equals(prefs.getString(PREF_TOKEN_ORIGIN, ""))) {
            token = prefs.getString(PREF_TOKEN, "");         // blank = keep it, for its own server only
        } else {
            // A new server with no token of its own: refused, and nothing is
            // saved -- the stored token stays with its server, Emma keeps
            // running there, and a stranger at the panel cannot even take her
            // off the air by typing an address.
            tokenInput.setError(TOKEN_FOR_THIS_SERVER);
            return;
        }
        SharedPreferences.Editor edit = prefs.edit().putString(PREF_SERVER, server);
        if (token.isEmpty()) edit.remove(PREF_TOKEN).remove(PREF_TOKEN_ORIGIN);
        else edit.putString(PREF_TOKEN, token).putString(PREF_TOKEN_ORIGIN, origin);
        edit.apply();
        setupPanel.setVisibility(View.GONE);
        refreshGear();      // now configured: the gear leaves the customer face
        loadEmma();
    }

    private void loadEmma() {
        String server = normalizeServer(prefs.getString(PREF_SERVER, DEFAULT_SERVER));
        if (!validServer(server)) {
            prefs.edit().remove(PREF_SERVER).apply();
            showSetup();
            serverInput.setError(debugBuild()
                    ? "ใช้ HTTPS หรือ HTTP เฉพาะ localhost/Android emulator"
                    : "ต้องใช้ HTTPS เท่านั้น");
            return;
        }
        String token = prefs.getString(PREF_TOKEN, "");
        Uri base = Uri.parse(server);
        String origin = originOf(base);
        if (token != null && !token.isEmpty()
                && !origin.equals(prefs.getString(PREF_TOKEN_ORIGIN, ""))) {
            // The last line, whatever path moved the server: a token stored for
            // ANOTHER origin is forgotten, never sent. The page of the old
            // server is retired so nothing listens behind the setup panel.
            forgetToken();
            retirePage();
            showSetup();
            tokenInput.setError(TOKEN_FOR_THIS_SERVER);
            return;
        }
        allowedOrigin = origin;                                // the one origin trusted with mic + camera + in-app nav
        Uri.Builder page = base.buildUpon();
        String path = base.getPath() == null ? "" : base.getPath();
        if (!path.endsWith("/preview")) {
            page.path((path.endsWith("/") ? path : path + "/") + "preview");
        }
        page.appendQueryParameter("app", "1");
        // cam=1: stream the robot camera to /ws/camera. The chest screen used to
        // be Chrome's /?kiosk=1&cam=1, which is what fed the greeter
        // (FACE_CAMERA_SOURCE=robot); once /preview moved into this WebView and
        // held the only USB camera, nothing fed it. The page's upload is opt-in
        // by URL (a desk browser must never start sending its webcam), so the
        // robot app is where the opt-in belongs. A cam already written into the
        // configured server URL wins -- cam=0 there turns the stream off
        // without a new APK.
        if (base.getQueryParameter("cam") == null) page.appendQueryParameter("cam", "1");
        // "Emma Event" (owner 30 Oct, build.gradle -PemmaApp=event): the same
        // face, every call the event host (app/event_host.py on the server).
        if (isEventApp(activity)) page.appendQueryParameter("event", "1");
        if (token != null && !token.isEmpty()) page.appendQueryParameter("token", token);
        progress.setVisibility(View.VISIBLE);
        errorText.setVisibility(View.GONE);
        if (root != null) root.removeCallbacks(retryLoad);   // this load is the retry
        mainFrameFailed = false;
        webView.loadUrl(page.build().toString());
    }

    private boolean validServer(String value) {
        try {
            Uri uri = Uri.parse(value.trim());
            String host = uri.getHost();
            if (host == null || uri.getUserInfo() != null) return false;
            if ("https".equals(uri.getScheme())) return true;
            return debugBuild() && "http".equals(uri.getScheme()) && isDebugLoopback(host);
        } catch (Exception ignored) {
            return false;
        }
    }

    private boolean isDebugLoopback(String host) {
        return "localhost".equalsIgnoreCase(host)
                || "127.0.0.1".equals(host)
                || "::1".equals(host)
                || "10.0.2.2".equals(host);
    }

    private String normalizeServer(String value) {
        String server = value == null || value.trim().isEmpty() ? DEFAULT_SERVER : value.trim();
        while (server.endsWith("/")) server = server.substring(0, server.length() - 1);
        return server;
    }

    /** The origin in the form Chromium hands back (PermissionRequest.getOrigin,
     *  WebView.getUrl): scheme and host lowercased, the scheme's default port
     *  dropped. android.net.Uri keeps both exactly as typed, so a server saved
     *  as https://Emma-PC.local:8001 or https://emma.example:443 never equalled
     *  the page's own origin -- every mic and camera request was refused at the
     *  gate, with no toast, and the page blamed a permission Android had
     *  already granted. Every side of every comparison goes through here. */
    private static String originOf(Uri uri) {
        if (uri == null || uri.getScheme() == null || uri.getHost() == null) return "";
        String scheme = uri.getScheme().toLowerCase(Locale.ROOT);
        String host = uri.getHost().toLowerCase(Locale.ROOT);
        int port = uri.getPort();
        if (("https".equals(scheme) && port == 443) || ("http".equals(scheme) && port == 80)) port = -1;
        return scheme + "://" + host + (port >= 0 ? ":" + port : "");
    }

    private String serverOrigin(String server) {
        return originOf(Uri.parse(normalizeServer(server)));
    }

    /** Nothing is trusted until loadEmma names an origin: "" is what originOf
     *  returns for a URL with no host, and must never match it. */
    private boolean trusted(String origin) {
        return !allowedOrigin.isEmpty() && allowedOrigin.equals(origin);
    }

    private void forgetToken() {
        prefs.edit().remove(PREF_TOKEN).remove(PREF_TOKEN_ORIGIN).apply();
        refreshGear();
    }

    /** Stop the page that is up (its origin is no longer ours to trust), so the
     *  old server's call, scan and camera do not keep running behind setup. */
    private void retirePage() {
        allowedOrigin = "";
        if (root != null) root.removeCallbacks(retryLoad);   // no reload of a server just retired
        if (webView != null && webView.getUrl() != null) webView.loadUrl("about:blank");
    }

    /** Tokens stored before 2026-09-29 carry no record of their server. Bind
     *  one to the server it was stored beside, so an upgraded robot keeps
     *  working with no re-provisioning and the binding holds from here on. */
    private void bindLegacyToken() {
        if (isConfigured() && !prefs.contains(PREF_TOKEN_ORIGIN)) {
            prefs.edit()
                    .putString(PREF_TOKEN_ORIGIN, serverOrigin(prefs.getString(PREF_SERVER, DEFAULT_SERVER)))
                    .apply();
        }
    }

    /** Debug only: `adb shell am start -n com.emma.robot.control/.MainActivity
     *  --es server_url https://... --es ws_token ...` is how the owner
     *  provisions the robot. The launcher activity is exported, so any app on
     *  the robot can send the same extras -- which is why a server that moves
     *  to another origin WITHOUT a token in the same intent drops the stored
     *  token instead of carrying it along: the extras alone can no longer walk
     *  the token to another host. */
    private boolean consumeLaunchConfiguration(Intent intent) {
        if (intent == null) return false;
        if (!debugBuild()) {
            // MainActivity is exported for the launcher, so release builds must
            // never let another app redefine the origin trusted with the mic
            // and the camera.
            intent.removeExtra(PREF_SERVER);
            intent.removeExtra(PREF_TOKEN);
            return false;
        }
        String server = intent.getStringExtra(PREF_SERVER);
        String token = intent.getStringExtra(PREF_TOKEN);
        intent.removeExtra(PREF_SERVER);
        intent.removeExtra(PREF_TOKEN);
        if (server == null && token == null) return false;
        // A server that is not accepted rejects the whole intent: its token was
        // meant for that server, and binding it to the one stored instead would
        // send the new server's token to the old one.
        if (server != null && !validServer(server)) return false;
        String target = server != null ? normalizeServer(server)
                : normalizeServer(prefs.getString(PREF_SERVER, DEFAULT_SERVER));
        String origin = serverOrigin(target);
        SharedPreferences.Editor editor = prefs.edit();
        if (server != null) editor.putString(PREF_SERVER, target);
        if (token != null && !token.trim().isEmpty()) {
            editor.putString(PREF_TOKEN, token.trim()).putString(PREF_TOKEN_ORIGIN, origin);
        } else if (token != null || !origin.equals(prefs.getString(PREF_TOKEN_ORIGIN, ""))) {
            // an empty ws_token clears it; a new origin with no token drops it
            editor.remove(PREF_TOKEN).remove(PREF_TOKEN_ORIGIN);
        }
        editor.apply();
        return true;
    }

    private TextView text(String value, int sp, int color) {
        TextView view = new TextView(activity);
        view.setText(value);
        view.setTextSize(sp);
        view.setTextColor(color);
        return view;
    }

    private LinearLayout.LayoutParams matchWrap(int left, int top, int right, int bottom) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.setMargins(dp(left), dp(top), dp(right), dp(bottom));
        return params;
    }

    private int dp(int value) {
        return Math.round(value * activity.getResources().getDisplayMetrics().density);
    }
}
