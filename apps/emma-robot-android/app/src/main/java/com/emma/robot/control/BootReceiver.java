package com.emma.robot.control;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

/**
 * Opens the app after Android boots -- the event build only: declared in
 * src/event/AndroidManifest.xml, which build.gradle overlays for -PemmaApp=event.
 *
 * 4 Nov 2026: Android power-cycled itself three times (pstore: RK806 ON=0x20,
 * OFF=0x02, reason none = cut at the PMIC; cause unknown). The nav board survives,
 * the app does not reopen, and with no app there is no server (ServerLauncher
 * starts it), no Emma and no staff page -- at the launch event, with nobody holding
 * an adb cable. The server notices the interrupted walk when it comes back
 * (app/event_walk.py recover).
 *
 * Android 15 blocks an activity start from a receiver in the background, so this
 * goes through su, the way every other shell of this app does (RootShell): a root
 * `am start` is not a background app. Off a thread: su can wedge (review2 #25), and
 * goAsync keeps the process for the few tries. A plain build does not declare the
 * receiver: the sales room's app does not start doing this after a pull.
 */
public final class BootReceiver extends BroadcastReceiver {
    private static final String TAG = "EmmaBoot";
    /** su and the window manager may not be ready the moment the boot broadcast goes. */
    private static final int TRIES = 6;
    private static final long TRY_EVERY_MS = 5000L;

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || !Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) {
            return;
        }
        final PendingResult pending = goAsync();
        final String command = "am start -n " + context.getPackageName() + "/" + MainActivity.class.getName();
        new Thread(() -> {
            try {
                for (int i = 0; i < TRIES; i++) {
                    try {
                        RootShell.Result r = RootShell.run(command, RootShell.TIMEOUT_MS);
                        Log.i(TAG, "am start after boot, try " + (i + 1) + ": exit " + r.exit);
                        if (r.exit == 0) {
                            return;
                        }
                    } catch (Exception e) {
                        Log.w(TAG, "am start after boot failed: " + e);
                    }
                    Thread.sleep(TRY_EVERY_MS);
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            } finally {
                pending.finish();
            }
        }, "emma-boot").start();
    }
}
