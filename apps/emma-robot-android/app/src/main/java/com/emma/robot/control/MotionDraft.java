package com.emma.robot.control;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** A local, non-flash sequence made only from targets the operator explicitly sent. */
final class MotionDraft {
    static final int DEFAULT_FRAME_MS = 3000;
    private final LinkedHashMap<Integer, Integer> commandedTargets = new LinkedHashMap<>();
    private final ArrayList<ArmController.PoseFrame> frames = new ArrayList<>();
    /** §2c rule 5: a draft may be saved as a voice pose only if it was played
     *  ("เล่นร่างท่า") since it was last edited -- somebody watched it move.
     *  Kept in memory only: after an app restart it has to be watched again.
     *
     *  Review2 #22: "played" means the playback RAN TO THE END. It used to be
     *  set the moment playback was queued, so pressing B after the first
     *  keyframe because it looked wrong (or a dead arm board) still unlocked
     *  the save, with played_ok:true -- which makes the id voice-playable.
     *  Set only on the UI thread (the playback's callbacks are posted there). */
    private volatile boolean playedSinceEdit = false;
    /** Bumped by every edit: a playback that finishes after the draft
     *  changed under it watched the OLD frames, not these. */
    private volatile int edits = 0;

    void rememberTarget(int channel, int pulse) {
        validateTarget(channel, pulse);
        commandedTargets.put(channel, pulse);
    }

    int targetFor(int channel, int fallback) {
        Integer value = commandedTargets.get(channel);
        return value == null ? fallback : value;
    }

    ArmController.PoseFrame captureFrame() {
        return captureFrame(DEFAULT_FRAME_MS);
    }

    /** The studio's "เวลาคีย์เฟรม" (owner 15 Oct: 3 s frames made her
     *  explaining hand slow). Only the voice-gesture range: a draft frame
     *  outside it could never be saved as a pose anyway. */
    ArmController.PoseFrame captureFrame(int durationMs) {
        if (commandedTargets.isEmpty()) {
            throw new IllegalStateException("ยังไม่มีค่าช่องที่ส่งจริงให้จำเป็นคีย์เฟรม");
        }
        if (durationMs < BodyGestures.MIN_FRAME_MS || durationMs > BodyGestures.MAX_FRAME_MS) {
            throw new IllegalArgumentException("เวลาคีย์เฟรมต้องอยู่ระหว่าง "
                    + BodyGestures.MIN_FRAME_MS + "–" + BodyGestures.MAX_FRAME_MS + " ms");
        }
        ArmController.PoseFrame frame = new ArmController.PoseFrame(
                commandedTargets, durationMs);
        frames.add(frame);
        playedSinceEdit = false;
        edits++;
        return frame;
    }

    List<ArmController.PoseFrame> frames() {
        return Collections.unmodifiableList(new ArrayList<>(frames));
    }

    int frameCount() {
        return frames.size();
    }

    /** "1.0+0.8+0.8 วิ" for the draft line, or "" with no frames. */
    String frameLengths() {
        StringBuilder out = new StringBuilder();
        for (ArmController.PoseFrame frame : frames) {
            if (out.length() > 0) out.append('+');
            out.append(String.format(java.util.Locale.US, "%.1f", frame.durationMs / 1000.0));
        }
        return out.length() == 0 ? "" : out + " วิ";
    }

    int targetCount() {
        return commandedTargets.size();
    }

    void clearCommandedTargets() {
        commandedTargets.clear();
    }

    void clear() {
        frames.clear();
        commandedTargets.clear();
        playedSinceEdit = false;
        edits++;
    }

    /** Review2 #22: a new playback starts. Until it finishes, the draft
     *  counts as not watched -- only the latest playback, complete, counts. */
    int startPlayback() {
        playedSinceEdit = false;
        return edits;
    }

    /** From the playback's onDone only (every frame sent and timed out): the
     *  draft counts as watched if it was not edited since startPlayback. */
    void markPlayed(int editsAtStart) {
        playedSinceEdit = editsAtStart == edits && !frames.isEmpty();
    }

    /** A playback that was stopped or aborted: not watched. */
    void playbackCut() {
        playedSinceEdit = false;
    }

    boolean playedSinceEdit() {
        return playedSinceEdit;
    }

    String toJson() {
        try {
            JSONObject root = new JSONObject();
            JSONArray storedFrames = new JSONArray();
            for (ArmController.PoseFrame frame : frames) {
                JSONObject storedFrame = new JSONObject();
                storedFrame.put("duration_ms", frame.durationMs);
                JSONObject targets = new JSONObject();
                for (Map.Entry<Integer, Integer> target : frame.targets.entrySet()) {
                    targets.put(String.valueOf(target.getKey()), target.getValue());
                }
                storedFrame.put("targets", targets);
                storedFrames.put(storedFrame);
            }
            JSONObject liveTargets = new JSONObject();
            for (Map.Entry<Integer, Integer> target : commandedTargets.entrySet()) {
                liveTargets.put(String.valueOf(target.getKey()), target.getValue());
            }
            root.put("format", 2);
            root.put("frames", storedFrames);
            root.put("commanded_targets", liveTargets);
            return root.toString();
        } catch (JSONException error) {
            throw new IllegalStateException("บันทึกร่างท่าเป็น JSON ไม่สำเร็จ", error);
        }
    }

    static MotionDraft fromJson(String source) {
        MotionDraft draft = new MotionDraft();
        if (source == null || source.trim().isEmpty()) return draft;
        try {
            JSONObject root = new JSONObject(source);
            int format = root.optInt("format", -1);
            if (format != 1 && format != 2) return draft;
            JSONArray storedFrames = root.optJSONArray("frames");
            if (storedFrames == null) return draft;
            for (int index = 0; index < storedFrames.length(); index++) {
                JSONObject storedFrame = storedFrames.getJSONObject(index);
                int durationMs = storedFrame.getInt("duration_ms");
                JSONObject storedTargets = storedFrame.getJSONObject("targets");
                LinkedHashMap<Integer, Integer> targets = new LinkedHashMap<>();
                JSONArray names = storedTargets.names();
                if (names == null) continue;
                for (int nameIndex = 0; nameIndex < names.length(); nameIndex++) {
                    String name = names.getString(nameIndex);
                    int channel = Integer.parseInt(name);
                    int pulse = storedTargets.getInt(name);
                    validateTarget(channel, pulse);
                    targets.put(channel, pulse);
                    if (format == 1) draft.commandedTargets.put(channel, pulse);
                }
                draft.frames.add(new ArmController.PoseFrame(targets, durationMs));
            }
            if (format == 2) {
                JSONObject storedLiveTargets = root.optJSONObject("commanded_targets");
                if (storedLiveTargets != null) {
                    JSONArray names = storedLiveTargets.names();
                    if (names != null) {
                        for (int index = 0; index < names.length(); index++) {
                            String name = names.getString(index);
                            int channel = Integer.parseInt(name);
                            int pulse = storedLiveTargets.getInt(name);
                            validateTarget(channel, pulse);
                            draft.commandedTargets.put(channel, pulse);
                        }
                    }
                }
            }
        } catch (Exception ignored) {
            return new MotionDraft();
        }
        return draft;
    }

    private static void validateTarget(int channel, int pulse) {
        if (channel < 1 || channel > 20) {
            throw new IllegalArgumentException("ช่องเซอร์โวต้องอยู่ระหว่าง 1–20");
        }
        if (pulse < 500 || pulse > 2500) {
            throw new IllegalArgumentException("ค่าเซอร์โวต้องอยู่ระหว่าง 500–2500");
        }
    }
}
