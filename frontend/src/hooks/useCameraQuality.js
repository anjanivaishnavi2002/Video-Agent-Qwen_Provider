import { useEffect, useState } from "react";

/**
 * Samples the camera every couple of seconds and reports whether the picture is too dark to see the candidate.
 * It only measures average brightness on a tiny canvas; nothing is stored or sent anywhere.
 */
export default function useCameraQuality(stream, { intervalMs = 2500, darkBelow = 45 } = {}) {
  const [dark, setDark] = useState(false);

  useEffect(() => {
    if (!stream || stream.getVideoTracks().length === 0) return undefined;
    const video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.srcObject = stream;
    video.play().catch(() => {});
    const canvas = document.createElement("canvas");
    canvas.width = 32;
    canvas.height = 24;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    let lowInARow = 0;

    const timer = setInterval(() => {
      if (!ctx || video.readyState < 2) return;
      try {
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height);
        let sum = 0;
        for (let i = 0; i < data.length; i += 4) sum += 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
        const mean = sum / (data.length / 4);
        lowInARow = mean < darkBelow ? lowInARow + 1 : 0;
        setDark(lowInARow >= 3);                    // needs ~7 seconds of darkness, so a blink or shadow is ignored
      } catch {
        /* measuring is best effort */
      }
    }, intervalMs);

    return () => {
      clearInterval(timer);
      video.srcObject = null;
    };
  }, [stream, intervalMs, darkBelow]);

  return dark;
}
