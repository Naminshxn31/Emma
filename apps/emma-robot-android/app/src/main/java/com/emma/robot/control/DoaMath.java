package com.emma.robot.control;

import java.util.Locale;

/**
 * Direction-of-arrival FEATURES from the robot's mic array: GCC-PHAT time
 * lags between the four real microphones (docs/listening-focus-2026-10-09.md,
 * input 4). The server (app/doa.py) learns what the lags mean -- the mic
 * geometry is unknown, so nothing here pretends to know an angle.
 *
 * Pure Java on purpose (no android.*, no org.json), like BodyGestures and
 * NavRules: the tests COMPILE this file with the plain JDK and RUN it on
 * synthetic multichannel signals with known fractional delays, so the sign
 * of every lag, the PHAT normalisation and the sub-sample peak are checked
 * for real, not found as text. ArrayListener (android) only feeds it frames.
 *
 * Conventions shared with app/doa.py -- change them together or not at all:
 *  - pair order {0,1},{0,2},{0,3},{1,2},{1,3},{2,3} of the four PICKED
 *    channels (the server's `channels`, default 0-3);
 *  - lag[p] = arrival at mic j minus arrival at mic i, in samples at the
 *    capture rate (positive: the sound reached i first);
 *  - strength[p] = the PHAT correlation peak, 1.0 for one clean source with
 *    the whole band coherent, ~0 for noise.
 *
 * Not thread-safe: one instance per capture thread (it reuses its buffers --
 * a frame every 64 ms must not feed the garbage collector).
 */
final class DoaMath {
    static final int[][] PAIRS = {{0, 1}, {0, 2}, {0, 3}, {1, 2}, {1, 3}, {2, 3}};
    static final int MICS = 4;
    static final int CAPTURE_CHANNELS = 8;

    final int frame;
    final int rate;
    final double maxLag;
    final double step;
    private final int lo;
    private final int bins;
    private final int lagCount;
    private final double[] window;
    private final double[] twCos;
    private final double[] twSin;
    private final int[] reverse;
    /** steerCos[t][b] = cos(2 pi (lo+b) tau_t / frame), likewise sin. */
    private final float[][] steerCos;
    private final float[][] steerSin;
    private final double[][] re = new double[MICS][];
    private final double[][] im = new double[MICS][];
    private final double[] gRe;
    private final double[] gIm;
    private final double[] corr;

    /** One analysed frame. Reused by the caller. */
    static final class Result {
        final double[] lags = new double[PAIRS.length];
        final double[] strength = new double[PAIRS.length];
        double rms;
    }

    /**
     * @param frame  samples per frame, a power of two (Params clamps it)
     * @param maxLag lag search window, samples, +-
     * @param step   lag grid step, samples (refined to sub-step by a parabola)
     * @param fLo    PHAT band, Hz: below is hum and handling noise
     * @param fHi    above, a few-cm array aliases
     */
    DoaMath(int frame, int rate, double maxLag, double step, double fLo, double fHi) {
        if (frame < 64 || (frame & (frame - 1)) != 0) throw new IllegalArgumentException("frame must be a power of two");
        this.frame = frame;
        this.rate = rate;
        this.maxLag = maxLag;
        this.step = step;
        int first = Math.max(1, (int) Math.ceil(fLo * frame / rate));
        int last = Math.min(frame / 2 - 1, (int) Math.floor(fHi * frame / rate));
        if (last <= first) throw new IllegalArgumentException("empty band");
        lo = first;
        bins = last - first + 1;
        lagCount = (int) Math.round(2 * maxLag / step) + 1;
        window = new double[frame];
        for (int n = 0; n < frame; n++) window[n] = 0.5 - 0.5 * Math.cos(2 * Math.PI * n / frame);
        twCos = new double[frame / 2];
        twSin = new double[frame / 2];
        for (int k = 0; k < frame / 2; k++) {
            twCos[k] = Math.cos(-2 * Math.PI * k / frame);
            twSin[k] = Math.sin(-2 * Math.PI * k / frame);
        }
        reverse = new int[frame];
        int bitsN = Integer.numberOfTrailingZeros(frame);
        for (int n = 0; n < frame; n++) reverse[n] = Integer.reverse(n) >>> (32 - bitsN);
        steerCos = new float[lagCount][bins];
        steerSin = new float[lagCount][bins];
        for (int t = 0; t < lagCount; t++) {
            double tau = -maxLag + t * step;
            for (int b = 0; b < bins; b++) {
                double phase = 2 * Math.PI * (lo + b) * tau / frame;
                steerCos[t][b] = (float) Math.cos(phase);
                steerSin[t][b] = (float) Math.sin(phase);
            }
        }
        for (int m = 0; m < MICS; m++) {
            re[m] = new double[frame];
            im[m] = new double[frame];
        }
        gRe = new double[bins];
        gIm = new double[bins];
        corr = new double[lagCount];
    }

