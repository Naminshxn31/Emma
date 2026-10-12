package com.emma.robot.control;

import java.util.Arrays;

/**
 * Beamformed conversation audio from the robot's mic array (ARRAY_AUDIO,
 * app/array_audio.py): channels 0-3 of the Bothlent array, one
 * mono 16 kHz stream steered toward the person the server says is talking
 * to the robot (the focus target), sent up the DOA link as binary frames.
 *
 * The geometry is still unknown, so nothing here knows where a microphone
 * is. The server sends the target's ARRIVAL TIMES (samples, mic 0 = 0), read
 * off the DOA calibration it learned from faces (app/doa.py: lags = c +
 * g sin(bearing)); a steering vector needs nothing else.
 *
 * Two beams, one engine (STFT, 512-point sqrt-Hann, hop 256 = 16 ms, 32 ms
 * algorithmic latency):
 *  - DS (default): delay-and-sum with fractional delays -- w = d / 4. It
 *    cannot hurt the target (distortionless for any mic gain mismatch).
 *    Measured on synthetic 4-mic rooms (tests/test_array_audio.py): small --
 *    a ~6 cm array has almost no directivity at speech frequencies.
 *  - MVDR (ARRAY_AUDIO_BEAM=mvdr): w = R^-1 d / (d^H R^-1 d), the noise
 *    covariance R learned ONLY on hops the target is not talking in: quiet
 *    hops (the room's floor) and hops whose GCC-PHAT lags say one source
 *    that is NOT the target (another talker). Hops the lags put on the
 *    target, and mixtures, teach nothing -- a target in R is cancelled by
 *    its own beam. Diagonal loading, per-mic gain equalisation (a ±1.5 dB
 *    sensitivity mismatch cost the target 1.6-2.8 dB without it), DS until
 *    the estimate is warm, and R forgotten when the target moves.
 * When the server has no target (no calibration, nobody in front) the
 * output is the server's "unsteered" weights: mic 0 alone by default -- a
 * plain average of four unaligned mics comb-filters the voice (measured log
 * spectral distance 12-19 dB on the synthetic circle, against 0.2 for a
 * steered sum).
 *
 * Pure Java (no android.*): the tests COMPILE this file and RUN it on
 * synthetic multi-talker rooms, measuring the SIR/SNR the robot would get.
 * Not thread-safe: the capture thread owns it; steering arrives as one
 * immutable {@link Steer} through a volatile field read once per hop.
 */
final class ArrayBeam {
    static final int MICS = 4;
    static final int N = 512;
    static final int HOP = 256;
    static final int MODE_DS = 0;
    static final int MODE_MVDR = 1;

    // ---- the wire: binary frames on the DOA link (app/array_audio.py parses) ----
    static final int MAGIC = 0xEA;
    /** One mono 16 kHz PCM16 chunk of the beam. */
    static final int KIND_BEAM = 1;
    /** One block of the RAW 8-channel capture (the channels 4-7 test only). */
    static final int KIND_RAW8 = 2;
    static final int HEADER = 8;
    /** Beam audio goes up one hop at a time (16 ms): the server falls back to
     *  the page after ARRAY_AUDIO_STALE_MS (60) without a chunk, and the hole
     *  that leaves stays under 100 ms only with chunks this short. */
    static final int CHUNK = HOP;

    // ---- MVDR tunables (measured on synthetic rooms, see the class comment) ----
    /** Noise covariance time constant, seconds. */
    static final double COV_TAU_S = 1.0;
    /** Diagonal loading, fraction of the mean mic power in the bin. */
    static final double LOADING = 0.01;
    /** Noise hops needed before MVDR weights replace DS. */
    static final int WARM_HOPS = 10;
    /** "Quiet": hop energy at or under this percentile of the last QUIET_WINDOW_S. */
    static final double QUIET_PERCENTILE = 30.0;
    static final double QUIET_WINDOW_S = 5.0;
    /** "Another talker": one source (strength, closure) whose lags sit this
     *  far from the target's (max of SIGMAS x the calibration's sigma, MIN). */
    static final double OTHER_SIGMAS = 3.0;
    static final double OTHER_MIN_SAMPLES = 0.6;
    static final double ONE_SOURCE_STRENGTH = 0.3;
    static final double ONE_SOURCE_CLOSURE = 0.35;
    /** A per-bin weight this large is a numerical accident: DS for that bin. */
    static final double MAX_WEIGHT = 4.0;
    /** Gain equalisation: time constant (s) and clamp (either way, x). */
    static final double EQ_TAU_S = 10.0;
    static final double EQ_MAX = 2.0;
    /** The self-cancellation guard: on hops the gate puts on the TARGET, the
     *  MVDR output's power against the DS output's, averaged over ~10 such
     *  hops; below this (dB) MVDR is eating the voice it is pointed at (a
     *  mis-steered beam, an unmatched mic) -> forget R, DS for GUARD_HOLD_S. */
    static final double GUARD_DB = -3.0;
    static final double GUARD_HOLD_S = 2.0;
    /** GCC band for the MVDR gate, Hz (as DoaMath's). */
    static final double GATE_LO_HZ = 200;
    static final double GATE_HI_HZ = 4000;

