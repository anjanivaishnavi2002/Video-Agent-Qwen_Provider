import { useCallback, useRef } from "react";

function pickMimeType() {
  const options = [
    "video/webm;codecs=vp8,opus",
    "video/webm",
    "video/mp4",
  ];
  return options.find((t) => window.MediaRecorder?.isTypeSupported?.(t)) || "";
}

/**
 * Records camera + microphone for the WHOLE interview.
 *   start()   begin recording (call once the interview starts)
 *   stop()    resolves with the recording Blob (or null)
 *   offsetMs  ms since the recording started, to line monitoring events up with the video
 */
export default function useInterviewRecorder(stream, config) {
  const recorderRef = useRef(null);
  const chunksRef = useRef([]);
  const startedAtRef = useRef(null);

  const start = useCallback(() => {
    if (!stream || recorderRef.current || !window.MediaRecorder) return;

    const mimeType = pickMimeType();
    const options = { videoBitsPerSecond: config?.video_bits_per_second };
    if (mimeType) options.mimeType = mimeType;

    try {
      const recorder = new MediaRecorder(stream, options);
      chunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.start(1000); // a chunk every second: little is lost if the tab dies
      recorderRef.current = recorder;
      startedAtRef.current = performance.now();
    } catch (err) {
      console.error("Could not start interview recording:", err);
    }
  }, [stream, config]);

  const stop = useCallback(() => {
    const recorder = recorderRef.current;
    recorderRef.current = null;

    if (!recorder || recorder.state === "inactive") return Promise.resolve(null);

    return new Promise((resolve) => {
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, {
          type: recorder.mimeType || "video/webm",
        });
        chunksRef.current = [];
        resolve(blob.size > 0 ? blob : null);
      };
      recorder.stop();
    });
  }, []);

  const offsetMs = useCallback(
    () =>
      startedAtRef.current === null
        ? null
        : Math.round(performance.now() - startedAtRef.current),
    []
  );

  return { start, stop, offsetMs };
}
