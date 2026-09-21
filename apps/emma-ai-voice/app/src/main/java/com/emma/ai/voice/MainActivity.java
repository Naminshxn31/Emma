package com.emma.ai.voice;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.media.AudioManager;
import android.net.Uri;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
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

import java.util.ArrayList;
import java.util.List;

public final class MainActivity extends Activity {
    private static final String PREFS = "emma_ai_voice";
    private static final String PREF_SERVER = "server_url";
    private static final String PREF_TOKEN = "ws_token";
    private static final String DEFAULT_SERVER = "https://127.0.0.1:8001";
    private static final int MIC_PERMISSION_REQUEST = 901;

    private SharedPreferences prefs;
    private WebView webView;
    private LinearLayout setupPanel;
    private EditText serverInput;
    private EditText tokenInput;
    private TextView errorText;
    private ProgressBar progress;
    private PermissionRequest pendingWebPermission;
    private String allowedOrigin = "";

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().setStatusBarColor(Color.rgb(8, 18, 31));
        getWindow().setNavigationBarColor(Color.rgb(5, 11, 18));
        setVolumeControlStream(AudioManager.STREAM_MUSIC);
        hideNavigationBar();
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        consumeLaunchConfiguration(getIntent());
        setContentView(buildUi());

        String token = prefs.getString(PREF_TOKEN, "");
        if (token == null || token.trim().isEmpty()) showSetup();
        else loadEmma();
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) hideNavigationBar();
    }

    private void hideNavigationBar() {
        getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                        | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                        | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        boolean changed = consumeLaunchConfiguration(intent);
        if (changed && webView != null) loadEmma();
    }

    private boolean consumeLaunchConfiguration(Intent intent) {
        if (intent == null) return false;
        if (!BuildConfig.DEBUG) {
            // MainActivity must be exported for the launcher, so release builds
            // must never let another app redefine the origin trusted with mic.
            intent.removeExtra(PREF_SERVER);
            intent.removeExtra(PREF_TOKEN);
            return false;
        }
        String server = intent.getStringExtra(PREF_SERVER);
        String token = intent.getStringExtra(PREF_TOKEN);
        SharedPreferences.Editor editor = prefs.edit();
        boolean changed = false;
        if (server != null && validServer(server)) {
            editor.putString(PREF_SERVER, normalizeServer(server));
            changed = true;
        }
        if (token != null) {
            editor.putString(PREF_TOKEN, token.trim());
            changed = true;
        }
        if (changed) editor.apply();
        intent.removeExtra(PREF_SERVER);
        intent.removeExtra(PREF_TOKEN);
        return changed;
    }

    private View buildUi() {
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.rgb(5, 11, 18));

        webView = new WebView(this);
        configureWebView();
        root.addView(webView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        progress = new ProgressBar(this);
        FrameLayout.LayoutParams progressParams = new FrameLayout.LayoutParams(dp(38), dp(38), Gravity.CENTER);
        root.addView(progress, progressParams);

        errorText = text("", 16, 0xFFFFA7B3);
        errorText.setGravity(Gravity.CENTER);
        errorText.setPadding(dp(24), dp(18), dp(24), dp(18));
        errorText.setBackgroundColor(Color.argb(225, 23, 18, 28));
        errorText.setVisibility(View.GONE);
        errorText.setOnClickListener(v -> loadEmma());
        FrameLayout.LayoutParams errorParams = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER);
        errorParams.setMargins(dp(28), 0, dp(28), 0);
        root.addView(errorText, errorParams);

        ImageButton settings = new ImageButton(this);
        settings.setImageResource(com.emma.ai.voice.R.drawable.ic_settings);
        settings.setBackgroundColor(Color.argb(94, 8, 18, 31));
        settings.setContentDescription("ตั้งค่าการเชื่อมต่อ Emma");
        settings.setPadding(dp(12), dp(12), dp(12), dp(12));
        settings.setOnClickListener(v -> showSetup());
        FrameLayout.LayoutParams settingsParams = new FrameLayout.LayoutParams(dp(48), dp(48), Gravity.TOP | Gravity.END);
        settingsParams.setMargins(0, dp(12), dp(12), 0);
        root.addView(settings, settingsParams);

        setupPanel = buildSetupPanel();
        setupPanel.setVisibility(View.GONE);
        root.addView(setupPanel, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        return root;
    }

    private void configureWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setUserAgentString(settings.getUserAgentString() + " EmmaAIVoice/0.1");
        WebView.setWebContentsDebuggingEnabled(
                (getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                if (!request.isForMainFrame()) return false;
                Uri target = request.getUrl();
                String origin = originOf(target);
                if (origin.equals(allowedOrigin)) return false;
                if ("http".equals(target.getScheme()) || "https".equals(target.getScheme())) {
                    startActivity(new Intent(Intent.ACTION_VIEW, target));
                }
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                progress.setVisibility(View.GONE);
                errorText.setVisibility(View.GONE);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (!request.isForMainFrame()) return;
                progress.setVisibility(View.GONE);
                errorText.setText("เชื่อมต่อ Emma ไม่ได้\nตรวจว่าเซิร์ฟเวอร์ทำงานและที่อยู่ถูกต้อง แล้วกดลองใหม่");
                errorText.setVisibility(View.VISIBLE);
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(PermissionRequest request) {
                runOnUiThread(() -> handleWebPermission(request));
            }
        });
    }

    private void handleWebPermission(PermissionRequest request) {
        if (!originOf(request.getOrigin()).equals(allowedOrigin)) {
            request.deny();
            return;
        }
        List<String> allowed = new ArrayList<>();
        for (String resource : request.getResources()) {
            if (PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(resource)) allowed.add(resource);
        }
        if (allowed.isEmpty()) {
            request.deny();
            return;
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            request.grant(allowed.toArray(new String[0]));
            return;
        }
        pendingWebPermission = request;
        requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, MIC_PERMISSION_REQUEST);
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (requestCode != MIC_PERMISSION_REQUEST || pendingWebPermission == null) return;
        PermissionRequest request = pendingWebPermission;
        pendingWebPermission = null;
        String currentOrigin = webView == null || webView.getUrl() == null
                ? "" : originOf(Uri.parse(webView.getUrl()));
        if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED
                && originOf(request.getOrigin()).equals(allowedOrigin)
                && currentOrigin.equals(allowedOrigin)) {
            request.grant(new String[]{PermissionRequest.RESOURCE_AUDIO_CAPTURE});
        } else {
            request.deny();
            Toast.makeText(this, "Emma ต้องใช้ไมโครโฟนเพื่อรับเสียงพูด", Toast.LENGTH_LONG).show();
        }
    }

    private LinearLayout buildSetupPanel() {
        LinearLayout outer = new LinearLayout(this);
        outer.setOrientation(LinearLayout.VERTICAL);
        outer.setGravity(Gravity.CENTER);
        outer.setPadding(dp(28), dp(32), dp(28), dp(32));
        outer.setBackgroundColor(Color.rgb(5, 11, 18));

        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(24), dp(24), dp(24), dp(24));
        card.setBackgroundColor(Color.rgb(13, 27, 41));
        outer.addView(card, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        TextView title = text("เชื่อมต่อ Emma AI Voice", 24, Color.WHITE);
        card.addView(title, matchWrap(0, 0, 0, 8));
        TextView detail = text("ใส่ที่อยู่เซิร์ฟเวอร์ condo-voice และ WS_TOKEN ข้อมูลนี้เก็บในพื้นที่ส่วนตัวของแอป", 15, 0xFFB9C7D2);
        card.addView(detail, matchWrap(0, 0, 0, 18));

        serverInput = new EditText(this);
        serverInput.setHint("https://127.0.0.1:8001");
        serverInput.setTextColor(Color.WHITE);
        serverInput.setHintTextColor(0xFF728392);
        serverInput.setSingleLine(true);
        serverInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        card.addView(serverInput, matchWrap(0, 0, 0, 12));

        tokenInput = new EditText(this);
        tokenInput.setHint("WS_TOKEN");
        tokenInput.setTextColor(Color.WHITE);
        tokenInput.setHintTextColor(0xFF728392);
        tokenInput.setSingleLine(true);
        tokenInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        card.addView(tokenInput, matchWrap(0, 0, 0, 18));

        Button connect = new Button(this);
        connect.setText("เชื่อมต่อและเปิด Emma");
        connect.setTextColor(0xFF05110C);
        connect.setBackgroundColor(0xFF67E6B3);
        connect.setOnClickListener(v -> saveAndLoad());
        card.addView(connect, matchWrap(0, 0, 0, 10));

        Button cancel = new Button(this);
        cancel.setText("ยกเลิก");
        cancel.setTextColor(0xFFD8E2EA);
        cancel.setBackgroundColor(0xFF1B3042);
        cancel.setOnClickListener(v -> {
            if (webView.getUrl() != null) setupPanel.setVisibility(View.GONE);
        });
        card.addView(cancel, matchWrap(0, 0, 0, 0));
        return outer;
    }

    private void showSetup() {
        serverInput.setText(prefs.getString(PREF_SERVER, DEFAULT_SERVER));
        tokenInput.setText(prefs.getString(PREF_TOKEN, ""));
        setupPanel.setVisibility(View.VISIBLE);
    }

    private void saveAndLoad() {
        String server = serverInput.getText().toString().trim();
        if (!validServer(server)) {
            serverInput.setError(BuildConfig.DEBUG
                    ? "ใช้ HTTPS หรือ HTTP เฉพาะ localhost/Android emulator"
                    : "ต้องใช้ HTTPS เท่านั้น");
            return;
        }
        prefs.edit()
                .putString(PREF_SERVER, normalizeServer(server))
                .putString(PREF_TOKEN, tokenInput.getText().toString().trim())
                .apply();
        setupPanel.setVisibility(View.GONE);
        loadEmma();
    }

    private void loadEmma() {
        String server = normalizeServer(prefs.getString(PREF_SERVER, DEFAULT_SERVER));
        if (!validServer(server)) {
            prefs.edit().remove(PREF_SERVER).apply();
            showSetup();
            serverInput.setError(BuildConfig.DEBUG
                    ? "ใช้ HTTPS หรือ HTTP เฉพาะ localhost/Android emulator"
                    : "ต้องใช้ HTTPS เท่านั้น");
            return;
        }
        String token = prefs.getString(PREF_TOKEN, "");
        Uri base = Uri.parse(server);
        allowedOrigin = originOf(base);
        Uri.Builder page = base.buildUpon();
        String path = base.getPath() == null ? "" : base.getPath();
        if (!path.endsWith("/preview")) {
            page.path((path.endsWith("/") ? path : path + "/") + "preview");
        }
        page.appendQueryParameter("app", "1");
        page.appendQueryParameter("autoPose", "0");
        if (token != null && !token.isEmpty()) page.appendQueryParameter("token", token);
        progress.setVisibility(View.VISIBLE);
        errorText.setVisibility(View.GONE);
        webView.loadUrl(page.build().toString());
    }

    private boolean validServer(String value) {
        try {
            Uri uri = Uri.parse(value.trim());
            String host = uri.getHost();
            if (host == null || uri.getUserInfo() != null) return false;
            if ("https".equals(uri.getScheme())) return true;
            return BuildConfig.DEBUG && "http".equals(uri.getScheme())
                    && isDebugLoopback(host);
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

    private String originOf(Uri uri) {
        if (uri == null || uri.getScheme() == null || uri.getHost() == null) return "";
        int port = uri.getPort();
        return uri.getScheme() + "://" + uri.getHost() + (port >= 0 ? ":" + port : "");
    }

    @Override
    protected void onPause() {
        if (webView != null) {
            webView.evaluateJavascript("window.dispatchEvent(new Event('emma-native-pause'))", null);
            webView.onPause();
        }
        super.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (webView != null) webView.onResume();
    }

    @Override
    protected void onDestroy() {
        if (pendingWebPermission != null) pendingWebPermission.deny();
        pendingWebPermission = null;
        if (webView != null) {
            webView.loadUrl("about:blank");
            webView.destroy();
        }
        super.onDestroy();
    }

    private TextView text(String value, int sp, int color) {
        TextView view = new TextView(this);
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
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