    static final int[][] PAIRS = DoaMath.PAIRS;

    /** What the server asked for, validated. Immutable: the capture thread
     *  reads one reference per hop. */
    static final class Steer {
        /** Arrival time of the target at mics 0-3, samples (mic 0 = 0), or null. */
        final double[] tau;
        /** The target's six pair lags (tau_j - tau_i, MVDR's gate), or null. */
        final double[] lags;
        /** The calibration's residual scale, samples. */
        final double sigma;
        /** Weights when tau is null (no target): sum 1. */
        final double[] unsteered;
        final int mode;
        /** The server's counter: logged, and echoed in the status. */
        final int gen;
        /** The camera sees the target's mouth moving right now (focus's
         *  active speaker). MVDR then learns nothing from "another talker"
         *  hops: with the beam mis-steered (a calibration off by 15 degrees
         *  on a line array), the target's own hops read as another talker,
         *  went into R, and the target lost 1.3-2.8 dB -- with no hop the
         *  self-cancellation guard could see. */
        final boolean talking;

        private Steer(double[] tau, double[] lags, double sigma, double[] unsteered, int mode, int gen,
                      boolean talking) {
            this.tau = tau;
            this.lags = lags;
            this.sigma = sigma;
            this.unsteered = unsteered;
            this.mode = mode;
            this.gen = gen;
            this.talking = talking;
        }

        static Steer none() {
            return new Steer(null, null, 0.3, new double[]{1, 0, 0, 0}, MODE_DS, 0, false);
        }

        /** The server's numbers, checked: a bad one becomes "no target" (mic 0
         *  or the given unsteered weights), never a wild filter. The pair lags
         *  are tau's own differences -- the server sends arrival times of a
         *  consistent lag vector, so the two cannot disagree. */
        static Steer parse(double[] tau, double sigma, double[] unsteered, String mode, int gen,
                           double maxLag, boolean talking) {
            double[] w = {1, 0, 0, 0};
            if (unsteered != null && unsteered.length == MICS) {
                double sum = 0;
                boolean ok = true;
                for (double v : unsteered) {
                    if (Double.isNaN(v) || Double.isInfinite(v) || v < 0 || v > 1) ok = false;
                    sum += v;
                }
                if (ok && sum > 0.5 && sum < 1.5) {
                    w = new double[MICS];
                    for (int m = 0; m < MICS; m++) w[m] = unsteered[m] / sum;
                }
            }
            int md = "mvdr".equals(mode) ? MODE_MVDR : MODE_DS;
            double sg = Double.isNaN(sigma) ? 0.3 : Math.max(0.05, Math.min(2.0, sigma));
            double[] t = finite(tau, MICS, maxLag);
            double[] l = null;
            if (t != null) {
                double ref = t[0];
                for (int m = 0; m < MICS; m++) t[m] -= ref;      // mic 0 = 0
                l = new double[PAIRS.length];
                for (int p = 0; p < PAIRS.length; p++) l[p] = t[PAIRS[p][1]] - t[PAIRS[p][0]];
            }
            return new Steer(t, l, sg, w, md, gen, talking && t != null);
        }

        private static double[] finite(double[] v, int n, double bound) {
            if (v == null || v.length != n) return null;
            double[] out = new double[n];
            for (int k = 0; k < n; k++) {
                if (Double.isNaN(v[k]) || Double.isInfinite(v[k]) || Math.abs(v[k]) > bound) return null;
                out[k] = v[k];
            }
            return out;
        }

