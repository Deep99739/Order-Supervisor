"use client";

import { useCallback, useState } from "react";
import { BarChart3, CircleAlert, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { StateBadge } from "@/components/state-badge";
import { ErrorState } from "@/components/states";
import { Panel } from "@/components/runs/side-panels";
import { ApiError, getAnalytics } from "@/lib/api";
import { useOnce } from "@/lib/polling";
import { ACTION_LABEL, BLOCK_REASON, durationLabel, eventLabel } from "@/lib/display";
import type {
  ActionName,
  BlockReason,
  RunAnalytics,
  RunSnapshot,
} from "@/lib/contracts";

function Figure({
  label,
  value,
  detail,
}: {
  label: string;
  value: string | number;
  detail?: string;
}) {
  return (
    <div className="rounded-lg border px-3.5 py-3">
      <p className="text-[13px] text-muted-foreground">{label}</p>
      <p className="mt-1 text-xl font-semibold tracking-tight tabular-nums">
        {value}
      </p>
      {detail ? (
        <p className="mt-1 text-[13px] leading-5 text-muted-foreground">{detail}</p>
      ) : null}
    </div>
  );
}

/** A bar is a proportion of the largest row, drawn in CSS. No chart library is needed. */
function Bars({
  rows,
  empty,
  tone = "bg-primary/70",
}: {
  rows: [string, number][];
  empty: string;
  tone?: string;
}) {
  if (rows.length === 0) {
    return <p className="text-muted-foreground">{empty}</p>;
  }
  const largest = Math.max(...rows.map(([, value]) => value), 1);
  return (
    <ul className="space-y-2.5">
      {rows.map(([label, value]) => (
        <li key={label}>
          <div className="flex items-baseline justify-between gap-3">
            <span className="min-w-0 truncate">{label}</span>
            <span className="shrink-0 tabular-nums">{value}</span>
          </div>
          <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-muted">
            <div
              className={`h-full rounded-full ${tone}`}
              style={{ width: `${Math.max((value / largest) * 100, 4)}%` }}
            />
          </div>
        </li>
      ))}
    </ul>
  );
}

function sorted(counts: Record<string, number>): [string, number][] {
  return Object.entries(counts).sort((a, b) => b[1] - a[1]);
}

/**
 * Counts derived from this run's own records.
 *
 * This is a separate observation from the snapshot beside it, so it carries its own
 * timestamp and cutoff and says when the two have drifted apart. Nothing here is a rate,
 * a saving, or a success score: none of those is inferable from a local simulation.
 */
export function AnalyticsTab({ snapshot }: { snapshot: RunSnapshot }) {
  const [report, setReport] = useState<RunAnalytics | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setReport(await getAnalytics(snapshot.run_id));
      setError(null);
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? cause
          : new ApiError("These counts could not be read.", "network"),
      );
    } finally {
      setLoading(false);
    }
  }, [snapshot.run_id]);

  useOnce(load);

  if (loading && !report) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (!report) {
    return (
      <Panel title="Run analytics" icon={BarChart3}>
        <ErrorState
          title="These counts could not be read"
          description={error?.message ?? "The analytics route did not answer."}
          onRetry={() => void load()}
          retrying={loading}
          className="py-6"
        />
      </Panel>
    );
  }

  const behind = snapshot.last_sequence - report.through_sequence;
  const disagreements = report.counter_checks.filter((check) => !check.agrees);
  const allTriggers: [string, number][] = [
    ["Supervision started", report.episodes_by_trigger.start],
    ["An important event arrived", report.episodes_by_trigger.important_event],
    ["A scheduled review came due", report.episodes_by_trigger.scheduled_wake],
    ["An operator change", report.episodes_by_trigger.control_reassessment],
  ];
  const triggers = allTriggers.filter(([, value]) => value > 0);

  return (
    <div className="space-y-5">
      <Panel
        title="Run analytics"
        icon={BarChart3}
        action={
          <Button
            variant="outline"
            size="sm"
            onClick={() => void load()}
            disabled={loading}
          >
            <RefreshCw className="size-4" aria-hidden="true" />
            Recount
          </Button>
        }
      >
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <Figure
            label="Order events"
            value={report.unique_events}
            detail={
              report.duplicate_events > 0
                ? `${report.duplicate_events} repeat delivery(ies) ignored`
                : "no repeat deliveries"
            }
          />
          <Figure
            label="Review episodes"
            value={report.decision_episodes}
            detail={`${report.completed_episodes} recorded · ${report.discarded_episodes} discarded`}
          />
          <Figure
            label="Actions recorded"
            value={report.action_outcomes.committed}
            detail={`${report.action_outcomes.blocked} blocked · ${report.action_outcomes.pending_review} awaiting approval`}
          />
          <Figure
            label="Supervised for"
            value={durationLabel(report.duration_seconds)}
            detail={report.closed_at ? "to closure" : "and still running"}
          />
        </div>
        <p className="mt-4 text-[13px] leading-5 text-muted-foreground">
          Counted from this run&rsquo;s records up to entry #{report.through_sequence},
          observed{" "}
          <time dateTime={report.observed_at}>
            {new Date(report.observed_at).toLocaleTimeString()}
          </time>
          .{" "}
          {behind > 0
            ? `${behind} newer record${behind === 1 ? " has" : "s have"} been written since; these numbers do not include ${behind === 1 ? "it" : "them"}.`
            : "The run has recorded nothing since."}
        </p>
      </Panel>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="Events by type">
          <Bars
            rows={sorted(report.events_by_type).map(([type, value]) => [
              eventLabel(type),
              value,
            ])}
            empty="No order event has been admitted yet."
          />
          {report.deferred_events > 0 ? (
            <p className="mt-4 border-t pt-3 text-[13px] leading-5 text-muted-foreground">
              <span className="font-medium text-foreground" data-numeric>
                {report.deferred_events}
              </span>{" "}
              were recorded without waking the agent. Deferred is not ignored: the
              evidence is carried until a review covers it.
            </p>
          ) : null}
        </Panel>

        <Panel title="Why the agent ran">
          <Bars
            rows={triggers}
            empty="No review episode has started yet."
            tone="bg-working/70"
          />
          <p className="mt-4 border-t pt-3 text-[13px] leading-5 text-muted-foreground">
            {report.provider_attempts} reasoning attempt
            {report.provider_attempts === 1 ? "" : "s"} were dispatched across those
            episodes, plus {report.report_attempts} closing-report call
            {report.report_attempts === 1 ? "" : "s"}. An attempt is not an episode.
          </p>
        </Panel>

        <Panel title="Actions recorded, by type">
          <Bars
            rows={sorted(report.committed_by_action).map(([action, value]) => [
              ACTION_LABEL[action as ActionName] ?? action,
              value,
            ])}
            empty="No action has been recorded for this order."
            tone="bg-done/70"
          />
        </Panel>

        <Panel title="Proposals that were refused">
          <Bars
            rows={sorted(report.blocked_by_reason).map(([reason, value]) => [
              BLOCK_REASON[reason as BlockReason] ?? reason,
              value,
            ])}
            empty="Every proposal this run made was carried out."
            tone="bg-hold/70"
          />
        </Panel>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="Review, memory, and continuity">
          <dl className="divide-y">
            {([
              ["Events flagged for a person", report.review_flags],
              ["Concerns still open", report.open_issues],
              ["Of those, escalated", report.escalated_issues],
              ["Summaries adopted", report.compactions],
              ["Summaries refused", report.refused_compactions],
              ["Executions resumed", report.continuations],
              ["Rollovers prepared", report.prepared_continuations],
              ["Operational failures", report.operational_failures],
            ] as [string, number][]).map(([label, value]) => (
              <div
                key={label}
                className="flex items-baseline justify-between gap-4 py-2"
              >
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="tabular-nums">{value}</dd>
              </div>
            ))}
          </dl>
        </Panel>

        <Panel title="Provider usage">
          {report.tokens.reported_calls === 0 ? (
            <p className="text-muted-foreground">
              No provider reported usage for this run. Nothing is estimated in its
              place, and no cost is derived from these counts.
            </p>
          ) : (
            <>
              <div className="grid gap-2 sm:grid-cols-2">
                <Figure
                  label="Input tokens"
                  value={report.tokens.input_tokens ?? "—"}
                />
                <Figure
                  label="Output tokens"
                  value={report.tokens.output_tokens ?? "—"}
                />
              </div>
              <p className="mt-3 text-[13px] leading-5 text-muted-foreground">
                Reported by the provider for {report.tokens.reported_calls} of{" "}
                {report.tokens.calls} call
                {report.tokens.calls === 1 ? "" : "s"}. Numbers nobody reported are
                absent rather than estimated.
              </p>
            </>
          )}
        </Panel>
      </div>

      <Panel
        title="Cached counters against the records"
        action={
          <StateBadge
            label={disagreements.length === 0 ? "In agreement" : "Disagreement"}
            tone={disagreements.length === 0 ? "quiet" : "alert"}
            dot={false}
            className="px-2 py-0.5 text-[12px]"
          />
        }
      >
        <p className="text-[13px] leading-5 text-muted-foreground">
          The run keeps counters inside the same transaction that writes each record.
          Comparing them with the records themselves is published rather than assumed,
          so a drift is visible instead of silent.
        </p>
        <ul className="mt-3 divide-y">
          {report.counter_checks.map((check) => (
            <li
              key={check.metric}
              className="flex items-baseline justify-between gap-4 py-2"
            >
              <span className="font-mono text-[13px]">{check.metric}</span>
              <span className="flex items-center gap-3 tabular-nums">
                <span className={check.agrees ? "" : "text-destructive"}>
                  counter {check.recorded} · records {check.derived}
                </span>
                {check.agrees ? null : (
                  <CircleAlert
                    className="size-4 text-destructive"
                    aria-label="disagreement"
                  />
                )}
              </span>
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}
