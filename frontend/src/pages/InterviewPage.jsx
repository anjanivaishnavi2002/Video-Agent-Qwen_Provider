import { useCallback, useEffect, useRef, useState } from "react";

import MicIndicator from "../components/MicIndicator";
import InterviewerAvatar from "../components/InterviewerAvatar";
import InterviewStatus from "../components/InterviewStatus";
import FaceMotionDetector from "../components/FaceMotionDetector";
import ReportPanel from "../components/ReportPanel";

import useGeminiLive from "../hooks/useGeminiLive";
import useInterviewRecorder from "../hooks/useInterviewRecorder";
import useMediaStream from "../hooks/useMediaStream";
import useSessionEvents from "../hooks/useSessionEvents";

import {
  endInterview,
  generateInterviewReport,
  getInterviewReport,
  startLiveInterview,
  uploadVideo,
} from "../services/api";

function formatClock(totalSeconds) {
  const m = Math.floor(totalSeconds / 60);
  const sec = totalSeconds % 60;
  return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

// Elapsed interview time. Counts only while `running`.
function Timer({ startedAt, running }) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!running) return undefined;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);

  const seconds = startedAt ? Math.max(0, Math.floor((now - startedAt) / 1000)) : 0;
  return <span className="room-timer">{formatClock(seconds)}</span>;
}