        boolean steered() {
            return tau != null;
        }
    }

    final int rate;
    final double maxLag;
    final int bins = N / 2 + 1;
    private final double[] win = new double[N];
    private final double[] twCos = new double[N / 2];
    private final double[] twSin = new double[N / 2];
    private final int[] reverse = new int[N];
    private final double[][] frame = new double[MICS][N];
    private final double[][] re = new double[MICS][N];
    private final double[][] im = new double[MICS][N];
    private final double[] yRe = new double[N];
    private final double[] yIm = new double[N];
    private final double[] ola = new double[N];
    private final float[] out = new float[HOP];

    // weights per bin (complex), what synthesis uses
    final double[][] wRe = new double[N / 2 + 1][MICS];
    final double[][] wIm = new double[N / 2 + 1][MICS];
    // the DS weights of the current steering (the guard's reference)
    private final double[][] dsRe = new double[N / 2 + 1][MICS];
    private final double[][] dsIm = new double[N / 2 + 1][MICS];

    // gain equalisation
    final double[] eq = {1, 1, 1, 1};
    private final double[] eqPow = new double[MICS];
    private boolean eqSeeded = false;
    private boolean eqOn = true;

    // steering
    private Steer steer = Steer.none();
    private Steer applied = null;

    // MVDR state
    private final double[][] rRe = new double[N / 2 + 1][MICS * MICS];
    private final double[][] rIm = new double[N / 2 + 1][MICS * MICS];
    private double rWeight = 0;
    private int noiseHops = 0;
    private final double covAlpha;
    private final double eqAlpha;
    private final double[] energies;
    private int energyCount = 0;
    private int energyAt = 0;
    private double quietBar = Double.NaN;
    private int sinceQuiet = 0;
    // GCC gate tables
    private final int gLo;
    private final int gBins;
    private final int gLags;
    private final float[][] gCos;
    private final float[][] gSin;
    private final double[] gccRe;
    private final double[] gccIm;
    private final double[] corr;
    final double[] hopLags = new double[PAIRS.length];
    final double[] hopStrength = new double[PAIRS.length];

    // counters (the status line and the tests read them)
    long hops = 0;
    long quietHops = 0;
    long otherHops = 0;
    long targetHops = 0;
    long mvdrHops = 0;
    long resets = 0;
    long guardTrips = 0;
    /** "Another talker" hops NOT learned because the target was talking. */
    long heldHops = 0;
    /** The guard's running MVDR-vs-DS power on target hops, dB. */
    double guardDb = 0;
    private int guardHold = 0;

    // scratch for the 4x4 solve
    private final double[] aRe = new double[MICS * MICS];
    private final double[] aIm = new double[MICS * MICS];
    private final double[] zRe = new double[MICS];
    private final double[] zIm = new double[MICS];
    private final double[] dRe = new double[MICS];
    private final double[] dIm = new double[MICS];

    ArrayBeam(int rate, double maxLag) {
        this.rate = rate;
        this.maxLag = Math.max(2, Math.min(32, maxLag));
        for (int n = 0; n < N; n++) win[n] = Math.sqrt(0.5 - 0.5 * Math.cos(2 * Math.PI * n / N));
        for (int k = 0; k < N / 2; k++) {
            twCos[k] = Math.cos(-2 * Math.PI * k / N);
            twSin[k] = Math.sin(-2 * Math.PI * k / N);
        }
        int bitsN = Integer.numberOfTrailingZeros(N);
        for (int n = 0; n < N; n++) reverse[n] = Integer.reverse(n) >>> (32 - bitsN);
        covAlpha = Math.exp(-HOP / (double) rate / COV_TAU_S);
        eqAlpha = Math.exp(-HOP / (double) rate / EQ_TAU_S);
        energies = new double[(int) Math.round(QUIET_WINDOW_S * rate / HOP)];
        gLo = Math.max(1, (int) Math.ceil(GATE_LO_HZ * N / rate));
        int hi = Math.min(N / 2 - 1, (int) Math.floor(GATE_HI_HZ * N / rate));
        gBins = hi - gLo + 1;
        double step = 0.25;
        gLags = (int) Math.round(2 * this.maxLag / step) + 1;
        gCos = new float[gLags][gBins];
        gSin = new float[gLags][gBins];
        for (int t = 0; t < gLags; t++) {
            double tau = -this.maxLag + t * step;
            for (int b = 0; b < gBins; b++) {
                double phase = 2 * Math.PI * (gLo + b) * tau / N;
                gCos[t][b] = (float) Math.cos(phase);
                gSin[t][b] = (float) Math.sin(phase);
            }
        }
        gccRe = new double[gBins];
        gccIm = new double[gBins];
        corr = new double[gLags];
        setWeights(steer);
    }

