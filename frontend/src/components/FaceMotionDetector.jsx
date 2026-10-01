import { useEffect, useRef, useState } from "react";
import {
  FaceLandmarker,
  FilesetResolver,
} from "@mediapipe/tasks-vision";

const DETECTION_INTERVAL_MS = 100; // ~10 checks per second is plenty

/**
 * Face monitoring limited to OBSERVABLE events:
 *   face_missing   no face for `missing_grace_ms`
 *   face_returned  a face is back after being missing
 *   multiple_faces more than one face in view
 *   head_movement  the face moved a lot within a short window
 *
 * It never infers emotion, confidence, honesty, attention or personality.
 * It uses the shared camera stream (it does not open the camera itself) and
 * reports events through `onEvent({ type, details, timestamp })`. The parent
 * attaches the interview session id / recording offset.
 */
function FaceMotionDetector({ stream, config, onEvent }) {
  const videoRef = useRef(null);
  const [faceDetected, setFaceDetected] = useState(false);
  const [cameraError, setCameraError] = useState("");

  const onEventRef = useRef(onEvent);
  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  const hasVideo = Boolean(stream && stream.getVideoTracks().length > 0);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !stream || !hasVideo) return undefined;

    let landmarker = null;
    let timer = null;
    let cancelled = false;
    let faceVisible = null;

    let missingSince = null;
    let missingReported = false;
    let history = []; // [{ t, x, y }] recent face-centre positions
    const lastEmitted = {};

    function emit(type, details) {
      const now = performance.now();
      if (
        lastEmitted[type] !== undefined &&
        now - lastEmitted[type] < config.event_cooldown_ms
      ) {
        return;
      }
      lastEmitted[type] = now;
      onEventRef.current?.({
        type,
        details,
        timestamp: new Date().toISOString(),
      });
    }

    function faceCentre(points) {
      let x = 0;
      let y = 0;
      for (const p of points) {
        x += p.x;
        y += p.y;
      }
      return { x: x / points.length, y: y / points.length };
    }

    function analyse() {
      if (cancelled || !landmarker || video.readyState < 2) return;

      const now = performance.now();
      const faces = landmarker.detectForVideo(video, now).faceLandmarks || [];
      const present = faces.length > 0;

      if (present !== faceVisible) {
        faceVisible = present;
        setFaceDetected(present);
      }

      if (!present) {
        history = [];
        if (missingSince === null) missingSince = now;
        if (!missingReported && now - missingSince >= config.missing_grace_ms) {
          missingReported = true;
          emit("face_missing");
        }
        return;
      }

      if (missingReported) {
        emit("face_returned");
      }
      missingSince = null;
      missingReported = false;

      if (faces.length > 1) {
        emit("multiple_faces", { faces: faces.length });
      }

      // Significant movement = displacement inside a short time window.
      const centre = faceCentre(faces[0]);
      history.push({ t: now, ...centre });
      history = history.filter((h) => now - h.t <= config.movement_window_ms);

      const oldest = history[0];
      if (now - oldest.t >= config.movement_window_ms * 0.5) {
        const moved = Math.hypot(centre.x - oldest.x, centre.y - oldest.y);
        if (moved > config.movement_threshold) {
          emit("head_movement", { magnitude: Number(moved.toFixed(3)) });
          history = [];
        }
      }
    }

    async function setup() {
      try {
        video.srcObject = new MediaStream(stream.getVideoTracks());
        await video.play();

        // Engine files are served locally (public/mediapipe/wasm) so their
        // version always matches the installed @mediapipe/tasks-vision package.
        const vision = await FilesetResolver.forVisionTasks(config.wasm_url);

        const options = (delegate) => ({
          baseOptions: {
            modelAssetPath: config.model_url,
            delegate,
          },
          runningMode: "VIDEO",
          numFaces: config.max_faces,
        });

        try {
          landmarker = await FaceLandmarker.createFromOptions(vision, options("GPU"));
        } catch {
          landmarker = await FaceLandmarker.createFromOptions(vision, options("CPU"));
        }

        if (cancelled) {
          landmarker.close();
          return;
        }
        timer = setInterval(analyse, DETECTION_INTERVAL_MS);
      } catch (err) {
        console.error("Face monitor setup failed:", err);
        setCameraError(
          `Face monitoring could not start${err?.message ? ` (${err.message})` : ""}.`
        );
      }
    }

    setup();

    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
      landmarker?.close();
      video.srcObject = null;
    };
  }, [stream, hasVideo, config]);

  return (
    <div className="camera-container">
      <video
        ref={videoRef}
        className="camera-video"
        autoPlay
        muted
        playsInline
      />

      <div className="camera-status">
        <span
          className={`camera-dot ${faceDetected ? "active" : "inactive"}`}
        />
        {faceDetected ? "Face detected" : "Face not detected"}
      </div>

      {(cameraError || (stream && !hasVideo)) && (
        <div className="camera-error">
          {cameraError || "Camera permission is required for the interview."}
        </div>
      )}
    </div>
  );
}

export default FaceMotionDetector;
