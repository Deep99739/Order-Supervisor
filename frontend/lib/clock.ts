"use client";

import { useEffect, useRef, useState } from "react";

/**
 * A clock aligned to the API's own `observed_at`, so a countdown does not drift with the
 * browser's clock. It remains display only: reaching zero means a review is due, never
 * that one has happened. Only a recorded episode establishes that.
 */
export function useServerNow(observedAt: string | null | undefined): number {
  const offset = useRef(0);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!observedAt) return;
    const parsed = Date.parse(observedAt);
    // Captured at the moment the observation arrived; measuring it later would fold the
    // age of the response into the offset.
    if (Number.isFinite(parsed)) offset.current = parsed - Date.now();
  }, [observedAt]);

  useEffect(() => {
    const timer = window.setInterval(
      () => setNow(Date.now() + offset.current),
      1000,
    );
    return () => window.clearInterval(timer);
  }, []);

  return now;
}