function InterviewPage({
  candidateId,
  candidateName,
  sessionId,
  setSessionId,
  config,
}) {
  // starting | thinking | speaking | listening | finished | error
  const [status, setStatus] = useState("starting");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [report, setReport] = useState(null);
  const [reportState, setReportState] = useState("idle"); // idle | loading | ready | failed
  const [startedAt, setStartedAt] = useState(null);
  const [isFullscreen, setIsFullscreen] = useState(Boolean(document.fullscreenElement));

  const startedRef = useRef(false);
  const sessionRef = useRef(null);
  const liveDisconnectRef = useRef(null);
  const finishedRef = useRef(false);

  // One camera + microphone stream shared by face monitor, listener and recorder.
  const { stream, error: mediaError } = useMediaStream();

  // Problems that make the interview impossible are derived, not stored.
  const fatalError = candidateId
    ? mediaError
    : "Candidate information is missing.";
  const viewStatus = fatalError ? "error" : status;

  const {
    start: startRecording,
    stop: stopRecording,
    offsetMs,
  } = useInterviewRecorder(stream, config.recording);

  const { push: pushEvent, flush: flushEvents } = useSessionEvents(
    sessionId,
    config.face.flush_ms
  );

  // Face events are tied to the interview session and to the recording timeline.
  const handleFaceEvent = useCallback(
    (event) => pushEvent({ ...event, offset_ms: offsetMs() }),
    [pushEvent, offsetMs]
  );

  // Stop the recording, save monitoring events and upload the video.
  const finishInterview = useCallback(
    async ({ userEnded }) => {
      if (finishedRef.current) return;
      finishedRef.current = true;
      liveDisconnectRef.current?.();

      setStatus("finished");
      setSaving(true);

      const id = sessionRef.current;

      try {
        if (userEnded && id) {
          await endInterview(id);
        }

        const recording = await stopRecording();
        await flushEvents();

        if (id && recording) {
          await uploadVideo(id, recording);
        }
      } catch (err) {
        console.error("Saving interview data failed:", err);
        setError(err.message || "Could not save the interview recording.");
      } finally {
        setSaving(false);
      }

      // The report is written by the backend after the recording arrives: wait for it briefly,
      // and ask for it directly if it did not start by itself.
      if (id) {
        setReportState("loading");
        try {
          let found = null;
          for (let attempt = 0; attempt < 8 && !found; attempt += 1) {
            const current = await getInterviewReport(id);
            if (current && current.status && current.status !== "pending") found = current;
            else await new Promise((resolve) => setTimeout(resolve, 3000));
          }
          setReport(found || (await generateInterviewReport(id)));
          setReportState("ready");
        } catch (reportError) {
          console.error("Report failed:", reportError);
          setReportState("failed");
        }
      }
    },
    [stopRecording, flushEvents]
  );

  // Start automatically once camera + microphone are ready.
  useEffect(() => {
    if (startedRef.current || fatalError) return;

    if (!stream) return; // still waiting for camera/microphone permission

    startedRef.current = true;

    async function initializeInterview() {
      try {
        setError("");

        const result = await startLiveInterview(candidateId);
        sessionRef.current = result.sessionId;
        setSessionId(result.sessionId);

        startRecording(); // camera + microphone, whole interview
        setStartedAt(Date.now());
        setStatus("listening");
      } catch (err) {
        console.error("Interview start failed:", err);
        setError(err.message || "Could not start interview.");
        setStatus("error");
      }
    }

    initializeInterview();
  }, [candidateId, stream, fatalError, setSessionId, startRecording]);

  const live = useGeminiLive({
    stream,
    sessionId,
    onStatus: setStatus,
    onFinished: () => finishInterview({ userEnded: false }),
  });

  useEffect(() => {
    liveDisconnectRef.current = live.disconnect;
    return () => {
      liveDisconnectRef.current = null;
    };
  }, [live.disconnect]);

  // End interview manually
  const handleEndInterview = useCallback(async () => {
    if (!sessionRef.current || finishedRef.current) return;

    liveDisconnectRef.current?.();
    await finishInterview({ userEnded: true });
  }, [finishInterview]);

  // Leaving the page releases the recorder and the AI voice.
  useEffect(() => {
    return () => {
      liveDisconnectRef.current?.();
      stopRecording();
    };
  }, [stopRecording]);

  // Keep the button in sync with the browser's full-screen state (Esc also exits).
  useEffect(() => {
    const onChange = () => setIsFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  const toggleFullscreen = useCallback(() => {
    if (document.fullscreenElement) {
      document.exitFullscreen?.();
    } else {
      document.documentElement.requestFullscreen?.().catch(() => {});
    }
  }, []);

  const shownError = fatalError || error || live.error;
  const isLive = viewStatus !== "finished" && viewStatus !== "error";

  return (
    <div className="interview-room">
      <header className="room-topbar">
        <div className="room-brand">
          <span className="room-logo">AI</span>
          <div>
            <strong>AI Interview</strong>
            <span>{config.interview_type}</span>
          </div>
        </div>

        <div className="room-meta">
          {startedAt && isLive && (
            <span className="rec-chip">
              <span className="rec-dot" /> REC
            </span>
          )}
          <Timer startedAt={startedAt} running={Boolean(startedAt) && isLive} />
          <button
            type="button"
            className="room-icon-button"
            onClick={toggleFullscreen}
            aria-label={isFullscreen ? "Exit full screen" : "Enter full screen"}
            title={isFullscreen ? "Exit full screen" : "Enter full screen"}
          >
            {isFullscreen ? "⤡" : "⤢"}
          </button>
        </div>
      </header>

      {viewStatus === "finished" && reportState !== "idle" ? (
        <ReportPanel state={reportState} report={report} name={candidateName} />
      ) : (
      <main className="room-stage">
        <section
          className={`room-tile tile-ai ${viewStatus === "speaking" ? "is-speaking" : ""}`}
        >
          <InterviewerAvatar status={viewStatus} name={config.interviewer_name} />
          <p className="tile-role">Interviewer</p>

          <div
            className={`voice-wave ${viewStatus === "speaking" ? "active" : ""}`}
            aria-hidden="true"
          >
            <span /><span /><span /><span /><span /><span /><span />
          </div>

          <InterviewStatus status={viewStatus} />
        </section>

        <section className="room-tile tile-you">
          <FaceMotionDetector
            stream={stream}
            config={config.face}
            onEvent={handleFaceEvent}
          />
          <span className="tile-label">{candidateName || "You"}</span>
        </section>
      </main>
      )}

      <footer className="room-controls">
        <div className="room-controls-center">
          <MicIndicator
            listening={viewStatus === "listening"}
            speechActive={live.speechActive}
          />

          <button
            className="end-button room-end"
            onClick={handleEndInterview}
            disabled={!sessionId || viewStatus === "finished"}
          >
            End Interview
          </button>
        </div>

        {saving && <p className="room-note">Saving your recording...</p>}
        {shownError && <p className="room-error">{shownError}</p>}
      </footer>
    </div>
  );
}

export default InterviewPage;
