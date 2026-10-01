/**
 * Pure speech / silence state machine (no browser APIs, fully testable).
 *
 * Feed it one loudness value (RMS, 0..1) per audio chunk together with a
 * monotonic time in ms. It tells you when the candidate STARTED and FINISHED
 * speaking. All timings come from configuration:
 *
 *   silence_ms          silence after speech that ends an answer
 *   min_speech_ms       less voiced audio than this is treated as noise
 *   onset_ms            sustained sound needed before speech is declared
 *   min_threshold       absolute RMS floor
 *   noise_multiplier    speech must exceed (room noise x this)
 *   max_noise_floor     cap on the measured room noise
 *   calibration_ms      room-noise sampling window when listening starts
 *   max_utterance_ms    safety cut-off for a single answer
 *   no_speech_timeout_ms  nothing said for this long -> "no_speech_timeout" (0 = off)
 *
 * A short natural pause never ends an answer: the answer only ends after
 * `silence_ms` of CONTINUOUS silence.
 */
export class SpeechSegmenter {
  constructor(config) {
    this.cfg = config;
    this.reset(0);
  }

  reset(now) {
    this.state = "calibrating"; // calibrating -> idle -> speaking -> done
    this.lastTick = now;
    this.calStart = now;
    this.calSum = 0;
    this.calCount = 0;
    this.noiseFloor = 0;
    this.threshold = this.cfg.min_threshold;
    this.voicedRunMs = 0;
    this.voicedMs = 0;
    this.speechStart = null;
    this.lastVoiceAt = null;
    this.listenStart = now;
  }

  /** After an answer that was too short to count: keep listening, keep the noise estimate. */
  resumeListening(now) {
    this.state = "idle";
    this.lastTick = now;
    this.voicedRunMs = 0;
    this.voicedMs = 0;
    this.speechStart = null;
    this.lastVoiceAt = null;
  }

  _updateThreshold() {
    this.threshold = Math.max(
      this.cfg.min_threshold,
      this.noiseFloor * this.cfg.noise_multiplier
    );
  }

  /** @returns {null | {type: string, ...}} */
  feed(rms, now) {
    const dt = Math.max(0, now - this.lastTick);
    this.lastTick = now;

    if (this.state === "calibrating") {
      this.calSum += rms;
      this.calCount += 1;
      if (now - this.calStart >= this.cfg.calibration_ms) {
        const avg = this.calCount ? this.calSum / this.calCount : 0;
        this.noiseFloor = Math.min(avg, this.cfg.max_noise_floor);
        this._updateThreshold();
        this.state = "idle";
        this.listenStart = now;
      }
      return null;
    }

    if (this.state === "idle") {
      if (rms > this.threshold) {
        this.voicedRunMs += dt;
        if (this.voicedRunMs >= this.cfg.onset_ms) {
          this.state = "speaking";
          this.speechStart = now - this.voicedRunMs;
          this.voicedMs = this.voicedRunMs;
          this.lastVoiceAt = now;
          return { type: "speech_start", at: this.speechStart };
        }
        return null;
      }

      this.voicedRunMs = 0;
      // Track slow changes in room noise while nobody is speaking.
      this.noiseFloor = Math.min(
        this.cfg.max_noise_floor,
        this.noiseFloor * 0.97 + rms * 0.03
      );
      this._updateThreshold();

      const timeout = this.cfg.no_speech_timeout_ms;
      if (timeout > 0 && now - this.listenStart >= timeout) {
        this.listenStart = now;
        return { type: "no_speech_timeout" };
      }
      return null;
    }

    if (this.state === "speaking") {
      // Hysteresis: once speaking, quieter sounds still count as voice, so
      // trailing words and soft consonants don't look like silence.
      if (rms > this.threshold * 0.6) {
        this.voicedMs += dt;
        this.lastVoiceAt = now;
      }

      const silentFor = now - this.lastVoiceAt;
      const tooLong = now - this.speechStart >= this.cfg.max_utterance_ms;

      if (silentFor >= this.cfg.silence_ms || tooLong) {
        this.state = "done";
        return {
          type: "speech_end",
          valid: this.voicedMs >= this.cfg.min_speech_ms,
          voicedMs: this.voicedMs,
          forced: tooLong && silentFor < this.cfg.silence_ms,
        };
      }
    }

    return null;
  }
}

export function rmsOf(samples) {
  let sum = 0;
  for (let i = 0; i < samples.length; i += 1) sum += samples[i] * samples[i];
  return Math.sqrt(sum / (samples.length || 1));
}
