import test from "node:test";
import { strict as strictAssert } from "node:assert";

import { createDownsampler, pcm16Base64ToFloat32 } from "../src/audio/pcm.js";

const sine = (rate, seconds, hz, amp = 0.5) =>
  Float32Array.from({ length: Math.round(rate * seconds) }, (_, i) => amp * Math.sin((2 * Math.PI * hz * i) / rate));

test("48 kHz -> 16 kHz keeps the right length and level", () => {
  const out = createDownsampler(48000)(sine(48000, 1, 440));
  strictAssert.equal(out.length, 16000);
  const peak = Math.max(...Array.from(out, Math.abs));
  strictAssert.ok(peak > 0.45 * 32767 && peak < 0.52 * 32767, `peak ${peak}`);
});

test("chunk boundaries do not change the result", () => {
  const signal = sine(44100, 0.5, 300);
  const whole = createDownsampler(44100)(signal);
  const down = createDownsampler(44100);
  const parts = [];
  for (let i = 0; i < signal.length; i += 2048) parts.push(...down(signal.subarray(i, i + 2048)));
  strictAssert.deepEqual(Int16Array.from(parts), whole);
  strictAssert.ok(Math.abs(whole.length - 8000) <= 1);
});

test("frequencies above the new Nyquist are attenuated versus naive sample skipping", () => {
  // 11 kHz cannot be represented at 16 kHz. Skipping samples folds it into the speech band at full
  // strength (aliasing); averaging must clearly reduce it.
  const signal = sine(48000, 0.5, 11000, 0.8);
  const naive = Float32Array.from({ length: Math.floor(signal.length / 3) }, (_, i) => signal[i * 3]);
  const rmsOf = (values, scale = 1) => Math.sqrt(values.reduce((a, v) => a + (v / scale) ** 2, 0) / values.length);
  const filtered = rmsOf(createDownsampler(48000)(signal), 32767);
  strictAssert.ok(filtered < 0.6 * rmsOf(naive), `filtered ${filtered} vs naive ${rmsOf(naive)}`);
});

test("clipping is safe", () => {
  const out = createDownsampler(16000)(Float32Array.from([2, -2, 0.5]));
  strictAssert.deepEqual(Array.from(out), [32767, -32767, 16384]);
});

test("base64 PCM16 from the server decodes to floats", () => {
  const bytes = new Uint8Array([0x00, 0x40, 0x00, 0xc0, 0xff]); // 16384, -16384, + a stray byte
  const b64 = btoa(String.fromCharCode(...bytes));
  const samples = pcm16Base64ToFloat32(b64);
  strictAssert.equal(samples.length, 2);
  strictAssert.ok(Math.abs(samples[0] - 0.5) < 1e-4 && Math.abs(samples[1] + 0.5) < 1e-4);
});
