import { useCallback, useEffect, useRef, useState } from "react";

import MicIndicator from "../components/MicIndicator";
import InterviewerAvatar from "../components/InterviewerAvatar";
import InterviewStatus from "../components/InterviewStatus";
import FaceMotionDetector from "../components/FaceMotionDetector";
import ReportPanel from "../components/ReportPanel";

import useAutoListen from "../hooks/useAutoListen";
import useInterviewRecorder from "../hooks/useInterviewRecorder";
import useLiveInterview from "../hooks/useLiveInterview";
import useMediaStream from "../hooks/useMediaStream";
import useSessionEvents from "../hooks/useSessionEvents";

import {
  endInterview,
  sendNoResponse,
  sendVoiceAnswer,
  startInterview,
  uploadVideo,
  waitForInterviewReport,
} from "../services/api";

// Turn errors are recoverable (the candidate simply keeps talking) until
// they repeat this many times in a row.
const MAX_CONSECUTIVE_FAILURES = 3;

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

// Turn-based voice (LLM_PROVIDER=ollama): the candidate speaks, Whisper transcribes, Qwen answers, Piper speaks.
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

  const audioRef = useRef(null);
  const startedRef = useRef(false);
  const sessionRef = useRef(null);
  const finishedRef = useRef(false);
  const failuresRef = useRef(0);

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

  // Plays a blob and RESOLVES ONLY WHEN THE AUDIO HAS FINISHED, so the
  // microphone never starts while the AI is still talking.
  const playBlob = useCallback((blob) => {
    return new Promise((resolve, reject) => {
      const audio = audioRef.current;

      if (!audio) {
        resolve();
        return;
      }

      const url = URL.createObjectURL(blob);

      const cleanup = () => {
        URL.revokeObjectURL(url);
        audio.onended = null;
        audio.onerror = null;
      };

      audio.onended = () => {
        cleanup();
        resolve();
      };

      audio.onerror = () => {
        cleanup();
        reject(new Error("Could not play AI voice"));
      };

      audio.src = url;
      audio.play().catch((err) => {
        cleanup();
        reject(err);
      });
    });
  }, []);

  // Stop the recording, save monitoring events and upload the video.
  const finishInterview = useCallback(
    async ({ userEnded, alreadyEnded = false }) => {
      if (finishedRef.current) return;
      finishedRef.current = true;

      setStatus("finished");
      setSaving(true);

      const id = sessionRef.current;

      try {
        if (userEnded && id && !alreadyEnded && !liveModeRef.current) {
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

      // The summary + scorecard are written by the backend after the recording arrives.
      if (id) {
        setReportState("loading");
        try {
          setReport(await waitForInterviewReport(id));
          setReportState("ready");
        } catch (reportError) {
          console.error("Report failed:", reportError);
          setReportState("failed");
        }
      }
    },
    [stopRecording, flushEvents]
  );

  // Gemini Live (real-time voice): the backend relays the audio, so there is no turn loop in the browser.
  const liveModeRef = useRef(false);
  const [liveVoice, setLiveVoice] = useState(false);
  const live = useLiveInterview({
    onSpeaking: (active) => {
      if (!finishedRef.current) setStatus(active ? "speaking" : "listening");
    },
    onFinished: () => finishInterview({ userEnded: false, alreadyEnded: true }),
    onError: (message) => setError(message),
  });

  // Speak the interviewer's reply, then either finish or listen again.
  const deliverReply = useCallback(
    async (reply) => {
      if (reply.audio) {
        setStatus("speaking");
        await playBlob(reply.audio);
      }

      if (reply.finished) {
        await finishInterview({ userEnded: false });
      } else {
        setStatus("listening");
      }
    },
    [playBlob, finishInterview]
  );

  const handleTurnError = useCallback((err) => {
    console.error("Interview turn failed:", err);

    failuresRef.current += 1;
    const message = err.message || "Something went wrong.";
    setError(message);

    const fatal =
      /session not found/i.test(message) ||
      failuresRef.current >= MAX_CONSECUTIVE_FAILURES;

    setStatus(fatal ? "error" : "listening");
  }, []);

  // Start automatically once camera + microphone are ready.
  useEffect(() => {
    if (startedRef.current || fatalError) return;

    if (!stream) return; // still waiting for camera/microphone permission

    startedRef.current = true;

    async function initializeInterview() {
      try {
        setError("");

        const result = await startInterview(candidateId);
        sessionRef.current = result.sessionId;
        setSessionId(result.sessionId);

        startRecording(); // camera + microphone, whole interview
        setStartedAt(Date.now());

        if (result.mode === "live") {
          liveModeRef.current = true;
          setLiveVoice(true);
          await live.connect({ sessionId: result.sessionId, stream });
          setStatus("listening");
          return;
        }

        await deliverReply(result);
      } catch (err) {
        console.error("Interview start failed:", err);
        setError(err.message || "Could not start interview.");
        setStatus("error");
      }
    }

    initializeInterview();
  }, [candidateId, stream, fatalError, setSessionId, startRecording, deliverReply, live]);

  // The candidate finished an answer (silence detected).
  const handleUtterance = useCallback(
    async (audioBlob) => {
      const id = sessionRef.current;
      if (!id || finishedRef.current) return;

      try {
        setError("");
        setStatus("thinking");

        const reply = await sendVoiceAnswer(id, audioBlob);
        failuresRef.current = 0;

        if (reply.status === "no_speech") {
          setStatus("listening"); // nothing intelligible: just keep listening
          return;
        }

        await deliverReply(reply);
      } catch (err) {
        handleTurnError(err);
      }
    },
    [deliverReply, handleTurnError]
  );

  // The candidate stayed silent for the configured time: the AI checks in.
  const handleNoSpeech = useCallback(async () => {
    const id = sessionRef.current;
    if (!id || finishedRef.current) return;

    try {
      setError("");
      setStatus("thinking");

      const reply = await sendNoResponse(id);
      failuresRef.current = 0;
      await deliverReply(reply);
    } catch (err) {
      handleTurnError(err);
    }
  }, [deliverReply, handleTurnError]);

  const { speechActive, error: listenError } = useAutoListen({
    stream,
    enabled: viewStatus === "listening" && !liveVoice,
    config: config.voice,
    onUtterance: handleUtterance,
    onNoSpeech: handleNoSpeech,
  });

  // End interview manually
  const handleEndInterview = useCallback(async () => {
    if (!sessionRef.current || finishedRef.current) return;

    audioRef.current?.pause(); // stop the AI voice immediately
    if (liveModeRef.current) {
      live.end(); // the backend saves the transcript, then answers "finished"
      return;
    }
    await finishInterview({ userEnded: true });
  }, [finishInterview, live]);

  // Leaving the page releases the recorder and the AI voice.
  useEffect(() => {
    const audio = audioRef.current;
    return () => {
      audio?.pause();
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

  const shownError = fatalError || error || listenError;
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
            speechActive={speechActive}
          />

          <button
            className="end-button room-end"
            onClick={handleEndInterview}
            disabled={!sessionId || viewStatus === "finished" || viewStatus === "error"}
          >
            End Interview
          </button>
        </div>

        {saving && <p className="room-note">Saving your recording...</p>}
        {shownError && <p className="room-error">{shownError}</p>}
      </footer>

      <audio ref={audioRef} />
    </div>
  );
}

export default InterviewPage;
