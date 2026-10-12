package com.emma.panel.tools;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.os.Build;
import android.os.Bundle;
import android.os.UserManager;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.util.Collections;

/**
 * A tiny, single-purpose tool for the sales-room Android panels whose Settings
 * hide the Developer options menu (so ADB cannot be turned on the normal way).
 *
 * It does not control the panel and holds no dangerous permission. It only
 * tries to OPEN the hidden Developer-options screen directly -- first through
 * the public {@link Settings#ACTION_APPLICATION_DEVELOPMENT_SETTINGS} action,
 * then through the explicit Settings activities as fallbacks. On many ROMs the
 * screen still exists even when its menu entry is removed; if it opens, a
 * person turns on USB / wireless debugging there and reads back the panel's IP,
 * after which the panel can be driven over ADB with no app left running here.
 *
 * If every attempt fails, this ROM has genuinely removed the screen and the
 * only remaining path to control is a native agent app (Accessibility +
 * MediaProjection) -- which is why this stays a throwaway probe, not that app.
 */
public final class MainActivity extends Activity {

    private TextView status;
    private TextView info;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        setContentView(buildUi());
    }

    @Override
    protected void onResume() {
        super.onResume();
        // Refresh so enabling developer mode and coming back shows "เปิดแล้ว".
        if (info != null) info.setText(deviceInfo());
    }

    private View buildUi() {
        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(Color.rgb(9, 14, 23));

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(28), dp(28), dp(28), dp(28));
        scroll.addView(root);

        root.addView(text("Emma Panel Tools", 26, Color.WHITE), gap(0, 0, 0, 6));
        root.addView(text("เปิด Developer Options → เปิด Wireless debugging → จด IP:PORT + รหัส pair ให้ทีมงาน",
                16, 0xFFB9C7D2), gap(0, 0, 0, 18));

        info = text(deviceInfo(), 15, 0xFF9FE8C9);
        root.addView(info, gap(0, 0, 0, 20));

        root.addView(bigButton("①  เปิด Developer Options", 0xFF67E6B3, 0xFF05110C,
                v -> openDeveloperOptions()), gap(0, 0, 0, 12));
        root.addView(bigButton("②  เปิด Settings ทั่วไป", 0xFF1B3042, 0xFFE8F0F6,
                v -> tryOpen(new Intent(Settings.ACTION_SETTINGS),
                        "เปิด Settings แล้ว — หา Developer options / About เอง")), gap(0, 0, 0, 12));
        root.addView(bigButton("③  เปิด About (หา Build number)", 0xFF1B3042, 0xFFE8F0F6,
                v -> tryOpen(new Intent(Settings.ACTION_DEVICE_INFO_SETTINGS),
                        "เปิดหน้าเกี่ยวกับเครื่องแล้ว — แตะ Build number/Version 7 ครั้ง")), gap(0, 0, 0, 18));

        status = text("", 15, 0xFFFFC857);
        root.addView(status, gap(0, 0, 0, 0));

        return scroll;
    }

    private void openDeveloperOptions() {
        // A Device Owner/MDM can block debugging outright -- opening the screen
        // would be a dead-end (the USB-debugging toggle is greyed), so say so and
        // route to the native path instead of chasing buttons.
        if (debuggingBlocked()) {
            say("⚠ เครื่องนี้ล็อก ADB ไว้ที่ระดับนโยบาย (MDM/Device Owner)\n"
                    + "เข้าหน้า Developer options ได้ก็เปิด USB/Wireless debugging ไม่ได้ (ปุ่มจะเทา)\n"
                    + "→ ต้องไปทางแอป control native (Accessibility) แจ้งทีมงานได้เลย");
            return;
        }
        // Public action first (works on many ROMs even when the menu is hidden);
        // then the explicit activities as a long shot on odd ROMs.
        Intent[] tries = new Intent[]{
                new Intent(Settings.ACTION_APPLICATION_DEVELOPMENT_SETTINGS),
                new Intent().setClassName("com.android.settings",
                        "com.android.settings.Settings$DevelopmentSettingsDashboardActivity"),
                new Intent().setClassName("com.android.settings",
                        "com.android.settings.DevelopmentSettings"),
        };
        for (Intent intent : tries) {
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            try {
                startActivity(intent);
                // startActivity not throwing does NOT prove the screen is usable:
                // a locked ROM may open it then bounce back or show it greyed. So
                // do not claim success -- tell the person exactly what to look for.
                say("ลองเปิดหน้า Developer Options แล้ว:\n"
                        + "• เปิดขึ้นจริง → เปิด Wireless debugging แล้วจด IP:PORT + รหัส pair กลับมา\n"
                        + "  (ถ้ามีสาย USB ต่อคอมได้ ใช้ USB debugging ก็พอ)\n"
                        + "• เด้งกลับ/ว่างเปล่า → ROM บล็อก: กดปุ่ม ③ แตะ Build number 7 ครั้งก่อน แล้วลอง ① ใหม่\n"
                        + "  ยังไม่ได้ → แจ้งทีมงานไปทางแอป native");
                return;
            } catch (Exception ignored) {
                // try the next one
            }
        }
        say("เปิด Developer Options ไม่ได้เลยบนเครื่องนี้ — ROM ถอด/ล็อกหน้านี้\n"
                + "→ ต้องไปทางแอป control native (Accessibility) แจ้งทีมงาน");
    }

    private void tryOpen(Intent intent, String ok) {
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            startActivity(intent);
            say(ok);
        } catch (Exception e) {
            say("เปิดหน้านี้ไม่ได้: " + e.getClass().getSimpleName());
        }
    }

    private String deviceInfo() {
        boolean devOn = Settings.Global.getInt(getContentResolver(),
                Settings.Global.DEVELOPMENT_SETTINGS_ENABLED, 0) == 1;
        return "รุ่น: " + Build.MODEL + "  (" + Build.MANUFACTURER + ")\n"
                + "Android: " + Build.VERSION.RELEASE + "  (SDK " + Build.VERSION.SDK_INT + ")\n"
                + "IP ในวง: " + lanIp() + "\n"
                + "Developer mode: " + (devOn ? "เปิดแล้ว" : "ยังไม่เปิด") + "\n"
                + "ADB โดยนโยบาย: " + (debuggingBlocked() ? "⚠ ถูกล็อก (MDM/Device Owner)" : "ไม่ถูกล็อก");
    }

    /** True when a Device Owner/MDM blocks debugging features, in which case the
     *  USB/Wireless debugging toggle is greyed even if Developer options opens.
     *  Permission-free read. */
    private boolean debuggingBlocked() {
        try {
            UserManager um = (UserManager) getSystemService(USER_SERVICE);
            return um != null && um.hasUserRestriction(UserManager.DISALLOW_DEBUGGING_FEATURES);
        } catch (Exception ignored) {
            return false;
        }
    }

    /** The first site-local IPv4 (192.168/10./172.16-31) on an up interface. */
    private String lanIp() {
        try {
            for (NetworkInterface nif : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (!nif.isUp() || nif.isLoopback()) continue;
                for (InetAddress addr : Collections.list(nif.getInetAddresses())) {
                    if (addr instanceof Inet4Address && addr.isSiteLocalAddress()) {
                        return addr.getHostAddress();
                    }
                }
            }
        } catch (Exception ignored) {
        }
        return "(หา IP ไม่เจอ — เช็คว่าต่อ WiFi อยู่)";
    }

    private void say(String message) {
        if (status != null) status.setText(message);
    }

    // --- tiny view helpers (framework-only, no androidx) ---------------------

    private TextView text(String value, int sp, int color) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(sp);
        view.setTextColor(color);
        return view;
    }

    private Button bigButton(String label, int bg, int fg, View.OnClickListener onClick) {
        Button button = new Button(this);
        button.setText(label);
        button.setAllCaps(false);
        button.setTextSize(18);
        button.setTextColor(fg);
        button.setBackgroundColor(bg);
        button.setPadding(dp(16), dp(18), dp(16), dp(18));
        button.setOnClickListener(onClick);
        return button;
    }

    private LinearLayout.LayoutParams gap(int l, int t, int r, int b) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.setMargins(dp(l), dp(t), dp(r), dp(b));
        params.gravity = Gravity.CENTER_HORIZONTAL;
        return params;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