    /** Pull the four picked channels of one interleaved 8-channel frame into
     *  floats (full scale = 1). Returns the RMS of those four. */
    static double pick(short[] interleaved, int frames, int[] channels, float[][] out) {
        double sum = 0;
        for (int m = 0; m < MICS; m++) {
            int c = channels[m];
            float[] dst = out[m];
            for (int n = 0; n < frames; n++) {
                float v = interleaved[n * CAPTURE_CHANNELS + c] / 32768f;
                dst[n] = v;
                sum += v * v;
            }
        }
        return Math.sqrt(sum / (MICS * (double) frames));
    }

    /** Accumulate per-channel sums of squares of all eight channels (the
     *  status line's rms8: which channels are microphones is measured on the
     *  robot, not assumed) and return the peak |sample| of the frame. A
     *  real microphone never reads exactly zero; a frame of 0 is digital
     *  silence -- a capture the system muted (CLAUDE.md, peak=0.0000). */
    static int accumulate(short[] interleaved, int frames, double[] sumSquares8) {
        int peak = 0;
        for (int n = 0; n < frames; n++) {
            int base = n * CAPTURE_CHANNELS;
            for (int c = 0; c < CAPTURE_CHANNELS; c++) {
                int v = interleaved[base + c];
                sumSquares8[c] += (double) v * v;
                int a = v < 0 ? -v : v;
                if (a > peak) peak = a;
            }
        }
        return peak;
    }

    /** GCC-PHAT on four channels of `frame` samples each. */
    void analyze(float[][] x, Result out) {
        double sum = 0;
        for (int m = 0; m < MICS; m++) {
            double[] r = re[m];
            double[] i = im[m];
            float[] src = x[m];
            for (int n = 0; n < frame; n++) {
                sum += src[n] * src[n];
                r[n] = src[n] * window[n];
                i[n] = 0;
            }
            fft(r, i);
        }
        out.rms = Math.sqrt(sum / (MICS * (double) frame));
        for (int p = 0; p < PAIRS.length; p++) {
            int a = PAIRS[p][0];
            int b = PAIRS[p][1];
            int used = 0;
            for (int k = 0; k < bins; k++) {
                int bin = lo + k;
                double ar = re[a][bin], ai = im[a][bin], br = re[b][bin], bi = im[b][bin];
                // conj(X_a) * X_b: for x_b delayed by d this is |X|^2 e^{-i w d},
                // so the steered sum below peaks at tau = +d (b later = +lag)
                double cr = ar * br + ai * bi;
                double ci = ar * bi - ai * br;
                double mag = Math.hypot(cr, ci);
                if (mag > 1e-20) {
                    gRe[k] = cr / mag;      // PHAT: phase only, every bin one vote
                    gIm[k] = ci / mag;
                    used++;
                } else {
                    gRe[k] = 0;
                    gIm[k] = 0;
                }
            }
            if (used == 0) {
                out.lags[p] = 0;
                out.strength[p] = 0;
                continue;
            }
            int best = 0;
            for (int t = 0; t < lagCount; t++) {
                float[] c = steerCos[t];
                float[] s = steerSin[t];
                double acc = 0;
                for (int k = 0; k < bins; k++) acc += gRe[k] * c[k] - gIm[k] * s[k];
                corr[t] = acc / used;
                if (corr[t] > corr[best]) best = t;
            }
            double delta = 0;
            double peak = corr[best];
            if (best > 0 && best < lagCount - 1) {
                double y0 = corr[best - 1], y1 = corr[best], y2 = corr[best + 1];
                double den = y0 - 2 * y1 + y2;
                if (den < 0) {
                    delta = 0.5 * (y0 - y2) / den;
                    if (delta > 0.5) delta = 0.5;
                    if (delta < -0.5) delta = -0.5;
                    peak = y1 - 0.25 * (y0 - y2) * delta;
                }
            }
            out.lags[p] = -maxLag + (best + delta) * step;
            out.strength[p] = Math.max(0, Math.min(1, peak));
        }
    }

