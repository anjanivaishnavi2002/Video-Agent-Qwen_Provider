// Microphone -> Gemini Live: float samples at the device rate (usually 44.1/48 kHz) to 16-bit PCM at 16 kHz.
//
// Each output sample is the AVERAGE of the input samples it covers (a box filter). That removes the
// high frequencies that would otherwise fold back into the speech band (aliasing) and hurt recognition.
// The converter is stateful, so audio chunk boundaries never create clicks or drift.

export function createDownsampler(inputRate, outputRate = 16000) {
  const ratio = inputRate / outputRate;
  let sum = 0;
  let count = 0;
  let position = 0; // input samples seen so far
  let boundary = ratio; // input position where the current output sample ends

  /** @param {Float32Array} chunk  @returns {Int16Array} */
  return function process(chunk) {
    const out = [];
    for (let i = 0; i < chunk.length; i += 1) {
      sum += chunk[i];
      count += 1;
      position += 1;
      if (position >= boundary - 1e-9) {
        const value = Math.max(-1, Math.min(1, sum / count));
        out.push(Math.round(value * 32767));
        sum = 0;
        count = 0;
        boundary += ratio;
      }
    }
    return Int16Array.from(out);
  };
}

/** base64 of 16-bit little-endian PCM (what the server sends) -> Float32Array in [-1, 1). */
export function pcm16Base64ToFloat32(base64) {
  const binary = atob(base64);
  const view = new DataView(new ArrayBuffer(binary.length - (binary.length % 2)));
  for (let i = 0; i < view.byteLength; i += 1) view.setUint8(i, binary.charCodeAt(i));
  const samples = new Float32Array(view.byteLength / 2);
  for (let i = 0; i < samples.length; i += 1) samples[i] = view.getInt16(i * 2, true) / 32768;
  return samples;
}
