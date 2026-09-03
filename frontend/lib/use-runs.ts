"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, listRuns } from "./api";
import { usePoller } from "./polling";
import type { RunPage } from "./contracts";

const INTERVAL_MS = 5000;

export type RunListState = {
  page: RunPage | null;
  loading: boolean;
  error: ApiError | null;
  refreshing: boolean;
  refresh: () => void;
};

/**
 * The visible run list, refreshed while the tab is open. A failed read keeps the last
 * good page on screen so the operator can still see what was recorded; the caller labels
 * it stale rather than replacing it with an empty state, which would read as "no runs".
 */
export function useRuns(
  state: "active" | "closed" | "all",
  orderId: string,
): RunListState {
  const [page, setPage] = useState<RunPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const inFlight = useRef(false);
  const request = useRef<AbortController | null>(null);
  const applied = useRef<string | null>(null);

  useEffect(() => () => request.current?.abort(), []);

  const search = orderId.trim();
  const read = useCallback(
    async (manual: boolean): Promise<void> => {
      if (inFlight.current) return;
      inFlight.current = true;
      const controller = new AbortController();
      request.current = controller;
      const key = `${state}|${search}`;
      // A filter change is a new question, so the table waits rather than showing the
      // previous answer under the new heading.
      if (applied.current !== key) setLoading(true);
      if (manual) setRefreshing(true);
      try {
        const result = await listRuns(
          { state, orderId: search || undefined, limit: 50 },
          controller.signal,
        );
        if (controller.signal.aborted) return;
        applied.current = key;
        setPage(result);
        setError(null);
        setLoading(false);
      } catch (cause) {
        if (controller.signal.aborted) return;
        setError(
          cause instanceof ApiError
            ? cause
            : new ApiError("The run list could not be read.", "network"),
        );
        setLoading(false);
      } finally {
        if (request.current === controller) request.current = null;
        inFlight.current = false;
        if (manual) setRefreshing(false);
      }
    },
    [state, search],
  );

  const poll = useCallback(() => read(false), [read]);
  usePoller(poll, INTERVAL_MS, true);

  const refresh = useCallback(() => {
    void read(true);
  }, [read]);

  return { page, loading, error, refreshing, refresh };
}