    /** In-place iterative radix-2 FFT (forward, e^{-i...}). */
    private void fft(double[] r, double[] i) {
        int n = frame;
        for (int a = 0; a < n; a++) {
            int b = reverse[a];
            if (b > a) {
                double t = r[a]; r[a] = r[b]; r[b] = t;
                t = i[a]; i[a] = i[b]; i[b] = t;
            }
        }
        for (int size = 2; size <= n; size <<= 1) {
            int half = size >> 1;
            int stride = n / size;
            for (int start = 0; start < n; start += size) {
                for (int k = 0; k < half; k++) {
                    double wr = twCos[k * stride], wi = twSin[k * stride];
                    int e = start + k, o = e + half;
                    double tr = r[o] * wr - i[o] * wi;
                    double ti = r[o] * wi + i[o] * wr;
                    r[o] = r[e] - tr;
                    i[o] = i[e] - ti;
                    r[e] += tr;
                    i[e] += ti;
                }
            }
        }
    }

    // ---- the wire (app/doa.py validates every field again) ----

    /** {"type":"doa","t_app_ms":..,"lags":[6],"strength":[6],"rms":..} --
     *  ~170 bytes, under the server's 1024-byte cap with room to spare. */
    static String frameJson(long tAppMs, Result r) {
        StringBuilder out = new StringBuilder(200);
        out.append("{\"type\":\"doa\",\"t_app_ms\":").append(tAppMs).append(",\"lags\":[");
        for (int p = 0; p < PAIRS.length; p++) {
            if (p > 0) out.append(',');
            out.append(String.format(Locale.US, "%.3f", r.lags[p]));
        }
        out.append("],\"strength\":[");
        for (int p = 0; p < PAIRS.length; p++) {
            if (p > 0) out.append(',');
            out.append(String.format(Locale.US, "%.3f", r.strength[p]));
        }
        out.append("],\"rms\":").append(String.format(Locale.US, "%.5f", r.rms)).append('}');
        return out.toString();
    }

    /** A JSON string literal: quotes, backslashes and control characters
     *  escaped (a USB product name is the device's own text). */
    static String quote(String value) {
        if (value == null) return "null";
        StringBuilder out = new StringBuilder(value.length() + 2).append('"');
        for (int k = 0; k < value.length() && k < 160; k++) {
            char ch = value.charAt(k);
            if (ch == '"' || ch == '\\') out.append('\\').append(ch);
            else if (ch < 0x20 || ch == 0x7f) out.append(String.format(Locale.US, "\\u%04x", (int) ch));
            else out.append(ch);
        }
        return out.append('"').toString();
    }

    /** The capture's state for the server log and the operator screen. */
    static String statusJson(String state, String source, String device, String detail, double[] rms8,
                             int others, Boolean othersSilenced, Boolean selfSilenced, long zeroMs,
                             long sent, long gated, int rate, int channels) {
        return statusJson(state, source, device, detail, rms8, others, othersSilenced, selfSilenced, zeroMs,
                sent, gated, rate, channels, null);
    }

    /** ...with the array audio's counters (ARRAY_AUDIO) appended: a
     *  `beam` object of non-negative integers and its mode, or nothing. */
    static String statusJson(String state, String source, String device, String detail, double[] rms8,
                             int others, Boolean othersSilenced, Boolean selfSilenced, long zeroMs,
                             long sent, long gated, int rate, int channels, long[] beam) {
        StringBuilder out = new StringBuilder(560);
        out.append("{\"type\":\"doa_status\",\"state\":").append(quote(state))
                .append(",\"source\":").append(quote(source))
                .append(",\"device\":").append(quote(device))
                .append(",\"detail\":").append(quote(detail))
                .append(",\"rms8\":");
        if (rms8 == null) {
            out.append("null");
        } else {
            out.append('[');
            for (int c = 0; c < rms8.length; c++) {
                if (c > 0) out.append(',');
                out.append(String.format(Locale.US, "%.5f", rms8[c]));
            }
            out.append(']');
        }
        out.append(",\"others\":").append(Math.max(0, others))
                .append(",\"others_silenced\":").append(othersSilenced == null ? "null" : othersSilenced.toString())
                .append(",\"self_silenced\":").append(selfSilenced == null ? "null" : selfSilenced.toString())
                .append(",\"zero_ms\":").append(Math.max(0, zeroMs))
                .append(",\"sent\":").append(Math.max(0, sent))
                .append(",\"gated\":").append(Math.max(0, gated))
                .append(",\"rate\":").append(Math.max(0, rate))
                .append(",\"channels\":").append(Math.max(0, channels));
        if (beam != null && beam.length == BEAM_FIELDS.length) {
            out.append(",\"beam\":{");
            for (int k = 0; k < beam.length; k++) {
                if (k > 0) out.append(',');
                out.append('"').append(BEAM_FIELDS[k]).append("\":").append(Math.max(0, beam[k]));
            }
            out.append('}');
        }
        return out.append('}').toString();
    }

