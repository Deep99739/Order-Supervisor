"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, getActivity, getRun } from "./api";
import { usePoller } from "./polling";
import type { ActivityRecord, RunView } from "./contracts";

const INTERVAL_MS = 2000;
const PAGE = 60;
// One cycle catches up on a burst without turning into an unbounded fetch loop.
const CATCH_UP_ROUNDS = 6;

export type RunFeed = {
  view: RunView | null;
  records: ActivityRecord[];
  earlierCursor: number | null;
  /** True only before the first successful read. */
  loading: boolean;
  /** The most recent read failed. Any view still on screen is the last good one. */
  error: ApiError | null;
  refreshing: boolean;
  loadingEarlier: boolean;
  /** Periodic reading has stopped because the run is closed and its report is recorded. */
  settled: boolean;
  refresh: () => void;
  loadEarlier: () => void;
};

function merge(
  existing: ActivityRecord[],
  incoming: ActivityRecord[],
): ActivityRecord[] {
  if (incoming.length === 0) return existing;
  const bySequence = new Map(existing.map((record) => [record.sequence, record]));
  for (const record of incoming) bySequence.set(record.sequence, record);
  return [...bySequence.values()].sort((a, b) => a.sequence - b.sequence);
}

/**
 * The single polling owner for one run. Every panel on the detail screen reads from this
 * one result, so no component starts its own competing poll and no view mixes a newer
 * receipt into an older set of order facts: history is always requested bounded by the
 * `last_sequence` of the snapshot it will be displayed beside.
 */
export function useRun(runId: string): RunFeed {
  const [view, setView] = useState<RunView | null>(null);
  const [records, setRecords] = useState<ActivityRecord[]>([]);
  const [earlierCursor, setEarlierCursor] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [settled, setSettled] = useState(false);

  const inFlight = useRef(false);
  const highest = useRef<number | null>(null);
  const request = useRef<AbortController | null>(null);

  useEffect(() => {
    highest.current = null;
    return () => request.current?.abort();
  }, [runId]);

  const read = useCallback(
    async (manual: boolean): Promise<void> => {
      // A second read while one is running would only duplicate it; the loop is short.
      if (inFlight.current) return;
      inFlight.current = true;
      const controller = new AbortController();
      request.current = controller;
      if (manual) setRefreshing(true);

      try {
        const next = await getRun(runId, controller.signal);
        const bound = next.snapshot.last_sequence;
        const start = highest.current;
        let known = start ?? 0;
        let collected: ActivityRecord[] = [];

        if (start === null) {
          const page = await getActivity(
            runId,
            { throughSequence: bound, limit: PAGE },
            controller.signal,
          );
          collected = page.records;
          setEarlierCursor(page.earlier_cursor);
        } else {
          for (let round = 0; round < CATCH_UP_ROUNDS && known < bound; round += 1) {
            const page = await getActivity(
              runId,
              { afterSequence: known, throughSequence: bound, limit: PAGE },
              controller.signal,
            );
            if (page.records.length === 0) break;
            collected = collected.concat(page.records);
            known = page.records[page.records.length - 1].sequence;
            if (page.records.length < PAGE) break;
          }
        }

        if (controller.signal.aborted) return;
        const reached = collected.at(-1)?.sequence;
        highest.current = Math.max(highest.current ?? 0, reached ?? 0);
        setView(next);
        setRecords((current) => merge(current, collected));
        setError(null);
        setLoading(false);
        setSettled(
          next.snapshot.closed_at !== null && next.snapshot.final_output !== null,
        );
      } catch (cause) {
        if (controller.signal.aborted) return;
        setError(
          cause instanceof ApiError
            ? cause
            : new ApiError("This run could not be read.", "network"),
        );
        setLoading(false);
      } finally {
        if (request.current === controller) request.current = null;
        inFlight.current = false;
        if (manual) setRefreshing(false);
      }
    },
    [runId],
  );

  const poll = useCallback(() => read(false), [read]);
  usePoller(poll, INTERVAL_MS, !settled);

  const refresh = useCallback(() => {
    void read(true);
  }, [read]);

  const bound = view?.snapshot.last_sequence;
  const loadEarlier = useCallback(() => {
    if (earlierCursor === null || bound === undefined || loadingEarlier) return;
    setLoadingEarlier(true);
    getActivity(runId, {
      beforeSequence: earlierCursor,
      throughSequence: bound,
      limit: PAGE,
    })
      .then((page) => {
        setRecords((current) => merge(current, page.records));
        setEarlierCursor(page.earlier_cursor);
      })
      .catch((cause: unknown) => {
        setError(
          cause instanceof ApiError
            ? cause
            : new ApiError("Earlier activity could not be read.", "network"),
        );
      })
      .finally(() => setLoadingEarlier(false));
  }, [bound, earlierCursor, loadingEarlier, runId]);

  return {
    view,
    records,
    earlierCursor,
    loading,
    error,
    refreshing,
    loadingEarlier,
    settled,
    refresh,
    loadEarlier,
  };
}