    /** Tests: equalisation off (a follower must not learn its own). */
    void equalise(boolean on) {
        eqOn = on;
        if (!on) Arrays.fill(eq, 1.0);
    }

    /** Any thread hands a new Steer to the capture thread through this. */
    void steer(Steer next) {
        steer = next == null ? Steer.none() : next;
    }

    Steer steering() {
        return applied;
    }

    /** One hop of the four picked channels (HOP samples each, full scale 1)
     *  -> HOP samples of the beam (a reused buffer). */
    float[] process(float[][] hop) {
        return step(hop, this);
    }

    /** The tests' follower: the same analysis/synthesis, with the LEADER's
     *  gains and weights for this hop -- so a separate signal component
     *  (target alone, interferer alone) goes through exactly the filter the
     *  mixture got, and SIR/SNR can be measured. Call after the leader. */
    float[] processWith(float[][] hop, ArrayBeam leader) {
        return step(hop, leader);
    }

    private float[] step(float[][] hop, ArrayBeam src) {
        boolean lead = src == this;
        if (lead) {
            Steer s = steer;
            if (s != applied) adopt(s);
            if (eqOn) updateEq(hop);
        }
        for (int m = 0; m < MICS; m++) {
            double g = src.eq[m];
            double[] f = frame[m];
            System.arraycopy(f, HOP, f, 0, N - HOP);
            float[] x = hop[m];
            for (int n = 0; n < HOP; n++) f[N - HOP + n] = x[n] * g;
            double[] r = re[m];
            double[] i = im[m];
            for (int n = 0; n < N; n++) {
                r[n] = f[n] * win[n];
                i[n] = 0;
            }
            fft(r, i);
        }
        if (lead) {
            hops++;
            if (applied.mode == MODE_MVDR && applied.steered()) mvdrHop();
        }
        // Y = sum_m conj(w_m) X_m, bins 0..N/2, then Hermitian for the rest
        for (int k = 0; k < bins; k++) {
            double sr = 0, si = 0;
            double[] wr = src.wRe[k];
            double[] wi = src.wIm[k];
            for (int m = 0; m < MICS; m++) {
                double xr = re[m][k], xi = im[m][k];
                sr += wr[m] * xr + wi[m] * xi;
                si += wr[m] * xi - wi[m] * xr;
            }
            yRe[k] = sr;
            yIm[k] = si;
        }
        yIm[0] = 0;
        yIm[N / 2] = 0;
        for (int k = 1; k < N / 2; k++) {
            yRe[N - k] = yRe[k];
            yIm[N - k] = -yIm[k];
        }
        // inverse FFT: conj(FFT(conj(Y))) / N
        for (int k = 0; k < N; k++) yIm[k] = -yIm[k];
        fft(yRe, yIm);
        for (int n = 0; n < N; n++) ola[n] += yRe[n] / N * win[n];
        for (int n = 0; n < HOP; n++) out[n] = (float) ola[n];
        System.arraycopy(ola, HOP, ola, 0, N - HOP);
        Arrays.fill(ola, N - HOP, N, 0.0);
        return out;
    }

    /** A new Steer from the server. Another target (or mode) starts the noise
     *  estimate over -- the old R may hold the new target's own voice as
     *  "another talker". The same target re-sent (a refresh, the talking
     *  flag) keeps R and the MVDR weights; a small re-aim re-solves them. */
    private void adopt(Steer s) {
        Steer old = applied;
        applied = s;
        if (old == null || old.mode != s.mode || moved(old, s)) {
            resetCovariance();                          // sets the DS weights too
            return;
        }
        if (Arrays.equals(old.tau, s.tau) && Arrays.equals(old.unsteered, s.unsteered)) return;
        setWeights(s);
        if (s.mode == MODE_MVDR && s.steered() && noiseHops >= WARM_HOPS) solveWeights();
    }