    /** The order of the counters in statusJson's `beam` (app/array_audio.py
     *  reads exactly these names). mode: 0 ds, 1 mvdr. */
    static final String[] BEAM_FIELDS = {"mode", "gen", "sent", "dropped", "hops", "mvdr_hops", "quiet",
            "other", "target", "held", "guard_trips", "raw8_sent"};

    // ---- the server's parameters, clamped: a bad number must not become a
    //      giant FFT or a frame a second per pair of ears ----

    static final class Params {
        int frame = 1024;
        int[] channels = {0, 1, 2, 3};
        double maxLag = 16;
        double gateRms = 0.004;
        int maxFps = 16;
        double fLo = 200;
        double fHi = 4000;
        long statsMs = 2000;

        Params clamp(int rate) {
            int f = 512;
            while (f < 2048 && f < frame) f <<= 1;
            frame = f;
            boolean ok = channels != null && channels.length == MICS;
            if (ok) {
                for (int a = 0; a < MICS && ok; a++) {
                    if (channels[a] < 0 || channels[a] >= CAPTURE_CHANNELS) ok = false;
                    for (int b = 0; b < a && ok; b++) if (channels[a] == channels[b]) ok = false;
                }
            }
            if (!ok) channels = new int[]{0, 1, 2, 3};
            maxLag = clampD(maxLag, 2, 32);
            gateRms = clampD(gateRms, 0.0005, 0.1);
            maxFps = (int) clampD(maxFps, 1, 20);
            fLo = clampD(fLo, 50, 1000);
            fHi = clampD(fHi, fLo + 500, rate * 0.475);
            statsMs = (long) clampD(statsMs, 1000, 10000);
            return this;
        }

        private static double clampD(double v, double lo, double hi) {
            if (Double.isNaN(v)) return lo;
            return Math.max(lo, Math.min(hi, v));
        }
    }

    // ---- the link's rules (mirrors the face page's status link) ----

    static final int LINK_READY = 1;
    static final int LINK_OFF = 2;
    static final int LINK_STOP_FOR_GOOD = 3;
    static final int LINK_BUSY = 4;
    static final int LINK_BEAT = 5;
    static final int LINK_IGNORE = 0;

    /** What a server message means for the link. Refusals that a retry
     *  cannot fix stop the link for good (the token gate accepts-then-closes,
     *  and a page that redialled it every two seconds was CLAUDE.md's
     *  "ปิดทางเข้าเดียวไม่พอ"); `busy` is another robot holding the slot. */
    static int linkAction(String type, String code) {
        if ("doa_ready".equals(type)) return LINK_READY;
        if ("doa_off".equals(type)) return LINK_OFF;
        if ("doa_beat".equals(type)) return LINK_BEAT;
        if ("error".equals(type)) {
            if ("busy".equals(code)) return LINK_BUSY;
            return LINK_STOP_FOR_GOOD;      // unauthorized, not_robot, bad_face_token, anything new
        }
        return LINK_IGNORE;
    }

    /** Redial delay after `failures` drops in a row; reset only by doa_ready. */
    static long backoffMs(int failures) {
        int f = Math.max(0, Math.min(failures, 6));
        return Math.min(60000L, 1000L << f);
    }

    /** DOA_ENABLED off on the server: ask again this much later. */
    static final long OFF_RETRY_MS = 5 * 60 * 1000L;
    /** All-zero audio this long = the system silenced this capture. */
    static final long ZERO_LIMIT_MS = 3000L;
}
