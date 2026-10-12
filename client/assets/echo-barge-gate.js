/* Detect a person speaking over Emma without sending Emma's speaker bleed to
 * the voice model. The page briefly ducks its own output when the microphone
 * gets louder than the recent echo. A voice that persists while the speaker
 * is quiet is a barge-in; an echo that falls with the speaker is discarded.
 * No microphone samples or transcripts leave this gate during a probe. */
(function (root) {
  'use strict';

  function createEchoBargeGate() {
    var echoLevel = 0;
    var probe = null;
    var cooldownUntil = 0;
    var startedAt = null;
    var candidateAt = null;
    var candidatePeak = 0;

    return {
      reset: function () {
        echoLevel = 0;
        probe = null;
        cooldownUntil = 0;
        startedAt = null;
        candidateAt = null;
        candidatePeak = 0;
      },
      sample: function (rms, now) {
        rms = Number.isFinite(rms) ? Math.max(0, rms) : 0;
        if (probe) {
          var age = now - probe.started;
          // Wait for the gain change and room response, then compare the
          // final frames with the level before the short probe.
          if (age >= 60) { probe.after += rms; probe.count += 1; }
          if (age < 120) return { action: 'hold', probing: true };
          var remaining = probe.count ? probe.after / probe.count : 0;
          var isPerson = remaining >= Math.max(0.009, probe.before * 0.78);
          echoLevel = Math.max(echoLevel, probe.before * 0.9);
          probe = null;
          if (isPerson) return { action: 'barge', probing: false };
          cooldownUntil = now + 1200;
          return { action: 'restore', probing: false };
        }

        if (startedAt === null) startedAt = now;
        if (now - startedAt >= 140 && now >= cooldownUntil &&
            rms >= Math.max(0.016, echoLevel * 1.9)) {
          if (candidateAt === null) candidateAt = now;
          candidatePeak = Math.max(candidatePeak, rms);
          if (now - candidateAt >= 60) {
            probe = { started: now, before: candidatePeak, after: 0, count: 0 };
            candidateAt = null;
            candidatePeak = 0;
            return { action: 'duck', probing: true };
          }
          return { action: 'hold', probing: true };
        }
        var cleared = candidateAt !== null;
        candidateAt = null;
        candidatePeak = 0;
        echoLevel = echoLevel ? echoLevel * 0.88 + rms * 0.12 : rms;
        return { action: cleared ? 'clear' : 'hold', probing: false };
      }
    };
  }

  root.createEchoBargeGate = createEchoBargeGate;
  if (typeof module !== 'undefined' && module.exports) module.exports = createEchoBargeGate;
})(typeof window !== 'undefined' ? window : globalThis);
