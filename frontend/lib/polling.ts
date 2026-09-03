"use client";

import { useEffect } from "react";

/**
 * A refresh loop that never overlaps itself, never runs while the tab is hidden, and
 * reads again as soon as the tab comes back. Polling is a read: it schedules no business
 * work, and a paused loop cannot stop a run from being supervised.
 *
 * The first read is scheduled by this loop too, so a screen has exactly one owner of its
 * data rather than an initial fetch racing a poll.
 */
export function usePoller(
  read: () => Promise<void>,
  intervalMs: number,
  enabled: boolean,
): void {
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let timer = 0;

    const tick = async () => {
      if (cancelled) return;
      if (document.visibilityState === "visible") await read();
      if (!cancelled) timer = window.setTimeout(tick, intervalMs);
    };

    const onVisibility = () => {
      if (document.visibilityState === "visible") void read();
    };

    timer = window.setTimeout(tick, 0);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [read, intervalMs, enabled]);
}
