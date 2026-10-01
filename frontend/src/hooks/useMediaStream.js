import { useEffect, useState } from "react";

// ONE getUserMedia call for the whole interview. The same stream feeds
//   - the face monitor (video track)
//   - hands-free answer capture (audio track)
//   - the full interview recording (video + audio)
const AUDIO_CONSTRAINTS = {
  echoCancellation: true,
  noiseSuppression: true,
  autoGainControl: true,
};
const VIDEO_CONSTRAINTS = { width: 640, height: 480, facingMode: "user" };

export default function useMediaStream() {
  const [state, setState] = useState({
    stream: null,
    error: "", // fatal: no microphone
    videoError: "", // non-fatal: interview continues without camera
  });

  useEffect(() => {
    let cancelled = false;
    let acquired = null;

    async function acquire() {
      let stream;
      let videoError = "";

      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: AUDIO_CONSTRAINTS,
          video: VIDEO_CONSTRAINTS,
        });
      } catch (err) {
        console.warn("Camera + microphone failed, trying microphone only:", err);
        try {
          stream = await navigator.mediaDevices.getUserMedia({
            audio: AUDIO_CONSTRAINTS,
          });
          videoError = "Camera permission is required for the interview.";
        } catch (audioErr) {
          console.error("Microphone error:", audioErr);
          if (!cancelled) {
            setState({
              stream: null,
              error: "Microphone permission is required for the interview.",
              videoError: "",
            });
          }
          return;
        }
      }

      if (cancelled) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }

      acquired = stream;
      setState({ stream, error: "", videoError });
    }

    acquire();

    return () => {
      cancelled = true;
      acquired?.getTracks().forEach((track) => track.stop());
    };
  }, []);

  return state;
}
