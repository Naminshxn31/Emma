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

    void rememberTarget(int channel, int pulse) {
        validateTarget(channel, pulse);
        commandedTargets.put(channel, pulse);
    }

    int targetFor(int channel, int fallback) {
        Integer value = commandedTargets.get(channel);
        return value == null ? fallback : value;
    }

    ArmController.PoseFrame captureFrame() {
        if (commandedTargets.isEmpty()) {
            throw new IllegalStateException("ยังไม่มีค่าช่องที่ส่งจริงให้จำเป็นคีย์เฟรม");
        }
        ArmController.PoseFrame frame = new ArmController.PoseFrame(
                commandedTargets, DEFAULT_FRAME_MS);
        frames.add(frame);
        return frame;
    }

    List<ArmController.PoseFrame> frames() {
        return Collections.unmodifiableList(new ArrayList<>(frames));
    }

    int frameCount() {
        return frames.size();
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