    private static boolean moved(Steer a, Steer b) {
        if (a.steered() != b.steered()) return true;
        if (!b.steered() || a.lags == null || b.lags == null) return false;
        double d = 0;
        for (int p = 0; p < PAIRS.length; p++) d += (a.lags[p] - b.lags[p]) * (a.lags[p] - b.lags[p]);
        return Math.sqrt(d) > Math.max(OTHER_SIGMAS * b.sigma, OTHER_MIN_SAMPLES);
    }

    private void resetCovariance() {
        for (int k = 0; k < bins; k++) {
            Arrays.fill(rRe[k], 0);
            Arrays.fill(rIm[k], 0);
        }
        rWeight = 0;
        noiseHops = 0;
        guardDb = 0;
        resets++;
        if (applied != null) setWeights(applied);      // DS until warm again
    }

    /** DS weights for a steered target, the unsteered weights otherwise. */
    private void setWeights(Steer s) {
        for (int k = 0; k < bins; k++) {
            for (int m = 0; m < MICS; m++) {
                if (s.steered()) {
                    double ph = -2 * Math.PI * k * s.tau[m] / N;    // d_m = e^{-j w tau_m}
                    wRe[k][m] = Math.cos(ph) / MICS;
                    wIm[k][m] = Math.sin(ph) / MICS;
                } else {
                    wRe[k][m] = s.unsteered[m];
                    wIm[k][m] = 0;
                }
                dsRe[k][m] = wRe[k][m];
                dsIm[k][m] = wIm[k][m];
            }
        }
    }

    /** Output power of this hop's spectra through weights (re, im). */
    private double powerThrough(double[][] pr, double[][] pi) {
        double total = 0;
        for (int k = 1; k < bins - 1; k++) {
            double sr = 0, si = 0;
            for (int m = 0; m < MICS; m++) {
                double xr = re[m][k], xi = im[m][k];
                sr += pr[k][m] * xr + pi[k][m] * xi;
                si += pr[k][m] * xi - pi[k][m] * xr;
            }
            total += sr * sr + si * si;
        }
        return total;
    }

    private final double[] hopPow = new double[MICS];

    private void updateEq(float[][] hop) {
        double total = 0;
        double[] p = hopPow;
        for (int m = 0; m < MICS; m++) {
            double s = 0;
            for (int n = 0; n < HOP; n++) s += hop[m][n] * hop[m][n];
            p[m] = s / HOP;
            total += p[m];
        }
        if (total < 1e-12) return;                 // digital silence teaches nothing
        for (int m = 0; m < MICS; m++) {
            eqPow[m] = eqSeeded ? eqAlpha * eqPow[m] + (1 - eqAlpha) * p[m] : p[m];
        }
        eqSeeded = true;
        double logMean = 0;
        for (int m = 0; m < MICS; m++) logMean += Math.log(Math.max(eqPow[m], 1e-20));
        logMean /= MICS;
        for (int m = 0; m < MICS; m++) {
            double g = Math.sqrt(Math.exp(logMean) / Math.max(eqPow[m], 1e-20));
            eq[m] = Math.max(1 / EQ_MAX, Math.min(EQ_MAX, g));
        }
    }

    // ---- MVDR ----

