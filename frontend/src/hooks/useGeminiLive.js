import { useCallback, useEffect, useRef, useState } from "react";

import { createMicCapture } from "../audio/micCapture";
import { createDownsampler, pcm16Base64ToFloat32 } from "../audio/pcm";
import { rmsOf } from "../audio/speechSegmenter";
import { connectLiveInterview } from "../services/api";

const INPUT_RATE = 16000; // what Gemini Live expects from the microphone
const OUTPUT_RATE = 24000; // what Gemini Live sends back

export default function useGeminiLive({ stream, sessionId, onStatus, onFinished }) {
  const [speechActive, setSpeechActive] = useState(false);
  const [error, setError] = useState("");
  const callbacks = useRef({ onStatus, onFinished });
  const disconnectRef = useRef(() => {});

  useEffect(() => {
    callbacks.current = { onStatus, onFinished };
  }, [onStatus, onFinished]);

  const disconnect = useCallback(() => disconnectRef.current(), []);

  useEffect(() => {
    if (!sessionId || !stream) return undefined;

    let cancelled = false;
    let connection;
    let capture;
    let outputContext;
    let nextPlaybackTime = 0;
    let sources = new Set();
    let finishAfterPlayback = false;
    let finishNotified = false;
    let loggedFirstAudio = false;
    const unlockAudio = () => {
      if (outputContext && outputContext.state !== "running") outputContext.resume().catch(() => {});
    };
    window.addEventListener("pointerdown", unlockAudio);
    window.addEventListener("keydown", unlockAudio);

    const notifyFinished = () => {
      if (finishNotified) return;
      finishNotified = true;
      callbacks.current.onFinished?.();
    };

    const stopPlayback = () => {
      for (const source of sources) {
        try { source.stop(); } catch { /* already stopped */ }
      }
      sources.clear();
      nextPlaybackTime = outputContext?.currentTime || 0;
    };

    const handleEvent = (event) => {
      if (event.type === "ready") {
        callbacks.current.onStatus?.("listening");
      } else if (event.type === "audio" && outputContext) {
        try {
          // Browsers may leave the context suspended (autoplay policy): wake it on every chunk.
          if (outputContext.state !== "running") outputContext.resume().catch(() => {});
          if (!loggedFirstAudio) {
            loggedFirstAudio = true;
            console.info("[live] first audio chunk received; AudioContext state:", outputContext.state);
          }
          const samples = pcm16Base64ToFloat32(event.data);
          const audioBuffer = outputContext.createBuffer(1, samples.length, OUTPUT_RATE);
          audioBuffer.copyToChannel(samples, 0);
          const source = outputContext.createBufferSource();
          source.buffer = audioBuffer;
          source.connect(outputContext.destination);
          const startAt = Math.max(outputContext.currentTime, nextPlaybackTime);
          nextPlaybackTime = startAt + audioBuffer.duration;
          sources.add(source);
          source.onended = () => {
            sources.delete(source);
            if (sources.size === 0) {
              if (finishAfterPlayback) notifyFinished();
              else if (nextPlaybackTime <= outputContext.currentTime + 0.03) {
                callbacks.current.onStatus?.("listening");
              }
            }
          };
          source.start(startAt);
          callbacks.current.onStatus?.("speaking");
        } catch (playError) {
          setError(`Could not play Gemini Live audio: ${playError.message}`);
        }
      } else if (event.type === "interrupted") {
        stopPlayback();
        callbacks.current.onStatus?.("listening");
      } else if (event.type === "turn_complete") {
        if (sources.size === 0) callbacks.current.onStatus?.("listening");
      } else if (event.type === "finished") {
        finishAfterPlayback = true;
        if (sources.size === 0) notifyFinished();
      } else if (event.type === "error") {
        setError(event.message || "Gemini Live connection failed.");
        callbacks.current.onStatus?.("error");
      }
    };

    async function connect() {
      try {
        outputContext = new (window.AudioContext || window.webkitAudioContext)();
        await outputContext.resume().catch(() => {});
        connection = await connectLiveInterview(sessionId, handleEvent);
        if (cancelled) {
          connection.close();
          return;
        }
        disconnectRef.current = () => {
          capture?.close();
          capture = undefined;
          connection?.close();
        };
        let downsample;
        capture = await createMicCapture(stream, (chunk, sampleRate) => {
          setSpeechActive(rmsOf(chunk) > 0.015);
          downsample ||= createDownsampler(sampleRate, INPUT_RATE);
          const pcm = downsample(chunk);
          if (pcm.length) connection?.sendAudio(pcm.buffer);
        });
      } catch (connectError) {
        if (cancelled) return;
        setError(connectError.message || "Could not start Gemini Live.");
        callbacks.current.onStatus?.("error");
      }
    }

    connect();
    return () => {
      cancelled = true;
      window.removeEventListener("pointerdown", unlockAudio);
      window.removeEventListener("keydown", unlockAudio);
      disconnectRef.current = () => {};
      capture?.close();
      connection?.close();
      stopPlayback();
      outputContext?.close().catch(() => {});
      setSpeechActive(false);
    };
  }, [sessionId, stream]);

  return { speechActive, error, disconnect };
}