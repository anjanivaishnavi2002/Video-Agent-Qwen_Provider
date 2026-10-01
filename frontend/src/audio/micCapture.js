// Raw microphone samples through the Web Audio API (AudioWorklet).
//
// Capturing PCM ourselves (instead of MediaRecorder) lets us keep a short
// "pre-roll" so the first syllable is never clipped, and lets us measure
// loudness on exactly the same samples we send to Whisper.

const WORKLET_SOURCE = `
class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(2048);
    this.filled = 0;
  }
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) {
      for (let i = 0; i < channel.length; i += 1) {
        this.buffer[this.filled++] = channel[i];
        if (this.filled === this.buffer.length) {
          this.port.postMessage(this.buffer.slice(0));
          this.filled = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor("pcm-capture", PcmCapture);
`;

/**
 * @param {MediaStream} stream  a stream that has at least one audio track
 * @param {(chunk: Float32Array, sampleRate: number) => void} onChunk
 */
export async function createMicCapture(stream, onChunk) {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  const context = new AudioContextClass();
  await context.resume().catch(() => {});

  const url = URL.createObjectURL(
    new Blob([WORKLET_SOURCE], { type: "application/javascript" })
  );
  try {
    await context.audioWorklet.addModule(url);
  } finally {
    URL.revokeObjectURL(url);
  }

  const source = context.createMediaStreamSource(
    new MediaStream(stream.getAudioTracks())
  );
  const node = new AudioWorkletNode(context, "pcm-capture");

  // A muted path to the destination keeps the worklet running without echoing the mic.
  const mute = context.createGain();
  mute.gain.value = 0;
  source.connect(node);
  node.connect(mute);
  mute.connect(context.destination);

  node.port.onmessage = (event) => onChunk(event.data, context.sampleRate);

  return {
    sampleRate: context.sampleRate,
    close() {
      node.port.onmessage = null;
      try {
        source.disconnect();
        node.disconnect();
        mute.disconnect();
      } catch {
        /* already disconnected */
      }
      context.close().catch(() => {});
    },
  };
}