    private void mvdrHop() {
        double energy = 0;
        for (int m = 0; m < MICS; m++) {
            double[] f = frame[m];
            for (int n = N - HOP; n < N; n++) energy += f[n] * f[n];
        }
        energy /= MICS * HOP;
        energies[energyAt] = energy;
        energyAt = (energyAt + 1) % energies.length;
        energyCount = Math.min(energyCount + 1, energies.length);
        if (++sinceQuiet >= 8 || Double.isNaN(quietBar)) {
            sinceQuiet = 0;
            double[] copy = Arrays.copyOf(energies, energyCount);
            Arrays.sort(copy);
            quietBar = copy[(int) Math.floor((copy.length - 1) * QUIET_PERCENTILE / 100.0)];
        }
        boolean noise = false;
        if (energyCount >= rate / HOP && energy <= quietBar) {
            noise = true;
            quietHops++;
        } else if (energy > 1e-12) {
            gcc();
            double closure = consistentInto(hopLags, hopStrength, fitted);
            double strength = 0;
            for (double s : hopStrength) strength += s;
            strength /= hopStrength.length;
            if (strength > ONE_SOURCE_STRENGTH && closure < ONE_SOURCE_CLOSURE) {
                double d = 0;
                for (int p = 0; p < PAIRS.length; p++) d += sq(fitted[p] - applied.lags[p]);
                if (Math.sqrt(d) > Math.max(OTHER_SIGMAS * applied.sigma, OTHER_MIN_SAMPLES)) {
                    if (applied.talking) {
                        heldHops++;                     // may be the target, mis-steered
                    } else {
                        noise = true;
                        otherHops++;
                    }
                } else {
                    targetHops++;
                    if (noiseHops >= WARM_HOPS) guard();
                }
            }
        }
        if (guardHold > 0) {
            guardHold--;
            return;                                     // DS while the guard holds
        }
        if (noise) {
            for (int k = 0; k < bins; k++) {
                double[] rr = rRe[k];
                double[] ri = rIm[k];
                for (int a = 0; a < MICS; a++) {
                    double ar = re[a][k], ai = im[a][k];
                    for (int b = 0; b < MICS; b++) {
                        double br = re[b][k], bi = im[b][k];
                        // x_a conj(x_b)
                        int at = a * MICS + b;
                        rr[at] = covAlpha * rr[at] + (1 - covAlpha) * (ar * br + ai * bi);
                        ri[at] = covAlpha * ri[at] + (1 - covAlpha) * (ai * br - ar * bi);
                    }
                }
            }
            rWeight = covAlpha * rWeight + (1 - covAlpha);
            noiseHops++;
            if (noiseHops >= WARM_HOPS) solveWeights();
        }
        if (noiseHops >= WARM_HOPS) mvdrHops++;
    }

    private final double[] fitted = new double[PAIRS.length];

    /** A target hop with MVDR live: is the beam keeping the voice it is
     *  pointed at? (It is distortionless only for a PERFECT steering vector;
     *  a wrong one, or mics that do not match, let R cancel the target.) */
    private void guard() {
        double ds = powerThrough(dsRe, dsIm);
        double mv = powerThrough(wRe, wIm);
        if (ds <= 1e-20) return;
        double ratio = 10 * Math.log10(Math.max(mv, 1e-30) / ds);
        guardDb = 0.9 * guardDb + 0.1 * ratio;
        if (guardDb < GUARD_DB) {
            guardTrips++;
            resetCovariance();
            guardHold = (int) Math.round(GUARD_HOLD_S * rate / HOP);
        }
    }

    private static double sq(double v) {
        return v * v;
    }

    private void solveWeights() {
        double[] tau = applied.tau;
        for (int k = 0; k < bins; k++) {
            double trace = 0;
            for (int m = 0; m < MICS; m++) trace += rRe[k][m * MICS + m];
            double load = LOADING * trace / MICS + 1e-12 * rWeight;
            for (int e = 0; e < MICS * MICS; e++) {
                aRe[e] = rRe[k][e];
                aIm[e] = rIm[k][e];
            }
            for (int m = 0; m < MICS; m++) aRe[m * MICS + m] += load;
            for (int m = 0; m < MICS; m++) {
                double ph = -2 * Math.PI * k * tau[m] / N;
                dRe[m] = Math.cos(ph);
                dIm[m] = Math.sin(ph);
                zRe[m] = dRe[m];
                zIm[m] = dIm[m];
            }
            boolean ok = solve4(aRe, aIm, zRe, zIm);
            // denominator d^H z
            double nr = 0, ni = 0;
            if (ok) {
                for (int m = 0; m < MICS; m++) {
                    nr += dRe[m] * zRe[m] + dIm[m] * zIm[m];
                    ni += dRe[m] * zIm[m] - dIm[m] * zRe[m];
                }
            }
            double den = nr * nr + ni * ni;
            boolean use = ok && den > 1e-30 && !Double.isNaN(den);
            double[] wr = wRe[k];
            double[] wi = wIm[k];
            if (use) {
                double big = 0;
                for (int m = 0; m < MICS; m++) {
                    double r = (zRe[m] * nr + zIm[m] * ni) / den;     // z / (d^H z)
                    double i = (zIm[m] * nr - zRe[m] * ni) / den;
                    wr[m] = r;
                    wi[m] = i;
                    big = Math.max(big, Math.hypot(r, i));
                }
                if (big > MAX_WEIGHT || Double.isNaN(big)) use = false;
            }
            if (!use) {
                for (int m = 0; m < MICS; m++) {
                    wr[m] = dRe[m] / MICS;
                    wi[m] = dIm[m] / MICS;
                }
            }
        }
    }

