import { useCallback, useEffect, useRef } from "react";

import { sendEvents } from "../services/api";

/**
 * Collects monitoring events and sends them in batches, tied to the interview
 * session id. Events raised before the session exists are kept and sent once
 * the id is known.
 */
export default function useSessionEvents(sessionId, flushMs) {
  const queueRef = useRef([]);
  const sessionRef = useRef(null);

  useEffect(() => {
    sessionRef.current = sessionId;
  }, [sessionId]);

  const push = useCallback((event) => {
    queueRef.current.push(event);
  }, []);

  const flush = useCallback(async () => {
    const id = sessionRef.current;
    if (!id || queueRef.current.length === 0) return;

    const batch = queueRef.current.splice(0);
    try {
      await sendEvents(id, batch);
    } catch (err) {
      console.warn("Event upload failed, will retry:", err);
      queueRef.current.unshift(...batch);
    }
  }, []);

  useEffect(() => {
    const timer = setInterval(flush, flushMs);
    return () => clearInterval(timer);
  }, [flush, flushMs]);

  return { push, flush };
}
