package com.emma.robot.control;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONObject;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/**
 * Operator-recorded poses (§2c): SharedPreferences `emma_body_poses`, key
 * `poses_json`, format 1. A pose turns an unmapped id into operator_mapped.
 *
 * Saving goes through BodyGestures.checkSave, so a pose that is outside the
 * clamps cannot be stored; loading re-checks every frame against the limits
 * and DROPS a bad entry rather than repairing it -- a "fixed" pose is a pose
 * nobody watched move.
 */
final class BodyPoseStore {
    static final String PREFS = "emma_body_poses";
    static final String KEY = "poses_json";
    static final int FORMAT = 1;

    static final class Pose {
        final List<BodyGestures.Frame> frames;
        final boolean restAfter;
        final String recordedAt;
        final String note;

        Pose(List<BodyGestures.Frame> frames, boolean restAfter, String recordedAt, String note) {
            this.frames = Collections.unmodifiableList(new ArrayList<>(frames));
            this.restAfter = restAfter;
            this.recordedAt = recordedAt;
            this.note = note;
        }
    }

    private final SharedPreferences prefs;

    BodyPoseStore(Context context) {
        prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    synchronized boolean has(String id) {
        return get(id) != null;
    }

    /** The stored pose for an id, or null. Verified ids never have one. */
    synchronized Pose get(String id) {
        String builtIn = BodyGestures.builtInStatus(id);
        if (builtIn == null || BodyGestures.VERIFIED.equals(builtIn)) return null;
        try {
            JSONObject entry = poses().optJSONObject(id);
            if (entry == null) return null;
            JSONArray stored = entry.getJSONArray("frames");
            ArrayList<BodyGestures.Frame> frames = new ArrayList<>();
            for (int i = 0; i < stored.length(); i++) {
                JSONObject frame = stored.getJSONObject(i);
                JSONObject targets = frame.getJSONObject("targets");
                LinkedHashMap<Integer, Integer> map = new LinkedHashMap<>();
                JSONArray names = targets.names();
                if (names == null) return null;
                for (int n = 0; n < names.length(); n++) {
                    String name = names.getString(n);
                    map.put(Integer.parseInt(name), targets.getInt(name));
                }
                frames.add(new BodyGestures.Frame(frame.getInt("duration_ms"), map));
            }
            // Limits only here (every channel allowed); the allow-list is
            // re-checked by BodyController at play time.
            if (BodyGestures.checkFrames(frames, everyChannel(frames)) != null) return null;
            if (!entry.optBoolean("played_ok", false)) return null;
            return new Pose(frames, BodyGestures.restAfter(frames),
                    entry.optString("recorded_at", ""), entry.optString("note", ""));
        } catch (Exception ignored) {
            return null;
        }
    }

    /** Returns null when saved, or the Thai rule that refused it. */
    synchronized String save(String id, List<BodyGestures.Frame> frames, String note,
                             boolean playedOk, Set<Integer> allowedChannels) {
        String refusal = BodyGestures.checkSave(id, frames, allowedChannels, playedOk);
        if (refusal != null) return refusal;
        try {
            JSONArray stored = new JSONArray();
            for (BodyGestures.Frame frame : frames) {
                JSONObject targets = new JSONObject();
                for (java.util.Map.Entry<Integer, Integer> t : frame.targets.entrySet()) {
                    targets.put(String.valueOf(t.getKey()), t.getValue());
                }
                stored.put(new JSONObject().put("duration_ms", frame.durationMs).put("targets", targets));
            }
            JSONObject entry = new JSONObject()
                    .put("frames", stored)
                    .put("rest_after", BodyGestures.restAfter(frames))
                    .put("recorded_at", new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", Locale.US)
                            .format(new Date()))
                    .put("note", note == null ? "" : note)
                    .put("played_ok", true);
            JSONObject all = poses();
            all.put(id, entry);
            prefs.edit().putString(KEY, new JSONObject().put("format", FORMAT).put("poses", all).toString())
                    .apply();
            return null;
        } catch (Exception e) {
            return "บันทึกท่าไม่สำเร็จ: " + e.getMessage();
        }
    }

    private JSONObject poses() {
        try {
            JSONObject root = new JSONObject(prefs.getString(KEY, ""));
            if (root.optInt("format", -1) != FORMAT) return new JSONObject();
            JSONObject poses = root.optJSONObject("poses");
            return poses == null ? new JSONObject() : poses;
        } catch (Exception ignored) {
            return new JSONObject();
        }
    }

    private static Set<Integer> everyChannel(List<BodyGestures.Frame> frames) {
        java.util.HashSet<Integer> all = new java.util.HashSet<>();
        for (BodyGestures.Frame frame : frames) all.addAll(frame.targets.keySet());
        return all;
    }
}