    /** Solve A z = b (4x4 complex, row-major) in place; false if singular. */
    private static boolean solve4(double[] ar, double[] ai, double[] br, double[] bi) {
        for (int c = 0; c < MICS; c++) {
            int piv = c;
            double best = -1;
            for (int r = c; r < MICS; r++) {
                double mag = ar[r * MICS + c] * ar[r * MICS + c] + ai[r * MICS + c] * ai[r * MICS + c];
                if (mag > best) {
                    best = mag;
                    piv = r;
                }
            }
            if (best < 1e-40) return false;
            if (piv != c) {
                for (int k = 0; k < MICS; k++) {
                    double t = ar[c * MICS + k]; ar[c * MICS + k] = ar[piv * MICS + k]; ar[piv * MICS + k] = t;
                    t = ai[c * MICS + k]; ai[c * MICS + k] = ai[piv * MICS + k]; ai[piv * MICS + k] = t;
                }
                double t = br[c]; br[c] = br[piv]; br[piv] = t;
                t = bi[c]; bi[c] = bi[piv]; bi[piv] = t;
            }
            double pr = ar[c * MICS + c], pi = ai[c * MICS + c];
            double pm = pr * pr + pi * pi;
            for (int r = c + 1; r < MICS; r++) {
                double xr = ar[r * MICS + c], xi = ai[r * MICS + c];
                double fr = (xr * pr + xi * pi) / pm;           // x / p
                double fi = (xi * pr - xr * pi) / pm;
                for (int k = c; k < MICS; k++) {
                    double yr = ar[c * MICS + k], yi = ai[c * MICS + k];
                    ar[r * MICS + k] -= fr * yr - fi * yi;
                    ai[r * MICS + k] -= fr * yi + fi * yr;
                }
                br[r] -= fr * br[c] - fi * bi[c];
                bi[r] -= fr * bi[c] + fi * br[c];
            }
        }
        for (int r = MICS - 1; r >= 0; r--) {
            double sr = br[r], si = bi[r];
            for (int k = r + 1; k < MICS; k++) {
                double yr = ar[r * MICS + k], yi = ai[r * MICS + k];
                sr -= yr * br[k] - yi * bi[k];
                si -= yr * bi[k] + yi * br[k];
            }
            double pr = ar[r * MICS + r], pi = ai[r * MICS + r];
            double pm = pr * pr + pi * pi;
            br[r] = (sr * pr + si * pi) / pm;
            bi[r] = (si * pr - sr * pi) / pm;
        }
        return true;
    }

    /** GCC-PHAT of the six pairs on this hop's spectra (DoaMath's sign:
     *  lag = arrival at j minus arrival at i). */
    private void gcc() {
        for (int p = 0; p < PAIRS.length; p++) {
            int a = PAIRS[p][0];
            int b = PAIRS[p][1];
            int used = 0;
            for (int k = 0; k < gBins; k++) {
                int bin = gLo + k;
                double ar = re[a][bin], ai = im[a][bin], br = re[b][bin], bi = im[b][bin];
                double cr = ar * br + ai * bi;
                double ci = ar * bi - ai * br;
                double mag = Math.hypot(cr, ci);
                if (mag > 1e-20) {
                    gccRe[k] = cr / mag;
                    gccIm[k] = ci / mag;
                    used++;
                } else {
                    gccRe[k] = 0;
                    gccIm[k] = 0;
                }
            }
            if (used == 0) {
                hopLags[p] = 0;
                hopStrength[p] = 0;
                continue;
            }
            int best = 0;
            for (int t = 0; t < gLags; t++) {
                float[] c = gCos[t];
                float[] s = gSin[t];
                double acc = 0;
                for (int k = 0; k < gBins; k++) acc += gccRe[k] * c[k] - gccIm[k] * s[k];
                corr[t] = acc / used;
                if (corr[t] > corr[best]) best = t;
            }
            double delta = 0;
            double peak = corr[best];
            if (best > 0 && best < gLags - 1) {
                double y0 = corr[best - 1], y1 = corr[best], y2 = corr[best + 1];
                double den = y0 - 2 * y1 + y2;
                if (den < 0) {
                    delta = Math.max(-0.5, Math.min(0.5, 0.5 * (y0 - y2) / den));
                    peak = y1 - 0.25 * (y0 - y2) * delta;
                }
            }
            hopLags[p] = -maxLag + (best + delta) * 0.25;
            hopStrength[p] = Math.max(0, Math.min(1, peak));
        }
    }

