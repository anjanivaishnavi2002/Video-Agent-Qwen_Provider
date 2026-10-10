import { useCallback, useEffect, useRef } from "react";

import { createPcmPlayer, startMicStreaming } from "../audio/liveAudio";
import { liveSocketUrl, getSessionToken } from "../services/api";

/**
 * Real-time interview over the backend's Gemini Live WebSocket.
 *   connect({ sessionId, stream })  opens the socket, streams the microphone, plays the interviewer's voice
 *   end()                            asks the backend to finish the interview
 * Callbacks: onSpeaking(boolean), onFinished(reason), onError(message), onExercise(task)
 */
export default function useLiveInterview({ onSpeaking, onFinished, onError, onExercise }) {
  const handlers = useRef({ onSpeaking, onFinished, onError, onExercise });
  useEffect(() => {
    handlers.current = { onSpeaking, onFinished, onError, onExercise };
  }, [onSpeaking, onFinished, onError, onExercise]);

  const ref = useRef({ socket: null, player: null, mic: null, done: false, closing: false, retries: 0 });

  const teardown = useCallback(() => {
    const live = ref.current;
    live.mic?.stop();
    live.player?.close();
    live.mic = null;
    live.player = null;
    live.closing = true;                                   // a deliberate close must not trigger a reconnect
    if (live.socket && live.socket.readyState <= 1) live.socket.close();
    live.socket = null;
  }, []);

  const connect = useCallback(
    ({ sessionId, stream }) => {
      const live = ref.current;
      live.done = false;
      live.closing = false;
      live.retries = 0;
      const retry = () => {
        if (live.done || live.closing) return;
        if (live.retries >= 4) {
          handlers.current.onError?.("The connection to the interviewer was lost. Please reload the page to continue.");
          return;
        }
        live.retries += 1;
        setTimeout(() => {
          if (live.done || live.closing) return;
          open().then(() => { live.retries = 0; }, retry);
        }, 1000 * live.retries);
      };
      const open = () => new Promise((resolve, reject) => {
        const socket = new WebSocket(liveSocketUrl(sessionId));
        socket.binaryType = "arraybuffer";
        live.socket = socket;
        live.player?.close();
        live.player = createPcmPlayer((active) => handlers.current.onSpeaking?.(active));
        let opened = false;

        let failure = null;
        socket.onopen = () => socket.send(JSON.stringify({ type: "auth", token: getSessionToken() }));

        socket.onmessage = async (event) => {
          if (typeof event.data !== "string") {
            live.player?.enqueue(event.data);
            return;
          }
          let message;
          try {
            message = JSON.parse(event.data);
          } catch {
            return;
          }
          if (message.type === "ready" && !opened) {
            opened = true;
            try {
              live.mic = await startMicStreaming(stream, (frame) => {
                if (socket.readyState === WebSocket.OPEN) socket.send(frame);
              });
              resolve();
            } catch (err) {
              reject(err);
            }
          } else if (message.type === "exercise") {
            handlers.current.onExercise?.(message.task);
          } else if (message.type === "interrupted") {
            live.player?.clear();
          } else if (message.type === "finished") {
            live.done = true;
            // Let the goodbye finish playing before the page moves on.
            handlers.current.onFinished?.(message.reason);
            live.mic?.stop();
            live.mic = null;
          } else if (message.type === "error") {
            if (!opened) failure = message.message || null;     // the real reason, shown if the socket then closes
            handlers.current.onError?.(message.message || "The AI interviewer connection failed.");
          }
        };

        socket.onerror = () => {
          if (!opened) reject(new Error("Could not connect to the AI interviewer."));
        };

        socket.onclose = (event) => {
          if (!opened) {
            const refused = event.code === 4401 || event.code === 4403;
            reject(new Error(failure || (refused
              ? "The interview connection was refused. Please reload and start again."
              : "Could not reach the AI interviewer. Please try again in a moment.")));
          } else if (!live.done && !live.closing) {
            // The backend keeps the transcript, so the interviewer can pick the call up again: retry a few times.
            live.mic?.stop();
            live.mic = null;
            retry();
          }
        };
      });
      return open();
    },
    []
  );

  const end = useCallback(() => {
    const socket = ref.current.socket;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "end" }));
      return true;
    }
    return false; // not connected: the caller finishes locally
  }, []);

  // The candidate submitted the written exercise (already saved over REST): the interviewer may now ask about it.
  const exerciseDone = useCallback((taskId) => {
    const socket = ref.current.socket;
    if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "exercise_done", task_id: taskId }));
  }, []);

  useEffect(() => teardown, [teardown]);

  return { connect, end, exerciseDone, teardown };
}
