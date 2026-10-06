import { useCallback, useEffect, useRef } from "react";

import { createPcmPlayer, startMicStreaming } from "../audio/liveAudio";
import { liveSocketUrl, getSessionToken } from "../services/api";

/**
 * Real-time interview over the backend's Gemini Live WebSocket.
 *   connect({ sessionId, stream })  opens the socket, streams the microphone, plays the interviewer's voice
 *   end()                            asks the backend to finish the interview
 * Callbacks: onSpeaking(boolean), onFinished(reason), onError(message)
 */
export default function useLiveInterview({ onSpeaking, onFinished, onError }) {
  const handlers = useRef({ onSpeaking, onFinished, onError });
  useEffect(() => {
    handlers.current = { onSpeaking, onFinished, onError };
  }, [onSpeaking, onFinished, onError]);

  const ref = useRef({ socket: null, player: null, mic: null, done: false });

  const teardown = useCallback(() => {
    const live = ref.current;
    live.mic?.stop();
    live.player?.close();
    live.mic = null;
    live.player = null;
    if (live.socket && live.socket.readyState <= 1) live.socket.close();
    live.socket = null;
  }, []);

  const connect = useCallback(
    ({ sessionId, stream }) =>
      new Promise((resolve, reject) => {
        const live = ref.current;
        live.done = false;
        const socket = new WebSocket(liveSocketUrl(sessionId));
        socket.binaryType = "arraybuffer";
        live.socket = socket;
        live.player = createPcmPlayer((active) => handlers.current.onSpeaking?.(active));
        let opened = false;

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
          } else if (message.type === "interrupted") {
            live.player?.clear();
          } else if (message.type === "finished") {
            live.done = true;
            // Let the goodbye finish playing before the page moves on.
            handlers.current.onFinished?.(message.reason);
            live.mic?.stop();
            live.mic = null;
          } else if (message.type === "error") {
            handlers.current.onError?.(message.message || "The AI interviewer connection failed.");
          }
        };

        socket.onerror = () => {
          if (!opened) reject(new Error("Could not connect to the AI interviewer."));
        };

        socket.onclose = () => {
          if (!opened) {
            reject(new Error("The interview connection was refused. Please reload and try again."));
          } else if (!live.done) {
            handlers.current.onError?.("The connection to the interviewer was lost.");
          }
        };
      }),
    []
  );

  const end = useCallback(() => {
    const socket = ref.current.socket;
    if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "end" }));
  }, []);

  useEffect(() => teardown, [teardown]);

  return { connect, end, teardown };
}