    /** app/doa.py `consistent`, in Java: the closest lag vector four arrival
     *  times can make (weighted by strength), into `fit`; returns the closure. */
    static double consistentInto(double[] lags, double[] strength, double[] fit) {
        double[] w = new double[PAIRS.length];
        double[][] ata = new double[3][3];
        double[] atl = new double[3];
        for (int p = 0; p < PAIRS.length; p++) {
            double s = Math.max(Math.min(Math.max(strength[p], 0), 1), 0.05);
            w[p] = s * s;
            double[] row = rowOf(p);
            for (int a = 0; a < 3; a++) {
                atl[a] += w[p] * row[a] * lags[p];
                for (int b = 0; b < 3; b++) ata[a][b] += w[p] * row[a] * row[b];
            }
        }
        double[] t = solve3(ata, atl);
        double sw = 0, sr = 0;
        for (int p = 0; p < PAIRS.length; p++) {
            double[] row = rowOf(p);
            fit[p] = row[0] * t[0] + row[1] * t[1] + row[2] * t[2];
            sr += w[p] * sq(lags[p] - fit[p]);
            sw += w[p];
        }
        return Math.sqrt(sr / sw);
    }

    /** Row p of the pair matrix without mic 0's column (t0 pinned to 0). */
    private static double[] rowOf(int p) {
        double[] row = new double[3];
        int i = PAIRS[p][0];
        int j = PAIRS[p][1];
        if (i > 0) row[i - 1] -= 1;
        if (j > 0) row[j - 1] += 1;
        return row;
    }

    private static double[] solve3(double[][] a, double[] b) {
        double det = a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
                - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
                + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]);
        double[] x = new double[3];
        if (Math.abs(det) < 1e-12) return x;
        for (int c = 0; c < 3; c++) {
            double[][] m = {a[0].clone(), a[1].clone(), a[2].clone()};
            for (int r = 0; r < 3; r++) m[r][c] = b[r];
            x[c] = (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
                    - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
                    + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])) / det;
        }
        return x;
    }

    /** In-place iterative radix-2 FFT (forward, e^{-i...}). */
    private void fft(double[] r, double[] i) {
        for (int a = 0; a < N; a++) {
            int b = reverse[a];
            if (b > a) {
                double t = r[a]; r[a] = r[b]; r[b] = t;
                t = i[a]; i[a] = i[b]; i[b] = t;
            }
        }
        for (int size = 2; size <= N; size <<= 1) {
            int half = size >> 1;
            int stride = N / size;
            for (int start = 0; start < N; start += size) {
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

    // ---- the wire ----

    /** [0xEA, kind, seq u16 LE, t_app_ms u32 LE] + PCM16 LE. app/array_audio.py
     *  checks every field again; `samples` interleaved for KIND_RAW8. */
    static byte[] pack(int kind, int seq, long tAppMs, short[] samples, int count) {
        byte[] b = new byte[HEADER + 2 * count];
        b[0] = (byte) MAGIC;
        b[1] = (byte) kind;
        b[2] = (byte) seq;
        b[3] = (byte) (seq >>> 8);
        b[4] = (byte) tAppMs;
        b[5] = (byte) (tAppMs >>> 8);
        b[6] = (byte) (tAppMs >>> 16);
        b[7] = (byte) (tAppMs >>> 24);
        for (int k = 0; k < count; k++) {
            short v = samples[k];
            b[HEADER + 2 * k] = (byte) v;
            b[HEADER + 2 * k + 1] = (byte) (v >>> 8);
        }
        return b;
    }

    /** Float beam samples -> PCM16, clipped. */
    static void toPcm(float[] x, int from, int count, short[] dst, int at) {
        for (int k = 0; k < count; k++) {
            float v = x[from + k] * 32768f;
            if (v > 32767f) v = 32767f;
            if (v < -32768f) v = -32768f;
            dst[at + k] = (short) Math.round(v);
        }
    }
}
