// Run with:  node --test tests
import assert from "node:assert/strict";
import test from "node:test";

import { SpeechSegmenter } from "../src/audio/speechSegmenter.js";
import { encodeWav } from "../src/audio/wav.js";

const CFG = {
  silence_ms: 1800, min_speech_ms: 500, onset_ms: 150, min_threshold: 0.012,
  noise_multiplier: 3, max_noise_floor: 0.05, calibration_ms: 300,
  max_utterance_ms: 90000, no_speech_timeout_ms: 20000,
};
const FRAME = 43; // ms per audio chunk (2048 samples @ 48 kHz)

// script: [[rms, durationMs], ...]  -> events with the time they fired
function run(script, cfg = CFG) {
  const seg = new SpeechSegmenter(cfg);
  seg.reset(0);
  const events = [];
  let t = 0;
  for (const [rms, ms] of script) {
    for (let n = 0; n < Math.round(ms / FRAME); n += 1) {
      t += FRAME;
      const jitter = rms * (1 + 0.2 * Math.sin(t));
      const ev = seg.feed(jitter, t);
      if (ev) events.push({ ...ev, t });
    }
  }
  return { events, seg, t };
}

test("one answer with a natural 1 s pause in the middle is NOT split", () => {
  const { events } = run([[0.004, 1000], [0.1, 2000], [0.004, 1000], [0.1, 1500], [0.004, 3000]]);
  const types = events.map((e) => e.type);
  assert.deepEqual(types, ["speech_start", "speech_end"]);
  const end = events[1];
  assert.equal(end.valid, true);
  // ends ~1.8 s after the LAST voiced sound (1000+2000+1000+1500 = 5500 ms)
  assert.ok(end.t >= 5500 + 1800 && end.t <= 5500 + 1800 + 2 * FRAME, `ended at ${end.t}`);
});

test("a pause longer than silence_ms ends the answer", () => {
  const { events } = run([[0.004, 500], [0.1, 1500], [0.004, 2500]]);
  assert.deepEqual(events.map((e) => e.type), ["speech_start", "speech_end"]);
});

test("a 100 ms click never starts speech", () => {
  const { events } = run([[0.004, 800], [0.2, 100], [0.004, 3000]]);
  assert.equal(events.length, 0);
});

test("a short cough is detected but flagged invalid (too little speech)", () => {
  const { events } = run([[0.004, 800], [0.2, 300], [0.004, 3000]]);
  assert.equal(events[0].type, "speech_start");
  assert.equal(events[1].type, "speech_end");
  assert.equal(events[1].valid, false);
});

test("noisy room: steady noise is not speech, real speech still detected", () => {
  const { events } = run([[0.03, 1500], [0.2, 1500], [0.03, 3000]]);
  assert.deepEqual(events.map((e) => e.type), ["speech_start", "speech_end"]);
});

test("steady noise alone never triggers", () => {
  const { events } = run([[0.03, 10000]], { ...CFG, no_speech_timeout_ms: 0 });
  assert.equal(events.length, 0);
});

test("no speech for the configured time -> no_speech_timeout", () => {
  const { events } = run([[0.004, 21000]]);
  assert.equal(events[0].type, "no_speech_timeout");
});

test("timeout 0 disables the no-speech timeout", () => {
  const { events } = run([[0.004, 30000]], { ...CFG, no_speech_timeout_ms: 0 });
  assert.equal(events.length, 0);
});

test("silence threshold is configurable", () => {
  const { events } = run([[0.004, 500], [0.1, 1500], [0.004, 2500]], { ...CFG, silence_ms: 800 });
  const end = events.find((e) => e.type === "speech_end");
  assert.ok(end.t < 500 + 1500 + 800 + 3 * FRAME);
});

test("max utterance forces an end", () => {
  const { events } = run([[0.004, 500], [0.1, 6000]], { ...CFG, max_utterance_ms: 3000 });
  const end = events.find((e) => e.type === "speech_end");
  assert.equal(end.forced, true);
});

test("resumeListening keeps detecting after a discarded blip", () => {
  const seg = new SpeechSegmenter(CFG);
  seg.reset(0);
  let t = 0;
  const feed = (rms, ms) => { const out = []; for (let n = 0; n < ms / FRAME; n += 1) { t += FRAME; const e = seg.feed(rms, t); if (e) out.push(e); } return out; };
  feed(0.004, 500);
  feed(0.2, 300);
  const first = feed(0.004, 2500);
  assert.equal(first[0].valid, false);
  seg.resumeListening(t);
  const second = [...feed(0.1, 1500), ...feed(0.004, 2500)];
  assert.deepEqual(second.map((e) => e.type), ["speech_start", "speech_end"]);
  assert.equal(second[1].valid, true);
});

test("WAV encoder: valid header, correct length, downsampled", async () => {
  const chunk = new Float32Array(48000).map((_, i) => Math.sin(i / 20)); // 1 s @ 48 kHz
  const blob = encodeWav([chunk], 48000, 16000);
  const buf = Buffer.from(await blob.arrayBuffer());
  assert.equal(buf.toString("ascii", 0, 4), "RIFF");
  assert.equal(buf.toString("ascii", 8, 12), "WAVE");
  assert.equal(buf.readUInt32LE(24), 16000);
  assert.equal(buf.readUInt16LE(22), 1);
  assert.equal(buf.readUInt32LE(40), 16000 * 2);
  assert.equal(buf.length, 44 + 32000);
});
