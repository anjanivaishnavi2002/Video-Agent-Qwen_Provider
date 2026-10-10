import { useEffect, useRef } from "react";

import { sendTabSwitch } from "../services/api";

// Two switches within this window are ONE switch (a tab change fires both "blur" and "visibilitychange").
const DEDUPE_MS = 3000;

/**
 * Watches for the candidate leaving the page (another tab, another window, minimised browser).
 * The BACKEND counts it: warning 1, warning 2, and the next switch ends the interview, so the rule cannot be
 * skipped by editing the page. This hook only reports the switch and shows what the server answered.
 *
 *   onWarning({ count, warningsLeft, limit })   called for each warning
 *   onEnded()                                  called when the server ended the interview
 */
export default function useTabGuard({ sessionId, enabled, offsetMs, onWarning, onEnded }) {
  const lastRef = useRef(0);
  const callbacks = useRef({ onWarning, onEnded, offsetMs });

  useEffect(() => {
    callbacks.current = { onWarning, onEnded, offsetMs };
  });

  useEffect(() => {
    if (!enabled || !sessionId) return undefined;

    let ended = false;

    async function report() {
      const now = Date.now();
      if (ended || now - lastRef.current < DEDUPE_MS) return;
      lastRef.current = now;
      try {
        const result = await sendTabSwitch(sessionId, callbacks.current.offsetMs?.() ?? null);
        if (result.ended) {
          ended = true;
          callbacks.current.onEnded?.(result);
        } else if (result.warning) {
          callbacks.current.onWarning?.({
            count: result.count,
            warningsLeft: result.warnings_left,
            limit: result.limit,
          });
        }
      } catch (err) {
        console.warn("Could not report the tab switch:", err);
      }
    }

    const onVisibility = () => {
      if (document.visibilityState === "hidden") report();
    };
    // A window blur alone is not a tab switch: clicking the address bar, dev tools or a permission prompt also blurs.
    // Count it only if the page is STILL not focused after a short grace period, and never in dev builds.
    const onBlur = () => {
      if (import.meta.env.DEV) return;
      setTimeout(() => { if (!document.hasFocus()) report(); }, 1500);
    };

    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("blur", onBlur);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("blur", onBlur);
    };
  }, [enabled, sessionId]);
}
