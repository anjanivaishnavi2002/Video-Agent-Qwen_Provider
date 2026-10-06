// Audio plumbing for the Gemini Live interview.
//   microphone  -> 16 kHz mono PCM16 frames  -> WebSocket   (startMicStreaming)
//   WebSocket   -> 24 kHz mono PCM16 chunks  -> speakers    (createPcmPlayer)

export const INPUT_RATE = 16000;
export const OUTPUT_RATE = 24000;
const FRAME_SAMPLES = 640; // 40 ms at 16 kHz

// ---- pure helpers (unit-tested) -------------------------------------------------------------

/** Float32 [-1, 1] -> Int16 little-endian samples. */
export function floatToPcm16(float32) {
  const out = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i += 1) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

/** PCM16 bytes (ArrayBuffer, little-endian) -> Float32 [-1, 1]. An odd trailing byte is ignored. */
export function pcm16ToFloat(buffer) {
  const view = new DataView(buffer);
  const n = Math.floor(buffer.byteLength / 2);
  const out = new Float32Array(n);
  for (let i = 0; i < n; i += 1) out[i] = view.getInt16(i * 2, true) / 0x8000;
  return out;
}

/** Linear-interpolation resampler (good enough for speech). */
export function resample(input, fromRate, toRate) {
  if (fromRate === toRate) return input;
  const ratio = fromRate / toRate;
  const length = Math.floor(input.length / ratio);
  const out = new Float32Array(length);
  for (let i = 0; i < length; i += 1) {
    const pos = i * ratio;
    const i0 = Math.floor(pos);
    const i1 = Math.min(i0 + 1, input.length - 1);
    out[i] = input[i0] + (input[i1] - input[i0]) * (pos - i0);
  }
  return out;
}

// ---- microphone -> frames ---------------------------------------------------------------------

const WORKLET_SOURCE = `
class CaptureProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) this.port.postMessage(channel.slice(0));
    return true;
  }
}
registerProcessor("capture-processor", CaptureProcessor);
`;

/**
 * Streams the microphone as 16 kHz PCM16 frames to `onFrame(ArrayBuffer)`.
 * Returns { stop() }. `stream` is the shared camera+microphone MediaStream.
 */
export async function startMicStreaming(stream, onFrame) {
  const audioTracks = stream.getAudioTracks();
  if (!audioTracks.length) throw new Error("No microphone is available.");
  const context = new AudioContext();
  await context.audioWorklet.addModule(
    URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: "application/javascript" }))
  );
  const source = context.createMediaStreamSource(new MediaStream(audioTracks));
  const node = new AudioWorkletNode(context, "capture-processor");
  let pending = new Float32Array(0);

  node.port.onmessage = (event) => {
    const resampled = resample(event.data, context.sampleRate, INPUT_RATE);
    const merged = new Float32Array(pending.length + resampled.length);
    merged.set(pending);
    merged.set(resampled, pending.length);
    let offset = 0;
    while (merged.length - offset >= FRAME_SAMPLES) {
      onFrame(floatToPcm16(merged.subarray(offset, offset + FRAME_SAMPLES)).buffer);
      offset += FRAME_SAMPLES;
    }
    pending = merged.slice(offset);
  };

  source.connect(node);
  // Not connected to the destination: the candidate must not hear themselves.
  return {
    stop() {
      node.port.onmessage = null;
      try {
        source.disconnect();
        node.disconnect();
      } catch {
        /* already disconnected */
      }
      context.close().catch(() => {});
    },
  };
}

// ---- chunks -> speakers -----------------------------------------------------------------------

/**
 * Gapless playback of streamed PCM16 chunks. `onActive(true|false)` fires when the interviewer starts / stops
 * making sound. clear() drops everything queued (used when the candidate interrupts).
 */
export function createPcmPlayer(onActive = () => {}) {
  const context = new AudioContext({ sampleRate: OUTPUT_RATE });
  let nextTime = 0;
  const playing = new Set();

  function enqueue(arrayBuffer) {
    const samples = pcm16ToFloat(arrayBuffer);
    if (!samples.length) return;
    if (context.state === "suspended") context.resume().catch(() => {});
    const buffer = context.createBuffer(1, samples.length, OUTPUT_RATE);
    buffer.copyToChannel(samples, 0);
    const node = context.createBufferSource();
    node.buffer = buffer;
    node.connect(context.destination);
    const startAt = Math.max(context.currentTime + 0.02, nextTime);
    node.start(startAt);
    nextTime = startAt + buffer.duration;
    if (playing.size === 0) onActive(true);
    playing.add(node);
    node.onended = () => {
      playing.delete(node);
      if (playing.size === 0) onActive(false);
    };
  }

  function clear() {
    for (const node of playing) {
      node.onended = null;
      try {
        node.stop();
      } catch {
        /* already stopped */
      }
    }
    const wasActive = playing.size > 0;
    playing.clear();
    nextTime = 0;
    if (wasActive) onActive(false);
  }

  function close() {
    clear();
    context.close().catch(() => {});
  }

  return { enqueue, clear, close, resume: () => context.resume().catch(() => {}) };
}
