import { useEffect, useRef, useState } from "react";

import { createMicCapture } from "../audio/micCapture";
import { rmsOf, SpeechSegmenter } from "../audio/speechSegmenter";
import { encodeWav } from "../audio/wav";

/**
 * Hands-free listening.
 *
 * While `enabled` is true the microphone is analysed continuously:
 * speech starts -> audio is collected -> after `silence_ms` of silence the
 * answer is encoded as WAV and delivered through `onUtterance`.
 * While `enabled` is false nothing is captured (so the AI's own voice is
 * never picked up).
 *
 * All thresholds/timings come from `config` (the backend's `voice` section).
 */
export default function useAutoListen({
  stream,
  enabled,
  config,
  onUtterance,
  onNoSpeech,
}) {
  const [speechActive, setSpeechActive] = useState(false);
  const [error, setError] = useState("");

  const handlers = useRef({ onUtterance, onNoSpeech });
  useEffect(() => {
    handlers.current = { onUtterance, onNoSpeech };
  }, [onUtterance, onNoSpeech]);

  const enabledRef = useRef(false);
  const segmenterRef = useRef(null);
  const clockRef = useRef(0); // audio time in ms (advances with captured samples)
  const prerollRef = useRef([]); // [{chunk, ms}] audio from just before speech
  const utteranceRef = useRef(null); // chunks of the answer in progress
  const rateRef = useRef(48000);
  const activeRef = useRef(false); // mirrors `speechActive` for use inside audio callbacks

  function markActive(value) {
    if (activeRef.current !== value) {
      activeRef.current = value;
      setSpeechActive(value);
    }
  }

  // (Re)start a listening round whenever `enabled` turns on.
  useEffect(() => {
    enabledRef.current = enabled;
    prerollRef.current = [];
    utteranceRef.current = null;

    if (enabled) {
      const segmenter = new SpeechSegmenter(config);
      segmenter.reset(clockRef.current);
      segmenterRef.current = segmenter;
    } else {
      segmenterRef.current = null;
    }
  }, [enabled, config]);

  // One audio pipeline for the whole interview.
  useEffect(() => {
    if (!stream || stream.getAudioTracks().length === 0) return undefined;

    let capture = null;
    let cancelled = false;

    function handleChunk(chunk, sampleRate) {
      rateRef.current = sampleRate;
      const chunkMs = (chunk.length / sampleRate) * 1000;
      clockRef.current += chunkMs;

      const segmenter = segmenterRef.current;
      if (!enabledRef.current || !segmenter) {
        markActive(false);
        return;
      }

      const event = segmenter.feed(rmsOf(chunk), clockRef.current);

      if (event?.type === "speech_start") {
        // include the pre-roll so the first syllable is not clipped
        utteranceRef.current = [
          ...prerollRef.current.map((p) => p.chunk),
          chunk,
        ];
        prerollRef.current = [];
        markActive(true);
        return;
      }

      if (utteranceRef.current) {
        utteranceRef.current.push(chunk);

        if (event?.type === "speech_end") {
          const chunks = utteranceRef.current;
          utteranceRef.current = null;
          markActive(false);

          if (event.valid) {
            const wav = encodeWav(
              chunks,
              sampleRate,
              config.target_sample_rate
            );
            handlers.current.onUtterance?.(wav);
          } else {
            // a cough / click: ignore it and keep listening
            segmenter.resumeListening(clockRef.current);
          }
        }
        return;
      }

      if (event?.type === "no_speech_timeout") {
        handlers.current.onNoSpeech?.();
        return;
      }

      // idle: keep a rolling pre-roll buffer
      const preroll = prerollRef.current;
      preroll.push({ chunk, ms: chunkMs });
      let total = preroll.reduce((sum, p) => sum + p.ms, 0);
      while (preroll.length > 1 && total - preroll[0].ms >= config.preroll_ms) {
        total -= preroll.shift().ms;
      }
    }

    createMicCapture(stream, handleChunk)
      .then((created) => {
        if (cancelled) created.close();
        else capture = created;
      })
      .catch((err) => {
        console.error("Microphone capture failed:", err);
        setError("Could not start microphone listening in this browser.");
      });

    return () => {
      cancelled = true;
      capture?.close();
    };
  }, [stream, config]);

  return { speechActive: enabled && speechActive, error };
}
